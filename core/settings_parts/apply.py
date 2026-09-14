# -*- coding: utf-8 -*-
"""设置套用的分块实现 —— 2026-09-13 从 core/settings.py 的 apply_settings 拆出。

只做搬运，未改任何逻辑。每个函数负责一类字段（数字/布尔/多选/等级表/文本/赛马三件套/词表），
签名统一 `(cfg, applied)`：cfg 是待套用的设置字典，applied 是"实际生效的键值"累加器（dict，原地改）。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
⚠️ 本包**禁止** import core.settings（循环导入）。
"""
import re

from core import hub



def _apply_numeric_fields(cfg, applied):
    SETTINGS_FIELDS = hub.SETTINGS_FIELDS
    # 数字字段先套用；赛马三件套（count/names/emoji）抽出单独联动处理
    for key, gname, _label, ftype, lo, hi, _grp in SETTINGS_FIELDS:
        if key not in cfg or ftype not in ("int", "float") or key == "horse_count":
            continue
        try:
            v = float(cfg[key])
            if ftype == "int":
                if v != int(v): raise ValueError
                v = int(v)
            if not (lo <= v <= hi): raise ValueError
        except (ValueError, TypeError):
            continue
        hub.namespace()[gname] = v
        applied[key] = v


def _apply_bool_fields(cfg, applied):
    SETTINGS_FIELDS = hub.SETTINGS_FIELDS
    # 布尔字段
    for key, gname, _label, ftype, _lo, _hi, _grp in SETTINGS_FIELDS:
        if key in cfg and ftype == "bool":
            v = 1 if str(cfg[key]).strip().lower() in ("1", "on", "true", "yes", "是") else 0
            hub.namespace()[gname] = v
            applied[key] = v


def _apply_multi_fields(cfg, applied):
    SETTINGS_FIELDS = hub.SETTINGS_FIELDS
    _multi_set = hub._multi_set
    # 多选字段（勾选组）：非法选项直接丢弃，顺序按 MULTI_OPTIONS 定义
    for key, gname, _label, ftype, _lo, _hi, _grp in SETTINGS_FIELDS:
        if key not in cfg or ftype != "multi":
            continue
        v = _multi_set(cfg[key], key)
        hub.namespace()[gname] = v
        applied[key] = v


def _apply_levels_items(cfg, applied):
    _normalize_levels = hub._normalize_levels
    # 等级表 / 商品表：每行 "名称:数值"（支持中英文冒号），按数值升序
    for key, gname, ftype in (("point_levels", "POINT_LEVELS", "levels"), ("mall_items", "MALL_ITEMS", "items")):
        if key not in cfg:
            continue
        raw = cfg[key]
        if isinstance(raw, (list, tuple)):
            # 富结构直传（网页保存的等级表含 perms/on）：原样收下，只做字段校验
            if ftype == "levels" and raw and all(isinstance(x, dict) for x in raw):
                parsed, ok = [], True
                for it in raw:
                    name = str(it.get("name", "")).strip()[:12]
                    if not name or any(ch in name for ch in "<>&"):
                        ok = False; break
                    try:
                        v = int(float(it.get("value", 0) or 0))
                    except (TypeError, ValueError):
                        ok = False; break
                    if not (0 <= v <= 10000000):
                        ok = False; break
                    item = {"name": name, "value": v}
                    if "perms" in it:
                        item["perms"] = str(it.get("perms") or "")
                    if "on" in it:
                        item["on"] = it.get("on")
                    parsed.append(item)
                if ok and parsed and len(parsed) <= 30:
                    parsed.sort(key=lambda x: x["value"])
                    hub.namespace()[gname] = parsed
                    _normalize_levels()
                    applied[key] = parsed
                continue
            # 2026-09-13 补：商城商品表的富结构（此前只给 levels 写了这支，items 漏了 ——
            #   后台「添加商品」与存档快照 data.py 写出的都是字典列表，喂进来会被**静默丢弃**，
            #   属「改了没用」的同一类坑）。校验口径与 /mall_add 一致：名字非空且无 HTML 字符、
            #   价格 ≥1；价格兼容新旧结构（price / value，与 _mall_price 同口径）。
            #   刻意**不排序**：富结构是机器生成的，商品顺序就是用户在后台排好的顺序
            #   （/mall_toggle/{i} 按下标操作），排序会把用户的顺序打乱。
            if ftype == "items" and raw and all(isinstance(x, dict) for x in raw):
                parsed, ok = [], True
                for it in raw:
                    name = str(it.get("name", "")).strip()[:30]
                    if not name or any(ch in name for ch in "<>&"):
                        ok = False; break
                    try:
                        v = int(float(it.get("price", it.get("value", 0)) or 0))
                    except (TypeError, ValueError):
                        ok = False; break
                    if not (1 <= v <= 10000000):
                        ok = False; break
                    item = dict(it)                     # 原样收下：desc / on / 自定义键都不丢
                    item["name"] = name
                    item["price"] = v
                    item.pop("value", None)             # 避免新旧两个价格键并存
                    parsed.append(item)
                if ok and parsed and len(parsed) <= 30:
                    hub.namespace()[gname] = parsed
                    applied[key] = parsed
                continue
            lines = raw
        else:
            lines = str(raw).replace("：", ":").splitlines()
        parsed, ok = [], True
        for line in lines:
            line = str(line).strip()
            if not line:
                continue
            name, _, val = line.partition(":")
            name, val = name.strip(), val.strip()
            if not name or len(name) > 12 or any(ch in name for ch in "<>&"):
                ok = False; break
            try:
                v = int(float(val))
            except ValueError:
                ok = False; break
            if not (0 <= v <= 10000000) or (ftype == "items" and v < 1):
                ok = False; break
            parsed.append({"name": name, "value": v})
        if ok and parsed and len(parsed) <= 30:
            parsed.sort(key=lambda x: x["value"])
            hub.namespace()[gname] = parsed
            if ftype == "levels":
                _normalize_levels()   # 纯文本格式（名称:数值）也要补 perms/on
            applied[key] = parsed
        elif ok and not parsed and ftype == "items":
            hub.namespace()[gname] = []  # 商品表允许清空
            applied[key] = []


