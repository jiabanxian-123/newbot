# -*- coding: utf-8 -*-
"""feature/season —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from collections import defaultdict
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
import asyncio
import html

def _season_rule(game, daily_val, season_val):
    """赛季独立参数取值：赛季局且赛季参数非 0 时用赛季值，否则沿用日常德州的值。

    约定 SEASON_* = 0 表示「沿用日常」，这样新增参数不需要迁移旧设置。
    盲注/前注本身允许为 0（不设），0 与「沿用日常」语义冲突，
    故盲注类单独走 _season_blind。
    """
    return season_val if (getattr(game, "season", False) and season_val) else daily_val


def _season_blind(game, daily_val, season_val):
    """盲注/前注专用：**三个值各自独立**判定（2026-09-14 用户拍板 S1A）。

    赛季局且**该项**赛季值非 0 → 用赛季值；否则该项沿用日常。

    ⚠️ 与旧版的行为差异（这是一次**有意的修正**，不是重构）：
      旧版口径是「三个盲注里**任一 > 0** → 三个整体按赛季走」。于是只填
      「赛季小盲 = 10」会让大盲 / 前注**一起变成 0** —— 而日常前注默认是 200，
      赛季局的前注就这么**静默丢了**（不报错、不打日志）。
      各自独立之后，没填的项各自沿用日常，不再被"连坐"清零。

    ⚠️ 代价（已向用户说明）：0 现在统一表示「沿用日常」，所以
      **赛季局无法把前注单独设成 0**；要「赛季无前注」，得先把日常前注也设成 0。
    """
    return season_val if (getattr(game, "season", False) and season_val) else daily_val


# ── 赛季「参数表」（2026-09-14，桌面清单 C）──────────────────────────────
# 「日常 / 赛季」两套值的**唯一登记处**：加一个赛季专用参数 = 表里加一行。
# 加 / 改赛季参数只改这一处，不会漏（以前要在德州的 6 个属性里各改一遍）。
#   (键,              中文名,     日常设置键,           赛季设置键,               口径)
SEASON_PARAM_TABLE = (
    ("fixed_min_raise",   "最低加注", "FIXED_MIN_RAISE",   "SEASON_FIXED_MIN_RAISE",   "rule"),
    ("turn_timeout",      "思考秒数", "TURN_TIMEOUT",      "SEASON_TURN_TIMEOUT",      "rule"),
    ("room_wait_timeout", "房间等待", "ROOM_WAIT_TIMEOUT", "SEASON_ROOM_WAIT_TIMEOUT", "rule"),
    ("ante",              "前注",     "ANTE",              "SEASON_ANTE",              "blind"),
    ("small_blind",       "小盲",     "SMALL_BLIND",       "SEASON_SMALL_BLIND",       "blind"),
    ("big_blind",         "大盲",     "BIG_BLIND",         "SEASON_BIG_BLIND",         "blind"),
)


def _season_param(game, key):
    """赛季参数取值**唯一入口**：按 `SEASON_PARAM_TABLE` 查表 → 交给 `_season_rule` / `_season_blind`。

    口径（与重构前逐字一致）：
      · "rule"  → `_season_rule`：赛季局且赛季值非 0 用赛季值，否则日常值；
      · "blind" → `_season_blind`：**三个盲注各自独立**判定（S1A）。
    查不到 key 直接抛 KeyError（响亮失败，不静默返回 0）。
    """
    for k, _label, daily_key, season_key, kind in SEASON_PARAM_TABLE:
        if k == key:
            fn = _season_blind if kind == "blind" else _season_rule
            return fn(game, hub.sget(daily_key), hub.sget(season_key))
    raise KeyError(f"未知赛季参数键：{key}")


# 到点后最多再等多久（秒）就强制结算 —— 防某局卡死导致「永远结算不了」（S5C）。
SEASON_SETTLE_GRACE_SECONDS = 600


def _season_has_active_games():
    """是否存在「进行中的赛季局」（赛季 + 非 waiting）。

    用于 S5C：到 `season_end_ts` 后先不结算，等这些牌局打完；调度器再用本函数判定。
    只看「正在打」的局；仅等待中的房间（waiting）不算（还没真正开始，不该拖着结算）。
    """
    for g in list(hub.active_poker_games.values()):
        if getattr(g, "season", False) and getattr(g, "phase", "waiting") != "waiting":
            return True
    return False


async def start_season(cid, name="", forced=False):
    """开启新赛季：给所有已报名者发放起始分。返回 (ok, msg)。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _season_base = hub._season_base
    now_bj = hub.now_bj
    save_data = hub.save_data
    season_games = hub.season_games
    season_joined = hub.season_joined
    season_points = hub.season_points
    season_profit_by_date = hub.season_profit_by_date
    season_rebuy = hub.season_rebuy
    sget = hub.sget

    if hub.season_active:
        return True, "already_active"  # 幂等：已在赛季中，不重复初始化（防自动开赛竞态双击）
    joined = season_joined.get(cid, set())
    if not forced and len(joined) < sget("SEASON_MIN_PLAYERS"):
        return False, f"需满 {sget('SEASON_MIN_PLAYERS')} 人报名才能开赛（当前 {len(joined)} 人）"
    if forced and not joined:
        return False, "尚无任何人报名，无法强制开赛"
    hub.set("season_active", True)
    hub.set("season_id", now_bj().strftime("%Y%m%d"))
    hub.set("season_name", name or f"第{hub.season_id}赛季")
    hub.set("season_start_ts", int(now_bj().timestamp()))
    hub.set("season_end_ts", hub.season_start_ts + sget("SEASON_DAYS") * 86400)
    season_points[cid] = defaultdict(int)
    season_games[cid] = defaultdict(int)
    season_rebuy[cid] = defaultdict(int)
    for uid in joined:
        # 赛前兑换的赛季分带进新赛季：基准分 = 起始分 + 该玩家已兑换分
        season_points[cid][uid] = _season_base(cid, uid)
        season_games[cid][uid] = 0
        season_rebuy[cid][uid] = 0
    season_profit_by_date.pop(cid, None)
    save_data()
    return True, None


