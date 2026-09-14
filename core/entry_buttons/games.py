# -*- coding: utf-8 -*-
"""按钮回调 handler —— 2026-09-13 从 core/entry.py 的 on_button 拆出。

只做搬运，未改任何逻辑。每个函数对应 on_button 里一个 `data` 命名空间分支；
外层保留 `if <原 test>: await <fn>(...); return`（分支体命中后必 return，故等价）。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
⚠️ 本包**禁止** import core.entry（循环导入）。
"""
import time

from core import hub



async def _btn_blackjack(update, context, q, cid, uid, data):
    action_notice = hub.action_notice
    active_blackjack_games = hub.active_blackjack_games
    game_chips = hub.game_chips
    is_bot_admin = hub.is_bot_admin
    pending_game_bets = hub.pending_game_bets
    retire_panel = hub.retire_panel
    start_bj_turn_timer = hub.start_bj_turn_timer
    update_blackjack_ui = hub.update_blackjack_ui
    wallet_locks = hub.wallet_locks
    game = active_blackjack_games.get(cid)
    if not game: await q.answer("游戏已结束", show_alert=True); return
    if data.startswith("bj_join_"):
        bet = int(data.split("_")[2])
        wallet = game_chips
        async with wallet_locks[uid]:
            if wallet[cid][uid] < bet: await q.answer("积分不足", show_alert=True); return
            if game.add_player(uid, bet):
                wallet[cid][uid] -= bet
            else:
                await q.answer("你已在局中或无法加入", show_alert=True); return
        await q.answer("已加入"); await update_blackjack_ui(game, context.application)
    elif data == "bj_start":
        if uid != game.owner_id: await q.answer("仅发起人可开始", show_alert=True); return
        # 幂等：等待房 60 秒自动开局与本按钮会抢跑；连点也在这里被挡（见 BlackjackGame.start 注释）
        if game.phase != "waiting": await q.answer("游戏已经开始", show_alert=True); return
        if game.start(): 
            game.cancel_wait() # 开始后取消等待计时
            await update_blackjack_ui(game, context.application); await start_bj_turn_timer(game, context.application)
        else: await q.answer("人数不足", show_alert=True)
    elif data.startswith("bj_hit_"):
        if uid not in game.players:
            await q.answer("❌ 你未参与本局游戏。", show_alert=True); return
        if str(uid) != data.split("_")[2]: await q.answer("不是你的回合", show_alert=True); return
        card = game.hit(uid)
        if card is None:  # 超时/并发竞态下已不是该玩家回合（stale 按钮），不能让 get_card_str 崩溃
            await q.answer("不是你的回合", show_alert=True); return
        await q.answer(f"你抽到了 {game.get_card_str([card])}")
        if game.phase == "finished" or game.phase == "dealer_turn": await update_blackjack_ui(game, context.application)
        else: await update_blackjack_ui(game, context.application); await start_bj_turn_timer(game, context.application)
    elif data.startswith("bj_stand_"):
        if uid not in game.players:
            await q.answer("❌ 你未参与本局游戏。", show_alert=True); return
        if str(uid) != data.split("_")[2]: await q.answer("不是你的回合", show_alert=True); return
        # 额外校验：必须当前确为该玩家回合，防止旧按钮双击跳过下一位玩家
        if game.players[game.current_player_idx] != uid: await q.answer("不是你的回合", show_alert=True); return
        game.next_player(); await q.answer("停牌")
        if game.phase == "finished" or game.phase == "dealer_turn": await update_blackjack_ui(game, context.application)
        else: await update_blackjack_ui(game, context.application); await start_bj_turn_timer(game, context.application)
    elif data.startswith("bj_double_"):
        if uid not in game.players:
            await q.answer("❌ 你未参与本局游戏。", show_alert=True); return
        if str(uid) != data.split("_")[2]: await q.answer("不是你的回合", show_alert=True); return
        wallet = game_chips

        # 用户级锁包裹检查余额→扣款，防止并发超扣
        async with wallet_locks[uid]:
            if wallet[cid][uid] < game.bets[uid]: await q.answer("积分不足，无法双倍", show_alert=True); return
            # 原子化：先让 game 校验回合并翻倍（内部翻倍 bets + 更新退款保护），
            # 仅成功才扣钱；避免超时/重复点击导致静默丢分
            prev_bet = game.bets[uid]
            if not game.double_down(uid):
                await q.answer("操作失败：已不是你的回合", show_alert=True); return
            wallet[cid][uid] -= prev_bet
        await q.answer("双倍下注！摸牌并停牌")
        await action_notice(cid, context.application, uid, "选择了双倍下注！")

        if game.phase == "finished" or game.phase == "dealer_turn": await update_blackjack_ui(game, context.application)
        else: await update_blackjack_ui(game, context.application); await start_bj_turn_timer(game, context.application)
    elif data == "bj_end":
        if not is_bot_admin(uid) and uid != game.owner_id: await q.answer("权限不足", show_alert=True); return
        game.cancel_timer(); game.cancel_wait()
        # 退还本局下注
        wallet = game_chips
        for p_uid, bet in game.bets.items():
            wallet[cid][p_uid] += bet
            pending_game_bets[cid].get(p_uid, {}).pop("21", None)
        active_blackjack_games.pop(cid, None)
        await retire_panel(context.application, cid, game.game_msg_id, "🛑 21点已手动终止，积分已退回。")
    return


