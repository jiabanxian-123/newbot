# -*- coding: utf-8 -*-
"""feature/invite —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from collections import defaultdict
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
from urllib.parse import parse_qs, quote, urlparse
import html
import time

def _inv_notice_fresh(key, window=60):
    """诊断/失败提示的窗口去重：窗口内同一人只发一次。返回 True=允许发（并登记）。

    注意：只去重「提示类」副作用，不挡归因本身——第二个事件源若带来链接仍会正常归因。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _inv_notice_ts = hub._inv_notice_ts
    now = time.time()
    last = float(_inv_notice_ts.get(key, 0) or 0)
    if last and now - last < max(10, int(window)):
        return False
    _inv_notice_ts[key] = now
    if len(_inv_notice_ts) > 3000:                   # 防无限膨胀：清 1 小时前的旧键
        for k in [k for k, t in _inv_notice_ts.items() if now - float(t) > 3600]:
            _inv_notice_ts.pop(k, None)
    return True


def _inv_dbg(cid, msg):
    """邀请链路调试事件：任何一环（发链接/申请/进群/归因）走到都记录，失败不再无声无息。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    invite_debug = hub.invite_debug
    now_bj = hub.now_bj
    try:
        invite_debug[cid].append(f"{now_bj().strftime('%H:%M:%S')} {msg}")
        invite_debug[cid] = invite_debug[cid][-10:]
    except Exception:
        pass


def _invite_progress_text(uid, cid, my_name, cname):
    """合格邀请结算卡片正文（cmd_my_invite / /link / 刷新按钮共用）。
    计入=名下全部邀请；合格=达质量要求；发放=已发奖次数(≤上限)；拒绝=进群硬门槛不满足。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _rec_awarded = hub._rec_awarded
    _rec_ok = hub._rec_ok
    _rec_rejected = hub._rec_rejected
    invite_records = hub.invite_records
    sget = hub.sget
    recs = [r for r in invite_records.values()
            if r.get("inviter") == uid and (cid is None or r.get("cid") == cid)]
    counted = len(recs)
    qualified = sum(1 for r in recs if _rec_ok(r))
    rejected = sum(1 for r in recs if _rec_rejected(r))
    awarded = sum(1 for r in recs if _rec_awarded(r))
    req = []
    if sget("INVITE_QUALIFY_ENABLED"):
        if sget("INVITE_QUALIFY_MSGS") > 0:
            req.append(f"本群发言 ≥ {sget('INVITE_QUALIFY_MSGS')} 条")
        if sget("INVITE_QUALIFY_POINTS") > 0:
            req.append(f"本群净赚积分 ≥ {sget('INVITE_QUALIFY_POINTS')}")
        if sget("INVITE_QUALIFY_AVATAR"):
            req.append("进群须有头像")
        if sget("INVITE_QUALIFY_USERNAME"):
            req.append("进群须有用户名")
        req_txt = "、".join(req) if req else "无（进群即合格）"
    else:
        req_txt = "已关闭（进群即发奖）"
    L = [
        f"🎟️ <b>合格邀请结算</b> · {html.escape(str(cname))}",
        f"👤 邀请人：{html.escape(str(my_name))} <code>{uid}</code>",
        "━━━━━━━━━━━━━━",
        f"📥 已计入　<b>{counted}</b> 人",
        f"✅ 合格　　<b>{qualified}</b> 人",
        f"💰 已发放　<b>{awarded}</b> 次",
        f"🎁 奖励：每合格 1 人 +{int(sget('INVITE_REWARD'))} 积分（每人最多发 {sget('INVITE_REWARD_TIMES')} 次）",
        f"🎯 质量要求：{html.escape(str(req_txt))}",
        "━━━━━━━━━━━━━━",
    ]
    if rejected:
        L.append(f"🚫 拒绝 {rejected} 人（进群未满足头像/用户名，不发放）")
    return "\n".join(L)


def _invite_card_body_kb(uid, cid, cname, my_name, link, refresh_cb):
    """邀请卡片正文+按钮（群卡片/私聊推送共用）。

    安全：分享按钮走 t.me/share/url?url=<链接>，链接里的 + 在 query 中会被解码成空格
    （t.me/+hash 变 t.me/ 空格hash → 好友收到无效链接），必须整体 URL 编码（+ → %2B）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    INVITE_LINK_CMD = hub.INVITE_LINK_CMD
    _invite_progress_text = hub._invite_progress_text
    body = _invite_progress_text(uid, cid, my_name, cname)
    rows = []
    if link:
        # 2026-09-10 用户报障「专属链接无效」：/link 发的是 deep-link，好友点开后**必须点「开始」**
        # 才会触发归因并拿到加群按钮。旧文案写「点开直接进群」，新人不点开始 → 以为链接无效。
        body += (f"\n🔗 <b>你的专属链接</b>\n<code>{link}</code>\n\n"
                 f"📌 好友点开后，<b>先点页面底部的「开始 / START」</b>，机器人会回他一个"
                 f"「加入群组」按钮，再点一下才能进群（不点开始 = 链接没反应）。\n"
                 f"📌 好友进群先记账为「待达标」，本群达标后自动发奖；也可点下方「刷新进度」立即重判。")
        rows.append([InlineKeyboardButton("打开链接", url=link),
                     InlineKeyboardButton("分享给好友", url="https://t.me/share/url?url=" + quote(link, safe=""))])
    else:
        body += "\n\n📌 发「" + str(INVITE_LINK_CMD) + "」领取本群专属链接。"
    if refresh_cb:
        rows.append([InlineKeyboardButton("🔄 刷新进度", callback_data=refresh_cb)])
    return body, (InlineKeyboardMarkup(rows) if rows else None)


async def _invite_push_card_to_private(context, uid, cid, cname):
    """邀请面板推送到用户私聊（群里发「邀请」时不再刷屏群消息）。

    返回 True=已送达私聊；False=私聊发不出去（用户没 /start 过 bot），调用方应回退群内发送。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_card_body_kb = hub._invite_card_body_kb
    get_name = hub.get_name
    invite_links = hub.invite_links
    logger = hub.logger
    link = (invite_links.get(cid, {}).get(uid) or {}).get("link", "")
    my_name = await get_name(context.application, uid, cid=cid)
    body, kb = _invite_card_body_kb(uid, cid, cname, my_name, link,
                                    f"invite_refresh_priv_{cid}_{uid}")
    try:
        await context.bot.send_message(uid, body, parse_mode="HTML", reply_markup=kb)
        return True
    except Exception:  # silent-ok: 已有 logger.info + 返回 False，调用方会回退群内发送
        logger.info("邀请面板推送私聊失败 uid=%s（用户可能未 /start）", uid)
        return False


