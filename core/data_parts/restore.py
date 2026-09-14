# -*- coding: utf-8 -*-
"""存档恢复的分段实现 —— 2026-09-13 从 core/data.py 的 load_data 拆出。

只做搬运，未改任何逻辑。每个函数负责一块数据域的恢复，签名统一 `(data)`，
data 是读盘得到的存档字典。

⚠️ **调用顺序不能改**：原代码注释写明了依赖，例如
   「红包恢复必须放在 game_chips 恢复/合并之后，否则退款会被 restore_nested 覆盖」。
   顺序由 load_data 里的调用次序保证。

⚠️ **本包禁止** import core.data（循环导入）。依赖 bot 命名空间一律走
   `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
"""
import asyncio
from core import hub


# ★「一次性补偿」的进程内记录。
#   load_data() 里有些动作是**补偿性质**的（把旧格式的余额并入、把过期红包退回去、
#   把没打完的押注退回去）。它们正常启动时只该做一次；但 /restore 会让
#   load_data() 在同一进程里再跑一遍，若再补一次就是**重复发钱**
#   （同一份备份连做两次恢复 → 钱翻倍）。
#   这里按 key 记一笔，同一个 key 在本进程内只放行一次。
_COMPENSATED = set()


def _once(key):
    """本进程内只放行一次；返回 True 表示「这次该做」。"""
    if key in _COMPENSATED:
        return False
    _COMPENSATED.add(key)
    return True



def _restore_chips(data):
    game_chips = hub.game_chips
    logger = hub.logger
    now_bj = hub.now_bj
    poker_profit_by_date = hub.poker_profit_by_date
    restore_nested = hub.restore_nested
    rp_packets = hub.rp_packets
    # 兼容旧存档：group_chips 键迁移为统一积分
    restore_nested(game_chips, data.get("game_chips", data.get("group_chips", {})))
    # 幽灵键清理：game_chips 的键必须是真实群 ID（负数）；
    # 私聊里 cid==uid（正数）经 defaultdict 读会误建「以用户 ID 为群」的幽灵键，
    # 既污染后台群下拉，又会随备份持久化。启动时一次性剔除所有非群键（清历史 + 防复发）。
    _ghost_keys = [c for c in list(game_chips.keys()) if not str(c).startswith("-")]
    for _gk in _ghost_keys:
        game_chips.pop(_gk, None)
    if _ghost_keys:
        logger.warning("已剔除 %d 个非群幽灵键（game_chips）：%s", len(_ghost_keys), _ghost_keys)
    # 统一积分：旧存档的德州专用积分余额一次性并入统一积分（在 game_chips 覆盖恢复之后追加，之后不再单独保存）
    # ★ 用 _once 兜住：同一份备份被执行两次恢复时，这一笔不能并入两次。
    if _once("texas_chips"):
        for cid, users in data.get("texas_chips", {}).items():
            try:
                c = int(cid)
                for uid, v in users.items():
                    try: game_chips[c][int(uid)] += int(v)
                    except (ValueError, TypeError): continue
            except (ValueError, TypeError): continue
    for date, chats in data.get("poker_profit_by_date", {}).items(): restore_nested(poker_profit_by_date[date], chats)
    # 红包恢复：必须放在 game_chips 恢复/合并之后，否则退款会被 restore_nested 覆盖。
    # 未过期红包继续可抢；已过期的把剩余金额退回发包人（重启不再丢钱）。
    try:
        rp_packets.clear()
        _now = now_bj().timestamp()
        for pid, p in (data.get("rp_packets") or {}).items():
            if not isinstance(p, dict) or "cid" not in p or "from" not in p:
                continue
            p["lock"] = asyncio.Lock()
            try:
                _cid, _from = int(p["cid"]), int(p["from"])
            except (ValueError, TypeError):
                continue
            _refund = p.get("left_amt", 0) or 0
            if _now - p.get("ts", 0) > 86400 or p.get("left_n", 0) <= 0:
                # ★ 按红包 id 记一笔：同一个红包在本进程内只退一次。
                #   （重复 /restore 同一份备份时，否则会退第二遍。）
                if _refund > 0 and _once("rp_refund:" + str(pid)):
                    game_chips[_cid][_from] += _refund
                continue  # 已过期/已抢完：退款后不入内存
            rp_packets[pid] = p
    except Exception:
        logger.exception("红包数据恢复失败")


