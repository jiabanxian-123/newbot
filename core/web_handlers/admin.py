# -*- coding: utf-8 -*-
"""网页后台 · Web 层处理函数（admin 域）

2026-09-13 从 core/web.py 的 _AdminHandler 里**原样搬出来**的方法（批 1）。

抽缝/搬迁铁律：
  - 只搬不改：方法体一个字符没动，只做了整体 dedent；
  - 新增 import 只补方法真正用到的（用 AST 收集 Name(Load) 后与原名表求交）；
  - 本模块**不 import core.web**（会循环导入）—— 所以这里只放不依赖 web.py
    内部函数（_home_page / _tpl_preview / _admin_page）的方法。
"""
import asyncio
import json
import re
from urllib.parse import urlparse, quote
from telegram import BotCommand
from core import hub


class AdminHandlers:
    """admin 域的 HTTP 处理函数（以 Mixin 形式组合进 _AdminHandler）。"""

    def _post_adminops(self, r):
        form = r.form
        action = form.get("action", [""])[0]
        try:
            uid = int(form.get("uid", [""])[0])
        except ValueError:
            self._redirect("/page/general"); return
        if action == "add" and uid > 0 and uid not in hub.BOT_ADMINS:
            hub.BOT_ADMINS.add(uid); hub.save_data()
        elif action == "del" and uid not in hub.ADMIN_USER_IDS:
            hub.BOT_ADMINS.discard(uid); hub.save_data()
        self._redirect("/page/general"); return


    def _post_adminops2(self, r):
        form = r.form
        op = form.get("op", [""])[0]
        sub_map = {"authadd": "auth", "authdel": "auth", "black": "blacklist",
                   "unblack": "blacklist", "godgrant": "god", "godrevoke": "god", "seasonpts": "seasonpts",
                   "titlegrant": "titg", "titlerevoke": "titg"}
        def _back(note="", err="", extra=""):
            sub = sub_map.get(op, "auth")
            # 称号加封挂在「群组设置→群组管理」下，回跳别再把用户扔到管理员中心
            _base = "/page/members/titg" if sub == "titg" else f"/page/admin/{sub}"
            q = ("note=" + quote(note)) if note else ("err=" + quote(err) if err else "")
            if extra:   # 加封/撤销后回到同一个群+同一个成员，少点几下
                q = (extra + "&" + q) if q else extra
            self._redirect(_base + ("?" + q if q else ""))
        try:
            cid_ = int(form["cid"][0]) if form.get("cid") else None
            uid_ = int(form["uid"][0]) if form.get("uid") and form["uid"][0].strip() else None
            amt = int(form["amount"][0]) if form.get("amount") else None
        except ValueError:
            _back(err="参数必须是数字"); return
        # 2026-09-12：赛季分调整页的「手填用户」兜底（数字 ID / @用户名 / 昵称）。
        # 原生下拉没选中、但手填了 uid_raw 时，先把它解析成数字 ID 再走原逻辑；
        # 解析失败直接回可读错误（不静默改错人）。
        if op == "seasonpts" and cid_ and not uid_:
            _uid_raw = (form.get("uid_raw", [""])[0] or "").strip()
            if _uid_raw:
                _ru, _rerr = hub._resolve_web_uid(cid_, _uid_raw)
                if _rerr:
                    _back(err=_rerr); return
                uid_ = _ru
        if op == "authadd" and cid_:
            self._adm_authadd(form, cid_, uid_, amt, op, _back); return
        elif op == "authdel" and cid_:
            self._adm_authdel(form, cid_, uid_, amt, op, _back); return
        elif op == "black" and uid_:
            self._adm_black(form, cid_, uid_, amt, op, _back); return
        elif op == "unblack" and uid_:
            self._adm_unblack(form, cid_, uid_, amt, op, _back); return
        elif op == "godgrant" and uid_:
            self._adm_godgrant(form, cid_, uid_, amt, op, _back); return
        elif op == "godrevoke" and uid_:
            self._adm_godrevoke(form, cid_, uid_, amt, op, _back); return
        elif op in ("titlegrant", "titlerevoke") and uid_ is not None:
            self._adm_title(form, cid_, uid_, amt, op, _back); return
        elif op == "seasonpts" and cid_ and uid_ and amt is not None:
            self._adm_seasonpts(form, cid_, uid_, amt, op, _back); return
        elif op in ("join_approve", "join_decline") and cid_ and uid_:
            self._adm_joinreq(form, cid_, uid_, amt, op, _back); return
        else:
            _back(err="参数错误"); return
        return


    def _adm_authadd(self, form, cid_, uid_, amt, op, _back):
        hub.AUTHORIZED_GROUPS.add(cid_); hub.save_data()
        try:   # 顺手拉群名进缓存，授权列表不再显示裸 ID
            if hub._bot_app and hub._bot_loop:
                _ch = asyncio.run_coroutine_threadsafe(
                    hub._bot_app.bot.get_chat(cid_), hub._bot_loop).result(8)
                if getattr(_ch, "title", None):
                    hub.chat_name_cache[cid_] = _ch.title
        except Exception:
            pass
        _back(note=f"✅ 已授权群 {cid_}")
    def _adm_authdel(self, form, cid_, uid_, amt, op, _back):
        # 移除群 = 取消授权 + 清掉该群所有残留数据（邀请记录/链接、各群开关、进出群缓存），
        # 群解散后不再在下拉里留裸数字
        hub.AUTHORIZED_GROUPS.discard(cid_)
        n_inv = sum(1 for k in list(hub.invite_records) if k.startswith(f"{cid_}:"))
        for k in [k for k in list(hub.invite_records) if k.startswith(f"{cid_}:")]:
            hub.invite_records.pop(k, None)
        hub.invite_links.pop(cid_, None)
        hub.invite_pending.pop(f"{cid_}:", None)
        for k in [k for k in list(hub.invite_pending) if k.startswith(f"{cid_}:")]:
            hub.invite_pending.pop(k, None)
        hub.hourly_race_enabled.pop(cid_, None)
        hub.join_requests.pop(cid_, None); hub.leave_records.pop(cid_, None)
        hub.member_joined_at.pop(cid_, None)
        hub.chat_name_cache.pop(cid_, None)
        hub.game_chips.pop(cid_, None)   # 群已解散积分数据无用；下拉(授权∪有积分群)不再出现裸数字
        hub.save_data()
        _back(note=f"✅ 已移除群 {cid_}（含 {n_inv} 条邀请记录及全部群数据）")
    def _adm_black(self, form, cid_, uid_, amt, op, _back):
        hub.BLACKLISTED_USERS.add(uid_); hub.save_data(); _back(note=f"🔨 已拉黑 {uid_}")
    def _adm_unblack(self, form, cid_, uid_, amt, op, _back):
        hub.BLACKLISTED_USERS.discard(uid_); hub.save_data(); _back(note=f"✅ 已解黑 {uid_}")
    def _adm_godgrant(self, form, cid_, uid_, amt, op, _back):
        for _u in list(hub.user_titles.keys()):
            hub.user_titles[_u].discard(hub.TITLE_GAMBLING_GOD)
            if not hub.user_titles[_u]: del hub.user_titles[_u]
        hub.user_titles.setdefault(uid_, set()).add(hub.TITLE_GAMBLING_GOD)
        hub.save_data(); _back(note=f"👑 已将 {uid_} 封为赌神（覆盖上任）")
    def _adm_godrevoke(self, form, cid_, uid_, amt, op, _back):
        # ★ user_titles 是普通 dict（bot.py），用户本来没称号时 [uid_] 会 KeyError，
        #   异常冒泡到 handler 就是 500。旁边的「授予」用了 setdefault（会建键），
        #   撤销这处漏了 —— 这里用 .get 兜住。
        hub.user_titles.get(uid_, set()).discard(hub.TITLE_GAMBLING_GOD)
        if hub.title_equipped.get(uid_) == hub.TITLE_GAMBLING_GOD: hub.title_equipped.pop(uid_, None)
        if uid_ in hub.user_titles and not hub.user_titles[uid_]: del hub.user_titles[uid_]
        hub.save_data(); _back(note=f"🔻 已撤销 {uid_} 的赌神称号")
    def _adm_title(self, form, cid_, uid_, amt, op, _back):
        t_ = form.get("title", [""])[0]
        _fx = "&".join(x for x in (f"cid={cid_}" if cid_ else "",
                                   f"uid={uid_}" if uid_ else "") if x)
        _okk, _tmsg = (hub.grant_title(uid_, t_) if op == "titlegrant"
                       else hub.revoke_title(uid_, t_))
        if not _okk:
            _back(err=_tmsg, extra=_fx); return
        hub.save_data()
        hub.admin_logs.append({"ts": hub.now_bj().strftime("%Y-%m-%d %H:%M"), "cid": cid_ or 0,
                           "admin": "网页后台",
                           "action": "加封称号" if op == "titlegrant" else "撤销称号",
                           "target": f"{uid_}:{t_}"})
        _back(note=_tmsg, extra=_fx)
    def _adm_seasonpts(self, form, cid_, uid_, amt, op, _back):
        if not hub.season_active and uid_ not in hub.season_points.get(cid_, {}):
            _back(err="该玩家不在当前赛季，且赛季未激活"); return
        if amt < 0 and hub.season_points.get(cid_, {}).get(uid_, 0) < -amt:
            _back(err="该玩家赛季分不足"); return
        hub.season_points[cid_][uid_] = hub.season_points.get(cid_, {}).get(uid_, 0) + amt
        hub.save_data(); _back(note=f"✅ 用户 {uid_} 赛季分 {amt:+d}，当前 {hub.season_points[cid_][uid_]}")
    def _adm_joinreq(self, form, cid_, uid_, amt, op, _back):
        if not (hub._bot_app and hub._bot_loop):
            _back(err="bot 尚未启动完成，请稍后再试"); return
        async def _jr():
            if op == "join_approve":
                await hub._bot_app.bot.approve_chat_join_request(cid_, uid_)
            else:
                await hub._bot_app.bot.decline_chat_join_request(cid_, uid_)
        try:
            asyncio.run_coroutine_threadsafe(_jr(), hub._bot_loop).result(15)
            hub.join_requests[cid_] = [r for r in hub.join_requests.get(cid_, []) if r.get("uid") != uid_]
            hub.save_data()
            self._redirect("/page/members?" + ("note=" if op == "join_approve" else "err=")
                           + quote(("✅ 已批准入群 " if op == "join_approve" else "🚫 已拒绝入群 ") + str(uid_)))
        except Exception as e:
            self._redirect("/page/members?err=" + quote(f"操作失败：{e}（申请可能已被处理）"))



    def _post_cmdaliases(self, r):
        form = r.form
        def _cb_back(note="", err=""):
            q = ("?note=" + quote(note)) if note else ("?err=" + quote(err) if err else "")
            self._redirect("/page/commands" + q)
        new_over = {}
        for key, vals in form.items():
            if key in hub._HANDLERS_BY_NAME:  # 字段名=处理函数名（如 cmd_ph）
                new_over[key] = vals[0].replace("，", ",").strip()
        hub.CMD_ALIAS_OVERRIDES.clear()
        hub.CMD_ALIAS_OVERRIDES.update(new_over)
        hub.apply_command_aliases()
        menu_rows = []
        for line in form.get("tg_menu", [""])[0].splitlines():
            line = line.strip()
            if not line: continue
            parts_ = line.split(None, 1)
            if len(parts_) != 2 or not re.fullmatch(r"[a-z0-9_]{1,32}", parts_[0]):
                _cb_back(err=f"菜单行格式错误：「{line[:30]}」— 命令仅限英文小写/数字/下划线，后跟一个空格和描述"); return
            menu_rows.append([parts_[0], parts_[1]])
        if not menu_rows:
            _cb_back(err="/ 菜单不能为空"); return
        hub.TG_MENU.clear(); hub.TG_MENU.extend(menu_rows)
        with hub._settings_lock:
            try:
                with open(hub.SETTINGS_FILE, "r", encoding="utf-8") as f:
                    raw2 = json.load(f)
            except Exception:
                raw2 = {}
            hub._write_settings_file(raw2.get("fields", {}), hub._web_password,
                                 dict(hub.CMD_ALIAS_OVERRIDES), [list(t) for t in hub.TG_MENU])
        if hub._bot_app and hub._bot_loop:  # 热更新 Telegram 菜单
            async def _set_menu():
                await hub._bot_app.bot.set_my_commands([BotCommand(cc, dd) for cc, dd in hub.TG_MENU])
            try: asyncio.run_coroutine_threadsafe(_set_menu(), hub._bot_loop).result(10)
            except Exception: pass
        _cb_back(note=f"✅ 已保存并生效：{len(new_over)} 个命令触发词 + {len(hub.TG_MENU)} 项 / 菜单")
        return
