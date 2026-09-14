# -*- coding: utf-8 -*-
"""积分商城页（原 `_admin_page` 的 `elif gkey=="points" and sub=="mall"` 分支，25 行）。

2026-09-13 从 core/web.py 原样搬出，字符级一致（由页面快照比对守卫保证）。
"""
import html

from core import hub
from core.pages._common import field_rows, savebar, esc


def page_points_mall(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    mall_rows = ""
    for i, x in enumerate(hub.MALL_ITEMS):
        on = bool(x.get("on", True))
        st = "<span class='c-ok'>上架</span>" if on else "<span class='c-dim'>下架</span>"
        mall_rows += (f"<tr><td>{esc(str(x.get('name', '?')))}</td>"
                      f"<td>{hub._mall_price(x)}</td>"
                      f"<td>{esc(str(x.get('desc', '') or ''))}</td><td>{st}</td>"
                      f"<td><a href='/mall_toggle/{i}'>{'下架' if on else '上架'}</a> · "
                      f"<a href='/mall_del/{i}' class='c-bad'>删除</a></td></tr>")
    if not mall_rows:
        mall_rows = ("<tr><td colspan='5' class='empty'>"
                     "暂无商品数据，先新增商品</td></tr>")
    body = (f"<h1>{gicon} {sname}</h1><div class='sub'>玩家发「积分商城」看商品、发「购买 编号」兑换，管理员人工发货。保存立即生效</div>{msg}"
            "<div class='card'><h3>🛍 商品列表</h3>"
            "<table class='tbl'><tr><th>商品</th><th>价格(积分)</th><th>说明</th><th>状态</th><th>操作</th></tr>"
            + mall_rows + "</table>"
            "<form method='post' action='/mall_add' class='fx-g10-mt12'>"
            "<input type='text' name='name' placeholder='商品名称' required class='f2'>"
            "<input type='number' name='price' placeholder='价格(积分)' required min='1' class='f1'>"
            "<input type='text' name='desc' placeholder='说明(可选)' class='f2'>"
            "<button class='m0'>➕ 新增商品</button></form></div>"
            "<div class='card mt18' ><form method='post' action='/save'>"
            "<input type='hidden' name='group' value='points/mall'>"
            + field_rows("points/mall") + savebar("保存商城设置") + "</form></div>")
    return body
