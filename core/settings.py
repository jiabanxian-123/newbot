# -*- coding: utf-8 -*-
"""infra/settings —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

from core.settings_parts import (  # noqa: E402
    _apply_numeric_fields,
    _apply_bool_fields,
    _apply_multi_fields,
    _apply_levels_items,
    _apply_text_fields,
    _apply_horse_trio,
    _apply_lists_fields,
    _apply_fields,
    _apply_web_credentials,
    _apply_cmd_aliases_menu,
    _apply_snapshot_and_layout,
    _apply_group_settings,
    _apply_persist_back,
)

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from contextlib import asynccontextmanager
import json
import os
import re
import secrets
import time

def group_get(cid, key, default=None):
    """读设置项：该群有覆盖用覆盖值，否则用全局默认。

    cid 为 0/None/非法 → 直接走全局（全局页与所有旧调用点零影响）。
    key 不在 GROUP_SCOPED_KEYS 里 → 也走全局（不支持的键不参与群级覆盖）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    GROUP_SCOPED_KEYS = hub.GROUP_SCOPED_KEYS
    GROUP_SETTINGS = hub.GROUP_SETTINGS
    _KEY2VAR = hub._KEY2VAR
    gname = _KEY2VAR.get(key)
    if gname is None:
        return default
    try:
        cid = int(cid or 0)
    except (TypeError, ValueError):
        cid = 0
    if cid and key in GROUP_SCOPED_KEYS:
        rec = GROUP_SETTINGS.get(cid) or {}
        if key in rec:
            return rec[key]
    return hub.namespace().get(gname, default)


def group_set(cid, key, value):
    """写群级覆盖。value=None 表示**清除覆盖**（回继承全局）。返回 True=写入成功。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    GROUP_SCOPED_KEYS = hub.GROUP_SCOPED_KEYS
    GROUP_SETTINGS = hub.GROUP_SETTINGS
    _KEY2VAR = hub._KEY2VAR
    gname = _KEY2VAR.get(key)
    if gname is None:
        return False
    try:
        cid = int(cid or 0)
    except (TypeError, ValueError):
        return False
    if not cid or key not in GROUP_SCOPED_KEYS:
        return False
    # 留痕用：改之前该群有没有覆盖、覆盖值是什么
    _rec0 = GROUP_SETTINGS.get(cid) or {}
    _had, _old = (key in _rec0), _rec0.get(key)
    if value is None:
        if not _had:
            return True          # 本来就没覆盖 → 不算一次改动（重复清除不记）
        rec = GROUP_SETTINGS.get(cid)
        if rec:
            rec.pop(key, None)
            if not rec:
                GROUP_SETTINGS.pop(cid, None)
        hub._record_setting_change(cid, key, _old, "（继承全局）")
        return True
    if _had and str(_old) == str(value):
        return True              # 值没变 → 不记（网页整表提交不能把日报刷屏）
    GROUP_SETTINGS.setdefault(cid, {})[key] = value
    hub._record_setting_change(cid, key, _old if _had else "（继承全局）", value)
    return True


def group_effective(cid, key, default=None):   # wiring-ok: 群级覆盖基础设施，暂未接线（待清理）
    """该群当前生效值 + 是否来自群级覆盖。返回 (值, 是否覆盖)。网页回显用。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    GROUP_SCOPED_KEYS = hub.GROUP_SCOPED_KEYS
    GROUP_SETTINGS = hub.GROUP_SETTINGS
    group_get = hub.group_get
    try:
        cid = int(cid or 0)
    except (TypeError, ValueError):
        cid = 0
    if cid and key in GROUP_SCOPED_KEYS and key in (GROUP_SETTINGS.get(cid) or {}):
        return GROUP_SETTINGS[cid][key], True
    return group_get(cid, key, default), False


