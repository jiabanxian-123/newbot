# -*- coding: utf-8 -*-
"""feature/blackjack —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

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

class BlackjackGame:
    def __init__(self, cid, owner, mode="official"):
        self.chat_id, self.owner_id, self.mode = cid, owner, mode
        self.phase = "waiting" # waiting, playing, dealer_turn, finished
        self.players = [] # uid list
        self.bets = {} # uid -> amount
        self.hands = defaultdict(list) # uid -> cards
        self.dealer_hand = []
        self.deck = []
        self.current_player_idx = 0
        self.game_msg_id = None
        self.action_msg_id = None # 用于置底的动态按钮消息 ID
        self.name_cache = {}
        self.timer_task = None
        self.wait_task = None # 新增等待解散任务变量
        self.settled = False
        self.turn_notice_id = None   # 「轮到谁行动」提醒消息 id（单独一条、60 秒自动回收，见 announce_turn）

    def cancel_timer(self):
        # ★ 不能把「自己」取消掉：超时任务 timeout() 内部会调 start_bj_turn_timer，
        #   而后者的第一行就是 cancel_timer() —— 若当前任务正是 timer_task，
        #   就会把自己 cancel，紧接着的 await 立刻抛 CancelledError，
        #   新建计时器那一行**永不执行** → 下一位玩家不手动操作就永久停摆。
        #   德州 / 金花 / 大话骰都有这个守卫，21点此前漏了。
        try:
            _cur = asyncio.current_task()
        except (RuntimeError, AttributeError):
            _cur = None
        if (self.timer_task and not self.timer_task.done()
                and self.timer_task is not _cur):
            self.timer_task.cancel()

    def cancel_wait(self):
        if self.wait_task and not self.wait_task.done():
            self.wait_task.cancel()

    def add_player(self, uid, bet):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        pending_game_bets = hub.pending_game_bets
        if self.phase != "waiting" or uid in self.players: return False
        self.players.append(uid)
        self.bets[uid] = bet

        # 记录退款保护
        hub.pending_game_bets[self.chat_id][uid]["21"] = {"amount": bet, "mode": self.mode}
        return True

    def start(self):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        sget = hub.sget
        if not self.players: return False
        # ⛔ 幂等闸门（2026-09-12 修「庄家每一把都 20/21」）：
        #   入口有两处会调 start()——发起人点「🎮 开始游戏」+ 等待房 60 秒自动开局；
        #   而本函数过去**不看 phase**，双击/抢跑就会再发一轮牌：每人 4 张、庄家 4 张。
        #   实测（20 万局，8 副牌）：正常单人玩家胜率 38.2%／庄家 56.9%；
        #   连发两轮 → 玩家 20.0%／庄家 79.3%——玩家几乎把把爆牌输钱，看着就像「庄家在作弊」。
        #   线上 09-10 那天「5 人全部输钱、赢家合计 +0」正是这条路径。
        if self.phase != "waiting": return False
        self.phase = "playing"
        # 防御式清空：任何调用路径都不该带着上一轮的牌开局（hands 是 defaultdict，会静默累积）
        self.hands.clear()
        self.dealer_hand = []
        self.current_player_idx = 0
        self.settled = False
        self.deck = [r + s for r in "23456789TJQKA" for s in "shdc"] * hub.sget("BLACKJACK_DECKS")
        random.shuffle(self.deck)
        # 发初始牌
        for _ in range(2):
            for p in self.players: self.hands[p].append(self.deck.pop())
            self.dealer_hand.append(self.deck.pop())
        return True

    def get_score(self, cards):
        score, aces = 0, 0
        val_map = {**{str(i): i for i in range(2, 10)}, "T": 10, "J": 10, "Q": 10, "K": 10, "A": 11}
        for c in cards:
            score += val_map[c[0]]
            if c[0] == "A": aces += 1
        while score > 21 and aces:
            score -= 10; aces -= 1
        return score

    def is_blackjack(self, cards):
        return len(cards) == 2 and self.get_score(cards) == 21

    def _draw(self):
        """发牌；牌堆耗尽时自动重洗一副新牌，避免抽牌崩溃。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        sget = hub.sget
        if not self.deck:
            self.deck = [r + s for r in "23456789TJQKA" for s in "shdc"] * hub.sget("BLACKJACK_DECKS")
            random.shuffle(self.deck)
        return self.deck.pop()

    def hit(self, uid):
        if self.phase != "playing" or self.players[self.current_player_idx] != uid: return None
        card = self._draw()
        self.hands[uid].append(card)
        score = self.get_score(self.hands[uid])
        if score >= 21: self.next_player()
        return card

    def double_down(self, uid):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        pending_game_bets = hub.pending_game_bets
        if self.phase != "playing" or self.players[self.current_player_idx] != uid: return False
        # 翻倍：再扣一份钱
        bet = self.bets[uid]
        self.bets[uid] += bet

        # 记录退款保护 (更新)
        hub.pending_game_bets[self.chat_id][uid]["21"] = {"amount": self.bets[uid], "mode": self.mode}

        # 强制摸一张
        self.hands[uid].append(self._draw())
        # 强制停牌
        self.next_player()
        return True

    def next_player(self):
        self.current_player_idx += 1
        if self.current_player_idx >= len(self.players):
            self.phase = "dealer_turn"

    def dealer_play(self):
        while self.get_score(self.dealer_hand) < 17:
            self.dealer_hand.append(self._draw())
        self.phase = "finished"

    def get_card_str(self, cards, hide_first=False):
        res = []
        for i, c in enumerate(cards):
            if i == 0 and hide_first: res.append("❓")
            else:
                raw = c.replace("T", "10")
                suit = {"s":"♠️", "h":"♥️", "d":"♦️", "c":"♣️"}.get(raw[-1], raw[-1])
                res.append(f"{suit}{raw[:-1]}")
        return " ".join(res)