async def _invite_send_progress_card(update, context, uid, cid, cname, link=None, edit_msg=None):
    """发/刷新合格结算卡片。edit_msg 存在则编辑原消息（刷新按钮）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_card_body_kb = hub._invite_card_body_kb
    get_name = hub.get_name
    send_reply = hub.send_reply
    my_name = await get_name(context.application, uid, cid=cid if cid else None)
    refresh_cb = f"invite_refresh_{uid}" if cid is not None else None
    body, kb = _invite_card_body_kb(uid, cid, cname, my_name, link, refresh_cb)
    if edit_msg is not None:
        try:
            await edit_msg.edit_text(body, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
    else:
        await send_reply(update, context, body, kb=kb, parse_mode="HTML")


async def _deep_invite_start(update, context, payload):
    """deep-link 邀请确认（t.me/<bot>?start=inv_<邀请人>_<群id>）：
    新人私聊点 START 即锁定归因（参数由 bot 自己生成，不依赖任何群事件链接字段——
    Telegram 公开群官方不保证把 invite_link 传给 bot，实测直链/申请制都丢）。
    锁定后发「加入群组」按钮（一次性申请制链接，bot 收到申请自动秒批）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    _inv_dbg = hub._inv_dbg
    chat_name_cache = hub.chat_name_cache
    get_name = hub.get_name
    invite_confirmed = hub.invite_confirmed
    invite_records = hub.invite_records
    save_data = hub.save_data
    send_reply = hub.send_reply
    sget = hub.sget
    try:
        uid = update.effective_user.id
        body = payload[len("inv_"):]
        inviter_s, _, cid_s = body.rpartition("_")
        inviter, cid = int(inviter_s), int(cid_s)
    except (ValueError, AttributeError):
        await send_reply(update, context, "⚠️ 邀请链接无效，请让邀请人重新发送「邀请」获取。")
        return
    if not sget("INVITE_ENABLED") or cid not in AUTHORIZED_GROUPS:
        await send_reply(update, context, "⚠️ 该邀请链接对应的群暂未开放邀请。")
        return
    if uid == inviter:
        await send_reply(update, context, "🙂 不能邀请自己哦。")
        return
    if f"{cid}:{uid}" in invite_records:
        await send_reply(update, context, "ℹ️ 你已在邀请记录中（重复进群不重复计）。")
        return
    invite_confirmed[f"{cid}:{uid}"] = inviter   # 归因锁定（落盘持久化）
    save_data()
    _inv_dbg(cid, f"[deep-link] uid={uid} 确认邀请人 {inviter}（START 参数）")
    inviter_name = await get_name(context.application, inviter, cid=cid)
    cname = chat_name_cache.get(cid) or str(cid)
    join_link, last_err = None, None
    for kw in ({"name": f"inv{uid}", "creates_join_request": True, "member_limit": 1},
               {"creates_join_request": True, "member_limit": 1},
               {"creates_join_request": True}):
        try:
            link_obj = await context.bot.create_chat_invite_link(chat_id=cid, **kw)
            join_link = link_obj.invite_link
            break
        except Exception as e:
            last_err = e
    if not join_link:
        invite_confirmed.pop(f"{cid}:{uid}", None)   # 链接都发不出，归因不落
        save_data()
        _inv_dbg(cid, f"[deep-link] 创建进群链接失败 inviter={inviter}：{last_err!r}")
        await send_reply(update, context,
                         f"❌ 生成进群链接失败：{last_err!r}\n请让管理员确认机器人是群管理员并勾选「邀请用户」权限。")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚪 加入群组（点击自动通过）", url=join_link)]])
    await send_reply(update, context,
                     f"🎟️ 你由 <b>{inviter_name}</b> 邀请加入「{cname}」\n\n"
                     f"👇 点下方按钮进群，机器人会自动为你通过。\n"
                     f"进群后邀请即计入 <b>{inviter_name}</b> 名下。",
                     kb=kb, parse_mode="HTML")


async def cmd_my_invite(update, context):
    """我的邀请合格结算进度：已计入/合格/已发放 + 刷新。群聊=本群；私聊=全部群合计。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_push_card_to_private = hub._invite_push_card_to_private
    _invite_send_progress_card = hub._invite_send_progress_card
    chat_name_cache = hub.chat_name_cache
    invite_links = hub.invite_links
    is_group_chat = hub.is_group_chat
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    if not sget("INVITE_ENABLED"):
        await send_reply(update, context, "❌ 邀请系统未开启。"); return
    if is_group_chat(update):
        cid = update.effective_chat.id
        cname = chat_name_cache.get(cid) or (getattr(update.effective_chat, "title", "") or str(cid))
    else:
        cid, cname = None, "全部群"
    uid = update.effective_user.id
    mine = None
    if cid is not None:
        mine = (invite_links.get(cid, {}).get(uid) or {}).get("link")
        # 群里只回执一句话，完整面板推私聊（不占群消息；私聊可反复刷新）
        if await _invite_push_card_to_private(context, uid, cid, cname):
            await send_reply(update, context, "🎟️ 邀请面板已发到你的私聊，进度可随时在私聊刷新。")
            return
        await send_reply(update, context, "⚠️ 私聊推送失败（可能你还没私聊过我发 /start），先在这里看：")
    await _invite_send_progress_card(update, context, uid, cid, cname, link=mine)


async def cmd_invite_debug(update, context):
    """邀请系统体检（管理员）：bot 自查权限/事件源/数据，一条命令定位「为什么进人不加分」。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    invite_debug = hub.invite_debug
    invite_links = hub.invite_links
    invite_pending = hub.invite_pending
    invite_records = hub.invite_records
    is_bot_admin = hub.is_bot_admin
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 邀请调试仅管理员可用。"); return
    cid = update.effective_chat.id
    L = ["🔧 邀请系统体检（本群）", "━━━━━━━━━━━━━━━━━"]
    L.append(f"开关：{'开' if sget('INVITE_ENABLED') else '关'}｜归因=deep-link确认制（点专属链接→START→自动通过）；无确认申请：{'自动批(带链接+开关开)' if sget('INVITE_AUTO_APPROVE') else '管理员手动批(网页成员页)'}")
    L.append(f"本群已授权：{'是' if cid in AUTHORIZED_GROUPS else '否'}")

    # ① bot 在群里的权限 —— 邀请系统能工作的硬前提
    st = "unknown"
    try:
        me = await context.bot.get_me()
        mem = await context.bot.get_chat_member(cid, me.id)
        st = mem.status
        if st == "administrator":
            r = mem  # ChatMemberAdministrator
            L.append(f"bot 身份：✅ 管理员（can_invite_users={bool(getattr(r, 'can_invite_users', True))}，can_restrict_members={bool(getattr(r, 'can_restrict_members', False))}）")
        elif st == "member":
            L.append("bot 身份：❌ 普通成员 —— 收不到申请/进出事件，邀请系统不可能工作")
            L.append("👉 在群设置把 bot 设为管理员（需勾选「邀请用户」权限）")
        elif st == "restricted":
            L.append("bot 身份：⚠️ 受限成员（被限制），多半收不到事件")
            L.append("👉 在群设置把 bot 设为管理员")
        else:
            L.append(f"bot 身份：❌ {st}（不在群里或被踢）—— 先拉回群并设管理员")
    except Exception as exc:
        L.append(f"查询 bot 权限失败：{type(exc).__name__}（网络？）")

    # ② 归因数据现状
    links = invite_links.get(cid, {})
    L.append(f"本群专属链接：{len(links)} 条" if links else "本群专属链接：无（还没人发过「邀请」）")
    recs = {k: v for k, v in invite_records.items() if k.startswith(f"{cid}:") and not v.get("left")}
    L.append(f"本群有效邀请记录：{len(recs)} 条")
    pend = {k: v for k, v in invite_pending.items() if k.startswith(f"{cid}:")}
    L.append(f"待归因申请（等进群事件）：{len(pend)} 条")
    dbg = invite_debug.get(cid) or []
    if dbg:
        L.append("最近事件：")
        L += [f"　{d}" for d in dbg[-12:]]
    else:
        L.append("最近事件：无 —— 若 bot 是管理员且刚有人点链接进群仍无事件，把 /邀请调试 结果发管理员排查")

    # ③ 给结论
    L.append("━━━━━━━━━━━━━━━━━")
    if st == "administrator":
        L.append("✅ bot 是管理员，事件源就绪。请做一次真实测试：")
        L.append("发「邀请」→ 复制链接 → 换一个号点链接 → 应直接进群并入群加分。")
        L.append("若弹的是「申请加入」：群开着申请制，直链被转申请会丢链接、无法精确归因，去群设置关掉「需批准/申请加入」。")
        L.append("进群后仍无事件：说明进的人不是通过 bot 专属链接（直接拉人/群链接不算邀请）。")
    else:
        L.append("❌ 结论：先到群设置把 bot 设为管理员再测，否则改代码没用。")
    await send_reply(update, context, "\n".join(L))


