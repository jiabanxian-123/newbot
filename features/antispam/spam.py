# -*- coding: utf-8 -*-
"""① 刷屏 / 重复消息拦截

两个可抓特征（TG 定时消息发出后与普通消息无差别，固定节奏是它唯一的马脚）：
  · repeat：复读机 —— 窗口内同人同内容达到 ANTISPAM_REPEAT_N 条
  · timer ：定时器 —— 同内容累计 ANTISPAM_TIMER_N 条，且相邻间隔近似相等
命中后按累犯次数递增禁言时长（ANTISPAM_MUTE_ESCALATE）。
"""

from core import hub

from datetime import datetime, timedelta, timezone
from telegram import ChatPermissions
from telegram.error import TelegramError
import re, time


def _antispam_norm(text):
    """内容归一化：去空白 + 小写，同文异构（加空格/大小写变化）视为同一内容。"""
    return re.sub(r"\s+", "", (text or "")).lower()


def _antispam_check(cid, uid, text):
    """记录并判定刷屏特征。返回 'repeat' / 'timer' / None。

    - repeat：复读机——窗口内同人同内容达到 ANTISPAM_REPEAT_N 条
    - timer：定时器——同内容累计 ANTISPAM_TIMER_N 条，且相邻间隔近似相等
      （TG 定时消息发出后与普通消息无差别，固定节奏是其唯一可抓特征）
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _antispam_norm = hub._antispam_norm
    antispam_hist = hub.antispam_hist
    sget = hub.sget
    now = time.time()
    key = (cid, uid, _antispam_norm(text))
    hist = antispam_hist.setdefault(key, [])
    hist.append(now)
    if len(hist) > 12: del hist[:-12]
    recent = [t for t in hist if now - t <= sget("ANTISPAM_WINDOW")]
    if len(recent) >= sget("ANTISPAM_REPEAT_N"):
        return "repeat"
    if len(hist) >= sget("ANTISPAM_TIMER_N"):
        ivs = [b - a for a, b in zip(hist, hist[1:])][-(sget("ANTISPAM_TIMER_N") - 1):]
        mean = sum(ivs) / len(ivs)
        if mean >= 30 and all(abs(iv - mean) <= mean * sget("ANTISPAM_TIMER_TOL") / 100 for iv in ivs):
            return "timer"
    return None


def _antispam_prune(offs, window=None):
    """累犯计数只保留时间窗内的命中（就地清理并返回）。

    不清理的话 antispam_offense 只增不减，禁言时长 = 基础 × 2^(n-1)，
    老用户隔几个月再犯一次就是几十小时，等同永久禁言，内存也只涨不降。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ANTISPAM_OFFENSE_WINDOW = hub.ANTISPAM_OFFENSE_WINDOW
    w = ANTISPAM_OFFENSE_WINDOW if window is None else window
    try: w = float(w)
    except (TypeError, ValueError): return offs
    if w and w > 0:
        cut = time.time() - w
        offs[:] = [t for t in offs if t >= cut]
    return offs


async def _antispam_hit(update, context, cid, uid, reason):
    """命中处理：撤删本条 → 清该用户统计防连环触发 → 禁言（累犯翻倍）→ 群内通告。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _antispam_prune = hub._antispam_prune
    antispam_hist = hub.antispam_hist
    antispam_offense = hub.antispam_offense
    schedule_delete = hub.schedule_delete
    sget = hub.sget
    user_names = hub.user_names
    message = update.effective_message
    try: await message.delete()
    except TelegramError: pass
    for k in [k for k in antispam_hist if k[0] == cid and k[1] == uid]:
        antispam_hist.pop(k, None)
    offs = antispam_offense.setdefault((cid, uid), [])
    offs.append(time.time())
    _antispam_prune(offs)   # 只保留时间窗内的命中，否则累犯计数只增不减 → 2^(n-1) 变事实永久禁言
    n = len(offs)
    mute = sget("ANTISPAM_MUTE_SECONDS") * (2 ** (n - 1)) if sget("ANTISPAM_MUTE_ESCALATE") else sget("ANTISPAM_MUTE_SECONDS")
    muted = False
    if mute > 0:
        try:
            await context.bot.restrict_chat_member(
                cid, uid, permissions=ChatPermissions(can_send_messages=False),
                until_date=datetime.now(timezone.utc) + timedelta(seconds=mute))
            muted = True
        except TelegramError: pass
    name = user_names.get(uid) or str(uid)
    why = "复读刷屏" if reason == "repeat" else "定时器式连发"
    tip = f"🔨 检测到{why}：{name} 的消息已自动撤删"
    if muted:
        mins = max(1, mute // 60)
        tip += f"，禁言 {mins} 分钟" + ("（累犯加倍）" if n > 1 and sget("ANTISPAM_MUTE_ESCALATE") else "")
    elif sget("ANTISPAM_MUTE_SECONDS") > 0:
        tip += "（我需要管理员禁言权限才能禁言）"
    try:
        m = await context.bot.send_message(cid, tip)
        if sget("ANTISPAM_NOTICE_SECONDS") > 0:
            schedule_delete(context.application, cid, m, sget("ANTISPAM_NOTICE_SECONDS"))
    except TelegramError: pass
