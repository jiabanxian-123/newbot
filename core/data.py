# -*- coding: utf-8 -*-
"""infra/data —— 从 bot.py 抽出（由 tools/extract.py 生成，只做搬运不做逻辑改动）。

依赖约定（见 README「抽缝」）：
  - 只允许 `from core import hub`；禁止 `from bot import ...` / `import bot`
  - 函数开头的前导别名 `名 = hub.名` 在**每次调用时**才查表，
    所以测试里 `m.名 = fake` 对这里实时生效
  - 被本模块改写的全局变量走 `hub.X` 读 / `hub.set("X", v)` 写
"""

from core import hub

from core.data_parts import (  # noqa: E402
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
    build_data_snapshot,
    _restore_web_settings_only,
    _restore_embedded_settings,
    _send_restore_summary,
)

# 本模块用到的标准库/第三方 import（bot.py 里原有的那几条）
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import asyncio
import json
import os
import shutil
import time

def total_profit_by_game(game_profit, chat_id):
    """聚合某游戏所有日期的盈亏为累计总数。"""
    total = defaultdict(int)
    for dates in game_profit.values():
        for uid, v in dates.get(chat_id, {}).items():
            total[uid] += v
    return dict(total)


def now_bj():
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BEIJING_TZ = hub.BEIJING_TZ
    return datetime.now(BEIJING_TZ)


def race_id(ts):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BEIJING_TZ = hub.BEIJING_TZ
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(BEIJING_TZ).strftime("%Y%m%d-%H%M")


def business_date(now=None):
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    now_bj = hub.now_bj
    now = now or now_bj()
    return (now + timedelta(days=1) if (now.hour, now.minute) >= (23, 50) else now).strftime("%Y-%m-%d")


def restore_nested(target, source):

    for cid, users in source.items():
        for uid, value in users.items(): target[int(cid)][int(uid)] = int(value)


