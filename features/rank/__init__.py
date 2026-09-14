# -*- coding: utf-8 -*-
"""feature/rank —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from datetime import datetime, timedelta, timezone
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
import asyncio
import html

def rank_marker(index):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    RANK_ICONS = hub.RANK_ICONS
    sget = hub.sget
    if index == 1 and sget("RANK_1_EMOJI"): return sget("RANK_1_EMOJI")
    if index == 2 and sget("RANK_2_EMOJI"): return sget("RANK_2_EMOJI")
    if index == 3 and sget("RANK_3_EMOJI"): return sget("RANK_3_EMOJI")
    return RANK_ICONS[index - 1] if 1 <= index <= len(RANK_ICONS) else f"🔸{index}"


def rank_line(index, uid, name_html, tail):
    """排行榜单行统一渲染：`🥇 <蓝色可点名字>：+120`。

    tail 是名字后面的整段（含全角冒号），由各游戏自己拼，保证各榜原有格式不变。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    rank_marker = hub.rank_marker
    user_link = hub.user_link
    return f"{rank_marker(index)} {user_link(uid, name_html)}{tail}"


def _rank_delete_secs():
    """榜单消息回收秒数（0=不删）。榜单是「查询结果」，不该像牌桌一样永久占屏。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    try:
        return int(sget("RANK_DELETE_SECONDS") or 0)
    except (TypeError, ValueError):
        return 0


async def broadcast_big_win(app, cid, uid, game_name, net, detail=""):
    """大奖战报：单局净赢超阈值时推送。**事发群必发**，其余授权群一起广播制造气氛。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    get_name = hub.get_name
    logger = hub.logger
    safe_send = hub.safe_send
    sget = hub.sget
    try:
        if not sget("BROADCAST_ENABLED") or net < max(1, sget("BROADCAST_MIN_AMOUNT")): return
        if cid not in AUTHORIZED_GROUPS: return
        name = await get_name(app, uid, cid=cid)
        extra = f"\n{detail}" if detail else ""
        text = (f"📣 <b>战报快讯</b>\n{game_name}｜{html.escape(str(name))} 单局豪赢 <b>{net}</b> 积分{extra}")
        # ⚠️ 2026-09-12 修「战报快讯在发生的群不推送，反而推送别的群」：
        #   老代码遍历授权群时遇到「事发群自己」就跳过 —— 等于把刚中大奖的群排除掉，
        #   于是它一条战报都收不到，气氛全送给了不相干的群。
        #   现在事发群固定排第一个（必发），其余授权群照旧广播。
        for g in [cid] + [x for x in AUTHORIZED_GROUPS if x != cid]:
            await safe_send(app.bot, g, text, parse_mode="HTML")
    except Exception:
        logger.exception("大奖战报广播失败（不影响结算）")


