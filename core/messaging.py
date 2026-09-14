# -*- coding: utf-8 -*-
"""infra/messaging —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from telegram.error import BadRequest, RetryAfter, TelegramError
import asyncio
import time

# 定义时就需要的常量（默认参数等）—— 随定义一起搬，bot.py 里 re-export
RANK_NAME_MAX = 12

def _fmt_tpl(key, **kw):
    """按网页模板渲染消息；模板非法/为空时回退默认，绝不因占位符写错而崩。

    模板读取**必须走 `sget()`**：后台「按群改的提示文案」是靠 GROUP_SETTINGS 的
    群级覆盖实现的，而 `sget()` 是解析「群级覆盖 → 全局默认」的唯一入口。
    原先这里直接读全局变量（`hub.namespace().get(gname)`）→ **绕过群级覆盖**，
    后果是后台里按群改的 29 个模板文案**全部白改**，只有改全局才生效；
    不报错、不打日志，属于最典型的「改了没生效」。
    （2026-09-13 修，守卫见 test_msg_tpl_group_override.py）
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    MSG_TPL_DEFAULTS = hub.MSG_TPL_DEFAULTS
    SETTINGS_FIELDS = hub.SETTINGS_FIELDS
    sget = hub.sget
    gname = next((g for k, g, *_r in SETTINGS_FIELDS if k == key), None)
    tpl = sget(gname) if gname else None
    fallback = MSG_TPL_DEFAULTS[key]
    try:
        return (tpl or fallback).format(**kw)
    except Exception:
        try:
            return fallback.format(**kw)
        except Exception:
            # 连内置默认都渲染不了（占位符与调用方传的参数不匹配）→ 返回原文，绝不崩。
            # 上面那句 docstring 承诺「绝不因占位符写错而崩」，但原先 fallback.format()
            # 自己抛出去就没兜住了：少传一个占位符 = 整个 handler 挂掉。（2026-09-13 补）
            return fallback


def user_link(uid, name_html):
    """把（已转义的）展示名包成**蓝色可点文本链接** → 点名字直接跳到该玩家的 Telegram 资料页。

    2026-09-12 用户要求：「所有的积分榜或者能显示排名的东西，用户的名字都改成蓝色文本链接，
    像我当时改积分兑换那个兑换一样，就是点那个玩家的名称就跳转到他的页面」。
    用 Telegram 的 `tg://user?id=` 文本提及（text mention）实现：客户端渲染成蓝色、点击弹资料页，
    观感与「积分兑换」面板的蓝色 `<a href>` 一致。

    ⚠️ name_html 必须是**已经 HTML 转义**的展示名（get_name / html.escape 的产物），
       这里**不再 escape** —— 二次转义会把昵称里的 & < > 变成可见的 &amp; 实体。
    ⚠️ uid 必须 > 0：幽灵/占位 key（0、负数）拼出来的链接点不开，直接退回纯文本。

    ⚠️ 默认 **关闭**（`RANK_NAME_LINK=0` → 纯文本）：文本提及会**给被提及的人发通知**
       —— 2026-09-12 用户报「发积分排行，机器人艾特所有人群友」，就是它造成的。
       后台「积分系统 → 榜单人名显示蓝色可点链接」可手动开启（开启即接受会通知前 5 人）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    sget = hub.sget
    if not sget("RANK_NAME_LINK"):
        return name_html
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return name_html
    if uid <= 0:
        return name_html
    return f"<a href=\"tg://user?id={uid}\">{name_html}</a>"


def _in_bot_loop():
    """当前线程是否就在 bot 主事件循环里。

    用于同步函数判断「能不能跨线程回投」——在循环里跨线程回投同一个循环 = 自己等自己，
    必然等到超时（群管响应慢 8 秒/条的元凶）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _bot_loop = hub._bot_loop
    if not _bot_loop:
        return False
    try:
        return asyncio.get_running_loop() is _bot_loop
    except RuntimeError:
        return False   # 当前线程没有运行中的循环 → 不在 bot 循环里