def _restore_profit_and_season(data):
    blackjack_profit_by_date = hub.blackjack_profit_by_date
    games_played = hub.games_played
    jinhua_profit_by_date = hub.jinhua_profit_by_date
    race_profit_by_date = hub.race_profit_by_date
    restore_nested = hub.restore_nested
    season_exchange_bonus = hub.season_exchange_bonus
    season_exchange_daily = hub.season_exchange_daily
    season_games = hub.season_games
    season_joined = hub.season_joined
    season_points = hub.season_points
    season_profit_by_date = hub.season_profit_by_date
    season_rebuy = hub.season_rebuy
    total_earned = hub.total_earned
    for date, chats in data.get("race_profit_by_date", {}).items(): restore_nested(race_profit_by_date[date], chats)
    for date, chats in data.get("blackjack_profit_by_date", {}).items(): restore_nested(blackjack_profit_by_date[date], chats)
    for date, chats in data.get("jinhua_profit_by_date", {}).items(): restore_nested(jinhua_profit_by_date[date], chats)
    # 德州赛季状态恢复
    hub.set("season_active", data.get("season_active", False))
    hub.set("season_id", data.get("season_id"))
    hub.set("season_name", data.get("season_name", ""))
    hub.set("season_start_ts", data.get("season_start_ts", 0))
    hub.set("season_end_ts", data.get("season_end_ts", 0))
    restore_nested(season_points, data.get("season_points", {}))
    restore_nested(season_games, data.get("season_games", {}))
    restore_nested(season_rebuy, data.get("season_rebuy", {}))
    for cid, uids in data.get("season_joined", {}).items():
        season_joined[int(cid)] = set(int(u) for u in uids)
    for date, chats in data.get("season_profit_by_date", {}).items(): restore_nested(season_profit_by_date[date], chats)
    for date, cids in data.get("season_exchange_daily", {}).items():
        for cid, users in cids.items():
            for uid, v in users.items():
                try: season_exchange_daily[str(date)][int(cid)][int(uid)] = int(v)
                except (ValueError, TypeError): continue
    season_exchange_bonus.clear()
    restore_nested(season_exchange_bonus, data.get("season_exchange_bonus", {}))
    # 累计积分账本 + 累计局数：老存档没有这两个键，留空由 _earn_get 用余额兜底（不会掉级）
    total_earned.clear()
    restore_nested(total_earned, data.get("total_earned", {}))
    games_played.clear()
    restore_nested(games_played, data.get("games_played", {}))


