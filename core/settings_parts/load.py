# -*- coding: utf-8 -*-
"""load_settings 的「设置套用流水线」分段实现 —— 2026-09-13 从 core/settings.py 拆出。

只做搬运，未改任何逻辑。签名统一 `(payload, origin)`：
  payload —— 待套用的设置字典（可能来自设置文件，也可能来自数据内嵌快照）
  origin  —— "file" / "data"，只有回写设置文件那一段真的用到，其余段忽略

⚠️ payload 的归一化（`payload = payload["_settings"]` 那次**重新绑定**）留在 load_settings 壳里，
   不搬 —— 搬进函数就传不回来。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
⚠️ 本包**禁止** import core.settings（循环导入）。
"""
from core import hub



def _apply_fields(payload, origin):
    _migrate_level_demote_switch = hub._migrate_level_demote_switch
    apply_settings = hub.apply_settings
    # 2026-09-13 一次性迁移：等级口径开关改名+取反（旧键 level_allow_demote → 新键）。
    # 原地改 payload（而不是只改 apply_settings 的入参），下面 origin=="data" 的回写才会写出新键。
    _flds = payload.get("fields")
    if isinstance(_flds, dict):
        payload["fields"] = _migrate_level_demote_switch(_flds)
    apply_settings(payload.get("fields", {}), _record=False)   # 启动加载：不是用户改设置，不记留痕


def _apply_web_credentials(payload, origin):
    # 密码：优先读 hash（新格式）；旧存档是明文则临时保留，下次写盘自动升级为 hash
    h = str(payload.get("web_password_hash", "")).strip()
    s = str(payload.get("web_password_salt", "")).strip()
    if h and s:
        hub.set_many(("_web_password_hash", "_web_salt"), (h, s))
        hub.set("_web_password", "")   # 内存不再保留明文（hash 无法反推，登录走 _pwd_ok 的 hash 分支）
    pwd = str(payload.get("web_password", "")).strip()
    if pwd:
        hub.set("_web_password", pwd)


def _apply_cmd_aliases_menu(payload, origin):
    CMD_ALIAS_OVERRIDES = hub.CMD_ALIAS_OVERRIDES
    TG_MENU = hub.TG_MENU
    _migrate_rank_alias_str = hub._migrate_rank_alias_str
    _migrate_rank_rename = hub._migrate_rank_rename
    apply_command_aliases = hub.apply_command_aliases
    # 命令管理：别名覆盖层 + Telegram / 菜单
    # 2026-09-13 一次性迁移：历史存档里的「排位」统一成「赛季」（幂等）。
    # 覆盖层是**替换**语义，不迁移则旧别名会在启动后把代码里的新名字盖回去。
    ca = payload.get("cmd_aliases") or {}
    if isinstance(ca, dict):
        ca = {str(k): _migrate_rank_alias_str(str(v)) for k, v in ca.items()}
        payload["cmd_aliases"] = ca
        CMD_ALIAS_OVERRIDES.clear()
        CMD_ALIAS_OVERRIDES.update(ca)
    tm = payload.get("tg_menu") or []
    if isinstance(tm, list) and tm:
        cleaned = [list(x) for x in tm if isinstance(x, (list, tuple)) and len(x) == 2]
        cleaned = [[_c, _migrate_rank_rename(_d)] for _c, _d in cleaned]
        if cleaned:
            payload["tg_menu"] = cleaned
            TG_MENU.clear(); TG_MENU.extend(cleaned)
    apply_command_aliases()


def _apply_snapshot_and_layout(payload, origin):
    MALL_ITEMS = hub.MALL_ITEMS
    POINT_LEVELS = hub.POINT_LEVELS
    SETTINGS_SNAPSHOT = hub.SETTINGS_SNAPSHOT
    SIDEBAR_ORDER = hub.SIDEBAR_ORDER
    _normalize_levels = hub._normalize_levels
    buy_packages = hub.buy_packages
    chat_rules = hub.chat_rules
    redeem_goods = hub.redeem_goods
    SETTINGS_SNAPSHOT.clear(); SETTINGS_SNAPSHOT.update(payload)
    so = payload.get("sidebar_order") or []
    if isinstance(so, list):
        SIDEBAR_ORDER.clear(); SIDEBAR_ORDER.extend(str(x) for x in so)
    # 表格化数据：聊天积分规则 / 购买积分套餐
    for key, gl in (("chat_rules", chat_rules), ("buy_packages", buy_packages),
                    ("point_levels", POINT_LEVELS), ("mall_items", MALL_ITEMS),
                    ("redeem_goods", redeem_goods)):
        v = payload.get(key)
        if isinstance(v, list):
            gl.clear(); gl.extend(x for x in v if isinstance(x, dict))
    _normalize_levels()   # 旧存档等级表补 perms/on（幂等）