async def safe_send(bot, cid, text, **kwargs):
    # 默认 HTML 解析：模板里的 <b> 生效；解析失败（昵称含 < 等）自动回退纯文本重发
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    kwargs.setdefault("parse_mode", "HTML")
    for attempt in range(2):
        try: return await bot.send_message(chat_id=cid, text=text, **kwargs)
        except RetryAfter as exc:
            if attempt == 0: await asyncio.sleep(min(exc.retry_after, 5)); continue
        except BadRequest as exc:
            if "parse entities" in str(exc).lower() and kwargs.get("parse_mode"):
                kwargs.pop("parse_mode"); continue  # HTML 解析炸了 → 纯文本重发
            logger.exception("发送消息失败: %s", cid); break
        except TelegramError:
            logger.exception("发送消息失败: %s", cid); break
    return None


def _open_tags_after(text, stack):
    """在 `stack`（进入时的未闭合标签栈）基础上扫描 `text`，返回结束时的未闭合标签栈。

    关闭标签时从栈顶向下找**最近的同名标签**并连同其内层一起弹出
    （`<b><code>x</code>` 的 `</code>` 应只弹掉 code，b 继续开着）。
    找不到同名标签的孤立关闭标签直接忽略——补一个多余的闭合标签会让
    Telegram 整条消息解析失败，比少闭合更糟。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _HTML_TAG_RE = hub._HTML_TAG_RE
    _TG_PAIR_TAGS = hub._TG_PAIR_TAGS
    stack = list(stack)
    for match in _HTML_TAG_RE.finditer(text):
        name = match.group(1).lower()
        if name not in _TG_PAIR_TAGS:
            continue
        if match.group(0).startswith("</"):
            for index in range(len(stack) - 1, -1, -1):
                if stack[index] == name:
                    del stack[index:]
                    break
        else:
            stack.append(name)
    return stack


def split_telegram_text(text, max_bytes=4000):
    """按 UTF-8 字节切分，避免中文结算消息超过 Telegram 的 4096 字节限制。

    切分时保证：① 不切断 <b>/</b> 等 HTML 标签的字面量；② 各分片标签自平衡
    （跨分片记住未闭合的标签栈，在下一片开头补开、结尾补闭）。

    2026-09-12 修（两个真实 bug，都有测试固化在 test_split_telegram_text.py）：
      · 原实现**只平衡 <b>**，但消息里实际用到 <code>（bot.py 内 108 处），
        典型现场是 `/定时任务状态` 逐群拼 `　✅ <code>{cid}</code> {群名}…`。
        群一多超过 4000 字节被切分后，第一片就成了「有 <code> 无 </code>」，
        Telegram 报 Unclosed tag → safe_send 降级纯文本 → 管理员看到满屏
        字面量 `<b>` `<code>` 尖括号。现按白名单平衡全部成对标签。
      · 末片原实现无条件补 `pending_before` 个闭合标签，而原文自带的闭合
        标签就在末片里 → 变成重复闭合 `…</b></b>`，同样触发解析失败。
        现改为按分片实际扫描结果补闭。

    注：按 UTF-8 字节切分对 Telegram 的 4096「字符」上限是**保守**的
    （任一字符的 UTF-8 字节数 ≥ 其 UTF-16 单元数），因此不会超限。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _open_tags_after = hub._open_tags_after
    parts, open_stack = [], []
    while True:
        prefix = "".join(f"<{tag}>" for tag in open_stack)
        budget = max_bytes - len(prefix.encode("utf-8"))
        if budget < 64:            # 栈深得离谱时兜底，别让预算被补开的标签吃光
            budget = max_bytes
        if len(text.encode("utf-8")) <= budget:
            break
        size, cut = 0, 0
        for index, char in enumerate(text):
            char_size = len(char.encode("utf-8"))
            if size + char_size > budget: break
            size += char_size; cut = index + 1
        # 优先在换行处切
        newline = text.rfind("\n", 0, cut)
        cut = newline if newline > 0 else cut
        # 避免切断 HTML 标签：若切点在 < 与 > 之间，回退到该 < 之前
        open_pos = text.rfind("<", 0, cut)
        close_pos = text.rfind(">", 0, cut)
        if open_pos > close_pos:
            cut = open_pos
        if cut <= 0:
            # 极端：单标签超长，按字节硬切保底
            size, cut = 0, 0
            for index, char in enumerate(text):
                char_size = len(char.encode("utf-8"))
                if size + char_size > budget: break
                size += char_size; cut = index + 1
        body = text[:cut]
        open_stack = _open_tags_after(body, open_stack)
        # 该分片：开头补上进入时仍打开的标签，结尾补闭扫完后仍打开的标签
        parts.append(prefix + body + "".join(f"</{tag}>" for tag in reversed(open_stack)))
        text = text[cut:].lstrip("\n")
    if text:
        prefix = "".join(f"<{tag}>" for tag in open_stack)
        final_stack = _open_tags_after(text, open_stack)
        parts.append(prefix + text + "".join(f"</{tag}>" for tag in reversed(final_stack)))
    return parts


