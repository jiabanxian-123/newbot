# -*- coding: utf-8 -*-
"""feature/texas —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 新界面渲染层（2026-09-14 用户拍板「界面改新版」）：纯函数出 PNG bytes，
# 任何图片环节失败由调用方回退文本，永不挡牌局 —— 见 img.py 模块注释。
from .img import _font, _suit_rank, _png, _rrect, _card, _badges
from .img import card_face, hand_popup, reveal_img, showdown_img, table_img

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from contextlib import asynccontextmanager
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
from treys import Card, Evaluator
import asyncio
import random
import time

def player_line(index, name, badge="", acting=False, cols=(), sep=" "):
    """四款游戏（德州 / 21点 / 炸金花 / 大话骰）玩家行的**唯一**渲染出口。

    版式：`{行动标记}{徽标}{序号}. {名字}{分隔}{信息列…}`

      · 行动标记：`PLAYER_MARK`(👉 ) / `PLAYER_PAD`(盲文空格 U+2800 补位，非空白字符不会被客户端裁切)。两者视觉等宽
        ⇒ 不管是不是行动者，**序号永远落在同一列**（别再手写空格，见上面注释的根因）。
      · 徽标：**恒定在最前**，且只用 1 个 emoji（等宽，不会把序号列推歪）。
          🟢 在局 ｜ ❌ 弃牌·爆牌 ｜ 🔥 全下 ｜ 💀 出局 ｜ ✋ 已停牌
        2026-09-12 用户二选一后拍板「徽标前置」：眼睛扫第一列就知道谁出局了，
        不必逐行读到名字中段；徽标本身就说明了状态，所以不再画蛇添足写「在局 / 弃牌 / 已出局」。
      · 信息列（👁看牌 / 🎲点数 / 手牌+点数 / 投注 / 余额）：**留在名字后面**。
        徽标与信息列之间隔着「序号. 名字」一整段，所以不会出现 `🟢👁1. 名`
        那种两个状态挤在一起、读起来像两种状态的歧义。
      · `cols` 里的空值自动跳过（例如某些局面没有第二信息列），不会留下连续分隔符。

    ⛔ 别再改回「名字在前 + 状态挂名字/信息列后面」：用户已明确否掉，四款必须一致。
       （旧写法把状态徽标埋在中段 —— 顺序是「名字 → 第二信息列 → 状态 → 投注 → 余额」，
        眼睛必须读到中段才知道谁出局了；徽标前置后扫第一列即可。）
    参数都是纯值、不碰 game 对象 ⇒ 单测可以直接验版式（见 test_player_row_unified.py）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    PLAYER_MARK = hub.PLAYER_MARK
    PLAYER_PAD = hub.PLAYER_PAD
    mark = PLAYER_MARK if acting else PLAYER_PAD
    row = f"{mark}{badge}{index}. {name}"
    for col in cols:
        if col not in (None, ""):
            row += f"{sep}{col}"
    return row


async def panel_adopt(bot, cid, msg_id):
    """登记本群当前牌桌消息，并清掉上一张。

    换局时新 game 对象的 `game_msg_id` 是 None，**结构上碰不到上一局的牌桌**；
    而结算走延迟删除，窗口期内旧牌桌仍可见可点，点一次又刷一条 →
    这就是「连续多条重复牌桌」的成因（2026-09-12 用户截图，德州 4 条）。
    所有「开新局」的地方发完等待房牌桌后都应调用本函数接管本群面板。

    返回被清掉的上一张 id（没有则 None）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _live_panel_msg = hub._live_panel_msg
    safe_delete = hub.safe_delete
    prev = _live_panel_msg.get(cid)
    _live_panel_msg[cid] = msg_id
    if prev and prev != msg_id:
        await safe_delete(bot, cid, prev)
    return prev


@asynccontextmanager
async def user_wallet_locks(uids):
    """按 uid 全局有序获取多个用户钱包锁，避免死锁。用于多人结算（牛牛/德州等）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    wallet_locks = hub.wallet_locks
    uids = sorted(set(uids))
    for uid in uids:
        await wallet_locks[uid].acquire()
    try:
        yield
    finally:
        for uid in reversed(uids):
            wallet_locks[uid].release()


def current_game_mode():
    """统一正式模式（已移除娱乐时段）。"""
    return "official"


