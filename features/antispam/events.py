# -*- coding: utf-8 -*-
"""Telegram 事件接线层

四个入口（在 bot.py 里注册到 Application）：
  · `on_new_members_msg` —— message 版入群事件（普通群收不到 chat_member 更新，靠服务消息兜底）
  · `on_member_event`    —— chat_member 版成员进出事件（需 bot 是管理员）
  · `on_my_chat_member`  —— bot 自身被拉进/踢出群；**这其实不属于六域任何一域**，
    它记的是「谁把机器人拉进群」（bot_added_by），是「未授权群却有人发命令」的溯源入口
  · `on_join_request`    —— 入群申请；**这也不属于六域**，它是**邀请归因**
    （deep-link / invite_pending / INVITE_AUTO_APPROVE），逻辑上属于 features/invite 域，
    暂留此处（见文件末「已知归属问题」）。

`on_new_members_msg` / `on_member_event` 是「新人进群」的两条入口，会依次调用
硬门槛(joingate) → 防突袭(raid) → 入群验证(joinverify)，所以不适合塞进任何单域。
"""

from core import hub

from telegram.error import TelegramError
import time


async def _clean_service_msg(context, message, kind):
    """清理服务消息：入群/退群的系统提示（Rose cleanservice 同款）。

    赌场群人来人往，「XX 加入了群组」这类提示一天几百条 —— 既刷屏，又等于给
    广告号直播你群里有多热闹。开关 CLEAN_SERVICE_ENABLED 打开就删掉。

    ⚠️ 删不掉是常态（bot 没删除权限 / 消息太旧 / 已被别人删）→ 只 warning 不抛，
       **绝不能因为删不掉就打断入群流程**（入群验证/防突袭都在同一条链上）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    sget = hub.sget
    if not message:
        return False
    if not sget("CLEAN_SERVICE_ENABLED"):
        return False
    try:
        await context.bot.delete_message(chat_id=message.chat_id, message_id=message.message_id)
        logger.info("服务消息已清理（%s）cid=%s mid=%s", kind, message.chat_id, message.message_id)
        return True
    except Exception as e:
        logger.warning("服务消息删除失败（%s）cid=%s mid=%s：%r",
                       kind, message.chat_id, message.message_id, e)
        return False


async def on_left_member_msg(update, context):
    """退群服务消息（message 版）：只清理提示，不重复记 leave_records。

    普通群收不到 chat_member 更新，退群只有这条服务消息兜底；chat_member 版
    （on_member_event）能收到时这里也会触发（Telegram 两路都发），所以这里
    **只做清理**，退群统计仍由 on_member_event 负责，避免记两遍。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _bind_update_cid = hub._bind_update_cid
    _clean_service_msg = hub._clean_service_msg
    logger = hub.logger
    _bind_update_cid(update)
    try:
        message = update.effective_message
        if not message or not getattr(message, "left_chat_member", None):
            return
        await _clean_service_msg(context, message, "退群")
    except Exception:
        logger.exception("退群服务消息处理异常（已吞并）")