async def season_settle(app, manual=False):
    """赛季结算：按当前赛季分排名（过滤未达最少局数者），推榜后重置。

    ★ 2026-09-14 修「结算失败永不重试」（桌面清单 S3A+B）：
      旧版一进来就 `season_active=False`（防重入）。若随后任一步抛异常，异常被 60 秒轮询
      吞掉只记日志 —— 但赛季已标记结束 ⇒ **永不重试**：赌神没加冕、数据没清，只能人工收拾。
      新口径：
        · season_settling 防重入（并发/重叠调用直接返回）；
        · 结算**成功**后才真正关闭赛季并清数据；失败**保留** active → 调度器 60 秒后重试；
        · season_settled_cids 记录本轮已推过榜的群，重试跳过 → 不重复推榜/加冕；
        · season_settling 在 finally 复位 —— 任何异常都不会把结算永久卡死。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    active_poker_games = hub.active_poker_games
    champions_history = hub.champions_history
    get_name = hub.get_name
    rank_marker = hub.rank_marker
    safe_send = hub.safe_send
    safe_send_long = hub.safe_send_long
    save_data = hub.save_data
    schedule_notice_delete = hub.schedule_notice_delete
    season_exchange_bonus = hub.season_exchange_bonus
    season_games = hub.season_games
    season_joined = hub.season_joined
    season_lobby_msg = hub.season_lobby_msg
    season_points = hub.season_points
    season_profit_by_date = hub.season_profit_by_date
    season_rebuy = hub.season_rebuy
    season_total_profit = hub.season_total_profit
    sget = hub.sget
    user_link = hub.user_link
    user_titles = hub.user_titles

    if not hub.season_active:
        return
    if hub.season_settling:
        return  # 已有一次结算在进行中：防并发/重叠重入，直接返回（不做任何事）
    hub.set("season_settling", True)
    try:
        # 快照（外层+内层均拷贝）：防止结算过程中并发的 showdown 修改 season_points 导致 RuntimeError
        season_snapshot = {cid: dict(users) for cid, users in season_points.items()}
        pushed = hub.season_settled_cids
        for cid, users in season_snapshot.items():
            if cid in pushed:
                continue  # 上次推了一半失败：这个群已推过最终榜，跳过（防重复推榜/重复加冕）
            standings = sorted(users.items(), key=lambda x: (-hub.season_total_profit(cid, x[0]), x[0]))
            eligible = [(uid, val) for uid, val in standings if uid >= 0 and season_games[cid].get(uid, 0) >= sget("SEASON_MIN_GAMES")]
            lines = [f"🏆 第{hub.season_id}赛季最终榜（{hub.season_name or '赛季比赛'}）", "━" * 18]
            if not eligible:
                lines.append("本赛季无达标玩家，赌神称号保留在任者。")
            for i, (uid, val) in enumerate(eligible[:50], 1):
                g = season_games[cid].get(uid, 0)
                marker = "👑" if (i == 1 and uid in user_titles and TITLE_GAMBLING_GOD in user_titles[uid]) else rank_marker(i)
                lines.append(f"{marker} {user_link(uid, await get_name(app, uid, cid=cid, with_title=False))}：总{season_total_profit(cid, uid):+d}｜{g}局")
            lines.extend(["", "⚠️ 结算时刻进行中的牌局不计入本赛季。", "🎁 奖励由管理员另行发放。"])
            await safe_send_long(app.bot, cid, "\n".join(lines))
            # 自动加冕本赛季赌神（全局唯一，覆盖上任）
            if eligible:
                champ_uid = eligible[0][0]
                champ_name = await get_name(app, champ_uid, cid=cid, with_title=False)
                streak = 1
                if champions_history and champions_history[-1]["uid"] == champ_uid:
                    streak = champions_history[-1].get("streak", 1) + 1
                # 移除旧赌神称号（赌神全局唯一），保留其余称号；再给新冠军加冕
                for _u in list(user_titles.keys()):
                    user_titles[_u].discard(TITLE_GAMBLING_GOD)
                    if not user_titles[_u]:
                        del user_titles[_u]
                user_titles.setdefault(champ_uid, set()).add(TITLE_GAMBLING_GOD)
                champions_history.append({"season_id": hub.season_id, "uid": champ_uid, "name": champ_name, "score": season_total_profit(cid, champ_uid), "streak": streak})
                crown = f"👑 恭喜 {await get_name(app, champ_uid, cid=cid, with_title=False)} 加冕本赛季 🎰赌神" + (f"（{streak}连冠！）" if streak > 1 else "！")
                try:
                    await safe_send(app.bot, cid, crown)
                except Exception:
                    pass
            # 推完一个群就记进「已推集合」：若后面某群抛异常，重试时不会重复推这个群
            pushed.add(cid)
            hub.set("season_settled_cids", pushed)
        # 进行中的赛季牌局：本手结算不计入排名，提前告知玩家（赛季关闭后其 settle_poker 仅派奖不写回）
        for g in list(active_poker_games.values()):
            if getattr(g, "season", False) and getattr(g, "phase", "waiting") != "waiting" and g.chat_id in season_points:
                try:
                    schedule_notice_delete(app, g.chat_id, await safe_send(app.bot, g.chat_id, "⏰ 赛季已结束，本手牌结算不计入赛季排名（仍正常派奖）。"))
                except Exception:
                    pass
        # ── 全部推完，才真正关闭赛季并清空数据（上面任一步抛异常都会跳过这里 → 交给调度器重试）──
        hub.set("season_active", False)
        hub.set("season_id", None)
        hub.set("season_name", "")
        hub.set("season_start_ts", 0)
        hub.set("season_end_ts", 0)
        season_points.clear(); season_games.clear(); season_joined.clear(); season_rebuy.clear(); season_profit_by_date.clear()
        season_exchange_bonus.clear()  # 兑换底分随赛季结束清零，下赛季重新累计
        season_lobby_msg.clear()  # 大厅看板为 UI 态，结算后清空，下赛季重新发
        hub.set("season_settled_cids", set())  # 复位，供下赛季
        save_data()
    finally:
        # 无论成功/失败都复位：失败时 season_active 仍为 True 会被调度器重试；这里只保证不永久卡死
        hub.set("season_settling", False)


def _season_base(cid, uid):
    """当日基准分 = 起始分 + 本赛季累计兑换分。
    兑换分是「额外底分」：每日 0 点重置时保留，且不计入任何盈亏榜（否则花钱买的分会虚增名次）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    season_exchange_bonus = hub.season_exchange_bonus
    sget = hub.sget
    return sget("SEASON_START_CHIPS") + season_exchange_bonus.get(cid, {}).get(uid, 0)


