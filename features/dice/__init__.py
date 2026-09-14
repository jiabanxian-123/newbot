# -*- coding: utf-8 -*-
"""feature/dice —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
import asyncio
import random
import re

def _dice_is_straight(hand, wild=True):
    """一手骰是否「顺子」：恰好 5 颗且连号 —— 12345 或 23456。

    2026-09-11 用户报障补的规则（原话：「23456不是顺子吗 顺子不是算0个吗」）：
    顺子这手**整手算 0 个**，不参与任何点数统计（开骰计数 + 亮盅展示都要体现）。
    万能开时还认「**假顺**」（2026-09-11 群友口径，张瑾一：12456 在两人局算顺子）：
    1 当替身补上空位，只要 5 颗能排成 12345 / 23456 就算 —— 如 12456（1→3）、13456（1→2）。

    ⚠️ 2026-09-12 修正（用户两次报障）：
    **假顺最多只能用 1 个万能 1 补位（= 整手只能有 1 个空位）**。需要同时拿 2 个 1 去补两个空位
    不算顺子——否则几乎任何带两 1 的牌都能凑顺子，规则就失去意义。含两类典型错判：
      · 1 1 3 4 6（补 2、5 → 23456）不算顺子；
      · 1 1 2 3 4 / 1 1 3 4 5（补 1 位 + 另一空位 → 12345）也不算顺子——
        即便其中一个空位恰是目标连号里天然的 1 位，它照样要消耗一个万能，
        所以仍需 2 个万能 → 超出「最多 1 个」上限。
    真顺（无 1 / 只 1 个 1 自然连号）与单 1 假顺（12456 / 13456 / 12346）保持原样。
    掉骰制下只有满 5 颗才可能成顺（4 颗/6 颗一律不算）。
    """
    if len(hand) != 5: return False
    s = sorted(hand)
    for target in ([1, 2, 3, 4, 5], [2, 3, 4, 5, 6]):
        if s == target: return True          # 真顺（天然连号）
    if not wild: return False
    ones = hand.count(1)
    if ones == 0: return False
    # 非 1 的骰本身必须都是目标连号里的值；否则哪怕有万能也拼不出顺子
    rest = [d for d in hand if d != 1]
    for target in ([1, 2, 3, 4, 5], [2, 3, 4, 5, 6]):
        if all(d in target for d in rest):
            covered = set(rest)
            missing = [v for v in target if v not in covered]   # 需要靠 1 补的空位（含目标连号里天然的 1 位）
            # 每个空位都消耗一个万能 1；假顺只允许 1 个空位（= 只能拿 1 个 1 补 1 个位置）。
            wilds_needed = len(missing)
            # 最多 1 个万能（= 空位数 ≤ 1）；且 1 的总数够填满所有空位。
            if wilds_needed <= 1 and ones >= len(missing):
                return True
    return False


def _dice_rule_on(setting_name, n_players):
    """规则生效范围开关：0=关；1=仅两人局；2=所有人数（2026-09-11 群友口径：默认只两人局）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    try:
        lv = int(sget(setting_name) or 0)
    except (TypeError, ValueError):
        lv = 1
    if lv <= 0: return False
    return True if lv >= 2 else n_players <= 2


def dice_active_rules(n_players, wild_on=None, straight_lv=None, leopard_lv=None, duel=None):
    """**本局大话骰生效哪些规则** —— 唯一判定出处（2026-09-13 集中）。

    为什么要有它：这四条规则「按人数生效」的判定原先在
    dice_waiting_text（等待房完整规则串）和 dice_rules_text（看牌弹窗一行摘要）
    各写一遍 —— 漏改一处就会出现「等待房说顺子算0、弹窗说没有顺子」的自相矛盾。

    ★ 本函数只返回**事实**，不含任何展示措辞（措辞由各渲染函数自己决定，
      所以等待房能写「顺子（含 1 补位的假顺）一手全算 0 个。」、
      弹窗能压成「顺子算0」，两者事实同源、不会打架）。

    入参：
        n_players    本局人数（决定 0/1/2 三档里「档 1」是否生效）
        wild_on      1 是否万能牌（None ⇒ 读设置 DICE_WILD_ONE）
        straight_lv  顺子算 0 的档位（None ⇒ 读 DICE_STRAIGHT_ZERO）
        leopard_lv   豹子加成的档位（None ⇒ 读 DICE_LEOPARD_BONUS）
        duel         是否一把定胜负（None ⇒ 用 _dice_duel_mode 判定）
    返回：{"wild": bool, "straight": bool, "leopard": bool, "duel": bool}
        档位语义：0=关 / 1=仅两人局 / 2=所有人数
    """
    if wild_on is None:
        wild_on = hub.sget("DICE_WILD_ONE")
    if straight_lv is None:
        straight_lv = hub.sget("DICE_STRAIGHT_ZERO")
    if leopard_lv is None:
        leopard_lv = hub.sget("DICE_LEOPARD_BONUS")
    if duel is None:
        duel = hub._dice_duel_mode(n_players=n_players)
    return {
        "wild": bool(wild_on),
        "straight": _dice_rule_lv_on(straight_lv, n_players),
        "leopard": _dice_rule_lv_on(leopard_lv, n_players),
        "duel": bool(duel),
    }


def _dice_rule_lv_on(lv, n_players):
    """把 0/1/2 档位折算成布尔（0=关 / 1=仅两人局 / 2=所有人数）。
    出过分片问过或填错 → 当作档 1（与 _dice_rule_on 旧行为一致，别改口径）。"""
    try:
        lv = int(lv or 0)
    except (TypeError, ValueError):
        lv = 1
    if lv <= 0: return False
    return True if lv >= 2 else n_players <= 2


def _dice_duel_mode(game=None, n_players=None):
    """本局是否「一把定胜负」：默认任何人数都一把定胜负（掉骰子多轮制关闭）；
    掉骰开关打开时退回旧行为——三人以上掉骰多轮、只有两人局一把定胜负。

    写成模块级函数是为了兼容只提供 players 的轻量测试桩（不依赖 DiceGame.duel）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    if not sget("DICE_DROP_DICE"): return True
    if n_players is None:
        n_players = len(getattr(game, "players", None) or [])
    return n_players <= 2


def _dice_ante_for(n_players):
    """本局底注：**两人局**读 `DICE_ANTE_DUEL`，**多人局（3 人及以上）**读 `DICE_ANTE_MULTI`。

    2026-09-13 用户要求「两人局底注 / 多人局底注 分开设定」。
    两项任一为 0（或没设/填错）时**回落到 `DICE_ANTE`** —— 所以默认 0/0 时
    行为和改造前一模一样，老群不需要改任何设置。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    key = "DICE_ANTE_DUEL" if n_players <= 2 else "DICE_ANTE_MULTI"
    try:
        v = int(sget(key) or 0)
    except (TypeError, ValueError):
        v = 0
    if v > 0:
        return v
    try:
        return int(sget("DICE_ANTE") or 0)
    except (TypeError, ValueError):
        return 0


