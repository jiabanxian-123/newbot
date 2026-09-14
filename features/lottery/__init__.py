# -*- coding: utf-8 -*-
"""feature/lottery —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from datetime import datetime, timedelta, timezone
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
import asyncio
import html
import random
import time

def _migrate_stale_lottery_tpl(cfg: dict):
    """把存档里「旧版内置默认」的抽奖模板升到当前默认（自定义模板原样保留）。

    幂等：已是新默认 / 是自定义内容 → 原样返回。返回是否发生过替换，供日志与测试使用。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _KEY2VAR = hub._KEY2VAR
    _LOTTERY_TPL_SIGS = hub._LOTTERY_TPL_SIGS
    changed = False
    for key, (sig, _cur) in _LOTTERY_TPL_SIGS.items():
        v = cfg.get(key)
        if isinstance(v, str) and sig in v:
            gname = _KEY2VAR.get(key)
            if gname:
                cfg[key] = hub.namespace().get(gname, v)
                changed = True
    return changed


def _lottery_render_prizes(prizes):
    """奖品列表渲染，每档一行（照阿福「奖品列表模板」= <b>名称</b> X <b>数量</b>）。"""
    return "\n".join(f"<b>{html.escape(str(p['name']))}</b> X <b>{int(p['count'])}</b>" for p in prizes)


def _lottery_kb(lo):
    """参与按钮：文案带实时参与人数（照阿福按钮「参与抽奖 N」）。"""
    n = len(lo.get("participants", []))
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"参与抽奖 {n}", callback_data="lot_join")]])


def _lottery_parse_end(spec: str):
    """解析开奖时间字段：纯数字=秒数；'20:00'=今天(已过顺延明天)；'09-08 20:00'=今年(已过顺延明年)；
    '2026-09-08 20:00'=指定日期。均按北京时间。非法返回 None。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BEIJING_TZ = hub.BEIJING_TZ
    now_bj = hub.now_bj
    s = (spec or "").strip().replace("：", ":")
    if not s:
        return None
    if s.isdigit():
        return time.time() + max(10, min(7 * 86400, int(s)))
    now = now_bj()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m-%d %H:%M", "%m-%d %H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt).replace(tzinfo=BEIJING_TZ)  # strptime 产出 naive，必须补时区才能与 now_bj 比较
        except ValueError:
            continue
        if fmt == "%H:%M":
            dt = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
        elif fmt.startswith("%m-%d"):
            dt = t.replace(year=now.year)
            if dt <= now:
                dt = dt.replace(year=now.year + 1)
        else:
            dt = t
        ts = dt.timestamp()
        return time.time() + 10 if ts < time.time() + 10 else ts  # 至少留 10 秒
    return None


def _lottery_form_parse(form):
    """网页创建抽奖表单（阿福格式）→ (fields, err)。

    fields: title/desc/keyword/prizes/min_bal/fee/end_ts。
    奖品：结构化行 prize_name[] + prize_count[]（优先），无则回退旧 textarea prizes。
    开奖方式 mode：'time'=定时开奖（按北京时间解析 endtime）；'duration'=倒计时秒数。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _lottery_parse_end = hub._lottery_parse_end
    _lottery_parse_prizes = hub._lottery_parse_prizes
    sget = hub.sget
    title = (form.get("title", [""])[0] or "").strip()
    if not title or len(title) > 50:
        return None, "标题不能为空或超过 50 字"
    desc = (form.get("desc", [""])[0] or "").strip()[:300]
    keyword = (form.get("keyword", [""])[0] or "").strip() or sget("LOTTERY_KEYWORD")
    mode = form.get("mode", ["duration"])[0]
    min_bal_raw = (form.get("min_bal", [""])[0] or "").strip()
    if min_bal_raw:
        try:
            min_bal = max(0, int(min_bal_raw))
        except ValueError:
            return None, "参与门槛（最低积分）必须是数字"
    else:
        min_bal = 0   # 留空=不限制（门槛只跟活动走，不再有全局默认）
    # 参与费（2026-09-12 用户报「我设置要 188 积分结果根本不扣分」）：
    #   旧版网页表单**只有门槛、没有参与费**，管理员填的 188 其实是「最低持有积分」——
    #   它只挡人、不扣分。现在加显式「参与费」字段，留空才回退全局 LOTTERY_FEE。
    fee_raw = (form.get("fee", [""])[0] or "").strip()
    if fee_raw:
        try:
            fee = max(0, int(fee_raw))
        except ValueError:
            return None, "参与费（参与扣积分）必须是数字"
    else:
        fee = int(sget("LOTTERY_FEE") or 0)
    # 奖品：结构化行优先，回退 textarea
    names = [x.strip() for x in form.get("prize_name", [])]
    if names:
        counts = [x.strip() for x in form.get("prize_count", [])]
        spec = ",".join(f"{n}:{(counts[i] if i < len(counts) and counts[i] else '1')}"
                        for i, n in enumerate(names) if n)
    else:
        spec = (form.get("prizes", [""])[0] or "").strip().replace("\r", "").replace("\n", ",")
    prizes, perr = _lottery_parse_prizes(spec)
    if perr:
        return None, perr
    if mode == "time":
        end_ts = _lottery_parse_end(form.get("endtime", [""])[0])
        if end_ts is None:
            return None, "开奖时间格式不对：支持 20:00 / 09-08 20:00 / 2026-09-08 20:00"
    else:
        dur_raw = (form.get("duration", [""])[0] or "").strip()
        try:
            duration = max(10, min(7 * 86400, int(dur_raw)))
        except ValueError:
            return None, "持续秒数必须是数字"
        end_ts = time.time() + duration
    return {"title": title, "desc": desc, "keyword": keyword, "prizes": prizes,
            "min_bal": min_bal, "fee": fee, "end_ts": end_ts}, ""