async def safe_send_long(bot, cid, text, **kwargs):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    safe_send = hub.safe_send
    split_telegram_text = hub.split_telegram_text
    sent = []
    for index, part in enumerate(split_telegram_text(text)):
        part_kwargs = dict(kwargs)
        if index > 0: part_kwargs.pop("reply_markup", None)  # 只有第一段带键盘，后续段保留 parse_mode
        msg = await safe_send(bot, cid, part, **part_kwargs)
        if msg is None:
            logger.error("长消息发送失败，群 %s，第 %s 段未送达", cid, index + 1)
            return sent or None  # 返回已送达段（供自动删除回收）；全失败仍为 None
        sent.append(msg)
    return sent


async def safe_edit(bot, cid, msg_id, text, **kwargs):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    safe_edit = hub.safe_edit
    if not msg_id: return None
    kwargs.setdefault("parse_mode", "HTML")
    try: return await bot.edit_message_text(chat_id=cid, message_id=msg_id, text=text, **kwargs)
    except BadRequest as exc:
        if "parse entities" in str(exc).lower() and kwargs.get("parse_mode"):
            kwargs.pop("parse_mode")
            try: return await bot.edit_message_text(chat_id=cid, message_id=msg_id, text=text, **kwargs)
            except BadRequest as exc2:
                if "Message is not modified" not in str(exc2): logger.warning("编辑消息失败: %s", exc2)
            return None
        if "Message is not modified" not in str(exc): logger.warning("编辑消息失败: %s", exc)
    except RetryAfter as exc:
        await asyncio.sleep(min(exc.retry_after, 5))
        return await safe_edit(bot, cid, msg_id, text, **kwargs)
    except TelegramError: logger.exception("编辑消息失败")
    return None


async def safe_send_photo(bot, cid, photo, caption, **kwargs):   # wiring-ok: 未被调用的带退避发图包装（待清理）
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    for attempt in range(2):
        try: return await bot.send_photo(chat_id=cid, photo=photo, caption=caption, **kwargs)
        except RetryAfter as exc:
            if attempt == 0: await asyncio.sleep(min(exc.retry_after, 5)); continue
        except TelegramError:
            logger.exception("发送图片失败: %s", cid); break
    return None


async def safe_delete(bot, cid, msg_id):
    """删除消息；**遇限流必须重试**，返回 True=已删（或本来就没有），False=这次没删掉。

    2026-09-11 用户报「消息都不自动删除」的两个元凶之一就在这里：
    旧版 `except TelegramError: pass` 把 429（RetryAfter 是 TelegramError 子类）**静默吞掉**，
    而 `_flush_deletes` 随后又无条件把该条目清出队列 → 这条消息**永远删不掉了**。
    现在：429 退避重试最多 3 次；失败返回 False 由调用方（_flush_deletes）重新入队。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    if not msg_id:
        return True
    for attempt in range(3):
        try:
            await bot.delete_message(chat_id=cid, message_id=msg_id)
            return True
        except RetryAfter as exc:
            if attempt == 2:
                return False
            await asyncio.sleep(min(getattr(exc, "retry_after", 1) + 0.5, 25))
        except TelegramError as exc:
            msg = str(exc).lower()
            # 「消息不存在 / 已被删」= 目的已达成，算成功，别反复重排
            for _ok in ("message to delete not found", "message can't be deleted",
                        "message identifier is not specified", "message is not found"):
                if _ok in msg:
                    return True
            logger.warning("删除消息失败 cid=%s mid=%s: %s", cid, msg_id, exc)
            return False
        except Exception:
            return False
    return False


async def _flush_deletes(app):
    """删除队列里所有已到期消息；**删失败的重新入队**（最多重试 5 次），不再静默丢弃。

    旧版无条件 `_pending_deletes[:] = [未到期]`，把删失败的条目一并清出 → 消息永久残留。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _pending_deletes = hub._pending_deletes
    safe_delete = hub.safe_delete
    now = time.time()
    due = [q for q in _pending_deletes if q[2] <= now]
    if not due:
        return
    retry = []
    for q in due:
        try:
            cid, mid = int(q[0]), int(q[1])
        except (ValueError, TypeError, IndexError):
            continue
        tries = int(q[3]) if len(q) > 3 else 0
        if not await safe_delete(app.bot, cid, mid) and tries + 1 < 5:
            retry.append([cid, mid, now + 120, tries + 1])   # 2 分钟后再试
    _pending_deletes[:] = [q for q in _pending_deletes if q[2] > now] + retry