def _dice_leopard_kind(hand, wild=True):
    """豹子牌型：'纯豹'（5 颗点数完全相同）/ '花豹'（带万能 1、其余同点）/ None。"""
    if len(hand) != 5: return None
    if len(set(hand)) == 1: return "纯豹"
    if not wild: return None
    others = set(d for d in hand if d != 1)
    if len(others) == 1 and 1 in hand: return "花豹"
    return None


def _dice_leopard_bonus(hand, face, wild=True):
    """豹子加成（2026-09-11 用户口径）：返回该手在叫 face 时的**额外颗数**。

    - **纯豹（真豹子）**：5 颗点数完全相同 → 该点数 +2（如 5 个 4 算 7）
    - **花豹（假豹子）**：带 1 且其余同点 → 该点数 +1（如 2 个 1 + 3 个 6 算 6）
    加成只作用于豹子自己的点数；「5 个 1」是纯豹且对**任意点数**都成立
    （它本身就是 5 个万能 1，叫什么都算 7 个）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _dice_leopard_kind = hub._dice_leopard_kind
    kind = _dice_leopard_kind(hand, wild)
    if not kind: return 0
    if kind == "纯豹":
        f = hand[0]
        return 2 if (f == 1 and wild) or f == face else 0
    others = set(d for d in hand if d != 1)
    return 1 if others.pop() == face else 0


def parse_dice_bid(text):
    """把群友的叫牌文本解析成 (数量, 点数)。不是叫牌返回 None。
    宽容识别（玩家怎么顺手怎么打）：
      6个3 / 6個3 / 6 3 / 六个三 / 六個三 / 两 个 四 / 十个2
      叫6个3 / 喊 6个3 / 报6个3 / 我出6个3 / 6个3吧
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _CN_NUM = hub._CN_NUM
    t = text.strip()
    t = re.sub(r"^\s*(?:我\s*)?(?:叫|喊|报|出|要)\s*[:：]?\s*", "", t)
    t = re.sub(r"\s*(?:吧|起|了)\s*$", "", t)
    mm = re.fullmatch(r"(\d{1,2}|[一二两三四五六七八九十]+)\s*[个個]\s*(\d|[一二三四五六]+)", t)
    if not mm:
        mm = re.fullmatch(r"(\d{1,2})\s+(\d)", t)
        if not mm: return None
    def _num(s):
        s = s.strip()
        if s.isdigit(): return int(s)
        if len(s) == 1: return hub._CN_NUM.get(s)
        if s == "十": return 10
        if "十" in s:
            a, _, b = s.partition("十")
            return (hub._CN_NUM.get(a, 1) if a else 1) * 10 + (hub._CN_NUM.get(b, 0) if b else 0)
        return None
    c, f = _num(mm.group(1)), _num(mm.group(2))
    if c is None or f is None or c < 1 or f < 1: return None
    return c, f