def cur_cid():
    """当前上下文群 ID（0=无群上下文，走全局默认）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _CUR_CID = hub._CUR_CID
    try:
        return int(_CUR_CID.get() or 0)
    except (TypeError, ValueError):
        return 0


@asynccontextmanager
async def group_ctx_async(cid):   # wiring-ok: 群级覆盖基础设施，暂未接线（待清理）
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _CUR_CID = hub._CUR_CID
    _safe_cid = hub._safe_cid
    tok = _CUR_CID.set(_safe_cid(cid))
    try: yield
    finally: _CUR_CID.reset(tok)


class group_ctx:
    """同步上下文管理器：with group_ctx(cid): ... 期间 sget() 按该群解析。

    用于定时任务按群循环、网页请求渲染等**同步**场景。
    """
    __slots__ = ("_cid", "_tok")

    def __init__(self, cid):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        _safe_cid = hub._safe_cid
        self._cid = hub._safe_cid(cid)

    def __enter__(self):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        _CUR_CID = hub._CUR_CID
        self._tok = hub._CUR_CID.set(self._cid)
        return self._cid

    def __exit__(self, *exc):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
        _CUR_CID = hub._CUR_CID
        hub._CUR_CID.reset(self._tok)
        return False


def _safe_cid(cid):
    try:
        return int(cid or 0)
    except (TypeError, ValueError):
        return 0


_SGET_UNKNOWN_WARNED = set()   # sget 读不到的名字中，已经警告过的（同一名字只警告一次，防刷屏）


def _warn_unknown_setting(name):
    """设置项名字读不到 → 打一行警告（产品口径：**只警告，不拦启动**）。

    挡住的是这一类静默失效：名字拼错一个字母 / 被改名之后，`sget()` 悄悄返回
    None，对应功能就不工作了，而日志里**什么都没有** —— 用户只会觉得
    「我改了怎么没生效」。这是「改了没生效」的头号成因。

    为什么要去重：`sget()` 在热路径上（群消息、游戏轮询、定时任务都会调），
    不去重的话一个拼错的名字会把日志刷爆。
    """
    if name in _SGET_UNKNOWN_WARNED:
        return
    _SGET_UNKNOWN_WARNED.add(name)
    hub.logger.warning(
        "设置项 %s 不存在（sget 读不到，已返回默认值）。"
        "多半是名字拼错或被改名了 —— 对应功能会静默失效，请检查。", name)


def sget(name, default=None):
    """读设置项（**业务代码统一入口**）：name 是模块全局变量名，如 sget("BJ_MIN_BET")。

    解析顺序：当前群覆盖 → 全局默认。cid 来自 contextvars（handler/定时任务/网页请求会设）。
    GROUP_SETTINGS 为空或该群没覆盖该键时，返回值与直接读全局变量完全一致。

    name 在 bot 命名空间里根本不存在时（拼错 / 被改名）→ 记一行 WARNING，
    但仍然返回 default：**只警告、不拦启动**，且同一名字只警告一次。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    GROUP_SETTINGS = hub.GROUP_SETTINGS
    _MISS = hub._MISS
    _VAR2KEY = hub._VAR2KEY
    cur_cid = hub.cur_cid
    cid = cur_cid()
    if cid:
        key = _VAR2KEY.get(name)
        if key is not None:
            rec = GROUP_SETTINGS.get(cid)
            if rec:
                v = rec.get(key, _MISS)
                if v is not _MISS:
                    return v
    ns = hub.namespace()
    if name not in ns:
        _warn_unknown_setting(name)
    return ns.get(name, default)


def sget_key(key, default=None):   # wiring-ok: 按 settings 键读的便捷包装，暂未接线（待清理）
    """按 settings 键读（等价 sget，键名更顺手时用）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _KEY2VAR = hub._KEY2VAR
    sget = hub.sget
    gname = _KEY2VAR.get(key)
    if gname is None:
        return default
    return sget(gname, default)


def _bind_update_cid(update):
    """把「这条更新属于哪个群」写进上下文，让整条调用链的 sget() 自动解析群级覆盖。

    7 个更新入口（命令/回调/文本/媒体/成员变动/入群消息/入群申请）第一行调用。
    私聊、频道、无 chat 的更新 → cid=0 → 全部走全局默认（与改前一致）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _CUR_CID = hub._CUR_CID
    cur_cid = hub.cur_cid
    try:
        chat = update.effective_chat
        _CUR_CID.set(int(chat.id) if chat is not None else 0)
    except Exception:
        _CUR_CID.set(0)
    return cur_cid()


def _group_settings_json():
    """群级覆盖的落盘形式（json 的键必须是字符串，读回时再转 int）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    GROUP_SETTINGS = hub.GROUP_SETTINGS
    return {str(cid): dict(rec) for cid, rec in GROUP_SETTINGS.items() if rec}


def _normalize_settings_values(cfg: dict):
    """按 apply_settings 的规则算出"标准值"，但**不改动**内存里的全局设置。

    用途：群级保存要拿"标准值"跟全局默认比对，才能只存差异。
    做法：先快照所有设置全局变量 → 调 apply_settings 归一化 → 立刻还原。
    只传入群级键（levels/items/cmd 已在 GROUP_SCOPED_KEYS 里排除），
    因此 _normalize_levels() 之类会原地改列表的分支不会被触发。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    GROUP_SCOPED_KEYS = hub.GROUP_SCOPED_KEYS
    SETTINGS_FIELDS = hub.SETTINGS_FIELDS
    apply_settings = hub.apply_settings
    sub = {k: v for k, v in cfg.items() if k in GROUP_SCOPED_KEYS}
    if not sub:
        return {}
    snap = {}
    for _f in SETTINGS_FIELDS:
        _g = _f[1]
        if _g and _g not in snap:
            snap[_g] = hub.namespace().get(_g)
    try:
        return apply_settings(sub, _record=False)   # 只读探测：快照→套用→还原，不是用户改设置
    finally:
        for _g, _v in snap.items():
            hub.namespace()[_g] = _v


