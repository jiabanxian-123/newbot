"""网页后台 · 积分 · level（points/level）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（C 批，2026-09-13）。
  原条件：`if gkey == "points" and sub == "level":`

  ⚠️ 本页依赖总线的公共前缀 `subs` / `sname`，已原样抄进函数开头。

⚠️ 搬运铁律（本项目硬规矩）：
  - 模块顶层只准 `from core import hub`，符号一律 `hub.X` 延迟绑定；
  - **禁止** `from bot import ...`；
  - 函数内可自由 `hub.` 访问所有全局。
"""
from core import hub
from core.pages._common import (  # noqa: F401
    esc,
    group_options, field_rows, savebar, flt_bar,
    _group_options, _members_body, _all_user_options,
    _field_rows, _savebar, _flt_bar,
    _perm_boxes, _perm_summary,
)
import html  # noqa: F401
import time
from datetime import datetime



def page_points_level(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """积分 · level（points/level）"""
    subs = {k: n for k, n in hub.SUBPAGES.get(gkey, [])}
    sname = subs.get(sub, sub)
            # 等级表：名称 / 最低积分 / 状态 / 消息权限（8 类勾选），支持行内编辑
    hub._normalize_levels()
    edit_i = int((flt or {}).get("edit", -1))
    if not (0 <= edit_i < len(hub.POINT_LEVELS)):
        edit_i = -1
    lv_rows = ""
    for i, x in enumerate(hub.POINT_LEVELS):
        on = int(x.get("on", 1) or 0)
        st = ("<span class='c-ok'>启用</span>" if on
              else "<span class='c-dim'>停用</span>")
        if i == edit_i:
                    # 编辑态：整行换成表单（权限 8 勾选 + 状态开关）
            lv_rows += (
                f"<tr style='background:rgba(120,120,200,.08)'>"
                f"<td colspan='5'><form method='post' action='/level_edit'>"
                f"<input type='hidden' name='i' value='{i}'>"
                f"<div style='display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap'>"
                f"<div style='flex:2;min-width:140px'><div class='lbl'>等级名称</div>"
                f"<input type='text' name='name' value='{esc(str(x.get('name', '')))}' "
                f"required maxlength='12' class='w100'></div>"
                f"<div style='flex:1;min-width:110px'><div class='lbl'>最低积分</div>"
                f"<input type='number' name='value' value='{int(x.get('value', 0) or 0)}' "
                f"required min='0' class='w100'></div>"
                f"<div style='min-width:110px'><div class='lbl'>状态</div>"
                f"<label class='tg'><input type='checkbox' name='on' value='1'"
                f"{' checked' if on else ''}><span class='sl'></span></label></div></div>"
                f"<div class='mt10'><div class='lbl'>等级消息权限"
                f"<small>勾选=允许；未勾选的消息类型会被撤回并提示</small></div>"
                f"{_perm_boxes(x.get('perms'))}</div>"
                f"<div style='display:flex;gap:10px;margin-top:10px'>"
                f"<button class='m0'>💾 保存</button>"
                f"<a href='/page/points/level' style='align-self:center'>取消</a></div>"
                f"</form></td></tr>")
        else:
            lv_rows += (
                f"<tr><td><b>L{i + 1}</b> {esc(str(x.get('name', '?')))}</td>"
                f"<td>{int(x.get('value', 0) or 0)}</td><td>{st}</td>"
                f"<td>{_perm_summary(x.get('perms'))}</td>"
                f"<td><a href='/page/points/level?edit={i}'>编辑</a> · "
                f"<a href='/level_toggle/{i}'>{'停用' if on else '启用'}</a> · "
                f"<a href='/level_del/{i}' class='c-bad'>删除</a></td></tr>")
    if not lv_rows:
        lv_rows = ("<tr><td colspan='5' class='empty'>"
                   "暂无等级数据，先新增等级</td></tr>")
    body = (f"<h1>{gicon} {sname}</h1><div class='sub'>按「累计积分」判定等级（消费不掉级，只升不降）；"
            f"停用的等级不参与判定与权限；升级自动群内通知（下方可开关）。保存立即生效</div>{msg}"
            "<div class='card'><h3>🎖 等级列表（按最低积分升序）</h3>"
            "<table class='tbl'><tr><th>等级名称</th><th>最低积分</th><th>状态</th>"
            "<th>消息权限</th><th>操作</th></tr>"
            + lv_rows + "</table>"
            "<form method='post' action='/level_add' class='mt16'>"
            "<h3 style='margin-bottom:8px'>➕ 新增积分等级</h3>"
            "<div style='display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap'>"
            "<div style='flex:2;min-width:140px'><div class='lbl'>等级名称</div>"
            "<input type='text' name='name' placeholder='≤12字，如 铜牌' required maxlength='12' class='w100'></div>"
            "<div style='flex:1;min-width:110px'><div class='lbl'>最低积分</div>"
            "<input type='number' name='value' placeholder='如 100' required min='0' class='w100'></div>"
            "<div style='min-width:110px'><div class='lbl'>状态</div>"
            "<label class='tg'><input type='checkbox' name='on' value='1' checked>"
            "<span class='sl'></span></label></div></div>"
            "<div class='mt10'><div class='lbl'>等级消息权限"
            "<small>勾选=允许；不勾则拦截。默认全勾（不限制）</small></div>"
            + _perm_boxes(hub.LEVEL_PERM_DEFAULT) +
            "</div><button class='mt10'>➕ 新增等级</button></form></div>"
            "<div class='card mt18' ><form method='post' action='/save'>"
            "<input type='hidden' name='group' value='points/level'>"
            + _field_rows("points/level")
            + "<div class='sub mt16' >占位符：<code>{name}</code> <code>{level}</code> <code>{balance}</code>；群内发「"
              + esc(str(hub.LEVEL_CMD)) + "」查询自己的等级"
            + "（查询触发词在「命令管理」页改，本页不重复配置以免两处打架）</div>" +
            _savebar("保存通知设置") + "</form></div>")
    return body