async def bj_turn_notice(game, app):
    """21点「轮到谁」提醒文案（单独一条消息，见 announce_turn）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    sget = hub.sget
    if game.phase != "playing": return None
    uid = game.players[game.current_player_idx]
    hand = game.get_card_str(game.hands[uid])
    return (f"⏳ <b>{await get_name(app, uid)}</b> 轮到你行动｜你的手牌 {hand}（{game.get_score(game.hands[uid])}点）\n"
            f"⏰ 请在 {sget('TURN_TIMEOUT')} 秒内操作，超时自动停牌")


async def start_bj_turn_timer(game, app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_blackjack_games = hub.active_blackjack_games
    announce_turn = hub.announce_turn
    bj_turn_notice = hub.bj_turn_notice
    get_name = hub.get_name
    safe_send = hub.safe_send
    schedule_notice_delete = hub.schedule_notice_delete
    sget = hub.sget
    start_bj_turn_timer = hub.start_bj_turn_timer
    update_blackjack_ui = hub.update_blackjack_ui
    game.cancel_timer()
    curr_uid = game.players[game.current_player_idx]
    # 行动提醒：单独一条 + 60 秒自动删除（2026-09-12 用户要求，牌桌正文里已不再写行动行）
    game.turn_notice_id = await announce_turn(app, game.chat_id, await bj_turn_notice(game, app),
                                              old_id=game.turn_notice_id)
    async def timeout():
        await asyncio.sleep(hub.sget("TURN_TIMEOUT"))
        if hub.active_blackjack_games.get(game.chat_id) is not game: return  # 游戏已终止或被替换
        if game.phase == "playing" and game.players[game.current_player_idx] == curr_uid:
            game.next_player()
            hub.schedule_notice_delete(app, game.chat_id,
                                   await hub.safe_send(app.bot, game.chat_id, f"⏰ {await hub.get_name(app, curr_uid)} 超时自动停牌。"))
            if game.phase == "dealer_turn": await hub.update_blackjack_ui(game, app)
            else: await hub.update_blackjack_ui(game, app); await hub.start_bj_turn_timer(game, app)
    game.timer_task = asyncio.create_task(timeout())


async def start_bj_wait_timeout(game, app):
    """21点等待房倒计时：有人加入则自动开局，无人加入自动解散。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_blackjack_games = hub.active_blackjack_games
    retire_panel = hub.retire_panel
    sget = hub.sget
    start_bj_turn_timer = hub.start_bj_turn_timer
    update_blackjack_ui = hub.update_blackjack_ui
    game.cancel_wait()
    async def expire():
        _wait = hub.sget("ROOM_WAIT_TIMEOUT")
        await asyncio.sleep(_wait)
        if game.phase != "waiting" or hub.active_blackjack_games.get(game.chat_id) is not game:
            return
        if game.players:
            if game.start():
                await hub.update_blackjack_ui(game, app)
                await hub.start_bj_turn_timer(game, app)
        else:
            hub.active_blackjack_games.pop(game.chat_id, None)
            # 2026-09-12 修：解散提示是靠 edit 把常驻看板**改写**出来的 ⇒ 不经过 send
            #   ⇒ 全局「默认自动删除」收不到它 ⇒ 此前永久挂在群里（用户截图 07:51/08:07）。
            #   改走 retire_panel：改写 + 排入 PANEL_DELETE_SECONDS 回收。
            await hub.retire_panel(app, game.chat_id, game.game_msg_id,
                               f"⌛ 21点等待 {_wait} 秒无人加入，房间已自动解散。")
    game.wait_task = asyncio.create_task(expire())