def _write_settings_file(cfg: dict, password: str, cmd_aliases=None, tg_menu=None, sidebar_order=None):
    """写设置文件 + 同步到数据快照。

    安全红线：密码**只存 hash**，明文绝不落盘——否则它会随 bot_data.json 的自动备份
    发到每个备份接收人手里，等于把后台口令交给普通管理员。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    MALL_ITEMS = hub.MALL_ITEMS
    POINT_LEVELS = hub.POINT_LEVELS
    SETTINGS_FILE = hub.SETTINGS_FILE
    SETTINGS_SNAPSHOT = hub.SETTINGS_SNAPSHOT
    _group_settings_json = hub._group_settings_json
    _hash_web_pwd = hub._hash_web_pwd
    _migrate_level_demote_switch = hub._migrate_level_demote_switch
    _migrate_rank_alias_str = hub._migrate_rank_alias_str
    _migrate_rank_rename = hub._migrate_rank_rename
    backup_admins = hub.backup_admins
    buy_packages = hub.buy_packages
    chat_rules = hub.chat_rules
    daily_reset_groups = hub.daily_reset_groups
    leaderboard_groups = hub.leaderboard_groups
    logger = hub.logger
    redeem_goods = hub.redeem_goods
    save_data = hub.save_data

    if sidebar_order is None:
        sidebar_order = list(SETTINGS_SNAPSHOT.get("sidebar_order") or [])
    if password:
        try:
            hub.set("_web_salt", secrets.token_hex(16))
            hub.set("_web_password_hash", _hash_web_pwd(password, hub._web_salt))
            hub.set("_web_password", password)   # 仅内存，供本次运行期的明文分支比对
        except Exception:
            logger.exception("密码摘要计算失败（保持原凭据不变）")
    if not hub._web_password_hash:
        # 内存还没凭据（如恢复流程早于 load_settings）：从现有文件继承，绝不写空凭据把人锁在门外
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                r0 = json.load(f)
            h0 = str(r0.get("web_password_hash", "")).strip()
            s0 = str(r0.get("web_password_salt", "")).strip()
            if h0 and s0:
                hub.set_many(("_web_password_hash", "_web_salt"), (h0, s0))
            elif not password and str(r0.get("web_password", "")).strip():
                hub.set("_web_password", str(r0.get("web_password", "")).strip())
        except Exception:
            pass
    # 2026-09-13 一次性迁移：写盘前把「排位」统一成「赛季」，让历史存档自然收敛
    # （否则保存设置时会把文件里读到的旧别名原样写回，迁移永远不生效）
    if cmd_aliases:
        cmd_aliases = {str(k): _migrate_rank_alias_str(str(v)) for k, v in cmd_aliases.items()}
    if tg_menu:
        tg_menu = [[_c, _migrate_rank_rename(_d)] for _c, _d in tg_menu]
    # 同级迁移：写盘前把旧等级开关也换掉。不改这里的话，保存设置会把文件里读到的
    # 旧键原样写回，迁移永远收敛不了（与上面「排位→赛季」同一个坑）。
    if isinstance(cfg, dict):
        cfg = _migrate_level_demote_switch(dict(cfg))
    _gs = _group_settings_json()
    for _r in _gs.values():
        _migrate_level_demote_switch(_r)
    payload = {"fields": cfg, "web_password_hash": hub._web_password_hash,
               "web_password_salt": hub._web_salt,
               "cmd_aliases": cmd_aliases or {}, "tg_menu": tg_menu or [],
               "sidebar_order": sidebar_order,
               # 群级覆盖：{群ID: {设置键: 值}}，只存与该群"全局默认"不同的项
               "group_settings": _gs,
               "chat_rules": list(chat_rules), "buy_packages": list(buy_packages),
               "point_levels": list(POINT_LEVELS), "mall_items": list(MALL_ITEMS),
               "redeem_goods": list(redeem_goods),
               # 4 个调度任务的作用对象（json 不支持 set，存 list）
               "schedule_targets": {
                   "daily_reset_groups": sorted(daily_reset_groups),
                   "leaderboard_groups": sorted(leaderboard_groups),
                   "backup_admins": sorted(backup_admins),
               }}
    try:
        tmp = f"{SETTINGS_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except Exception:
        logger.exception("设置文件写盘失败")
    # 同步进快照：随 bot_data.json 一起落盘与备份，容器重建后可从数据文件还原设置
    try:
        SETTINGS_SNAPSHOT.clear()
        SETTINGS_SNAPSHOT.update(payload)
        save_data()
    except Exception:
        logger.exception("设置快照同步失败")


def _fn_legit_aliases(fn):
    """某命令当前**应当**生效的别名集合：有覆盖=覆盖表里的全部；无覆盖=出厂别名。

    与 apply_command_aliases 的语义严格一致（覆盖即替换）。
    用途：判断「上一轮的动态指令名」是否还属于合法别名——是就别删。
    """
    overrides = hub.namespace().get("CMD_ALIAS_OVERRIDES") or {}
    base = hub.namespace().get("BASE_CMD_ALIASES") or {}
    override = overrides.get(fn.__name__)
    if override is not None:
        names = [a.strip() for a in str(override).replace("，", ",").split(",") if a.strip()]
        if names:
            return set(names)
    return {a for a, f in base.items() if f is fn}


def _sync_dyn_aliases():
    """把网页自定义的指令名（查询积分/签到/积分排行）注册进命令分发表；旧名随之失效。

    修复（2026-09-09 用户报告）：此前无条件 pop 掉「上一轮动态名」，而 SIGN_CMD 取的是
    覆盖表的**第一个**别名，于是把用户显式写在覆盖表里的其它别名一起删了——
    典型表现：签到页填「每日签到,签到」时「签到」失效，把顺序换成「签到,每日签到」才好。
    现在只移除「既不在出厂别名、也不在覆盖表」的陈旧动态名，用户配置的别名一律保留。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    _DYN_CMD_OWNED = hub._DYN_CMD_OWNED
    _fn_legit_aliases = hub._fn_legit_aliases
    cmd_invite_link = hub.cmd_invite_link
    cmd_invite_rank_all = hub.cmd_invite_rank_all
    cmd_invite_rank_month = hub.cmd_invite_rank_month
    cmd_invite_rank_today = hub.cmd_invite_rank_today
    cmd_my_level = hub.cmd_my_level
    cmd_my_points = hub.cmd_my_points
    cmd_points_rank = hub.cmd_points_rank
    cmd_points_redeem = hub.cmd_points_redeem
    cmd_sign = hub.cmd_sign
    aliases = hub.namespace().get("CMD_ALIASES")
    if aliases is None:
        return
    for gname, fn in (("QUERY_CMD", cmd_my_points), ("SIGN_CMD", cmd_sign), ("RANK_CMD", cmd_points_rank), ("LEVEL_CMD", cmd_my_level), ("REDEEM_CMD", cmd_points_redeem),
                      ("INVITE_LINK_CMD", cmd_invite_link), ("INVITE_RANK_TODAY_CMD", cmd_invite_rank_today),
                      ("INVITE_RANK_MONTH_CMD", cmd_invite_rank_month), ("INVITE_RANK_ALL_CMD", cmd_invite_rank_all)):
        old = _DYN_CMD_OWNED.get(gname)
        if old and old not in _fn_legit_aliases(fn) and aliases.get(old) is fn:
            aliases.pop(old, None)
        name = hub.namespace().get(gname)
        if name:
            aliases[name] = fn
            _DYN_CMD_OWNED[gname] = name