async def cmd_invite_report(update, context):
    """手动报备入群（管理员兜底）：当 chat_join_request / chat_member / service message 三路事件都丢时用。
    用法：/报备入群 新人名字/uid → 拿 invite_pending 里本群最近一条申请链接，强制走 _invite_track_join 归因+发奖。
    不传名字：列出本群待归因申请清单（每条 [归因] 按钮）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _inv_dbg = hub._inv_dbg
    _invite_track_join = hub._invite_track_join
    invite_debug = hub.invite_debug
    invite_pending = hub.invite_pending
    invite_records = hub.invite_records
    is_bot_admin = hub.is_bot_admin
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    if not await need_auth(update, context): return
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅管理员可用。"); return
    cid = update.effective_chat.id
    pend = [(k, v) for k, v in invite_pending.items() if k.startswith(f"{cid}:")]

    if not context.args:
        if not pend:
            await send_reply(update, context, "📭 本群没有待归因申请。\n（如果刚刚有人进群且调试无事件，先让 ta 再点一次链接走申请，再发「/报备入群 名字」）")
            return
        rows = []
        L = ["<b>📋 本群待归因申请（点按钮强制归因）</b>"]
        for i, (k, link) in enumerate(pend[:20], 1):
            uid_str = k.split(":", 1)[1]
            L.append(f"{i}. <code>{uid_str}</code>　链接 …{str(link)[-12:]}")
            rows.append([InlineKeyboardButton(f"✅ 归因 #{i}", callback_data=f"invreport_{i}")])
        L.append("\n用法：/报备入群 名字/uid 直接归给最近一条")
        await send_reply(update, context, "\n".join(L), kb=InlineKeyboardMarkup(rows))
        return

    target = context.args[0].strip()
    if not pend:
        await send_reply(update, context, f"📭 本群无待归因申请，无法给「{html.escape(str(target))}」归因。\n让 ta 再点一次链接走申请流程，再发本命令。")
        return
    k, link = pend[-1]
    uid_ = int(k.split(":", 1)[1])
    _inv_dbg(cid, f"[手动报备] {target} → 用 invite_pending 的 {k}（链接 …{link[-12:]}）归因")
    cmu = type("CMU", (), {})()
    setattr(cmu, "invite_link", type("L", (), {"link": link})())
    name = target.lstrip("@")
    await _invite_track_join(cmu, cid, uid_, name, context)
    key = f"{cid}:{uid_}"
    rec = invite_records.get(key, {})
    if rec.get("inviter"):
        await send_reply(update, context, f"✅ 手动归因成功：<b>{html.escape(str(target))}</b> 算作 <code>{rec['inviter']}</code> 邀请，奖励 {rec.get('award', 0)} 分")
    else:
        await send_reply(update, context, f"⚠️ 归因未建记录，看调试：{invite_debug.get(cid, [])[-3:]}")


def _rec_qualified(rec):
    if not rec: return False
    if rec.get("qualified") is not None: return bool(rec.get("qualified"))
    return rec.get("audit") == "ok"


def _rec_rejected(rec):
    if not rec: return False
    if rec.get("rejected") is not None: return bool(rec.get("rejected"))
    return rec.get("audit") == "rejected"


def _rec_awarded(rec):
    return int((rec or {}).get("award", 0) or 0) > 0


def _rec_ok(rec):
    """合格记录（含已退群）：达标且未被拒、未被广告连坐。用于「合格人数/每日合格」统计。

    与 _rec_valid 的区别：本函数**不排除已退群**——用户面板的「合格」表示「历史达标过的人数」，
    退群不抹掉这个事实（测试 test_race_schedule_invdel 已固化该语义）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _rec_qualified = hub._rec_qualified
    _rec_rejected = hub._rec_rejected
    if not rec:
        return False
    return bool(_rec_qualified(rec) and not _rec_rejected(rec) and not rec.get("ad_flag"))


def _rec_valid(rec):
    """有效邀请记录（唯一判定入口）：合格 + 未退群 + 未被拒 + 未因发广告被连坐。

    2026-09-09 收口：此前多处各自手写 `_rec_qualified(r) and not _rec_rejected(r) and not r.get("left")`，
    加「广告连坐」时只改了 _invite_count → 排行和群总览仍把广告号算作有效邀请。
    用于「有效邀请数」口径（邀请数、排行、群总览今日有效邀请）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _rec_ok = hub._rec_ok
    if not rec:
        return False
    return bool(_rec_ok(rec) and not rec.get("left"))


def _invite_count(inviter, cid=None):
    """邀请人有效邀请数（合格且未退群、未发广告连坐）；cid 限定群，None=全部群。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _rec_valid = hub._rec_valid
    invite_records = hub.invite_records
    n = 0
    for rec in invite_records.values():
        if rec.get("inviter") != inviter:
            continue
        if cid is not None and rec.get("cid") != cid:
            continue
        if _rec_valid(rec):
            n += 1
    return n


