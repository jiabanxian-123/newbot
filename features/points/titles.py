# -*- coding: utf-8 -*-
"""④ 称号（商店 / 兑换 / 佩戴 / 加封）

④ 称号：商店称号列表 / 兑换 / 查看 / 佩戴，以及管理员加封（纯逻辑 grant/revoke）。

· `grant_title` / `revoke_title` 是**纯逻辑**（不落盘、不发消息、不碰网络），
  群内命令与网页后台共用同一条路径；**全局唯一类称号（赌神）的唯一性只在这里实现**。
· 撤销时要顺手清 `title_equipped`，否则留下「佩戴着一个不存在的称号」的悬空引用。
· `title_prefix` 的优先级：手动佩戴 > 赌神 > 最贵商店称号（免费用称号前缀冒充赌神会被覆盖）。
· `all_titles()` 是网页下拉的唯一数据源，也是「称号一个没丢」的测试锚点。
"""

from core import hub

import html


def title_icon(title):
    """称号图标，缺省空串。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    TITLE_ICONS = hub.TITLE_ICONS
    return TITLE_ICONS.get(title, "")


def all_titles():
    """称号库全量（商店称号按原顺序 + 赌神），供网页下拉/批量选择使用。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    SHOP_TITLES = hub.SHOP_TITLES
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    return list(SHOP_TITLES.keys()) + [TITLE_GAMBLING_GOD]


def grant_title(uid, title, expire_ts=None):
    """给玩家加封称号（**纯逻辑**：不落盘、不发消息、不碰网络，便于单测）。

    网页后台「称号加封」与群内 /封赌神 共用这一条路径 —— 管理员直接给称号，
    不扣积分、不经过商店那条「兑换」流程。
    赌神仍是全局唯一：加封时自动撤销上任。
    返回 (ok, msg)。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    SHOP_TITLES = hub.SHOP_TITLES
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    title_expiry = hub.title_expiry
    title_icon = hub.title_icon
    user_titles = hub.user_titles
    title = str(title or "").strip()
    if title not in SHOP_TITLES and title != TITLE_GAMBLING_GOD:
        return False, f"「{title}」不在称号库中"
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return False, "用户 ID 必须是数字"
    if title == TITLE_GAMBLING_GOD:          # 全局唯一：先撤掉所有旧持有者
        for u in list(user_titles.keys()):
            user_titles[u].discard(title)
            if not user_titles[u]:
                del user_titles[u]
    user_titles.setdefault(uid, set()).add(title)
    if expire_ts:                            # 限时称号：记到期时间戳
        title_expiry.setdefault(uid, {})[title] = float(expire_ts)
    else:                                    # 永久：清掉同名限时残留（防串味）
        title_expiry.get(uid, {}).pop(title, None)
        if uid in title_expiry and not title_expiry[uid]:
            del title_expiry[uid]
    return True, f"已为 {uid} 加封「{title_icon(title)}{title}」"


def revoke_title(uid, title):
    """撤销玩家的某个称号（**纯逻辑**，不落盘）。返回 (ok, msg)。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    title_equipped = hub.title_equipped
    title_expiry = hub.title_expiry
    user_titles = hub.user_titles
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return False, "用户 ID 必须是数字"
    title = str(title or "").strip()
    if title not in (user_titles.get(uid) or set()):
        return False, f"该玩家没有「{title}」称号"
    user_titles[uid].discard(title)
    if not user_titles[uid]:
        del user_titles[uid]
    title_expiry.get(uid, {}).pop(title, None)
    if uid in title_expiry and not title_expiry[uid]:
        del title_expiry[uid]
    if title_equipped.get(uid) == title:     # 撤销的正好是佩戴中的 → 取消佩戴，回落默认前缀
        title_equipped.pop(uid, None)
    return True, f"已撤销 {uid} 的「{title}」"


