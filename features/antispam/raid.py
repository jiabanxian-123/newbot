# -*- coding: utf-8 -*-
"""⑥ 防突袭人墙 + 新人观察期

· 防突袭：窗口内进群人数达 RAID_THRESHOLD → 自动开启「人墙」（新人一律先验证），
  持续 RAID_COOLDOWN 秒后自动解除。RAID_WINDOW 是检测窗口。
· 新人观察期：入群未满 OBSERVE_SECONDS 一律禁言；到期巡检 `observe_check_sweep`
  找出「零/少发言」的成员按 OBSERVE_CHECK_ACTION 处理（0=提醒管理员 1=禁言 2=踢出）。
"""

from core import hub

from datetime import datetime, timedelta, timezone
from telegram import ChatPermissions
from telegram.error import TelegramError
import html, time


async def _observe_enforce(update, context):
    """新成员观察期：入群未满观察时长的成员发言即删并禁言至期满。返回 True=已拦截。

    文本与媒体消息共用（此前只在 on_text 拦截 → 观察期内发个表情包就能照常发言）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_bot_admin = hub.is_bot_admin
    is_group_chat = hub.is_group_chat
    member_joined_at = hub.member_joined_at
    sget = hub.sget
    if not (sget("OBSERVE_ENABLED") and sget("OBSERVE_SECONDS") > 0):
        return False
    user, message = update.effective_user, update.effective_message
    if not message or not user or user.is_bot:
        return False
    if not is_group_chat(update) or is_bot_admin(user.id):
        return False
    cid = update.effective_chat.id
    _jt = member_joined_at.get(cid, {}).get(user.id, 0)
    _elapsed = time.time() - _jt if _jt else 1e9
    if _elapsed >= sget("OBSERVE_SECONDS"):
        return False
    try:
        await context.bot.delete_message(chat_id=cid, message_id=message.message_id)
        await context.bot.restrict_chat_member(
            cid, user.id, permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now(timezone.utc) + timedelta(seconds=sget("OBSERVE_SECONDS") - _elapsed + 1))
    except TelegramError:
        pass
    return True


def _raid_active(cid):
    """突袭人墙是否生效中（人墙期间新人一律强制走验证禁言流程）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    raid_until = hub.raid_until
    sget = hub.sget
    return bool(sget("RAID_ENABLED")) and float(raid_until.get(cid, 0) or 0) > time.time()


async def _raid_recover(context, cid):
    """人墙到期自动解除（有人进群/巡检触发时惰性检查）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    raid_until = hub.raid_until
    until = float(raid_until.get(cid, 0) or 0)
    if until and time.time() >= until:
        raid_until.pop(cid, None)
        try:
            await context.bot.send_message(cid, "✅ 突袭警戒解除，入群恢复正常。")
        except Exception:
            pass


async def _raid_on_join(context, cid, uid=0):
    """防突袭：滑窗计数进群人数；超阈值 → 临时人墙（期间新人强制验证禁言），到期自动解除。

    去重：同一次进群会同时到 chat_member 与服务消息两个事件源，若按事件计数，阈值实际被腰斩。
    uid 相同且在窗口内的重复上报只计一次。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    _raid_recover = hub._raid_recover
    logger = hub.logger
    raid_counted = hub.raid_counted
    raid_joins = hub.raid_joins
    raid_until = hub.raid_until
    sget = hub.sget
    if not sget("RAID_ENABLED"):
        return
    try:
        await _raid_recover(context, cid)
    except Exception:
        logger.exception("防突袭恢复检查异常（已吞并）")
    now = time.time()
    if uid:
        ckey = f"{cid}:{uid}"
        last = float(raid_counted.get(ckey, 0) or 0)
        if last and now - last < max(30, int(sget("RAID_WINDOW"))):
            return          # 同一个人同一次进群的第二个事件源，不重复计数
        raid_counted[ckey] = now
        if len(raid_counted) > 2000:
            for k in [k for k, t in raid_counted.items() if now - float(t) > 3600]:
                raid_counted.pop(k, None)
    arr = [t for t in raid_joins.get(cid, []) if now - float(t) < int(sget("RAID_WINDOW"))]
    arr.append(now)
    raid_joins[cid] = arr[-300:]
    if len(arr) >= max(2, int(sget("RAID_THRESHOLD"))) and not raid_until.get(cid):
        raid_until[cid] = now + max(60, int(sget("RAID_COOLDOWN")))
        try:
            await context.bot.send_message(
                cid, f"🚨 检测到疑似突袭（{int(sget('RAID_WINDOW'))} 秒内 {len(arr)} 人进群），"
                     f"已临时开启人墙：新人进群需先完成验证，{int(sget('RAID_COOLDOWN'))} 秒后自动恢复。")
        except Exception:
            pass
        try:
            await context.bot.send_message(
                ADMIN_USER_ID, f"🚨 防突袭：群 <code>{cid}</code> {int(sget('RAID_WINDOW'))} 秒内 {len(arr)} 人进群，已临时人墙。")
        except Exception:
            pass


