# -*- coding: utf-8 -*-
"""一键举报 /report（Rose 同款，2026-09-14）

群友**回复**一条消息发 `/举报`（或 `/report`）→ 全体管理员私聊收到提醒（带原消息）。
比「看到了喊一声」快得多 —— 广告消息从发出到管理看到，中间可能已经几十人看到。

注意：跟 2026-09-14 已下线的「经营日报」是两回事，这个是群友的举报入口。

设计取舍
--------
· **必须回复一条消息**才知道举报的是什么 —— 单独发 /举报 只给用法提示。
· 通知走**私聊**（不刷群），管理员点名字能直接跳到人。
· 同一人 60 秒内重复举报**同一条消息**会被限流（防恶作剧刷管理员），
  但不同消息不限 —— 真看到多条广告要能一条条报。
· 通知失败（管理员没 /start 过机器人）静默跳过，不因此报错给举报人。
"""

from core import hub

import html
import time


# uid -> (上次举报的 (cid, mid), 时间戳)
_report_recent = {}


def _report_throttled(uid, cid, mid, window=60):
    """同一人对同一条消息 60 秒内重复举报 → True（丢弃）。"""
    global _report_recent
    now = time.time()
    key = (cid, mid)
    last_ts = _report_recent.get(uid)
    # 顺手清理过期项，避免字典无限增长
    if len(_report_recent) > 500:
        _report_recent = {k: v for k, v in _report_recent.items() if now - v[1] < window}
    if last_ts and last_ts[0] == key and now - last_ts[1] < window:
        return True
    _report_recent[uid] = (key, now)
    return False


def _report_target_text(msg):
    """被举报消息的文字（正文优先，其次 caption，都没有给个占位）。"""
    if msg is None:
        return "（消息不可读）"
    t = (getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip()
    if not t:
        t = "（图片/贴纸/视频等无文字消息）"
    return t[:400]  # 截断，避免一条举报把管理员私聊刷满


async def cmd_report(update, context):
    """群友举报：回复一条消息发 /举报，全体管理员私聊收到提醒。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_IDS = hub.ADMIN_USER_IDS
    _report_throttled = hub._report_throttled
    _report_target_text = hub._report_target_text
    get_name = hub.get_name
    logger = hub.logger
    now_bj = hub.now_bj
    send_reply = hub.send_reply
    user_link = hub.user_link

    message = getattr(update, "message", None)
    if not message:
        return
    uid = update.effective_user.id
    cid = update.effective_chat.id
    target = getattr(message, "reply_to_message", None)
    if not target:
        await send_reply(
            update, context,
            "⚠️ 请**回复**你要举报的那条消息，再发 /举报\n\n"
            "用法：长按那条消息 → 回复 → 输入 /举报")
        return

    # 限流：同一人 60 秒内重复举报同一条
    if _report_throttled(uid, cid, target.message_id):
        logger.info("举报被限流（重复）uid=%s cid=%s mid=%s", uid, cid, target.message_id)
        return

    try:
        reporter = await get_name(context.application, uid)
        tuser = target.from_user
        target_name = (await get_name(context.application, tuser.id)) if tuser else "未知用户"
        target_uid = tuser.id if tuser else 0
    except Exception:
        logger.exception("举报：取名字失败")
        reporter, target_name, target_uid = f"用户{uid}", "未知用户", 0

    body = _report_target_text(target)
    ts = now_bj().strftime("%Y-%m-%d %H:%M")
    chat_title = getattr(update.effective_chat, "title", "") or str(cid)

    notice = (
        f"🚨 <b>收到举报</b>\n"
        # str() 包一层是仓库铁律（test_web_escape_guard）：名字/标题/消息体
        # 都可能拿到数字脏值，裸 html.escape(123) 会 AttributeError 打死整个 handler。
        f"群：<code>{html.escape(str(chat_title))}</code>（<code>{cid}</code>）\n"
        f"举报人：{user_link(uid, html.escape(str(reporter)))}\n"
        f"被举报：{user_link(target_uid, html.escape(str(target_name)))}\n"
        f"时间：{ts}\n\n"
        f"原消息：\n{html.escape(str(body))}"
    )

    sent = 0
    for aid in sorted(ADMIN_USER_IDS or ()):
        if not aid:
            continue
        try:
            await context.bot.send_message(chat_id=aid, text=notice, parse_mode="HTML")
            sent += 1
        except Exception as e:
            # 管理员没 /start 过机器人 → 发不出，静默跳过（不打扰举报人）
            logger.warning("举报通知发送失败 aid=%s：%r", aid, e)

    logger.warning("举报：群 %s 用户 %s 举报了 %s 的消息（通知 %s 位管理员）",
                   cid, uid, target_uid, sent)
    await send_reply(update, context, "✅ 已通知管理员，感谢反馈。")