def restore_pending_deletes(app):
    """启动重放：把上次没删完的消息重新排程。已过期的立即补删，超过 1 天的陈条目直接丢弃。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _delete_tasks = hub._delete_tasks
    _pending_deletes = hub._pending_deletes
    logger = hub.logger
    safe_delete = hub.safe_delete
    schedule_delete_ids = hub.schedule_delete_ids
    if not _pending_deletes:
        return
    now = time.time()
    old, _pending_deletes[:] = list(_pending_deletes), []
    for q in old:
        try:
            cid, mid, due = int(q[0]), int(q[1]), float(q[2])
        except (ValueError, TypeError, IndexError):
            continue
        if now - due > 86400:
            continue
        if due <= now:
            async def _del_now(_cid=cid, _mid=mid):
                await hub.safe_delete(app.bot, _cid, _mid)
            try:
                t = asyncio.create_task(_del_now())
                _delete_tasks.add(t)
                t.add_done_callback(_delete_tasks.discard)
            except RuntimeError:
                _pending_deletes.append([cid, mid, due])   # 无 loop：退回队列等周期兜底
        else:
            schedule_delete_ids(app, cid, [mid], int(due - now) + 1)
    logger.info("已恢复 %d 条待删消息的删除排程", len(old))


def schedule_delete_ids(app, cid, ids, seconds):
    """延迟删除指定 message_id（用于只有 id、拿不到 Message 对象的场景，如原地编辑的下注面板）。

    队列随 bot_data 持久化：容器重启/重部署后由 restore_pending_deletes 重放，
    不再出现「消息说好自动删、重部署后永久残留」的问题。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _delete_tasks = hub._delete_tasks
    _flush_deletes = hub._flush_deletes
    _pending_deletes = hub._pending_deletes
    if seconds <= 0 or not ids: return
    if isinstance(ids, int): ids = [ids]
    ids = [int(i) for i in ids if i]
    if not ids: return
    due = time.time() + int(seconds)
    for mid in ids:
        # 同一消息重复排程 → 只留最新一条（榜单翻页续期、面板重发都会走到这里，
        # 不去重会堆出多条重复条目，白挨几次注定失败的删除）
        keep_q = []
        for q in _pending_deletes:
            try:
                same = int(q[0]) == int(cid) and int(q[1]) == int(mid)
            except (ValueError, TypeError, IndexError):
                same = False
            if not same:
                keep_q.append(q)
        keep_q.append([cid, mid, due])
        _pending_deletes[:] = keep_q
    if len(_pending_deletes) > 5000:
        del _pending_deletes[:-2000]   # 防异常堆积
    async def _del_later():
        await asyncio.sleep(seconds)
        await hub._flush_deletes(app)
    _coro = _del_later()
    try:
        t = asyncio.create_task(_coro)
        _delete_tasks.add(t)
        t.add_done_callback(_delete_tasks.discard)
    except RuntimeError:
        # 无事件循环（如网页线程调用）：条目已在队列，由周期兜底/启动重放接管。
        # ⚠️ 必须 close()：不然这个协程永远没被 await，Python 会报 RuntimeWarning，
        #    而且看上去像"排了删除却没执行"——很难查的假故障。
        _coro.close()