def _inviter_awarded_count(cid, inviter):
    """该邀请人在本群已发放奖励的次数（超额即不再发，防白嫖）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _rec_awarded = hub._rec_awarded
    invite_records = hub.invite_records
    return sum(1 for r in invite_records.values()
               if r.get("inviter") == inviter and r.get("cid") == cid and _rec_awarded(r))


def _inviter_frozen(cid, inviter):
    """该邀请人是否已被「广告连坐」冻结（名下**任一**被邀请人发过广告）。

    I4C（2026-09-14 用户拍板）：从「只让这一条记录不计合格」升级为
    「冻结该邀请人**后续全部**奖励」—— 拉广告号刷奖励的收益被彻底掐断。

    ★ 冻结是**派生**的（从 `invite_records` 现算），不新增全局、不动数据层 3 处接线：
      重启 / 存档恢复后自动仍然成立，也不会因为漏加接线而静默失效。
    ★ 只冻结**后续**：已发出去的奖励不追回（钱已到账，追回会引起纠纷）——
      与 `_invite_flag_ad` 的既有原则一致。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    invite_records = hub.invite_records
    for r in invite_records.values():
        if r.get("inviter") == inviter and r.get("cid") == cid and r.get("ad_flag"):
            return True
    return False


async def _invite_qualify_ready(cid, uid):
    """被邀请人在本群是否达到质量要求（发言阈值 / 净赚积分阈值）。
    注意：game_chips 是 defaultdict(初始分)——必须用「净赚=余额-初始分」判定积分，
    否则人人进群即自动 5W 初始分 → 积分门槛永远满足，防白嫖失效。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    game_chips = hub.game_chips
    member_profiles = hub.member_profiles
    sget = hub.sget
    if sget("INVITE_QUALIFY_MSGS") > 0:
        msgs = int((member_profiles.get(cid, {}).get(uid, {}) or {}).get("msgs", 0) or 0)
        if msgs < sget("INVITE_QUALIFY_MSGS"):
            return False
    if sget("INVITE_QUALIFY_POINTS") > 0:
        bal = int(game_chips.get(cid, {}).get(uid, sget("GAME_STARTING_CHIPS")) or 0)
        # ★ 原来用的是 abs()：|净变化| ≥ 阈值 就算达标，于是「亏 5 万」和
        #   「赚 5 万」被当成同一件事 —— 被邀请人故意大亏也能让邀请人拿奖，
        #   防白嫖门槛形同虚设。净赚必须**不带绝对值**（负数一律不达标）。
        if bal - sget("GAME_STARTING_CHIPS") < sget("INVITE_QUALIFY_POINTS"):
            return False
    return True


def _invite_daily_get(cid, inviter):
    """取邀请人当日拉新统计 {"times":已发奖次数,"points":已发奖积分}。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    invite_daily = hub.invite_daily
    now_bj = hub.now_bj
    return invite_daily[now_bj().strftime("%Y-%m-%d")][cid][inviter]


def _invite_daily_capped(cid, inviter):
    """是否已达当日拉新上限（人数 / 积分任一超限即停发）。0=不限。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_daily_get = hub._invite_daily_get
    sget = hub.sget
    d = _invite_daily_get(cid, inviter)
    if sget("INVITE_DAILY_CAP_TIMES") > 0 and int(d.get("times", 0)) >= int(sget("INVITE_DAILY_CAP_TIMES")):
        return True
    if sget("INVITE_DAILY_CAP_POINTS") > 0 and int(d.get("points", 0)) >= int(sget("INVITE_DAILY_CAP_POINTS")):
        return True
    return False


def _invite_daily_add(cid, inviter, pts):
    """记一次当日发放（人数+1，积分累加）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_daily_get = hub._invite_daily_get
    d = _invite_daily_get(cid, inviter)
    d["times"] = int(d.get("times", 0)) + 1
    d["points"] = int(d.get("points", 0)) + int(pts)


async def _invite_try_award(app, rec):
    """合格后发奖（不超每人上限 INVITE_REWARD_TIMES / 当日上限；award>0 防重入）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_award = hub._invite_award
    _invite_daily_capped = hub._invite_daily_capped
    _inviter_awarded_count = hub._inviter_awarded_count
    _inviter_frozen = hub._inviter_frozen
    _rec_awarded = hub._rec_awarded
    sget = hub.sget
    if _rec_awarded(rec):
        return
    cid, inviter = rec["cid"], rec["inviter"]
    if _inviter_awarded_count(cid, inviter) >= max(1, int(sget("INVITE_REWARD_TIMES"))):
        return  # 超上限：仍合格（计入合格数），但不再发奖
    if _invite_daily_capped(cid, inviter):
        return  # 当日拉新上限：仍合格，但当日不再发奖（次日恢复）
    if _inviter_frozen(cid, inviter):
        return  # ★ I4C 广告连坐冻结：名下有人发过广告 ⇒ 该邀请人后续奖励全部停发
    await _invite_award(app, rec)


async def _invite_ping_qualify(app, cid, uid):
    """被邀请人在本群有动静（发言/签到/刷新按钮）后调用：达标则标合格并发奖。幂等、异常吞并。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_qualify_ready = hub._invite_qualify_ready
    _invite_try_award = hub._invite_try_award
    _rec_qualified = hub._rec_qualified
    _rec_rejected = hub._rec_rejected
    invite_records = hub.invite_records
    logger = hub.logger
    save_data = hub.save_data
    sget = hub.sget
    try:
        rec = invite_records.get(f"{cid}:{uid}")
        if not rec or rec.get("left") or _rec_rejected(rec) or _rec_qualified(rec):
            return
        if not sget("INVITE_QUALIFY_ENABLED"):
            return  # 开关关 = 进群已即时结算，不重复判
        if not await _invite_qualify_ready(cid, uid):
            return
        rec["qualified"] = True
        await _invite_try_award(app, rec)
        save_data()
    except Exception:
        logger.exception("邀请达标判定异常（已吞并）")