def _restore_titles_and_names(data):
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    champions_history = hub.champions_history
    sign_data = hub.sign_data
    title_equipped = hub.title_equipped
    title_expiry = hub.title_expiry
    user_names = hub.user_names
    user_titles = hub.user_titles
    # 赌神称号恢复
    user_titles.clear()
    for uid, t in data.get("user_titles", {}).items():
        # 兼容旧数据格式（单个称号字符串）与新格式（称号列表）
        user_titles[int(uid)] = {t} if isinstance(t, str) else set(t)
    title_expiry.clear()
    for uid, ts in data.get("title_expiry", {}).items():
        title_expiry[int(uid)] = {t: int(exp) for t, exp in ts.items()}
    title_equipped.clear()
    for uid, t in data.get("title_equipped", {}).items():
        title_equipped[int(uid)] = t
    # 数据迁移：赌神图标 🎰→🔱（兼容旧数据，避免已持有玩家丢失称号）
    _OLD_GOD = "🎰赌神"
    for _uid, _titles in list(user_titles.items()):
        if _OLD_GOD in _titles:
            _titles.discard(_OLD_GOD)
            _titles.add(TITLE_GAMBLING_GOD)
    for _uid, _t in list(title_equipped.items()):
        if _t == _OLD_GOD:
            title_equipped[_uid] = TITLE_GAMBLING_GOD
    champions_history.clear()
    champions_history.extend(data.get("champions_history", []))
    # 昵称缓存恢复：群里成员真名（避免重启后大量回退成“玩家{uid}”）
    user_names.clear()
    for uid, n in data.get("user_names", {}).items():
        if n: user_names[int(uid)] = n
    # 积分系统数据恢复
    for cid, users in data.get("sign_data", {}).items():
        for uid, v in users.items():
            if isinstance(v, dict): sign_data[int(cid)][int(uid)] = {"last": str(v.get("last", "")), "streak": int(v.get("streak", 0))}


def _restore_points_ledger(data):
    chat_earn_daily = hub.chat_earn_daily
    invite_daily = hub.invite_daily
    mall_orders = hub.mall_orders
    newbie_rewarded = hub.newbie_rewarded
    chat_earn_daily.clear()
    for date, chats in data.get("chat_earn_daily", {}).items():
        for cid, users in chats.items():
            for uid, v in users.items():
                chat_earn_daily[str(date)][int(cid)][int(uid)] = int(v)
    # 旧存档迁移（2026-09-12 双账本合并）：老 bot_data.json 里还残留着一个已删除的旧账本键
    #   （结构与 chat_earn_daily 相同、只留 2 天）。直接丢弃会让「最近 2 天的聊天分」
    #   在「积分流水」里凭空消失 —— 那正是用户 09-12 报过的「聊天获得也没有」。
    #   所以按 (date, cid, uid) **取大**并入唯一账本：取大而不是相加，
    #   因为同一天同一笔分在旧版被两个账本各记过一次，相加等于双计。
    for date, chats in data.get("chat_today", {}).items():
        for cid, users in chats.items():
            for uid, v in users.items():
                _cd, _ud, _vd = int(cid), int(uid), int(v)
                _dst = chat_earn_daily[str(date)][_cd]
                if _vd > _dst.get(_ud, 0):
                    _dst[_ud] = _vd
    newbie_rewarded.clear()
    for k in (data.get("newbie_rewarded") or {}):
        newbie_rewarded[str(k)] = True
    invite_daily.clear()
    for date, cs in (data.get("invite_daily") or {}).items():
        for cid, us in cs.items():
            for u, v in us.items():
                invite_daily[str(date)][int(cid)][int(u)] = {
                    "times": int((v or {}).get("times", 0)), "points": int((v or {}).get("points", 0))}
    mall_orders.clear()
    mall_orders.extend(data.get("mall_orders", [])[-200:])


def _restore_guesses_refund(data):
    game_chips = hub.game_chips
    # 积分竞猜（features/guess）已于 2026-09-13 整体删除（用户要求，功能用不到）。
    # 迁移：旧存档若还留着 guesses（下注积分当时已被扣走、托管在奖池里），
    # 先把每人托管注金**全额退还**再丢弃该字段；否则玩家的分永远拿不回来。
    # 只读 `data` 里的旧键，不依赖已删除的 hub.guesses。
    _legacy_guesses = data.get("guesses") or {}
    _refunded_guess = 0
    for _cid_s, _g in _legacy_guesses.items():
        try:
            _cid = int(_cid_s)
        except (TypeError, ValueError):
            continue
        for _u_s, _b in (_g.get("bets") or {}).items():
            try:
                _u = int(_u_s)
                _back = int((_b or {}).get("A", 0)) + int((_b or {}).get("B", 0))
            except (TypeError, ValueError):
                continue
            if _back > 0:
                _cur = game_chips.get(_cid, {}).get(_u, 0)
                game_chips.setdefault(_cid, {})[_u] = int(_cur) + _back
                _refunded_guess += 1
    if _refunded_guess:
        hub.logger.warning(
            "检测到旧存档里的积分竞猜数据（功能已删除），已退还 %d 笔托管注金后丢弃该字段",
            _refunded_guess)


