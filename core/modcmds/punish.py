# -*- coding: utf-8 -*-
"""群管处罚**原语** —— 从 core/members.py 分家出来（2026-09-14）。

`_mod_punish`（禁言/踢出/封禁的统一执行）被 features 的 joinverify / levels /
raid / sensitive 等 **5 个模块**复用 —— 它是**域中立的动作原语**，不是哪条命令的
私有实现，所以单列一个文件，别塞进 group.py（那会让反捣乱域反向依赖"命令文件"）。
`_admin_log` 同理（joinverify 也在用）。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
"""

from core import hub

from datetime import datetime, timedelta, timezone
from telegram import ChatPermissions


async def _mod_punish(context, cid, uid, action, mute_seconds, name, reason):
    """群管统一处罚：1=禁言 2=踢出（踢出用 ban+立即 unban，成员可自行回来）。异常全吞。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    try:
        if action == 1:
            await context.bot.restrict_chat_member(
                cid, uid, permissions=ChatPermissions(can_send_messages=False),
                until_date=datetime.now(timezone.utc) + timedelta(seconds=max(30, int(mute_seconds))))
        elif action == 2:
            await context.bot.ban_chat_member(cid, uid)
            await context.bot.unban_chat_member(cid, uid)
        elif action == 3:
            await context.bot.ban_chat_member(cid, uid)   # 封禁：只 ban 不解封
    except Exception:
        logger.exception("群管处罚失败 cid=%s uid=%s action=%s（已吞并）", cid, uid, action)


def _admin_log(cid, admin_uid, action, target):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    admin_logs = hub.admin_logs
    now_bj = hub.now_bj
    user_names = hub.user_names
    admin_logs.append({"ts": now_bj().strftime("%Y-%m-%d %H:%M"), "cid": cid,
                       "admin": user_names.get(admin_uid, str(admin_uid)), "action": action, "target": target})