def schedule_delete(app, cid, msgs, seconds):
    """seconds 秒后自动删除 bot 发出的消息（0=不删）。msgs 可为单条 Message 或 Message 列表。
    用于：查询类回复（REPLY_DELETE_SECONDS）、游戏结算消息（SETTLE_DELETE_SECONDS）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    schedule_delete_ids = hub.schedule_delete_ids
    if seconds <= 0 or not msgs: return
    if not isinstance(msgs, (list, tuple)): msgs = [msgs]
    ids = [m.message_id for m in msgs if m is not None and getattr(m, "message_id", None)]
    schedule_delete_ids(app, cid, ids, seconds)


def cancel_scheduled_delete(cid, ids):
    """**撤销**这些消息已排入的自动删除队列（= 显式声明「永不删」）。

    为什么必须有它：后台把「查询回复自动删除(秒)」设成 0 表示**永久保留**，
    但「默认自动删除」补丁在发送那一刻已经按默认秒数排了一条 ——
    不撤销的话，用户把秒数设成 0 也照样被删（典型的「设置了不生效」）。
    与 `schedule_delete_ids` 共用同一个队列、(cid, mid) 同一套去重口径。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _pending_deletes = hub._pending_deletes
    if not ids:
        return
    if isinstance(ids, int):
        ids = [ids]
    want = set()
    for i in ids:
        try:
            want.add(int(i))
        except (TypeError, ValueError):
            continue
    if not want:
        return
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return
    keep = []
    for q in _pending_deletes:
        try:
            same = int(q[0]) == cid and int(q[1]) in want
        except (ValueError, TypeError, IndexError):
            same = False
        if not same:
            keep.append(q)
    _pending_deletes[:] = keep


def own_messages(app, cid, msgs, seconds):
    """【显式声明消息生命周期】「自动删除改显式声明」的统一出口 —— 调用方自己负责这批消息。

      seconds > 0  → 排入 N 秒后删除（覆盖补丁刚排的默认值，后写者胜）
      seconds <= 0 → **撤销**任何已排的删除 = 永久保留（后台把秒数设 0 时靠这条生效）

    替代原先「靠调用方函数名命中 _AUTODEL_OWN_FUNCS」的猜法：函数改名 / 被搬走，
    不会再让删除行为**悄悄变掉**（旧机制失配时不报错、不打日志）。
    直接调 Bot API 的场景仍可用行内 kwarg `autodel_own=True`；
    本函数专供「走了 reply_text / safe_send_long、只拿得到 Message 对象」的路径 ——
    因为 `Message.reply_text` 与 `Bot.send_message` **都不接受任意关键字参数**，kwarg 传不过去。

    msgs 可以是 Message / message_id / 它们的列表（这里统一归一化，省得每个调用方写一遍）。

    ⚠️ `app` 只在**排删除**时用得到；`seconds <= 0` 的撤销路径不需要它。
       因此 app 为空（手工构造的 context、测试桩）时**撤销照常生效**，只有排程被跳过 ——
       绝不因为拿不到 app 就抛 AttributeError 把整个命令带崩。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    logger = hub.logger
    schedule_delete_ids = hub.schedule_delete_ids
    cancel_scheduled_delete = hub.cancel_scheduled_delete
    if not msgs:
        return
    if not isinstance(msgs, (list, tuple, set)):
        msgs = [msgs]
    ids = []
    for m in msgs:
        mid = m if isinstance(m, int) else getattr(m, "message_id", None)
        if mid:
            ids.append(mid)
    if not ids:
        return
    try:
        secs = int(seconds or 0)
    except (TypeError, ValueError):
        secs = 0
    if secs > 0:
        if app is None:
            # 没有 application 就排不了删除（队列条目最终要靠 app.bot 去删）。
            # 不静默：留日志，别让「说好要删的消息没被回收」变成查不出的谜案。
            logger.warning("own_messages 缺少 application，跳过自动删除排程：cid=%s ids=%s", cid, ids)
            return
        schedule_delete_ids(app, cid, ids, secs)
    else:
        cancel_scheduled_delete(cid, ids)


async def send_settle(app, cid, text, kb=None, parse_mode="HTML", delete_after=None):
    """【新游戏必用】游戏结算/收尾消息统一出口：发送 + 自动按 SETTLE_DELETE_SECONDS 回收。

    以后写任何新游戏，结算消息一律走这个函数，别直接 reply_text/safe_send——
    结算自动删除在这里是默认行为，不用（也不会忘）另写 schedule_delete。
    回收时长由网页「通用与应急 → 游戏结算消息自动删除(秒)」控制，设 0 = 永久保留。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    own_messages = hub.own_messages
    safe_send_long = hub.safe_send_long
    sget = hub.sget
    secs = int(sget("SETTLE_DELETE_SECONDS") if delete_after is None else delete_after)
    kwargs = {"parse_mode": parse_mode}
    if kb is not None:
        kwargs["reply_markup"] = kb
    msgs = await safe_send_long(app.bot, cid, text, **kwargs)
    # 显式声明生命周期（不再靠「调用方函数名 = send_settle」猜）：
    #   secs > 0 → 排删除；secs == 0（后台设 0 = 永久保留）→ 撤销默认删除。
    own_messages(app, cid, msgs, secs)
    return msgs