def _cross_keys(group):
    """跨分组聚合页（话术库等）可编辑的字段集合；普通分组返回 None（维持按组过滤）。

    话术模板散落在 11 个分组里，以前改一句欢迎语要先想清楚它在哪个菜单。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    MOD_PAGE_FIELDS = hub.MOD_PAGE_FIELDS
    SETTINGS_FIELDS = hub.SETTINGS_FIELDS
    if group == "tpls":
        return {k for k, _g, _l, t, _lo, _hi, _grp in SETTINGS_FIELDS if t == "text"}
    if group == "mod":
        return ({k for k, _g, _l, _t, _lo, _hi, grp in SETTINGS_FIELDS if grp == "mod"}
                | set(MOD_PAGE_FIELDS))
    return None


def _grp_title(grp):
    """把分组键（points/sign）翻成人类可读标题（积分系统 · 每日签到）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    SETTINGS_GROUPS = hub.SETTINGS_GROUPS
    SUBPAGES = hub.SUBPAGES
    base, _, sub = str(grp).partition("/")
    gnames = {k: n for k, n, _i in SETTINGS_GROUPS}
    name = gnames.get(base, base)
    if sub:
        subname = dict(SUBPAGES.get(base, [])).get(sub, sub)
        name = f"{name} · {subname}"
    return name


