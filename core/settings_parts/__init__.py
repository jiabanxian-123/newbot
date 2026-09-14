# -*- coding: utf-8 -*-
"""设置套用的分块实现 —— 2026-09-13 从 core/settings.py 的 apply_settings 拆出。

只做搬运，未改任何逻辑。每个函数负责一类字段（数字/布尔/多选/等级表/文本/赛马三件套/词表），
签名统一 `(cfg, applied)`：cfg 是待套用的设置字典，applied 是"实际生效的键值"累加器（dict，原地改）。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
⚠️ 本包**禁止** import core.settings（循环导入）。
"""

from core.settings_parts.load import (  # noqa: E402,F401
    _apply_fields,
    _apply_web_credentials,
    _apply_cmd_aliases_menu,
    _apply_snapshot_and_layout,
    _apply_group_settings,
    _apply_persist_back,
)
from core.settings_parts.apply import (  # noqa: E402,F401
    _apply_numeric_fields,
    _apply_bool_fields,
    _apply_multi_fields,
    _apply_levels_items,
    _apply_text_fields,
    _apply_horse_trio,
    _apply_lists_fields,
)

__all__ = [
    "_apply_fields",
    "_apply_web_credentials",
    "_apply_cmd_aliases_menu",
    "_apply_snapshot_and_layout",
    "_apply_group_settings",
    "_apply_persist_back",
    "_apply_numeric_fields",
    "_apply_bool_fields",
    "_apply_multi_fields",
    "_apply_levels_items",
    "_apply_text_fields",
    "_apply_horse_trio",
    "_apply_lists_fields",
]