async def _invite_refresh_all(app, cid, inviter):
    """刷新进度：重判某邀请人在本群全部待达标记录（事件驱动兜底）。返回新发奖数。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_ping_qualify = hub._invite_ping_qualify
    _rec_awarded = hub._rec_awarded
    _rec_qualified = hub._rec_qualified
    _rec_rejected = hub._rec_rejected
    invite_records = hub.invite_records
    n = 0
    for key, rec in list(invite_records.items()):
        if rec.get("inviter") != inviter or rec.get("cid") != cid:
            continue
        if rec.get("left") or _rec_rejected(rec) or _rec_qualified(rec):
            continue
        before = _rec_awarded(rec)
        await _invite_ping_qualify(app, cid, rec.get("invitee"))
        after = _rec_awarded(invite_records.get(key))
        if after and not before:
            n += 1
    return n


async def _invite_award(app, rec):
    """给邀请人发合格奖励并通知（群内 + 私聊）。rec 需含 cid/inviter/invitee_name；qualified 由调用方先置位。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    INVITE_LINK_CMD = hub.INVITE_LINK_CMD
    _check_level_change = hub._check_level_change
    _earn_add = hub._earn_add
    _earn_get = hub._earn_get
    _fmt_tpl = hub._fmt_tpl
    _invite_daily_add = hub._invite_daily_add
    game_chips = hub.game_chips
    get_name = hub.get_name
    ledger_add = hub.ledger_add
    save_data = hub.save_data
    schedule_delete = hub.schedule_delete
    sget = hub.sget
    wallet_locks = hub.wallet_locks
    inviter, cid = rec["inviter"], rec["cid"]
    reward = max(0, int(sget("INVITE_REWARD")))
    if reward:
        # 必须持锁：两名被邀请人同时达标时，两次"读 old + 写 old+reward"会互相覆盖，只到账一份
        async with wallet_locks[inviter]:
            old = game_chips[cid].get(inviter, 0)
            old_earned = _earn_get(cid, inviter)
            game_chips[cid][inviter] = old + reward
            _earn_add(cid, inviter, reward)   # 邀请奖励是系统新产出，计入累计积分
        rec["award"] = reward
        _invite_daily_add(cid, inviter, reward)   # 当日拉新统计（每日上限用）
        ledger_add(cid, 0, inviter, reward, "邀请奖励")
        await _check_level_change(app, cid, inviter, old_earned, _earn_get(cid, inviter))
    inviter_name = await get_name(app, inviter, cid=cid)
    if sget("INVITE_NOTIFY"):
        try:
            await app.bot.send_message(chat_id=inviter,
                text=f"🎟️ 你邀请的 {rec.get('invitee_name', '')} 已达标合格，奖励 {reward} 积分已到账。发「{INVITE_LINK_CMD}」看进度。")
        except Exception:
            pass
    if str(sget("INVITE_OK_GROUP")).strip():
        try:
            ok_msg = await app.bot.send_message(chat_id=cid, text=_fmt_tpl(
                "invite_ok_group", inviter=inviter_name, inviter_id=inviter,
                invitee=rec.get("invitee_name", ""), invitee_id=rec.get("invitee", ""), reward=reward))
            if ok_msg and sget("REPLY_DELETE_SECONDS") > 0:
                schedule_delete(app, cid, ok_msg, sget("REPLY_DELETE_SECONDS"))
        except Exception:
            pass
    save_data()


def _invite_flag_ad(cid, uid, reason=""):
    """风控连坐（2026-09-09 用户规则）：被邀请人发广告 → 标记其邀请记录，不再计入邀请人的合格数。

    不追回已发奖励（钱已到账，追回会引起纠纷），只做「后续不计合格」+ 记录违规原因，
    让邀请人无法继续靠拉广告号刷奖励。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    invite_records = hub.invite_records
    logger = hub.logger
    now_bj = hub.now_bj
    save_data = hub.save_data
    rec = invite_records.get(f"{cid}:{uid}")
    if not rec:
        return False
    rec["ad_flag"] = True
    rec["ad_reason"] = str(reason or "")
    rec["ad_ts"] = now_bj().strftime("%Y-%m-%d %H:%M")
    save_data()
    logger.warning("邀请连坐：被邀请人发广告 cid=%s uid=%s inviter=%s 原因=%s",
                   cid, uid, rec.get("inviter"), reason)
    return True


def _invite_find_invitee(uid):
    """该用户是否**已有邀请记录**（跨群全局查）。返回记录键，没有则 None。

    I5B（2026-09-14 用户拍板）：邀请归因**全局**去重 —— 同一个人一辈子只算一次邀请，
    不管他进的是哪个群。没有这个函数时，同一个人进群 A 之后再进群 B 会产生
    `B:uid` 第二条记录，于是**第二个邀请人也能拿一次奖**（跨群刷奖励的洞）。

    ⚠️ 代价（已向用户说明）：一个人被两个不同的人分别拉进两个群时，
       只有**第一个**邀请人拿得到奖，第二个拿不到（他确实拉了人，但规则上不算）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    invite_records = hub.invite_records
    _u = str(uid)
    for k, r in invite_records.items():
        if isinstance(r, dict) and str(r.get("invitee")) == _u:
            return k
    return None


# ─────────────────────────────────────────────────────────────────────────────
# I1A（2026-09-14）邀请归因「一表 + 一纯函数」：让「这个新人到底走哪条路、归给谁」
# 一眼看得懂、可逐行测试。REVIEW 原话：这是最容易出「进人不加分」的功能。
# ─────────────────────────────────────────────────────────────────────────────
#
# 5 条归因路径（入口 → 机制 → 最终归因来源）：
# ┌──── 路径 ────┬──────────────── 入口 ────────────────┬──────── 机制 ────────┬── 来源 ──┐
# │ 1 入群申请    │ antispam/events.py::on_join_request   │ 申请携带的链接入 pending │  link    │
# │ 2 专属链接    │ invite/__init__.py::_deep_invite_start │ 私聊 START 写 confirmed │  confirm │
# │ 3 服务消息    │ antispam/events.py::on_new_members_msg │ 普通群 service message  │  link    │
# │ 4 chat_member │ antispam/events.py::on_member_event    │ 事件的 link / from_user │ link/manual │
# │ 5 手动报备    │ invite/__init__.py::cmd_invite_report  │ 取 pending 链接强制归因  │  link    │
# └──────────────┴───────────────────────────────────────┴──────────────────────┴──────────┘
# 这 5 条路**全部**汇进 _invite_track_join → _invite_attribute（唯一的归因判定）。
INVITE_ATTRIBUTION_TABLE = (
    {"path": "1 入群申请", "entry": "features/antispam/events.py:on_join_request",
     "mechanism": "审批制进群：申请携带的链接记入 invite_pending[cid:uid]，批后进群事件缺链接时兜底",
     "source": "link"},
    {"path": "2 专属链接", "entry": "features/invite/__init__.py:_deep_invite_start",
     "mechanism": "私聊点 START（?start=inv_<邀请人>_<群id>）→ 写 invite_confirmed，最可靠",
     "source": "confirm"},
    {"path": "3 服务消息", "entry": "features/antispam/events.py:on_new_members_msg",
     "mechanism": "普通群收不到 chat_member，靠 service message 兜底（与路径 4 双事件源，_inv_notice_fresh 去重）",
     "source": "link"},
    {"path": "4 chat_member", "entry": "features/antispam/events.py:on_member_event",
     "mechanism": "chat_member 事件带的 invite_link；手动拉人时 from_user = 添加人",
     "source": "link/manual"},
    {"path": "5 手动报备", "entry": "features/invite/__init__.py:cmd_invite_report",
     "mechanism": "管理员 /报备入群，取 invite_pending 最近一条链接强制走归因（三路事件全丢时兜底）",
     "source": "link"},
)


