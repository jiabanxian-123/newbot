"""网页后台 · 积分 · buypkg（points/buypkg）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（C 批，2026-09-13）。
  原条件：`if gkey == "points" and sub == "buypkg":`

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
)
import html  # noqa: F401
import time
from datetime import datetime



def page_points_buypkg(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """积分 · buypkg（points/buypkg）"""
    subs = {k: n for k, n in hub.SUBPAGES.get(gkey, [])}
    sname = subs.get(sub, sub)
    pkg_rows = ""
    for i, p in enumerate(hub.buy_packages):
        on = bool(p.get("on"))
        st = "<span class='c-ok'>启用</span>" if on else "<span class='c-dim'>停用</span>"
        pkg_rows += (f"<tr><td>{esc(str(p.get('name', '?')))}</td>"
                     f"<td>¥{p.get('cny', 0)}</td><td>{p.get('points', 0)}</td>"
                     f"<td>{p.get('sort', 0)}</td><td>{st}</td>"
                     f"<td><a href='/pkg_toggle/{i}'>{'停用' if on else '启用'}</a> · "
                     f"<a href='/pkg_del/{i}' class='c-bad'>删除</a></td></tr>")
    if not pkg_rows:
        pkg_rows = "<tr><td colspan='6' class='empty'>暂无套餐</td></tr>"
    body = (f"<h1>{gicon} {sname}</h1><div class='sub'>玩家发「充值」看套餐列表，发「充值 套餐名」提交申请（管理员确认到账）。排序小的排前面</div>{msg}"
            "<div class='card'><table class='tbl'><tr><th>名称</th><th>金额(CNY)</th><th>积分</th><th>排序</th><th>状态</th><th>操作</th></tr>"
            + pkg_rows + "</table>"
            "<form method='post' action='/pkg_add' class='fx-g10-mt12'>"
            "<input type='text' name='name' placeholder='套餐名称' required class='f2'>"
            "<input type='number' name='cny' placeholder='金额 ¥' required min='0' class='f1'>"
            "<input type='number' name='points' placeholder='积分' required min='1' class='f1'>"
            "<input type='number' name='sort' value='0' placeholder='排序' class='f1'>"
            "<button class='m0'>➕ 新增套餐</button></form></div>")
    return body
