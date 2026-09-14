# -*- coding: utf-8 -*-
"""**群管命令**：禁言 / 解禁 / 群封 / 群解封 / 白名单（3 条）—— 从 core/members.py 分家（2026-09-14）。

这 7 个只由 bot.py 的命令别名表引用（**没有任何模块依赖它们**）＝纯"装修"；
而 core/members.py 剩下的「身份与权限」是全站地基。地基和装修混在一起，
想确认"权限到底怎么判的"要翻过 700 行命令实现。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
"""

from core import hub

from telegram import ChatPermissions


async def cmd_mute(update, context):
    """禁言：回复消息发「禁言 分钟」或「禁言 用户ID 分钟」。白名单免疫。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _admin_log = hub._admin_log
    _is_group_admin = hub._is_group_admin
    is_group_chat = hub.is_group_chat
    need_auth = hub.need_auth
    now_bj = hub.now_bj
    save_data = hub.save_data
    send_reply = hub.send_reply
    user_names = hub.user_names
    whitelist = hub.whitelist
    if not await need_auth(update, context): return
    cid, admin = update.effective_chat.id, update.effective_user.id
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 禁言请在群聊中使用。"); return
    if not await _is_group_admin(context, cid, admin):
        await send_reply(update, context, "❌ 仅管理员可操作"); return
    args = context.args or []
    reply = update.message.reply_to_message
    if reply and args and args[0].isdigit():
        target, minutes = reply.from_user.id, max(1, min(int(args[0]), 43200))
    elif len(args) >= 2 and args[0].lstrip("-").isdigit() and args[1].isdigit():
        target, minutes = int(args[0]), max(1, min(int(args[1]), 43200))
    else:
        await send_reply(update, context, "用法：回复消息发「禁言 分钟」，或「禁言 用户ID 分钟」"); return
    if target in whitelist[cid]:
        await send_reply(update, context, "✅ 该用户在白名单中，已跳过禁言。"); return
    if target == admin:
        await send_reply(update, context, "❌ 不能禁言自己。"); return
    try:
        await context.bot.restrict_chat_member(cid, target, permissions=ChatPermissions(can_send_messages=False),
                                               until_date=int(now_bj().timestamp()) + minutes * 60)
    except Exception as e:
        await send_reply(update, context, f"❌ 禁言失败（需 bot 为群管理员且有禁言权限）：{e}"); return
    tname = user_names.get(target, str(target))
    _admin_log(cid, admin, f"禁言 {minutes} 分钟", tname); save_data()
    await send_reply(update, context, f"🔇 已禁言 {tname} {minutes} 分钟。")


async def cmd_unmute(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _admin_log = hub._admin_log
    _is_group_admin = hub._is_group_admin
    need_auth = hub.need_auth
    save_data = hub.save_data
    send_reply = hub.send_reply
    user_names = hub.user_names
    if not await need_auth(update, context): return
    cid, admin = update.effective_chat.id, update.effective_user.id
    if not await _is_group_admin(context, cid, admin):
        await send_reply(update, context, "❌ 仅管理员可操作"); return
    reply = update.message.reply_to_message
    args = context.args or []
    if reply:
        target = reply.from_user.id
    elif args and args[0].lstrip("-").isdigit():
        target = int(args[0])
    else:
        await send_reply(update, context, "用法：回复消息发「解禁」，或「解禁 用户ID」"); return
    try:
        await context.bot.restrict_chat_member(cid, target, permissions=ChatPermissions(
            can_send_messages=True, can_send_other_messages=True, can_add_web_page_previews=True,
            can_send_polls=True, can_invite_users=True))
    except Exception as e:
        await send_reply(update, context, f"❌ 解禁失败：{e}"); return
    _admin_log(cid, admin, "解除禁言", user_names.get(target, str(target))); save_data()
    await send_reply(update, context, f"🔊 已解除 {user_names.get(target, target)} 的禁言。")


async def cmd_groupban(update, context):
    """Telegram 级封禁：踢出并禁止再入群（区别于 /拉黑 的 bot 层黑名单）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _admin_log = hub._admin_log
    _is_group_admin = hub._is_group_admin
    is_group_chat = hub.is_group_chat
    need_auth = hub.need_auth
    save_data = hub.save_data
    send_reply = hub.send_reply
    user_names = hub.user_names
    whitelist = hub.whitelist
    if not await need_auth(update, context): return
    cid, admin = update.effective_chat.id, update.effective_user.id
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 请在群聊中使用。"); return
    if not await _is_group_admin(context, cid, admin):
        await send_reply(update, context, "❌ 仅管理员可操作"); return
    reply = update.message.reply_to_message
    args = context.args or []
    if reply:
        target = reply.from_user.id
    elif args and args[0].lstrip("-").isdigit():
        target = int(args[0])
    else:
        await send_reply(update, context, "用法：回复消息发「群封」，或「群封 用户ID」"); return
    if target in whitelist[cid]:
        await send_reply(update, context, "✅ 该用户在白名单中，已跳过。"); return
    try:
        await context.bot.ban_chat_member(cid, target)
    except Exception as e:
        await send_reply(update, context, f"❌ 封禁失败（需 bot 为群管理员）：{e}"); return
    tname = user_names.get(target, str(target))
    _admin_log(cid, admin, "Telegram级封禁", tname); save_data()
    await send_reply(update, context, f"🔨 已将 {tname} 封禁并移出群组（可用「群解封」撤销）。")


