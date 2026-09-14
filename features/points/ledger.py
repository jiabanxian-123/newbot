# -*- coding: utf-8 -*-
"""账本地基：累计积分 / 流水 / 我的积分 / 排行

积分账本的地基：累计积分记账 + 流水展示 + 我的积分/排行查询。

· `_earn_add` / `_earn_get` 是「累计积分」唯一读写口（等级判定基数）。
  ⚠️ 它与 `ledger_add`（流水台账）是**两个出口**：新增任何「系统发分」入口必须两个都调 ——
  只调 `_earn_add` 不报错、不崩、测试也不红，**只是玩家在流水里看不到**。
· `cmd_points_flow` 是玩家唯一能看到资金来源的地方。聊天分有**两个**账本
  （`chat_earn_daily` 现行 / `chat_today` 旧），同一天取**较大值**，不能相加（会双计）。
· `_point_adj_log_card` 是网页加减分页的「最近调整」卡片（确认操作真的生效）。
"""

from core import hub

import html


def _earn_add(cid, uid, amount):
    """记一笔「累计积分」。amount<=0 忽略；异常全吞（记账失败绝不影响主流程）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    total_earned = hub.total_earned
    try:
        amount = int(amount or 0)
        if amount > 0:
            total_earned[cid][uid] = int(total_earned[cid][uid] or 0) + amount
    except Exception:
        logger.exception("累计积分记账异常 cid=%s uid=%s（已吞并）", cid, uid)


def _earn_get(cid, uid):
    """取「累计积分」。老玩家账本为空时用「当前余额」兜底，避免升级后一夜掉级。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    game_chips = hub.game_chips
    sget = hub.sget
    total_earned = hub.total_earned
    try:
        got = int(total_earned.get(cid, {}).get(uid, 0) or 0)
    except Exception:
        got = 0
    if got > 0:
        return got
    try:
        return max(0, int(game_chips.get(cid, {}).get(uid, sget("GAME_STARTING_CHIPS")) or 0))
    except Exception:
        return 0


