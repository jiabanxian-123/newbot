# -*- coding: utf-8 -*-
"""**Bot 管理员命令**：赌神 / 加减分 / 黑名单 / 管理员增删 / 群授权 / 本群赛车开关
—— 从 core/members.py 分家（2026-09-14）。

同 group.py，这 10 个只由 bot.py 的命令别名表引用。
⚠️ `cmd_autosm`（本群整点赛车开关）是个**赛车功能开关**，躺在这里纯属历史原因；
   归到本文件是因为它同样只被别名表引用、动作也只是翻转一个群级开关。
   真要归位应去 features/race —— 跨包搬迁要用户拍板，此处不动。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
"""

from core import hub

import html


async def cmd_god(update, context):
    """查看当前赌神与历届荣誉墙。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    champions_history = hub.champions_history
    get_name = hub.get_name
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    user_titles = hub.user_titles
    if not await need_auth(update, context): return
    app = context.application
    cid = update.effective_chat.id
    lines = ["👑 <b>🎰赌神 荣誉殿堂</b>", "━" * 16]
    champ_uid = next((u for u, ts in user_titles.items() if TITLE_GAMBLING_GOD in ts), None)
    if champ_uid is None:
        lines.append("当前暂无 🎰赌神。拿下赛季冠军即可加冕！")
    else:
        lines.append(f"🏅 现任赌神：{await get_name(app, champ_uid, cid=cid, with_title=False)}")
    if champions_history:
        lines.append("")
        lines.append("📜 <b>历届荣誉墙</b>")
        for rec in champions_history[-12:][::-1]:
            streak = rec.get("streak", 1)
            sfx = f" · {streak}连冠" if streak > 1 else ""
            name = html.escape(str(rec.get("name", "?")))
            lines.append(f"第{rec['season_id']}赛季：{name}（{rec.get('score', 0)}分）{sfx}")
    else:
        lines.append("")
        lines.append("📜 历届荣誉墙：暂无记录")
    text = "\n".join(lines)
    try:
        await send_reply(update, context, text)
    except Exception:
        # 历史称号含 < & 等特殊字符导致 HTML 渲染失败时，降级为纯文本发送，避免命令“失效无响应”
        await send_reply(update, context, text, parse_mode=None)


async def cmd_god_grant(update, context):
    """管理员封赌神（全局唯一，覆盖上任）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    is_bot_admin = hub.is_bot_admin
    save_data = hub.save_data
    send_reply = hub.send_reply
    user_titles = hub.user_titles
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    if not context.args:
        await send_reply(update, context, "用法：/封赌神 <用户ID>"); return
    try:
        uid = int(context.args[0])
    except ValueError:
        await send_reply(update, context, "❌ 用户 ID 必须是数字。"); return
    for _u in list(user_titles.keys()):
        user_titles[_u].discard(TITLE_GAMBLING_GOD)
        if not user_titles[_u]:
            del user_titles[_u]
    user_titles.setdefault(uid, set()).add(TITLE_GAMBLING_GOD)
    save_data()
    await send_reply(update, context, f"👑 已将 {uid} 封为 🎰赌神（覆盖上任）。")


