# -*- coding: utf-8 -*-
"""core.hub —— 抽出的模块访问 bot 命名空间的**唯一入口**（late-binding 代理）。

为什么需要它
------------
`bot.py` 里有 815 个测试补丁点（`m.xxx = fake`）。一旦把代码搬到别的文件，
被搬走的代码若**按值**引用 `bot` 的符号（`from bot import sget`），补丁就失效了 ——
测试照样绿，但测的是另一个对象。这是「拆文件」最容易踩的静默坑。

本模块用**注入 + 延迟查表**解决：
    1. `bot.py` 在导入期调用 `core.hub.bind(globals())`，把**自己的 __dict__** 交出来；
    2. 抽出的模块一律通过 `hub.sget(...)` / `hub.safe_send(...)` 访问；
    3. `hub.X` 是运行到那一行时才去 `_ns["X"]` 查，所以 `m.X = fake` 立刻生效。

为什么用 `globals()` 而不是 `sys.modules[__name__]`
--------------------------------------------------
测试用 `spec_from_file_location("m", BOT)` 加载 bot.py，模块名是 `"m"` 而非 `"bot"`，
且 `module_from_spec` **不会**把模块登记进 `sys.modules`。
`globals()` 返回的就是模块的 `__dict__` 本身（同一个对象），
所以 `m.X = fake` 改的正是我们查表用的那个 dict —— 与模块叫什么名字无关。

使用约定（两条硬规则，有测试守着）
--------------------------------
1. **抽出的模块里，禁止在模块顶层访问 hub**（那时 bind 可能还没跑）。
   只允许在函数体里用。需要常量就定义在自己模块内。
2. **禁止 `from bot import xxx`**。一律走 `hub.xxx`。
"""
import os as _os
import glob as _glob
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_source_cache = None

_ns = None
_bound_by = None


def root():
    """仓库根目录（core/hub.py 的上一级）。"""
    return _ROOT


def codebase_source():
    """整个代码库的源码：`bot.py` + `core/*.py` + `features/**/*.py`（带缓存）。

    为什么需要：抽出模块后，模块自己的 `__file__` **只指向它自己**。但
    「按函数名匹配」的白名单/自检必须回看整个代码库 —— 例如
    `audit_autodel_whitelist` 要确认 `safe_send` 这个 def 还在不在，
    而 `safe_send` 已经搬到 `core/messaging.py` 了。只读自己的文件会**静默误报**
    （白名单"失配"报警 → 该删的不删），正是最阴的那类 bug。

    测试侧同样用它：`bot_src.codebase_source()` 就是转发到这里。
    """
    global _source_cache
    if _source_cache is not None:
        return _source_cache
    parts = []
    for pat in ("bot.py", "core/*.py", "features/**/*.py"):
        for p in sorted(_glob.glob(_os.path.join(_ROOT, pat), recursive=True)):
            try:
                with open(p, encoding="utf-8") as f:
                    parts.append(f.read())
            except OSError:
                pass
    _source_cache = "\n".join(parts)
    return _source_cache


def bind(ns):
    """由 bot.py 在导入期调用：交出它自己的 globals()。返回是否成功。"""
    global _ns, _bound_by
    if not isinstance(ns, dict):
        raise TypeError(f"bind() 需要一个模块命名空间 dict，收到 {type(ns).__name__}")
    _ns = ns
    _bound_by = ns.get("__file__") or ns.get("__name__")
    return True


def bound():
    """是否已 bind（测试与自检用）。"""
    return _ns is not None


def where():
    """记录 bind 时交出的文件（诊断用）。"""
    return _bound_by


def namespace():
    """返回 bind 时交出的那个 dict 本身（诊断/自检用）。

    正常情况下它 **就是** 被测 bot.py 的 `__dict__` —— 所以 `m.X = fake`
    改的正是 hub 查表用的那个 dict。守卫测试靠这一点确认机制没被改坏。
    """
    return _ns


def get(name, default=None):
    """显式取值：拿不到时返回 default，不抛异常。"""
    if _ns is None:
        return default
    return _ns.get(name, default)


def require(name):
    """显式取值：拿不到就抛 —— 用于「这里必须有」的场合，早失败好过静默错。"""
    if _ns is None:
        raise RuntimeError(
            "core.hub 尚未 bind。bot.py 需要在导入期调用 core.hub.bind(globals())；"
            "若你在跑测试，请确认 bot.py 顶部已导入 core.hub。"
        )
    try:
        return _ns[name]
    except KeyError:
        raise AttributeError(f"bot 命名空间里没有 {name!r}") from None


def __getattr__(name):
    """`hub.sget` → 运行到这一行才去 bot 的 __dict__ 查，因此补丁实时生效。"""
    if name.startswith("__"):
        raise AttributeError(name)
    if _ns is None:
        raise RuntimeError(
            f"core.hub 尚未 bind，无法取 {name!r}。"
            "bot.py 需要在导入期调用 core.hub.bind(globals())。"
        )
    try:
        return _ns[name]
    except KeyError:
        raise AttributeError(
            f"bot 命名空间里没有 {name!r}（hub 只能代理 bot 里存在的符号）"
        ) from None


def __dir__():
    return sorted(_ns) if _ns else []


def set(name, value):  # noqa: A001  —— 刻意叫 set，读起来像「写进 bot 命名空间」
    """把 value 写进 bot 的命名空间，并返回 value。

    为什么需要它：抽出的模块里如果有 `global X; X = v`，那个 `X` 是 bot 的模块级
    全局变量。搬运时**不能**用前导别名（`X = hub.X` 会在写入后变成陈旧副本），
    必须把写入落到 bot 的 `__dict__` 上 —— 也就是这个函数。

    模块对象不支持自定义 `__setattr__`，所以 `hub.X = v` 只会改到 hub 自己身上，
    达不到目的。这就是为什么写入必须显式走 `hub.set("X", v)`。
    """
    if _ns is None:
        raise RuntimeError(
            f"core.hub 尚未 bind，无法写 {name!r}。"
            "bot.py 需要在导入期调用 core.hub.bind(globals())。"
        )
    _ns[name] = value
    return value


def set_many(names, values):
    """一次写多个全局变量：`a, b = x, y` → `hub.set_many(("a", "b"), (x, y))`。

    元组解包赋值没法改写成多次 `hub.set`（右值只求值一次，且可能是迭代器），
    所以单独提供一个批量写入。长度不符时照 Python 的解包语义抛 ValueError。
    """
    if _ns is None:
        raise RuntimeError(
            "core.hub 尚未 bind，无法写 " + repr(list(names)) + "。"
            "bot.py 需要在导入期调用 core.hub.bind(globals())。"
        )
    vals = list(values)
    names = list(names)
    if len(vals) != len(names):
        raise ValueError(f"解包失败：期望 {len(names)} 个值，实际 {len(vals)} 个")
    for n, v in zip(names, vals):
        _ns[n] = v
    return vals
