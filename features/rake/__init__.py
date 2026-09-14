# -*- coding: utf-8 -*-
"""feature/rake —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

def calc_rake(nets):
    """计算抽水（纯计算不扣款）：对赢家净赢抽成。返回 (总抽水, {uid: 金额})。

    ⚠️ 别给本函数加「按派彩抽」的档位：赛车用的是**另一个口径**（见 `race_rake_split`），
    两种口径的基数不同（净赢 vs 派彩），塞进一个函数只会让两边都看不懂。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    if not sget("RAKE_ENABLED") or sget("RAKE_PERCENT") <= 0:
        return 0, {}
    rake_total, rake_per = 0, {}
    for uid, net in (nets or {}).items():
        if not isinstance(uid, int) or uid <= 0 or net <= 0:
            continue
        if net < sget("RAKE_MIN_NET"):
            continue
        amt = int(net * sget("RAKE_PERCENT") / 100)
        if amt <= 0:
            continue
        rake_per[uid] = amt
        rake_total += amt
    return rake_total, rake_per


async def commit_rake(app, cid, rake_per, label):
    """抽水落账：从钱包扣除 + 写台账。不再单独发群消息——抽水在结算面板里直接体现为「实收」。

    修复（P1④ 记账边界）：此前先 max(0, 余额-抽水) 扣款、却把**应抽金额**写进台账，
    余额不足时（结算与抽水之间有 await，玩家可能已转出/被并发扣款）会出现
    「台账记了 X、钱包只扣了 Y<X」的账实不符。现在按**实际扣到的金额**记账，
    余额不足时少收多少就记多少，台账与钱包永远一致。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    game_chips = hub.game_chips
    ledger_add = hub.ledger_add
    logger = hub.logger
    save_data = hub.save_data
    wallet_locks = hub.wallet_locks
    if not rake_per:
        return 0
    total = 0
    for uid, amt in rake_per.items():
        try:
            amt = int(amt or 0)
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            continue
        async with wallet_locks[uid]:
            bal = int(game_chips[cid].get(uid, 0) or 0)
            real = min(amt, bal) if bal > 0 else 0
            if real <= 0:
                logger.warning("抽水跳过：cid=%s uid=%s 余额 %s 不足以支付抽水 %s（%s）", cid, uid, bal, amt, label)
                continue
            game_chips[cid][uid] = bal - real
        ledger_add(cid, uid, 0, real, f"抽水-{label}")
        total += real
    if total:
        save_data()
    return total