def season_daily_refresh(day_key, cids, protected=None):
    """赛季每日归位：每人分数重置为「起始分+兑换底分」，当日盈亏（当前分-基准分）记入 day_key。

    protected = {(cid, uid)}：进行中的赛季局跳过本次归位，等其结算时在 settle_poker 内补记，
    避免打断进行中的牌局。返回实际归位人数（便于日志/测试）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _season_base = hub._season_base
    season_points = hub.season_points
    season_profit_by_date = hub.season_profit_by_date
    protected = protected or set()
    touched = 0
    for cid in cids:
        users = season_points.get(cid)
        if not users: continue
        for uid in list(users.keys()):
            if (cid, uid) in protected: continue
            base = _season_base(cid, uid)
            day_profit = users[uid] - base
            if day_profit:
                season_profit_by_date[day_key][cid][uid] += day_profit
            users[uid] = base
            touched += 1
    return touched


def season_total_profit(cid, uid):
    """赛季总盈亏 = 各日已结算盈亏之和 + 当前未结算当日盈亏（当前分 - 基准分）。
    基准分含兑换底分，故兑换来的分不会虚增排名；仅对已报名玩家有意义。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _season_base = hub._season_base
    season_points = hub.season_points
    season_profit_by_date = hub.season_profit_by_date
    total = 0
    for d in season_profit_by_date:
        total += season_profit_by_date[d].get(cid, {}).get(uid, 0)
    pts = season_points.get(cid, {}).get(uid)
    if pts is not None:
        total += pts - _season_base(cid, uid)
    return total


async def season_standings_lines(app, cid, uid=None):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    _season_base = hub._season_base
    get_name = hub.get_name
    now_bj = hub.now_bj
    rank_marker = hub.rank_marker
    season_end_ts = hub.season_end_ts
    season_exchange_bonus = hub.season_exchange_bonus
    season_games = hub.season_games
    season_id = hub.season_id
    season_name = hub.season_name
    season_points = hub.season_points
    season_total_profit = hub.season_total_profit
    sget = hub.sget
    user_link = hub.user_link
    user_titles = hub.user_titles
    users = season_points.get(cid, {})
    standings = sorted(users.items(), key=lambda x: (-hub.season_total_profit(cid, x[0]), x[0]))
    remain = max(0, int((season_end_ts - now_bj().timestamp()) / 86400))
    lines = [f"🏆 第{season_id}赛季榜（{season_name or '赛季比赛'}）",
             f"⏳ 剩余约 {remain} 天｜上榜需≥{sget('SEASON_MIN_GAMES')}局", "━" * 18]
    if not standings:
        lines.append("暂无数据")
    for i, (u, val) in enumerate(standings[:50], 1):
        g = season_games[cid].get(u, 0)
        tag = "" if g >= sget("SEASON_MIN_GAMES") else f"（{g}局·未达标）"
        marker = "👑" if (i == 1 and u in user_titles and TITLE_GAMBLING_GOD in user_titles[u]) else rank_marker(i)
        bonus = season_exchange_bonus.get(cid, {}).get(u, 0)
        btag = f"｜底分{bonus}" if bonus else ""
        lines.append(f"{marker} {user_link(u, await get_name(app, u, cid=cid, with_title=False))}：总{season_total_profit(cid, u):+d}｜当日{val - _season_base(cid, u):+d}｜{g}局{btag}{tag}")
    # 个人排名行：请求者不在前 50 时，单独补一行真实名次，避免大群看不到自己
    if uid is not None and uid in users:
        full_rank = next((i for i, (u, _) in enumerate(standings, 1) if u == uid), None)
        if full_rank is not None and full_rank > 50:
            g = season_games[cid].get(uid, 0)
            tag = "" if g >= sget("SEASON_MIN_GAMES") else f"（{g}局·未达标）"
            lines.append(f"…（仅显示前 50，你当前第 {full_rank} 名：总{season_total_profit(cid, uid):+d}分{tag}）")
    return lines


