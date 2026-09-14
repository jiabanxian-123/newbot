# -*- coding: utf-8 -*-
"""infra/entry —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

from core.entry_buttons import (  # noqa: E402
    _btn_deep_start_confirm, _btn_season_exchange, _btn_lottery_join, _btn_invite_refresh_priv,
    _btn_invite_accept, _btn_join_verify, _btn_invite_refresh, _btn_invreport,
    _btn_fsub_recheck, _btn_noop, _btn_blackjack, _btn_season, _btn_texas, _btn_dice,
    _btn_jinhua, _btn_redeem_show, _btn_redeem_buy, _btn_redpacket_grab,
    _btn_buy_confirm, _btn_mall, _btn_horse_bet,
)
from core.entry_text import text_game_bets

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from datetime import datetime, timedelta, timezone
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
import asyncio
import os
import re
import time

async def cmd_start(update, context):
    """/start 只回一句你好（deep-link 邀请点 START 后发一屏帮助太刷屏）；完整帮助在 /help。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _deep_invite_start = hub._deep_invite_start
    _deep_mall_start = hub._deep_mall_start
    _deep_redeem_start = hub._deep_redeem_start
    _deep_season_exchange_start = hub._deep_season_exchange_start
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    if not await need_auth(update, context): return
    # 私聊深链：邀请 deep-link（t.me/<bot>?start=inv_<邀请人>_<群id>）等在此分流
    _args = context.args or []
    if _args:
        _a0 = _args[0]
        if _a0.startswith("redeem_"):
            await _deep_redeem_start(update, context, _a0); return
        if _a0.startswith("mall_"):
            await _deep_mall_start(update, context, _a0); return
        if _a0.startswith("sexch_"):
            await _deep_season_exchange_start(update, context, _a0); return
        if _a0.startswith("inv_"):
            await _deep_invite_start(update, context, _a0); return
    await send_reply(update, context, "👋 你好！我是娱乐机器人 🎮\n\n发 /help 查看全部功能（游戏 / 积分 / 邀请 / 数据）。")


async def cmd_help(update, context):
    """/help（帮助/菜单）：完整功能帮助；管理员追加管理命令段。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_bot_admin = hub.is_bot_admin
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    if not await need_auth(update, context): return
    text = "🎮 娱乐机器人功能帮助\n\n🎲 发起游戏：\n/开始 或 /help - 查看本帮助\n/德州 - 发起德州扑克（统一积分）\n/赛车 - 发起赛车\n/21点 - 发起21点\n/炸金花 - 发起炸金花（闷牌偷鸡）\n/大话骰 - 发起大话骰（吹牛骰盅，掉骰子制）\n\n💰 积分系统：\n/签到 - 每日签到领积分\n/我的积分 - 积分/等级/签到状态\n/积分排行 - 积分排行榜\n/积分商城 - 用积分换好物\n红包 总数 份数 - 发积分红包（如：红包 1000 5）\n转赠 数量 - 把积分转给群里成员（回复消息用）\n充值 数量 - 申请购买积分（管理员确认到账）\n\n🎟️ 邀请有礼：\n/link - 领取本群专属邀请链接\n今日邀请排行 / 本月邀请排行 / 总邀请排行 - 查看邀请榜\n\n📊 数据查询：\n/盈亏 - 当日盈亏榜\n/排行 - 总积分榜\n流水 - 查自己的积分来源明细（红包/抽水/邀请奖励等；回复他人消息查对方仅限管理员）\n/结束 - 终止当前游戏\n\n🏪 称号商店：\n/商店 - 查看可兑换称号\n/兑换 称号名 - 用积分换称号"
    if is_bot_admin(update.effective_user.id):
        text += "\n\n🔧 管理命令（仅管理员）：\n/授权 - 授权当前群使用\n取消授权 - 取消群授权\n/授权列表 - 查看已授权群\n/加管理员 /减管理员 /管理员列表\n/加积分(负数即减) /赛季分\n/拉黑 /解黑 /黑名单 - 封禁违规玩家\n/列表 - 管理总览(管理员/授权群/黑名单三合一)\n/备份 /恢复\n/同步标签 - 把「积分称号」补同步成 Telegram 成员标签（老玩家标签缺失时用；加「全部」=所有授权群）\n💡 快捷加减分：在群里回复某玩家的消息，然后发「/add 数量」即可给他加/减分（负数即减），不用输ID"
    await send_reply(update, context, text)


async def _parse_target_amount(update, context):
    if len(context.args) >= 2:
        return int(context.args[0]), int(context.args[1])
    if len(context.args) == 1 and update.message.reply_to_message:
        return update.message.reply_to_message.from_user.id, int(context.args[0])
    raise ValueError


async def on_rank_page(update, context):
    """榜单翻页 / 切换榜单：原地编辑同一条消息。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _RANK_GROUPS = hub._RANK_GROUPS
    _RANK_TITLES = hub._RANK_TITLES
    _rank_keyboard = hub._rank_keyboard
    _rank_page_text = hub._rank_page_text
    _rank_rearm = hub._rank_rearm
    safe_edit = hub.safe_edit
    q = update.callback_query
    data = q.data or ""
    m = re.fullmatch(r"rk_([a-z_]+)_(\d+)", data)
    if not m:
        await q.answer(); return
    kind, page = m.group(1), int(m.group(2))
    if kind == "noop":
        await q.answer(); return
    if kind not in _RANK_TITLES:
        await q.answer("该榜已下线", show_alert=True); return
    await q.answer()
    cid = q.message.chat_id
    text, page, pages = await _rank_page_text(context.application, cid, kind, page)
    kb = _rank_keyboard(kind, page, pages, _RANK_GROUPS.get(kind, (kind,)))
    await safe_edit(context.bot, cid, q.message.message_id, text, reply_markup=kb, parse_mode="HTML")
    # 有人翻页 → 回收倒计时重新计时（否则刚点开就被删，体验突兀）
    _rank_rearm(context.application, cid, q.message.message_id)


