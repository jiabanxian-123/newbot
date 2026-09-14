# -*- coding: utf-8 -*-
"""**潜水号清理**（每 6 小时）：入群超 LURKER_DAYS 天且累计发言少于 LURKER_MSGS 条 → 按档处理。

从 features/schedule/__init__.py 分家（2026-09-14，桌面清单第 4 项）：每个定时任务独立成一个小文件，加任务 = 加一个文件 + 状态表里加一行。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
"""

from core import hub

import html, time

__all__ = ["lurker_sweep"]


async def lurker_sweep(context):
    """潜水号清理（每 6 小时）：入群超 LURKER_DAYS 天且累计发言少于 LURKER_MSGS 条 → 按档处理。

    处理过/已豁免的人记 lurker_checked 不反复骚扰；管理员豁免。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    _CUR_CID = hub._CUR_CID
    _mod_punish = hub._mod_punish
    _safe_cid = hub._safe_cid
    group_get = hub.group_get
    is_bot_admin = hub.is_bot_admin
    lurker_checked = hub.lurker_checked
    member_joined_at = hub.member_joined_at
    member_profiles = hub.member_profiles
    sget = hub.sget
    for cid, joined in list(member_joined_at.items()):
        if not group_get(cid, "lurker_enabled"):
            continue          # 该群没开潜水清理（群级可覆盖全局开关）
        _CUR_CID.set(_safe_cid(cid))   # 天数/条数等阈值按该群配置解析
        hits = []
        for uid, jt in list(joined.items()):
            key = f"{cid}:{uid}"
            if key in lurker_checked:
                continue
            if not jt or time.time() - float(jt) < max(1, int(sget("LURKER_DAYS"))) * 86400:
                continue
            lurker_checked.add(key)   # 不论结果只处理一次（活跃者以后也不会变潜水：发言数只增不减）
            if is_bot_admin(uid):
                continue
            prof = member_profiles.get(cid, {}).get(uid, {}) or {}
            if int(prof.get("msgs", 0) or 0) >= int(sget("LURKER_MSGS")):
                continue
            hits.append((uid, str(prof.get("name") or f"用户{uid}")))
        if not hits:
            continue
        if sget("LURKER_ACTION") == 0:
            try:
                await context.bot.send_message(
                    ADMIN_USER_ID,
                    f"💤 潜水巡查：群 <code>{cid}</code> 发现 {len(hits)} 个潜水号"
                    f"（入群超 {int(sget('LURKER_DAYS'))} 天、发言少于 {int(sget('LURKER_MSGS'))} 条）：\n"
                    + "\n".join(f"· {html.escape(str(nm))}（{u_}）" for u_, nm in hits[:20])
                    + "\n可在「群管中心」改为自动禁言/踢出。")
            except Exception:
                pass
        else:
            for u_, nm in hits:
                await _mod_punish(context, cid, u_, sget("LURKER_ACTION"), sget("SENSITIVE_MUTE_SECONDS"), nm, "潜水清理")
            try:
                await context.bot.send_message(
                    ADMIN_USER_ID,
                    f"💤 潜水巡查：群 <code>{cid}</code> 已按配置"
                    f"{'禁言' if sget('LURKER_ACTION') == 1 else '踢出'} {len(hits)} 个潜水号。")
            except Exception:
                pass
