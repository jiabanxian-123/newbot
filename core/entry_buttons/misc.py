# -*- coding: utf-8 -*-
"""按钮回调 handler —— 2026-09-13 从 core/entry.py 的 on_button 拆出。

只做搬运，未改任何逻辑。每个函数对应 on_button 里一个 `data` 命名空间分支；
外层保留 `if <原 test>: await <fn>(...); return`（分支体命中后必 return，故等价）。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
⚠️ 本包**禁止** import core.entry（循环导入）。
"""
import time

from core import hub


async def _btn_noop(update, context, q, cid, uid, data):
    """占位按钮（售罄/页码等）：点了只应答、不报错。

    2026-09-14 从 on_button 的 `if data == "noop"` 抽成表里一行。
    """
    await q.answer()
    return


async def _btn_fsub_recheck(update, context, q, cid, uid, data):
    _fsub_ok_cache = hub._fsub_ok_cache
    _fsub_probe = hub._fsub_probe
    logger = hub.logger
    _fsub_ok_cache.get(cid, {}).pop(uid, None)
    try:
        _ok, _checked = await _fsub_probe(context, uid)
    except Exception:
        logger.exception("强制订阅复检异常（已吞并）")
        _ok, _checked = False, 0
    if _ok:
        # ⚠️ 原先这里是裸 `time.time()`，而本模块没有 import time →
        #    复检**成功**那条路一走就 NameError（2026-09-14 修）。
        _fsub_ok_cache[cid][uid] = (time.time(), True)
        try:
            await q.message.delete()
        except Exception:
            pass
        await q.answer("✅ 已确认订阅，现在可以正常发言啦")
    elif _checked == 0:
        await q.answer("⚠️ 机器人读不到该频道成员（需把机器人加入频道并设为管理员），已暂时放行", show_alert=True)
    else:
        await q.answer("❌ 还没检测到订阅，请先点上面的按钮加入频道", show_alert=True)
    return
