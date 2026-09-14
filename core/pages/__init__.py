# -*- coding: utf-8 -*-
"""网页后台「页面渲染器」包（2026-09-13 从 core/web.py 的 _admin_page 拆分而来）。

## 这个包是什么

原来 `core/web.py` 里有一个 1,189 行的 `_admin_page` 函数，里面用
`if gkey == "xxx"` / `elif sub == "yyy"` 一长串分支渲染**全部 21 个后台页面**。
想改一个页面，得钻进这个千行函数里找地方，容易串到隔壁页面 —— 这是
「改 A 崩 B」的主要来源。

现在拆成：**一个页面一个文件 + 一张路由表**。加页面 = 新建文件 + 表里加一行。

## 硬规矩（拆出来必须遵守）

1. **禁止 `from bot import xxx`** —— 一律 `from core import hub`，用 `hub.X` 延迟绑定。
   这样测试里 `bot.X = fake` 才能实时穿透。
2. **禁止在模块顶层访问 hub** —— 只能在函数体里访问（顶层访问会在导入期就执行，
   那时 hub 还没 bind 完）。
3. **`bot.py` 必须保留同名 re-export** —— 由 `test_seam_hub.py` 守卫。

## 页面函数约定

- 函数名：`page_<分组>` 或 `page_<分组>_<子页>`
- 签名：`f(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname)` →
  返回页面**主体 HTML**（不含外层 `<html>`；外层由 `_page()` 套）
- 返回值类型：`str`（老的 `_admin_page` 返回 bytes，由 `_page()` 转）

## 路由表

`PAGE_RENDERERS`: `(gkey, sub) -> 函数名`。查不到就走兜底（见 `fallback.py`）。
"""
# ── 已搬出的页面函数（导入即注册）────────────────────────────────
# ⚠️ 抽缝硬规矩：新加页面函数后，**必须在 bot.py 里加 re-export**，
#    否则 test_seam_hub.py 会红（它检查「抽出模块的每个模块级名字都能从 bot 取到」）。
from core import hub                                             # noqa: E402,F401
from core.pages.commands import page_commands                    # noqa: E402,F401
from core.pages.security import page_security                    # noqa: E402,F401
from core.pages.points.mall import page_points_mall              # noqa: E402,F401
from core.pages.points.mallord import page_points_mallord        # noqa: E402,F401
# ── B 批（2026-09-13）───────────────────────────────────────────
from core.pages.members.mlist import page_members_mlist          # noqa: E402,F401
from core.pages.members.main import page_members_main            # noqa: E402,F401
from core.pages.admin import page_admin                          # noqa: E402,F401
from core.pages.lottery import page_lottery                      # noqa: E402,F401
# ── C 批（2026-09-13）───────────────────────────────────────────
from core.pages.invite import page_invite                        # noqa: E402,F401
from core.pages.season import page_season                        # noqa: E402,F401
from core.pages.points.adjust import page_points_adjust          # noqa: E402,F401
from core.pages.points.impexp import page_points_impexp          # noqa: E402,F401
from core.pages.points.level import page_points_level            # noqa: E402,F401
from core.pages.points.levelguard import page_points_levelguard  # noqa: E402,F401
from core.pages.points.rule import page_points_rule              # noqa: E402,F401
from core.pages.points.buypkg import page_points_buypkg          # noqa: E402,F401
from core.pages.points.redeem import page_points_redeem          # noqa: E402,F401
from core.pages.points.buy import page_points_buy                # noqa: E402,F401
from core.pages.points._fallback import page_points_fallback     # noqa: E402,F401

__all__ = [
    "page_commands",
    "page_security",
    "page_points_mall",
    "page_points_mallord",
    "page_members_mlist",
    "page_members_main",
    "page_admin",
    "page_lottery",
    "page_invite",
    "page_points_adjust",
    "page_points_impexp",
    "page_points_level",
    "page_points_levelguard",
    "page_points_rule",
    "page_points_buypkg",
    "page_points_redeem",
    "page_points_buy",
    "page_points_fallback",
    "PAGE_RENDERERS",
    "SUB_PAGE_RENDERERS",
    "render_page",
]

