# -*- coding: utf-8 -*-
"""⑤ 进群硬门槛

不满足就当场移出，不给验证机会。三项都可单独开关（join_gate_username /
join_gate_premium / join_gate_bio）；查 API 失败时**放行**（宁可漏拦不误杀）。
另含 `_is_join_transition`：判断一次 chat_member 更新算不算「进群」，
入群相关的三处入口（硬门槛 / 防突袭 / 入群验证）共用这一个判定。
"""

from core import hub

import html, time


def _is_join_transition(new, old):
    """是否属于「进入群聊」的状态变更（进群判定唯一入口）。

    - new 在 _JOIN_IN_CHAT 且 old 是 left/kicked → 进群。
    - **restricted → member 不算进群**：那是 bot 自己解除限制触发的状态变更，
      若算进群会再次发验证 → 解限/发验证互相触发，死循环。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _JOIN_IN_CHAT = hub._JOIN_IN_CHAT
    ns = str(getattr(new, "status", "") or "")
    os_ = str(getattr(old, "status", "") or "")
    if ns not in _JOIN_IN_CHAT:
        return False
    return os_ in ("left", "kicked")


async def _join_gate_check(context, cid, member, name):
    """进群硬门槛：用户名 / Premium / 简介，任一不满足 → 直接移出（不进验证流程）。全关或放行返回 True。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _gate_kicked_ts = hub._gate_kicked_ts
    logger = hub.logger
    sget = hub.sget
    if not (sget("JOIN_GATE_USERNAME") or sget("JOIN_GATE_PREMIUM") or sget("JOIN_GATE_BIO")):
        return True
    reasons = []
    if sget("JOIN_GATE_USERNAME") and not getattr(member, "username", None):
        reasons.append("无用户名")
    if sget("JOIN_GATE_PREMIUM") and not getattr(member, "is_premium", False):
        reasons.append("非Premium")
    if sget("JOIN_GATE_BIO"):
        bio_ok = None   # None=查询失败（不拦，宁放过不误杀） True=有简介 False=无简介
        try:
            ch = await context.bot.get_chat(member.id)
            bio_ok = bool(str(getattr(ch, "bio", "") or "").strip())
        except Exception:
            logger.exception("进群门槛：查简介失败 uid=%s（宁放过不误杀）", member.id)
        if bio_ok is False:
            reasons.append("无简介")
    if not reasons:
        return True
    # 双事件源去重：同一次进群 chat_member + 服务消息各调一次，不去重会重复 ban + 提示发 2 条（2026-09-10 复扫发现）
    _gk = f"{cid}:{member.id}"
    _now = time.time()
    if float(_gate_kicked_ts.get(_gk, 0) or 0) and _now - float(_gate_kicked_ts[_gk]) < 60:
        return False
    _gate_kicked_ts[_gk] = _now
    try:
        await context.bot.ban_chat_member(cid, member.id)
        await context.bot.unban_chat_member(cid, member.id)   # 踢出（可自行再进，先过门槛再说）
    except Exception:
        logger.exception("进群门槛踢出失败 cid=%s uid=%s", cid, member.id)
    try:
        await context.bot.send_message(
            cid, f"🚪 {html.escape(str(name))} 未满足进群要求（{'、'.join(reasons)}），已移出。")
    except Exception:  # silent-ok: 提示发不出不影响已完成的移出动作；踢人失败已单独记 exception
        pass
    return False
