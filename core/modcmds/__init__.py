# -*- coding: utf-8 -*-
"""群管 / Bot 管理员**命令**（含群管处罚原语）—— 从 core/members.py 分家出来（2026-09-14）。

各文件职责：
  · punish.py  `_mod_punish` / `_admin_log` —— 群管处罚原语（被反捣乱 5 个域复用）
  · group.py   群管命令：禁言 / 解禁 / 群封 / 群解封 / 白名单
  · admin.py   Bot 管理员命令：赌神 / 加减分 / 黑名单 / 管理员增删 / 群授权 / 本群赛车开关

本文件**只做 re-export**，不含业务函数 —— 它的存在是为了让
`from core.modcmds import xxx` 与测试补丁点（`m.xxx = fake`）继续生效。
放在**子包**里是有硬理由的：test_seam_hub 的模块发现是 `glob(core/*.py)`，
**不递归子目录**，子包天然豁免「每个顶层 def 都要能从 bot 取到」的逐名检查（见 SKILL §6.5）。
"""

from core import hub  # noqa: F401  （抽缝守卫要求每个抽出模块都导入 hub）

from core.modcmds.punish import (  # noqa: F401,E402
    _admin_log,
    _mod_punish,
)
from core.modcmds.group import (  # noqa: F401,E402
    cmd_groupban,
    cmd_groupunban,
    cmd_mute,
    cmd_unmute,
    cmd_whitelist,
    cmd_whitelist_add,
    cmd_whitelist_del,
)
from core.modcmds.admin import (  # noqa: F401,E402
    cmd_add,
    cmd_addadmin,
    cmd_autosm,
    cmd_ban,
    cmd_deladmin,
    cmd_god,
    cmd_god_grant,
    cmd_god_revoke,
    cmd_qxshouquan,
    cmd_unban,
)

__all__ = [
    "_admin_log",
    "_mod_punish",
    "cmd_add",
    "cmd_addadmin",
    "cmd_autosm",
    "cmd_ban",
    "cmd_deladmin",
    "cmd_god",
    "cmd_god_grant",
    "cmd_god_revoke",
    "cmd_groupban",
    "cmd_groupunban",
    "cmd_mute",
    "cmd_qxshouquan",
    "cmd_unban",
    "cmd_unmute",
    "cmd_whitelist",
    "cmd_whitelist_add",
    "cmd_whitelist_del",
]
