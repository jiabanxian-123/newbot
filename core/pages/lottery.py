"""网页后台 · 群组抽奖（lottery）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（B 批，2026-09-13）。
  原条件：`elif gkey == 'lottery':`（从积分系统移出的独立组，无子页）。
  `sname = gname` 那一行原就在分支体内，原样保留。

⚠️ 搬运铁律（本项目硬规矩）：
  - 模块顶层只准 `from core import hub`，符号一律 `hub.X` 延迟绑定；
  - **禁止** `from bot import ...`；
  - 函数内可自由 `hub.` 访问所有全局（常量、函数、状态）。

返回值：通常 `body`（str）。若返回 `(body, redirect)` 二元组，表示 `_admin_page`
需要按 `redirect = (gkey, kwargs)` 重跑一次（自递归用）。
"""
from core import hub
from core.pages import tpl
from core.pages._common import (  # noqa: F401
    esc,
    group_options, field_rows, savebar, flt_bar, paginate,
    _group_options, _field_rows, _flt_bar, _savebar,
)
import html  # noqa: F401
from datetime import datetime
import time


def page_lottery(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """群组抽奖（lottery）"""
    sname = gname  # 顶层分支：组名直接取参数（原 elif sub: 内由子页表推导）
            # 群组抽奖页：新增抽奖表单 + 活动列表（可取消）+ 配置表单
    def _fmt_ts(t):
        try: return datetime.fromtimestamp(float(t), hub.BEIJING_TZ).strftime("%m-%d %H:%M")
        except Exception: return "-"
    def _row(lo):
        cid = lo.get("chat_id", -1)
        status = lo.get("status", "?")
        active = status == "open"
        badges = {"open": "<span class='c-ok'>进行中</span>",
                  "drawing": "<span class='c-warn'>开奖中</span>",
                  "finished": "<span class='c-dim'>已结束</span>",
                  "cancelled": "<span class='c-bad'>已取消</span>"}
        ends_in = ""
        if active:
            left = int(lo["end_ts"] - time.time())
            ends_in = f" · 剩 {left}s" if left > 0 else " · 到点开奖中"
        winners = lo.get("winners") or []
        win_txt = f"{len(winners)} 人" if winners else "—"
        fee_txt = f" {int(lo.get('fee', 0))}分/人" if lo.get("fee") else " 免费"
        cancel_btn = (f"<a class='q' href='/lottery_cancel?cid={cid}' "
                      f"onclick=\"return confirm('取消该抽奖并退还参与费？')\">🛑 取消</a>" if active else "")
        return (f"<tr><td><code>{cid}</code> {esc(hub.chat_name_cache.get(cid) or '')}</td>"
                f"<td>{esc(str(lo.get('title', ''))[:24])}</td>"
                f"<td>{badges.get(status, status)}{ends_in}</td>"
                f"<td>{len(lo.get('participants', []))}</td>"
                f"<td>{win_txt}</td>"
                f"<td>{fee_txt}</td>"
                f"<td>{_fmt_ts(lo.get('start_ts'))}</td>"
                f"<td>{cancel_btn}</td></tr>")
            # 进行中优先；其余按 start_ts 倒序
    items = list(hub.lotteries.items())
    items.sort(key=lambda kv: (kv[1].get("status") != "open", -(kv[1].get("start_ts") or 0)))
    rows, rows_foot = paginate(items, flt, "/page/lottery")
    rows_html = "".join(_row(lo) for _, lo in rows)
    if not rows_html:
        rows_html = "<tr><td colspan='8' class='empty'>暂无活动，用上方表单创建第一个</td></tr>"
    status_html = ("<div class='err'>⚠️ 群组抽奖当前已关闭，先打开下方「群组抽奖总开关」</div>"
                   if not hub.sget("LOTTERY_ENABLED") else "")
    body = (f"<h1>{gicon} {sname}</h1>"
            f"<div class='sub'>在这里创建抽奖 → 机器人自动发到群里 → 群成员发「{esc(hub.sget('LOTTERY_KEYWORD'))}」参与 → 到点自动开奖</div>"
            f"{msg}{err}{status_html}" +
                    # 新增抽奖表单（照阿福格式：描述/关键词/开奖方式下拉/结构化奖品行）
            tpl.render("lottery_create",
                       group_options=_group_options(),
                       keyword=esc(hub.sget("LOTTERY_KEYWORD")),
                       duration=max(10, int(hub.sget("LOTTERY_DEFAULT_DURATION"))),
                       fee=int(hub.sget("LOTTERY_FEE") or 0)) +
                    # 活动列表
            _flt_bar("/page/lottery") +
            "<div class='card'><h3>📋 活动列表（进行中置顶）</h3>"
            "<table class='tbl'><tr><th>群</th><th>标题</th><th>状态</th><th>参与</th><th>中奖</th><th>参与费</th><th>开局</th><th>操作</th></tr>"
            f"{rows_html}</table>{rows_foot}</div>"
                    # 配置表单
            f"<div class='card'><h3>⚙️ 配置</h3><form method='post' action='/save'>"
            f"<input type='hidden' name='group' value='lottery'>"
            + _field_rows("lottery")
            + "<div class='sub mt16' >公告模板支持占位符："
              f"<code>{'{title}'}</code> <code>{'{n}'}</code> <code>{'{end_line}'}</code> "
              f"<code>{'{desc_block}'}</code> <code>{'{fee_block}'}</code> "
              f"<code>{'{keyword_block}'}</code> <code>{'{prize_list}'}</code> "
              f"<code>{'{req_block}'}</code>"
              "<br>结果模板：<code>{'{title}'}</code> <code>{'{winners}'}</code> "
              f"<code>{'{n}'}</code> <code>{'{w}'}</code>"
              "<br>参与/失败模板：<code>{'{nick}'}</code> <code>{'{n}'}</code> "
              f"<code>{'{balance}'}</code> <code>{'{fee_line}'}</code> <code>{'{reason}'}</code>"
              "<div class='sub'>⚠️ 清空或改成旧版横幅样式会被自动升回新版（只认旧内置默认，自定义内容不动）</div></div>" +
            _savebar("保存全部抽奖设置") + "</form></div>")
    return body
