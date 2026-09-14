# -*- coding: utf-8 -*-
"""feature/race —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
from telegram.error import BadRequest, RetryAfter, TelegramError
import asyncio
import math
import random
import time

def race_rake_split(payouts, percent=None):
    """赛车抽水（纯计算不扣款），返回 (总抽水, {uid: 金额})。

    口径 = 群友原话（2026-09-11 21:50）：**「反正就是投注总数，全部赔给押中的人，然后抽5%」**
    ⇒ 基数 = **派彩**（不是净赢）。总池 1000 就抽 50、押中者合计到手 950 —— 正是群友要的
    「1000 起码给 950」。抽出来的钱**不销毁**，进底池由下一期的押中者瓜分（见 settle()），
    这样群里每一分都在转，谁都不当貔貅。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    if not sget("RAKE_ENABLED"):
        return 0, {}
    pct = sget("RACE_RAKE_PERCENT") if percent is None else int(percent or 0)
    if pct <= 0:
        return 0, {}
    total, per = 0, {}
    for uid, payout in (payouts or {}).items():
        if not isinstance(uid, int) or uid <= 0 or payout <= 0:
            continue
        amt = int(payout * pct / 100)
        if amt <= 0:
            continue
        per[uid] = amt
        total += amt
    return total, per


class HorseRace:
    def __init__(self, cid, owner, jackpot, mode=None, auto=False):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        current_game_mode = hub.current_game_mode
        sget = hub.sget
        self.chat_id, self.owner_id, self.jackpot = cid, owner, jackpot
        self.mode = mode or hub.current_game_mode()
        # 开赛来源：True=定时自动开赛（可享系统加奖）；False=群友/管理员手动发起（不派奖，2026-09-11 用户要求）
        self.auto_started = bool(auto)
        self.bets, self.total_bets, self.pool = defaultdict(dict), [0] * hub.sget("HORSE_COUNT"), 0
        self.phase, self.create_time, self.positions, self.arrivals = "betting", time.time(), [0.0] * hub.sget("HORSE_COUNT"), []
        self.display_positions = [0] * hub.sget("HORSE_COUNT")  # 显示格（=节奏曲线进度的整数部分，严格跟随真实进度）
        self.arrival_times, self.race_start_time = {}, None
        self.notified, self.name_cache = set(), {}
        self.game_msg_id = self.animation_msg_id = None
        self.task, self.settled, self.cancelled, self.lock = None, False, False, asyncio.Lock()
        self.final_odds = None
        self.bet_odds = defaultdict(dict)  # 每注下注瞬间锁定的赔率（uid->horse 金额加权平均）
        self.panel_cd = 0.0  # 看板重发冷却：重复发 /赛车 时避免刷屏（未超时只回一句文字）
        rates = [random.uniform(.18, .35) for _ in range(hub.sget("HORSE_COUNT"))]
        # 先按显示精度（整数%）四舍五入再归一化，避免"显示胜率相同、真实胜率不同"导致同胜率马赔率不同
        rates = [round(r, 2) for r in rates]
        total = sum(rates)
        self.rates = [value / total for value in rates]
        # 用胜率抽样每匹对象的精确完赛时间：长期获胜概率更接近显示胜率，仍保留随机爆冷。
        self.finish_durations = {}

    def odds(self):
        # 【押注池 parimutuel · 2026-09-11】用户：「赔率不是动态的也不是根据下注金额的」；
        # 群友：「皮卡这赔率你看对？」「赔率太低啦」「总下注1000分起码950分给赢的人」。
        # 赔率 = 总池 ÷ 该马注额 —— 完全由下注金额决定（押得越少赔率越高），
        # 全体押中者合计恰好分完总池，不需要系统补分、也不会大量滚存。
        #
        # 旧模型（下列 else 分支）为什么被换掉：1/胜率 × 注额压力因子，再叠三道「压平器」——
        #   因子夹在 0.4~2.5（注额最多影响 ±2.5 倍）；单调约束（低胜率马赔率被往下拉）；
        #   同显示胜率分组统一（取组内最低值）。仿真：四匹马显示胜率都是 22% 时，
        #   押 300 的轿车与押 100 的皮卡赔率**都是 4.29x**——这就是群友质疑的那一幕。
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        sget = hub.sget
        n = hub.sget("HORSE_COUNT")
        total = sum(self.total_bets)
        if hub.sget("RACE_PARIMUTUEL"):
            if total <= 0:
                # 开盘还没人下注：先给「1/胜率」参考赔率（仅供预览，下注后立刻变真池赔率）
                return [max(1.05, 1.0 / self.rates[i]) for i in range(n)]
            # 赔付池 = 本期总注 + 滚存底池（2026-09-11 群友：「不要让赛车成为貔貅，只进不出的游戏
            # 很快就没人玩了」）。底池**必须并进来**才算真的发得出去 —— 否则「无人押中」的整池和
            # 抽水都会永久冻死在这个字段里（parimutuel 下赔率只认注额，原本一分都不会用到底池）。
            payout = total + max(0, self.jackpot)
            # ---- 2026-09-13 用户报障：「赛马赔率 bug，胜率不同赔率却一样」 ----
            # 旧口径 赔率 = 派彩池 ÷ 该马注额，**只看押注金额、完全不看胜率** ⇒ 两匹马只要注额
            # 一样，胜率 35% 和 18% 拿到的赔率就一模一样（甚至冷门马赔率反而更低）。
            # 新口径把两个因素**都**接进来：
            #     p_i   = (1-w)·押注占比 + w·真实胜率      ← w = RACE_ODDS_RATE_WEIGHT（0~1）
            #     赔率_i = 派彩池 ÷ (p_i × 总注)
            #   · w = 0  → 与旧口径**数学上完全等价**（p = 占比 ⇒ 赔率 = 派彩池 ÷ 注额）
            #   · w > 0  → 胜率高的马 p 大 ⇒ 赔率低；押得多的马 p 也大 ⇒ 赔率低（两者都挂钩）
            #   · w = 1  → 只看胜率（= 公平赔率 1/胜率，再按派彩池缩放）
            # 派彩保险（绝不允许凭空印分）：某匹马胜出时派彩 = 派彩池 × (占比_i / p_i)，
            # 这个值可能 > 派彩池。故统一乘 K = 1 / max(占比/p)，把**最坏那匹**压到恰好等于
            # 派彩池，其余更省 ⇒ 「派彩 ≤ 派彩池」在数学上恒真（Σ占比 = Σp = 1 ⇒ max ≥ 1）。
            # 副作用正好是想要的：最坏那匹拿到的就是旧的池赔率，其余按实力往下压。
            try:
                _w = max(0.0, min(1.0, float(hub.sget("RACE_ODDS_RATE_WEIGHT") or 0) / 100.0))
            except (TypeError, ValueError):
                _w = 0.0
            if _w <= 0:
                raw = [payout / self.total_bets[i] if self.total_bets[i] > 0 else 0.0
                       for i in range(n)]      # 无人押注的马 = 没有赔率（0，界面展示为 —）
            else:
                _p = [(1.0 - _w) * (self.total_bets[i] / total) + _w * self.rates[i] for i in range(n)]
                raw = [payout / (_p[i] * total) if self.total_bets[i] > 0 else 0.0 for i in range(n)]
                _mx = max((self.total_bets[i] / total) / _p[i]
                          for i in range(n) if self.total_bets[i] > 0) if any(self.total_bets) else 0.0
                if _mx > 1.0:
                    raw = [v / _mx for v in raw]
            if hub.sget("RACE_ODDS_CAP") > 0:
                raw = [min(v, hub.sget("RACE_ODDS_CAP")) if v > 0 else v for v in raw]
            return raw
        # ---- 旧模型：后台把「押注池赔率」关掉即回退到此 ----
        avg = 1.0 / n
        raw = []
        for i in range(n):
            base = 1.0 / self.rates[i]                       # 自然公平赔率
            if total > 0 and self.total_bets[i] > 0:
                share = self.total_bets[i] / total
                factor = (avg / share) ** 0.5                # 押注占比越高 -> 因子越小
                factor = max(0.4, min(factor, 2.5))          # 限制单匹摆动幅度，保证可读
            else:
                factor = 1.0
            raw.append(base * factor)
        # 赔率上限（经济保护）：默认 10 倍，0=无上限（旧行为）。封顶在单调约束前统一应用
        if hub.sget("RACE_ODDS_CAP") > 0:
            raw = [min(value, hub.sget("RACE_ODDS_CAP")) for value in raw]
        # 单调约束：按胜率升序，确保低胜率马的赔率不低于高胜率马
        order = sorted(range(n), key=lambda i: self.rates[i])
        for a, b in zip(order, order[1:]):
            if raw[a] < raw[b]:
                raw[b] = raw[a]
        # 同显示胜率（整数%）的馬，赔率必须完全一致，避免"胜率一样赔率却不同"的困惑。
        # 取组内最低赔率统一，不抬高任一匹，保证庄家不被过度赔付。
        groups = {}
        for i in range(n):
            groups.setdefault(round(self.rates[i] * 100), []).append(i)
        for grp in groups.values():
            if len(grp) > 1:
                lo = min(raw[i] for i in grp)
                for i in grp:
                    raw[i] = lo
        return [max(1.05, v) for v in raw]

    async def bet(self, uid, horse, amount):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        game_chips = hub.game_chips
        pending_game_bets = hub.pending_game_bets
        save_data = hub.save_data
        sget = hub.sget
        wallet_locks = hub.wallet_locks
        if self.phase != "betting" or self.cancelled: return False, "当前不是下注阶段"
        wallet = hub.game_chips
        if not 0 <= horse < hub.sget("HORSE_COUNT") or amount <= 0: return False, "马号或金额无效"
        async with hub.wallet_locks[uid]:
            # 锁内重校阶段：等锁期间比赛可能已开跑，避免按旧赔率接受下注
            if self.phase != "betting" or self.cancelled: return False, "当前不是下注阶段"
            if amount > wallet[self.chat_id][uid]: return False, "积分不足"
            # 先按「下注前」赔率锁定（即玩家在界面上看到的赔率），确保看到=拿到
            o = self.odds()[horse]
            wallet[self.chat_id][uid] -= amount; self.pool += amount; self.total_bets[horse] += amount
        self.bets[uid][horse] = self.bets[uid].get(horse, 0) + amount
        prev_amt = self.bets[uid][horse] - amount
        prev_odd = self.bet_odds[uid].get(horse)
        self.bet_odds[uid][horse] = o if prev_odd is None else (prev_amt * prev_odd + amount * o) / (prev_amt + amount)

        # 记录退款保护
        curr_pending = hub.pending_game_bets[self.chat_id][uid].get("horse", {}).get("amount", 0)
        hub.pending_game_bets[self.chat_id][uid]["horse"] = {"amount": curr_pending + amount, "mode": self.mode}
        hub.save_data(); return True, "下注成功"

    def buttons(self):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        sget = hub.sget
        return InlineKeyboardMarkup([[InlineKeyboardButton(f"{hub.sget('HORSE_EMOJI')[i]} {amount}", callback_data=f"horsebet_{i}_{amount}") for i in range(hub.sget("HORSE_COUNT"))] for amount in hub.sget("FIXED_BET_AMOUNTS")])

    async def view(self, app):
        """保持原版赛车下注界面的赛道、路书、胜率和投注信息结构。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        get_name = hub.get_name
        race_daily_stats = hub.race_daily_stats
        race_history = hub.race_history
        race_id = hub.race_id
        race_subsidy_banner = hub.race_subsidy_banner
        sget = hub.sget
        remain = max(0, int(hub.sget("RACE_AUTO_START") - (time.time() - self.create_time)))
        minutes, seconds = divmod(remain, 60)
        history = "".join(hub.sget("HORSE_EMOJI")[index] for index in hub.race_history[self.chat_id][-10:]) or "暂无"
        stats = hub.race_daily_stats[self.chat_id]
        total_wins = sum(stats)
        odds = self.odds()
        pool_total = sum(self.total_bets)
        lines = [
            f"🏁 赛车大赛 {hub.race_id(self.create_time)} 🏁",
            "━" * 14,
            *[f"🏁{'━' * 13}{hub.sget('HORSE_EMOJI')[i]}" for i in range(hub.sget("HORSE_COUNT"))],
            "━" * 14,
            "📊 路书",
            f"近10场: {history}",
            "📜 当日胜率:",
            "  " + " | ".join(f"{hub.sget('HORSE_EMOJI')[i]} {stats[i]}胜" for i in range(hub.sget("HORSE_COUNT"))),
            "  " + " | ".join(f"{hub.sget('HORSE_EMOJI')[i]} {stats[i] / total_wins * 100:.0f}%" if total_wins else f"{hub.sget('HORSE_EMOJI')[i]} 0%" for i in range(hub.sget("HORSE_COUNT"))),
            "📊 投注情况:" + (f" 总池 {pool_total} 积分" if pool_total else "")
            + (f" ｜🎰 底池 {self.jackpot}（押中者瓜分）" if self.jackpot else ""),
        ]
        for i, odd in enumerate(odds):
            # 押注池下「无人押注的马」没有赔率（0）——显示 — 而不是 0.00x，避免误读成「押了不赔」
            _odd_txt = f"{odd:.2f}x" if odd > 0 else "—"
            lines.append(f"{hub.sget('HORSE_EMOJI')[i]} {hub.sget('HORSE_NAMES')[i]}: 胜率{self.rates[i] * 100:.0f}% | {self.total_bets[i]}积分 | 赔率 {_odd_txt}")
        lines.append("━" * 14)
        if self.bets:
            lines.append("📋 玩家下注：")
            for uid, bets in self.bets.items():
                name = self.name_cache.get(uid) or await hub.get_name(app, uid)
                self.name_cache[uid] = name
                lines.append(f"{name}: " + " ".join(f"{hub.sget('HORSE_EMOJI')[h]}{amount}" for h, amount in bets.items()))
            lines.append("")
        _banner = hub.race_subsidy_banner(self.auto_started)
        if _banner: lines.append(_banner)
        # 押注池的赔率只反映「此刻」的注额分布，开赛后锁盘才定终价 —— 文案要让玩家知道
        _lock_hint = ("赔率随注实时浮动，以开赛锁盘时为准" if hub.sget("RACE_PARIMUTUEL")
                      else "赔率随注浮动、下注即锁")
        lines.extend([f"⏰ 距离开赛还有 {minutes} 分 {seconds:02d} 秒", f"🔒 开赛后锁盘，{_lock_hint}"])
        return "\n".join(lines)

    def animation(self):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        sget = hub.sget
        lines = ["🏁 赛车进行中", "━" * 14]
        for i, pos in enumerate(self.display_positions):
            track_pos = max(0, min(hub.sget("RACE_TRACK_LENGTH"), int(pos)))
            track = "🏁" + (hub.sget("HORSE_EMOJI")[i] + "━" * hub.sget("RACE_TRACK_LENGTH") if track_pos >= hub.sget("RACE_TRACK_LENGTH") else "━" * (hub.sget("RACE_TRACK_LENGTH") - track_pos - 1) + hub.sget("HORSE_EMOJI")[i] + "━" * track_pos)
            lines.append(track)
        if self.arrivals: lines.append("✅ 到达：" + " ".join(hub.sget("HORSE_EMOJI")[i] for i in self.arrivals))
        return "\n".join(lines)

    async def _push_animation_frame(self, app):
        """先删旧帧、再发新帧（每帧以新消息出现在群底部，无双画面堆叠）。
        发送/删除都完整尊重 429 的 retry_after 重试——之前画面冻结的根因是
        safe_send/safe_delete 吞掉限流错误，而不是删发顺序本身。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        logger = hub.logger
        text = self.animation()
        old_id, self.animation_msg_id = self.animation_msg_id, None
        if old_id:
            for _ in range(3):
                try:
                    await app.bot.delete_message(chat_id=self.chat_id, message_id=old_id)
                    break
                except RetryAfter as exc:
                    await asyncio.sleep(min(exc.retry_after + 0.5, 25))
                except TelegramError:
                    break
        msg = None
        for _ in range(4):  # 发送最多重试 4 次并按 Telegram 要求等待，保证帧必达、不冻结
            try:
                # ⚠️ autodel_keep：赛马动画帧比赛全程要一直能编辑（下一帧会删掉上一帧），
                #   绝不能进「默认自动删除」。旧版靠「调用方函数名 == _push_animation_frame」
                #   豁免 —— 函数改名就会让整场比赛的动画逐帧消失。
                msg = await app.bot.send_message(chat_id=self.chat_id, text=text, autodel_keep=True)
                break
            except RetryAfter as exc:
                await asyncio.sleep(min(exc.retry_after + 0.5, 25))
            except TelegramError:
                hub.logger.exception("赛车动画帧发送失败: %s", self.chat_id)
                break
        self.animation_msg_id = msg.message_id if msg else None

    async def _run_betting_phase(self, app):
        """**界面刷新段**：下注倒计时 —— 整点推送通知 + 每 30 秒刷新主面板。

        返回 True 表示本局已被取消（调用方应直接结束）。
        2026-09-13 从 run() 拆出：原先 run() 把「倒计时刷新」「排赛程」「跑动画」
        三件事搅在 100 行里，改通知文案要绕过赛程/动画代码（改 A 崩 B 的温床）。
        """
        safe_edit = hub.safe_edit
        safe_send = hub.safe_send
        # 统一通知点：60秒, 30秒
        thresholds = [60, 30]
        while self.phase == "betting" and not self.cancelled:
            # 实时计算剩余时间
            remain = max(0, int(hub.sget("RACE_AUTO_START") - (time.time() - self.create_time)))

            # 只有在整秒点附近才发出推送通知，防止重复发送
            for threshold in thresholds:
                if remain <= threshold and threshold not in self.notified:
                    self.notified.add(threshold)
                    notice = await safe_send(app.bot, self.chat_id, f"⏰ 赛车还剩 {threshold // 60} 分钟 {threshold % 60} 秒！")
                    # 之前这条提示发出后就一直留在群里，需要自动删除
                    hub.schedule_delete(app, self.chat_id, notice, hub.sget("RACE_NOTICE_DELETE_SECONDS"))

            if not remain: break

            # 实时刷新主面板
            await safe_edit(app.bot, self.chat_id, self.game_msg_id, await self.view(app), reply_markup=self.buttons())

            # 界面刷新与通知检查间隔 30 秒
            await asyncio.sleep(min(30, max(1, remain)))
        return self.cancelled

    def _plan_race(self):
        """**比赛过程·排赛程**（纯计算，不碰网络、无 await）。

        2026-09-13 从 run() 拆出 —— 这段是「谁第几名、每匹马跑多久、中途什么节奏」
        的剧本，与「怎么把画面发到群里」完全无关，单拎出来才好单独推理/测试。
        写入 self.finish_order / self.finish_durations / self.race_tempo。
        """
        # 先按胜率加权抽取完整名次，再分配有间隔的完赛时间。
        # 这样长期夺冠率接近显示胜率，同时不会出现开赛第一帧直接到终点。
        remaining = list(range(hub.sget("HORSE_COUNT")))
        finish_order = []
        while remaining:
            total_rate = sum(self.rates[index] for index in remaining)
            target = random.uniform(0, total_rate)
            cumulative = 0.0
            for index in remaining:
                cumulative += self.rates[index]
                if cumulative >= target:
                    finish_order.append(index)
                    remaining.remove(index)
                    break
        self.finish_order = finish_order
        # 赛程总时长 = minimum_duration + 3*finish_gap = 70 秒
        # 配合 RACE_ANIMATION_INTERVAL=5.0 与 RACE_TRACK_LENGTH=14 → 约 15 帧、每帧走 1 格
        minimum_duration = 55.0
        finish_gap = 5.0
        self.finish_durations = {
            horse: minimum_duration + rank * finish_gap + random.uniform(-0.20, 0.20)
            for rank, horse in enumerate(finish_order)
        }
        # 节奏分配（v2.9）：每辆车一条单调节奏曲线 f(p) = p ± a·sin(pπ) —— f(0)=0, f(1)=1,
        # 完赛时刻与名次分毫不差，但中段节奏不同 → 真实反超/守擂戏码。
        # 方向与幅度全随机（冠军不特殊，先慢后快/先快后慢各半，25% 概率接近匀速），每场剧本不重样。
        # a 上限按各车完赛时长收紧，保证最慢段每帧仍 +≥1 格（不钉死）；曲线单调 → 无倒退。
        # 显示 = int(真实节奏进度)，无累积/无强制步长 → 显示与真实严格同步，零失真。
        self.race_tempo = {}
        for rank, horse in enumerate(finish_order):
            T = self.finish_durations[horse]
            a_cap = max(0.0, (1 - T / (hub.sget("RACE_TRACK_LENGTH") * hub.sget("RACE_ANIMATION_INTERVAL"))) / math.pi)
            if random.random() < 0.25:
                a = random.uniform(0.05, 0.35) * a_cap    # 四分之一概率接近匀速，增加剧本多样性
            else:
                a = random.uniform(0.4, 0.95) * a_cap
            sign = random.choice([-1, 1])                  # -1 先慢后快（反超），+1 先快后慢（守擂）
            self.race_tempo[horse] = (sign, a)

    async def _run_race_frames(self, app):
        """**比赛过程·跑帧**：按时间推进位置、逐帧推送，直到全部到达。

        2026-09-13 从 run() 拆出 —— 只负责「时间 → 位置 → 画面」，
        赛程数据由 _plan_race 预先备好。
        """
        while not self.cancelled and len(self.arrivals) < hub.sget("HORSE_COUNT"):
            now = time.time()
            for i in range(hub.sget("HORSE_COUNT")):
                if i in self.arrival_times:
                    continue
                duration = self.finish_durations[i]
                progress = min(1.0, max(0.0, (now - self.race_start_time) / duration))
                sign, amp = self.race_tempo.get(i, (0, 0.0))
                tempo = progress + sign * amp * math.sin(progress * math.pi)
                self.positions[i] = max(0.0, min(float(hub.sget("RACE_TRACK_LENGTH")),
                                                 hub.sget("RACE_TRACK_LENGTH") * tempo))
                if progress >= 1.0:
                    self.arrival_times[i] = self.race_start_time + duration
                    self.positions[i] = float(hub.sget("RACE_TRACK_LENGTH"))
                    self.display_positions[i] = hub.sget("RACE_TRACK_LENGTH")
                else:
                    # 画面格子严格跟随真实节奏进度：既不超前（不会提前压线）也不滞后（不会钉死）
                    self.display_positions[i] = max(0, min(hub.sget("RACE_TRACK_LENGTH"), int(self.positions[i])))
            self.arrivals = sorted(self.arrival_times, key=self.arrival_times.get)
            await self._push_animation_frame(app)
            if len(self.arrivals) < hub.sget("HORSE_COUNT"): await asyncio.sleep(hub.sget("RACE_ANIMATION_INTERVAL"))

    async def run(self, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        try:
            # ① 下注倒计时：通知 + 面板刷新（界面刷新段）
            if await self._run_betting_phase(app): return
            # ② 开赛：切阶段、记开赛时刻、发开赛提示
            self.phase = "racing"
            self.final_odds = self.odds()
            self.race_start_time = time.time()
            # autodel-keep：开赛帧不回收——牌桌这条消息比赛全程都要能被继续编辑
            #   （结束后会被改写成结果看板；中途取消则走 refund() 改写 + 排程回收）。
            await hub.safe_edit(app.bot, self.chat_id, self.game_msg_id, "🏁 比赛开始！正在奔跑中……", reply_markup=None)
            # ⚠️ autodel_keep：赛马**开赛帧**是动画的起点消息，随后每帧「删旧发新」，
            #   比赛结束前必须一直在场（旧版靠调用方函数名 run 豁免 → 改名即失效）。
            msg = await hub.safe_send(app.bot, self.chat_id, "🏁 比赛开始！正在奔跑中……", autodel_keep=True); self.animation_msg_id = msg.message_id if msg else None
            # ③ 排赛程（纯计算）
            self._plan_race()
            # ④ 跑帧直到全部到达
            await self._run_race_frames(app)
            # ⑤ 结算
            if not self.cancelled: await self.settle(app)
        except asyncio.CancelledError: raise
        except Exception:
            hub.logger.exception("赛车任务异常")
            if not self.settled:
                await self.refund(app, "⚠️ 赛车异常，所有下注已退款。")

    async def settle(self, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        RANK_PAGE_SIZE = hub.RANK_PAGE_SIZE
        _check_level_change = hub._check_level_change
        _earn_add = hub._earn_add
        _earn_get = hub._earn_get
        active_horse_races = hub.active_horse_races
        broadcast_big_win = hub.broadcast_big_win
        business_date = hub.business_date
        commit_rake = hub.commit_rake
        emergency_if_needed = hub.emergency_if_needed
        force_save_now = hub.force_save_now
        game_chips = hub.game_chips
        games_played = hub.games_played
        get_name = hub.get_name
        logger = hub.logger
        pending_game_bets = hub.pending_game_bets
        race_daily_stats = hub.race_daily_stats
        race_history = hub.race_history
        race_id = hub.race_id
        race_jackpot = hub.race_jackpot
        race_profit_by_date = hub.race_profit_by_date
        race_rake_split = hub.race_rake_split
        race_subsidy_by_day = hub.race_subsidy_by_day
        race_subsidy_for = hub.race_subsidy_for
        race_subsidy_split = hub.race_subsidy_split
        rank_line = hub.rank_line
        safe_delete = hub.safe_delete
        safe_send = hub.safe_send
        safe_send_long = hub.safe_send_long
        save_data = hub.save_data
        schedule_delete = hub.schedule_delete
        schedule_delete_ids = hub.schedule_delete_ids
        schedule_notice_delete = hub.schedule_notice_delete
        send_settle_rank = hub.send_settle_rank
        sget = hub.sget
        total_profit_by_game = hub.total_profit_by_game
        async with self.lock:
            if self.settled or self.cancelled: return
            self.settled, self.phase = True, "settling"
            payouts_applied = False
            try:
                if not self.arrivals: raise RuntimeError("赛车未产生到达顺序")
                winner, date = self.arrivals[0], hub.business_date()
                fallback_odd = (self.final_odds or self.odds())[winner]
                if self.mode == "official":
                    hub.race_daily_stats[self.chat_id][winner] += 1
                    hub.race_history[self.chat_id] = (hub.race_history[self.chat_id] + [winner])[-10:]
                standings = ["🥇", "🥈", "🥉", "🏅"]
                lines = [f"🏆 赛车大赛 {hub.race_id(self.create_time)} 结果", "━━━━━━━━━━━━━━━━━"]
                # 名次合并成一行（2026-09-11 用户：「后面的结算好他妈的长啊文字」）。
                # 顺带修掉隐藏越界：standings 只有 4 个，HORSE_COUNT 可配到 8 —— 原写法会 IndexError
                # 把整场拖进「结算异常→退款」。现在第 5 名起退化为「5. 名字」。
                lines.append(" ".join(
                    f"{standings[index] if index < len(standings) else f'{index + 1}.'}"
                    f"{hub.sget('HORSE_EMOJI')[horse]} {hub.sget('HORSE_NAMES')[horse]}"
                    for index, horse in enumerate(self.arrivals)))

                # 先获取所有玩家名字：避免派彩后因取名字失败触发异常退款，导致已派彩玩家被双重派彩
                for uid in self.bets:
                    if uid not in self.name_cache: self.name_cache[uid] = await hub.get_name(app, uid)

                # 阶段一：计算派彩并记录盈亏（不动钱包，避免中途异常导致已派彩玩家被双重退款）
                settlements, total_payout = [], 0
                wallet = hub.game_chips
                for uid, bets in self.bets.items():
                    stake = sum(bets.values()); bet_on_winner = bets.get(winner, 0)
                    # 押注池：同一匹马的所有押中者共用「锁盘时的池赔率」（赔率本就随注额走到锁盘）；
                    # 旧模型才用「下注瞬间锁定的个人赔率」。这修掉了「面板看到 4.29x、结算只给 2.44x」。
                    bet_odd = (fallback_odd if hub.sget("RACE_PARIMUTUEL")
                               else self.bet_odds[uid].get(winner, fallback_odd))
                    payout = int(bet_on_winner * bet_odd)
                    net = payout - stake
                    if self.mode == "official":
                        hub.race_profit_by_date[date][self.chat_id][uid] += net
                    settlements.append((uid, self.name_cache[uid], stake, bet_on_winner, payout, net, bet_odd))
                # 抽水先算（官方模式），结算行直接带「实收」。
                # 口径 = 群友原话：「投注总数，全部赔给押中的人，然后抽 5%」⇒ **按派彩抽**
                # （总池 1000 就抽 50、押中者合计到手 950），不是按「净赢」抽。
                # 这 5% 不销毁 —— 进底池，下一期由押中者按注额比例瓜分（见下面 race_jackpot 那行）。
                rake_per = (hub.race_rake_split({s[0]: s[4] for s in settlements})[1]
                            if self.mode == "official" else {})
                # 系统加奖（2026-09-11 用户要求）：先判定额度，再按押中者注额比例拆分。
                # 无人押中 → 拆不出人 → 不发、也不消耗当日额度。
                subsidy = hub.race_subsidy_for(self.chat_id, date, len(self.bets), self.auto_started)
                subsidy_map = hub.race_subsidy_split(subsidy, self.bets, winner) if subsidy else {}
                if not subsidy_map: subsidy = 0
                # 阶段二：统一改写钱包（此处仅 dict 操作，不会抛异常，payouts_applied 必定置位）
                for uid, _, _, _, payout, _, _ in settlements:
                    wallet[self.chat_id][uid] += payout + subsidy_map.get(uid, 0); total_payout += payout
                if subsidy: hub.race_subsidy_by_day[date][self.chat_id] += subsidy
                if len(hub.race_subsidy_by_day) > 3:      # 只留最近 3 天，别让按日容器无限膨胀
                    for _d in sorted(hub.race_subsidy_by_day.keys())[:-3]: hub.race_subsidy_by_day.pop(_d, None)
                payouts_applied = True

                # 大奖战报：押中独赢且净赢超阈值 → 广播其他授权群
                best = max(settlements, key=lambda s: s[5]) if settlements else None
                if best and best[5] > 0:
                    detail = f"🐴 押中 {hub.sget('HORSE_EMOJI')[winner]}{hub.sget('HORSE_NAMES')[winner]}（赔率 {best[6]:.1f}）"
                    await hub.broadcast_big_win(app, self.chat_id, best[0], "🏎️ 赛车大赛", best[5], detail)
                if self.mode == "official":
                    await hub.commit_rake(app, self.chat_id, rake_per, "赛车")
                    # 累计参与局数（归零门槛）+ 赢分计入累计积分 + 升级通知
                    for uid, _nm, _stake, _bow, _pay, _net, _odd in settlements:
                        hub.games_played[self.chat_id][uid] += 1
                        # 实际到手 = 净赢 - 本局抽水（抽水已在上面扣除）+ 系统加奖（真产出，计入累计积分）
                        _gain = _net - int(rake_per.get(uid, 0) or 0) + subsidy_map.get(uid, 0)
                        if _gain > 0:
                            _oe = hub._earn_get(self.chat_id, uid)
                            hub._earn_add(self.chat_id, uid, _gain)
                            await hub._check_level_change(app, self.chat_id, uid, _oe, hub._earn_get(self.chat_id, uid))

                # 底池进出（2026-09-11 群友：「不要让赛车成为貔貅，只进不出的游戏很快就没人玩了」）：
                #   进 = 本期抽水 + 无人押中时的整池 + 赔率封顶没派完的部分；
                #   出 = 下一期并入赔付池（见 odds()），由押中者按注额比例瓜分。
                # 修的是真 bug：改押注池之前这行是 old 模型的「补充赔付」逻辑，改池之后
                # total_payout ≡ available_pool ⇒ 这个字段**既不进也不出**，成了个死数字；
                # 而「无人押中」时整池被它吞掉、永不发放 —— 就是群友说的貔貅。
                available_pool = self.jackpot + self.pool
                _rake_total = sum(rake_per.values()) if rake_per else 0
                if self.mode == "official":
                    hub.race_jackpot[self.chat_id] = max(0, available_pool - total_payout) + _rake_total
                if not total_payout:
                    lines.extend(["", f"🔄 无人押中，{available_pool} 分全部滚入底池（下一期押中者瓜分）。"])

                if subsidy:
                    # 加奖压成一行（原来是「标题 + 每人一行」，多人时又长又占地方）
                    _sub_parts = []
                    for _uid, _amt in sorted(subsidy_map.items(), key=lambda x: -x[1]):
                        _nm = self.name_cache.get(_uid)
                        if not _nm:
                            _nm = await hub.get_name(app, _uid); self.name_cache[_uid] = _nm
                        _sub_parts.append(f"{_nm}+{_amt}")
                    lines.append(f"🎁 加奖 {subsidy}：" + "、".join(_sub_parts))

                # 结算逐人一行、只留必需字段（2026-09-11 用户：结算文字太长）。
                # 原来每行是「名：总投注 N｜命中 N（N.NNx）｜派彩 N｜净 ±N（实收 N，含抽水N）」，40+ 字。
                # 标题顺带带「总池/抽水/底池」（群友质疑「总下注1000 到底给赢家多少」）——
                # 把池子和抽水摆在标题里一眼能算，**不额外占一行**（`→底池` 表意：这 5% 没销毁、
                # 下一期还发给押中的人）；无抽水（个人局/未开启）时退回纯标题。
                if self.pool and _rake_total:
                    lines.append(f"💰 本局结算（总池 {self.pool} · 抽水 {_rake_total}→底池）")
                elif self.jackpot + self.pool:
                    lines.append(f"💰 本局结算（总池 {self.pool + self.jackpot}）")
                else:
                    lines.append("💰 本局结算")
                for _, name, stake, bet_on_winner, payout, net, bo in settlements:
                    if bet_on_winner > 0:
                        lines.append(f"{name} 押{stake}→派{payout}（{bo:.2f}x）净{net:+d}")
                    else:
                        lines.append(f"{name} 净{net:+d}")

                # 累计盈利榜单独发一条（2026-09-11 用户要求：结算正文太长像刷屏，榜单拆开发）
                _rank_lines = None
                if self.mode == "official":
                    day_rank = sorted(hub.total_profit_by_game(hub.race_profit_by_date, self.chat_id).items(), key=lambda item: item[1], reverse=True)[:hub.RANK_PAGE_SIZE]
                    if day_rank:
                        _rank_lines = ["🏆 <b>赛车累计盈利榜（总数）</b>", "━━━━━━━━━━━━━━━━━"]
                        for index, (uid, amount) in enumerate(day_rank, 1):
                            name = self.name_cache.get(uid)
                            if not name:
                                name = await hub.get_name(app, uid)
                                self.name_cache[uid] = name
                            _rank_lines.append(hub.rank_line(index, uid, name, f"：{amount:+d}"))
                else:
                    lines.extend(["", "🎮 娱乐局：本局不计入正式盈亏榜。"])

                self.phase = "finished"
                # ★ 先清待退清单，再存盘（顺序不能反）：
                #   原写法「先存盘 → 再清内存里的 pending」，存盘那一刻 pending 还在存档里，
                #   清完又不再存 —— 若进程恰好在这两步之间被打掉（Railway 重新部署 / OOM），
                #   下次启动 restore.py 会按残留的 pending **再全额退一次**，而奖金早已派发。
                for uid in self.bets: hub.pending_game_bets[self.chat_id].get(uid, {}).pop("horse", None)
                hub.save_data(); await asyncio.to_thread(hub.force_save_now)
                delivered = await hub.safe_send_long(app.bot, self.chat_id, "\n".join(lines), parse_mode="HTML")
                if hub.sget("SETTLE_DELETE_SECONDS") > 0:
                    hub.schedule_delete(app, self.chat_id, delivered, hub.sget("SETTLE_DELETE_SECONDS"))
                await hub.send_settle_rank(app, self.chat_id, _rank_lines)

                if delivered is None:
                    hub.schedule_notice_delete(app, self.chat_id, await hub.safe_send(app.bot, self.chat_id, "⚠️ 赛车已完成结算，但详细结果消息发送失败。积分与当日盈亏已保存，可使用 /cx 查看排行榜。"), kind="settle")
            except Exception:
                hub.logger.exception("赛车结算异常，群 %s", self.chat_id)
                # 仅在尚未派彩时退款，避免已派彩玩家被双重派彩
                if not payouts_applied:
                    self.refund_all()   # 退款与清 pending 必须同生共死，否则重启会再退一次
                    if self.mode == "official": hub.race_jackpot[self.chat_id] = self.jackpot
                    hub.schedule_notice_delete(app, self.chat_id, await hub.safe_send(app.bot, self.chat_id, "⚠️ 赛车结算异常，本局已退款以保护玩家积分。"), kind="settle")
                else:
                    hub.schedule_notice_delete(app, self.chat_id, await hub.safe_send(app.bot, self.chat_id, "⚠️ 赛车结算显示异常，派彩已保存，可使用 /cx 查看排行榜。"), kind="settle")
                hub.save_data()
            finally:
                await hub.safe_delete(app.bot, self.chat_id, self.animation_msg_id)
                # 下注面板此前全程只做原地编辑、从不删除，会一直堆在群里；结算后延迟清理
                hub.schedule_delete_ids(app, self.chat_id, self.game_msg_id, hub.sget("PANEL_DELETE_SECONDS"))
                if hub.active_horse_races.get(self.chat_id) is self: hub.active_horse_races.pop(self.chat_id, None)
                if self.mode == "official":
                    for uid in self.bets: await hub.emergency_if_needed(self.chat_id, uid, app)

    def refund_all(self):
        """全部下注原路退回，并同步清除 pending_game_bets。

        退款与清 pending 必须同生共死：只退钱不清 pending 的话，进程重启时
        load_data 会按残留的 pending 再退一次 = 凭空多出一份积分。
        """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        game_chips = hub.game_chips
        pending_game_bets = hub.pending_game_bets
        for uid, bets in self.bets.items():
            hub.game_chips[self.chat_id][uid] += sum(bets.values())
            hub.pending_game_bets[self.chat_id].get(uid, {}).pop("horse", None)

    async def refund(self, app, notice):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        active_horse_races = hub.active_horse_races
        safe_edit = hub.safe_edit
        save_data = hub.save_data
        schedule_delete_ids = hub.schedule_delete_ids
        sget = hub.sget
        async with self.lock:
            if self.cancelled: return
            self.cancelled, self.phase = True, "cancelled"
            self.refund_all()
            # 奖池不再在开局时弹出，故取消/退款时无需回写（race_jackpot[cid] 始终保留原始奖池）
            hub.save_data()
            if hub.active_horse_races.get(self.chat_id) is self: hub.active_horse_races.pop(self.chat_id, None)
            await hub.safe_edit(app.bot, self.chat_id, self.game_msg_id, notice, reply_markup=None)
            # 取消/退款后的面板同样只留一小会儿，避免残留占位
            hub.schedule_delete_ids(app, self.chat_id, self.game_msg_id, hub.sget("PANEL_DELETE_SECONDS"))


def _race_hour_in_window(start, end, hour):
    """自动开赛时段判定（**支持跨午夜**，2026-09-11 用户报障）。

    群友要的档期是「18 点到凌晨 2 点」，旧实现是 `start <= h <= end`，
    起始大于结束直接恒 False → 跨夜档**根本设不出来**。
    现行语义：start <= end = 普通档（同日区间）；start > end = 跨夜档。
    例：18→2 命中 18/19/.../23/0/1/2，不命中 3~17。
    """
    start = max(0, min(23, int(start)))
    end = max(0, min(23, int(end)))
    hour = int(hour) % 24
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end


def _race_window_text(start, end):
    """时段的可读文案；跨夜档在终点前加「次日」（后台状态卡/页面提示共用）。"""
    start = max(0, min(23, int(start)))
    end = max(0, min(23, int(end)))
    return f"{start:02d}:00–{'次日' if start > end else ''}{end:02d}:59"


def race_subsidy_banner(auto_started=True):
    """赛车面板上的加奖预告（开关关 / 金额 0 / 个人发起 → 空串，不显示多余行）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    if not sget("RACE_SUBSIDY_ENABLED"): return ""
    if sget("RACE_SUBSIDY_AUTO_ONLY") and not auto_started: return ""
    amt = max(0, int(sget("RACE_SUBSIDY_AMOUNT")))
    if amt <= 0: return ""
    n = max(1, int(sget("RACE_SUBSIDY_MIN_PLAYERS")))
    return f"🎁 本场系统加奖 {amt} 积分（押中者按注额分，需 ≥{n} 人下注）"


def race_subsidy_for(cid, date, n_bettors, auto_started=True):
    """本场可发的加奖金额（0 = 不发）。五道闸：开关 → 来源 → 金额 → 人数门槛 → 当日上限。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    race_subsidy_by_day = hub.race_subsidy_by_day
    sget = hub.sget
    if not sget("RACE_SUBSIDY_ENABLED"): return 0
    if sget("RACE_SUBSIDY_AUTO_ONLY") and not auto_started: return 0   # 个人发起的赛车不派奖
    amt = max(0, int(sget("RACE_SUBSIDY_AMOUNT")))
    if amt <= 0: return 0
    if n_bettors < max(1, int(sget("RACE_SUBSIDY_MIN_PLAYERS"))): return 0
    cap = max(0, int(sget("RACE_SUBSIDY_DAILY_CAP")))
    if cap and race_subsidy_by_day[date][cid] + amt > cap: return 0
    return amt


def race_subsidy_split(subsidy, bets, winner):
    """加奖按「押中者的注额比例」拆分，返回 {uid: 金额}。
    整数除法余数补给押注最多的那个人 —— 总额严格等于 subsidy，不许凭空多出/少了积分。"""
    if subsidy <= 0: return {}
    winners = [(uid, bets[uid].get(winner, 0)) for uid in bets if bets[uid].get(winner, 0) > 0]
    if not winners: return {}          # 无人押中 → 不发（额度也不消耗），避免白送钱给庄家
    tot = sum(b for _, b in winners)
    alloc, used = {}, 0
    for uid, b in winners:
        share = subsidy * b // tot
        alloc[uid] = share; used += share
    rest = subsidy - used
    if rest:
        top = max(winners, key=lambda x: x[1])[0]
        alloc[top] = alloc.get(top, 0) + rest
    return alloc


async def cmd_sm(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    HorseRace = hub.HorseRace
    _game_gate = hub._game_gate
    active_horse_races = hub.active_horse_races
    current_game_mode = hub.current_game_mode
    need_auth = hub.need_auth
    panel_adopt = hub.panel_adopt
    race_jackpot = hub.race_jackpot
    require_group_chat = hub.require_group_chat
    safe_send = hub.safe_send
    save_data = hub.save_data
    send_reply = hub.send_reply
    if not await need_auth(update, context): return
    if not await _game_gate(update, context, "race"): return
    if not await require_group_chat(update, "赛车", "sc", context): return
    cid = update.effective_chat.id
    if cid in active_horse_races:
        race = active_horse_races[cid]
        # 已有赛车：直接回应当前进行中的这一局，绝不新开一局
        if getattr(race, "phase", "") == "betting":
            # 防刷屏：短时间内重复发 /赛车 只回一句文字；超过冷却才重发看板（让后进群的人能看到按钮）
            now_ts = time.time()
            if now_ts - float(getattr(race, "panel_cd", 0) or 0) < 15:
                await send_reply(update, context, "当前已有赛车进行中，直接点上方看板下注即可。")
                return
            race.panel_cd = now_ts
            # ★ 重发前先回收旧看板：否则群里会同时存在多张带「下注」按钮的面板，
            #   点旧的那张照样扣分（旧面板不知道有新面板了）。
            if getattr(race, "game_msg_id", None):
                await hub.safe_delete(context.bot, cid, race.game_msg_id)
            msg = await safe_send(context.bot, cid, await race.view(context.application), reply_markup=race.buttons())
            if msg: race.game_msg_id = msg.message_id
        else:
            await send_reply(update, context, "当前已有赛车进行中。")
        return
    mode = current_game_mode()
    jackpot = race_jackpot.get(cid, 0) if mode == "official" else 0
    race = HorseRace(cid, update.effective_user.id, jackpot, mode); active_horse_races[cid] = race
    msg = await safe_send(context.bot, cid, await race.view(context.application), reply_markup=race.buttons())
    if msg:
        race.game_msg_id = msg.message_id
        await panel_adopt(context.bot, cid, msg.message_id)   # 清掉上一场残留牌桌
    race.task = asyncio.create_task(race.run(context.application)); save_data()


async def _auto_race_tick(app, now):
    """自动开赛单轮扫描：总开关开着且到点时，对每个授权群发车。
    每群默认开启；群里发「整点自动赛车」可单独关/开本群。（此前默认关闭+只遍历手动开过的群，
    网页总开关打开也不会发车——bug 已修）"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    HorseRace = hub.HorseRace
    _race_hour_in_window = hub._race_hour_in_window
    active_horse_races = hub.active_horse_races
    current_game_mode = hub.current_game_mode
    hourly_race_enabled = hub.hourly_race_enabled
    logger = hub.logger
    race_jackpot = hub.race_jackpot
    race_last_sent = hub.race_last_sent
    race_skip_stats = hub.race_skip_stats
    safe_send = hub.safe_send
    save_data = hub.save_data
    sget = hub.sget
    if not (sget("RACE_AUTO_ENABLED") and sget("RACE_ENABLED")
            and now.minute == max(0, min(59, sget("RACE_HOURLY_MINUTE")))
            and _race_hour_in_window(sget("RACE_HOURLY_START"), sget("RACE_HOURLY_END"), now.hour)):
        return
    for cid in list(AUTHORIZED_GROUPS):
        if not hourly_race_enabled.get(cid, True):
            race_skip_stats[cid]["群开关关闭"] += 1; continue
        if cid in active_horse_races:
            race_skip_stats[cid]["已有进行中赛车"] += 1; continue
        try:
            mode = current_game_mode()
            jackpot = race_jackpot.get(cid, 0) if mode == "official" else 0
            race = HorseRace(cid, ADMIN_USER_ID, jackpot, mode, auto=True); active_horse_races[cid] = race
            msg = await safe_send(app.bot, cid, await race.view(app), reply_markup=race.buttons())
            if not msg:
                race_skip_stats[cid]["safe_send返回None"] += 1
                active_horse_races.pop(cid, None); continue
            race.game_msg_id = msg.message_id
            race.task = asyncio.create_task(race.run(app))
            race_last_sent[cid] = now.strftime("%Y-%m-%d %H:%M")
            save_data()
        except Exception as exc:
            logger.exception(f"自动开赛 群 {cid} 异常")
            race_skip_stats[cid][f"异常:{type(exc).__name__}"] += 1
            active_horse_races.pop(cid, None)


async def hourly_race_scheduler(app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _auto_race_tick = hub._auto_race_tick
    logger = hub.logger
    now_bj = hub.now_bj
    sget = hub.sget
    last_key = None
    while True:
        try:
            now = now_bj(); key = now.strftime("%Y%m%d%H")
            if key != last_key:  # 每分钟轮询，同一小时只发一轮；开赛分钟/时段均网页可配
                await _auto_race_tick(app, now)
                if sget("RACE_AUTO_ENABLED") and now.minute == max(0, min(59, sget("RACE_HOURLY_MINUTE"))):
                    last_key = key
            next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
            await asyncio.sleep(max(1, (next_minute-now).total_seconds()))
        except Exception:
            logger.exception("hourly_race_scheduler 本轮异常（已吞并继续）")
            await asyncio.sleep(60)