async def cmd_god_revoke(update, context):
    """管理员撤赌神。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    is_bot_admin = hub.is_bot_admin
    save_data = hub.save_data
    send_reply = hub.send_reply
    title_equipped = hub.title_equipped
    user_titles = hub.user_titles
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    if not context.args:
        await send_reply(update, context, "用法：/撤赌神 <用户ID>"); return
    try:
        uid = int(context.args[0])
    except ValueError:
        await send_reply(update, context, "❌ 用户 ID 必须是数字。"); return
    if uid in user_titles and TITLE_GAMBLING_GOD in user_titles[uid]:
        user_titles[uid].discard(TITLE_GAMBLING_GOD)
        if title_equipped.get(uid) == TITLE_GAMBLING_GOD:
            title_equipped.pop(uid, None)
        if not user_titles[uid]:
            del user_titles[uid]
        save_data()
        await send_reply(update, context, f"🔻 已撤销 {uid} 的 🎰赌神 称号。")
    else:
        await send_reply(update, context, "ℹ️ 该用户当前没有 🎰赌神 称号。")


async def cmd_add(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _check_level_change = hub._check_level_change
    _earn_add = hub._earn_add
    _earn_get = hub._earn_get
    _fmt_tpl = hub._fmt_tpl
    _parse_target_amount = hub._parse_target_amount
    game_chips = hub.game_chips
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    ledger_add = hub.ledger_add
    need_auth = hub.need_auth
    player_is_busy = hub.player_is_busy
    save_data = hub.save_data
    send_reply = hub.send_reply
    sget = hub.sget
    wallet_locks = hub.wallet_locks
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    if not sget("ADMIN_ADJUST_ENABLED"):
        await send_reply(update, context, "❌ 管理员加减分功能已关闭（网页「积分系统 → 积分设置」可开启）。"); return
    if not await need_auth(update, context): return
    try:
        uid, amount = await _parse_target_amount(update, context)
        if amount == 0: raise ValueError
    except (ValueError, IndexError):
        await send_reply(update, context, "用法：/add 用户ID 数量（正为加，负为减），或回复玩家消息后使用 /add 数量"); return
    cid = update.effective_chat.id
    if player_is_busy(cid, uid):
        await send_reply(update, context, "该玩家正在游戏中，无法修改积分。"); return
    async with wallet_locks[uid]:
        if amount < 0 and game_chips[cid][uid] < -amount:
            await send_reply(update, context, "❌ 玩家积分不足。"); return
        old_earned = _earn_get(cid, uid)
        game_chips[cid][uid] += amount
        if amount > 0:
            _earn_add(cid, uid, amount)   # 管理员加分算「获得」；扣分不回退累计（只增不减）
        # 台账：加分记「谁给的」（frm=0 系统），扣分记「从谁那扣的」（frm=uid）——
        # cmd_points_flow 按 frm==我 → 支出、to==我 → 收入 来判方向，两侧各记一半即可。
        if amount > 0:
            ledger_add(cid, 0, uid, amount, "管理员加分")
        else:
            ledger_add(cid, uid, 0, -amount, "管理员扣分")
        save_data()
    verb = "添加" if amount > 0 else "扣除"
    msg = _fmt_tpl("add_msg_tpl", target=await get_name(context.application, uid),
                   verb=verb, amount=abs(amount), balance=game_chips[cid][uid])
    await send_reply(update, context, msg)
    await _check_level_change(context.application, cid, uid, old_earned, _earn_get(cid, uid))


async def cmd_qxshouquan(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    is_bot_admin = hub.is_bot_admin
    save_data = hub.save_data
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id): return
    try: cid = int(context.args[0])
    except (IndexError, ValueError): await send_reply(update, context, "用法：取消授权 群ID（或 /qxsh 群ID）"); return
    AUTHORIZED_GROUPS.discard(cid); save_data(); await send_reply(update, context, f"✅ 已取消授权 {cid}")


async def cmd_ban(update, context):
    """管理员拉黑玩家（禁止使用机器人）。支持 /拉黑 用户ID 或 回复玩家消息 /拉黑"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    save_data = hub.save_data
    send_reply = hub.send_reply
    user_names = hub.user_names
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    target = None
    replied = update.message.reply_to_message
    if replied:
        target = replied.from_user.id
        # 顺带缓存被回复者的真名，黑名单列表不再显示"玩家{ID}"
        ru = replied.from_user
        if not ru.is_bot:
            nm = ru.full_name or (f"@{ru.username}" if ru.username else None)
            if nm: user_names[target] = nm
    else:
        try: target = int(context.args[0])
        except (IndexError, ValueError): pass
    if not target:
        await send_reply(update, context, "用法：/拉黑 用户ID，或回复玩家消息后使用 /拉黑"); return
    if is_bot_admin(target):
        await send_reply(update, context, "⚠️ 不能拉黑管理员。"); return
    if target in BLACKLISTED_USERS:
        await send_reply(update, context, "ℹ️ 该用户已在黑名单中。"); return
    # 用 ID 拉黑且尚无缓存名字时，主动 get_chat 取名缓存（失败则回退"玩家{ID}"）
    if target not in user_names:
        try:
            chat = await context.bot.get_chat(target)
            nm = getattr(chat, "first_name", None) or getattr(chat, "title", None) or (f"@{chat.username}" if getattr(chat, "username", None) else None)
            if nm: user_names[target] = nm
        except Exception:
            pass
    BLACKLISTED_USERS.add(target); save_data()
    await send_reply(update, context, f"🚫 已拉黑 {await get_name(context.application, target)}（{target}），该用户已被禁止使用机器人。")