def _multi_set(raw, key):
    """把多选字段的原始值（list 或逗号分隔字符串）规整成逗号分隔字符串，只保留合法选项。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    MULTI_OPTIONS = hub.MULTI_OPTIONS
    allowed = [v for v, _l in MULTI_OPTIONS.get(key, [])]
    if isinstance(raw, (list, tuple)):
        parts = [str(p).strip() for p in raw]
    else:
        parts = [p.strip() for p in re.split(r"[,，]", str(raw)) if p.strip()]
    return ",".join([p for p in parts if p in allowed])


def _multi_has(raw, opt):
    """判断多选字符串里是否含某项（自动删除规则判定用）。"""
    return opt in {p.strip() for p in str(raw or "").split(",") if p.strip()}


def _migrate_legacy_autodel(cfg: dict):
    """旧版「每类型一个开关」的自动删除配置 → 新版两个多选。

    老的 bot_settings.json 里还是 autodel_photo / autodel_links / autodel_service_seconds
    这类键，直接套用会全部丢失（用户已开的开关莫名关掉），所以先折算成新字段。
    只有新版字段没给值时才迁移，避免覆盖用户刚在网页上的新选择。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    MULTI_OPTIONS = hub.MULTI_OPTIONS
    legacy_media = {
        "autodel_photo": "photo", "autodel_video": "video", "autodel_sticker": "sticker",
        "autodel_gif": "gif", "autodel_voice": "voice", "autodel_document": "document",
        "autodel_archive": "archive", "autodel_executable": "executable",
        "autodel_contact": "contact", "autodel_service": "service",
    }
    legacy_text = {"autodel_links": "link", "autodel_long_enabled": "long",
                   "autodel_premium_emoji": "premium_emoji"}
    def _on(v):
        return str(v).strip().lower() in ("1", "on", "true", "yes", "是")
    if any(k in cfg for k in legacy_media) and "autodel_media_types" not in cfg:
        picked = [opt for k, opt in legacy_media.items() if _on(cfg.get(k, 0))]
        cfg["autodel_media_types"] = ",".join([o for o, _l in MULTI_OPTIONS["autodel_media_types"]
                                               if o in picked])
    if any(k in cfg for k in legacy_text) and "autodel_text_rules" not in cfg:
        picked = [opt for k, opt in legacy_text.items() if _on(cfg.get(k, 0))]
        cfg["autodel_text_rules"] = ",".join([o for o, _l in MULTI_OPTIONS["autodel_text_rules"]
                                              if o in picked])
    if "autodel_service_seconds" in cfg and "autodel_media_seconds" not in cfg:
        try:
            cfg["autodel_media_seconds"] = int(float(cfg["autodel_service_seconds"]))
        except (ValueError, TypeError):
            pass
    return cfg


def _record_setting_change(cid, key, old, new):
    """记一条设置改动（留痕 → 经营日报「昨天哪些设置被改过」）。

    两条上限，防止存档被撑爆：
      · 单个值截断到 60 字（等级表 / 商品表这类大结构动辄几千字符）
      · 只留最近 300 条（超了从最旧的丢）
    返回写入的那条记录。
    """
    def _cut(v):
        s = "" if v is None else str(v)
        return s if len(s) <= 60 else s[:59] + "\u2026"
    entry = {"ts": time.strftime("%Y-%m-%d %H:%M"), "cid": int(cid or 0),
             "key": str(key), "old": _cut(old), "new": _cut(new)}
    lst = hub.settings_changes
    lst.append(entry)
    if len(lst) > 300:
        del lst[:-300]
    return entry


