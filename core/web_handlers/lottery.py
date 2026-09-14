# -*- coding: utf-8 -*-
"""网页后台 · Web 层处理函数（lottery 域）

2026-09-13 从 core/web.py 的 _AdminHandler 里**原样搬出来**的方法（批 1）。

抽缝/搬迁铁律：
  - 只搬不改：方法体一个字符没动，只做了整体 dedent；
  - 新增 import 只补方法真正用到的（用 AST 收集 Name(Load) 后与原名表求交）；
  - 本模块**不 import core.web**（会循环导入）—— 所以这里只放不依赖 web.py
    内部函数（_home_page / _tpl_preview / _admin_page）的方法。
"""
import asyncio
import html
import time
from urllib.parse import urlparse, quote
from core import hub


class LotteryHandlers:
    """lottery 域的 HTTP 处理函数（以 Mixin 形式组合进 _AdminHandler）。"""

    def _get_lottery_cancel(self, r):
        """网页取消抽奖：退参与费、标记取消、群里通知。"""
        qs = r.qs
        def _lc_back(note="", err=""):
            q = ("?note=" + quote(note)) if note else ("?err=" + quote(err) if err else "")
            self._redirect("/page/lottery" + q)
        try: cid = int(qs.get("cid", ["0"])[0] or 0)
        except ValueError: cid = 0
        lo = hub._lottery_active(cid)
        if not lo:
            _lc_back(err="该群没有进行中的抽奖"); return
        fee = int(lo.get("fee", 0))
        if fee > 0:
            for u, _ts, _n in lo["participants"]:
                hub.game_chips[cid][u] = hub.game_chips[cid].get(u, 0) + fee
        lo["status"] = "cancelled"
        hub.save_data()
        if hub._bot_app and hub._bot_loop:
            async def _notify():
                tail = f"，已退还 {fee} 积分/人" if fee > 0 else ""
                await hub.safe_send(hub._bot_app.bot, cid,
                    f"🛑 抽奖活动「<b>{html.escape(str(lo['title']))}</b>」已被管理员取消{tail}",
                    parse_mode="HTML")
            try: asyncio.run_coroutine_threadsafe(_notify(), hub._bot_loop).result(8)
            except Exception: pass
        _lc_back(note=f"✅ 已取消「{lo['title'][:20]}」" + (f"，退还 {fee} 分/人" if fee > 0 else ""))
        return


    def _post_lottery_create(self, r):
        """网页创建抽奖（阿福格式）：解析 → 落库 + bot 发公告到群。"""
        form = r.form
        def _lc_back(note="", err=""):
            q = ("?note=" + quote(note)) if note else ("?err=" + quote(err) if err else "")
            self._redirect("/page/lottery" + q)
        try: cid = int(form.get("cid", ["0"])[0] or 0)
        except ValueError: cid = 0
        if not cid or cid not in (set(hub.AUTHORIZED_GROUPS) | set(hub.game_chips.keys())):
            _lc_back(err="请选择有效的群"); return
        if hub._lottery_active(cid):
            _lc_back(err="该群已有进行中的抽奖，请先取消或等开奖"); return
        fields, ferr = hub._lottery_form_parse(form)
        if ferr:
            _lc_back(err=ferr); return
        hub.lotteries[cid] = {
            "title": fields["title"], "desc": fields["desc"], "prizes": fields["prizes"],
            "fee": int(fields.get("fee") or hub.sget("LOTTERY_FEE") or 0), "keyword": fields["keyword"],
            "min_bal": fields["min_bal"], "start_ts": time.time(),
            "end_ts": fields["end_ts"], "msg_id": None,
            "participants": [], "status": "open", "winners": [],
            "creator": 0, "chat_id": cid,
        }
        hub.save_data()
        # 跨线程让 bot 把公告发到群（网页线程 → bot 主事件循环）
        pub_err = ""
        if hub._bot_app and hub._bot_loop:
            async def _pub():
                await hub._lottery_publish(hub._bot_app, cid, hub.lotteries[cid])
            try:
                asyncio.run_coroutine_threadsafe(_pub(), hub._bot_loop).result(10)
            except Exception as exc:
                hub.logger.exception("网页创建抽奖：公告发送失败")
                pub_err = f"（公告发送失败：{type(exc).__name__}，活动已创建，可在群内发 /开奖 触发参与）"
        _lc_back(note=f"✅ 抽奖「{fields['title'][:20]}」已创建并发到群 {cid}{pub_err}")
        return