async def cmd_unban(update, context):
    """管理员解封玩家。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    save_data = hub.save_data
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    target = None
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user.id
    else:
        try: target = int(context.args[0])
        except (IndexError, ValueError): pass
    if not target:
        await send_reply(update, context, "用法：/解黑 用户ID，或回复玩家消息后使用 /解黑"); return
    if target not in BLACKLISTED_USERS:
        await send_reply(update, context, "ℹ️ 该用户不在黑名单中。"); return
    BLACKLISTED_USERS.discard(target); save_data()
    await send_reply(update, context, f"✅ 已解封 {await get_name(context.application, target)}（{target}）。")


async def cmd_addadmin(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BOT_ADMINS = hub.BOT_ADMINS
    is_bot_admin = hub.is_bot_admin
    save_data = hub.save_data
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    try: uid = int(context.args[0])
    except (IndexError, ValueError):
        await send_reply(update, context, "用法：/addadmin 用户ID，例如 /addadmin 123456789"); return
    if uid in BOT_ADMINS:
        await send_reply(update, context, f"ℹ️ {uid} 已经是管理员了"); return
    BOT_ADMINS.add(uid); save_data()
    await send_reply(update, context, f"✅ 已添加机器人管理员：{uid}")


async def cmd_deladmin(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_IDS = hub.ADMIN_USER_IDS
    BOT_ADMINS = hub.BOT_ADMINS
    is_bot_admin = hub.is_bot_admin
    save_data = hub.save_data
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    try: uid = int(context.args[0])
    except (IndexError, ValueError):
        await send_reply(update, context, "用法：/deladmin 用户ID，例如 /deladmin 123456789"); return
    if uid in ADMIN_USER_IDS:
        await send_reply(update, context, f"⚠️ {uid} 是种子管理员，重启后自动恢复，无法移除（如需移除请改代码 ADMIN_USER_IDS）"); return
    if uid not in BOT_ADMINS:
        await send_reply(update, context, f"ℹ️ {uid} 不是管理员"); return
    BOT_ADMINS.discard(uid); save_data()
    await send_reply(update, context, f"✅ 已移除机器人管理员：{uid}")


async def cmd_autosm(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    hourly_race_enabled = hub.hourly_race_enabled
    is_bot_admin = hub.is_bot_admin
    need_auth = hub.need_auth
    save_data = hub.save_data
    send_reply = hub.send_reply
    if not await need_auth(update, context): return
    if not is_bot_admin(update.effective_user.id): await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    cid = update.effective_chat.id
    cur = hourly_race_enabled.get(cid, True)   # 授权群默认开启，此处按群覆盖
    hourly_race_enabled[cid] = not cur; save_data()
    await send_reply(update, context, f"本群整点自动赛车：{'✅ 已开启' if not cur else '❌ 已关闭（总开关和时段仍需在后台配置）'}")