def _invite_attribute(uid, link, confirmed, links, adder_id=0, adder_is_bot=False,
                      manual_enabled=False):
    """**纯函数**：把「进群事件 + 已存归因数据」翻译成归因结论。无副作用、可逐行测试。

    参数：
      uid            进群者
      link           本次进群事件/申请携带的邀请链接（可能为空串）
      confirmed      invite_confirmed 里锁定的邀请人 uid（deep-link START / 主动问确认），0 = 无
      links          {邀请人uid: {"link": ...}} 本群已登记的邀请链接表
      adder_id       手动添加者 uid（chat_member 的 from_user），0 = 未知
      adder_is_bot   手动添加者是否机器人
      manual_enabled 后台开关「手动拉人计入邀请」

    返回 (inviter, source, reason)：
      inviter 归因到的邀请人 uid；**0 = 不归因**（宁缺毋滥）
      source  "confirm" / "link" / "manual" / None
      reason  不归因时的原因："self" / "link_unmatched" / "no_link" / ""

    优先级（与重构前逐字一致，**不许改**）：
      ① 确认制 confirmed（最可靠，优先于一切链接字段）
      ② 链接精确匹配 links
      ③ 手动拉人（仅当开关开 + adder 有效 + 非机器人 + 非本人）
      ④ 有链接但不匹配 → link_unmatched（**不**回退手动）；无链接 → 走到 ③ 或 no_link
      自己邀自己 → (0, None, "self")
    """
    if confirmed:
        inv = int(confirmed)
        if inv == uid:
            return 0, None, "self"
        return inv, "confirm", ""
    if link:
        for i_uid, info in (links or {}).items():
            if info.get("link") == link and i_uid != uid:
                return int(i_uid), "link", ""
        return 0, None, "link_unmatched"
    if manual_enabled and adder_id > 0 and adder_id != uid and not adder_is_bot:
        return int(adder_id), "manual", ""
    return 0, None, "no_link"


async def _invite_track_join(cmu, cid, uid, name, context):
    """邀请追踪归因（优先级）：① invite_confirmed（deep-link/主动问确认，最可靠）
    → ② 事件/申请携带的链接精确匹配 → ③ 都没有则不归因（宁缺毋滥）。
    归因后记记录/发奖励/通知（所有异常吞并）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    _fmt_tpl = hub._fmt_tpl
    _inv_dbg = hub._inv_dbg
    _inv_notice_fresh = hub._inv_notice_fresh
    _invite_attribute = hub._invite_attribute
    _invite_find_invitee = hub._invite_find_invitee
    _invite_try_award = hub._invite_try_award
    _join_user_obj = hub._join_user_obj
    _user_has_avatar = hub._user_has_avatar
    invite_confirmed = hub.invite_confirmed
    invite_links = hub.invite_links
    invite_pending = hub.invite_pending
    invite_records = hub.invite_records
    logger = hub.logger
    now_bj = hub.now_bj
    save_data = hub.save_data
    sget = hub.sget
    try:
        if not sget("INVITE_ENABLED") or uid <= 0 or cid not in AUTHORIZED_GROUPS:
            _inv_dbg(cid, f"进群 uid={uid} 跳过：开关{sget('INVITE_ENABLED')}/授权{cid in AUTHORIZED_GROUPS}")
            return
        key = f"{cid}:{uid}"
        if key in invite_records:   # **同群**重复进群：不重复计，仅视为回归
            invite_records[key]["left"] = False
            _inv_dbg(cid, f"进群 uid={uid} 重复（已有记录，视为回归）")
            return
        # ★ I5B（2026-09-14 用户拍板）：**跨群**也去重 —— 同一个人一辈子只算一次邀请。
        #   没有这一段时，同一个人进群 A 后再进群 B 会新增 `B:uid` 记录，
        #   第二个邀请人也能拿奖（跨群刷奖励的洞）。
        _dup = _invite_find_invitee(uid)
        if _dup is not None:
            # ★ 提前 return 前必须清掉这两个中间状态键：
            #   它们之后再也不会被消费（下次进来还是先命中去重），会永久残留；
            #   残留的 invite_confirmed 还会让 on_join_request **自动批准**这个人的入群申请。
            hub.invite_pending.pop(key, None)
            hub.invite_confirmed.pop(key, None)
            _inv_dbg(cid, f"进群 uid={uid} 已有跨群邀请记录 {_dup}（I5B 全局去重，不计邀请）")
            return
        link = (getattr(getattr(cmu, "invite_link", None), "link", "")
                or invite_pending.pop(f"{cid}:{uid}", ""))   # 申请制兜底：审批后的 join 事件常不带链接
        _inv_dbg(cid, f"进群 uid={uid} 事件链接：{link or '（无）'}")
        confirmed = invite_confirmed.pop(key, 0)
        adder = getattr(cmu, "from_user", None)
        a_id = getattr(adder, "id", 0) if adder else 0
        # ★ I1A：归因判定收敛到一个**纯函数**（5 条路径共用），这里只负责副作用（提示/诊断/建记录）。
        inviter, source, reason = _invite_attribute(
            uid, link, confirmed, invite_links.get(cid, {}),
            adder_id=a_id, adder_is_bot=bool(getattr(adder, "is_bot", False)),
            manual_enabled=bool(sget("INVITE_MANUAL_COUNT")),
        )
        if source == "confirm":
            _inv_dbg(cid, f"✅ 归因成功 uid={uid} → 邀请人 {inviter}（确认制）")
        elif source == "manual":
            _inv_dbg(cid, f"✅ 手动拉人计入邀请：uid={uid} 由 {inviter} 添加（开关已开）")
        if not inviter:
            if reason == "self":
                _inv_dbg(cid, f"uid={uid} 自己邀自己，跳过")
                if str(sget("INVITE_SELF_MSG")).strip():
                    try:
                        await context.bot.send_message(chat_id=cid, text=_fmt_tpl("invite_self_msg", name=name))
                    except Exception:
                        pass
                return
            if reason == "link_unmatched":
                _inv_dbg(cid, f"⚠️ 归因失败：链接不在已存表（已存：{[i.get('link','')[-12:] for i in invite_links.get(cid, {}).values()]}）")
                if str(sget("INVITE_INVALID_MSG")).strip() and _inv_notice_fresh(key):
                    try:
                        await context.bot.send_message(chat_id=cid, text=_fmt_tpl("invite_invalid_msg", name=name))
                    except Exception:
                        pass
                return
            # reason == "no_link"：事件/申请都没带链接 → 不归因（宁缺毋滥，绝不猜测安错人）。
            # /link 发的是申请制链接：正常路径 申请(chat_join_request 必带链接入 pending)→批准→进群，
            # 进群事件即使漏链接也能从 pending 兜底归因；都查不到说明是手动拉人/直接搜索进群。
            _inv_dbg(cid, "⚠️ 事件与申请均无链接 → 不归因（宁缺毋滥）：申请制路径应有 pending，查不到多为手动拉人/搜索进群")
            # 黑盒终结：给管理员私聊发诊断通知（不打扰群），说明为何没计入
            # 双事件源去重：chat_member + 服务消息各调一次，不去重管理员会收到 2 条（2026-09-10 用户报障）
            try:
                if _inv_notice_fresh(key):
                    await context.bot.send_message(
                        ADMIN_USER_ID,
                        f"ℹ️ 进群未计入邀请：{name}（<code>{uid}</code>）加入群 <code>{cid}</code> 时"
                        f"未携带任何邀请链接（多为手动拉人/直接搜索进群）。\n"
                        f"邀请只认「邀请人的专属链接」进群；可让邀请人邀请，或后台开启「手动拉人计入邀请」。",
                        parse_mode="HTML")
            except Exception:
                pass
            return
        _inv_dbg(cid, f"✅ 归因成功 uid={uid} → 邀请人 {inviter}")
        rec = {"cid": cid, "inviter": inviter, "invitee": uid, "invitee_name": name,
               "ts": now_bj().strftime("%Y-%m-%d %H:%M"), "qualified": False,
               "rejected": False, "left": False, "award": 0, "link": link,
               "manual": not bool(link) and not confirmed,   # 手动拉人计入的记录带 manual 标记（确认制归因不算）
               "source": "confirm" if confirmed else "link"}
        # 进群硬门槛（头像/用户名，进群瞬间检查一次；不满足直接拒绝，永不发奖）
        ju = _join_user_obj(cmu)
        if sget("INVITE_QUALIFY_USERNAME") and not getattr(ju, "username", None):
            rec["rejected"] = True; rec["note"] = "无用户名"
            _inv_dbg(cid, f"进群 uid={uid} 无用户名 → 拒绝（永不发奖）")
        elif sget("INVITE_QUALIFY_AVATAR") and not await _user_has_avatar(context, uid):
            rec["rejected"] = True; rec["note"] = "无头像"
            _inv_dbg(cid, f"进群 uid={uid} 无头像 → 拒绝（永不发奖）")
        invite_records[key] = rec
        if not sget("INVITE_QUALIFY_ENABLED") and not rec["rejected"]:
            # 合格结算关 → 兼容老行为：进群即合格发奖
            rec["qualified"] = True
            await _invite_try_award(context.application, rec)
        else:
            _inv_dbg(cid, f"进群 uid={uid} 记待达标（达标后事件驱动发奖）")
        save_data()
    except Exception:
        logger.exception("邀请追踪异常（已吞并）")


def _invite_rank_rows(scope):
    """按 scope（today/month/all）算邀请排行，返回 [(rank, uid, count)]（前 10）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _rec_valid = hub._rec_valid
    invite_records = hub.invite_records
    now_bj = hub.now_bj
    today = now_bj().strftime("%Y-%m-%d")
    month = today[:7]
    counts = defaultdict(int)
    for rec in invite_records.values():
        if not _rec_valid(rec):
            continue
        ts = str(rec.get("ts", ""))
        if scope == "today" and not ts.startswith(today):
            continue
        if scope == "month" and not ts.startswith(month):
            continue
        counts[rec.get("inviter")] += 1
    counts.pop(0, None)
    ranked = sorted(counts.items(), key=lambda x: -x[1])[:10]
    return [(i + 1, uid, n) for i, (uid, n) in enumerate(ranked)]


