# -*- coding: utf-8 -*-
"""成员 / 消息类**域助手** —— **归属存疑**，从 core/members.py 挪到中性文件（2026-09-14）。

为什么只挪到中性文件、**不跨包搬迁**：每个函数都**只被一个 feature 域**使用，
逻辑上属于那边，但跨包搬进别人的域等于替用户改别人的域（SKILL §5.6 明令不许顺手做）。
所以本轮只做「从权限核心里挪走」这一步，逐条注明本该属于哪，等用户拍板：

  · `_chat_is_effective`         → 本该去 features/points（聊天积分：有效发言判定）
  · `_peer_brief`                → 本该去 features/points（积分流水行的对家名）
  · `_msg_kind`                  → 本该去 features/antispam（等级消息管控）
  · `_is_service_message`        → 本该去 features/antispam（系统消息判定）
  · `_join_user_obj`             → 本该去 features/antispam / invite（成员事件取 user）
  · `_user_has_avatar`           → 本该去 features/invite（邀请质量门槛）
  · `_cleanup_left_member_games` → 本该去 features/antispam（退群清理，碰游戏等待房）
  · `_nm_r`                      → UI 文本工具（按钮名压短），与成员/权限无关

⚠️ 搬走它们不是目的，是手段：留在地基文件里会让「权限怎么判的」被 160 行无关代码稀释。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
"""

from core import hub

import re, time


def _peer_brief(name):
    """流水括号里的对家名：截到能认出人的长度。

    2026-09-12 用户报「流水里 +50（德州·投喂 无敌棒棒屌爆）太复杂」——**对局行直接不写对家**
    （见 cmd_points_flow），本函数只管**人对人转移**（转赠/红包），那是唯一需要「谁给的」的信息。
    昵称可能极长（群友爱堆 emoji/长句），不截断会把一行撑成两行、时间戳被挤下去。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    FLOW_PEER_MAX = hub.FLOW_PEER_MAX
    s = str(name or "").strip()
    if len(s) <= FLOW_PEER_MAX:
        return s
    return s[:FLOW_PEER_MAX - 1] + "…"


def _is_service_message(message):
    """判断是否为系统消息（入退群/改名/换头像/删头像/置顶/建群/迁移等）。
    系统消息的 effective_user 经常为 None（建群/迁移尤其），单独判定便于豁免 user 检查。
    用 getattr 兜底空值，兼容测试 mock（生产 PTB Message 这些字段全有）。"""
    if message is None:
        return False
    for attr in ("new_chat_members", "left_chat_member", "new_chat_title",
                 "new_chat_photo", "delete_chat_photo", "pinned_message",
                 "group_chat_created", "supergroup_chat_created",
                 "migrate_to_chat_id", "migrate_from_chat_id"):
        if getattr(message, attr, None):
            return True
    return False


def _msg_kind(message, text=""):
    """判定一条消息的类型（用于等级权限校验）。返回 LEVEL_PERM_OPTIONS 的键。

    优先级：转发 > 贴纸 > 图片 > 视频 > 音频 > 链接 > 编辑 > 文字。
    转发优先于内容类型：用户要的是「能不能转发」这一维度的管控。
    """
    if message is None:
        return "text"
    if getattr(message, "forward_origin", None) or getattr(message, "forward_from", None) \
            or getattr(message, "forward_from_chat", None) or getattr(message, "forward_sender_name", None):
        return "forward"
    if getattr(message, "sticker", None) is not None:
        return "sticker"
    if getattr(message, "photo", None) is not None:
        return "photo"
    if getattr(message, "video", None) is not None or getattr(message, "video_note", None) is not None:
        return "video"
    if getattr(message, "audio", None) is not None or getattr(message, "voice", None) is not None:
        return "audio"
    t = str(text or "")
    if ("http://" in t or "https://" in t or "t.me/" in t
            or any(getattr(e, "type", None) in ("url", "text_link") for e in (getattr(message, "entities", None) or []))):
        return "link"
    if getattr(message, "edit_date", None):
        return "edit"
    return "text"


def _chat_is_effective(cid, uid, text, min_len=None):
    """有效发言判定（2026-09-09 用户规则）：正常话题/有内容的讨论才算有效。

    三条否决（任一命中即不计分）：
      ① 过短：去掉空白后长度 < min_len（仅「每N字符」旧规则模式检查；
              规则表模式由用户自己配条件，不额外卡长度）
      ② 纯符号/表情：把标点、emoji、空白全部剔除后什么都不剩
              （如「😂😂😂」「。。。」「!!!!」「👍👍。。。」）→ 无信息量，不计分
      ③ 无意义：整条消息只由黑名单词/标点/表情构成（如「哈哈」「哦哦」「收到」）
      ④ 灌水：窗口内同一内容已发 CHAT_DUP_N 条（复读机）
    纯表情包/图片本身走 on_media，不经过本函数（天然不计分）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _antispam_norm = hub._antispam_norm
    chat_dup_hist = hub.chat_dup_hist
    sget = hub.sget
    t = str(text or "").strip()
    if not t:
        return False
    if min_len is not None and len(re.sub(r"\s+", "", t)) < max(1, int(min_len)):
        return False
    # ② 纯符号/表情：剔除标点、符号、emoji、空白后若无任何中文/字母/数字 → 无信息量
    #    无条件生效（规则表模式也不能给纯表情/纯符号送分）
    if not re.sub(r"[\s\W_]+", "", t, flags=re.UNICODE):
        return False
    # ③ 无意义词：把黑名单词与常见标点/空白全部剔除后，若什么都不剩 → 纯无意义
    if sget("CHAT_JUNK_WORDS"):
        _r = t
        for w in sget("CHAT_JUNK_WORDS"):
            w = str(w).strip()
            if w:
                _r = _r.replace(w, "")
        _r = re.sub(r"[\s\W_]+", "", _r, flags=re.UNICODE)
        if not _r:
            return False
    # ④ 重复灌水：本函数独立记录（不复用 antispam_hist——那个受 ANTISPAM_ENABLED 开关
    #    与 ANTISPAM_MIN_LEN 长度门槛控制，关掉刷屏识别后重复检测会静默失效）
    _n = int(sget("CHAT_DUP_N"))
    if _n > 0:
        _key = (cid, uid, _antispam_norm(t))
        _now = time.time()
        _win = max(10, int(sget("CHAT_DUP_WINDOW")))
        _hist = [x for x in (chat_dup_hist.get(_key) or []) if _now - x <= _win]
        _dup = len(_hist) >= _n          # 先判定：本条之前的条数已达阈值 → 本条不计分
        _hist.append(_now)
        chat_dup_hist[_key] = _hist[-20:]
        # ★ 键是 (cid, uid, 归一化文本)，每条不同的发言内容都会新建一个键；
        #   原来只更新值、从不删键 → 长期运行会攒到几十万个键（内存只涨不降）。
        #   键数超过阈值时顺手扫一遍，把窗口外（最后一条已过期）的键删掉。
        if len(chat_dup_hist) > 20000:
            for _k in [k for k, v in chat_dup_hist.items()
                       if not v or (_now - v[-1]) > _win]:
                chat_dup_hist.pop(_k, None)
        if _dup:
            return False
    return True


