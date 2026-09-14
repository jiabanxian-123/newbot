# -*- coding: utf-8 -*-
"""core/data.py 拆出的子包（2026-09-13）。只 re-export，不含业务逻辑。

  - `restore.py`  —— load_data 的分段恢复流水线（10 段，按数据域）
  - `snapshot.py` —— force_save_now 的快照构造（build_data_snapshot）
"""
from core.data_parts.restore_flow import (  # noqa: E402,F401
    _restore_web_settings_only,
    _restore_embedded_settings,
    _send_restore_summary,
)
from core.data_parts.snapshot import build_data_snapshot  # noqa: E402,F401
from core.data_parts.restore import (  # noqa: E402,F401
    _restore_chips,
    _restore_profit_and_season,
    _restore_titles_and_names,
    _restore_points_ledger,
    _restore_guesses_refund,
    _restore_redeem_orders,
    _restore_invite_data,
    _restore_member_and_pending,
    _restore_group_data,
    _restore_permissions_and_history,
)

__all__ = [
    "_restore_web_settings_only",
    "_restore_embedded_settings",
    "_send_restore_summary",
    "build_data_snapshot",
    "_restore_chips",
    "_restore_profit_and_season",
    "_restore_titles_and_names",
    "_restore_points_ledger",
    "_restore_guesses_refund",
    "_restore_redeem_orders",
    "_restore_invite_data",
    "_restore_member_and_pending",
    "_restore_group_data",
    "_restore_permissions_and_history",
]
