# -*- coding: utf-8 -*-
"""按钮回调 handler —— 2026-09-13 从 core/entry.py 的 on_button 拆出。

只做搬运，未改任何逻辑。每个函数对应 on_button 里一个 `data` 命名空间分支；
外层保留 `if <原 test>: await <fn>(...); return`（分支体命中后必 return，故等价）。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
⚠️ 本包**禁止** import core.entry（循环导入）。
"""
from core import hub



async def _btn_invite_refresh_priv(update, context, q, cid, uid, data):
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    _invite_card_body_kb = hub._invite_card_body_kb
    _invite_refresh_all = hub._invite_refresh_all
    chat_name_cache = hub.chat_name_cache
    get_name = hub.get_name
    invite_links = hub.invite_links
    is_bot_admin = hub.is_bot_admin
    logger = hub.logger
    if uid in BLACKLISTED_USERS and not is_bot_admin(uid):
        await q.answer("🚫 你已被禁止使用本机器人", show_alert=True); return
    try:
        rcid_s, _, owner_s = data[len("invite_refresh_priv_"):].rpartition("_")
        rcid, owner = int(rcid_s), int(owner_s)
    except ValueError:
        await q.answer("按钮已过期", show_alert=True); return
    if uid != owner:
        await q.answer("只能刷新自己的进度", show_alert=True); return
    new_awd = 0
    try:
        new_awd = await _invite_refresh_all(context.application, rcid, owner)
    except Exception:
        logger.exception("邀请私聊刷新异常（已吞并）")
    cname = chat_name_cache.get(rcid) or str(rcid)
    link = (invite_links.get(rcid, {}).get(owner) or {}).get("link", "")
    try:
        my_name = await get_name(context.application, owner, cid=rcid)
        body, kb = _invite_card_body_kb(owner, rcid, cname, my_name, link,
                                        f"invite_refresh_priv_{rcid}_{owner}")
        await q.message.edit_text(body, parse_mode="HTML", reply_markup=kb)
    except Exception:
        logger.exception("刷新私聊邀请卡片失败（已吞并）")
    try:
        await q.answer("已刷新" + (f"：新发放 {new_awd} 次奖励 🎉" if new_awd else "：暂无新达标"))
    except Exception:
        pass
    return


async def _btn_invite_accept(update, context, q, cid, uid, data):
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    _inv_dbg = hub._inv_dbg
    invite_confirmed = hub.invite_confirmed
    is_bot_admin = hub.is_bot_admin
    save_data = hub.save_data
    sget = hub.sget
    if uid in BLACKLISTED_USERS and not is_bot_admin(uid):
        await q.answer("🚫 你已被禁止使用本机器人", show_alert=True); return
    body = data[len("inva_"):]
    try:
        choice_s = ""
        if body.startswith("none_"):                 # inva_none_<cid>_<uid>：我自己进的
            choice_s = "none"; body = body[len("none_"):]
        left, _, owner_s = body.rpartition("_")      # 末段 = 申请人 uid
        cid_s, _, inviter_s = left.partition("_")    # 首段 = 群 id（负数），中段 = 邀请人
        cid_, owner = int(cid_s), int(owner_s)
        inviter = 0 if choice_s == "none" else int(inviter_s)
    except ValueError:
        await q.answer("按钮已过期", show_alert=True); return
    rcid, uid_owner = cid_, owner
    if uid != uid_owner:
        await q.answer("只能由申请人本人确认", show_alert=True); return
    if choice_s == "none":
        await q.answer("好的，等管理员批准进群。", show_alert=False)
        return
    if inviter == uid or rcid not in AUTHORIZED_GROUPS or not sget("INVITE_ENABLED"):
        await q.answer("该邀请不可用", show_alert=True); return
    invite_confirmed[f"{rcid}:{uid}"] = inviter   # 归因锁定
    save_data()
    _inv_dbg(rcid, f"[主动问] uid={uid} 确认邀请人 {inviter} → 自动批准")
    try:
        await context.bot.approve_chat_join_request(chat_id=rcid, user_id=uid)
        await q.answer("✅ 已确认，正在为你通过进群…", show_alert=False)
    except Exception as e:
        _inv_dbg(rcid, f"[主动问] 自动批准失败 uid={uid}：{e!r}（转人工，归因已锁定）")
        await q.answer("✅ 邀请已记录，等管理员批准进群。", show_alert=False)
    return


