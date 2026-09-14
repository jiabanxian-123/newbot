# -*- coding: utf-8 -*-
"""feature/jinhua —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

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
import random


def jinhua_seen_mult(seen, double_on):
    """炸金花「看牌加倍」的唯一出处（2026-09-13 集中，原先手抄 3 遍）。

    规则：看牌者下注按 2 倍（开关 JINHUA_SEEN_DOUBLE 关闭时与闷牌同价 1 倍）。
    _target / _do_raise / _do_allin 三处都调这里 —— 以后要改倍率（例如 3 倍、
    或给某类玩家免加倍），**只改这一个函数**，不会再出现「跟注按 2 倍、
    加注按 1 倍」这种三处不同步的账。

    ★ 纯函数：只收两个布尔，不看 self、不查 hub。
        seen      —— 该玩家是否已看牌
        double_on —— 看牌加倍开关是否打开
    返回：1 或 2（下注倍率）
    """
    return 2 if (seen and double_on) else 1


class JinhuaGame:
    """炸金花：3 张暗牌，闷牌（不看牌下注）与看牌（看牌者 2 倍跟），跟平后可开牌或继续加注。"""

    def __init__(self, cid, owner, mode=None):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        current_game_mode = hub.current_game_mode
        self.chat_id, self.owner_id, self.mode, self.phase = cid, owner, mode or hub.current_game_mode(), "waiting"
        self.players, self.chips, self.initial_chips = [], {}, {}
        self.total_bet, self.round_bets = {}, {}
        self.hands = {}        # uid -> [3张牌]
        self.seen = set()      # 已看牌玩家（看牌者下注翻倍）
        self.folded, self.all_in, self.acted, self.raise_locked = set(), set(), set(), set()
        self.deck = []
        self.pot = self.current_bet = self.actor_idx = 0
        self.game_msg_id = None  # 唯一权威牌桌消息（删旧发新：每次行动重发到群最新位置，全群始终只有这一条）
        self.turn_task = self.wait_task = None
        self.settled = False
        self.showdown_order = []
        self.last_compare = None
        self.penalty_log = []
        self.last_action = None          # 牌桌状态行：展示“上一手”动作，取代浮动提示消息
        self.compare_menu_owner = None   # 比牌选人菜单发起者（锁），仅其可点选对手
        self._render_lock = asyncio.Lock()  # 牌桌渲染锁：删旧发新期间防并发导致出现两条牌桌
        self.turn_notice_id = None   # 「轮到谁行动」提醒消息 id（单独一条、60 秒自动回收，见 announce_turn）

    def add(self, uid):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        game_chips = hub.game_chips
        sget = hub.sget
        if self.phase != "waiting" or uid in self.players: return False
        if hub.game_chips[self.chat_id][uid] < hub.sget("MIN_ENTRY_CHIPS"): return False
        self.players.append(uid)
        return True

    def start(self):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        Card = hub.Card
        game_chips = hub.game_chips
        sget = hub.sget
        if len(self.players) < 2: return False
        random.shuffle(self.players)
        self.cancel_wait(); self.folded.clear(); self.all_in.clear(); self.acted.clear(); self.raise_locked.clear(); self.seen.clear()
        self.pot = self.current_bet = 0; self.settled = False
        self.deck = [hub.Card.new(rank + suit) for rank in "23456789TJQKA" for suit in "shdc"]
        random.shuffle(self.deck)
        for uid in self.players:
            self.chips[uid] = hub.game_chips[self.chat_id][uid]; self.initial_chips[uid] = self.chips[uid]
            self.total_bet[uid] = self.round_bets[uid] = 0
            ante = min(hub.sget("JINHUA_ANTE"), self.chips[uid]); self.chips[uid] -= ante; self.total_bet[uid] += ante; self.pot += ante
            if not self.chips[uid]: self.all_in.add(uid)
            self.hands[uid] = [self.deck.pop(), self.deck.pop(), self.deck.pop()]
        self.phase = "betting"
        self.actor_idx = 0
        # 跳过开局即全下(ante 后 0 筹)的玩家，避免首轮无人可行动而卡死
        if self.current() is None:
            self._next()
            if self.current() is None: self.phase = "showdown"
        return True

    def _target(self, uid):
        """该玩家本轮应投入的实际金额 = 闷牌单位 × (看牌 2 倍 / 闷牌 1 倍)。
        加倍开关关闭时看牌与闷牌同价。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        sget = hub.sget
        mult = jinhua_seen_mult(uid in self.seen, hub.sget("JINHUA_SEEN_DOUBLE"))
        return self.current_bet * mult

    def current(self):
        if self.actor_idx >= len(self.players): return None
        uid = self.players[self.actor_idx]
        return uid if uid not in self.folded and uid not in self.all_in and uid not in self.acted else None

    def _next(self):
        n = len(self.players)
        for offset in range(1, n + 1):
            idx = (self.actor_idx + offset) % n
            uid = self.players[idx]
            if uid not in self.folded and uid not in self.all_in and uid not in self.acted:
                self.actor_idx = idx
                return uid
        return None

    def _round_done(self):
        return all(uid in self.folded or uid in self.all_in or uid in self.acted for uid in self.players)

    def _do_raise(self, uid, extra):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        sget = hub.sget
        try: extra = int(extra)
        except (TypeError, ValueError): return False, "无效加注额"
        if extra < hub.sget("JINHUA_BASE"): return False, f"最低加注为 {hub.sget('JINHUA_BASE')}"
        if uid in self.raise_locked: return False, "短全下后已行动玩家只能跟注或弃牌"
        # 先按校验后的值计算目标投入，余额不足直接拒绝，绝不先改 current_bet
        mult = jinhua_seen_mult(uid in self.seen, hub.sget("JINHUA_SEEN_DOUBLE"))
        new_current_bet = self.current_bet + extra
        new_target = new_current_bet * mult
        paid = new_target - self.round_bets[uid]
        if paid > self.chips[uid]:
            return False, f"积分不足：需要 {paid}，你只有 {self.chips[uid]}"
        # 校验通过后才改状态
        self.current_bet = new_current_bet
        self.chips[uid] -= paid; self.round_bets[uid] = new_target; self.total_bet[uid] += paid; self.pot += paid
        if not self.chips[uid]: self.all_in.add(uid)
        self.acted = {uid}; self.phase = "betting"
        # ★ 与德州（features/texas/__init__.py）保持一致：有人**正常加注**后，
        #   之前因「短全下」被锁住的玩家应该重新获得加注权。
        #   原来只清 acted、不清 raise_locked → 被锁的人之后一直只能跟注或弃牌。
        self.raise_locked.clear()
        return True, f"加注 {extra}"

    def _do_allin(self, uid):
        """炸金花全下：不足跟注或部分高出(不足一个单位加注)按 call 处理；超出足够则全下并加注、重开下注。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        sget = hub.sget
        if self.chips[uid] <= 0:
            return False, "你没有可下的筹码"
        mult = jinhua_seen_mult(uid in self.seen, hub.sget("JINHUA_SEEN_DOUBLE"))
        to_call = max(0, self._target(uid) - self.round_bets[uid])
        paid = self.chips[uid]
        if paid <= to_call or (paid - to_call) < mult:
            # 部分跟注：不足一个单位加注时按 call 处理（余下零星筹码保留），不重开下注
            shove = min(paid, to_call)
            self.chips[uid] -= shove
            self.round_bets[uid] += shove
            self.total_bet[uid] += shove
            self.pot += shove
            self.acted.add(uid)
            desc = f"全下 {shove}" if shove else "过牌"
        else:
            # 超出跟注足够：全下并加注，重开下注让其余玩家响应
            excess = paid - to_call
            raise_units = excess // mult
            shove = to_call + raise_units * mult
            self.chips[uid] -= shove
            self.round_bets[uid] += shove
            self.total_bet[uid] += shove
            self.pot += shove
            self.current_bet += raise_units
            prior_actors = self.acted.copy()
            if raise_units * mult < hub.sget("JINHUA_BASE"):
                # 短全下(加注不足一个单位)：已行动者只能跟注/弃牌，不能再加注
                self.raise_locked.update(prior_actors - {uid})
            else:
                self.raise_locked.clear()
            self.acted = {uid}
            desc = f"全下 {shove}"
        if self.chips[uid] == 0:
            self.all_in.add(uid)
        return True, desc

    def action(self, uid, kind, extra=0):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        compare_jinhua_pair = hub.compare_jinhua_pair
        sget = hub.sget
        if kind == "see":
            if uid in self.seen: return False, "你已看过牌"
            self.seen.add(uid)
            return True, "看牌"
        if kind == "compare":
            if uid in self.folded: return False, "你已弃牌"
            if self.phase == "betting" and uid != self.current():
                return False, "还没轮到你"
            if self.phase not in ("betting", "open_pending"):
                return False, "当前无法比牌"
            if extra in (None, uid) or extra in self.folded or extra not in self.players:
                return False, "无效比牌对象"
            alive = [p for p in self.players if p not in self.folded]
            if len(alive) < 2:
                return False, "人数不足，无法比牌"
            # 比牌者需先跟平当前注（若尚未跟平）；看牌者需按 2 倍匹配
            to_call = max(0, self._target(uid) - self.round_bets[uid])
            if to_call > self.chips[uid]:
                return False, f"需先跟注 {to_call} 才能比牌，但积分不足"
            if to_call > 0:
                self.chips[uid] -= to_call; self.round_bets[uid] += to_call
                self.total_bet[uid] += to_call; self.pot += to_call
                if self.chips[uid] == 0: self.all_in.add(uid)
            winner = hub.compare_jinhua_pair(uid, extra, self.hands)
            loser = extra if winner == "challenger" else uid
            penalty = 0
            # 官方规则：看牌者向闷牌者比牌落败 → 倒赔 2 倍底注
            if loser == uid and uid in self.seen and extra not in self.seen:
                penalty = min(2 * hub.sget("JINHUA_BASE"), self.chips[uid])
                self.chips[uid] -= penalty; self.chips[extra] += penalty
                self.penalty_log.append((uid, extra, penalty))
            self.folded.add(loser)
            self.last_compare = (uid, extra, winner, penalty)
            self.acted.add(uid)
            alive = [p for p in self.players if p not in self.folded]
            if len(alive) <= 1:
                self.phase = "showdown"
            elif self._round_done():
                self.phase = "open_pending"
            else:
                self._next()
            return True, f"比牌：{'你胜' if winner == 'challenger' else '你负'}"
        if self.phase == "open_pending":
            if uid in self.folded: return False, "你已弃牌"
            if kind == "open":
                self.phase = "showdown"
                return True, "开牌"
            if kind == "fold":
                # 跟平后也必须允许弃牌止损（此前只开放开牌/加注，玩家被锁死）
                self.folded.add(uid)
                self.acted.add(uid)
                if len([p for p in self.players if p not in self.folded]) <= 1:
                    self.phase = "showdown"
                return True, "弃牌"
            if kind == "raise":
                ok, desc = self._do_raise(uid, extra)
                if not ok: return False, desc
                # 关键：open_pending 里加注后必须重新推进回合，否则 actor_idx 停在
                # 已行动的玩家上 → current() 恒为 None → 无按钮无计时器，整局冻死
                alive2 = [p for p in self.players if p not in self.folded]
                if len(alive2) <= 1: self.phase = "showdown"
                elif self._round_done(): self.phase = "open_pending"
                else: self._next()
                return True, desc
            return False, "当前只能开牌或继续加注"
        if uid != self.current(): return False, "还没轮到你"
        if kind == "fold":
            self.folded.add(uid); desc = "弃牌"
        elif kind == "call":
            target = self._target(uid)
            paid = max(0, target - self.round_bets[uid])
            if paid > self.chips[uid]: return False, f"积分不足：需要 {paid}，你只有 {self.chips[uid]}"
            self.chips[uid] -= paid; self.round_bets[uid] += paid; self.total_bet[uid] += paid; self.pot += paid
            if not self.chips[uid]: self.all_in.add(uid)
            self.acted.add(uid)
            desc = f"跟注 {paid}" if paid else "过牌"
        elif kind == "raise":
            ok, desc = self._do_raise(uid, extra)
            if not ok: return False, desc
        elif kind == "allin":
            ok, desc = self._do_allin(uid)
            if not ok: return False, desc
        else: return False, "未知操作"
        alive = [p for p in self.players if p not in self.folded]
        if len(alive) <= 1:
            self.phase = "showdown"
        elif self._round_done():
            self.phase = "open_pending"
        else:
            self._next()
        return True, desc

    def _hand_name(self, uid):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        JINHUA_HAND_NAMES = hub.JINHUA_HAND_NAMES
        evaluate_jinhua = hub.evaluate_jinhua
        is_235 = hub.is_235
        if hub.is_235(self.hands[uid]): return "235"
        return hub.JINHUA_HAND_NAMES.get(hub.evaluate_jinhua(self.hands[uid])[0], "散牌")

    def jinhua_side_pot_payouts(self):
        """炸金花边池派奖：按总投入分层主池/边池，每层用 jinhua_winners 选赢家(保留 235 专杀)。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        jinhua_winners = hub.jinhua_winners
        side_pots = hub.side_pots
        payouts = defaultdict(lambda: {"amount": 0, "details": []})
        total_pot = sum(self.total_bet.values())
        for index, (amount, contributors) in enumerate(hub.side_pots(self.total_bet)):
            alive_contributors = [u for u in contributors if u not in self.folded]
            if not alive_contributors:
                # 根因：该层贡献者已全部弃牌，原逻辑直接 skip，余额靠全局兜底掩盖分配。
                # 修正：弃牌者的投入仍属底池，归入仍存活的最佳牌型（炸金花规则）。
                alive = [u for u in self.players if u not in self.folded]
                winners = hub.jinhua_winners({u: self.hands[u] for u in alive})
                share, remainder = divmod(amount, len(winners))
                for pos, uid in enumerate(sorted(winners)):
                    won = share + (1 if pos < remainder else 0)
                    payouts[uid]["amount"] += won
                    payouts[uid]["details"].append(("主池" if index == 0 else f"边池{index}", won))
                continue
            winners = hub.jinhua_winners({u: self.hands[u] for u in alive_contributors})
            share, remainder = divmod(amount, len(winners))
            for pos, uid in enumerate(sorted(winners)):
                won = share + (1 if pos < remainder else 0)
                payouts[uid]["amount"] += won
                payouts[uid]["details"].append(("主池" if index == 0 else f"边池{index}", won))
        # 守恒兜底：未被分配的底池归入最佳牌型存活玩家，禁止积分凭空消失
        allocated = sum(item["amount"] for item in payouts.values())
        unallocated = total_pot - allocated
        if unallocated > 0:
            alive = [u for u in self.players if u not in self.folded]
            winners = hub.jinhua_winners({u: self.hands[u] for u in alive})
            share, remainder = divmod(unallocated, len(winners))
            for pos, uid in enumerate(sorted(winners)):
                won = share + (1 if pos < remainder else 0)
                payouts[uid]["amount"] += won
                payouts[uid]["details"].append(("底池兜底", won))
        return payouts

    async def showdown(self):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        force_save_now = hub.force_save_now
        game_chips = hub.game_chips
        save_data = hub.save_data
        alive = [uid for uid in self.players if uid not in self.folded]
        self.showdown_order = alive.copy()
        if len(alive) == 1:
            winner = alive[0]
            self.chips[winner] += self.pot
            for uid in self.players:
                hub.game_chips[self.chat_id][uid] += self.chips[uid] - self.initial_chips.get(uid, hub.game_chips[self.chat_id][uid])
            hub.save_data(); await asyncio.to_thread(hub.force_save_now)
            return [(winner, "最后赢家", self.pot, [("全部底池", self.pot)], {})]
        names = {uid: self._hand_name(uid) for uid in alive}
        payouts = self.jinhua_side_pot_payouts()
        for uid, item in payouts.items():
            self.chips[uid] += item["amount"]
        for uid in self.players:
            hub.game_chips[self.chat_id][uid] += self.chips[uid] - self.initial_chips.get(uid, hub.game_chips[self.chat_id][uid])
        hub.save_data(); await asyncio.to_thread(hub.force_save_now)
        return [(uid, names[uid], payouts[uid]["amount"], payouts[uid]["details"], names) for uid in alive if payouts[uid]["amount"] > 0]

    def cancel_timer(self):
        task, self.turn_task = self.turn_task, None
        if task and task is not asyncio.current_task() and not task.done(): task.cancel()

    def cancel_wait(self):
        task, self.wait_task = self.wait_task, None
        if task and task is not asyncio.current_task() and not task.done(): task.cancel()


