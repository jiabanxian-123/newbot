# -*- coding: utf-8 -*-
"""网页后台 · 赛季（season）

**2026-09-14 新建**（清单 S2A + S4A）。此前赛季设置走 `web.py` 的**通用表单兜底**：
能改，但两件事看不见 ——
  · 「留 0 = 沿用日常」的那 6 个参数，**实际生效值是多少**（得跳到德州设置页去查）
  · 赛季有**哪几个定时任务**、什么周期、开关在哪

本页**只做展示**，不改任何玩法/保存逻辑：设置表单仍然走通用的 `field_rows("season")`
+ `/save`，与本页新增的说明卡片完全解耦。

⚠️ 搬运铁律：模块顶层只准 `from core import hub`，符号一律 `hub.X` 延迟绑定。
"""
from core import hub
from core.pages._common import _field_rows, _savebar, esc
import html


# 「留 0 = 沿用日常德州」的 6 个参数：
#   赛季设置键 → (显示名, 对应的**日常**常量名, 单位后缀)
_SEASON_FORK = (
    ("season_small_blind",       "德州小盲注",     "SMALL_BLIND",       ""),
    ("season_big_blind",         "德州大盲注",     "BIG_BLIND",         ""),
    ("season_ante",              "德州前注",       "ANTE",              ""),
    ("season_fixed_min_raise",   "最低加注额",     "FIXED_MIN_RAISE",   ""),
    ("season_turn_timeout",      "单回合思考时间", "TURN_TIMEOUT",      " 秒"),
    ("season_room_wait_timeout", "等待房倒计时",   "ROOM_WAIT_TIMEOUT", " 秒"),
)


def _int_or_0(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _season_status_html():
    """当前赛季状态一行（进行中 / 未开始），带结束时间。"""
    if not hub.season_active:
        return "<div class='sub'>当前<b>没有进行中的赛季</b>。群里发「赛季开赛」或点后台的按钮开赛。</div>"
    _end = _int_or_0(hub.season_end_ts)
    _end_txt = "—"
    if _end > 0:
        try:
            import datetime as _dt
            _end_txt = _dt.datetime.fromtimestamp(_end).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            _end_txt = str(_end)
    _name = esc(str(hub.season_name or "赛季比赛"))
    return ("<div class='sub'>进行中：第 <b>%s</b> 赛季「%s」· 结束时间 <b>%s</b>"
            "（到点由独立调度自动结算，精确到分钟）</div>"
            % (esc(str(hub.season_id)), _name, esc(_end_txt)))


def _fork_table_html():
    """S2A：6 个「留 0 = 沿用日常」参数的当前实际生效值。"""
    _k2v = hub._KEY2VAR
    _ns = hub.namespace()
    rows = []
    for _key, _label, _daily_var, _unit in _SEASON_FORK:
        _sv = _int_or_0(hub.sget(_k2v.get(_key)))
        _dv = _int_or_0(_ns.get(_daily_var))
        if _sv:
            _eff = "<b class='c-ok'>%d%s</b>　赛季专用" % (_sv, _unit)
        else:
            _eff = "<b class='c-warn'>%d%s</b>　沿用日常" % (_dv, _unit)
        rows.append("<tr><td>%s</td><td><code>%s</code></td><td>%s</td></tr>"
                    % (esc(_label), esc(_key), _eff))
    return ("<div class='card'><h3>🔁 留 0 = 沿用日常德州 —— 当前实际生效值</h3>"
            "<div class='sub'>下面这 6 项在赛季局里<b>各自独立</b>判定：填了非 0 就用赛季值，"
            "留 0 就该项沿用日常德州的设置。所以「留 0」不是「不生效」，而是「跟日常一样」。</div>"
            "<table class='tbl'><tr><th>参数</th><th>赛季设置键</th><th>赛季局实际取值</th></tr>"
            + "".join(rows) + "</table></div>")


def _task_table_html():
    """S4A：赛季的三个调度 —— 周期、开关、挂在哪。"""
    _reset_on = bool(hub.sget("DAILY_RESET_ENABLED"))
    _reset_at = esc(str(hub.sget("DAILY_RESET_TIME") or "00:00"))
    _on = "<span class='c-ok'>开</span>"
    _off = "<span class='c-bad'>关</span>"
    rows = [
        ("每日赛季分归位", "每日 <b>%s</b>" % _reset_at,
         _on if _reset_on else _off,
         "把赛季分重置回「每人起始分」。挂在<b>「定时任务」的每日重置</b>里"
         "（开关也在那页）。进行中的赛季局会跳过本次重置，等它结算时补上。"),
        ("赛季到点结算", "每 <b>60 秒</b>检查一次",
         _on if hub.season_active else "待赛季进行中",
         "到「结束时间」即自动结算：推最终榜 → 加冕赌神 → 清赛季数据。"
         "独立调度，不依赖每日 0 点循环（避免最多延迟 24 小时）。"),
        ("赛季分兑换", "命令触发：<code>赛季兑换</code> / <code>兑换赛季</code>",
         "—",
         "聊天积分按比例换成赛季分，有每日上限。比例与上限在<b>「赛季」设置</b>里。"),
    ]
    body = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % r for r in rows)
    return ("<div class='card mt18' ><h3>⏰ 赛季任务表</h3>"
            "<div class='sub'>赛季一共 <b>3</b> 个自动/半自动流程，各自挂在不同地方 —— "
            "想知道「什么时候会发生什么」看这张表就够了。</div>"
            "<table class='tbl'><tr><th>任务</th><th>周期 / 触发</th><th>当前状态</th><th>说明</th></tr>"
            + body + "</table></div>")


def page_season(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """赛季页：状态 + 「留 0 实际是多少」+ 任务表 + 赛季设置表单。"""
    body = (f"<h1>{gicon} {gname}</h1>"
            "<div class='sub'>赛季德州独立参数 · 每日归位 · 到点结算 · 聊天积分换赛季分</div>"
            f"{msg}{_season_status_html()}"
            + _fork_table_html()
            + _task_table_html()
            + "<div class='card mt18' ><h3>⚙️ 赛季设置</h3>"
              "<form method='post' action='/save'>"
              "<input type='hidden' name='group' value='season'>"
            + _field_rows("season")
            + _savebar("保存赛季设置") + "</form></div>")
    return body
