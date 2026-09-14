# -*- coding: utf-8 -*-
"""③ 等级与消息权限

   「等级消息管控」（按等级限制可发的消息类型 + 违规惩罚）。

· 等级判定**只有一个入口** `_level_base`（`LEVEL_KEEP_ON_SPEND` 开 → 累计积分；关 → 当前余额）。
  `_level_of` / `_level_item` / `_level_perms` 全走它，不许各写一套。
· `_check_level_change` **只管升级**（累计减少直接 return，历史测试锁死）；
  降级走 `_check_level_drop_on_spend`（仅在「消费不掉级」关掉时生效）。
· `_normalize_levels` 的「缺 `perms` 补默认 / 空串保留」分支区别是踩过的坑：
  空串 = 用户显式一个都没勾 = **全部禁止**，不许补默认。
· `_level_msg_enforce` 必须同时接 `on_text` 与 `on_media`（双路径铁律）；
  消息删除要用 `except Exception` 并 fallback 到 `bot.delete_message`。
"""

from core import hub

from telegram import ChatPermissions
import html, time


def _normalize_levels():
    """等级表结构归一（2026-09-09 用户需求）：旧存档 {name,value} → 补齐 perms/on。

    兼容原则：**旧数据默认全放行**（perms=LEVEL_PERM_DEFAULT, on=1），
    否则升级后老用户会突然被拦（历史无权限概念，不能追溯处罚）。
    幂等：可重复调用。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    LEVEL_PERM_DEFAULT = hub.LEVEL_PERM_DEFAULT
    LEVEL_PERM_OPTIONS = hub.LEVEL_PERM_OPTIONS
    sget = hub.sget
    for it in sget("POINT_LEVELS"):
        if not isinstance(it, dict):
            continue
        if "perms" not in it or not isinstance(it.get("perms"), str):
            # 旧存档缺字段 → 兼容放行（不能追溯处罚老用户）
            it["perms"] = LEVEL_PERM_DEFAULT
        else:
            # 空串是用户在网页上显式「一个都不勾」= 全部禁止，必须保留（不能补默认）
            picked = {p.strip() for p in it["perms"].split(",") if p.strip()}
            it["perms"] = ",".join(k for k, _v in LEVEL_PERM_OPTIONS if k in picked)
        if "on" not in it:
            it["on"] = 1
        else:
            it["on"] = 1 if str(it.get("on")).strip().lower() in ("1", "true", "on", "yes", "是") else 0


def _set_level_enabled(v):
    """开关积分等级系统（测试与网页共用入口）。"""

    hub.set("LEVEL_ENABLED", 1 if str(v).strip().lower() in ("1", "true", "on", "yes", "是") else 0)
    return hub.LEVEL_ENABLED


def _level_base(cid, uid):
    """等级判定基数。`LEVEL_KEEP_ON_SPEND`（消费不掉级）关 → 当前余额；开 → 累计积分。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _earn_get = hub._earn_get
    game_chips = hub.game_chips
    sget = hub.sget
    if not sget("LEVEL_KEEP_ON_SPEND"):
        # 关掉「消费不掉级」= 允许降级：按当前余额算，花掉就掉级
        try:
            return max(0, int(game_chips.get(cid, {}).get(uid, sget("GAME_STARTING_CHIPS")) or 0))
        except Exception:
            return 0
    return _earn_get(cid, uid)


def _level_item(cid, uid):
    """返回该用户当前命中的等级 dict（跳过停用等级）；未达最低等级返回 None。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _level_base = hub._level_base
    sget = hub.sget
    base = _level_base(cid, uid)
    cur = None
    for it in sget("POINT_LEVELS"):
        if not int(it.get("on", 1) or 0):
            continue   # 停用等级不参与判定（与 _get_level 口径一致）
        if base >= int(it.get("value", 0) or 0):
            cur = it
    return cur


def _level_of(cid, uid):
    """返回 (等级名, 用于判定的数值)。等级判定唯一口径。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _get_level = hub._get_level
    _level_base = hub._level_base
    base = _level_base(cid, uid)
    return _get_level(base), base


def _get_level(balance):
    """按积分等级表返回当前等级名，表为空返回空串。停用的等级不参与判定。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    lv = ""
    for item in sget("POINT_LEVELS"):
        if not int(item.get("on", 1) or 0):
            continue
        if balance >= int(item.get("value", 0) or 0):
            lv = item["name"]
    return lv


def _level_rank(lv_name):
    """等级名 -> 在等级表中的序号（升序），未找到返回 -1。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    for i, item in enumerate(sget("POINT_LEVELS")):
        if item["name"] == lv_name:
            return i
    return -1