def _restore_redeem_orders(data):
    buy_orders = hub.buy_orders
    redeem_counts = hub.redeem_counts
    redeem_orders = hub.redeem_orders
    buy_orders.clear()
    for oid, o in data.get("buy_orders", {}).items():
        try: buy_orders[str(oid)] = {"cid": int(o["cid"]), "uid": int(o["uid"]), "amount": int(o["amount"]), "ts": o.get("ts", "")}
        except (KeyError, ValueError, TypeError): continue
    redeem_counts.clear()
    for uid, v in data.get("redeem_counts", {}).items():
        try: redeem_counts[int(uid)] = int(v)
        except (KeyError, ValueError, TypeError): continue
    redeem_orders.clear()
    for o in data.get("redeem_orders", [])[-500:]:
        if isinstance(o, dict): redeem_orders.append(dict(o))


def _restore_invite_data(data):
    game_flows = hub.game_flows
    invite_confirmed = hub.invite_confirmed
    invite_pending = hub.invite_pending
    invite_records = hub.invite_records
    join_verify_pending = hub.join_verify_pending
    lurker_checked = hub.lurker_checked
    observe_checked = hub.observe_checked
    tag_synced = hub.tag_synced
    game_flows.clear()
    for o in data.get("game_flows", [])[-2000:]:
        if isinstance(o, dict): game_flows.append(dict(o))
    invite_records.clear()
    for k, v in data.get("invite_records", {}).items():
        if isinstance(v, dict): invite_records[str(k)] = dict(v)
    invite_pending.update(data.get("invite_pending", {}))
    invite_confirmed.update({str(k): int(v) for k, v in (data.get("invite_confirmed", {}) or {}).items()
                             if str(v).lstrip("-").isdigit()})
    for k, v in (data.get("join_verify_pending", {}) or {}).items():   # 入群验证待处理（重启不丢）
        if isinstance(v, dict): join_verify_pending[str(k)] = dict(v)
    observe_checked.update(str(x) for x in (data.get("observe_checked", []) or []))
    lurker_checked.update(str(x) for x in (data.get("lurker_checked", []) or []))
    # 成员标签同步进度（断点续传）：dict 走「清空再填」，与上面两个 set 的
    # update 语义不同 —— 存档里没有的键必须消失，否则旧的"已同步"标记会残留，
    # 让人以为同步过了而其实没同步（那是资损类误判）。
    # 老存档没有这个键 → data.get 退化为 {} → 清空即可，不会崩。
    tag_synced.clear()
    tag_synced.update({str(k): str(v) for k, v in (data.get("tag_synced", {}) or {}).items()})