async def on_new_members_msg(update, context):
    """message 版入群事件（普通群收不到 chat_member 更新，只能靠服务消息兜底）。
    普通群的服务消息不带邀请链接，能归因到就走 pending 映射，归因不到就静默跳过。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _bind_update_cid = hub._bind_update_cid
    _clean_service_msg = hub._clean_service_msg
    _inv_dbg = hub._inv_dbg
    _invite_track_join = hub._invite_track_join
    _join_gate_check = hub._join_gate_check
    _join_verify_start = hub._join_verify_start
    _raid_active = hub._raid_active
    _raid_on_join = hub._raid_on_join
    is_bot_admin = hub.is_bot_admin
    logger = hub.logger
    member_joined_at = hub.member_joined_at
    sget = hub.sget
    _bind_update_cid(update)
    try:
        message = update.effective_message
        if not message or not message.new_chat_members:
            return
        cid = message.chat_id
        _inv_dbg(cid, f"[svc] on_new_members_msg 触发 新成员数={len(message.new_chat_members)}")
        for member in message.new_chat_members:
            uid = member.id
            name = member.first_name or f"用户{uid}"
            _inv_dbg(message.chat_id, f"服务消息进群 uid={uid}（new_chat_members 兜底）")
            await _invite_track_join(message, cid, uid, name, context)
            _gate_ok = True
            if not member.is_bot and not is_bot_admin(uid):
                _gate_ok = await _join_gate_check(context, cid, member, name)    # 硬门槛：不满足直接移出
                await _raid_on_join(context, cid, uid)                           # 防突袭计数（按人去重）
                # 入群时间无条件记录：观察期起点与「强制订阅只拦新人」都依赖它，
                # 只在验证开启时才记 → 验证关着时这两项全部静默失效。
                member_joined_at[cid][uid] = time.time()
                if _gate_ok and (sget("JOIN_VERIFY_ENABLED") or _raid_active(cid)):
                    await _join_verify_start(context, cid, uid, name)
        # 入群提示清理放在**最后**：硬门槛/防突袭/入群验证都跑完才删，
        # 否则删早了会导致新人记录不到（「开关开了没反应」的另一种死法）。
        await _clean_service_msg(context, message, "入群")
    except Exception:
        logger.exception("message 入群事件处理异常（已吞并）")


async def on_member_event(update, context):
    """成员进出事件：退群/入群记录（bot 需为群管理员才能收到）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _bind_update_cid = hub._bind_update_cid
    _cleanup_left_member_games = hub._cleanup_left_member_games
    _inv_dbg = hub._inv_dbg
    _invite_track_join = hub._invite_track_join
    _is_join_transition = hub._is_join_transition
    _join_gate_check = hub._join_gate_check
    _join_verify_start = hub._join_verify_start
    _raid_active = hub._raid_active
    _raid_on_join = hub._raid_on_join
    _remember_name = hub._remember_name
    invite_records = hub.invite_records
    is_bot_admin = hub.is_bot_admin
    join_verify_pending = hub.join_verify_pending
    leave_records = hub.leave_records
    logger = hub.logger
    member_joined_at = hub.member_joined_at
    now_bj = hub.now_bj
    raid_counted = hub.raid_counted
    save_data = hub.save_data
    schedule_delete = hub.schedule_delete
    sget = hub.sget
    _bind_update_cid(update)
    try:
        cmu = update.chat_member
        if not cmu:
            return
        cid = cmu.chat.id
        _inv_dbg(cid, f"[evt] on_member_event 触发 cid={cid}")
        new, old = cmu.new_chat_member, cmu.old_chat_member
        uid, name = new.user.id, new.user.first_name or f"用户{new.user.id}"
        ts = now_bj().strftime("%Y-%m-%d %H:%M")
        if new.status == "left" and old.status != "left":
            leave_records[cid].append({"ts": ts, "uid": uid, "name": name})
            leave_records[cid] = leave_records[cid][-100:]
            rec = invite_records.get(f"{cid}:{uid}")
            if rec: rec["left"] = True   # 邀请记录：退群即失效（不再计入排行）
            join_verify_pending.pop(f"{cid}:{uid}", None)  # 退群清待验证：防幽灵记录（重进可重新验证）
            raid_counted.pop(f"{cid}:{uid}", None)         # 退群清计数：重进算新一次
            await _cleanup_left_member_games(context.application, cid, uid)  # ⑭ 从等待房移除，防幽灵开局
        elif _is_join_transition(new, old):
            leave_records[cid].append({"ts": ts, "uid": uid, "name": name, "join": True})
            leave_records[cid] = leave_records[cid][-100:]
            member_joined_at[cid][uid] = time.time()  # 观察期起点
            _inv_dbg(cid, f"chat_member 进群事件 uid={uid}，事件链接：{(getattr(getattr(cmu, 'invite_link', None), 'link', '') or '（无）')}")
            await _invite_track_join(cmu, cid, uid, name, context)  # 邀请系统追踪（内部自吞异常）
            _gate_ok = True
            if not new.user.is_bot and not is_bot_admin(uid):
                _gate_ok = await _join_gate_check(context, cid, new.user, name)  # 硬门槛：不满足直接移出
                await _raid_on_join(context, cid, uid)                           # 防突袭计数（按人去重）
                if _gate_ok and (sget("JOIN_VERIFY_ENABLED") or _raid_active(cid)):
                    await _join_verify_start(context, cid, uid, name)            # 入群验证（默认关/突袭期强制）
            if sget("WELCOME_ENABLED"):
                try:
                    text = sget("WELCOME_TPL").replace("{name}", name).replace("{group}", getattr(cmu.chat, "title", "") or "").replace("{id}", str(uid))
                    wmsg = await context.bot.send_message(chat_id=cid, text=text)
                    if wmsg and sget("REPLY_DELETE_SECONDS") > 0:
                        schedule_delete(context.application, cid, wmsg, sget("REPLY_DELETE_SECONDS"))
                except TelegramError: pass
        _remember_name(update)
        save_data()
    except Exception:
        logger.exception("成员事件处理异常（已吞并）")