def _level_perms(cid, uid):
    """取该用户当前等级允许的消息类型集合。未达最低等级/表为空 → 返回全部（放行）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    LEVEL_PERM_DEFAULT = hub.LEVEL_PERM_DEFAULT
    LEVEL_PERM_OPTIONS = hub.LEVEL_PERM_OPTIONS
    _level_item = hub._level_item
    sget = hub.sget
    if not sget("POINT_LEVELS"):
        return set(k for k, _v in LEVEL_PERM_OPTIONS)
    cur = _level_item(cid, uid)
    if cur is None:
        return set(k for k, _v in LEVEL_PERM_OPTIONS)   # 未达最低等级：放行，不惩罚新人
    raw = str(cur.get("perms") if cur.get("perms") is not None else LEVEL_PERM_DEFAULT)
    return {p.strip() for p in raw.split(",") if p.strip()}


def _level_allows(cid, uid, kind):
    """该用户当前等级是否允许发送某类消息。总开关关/表空/未达最低等级 → 一律放行。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _level_perms = hub._level_perms
    sget = hub.sget
    if not sget("LEVEL_ENABLED") or not sget("LEVEL_MSG_GUARD_ENABLED"):
        return True
    if not sget("POINT_LEVELS"):
        return True
    return str(kind) in _level_perms(cid, uid)


def _level_msg_hit(cid, uid, message, text=""):
    """等级消息管控判定。返回被拦截的消息类型键，放行返回 None。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _level_allows = hub._level_allows
    _msg_kind = hub._msg_kind
    is_bot_admin = hub.is_bot_admin
    sget = hub.sget
    if not sget("LEVEL_ENABLED") or not sget("LEVEL_MSG_GUARD_ENABLED"):
        return None
    if not sget("POINT_LEVELS"):
        return None
    if is_bot_admin(uid):
        return None
    kind = _msg_kind(message, text)
    if _level_allows(cid, uid, kind):
        return None
    return kind


def _level_violation_hit(cid, uid):
    """记一次等级消息违规，返回 (窗口内次数, 是否达惩罚阈值)。

    窗口与阈值来自网页配置（LEVEL_MSG_WINDOW / LEVEL_MSG_MAX_HITS）。
    达阈值后清空窗口，避免「到阈值后每发一条都触发一次惩罚」。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    level_msg_violations = hub.level_msg_violations
    sget = hub.sget
    now = time.time()
    win = max(1, int(sget("LEVEL_MSG_WINDOW")))
    lst = [t for t in (level_msg_violations.get((cid, uid)) or []) if now - t <= win]
    lst.append(now)
    level_msg_violations[(cid, uid)] = lst[-50:]
    n = len(lst)
    if n >= max(1, int(sget("LEVEL_MSG_MAX_HITS"))):
        level_msg_violations.pop((cid, uid), None)
        return n, True
    return n, False