async def build_blackjack_wait_board(game, app):
    """构建 21点 等待房间阶段的看板（文本+按钮），供首发与重发复用。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    blackjack_history = hub.blackjack_history
    get_name = hub.get_name
    sget = hub.sget
    history_list = "".join(blackjack_history[game.chat_id][-10:]) or "暂无"
    text = (
        f"🃏 <b>21点 (Blackjack)</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>庄家路书</b>：{history_list}\n\n"
        f"发起人：{await get_name(app, game.owner_id)}\n\n"
        f"已加入：\n"
    )
    for uid in game.players:
        text += f"- {await get_name(app, uid)} (下注: {game.bets[uid]})\n"
    text += f"\n⏰ 有人加入后 {sget('ROOM_WAIT_TIMEOUT')} 秒自动开局，无人加入自动解散。\n"
    # 按钮每行最多 2 个：Telegram 一行挤 4 个会截断成「📥 加入 (下...」（用户 2026-09-10 截图报障）
    kb = [[InlineKeyboardButton(f"📥 加入 {b}", callback_data=f"bj_join_{b}") for b in sget("BJ_JOIN_BETS")[i:i + 2]]
          for i in range(0, len(sget("BJ_JOIN_BETS")), 2)]
    if game.players: kb.append([InlineKeyboardButton("🎮 开始游戏", callback_data="bj_start")])
    kb.append([InlineKeyboardButton("❌ 终止", callback_data="bj_end")])
    return text, InlineKeyboardMarkup(kb)


async def update_blackjack_ui(game, app):
    """21 点牌桌刷新**总机**：按 `game.phase` 分派到四个阶段函数（每个都能单独测）。

    拆分前它是一个 219 行的函数，四个阶段挤在一起（算状态 / 拼文案 / 拼按钮 / 结算 / 退款兜底），
    改按钮布局得先翻过算分逻辑。现在：
      waiting      → `_bj_render_waiting`
      playing      → `_bj_render_playing`
      dealer_turn  → `_bj_dealer_turn`
      finished     → `_bj_finish`

    ⚠️ 本函数**不再持有 hub 别名**：分支头只比较 `game.phase`，不碰任何 bot 全局量。
    """
    if game.phase == "waiting":
        await _bj_render_waiting(game, app)
    elif game.phase == "playing":
        await _bj_render_playing(game, app)
    elif game.phase == "dealer_turn":
        await _bj_dealer_turn(game, app)
    elif game.phase == "finished":
        await _bj_finish(game, app)


async def _bj_render_waiting(game, app):
    """等待开局（等人下注）：渲染/更新等待面板。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    build_blackjack_wait_board = hub.build_blackjack_wait_board
    safe_edit = hub.safe_edit
    safe_send = hub.safe_send

    text, kb = await build_blackjack_wait_board(game, app)
    if game.game_msg_id:
        await safe_edit(app.bot, game.chat_id, game.game_msg_id, text, reply_markup=kb, parse_mode="HTML")
    else:
        msg = await safe_send(app.bot, game.chat_id, text, reply_markup=kb, parse_mode="HTML")
        if msg: game.game_msg_id = msg.message_id


