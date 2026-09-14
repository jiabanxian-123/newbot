# -*- coding: utf-8 -*-
"""features/points —— 积分经济（**门面文件**）。

REVIEW §八 ⑤「把 points 拆成 8 块」：按功能域拆文件后，本文件只做 re-export。

    ledger.py     账本地基（累计积分记账 / 流水 / 我的积分 / 排行）
    bulkio.py     ⑧ 批量导入导出（CSV / xlsx → [(uid, 积分, 昵称)]）
    sign.py       ① 签到（+ 新人首次发言奖励）
    chat.py       ② 聊天积分（网页规则表）
    levels.py     ③ 等级与消息权限（判定 / 升降级通知 / 成员标签 / 消息管控）
    titles.py     ④ 称号（商店 / 兑换 / 佩戴 / 加封）
    shop.py       ⑤ 商城与兑换（+ 积分↔排位分比例文案）
    redpacket.py  ⑥ 红包
    transfer.py   ⑦ 转赠与买分
    deep.py       深链接线层（?start= 的私聊落地面，跨商城/兑换两块）

（`sensitive.py` 已于 2026-09-13 搬去 `features/antispam/sensitive.py` ——
 敏感词拦截本质是群管，不属于积分域；见 REVIEW §八 ⑩ / `test_sensitive_relocation.py`）

本文件**只做 re-export**，不含任何业务函数 —— 它的存在是为了让
`from features.points import xxx`（bot.py 的用法）与全项目测试的补丁点
（`m.xxx = fake`）继续生效。

抽缝约定（见 README「抽缝」）：
  - 子模块只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 跨模块调用一律写 `hub.X`，且必须在函数体里（顶层不能访问 hub）
"""

from core import hub  # noqa: F401  （抽缝守卫要求每个抽出模块都导入 hub）

from .ledger import (
    _earn_add,
    _earn_get,
    _point_adj_log_card,
    cmd_my_points,
    cmd_points_flow,
    cmd_points_rank,
)
from .bulkio import (
    _parse_points_rows,
)
from .sign import (
    _grant_newbie_reward,
    cmd_sign,
    cmd_sign_rank,
)
from .chat import (
    _award_chat_points,
)
from .levels import (
    _check_level_change,
    _check_level_drop_on_spend,
    _get_level,
    _level_allows,
    _level_base,
    _level_item,
    _level_msg_enforce,
    _level_msg_hit,
    _level_of,
    _level_perms,
    _level_rank,
    _level_sync_member_tag,
    _level_violation_hit,
    _normalize_levels,
    _set_level_enabled,
    cmd_my_level,
)
from .titles import (
    all_titles,
    cmd_equip,
    cmd_my_titles,
    cmd_redeem,
    cmd_shop,
    grant_title,
    revoke_title,
    title_icon,
    title_prefix,
)
from .shop import (
    _exchange_rate_text,
    _mall_dm_ok,
    _mall_items_on,
    _mall_panel,
    _mall_price,
    _redeem_dm_ok,
    _redeem_execute,
    _redeem_gate,
    _redeem_items_for,
    cmd_mall,
    cmd_mall_buy,
    cmd_points_redeem,
)
from .redpacket import (
    _rp_grab,
    cmd_redpacket,
)
from .transfer import (
    _buy_settle,
    cmd_buy_points,
    cmd_inherit,
)
from .deep import (
    _deep_buy_url,
    _deep_mall_start,
    _deep_redeem_start,
    _deep_start_confirm,
    _parse_dm_redeem_data,
)


__all__ = [
    "_award_chat_points",
    "_buy_settle",
    "_check_level_change",
    "_check_level_drop_on_spend",
    "_deep_buy_url",
    "_deep_mall_start",
    "_deep_redeem_start",
    "_deep_start_confirm",
    "_earn_add",
    "_earn_get",
    "_exchange_rate_text",
    "_get_level",
    "_grant_newbie_reward",
    "_level_allows",
    "_level_base",
    "_level_item",
    "_level_msg_enforce",
    "_level_msg_hit",
    "_level_of",
    "_level_perms",
    "_level_rank",
    "_level_sync_member_tag",
    "_level_violation_hit",
    "_mall_dm_ok",
    "_mall_items_on",
    "_mall_panel",
    "_mall_price",
    "_normalize_levels",
    "_parse_dm_redeem_data",
    "_parse_points_rows",
    "_point_adj_log_card",
    "_redeem_dm_ok",
    "_redeem_execute",
    "_redeem_gate",
    "_redeem_items_for",
    "_rp_grab",
    "_set_level_enabled",
    "all_titles",
    "cmd_buy_points",
    "cmd_equip",
    "cmd_inherit",
    "cmd_mall",
    "cmd_mall_buy",
    "cmd_my_level",
    "cmd_my_points",
    "cmd_my_titles",
    "cmd_points_flow",
    "cmd_points_rank",
    "cmd_points_redeem",
    "cmd_redeem",
    "cmd_redpacket",
    "cmd_shop",
    "cmd_sign",
    "cmd_sign_rank",
    "grant_title",
    "revoke_title",
    "title_icon",
    "title_prefix",
]