async def _level_msg_enforce(update, context):
    """等级消息管控执行：超出等级权限的消息撤删 + 违规计数 + 达阈值惩罚。

    返回 True 表示已处理（调用方应停止后续处理）。
    双路径接入：on_text 与 on_media 都要调（媒体消息走 on_media）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    LEVEL_PERM_NAMES = hub.LEVEL_PERM_NAMES
    _fmt_tpl = hub._fmt_tpl
    _level_msg_hit = hub._level_msg_hit
    _level_of = hub._level_of
    _level_violation_hit = hub._level_violation_hit
    _mod_punish = hub._mod_punish
    is_bot_admin = hub.is_bot_admin
    is_group_chat = hub.is_group_chat
    logger = hub.logger
    schedule_delete = hub.schedule_delete
    sget = hub.sget
    user_names = hub.user_names
    try:
        if not sget("LEVEL_ENABLED") or not sget("LEVEL_MSG_GUARD_ENABLED"):
            return False
        user, message = update.effective_user, update.effective_message
        if not message or not user or user.is_bot or not is_group_chat(update):
            return False
        if is_bot_admin(user.id):
            return False
        cid = update.effective_chat.id
        if cid not in AUTHORIZED_GROUPS:
            return False
        text = message.text or message.caption or ""
        kind = _level_msg_hit(cid, user.id, message, text)
        if not kind:
            return False
        try:
            await message.delete()
        except Exception:
            # 生产上是 TelegramError（无删除权限等）；测试桩/异常结构也不许中断管控流程
            try:
                await context.bot.delete_message(cid, message.message_id)
            except Exception:
                pass
        name = user.first_name or user_names.get(user.id) or f"用户{user.id}"
        lv = _level_of(cid, user.id)[0] or "无"
        n, over = _level_violation_hit(cid, user.id)
        if over and int(sget("LEVEL_MSG_PUNISH")):
            secs = int(sget("LEVEL_MSG_MUTE_SECONDS"))
            if int(sget("LEVEL_MSG_PUNISH")) == 1:
                if secs <= 0:
                    return True          # 0=不禁言（只删消息 + 已发提示）
                if secs < 30:
                    # 用户口径：小于 30 秒 = 永久禁言。_mod_punish 有 max(30,..) 下限，
                    # 无法表达「永久」，这里直接 restrict 且不带 until_date。
                    try:
                        await context.bot.restrict_chat_member(
                            cid, user.id, permissions=ChatPermissions(can_send_messages=False))
                    except Exception:
                        logger.exception("等级消息管控：永久禁言失败 cid=%s uid=%s（已吞并）", cid, user.id)
                    mute_txt = _fmt_tpl("level_msg_mute_tpl", name=html.escape(str(name)), seconds="永久")
                else:
                    await _mod_punish(context, cid, user.id, 1, secs, name, "等级消息越权")
                    mute_txt = _fmt_tpl("level_msg_mute_tpl", name=html.escape(str(name)), seconds=str(secs))
                try:
                    await context.bot.send_message(cid, mute_txt)
                except Exception:
                    pass
            else:
                await _mod_punish(context, cid, user.id, 2, 0, name, "等级消息越权")
            return True
        # 未达惩罚阈值：发一次违规提示（按 REPLY_DELETE_SECONDS 自动回收，防刷屏）
        try:
            tip = await context.bot.send_message(
                cid, _fmt_tpl("level_msg_warn_tpl", name=html.escape(str(name)),
                              level=html.escape(str(lv)),
                              kind=html.escape(str(LEVEL_PERM_NAMES.get(kind, kind))),
                              count=str(n), limit=str(int(sget("LEVEL_MSG_MAX_HITS")))))
            if sget("REPLY_DELETE_SECONDS") > 0:
                schedule_delete(context.application, cid, tip, sget("REPLY_DELETE_SECONDS"))
        except Exception:
            pass
        return True
    except Exception:
        logger.exception("等级消息管控异常（已吞并）")
        return False


async def _level_sync_member_tag(app, cid, uid, level_name=None, return_reason=False):
    """把积分称号同步成 Telegram 成员标签（群昵称后面那个小标识）。

    返回 True/False；`return_reason=True` 时返回 `(ok, 说明)` 供排障提示。
    失败**绝不抛**：权限不足 / 群不支持 都不影响等级系统主流程。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _level_of = hub._level_of
    _tag_call = hub._tag_call
    _tag_err_hint = hub._tag_err_hint
    _tag_sanitize = hub._tag_sanitize
    logger = hub.logger
    sget = hub.sget
    try:
        if not sget("LEVEL_SYNC_TAG"):
            return (False, "后台「积分称号同步成员标签」开关是关的") if return_reason else False
        lv = level_name if level_name is not None else _level_of(cid, uid)[0]
        tag = _tag_sanitize(lv)
        if not tag:
            return (False, "未达任何等级（或等级名全是 emoji）") if return_reason else False
        ok, info, _wait = await _tag_call(app.bot, cid, uid, tag)
        return (ok, info) if return_reason else ok
    except Exception as exc:  # silent-ok: 最外层双保险（_tag_call 内部已保证不抛），非实际失败路径
        logger.debug("同步成员标签异常：cid=%s uid=%s", cid, uid, exc_info=True)
        return (False, _tag_err_hint(exc)) if return_reason else False


