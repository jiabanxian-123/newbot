# -*- coding: utf-8 -*-
"""④ 入群验证

JOIN_VERIFY_MODE：0=按钮选答案 1=图片算术 2=一键通过。
新人进群先禁言 + 发验证消息，答对放行；超时按 JOIN_VERIFY_ACTION 处理
（0=只提醒 1=禁言 2=踢出 3=封禁）；答错 JOIN_VERIFY_MAX_WRONG 次按超时档处理。
`cmd_jv_pass` 是管理员的兜底放行通道（回复成员消息发「放行」）。
待验证状态存 `join_verify_pending[cid:uid]`。
"""

from core import hub

from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
import html, io, random, time


def _captcha_render(a, b):
    """画一张 a+b 算术验证码 PNG（带噪点/干扰线）；未装 Pillow 返回 None（调用方降级为文本算式）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    try:
        img = Image.new("RGB", (340, 120), (246, 247, 251))
        d = ImageDraw.Draw(img)
        for _ in range(80):   # 噪点：防机器直读像素
            x, y = random.randint(0, 339), random.randint(0, 119)
            g = random.randint(140, 210)
            d.point((x, y), fill=(g, g, min(255, g + 20)))
        for _ in range(3):    # 干扰线
            x1, y1 = random.randint(0, 339), random.randint(0, 119)
            x2, y2 = random.randint(0, 339), random.randint(0, 119)
            d.line((x1, y1, x2, y2), fill=(185, 185, 205), width=1)
        font = None
        for fp in ("arial.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(fp, 52); break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        d.text((26, 30), f"{a} + {b} = ?", fill=(28, 28, 58), font=font)
        bio = io.BytesIO()
        img.save(bio, "PNG")
        return bio.getvalue()
    except Exception:
        logger.exception("验证码图片生成失败（降级文本算式）")
        return None


def _jv_options(a, b, n=5):
    """按钮验证候选答案：正确答案 + (n-1) 个干扰项，随机顺序、互不重复、非负。"""
    ans = a + b
    opts = {ans}
    for _ in range(400):
        if len(opts) >= n:
            break
        v = ans + random.choice((-5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6, -6))
        if v >= 0:
            opts.add(v)
    v = 0
    while len(opts) < n:      # 极端兜底：补足数量
        if v not in opts:
            opts.add(v)
        v += 1
    opts = list(opts)
    random.shuffle(opts)
    return opts


async def _join_verify_start(context, cid, uid, name):
    """入群验证：mode=0 按钮选答案（默认）/ 1 图片算术打字回复 / 2 一键通过。

    0/2 先限制发言（防打字绕过），点按钮解锁；1 不限制（否则没法回复答案），
    答题期间发言全部被 _join_verify_handle_text 消费，答对/超时由巡检兜底。

    去重：超级群同一次进群会同时收到 chat_member 与 NEW_CHAT_MEMBERS 两个事件源，
    两个 handler 都在 group 2（不同类型互不排斥）→ 这里必须只发一条验证消息。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _captcha_render = hub._captcha_render
    _jv_options = hub._jv_options
    join_verify_pending = hub.join_verify_pending
    logger = hub.logger
    sget = hub.sget
    key = f"{cid}:{uid}"
    old = join_verify_pending.get(key)
    if old:
        if int(old.get("msg_id", 0) or 0):
            return                       # 已登记且验证消息已发出 → 第二个事件源直接跳过
        join_verify_pending.pop(key, None)   # 上一条验证消息没发出去（异常）→ 允许补发
    mode = int(sget("JOIN_VERIFY_MODE"))
    if mode != 1:
        try:
            await context.bot.restrict_chat_member(
                cid, uid, permissions=ChatPermissions(can_send_messages=False))
        except Exception:
            logger.exception("入群验证：限制发言失败 cid=%s uid=%s（继续发验证消息）", cid, uid)
    txt = (str(sget("JOIN_VERIFY_MSG")).replace("{name}", html.escape(str(name)))
           .replace("{seconds}", str(int(sget("JOIN_VERIFY_SECONDS")))))
    mid, png, a, b, opts = 0, None, 0, 0, []
    if mode == 0:
        a, b = random.randint(1, 9), random.randint(1, 9)
        opts = _jv_options(a, b)
        txt += f"\n\n🧮 验证问题：{a} + {b} = ?\n点下方正确答案按钮完成验证。"
    elif mode == 1:
        a, b = random.randint(2, 9), random.randint(2, 9)
        png = _captcha_render(a, b)
        txt += "\n\n🧮 验证问题：" + (f"看图作答（{a} + {b} = ?）" if png is None else "请直接回复图中算式的结果（只发数字）")
    try:
        if png is not None:
            try:
                msg = await context.bot.send_photo(cid, photo=png, caption=txt)
            except Exception:
                # 图片发不出去（群限制发图/权限异常）→ 降级文字题目，别让新人卡在看不见的验证里
                logger.exception("入群验证：验证码图片发送失败，降级文字题目 cid=%s uid=%s", cid, uid)
                msg = await context.bot.send_message(cid, txt + "\n（请直接回复算式结果，只发数字）")
        else:
            if mode == 0:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(str(o), callback_data=f"jv_{cid}_{uid}_{o}")
                                            for o in opts]])
            elif mode == 2:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                    "✅ 点击完成验证", callback_data=f"jv_{cid}_{uid}")]])
            else:
                kb = None
            msg = await context.bot.send_message(cid, txt, reply_markup=kb)
        mid = getattr(msg, "message_id", 0) or 0
    except Exception:
        logger.exception("入群验证：发送验证消息失败 cid=%s uid=%s", cid, uid)
    if not mid:
        # 验证消息没发出去 → 不登记。否则用户看不到题目却会被超时禁言/踢出（静默处罚）。
        logger.warning("入群验证：验证消息未能发出，跳过登记 cid=%s uid=%s", cid, uid)
        if mode != 1:
            # 修复（2026-09-09）：上面已经禁言了，消息却发不出去 → 必须撤销禁言，
            # 否则新人既看不到题目、又永远发不了言（用户报障「发不了言、找不到验证」）。
            try:
                await context.bot.restrict_chat_member(
                    cid, uid, permissions=ChatPermissions(
                        can_send_messages=True, can_send_other_messages=True,
                        can_add_web_page_previews=True, can_send_polls=True, can_invite_users=True))
                logger.info("入群验证：已撤销禁言 cid=%s uid=%s", cid, uid)
            except Exception:
                logger.exception("入群验证：撤销禁言失败 cid=%s uid=%s", cid, uid)
        return
    rec = {"ts": time.time(), "msg_id": mid, "name": str(name), "mode": mode}
    if mode in (0, 1):
        rec.update({"a": a, "b": b, "wrong": 0})
        if mode == 0:
            rec["ans"] = a + b
    join_verify_pending[key] = rec


