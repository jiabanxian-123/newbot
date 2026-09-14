# -*- coding: utf-8 -*-
"""feature/autodel —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from telegram.error import BadRequest, RetryAfter, TelegramError

# ⛔ 2026-09-14 删除：这里原有 `_autodel_skip()` / `_autodel_caller_name()` ——
#   它们用 `sys._getframe` 回溯「这次发送是谁发起的」，供**按函数名匹配**的三张白名单
#   （KEEP / OWN / MARKUP_IGNORE）使用。那套机制的根本缺陷是：
#   **函数一改名 / 被合并 / 搬走，白名单就静默失配** ——
#   该删的不删、不该删的被删，不报错、不打日志。历史上「自动删除反复出问题」就是它。
#   现在改成「发送时自己声明」：默认删，例外由发送点在**调用处**显式声明 ——
#     · 直接调 Bot API 时：行内 `autodel_keep=True` / `autodel_secs=N`；
#     · 走了 reply_text / 长消息包装、拿得到消息对象时：`own_messages(app, cid, ids, secs)`。
#   调用方函数叫什么名字，从此**不再参与任何判断**（守卫 test_autodel_explicit.py）。


def _autodel_default_secs():
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    try:
        return int(sget("AUTODEL_DEFAULT_SECONDS") or 0)
    except (TypeError, ValueError):
        return 0


def _autodel_decide(chat_id, has_markup, secs):
    """纯函数：算出这次发送要不要排入删除、排多少秒（0 = 不删）。

    规则（默认删，例外才不删）—— **只看消息本身的属性，不看调用方是谁**：
      · 私聊（cid > 0）→ 不删（玩家自己的收件箱）
      · 带按钮（reply_markup）→ 不删（交互面板，删了没法玩）
      · 秒数设置 <= 0（功能关闭）→ 不删
      · 其余：按 secs 删

    ⛔ 这里刻意**没有 caller 参数**：旧版拿「调用方函数名」去白名单里查，
       函数一改名就静默失配。需要豁免的发送点自己声明（见本文件头的说明）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    if chat_id is None:
        return 0
    try:
        if int(chat_id) >= 0:
            return 0
    except (TypeError, ValueError):
        return 0
    if has_markup:
        return 0
    try:
        return max(0, int(secs or 0))
    except (TypeError, ValueError):
        return 0


