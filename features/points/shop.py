# -*- coding: utf-8 -*-
"""⑤ 商城与兑换

   以及「聊天积分 ↔ 排位分」的兑换比例文案。

· 扣款逻辑**只留一份出口**：`_redeem_execute` 是实物兑换的唯一实现
  （群内命令与私聊「确认兑换」都调它），别在 `cmd_points_redeem` 里再写一份。
· 商城两条入口 `cmd_mall_buy`（群内）与 `_mall_dm_ok`（私聊确认）**必须同口径**：
  库存 / 门槛 / 扣款 / 台账 / 管理员通知 —— 改一处要复扫另一处。
· 商品价格新旧结构兼容走 `_mall_price`，不要在调用点各写一遍。
· `_exchange_rate_text` 供排位分兑换面板用（`RANKED_EXCHANGE_COST` / `RANKED_EXCHANGE_GAIN`）。
"""

from core import hub

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
import html, secrets


def _exchange_rate_text():
    """兑换比例文案：1:1 时显示「1 积分 = 1 赛季分」。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    return f"{sget('RANKED_EXCHANGE_COST')} 积分 = {sget('RANKED_EXCHANGE_GAIN')} 赛季分"


def _mall_price(item):
    """商品价格兼容新旧结构（旧 {"name","value"} / 新 {"name","price",...}）。"""
    try:
        return int(item.get("price", item.get("value", 0)) or 0)
    except (TypeError, ValueError):
        return 0


def _mall_items_on():
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    return [x for x in sget("MALL_ITEMS") if x.get("on", True)]


def _mall_panel(page, items, cid):
    """商城货架卡：正文逐商品小卡 + 行内蓝色「立即兑换」文本超链接（售罄置灰）。
    返回 (text, rows)；cmd_mall 首屏与 mall_page_ 翻页共用，保证样式一致。

    样式对齐用户 2026-09-10 指定截图：商品名 + 「└ 价格 积分 剩余 N 立即兑换(蓝字)」，
    底部「第 x/y 页」；不再每商品占一行按钮（按钮只留翻页）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _deep_buy_url = hub._deep_buy_url
    _mall_price = hub._mall_price
    sget = hub.sget
    pages = max(1, (len(items) + sget("MALL_PAGE_SIZE") - 1) // sget("MALL_PAGE_SIZE"))
    page = max(1, min(page, pages))
    chunk = items[(page - 1) * sget("MALL_PAGE_SIZE"): page * sget("MALL_PAGE_SIZE")]
    lines = ["🛒 <b>积分商城</b>", ""]
    rows = []
    for i, item in enumerate(chunk, (page - 1) * sget("MALL_PAGE_SIZE") + 1):
        stk = item.get("stock")
        sold_out = isinstance(stk, int) and stk <= 0
        left_txt = "不限" if not isinstance(stk, int) else str(stk)
        lines.append(f"🟡 <b>{html.escape(str(item['name']))}</b>")
        meta = f"{_mall_price(item)} 积分 剩余 {left_txt}"
        url = _deep_buy_url("mall", cid, i)
        if sold_out:
            lines.append(f"└ {meta} <s>已售罄</s>")
        elif url:
            lines.append(f"└ {meta} <a href='{html.escape(str(url), quote=True)}'>立即兑换</a>")
        else:
            # 启动早期/无 bot 用户名 → 没有可用深链，退回底部按钮直兑
            lines.append(f"└ {meta}")
            rows.append([InlineKeyboardButton(f"{i}. 立即兑换", callback_data=f"mall_buy_{i}")])
        lines.append("")
    lines.append(f"第 {page}/{pages} 页")
    nav = []
    # 2026-09-13：箭头由 ⬅/➡ 换成**实心三角** ◀/▶，与榜单翻页统一（用户「箭头太丑」）。
    # 商城按钮少（最多 3 个），不会撑宽，所以保留「上一页/下一页」文字更好认。
    if page > 1:
        nav.append(InlineKeyboardButton("◀ 上一页", callback_data=f"mall_page_{page-1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(f"📄 {page}/{pages}", callback_data="noop"))
    if page < pages:
        nav.append(InlineKeyboardButton("下一页 ▶", callback_data=f"mall_page_{page+1}"))
    if nav:
        rows.append(nav)
    return "\n".join(lines), rows


async def cmd_mall(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fmt_tpl = hub._fmt_tpl
    _mall_panel = hub._mall_panel
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    if not sget("MALL_ENABLED"):
        await send_reply(update, context, "ℹ️ 积分商城未开启。"); return
    items = [x for x in sget("MALL_ITEMS") if x.get("on", True)]
    if not items:
        await send_reply(update, context, _fmt_tpl("mall_msg_empty")); return
    cid = update.effective_chat.id
    page = 1
    if context.args and context.args[0].isdigit():
        page = max(1, int(context.args[0]))
    text, rows = _mall_panel(page, items, cid)
    kb = InlineKeyboardMarkup(rows) if rows else None
    await send_reply(update, context, text, kb=kb, delete_after=int(sget("MALL_LIST_DELETE_SECONDS") or 0))


async def cmd_mall_buy(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    _fmt_tpl = hub._fmt_tpl
    _mall_price = hub._mall_price
    blackjack_profit_by_date = hub.blackjack_profit_by_date
    chat_name_cache = hub.chat_name_cache
    game_chips = hub.game_chips
    get_name = hub.get_name
    is_group_chat = hub.is_group_chat
    jinhua_profit_by_date = hub.jinhua_profit_by_date
    logger = hub.logger
    mall_orders = hub.mall_orders
    need_auth = hub.need_auth
    now_bj = hub.now_bj
    poker_profit_by_date = hub.poker_profit_by_date
    race_profit_by_date = hub.race_profit_by_date
    save_data = hub.save_data
    send_reply = hub.send_reply
    sget = hub.sget
    user_first_seen = hub.user_first_seen
    wallet_locks = hub.wallet_locks
    if not await need_auth(update, context): return
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 购买请在群聊中进行。"); return
    if not sget("MALL_ENABLED"):
        await send_reply(update, context, "ℹ️ 积分商城未开启。"); return
    items = [x for x in sget("MALL_ITEMS") if x.get("on", True)]
    if not items:
        await send_reply(update, context, _fmt_tpl("mall_msg_empty")); return
    if not context.args:
        await send_reply(update, context, f"用法：购买 编号（1~{len(items)}），用「积分商城」查看列表。"); return
    arg = context.args[0].strip()
    item = None
    if arg.isdigit() and 1 <= int(arg) <= len(items):
        item = items[int(arg) - 1]
    else:
        name = arg.lstrip("0123456789.、 ").strip()
        item = next((x for x in items if x["name"] == name), None)
    if not item:
        await send_reply(update, context, "❌ 没有这个商品，用「积分商城」查看列表。"); return
    stk = item.get("stock")
    if isinstance(stk, int) and stk <= 0:
        await send_reply(update, context, "❌ 该商品已售罄。"); return
    cid, uid = update.effective_chat.id, update.effective_user.id
    price = _mall_price(item)
    if sget("MALL_MIN_AGE_DAYS") > 0:  # 兑换门槛1：与 bot 首次互动满 N 天（小号没有历史）
        seen = user_first_seen.get(uid)
        days = (now_bj().timestamp() - seen) / 86400 if seen else 0.0
        if days < sget("MALL_MIN_AGE_DAYS"):
            await send_reply(update, context, f"❌ 兑换门槛：使用满 {sget('MALL_MIN_AGE_DAYS')} 天才能兑换（当前 {days:.0f} 天）。"); return
    if sget("MALL_MIN_ACTIVE_DAYS") > 0:  # 兑换门槛2：有游戏盈亏记录的天数 ≥N
        active_days = set()
        for prof in (poker_profit_by_date, race_profit_by_date, blackjack_profit_by_date, jinhua_profit_by_date):
            for d, chats in prof.items():
                if uid in (chats.get(cid) or {}): active_days.add(d)
        if len(active_days) < sget("MALL_MIN_ACTIVE_DAYS"):
            await send_reply(update, context, f"❌ 兑换门槛：累计 {sget('MALL_MIN_ACTIVE_DAYS')} 天参与游戏才能兑换（当前 {len(active_days)} 天）。"); return
    async with wallet_locks[uid]:
        if game_chips[cid][uid] < price:
            await send_reply(update, context, f"❌ 积分不足：需要 {price}，当前 {game_chips[cid][uid]}。"); return
        game_chips[cid][uid] -= price
        if isinstance(stk, int):
            item["stock"] = stk - 1
        mall_orders.append({"ts": now_bj().strftime("%Y-%m-%d %H:%M"), "cid": cid, "uid": uid,
                            "name": await get_name(context.application, uid), "item": item["name"], "price": price})
        save_data()
    await send_reply(update, context, _fmt_tpl("mall_msg_buy",
        name=await get_name(context.application, uid), item=item["name"], price=price, balance=game_chips[cid][uid]))
    # 消费点不再检查等级：等级按「累计积分」算，花分不掉级（此前误传余额 → 花分即降级公告）
    try:
        await context.bot.send_message(ADMIN_USER_ID,
            f"🛒 积分商城订单\n群：{chat_name_cache.get(cid, cid)}\n"
            f"玩家：{await get_name(context.application, uid)}（{uid}）\n商品：{item['name']}（{price} 积分）")
    except Exception:
        logger.exception("商城订单通知管理员失败")


async def _mall_dm_ok(context, cid, uid, idx):
    """私聊「是否兑换 → 确认」：商城商品二次校验后执行购买（与群内购买同口径：库存/门槛/扣款/台账/管理员）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    _fmt_tpl = hub._fmt_tpl
    _mall_items_on = hub._mall_items_on
    _mall_price = hub._mall_price
    blackjack_profit_by_date = hub.blackjack_profit_by_date
    chat_name_cache = hub.chat_name_cache
    game_chips = hub.game_chips
    get_name = hub.get_name
    is_auth = hub.is_auth
    jinhua_profit_by_date = hub.jinhua_profit_by_date
    logger = hub.logger
    mall_orders = hub.mall_orders
    now_bj = hub.now_bj
    poker_profit_by_date = hub.poker_profit_by_date
    race_profit_by_date = hub.race_profit_by_date
    save_data = hub.save_data
    send_settle = hub.send_settle
    sget = hub.sget
    user_first_seen = hub.user_first_seen
    wallet_locks = hub.wallet_locks
    if not is_auth(cid):
        return False, "❌ 该群未授权使用本机器人。"
    if not sget("MALL_ENABLED"):
        return False, "ℹ️ 积分商城未开启。"
    items = _mall_items_on()
    if not (1 <= idx <= len(items)):
        return False, "❌ 商品不存在或已下架，请回群重新打开列表。"
    item = items[idx - 1]
    stk = item.get("stock")
    if isinstance(stk, int) and stk <= 0:
        return False, "❌ 该商品已售罄。"
    price = _mall_price(item)
    if sget("MALL_MIN_AGE_DAYS") > 0:  # 兑换门槛1：与 bot 首次互动满 N 天
        seen = user_first_seen.get(uid)
        days = (now_bj().timestamp() - seen) / 86400 if seen else 0.0
        if days < sget("MALL_MIN_AGE_DAYS"):
            return False, f"❌ 兑换门槛：使用满 {sget('MALL_MIN_AGE_DAYS')} 天才能兑换（当前 {days:.0f} 天）。"
    if sget("MALL_MIN_ACTIVE_DAYS") > 0:  # 兑换门槛2：有游戏盈亏记录的天数 ≥N
        active_days = set()
        for prof in (poker_profit_by_date, race_profit_by_date, blackjack_profit_by_date, jinhua_profit_by_date):
            for d, chats in prof.items():
                if uid in (chats.get(cid) or {}): active_days.add(d)
        if len(active_days) < sget("MALL_MIN_ACTIVE_DAYS"):
            return False, f"❌ 兑换门槛：累计 {sget('MALL_MIN_ACTIVE_DAYS')} 天参与游戏才能兑换（当前 {len(active_days)} 天）。"
    async with wallet_locks[uid]:
        if game_chips[cid][uid] < price:
            return False, f"❌ 积分不足：需要 {price}，当前 {game_chips[cid][uid]}。"
        game_chips[cid][uid] -= price
        if isinstance(stk, int):
            item["stock"] = stk - 1
        mall_orders.append({"ts": now_bj().strftime("%Y-%m-%d %H:%M"), "cid": cid, "uid": uid,
                            "name": await get_name(context.application, uid), "item": item["name"], "price": price})
        save_data()
    await send_settle(context.application, cid, _fmt_tpl("mall_msg_buy",
        name=await get_name(context.application, uid), item=item["name"], price=price, balance=game_chips[cid][uid]))
    # 消费点不再检查等级：等级按「累计积分」算，花分不掉级（此前误传余额 → 花分即降级公告）
    try:
        await context.bot.send_message(ADMIN_USER_ID,
            f"🛒 积分商城订单\n群：{chat_name_cache.get(cid, cid)}\n"
            f"玩家：{await get_name(context.application, uid)}（{uid}）\n商品：{item['name']}（{price} 积分）")
    except Exception:
        logger.exception("商城订单通知管理员失败")
    return True, f"🎉 兑换成功：{item['name']}"


def _redeem_gate():
    """兑换时间窗检查：返回拒绝文案或 None（可兑换）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _parse_dt_bj = hub._parse_dt_bj
    now_bj = hub.now_bj
    sget = hub.sget
    now = now_bj()
    start, end = _parse_dt_bj(sget("REDEEM_START")), _parse_dt_bj(sget("REDEEM_END"))
    if start and now < start:
        return f"⏳ 兑换活动尚未开始（{sget('REDEEM_START')} 起）。"
    if end and now > end:
        return "🔚 兑换活动已结束。"
    return None


async def _redeem_execute(context, cid, uid, item):
    """执行兑换（命令与按钮回调共用）：限购→扣费→自动下架→台账→群通知+私聊通知。
    返回 None=成功；字符串=拒绝原因。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    _fmt_tpl = hub._fmt_tpl
    chat_name_cache = hub.chat_name_cache
    game_chips = hub.game_chips
    get_name = hub.get_name
    ledger_add = hub.ledger_add
    logger = hub.logger
    now_bj = hub.now_bj
    redeem_counts = hub.redeem_counts
    redeem_orders = hub.redeem_orders
    save_data = hub.save_data
    send_settle = hub.send_settle
    sget = hub.sget
    wallet_locks = hub.wallet_locks
    if sget("REDEEM_MAX_PER_USER") > 0 and redeem_counts.get(uid, 0) >= sget("REDEEM_MAX_PER_USER"):
        return f"❌ 每人限兑 {sget('REDEEM_MAX_PER_USER')} 次，你已用完额度。"
    price = int(item.get("price", 0) or 0)
    left = int(item.get("left", 0) or 0)
    async with wallet_locks[uid]:
        if game_chips[cid][uid] < price:
            return f"❌ 积分不足：需要 {price}，当前 {game_chips[cid][uid]}。"
        game_chips[cid][uid] -= price
        if left > 0:
            item["left"] = left - 1
            if item["left"] <= 0:
                item["on"] = False  # 兑完自动下架
        item["redeemed"] = int(item.get("redeemed", 0) or 0) + 1
        redeem_counts[uid] = redeem_counts.get(uid, 0) + 1
        ledger_add(cid, uid, 0, price, "兑换")  # 资金流台账：玩家→系统
        order_no = f"DH-{secrets.token_hex(4)}"  # 防伪单号：群通知/私聊/管理员对账三处一致
        order_ts = now_bj().strftime("%Y-%m-%d %H:%M")
        redeem_orders.append({"no": order_no, "ts": order_ts, "cid": cid, "uid": uid,
                              "item": item["name"], "price": price, "bal": game_chips[cid][uid]})
        del redeem_orders[:-500]  # 只留最近 500 条，防膨胀
        save_data()
        # ★ 库存 / 上架状态属于**设置项**（redeem_goods 存 bot_settings.json），
        #   而 save_data() 只写数据分片 → 不落盘的话重启后库存回满、商品重新上架
        #   = 限量商品可以无限兑。这里补一次设置落盘。
        try:
            hub.save_settings({})
        except Exception:
            logger.exception("兑换后保存库存失败（库存可能未落盘）")
    uname = await get_name(context.application, uid)
    # 群通知不带防伪单号（群友能看到别人的单号就失去核验意义）；单号只发用户私聊+管理员对账
    await send_settle(context.application, cid, _fmt_tpl("redeem_msg_ok_group",
        name=uname, goodsName=item["name"], pointNum=price,
        balance=game_chips[cid][uid]))
    # 消费点不再检查等级：等级按「累计积分」算，花分不掉级（此前误传余额 → 花分即降级公告）
    try:
        await context.bot.send_message(uid, _fmt_tpl("redeem_msg_ok_dm", goodsName=item["name"], pointNum=price)
                                       + f"\n🔎 防伪单号 {order_no}（管理员发货凭此号核对）")
    except TelegramError:
        pass  # 未私聊过 bot 的用户收不到 DM；单号仍在管理员对账+后台兑换订单页可查
    try:
        await context.bot.send_message(ADMIN_USER_ID,
            f"🧾 积分兑换订单｜单号 {order_no}\n群：{chat_name_cache.get(cid, cid)}\n"
            f"用户：{uname}（{uid}）\n商品：{item['name']}（{price} 积分）\n时间：{order_ts}")
    except Exception:
        logger.exception("兑换订单通知管理员失败")
    return None


def _redeem_items_for(cid):
    """本群可见的兑换商品（上架 + target_groups 命中本群）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    redeem_goods = hub.redeem_goods
    return [x for x in redeem_goods if x.get("on", True)
            and (not x.get("target_groups") or cid in x["target_groups"])]


async def _redeem_dm_ok(context, cid, uid, idx):
    """私聊「是否兑换 → 确认」：二次校验（时间窗/商品/余额）后执行兑换。
    返回 (ok, 提示文本)。群通知+私聊单号+管理员对账全部走 _redeem_execute。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _redeem_execute = hub._redeem_execute
    _redeem_gate = hub._redeem_gate
    _redeem_items_for = hub._redeem_items_for
    is_auth = hub.is_auth
    if not is_auth(cid):
        return False, "❌ 该群未授权使用本机器人。"
    gate = _redeem_gate()
    if gate:
        return False, gate
    items = _redeem_items_for(cid)
    if not (1 <= idx <= len(items)):
        return False, "❌ 商品不存在或已下架，请回群重新打开列表。"
    err = await _redeem_execute(context, cid, uid, items[idx - 1])
    if err:
        return False, err
    return True, f"🎉 兑换成功：{items[idx - 1]['name']}"


async def cmd_points_redeem(update, context):
    """积分兑换（阿福式活动）：发触发词看商品按钮列表，点蓝色按钮立即兑换；
    仍支持「触发词 编号/名称」。剩余 0=不限；限量兑完自动下架；支持起止时间与每人限购。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    REDEEM_CMD = hub.REDEEM_CMD
    _deep_buy_url = hub._deep_buy_url
    _redeem_execute = hub._redeem_execute
    _redeem_gate = hub._redeem_gate
    is_group_chat = hub.is_group_chat
    need_auth = hub.need_auth
    redeem_goods = hub.redeem_goods
    safe_send = hub.safe_send
    schedule_delete = hub.schedule_delete
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 积分兑换请在群聊中使用。"); return
    cid, uid = update.effective_chat.id, update.effective_user.id
    gate = _redeem_gate()
    if gate:
        await send_reply(update, context, gate); return
    items = [x for x in redeem_goods if x.get("on", True)
             and (not x.get("target_groups") or cid in x["target_groups"])]
    if not items:
        await send_reply(update, context, "🎁 本群暂无可兑换商品，管理员可在后台「积分系统 → 积分兑换」给本群上架。"); return
    args = context.args or []
    if not args:  # 货架卡：正文逐商品小卡 + 行内蓝色「立即兑换」文本超链接（点蓝字跳私聊确认）
        lines = ["🎁 <b>积分兑换</b>", ""]
        rows = []
        for i, x in enumerate(items, 1):
            price = int(x.get("price", 0) or 0)
            left = int(x.get("left", 0) or 0)
            left_txt = "不限" if left <= 0 else str(left)
            lines.append(f"🟡 <b>{html.escape(str(x['name']))}</b>")
            desc = str(x.get("desc", "") or "").strip()
            if desc and len(desc) <= 60:
                lines.append(f"<i>{html.escape(str(desc))}</i>")
            meta = f"{price} 积分 剩余 {left_txt}"
            url = _deep_buy_url("redeem", cid, i)
            if url:
                lines.append(f"└ {meta} <a href='{html.escape(str(url), quote=True)}'>立即兑换</a>")
            else:
                # 启动早期/无 bot 用户名 → 没有可用深链，退回底部按钮直兑
                lines.append(f"└ {meta}")
                rows.append([InlineKeyboardButton(f"{i}. 立即兑换", callback_data=f"redeem_buy_{i}")])
            lines.append("")
        msg = await safe_send(context.bot, cid, "\n".join(lines),
                              reply_markup=(InlineKeyboardMarkup(rows) if rows else None))
        _mls = int(sget("MALL_LIST_DELETE_SECONDS") or 0)   # 读时取值：网页改完立即生效
        if msg and _mls > 0:
            schedule_delete(context.application, cid, msg, _mls)
        return
    arg = args[0].strip()
    item = None
    if arg.isdigit() and 1 <= int(arg) <= len(items):
        item = items[int(arg) - 1]
    else:
        item = next((x for x in items if x["name"] == arg), None)
    if not item:
        await send_reply(update, context, f"❌ 没有这个商品，发「{REDEEM_CMD}」查看列表。"); return
    err = await _redeem_execute(context, cid, uid, item)
    if err:
        await send_reply(update, context, err)
