# -*- coding: utf-8 -*-
"""feature/tag —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
import asyncio
import html
import re

def _tag_retry_after(text):
    """从「Flood control exceeded. Retry in 38 seconds」里提取需等待的秒数；没有则 0。"""
    mm = re.search(r"retry in\s+(\d+)\s*second", str(text or ""), re.I)
    if mm:
        return int(mm.group(1))
    mm = re.search(r"(\d+)\s*second", str(text or ""), re.I)
    return int(mm.group(1)) if mm else 0


async def _tag_group_admins(app, cid):
    """一次调用取回群内「管理员 + 群主」的 ID 集合。

    🚨 为什么必须排除他们：Telegram 规定**只有群创建者能改管理员的标签**
    （管理员的自定义头衔 = 同一字段）。bot 不是群主 ⇒ 给管理员设标签必定返回
    400 `CHAT_CREATOR_REQUIRED`（用户截图里固定失败的 9 人就是管理员）。
    提前排除 = 不浪费配额、不产生吓人的"失败"数字。
    取不到（权限不足等）就返回空集合，让后续照常尝试（失败也只记 skip）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    try:
        admins = await app.bot.get_chat_administrators(cid)
    except Exception:  # silent-ok: 取不到管理员名单只降级为「不排除」，失败项会按 skip 归类，不会崩
        logger.debug("取群管理员失败（同步标签将不做排除）：cid=%s", cid, exc_info=True)
        return set()
    out = set()
    for a in admins or []:
        u = getattr(a, "user", None)
        if u is not None and getattr(u, "id", None):
            out.add(int(u.id))
    return out


def _tag_sanitize(s):
    """把等级名净化成合法成员标签：≤16 字符、去掉 emoji 等非法字符。

    官方 setChatMemberTag：tag 必须 0-16 字符且**不能含 emoji**，否则 400 TAG_INVALID。
    等级名是后台可改的（用户很可能写成「👑 大神」），所以必须清洗后再发。
    返回空串 = 这个等级名没法当标签；调用方**必须跳过**（用空值调 API 等于清除标签！）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    TAG_MAX_LEN = hub.TAG_MAX_LEN
    out = []
    for ch in str(s or ""):
        o = ord(ch)
        if o < 128:
            if ch.isprintable():
                out.append(ch)
            continue
        # 只保留 CJK / 日文假名 / 韩文；emoji、零宽连接符 U+200D、
        # 变体选择符 U+FE0F、各类符号一律丢弃。
        if (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
                or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7A3):
            out.append(ch)
    return "".join(out).strip()[:TAG_MAX_LEN]


def _tag_err_hint(exc):
    """把 setChatMemberTag 的报错翻译成管理员能照着做的人话（排障用）。"""
    txt = str(exc).lower()
    if "chat_creator_required" in txt:
        return "对方是群主/管理员：Telegram 规定只有群主能改管理员标签，bot 改不了（属正常，已自动跳过）"
    if "flood" in txt or "retry in" in txt or "too many requests" in txt or "429" in txt:
        return "Telegram 限速（调用太密）→ 已自动等待后重试"
    if "not enough rights" in txt or "403" in txt or "forbidden" in txt:
        return "bot 缺少「管理标签」权限 → 群管理→管理员→给 bot 勾上「管理标签」"
    if "tag_invalid" in txt or "emoji" in txt:
        return "等级名含 emoji 或超过 16 字（Telegram 不允许）→ 改成纯文字等级名"
    if "supergroup" in txt or "channel chats only" in txt:
        return "该群类型不支持成员标签（仅群/超级群可用）"
    # 对方已不在群：候选名单是本地账本 `total_earned ∪ game_chips`，**退群/被移出的人仍在账本里**
    # ⇒ 会被当成候选去改标签，Telegram 回 400 `USER_NOT_PARTICIPANT`
    # （user is not a participant of the chat）。
    # 2026-09-11 生产实测（用户截图）：旧版只认 "participant not found"/"user not found"，
    # 认不出 `USER_NOT_PARTICIPANT` ⇒ 英文原文漏进同步报告；而且 `_tag_is_expected_skip`
    # 靠「对方已不在群」前缀识别，认不出英文 ⇒ 被算成「❌ 失败 1 人」，白白吓人。
    if "participant" in txt or "user not found" in txt:
        return "对方已不在群里（已退群或被移出，标签改不了属正常）"
    if "chat not found" in txt:
        return "群不存在或 bot 不在该群"
    return str(exc)[:120]


def _tag_is_expected_skip(reason):
    """这些"失败"是**平台规则决定的、不可能成功**的，应算 skip 而不是 fail（别吓人）。"""
    return (reason.startswith("未达任何等级") or reason.startswith("后台")
            or reason.startswith("对方是群主/管理员") or reason.startswith("对方已不在群"))


async def _tag_call(bot, cid, uid, tag):
    """发一次 setChatMemberTag。返回 `(ok, 人话说明, 需等待秒数)`，**绝不抛**。

    需等待秒数 > 0 表示被 Telegram 限速（Flood control），调用方应等这么久再重试。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _bot_api = hub._bot_api
    _tag_err_hint = hub._tag_err_hint
    _tag_retry_after = hub._tag_retry_after
    logger = hub.logger
    try:
        await _bot_api(bot, "set_chat_member_tag", "setChatMemberTag",
                       chat_id=cid, user_id=uid, tag=tag)
        return True, tag, 0
    except Exception as exc:
        logger.debug("同步成员标签失败：cid=%s uid=%s", cid, uid, exc_info=True)
        return False, _tag_err_hint(exc), _tag_retry_after(exc)


