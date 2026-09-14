# -*- coding: utf-8 -*-
"""⑦ 转赠与买分

· 转赠是**人对人转移**：只走 `ledger_add`，**不调 `_earn_add`**
  （计入累计积分会被小号对倒刷等级）。
· 买分到账 `_buy_settle` 是**真产出**（管理员确认到账），必须
  `_earn_add` + `_check_level_change` + `ledger_add` 三处都调。
· 凭证类单号**只发私聊 + 管理员，绝不进群通知**（群友看到别人单号 = 可冒领）。
"""

from core import hub

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import secrets


async def cmd_inherit(update, context):
    """积分转赠（继承）：把积分转给同群其他成员，可收手续费。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fmt_tpl = hub._fmt_tpl
    game_chips = hub.game_chips
    get_name = hub.get_name
    inherit_daily = hub.inherit_daily
    is_group_chat = hub.is_group_chat
    ledger_add = hub.ledger_add
    need_auth = hub.need_auth
    now_bj = hub.now_bj
    player_is_busy = hub.player_is_busy
    save_data = hub.save_data
    send_reply = hub.send_reply
    sget = hub.sget
    user_wallet_locks = hub.user_wallet_locks
    if not await need_auth(update, context): return
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 转赠请在群聊中使用。"); return
    if not sget("INHERIT_ENABLED"):
        await send_reply(update, context, "❌ 转赠功能未开启（网页「积分系统 → 积分继承」可开启）。"); return
    cid, uid = update.effective_chat.id, update.effective_user.id
    args = context.args or []
    reply = update.message.reply_to_message
    if reply and args and args[0].isdigit():
        target, amount = reply.from_user.id, int(args[0])
    elif len(args) >= 2 and args[0].isdigit() and args[1].isdigit():
        target, amount = int(args[0]), int(args[1])
    else:
        await send_reply(update, context, "用法：回复成员消息发「转赠 数量」，或「转赠 用户ID 数量」"); return
    if amount <= 0:
        await send_reply(update, context, "❌ 转赠数量必须为正数。"); return
    if target == uid:
        await send_reply(update, context, "❌ 不能转给自己。"); return
    if player_is_busy(cid, uid) or player_is_busy(cid, target):
        await send_reply(update, context, "⚠️ 转赠双方有正在进行的游戏，请先结束。"); return
    fee = amount * sget("INHERIT_FEE_PERCENT") // 100
    recv = amount - fee
    if sget("INHERIT_DAILY_LIMIT") > 0:  # 每日转赠总额上限（防小号互刷）
        today = now_bj().strftime("%Y-%m-%d")
        used = inherit_daily[today][cid].get(uid, 0)
        if used + amount > sget("INHERIT_DAILY_LIMIT"):
            await send_reply(update, context, f"❌ 超出每日转赠上限：今日已转出 {used}，上限 {sget('INHERIT_DAILY_LIMIT')}（网页「积分继承」可调）。"); return
    # 必须同时锁住收款方：只锁付款方的话，收款方此刻若有 /add、结算等持锁写操作，转入会被覆盖丢失
    async with user_wallet_locks([uid, target]):
        if game_chips[cid][uid] < amount:
            await send_reply(update, context, f"❌ 你的积分不足：需要 {amount}，当前 {game_chips[cid][uid]}。"); return
        game_chips[cid][uid] -= amount
        game_chips[cid][target] += recv
        if sget("INHERIT_DAILY_LIMIT") > 0:
            inherit_daily[now_bj().strftime("%Y-%m-%d")][cid][uid] += amount
        ledger_add(cid, uid, target, amount, "转赠")  # 资金流台账
        save_data()
    fee_txt = f"（手续费 {fee}）" if fee else ""
    await send_reply(update, context, _fmt_tpl("inherit_msg_ok",
        name=await get_name(context.application, uid), target=await get_name(context.application, target, cid=cid),
        amount=amount, fee=fee_txt, recv=recv, balance=game_chips[cid][uid]))


async def cmd_buy_points(update, context):
    """购买积分（人工确认制，无需支付通道）：玩家申请 → 私聊通知管理员 → 管理员一键确认到账。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    buy_orders = hub.buy_orders
    buy_packages = hub.buy_packages
    chat_name_cache = hub.chat_name_cache
    get_name = hub.get_name
    is_group_chat = hub.is_group_chat
    logger = hub.logger
    need_auth = hub.need_auth
    now_bj = hub.now_bj
    save_data = hub.save_data
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 请在群聊中申请购买积分。"); return
    if not sget("BUY_ENABLED"):
        await send_reply(update, context, "❌ 购买积分功能未开启（网页「积分系统 → 购买积分」可开启）。"); return
    args = context.args or []
    if not args:
        on = sorted([p for p in buy_packages if p.get("on")], key=lambda x: x.get("sort", 0))
        if on:
            lines = ["💳 积分套餐", "━" * 14]
            for i, p in enumerate(on, 1):
                lines.append(f"{i}. {p.get('name', '?')}　¥{p.get('cny', 0)} = {p.get('points', 0)} 积分")
            lines.append("")
            lines.append("💡 发「充值 套餐名」或「充值 数量」提交申请，管理员确认后到账。")
            await send_reply(update, context, "\n".join(lines)); return
        await send_reply(update, context, f"用法：充值 数量（{sget('BUY_MIN')} ~ {sget('BUY_MAX')}）\n提交申请后联系管理员转账，管理员确认后积分自动到账。"); return
    pkg_arg = args[0].strip()
    amount = int(pkg_arg) if pkg_arg.isdigit() else None
    if amount is None:
        p = next((x for x in buy_packages if x.get("on") and x.get("name") == pkg_arg), None)
        if p:
            amount = int(p.get("points", 0) or 0)
    if not amount:
        await send_reply(update, context, "❌ 没有这个套餐；按数量充值用法：充值 数量。"); return
    from_pkg = amount is not None and not pkg_arg.isdigit()
    if not from_pkg and not (sget("BUY_MIN") <= amount <= sget("BUY_MAX")):
        await send_reply(update, context, f"❌ 单次购买需在 {sget('BUY_MIN')} ~ {sget('BUY_MAX')} 之间。"); return
    cid, uid = update.effective_chat.id, update.effective_user.id
    oid = secrets.token_hex(4)
    buy_orders[oid] = {"cid": cid, "uid": uid, "amount": amount, "ts": now_bj().strftime("%Y-%m-%d %H:%M")}
    save_data()
    await send_reply(update, context, f"📝 购买申请已提交：{amount} 积分（单号 {oid}）\n请联系管理员完成转账，确认后积分自动到账。")
    try:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认到账", callback_data=f"buyok_{oid}"),
                                    InlineKeyboardButton("❌ 取消", callback_data=f"buyno_{oid}")]])
        await context.bot.send_message(ADMIN_USER_ID,
            f"💳 购买积分申请｜单号 {oid}\n用户：{await get_name(context.application, uid, cid=cid)}（{uid}）\n群：{chat_name_cache.get(cid) or cid}（{cid}）\n数量：{amount} 积分", reply_markup=kb)
    except Exception:
        logger.exception("购买积分申请通知管理员失败（已吞并）")


