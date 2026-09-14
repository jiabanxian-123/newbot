# -*- coding: utf-8 -*-
"""infra/web —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

# 网页后台各功能域的 Mixin（2026-09-13 从本文件 _AdminHandler 拆出，见 core/web_handlers/）
from core.web_handlers import (  # noqa: E402
    ToggleHandlers, PointsHandlers, MemberHandlers, InviteHandlers, LotteryHandlers, AdminHandlers, MiscHandlers,
)

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
from urllib.parse import parse_qs, quote, urlparse
import asyncio
import html
import json
import os
import re
import secrets
import threading
import time

# 定义时就需要的常量（默认参数等）—— 随定义一起搬，bot.py 里 re-export
WEB_LIST_PAGE_SIZE = 20

def _hash_web_pwd(pwd, salt):
    """后台密码摘要（pbkdf2-sha256）。落盘只存它，明文只活在内存里。"""
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", str(pwd).encode("utf-8"), str(salt).encode("utf-8"), 120000).hex()


def _pwd_ok(pwd):
    """校验后台登录密码：有 hash 就用 hash 比对，没有（首次/旧明文存档）才退回到明文比对。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _hash_web_pwd = hub._hash_web_pwd
    _web_password = hub._web_password
    _web_password_hash = hub._web_password_hash
    _web_salt = hub._web_salt
    try:
        if _web_password_hash and _web_salt:
            return secrets.compare_digest(_hash_web_pwd(pwd, _web_salt), _web_password_hash)
    except Exception:
        return False
    return bool(pwd) and secrets.compare_digest(str(pwd), str(_web_password))


def _client_ip(handler):
    """⑲ 取真实客户端 IP：反代场景（Northflank ingress 等）socket 地址恒为代理 IP，
    若直接用它做登录限速键，一人试错就会锁死全部管理员。

    规则：有 X-Forwarded-For 取最后一段（ingress 在末尾追加的真实连接来源）；
    无 XFF（直连）用 socket 地址。注意：直连部署时 XFF 可被伪造绕过限速，
    本 bot 部署在 Northflank（必经 ingress），信任 XFF 是正确取舍。
    """
    try:
        xff = (handler.headers.get("X-Forwarded-For") or "").split(",")
        for seg in reversed(xff):
            seg = seg.strip()
            if seg:
                return seg
    except Exception:
        pass
    try:
        return handler.client_address[0]
    except Exception:
        return "unknown"