async def sync_member_tags(app, cid, limit=None, progress=None):
    """把群内「有积分账本 / 有钱包」的成员称号，批量补同步成 Telegram 成员标签。

    为什么要批量补：称号→标签只在**升级瞬间**同步（`_check_level_change`），
    存量玩家早在修复前就满级了，永远不会再触发 ⇒ 必须能补历史欠账。

    生产实测踩到的两个坑（2026-09-11 用户截图，都已处理）：
      · **群主 / 管理员会被平台拒绝** —— Telegram 规定只有**群创建者**能改管理员标签
        ⇒ 400 `CHAT_CREATOR_REQUIRED`。提前取管理员名单排除，不浪费配额。
      · **按每秒十几次调会被限速** —— `Flood control exceeded. Retry in 38 seconds`。
        现在按 `TAG_SYNC_GAP`(3.2s) 限速，且遇限速会**等 RetryAfter 后重试**并把间隔翻倍。

    返回 {total, ok, skip, fail, reasons, admins_skipped, flood, busy, first_err}。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    TAG_SYNC_GAP = hub.TAG_SYNC_GAP
    TAG_SYNC_MAX = hub.TAG_SYNC_MAX
    TAG_SYNC_RETRIES = hub.TAG_SYNC_RETRIES
    _TAG_SYNCING = hub._TAG_SYNCING
    _level_of = hub._level_of
    _tag_call = hub._tag_call
    _tag_group_admins = hub._tag_group_admins
    _tag_is_expected_skip = hub._tag_is_expected_skip
    _tag_sanitize = hub._tag_sanitize
    game_chips = hub.game_chips
    logger = hub.logger
    save_data = hub.save_data
    sget = hub.sget
    tag_synced = hub.tag_synced
    total_earned = hub.total_earned
    res = {"total": 0, "ok": 0, "skip": 0, "fail": 0, "resumed": 0, "reasons": {},
           "skip_reasons": {}, "admins_skipped": 0, "flood": 0, "busy": False, "first_err": ""}
    if cid in _TAG_SYNCING:
        res["busy"] = True      # 已在同步中：直接返回，避免用户连点把配额打爆
        return res
    _TAG_SYNCING.add(cid)
    try:
        if not sget("LEVEL_SYNC_TAG"):
            return res
        # 候选 = 有累计账本 ∪ 有钱包的人（一律 .get，防 defaultdict 幽灵键，§4.31）
        cand = set()
        for src in (total_earned.get(cid) or {}, game_chips.get(cid) or {}):
            for u in list(src.keys()):
                try:
                    u = int(u)
                except (TypeError, ValueError):
                    continue
                if u > 0:
                    cand.add(u)
        # 群主 + 管理员：平台不让 bot 改他们的标签，提前排除（否则一批必然失败）
        admin_ids = await _tag_group_admins(app, cid)
        res["admins_skipped"] = len(cand & admin_ids)
        # limit 默认 None（**不是** TAG_SYNC_MAX）：默认参数在 def 时求值，写成常量的话
        # 网页改了「批量同步上限」也不会生效 —— 老 bug 形态，这里现读，改完立即生效。
        _cap = max(1, int(limit or sget("TAG_SYNC_MAX") or TAG_SYNC_MAX))
        # ── 断点续传 ──────────────────────────────────────────────────────
        # tag_synced 记着「谁上一轮已经同步成什么标签」。先把这些人摘掉**再**截上限：
        # 排序键固定是 uid ⇒ 被限流打爆 / 容器重部署之后，下一轮自动从没做完的地方
        # 接着跑，不会每次都从第一个人重来（人多的群否则永远同步不完）。
        # 标记里存的是"标签值"而不只是"同步过"：等级名一改，标记自动失效 → 重新同步，
        # 不会留下陈旧标签。
        todo = []
        for _u in sorted(cand - admin_ids):
            _t = _tag_sanitize(_level_of(cid, _u)[0])
            if _t and tag_synced.get(f"{cid}:{_u}") == _t:
                res["resumed"] += 1
                continue
            todo.append(_u)
        uids = todo[:_cap]
        res["total"] = len(uids)
        gap = TAG_SYNC_GAP
        _dirty = False          # 断点是否已改动、还没落盘
        for i, uid in enumerate(uids):
            tag = _tag_sanitize(_level_of(cid, uid)[0])
            if not tag:                       # 没等级 / 净化后为空 ⇒ 绝不能发空标签（=清除标签）
                res["skip"] += 1
                # 记下"为什么跳过"：只报「跳过 1 人」管理员会以为功能又坏了
                _why = "未达任何等级（等级名为空，或等级名净化后为空）"
                res["skip_reasons"][_why] = res["skip_reasons"].get(_why, 0) + 1
            else:
                ok, info = False, ""
                for _attempt in range(TAG_SYNC_RETRIES + 1):
                    ok, info, wait = await _tag_call(app.bot, cid, uid, tag)
                    if ok or wait <= 0:
                        break
                    res["flood"] += 1
                    gap = min(gap * 2, 30.0)  # 被限速 ⇒ 自适应拉长间隔，别硬冲
                    await asyncio.sleep(min(wait, 90) + 0.5)
                if ok:
                    res["ok"] += 1
                    # 记断点：这一轮即使随后被打断，下一轮也不会再为这个人花配额
                    tag_synced[f"{cid}:{uid}"] = tag
                    _dirty = True
                elif _tag_is_expected_skip(info):
                    res["skip"] += 1      # 平台规则决定的失败，别算进"失败"吓人
                    res["skip_reasons"][info] = res["skip_reasons"].get(info, 0) + 1
                else:
                    res["fail"] += 1
                    res["reasons"][info] = res["reasons"].get(info, 0) + 1
                    if not res["first_err"]:
                        res["first_err"] = info
            if progress and (i + 1) % 20 == 0:
                try:
                    await progress(i + 1, len(uids), res)
                except Exception:
                    logger.debug("同步进度回调失败（已忽略）", exc_info=True)
            # ⚠️ 断点必须**边跑边落盘**：只在收尾存的话，容器一重启整轮进度全丢，
            #    下一轮还是从头来 —— 那就不叫断点续传了。
            if _dirty and (i + 1) % 20 == 0:
                save_data(); _dirty = False
            if i + 1 < len(uids):
                await asyncio.sleep(gap)
        if _dirty:
            save_data()
        return res
    finally:
        _TAG_SYNCING.discard(cid)


def _tag_eta_seconds(n):
    """按当前限速估算批量同步耗时（秒），用于提前告诉用户要等多久。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    TAG_SYNC_GAP = hub.TAG_SYNC_GAP
    try:
        return max(0, int(n or 0)) * TAG_SYNC_GAP
    except (TypeError, ValueError):
        return 0