def _restore_member_and_pending(data):
    _pending_deletes = hub._pending_deletes
    invite_debug = hub.invite_debug
    invite_links = hub.invite_links
    logger = hub.logger
    member_joined_at = hub.member_joined_at
    # 入群时间表恢复：与保存侧成对，重部署后观察期巡检/潜水清理才能继续工作
    for cid, users in (data.get("member_joined_at", {}) or {}).items():
        try:
            c = int(cid)
        except (ValueError, TypeError):
            continue
        for uid, ts in (users or {}).items():
            try:
                member_joined_at[c][int(uid)] = float(ts)
            except (ValueError, TypeError):
                continue
    # 待删消息队列恢复：重启/重部署后由 restore_pending_deletes 重放，游戏面板不再永久残留
    _pending_deletes[:] = [list(q) for q in (data.get("pending_deletes") or [])
                           if isinstance(q, (list, tuple)) and len(q) in (3, 4)]

    hub.set("announce_last_date", str(data.get("announce_last_date", "") or ""))   # 重启同天不重发公告
    invite_debug.clear()
    for cid, lst in data.get("invite_debug", {}).items():
        invite_debug[int(cid)] = list(lst)[-10:]
    invite_links.clear()
    for cid, users in data.get("invite_links", {}).items():
        for uid, v in users.items():
            # 必须 setdefault：invite_links 即使已是 defaultdict，这里 clear() 后仍是 defaultdict，
            # 但旧的 except(KeyError) 会把类型错误也一并吞掉 → 整表静默丢失（邀请进度恒 0 的真凶）
            try:
                invite_links.setdefault(int(cid), {})[int(uid)] = dict(v)
            except (ValueError, TypeError):
                logger.warning("邀请链接恢复跳过异常项 cid=%s uid=%s", cid, uid)
    if not invite_links and data.get("invite_links"):
        logger.warning("邀请链接表恢复后为空，但存档里有 %d 条——请检查恢复逻辑", len(data["invite_links"]))


def _restore_group_data(data):
    admin_logs = hub.admin_logs
    settings_changes = hub.settings_changes
    bot_added_by = hub.bot_added_by
    inherit_daily = hub.inherit_daily
    join_requests = hub.join_requests
    leave_records = hub.leave_records
    ledger = hub.ledger
    member_profiles = hub.member_profiles
    user_first_seen = hub.user_first_seen
    warn_counts = hub.warn_counts
    whitelist = hub.whitelist
    for cid, users in data.get("warn_counts", {}).items():
        for uid, v in users.items():
            try: warn_counts[int(cid)][int(uid)] = int(v)
            except (KeyError, ValueError, TypeError): continue
    # 群组管理数据恢复
    for cid, users in data.get("member_profiles", {}).items():
        for uid, v in users.items():
            if isinstance(v, dict): member_profiles[int(cid)][int(uid)] = v
    for cid, uids in data.get("whitelist", {}).items():
        whitelist[int(cid)].update(int(u) for u in uids)
    for cid, v in data.get("leave_records", {}).items():
        leave_records[int(cid)] = list(v)[-100:]
    for cid, v in data.get("bot_added_by", {}).items():
        if isinstance(v, dict):
            bot_added_by[int(cid)] = {"by": int(v.get("by", 0) or 0), "by_name": v.get("by_name", ""),
                                     "ts": v.get("ts", ""), "title": v.get("title", ""),
                                     "left_ts": v.get("left_ts", "")}
    for cid, v in data.get("join_requests", {}).items():
        join_requests[int(cid)] = list(v)[-100:]
    # 注意：必须先 clear —— load_data() 会在 /恢复 时二次调用，
    # 只 extend 不 clear 会让管理员日志每恢复一次翻一倍（相邻的 ledger 就是对的写法）。
    admin_logs.clear()
    admin_logs.extend(data.get("admin_logs", [])[-300:])
    # 设置改动留痕：与 admin_logs 同组同行。list 必须 clear+fill ——
    # 只 extend 不 clear 会让 /恢复 时条目翻倍，且存档里没有的旧条目必须消失。
    # 老存档没有这个键 → data.get 退化为 [] → 清空即可，不会崩。
    settings_changes.clear()
    settings_changes.extend([dict(e) for e in (data.get("settings_changes") or [])[-300:]
                             if isinstance(e, dict)])
    ledger.clear(); ledger.extend(data.get("ledger", [])[-5000:])
    for date, cids in data.get("inherit_daily", {}).items():
        for cid, users in cids.items():
            for uid, v in users.items():
                try: inherit_daily[str(date)][int(cid)][int(uid)] = int(v)
                except (ValueError, TypeError): continue
    for uid, ts in data.get("user_first_seen", {}).items():
        try: user_first_seen[int(uid)] = float(ts)
        except (ValueError, TypeError): continue