async def season_signup(app, cid, uid):
    """报名 / 赛中补报名。处理自动开赛。返回 (ok, key)。key∈joining/started/joined_active。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _season_base = hub._season_base
    save_data = hub.save_data
    season_active = hub.season_active
    season_games = hub.season_games
    season_joined = hub.season_joined
    season_points = hub.season_points
    season_rebuy = hub.season_rebuy
    sget = hub.sget
    start_season = hub.start_season
    if season_active:
        season_joined.setdefault(cid, set()).add(uid)
        if uid not in season_points.get(cid, {}):
            season_points[cid][uid] = _season_base(cid, uid)   # 含赛前兑换的底分
            season_games[cid][uid] = 0
            season_rebuy[cid][uid] = 0
        save_data()
        return True, "joined_active"
    season_joined.setdefault(cid, set()).add(uid)
    save_data()
    n = len(season_joined[cid])
    if n >= sget("SEASON_MIN_PLAYERS"):
        ok, msg = await start_season(cid)
        # 只有真正“首次开赛”的那次才回 started；并发点按钮导致的二次进入回 joined_active
        return True, "started" if (ok and msg != "already_active") else "joined_active"
    return True, "joining"


async def season_lobby_content(app, cid):
    """返回 (text, reply_markup) 赛季大厅看板，按赛季状态切换。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    now_bj = hub.now_bj
    season_active = hub.season_active
    season_end_ts = hub.season_end_ts
    season_id = hub.season_id
    season_joined = hub.season_joined
    season_name = hub.season_name
    sget = hub.sget
    if not season_active:
        joined = list(season_joined.get(cid, set()))
        n = len(joined)
        # 列出已报名昵称（最多 15 个，避免刷屏 + 控制 get_chat API 调用量）
        names = [await get_name(app, u) for u in joined[:15]]
        names_text = ("、".join(names) + (f" 等 {n} 人" if n > 15 else "")) if n else "（暂无）"
        text = (f"🏆 <b>赛季报名大厅</b>\n\n"
                f"当前报名：<b>{n}/{sget('SEASON_MIN_PLAYERS')}</b> 人\n"
                f"满 {sget('SEASON_MIN_PLAYERS')} 人自动开赛，每人 {sget('SEASON_START_CHIPS')} 分，周期 {sget('SEASON_DAYS')} 天。\n"
                f"已报名：{names_text}\n"
                f"点下面按钮报名，或用 /赛季报名 也能一键报名。")
        _ex_row = [[InlineKeyboardButton("💱 积分兑换赛季分", callback_data="season_exchange_info")]] if sget("RANKED_EXCHANGE_ENABLED") else []
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📝 报名参赛（{n}/{sget('SEASON_MIN_PLAYERS')}）", callback_data="season_signup")],
            *_ex_row,
            [InlineKeyboardButton("❌ 关闭看板", callback_data="season_lobby_close")],
        ])
    else:
        remain = max(0, int((season_end_ts - now_bj().timestamp()) / 86400))
        sn = html.escape(str(season_name or '赛季比赛'))  # 防止管理员自定义赛季名含 < 或 & 触发 BadRequest
        text = (f"🏆 <b>第{season_id}赛季「{sn}」进行中</b>\n\n"
                f"⏳ 剩余约 {remain} 天｜上榜需≥{sget('SEASON_MIN_GAMES')}局\n"
                f"用 /赛季 开局入座；中途想加入点下面按钮。")
        _ex_row = [[InlineKeyboardButton("💱 积分兑换赛季分", callback_data="season_exchange_info")]] if sget("RANKED_EXCHANGE_ENABLED") else []
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 中途报名加入", callback_data="season_signup")],
            [InlineKeyboardButton("📊 看赛季榜", callback_data="season_rank_btn")],
            *_ex_row,
            [InlineKeyboardButton("❌ 关闭看板", callback_data="season_lobby_close")],
        ])
    return text, markup


