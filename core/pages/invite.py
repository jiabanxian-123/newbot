"""网页后台 · 邀请系统（invite）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（C 批，2026-09-13）。
  原条件：`elif gkey == 'invite':`（邀请系统六子页：配置/记录/统计/汇总/前置条件/审核）

  该分支是**顶层分支**，外层 `sname` 定义在其后的 `elif sub:` 里 →
  调用时 `sname` 传 `None`。

⚠️ 搬运铁律（本项目硬规矩）：
  - 模块顶层只准 `from core import hub`，符号一律 `hub.X` 延迟绑定；
  - **禁止** `from bot import ...`；
  - 函数内可自由 `hub.` 访问所有全局。
"""
from core import hub
from core.pages._common import (  # noqa: F401
    esc,
    group_options, field_rows, savebar, flt_bar, paginate,
    _group_options, _members_body, _all_user_options,
    _field_rows, _savebar, _flt_bar,
)
import html  # noqa: F401
import time
from collections import defaultdict
from datetime import datetime



def page_invite(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """邀请系统六子页。"""
            # 邀请系统六子页：配置/记录/统计/汇总/前置条件/审核
    subs_inv = {k: n for k, n in hub.SUBPAGES.get("invite", [])}
    sub = sub or "config"
    sname = subs_inv.get(sub, sub)
    sel_icid = (flt or {}).get("cid", 0)
    def _inv_icid(v):   # int 化记录 cid（记录里存的是 int）
        try: return int(v)
        except (TypeError, ValueError): return 0
    def _inv_bar(action):
        return ("<form method='get' action='" + action + "' class='mb10'>"
                "<div class='lbl'>群组筛选</div><select name='cid' onchange='this.form.submit()'>"
                "<option value='0'>全部群</option>" + _group_options(selected=sel_icid)
                + "</select><noscript><button class='m0'>查看</button></noscript></form>")
    if sub == "config":
        warn_html = ("<div class='err'>⚠️ 邀请系统当前已关闭</div>" if not hub.sget("INVITE_ENABLED") else "")
        body = (f"<h1>{gicon} {gname}</h1>"
                f"<div class='sub'>群里发「<code>{esc(str(hub.INVITE_LINK_CMD))}</code>」领专属邀请链接 → 新朋友经链接进群 → 本群达标后邀请人得奖励"
                f"（群内发「{esc(str(hub.INVITE_RANK_ALL_CMD))}」看排行）</div>{msg}{warn_html}"
                "<div class='card'><h3>🎟️ 使用说明</h3>"
                "<div class='sub'>链接经 Telegram 官方 invite_link 事件追踪，进群先记账为「待达标」；"
                "被邀请人在本群发言/净赚积分达到「合格结算」页的质量要求后自动发奖（也可点群里的「刷新进度」立即重判）；"
                "每人最多发放次数见下方「每人最多发放奖励次数」；被邀请人退群后不计排行。</div></div>"
                "<div class='card mt18' ><form method='post' action='/save'>"
                "<input type='hidden' name='group' value='invite/config'>"
                + _field_rows("invite/config") +
                "<div class='sub mt16' >模板占位符：邀请成功通知 <code>{inviter}</code> <code>{invitee}</code> <code>{reward}</code>；"
                "链接消息 <code>{link}</code> <code>{reward}</code>；排行行 <code>{i}</code> <code>{name}</code> <code>{count}</code></div>" +
                _savebar("保存邀请设置") + "</form></div>")
    elif sub == "records":
        _recs = [(k, r) for k, r in hub.invite_records.items()
                 if not sel_icid or _inv_icid(r.get("cid")) == sel_icid]
        def _rec_badge(r):
            if hub._rec_rejected(r):
                return "<span class='c-bad'>拒绝</span>"
            if r.get("ad_flag"):
                return "<span class='c-bad'>连坐·发广告</span>"
            if hub._rec_qualified(r):
                return ("<span class='c-ok'>合格·已发放</span>" if hub._rec_awarded(r)
                        else "<span class='c-warn'>合格·超额未发</span>")
            return "<span class='c-dim'>待达标</span>"
        _rec_rows, _rec_foot = paginate(
            sorted(_recs, key=lambda kv: kv[1].get("ts", ""), reverse=True),
            flt, "/page/invite/records", qs=({"cid": sel_icid} if sel_icid else {}))
        rows_html = ""
        for k, r in _rec_rows:
            badge = _rec_badge(r)
            if r.get("left"):
                badge += " <span class='c-dim'>(已退群)</span>"
            if r.get("note") and hub._rec_rejected(r):
                badge += f" <span class='c-bad'>{esc(str(r.get('note', '')))}</span>"
            rows_html += (f"<tr><td><code>{k}</code></td>"
                          f"<td><code>{r.get('inviter', '')}</code></td>"
                          f"<td><code>{r.get('invitee', '')}</code> {esc(str(r.get('invitee_name', '')))}</td>"
                          f"<td>{r.get('ts', '')}</td><td>{badge}</td>"
                          f"<td>{r.get('award', 0)}</td>"
                          f"<td><a class='q' href='/invite_del/{k}' onclick=\"return confirm('删除该邀请记录？')\">🗑 删除</a></td></tr>")
        if not rows_html:
            rows_html = "<tr><td colspan='7' class='empty'>暂无邀请记录</td></tr>"
        body = (f"<h1>{gicon} 邀请记录</h1><div class='sub'>邀请记录（按时间倒序）；待达标=进群未达质量要求，达标后自动转合格；退群自动标失效</div>{msg}"
                "<div class='card'>" + _inv_bar("/page/invite/records") +
                "<form method='post' action='/invite_clear' class='mb10' "
                "onsubmit=\"return confirm('确认清空全部邀请记录与邀请链接？此操作不可恢复！')\">"
                "<button style='background:#8a3b3b;color:#fff'>🧹 清空全部邀请数据</button></form>"
                "<form method='post' action='/invite_clear_group' style='margin-bottom:10px;display:flex;gap:8px;align-items:center' "
                "onsubmit=\"return confirm('确认删除所选群的全部邀请记录？其他群不受影响，此操作不可恢复！')\">"
                f"<select name='cid' required><option value=''>选择要清记录的群</option>{_group_options(selected=sel_icid)}</select>"
                "<button style='background:#a3663b;color:#fff'>🗑 删除该群记录</button></form>"
                "<table class='tbl'><tr><th>记录ID</th><th>邀请人</th><th>被邀请人</th><th>时间</th><th>状态</th><th>奖励</th><th>操作</th></tr>"
                + rows_html + "</table>" + _rec_foot + "</div>")
    elif sub == "daily":
        daily_counts = defaultdict(int)
        for r in hub.invite_records.values():
            if hub._rec_ok(r) and (not sel_icid or _inv_icid(r.get("cid")) == sel_icid):
                daily_counts[str(r.get("ts", ""))[:10]] += 1
        _day_rows, _day_foot = paginate(sorted(daily_counts.items(), reverse=True),
                                        flt, "/page/invite/daily", qs=({"cid": sel_icid} if sel_icid else {}))
        rows_html = "".join(f"<tr><td>{d}</td><td>{n}</td></tr>" for d, n in _day_rows)
        if not rows_html:
            rows_html = "<tr><td colspan='2' class='empty'>暂无数据</td></tr>"
        body = (f"<h1>{gicon} 统计</h1><div class='sub'>每日合格邀请数（最近 60 天，按进群日计）</div>{msg}"
                "<div class='card'>" + _inv_bar("/page/invite/daily") +
                "<table class='tbl'><tr><th>日期</th><th>合格邀请</th></tr>"
                + rows_html + "</table>" + _day_foot + "</div>")
    elif sub == "summary":
        sums = defaultdict(lambda: {"ok": 0, "award": 0, "pend": 0})
        for r in hub.invite_records.values():
            if (not sel_icid or _inv_icid(r.get("cid")) == sel_icid):
                if hub._rec_rejected(r) or r.get("left"):
                    continue
                if hub._rec_valid(r):
                    sums[r.get("inviter")]["ok"] += 1
                    sums[r.get("inviter")]["award"] += int(r.get("award", 0) or 0)
                elif not hub._rec_qualified(r):
                    sums[r.get("inviter")]["pend"] += 1
                        # 合格但发过广告（连坐）→ 既不算合格也不算待达标，只保留已发奖励
        # ── I6C（2026-09-14）：把「还剩几次」直接算出来 ──────────────────
        # 邀请有**两套**上限：INVITE_REWARD_TIMES（每人**总**发奖次数）
        # 与 INVITE_DAILY_CAP_TIMES/POINTS（**每日**人数/积分）。
        # 想知道「今天还能拉几个」得同时看两套 —— 这里把结论摆出来。
        _cap_total = int(hub.sget("INVITE_REWARD_TIMES") or 0)
        _cap_day_t = int(hub.sget("INVITE_DAILY_CAP_TIMES") or 0)
        _cap_day_p = int(hub.sget("INVITE_DAILY_CAP_POINTS") or 0)
        _today_key = hub.now_bj().strftime("%Y-%m-%d")
        # ⚠️ 只读快照，别用 _invite_daily_get()（那个会往 defaultdict 里建空条目 ——
        #    渲染页面不该改数据）。
        _daily_today = (hub.invite_daily.get(_today_key, {}) or {}).get(sel_icid, {}) or {}

        def _cap_cell(used, cap, unit=""):
            if cap <= 0:
                return f"{used}{unit} <span class='c-dim'>/ 不限</span>"
            left = max(0, cap - used)
            color = "#f09595" if left == 0 else ("#f0c060" if left <= 1 else "#6fd08c")
            return (f"{used}{unit} / {cap}{unit} "
                    f"<span style='color:{color}'>剩 {left}{unit}</span>")

        def _quota_cells(uid):
            if not sel_icid:
                return ("<td><span class='c-dim'>选一个群</span></td>"
                        "<td><span class='c-dim'>选一个群</span></td>")
            _used_total = hub._inviter_awarded_count(sel_icid, uid)
            _d = _daily_today.get(uid) or {}
            _dt = int(_d.get("times", 0) or 0)
            _dp = int(_d.get("points", 0) or 0)
            _day_txt = (_cap_cell(_dt, _cap_day_t, " 次")
                        + "<br>" + _cap_cell(_dp, _cap_day_p, " 分"))
            return (f"<td>{_cap_cell(_used_total, _cap_total, ' 次')}</td><td>{_day_txt}</td>")

        _sum_rows, _sum_foot = paginate(sorted(sums.items(), key=lambda kv: -kv[1]["ok"]),
                                        flt, "/page/invite/summary", qs=({"cid": sel_icid} if sel_icid else {}))
        rows_html = ""
        for uid, s in _sum_rows:
            rows_html += (f"<tr><td><code>{uid}</code> {esc(hub.user_names.get(uid, ''))}</td>"
                          f"<td>{s['ok']}</td><td>{s['pend']}</td><td>{s['award']}</td>"
                          + _quota_cells(uid) + "</tr>")
        if not rows_html:
            rows_html = "<tr><td colspan='6' class='empty'>暂无数据</td></tr>"
        _quota_hint = ("" if sel_icid else
                       "　·　<b>选一个群</b>后可看每人的「总次数 / 今日」剩余额度（两套上限分别算）")
        body = (f"<h1>{gicon} 汇总</h1><div class='sub'>按邀请人汇总（未退群）：合格=已达标人数，"
                f"待达标=进群未达标，累计奖励{_quota_hint}</div>{msg}"
                "<div class='card'>" + _inv_bar("/page/invite/summary") +
                "<table class='tbl'><tr><th>邀请人</th><th>合格</th><th>待达标</th><th>累计奖励</th>"
                "<th>总次数</th><th>今日额度</th></tr>"
                + rows_html + "</table>" + _sum_foot + "</div>")
    elif sub == "qualify":
        body = (f"<h1>{gicon} 合格结算</h1>"
                f"<div class='sub'>被邀请人进群先记账，<b>在本群</b>达到下列质量要求才算「合格」并发放邀请奖励"
                f"（{hub.sget('INVITE_REWARD')} 积分/人，每人上限 {hub.sget('INVITE_REWARD_TIMES')} 次）；发言/积分达标事件驱动自动结算，超额只计合格不再发；"
                f"头像/用户名要求进群时检查，不满足直接拒绝永不发（防小号白嫖）。</div>{msg}"
                "<div class='card'><form method='post' action='/save'>"
                "<input type='hidden' name='group' value='invite/qualify'>"
                + _field_rows("invite/qualify") +
                _savebar("保存合格结算设置") + "</form></div>")
    else:
        body = f"<h1>{gicon} {gname}</h1><div class='sub'>该子页暂未开通</div>{msg}"
    return body