async def _jv_wrong_hit(context, cid, uid, name, rec):
    """记一次验证答错；达到上限按超时档处理。返回 (累计错次, 是否已达上限)。

    修复（2026-09-09 用户报障「新人被永久禁言、找不到验证」）：
    此前达上限一律 pop 掉 pending + 按档处罚。action=0/1（提醒/禁言）时人还在群里，
    pending 一清 → 再点正确答案只会收到「✅ 你已通过验证」而**不会解除禁言**，
    巡检也扫不到 → **永久禁言**（真实配置 max_wrong=1，点错一次即触发）。
    现在：action 0/1 保留 pending（禁言不影响点按钮，仍可自救），action 2/3 才清（人已离群）。
    重复答错不重复刷群消息（用 over_notified 标记），避免刷屏。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _mod_punish = hub._mod_punish
    join_verify_pending = hub.join_verify_pending
    sget = hub.sget
    rec["wrong"] = int(rec.get("wrong", 0) or 0) + 1
    if int(sget("JOIN_VERIFY_MAX_WRONG")) > 0 and rec["wrong"] >= int(sget("JOIN_VERIFY_MAX_WRONG")):
        if sget("JOIN_VERIFY_ACTION") >= 2:
            join_verify_pending.pop(f"{cid}:{uid}", None)   # 踢出/封禁：人已离群，清记录
        else:
            rec["over"] = True          # 保留 pending，允许继续点按钮自救
        if rec.get("over_notified"):
            return rec["wrong"], True   # 已提醒过，不重复刷屏
        rec["over_notified"] = True
        if sget("JOIN_VERIFY_ACTION") == 0:
            try:
                await context.bot.send_message(
                    cid, f"❌ {html.escape(str(name))} 答错 {rec['wrong']} 次未通过验证，"
                         f"请管理员留意（可继续点按钮作答）。")
            except Exception:
                pass
        else:
            await _mod_punish(context, cid, uid, sget("JOIN_VERIFY_ACTION"), sget("SENSITIVE_MUTE_SECONDS"), name, "验证答错超限")
            try:
                await context.bot.send_message(
                    cid, f"❌ {html.escape(str(name))} 验证答错超限，已"
                         f"{'禁言' if sget('JOIN_VERIFY_ACTION') == 1 else '移出群' if sget('JOIN_VERIFY_ACTION') == 2 else '封禁'}。"
                         + ("（仍可点下方正确答案通过验证）" if sget("JOIN_VERIFY_ACTION") == 1 else ""))
            except Exception:
                pass
        return rec["wrong"], True
    return rec["wrong"], False


async def _join_verify_handle_text(context, cid, uid, text):
    """入群验证（图片算术 mode=1）：待验证成员的发言优先当答案处理。返回 True=消息已消费，不再进命令/游戏逻辑。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _join_verify_pass = hub._join_verify_pass
    _jv_wrong_hit = hub._jv_wrong_hit
    join_verify_pending = hub.join_verify_pending
    rec = join_verify_pending.get(f"{cid}:{uid}")
    if not rec or int(rec.get("mode", 0) or 0) != 1:
        return False
    name = str(rec.get("name") or f"用户{uid}")
    ans = str(text or "").strip()
    if ans.isdigit() and int(ans) == int(rec.get("a", 0)) + int(rec.get("b", 0)):
        await _join_verify_pass(context, cid, uid, name, int(rec.get("msg_id", 0) or 0))
        return True
    wrong, over = await _jv_wrong_hit(context, cid, uid, name, rec)
    if not over:
        try:
            await context.bot.send_message(cid, f"❌ 答案不对，请再试一次（已错 {wrong} 次）。")
        except Exception:
            pass
    return True


