# -*- coding: utf-8 -*-
"""文本交互 · 下注/游戏文本（2026-09-13 从 core/entry.py 的 on_text 尾部原样搬出）。

抽缝铁律：只允许 `from core import hub`；函数体里用前导别名 `名 = hub.名` 访问 bot 全局量。
"""
import re

from core import hub


async def text_game_bets(update, context, cid, user, text, message):
    """下注 / 游戏文本交互：21点文字加入、赛车下注、扑克加注/全下、大话骰叫牌开骰。

    2026-09-13 从 on_text 尾部**逐字**搬出（只去缩进，逻辑一字未改）。
    """
    _dice_del_bid_msg = hub._dice_del_bid_msg
    _dice_resolve_and_continue = hub._dice_resolve_and_continue
    action_notice = hub.action_notice
    active_blackjack_games = hub.active_blackjack_games
    active_dice_games = hub.active_dice_games
    active_horse_races = hub.active_horse_races
    active_jinhua_games = hub.active_jinhua_games
    active_poker_games = hub.active_poker_games
    game_chips = hub.game_chips
    get_name = hub.get_name
    parse_dice_bid = hub.parse_dice_bid
    safe_edit = hub.safe_edit
    send_reply = hub.send_reply
    settle_jinhua = hub.settle_jinhua
    settle_poker = hub.settle_poker
    sget = hub.sget
    start_dice_turn_timer = hub.start_dice_turn_timer
    start_jinhua_turn_timer = hub.start_jinhua_turn_timer
    start_turn_timer = hub.start_turn_timer
    update_blackjack_ui = hub.update_blackjack_ui
    update_jinhua_table = hub.update_jinhua_table
    update_poker_table = hub.update_poker_table
    wallet_locks = hub.wallet_locks
    # 21点文字加入
    blackjack = active_blackjack_games.get(cid)
    bj_match = re.fullmatch(r"(?:下注|下|押|买)?(?:21点|21)\s*(\d+)", text)
    if bj_match and blackjack:
        if blackjack.phase != "waiting":
            await send_reply(update, context, "❌ 21点已经开始，请等待下一局。"); return
        amount = int(bj_match.group(1))
        if amount < sget("BJ_MIN_BET"):
            await send_reply(update, context, f"❌ 21点最低下注 {sget('BJ_MIN_BET')} 积分。"); return
        wallet = game_chips
        async with wallet_locks[user.id]:
            if wallet[cid][user.id] < amount:
                await send_reply(update, context, f"❌ 积分不足，你只有 {wallet[cid][user.id]}。"); return
            if blackjack.add_player(user.id, amount):
                wallet[cid][user.id] -= amount
            else:
                await send_reply(update, context, "❌ 你已在局中或无法加入。"); return
        await action_notice(cid, context.application, user.id, f"加入了 21点，下注 {amount}")
        await update_blackjack_ui(blackjack, context.application)
        return

    # 赛车与德州传统匹配
    match = re.fullmatch(r"下注\s+(\d+)\s+(\d+)", text); race = active_horse_races.get(cid)
    if match and race:
        horse, amount = int(match.group(1))-1, int(match.group(2))
        ok, desc = await race.bet(user.id, horse, amount)
        if not ok: await send_reply(update, context, f"❌ {desc}"); return
        race.name_cache[user.id] = await get_name(context.application, user.id)
        await action_notice(cid, context.application, user.id, f"下注 {amount} 于 {sget('HORSE_EMOJI')[horse]}")
        await safe_edit(context.bot, cid, race.game_msg_id, await race.view(context.application), reply_markup=race.buttons())
        return

    # 扑克类游戏文字加注（自动路由到玩家当前轮到的游戏：德州→炸金花）
    bet_match = re.fullmatch(r"(?:继续)?(?:下注|加注)\s*[:：]?\s*(\d+)\s*(?:积分)?", text)
    if bet_match:
        amount = int(bet_match.group(1))
        for game, settle, update, start_timer in [
            (active_poker_games.get(cid), settle_poker, update_poker_table, start_turn_timer),
            (active_jinhua_games.get(cid), settle_jinhua, update_jinhua_table, start_jinhua_turn_timer),
        ]:
            if not game:
                continue
            # 炸金花跟平阶段(open_pending)：所有存活玩家(未弃)均可加注，此时 current() 返回 None
            if game is active_jinhua_games.get(cid) and game.phase == "open_pending":
                if user.id not in game.players or user.id in game.folded:
                    continue
            elif not (game.phase != "waiting" and user.id == game.current()):
                continue
            ok, desc = game.action(user.id, "raise", amount)
            if not ok: await send_reply(update, context, f"❌ {desc}"); return
            await action_notice(cid, context.application, user.id, desc)
            if game.phase == "showdown": await settle(game, context.application)
            # 只调 start_timer：它内部已渲染牌桌（删旧发新）。
            # 原来这里先 update() 再 start_timer() = 同一画面连发两条，群里会闪一下。
            else: await start_timer(game, context.application)
            return

    # 扑克类游戏文字全下（自动路由到玩家当前轮到的游戏：德州→炸金花）
    if re.fullmatch(r"全下|all\s*in", text.strip(), re.IGNORECASE):
        for game, settle, update, start_timer in [
            (active_poker_games.get(cid), settle_poker, update_poker_table, start_turn_timer),
            (active_jinhua_games.get(cid), settle_jinhua, update_jinhua_table, start_jinhua_turn_timer),
        ]:
            if not (game and game.phase != "waiting" and user.id == game.current()):
                continue
            ok, desc = game.action(user.id, "allin")
            if not ok: await send_reply(update, context, f"❌ {desc}"); return
            await action_notice(cid, context.application, user.id, desc)
            if game.phase == "showdown": await settle(game, context.application)
            else: await start_timer(game, context.application)   # 同上：内部已渲染
            return

    # 大话骰：群里直接打「6个3」「6 3」「六個三」叫牌（也认「叫6个3」）；
    # 打「开」「开骰」「开牌」「不信」「掀」＝开骰
    #（仅轮到你时消费消息；不是你的回合/不是叫牌则照常走聊天积分等后续逻辑）
    _dg = active_dice_games.get(cid)
    if _dg and _dg.phase == "playing":
        if re.fullmatch(r"开骰?|开牌|开他|开盅|掀盅?|不信|不開|不开", text.replace(" ", "")):
            if user.id == _dg.actor and _dg.bid:
                ok, _d = _dg.action(user.id, "open")
                if ok:
                    _dg.last_action = f"{await get_name(context.application, user.id)} 开骰"
                    await _dice_del_bid_msg(context, cid, message)   # 删掉玩家发的「开骰」文本
                    await _dice_resolve_and_continue(_dg, context.application, user.id)
                    return
            elif user.id in _dg.players and not _dg.bid:
                await send_reply(update, context, "❌ 你是先叫方，起手必须先叫牌（如「3个4」）。")
                return
        else:
            _bid = parse_dice_bid(text)
            if _bid is not None:
                if user.id != _dg.actor:
                    # 只有本局玩家提示「没轮到」，路人发「6 3」这类消息不受打扰
                    if user.id in _dg.players:
                        await send_reply(update, context, "❌ 还没轮到你叫牌。")
                    return
                ok, desc = _dg.action(user.id, "bid", _bid)
                if not ok:
                    await send_reply(update, context, f"❌ {desc}"); return
                _dg.last_action = f"{await get_name(context.application, user.id)} 叫 {_bid[0]}个{_bid[1]}"
                await _dice_del_bid_msg(context, cid, message)   # 删掉玩家发的叫牌文本，保持群聊清爽
                # 当前叫牌单独发一条（跟德州 action_notice 同款），不再堆在牌桌上
                await action_notice(cid, context.application, user.id, f"叫 {_bid[0]}个{_bid[1]}")
                await start_dice_turn_timer(_dg, context.application)
                return
