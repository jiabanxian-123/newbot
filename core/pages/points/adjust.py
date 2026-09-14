"""网页后台 · 积分 · adjust（points/adjust）

从 core/web.py 的 `_admin_page` 里**原样搬出来**的分支（C 批，2026-09-13）。
  原条件：`if gkey == "points" and sub == "adjust":`

  ⚠️ 本页依赖总线的公共前缀 `subs` / `sname`，已原样抄进函数开头。

⚠️ 搬运铁律（本项目硬规矩）：
  - 模块顶层只准 `from core import hub`，符号一律 `hub.X` 延迟绑定；
  - **禁止** `from bot import ...`；
  - 函数内可自由 `hub.` 访问所有全局。
"""
from core import hub
from core.pages._common import (  # noqa: F401
    esc,
    group_options, field_rows, savebar, flt_bar,
    _group_options, _members_body, _all_user_options,
    _field_rows, _savebar, _flt_bar,
)
import html  # noqa: F401
import time
from datetime import datetime



def page_points_adjust(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname):
    """积分 · adjust（points/adjust）"""
    subs = {k: n for k, n in hub.SUBPAGES.get(gkey, [])}
    sname = subs.get(sub, sub)
            # 2026-09-12 用户报「网页端积分加减分无效根本用不了」。
            # 后端 /points_adj 本地端到端实测完全正常（加分/减分/超扣拦截/落盘全部通过），
            # 线上源码与本地逐行一致 ⇒ 卡点在**表单选不了人**：
            # 旧表单只有一个 `input type=number list=datalist` 的「用户 ID」，
            # number+datalist 在移动端浏览器上支持极差（下拉根本弹不出来），
            # 而玩家不可能去记 10 位数字 ID ⇒ 管理员实际上根本没法完成这一步。
            # 改为「① 选群 → ② 原生下拉选成员」两步（原生 select 在各端都正常），
            # 另留「手填用户」兜底，支持 数字 ID / @用户名 / 昵称 三种写法。
    _gsel = sel_flt_cid
    _hdr = (f"<h1>{gicon} {sname}</h1>"
            f"<div class='sub'>直接给玩家加/减统一积分（正数加、负数减），立即生效并落盘；等效群里的 /add 命令</div>{msg}{err}")
    _card1 = ("<div class='card'><h3>① 选择群组</h3>"
              "<div class='sub'>先选群并载入，下面才会列出该群成员（手机端同样可用）</div>"
              "<form method='get' action='/page/points/adjust' class='fx-g10-ae-wrap'>"
              "<div style='flex:1;min-width:220px'><div class='sub'>群组</div>"
              "<select name='cid' required><option value=''>— 请选择群 —</option>"
              + _group_options(selected=_gsel) + "</select></div>"
              "<button type='submit' class='mt0'>🔍 载入成员</button></form></div>")
    if _gsel:
        _mem_opts = "".join(
            f"<option value='{u}'>{esc(hub.user_names.get(u, str(u)))}（{u}）</option>"
            for u in sorted(set(hub.game_chips.get(_gsel, {})) | set(hub.member_profiles.get(_gsel, {})),
                            key=lambda u: -int(hub.game_chips.get(_gsel, {}).get(u, 0) or 0)))
        _mem_sel = (f"<select name='uid' class='f1-200'>"
                    f"<option value=''>（可选）从该群成员里挑一个</option>{_mem_opts}</select>"
                    if _mem_opts else
                    "<div class='sub f1' >该群还没有任何成员记录，请用下面的「手填用户」</div>")
        _known = len(set(hub.game_chips.get(_gsel, {})) | set(hub.member_profiles.get(_gsel, {})))
        _card2 = ("<div class='card mt18' ><h3>② 执行加减分</h3>"
                  f"<div class='sub'>目标群：<b>{esc(hub.chat_name_cache.get(_gsel) or '未命名群')}</b>"
                  f" <code>{_gsel}</code> · 已知成员 {_known} 人</div>"
                  "<form method='post' action='/points_adj'>"
                  f"<input type='hidden' name='cid' value='{_gsel}'>"
                  "<div class='row'><div class='lbl'>成员<small>原生下拉，手机也能正常选</small></div>"
                  f"{_mem_sel}</div>"
                  "<div class='row'><div class='lbl'>手填用户<small>数字 ID / @用户名 / 昵称任填其一，填了就优先用它</small></div>"
                  "<input type='text' name='uid_raw' placeholder='如 123456789 或 @name 或 昵称' class='f1-200'></div>"
                  "<div class='row'><div class='lbl'>积分变动<small>正数=加分，负数=扣分，0 无效</small></div>"
                  "<input type='number' name='amount' value='1000' required></div>"
                  "<button type='submit'>💾 执行加减分</button></form></div>")
        _card3 = hub._point_adj_log_card()
    else:
        _card2 = ("<div class='card mt18' ><h3>② 执行加减分</h3>"
                  "<div class='sub'>请先在 ① 里选好群组并点「🔍 载入成员」</div></div>")
        _card3 = ""
    body = _hdr + _card1 + _card2 + _card3
    return body
