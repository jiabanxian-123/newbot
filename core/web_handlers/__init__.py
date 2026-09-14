# -*- coding: utf-8 -*-
"""网页后台 · Web 层处理函数包（2026-09-13 从 core/web.py 的 _AdminHandler 拆出）。

一个功能域一个文件，每个文件一个 Mixin 类；`core/web.py` 里的
`_AdminHandler` 通过多继承把它们组合起来。

⚠️ 本包**不许 import core.web**（循环导入）。依赖 web.py 内部函数的方法
   留在 core/web.py 的 _AdminHandler 里，不搬过来。
"""
from core.web_handlers.toggles import ToggleHandlers  # noqa: E402,F401
from core.web_handlers.points import PointsHandlers  # noqa: E402,F401
from core.web_handlers.members import MemberHandlers  # noqa: E402,F401
from core.web_handlers.invite import InviteHandlers  # noqa: E402,F401
from core.web_handlers.lottery import LotteryHandlers  # noqa: E402,F401
from core.web_handlers.admin import AdminHandlers  # noqa: E402,F401
from core.web_handlers.misc import MiscHandlers  # noqa: E402,F401

__all__ = [
    "ToggleHandlers",
    "PointsHandlers",
    "MemberHandlers",
    "InviteHandlers",
    "LotteryHandlers",
    "AdminHandlers",
    "MiscHandlers",
]
