# -*- coding: utf-8 -*-
"""cmd_restore（管理员 /restore 恢复）的 3 段自成一体的逻辑 —— 2026-09-13 从 core/data.py 拆出。

只做搬运，未改任何逻辑：
  · _restore_web_settings_only —— 设置专用备份（有 fields、无 game_chips）的恢复分支
  · _restore_embedded_settings  —— 数据恢复后，一并还原备份里内嵌的网页设置快照
  · _send_restore_summary       —— 恢复完成后回一条摘要（玩家数/积分总量/授权群/赛季）

⚠️ 调用顺序不能改（写盘 → 还原设置 → 发摘要），由 cmd_restore 里的调用次序保证。
依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
⚠️ 本包**禁止** import core.data（循环导入）。
"""
import asyncio
import os
from core import hub



async def _restore_web_settings_only(update, context, data, tmp_path, uid):
    _write_settings_file = hub._write_settings_file
    force_save_now = hub.force_save_now
    load_settings = hub.load_settings
    logger = hub.logger
    send_reply = hub.send_reply
    try:
        os.remove(tmp_path)
    except Exception:
        pass
    try:
        _write_settings_file(data.get("fields", {}),
                             data.get("web_password") or "",   # 新格式备份没有明文，空=不动凭据
                             data.get("cmd_aliases") or {}, data.get("tg_menu") or [])
        load_settings()
        await asyncio.to_thread(force_save_now)
        await send_reply(update, context, "\n".join([
            "✅ 网页设置恢复成功，已立即生效",
            "━━━━━━━━━━━━━━━",
            f"⚙️ 恢复设置项：{len(data.get('fields', {}))} 项",
            "",
            "本次只恢复设置，积分与数据未改动。",
        ]))
        logger.warning("管理员 %s 恢复了网页设置", uid)
    except Exception:
        logger.exception("设置恢复失败")
        await send_reply(update, context, "⚠️ 设置恢复失败，文件可能已损坏")


def _restore_embedded_settings(data):
    CMD_ALIAS_OVERRIDES = hub.CMD_ALIAS_OVERRIDES
    SETTINGS_SNAPSHOT = hub.SETTINGS_SNAPSHOT
    TG_MENU = hub.TG_MENU
    _write_settings_file = hub._write_settings_file
    apply_command_aliases = hub.apply_command_aliases
    apply_settings = hub.apply_settings
    logger = hub.logger
    # 一并还原网页设置：备份里内嵌了设置快照，恢复数据即恢复设置，
    # 避免重新部署后网页设置回退成代码默认值。
    try:
        embedded = data.get("_settings")
        if hub._looks_like_settings_snapshot(embedded):
            SETTINGS_SNAPSHOT.clear(); SETTINGS_SNAPSHOT.update(embedded)
            apply_settings(embedded.get("fields", {}), _record=False)   # 恢复数据：不是用户改设置，不记留痕
            ca = embedded.get("cmd_aliases") or {}
            if isinstance(ca, dict):
                CMD_ALIAS_OVERRIDES.clear()
                CMD_ALIAS_OVERRIDES.update({str(k): str(v) for k, v in ca.items()})
            tm = embedded.get("tg_menu") or []
            if isinstance(tm, list) and tm:
                TG_MENU.clear()
                TG_MENU.extend([list(x) for x in tm if isinstance(x, (list, tuple)) and len(x) == 2])
            apply_command_aliases()
            # ★ 表格化设置也要还原（P1-8）：
            #   _write_settings_file 是从**内存变量**现取表格的，前面只 apply 了
            #   fields（普通设置），内存里的表格还是恢复前的 —— 那样写出去的文件
            #   表格仍是旧的，备份里的等级表 / 商品表就**静默不生效**。
            #   所以在写文件之前，先把备份里的表格灌回内存变量。
            try:
                for _var, _key in (("POINT_LEVELS", "point_levels"),
                                   ("MALL_ITEMS", "mall_items"),
                                   ("redeem_goods", "redeem_goods"),
                                   ("buy_packages", "buy_packages"),
                                   ("chat_rules", "chat_rules")):
                    _tb = embedded.get(_key)
                    if not isinstance(_tb, list):
                        continue
                    _cur = hub.namespace().get(_var)
                    if isinstance(_cur, list):
                        _cur.clear(); _cur.extend(_tb)
                    else:
                        hub.set(_var, list(_tb))
                logger.warning("数据恢复：已还原表格化设置（%s）",
                               ", ".join(k for k in ("point_levels", "mall_items",
                                                     "redeem_goods", "buy_packages",
                                                     "chat_rules")
                                         if isinstance(embedded.get(k), list)) or "无")
            except Exception:
                logger.exception("数据恢复：还原表格化设置失败（其余设置仍已还原）")
            _write_settings_file(embedded.get("fields", {}),
                                 embedded.get("web_password") or "",   # 空=不动凭据，避免把密码打回默认
                                 embedded.get("cmd_aliases") or {}, embedded.get("tg_menu") or [])
            logger.warning("数据恢复：已一并还原网页设置（%s 项）", len(embedded.get("fields", {})))
    except Exception:
        logger.exception("数据恢复：设置还原失败")


async def _send_restore_summary(update, context):
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    game_chips = hub.game_chips
    logger = hub.logger
    season_active = hub.season_active
    season_name = hub.season_name
    send_reply = hub.send_reply
    # 恢复摘要：一眼确认恢复成没成功，不用再翻 /列表
    try:
        all_players = {u for users in list(game_chips.values()) for u in users}
        game_total = sum(sum(users.values()) for users in list(game_chips.values()))
        await send_reply(update, context, "\n".join([
            "✅ 数据恢复成功，已立即生效（无需重启）",
            "━━━━━━━━━━━━━━━",
            f"👥 玩家总数：{len(all_players)}",
            f"💰 积分总量：{game_total}",
            f"📋 授权群：{len(AUTHORIZED_GROUPS)}",
            f"🏆 赛季：{'进行中 · ' + (season_name or '未命名') if season_active else '未开启'}",
            "",
            "⚠️ 如有正在进行的牌局，请重新开局。",
        ]))
    except Exception:
        logger.exception("生成恢复摘要失败")
        await send_reply(update, context, "✅ 数据恢复成功，已立即生效（无需重启）")
