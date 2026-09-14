# -*- coding: utf-8 -*-
"""**任务状态查询**（/定时任务状态）—— 把各调度任务的状态拼成一条消息给管理员。

2026-09-14 分家 + 表化（桌面清单第 4 项）。原来的毛病：4 个段落是**硬编码
1️⃣2️⃣3️⃣4️⃣ + 各写各的拼装**，加一个任务要同时改「任务本身」和「这里的拼装」两处，
容易漏 —— 序号还得手动顺延。现在段落由本文件的 **TASK_TABLE** 生成：
**加一个状态段落 = 表里加一行**，序号自动顺延，正文由该行的 `render` 提供。

⚠️ 本表只覆盖「状态命令要汇报的任务」，**不等于**全部调度任务的注册表：
注册目前散在两处 —— `bot.py::main`（auto_backup / join_verify_sweep /
observe_check_sweep / announce_sweep / lurker_sweep）与 `core/entry.py::post_init`
（daily_reset / leaderboard / season_settle / hourly_race / lottery /
poker_watchdog / data_save / delete_sweeper）。把「注册」也收进一张表要动启动接线
（不是"只搬不改"），留给用户拍板。

依赖 bot 命名空间一律走 `hub.X` 前导别名（延迟绑定 → 测试补丁实时穿透）。
"""

from core import hub

from datetime import datetime
import html

# ★ 显式 __all__：下面那些 _sec_* 是内部段落渲染器，不该算模块导出
#   （否则 test_seam_hub【5】会要求把它们也 re-export 进 bot.py）
__all__ = ["cmd_schedule_status"]


# 序号表情：加段落自动顺延（原来写死在正文里，加任务必须手改）
NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
                "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def _switch_text(*keys):
    """任务开关的展示文本；多个 key = 全开才算开（整点赛车有总开关 + 赛车总开关两道）。"""
    sget = hub.sget
    return "✅ 开" if all(sget(k) for k in keys) else "❌ 关"


def _sec_race(context):
    """① 整点自动赛车：总开关 + 时段 + 每群近况（开关 / 最近推送 / 跳过统计）。"""
    AUTHORIZED_GROUPS = hub.AUTHORIZED_GROUPS
    _race_window_text = hub._race_window_text
    chat_name_cache = hub.chat_name_cache
    hourly_race_enabled = hub.hourly_race_enabled
    race_last_sent = hub.race_last_sent
    race_skip_stats = hub.race_skip_stats
    sget = hub.sget
    out = [f"　总开关：{_switch_text('RACE_AUTO_ENABLED', 'RACE_ENABLED')}"
           f"　时段：{_race_window_text(sget('RACE_HOURLY_START'), sget('RACE_HOURLY_END'))}"
           f"　开赛分钟：{sget('RACE_HOURLY_MINUTE'):02d} 分"]
    if AUTHORIZED_GROUPS:
        for cid in sorted(AUTHORIZED_GROUPS):
            on = "✅" if hourly_race_enabled.get(cid, True) else "⏸"
            last = race_last_sent.get(cid) or "（暂无记录）"
            skips = race_skip_stats.get(cid, {})
            skip_txt = ""
            if skips:
                items = ", ".join(f"{k}×{v}" for k, v in skips.items())
                skip_txt = f"　跳过：{items}"
            out.append(f"　{on} <code>{cid}</code> "
                       f"{html.escape(str(chat_name_cache.get(cid) or str(cid)))}　最近推送：{last}{skip_txt}")
    else:
        out.append("　（无授权群）")
    return out


def _sec_reset(context):
    """② 每日重置：时刻 + 上次业务日。"""
    last_business_date = hub.last_business_date
    sget = hub.sget
    return [f"　时刻：{sget('DAILY_RESET_TIME')}　上次业务日：{last_business_date or '（未记录）'}"]


def _sec_backup(context):
    """③ 自动备份：间隔 + job_queue 是否真的可用。

    ⚠️ 必须真的查 job_queue：`requirements.txt` 少了 `[job-queue]` 扩展时
    `app.job_queue` 为 None → 备份任务**静默不注册**；只看间隔会以为它在跑。
    """
    sget = hub.sget
    jq = getattr(context.application, "job_queue", None)
    if jq is not None:
        return [f"　间隔：{sget('BACKUP_INTERVAL_HOURS')} 小时　job_queue：✅ 运行中"]
    return [f"　间隔：{sget('BACKUP_INTERVAL_HOURS')} 小时　job_queue：❌ 未启用"
            f"（需 python-telegram-bot[job-queue]）"]


def _sec_season(context):
    """④ 赛季结算：当前赛季 + 结束时刻（北京时间）。"""
    BEIJING_TZ = hub.BEIJING_TZ
    season_active = hub.season_active
    season_end_ts = hub.season_end_ts
    season_id = hub.season_id
    season_name = hub.season_name
    if not season_active:
        return ["　无进行中的赛季"]
    # 2026-09-12 用户「时间不是北京时间」→ 原来用 time.localtime（= 容器本地时区，
    # Northflank 上是 UTC，比北京时间少 8 小时），统一改成北京时间。
    end_str = datetime.fromtimestamp(int(season_end_ts), BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
    return [f"　当前赛季：<b>{html.escape(str(season_name))}</b>（ID {season_id}）　结束：{end_str}"]


# ── 任务表：**加一个状态段落 = 表里加一行** ────────────────────────────────
#   name   段落标题（序号由 NUMBER_EMOJI 渲染时自动配，不用手写）
#   period 周期说明（给人看的元数据；守护测试校验非空）
#   switch 开关设置项（元组；全开才算"开"。守护测试校验每个 key 都是真实的 schedule 设置项）
#   render 段落正文渲染器 → list[str]（第一行会直接接在标题后面）
TASK_TABLE = [
    {"key": "race", "name": "整点自动赛车", "period": "每小时",
     "switch": ("RACE_AUTO_ENABLED", "RACE_ENABLED"), "render": _sec_race},
    {"key": "reset", "name": "每日重置", "period": "每天 DAILY_RESET_TIME",
     "switch": ("DAILY_RESET_ENABLED",), "render": _sec_reset},
    {"key": "backup", "name": "自动备份", "period": "每 BACKUP_INTERVAL_HOURS 小时",
     "switch": ("BACKUP_ENABLED",), "render": _sec_backup},
    {"key": "season", "name": "赛季结算", "period": "赛季结束时",
     "switch": (), "render": _sec_season},
]


async def cmd_schedule_status(update, context):
    """管理员一键打印所有调度任务状态 + 每群最近推送。

    **段落与序号全部由 TASK_TABLE 生成** —— 加任务只改表，不用碰本函数的拼装。
    """
    # ── 依赖 bot 命名空间（延迟绑定 → 测试补丁实时穿透）──
    is_bot_admin = hub.is_bot_admin
    now_bj = hub.now_bj
    send_reply = hub.send_reply
    uid = update.effective_user.id if update.effective_user else 0
    if not is_bot_admin(uid):
        await send_reply(update, context, "⛔ 仅管理员可用。"); return
    now = now_bj()
    lines = ["<b>🕐 定时任务状态</b>", ""]
    for idx, row in enumerate(TASK_TABLE):
        num = NUMBER_EMOJI[idx] if idx < len(NUMBER_EMOJI) else f"{idx + 1}."
        body = list(row["render"](context))
        lines.append(f"<b>{num} {row['name']}</b>{body[0] if body else ''}")
        lines.extend(body[1:])
        lines.append("")
    lines.append(f"⏱ 当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}")
    await send_reply(update, context, "\n".join(lines))
