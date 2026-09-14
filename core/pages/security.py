# -*- coding: utf-8 -*-
"""安全页（原 `_admin_page` 的 `elif gkey == "security"` 分支，11 行）。

2026-09-13 从 core/web.py 原样搬出，字符级一致（由页面快照比对守卫保证）。
"""
from core.pages._common import field_rows


def page_security(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    # 原来这里只渲染密码输入框，从不渲染 _field_rows("security")
    # ⇒ 分到 security 组的开关（登录二次验证）永远是「后台看不到」的黑洞。
    body = (f"<h1>{gicon} {gname}</h1><div class='sub'>修改后台登录密码 · 登录二次验证</div>{msg}"
            "<div class='card'><form method='post' action='/save'>"
            "<input type='hidden' name='group' value='security'>"
            + field_rows("security") +
            "<label>新密码（至少 4 位；留空 = 不修改）"
            "<input type='password' name='new_password'></label>"
            "<button type='submit'>💾 保存</button></form></div>")
    return body