class DiceGame:
    """大话骰：每人 N 骰偷看，轮流叫「X个Y」必须越叫越大，开骰掀盅定输赢，掉骰子多轮制。

    资金模型与炸金花一致：开局底注扣进局内副本（钱包不动），结算按净差回写，
    弃局直接作废副本即等于全额退款。
    """

    def __init__(self, cid, owner, mode=None):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        current_game_mode = hub.current_game_mode
        self.chat_id, self.owner_id, self.mode, self.phase = cid, owner, mode or hub.current_game_mode(), "waiting"
        self.players, self.chips, self.initial_chips, self.paid = [], {}, {}, {}
        self.dice = {}         # uid -> 剩余骰子数
        self.hands = {}        # uid -> [点数列表]（仅本局内存，绝不下发群）
        self.out = set()       # 已出局
        self.dropped = []      # 开局因底注不足被剔除的玩家（调用方提示用）
        self.pot = 0
        self.ante_used = 0     # 本局实际用的底注（两人局/多人局分开设定，见 _dice_ante_for）
        self.bid = None        # 当前叫牌 (count, face, uid)
        # 2026-09-13 用户要求「跳开」：还没轮到我，也能指定开某个人的叫牌。
        # bids = 本手各人**最后一次**叫牌（uid -> (count, face)）；只有叫过牌的人才能被跳开。
        self.bids = {}
        self.skip_menu_owner = None   # 跳开选人菜单的发起者（None = 当前不在选人）
        self.skip_pair = None         # 跳开结算的 (赢家, 输家)：只结算这两个人，其余底注退回
        self.starter_uid = None  # 本手先叫者（上一手输家先叫）
        self.actor = None      # 当前行动者
        self.hand_no = 0
        self.game_msg_id = None
        self.turn_task = self.wait_task = None
        self.settled = False
        self.last_action = None
        self._render_lock = asyncio.Lock()
        self.turn_notice_id = None   # 「轮到谁行动」提醒消息 id（单独一条、60 秒自动回收，见 announce_turn）

    # ---- 等待房 ----
    def add(self, uid):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        game_chips = hub.game_chips
        sget = hub.sget
        if self.phase != "waiting" or uid in self.players: return False
        if len(self.players) >= hub.sget("DICE_MAX_PLAYERS"): return False
        if hub.game_chips[self.chat_id][uid] < hub.sget("MIN_ENTRY_CHIPS"): return False
        self.players.append(uid)
        return True

    # ---- 开局 ----
    def start(self):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        _dice_ante_for = hub._dice_ante_for
        game_chips = hub.game_chips
        sget = hub.sget
        if len(self.players) < 2: return False
        # 底注不足者剔除（否则 paid=0 却能分奖池 = 零成本白拿），由调用方提示。
        # 底注按**开局时房间人数**取：≤2 人走「两人局底注」，≥3 人走「多人局底注」
        # （2026-09-13 用户要求分开设定；两项都是 0 时回落到 DICE_ANTE）。
        # 注：剔除完若只剩 2 人，底注**不重算** —— 否则「先剔人、再改底注」会来回震荡。
        _ante = _dice_ante_for(len(self.players))
        self.ante_used = _ante
        self.dropped = [u for u in self.players if hub.game_chips[self.chat_id][u] < _ante]
        if self.dropped:
            self.players = [u for u in self.players if u not in self.dropped]
        if len(self.players) < 2: return False
        random.shuffle(self.players)
        self.cancel_wait(); self.out.clear(); self.settled = False
        self.pot = 0; self.paid.clear(); self.chips.clear(); self.initial_chips.clear()
        for uid in self.players:
            self.chips[uid] = hub.game_chips[self.chat_id][uid]
            self.initial_chips[uid] = self.chips[uid]
            ante = min(_ante, self.chips[uid])
            self.chips[uid] -= ante; self.paid[uid] = ante; self.pot += ante
            self.dice[uid] = hub.sget("DICE_DICE_COUNT")
        self.phase = "playing"
        self.starter_uid = self.players[0]
        self._new_hand()
        return True

    # ---- 基础 ----
    def alive(self):
        return [u for u in self.players if u not in self.out]

    def total_dice(self):
        return sum(self.dice[u] for u in self.alive())

    def cancel_timer(self):
        task, self.turn_task = self.turn_task, None
        if task and task is not asyncio.current_task() and not task.done(): task.cancel()

    def cancel_wait(self):
        task, self.wait_task = self.wait_task, None
        if task and task is not asyncio.current_task() and not task.done(): task.cancel()

    # ---- 港式叫牌比较 ----
    def bid_beats(self, new, prev):
        """new 是否严格大于 prev（各为 (count, face)）。
        万能开：普通叫之间同数量可升点；跨 1 的叫牌按翻倍强度比较（X个1 = 2X 个其他点）。
        无万能局：1 是普通点数，全部走 数量优先、同数量比点数。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        sget = hub.sget
        if not hub.sget("DICE_WILD_ONE"):
            if new[0] != prev[0]: return new[0] > prev[0]
            return new[1] > prev[1]
        if prev[1] == 1 and new[1] == 1: return new[0] > prev[0]
        if prev[1] == 1: return new[0] > prev[0] * 2
        if new[1] == 1: return new[0] * 2 > prev[0]
        if new[0] != prev[0]: return new[0] > prev[0]
        return new[1] > prev[1]

    # ---- 行动 ----
    def action(self, uid, kind, extra=None):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        sget = hub.sget
        if self.phase != "playing": return False, "当前不在叫牌阶段"
        if uid != self.actor: return False, "还没轮到你"
        if kind == "bid":
            c, f = extra
            if not (isinstance(c, int) and isinstance(f, int)): return False, "叫牌格式无效"
            if not (1 <= f <= 6): return False, "点数必须是 1~6"
            if c < 1 or c > self.total_dice():
                return False, f"数量要在 1~{self.total_dice()} 之间（在场骰子总数）"
            if hub.sget("DICE_WILD_ONE") and f == 1 and self.bid is None:
                return False, "首手不能叫 1（万能 1 只能在加码中出现）"
            if self.bid and not self.bid_beats((c, f), (self.bid[0], self.bid[1])):
                return False, "叫牌必须严格大于上家（X个1＝2X个其他点）"
            self.bid = (c, f, uid)
            self.bids[uid] = (c, f)      # 记下本人本手最后一次叫牌（跳开时才能开他）
            self._next_actor()
            return True, f"叫 {c}个{f}"
        if kind == "open":
            if not self.bid: return False, "还没有叫牌可开"
            if self.bid[2] == uid: return False, "不能开自己的叫牌"
            return True, "开骰"
        return False, "未知操作"

    def skip_open(self, uid, target):
        """跳开（2026-09-13 用户要求）：**还没轮到我**，也能指定开某个人的叫牌。

        只判合法性、不改状态（真正的掀盅在 `_dice_resolve_and_continue(opener, target)`）。

        · 限**多人局（≥3 人）** —— 两人局本来就能直接开上家，再给个选人菜单纯属多余。
        · 只能开**本手叫过牌**的人（没叫过就无从判定「他喊的数字跟结果符不符」）。
        · 判定与正常开骰一致：实际 < 他叫的 → **他（被跳开者）输**；否则开的人输。
        · 结算只涉及这两人，其余人的底注**原路退回**（见 settle_dice 的 skip_pair 分支）。
        """
        if self.phase != "playing": return False, "当前不在叫牌阶段"
        if len(self.players) < 3: return False, "两人局直接用「🎯 开骰」就行"
        if uid not in self.players or uid in self.out: return False, "你不在本局"
        if target not in self.players or target in self.out: return False, "对方已出局"
        if target == uid: return False, "不能开自己"
        _b = self.bids.get(target)
        if not _b: return False, "对方本手还没叫过牌，无从开起"
        return True, f"跳开 {_b[0]}个{_b[1]}"

    def _next_actor(self, after=None):
        cur = after if after is not None else self.actor
        if cur not in self.players: return None
        idx = self.players.index(cur)
        for off in range(1, len(self.players) + 1):
            u = self.players[(idx + off) % len(self.players)]
            if u not in self.out:
                self.actor = u
                return u
        return None

    def _next_alive_after(self, uid):
        idx = self.players.index(uid) if uid in self.players else -1
        for off in range(1, len(self.players) + 1):
            u = self.players[(idx + off) % len(self.players)]
            if u not in self.out: return u
        return uid

    def _new_hand(self):
        """掉骰子制：全员重摇，本手先叫者开局（输家先叫）。"""
        self.hand_no += 1
        for uid in self.alive():
            self.hands[uid] = sorted(random.randint(1, 6) for _ in range(self.dice[uid]))
        self.bid = None
        self.bids = {}          # 换一手：清掉上一手的叫牌记录（跳开只能开**本手**叫过的人）
        self.actor = self.starter_uid

    # ---- 开骰结算 ----
    def resolve_open(self, opener, bid=None):
        """返回 (实际数量, 输家, 纯点数个数, 万能1个数)。

        `bid` 可显式传 (count, face, bidder) —— **跳开**时开的是某个指定玩家的叫牌，
        而不是 `self.bid`（场上最后一次叫牌）。不传就按正常开骰走 `self.bid`。

        被叫点是 2~6 且万能开：该点数 + 全部1 都计入；被叫点是 1：只数 1 本身。
        两条规则按人数生效（默认**只两人局**，2026-09-11 群友口径「多人的没有顺子，没有豹子」）：
        - **顺子整手算 0 个**（DICE_STRAIGHT_ZERO，含 1 补位的假顺）
          ——2026-09-11 用户报障，此前把顺子里的点数照算，导致结算反了。
        - **豹子加成**（DICE_LEOPARD_BONUS）：纯豹 +2、花豹 +1。
        实际 ≥ 叫的 → 开骰者输；实际 < 叫的 → 被开者输（无平局）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        _dice_is_straight = hub._dice_is_straight
        _dice_leopard_bonus = hub._dice_leopard_bonus
        _dice_rule_on = hub._dice_rule_on
        sget = hub.sget
        count, face, bidder = bid or self.bid
        wild = hub.sget("DICE_WILD_ONE")
        _n = len(self.players)
        straight_on = hub._dice_rule_on("DICE_STRAIGHT_ZERO", _n)
        leopard_on = hub._dice_rule_on("DICE_LEOPARD_BONUS", _n)
        n_face = n_one = _bonus = 0
        for u in self.alive():
            hand = self.hands[u]
            if straight_on and hub._dice_is_straight(hand, wild):
                continue          # 顺子整手作废，一颗都不算
            n_face += sum(1 for d in hand if d == face)
            if wild and face != 1:
                n_one += sum(1 for d in hand if d == 1)
            if leopard_on:
                _bonus += hub._dice_leopard_bonus(hand, face, wild)
        actual = n_face + n_one + _bonus
        loser = opener if actual >= count else bidder
        return actual, loser, n_face, n_one

    def duel(self):
        """本局是否「一把定胜负」（默认任何人数都一把定胜负，见 _dice_duel_mode）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        _dice_duel_mode = hub._dice_duel_mode
        return hub._dice_duel_mode(n_players=len(self.players))

    def apply_loss(self, loser):
        """输家掉一颗骰子；归零出局；幸存者重摇、输家先叫；只剩 1 人 → 终局。

        **一把定胜负**（2026-09-11 群友要求「不用少一颗」「一局结束就结算」）：
        输家直接出局、**本局立即结算**，不玩掉骰多轮（三人以上同样生效）；
        奖池由其余存活者平分（见 settle_dice），两人局即退化成「幸存者通吃」。
        """
        if self.duel():
            self.dice[loser] = 0
            self.out.add(loser)
            eliminated = True
        else:
            self.dice[loser] = max(0, self.dice[loser] - 1)
            eliminated = self.dice[loser] <= 0
            if eliminated: self.out.add(loser)
        self.bid = None
        self.starter_uid = loser if loser not in self.out else self._next_alive_after(loser)
        if self.duel() or len(self.alive()) <= 1:
            self.phase = "showdown"
            self.actor = None
        else:
            self._new_hand()
        return eliminated


def dice_min_raise(game):
    """按钮「➕ 加码」的目标叫牌：优先同点数量+1 → 同数量升点 → 全场最小合法叫。无则 None。
    注意面数含 1：叫「X个1」之后同点加码到 (X+1)个1 是合法的（4个1 > 3个1）；
    只有「首手」在万能开时禁叫 1（由 action() 与 not game.bid 分支保证）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    wild = sget("DICE_WILD_ONE")
    faces = list(range(1, 7))
    total = game.total_dice()
    if not game.bid:
        return 1, 2 if wild else 1
    pc, pf, _ = game.bid
    if pc + 1 <= total and game.bid_beats((pc + 1, pf), (pc, pf)):
        return pc + 1, pf
    if pf < 6 and game.bid_beats((pc, pf + 1), (pc, pf)):
        return pc, pf + 1
    for f in faces:
        for c in range(1, total + 1):
            if game.bid_beats((c, f), (pc, pf)):
                return c, f
    return None


