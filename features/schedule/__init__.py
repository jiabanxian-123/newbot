# -*- coding: utf-8 -*-
"""features/schedule —— **门面文件**（2026-09-14 分家）。

每个任务独立成一个小文件：
  · emergency.py  `emergency_if_needed`   应急赠送（**不是定时任务**，归属存疑见文件头）
  · lurker.py     `lurker_sweep`          潜水号清理（每 6 小时）
  · reset.py      `daily_reset_scheduler`  每日重置
  · status.py     `cmd_schedule_status`    /定时任务状态（**段落与序号由文件内的任务表生成**）

本文件**只做 re-export**，不含业务函数 —— 它的存在是为了让
`from features.schedule import xxx`（bot.py 的用法）与测试补丁点（`m.xxx = fake`）继续生效。

⚠️ 仍未做的那一半（需要用户拍板，见交接说明）：**任务的注册**目前散在三处 ——
`bot.py::main`（auto_backup / join_verify_sweep / observe_check_sweep / announce_sweep /
lurker_sweep）、`core/entry.py::post_init`（daily_reset / leaderboard / season_settle /
hourly_race / lottery / poker_watchdog / data_save / delete_sweeper）。
把「注册」也统一到一张表要动启动接线，不属"只搬不改"，故未做。
"""

from core import hub  # noqa: F401  （抽缝守卫要求每个抽出模块都导入 hub）

from features.schedule.emergency import emergency_if_needed  # noqa: F401
from features.schedule.lurker import lurker_sweep  # noqa: F401
from features.schedule.reset import daily_reset_scheduler  # noqa: F401
from features.schedule.status import cmd_schedule_status  # noqa: F401

__all__ = [
    "emergency_if_needed",
    "lurker_sweep",
    "daily_reset_scheduler",
    "cmd_schedule_status",
]
