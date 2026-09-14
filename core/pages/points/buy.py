"""网页后台 · 积分 · buy（points/buy）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（C 批，2026-09-13）。
  原条件：`if gkey == "points" and sub == "buy":`

  ⚠️ 本页依赖总线的公共前缀 `subs` / `sname`，已原样抄进函数开头。

⚠️ 搬运铁律（本项目硬规矩）：
  - 模块顶层只准 `from core import hub`，符号一律 `hub.X` 延迟绑定；
  - **禁止** `from bot import ...`；
  - 函数内可自由 `hub.` 访问所有全局。
"""
from core import hub
from core.pages._common import (  # noqa: F401
    esc,
    group_options, field_rows, savebar, flt_bar, paginate,
    _group_options, _members_body, _all_user_options,
    _field_rows, _savebar, _flt_bar,
)
import html  # noqa: F401
import time
from datetime import datetime



def page_points_buy(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """积分 · buy（points/buy）"""
    subs = {k: n for k, n in hub.SUBPAGES.get(gkey, [])}
    sname = subs.get(sub, sub)
    _pend_src = [(oid, o) for oid, o in hub.buy_orders.items()
                 if not sel_flt_cid or int(o.get("cid", 0) or 0) == sel_flt_cid]
    _pend_rows, _pend_foot = paginate(_pend_src, flt, "/page/points/buy",
                                      qs=({"cid": sel_flt_cid} if sel_flt_cid else {}))
    pend_rows = "".join(
        f"<tr><td><code>{oid}</code></td><td>{o.get('cid')} {esc(hub.chat_name_cache.get(int(o.get('cid', 0) or 0)) or str(o.get('cid', '')))}</td>"
        f"<td>{o.get('uid')} {esc(hub.user_names.get(o.get('uid'), ''))}</td>"
        f"<td>{o.get('amount')}</td><td>{esc(str(o.get('ts', '')))}</td></tr>"
        for oid, o in _pend_rows)
    if not pend_rows:
        pend_rows = ("<tr><td colspan='5' class='empty'>"
                     "没有待处理的购买申请（群里点按钮处理）</td></tr>")
    body = (f"<h1>{gicon} {sname}</h1><div class='sub'>保存立即生效 · 套餐在「积分套餐管理」页配置</div>{msg}"
            + _flt_bar("/page/points/buy") +
            "<div class='card'><h3>🧾 待处理购买申请</h3>"
            "<table class='tbl'><tr><th>单号</th><th>群</th><th>用户</th><th>数量</th><th>时间</th></tr>"
            + pend_rows + "</table>" + _pend_foot + "</div>"
            "<div class='card mt18' ><form method='post' action='/save'>"
            "<input type='hidden' name='group' value='points/buy'>"
            + _field_rows("points/buy") + _savebar() + "</form></div>")
    return body