async def on_my_chat_member(update, context):
    """bot 自身成员状态变化（MY_CHAT_MEMBER）：记录「谁把机器人拉进群」。

    普通群收不到普通成员的 chat_member，但 bot 被拉进/踢出群会触发 my_chat_member，
    且 from_user 就是操作者——这正是「未授权群却有人发命令」的溯源入口（用户 2026-09-11 需求）。
    只在「进入群」时记录，踢出只补 left_ts，避免状态抖动重复写。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    bot_added_by = hub.bot_added_by
    logger = hub.logger
    now_bj = hub.now_bj
    save_data = hub.save_data
    user_names = hub.user_names
    try:
        mcu = update.my_chat_member
        if not mcu:
            return
        new, old = mcu.new_chat_member, mcu.old_chat_member
        if not new or not new.user or not new.user.is_bot:
            return  # 只关心 bot 自己
        cid = mcu.chat.id
        if cid >= 0:
            return  # 群 ID 必为负数；私聊不是群，跳过
        _old = old.status if old else None
        _new = new.status
        if _new in ("member", "administrator", "creator") and _old not in ("member", "administrator", "creator"):
            inviter = mcu.from_user.id if mcu.from_user else 0
            inv_name = (mcu.from_user.first_name or f"用户{inviter}") if mcu.from_user else "未知"
            if inviter:
                user_names[inviter] = inv_name
            bot_added_by[cid] = {
                "by": inviter,
                "by_name": inv_name,
                "ts": now_bj().strftime("%Y-%m-%d %H:%M"),
                "title": getattr(mcu.chat, "title", "") or "",
            }
            logger.info("机器人被拉进群 cid=%s 群名=%s 操作者=%s(%s) 已授权=%s",
                        cid, bot_added_by[cid]["title"], inviter, inv_name, cid in AUTHORIZED_GROUPS)
            save_data()
        elif _new in ("left", "kicked") and _old not in ("left", "kicked"):
            rec = bot_added_by.get(cid)
            if rec:
                rec["left_ts"] = now_bj().strftime("%Y-%m-%d %H:%M")
    except Exception:
        logger.exception("my_chat_member 处理异常（已吞并）")


async def on_join_request(update, context):
    """入群申请处理（三路归因，全部不依赖群事件的链接字段可靠性）：
    ① deep-link/按钮 已锁定归因（invite_confirmed）→ 无条件自动批准秒进；
    ② 申请自带链接且命中专属链接 → 存 invite_pending，按 INVITE_AUTO_APPROVE 开关决定是否自动批；
    ③ 都没有（搜群/旧链接进的）→ bot 用 user_chat_id 主动私聊弹邀请人按钮（Bot API 5.5
       文档保证 bot 管理员可主动联系发申请者），点按钮补归因后自动批。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _bind_update_cid = hub._bind_update_cid
    _inv_dbg = hub._inv_dbg
    _invite_ask_inviter = hub._invite_ask_inviter
    invite_confirmed = hub.invite_confirmed
    invite_pending = hub.invite_pending
    join_requests = hub.join_requests
    logger = hub.logger
    now_bj = hub.now_bj
    save_data = hub.save_data
    sget = hub.sget
    _bind_update_cid(update)
    try:
        req = getattr(update, "chat_join_request", None)
        if req is None and hasattr(update, "from_user") and hasattr(update, "chat"):
            req = update   # 兼容直接传入 request 对象（内部复用/测试）
        if not req:
            return
        cid = req.chat.id
        _inv_dbg(cid, f"[req] on_join_request 触发 cid={cid}")
        uid, name = req.from_user.id, req.from_user.first_name or f"用户{req.from_user.id}"
        join_requests[cid].append({"ts": now_bj().strftime("%Y-%m-%d %H:%M"), "uid": uid, "name": name})
        join_requests[cid] = join_requests[cid][-100:]
        inviter = invite_confirmed.get(f"{cid}:{uid}")
        has_link = bool(getattr(req, "invite_link", None) and getattr(req.invite_link, "link", ""))
        if has_link:
            invite_pending[f"{cid}:{uid}"] = req.invite_link.link   # 归因兜底：批准后的 join 事件可能不带链接
            _inv_dbg(cid, f"入群申请 uid={uid}，已存待归因链接 …{req.invite_link.link[-12:]}")
        else:
            _inv_dbg(cid, f"入群申请 uid={uid}，申请未携带链接（归因{'已锁定 ' + str(inviter) if inviter else '未确认'}）")
        if inviter:
            try:
                await context.bot.approve_chat_join_request(chat_id=cid, user_id=uid)
                _inv_dbg(cid, f"归因已锁定（邀请人 {inviter}）→ 自动批准 uid={uid}")
            except Exception as e:
                _inv_dbg(cid, f"自动批准失败 uid={uid}：{e!r}（转人工）")
        elif not has_link:
            await _invite_ask_inviter(req, cid, uid, name, context)   # 主动问兜底（内部吞异常）
        elif sget("INVITE_AUTO_APPROVE"):
            try:
                await context.bot.approve_chat_join_request(chat_id=cid, user_id=uid)
                _inv_dbg(cid, f"申请带链接 + 开关开 → 自动批准 uid={uid}")
            except Exception as e:
                _inv_dbg(cid, f"自动批准失败 uid={uid}：{e!r}（转人工）")
        save_data()
    except Exception:
        logger.exception("入群申请处理异常（已吞并）")
