"""网页后台 · 积分 · rule（points/rule）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（C 批，2026-09-13）。
  原条件：`if gkey == "points" and sub == "rule":`

  ⚠️ 本页依赖总线的公共前缀 `subs` / `sname`，已原样抄进函数开头。

⚠️ 搬运铁律（本项目硬规矩）：
  - 模块顶层只准 `from core import hub`，符号一律 `hub.X` 延迟绑定；
  - **禁止** `from bot import ...`；
  - 函数内可自由 `hub.` 访问所有全局。
"""
from core import hub
from core.pages._common import (  # noqa: F401
    group_options, field_rows, savebar, flt_bar,
    _group_options, _members_body, _all_user_options,
    _field_rows, _savebar, _flt_bar,
)
import html  # noqa: F401
import time
from datetime import datetime



def page_points_rule(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """积分 · rule（points/rule）"""
    subs = {k: n for k, n in hub.SUBPAGES.get(gkey, [])}
    sname = subs.get(sub, sub)
    cap = f"每日上限 {hub.sget('CHAT_DAILY_CAP')} 分" if hub.sget("CHAT_DAILY_CAP") else "不设上限"
    fee = f"（手续费 {hub.sget('INHERIT_FEE_PERCENT')}%）" if hub.sget("INHERIT_FEE_PERCENT") else "（免手续费）"
    body = (f"<h1>💰 {sname}</h1><div class='sub'>当前生效规则（改设置自动更新）</div>{msg}"
            "<div class='card'><table class='tbl'>"
            f"<tr><th>获取</th><td>聊天：每满 {hub.sget('CHAT_CHARS_PER')} 字符记 {hub.sget('CHAT_REWARD')} 分，{cap}；"
            f"签到：基础 {hub.sget('SIGN_BASE_REWARD')} 分，连续满 7 天额外 +{hub.sget('SIGN_STREAK_BONUS')} 分；抢积分红包"
            + (f"；充值：{hub.sget('BUY_MIN')}~{hub.sget('BUY_MAX')}/次（管理员确认到账）" if hub.sget("BUY_ENABLED") else "") + "</td></tr>"
            "<tr><th>消耗</th><td>发积分红包；积分商城下单（"
            # ★ 商品名必须转义：新增商品时后台不过滤 <>&，
            #   名字里带 <img onerror=...> 的话，谁打开「积分规则」页就执行。
            + ("、".join(f"{hub._esc(x.get('name', '?'))} {hub._mall_price(x)}分" for x in hub.MALL_ITEMS) or "暂无商品")
            + "）</td></tr>"
            f"<tr><th>转赠</th><td>{'开启' if hub.sget('INHERIT_ENABLED') else '关闭'}，把积分转给群内成员{fee}</td></tr>"
            "<tr><th>等级</th><td>"
            + " ≥ ".join(f"{x['name']} {x['value']}分" for x in hub.POINT_LEVELS)
            + "<br><span class='sub'>按「累计积分」计算：消费/兑换不掉级，只有真实产出（签到/游戏赢分/邀请/红包等）才涨</span></td></tr>"
            "</table></div>")
            # 阿福式聊天积分规则表：逐条「条件→积分」，命中即停；空表回退「每N字符」旧规则
    def _m_desc(m):
        m = (m or "").strip()
        if not m: return "任意消息（兜底）"
        if m.startswith("len>="): return f"消息 ≥ {m[5:]} 字"
        return f"包含「{m}」"
    rule_rows = ""
    for i, r in enumerate(hub.chat_rules):
        on = bool(r.get("on"))
        st = "<span class='c-ok'>启用</span>" if on else "<span class='c-dim'>停用</span>"
        rule_rows += (f"<tr><td>{_m_desc(r.get('match'))}</td>"
                      f"<td>{int(r.get('points', 0) or 0)}</td><td>{st}</td>"
                      f"<td><a href='/rule_toggle/{i}'>{'停用' if on else '启用'}</a> · "
                      f"<a href='/rule_del/{i}' class='c-bad'>删除</a></td></tr>")
    if not rule_rows:
        rule_rows = ("<tr><td colspan='4' class='empty'>"
                     "暂无自定义规则（聊天按「每N字符」旧规则计分）</td></tr>")
    body += ("<div class='card mt18' ><h3>📋 聊天积分规则表（命中即停）</h3>"
             "<div class='sub'>文字=消息包含即命中；<code>len>=5</code>=消息满5字；留空=任意消息兜底。"
             "规则表有启用的规则时按表计分（都不命中不计分），旧的「每N字符」规则失效</div>"
             "<table class='tbl'><tr><th>条件</th><th>积分</th><th>状态</th><th>操作</th></tr>"
             + rule_rows + "</table>"
             "<form method='post' action='/rule_add' class='fx-g10-mt12'>"
             "<input type='text' name='match' placeholder='文字或 len>=5（留空=任意消息）' class='f2'>"
             "<input type='number' name='points' placeholder='积分' required class='f1'>"
             "<button class='m0'>➕ 新增规则</button></form></div>")
    return body
