"""网页后台 · 积分 · impexp（points/impexp）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（C 批，2026-09-13）。
  原条件：`if gkey == "points" and sub == "impexp":`

  ⚠️ 本页依赖总线的公共前缀 `subs` / `sname`，已原样抄进函数开头。

⚠️ 搬运铁律（本项目硬规矩）：
  - 模块顶层只准 `from core import hub`，符号一律 `hub.X` 延迟绑定；
  - **禁止** `from bot import ...`；
  - 函数内可自由 `hub.` 访问所有全局。
"""
from core import hub
from core.pages._common import (  # noqa: F401
    group_options, field_rows, savebar, flt_bar,
    _group_options, _members_body, _all_user_options,
    _field_rows, _savebar, _flt_bar,
)
import html  # noqa: F401
import time
from datetime import datetime



def page_points_impexp(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """积分 · impexp（points/impexp）"""
    subs = {k: n for k, n in hub.SUBPAGES.get(gkey, [])}
    sname = subs.get(sub, sub)
    opts = _group_options()
    body = (f"<h1>{gicon} {sname}</h1>"
            f"<div class='sub'>按群导出/导入积分。导入会<b>覆盖</b>该群已有积分，务必先用模板核对格式</div>{msg}{err}"
            "<div class='card'><h3>📥 导出</h3>"
            "<form method='get' action='/points_export'>"
            f"<div class='row'><div class='lbl'>选择群</div><select name='cid'>{opts}</select></div>"
            "<button type='submit'>⬇ 导出 CSV（Excel 可直接打开）</button></form></div>"
            "<div class='card'><h3>📤 导入</h3>"
            "<div class='sub'>表头必须包含：用户ID 和 积分（昵称列可选）。同群已有积分将被覆盖</div>"
            "<p><a href='/points_template'>⬇ 下载模板</a>　请先下载模板，按格式填写</p>"
            "<form method='post' action='/points_import' enctype='multipart/form-data'>"
            f"<div class='row'><div class='lbl'>导入到群</div><select name='cid'>{opts}</select></div>"
            "<div class='row'><div class='lbl'>数据文件<small>.csv / .xls / .xlsx</small></div>"
            "<input type='file' name='file' accept='.csv,.xls,.xlsx' required></div>"
            "<div class='row'><div class='lbl'>⚠️ 覆盖确认<small>导入为覆盖式写入，不可撤销</small></div>"
            "<label style='display:flex;gap:8px;align-items:center'><input type='checkbox' name='confirm' value='1' required> 我确认覆盖所选群的全部积分</label></div>"
            "<button type='submit'>✅ 确认导入</button></form></div>")
    return body
