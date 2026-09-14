"""网页后台 · 管理员（admins / fundflow / 子页兜底）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（B 批，2026-09-13）。
  原条件：`elif gkey == 'admin':`

  ★ B 批唯一带**自递归**的页面：子页兜底时原代码
  `return _admin_page(gkey, sub=first, saved=saved, bad=bad, note=note, err=err)`，
  搬出后改为 `return None, (gkey, dict(sub=first, ...))`，由 `_admin_page` 解包后重跑。
  `saved` / `bad` / `note` 是自递归要透传的状态，故做成关键字参数（默认即首次调用时的值）。

⚠️ 搬运铁律（本项目硬规矩）：
  - 模块顶层只准 `from core import hub`，符号一律 `hub.X` 延迟绑定；
  - **禁止** `from bot import ...`；
  - 函数内可自由 `hub.` 访问所有全局（常量、函数、状态）。

返回值：通常 `body`（str）。若返回 `(body, redirect)` 二元组，表示 `_admin_page`
需要按 `redirect = (gkey, kwargs)` 重跑一次（自递归用）。
"""
from core import hub
from core.pages._common import (  # noqa: F401
    esc,
    group_options, field_rows, savebar, flt_bar, paginate,
    _group_options, _all_user_options, _field_rows, _savebar,
)
import html  # noqa: F401
from collections import defaultdict