async def render_season_lobby(app, cid):
    """编辑已有大厅看板，没有则新发。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    safe_edit = hub.safe_edit
    safe_send = hub.safe_send
    season_lobby_content = hub.season_lobby_content
    season_lobby_msg = hub.season_lobby_msg
    text, markup = await season_lobby_content(app, cid)
    mid = season_lobby_msg.get(cid)
    if mid:
        try:
            await safe_edit(app.bot, cid, mid, text, reply_markup=markup, parse_mode="HTML")
            return
        except Exception:
            pass
    msg = await safe_send(app.bot, cid, text, reply_markup=markup, parse_mode="HTML")
    if msg:
        season_lobby_msg[cid] = msg.message_id


async def cmd_season_join(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    need_auth = hub.need_auth
    render_season_lobby = hub.render_season_lobby
    require_group_chat = hub.require_group_chat
    season_id = hub.season_id
    season_joined = hub.season_joined
    season_name = hub.season_name
    season_points = hub.season_points
    season_signup = hub.season_signup
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    if not await require_group_chat(update, "德州赛季", "赛季", context): return
    cid, uid = update.effective_chat.id, update.effective_user.id
    ok, key = await season_signup(context.application, cid, uid)
    await render_season_lobby(context.application, cid)
    if key == "started":
        await send_reply(update, context, f"🏆 报名满 {sget('SEASON_MIN_PLAYERS')} 人，第{season_id}赛季「{season_name or '赛季比赛'}」开始！每人 {sget('SEASON_START_CHIPS')} 分，周期 {sget('SEASON_DAYS')} 天。用 /赛季 开局。")
    elif key == "joined_active":
        await send_reply(update, context, f"✅ 已加入进行中的赛季（需满 {sget('SEASON_MIN_GAMES')} 局才上榜）。当前分 {season_points[cid][uid]}。用 /赛季 开局。")
    else:
        n = len(season_joined[cid])
        await send_reply(update, context, f"✅ 已报名本赛季（{n}/{sget('SEASON_MIN_PLAYERS')}）。满 {sget('SEASON_MIN_PLAYERS')} 人自动开赛；也可点群里的大厅看板报名。")


async def cmd_season_start(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_bot_admin = hub.is_bot_admin
    require_group_chat = hub.require_group_chat
    season_active = hub.season_active
    season_id = hub.season_id
    season_name = hub.season_name
    send_reply = hub.send_reply
    sget = hub.sget
    start_season = hub.start_season
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可强制开赛"); return
    if not await require_group_chat(update, "德州赛季", "赛季", context): return
    cid = update.effective_chat.id
    if season_active:
        await send_reply(update, context, "⚠️ 本赛季已在进行中。"); return
    name = " ".join(context.args) if context.args else ""
    ok, msg = await start_season(cid, name, forced=True)
    if ok:
        await send_reply(update, context, f"🏆 第{season_id}赛季「{season_name or '赛季比赛'}」由管理员强制开启！每人 {sget('SEASON_START_CHIPS')} 分，周期 {sget('SEASON_DAYS')} 天。用 /赛季 开局。")
    else:
        await send_reply(update, context, msg)


async def cmd_season_end(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_bot_admin = hub.is_bot_admin
    season_active = hub.season_active
    season_settle = hub.season_settle
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    if not season_active:
        await send_reply(update, context, "⚠️ 当前无进行中的赛季。"); return
    await season_settle(context.application, manual=True)
    await send_reply(update, context, "🏁 赛季已手动结算并重置。")


async def cmd_season_rank(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    need_auth = hub.need_auth
    season_active = hub.season_active
    season_standings_lines = hub.season_standings_lines
    send_reply = hub.send_reply
    if not await need_auth(update, context): return
    cid, uid = update.effective_chat.id, update.effective_user.id
    if not season_active:
        await send_reply(update, context, "⚠️ 当前无进行中的赛季。"); return
    lines = await season_standings_lines(context.application, cid, uid=uid)
    await send_reply(update, context, "\n".join(lines))


async def cmd_season_points(update, context):
    """管理员加减赛季分（正为加，负为减）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _parse_target_amount = hub._parse_target_amount
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    need_auth = hub.need_auth
    save_data = hub.save_data
    season_active = hub.season_active
    season_joined = hub.season_joined
    season_points = hub.season_points
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    if not await need_auth(update, context): return
    try:
        uid, amount = await _parse_target_amount(update, context)
        if amount == 0: raise ValueError
    except (ValueError, IndexError):
        await send_reply(update, context, "用法：/赛季分 用户ID 数量（正加负减），或回复玩家消息后使用 /赛季分 数量"); return
    cid = update.effective_chat.id
    if not season_active and uid not in season_points.get(cid, {}):
        await send_reply(update, context, "⚠️ 该玩家不在当前赛季，且赛季未激活。"); return
    if amount < 0 and season_points.get(cid, {}).get(uid, 0) < -amount:
        await send_reply(update, context, "❌ 该玩家赛季分不足。"); return
    season_points.setdefault(cid, defaultdict(int))[uid] += amount
    season_joined.setdefault(cid, set()).add(uid)
    save_data()
    verb = "增加" if amount > 0 else "扣除"
    await send_reply(update, context, f"✅ 已为 {await get_name(context.application, uid)} {verb} {abs(amount)} 赛季分，当前 {season_points[cid][uid]}。")