async def cmd_groupunban(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _admin_log = hub._admin_log
    _is_group_admin = hub._is_group_admin
    need_auth = hub.need_auth
    save_data = hub.save_data
    send_reply = hub.send_reply
    if not await need_auth(update, context): return
    cid, admin = update.effective_chat.id, update.effective_user.id
    if not await _is_group_admin(context, cid, admin):
        await send_reply(update, context, "❌ 仅管理员可操作"); return
    args = context.args or []
    if not args or not args[0].lstrip("-").isdigit():
        await send_reply(update, context, "用法：群解封 用户ID"); return
    target = int(args[0])
    try:
        await context.bot.unban_chat_member(cid, target, only_if_banned=True)
    except Exception as e:
        await send_reply(update, context, f"❌ 解封失败：{e}"); return
    _admin_log(cid, admin, "Telegram级解封", str(target)); save_data()
    await send_reply(update, context, f"✅ 已解封 {target}，可重新拉入群。")


async def cmd_whitelist(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    user_names = hub.user_names
    whitelist = hub.whitelist
    if not await need_auth(update, context): return
    cid = update.effective_chat.id
    users = whitelist.get(cid, set())
    if not users:
        await send_reply(update, context, "白名单为空。回复成员消息发「加白」可加入。"); return
    lines = ["📋 白名单成员", "━" * 14]
    for i, u in enumerate(sorted(users), 1):
        lines.append(f"{i}. {user_names.get(u, u)}（{u}）")
    lines.append("💡 白名单成员免疫禁言/群封；「加白」「删白」管理。")
    await send_reply(update, context, "\n".join(lines))


async def cmd_whitelist_add(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _admin_log = hub._admin_log
    _is_group_admin = hub._is_group_admin
    need_auth = hub.need_auth
    save_data = hub.save_data
    send_reply = hub.send_reply
    user_names = hub.user_names
    whitelist = hub.whitelist
    if not await need_auth(update, context): return
    cid, admin = update.effective_chat.id, update.effective_user.id
    if not await _is_group_admin(context, cid, admin):
        await send_reply(update, context, "❌ 仅管理员可操作"); return
    reply = update.message.reply_to_message
    args = context.args or []
    if reply:
        target = reply.from_user.id
    elif args and args[0].lstrip("-").isdigit():
        target = int(args[0])
    else:
        await send_reply(update, context, "用法：回复成员消息发「加白」，或「加白 用户ID」"); return
    whitelist[cid].add(target); save_data()
    _admin_log(cid, admin, "加白名单", user_names.get(target, str(target)))
    await send_reply(update, context, f"✅ 已把 {user_names.get(target, target)} 加入白名单。")


async def cmd_whitelist_del(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _admin_log = hub._admin_log
    _is_group_admin = hub._is_group_admin
    need_auth = hub.need_auth
    save_data = hub.save_data
    send_reply = hub.send_reply
    user_names = hub.user_names
    whitelist = hub.whitelist
    if not await need_auth(update, context): return
    cid, admin = update.effective_chat.id, update.effective_user.id
    if not await _is_group_admin(context, cid, admin):
        await send_reply(update, context, "❌ 仅管理员可操作"); return
    reply = update.message.reply_to_message
    args = context.args or []
    if reply:
        target = reply.from_user.id
    elif args and args[0].lstrip("-").isdigit():
        target = int(args[0])
    else:
        await send_reply(update, context, "用法：回复成员消息发「删白」，或「删白 用户ID」"); return
    whitelist[cid].discard(target); save_data()
    _admin_log(cid, admin, "移出白名单", user_names.get(target, str(target)))
    await send_reply(update, context, f"✅ 已把 {user_names.get(target, target)} 移出白名单。")
