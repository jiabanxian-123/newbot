# -*- coding: utf-8 -*-
"""infra/members —— **身份与权限**（全站地基）。

2026-09-14 分家（桌面清单第 2 项）：本文件原 913 行 / 40 个函数，混了三类东西，
其中只有 13 个属于「身份与权限」。它是**全站地基**——`is_bot_admin` 被 15 个模块
直接依赖、`get_name` 14 个、`need_auth` 11 个，错了全盘都错，所以必须小到一眼看完。
已搬走：
  · 19 个命令实现（群管 + Bot 管理员）→ `core/modcmds/`（punish / group / admin）
  · 8 个各域历史遗留助手（**归属存疑**）→ `core/member_utils.py`

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 本模块用到的标准库/第三方 import
import asyncio, html, time


def _extract_name(chat):
    """从 Chat/User 对象提取展示名；提取不到返回 None。"""
    name = " ".join(part for part in (chat.first_name, chat.last_name) if part)
    return name or (f"@{chat.username}" if getattr(chat, "username", None) else None)


def _remember_name(update):
    """从任意入站 update 抓取发送者真名进 user_names 缓存（零 API 调用，优先于 get_chat）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    chat_name_cache = hub.chat_name_cache
    user_names = hub.user_names
    u = update.effective_user
    if not u or u.is_bot: return
    name = u.full_name or (f"@{u.username}" if u.username else None)
    if name: user_names[u.id] = name
    # 顺带缓存群名：授权列表等场景无需再调 get_chat（避免被异常静默吞掉导致只显示 ID）
    c = update.effective_chat
    if c and c.title:
        chat_name_cache[c.id] = c.title


async def _warm_group_names(app):
    """启动群名预热：把全部授权群的标题拉进 chat_name_cache。

    没有这步时，新部署/没来过消息的群在网页上全是裸群ID或 ?（用户点名要求补群名）。
    单群失败（bot 已不在该群等）只警告不炸，不影响其他群。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    chat_name_cache = hub.chat_name_cache
    logger = hub.logger
    for cid in list(AUTHORIZED_GROUPS):
        if cid in chat_name_cache:
            continue
        try:
            ch = await app.bot.get_chat(cid)
            if getattr(ch, "title", None):
                chat_name_cache[cid] = ch.title
        except Exception:
            logger.warning("群名预热失败 cid=%s（bot 可能已不在该群）", cid)


async def _group_admins_get_async(cid, max_age=300):
    """群主/管理员缓存（5 分钟）·异步版：返回 {uid: "owner"|"admin"}。

    【handler 内必须用这个】直接 await，不走 run_coroutine_threadsafe，
    因此不会出现「循环里等循环」的自锁。bot 不在群/接口失败返回上次缓存或空 dict。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _bot_app = hub._bot_app
    _group_admins_cache = hub._group_admins_cache
    logger = hub.logger
    rec = _group_admins_cache.get(cid)
    now = time.time()
    if rec and now - float(rec[0]) < max_age:
        return rec[1]
    try:
        out = {}
        for a in await _bot_app.bot.get_chat_administrators(cid):
            out[a.user.id] = "owner" if getattr(a, "status", "") == "creator" else "admin"
        _group_admins_cache[cid] = (now, out)
        return out
    except Exception:
        logger.warning("拉取群管理员失败 cid=%s（按无徽章展示）", cid)
    return rec[1] if rec else {}