def apply_settings(cfg: dict, _record: bool = True):
    """把设置字典套用到内存全局常量（带类型与范围校验，非法值跳过）。

    数字字段先套用（HORSE_COUNT 先生效，名称/表情才好做数量联动校验）；
    names/emoji 要求拆分后条数 == 当前 HORSE_COUNT，否则整条跳过；
    bets 要求 1~6 个 1~100000 的正整数，自动去重升序。
    multi 为多选项，只保留 MULTI_OPTIONS 里登记的合法值，存为逗号分隔字符串。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    SETTINGS_FIELDS = hub.SETTINGS_FIELDS
    _migrate_legacy_autodel = hub._migrate_legacy_autodel
    _migrate_stale_lottery_tpl = hub._migrate_stale_lottery_tpl
    _multi_set = hub._multi_set
    _normalize_levels = hub._normalize_levels
    _sync_dyn_aliases = hub._sync_dyn_aliases
    logger = hub.logger
    cfg = _migrate_legacy_autodel(dict(cfg))
    if _migrate_stale_lottery_tpl(cfg):
        logger.info("抽奖模板已从旧版内置默认升级为阿福版式（自定义模板不在此列）")
    # 留痕用：套用**之前**的旧值。必须在所有 _apply_* 之前取（它们会原地改全局）。
    # _record=False 用于「加载 / 恢复 / 归一化探测」——那些不是用户改设置，
    # 记了会在每次重启刷出几百条假记录。
    _before = {}
    if _record:
        _k2v = hub._KEY2VAR
        _ns0 = hub.namespace()
        for _k in cfg:
            _gn = _k2v.get(_k)
            if _gn:
                _before[_k] = _ns0.get(_gn)
    applied = {}
    _apply_numeric_fields(cfg, applied)
    _apply_bool_fields(cfg, applied)
    _apply_multi_fields(cfg, applied)
    _apply_levels_items(cfg, applied)
    _apply_text_fields(cfg, applied)
    _sync_dyn_aliases()
    _apply_horse_trio(cfg, applied)
    _apply_lists_fields(cfg, applied)
    # 留痕：只记**真的变了**的（网页是整表提交，全记会把日报刷屏）
    if _record:
        for _k, _new in applied.items():
            _old = _before.get(_k)
            if str(_old) != str(_new):
                hub._record_setting_change(0, _k, _old, _new)
    return applied


_SETTINGS_SNAPSHOT_KEYS = ("fields", "chat_rules", "buy_packages", "point_levels",
                           "mall_items", "redeem_goods", "cmd_aliases", "tg_menu")


def _looks_like_settings_snapshot(d):
    """判断一份内嵌快照是不是真的「设置快照」（而不是别的什么字典）。

    注意：不能只看有没有 "fields"。save_data() 在「从未成功加载过设置文件」时
    写出的 _settings 只有表格数据（chat_rules / point_levels / redeem_goods ...），
    没有 fields —— 但它依然是需要还原的设置。旧判据只看 fields 会把它整份丢掉，
    导致等级表 / 商品表 / 兑换商品静默回退成代码默认值（不报错、只丢数据）。
    """
    if not isinstance(d, dict) or not d:
        return False
    return any(k in d for k in _SETTINGS_SNAPSHOT_KEYS)


def _load_settings_payload():
    """依次尝试：bot_settings.json → 分片里的 _settings → 旧 bot_data.json 内嵌快照。

    容器重建会清空 bot_settings.json，所以要从数据侧还原 _settings 键，
    让重新部署后的设置保持原样。
    Q9 之后数据拆成 6 个分片，_settings 落在 groups.json 里 —— 必须先查分片，
    否则「容器重建后设置自动还原」会静默失效（表现为设置莫名回退成默认值）。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    DATA_BACKUP_FILE = hub.DATA_BACKUP_FILE
    DATA_FILE = hub.DATA_FILE
    SETTINGS_FILE = hub.SETTINGS_FILE
    logger = hub.logger
    load_parts = hub.load_parts
    _looks_like_settings_snapshot = hub._looks_like_settings_snapshot
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f), "file"
        except Exception:
            logger.exception("设置文件读取失败，尝试从数据文件还原")
    # Q9：分片里的 _settings 快照
    try:
        _parts = load_parts()
        if _parts:
            _embedded = _parts.get("_settings")
            if _looks_like_settings_snapshot(_embedded):
                logger.warning("设置文件缺失，已从分片数据的内嵌快照还原设置")
                return _embedded, "data"
    except Exception:
        logger.exception("分片设置快照读取失败，回退旧单文件")
    for src in (DATA_FILE, DATA_BACKUP_FILE):
        if not os.path.exists(src):
            continue
        try:
            with open(src, "r", encoding="utf-8") as f:
                embedded = json.load(f).get("_settings")
            if _looks_like_settings_snapshot(embedded):
                logger.warning("设置文件缺失，已从 %s 内嵌快照还原设置", src)
                return embedded, "data"
        except Exception:
            continue
    return None, ""


_RANK_RENAME_RULES = (("排位分", "赛季分"), ("排位赛", "赛季"), ("排位", "赛季"))

# 等级口径开关改名（2026-09-13）：旧键反向、新键正向，迁移时取值取反
_LEVEL_DEMOTE_OLD_KEY = "level_allow_demote"    # 1=积分不足允许降级（旧）
_LEVEL_DEMOTE_NEW_KEY = "level_keep_on_spend"   # 1=消费不掉级（新，默认）


def _migrate_rank_rename(text):
    """一次性迁移：把历史设置里存的「排位」字样统一成「赛季」。

    2026-09-13 Q4=C：排位分 → 赛季分、排位赛 → 赛季、/排位 → /赛季。
    设置文件里存着命令别名覆盖层（cmd_aliases）与 Telegram 菜单（tg_menu），
    它们是**覆盖**语义 —— 不迁移的话，线上启动后旧别名「排位」会把代码里的
    新名字盖掉，改了代码也白改。幂等：跑多次结果一致。
    """
    if not text or "排位" not in str(text):
        return text
    for _a, _b in _RANK_RENAME_RULES:
        text = text.replace(_a, _b)
    return text


def _migrate_rank_alias_str(alias_str):
    """同 _migrate_rank_rename，但针对「逗号分隔的别名串」：替换后去重保序。

    因为「排位」「排位赛」「赛季」迁移后会撞成同一个词，不去重会出现重复别名。
    """
    s = _migrate_rank_rename(alias_str)
    if s == alias_str:
        return s
    seen, out = set(), []
    for x in s.split(","):
        if x not in seen:
            seen.add(x); out.append(x)
    return ",".join(out)


