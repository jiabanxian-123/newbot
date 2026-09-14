# -*- coding: utf-8 -*-
"""网页后台 · Web 层处理函数（points 域）

2026-09-13 从 core/web.py 的 _AdminHandler 里**原样搬出来**的方法（批 1）。

抽缝/搬迁铁律：
  - 只搬不改：方法体一个字符没动，只做了整体 dedent；
  - 新增 import 只补方法真正用到的（用 AST 收集 Name(Load) 后与原名表求交）；
  - 本模块**不 import core.web**（会循环导入）—— 所以这里只放不依赖 web.py
    内部函数（_home_page / _tpl_preview / _admin_page）的方法。
"""
import asyncio
import csv
import io
from urllib.parse import urlparse, quote
from core import hub


class PointsHandlers:
    """points 域的 HTTP 处理函数（以 Mixin 形式组合进 _AdminHandler）。"""

    def _get_points_template(self, r):
        self._send(200, "用户ID,昵称,积分\n123456789,示例玩家,1000\n".encode("utf-8-sig"),
                   [("Content-Type", "text/csv; charset=utf-8"),
                    ("Content-Disposition", "attachment; filename=points_template.csv")]); return


    def _get_points_export(self, r):
        qs = r.qs
        try: cid = int(qs.get("cid", ["0"])[0])
        except ValueError: cid = 0
        if not cid: self._send(400, b"bad cid", [("Content-Type", "text/plain")]); return
        # ★ 必须走 csv 模块加引号：原来用 f-string 裸拼，昵称里带逗号时
        #   （如「张三,999」）导出再导入会把 999 当成积分写进去 → 静默写错分。
        #   另外以 = + - @ 开头的昵称在 Excel 里会被当公式执行（CSV 注入），
        #   统一在前面加一个单引号。
        _buf = io.StringIO()
        _w = csv.writer(_buf)
        _w.writerow(["用户ID", "昵称", "积分"])
        for uid, value in sorted(hub.game_chips.get(cid, {}).items()):
            _nm = str(hub.user_names.get(uid, "") or "")
            if _nm[:1] in ("=", "+", "-", "@"):
                _nm = "'" + _nm
            _w.writerow([uid, _nm, value])
        self._send(200, _buf.getvalue().encode("utf-8-sig"),
                   [("Content-Type", "text/csv; charset=utf-8"),
                    ("Content-Disposition", f"attachment; filename=points_{cid}.csv")]); return


    def _post_points_adj(self, r):
        form = r.form
        def _back(note="", err="", cid=0):
            q = ("note=" + quote(note)) if note else ("err=" + quote(err) if err else "")
            if cid:
                q = (f"cid={cid}&" + q) if q else f"cid={cid}"
            self._redirect("/page/points/adjust" + ("?" + q if q else ""))
        try:
            cid = int(form.get("cid", [""])[0])
        except ValueError:
            _back(err="参数错误：群 ID 必须是数字"); return
        # 手填优先（填了说明用户就是要指定这个人），否则用下拉选中的成员
        _raw = (form.get("uid_raw", [""])[0] or "").strip() or (form.get("uid", [""])[0] or "").strip()
        uid, _uerr = hub._resolve_web_uid(cid, _raw)
        if _uerr:
            _back(err=_uerr, cid=cid); return
        try:
            amount = int(form.get("amount", [""])[0])
            if amount == 0: raise ValueError
        except ValueError:
            _back(err="参数错误：金额必须是数字且不能为 0", cid=cid); return
        _who = (hub.user_names.get(uid) or str(uid))
        if hub.player_is_busy(cid, uid):
            _back(err=f"该玩家正在游戏中，请等牌局结束再调整积分（{_who}）", cid=cid); return
        # ★ 所有「碰钱」的操作必须**投回 asyncio 主循环**、在钱包锁里执行。
        #   这里跑在 ThreadingHTTPServer 的线程里，而 game_chips / ledger 归主循环所有；
        #   `+=` 是「读出来 → 加一下 → 写回去」三步，与主循环的结算 / 转赠 / 发红包撞上
        #   就会**丢更新**（账目对不上）。下面 `_check_level_drop_on_spend` 就是
        #   用 run_coroutine_threadsafe 投回去的，加减分本身此前漏了这一层。
        async def _apply():
            async with hub.wallet_locks[uid]:
                if amount < 0 and hub.game_chips[cid][uid] < -amount:
                    return ("short", 0)
                _before = int(hub.game_chips[cid][uid] or 0)
                hub.game_chips[cid][uid] += amount
                # 记一笔「最近调整」：管理员一眼能看到刚才那次确实生效了（用户报「无效」时的痛点）
                hub.WEB_POINT_ADJ_LOG.append({
                    "ts": hub.now_bj().strftime("%m-%d %H:%M:%S"), "cid": cid,
                    "gname": hub.chat_name_cache.get(cid) or str(cid), "uid": uid,
                    "name": _who, "amount": amount, "after": hub.game_chips[cid][uid]})
                del hub.WEB_POINT_ADJ_LOG[:-20]
                if amount > 0:
                    hub._earn_add(cid, uid, amount)   # 网页加分同样计入累计积分
                # 台账：与群内 /add 同口径（网页改的分也要能在玩家「流水」里查到）
                if amount > 0:
                    hub.ledger_add(cid, 0, uid, amount, "管理员加分")
                else:
                    hub.ledger_add(cid, uid, 0, -amount, "管理员扣分")
                hub.force_save_now()
                return (None, _before)
        try:
            if hub._bot_loop:
                _e, _before_bal = asyncio.run_coroutine_threadsafe(_apply(), hub._bot_loop).result(8)
            else:
                _e, _before_bal = asyncio.run(_apply())
        except Exception:
            hub.logger.exception("网页调整积分失败")
            _back(err="调整积分失败，请重试", cid=cid); return
        if _e == "short":
            _back(err=f"扣分失败：{_who} 当前积分 {hub.game_chips[cid][uid]} 不足 {-amount}", cid=cid); return
        if amount < 0 and hub._bot_app and hub._bot_loop:
            # 开启「允许降级」时扣分可能掉级 → 发降级通知（开关内自判，零开销）
            try:
                asyncio.run_coroutine_threadsafe(
                    hub._check_level_drop_on_spend(hub._bot_app, cid, uid, _before_bal), hub._bot_loop).result(8)
            except Exception:
                hub.logger.exception("网页扣分降级通知失败（已吞并）")
        _back(note=f"✅ 已{'给' if amount > 0 else '扣除'} {_who}（{uid}）{abs(amount)} 积分，"
                   f"当前余额 {hub.game_chips[cid][uid]}（群 {cid}）", cid=cid)
        return


    def _post_rule_add(self, r):
        form = r.form
        try:
            pts = int(form.get("points", ["0"])[0] or 0)
        except ValueError:
            pts = 0
        match = (form.get("match", [""])[0] or "").strip()[:100]
        hub.chat_rules.append({"match": match, "points": pts, "on": True})
        hub.save_settings({})
        self._redirect("/page/points/rule"); return


    def _post_pkg_add(self, r):
        form = r.form
        def _ipkg(k, dflt):
            try: return int(form.get(k, [str(dflt)])[0] or dflt)
            except ValueError: return dflt
        name = (form.get("name", [""])[0] or "").strip()[:30]
        if name:
            hub.buy_packages.append({"name": name, "cny": max(0, _ipkg("cny", 0)),
                                 "points": max(1, _ipkg("points", 1)),
                                 "sort": _ipkg("sort", 0), "on": True})
            hub.save_settings({})
        self._redirect("/page/points/buypkg"); return


    def _post_level_add(self, r):
        form = r.form
        path = r.path
        def _ilv(k, dflt=0):
            try: return int(form.get(k, [str(dflt)])[0] or dflt)
            except ValueError: return dflt
        def _lv_perms():
            raw = form.getlist("perms") if hasattr(form, "getlist") else form.get("perms", [])
            picked = {str(p).strip() for p in raw if str(p).strip()}
            # 只留合法键，按定义顺序排列
            return ",".join(k for k, _v in hub.LEVEL_PERM_OPTIONS if k in picked)
        def _lv_on():
            v = form.get("on", [""])
            return 1 if str(v[0] if v else "").strip() in ("1", "on", "true") else 0
        name = (form.get("name", [""])[0] or "").strip()[:12]
        val = max(0, _ilv("value"))
        if path == "/level_edit":
            try: idx = int(form.get("i", ["-1"])[0])
            except ValueError: idx = -1
            if 0 <= idx < len(hub.POINT_LEVELS) and name and not any(ch in name for ch in "<>&"):
                it = hub.POINT_LEVELS[idx]
                it["name"], it["value"] = name, val
                it["perms"], it["on"] = _lv_perms(), _lv_on()
                hub.POINT_LEVELS.sort(key=lambda x: int(x.get("value", 0) or 0))
                hub.save_settings({})
            self._redirect("/page/points/level?note=" + quote("✅ 等级已更新")); return
        if name and not any(ch in name for ch in "<>&"):
            hub.POINT_LEVELS.append({"name": name, "value": val,
                                 "perms": _lv_perms(), "on": _lv_on()})
            hub.POINT_LEVELS.sort(key=lambda x: int(x.get("value", 0) or 0))
            hub.save_settings({})
        self._redirect("/page/points/level"); return


    def _post_mall_add(self, r):
        form = r.form
        def _iml(k, dflt=0):
            try: return int(form.get(k, [str(dflt)])[0] or dflt)
            except ValueError: return dflt
        name = (form.get("name", [""])[0] or "").strip()[:30]
        if name:
            hub.MALL_ITEMS.append({"name": name, "price": max(1, _iml("price", 1)),
                               "desc": (form.get("desc", [""])[0] or "").strip()[:60], "on": True})
            hub.save_settings({})
        self._redirect("/page/points/mall"); return


    def _post_redeem_add(self, r):
        form = r.form
        def _ird(k, dflt=0):
            try: return int(form.get(k, [str(dflt)])[0] or dflt)
            except ValueError: return dflt
        name = (form.get("name", [""])[0] or "").strip()[:30]
        if name:
            # 作用群：空列表 = 所有授权群（默认全群上架）；勾选则只发到这些群
            cids_raw = form.getlist("cids") if hasattr(form, "getlist") else form.get("cids", [])
            target_groups = []
            for c in cids_raw:
                try:
                    x = int(c)
                    if x in hub.AUTHORIZED_GROUPS: target_groups.append(x)
                except (ValueError, TypeError): pass
            hub.redeem_goods.append({"name": name, "price": max(1, _ird("price", 1)),
                                 "left": max(0, _ird("left", 0)),
                                 "redeemed": 0,
                                 "target_groups": target_groups,
                                 "desc": (form.get("desc", [""])[0] or "").strip()[:60], "on": True})
            hub.save_settings({})
        self._redirect("/page/points/redeem"); return