def _restore_permissions_and_history(data):
    ADMIN_USER_IDS = hub.ADMIN_USER_IDS
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    BOT_ADMINS = hub.BOT_ADMINS
    blackjack_history = hub.blackjack_history
    daily_emergency_used = hub.daily_emergency_used
    force_save_now = hub.force_save_now
    game_chips = hub.game_chips
    hourly_race_enabled = hub.hourly_race_enabled
    pending_game_bets = hub.pending_game_bets
    race_daily_stats = hub.race_daily_stats
    race_history = hub.race_history
    race_jackpot = hub.race_jackpot
    race_subsidy_by_day = hub.race_subsidy_by_day
    sget = hub.sget
    AUTHORIZED_GROUPS.update(int(cid) for cid in data.get("authorized_groups", []))
    BOT_ADMINS.clear(); BOT_ADMINS.update(ADMIN_USER_IDS)
    BOT_ADMINS.update(int(x) for x in data.get("bot_admins", []))
    BLACKLISTED_USERS.update(int(x) for x in data.get("blacklist", []))
    for cid, value in data.get("race_jackpot", {}).items(): race_jackpot[int(cid)] = int(value)
    for date, chats in data.get("race_subsidy_by_day", {}).items():
        for cid, amount in chats.items(): race_subsidy_by_day[date][int(cid)] = int(amount)
    for cid, value in data.get("hourly_race_enabled", {}).items(): hourly_race_enabled[int(cid)] = bool(value)
    for cid, value in data.get("race_history", {}).items(): race_history[int(cid)] = list(value)[-10:]
    for cid, value in data.get("blackjack_history", {}).items(): blackjack_history[int(cid)] = list(value)[-10:]
    for cid, value in data.get("race_daily_stats", {}).items(): race_daily_stats[int(cid)] = list(value)[:sget("HORSE_COUNT")]
    for cid, users in data.get("daily_emergency_used", {}).items():
        for uid, used in users.items(): daily_emergency_used[int(cid)][int(uid)] = min(int(used), sget("EMERGENCY_MAX_USES"))
    hub.set("last_business_date", data.get("last_business_date", ""))
    # 全系游戏退款恢复逻辑
    # ★ 逐条容错 + clear() 进 finally：
    #   一条脏记录（非 dict / 金额非数字）把异常抛出去，会连累后面的
    #   `pending_game_bets.clear()` 与 `force_save_now()` 一起被跳过 ——
    #   已退的分留在内存、待退清单还在 → 周期保存又写回磁盘 →
    #   **下次启动再退一遍**（无限发分）。所以：
    #     ① 每条单独 try，坏记录跳过不影响别人；
    #     ② clear() 与存盘放 finally，无论如何都执行（宁可少退，不可重复退）。
    _bad = 0
    _pgb = data.get("pending_game_bets")
    try:
        if not isinstance(_pgb, dict):
            raise TypeError("pending_game_bets 不是字典（%s）" % type(_pgb).__name__)
        for cid, users in _pgb.items():
            if not isinstance(users, dict):
                _bad += 1; continue
            for uid, info in users.items():
                # 兼容旧格式（单条记录）与新格式（按游戏类型分条）
                if isinstance(info, dict):
                    entries = [info] if "amount" in info else list(info.values())
                else:
                    _bad += 1; continue
                for ginfo in entries:
                    try:
                        game_chips[int(cid)][int(uid)] += int(ginfo.get("amount", 0))
                    except (ValueError, TypeError, AttributeError):
                        _bad += 1
    except Exception:
        hub.logger.exception("待退押注恢复失败（清单仍会清空，避免下次重启重复退款）")
        _bad += 1
    finally:
        pending_game_bets.clear()
        try:
            force_save_now()
        except Exception:
            hub.logger.exception("待退押注恢复后立即存盘失败")
    if _bad:
        hub.logger.warning("待退押注恢复：跳过 %d 条格式异常记录", _bad)