async def _bj_render_playing(game, app):
    """行牌中的牌桌刷新：
  ① 算状态（谁在行动、是否爆牌/停牌）② 拼文案 ③ 拼按钮 → 原地编辑同一条消息。

⛔ 别再往牌桌加回「行动提示」行：2026-09-12 已整体移到 `start_bj_turn_timer` 的
   `announce_turn()`（单独一条、60 秒回收），加回来会重复说同一件事。
⚠️ 玩家状态列判定顺序：**先看爆牌再看轮次** —— 爆牌玩家轮次也「已过」，
   反过来判就永远显示不出 ❌（玩家会以为这局自己没爆）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    game_chips = hub.game_chips
    get_name = hub.get_name
    player_line = hub.player_line
    safe_delete = hub.safe_delete
    safe_edit = hub.safe_edit
    safe_send = hub.safe_send

    curr_uid = game.players[game.current_player_idx]
    dealer_peek = game.get_card_str(game.dealer_hand, True)
    # 单一权威面板：牌桌+操作按钮同一条消息，原地编辑不闪跳；信息不再写两遍
    lines = ["🃏 <b>21点</b>", f"🏛 庄家：{dealer_peek}", "━━━━━━━━━━━━━━━━━"]
    # 行动提示（`⏳ 名 行动｜我 ♠️A ♥️K (21点)`）2026-09-12 已**整体移出牌桌** →
    # 改由 start_bj_turn_timer 调 announce_turn() 单独发一条（60 秒自动回收）。
    # 玩家自己的手牌在下方玩家列表里已有，牌桌不再重复。
    # ⛔ 别再往牌桌加回行动行（会与提醒消息重复说同一件事）。
    lines.append("")
    # 玩家行：统一走 player_line()（徽标前置 + 序号对齐）。
    #   21点原来**没有任何状态列**，与另外三款不一致 ⇒ 本轮补上，语义与德州/金花对齐：
    #     ❌ 爆牌（点数 > 21）｜✋ 已停牌（轮次已过、人还在）｜🟢 待行动
    #   ⚠️ 判定顺序：先看爆牌再看轮次 —— 爆牌的玩家轮次也「已过」，
    #      若反过来判就永远显示不出 ❌（玩家会以为这局自己没爆）。
    #   信息列（手牌 + 点数）留在名字后面，与徽标隔开一整列，不会读成两个状态。
    for i, uid in enumerate(game.players):
        score = game.get_score(game.hands[uid])
        if score > 21:
            badge = "❌"
        elif i < game.current_player_idx:
            badge = "✋"
        else:
            badge = "🟢"
        lines.append(player_line(
            i + 1, await get_name(app, uid), badge, acting=(i == game.current_player_idx),
            cols=[game.get_card_str(game.hands[uid]), f"({score})"]))
    text = "\n".join(lines)

    kb_rows = [[
        InlineKeyboardButton("🃏 要牌", callback_data=f"bj_hit_{curr_uid}"),
        InlineKeyboardButton("✋ 停牌", callback_data=f"bj_stand_{curr_uid}")
    ]]
    wallet = game_chips
    if len(game.hands[curr_uid]) == 2 and wallet[game.chat_id][curr_uid] >= game.bets[curr_uid] and not game.is_blackjack(game.hands[curr_uid]):
        kb_rows.append([InlineKeyboardButton("💰 双倍", callback_data=f"bj_double_{curr_uid}")])
    kb = InlineKeyboardMarkup(kb_rows)

    edited = await safe_edit(app.bot, game.chat_id, game.game_msg_id, text, reply_markup=kb, parse_mode="HTML") if game.game_msg_id else None
    if edited:
        game.game_msg_id = edited.message_id
    else:
        await safe_delete(app.bot, game.chat_id, game.game_msg_id)
        msg = await safe_send(app.bot, game.chat_id, text, reply_markup=kb, parse_mode="HTML")
        if msg: game.game_msg_id = msg.message_id
    game.action_msg_id = None


async def _bj_dealer_turn(game, app):
    """庄家补牌（`game.dealer_play()`）后转结算；消息删除统一交给 finished 分支，避免重复删。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    update_blackjack_ui = hub.update_blackjack_ui

    # 庄家补牌后直接进入结算（消息删除由 finished 分支统一处理，避免重复删除）
    game.dealer_play()
    # 确保跳转到 finished 逻辑
    await update_blackjack_ui(game, app)