async def jinhua_waiting_text(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    sget = hub.sget
    players = [f"{i}. {await get_name(app, uid)}" for i, uid in enumerate(game.players, 1)]
    return f"🌸 新一局炸金花\n发起人：{await get_name(app, game.owner_id)}\n\n已加入：\n" + "\n".join(players) + f"\n\n点击加入，发起人可立即开始。\n⏰ 满 2 人后 {sget('ROOM_WAIT_TIMEOUT')} 秒自动开局，不足 2 人 {sget('ROOM_WAIT_TIMEOUT')} 秒后自动解散。"


async def update_jinhua_waiting(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    jinhua_waiting_text = hub.jinhua_waiting_text
    safe_edit = hub.safe_edit
    rows = [[InlineKeyboardButton("📥 加入游戏", callback_data="jh_join")]]
    if len(game.players) >= 2: rows.append([InlineKeyboardButton("🎮 开始游戏", callback_data="jh_start")])
    rows.append([InlineKeyboardButton("❌ 终止房间", callback_data="jh_end")])
    await safe_edit(app.bot, game.chat_id, game.game_msg_id, await jinhua_waiting_text(game, app), reply_markup=InlineKeyboardMarkup(rows))


async def jinhua_table_text(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    player_line = hub.player_line
    sget = hub.sget
    lines = [
        "🌸 炸金花",
        f"💰 奖池 {game.pot}｜单注 {game.current_bet}" + ("（看牌者×2）" if sget("JINHUA_SEEN_DOUBLE") else ""),
    ]
    if game.last_action:
        lines.append(f"🔔 上一手：{game.last_action}")
    current = game.current() if game.phase == "betting" else None
    lines.append("━━━━━━━━━━━━━━━━━")
    # 行动提示（`⏳ 当前行动` / `⏰ 请在 N 秒内行动`）2026-09-12 已**整体移出牌桌** →
    # 改由 start_jinhua_turn_timer 调 announce_turn() 单独发一条（60 秒自动回收）。
    # ⛔ 别再往牌桌加回行动行（会与提醒消息重复说同一件事）。
    # 紧凑排版：每人 1 行；版式统一走 player_line()（徽标前置 + 序号对齐）。
    #   在局 `👉 🟢1. 名 👁 投100 余900` ｜ 弃牌 `{PLAYER_PAD}❌2. 名 🎴 投100 余900`
    # 徽标只留 1 个 emoji（🟢/❌/🔥），不再写「弃 / 全下」；看牌标记 👁/🎴 属于**信息列**，
    # 留在名字后面 —— 与徽标隔着「序号. 名字」一整段，不会出现「🟢👁」连着读成两个状态。
    for index, uid in enumerate(game.players, 1):
        badge = "❌" if uid in game.folded else "🔥" if uid in game.all_in else "🟢"
        seen_mark = "👁" if uid in game.seen else "🎴"
        lines.append(player_line(
            index, await get_name(app, uid), badge, acting=(uid == current),
            cols=[seen_mark, f"投{game.total_bet[uid]}", f"余{game.chips[uid]}"]))
    return "\n".join(lines)


async def jinhua_turn_notice(game, app):
    """炸金花「轮到谁」提醒文案（单独一条消息，见 announce_turn）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    sget = hub.sget
    if game.phase != "betting": return None
    uid = game.current()
    if uid is None: return None
    need = max(0, game._target(uid) - game.round_bets[uid])
    tail = f"｜需补 {need}" if need else "｜可过牌"
    return (f"⏳ <b>{await get_name(app, uid)}</b> 轮到你行动{tail}\n"
            f"⏰ 请在 {sget('TURN_TIMEOUT')} 秒内操作，超时自动弃牌")


def jinhua_buttons(game, uid):
    """紧凑布局：非行动玩家仅「看牌」；行动玩家 4 行
    （看牌单独首行 → 弃牌|跟注|比牌 → 加注|刷新 → 全下）。
    2026-09-11 用户要求（防误触）：看牌独占首行、弃牌退到第二行最左，
    与高频的跟注/加注拉开距离。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    if uid not in game.folded:
        label = "🃏 手牌" if uid in game.seen else "👁 看牌"   # 统一 emoji+2字（同行等宽，2026-09-10 对齐改造）
        if uid != game.current():
            return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="jh_see")]])
    else:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 刷新界面", callback_data="jh_refresh")]])
    to_call = max(0, game._target(uid) - game.round_bets[uid])
    # 2026-09-11 用户要求撤销「emoji+2字」改造：金额回到按钮上（快捷档行已删）
    # 2026-09-11 用户要求（防误触）：看牌独占首行；弃牌退到第二行最左，跟注/比牌在其右
    rows = [[InlineKeyboardButton(label, callback_data="jh_see")]]
    row_call = [InlineKeyboardButton("❌ 弃牌", callback_data="jh_fold"),
                InlineKeyboardButton("✅ 过牌" if not to_call else "✅ 跟注", callback_data="jh_call")]
    if sum(1 for p in game.players if p not in game.folded) >= 2:
        row_call.append(InlineKeyboardButton("⚔️ 比牌", callback_data="jh_compare_menu"))
    rows.append(row_call)
    row_raise = []
    if uid not in game.raise_locked and game.chips[uid] >= to_call + sget("JINHUA_BASE"):
        row_raise.append(InlineKeyboardButton(f"🚀 加注 {sget('JINHUA_BASE')}", callback_data=f"jh_raise_{sget('JINHUA_BASE')}"))
    row_raise.append(InlineKeyboardButton("🔄 刷新", callback_data="jh_refresh"))
    rows.append(row_raise)
    if game.chips[uid] > 0:
        rows.append([InlineKeyboardButton(f"🔥 全下 {game.chips[uid]}", callback_data="jh_allin")])
    return InlineKeyboardMarkup(rows)