async def _join_verify_pass(context, cid, uid, name, msg_id=0):
    """验证通过：解除限制 → 删掉验证消息 → 发通过提示。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    join_verify_pending = hub.join_verify_pending
    logger = hub.logger
    sget = hub.sget
    key = f"{cid}:{uid}"
    join_verify_pending.pop(key, None)
    try:
        await context.bot.restrict_chat_member(
            cid, uid,
            permissions=ChatPermissions(can_send_messages=True, can_send_other_messages=True,
                                        can_add_web_page_previews=True, can_send_polls=True,
                                        can_invite_users=True))
    except Exception:
        logger.exception("入群验证：解除限制失败 cid=%s uid=%s", cid, uid)
    if msg_id:
        try:
            await context.bot.delete_message(cid, msg_id)
        except Exception:
            pass
    try:
        await context.bot.send_message(cid, str(sget("JOIN_VERIFY_OK_MSG")).replace("{name}", html.escape(str(name))))
    except Exception:  # silent-ok: 通过提示发不出只是外观；解除限制失败已单独记 exception
        pass


async def join_verify_sweep(context):
    """入群验证超时巡检（每 60 秒）：超时未通过 → 按配置提醒/禁言/踢出/封禁，并清掉验证消息。

    不因 JOIN_VERIFY_ENABLED=0 早退：突袭人墙期间强制登记的待验证（以及关开关瞬间的存量）
    也要正常结算，否则人被永久禁言。新登记入口才受开关控制。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _CUR_CID = hub._CUR_CID
    _mod_punish = hub._mod_punish
    _raid_recover = hub._raid_recover
    _safe_cid = hub._safe_cid
    join_verify_pending = hub.join_verify_pending
    logger = hub.logger
    raid_until = hub.raid_until
    sget = hub.sget
    # 不按全局开关早退：人墙可能只在个别群生效（群级覆盖），到期都必须解除
    for _cid in list(raid_until):
        try:
            _CUR_CID.set(_safe_cid(_cid))
            await _raid_recover(context, _cid)
        except Exception:
            logger.exception("防突袭恢复检查异常（已吞并）")
    now = time.time()
    for key in list(join_verify_pending):
        rec = join_verify_pending.get(key) or {}
        try:
            cid_s, _, uid_s = str(key).partition(":")
            cid, uid = int(cid_s), int(uid_s)
        except ValueError:
            join_verify_pending.pop(key, None); continue
        _CUR_CID.set(_safe_cid(cid))   # 这条待验证记录属于该群 → 阈值/动作按该群配置解析
        if now - float(rec.get("ts", now)) < int(sget("JOIN_VERIFY_SECONDS")):
            continue
        join_verify_pending.pop(key, None)
        name = rec.get("name") or f"用户{uid}"
        if sget("JOIN_VERIFY_ACTION") == 0:
            # 修复（2026-09-09）：action=0「只提醒」时，_join_verify_start 给的禁言没人解
            # → 新人被永久禁言（用户报障「发不了言」）。只提醒档必须解除禁言。
            try:
                await context.bot.restrict_chat_member(
                    cid, uid, permissions=ChatPermissions(
                        can_send_messages=True, can_send_other_messages=True,
                        can_add_web_page_previews=True, can_send_polls=True, can_invite_users=True))
            except Exception:
                logger.exception("入群验证：超时(action=0)解除限制失败 cid=%s uid=%s", cid, uid)
            try:
                await context.bot.send_message(
                    cid, f"⏰ {html.escape(str(name))} 入群后未在 {int(sget('JOIN_VERIFY_SECONDS'))} 秒内完成验证，"
                         f"已自动放行，请管理员留意。")
            except Exception:
                pass
        else:
            await _mod_punish(context, cid, uid, sget("JOIN_VERIFY_ACTION"), sget("SENSITIVE_MUTE_SECONDS"), name, "入群验证超时")
            try:
                await context.bot.send_message(
                    cid, f"⏰ {html.escape(str(name))} 入群验证超时，已{'禁言' if sget('JOIN_VERIFY_ACTION') == 1 else '移出群'}。")
            except Exception:
                pass
        if rec.get("msg_id"):
            try:
                await context.bot.delete_message(cid, int(rec["msg_id"]))
            except Exception:
                pass


