# -*- coding: utf-8 -*-
"""**应急赠送**：玩家积分归零且未超当日上限时补一手应急积分。

⚠️ 它**不是定时任务**（由游戏入口按需调用），躺在这个包里纯属历史原因 ——
   真要归位应去 features/points（积分域）。跨包搬迁要用户拍板，此处只注明不动。

从 features/schedule/__init__.py 分家（2026-09-14，桌面清单第 4 项）：每个定时任务独立成一个小文件，加任务 = 加一个文件 + 状态表里加一行。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
"""

from core import hub

__all__ = ["emergency_if_needed"]


async def emergency_if_needed(cid, uid, app, wallet=None, poker=None):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _earn_add = hub._earn_add
    daily_emergency_used = hub.daily_emergency_used
    game_chips = hub.game_chips
    games_played = hub.games_played
    get_name = hub.get_name
    ledger_add = hub.ledger_add
    safe_send = hub.safe_send
    save_data = hub.save_data
    schedule_notice_delete = hub.schedule_notice_delete
    sget = hub.sget
    used = daily_emergency_used[cid][uid]
    wallet = wallet or game_chips
    if wallet[cid][uid] != 0 or used >= sget("EMERGENCY_MAX_USES"): return False
    # 参与门槛：纯靠归零白嫖的小号不给（本群累计玩过 EMERGENCY_MIN_GAMES 局才发）
    if int(sget("EMERGENCY_MIN_GAMES") or 0) > 0 and int(games_played[cid][uid] or 0) < int(sget("EMERGENCY_MIN_GAMES")):
        return False
    wallet[cid][uid] = sget("EMERGENCY_CHIPS")
    if poker and uid in poker.chips: poker.chips[uid] += sget("EMERGENCY_CHIPS")
    _earn_add(cid, uid, sget("EMERGENCY_CHIPS"))   # 归零赠送属于「白给分」，计入累计积分
    # 台账：白给的分是「凭空产出」，不记流水的话玩家会以为这笔分来路不明/被吞了
    ledger_add(cid, 0, uid, sget("EMERGENCY_CHIPS"), "应急赠送")
    daily_emergency_used[cid][uid] = used + 1; save_data()
    remaining = sget("EMERGENCY_MAX_USES") - daily_emergency_used[cid][uid]
    schedule_notice_delete(app, cid, await safe_send(app.bot, cid, f"🆘 {await get_name(app, uid)} 积分归零，已赠送 {sget('EMERGENCY_CHIPS')} 应急积分（今日已补充 {daily_emergency_used[cid][uid]}/{sget('EMERGENCY_MAX_USES')} 次，剩余 {remaining} 次）。"))
    return True