async def _btn_texas(update, context, q, cid, uid, data):
    _season_base = hub._season_base
    action_notice = hub.action_notice
    active_poker_games = hub.active_poker_games
    card_str = hub.card_str
    game_chips = hub.game_chips
    handle_texas_reveal = hub.handle_texas_reveal
    is_bot_admin = hub.is_bot_admin
    poker_room_of = hub.poker_room_of
    refund_poker = hub.refund_poker
    save_data = hub.save_data
    season_games = hub.season_games
    season_joined = hub.season_joined
    season_points = hub.season_points
    season_rebuy = hub.season_rebuy
    settle_poker = hub.settle_poker
    sget = hub.sget
    start_turn_timer = hub.start_turn_timer
    update_poker_waiting = hub.update_poker_waiting
    game = active_poker_games.get(cid)
    if data == "texas_reveal":
        await handle_texas_reveal(cid, uid, q, context); return
    if not game: await q.answer("德州游戏已结束", show_alert=True); return
    if data == "texas_hand":
        hand = game.hands.get(uid); await q.answer(f"你的手牌：{card_str(hand[0])}  {card_str(hand[1])}" if hand and uid not in game.folded else "当前无法查看手牌", show_alert=True); return
    if data == "texas_end":
        if not is_bot_admin(uid) and uid not in game.players:
            await q.answer("权限不足", show_alert=True); return
        await refund_poker(game, context.application, "🛑 德州已终止，积分已退回。")
        await q.answer("本局已终止")
        return
    if game.phase == "waiting":
        if data == "texas_join":
            room_name, _ = poker_room_of(cid, uid, exclude_game=game)
            if room_name:
                await q.answer(f"你已在 {room_name} 房间，请先结束再加入", show_alert=True); return
            if game.season:
                if uid not in season_joined.get(cid, set()):
                    season_joined.setdefault(cid, set()).add(uid)
                    if uid not in season_points.get(cid, {}):
                        season_points[cid][uid] = _season_base(cid, uid)   # 含赛前兑换的底分
                        season_games[cid][uid] = 0
                        season_rebuy[cid][uid] = 0
                    save_data()
                if season_points[cid][uid] <= 0:
                    await q.answer("赛季分不足，无法加入", show_alert=True); return
                if sget("SEASON_MIN_ENTRY_CHIPS") and season_points[cid][uid] < sget("SEASON_MIN_ENTRY_CHIPS"):
                    await q.answer(f"进入赛季至少需要 {sget('SEASON_MIN_ENTRY_CHIPS')} 赛季分，你当前 {season_points[cid][uid]}",
                                   show_alert=True); return
            else:
                wallet = game_chips
                if wallet[cid][uid] < sget("MIN_ENTRY_CHIPS"):
                    await q.answer(f"进入德州至少需要 {sget('MIN_ENTRY_CHIPS')} 积分", show_alert=True); return
            if game.add(uid):
                await q.answer("已加入"); await update_poker_waiting(game, context.application)
            else: await q.answer("你已在等待房间中。", show_alert=True)
        elif data == "texas_start" and uid == game.owner_id and game.start():
            game.cancel_wait()
            await q.answer("游戏开始"); await start_turn_timer(game, context.application)
        else: await q.answer("无法执行此操作", show_alert=True)
        return
    if uid not in game.players:
        # 2026-09-13 用户报障：不在局内的人点牌桌会导致**界面重新刷新**。
        # 只提示、绝不重绘 —— 重绘是「删旧消息 + 发新消息」，群里会看到
        # 「X 删除了消息」并把牌桌顶到最底，比不提示更烦人。
        await q.answer("你不在本局", show_alert=True)
        return
    if data in ("texas_pre_check", "texas_pre_fold"):
        # 2026-09-13 用户要求（提前操作）：**还没轮到我**也能先把「过牌 / 弃牌」按下去，
        # 轮到时自动执行 —— 不用一直盯着屏幕等自己那一手。
        # 规则（用户原话）：预过牌 = 轮到时**没人加注**就过牌、有人加注则改为弃牌；
        #                 预弃牌 = 轮到时一律弃牌。
        # 牌桌是全群共享的一条消息，所以这两个键每个人都看得见；
        # 但「点的是谁」由这里的 uid 判定，弹窗也只有本人可见（见 poker_buttons）。
        if uid == game.current():
            await q.answer("已经轮到你了，直接按上面的按钮就行", show_alert=True); return
        if uid in game.folded or uid in game.all_in:
            await q.answer("你已经弃牌或全下了，不用再设定", show_alert=True); return
        _kind = "check" if data == "texas_pre_check" else "fold"
        if game.pre_action.get(uid) == _kind:
            game.pre_action.pop(uid, None)          # 再点一次 = 取消
            await q.answer("↩️ 已取消提前操作")
        else:
            game.pre_action[uid] = _kind
            _word = "过牌（若轮到你时有人加注，会自动改为弃牌）" if _kind == "check" else "弃牌"
            await q.answer(f"⏭ 已设定：轮到你时自动「{_word}」\n再点一次可取消", show_alert=True)
        return
    if uid != game.current():
        # 不是你的回合：同样只提示、不重绘（同上，2026-09-13 用户报障）。
        # 原先这里会顺手 render_poker_table 把画面拉回正确状态，但代价是
        # 全群画面闪一下 —— 用户明确要求取消。
        await q.answer("⏰ 还没轮到你", show_alert=True)
        return
    action = {"texas_fold":"fold", "texas_check":"check", "texas_call":"call", "texas_allin":"allin"}.get(data); extra = 0
    if data == "texas_raise_half": action, extra = "raise", max(game.min_raise, game.pot // 2)
    elif data == "texas_raise_pot": action, extra = "raise", max(game.min_raise, game.pot)
    elif data.startswith("texas_raise_"):
        try: action, extra = "raise", int(data.rsplit("_", 1)[1])
        except ValueError: await q.answer("无效加注额", show_alert=True); return
    if not action: await q.answer("未知操作", show_alert=True); return
    # 2026-09-13 用户要求（防误触）：全下 / 跟注 要掏积分，必须二次确认。
    # 为什么是「再点一次同一个按钮」而不是另加一个确认键：牌桌是**全群共享**的
    # 一条消息，换成确认键盘会让别人的画面也变成确认界面（炸金花比牌菜单那样
    # 得额外加锁 + 超时）。再点一次只影响点的人，且不留下任何界面状态。
    # 「过牌」（to_call=0，callback 走 texas_check）不花钱，不拦。
    if action in ("allin", "call"):
        _cost = game.chips[uid] if action == "allin" else max(0, game.current_bet - game.round_bets[uid])
        if _cost > 0:
            _pend = game.pending_confirm
            _prev = _pend.get(uid)
            _now = time.time()
            if _prev and _prev[0] == action and (_now - _prev[2]) <= 10:
                _pend.pop(uid, None)      # 确认通过：清掉待确认，继续往下执行
            else:
                _pend[uid] = (action, extra, _now)
                _word = "全下" if action == "allin" else "跟注"
                await q.answer(f"⚠️ 再点一次「{_word} {_cost}」确认（10 秒内有效）", show_alert=True)
                return
    ok, desc = game.action(uid, action, extra)
    if not ok: await q.answer(desc, show_alert=True); return
    await q.answer(desc); await action_notice(cid, context.application, uid, desc)
    if game.phase == "showdown": await settle_poker(game, context.application)
    else: await start_turn_timer(game, context.application)
    return


async def _btn_dice(update, context, q, cid, uid, data):
    _dice_is_straight = hub._dice_is_straight
    _dice_leopard_kind = hub._dice_leopard_kind
    _dice_notify_dropped = hub._dice_notify_dropped
    _dice_resolve_and_continue = hub._dice_resolve_and_continue
    _dice_rule_on = hub._dice_rule_on
    action_notice = hub.action_notice
    active_dice_games = hub.active_dice_games
    dice_min_raise = hub.dice_min_raise
    dice_rules_text = hub.dice_rules_text
    game_chips = hub.game_chips
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    poker_room_of = hub.poker_room_of
    refund_dice = hub.refund_dice
    sget = hub.sget
    show_dice_action = hub.show_dice_action
    start_dice_turn_timer = hub.start_dice_turn_timer
    update_dice_waiting = hub.update_dice_waiting
    game = active_dice_games.get(cid)
    if not game: await q.answer("大话骰游戏已结束", show_alert=True); return
    if data == "dice_refresh":
        game.cancel_timer()
        if game.phase == "waiting": await update_dice_waiting(game, context.application)
        else: await start_dice_turn_timer(game, context.application)
        await q.answer("已刷新界面")
        return
    if data == "dice_end":
        if not is_bot_admin(uid) and uid not in game.players:
            await q.answer("权限不足", show_alert=True); return
        await refund_dice(game, context.application, "🛑 大话骰已终止，底注已退回。")
        await q.answer("本局已终止")
        return
    if game.phase == "waiting":
        if data == "dice_join":
            _room, _ = poker_room_of(cid, uid, exclude_game=game)
            if _room:
                await q.answer(f"你已在 {_room} 房间，请先结束再加入", show_alert=True); return
            if len(game.players) >= sget("DICE_MAX_PLAYERS"):
                await q.answer("房间已满", show_alert=True); return
            if game_chips[cid][uid] < sget("MIN_ENTRY_CHIPS"):
                await q.answer(f"进入大话骰至少需要 {sget('MIN_ENTRY_CHIPS')} 积分", show_alert=True); return
            if game.add(uid):
                await q.answer("已加入"); await update_dice_waiting(game, context.application)
            else: await q.answer("你已在等待房间中。", show_alert=True)
        elif data == "dice_start":
            if uid != game.owner_id:
                await q.answer("只有发起人可开始游戏（满 2 人会倒计时自动开始）", show_alert=True); return
            if len(game.players) < 2:
                await q.answer("至少要 2 人才能开始", show_alert=True); return
            if game.start():
                game.cancel_wait()
                await _dice_notify_dropped(game, context.application)
                await q.answer("游戏开始")
                await start_dice_turn_timer(game, context.application)
            else:
                await q.answer("开局失败：至少 2 人且余额够底注", show_alert=True)
        else: await q.answer("无法执行此操作", show_alert=True)
        return
    if data == "dice_see":
        if uid not in game.hands:
            await q.answer("还没开局，没有骰子", show_alert=True); return
        if uid in game.out:
            await q.answer("你已出局，没有骰子了", show_alert=True); return
        ds = "  ".join(map(str, game.hands[uid]))
        # 顺子/豹子要当场告诉玩家，否则他会照着自己骰子叫，开骰必翻车（压成短提示）
        note = ""
        _dn, _dw = len(game.players), sget("DICE_WILD_ONE")
        if _dice_rule_on("DICE_STRAIGHT_ZERO", _dn) and _dice_is_straight(game.hands[uid], _dw):
            note = "（顺子·算0个，别照它叫）"
        elif _dice_rule_on("DICE_LEOPARD_BONUS", _dn):
            _lk = _dice_leopard_kind(game.hands[uid], _dw)
            if _lk: note = f"（{_lk}·+{2 if _lk == '纯豹' else 1}）"
        bid_txt = f"{game.bid[0]}个{game.bid[1]}" if game.bid else "你先叫"
        # 2026-09-11 用户要求：直接弹窗，不走私聊（弹窗只有点击者本人可见，不泄露骰子）
        # 2026-09-12 用户报「弹窗一大堆文字、骰子数字不明显」→ 骰子用括号置顶放大，
        #   规则压成一行丢最后；当前叫牌必须保留（玩家得知道叫到哪了）。
        await q.answer(f"🎲 你的骰子\n【 {ds} 】{note}\n"
                       f"🎙 当前叫牌：{bid_txt}\n{dice_rules_text(game)}",
                       show_alert=True)
        return
    if data == "dice_raise":
        if uid != game.actor or game.phase != "playing":
            await q.answer("还没轮到你", show_alert=True); return
        mr = dice_min_raise(game)
        if not mr:
            await q.answer("没有更大的叫法了，只能开骰", show_alert=True); return
        ok, desc = game.action(uid, "bid", mr)
        if not ok: await q.answer(desc, show_alert=True); return
        await q.answer(f"已叫 {mr[0]}个{mr[1]}")
        game.last_action = f"{await get_name(context.application, uid)} 加码叫 {mr[0]}个{mr[1]}"
        # 当前叫牌单独发一条（跟德州 action_notice 同款），不再堆在牌桌上
        await action_notice(cid, context.application, uid, f"加码叫 {mr[0]}个{mr[1]}")
        await start_dice_turn_timer(game, context.application)
        return
    if data == "dice_open":
        if uid != game.actor or game.phase != "playing":
            await q.answer("还没轮到你", show_alert=True); return
        ok, desc = game.action(uid, "open")
        if not ok: await q.answer(desc, show_alert=True); return
        await q.answer("开骰！")
        game.last_action = f"{await get_name(context.application, uid)} 开骰"
        await _dice_resolve_and_continue(game, context.application, uid)
        return
    # --- 跳开（2026-09-13 用户要求）：还没轮到我，也能指定开某个人 ---
    if data == "dice_skip":
        # 牌桌是全群共享的一条消息，所以这个键每个人都看得见；
        # 但接下来弹出的「选人菜单」用 skip_menu_owner 上锁，只有发起者点得动。
        if game.phase != "playing":
            await q.answer("当前不在叫牌阶段", show_alert=True); return
        if len(game.players) < 3:
            await q.answer("两人局直接用「🎯 开骰」就行", show_alert=True); return
        if uid not in game.players or uid in game.out:
            await q.answer("你不在本局", show_alert=True); return
        if not [t for t in game.players if t != uid and t not in game.out and t in game.bids]:
            await q.answer("本手还没人叫过牌，没人可跳开", show_alert=True); return
        game.cancel_timer()                       # 选人期间暂停超时（同炸金花比牌菜单）
        game.skip_menu_owner = uid
        await show_dice_action(game, context.application)
        await q.answer("选择要跳开的人")
        return
    if data == "dice_skip_cancel":
        game.skip_menu_owner = None
        await start_dice_turn_timer(game, context.application)
        await q.answer("已取消跳开")
        return
    if data.startswith("dice_skip_"):
        if game.skip_menu_owner is None or uid != game.skip_menu_owner:
            await q.answer("不是你发起的跳开", show_alert=True); return
        try: target = int(data.rsplit("_", 1)[1])
        except ValueError:
            await q.answer("无效的跳开对象", show_alert=True); return
        game.skip_menu_owner = None
        ok, desc = game.skip_open(uid, target)
        if not ok:
            await q.answer(desc, show_alert=True)
            await start_dice_turn_timer(game, context.application)
            return
        await q.answer("跳开！")
        game.last_action = f"{await get_name(context.application, uid)} 跳开"
        await _dice_resolve_and_continue(game, context.application, uid, target)
        return
    await q.answer("未知操作", show_alert=True)
    return


async def _btn_jinhua(update, context, q, cid, uid, data):
    _refresh_jinhua_table = hub._refresh_jinhua_table
    active_jinhua_games = hub.active_jinhua_games
    card_str = hub.card_str
    game_chips = hub.game_chips
    get_name = hub.get_name
    is_bot_admin = hub.is_bot_admin
    poker_room_of = hub.poker_room_of
    refund_jinhua = hub.refund_jinhua
    safe_send = hub.safe_send
    schedule_notice_delete = hub.schedule_notice_delete
    settle_jinhua = hub.settle_jinhua
    sget = hub.sget
    show_jinhua_action = hub.show_jinhua_action
    start_jinhua_turn_timer = hub.start_jinhua_turn_timer
    update_jinhua_table = hub.update_jinhua_table
    update_jinhua_waiting = hub.update_jinhua_waiting
    game = active_jinhua_games.get(cid)
    if not game: await q.answer("炸金花游戏已结束", show_alert=True); return
    if data == "jh_refresh":
        await _refresh_jinhua_table(game, context.application)
        await q.answer("已刷新界面")
        return
    if data == "jh_see":
        if uid not in game.hands or uid in game.folded:
            await q.answer("当前无法看牌", show_alert=True); return
        first = uid not in game.seen
        if first:
            game.seen.add(uid)  # 首次看牌：标记 seen，之后下注翻倍
        cards = "  ".join(card_str(c) for c in game.hands[uid])
        await q.answer(f"你的手牌：{cards}｜{game._hand_name(uid)}", show_alert=True)
        if first:
            # 看牌是公开信息（闷牌→已看牌），写入状态行并**原地编辑**同一牌桌消息。
            # 2026-09-11 用户报障：原来看牌走「删旧发新」，群里立刻刷「X 删除了消息」，
            # 玩家根本不知道发生了什么 → 看牌改为 edit_only（只编辑、不删），
            # 轮到下一个人操作时才删旧发新。
            game.last_action = f"{await get_name(context.application, uid)} 看了牌"
            await show_jinhua_action(game, context.application, edit_only=True)
        return
    if data == "jh_compare_menu":
        if uid in game.folded:
            await q.answer("你已弃牌", show_alert=True); return
        if game.phase == "betting" and uid != game.current():
            await q.answer("还没轮到你", show_alert=True); return
        if game.phase not in ("betting", "open_pending"):
            await q.answer("当前无法比牌", show_alert=True); return
        targets = [p for p in game.players if p not in game.folded and p != uid]
        if not targets:
            await q.answer("没有可比对的对象", show_alert=True); return
        game.cancel_timer()  # 选人期间暂停超时，避免被自动弃牌
        game.compare_menu_owner = uid  # 锁：仅发起者能点选对手
        await show_jinhua_action(game, context.application)  # 同一条消息切换为选人菜单
        await q.answer("选择比牌对手"); return
    if data == "jh_cancel_pk":
        game.compare_menu_owner = None
        await start_jinhua_turn_timer(game, context.application)  # 内部含 open_pending 看门狗
        await q.answer("已取消比牌"); return
    if data.startswith("jh_pk_"):
        if game.compare_menu_owner is None or uid != game.compare_menu_owner:
            await q.answer("不是你发起的比牌", show_alert=True); return
        try: target = int(data.rsplit("_", 1)[1])
        except ValueError:
            await q.answer("无效比牌对象", show_alert=True); return
        game.compare_menu_owner = None
        ok, desc = game.action(uid, "compare", target)
        if not ok:
            await q.answer(desc, show_alert=True)
            await start_jinhua_turn_timer(game, context.application)
            return
        cmp = game.last_compare
        challenger, target, winner, penalty = cmp
        cname = await get_name(context.application, challenger)
        tname = await get_name(context.application, target)
        challenger_win = (winner == "challenger")
        lname = tname if challenger_win else cname
        ann = f"⚔️ {cname} 比牌 {tname}：{cname if challenger_win else tname} 胜，{lname} 出局"
        if penalty:
            ann += f"（看牌者向闷牌者比牌落败，倒赔 {penalty}）"
        # schedule_notice_delete 是同步函数，不能 await（2026-09-13 修，见 features/texas 同款注释）
        schedule_notice_delete(context.application, cid,
                               await safe_send(context.bot, cid, ann))
        # 仅比牌双方私聊亮牌，旁观者看不到牌面
        ccards = "  ".join(card_str(c) for c in game.hands[challenger])
        tcards = "  ".join(card_str(c) for c in game.hands[target])
        pm = (f"⚔️ 比牌结果\n你：{ccards}（{game._hand_name(challenger)}）\n"
              f"对方：{tcards}（{game._hand_name(target)}）\n"
              f"结果：{'你胜' if challenger_win else '你负'}")
        await safe_send(context.bot, challenger, pm)
        await safe_send(context.bot, target, pm)
        await q.answer(desc)
        if game.phase == "showdown":
            await settle_jinhua(game, context.application)
        else:
            await start_jinhua_turn_timer(game, context.application)
        return
    if data == "jh_end":
        if not is_bot_admin(uid) and uid not in game.players:
            await q.answer("权限不足", show_alert=True); return
        await refund_jinhua(game, context.application, "🛑 炸金花已终止，积分已退回。")
        await q.answer("本局已终止")
        return
    if game.phase == "waiting":
        if data == "jh_join":
            room_name, _ = poker_room_of(cid, uid, exclude_game=game)
            if room_name:
                await q.answer(f"你已在 {room_name} 房间，请先结束再加入", show_alert=True); return
            if game_chips[cid][uid] < sget("MIN_ENTRY_CHIPS"):
                await q.answer(f"进入炸金花至少需要 {sget('MIN_ENTRY_CHIPS')} 积分", show_alert=True); return
            if game.add(uid):
                await q.answer("已加入"); await update_jinhua_waiting(game, context.application)
            else: await q.answer("你已在等待房间中。", show_alert=True)
        elif data == "jh_start" and uid == game.owner_id and game.start():
            game.cancel_wait()
            await q.answer("游戏开始"); await update_jinhua_table(game, context.application); await start_jinhua_turn_timer(game, context.application)
        else: await q.answer("无法执行此操作", show_alert=True)
        return
    # 跟平阶段：开牌 / 继续加注（所有存活玩家可操作）
    if game.phase == "open_pending":
        if uid in game.folded:
            await q.answer("你已弃牌", show_alert=True); return
        if data == "jh_open":
            ok, desc = game.action(uid, "open")
            await q.answer(desc); await settle_jinhua(game, context.application)
        elif data.startswith("jh_raise_"):
            try: extra = int(data.rsplit("_", 1)[1])
            except ValueError: await q.answer("无效加注额", show_alert=True); return
            ok, desc = game.action(uid, "raise", extra)
            if not ok: await q.answer(desc, show_alert=True); return
            await q.answer(desc)
            game.last_action = f"{await get_name(context.application, uid)} {desc}"
            await start_jinhua_turn_timer(game, context.application)  # open_pending 后挂超时看门狗
        else:
            await q.answer("未知操作", show_alert=True)
        return
    # 下注阶段：当前玩家操作；跟平阶段（open_pending）允许任意存活玩家弃牌止损
    if data == "jh_fold" and game.phase == "open_pending":
        if uid in game.folded:
            await q.answer("你已弃牌", show_alert=True); return
        ok, desc = game.action(uid, "fold")
        if not ok: await q.answer(desc, show_alert=True); return
        await q.answer(desc)
        game.last_action = f"{await get_name(context.application, uid)} 弃牌"
        if game.phase == "showdown": await settle_jinhua(game, context.application)
        else: await start_jinhua_turn_timer(game, context.application)  # 弃牌后仍在 open_pending 则挂看门狗
        return
    if uid != game.current(): await q.answer("还没轮到你", show_alert=True); return
    action = {"jh_fold": "fold", "jh_call": "call", "jh_allin": "allin"}.get(data); extra = 0
    if data.startswith("jh_raise_"):
        try: action, extra = "raise", int(data.rsplit("_", 1)[1])
        except ValueError: await q.answer("无效加注额", show_alert=True); return
    if not action: await q.answer("未知操作", show_alert=True); return
    ok, desc = game.action(uid, action, extra)
    if not ok: await q.answer(desc, show_alert=True); return
    await q.answer(desc)
    game.last_action = f"{await get_name(context.application, uid)} {desc}"
    if game.phase == "showdown": await settle_jinhua(game, context.application)
    elif game.phase == "open_pending": await start_jinhua_turn_timer(game, context.application)  # 挂超时看门狗
    else: await start_jinhua_turn_timer(game, context.application)
    return


async def _btn_horse_bet(update, context, q, cid, uid, data):
    action_notice = hub.action_notice
    active_horse_races = hub.active_horse_races
    get_name = hub.get_name
    safe_edit = hub.safe_edit
    sget = hub.sget
    race = active_horse_races.get(cid)
    try: _, horse, amount = data.split("_"); horse, amount = int(horse), int(amount)
    except ValueError: await q.answer("无效下注数据", show_alert=True); return
    if not race: await q.answer("赛车已结束", show_alert=True); return
    ok, desc = await race.bet(uid, horse, amount)
    if not ok: await q.answer(desc, show_alert=True); return
    race.name_cache[uid] = await get_name(context.application, uid); await q.answer(desc); await action_notice(cid, context.application, uid, f"下注 {amount} 于 {sget('HORSE_EMOJI')[horse]}")
    await safe_edit(context.bot, cid, race.game_msg_id, await race.view(context.application), reply_markup=race.buttons())
    return
