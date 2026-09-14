"""网页后台 · 积分 · 通用表单兜底

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（C 批，2026-09-13）。
  原为积分子页总线末尾的兜底 `else:`（通用表单页）。

  ⚠️ 依赖总线的公共前缀 `subs` / `sname`，已原样抄进函数开头。

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



def page_points_fallback(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """积分子页兜底：通用「按 group 存设置」表单页。"""
    subs = {k: n for k, n in hub.SUBPAGES.get(gkey, [])}
    sname = subs.get(sub, sub)
    body = (f"<h1>{gicon} {sname}</h1><div class='sub'>保存立即生效，无需重启</div>{msg}"
            "<div class='card'><form method='post' action='/save'>"
            f"<input type='hidden' name='group' value='{gkey}/{sub}'>"
            + _field_rows(f"{gkey}/{sub}") +
            _savebar() + "</form></div>")
    return body