async def dice_waiting_text(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _dice_ante_for = hub._dice_ante_for
    dice_active_rules = hub.dice_active_rules
    get_name = hub.get_name
    sget = hub.sget
    players = [f"{i}. {await get_name(app, uid)}" for i, uid in enumerate(game.players, 1)]
    _n = len(game.players)
    # 生效规则统一由 dice_active_rules 判定（事实同源，措辞在这里定）
    _rules = dice_active_rules(_n)
    # 一把定胜负是默认（2026-09-11 群友要求：不用少一颗、一局结束就结算）
    rule = ("一把定胜负，开骰即结算（奖池由其余人平分）。" if _rules["duel"]
            else "输家掉一颗骰子，掉光出局。")
    wild_rule = "1 是万能牌。" if _rules["wild"] else ""
    # 顺子/豹子默认只两人局生效（群友口径：多人的没有顺子，没有豹子）
    straight_rule = "顺子（含 1 补位的假顺）一手全算 0 个。" if _rules["straight"] else ""
    leopard_rule = "豹子加成：纯豹 +2、花豹 +1。" if _rules["leopard"] else ""
    extra = (wild_rule + straight_rule + leopard_rule)
    # 底注：两人局 / 多人局分开（2026-09-13 用户要求）。等待房还不知道最终几个人，
    # 所以两项都报出来；两项一样（含都是 0 回落到同一个 DICE_ANTE）时只报一个，别啰嗦。
    _a_duel, _a_multi = _dice_ante_for(2), _dice_ante_for(3)
    ante_txt = (f"底注 {_a_duel}" if _a_duel == _a_multi
                else f"底注 两人局 {_a_duel}｜多人局 {_a_multi}")
    return (f"🎲 新一局大话骰（吹牛）\n发起人：{await get_name(app, game.owner_id)}\n\n已加入：\n" + "\n".join(players)
            + f"\n\n每人 {sget('DICE_DICE_COUNT')} 颗骰子偷看自己的，轮流叫「X个Y」越叫越大，开骰掀盅，{rule}\n"
            + (extra + "\n" if extra else "")
            + f"⏰ 满 2 人后 {sget('ROOM_WAIT_TIMEOUT')} 秒自动开局，不足 2 人自动解散。{ante_txt}。")


async def update_dice_waiting(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    dice_buttons = hub.dice_buttons
    dice_waiting_text = hub.dice_waiting_text
    safe_edit = hub.safe_edit
    await safe_edit(app.bot, game.chat_id, game.game_msg_id, await dice_waiting_text(game, app),
                    reply_markup=dice_buttons(game, game.owner_id))


async def _dice_del_bid_msg(context, cid, message):
    """删掉玩家发在群里的叫牌/开骰文本，保持群聊清爽（2026-09-11 用户要求）。

    bot 非管理员 / 无删除权限时删不掉 → 静默失败，绝不影响牌局主流程。
    """
    try:
        await message.delete()
    except Exception:  # silent-ok: 删不掉玩家叫牌文本只是外观，绝不能影响牌局主流程
        try:
            await context.bot.delete_message(cid, message.message_id)
        except Exception:
            pass


def dice_buttons(game, uid):
    """等待房：加入/开始/终止；牌局中：行动玩家加码|开骰|跳开，其余玩家仅私看自己的骰子（2026-09-11 用户要求）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    dice_min_raise = hub.dice_min_raise
    # 🎯 跳开（2026-09-13 用户要求）：还没轮到我，也能指定开某个人的叫牌。
    # 牌桌是**全群共享的一条消息**，键盘是按当前行动者渲染的 ⇒ 这个键必须挂在
    # **行动者那块键盘**上，否则其他人根本上看不到它（体现在下面两个分支都要 append）。
    # 「点的是谁」由回调里的 uid 判定，选人菜单还有 skip_menu_owner 上锁（见 entry.py）。
    _skip = ([InlineKeyboardButton("🎯 跳开", callback_data="dice_skip")]
             if (game.phase == "playing" and len(game.players) >= 3 and game.bids) else [])
    if game.phase == "waiting":
        rows = [[InlineKeyboardButton("📥 加入游戏", callback_data="dice_join")]]
        if len(game.players) >= 2: rows.append([InlineKeyboardButton("🎮 开始游戏", callback_data="dice_start")])
        rows.append([InlineKeyboardButton("❌ 终止房间", callback_data="dice_end")])
        return InlineKeyboardMarkup(rows)
    if uid in game.out:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 刷新界面", callback_data="dice_refresh")]])
    if uid != game.actor or game.phase != "playing":
        rows = [[InlineKeyboardButton("🎲 看牌", callback_data="dice_see")]]
        if _skip: rows.append(_skip)
        return InlineKeyboardMarkup(rows)
    row1 = [InlineKeyboardButton("🎲 看牌", callback_data="dice_see")]
    # 无合法加码（数量点数双顶满）时隐藏「加码」，避免用户点了才被拒
    if dice_min_raise(game):
        row1.append(InlineKeyboardButton("➕ 加一个", callback_data="dice_raise"))
    rows = [row1]
    row2 = []
    if game.bid:
        row2.append(InlineKeyboardButton("🎯 开骰", callback_data="dice_open"))
    row2.extend(_skip)
    row2.append(InlineKeyboardButton("🔄 刷新", callback_data="dice_refresh"))
    rows.append(row2)
    return InlineKeyboardMarkup(rows)


async def dice_table_text(game, app):
    """牌桌文案（2026-09-11 用户「字太多有点复杂」→ 按《大话骰文案精简方案》减肥）。

    精简 4 刀（11 行 → 9 行）：
      ① 删规则串（`1=万能｜顺子=0｜豹子+2/+1｜一把定胜负`）—— **等待房第一条消息已完整写过**，
         想看规则点「🎲 看牌」弹窗里也有；牌桌每局重复一遍纯占屏。
      ② 删 `🔔 上一手` —— 与 `🎙 当前叫牌` 说的是同一件事（当前叫牌就是最新一手）。
      ③ 删 `💡 玩法提示` 静态行 —— 看一次就会背的说明书，移到「🎲 看牌」弹窗。
      ④ 标题/奖池/叫牌/玩家行各砍冗余字（`（吹牛）`、`底注 N`、`在场骰子`→`场上`、`5颗`→`5`、`余`→空格）。
    ⛔ 保留（排版铁律，别再动）：`━━━` 分隔线、玩家行 👉/占位对齐。
    ⚠️ 行动提示（`⏳ 当前行动` / `⏰ 请在 N 秒内加码或开骰`）2026-09-12 已**整体移出牌桌** →
       改由 start_dice_turn_timer 调 announce_turn() 单独发一条（60 秒自动回收）。
       别再往牌桌加回行动行（会与提醒消息重复说同一件事）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    player_line = hub.player_line
    lines = [
        f"🎲 大话骰 · 第{game.hand_no}手",
        f"💰 奖池 {game.pot} ｜ 场上 {game.total_dice()} 颗骰",
    ]
    cur = game.actor if game.phase == "playing" else None
    lines.append("━━━━━━━━━━━━━━━━━")
    # 玩家行**单行**（2026-09-11 用户要求：与炸金花一致压成一行）
    #   在局 `👉 🟢1. Hank 🎲5 2400` ｜ 出局 `{PLAYER_PAD}💀2. Hank`
    # 版式统一走 player_line()（徽标前置 + 序号对齐）：状态由「中段」提到行首，
    # 徽标本身即状态，所以不再写「已出局」；🎲点数与积分作为**信息列**留在名字后面。
    # 注：积分前不加「余」字 —— 2026-09-11 用户点名砍冗余字（`余`→空格），别再补回去。
    for index, uid in enumerate(game.players, 1):
        acting = (uid == cur)
        if uid in game.out:
            lines.append(player_line(index, await get_name(app, uid), "💀", acting=acting))
        else:
            lines.append(player_line(
                index, await get_name(app, uid), "🟢", acting=acting,
                cols=[game.chips[uid]]))
    return "\n".join(lines)


async def dice_turn_notice(game, app):
    """大话骰「轮到谁」提醒文案（单独一条消息，见 announce_turn）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    sget = hub.sget
    cur = game.actor if game.phase == "playing" else None
    if not cur: return None
    return (f"⏳ <b>{await get_name(app, cur)}</b> 轮到你叫牌（加码或开骰）\n"
            f"⏰ 请在 {sget('DICE_THINK_SECONDS')} 秒内操作，超时自动开骰")


def dice_rules_text(game):
    """本局大话骰规则**一行**摘要（给「🎲 看牌」弹窗用）。

    2026-09-12 用户报「看骰子弹窗一大堆文字、反而骰子数字不明显」→ 规则压成一行，
    弹窗主角让给骰子本身。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    dice_active_rules = hub.dice_active_rules
    sget = hub.sget
    _n = len(game.players)
    # 事实同源（见 dice_active_rules），这里只把措辞压成一行
    _rules = dice_active_rules(_n)
    bits = ["1 万能" if _rules["wild"] else "1 不万能"]
    if _rules["straight"]:
        bits.append("顺子算0")
    if _rules["leopard"]:
        bits.append("豹子+2/+1")
    bits.append("一把定胜负" if _rules["duel"] else "输家掉骰")
    return "📖 " + "｜".join(bits)


async def show_dice_action(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _sync_jinhua_msg = hub._sync_jinhua_msg
    dice_buttons = hub.dice_buttons
    dice_table_text = hub.dice_table_text
    get_name = hub.get_name
    # 跳开选人菜单（2026-09-13）：与炸金花比牌菜单同一套做法 —— 同一条牌桌消息
    # 临时切成「选人」视图，用 skip_menu_owner 上锁，只有发起者点得动（见 entry.py）。
    if getattr(game, "skip_menu_owner", None) is not None:
        _owner = game.skip_menu_owner
        _targets = [t for t in game.players
                    if t != _owner and t not in game.out and t in game.bids]
        if _targets:
            _rows = []
            for t in _targets:
                _b = game.bids[t]
                _rows.append([InlineKeyboardButton(
                    f"🎯 {await get_name(app, t)}（{_b[0]}个{_b[1]}）", callback_data=f"dice_skip_{t}")])
            _rows.append([InlineKeyboardButton("❌ 取消", callback_data="dice_skip_cancel")])
            await _sync_jinhua_msg(game, app,
                f"{await dice_table_text(game, app)}\n\n"
                f"🎯 {await get_name(app, _owner)} 选择要跳开的人\n"
                f"　（只结算你和他两个人，其余人底注原路退回）",
                InlineKeyboardMarkup(_rows))
            return
        game.skip_menu_owner = None      # 已经没人可跳开了（都出局/都没叫牌）→ 退回普通牌桌
    cur = game.actor if game.phase == "playing" else None
    # 牌桌统一删旧发新（见 _sync_jinhua_msg），始终顶到群最底部；
    # 行动提示已并入牌桌文本（见 dice_table_text），此处不再追加第二句
    if cur:
        await _sync_jinhua_msg(game, app, await dice_table_text(game, app), dice_buttons(game, cur))
    else:
        await _sync_jinhua_msg(game, app, await dice_table_text(game, app), dice_buttons(game, game.owner_id))


async def start_dice_turn_timer(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _dice_resolve_and_continue = hub._dice_resolve_and_continue
    announce_turn = hub.announce_turn
    dice_min_raise = hub.dice_min_raise
    dice_turn_notice = hub.dice_turn_notice
    get_name = hub.get_name
    safe_send = hub.safe_send
    schedule_notice_delete = hub.schedule_notice_delete
    settle_dice = hub.settle_dice
    sget = hub.sget
    show_dice_action = hub.show_dice_action
    start_dice_turn_timer = hub.start_dice_turn_timer
    game.cancel_timer()
    # 先判终局：showdown 直接结算，绝不留一个带按钮但不结算的死界面（牌局卡死）
    if game.phase == "showdown":
        await settle_dice(game, app); return
    await show_dice_action(game, app)
    if game.phase != "playing": return
    uid = game.actor
    # 行动提醒：单独一条 + 60 秒自动删除（2026-09-12 用户要求，牌桌正文里已不再写行动行）
    game.turn_notice_id = await announce_turn(app, game.chat_id, await dice_turn_notice(game, app),
                                              old_id=game.turn_notice_id)

    async def timeout_action():
        await asyncio.sleep(hub.sget("DICE_THINK_SECONDS"))
        if game.settled or game.phase != "playing" or game.actor != uid: return
        _who = await hub.get_name(app, uid)
        if game.bid:
            ok, _ = game.action(uid, "open")
            if not ok: return
            game.last_action = f"{_who} 超时自动开骰"
            # 超时是玩家掉骰子的直接原因，必须让群里知道发生了什么
            hub.schedule_notice_delete(app, game.chat_id,
                                   await hub.safe_send(app.bot, game.chat_id,
                                                   f"⏰ {_who} 超时未行动（{hub.sget('DICE_THINK_SECONDS')}秒），自动开骰。"))
            await hub._dice_resolve_and_continue(game, app, uid)
        else:
            mr = hub.dice_min_raise(game)
            if not mr: return
            game.action(uid, "bid", mr)
            game.last_action = f"{_who} 超时自动叫 {mr[0]}个{mr[1]}"
            hub.schedule_notice_delete(app, game.chat_id,
                                   await hub.safe_send(app.bot, game.chat_id,
                                                   f"⏰ {_who} 超时未叫牌（{hub.sget('DICE_THINK_SECONDS')}秒），自动叫 {mr[0]}个{mr[1]}。"))
            await hub.start_dice_turn_timer(game, app)
    game.turn_task = asyncio.create_task(timeout_action())


async def _dice_resolve_and_continue(game, app, opener, target=None):
    """掀盅亮牌 → 判定 → 掉骰子 → 重摇续局 / 终局结算。

    `target` 不为空 = **跳开**（2026-09-13 用户要求）：开的是 `target` 这个人本手叫的牌，
    而不是场上最后一次叫牌；判定后**一锤定音直接结算**，只结算跳开者与被跳开者两个人，
    其余人的底注原路退回（见 settle_dice 的 skip_pair 分支）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _dice_duel_mode = hub._dice_duel_mode
    _dice_is_straight = hub._dice_is_straight
    _dice_leopard_bonus = hub._dice_leopard_bonus
    _dice_leopard_kind = hub._dice_leopard_kind
    _dice_rule_on = hub._dice_rule_on
    get_name = hub.get_name
    safe_send = hub.safe_send
    schedule_notice_delete = hub.schedule_notice_delete
    settle_dice = hub.settle_dice
    sget = hub.sget
    start_dice_turn_timer = hub.start_dice_turn_timer
    if target is not None:
        _c, _f = game.bids[target]
        _bid = (_c, _f, target)
    else:
        _bid = game.bid
    bc, bf, bidder = _bid
    actual, loser, n_face, n_one = game.resolve_open(opener, _bid)
    bidder_name = await get_name(app, bidder)
    opener_name = await get_name(app, opener)
    loser_name = await get_name(app, loser)
    lines = [f"🎯 {'跳开' if target is not None else '开骰'}！"
             f"（{opener_name} 开 {bidder_name} 的 {bc}个{bf}）", "亮盅："]
    wild = sget("DICE_WILD_ONE")
    _n = len(game.players)
    _straight_on = _dice_rule_on("DICE_STRAIGHT_ZERO", _n)
    _leopard_on = _dice_rule_on("DICE_LEOPARD_BONUS", _n)
    _bonus_total = 0
    for u in game.alive():
        _hand = " ".join(map(str, game.hands[u]))
        if _straight_on and _dice_is_straight(game.hands[u], wild):
            _hand += "（顺子·算0个）"
        elif _leopard_on:
            _lb = _dice_leopard_bonus(game.hands[u], bf, wild)
            if _lb:
                _hand += f"（{_dice_leopard_kind(game.hands[u], wild)}·{bf}点+{_lb}）"
                _bonus_total += _lb
        lines.append(f"　{await get_name(app, u)}：{_hand}")
    if wild and bf != 1:
        calc = f"{bf}点×{n_face} + 万能1×{n_one} = "
    else:
        calc = ""
    if _bonus_total:
        calc += f"豹子+{_bonus_total} → "
    _duel = _dice_duel_mode(game)      # 一把定胜负：输家直接出局、本局立即结算
    _pen = "输" if _duel else "掉一颗骰子"
    tail = f"{calc}实际 {actual} 个 ≥ 叫 {bc} 个 → {bidder_name} 没吹，{loser_name}（开骰者）{_pen}" \
        if loser is opener else \
        f"{calc}实际 {actual} 个 < 叫 {bc} 个 → {bidder_name} 吹牛实锤，{_pen}"
    if target is not None:
        # 跳开 = 一锤定音：赢家是输家之外的那个人，只结算这两个人
        game.skip_pair = (bidder if loser is opener else opener, loser)
    elim = game.apply_loss(loser)
    if target is not None:
        # 跳开无视「掉骰子多轮制」开关：用户口径就是「只结算这两个人、其余人退回」，
        # 所以这里必须直接进终局结算，不能让输家掉一颗骰子继续打。
        game.phase = "showdown"
    if game.phase == "showdown":
        _end = "本局结束！" if _duel else f"{loser_name} 出局！"
        # 终局必须亮盅：此前两人局只发判定句、不发牌面，群友无法核对「谁该输」
        # （2026-09-11 用户报障的现场就是这种「只看到一句结论、看不到骰子」）
        schedule_notice_delete(app, game.chat_id,
                               await safe_send(app.bot, game.chat_id,
                                               "\n".join(lines + [f"{tail}，{_end}"])))
        await settle_dice(game, app)
        return
    nxt_name = await get_name(app, game.starter_uid)
    tail += f"（剩 {game.dice[loser]} 颗）" if not elim else ""
    lines.append(tail)
    lines.append(f"全员重摇，{nxt_name} 先叫。")
    schedule_notice_delete(app, game.chat_id, await safe_send(app.bot, game.chat_id, "\n".join(lines)))
    await start_dice_turn_timer(game, app)


async def settle_dice(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _check_level_change = hub._check_level_change
    _earn_add = hub._earn_add
    _earn_get = hub._earn_get
    active_dice_games = hub.active_dice_games
    broadcast_big_win = hub.broadcast_big_win
    calc_rake = hub.calc_rake
    commit_rake = hub.commit_rake
    emergency_if_needed = hub.emergency_if_needed
    force_save_now = hub.force_save_now
    game_chips = hub.game_chips
    games_played = hub.games_played
    get_name = hub.get_name
    logger = hub.logger
    record_game_flows = hub.record_game_flows
    safe_delete = hub.safe_delete
    safe_send_long = hub.safe_send_long
    save_data = hub.save_data
    schedule_delete = hub.schedule_delete
    sget = hub.sget
    user_wallet_locks = hub.user_wallet_locks
    if game.settled: return
    game.settled = True; game.cancel_timer(); game.cancel_wait()
    try:
        async with user_wallet_locks([u for u in game.players if u >= 0]):
            survivors = game.alive()
            _skip = getattr(game, "skip_pair", None)
            if _skip:
                # 跳开结算（2026-09-13 用户原话）：「只结算跳开和被跳开人的，其他人积分退回」。
                # 做法：把不相干的人**还原成开局余额**（等于底注原路退回、不赔不赚），
                # 奖池收缩成这两个人的底注之和，再由赢家通吃 —— 一分钱都不会凭空产生或消失。
                _w, _l = _skip
                for uid in game.players:
                    if uid in (_w, _l): continue
                    game.chips[uid] = game.initial_chips.get(uid, game.chips[uid])
                game.pot = game.paid.get(_w, 0) + game.paid.get(_l, 0)
                survivors = [_w]
            if survivors:
                # 一把定胜负时可能有多人存活 → 奖池平分（余数给靠前的，总额严格等于奖池）
                _base, _rest = divmod(game.pot, len(survivors))
                for _i, uid in enumerate(survivors):
                    game.chips[uid] += _base + (1 if _i < _rest else 0)
            else:
                # 极端兜底（理论不可达）：无人存活 → 底注原路退回，禁止积分凭空消失
                for uid in game.players:
                    game.chips[uid] = game.initial_chips.get(uid, game.chips[uid])
            for uid in game.players:
                game_chips[game.chat_id][uid] += game.chips[uid] - game.initial_chips.get(uid, game_chips[game.chat_id][uid])
        save_data(); await asyncio.to_thread(force_save_now)
        names = {uid: await get_name(app, uid) for uid in game.players}
        lines = ["🎲 <b>大话骰结算</b>", "━━━━━━━━━━━━━━━━━", ""]
        if _skip:
            _w, _l = _skip
            lines.append(f"🎯 <b>跳开</b>：{names.get(_w)} ⚔ {names.get(_l)}")
            lines.append(f"🏆 {names.get(_w)} 通吃两人底注 {game.pot}（{names.get(_l)} 输）")
            _others = [u for u in game.players if u not in (_w, _l)]
            if _others:
                lines.append("　" + "、".join(names.get(u, "") for u in _others) + " 底注已原路退回")
        elif survivors:
            if len(survivors) == 1:
                lines.append(f"🏆 幸存者：{names.get(survivors[0])}｜通吃奖池 {game.pot}")
            else:
                lines.append(f"🏆 幸存者 {len(survivors)} 人平分奖池 {game.pot}（每人 {game.pot // len(survivors)}）")
                lines.append("　" + "、".join(names.get(u, "") for u in survivors))
        else:
            lines.append("🏆 本局无人存活，底注已原路退回")
        lines.append("")
        lines.append("终局牌面：")
        for uid in game.players:
            st = "💀出局" if uid in game.out else f"🎲剩{game.dice[uid]}颗"
            lines.append(f"　{names[uid]}：{st}｜底注 {game.paid.get(uid, 0)}")
        # 抽水先算（官方模式）
        _nets = {uid: game.chips[uid] - game.initial_chips[uid] for uid in game.players}
        rake_per = calc_rake(_nets)[1] if game.mode == "official" else {}
        lines.append("")
        lines.append("投入 / 盈亏：")
        for uid in game.players:
            net = _nets[uid]
            r_amt = rake_per.get(uid, 0)
            r_txt = f"（实收 {net - r_amt}，含抽水{r_amt}）" if r_amt else ""
            lines.append(f"　{names[uid]}：投入 {game.paid.get(uid, 0)}｜盈亏 {net:+d}{r_txt}")
        if game.mode == "official":
            record_game_flows(game.chat_id, _nets, "大话骰")
            await commit_rake(app, game.chat_id, rake_per, "大话骰")
            for uid in game.players:
                if uid < 0: continue
                games_played[game.chat_id][uid] += 1
                _gain = _nets[uid] - int(rake_per.get(uid, 0) or 0)
                if _gain > 0:
                    _oe = _earn_get(game.chat_id, uid)
                    _earn_add(game.chat_id, uid, _gain)
                    await _check_level_change(app, game.chat_id, uid, _oe, _earn_get(game.chat_id, uid))
            _top = max(survivors, key=lambda u: _nets.get(u, 0)) if survivors else None
            if _top is not None and _nets.get(_top, 0) > 0:
                await broadcast_big_win(app, game.chat_id, _top, "🎲 大话骰", _nets[_top],
                                        f"🎲 终局剩骰：{game.dice.get(_top, 0)} 颗")
        await safe_delete(app.bot, game.chat_id, game.game_msg_id)
        delivered = await safe_send_long(app.bot, game.chat_id, "\n".join(lines), parse_mode="HTML")
        if sget("SETTLE_DELETE_SECONDS") > 0:
            schedule_delete(app, game.chat_id, delivered, sget("SETTLE_DELETE_SECONDS"))
    except Exception:
        logger.exception("大话骰结算异常")
    finally:
        if active_dice_games.get(game.chat_id) is game: active_dice_games.pop(game.chat_id, None)
        if game.mode == "official":
            for uid in game.players: await emergency_if_needed(game.chat_id, uid, app)
        save_data(); await asyncio.to_thread(force_save_now)


async def refund_dice(game, app, notice):
    """终止大话骰：底注扣在局内副本（钱包结算前不动），弃局即作废副本 = 全额退款。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_dice_games = hub.active_dice_games
    safe_delete = hub.safe_delete
    safe_send = hub.safe_send
    save_data = hub.save_data
    schedule_notice_delete = hub.schedule_notice_delete
    game.cancel_timer(); game.cancel_wait(); game.phase = "cancelled"
    if active_dice_games.get(game.chat_id) is game: active_dice_games.pop(game.chat_id, None)
    await safe_delete(app.bot, game.chat_id, game.game_msg_id)
    # 解散提示挂自动回收：此前是裸 safe_send，「房间已解散」永久堆在群里
    schedule_notice_delete(app, game.chat_id, await safe_send(app.bot, game.chat_id, notice))
    save_data()


async def start_dice_wait_timeout(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _dice_notify_dropped = hub._dice_notify_dropped
    active_dice_games = hub.active_dice_games
    refund_dice = hub.refund_dice
    sget = hub.sget
    start_dice_turn_timer = hub.start_dice_turn_timer
    game.cancel_wait()
    async def countdown():
        _wait = hub.sget("ROOM_WAIT_TIMEOUT")
        await asyncio.sleep(_wait)
        if game.phase != "waiting" or hub.active_dice_games.get(game.chat_id) is not game: return
        if len(game.players) >= 2:
            if game.start():
                await hub._dice_notify_dropped(game, app)
                await hub.start_dice_turn_timer(game, app)
            else:
                # ★ start() 会剔除「余额 < 底注」的玩家，剔完若不足 2 人返回 False。
                #   原写法只写了成功分支 → 房间停在 waiting、倒计时也走完了，
                #   既不解散也不开局（永久卡死）。这里补上解散并退款。
                await hub.refund_dice(game, app, "⌛ 有人积分不足底注，房间已自动解散。")
        else:
            await hub.refund_dice(game, app, f"⌛ 大话骰等待 {_wait} 秒不足 2 人，房间已自动解散。")
    # 必须把任务挂到 game.wait_task 上：
    # ① 不启动 → 等待房永不解散（2026-09-11 事故）；
    # ② 不持有引用 → 任务可能被事件循环 GC 掉。
    game.wait_task = asyncio.create_task(countdown())


async def _dice_notify_dropped(game, app):
    """开局时因底注不足被剔除的玩家，群里明确告知（避免「我怎么没在局里」的困惑）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    safe_send = hub.safe_send
    schedule_notice_delete = hub.schedule_notice_delete
    sget = hub.sget
    if not game.dropped: return
    _names = "、".join([await get_name(app, u) for u in game.dropped])
    # 报**本局实际用的**底注（两人局/多人局分开设定，见 _dice_ante_for）
    _ante = getattr(game, "ante_used", 0) or sget("DICE_ANTE")
    schedule_notice_delete(app, game.chat_id, await safe_send(app.bot, game.chat_id,
                    f"⚠️ {_names} 积分不足 {_ante} 底注，本局未参与（余额可先签到/兑换）。"))


async def cmd_dice(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    DiceGame = hub.DiceGame
    _game_gate = hub._game_gate
    active_dice_games = hub.active_dice_games
    current_game_mode = hub.current_game_mode
    dice_buttons = hub.dice_buttons
    dice_waiting_text = hub.dice_waiting_text
    game_chips = hub.game_chips
    need_auth = hub.need_auth
    panel_adopt = hub.panel_adopt
    poker_room_of = hub.poker_room_of
    require_group_chat = hub.require_group_chat
    safe_send = hub.safe_send
    send_reply = hub.send_reply
    sget = hub.sget
    start_dice_wait_timeout = hub.start_dice_wait_timeout
    update_dice_waiting = hub.update_dice_waiting
    if not await need_auth(update, context): return
    if not await _game_gate(update, context, "dice"): return
    if not await require_group_chat(update, "大话骰", "dice", context): return
    cid, uid = update.effective_chat.id, update.effective_user.id
    game = active_dice_games.get(cid)
    if game:
        if game.phase != "waiting":
            await send_reply(update, context, "当前已有进行中的大话骰。"); return
        room_name, _ = poker_room_of(cid, uid, exclude_game=game)
        if room_name:
            await send_reply(update, context, f"⚠️ 你已在 {room_name} 房间，请先结束再加入大话骰。"); return
        if len(game.players) >= sget("DICE_MAX_PLAYERS"):
            await send_reply(update, context, "等待房间已满。"); return
        if game.add(uid):
            await update_dice_waiting(game, context.application); await send_reply(update, context, "已加入当前等待房间。")
        else: await send_reply(update, context, "你已在等待房间中。")
        return
    room_name, _ = poker_room_of(cid, uid)
    if room_name:
        await send_reply(update, context, f"⚠️ 你已在 {room_name} 房间，请先结束再开大话骰。"); return
    if game_chips[cid][uid] < sget("MIN_ENTRY_CHIPS"):
        await send_reply(update, context, f"❌ 进入大话骰至少需要 {sget('MIN_ENTRY_CHIPS')} 积分。"); return
    game = DiceGame(cid, uid, current_game_mode()); game.add(uid); active_dice_games[cid] = game
    msg = await safe_send(context.bot, cid, await dice_waiting_text(game, context.application),
                          reply_markup=dice_buttons(game, uid))
    if msg:
        game.game_msg_id = msg.message_id
        await panel_adopt(context.bot, cid, msg.message_id)   # 清掉上一局残留牌桌
        await start_dice_wait_timeout(game, context.application)
