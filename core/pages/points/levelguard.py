"""网页后台 · 积分 · levelguard（points/levelguard）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（C 批，2026-09-13）。
  原条件：`if gkey == "points" and sub == "levelguard":`

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



def page_points_levelguard(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """积分 · levelguard（points/levelguard）"""
    subs = {k: n for k, n in hub.SUBPAGES.get(gkey, [])}
    sname = subs.get(sub, sub)
    body = (f"<h1>{gicon} {sname}</h1>"
            f"<div class='sub'>按等级限制群成员可发送的消息类型：越权消息立即撤回并提示，"
            f"窗口内连续违规按下方规则惩罚。等级权限在「积分等级」页逐级勾选</div>{msg}"
            "<div class='card'><form method='post' action='/save'>"
            "<input type='hidden' name='group' value='points/levelguard'>"
            + _field_rows("points/levelguard")
            + "<div class='sub mt16' >占位符：<code>{name}</code> <code>{level}</code> "
              "<code>{kind}</code>（消息类型）<code>{seconds}</code>（禁言秒数）</div>"
            + _savebar("保存管控设置") + "</form></div>"
            "<div class='card mt18' ><h3>📋 当前等级权限一览</h3>"
            "<div class='sub'>改权限请去「积分等级」页点编辑</div>"
            + "<table class='tbl'><tr><th>等级</th><th>最低积分</th><th>允许发送</th></tr>"
            + "".join(
                f"<tr><td><b>L{i + 1}</b> {esc(str(x.get('name', '?')))}</td>"
                f"<td>{int(x.get('value', 0) or 0)}</td><td>{_perm_summary(x.get('perms'))}</td></tr>"
                for i, x in enumerate(hub.POINT_LEVELS))
            + "</table></div>")
    return body