async def retire_panel(app, cid, msg_id, text, seconds=None):
    """把一张**常驻**面板/卡片原地改写成一条**短命**状态提示，并排入自动回收。

    【为什么必须有这个函数】（2026-09-12 用户第 N 次报「21点这个不自动删除」）
    上面那套「默认自动删除」是挂在 `Bot.send_*` 上的 ⇒ 只对**新发送**的消息生效。
    而用 `safe_edit(..., reply_markup=None)` 把常驻面板**改写**出来的提示
    **根本不经过 send**，于是永远不会被回收 —— 用户截图里 07:51 / 08:07 两条
    「21点等待 60 秒无人加入，房间已自动解散。」一直挂到 08:45 就是这个原因。
    ⚠️ 同一处「编辑了但没排程」在本文件里出现过 6 次：
      21点等待解散 / `/end bj` / `bj_end` 按钮 / 红包过期 / 红包「已被抢完」卡片 /
      （原「封盘看板」一类条目已随对应模块于 2026-09-13 删除而移除。）
    ⇒ **统一走本函数**，别再手写 `safe_edit(..., reply_markup=None)`。
       若某张面板本来就该长期常驻（如赛马「正在奔跑中」的开赛帧），
       请在该行加 `# autodel-keep` 注释显式说明理由
       —— `test_autodel_edit_sweep.py` 会全文件扫描并拦住漏网的下一处。

    ⚠️ **先排程、后编辑**：`safe_edit` 失败时旧面板还在（更该收掉），
       而且极端情况下异常也不会把排程一起吞掉。
    seconds=None → 用 `PANEL_DELETE_SECONDS`（与 `refund_poker` / 赛车 `refund` 一致）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    safe_edit = hub.safe_edit
    schedule_delete_ids = hub.schedule_delete_ids
    sget = hub.sget
    secs = sget("PANEL_DELETE_SECONDS") if seconds is None else seconds
    try:
        secs = int(secs or 0)
    except (TypeError, ValueError):
        secs = 0
    if secs > 0 and msg_id:
        schedule_delete_ids(app, cid, msg_id, secs)
    await safe_edit(app.bot, cid, msg_id, text, reply_markup=None)


class PokerGame:
    def __init__(self, cid, owner, mode=None, season=False):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        current_game_mode = hub.current_game_mode
        now_bj = hub.now_bj
        self.chat_id, self.owner_id, self.mode, self.phase = cid, owner, mode or hub.current_game_mode(), "waiting"
        self.season = season  # 赛季模式：用独立 season_points 下注，单手总投入不封顶
        self.players, self.chips, self.initial_chips = [], {}, {}
        self.total_bet, self.round_bets, self.hands = {}, {}, {}
        self.folded, self.all_in, self.acted = set(), set(), set()
        # 短全下抬高下注额时，已行动者必须补齐或弃牌，但不能再次加注。
        self.raise_locked = set()
        # 2026-09-13 用户要求（防误触）：全下 / 跟注 要「再点一次」确认。
        # uid -> (action, extra, ts)。存在局对象上而不是模块级常量：
        # 抽缝模块的模块级名字必须被 bot.py re-export，局内临时状态不该占那个位置。
        self.pending_confirm = {}
        # 2026-09-13 用户要求（提前操作）：**还没轮到我**也能先把「过牌 / 弃牌」按下去，
        # 轮到时自动执行，不用一直盯着屏幕等自己那一手。uid -> "check" | "fold"。
        # 规则（用户原话）：预过牌 = 轮到时**没人加注**才过牌、有人加注则改为弃牌；
        #                 预弃牌 = 轮到时一律弃牌。
        # 换新街（_end_round）/ 新一局（start）时**清空** —— 上一街预留的动作不能跨街生效，
        # 否则玩家会莫名其妙在下一街被自动弃牌。
        self.pre_action = {}
        self.board, self.deck, self.active = [], [], []
        self.pot = self.current_bet = self.actor_idx = self.dealer_idx = 0
        self.game_msg_id = self.action_msg_id = None
        self.turn_task = self.auto_task = self.wait_task = None
        # 本回合倒计时「是什么时候挂上去的」：看门狗据此判断牌局是不是真的没人管了
        # （刚渲染完的那个瞬间计时器还没建好，不能误判成冻结，见 _poker_watchdog_tick）
        self.turn_started_at = 0.0
        # 牌桌渲染锁：删旧发新期间防并发（连点 / 超时与点击撞一起）导致群里出现两条牌桌。
        # 2026-09-12 与炸金花/大话骰统一补上（那两个游戏早就有，德州漏了）。
        self._render_lock = asyncio.Lock()
        self.turn_notice_id = None   # 「轮到谁行动」提醒消息 id（单独一条、60 秒自动回收，见 announce_turn）
        self.evaluator, self.settled, self.showdown_order = Evaluator(), False, []
        self.max_total_bet = None  # 赛季单局每人投入上限（仅 season，start 时按总筹码×百分比算）
        self.start_date = hub.now_bj().strftime("%Y-%m-%d")  # 开局业务日，用于赛季跨午夜补重置判断

    # ---------- 规则参数（赛季局可独立配置，0/未填 = 沿用日常德州） ----------
    # ★ 2026-09-14：取值统一走 `hub._season_param(游戏, 键)` —— 赛季「参数表」唯一入口，
    #   表在 `features/season/__init__.py::SEASON_PARAM_TABLE`。加赛季参数只改表，不改这里。
    @property
    def min_raise(self):
        """最低加注额。"""
        return hub._season_param(self, "fixed_min_raise")

    @property
    def turn_timeout(self):
        """单回合思考时间（秒）。"""
        return hub._season_param(self, "turn_timeout")

    @property
    def wait_timeout(self):
        """等待房倒计时（秒）。"""
        return hub._season_param(self, "room_wait_timeout")

    @property
    def ante_value(self):
        """前注。"""
        return hub._season_param(self, "ante")

    @property
    def small_blind_value(self):
        """小盲注。"""
        return hub._season_param(self, "small_blind")

    @property
    def big_blind_value(self):
        """大盲注。"""
        return hub._season_param(self, "big_blind")

    def add(self, uid):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        game_chips = hub.game_chips
        season_joined = hub.season_joined
        season_points = hub.season_points
        sget = hub.sget
        if self.phase != "waiting" or uid in self.players: return False
        if self.season:
            # 赛季：必须已报名、且赛季分 > 0（可选再加一道「入座最低赛季分」门槛）
            if uid not in hub.season_joined.get(self.chat_id, set()):
                return False
            wallet = hub.season_points
            if wallet[self.chat_id][uid] <= 0: return False
            if hub.sget("SEASON_MIN_ENTRY_CHIPS") and wallet[self.chat_id][uid] < hub.sget("SEASON_MIN_ENTRY_CHIPS"): return False
        else:
            wallet = hub.game_chips
            if wallet[self.chat_id][uid] < hub.sget("MIN_ENTRY_CHIPS"): return False
        self.players.append(uid); self.chips[uid] = wallet[self.chat_id][uid]; self.total_bet[uid] = 0
        return True

    def start(self):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        Card = hub.Card
        SEASON_BET_PERCENT = hub.SEASON_BET_PERCENT
        game_chips = hub.game_chips
        group_get = hub.group_get
        season_points = hub.season_points
        if len(self.players) < 2: return False
        random.shuffle(self.players)
        self.cancel_auto(); self.cancel_wait(); self.folded.clear(); self.all_in.clear(); self.acted.clear(); self.raise_locked.clear(); self.board = []; self.pot = 0; self.settled = False
        self.pending_confirm.clear(); self.pre_action.clear()   # 上一局残留的「待确认 / 提前操作」一律作废
        wallet = hub.season_points if self.season else hub.game_chips
        _ante = self.ante_value
        for uid in self.players:
            self.chips[uid] = wallet[self.chat_id][uid]; self.initial_chips[uid] = self.chips[uid]
            self.total_bet[uid] = self.round_bets[uid] = 0
            ante = min(_ante, self.chips[uid]); self.chips[uid] -= ante; self.total_bet[uid] += ante; self.pot += ante
            if not self.chips[uid]: self.all_in.add(uid)
        # 赛季：单局每人投入上限 = 本局落座玩家带入筹码总和 × 百分比（人少上限低，防串通）
        _sbp = hub.group_get(self.chat_id, "season_bet_percent", hub.SEASON_BET_PERCENT)   # 精确到本群
        self.max_total_bet = max(int(sum(self.initial_chips.values()) * float(_sbp or hub.SEASON_BET_PERCENT)), _ante) if self.season else None
        self.deck = [hub.Card.new(rank + suit) for rank in "23456789TJQKA" for suit in "shdc"]
        random.shuffle(self.deck); self.hands = {uid: [self.deck.pop(), self.deck.pop()] for uid in self.players}
        self.dealer_idx = len(self.players) - 1; self.active = self.players.copy()
        # 盲注位（2026-09-11 按官方规则核对修正）：
        #   3 人及以上：小盲 = 庄家左边第一位、大盲 = 庄家左边第二位；
        #   **单挑（2 人）：按钮位本身就是小盲**，另一位是大盲。
        #   原实现两人时「大盲」落在庄家自己身上（既当按钮又下大盲），
        #   且翻牌后从大盲位先动 —— 与官方「单挑翻牌后大盲（非按钮）先动」相反。
        _n = len(self.players)
        if _n == 2:
            sb_uid, bb_uid = self.players[self.dealer_idx], self.players[(self.dealer_idx + 1) % _n]
        else:
            sb_uid = self.players[(self.dealer_idx + 1) % _n]
            bb_uid = self.players[(self.dealer_idx + 2) % _n]
        self._blind(sb_uid, self.small_blind_value)
        self._blind(bb_uid, self.big_blind_value)
        # 跟注基准（2026-09-11 修正）：**短全下的盲注不降低跟注额**——
        # 大盲筹码不足时，其他人仍需按完整大盲跟注；原实现取 max(round_bets)
        # 会低于大盲值，导致全场少跟注。盲注为 0 时行为不变。
        self.current_bet = max(self.big_blind_value, max(self.round_bets.values()))
        self.phase = "preflop"
        self.actor_idx = (self.players.index(bb_uid) + 1) % len(self.active)
        if self._next(self.actor_idx) is None: self.phase = "showdown"
        return True

    def _blind(self, uid, value):
        paid = min(value, self.chips[uid])
        self.chips[uid] -= paid; self.round_bets[uid] += paid; self.total_bet[uid] += paid; self.pot += paid
        if not self.chips[uid]: self.all_in.add(uid)

    def current(self):
        if not self.active or self.actor_idx >= len(self.active): return None
        uid = self.active[self.actor_idx]
        return uid if uid not in self.folded and uid not in self.all_in and uid not in self.acted else None

    def recover_actor(self):
        """自愈：非摊牌阶段却定位不到行动者时，重新找一位能行动的人。

        正常流程下 `current()` 不该返回 None（`_next` 返回 None ⟺ `_round_done()`），
        但并发/异常可能把棋盘留在「没人能行动、也没进摊牌」的脏状态 —— 这时**没有超时任务**
        也**没有按钮**，牌局就永久冻结（2026-09-11 群友「这是卡了？」「也没自动弃牌」）。
        这里按「未弃牌且未全下」重新定位；先要求「本轮没行动过」（规则正确），
        实在不行放宽到「本轮已行动过」（宁可让他多行动一次，也不能让全群死等）。
        返回 None 表示确实没人能行动，调用方应进摊牌结算。
        """
        if not self.active: return None
        for relax in (False, True):
            for idx, uid in enumerate(self.active):
                if uid in self.folded or uid in self.all_in: continue
                if not relax and uid in self.acted: continue
                self.actor_idx = idx
                # 放宽这一档必须把「已行动」标记撤掉，否则 current() 仍会判定他没得行动
                # （current() 也会看 acted）——自愈就会变成一次空转。
                if relax: self.acted.discard(uid)
                return uid
        return None

    def _next(self, start):
        for offset in range(len(self.active)):
            idx = (start + offset) % len(self.active); uid = self.active[idx]
            if uid not in self.folded and uid not in self.all_in and uid not in self.acted:
                self.actor_idx = idx; return uid
        return None

    def _round_done(self): return all(uid in self.folded or uid in self.all_in or uid in self.acted for uid in self.active)

    def action(self, uid, kind, extra=0):
        if uid != self.current(): return False, "还没轮到你"
        skip_next = False
        if kind == "fold":
            old = self.active.index(uid); self.folded.add(uid); self.active.remove(uid)
            if self.active:
                self.actor_idx = old % len(self.active)
                self._next(self.actor_idx)   # 直接定位下一个行动者，避免末尾 _next(actor_idx+1) 跳过下家
            desc = "弃牌"
            skip_next = True
        elif kind == "check":
            if self.round_bets[uid] != self.current_bet: return False, "必须跟注或加注"
            self.acted.add(uid); desc = "过牌"
        elif kind == "call":
            paid = min(self.current_bet - self.round_bets[uid], self.chips[uid])
            if self.max_total_bet is not None and self.total_bet[uid] + paid > self.max_total_bet:
                return False, f"单局每人投入上限 {self.max_total_bet}，你已投入 {self.total_bet[uid]}"
            self.chips[uid] -= paid; self.round_bets[uid] += paid; self.total_bet[uid] += paid; self.pot += paid
            if not self.chips[uid]: self.all_in.add(uid)
            self.acted.add(uid); desc = f"跟注 {paid}"
        elif kind == "allin":
            paid = self.chips[uid]
            if self.max_total_bet is not None and self.total_bet[uid] + paid > self.max_total_bet:
                return False, f"单局每人投入上限 {self.max_total_bet}，你已投入 {self.total_bet[uid]}，可用加注补齐"
            old_bet = self.current_bet; new_total = self.round_bets[uid] + paid
            self.chips[uid] = 0; self.round_bets[uid] = new_total; self.total_bet[uid] += paid; self.pot += paid; self.all_in.add(uid)
            if new_total > old_bet:
                raise_size = new_total - old_bet
                prior_actors = self.acted.copy()
                self.current_bet = new_total
                # 任意抬高下注额的全下都要求其余玩家重新响应。
                self.acted = {uid}
                if raise_size < self.min_raise:
                    # 短全下不重新开放加注：之前已经行动的玩家只能跟注或弃牌。
                    self.raise_locked.update(prior_actors - {uid})
                else:
                    self.raise_locked.clear()
            else: self.acted.add(uid)
            desc = f"全下 {paid}"
        elif kind == "raise":
            try: extra = int(extra)
            except (TypeError, ValueError): return False, "无效加注额"
            to_call = self.current_bet - self.round_bets[uid]; paid = to_call + extra; new_total = self.round_bets[uid] + paid
            if extra < self.min_raise: return False, f"最低加注为 {self.min_raise}"
            if paid > self.chips[uid]: return False, f"积分不足：本次需要跟注 {to_call} + 加注 {extra}，共 {paid}，你只有 {self.chips[uid]}"
            if self.max_total_bet is not None and self.total_bet[uid] + paid > self.max_total_bet:
                return False, f"单局每人投入上限 {self.max_total_bet}，你已投入 {self.total_bet[uid]}"
            if new_total <= self.current_bet: return False, "加注后总下注必须高于当前下注"
            if uid in self.raise_locked: return False, "短全下后已行动玩家只能跟注或弃牌"
            self.chips[uid] -= paid; self.round_bets[uid] = new_total; self.total_bet[uid] += paid; self.pot += paid; self.current_bet = new_total; self.acted = {uid}; self.raise_locked.clear()
            if not self.chips[uid]: self.all_in.add(uid)
            desc = f"加注 {extra}"
        else: return False, "未知操作"
        alive = [p for p in self.active if p not in self.folded]
        if len(alive) <= 1 or all(p in self.all_in for p in alive): self.phase = "showdown"
        elif self._round_done(): self._end_round()
        elif not skip_next: self._next(self.actor_idx + 1)
        return True, desc

    def _draw(self):
        """安全抽牌：牌堆耗尽时重洗一副新牌，避免抽牌 IndexError。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        Card = hub.Card
        if not self.deck:
            self.deck = [hub.Card.new(rank + suit) for rank in "23456789TJQKA" for suit in "shdc"]
            random.shuffle(self.deck)
        return self.deck.pop()

    def _end_round(self):
        self.round_bets = {uid: 0 for uid in self.players}; self.current_bet = 0; self.acted.clear(); self.raise_locked.clear()
        # 换新街：清掉「提前操作」与「待确认」——上一街预留的过牌/弃牌不能跨街生效（见 __init__ 注释）
        self.pre_action.clear(); self.pending_confirm.clear()
        if self.phase == "preflop": self._draw(); self.board.extend([self._draw() for _ in range(3)]); self.phase = "flop"
        elif self.phase == "flop": self._draw(); self.board.append(self._draw()); self.phase = "turn"
        elif self.phase == "turn": self._draw(); self.board.append(self._draw()); self.phase = "river"
        else: self.phase = "showdown"; return
        # 基于 dealer 在 active 中的位置计算起始行动者（dealer 可能已弃牌）
        dealer = self.players[self.dealer_idx]
        if dealer in self.active:
            start = (self.active.index(dealer) + 1) % len(self.active)
        else:
            # dealer 已弃牌：从按钮（dealer_idx）下一位开始，找第一个仍为 active 的玩家，以其 active 索引作为起点
            n = len(self.players)
            start = 0
            for i in range(1, n + 1):
                cand = self.players[(self.dealer_idx + i) % n]
                if cand in self.active:
                    start = self.active.index(cand)
                    break
        if self._next(start) is None: self.phase = "showdown"

    async def showdown(self):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        HAND_NAME_CN = hub.HAND_NAME_CN
        distribute_side_pots = hub.distribute_side_pots
        force_save_now = hub.force_save_now
        game_chips = hub.game_chips
        save_data = hub.save_data
        season_active = hub.season_active
        season_points = hub.season_points
        alive = [uid for uid in self.players if uid not in self.folded]
        self.showdown_order = alive.copy()

        # 只剩一名未弃牌玩家：直接获得底池。
        # 不继续发公牌，也不进入摊牌亮牌。
        if len(alive) == 1:
            winner = alive[0]
            self.chips[winner] += self.pot
            wallet = hub.season_points if self.season else hub.game_chips
            for uid in self.players:
                # 增量结算：保留牌局进行中管理员用 /adddz 加的分，避免被开局快照覆盖
                # 赛季已结束的进行中牌局不写回 season_points，避免污染已清空的赛季账本（仍正常派奖，筹码不持久化）
                if not (self.season and not hub.season_active):
                    wallet[self.chat_id][uid] += self.chips[uid] - self.initial_chips.get(uid, wallet[self.chat_id][uid])
            hub.save_data(); await asyncio.to_thread(hub.force_save_now)
            return [(winner, "最后赢家", self.pot, [("全部底池", self.pot)], {})]

        # 至少两人仍在局内，才补齐五张公牌并进行正常摊牌。
        while len(self.board) < 5:
            self._draw()
            if not self.board:
                self.board.extend([self._draw() for _ in range(3)])
            else:
                self.board.append(self._draw())
        scores = {uid: self.evaluator.evaluate(self.hands[uid], self.board) for uid in alive}
        names = {uid: hub.HAND_NAME_CN.get(self.evaluator.class_to_string(self.evaluator.get_rank_class(score)), "未知") for uid, score in scores.items()}
        payouts = hub.distribute_side_pots(self.total_bet, scores)
        for uid, item in payouts.items(): self.chips[uid] += item["amount"]
        wallet = hub.season_points if self.season else hub.game_chips
        for uid in self.players:
            # 增量结算：保留牌局进行中管理员用 /adddz 加的分，避免被开局快照覆盖
            # 赛季已结束的进行中牌局不写回 season_points，避免污染已清空的赛季账本（仍正常派奖，筹码不持久化）
            if not (self.season and not hub.season_active):
                wallet[self.chat_id][uid] += self.chips[uid] - self.initial_chips.get(uid, wallet[self.chat_id][uid])
        hub.save_data(); await asyncio.to_thread(hub.force_save_now); return [(uid, names[uid], item["amount"], item["details"], names) for uid, item in payouts.items()]

    def cancel_timer(self):
        task, self.turn_task = self.turn_task, None
        if task and task is not asyncio.current_task() and not task.done(): task.cancel()

    def cancel_auto(self):
        task, self.auto_task = self.auto_task, None
        if task and task is not asyncio.current_task() and not task.done(): task.cancel()

    def cancel_wait(self):
        task, self.wait_task = self.wait_task, None
        if task and task is not asyncio.current_task() and not task.done(): task.cancel()


