# -*- coding: utf-8 -*-
"""**每日重置**：到点清当日榜 / 赛季日刷新 / 过期红包退余款 / 限时称号到期清理。

从 features/schedule/__init__.py 分家（2026-09-14，桌面清单第 4 项）：每个定时任务独立成一个小文件，加任务 = 加一个文件 + 状态表里加一行。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
"""

from core import hub

from datetime import timedelta
import asyncio

__all__ = ["daily_reset_scheduler"]


async def daily_reset_scheduler(app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    _CUR_CID = hub._CUR_CID
    _safe_cid = hub._safe_cid
    active_poker_games = hub.active_poker_games
    archive_old_profit_data = hub.archive_old_profit_data
    chat_earn_daily = hub.chat_earn_daily
    daily_emergency_used = hub.daily_emergency_used
    daily_reset_groups = hub.daily_reset_groups
    game_chips = hub.game_chips
    logger = hub.logger
    now_bj = hub.now_bj
    parse_hm = hub.parse_hm
    poker_profit_by_date = hub.poker_profit_by_date
    race_daily_stats = hub.race_daily_stats
    rp_packets = hub.rp_packets
    save_data = hub.save_data
    season_daily_refresh = hub.season_daily_refresh
    season_exchange_daily = hub.season_exchange_daily
    sget = hub.sget
    title_equipped = hub.title_equipped
    title_expiry = hub.title_expiry
    user_titles = hub.user_titles

    today = now_bj().strftime("%Y-%m-%d")
    # 第一次启动只记录业务日，避免因部署重启立刻重置玩家积分。
    if not hub.last_business_date:
        hub.set("last_business_date", today); save_data()
    # 延迟导入：互斥的实现放在 features.texas（与 _game_gate 同一处），
    # 在这里 import 一次即可 —— 放函数体内可彻底避开模块级循环导入。
    from features.texas import game_mutex_wait_idle
    while True:
        now = now_bj()
        rh, rm = parse_hm(sget("DAILY_RESET_TIME"), 0, 0)  # 每轮重读，网页改时间即时生效
        target = now.replace(hour=rh, minute=rm, second=1, microsecond=0)
        if target <= now: target += timedelta(days=1)
        await asyncio.sleep((target-now).total_seconds())
        if not sget("DAILY_RESET_ENABLED"):  # 后台「定时任务」开关：关闭期间到点不执行
            continue
        # 懒填默认：空作用群 = 全授权群
        if not daily_reset_groups: daily_reset_groups.update(AUTHORIZED_GROUPS)
        target_groups = daily_reset_groups & AUTHORIZED_GROUPS
        if not target_groups: continue
        # ★ 2026-09-14 用户要求：定时任务**不要在游戏进行中插进来** ——
        #   会把正在玩的人的积分清零、或者打断结算。
        #   先等每个群的当前这一局结束（超时则放弃等待、照常重置，
        #   免得一局卡死就把每日重置永久拖住）。
        _wait_sec = int(hub.namespace().get("DAILY_RESET_WAIT_GAME_SEC", 900) or 0)
        if _wait_sec > 0:
            for _cid in target_groups:
                try:
                    await game_mutex_wait_idle(_cid, timeout=_wait_sec, poll=5)
                except Exception:
                    logger.exception("等待群 %s 游戏结束失败（继续重置）", _cid)
        try:
            # ★ 每轮重读可重赋值的 bot 全局：协程启动时只读一次会**永久陈旧**
            season_active = hub.season_active
            today = now_bj().strftime("%Y-%m-%d")
            # 赛季到点自动结算已移至独立的 season_settle_scheduler（精确到分钟），此处不再处理
            # 午夜仅清理「刚结束的那一天」德州当日榜；保留 _archive 与其他日期历史，避免清空全部历史盈亏
            finished_day = (now_bj() - timedelta(days=1)).strftime("%Y-%m-%d")
            poker_profit_by_date.pop(finished_day, None)
            # 统一积分永久不清零，无需每日重置。
            # 赛季仍每日重置为起始分；进行中的赛季局跳过本次重置，待其结算时在 settle_poker 内补重置（跨午夜补重置）。
            if season_active:
                season_protected = set()
                for poker in active_poker_games.values():
                    if poker.season and poker.phase != "waiting":
                        season_protected.update((poker.chat_id, uid) for uid in poker.players)
                day_key = (now_bj() - timedelta(days=1)).strftime("%Y-%m-%d")
                season_daily_refresh(day_key, target_groups, season_protected)
                save_data()
            for cid in target_groups:
                _CUR_CID.set(_safe_cid(cid))   # 马匹数量可按群覆盖 → 该群日统计长度按该群解析
                if cid in race_daily_stats: race_daily_stats[cid] = [0] * sget("HORSE_COUNT")
            archive_old_profit_data()
            # 聊天积分聚合账本（2026-09-12 起**唯一**）：保留最近 7 天，超出即清，
            # 防长期运行后字典无限膨胀。旧账本那份「2 天清理」随变量一起删掉了。
            # （过期红包退余款仍在下面统一处理。）
            for _d in [d for d in chat_earn_daily if d < (now_bj() - timedelta(days=7)).strftime("%Y-%m-%d")]:
                chat_earn_daily.pop(_d, None)
            # 兑换赛季分的每日累计：只留最近两天，防长期运行后字典无限膨胀
            for _d in [d for d in season_exchange_daily if d < (now_bj() - timedelta(days=1)).strftime("%Y-%m-%d")]:
                season_exchange_daily.pop(_d, None)
            for pid in list(rp_packets.keys()):
                p = rp_packets[pid]
                if now_bj().timestamp() - p["ts"] > 86400:
                    if p["left_amt"] > 0:
                        game_chips[p["cid"]][p["from"]] += p["left_amt"]
                    rp_packets.pop(pid, None)
            save_data()
            # 清理已到期的限时商店称号
            _now_ts = int(now_bj().timestamp())
            for _u in list(title_expiry.keys()):
                for _t in list(title_expiry[_u].keys()):
                    if title_expiry[_u][_t] <= _now_ts:
                        title_expiry[_u].pop(_t, None)
                        user_titles.get(_u, set()).discard(_t)
                        if title_equipped.get(_u) == _t:
                            title_equipped.pop(_u, None)
                if not title_expiry[_u]:
                    del title_expiry[_u]
                if _u in user_titles and not user_titles[_u]:
                    del user_titles[_u]
            daily_emergency_used.clear()
            # ⚠️ 必须走 hub.set：单文件时代这里靠函数开头的 `global last_business_date`
            #    才能写进模块全局；抽缝时那条 global 被丢掉，裸赋值就变成了**只写模块局部**
            #    → bot 的全局永不前进 → `/定时任务状态` 的「上次业务日」永远停在首次运行那天
            #    （2026-09-14 修，pyflakes 的 `last_business_date is assigned to but never used`
            #     把它抓出来的；守卫见 test_schedule_split.py【8】）。
            hub.set("last_business_date", today)
            save_data()
        except Exception:
            logger.exception("daily_reset_scheduler 本轮异常（已吞并继续，下个周期重试）")