async def cmd_cx(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    need_auth = hub.need_auth
    send_rank_page = hub.send_rank_page
    if not await need_auth(update, context): return
    await send_rank_page(update, context, "texas_day")


async def cmd_ph(update, context):
    # 2026-09-11 用户要求：榜单太长刷屏（群里 100+ 人），改分页，一页 10 人；
    # 底部按钮可翻页，也可切到「累计盈利榜」。
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    need_auth = hub.need_auth
    send_rank_page = hub.send_rank_page
    if not await need_auth(update, context): return
    await send_rank_page(update, context, "points")


async def cmd_sq(update, context):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    chat_name_cache = hub.chat_name_cache
    is_bot_admin = hub.is_bot_admin
    is_group_chat = hub.is_group_chat
    save_data = hub.save_data
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅 Bot 管理员可操作"); return
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 授权需在群聊中进行：请在目标群里发送 /授权，机器人会把该群加入授权名单。私聊里授权无意义，且会导致游戏开在私聊、别人看不到。")
        return
    cid = update.effective_chat.id
    AUTHORIZED_GROUPS.add(cid); save_data()
    if update.effective_chat.title:
        chat_name_cache[cid] = update.effective_chat.title   # 当场缓存群名，后台不再显示裸 ID
    await send_reply(update, context, f"✅ 当前群已授权：{cid}")


# ══════════════════════════════════════════════════════════════════════════
#  按钮回调**对照表** —— 加一个按钮 = 表里加一行（2026-09-14 表化）
#
#  为什么不再用一长串 if：
#    · 旧结构「加分支 = 在 240 行 if 链里找位置」，容易插错、容易漏 return；
#    · 更阴的是「短前缀排在前 → 长前缀永远轮不到」。真实坑：MAIN 里的
#      `invite_refresh` 正是 PREAUTH 里 `invite_refresh_priv_` 的**前缀** ——
#      顺序一反，私聊刷新按钮就永远没反应，而且不报错、不打日志。
#  顺序即优先级：从上往下第一个命中者赢；撞车由 test_button_table.py 兜底。
#
#  触发词写法：
#    "xxx_"   前缀匹配（data.startswith("xxx_")）
#    "=xxx"   精确匹配（data == "xxx"）—— "=" 开头的 key 不参与前缀匹配
#
#  分两张表的唯一原因：中间夹着「群授权 / 黑名单」守卫。
#  PREAUTH 的表在守卫**之前**跑（私聊回调、未验证者也得点得动）；
#  MAIN 的表在守卫**之后**跑。两段合起来的顺序也必须无撞车。
# ══════════════════════════════════════════════════════════════════════════
CALLBACK_PREAUTH = [
    # 私聊兑换确认回调（redeem/mall ok|no_<cid>_<idx>）：在 bot 私聊里点「确认/取消」触发，
    # 聊天是私聊（cid=用户id），不能用群授权拦截；真实目标群 id 内嵌在 data 里。
    ("redeem_ok_", _btn_deep_start_confirm),
    ("redeem_no_", _btn_deep_start_confirm),
    ("mall_ok_", _btn_deep_start_confirm),
    ("mall_no_", _btn_deep_start_confirm),
    ("sexch_ok_", _btn_deep_start_confirm),
    ("sexch_no_", _btn_deep_start_confirm),
    # 兑换赛季分：无深链兜底（_BOT_USERNAME 为空时面板给的是 callback 按钮）
    ("sexch_ask_", _btn_season_exchange),
    # 抽奖：公告上的「参与抽奖」按钮（2026-09-12 用户报「抽奖没有按钮、不知道怎么参与」）
    ("=lot_join", _btn_lottery_join),
    # 邀请：私聊刷新（面板推送到私聊后，私聊 chat.id 不是群 id，必须在群授权前处理）
    ("invite_refresh_priv_", _btn_invite_refresh_priv),
    # 邀请：主动问兜底按钮 inva_<cid>_<inviter>_<uid>（私聊回调，is_auth 之前处理）
    ("inva_", _btn_invite_accept),
    # 入群验证：按钮选答案 / 一键通过（群授权前处理，未验证者也得能点）
    ("jv_", _btn_join_verify),
]

CALLBACK_MAIN = [
    # 占位按钮（售罄/页码），点了不报错
    ("=noop", _btn_noop),
    # 邀请合格结算：刷新进度（事件驱动兜底重判）
    ("invite_refresh", _btn_invite_refresh),
    # 强制订阅：点「我已加入」立即复检（不等 60 秒负缓存，也不用重发消息）
    ("=fsub_recheck", _btn_fsub_recheck),
    # 21点 回调
    ("bj_", _btn_blackjack),
    ("season_", _btn_season),
    ("texas_", _btn_texas),
    # 大话骰：等待房 + 牌局操作（加入/开始/终止/私看骰子/加码/开骰/刷新）
    ("dice_", _btn_dice),
    ("jh_", _btn_jinhua),
    # 积分商城：点蓝色按钮直接兑换 / 翻页 / 商品详情
    ("mall_buy_", _btn_mall),
    ("mall_show_", _btn_mall),
    ("mall_page_", _btn_mall),
    # 积分兑换：点蓝色商品按钮直接兑换
    ("redeem_show_", _btn_redeem_show),
    ("redeem_buy_", _btn_redeem_buy),
    # 邀请：管理员手动归因（待归因列表按序号点）
    ("invreport_", _btn_invreport),
    ("rp_grab_", _btn_redpacket_grab),
    ("buyok_", _btn_buy_confirm),
    ("buyno_", _btn_buy_confirm),
    ("horsebet_", _btn_horse_bet),
]


def cb_match(table, data):
    """按表找第一个命中的处理函数；没命中返回 None。**顺序即优先级**。

    "=xxx" 精确匹配、"xxx" 前缀匹配（见上方表头注释）。
    """
    for key, handler in table:
        if key.startswith("="):
            if data == key[1:]:
                return handler
        elif data.startswith(key):
            return handler
    return None


async def on_button(update, context):
    """按钮回调总入口：**查表分发**（表见本文件上方 CALLBACK_PREAUTH / CALLBACK_MAIN）。

    三段式，顺序即语义：
      ① CALLBACK_PREAUTH —— 群授权 / 黑名单**之前**就要处理的
         （私聊兑换确认、私聊刷新、邀请确认、入群验证：未授权也得点得动）
      ② 守卫：群授权 → 黑名单
      ③ CALLBACK_MAIN —— 其余全部按钮

    加新按钮 = 表里加一行；顺序即优先级（短前缀排在长前缀前面会把它整个吞掉），
    撞车由 test_button_table.py 挡住。守卫顺序（先授权后黑名单）不要动：
    2026-09-11 之前的顺序反了，未授权群里被封的人反而点得动按钮。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    _bind_update_cid = hub._bind_update_cid
    _remember_name = hub._remember_name
    is_auth = hub.is_auth
    is_bot_admin = hub.is_bot_admin
    logger = hub.logger
    _bind_update_cid(update)
    try:
        q = update.callback_query
        if not q or not q.message:
            if q: await q.answer("该操作已过期", show_alert=True)
            return
        cid, uid, data = q.message.chat.id, q.from_user.id, q.data or ""
        _remember_name(update)
        # ① 授权/黑名单之前：私聊兑换确认、抽奖参与、邀请确认、入群验证
        handler = cb_match(CALLBACK_PREAUTH, data)
        if handler is not None:
            await handler(update, context, q, cid, uid, data)
            return
        # ② 守卫
        if not is_auth(cid): await q.answer("未授权", show_alert=True); return
        if uid in BLACKLISTED_USERS and not is_bot_admin(uid): await q.answer("🚫 你已被禁止使用本机器人", show_alert=True); return
        # ③ 其余全部按钮
        handler = cb_match(CALLBACK_MAIN, data)
        if handler is not None:
            await handler(update, context, q, cid, uid, data)
            return
    except Exception:
        logger.exception("按钮处理异常")
        try:
            await update.callback_query.answer("操作异常，请重试", show_alert=True)
        except Exception:
            pass


async def on_media(update, context):
    """自动删除规则中心：非文本消息（图/视频/贴纸/文件/联系人/系统消息等）按开关静默撤删。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    _autodel_enforce = hub._autodel_enforce
    _bind_update_cid = hub._bind_update_cid
    _forcesub_enforce = hub._forcesub_enforce
    _level_msg_enforce = hub._level_msg_enforce
    _observe_enforce = hub._observe_enforce
    _sensitive_enforce = hub._sensitive_enforce
    is_bot_admin = hub.is_bot_admin
    logger = hub.logger
    _bind_update_cid(update)
    try:
        # 黑名单拦截：命令/文本/回调入口都拦了，媒体消息此前漏了 → 拉黑形同虚设
        u = update.effective_user
        if u and not u.is_bot and u.id in BLACKLISTED_USERS and not is_bot_admin(u.id):
            try: await update.effective_message.delete()
            except Exception: pass
            return
        # 强制订阅：媒体消息（表情包/图片/视频等）也必须拦——此前只在 on_text 拦截，
        # 未订阅的新人发个表情包就能照常聊天（用户报障「只删文字，不删表情包那些」）
        if await _forcesub_enforce(update, context):
            return
        # 观察期：媒体消息同样要拦（同一条漏口，一并收口）
        if await _observe_enforce(update, context):
            return
        if await _autodel_enforce(update, context):
            return
        # 等级消息管控：超出当前等级权限的消息类型撤删（双路径，见 on_media）
        if await _level_msg_enforce(update, context):
            return
        # 敏感词：媒体消息的 caption 同样要查（此前只查文本 → 表情包/图片配文里的敏感词漏网）
        if await _sensitive_enforce(update, context):
            return
    except Exception:
        logger.exception("自动删除(媒体)异常（已吞并）")


# ── 文本消息（on_text）的**群管过滤器链** ────────────────────────────────────
# **顺序即语义**：从上往下，第一个返回 True 的就把这条消息消费掉（后面的全不跑）。
# 加一条新的群管过滤规则 = 表里加一行；调优先级 = 挪行（守卫 test_text_routes.py 钉顺序）。
#
# ⚠️ 表项存的是**名字字符串**、不是函数对象 —— 这 5 个函数都是测试补丁点
#    （test_dice_bid_announce.py 会 `m._forcesub_enforce = fake`），
#    按值存进模块级表会在 import 时固化，补丁就**穿透不了**（那是本仓库最阴的坑）。
#    运行时用 `getattr(hub, 名字)` 查表，与手写 `hub._forcesub_enforce` 等价。
TEXT_GATES = (
    ("forcesub",  "_forcesub_enforce"),    # 强制订阅频道（默认关；管理员豁免）
    ("observe",   "_observe_enforce"),     # 新成员观察期（未满时长发言即删+禁言）
    ("autodel",   "_autodel_enforce"),     # 自动删除规则中心（链接/超长/会员表情）
    ("levelmsg",  "_level_msg_enforce"),   # 等级消息管控（当前等级不允许的消息类型）
    ("sensitive", "_sensitive_enforce"),   # 敏感词过滤（命中即删，可叠加禁言/踢出）
)
# ⚠️ 下面两步**刻意不在上面那张表里**（签名/流程不同，硬塞进去要加适配层，得不偿失）：
#   ① 入群验证答题 `_join_verify_handle_text(context, cid, uid, text)` —— 带自己的
#      try/except，且**必须排在上面 5 条之后**（2026-09-13 用户报障：验证中的人
#      连发违禁词没被处理 —— 先被它消费掉，敏感词/订阅/观察期就全失效了）。
#   ② 定时刷屏识别 —— 带设置开关判断 + 两段调用（先 _antispam_check 拿理由，再 _antispam_hit）。

# 棋盘刷新词：说这些词就把当前群正在进行的游戏面板重画一遍。
# ⚠️ 它们**不能**与 `CMD_ALIASES` 的键重复 —— on_text 里命令别名（先）比刷新词（后）先判，
#    一旦撞车，刷新词**永远轮不到**，而且不报错、不打日志（守卫 test_text_routes.py 挡住）。
BOARD_REFRESH_WORDS = ("棋盘", "刷新", "看棋", "board", "qp")


async def on_text(update, context):
    # 外层 try 包命令分发；开头校验单独内层 try（消息结构异常属噪音，静默忽略）
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    CMD_ALIASES = hub.CMD_ALIASES
    _antispam_check = hub._antispam_check
    _antispam_hit = hub._antispam_hit
    _autoreply_enforce = hub._autoreply_enforce
    _award_chat_points = hub._award_chat_points
    _bind_update_cid = hub._bind_update_cid
    _check_level_change = hub._check_level_change
    _dispatch_alias = hub._dispatch_alias
    _earn_get = hub._earn_get
    _grant_newbie_reward = hub._grant_newbie_reward
    _invite_ping_qualify = hub._invite_ping_qualify
    _join_verify_handle_text = hub._join_verify_handle_text
    _lottery_active = hub._lottery_active
    _refresh_jinhua_table = hub._refresh_jinhua_table
    _remember_name = hub._remember_name
    active_blackjack_games = hub.active_blackjack_games
    active_horse_races = hub.active_horse_races
    active_jinhua_games = hub.active_jinhua_games
    active_poker_games = hub.active_poker_games
    background_tasks = hub.background_tasks
    is_auth = hub.is_auth
    is_bot_admin = hub.is_bot_admin
    is_group_chat = hub.is_group_chat
    logger = hub.logger
    member_profiles = hub.member_profiles
    now_bj = hub.now_bj
    poker_table_text = hub.poker_table_text
    poker_waiting_text = hub.poker_waiting_text
    safe_delete = hub.safe_delete
    safe_send = hub.safe_send
    send_reply = hub.send_reply
    sget = hub.sget
    start_turn_timer = hub.start_turn_timer
    update_blackjack_ui = hub.update_blackjack_ui
    user_names = hub.user_names
    _bind_update_cid(update)
    try:
        # 开头校验：无效消息静默跳过，不打扰用户
        try:
            message, user = update.effective_message, update.effective_user
            if not message or not message.text or not user or user.is_bot: return
            if message.date and (datetime.now(timezone.utc) - message.date).total_seconds() > sget("STALE_TEXT_COMMAND_SECONDS"):
                return
            cid, text = update.effective_chat.id, message.text.strip()
            _remember_name(update)
        except Exception:
            return
        # 拉黑拦截：被封禁用户（非管理员）禁止使用全部功能，连帮助都看不到
        if update.effective_user.id in BLACKLISTED_USERS and not is_bot_admin(update.effective_user.id):
            await send_reply(update, context, "🚫 你已被禁止使用本机器人，如有疑问请联系管理员。"); return

        # ── 群管过滤器链：顺序即语义（表见本文件上方 TEXT_GATES）──
        # 加规则 = 表里加一行；调优先级 = 挪行。运行时 getattr 查表，补丁照样穿透。
        for _gname, _gfn in TEXT_GATES:
            if await getattr(hub, _gfn)(update, context):
                return

        # 入群验证答题：放在过滤之后 —— 图片模式待验证者不禁言，若这里先消费掉发言，
        # 敏感词/订阅/观察期等规则对「正在验证的人」全部失效（用户报截图中的人连发违禁词未被处理）。
        try:
            if await _join_verify_handle_text(context, cid, user.id, text):
                return
        except Exception:
            logger.exception("入群验证答题处理异常（已吞并）")

        # 定时刷屏识别：复读机 + 定时器特征（管理员豁免；内容太短不参与统计防误伤闲聊）
        if (sget("ANTISPAM_ENABLED") and is_group_chat(update) and not is_bot_admin(user.id)
                and len(re.sub(r"\s+", "", text)) >= sget("ANTISPAM_MIN_LEN")):
            try:
                _reason = _antispam_check(cid, user.id, text)
                if _reason:
                    await _antispam_hit(update, context, cid, user.id, _reason)
                    return
            except Exception:
                logger.exception("定时刷屏识别异常（已吞并）")

        # 抽奖触发词：公告宣传的关键词直接参与（支持每个活动自带关键词；修复此前自定义词没反应）
        if is_group_chat(update) and text.strip():
            _alo = _lottery_active(cid)
            if _alo and text.strip() in {sget("LOTTERY_KEYWORD"), (_alo.get("keyword") or "").strip()}:
                await _dispatch_alias("抽奖", [], update, context)
                return

        # 关键词自动回复：排在过滤链+抽奖之后；本群打牌时不插嘴（见 features/autoreply）
        if await _autoreply_enforce(update, context): return
        # 不带 / 的命令直达：若首词是已知命令别名，按命令处理（全部命令均可不带 / 触发）
        _words = text.split()
        if _words and _words[0] in CMD_ALIASES:
            await _dispatch_alias(_words[0], _words[1:], update, context)
            return

        # 深度防御：非命令的游戏交互（下注/落子/加注）仅在授权群内处理，
        # 与 on_button 对齐；命令分发仍在上面由各自 cmd_* 自行校验权限
        if not is_auth(cid):
            return

        # 聊天积分：静默计分，不影响下方游戏文本处理
        try:
            _award_chat_points(cid, user.id, text)
        except Exception:
            logger.exception("聊天积分记账异常（已吞并）")
        # 成员档案：发言即记录（首次见/最后见/消息数）
        try:
            prof = member_profiles[cid][user.id]
            _first_speak = not prof          # 首次发言（档案为空）
            if _first_speak:
                prof.update({"name": user_names.get(user.id, f"用户{user.id}"), "first": now_bj().strftime("%Y-%m-%d %H:%M"), "msgs": 0})
            prof["last"] = now_bj().strftime("%Y-%m-%d %H:%M")
            prof["msgs"] = prof.get("msgs", 0) + 1
            # 新人欢迎奖励：首次发言时发放（用户规则「新人完成入群审核 +200」）
            if _first_speak and not is_bot_admin(user.id):
                _npts, _old = _grant_newbie_reward(cid, user.id,
                                                   user_names.get(user.id) or user.first_name or "")
                if _npts:
                    _app = context.application
                    # 持有引用：裸 create_task 的任务可能被事件循环 GC 掉，升级公告会悄悄丢失
                    background_tasks.add(asyncio.create_task(_check_level_change(
                        _app, cid, user.id, _old, _earn_get(cid, user.id))))
        except Exception:
            logger.exception("成员档案记录异常（已吞并）")
        # 合格邀请结算（事件驱动）：被邀请人在本群发言后即时判定是否达标（发奖/待达标）
        try:
            if sget("INVITE_ENABLED") and is_group_chat(update):
                await _invite_ping_qualify(context.application, cid, user.id)
        except Exception:
            logger.exception("邀请达标判定异常（已吞并）")

        # 统一刷新逻辑
        if text in BOARD_REFRESH_WORDS:
            found = False
            # 1. 21点
            bj = active_blackjack_games.get(cid)
            if bj: found = True; await update_blackjack_ui(bj, context.application)
            # 3. 德州
            poker = active_poker_games.get(cid)
            if poker:
                found = True
                await safe_delete(context.bot, cid, poker.game_msg_id)
                if poker.phase == "waiting":
                    msg = await safe_send(context.bot, cid, await poker_waiting_text(poker, context.application), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 加入游戏", callback_data="texas_join")], [InlineKeyboardButton("❌ 终止房间", callback_data="texas_end")]]))
                    if msg: poker.game_msg_id = msg.message_id
                else:
                    # ⚠️ autodel_keep：这是**德州牌桌正文**（无按钮），必须常驻到本局结束 ——
                    #   2026-09-14 之前靠「调用方函数名 == on_text」在 _AUTODEL_KEEP_FUNCS 里豁免，
                    #   函数一改名牌桌就会被 300 秒删掉。改成发送点自己声明。
                    msg = await safe_send(context.bot, cid, await poker_table_text(poker, context.application),
                                          autodel_keep=True)
                    if msg:
                        poker.game_msg_id = msg.message_id
                        # 恢复当前行动玩家的操作按钮，防止刷新后游戏卡死
                        await start_turn_timer(poker, context.application)
            # 3.5 炸金花：刷新发新界面（仿德州，避免长消息越拉越长）
            jh = active_jinhua_games.get(cid)
            if jh and jh.phase != "waiting":
                found = True
                await _refresh_jinhua_table(jh, context.application)
            # 4. 赛车
            race = active_horse_races.get(cid)
            if race and race.phase == "betting":
                found = True
                await safe_delete(context.bot, cid, race.game_msg_id)
                msg = await safe_send(context.bot, cid, await race.view(context.application), reply_markup=race.buttons())
                if msg: race.game_msg_id = msg.message_id
            if not found: await send_reply(update, context, "💡 当前没有任何正在进行的游戏。")
            return

        # 下注 / 游戏文本交互（21点加入、赛车下注、扑克加注/全下、大话骰叫牌）
        # 2026-09-13 搬去 core/entry_text/bets.py，逻辑未动
        await text_game_bets(update, context, cid, user, text, message)
    except Exception:
        logger.exception("文本指令处理异常")
        # 命令分发异常不再静默：给用户明确反馈，便于排查而非毫无反应
        try:
            await send_reply(update, context, "⚠️ 指令处理出错，请联系管理员。")
        except Exception:
            pass


async def cmd_record(update, context):
    """个人战绩：本群四游戏累计盈亏汇总。用法：战绩 / 回复成员消息发「战绩」/「战绩 用户ID」。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    blackjack_profit_by_date = hub.blackjack_profit_by_date
    game_chips = hub.game_chips
    get_name = hub.get_name
    is_group_chat = hub.is_group_chat
    jinhua_profit_by_date = hub.jinhua_profit_by_date
    need_auth = hub.need_auth
    poker_profit_by_date = hub.poker_profit_by_date
    race_profit_by_date = hub.race_profit_by_date
    schedule_delete = hub.schedule_delete
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    if not is_group_chat(update):
        await send_reply(update, context, "⚠️ 战绩请在群聊中查看。"); return
    cid, uid = update.effective_chat.id, update.effective_user.id
    args = context.args or []
    reply = update.message.reply_to_message
    if reply is not None and reply.from_user and not reply.from_user.is_bot:
        target = reply.from_user.id
    elif args and args[0].lstrip("-").isdigit():
        target = int(args[0])
    else:
        target = uid
    per, days = {}, set()
    for label, prof in (("🃏 德州", poker_profit_by_date), ("🏎️ 赛车", race_profit_by_date),
                        ("♠️ 21点", blackjack_profit_by_date), ("♣️ 炸金花", jinhua_profit_by_date)):
        total = 0
        for d, chats in prof.items():
            v = (chats.get(cid) or {}).get(target)
            if v: total += v; days.add(d)
        per[label] = total
    balance = game_chips[cid].get(target, 0)
    name = await get_name(context.application, target, cid=cid)
    lines = [f"📊 {name} 的战绩（本群）", "━━━━━━━━━━━━"]
    for label, total in per.items():
        lines.append(f"{label}：{total:+d}")
    lines.append("━━━━━━━━━━━━")
    lines.append(f"💰 累计：{sum(per.values()):+d}")
    lines.append(f"📅 活跃 {len(days)} 天｜💳 当前余额 {balance}")
    reply_msg = await send_reply(update, context, "\n".join(lines))
    schedule_delete(context.application, cid, reply_msg, sget("REPLY_DELETE_SECONDS"))


async def cmd_status(update, context):
    """机器人自检（管理员）：运行时长/各游戏活跃局/台账/数据文件/调度任务。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BOT_BOOT_TS = hub.BOT_BOOT_TS
    DATA_FILE = hub.DATA_FILE
    active_blackjack_games = hub.active_blackjack_games
    active_horse_races = hub.active_horse_races
    active_jinhua_games = hub.active_jinhua_games
    active_poker_games = hub.active_poker_games
    background_tasks = hub.background_tasks
    data_size_kb = hub.data_size_kb
    is_bot_admin = hub.is_bot_admin
    ledger = hub.ledger
    rp_packets = hub.rp_packets
    send_reply = hub.send_reply
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅机器人管理员可用。"); return
    uptime = int(time.time() - BOT_BOOT_TS)
    uptime_txt = f"{uptime // 86400}天{uptime % 86400 // 3600}小时{uptime % 3600 // 60}分"
    # Q9：数据拆成 6 个分片后，直接量旧 DATA_FILE 会显示过期大小
    try: dsz = data_size_kb()
    except OSError: dsz = "无"
    lines = [
        "🩺 机器人自检", "━━━━━━━━━━━━",
        f"⏱ 运行时长：{uptime_txt}",
        f"🃏 德州进行中：{len(active_poker_games)} 局",
        f"♠️ 21点进行中：{len(active_blackjack_games)} 局",
        f"♣️ 炸金花进行中：{len(active_jinhua_games)} 局",
        f"🏎️ 赛车进行中：{len(active_horse_races)} 场",
        f"🧧 未结算红包：{len(rp_packets)} 个",
        f"📒 资金流台账：{len(ledger)} 条",
        f"💾 数据文件：{dsz}｜后台任务：{len(background_tasks)} 个",
    ]
    await send_reply(update, context, "\n".join(lines))


async def post_init(app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    SETTINGS_SNAPSHOT = hub.SETTINGS_SNAPSHOT
    TG_MENU = hub.TG_MENU
    _flush_deletes = hub._flush_deletes
    _warm_group_names = hub._warm_group_names
    background_tasks = hub.background_tasks
    daily_reset_scheduler = hub.daily_reset_scheduler
    data_save_worker = hub.data_save_worker
    hourly_race_scheduler = hub.hourly_race_scheduler
    install_autodelete_default = hub.install_autodelete_default
    leaderboard_scheduler = hub.leaderboard_scheduler
    logger = hub.logger
    lottery_scheduler = hub.lottery_scheduler
    now_bj = hub.now_bj
    poker_watchdog = hub.poker_watchdog
    restore_pending_deletes = hub.restore_pending_deletes
    season_settle_scheduler = hub.season_settle_scheduler

    hub.set_many(("_bot_app", "_bot_loop"), (app, asyncio.get_running_loop()))  # 供网页后台跨线程调用 bot API（入群批准/拒绝等）
    # 【必须最先做】打上「默认自动删除」补丁：必须早于本函数里任何一次发送，
    # 否则 post_init 期间发出的消息不受默认回收管辖（2026-09-11 用户第 N 次追问后根治）。
    install_autodelete_default(app)
    # 兑换按钮深链需要 bot 用户名（https://t.me/<用户名>?start=...）；启动时缓存
    try:
        _me = await app.bot.get_me()
        hub.set("_BOT_USERNAME", (_me.username or "").lstrip("@"))
    except Exception:
        hub.set("_BOT_USERNAME", "")
    background_tasks.update({
        asyncio.create_task(daily_reset_scheduler(app)),
        asyncio.create_task(leaderboard_scheduler(app)),
        asyncio.create_task(season_settle_scheduler(app)),
        asyncio.create_task(hourly_race_scheduler(app)),
        asyncio.create_task(lottery_scheduler(app)),
        asyncio.create_task(poker_watchdog(app)),
        asyncio.create_task(data_save_worker())
    })
    background_tasks.add(asyncio.create_task(_warm_group_names(app)))   # 群名预热：网页/推送不再显示裸群ID
    # 待删消息队列：重放上次没删完的（重部署不再残留游戏面板）+ 每 60 秒兜底 flush 一轮
    try:
        restore_pending_deletes(app)
        async def _delete_sweeper():
            while True:
                await asyncio.sleep(60)
                try: await hub._flush_deletes(app)
                except Exception: pass
        background_tasks.add(asyncio.create_task(_delete_sweeper()))
    except Exception:
        logger.exception("待删消息队列恢复失败（不影响主功能）")
    # 注册 Telegram 原生命令菜单（仅支持拉丁字符命令，中文命令走自定义路由）。
    # 作用：群里打 / 能看到、能点；命令以 bot_command 实体发送，不受隐私模式影响，必定送达。
    # 菜单内容在「命令管理」页可改，存 bot_settings.json 的 tg_menu。
    try:
        menu = [BotCommand(c, d) for c, d in TG_MENU]
        await app.bot.set_my_commands(menu)
    except Exception:
        logger.warning("注册命令菜单失败（不影响主功能）")
    # 全新部署检测：容器重建会清空 bot_settings.json，数据里也没有快照时，
    # 主动私聊提醒管理员恢复设置，避免「设置莫名回退成默认值」却没人知道。
    try:
        if not SETTINGS_SNAPSHOT:
            await app.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text="⚠️ 检测到全新部署：网页后台的设置已回退为代码默认值。\n\n"
                     "恢复方法（二选一）：\n"
                     "1️⃣ 回复最近一份「⚙️ 网页设置备份」文件 → 发送 /restore\n"
                     "2️⃣ 回复「🤖 每日自动备份」数据文件 → 发送 /restore（设置已内嵌在数据里）\n\n"
                     "要重新配置的话，忽略本条即可。",
            )
    except Exception:
        logger.warning("全新部署提醒发送失败（不影响运行）")


async def post_shutdown(app):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    force_save_now = hub.force_save_now
    force_save_now()


async def _dispatch_alias(cmd, args, update, context):
    """根据命令别名（无论带不带 /）分发到对应处理函数，并填充 context.args。
    命中已知命令后，按 POINTS_DELETE_SECONDS 自动删除用户发的命令消息（全局，群聊限定）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    CMD_ALIASES = hub.CMD_ALIASES
    is_group_chat = hub.is_group_chat
    schedule_delete = hub.schedule_delete
    send_reply = hub.send_reply
    sget = hub.sget
    handler = CMD_ALIASES.get(cmd)
    if not handler:
        await send_reply(update, context, "❓ 未知命令，发送 /开始 查看可用命令")
        return
    context.args = args
    # 2026-09-12 用户报「抽奖完那句话也不删除」→ 取消抽奖消息的豁免，一律按 POINTS_DELETE_SECONDS
    #   回收（抽奖**公告本身**与**开奖结果**各自走 autodel_keep 永久保留，不受这里影响）。
    if sget("POINTS_DELETE_SECONDS") > 0 and is_group_chat(update):
        schedule_delete(context.application, update.effective_chat.id, update.message, sget("POINTS_DELETE_SECONDS"))
    await handler(update, context)


async def route_command(update, context):
    """把 /中文 或 /英文 命令路由到对应处理函数。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    _bind_update_cid = hub._bind_update_cid
    _dispatch_alias = hub._dispatch_alias
    _remember_name = hub._remember_name
    is_bot_admin = hub.is_bot_admin
    send_reply = hub.send_reply
    _bind_update_cid(update)
    if not update.message or not update.message.text:
        return
    _remember_name(update)
    # 拉黑拦截：被封禁用户（非管理员）禁止使用全部命令
    if update.effective_user.id in BLACKLISTED_USERS and not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "🚫 你已被禁止使用本机器人，如有疑问请联系管理员。"); return
    parts = update.message.text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return
    cmd = parts[0][1:]
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    await _dispatch_alias(cmd, parts[1:], update, context)


async def on_app_error(update, context):
    """全局错误兜底：任何 handler 抛出的未捕获异常都会进入这里。

    记录完整堆栈日志，给当事人一条可感知的提示（不再静默失败），并推送管理员。
    网络类错误（httpx.ReadError / 平台抖动 / 容器重启瞬断）会自动重连，属无害噪音，
    默认只写日志，5 分钟窗口内累计 3 次以上才私聊管理员，避免刷屏。
    自身全程吞异常——错误处理器绝不能二次抛错。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    _is_network_error = hub._is_network_error
    _net_err_log = hub._net_err_log
    _net_error_tick = hub._net_error_tick
    logger = hub.logger
    err = context.error
    logger.exception("未处理异常", exc_info=err)
    net = _is_network_error(err)
    if net:
        hit = _net_error_tick()
        if not hit:
            logger.warning("网络类异常（自动重连，未打扰管理员）：%r（5分钟内第 %d 次）", err, len(_net_err_log))
            return
    try:
        chat = getattr(update, "effective_chat", None) if update is not None else None
        if chat is not None and not net:
            await context.bot.send_message(chat.id, "⚠️ 处理该操作时出错，请稍后重试；已通知管理员排查。")
    except Exception:  # silent-ok: 错误处理器自身绝不能二次抛错：提示发不出也不能再抛
        pass
    try:
        if net:
            await context.bot.send_message(
                ADMIN_USER_ID,
                f"⚠️ 网络异常持续发生（5 分钟内第 {len(_net_err_log)} 次）：{err!r}\n"
                "多为平台网络抖动或容器重启，bot 一般会自动重连；若群内命令也无反应，请检查平台实例状态。")
        else:
            await context.bot.send_message(ADMIN_USER_ID, f"⚠️ bot 发生未处理异常：{err!r}")
    except Exception:  # silent-ok: 错误处理器自身绝不能二次抛错：提示发不出也不能再抛
        pass
