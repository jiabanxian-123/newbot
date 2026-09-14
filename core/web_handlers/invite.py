# -*- coding: utf-8 -*-
"""网页后台 · Web 层处理函数（invite 域）

2026-09-13 从 core/web.py 的 _AdminHandler 里**原样搬出来**的方法（批 1）。

抽缝/搬迁铁律：
  - 只搬不改：方法体一个字符没动，只做了整体 dedent；
  - 新增 import 只补方法真正用到的（用 AST 收集 Name(Load) 后与原名表求交）；
  - 本模块**不 import core.web**（会循环导入）—— 所以这里只放不依赖 web.py
    内部函数（_home_page / _tpl_preview / _admin_page）的方法。
"""
from urllib.parse import urlparse, quote
from core import hub


class InviteHandlers:
    """invite 域的 HTTP 处理函数（以 Mixin 形式组合进 _AdminHandler）。"""

    def _post_invite_clear(self, r):
        hub.invite_records.clear(); hub.invite_links.clear()
        hub.save_data()
        self._redirect("/page/invite/records?note=" + quote("🧹 已清空全部邀请数据")); return


    def _post_invite_clear_group(self, r):
        form = r.form
        try: cid_ = int(form.get("cid", ["0"])[0] or 0)
        except ValueError: cid_ = 0
        if cid_:
            n = sum(1 for k in list(hub.invite_records) if k.startswith(f"{cid_}:"))
            for k in [k for k in list(hub.invite_records) if k.startswith(f"{cid_}:")]:
                hub.invite_records.pop(k, None)
            hub.save_data()
            self._redirect("/page/invite/records?cid=" + str(cid_) + "&note=" + quote(f"🗑 已删除该群 {n} 条邀请记录"))
        else:
            self._redirect("/page/invite/records?note=" + quote("请先选择要删除的群"))
        return


    def _get_invite_del(self, r, mm):
        key = f"{mm.group(1)}:{mm.group(2)}"
        hub.invite_records.pop(key, None)
        hub.save_data()
        self._redirect("/page/invite/records?note=" + quote("🗑 已删除记录 " + key)); return


    def _get_legacy_titg_redirect(self, r):
        _lq = urlparse(self.path).query
        self._redirect("/page/members/titg" + (("?" + _lq) if _lq else "")); return