def _migrate_level_demote_switch(rec):
    """一次性迁移：旧「积分不足是否允许降级」→ 新「消费不掉级」（**语义取反**）。

    2026-09-13 用户拍板：等级默认按「累计积分」算（消费不掉级），并留一个**正向**开关
    供以后切成「消费掉级」。旧键 `level_allow_demote`（1=允许降级）与新键
    `level_keep_on_spend`（1=消费不掉级）语义**正好相反**，所以必须取反 ——
    只改名会让「原本允许降级」的群变成「不允许降级」，等于偷偷改了行为还不报错。

    幂等：跑多次结果一致。新旧都在时以**新键为准**并删掉旧键（只看新键的意图）。
    非 dict / 坏值 / 缺键一律不抛。
    """
    if not isinstance(rec, dict):
        return rec
    if _LEVEL_DEMOTE_OLD_KEY in rec:
        if _LEVEL_DEMOTE_NEW_KEY not in rec:
            try:
                old = int(rec.get(_LEVEL_DEMOTE_OLD_KEY) or 0)
            except (TypeError, ValueError):
                old = 0          # 坏值按「不允许降级」处理：宁可保持不掉级，也不误降级
            rec[_LEVEL_DEMOTE_NEW_KEY] = 0 if old else 1
        rec.pop(_LEVEL_DEMOTE_OLD_KEY, None)
    return rec


def load_settings():
    """启动时读取设置并套用；无设置文件则用数据内嵌快照，仍无则用代码内默认值。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    CMD_ALIAS_OVERRIDES = hub.CMD_ALIAS_OVERRIDES
    GROUP_SETTINGS = hub.GROUP_SETTINGS
    MALL_ITEMS = hub.MALL_ITEMS
    POINT_LEVELS = hub.POINT_LEVELS
    SETTINGS_FILE = hub.SETTINGS_FILE
    SETTINGS_SNAPSHOT = hub.SETTINGS_SNAPSHOT
    SIDEBAR_ORDER = hub.SIDEBAR_ORDER
    TG_MENU = hub.TG_MENU
    _load_settings_payload = hub._load_settings_payload
    _migrate_level_demote_switch = hub._migrate_level_demote_switch
    _migrate_rank_alias_str = hub._migrate_rank_alias_str
    _migrate_rank_rename = hub._migrate_rank_rename
    _migrate_stale_lottery_tpl = hub._migrate_stale_lottery_tpl
    _normalize_levels = hub._normalize_levels
    _write_settings_file = hub._write_settings_file
    apply_command_aliases = hub.apply_command_aliases
    apply_settings = hub.apply_settings
    backup_admins = hub.backup_admins
    buy_packages = hub.buy_packages
    chat_rules = hub.chat_rules
    daily_reset_groups = hub.daily_reset_groups
    leaderboard_groups = hub.leaderboard_groups
    logger = hub.logger
    redeem_goods = hub.redeem_goods

    payload, origin = _load_settings_payload()
    if not payload:
        logger.info("无可用设置（%s 与数据快照均无），全部使用默认配置", SETTINGS_FILE)
        return
    try:
        if "fields" not in payload and "_settings" in payload:
            payload = payload["_settings"]  # 误指向 bot_data.json 时拆出内嵌设置，表格化数据(chat_rules等)才读得到
        _apply_fields(payload, origin)
        _apply_web_credentials(payload, origin)
        _apply_cmd_aliases_menu(payload, origin)
        _apply_snapshot_and_layout(payload, origin)
        _apply_group_settings(payload, origin)
        _apply_persist_back(payload, origin)
    except Exception:
        logger.exception("设置套用失败，使用默认配置")


def save_settings(cfg: dict, new_password: str = ""):
    """网页保存入口：套用内存 + 与已有存档合并写盘（分页保存互不覆盖）+ 可选改密码。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    SETTINGS_FILE = hub.SETTINGS_FILE
    _settings_lock = hub._settings_lock
    _write_settings_file = hub._write_settings_file
    apply_settings = hub.apply_settings

    with _settings_lock:
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        stored = raw.get("fields", {})
        applied = apply_settings(cfg)
        stored.update(applied)
        if new_password and len(new_password.strip()) >= 4:
            hub.set("_web_password", new_password.strip())
        _write_settings_file(stored, hub._web_password, raw.get("cmd_aliases") or {}, raw.get("tg_menu") or [])
    return applied