def install_autodelete_default(app):
    """【默认开启自动删除】给**运行实例真实类**的每个发送 API 打补丁：群消息默认回收。

    幂等（重复调用无副作用）。用**类级**补丁，因为 Bot 定义了 __slots__，无法挂实例属性。

    显式声明（补丁唯一的例外来源，**不再看调用方函数名**）：
      · `bot.send_message(..., autodel_keep=True)`  → 这条永久保留（公示类）；
      · `bot.send_message(..., autodel_secs=N)`     → 这条按 N 秒回收（覆盖默认）；
      · `bot.send_message(..., autodel_own=True)`   → 这条自己管生命周期，补丁不插手；
      · 走 `reply_text` / `safe_send_long` 拿得到消息对象时用 `own_messages(app, cid, ids, secs)`
        （`Message.reply_text` 与 `Bot.send_message` **都不接受任意关键字参数**，上面那三个 kwarg
         传不过 reply_text，所以必须有这条路）。

    ⚠️ 覆盖 `_AUTODEL_PATCH_METHODS` 里的**全部**发送 API，而不是只包 send_message：
    只包一个的话，用 send_photo / send_document 发的新功能又变成「永不删除」。

    ⚠️⚠️ 2026-09-12 真根因（用户截图：群里发「积分排名」→「⚠️ 指令处理出错，请联系管理员。」）：
      `Application.builder().build()` 造出来的是 **ExtBot**（`Bot` 的子类，PTB 源码里走
      `ApplicationBuilder._build_ext_bot()`），而 ExtBot **自己实现了全部 send_xxx**，内部用
      `super().send_xxx()` 转发。此前把补丁 setattr 到父类 `telegram.Bot` 上 ⇒ 实例走的是
      子类方法，补丁**从来没有生效过**，后果两条：
        ① 「默认自动删除」自上线起一次都没跑过（这就是用户第 N 次追问「为什么新加的功能
           永远不会自动删除」的真因 —— 不是没写，是补丁打在了不被用到的那一层）；
        ② 调用方传的 `autodel_own` / `autodel_keep` / `autodel_secs` 从来没被 pop 掉，
           ExtBot 直接抛 `TypeError: got an unexpected keyword argument 'autodel_own'`。
           榜单 `send_rank_page` 正好传了 `autodel_own=True` ⇒ 异常冒到 on_text 外层 except
           ⇒ 用户看到「指令处理出错」，消息还被 POINTS_DELETE_SECONDS 回收（截图原样）。
      修法：沿**真实类的方法解析顺序（MRO）**找到「实际定义该方法的那个类」再 setattr，
      每个方法只包一层（父类不再包，否则走 super() 转发时会二次记账）。
      ⚠️ 别再用「给 Bot 打补丁」这种写在父类上的写法：父类补丁会被子类覆盖悄悄吃掉。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _AUTODEL_PATCH_METHODS = hub._AUTODEL_PATCH_METHODS
    _AUTODEL_PATCH_PREFIX = hub._AUTODEL_PATCH_PREFIX
    logger = hub.logger
    from telegram import Bot as _Bot
    _bot_obj = getattr(app, "bot", None)
    _bot_cls = _Bot
    if _bot_obj is not None and isinstance(type(_bot_obj), type):
        _bot_cls = type(_bot_obj)
    if getattr(_bot_cls, "_autodel_patched", False):
        return   # 幂等：同一类只打一遍

    def _owner(cls, name):
        """该方法在 cls 的 MRO 上「实际生效」的定义者 = 第一个在 __dict__ 里定义它的类。

        ExtBot 重写了全部 send_xxx ⇒ owner 是 ExtBot；只有它自己没实现的才回落父类。
        **必须打在 owner 上**：打在父类 = 被 ExtBot 覆盖吃掉（死补丁，本 bug 的成因）；
        打在父类和子类两层 = 子类 `super()` 转发时二次排程（同一消息排两条删除任务）。
        """
        return next((k for k in cls.__mro__ if name in k.__dict__), None)

    def _make_patch(method_name, orig):
        async def _patched(self, *args, **kwargs):
            keep = bool(kwargs.pop("autodel_keep", False))
            # 显式声明生命周期（比按「调用方函数名」匹配可靠得多，见 audit_autodel_whitelist 注释）：
            #   autodel_own=True → 这条消息自己管回收，补丁不要插手（防重复排程）
            #   autodel_secs=N   → 这条消息用 N 秒（覆盖默认 / 榜单口径）
            own = bool(kwargs.pop("autodel_own", False))
            secs_override = kwargs.pop("autodel_secs", None)
            res = await orig(self, *args, **kwargs)
            try:
                if keep or own or res is None:
                    return res
                msgs = res if isinstance(res, (list, tuple)) else [res]
                for msg in msgs:
                    mid = getattr(msg, "message_id", None)
                    cid = getattr(msg, "chat_id", None)
                    if mid is None or cid is None:
                        continue
                    if secs_override is not None:
                        try: secs = max(0, int(secs_override))
                        except (TypeError, ValueError): secs = 0
                    else:
                        markup = kwargs.get("reply_markup") or getattr(msg, "reply_markup", None)
                        # ★ 只看消息属性，不看调用方是谁（旧版这里还多传一个「调用方函数名」参数）
                        secs = hub._autodel_decide(cid, markup is not None,
                                                   hub._autodel_default_secs())
                    if secs > 0:
                        hub.schedule_delete_ids(app, int(cid), int(mid), secs)
            except Exception:
                hub.logger.exception("默认自动删除排程失败（不影响发送）")
            return res
        _patched.__name__ = hub._AUTODEL_PATCH_PREFIX + method_name
        # `__code__.co_name` 一起改**只为可读性**（traceback / 日志里显示
        # `_autodel_patched_send_message` 而不是 `_patched`）。
        # ⚠️ 2026-09-14 起它不再是「白名单能否命中」的前提 —— 按函数名匹配的那一层已经删掉了。
        try:
            _patched.__code__ = _patched.__code__.replace(co_name=_patched.__name__)
        except Exception:   # pragma: no cover - 极端环境兜底
            hub.logger.debug("补丁帧名改写失败（仅影响日志可读性，不影响删除行为）")
        return _patched

    done = []
    for _m in _AUTODEL_PATCH_METHODS:
        _own = _owner(_bot_cls, _m)          # ← 打在该方法的真实定义类上（ExtBot）
        if _own is None:
            continue
        _func = _own.__dict__.get(_m)
        if not callable(_func):
            continue
        if getattr(_func, "__name__", "").startswith(_AUTODEL_PATCH_PREFIX):
            continue                          # 已经包过（防二次包装）
        setattr(_own, _m, _make_patch(_m, _func))
        done.append(_m)
    _bot_cls._autodel_patched = True
    # ★ 自检（接棒退役的 audit_autodel_whitelist）：逐个确认清单里的方法**真的**被包上了。
    #   2026-09-12 那次事故就是「补丁打在了不被用到的那一层」—— 静默不生效、没人发现，
    #   直到用户截图「指令处理出错」才暴露。所以这里必须有一条会响的告警。
    _miss = [x for x in _AUTODEL_PATCH_METHODS
             if _owner(_bot_cls, x) is not None
             and not getattr(getattr(_owner(_bot_cls, x), x), "__name__", "")
                     .startswith(_AUTODEL_PATCH_PREFIX)]
    if _miss:
        logger.error("⚠️ 默认自动删除补丁未生效的发送 API：%s"
                     "（这些 API 发出的群消息不会被自动回收）", _miss)
    # 走 sget 而不是 `hub.AUTODEL_DEFAULT_SECONDS` 直读：这个设置是**按群可覆盖**的，
    # 直读全局变量会绕过群级覆盖（同 _fmt_tpl 那个坑）。启动日志本身没有群上下文、
    # 拿到的还是全局值，但保持统一入口，守卫（test_settings_no_group_bypass.py）才好查。
    logger.info("已启用「默认自动删除」：群里无按钮消息 %s 秒后回收（已覆盖 %s）",
                hub.sget("AUTODEL_DEFAULT_SECONDS"), ",".join(done))


def _autodel_text_hit(message, text):
    """自动删除规则（文本类）：返回规则名或 None。开关即法律，网页「自动删除」页可改。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _link_whitelisted = hub._link_whitelisted
    _multi_has = hub._multi_has
    sget = hub.sget
    if _multi_has(sget("AUTODEL_TEXT_RULES"), "link") and text and ("http://" in text or "https://" in text or "t.me/" in text
            or any(getattr(e, "type", None) in ("url", "text_link") for e in (message.entities or []))):
        # 域名白名单：名单内（含子域名）的链接放行，不再一律删
        if sget("LINK_WHITELIST_ENABLED") and _link_whitelisted(text):
            pass
        else:
            return "link"
    if _multi_has(sget("AUTODEL_TEXT_RULES"), "long") and text and len(text) > max(50, int(sget("AUTODEL_LONG_LEN"))):
        return "long"
    if _multi_has(sget("AUTODEL_TEXT_RULES"), "premium_emoji") and any(
            getattr(e, "type", None) == "custom_emoji" for e in (message.entities or [])):
        return "premium_emoji"
    return None


