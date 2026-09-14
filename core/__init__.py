# -*- coding: utf-8 -*-
"""core 包 —— bot.py 抽出的模块。

约定见 `core/hub.py`：
  · 抽出的模块一律通过 `core.hub` 访问 bot 的符号（禁止 `from bot import xxx`）
  · 不在模块顶层访问 hub（那时 bot.py 可能还没 bind）
"""