def _lottery_parse_prizes(spec: str):
    """解析「奖品A:数量,奖品B:数量」；空 / 非法返回 ([], err)。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    LOTTERY_MAX_PRIZES = hub.LOTTERY_MAX_PRIZES
    sget = hub.sget
    if not spec:
        return [], "奖品不能为空"
    out, seen = [], set()
    for chunk in spec.replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            return [], f"奖品格式错误：{chunk}（应为 名称:数量）"
        name, _, cnt = chunk.partition(":")
        name = name.strip()
        cnt = cnt.strip()
        if not name:
            return [], f"奖品名称不能为空：{chunk}"
        try:
            n = int(cnt)
        except ValueError:
            return [], f"奖品数量必须是数字：{chunk}"
        if n <= 0:
            return [], f"奖品数量必须 >0：{chunk}"
        if name in seen:
            return [], f"重复奖品：{name}"
        seen.add(name)
        out.append({"name": name, "count": n})
    if not out:
        return [], "奖品不能为空"
    _max_prizes = int(sget("LOTTERY_MAX_PRIZES") or LOTTERY_MAX_PRIZES)
    if len(out) > _max_prizes:
        return [], f"奖品最多 {_max_prizes} 档"
    return out, ""


def _lottery_active(cid):
    """返回进行中的活动；不存在或已结束返回 None。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    lotteries = hub.lotteries
    lo = lotteries.get(cid)
    if not lo or lo.get("status") != "open":
        return None
    return lo


def _lottery_match_join_words(active, defaults):
    """参与词判定 —— 返回 (join_words, suppressed)（2026-09-13 从 cmd_lottery 抽出）。

    三种词的关系（2026-09-12 用户报过「我设置了指定一句话，结果发『抽奖』两个
    字也能参与」）：
      · 活动设了**自定义参与词** ⇒ 默认词失效，只认那一个词
        → join_words={自定义词}，suppressed=默认词（用默认词来参与时要明确提示）
      · 否则 ⇒ 默认词（+ 自定义词若有）都算参与
        → join_words=默认词∪{自定义词}，suppressed=∅

    ★ 纯函数：不看 hub、不判权限、不发消息，只回答「哪些词算参与」。
      调用方（cmd_lottery）拿到结果后再决定怎么回执。
    """
    kw = (active.get("keyword") or "").strip() if active else ""
    if active and kw and kw not in defaults:
        return {kw}, set(defaults)
    return set(defaults) | ({kw} if kw else set()), set()