def _autodel_media_hit(message):
    """自动删除规则（媒体类）：返回规则名或 None。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _is_service_message = hub._is_service_message
    _multi_has = hub._multi_has
    sget = hub.sget
    if message is None:
        return None
    if _is_service_message(message):
        return "service" if _multi_has(sget("AUTODEL_MEDIA_TYPES"), "service") else None
    if message.sticker is not None:
        return "sticker" if _multi_has(sget("AUTODEL_MEDIA_TYPES"), "sticker") else None
    if message.animation is not None:
        return "gif" if _multi_has(sget("AUTODEL_MEDIA_TYPES"), "gif") else None
    if message.voice is not None or message.video_note is not None:
        return "voice" if _multi_has(sget("AUTODEL_MEDIA_TYPES"), "voice") else None
    if message.contact is not None:
        return "contact" if _multi_has(sget("AUTODEL_MEDIA_TYPES"), "contact") else None
    if message.document is not None:
        name = (message.document.file_name or "").lower()
        mt = message.document.mime_type or ""
        if _multi_has(sget("AUTODEL_MEDIA_TYPES"), "archive") and (mt in ("application/zip", "application/x-rar-compressed",
                                       "application/x-7z-compressed", "application/gzip", "application/x-tar")
                or name.endswith((".zip", ".rar", ".7z", ".tar", ".gz"))):
            return "archive"
        if _multi_has(sget("AUTODEL_MEDIA_TYPES"), "executable") and (mt in ("application/x-msdownload", "application/vnd.android.package-archive",
                                          "application/x-dosexec")
                or name.endswith((".exe", ".msi", ".bat", ".cmd", ".scr", ".apk", ".com"))):
            return "executable"
        return "document" if _multi_has(sget("AUTODEL_MEDIA_TYPES"), "document") else None
    if message.photo:
        return "photo" if _multi_has(sget("AUTODEL_MEDIA_TYPES"), "photo") else None
    if message.video is not None:
        return "video" if _multi_has(sget("AUTODEL_MEDIA_TYPES"), "video") else None
    return None


async def _autodel_enforce(update, context):
    """自动删除规则执行：命中即静默撤删。返回 True 表示已删（调用方应停止后续处理）。
    系统消息（建群/迁移等）effective_user 经常为 None，单独走路径不要求 user 在场；非系统消息仍按原规则：管理员/机器人豁免。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _autodel_media_hit = hub._autodel_media_hit
    _autodel_text_hit = hub._autodel_text_hit
    _invite_flag_ad = hub._invite_flag_ad
    _is_service_message = hub._is_service_message
    is_bot_admin = hub.is_bot_admin
    is_group_chat = hub.is_group_chat
    schedule_delete = hub.schedule_delete
    sget = hub.sget
    user, message = update.effective_user, update.effective_message
    if not message or not is_group_chat(update):
        return False
    is_svc = _is_service_message(message)
    if not is_svc:
        if not user or user.is_bot:
            return False
        if is_bot_admin(user.id):
            return False
    hit = _autodel_text_hit(message, message.text or message.caption or "") or _autodel_media_hit(message)
    if hit:
        if hit == "link" and not is_svc and user:
            _invite_flag_ad(update.effective_chat.id, user.id, "发链接/广告")  # 风控连坐
        delay = int(sget("AUTODEL_MEDIA_SECONDS") if is_svc or hit in (
            "photo", "video", "sticker", "gif", "voice", "contact", "document", "archive", "executable", "service"
        ) else sget("AUTODEL_TEXT_SECONDS"))
        if delay > 0:
            # 延迟删除：命中后 N 秒再撤（0=立即删），给管理员留查看时间
            schedule_delete(context.application, update.effective_chat.id, message, delay)
        else:
            try:
                await message.delete()
            except TelegramError:
                pass
        return True
    return False
