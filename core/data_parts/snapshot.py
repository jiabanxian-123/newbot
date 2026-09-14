# -*- coding: utf-8 -*-
"""force_save_now 的「快照构造」实现 —— 2026-09-13 从 core/data.py 拆出。

只做搬运，未改任何逻辑：把内存里的全部数据域序列化成待写盘的 dict。
落盘（makedirs / save_parts / 锁）仍留在 core/data.py 的 force_save_now。

⚠️ **本包禁止** import core.data（循环导入）。依赖 bot 命名空间一律走
   `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
"""
from core import hub


def build_data_snapshot():
    """把内存里的全部数据域序列化成待写盘的 dict（纯构造，不落盘）。

    2026-09-13 从 force_save_now 搬出，只搬不改。别名在此重新延迟绑定 hub，
    与原来在 force_save_now 里逐次读 hub 的语义完全一致（测试补丁实时穿透）。
    """
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    BOT_ADMINS = hub.BOT_ADMINS
    SETTINGS_SNAPSHOT = hub.SETTINGS_SNAPSHOT
    settings_changes = hub.settings_changes
    _pending_deletes = hub._pending_deletes
    admin_logs = hub.admin_logs
    announce_last_date = hub.announce_last_date
    blackjack_history = hub.blackjack_history
    blackjack_profit_by_date = hub.blackjack_profit_by_date
    bot_added_by = hub.bot_added_by
    buy_orders = hub.buy_orders
    buy_packages = hub.buy_packages
    champions_history = hub.champions_history
    chat_earn_daily = hub.chat_earn_daily
    chat_rules = hub.chat_rules
    daily_emergency_used = hub.daily_emergency_used
    game_chips = hub.game_chips
    game_flows = hub.game_flows
    games_played = hub.games_played
    hourly_race_enabled = hub.hourly_race_enabled
    inherit_daily = hub.inherit_daily
    invite_confirmed = hub.invite_confirmed
    invite_daily = hub.invite_daily
    invite_debug = hub.invite_debug
    invite_links = hub.invite_links
    invite_pending = hub.invite_pending
    invite_records = hub.invite_records
    jinhua_profit_by_date = hub.jinhua_profit_by_date
    join_requests = hub.join_requests
    join_verify_pending = hub.join_verify_pending
    last_business_date = hub.last_business_date
    leave_records = hub.leave_records
    ledger = hub.ledger
    lotteries = hub.lotteries
    lurker_checked = hub.lurker_checked
    mall_orders = hub.mall_orders
    member_joined_at = hub.member_joined_at
    member_profiles = hub.member_profiles
    newbie_rewarded = hub.newbie_rewarded
    observe_checked = hub.observe_checked
    tag_synced = hub.tag_synced
    pending_game_bets = hub.pending_game_bets
    poker_profit_by_date = hub.poker_profit_by_date
    race_daily_stats = hub.race_daily_stats
    race_history = hub.race_history
    race_jackpot = hub.race_jackpot
    race_profit_by_date = hub.race_profit_by_date
    race_subsidy_by_day = hub.race_subsidy_by_day
    redeem_counts = hub.redeem_counts
    redeem_goods = hub.redeem_goods
    redeem_orders = hub.redeem_orders
    rp_packets = hub.rp_packets
    season_active = hub.season_active
    season_end_ts = hub.season_end_ts
    season_exchange_bonus = hub.season_exchange_bonus
    season_exchange_daily = hub.season_exchange_daily
    season_games = hub.season_games
    season_id = hub.season_id
    season_joined = hub.season_joined
    season_name = hub.season_name
    season_points = hub.season_points
    season_profit_by_date = hub.season_profit_by_date
    season_rebuy = hub.season_rebuy
    season_start_ts = hub.season_start_ts
    sget = hub.sget
    sign_data = hub.sign_data
    title_equipped = hub.title_equipped
    title_expiry = hub.title_expiry
    total_earned = hub.total_earned
    user_first_seen = hub.user_first_seen
    user_names = hub.user_names
    user_titles = hub.user_titles
    warn_counts = hub.warn_counts
    whitelist = hub.whitelist
    return {
        "game_chips": {str(cid): dict(users) for cid, users in game_chips.items()},
        "poker_profit_by_date": {date: {str(cid): dict(users) for cid, users in chats.items()} for date, chats in poker_profit_by_date.items()},
        "race_profit_by_date": {date: {str(cid): dict(users) for cid, users in chats.items()} for date, chats in race_profit_by_date.items()},
        "blackjack_profit_by_date": {date: {str(cid): dict(users) for cid, users in chats.items()} for date, chats in blackjack_profit_by_date.items()},
        "jinhua_profit_by_date": {date: {str(cid): dict(users) for cid, users in chats.items()} for date, chats in jinhua_profit_by_date.items()},
        "authorized_groups": list(AUTHORIZED_GROUPS),
        "race_subsidy_by_day": {date: {str(cid): amount for cid, amount in chats.items()} for date, chats in race_subsidy_by_day.items()},
        "bot_admins": list(BOT_ADMINS),
        "blacklist": list(BLACKLISTED_USERS),
        "race_jackpot": {str(cid): value for cid, value in race_jackpot.items()},
        "hourly_race_enabled": {str(cid): value for cid, value in hourly_race_enabled.items()},
        "race_history": {str(cid): value[-10:] for cid, value in race_history.items()},
        "blackjack_history": {str(cid): value[-10:] for cid, value in blackjack_history.items()},
        "race_daily_stats": {str(cid): value for cid, value in race_daily_stats.items()},
        "daily_emergency_used": {str(cid): {str(uid): used for uid, used in users.items()} for cid, users in daily_emergency_used.items()},
        "last_business_date": last_business_date,
        "pending_game_bets": {str(cid): {str(uid): val for uid, val in users.items()} for cid, users in pending_game_bets.items()},
        "season_active": season_active,
        "season_id": season_id,
        "season_name": season_name,
        "season_start_ts": season_start_ts,
        "season_end_ts": season_end_ts,
        "season_points": {str(cid): dict(users) for cid, users in season_points.items()},
        "season_games": {str(cid): dict(users) for cid, users in season_games.items()},
        "season_joined": {str(cid): list(users) for cid, users in season_joined.items()},
        "season_rebuy": {str(cid): dict(users) for cid, users in season_rebuy.items()},
        "season_profit_by_date": {date: {str(cid): dict(users) for cid, users in chats.items()} for date, chats in season_profit_by_date.items()},
        "season_exchange_daily": {date: {str(cid): {str(uid): int(v) for uid, v in users.items()} for cid, users in chats.items()} for date, chats in season_exchange_daily.items()},
        "season_exchange_bonus": {str(cid): dict(users) for cid, users in season_exchange_bonus.items()},
        # 累计积分账本 + 累计局数（等级按累计积分算，消费不掉级；局数用于归零门槛）
        "total_earned": {str(cid): {str(uid): int(v) for uid, v in users.items()} for cid, users in total_earned.items()},
        "games_played": {str(cid): {str(uid): int(v) for uid, v in users.items()} for cid, users in games_played.items()},
        "user_titles": {str(uid): sorted(t) for uid, t in user_titles.items()},
        "title_expiry": {str(uid): {t: int(exp) for t, exp in ts.items()} for uid, ts in title_expiry.items()},
        "title_equipped": {str(uid): t for uid, t in title_equipped.items()},
        "champions_history": champions_history,
        "user_names": {str(uid): n for uid, n in user_names.items()},
        "sign_data": {str(cid): {str(uid): dict(v) for uid, v in users.items()} for cid, users in sign_data.items()},
        "chat_earn_daily": {date: {str(cid): {str(uid): v for uid, v in users.items()} for cid, users in chats.items()} for date, chats in chat_earn_daily.items()},
        "newbie_rewarded": {k: 1 for k in newbie_rewarded},
        "invite_daily": {date: {str(cid): {str(u): dict(v) for u, v in us.items()}
                                for cid, us in cs.items()} for date, cs in invite_daily.items()},
        "mall_orders": mall_orders[-200:],
        "buy_orders": {oid: dict(o) for oid, o in buy_orders.items()},
        "redeem_counts": {str(uid): int(v) for uid, v in redeem_counts.items()},
        "redeem_orders": redeem_orders[-500:],
        "game_flows": game_flows[-2000:],
        "invite_records": {k: dict(v) for k, v in invite_records.items() if isinstance(v, dict)},
        "invite_pending": {k: v for k, v in invite_pending.items()},  # 待归因：容器重启也不丢
        "invite_confirmed": {k: int(v) for k, v in invite_confirmed.items()},  # deep-link/主动问 锁定的归因
        "join_verify_pending": {k: dict(v) for k, v in join_verify_pending.items() if isinstance(v, dict)},
        "observe_checked": sorted(observe_checked),
        "lurker_checked": sorted(lurker_checked),
        # 成员标签同步进度（断点续传）：cid:uid -> 已同步进去的标签值。
        # 存"值"而不只是"已同步过"：等级名改过以后标记会自动失效并重新同步。
        "tag_synced": {k: v for k, v in tag_synced.items()},
        # 入群时间表：观察期巡检 + 潜水号清理的唯一数据源。
        # 此前没持久化，Railway 每次重部署都清零 → 两个巡检永远扫不到人（开关开了也没用）。
        "member_joined_at": {str(cid): {str(uid): float(t) for uid, t in users.items()}
                             for cid, users in member_joined_at.items()},
        "announce_last_date": announce_last_date,
        "invite_debug": {str(cid): list(v) for cid, v in invite_debug.items()},
        "invite_links": {str(cid): {str(uid): dict(v) for uid, v in users.items()}
                         for cid, users in invite_links.items()},
        "warn_counts": {str(cid): {str(uid): int(v) for uid, v in users.items()} for cid, users in warn_counts.items()},
        "member_profiles": {str(cid): {str(uid): dict(v) for uid, v in users.items()} for cid, users in member_profiles.items()},
        "whitelist": {str(cid): sorted(users) for cid, users in whitelist.items()},
        "leave_records": {str(cid): v[-100:] for cid, v in leave_records.items()},
        # 谁把机器人拉进群：bot 被拉进/踢出的溯源（普通群也能抓到，靠 my_chat_member 事件）
        "bot_added_by": {str(cid): {"by": int(v.get("by", 0) or 0), "by_name": v.get("by_name", ""),
                                   "ts": v.get("ts", ""), "title": v.get("title", ""),
                                   "left_ts": v.get("left_ts", "")}
                       for cid, v in bot_added_by.items() if isinstance(v, dict)},
        "join_requests": {str(cid): v[-100:] for cid, v in join_requests.items()},
        "admin_logs": admin_logs[-300:],
        "settings_changes": settings_changes[-300:],
        "ledger": ledger[-5000:],
        "inherit_daily": {date: {str(cid): {str(uid): v for uid, v in users.items()} for cid, users in cids.items()} for date, cids in inherit_daily.items()},
        "user_first_seen": {str(uid): ts for uid, ts in user_first_seen.items()},
        # 设置快照内嵌进数据：跟着备份/恢复一起走，容器重建后设置不回退
        "_settings": dict(SETTINGS_SNAPSHOT, chat_rules=list(chat_rules),
                          buy_packages=list(buy_packages),
                          point_levels=list(sget("POINT_LEVELS")), mall_items=list(sget("MALL_ITEMS")),
                          redeem_goods=list(redeem_goods)),
        # 群组抽奖：每群活动（含已结束的，方便历史展示）
        "_lotteries": {str(cid): {k: v for k, v in lo.items() if k != "msg_id"}
                       for cid, lo in lotteries.items()},
        # 进行中的红包（lock 不序列化）：不持久化的话重启后未领完的积分凭空消失
        "rp_packets": {pid: {k: v for k, v in p.items() if k != "lock"}
                       for pid, p in rp_packets.items()},
        # 待删消息队列：重启后重放，游戏面板不再因重部署而永久残留
        "pending_deletes": [list(q) for q in _pending_deletes[-2000:]],
    }
