# -*- coding: utf-8 -*-
"""按钮回调 handler —— 2026-09-13 从 core/entry.py 的 on_button 拆出。

只做搬运，未改任何逻辑。每个函数对应 on_button 里一个 `data` 命名空间分支；
外层保留 `if <原 test>: await <fn>(...); return`（分支体命中后必 return，故等价）。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
⚠️ 本包**禁止** import core.entry（循环导入）。
"""
from core import hub


from core.entry_buttons.points import (  # noqa: E402,F401
    _btn_deep_start_confirm, _btn_season_exchange, _btn_lottery_join, _btn_redeem_show,
    _btn_redeem_buy, _btn_redpacket_grab, _btn_buy_confirm, _btn_mall,
)
from core.entry_buttons.invite import (  # noqa: E402,F401
    _btn_invite_refresh_priv, _btn_invite_accept, _btn_join_verify, _btn_invite_refresh,
    _btn_invreport,
)
from core.entry_buttons.misc import (  # noqa: E402,F401
    _btn_fsub_recheck, _btn_noop,
)
from core.entry_buttons.games import (  # noqa: E402,F401
    _btn_blackjack, _btn_texas, _btn_dice, _btn_jinhua,
    _btn_horse_bet,
)
from core.entry_buttons.season import (  # noqa: E402,F401
    _btn_season,
)

__all__ = [
    "_btn_deep_start_confirm",
    "_btn_season_exchange",
    "_btn_lottery_join",
    "_btn_invite_refresh_priv",
    "_btn_invite_accept",
    "_btn_join_verify",
    "_btn_invite_refresh",
    "_btn_invreport",
    "_btn_fsub_recheck",
    "_btn_noop",
    "_btn_blackjack",
    "_btn_season",
    "_btn_texas",
    "_btn_dice",
    "_btn_jinhua",
    "_btn_redeem_show",
    "_btn_redeem_buy",
    "_btn_redpacket_grab",
    "_btn_buy_confirm",
    "_btn_mall",
    "_btn_horse_bet",
]
