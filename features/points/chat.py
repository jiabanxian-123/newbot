# -*- coding: utf-8 -*-
"""② 聊天积分

· 必须先过 `_chat_is_effective` 四条否决（过短 / 纯符号 / 垃圾词 / 重复灌水）；
  规则表模式要传 `min_len=None` —— 用户自配的条件优先，额外卡长度会误杀。
· 重复灌水用独立的 `chat_dup_hist`，**不复用 `antispam_hist`**
  （那个受 `ANTISPAM_*` 控制，关掉刷屏识别会静默失效）。
· 按「人·天」聚合进 `chat_earn_daily`，不逐条进 ledger（否则几天就冲掉真正的资金流水）。
"""

from core import hub


def _award_chat_points(cid, uid, text):
    """聊天积分：优先走网页配置的规则表（阿福式：文字/长度条件 → 分值，命中即停）；
    规则表为空或全停时回退旧逻辑（每 N 字符记 X 分）。均受每日上限约束。

    2026-09-09：加「有效发言」前置判定——无意义词/重复灌水一律不计分；
    长度门槛只在旧规则模式生效（规则表模式由用户配置的条件决定，不额外卡长度）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _chat_is_effective = hub._chat_is_effective
    _earn_add = hub._earn_add
    chat_earn_daily = hub.chat_earn_daily
    chat_rules = hub.chat_rules
    game_chips = hub.game_chips
    now_bj = hub.now_bj
    sget = hub.sget
    if not sget("CHAT_ENABLED"):
        return
    t = text.strip()
    enabled = [r for r in chat_rules if r.get("on")]
    if not _chat_is_effective(cid, uid, t, min_len=None if enabled else sget("CHAT_MIN_LEN")):
        return
    if enabled:
        gain = 0
        for r in enabled:
            m = str(r.get("match", "")).strip()
            if not m or m in t:                      # 空 match=任意消息兜底；其余=包含即命中
                gain = int(r.get("points", 0) or 0)
                break
            if m.startswith("len>="):
                try:
                    if len(t) >= int(m[5:]):
                        gain = int(r.get("points", 0) or 0)
                        break
                except ValueError:
                    pass
        else:
            return                                   # 有启用规则但一条都没命中 → 不加分
    else:
        if sget("CHAT_REWARD") <= 0 or sget("CHAT_CHARS_PER") <= 0:
            return
        n = len(t)
        if n < sget("CHAT_CHARS_PER"):
            return
        gain = (n // sget("CHAT_CHARS_PER")) * sget("CHAT_REWARD")
    # 单条消息上限（2026-09-14 用户选 B）：0 = 不限（旧行为）。
    # 累进模式与规则表模式**都**受它约束 —— 它管的是「一条消息最多给多少」，
    # 与「每日上限 CHAT_DAILY_CAP」是两把不同的尺子，各自独立生效。
    _cap = int(sget("CHAT_MAX_PER_MSG") or 0)
    if _cap > 0:
        gain = min(gain, _cap)
    if gain <= 0:
        return
    date = now_bj().strftime("%Y-%m-%d")
    # 唯一账本（2026-09-12 合并，见声明处注释）：当日已得分既用于日上限判定，
    # 也直接供「积分流水」按人·天展示 —— 不必再多维护一份拷贝。
    daily = chat_earn_daily[date][cid]
    earned = daily.get(uid, 0)
    if sget("CHAT_DAILY_CAP") > 0:
        gain = min(gain, sget("CHAT_DAILY_CAP") - earned)
        if gain <= 0:
            return
    daily[uid] = earned + gain
    game_chips[cid][uid] += gain
    _earn_add(cid, uid, gain)   # 聊天积分计入累计积分（等级口径），但不发升级通知（高频防刷屏）