async def _season_exchange_execute(context, cid, uid, cost):
    """聊天积分 → 赛季分（**唯一扣款入口**：群内命令与私聊蓝字确认共用，避免两套逻辑漂移）。
    返回 (ok, 提示文本)。比例/开关/每日上限全部读后台设置，改比例无需动代码。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _exchange_rate_text = hub._exchange_rate_text
    business_date = hub.business_date
    force_save_now = hub.force_save_now
    game_chips = hub.game_chips
    ledger_add = hub.ledger_add
    player_is_busy = hub.player_is_busy
    save_data = hub.save_data
    season_exchange_bonus = hub.season_exchange_bonus
    season_exchange_daily = hub.season_exchange_daily
    season_games = hub.season_games
    season_joined = hub.season_joined
    season_points = hub.season_points
    season_rebuy = hub.season_rebuy
    sget = hub.sget
    user_wallet_locks = hub.user_wallet_locks
    if not sget("RANKED_EXCHANGE_ENABLED"):
        return False, "❌ 积分兑换赛季分功能未开启（管理员可在后台「赛季」分组开启）。"
    if cost < sget("RANKED_EXCHANGE_COST"):
        return False, f"❌ 最少兑换 {sget('RANKED_EXCHANGE_COST')} 积分（当前比例 {_exchange_rate_text()}）。"
    gain = cost * sget("RANKED_EXCHANGE_GAIN") // sget("RANKED_EXCHANGE_COST")
    if gain <= 0:
        return False, f"❌ 兑换数量太小，至少能得到 1 赛季分（比例 {_exchange_rate_text()}）。"
    if player_is_busy(cid, uid):
        return False, "⚠️ 你正在游戏中，请先结束再兑换赛季分。"
    # 每日上限：按「消耗的聊天积分」累计，跨天自动重置（键为业务日）
    today = business_date()
    if sget("RANKED_EXCHANGE_DAILY_LIMIT") > 0:
        used = season_exchange_daily[today][cid].get(uid, 0)
        if used + cost > sget("RANKED_EXCHANGE_DAILY_LIMIT"):
            return False, (f"❌ 超出每日兑换上限：今日已兑换 {used} 积分，上限 "
                           f"{sget('RANKED_EXCHANGE_DAILY_LIMIT')}（管理员可在后台调整）。")
    async with user_wallet_locks([uid]):
        if game_chips[cid][uid] < cost:
            return False, f"❌ 聊天积分不足：需要 {cost}，当前 {game_chips[cid][uid]}。"
        game_chips[cid][uid] -= cost
        # 赛季分账本：赛季未开赛也允许先兑换，开赛后报名即可带入（与 /赛季分 同一容器）
        season_points.setdefault(cid, defaultdict(int))[uid] += gain
        # 兑换分记为「额外底分」：每日重置保留、不计入盈亏榜（避免花钱买分虚增排名）
        season_exchange_bonus.setdefault(cid, defaultdict(int))[uid] += gain
        season_joined.setdefault(cid, set()).add(uid)
        if uid not in season_games.get(cid, {}):
            season_games[cid][uid] = 0
            season_rebuy[cid][uid] = 0
        if sget("RANKED_EXCHANGE_DAILY_LIMIT") > 0:
            season_exchange_daily[today][cid][uid] += cost
        ledger_add(cid, uid, 0, cost, "兑换赛季分")  # 资金流台账：聊天积分回收
        save_data()
        await asyncio.to_thread(force_save_now)
    return True, (f"✅ 兑换成功：-{cost} 聊天积分 → +{gain} 赛季分\n"
                  f"💰 聊天积分余额 {game_chips[cid][uid]}｜🏆 当前赛季分 {season_points[cid][uid]}\n"
                  f"（比例 {_exchange_rate_text()}；兑换分算「额外底分」，每日 0 点重置后保留、不计入盈亏榜。用 /赛季 开局入座）")


def _season_exchange_panel(cid, uid):
    """兑换赛季分面板：正文蓝色文本超链接（与商城/兑换同款），点蓝字跳私聊确认。
    档位按后台比例生成 1×/10×/100×（去重、超上限截断）；命令 /游戏积分兑换 数量 仍可自定义直接兑换。
    返回 (text, rows)。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _deep_buy_url = hub._deep_buy_url
    _exchange_rate_text = hub._exchange_rate_text
    business_date = hub.business_date
    game_chips = hub.game_chips
    season_exchange_daily = hub.season_exchange_daily
    season_points = hub.season_points
    sget = hub.sget
    unit_c = max(1, sget("RANKED_EXCHANGE_COST"))
    unit_g = max(1, sget("RANKED_EXCHANGE_GAIN"))
    bal = game_chips[cid][uid]
    lines = ["🏆 <b>积分兑换赛季分</b>", ""]
    lines.append(f"💰 聊天积分：{bal}")
    lines.append(f"🏆 当前赛季分：{season_points[cid][uid]}")
    lines.append(f"📊 兑换比例：{_exchange_rate_text()}")
    if sget("RANKED_EXCHANGE_DAILY_LIMIT") > 0:
        _used = season_exchange_daily[business_date()][cid].get(uid, 0)
        lines.append(f"📅 今日已兑换：{_used}／{sget('RANKED_EXCHANGE_DAILY_LIMIT')} 积分")
    else:
        lines.append("📅 每日不限")
    lines.append("")
    tiers = []
    for _m in (1, 10, 100):
        _c = unit_c * _m
        if _c > 100000000:
            break
        if _c not in tiers:
            tiers.append(_c)
    rows = []
    for _c in tiers:
        _g = _c * unit_g // unit_c
        url = _deep_buy_url("sexch", cid, _c)
        lines.append(f"🟡 <b>{_c} 聊天积分 → {_g} 赛季分</b>")
        if url:
            lines.append(f"└ <a href='{html.escape(str(url), quote=True)}'>立即兑换</a>")
        else:
            # 启动早期/无 bot 用户名 → 拿不到深链，退回群内按钮直兑
            rows.append([InlineKeyboardButton(f"💱 {_c} 积分 → {_g} 赛季分",
                                              callback_data=f"sexch_ask_{_c}")])
        lines.append("")
    lines.append("💡 也可发「/游戏积分兑换 数量」自定义数量直接兑换")
    return "\n".join(lines), rows