async def send_settle_rank(app, cid, lines):
    """【结算榜单专用】把「累计盈利榜」单独发一条消息。

    2026-09-11 用户要求：「把所有游戏的结算画面中的（累计盈利榜）拆开发，不然一个界面太长了，
    有点刷屏的感觉」——结算正文保持精简，榜单另起一条，同样走 SETTLE_DELETE_SECONDS 回收。
    lines 为已渲染好的行列表；为空/None 则一条都不发（无榜单就不产生空白消息）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    send_settle = hub.send_settle
    if not lines: return None
    return await send_settle(app, cid, "\n".join(lines))


async def announce_turn(app, cid, text, old_id=None, seconds=None):
    """【回合提醒专用】把「轮到谁行动」单独发一条并定时回收，返回新消息 id。

    2026-09-12 用户要求：「把游戏轮到谁行动单独发一条出来提醒玩家（记得设置自动1分钟删除），
    然后就可以把牌桌文本那个该谁行动删除了」——
    牌桌正文从此只描述牌局状态（干净、不再每回合变长），提醒本身醒目且不长期占屏。

    为什么顺带删上一条提醒：一局里最密的德州/金花，10 秒内可能连过 3 个回合，
    只靠 60 秒定时回收会在屏幕上叠出 3 条「⏳ 轮到 X」——同一件事说三遍比不说还吵。
    因此每次发新提醒就删掉上一条（`old_id`），60 秒定时删除只是最后的兜底。

    ⛔ 与牌桌相反，这里**不存在自愈问题**：提醒是一次性的，删失败/发失败都只是少一条提醒，
    谁的回合由牌桌按钮 + 回合超时看门狗兜底，绝不允许因为提醒异常把牌局带崩。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    TURN_NOTICE_DELETE_SECONDS = hub.TURN_NOTICE_DELETE_SECONDS
    logger = hub.logger
    safe_delete = hub.safe_delete
    safe_send = hub.safe_send
    schedule_delete = hub.schedule_delete
    secs = TURN_NOTICE_DELETE_SECONDS if seconds is None else int(seconds)
    if old_id and not text:
        await safe_delete(app.bot, cid, old_id)   # 本轮无人行动（如已进摊牌）→ 只清掉过期提醒
        return None
    if not text:
        return None
    try:
        # 顺序与牌桌一致：**先发新、成功才删旧**。发送失败时旧提醒还在，不会出现「一条提醒都没有」
        msg = await safe_send(app.bot, cid, text, parse_mode="HTML")
        if msg:
            if old_id and old_id != msg.message_id:
                await safe_delete(app.bot, cid, old_id)
            if secs > 0:
                schedule_delete(app, cid, msg, secs)
            return msg.message_id
        return old_id   # 发失败 → 保留旧提醒的 id，下一轮还能接着删
    except Exception:
        logger.exception("回合提醒发送失败（已吞并，不影响牌局）")
        return old_id


