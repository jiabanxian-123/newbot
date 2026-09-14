"""网页后台 · 成员 · 名单（mlist）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（B 批，2026-09-13）。
  原条件：`if gkey == 'members' and sub == 'mlist':`
  本页是**唯一**用到 `sel_flt_cid` 的成员页；其余参数只为签名统一。

⚠️ 搬运铁律（本项目硬规矩）：
  - 模块顶层只准 `from core import hub`，符号一律 `hub.X` 延迟绑定；
  - **禁止** `from bot import ...`；
  - 函数内可自由 `hub.` 访问所有全局（常量、函数、状态）。

返回值：通常 `body`（str）。若返回 `(body, redirect)` 二元组，表示 `_admin_page`
需要按 `redirect = (gkey, kwargs)` 重跑一次（自递归用）。
"""
from core import hub
from core.pages._common import (  # noqa: F401
    esc,
    group_options, field_rows, savebar, flt_bar,
    _group_options,
)
import html  # noqa: F401
from datetime import datetime
from urllib.parse import quote


def page_members_mlist(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """成员 · 名单（mlist）"""
    fl = flt or {}
    sel_cid = fl.get("cid", 0)
    q = (fl.get("q") or "").strip()
    per = fl.get("per", 20) if fl.get("per", 20) in (10, 20, 50, 100) else 20
    page = max(1, fl.get("page", 1))
    members = []
    if sel_cid:
        profs = hub.member_profiles.get(sel_cid, {})
        uids = set(profs) | set(hub.game_chips.get(sel_cid, {})) | set(hub.member_joined_at.get(sel_cid, {}))
        now = hub.now_bj()
        for u in uids:
            pr = profs.get(u, {})
            joined = hub.member_joined_at.get(sel_cid, {}).get(u, 0)
            last = str(pr.get("last", "") or "")
            days = None
            if last:
                try:
                    days = (now - datetime.strptime(last, "%Y-%m-%d %H:%M").replace(tzinfo=hub.BEIJING_TZ)).days
                except ValueError:
                    pass
            members.append({"uid": u, "name": str(pr.get("name", f"用户{u}")),
                            "msgs": int(pr.get("msgs", 0) or 0), "last": last or "-",
                            "days": days, "joined": joined,
                            "joined_txt": (datetime.fromtimestamp(float(joined), hub.BEIJING_TZ).strftime("%Y-%m-%d %H:%M") if joined else "早于机器人进群"),
                            "chips": hub.game_chips.get(sel_cid, {}).get(u, 0),
                            "warn": hub.warn_counts.get(sel_cid, {}).get(u, 0)})
        if q:
            members = [x for x in members if q in x["name"] or q in str(x["uid"])]
        if fl.get("never"):
            members = [x for x in members if x["msgs"] == 0]
        if fl.get("silent"):
            members = [x for x in members if x["days"] is not None and x["days"] >= fl["silent"]]
        _d0 = hub._parse_dt_bj(fl.get("join_from", ""))
        if _d0:
            members = [x for x in members if x["joined"] and x["joined"] >= _d0.timestamp()]
        _d1 = hub._parse_dt_bj(fl.get("join_to", ""))
        if _d1:
            members = [x for x in members if x["joined"] and x["joined"] <= _d1.timestamp()]
        members.sort(key=lambda x: (-x["joined"], -x["chips"]))
    total = len(members)
    pages = max(1, (total + per - 1) // per)
    page = min(page, pages)
    def _mb(op, uid, label, color="#5b5b76"):
        return ("<form class='i-m0' method='post' action='/memops'>"
                f"<input type='hidden' name='op' value='{op}'>"
                f"<input type='hidden' name='cid' value='{sel_cid}'>"
                f"<input type='hidden' name='uid' value='{uid}'>"
                f"<button type='submit' class='pbtn' style='background:{color}'>{label}</button></form>")
    admins_map = hub._group_admins_get(sel_cid) if sel_cid else {}   # 群主/管理员徽章（5 分钟缓存）
    rows_html = ""
    for x in members[(page - 1) * per: page * per]:
        in_wl = x["uid"] in hub.whitelist.get(sel_cid, set())
        nm = esc(x["name"])
        av_ch = esc((x["name"] or "?")[0].upper())
        av_bg = hub._AV_COLORS[x["uid"] % len(hub._AV_COLORS)]
        role = admins_map.get(x["uid"])
        role_html = ("<span class='role owner'>👑 群主</span>" if role == "owner"
                     else "<span class='role admin'>🛡 管理员</span>" if role == "admin" else "")
        ops = (_mb("wl_del", x["uid"], "删白", "#8a6d3b") if in_wl
               else _mb("wl_add", x["uid"], "✅ 加白", "#2f9e5f")) \
            + " " + _mb("ban", x["uid"], "⛔ 封禁", "#c0392b") \
            + " " + _mb("kick", x["uid"], "👋 踢出", "#c0392b") \
            + ("<div class='more'><button type='button' class='pbtn' style='background:#4a4462'>"
               "更多 ▾</button><div class='menu'>"
               + _mb("warn_add", x["uid"], "⚠️ 警告 +1", "#b8860b")
               + " " + _mb("warn_sub", x["uid"], "⚠️ 警告 −1", "#5b5b76")
               + " <a href='/page/members/titg?cid=" + str(sel_cid) + "&uid=" + str(x["uid"]) + "'>"
               + "<button type='button' class='pbtn' style='background:#8a6d3b'>🏅 称号</button></a>"
               + "</div></div>")
        rows_html += (f"<tr><td><div style='display:flex;align-items:center;gap:9px;min-width:0'>"
                      f"<span class='av' style='background:{av_bg}'>{av_ch}</span>"
                      f"<span style='overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{nm}{role_html}</span></div></td>"
                      f"<td><code>{x['uid']}</code></td>"
                      f"<td>{x['joined_txt']}</td>"
                      f"<td>{x['last']}</td>"
                      f"<td>{x['chips']:,}</td>"
                      f"<td>{x['warn']}</td>"
                      f"<td class='nowrap'>{ops}</td></tr>")
    if not rows_html:
        rows_html = f"<tr><td colspan='7' class='empty'>{'左侧选一个群后展示成员' if not sel_cid else '该群暂无成员档案（发过言/进过群才会建档）'}</td></tr>"
    clear_warn_btn = (f"<form class='i-m0' method='post' action='/memops'>"
                      f"<input type='hidden' name='op' value='warn_clear_all'>"
                      f"<input type='hidden' name='cid' value='{sel_cid}'>"
                      f"<input type='hidden' name='uid' value='0'>"
                      "<button type='submit' style='padding:4px 12px;cursor:pointer;background:#8a3b3b;color:#fff;border:none;border-radius:6px'>🧹 清除全部警告</button></form>") if sel_cid else ""
    chips_clear_btn = (f"<form class='i-m0' method='post' action='/memops'>"
                       f"<input type='hidden' name='op' value='chips_clear_all'>"
                       f"<input type='hidden' name='cid' value='{sel_cid}'>"
                       f"<input type='hidden' name='uid' value='0'>"
                       "<button type='submit' onclick=\"return confirm('确定清空该群所有成员的积分？此操作不可恢复！')\" "
                       "style='padding:4px 12px;cursor:pointer;background:#8a3b3b;color:#fff;border:none;border-radius:6px'>💰 清除全部积分</button></form>") if sel_cid else ""
    impexp_link = ("<a href='/page/points/impexp'><button type='button' style='padding:4px 12px;cursor:pointer;background:#3d6b4f;color:#fff;border:none;border-radius:6px'>📥 积分导入/导出</button></a>") if sel_cid else ""
            # 🏷 一键补齐成员标签（2026-09-11：标签功能此前从未生效过，老玩家全都没标签）
    tag_sync_btn = (f"<form class='i-m0' method='post' action='/memops'>"
                    f"<input type='hidden' name='op' value='tag_sync_all'>"
                    f"<input type='hidden' name='cid' value='{sel_cid}'>"
                    "<button type='submit' onclick=\"return confirm('将把该群所有「有积分」成员的标签覆盖为他们的积分称号，确定？')\" "
                    "style='padding:4px 12px;cursor:pointer;background:#4a5f8a;color:#fff;border:none;border-radius:6px'>🏷 同步成员标签</button></form>") if sel_cid else ""
    body = (f"<h1>{gicon} {gname}</h1><div class='sub'>数据来自成员档案+积分账本+进群事件；封禁/踢出需要 bot 是群管理员</div>{msg}"
            f"<div class='card'><h3>🧹 批量操作 {chips_clear_btn} {clear_warn_btn} {tag_sync_btn} {impexp_link}</h3></div>"
            "<div class='card mt18' >"
            "<form method='get' action='/page/members/mlist' style='display:flex;flex-wrap:wrap;gap:10px;align-items:end'>"
            f"<div><div class='sub'>群</div><select name='cid' required>{_group_options(selected=sel_cid)}</select></div>"
            f"<div><div class='sub'>用户名/昵称/ID</div><input type='text' name='q' value='{esc(q, quote=True)}'></div>"
            "<div><div class='sub'>只看</div><label class='chk'><input type='checkbox' name='never' value='1' "
            + ("checked" if fl.get("never") else "") + "> 从未发言</label></div>"
            f"<div><div class='sub'>超过N天未发言</div><input type='number' name='silent' value='{fl.get('silent', 0) or ''}' min='0' style='width:90px'></div>"
            f"<div><div class='sub'>进群时间从</div><input type='date' name='join_from' value='{esc(fl.get('join_from', ''), quote=True)}'></div>"
            f"<div><div class='sub'>至</div><input type='date' name='join_to' value='{esc(fl.get('join_to', ''), quote=True)}'></div>"
            f"<div><div class='sub'>每页</div><select name='per'>"
            + "".join(f"<option value='{v}'{' selected' if v == per else ''}>{v}</option>" for v in (10, 20, 50, 100))
            + "</select></div>"
            "<button type='submit'>🔍 搜索</button> "
            "<a href='/page/members/mlist'><button type='button'>♻️ 重置</button></a>"
            "</form></div>"
            f"<div class='card mt18' ><div class='sub'>共 {total} 条记录 · 第 {page}/{pages} 页 · 点列头可排序</div>"
            "<table class='tbl cp'><tr><th data-s>成员</th><th data-s>用户ID</th><th data-s>进群时间</th>"
            "<th data-s>最近发言</th><th data-s>积分</th><th data-s>警告</th><th>操作</th></tr>"
            + rows_html + "</table>"
            "<div style='margin-top:12px;display:flex;gap:10px'>"
            + (f"<a href='/page/members/mlist?cid={sel_cid}&q={quote(q)}&per={per}&page={page-1}'><button type='button'>‹ 上一页</button></a>" if page > 1 else "")
            + (f"<a href='/page/members/mlist?cid={sel_cid}&q={quote(q)}&per={per}&page={page+1}'><button type='button'>下一页 ›</button></a>" if page < pages else "")
            + "</div></div>")
    return body