async def _invite_send_rank(update, context, scope):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fmt_tpl = hub._fmt_tpl
    _invite_rank_rows = hub._invite_rank_rows
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    sget = hub.sget
    user_link = hub.user_link
    if not await need_auth(update, context): return
    if not sget("INVITE_ENABLED"):
        await send_reply(update, context, "❌ 邀请系统未开启（网页「群组设置 → 邀请系统」可开启）。"); return
    if sget("INVITE_RANK_ADMIN_ONLY") and not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 邀请排行仅管理员可查。"); return
    cid = update.effective_chat.id
    titles = {"today": "invite_rank_today_msg", "month": "invite_rank_month_msg", "all": "invite_rank_all_msg"}
    lines = [_fmt_tpl(titles[scope])]
    rows = _invite_rank_rows(scope)
    if not rows:
        lines.append("暂无数据")
    for i, uid, n in rows:
        # 名字包成蓝色可点链接（2026-09-12 用户要求：所有排名的名字都能点开看人）
        lines.append(_fmt_tpl("invite_rank_line_fmt", i=i,
                              name=user_link(uid, await get_name(context.application, uid, cid=cid)), count=n))
    await send_reply(update, context, "\n".join(lines))


async def cmd_invite_rank_today(update, context):
    """今日邀请排行。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_send_rank = hub._invite_send_rank
    await _invite_send_rank(update, context, "today")


async def cmd_invite_rank_month(update, context):
    """本月邀请排行。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_send_rank = hub._invite_send_rank
    await _invite_send_rank(update, context, "month")


