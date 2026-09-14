# -*- coding: utf-8 -*-
"""core.entry_text —— entry 域的**文本交互**处理（on_text 的子模块）。

2026-09-13 新建：on_text 里那 105 行的「下注 / 游戏文本」尾段单独成文件。

抽缝铁律：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数体里用前导别名 `名 = hub.名` 访问 bot 全局量（每次调用查表 ⇒ 测试补丁实时穿透）
  - 子包 ⇒ test_seam_hub 的模块发现（glob(core/*.py)）不递归本目录，无需 bot.py re-export
"""
from core.entry_text.bets import text_game_bets  # noqa: E402,F401

__all__ = ["text_game_bets"]