async def _btn_join_verify(update, context, q, cid, uid, data):
    ChatPermissions = hub.ChatPermissions
    _join_verify_pass = hub._join_verify_pass
    _jv_wrong_hit = hub._jv_wrong_hit
    is_bot_admin = hub.is_bot_admin
    join_verify_pending = hub.join_verify_pending
    logger = hub.logger
    parts = data[len("jv_"):].split("_")     # [群id, 用户id] 或 [群id, 用户id, 选项值]
    try:
        cid_v, uid_v = int(parts[0]), int(parts[1])
        pick = parts[2] if len(parts) > 2 else ""
    except (ValueError, IndexError):
        await q.answer("按钮已过期", show_alert=True); return
    if uid != uid_v and not is_bot_admin(uid):
        await q.answer("❌ 这不是你的验证按钮", show_alert=True); return
    rec_v = join_verify_pending.get(f"{cid_v}:{uid_v}")
    if not rec_v:
        # 修复（2026-09-09）：pending 丢失（重部署后存档未同步/记录被清）时，
        # 人可能仍处于验证禁言状态。只说「已通过验证」却不解禁 = 用户永远发不了言。
        # 这里幂等兜底解除限制（本来能发言时重复设置也无害）。
        try:
            await context.bot.restrict_chat_member(
                cid_v, uid_v, permissions=ChatPermissions(
                    can_send_messages=True, can_send_other_messages=True,
                    can_add_web_page_previews=True, can_send_polls=True, can_invite_users=True))
        except Exception:
            logger.exception("入群验证：pending 缺失时解除限制失败 cid=%s uid=%s", cid_v, uid_v)
        await q.answer("✅ 你已通过验证，可以发言了", show_alert=False); return
    name_v = str(rec_v.get("name") or q.from_user.first_name or f"用户{uid_v}")
    ans_v = rec_v.get("ans")
    if ans_v is not None and not str(pick).strip():
        await q.answer("请点下方选项按钮作答", show_alert=True); return   # 按钮模式不接受无选项回调
    if pick:
        if str(pick).strip() != str(ans_v):
            wrong_v, over_v = await _jv_wrong_hit(context, cid_v, uid_v, name_v, rec_v)
            await q.answer("❌ 答案不对，再选一次" + (f"（已错 {wrong_v} 次）" if not over_v else ""),
                           show_alert=not over_v)
            return
    await _join_verify_pass(context, cid_v, uid_v, name_v,
                            int(rec_v.get("msg_id", 0) or 0) or getattr(q.message, "message_id", 0))
    await q.answer("✅ 验证通过，可以发言了", show_alert=False)
    return


async def _btn_invite_refresh(update, context, q, cid, uid, data):
    _invite_refresh_all = hub._invite_refresh_all
    _invite_send_progress_card = hub._invite_send_progress_card
    chat_name_cache = hub.chat_name_cache
    invite_links = hub.invite_links
    logger = hub.logger
    owner = int(data.split("_")[-1]) if data.split("_")[-1].isdigit() else uid
    if uid != owner:
        await q.answer("只能刷新自己的进度", show_alert=True); return
    new_awd = 0
    try:
        new_awd = await _invite_refresh_all(context.application, cid, owner)
    except Exception:
        logger.exception("邀请进度刷新异常（已吞并）")
    cname = chat_name_cache.get(cid) or (getattr(q.message.chat, "title", "") or str(cid))
    link = (invite_links.get(cid, {}).get(owner) or {}).get("link", "")
    try:
        await _invite_send_progress_card(None, context, owner, cid, cname,
                                         link=link, edit_msg=q.message)
    except Exception:
        logger.exception("刷新邀请卡片失败（已吞并）")
    try:
        await q.answer("已刷新" + (f"：新发放 {new_awd} 次奖励 🎉" if new_awd else "：暂无新达标"))
    except Exception:
        pass
    return


async def _btn_invreport(update, context, q, cid, uid, data):
    """管理员手动归因：invreport_<序号>（待归因申请列表上按序号点）。

    2026-09-14 从 on_button 的内联块搬出，逻辑未动。
    """
    _invite_track_join = hub._invite_track_join
    invite_pending = hub.invite_pending
    invite_records = hub.invite_records
    is_bot_admin = hub.is_bot_admin
    try: idx = int(data[len("invreport_"):])
    except ValueError: await q.answer(); return
    if not is_bot_admin(uid): await q.answer("仅管理员", show_alert=True); return
    pend = [(k, v) for k, v in invite_pending.items() if k.startswith(f"{cid}:")]
    if not (1 <= idx <= len(pend)): await q.answer("已失效", show_alert=True); return
    k, link = pend[idx - 1]
    uid_ = int(k.split(":", 1)[1])
    cmu = type("CMU", (), {})()
    setattr(cmu, "invite_link", type("L", (), {"link": link})())
    await _invite_track_join(cmu, cid, uid_, "报备入群", context)
    rec = invite_records.get(f"{cid}:{uid_}", {})
    await q.answer(f"✅ 已归因 奖励 {rec.get('award', 0)} 分" if rec.get('inviter') else "⚠️ 未建记录", show_alert=True)
    return