def _lottery_join(lo, uid, name):
    """加入参与者；返回 (ok, reason_or_index)。已加入返回 (False, 'dup')。"""
    for i, (u, _, _) in enumerate(lo["participants"]):
        if u == uid:
            return False, "dup"
    lo["participants"].append((uid, time.time(), name))
    return True, len(lo["participants"])


async def _lottery_publish(app, cid, lo):
    """编辑/发送活动公告消息；记录 msg_id 用于开奖后编辑。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _lottery_announce_text = hub._lottery_announce_text
    _lottery_kb = hub._lottery_kb
    logger = hub.logger
    safe_send = hub.safe_send
    sget = hub.sget
    if not sget("LOTTERY_ENABLED"): return
    # ⚠️ autodel_keep：抽奖公告是**常驻活动看板**，绝不能进「默认自动删除」
    #   （2026-09-12 用户报障：群里刚发起抽奖，公告就被删了）。
    #   它还要在开奖时被 edit 成「已开奖 ✅」，被删掉连 edit 都无处可改。
    # 2026-09-12 用户报「抽奖不知道怎么参与、也没有按钮」→ 公告直接挂一个「参与抽奖」按钮，
    #   不点也行（照样能发关键词），但按钮让新人有明确的入口。
    kb = _lottery_kb(lo)
    msg = await safe_send(app.bot, cid, _lottery_announce_text(lo), parse_mode="HTML",
                          reply_markup=kb, autodel_keep=True)
    if msg:
        lo["msg_id"] = msg.message_id
        # 置顶公告（照阿福「抽奖消息置顶」）。机器人需为管理员且有置顶权限；
        # 没权限就报错 → 只记日志，绝不因为置顶失败而中断抽奖本身。
        if sget("LOTTERY_PIN_MSG"):
            try:
                await app.bot.pin_chat_message(chat_id=cid, message_id=msg.message_id,
                                               disable_notification=True)
            except Exception:
                logger.info("抽奖公告置顶失败（机器人可能无置顶权限），已跳过", exc_info=True)


def _lottery_announce_text(lo):
    """公告文案（照阿福版式：纯文本 + 空行分块 + 「标签：值」行 + 参与要求树形）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BEIJING_TZ = hub.BEIJING_TZ
    _lottery_render_prizes = hub._lottery_render_prizes
    sget = hub.sget
    end_line = datetime.fromtimestamp(lo["end_ts"], BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    _fee = int(lo.get("fee") or 0)
    fee_block = f"\n消耗积分：<b>{_fee}</b> 积分\n" if _fee else ""
    desc = (lo.get("desc") or "").strip()
    desc_block = f"\n{html.escape(str(desc))}\n" if desc else ""
    kw = (lo.get("keyword") or "").strip()
    keyword_block = f"\n参与关键词：<b>{html.escape(str(kw))}</b>\n" if kw else ""
    # 参与要求：非末项 ➕、末项 └（照阿福截图里的树形）。本机器人目前只有「最低持有积分」
    # 一条要求；阿福那种「加入频道」条件还没有，将来要加就往 _reqs 里追加。
    min_bal = int(lo.get("min_bal") or 0)   # 门槛只跟活动走：留空/0=不限，N=门槛N
    _reqs = []
    if min_bal > 0:
        _reqs.append(f"💰 持有积分大于 {min_bal}")
    req_block = ""
    if _reqs:
        _lines = [f"➕ {t}" for t in _reqs[:-1]] + [f"└ {_reqs[-1]}"]
        req_block = "\n参与要求：\n" + "\n".join(_lines) + "\n"
    n = len(lo.get("participants", []))
    # ⚠️ 同时给「新模板 + 旧模板」两套占位符：老版本存过自定义模板的群（bot_data.json 里
    #   那份以描述行/门槛行/扣费行为占位符的公告模板），若只传新键会让 .format() 抛
    #   KeyError，整条公告塌成兜底文案。两套都传 ⇒ 新旧模板都能正常渲染。
    kwargs = {
        # 新版（阿福版式）
        "title": html.escape(str(lo["title"])), "desc_block": desc_block, "fee_block": fee_block,
        "end_line": end_line, "keyword_block": keyword_block,
        "prize_list": _lottery_render_prizes(lo["prizes"]),
        "n": n, "keyword": html.escape(str(kw)), "req_block": req_block,
        # 旧版占位符（兼容存量自定义模板，勿删）
        "desc_line": (f"📖 {html.escape(str(desc))}\n" if desc else ""),
        "fee_line": (f"💰 参与扣 <b>{_fee}</b> 积分\n" if _fee else ""),
        "min_line": (f"门槛：<b>{min_bal}</b> 积分以上可参与\n" if min_bal else ""),
        "duration": int(lo["end_ts"] - lo["start_ts"]),
    }
    try:
        text = sget("LOTTERY_MSG_START").format(**kwargs)
    except (KeyError, IndexError, ValueError):
        text = ""
    # ⚠️ 2026-09-12 用户报「抽奖看不到人数」——多半是后台存了一份**旧模板/自定义模板**，
    #   里面没有人数占位符。所以这里做兜底：格式化结果里只要没出现「已参与」，
    #   就无条件补一行人数，保证「人数」永远看得见。
    if "已参与" not in (text or ""):
        text = (text or f"🎉 抽奖标题：{html.escape(str(lo['title']))}\n\n定时开奖 {end_line}") + \
               f"\n\n已参与：<b>{n}</b> 人"
    return text


async def _lottery_refresh_announce(app, cid, lo):
    """有人参与后刷新公告上的已参与人数（编辑失败静默，不影响参与流程）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _lottery_announce_text = hub._lottery_announce_text
    _lottery_kb = hub._lottery_kb
    if not lo.get("msg_id"):
        return
    try:
        await app.bot.edit_message_text(chat_id=cid, message_id=lo["msg_id"],
                                        text=_lottery_announce_text(lo), parse_mode="HTML",
                                        reply_markup=_lottery_kb(lo))
    except Exception:  # silent-ok: 刷新参与人数失败不影响参与，下次有人参与会再刷
        pass


async def _lottery_draw(app, cid, lo):
    """开奖：从参与者中按奖品库存随机抽；写回 lo['winners']/prizes 剩余库存；发群通知 + 私聊中奖者。

    返回 True=本次实际开奖；False=活动已不在 open 状态（防定时扫描与手动开奖并发重复开奖、覆盖中奖名单）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    LOTTERY_WIN_LINE = hub.LOTTERY_WIN_LINE
    logger = hub.logger
    safe_send = hub.safe_send
    save_data = hub.save_data
    sget = hub.sget
    if lo.get("status") != "open":
        return False
    lo["status"] = "drawing"
    prizes = [dict(p) for p in lo["prizes"]]  # 拷贝并加 left 字段
    for p in prizes:
        p["left"] = int(p["count"])
    pool = list(lo["participants"])
    winners = []
    # 总中奖数 ≤ 总奖品数 且 ≤ 参与人数
    while pool and any(p["left"] > 0 for p in prizes):
        # 选一个仍有库存的奖品
        avail = [p for p in prizes if p["left"] > 0]
        if not avail: break
        prize = random.choice(avail)
        # 选一个参与者
        idx = random.randrange(len(pool))
        uid, _ts, name = pool.pop(idx)
        winners.append({"uid": uid, "name": name, "prize": prize["name"]})
        prize["left"] -= 1
    lo["prizes"] = prizes
    lo["winners"] = winners
    # 群内通知：结果单独发一条醒目消息（绝不自动删除，永久保留在群里）
    if winners:
        win_lines = [LOTTERY_WIN_LINE.format(name=html.escape(str(w["name"])), prize=html.escape(str(w["prize"])))
                     for w in winners]
        try:
            text = sget("LOTTERY_MSG_RESULT").format(
                title=html.escape(str(lo["title"])),
                winners="\n".join(win_lines),
                n=len(lo["participants"]),
                w=len(winners),
            )
        except (KeyError, IndexError, ValueError, AttributeError):
            # ★ 后台把「开奖结果模板」改成含未提供占位符（如 {prize_list}）时，
            #   .format 会抛异常；异常上抛后 lo["status"] 永远停在 "drawing"，
            #   而 _lottery_active / _lottery_try_join 只认 "open" →
            #   既不能再开奖，也不能结束退款（参与费已扣）。这里兜底成默认文案，
            #   与同文件 _lottery_announce_text 的做法保持一致。
            text = (f"🎉 <b>{html.escape(str(lo['title']))}</b> · 开奖结果\n"
                    + "\n".join(win_lines)
                    + f"\n\n📊 共 {len(lo['participants'])} 人参与，{len(winners)} 人中奖")
    else:
        text = (f"🎉 <b>{html.escape(str(lo['title']))}</b> · 开奖结果\n"
                "\n😢 本轮无人中奖（参与人数不足）\n"
                f"📊 共 {len(lo['participants'])} 人参与")
    # 原公告编辑为已开奖状态（原文不再覆盖成结果——结果要独立醒目公布）
    done_line = f"🎉 <b>{html.escape(str(lo['title']))}</b> 已开奖 ✅ 结果见下方开奖公告"
    try:
        if lo.get("msg_id"):
            # autodel-keep：这是**永久公示**（开奖后仍留在群里），不是临时面板；
            # 顺带把「参与抽奖」按钮摘掉——活动已结束，按钮再点只会报错。
            # 用空键盘移除按钮（PTB 里 reply_markup=None 表示"不改动键盘"，摘不掉）。
            await app.bot.edit_message_text(chat_id=cid, message_id=lo["msg_id"],
                                            text=done_line, parse_mode="HTML",
                                            reply_markup=InlineKeyboardMarkup([]))
    except Exception:
        logger.exception("编辑公告为已开奖状态失败（忽略）")
    # ⚠️ autodel_keep：开奖结果是**永久公示**（上面注释写的「绝不自动删除」要靠这个参数兑现），
    #   否则会被「默认自动删除」在几分钟后收走（2026-09-12 用户报障：开奖也要保留）。
    result_msg = await safe_send(app.bot, cid, text, parse_mode="HTML", autodel_keep=True)
    if not result_msg:
        # 编辑路径已废弃，新消息也失败则退回编辑公告兜底
        try:
            if lo.get("msg_id"):
                await app.bot.edit_message_text(chat_id=cid, message_id=lo["msg_id"],
                                                text=text, parse_mode="HTML")
        except Exception:
            logger.exception("开奖结果兜底编辑也失败")
    # 置顶开奖结果（照阿福「抽奖结果置顶」）。**先置顶结果、成功后再取消公告的置顶**，
    # 免得群里同时挂两条置顶；任何一步没权限都只记日志，不影响开奖已经完成的事实。
    if result_msg and sget("LOTTERY_PIN_RESULT"):
        try:
            await app.bot.pin_chat_message(chat_id=cid, message_id=result_msg.message_id,
                                           disable_notification=True)
            if lo.get("msg_id"):
                try:
                    await app.bot.unpin_chat_message(chat_id=cid, message_id=lo["msg_id"])
                except Exception:
                    logger.info("取消抽奖公告置顶失败，已跳过", exc_info=True)
        except Exception:
            logger.info("抽奖结果置顶失败（机器人可能无置顶权限），已跳过", exc_info=True)
    # 中奖信息推送一份给管理员私聊（与群内同文，永久保留）
    try:
        await app.bot.send_message(ADMIN_USER_ID, "📬 抽奖开奖推送\n\n" + text, parse_mode="HTML")
    except Exception:
        logger.exception("开奖结果推送管理员失败（忽略）")
    # 私聊中奖者
    for w in winners:
        try:
            await app.bot.send_message(chat_id=w["uid"],
                text=f"🎉 恭喜！你中奖了：<b>{html.escape(str(w['prize']))}</b>\n来自活动：{html.escape(str(lo['title']))}",
                parse_mode="HTML")
        except Exception:
            pass  # 对方没私聊过 bot 也没关系
    lo["status"] = "finished"
    lo["end_ts"] = time.time()
    save_data()
    return True


async def _lottery_admin_subcommand(update, context, cid, uid, args_part):
    """管理员子命令「开奖 / 结束」（2026-09-13 从 cmd_lottery 抽出）。

    返回 True = 已识别并处理（调用方直接 return）；
    返回 False = 不是子命令，交给后续分支（玩家参与 / 开局解析）。

    ★ 逻辑原样搬运，一字未改：
      · 「开奖」→ 立即开奖
      · 「结束 / 取消」→ 退款（按人头逐个退）→ 置 cancelled → 公告
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _lottery_active = hub._lottery_active
    _lottery_draw = hub._lottery_draw
    game_chips = hub.game_chips
    ledger_add = hub.ledger_add
    safe_send = hub.safe_send
    save_data = hub.save_data
    send_reply = hub.send_reply
    if args_part in ("开奖", "开奖开奖", "开", "开奖", "开奖开奖"):
        lo = _lottery_active(cid)
        if not lo:
            await send_reply(update, context, "❌ 当前没有进行中的抽奖活动"); return True
        await send_reply(update, context, "🎲 正在开奖…")
        if not await _lottery_draw(context.application, cid, lo):
            await send_reply(update, context, "ℹ️ 本活动已开奖/已结束，请勿重复操作")
        return True
    if args_part in ("结束", "取消"):
        lo = _lottery_active(cid)
        if not lo:
            await send_reply(update, context, "❌ 当前没有进行中的抽奖活动"); return True
        # 退积分（若有扣费）
        if lo["fee"] > 0:
            for u, _, _ in lo["participants"]:
                game_chips[cid][u] = game_chips[cid].get(u, 0) + lo["fee"]
                ledger_add(cid, 0, u, lo["fee"], "抽奖退款")   # 台账：系统→玩家（退款）
        lo["status"] = "cancelled"
        save_data()
        await safe_send(context.application.bot, cid,
            f"🛑 抽奖活动「<b>{html.escape(str(lo['title']))}</b>」已被管理员取消" + (
                f"，已退还 {lo['fee']} 积分/人" if lo["fee"] else ""))
        return True
    return False


async def _lottery_create(update, context, cid, uid, args_part):
    """管理员开新活动：解析「标题 | 奖品 | 秒数」→ 建活动字典 → 发布。

    （2026-09-13 从 cmd_lottery 抽出，逻辑原样搬运、一字未改。）
    返回 True 表示已成功发布；False 表示校验失败（已回过错）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _lottery_parse_end = hub._lottery_parse_end
    _lottery_parse_prizes = hub._lottery_parse_prizes
    _lottery_publish = hub._lottery_publish
    lotteries = hub.lotteries
    save_data = hub.save_data
    send_reply = hub.send_reply
    sget = hub.sget
    # 解析 "标题 | 奖品 | 秒数"（秒数可选，奖品必填）
    parts = [p.strip() for p in args_part.split("|")]
    title = parts[0]
    if not title or len(title) > 50:
        await send_reply(update, context, "❌ 标题不能为空或超过 50 字"); return False
    if len(parts) < 2:
        await send_reply(update, context, 
            "用法：\n"
            "<code>/开奖 标题 | 奖品A:数量,奖品B:数量 | 秒数或开奖时间</code>\n\n"
            "示例：\n"
            "<code>/开奖 群友福利 | 100积分:1,小星星:5 | 60</code>（60 秒后开）\n"
            "<code>/开奖 群友福利 | 100积分:1 | 20:00</code>（今晚 8 点开）\n"
            "<code>/开奖 群友福利 | 100积分:1 | 09-08 20:00</code>（指定日期）",
            parse_mode="HTML"); return False
    prizes, perr = _lottery_parse_prizes(parts[1])
    if perr:
        await send_reply(update, context, f"❌ {perr}"); return False
    # 第三段：纯数字=秒数倒计时；或指定开奖时间（20:00 / 09-08 20:00 / 2026-09-08 20:00）
    end_ts = None
    if len(parts) >= 3 and parts[2]:
        end_ts = _lottery_parse_end(parts[2])
        if end_ts is None:
            await send_reply(update, context, 
                "❌ 开奖时间格式不对\n\n支持：<code>90</code>（90秒后）、<code>20:00</code>、"
                "<code>09-08 20:00</code>、<code>2026-09-08 20:00</code>",
                parse_mode="HTML"); return False
    duration = max(10, int(end_ts - time.time())) if end_ts else sget("LOTTERY_DEFAULT_DURATION")
    lotteries[cid] = {
        "title": title, "prizes": prizes, "fee": int(sget("LOTTERY_FEE")),
        "keyword": sget("LOTTERY_KEYWORD"), "start_ts": time.time(),
        "end_ts": end_ts or (time.time() + duration), "msg_id": None,
        "participants": [], "status": "open", "winners": [],
        "creator": uid, "chat_id": cid,
    }
    save_data()
    await _lottery_publish(context.application, cid, lotteries[cid])
    return True


async def cmd_lottery(update, context):
    """群组抽奖：管理员用 /开奖 <标题> | <奖品> | <秒数> 开局；玩家用 /开奖 或 关键词 参与。

    用法：
      /开奖                              → 若活动进行中视为参与；否则帮助
      /开奖 <标题> | <奖品> | <秒数>      → 管理员开新活动
      /开奖开奖                           → 管理员手动立即开奖
      /开奖结束                           → 管理员强制结束并退款（按需）
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _lottery_active = hub._lottery_active
    _lottery_admin_subcommand = hub._lottery_admin_subcommand
    _lottery_create = hub._lottery_create
    _lottery_draw = hub._lottery_draw
    _lottery_match_join_words = hub._lottery_match_join_words
    _lottery_parse_end = hub._lottery_parse_end
    _lottery_parse_prizes = hub._lottery_parse_prizes
    _lottery_publish = hub._lottery_publish
    _lottery_refresh_announce = hub._lottery_refresh_announce
    _lottery_try_join = hub._lottery_try_join
    game_chips = hub.game_chips
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    ledger_add = hub.ledger_add
    lotteries = hub.lotteries
    need_auth = hub.need_auth
    safe_send = hub.safe_send
    save_data = hub.save_data
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    if not sget("LOTTERY_ENABLED"):
        await send_reply(update, context, "❌ 群组抽奖已关闭（后台「积分系统→群组抽奖」可开启）"); return
    cid = update.effective_chat.id
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    # 参与形态：/开奖 命令、本活动自定义关键词、全局触发词；其余原样交给开局参数
    _alo = _lottery_active(cid)
    _defaults = {sget("LOTTERY_KEYWORD"), "抽奖"}
    _defaults.discard("")
    # 参与词判定抽成纯函数（见 _lottery_match_join_words 注释：自定义词会顶掉默认词）
    _join_words, _suppressed = _lottery_match_join_words(_alo, _defaults)
    _kw = (_alo.get("keyword") or "").strip() if _alo else ""
    _bare = text[1:].strip() if text.startswith("/") else text
    if text.startswith("/开奖"):
        args_part = text[len("/开奖"):].strip()
    elif _bare in _join_words or text in _join_words:
        args_part = ""
    elif _bare in _suppressed and not is_bot_admin(uid):
        # 用默认词来参与、但本活动只认自定义词 → 明确提示，别让人只收到「仅管理员可以开局」的谜语报错
        await send_reply(update, context,
            f"⚠️ 本活动的参与词是「<b>{html.escape(str(_kw))}</b>」，发它才能参与（不是「{html.escape(str(_bare))}」）。",
            parse_mode="HTML")
        return
    else:
        args_part = text
    # 管理员子命令（开奖 / 结束）抽成独立段：返回 True 表示已处理并给过回执
    if is_bot_admin(uid):
        if await _lottery_admin_subcommand(update, context, cid, uid, args_part):
            return
    # 玩家参与
    if not args_part:
        lo = _lottery_active(cid)
        if not lo:
            await send_reply(update, context, 
                "❌ 当前没有进行中的抽奖\n\n"
                "管理员开局：<code>/开奖 标题 | 奖品A:数量,奖品B:数量 | 秒数或时间</code>",
                parse_mode="HTML")
            return
        ok, info = await _lottery_try_join(context.application, lo, uid, cid)
        if ok:
            name = await get_name(context.application, uid)
            bal = game_chips.get(cid, {}).get(uid, 0)
            _fee = int(lo.get("fee") or 0)
            await send_reply(update, context, 
                sget("LOTTERY_MSG_JOINED").format(nick=html.escape(str(name)), n=info, balance=bal,
                                                  fee_line=(f"💸 已扣 <b>{_fee}</b> 积分\n" if _fee else "")),
                parse_mode="HTML")
            # 公告上的已参与人数实时刷新
            await _lottery_refresh_announce(context.application, cid, lo)
        elif info == "dup":
            name = await get_name(context.application, uid)
            await send_reply(update, context, sget("LOTTERY_MSG_DUP").format(nick=html.escape(str(name))))
        else:
            name = await get_name(context.application, uid)
            await send_reply(update, context, sget("LOTTERY_MSG_FAIL").format(nick=html.escape(str(name)), reason=info))
        return
    # 管理员开新活动（解析 + 建活动字典抽成独立段）
    if not is_bot_admin(uid):
        await send_reply(update, context, "❌ 仅管理员可以开局"); return
    if _lottery_active(cid):
        await send_reply(update, context, "⚠️ 当前群已有进行中的抽奖，请先 /开奖开奖 或 /开奖结束"); return
    ok = await _lottery_create(update, context, cid, uid, args_part)
    return


async def _lottery_try_join(app, lo, uid, cid):
    """尝试加入抽奖：扣积分（若需）、检查门槛。返回 (ok, info_or_err_msg)。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _lottery_join = hub._lottery_join
    game_chips = hub.game_chips
    ledger_add = hub.ledger_add
    save_data = hub.save_data
    user_names = hub.user_names
    if not lo or lo.get("status") != "open":
        return False, "活动已结束"
    if time.time() >= lo["end_ts"]:
        return False, "活动已结束"
    balance = game_chips.get(cid, {}).get(uid, 0)
    min_bal = int(lo.get("min_bal") or 0)   # 门槛只跟活动走：留空/0=不限，N=门槛N
    if min_bal > 0 and balance < min_bal:
        return False, f"余额不足 {min_bal}，无法参与"
    fee = int(lo.get("fee", 0))
    if fee > 0 and balance < fee:
        return False, f"余额不足（需 {fee}）"
    if fee > 0:
        game_chips[cid][uid] = balance - fee
    ok, info = _lottery_join(lo, uid, user_names.get(uid, str(uid)))
    if not ok:
        # 重复参与：退还已扣（按理说前面不会走到这，但保险）
        if fee > 0:
            game_chips[cid][uid] = game_chips[cid].get(uid, 0) + fee
        return False, "dup"
    if fee > 0:
        ledger_add(cid, uid, 0, fee, "抽奖参与")   # 资金流台账：玩家→系统（参与费，积分流水可见）
    save_data()
    return True, info


async def lottery_scheduler(app):
    """每 5 秒扫一遍所有群的超时活动，到点自动开奖。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _lottery_draw = hub._lottery_draw
    logger = hub.logger
    lotteries = hub.lotteries
    while True:
        try:
            now = time.time()
            for cid, lo in list(lotteries.items()):
                if lo.get("status") != "open": continue
                if now < lo["end_ts"]: continue
                await _lottery_draw(app, cid, lo)
        except Exception:
            logger.exception("lottery_scheduler 本轮异常（已吞并继续）")
        await asyncio.sleep(5)