async def cmd_jv_pass(update, context):
    """管理员一键放行入群验证（2026-09-09 兜底通道）。

    场景：新人卡在验证（被禁言/验证消息被顶掉/答错超限）在群里求助，
    管理员回复其消息发「放行」即可解除限制并清掉 pending，不用去后台改配置。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _admin_log = hub._admin_log
    _is_group_admin = hub._is_group_admin
    join_verify_pending = hub.join_verify_pending
    need_auth = hub.need_auth
    save_data = hub.save_data
    send_reply = hub.send_reply
    user_names = hub.user_names
    if not await need_auth(update, context): return
    cid, admin = update.effective_chat.id, update.effective_user.id
    if not await _is_group_admin(context, cid, admin):
        await send_reply(update, context, "❌ 仅管理员可操作"); return
    reply = update.message.reply_to_message
    args = context.args or []
    if reply:
        target = reply.from_user.id
    elif args and args[0].lstrip("-").isdigit():
        target = int(args[0])
    else:
        await send_reply(update, context, "用法：回复该成员的消息发「放行」，或「放行 用户ID」"); return
    rec = join_verify_pending.pop(f"{cid}:{target}", None)
    try:
        await context.bot.restrict_chat_member(cid, target, permissions=ChatPermissions(
            can_send_messages=True, can_send_other_messages=True, can_add_web_page_previews=True,
            can_send_polls=True, can_invite_users=True))
    except Exception as e:
        await send_reply(update, context, f"❌ 放行失败：{e}"); return
    if rec and rec.get("msg_id"):
        try:
            await context.bot.delete_message(cid, int(rec["msg_id"]))
        except Exception:
            pass
    _admin_log(cid, admin, "放行入群验证", user_names.get(target, str(target))); save_data()
    await send_reply(update, context,
                     f"✅ 已放行 {user_names.get(target, target)}，他现在可以发言了。"
                     + ("（原有验证记录已清除）" if rec else ""))