def _apply_text_fields(cfg, applied):
    SETTINGS_FIELDS = hub.SETTINGS_FIELDS
    # 文本模板 / 短文本（表情）/ 自定义指令
    for key, gname, _label, ftype, _lo, _hi, _grp in SETTINGS_FIELDS:
        if key not in cfg or ftype not in ("text", "short", "cmd"):
            continue
        v = str(cfg[key]).replace("\r\n", "\n").strip()
        if ftype == "cmd":
            v = v.lstrip("/")
            if not v or len(v) > 16 or re.search(r"[\s<>&@]", v):
                continue
        elif ftype == "short":
            if not v or len(v) > 8:
                continue
        elif len(v) > 1500:
            continue
        hub.namespace()[gname] = v
        applied[key] = v


def _apply_horse_trio(cfg, applied):
    # --- 赛马三件套联动：数量/名称/表情必须一致才提交，否则整体保持原状（防 5 匹马 3 个名字的崩局） ---
    if any(k in cfg for k in ("horse_count", "horse_names", "horse_emoji")):
        def _split(v, maxlen):
            if isinstance(v, (list, tuple)):
                ps = [str(x).strip() for x in v if str(x).strip()]
            else:
                ps = [p.strip() for p in re.split(r"[,，]", str(v)) if p.strip()]
            if ps and all(0 < len(p) <= maxlen and not any(ch in p for ch in "<>&") for p in ps):
                return ps
            return None
        new_count = hub.namespace()["HORSE_COUNT"]
        cnt_given = "horse_count" in cfg
        try:
            c = int(float(cfg.get("horse_count")))
            if not (2 <= c <= 8): raise ValueError
            new_count = c
        except (ValueError, TypeError):
            pass
        new_names = _split(cfg["horse_names"], 8) if "horse_names" in cfg else hub.namespace()["HORSE_NAMES"]
        new_emoji = _split(cfg["horse_emoji"], 4) if "horse_emoji" in cfg else hub.namespace()["HORSE_EMOJI"]
        if new_names and new_emoji and len(new_names) == new_count == len(new_emoji):
            hub.namespace()["HORSE_COUNT"], hub.namespace()["HORSE_NAMES"], hub.namespace()["HORSE_EMOJI"] = new_count, new_names, new_emoji
            if cnt_given: applied["horse_count"] = new_count
            if "horse_names" in cfg: applied["horse_names"] = new_names
            if "horse_emoji" in cfg: applied["horse_emoji"] = new_emoji


def _apply_lists_fields(cfg, applied):
    SETTINGS_FIELDS = hub.SETTINGS_FIELDS
    for key, gname, _label, ftype, _lo, _hi, _grp in SETTINGS_FIELDS:
        if key not in cfg or ftype not in ("names", "emoji", "bets") or key in ("horse_names", "horse_emoji"):
            continue
        raw = cfg[key]
        if isinstance(raw, (list, tuple)):
            parts = [str(p).strip() for p in raw if str(p).strip()]
        else:
            parts = [p.strip() for p in re.split(r"[,，]", str(raw)) if p.strip()]
        if ftype == "bets":
            try:
                vals = sorted({int(float(p)) for p in parts})
            except (ValueError, TypeError):
                continue
            if not (1 <= len(vals) <= 6 and all(1 <= v <= 100000 for v in vals)):
                continue
            hub.namespace()[gname] = vals
            applied[key] = vals
        elif ftype == "emoji":
            # 表情列表（如赛马表情）：每条 ≤4 字符；条数联动校验走下方赛马三件套
            if parts and all(0 < len(p) <= 4 and not any(ch in p for ch in "<>&") for p in parts):
                hub.namespace()[gname] = parts
                applied[key] = parts
        else:
            # 通用词表（敏感词/域名白名单等）：每条 ≤64 字符；删光提交空=清空词表。
            # 此前缺失本分支，导致保存被静默丢弃且页面永远提示「已跳过」——敏感词形同虚设的真 bug。
            if parts:
                if all(0 < len(p) <= 64 and not any(ch in p for ch in "<>&") for p in parts):
                    hub.namespace()[gname] = parts
                    applied[key] = parts
            else:
                hub.namespace()[gname] = []
                applied[key] = []
