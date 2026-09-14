# -*- coding: utf-8 -*-
"""core.cards —— 牌型/底池的**纯计算**（从 bot.py 抽出）。

抽出理由：这些函数是德州与炸金花的判定核心，**不碰任何全局状态**，
只依赖 `Card` 的编解码。它们决定谁赢钱，却最容易在"顺手重构"时被改坏
（A23 是最小顺子 / 235 只专杀豹子 / 平局先比者输），
独立成文件后可以单独重写、单独测，不会牵动 bot.py 里的任何东西。

关于 `Card`：走 `hub.Card` 而不是 `from treys import Card`。
因为测试里有 `m.Card = fake` 这种补丁，直接 import 会让补丁静默失效。
"""
from collections import defaultdict

from core import hub

__all__ = [
    "side_pots",
    "distribute_side_pots",
    "evaluate_jinhua",
    "is_235",
    "jinhua_winners",
    "compare_jinhua_pair",
    "card_str",
]


def card_str(card):
    raw = hub.Card.int_to_pretty_str(card).strip("[]")
    suit = {"♠": "♠️", "♥": "♥️", "♦": "♦️", "♣": "♣️"}.get(raw[-1], raw[-1])
    return f"{suit}{raw[:-1].replace('T', '10')}"


def side_pots(total_bets):
    # 必须按投入金额升序构造主池和边池；按用户 ID 排序会跳过部分底池。
    ordered = sorted(
        ((uid, value) for uid, value in total_bets.items() if value > 0),
        key=lambda item: item[1],
    )
    result, previous = [], 0
    for _, level in ordered:
        if level <= previous: continue
        contributors = [uid for uid, amount in ordered if amount >= level]
        result.append(((level - previous) * len(contributors), contributors)); previous = level
    return result


def distribute_side_pots(total_bets, scores):
    payouts = defaultdict(lambda: {"amount": 0, "details": []})
    total_pot = sum(total_bets.values())
    for index, (amount, contributors) in enumerate(side_pots(total_bets)):
        eligible = {uid: scores[uid] for uid in contributors if uid in scores}
        if not eligible:
            # 根因：该层所有贡献者都已弃牌，原逻辑直接 skip，余额靠全局兜底掩盖分配。
            # 修正：弃牌者的投入仍属底池，按扑克规则归入仍存活的最佳牌型（低分=好牌）。
            best_score = min(scores.values())
            winners = sorted(uid for uid, score in scores.items() if score == best_score)
            share, remainder = divmod(amount, len(winners))
            for position, uid in enumerate(winners):
                won = share + (1 if position < remainder else 0)
                payouts[uid]["amount"] += won
                payouts[uid]["details"].append(("主池" if index == 0 else f"边池{index}", won))
            continue
        best = min(eligible.values()); winners = sorted(uid for uid, score in eligible.items() if score == best)
        share, remainder = divmod(amount, len(winners))
        for position, uid in enumerate(winners):
            won = share + (1 if position < remainder else 0)
            payouts[uid]["amount"] += won
            payouts[uid]["details"].append(("主池" if index == 0 else f"边池{index}", won))
    # 守恒兜底：任何因异常边池资格导致的剩余底池，归入当前最佳存活玩家，禁止积分凭空消失。
    allocated = sum(item["amount"] for item in payouts.values())
    unallocated = total_pot - allocated
    if unallocated > 0 and scores:
        best_score = min(scores.values())
        winners = sorted(uid for uid, score in scores.items() if score == best_score)
        share, remainder = divmod(unallocated, len(winners))
        for position, uid in enumerate(winners):
            won = share + (1 if position < remainder else 0)
            payouts[uid]["amount"] += won
            payouts[uid]["details"].append(("底池兜底", won))
    return payouts


def evaluate_jinhua(cards):
    """炸金花 3 张牌牌型：返回 (等级, 比较键)。等级 5豹子 > 4同花顺 > 3金花 > 2顺子 > 1对子 > 0散牌。
    rank_int: 2=0...A=12；A23 是最小顺子（rank 12,0,1）。"""
    Card = hub.Card
    ranks = sorted([Card.get_rank_int(c) for c in cards], reverse=True)
    suits = [Card.get_suit_int(c) for c in cards]
    is_flush = len(set(suits)) == 1
    is_straight = (ranks[0] == ranks[1] + 1 == ranks[2] + 2) or (ranks == [12, 1, 0])
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    if len(counts) == 1:
        return (5, (ranks[0],))
    if is_straight and is_flush:
        return (4, (1 if ranks == [12, 1, 0] else ranks[0],))
    if is_flush:
        return (3, tuple(ranks))
    if is_straight:
        return (2, (1 if ranks == [12, 1, 0] else ranks[0],))
    if len(counts) == 2:
        pair_rank = [r for r, c in counts.items() if c == 2][0]
        kicker = [r for r, c in counts.items() if c == 1][0]
        return (1, (pair_rank, kicker))
    return (0, tuple(ranks))


def is_235(cards):
    """不同花的 2、3、5（散牌最小，但专杀豹子）。rank: 2=0,3=1,5=3"""
    Card = hub.Card
    ranks = sorted([Card.get_rank_int(c) for c in cards])
    suits = [Card.get_suit_int(c) for c in cards]
    return ranks == [0, 1, 3] and len(set(suits)) == 3


def jinhua_winners(hands_map):
    """炸金花比牌：返回赢家 uid 列表。235 专杀豹子。"""
    alive = list(hands_map.keys())
    has_235 = [uid for uid in alive if is_235(hands_map[uid])]
    has_triple = [uid for uid in alive if evaluate_jinhua(hands_map[uid])[0] == 5]
    if has_235 and has_triple:
        return sorted(has_235)  # 235 专杀豹子
    best = max(evaluate_jinhua(hands_map[uid]) for uid in alive)
    return sorted(uid for uid in alive if evaluate_jinhua(hands_map[uid]) == best)


def compare_jinhua_pair(challenger, target, hands):
    """炸金花官方比牌（点对点）：返回 'challenger' 或 'target'。
    235 专杀豹子；其余按标准牌型比较。平局（含双方都235/都豹子）→ 先比者(挑战方)输，返回 'target'。"""
    hc, ht = hands[challenger], hands[target]
    c235, t235 = is_235(hc), is_235(ht)
    ctriple = evaluate_jinhua(hc)[0] == 5
    ttriple = evaluate_jinhua(ht)[0] == 5
    if c235 and ttriple: return "challenger"   # 235 杀豹子
    if t235 and ctriple: return "target"        # 豹子被235杀，挑战方胜
    if evaluate_jinhua(hc) > evaluate_jinhua(ht): return "challenger"
    return "target"  # 挑战方牌小或平局 → 先比者输