def page_admin(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname, uid=0, saved=0, bad=0, note="", **kw):
    """管理员（admins / fundflow / 子页兜底）"""
    def _btn(action, key, val, label, color="#7c6cf0"):
        return (f"<form class='i' method='post' action='/adminops2'>"
                f"<input type='hidden' name='op' value='{action}'>"
                f"<input type='hidden' name='{key}' value='{val}'>"
                f"<button style='margin:0;padding:4px 12px;font-size:12px;background:{color};margin-top:0'>{label}</button></form>")
    if sub == "admins":
        admin_rows = ""
        for a in sorted(hub.BOT_ADMINS):
            if a in hub.ADMIN_USER_IDS:
                src = "种子管理员(代码写入,不可移除)"
            else:
                src = (f"<form class='i' method='post' action='/adminops'>"
                       f"<input type='hidden' name='action' value='del'>"
                       f"<input type='hidden' name='uid' value='{a}'>"
                       f"<button style='margin:0;padding:4px 12px;font-size:12px;background:#e06666;margin-top:0'>移除</button></form>")
            admin_rows += f"<tr><td><code>{a}</code></td><td>{src}</td></tr>"
        body = ("<h1>🛡️ Bot 管理员</h1>"
                "<div class='sub'>种子管理员来自代码/环境变量，防锁死不可移除；新增的重启不丢。</div>"
                f"{msg}<div class='card'>"
                f"<table class='tbl'><tr><th>ID</th><th>操作</th></tr>{admin_rows}</table>"
                "<form method='post' action='/adminops' class='fx-g10-mt14'>"
                "<input type='hidden' name='action' value='add'>"
                "<input type='number' name='uid' list='dl_users_admin' placeholder='用户数字ID' required class='f1'>"
                f"<datalist id='dl_users_admin'>{_all_user_options()}</datalist>"
                "<button type='submit' class='mt0'>➕ 添加管理员</button></form></div>")
    elif sub == "auth":
        rows = "".join(f"<tr><td><code>{g}</code> {esc(hub.chat_name_cache.get(g) or '（未知群/已解散）')}</td>"
                       f"<td>{_btn('authdel', 'cid', g, '移除(含数据)', '#e06666')}</td></tr>"
                       for g in sorted(hub.AUTHORIZED_GROUPS))
        body = (f"<h1>{gicon} 授权群管理</h1>"
                f"<div class='sub'>授权群里的玩家才能使用游戏；也可在群里发 /授权</div>{msg}"
                "<div class='card'><table class='tbl'><tr><th>群</th><th>操作</th></tr>"
                + (rows or "<tr><td colspan='2'>暂无授权群</td></tr>") + "</table>"
                "<form method='post' action='/adminops2' class='fx-g10-mt14'>"
                "<input type='hidden' name='op' value='authadd'>"
                "<input type='number' name='cid' list='dl_groups_auth' placeholder='群 ID（-100 开头）' required class='f1'>"
                f"<datalist id='dl_groups_auth'>{_group_options()}</datalist>"
                "<button type='submit' class='mt0'>➕ 添加授权</button></form></div>")
    elif sub == "blacklist":
        _bl_rows, _bl_foot = paginate(sorted(hub.BLACKLISTED_USERS), flt, "/page/admin/blacklist")
        rows = "".join(f"<tr><td><code>{u}</code></td><td>{esc(hub.user_names.get(u, ''))}</td>"
                       f"<td>{_btn('unblack', 'uid', u, '解黑')}</td></tr>"
                       for u in _bl_rows)
        body = (f"<h1>{gicon} 拉黑管理</h1>"
                f"<div class='sub'>被拉黑的玩家无法使用机器人任何功能；也可群里 /拉黑 /解黑</div>{msg}"
                "<div class='card'><table class='tbl'><tr><th>ID</th><th>名字</th><th>操作</th></tr>"
                + (rows or "<tr><td colspan='3'>黑名单为空</td></tr>") + "</table>" + _bl_foot
                + "<form method='post' action='/adminops2' class='fx-g10-mt14'>"
                "<input type='hidden' name='op' value='black'>"
                "<input type='number' name='uid' list='dl_users_black' placeholder='用户 ID' required class='f1'>"
                f"<datalist id='dl_users_black'>{_all_user_options()}</datalist>"
                "<button type='submit' style='margin-top:0;background:#e06666'>🔨 拉黑</button></form></div>")
    elif sub == "god":
        god = next((u for u, ts in hub.user_titles.items() if hub.TITLE_GAMBLING_GOD in ts), None)
        cur = (f"<code>{god}</code> {esc(hub.user_names.get(god, ''))}" if god else "暂无（全局唯一，封新撤旧）")
        body = (f"<h1>{gicon} 赌神称号</h1><div class='sub'>全局唯一：封新人自动撤销上任</div>{msg}"
                "<div class='card'><table class='tbl'><tr><th>现任赌神</th></tr>"
                f"<tr><td>{cur}　{_btn('godrevoke', 'uid', god or 0, '撤销', '#e06666') if god else ''}</td></tr></table>"
                "<form method='post' action='/adminops2' class='fx-g10-mt14'>"
                "<input type='hidden' name='op' value='godgrant'>"
                "<input type='number' name='uid' list='dl_users_god' placeholder='用户 ID' required class='f1'>"
                f"<datalist id='dl_users_god'>{_all_user_options()}</datalist>"
                "<button type='submit' class='mt0'>👑 封赌神</button></form></div>")
    elif sub == "seasonpts":
                # 2026-09-12 用户报「网页端积分加减分无效根本用不了」。
                # 赛季分调整页是**同一个病**：旧表单用 `number + datalist`，
                # number+datalist 在移动端弹不出成员下拉，管理员记不住 10 位数字 ID
                # ⇒ 实际根本选不了人。这里同样改成「① 选群载入 → ② 原生下拉选成员」，
                # 并留「手填用户」兜底（数字 ID / @用户名 / 昵称），与积分加减分页一致。
        _sg = sel_flt_cid
        _c1 = ("<div class='card'><h3>① 选择群组</h3>"
               "<div class='sub'>先选群并载入，下面才会列出该群成员（手机端同样可用）</div>"
               "<form method='get' action='/page/admin/seasonpts' "
               "class='fx-g10-ae-wrap'>"
               "<div style='flex:1;min-width:220px'><div class='sub'>群组</div>"
               "<select name='cid' required><option value=''>— 请选择群 —</option>"
               + _group_options(selected=_sg) + "</select></div>"
               "<button type='submit' class='mt0'>🔍 载入成员</button></form></div>")
        _sp_hdr = (f"<h1>{gicon} 赛季分调整</h1>"
                   f"<div class='sub'>给玩家加/减赛季分（正加负减）；赛季未开始时需玩家已在赛季名单</div>{msg}{err}")
        if _sg:
            _sp_members = (set(hub.game_chips.get(_sg, {})) | set(hub.member_profiles.get(_sg, {}))
                           | set(hub.season_points.get(_sg, {})))
            _sp_opts = "".join(
                f"<option value='{u}'>{esc(hub.user_names.get(u, str(u)))}（{u}）</option>"
                for u in sorted(_sp_members,
                                key=lambda u: -int(hub.game_chips.get(_sg, {}).get(u, 0) or 0)))
            _sp_sel = (f"<select name='uid' class='f1-200'>"
                       f"<option value=''>（可选）从该群成员里挑一个</option>{_sp_opts}</select>"
                       if _sp_opts else
                       "<div class='sub f1' >该群还没有成员记录，请用下面的「手填用户」</div>")
            body = (_sp_hdr + _c1 +
                    "<div class='card mt18' ><h3>② 执行调整</h3>"
                    "<div class='sub'>目标群："
                    f"<b>{esc(hub.chat_name_cache.get(_sg) or '未命名群')}</b> <code>{_sg}</code>"
                    f" · 已知成员 {len(_sp_members)} 人</div>"
                    "<form method='post' action='/adminops2'>"
                    "<input type='hidden' name='op' value='seasonpts'>"
                    f"<input type='hidden' name='cid' value='{_sg}'>"
                    "<div class='row'><div class='lbl'>成员<small>原生下拉，手机也能正常选</small></div>"
                    f"{_sp_sel}</div>"
                    "<div class='row'><div class='lbl'>手填用户<small>数字 ID / @用户名 / 昵称任填其一，填了就优先用它</small></div>"
                    "<input type='text' name='uid_raw' placeholder='如 123456789 或 @name 或 昵称' "
                    "class='f1-200'></div>"
                    "<div class='row'><div class='lbl'>赛季分变动<small>正数=加，负数=减</small></div>"
                    "<input type='number' name='amount' value='100' required></div>"
                    "<button type='submit'>💾 执行调整</button></form></div>")
        else:
            body = (_sp_hdr + _c1 +
                    "<div class='card mt18' ><h3>② 执行调整</h3>"
                    "<div class='sub'>请先在 ① 里选好群组并点「🔍 载入成员」</div></div>")
    elif sub == "fundflow":
        sel = uid or 0
        recv_map, send_map = defaultdict(int), defaultdict(int)
        for e in list(hub.ledger) + list(hub.game_flows):
            if sel and e.get("to") == sel: recv_map[e.get("frm")] += e.get("amt", 0)
            if sel and e.get("frm") == sel: send_map[e.get("to")] += e.get("amt", 0)
        def _ff_rows(m, empty_txt):
            if not m: return f"<tr><td colspan='3'>{empty_txt}</td></tr>"
            out = []
            for peer, total in sorted(m.items(), key=lambda x: -x[1])[:10]:
                red = " style='color:#ff7b7b;font-weight:700'" if total >= hub.FUND_FLOW_ALERT else ""
                out.append(f"<tr{red}><td><code>{peer}</code> {esc(hub.user_names.get(peer, ''))}</td>"
                           f"<td>{total}</td><td>{'🚨 超阈值，重点核查' if total >= hub.FUND_FLOW_ALERT else ''}</td></tr>")
            return "".join(out)
        _flows = sorted([x for x in list(hub.ledger) + list(hub.game_flows) if sel in (x.get("frm"), x.get("to"))],
                        key=lambda x: str(x.get("ts", "")))
        _flow_rows, _flow_foot = paginate(list(reversed(_flows)), flt, "/page/admin/fundflow",
                                          qs=({"uid": sel} if sel else {}))
        detail = "".join(
            f"<tr><td>{esc(str(e.get('ts', '')))}</td>"
            f"<td><code>{e.get('frm')}</code> → <code>{e.get('to')}</code></td>"
            f"<td>{esc(str(e.get('typ', '')))}</td><td>{e.get('amt', 0)}</td></tr>"
            for e in _flow_rows)
        sel_txt = f"<code>{sel}</code> {esc(hub.user_names.get(sel, ''))}" if sel else ""
        body = (f"<h1>{gicon} 资金流审查</h1>"
                f"<div class='sub'>红包/转赠/游戏送分（德州·金花故意输牌）等人对人转移全部记账；兑换周边前先查一眼，小号一查一个准。累计 ≥ {hub.FUND_FLOW_ALERT} 标红</div>{msg}"
                "<div class='card'><form method='get' action='/page/admin/fundflow'>"
                "<div class='row'><div class='lbl'>选择要审查的用户</div>"
                f"<select name='uid' required>{_all_user_options(sel)}</select></div>"
                "<button type='submit' class='mt10'>🔍 审查</button></form></div>"
                + (f"<div class='card'><h3>给 {sel_txt} 送钱的 TOP（收到）</h3>"
                   f"<table class='tbl'><tr><th>来源</th><th>累计</th><th>警示</th></tr>{_ff_rows(recv_map, '该用户没有收钱记录')}</table>"
                   f"<h3 class='mt16'>{sel_txt} 送钱的 TOP（转出）</h3>"
                   f"<table class='tbl'><tr><th>去向</th><th>累计</th><th>警示</th></tr>{_ff_rows(send_map, '该用户没有转出记录')}</table>"
                   f"<h3 class='mt16'>资金流明细</h3>"
                   "<table class='tbl'><tr><th>时间</th><th>流向</th><th>类型</th><th>金额</th></tr>"
                   + (detail or "<tr><td colspan='4'>暂无明细</td></tr>") + "</table>" + _flow_foot + "</div>" if sel else "")
                + "<div class='card'><form method='post' action='/save'>"
                  "<input type='hidden' name='group' value='admin/fundflow'>"
                  + _field_rows("admin/fundflow") +
                  _savebar("保存阈值") + "</form></div>")
    else:
        first = hub.SUBPAGES["admin"][0][0]
        if first == sub:
                    # 防自递归：兜底要跳的子页就是当前子页 → 渲染占位页，绝不能自己跳自己
            body = f"<h1>{gicon} {gname}</h1><div class='sub'>该子页暂未开通</div>{msg}"
        else:
            return None, (gkey, {"sub": first, "saved": saved, "bad": bad, "note": note, "err": err})
    return body