async def send_reply(update, context, text, kb=None, parse_mode=None, delete_after=None):
    """【查询类命令必用】查询回复统一出口：发送 + 自动按 REPLY_DELETE_SECONDS 回收。

    以后写任何查询类命令（积分/战绩/排行/商城等），回复一律走这里——
    回复自动删除是默认行为，不用（也不会忘）另写 schedule_delete。
    时长由网页「积分系统 → 积分设置 → 查询回复自动删除(秒)」控制，0 = 永久保留。
    注意：用户发的命令本身按 POINTS_DELETE_SECONDS 在 _dispatch_alias 全局统一删，无需关心。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    own_messages = hub.own_messages
    safe_send_long = hub.safe_send_long
    sget = hub.sget
    secs = int(sget("REPLY_DELETE_SECONDS") if delete_after is None else delete_after)
    kwargs = {}
    if parse_mode is None:
        parse_mode = "HTML"   # 默认 HTML：<b>/<code> 生效；解析失败自动回退纯文本
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    if kb is not None:
        kwargs["reply_markup"] = kb
    target = getattr(update, "message", None)
    # ⚠️ 用 getattr 兜底：真实 handler 的 context 一定有 application，
    #    但手工构造的 context / 测试桩可能没有 —— 不能因为取 app 就把回复本身搞崩。
    app = getattr(context, "application", None)
    if target is None:
        # 按钮触发（CallbackQuery）没有可回复的消息 → 发到群里，**同样走 REPLY_DELETE_SECONDS**。
        # 2026-09-11 补：此前按钮触发的查询（如「赛季榜」）直接 safe_send_long，永不回收。
        msg = await safe_send_long(context.application.bot, update.effective_chat.id, text, **kwargs)
        if msg:
            own_messages(app, update.effective_chat.id, msg, secs)
        return msg
    try:
        reply = await target.reply_text(text, **kwargs)
    except BadRequest as exc:
        if "parse entities" in str(exc).lower() and kwargs.get("parse_mode"):
            kwargs.pop("parse_mode")  # 昵称含 < 等导致解析炸 → 纯文本重发
            reply = await target.reply_text(text, **kwargs)
        else:
            raise
    # 显式声明生命周期（不再靠「调用方函数名 = send_reply」猜）：
    #   secs > 0 → 排删除；secs == 0（后台设 0 = 永久保留）→ 撤销默认删除。
    # ⚠️ 只在真拿到回复消息时才声明：reply 为空 = 根本没发出去，
    #    谈不上「这批消息」的生命周期；同时也不必去碰 update.effective_chat
    #    （旧版 `if reply and secs > 0` 就是靠这个短路容忍了精简的 Update 桩）。
    if reply:
        own_messages(app, update.effective_chat.id, reply, secs)
    return reply


async def action_notice(cid, app, uid, desc):
    """游戏内「下注/加注/比牌」等即时提示：发一条、10 秒后删。

    **必须走 schedule_delete_ids**（持 task 引用 + 队列持久化 + 60s 兜底）——
    旧版裸 `asyncio.create_task(delete_later())` 不保存引用，任务可能在跑完前被事件循环 GC，
    「说好 10 秒删」的提示就永远留在群里（2026-09-11 用户报「消息都不自动删除」的元凶之一）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    get_name = hub.get_name
    safe_send = hub.safe_send
    schedule_delete_ids = hub.schedule_delete_ids
    message = await safe_send(app.bot, cid, f"🎲 {await get_name(app, uid)} {desc}")
    schedule_delete_ids(app, cid, message.message_id if message else None, 10)