# ══════════════════════════════════════════════════════════════════════════
# 路由表（★ 加 / 删页面就改这里）
# ══════════════════════════════════════════════════════════════════════════
# 主页面表：gkey → 渲染函数。
#   「主页面」= 顶层分组页（成员 / 管理员 / 命令 / 安全 / 抽奖 / 邀请…）。
# ⚠️ 顺序有讲究：字典本身无序，但 `render_page()` 里**先查主表、再查子页表**。
#    历史上 `members/mlist` 必须排在 `members` 之前（子页优先），
#    这里靠「先精确匹配 (gkey, sub)、再匹配 gkey」来实现，不依赖插入顺序。
PAGE_RENDERERS = {
    "members": page_members_main,
    "admin": page_admin,
    "commands": page_commands,
    "security": page_security,
    "lottery": page_lottery,
    "invite": page_invite,
    "season": page_season,
}

# 特殊组合表：(gkey, sub) → 渲染函数。
# 用于「同一个 gkey 下，某个子页要单独一个文件」的情况。
SPECIAL_RENDERERS = {
    ("members", "mlist"): page_members_mlist,
}

# 积分子页表：sub → 渲染函数（gkey == "points" 时生效）。
SUB_PAGE_RENDERERS = {
    "adjust": page_points_adjust,
    "impexp": page_points_impexp,
    "level": page_points_level,
    "levelguard": page_points_levelguard,
    "mall": page_points_mall,
    "rule": page_points_rule,
    "buypkg": page_points_buypkg,
    "redeem": page_points_redeem,
    "mallord": page_points_mallord,
    "buy": page_points_buy,
}

# 需要额外参数（自递归状态）的页面：签名多了 uid / saved / bad / note。
# `render_page()` 会给**所有**页面都传这些（多余的关键字参数由页面的 **kw 兜住），
# 只有 admin 真正用到。
_EXTRA_KW = ("uid", "saved", "bad", "note")


def render_page(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt,
                uid=0, saved=False, bad=False, note="", **kw):
    """按路由表把请求分发给对应页面函数，返回它的返回值。

    分发顺序（★ 别随便改，改了会串页）：
      1. `(gkey, sub)` 精确匹配 —— 如 ("members", "mlist")
      2. `gkey` 匹配主表 —— 如 members / admin / commands / lottery / invite
      3. `gkey == "points"` 且有 sub → 查积分子页表；查不到走积分子页兜底
      4. 都没有 → 返回 `None`，表示「交给 web.py 的通用表单兜底」

    ⚠️ 第 3 步里 `(gkey, sub)` 优先于 `gkey`：老代码里 `members/mlist` 就是
       因为写在 `elif gkey == "members"` **之前**才生效的。

    `uid` / `saved` / `bad` / `note` 只在 admin 分支用到（自递归要透传），
    其余页面签名里没有，靠 `**kw` 吸收。
    """
    # 1) (gkey, sub) 精确匹配
    fn = SPECIAL_RENDERERS.get((gkey, sub))
    if fn is not None:
        return fn(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, None, **kw)

    # 2) 主页面表
    fn = PAGE_RENDERERS.get(gkey)
    if fn is not None:
        extra = {}
        if fn is page_admin:
            extra = dict(uid=uid, saved=saved, bad=bad, note=note)
        return fn(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, None, **extra, **kw)

    # 3) 子页总线（还原老代码的 `elif sub:`）
    #    ⚠️ 两个坑，都是实测踩出来的（2026-09-13 D 批）：
    #    ① **不限于 points**：老代码的 `elif sub:` 是通用的，`SUB_PAGE_RENDERERS`
    #       按 sub 查表，不检查 gkey。限定成 `gkey == "points"` 会让
    #       「未匹配分组 + 子页」的请求从「走子页兜底」变成「走 web.py 通用表单」，行为变了。
    #    ② **sname 必须真实取**：mall / mallord 两个页面直接把第 9 个参数当标题用
    #       （`<h1>{gicon} {sname}</h1>`），传 None 会让标题变成「None」。
    #       其余 9 个积分子页在函数开头自己重算了 sname，所以只有这两页会露。
    if sub:
        subs = {k: n for k, n in (hub.SUBPAGES.get(gkey) or [])}
        sname = subs.get(sub, sub)
        fn = SUB_PAGE_RENDERERS.get(sub) or page_points_fallback
        return fn(gkey, sub, gicon, gname, msg, err, sel_flt_cid, flt, sname, **kw)

    # 4) 交给 web.py 的通用兜底
    return None

