# -*- coding: utf-8 -*-
"""命令管理页（原 `_admin_page` 的 `elif gkey == "commands"` 分支，32 行）。

2026-09-13 从 core/web.py 原样搬出，字符级一致（由页面快照比对守卫保证）。
"""
import html

from core import hub
from core.pages._common import savebar, esc


def page_commands(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    rows = []
    for fn_name in sorted(hub._HANDLERS_BY_NAME):
        cur = hub.CMD_ALIAS_OVERRIDES.get(fn_name)
        if cur is None:
            cur = ",".join(sorted(a for a, f in hub.BASE_CMD_ALIASES.items() if f.__name__ == fn_name))
        rows.append("<tr><td class='nowrap'><code>" + fn_name + "</code></td>"
                    # `size='1'`：不写 size 时浏览器按默认 20 字符算输入框的**固有宽度**，
                    # 表格布局会拿它当所在列的宽度下限 —— 2 列表被顶宽、右列输入框被裁。
                    # 视觉宽度由 `.w100{width:100%}` 决定，跟 size 无关，写 1 不会变窄。
                    "<td><input type='text' size='1' name='" + esc(fn_name) +
                    "' value=\"" + esc(cur) + "\" class='w100'></td></tr>")
    menu_txt = "\n".join(f"{c_} {d_}" for c_, d_ in hub.TG_MENU)
    _cf = hub.cmd_conflicts()
    _cf_html = ""
    if _cf:
        _li = "".join(f"<tr><td><code>{esc(a)}</code></td>"
                      f"<td>{esc('、'.join(fns))}</td></tr>" for a, fns in _cf)
        _cf_html = ("<div class='err' style='margin-top:14px'>⚠️ 有 %d 个触发词被多个命令占用，"
                    "排在后面的会顶掉前面的（表现为某命令在群里没反应）：</div>"
                    "<table class='tbl'><tr><th style='width:180px'>触发词</th><th>被这些命令占用</th></tr>"
                    % len(_cf) + _li + "</table>")
    else:
        _cf_html = ("<div class='ok' style='margin-top:14px'>✅ 触发词体检通过：没有重复占用</div>")
    body = (f"<h1>{gicon} 命令管理</h1>"
            "<div class='sub'>每个命令的触发词随意改（逗号分隔，可中文可英文）；保存后<b>立即生效</b>并持久化。"
            "这里是触发词的<b>唯一入口</b>，帮助文案里显示的指令名会自动跟着改。"
            f"Telegram / 菜单每行一条「命令 描述」，命令仅限英文小写/数字/下划线</div>{msg}{err}"
            "<div class='card'><form method='post' action='/cmdaliases'>"
            "<h3>⌨️ 命令触发词</h3>"
            "<table class='tbl'><tr><th style='width:150px'>命令</th><th>触发词（逗号分隔）</th></tr>"
            + "".join(rows) + "</table>" + _cf_html
            + "<h3 style='margin-top:20px'>📱 Telegram / 菜单</h3>"
            "<textarea name='tg_menu' rows='14' style='width:100%;font-family:inherit'>" + esc(menu_txt) + "</textarea>" +
            savebar("保存全部命令设置") + "</form></div>")
    return body