def schedule_notice_delete(app, cid, message, kind="panel"):
    """给机器人「提示/播报」类消息挂后台自动回收（2026-09-11 用户报「超时自动过牌」「亮牌」等不删）。

    kind="panel"  过程类（超时自动行动提示、亮牌按钮卡、比牌公告）→ PANEL_DELETE_SECONDS
    kind="settle" 结算类播报（亮牌收池播报、结算失败提示）→ SETTLE_DELETE_SECONDS
    秒数为 0 表示不删（与后台设置语义一致）；message 为空时静默跳过。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    schedule_delete = hub.schedule_delete
    sget = hub.sget
    if not message:
        return
    secs = sget("SETTLE_DELETE_SECONDS") if kind == "settle" else sget("PANEL_DELETE_SECONDS")
    try:
        secs = int(secs or 0)
    except (TypeError, ValueError):
        secs = 0
    if secs > 0:
        schedule_delete(app, cid, message, secs)


def clip_name(name, n=RANK_NAME_MAX):
    """榜单/表格里截断过长昵称，避免把消息气泡撑宽（2026-09-11 用户报「这么宽吗」）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    RANK_NAME_MAX = hub.RANK_NAME_MAX
    s = (name or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _safe_html_clip(name, n=RANK_NAME_MAX):
    """截断**已 HTML 转义**的展示名，且绝不把实体的 `&amp;` 截成半截。

    为什么必须单独一个函数：`get_name()` 返回的名字已经转义过（`&` → `&amp;`）。
    直接 clip_name 到 12 字，正好切在 `&amp;` 中间就会产出 `&am` 这种非法实体，
    整条消息（含榜单）HTML 解析失败、被 safe_send 掉回纯文本重发 —— 玩家看到的是
    一屏裸露的 `<a href="tg://user?id=...">`，比不截断还难看。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    RANK_NAME_MAX = hub.RANK_NAME_MAX
    clip_name = hub.clip_name
    t = clip_name(name, n)
    if "&" in t and ";" not in t.rsplit("&", 1)[-1]:
        t = t.rsplit("&", 1)[0]      # 丢掉残缺的实体尾巴
    return t


async def announce_sweep(context):
    """定时群公告（每 60 秒）：北京时间到 ANNOUNCE_TIME 后向全部授权群推一条，一天只发一次。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    logger = hub.logger
    now_bj = hub.now_bj
    save_data = hub.save_data
    sget = hub.sget
    if not sget("ANNOUNCE_ENABLED"):
        return

    try:
        hh, mm = (int(x) for x in str(sget("ANNOUNCE_TIME")).strip().split(":")[:2])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return
    except Exception:
        return
    now = now_bj()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < target or hub.announce_last_date == now.strftime("%Y-%m-%d"):
        return
    txt = str(sget("ANNOUNCE_TEXT") or "").strip()
    if not txt:
        return
    hub.set("announce_last_date", now.strftime("%Y-%m-%d"))   # 先记再发：丢一条公告好过刷屏重发
    save_data()
    n = 0
    for cid in sorted(AUTHORIZED_GROUPS):
        try:
            # ⚠️ autodel_keep：「定时群公告」是**公示**，必须留到下一次公告（甚至更久）。
            #   否则「默认自动删除」会在几分钟后把它收走 —— 一天只发一次的公告活 5 分钟，
            #   等于没发（与 2026-09-12 用户报的「抽奖信息被删」同一类问题）。
            await context.bot.send_message(
                cid, txt.replace("{date}", hub.announce_last_date), autodel_keep=True)
            n += 1
        except Exception:
            logger.exception("定时公告推送失败 cid=%s", cid)
    try:
        await context.bot.send_message(
            ADMIN_USER_ID, f"📣 定时群公告已推送到 {n}/{len(AUTHORIZED_GROUPS)} 个群。")
    except Exception:  # silent-ok: 各群推送失败已逐群记 exception；管理员汇总发不出无妨
        pass


async def _bot_api(bot, snake, camel, **params):
    """调用 Telegram Bot API —— 兼容 PTB 尚未封装的新方法。

    优先原生 `Bot.<snake>`；本库没有就退回通用出口 `Bot.do_api_request("<camel>")`。
    这样 PTB 升级前后都不用改业务代码。
    """
    fn = getattr(bot, snake, None)
    if callable(fn):
        return await fn(**params)
    return await bot.do_api_request(camel, api_kwargs=dict(params))


def _spawn_background(coro):
    """把一个协程丢到后台跑，并持引用防 GC。返回 Task 或 None（无事件循环时）。

    通用工具：任何"耗时长但不该阻塞用户"的任务（批量同步、批量导出…）都走这里。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _BG_TASKS = hub._BG_TASKS
    logger = hub.logger
    try:
        t = asyncio.create_task(coro)
    except RuntimeError:
        logger.debug("无事件循环，后台任务未启动", exc_info=True)
        return None
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return t


def _is_network_error(err):
    """判断是否为网络层异常（平台抖动/容器重启瞬断，PTB 会自动重连，属无害噪音）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _NET_ERR_NAMES = hub._NET_ERR_NAMES
    if err is None:
        return False
    if type(err).__name__ in _NET_ERR_NAMES:
        return True
    s = f"{err!r}"
    return any(k in s for k in ("httpx", "ReadError", "ConnectError", "Connection aborted",
                                "Connection reset", "Server disconnected"))


def _net_error_tick(window=300, threshold=3):
    """记录一次网络异常，返回 True 表示达到告警门槛（窗口内第 threshold 次）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _net_err_log = hub._net_err_log
    now = time.time()
    _net_err_log[:] = [t for t in _net_err_log if now - t < window]
    _net_err_log.append(now)
    return len(_net_err_log) >= threshold