async def observe_check_sweep(context):
    """观察期到期巡检：观察期走完的人复核一次，发言不达标（可选：无头像）→ 提醒/禁言/踢出。

    只在 OBSERVE_CHECK_ENABLED 打开时干活；处理过的人记进 observe_checked，不会反复骚扰。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    _CUR_CID = hub._CUR_CID
    _mod_punish = hub._mod_punish
    _safe_cid = hub._safe_cid
    group_get = hub.group_get
    is_bot_admin = hub.is_bot_admin
    member_joined_at = hub.member_joined_at
    member_profiles = hub.member_profiles
    observe_checked = hub.observe_checked
    sget = hub.sget
    now = time.time()
    for cid, joined in list(member_joined_at.items()):
        if not group_get(cid, "observe_check_enabled") or group_get(cid, "observe_seconds") <= 0:
            continue          # 该群没开观察期复核（群级可覆盖全局开关）
        _CUR_CID.set(_safe_cid(cid))   # 观察时长/达标条数/动作按该群配置解析
        for uid, jt in list(joined.items()):
            key = f"{cid}:{uid}"
            if key in observe_checked:
                continue
            if not jt or now - float(jt) < sget("OBSERVE_SECONDS"):
                continue
            observe_checked.add(key)          # 先标记，避免异常导致反复处理
            if is_bot_admin(uid):
                continue
            prof = member_profiles.get(cid, {}).get(uid, {}) or {}
            name = prof.get("name") or f"用户{uid}"
            msgs = int(prof.get("msgs", 0) or 0)
            if msgs >= sget("OBSERVE_CHECK_MSGS"):
                continue
            if sget("OBSERVE_CHECK_AVATAR") and msgs == 0:
                try:
                    ch = await context.bot.get_chat(uid)
                    if getattr(ch, "photo", None):
                        continue            # 有头像就不算小号
                except Exception:
                    pass
            if sget("OBSERVE_CHECK_ACTION") == 0:
                try:
                    await context.bot.send_message(
                        ADMIN_USER_ID,
                        f"🔎 观察期巡检：群 <code>{cid}</code> 的 {html.escape(str(name))}（{uid}）"
                        f"观察期已满但本群发言仅 {msgs} 条，请留意（可在「群管中心」配置为自动禁言/移出）。")
                except Exception:
                    pass
            else:
                await _mod_punish(context, cid, uid, sget("OBSERVE_CHECK_ACTION"), sget("SENSITIVE_MUTE_SECONDS"), name, "观察期巡检")
                try:
                    await context.bot.send_message(
                        ADMIN_USER_ID,
                        f"🔎 观察期巡检：{html.escape(str(name))}（{uid}）已"
                        f"{'禁言' if sget('OBSERVE_CHECK_ACTION') == 1 else '移出群'}。")
                except Exception:
                    pass