async def cmd_points_flow(update, context):
    """积分流水：一笔笔列出来源/去向（游戏送分 / 红包 / 转赠 / 兑换 / 抽水 / 抽奖 / 聊天积分 / 邀请奖励…）。

    2026-09-12 用户四连报障（本轮一次性修掉）：
      ① 「只显示游戏的、不显示聊天/抽奖等得失」→ 补进聊天积分（按人·天聚合）与抽奖参与/退款；
      ②③ 「流水文本格式更换（第 2 版）」→ 换成**用户在截图里指定的版式**：
         首行 `您当前的积分为N，最近积分流水如下：`，每行 `+10（文字信息） - 2026-09-12 09:36:44`
         （ASCII 正负号 + 全角括号包住类型 + ' - ' + 带秒的完整北京时间）；
      ④ 「流水又偷懒只显示 14 条」→ **默认全部显示**，仅在超过单条上限时丢掉最旧的几条。
      ⑤ 「德州获得积分文本太他妈的复杂了 而且聊天获得也没有」→ 对局行不再拼对家昵称
         （`+50（德州）`，见 game_flows 段注释）；聊天积分改为按 `chat_earn_daily` 聚合展示
         （2026-09-12 起**只有一个账本**，不再双账本取大，见变量声明处注释）；
         另外把「签到/购买到账/管理员加减分/应急赠送」四条只加钱、不进流水的
         入口全部补上台账（否则流水永远缺这几类，用户看着就像"漏账"）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _flow_ts_full = hub._flow_ts_full
    _peer_brief = hub._peer_brief
    chat_earn_daily = hub.chat_earn_daily
    game_chips = hub.game_chips
    game_flows = hub.game_flows
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    ledger = hub.ledger
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    user_names = hub.user_names
    if not await need_auth(update, context): return
    uid = update.effective_user.id
    target = uid
    if update.effective_message and update.effective_message.reply_to_message:
        if not is_bot_admin(uid):
            await send_reply(update, context, "❌ 查别人的流水仅限管理员（回复对方消息发「流水」）。"); return
        target = update.effective_message.reply_to_message.from_user.id
    entries = []
    for e in ledger:
        amt = int(e.get("amt", 0) or 0)
        if e.get("frm") == target and amt:
            entries.append((e.get("ts", ""), -amt, str(e.get("typ", "")), e.get("to")))
        elif e.get("to") == target and amt:
            entries.append((e.get("ts", ""), amt, str(e.get("typ", "")), e.get("frm")))
    # 对局净转移（德州/金花/大话骰）：**只记游戏名，不记对家**
    #   —— 2026-09-12 用户原话「德州获得积分文本太他妈的复杂了」（`（德州·投喂 无敌棒棒屌爆）`）。
    #   一局里钱是散户对全桌的净转移，挑一个「对家」本来就是不准确的，写出来只是噪音。
    for e in game_flows:
        amt = int(e.get("amt", 0) or 0)
        if e.get("frm") == target and amt:
            entries.append((e.get("ts", ""), -amt, str(e.get("typ", "")), 0))
        elif e.get("to") == target and amt:
            entries.append((e.get("ts", ""), amt, str(e.get("typ", "")), 0))
    # 聊天积分：按「人·天」聚合出一行（逐条进台账会把红包/转赠挤掉，见 chat_earn_daily 注释）。
    #   2026-09-12 起**只有一个账本**：旧的双账本已被证明是同一份数据的两份拷贝（见声明处注释），
    #   旧存档的残留由 load_data() 一次性搬进来 ⇒ 这里不必再「两个账本取较大值」。
    chat_by_date = {}
    for date, chats in chat_earn_daily.items():
        tot = sum(int(users.get(target, 0) or 0) for users in chats.values())
        if tot > chat_by_date.get(date, 0):
            chat_by_date[date] = tot
    for date, tot in chat_by_date.items():
        if tot:
            entries.append((f"{date} 23:59", tot, "聊天积分", 0))
    entries.sort(key=lambda x: x[0])
    if not entries:
        await send_reply(update, context,
            "📒 暂无积分流水。游戏送分 / 红包 / 转赠 / 兑换 / 抽水 / 抽奖 / 聊天积分 / 邀请奖励都会记在这里；"
            "21点 / 赛车等每局净盈亏用「盈亏」查。"); return
    cid = update.effective_chat.id
    # 首行「您当前的积分为N」——余额取本群钱包，无记录按 0（别裸下标读 defaultdict）
    bal = game_chips.get(cid, {}).get(target, 0)
    lines = []
    if target != uid:
        lines.append(f"👤 {html.escape(str(await get_name(context.application, target, cid=cid)))}")
    lines.append(f"您当前的积分为{bal}，最近积分流水如下：")
    body, peer_cache = [], {}
    for ts, amt, typ, peer in entries:
        # 对家信息不单独开一列（截图里没有），塞进括号内用 · 分隔：+100（转赠·甲）
        peer_txt = ""
        if peer:
            if peer not in peer_cache:
                nm = user_names.get(peer)
                if not nm:
                    try: nm = await get_name(context.application, peer, cid=cid)
                    except Exception: nm = str(peer)
                peer_cache[peer] = nm or str(peer)
            # 只有人对人转移才保留对方（「这 100 是谁给的」是这笔账的全部信息量）；
            # 长昵称截断（见 _peer_brief），别让一行的宽度被昵称拖垮。
            peer_txt = "·" + _peer_brief(peer_cache[peer])
        body.append(f"{'+' if amt >= 0 else '-'}{abs(amt)}（{typ}{peer_txt}） - {_flow_ts_full(ts)}")
    total = len(body)
    dropped = 0
    # 全部显示；只按单条消息上限兜底丢最旧的（Telegram 单条 4096 字符，留足余量）
    while body and sum(len(x) + 1 for x in body) + 120 > 3600:
        body.pop(0); dropped += 1
    lines.extend(body)
    if dropped:
        lines.append(f"（共 {total} 笔，太长了只显示最近 {len(body)} 笔）")
    await send_reply(update, context, "\n".join(lines), parse_mode="HTML")


def _point_adj_log_card():
    """网页加减分「最近调整」卡片：让管理员一眼看到刚才那次操作确实生效了。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    WEB_POINT_ADJ_LOG = hub.WEB_POINT_ADJ_LOG
    if not WEB_POINT_ADJ_LOG:
        return ""
    rows = "".join(
        f"<tr><td>{html.escape(str(r['ts']))}</td>"
        f"<td>{html.escape(str(r['gname']))} <code>{r['cid']}</code></td>"
        f"<td>{html.escape(str(r['name']))} <code>{r['uid']}</code></td>"
        f"<td style='color:{'#6fd08c' if r['amount'] > 0 else '#ff7b7b'};font-weight:700'>{r['amount']:+d}</td>"
        f"<td>{r['after']}</td></tr>"
        for r in reversed(WEB_POINT_ADJ_LOG))
    return ("<div class='card' style='margin-top:18px'><h3>🕘 最近调整</h3>"
            "<div class='sub'>本页最近 20 次操作（刷新页面不丢，重启机器人后清空）</div>"
            "<table class='tbl'><tr><th>时间</th><th>群</th><th>成员</th><th>变动</th><th>变动后</th></tr>"
            + rows + "</table></div>")


async def cmd_my_points(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fmt_tpl = hub._fmt_tpl
    _level_of = hub._level_of
    chat_earn_daily = hub.chat_earn_daily
    game_chips = hub.game_chips
    get_name = hub.get_name
    need_auth = hub.need_auth
    now_bj = hub.now_bj
    send_reply = hub.send_reply
    sget = hub.sget
    sign_data = hub.sign_data
    if not await need_auth(update, context): return
    cid, uid = update.effective_chat.id, update.effective_user.id
    balance = game_chips.get(cid, {}).get(uid, sget("GAME_STARTING_CHIPS"))
    date = now_bj().strftime("%Y-%m-%d")
    today_chat = chat_earn_daily.get(date, {}).get(cid, {}).get(uid, 0)
    streak = sign_data.get(cid, {}).get(uid, {}).get("streak", 0)
    signed = "✅ 已签" if sign_data.get(cid, {}).get(uid, {}).get("last") == date else "❌ 未签"
    lv, earned = _level_of(cid, uid)   # 等级按累计积分，与「我的等级」口径一致
    lv_line = f"🎖 等级：{lv}\n" if lv else ""
    msg = _fmt_tpl("query_msg_tpl", name=await get_name(context.application, uid),
                   balance=balance, level_line=lv_line, signed=signed, streak=streak, today_chat=today_chat)
    reply = await send_reply(update, context, msg)
    return reply


async def cmd_points_rank(update, context):
    """积分排行 —— 2026-09-11 用户「积分排名和积分排行感觉好乱」→ 与「积分榜」**合并为同一个榜**。

    现在 `积分榜`/`积分排行`/`排行`/`排行榜` 全部输出「💰 积分榜」，每行显示「积分 + 等级」，
    不再存在两个名字不同、内容几乎一样的榜。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    need_auth = hub.need_auth
    send_rank_page = hub.send_rank_page
    if not await need_auth(update, context): return
    await send_rank_page(update, context, "points")
