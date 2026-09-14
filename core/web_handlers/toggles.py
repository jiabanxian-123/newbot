# -*- coding: utf-8 -*-
"""网页后台 · Web 层处理函数（toggles 域）

2026-09-13 从 core/web.py 的 _AdminHandler 里**原样搬出来**的方法（批 1）。

抽缝/搬迁铁律：
  - 只搬不改：方法体一个字符没动，只做了整体 dedent；
  - 新增 import 只补方法真正用到的（用 AST 收集 Name(Load) 后与原名表求交）；
  - 本模块**不 import core.web**（会循环导入）—— 所以这里只放不依赖 web.py
    内部函数（_home_page / _tpl_preview / _admin_page）的方法。
"""
from urllib.parse import urlparse, quote
from core import hub


class ToggleHandlers:
    """toggles 域的 HTTP 处理函数（以 Mixin 形式组合进 _AdminHandler）。"""

    def _get_list_del_toggle(self, r, mm):
        kind, act, idx = mm.group(1), mm.group(2), int(mm.group(3))
        lst = {"rule": hub.chat_rules, "pkg": hub.buy_packages,
               "level": hub.POINT_LEVELS, "mall": hub.MALL_ITEMS, "redeem": hub.redeem_goods}[kind]
        back = {"rule": "/page/points/rule", "pkg": "/page/points/buypkg",
                "level": "/page/points/level", "mall": "/page/points/mall",
                "redeem": "/page/points/redeem"}[kind]
        if 0 <= idx < len(lst):
            if act == "del":
                lst.pop(idx)
            else:
                # 2026-09-09：等级也有启停（用户截图「状态」列），不再一律当删除
                lst[idx]["on"] = 0 if int(lst[idx].get("on", 1) or 0) else 1
            hub.save_settings({})
        self._redirect(back); return


    def _get_group_clear(self, r, mm):
        _gc, _gk = int(mm.group(1)), mm.group(2)
        hub.group_set(_gc, _gk, None)
        hub.save_settings({})   # 立即落盘（含 group_settings）
        _u = urlparse(self.headers.get("Referer") or "")
        _back = (_u.path if _u.path.startswith("/") else "/page/dashboard")
        if _u.query:
            _back += "?" + _u.query
        self._redirect(_back + ("&" if "?" in _back else "?") + "note="
                       + quote("↺ 已恢复继承全局默认"))
        return


    def _get_racegrp_toggle(self, r, mm):
        rcid = int(mm.group(1))
        if rcid in hub.AUTHORIZED_GROUPS:
            hub.hourly_race_enabled[rcid] = not hub.hourly_race_enabled.get(rcid, True)
            hub.save_data()
        self._redirect("/page/schedule"); return


    def _get_sched_groups_toggle(self, r, mm):
        task, rid = mm.group(1), int(mm.group(2))
        target = hub.daily_reset_groups if task == "dailyreset" else hub.leaderboard_groups
        if rid in hub.AUTHORIZED_GROUPS:
            if rid in target: target.discard(rid)
            else: target.add(rid)
            hub.save_settings({})
        self._redirect("/page/schedule"); return


    def _get_sched_admins_toggle(self, r, mm):
        # 2026-09-14：经营日报整体下线 ⇒ 路由正则从
        #   /sch_(backup|report)_admins_toggle/(\d+)
        # 收窄成 /sch_backup_admins_toggle/(\d+)，**只剩一个捕获组**，
        # 所以不再有 `task`（原来靠 group(1) 区分 backup / report）。
        rid = int(mm.group(1))
        target = hub.backup_admins
        if rid in target: target.discard(rid)
        else: target.add(rid)
        hub.save_settings({})
        self._redirect("/page/schedule"); return


    def _get_menu_move(self, r, mm):
        g, d = mm.group(1), int(mm.group(2))
        keys_now = [gk for gk, _n, _i in hub.SETTINGS_GROUPS if gk != "dashboard"]
        order = [k for k in hub.SIDEBAR_ORDER if k in keys_now] + [k for k in keys_now if k not in hub.SIDEBAR_ORDER]
        if g in order:
            i = order.index(g)
            j = max(0, min(len(order) - 1, i + d))
            order[i], order[j] = order[j], order[i]
            hub.SIDEBAR_ORDER.clear(); hub.SIDEBAR_ORDER.extend(order)
            hub.SETTINGS_SNAPSHOT["sidebar_order"] = list(hub.SIDEBAR_ORDER)  # 供 _write_settings_file 带出
            hub.save_settings({})  # 走统一保存通道：套用+合并写盘+快照同步
        self._redirect("/page/dashboard"); return
