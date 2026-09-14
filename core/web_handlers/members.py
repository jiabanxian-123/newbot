# -*- coding: utf-8 -*-
"""网页后台 · Web 层处理函数（members 域）

2026-09-13 从 core/web.py 的 _AdminHandler 里**原样搬出来**的方法（批 1）。

抽缝/搬迁铁律：
  - 只搬不改：方法体一个字符没动，只做了整体 dedent；
  - 新增 import 只补方法真正用到的（用 AST 收集 Name(Load) 后与原名表求交）；
  - 本模块**不 import core.web**（会循环导入）—— 所以这里只放不依赖 web.py
    内部函数（_home_page / _tpl_preview / _admin_page）的方法。
"""
import asyncio
from urllib.parse import urlparse, quote
from core import hub


class MemberHandlers:
    """members 域的 HTTP 处理函数（以 Mixin 形式组合进 _AdminHandler）。"""

    def _post_memops(self, r):
        form = r.form
        op = form.get("op", [""])[0]
        cid_ = 0   # ★ 先给默认值：上面 int() 一抛 ValueError，except 里就要引用 cid_
        try:
            cid_ = int(form.get("cid", ["0"])[0] or 0)
            uid_ = int(form.get("uid", ["0"])[0] or 0)
            amt = int(form.get("amount", ["1"])[0] or 1)
        except ValueError:
            self._redirect(f"/page/members/mlist?cid={cid_}&err=" + quote("参数必须是数字")); return
        def _mb(note="", err=""):
            q = ("note=" + quote(note)) if note else ("err=" + quote(err) if err else "")
            self._redirect(f"/page/members/mlist?cid={cid_}&per=20" + ("&" + q if q else ""))
        if op in ("warn_add", "warn_sub") and cid_ and uid_:
            delta = amt if op == "warn_add" else -amt
            cur = hub.warn_counts[cid_][uid_]
            hub.warn_counts[cid_][uid_] = max(0, cur + delta)
            if not hub.warn_counts[cid_][uid_]: hub.warn_counts[cid_].pop(uid_, None)
            hub.save_data()
            _mb(note=f"✅ 用户 {uid_} 警告 {delta:+d}，当前 {hub.warn_counts[cid_].get(uid_, 0)}")
        elif op == "warn_clear_all" and cid_:
            n = len(hub.warn_counts.get(cid_, {}))
            hub.warn_counts.pop(cid_, None)
            hub.save_data()
            _mb(note=f"🧹 已清除群 {cid_} 全部警告（{n} 人）")
        elif op == "chips_clear_all" and cid_:
            n = len(hub.game_chips.get(cid_, {}))
            hub.game_chips.pop(cid_, None)
            hub.save_data()
            _mb(note=f"🧹 已清空群 {cid_} 全部成员积分（{n} 人）")
        elif op == "tag_sync_all" and cid_:
            # 🏷 补齐成员标签：长任务丢后台跑，跑完私聊回报，避免网页请求挂住
            if not (hub._bot_app and hub._bot_loop):
                _mb(err="bot 尚未启动完成，请稍后再试"); return
            if not hub.sget("LEVEL_SYNC_TAG"):
                _mb(err="后台「积分称号同步成员标签」开关是关闭状态，请先开启"); return
            asyncio.run_coroutine_threadsafe(
                hub.sync_member_tags_notify(hub._bot_app, cid_), hub._bot_loop)   # 非阻塞
            hub.admin_logs.append({"ts": hub.now_bj().strftime("%Y-%m-%d %H:%M"), "cid": cid_,
                               "admin": "网页后台", "action": "同步成员标签",
                               "target": str(cid_)})
            hub.save_data()
            _mb(note=f"🏷 已在后台开始同步群 {cid_} 的成员标签（最多 {hub.sget('TAG_SYNC_MAX')} 人），完成后会私聊通知管理员")
        elif op == "wl_add" and cid_ and uid_:
            hub.whitelist.setdefault(cid_, set()).add(uid_); hub.save_data()
            hub.admin_logs.append({"ts": hub.now_bj().strftime("%Y-%m-%d %H:%M"), "cid": cid_,
                               "admin": "网页后台", "action": "加白", "target": str(uid_)})
            _mb(note=f"✅ 已将 {uid_} 加入白名单")
        elif op == "wl_del" and cid_ and uid_:
            hub.whitelist.get(cid_, set()).discard(uid_); hub.save_data()
            _mb(note=f"✅ 已将 {uid_} 移出白名单")
        elif op in ("ban", "kick") and cid_ and uid_:
            if not (hub._bot_app and hub._bot_loop):
                _mb(err="bot 尚未启动完成，请稍后再试"); return
            async def _bk():
                await hub._bot_app.bot.ban_chat_member(cid_, uid_)
                if op == "kick":  # 踢出=先封再解封，人已离群且可重新加入
                    await hub._bot_app.bot.unban_chat_member(cid_, uid_, only_if_banned=True)
            try:
                asyncio.run_coroutine_threadsafe(_bk(), hub._bot_loop).result(15)
                hub.admin_logs.append({"ts": hub.now_bj().strftime("%Y-%m-%d %H:%M"), "cid": cid_,
                                   "admin": "网页后台", "action": "封禁" if op == "ban" else "踢出",
                                   "target": str(uid_)})
                hub.save_data()
                _mb(note=("⛔ 已封禁 " if op == "ban" else "👋 已踢出 ") + str(uid_))
            except Exception as e:
                _mb(err=f"操作失败：{e}（bot 需为群管理员且有封禁权限）")
        else:
            _mb(err="参数错误")
        return
