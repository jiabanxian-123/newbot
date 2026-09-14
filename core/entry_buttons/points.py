# -*- coding: utf-8 -*-
"""按钮回调 handler —— 2026-09-13 从 core/entry.py 的 on_button 拆出。

只做搬运，未改任何逻辑。每个函数对应 on_button 里一个 `data` 命名空间分支；
外层保留 `if <原 test>: await <fn>(...); return`（分支体命中后必 return，故等价）。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
⚠️ 本包**禁止** import core.entry（循环导入）。
"""
from core import hub



async def _btn_deep_start_confirm(update, context, q, cid, uid, data):
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    _deep_start_confirm = hub._deep_start_confirm
    is_bot_admin = hub.is_bot_admin
    if uid in BLACKLISTED_USERS and not is_bot_admin(uid):
        await q.answer("🚫 你已被禁止使用本机器人", show_alert=True); return
    await _deep_start_confirm(q, data, context)
    return


async def _btn_season_exchange(update, context, q, cid, uid, data):
    _season_exchange_execute = hub._season_exchange_execute
    send_reply = hub.send_reply
    try: _sc = int(data[len("sexch_ask_"):])
    except ValueError:
        await q.answer("按钮已过期", show_alert=True); return
    _sok, _stxt = await _season_exchange_execute(context, cid, uid, _sc)
    if _sok:
        await send_reply(update, context, _stxt)
    await q.answer(_stxt.split("\n")[0][:190], show_alert=not _sok)
    return


async def _btn_lottery_join(update, context, q, cid, uid, data):
    _lottery_active = hub._lottery_active
    _lottery_refresh_announce = hub._lottery_refresh_announce
    _lottery_try_join = hub._lottery_try_join
    game_chips = hub.game_chips
    get_name = hub.get_name
    lo = _lottery_active(cid)
    if not lo:
        await q.answer("当前没有进行中的抽奖", show_alert=True); return
    ok, info = await _lottery_try_join(context.application, lo, uid, cid)
    if ok:
        name = await get_name(context.application, uid, cid=cid)
        bal = game_chips.get(cid, {}).get(uid, 0)
        await q.answer(f"✅ {name} 参与成功！你是第 {info} 位参与者\n💰 余额：{bal}", show_alert=True)
        await _lottery_refresh_announce(context.application, cid, lo)
    elif info == "dup":
        await q.answer("⚠️ 你已经参与过啦，等开奖即可", show_alert=True)
    else:
        await q.answer(f"❌ {info}", show_alert=True)
    return


async def _btn_redeem_show(update, context, q, cid, uid, data):
    redeem_goods = hub.redeem_goods
    try: idx = int(data[len("redeem_show_"):])
    except ValueError: await q.answer(); return
    items = [x for x in redeem_goods if x.get("on", True)]
    if not (1 <= idx <= len(items)): await q.answer("商品已下架", show_alert=True); return
    x = items[idx - 1]
    left = int(x.get("left", 0) or 0)
    await q.answer(f"#{idx} {x['name']}\n价格 {int(x.get('price', 0) or 0)} 分｜剩余 {'不限' if left <= 0 else left}\n点 ✅ 立即兑换 直接兑换", show_alert=True)
    return


async def _btn_redeem_buy(update, context, q, cid, uid, data):
    _redeem_buy_cb = hub._redeem_buy_cb
    try: idx = int(data[len("redeem_buy_"):])
    except ValueError:
        await q.answer("无效商品", show_alert=True); return
    await _redeem_buy_cb(q, idx, context)
    return


async def _btn_redpacket_grab(update, context, q, cid, uid, data):
    _rp_grab = hub._rp_grab
    rp_packets = hub.rp_packets
    p = rp_packets.get(data[8:])
    if not p:
        await q.answer("红包已结束或过期", show_alert=True); return
    await _rp_grab(p, data[8:], uid, context, q)
    return


async def _btn_buy_confirm(update, context, q, cid, uid, data):
    _buy_settle = hub._buy_settle
    is_bot_admin = hub.is_bot_admin
    if not is_bot_admin(uid):
        await q.answer("仅 Bot 管理员可操作", show_alert=True); return
    await _buy_settle(context, data[6:], data.startswith("buyok_"), q)
    return


async def _btn_mall(update, context, q, cid, uid, data):
    """积分商城三个回调：mall_show_（详情）/ mall_buy_（直接兑换）/ mall_page_（翻页）。

    2026-09-14 从 on_button 的内联块搬出（原本是 if 链里的 40 行内联体），
    逻辑未动；搬出后 on_button 只查表，不在函数体里堆分支。
    """
    InlineKeyboardMarkup = hub.InlineKeyboardMarkup
    _mall_panel = hub._mall_panel
    _mall_price = hub._mall_price
    _redeem_execute = hub._redeem_execute
    sget = hub.sget
    if data.startswith("mall_show_"):
        # 商品详情：弹出商品信息，1 秒后回到原列表
        try: idx = int(data[len("mall_show_"):])
        except ValueError: await q.answer(); return
        items = [x for x in sget("MALL_ITEMS") if x.get("on", True)]
        if not (1 <= idx <= len(items)): await q.answer("商品已下架", show_alert=True); return
        it = items[idx - 1]
        stk = it.get("stock")
        stk_txt = "不限量" if not isinstance(stk, int) else (f"剩 {stk}" if stk > 0 else "已售罄")
        await q.answer(f"#{idx} {it['name']}\n价格 {_mall_price(it)} 分｜{stk_txt}\n点 ✅ 立即兑换 直接购买", show_alert=True)
        return
    if data.startswith("mall_buy_"):
        try: idx = int(data[len("mall_buy_"):])
        except ValueError: await q.answer(); return
        if not sget("MALL_ENABLED"): await q.answer("商城未开启", show_alert=True); return
        items = [x for x in sget("MALL_ITEMS") if x.get("on", True)]
        if not (1 <= idx <= len(items)): await q.answer("商品已下架", show_alert=True); return
        it = items[idx - 1]
        stk = it.get("stock")
        if isinstance(stk, int) and stk <= 0: await q.answer("已售罄", show_alert=True); return
        err = await _redeem_execute(context, cid, uid, it)
        if err: await q.answer(err, show_alert=True)
        else: await q.answer("🎉 兑换成功")
        return
    if data.startswith("mall_page_"):
        try: page = int(data[len("mall_page_"):])
        except ValueError: await q.answer(); return
        # 在原按钮消息就地刷新为新页：正文小卡 + 按钮一起重建（_mall_panel 保证与首屏一致）
        items = [x for x in sget("MALL_ITEMS") if x.get("on", True)]
        if not items:
            await q.answer("商城已空"); return
        text, rows = _mall_panel(page, items, q.message.chat.id)
        try:
            await q.message.edit_text(text, parse_mode="HTML",
                                      reply_markup=InlineKeyboardMarkup(rows) if rows else None)
        except Exception:
            pass
        pages = max(1, (len(items) + sget("MALL_PAGE_SIZE") - 1) // sget("MALL_PAGE_SIZE"))
        await q.answer(f"已切到 {page}/{pages} 页")
        return
    return
