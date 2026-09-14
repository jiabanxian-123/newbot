# -*- coding: utf-8 -*-
"""积分商城订单页（原 `_admin_page` 的 `elif gkey=="points" and sub=="mallord"` 分支，13 行）。

2026-09-13 从 core/web.py 原样搬出，字符级一致（由页面快照比对守卫保证）。
"""
import html

from core import hub
from core.pages._common import flt_bar, paginate, esc


def page_points_mallord(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    _ord_src = [o for o in reversed(hub.mall_orders[-200:]) if not sel_flt_cid or int(o.get("cid", 0) or 0) == sel_flt_cid]
    rows, foot = paginate(_ord_src, flt, "/page/points/mallord",
                          qs=({"cid": sel_flt_cid} if sel_flt_cid else {}))
    ord_rows = "".join(
        f"<tr><td>{esc(str(o.get('ts', '')))}</td><td>{o.get('cid')} {esc(hub.chat_name_cache.get(int(o.get('cid', 0) or 0)) or str(o.get('cid', '')))}</td>"
        f"<td>{o.get('uid')} {esc(hub.user_names.get(o.get('uid'), ''))}</td>"
        f"<td>{esc(str(o.get('item', '')))}</td><td>{o.get('price')}</td></tr>"
        for o in rows)
    if not ord_rows:
        ord_rows = "<tr><td colspan='5' class='empty'>还没有兑换订单</td></tr>"
    body = (f"<h1>{gicon} {sname}</h1><div class='sub'>商城兑换订单</div>{msg}"
            + flt_bar("/page/points/mallord") +
            "<div class='card'><table class='tbl'><tr><th>时间</th><th>群</th><th>用户</th><th>商品</th><th>价格</th></tr>"
            + ord_rows + "</table>" + foot + "</div>")
    return body
