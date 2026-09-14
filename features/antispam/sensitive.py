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

import html, re


def _sensitive_hit(text):
    """敏感词判定：明文按子串（忽略大小写），/xxx/ 形式按正则。返回命中的词条或 None。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    sget = hub.sget
    t = str(text or "")
    if not t or not sget("SENSITIVE_WORDS"):
        return None
    for w in sget("SENSITIVE_WORDS"):
        w = str(w).strip()
        if not w:
            continue
        if len(w) > 2 and w.startswith("/") and w.endswith("/"):
            try:
                if re.search(w[1:-1], t, re.I):
                    return w
            except re.error:
                logger.warning("敏感词正则非法，已跳过：%s", w)
                continue
        elif w.lower() in t.lower():
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