async def _bj_finish(game, app):
    """结算：算派彩 → 统一动钱包 → 拼战报 → 发榜 → 清场。

⚠️ 这里保留原样的 `try/except/finally` 与 `payments_applied` 兜底：
   派彩前出错 → 退还投注；派彩后出错 → 只提示战报渲染失败（积分已保存）。
   **不要**把这段拆出去：`payments_applied` 一旦跨函数传递，异常路径就可能判错，
   那是「双重退款 / 吞钱」级别的坑。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    RANK_PAGE_SIZE = hub.RANK_PAGE_SIZE
    _check_level_change = hub._check_level_change
    _earn_add = hub._earn_add
    _earn_get = hub._earn_get
    active_blackjack_games = hub.active_blackjack_games
    blackjack_history = hub.blackjack_history
    blackjack_profit_by_date = hub.blackjack_profit_by_date
    broadcast_big_win = hub.broadcast_big_win
    business_date = hub.business_date
    calc_rake = hub.calc_rake
    commit_rake = hub.commit_rake
    force_save_now = hub.force_save_now
    game_chips = hub.game_chips
    games_played = hub.games_played
    get_name = hub.get_name
    logger = hub.logger
    pending_game_bets = hub.pending_game_bets
    rank_line = hub.rank_line
    safe_delete = hub.safe_delete
    safe_send = hub.safe_send
    safe_send_long = hub.safe_send_long
    save_data = hub.save_data
    schedule_delete = hub.schedule_delete
    schedule_notice_delete = hub.schedule_notice_delete
    send_settle_rank = hub.send_settle_rank
    sget = hub.sget
    total_profit_by_game = hub.total_profit_by_game
    user_wallet_locks = hub.user_wallet_locks

    if getattr(game, "settled", False):
        return
    game.settled = True
    # 增加整体 try-except 保护
    try:
        payments_applied = False
        await safe_delete(app.bot, game.chat_id, game.action_msg_id)
        d_score = game.get_score(game.dealer_hand)
        d_bust = d_score > 21
        text = (f"🃏 <b>21点 · 结算</b>\n━━━━━━━━━━━━━━━━━\n"
                f"🏛 <b>庄家</b> {game.get_card_str(game.dealer_hand)} · <b>{d_score}</b>"
                f"{' 💥 爆牌' if d_bust else ''}\n")
        date = business_date()
        wallet = game_chips

        lines = []
        # 预先获取所有名字，提高 HTML 生成速度
        player_names = {}
        for uid in game.players: player_names[uid] = await get_name(app, uid)

        # ===== 阶段一：只计算派彩，绝不动钱包（先算后付，杜绝中途异常双重退款）=====
        payments_applied = False
        payout_plan = []  # (uid, payout, net)
        for uid in game.players:
            p_score = game.get_score(game.hands[uid])
            bet = game.bets[uid]
            result_str = ""
            payout = 0

            dealer_bj = game.is_blackjack(game.dealer_hand)
            player_bj = game.is_blackjack(game.hands[uid])
            if p_score > 21:
                result_str = "💥 爆牌"; payout = 0
            elif dealer_bj and not player_bj:
                result_str = "🏛 庄家天生21"; payout = 0
            elif d_score > 21:
                if player_bj: result_str = "🃏 Blackjack"; payout = int(bet * 2.5)
                else: result_str = "🏛 庄家爆牌"; payout = bet * 2
            elif p_score > d_score:
                if player_bj: result_str = "🃏 Blackjack"; payout = int(bet * 2.5)
                else: result_str = "🎉 获胜"; payout = bet * 2
            elif p_score < d_score:
                result_str = "💸 战败"; payout = 0
            else:
                if player_bj and dealer_bj: result_str = "🤝 双天生21"; payout = bet
                # ★ 玩家是「两张 21 点」(Blackjack)，庄家用**三张**才凑到 21 ——
                #   庄家那手不算 Blackjack（is_blackjack 要求恰好两张），玩家应当
                #   按 2.5 倍赢。原来的 elif 链落到这里只认「双方都是天生」，
                #   其余一律判平局 → 玩家少拿 1.5 倍底注。
                elif player_bj: result_str = "🃏 Blackjack"; payout = int(bet * 2.5)
                else: result_str = "🤝 平局"; payout = bet

            net = payout - bet
            hand_text = game.get_card_str(game.hands[uid])
            # 抽水在面板体现：赢家单独一行标「抽水」，不再塞进盈亏后面的括号里
            r_amt = calc_rake({uid: net})[1].get(uid, 0) if game.mode == "official" else 0
            head = f"👤 <b>{player_names[uid]}</b> {hand_text} · <b>{p_score}</b>\n"
            if r_amt:
                lines.append(f"{head}"
                             f"　　{result_str}｜盈亏 <b>{net:+d}</b>\n"
                             f"　　　└ 实收 <b>{net - r_amt:+d}</b>（抽水 {r_amt}）")
            else:
                lines.append(f"{head}"
                             f"　　{result_str}｜盈亏 <b>{net:+d}</b>")
            payout_plan.append((uid, payout, net))
        # 抽水金额先算好（与面板一致），结算落账时一并扣
        rake_per = calc_rake({uid: net for uid, _p, net in payout_plan})[1] if game.mode == "official" else {}

        # ===== 阶段二：全部算成功后，统一改钱包 + 写盈亏 + 清退款记录 =====
        async with user_wallet_locks([uid for uid, _, _ in payout_plan]):
            for uid, payout, net in payout_plan:
                wallet[game.chat_id][uid] += payout
                if game.mode == "official":
                    blackjack_profit_by_date[date][game.chat_id][uid] += net
                pending_game_bets[game.chat_id].get(uid, {}).pop("21", None)
        payments_applied = True
        save_data(); await asyncio.to_thread(force_save_now)

        # 大奖战报：官方模式玩家净赢超阈值 → 广播其他授权群
        if game.mode == "official" and payout_plan:
            best = max(payout_plan, key=lambda x: x[2])
            if best[2] > 0: await broadcast_big_win(app, game.chat_id, best[0], "♠️ 21点", best[2])
            await commit_rake(app, game.chat_id, rake_per, "21点")
            # 累计参与局数（归零门槛）+ 赢分计入累计积分 + 升级通知
            for uid, _payout, _net in payout_plan:
                games_played[game.chat_id][uid] += 1
                # 实际到手 = 净赢 - 本局抽水（抽水已在上面扣除）
                _gain = _net - int(rake_per.get(uid, 0) or 0)
                if _gain > 0:
                    _oe = _earn_get(game.chat_id, uid)
                    _earn_add(game.chat_id, uid, _gain)
                    await _check_level_change(app, game.chat_id, uid, _oe, _earn_get(game.chat_id, uid))

        # 记录庄家历史 (仅记录本局主要趋势)
        if game.mode == "official":
            # 计算本局玩家总体输赢，用于生成庄家路书图标
            total_net = sum(net for (uid, payout, net) in payout_plan)
            history_icon = "🏛" if total_net < 0 else ("🤝" if total_net == 0 else "👤")
            blackjack_history[game.chat_id] = (blackjack_history[game.chat_id] + [history_icon])[-10:]

        text += "\n\n".join(lines)

        # 累计盈利榜单独发一条（2026-09-11 用户要求：结算正文太长像刷屏，榜单拆开发）
        _rank_lines = None
        if game.mode == "official":
            bj_rank = sorted(total_profit_by_game(blackjack_profit_by_date, game.chat_id).items(), key=lambda item: item[1], reverse=True)[:RANK_PAGE_SIZE]
            if bj_rank:
                _rank_lines = ["🏆 <b>21点 累计盈利榜（总数）</b>"]
                for i, (u, a) in enumerate(bj_rank, 1):
                    name = game.name_cache.get(u) or await get_name(app, u)
                    game.name_cache[u] = name
                    _rank_lines.append(rank_line(i, u, name, f"：{a:+d}"))

        await safe_delete(app.bot, game.chat_id, game.game_msg_id)
        settled_msgs = await safe_send_long(app.bot, game.chat_id, text, parse_mode="HTML")
        if sget("SETTLE_DELETE_SECONDS") > 0:
            schedule_delete(app, game.chat_id, settled_msgs, sget("SETTLE_DELETE_SECONDS"))
        await send_settle_rank(app, game.chat_id, _rank_lines)
    except Exception:
        logger.exception("21点结算显示失败")
        if not payments_applied:
            # 派彩前出错，退还投注
            wallet = game_chips
            for uid in game.players:
                wallet[game.chat_id][uid] += game.bets[uid]
            schedule_notice_delete(app, game.chat_id, await safe_send(app.bot, game.chat_id, "⚠️ 21点结算异常，本局已退款，积分不受影响。"), kind="settle")
        else:
            schedule_notice_delete(app, game.chat_id, await safe_send(app.bot, game.chat_id, "⚠️ 21点已结算，但由于 HTML 渲染问题无法显示详细战报。积分已保存。"), kind="settle")
    finally:
        active_blackjack_games.pop(game.chat_id, None)
        save_data()


async def cmd_21(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BlackjackGame = hub.BlackjackGame
    _game_gate = hub._game_gate
    active_blackjack_games = hub.active_blackjack_games
    current_game_mode = hub.current_game_mode
    need_auth = hub.need_auth
    panel_adopt = hub.panel_adopt
    require_group_chat = hub.require_group_chat
    send_reply = hub.send_reply
    start_bj_wait_timeout = hub.start_bj_wait_timeout
    update_blackjack_ui = hub.update_blackjack_ui
    if not await need_auth(update, context): return
    if not await _game_gate(update, context, "blackjack"): return
    if not await require_group_chat(update, "21点", "21", context): return
    cid, uid = update.effective_chat.id, update.effective_user.id
    if cid in active_blackjack_games:
        g = active_blackjack_games[cid]
        if g.phase == "waiting":
            # 已有等待房：绝不发第二块面板（两块并存时旧面板按钮会指到已失效状态，点了没反应）。面板状态没变，无需编辑，只提示。
            await send_reply(update, context, "本群已有等待中的 21点，请在上面的面板加入。")
        else:
            await send_reply(update, context, "当前已有 21点 进行中。")
        return
    mode = current_game_mode()
    game = BlackjackGame(cid, uid, mode)
    active_blackjack_games[cid] = game
    # 发起人不自动入座：想玩自己点「加入」下注，不强制扣分（2026-09-08 用户要求）
    await update_blackjack_ui(game, context.application)  # 直接发送等待房界面，无"准备中"占位
    if game.game_msg_id:
        await panel_adopt(context.bot, cid, game.game_msg_id)   # 清掉上一局残留牌桌
    await start_bj_wait_timeout(game, context.application) # 启动等待超时


__all__ = [
    "BlackjackGame",
    "bj_turn_notice",
    "build_blackjack_wait_board",
    "cmd_21",
    "start_bj_turn_timer",
    "start_bj_wait_timeout",
    "update_blackjack_ui",
]