def _apply_group_settings(payload, origin):
    GROUP_SETTINGS = hub.GROUP_SETTINGS
    _migrate_level_demote_switch = hub._migrate_level_demote_switch
    _migrate_stale_lottery_tpl = hub._migrate_stale_lottery_tpl
    backup_admins = hub.backup_admins
    daily_reset_groups = hub.daily_reset_groups
    leaderboard_groups = hub.leaderboard_groups
    logger = hub.logger
    # 群级覆盖：{群ID: {设置键: 值}}。旧存档没有这个键 → 不动内存（避免把已恢复的覆盖清掉）
    if "group_settings" in payload:
        _gs = payload.get("group_settings")
        GROUP_SETTINGS.clear()
        if isinstance(_gs, dict):
            for _cid, _rec in _gs.items():
                try:
                    _c = int(_cid)
                except (TypeError, ValueError):
                    continue
                if isinstance(_rec, dict) and _rec:
                    _r = {str(k): v for k, v in _rec.items()}
                    # 群级覆盖里也可能存着旧版内置模板 → 同样升级（自定义的不动）
                    _migrate_stale_lottery_tpl(_r)
                    # 群级覆盖里存的旧等级开关 → 同名迁移+取反（否则该群覆盖静默失效）
                    _migrate_level_demote_switch(_r)
                    GROUP_SETTINGS[_c] = _r
        logger.info("群级覆盖已恢复：%d 个群", len(GROUP_SETTINGS))
    # 4 个调度任务的作用对象
    st = payload.get("schedule_targets") or {}
    if isinstance(st, dict):
        try: daily_reset_groups.update(int(x) for x in st.get("daily_reset_groups", []) if str(x).lstrip("-").isdigit())
        except Exception: pass
        try: leaderboard_groups.update(int(x) for x in st.get("leaderboard_groups", []) if str(x).lstrip("-").isdigit())
        except Exception: pass
        try: backup_admins.update(int(x) for x in st.get("backup_admins", []) if str(x).lstrip("-").isdigit())
        except Exception: pass


def _apply_persist_back(payload, origin):
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    _write_settings_file = hub._write_settings_file
    backup_admins = hub.backup_admins
    daily_reset_groups = hub.daily_reset_groups
    leaderboard_groups = hub.leaderboard_groups
    logger = hub.logger
    # 懒填默认：空集合 = 默认全授权群/默认管理员
    # 注意：从数据侧还原时，只有「真的保存过设置」才懒填 —— 判据是快照里有没有 fields。
    # 没有 fields 说明这台机器人从没保存过设置（快照里只剩代码默认的表格数据），
    # 此时必须保持和「无设置可加载」完全一样的行为，否则全新部署会凭空多出
    # 一份设置文件、并把调度默认值提前塞进内存（改的是启动行为，不是数据）。
    _saved_before = (origin == "file") or (payload.get("fields") is not None)
    if _saved_before:
        if not daily_reset_groups: daily_reset_groups.update(AUTHORIZED_GROUPS)
        if not leaderboard_groups: leaderboard_groups.update(AUTHORIZED_GROUPS)
        if not backup_admins: backup_admins.add(ADMIN_USER_ID)
    # 从数据还原的：立刻回写设置文件，保证网页端与后续保存读到一致内容
    if origin == "data" and _saved_before:
        _write_settings_file(payload.get("fields", {}), hub._web_password,
                             payload.get("cmd_aliases") or {}, payload.get("tg_menu") or [])
    logger.info("设置已加载（来源：%s）", "设置文件" if origin == "file" else "数据内嵌快照")