async def _buy_settle(context, oid, ok, q):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _check_level_change = hub._check_level_change
    _earn_add = hub._earn_add
    _earn_get = hub._earn_get
    buy_orders = hub.buy_orders
    game_chips = hub.game_chips
    ledger_add = hub.ledger_add
    logger = hub.logger
    save_data = hub.save_data
    o = buy_orders.pop(oid, None)
    if not o:
        await q.answer("该申请已处理过", show_alert=True); return
    if ok:
        _cid, _uid = o["cid"], o["uid"]
        old_earned = _earn_get(_cid, _uid)
        game_chips[_cid][_uid] += o["amount"]
        _earn_add(_cid, _uid, o["amount"])   # 购买到账是系统新产出，计入累计积分
        ledger_add(_cid, 0, _uid, o["amount"], "购买到账")   # 台账：玩家「流水」里要能看到这笔充值
        try:
            await context.bot.send_message(o["cid"], f"✅ 你的购买申请（{o['amount']} 积分）已确认到账，当前积分 {game_chips[o['cid']][o['uid']]}。")
        except Exception:
            pass
        try:
            await _check_level_change(context.application, _cid, _uid, old_earned, _earn_get(_cid, _uid))
        except Exception:
            logger.exception("购买到账等级通知失败（已吞并）")
    save_data()
    await q.answer("已确认到账" if ok else "已取消")
