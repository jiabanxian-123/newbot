"""网页后台 · 成员 · 主页面（join / titg / records / ops）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（B 批，2026-09-13）。
  原条件：`elif gkey == 'members':`
  内含 join / titg / records / ops 四个子页 + 兜底分支。

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
    _group_options, _members_body, _field_rows, _savebar,
)
import html  # noqa: F401


def page_members_main(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """成员 · 主页面（join / titg / records / ops）"""
    if sub == "join":
        body = (f"<h1>{gicon} 入群与观察</h1><div class='sub'>新成员观察期与入群欢迎，保存立即生效</div>{msg}"
                "<div class='card'><form method='post' action='/save'>"
                "<input type='hidden' name='group' value='members/join'>"
                + _field_rows("members/join") +
                _savebar() + "</form></div>")
    elif sub == "titg":
                # 🏅 称号加封：管理员从称号库直接挑一个称号给群友（不扣积分、不走商店兑换）
        _gsel = int(flt.get("cid") or 0)
        _usel = int(flt.get("uid") or 0)
        _mem_opts = ""
        if _gsel:
            _profs = hub.member_profiles.get(_gsel, {})
            _seen_m = {}
            for _u in (set(_profs) | set(hub.game_chips.get(_gsel, {}))
                       | set(hub.member_joined_at.get(_gsel, {}))):
                _seen_m[_u] = (hub.user_names.get(_u)
                               or (_profs.get(_u) or {}).get("name")
                               or f"用户{_u}")
            _mem_opts = "".join(
                f"<option value='{_u}'{' selected' if _u == _usel else ''}>"
                f"{esc(str(_n))}（{_u}）</option>"
                for _u, _n in sorted(_seen_m.items(), key=lambda x: str(x[1])))
        _title_opts = "".join(f"<option value='{t}'>{hub.title_icon(t)}{t}</option>"
                              for t in hub.all_titles())
                # 该群已持有称号的人（展示层实际前缀；撤销按钮逐个称号一个表单）
        _own_rows = ""
        if _gsel:
            _cands = (set(hub.member_profiles.get(_gsel, {}))
                      | set(hub.game_chips.get(_gsel, {}))
                      | set(hub.user_titles.keys()))
            for _u in sorted(_cands):
                _ts = sorted(hub.user_titles.get(_u) or set())
                if not _ts:
                    continue
                _nm = esc(str(hub.user_names.get(_u) or f"用户{_u}"))
                _btns = " ".join(
                    "<form class='i' method='post' action='/adminops2'>"
                    "<input type='hidden' name='op' value='titlerevoke'>"
                    f"<input type='hidden' name='uid' value='{_u}'>"
                    f"<input type='hidden' name='cid' value='{_gsel}'>"
                    f"<input type='hidden' name='title' value='{esc(t, quote=True)}'>"
                    f"<button style='margin:0;padding:4px 10px;font-size:12px;background:#c0392b;margin-top:0'>撤销 {esc(t)}</button></form>"
                    for t in _ts)
                _own_rows += (f"<tr><td><code>{_u}</code> {_nm}</td>"
                              f"<td>{esc(chr(32).join(hub.title_icon(t) + t for t in _ts))}</td>"
                              f"<td class='nowrap'>{_btns}</td></tr>")
        _mem_sel = ("<select name='uid' required style='flex:1;min-width:190px'>"
                    f"<option value=''>— 请选择成员 —</option>{_mem_opts}</select>") if _gsel else \
                   "<div class='sub f1' >先在上面选好群，成员下拉会自动带出该群玩家</div>"
        body = (f"<h1>{gicon} 称号加封</h1>"
                "<div class='sub'>管理员直接把称号加封给群友：<b>不扣积分、不走商店那条路</b>，加封后立即出现在聊天里的名字前缀。赌神仍全局唯一（封新自动撤旧）。称号库里 <b>" + str(len(hub.all_titles())) + "</b> 个称号全部可选。</div>"
                f"{msg}<div class='card'><h3>① 选择群组</h3>"
                "<form method='get' action='/page/members/titg' class='fx-g10-ae-wrap'>"
                f"<div class='f1-200'><div class='sub'>群</div><select name='cid' required>{_group_options(selected=_gsel)}</select></div>"
                "<button type='submit' class='mt0'>🔍 载入成员</button></form></div>"
                "<div class='card mt18' ><h3>② 加封称号</h3>"
                "<form method='post' action='/adminops2' class='fx-g10-ae-wrap'>"
                "<input type='hidden' name='op' value='titlegrant'>"
                f"<input type='hidden' name='cid' value='{_gsel}'>"
                f"<div style='flex:2;min-width:200px'><div class='sub'>成员</div>{_mem_sel}</div>"
                f"<div style='flex:2;min-width:200px'><div class='sub'>称号</div><select name='title' required>{_title_opts}</select></div>"
                "<button type='submit' class='mt0'>🏅 加封</button></form></div>"
                "<div class='card mt18' ><h3>🏆 该群已持有称号</h3>"
                "<table class='tbl'><tr><th>成员</th><th>称号</th><th>操作</th></tr>"
                + (_own_rows or "<tr><td colspan='3'>该群暂无成员持有称号</td></tr>")
                + "</table></div>")
    else:
        body = (f"<h1>{gicon} {gname}</h1><div class='sub'>数据只读展示，管理操作在群里用命令完成</div>{msg}"
                + _members_body("records" if sub == "records" else "ops", pgs=flt))
    return body