def _resolve_web_uid(cid, raw):
    """把网页加减分表单里的「用户」解析成 uid。返回 (uid, 错误信息)。

    2026-09-12 用户报「网页端积分加减分无效根本用不了」：后端路由实测完全正常，
    真正卡住的是**选人**——原表单只有一个 `input type=number list=datalist`，
    移动端浏览器对 number+datalist 支持极差（下拉根本不弹），而玩家不可能手输 10 位数字 ID，
    于是这功能实际用不了。现在首页面改用原生 <select> 选成员，本函数只负责兜底入口：
    允许直接填 **数字 ID / @用户名 / 昵称**，任一种都认。

    注意：昵称匹配优先在本群范围内找（避免把别群同名玩家改错分），唯一命中才接受。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    game_chips = hub.game_chips
    member_profiles = hub.member_profiles
    user_names = hub.user_names
    raw = (raw or "").strip()
    if not raw:
        return 0, "请先选择群组并从下拉里挑一个成员（或在「手填用户」里填 ID）"
    if raw.lstrip("-").isdigit():
        return int(raw), ""
    key = raw.lstrip("@").strip().lower()
    if not key:
        return 0, f"「{raw}」不是有效的用户标识"
    local = set(game_chips.get(cid, {})) | set(member_profiles.get(cid, {}))
    for pool, where in ((local, "本群"), (set(user_names.keys()), "全部群")):
        exact = [u for u in pool if str(user_names.get(u, "")).strip().lower() == key]
        if len(exact) == 1:
            return exact[0], ""
        part = [u for u in pool if key in str(user_names.get(u, "")).strip().lower()]
        if len(part) == 1:
            return part[0], ""
        if len(part) > 1:
            return 0, f"{where}内有 {len(part)} 个昵称含「{raw}」，请改用数字 ID 或 @用户名"
    return 0, f"在群 {cid} 里找不到「{raw}」，请改用数字 ID 或在群里让他先发一条消息"


def _web_page_bounds(total, page, per=WEB_LIST_PAGE_SIZE):
    """网页列表分页边界（纯函数，便于测试）：返回 (start, end, page, pages)。

    page 为 1 基；越界自动夹紧到 [1, pages]；total=0 时 pages=1、返回空切片。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    WEB_LIST_PAGE_SIZE = hub.WEB_LIST_PAGE_SIZE
    try:
        per = max(1, int(per or WEB_LIST_PAGE_SIZE))
    except (TypeError, ValueError):
        per = WEB_LIST_PAGE_SIZE
    total = max(0, int(total or 0))
    pages = max(1, (total + per - 1) // per)
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        page = 1
    page = max(1, min(page, pages))
    # end 夹到 total：空表返回 (0,0,...)，末页不多算（切片更精确）
    return (page - 1) * per, min(page * per, total), page, pages


async def _redeem_buy_cb(q, idx, context):
    """蓝色按钮点一下直接兑换：idx=上架商品编号（与列表消息一致）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    REDEEM_CMD = hub.REDEEM_CMD
    _redeem_execute = hub._redeem_execute
    _redeem_gate = hub._redeem_gate
    redeem_goods = hub.redeem_goods
    cid, uid = q.message.chat.id, q.from_user.id
    gate = _redeem_gate()
    if gate:        await q.answer(gate, show_alert=True); return
    items = [x for x in redeem_goods if x.get("on", True)
             and (not x.get("target_groups") or cid in x["target_groups"])]
    if not (1 <= idx <= len(items)):
        await q.answer("❌ 商品不存在或已下架，重新发「%s」看最新列表" % REDEEM_CMD, show_alert=True); return
    err = await _redeem_execute(context, cid, uid, items[idx - 1])
    if err:
        await q.answer(err, show_alert=True)
    else:
        await q.answer("🎉 兑换成功！")


async def cmd_webcode(update, context):
    """后台登录验证码（管理员）：bot 私聊推送失败时的备用取码通道。

    Telegram 不允许 bot 主动给「从未私聊过」的用户发消息，此时网页端拿不到码，
    用这条命令主动索取即可——命令是用户发起的，不受该限制。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_bot_admin = hub.is_bot_admin
    send_reply = hub.send_reply
    web_pending_otp = hub.web_pending_otp
    if not is_bot_admin(update.effective_user.id):
        await send_reply(update, context, "❌ 仅机器人管理员可用。"); return
    now = time.time()
    alive = [(k, v) for k, v in web_pending_otp.items() if v["exp"] > now]
    if not alive:
        await send_reply(update, context, 
            "当前没有待验证的登录请求。\n\n"
            "用法：先在网页端输入密码 → 再回来发 /网页码 取验证码。")
        return
    _tok, rec = alive[-1]
    left = int(rec["exp"] - now)
    await send_reply(update, context, 
        f"🔐 <b>后台登录验证码</b>\n\n"
        f"验证码：<code>{rec['code']}</code>\n"
        f"来源 IP：<code>{rec['ip']}</code>\n"
        f"剩余有效：{left} 秒\n\n"
        f"⚠️ 不是你本人操作请立即改后台密码。",
        parse_mode="HTML")


async def cmd_weblogin(update, context):
    """后台一键登录（管理员）：校验身份后私聊发一次性登录链接，点开即进后台，免密码免验证码。

    链接 2 分钟有效、单次使用；新链接会作废旧链接。需先在网页「通用与应急」
    配置「后台公网地址」（WEB_BASE_URL），否则 bot 不知道该拼什么域名。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_bot_admin = hub.is_bot_admin
    send_reply = hub.send_reply
    sget = hub.sget
    web_magic_tokens = hub.web_magic_tokens
    uid = update.effective_user.id
    if not is_bot_admin(uid):
        await send_reply(update, context, "⛔ 仅机器人管理员可用")
        return
    base = (sget("WEB_BASE_URL") or "").strip().rstrip("/")
    if not base:
        await send_reply(update, context, 
            "⚠️ 还没配置后台地址，一键登录不可用。\n\n"
            "请先用密码登录网页后台 → 「通用与应急」→「后台公网地址」\n"
            "填你的后台访问地址（如 https://xxx.northflank.app），保存后再来。")
        return
    # 同一管理员旧 token 一律作废
    now = time.time()
    for k in [k for k, v in web_magic_tokens.items() if v["uid"] == uid or v["exp"] < now]:
        web_magic_tokens.pop(k, None)
    token = secrets.token_urlsafe(32)
    web_magic_tokens[token] = {"uid": uid, "exp": now + 120}
    url = f"{base}/magic?token={token}"
    try:
        await context.bot.send_message(chat_id=uid, text=(
            "🔐 <b>后台一键登录</b>\n\n"
            f"<a href='{url}'>👉 点这里直接登录</a>\n\n"
            "· 2 分钟内有效，仅可使用一次\n"
            "· 点开即进后台，无需密码\n"
            "· 不是你本人操作请忽略"),
            parse_mode="HTML", disable_web_page_preview=True)
        if update.effective_chat.id != uid:
            await send_reply(update, context, "✅ 登录链接已发到你的私聊（2 分钟内有效）")
    except Exception:
        await send_reply(update, context, 
            "⚠️ 链接发送失败（你可能从未私聊过本机器人）。\n"
            "请先私聊我发 /start，然后再发 /后台。")


def _parse_multipart(raw, content_type):
    """极简 multipart/form-data 解析（积分导入文件上传用）。
    返回 {字段名: 字符串值 或 (文件名, bytes)}。"""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m: return {}
    boundary = ("--" + m.group(1)).encode()
    fields = {}
    for part in raw.split(boundary):
        # 只掐掉协议规定的「一个」前导 CRLF 与「一个」尾随 CRLF —— 不能用 strip()：
        # 空值字段的「头 / 空行 / 值(空)」里那个空行会被一并吃掉，于是
        # b"\r\n\r\n" not in part 成立 → **整条字段被静默丢弃**（后台表现为「未收到文件」）。
        # 同理，文件末尾自带的 CRLF（Excel 存的 CSV 就是 CRLF 换行）也会被多咬掉一个。
        if part.startswith(b"\r\n"): part = part[2:]
        if part.endswith(b"\r\n"): part = part[:-2:]
        if not part or part in (b"--", b"--\r\n"): continue
        if b"\r\n\r\n" not in part: continue
        head, _, value = part.partition(b"\r\n\r\n")
        headers = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]*)"', headers)
        fname = re.search(r'filename="([^"]*)"', headers)
        key = name.group(1) if name else ""
        if not key: continue
        if fname and fname.group(1):
            fields[key] = (fname.group(1), value)
        else:
            fields[key] = value.decode("utf-8", "replace")
    return fields
# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

class _Req:
    """一次请求的上下文，交给路由处理函数用。

        为什么要有它：处理函数是从 do_GET / do_POST 的 if 分支里搬出来的，
        搬出来之后就看不到原来的局部变量（path / qs / form …）了。
        把这次请求需要的东西装进一个对象传过去，处理函数开头解包一下即可 ——
        这样**函数体可以一个字都不改**，搬运出错的风险最低。

        为什么定义在这里而不是模块级：抽缝守卫要求 core.web 的每个模块级
        名字都必须在 bot 里有同名 re-export（保证 bot.xxx 是同一对象）。
        这只是 web 层内部的小工具，不该占 bot 的命名空间，所以留在里面。
        """
    __slots__ = ("path", "qs", "saved", "bad", "note", "err", "raw", "form")

    def __init__(self, path="", raw=b""):
        self.path = path
        self.qs = {}
        self.saved = False
        self.bad = False
        self.note = ""
        self.err = ""
        self.raw = raw
        self.form = {}

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

# ── 后台共享样式（2026-09-14 从 _page() 内嵌的 ~240 行 CSS 抽成独立文件，体检报告 ④）──
_APP_CSS_CACHE = {}


def _app_css():
    """后台共享样式：从 `core/pages/static/app.css` 读取（读一次缓存）。

    改样式改 .css 文件即可，不用碰 Python —— 消除「改个颜色要动代码」的土壤。
    """
    css = _APP_CSS_CACHE.get("v")
    if css is None:
        import os
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "pages", "static", "app.css"), encoding="utf-8") as f:
            css = f.read()
        _APP_CSS_CACHE["v"] = css
    return css


def _page(title, sidebar_active, body):
    """阿福风格布局：左侧深色菜单栏（分组可折叠子页面）+ 右侧内容区，窄屏折叠为顶部横排。"""
    sidebar_active = sidebar_active or ""
    _gcid = hub.cur_cid()                                        # 当前正在配置的群（0=全局默认）
    _gact = ("/page/" + sidebar_active) if sidebar_active else "/page/dashboard"
    _gq = (f"?cid={_gcid}" if _gcid else "")                 # 左侧菜单链接带上群，切页不串台
    _gbanner = ("" if not _gcid else
                "<div style='margin:0 0 14px;padding:10px 14px;border-radius:10px;"
                "background:rgba(var(--acr),.12);border:1px solid rgba(var(--acr),.3);font-size:13px;"
                "color:#d9d3f0'>🏷 正在为 <b>" + _esc(hub.chat_name_cache.get(_gcid) or "该群")
                + "</b>（<b>" + str(_gcid) + "</b>）配置 · 只保存与该群不同的项，"
                "未单独设置的项自动继承全局默认；标有「⚑ 群专属」的项才是该群已覆盖的</div>")
            # 群级页面：给所有 /save 表单自动补一个 cid 隐藏字段，保存才知道往哪个群写
            # （表单有十几处手写，逐个加容易漏；这里用一次 JS 统一注入，零遗漏）
    _gjs = ("<script>var _GCID=" + str(_gcid) + ";"
            "document.addEventListener('DOMContentLoaded',function(){if(!_GCID)return;"
            "document.querySelectorAll(\"form[action='/save']\").forEach(function(f){"
            "if(f.querySelector(\"input[name='cid']\"))return;"
            "var i=document.createElement('input');i.type='hidden';i.name='cid';i.value=_GCID;"
            "f.appendChild(i);});});</script>")
    _thm_key = hub.SETTINGS_SNAPSHOT.get("ui_theme") if hub.SETTINGS_SNAPSHOT.get("ui_theme") in hub._UI_THEMES else "purple"
    _ac, _ac2, _actx, _bg, _side, _card, _input, _border, _thead, _hover = hub._UI_THEMES[_thm_key]
    _acr = ",".join(str(int(_ac[i:i + 2], 16)) for i in (1, 3, 5))   # 主色的 R,G,B（供 rgba(var(--acr),x)）
    _bgr = ",".join(str(int(_card[i:i + 2], 16)) for i in (1, 3, 5))  # 卡片底色的 R,G,B（玻璃条用）
    items = []
    child_of = {ck: pk for pk, cks in hub.SIDEBAR_CHILDREN.items() for ck in cks}
    meta = {g[0]: (g[1], g[2]) for g in hub.SETTINGS_GROUPS}

    def _render_group(gkey):
        name, icon = meta.get(gkey, (gkey, "•"))
        active_now = sidebar_active == gkey or sidebar_active.startswith(gkey + "/")
        subpage_subs = hub.SUBPAGES.get(gkey) or []
        child_groups = [(ck, meta.get(ck, (ck, "•"))) for ck in hub.SIDEBAR_CHILDREN.get(gkey, [])]
        if subpage_subs or child_groups:
            subs = []
                    # 父组只有"挂子组"（如德州挂赛季）而无自身子页时，父组菜单变折叠开关，
                    # 原设置页会失去入口 → 子菜单第一位固定补"XX设置"链接
            if not subpage_subs and child_groups:
                pcls = "active" if sidebar_active == gkey else ""
                subs.append(f"<a class='{pcls}' href='/page/{gkey}{_gq}'>⚙️ {name}设置</a>")
            for skey, sname in subpage_subs:
                cls = "active" if sidebar_active == f"{gkey}/{skey}" else ""
                subs.append(f"<a class='{cls}' href='/page/{gkey}/{skey}{_gq}'>{sname}</a>")
            for ck, (cname, cicon) in child_groups:
                ccls = "active" if sidebar_active == ck or sidebar_active.startswith(ck + "/") else ""
                subs.append(f"<a class='{ccls}' href='/page/{ck}{_gq}'>{cname}</a>")
            items.append(
                f"<details{' open' if active_now else ''}>"
                f"<summary class='{'active' if active_now else ''}'>{icon}<span>{name}</span></summary>"
                f"<div class='sub'>{''.join(subs)}</div></details>")
        else:
            cls = "item active" if active_now else "item"
            items.append(f"<a class='{cls}' href='/page/{gkey}{_gq}'>{icon}<span>{name}</span></a>")

    def _sec_sort(keys):
        ks = [k for k in keys if k in meta and k not in child_of]
        ks.sort(key=lambda k: hub.SIDEBAR_ORDER.index(k) if k in hub.SIDEBAR_ORDER else 999)
        if "dashboard" in ks:  # 群体总览固定第一
            ks.remove("dashboard"); ks.insert(0, "dashboard")
        return ks

    rendered = set()
    for sec_name, sec_keys in hub.SIDEBAR_SECTIONS:
        ks = _sec_sort(sec_keys)
        if not ks: continue
        items.append(f"<div class='grp-title'>{sec_name}</div>")
        for k in ks:
            _render_group(k); rendered.add(k)
    others = [g[0] for g in hub.SETTINGS_GROUPS if g[0] not in child_of and g[0] not in rendered]
    if others:
        items.append("<div class='grp-title'>其他</div>")
        for k in others: _render_group(k)
    return ("<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{title} - 机器人后台</title><style>"
            f":root{{--ac:{_ac};--ac2:{_ac2};--acr:{_acr};--ac-tx:{_actx};"
            f"--bg:{_bg};--side:{_side};--card:{_card};--input:{_input};"
            f"--border:{_border};--thead:{_thead};--hover:{_hover};--bgr:{_bgr}}}"
            + _app_css()
            + _SEARCH_CSS
            + "</style></head><body>"
                    # 顶部 header
            "<header class='hd'>"
            "<button class='burger' onclick=\"document.querySelector('.side').classList.toggle('open');"
            "document.querySelector('.backdrop').classList.toggle('show')\" aria-label='菜单'>☰</button>"
            f"<div class='logo'>🤖 机器人后台</div>"
            f"<div class='crumb'>· {_esc(title)}"
            + (f" · 群 {_esc(hub.chat_name_cache.get(_gcid) or str(_gcid))}" if _gcid else "")
            + "</div>"
                    # 群切换器：切到某群后，所有设置页读写的都是该群的专属值（未单独设置的项继承全局）
            + ("<form method='get' class='gsel' action='" + _gact + "'>"
               "<span class='gl'>🌐 正在配置</span>"
               "<select name='cid' onchange='this.form.submit()' "
               "title='选择要配置的群；选「全局默认」则改动作用于所有群'>"
               + "<option value='0'" + (" selected" if not _gcid else "") + ">全局默认（所有群）</option>"
               + _group_options(_gcid) + "</select></form>")
            + _search_box()
            + "<div class='right'><span class='rlbl'>机器人后台</span>"
            + "".join(f"<a class='dot{' cur' if k == _thm_key else ''}' title='主题：{k}' "
                      f"href='/theme/{k}?back={quote(('/page/' + sidebar_active if sidebar_active else '/') + _gq)}' "
                      f"style='background:{v[0]}'></a>" for k, v in hub._UI_THEMES.items())
            + "</div>"
            "</header>"
            "<div class='backdrop' onclick=\"document.querySelector('.side').classList.remove('open');"
            "this.classList.remove('show')\"></div>"
            "<div class='wrap'>"
            f"<nav class='side'>{''.join(items)}</nav>"
            f"<main class='main'>{_gbanner}{body}</main>{_id_picker_js()}{_sort_js()}{_GUARD_JS}{_gjs}</div>"
            "<button class='totop' id='_totop' title='回到顶部' aria-label='回到顶部'"
            " onclick=\"window.scrollTo({top:0,behavior:'smooth'})\">↑</button>"
            "<script>(function(){"
            "var b=document.getElementById('_totop');"
            "if(b){window.addEventListener('scroll',function(){"
            "b.classList.toggle('show',window.scrollY>260)},{passive:true});}"
            "document.querySelectorAll('.main table').forEach(function(t){"
            "if(t.parentElement&&String(t.parentElement.className).indexOf('tblwrap')>=0)return;"
            "var w=document.createElement('div');w.className='tblwrap';"
            "t.parentNode.insertBefore(w,t);w.appendChild(t);});})();</script>"
            + _settings_search_block()
            + "<footer class='ft'>© 机器人后台</footer>"
            "</body></html>").encode("utf-8")

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _sort_js():
    """表格列头点击排序（通用）：带 data-s 的 th 可点，数字列按数值、其余按中文排序。"""
    return ("<script>document.addEventListener('click',function(e){"
            "var th=e.target.closest('th[data-s]');if(!th)return;"
            "var tb=th.closest('table');if(!tb)return;"
            "var idx=Array.prototype.indexOf.call(th.parentNode.children,th);"
            "var asc=th.dataset.asc!=='1';"
            "tb.querySelectorAll('th[data-s]').forEach(function(o){"
            "o.textContent=o.textContent.replace(/[▲▼]\\s*$/,'');delete o.dataset.asc;});"
            "th.textContent=th.textContent.replace(/[▲▼]\\s*$/,'')+(asc?' ▲':' ▼');"
            "th.dataset.asc=asc?'1':'0';"
            "var rows=[].slice.call(tb.rows).filter(function(r){"
            "return r.cells.length&&r.cells[0].tagName==='TD';});"
            "rows.sort(function(a,b){"
            "var x=a.cells[idx].innerText.trim(),y=b.cells[idx].innerText.trim();"
            "var nx=parseFloat(x.replace(/,/g,'')),ny=parseFloat(y.replace(/,/g,''));"
            "if(!isNaN(nx)&&!isNaN(ny))return asc?nx-ny:ny-nx;"
            "return asc?x.localeCompare(y,'zh'):y.localeCompare(x,'zh');});"
            "rows.forEach(function(r){tb.appendChild(r);});});</script>")

# ── 设置项搜索（2026-09-14）─────────────────────────────────────────────
# 病根（后台体检报告 ②）：272 个设置项分在 21 个分组、跨 29 个页面，**没有搜索框**，
# 想改「签到送多少积分」得先记住它在哪个菜单哪一页哪一节。
# 做法：把「键 → 所在页面」索引**内嵌**进页面（与当前代码同源，不会读到过期缓存），
# 顶栏搜索框输入即过滤，点结果 → 跳到该设置所在页并高亮那一项。
# 只读渲染：索引从 SETTINGS_FIELDS 现算，不改任何现有 HTML 结构与业务逻辑。

_SEARCH_CSS = (
    ".sbox{position:relative;display:inline-flex;align-items:center;margin-left:10px}"
    ".sbox input{width:180px;min-width:0;padding:5px 10px 5px 26px;border-radius:9px;"
    "border:1px solid var(--border);background:var(--input);color:inherit;font-size:13px}"
    ".sbox:before{content:'\2315';position:absolute;left:9px;opacity:.6;font-size:14px;pointer-events:none}"
    ".sdrop{position:absolute;top:100%;left:0;margin-top:6px;width:430px;max-height:340px;overflow:auto;"
    "background:var(--card);border:1px solid var(--border);border-radius:12px;z-index:70;display:none;"
    "box-shadow:0 12px 30px rgba(0,0,0,.45)}"
    ".srow{display:flex;align-items:center;gap:8px;padding:8px 11px;border-bottom:1px solid var(--border);"
    "text-decoration:none;color:inherit;font-size:13px}"
    ".srow:hover{background:rgba(var(--acr),.14)}"
    ".srow b{font-weight:500}"
    ".srow .sg{color:#a9a3c4;font-size:11px;margin-left:auto;white-space:nowrap}"
    ".srow .ss{font-style:normal;font-size:10px;padding:1px 6px;border-radius:6px;"
    "background:rgba(var(--acr),.18);color:#c4b5fd;border:1px solid rgba(var(--acr),.35)}"
    ".sni{padding:11px;font-size:13px;color:#a9a3c4}"
    "._hl{outline:2px solid var(--ac2);outline-offset:3px;border-radius:8px}"
    "@media(max-width:768px){.sbox input{width:120px}}"
)


def _search_box():
    """顶栏「设置项搜索」输入框（结果下拉容器同在这）。"""
    return ("<div class='sbox'><input id='_sbox' type='search' autocomplete='off' "
            "placeholder='搜索设置项…' aria-label='搜索设置项'>"
            "<div class='sdrop' id='_sdrop'></div></div>")


def _settings_search_index():
    """全部可搜设置项的「键 → 所在页面」索引（纯读 SETTINGS_FIELDS，不落盘）。

    跳过 ftype == "sep"（那是组内分段小标题，不是设置项）。
    返回 list[dict]，字段：k=设置键 / l=显示名 / t=分组标题 / u=页面URL / s=是否可群级。
    """
    out = []
    for _f in hub.SETTINGS_FIELDS:
        _key, _label, _ftype, _grp = _f[0], _f[2], _f[3], _f[6]
        if _ftype == "sep" or not _grp:
            continue
        _base, _s, _sub = str(_grp).partition("/")
        out.append({"k": _key, "l": _label, "t": hub._grp_title(_grp),
                    "u": "/page/" + _base + ("/" + _sub if _sub else ""),
                    "s": 1 if _key in hub.GROUP_SCOPED_KEYS else 0})
    return out


_SEARCH_JS = """<script>(function(){
var box=document.getElementById('_sbox'),drop=document.getElementById('_sdrop');
if(!box||!drop)return;
var IDX=null;
function esc(s){return String(s).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function load(){if(IDX)return IDX;var el=document.getElementById('_setidx');
  try{IDX=el?JSON.parse(el.textContent||'[]'):[];}catch(e){IDX=[];}return IDX;}
function render(q){q=(q||'').trim().toLowerCase();
  if(!q){drop.style.display='none';drop.innerHTML='';return;}
  var all=load(),out=[],i,x;
  for(i=0;i<all.length;i++){x=all[i];
    if((x.l+' '+x.k+' '+x.t).toLowerCase().indexOf(q)>=0){out.push(x);if(out.length>=40)break;}}
  if(!out.length){drop.innerHTML="<div class='sni'>没有匹配的设置项</div>";
    drop.style.display='block';return;}
  var h='',gq=(typeof _GCID!=='undefined'&&_GCID)?('?cid='+_GCID):'';
  for(i=0;i<out.length;i++){x=out[i];
    h+="<a class='srow' href='"+x.u+gq+'#f_'+encodeURIComponent(x.k)+"'>"
      +"<b>"+esc(x.l)+"</b><span class='sg'>"+esc(x.t)+"</span>"
      +(x.s?"<em class='ss'>可群级</em>":'')+"</a>";}
  drop.innerHTML=h;drop.style.display='block';}
box.addEventListener('input',function(){render(box.value);});
box.addEventListener('focus',function(){render(box.value);});
box.addEventListener('keydown',function(e){if(e.key==='Escape'){box.value='';drop.style.display='none';}});
document.addEventListener('click',function(e){if(e.target!==box&&!drop.contains(e.target))drop.style.display='none';});
var m=/^#f_(.+)$/.exec(location.hash||'');
if(m){var k=decodeURIComponent(m[1]);
  var t=document.querySelector("[name='"+k+"']");
  if(t){var w=t.closest('.row')||t.parentElement||t;
    try{w.scrollIntoView({block:'center'});}catch(e){w.scrollIntoView();}
    w.classList.add('_hl');setTimeout(function(){w.classList.remove('_hl');},2600);}}
})();</script>"""


def _settings_search_block():
    """内嵌索引（JSON）+ 搜索脚本。JSON 放在 type=application/json 的 script 里，不执行。"""
    data = json.dumps(_settings_search_index(), ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")          # 防标签串提前闭合
    return ("<script id='_setidx' type='application/json'>" + data + "</script>" + _SEARCH_JS)


# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _group_options(selected=0):
    """已知群下拉选项（仅真实群 ID，负数；过滤私聊/幽灵键）；selected=回显选中。"""
    valid = [c for c in set(hub.AUTHORIZED_GROUPS) | set(hub.game_chips.keys()) if str(c).startswith("-")]
    return "".join(f"<option value='{cid}'{' selected' if cid == selected else ''}>{_esc(hub.chat_name_cache.get(cid) or '')} {cid}</option>"
                   for cid in sorted(valid))

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _all_user_options(selected=0):
    """全部已知用户选项（value=ID，label=昵称；selected=回显选中）。"""
    seen = {}
    for chips in hub.game_chips.values():
        for u in chips: seen[u] = hub.user_names.get(u, str(u))
    return "".join(f"<option value='{u}'{' selected' if u == selected else ''}>{_esc(n)}</option>" for u, n in sorted(seen.items()))

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _id_picker_js():
    """群选择联动用户 datalist 的脚本：select[data-users-for] 选中群后自动填充对应成员。"""
    gusers = {str(cid): {str(u): hub.user_names.get(u, str(u)) for u in chips}
              for cid, chips in hub.game_chips.items()}
            # 安全：昵称是可控输入，直接嵌进 <script> 会形成存储型 XSS（玩家改昵称即可在后台执行 JS）。
            # ① 把 < / 转成 \u003c \u002f（JSON 合法转义，JS 解析后还原，但不会闭合标签）
            # ② 不再用 innerHTML 拼字符串，改用 DOM API 写入
    raw = json.dumps(gusers, ensure_ascii=False).replace("<", "\\u003c").replace("/", "\\u002f")
    return ("<script>var GUSERS=" + raw + ";"
            "document.addEventListener('DOMContentLoaded',function(){"
            "function fill(){document.querySelectorAll('select[data-users-for]').forEach(function(sel){"
            "var dl=document.getElementById(sel.getAttribute('data-users-for'));if(!dl)return;"
            "var us=GUSERS[sel.value]||{};"
            "dl.textContent='';"
            "Object.keys(us).forEach(function(u){var o=document.createElement('option');"
            "o.value=u;o.textContent=us[u];dl.appendChild(o);});});}"
            "document.querySelectorAll('select[data-users-for]').forEach(function(sel){sel.addEventListener('change',fill);});fill();});</script>")

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _otp_page(otp_token, err="", notice=""):
    """二次验证页：密码已通过，等 Telegram 私聊发来的 6 位验证码。"""
    msg = f"<div class='err'>{_esc(err)}</div>" if err else ""
    msg += f"<div class='ok'>{_esc(notice)}</div>" if notice else ""
    return ("<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>二次验证 - 机器人后台</title><style>"
            "*{box-sizing:border-box}html,body{height:100%}"
            "body{background:linear-gradient(135deg,#17181d 0%,#121318 100%);color:#e6e5f0;"
            "font-family:system-ui,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;display:flex;"
            "align-items:center;justify-content:center;padding:20px;min-height:100vh}"
            ".login{background:#1b1c22;border:1px solid #2a2b33;border-radius:14px;padding:32px;"
            "width:min(380px,100%);box-shadow:0 20px 60px rgba(0,0,0,.4)}"
            ".login h1{font-size:20px;font-weight:500;margin:0 0 4px;text-align:center;color:#fff}"
            ".login .desc{font-size:12px;color:#8a89a0;text-align:center;margin-bottom:24px;line-height:1.6}"
            ".login label{display:block;font-size:13px;color:#a9a8bd;margin:14px 0 6px}"
            ".login input{width:100%;background:#121318;border:1px solid #2a2b33;color:#e6e5f0;"
            "border-radius:8px;padding:11px 14px;font-size:14px;transition:border-color .12s;"
            "letter-spacing:6px;text-align:center;font-size:20px}"
            ".login input:focus{outline:none;border-color:#7c6cf0;box-shadow:0 0 0 3px rgba(124,108,240,.12)}"
            ".login button{width:100%;background:linear-gradient(135deg,#7c6cf0 0%,#5d4dd6 100%);"
            "color:#fff;border:none;border-radius:8px;padding:12px;font-size:15px;cursor:pointer;"
            "font-weight:500;margin-top:22px;box-shadow:0 4px 14px rgba(124,108,240,.3)}"
            ".login .err{color:#f09595;font-size:13px;padding:10px 14px;background:rgba(240,149,149,.08);"
            "border:1px solid rgba(240,149,149,.2);border-radius:8px;margin-bottom:14px;text-align:center}"
            ".login .ok{color:#6fd08c;font-size:13px;padding:10px 14px;background:rgba(111,208,140,.08);"
            "border:1px solid rgba(111,208,140,.2);border-radius:8px;margin-bottom:14px;text-align:center}"
            ".login .resend{margin-top:14px;text-align:center}"
            ".login .resend button{background:transparent;border:1px solid #2a2b33;color:#a9a8bd;"
            "box-shadow:none;font-size:13px;padding:8px 16px;margin-top:0}"
            ".login .ft{padding:14px 0 0;margin-top:20px;border-top:1px solid #26272e;font-size:11px;"
            "color:#6a6982;text-align:center}"
            "</style></head><body><div class='login'>"
            "<h1>🔐 二次验证</h1>"
            "<div class='desc'>密码已通过<br>验证码已发到你的 Telegram 私聊<br><b>5 分钟内有效</b></div>"
            + msg +
            "<form method='post' action='/login2'>"
            f"<input type='hidden' name='otp_token' value='{_esc(otp_token)}'>"
            "<label>6 位验证码</label>"
            "<input type='text' name='otp' inputmode='numeric' pattern='[0-9]{6}' "
            "maxlength='6' autocomplete='one-time-code' autofocus required placeholder='——————'>"
            "<button type='submit'>验 证 并 登 录</button></form>"
            "<form method='post' action='/login' class='resend'>"
            f"<input type='hidden' name='resend' value='{_esc(otp_token)}'>"
            "<button type='submit'>🔄 重新发送验证码</button></form>"
            "<div class='ft'>© 机器人后台 · 二次验证保护</div>"
            "</div></body></html>").encode("utf-8")

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _admin_receivers():
    """后台通知收件人（与模块级 _admin_notify_ids 同一口径，避免两处逻辑漂移）。"""
    return hub._admin_notify_ids()

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _login_page(err=""):
    msg = "<div class='err'>密码错误，请重试</div>" if err else ""
            # 用户要求：登录页不放任何默认密码提示（安全红线不变：也绝不显示密码本体）
    return ("<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>登录 - 机器人后台</title><style>"
            "*{box-sizing:border-box}"
            "html,body{height:100%}"
            "body{background:linear-gradient(135deg,#17181d 0%,#121318 100%);color:#e6e5f0;"
            "font-family:system-ui,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;display:flex;"
            "align-items:center;justify-content:center;padding:20px;min-height:100vh}"
            ".login{background:#1b1c22;border:1px solid #2a2b33;border-radius:14px;padding:32px;"
            "width:min(380px,100%);box-shadow:0 20px 60px rgba(0,0,0,.4)}"
            ".login h1{font-size:20px;font-weight:500;margin:0 0 4px;text-align:center;color:#fff}"
            ".login .desc{font-size:12px;color:#8a89a0;text-align:center;margin-bottom:24px}"
            ".login label{display:block;font-size:13px;color:#a9a8bd;margin:14px 0 6px}"
            ".login input{width:100%;background:#121318;border:1px solid #2a2b33;color:#e6e5f0;"
            "border-radius:8px;padding:11px 14px;font-size:14px;transition:border-color .12s}"
            ".login input:focus{outline:none;border-color:#7c6cf0;box-shadow:0 0 0 3px rgba(124,108,240,.12)}"
            ".login button{width:100%;background:linear-gradient(135deg,#7c6cf0 0%,#5d4dd6 100%);"
            "color:#fff;border:none;border-radius:8px;padding:12px;font-size:15px;cursor:pointer;"
            "font-weight:500;margin-top:22px;transition:transform .1s,box-shadow .12s;"
            "box-shadow:0 4px 14px rgba(124,108,240,.3)}"
            ".login button:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(124,108,240,.45)}"
            ".login .err{color:#f09595;font-size:13px;padding:10px 14px;background:rgba(240,149,149,.08);"
            "border:1px solid rgba(240,149,149,.2);border-radius:8px;margin-bottom:14px;text-align:center}"
            ".login .ft{padding:14px 0 0;margin-top:20px;border-top:1px solid #26272e;font-size:11px;"
            "color:#6a6982;text-align:center}"
            "</style></head><body><div class='login'>"
            "<h1>🤖 机器人后台</h1>"
            "<div class='desc'>管理员登录</div>" + msg +
            "<form method='post' action='/login'>"
            "<label>管理密码</label><input type='password' name='password' autofocus required>"
            "<button type='submit'>登 录</button></form>"
            "<div class='ft'>💡 推荐：Telegram 发 <b>/后台</b>，点链接免密登录（密码为备用通道）<br>© 机器人后台 · v" + hub.BOT_VERSION + "</div>"
            "</div></body></html>").encode("utf-8")

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _savebar(label="保存设置", hint="保存立即生效，无需重启"):
            # 吸底保存条：贴在卡片底部、滚动时始终可见（照阿福的受控保存区）
    return (f"<div class='savebar'><button type='submit'>💾 {label}</button>"
            f"<span class='hint'>{hint}</span></div>")

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

_GUARD_JS = ("<script>function openModal(id){document.getElementById(id).classList.add('open')} "
             "function closeModal(id){document.getElementById(id).classList.remove('open')} "
             "document.addEventListener('click',function(e){"
             "if(e.target.classList&&e.target.classList.contains('modal'))e.target.classList.remove('open')});"
             "document.addEventListener('keydown',function(e){"
             "if(e.key==='Escape')document.querySelectorAll('.modal.open').forEach("
             "function(m){m.classList.remove('open')})});</script>")

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _guard_badge(on):
    return ("<span style='color:#6fd08c;font-weight:500'>✅ 已启用</span>" if on
            else "<span style='color:#8a89a0'>⛔ 已停用</span>")

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _guard_row(name, on, summary, mid):
    """防护类型一览行（照方丈）：名称 + 状态徽章 + 参数摘要 + 编辑按钮。"""
    return (f"<tr><td style='width:32%'><b>{_esc(name)}</b></td>"
            f"<td>{_guard_badge(on)}<div class='m-sum' style='font-size:12px;color:#8a89a0;margin-top:3px'>"
            f"{summary}</div></td>"
            f"<td style='width:90px'><a class='q' style='cursor:pointer' onclick=\"openModal('{mid}')\">✏️ 编辑</a></td></tr>")

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _guard_modal(mid, title, sub, group, keys):
    """防护编辑弹窗：内部仍用我们自己的表单排版（_field_rows）；带 _fields 限定提交范围，
            /save 只套用弹窗内的字段，组内其他开关不会被误清零。"""
    from_mark = ",".join(sorted(keys))
    return (f"<div class='modal' id='{mid}'><div class='mbox'>"
            f"<div class='mhead'><h3>{_esc(title)}</h3>"
            f"<button class='mclose' type='button' onclick=\"closeModal('{mid}')\">✕</button></div>"
            f"<div class='msub'>{sub}</div>"
            f"<form method='post' action='/save'>"
            f"<input type='hidden' name='group' value='{group}'>"
            f"<input type='hidden' name='_fields' value=\"{_esc(from_mark, quote=True)}\">"
            + _field_rows(group, keys=keys) +
            f"<button type='submit' style='margin-top:16px'>💾 保存（{_esc(title)}）</button></form></div></div>")

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _pg():
    """延迟导入 core.pages（页面渲染器包）。

    ★ 为什么用函数内导入而不是模块顶层 import：
       内部有一个局部变量就叫 （分页页码），
      模块顶层若也 import 名为 pages 的东西，函数里会出现
      UnboundLocalError（Python 把局部  当成整个函数内的名字，
      顶层的 import 就被遮蔽了）。
      用函数内导入 + 别名，彻底避开这个坑。
    """
    from core import pages as _pages_mod
    return _pages_mod


def _field_rows(gkey, keys=None):
            # keys=None 渲染组内全部字段；传键集合则只渲染这些字段（防护弹窗用）
    rows = []
            # 群级页面右上角取值来源小标（仅群页面出现）
    _OV_CSS = ("position:absolute;top:6px;right:0;font-size:11px;padding:1px 7px;"
               "border-radius:7px;background:rgba(var(--acr),.18);color:#c4b5fd;"
               "border:1px solid rgba(var(--acr),.35);text-decoration:none;white-space:nowrap")

    def _ov_wrap(key, body):
        """群页面上的取值来源标记：
                   ⚑ 群专属 ↺ = 该群已覆盖（点它清除覆盖、改回继承全局）
                   🌐 全局项   = 全站唯一设置（密码/调度/备份等），按群覆盖无意义，保存只作用于全局
                   无标记     = 继承全局默认
                """
        cid = hub.cur_cid()
        if not cid:
            return body
        if key in hub.GROUP_SCOPED_KEYS:
            if key not in (hub.GROUP_SETTINGS.get(cid) or {}):
                return body
            badge = ("<a href='/gclear/" + str(cid) + "/" + key + "' style='" + _OV_CSS +
                     "' title='清除该群专属值，改回继承全局默认'>⚑ 群专属 ↺</a>")
        else:
            badge = ("<span style='" + _OV_CSS + ";opacity:.75' title='全站唯一设置，"
                     "保存会作用于所有群'>🌐 全局项</span>")
        return "<div style='position:relative'>" + body + badge + "</div>"

    if gkey == "tpls":
                # 跨组聚合：全部话术模板按所属模块分区，一次改完
        last = None
        for key, _g, label, ftype, lo, hi, grp in hub.SETTINGS_FIELDS:
            if ftype != "text" or key not in (hub._cross_keys("tpls") or set()):
                continue
            if grp != last:
                rows.append(f"<div class='sec'>{_esc(hub._grp_title(grp))}</div>")
                last = grp
            cur = hub.sget(_g)
            rows.append(_ov_wrap(key, f"<div style='padding:13px 2px;border-bottom:1px solid var(--border)'>"
                        f"<div class='lbl' style='display:flex;align-items:center;justify-content:space-between'>"
                        f"<span>{_esc(label)}<small>可用占位符见默认值；支持换行</small></span>"
                        f"<a class='q' href='/tplprev/{key}'>🔍 预览</a></div>"
                        f"<textarea name='{key}' rows='4' style='margin-top:8px'>{_esc(cur or '')}</textarea></div>"))
        return "".join(rows)

    def _one_raw(key, _g, label, ftype, lo, hi):
        cur = hub.sget(_g)
        if ftype == "multi":
            picked = {p.strip() for p in str(cur or "").split(",") if p.strip()}
            boxes = []
            for val, vlabel in hub.MULTI_OPTIONS.get(key, []):
                ck = " checked" if val in picked else ""
                boxes.append(f"<label class='cb'><input type='checkbox' name='{key}' "
                             f"value='{_esc(val, quote=True)}'{ck}>"
                             f"<span>{_esc(vlabel)}</span></label>")
            return (f"<div style='padding:13px 2px;border-bottom:1px solid var(--border)'>"
                    f"<div class='lbl'>{_esc(label)}"
                    f"<small>勾选即生效；未勾选的类型一律放行</small></div>"
                    f"<div class='cbs'>{''.join(boxes)}</div></div>")
        if ftype == "bool":
            checked = " checked" if cur else ""
            return (f"<div class='row'><div class='lbl'>{_esc(label)}</div>"
                    f"<label class='tg'><input type='checkbox' name='{key}'{checked}>"
                    f"<span class='sl'></span></label></div>")
        if ftype in ("levels", "items"):
            if isinstance(cur, (list, tuple)) and cur and isinstance(cur[0], dict):
                val = "\n".join(f"{x['name']}:{x['value']}" for x in cur)
            else:
                val = str(cur or "")
            return (f"<div style='padding:13px 2px;border-bottom:1px solid var(--border)'>"
                    f"<div class='lbl'>{_esc(label)}<small>每行一条：名称:数值</small></div>"
                    f"<textarea name='{key}' rows='5' style='margin-top:8px'>{_esc(val)}</textarea></div>")
        if ftype == "text":
            return (f"<div style='padding:13px 2px;border-bottom:1px solid var(--border)'>"
                    f"<div class='lbl' style='display:flex;align-items:center;justify-content:space-between'>"
                    f"<span>{_esc(label)}<small>可用占位符见默认值；支持换行</small></span>"
                    f"<a class='q' href='/tplprev/{key}'>🔍 预览</a></div>"
                    f"<textarea name='{key}' rows='5' style='margin-top:8px'>{_esc(cur or '')}</textarea></div>")
        if ftype == "short":
            return (f"<div class='row'><div class='lbl'>{_esc(label)}</div>"
                    f"<input type='text' name='{key}' value='{_esc(cur, quote=True)}' maxlength='8'></div>")
        if ftype == "cmd":
            return (f"<div class='row'><div class='lbl'>{_esc(label)}"
                    f"<small>改完立即生效，无需重启；旧指令同时失效</small></div>"
                    f"<input type='text' name='{key}' value='{_esc(cur, quote=True)}' maxlength='16'></div>")
        if ftype in ("names", "emoji", "bets"):
                    # 词表标签输入（照方丈）：回车/逗号即添加，✕ 删除，退格删末尾；
                    # 底层仍是逗号分隔的 hidden input，提交格式不变，保存逻辑零改动
            items = ([str(x).strip() for x in cur if str(x).strip()] if isinstance(cur, (list, tuple))
                     else [p.strip() for p in re.split(r"[,，]", str(cur or "")) if p.strip()])
            chips = "".join(f"<span class='chip'><b>{_esc(s)}</b><i title='删除'>✕</i></span>" for s in items)
            ph = "数字 1~100000，回车添加" if ftype == "bets" else "输入后按回车添加"
            return (f"<div class='row'><div class='lbl'>{_esc(label)}</div>"
                    f"<div class='tagbox'>{chips}"
                    f"<input type='hidden' name='{key}' value='{_esc(','.join(items), quote=True)}'>"
                    f"<input type='text' placeholder='{ph}' autocomplete='off'>"
                    "</div>"
                    "<script>if(!window._tbInit){window._tbInit=1;"
                    "function _tbSync(tb){var h=tb.querySelector('input[type=hidden]');if(!h)return;"
                    "h.value=[].map.call(tb.querySelectorAll('.chip b'),function(x){return x.textContent}).join(',')}"
                    "document.addEventListener('click',function(e){"
                    "var i=e.target.closest('.tagbox .chip i');"
                    "if(i){var tb=i.closest('.tagbox');i.parentElement.remove();_tbSync(tb);return}"
                    "var tb=e.target.closest('.tagbox');if(tb){var f=tb.querySelector('input[type=text]');if(f)f.focus()}});"
                    "document.addEventListener('keydown',function(e){"
                    "var inp=e.target.closest('.tagbox input[type=text]');if(!inp)return;"
                    "var tb=inp.closest('.tagbox');"
                    "if(e.key==='Enter'||e.key===','||e.key==='，'){e.preventDefault();"
                    "var v=inp.value.replace(/[,，]/g,'').trim();"
                    "if(v){var c=document.createElement('span');c.className='chip';"
                    "var b=document.createElement('b');b.textContent=v;c.appendChild(b);"
                    "var x=document.createElement('i');x.textContent='✕';x.title='删除';c.appendChild(x);"
                    "tb.insertBefore(c,inp);inp.value='';_tbSync(tb)}}"
                    "else if(e.key==='Backspace'&&!inp.value){"
                    "var cs=tb.querySelectorAll('.chip');if(cs.length){cs[cs.length-1].remove();_tbSync(tb)}}});}"
                    "</script></div>")
        return (f"<div class='row'><div class='lbl'>{_esc(label)}"
                f"<small>范围 {lo} ~ {hi}</small></div>"
                f"<input type='number' name='{key}' value='{cur}' step='{'0.1' if ftype == 'float' else '1'}'></div>")

    def _one(key, _g, label, ftype, lo, hi):
        """渲染单个设置项：取值走 sget（群级覆盖 → 全局），并标记群专属项。"""
        return _ov_wrap(key, _one_raw(key, _g, label, ftype, lo, hi))

    grp_fields = [f for f in hub.SETTINGS_FIELDS if f[6] == gkey and (keys is None or f[0] in keys)]
    if not grp_fields:
        return ""
    if any(f[3] == "sep" for f in grp_fields):
                # ①②③ 结构化页（mod/autodel/schedule）：分节标题就是布局，保持定义顺序原样渲染
        return "".join(f"<div class='sec'>{_esc(f[2])}</div>" if f[3] == "sep" else _one(*f[:6])
                       for f in grp_fields)
            # 普通页（照阿福）：开关置顶 → 数字/短文本参数双列 → 宽内容（模板/多选/词表）殿后
            # bets 走宽内容区独占整行：它是 tagbox（可换行的标签盒），塞进 grid2 双列必被裁切
            # （用户 2026-09-10 截图：4 个金额标签只显示到「200」右侧就被切掉）
    bools = [f for f in grp_fields if f[3] == "bool"]
    simple = [f for f in grp_fields if f[3] in ("int", "float", "short", "cmd")]
    wide = [f for f in grp_fields if f[3] in ("text", "multi", "levels", "items", "names", "emoji", "bets")]
    parts = []
    if bools and (simple or wide):
        parts.append("<div class='sec'>🎛 开关</div>")
    parts += [_one(*f[:6]) for f in bools]
    if simple:
        if bools:
            parts.append("<div class='sec'>⚙️ 参数</div>")
        parts.append("<div class='grid2'>" + "".join(_one(*f[:6]) for f in simple) + "</div>")
    if wide:
        if bools or simple:
            parts.append("<div class='sec'>📝 内容与模板</div>")
        parts += [_one(*f[:6]) for f in wide]
    return "".join(parts)

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _home_page():
    all_players = {u for users in hub.game_chips.values() for u in users}
    total_chips = sum(sum(users.values()) for users in hub.game_chips.values())
    group_count = len(hub.AUTHORIZED_GROUPS)
            # 今日四个游戏的局数（按日期分组的 profit dict 的 key 数）
    today = datetime.now(hub.BEIJING_TZ).strftime("%Y-%m-%d")
    today_bets = sum(
        sum(len(v) for v in d.get(today, {}).values())
        for d in (hub.poker_profit_by_date, hub.race_profit_by_date, hub.blackjack_profit_by_date, hub.jinhua_profit_by_date)
    )
    season_txt = (hub.season_name + " · 进行中") if hub.season_active else "未开启"
    today_profit = sum(
        sum(users.values())
        for d in (hub.poker_profit_by_date, hub.race_profit_by_date, hub.blackjack_profit_by_date, hub.jinhua_profit_by_date)
        for users in [d.get(today, {}).get(c, {}) for c in d.get(today, {})]
    )
    def stat(label, value, hint=""):
        hint_html = f"<div style='font-size:11px;color:#6a6982;margin-top:4px'>{hint}</div>" if hint else ""
        return f"<div class='stat'><div class='t'>{label}</div><div class='v'>{value}</div>{hint_html}</div>"
    cards = (
        stat("👥 玩家总数", f"{len(all_players):,}", "跨所有授权群去重") +
        stat("💰 积分总量", f"{total_chips:,}", "所有玩家钱包余额之和") +
        stat("🏘️ 授权群数", f"{group_count}", "Bot 服务覆盖的群") +
        stat("🎮 今日局数", f"{today_bets:,}", f"德州+赛车+21点+炸金花 · {today}") +
        stat("📈 今日净盈亏", f"{today_profit:+,}", "正=玩家净赚，负=玩家净输") +
        stat("🏆 赛季", season_txt, f"ID {hub.season_id}" if hub.season_id else "") +
        stat("💾 数据存储", "✔ 持久化" if str(hub._data_file_status()).startswith("/data") else "⚠ 容器内",
             hub._data_file_status())
    )
    quick = "".join(f"<a class='q' href='/page/{g}'>{i} {n}</a>" for g, n, i in hub.SETTINGS_GROUPS if g != "dashboard")
            # 每群活动详情：你说的"分开的活跃度"——每群一行，玩了多少局/邀了多少人/赛车开关一眼看完
    g_rows = []
    for cid in sorted(hub.AUTHORIZED_GROUPS, key=lambda c: (hub.chat_name_cache.get(c) or str(c))):
        gname = hub.chat_name_cache.get(cid) or str(cid)
        g_players = len(hub.game_chips.get(cid, {}))
        g_chips = sum(hub.game_chips.get(cid, {}).values())
                # 今日四游戏局数（按 profit dict 中今日该 cid 的玩家数近似）
        g_bets = sum(
            1 for d in (hub.poker_profit_by_date, hub.race_profit_by_date, hub.blackjack_profit_by_date, hub.jinhua_profit_by_date)
            for uid in d.get(today, {}).get(cid, {}))
                # 今日有效邀请
        g_invites = sum(1 for r in hub.invite_records.values()
                        if r.get("cid") == cid and hub._rec_valid(r)
                        and str(r.get("ts", "")).startswith(today))
        g_race = "⏰ 开启" if hub.hourly_race_enabled.get(cid, True) else "⏸ 关闭"
        g_rows.append(
            f"<tr><td><code>{cid}</code> {_esc(gname)}</td>"
            f"<td>{g_players}</td><td>{g_chips:,}</td>"
            f"<td>{g_bets}</td><td>{g_invites}</td>"
            f"<td>{g_race}</td></tr>")
    if not g_rows:
        g_rows = "<tr><td colspan='6' style='text-align:center;color:#6a6982'>还没授权群，群里发 /授权</td></tr>"
    group_detail = ("<div class='card' style='margin-top:18px'>"
                    "<h3>🏘️ 各群活动详情（你要的分开的活跃度）</h3>"
                    "<div class='sub'>玩家/积分为当前余额；今日局数=德州+赛车+21点+炸金花官方局；今日有效邀请=未退群且审核通过；整点赛车=群内默认状态</div>"
                    "<table class='tbl'><tr><th>群</th><th>玩家</th><th>积分余额</th><th>今日局数</th><th>今日有效邀请</th><th>整点赛车</th></tr>"
                    + "".join(g_rows) + "</table></div>")
            # 菜单排序：▲▼ 调整侧边栏顺序（群体总览固定第一），保存进设置
    nav = [g for g in hub.SETTINGS_GROUPS if g[0] != "dashboard"]
    keys_now = [g[0] for g in nav]
    meta = {g[0]: (g[1], g[2]) for g in hub.SETTINGS_GROUPS}
    ordered_keys = [k for k in hub.SIDEBAR_ORDER if k in keys_now] + [k for k in keys_now if k not in hub.SIDEBAR_ORDER]
    sort_rows = ""
    for k in ordered_keys:
        n, i = meta.get(k, (k, "•"))
        sort_rows += ("<div style='display:flex;align-items:center;gap:12px;padding:7px 2px;"
                      "border-bottom:1px solid var(--border)'>"
                      f"<span style='flex:1'>{i} {n}</span>"
                      f"<a href='/menu_move/{k}/-1' style='padding:2px 10px;background:var(--hover);"
                      "border-radius:6px;font-size:12px'>▲ 上移</a>"
                      f"<a href='/menu_move/{k}/1' style='padding:2px 10px;background:var(--hover);"
                      "border-radius:6px;font-size:12px'>▼ 下移</a></div>")
    return _page("群体总览", "dashboard",
        "<h1><span class='ico'>📊</span>群体总览</h1>"
        "<div class='sub'>实时数据快照 · 改设置去左侧菜单 · 数据修改去 Telegram 群用 /命令</div>"
        f"<div class='cards'>{cards}</div>"
        + group_detail +
        "<div class='card' style='margin-top:18px'>"
        "<h3>⚡ 快捷入口</h3>" + quick + "</div>"
        "<div class='card' style='margin-top:18px'>"
        "<h3>🧭 侧边栏菜单排序</h3>"
        "<div class='sub' style='margin-bottom:8px'>点 ▲▼ 调整左侧菜单顺序，立即生效并保存</div>"
        + sort_rows + "</div>")

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _esc(v, quote=True):
    """转义并**容忍非字符串**（后台所有「把值放进 HTML」的地方**统一走这里**）。

    ★ 为什么不能直接写 `_esc(x)`：`admin_logs` / `chat_name_cache` /
    `user_names` / `leave_records` / `join_requests` 这些容器**会被序列化到磁盘、
    跨版本回读**（见 `core/data_parts/restore.py`），历史数据里完全可能是数字 ——
    用户 ID 就是最典型的。**设置项也一样**（`hub.sget(...)` 读到的是模块全局变量，
    手改过的设置文件 / 老版本存下来的格式都可能是数字）。
    而 `_esc(12345)` 会抛
    `AttributeError: 'int' object has no attribute 'replace'`，
    并且抛在**列表推导式 / 生成器 / f-string** 里，会让**整个页面**挂掉。
    一条脏记录打死一整页，代价完全不成比例 —— 所以这里一律先 `str()` 再转义。

    实测（`_scratch/_dirty_sweep.py`：把容器与设置项全换成数字/空值，跑 51 个页面）：
    不统一走这里时有 **20 个页面 500**；统一之后 **0 个**。

    `None` 转成**空串**而不是 `"None"`（后者是另一个更隐蔽的 bug：
    页面上会出现字面量 None，而且长度校验抓不到）。

    `quote` 参数与 `html.escape` 同名同义（默认 True），
    所以 `_esc(x, quote=True)` 与 `_esc(x, quote=True)` 可以**直接对调**。

    对字符串输入，输出与 `_esc(x[, quote])` **逐字节相同**，
    所以页面快照基线不受影响（有对照实验证明，见 `test_web_members_dirty_data.py`）。
    """
    return html.escape("" if v is None else str(v), quote)


def _members_body(mode="ops", pgs=None):
    """群组管理：records=进出记录+入群申请；ops=白名单+操作记录。

            （成员档案已独立成「群组成员列表」mlist 页，这里不再重复展示）
            2026-09-11 用户报「就知道偷懒 只取前30条 不会弄成可以翻页啊」→ 4 张表全部改真分页：
            每张表独立页码（p_rec/p_jr/p_wl/p_op），每页 WEB_LIST_PAGE_SIZE 条，标题显示总条数。
            """
    pgs = pgs or {}

    def tbl(headers, rows):
        if not rows:
            return "<div class='sub' style='margin-top:8px'>暂无记录</div>"
        head = "".join(f"<th>{h}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        return f"<table class='tbl'><tr>{head}</tr>{body}</table>"

    def paged(rows, key, sub):
        """按页码切片 + 生成翻页条。返回 (本页行, 页脚 html)。"""
        total = len(rows)
        s, e, page, pages = hub._web_page_bounds(total, pgs.get(key, 1))
        if pages <= 1:
            foot = (f"<div class='sub' style='margin-top:8px'>共 {total} 条</div>"
                    if total else "")
            return rows[:e], foot
        def _lnk(p, label, dis):
            if dis:
                return f"<span class='sub' style='margin:0'>{label}</span>"
            return (f"<a href='/page/members/{sub}?{key}={p}'>"
                    f"<button type='button'>{label}</button></a>")
        foot = ("<div style='margin-top:12px;display:flex;gap:10px;align-items:center'>"
                + _lnk(page - 1, "‹ 上一页", page <= 1)
                + f"<span class='sub' style='margin:0'>共 {total} 条 · 第 {page}/{pages} 页</span>"
                + _lnk(page + 1, "下一页 ›", page >= pages) + "</div>")
        return rows[s:e], foot

    parts = []
    if mode == "records":
                # 1. 退群/入群记录（全部群合并，按时间倒序，真分页）
        lv_all = [[r.get("ts", ""), _esc(hub.chat_name_cache.get(cid) or str(cid)),
                   _esc(r.get("name", "")), f"<code>{r.get('uid', '')}</code>",
                   "🟢 入群" if r.get("join") else "🔴 退群"]
                  for cid, lst in hub.leave_records.items() for r in reversed(lst)]
        lv_rows, lv_foot = paged(lv_all, "p_rec", "records")
        parts.append("<div class='card'><h1>📥 进出记录</h1>"
                     "<div class='sub'>bot 需为群管理员才能收到成员进出事件</div>"
                     + tbl(["时间", "群", "成员", "ID", "类型"], lv_rows) + lv_foot + "</div>")
                # 2. 入群申请（可直接网页批准/拒绝）
        def _jr_btn(op, c, u, label):
            return ("<form style='display:inline;margin:0' method='post' action='/adminops2'>"
                    f"<input type='hidden' name='op' value='{op}'>"
                    f"<input type='hidden' name='cid' value='{c}'>"
                    f"<input type='hidden' name='uid' value='{u}'>"
                    f"<button type='submit' style='padding:2px 10px;cursor:pointer'>{label}</button></form>")
        jq_all = [[r.get("ts", ""), _esc(hub.chat_name_cache.get(cid) or str(cid)),
                   _esc(r.get("name", "")), f"<code>{r.get('uid', '')}</code>",
                   _jr_btn("join_approve", cid, r.get("uid", ""), "✅ 批准") + " " + _jr_btn("join_decline", cid, r.get("uid", ""), "🚫 拒绝")]
                  for cid, lst in hub.join_requests.items() for r in reversed(lst)]
        jq_rows, jq_foot = paged(jq_all, "p_jr", "records")
        parts.append("<div class='card'><h1>📨 入群申请</h1>"
                     "<div class='sub'>群需开启「申请加入」；可直接在此批准或拒绝，无需去 Telegram 客户端</div>"
                     + tbl(["时间", "群", "申请人", "ID", "操作"], jq_rows) + jq_foot + "</div>")
    else:
                # 白名单
        wl_all = [[cid, _esc(hub.user_names.get(u, str(u))), f"<code>{u}</code>"]
                  for cid, us in hub.whitelist.items() for u in sorted(us)]
        wl_rows, wl_foot = paged(wl_all, "p_wl", "ops")
        parts.append("<div class='card'><h1>🛡️ 白名单</h1>"
                     "<div class='sub'>免疫禁言/群封；群里回复消息发「加白」「删白」管理，或在成员列表页加白</div>"
                     + tbl(["群", "成员", "ID"], wl_rows) + wl_foot + "</div>")
                # 管理员操作记录
        op_all = [[r.get("ts", ""), f"群 {r.get('cid', '')}", _esc(r.get("admin", "")),
                   _esc(r.get("action", "")), _esc(r.get("target", ""))]
                  for r in reversed(hub.admin_logs)]
        op_rows, op_foot = paged(op_all, "p_op", "ops")
        parts.append("<div class='card'><h1>📜 管理员操作记录</h1>"
                     "<div class='sub'>bot 执行的每次禁言/封禁/白名单操作自动留档</div>"
                     + tbl(["时间", "群", "操作人", "动作", "对象"], op_rows) + op_foot + "</div>")
    return "".join(parts)

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _botlog_page():
    """谁把机器人拉进群：溯源页（只读）。"""
    rows = []
    for _cid, rec in sorted(hub.bot_added_by.items(), key=lambda kv: kv[1].get("ts", ""), reverse=True):
        cid = int(_cid)
        auth = "✅ 已授权" if cid in hub.AUTHORIZED_GROUPS else "⚠️ 未授权"
        by = int(rec.get("by", 0) or 0)
        _raw = rec.get("by_name") or (hub.user_names.get(by) if by else None) or (f"用户{by}" if by else "未知")
        by_name = _esc(_raw)
        left = rec.get("left_ts")
        left_txt = f"｜已离开 {_esc(left)}" if left else ""
        gname = _esc(rec.get("title") or hub.chat_name_cache.get(cid) or "未知群")
        rows.append(
            f"<tr><td>{gname}</td><td>{cid}</td><td>{by_name}（{by}）</td>"
            f"<td>{_esc(rec.get('ts', ''))}{left_txt}</td><td>{auth}</td></tr>")
    body = ("<div class='card'><h3>🤖 谁把机器人拉进群</h3>"
            "<p style='color:#aaa;font-size:13px'>记录 bot 被拉进每个群的来源（含未授权群）。"
            "未授权群却有人发命令时，可在此溯源是谁拉进来的。</p>"
            "<table class='tbl'><thead><tr><th>群名</th><th>群ID</th><th>拉人者</th>"
            "<th>时间</th><th>授权状态</th></tr></thead><tbody>"
            + ("".join(rows) if rows else "<tr><td colspan='5' style='text-align:center;color:#888'>暂无记录</td></tr>")
            + "</tbody></table></div>")
    return _page("谁拉机器人", "botlog", body)

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _daily_reset_change_note(old_val, new_val):
    """改「每日重置时间」要不要弹一行「下次生效」提示？

    定时任务是「算好下次时间就长睡」的，**睡下了叫不醒**。
    改完时间，最坏要等一觉睡醒才生效（最多 24 小时）。

    用户方案 B（2026-09-14）：逻辑不动，只加这行提示让管理员心里有数。
    返回提示文案（追加到重定向 URL 的 note=），或空串（不弹）。
    """
    if str(old_val or "") == str(new_val or ""):
        return ""
    return ("⏰ 定时任务要等下一轮睡醒才按新时间运行"
            "（最长等 24 小时；在此之前会按旧时间重置一次）")


def _admin_page(gkey, sub=None, saved=False, bad=False, note="", err="", uid=0, flt=None):
    # ★ `flt` 兜底成空字典：HTTP 层总会传一个完整的筛选字典，但**直接调用**
    #   （测试、以及下面那两处自递归 `_admin_page(gkey, sub=first, ...)`）不会传。
    #   而子页渲染里写的是 `flt.get("cid")`（如 `members/titg`）——
    #   `flt=None` 时抛 `AttributeError: 'NoneType' object has no attribute 'get'`，
    #   整页 500。这里兜一下，`_admin_page` 就成了**可以安全直接调用**的入口。
    flt = flt or {}
    gname, gicon = next((n, i) for k, n, i in hub.SETTINGS_GROUPS if k == gkey)
    if gkey == "botlog":   # 只读溯源页，不走通用表单渲染
        return _botlog_page()
    msg = "<div class='ok'>✅ 已保存并立即生效</div>" if saved else ""
    msg += "<div class='err'>部分数值超出范围或非法，已跳过这些项</div>" if bad else ""
    msg += f"<div class='ok'>{_esc(note)}</div>" if note else ""
    msg += f"<div class='err'>{_esc(err)}</div>" if err else ""
    sel_flt_cid = int((flt or {}).get("cid", 0) or 0)
    def _flt_bar(action):
                # 数据页通用「群组筛选」条：GET ?cid=，选择即提交
        return ("<form method='get' action='" + action + "' style='margin-bottom:10px'>"
                "<div class='lbl'>群组筛选</div><select name='cid' onchange='this.form.submit()'>"
                "<option value='0'>全部群</option>" + _group_options(selected=sel_flt_cid)
                + "</select><noscript><button style='margin:0'>查看</button></noscript></form>")
    def _perm_boxes(picked, name="perms"):
                # 8 类消息权限勾选（等级新增/编辑共用）
        pk = {p.strip() for p in str(picked or "").split(",") if p.strip()}
        out = []
        for _k, _v in hub.LEVEL_PERM_OPTIONS:
            ck = " checked" if _k in pk else ""
            out.append(f"<label class='cb' style='margin:0 14px 6px 0'>"
                       f"<input type='checkbox' name='{name}' value='{_k}'{ck}>"
                       f"<span>{_esc(_v)}</span></label>")
        return "<div class='cbs' style='flex-wrap:wrap'>" + "".join(out) + "</div>"
    def _perm_summary(picked):
        pk = {p.strip() for p in str(picked or "").split(",") if p.strip()}
        if len(pk) >= len(hub.LEVEL_PERM_OPTIONS):
            return "<span style='color:#6fd08c'>全部放行</span>"
        if not pk:
            return "<span style='color:#f09595'>全部禁止</span>"
        names = [hub.LEVEL_PERM_NAMES.get(k, k) for k, _v in hub.LEVEL_PERM_OPTIONS if k in pk]
        return _esc("、".join(names))
    # ── 页面渲染：全部交给 core/pages 的路由表（2026-09-13 拆分层 D 批）──
    # 加/删页面 = 改 core/pages/__init__.py 的 PAGE_RENDERERS 一张表 + 加一个文件，
    # 不用再动这个函数。
    _ret = _pg().render_page(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, uid=uid,
                             saved=saved, bad=bad, note=note)
    if isinstance(_ret, tuple):
        # (body, (gkey, kwargs)) → 自递归（子页兜底跳转）
        body, (_rk, _rkw) = _ret
        return _admin_page(_rk, **_rkw)
    body = _ret
    if body is None:
                # 无子页分组照旧；有子页分组落到第一个子页
        # （render_page 返回 None = 路由表没命中，等价于老代码的「没有 elif 匹配」）
        if gkey in hub.SUBPAGES and hub.SUBPAGES[gkey]:
            first = hub.SUBPAGES[gkey][0][0]
            return _admin_page(gkey, sub=first, saved=saved, bad=bad)
        form_open = "<div class='card'>"
        if gkey == "tpls":
            n_tpl = len(hub._cross_keys("tpls") or ())
            form_open = ("<div class='card' style='border-color:#3a3b5a'>"
                         "<div class='sub' style='margin:0 0 4px'>"
                         f"全部 <b>{n_tpl}</b> 条话术集中在这里改，按所属模块分区；"
                         "改完点底部保存，各页面同步生效（原页面里的同一项也已移除，不会两处打架）。"
                         "点右侧「🔍 预览」看填充后的效果。</div></div>"
                         "<div class='card' style='margin-top:18px'>")
        _modals = ""
        if gkey == "autodel":
                    # 照方丈「垃圾防护」结合自身排版：顶部防护一览（名称+状态+摘要+编辑），点编辑弹窗内仍是我们的表单
            def _multi_names(key, val):
                mp = dict(hub.MULTI_OPTIONS.get(key, []))
                return ("、".join(mp.get(v.strip(), v.strip()) for v in str(val or "").split(",") if v.strip())
                        or "未勾选任何规则（全部放行）")
                    # 键集合统一放模块级（AUTODEL_TAB_*）：页面用哪几个、测试就校验哪几个，
                    # 避免「新字段忘了加进弹窗 ⇒ 后台看不见」这种静默漏接线（见常量处注释）
            _K_RECYCLE = hub.AUTODEL_TAB_RECYCLE
            _K_ANTISPAM = hub.AUTODEL_TAB_ANTISPAM
            _K_TEXT = hub.AUTODEL_TAB_TEXT
            _K_MEDIA = hub.AUTODEL_TAB_MEDIA
                    # 摘要必须列全回收项：漏掉「其他默认消息/榜单」= 管理员以为这两项没生效
            _on_rec = any(hub.namespace().get(g) for g in ("PANEL_DELETE_SECONDS", "AUTODEL_DEFAULT_SECONDS",
                                                     "POINTS_DELETE_SECONDS", "REPLY_DELETE_SECONDS",
                                                     "SETTLE_DELETE_SECONDS", "RACE_NOTICE_DELETE_SECONDS",
                                                     "RANK_DELETE_SECONDS"))
            _sum_rec = (" · ".join(t for t, g in (("面板", "PANEL_DELETE_SECONDS"), ("其他默认", "AUTODEL_DEFAULT_SECONDS"),
                                                  ("命令", "POINTS_DELETE_SECONDS"), ("回复", "REPLY_DELETE_SECONDS"),
                                                  ("结算", "SETTLE_DELETE_SECONDS"), ("赛车提示", "RACE_NOTICE_DELETE_SECONDS"),
                                                  ("榜单", "RANK_DELETE_SECONDS")) if hub.namespace().get(g)
                        ) + " 后删除") if _on_rec else "全部为 0（不自动删除）"
            _sum_anti = (f"复读 {hub.sget('ANTISPAM_REPEAT_N')} 条/{hub.sget('ANTISPAM_WINDOW')}s 内 · 定时器特征 {hub.sget('ANTISPAM_TIMER_N')} 条"
                         f" · 最短 {hub.sget('ANTISPAM_MIN_LEN')} 字"
                         + (f" · 禁言 {hub.sget('ANTISPAM_MUTE_SECONDS')}s" if hub.sget("ANTISPAM_MUTE_SECONDS") else " · 只删不禁")) \
                if hub.sget("ANTISPAM_ENABLED") else "开关关闭"
            _rows_g = (_guard_row("消息自动回收", _on_rec, _sum_rec, "md_recycle")
                       + _guard_row("刷屏识别", bool(hub.sget("ANTISPAM_ENABLED")), _sum_anti, "md_antispam")
                       + _guard_row("文本类规则", bool(hub.sget("AUTODEL_TEXT_RULES")), _multi_names("autodel_text_rules", hub.sget("AUTODEL_TEXT_RULES"))
                                    + (f" · 阈值 {hub.sget('AUTODEL_LONG_LEN')} 字" if "long" in str(hub.sget("AUTODEL_TEXT_RULES")) else ""), "md_textrule")
                       + _guard_row("媒体与系统类规则", bool(hub.sget("AUTODEL_MEDIA_TYPES")),
                                    _multi_names("autodel_media_types", hub.sget("AUTODEL_MEDIA_TYPES")), "md_mediarule"))
            form_open = ("<div class='card'><h3>🛡 防护类型</h3>"
                         "<div class='sub'>点「✏️ 编辑」调整对应防护；弹窗内保存立即生效，只影响该防护的参数</div>"
                         "<table class='tbl'><tr><th>防护项</th><th>状态 / 摘要</th><th style='width:90px'>操作</th></tr>"
                         + _rows_g + "</table></div>")
            _modals = (_guard_modal("md_recycle", "消息自动回收",
                                    "游戏卡片/下注面板、命令、查询回复、结算消息、赛车提示、"
                                    "其他默认消息、榜单的自动删除（秒，0=不删；牌桌等带按钮的交互面板不在此列）",
                                    "autodel", _K_RECYCLE)
                       + _guard_modal("md_antispam", "刷屏识别", "复读机与定时脚本特征识别（管理员豁免）",
                                      "autodel", _K_ANTISPAM)
                       + _guard_modal("md_textrule", "文本类规则", "勾选即删；未勾选的类型一律放行",
                                      "autodel", _K_TEXT)
                       + _guard_modal("md_mediarule", "媒体与系统类规则", "勾选即删；未勾选的类型一律放行",
                                      "autodel", _K_MEDIA))
        if gkey == "mod":
            _SENS_KEYS = hub.MOD_MODAL_KEYS
            _sens_on = bool(hub.sget("SENSITIVE_ENABLED") or hub.sget("LINK_WHITELIST_ENABLED"))
            _act_cn = ("删除", "删除+禁言", "删除+踢出")
            _sens_sum = (f"敏感词 {len(hub.sget('SENSITIVE_WORDS'))} 个 · 命中处理 {_act_cn[hub.sget('SENSITIVE_ACTION')] if hub.sget('SENSITIVE_ACTION') in (0, 1, 2) else hub.sget('SENSITIVE_ACTION')}"
                         + (f" · 白名单 {len(hub.sget('LINK_WHITELIST'))} 个域名" if hub.sget("LINK_WHITELIST_ENABLED") else " · 白名单关闭")
                         ) if _sens_on else "开关关闭"
            _sens_card = ("<div class='card' style='margin-top:18px'><h3>🛡 防护类型</h3>"
                          "<table class='tbl'><tr><th>防护项</th><th>状态 / 摘要</th><th style='width:90px'>操作</th></tr>"
                          + _guard_row("敏感词与域名白名单", _sens_on, _sens_sum, "md_sensitive")
                          + "</table></div>")
            _modals += _guard_modal("md_sensitive", "敏感词与域名白名单",
                                    "检测群里消息包含违禁词时按设置处理（明文子串或 /正则/）；域名白名单内的链接不按「链接消息」规则删",
                                    "mod", _SENS_KEYS)
            _mod_on = [n for k, n in (("JOIN_VERIFY_ENABLED", "入群验证"), ("SENSITIVE_ENABLED", "敏感词"),
                                      ("LINK_WHITELIST_ENABLED", "域名白名单"),
                                      ("OBSERVE_CHECK_ENABLED", "观察期巡检"),
                                      ("LURKER_ENABLED", "潜水清理"), ("RAID_ENABLED", "防突袭"),
                                      ("JOIN_GATE_USERNAME", "门槛·用户名"),
                                      ("JOIN_GATE_PREMIUM", "门槛·Premium"),
                                      ("JOIN_GATE_BIO", "门槛·简介")) if hub.namespace().get(k)]
            _mod_txt = ("、".join(_mod_on) + " 已开启") if _mod_on else \
                "以下功能全部默认关闭，打开开关即生效；不想用了关掉开关即可，互不影响"
            form_open = ("<div class='card' style='border-color:#3a3b5a'>"
                         "<div class='sub' style='margin:0 0 4px'>🛡️ 群管中心："
                         f"{_esc(_mod_txt)}。</div>"
                         "<div class='sub' style='margin:0'>相关页面："
                         "<a class='q' href='/page/autodel'>🗑️ 自动删除</a>"
                         "<a class='q' href='/page/members/join'>👥 入群与观察</a>"
                         "<a class='q' href='/page/members/ops'>🔒 白名单</a>"
                         "<a class='q' href='/page/admin/blacklist'>🚫 拉黑管理</a></div></div>"
                         + _sens_card +
                         "<div class='card' style='margin-top:18px'>")
        if gkey == "schedule":
            def _sched_card(title, task, items, selected, subtitle, path):
                """通用作用对象切换卡：items=(id, 显示名) 列表，selected=当前开启 id 集合。"""
                if not items:
                    return ("<div class='card' style='margin-top:18px'><h3>" + title + "</h3>"
                            "<div class='sub'>无可配置对象</div></div>")
                rows = ""
                for rid, name in items:
                    on = rid in selected
                    badge = "<span style='color:#6fd08c'>✅ 开</span>" if on else "<span style='color:#8a89a0'>⏸ 关</span>"
                    btn = "<a href='" + path + str(rid) + "/toggle' style='margin-left:8px'>" + ("关闭" if on else "开启") + "</a>"
                    rows += "<tr><td>" + badge + " " + _esc(str(name)) + " <code style='font-size:11px;color:#8a89a0'>" + str(rid) + "</code>" + btn + "</td></tr>"
                return ("<div class='card' style='margin-top:18px'><h3>" + title + "</h3>"
                        "<div class='sub'>" + subtitle + "（未勾选的不参与；都未勾=不执行该任务）</div>"
                        "<table class='tbl'>" + rows + "</table></div>")

                    # 4 个调度任务的作用对象
            sched_cards = ""
            grp_items = [(cid, hub.chat_name_cache.get(cid, str(cid))) for cid in sorted(hub.AUTHORIZED_GROUPS)]
            uid_items = [(uid, hub.user_names.get(uid, str(uid))) for uid in sorted({hub.ADMIN_USER_ID, *hub.BOT_ADMINS})]
            if not hub.daily_reset_groups: hub.daily_reset_groups.update(hub.AUTHORIZED_GROUPS)
            if not hub.leaderboard_groups: hub.leaderboard_groups.update(hub.AUTHORIZED_GROUPS)
            if not hub.backup_admins: hub.backup_admins.add(hub.ADMIN_USER_ID)
            sched_cards += _sched_card("🔄 每日重置 · 作用群", "dailyreset", grp_items, hub.daily_reset_groups,
                                       f"每日 {hub.DAILY_RESET_TIME} 清理这些群的赛季当日分/聊天积分/赛车当日统计", "/sch_dailyreset_toggle/")
            sched_cards += _sched_card("🏆 德州日榜推送 · 作用群", "leaderboard", grp_items, hub.leaderboard_groups,
                                       f"每日 {hub.LEADERBOARD_TIME} 向这些群推送德州当日排行榜", "/sch_leaderboard_toggle/")
            sched_cards += _sched_card("💾 自动备份 · 接收私聊的管理员", "backup", uid_items, hub.backup_admins,
                                       f"每 {hub.BACKUP_INTERVAL_HOURS} 小时私聊发送 bot_data.json + bot_settings.json", "/sch_backup_admins_toggle/")

                    # 定时任务状态总览：一眼看出哪些任务在跑（与下方开关实时联动）
            def _badge(_on):
                return ("<span style='color:#6fd08c;font-weight:700'>✅ 开启</span>" if _on
                        else "<span style='color:#f09595;font-weight:700'>⛔ 关闭</span>")
            _rows = ""
            for _name, _on in (("每日重置", hub.DAILY_RESET_ENABLED), ("德州日榜推送", hub.LEADERBOARD_ENABLED),
                               ("赛车自动开赛", hub.RACE_AUTO_ENABLED), ("自动备份", hub.BACKUP_ENABLED),
                               ("定时群公告", hub.ANNOUNCE_ENABLED)):
                _rows += ("<div style='display:flex;justify-content:space-between;padding:7px 2px;"
                          "border-bottom:1px solid var(--border)'><span>" + _name + "</span>" + _badge(_on) + "</div>")
                    # 整点赛车每群推送明细：一眼看出哪个群没收到 + 网页直接开关每群
            _race_rows = ""
            for _cid in sorted(hub.AUTHORIZED_GROUPS):
                _on = bool(hub.hourly_race_enabled.get(_cid, True))
                _badge = "<span style='color:#6fd08c'>✅ 开</span>" if _on else "<span style='color:#8a89a0'>⏸ 关</span>"
                _last = hub.race_last_sent.get(_cid) or "（暂无）"
                _tg = "<a href='/racegrp/" + str(_cid) + "/toggle' style='margin-left:8px'>" + ("关闭" if _on else "开启") + "</a>"
                _race_rows += ("<tr><td>" + _badge + " <code>" + str(_cid) + "</code> " + _esc(hub.chat_name_cache.get(_cid) or str(_cid)) + _tg + "</td>"
                               "<td>" + _last + "</td></tr>")
            if not _race_rows:
                _race_rows = "<tr><td colspan='2' style='text-align:center;color:#6a6982'>无授权群</td></tr>"
                    # 兑换商品作用群
            _redeem_rows = ""
            for _i, _x in enumerate(hub.redeem_goods):
                _tg = _x.get("target_groups") or []
                if not _tg:
                    _tg_txt = "<span style='color:#6fd08c'>全部授权群</span>"
                else:
                    _tg_txt = "<br>".join(f"<code>{c}</code> {_esc(hub.chat_name_cache.get(c) or str(c))}" for c in _tg)
                _redeem_rows += (f"<tr><td>{_esc(str(_x.get('name', '?')))}</td><td>{_tg_txt}</td></tr>")
            if not _redeem_rows:
                _redeem_rows = "<tr><td colspan='2' style='text-align:center;color:#6a6982'>暂无兑换商品</td></tr>"
            form_open = ("<div class='card'><h3>📋 当前任务状态</h3>"
                         "<div class='sub' style='margin-bottom:8px'>与下方开关实时联动；关闭后到点不再执行，"
                         "重新开启从下一个周期生效（自动备份开关即时生效）</div>" + _rows + "</div>"
                         "<div class='card' style='margin-top:18px'><h3>⏰ 整点赛车 · 每群推送明细</h3>"
                         "<div class='sub'>每行一个授权群：状态（开关）/最近成功推送时间；下方红色统计是跳过原因计数（重启清零）</div>"
                         "<table class='tbl'><tr><th>群（点右侧字开/关本群赛车）</th><th>最近成功推送</th></tr>" + _race_rows + "</table></div>"
                         + sched_cards +
                         "<div class='card' style='margin-top:18px'><h3>🎁 兑换商品 · 作用群</h3>"
                         "<div class='sub'>作用群空=全授权群；不勾选部分=只在该群触发</div>"
                         "<table class='tbl'><tr><th>商品</th><th>作用群</th></tr>" + _redeem_rows + "</table></div>"
                         "<div class='card' style='margin-top:18px'>")
        if gkey != "autodel":
            _field_keys = None
            if gkey == "mod":   # 敏感词已移入防护弹窗，主表单剔除（含其分节标题）
                _field_keys = {f[0] for f in hub.SETTINGS_FIELDS if f[6] == "mod"} - hub.MOD_MODAL_KEYS
                    # 主表单也声明 _fields：被剔除的键（如敏感词已移入弹窗）不进「补 0」名单，
                    # 否则用户只改个验证方式就把敏感词总开关静默清零（2026-09-09 报障真凶）
            _form_fields = (f"<input type='hidden' name='_fields' value=\""
                            f"{_esc(','.join(sorted(_field_keys)), quote=True)}\">"
                            if _field_keys is not None else "")
            body = (f"<h1>{gicon} {gname}</h1><div class='sub'>保存立即生效，无需重启</div>{msg}"
                    + form_open +
                    "<form method='post' action='/save'>"
                    f"<input type='hidden' name='group' value='{gkey}'>"
                    + _form_fields
                    + _field_rows(gkey, keys=_field_keys) +
                    _savebar() + "</form>" + _modals + "</div>")
        else:
            body = (f"<h1>{gicon} {gname}</h1>"
                    "<div class='sub'>点「✏️ 编辑」调整对应防护；弹窗内保存立即生效，只影响该项参数</div>"
                    f"{msg}" + form_open + _modals + "</div>")
    return _page(gname, gkey, body)

# ── 后台页面渲染器（2026-09-13 从 start_health_server 内嵌提升到模块级）──

def _tpl_preview(key):
    samples = {
        "sign_msg_tpl": dict(name="玩家A", streak=7, reward=1000, bonus="（含连续7天额外奖励）", balance=50000),
        "query_msg_tpl": dict(name="玩家A", balance=50000, level_line="🎖 等级：黄金\n", signed="✅ 已签", streak=7, today_chat=100),
        "add_msg_tpl": dict(target="玩家B", verb="添加", amount=1000, balance=51000),
    }
    kw = samples.get(key)
    if not kw:
        out = "该字段不支持预览"
    else:
        gname = next((g for k, g, *_r in hub.SETTINGS_FIELDS if k == key), None)
        tpl = hub.namespace().get(gname, "") if gname else ""
        out = hub._fmt_tpl(key, **kw) if (tpl or "").strip() else hub.MSG_TPL_DEFAULTS[key].format(**kw)
    return ("<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>预览 - 机器人后台</title><style>"
            "body{background:#121318;color:#e6e5f0;font-family:system-ui,sans-serif;margin:0;"
            "display:flex;justify-content:center;padding-top:10vh}"
            "pre{background:#1b1c22;border:1px solid #2a2b33;border-radius:14px;padding:24px;"
            "width:min(420px,92vw);white-space:pre-wrap;font-size:15px;line-height:1.7;font-family:inherit}"
            "</style></head><body><pre>" + _esc(out) + "</pre></body></html>").encode("utf-8")


def start_health_server():
    """网页后台：密码登录 + 在线调设置。

    端口从环境变量 PORT 读取（平台注入），本地没有时默认 8080。
    - GET /health            → 200 ok（给 UptimeRobot ping，不需要登录）
    - GET /                  → 未登录显示登录页；已登录显示群体总览（关键数值卡片）
    - GET /page/<分组>       → 各游戏/分类设置页（左侧菜单栏导航）
    - POST /login            → 校验密码，发 Cookie 会话（7 天有效）
    - POST /save             → 分组保存：立即套用内存全局常量 + 合并写 bot_settings.json
    全部跑在独立守护线程，任何异常都不影响 bot 主逻辑。
    """

    try:
        port = int(os.environ.get("PORT", 8080))
        sessions = {}  # token -> 过期时间戳
        sess_lock = threading.Lock()
        login_fails = {}  # ip -> [连续失败次数, 锁定截止时间戳]（防爆破：连续错 5 次锁 10 分钟）
        otp_fails = {}    # ip -> [连续验证码错误次数, 锁定截止]

        def _check_session(cookie_header):
            if not cookie_header:
                return False
            token = None
            for part in cookie_header.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "wb_session":
                    token = v
                    break
            if not token:
                return False
            with sess_lock:
                exp = sessions.get(token)
                if exp and exp > time.time():
                    return True
                sessions.pop(token, None)
            return False

        def _send_otp_code(ip):
            """生成验证码并私聊发给所有管理员。返回 (otp_token, code, err)。

            任一管理员收到即可完成登录；全部发送失败才返回错误
            （常见原因：管理员从未私聊过机器人，Telegram 禁止 bot 主动发起）。
            """
            code = f"{secrets.randbelow(1000000):06d}"
            otp_token = secrets.token_urlsafe(24)
            with sess_lock:
                hub.web_pending_otp[otp_token] = {"code": code, "exp": time.time() + 300, "ip": ip}
                # 清理过期等待
                for k in [k for k, v in hub.web_pending_otp.items() if v["exp"] < time.time()]:
                    hub.web_pending_otp.pop(k, None)
            if not (hub._bot_app and hub._bot_loop):
                return otp_token, code, "bot 未就绪，无法发送验证码"
            text = (f"🔐 <b>后台登录二次验证</b>\n\n"
                    f"验证码：<code>{code}</code>\n"
                    f"来源 IP：<code>{ip}</code>\n"
                    f"5 分钟内有效，一次性使用。\n\n"
                    f"⚠️ 如果不是你本人操作，请立即修改后台密码。")
            async def _send():
                ok_cnt = 0
                for rid in _admin_receivers():
                    try:
                        await hub._bot_app.bot.send_message(chat_id=rid, text=text, parse_mode="HTML")
                        ok_cnt += 1
                    except Exception:
                        hub.logger.warning("验证码发送给 %s 失败（可能未私聊过机器人）", rid)
                return ok_cnt
            try:
                ok_cnt = asyncio.run_coroutine_threadsafe(_send(), hub._bot_loop).result(15)
                if ok_cnt <= 0:
                    return otp_token, code, "验证码发送失败：所有管理员均未私聊过机器人（先在 Telegram 私聊发 /start，或用 /网页码 取码）"
                return otp_token, code, ""
            except Exception as exc:
                hub.logger.exception("登录验证码发送失败")
                return otp_token, code, f"发送失败：{type(exc).__name__}: {str(exc)[:120]}"

        class _AdminHandler(ToggleHandlers, PointsHandlers, MemberHandlers, InviteHandlers, LotteryHandlers, AdminHandlers, MiscHandlers, BaseHTTPRequestHandler):
            def _send(self, code, body, headers=None):
                self.send_response(code)
                for k, v in (headers or []):
                    self.send_header(k, v)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _redirect(self, to, cookie=None):
                headers = [("Location", to)]
                if cookie:
                    headers.append(("Set-Cookie", cookie))
                self.send_response(302)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", "0")
                self.end_headers()

            # ══════════════════════════════════════════════════════════
            # 路由表（2026-09-13 · 方案 1）
            # ══════════════════════════════════════════════════════════
            # 以前这里是 do_GET / do_POST 里一长串 `if path == "...":`。
            # 想加一个页面，得在 100 多个 if 里找地方插，插错就串到隔壁
            # 分支 —— 「改 A 崩 B」的主要来源。
            # 改成表之后：**加页面 = 表里加一行 + 写一个处理函数**，
            # 派发逻辑（do_GET / do_POST）再也不用动。
            #
            # 表里存的是**方法名**（字符串），不是方法本身：这样表能写在
            # 方法前面，读起来就是「路径 → 方法名」，调用时才 getattr 取。
            #
            # 处理函数统一收一个 r（本次请求的上下文：路径/查询串/表单…），
            # 函数体是从原来 if 分支里**原样搬过来的**，所以里面用的变量名
            # 跟以前一模一样。

            # GET ①：不用登录就能访问的（必须排在会话校验前面）
            _GET_PUBLIC = {
                "/health":             "_get_health",
                "/magic":              "_get_magic",
            }

            # GET ②：需要登录的，字面量路径 → 方法名
            _GET_ROUTES = {
                "/":                   "_get_home",
                "/lottery_cancel":     "_get_lottery_cancel",
                "/points_template":    "_get_points_template",
                "/points_export":      "_get_points_export",
                "/page/admin/titg":    "_get_legacy_titg_redirect",
            }

            # GET ③：需要登录的，正则路径 —— **按顺序**匹配，先匹配到的先赢。
            # 处理函数返回 False = 「这条不是我的」，派发继续往下试
            # （/theme、/page 两条要先看内容才知道收不收）。
            _GET_PATTERNS = (
                (r"/(rule|pkg|level|mall|redeem)_(del|toggle)/(\d+)",               "_get_list_del_toggle"),
                (r"/gclear/(-?\d+)/([a-z0-9_]+)",                                   "_get_group_clear"),
                (r"/racegrp/(-?\d+)/toggle",                                        "_get_racegrp_toggle"),
                (r"/sch_(dailyreset|leaderboard)_toggle/(-?\d+)(?:/toggle)?",       "_get_sched_groups_toggle"),
                # 2026-09-14：经营日报已下线 → 只剩 backup 一条收件人路由
                (r"/sch_backup_admins_toggle/(-?\d+)(?:/toggle)?",                "_get_sched_admins_toggle"),
                (r"/theme/([a-z]+)",                                                "_get_theme"),
                (r"/menu_move/([a-z0-9_]+)/(-?1)",                                  "_get_menu_move"),
                (r"/tplprev/([a-z0-9_]+)",                                          "_get_tpl_preview"),
                (r"/invite_del/(-?\d+):(\d+)",                                      "_get_invite_del"),
                (r"/page/([a-z]+)(?:/([a-z0-9_]+))?",                               "_get_admin_page"),
            )

            # POST ①：不用登录、也不用解析表单（积分导入自己解析 multipart）
            _POST_NOAUTH_RAW = {
                "/points_import":      "_post_points_import",
            }

            # POST ②：不用登录，但要用表单（登录 / 二次验证）
            _POST_NOAUTH_FORM = {
                "/login":  "_post_login",
                "/login2": "_post_login2",
            }

            # POST ③：需要登录（其余全部）
            _POST_ROUTES = {
                "/cmdaliases":         "_post_cmdaliases",
                "/adminops":           "_post_adminops",
                "/adminops2":          "_post_adminops2",
                "/memops":             "_post_memops",
                "/invite_clear":       "_post_invite_clear",
                "/invite_clear_group": "_post_invite_clear_group",
                "/rule_add":           "_post_rule_add",
                "/pkg_add":            "_post_pkg_add",
                "/level_add":          "_post_level_add",
                "/level_edit":         "_post_level_add",
                "/mall_add":           "_post_mall_add",
                "/redeem_add":         "_post_redeem_add",
                "/lottery_create":     "_post_lottery_create",
                "/points_adj":         "_post_points_adj",
                "/save":               "_post_save",
            }

            # ★ 这些 GET 路径「点一下就改数据」（删规则 / 清积分 / 关开关 / 移菜单…）。
            #   Cookie 虽然设了 SameSite=Lax，但它**只拦跨站 POST** ——
            #   从别的站点点链接 / 被跳转过来的**顶层 GET** 照样会带上 Cookie。
            #   所以这些路径要自己校验来源（Origin / Referer 必须同站）。
            _STATE_GET_PREFIXES = (
                "/gclear/", "/racegrp/", "/menu_move/", "/invite_del/",
                "/sch_dailyreset_toggle/", "/sch_leaderboard_toggle/",
                "/sch_backup_admins_toggle/",
                "/rule_del/", "/rule_toggle/", "/pkg_del/", "/pkg_toggle/",
                "/level_del/", "/level_toggle/", "/mall_del/", "/mall_toggle/",
                "/redeem_del/", "/redeem_toggle/",
            )

            def do_GET(self):
                path = urlparse(self.path).path
                r = _Req(path)

                _h = self._GET_PUBLIC.get(path)
                if _h is not None:
                    return getattr(self, _h)(r)

                if not _check_session(self.headers.get("Cookie")):
                    self._send(200, _login_page()); return

                # ★ CSRF：改状态的请求必须来自本站。
                #   没有 Origin/Referer 的（地址栏直开、隐私设置去掉了）一律放行，
                #   只拦「明确从别的站点来的」。
                if path.startswith(self._STATE_GET_PREFIXES):
                    _host = (self.headers.get("Host") or "").split(":")[0]
                    _crossed = False
                    for _hk in ("Origin", "Referer"):
                        _v = self.headers.get(_hk)
                        if not _v:
                            continue
                        try:
                            _h2 = urlparse(_v).hostname
                        except Exception:
                            _h2 = None
                        if _h2 and _host and _h2 != _host:
                            _crossed = True
                    if _crossed:
                        self._send(403, b"cross-site request blocked",
                                   [("Content-Type", "text/plain")]); return

                qs = parse_qs(urlparse(self.path).query)
                r.qs = qs
                # 群级上下文：URL 带 ?cid=<群ID> 时，本请求渲染的取值/回显都按该群解析
                # （ThreadingHTTPServer 每请求一个线程，contextvar 天然按请求隔离，无需复位）
                try: hub._CUR_CID.set(int(qs.get("cid", ["0"])[0] or 0))
                except (TypeError, ValueError): hub._CUR_CID.set(0)
                r.saved, r.bad = "saved" in qs, "bad" in qs
                r.note = qs.get("note", [""])[0]
                r.err = qs.get("err", [""])[0]

                _h = self._GET_ROUTES.get(path)
                if _h is not None:
                    return getattr(self, _h)(r)

                for _pat, _name in self._GET_PATTERNS:
                    _mm = re.fullmatch(_pat, path)
                    if _mm is None:
                        continue
                    if getattr(self, _name)(r, _mm) is False:
                        continue
                    return

                self._send(404, b"not found", [("Content-Type", "text/plain")])

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    # ★ 限制请求体大小：原来 Content-Length 写多大就照读多大，
                    #   伪造一个超大的 Content-Length（或真传几百 MB）能把容器内存撑爆。
                    #   8 MB 足够最大的 CSV 导入（约 20 万行）。
                    if length > 8 * 1024 * 1024:
                        self._send(413, b"payload too large",
                                   [("Content-Type", "text/plain")]); return
                    raw = self.rfile.read(length) if length else b""
                except Exception:
                    raw = b""
                path = urlparse(self.path).path
                r = _Req(path, raw=raw)

                # ① 不用登录、不用解析表单（积分导入自己解析 multipart）
                _h = self._POST_NOAUTH_RAW.get(path)
                if _h is not None:
                    return getattr(self, _h)(r)

                try:
                    form = parse_qs(raw.decode("utf-8"))
                except Exception:
                    form = {}
                r.form = form
                # 群级上下文：表单带 cid（群页面保存时由页面自动注入的隐藏字段）→ 本请求按该群解析
                try: hub._CUR_CID.set(int((form.get("cid", [""])[0] or "0") or 0))
                except (TypeError, ValueError): hub._CUR_CID.set(0)

                # ② 不用登录，但要用表单（登录 / 二次验证）
                _h = self._POST_NOAUTH_FORM.get(path)
                if _h is not None:
                    return getattr(self, _h)(r)

                # 其余全部 POST 操作（管理员/加减分/赛季分/保存…）必须已登录，防止未授权调用
                if not _check_session(self.headers.get("Cookie")):
                    self._redirect("/"); return

                # ③ 需要登录
                _h = self._POST_ROUTES.get(path)
                if _h is not None:
                    return getattr(self, _h)(r)

                self._send(404, b"not found", [("Content-Type", "text/plain")])

            # ══════════════════════════════════════════════════════════
            # 路由处理函数（从原来 do_GET / do_POST 的 if 分支原样搬来）
            # ══════════════════════════════════════════════════════════

            def _get_magic(self, r):
                """Telegram 一键登录：校验一次性 token → 直接建会话 → 302 进后台。"""
                qs_m = parse_qs(urlparse(self.path).query)
                tok = (qs_m.get("token", [""])[0] or "").strip()
                with sess_lock:
                    rec = hub.web_magic_tokens.pop(tok, None)  # pop 即一次性
                if not rec or rec["exp"] < time.time():
                    self._send(200, _login_page(err=1)); return
                session = secrets.token_urlsafe(32)
                with sess_lock:
                    sessions[session] = time.time() + 7 * 86400
                # 通知所有管理员：有人通过一键链接进入后台
                try:
                    ip = hub._client_ip(self)
                    ua = (self.headers.get("User-Agent") or "")[:120]
                    if hub._bot_app and hub._bot_loop:
                        async def _notify():
                            ntxt = (f"✅ <b>后台登录成功（一键链接）</b>\n\n"
                                    f"来源 IP：<code>{ip}</code>\n"
                                    f"时间：{datetime.now(hub.BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n"
                                    f"设备：<code>{_esc(ua)}</code>")
                            for rid in _admin_receivers():
                                try:
                                    await hub._bot_app.bot.send_message(chat_id=rid, text=ntxt, parse_mode="HTML")
                                except Exception:
                                    pass
                        asyncio.run_coroutine_threadsafe(_notify(), hub._bot_loop).result(8)
                except Exception:
                    pass
                self._redirect("/", cookie=f"wb_session={session}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800")
                return

            def _get_home(self, r):
                self._send(200, _home_page()); return

            def _get_tpl_preview(self, r, mm):
                self._send(200, _tpl_preview(mm.group(1))); return

            def _get_admin_page(self, r, mm):
                if mm.group(1) not in {g for g, _n, _i in hub.SETTINGS_GROUPS}:
                    return False
                m = mm
                bad = r.bad
                err = r.err
                note = r.note
                path = r.path
                qs = r.qs
                saved = r.saved
                if m.group(1) == "dashboard":   # 群体总览是定制页（统计卡+排序），无通用表单，别落空壳
                    self._send(200, _home_page()); return
                try: sel_uid = int(qs.get("uid", ["0"])[0])
                except ValueError: sel_uid = 0
                def _qi(k, dflt):
                    try: return int(qs.get(k, [str(dflt)])[0] or dflt)
                    except ValueError: return dflt
                flt = {"cid": _qi("cid", 0), "q": (qs.get("q", [""])[0] or "")[:50],
                       "edit": _qi("edit", -1),   # 等级页：点「编辑」带 ?edit=序号
                       "never": 1 if qs.get("never", [""])[0] else 0, "silent": _qi("silent", 0),
                       "join_from": (qs.get("join_from", [""])[0] or "")[:16],
                       "join_to": (qs.get("join_to", [""])[0] or "")[:16],
                       "page": max(1, _qi("page", 1)), "per": _qi("per", 20),
                       # 群组管理 4 张记录表各自的页码（2026-09-11 用户：写死条数看不到老记录）
                       "p_rec": max(1, _qi("p_rec", 1)), "p_jr": max(1, _qi("p_jr", 1)),
                       "p_wl": max(1, _qi("p_wl", 1)), "p_op": max(1, _qi("p_op", 1))}
                try:
                    self._send(200, _admin_page(m.group(1), sub=m.group(2), saved=saved, bad=bad, note=note, err=err, uid=sel_uid, flt=flt)); return
                except Exception as _pg_exc:
                    # 渲染出错返回可读错误页（带异常信息），绝不静默断连让用户看到"上游连接错误"
                    hub.logger.exception("后台页面渲染失败：%s", path)
                    self._send(500, ("<h1>页面渲染出错</h1><p>" + _esc(str(_pg_exc))
                                     + "</p><p>请截图本页反馈给开发者排查</p>").encode("utf-8"),
                               [("Content-Type", "text/html; charset=utf-8")]); return

            # ── POST 处理函数 ──────────────────────────────────────────

            def _post_points_import(self, r):
                raw = r.raw
                if not _check_session(self.headers.get("Cookie")):
                    self._redirect("/"); return
                def _imp_back(note="", err=""):
                    q = ("?note=" + quote(note)) if note else ("?err=" + quote(err) if err else "")
                    self._redirect("/page/points/impexp" + q)
                fields = hub._parse_multipart(raw, self.headers.get("Content-Type", ""))
                try: cid = int(fields.get("cid", 0))
                except (TypeError, ValueError): cid = 0
                if not cid: _imp_back(err="群 ID 无效"); return
                # ⑱ 二次确认：导入是覆盖式写入，未显式勾选确认一律拒绝（防选错群整群余额被覆盖）
                if str(fields.get("confirm", "")).strip() not in ("1", "on", "true"):
                    _imp_back(err="未勾选「我确认覆盖所选群的全部积分」，导入已取消（不影响现有数据）"); return
                file_field = fields.get("file")
                if not isinstance(file_field, tuple) or not file_field[1]:
                    _imp_back(err="未收到文件"); return
                fname, data = file_field
                parsed, perr = hub._parse_points_rows(data, fname)
                if perr: _imp_back(err=perr); return
                rows, skipped = parsed
                if not rows:
                    _imp_back(err="没有可导入的有效行（需 用户ID 和 积分 两列数字）"); return
                for uid, pts, nick in rows:
                    hub.game_chips[cid][uid] = pts          # 阿福语义：覆盖该群已有积分
                    if nick: hub.user_names[uid] = nick
                hub.force_save_now()
                _imp_back(note=f"✅ 导入完成：成功 {len(rows)} 条，跳过 {skipped} 条（群 {cid}，已覆盖式写入并落盘）")
                return

            def _post_login(self, r):
                form = r.form
                ip = hub._client_ip(self)
                with sess_lock:
                    _cnt, lock_until = login_fails.get(ip, [0, 0])
                if time.time() < lock_until:
                    self._send(429, b"too many failed logins, try again in 10 minutes", [("Content-Type", "text/plain")]); return
                pwd = (form.get("password", [""])[0] or "").strip()
                # 重新发送验证码（OTP 页上的「重新发送」按钮）
                resend_tok = form.get("resend", [""])[0]
                if resend_tok:
                    # ★ OTP 页的「重新发送」表单只提交 resend，**不带 password**
                    #   （见 _otp_page）。原写法拿恒为空串的 pwd 去比对密码 →
                    #   必然走 else 报「密码错误」，而旧 token 已经被 pop 作废，
                    #   原验证码也没了 —— 这个按钮等于永远点不动。
                    #   改为：凭 resend 令牌本身判断（它就是一次登录会话的凭证），
                    #   并且**校验通过前不 pop**，失败也能保留原验证码。
                    with sess_lock:
                        old = hub.web_pending_otp.get(resend_tok)
                    if old:
                        tok, _code, serr = _send_otp_code(ip)
                        if serr:
                            self._send(200, _otp_page(tok, err=serr + "（请检查是否已私聊过机器人 /start）"))
                        else:
                            with sess_lock:
                                hub.web_pending_otp.pop(resend_tok, None)
                            self._send(200, _otp_page(tok, notice="✅ 新的验证码已发送"))
                    else:
                        self._send(200, _login_page(err=1))
                    return
                if hub._pwd_ok(pwd):
                    # 二次验证：密码对了还不够，还要 Telegram 私聊验证码
                    if hub.namespace().get("WEB_OTP_ENABLED", True):
                        tok, _code, serr = _send_otp_code(ip)
                        self._send(200, _otp_page(tok,
                            err=(serr + "（请先在 Telegram 私聊机器人发 /start）") if serr else ""))
                        return
                    token = secrets.token_urlsafe(32)
                    with sess_lock:
                        sessions[token] = time.time() + 7 * 86400
                        login_fails.pop(ip, None)
                    self._redirect("/", cookie=f"wb_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800")
                else:
                    # ★ 自增与写回必须在**同一把锁里重新读一次**当前值。
                    #   原写法用的是函数开头那个早已解锁的 `_cnt`：并发请求会
                    #   读到同一个旧值、回写同一个 new_cnt，计数永远停在 1
                    #   →「连错 5 次锁 10 分钟」永不触发，限速形同虚设。
                    with sess_lock:
                        _cur = login_fails.get(ip, [0, 0])
                        new_cnt = _cur[0] + 1
                        login_fails[ip] = [new_cnt, time.time() + 600 if new_cnt >= 5 else 0]
                    self._send(200, _login_page(err=1))
                return

            def _post_login2(self, r):
                """二次验证提交：校验 Telegram 验证码，通过才建会话。"""
                form = r.form
                ip = hub._client_ip(self)
                with sess_lock:
                    _c2, lock2 = otp_fails.get(ip, [0, 0])
                if time.time() < lock2:
                    self._send(429, b"too many failed otp attempts", [("Content-Type", "text/plain")]); return
                tok = (form.get("otp_token", [""])[0] or "").strip()
                code = (form.get("otp", [""])[0] or "").strip()
                with sess_lock:
                    rec = hub.web_pending_otp.get(tok)
                if not rec or rec["exp"] < time.time() or rec["ip"] != ip:
                    hub.web_pending_otp.pop(tok, None)
                    self._send(200, _login_page(err=1))  # 超时/失效 → 回密码页重来
                    return
                if secrets.compare_digest(code, rec["code"]):
                    hub.web_pending_otp.pop(tok, None)
                    token = secrets.token_urlsafe(32)
                    with sess_lock:
                        sessions[token] = time.time() + 7 * 86400
                        login_fails.pop(ip, None)
                        otp_fails.pop(ip, None)
                    # 登录成功通知：发给所有管理员，让全员知道有人进了后台
                    try:
                        ua = (self.headers.get("User-Agent") or "")[:120]
                        if hub._bot_app and hub._bot_loop:
                            async def _notify():
                                ntxt = (f"✅ <b>后台登录成功</b>\n\n"
                                        f"来源 IP：<code>{ip}</code>\n"
                                        f"时间：{datetime.now(hub.BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n"
                                        f"设备：<code>{_esc(ua)}</code>")
                                for rid in _admin_receivers():
                                    try:
                                        await hub._bot_app.bot.send_message(chat_id=rid, text=ntxt, parse_mode="HTML")
                                    except Exception:
                                        pass
                            asyncio.run_coroutine_threadsafe(_notify(), hub._bot_loop).result(8)
                    except Exception:
                        pass  # 通知失败不影响登录
                    self._redirect("/", cookie=f"wb_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800")
                else:
                    with sess_lock:
                        new_cnt = _c2 + 1
                        otp_fails[ip] = [new_cnt, time.time() + 600 if new_cnt >= 5 else 0]
                    self._send(200, _otp_page(tok, err="验证码错误，请重试"))
                return

            def _post_save(self, r):
                form = r.form
                if not _check_session(self.headers.get("Cookie")):
                    self._redirect("/"); return
                # ★ 表单里的 cid 是靠 JS 统一注入的（页面有十几处 /save 表单，
                #   逐个手写容易漏）。禁用 JS 时这个字段就不存在 →
                #   原来会**静默写成全局默认**，一次改动波及所有群。
                #   兜底：从 Referer 里把 cid 找回来（管理员都是从 ?cid=xxx 的页面点过来的）。
                if "cid" not in form:
                    try:
                        from urllib.parse import urlparse as _up, parse_qs as _pq
                        _c = _pq(_up(self.headers.get("Referer") or "").query).get("cid", [None])[0]
                    except Exception:
                        _c = None
                    if _c:
                        form["cid"] = [_c]
                group = form.get("group", [""])[0]
                if group == "security":
                    # security 组字段（登录二次验证）与改密分开处理：
                    # 密码留空 = 不改凭据，只改开关（以前这里只认 new_password，开关上不了线）
                    _sec_cfg = {}
                    for _k, _g, _l, _ft, _lo, _hi, _grp in hub.SETTINGS_FIELDS:
                        if _grp != "security":
                            continue
                        if _ft == "bool":
                            _sec_cfg[_k] = "1" if _k in form else "0"   # 复选框不勾 = 表单里没这个键
                        elif _k in form:
                            _sec_cfg[_k] = form[_k][0]
                    _np = (form.get("new_password", [""])[0] or "").strip()
                    if _np and len(_np) < 4:   # 此前不足 4 位被 save_settings 静默跳过，却仍提示"已保存"
                        self._redirect("/page/security?err=" + quote("密码至少 4 位（其余改动已保存）")); return
                    hub.save_settings(_sec_cfg, _np)
                    if _np:
                        # 改密后作废其他会话，只保留当前这个（旧会话继续可用等于白改）
                        try:
                            _mm = re.search(r"wb_session=([^;\s]+)", self.headers.get("Cookie") or "")
                            _cur = _mm.group(1) if _mm else ""
                            with sess_lock:
                                for _s in [s for s in list(sessions) if s != _cur]:
                                    sessions.pop(_s, None)
                        except Exception:
                            pass
                    self._redirect("/page/security?saved=1"); return
                valid_keys = hub._cross_keys(group) or {k for k, _g, _l, _t, _lo, _hi, grp in hub.SETTINGS_FIELDS if grp == group}
                # _fields：弹窗表单声明本次只提交这些键 → bool 补 0 / multi 清空只作用于声明的键，
                # 组内其他开关参数不受影响（不然保存一个弹窗会把没提交的开关全部关掉）
                _only_raw = form.get("_fields", [""])[0]
                only_set = ({s.strip() for s in _only_raw.split(",") if s.strip()} or None)
                cfg = {}
                for k, v in form.items():
                    if k not in valid_keys or (only_set and k not in only_set):
                        continue
                    ft = next((t for kk, _g, _l, t, _lo, _hi, _grp in hub.SETTINGS_FIELDS if kk == k), "")
                    if ft == "sep":
                        continue          # 分组标题行不落盘
                    cfg[k] = ",".join(v) if ft == "multi" else v[0]
                for k, _g, _l, ft, _lo, _hi, _grp in hub.SETTINGS_FIELDS:  # checkbox 未勾选时表单不含该键 → 显式补 0（仅 bool）
                    if k in valid_keys and (only_set is None or k in only_set):
                        if ft == "bool":
                            cfg.setdefault(k, "0")
                        elif ft == "multi":
                            cfg.setdefault(k, "")   # 全不勾 = 关闭全部规则
                try:
                    _gcid = int((form.get("cid", [""])[0] or "0") or 0)
                except (TypeError, ValueError):
                    _gcid = 0
                # 带 cid = 群级保存（只存与该群全局默认不同的项）；不带 = 原来的全局保存
                # ★ P2-12 提示：改「每日重置时间」要在下一轮睡醒才生效（最多等 24 小时）。
                #   必须**在 save_settings 之前**比 —— save_settings 内部会调
                #   apply_settings 立即把 DAILY_RESET_TIME 改成新值，sget 就读不到旧值了。
                #   注意：sget 取的是**变量名**（DAILY_RESET_TIME），不是 key（daily_reset_time）。
                _extra = ""
                if "daily_reset_time" in cfg:
                    _note = _daily_reset_change_note(str(hub.sget("DAILY_RESET_TIME") or ""), cfg["daily_reset_time"])
                    if _note:
                        _extra = f"&note={quote(_note)}"
                applied = hub.save_group_settings(_gcid, cfg) if _gcid else hub.save_settings(cfg)
                skipped = [k for k in cfg if k not in applied]
                self._redirect(f"/page/{group}?saved=1" + (f"&cid={_gcid}" if _gcid else "")
                               + ("&bad=1" if skipped else "") + _extra)
                return

            def log_message(self, *args):
                pass  # 抑制访问日志，避免刷屏

        server = ThreadingHTTPServer(("0.0.0.0", port), _AdminHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        hub.logger.info("网页后台已启动：端口 %s（/health 健康检查 · / 设置面板）", port)
    except Exception:
        hub.logger.exception("网页后台启动失败（不影响 bot 运行）")