def _join_user_obj(cmu):
    """从 chat_member 事件或服务消息中取被邀请人 user 对象。"""
    u = getattr(getattr(cmu, "new_chat_member", None), "user", None)
    if u is None:
        mlist = getattr(cmu, "new_chat_members", None)
        if mlist:
            u = mlist[0]
    return u


async def _user_has_avatar(context, uid):
    """查询用户是否有头像（邀请质量门槛；网络异常视为有头像放行，防误伤正常进群）。"""
    try:
        photos = await context.bot.get_user_profile_photos(uid, limit=1)
        return bool(getattr(photos, "total_count", 0))
    except Exception:
        return True


async def _cleanup_left_member_games(app, cid, uid):
    """⑭ 退群清理：把已退群成员从等待房移除，防止幽灵玩家被开局带上场。

    - 金花/德州 waiting：纯移除（两游戏入房不扣钱，德州 chips 只是只读快照）
    - 21点 waiting：入房时已预扣积分，移除必须原额退款并清 pending（防重启重复退）
    - 进行中对局（betting/playing/open_pending）不动内部结构——各游戏已有回合超时
      （德州/21点超时自动弃牌停牌、金花 open_pending 看门狗自动开牌），不卡局不丢钱。
    全程吞异常：退群清理绝不能拖垮成员事件处理。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    active_blackjack_games = hub.active_blackjack_games
    active_jinhua_games = hub.active_jinhua_games
    active_poker_games = hub.active_poker_games
    game_chips = hub.game_chips
    logger = hub.logger
    pending_game_bets = hub.pending_game_bets
    update_blackjack_ui = hub.update_blackjack_ui
    update_jinhua_waiting = hub.update_jinhua_waiting
    update_poker_waiting = hub.update_poker_waiting
    wallet_locks = hub.wallet_locks
    try:
        g = active_jinhua_games.get(cid)
        if g and g.phase == "waiting" and uid in g.players:
            g.players.remove(uid)
            await update_jinhua_waiting(g, app)
            return
        g = active_poker_games.get(cid)
        if g and g.phase == "waiting" and uid in g.players:
            g.players.remove(uid)
            await update_poker_waiting(g, app)
            return
        g = active_blackjack_games.get(cid)
        if g and g.phase == "waiting" and uid in g.players:
            async with wallet_locks[uid]:
                if uid in g.players:
                    g.players.remove(uid)
                    amt = g.bets.pop(uid, 0)
                    if amt:
                        game_chips[cid][uid] += amt   # 与 bj_end 手动终止同一退款语义
                    pending_game_bets[cid].get(uid, {}).pop("21", None)
            await update_blackjack_ui(g, app)
    except Exception:
        logger.exception("退群清理牌局异常（已吞并）")


def _nm_r(n):
    """按钮名压短：去 HTML 敏感字符并截断（callback 按钮 64 字节限制余量留给 data）。"""
    return str(n or "?").replace("<", "‹").replace(">", "›").replace("&", "＆")[:16]
