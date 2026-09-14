# -*- coding: utf-8 -*-
"""页面渲染器公共工具（2026-09-13 从 _admin_page 的公共前缀提出）。

这些是多个页面都要用的小工具：群组下拉、页面内小标题分段、保存条等。
原先它们作为**内嵌函数**睡在 `_admin_page` 肚子里，只有 `_admin_page` 自己能用；
提出来放这里，所有页面文件都能 import。

⚠️ 全部是**纯渲染函数**：只读 hub 的当前值，不写任何状态。
"""
from core import hub
import html


def esc(v, quote=True):
    """转义并**容忍非字符串**（后台页面统一走这里，实现只有一份）。

    ★ 为什么不能直接写 `esc(x)`：这些值很多来自**持久化容器**
    （`chat_name_cache` / `user_names` / `admin_logs` / `leave_records` …
    见 `core/data_parts/restore.py`，跨版本回读）或**设置项**（`hub.sget(...)`
    读的是模块全局变量）—— 里面完全可能是**数字**（用户 ID 就是最典型的）。
    而 `esc(12345)` 抛 `AttributeError: int has no attribute replace`，
    且抛在列表推导式 / 生成器 / f-string 里，会让**整个页面 500**。

    实测（`_scratch/_dirty_sweep.py`，把容器与设置项全换成数字/空值，跑 51 页）：
    不统一走这里时 **20 个页面 500**，统一之后 **0 个**。

    `None` 转**空串**而不是字面量 `"None"`。`quote` 与 `html.escape` 同名同义。
    对字符串输入与 `esc(x[, quote])` **逐字节相同** —— 所以页面快照基线不变。
    """
    # 延迟导入：`core.web` 会 import `core.pages`，顶层导入会成环。
    # 与本文件既有的 field_rows / savebar 转发写法保持一致。
    import core.web as webmod
    return webmod._esc(v, quote)


def group_options(selected=0):
    """群组下拉（`<option>` 串，不含 `<select>` 外壳）。选中项加 selected。"""
    out = []
    for cid, name in hub.chat_name_cache.items():
        sel = " selected" if str(cid) == str(selected) else ""
        out.append(f"<option value='{cid}'{sel}>{esc(name)}</option>")
    return "".join(out)


def field_rows(gkey, keys=None):
    """字段渲染（已提到模块级 `core/web.py::_field_rows`，这里做转发方便页面文件调用）。"""
    import core.web as webmod
    return webmod._field_rows(gkey, keys)


def savebar(label="保存设置", hint="保存立即生效，无需重启"):
    """底部保存条（已提到模块级，这里转发）。"""
    import core.web as webmod
    return webmod._savebar(label, hint)


def flt_bar(action):
    """数据页通用的「群组筛选」条：GET ?cid=，选择即提交。"""
    import core.web as webmod
    cid = hub.cur_cid()
    return ("<form method='get' action='" + action + "' class='mb10'>"
            "<div class='lbl'>群组筛选</div><select name='cid' onchange='this.form.submit()'>"
            "<option value='0'>全部群</option>" + webmod._group_options(selected=cid)
            + "</select><noscript><button class='m0'>查看</button></noscript></form>")


# ── B 批（2026-09-13）新增：搬出去的分支里用的 web.py 内部小工具 ──────────
# 这些名字在 web.py 里是**模块级私有函数**（`_group_options` / `_members_body` /
# `_all_user_options`），搬出去的页面文件要能直接按原名调用，所以这里做同名转发。
# 用**函数内 import core.web** 而不是顶层 import：避免 `core.web` ↔ `core.pages`
# 的循环导入（web.py 顶部会 import core.pages 里的东西）。

def _group_options(selected=0):
    """群组下拉（web.py 里的模块级私有函数，转发）。"""
    import core.web as webmod
    return webmod._group_options(selected)


def _members_body(which="records", pgs=None):
    """成员数据只读表（web.py 里的模块级私有函数，转发）。"""
    import core.web as webmod
    return webmod._members_body(which, pgs=pgs)


def _all_user_options(sel=0):
    """全部用户下拉（web.py 里的模块级私有函数，转发）。"""
    import core.web as webmod
    return webmod._all_user_options(sel)


# 下划线别名：搬出去的老代码是按原名调用的（`_field_rows(...)` / `_savebar(...)`），
# 所以除了上面无前缀版，再绑一组下划线名，让搬运代码**原样可跑**。
_field_rows = field_rows
_savebar = savebar
_flt_bar = flt_bar


