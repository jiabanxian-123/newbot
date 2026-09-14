"""网页后台 · 积分 · redeem（points/redeem）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（C 批，2026-09-13）。
  原条件：`if gkey == "points" and sub == "redeem":`

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



def page_points_redeem(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """积分 · redeem（points/redeem）"""
    subs = {k: n for k, n in hub.SUBPAGES.get(gkey, [])}
    sname = subs.get(sub, sub)
    rd_rows = ""
    for i, x in enumerate(hub.redeem_goods):
        on = bool(x.get("on", True))
        st = "<span class='c-ok'>上架</span>" if on else "<span class='c-dim'>下架</span>"
        left = int(x.get("left", 0) or 0)
        tg = x.get("target_groups") or []
        if not tg:
            tg_txt = "<span class='c-ok'>全部授权群</span>"
        else:
            tg_txt = "<br>".join(f"<code>{c}</code> {esc(hub.chat_name_cache.get(c, '') or str(c))}" for c in tg)
        rd_rows += (f"<tr><td>{esc(str(x.get('name', '?')))}<div style='font-size:11px;color:#8a89a0;margin-top:2px'>作用群：{tg_txt}</div></td>"
                    f"<td>{int(x.get('price', 0) or 0)}</td>"
                    f"<td>{'不限' if left <= 0 else left}</td>"
                    f"<td>{int(x.get('redeemed', 0) or 0)}</td><td>{st}</td>"
                    f"<td><a href='/redeem_toggle/{i}'>{'下架' if on else '上架'}</a> · "
                    f"<a href='/redeem_del/{i}' class='c-bad'>删除</a></td></tr>")
    if not rd_rows:
        rd_rows = ("<tr><td colspan='6' class='empty'>"
                   "暂无兑换商品，先新增商品</td></tr>")
    ro_page, ro_foot = paginate(list(reversed(hub.redeem_orders)), flt, "/page/points/redeem")
    ro_rows = ""
    for o in ro_page:
        _oc = int(o.get("cid", 0) or 0)
        _oc_txt = f"{esc(hub.chat_name_cache.get(_oc) or str(_oc or '—'))}"
        ro_rows += (f"<tr><td>{esc(str(o.get('no', '')))}</td>"
                    f"<td>{esc(str(o.get('ts', '')))}</td>"
                    f"<td>{_oc_txt}</td>"
                    f"<td>{esc(str(o.get('uid', '')))}</td>"
                    f"<td>{esc(str(o.get('item', '')))}</td>"
                    f"<td>{int(o.get('price', 0) or 0)}</td></tr>")
    if not ro_rows:
        ro_rows = ("<tr><td colspan='6' class='empty'>暂无兑换订单</td></tr>")
    cmd_esc = esc(str(hub.REDEEM_CMD))
            # 作用群多选（空勾 = 全部授权群）
    tg_checks = ""
    for cid, cname in sorted(((c, hub.chat_name_cache.get(c, str(c))) for c in hub.AUTHORIZED_GROUPS), key=lambda kv: kv[1]):
        tg_checks += (f"<label style='display:inline-flex;align-items:center;gap:4px;margin-right:12px;color:#cfcfe8'>"
                      f"<input type='checkbox' name='cids' value='{cid}' checked> {esc(cname)} <code style='font-size:11px;color:#8a89a0'>{cid}</code></label>")
    body = (f"<h1>{gicon} {sname}</h1><div class='sub'>群内发「{cmd_esc}」看商品列表，发「{cmd_esc} 编号」立即兑换；剩余 0=不限，限量商品兑完自动下架</div>{msg}"
            "<div class='card'><h3>🛒 兑换商品</h3>"
            "<table class='tbl'><tr><th>商品</th><th>所需积分</th><th>剩余</th><th>已兑换</th><th>状态</th><th>操作</th></tr>"
            + rd_rows + "</table>"
            "<form method='post' action='/redeem_add' style='margin-top:12px'>"
            "<div style='display:flex;gap:10px'>"
            "<input type='text' name='name' placeholder='商品名称' required class='f2'>"
            "<input type='number' name='price' placeholder='所需积分' required min='1' class='f1'>"
            "<input type='number' name='left' placeholder='剩余数量(0=不限)' min='0' class='f1'>"
            "<input type='text' name='desc' placeholder='说明(可选)' class='f2'>"
            "<button class='m0'>➕ 新增商品</button></div>"
            "<div style='margin-top:8px;padding:8px;background:#1a1a2e;border-radius:6px'>"
            "<div style='color:#8a89a0;font-size:12px;margin-bottom:6px'>📍 作用群（不勾 = 全部授权群；只勾部分 = 只在勾选群触发列表）</div>"
            + tg_checks +
            "</div></form></div>"
            "<div class='card mt18' ><h3>🧾 最近兑换订单（防伪核对）</h3>"
            "<table class='tbl'><tr><th>单号</th><th>时间</th><th>群</th><th>用户ID</th><th>商品</th><th>积分</th></tr>"
            + ro_rows + "</table>" + ro_foot + "</div>"
            "<div class='card mt18' ><form method='post' action='/save'>"
            "<input type='hidden' name='group' value='points/redeem'>"
            + _field_rows("points/redeem")
            + "<div class='sub mt16' >商品行占位符：<code>{goodsName}</code> <code>{pointNum}</code> <code>{leftNum}</code>；"
              "成功通知占位符：<code>{name}</code> <code>{goodsName}</code> <code>{pointNum}</code> <code>{balance}</code>；"
              "防伪单号由系统自动生成，只发用户私聊和管理员对账（群里不显示，防群友看到别人单号冒领），无需模板配置</div>" +
            _savebar("保存兑换设置") + "</form></div>")
    return body
