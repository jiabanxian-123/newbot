# -*- coding: utf-8 -*-
"""敏感词过滤（归属存疑）

敏感词过滤（文本与媒体 caption 共用）：命中即删，可按档禁言/踢出。

⚠️ **归属存疑**：敏感词拦截与 `_forcesub_enforce` / `_observe_enforce` / `_autodel_enforce`
一样是**群管拦截**，挂在 `on_text` / `on_media` 上（双路径铁律），逻辑上属于
`features/antispam` 域；当年被抽进 points 只是因为设置项分在 mod 组。
本次**只按现状搬进独立文件并写明问题**，不跨包搬迁 —— 跨包动的是别人的域，须单独拍板。

· 拦截顺序：黑名单 → 强制订阅 → 观察期 → 自动删除 → 敏感词 → 入群验证答题（必须最后）。
· 明文按子串（忽略大小写）匹配，`/xxx/` 形式按正则；查 `text` 也要查 `caption`。
· 删除失败**不许静默**：私聊 ADMIN 报错（含 `repr(e)`），否则管理员只看到「设了没反应」。
"""

from core import hub

import html, re, unicodedata


def _normalize_for_match(text):
    """敏感词匹配前的归一化（2026-09-14 Rose 同款 lookalike）。

    把「全角字符 / 同形字母 / emoji 装饰字符 / 大小写」这四类绕过手段压平，
    让明文子串匹配也能拦下「微信」写「微❤信」「ｗ信」「b0t」写「bot」这种。

    只用于匹配阶段，**不动原文** —— 机器人发给群的消息还是原文，玩家看到的
    不变；归一只是「翻译员」，让词条和消息用同一种话讲一遍。

    ⚠️ 严格限制：
      · 只归一「字母 / 数字 / 标点 / 杂符号」，**绝不**对中文字符做同形替换
        （微/徵、土/士 这种同形中文字不能动，否则会误伤 —— 2026-09-12 那次
        「一字不差」就是用户对过度模糊的反感）。
      · emoji 全去掉（category So/Sk/Mn），这样「微❤️信」「ｗ信」都被压回「微信」。
      · 西里尔/希腊字母只做最常见几个的映射（2025-07 Rose 那次同款）；
        不做完整 Unicode confusables 表（避免性能问题和漏匹配）。
    """
    if not text:
        return ""
    # ① NFKC：全角→半角、组合字符分解后重组。中文不受影响，但「ｗ」(全角)→「w」
    t = unicodedata.normalize("NFKC", str(text))
    # ② 大小写归一（casefold 比 lower 更彻底，处理德语 ß→ss、土耳其语 İ→i 等）
    t = t.casefold()
    # ③ 同形字母 / 数字 归一（仅 ASCII + 西里尔 + 希腊常见几个；中文一字不动）
    t = t.translate(str.maketrans({
        # 数字 ↔ 字母（视觉同形）
        "0": "o", "1": "l", "5": "s",
        # 希腊 → 拉丁（视觉同形）
        "α": "a", "ο": "o", "ρ": "p", "ω": "w", "ε": "e",
        # 西里尔 → 拉丁（视觉同形；小写）
        "а": "a", "е": "e", "и": "n", "о": "o", "р": "p",
        "в": "b", "г": "r", "к": "k", "м": "m", "н": "h",
        "с": "c", "т": "t", "у": "y", "х": "x", "ѕ": "s", "ј": "j",
        "і": "i", "ѡ": "w", "һ": "h", "ԁ": "d", "ь": "", "ъ": "",
        # 西里尔大写
        "А": "a", "В": "b", "Е": "e", "Н": "h", "К": "k",
        "М": "m", "О": "o", "Р": "p", "С": "c", "Т": "t",
        "Х": "x", "У": "y",
    }))
    # ④ 去装饰字符（emoji、变音符号、合字）。**中文不在这里** —— 汉字的 category 是 Lo
    out = []
    for ch in t:
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me", "So", "Sk"):  # 变音符号 / 杂符号
            continue
        out.append(ch)
    return "".join(out)


def _sensitive_hit(text):
    """敏感词判定：明文先归一再子串；/xxx/ 形式按正则（输入也走归一，去掉 emoji 装饰）。

    2026-09-14 Rose 同款 lookalike：词条和消息都过 `_normalize_for_match`，
    拦下「微信」→「微❤信」「ｗ信」「b0t」→「bot」这种形近字绕过。
    原文**不变**，机器人发给群的消息还是原样。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    sget = hub.sget
    t = str(text or "")
    if not t or not sget("SENSITIVE_WORDS"):
        return None
    t_norm = _normalize_for_match(t)
    for w in sget("SENSITIVE_WORDS"):
        w = str(w).strip()
        if not w:
            continue
        if len(w) > 2 and w.startswith("/") and w.endswith("/"):
            # 正则：输入走归一（去掉 emoji/装饰），正则本身用户自己写变形字符类
            try:
                if re.search(w[1:-1], t_norm, re.I):
                    return w
            except re.error:
                logger.warning("敏感词正则非法，已跳过：%s", w)
                continue
        else:
            # 明文：双向归一后子串
            w_norm = _normalize_for_match(w)
            if w_norm and w_norm in t_norm:
                return w
    return None


async def _sensitive_enforce(update, context):
    """敏感词过滤（文本与媒体 caption 共用）：命中即删，可按档禁言/踢出。返回 True=已拦截。

    此前只有 on_text 调用、且只看 message.text → 图片/贴纸/视频的 caption 里的敏感词
    永远不会被查（用户报障「敏感词设了不删」的一半根因）。
    删除失败（bot 无删除权限）不再静默：私聊管理员告警，否则管理员只看到「设了没反应」。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    _invite_flag_ad = hub._invite_flag_ad
    _mod_punish = hub._mod_punish
    _sensitive_hit = hub._sensitive_hit
    is_bot_admin = hub.is_bot_admin
    is_group_chat = hub.is_group_chat
    logger = hub.logger
    sget = hub.sget
    if not sget("SENSITIVE_ENABLED"):
        return False
    user, message = update.effective_user, update.effective_message
    if not message or not user or user.is_bot:
        return False
    if not is_group_chat(update) or is_bot_admin(user.id):
        return False
    cid = update.effective_chat.id
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    if not _sensitive_hit(text):
        return False
    try:
        await context.bot.delete_message(chat_id=cid, message_id=message.message_id)
    except Exception as e:
        logger.warning("敏感词消息删除失败 cid=%s mid=%s：%r", cid, message.message_id, e)
        try:
            await context.bot.send_message(
                ADMIN_USER_ID,
                f"⚠️ 敏感词消息删除失败（请确认机器人有删除消息权限）：\n"
                f"群 <code>{cid}</code> · 消息 <code>{message.message_id}</code> · 错误 <code>{html.escape(str(repr(e)))}</code>",
                parse_mode="HTML")
        except Exception:
            pass
    if sget("SENSITIVE_ACTION"):
        await _mod_punish(context, cid, user.id, sget("SENSITIVE_ACTION"), sget("SENSITIVE_MUTE_SECONDS"),
                          user.first_name or f"用户{user.id}", "敏感词")
    _invite_flag_ad(cid, user.id, "敏感词")   # 风控连坐：被邀请人发广告 → 邀请人不再计合格
    return True
