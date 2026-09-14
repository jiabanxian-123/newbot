# -*- coding: utf-8 -*-
"""③ 强制订阅频道

配置：FORCE_SUB_CHANNELS（@用户名 或 -100xxx，订阅其一即可）、FORCE_SUB_ONLY_NEW
（只拦入群 10 分钟内的新人）、提示语与自动删除时长。
探测结果带短缓存（_fsub_ok_cache / _fsub_invite_cache），避免每条消息都打 TG API。
"""

from core import hub

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import html, re, time


def _fsub_parse(ch):
    """把网页「须订阅的频道」里填的内容统一解析成 get_chat_member 能用的 key。

    支持：@用户名 / 裸用户名 / t.me/用户名 / https://t.me/用户名 / -100xxx 频道 id。
    ⚠️ 私有邀请链接（t.me/+hash、t.me/joinchat/xxx）Bot API **查不了成员状态**，返回 None；
    调用方必须跳过该频道，绝不能把它当作「未订阅」——这正是 2026-09-10 用户报的
    「群友订阅成功却还是被删消息」的根因（当时填的是完整链接，int() 抛异常被吞）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _FSUB_RE_HANDLE = hub._FSUB_RE_HANDLE
    _FSUB_RE_URL = hub._FSUB_RE_URL
    s = str(ch or "").strip()
    if not s:
        return None
    if "t.me/+" in s or "joinchat/" in s:
        return None                      # 私有邀请链接：无法查询
    if re.fullmatch(r"-?\d+", s):        # 纯数字 / -100xxx 频道 id
        try:
            return int(s)
        except Exception:
            return None
    m = _FSUB_RE_URL.search(s)           # 链接形式
    if m:
        return "@" + m.group(1)
    m = _FSUB_RE_HANDLE.fullmatch(s)     # @用户名 或裸用户名
    if m:
        return "@" + m.group(1)
    return None


async def _fsub_links(context):
    """把 FORCE_SUB_CHANNELS 转成 [(显示名, 链接)]：@用户名→公开 t.me 链接；数字 id→get_chat 邀请链接（缓存）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fsub_invite_cache = hub._fsub_invite_cache
    sget = hub.sget
    out = []
    for ch in sget("FORCE_SUB_CHANNELS"):
        raw = str(ch or "").strip()
        if not raw:
            continue
        if raw.startswith("@"):
            out.append((raw, f"https://t.me/{raw.lstrip('@')}"))
        elif "t.me/" in raw:
            _u = raw.split("t.me/")[-1].strip("/")
            out.append((f"@{_u}" if not _u.startswith("+") else "频道", f"https://t.me/{_u}"))
        else:
            try:
                if raw in _fsub_invite_cache:
                    out.append((f"频道 {raw}", _fsub_invite_cache[raw]))
                    continue
                chat = await context.bot.get_chat(int(raw))
                url = getattr(chat, "invite_link", None) or (f"https://t.me/{chat.username}" if getattr(chat, "username", "") else "")
                if url:
                    _fsub_invite_cache[raw] = url
                    out.append((getattr(chat, "title", None) or f"频道 {raw}", url))
            except Exception:
                continue
    return out