def save_data():
    """高性能脏标记保存：确保安全初始化。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    save_event = hub.save_event

    hub.set("data_dirty", True)
    # 彻底解决 save_event 未初始化导致的挂死问题
    try:
        if save_event is not None:
            save_event.set()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
# 分片存储层（Q9）：把原来一个大文件拆成 6 个，按功能域分组
# ══════════════════════════════════════════════════════════════════════════
# 为什么拆：原 bot_data.json 单文件 290KB+、70 个数据块混在一起 ——
#   出问题难定位、想手动改难、某块损坏牵连全部。
#
# 为什么是「a/b 两槽轮换 + 指针」而不是「直接写 6 个文件」：
#   单文件写盘是原子的（写 .tmp → os.replace），拆成 6 个就没法一次换掉。
#   写一半崩 → 前 3 个新的、后 3 个旧的 → 账目对不上（积分加了但流水没记）。
#   所以每次写到**另一个槽**（parts/a ↔ parts/b 轮流），6 个文件全部写好后
#   用**一个指针文件**原子切换；断电时指针还指着上一槽的完整数据，
#   永远读不到半新半旧的状态。另一槽留着上一代，可随时回退。
#
# 为什么不用「分代目录」（每代一个新目录 + 删掉更老的代）：
#   那样每次保存的**写盘路径里都带一次递归删除**。路径一旦算错，
#   删的就是用户真实数据 —— 写盘路径里不该出现删除动作。
#   两槽方案全程**不删任何文件**，而「指针原子切换」+「另一槽可回退」
#   这两个核心保证完全不变。
#
# 零迁移：parts/ 不存在时自动回退读旧 bot_data.json，
#   第一次保存自动写成新格式 —— 不需要单独的迁移步骤。

# ── 存档格式版本号（REVIEW §八 ⑦）────────────────────────────────────────
# 每次保存往当前槽写一个 meta.json，记下这份存档的格式版本。
#
# 为什么需要它：以后只要动了存档结构（加分片、改嵌套、换编码），就必须能一眼
#   认出「手里这份是老格式还是新格式」。没有版本号就只能在读取时猜，猜错 = 静默丢数据。
#
# 为什么单独一个文件、而不塞进某个分片：
#   分片是「6 个文件必须齐」的契约（_read_slot 少一个文件就返回 None）。
#   往 PART_FILE_GROUPS 里加一个必需文件，会让**本功能上线前写下的老存档直接读不出来**。
#   所以 meta.json 是**可选**的：没有 → 当老存档，照常读；损坏 → 当没有，照常读。
#
# 什么时候要 +1：存档结构发生**不向后兼容**的变化（读老数据必须先迁移）时。
SCHEMA_VERSION = 1

PART_FILE_GROUPS = (
    ("players.json", (
        "user_names", "user_first_seen", "member_profiles", "total_earned",
        "games_played", "sign_data", "newbie_rewarded", "game_chips",
        "user_titles", "title_expiry", "title_equipped", "warn_counts",
        "daily_emergency_used", "champions_history",
    )),
    ("groups.json", (
        "authorized_groups", "bot_admins", "blacklist", "whitelist", "_settings",
        "announce_last_date", "bot_added_by", "join_requests", "join_verify_pending",
        "leave_records", "member_joined_at", "admin_logs", "settings_changes",
    )),
    ("games.json", (
        "poker_profit_by_date", "race_profit_by_date", "blackjack_profit_by_date",
        "jinhua_profit_by_date", "race_history", "blackjack_history", "race_daily_stats",
"race_jackpot", "race_subsidy_by_day", "hourly_race_enabled",
        "mall_orders", "buy_orders", "redeem_counts", "redeem_orders", "_lotteries",
        "rp_packets", "pending_game_bets", "last_business_date", "pending_deletes",
    )),
    ("ledger.json", ("game_flows", "ledger", "chat_earn_daily")),
    ("season.json", (
        "season_points", "season_joined", "season_games", "season_rebuy", "season_name",
        "season_id", "season_start_ts", "season_end_ts", "season_active",
        "season_profit_by_date", "season_exchange_daily", "season_exchange_bonus",
    )),
    ("invite.json", (
        "invite_records", "invite_debug", "invite_links", "invite_confirmed",
        "invite_daily", "invite_pending", "observe_checked", "lurker_checked",
        "inherit_daily", "tag_synced",
    )),
)


def _parts_root():
    """分片目录：与数据文件同级的 parts/。"""
    DATA_FILE = hub.DATA_FILE
    return os.path.join(os.path.dirname(os.path.abspath(DATA_FILE)), "parts")


# 两个槽轮流写。**只用两个**是有意的：
#   写的时候指针还指着当前槽，当前槽始终完好 → 写一半崩也读得到旧数据；
#   另一槽留着上一代的完整副本 → 当前槽损坏时能回退；
#   不需要任何删除/清理，写盘路径里没有 rmtree（少一个能删错东西的地方）。
PART_SLOTS = ("a", "b")


def _slot_dir(slot):
    return os.path.join(_parts_root(), slot)


def _other_slot(slot):
    return "b" if slot == "a" else "a"


def _read_parts_pointer():
    """读指针，返回槽名（"a"/"b"）；没有或内容不认识则 None。"""
    try:
        with open(os.path.join(_parts_root(), "current"), "r", encoding="utf-8") as f:
            v = f.read().strip()
        return v if v in PART_SLOTS else None
    except Exception:
        return None


def _write_parts_pointer(slot):
    """原子切换指针（单文件 .tmp → os.replace，这是整个方案的关键）。"""
    root = _parts_root()
    tmp = os.path.join(root, "current.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(slot)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(root, "current"))


def _read_slot(slot):
    """读一个槽的 6 个文件并合并；任一文件缺失/损坏 → 返回 None。"""
    d = _slot_dir(slot)
    out = {}
    for fname, keys in PART_FILE_GROUPS:
        p = os.path.join(d, fname)
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            part = json.load(f)
        if not isinstance(part, dict):
            return None
        for k in keys:
            if k in part:
                out[k] = part[k]
    return out


def _read_shard_meta(slot):
    """读一个槽的 meta.json（存档版本号等辅助信息）。

    刻意做成「永不抛异常、永远返回 dict」：
      - 文件不存在（本功能上线前写的老存档）→ {}
      - 文件损坏 / 不是 JSON 对象 / 权限不够 → {}
    版本标记只是辅助信息，**绝不能成为单点故障** —— 它坏了数据也必须照常读得出来。
    """
    try:
        with open(os.path.join(_slot_dir(slot), "meta.json"), "r", encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _slot_mtime(slot):
    """一个槽里最新的文件时间；用来在指针丢失时挑「较新的那个」。"""
    d = _slot_dir(slot)
    try:
        return max((os.path.getmtime(os.path.join(d, n))
                    for n in os.listdir(d) if n.endswith(".json")), default=0)
    except OSError:
        return 0


def load_parts():
    """只读分片。指针指向的槽优先，它坏了自动换另一个槽。都没数据则 None。

    指针丢失（被误删等）时退化为「挑较新的那个槽」—— 比直接报无数据安全得多。
    """
    logger = hub.logger
    cur = _read_parts_pointer()
    order = [cur] if cur else []
    order += [s for s in PART_SLOTS if s not in order]
    if not cur:
        order.sort(key=_slot_mtime, reverse=True)
    for slot in order:
        try:
            data = _read_slot(slot)
        except Exception:
            logger.exception("分片读取失败（槽 %s）", slot)
            data = None
        if data is not None:
            if slot != cur:
                logger.warning("槽 %s 不可用，已改用槽 %s", cur or "(指针缺失)", slot)
            # 存档版本比本机新 → 照常把数据给出去，只记一条告警。
            # 理由：吞掉数据比读一份「可能偏新的」数据危险得多；而默默无语会让
            # 运维永远不知道手里的数据来自更新版本（版本号就白加了）。
            ver = _read_shard_meta(slot).get("schema_version")
            if isinstance(ver, int) and ver > SCHEMA_VERSION:
                logger.warning(
                    "存档版本 %s 高于本程序支持的 %s（槽 %s）：数据照常读取，"
                    "但该存档可能由更新版本的程序写出，请确认是否需要升级程序",
                    ver, SCHEMA_VERSION, slot)
            return data
    return None


def data_size_kb():
    """数据体积（形如 "291 KB"）。Q9 后是 6 个分片的合计；未迁移则是旧单文件大小。"""
    DATA_FILE = hub.DATA_FILE
    try:
        cur = _read_parts_pointer()
        if cur is None:
            cur = max(PART_SLOTS, key=_slot_mtime)
        d = _slot_dir(cur)
        if os.path.isdir(d):
            total = sum(os.path.getsize(os.path.join(d, n))
                        for n in os.listdir(d) if n.endswith(".json"))
            if total:
                return f"{total / 1024:.0f} KB"
        return f"{os.path.getsize(DATA_FILE) / 1024:.0f} KB"
    except OSError:
        return "无"


def _single_file_is_newer(path):
    """旧单文件是不是比「分片指针」更新 —— 即有人刚把一份备份写回来。

    正常运行时没人写单文件（写盘走 save_parts），所以它的 mtime 会一直停留在
    迁移那一刻；只有 /恢复 命令和「管理员手工拷备份回数据目录」两种情况会刷新它。
    这两种情况都必须以单文件为准，否则恢复会被旧分片静默吃掉。
    """
    try:
        if not os.path.exists(path):
            return False
        ptr = os.path.join(_parts_root(), "current")
        if not os.path.exists(ptr):
            return False
        return os.path.getmtime(path) > os.path.getmtime(ptr)
    except OSError:
        return False


def read_snapshot():
    """读一份完整数据（dict）。分片优先，没有则回退旧单文件，主文件坏了再退 .bak。

    load_data() 与 /备份 共用这一个入口。

    ★ 例外：单文件比「分片指针」更新时，以单文件为准（见 _single_file_is_newer）。
    """
    logger = hub.logger
    DATA_FILE = hub.DATA_FILE
    DATA_BACKUP_FILE = hub.DATA_BACKUP_FILE
    data = load_parts()
    if data is not None:
        if _single_file_is_newer(DATA_FILE):
            logger.warning("检测到比数据分片更新的 %s，按它读取（/恢复 或手工恢复）", DATA_FILE)
        else:
            return data
    source = DATA_FILE if os.path.exists(DATA_FILE) else DATA_BACKUP_FILE
    if not os.path.exists(source):
        return None
    try:
        with open(source, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        if source != DATA_FILE or not os.path.exists(DATA_BACKUP_FILE):
            logger.exception("数据读取失败")
            return None
        try:
            with open(DATA_BACKUP_FILE, "r", encoding="utf-8") as file:
                d = json.load(file)
            logger.warning("主数据文件损坏，已从备份恢复")
            return d
        except Exception:
            logger.exception("备份读取失败")
            return None


def save_parts(data):
    """把整份数据写成分片：写另一个槽 → 全部写好后原子切指针。

    返回 True/False。**指针切换成功即视为保存成功** —— 那一刻数据已完整。
    全程不删任何文件：写的时候当前槽完好（指针还指着它），另一槽留着上一代。
    """
    logger = hub.logger
    cur = _read_parts_pointer()
    nxt = _other_slot(cur) if cur else PART_SLOTS[0]
    d = _slot_dir(nxt)
    try:
        os.makedirs(d, exist_ok=True)
        for fname, keys in PART_FILE_GROUPS:
            part = {k: data[k] for k in keys if k in data}
            tmp = os.path.join(d, fname + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(part, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, os.path.join(d, fname))
        # ── 存档版本标记（可选文件，见 SCHEMA_VERSION 处注释）──
        # 写失败只记日志、**不阻断保存**：数据完整性优先于版本标记。
        try:
            mt = os.path.join(d, "meta.json.tmp")
            with open(mt, "w", encoding="utf-8") as f:
                json.dump({"schema_version": SCHEMA_VERSION,
                           "saved_at": datetime.now(hub.BEIJING_TZ)
                                       .strftime("%Y-%m-%d %H:%M:%S")},
                          f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(mt, os.path.join(d, "meta.json"))
        except Exception:
            logger.exception("存档版本号写入失败（不影响本次保存，数据本身已写好）")
        _write_parts_pointer(nxt)          # ← 原子切换点：过了这行才算存好
    except Exception:
        logger.exception("分片写盘失败（槽 %s），指针未切换，仍读当前槽", nxt)
        return False
    return True


def force_save_now():
    """强制立刻执行物理写盘，用于关机等场景。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    DATA_BACKUP_FILE = hub.DATA_BACKUP_FILE
    DATA_FILE = hub.DATA_FILE
    DATA_TEMP_FILE = hub.DATA_TEMP_FILE
    data_save_lock = hub.data_save_lock
    logger = hub.logger
    try:
        with data_save_lock:
            data = build_data_snapshot()
            os.makedirs(os.path.dirname(os.path.abspath(DATA_FILE)), exist_ok=True)
            # Q9：写成分片（a/b 两槽轮换 + 原子指针），见上方「分片存储层」
            if not hub.save_parts(data):
                return False
        return True
    except Exception:
        logger.exception("物理写盘失败")
        return False