async def _sync_jinhua_msg(game, app, text, kb, edit_only=False):
    """渲染唯一权威牌桌消息：**删旧发新**，全群始终只有这一条，且永远停在群最底部。

    2026-09-11 用户明确：炸金花与大话骰**统一删旧发新**。
    （早前「原地编辑」的注释把「群友反馈乱跳」归因成删旧发新——**那是误记**：
    用户当时的抱怨是界面文本与按钮乱，跟发消息方式无关。）
    原地编辑位置不动，会被后来的聊天顶上去，群友就看不到轮到谁了。
    加锁避免快速连续操作（连点/超时与点击并发）时出现两条牌桌。

    edit_only=True：**原地编辑、绝不删除**。用于「看牌」这类非轮次推进的动作——
    删旧发新会让群里刷出「X 删除了消息」的系统提示，玩家根本不知道发生了什么
    （2026-09-11 用户报障）。看牌只更新自己那一行，下一个人操作时才走删旧发新。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _live_panel_msg = hub._live_panel_msg
    safe_delete = hub.safe_delete
    safe_edit = hub.safe_edit
    safe_send = hub.safe_send
    async with game._render_lock:
        if edit_only and game.game_msg_id:
            res = await safe_edit(app.bot, game.chat_id, game.game_msg_id, text,
                                  reply_markup=kb, parse_mode="HTML")
            if res is not None:
                return
        old_id = game.game_msg_id
        # 本群上一张牌桌：可能属于已被换掉的旧 game（换局/重开后残留的孤儿）
        orphan_id = _live_panel_msg.get(game.chat_id)
        msg = await safe_send(app.bot, game.chat_id, text, reply_markup=kb, parse_mode="HTML")
        if msg:
            game.game_msg_id = msg.message_id
            _live_panel_msg[game.chat_id] = msg.message_id
            if old_id and old_id != game.game_msg_id:
                await safe_delete(app.bot, game.chat_id, old_id)
            # 孤儿牌桌不属于当前 game 对象，但同样占着群里的位置，必须一起清掉
            if orphan_id and orphan_id not in (game.game_msg_id, old_id):
                await safe_delete(app.bot, game.chat_id, orphan_id)


async def update_jinhua_table(game, app):
    # 收敛为唯一牌桌消息：直接复用 show_jinhua_action（含当前操作者按钮），原地编辑不再删旧发新
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    show_jinhua_action = hub.show_jinhua_action
    await show_jinhua_action(game, app)


async def show_jinhua_action(game, app, edit_only=False):
    """渲染唯一权威牌桌消息（牌桌文本 + 当前操作者按钮）。

    三种视图：比牌选人菜单 / 跟平阶段全员开牌·继续加注 / 下注阶段当前玩家行动。
    edit_only=True 时只原地编辑、不删旧发新（看牌专用，见 _sync_jinhua_msg 注释）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _sync_jinhua_msg = hub._sync_jinhua_msg
    get_name = hub.get_name
    jinhua_buttons = hub.jinhua_buttons
    jinhua_table_text = hub.jinhua_table_text
    settle_jinhua = hub.settle_jinhua
    sget = hub.sget
    show_jinhua_action = hub.show_jinhua_action
    start_jinhua_turn_timer = hub.start_jinhua_turn_timer
    if game.compare_menu_owner is not None:
        owner = game.compare_menu_owner
        targets = [p for p in game.players if p not in game.folded and p != owner]
        rows = []
        for t in targets:
            tname = await get_name(app, t)
            tmark = "👁" if t in game.seen else "🎴"
            rows.append([InlineKeyboardButton(f"{tmark} {tname}", callback_data=f"jh_pk_{t}")])
        rows.append([InlineKeyboardButton("❌ 取消", callback_data="jh_cancel_pk")])
        text = f"{await jinhua_table_text(game, app)}\n\n⚔️ {await get_name(app, owner)} 选择比牌对手（仅比牌双方亮牌，其余玩家看不到牌面）："
        await _sync_jinhua_msg(game, app, text, InlineKeyboardMarkup(rows), edit_only=edit_only)
        return
    if game.phase == "open_pending":
        text = f"{await jinhua_table_text(game, app)}\n\n💡 已跟平：可 <b>弃牌</b> 止损、<b>比牌/开牌</b> 定胜负，或 <b>继续加注</b> 偷鸡。"
        # 2026-09-13 用户报障（截图：「🃏 开牌比大/」被截断）：
        # 一行塞 3 个按钮时每列只有 1/3 宽，长标签必被 Telegram 截掉。
        # 这里把标签压到 5 字以内（「继续加注」→「加注」），3 列也能完整显示。
        # 仍保持 2 行 —— 这是 2026-09-11 定的紧凑布局，别为了对齐拆成 3 行。
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ 弃牌", callback_data="jh_fold"),
             InlineKeyboardButton("⚔️ 比牌", callback_data="jh_compare_menu"),
             InlineKeyboardButton("🃏 开牌", callback_data="jh_open")],
            [InlineKeyboardButton(f"🚀 加注 {sget('JINHUA_BASE')}", callback_data=f"jh_raise_{sget('JINHUA_BASE')}"),
             InlineKeyboardButton("🔄 刷新", callback_data="jh_refresh")],
        ])
        await _sync_jinhua_msg(game, app, text, kb, edit_only=edit_only)
        return
    uid = game.current()
    if uid is None:
        if game.phase == "showdown": await settle_jinhua(game, app)
        elif game.phase == "betting":
            # 自愈兜底：行动指针悬空时自动推进，任何路径都不允许牌局无声冻死
            if game._round_done():
                game.phase = "open_pending"
                await show_jinhua_action(game, app)
            else:
                nxt = game._next()
                if nxt: await start_jinhua_turn_timer(game, app)
        return
    # 行动提示已并入牌桌文本（见 jinhua_table_text），此处不再追加第二句
    await _sync_jinhua_msg(game, app, await jinhua_table_text(game, app), jinhua_buttons(game, uid),
                           edit_only=edit_only)


