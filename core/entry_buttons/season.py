# -*- coding: utf-8 -*-
"""按钮回调 handler —— 2026-09-13 从 core/entry.py 的 on_button 拆出。

只做搬运，未改任何逻辑。每个函数对应 on_button 里一个 `data` 命名空间分支；
外层保留 `if <原 test>: await <fn>(...); return`（分支体命中后必 return，故等价）。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
⚠️ 本包**禁止** import core.entry（循环导入）。
"""
from core import hub



async def _btn_season(update, context, q, cid, uid, data):
    _exchange_rate_text = hub._exchange_rate_text
    render_season_lobby = hub.render_season_lobby
    safe_delete = hub.safe_delete
    season_active = hub.season_active
    season_lobby_msg = hub.season_lobby_msg
    season_signup = hub.season_signup
    season_standings_lines = hub.season_standings_lines
    send_reply = hub.send_reply
    sget = hub.sget
    if data == "season_signup":
        ok, key = await season_signup(context.application, cid, uid)
        await render_season_lobby(context.application, cid)
        if key == "started":
            await q.answer("🏆 报名已满，赛季自动开始！用 /赛季 开局", show_alert=True)
        elif key == "joined_active":
            await q.answer("✅ 已加入进行中的赛季")
        else:
            await q.answer("✅ 已报名")
        return
    if data == "season_exchange_info":
        # 大厅按钮：只回提示，不直接扣分（避免误触扣款，兑换走 /游戏积分兑换 数量）
        if not sget("RANKED_EXCHANGE_ENABLED"):
            await q.answer("积分兑换赛季分功能未开启", show_alert=True); return
        await q.answer(f"发「/游戏积分兑换 数量」即可兑换\n当前比例 {_exchange_rate_text()}"
                       + (f"\n每人每日上限 {sget('RANKED_EXCHANGE_DAILY_LIMIT')} 积分" if sget("RANKED_EXCHANGE_DAILY_LIMIT") else ""),
                       show_alert=True)
        return
    if data == "season_lobby_close":
        mid = season_lobby_msg.pop(cid, None)
        if mid: await safe_delete(context.bot, cid, mid)
        await q.answer("已关闭看板"); return
    if data == "season_rank_btn":
        if not season_active:
            await q.answer("当前无进行中的赛季", show_alert=True); return
        lines = await season_standings_lines(context.application, cid, uid=uid)
        await send_reply(update, context, "\n".join(lines))
        await q.answer("已发送赛季榜"); return
    await q.answer("未知操作", show_alert=True); return