# ── C 批（2026-09-13）新增 ────────────────────────────────────────────
# `_perm_boxes` / `_perm_summary` 原本是 `_admin_page` 的**内嵌函数**
# （8 类消息权限勾选 / 权限摘要）。level、levelguard 两个子页要用，
# 搬出去后取不到 → 在这里按原逻辑原样复刻一份（纯渲染、只读 hub）。
def _perm_boxes(picked, name="perms"):
    """8 类消息权限勾选（等级新增/编辑共用）。"""
    # 8 类消息权限勾选（等级新增/编辑共用）
    pk = {p.strip() for p in str(picked or "").split(",") if p.strip()}
    out = []
    for _k, _v in hub.LEVEL_PERM_OPTIONS:
        ck = " checked" if _k in pk else ""
        out.append(f"<label class='cb' style='margin:0 14px 6px 0'>"
                   f"<input type='checkbox' name='{name}' value='{_k}'{ck}>"
                   f"<span>{esc(_v)}</span></label>")
    return "<div class='cbs' style='flex-wrap:wrap'>" + "".join(out) + "</div>"


def _perm_summary(picked):
    """权限摘要（用于等级表格里显示「全部放行 / 全部禁止 / 具体名单」）。"""
    pk = {p.strip() for p in str(picked or "").split(",") if p.strip()}
    if len(pk) >= len(hub.LEVEL_PERM_OPTIONS):
        return "<span class='c-ok'>全部放行</span>"
    if not pk:
        return "<span class='c-bad'>全部禁止</span>"
    names = [hub.LEVEL_PERM_NAMES.get(k, k) for k, _v in hub.LEVEL_PERM_OPTIONS if k in pk]
    return esc("、".join(names))


# ── B 批（2026-09-14）新增：数据表通用分页 ─────────────────────────────
# 背景：体检报告①「13 个表格页一次性铺全部数据、手机撑爆」。
# 复用已有的纯函数 `hub._web_page_bounds`（web.py 里就是给列表分页算边界的），
# 只多包一层：读筛选参数里的 page/per + 生成「上一页/下一页」条。
# ⚠️ 与 `_members_body` 里那个内嵌 `paged()` 的区别：本函数生成的翻页链接会**带上
#    全部其它查询参数**（筛选条件不会在翻页后丢失）。
PAGE_SIZE_OPTIONS = (10, 20, 50, 100)


def page_size(fl, default=None):
    """从筛选参数 fl 里解析每页条数（只认 10/20/50/100；非法/缺失回落到默认）。"""
    per = (fl or {}).get("per")
    try:
        per = int(per)
    except (TypeError, ValueError):
        per = None
    if per not in PAGE_SIZE_OPTIONS:
        per = default if default in PAGE_SIZE_OPTIONS else hub.WEB_LIST_PAGE_SIZE
    return per


def paginate(rows, fl, base_url, qs=None):
    """数据表通用分页：切片 rows + 生成翻页条。返回 `(本页行, 页脚 html)`。

    rows     —— 全量行（list，任意类型；调用方自己负责排序/筛选）
    fl       —— 当前筛选参数（dict；读 `page` / `per`）
    base_url —— 本页地址（如 `/page/points/mallord`）
    qs       —— 翻页时要带过去的其它查询参数（dict）；`page`/`per` 由本函数写。

    单页（或空表）时页脚只显示「共 N 条」，不出现翻页按钮，避免视觉噪声。
    """
    per = page_size(fl)
    total = len(rows)
    s, e, page, pages = hub._web_page_bounds(total, (fl or {}).get("page", 1), per)
    if pages <= 1:
        foot = (f"<div class='sub' style='margin-top:8px'>共 {total} 条</div>") if total else ""
        return rows[s:e], foot
    from urllib.parse import urlencode

    def _lnk(p, label, dis):
        if dis:
            return f"<span class='sub m0' >{label}</span>"
        q = dict(qs or {})
        q["page"] = p
        q["per"] = per
        return f"<a href='{base_url}?{urlencode(q)}'><button type='button'>{label}</button></a>"

    foot = ("<div style='margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap'>"
            + _lnk(page - 1, "‹ 上一页", page <= 1)
            + f"<span class='sub m0' >共 {total} 条 · 第 {page}/{pages} 页</span>"
            + _lnk(page + 1, "下一页 ›", page >= pages) + "</div>")
    return rows[s:e], foot