def _tag_sync_line(cid, r):
    """把一次同步结果格式化成给人看的几行（群命令与网页后台共用同一份文案）。

    2026-09-11 生产实测后改进：原来只报「成功/失败」两个数，用户看到
    「失败 21 人」会以为功能又坏了 —— 其实里面混着**平台规则决定的必然跳过**
    （群主/管理员），和**限速后自动重试成功**的人。现在分开报，并给出可执行的下一步。
    """
    if r.get("busy"):
        return [f"· <code>{cid}</code>：⏳ 正在同步中，请等这一轮跑完再点（避免触发限速）"]
    if not r["total"]:
        skip = r.get("admins_skipped", 0)
        extra = f"（另有 {skip} 位群主/管理员，平台不允许改其标签）" if skip else ""
        if r.get("resumed"):
            return [f"· <code>{cid}</code>：✅ 已同步过 {r['resumed']} 人"
                    f"（断点续传：没有新的要处理）{extra}"]
        return [f"· <code>{cid}</code>：没有需要同步的成员，跳过{extra}"]
    line = f"· <code>{cid}</code>：✅ 成功 {r['ok']} 人"
    if r.get("resumed"):
        # 让管理员看出"这一轮是接着上次跑的"，而不是以为系统把谁漏了
        line += f" ｜ ⏭ 续传跳过已同步 {r['resumed']} 人"
    if r.get("admins_skipped"):
        line += f" ｜ 🚫 群主/管理员 {r['admins_skipped']} 人（平台不允许 bot 改）"
    if r["skip"]:
        line += f" ｜ ⏭ 跳过 {r['skip']} 人"
    if r["fail"]:
        line += f" ｜ ❌ 失败 {r['fail']} 人"
    out = [line]
    if r.get("flood"):
        out.append(f"    ↳ 期间被 Telegram 限速 {r['flood']} 次，已自动等待并加长间隔")
    # 跳过原因也列出来（不列的话管理员只看到「跳过 N 人」，会以为功能又坏了）
    for why, n in sorted((r.get("skip_reasons") or {}).items(), key=lambda kv: -kv[1])[:2]:
        out.append(f"    ↳ ⏭ {n} 人：{html.escape(str(why))}")
    for why, n in sorted(r["reasons"].items(), key=lambda kv: -kv[1])[:2]:
        out.append(f"    ↳ ❌ {n} 人：{html.escape(str(why))}")
    return out