async def start_jinhua_turn_timer(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    JINHUA_OPEN_PENDING_TIMEOUT = hub.JINHUA_OPEN_PENDING_TIMEOUT
    announce_turn = hub.announce_turn
    get_name = hub.get_name
    jinhua_turn_notice = hub.jinhua_turn_notice
    settle_jinhua = hub.settle_jinhua
    sget = hub.sget
    show_jinhua_action = hub.show_jinhua_action
    start_jinhua_turn_timer = hub.start_jinhua_turn_timer
    game.cancel_timer()
    await show_jinhua_action(game, app)
    if game.phase in ("open_pending", "showdown"):
        # open_pending 兜底：跟平阶段无"当前玩家"可计时，若全员不动（如掉线）牌局会永久卡死。
        # 挂一个看门狗：超时后仍处于 open_pending 则自动开牌结算（settle_jinhua 幂等，重复触发无害）。
        if game.phase == "open_pending":
            async def _open_pending_timeout():
                await asyncio.sleep(max(1, int(hub.sget("JINHUA_OPEN_PENDING_TIMEOUT") or hub.JINHUA_OPEN_PENDING_TIMEOUT)))
                if game.settled or game.phase != "open_pending": return
                game.last_action = "跟平阶段超时，自动开牌结算"
                await hub.settle_jinhua(game, app)
            game.turn_task = asyncio.create_task(_open_pending_timeout())
        return
    uid = game.current()
    # 行动提醒：单独一条 + 60 秒自动删除（2026-09-12 用户要求，牌桌正文里已不再写行动行）
    game.turn_notice_id = await announce_turn(app, game.chat_id, await jinhua_turn_notice(game, app),
                                              old_id=game.turn_notice_id)

    async def timeout_action():
        await asyncio.sleep(hub.sget("TURN_TIMEOUT"))
        if game.settled or game.phase == "showdown": return
        if game.phase == "open_pending": return
        if game.current() != uid: return
        game.action(uid, "fold")
        game.last_action = f"{await hub.get_name(app, uid)} 超时弃牌"
        if game.phase == "showdown":
            await hub.settle_jinhua(game, app)
        else:
            await hub.start_jinhua_turn_timer(game, app)
    game.turn_task = asyncio.create_task(timeout_action())


async def _refresh_jinhua_table(game, app):
    """炸金花刷新界面：原地重编辑唯一牌桌消息（无副作用，仅恢复当前 phase 的视图与 timer）。

    供 on_text 的「刷新」分支与 on_button 的 jh_refresh 复用。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    start_jinhua_turn_timer = hub.start_jinhua_turn_timer
    game.cancel_timer()
    await start_jinhua_turn_timer(game, app)
    return None


async def settle_jinhua(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    RANK_PAGE_SIZE = hub.RANK_PAGE_SIZE
    _check_level_change = hub._check_level_change
    _earn_add = hub._earn_add
    _earn_get = hub._earn_get
    active_jinhua_games = hub.active_jinhua_games
    broadcast_big_win = hub.broadcast_big_win
    business_date = hub.business_date
    calc_rake = hub.calc_rake
    card_str = hub.card_str
    commit_rake = hub.commit_rake
    emergency_if_needed = hub.emergency_if_needed
    force_save_now = hub.force_save_now
    games_played = hub.games_played
    get_name = hub.get_name
    jinhua_profit_by_date = hub.jinhua_profit_by_date
    logger = hub.logger
    rank_line = hub.rank_line
    record_game_flows = hub.record_game_flows
    safe_delete = hub.safe_delete
    safe_send = hub.safe_send
    safe_send_long = hub.safe_send_long
    save_data = hub.save_data
    schedule_delete = hub.schedule_delete
    schedule_notice_delete = hub.schedule_notice_delete
    send_settle_rank = hub.send_settle_rank
    sget = hub.sget
    user_wallet_locks = hub.user_wallet_locks
    if game.settled: return
    game.settled = True; game.cancel_timer(); game.cancel_wait()
    try:
        async with user_wallet_locks([uid for uid in game.players if uid >= 0]):
            result = await game.showdown()
        if not result: raise RuntimeError("炸金花开牌未生成结算结果")
        date, hand_types = business_date(), result[0][4]
        name_ids = set(game.players) | set(game.showdown_order)
        names = {uid: await get_name(app, uid) for uid in name_ids}
        lines = ["🌸 <b>炸金花结算</b>", "━━━━━━━━━━━━━━━━━", ""]
        lines.append("亮牌：")
        for uid in game.players:
            if uid in game.folded:
                lines.append(f"　{names[uid]}：弃牌")
            else:
                cards = "  ".join(card_str(c) for c in game.hands[uid])
                lines.append(f"　{names[uid]}：{cards}｜{hand_types.get(uid, '')}")
        lines.append("")
        lines.append("派奖：")
        for uid, hand, amount, details, _ in sorted(result, key=lambda item: item[2], reverse=True):
            if amount > 0:
                lines.append(f"　{names[uid]}：{hand}｜+{amount}（{'，'.join(f'{pool}+{value}' for pool, value in details)}）")
        # 抽水先算（官方模式），面板「盈亏」行直接带实收
        _nets = {uid: game.chips[uid] - game.initial_chips[uid] for uid in game.players} \
            if game.mode == "official" else {}
        rake_per = calc_rake(_nets)[1] if _nets else {}
        lines.append("")
        lines.append("投入 / 盈亏：")
        for uid in game.players:
            net = game.chips[uid] - game.initial_chips[uid]
            if game.mode == "official":
                jinhua_profit_by_date[date][game.chat_id][uid] += net
            r_amt = rake_per.get(uid, 0)
            r_txt = f"（实收 {net - r_amt}，含抽水{r_amt}）" if r_amt else ""
            lines.append(f"　{names[uid]}：投入 {game.total_bet[uid]}｜盈亏 {net:+d}{r_txt}")
        # 资金流审查：官方模式把本局人对人净转移记账（防"故意输牌/比牌倒赔送分"）
        if game.mode == "official":
            record_game_flows(game.chat_id, _nets, "金花")
            await commit_rake(app, game.chat_id, rake_per, "金花")
            # 累计参与局数（归零门槛）+ 赢分计入累计积分 + 升级通知
            for uid in game.players:
                if uid < 0: continue
                games_played[game.chat_id][uid] += 1
                # 实际到手 = 净赢 - 本局抽水（抽水已在上面扣除）
                _gain = (game.chips[uid] - game.initial_chips.get(uid, 0)) - int(rake_per.get(uid, 0) or 0)
                if _gain > 0:
                    _oe = _earn_get(game.chat_id, uid)
                    _earn_add(game.chat_id, uid, _gain)
                    await _check_level_change(app, game.chat_id, uid, _oe, _earn_get(game.chat_id, uid))
        # 大奖战报：官方模式单局净赢超阈值 → 广播其他授权群（豹子特别标注）
        if game.mode == "official":
            top_uid, top_net = None, 0
            for uid in game.players:
                _n = game.chips[uid] - game.initial_chips[uid]
                if _n > top_net: top_uid, top_net = uid, _n
            if top_uid and top_net > 0:
                _ht = str(hand_types.get(top_uid, ""))
                detail = f"🃏 牌型：{_ht}{' 🔥豹子！' if '豹子' in _ht else ''}"
                await broadcast_big_win(app, game.chat_id, top_uid, "♣️ 炸金花", top_net, detail)
        if getattr(game, "penalty_log", []):
            lines.extend(["", "比牌惩罚："])
            for payer, payee, amount in game.penalty_log:
                lines.extend([f"{names.get(payer, str(payer))} 倒赔 {amount} 给 {names.get(payee, str(payee))}", ""])
        # 累计盈利榜单独发一条（2026-09-11 用户要求：结算正文太长像刷屏，榜单拆开发）
        _rank_lines = None
        if game.mode == "official":
            rank = sorted(jinhua_profit_by_date[date][game.chat_id].items(), key=lambda item: item[1], reverse=True)[:RANK_PAGE_SIZE]
            if rank:
                _rank_lines = ["🏆 <b>当日炸金花累计盈利榜</b>", "━━━━━━━━━━━━━━━━━"]
                _rank_lines.extend([rank_line(index, uid, names.get(uid) or await get_name(app, uid), f"：{amount:+d}") for index, (uid, amount) in enumerate(rank, 1)])
        await safe_delete(app.bot, game.chat_id, game.game_msg_id)
        delivered = await safe_send_long(app.bot, game.chat_id, "\n".join(lines), parse_mode="HTML")
        if sget("SETTLE_DELETE_SECONDS") > 0:
            schedule_delete(app, game.chat_id, delivered, sget("SETTLE_DELETE_SECONDS"))
        await send_settle_rank(app, game.chat_id, _rank_lines)
        if delivered is None:
            schedule_notice_delete(app, game.chat_id, await safe_send(app.bot, game.chat_id, "⚠️ 炸金花已完成结算，但详细结算消息发送失败。"), kind="settle")
    except Exception:
        logger.exception("炸金花结算异常")
    finally:
        if active_jinhua_games.get(game.chat_id) is game: active_jinhua_games.pop(game.chat_id, None)
        if game.mode == "official":
            for uid in game.players: await emergency_if_needed(game.chat_id, uid, app)
        save_data(); await asyncio.to_thread(force_save_now)


async def start_jinhua_wait_timeout(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_jinhua_games = hub.active_jinhua_games
    refund_jinhua = hub.refund_jinhua
    sget = hub.sget
    start_jinhua_turn_timer = hub.start_jinhua_turn_timer
    update_jinhua_table = hub.update_jinhua_table
    game.cancel_wait()
    async def countdown():
        _wait = hub.sget("ROOM_WAIT_TIMEOUT")
        await asyncio.sleep(_wait)
        if game.phase != "waiting" or hub.active_jinhua_games.get(game.chat_id) is not game:
            return
        if len(game.players) >= 2:
            if game.start():
                await hub.update_jinhua_table(game, app)
                await hub.start_jinhua_turn_timer(game, app)
        else:
            await hub.refund_jinhua(game, app, f"⌛ 炸金花等待 {_wait} 秒不足 2 人，房间已自动解散。")
    game.wait_task = asyncio.create_task(countdown())


async def refund_jinhua(game, app, notice):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_jinhua_games = hub.active_jinhua_games
    safe_delete = hub.safe_delete
    safe_send = hub.safe_send
    save_data = hub.save_data
    schedule_notice_delete = hub.schedule_notice_delete
    game.cancel_timer(); game.cancel_wait()
    game.phase = "cancelled"
    if active_jinhua_games.get(game.chat_id) is game:
        active_jinhua_games.pop(game.chat_id, None)
    await safe_delete(app.bot, game.chat_id, game.game_msg_id)
    # 解散提示挂自动回收：此前是裸 safe_send，「已终止」永久堆在群里
    schedule_notice_delete(app, game.chat_id, await safe_send(app.bot, game.chat_id, notice))
    save_data()


async def cmd_jinhua(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    JinhuaGame = hub.JinhuaGame
    _game_gate = hub._game_gate
    active_jinhua_games = hub.active_jinhua_games
    current_game_mode = hub.current_game_mode
    game_chips = hub.game_chips
    jinhua_waiting_text = hub.jinhua_waiting_text
    need_auth = hub.need_auth
    panel_adopt = hub.panel_adopt
    poker_room_of = hub.poker_room_of
    require_group_chat = hub.require_group_chat
    safe_send = hub.safe_send
    send_reply = hub.send_reply
    sget = hub.sget
    start_jinhua_wait_timeout = hub.start_jinhua_wait_timeout
    update_jinhua_waiting = hub.update_jinhua_waiting
    if not await need_auth(update, context): return
    if not await _game_gate(update, context, "jinhua"): return
    if not await require_group_chat(update, "炸金花", "jinhua", context): return
    cid, uid = update.effective_chat.id, update.effective_user.id
    game = active_jinhua_games.get(cid)
    room_name, _ = poker_room_of(cid, uid, exclude_game=game)
    if room_name:
        await send_reply(update, context, f"⚠️ 你已在 {room_name} 房间，请先结束再开新的扑克游戏。"); return
    mode = game.mode if game and game.phase == "waiting" else current_game_mode()
    if game_chips[cid][uid] < sget("MIN_ENTRY_CHIPS"):
        await send_reply(update, context, f"❌ 进入炸金花至少需要 {sget('MIN_ENTRY_CHIPS')} 积分。"); return
    if game:
        if game.phase != "waiting": await send_reply(update, context, "当前已有进行中的炸金花。"); return
        if game.add(uid):
            await update_jinhua_waiting(game, context.application); await send_reply(update, context, "已加入当前等待房间。")
        else: await send_reply(update, context, "你已在等待房间中。")
        return
    game = JinhuaGame(cid, uid, mode); game.add(uid); active_jinhua_games[cid] = game
    msg = await safe_send(context.bot, cid, await jinhua_waiting_text(game, context.application), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 加入游戏", callback_data="jh_join")], [InlineKeyboardButton("❌ 终止房间", callback_data="jh_end")]]))
    if msg:
        game.game_msg_id = msg.message_id
        await panel_adopt(context.bot, cid, msg.message_id)   # 清掉上一局残留牌桌
        await start_jinhua_wait_timeout(game, context.application)