def _rank_source(cid, kind):
    """返回某榜的原始数据 [(uid, value)]，已降序。

    一律用 `.get` / 遍历取值，**不用裸下标读 defaultdict**（读了会凭空建幽灵键，见 SKILL §4.31）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    blackjack_profit_by_date = hub.blackjack_profit_by_date
    business_date = hub.business_date
    game_chips = hub.game_chips
    jinhua_profit_by_date = hub.jinhua_profit_by_date
    poker_profit_by_date = hub.poker_profit_by_date
    race_profit_by_date = hub.race_profit_by_date
    total_profit_by_game = hub.total_profit_by_game
    if kind == "points":
        rows = list(game_chips.get(cid, {}).items())
    elif kind == "texas_day":
        rows = list(poker_profit_by_date.get(business_date(), {}).get(cid, {}).items())
    else:  # profit / other_profit：炸金花 + 21点 + 赛车（含老虎机）累计合并
        combined = {}
        for g in (blackjack_profit_by_date, race_profit_by_date, jinhua_profit_by_date):
            for u, v in total_profit_by_game(g, cid).items():
                combined[u] = combined.get(u, 0) + v
        rows = list(combined.items())
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


async def _rank_page_text(app, cid, kind, page):
    """渲染某榜第 page 页（0 基）。返回 (text, page, pages)。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    RANK_PAGE_SIZE = hub.RANK_PAGE_SIZE
    _RANK_SIGNED = hub._RANK_SIGNED
    _RANK_SUBTITLES = hub._RANK_SUBTITLES
    _RANK_TITLES = hub._RANK_TITLES
    _rank_source = hub._rank_source
    _safe_html_clip = hub._safe_html_clip
    get_name = hub.get_name
    rank_line = hub.rank_line
    src = _rank_source(cid, kind)
    total = len(src)
    pages = max(1, (total + RANK_PAGE_SIZE - 1) // RANK_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * RANK_PAGE_SIZE
    chunk = src[start:start + RANK_PAGE_SIZE]
    # 标题 + 副标题：副标题一行小字说清「这个榜排的是哪个数」（2026-09-13 用户选 Q15=A）
    lines = [f"<b>{_RANK_TITLES[kind]}</b>"]
    _sub = _RANK_SUBTITLES.get(kind)
    if _sub:
        lines.append(f"<i>{_sub}</i>")
    lines.append("━" * 14)
    if not chunk:
        lines.append("暂无记录")
        return "\n".join(lines), page, pages
    for i, (uid, value) in enumerate(chunk, start + 1):
        # 昵称截断：防长昵称把整条消息撑宽（用户 2026-09-11 截图投诉）
        # ⚠️ 用 _safe_html_clip 而不是裸 clip_name：get_name 返回的是**已转义**文本，
        #   在 `&amp;` 中间截断会留下半截实体的，整条消息 HTML 直接解析失败（掉回纯文本，
        #   玩家会看到裸露的 <a href=...>）。截断后再包蓝色可点链接（2026-09-12 用户要求）。
        name = _safe_html_clip(await get_name(app, uid, cid=cid))
        if kind in _RANK_SIGNED:
            lines.append(rank_line(i, uid, name, f"：{value:+d}"))
        else:
            # 2026-09-12 用户「去除积分榜上面的标签」→ 榜上只留纯积分，不再跟随等级名。
            lines.append(rank_line(i, uid, name, f"：{value}"))
    lines.append("")
    lines.append(f"<i>共 {total} 人 · 第 {page + 1}/{pages} 页</i>")
    return "\n".join(lines), page, pages


def _rank_rearm(app, cid, mid):
    """把榜单消息的回收倒计时重排一次（到期时间刷新为「现在 + RANK_DELETE_SECONDS」）。

    走 `own_messages`（显式声明生命周期的统一出口）：
      · RANK_DELETE_SECONDS > 0 → 续期；
      · = 0（后台关掉回收）→ **撤销**已排的删除 = 永久保留。
    不再依赖「调用方函数名命中 _AUTODEL_MARKUP_IGNORE_FUNCS」——那套改名就静默失配。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _rank_delete_secs = hub._rank_delete_secs
    own_messages = hub.own_messages
    if app is not None and mid:
        own_messages(app, cid, mid, _rank_delete_secs())


def _rank_keyboard(kind, page, pages, kinds):
    """翻页/切换键盘。文案刻意用最短形式——按钮文案会把整条消息撑宽（用户 2026-09-11 截图投诉）。

    2026-09-13：翻页箭头由单细尖角 `‹` / `›` 换成**实心三角** `◀` / `▶`
    （用户：「箭头要弄的好看点啊，那个太丑了」→ 选了方案 A · 实心三角）。
    仍然是 1 个字符宽，不会把消息撑宽。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _RANK_BTNS = hub._RANK_BTNS
    rows = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"rk_{kind}_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="rk_noop_0"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"rk_{kind}_{page + 1}"))
    rows.append(nav)
    if len(kinds) > 1:
        rows.append([InlineKeyboardButton(_RANK_BTNS[k], callback_data=f"rk_{k}_0") for k in kinds])
    return InlineKeyboardMarkup(rows)


async def send_rank_page(update, context, kind, page=0):
    """发出分页榜单（第一条消息）。

    榜单带翻页/切榜按钮，但**照样会被回收**（RANK_DELETE_SECONDS，默认 300 秒、后台可调 0=不删）：
    它是「查询结果」而不是牌桌，用户想看再点一次就好。翻页/切榜会重新计时（见 on_rank_page）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _RANK_GROUPS = hub._RANK_GROUPS
    _rank_keyboard = hub._rank_keyboard
    _rank_page_text = hub._rank_page_text
    _rank_rearm = hub._rank_rearm
    cid = update.effective_chat.id
    text, page, pages = await _rank_page_text(context.application, cid, kind, page)
    kb = _rank_keyboard(kind, page, pages, _RANK_GROUPS.get(kind, (kind,)))
    msg = await context.bot.send_message(chat_id=cid, text=text, reply_markup=kb,
                                         parse_mode="HTML", autodel_own=True)
    # ⚠️ 2026-09-12 用户再次报「积分榜不自动删除」——**显式排入回收**，不再只靠
    #   「默认自动删除」补丁的栈帧识别：那条路要求调用方函数名命中 `_AUTODEL_MARKUP_IGNORE_FUNCS`，
    #   任何一次调用链改名 / 外面再包一层转发函数，都会**静默失效**（不报错、不写日志）。
    #   现在由发送方自己负责生命周期，补丁那条只是多余的保险（schedule_delete_ids 自带去重）。
    if msg is not None:
        _rank_rearm(context.application, cid, msg.message_id)
    return msg


async def cmd_auth_list(update, context):
    """管理员查看所有已授权群组（列出群 ID，尽量附带群名）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    chat_name_cache = hub.chat_name_cache
    is_bot_admin = hub.is_bot_admin
    logger = hub.logger
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    if not AUTHORIZED_GROUPS:
        await send_reply(update, context, "📋 当前没有任何已授权群组。"); return
    lines = ["📋 <b>已授权群组列表</b>", f"共 {len(AUTHORIZED_GROUPS)} 个：", "━"*14]
    for cid in sorted(AUTHORIZED_GROUPS):
        title = chat_name_cache.get(cid)
        if not title:
            try:
                chat = await context.bot.get_chat(cid)
                if getattr(chat, "title", None):
                    title = chat.title
                    chat_name_cache[cid] = title
            except Exception as e:
                logger.warning("授权列表取群名失败 cid=%s: %s", cid, e)
        lines.append(f"• {title}（{cid}）" if title else f"• {cid}（群名未知，bot 可能已不在该群）")
    await send_reply(update, context, "\n".join(lines))


async def cmd_banlist(update, context):
    """管理员查看黑名单。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    if not BLACKLISTED_USERS:
        await send_reply(update, context, "📋 当前黑名单为空。"); return
    lines = [f"📋 <b>黑名单（共 {len(BLACKLISTED_USERS)} 人）</b>", "━"*14]
    for uid in sorted(BLACKLISTED_USERS):
        lines.append(f"• {await get_name(context.application, uid)}（{uid}）")
    await send_reply(update, context, "\n".join(lines))


async def cmd_list_all(update, context):
    """管理员一键查看：管理员 / 授权群 / 黑名单 三合一总览。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_IDS = hub.ADMIN_USER_IDS
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    BOT_ADMINS = hub.BOT_ADMINS
    chat_name_cache = hub.chat_name_cache
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    logger = hub.logger
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    app = context.application
    lines = ["📋 <b>管理总览</b>", "━"*18]

    # 管理员
    seeds = set(ADMIN_USER_IDS)
    dynamic = BOT_ADMINS - seeds
    lines.append(f"👑 <b>管理员（{len(BOT_ADMINS)}）</b>")
    for uid in sorted(seeds):
        lines.append(f"  🔒 {await get_name(app, uid)}（{uid}）")
    for uid in sorted(dynamic):
        lines.append(f"  ➕ {await get_name(app, uid)}（{uid}）")
    if not dynamic:
        lines.append("  （无动态管理员）")
    lines.append("")

    # 授权群
    lines.append(f"✅ <b>已授权群（{len(AUTHORIZED_GROUPS)}）</b>")
    if not AUTHORIZED_GROUPS:
        lines.append("  （无）")
    else:
        for cid in sorted(AUTHORIZED_GROUPS):
            title = chat_name_cache.get(cid)
            if not title:
                try:
                    chat = await context.bot.get_chat(cid)
                    if getattr(chat, "title", None):
                        title = chat.title; chat_name_cache[cid] = title
                except Exception as e:
                    logger.warning("取群名失败 %s: %s", cid, e)
            lines.append(f"  • {title}（{cid}）" if title else f"  • {cid}（群名未知）")
    lines.append("")

    # 黑名单
    lines.append(f"🚫 <b>黑名单（{len(BLACKLISTED_USERS)}）</b>")
    if not BLACKLISTED_USERS:
        lines.append("  （无）")
    else:
        for uid in sorted(BLACKLISTED_USERS):
            lines.append(f"  • {await get_name(app, uid)}（{uid}）")

    await send_reply(update, context, "\n".join(lines))


async def cmd_admin_list(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_IDS = hub.ADMIN_USER_IDS
    BOT_ADMINS = hub.BOT_ADMINS
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    seeds = set(ADMIN_USER_IDS)
    dynamic = BOT_ADMINS - seeds
    lines = ["👑 <b>当前机器人管理员</b>",
             f"共 <b>{len(BOT_ADMINS)}</b> 人（种子 {len(seeds)} + 动态 {len(dynamic)}）", ""]
    lines.append("🔒 种子管理员（重启保留，不可被 /deladmin 移除）：")
    for uid in sorted(seeds):
        lines.append(f"  • {await get_name(context.application, uid)}（{uid}）")
    lines.append("")
    if dynamic:
        lines.append("➕ 动态添加（可被 /deladmin 移除）：")
        for uid in sorted(dynamic):
            lines.append(f"  • {await get_name(context.application, uid)}（{uid}）")
    else:
        lines.append("（暂无动态添加的管理员）")
    await send_reply(update, context, "\n".join(lines))


async def cmd_adminlist_tg(update, context):
    """列出本群 Telegram 管理员（实时接口）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    if not await need_auth(update, context): return
    cid = update.effective_chat.id
    try:
        admins = await context.bot.get_chat_administrators(cid)
    except Exception as e:
        await send_reply(update, context, f"❌ 获取失败：{e}"); return
    lines = ["👥 本群管理员", "━" * 14]
    for a in sorted(admins, key=lambda x: (x.status != "creator", x.user.id)):
        mark = "👑" if a.status == "creator" else "⚙️"
        lines.append(f"{mark} {a.user.first_name or ''}（{a.user.id}）")
    await send_reply(update, context, "\n".join(lines))


async def leaderboard_scheduler(app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    RANK_PAGE_SIZE = hub.RANK_PAGE_SIZE
    _CUR_CID = hub._CUR_CID
    _safe_cid = hub._safe_cid
    _season_base = hub._season_base
    business_date = hub.business_date
    get_name = hub.get_name
    leaderboard_groups = hub.leaderboard_groups
    logger = hub.logger
    now_bj = hub.now_bj
    parse_hm = hub.parse_hm
    poker_profit_by_date = hub.poker_profit_by_date
    rank_line = hub.rank_line
    safe_send_long = hub.safe_send_long
    save_data = hub.save_data
    season_games = hub.season_games
    season_points = hub.season_points
    sget = hub.sget
    while True:
        now = now_bj()
        lh, lm = parse_hm(sget("LEADERBOARD_TIME"), 23, 50)  # 每轮重读，网页改时间即时生效
        target = now.replace(hour=lh, minute=lm, second=0, microsecond=0)
        if target <= now: target += timedelta(days=1)
        await asyncio.sleep((target-now).total_seconds())
        if not sget("LEADERBOARD_ENABLED"):  # 后台「定时任务」开关：关闭期间到点不推送
            continue
        if not leaderboard_groups: leaderboard_groups.update(AUTHORIZED_GROUPS)
        target_groups = leaderboard_groups & AUTHORIZED_GROUPS
        if not target_groups: continue
        try:
            # ★ 每轮重读可重赋值的 bot 全局：协程启动时只读一次会**永久陈旧**
            season_active = hub.season_active
            season_id = hub.season_id
            # 只推送并清空德州当日榜；其他游戏榜保留累计（总数）
            date = now_bj().strftime("%Y-%m-%d"); texas_snapshot = poker_profit_by_date.pop(date, {})
            # 兜底：业务日在 23:50 翻日，23:50-23:59 的下注记录在下一日业务日键下，一并并入当日榜避免丢失
            date_next = business_date()
            if date_next != date:
                for c, ud in poker_profit_by_date.pop(date_next, {}).items():
                    texas_snapshot.setdefault(c, {})
                    for u, a in ud.items():
                        texas_snapshot[c][u] = texas_snapshot.get(c, {}).get(u, 0) + a
            for cid, data in texas_snapshot.items():
                if not data: continue
                if cid not in target_groups: continue  # 只推目标群
                _CUR_CID.set(_safe_cid(cid))
                lines = [f"🏆 德州当日排行榜（{date}）", "━"*14]
                for i, (uid, amount) in enumerate(sorted(data.items(), key=lambda x:x[1], reverse=True)[:RANK_PAGE_SIZE], 1): lines.append(rank_line(i, uid, await get_name(app, uid), f"：{amount:+d}"))
                await safe_send_long(app.bot, cid, "\n".join(lines))
            # 赛季每日 23:50 推送「当日盈亏」（当前分 - 基准分，兑换底分不计入）
            if season_active:
                for cid in list(season_points.keys()):
                    if cid not in target_groups: continue  # 只推目标群
                    _CUR_CID.set(_safe_cid(cid))   # 起始分/最低局数按该群配置解析
                    users = season_points.get(cid, {})
                    if not users: continue
                    # 当日盈亏 = 当前分 - (起始分+兑换底分)，与赛季榜口径一致（兑换分不计入）
                    day_rows = [(u, val - _season_base(cid, u)) for u, val in users.items()]
                    day_standings = sorted(day_rows, key=lambda x: (-x[1], x[0]))
                    lines = [f"🏆 第{season_id}赛季 当日盈亏榜（基准分 {sget('SEASON_START_CHIPS')}+兑换底分）", "━" * 18]
                    for i, (u, val) in enumerate(day_standings[:RANK_PAGE_SIZE], 1):
                        g = season_games[cid].get(u, 0)
                        tag = "" if g >= sget("SEASON_MIN_GAMES") else f"（{g}局·未达标）"
                        lines.append(rank_line(i, u, await get_name(app, u, cid=cid, with_title=False), f"：{val:+d}｜{g}局{tag}"))
                    await safe_send_long(app.bot, cid, "\n".join(lines))
            save_data()
        except Exception:
            logger.exception("leaderboard_scheduler 本轮异常（已吞并继续，下个周期重试）")


def cmd_conflicts():
    """触发词冲突体检：同一个触发词被多个命令占用时，后注册者会顶掉前者（表现为某命令莫名失效）。

    返回 [(触发词, [命令函数, ...])]，按触发词排序；无冲突返回空列表。命令管理页顶部展示。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BASE_CMD_ALIASES = hub.BASE_CMD_ALIASES
    CMD_ALIAS_OVERRIDES = hub.CMD_ALIAS_OVERRIDES
    _HANDLERS_BY_NAME = hub._HANDLERS_BY_NAME
    parsed = {}
    for fn_name, alias_str in CMD_ALIAS_OVERRIDES.items():
        if fn_name not in _HANDLERS_BY_NAME:
            continue
        aliases = [a.strip() for a in str(alias_str).replace("，", ",").split(",") if a.strip()]
        if aliases:
            parsed[fn_name] = aliases
    owner = {}
    for alias, fn in BASE_CMD_ALIASES.items():
        if fn.__name__ in parsed:
            continue                      # 被覆盖的命令其出厂触发词已整体失效
        owner.setdefault(alias, set()).add(fn.__name__)
    for fn_name, aliases in parsed.items():
        for a in aliases:
            owner.setdefault(a, set()).add(fn_name)
    return sorted((a, sorted(fns)) for a, fns in owner.items() if len(fns) > 1)