def title_prefix(uid):
    """持称号的玩家在名字前加称号前缀。优先级：手动佩戴 > 赌神 > 最贵商店称号。
    称号均为预设固定串（不含 <>&），HTML/纯文本均安全。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    SHOP_TITLES = hub.SHOP_TITLES
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    now_bj = hub.now_bj
    title_equipped = hub.title_equipped
    title_expiry = hub.title_expiry
    title_icon = hub.title_icon
    user_titles = hub.user_titles
    ts = user_titles.get(uid)
    if not ts:
        return ""
    # 过滤已过期的限时称号（避免 daily_reset 清理前仍显示过期称号）
    _exp = title_expiry.get(uid)
    if _exp:
        _now = int(now_bj().timestamp())
        ts = {t for t in ts if t not in _exp or _exp[t] > _now}
    if not ts:
        return ""
    equipped = title_equipped.get(uid)
    if equipped and equipped in ts:
        return f"{title_icon(equipped)}{equipped} "
    if TITLE_GAMBLING_GOD in ts:
        return f"{TITLE_GAMBLING_GOD} "
    best = max((t for t in ts if t in SHOP_TITLES), key=lambda t: hub.SHOP_TITLES[t]["price"], default=None)
    return f"{title_icon(best)}{best} " if best else ""


async def cmd_shop(update, context):
    """积分商店：列出可兑换的称号。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    SHOP_TITLES = hub.SHOP_TITLES
    is_group_chat = hub.is_group_chat
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    title_icon = hub.title_icon
    if not await need_auth(update, context): return
    if not is_group_chat(update):
        await send_reply(update, context, "🏪 积分商店请在群聊中使用（发 /商店）。"); return
    lines = ["🏪 <b>积分商店 · 称号兑换</b>", "━" * 16]
    for t, cfg in SHOP_TITLES.items():
        cur = "积分"
        dur = "永久" if cfg["duration"] is None else f"{cfg['duration'] // 86400}天"
        lines.append(f"• {title_icon(t)}<b>{html.escape(str(t))}</b>：{cfg['price']} {cur}｜{dur}")
    lines.append("")
    lines.append("💡 用 /兑换 称号名 购买；/我的称号 查看，/佩戴 切换亮出的称号。")
    await send_reply(update, context, "\n".join(lines))