async def cmd_invite_rank_all(update, context):
    """总邀请排行。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _invite_send_rank = hub._invite_send_rank
    await _invite_send_rank(update, context, "all")


async def cmd_invite_link(update, context):
    """获取本群专属邀请链接：/link。生成的是 deep-link（t.me/<bot>?start=inv_邀请人_群id）：
    新人点开 → bot 私聊点 START → 归因即时锁定（不依赖 Telegram 群事件带链接——公开群
    官方就不保证给 bot 传 invite_link，实测直链/申请制两条路都丢链接）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _inv_dbg = hub._inv_dbg
    _invite_push_card_to_private = hub._invite_push_card_to_private
    _invite_send_progress_card = hub._invite_send_progress_card
    chat_name_cache = hub.chat_name_cache
    invite_links = hub.invite_links
    is_group_chat = hub.is_group_chat
    need_auth = hub.need_auth
    now_bj = hub.now_bj
    save_data = hub.save_data
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 请在群聊中使用。"); return
    if not sget("INVITE_ENABLED"):
        await send_reply(update, context, "❌ 邀请系统未开启（网页「群组设置 → 邀请系统」可开启）。"); return
    cid = update.effective_chat.id
    uid = update.effective_user.id
    # 守卫：bot 用户名未就绪时会拼出 t.me/None 这种坏链接（深链功能的已知坑），宁可直接提示也别发坏链
    _bot_un = (getattr(context.bot, "username", "") or "").strip() or str(hub.namespace().get("_BOT_USERNAME") or "").strip()
    if not _bot_un:
        await send_reply(update, context, "❌ 机器人用户名尚未就绪，请稍等几秒后重试（或联系管理员）。")
        return
    deep = f"https://t.me/{_bot_un}?start=inv_{uid}_{cid}"
    mine = invite_links.get(cid, {}).get(uid)
    if not mine or mine.get("mode") != "deeplink" or mine.get("link") != deep:
        invite_links.setdefault(cid, {})[uid] = {"link": deep,
                                                 "invite_id": f"inv_{uid}",
                                                 "ts": now_bj().strftime("%Y-%m-%d %H:%M"),
                                                 "mode": "deeplink"}
        _inv_dbg(cid, f"生成 deep-link inviter={uid}：…{deep[-24:]}")
        save_data()
        mine = invite_links[cid][uid]
    link = mine.get("link", "")
    cname = chat_name_cache.get(cid) or (getattr(update.effective_chat, "title", "") or str(cid))
    # 邀请面板推私聊（群内不刷屏）；私聊发不出（用户没 /start）才回退群内卡片
    if await _invite_push_card_to_private(context, uid, cid, cname):
        await send_reply(update, context, "🎟️ 邀请面板已发到你的私聊，进度可随时在私聊刷新。")
        return
    await send_reply(update, context, "⚠️ 私聊推送失败（可能你还没私聊过我发 /start），先在这里看：")
    await _invite_send_progress_card(update, context, uid, cid, cname, link=link)


async def cmd_invite_test(update, context):
    """邀请归因预演（管理员）：只读模拟，不改任何数据、不发奖励。
    演示归因判定：①事件带链接 → 精确匹配 ②事件漏链接 → 一律不归因（宁缺毋滥）。
    用于定位「进人不加分」：/link 直链进群事件必带链接；若事件常漏链接，多半是群开了「申请加入」。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    invite_links = hub.invite_links
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    if not await need_auth(update, context): return
    cid = update.effective_chat.id
    uid = update.effective_user.id
    if cid not in AUTHORIZED_GROUPS:
        await send_reply(update, context, "❌ 本群未授权。"); return
    mine = invite_links.get(cid, {}).get(uid)
    if not mine:
        await send_reply(update, context, "❌ 你还没有专属链接，先发「邀请」领。"); return
    link = mine.get("link", "")
    n_links = len([i for i, info in invite_links.get(cid, {}).items() if str(info.get("link", "")).startswith("http") and i != uid])
    fake_uid = 10 ** 12 + int(time.time() * 1000) % (10 ** 11)

    def _judge(evt_link):
        """与 _invite_track_join 同款判定：返回 (inviter, 说明)。虚拟受邀人是 fake_uid。"""
        if evt_link:
            for i_uid, info in hub.invite_links.get(cid, {}).items():
                if info.get("link") == evt_link and i_uid != fake_uid:
                    return i_uid, "命中：事件携带的链接与" + ("你的" if i_uid == uid else "别人的") + "专属链接一致"
            return 0, "⚠️ 链接不在已存表（进的人用的是别处链接？）"
        return 0, "事件漏链接 → 一律不归因（宁缺毋滥）：直链进群必带链接，漏链接说明走的是申请制或直接拉人"

    L = ["🧪 邀请归因预演（只读，不改数据）", "━━━━━━━━━━━━━━━━━"]
    L.append(f"你的专属链接：…{link[-12:]}")
    L.append(f"本群专属链接数（含你的）：{n_links + 1} 条")
    L.append("")
    L.append("<b>场景① 事件带链接</b>（/link 直链进群，正常都带）")
    a, b = _judge(link)
    L.append(f"　→ 归因：{'✅ ' + str(a) if a else '❌ 失败'}")
    L.append(f"　　{b}")
    L.append("")
    L.append("<b>场景② 事件漏链接</b>（直链不会发生；申请制/直接拉人会出现）")
    c_, d = _judge("")
    L.append(f"　→ 归因：{'✅ ' + str(c_) if c_ else '❌ 不归因'}")
    L.append(f"　　{d}")
    L.append("")
    L.append("判定失败 ≠ 系统坏了：先发 /邀请调试 看 bot 是不是管理员、群有没有开「申请加入」。")
    await send_reply(update, context, "\n".join(L))


async def _invite_ask_inviter(req, cid, uid, name, context):
    """主动问兜底：无归因、无链接的入群申请 → bot 主动私聊申请者选邀请人。
    Bot API 5.5+：bot 为群管理员（can_invite_users）时，可主动联系发入群申请的用户
    （user_chat_id，24h 窗口），即使对方从未 /start。全程吞异常，失败不影响申请本身。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _inv_dbg = hub._inv_dbg
    _nm_r = hub._nm_r
    chat_name_cache = hub.chat_name_cache
    get_name = hub.get_name
    invite_links = hub.invite_links
    try:
        candidates = [(i_uid, info) for i_uid, info in invite_links.get(cid, {}).items()
                      if i_uid != uid and isinstance(info, dict) and info.get("link")]
        if not candidates:
            _inv_dbg(cid, f"[主动问] uid={uid} 本群无邀请人候选，跳过")
            return
        async def _nm(i):
            try: return await hub.get_name(context.application, i, cid=cid)
            except Exception: return f"用户{i}"
        rows, shown = [], 0
        for i_uid, _info in candidates[:8]:
            nm = _nm_r(await _nm(i_uid))
            rows.append([InlineKeyboardButton(f"🎟️ {nm}", callback_data=f"inva_{cid}_{i_uid}_{uid}")])
            shown += 1
        if shown:
            rows.append([InlineKeyboardButton("🚶 我是自己进的（不占邀请名额）",
                                              callback_data=f"inva_none_{cid}_{uid}")])
            await context.bot.send_message(
                chat_id=getattr(req, "user_chat_id", uid),
                text=f"👋 <b>{name}</b> 你好！你申请加入「{chat_name_cache.get(cid) or cid}」。\n\n"
                     f"你是被谁邀请进群的？点一下邀请人（计入 TA 的邀请奖励）：",
                reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")
            _inv_dbg(cid, f"[主动问] 已私聊 uid={uid} 选择邀请人（候选 {shown} 人）")
    except Exception as e:  # silent-ok: 主动问只是兜底，失败不影响入群申请本身（_inv_dbg 已记录）
        _inv_dbg(cid, f"[主动问] 私聊 uid={uid} 失败（吞并）：{e!r}")