async def data_save_worker():
    """后台高性能保存任务：数据变更后 3 秒合并保存；无变更时每 60 秒保底强制保存一次。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    force_save_now = hub.force_save_now
    logger = hub.logger
    save_event = hub.save_event

    while True:
        try:
            await asyncio.wait_for(save_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass  # 60 秒无触发，走保底保存
        save_event.clear()
        try:
            if hub.data_dirty:
                # ★ 先清 dirty **再**写盘（顺序很关键）：
                #   写盘是在线程里跑的、要花时间，期间若又发生了新改动，
                #   dirty 会被重新置起 → 下一轮会再存一次，不会丢。
                #   原写法是「写完再无条件清」，会把写盘**窗口内**的新改动
                #   一起误标成「已保存」，而 60 秒保底保存只看 dirty
                #   → 进程被硬杀（SIGKILL / OOM）时这一小段改动就丢了。
                hub.set("data_dirty", False)
                # 在单独的线程中执行写盘，不阻塞主循环
                ok = await asyncio.to_thread(force_save_now)
                # 写失败要重新标脏，否则这次改动永远不会重试（60 秒保底只看 dirty）。
                if not ok:
                    hub.set("data_dirty", True)
        except Exception:
            logger.exception("data_save_worker 写盘异常（已吞并继续）")
        await asyncio.sleep(3)


def load_data():
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_IDS = hub.ADMIN_USER_IDS
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    BLACKLISTED_USERS = hub.BLACKLISTED_USERS
    BOT_ADMINS = hub.BOT_ADMINS
    DATA_BACKUP_FILE = hub.DATA_BACKUP_FILE
    DATA_FILE = hub.DATA_FILE
    SETTINGS_SNAPSHOT = hub.SETTINGS_SNAPSHOT
    TITLE_GAMBLING_GOD = hub.TITLE_GAMBLING_GOD
    _pending_deletes = hub._pending_deletes
    admin_logs = hub.admin_logs
    blackjack_history = hub.blackjack_history
    blackjack_profit_by_date = hub.blackjack_profit_by_date
    bot_added_by = hub.bot_added_by
    buy_orders = hub.buy_orders
    champions_history = hub.champions_history
    chat_earn_daily = hub.chat_earn_daily
    daily_emergency_used = hub.daily_emergency_used
    force_save_now = hub.force_save_now
    game_chips = hub.game_chips
    game_flows = hub.game_flows
    games_played = hub.games_played
    hourly_race_enabled = hub.hourly_race_enabled
    inherit_daily = hub.inherit_daily
    invite_confirmed = hub.invite_confirmed
    invite_daily = hub.invite_daily
    invite_debug = hub.invite_debug
    invite_links = hub.invite_links
    invite_pending = hub.invite_pending
    invite_records = hub.invite_records
    jinhua_profit_by_date = hub.jinhua_profit_by_date
    join_requests = hub.join_requests
    join_verify_pending = hub.join_verify_pending
    leave_records = hub.leave_records
    ledger = hub.ledger
    logger = hub.logger
    lotteries = hub.lotteries
    lurker_checked = hub.lurker_checked
    mall_orders = hub.mall_orders
    member_joined_at = hub.member_joined_at
    member_profiles = hub.member_profiles
    newbie_rewarded = hub.newbie_rewarded
    now_bj = hub.now_bj
    observe_checked = hub.observe_checked
    pending_game_bets = hub.pending_game_bets
    poker_profit_by_date = hub.poker_profit_by_date
    race_daily_stats = hub.race_daily_stats
    race_history = hub.race_history
    race_jackpot = hub.race_jackpot
    race_profit_by_date = hub.race_profit_by_date
    race_subsidy_by_day = hub.race_subsidy_by_day
    redeem_counts = hub.redeem_counts
    redeem_orders = hub.redeem_orders
    restore_nested = hub.restore_nested
    rp_packets = hub.rp_packets
    season_exchange_bonus = hub.season_exchange_bonus
    season_exchange_daily = hub.season_exchange_daily
    season_games = hub.season_games
    season_joined = hub.season_joined
    season_points = hub.season_points
    season_profit_by_date = hub.season_profit_by_date
    season_rebuy = hub.season_rebuy
    sget = hub.sget
    sign_data = hub.sign_data
    title_equipped = hub.title_equipped
    title_expiry = hub.title_expiry
    total_earned = hub.total_earned
    user_first_seen = hub.user_first_seen
    user_names = hub.user_names
    user_titles = hub.user_titles
    warn_counts = hub.warn_counts
    whitelist = hub.whitelist
    read_snapshot = hub.read_snapshot

    # Q9：分片优先；没有分片（首次运行/尚未迁移）自动回退读旧单文件
    data = read_snapshot()
    if data is None: return
    try:
        # 设置快照：数据文件里内嵌的网页设置，供 load_settings 在 bot_settings.json 缺失时还原
        embedded = data.get("_settings")
        if hub._looks_like_settings_snapshot(embedded) and not SETTINGS_SNAPSHOT:
            SETTINGS_SNAPSHOT.update(embedded)
            logger.info("已从数据文件读出内嵌设置快照（%s 项）", len(embedded.get("fields", {})))
    except Exception:
        logger.exception("内嵌设置快照读取失败")
    # 群组抽奖恢复
    try:
        lotteries.clear()
        for cid_s, lo in (data.get("_lotteries") or {}).items():
            try: cid = int(cid_s)
            except (ValueError, TypeError): continue
            if not isinstance(lo, dict): continue
            lo.setdefault("status", "open")
            # 启动时若活动已超时且仍 open → 立即标记 finished（避免重部署后仍显示在进行中）
            if lo["status"] == "open" and time.time() >= lo.get("end_ts", 0):
                lo["status"] = "finished"
            lotteries[cid] = lo
    except Exception:
        logger.exception("抽奖数据恢复失败")
    try:
        _restore_chips(data)
        _restore_profit_and_season(data)
        _restore_titles_and_names(data)
        _restore_points_ledger(data)
        _restore_guesses_refund(data)
        _restore_redeem_orders(data)
        _restore_invite_data(data)
        _restore_member_and_pending(data)
        _restore_group_data(data)
        _restore_permissions_and_history(data)
    except Exception:
        logger.exception("恢复数据失败")


def archive_old_profit_data(keep_days=90):
    """将超过 keep_days 天的盈亏明细归档合并到 _archive，保留累计榜数字不变。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    blackjack_profit_by_date = hub.blackjack_profit_by_date
    jinhua_profit_by_date = hub.jinhua_profit_by_date
    logger = hub.logger
    now_bj = hub.now_bj
    race_profit_by_date = hub.race_profit_by_date
    season_profit_by_date = hub.season_profit_by_date
    cutoff = (now_bj() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    for profit_dict in (race_profit_by_date, blackjack_profit_by_date,
                        jinhua_profit_by_date, season_profit_by_date):
        old_dates = [d for d in list(profit_dict.keys()) if d != "_archive" and d < cutoff]
        if not old_dates:
            continue
        archive = profit_dict["_archive"]
        for d in old_dates:
            for cid, users in profit_dict[d].items():
                for uid, v in users.items():
                    archive[cid][uid] += v
            del profit_dict[d]
    logger.info(f"归档完成：保留 {keep_days} 天明细，旧数据已合并至 _archive")


def _data_file_status():
    """数据文件状态一行串：路径 + 存在/大小（供启动日志与 /health，让 Volume 是否生效一眼可见）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    DATA_FILE = hub.DATA_FILE
    try:
        # Q9：拆成 6 个分片后，报告「当前代 + 分片合计大小」，Volume 是否生效一眼可见
        root = _parts_root()
        cur = _read_parts_pointer()
        if cur and os.path.isdir(os.path.join(root, cur)):
            return f"{root}/（第 {cur} 代 · 6 个分片共 {data_size_kb()}）"
        if os.path.exists(DATA_FILE):
            return f"{DATA_FILE}（{data_size_kb()} · 未迁移）"
        return f"{DATA_FILE}（不存在，将新建）"
    except Exception:
        return DATA_FILE


def ledger_add(cid, frm, to, amt, typ):
    """资金流台账：红包领取/转赠等人对人转移逐笔记账（防小号审查用，留 5000 条）。

    ⚠️ ts 带秒（2026-09-12 用户要的流水显示到秒，见 cmd_points_flow）；读取侧必须容忍
    存量旧数据的「无秒」格式 —— `_flow_ts_full()` 负责补齐，别在显示处直接切片。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ledger = hub.ledger
    now_bj = hub.now_bj
    ledger.append({"ts": now_bj().strftime("%Y-%m-%d %H:%M:%S"), "cid": cid, "frm": frm, "to": to, "amt": amt, "typ": typ})
    if len(ledger) > 5000: del ledger[:len(ledger) - 5000]


def record_game_flows(cid, nets, typ):
    """一局游戏的人对人净转移记入 game_flows（资金流审查页可见，防"通过游戏故意输牌送分"）。

    nets: {uid: 本局净输赢}（正=赢，负=输）。牌局是池模式，无法精确知道谁输给谁，
    按惯例分摊：每个输家的损失按赢家净赢比例折算成 输家→赢家 流向（二人局=精确值）。
    只记真实用户（uid>0）；全输（奖池沉没）/全赢/打平无人对人转移不记。
    21点/赛车是对庄家局，不存在人对人转移，不接此记账。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    game_flows = hub.game_flows
    now_bj = hub.now_bj
    losers = {u: -n for u, n in nets.items() if u > 0 and n < 0}
    winners = {u: n for u, n in nets.items() if u > 0 and n > 0}
    if not losers or not winners:
        return
    lose_total = sum(losers.values())
    ts = now_bj().strftime("%Y-%m-%d %H:%M")
    for w, w_net in winners.items():
        for l, l_loss in losers.items():
            amt = int(l_loss * w_net // lose_total)
            if amt > 0:
                game_flows.append({"ts": ts, "cid": cid, "frm": l, "to": w, "amt": amt, "typ": typ})
    del game_flows[:-2000]


def _flow_ts_full(ts):
    """台账时间补足到秒（流水显示要 YYYY-MM-DD HH:MM:SS）。

    存量旧记录是 'YYYY-MM-DD HH:MM'（16 字），补 ':00'；聊天积分那行是合成时间戳
    （'YYYY-MM-DD 23:59'）同样走这里。已经是带秒的/无法识别的原样返回。
    """
    s = str(ts or "").strip()
    if len(s) == 16 and s[13] == ":":   # 'YYYY-MM-DD HH:MM' 的第 13 位是冒号，缺的正是秒
        return s + ":00"
    return s


def _parse_dt_bj(spec):
    """'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD'（北京时间）→ aware datetime；空/非法返回 None。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    BEIJING_TZ = hub.BEIJING_TZ
    spec = (spec or "").strip()
    if not spec: return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try: return datetime.strptime(spec, fmt).replace(tzinfo=BEIJING_TZ)
        except ValueError: continue
    return None


def parse_hm(value, def_h, def_m):
    """解析 'HH:MM' 配置；非法回退默认。"""
    try:
        h, mnt = str(value).split(":")
        h, mnt = int(h), int(mnt)
        if 0 <= h < 24 and 0 <= mnt < 60: return h, mnt
    except (ValueError, AttributeError): pass
    return def_h, def_m


async def cmd_backup(update, context):
    """管理员备份：把数据文件发送到管理员私聊。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    DATA_FILE = hub.DATA_FILE
    SETTINGS_FILE = hub.SETTINGS_FILE
    force_save_now = hub.force_save_now
    is_bot_admin = hub.is_bot_admin
    read_snapshot = hub.read_snapshot
    logger = hub.logger
    send_reply = hub.send_reply
    uid = update.effective_user.id
    if not is_bot_admin(uid):
        await send_reply(update, context, "⛔ 仅管理员可用")
        return
    # 强制写盘，确保文件是最新的
    ok = await asyncio.to_thread(force_save_now)
    if not ok:
        await send_reply(update, context, "⚠️ 写盘失败，请稍后再试")
        return
    # Q9：数据已拆成 6 个分片 —— 这里现场合并回一份完整快照再发，
    # 备份文件名与格式和拆之前完全一致，管理员用法无变化。
    snap = read_snapshot()
    if snap is None:
        await send_reply(update, context, "⚠️ 数据文件不存在")
        return
    # 拆开 try：把「数据文件发送」单独包，失败时把真实异常返回给管理员；
    # 之前一个大 try 吞所有，群内 /backup 失败只会看到「请先 /start」这种误导性提示。
    try:
        data_bytes = json.dumps(snap, ensure_ascii=False, indent=2).encode("utf-8")
        await context.bot.send_document(
            chat_id=uid,
            document=data_bytes,
            filename=f"bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            caption="📦 数据备份完成（此文件内含网页设置快照，恢复数据即恢复设置）",
        )
    except Exception as exc:
        logger.exception("数据备份发送失败")
        await send_reply(update, context, 
            f"⚠️ 数据备份失败：{type(exc).__name__}: {str(exc)[:200]}\n"
            f"请把这条错误发我排查（常见原因：私聊未 /start、容器磁盘满、文件被另一进程锁定）"
        )
        return
    # 同时发一份纯设置备份，便于「只恢复设置、保留现有数据」
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "rb") as f:
                cfg_bytes = f.read()
            await context.bot.send_document(
                chat_id=uid, document=cfg_bytes,
                filename=f"bot_settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                caption="⚙️ 网页设置备份（只需恢复设置：回复此文件发 /restore）",
            )
        except Exception as exc:
            logger.exception("设置备份发送失败")
            # 数据备份已成功，设置备份失败只是少一个文件，不影响主流程
            await send_reply(update, context, 
                f"⚠️ 设置备份失败：{type(exc).__name__}: {str(exc)[:160]}"
            )
    # 在群里发的命令时，提示一下文件已发到私聊
    if update.effective_chat.id != uid:
        await send_reply(update, context, "✅ 备份文件已发送到你的私聊")


async def cmd_restore(update, context):
    """管理员恢复：回复一个 JSON 备份文件来恢复数据，直接载入内存立即生效（不依赖平台重启）。"""
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    CMD_ALIAS_OVERRIDES = hub.CMD_ALIAS_OVERRIDES
    DATA_FILE = hub.DATA_FILE
    SETTINGS_SNAPSHOT = hub.SETTINGS_SNAPSHOT
    TG_MENU = hub.TG_MENU
    _write_settings_file = hub._write_settings_file
    apply_command_aliases = hub.apply_command_aliases
    apply_settings = hub.apply_settings
    force_save_now = hub.force_save_now
    game_chips = hub.game_chips
    is_bot_admin = hub.is_bot_admin
    load_data = hub.load_data
    load_settings = hub.load_settings
    logger = hub.logger
    save_event = hub.save_event
    season_active = hub.season_active
    season_name = hub.season_name
    send_reply = hub.send_reply

    uid = update.effective_user.id
    if not is_bot_admin(uid):
        await send_reply(update, context, "⛔ 仅管理员可用")
        return
    replied = update.message.reply_to_message
    if not replied or not replied.document:
        await send_reply(update, context, "⚠️ 请回复一个 JSON 备份文件，再发送 /restore\n\n用法：点开备份文件 → 回复 → 发送 /restore")
        return
    tmp_path = f"{DATA_FILE}.restore_tmp"
    try:
        # 下载并验证备份文件
        tg_file = await context.bot.get_file(replied.document.file_id)
        await tg_file.download_to_drive(tmp_path)
        with open(tmp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("备份文件格式错误：不是字典")
        # 设置备份文件（顶层是 fields/web_password，没有 game_chips）→ 只恢复设置，不动积分数据
        if "fields" in data and "game_chips" not in data:
            await _restore_web_settings_only(update, context, data, tmp_path, uid)
            return
        # 验证通过：先阻止后台保存线程用旧数据覆盖新文件
        hub.set("data_dirty", False)
        if save_event is not None:
            save_event.clear()
        # 把当前数据另存一份，再写入新数据
        # ★ Q9 之后数据已拆成 parts/ 分片，DATA_FILE 这个单文件迁移后就**不再更新**，
        #   拷贝它等于拷一份过时的旧数据 —— 真出事想回滚，拿到的是迁移前的状态，
        #   等于没有回退点。改成：先从分片/内存读一份完整快照落盘。
        try:
            _cur = read_snapshot()
            with open(f"{DATA_FILE}.restore_bak", "w", encoding="utf-8") as _bf:
                json.dump(_cur, _bf, ensure_ascii=False)
        except Exception:
            logger.exception("恢复前另存当前数据失败（本次没有回退点）")
        os.replace(tmp_path, DATA_FILE)
        # 关键修复：直接把新文件读进内存 + 落盘，不依赖平台重启。
        # 原实现 os._exit(1) 等平台重启后重新加载，但重启若重建容器，刚写入的文件会被清空 → 恢复失效。
        load_data()
        hub.set("data_dirty", False)
        # ★ 恢复后必须清掉进行中的牌局：load_data() 已经把待退押注退回钱包，
        #   但牌局对象还在内存里（bets / chips 都还在），等它结算时这笔
        #   「已经退过的钱」会被再算一次。
        #   用「active_ 开头、games 结尾的 dict」通配，免得漏掉某个游戏。
        try:
            for _gk, _gv in list(hub.namespace().items()):
                if _gk.startswith("active_") and _gk.endswith("games") and isinstance(_gv, dict):
                    if _gv:
                        logger.warning("数据恢复：清空进行中的牌局 %s（%d 局）", _gk, len(_gv))
                    _gv.clear()
        except Exception:
            logger.exception("数据恢复：清进行中牌局失败")
        # Q9：数据已拆成分片，read_snapshot() 平时优先读分片 —— 刚写回来的单文件
        # 若不重新写成一片，下次启动就会读到旧分片，「恢复」静默失效。
        # 这里立刻把内存里的恢复结果落成分片，之后一切照常（分片为准）。
        try:
            await asyncio.to_thread(force_save_now)
        except Exception:
            logger.exception("恢复后重建分片失败（read_snapshot 会退化为按时间取较新的单文件）")
        _restore_embedded_settings(data)
        await _send_restore_summary(update, context)
        logger.warning("管理员 %s 执行了数据恢复，已直接载入内存", uid)
    except json.JSONDecodeError:
        await send_reply(update, context, "⚠️ 文件不是有效的 JSON 格式，恢复已取消")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    except Exception as e:
        logger.exception("恢复失败")
        await send_reply(update, context, f"⚠️ 恢复失败：{e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def auto_backup(context):
    """定时把数据文件发给配置的目标管理员私聊，当作云端持久化备份。

    无持久磁盘的平台容器重启会清空磁盘，有这份备份就能用 /restore 恢复，
    最坏只丢一个备份周期（30 分钟）的积分变动。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    ADMIN_USER_ID = hub.ADMIN_USER_ID
    DATA_FILE = hub.DATA_FILE
    SETTINGS_FILE = hub.SETTINGS_FILE
    backup_admins = hub.backup_admins
    backup_msg_ids = hub.backup_msg_ids
    force_save_now = hub.force_save_now
    logger = hub.logger
    read_snapshot = hub.read_snapshot
    settings_backup_msg_ids = hub.settings_backup_msg_ids
    sget = hub.sget
    if not sget("BACKUP_ENABLED"):  # 后台「定时任务」开关：关了就不备份，保存即时生效
        return
    if not backup_admins: backup_admins.add(ADMIN_USER_ID)
    try:
        ok = await asyncio.to_thread(force_save_now)
        if not ok:
            logger.warning("自动备份：写盘失败，跳过本次")
            return
        # Q9：合并 6 个分片成一份快照再发（直接读 DATA_FILE 会拿到迁移前的旧数据）
        snap = read_snapshot()
        if snap is None:
            logger.warning("自动备份：数据不存在，跳过本次")
            return
        snap_bytes = json.dumps(snap, ensure_ascii=False, indent=2).encode("utf-8")
        for _uid in list(backup_admins):
            try:
                sent = await context.bot.send_document(
                        chat_id=_uid,
                        document=snap_bytes,
                        filename=f"auto_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                        caption="🤖 每日自动备份（需要恢复时：回复此文件发 /restore）",
                    )
                if sent and _uid == ADMIN_USER_ID:
                    backup_msg_ids.append(sent.message_id)
                    while len(backup_msg_ids) > 7:
                        old = backup_msg_ids.pop(0)
                        try: await context.bot.delete_message(chat_id=ADMIN_USER_ID, message_id=old)
                        except Exception: pass  # 消息可能已被手动删除，忽略
            except Exception as exc:
                logger.warning("自动备份推送 %s 失败: %s", _uid, exc)
        if os.path.exists(SETTINGS_FILE):
            for _uid in list(backup_admins):
                try:
                    with open(SETTINGS_FILE, "rb") as f:
                        sent_cfg = await context.bot.send_document(
                            chat_id=_uid,
                            document=f,
                            filename=f"bot_settings_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                            caption="⚙️ 网页设置备份（只需恢复设置：回复此文件发 /restore）",
                        )
                    if sent_cfg and _uid == ADMIN_USER_ID:
                        settings_backup_msg_ids.append(sent_cfg.message_id)
                        while len(settings_backup_msg_ids) > 7:
                            old = settings_backup_msg_ids.pop(0)
                            try: await context.bot.delete_message(chat_id=ADMIN_USER_ID, message_id=old)
                            except Exception: pass
                except Exception as exc:
                    logger.warning("设置备份推送 %s 失败: %s", _uid, exc)
        logger.info("自动备份完成")
    except Exception:
        logger.exception("自动备份失败")
