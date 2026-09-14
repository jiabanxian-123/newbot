# -*- coding: utf-8 -*-
"""features/antispam —— 反捣乱功能集合（**门面文件**）。

六域 + 事件接线层，各自独立成文件：
    spam.py       ① 刷屏 / 重复消息拦截
    links.py      ② 链接域名白名单
    forcesub.py   ③ 强制订阅频道
    joinverify.py ④ 入群验证
    joingate.py   ⑤ 进群硬门槛
    raid.py       ⑥ 防突袭人墙 + 新人观察期
    sensitive.py  ⑦ 敏感词过滤（2026-09-13 从 features/points 搬来：本质是群管拦截，
                      与防刷同族；见 REVIEW §八 ⑩）
    events.py     Telegram 事件接线层（含两个其实属于别域的入口，见该文件说明）

本文件**只做 re-export**，不含任何业务函数 —— 它的存在是为了让
`from features.antispam import xxx`（bot.py 的用法）与 815 个测试补丁点
（`m.xxx = fake`）继续生效。

抽缝约定（见 README「抽缝」）：
  - 子模块只允许 `from core import hub`；禁止 `from bot import ...`
  - 跨模块调用一律写 `hub.X`，且必须在函数体里（顶层不能访问 hub）

已知归属问题（留给后续，不在本次拆分范围内）：
  - `on_join_request` 是邀请归因，属于 features/invite 域
  - `on_my_chat_member` 是机器人授权溯源，不属于六域
"""

from core import hub  # noqa: F401  （抽缝守卫要求每个抽出模块都导入 hub）

from .spam import (
    _antispam_check,
    _antispam_hit,
    _antispam_norm,
    _antispam_prune,
)
from .links import (
    _link_domains,
    _link_whitelisted,
)
from .forcesub import (
    _forcesub_enforce,
    _fsub_links,
    _fsub_parse,
    _fsub_probe,
)
from .joinverify import (
    _captcha_render,
    _join_verify_handle_text,
    _join_verify_pass,
    _join_verify_start,
    _jv_options,
    _jv_wrong_hit,
    cmd_jv_pass,
    join_verify_sweep,
)
from .joingate import (
    _is_join_transition,
    _join_gate_check,
)
from .raid import (
    _observe_enforce,
    _raid_active,
    _raid_on_join,
    _raid_recover,
    observe_check_sweep,
)
from .sensitive import (
    _sensitive_enforce,
    _sensitive_hit,
)
from .events import (
    on_join_request,
    on_member_event,
    on_my_chat_member,
    on_new_members_msg,
)


__all__ = [
    "_antispam_check",
    "_antispam_hit",
    "_antispam_norm",
    "_antispam_prune",
    "_captcha_render",
    "_forcesub_enforce",
    "_fsub_links",
    "_fsub_parse",
    "_fsub_probe",
    "_is_join_transition",
    "_join_gate_check",
    "_join_verify_handle_text",
    "_join_verify_pass",
    "_join_verify_start",
    "_jv_options",
    "_jv_wrong_hit",
    "_link_domains",
    "_link_whitelisted",
    "_observe_enforce",
    "_raid_active",
    "_raid_on_join",
    "_raid_recover",
    "_sensitive_enforce",
    "_sensitive_hit",
    "cmd_jv_pass",
    "join_verify_sweep",
    "observe_check_sweep",
    "on_join_request",
    "on_member_event",
    "on_my_chat_member",
    "on_new_members_msg",
]