def save_group_settings(cid, cfg: dict):
    """网页在「某个群」下保存：只把与该群全局默认不同的项存成群级覆盖。

    用户确认的口径：① 全部设置项都能按群覆盖 ② 继承全局、只存差异。
    因此：
      - 标准值 == 全局默认 → **清除**该群覆盖（回继承）。否则会出现"存了个和全局一样的值，
        以后改全局这个群却不跟着变"的反直觉行为。
      - 标准值 != 全局默认 → 写入群级覆盖。
      - 不属于群级范围的键（密码/调度/备份/等级表/指令名）→ 退回按全局保存，行为与改前一致。
    返回 applied（键 → 标准值），网页据此提示"已保存/已跳过"。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    GROUP_SCOPED_KEYS = hub.GROUP_SCOPED_KEYS
    _KEY2VAR = hub._KEY2VAR
    _normalize_settings_values = hub._normalize_settings_values
    _safe_cid = hub._safe_cid
    group_set = hub.group_set
    logger = hub.logger
    save_settings = hub.save_settings
    cid = _safe_cid(cid)
    if not cid:
        return save_settings(cfg)
    scoped = {k: v for k, v in cfg.items() if k in GROUP_SCOPED_KEYS}
    rest = {k: v for k, v in cfg.items() if k not in GROUP_SCOPED_KEYS}
    norm = _normalize_settings_values(scoped)
    applied = {}
    for key, val in norm.items():
        gname = _KEY2VAR.get(key)
        if gname is None:
            continue
        if val == hub.namespace().get(gname):
            group_set(cid, key, None)      # 与全局一致 → 不存差异，回继承
        else:
            group_set(cid, key, val)       # 该群专属
        applied[key] = val
    if rest:
        applied.update(save_settings(rest))   # 全局键：照旧写全局
    else:
        save_settings({})                     # 仅触发落盘（把 group_settings 写进设置文件）
    logger.info("群 %s 保存群级设置：覆盖 %d 项，全局 %d 项", cid, len(norm), len(rest))
    return applied


def _sync_cmd_globals_from_aliases():
    """指令名单一真源：网页「命令管理」改了触发词，帮助文案里的指令名同步跟着变。

    之前 QUERY_CMD/SIGN_CMD 等既是设置项又在命令管理页可改，两处各说各话，
    改了页面里显示的还是旧词。现在设置项已下线，统一以命令管理的别名为准。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BASE_CMD_ALIASES = hub.BASE_CMD_ALIASES
    CMD_ALIAS_OVERRIDES = hub.CMD_ALIAS_OVERRIDES
    _CMD_FACTORY_DEFAULTS = hub._CMD_FACTORY_DEFAULTS
    cmd_invite_link = hub.cmd_invite_link
    cmd_invite_rank_all = hub.cmd_invite_rank_all
    cmd_invite_rank_month = hub.cmd_invite_rank_month
    cmd_invite_rank_today = hub.cmd_invite_rank_today
    cmd_my_level = hub.cmd_my_level
    cmd_my_points = hub.cmd_my_points
    cmd_points_rank = hub.cmd_points_rank
    cmd_points_redeem = hub.cmd_points_redeem
    cmd_sign = hub.cmd_sign
    for gname, fn in (("QUERY_CMD", cmd_my_points), ("SIGN_CMD", cmd_sign), ("RANK_CMD", cmd_points_rank),
                      ("LEVEL_CMD", cmd_my_level), ("REDEEM_CMD", cmd_points_redeem),
                      ("INVITE_LINK_CMD", cmd_invite_link), ("INVITE_RANK_TODAY_CMD", cmd_invite_rank_today),
                      ("INVITE_RANK_MONTH_CMD", cmd_invite_rank_month), ("INVITE_RANK_ALL_CMD", cmd_invite_rank_all)):
        override = CMD_ALIAS_OVERRIDES.get(fn.__name__)
        names = [a.strip() for a in str(override or "").replace("，", ",").split(",") if a.strip()]
        if not names:
            base_names = sorted(a for a, f in BASE_CMD_ALIASES.items() if f is fn)
            d = _CMD_FACTORY_DEFAULTS.get(gname)   # 出厂默认优先（中文指令比英文别名更贴近用户认知）
            names = [d] if d in base_names else base_names[:1]
        if names:
            hub.namespace()[gname] = names[0]


def apply_command_aliases():
    """重建命令分发表：出厂别名 + 网页覆盖层 + QUERY/SIGN/RANK 动态名。
    某命令有非空覆盖时，其出厂触发词整体失效（覆盖即替换）；覆盖为空则恢复出厂。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BASE_CMD_ALIASES = hub.BASE_CMD_ALIASES
    CMD_ALIASES = hub.CMD_ALIASES
    CMD_ALIAS_OVERRIDES = hub.CMD_ALIAS_OVERRIDES
    _HANDLERS_BY_NAME = hub._HANDLERS_BY_NAME
    _sync_cmd_globals_from_aliases = hub._sync_cmd_globals_from_aliases
    _sync_dyn_aliases = hub._sync_dyn_aliases
    CMD_ALIASES.clear()
    parsed = {}
    for fn_name, alias_str in CMD_ALIAS_OVERRIDES.items():
        if fn_name not in _HANDLERS_BY_NAME: continue
        aliases = [a.strip() for a in str(alias_str).replace("，", ",").split(",") if a.strip()]
        if aliases: parsed[fn_name] = aliases
    overridden_fns = {_HANDLERS_BY_NAME[fn_name] for fn_name in parsed}
    for alias, fn in BASE_CMD_ALIASES.items():
        if fn in overridden_fns: continue
        CMD_ALIASES[alias] = fn
    for fn_name, aliases in parsed.items():
        fn = _HANDLERS_BY_NAME[fn_name]
        for a in aliases: CMD_ALIASES[a] = fn
    _sync_cmd_globals_from_aliases()   # 帮助文案里的指令名跟随网页改动（单一真源）
    _sync_dyn_aliases()