async def _fsub_probe(context, user_id):
    """查询 user_id 是否订阅了任一 FORCE_SUB_CHANNELS。

    返回 (ok, checked)：
      ok=True  → 至少一个频道确认已订阅；
      checked=0 → 一个频道都没查成功（配置是私有链接 / bot 不在频道 / 非管理员 / 网络异常），
                  调用方应 fail-open 放行，避免误删已订阅的群友。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fsub_parse = hub._fsub_parse
    logger = hub.logger
    sget = hub.sget
    checked = 0
    for ch in sget("FORCE_SUB_CHANNELS"):
        key = _fsub_parse(ch)
        if key is None:
            logger.warning("强制订阅：频道配置 %r 无法解析（私有邀请链接 Bot 查不了成员，"
                           "请改填 -100xxx 频道 id 或 @用户名）", ch)
            continue
        try:
            _m = await context.bot.get_chat_member(key, user_id)
        except Exception as e:
            logger.warning("强制订阅：查询频道 %s 成员状态失败（需把机器人加入该频道并设为管理员）：%s", key, e)
            continue
        checked += 1
        if (getattr(_m, "status", "left") in ("member", "administrator", "creator", "restricted")
                or getattr(_m, "is_member", False)):
            return True, checked
    return False, checked


async def _forcesub_enforce(update, context):
    """强制订阅频道：未订阅任一频道的成员发言即删+提示（带订阅按钮）。

    返回 True=已拦截（消息已处理，调用方直接 return）。管理员/群管/Bot 管理员豁免；
    已订阅判定缓存 30 分钟、未订阅缓存 60 秒（订阅后一分钟内自动放行），避免每条消息打 API。

    ⚠️ fail-open（2026-09-10 修）：只有 API **明确返回**未订阅才删消息；频道配置解析不出、
    查询抛异常（bot 不在频道 / 非管理员 / 网络抖动）一律放行——宁可漏拦也不误删已订阅的群友。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fmt_tpl = hub._fmt_tpl
    _fsub_links = hub._fsub_links
    _fsub_ok_cache = hub._fsub_ok_cache
    _fsub_probe = hub._fsub_probe
    _group_admins_get_async = hub._group_admins_get_async
    is_bot_admin = hub.is_bot_admin
    is_group_chat = hub.is_group_chat
    logger = hub.logger
    member_joined_at = hub.member_joined_at
    schedule_delete_ids = hub.schedule_delete_ids
    sget = hub.sget
    user_names = hub.user_names
    if not sget("FORCE_SUB_ENABLED") or not sget("FORCE_SUB_CHANNELS"):
        return False
    message, user = update.effective_message, update.effective_user
    if not message or not user or user.is_bot or not is_group_chat(update) or is_bot_admin(user.id):
        return False
    cid = update.effective_chat.id
    try:
        if (await _group_admins_get_async(cid)).get(user.id):
            return False
    except Exception:
        pass
    if sget("FORCE_SUB_ONLY_NEW"):
        _jt = member_joined_at.get(cid, {}).get(user.id, 0)
        if not _jt or time.time() - _jt > 600:
            return False
    now = time.time()
    _hit = _fsub_ok_cache.get(cid, {}).get(user.id)
    ok = None
    if _hit:
        _age = now - _hit[0]
        if _hit[1] and _age < 1800:
            return False                 # 已订阅（30 分钟缓存）→ 直接放行
        if not _hit[1] and _age < 60:
            ok = False                   # 未订阅（60 秒负缓存）→ 不重复打 API
    if ok is None:
        ok, _checked = await _fsub_probe(context, user.id)
        if not ok and _checked == 0:
            # 一个频道都没查成功 → 配置/权限问题，放行避免误删（fail-open）
            _fsub_ok_cache[cid][user.id] = (now, True)
            return False
        _fsub_ok_cache[cid][user.id] = (now, ok)
        if ok:
            return False
    # 未订阅：删消息 + 发提示（订阅其一即可；提示可自动删除）
    try:
        await context.bot.delete_message(chat_id=cid, message_id=message.message_id)
    except Exception:  # silent-ok: 删不掉未订阅者消息只是外观；踢人失败已单独记 exception
        pass
    _prev = _fsub_ok_cache[cid].get(f"warned:{user.id}")
    if not (_prev and now - _prev < 60):   # 60 秒内只发一次提示，不刷屏
        try:
            links = await _fsub_links(context)
            # 频道名做成可点蓝字（HTML 文本超链接）：点一下 Telegram 直接跳频道
            _ch_html = "、".join(f"<a href='{html.escape(str(u), quote=True)}'>{html.escape(str(l))}</a>"
                                 for l, u in links if u)
            if not _ch_html:
                _ch_html = "、".join(html.escape(str(c)) for c in sget("FORCE_SUB_CHANNELS"))
            txt = _fmt_tpl("force_sub_warn_tpl",
                           name=html.escape(str(user_names.get(user.id) or user.first_name or f"用户{user.id}")),
                           channels=_ch_html,
                           seconds=sget("FORCE_SUB_WARN_SECONDS"))
            _rows = [[InlineKeyboardButton(f"📢 加入 {l}", url=u)] for l, u in links if u]
            _rows.append([InlineKeyboardButton("✅ 我已加入", callback_data="fsub_recheck")])
            sent = await context.bot.send_message(cid, txt, parse_mode="HTML",
                                                  reply_markup=InlineKeyboardMarkup(_rows))
            _fsub_ok_cache[cid][f"warned:{user.id}"] = now
            if sget("FORCE_SUB_WARN_SECONDS") > 0:
                # 走统一队列：持 task 引用 + 持久化 + 60s 兜底，裸 create_task 会被 GC 导致永不删除
                schedule_delete_ids(context.application, cid, sent.message_id,
                                    int(sget("FORCE_SUB_WARN_SECONDS")))
        except Exception:
            logger.exception("强制订阅提示发送失败 cid=%s（已吞并）", cid)
    return True