async def cmd_season_exchange(update, context):
    """聊天积分兑换赛季分（比例、开关、每日上限均可在后台「赛季」分组调整）。
    无参数 → 发蓝字面板（点链接跳私聊确认）；带数量 → 直接兑换（命令兜底，保留）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _season_exchange_execute = hub._season_exchange_execute
    _season_exchange_panel = hub._season_exchange_panel
    need_auth = hub.need_auth
    require_group_chat = hub.require_group_chat
    safe_send = hub.safe_send
    schedule_delete = hub.schedule_delete
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    if not await require_group_chat(update, "积分兑换赛季分", "游戏积分兑换", context): return
    if not sget("RANKED_EXCHANGE_ENABLED"):
        await send_reply(update, context, "❌ 积分兑换赛季分功能未开启（管理员可在后台「赛季」分组开启）。"); return
    cid, uid = update.effective_chat.id, update.effective_user.id
    args = context.args or []
    if not args or not args[0].isdigit() or int(args[0]) <= 0:
        text, rows = _season_exchange_panel(cid, uid)
        msg = await safe_send(context.bot, cid, text,
                              reply_markup=(InlineKeyboardMarkup(rows) if rows else None))
        _mls = int(sget("MALL_LIST_DELETE_SECONDS") or 0)   # 读时取值：网页改完立即生效
        if msg and _mls > 0:
            schedule_delete(context.application, cid, msg, _mls)
        return
    ok, txt = await _season_exchange_execute(context, cid, uid, int(args[0]))
    await send_reply(update, context, txt)


async def cmd_season_help(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _exchange_rate_text = hub._exchange_rate_text
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    text = (
        "🏆 <b>德州赛季使用说明</b>\n\n"
        "<b>报名 / 开局</b>\n"
        "• /赛季 — 一键报名（静默）并可在开赛后开/入牌桌；未报名会自动补报\n"
        "• /赛季报名 — 报名并弹出群里「报名大厅」看板（满 20 自动开赛）\n"
        "• 大厅看板按钮：📝 报名参赛 / 📝 中途报名加入\n\n"
        "<b>查询</b>\n"
        "• /赛季榜 — 看当前排名（榜尾显示你的名次）\n"
        "• /赌神 — 查看 🎰赌神 称号与历届荣誉墙\n"
        "• 大厅看板按钮：📊 看赛季榜\n\n"
        "<b>积分兑换赛季分</b>\n"
        "• /游戏积分兑换 — 打开兑换面板，点蓝字「立即兑换」跳私聊确认（推荐）\n"
        f"• /游戏积分兑换 数量 — 自定义数量直接兑换（当前比例 {_exchange_rate_text()}）\n"
        f"• 开关：{'已开启' if sget('RANKED_EXCHANGE_ENABLED') else '已关闭'}"
        + (f"｜每人每日上限 {sget('RANKED_EXCHANGE_DAILY_LIMIT')} 积分" if sget("RANKED_EXCHANGE_DAILY_LIMIT") else "｜每日不限")
        + "（管理员可在后台「赛季」分组调整）\n"
        "• 兑换来的分算「额外底分」：每日 0 点重置后保留，但不计入盈亏榜（不影响名次）\n\n"
        "<b>管理员专属</b>\n"
        "• /赛季开赛 [赛季名] — 强制开赛（可自定义名，如 /赛季开赛 赌神大战秋季赛）\n"
        "• /赛季结束 — 提前结算并推最终榜\n\n"
        "<b>自动机制</b>\n"
        "• 每日 23:50 自动推一次赛季榜\n"
        "• 开赛后第 7 天（到点后的首个午夜）自动结算，可能晚最多约 24 小时\n\n"
        "📌 满 20 人开赛；起始 20000 分；输光可应急补分 3×2000；满 5 局才上榜；次日 0 点重置为 20000 分（含已兑换的底分）可继续打。\n"
        "⚙️ 赛季的入座门槛/加注额/思考时间/等待倒计时/盲注/前注可在后台「赛季」分组单独设置，留 0 表示沿用日常德州。\n"
        "⚠️ 群里若中文命令无反应，多为 BotFather 隐私模式拦截，发 /setprivacy → Disable 即可。"
    )
    await send_reply(update, context, text)


async def cmd_season_play(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    PokerGame = hub.PokerGame
    _game_gate = hub._game_gate
    _season_base = hub._season_base
    active_poker_games = hub.active_poker_games
    current_game_mode = hub.current_game_mode
    need_auth = hub.need_auth
    poker_waiting_text = hub.poker_waiting_text
    render_season_lobby = hub.render_season_lobby
    require_group_chat = hub.require_group_chat
    safe_send = hub.safe_send
    save_data = hub.save_data
    season_active = hub.season_active
    season_games = hub.season_games
    season_joined = hub.season_joined
    season_points = hub.season_points
    season_rebuy = hub.season_rebuy
    season_signup = hub.season_signup
    send_reply = hub.send_reply
    sget = hub.sget
    start_wait_timeout = hub.start_wait_timeout
    update_poker_waiting = hub.update_poker_waiting
    if not await need_auth(update, context): return
    if not await _game_gate(update, context, "texas", ranked=True): return
    if not await require_group_chat(update, "德州赛季", "赛季", context): return
    cid, uid = update.effective_chat.id, update.effective_user.id
    if not season_active:
        ok, key = await season_signup(context.application, cid, uid)
        if key == "started":
            await render_season_lobby(context.application, cid)  # 满 20 自动开赛：翻转看板为进行中，继续往下开房
        else:
            # UX2：/赛季 静默报名不弹看板（看板仅在 /赛季报名 或按钮点击时出现，减少刷屏）
            n = len(season_joined[cid])
            await send_reply(update, context, f"✅ 已报名本赛季（{n}/{sget('SEASON_MIN_PLAYERS')}）。满 {sget('SEASON_MIN_PLAYERS')} 人自动开赛；发 /赛季报名 可看报名大厅。")
            return
    # 赛季进行中：开 / 入房间（赛中未报名者自动补报名）
    if uid not in season_joined.get(cid, set()):
        season_joined.setdefault(cid, set()).add(uid)
        if uid not in season_points.get(cid, {}):
            season_points[cid][uid] = _season_base(cid, uid)   # 含赛前兑换的底分
            season_games[cid][uid] = 0
            season_rebuy[cid][uid] = 0
        save_data()
    if season_points[cid][uid] <= 0:
        await send_reply(update, context, "❌ 你的赛季分已用完，等待应急补分或下局。"); return
    if sget("SEASON_MIN_ENTRY_CHIPS") and season_points[cid][uid] < sget("SEASON_MIN_ENTRY_CHIPS"):
        await send_reply(update, context,
                         f"❌ 进入赛季至少需要 {sget('SEASON_MIN_ENTRY_CHIPS')} 赛季分，你当前 {season_points[cid][uid]}。\n"
                         f"可用 /游戏积分兑换 数量 把聊天积分换成赛季分。"); return
    game = active_poker_games.get(cid)
    if game:
        if game.season:
            if game.phase != "waiting": await send_reply(update, context, "当前已有进行中的赛季。"); return
            if game.add(uid):
                await update_poker_waiting(game, context.application); await send_reply(update, context, "已加入当前等待房间。")
            else: await send_reply(update, context, "你已在等待房间中。")
            return
        else:
            await send_reply(update, context, "当前有日常德州房间，请先 /结束 后再开赛季。"); return
    # S5C：本赛季已到结束时间 → 禁止开新局（等进行中的牌局打完，由 season_settle_scheduler 统一结算）
    if hub.season_end_ts and hub.now_bj().timestamp() >= hub.season_end_ts:
        await send_reply(update, context, "⏰ 本赛季已到结束时间，正在等待进行中的牌局打完结算，暂不能开新局。"); return
    game = PokerGame(cid, uid, current_game_mode(), season=True)
    if not game.add(uid):  # 赛季分门槛/报名状态校验失败时不要留下空房间
        await send_reply(update, context, "❌ 无法入座赛季（赛季分不足或未报名）。"); return
    active_poker_games[cid] = game
    msg = await safe_send(context.bot, cid, await poker_waiting_text(game, context.application), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 加入游戏", callback_data="texas_join")], [InlineKeyboardButton("❌ 终止房间", callback_data="texas_end")]]))
    if msg:
        game.game_msg_id = msg.message_id
        await start_wait_timeout(game, context.application)


async def _deep_season_exchange_start(update, context, payload):
    """私聊里收到 /start sexch_<cid>_<cost>：显示「是否兑换 / 积分不足」确认卡片。
    与兑换/商城同一套竞品式流程；真正扣款仍走 _season_exchange_execute（唯一入口）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _exchange_rate_text = hub._exchange_rate_text
    business_date = hub.business_date
    game_chips = hub.game_chips
    is_auth = hub.is_auth
    season_exchange_daily = hub.season_exchange_daily
    season_points = hub.season_points
    send_reply = hub.send_reply
    sget = hub.sget
    if not update.effective_chat or update.effective_chat.type != "private":
        await send_reply(update, context, "⚠️ 请到机器人私聊完成兑换确认。"); return
    try:
        _, cid_s, cost_s = payload.split("_", 2)
        cid, cost = int(cid_s), int(cost_s)
    except (ValueError, AttributeError):
        await send_reply(update, context, "❌ 兑换链接无效，请回群重新打开面板。"); return
    uid = update.effective_user.id
    if not is_auth(cid):
        await send_reply(update, context, "❌ 该群未授权使用本机器人。"); return
    if not sget("RANKED_EXCHANGE_ENABLED"):
        await send_reply(update, context, "❌ 积分兑换赛季分功能未开启（管理员可在后台「赛季」分组开启）。"); return
    unit_c = max(1, sget("RANKED_EXCHANGE_COST"))
    if cost < unit_c:
        await send_reply(update, context, f"❌ 最少兑换 {unit_c} 积分（当前比例 {_exchange_rate_text()}）。"); return
    gain = cost * sget("RANKED_EXCHANGE_GAIN") // unit_c
    if gain <= 0:
        await send_reply(update, context, f"❌ 兑换数量太小，至少能得到 1 赛季分（比例 {_exchange_rate_text()}）。"); return
    bal = game_chips[cid][uid]
    if bal < cost:
        await send_reply(update, context,
                         f"❌ 聊天积分不足：需要 {cost}，当前 {bal}。\n去群聊赢积分后再来兑换吧～")
        return
    if sget("RANKED_EXCHANGE_DAILY_LIMIT") > 0:
        used = season_exchange_daily[business_date()][cid].get(uid, 0)
        if used + cost > sget("RANKED_EXCHANGE_DAILY_LIMIT"):
            await send_reply(update, context,
                             f"❌ 超出每日兑换上限：今日已兑换 {used} 积分，"
                             f"上限 {sget('RANKED_EXCHANGE_DAILY_LIMIT')}（管理员可在后台调整）。")
            return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 确认兑换", callback_data=f"sexch_ok_{cid}_{cost}"),
        InlineKeyboardButton("❌ 取消兑换", callback_data=f"sexch_no_{cid}_{cost}"),
    ]])
    txt = (f"🏆 <b>积分兑换赛季分</b>\n"
           f"━━━━━━━━━━━━━━━\n"
           f"消耗：{cost} 聊天积分\n"
           f"获得：{gain} 赛季分\n"
           f"当前积分：{bal}\n"
           f"当前赛季分：{season_points[cid][uid]}\n"
           f"━━━━━━━━━━━━━━━\n"
           f"是否兑换？")
    await send_reply(update, context, txt, kb=kb, parse_mode="HTML", delete_after=0)


async def season_settle_scheduler(app):
    """独立赛季结算调度：每 60 秒检查一次到点，精确到分钟结算（不再依赖每日 0 点循环，避免最多延迟 ~24h）。

    ★ 2026-09-14（桌面清单 S3A+B / S5C）两处修正：
      · `season_active` / `season_end_ts` **每轮重读**。旧版在协程启动时只读一次 →
        若赛季在调度器启动**之后**才开，`season_active` 陈旧为 False ⇒ 那一局**永远不会结算**。
      · 到点后若仍有进行中的赛季局（`_season_has_active_games`），**先不结算**，等它们打完（S5C）；
        但最多等到 `SEASON_SETTLE_GRACE_SECONDS` 就强制结算 —— 防某局卡死导致「永远结算不了」。
    """
    logger = hub.logger
    while True:
        try:
            if hub.season_active and hub.now_bj().timestamp() >= hub.season_end_ts:
                overdue = hub.now_bj().timestamp() - hub.season_end_ts
                if overdue >= SEASON_SETTLE_GRACE_SECONDS or not _season_has_active_games():
                    await hub.season_settle(app)
        except Exception:
            logger.exception("season_settle_scheduler 本轮异常（已吞并继续，下个周期重试）")
        await asyncio.sleep(60)