async def sync_member_tags_notify(app, cid):
    """后台（网页按钮）触发的批量同步：丢后台跑，完成后私聊回报；中途报一次进度。

    为什么必须非阻塞 + 报进度：按 3.2 秒/人的限速，100 人要约 5 分钟。
    网页请求不能挂那么久，用户也不该看到"点了没反应"。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _admin_notify_ids = hub._admin_notify_ids
    _tag_sync_line = hub._tag_sync_line
    logger = hub.logger
    sync_member_tags = hub.sync_member_tags
    try:
        async def _prog(done, total, r):
            txt = (f"🏷 <b>成员标签同步中…</b>\n群 <code>{cid}</code>："
                   f"{done}/{total}（已成功 {r['ok']}）")
            for rid in hub._admin_notify_ids():
                try:
                    await app.bot.send_message(chat_id=rid, text=txt)
                except Exception:
                    pass

        r = await sync_member_tags(app, cid, progress=_prog)
        txt = "🏷 <b>成员标签同步完成</b>\n" + "\n".join(_tag_sync_line(cid, r))
        for rid in _admin_notify_ids():
            try:
                await app.bot.send_message(chat_id=rid, text=txt, parse_mode="HTML")
            except Exception:
                pass
        return r
    except Exception:
        logger.exception("网页后台同步成员标签失败")
        return None


async def cmd_sync_tags(update, context):
    """补同步成员标签（管理员）：把「积分称号」写进 Telegram 成员标签。

    用法：群里发「同步标签」；「同步标签 全部」＝对全部授权群依次同步。
    典型场景：修好了标签功能后，把老玩家的标签一次性补齐。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    _spawn_background = hub._spawn_background
    _sync_tags_report = hub._sync_tags_report
    _tag_eta_seconds = hub._tag_eta_seconds
    is_bot_admin = hub.is_bot_admin
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context):
        return
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "⛔ 只有管理员能同步成员标签。")
        return
    if not sget("LEVEL_SYNC_TAG"):
        await send_reply(update, context,
                         "⚠️ 后台「积分称号同步成员标签」开关是关闭状态，先开启再同步。")
        return
    try:
        arg = (context.args[0] if context.args else "").strip()
    except Exception:
        arg = ""
    cid = update.effective_chat.id
    if arg in ("全部", "所有", "all"):
        targets = sorted(int(c) for c in AUTHORIZED_GROUPS if int(c) < 0)
    else:
        if cid > 0:
            await send_reply(update, context, "⚠️ 成员标签只在群里生效，请到群里发本命令。")
            return
        targets = [cid]
    if not targets:
        await send_reply(update, context, "⚠️ 没有可同步的群。")
        return

    # 非阻塞：按限速 3.2 秒/人，100 人要 5 分钟 —— 同步等会让这条命令看起来"卡死"。
    # 改成后台跑 + 完成后在群里回报（同网页后台口径）。
    await send_reply(update, context,
                     f"🏷 已开始在后台同步成员标签（{len(targets)} 个群，上限 {sget('TAG_SYNC_MAX')} 人/群）\n"
                     f"⏳ 按 Telegram 限速约需 {_tag_eta_seconds(sget('TAG_SYNC_MAX')) // 60} 分钟内完成，跑完汇报结果。")
    _spawn_background(_sync_tags_report(context.application, targets, cid))

# ── 2026-09-14：从 features/admin_report 搬来 ──────────────────────────
# 它是**成员标签批量同步**的群内回报（被本模块的 cmd_sync_tags 调用），
# 与「经营日报」无关 —— 只是当年恰好和日报住在同一个模块里。
# 日报整体下线后，把它搬回它真正属于的域。
async def _sync_tags_report(app, targets, report_cid):
    """后台跑完批量同步后在群里回报（群命令入口用）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _tag_sync_line = hub._tag_sync_line
    logger = hub.logger
    safe_send = hub.safe_send
    sync_member_tags = hub.sync_member_tags
    try:
        lines = []
        for c in targets:
            lines += _tag_sync_line(c, await sync_member_tags(app, c))
        await safe_send(app.bot, report_cid,
                        "🏷 <b>成员标签同步完成</b>\n" + "\n".join(lines), parse_mode="HTML")
    except Exception:
        logger.exception("群命令同步成员标签失败")
