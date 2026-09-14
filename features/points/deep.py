# -*- coding: utf-8 -*-
"""深链 / 私聊确认接线层

深链 / 私聊确认**接线层**：跨 ⑤ 商城与兑换两块的落地入口。

· `_deep_buy_url(kind, cid, val)` 生成 `https://t.me/<bot>?start=<kind>_<cid>_<val>`；
  蓝字点击 → bot 私聊 → `cmd_start` 分流 → 本文件里的 `_deep_*_start` 渲染确认卡。
· `_parse_dm_redeem_data` 解析确认回调的 kind 白名单，`_deep_start_confirm` 是总机。
· ⚠️ **新增一个深链 kind 必须同步改 4 处**：`cmd_start` 分流 / `_deep_*_start` /
  这里的 kind 白名单 / `on_button` 的 `data.startswith((...))` 元组。
· `_BOT_USERNAME` 为空时 `_deep_buy_url` 返回 None → 调用方必须退回按钮兜底，别让功能断掉。
· 为什么单开一个文件：它是**跨域接线层**，塞进 shop 或 redeem 任一边，另一边就读不到自己的入口
  （同 antispam 拆出 `events.py` 的理由）。
"""

from core import hub

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _deep_buy_url(kind, cid, idx):
    """竞品式兑换按钮：https://t.me/<bot>?start=<kind>_<cid>_<idx>，点了跳转 bot 私聊。
    _BOT_USERNAME 为空（启动早期/测试桩）时返回 None → 调用方退回群内 callback 直兑。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _BOT_USERNAME = hub._BOT_USERNAME
    if not _BOT_USERNAME:
        return None
    return f"https://t.me/{_BOT_USERNAME}?start={kind}_{cid}_{idx}"


async def _deep_redeem_start(update, context, payload):
    """私聊里收到 /start redeem_<cid>_<idx>：按竞品流程显示 是否兑换 / 积分不足。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _redeem_items_for = hub._redeem_items_for
    game_chips = hub.game_chips
    is_auth = hub.is_auth
    send_reply = hub.send_reply
    if not update.effective_chat or update.effective_chat.type != "private":
        await send_reply(update, context, "⚠️ 请到机器人私聊完成兑换确认。"); return
    try:
        _, cid_s, idx_s = payload.split("_", 2)
        cid, idx = int(cid_s), int(idx_s)
    except (ValueError, AttributeError):
        await send_reply(update, context, "❌ 兑换链接无效，请回群重新打开列表。"); return
    uid = update.effective_user.id
    if not is_auth(cid):
        await send_reply(update, context, "❌ 该群未授权使用本机器人。"); return
    items = _redeem_items_for(cid)
    if not items:
        await send_reply(update, context, "🎁 本群暂无可兑换商品。"); return
    if not (1 <= idx <= len(items)):
        await send_reply(update, context, "❌ 商品不存在或已下架，请回群重新打开列表。"); return
    item = items[idx - 1]
    price = int(item.get("price", 0) or 0)
    bal = game_chips[cid][uid]
    if bal < price:
        await send_reply(update, context, f"❌ 积分不足：需要 {price}，当前 {bal}。\n去群聊赢积分后再来兑换吧～")
        return
    left = int(item.get("left", 0) or 0)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 确认兑换", callback_data=f"redeem_ok_{cid}_{idx}"),
        InlineKeyboardButton("❌ 取消兑换", callback_data=f"redeem_no_{cid}_{idx}"),
    ]])
    # 逐行字段 + 等长按钮：手机端两个按钮宽度才一致（用户 2026-09-10 截图报「文本和按钮没对齐」）
    txt = (f"🎁 <b>{item['name']}</b>\n"
           f"━━━━━━━━━━━━━━━\n"
           f"价格：{price} 积分\n"
           f"剩余：{'不限' if left <= 0 else left}\n"
           f"当前积分：{bal}\n"
           f"━━━━━━━━━━━━━━━\n"
           f"是否兑换？")
    # 确认弹窗不清除（等用户点确认/取消后再编辑）；不走 REPLY_DELETE_SECONDS
    await send_reply(update, context, txt, kb=kb, parse_mode="HTML", delete_after=0)


