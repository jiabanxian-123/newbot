# -*- coding: utf-8 -*-
"""⑥ 红包

· `_rp_grab` 持**红包包级锁**：跨 `await` 的「检查→扣减」必须原子，否则并发抢会超发。
· 只有真实入账才写台账（`ledger_add`）；抢完 / 过期要把面板原地改写成提示并**排入回收**
  （`retire_panel`）—— 摘掉按钮的那次编辑 = 面板退役 = 必须排回收。
"""

from core import hub

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import asyncio, random, secrets


async def cmd_redpacket(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fmt_tpl = hub._fmt_tpl
    action_notice = hub.action_notice
    game_chips = hub.game_chips
    get_name = hub.get_name
    is_group_chat = hub.is_group_chat
    need_auth = hub.need_auth
    now_bj = hub.now_bj
    rp_packets = hub.rp_packets
    safe_send = hub.safe_send
    save_data = hub.save_data
    schedule_delete = hub.schedule_delete
    send_reply = hub.send_reply
    sget = hub.sget
    wallet_locks = hub.wallet_locks
    if not await need_auth(update, context): return
    if not sget("REDPACKET_ENABLED"):
        await send_reply(update, context, "ℹ️ 红包功能未开启。"); return
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 红包请在群聊中发。"); return
    cid = update.effective_chat.id

    async def _reply(text):  # 机器人提示语也按后台设置自动删除
        reply = await hub.send_reply(update, context, text)
        hub.schedule_delete(context.application, cid, reply, hub.sget("REPLY_DELETE_SECONDS"))

    args = context.args
    reply_to = update.message.reply_to_message
    target = 0  # 0=人人可抢；>0=专属红包仅 TA 可抢
    if reply_to is not None and reply_to.from_user and not reply_to.from_user.is_bot:
        target = reply_to.from_user.id
    elif len(args) >= 3 and args[2].lstrip("-").isdigit():
        target = int(args[2])
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        await _reply("用法：红包 总积分 份数（如：红包 1000 5）\n🎁 专属红包：回复某人消息发同样命令，或「红包 1000 5 用户ID」，仅 TA 能抢"); return
    total, count = int(args[0]), int(args[1])
    if not (1 <= total <= 1000000 and 2 <= count <= 100 and count <= total):
        await _reply("❌ 份数至少 2 份（防小号互刷），总积分 1~100 万且份数不超过总积分。"); return
    uid = update.effective_user.id
    if target == uid:
        await _reply("❌ 专属红包不能指定自己。"); return
    if target and not sget("RP_EXCLUSIVE_ENABLED"):
        await _reply("❌ 专属红包未开启（后台「积分系统 → 积分红包」可开启）。"); return
    async with wallet_locks[uid]:
        if game_chips[cid][uid] < total:
            await _reply(_fmt_tpl("rp_msg_poor", need=total, balance=game_chips[cid][uid])); return
        game_chips[cid][uid] -= total
        pid = secrets.token_urlsafe(8)
        rp_packets[pid] = {"cid": cid, "from": uid, "left_amt": total, "left_n": count,
                           "grabbed": {}, "ts": now_bj().timestamp(), "msg_id": None, "target": target,
                           "lock": asyncio.Lock()}  # 包级锁：检查+扣减必须原子，防并发双付
        save_data()
    await action_notice(cid, context.application, uid, f"发出了 {total} 积分 / {count} 份红包")
    who = f"\n🎯 仅 {await get_name(context.application, target, cid=cid)} 可抢" if target else ""
    msg = await safe_send(context.bot, cid,
        f"🧧 {await get_name(context.application, uid)} 的积分红包\n💰 {total} 积分 × {count} 份{who}\n点击下方按钮抢！",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧧 抢红包", callback_data=f"rp_grab_{pid}")]]))
    if msg:
        rp_packets[pid]["msg_id"] = msg.message_id


async def _rp_grab(p, pid, uid, context, q):
    """红包抢夺核心：检查过期/重复 → 随机拆分 → 入账 → 更新看板。

    全程持包级锁：剩余份数/金额的检查与扣减必须原子，否则两个不同用户
    并发抢同一包时，各自持有的是不同的 wallet_locks[uid]，互不排斥 → 超额派发。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fmt_tpl = hub._fmt_tpl
    game_chips = hub.game_chips
    get_name = hub.get_name
    ledger_add = hub.ledger_add
    now_bj = hub.now_bj
    rank_marker = hub.rank_marker
    retire_panel = hub.retire_panel
    rp_packets = hub.rp_packets
    safe_edit = hub.safe_edit
    save_data = hub.save_data
    sget = hub.sget
    user_link = hub.user_link
    wallet_locks = hub.wallet_locks
    cid = p["cid"]
    async with p["lock"]:
        now = now_bj().timestamp()
        if now - p["ts"] > 86400:  # 过期：剩余整体退回发包人
            refund = p["left_amt"]
            async with wallet_locks[p["from"]]:
                game_chips[cid][p["from"]] += refund
            # ★ 退款要补一笔台账：发包时按 total 记了「红包」流出，
            #   退款不记的话玩家的支出恒大于实际，账单对不上。
            try:
                hub.ledger_add(cid, 0, p["from"], refund, "红包退回")
            except Exception:
                hub.logger.exception("红包退款写台账失败（钱已退，仅是流水缺失）")
            rp_packets.pop(pid, None); save_data()
            await q.answer("红包已过期，剩余已退回", show_alert=True)
            # 同 21点 解散提示：edit 改写出来的提示不走 send ⇒ 必须显式排程回收
            await retire_panel(context.application, cid, p.get("msg_id"), "🧧 红包已过期，未领完的积分已退回。")
            return
        if uid in p["grabbed"]:
            await q.answer(_fmt_tpl("rp_msg_dup", amount=p["grabbed"][uid]), show_alert=True); return
        tgt = p.get("target") or 0  # 旧数据无 target 键按普通红包处理
        if tgt and uid != tgt:
            await q.answer(_fmt_tpl("rp_msg_target", name=await get_name(context.application, tgt, cid=cid)), show_alert=True); return
        if p["left_n"] <= 0:
            await q.answer(_fmt_tpl("rp_msg_none"), show_alert=True); return
        if p["left_n"] == 1:
            amt = p["left_amt"]
        elif sget("RP_LUCK_ENABLED"):  # 拼手气：随机拆分；关闭则平均分
            amt = random.randint(1, max(1, p["left_amt"] - p["left_n"] + 1))
        else:
            amt = max(1, p["left_amt"] // p["left_n"])
        async with wallet_locks[uid]:
            p["grabbed"][uid] = amt
            p["left_amt"] -= amt; p["left_n"] -= 1
            game_chips[cid][uid] += amt
            # 红包是「人对人转移」不产生新积分 → 不写入 total_earned（防小号对倒刷等级）
            ledger_add(cid, p["from"], uid, amt, "红包")  # 资金流台账：发包人→领取人
            save_data()
    await q.answer(_fmt_tpl("rp_msg_grab", amount=amt, balance=game_chips[cid][uid]))
    # 等级只看真实产出，转移类不动 → 无需检查
    total, count = sum(p["grabbed"].values()), len(p["grabbed"])
    if p["left_n"] <= 0:
        if sget("RP_LOG_ENABLED"):  # 手气排行：按金额降序，前三名带奖牌表情
            lines = []
            for i, (u, a) in enumerate(sorted(p["grabbed"].items(), key=lambda x: -x[1]), 1):
                lines.append(_fmt_tpl("rp_msg_log", rank=rank_marker(i),
                                      name=user_link(u, await get_name(context.application, u, cid=cid)), amount=a))
            detail = "\n".join(lines)
        else:
            detail = "\n".join(f"{await get_name(context.application, u, cid=cid)}：{a}"
                               for u, a in p["grabbed"].items())
        # 抢完 = 这张卡片退役（摘掉了「抢红包」按钮）⇒ 走 retire_panel 才会被回收。
        #   此前摘了按钮却没人排程，于是「红包已被抢完」卡片永久留在群里（同 21点 那类漏网）。
        await retire_panel(context.application, cid, p.get("msg_id"),
                           f"🧧 红包已被抢完（{count} 份 / {total} 积分）\n{detail}")
    else:
        await safe_edit(context.bot, cid, p["msg_id"],
                        f"🧧 红包进行中\n💰 已领 {count}/{p['left_n'] + count} 份｜剩 {p['left_amt']} 积分",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧧 抢红包", callback_data=f"rp_grab_{pid}")]]))
