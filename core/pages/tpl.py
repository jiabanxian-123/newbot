# -*- coding: utf-8 -*-
"""页面模板加载器（2026-09-14，体检报告「HTML 抽成独立文件」这一步的机制）。

把后台页面里**大段的静态 HTML**（表单、表格头、按钮、script）搬进
`core/pages/templates/*.html`，Python 只负责把动态值填进去。

为什么用 `str.replace("{{键}}", 值)` 而不用 `str.format`：
  页面 HTML 里有大量 `{` `}`（内联 CSS / JS 的**花括号**），`str.format` 会把它们
  当成占位符直接抛错；`{{键}}` + 替换没有这个坑。

用法：
    from core.pages import tpl
    body = tpl.render("lottery_create", group_options=_group_options(), fee=0)
"""
from core import hub  # noqa: F401  （保持与其他页面模块一致的抽缝约定）

import os

_TPL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_CACHE = {}


def load(name):
    """读模板原文（读一次缓存）。"""
    s = _CACHE.get(name)
    if s is None:
        with open(os.path.join(_TPL_DIR, name + ".html"), encoding="utf-8") as f:
            s = f.read()
        _CACHE[name] = s
    return s


def render(name, **kw):
    """读模板并把 `{{键}}` 替换成对应值（缺省参数保持原样，便于排查）。"""
    s = load(name)
    for k, v in kw.items():
        s = s.replace("{{" + k + "}}", "" if v is None else str(v))
    return s