async def poker_waiting_text(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    players = [f"{i}. {await get_name(app, uid)}" for i, uid in enumerate(game.players, 1)]
    prefix = "🏆 赛季｜" if game.season else "🃏 新一局积分德州扑克"
    return f"{prefix}\n发起人：{await get_name(app, game.owner_id)}\n\n已加入：\n" + "\n".join(players) + f"\n\n点击加入，发起人可立即开始。\n⏰ 满 2 人后 {game.wait_timeout} 秒自动开局，不足 2 人 {game.wait_timeout} 秒后自动解散。"


async def update_poker_waiting(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    poker_waiting_text = hub.poker_waiting_text
    safe_edit = hub.safe_edit
    rows = [[InlineKeyboardButton("📥 加入游戏", callback_data="texas_join")]]
    if len(game.players) >= 2: rows.append([InlineKeyboardButton("🎮 开始游戏", callback_data="texas_start")])
    rows.append([InlineKeyboardButton("❌ 终止房间", callback_data="texas_end")])
    await safe_edit(app.bot, game.chat_id, game.game_msg_id, await poker_waiting_text(game, app), reply_markup=InlineKeyboardMarkup(rows))


def _poker_quick_amounts(game, uid):
    """行动金额计算：poker_buttons 的加注/半池/全池按钮共用一份计算（防两处漂移）。
    返回 (to_call, min_raise, half, pot)。"""
    _min_raise = game.min_raise   # 赛季局可能用独立的最低加注额
    half_amt = max(_min_raise, game.pot // 2)
    pot_amt = max(_min_raise, game.pot)
    to_call = max(0, game.current_bet - game.round_bets[uid])
    return to_call, _min_raise, half_amt, pot_amt


async def poker_table_text(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    card_str = hub.card_str
    get_name = hub.get_name
    player_line = hub.player_line
    phase = {"preflop":"翻牌前", "flop":"翻牌圈", "turn":"转牌圈", "river":"河牌圈"}.get(game.phase, game.phase)
    lines = [
        f"{'🏆 赛季｜' if game.season else '🃏 积分德州'}｜{phase}",
        f"🃏 公牌：{'  '.join(card_str(card) for card in game.board) or '未发牌'}",
        f"💰 奖池 {game.pot}｜下注 {game.current_bet}",
    ]
    current = game.current()
    lines.append("━━━━━━━━━━━━━━━━━")
    # 2026-09-12 用户要求：「把游戏轮到谁行动单独发一条出来提醒玩家（记得设置自动1分钟删除），
    #   然后就可以把牌桌文本那个该谁行动删除了」→ 行动提示**整体移出牌桌**，改由
    #   start_turn_timer 调 announce_turn() 单独发一条（60 秒自动回收）。
    #   牌桌于是只描述牌局状态：不随回合变长、不再和提醒消息把同一件事说两遍。
    #   👉（PLAYER_MARK）仍标着当前行动者，牌面上不看文字也能认出轮到谁。
    # ⛔ 历史坑：09-11 曾三次要求「⏳ 当前行动 / ⏰ 请在 N 秒内行动」拆**两行留在牌桌里**
    #   （见 GitHub 474e6893）。那条排版要求已被本次「移出牌桌」取代 —— 别再往牌桌里加回
    #   行动行，否则又会出现「牌桌一行 + 提醒一条」的重复。要改只改 announce_turn 的文案。
    # 玩家行**单行**（2026-09-11 用户要求：与炸金花一致，压成一行）
    # 👉 留在行首，非行动者补 3 个半角空格占位 → 所有行序号落在同一列
    # 2026-09-12 用户二选一（「1. Sharo Bow 🟢」vs「🟢1. Sharo Bow」）→ **选徽标前置**：
    #   ① 视觉上是一列「状态」，眼睛扫第一列就知道谁弃了谁全下，不用逐行读到名字结尾；
    #   ② 「🟢」本身就是「还在局里」，`在局` 两个字是重复表达，删掉更干净（用户原话「在局删除
    #      是不更加简洁明了」）；`❌`/`🔥` 同理只留徽标，不再写「弃牌 / 全下」。
    #   ③ 徽标等宽（都是 1 个 emoji），序号列不会被状态长短推歪。
    # ⛔ 别再改回「名字在前 + 🟢 在局」：用户明确否了那一版。
    # 版式统一由 player_line() 负责（徽标前置 + 序号对齐），本处只报数据。
    for index, uid in enumerate(game.players, 1):
        badge = "❌" if uid in game.folded else "🔥" if uid in game.all_in else "🟢"
        lines.append(player_line(
            index, await get_name(app, uid), badge, acting=(uid == current),
            cols=[f"投{game.total_bet[uid]}", f"余{game.chips[uid]}"], sep="｜"))
    return "\n".join(lines)


async def poker_turn_notice(game, app):
    """德州「轮到谁」提醒文案（单独一条消息，见 announce_turn）。current() 为 None 时返回 None。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    uid = game.current()
    if uid is None: return None
    need = max(0, game.current_bet - game.round_bets[uid])
    tail = f"｜需跟 {need}" if need else "｜可过牌"
    return (f"⏳ <b>{await get_name(app, uid)}</b> 轮到你行动{tail}\n"
            f"⏰ 请在 {game.turn_timeout} 秒内操作，超时将自动{'过牌' if need == 0 else '弃牌'}")


def poker_buttons(game, uid):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _poker_quick_amounts = hub._poker_quick_amounts
    acting = (uid == game.current() and uid not in game.folded and uid not in game.all_in)
    if not acting:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🃏 查看手牌", callback_data="texas_hand")]])
    to_call = max(0, game.current_bet - game.round_bets[uid])
    # 2026-09-11 用户要求撤销「emoji+2字」改造：按钮恢复带金额旧样式（快捷档行已删）
    # 2026-09-11 用户要求（防误触）：过牌/跟注上移到首行右侧（高频且顺手），
    # 弃牌下移到第二行最左——与高频键拉开距离，避免手滑点错直接出局。
    act_btn = InlineKeyboardButton("✅ 过牌" if not to_call else "✅ 跟注",
                                   callback_data="texas_check" if not to_call else "texas_call")
    fold_btn = InlineKeyboardButton("❌ 弃牌", callback_data="texas_fold")
    rows = [[InlineKeyboardButton("🃏 手牌", callback_data="texas_hand"), act_btn]]
    if uid not in game.raise_locked:
        # 半池/全池快捷加注：加注金额=底池的 1/2 或 1 倍；不足最小加注时按最小加注兜底
        _tc, _min_raise, half_amt, pot_amt = _poker_quick_amounts(game, uid)
        # 去重（2026-09-14 界面新版，skill §4.29 留档的按钮重复项）：
        # 「支出额」= 跟注 + 加注额。某快捷键的支出额若与更激进的键（全池>半池>加注，
        # 全下最激进）**完全相同**，两个按钮就是同一个动作 —— 只留语义更强的那个。
        _allin_amt = game.chips[uid]
        _raise_spend = _tc + _min_raise          # 「🚀 加注 N」实际支出
        _half_spend = _tc + half_amt             # 「💰 半池 N」实际支出
        _pot_spend = _tc + pot_amt               # 「💰 全池 N」实际支出
        row_act = [fold_btn]
        if (game.chips[uid] >= to_call + _min_raise and half_amt > _min_raise
                and _raise_spend < _pot_spend and _raise_spend < _allin_amt):
            row_act.append(InlineKeyboardButton(f"🚀 加注 {_min_raise}", callback_data=f"texas_raise_{_min_raise}"))
        rows.append(row_act)
        row_p = []
        if (half_amt < pot_amt and game.chips[uid] >= to_call + half_amt
                and _half_spend < _allin_amt):
            row_p.append(InlineKeyboardButton(f"💰 半池 {half_amt}", callback_data="texas_raise_half"))
        if (game.chips[uid] >= to_call + pot_amt
                and _pot_spend < _allin_amt):
            row_p.append(InlineKeyboardButton(f"💰 全池 {pot_amt}", callback_data="texas_raise_pot"))
        if row_p: rows.append(row_p)
    else:
        rows.append([fold_btn])
    if game.chips[uid] > 0:
        rows.append([InlineKeyboardButton(f"🔥 全下 {game.chips[uid]}", callback_data="texas_allin")])
    # 提前操作（2026-09-13 用户要求）：牌桌是**全群共享的一条消息**，键盘按当前行动者渲染，
    # 所以这两个键每个人都看得见 —— 点的人是谁由**回调里的 uid** 判定，弹窗也只有本人可见，
    # 不会打扰别人。只有「还有别人在等」时才给，单挑/只剩自己时留着纯占屏。
    if getattr(game, "phase", "") not in ("waiting", "showdown"):
        _live = [u for u in game.players if u not in game.folded and u not in game.all_in]
        if len(_live) >= 2:
            rows.append([InlineKeyboardButton("⏭ 预过牌", callback_data="texas_pre_check"),
                         InlineKeyboardButton("⏭ 预弃牌", callback_data="texas_pre_fold")])
    return InlineKeyboardMarkup(rows)


async def render_poker_table(game, app):
    """渲染唯一权威牌桌消息：**删旧发新**，全群始终只有这一条，且永远停在群最底部。

    2026-09-12 用户：「为什么德州又是原地编辑了」——炸金花与大话骰早在 09-11 就已统一
    删旧发新，只有德州漏在外面。原地编辑**位置不动**，会被后来的聊天顶上去，
    群友翻不到牌桌就看不出轮到谁，表现成「卡了」「我放弃不了牌」。
    三种渲染方式在同一个 bot 里并存本身就是 bug，此处收敛为一致行为。

    顺序必须是**先发新、成功才删旧**（与 `_sync_jinhua_msg` 同理）：
    发送失败（限流/网络）时旧牌桌原封不动留着，全群不会出现「一条牌桌都没有」的空窗。
    本函数**不动回合计时器** —— 调用方负责挂表（连点旧按钮不能重置倒计时）。

    2026-09-12 二次修正（用户截图「连续 4 条重复牌桌」）：
    只删 `game.game_msg_id` 不够 —— 换局时 `/dz` 会新建 PokerGame 覆盖
    `active_poker_games[cid]`，新对象 `game_msg_id=None`，**结构上碰不到上一局的牌桌**；
    叠加结算的延迟删除（窗口期内旧牌桌仍可点）与「点旧按钮即重绘」，
    一次换局 + 两次连点就能堆出 4 条。故此处再叠加 `_live_panel_msg`（以 **cid** 为键）
    登记的孤儿牌桌一并清理，让「全群只有一条牌桌」在结构上恒真。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _live_panel_msg = hub._live_panel_msg
    get_name = hub.get_name
    poker_buttons = hub.poker_buttons
    poker_table_text = hub.poker_table_text
    safe_delete = hub.safe_delete
    safe_send = hub.safe_send
    safe_send_photo = hub.safe_send_photo
    table_img = hub.table_img
    async with game._render_lock:
        uid = game.current()
        kb = poker_buttons(game, uid) if uid is not None else None
        old_id = game.game_msg_id
        # 本群上一张牌桌：可能属于已被换掉的旧 game（换局/重开后残留的孤儿）
        orphan_id = _live_panel_msg.get(game.chat_id)
        # 2026-09-14 界面新版：优先发**桌面图**（椭圆毛毡+真牌图+玩家盒）。
        # 图片链路任何一环失败（无 PIL / 字体缺失 / fake bot 无 send_photo / 发送异常）
        # 都回退到原文本渲染 —— 文本路径是底线，一个字都不能少。
        msg = None
        try:
            names = {p: await get_name(app, p) for p in game.players}
            phase = {"preflop": "翻牌前", "flop": "翻牌圈", "turn": "转牌圈", "river": "河牌圈"}.get(game.phase, game.phase)
            caption = f"{'🏆 赛季德州' if game.season else '🃏 德州扑克'}｜{phase}"
            png = await asyncio.to_thread(table_img, game, names)
            if png is not None:
                msg = await safe_send_photo(app.bot, game.chat_id, png, caption, reply_markup=kb)
        except Exception:
            msg = None
        if msg is None:
            text = await poker_table_text(game, app)
            msg = await safe_send(app.bot, game.chat_id, text, reply_markup=kb)
        if msg:                                   # 发送失败 → 保留旧牌桌，下一轮再试
            game.game_msg_id = msg.message_id
            _live_panel_msg[game.chat_id] = msg.message_id
            if old_id and old_id != game.game_msg_id:
                await safe_delete(app.bot, game.chat_id, old_id)
            # 孤儿牌桌不属于当前 game 对象，但同样占着群里的位置，必须一起清掉
            if orphan_id and orphan_id not in (game.game_msg_id, old_id):
                await safe_delete(app.bot, game.chat_id, orphan_id)


async def update_poker_table(game, app):
    """兼容别名：等价于 render_poker_table（删旧发新）。

    历史上这里做过「原地编辑、且故意不传 reply_markup」——每回合先摘键盘再装键盘两次编辑，
    中间那次失败就把全群按钮抹掉（2026-09-11「德州卡了」的直接原因）。那条路径已彻底删除：
    删旧发新天然带上新键盘，不存在「正文已更新、按钮还是上一轮」的中间态。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    render_poker_table = hub.render_poker_table
    await render_poker_table(game, app)


async def start_turn_timer(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    announce_turn = hub.announce_turn
    get_name = hub.get_name
    poker_turn_notice = hub.poker_turn_notice
    render_poker_table = hub.render_poker_table
    safe_send = hub.safe_send
    schedule_notice_delete = hub.schedule_notice_delete
    settle_poker = hub.settle_poker
    start_turn_timer = hub.start_turn_timer
    game.cancel_timer()
    if game.phase == "waiting": return   # 等待房没有回合，别被强制推进到结算
    uid = game.current()
    if uid is None:
        # 自愈（2026-09-11 群友「德州卡了」）：绝不能「非摊牌 + 没人能行动」地静默 return ——
        # 那等于既没有按钮也没有超时任务，这一局就永久冻死（start_turn_timer 第一行已经把
        # 旧计时器 cancel 掉了）。要么找回一位行动者，要么直接进摊牌结算。
        uid = game.recover_actor()
        if uid is None:
            game.phase = "showdown"
            await settle_poker(game, app)
            return
    # 2026-09-13 用户要求（提前操作）：轮到某人时，先看他有没有提前按过「预过牌 / 预弃牌」。
    # 规则（用户原话）：预过牌 = 轮到时**没人加注**才过牌、有人加注则改为弃牌；预弃牌 = 一律弃牌。
    # 放在 render 之前：自动执行完就直接推进下一位，不必先渲染一张马上就要作废的牌桌。
    _pre = (getattr(game, "pre_action", None) or {}).pop(uid, None)
    if _pre:
        _need = max(0, game.current_bet - game.round_bets[uid])
        _do = "fold" if (_pre == "fold" or _need > 0) else "check"
        _ok, _d = game.action(uid, _do)
        if _ok:
            _word = "弃牌" if _do == "fold" else "过牌"
            # 注意：schedule_notice_delete 是同步函数，不能 await（见下面 timeout_action 的注释）
            schedule_notice_delete(app, game.chat_id,
                await safe_send(app.bot, game.chat_id,
                                f"⏭ {await get_name(app, uid)} 提前设定的「{_word}」已自动执行"))
            # 递归推进：start_turn_timer 里已含 render，这里**不能**再补一次渲染
            if game.phase == "showdown": await settle_poker(game, app)
            else: await start_turn_timer(game, app)
            return
    await deal_hand_cards(game, app)   # 开局私发弹窗手牌图（hands_dealt 防重发；失败静默，回退靠「手牌」按钮）
    await render_poker_table(game, app)
    game.turn_started_at = time.time()
    # 行动提醒：单独一条 + 60 秒自动删除（2026-09-12 用户要求，牌桌正文里已不再写行动行）
    game.turn_notice_id = await announce_turn(app, game.chat_id, await poker_turn_notice(game, app),
                                              old_id=game.turn_notice_id)

    # 真实超时任务：无需跟注自动过牌，否则自动弃牌，防止牌局卡死
    async def timeout_action():
        await asyncio.sleep(game.turn_timeout)
        if game.settled or game.phase == "showdown": return
        if game.current() != uid: return  # 该玩家已行动过
        if game.round_bets[uid] == game.current_bet:
            ok, _ = game.action(uid, "check")
            if not ok: game.action(uid, "fold")
            desc = "超时自动过牌"
        else:
            game.action(uid, "fold")
            desc = "超时自动弃牌"
        # ⚠️ schedule_notice_delete 是**同步**函数（core/messaging.py），这里绝不能 await：
        #    `await None` 会抛 TypeError —— 超时任务里一抛，下面的「推进下一位 / 进摊牌」
        #    就全都不执行了，牌局直接冻死。2026-09-13 修（做提前操作时连带发现）。
        hub.schedule_notice_delete(app, game.chat_id,
                                   await hub.safe_send(app.bot, game.chat_id, f"⏰ {desc}：{await hub.get_name(app, uid)}"))
        if game.phase == "showdown":
            await hub.settle_poker(game, app)
        else:
            # start_turn_timer 内部已含 render_poker_table，**不能**再在前面补一次
            # update_poker_table —— 删旧发新下那会连锁刷出两条牌桌。
            await hub.start_turn_timer(game, app)
    game.turn_task = asyncio.create_task(timeout_action())


def _poker_settle_lines(game, names, result, rake_per, hand_types, card_str):
    """拼德州结算正文（**纯函数**，2026-09-13 从 settle_poker 提出来）。

    为什么单独抽：结算正文占 settle_poker 一大半，改文案以前要在一个 176 行、
    夹着账本写回和异常兜底的 try 里翻找 —— 手滑就会碰到资金行（改 A 崩 B）。
    现在文案归文案、账本归账本，改字只动这里。

    ★ 本函数**只拼字符串**，不做任何副作用：
      · 不碰当日榜 / 资金流 / 抽水账 / 累计积分 / 赛季数据
      · 无 await、不访问 hub（card_str 由调用方传入，避免任何全局查表）
      · 参数全是「算好的数据」，与调用方的账本顺序完全解耦

    入参：
        game        牌局对象（只读 players/folded/hands/board/showdown_order/
                    total_bet/chips/initial_chips）
        names       {uid: 显示名}
        result      showdown() 返回的派奖列表 [(uid, 牌型名, 金额, 池明细, ...), ...]
        rake_per    {uid: 本局抽水}
        hand_types  {uid: 牌型}
        card_str    牌面格式化函数（调用方从 hub 绑定后传入）
    返回：list[str]（HTML 片段行，调用方 "\\n".join 后发送）
    """
    board_text = "  ".join(card_str(card) for card in game.board) or "未发牌"
    lines = ["🃏 <b>德州结算</b>", "━━━━━━━━━━━━━━━━━", f"🃏 公牌：{board_text}", ""]

    if len(game.showdown_order) > 1:
        lines.append("亮牌：")
        for uid in game.players:
            if uid in game.folded: lines.append(f"　{names[uid]}：弃牌")
            else: lines.append(f"　{names[uid]}：{'  '.join(card_str(card) for card in game.hands[uid])}｜{hand_types.get(uid, '')}")
    else:
        lines.append("亮牌牌型：")
        for uid in game.players:
            if uid not in game.folded: lines.append(f"　{names[uid]}：未亮牌")
            else: lines.append(f"　{names[uid]}：弃牌")
    lines.append("")

    lines.append("派奖：")
    for uid, hand, amount, details, _ in sorted(result, key=lambda item: item[2], reverse=True):
        lines.append(f"　{names[uid]}：{hand}｜+{amount}（{'，'.join(f'{pool}+{value}' for pool, value in details)}）")

    lines.append("投入 / 盈亏：")
    for uid in game.players:
        net = game.chips[uid] - game.initial_chips[uid]
        r_amt = rake_per.get(uid, 0)
        r_txt = f"（实收 {net - r_amt}，含抽水{r_amt}）" if r_amt else ""
        lines.append(f"　{names[uid]}：投入 {game.total_bet[uid]}｜盈亏 {net:+d}{r_txt}")
    lines.append("")
    return lines


async def settle_poker(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    RANK_PAGE_SIZE = hub.RANK_PAGE_SIZE
    _check_level_change = hub._check_level_change
    _earn_add = hub._earn_add
    _earn_get = hub._earn_get
    _season_base = hub._season_base
    active_poker_games = hub.active_poker_games
    broadcast_big_win = hub.broadcast_big_win
    business_date = hub.business_date
    calc_rake = hub.calc_rake
    card_str = hub.card_str
    commit_rake = hub.commit_rake
    emergency_if_needed = hub.emergency_if_needed
    force_save_now = hub.force_save_now
    game_chips = hub.game_chips
    games_played = hub.games_played
    get_name = hub.get_name
    logger = hub.logger
    poker_profit_by_date = hub.poker_profit_by_date
    rank_line = hub.rank_line
    recent_poker_reveals = hub.recent_poker_reveals
    record_game_flows = hub.record_game_flows
    safe_send = hub.safe_send
    safe_send_long = hub.safe_send_long
    safe_send_photo = hub.safe_send_photo
    save_data = hub.save_data
    schedule_delete = hub.schedule_delete
    schedule_delete_ids = hub.schedule_delete_ids
    schedule_notice_delete = hub.schedule_notice_delete
    season_active = hub.season_active
    season_games = hub.season_games
    season_points = hub.season_points
    season_profit_by_date = hub.season_profit_by_date
    season_rebuy = hub.season_rebuy
    send_settle_rank = hub.send_settle_rank
    sget = hub.sget
    showdown_img = hub.showdown_img
    safe_send_photo = hub.safe_send_photo
    user_wallet_locks = hub.user_wallet_locks
    if game.settled: return
    game.settled = True; game.cancel_timer(); game.cancel_auto(); game.cancel_wait()
    try:
        # 获取本局所有真实玩家的钱包锁，再执行 showdown 里的钱包写回，避免与同一用户的其他扣款路径并发
        async with user_wallet_locks([uid for uid in game.players if uid >= 0]):
            result = await game.showdown()
        if not result: raise RuntimeError("德州摊牌未生成结算结果")
        date, hand_types = business_date(), result[0][4]
        name_ids = set(game.players) | set(game.showdown_order)
        names = {uid: await get_name(app, uid) for uid in name_ids}

        # 摊牌+结算摘要图（2026-09-14 界面新版）：赢家金框/弃牌灰底/净盈亏红灰条，
        # 发在结算正文**之前**。仅多人摊牌才发 —— 单赢场景走下方「亮牌按钮」（muck 规则）。
        # 图片任何失败都静默跳过：结算正文（safe_send_long）才是权威账目，绝不能被图挡住。
        if len(game.showdown_order) > 1:
            try:
                _nets = {uid: game.chips[uid] - game.initial_chips[uid] for uid in game.players}
                _png = await asyncio.to_thread(showdown_img, game, names, result, _nets)
                if _png is not None:
                    await safe_send_photo(app.bot, game.chat_id, _png, "🃏 摊牌")
            except Exception:
                pass

        # 抽水先算（官方模式），面板「盈亏」行直接带实收；资金流审查同源
        _nets = {uid: game.chips[uid] - game.initial_chips[uid] for uid in game.players} \
            if (game.mode == "official" and not game.season) else {}
        rake_per = calc_rake(_nets)[1] if _nets else {}

        # 账本写回（当日榜）必须**先于**拼文案：文案里的盈亏行读的就是这份榜/抽水
        if game.mode == "official" and not game.season:
            for uid in game.players:
                net = game.chips[uid] - game.initial_chips[uid]
                poker_profit_by_date[date][game.chat_id][uid] += net

        # 结算正文：纯拼装，已提到 _poker_settle_lines（改文案不再需要翻账本代码）
        lines = _poker_settle_lines(game, names, result, rake_per, hand_types, card_str)

        # 资金流审查：官方模式把本局人对人净转移记账（防"故意输牌送分"）；赛季分不记
        if game.mode == "official" and not game.season:
            record_game_flows(game.chat_id, _nets, "德州")
            await commit_rake(app, game.chat_id, rake_per, "德州")

        # 官方局：累计参与局数（归零赠送门槛）+ 赢分计入累计积分 + 升级通知
        if game.mode == "official" and not game.season:
            for uid in game.players:
                if uid < 0: continue
                games_played[game.chat_id][uid] += 1
                # 实际到手 = 净赢 - 本局抽水（抽水已在上面扣除），记账才与实际余额一致
                _gain = (game.chips[uid] - game.initial_chips.get(uid, 0)) - int(rake_per.get(uid, 0) or 0)
                if _gain > 0:
                    _oe = _earn_get(game.chat_id, uid)
                    _earn_add(game.chat_id, uid, _gain)
                    await _check_level_change(app, game.chat_id, uid, _oe, _earn_get(game.chat_id, uid))

        # 大奖战报：官方模式单局净赢超阈值 → 广播其他授权群（赛季不播）
        if game.mode == "official" and not game.season:
            top_uid, top_net = None, 0
            for uid in game.players:
                _n = game.chips[uid] - game.initial_chips[uid]
                if _n > top_net: top_uid, top_net = uid, _n
            if top_uid: await broadcast_big_win(app, game.chat_id, top_uid, "🃏 德州扑克", top_net)

        # 赛季：累计局数 + 破产应急补分（已取消淘汰；赛季已结束的进行中牌局只正常派奖、不计入、不误判破产）
        if game.season and season_active:
            for p in game.players:
                if p < 0: continue
                season_games[game.chat_id][p] += 1
            for p in game.players:
                if p < 0: continue
                if season_points[game.chat_id][p] <= 0:
                    if season_rebuy[game.chat_id][p] < sget("SEASON_REBUY_COUNT"):
                        season_rebuy[game.chat_id][p] += 1
                        season_points[game.chat_id][p] = sget("SEASON_REBUY_AMOUNT")
                        lines.append(f"⚠️ {names[p]} 破产，启用应急筹码 +{sget('SEASON_REBUY_AMOUNT')}（剩 {sget('SEASON_REBUY_COUNT') - season_rebuy[game.chat_id][p]} 次）")
            # 赛季跨午夜补重置：本局横跨业务日结束，错过的午夜刷新在此补记当日盈亏并归位到基准分
            if game.start_date and business_date() != game.start_date:
                for uid in game.players:
                    if uid < 0: continue
                    base = _season_base(game.chat_id, uid)
                    final = season_points[game.chat_id][uid]
                    day_profit = final - base
                    if day_profit:
                        season_profit_by_date[game.start_date][game.chat_id][uid] += day_profit
                    season_points[game.chat_id][uid] = base
                    # 已取消淘汰机制：破产玩家当日剩余时间无法下注，次日 0 点重置为「起始分+兑换底分」后可继续参赛

        # 累计盈利榜单独发一条（2026-09-11 用户要求：结算正文太长像刷屏，榜单拆开发）
        _rank_lines = None
        if game.mode == "official" and not game.season:
            # 2026-09-11 用户要求：榜单别刷屏，一页 10 人
            rank = sorted(poker_profit_by_date[date][game.chat_id].items(), key=lambda item: item[1], reverse=True)[:RANK_PAGE_SIZE]
            if rank:
                _rank_lines = ["🏆 <b>当日德州累计盈利榜</b>", "━━━━━━━━━━━━━━━━━"]
                _rank_lines.extend([rank_line(index, uid, names.get(uid) or await get_name(app, uid), f"：{amount:+d}") for index, (uid, amount) in enumerate(rank, 1)])

        delivered = await safe_send_long(app.bot, game.chat_id, "\n".join(lines), parse_mode="HTML")
        if sget("SETTLE_DELETE_SECONDS") > 0:
            schedule_delete(app, game.chat_id, delivered, sget("SETTLE_DELETE_SECONDS"))
        await send_settle_rank(app, game.chat_id, _rank_lines)
        # 牌桌卡片此前结算后一直留在群里，结束后延迟清理
        schedule_delete_ids(app, game.chat_id, game.game_msg_id, sget("PANEL_DELETE_SECONDS"))
        # 单赢场景（只剩一人未弃牌）：提供可选亮牌按钮，尊重德州 muck 规则，不强制亮牌
        if len(game.showdown_order) <= 1:
            winner = game.showdown_order[0] if game.showdown_order else None
            if winner is not None and game.hands.get(winner):
                btn = await safe_send(app.bot, game.chat_id,
                    "💡 本局单挑收池，赢家可选择亮出底牌：",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🃏 亮牌", callback_data="texas_reveal")]]))
                schedule_notice_delete(app, game.chat_id, btn)
                # 入队而非覆盖：同一群可同时存在多局未亮牌的单赢
                recent_poker_reveals[game.chat_id].append({
                    "winner": winner,
                    "hand": list(game.hands[winner]),
                    "board": list(game.board),
                    "reveal_msg_id": btn.message_id if btn else None,
                })
                # 仅保留最近 5 条，避免极端情况下无限增长
                if len(recent_poker_reveals[game.chat_id]) > 5:
                    recent_poker_reveals[game.chat_id] = recent_poker_reveals[game.chat_id][-5:]
        if delivered is None:
            schedule_notice_delete(app, game.chat_id, await safe_send(app.bot, game.chat_id, "⚠️ 德州已完成结算，但详细结算消息发送失败。"), kind="settle")
    except Exception:
        logger.exception("德州结算异常")
    finally:
        if active_poker_games.get(game.chat_id) is game: active_poker_games.pop(game.chat_id, None)
        if game.mode == "official" and not game.season:
            for uid in game.players: await emergency_if_needed(game.chat_id, uid, app, game_chips, game)
        save_data(); await asyncio.to_thread(force_save_now)


async def handle_texas_reveal(cid, uid, q, context):
    """德州单赢后，点击「亮牌」按钮，把赢家两张底牌发到群（可选，不强制）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    card_str = hub.card_str
    get_name = hub.get_name
    recent_poker_reveals = hub.recent_poker_reveals
    safe_edit = hub.safe_edit
    safe_send = hub.safe_send
    safe_send_photo = hub.safe_send_photo
    schedule_delete_ids = hub.schedule_delete_ids
    schedule_notice_delete = hub.schedule_notice_delete
    sget = hub.sget
    infos = recent_poker_reveals.get(cid, [])
    if not infos:
        await q.answer("本局亮牌数据已失效", show_alert=True); return
    # 按点击的亮牌按钮消息 id 精确匹配对应的一局，避免新单赢覆盖旧单赢后无法亮牌
    clicked_id = q.message.message_id if q.message else None
    info = next((it for it in infos if it.get("reveal_msg_id") == clicked_id), None)
    if info is None and len(infos) == 1:
        info = infos[0]  # 兜底：仅剩一条且按钮消息 id 无法匹配时直接取该条
    if info is None:
        await q.answer("本局亮牌数据已失效", show_alert=True); return
    if uid != info["winner"]:
        await q.answer("只有赢家本人能亮牌", show_alert=True); return
    name = await get_name(context.application, info["winner"])
    hand_text = "  ".join(card_str(c) for c in info["hand"])
    board_text = "  ".join(card_str(c) for c in info["board"]) if info["board"] else "（未发公牌）"
    # 2026-09-14 界面新版：优先发**亮牌图**（赢家大牌金框+公牌行），失败回退原文本。
    _reveal_msg = None
    try:
        reveal_img = hub.reveal_img
        _png = await asyncio.to_thread(reveal_img, name, info["hand"], info["board"])
        if _png is not None:
            _reveal_msg = await safe_send_photo(context.bot, cid, _png, f"🃏 {name} 亮牌")
    except Exception:
        _reveal_msg = None
    if _reveal_msg is None:
        _reveal_msg = await safe_send(context.bot, cid,
            f"🃏 <b>{name} 亮牌</b>：{hand_text}\n🃏 公牌：{board_text}（收全部底池）",
            parse_mode="HTML")
    schedule_notice_delete(context.application, cid, _reveal_msg, kind="settle")
    rid = info.get("reveal_msg_id")
    if rid:
        await safe_edit(context.bot, cid, rid, "🃏 已亮牌", reply_markup=None)
        # 亮完牌的按钮卡片同样回收（之前编辑成「已亮牌」后就永久留在群里）
        _rid_secs = int(sget("PANEL_DELETE_SECONDS") or 0)
        if _rid_secs > 0:
            schedule_delete_ids(context.application, cid, rid, _rid_secs)
    # 仅移除本条亮牌记录，其余未亮牌的单赢保留
    recent_poker_reveals[cid] = [it for it in infos if it is not info]
    if not recent_poker_reveals[cid]:
        recent_poker_reveals.pop(cid, None)
    await q.answer("已亮牌")


async def send_hand_card(app, uid, hand):
    """把某玩家的弹窗手牌图**私发**给他。返回 Message；任何失败返回 None。

    失败场景（都不算错误）：用户从未私聊过 bot（Forbidden）/ bot 被'拉黑 /
    渲染环境缺 Pillow 或字体 / 测试 fake bot 无 send_photo。
    调用方拿到 None 就回退 q.answer(alert 报牌面) —— 手牌永远看得到。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    hand_popup = hub.hand_popup
    safe_send_photo = hub.safe_send_photo
    if not hand: return None
    try:
        if not hasattr(app.bot, "send_photo"): return None   # fake bot / 环境无图能力
        name = await get_name(app, uid)
        png = await asyncio.to_thread(hand_popup, name, list(hand))
        return await safe_send_photo(app.bot, uid, png, f"🃏 {name}，这是你的底牌（只有你能看）")
    except Exception:
        return None


async def deal_hand_cards(game, app):
    """开局给每位玩家私发弹窗手牌图（2026-09-14 界面新版）。

    - `game.hands_dealt` 标记防重发（start_turn_timer 每回合都会跑进来）；
    - 先标记后发送：个别玩家私聊发送失败也**不重试不刷屏**，回退靠「🃏 手牌」按钮；
    - send_hand_card 内部全吞异常 → 本函数绝不打断开局流程（含测试 fake 环境）。
    """
    if getattr(game, "hands_dealt", False): return
    game.hands_dealt = True
    for uid in game.players:
        hand = game.hands.get(uid)
        if hand and uid not in game.folded:
            await send_hand_card(app, uid, hand)


# ── 游戏互斥：同一群里同时只允许一个游戏进行（2026-09-14 用户要求）─────────
# 几个游戏在一个群里同时跑，会互相抢消息、抢结算时机、抢定时任务，
# 表现出来就是「卡住」（等一个永远不会来的操作）。
# 「bot 全局变量名 → 游戏显示名」，新增游戏只需在这里加一行。
_GAME_DICTS = (
    ("active_poker_games", "德州扑克"),
    ("active_blackjack_games", "21点"),
    ("active_jinhua_games", "炸金花"),
    ("active_dice_games", "大话骰"),
    ("active_horse_races", "赛车"),
)


def game_mutex_enabled():
    """总开关，默认开启。

    直接读全局而不走 sget：这个开关还没注册成后台设置项，
    走 sget 会触发「未知设置」告警。以后要在后台加开关，
    只要在设置里注册同名项即可，这里照样读得到。
    """
    return bool(hub.namespace().get("GAME_MUTEX_ENABLED", True))


def game_mutex_running(cid, exclude=None):
    """该群**正在进行**的游戏显示名；没有则 None。

    exclude：排除某个显示名 —— 已经有德州在跑时再发 /dz 属于德州自己的逻辑
    （会回应当前这一局），不算互斥冲突。
    """
    for _var, _label in _GAME_DICTS:
        if exclude and _label == exclude:
            continue
        _d = getattr(hub, _var, None)
        if _d and cid in _d:
            return _label
    return None


async def game_mutex_wait_idle(cid, timeout=900, poll=5):
    """等该群的游戏全部结束 —— **定时任务**用。

    定时任务（每日重置、潜水员清理…）在牌局进行中插进来，会打断结算、
    把正在玩的人踢掉、或者把刚赢的分清零。所以执行前先等。

    返回 True = 等到空闲；False = 超时（调用方自己决定跳过还是强制执行）。
    """
    if not game_mutex_running(cid):
        return True
    _waited = 0
    while _waited < timeout:
        await asyncio.sleep(poll)
        _waited += poll
        if not game_mutex_running(cid):
            hub.logger.info("定时任务：群 %s 游戏已结束，等待 %s 秒后继续", cid, _waited)
            return True
    hub.logger.warning("定时任务：群 %s 等了 %s 秒游戏仍未结束（超时放弃等待）", cid, timeout)
    return False


async def _game_gate(update, context, game, ranked=None):
    """4 游戏总开关/仅管理员开局（后台各游戏分组设置，保存立即生效）。返回 True=放行。

    ranked：仅德州用。True=赛季德州、False=日常德州、None=不区分（走总开关）。
    德州额外受「日常/赛季」两个独立开关控制，管理员可只开一种模式。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_bot_admin = hub.is_bot_admin
    send_reply = hub.send_reply
    sget = hub.sget
    label, enabled, admin_only = {
        "texas":     ("德州扑克", sget("TEXAS_ENABLED"), sget("TEXAS_ADMIN_ONLY")),
        "blackjack": ("21点", sget("BJ_ENABLED"), sget("BJ_ADMIN_ONLY")),
        "jinhua":    ("炸金花", sget("JINHUA_ENABLED"), sget("JINHUA_ADMIN_ONLY")),
        "dice":      ("大话骰", sget("DICE_ENABLED"), sget("DICE_ADMIN_ONLY")),
        "race":      ("赛车", sget("RACE_ENABLED"), sget("RACE_ADMIN_ONLY")),
    }[game]
    if not enabled:
        await send_reply(update, context, f"❌ {label}已关闭（管理员可在后台「{label}」分组重新开启）。")
        return False
    if game == "texas" and ranked is not None:
        if ranked and not sget("RANKED_TEXAS_ENABLED"):
            await send_reply(update, context, "❌ 赛季德州已关闭（管理员可在后台「德州扑克」分组开启）。")
            return False
        if not ranked and not sget("DAILY_TEXAS_ENABLED"):
            await send_reply(update, context, "❌ 日常德州已关闭（管理员可在后台「德州扑克」分组开启）。")
            return False
    if admin_only and not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, f"❌ {label}仅管理员可开局。")
        return False
    # ★ 同群互斥：同时只允许一个游戏进行（2026-09-14 用户要求）。
    #   放在这里（总门控）而不是各游戏内部 —— 一处覆盖全部 5 个游戏。
    #   用 exclude=label 让「同一游戏重复开局」仍走该游戏自己的逻辑。
    if game_mutex_enabled():
        _busy = game_mutex_running(update.effective_chat.id, exclude=label)
        if _busy:
            await send_reply(update, context,
                             f"⏳ 本群正在玩「{_busy}」，等这一局结束后再开「{label}」吧。")
            return False
    return True


async def start_wait_timeout(game, app):
    """德州等待房倒计时：满 2 人自动开局，不足 2 人自动解散。

    倒计时秒数走 game.wait_timeout：赛季局可用后台「赛季」分组单独配置。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_poker_games = hub.active_poker_games
    refund_poker = hub.refund_poker
    start_turn_timer = hub.start_turn_timer
    game.cancel_wait()
    async def countdown():
        _wait = game.wait_timeout
        await asyncio.sleep(_wait)
        if game.phase != "waiting" or hub.active_poker_games.get(game.chat_id) is not game:
            return
        if len(game.players) >= 2:
            if game.start():
                # start_turn_timer 内部已渲染牌桌（删旧发新），别再补一次
                await hub.start_turn_timer(game, app)
        else:
            await hub.refund_poker(game, app, f"⌛ 德州等待 {_wait} 秒不足 2 人，房间已自动解散。")
    game.wait_task = asyncio.create_task(countdown())


async def cmd_dz(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    PokerGame = hub.PokerGame
    _game_gate = hub._game_gate
    active_poker_games = hub.active_poker_games
    current_game_mode = hub.current_game_mode
    game_chips = hub.game_chips
    need_auth = hub.need_auth
    panel_adopt = hub.panel_adopt
    poker_room_of = hub.poker_room_of
    poker_waiting_text = hub.poker_waiting_text
    require_group_chat = hub.require_group_chat
    safe_send = hub.safe_send
    send_reply = hub.send_reply
    sget = hub.sget
    start_wait_timeout = hub.start_wait_timeout
    update_poker_waiting = hub.update_poker_waiting
    if not await need_auth(update, context): return
    if not await _game_gate(update, context, "texas", ranked=False): return
    if not await require_group_chat(update, "德州扑克", "dz", context): return
    cid, uid = update.effective_chat.id, update.effective_user.id; game = active_poker_games.get(cid)
    room_name, _ = poker_room_of(cid, uid, exclude_game=game)
    if room_name:
        await send_reply(update, context, f"⚠️ 你已在 {room_name} 房间，请先结束再开新的扑克游戏。"); return
    mode = game.mode if game and game.phase == "waiting" else current_game_mode()
    wallet = game_chips
    if wallet[cid][uid] < sget("MIN_ENTRY_CHIPS"):
        label = "积分"
        await send_reply(update, context, f"❌ 进入德州至少需要 {sget('MIN_ENTRY_CHIPS')} {label}。"); return
    if game:
        if game.season:
            await send_reply(update, context, "当前有赛季房间，请用 /赛季 加入或开局。"); return
        if game.phase != "waiting": await send_reply(update, context, "当前已有进行中的德州扑克。"); return
        if game.add(uid):
            await update_poker_waiting(game, context.application); await send_reply(update, context, "已加入当前等待房间。")
        else: await send_reply(update, context, "你已在等待房间中。")
        return
    # 注意：不再在此清空亮牌队列，保留上一局（已结束）单赢未亮牌的数据，供玩家随时补亮牌
    game = PokerGame(cid, uid, mode); game.add(uid); active_poker_games[cid] = game
    msg = await safe_send(context.bot, cid, await poker_waiting_text(game, context.application), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 加入游戏", callback_data="texas_join")], [InlineKeyboardButton("❌ 终止房间", callback_data="texas_end")]]))
    if msg:
        game.game_msg_id = msg.message_id
        # 接管本群牌桌并清掉上一局残留的孤儿（换局时新 game 碰不到旧 game 的牌桌）
        await panel_adopt(context.bot, cid, msg.message_id)
        await start_wait_timeout(game, context.application)


async def refund_poker(game, app, notice):
    """终止未结算牌局时退款。

    德州下注只在局对象 self.chips 中暂扣，wallet 在牌局期间不会被扣减
    （仅在开局快照 + 管理员 /adddz 加分时变动），因此直接保留 wallet 现状即可
    正确退还，同时不丢失牌局进行中管理员的加分。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_poker_games = hub.active_poker_games
    safe_delete = hub.safe_delete
    safe_edit = hub.safe_edit
    save_data = hub.save_data
    schedule_delete_ids = hub.schedule_delete_ids
    sget = hub.sget
    game.cancel_timer(); game.cancel_auto(); game.cancel_wait()
    game.phase = "cancelled"
    if active_poker_games.get(game.chat_id) is game:
        active_poker_games.pop(game.chat_id, None)
    await safe_delete(app.bot, game.chat_id, game.action_msg_id)
    await safe_edit(app.bot, game.chat_id, game.game_msg_id, notice, reply_markup=None)
    # 解散提示牌桌也延迟清理，避免一堆「已解散」卡片堆在群里
    schedule_delete_ids(app, game.chat_id, game.game_msg_id, sget("PANEL_DELETE_SECONDS"))
    save_data()


async def cmd_end(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_blackjack_games = hub.active_blackjack_games
    active_dice_games = hub.active_dice_games
    active_horse_races = hub.active_horse_races
    active_jinhua_games = hub.active_jinhua_games
    active_poker_games = hub.active_poker_games
    game_chips = hub.game_chips
    is_bot_admin = hub.is_bot_admin
    need_auth = hub.need_auth
    pending_game_bets = hub.pending_game_bets
    refund_dice = hub.refund_dice
    refund_jinhua = hub.refund_jinhua
    refund_poker = hub.refund_poker
    retire_panel = hub.retire_panel
    save_data = hub.save_data
    send_reply = hub.send_reply
    if not await need_auth(update, context): return
    cid, uid = update.effective_chat.id, update.effective_user.id
    arg = context.args[0].lower() if context.args else ""

    poker = active_poker_games.get(cid)
    race = active_horse_races.get(cid)
    bj = active_blackjack_games.get(cid)
    jinhua = active_jinhua_games.get(cid)
    dice = active_dice_games.get(cid)

    if not any([poker, race, bj, jinhua, dice]):
        await send_reply(update, context, "当前没有进行中的游戏。"); return

    notices = []
    # 如果带了参数，只针对性关闭
    target_all = (arg == "")

    if poker and (target_all or arg in ["dz", "dzpk", "texas", "德州"]):
        if is_bot_admin(uid) or uid in poker.players:
            await refund_poker(poker, context.application, "🛑 德州扑克已终止，积分已退回。")
            notices.append("德州已退款")

    if jinhua and (target_all or arg in ["jinhua", "zjh", "炸金花", "金花"]):
        if is_bot_admin(uid) or uid in jinhua.players:
            await refund_jinhua(jinhua, context.application, "🛑 炸金花已终止，积分已退回。")
            notices.append("炸金花已退款")

    if dice and (target_all or arg in ["dice", "大话骰", "大話骰", "吹牛"]):
        if is_bot_admin(uid) or uid in dice.players:
            await refund_dice(dice, context.application, "🛑 大话骰已终止，底注已退回。")
            notices.append("大话骰已退款")

    if race and (target_all or arg in ["sc", "sm", "race", "赛车"]):
        if is_bot_admin(uid) or uid in race.bets:
            if race.phase == "betting":
                if race.task and not race.task.done(): race.task.cancel()
                await race.refund(context.application, "🛑 赛车已终止，积分已退回。")
                notices.append("赛车已退款")
            else: notices.append("赛车进行中无法终止")

    if bj and (target_all or arg in ["21", "bj", "21点"]):
        if is_bot_admin(uid) or uid in bj.players:
            # ★ 结算已开始/已完成 → 拒绝终止：
            #   _bj_finish 会先把 settled 置 True、再逐个 await get_name（数秒窗口）、
            #   最后才把派彩写回钱包。在这段窗口里按 bets 退一次款，就会
            #   「退款 + 派彩」叠加 → 凭空多出一份积分。
            #   上面赛车分支已有同款 phase 判断，21点此前漏了这一层。
            if getattr(bj, "settled", False) or getattr(bj, "phase", "") in ("finished", "dealer_turn"):
                notices.append("21点结算中无法终止")
            else:
                bj.cancel_timer(); bj.cancel_wait()
                wallet = game_chips
                for p_uid, b in bj.bets.items():
                    wallet[cid][p_uid] += b
                    pending_game_bets[cid].get(p_uid, {}).pop("21", None)
                active_blackjack_games.pop(cid, None)
                # 与 /end 的其它游戏一致：终止提示也要回收（此前 edit 出来的提示永久残留）
                await retire_panel(context.application, cid, bj.game_msg_id, "🛑 21点已终止，积分已退回。")
                notices.append("21点已退款")

    if not notices:
        await send_reply(update, context, "❌ 权限不足或未找到匹配的游戏指令。用法示例：/end dz")
    else:
        save_data()
        await send_reply(update, context, "；".join(notices))


def player_is_busy(cid, uid):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_blackjack_games = hub.active_blackjack_games
    active_dice_games = hub.active_dice_games
    active_horse_races = hub.active_horse_races
    active_jinhua_games = hub.active_jinhua_games
    active_poker_games = hub.active_poker_games
    poker = active_poker_games.get(cid)
    if poker and poker.phase != "waiting" and uid in poker.players:
        return True
    race = active_horse_races.get(cid)
    if race and race.phase in {"betting", "racing", "settling"} and uid in race.bets:
        return True
    bj = active_blackjack_games.get(cid)
    if bj and bj.phase != "waiting" and uid in bj.players:
        return True
    jinhua = active_jinhua_games.get(cid)
    if jinhua and jinhua.phase != "waiting" and uid in jinhua.players:
        return True
    _dg = active_dice_games.get(cid)
    if _dg and _dg.phase != "waiting" and uid in _dg.players:
        return True
    return False


def poker_room_of(cid, uid, exclude_game=None):
    """玩家所在的游戏房间（德州/炸金花/大话骰，含等待房）。exclude_game 用于排除当前房间。
    返回 (游戏名, 游戏对象)，不在任何房间则返回 (None, None)。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_dice_games = hub.active_dice_games
    active_jinhua_games = hub.active_jinhua_games
    active_poker_games = hub.active_poker_games
    for name, g in (("德州", active_poker_games.get(cid)),
                    ("炸金花", active_jinhua_games.get(cid)),
                    ("大话骰", active_dice_games.get(cid))):
        if g and g is not exclude_game and uid in g.players:
            return name, g
    return None, None


async def _poker_watchdog_tick(app):
    """德州看门狗单轮：把「还在打、却没人管」的牌局救回来。

    两种死法（都是 2026-09-11 群友「这是卡了？」「也没自动弃牌」的形态）：
      ① 非摊牌阶段但回合计时器没了 → 重挂（重挂时会顺带重绘牌桌，画面也一起自愈）；
      ② 停在 showdown 却从没结算（settle 中途异常）→ 补一次结算（settle_poker 幂等）。
    只碰这两种；`sget` 出来的正常牌局一动不动，绝不重置玩家的倒计时。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_poker_games = hub.active_poker_games
    logger = hub.logger
    settle_poker = hub.settle_poker
    start_turn_timer = hub.start_turn_timer
    now = time.time()
    for cid, game in list(active_poker_games.items()):
        try:
            if game.settled or game.phase == "waiting": continue
            if game.phase == "showdown":
                await settle_poker(game, app)          # 幂等：settled 已置位会自动返回
                continue
            task = game.turn_task
            if task is not None and not task.done(): continue
            # 刚渲染完的那一小段窗口里计时器尚未建成 → 让子弹飞一会儿，别误判成冻结
            if now - float(getattr(game, "turn_started_at", 0) or 0) < 10: continue
            logger.warning("德州看门狗：群 %s 无回合计时器，重挂（phase=%s）", cid, game.phase)
            await start_turn_timer(game, app)
        except Exception:
            logger.exception("德州看门狗处理群 %s 异常（已吞并）", cid)


async def poker_watchdog(app):
    """德州牌局看门狗（每 20 秒）。全部异常吞并，绝不影响其他功能。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    POKER_WATCHDOG_SECONDS = hub.POKER_WATCHDOG_SECONDS
    _poker_watchdog_tick = hub._poker_watchdog_tick
    logger = hub.logger
    while True:
        try:
            await _poker_watchdog_tick(app)
        except Exception:
            logger.exception("poker_watchdog 本轮异常（已吞并继续）")
        await asyncio.sleep(POKER_WATCHDOG_SECONDS)