async def _check_level_change(app, cid, uid, old_earned, new_earned, balance=None):
    """「累计积分」变动后检查**升级**并发群内通知（LEVEL_NOTIFY_ENABLED 控制）。

    old_earned/new_earned 是**累计积分**（不是余额）——花积分不会掉级，所以消费点
    不该再调本函数（此前兑换/商城误传余额，导致花分就发降级公告）。
    本函数**只发升级公告**，减少/异常输入一律静默（降级走 _check_level_drop_on_spend）。

    接入点：签到 / 管理员加分 / 转赠收款 / 红包领取 / 邀请奖励 / 游戏结算 / 归零赠送。
    聊天积分小额高频，刻意不接（避免刷屏）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fmt_tpl = hub._fmt_tpl
    _get_level = hub._get_level
    _level_sync_member_tag = hub._level_sync_member_tag
    game_chips = hub.game_chips
    get_name = hub.get_name
    logger = hub.logger
    send_settle = hub.send_settle
    sget = hub.sget
    try:
        if not sget("POINT_LEVELS"):
            return
        old_v, new_v = int(old_earned or 0), int(new_earned or 0)
        if new_v <= old_v:
            return   # 只可能升不可能降（累计账本只增）；减少=异常输入，不公告
        old_lv, new_lv = _get_level(old_v), _get_level(new_v)
        if old_lv == new_lv:
            return
        await _level_sync_member_tag(app, cid, uid, new_lv)   # 称号同步标签（开关控制）
        if not sget("LEVEL_NOTIFY_ENABLED"):
            return
        name = await get_name(app, uid, cid=cid)
        # ★ game_chips 是 defaultdict，裸下标 [cid][uid] 会给「不在本群/没钱包」的人
        #   **凭空建一个 0 分键** → 出现在积分榜和 CSV 导出里。改用 .get。
        show_bal = game_chips.get(cid, {}).get(uid, 0) if balance is None else balance
        await send_settle(app, cid, _fmt_tpl("level_up_msg_tpl", name=name, level=new_lv, balance=show_bal))
    except Exception:
        logger.exception("等级变动通知失败（已忽略）")


async def _check_level_drop_on_spend(app, cid, uid, before_balance):
    """扣分后检查是否因余额下降而**降级**并发通知。

    仅在「消费不掉级」开关（`LEVEL_KEEP_ON_SPEND`）**关闭**时生效——默认开着时
    等级按累计积分判定，花分不掉级，本函数直接返回（零开销）。
    在扣分点调用，传扣分前的余额。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fmt_tpl = hub._fmt_tpl
    _get_level = hub._get_level
    _level_sync_member_tag = hub._level_sync_member_tag
    game_chips = hub.game_chips
    get_name = hub.get_name
    logger = hub.logger
    send_settle = hub.send_settle
    sget = hub.sget
    try:
        if sget("LEVEL_KEEP_ON_SPEND") or not sget("POINT_LEVELS"):
            return
        old_v = max(0, int(before_balance or 0))
        new_v = max(0, int(game_chips[cid][uid] or 0))
        if new_v >= old_v:
            return
        old_lv, new_lv = _get_level(old_v), _get_level(new_v)
        if old_lv == new_lv:
            return
        await _level_sync_member_tag(app, cid, uid, new_lv)
        if not sget("LEVEL_DOWN_NOTIFY_ENABLED"):
            return
        name = await get_name(app, uid, cid=cid)
        await send_settle(app, cid, _fmt_tpl("level_down_msg_tpl", name=name,
                                             level=new_lv or "无", balance=new_v))
    except Exception:
        logger.exception("扣分降级检查失败（已忽略）")


async def cmd_my_level(update, context):
    """查询我的积分等级与距下一级的差距。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _fmt_tpl = hub._fmt_tpl
    _level_of = hub._level_of
    game_chips = hub.game_chips
    get_name = hub.get_name
    need_auth = hub.need_auth
    send_reply = hub.send_reply
    sget = hub.sget
    if not await need_auth(update, context): return
    cid, uid = update.effective_chat.id, update.effective_user.id
    if not sget("POINT_LEVELS"):
        await send_reply(update, context, _fmt_tpl("level_query_none_tpl")); return
    bal = game_chips.get(cid, {}).get(uid, sget("GAME_STARTING_CHIPS"))
    lv, base = _level_of(cid, uid)
    next_lv, next_val = "", None
    for item in sget("POINT_LEVELS"):
        if not int(item.get("on", 1) or 0):
            continue
        if int(item.get("value", 0) or 0) > base and (next_val is None or item["value"] < next_val):
            next_lv, next_val = item["name"], int(item["value"])
    nxt = f"\n⬆️ 下一等级：{next_lv}（还差 {next_val - base} 积分）" if next_lv else "\n🏆 你已是最高等级！"
    base_line = ("📈 累计积分：{v}（等级按此计算，消费不降级）".format(v=base)
                 if sget("LEVEL_KEEP_ON_SPEND") else "📈 等级按当前积分计算（积分不足会降级）")
    await send_reply(update, context,
                     _fmt_tpl("level_query_msg_tpl", name=await get_name(context.application, uid),
                              level=lv or "无", balance=bal, earned=base,
                              base_line=base_line, next_line=nxt))
