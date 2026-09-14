# -*- coding: utf-8 -*-
"""网页后台 · Web 层处理函数（misc 域）

2026-09-13 从 core/web.py 的 _AdminHandler 里**原样搬出来**的方法（批 1）。

抽缝/搬迁铁律：
  - 只搬不改：方法体一个字符没动，只做了整体 dedent；
  - 新增 import 只补方法真正用到的（用 AST 收集 Name(Load) 后与原名表求交）；
  - 本模块**不 import core.web**（会循环导入）—— 所以这里只放不依赖 web.py
    内部函数（_home_page / _tpl_preview / _admin_page）的方法。
"""
from core import hub


class MiscHandlers:
    """misc 域的 HTTP 处理函数（以 Mixin 形式组合进 _AdminHandler）。"""

    def _get_health(self, r):
        self._send(200, f"ok {hub.BOT_VERSION} | data={hub._data_file_status()}".encode(),
                   [("Content-Type", "text/plain")]); return


    def _get_theme(self, r, mm):
        if mm.group(1) not in hub._UI_THEMES:
            return False
        qs = r.qs
        hub.SETTINGS_SNAPSHOT["ui_theme"] = mm.group(1)
        hub.save_data()
        # ★ 开放重定向防护：back 原来直接当跳转目标，
        #   /theme/purple?back=https://evil.com 会把已登录的管理员送去外部站点。
        #   只放行「站内绝对路径」：必须以 / 开头，且不能以 // 开头（协议相对 URL）。
        _back = qs.get("back", ["/"])[0] or "/"
        if not _back.startswith("/") or _back.startswith("//"):
            _back = "/"
        self._redirect(_back); return