def _group_admins_get(cid, max_age=300):
    """群主/管理员缓存（5 分钟）·同步版：返回 {uid: "owner"|"admin"}。

    仅供「网页后台线程」等非异步上下文调用（跨线程投递到 bot 循环）。
    若当前已在 bot 主循环里，绝不能跨线程回投（会自锁 8 秒）——
    此时只返回缓存、不主动拉取；需要拉取的异步 handler 请用 _group_admins_get_async。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _bot_app = hub._bot_app
    _bot_loop = hub._bot_loop
    _group_admins_cache = hub._group_admins_cache
    _in_bot_loop = hub._in_bot_loop
    logger = hub.logger
    rec = _group_admins_cache.get(cid)
    now = time.time()
    if rec and now - float(rec[0]) < max_age:
        return rec[1]
    if _in_bot_loop():
        # 自锁防护：在 bot 循环内不跨线程回投，直接用现有缓存（下次异步路径会刷新）
        return rec[1] if rec else {}
    if _bot_app and _bot_loop:
        try:
            async def _fetch():
                out = {}
                for a in await hub._bot_app.bot.get_chat_administrators(cid):
                    out[a.user.id] = "owner" if getattr(a, "status", "") == "creator" else "admin"
                return out
            out = asyncio.run_coroutine_threadsafe(_fetch(), _bot_loop).result(8)
            _group_admins_cache[cid] = (now, out)
            return out
        except Exception:
            logger.warning("拉取群管理员失败 cid=%s（按无徽章展示）", cid)
    return rec[1] if rec else {}


async def get_name(app, uid, with_title=True, cid=None):
    """解析玩家展示名。

    解析优先级：1) 群内 get_chat_member(cid, uid)（群里成员必能解，即使没和 bot 私聊）；
    2) get_chat(uid)；3) 已缓存的 user_names。全部失败才回退为“玩家{uid}”。
    解析成功的真名写入 user_names 缓存，减少后续 API 调用与失败率。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _extract_name = hub._extract_name
    title_prefix = hub.title_prefix
    user_names = hub.user_names
    raw = user_names.get(uid)
    if raw is None:
        if cid is not None:
            try:
                member = await app.bot.get_chat_member(cid, uid)
                raw = _extract_name(member.user)
            except Exception:
                raw = None
        if raw is None:
            try:
                raw = _extract_name(await app.bot.get_chat(uid))
            except Exception:
                raw = None
        if raw:
            user_names[uid] = raw
    if not raw:
        raw = f"玩家{uid}"
    base = html.escape(str(raw))
    return f"{title_prefix(uid)}{base}" if with_title else base


def is_auth(cid):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    return cid in AUTHORIZED_GROUPS


def is_bot_admin(uid):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BOT_ADMINS = hub.BOT_ADMINS
    return uid in BOT_ADMINS


def _admin_notify_ids():
    """后台通知收件人：ADMIN_USER_ID + 全部 BOT_ADMINS（去重、过滤无效 0）。

    ⚠️ 必须放在**模块级**：网页后台作用域里原本有个同名的嵌套函数，
    模块级代码（如定时/后台任务）调不到它（pyflakes: undefined name）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    BOT_ADMINS = hub.BOT_ADMINS
    ids = {ADMIN_USER_ID}
    ids.update(BOT_ADMINS)
    return sorted(i for i in ids if i)


async def need_auth(update, context=None):
    # 授权只针对「群聊」：私聊没有群组概念，不应被「群组未授权」拦截。
    # 私聊里真正受限的游戏/管理命令，各自还有 require_group_chat / is_bot_admin 兜底。
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_auth = hub.is_auth
    is_bot_admin = hub.is_bot_admin
    send_reply = hub.send_reply
    user_first_seen = hub.user_first_seen
    _u = update.effective_user
    if _u and _u.id and _u.id not in user_first_seen:
        user_first_seen[_u.id] = time.time()  # 首次互动时间（商城兑换门槛用）
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        if not is_auth(chat.id):
            # 新群默认不在授权名单里，而「初始积分/签到/游戏」等全部走这里拦截。
            # 管理员自己在新群里会只看到「请联系管理员」却无路可走（用户实际踩过），
            # 所以对 Bot 管理员直接把「本群怎么授权」写清楚，一步可解。
            if _u and is_bot_admin(_u.id):
                _tip = (f"❌ 本群尚未授权，群内积分/游戏等功能不会生效。\n"
                        f"你是 Bot 管理员，直接在本群发送 /授权 即可（群号 {chat.id}）。")
            else:
                _tip = "❌ 此群组未授权，请联系管理员。"
            if update.effective_message:
                if context is not None and update.message: await send_reply(update, context, _tip)
                else: await update.effective_message.reply_text(_tip)
            return False
    return True


def is_group_chat(update):
    """消息是否来自群聊/超级群（多人游戏只能在此发起，私聊开别人看不到）。"""
    chat_type = update.effective_chat.type if update.effective_chat else None
    return chat_type in ("group", "supergroup")


async def require_group_chat(update, game_name, cmd, context=None):
    """多人游戏必须在群聊发起；私聊里开只有发起人自己看得到。返回 False 时已回复提示。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_group_chat = hub.is_group_chat
    send_reply = hub.send_reply
    if not is_group_chat(update):
        await send_reply(update, context, 
            f"⚠️ {game_name}是多人游戏，请在群聊中发起（发送 /{cmd}），别人才能一起玩。私聊里开只有你自己看得到。")
        return False
    return True


async def _is_group_admin(context, cid, uid):
    """判断是否群管理员（创建者/管理员），失败时仅认 Bot 管理员体系。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_bot_admin = hub.is_bot_admin
    if is_bot_admin(uid):
        return True
    try:
        member = await context.bot.get_chat_member(cid, uid)
        return member.status in ("administrator", "creator")
    except Exception:
        return False
