# -*- coding: utf-8 -*-
"""① 签到（+ 新人首次发言奖励）

① 签到：每日签到发分（含连签加成）+ 签到榜，以及新人首次发言的一次性欢迎奖励。

· 签到分是**真产出**：`_earn_add` + `_check_level_change` + `ledger_add` 三处都要走。
· `_grant_newbie_reward` **只对 `member_joined_at[cid]` 里有记录的人发**
  （否则老成员重启后会被补发），`newbie_rewarded["cid:uid"]` 幂等。
"""

from core import hub

from datetime import timedelta


async def cmd_sign(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _check_level_change = hub._check_level_change
    _earn_add = hub._earn_add
    _earn_get = hub._earn_get
    _fmt_tpl = hub._fmt_tpl
    _invite_ping_qualify = hub._invite_ping_qualify
    game_chips = hub.game_chips
    get_name = hub.get_name
    is_group_chat = hub.is_group_chat
    ledger_add = hub.ledger_add
    logger = hub.logger
    need_auth = hub.need_auth
    now_bj = hub.now_bj
    save_data = hub.save_data
    send_reply = hub.send_reply
    sget = hub.sget
    sign_data = hub.sign_data
    wallet_locks = hub.wallet_locks
    if not await need_auth(update, context): return
    if not sget("SIGN_ENABLED"):
        await send_reply(update, context, "ℹ️ 签到功能未开启。"); return
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 签到请在群聊中进行。"); return
    cid, uid = update.effective_chat.id, update.effective_user.id
    today = now_bj().strftime("%Y-%m-%d")
    yesterday = (now_bj() - timedelta(days=1)).strftime("%Y-%m-%d")
    info = sign_data[cid][uid]
    if info.get("last") == today:
        await send_reply(update, context, f"✅ 今天已经签过啦（连续 {info.get('streak', 0)} 天）。"); return
    streak = info.get("streak", 0) + 1 if info.get("last") == yesterday else 1
    reward = sget("SIGN_BASE_REWARD") + (sget("SIGN_STREAK_BONUS") if streak % 7 == 0 else 0)
    async with wallet_locks[uid]:
        old_earned = _earn_get(cid, uid)
        game_chips[cid][uid] += reward
        _earn_add(cid, uid, reward)
        # 2026-09-12：签到以前只加钱、不进流水 ⇒ 玩家在「流水」里看不到这笔，以为没发。
        # 凡「系统凭空发分」的入口都必须记台账（口径见 cmd_points_flow 文档）。
        ledger_add(cid, 0, uid, reward, "签到奖励")
        sign_data[cid][uid] = {"last": today, "streak": streak}
        save_data()
    bonus = "（含连续7天额外奖励）" if streak % 7 == 0 else ""
    msg = _fmt_tpl("sign_msg_tpl", name=await get_name(context.application, uid),
                   streak=streak, reward=reward, bonus=bonus, balance=game_chips[cid][uid])
    await send_reply(update, context, msg)
    await _check_level_change(context.application, cid, uid, old_earned, _earn_get(cid, uid))
    # 合格邀请结算（事件驱动）：被邀请人签到加分后也即时判定是否达标
    try:
        if sget("INVITE_ENABLED"):
            await _invite_ping_qualify(context.application, cid, uid)
    except Exception:
        logger.exception("邀请达标判定异常（已吞并）")


async def cmd_sign_rank(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    need_auth = hub.need_auth
    rank_line = hub.rank_line
    send_reply = hub.send_reply
    sign_data = hub.sign_data
    if not await need_auth(update, context): return
    cid = update.effective_chat.id
    users = [(uid, v.get("streak", 0)) for uid, v in sign_data.get(cid, {}).items() if v.get("streak", 0) > 0]
    if not users:
        await send_reply(update, context, "本群还没有签到记录，发「签到」抢头名！"); return
    lines = ["📅 连续签到排行", "━" * 14]
    for i, (uid, s) in enumerate(sorted(users, key=lambda x: (-x[1], x[0]))[:20], 1):
        lines.append(rank_line(i, uid, await get_name(context.application, uid, cid=cid), f"：连续 {s} 天"))
    await send_reply(update, context, "\n".join(lines))


def _grant_newbie_reward(cid, uid, name=""):
    """新人欢迎奖励（2026-09-09 用户规则）：新人首次发言时发放，帮助其有基础分。

    幂等：`newbie_rewarded["cid:uid"]` 标记，重复调用不重复发。
    只对「入群记录里能查到的新人」发（避免老成员补发）。
    返回 (发放积分, 升级前的累计积分)；未发放返回 (0, None)。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _earn_add = hub._earn_add
    _earn_get = hub._earn_get
    game_chips = hub.game_chips
    ledger_add = hub.ledger_add
    logger = hub.logger
    member_joined_at = hub.member_joined_at
    newbie_rewarded = hub.newbie_rewarded
    save_data = hub.save_data
    sget = hub.sget
    if not sget("NEWBIE_REWARD_ENABLED") or int(sget("NEWBIE_REWARD")) <= 0:
        return 0, None
    key = f"{cid}:{uid}"
    if newbie_rewarded.get(key):
        return 0, None
    if uid not in member_joined_at.get(cid, {}):
        return 0, None                # 非本群记录过的新成员（老成员/重启后清空）不发
    pts = int(sget("NEWBIE_REWARD"))
    old_earned = _earn_get(cid, uid)
    game_chips[cid][uid] += pts
    _earn_add(cid, uid, pts)
    ledger_add(cid, 0, uid, pts, "新人欢迎奖励")
    save_data()
    # ★ 幂等标记必须在**发放成功之后**再置位：
    #   原写法一上来就置 True，若中途（加分 / 写台账 / 存盘）抛异常，
    #   奖励就永久不再补发 —— 而且没有任何报错（静默丢失）。
    newbie_rewarded[key] = True
    logger.info("新人欢迎奖励已发放 cid=%s uid=%s +%s（累计 %s）", cid, uid, pts, _earn_get(cid, uid))
    return pts, old_earned