async def _deep_mall_start(update, context, payload):
    """私聊里收到 /start mall_<cid>_<idx>：商城商品显示 是否兑换 / 积分不足。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _mall_items_on = hub._mall_items_on
    _mall_price = hub._mall_price
    game_chips = hub.game_chips
    is_auth = hub.is_auth
    send_reply = hub.send_reply
    sget = hub.sget
    if not update.effective_chat or update.effective_chat.type != "private":
        await send_reply(update, context, "⚠️ 请到机器人私聊完成兑换确认。"); return
    try:
        _, cid_s, idx_s = payload.split("_", 2)
        cid, idx = int(cid_s), int(idx_s)
    except (ValueError, AttributeError):
        await send_reply(update, context, "❌ 兑换链接无效，请回群重新打开列表。"); return
    uid = update.effective_user.id
    if not is_auth(cid):
        await send_reply(update, context, "❌ 该群未授权使用本机器人。"); return
    if not sget("MALL_ENABLED"):
        await send_reply(update, context, "ℹ️ 积分商城未开启。"); return
    items = _mall_items_on()
    if not (1 <= idx <= len(items)):
        await send_reply(update, context, "❌ 商品不存在或已下架，请回群重新打开列表。"); return
    item = items[idx - 1]
    stk = item.get("stock")
    if isinstance(stk, int) and stk <= 0:
        await send_reply(update, context, "❌ 该商品已售罄。"); return
    price = _mall_price(item)
    bal = game_chips[cid][uid]
    if bal < price:
        await send_reply(update, context, f"❌ 积分不足：需要 {price}，当前 {bal}。\n去群聊赢积分后再来兑换吧～")
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 确认兑换", callback_data=f"mall_ok_{cid}_{idx}"),
        InlineKeyboardButton("❌ 取消兑换", callback_data=f"mall_no_{cid}_{idx}"),
    ]])
    # 逐行字段 + 等长按钮：与积分兑换确认卡同款（手机端按钮宽度一致）
    txt = (f"🛒 <b>{item['name']}</b>\n"
           f"━━━━━━━━━━━━━━━\n"
           f"价格：{price} 积分\n"
           f"当前积分：{bal}\n"
           f"━━━━━━━━━━━━━━━\n"
           f"是否兑换？")
    # 确认弹窗不清除（等用户点确认/取消后再编辑）；不走 REPLY_DELETE_SECONDS
    await send_reply(update, context, txt, kb=kb, parse_mode="HTML", delete_after=0)


async def _deep_start_confirm(q, data, context):
    """群内点「立即兑换」蓝色文字 → 跳转 bot 私聊 → 机器人显示 是否兑换/积分不足。
    本函数处理私聊里确认/取消按钮回调（callback_data=redeem_ok_*/redeem_no_*/mall_*/sexch_*）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    _mall_dm_ok = hub._mall_dm_ok
    _parse_dm_redeem_data = hub._parse_dm_redeem_data
    _redeem_dm_ok = hub._redeem_dm_ok
    _season_exchange_execute = hub._season_exchange_execute
    is_bot_admin = hub.is_bot_admin
    kind, action, cid, idx = _parse_dm_redeem_data(data)
    uid = q.from_user.id
    if kind not in ("redeem", "mall", "sexch") or action not in ("ok", "no") or cid is None:
        await q.answer("无效操作", show_alert=True); return
    if uid in BLACKLISTED_USERS and not is_bot_admin(uid):
        await q.answer("🚫 你已被禁止使用本机器人", show_alert=True); return
    if action == "no":
        try: await q.message.edit_text("🚫 已取消兑换。")
        except Exception: pass
        await q.answer("已取消"); return
    if kind == "redeem":
        ok, txt = await _redeem_dm_ok(context, cid, uid, idx)
    elif kind == "mall":
        ok, txt = await _mall_dm_ok(context, cid, uid, idx)
    else:
        # sexch：idx 段承载的是「消耗的聊天积分」
        ok, txt = await _season_exchange_execute(context, cid, uid, idx)
    try:
        await q.message.edit_text(txt)
    except Exception:  # silent-ok: 编辑旧消息失败时，结果已通过 q.answer 弹给用户
        pass
    await q.answer(txt if not ok else "🎉 兑换成功！", show_alert=not ok)


def _parse_dm_redeem_data(data):
    """私聊确认回调数据 redeem_ok_<cid>_<idx> / redeem_no_<cid>_<idx> / mall_*。
    返回 (kind, action, cid, idx)；cid 为负数时整段不含下划线，可直接按 '_' 切。"""
    try:
        kind, action, cid_s, idx_s = data.split("_", 3)
        return kind, action, int(cid_s), int(idx_s)
    except (ValueError, AttributeError):
        return None, None, None, None
