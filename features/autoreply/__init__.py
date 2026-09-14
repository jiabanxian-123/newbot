# -*- coding: utf-8 -*-
"""关键词自动回复（Rose filters 同款，2026-09-14）

管理员在后台设「怎么玩 → 教程内容」，群友问到就自动答。
赌场群特别实用：怎么上分、怎么提现、规则在哪 —— 让机器人自己答，
管理不用天天当客服。

数据放在**一个 text 型设置项**里，每行一条规则：

    关键词|回复内容
    怎么玩|先 /充值 上分，再 /德州 开局
    提现|找管理，满 1000 起提

为什么不用表格型（像 chat_rules 那样）：加一张新表要动 restore_flow /
snapshot / settings / 页面五处，风险大；而这个场景管理员更想要「一眼看得见、
随手能改」的纯文本，竖线分隔比表格直观。

★ 触发时机白名单：本群**正在玩游戏**时不插嘴 —— 玩家打牌中途问「怎么玩」
  被机器人插一条教程，会被骂（用户明确提过）。
"""

from core import hub


def _parse_keyword_rules(raw):
    """把设置项原文解析成 [(关键词, 回复), ...]。

    容错优先：空行/缺竖线/空关键词一律跳过，绝不让一条坏规则拖垮整个功能。
    """
    rules = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            continue
        kw, _, reply = line.partition("|")
        kw, reply = kw.strip(), reply.strip()
        if kw and reply:
            rules.append((kw, reply))
    return rules


def _keyword_reply_match(text):
    """在已配规则里找第一条命中的（命中即停）。返回回复内容或 None。

    匹配用子串（跟敏感词同口径），且走 `_normalize_for_match` 归一 ——
    群友打「怎么玩？」带问号、或全角字符，照样命中。
    """
    sget = hub.sget
    _normalize_for_match = hub._normalize_for_match
    t = _normalize_for_match(text)
    if not t:
        return None
    for kw, reply in _parse_keyword_rules(sget("KEYWORD_REPLIES")):
        kwn = _normalize_for_match(kw)
        if kwn and kwn in t:
            return reply
    return None


async def _autoreply_enforce(update, context):
    """关键词自动回复：命中就回一条，返回 True=已处理（调用方应 return）。

    ★ 顺序：必须排在**群管过滤链之后**（敏感词命中会被删，不该再回复）、
      抽奖触发词之后（活动优先）。

    ★ 时机白名单：本群有游戏进行中 → 不插嘴（game_mutex_running 复用
      features/texas 那套互斥判定，一处覆盖 5 个游戏）。
    """
    game_mutex_running = hub.game_mutex_running
    is_bot_admin = hub.is_bot_admin
    is_group_chat = hub.is_group_chat
    logger = hub.logger
    send_reply = hub.send_reply
    sget = hub.sget
    _keyword_reply_match = hub._keyword_reply_match
    try:
        if not sget("AUTOREPLY_ENABLED"):
            return False
    except Exception:
        return False
    user, message = update.effective_user, update.effective_message
    if not message or not user or user.is_bot:
        return False
    if not is_group_chat(update):
        return False
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if not text:
        return False
    # 命令不参与（/开头交给命令路由）
    if text.startswith("/"):
        return False
    # ★ 时机白名单：正在打牌就别插嘴
    try:
        if game_mutex_running(update.effective_chat.id):
            return False
    except Exception:
        pass
    try:
        reply = _keyword_reply_match(text)
        if not reply:
            return False
        await send_reply(update, context, reply)
        logger.info("关键词自动回复命中 cid=%s uid=%s", update.effective_chat.id, user.id)
        return True
    except Exception:
        # 自动回复绝不能因为任何异常拖垮 on_text 主流程（静默 + 记日志）
        logger.exception("关键词自动回复异常（已吞并）")
        return False