async def cmd_redeem(update, context):
    """兑换称号：扣统一积分 + 挂称号（永久或限时）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    SHOP_TITLES = hub.SHOP_TITLES
    game_chips = hub.game_chips
    is_group_chat = hub.is_group_chat
    need_auth = hub.need_auth
    now_bj = hub.now_bj
    player_is_busy = hub.player_is_busy
    save_data = hub.save_data
    send_reply = hub.send_reply
    title_expiry = hub.title_expiry
    title_icon = hub.title_icon
    user_titles = hub.user_titles
    wallet_locks = hub.wallet_locks
    if not await need_auth(update, context): return
    if not is_group_chat(update):
        await send_reply(update, context, "🏪 积分商店请在群聊中使用（发 /商店）。"); return
    if not context.args:
        await send_reply(update, context, "用法：/兑换 称号名（用 /商店 查看可兑换称号）"); return
    # 容错：用户经常顺手多打「购买/一个/来一个」之类，按空格 join 后找不到。
    # 优先取第一个参数（称号意图词），join 作为兜底（SHOP_TITLES 实际全无空格）。
    _t1 = context.args[0].strip()
    _t2 = "".join(context.args).strip()
    # ★ 原来 cfg 可能来自 _t2（拼起来命中），但下面发奖 / 去重一律用 _t1（第一个词）
    #   →「/兑换 赌 狗」会扣 5000 分却发一个根本不在称号库里的「赌」，
    #     而且去重查的也是「赌」，重复兑换永远拦不住。
    #   这里统一成**真正命中的那个键**。
    title = _t1 if _t1 in SHOP_TITLES else (_t2 if _t2 in SHOP_TITLES else _t1)
    cfg = SHOP_TITLES.get(title)
    if not cfg:
        await send_reply(update, context, "❌ 该称号不存在，用 /商店 查看可兑换称号。"); return
    uid = update.effective_user.id
    cid = update.effective_chat.id
    # 已持有且未过期则拒绝重复兑换
    held = user_titles.get(uid, set())
    if title in held:
        exp = title_expiry.get(uid, {}).get(title)
        if exp is None or exp > int(now_bj().timestamp()):
            await send_reply(update, context, "ℹ️ 你已持有该称号，无需重复兑换。"); return
    if player_is_busy(cid, uid):
        await send_reply(update, context, "⚠️ 你正在游戏中，请先结束当前游戏再兑换。"); return
    wallet = game_chips
    cur = "积分"
    async with wallet_locks[uid]:
        if wallet[cid][uid] < cfg["price"]:
            await send_reply(update, context, f"❌ 你的{cur}不足：需要 {cfg['price']}，当前 {wallet[cid][uid]}。"); return
        wallet[cid][uid] -= cfg["price"]
        user_titles.setdefault(uid, set()).add(title)
        if cfg["duration"] is not None:
            title_expiry.setdefault(uid, {})[title] = int(now_bj().timestamp()) + cfg["duration"]
        else:
            title_expiry.setdefault(uid, {}).pop(title, None)
        save_data()
    dur = "永久" if cfg["duration"] is None else f"{cfg['duration'] // 86400}天"
    await send_reply(update, context, f"🎉 兑换成功！获得称号 {title_icon(title)}<b>{html.escape(str(title))}</b>（{dur}），花费 {cfg['price']} {cur}，剩余 {wallet[cid][uid]}。", parse_mode="HTML")


async def cmd_my_titles(update, context):
    """查看我持有的所有称号。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    SHOP_TITLES = hub.SHOP_TITLES
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    need_auth = hub.need_auth
    now_bj = hub.now_bj
    send_reply = hub.send_reply
    title_equipped = hub.title_equipped
    title_expiry = hub.title_expiry
    title_icon = hub.title_icon
    user_titles = hub.user_titles
    if not await need_auth(update, context): return
    uid = update.effective_user.id
    ts = user_titles.get(uid, set())
    if not ts:
        await send_reply(update, context, "你还没有任何称号，用 /商店 查看可兑换称号。")
        return
    lines = ["🎖 <b>我的称号</b>", "━" * 16]
    equipped = title_equipped.get(uid)
    now = int(now_bj().timestamp())
    for t in sorted(ts, key=lambda x: (-hub.SHOP_TITLES.get(x, {}).get("price", 0), x)):
        mark = " 👈佩戴中" if t == equipped else ""
        if t == TITLE_GAMBLING_GOD:
            lines.append(f"👑 {html.escape(str(t))}（赛季冠军专属）{mark}")
        else:
            cfg = SHOP_TITLES.get(t, {})
            if cfg.get("duration") is not None:
                exp = title_expiry.get(uid, {}).get(t, 0)
                if exp <= now:
                    lines.append(f"• {title_icon(t)}{html.escape(str(t))}（已过期，待清理）{mark}")
                else:
                    remain = max(1, (exp - now + 86399) // 86400)
                    lines.append(f"• {title_icon(t)}{html.escape(str(t))}（剩余约 {remain} 天）{mark}")
            else:
                lines.append(f"• {title_icon(t)}{html.escape(str(t))}（永久）{mark}")
    lines.append("")
    lines.append("💡 用 /佩戴 称号名 切换亮出的称号；不佩戴则默认显示最贵的。")
    await send_reply(update, context, "\n".join(lines))


async def cmd_equip(update, context):
    """佩戴某个已持有的称号（切换昵称前缀，可覆盖默认）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    need_auth = hub.need_auth
    save_data = hub.save_data
    send_reply = hub.send_reply
    title_equipped = hub.title_equipped
    user_titles = hub.user_titles
    if not await need_auth(update, context): return
    if not context.args:
        await send_reply(update, context, "用法：/佩戴 称号名（用 /我的称号 查看你持有的称号）")
        return
    title = "".join(context.args)
    uid = update.effective_user.id
    ts = user_titles.get(uid, set())
    if title not in ts:
        await send_reply(update, context, "❌ 你尚未持有该称号，用 /我的称号 查看。")
        return
    title_equipped[uid] = title
    save_data()
    await send_reply(update, context, f"✅ 已佩戴 <b>{html.escape(str(title))}</b>，将显示在昵称前。", parse_mode="HTML")
