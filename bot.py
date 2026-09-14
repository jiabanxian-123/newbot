import asyncio
import contextvars
import html
import io
import json
# 版本标记：/health 与登录页底部都会显示，用于一眼核对"线上跑的是不是最新代码"
BOT_VERSION = "2026-09-12-1720"
# 主题色：key -> (主色, 深主色, 强色上的文字色, 页面底色, 侧栏底, 卡片底, 输入框底, 边框, 表头底, 悬停底)
# 网页顶栏色点一键切换，存 SETTINGS_SNAPSHOT["ui_theme"] 持久化；整套色板全量生效，不是只换 accent
_UI_THEMES = {
    "purple": ("#8b5cf6", "#6d3fd4", "#ffffff", "#161320", "#1b1728", "#211c30", "#191527", "#352e4d", "#28223d", "#262038"),
    "blue":   ("#3b82f6", "#2563eb", "#ffffff", "#131722", "#161c2a", "#1b2231", "#171d2a", "#2c3a54", "#202a40", "#212b3e"),
    "cyan":   ("#06b6d4", "#0e7490", "#ffffff", "#0f181c", "#121f26", "#17252c", "#131f26", "#25414c", "#1b3039", "#1a2b33"),
    "green":  ("#10b981", "#047857", "#ffffff", "#111813", "#141d17", "#1a251d", "#151d17", "#294233", "#1d3125", "#1b2a20"),
    "rose":   ("#f43f5e", "#be123c", "#ffffff", "#1a1215", "#1e1519", "#261a1f", "#1e1519", "#432c37", "#33222b", "#2a1d23"),
    "amber":  ("#f59e0b", "#d97706", "#ffffff", "#181510", "#1c1811", "#242017", "#1d1911", "#42391f", "#322b18", "#2b251a"),
    "black":  ("#7b8194", "#3f4453", "#ffffff", "#121214", "#161617", "#1b1b1e", "#161617", "#2c2c31", "#222225", "#1f1f23"),
    "white":  ("#dbe0ea", "#aab2c2", "#1f2430", "#131419", "#17181e", "#1d1f26", "#17181e", "#2f323d", "#232630", "#20222b"),
}
# 成员列表首字母头像色环（按 uid 取模固定颜色，同人永远同色）
_AV_COLORS = ("#8b5cf6", "#3b82f6", "#10b981", "#f59e0b", "#f43f5e", "#06b6d4", "#ec4899", "#a3e635")
_group_admins_cache = {}   # cid -> (拉取时间戳, {uid: "owner"|"admin"})，网页成员列表徽章用（5 分钟缓存）
import logging
import math
import os
import random
import re
import secrets
import shutil
import sys
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatPermissions
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import Application, CallbackQueryHandler, ChatJoinRequestHandler, ChatMemberHandler, MessageHandler, filters
from treys import Card, Evaluator

# ---------- 抽缝：把本模块命名空间交给 core/ 下抽出的模块 ----------
# 【为什么】测试用 `spec_from_file_location("m", BOT)` 加载本文件，再 `m.xxx = fake`
# 打补丁（共 815 个补丁点）。抽到 core/ 的代码若按值引用本模块符号，补丁会静默失效。
# 这里交出 globals()（就是模块的 __dict__ 本身），core/hub.py 用延迟查表代理，
# 于是 `m.xxx = fake` 对抽出的模块同样实时生效。详见 core/hub.py。
from core import hub as _core_hub
_core_hub.bind(globals())

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from core.entry import (  # noqa: E402
    _dispatch_alias,
    _parse_target_amount,
    cb_match,
    cmd_cx,
    cmd_help,
    cmd_ph,
    cmd_record,
    cmd_sq,
    cmd_start,
    cmd_status,
    on_app_error,
    on_button,
    on_media,
    on_rank_page,
    on_text,
    post_init,
    post_shutdown,
    route_command,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from core.messaging import (  # noqa: E402
    _bot_api,
    _flush_deletes,
    _fmt_tpl,
    _in_bot_loop,
    _is_network_error,
    _net_error_tick,
    _open_tags_after,
    _safe_html_clip,
    _spawn_background,
    action_notice,
    announce_sweep,
    announce_turn,
    cancel_scheduled_delete,
    clip_name,
    own_messages,
    restore_pending_deletes,
    safe_delete,
    safe_edit,
    safe_send,
    safe_send_long,
    safe_send_photo,
    schedule_delete,
    schedule_delete_ids,
    schedule_notice_delete,
    send_reply,
    send_settle,
    send_settle_rank,
    split_telegram_text,
    user_link,
    RANK_NAME_MAX,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from core.settings import (  # noqa: E402
    _bind_update_cid,
    _cross_keys,
    _fn_legit_aliases,
    _group_settings_json,
    _grp_title,
    _load_settings_payload,
    _looks_like_settings_snapshot,
    _migrate_legacy_autodel,
    _migrate_level_demote_switch,
    _migrate_rank_alias_str,
    _migrate_rank_rename,
    _multi_has,
    _multi_set,
    _normalize_settings_values,
    _record_setting_change,
    _safe_cid,
    _SGET_UNKNOWN_WARNED,
    _sync_cmd_globals_from_aliases,
    _sync_dyn_aliases,
    _warn_unknown_setting,
    _write_settings_file,
    apply_command_aliases,
    apply_settings,
    cur_cid,
    group_ctx,
    group_ctx_async,
    group_effective,
    group_get,
    group_set,
    load_settings,
    save_group_settings,
    save_settings,
    sget,
    sget_key,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from core.web import (  # noqa: E402
    _app_css,
    _all_user_options,
    _admin_page,
    _admin_receivers,
    _botlog_page,
    _client_ip,
    _daily_reset_change_note,    # P2-12 折中方案：改每日重置时间时弹「下次生效」提示
    _esc,
    _field_rows,
    _group_options,
    _guard_badge,
    _guard_modal,
    _guard_row,
    _hash_web_pwd,
    _home_page,
    _id_picker_js,
    _login_page,
    _members_body,
    _otp_page,
    _page,
    _parse_multipart,
    _pwd_ok,
    _redeem_buy_cb,
    _Req,
    _resolve_web_uid,
    _savebar,
    _search_box,
    _settings_search_block,
    _settings_search_index,
    _pg,
    _sort_js,
    _tpl_preview,
    _web_page_bounds,
    cmd_webcode,
    cmd_weblogin,
    start_health_server,
    WEB_LIST_PAGE_SIZE,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from core.data import (  # noqa: E402
    _data_file_status,
    _flow_ts_full,
    _other_slot,
    _parse_dt_bj,
    _parts_root,
    _read_parts_pointer,
    _read_shard_meta,
    _read_slot,
    _slot_dir,
    _slot_mtime,
    _single_file_is_newer,
    _write_parts_pointer,
    archive_old_profit_data,
    auto_backup,
    business_date,
    cmd_backup,
    cmd_restore,
    data_save_worker,
    data_size_kb,
    force_save_now,
    ledger_add,
    load_data,
    load_parts,
    now_bj,
    parse_hm,
    race_id,
    read_snapshot,
    record_game_flows,
    restore_nested,
    save_data,
    save_parts,
    total_profit_by_game,
    PART_FILE_GROUPS,
    PART_SLOTS,
    SCHEMA_VERSION,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from core.members import (  # noqa: E402
    _admin_notify_ids,
    _extract_name,
    _group_admins_get,
    _group_admins_get_async,
    _is_group_admin,
    _remember_name,
    _warm_group_names,
    get_name,
    is_auth,
    is_bot_admin,
    is_group_chat,
    need_auth,
    require_group_chat,
)

from core.member_utils import (  # noqa: E402
    _chat_is_effective,
    _cleanup_left_member_games,
    _is_service_message,
    _join_user_obj,
    _msg_kind,
    _nm_r,
    _peer_brief,
    _user_has_avatar,
)

from core.modcmds import (  # noqa: E402
    _admin_log,
    _mod_punish,
    cmd_add,
    cmd_addadmin,
    cmd_autosm,
    cmd_ban,
    cmd_deladmin,
    cmd_god,
    cmd_god_grant,
    cmd_god_revoke,
    cmd_groupban,
    cmd_groupunban,
    cmd_mute,
    cmd_qxshouquan,
    cmd_unban,
    cmd_unmute,
    cmd_whitelist,
    cmd_whitelist_add,
    cmd_whitelist_del,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.autoreply import (  # noqa: E402
    _autoreply_enforce,
    _keyword_reply_match,
    _parse_keyword_rules,
)
from features.antispam import (  # noqa: E402
    _antispam_check,
    _antispam_hit,
    _antispam_norm,
    _antispam_prune,
    _captcha_render,
    _clean_service_msg,
    _forcesub_enforce,
    _fsub_links,
    _fsub_parse,
    _fsub_probe,
    _is_join_transition,
    _join_gate_check,
    _join_verify_handle_text,
    _join_verify_pass,
    _join_verify_start,
    _jv_options,
    _jv_wrong_hit,
    _link_domains,
    _link_whitelisted,
    _normalize_for_match,
    _observe_enforce,
    _raid_active,
    _raid_on_join,
    _raid_recover,
    _report_target_text,
    _report_throttled,
    _sensitive_enforce,
    _sensitive_hit,
    cmd_jv_pass,
    cmd_report,
    join_verify_sweep,
    observe_check_sweep,
    on_join_request,
    on_left_member_msg,
    on_member_event,
    on_my_chat_member,
    on_new_members_msg,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.points import (  # noqa: E402
    _award_chat_points,
    _buy_settle,
    _check_level_change,
    _check_level_drop_on_spend,
    _deep_buy_url,
    _deep_mall_start,
    _deep_redeem_start,
    _deep_start_confirm,
    _earn_add,
    _earn_get,
    _exchange_rate_text,
    _get_level,
    _grant_newbie_reward,
    _level_allows,
    _level_base,
    _level_item,
    _level_msg_enforce,
    _level_msg_hit,
    _level_of,
    _level_perms,
    _level_rank,
    _level_sync_member_tag,
    _level_violation_hit,
    _mall_dm_ok,
    _mall_items_on,
    _mall_panel,
    _mall_price,
    _normalize_levels,
    _parse_dm_redeem_data,
    _parse_points_rows,
    _point_adj_log_card,
    _redeem_dm_ok,
    _redeem_execute,
    _redeem_gate,
    _redeem_items_for,
    _rp_grab,
    _set_level_enabled,
    all_titles,
    cmd_buy_points,
    cmd_equip,
    cmd_inherit,
    cmd_mall,
    cmd_mall_buy,
    cmd_my_level,
    cmd_my_points,
    cmd_my_titles,
    cmd_points_flow,
    cmd_points_rank,
    cmd_points_redeem,
    cmd_redeem,
    cmd_redpacket,
    cmd_shop,
    cmd_sign,
    cmd_sign_rank,
    grant_title,
    revoke_title,
    title_icon,
    title_prefix,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.invite import (  # noqa: E402
    _deep_invite_start,
    _inv_dbg,
    _inv_notice_fresh,
    _invite_ask_inviter,
    _invite_attribute,
    _invite_award,
    _invite_card_body_kb,
    _invite_count,
    _invite_daily_add,
    _invite_daily_capped,
    _invite_daily_get,
    _invite_find_invitee,
    _invite_flag_ad,
    _invite_ping_qualify,
    _invite_progress_text,
    _invite_push_card_to_private,
    _invite_qualify_ready,
    _invite_rank_rows,
    _invite_refresh_all,
    _invite_send_progress_card,
    _invite_send_rank,
    _invite_track_join,
    _invite_try_award,
    _inviter_awarded_count,
    _inviter_frozen,
    _rec_awarded,
    _rec_ok,
    _rec_qualified,
    _rec_rejected,
    _rec_valid,
    cmd_invite_debug,
    cmd_invite_link,
    cmd_invite_rank_all,
    cmd_invite_rank_month,
    cmd_invite_rank_today,
    cmd_invite_report,
    cmd_invite_test,
    cmd_my_invite,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.season import (  # noqa: E402
    _deep_season_exchange_start,
    _season_base,
    _season_blind,
    _season_exchange_execute,
    _season_exchange_panel,
    _season_has_active_games,
    _season_param,
    _season_rule,
    cmd_season_end,
    cmd_season_exchange,
    cmd_season_help,
    cmd_season_join,
    cmd_season_play,
    cmd_season_points,
    cmd_season_rank,
    cmd_season_start,
    render_season_lobby,
    season_daily_refresh,
    season_lobby_content,
    season_settle,
    season_settle_scheduler,
    season_signup,
    season_standings_lines,
    season_total_profit,
    start_season,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.lottery import (  # noqa: E402
    _lottery_active,
    _lottery_admin_subcommand,
    _lottery_announce_text,
    _lottery_create,
    _lottery_draw,
    _lottery_form_parse,
    _lottery_join,
    _lottery_kb,
    _lottery_match_join_words,
    _lottery_parse_end,
    _lottery_parse_prizes,
    _lottery_publish,
    _lottery_refresh_announce,
    _lottery_render_prizes,
    _lottery_try_join,
    _migrate_stale_lottery_tpl,
    cmd_lottery,
    lottery_scheduler,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.jinhua import (  # noqa: E402
    JinhuaGame,
    _refresh_jinhua_table,
    _sync_jinhua_msg,
    cmd_jinhua,
    jinhua_buttons,
    jinhua_seen_mult,
    jinhua_table_text,
    jinhua_turn_notice,
    jinhua_waiting_text,
    refund_jinhua,
    settle_jinhua,
    show_jinhua_action,
    start_jinhua_turn_timer,
    start_jinhua_wait_timeout,
    update_jinhua_table,
    update_jinhua_waiting,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.texas import (  # noqa: E402
    PokerGame,
    _badges,
    _card,
    _font,
    _game_gate,
    _png,
    _poker_quick_amounts,
    _poker_settle_lines,
    _poker_watchdog_tick,
    _rrect,
    _suit_rank,
    card_face,
    cmd_dz,
    cmd_end,
    current_game_mode,
    deal_hand_cards,
    game_mutex_enabled,
    game_mutex_running,
    game_mutex_wait_idle,
    handle_texas_reveal,
    hand_popup,
    panel_adopt,
    player_is_busy,
    player_line,
    poker_buttons,
    poker_room_of,
    poker_table_text,
    poker_turn_notice,
    poker_waiting_text,
    poker_watchdog,
    refund_poker,
    render_poker_table,
    reveal_img,
    retire_panel,
    send_hand_card,
    settle_poker,
    showdown_img,
    start_turn_timer,
    start_wait_timeout,
    table_img,
    update_poker_table,
    update_poker_waiting,
    user_wallet_locks,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.dice import (  # noqa: E402
    DiceGame,
    _dice_ante_for,
    _dice_del_bid_msg,
    _dice_duel_mode,
    _dice_is_straight,
    _dice_leopard_bonus,
    _dice_leopard_kind,
    _dice_notify_dropped,
    _dice_resolve_and_continue,
    _dice_rule_lv_on,
    _dice_rule_on,
    cmd_dice,
    dice_active_rules,
    dice_buttons,
    dice_min_raise,
    dice_rules_text,
    dice_table_text,
    dice_turn_notice,
    dice_waiting_text,
    parse_dice_bid,
    refund_dice,
    settle_dice,
    show_dice_action,
    start_dice_turn_timer,
    start_dice_wait_timeout,
    update_dice_waiting,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.race import (  # noqa: E402
    HorseRace,
    _auto_race_tick,
    _race_hour_in_window,
    _race_window_text,
    cmd_sm,
    hourly_race_scheduler,
    race_rake_split,
    race_subsidy_banner,
    race_subsidy_for,
    race_subsidy_split,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.blackjack import (  # noqa: E402
    BlackjackGame,
    bj_turn_notice,
    build_blackjack_wait_board,
    cmd_21,
    start_bj_turn_timer,
    start_bj_wait_timeout,
    update_blackjack_ui,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.autodel import (  # noqa: E402
    _autodel_decide,
    _autodel_default_secs,
    _autodel_enforce,
    _autodel_media_hit,
    _autodel_text_hit,
    install_autodelete_default,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.tag import (  # noqa: E402
    _sync_tags_report,
    _tag_call,
    _tag_err_hint,
    _tag_eta_seconds,
    _tag_group_admins,
    _tag_is_expected_skip,
    _tag_retry_after,
    _tag_sanitize,
    _tag_sync_line,
    cmd_sync_tags,
    sync_member_tags,
    sync_member_tags_notify,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.schedule import (  # noqa: E402
    cmd_schedule_status,
    daily_reset_scheduler,
    emergency_if_needed,
    lurker_sweep,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.rake import (  # noqa: E402
    calc_rake,
    commit_rake,
)

# ---------- 抽出的功能域：re-export（bot.xxx 必须仍是同一对象）----------
from features.rank import (  # noqa: E402
    _rank_delete_secs,
    _rank_keyboard,
    _rank_page_text,
    _rank_rearm,
    _rank_source,
    broadcast_big_win,
    cmd_admin_list,
    cmd_adminlist_tg,
    cmd_auth_list,
    cmd_banlist,
    cmd_conflicts,
    cmd_list_all,
    leaderboard_scheduler,
    rank_line,
    rank_marker,
    send_rank_page,
)
# ★ 再留一个 `hub` 名字：测试会用
#   `types.FunctionType(嵌套函数的 code, bot.__dict__)` 重建被抽出的嵌套函数
#   （test_ghost_group.py / test_repo_hygiene.py），那些 code 里引用的是裸 `hub`，
#   只有 bot 命名空间里存在 `hub` 才解析得到。
hub = _core_hub

# 抽出的纯计算模块（re-export，保持 bot.xxx 可访问 —— 补丁面依赖这一点）
from core.cards import (  # noqa: E402
    card_str, side_pots, distribute_side_pots,
    evaluate_jinhua, is_235, jinhua_winners, compare_jinhua_pair,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------- 全局配置中心 ----------
GAME_STARTING_CHIPS = 50000  # 统一积分初始值（首次使用自动获得）

MIN_ENTRY_CHIPS = 200
EMERGENCY_CHIPS = 2000
EMERGENCY_MAX_USES = 3
EMERGENCY_MIN_GAMES = 0     # 归零赠送要求「累计玩过 N 局」（0=不限）；防小号纯靠归零薅分

# 游戏时间配置 (秒)
TURN_TIMEOUT = 60          # 德州/21点单回合思考时间
JINHUA_OPEN_PENDING_TIMEOUT = 600  # 金花跟平阶段（open_pending）超时自动开牌，防全员掉线牌局卡死
ROOM_WAIT_TIMEOUT = 60     # 各游戏等待房统一倒计时（60秒）
RACE_AUTO_START = 120      # 赛车自动开赛时间
RACE_ANIMATION_INTERVAL = 5.0   # 每帧画面停留秒数（间隔越大帧数越少，需与赛程总时长一起权衡）

# 游戏金额配置
BJ_MIN_BET = 100           # 21点最低打字下注
BJ_JOIN_BETS = [500, 1000] # 21点加入下注按钮档位（网页可改）
FIXED_MIN_RAISE = 100      # 德州最低加注额
BLACKJACK_DECKS = 6        # 21点使用6副牌（娱乐场标准）

# 其他配置
DEFAULT_ADMIN = 8929733838
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", DEFAULT_ADMIN))
# Bot 管理员：种子集合（始终为管理员，防锁死）+ 可动态增删的持久化集合
ADMIN_USER_IDS = {ADMIN_USER_ID}  # 种子管理员，重启后自动恢复，无法被 /deladmin 移除
BOT_ADMINS = set(ADMIN_USER_IDS)  # 运行时管理员集合 = 种子 ∪ 持久化新增，可经 /addadmin /deladmin 动态管理
SMALL_BLIND, BIG_BLIND, ANTE = 0, 0, 200
# 4 游戏总开关/仅管理员开局（后台各游戏分组可调，保存立即生效）
TEXAS_ENABLED, TEXAS_ADMIN_ONLY = 1, 0
# 德州两个模式各自独立开关（用户要求：可以只开赛季、关日常，随时切换）
DAILY_TEXAS_ENABLED, RANKED_TEXAS_ENABLED = 1, 1
BJ_ENABLED, BJ_ADMIN_ONLY = 1, 0
JINHUA_ENABLED, JINHUA_ADMIN_ONLY = 1, 0
RACE_ENABLED, RACE_ADMIN_ONLY = 1, 0
STALE_TEXT_COMMAND_SECONDS = 120
# ---------- 德州赛季 ----------
SEASON_START_CHIPS = 20000     # 赛季起始分（独立账本，7天不清零）
SEASON_MIN_PLAYERS = 20        # 报名满 20 人自动开赛
SEASON_MIN_GAMES = 5           # 上榜最少局数
SEASON_REBUY_COUNT = 3         # 破产应急补分次数
SEASON_REBUY_AMOUNT = 2000     # 每次应急补分
SEASON_DAYS = 7                # 赛季周期（天）
SEASON_BET_PERCENT = 0.2       # 赛季单局每人投入上限 = 本局落座玩家筹码总和 × 此比例（人少上限低，防串通）
# 赛季德州独立参数（用户要求：赛季可单独调规则，不用动日常德州）
# 0 = 继承日常德州对应设置；填了非 0 值则赛季局用这里的值
SEASON_MIN_ENTRY_CHIPS = 0     # 入座最低赛季分（0=沿用「赛季分>0」的原有判定）
SEASON_FIXED_MIN_RAISE = 0     # 最低加注额
SEASON_TURN_TIMEOUT = 0        # 单回合思考时间（秒）
SEASON_ROOM_WAIT_TIMEOUT = 0   # 等待房倒计时（秒）
SEASON_SMALL_BLIND = 0         # 小盲注（0=继承；日常也是 0 时表示不设盲注）
SEASON_BIG_BLIND = 0           # 大盲注（同上）
SEASON_ANTE = 0                # 前注（每人发牌前强制投入）
# ---------- 聊天积分兑换赛季分 ----------
RANKED_EXCHANGE_ENABLED = 1    # 兑换开关
RANKED_EXCHANGE_COST = 1       # 兑换比例-消耗的聊天积分（分母）
RANKED_EXCHANGE_GAIN = 1       # 兑换比例-得到的赛季分（分子）→ 默认 1:1
RANKED_EXCHANGE_DAILY_LIMIT = 0  # 每人每日兑换上限（按消耗的聊天积分累计，0=不限）
# 自适应数据持久化路径：HF Spaces 开启持久化(/data 存在)→用 /data；否则用当前目录(Serv00/本地均为真实磁盘，持久)
_data_candidates = [
    os.environ.get("DATA_FILE"),
    "/data/bot_data.json" if os.path.isdir("/data") else None,
    "bot_data.json",
    os.path.join(os.path.expanduser("~"), "bot_data.json"),
]
DATA_FILE = next((p for p in _data_candidates if p), "bot_data.json")
# --------------------------------

# ---------- 赛车/德州常量 ----------
HORSE_COUNT = 4
HORSE_NAMES = ["轿车", "出租", "越野", "皮卡"]
HORSE_EMOJI = ["🚗", "🚕", "🚙", "🛻"]
FIXED_BET_AMOUNTS = [100, 200, 500, 1000]
RACE_TRACK_LENGTH = 14
DATA_BACKUP_FILE, DATA_TEMP_FILE = f"{DATA_FILE}.bak", f"{DATA_FILE}.tmp"

# ---------- 网页后台：可在线调整的设置 ----------
# 设置存在独立文件 bot_settings.json，网页保存后立即覆盖内存中的全局常量，无需重启。
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(DATA_FILE)), "bot_settings.json")
# 设置快照：与 bot_data.json 同步持久化。
# 无持久磁盘的平台（Northflank 等）重建容器会清空 bot_settings.json，导致每次重新部署后
# 网页设置全部回退成代码默认值。把整份设置快照嵌进 bot_data.json 的 _settings 键后，
# 设置就能跟着数据一起被自动备份 / /restore 恢复，重新部署不再丢设置。
SETTINGS_SNAPSHOT = {}
WEB_DEFAULT_PASSWORD = "qwer1234"  # 首次登录用，登录后请在面板里立即修改
# 左侧菜单分组：(分组键, 显示名, 图标)
SETTINGS_GROUPS = [
    ("dashboard", "群体总览",   "📊"),
    ("texas",     "德州扑克",   "🃏"),
    ("blackjack", "21点",      "♠️"),
    ("jinhua",    "炸金花",     "♣️"),
    ("dice",      "大话骰",     "🎲"),
    ("race",      "赛车",       "🏎️"),
    ("rake",      "游戏抽水",   "💸"),
    ("points",    "积分系统",   "💰"),
    ("lottery",   "群组抽奖",   "🎉"),
    ("invite",    "邀请系统",   "🎟️"),
    ("season",    "赛季",     "🏆"),
    ("members",   "群组管理",   "👥"),
    ("mod",       "群管中心",   "🛡️"),
    ("autodel",   "自动删除",   "🗑️"),
    ("schedule",  "定时任务",   "⏰"),
    ("commands",  "命令管理",   "⌨️"),
    ("tpls",      "话术库",     "💬"),
    ("general",   "通用与应急", "⚙️"),
    ("admin",     "管理员中心", "👑"),
    ("security",  "安全",       "🔒"),
    ("botlog",    "谁拉机器人", "🔗"),
]
# 子页面制：有子页的分组在侧边栏折叠展开（照阿福模板）。None=未开通占位页
SIDEBAR_ORDER = []   # 侧边栏自定义排序（组键列表，网页「群体总览」可 ▲▼ 调整，随设置持久化）
SIDEBAR_CHILDREN = {"texas": ["season"]}  # 把某些独立组折叠进父组显示（路由不变）：赛季归入德州
# 群管中心（跨组聚合页）直接内嵌的高频开关；新增群管功能时往这里加键即可
MOD_PAGE_FIELDS = []
# ---------- 页面入口登记表（防「该上线的没上线」） ----------
# 自动删除页的 4 个弹窗各自负责哪些设置键（弹窗 = 该页的**唯一**入口）。
# ⚠️ 该页没有主表单（只渲染「防护类型」一览 + 弹窗），任何一个 autodel 字段只要不在这 4 个
#    集合里，就等于「后台永远改不了它」——2026-09-11 实测踩到：autodel_default_seconds
#    （默认回收总开关）与 rank_delete_seconds（榜单回收）两个字段谁也不认领，页面上根本没有。
#    新增字段请加进对应集合；test_web_field_reachable.py 会强制校验，漏了直接红。
AUTODEL_TAB_RECYCLE = {
    "panel_delete_seconds", "autodel_default_seconds", "points_delete_seconds",
    "reply_delete_seconds", "settle_delete_seconds", "race_notice_delete_seconds",
    "rank_delete_seconds",
}
AUTODEL_TAB_ANTISPAM = {
    "antispam_enabled", "antispam_repeat_n", "antispam_window", "antispam_min_len",
    "antispam_timer_n", "antispam_timer_tol", "antispam_mute_seconds",
    "antispam_mute_escalate", "antispam_notice_seconds",
}
AUTODEL_TAB_TEXT = {"autodel_text_rules", "autodel_long_len", "autodel_text_seconds"}
AUTODEL_TAB_MEDIA = {"autodel_media_types", "autodel_media_seconds"}
AUTODEL_TAB_SETS = {
    "md_recycle": AUTODEL_TAB_RECYCLE,
    "md_antispam": AUTODEL_TAB_ANTISPAM,
    "md_textrule": AUTODEL_TAB_TEXT,
    "md_mediarule": AUTODEL_TAB_MEDIA,
}
# mod 页「移进弹窗、主表单不再渲染」的键 = 主表单剔除名单的**唯一来源**。
# 别再写第二份内联字面量：曾经页面里外各写一份，改一处漏一处 ⇒ 保存一个弹窗把没提交的
# 开关静默清零（2026-09-09 用户报障真凶）。
MOD_MODAL_KEYS = {
    "sensitive_enabled", "sensitive_words", "sensitive_action", "sensitive_mute_seconds",
    "link_whitelist_enabled", "link_whitelist", "sep_mod_word",
}
# 侧边栏四大节（照阿福/方丈：节标题 + 节内菜单项）。不在任何节里的组保持原样渲染在最后。
# 排序逻辑：机器人日常 → 群治理(成员/群管/删除) → 增长与经济(邀请/积分/抽奖) → 娱乐游戏 → 系统管理殿后
SIDEBAR_SECTIONS = [
    ("🤖 机器人设置", ["dashboard", "schedule", "commands", "tpls", "general"]),
    ("👥 群组设置",   ["members", "mod", "autodel", "invite", "points", "lottery", "botlog"]),
    ("🎲 娱乐功能",   ["texas", "blackjack", "jinhua", "dice", "race", "rake"]),
    ("🛠 系统管理",   ["admin", "security"]),
]
SUBPAGES = {
    "points": [
        ("set",      "积分设置"),
        ("adjust",   "积分管理"),
        ("impexp",   "积分导入导出"),
        ("cap",      "积分每日上限"),
        ("sign",     "每日签到"),
        ("rule",     "积分规则"),
        ("rp",       "积分红包"),
        ("level",    "积分等级"),
        ("levelguard", "等级消息管控"),
        ("inherit",  "积分继承"),
        ("redeem",   "积分兑换"),
        ("mall",     "积分商城"),
        ("mallord",  "商城订单"),
        ("buy",      "购买积分"),
        ("buypkg",   "积分套餐管理"),
    ],
    "invite": [
        ("config",  "邀请链接配置"),
        ("records", "邀请记录"),
        ("daily",   "统计"),
        ("summary", "汇总"),
        ("qualify", "合格结算"),
    ],
    "members": [
        ("mlist",   "群组成员列表"),
        ("titg",    "称号加封"),
        ("records", "进出与申请"),
        ("ops",     "白名单与操作"),
        ("join",    "入群与观察"),
    ],
    "admin": [
        ("admins",    "Bot 管理员"),
        ("auth",      "授权群管理"),
        ("blacklist", "拉黑管理"),
        ("god",       "赌神称号"),
        ("seasonpts", "赛季分调整"),
        ("fundflow",  "资金流审查"),
    ],
}
# ===== 多选字段（类型 "multi"）的可选项：settings键 -> [(值, 显示名), ...] =====
# 值只允许英文小写+下划线，渲染成一组勾选框，保存为逗号分隔字符串（顺序按这里定义）。
MULTI_OPTIONS = {
    "autodel_text_rules": [
        ("link", "链接消息(http/t.me/链接实体)"),
        ("long", "超长消息"),
        ("premium_emoji", "会员表情(自定义表情)"),
    ],
    "autodel_media_types": [
        ("photo", "图片"),
        ("video", "视频"),
        ("sticker", "贴纸"),
        ("gif", "动图(GIF)"),
        ("voice", "语音/视频圆"),
        ("document", "文档文件"),
        ("archive", "压缩包(zip/rar/7z)"),
        ("executable", "可执行文件(exe/apk)"),
        ("contact", "分享联系人"),
        ("service", "系统消息(入退群/改群名)"),
    ],
}
SETTINGS_FIELDS = [
    # (settings键, 模块全局变量名, 面板显示名, 类型, 最小, 最大, 所属分组)
    ("min_entry_chips",         "MIN_ENTRY_CHIPS",         "入座最低积分",              "int",   0,   100000,  "texas"),
    ("fixed_min_raise",         "FIXED_MIN_RAISE",         "最低加注额",                "int",   10,  10000,   "texas"),
    ("turn_timeout",            "TURN_TIMEOUT",            "单回合思考时间(秒·德州/21点共用)", "int", 10, 600, "texas"),
    ("room_wait_timeout",       "ROOM_WAIT_TIMEOUT",       "等待房倒计时(秒)",          "int",   10,  600,     "texas"),
    ("bj_min_bet",              "BJ_MIN_BET",              "最低下注",                  "int",   1,   100000,  "blackjack"),
    ("bj_join_bets",            "BJ_JOIN_BETS",            "加入下注按钮金额(逗号分隔)", "bets",  0,   0,       "blackjack"),
    ("blackjack_decks",         "BLACKJACK_DECKS",         "使用几副牌",                "int",   1,   8,       "blackjack"),
    ("bj_enabled",              "BJ_ENABLED",              "21点开关",                  "bool",  0,   1,       "blackjack"),
    ("bj_admin_only",           "BJ_ADMIN_ONLY",           "21点仅管理员开局",          "bool",  0,   1,       "blackjack"),
    ("jinhua_ante",             "JINHUA_ANTE",             "底注",                      "int",   1,   100000,  "jinhua"),
    ("jinhua_base",             "JINHUA_BASE",             "单注基准",                  "int",   1,   100000,  "jinhua"),
    ("jinhua_seen_double",      "JINHUA_SEEN_DOUBLE",      "看牌者投注加倍开关",        "bool",  0,   1,       "jinhua"),
    ("jinhua_enabled",          "JINHUA_ENABLED",          "炸金花开关",                "bool",  0,   1,       "jinhua"),
    ("jinhua_admin_only",       "JINHUA_ADMIN_ONLY",       "炸金花仅管理员开局",        "bool",  0,   1,       "jinhua"),
    ("jinhua_open_timeout",     "JINHUA_OPEN_PENDING_TIMEOUT", "跟平阶段超时自动开牌(秒)", "int", 30, 3600, "jinhua"),
    # ---------- 大话骰（吹牛·港式标准） ----------
    ("dice_ante",               "DICE_ANTE",               "底注(开局一次性扣进奖池)",  "int",   1,   100000,  "dice"),
    ("dice_ante_duel",          "DICE_ANTE_DUEL",          "两人局底注(0=用上面那个底注)", "int", 0, 100000, "dice"),
    ("dice_ante_multi",         "DICE_ANTE_MULTI",         "多人局底注(3人及以上,0=用上面那个底注)", "int", 0, 100000, "dice"),
    ("dice_dice_count",        "DICE_DICE_COUNT",         "每人骰子数",                "int",   1,   10,      "dice"),
    ("dice_wild_one",          "DICE_WILD_ONE",           "1万能牌开关(关=无万能局,首手可叫1)", "bool", 0, 1, "dice"),
    ("dice_straight_zero",     "DICE_STRAIGHT_ZERO",      "顺子算0个(0关/1仅两人局/2所有人数;含1补位的假顺)", "int", 0, 2, "dice"),
    ("dice_leopard_bonus",     "DICE_LEOPARD_BONUS",      "豹子加成(0关/1仅两人局/2所有人数;纯豹+2花豹+1)", "int", 0, 2, "dice"),
    ("dice_drop_dice",         "DICE_DROP_DICE",          "掉骰子多轮制(0关=任何人数都一把定胜负;1开=三人以上掉骰)", "bool", 0, 1, "dice"),
    ("dice_think_seconds",     "DICE_THINK_SECONDS",      "叫牌思考秒数(超时自动开骰)", "int",   10,  600,     "dice"),
    ("dice_max_players",       "DICE_MAX_PLAYERS",        "单桌最多人数",              "int",   2,   20,      "dice"),
    ("dice_enabled",           "DICE_ENABLED",            "大话骰开关",                "bool",  0,   1,       "dice"),
    ("dice_admin_only",        "DICE_ADMIN_ONLY",         "大话骰仅管理员开局",        "bool",  0,   1,       "dice"),
    ("race_auto_start",         "RACE_AUTO_START",         "自动开赛时间(秒)",          "int",   10,  600,     "race"),
    ("race_animation_interval", "RACE_ANIMATION_INTERVAL", "动画帧间隔(秒)",            "float", 0.5, 30,      "race"),
    ("horse_count",             "HORSE_COUNT",             "赛马数量(匹)",              "int",   2,   8,       "race"),
    ("horse_names",             "HORSE_NAMES",             "赛马名称(逗号分隔)",        "names", 0,   0,       "race"),
    ("horse_emoji",             "HORSE_EMOJI",             "赛马表情(逗号分隔)",        "emoji", 0,   0,       "race"),
    ("race_track_length",       "RACE_TRACK_LENGTH",       "赛道长度(格)",              "int",   5,   50,      "race"),
    ("fixed_bet_amounts",       "FIXED_BET_AMOUNTS",       "下注按钮金额(逗号分隔)",    "bets",  0,   0,       "race"),
    ("race_odds_cap",           "RACE_ODDS_CAP",           "赔率上限(倍,0=无上限)",     "float", 0,   100,     "race"),
    ("race_odds_rate_weight",   "RACE_ODDS_RATE_WEIGHT",   "赔率里「胜率」的权重(%;0=只看押注金额=旧行为,100=只看胜率)", "int", 0, 100, "race"),
    ("race_parimutuel",         "RACE_PARIMUTUEL",         "押注池赔率(1=赔率=总池÷该马注额,押得少赔率高,派彩合计=总池;0=旧模型按胜率)", "bool", 0, 1, "race"),
    ("race_rake_percent",       "RACE_RAKE_PERCENT",       "赛车抽水比例(%·按派彩抽,钱不销毁而是滚进底池下期发;0=不抽)", "int", 0, 50, "race"),
    ("race_enabled",            "RACE_ENABLED",            "赛车开关",                  "bool",  0,   1,       "race"),
    ("race_subsidy_enabled",    "RACE_SUBSIDY_ENABLED",    "赛车系统加奖开关(每场给奖池加钱拉人气)", "bool", 0, 1, "race"),
    ("race_subsidy_amount",     "RACE_SUBSIDY_AMOUNT",     "赛车系统加奖金额(每场,押中者按注额分)", "int", 0, 100000, "race"),
    ("race_subsidy_min_players","RACE_SUBSIDY_MIN_PLAYERS","加奖生效最少下注人数(防单人薅)", "int", 1, 50, "race"),
    ("race_subsidy_daily_cap",  "RACE_SUBSIDY_DAILY_CAP",  "加奖每日上限(每群,0=不限)", "int", 0, 10000000, "race"),
    ("race_subsidy_auto_only",  "RACE_SUBSIDY_AUTO_ONLY",  "加奖仅限自动开赛(个人发起的赛车不派奖)", "bool", 0, 1, "race"),
    ("race_admin_only",         "RACE_ADMIN_ONLY",         "赛车仅管理员开局",          "bool",  0,   1,       "race"),
    ("rake_enabled",            "RAKE_ENABLED",            "游戏抽水开关(官方模式结算)", "bool",  0,   1,       "rake"),
    ("rake_percent",            "RAKE_PERCENT",            "抽水比例(%·赢家净赢抽成)",  "int",   0,   50,      "rake"),
    ("rake_min_net",            "RAKE_MIN_NET",            "抽水门槛(净赢低于此值不抽)", "int",   0,   1000000, "rake"),
    ("broadcast_enabled",       "BROADCAST_ENABLED",       "大奖战报自动广播开关",      "bool",  0,   1,       "general"),
    ("broadcast_min_amount",    "BROADCAST_MIN_AMOUNT",    "战报阈值(单局净赢≥此值广播)", "int",  100, 10000000,"general"),
    ("game_starting_chips",     "GAME_STARTING_CHIPS",     "新玩家初始积分(全游戏统一)", "int",  100, 1000000, "general"),
    ("small_blind",             "SMALL_BLIND",             "德州小盲注(0=不设盲注)",    "int",   0,   100000,  "texas"),
    ("big_blind",               "BIG_BLIND",               "德州大盲注(0=不设盲注)",    "int",   0,   100000,  "texas"),
    ("ante",                    "ANTE",                    "德州前注(每人发牌前强制投入)", "int", 0,  100000,  "texas"),
    ("texas_enabled",           "TEXAS_ENABLED",           "德州扑克开关",              "bool",  0,   1,       "texas"),
    ("daily_texas_enabled",     "DAILY_TEXAS_ENABLED",     "日常德州开关",              "bool",  0,   1,       "texas"),
    ("ranked_texas_enabled",    "RANKED_TEXAS_ENABLED",    "赛季德州开关",              "bool",  0,   1,       "texas"),
    ("texas_admin_only",        "TEXAS_ADMIN_ONLY",        "德州仅管理员开局",          "bool",  0,   1,       "texas"),
    ("stale_text_command_seconds","STALE_TEXT_COMMAND_SECONDS","过期消息忽略(秒,防翻旧账命令)", "int", 5, 3600, "general"),
    # ---------- 定时任务（时间可自行设置） ----------
    ("daily_reset_time",        "DAILY_RESET_TIME",        "每日重置时间(时:分,赛季分重置等)", "short", 0, 0, "schedule"),
    ("daily_reset_enabled",     "DAILY_RESET_ENABLED",     "每日重置开关",              "bool",  0,   1,       "schedule"),
    ("leaderboard_time",        "LEADERBOARD_TIME",        "德州日榜推送时间(时:分)",   "short", 0, 0,      "schedule"),
    ("leaderboard_enabled",     "LEADERBOARD_ENABLED",     "德州日榜推送开关",          "bool",  0,   1,       "schedule"),
    ("race_hourly_minute",      "RACE_HOURLY_MINUTE",      "赛车每小时自动开赛(第几分钟)", "int", 0, 59,    "schedule"),
    ("race_auto_enabled",       "RACE_AUTO_ENABLED",       "赛车自动开赛开关(仍受时段限制)", "bool", 0, 1,    "schedule"),
    ("race_hourly_start",       "RACE_HOURLY_START",       "自动开赛时段·开始(填小时0-23)",   "int",   0,   23,      "schedule"),
    ("race_hourly_end",         "RACE_HOURLY_END",         "自动开赛时段·结束(填小时0-23；比“开始”小=通宵到第二天。例：开始18+结束2=每天18点到次日凌晨2点多)", "int",   0,   23,      "schedule"),
    ("backup_interval_hours",   "BACKUP_INTERVAL_HOURS",   "自动备份间隔(小时,改间隔重启后生效)", "int", 1, 168,   "schedule"),
    ("backup_enabled",          "BACKUP_ENABLED",          "自动备份开关(保存即时生效)", "bool",  0,   1,       "schedule"),
    ("sep_announce",            None, "📣 定时群公告（每天定点推送到全部授权群）", "sep", 0, 0, "schedule"),
    ("announce_enabled",        "ANNOUNCE_ENABLED",        "定时群公告开关",            "bool",  0,   1,       "schedule"),
    ("announce_time",           "ANNOUNCE_TIME",           "公告推送时间(时:分,北京时间)", "short", 0, 0,     "schedule"),
    ("announce_text",           "ANNOUNCE_TEXT",           "公告内容(支持 {date}=当天日期)", "text", 0, 0,   "schedule"),
    ("sep_ad_botmsg",           None,                       "① 机器人自身消息自动回收(秒,0=不删)", "sep", 0, 0, "autodel"),
    ("panel_delete_seconds",    "PANEL_DELETE_SECONDS",    "游戏卡片/下注面板删除(秒,0=不删)", "int", 0, 86400, "autodel"),
    ("autodel_default_seconds", "AUTODEL_DEFAULT_SECONDS", "其他消息默认删除(秒,0=关；群里新发的无按钮消息都按这个回收)", "int", 0, 86400, "autodel"),
    ("points_delete_seconds",   "POINTS_DELETE_SECONDS",   "你发的命令消息删除(秒,0=不删)", "int", 0, 86400, "autodel"),
    ("reply_delete_seconds",    "REPLY_DELETE_SECONDS",    "查询类回复删除(秒,0=不删)", "int", 0, 86400, "autodel"),
    ("settle_delete_seconds",   "SETTLE_DELETE_SECONDS",   "游戏结算消息删除(秒,0=不删)", "int", 0, 86400, "autodel"),
    ("race_notice_delete_seconds", "RACE_NOTICE_DELETE_SECONDS", "赛车倒计时提示删除(秒,0=不删)", "int", 0, 86400, "autodel"),
    ("rank_delete_seconds",     "RANK_DELETE_SECONDS",      "榜单消息删除(秒,0=不删；带翻页按钮的榜单也按此回收)", "int", 0, 86400, "autodel"),
    ("web_base_url",            "WEB_BASE_URL",            "后台公网地址(/后台一键登录用)",          "text", 0,   0,    "general"),
    ("observe_enabled",         "OBSERVE_ENABLED",         "新成员观察期开关(入群未满时长禁言)", "bool", 0, 1, "members/join"),
    ("observe_seconds",         "OBSERVE_SECONDS",         "新成员观察期时长(秒,0=不限制)", "int", 0, 86400, "members/join"),
    ("sep_ad_spam",             None,                       "② 刷屏识别（复读/定时脚本）", "sep", 0, 0, "autodel"),
    ("antispam_enabled",        "ANTISPAM_ENABLED",        "定时刷屏识别开关(复读+定时器特征)", "bool", 0,   1,    "autodel"),
    ("antispam_repeat_n",       "ANTISPAM_REPEAT_N",       "复读命中条数(窗口内同内容)", "int",  2,   10,   "autodel"),
    ("antispam_window",         "ANTISPAM_WINDOW",         "复读检测窗口(秒)", "int",  10,  3600, "autodel"),
    ("antispam_min_len",        "ANTISPAM_MIN_LEN",        "参与统计的最短字数(更短的消息不参与判定)", "int", 1, 50, "autodel"),
    ("antispam_timer_n",        "ANTISPAM_TIMER_N",        "定时器特征最少累计条数", "int",  3,   20,   "autodel"),
    ("antispam_timer_tol",      "ANTISPAM_TIMER_TOL",      "定时器间隔偏差容忍(%)", "int",  5,   90,   "autodel"),
    ("antispam_mute_seconds",   "ANTISPAM_MUTE_SECONDS",   "命中禁言基础时长(秒,0=只删不禁)", "int",  0,   86400,"autodel"),
    ("antispam_mute_escalate",  "ANTISPAM_MUTE_ESCALATE",  "累犯禁言翻倍", "bool", 0,   1,    "autodel"),
    ("antispam_notice_seconds", "ANTISPAM_NOTICE_SECONDS", "刷屏命中通告删除(秒,0=不删)", "int", 0, 86400, "autodel"),
    # ===== 内容规则（合并后的两个多选，替代原先 12 个单开关） =====
    ("sep_ad_rule", None, "③ 内容规则（勾选即删，管理员豁免）", "sep", 0, 0, "autodel"),
    ("autodel_text_rules",     "AUTODEL_TEXT_RULES",     "文本类规则",          "multi", 0, 0, "autodel"),
    ("autodel_long_len",       "AUTODEL_LONG_LEN",       "超长消息长度阈值(选了「超长」才生效)", "int", 50, 4096, "autodel"),
    ("autodel_text_seconds",   "AUTODEL_TEXT_SECONDS",   "文本类删除延迟(秒,0=立即删)", "int", 0, 86400, "autodel"),
    ("autodel_media_types",    "AUTODEL_MEDIA_TYPES",    "媒体/系统类规则",     "multi", 0, 0, "autodel"),
    ("autodel_media_seconds",  "AUTODEL_MEDIA_SECONDS",  "媒体类删除延迟(秒,0=立即删)", "int", 0, 86400, "autodel"),
    # ===== 群管中心（新功能全部默认关闭，网页手动开启） =====
    ("sep_mod_verify",        None, "入群验证（新人点按钮才放行）", "sep", 0, 0, "mod"),
    ("join_verify_enabled",   "JOIN_VERIFY_ENABLED",   "入群验证开关",            "bool", 0, 1, "mod"),
    ("join_verify_seconds",   "JOIN_VERIFY_SECONDS",   "验证超时(秒)",            "int",  10, 3600, "mod"),
    ("join_verify_action",    "JOIN_VERIFY_ACTION",    "超时处理(0=只提醒 1=禁言 2=踢出 3=封禁)", "int", 0, 3, "mod"),
    ("join_verify_mode",      "JOIN_VERIFY_MODE",      "验证方式(0=按钮选答案 1=图片算术 2=一键通过)", "int", 0, 2, "mod"),
    ("join_verify_max_wrong", "JOIN_VERIFY_MAX_WRONG", "验证答错N次按超时档处理(0=不限)", "int", 0, 20, "mod"),
    ("join_verify_msg",       "JOIN_VERIFY_MSG",       "验证提示({name} {seconds})", "text", 0, 0, "mod"),
    ("join_verify_ok_msg",    "JOIN_VERIFY_OK_MSG",    "验证通过提示({name})",    "text", 0, 0, "mod"),
    ("clean_service_enabled", "CLEAN_SERVICE_ENABLED", "清理入群/退群系统提示(防刷屏)", "bool", 0, 1, "mod"),
    ("sep_autoreply",         None, "③ 关键词自动回复", "sep", 0, 0, "mod"),
    ("autoreply_enabled",     "AUTOREPLY_ENABLED",     "关键词自动回复开关",      "bool", 0, 1, "mod"),
    ("keyword_replies",       "KEYWORD_REPLIES",       "关键词自动回复(每行: 关键词|回复内容)", "text", 0, 0, "mod"),
    ("sep_mod_word",          None, "② 敏感词与域名白名单", "sep", 0, 0, "mod"),
    ("sensitive_enabled",     "SENSITIVE_ENABLED",     "敏感词过滤开关",          "bool", 0, 1, "mod"),
    ("sensitive_words",       "SENSITIVE_WORDS",       "敏感词(逗号分隔；/正则/ 形式支持正则)", "names", 0, 0, "mod"),
    ("sensitive_action",      "SENSITIVE_ACTION",      "命中处理(0=删除 1=删+禁言 2=删+踢出)", "int", 0, 2, "mod"),
    ("sensitive_mute_seconds", "SENSITIVE_MUTE_SECONDS", "敏感词禁言时长(秒)",    "int",  0, 86400, "mod"),
    ("link_whitelist_enabled", "LINK_WHITELIST_ENABLED", "域名白名单开关(名单内链接不删)", "bool", 0, 1, "mod"),
    ("link_whitelist",        "LINK_WHITELIST",        "白名单域名(逗号分隔，子域名自动放行)", "names", 0, 0, "mod"),
    ("sep_mod_observe",       None, "观察期到期巡检（须先开观察期：群组管理→入群与观察，否则本段不生效）", "sep", 0, 0, "mod"),
    ("observe_check_enabled", "OBSERVE_CHECK_ENABLED", "到期巡检开关(需先开观察期)", "bool", 0, 1, "mod"),
    ("observe_check_msgs",    "OBSERVE_CHECK_MSGS",    "发言少于N条视为不活跃",   "int",  0, 1000, "mod"),
    ("observe_check_avatar",  "OBSERVE_CHECK_AVATAR",  "无头像也算不活跃(查API，仅零发言者)", "bool", 0, 1, "mod"),
    ("observe_check_action",  "OBSERVE_CHECK_ACTION",  "处理方式(0=提醒管理员 1=禁言 2=踢出)", "int", 0, 2, "mod"),
    ("sep_mod_gate",          None, "进群硬门槛（不满足直接移出，不进验证流程）", "sep", 0, 0, "mod"),
    ("join_gate_username",    "JOIN_GATE_USERNAME",    "须有用户名",              "bool", 0, 1, "mod"),
    ("join_gate_premium",     "JOIN_GATE_PREMIUM",     "须 Telegram Premium",     "bool", 0, 1, "mod"),
    ("join_gate_bio",         "JOIN_GATE_BIO",         "须有简介(需查API，失败放行)", "bool", 0, 1, "mod"),
    ("sep_mod_lurker",        None, "潜水号清理（老成员长期不冒泡）", "sep", 0, 0, "mod"),
    ("lurker_enabled",        "LURKER_ENABLED",        "潜水清理开关",            "bool", 0, 1, "mod"),
    ("lurker_days",           "LURKER_DAYS",           "入群超过N天才纳入扫描",    "int",  1, 365, "mod"),
    ("lurker_msgs",           "LURKER_MSGS",           "累计发言少于N条视为潜水",  "int",  0, 1000, "mod"),
    ("lurker_action",         "LURKER_ACTION",         "处理方式(0=提醒管理员 1=禁言 2=踢出)", "int", 0, 2, "mod"),
    ("sep_mod_raid",          None, "防突袭（短时间大量进群自动人墙）", "sep", 0, 0, "mod"),
    ("raid_enabled",          "RAID_ENABLED",          "防突袭开关",              "bool", 0, 1, "mod"),
    ("raid_window",           "RAID_WINDOW",           "检测窗口(秒)",            "int",  10, 600, "mod"),
    ("raid_threshold",        "RAID_THRESHOLD",        "窗口内N人进群视为突袭",    "int",  3, 50, "mod"),
    ("raid_cooldown",         "RAID_COOLDOWN",         "人墙持续秒数(到期自动解除)", "int", 60, 86400, "mod"),
    ("sep_mod_forcesub",      None, "强制订阅频道（未订阅者发言即删+提示，管理员豁免）", "sep", 0, 0, "mod"),
    ("force_sub_enabled",     "FORCE_SUB_ENABLED",     "强制订阅开关",            "bool", 0, 1, "mod"),
    ("force_sub_channels",    "FORCE_SUB_CHANNELS",    "须订阅的频道(@用户名 或 -100xxx频道id，逗号分隔，订阅其一即可；私有频道请填id，邀请链接检测不了)", "names", 0, 0, "mod"),
    ("force_sub_only_new",    "FORCE_SUB_ONLY_NEW",    "只检测新用户(入群10分钟内)", "bool", 0, 1, "mod"),
    ("force_sub_warn_seconds","FORCE_SUB_WARN_SECONDS","提示自动删除(秒,0=不删)",  "int",  0, 3600, "mod"),
    ("force_sub_warn_tpl",    "FORCE_SUB_WARN_TPL",    "订阅提示({name} {channels} {seconds})", "text", 0, 0, "mod"),
    ("welcome_enabled",         "WELCOME_ENABLED",         "入群欢迎开关",              "bool",  0,   1,       "members/join"),
    ("welcome_tpl",             "WELCOME_TPL",             "入群欢迎消息(支持 {name} {group} {id})", "text", 0, 0, "members/join"),
    ("emergency_chips",         "EMERGENCY_CHIPS",         "归零赠送积分",              "int",   0,   100000,  "general"),
    ("emergency_max_uses",      "EMERGENCY_MAX_USES",      "归零每日赠送次数",          "int",   0,   99,      "general"),
    ("emergency_min_games",     "EMERGENCY_MIN_GAMES",     "归零赠送要求累计玩过局数(0=不限)", "int", 0, 9999, "general"),
    # ---------- 安全（后台登录） ----------
    ("web_otp_enabled",         "WEB_OTP_ENABLED",         "后台登录需要验证码(二次验证)", "bool", 0, 1, "security"),
    ("season_start_chips",      "SEASON_START_CHIPS",      "每人起始分",                "int",   100, 1000000, "season"),
    ("season_min_players",      "SEASON_MIN_PLAYERS",      "最少开赛人数",              "int",   2,   50,      "season"),
    ("season_min_games",        "SEASON_MIN_GAMES",        "结算最少局数",              "int",   0,   999,     "season"),
    ("season_days",             "SEASON_DAYS",             "赛季天数",                  "int",   1,   90,      "season"),
    ("season_rebuy_count",      "SEASON_REBUY_COUNT",      "每日重买次数上限",          "int",   0,   20,      "season"),
    ("season_rebuy_amount",     "SEASON_REBUY_AMOUNT",     "每次重买金额",              "int",   0,   1000000, "season"),
    ("season_bet_percent",      "SEASON_BET_PERCENT",      "赛季单人单局投入上限比例(×落座筹码总和)", "float", 0.0, 1.0, "season"),
    # ===== 赛季德州独立参数（0=沿用日常德州设置；填非 0 值即赛季局专用） =====
    ("sep_season_texas",        None, "赛季德州规则（留空/0 = 沿用日常德州；填了就是赛季局专用）", "sep", 0, 0, "season"),
    ("season_min_entry_chips",  "SEASON_MIN_ENTRY_CHIPS",  "入座最低赛季分(0=只要>0即可)", "int", 0, 1000000, "season"),
    ("season_fixed_min_raise",  "SEASON_FIXED_MIN_RAISE",  "最低加注额(0=沿用日常)",     "int",   0,  100000, "season"),
    ("season_turn_timeout",     "SEASON_TURN_TIMEOUT",     "单回合思考时间(秒,0=沿用日常)", "int", 0, 600,   "season"),
    ("season_room_wait_timeout","SEASON_ROOM_WAIT_TIMEOUT","等待房倒计时(秒,0=沿用日常)", "int",  0,  600,   "season"),
    ("season_small_blind",      "SEASON_SMALL_BLIND",      "德州小盲注(0=沿用日常)",     "int",   0,  100000, "season"),
    ("season_big_blind",        "SEASON_BIG_BLIND",        "德州大盲注(0=沿用日常)",     "int",   0,  100000, "season"),
    ("season_ante",             "SEASON_ANTE",             "德州前注(0=沿用日常)",       "int",   0,  100000, "season"),
    # ===== 聊天积分兑换赛季分（比例可调，默认 1:1） =====
    ("sep_season_exchange",     None, "聊天积分兑换赛季分", "sep", 0, 0, "season"),
    ("ranked_exchange_enabled", "RANKED_EXCHANGE_ENABLED", "兑换开关",                  "bool",  0,   1,       "season"),
    ("ranked_exchange_cost",    "RANKED_EXCHANGE_COST",    "兑换比例·消耗聊天积分",      "int",   1,   1000000, "season"),
    ("ranked_exchange_gain",    "RANKED_EXCHANGE_GAIN",    "兑换比例·得到赛季分",        "int",   1,   1000000, "season"),
    ("ranked_exchange_daily_limit","RANKED_EXCHANGE_DAILY_LIMIT","每人每日兑换上限(按消耗积分算,0=不限)", "int", 0, 10000000, "season"),
    # ---------- 积分系统（子页面制：points/子页键，照阿福模板） ----------
    ("admin_adjust",            "ADMIN_ADJUST_ENABLED",    "管理员可增减积分",          "bool",  0,   1,       "points/set"),
    ("rank_1_emoji",            "RANK_1_EMOJI",            "积分排行第一名表情",        "short", 0,   0,       "points/set"),
    ("rank_2_emoji",            "RANK_2_EMOJI",            "积分排行第二名表情",        "short", 0,   0,       "points/set"),
    ("rank_3_emoji",            "RANK_3_EMOJI",            "积分排行第三名表情",        "short", 0,   0,       "points/set"),
    ("rank_name_link",          "RANK_NAME_LINK",          "榜单人名显示蓝色可点链接(开启后每次发榜会通知榜上前5人，慎开)", "bool", 0, 1, "points/set"),
    ("add_msg_tpl",             "ADD_MSG_TPL",             "添加积分提示消息",          "text",  0,   0,       "points/set"),
    ("query_msg_tpl",           "QUERY_MSG_TPL",           "查询积分消息",              "text",  0,   0,       "points/set"),
    ("chat_enabled",            "CHAT_ENABLED",            "聊天积分开关(需关机器人隐私模式)", "bool", 0, 1,    "points/set"),
    ("chat_chars_per",          "CHAT_CHARS_PER",          "每满N个字符记分",           "int",   1,   200,     "points/set"),
    ("chat_reward",             "CHAT_REWARD",             "每满N字符记几分",           "int",   1,   1000,    "points/set"),
    ("chat_max_per_msg",        "CHAT_MAX_PER_MSG",        "单条消息最高得分(0=不限)",  "int",   0,   1000000, "points/set"),
    ("chat_daily_cap",          "CHAT_DAILY_CAP",          "聊天积分每日上限(0=不限)",  "int",   0,   1000000, "points/cap"),
    # ===== 有效发言判定（2026-09-09 用户规则：正常讨论才计分） =====
    ("chat_min_len",            "CHAT_MIN_LEN",            "有效发言最少字数(低于不计分)", "int", 1, 200, "points/cap"),
    ("chat_junk_words",         "CHAT_JUNK_WORDS",         "无意义词(纯这些词的消息不计分)", "names", 0, 0, "points/cap"),
    ("chat_dup_n",              "CHAT_DUP_N",              "窗口内同内容达N条不计分(0=不查重)", "int", 0, 20, "points/cap"),
    ("chat_dup_window",         "CHAT_DUP_WINDOW",         "重复内容判定窗口(秒)",      "int",   10,  86400,   "points/cap"),
    ("sign_enabled",            "SIGN_ENABLED",            "每日签到开关",              "bool",  0,   1,       "points/sign"),
    ("sign_base_reward",        "SIGN_BASE_REWARD",        "签到基础奖励",              "int",   0,   1000000, "points/sign"),
    ("sign_streak_bonus",       "SIGN_STREAK_BONUS",       "连续签到满7天额外奖励",     "int",   0,   1000000, "points/sign"),
    ("sign_msg_tpl",            "SIGN_MSG_TPL",            "签到成功消息",              "text",  0,   0,       "points/sign"),
    ("redpacket_enabled",       "REDPACKET_ENABLED",       "积分红包开关",              "bool",  0,   1,       "points/rp"),
    ("redpacket_exclusive_enabled","RP_EXCLUSIVE_ENABLED", "专属红包开关(回复/ID指定)", "bool",  0,   1,       "points/rp"),
    ("redpacket_luck_enabled",  "RP_LUCK_ENABLED",         "拼手气红包开关(0=平均分)",  "bool",  0,   1,       "points/rp"),
    ("redpacket_log_enabled",   "RP_LOG_ENABLED",          "抢完公布手气排行",          "bool",  0,   1,       "points/rp"),
    ("rp_msg_grab",             "RP_MSG_GRAB",             "抢红包成功提示",            "text",  0,   0,       "points/rp"),
    ("rp_msg_none",             "RP_MSG_NONE",             "未抢到红包提示",            "text",  0,   0,       "points/rp"),
    ("rp_msg_dup",              "RP_MSG_DUP",              "已抢过提示",                "text",  0,   0,       "points/rp"),
    ("rp_msg_poor",             "RP_MSG_POOR",             "发红包积分不足提示",        "text",  0,   0,       "points/rp"),
    ("rp_msg_target",           "RP_MSG_TARGET",           "专属红包非目标提示",        "text",  0,   0,       "points/rp"),
    ("rp_msg_log",              "RP_MSG_LOG",              "拼手气日志(每行,{rank}=名次)", "text", 0, 0,       "points/rp"),
    ("level_notify_enabled",    "LEVEL_NOTIFY_ENABLED",    "用户升级通知开关",          "bool",  0,   1,       "points/level"),
    ("level_enabled",           "LEVEL_ENABLED",           "积分等级系统开关",          "bool",  0,   1,       "points/level"),
    ("level_keep_on_spend",     "LEVEL_KEEP_ON_SPEND",     "消费不掉级(关闭=花掉积分会降级)", "bool", 0, 1,  "points/level"),
    ("level_sync_tag",          "LEVEL_SYNC_TAG",          "积分称号同步成员标签开关",  "bool",  0,   1,       "points/level"),
    ("tag_sync_max",            "TAG_SYNC_MAX",            "批量同步标签人数上限(每群)", "int",   1,   1000,   "points/level"),
    ("level_up_msg_tpl",        "LEVEL_UP_MSG_TPL",        "用户升级通知",              "text",  0,   0,       "points/level"),
    ("level_down_notify_enabled", "LEVEL_DOWN_NOTIFY_ENABLED", "用户降级通知开关",      "bool",  0,   1,       "points/level"),
    ("level_down_msg_tpl",      "LEVEL_DOWN_MSG_TPL",      "用户降级通知",              "text",  0,   0,       "points/level"),
    ("level_query_msg_tpl",     "LEVEL_QUERY_MSG_TPL",     "用户查询等级消息",          "text",  0,   0,       "points/level"),
    ("level_query_none_tpl",    "LEVEL_QUERY_NONE_TPL",    "用户查询等级无规则提示",    "text",  0,   0,       "points/level"),
    ("point_levels",            "POINT_LEVELS",            "积分等级表",               "levels", 0, 0,      "points/level_hidden"),
    ("level_msg_guard_enabled", "LEVEL_MSG_GUARD_ENABLED", "积分等级权限(等级消息管控)","bool",  0,   1,       "points/levelguard"),
    ("level_msg_warn_tpl",      "LEVEL_MSG_WARN_TPL",      "等级消息违规提示",          "text",  0,   0,       "points/levelguard"),
    ("level_msg_window",        "LEVEL_MSG_WINDOW",        "违规窗口时间(秒)",          "int",   1,   3600,    "points/levelguard"),
    ("level_msg_max_hits",      "LEVEL_MSG_MAX_HITS",      "窗口违规次数",              "int",   1,   100,     "points/levelguard"),
    ("level_msg_punish",        "LEVEL_MSG_PUNISH",        "频繁违规惩罚类型(0=只提醒 1=禁言 2=踢出)", "int", 0, 2, "points/levelguard"),
    ("level_msg_mute_seconds",  "LEVEL_MSG_MUTE_SECONDS",  "违规后禁言(秒 0不禁言 小于30秒永久)", "int", 0, 86400, "points/levelguard"),
    ("level_msg_mute_tpl",      "LEVEL_MSG_MUTE_TPL",      "禁言提示消息",              "text",  0,   0,       "points/levelguard"),
    ("mall_items",              "MALL_ITEMS",              "商城商品表",               "items", 0, 0,       "points/mall_hidden"),
    ("mall_enabled",            "MALL_ENABLED",            "开启积分商城",              "bool",  0,   1,       "points/mall"),
    ("mall_page_size",          "MALL_PAGE_SIZE",          "商城列表每页商品数",        "int",   1,   50,      "points/mall"),
    ("mall_msg_buy",            "MALL_MSG_BUY",            "兑换成功消息",              "text",  0,   0,       "points/mall"),
    ("mall_msg_empty",          "MALL_MSG_EMPTY",          "无商品提示消息",            "text",  0,   0,       "points/mall"),
    ("mall_min_age_days",       "MALL_MIN_AGE_DAYS",       "兑换门槛-使用满N天(0=不限,防小号)", "int", 0, 365, "points/mall"),
    ("mall_min_active_days",    "MALL_MIN_ACTIVE_DAYS",    "兑换门槛-游戏活跃天数≥N(0=不限)", "int", 0, 365,   "points/mall"),
    ("mall_list_delete_seconds","MALL_LIST_DELETE_SECONDS","商城/兑换列表消息删除(秒,0=不删)", "int", 0, 86400, "points/mall"),
    ("inherit_enabled",         "INHERIT_ENABLED",         "积分转赠(继承)开关",        "bool",  0,   1,       "points/inherit"),
    ("inherit_msg_ok",          "INHERIT_MSG_OK",          "转赠成功消息",              "text",  0,   0,       "points/inherit"),
    ("inherit_daily_limit",     "INHERIT_DAILY_LIMIT",     "每日转赠上限(0=不限,防小号)", "int",  0,   1000000, "points/inherit"),
    ("fund_flow_alert",         "FUND_FLOW_ALERT",         "资金流标红阈值(单对单向累计)", "int",  100, 10000000,"admin/fundflow"),
    ("inherit_fee_percent",     "INHERIT_FEE_PERCENT",     "转赠手续费(%,0=无)",        "int",   0,   50,      "points/inherit"),
    # 群组抽奖（基础版：1 个 prize+count 形式；点数抽奖/乐透等高级类型后续按需扩展）
    ("lottery_enabled",         "LOTTERY_ENABLED",         "群组抽奖总开关",            "bool",  0,   1,       "lottery"),
    ("lottery_keyword",         "LOTTERY_KEYWORD",         "参与触发词(也支持 /开奖)",   "short", 0,   0,       "lottery"),
    ("lottery_default_duration","LOTTERY_DEFAULT_DURATION","倒计时默认时长(秒)",          "int",   10,  3600,    "lottery"),
    ("lottery_max_prizes",      "LOTTERY_MAX_PRIZES",      "单次抽奖最多奖品档数",       "int",   1,   20,      "lottery"),
    ("lottery_fee",             "LOTTERY_FEE",             "参与扣积分(0=免费)",         "int",   0,   10000,   "lottery"),
    ("lottery_pin_msg",         "LOTTERY_PIN_MSG",         "抽奖消息置顶",              "bool",  0,   1,       "lottery"),
    ("lottery_pin_result",      "LOTTERY_PIN_RESULT",      "抽奖结果置顶",              "bool",  0,   1,       "lottery"),
    ("lottery_msg_start",       "LOTTERY_MSG_START",       "活动公告模板",              "text", 0,   0,       "lottery"),
    ("lottery_msg_joined",      "LOTTERY_MSG_JOINED",      "参与成功模板",              "text", 0,   0,       "lottery"),
    ("lottery_msg_dup",         "LOTTERY_MSG_DUP",         "重复参与模板",              "text", 0,   0,       "lottery"),
    ("lottery_msg_fail",        "LOTTERY_MSG_FAIL",        "参与失败模板(余额不足等)",  "text", 0,   0,       "lottery"),
    ("lottery_msg_result",      "LOTTERY_MSG_RESULT",      "开奖结果模板",              "text", 0,   0,       "lottery"),
    ("buy_enabled",             "BUY_ENABLED",             "购买积分开关(管理员人工确认)", "bool", 0, 1,     "points/buy"),
    ("buy_min",                 "BUY_MIN",                 "单次最低购买数量",          "int",   100, 1000000, "points/buy"),
    ("buy_max",                 "BUY_MAX",                 "单次最高购买数量",          "int",   100, 10000000,"points/buy"),
    ("redeem_max_per_user",     "REDEEM_MAX_PER_USER",     "每人最大兑换数量(0=不限)",  "int",   0,   9999,    "points/redeem"),
    ("redeem_start",            "REDEEM_START",            "兑换开始时间(YYYY-MM-DD HH:MM,留空不限)", "short", 0, 0, "points/redeem"),
    ("redeem_end",              "REDEEM_END",              "兑换结束时间(同上,留空不限)", "short", 0,  0,       "points/redeem"),
    ("redeem_msg_list",         "REDEEM_MSG_LIST",         "兑换商品行模板",            "text",  0,   0,       "points/redeem"),
    ("redeem_msg_ok_group",     "REDEEM_MSG_OK_GROUP",     "兑换成功群通知",            "text",  0,   0,       "points/redeem"),
    ("redeem_msg_ok_dm",        "REDEEM_MSG_OK_DM",        "兑换成功私聊通知",          "text",  0,   0,       "points/redeem"),
    # ---------- 邀请系统（群组设置 → 邀请系统，子页面制照阿福模板） ----------
    ("invite_enabled",          "INVITE_ENABLED",          "邀请系统开关",              "bool",  0,   1,       "invite/config"),
    ("invite_notify",           "INVITE_NOTIFY",           "邀请人私聊通知开关",        "bool",  0,   1,       "invite/config"),
    ("invite_reward",           "INVITE_REWARD",           "邀请奖励(积分/合格1人)",     "int",   0,   1000000, "invite/config"),
    ("invite_reward_times",     "INVITE_REWARD_TIMES",     "每人最多发放奖励次数",      "int",   1,   10000,   "invite/config"),
    ("invite_daily_cap_times",  "INVITE_DAILY_CAP_TIMES",  "每人每日拉新人数上限(0=不限)", "int", 0,   1000,    "invite/config"),
    ("invite_daily_cap_points", "INVITE_DAILY_CAP_POINTS", "每人每日拉新积分上限(0=不限)", "int", 0,   1000000, "invite/config"),
    ("newbie_reward_enabled",   "NEWBIE_REWARD_ENABLED",   "新人欢迎奖励开关(首次发言发)", "bool", 0, 1,      "invite/config"),
    ("newbie_reward",           "NEWBIE_REWARD",           "新人欢迎奖励积分",          "int",   0,   1000000, "invite/config"),
    ("invite_rank_admin_only",  "INVITE_RANK_ADMIN_ONLY",  "排行仅管理员可查开关",      "bool",  0,   1,       "invite/config"),
    # （2026-09-08 去重移除：今日/本月/总邀请排行指令三字段与「命令管理」页重复，
    #   触发词改由别名层统一管理：今日邀请排行/本月邀请排行/总邀请排行 照常可用）
    ("invite_ok_group",         "INVITE_OK_GROUP",         "邀请成功群内通知模板",      "text",  0,   0,       "invite/config"),
    ("invite_rank_today_msg",   "INVITE_RANK_TODAY_MSG",   "今日邀请排行标题模板",      "text",  0,   0,       "invite/config"),
    ("invite_rank_month_msg",   "INVITE_RANK_MONTH_MSG",   "本月邀请排行标题模板",      "text",  0,   0,       "invite/config"),
    ("invite_rank_all_msg",     "INVITE_RANK_ALL_MSG",     "总邀请排行标题模板",        "text",  0,   0,       "invite/config"),
    ("invite_rank_line_fmt",    "INVITE_RANK_LINE_FMT",    "排行行格式模板",            "text",  0,   0,       "invite/config"),
    ("invite_invalid_msg",      "INVITE_INVALID_MSG",      "无效邀请链接消息",          "text",  0,   0,       "invite/config"),
    ("invite_self_msg",         "INVITE_SELF_MSG",         "自己邀请自己消息",          "text",  0,   0,       "invite/config"),
    # 合格邀请结算（2026-09-08 替代旧「进群前置」死字段）：被邀请人本群达标才算合格才发奖
    ("invite_qualify_enabled",  "INVITE_QUALIFY_ENABLED",  "合格结算开关(达标才发奖)",   "bool",  0,   1,       "invite/qualify"),
    ("invite_manual_count",     "INVITE_MANUAL_COUNT",     "手动拉人计入邀请(添加人=邀请人)", "bool", 0, 1,  "invite/qualify"),
    ("invite_auto_approve",     "INVITE_AUTO_APPROVE",     "带链接申请自动批准(确认制归因不受此限)", "bool", 0, 1,  "invite/qualify"),
    ("invite_qualify_msgs",     "INVITE_QUALIFY_MSGS",     "质量要求-本群发言≥N条(0=不限)", "int", 0,  100000,  "invite/qualify"),
    ("invite_qualify_points",   "INVITE_QUALIFY_POINTS",   "质量要求-本群净赚积分≥M(0=不限)", "int", 0, 1000000, "invite/qualify"),
    ("invite_qualify_avatar",   "INVITE_QUALIFY_AVATAR",   "质量要求-进群须有头像(无则拒)", "bool", 0,   1,      "invite/qualify"),
    ("invite_qualify_username", "INVITE_QUALIFY_USERNAME", "质量要求-进群须有用户名(无则拒)","bool",0,   1,      "invite/qualify"),
]
# settings键 -> 模块全局变量名（群级读写与网页渲染共用）
_KEY2VAR = {f[0]: f[1] for f in SETTINGS_FIELDS if f[1]}
# 全站唯一、按群覆盖无意义的键（网页密码/备份接收人/调度作用对象/公网地址/功能开关类）
_GROUP_NEVER = {
    "web_base_url", "backup_interval_hours", "backup_enabled",
    "announce_enabled", "announce_time", "announce_text",
    "daily_reset_time", "daily_reset_enabled", "leaderboard_time", "leaderboard_enabled",
    "race_hourly_minute", "race_auto_enabled", "race_hourly_start", "race_hourly_end",
    "stale_text_command_seconds", "broadcast_enabled", "broadcast_min_amount",
    "admin_adjust", "fund_flow_alert",
    "rank_1_emoji", "rank_2_emoji", "rank_3_emoji",
    "web_otp_enabled",   # 后台登录二次验证：全站唯一，按群覆盖没有意义
}
_settings_lock = threading.Lock()
_web_password = WEB_DEFAULT_PASSWORD  # 运行时明文（仅内存，落盘绝不写它）；改密后由 hash 接管校验
_web_password_hash = ""  # pbkdf2-sha256 hex：真正落盘/进备份的凭据（拿到备份也还原不出密码）
_web_salt = ""           # 上面 hash 配套的盐

# ---------- 群级设置覆盖（2026-09-10 用户诉求：后台按群切设置） ----------
# 结构：{cid(int): {settings键: 值}}。语义 = **只存差异**：某群没覆盖的键，读取时继承全局默认。
# 因此改全局默认，所有没覆盖该项的群自动跟着变；群级只放"这个群特意要不一样"的值。
# 用 group_get(cid, key) 读取，永远不要直接 globals()[GNAME]——那样会绕过群级覆盖。
GROUP_SETTINGS = {}

# 哪些设置键支持群级覆盖（= 与具体群玩法/策略相关的键）。
# 刻意排除的：网页密码、备份接收人、调度作用对象、web_base_url 等"全站唯一"的键——
# 它们按群覆盖没有意义，反而会让管理员困惑。
# 具体清单在 SETTINGS_FIELDS 之后填充（需先有字段表才能推导）。
GROUP_SCOPED_KEYS = set()
GROUP_SCOPED_KEYS.update(
    f[0] for f in SETTINGS_FIELDS
    if f[1] and f[3] not in ("sep", "levels", "items", "cmd") and f[0] not in _GROUP_NEVER
)
# 排除理由：
#   sep   = 分组标题行，不是真设置
#   levels/items = 积分等级表 / 商城表（全站共享的数据表，按群覆盖会让"等级"概念崩掉）
#   cmd   = 自定义指令名（Telegram 命令是全 Bot 唯一，做不到按群）
# 其余（数字/开关/多选/话术模板/表情/词表/下注档位）全部支持按群覆盖。


# ---------- 当前群上下文（contextvars）：业务读取点零改动拿到群级值 ----------
# 设计要点：业务代码里 500+ 处直接读模块全局（如 BJ_MIN_BET），不可能逐个改签名传 cid。
# 改用「上下文变量」承载"这条消息属于哪个群"，再让统一读取入口 sget() 按上下文解析：
#   - 处理某群更新时（handler / 定时任务 / 网页请求）set 一次 cid，整条调用链自动生效；
#   - 没 set（启动初始化、跨群聚合、私聊命令）→ cid=0 → 读全局默认，**行为与改前逐字节一致**。
# 这是「零回归」的关键：GROUP_SETTINGS 为空时 sget("X") 恒等于裸全局 X。
_CUR_CID = contextvars.ContextVar("cur_cid", default=0)
_VAR2KEY = {f[1]: f[0] for f in SETTINGS_FIELDS if f[1]}   # 全局变量名 -> settings键
_MISS = object()


# ---------- 积分系统：运行时配置默认值（网页可改） ----------
SIGN_ENABLED = 1
SIGN_BASE_REWARD = 1000
SIGN_STREAK_BONUS = 500
CHAT_ENABLED = 1
CHAT_CHARS_PER = 5
CHAT_REWARD = 1
# 单条消息最高得分（0=不限）。2026-09-14 加：用户设「每满2字符1分」时，
# 发 10 个字得 5 分 —— 「每满N字符」这个口径本身让长消息成了刷分手段。
# 默认 0 = 不限 = 与旧版行为完全一致（不动任何现有配置）。
CHAT_MAX_PER_MSG = 0
CHAT_DAILY_CAP = 500
# 有效发言判定（2026-09-09 用户规则：正常讨论才计分，无意义/重复灌水不计）
CHAT_MIN_LEN = 4            # 消息最少字数（低于此不计分）
CHAT_JUNK_WORDS = []        # 无意义词黑名单（整条消息由这些词构成则不计分）
CHAT_DUP_WINDOW = 300       # 重复内容判定窗口（秒）
CHAT_DUP_N = 2              # 窗口内同内容达到 N 条即视为灌水，不再计分
POINTS_DELETE_SECONDS = 30
REPLY_DELETE_SECONDS = 30   # 查询类命令的 bot 回复自动删除（0=不删）
SETTLE_DELETE_SECONDS = 600 # 游戏结算消息自动删除（0=不删）
PANEL_DELETE_SECONDS = 300 # 游戏卡片/下注面板：本局结束后自动删除（0=不删）
AUTODEL_DEFAULT_SECONDS = 300  # 「默认自动删除」：群里**无按钮**消息一律按此回收（0=关，见 install_autodelete_default）
RACE_NOTICE_DELETE_SECONDS = 60  # 赛车倒计时提示自动删除（0=不删）
RANK_DELETE_SECONDS = 300  # 榜单/排行消息回收（0=不删）：榜单带翻页按钮，但属"查询结果"，不该永久占屏
RANK_NAME_LINK = 0         # 榜单人名是否渲染成蓝色可点链接（tg://user 文本提及）。
# ⚠️ 2026-09-12 用户报障：发「积分排行」后机器人**把榜上前 5 个群友全 @ 了一遍**。
#   Telegram 的文本提及（tg://user?id=）**会触发被提及者通知**（一条消息最多通知前 5 人），
#   榜单每页 10 人 ⇒ 每次发榜都像群发骚扰。故默认关闭（纯文本），需要蓝色可点再手动开。
_pending_deletes = []      # 待删消息队列 [[cid, mid, 到期时间戳], ...]：随 bot_data 持久化，重启后重放，重部署不再残留消息
_delete_tasks = set()      # 持有删除 task 的引用：裸 create_task 不保引用可能被事件循环 GC，删除凭空消失

# ── 玩家行序号对齐（2026-09-11 用户第 3 次投诉「1. 2. 3. 没对齐」后定死） ─────────────
# 根因：👉 是 emoji，在 Telegram 里实测约 1.3em 宽；而旧的 3 个半角空格只有约 0.75em，
#       ⇒ 行动者行的 `3.` 被推到右边，与 `1.`/`2.` 不同列。
# 正解：非行动者补位用「盲文空格 U+2800 + 1 个半角空格」≈ 与「👉 + 半角空格」≈ 1.58em 等宽。
# ⚠️ 四个游戏（德州/21点/金花/大话骰）玩家行**必须**用这两常量，别再手写空格。
#   ⚠️ 关键坑（2026-09-12 用户 Android 端第 4 次投诉「没对齐」）：旧实现用全角空格 U+3000 补位，
#   但 U+3000 属 Unicode 空白(Zs)，Android Telegram 会裁切行首空白 → 非行动者补位渲染成 0 宽、
#   徽标 🟢 顶到最左，与行动者 👉(≈2.5 格) 错位。改 U+2800 BRAILLE PATTERN BLANK（类别 So 非空白
#   → 绝不裁切，且天然占 2 格）即稳稳对齐。
PLAYER_MARK = "👉 "        # 行动者：emoji + 1 半角空格
PLAYER_PAD = "\u2800 "       # 非行动者：盲文空格(U+2800, 非空白→不裁切) + 1 半角空格，视觉等宽于 PLAYER_MARK       



WEB_OTP_ENABLED = False  # 一键登录(/后台)为主，密码直登为备用；验证码步骤默认关闭（要开改这里）
WEB_BASE_URL = ""  # 后台公网地址（如 https://xxx.northflank.app），/后台 一键登录链接用；不配则该功能不可用
# ---------- 群组抽奖 ----------
LOTTERY_ENABLED = True             # 总开关（网页 general→points/lottery 可关）
LOTTERY_KEYWORD = "抽奖"            # 玩家参与的触发词（也支持 /开奖 命令）
LOTTERY_DEFAULT_DURATION = 60       # 倒计时默认时长：群内命令开抽奖不带时间时用；网页倒计时模式预填值
LOTTERY_FEE = 0                     # 参与扣积分（0=免费）；参与门槛在每场活动创建时单独设置
LOTTERY_MAX_PRIZES = 8              # 单次抽奖最多几档奖品
LOTTERY_PIN_MSG = 1                 # 抽奖公告置顶（照阿福「抽奖消息置顶」；机器人需有置顶权限，否则静默降级）
LOTTERY_PIN_RESULT = 1              # 开奖结果置顶（照阿福「抽奖结果置顶」）
# ⚠️ 2026-09-12 用户第 2 次要求「抽奖界面重新抄阿福的」并给了 4 张阿福截图。
#   阿福公告的版式特征（第 4 张截图）：**不用任何横幅分隔符号**，纯文本 + 空行分块 +
#   「标签：值」行（🎉 抽奖标题：…／消耗积分：N 积分／定时开奖 YYYY-MM-DD HH:MM:SS／
#   参与关键词：…／已参与：N 人／🎁 奖品列表：／参与要求：），末尾「参与要求」用树形
#   （非末项 ➕ / 末项 └），参与按钮文案带实时人数「参与抽奖 N」，公告置顶。
LOTTERY_MSG_START = (
    "🎉 抽奖标题：{title}\n"
    "{desc_block}"
    "{fee_block}"
    "\n定时开奖 {end_line}\n"
    "{keyword_block}"
    "\n已参与：{n} 人\n"
    "\n🎁 奖品列表：\n{prize_list}\n"
    "{req_block}"
)
# 中奖行（照阿福「开奖奖品模板」= 🎉 {nickNameLink} 获得 {prizeName}）
LOTTERY_WIN_LINE = "🎉 <b>{name}</b> 获得 <b>{prize}</b>"
LOTTERY_MSG_JOINED = "✅ {nick} 参与成功！你是第 <b>{n}</b> 位参与者\n{fee_line}💰 余额：<b>{balance}</b>"
LOTTERY_MSG_DUP = "⚠️ {nick} 你已经参与过啦，等开奖即可"
LOTTERY_MSG_FAIL = "❌ {nick} {reason}"
LOTTERY_MSG_RESULT = (
    "🎉 <b>{title}</b> · 开奖结果\n"
    "\n{winners}\n"
    "\n📊 共 {n} 人参与，中奖 {w} 人"
)
OBSERVE_ENABLED = 0         # 新成员观察期开关（1=开启：入群未满时长的成员发言即删并禁言到期满）
OBSERVE_SECONDS = 300       # 观察期时长（秒）
# ===== 群管中心（mod 组）：入群验证 / 敏感词 / 域名白名单 / 观察期巡检 =====
# 全部默认关闭，网页「🛡️ 群管中心」手动开启（用户要求：新功能先关，自己开）
JOIN_VERIFY_ENABLED = 0     # 入群验证：新人进群先限制发言，点按钮才放行
JOIN_VERIFY_SECONDS = 120   # 验证超时（秒）
JOIN_VERIFY_ACTION = 0      # 超时处理：0=只提醒 1=禁言 2=踢出
JOIN_VERIFY_MSG = "👋 {name} 欢迎进群！请在 {seconds} 秒内点下方按钮完成验证，超时将按群规处理。"
JOIN_VERIFY_OK_MSG = "✅ {name} 验证通过，已解除限制，畅聊吧！"
JOIN_VERIFY_MODE = 0       # 验证方式：0=按钮选答案（题目+5个选项，点对的通过，默认）/ 1=图片算术（打字回复，未装 Pillow 降级文字算式）/ 2=一键通过
JOIN_VERIFY_MAX_WRONG = 0  # 验证答错 N 次按超时档处理（0=不限次数）
JOIN_GATE_USERNAME = 0     # 进群硬门槛：须有用户名（不满足直接移出，不进验证流程）
JOIN_GATE_PREMIUM = 0      # 进群硬门槛：须 Telegram Premium
JOIN_GATE_BIO = 0          # 进群硬门槛：须有简介（需额外查 API；查询失败宁放过不误杀）
AUTOREPLY_ENABLED = 0       # 关键词自动回复开关
KEYWORD_REPLIES = ""        # 每行一条：关键词|回复内容（见 features/autoreply）
CLEAN_SERVICE_ENABLED = 0   # 清理「XX 加入了群组/退出了群组」系统提示（防刷屏）
SENSITIVE_ENABLED = 0       # 敏感词过滤
SENSITIVE_WORDS = []        # 敏感词表（明文子串，或 /正则/ 形式）
SENSITIVE_ACTION = 0        # 命中处理：0=删除 1=删除+禁言 2=删除+踢出
SENSITIVE_MUTE_SECONDS = 600
LINK_WHITELIST_ENABLED = 0  # 域名白名单：名单内链接不按「链接消息」规则删
LINK_WHITELIST = []         # 白名单域名（t.me、example.com；子域名自动放行）
OBSERVE_CHECK_ENABLED = 0   # 观察期到期巡检
OBSERVE_CHECK_MSGS = 1      # 到期时本群发言少于 N 条视为不活跃
OBSERVE_CHECK_AVATAR = 0    # 无头像也算不活跃（需额外 API 查询，仅在发言为 0 时才查）
OBSERVE_CHECK_ACTION = 0    # 0=私聊提醒管理员 1=禁言 2=踢出
join_verify_pending = {}    # "cid:uid" -> {"ts":秒级时间戳, "msg_id":验证消息ID, "mode":0/1, "a","b","wrong"}
observe_checked = set()     # 已完成观察期复核的 "cid:uid"（防重复处理）
LURKER_ENABLED = 0          # 潜水号清理：入群超 N 天且累计发言不足 → 按档处理
LURKER_DAYS = 30            # 入群超过 N 天才纳入扫描
LURKER_MSGS = 5             # 累计发言少于 N 条视为潜水
LURKER_ACTION = 0           # 0=私聊提醒管理员 1=禁言 2=踢出
lurker_checked = set()      # 已处理/已豁免的 "cid:uid"（防重复骚扰）
settings_changes = []       # 设置改动留痕：[{ts,cid,key,old,new}]，只留最近 300 条
                            # （审计用；见 core/settings.py::_record_setting_change）
tag_synced = {}             # "cid:uid" -> 已同步进 Telegram 的成员标签值（批量同步断点续传用）
RAID_ENABLED = 0            # 防突袭：短时间大量进群 → 临时人墙（新人强制走验证禁言）
RAID_WINDOW = 60            # 检测窗口（秒）
RAID_THRESHOLD = 5          # 窗口内 N 人进群视为突袭
RAID_COOLDOWN = 600         # 人墙持续秒数，到期自动解除
raid_joins = {}             # cid -> [进群时间戳,...]（滑动窗口，重启清零即可）
raid_until = {}             # cid -> 人墙解除时间戳
raid_counted = {}           # "cid:uid" -> 进群时间戳（同一次进群双事件源只计一次）
FORCE_SUB_ENABLED = 0       # 强制订阅频道：未订阅者发言即删+提示（默认关，网页手动开启）
FORCE_SUB_CHANNELS = []     # 须订阅的频道（@用户名 或 -100 开头频道 id；订阅其一即可）
FORCE_SUB_ONLY_NEW = 0      # 只检测新用户（入群 10 分钟内），关=所有人
FORCE_SUB_WARN_SECONDS = 60 # 订阅提示自动删除秒数，0=不删
_fsub_ok_cache = defaultdict(dict)  # _fsub_ok_cache[cid][uid] = (判定时间, 是否已订阅)
_fsub_invite_cache = {}     # 频道id -> 邀请链接（私有频道 get_chat 结果缓存，避免每条消息打 API）
ANNOUNCE_ENABLED = 0        # 定时群公告：每天到点向全部授权群推一条
ANNOUNCE_TIME = "09:00"     # 推送时间（时:分，北京时间）
ANNOUNCE_TEXT = ""          # 公告内容（支持 {date}=当天日期）
announce_last_date = ""     # 当天已发标记（YYYY-MM-DD，重启不重发）
# ---------- 邀请系统 ----------
INVITE_ENABLED = 1          # 邀请系统总开关
INVITE_NOTIFY = 1           # 邀请成功私聊通知邀请人开关
INVITE_REWARD = 50          # 每合格 1 人奖励积分（达到质量要求才发）
INVITE_REWARD_TIMES = 10    # 单邀请人最多发放奖励次数（超额合格不再发，防白嫖）
# 每日拉新上限（2026-09-09 用户规则：单人每日最多 6 人 / 300 分，避免诱导乱拉人）
INVITE_DAILY_CAP_TIMES = 6      # 单人每日最多发放奖励的次数（0=不限）
INVITE_DAILY_CAP_POINTS = 300   # 单人每日拉新积分上限（0=不限）
# 新人欢迎奖励（2026-09-09 用户规则：新人完成入群审核 +200，帮助其有基础分）
NEWBIE_REWARD_ENABLED = 0   # 新人欢迎奖励开关
NEWBIE_REWARD = 200         # 新人首次发言奖励积分
# 归因策略（2026-09-08 二次改：申请制链接）：实测 chat_member 进群事件的 invite_link 字段
# 经常为空（直链/主链/时序都踩过），导致归因恒 0。而 chat_join_request 事件由 API 保证携带
# invite_link → /link 改发「申请制链接」（creates_join_request=True）：点链接 → 申请（带链接
# 入 invite_pending）→ 批准 → 进群 → 从 pending 精确归因。事件/申请都没带链接时仍不归因（宁缺毋滥）。
# 自动批准开关 INVITE_AUTO_APPROVE（默认 0=管理员手动批，可开 1=bot 对可归因申请自动批准）。
INVITE_LINK_CMD = "link"    # 获取专属邀请链接指令
INVITE_RANK_ADMIN_ONLY = 0  # 邀请排行仅管理员可查开关
INVITE_RANK_TODAY_CMD = "今日邀请排行"
INVITE_RANK_MONTH_CMD = "本月邀请排行"
INVITE_RANK_ALL_CMD = "总邀请排行"
# 合格邀请结算（2026-09-08 改造，替代旧「进群前置」死字段——旧逻辑人没进群查本群积分/发言，
# 新人必然 0 永远不满足=100% 死代码）：
# 机制：被邀请人经专属直链进群只记账（待达标）；在本群真实活动（发言/净赚积分）达阈值才标合格并发奖；
# 达标检查事件驱动（发言/签到/刷新按钮兜底重判）；单邀请人发放次数 ≤ INVITE_REWARD_TIMES。
INVITE_QUALIFY_ENABLED = 1   # 合格结算开关（1=达标才发奖；0=进群即发，兼容老行为）
INVITE_MANUAL_COUNT = 0      # 手动拉人计入邀请（1=管理员/成员手动添加的人记到添加人名下；默认关，防拉小号刷奖励）
INVITE_AUTO_APPROVE = 0      # 申请制链接自动批准（1=bot 自动批准携带链接的入群申请；0=管理员手动批，网页「成员/入群申请」可批）
INVITE_QUALIFY_MSGS = 10     # 质量要求：被邀请人本群累计发言 ≥ N 条（0=不限）
INVITE_QUALIFY_POINTS = 0    # 质量要求：被邀请人本群净赚积分 ≥ M（0=不限；净赚=余额-初始分）
INVITE_QUALIFY_AVATAR = 0    # 质量要求：进群须有头像（无则拒绝，永不发；防小号）
INVITE_QUALIFY_USERNAME = 0  # 质量要求：进群须有用户名（无则拒绝，永不发；防小号）
# ===== 定时刷屏识别（TG 定时消息发出后无标记，只能按行为特征抓：复读机 + 定时器节奏） =====
ANTISPAM_ENABLED = 1        # 1=开启
ANTISPAM_REPEAT_N = 3       # 复读命中：窗口内同内容第 N 条
ANTISPAM_WINDOW = 120       # 复读检测窗口（秒）
ANTISPAM_TIMER_N = 4        # 定时器特征：同内容累计至少 N 条才开始判定
ANTISPAM_TIMER_TOL = 30     # 定时器间隔偏差容忍（百分比，间隔需落在均值 ±30% 内）
ANTISPAM_MUTE_SECONDS = 3600  # 命中禁言基础时长（秒，0=只删不禁）
ANTISPAM_MUTE_ESCALATE = 1  # 累犯禁言翻倍（1h→2h→4h…）
ANTISPAM_OFFENSE_WINDOW = 7 * 86400  # 累犯计数的有效期（秒，默认 7 天）：窗口外的命中不再计入翻倍，防"历史总次数"把人变成事实永久禁言
ANTISPAM_NOTICE_SECONDS = 60  # 命中通告自动删除（秒，0=不删）
ANTISPAM_MIN_LEN = 5        # 参与统计的最短内容长度（防误伤"哈哈哈"类闲聊）
antispam_hist = {}          # (cid, uid, 内容归一化) -> [ts,...] 最多保留 12 条
antispam_offense = {}       # (cid, uid) -> [命中 ts,...] 用于累犯加重
# ===== 自动删除规则中心默认值（网页「自动删除」页可改，保存立即生效） =====
# ===== 自动删除：合并后的多选规则（旧的单开关已由 _migrate_legacy_autodel 自动换算） =====
AUTODEL_TEXT_RULES = "link,long"          # 文本类规则：link=链接 long=超长 premium_emoji=会员表情
AUTODEL_TEXT_SECONDS = 0                  # 文本类命中后延迟删除秒数（0=立即删）
AUTODEL_MEDIA_TYPES = "executable,contact,service"   # 媒体/系统类规则（见 MULTI_OPTIONS）
AUTODEL_MEDIA_SECONDS = 0                 # 媒体/系统类命中后延迟删除秒数（0=立即删）
AUTODEL_LONG_LEN = 200                    # 超长阈值（仅在选中 long 时生效）
WELCOME_ENABLED = 0         # 入群欢迎开关（1=开启）
WELCOME_TPL = "🎉 欢迎 {name} 加入本群！\n积分游戏请在群内发送 /start 查看玩法。"
REDPACKET_ENABLED = 1
POINT_LEVELS = [
    # 2026-09-09 用户截图口径：L1~L20 对应 100~20000 分
    # 权限阶梯：L1 只能文字 → L3 起可发贴纸 → L5 起可发图/视频 → L8 起可发音频
    #          → L13 起可发链接 → L18 起可转发/编辑（每级可在网页单独改）
    {"name": "L1",  "value": 100,   "perms": "text",                                          "on": 1},
    {"name": "L2",  "value": 200,   "perms": "text",                                          "on": 1},
    {"name": "L3",  "value": 350,   "perms": "text,sticker",                                  "on": 1},
    {"name": "L4",  "value": 550,   "perms": "text,sticker",                                  "on": 1},
    {"name": "L5",  "value": 800,   "perms": "text,sticker,photo,video",                      "on": 1},
    {"name": "L6",  "value": 1100,  "perms": "text,sticker,photo,video",                      "on": 1},
    {"name": "L7",  "value": 1450,  "perms": "text,sticker,photo,video",                      "on": 1},
    {"name": "L8",  "value": 1850,  "perms": "text,sticker,photo,video,audio",                "on": 1},
    {"name": "L9",  "value": 2300,  "perms": "text,sticker,photo,video,audio",                "on": 1},
    {"name": "L10", "value": 2800,  "perms": "text,sticker,photo,video,audio",                "on": 1},
    {"name": "L11", "value": 3400,  "perms": "text,sticker,photo,video,audio",                "on": 1},
    {"name": "L12", "value": 4100,  "perms": "text,sticker,photo,video,audio",                "on": 1},
    {"name": "L13", "value": 4900,  "perms": "text,sticker,photo,video,audio,link",           "on": 1},
    {"name": "L14", "value": 5800,  "perms": "text,sticker,photo,video,audio,link",           "on": 1},
    {"name": "L15", "value": 6800,  "perms": "text,sticker,photo,video,audio,link",           "on": 1},
    {"name": "L16", "value": 8000,  "perms": "text,sticker,photo,video,audio,link",           "on": 1},
    {"name": "L17", "value": 9500,  "perms": "text,sticker,photo,video,audio,link",           "on": 1},
    {"name": "L18", "value": 12000, "perms": "text,sticker,photo,video,audio,link,forward",   "on": 1},
    {"name": "L19", "value": 15000, "perms": "text,sticker,photo,video,audio,link,forward",   "on": 1},
    {"name": "L20", "value": 20000, "perms": "text,sticker,photo,video,audio,forward,link,edit", "on": 1},
]
MALL_ITEMS = []  # [{"name": 商品名, "value": 价格}]
INHERIT_ENABLED = 1
INHERIT_FEE_PERCENT = 0
BUY_ENABLED = 1
BUY_MIN = 1000
BUY_MAX = 100000
REDEEM_CMD = "积分兑换"      # 积分兑换触发词
REDEEM_MAX_PER_USER = 0     # 每人最大兑换数量（0=不限）
REDEEM_START = ""           # 兑换开始时间（YYYY-MM-DD HH:MM，留空不限）
REDEEM_END = ""             # 兑换结束时间（同上，留空不限）
RP_EXCLUSIVE_ENABLED = 1    # 专属红包开关（回复/指定ID红包）
RP_LUCK_ENABLED = 1         # 1=拼手气随机拆分 0=平均分
RP_LOG_ENABLED = 1          # 抢完公布手气排行
MALL_ENABLED = 1            # 积分商城开关
MALL_PAGE_SIZE = 10         # 商城列表每页商品数
MALL_LIST_DELETE_SECONDS = 300  # 兑换/商城列表消息自动删除秒数（5 分钟；按钮要活所以不能 30 秒太短；0=不删）
LEVEL_NOTIFY_ENABLED = 1    # 等级升降群内通知开关
LEVEL_ENABLED = 1           # 积分等级系统总开关（2026-09-09 用户截图「积分等级系统开关」）
LEVEL_KEEP_ON_SPEND = 1     # 消费不掉级（默认开=等级按累计积分算；关闭=按当前余额算，花掉会降级）
                            # 2026-09-13 用户要求：正向开关。旧反向开关 LEVEL_ALLOW_DEMOTE 已迁移（core/settings.py）
LEVEL_SYNC_TAG = 1          # 积分称号同步成员标签开关（用户截图）
LEVEL_MSG_GUARD_ENABLED = 1 # 等级消息管控总开关（按等级限制可发的消息类型）
LEVEL_MSG_WARN_TPL = ("⚠️ {name}，你当前等级「{level}」还不能发送{kind}。\n"
                      "多发消息或参与游戏升级后即可解锁。")
LEVEL_MSG_MUTE_TPL = "🔇 {name} 因频繁发送超出等级权限的消息，已被禁言 {seconds} 秒。"
LEVEL_MSG_WINDOW = 10       # 违规窗口（秒）
LEVEL_MSG_MAX_HITS = 2      # 窗口内违规次数达此值触发惩罚
LEVEL_MSG_PUNISH = 1        # 频繁违规惩罚：0=只提醒 1=禁言 2=踢出
LEVEL_MSG_MUTE_SECONDS = 60 # 违规后禁言秒数（0=不禁言；小于30视为永久）
LEVEL_QUERY_NONE_TPL = "ℹ️ 积分等级未配置（后台「积分系统 → 积分等级」添加）。"
LEVEL_DOWN_NOTIFY_ENABLED = 1   # 用户降级通知开关（用户截图）
LEVEL_DOWN_MSG_TPL_DEFAULT = "📉 {name} 降级到「{level}」。\n💰 当前积分：{balance}"
# 等级可授权的消息类型（键=存储值，值=显示名）。顺序即网页勾选顺序
LEVEL_PERM_OPTIONS = [
    ("text",    "允许发送文字（纯文字，无媒体、非转发）"),
    ("photo",   "允许发送图片"),
    ("video",   "允许发送视频"),
    ("audio",   "允许发送音频"),
    ("sticker", "允许发送贴纸"),
    ("forward", "允许转发消息"),
    ("link",    "允许发送含链接的消息"),
    ("edit",    "允许编辑消息"),
]
LEVEL_PERM_NAMES = {k: v.split("（")[0].replace("允许发送", "").replace("允许转发", "转发").replace("允许编辑", "编辑")
                    for k, v in LEVEL_PERM_OPTIONS}
LEVEL_PERM_DEFAULT = ",".join(k for k, _v in LEVEL_PERM_OPTIONS)   # 旧数据默认全放行
level_msg_violations = {}   # (cid, uid) -> [违规时间戳...]，等级消息越权计数（窗口内）
LEVEL_CMD = "我的等级"
RANK_1_EMOJI = "🥇"
RANK_2_EMOJI = "🥈"
RANK_3_EMOJI = "🥉"
QUERY_CMD = "我的积分"
RANK_CMD = "积分排行"
SIGN_CMD = "签到"
ADMIN_ADJUST_ENABLED = 1
MSG_TPL_DEFAULTS = {
    "sign_msg_tpl": "🎉 {name} 签到成功！\n📅 连续签到 {streak} 天｜💰 +{reward}{bonus}\n💰 当前积分：{balance}",
    "query_msg_tpl": "💰 我的积分：{balance}\n{level_line}📅 今日签到：{signed}（连续 {streak} 天）\n💬 今日聊天获得：{today_chat}",
    "add_msg_tpl": "✅ 已给 {target} {verb} {amount} 积分，当前 {balance}。",
    "rp_msg_grab": "🧧 抢到 {amount} 积分！",
    "rp_msg_none": "手慢了～什么都没抢到～",
    "rp_msg_dup": "❌ 你已经抢过该红包了",
    "rp_msg_poor": "❌ 积分不足：需要 {need}，当前 {balance}。",
    "rp_msg_target": "🎯 这是专属红包，只有 {name} 能抢",
    "rp_msg_log": "{rank} {name}：{amount} 积分",
    "level_up_msg_tpl": "🎉 恭喜 {name} 升级「{level}」！\n💰 当前积分：{balance}",
    "level_down_msg_tpl": "📉 {name} 降级到「{level}」。\n💰 当前积分：{balance}",
    "level_query_msg_tpl": ("🎖 {name} 的等级：{level}\n"
                            "💰 当前积分：{balance}\n"
                            "{base_line}{next_line}"),
    "level_query_none_tpl": "ℹ️ 积分等级未配置（后台「积分系统 → 积分等级」添加）。",
    "level_msg_warn_tpl": ("⚠️ {name}，你当前等级「{level}」还不能发送{kind}。\n"
                           "多发消息或参与游戏升级后即可解锁。"),
    "level_msg_mute_tpl": "🔇 {name} 因频繁发送超出等级权限的消息，已被禁言 {seconds} 秒。",
    "mall_msg_buy": "🛍 购买成功：{item}（-{price} 积分）\n💰 余额 {balance}\n管理员会尽快处理发货。",
    "mall_msg_empty": "🛒 商城暂无商品，管理员可在后台上架。",
    "inherit_msg_ok": "✅ {name} → {target}：{amount} 积分{fee}\n💰 对方到账 {recv}｜你当前 {balance}",
    "redeem_msg_list": "🎁 {goodsName}｜{pointNum} 积分｜剩余 {leftNum}",
    "redeem_msg_ok_group": "🎉 {name} 兑换成功：{goodsName}（-{pointNum} 积分）\n💰 余额 {balance}",
    "redeem_msg_ok_dm": "🎉 你已成功兑换「{goodsName}」（{pointNum} 积分），请联系管理员发货。",
    "invite_ok_group": "🎉 {invitee} 通过 {inviter} 的邀请加入本群！\n💰 {inviter} 获得邀请奖励 {reward} 积分",
    "invite_rank_today_msg": "📈 <b>今日邀请排行</b>",
    "invite_rank_month_msg": "📅 <b>本月邀请排行</b>",
    "invite_rank_all_msg": "🏆 <b>总邀请排行</b>",
    "invite_rank_line_fmt": "{i}. {name}｜邀请 {count} 人",
    "invite_invalid_msg": "⚠️ {name} 的邀请链接无效，请让邀请人重新生成",
    "invite_self_msg": "😅 不能邀请自己哦",
    "force_sub_warn_tpl": "📢 {name}，请先订阅我们的频道再发言～\n- 加入频道：{channels}\n订阅后重新发一次消息即可正常聊天。\n（本提示 {seconds} 秒后自动消失）",
}
INVITE_OK_GROUP = MSG_TPL_DEFAULTS["invite_ok_group"]
INVITE_RANK_TODAY_MSG = MSG_TPL_DEFAULTS["invite_rank_today_msg"]
INVITE_RANK_MONTH_MSG = MSG_TPL_DEFAULTS["invite_rank_month_msg"]
INVITE_RANK_ALL_MSG = MSG_TPL_DEFAULTS["invite_rank_all_msg"]
INVITE_RANK_LINE_FMT = MSG_TPL_DEFAULTS["invite_rank_line_fmt"]
INVITE_INVALID_MSG = MSG_TPL_DEFAULTS["invite_invalid_msg"]
INVITE_SELF_MSG = MSG_TPL_DEFAULTS["invite_self_msg"]
SIGN_MSG_TPL = MSG_TPL_DEFAULTS["sign_msg_tpl"]
QUERY_MSG_TPL = MSG_TPL_DEFAULTS["query_msg_tpl"]
ADD_MSG_TPL = MSG_TPL_DEFAULTS["add_msg_tpl"]
RP_MSG_GRAB = MSG_TPL_DEFAULTS["rp_msg_grab"]
RP_MSG_NONE = MSG_TPL_DEFAULTS["rp_msg_none"]
RP_MSG_DUP = MSG_TPL_DEFAULTS["rp_msg_dup"]
RP_MSG_POOR = MSG_TPL_DEFAULTS["rp_msg_poor"]
RP_MSG_TARGET = MSG_TPL_DEFAULTS["rp_msg_target"]
RP_MSG_LOG = MSG_TPL_DEFAULTS["rp_msg_log"]
LEVEL_UP_MSG_TPL = MSG_TPL_DEFAULTS["level_up_msg_tpl"]
LEVEL_DOWN_MSG_TPL = MSG_TPL_DEFAULTS["level_down_msg_tpl"]
LEVEL_QUERY_MSG_TPL = MSG_TPL_DEFAULTS["level_query_msg_tpl"]
MALL_MSG_BUY = MSG_TPL_DEFAULTS["mall_msg_buy"]
MALL_MSG_EMPTY = MSG_TPL_DEFAULTS["mall_msg_empty"]
INHERIT_MSG_OK = MSG_TPL_DEFAULTS["inherit_msg_ok"]
REDEEM_MSG_LIST = MSG_TPL_DEFAULTS["redeem_msg_list"]
REDEEM_MSG_OK_GROUP = MSG_TPL_DEFAULTS["redeem_msg_ok_group"]
REDEEM_MSG_OK_DM = MSG_TPL_DEFAULTS["redeem_msg_ok_dm"]
FORCE_SUB_WARN_TPL = MSG_TPL_DEFAULTS["force_sub_warn_tpl"]


# 积分系统持久化数据（与主数据同一套脏标记/写盘/备份机制）
sign_data = defaultdict(lambda: defaultdict(dict))   # sign_data[cid][uid] = {"last": "YYYY-MM-DD", "streak": n}
# chat_earn_daily[date][cid][uid] = 当日聊天积分累计（**只增不减**）。一份账本管三件事：
#   ① 聊天每日上限判定（_award_chat_points）②「我的积分」里的当日聊天分 ③「积分流水」按人·天聚合展示。
# 为什么不逐条进 ledger：聊天积分小额高频，逐条记账会把 5000 条台账几天内冲干净，
# 把红包/转赠这些真正需要审查的记录挤掉。按「人·天」聚合成一条，既看得到又不淹台账。
# ⚠️ 2026-09-12 合并：这里**曾经还有第二个账本**（结构相同、只留 2 天、只服务日上限判定）。
#   它与本账本是同一份数据的两份拷贝 —— _award_chat_points() 里两行紧挨着写、中间没有任何
#   提前返回，且「上一次的值 + 本次得分」恒等于「本次得分累加」⇒ 两者逐字节相同，
#   唯一差别只有保留天数。用户点名「聊天分还分为2个账本 你是不是在代码里面写了一堆重复没用的东西」
#   ⇒ 删掉旧账本，三处读点全改读本账本（判定语义不变，留存反而从 2 天变 7 天，更稳）。
#   旧存档里的残留键由 load_data() 一次性搬进来（见那里的迁移注释），历史聊天分不会丢。
chat_earn_daily = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
newbie_rewarded = {}                                 # "cid:uid" -> True 新人欢迎奖励已发放（防重复）
chat_dup_hist = {}                                   # (cid,uid,内容归一化) -> [ts,...] 有效发言查重用
invite_daily = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
# invite_daily[date][cid][inviter] = {"times": 已发奖次数, "points": 已发奖积分}（每日拉新上限用）
mall_orders = []                                     # [{"ts","cid","uid","name","item","price"}]
chat_rules = []                                      # 阿福式聊天积分规则 [{"match","points","on"}] 命中即停；空=走每N字符旧规则
buy_packages = []                                    # 购买积分套餐 [{"name","cny","points","sort","on"}]
rp_packets = {}                                      # pid -> {"cid","from","left_amt","left_n","grabbed":{uid:amt},"ts","msg_id"}
invite_links = defaultdict(dict)                     # cid -> {uid: {"link","invite_id","ts"}} 每人专属邀请链接（必须 defaultdict：恢复/写入走 [cid][uid] 两级，普通 dict 会 KeyError 被吞→整表丢失→归因全失败、邀请进度恒 0）
invite_records = {}                                  # "cid:uid" -> {"cid","inviter","invitee","invitee_name","ts","qualified","rejected","left","award","link"}；旧存档可能带 audit(ok/pending/unmet/rejected) 兼容读取
invite_pending = {}                                  # "cid:uid" -> 进群申请携带的邀请链接（人审批后 join 事件常不带链接，靠这个兜底归因；内存态）
invite_confirmed = {}                                # "cid:uid" -> 邀请人 uid（deep-link START / 主动问按钮 锁定的归因；落盘持久化）
invite_debug = defaultdict(list)                     # cid -> [最近10条邀请链路调试事件]（每环失败不再静默，/邀请调试 可查）
_inv_notice_ts = {}                                  # "cid:uid" -> 上次发「进群未计入邀请/归因失败」提示的时间戳（运行时态）
                                                     # 同一次进群会同时到 chat_member 与服务消息两个事件源，提示类副作用必须按人短窗去重（2026-09-10 用户报「收到 2 条」）



buy_orders = {}                                      # oid -> {"cid","uid","amount","ts"} 购买积分申请（管理员人工确认）
warn_counts = defaultdict(lambda: defaultdict(int))  # warn_counts[cid][uid] = 警告次数（网页成员列表加减）
redeem_goods = []                                    # 积分兑换商品 [{"name","price","left","redeemed","desc","on"}] left=0 不限
redeem_counts = {}                                   # uid -> 全期已兑换次数（每人限购用）
redeem_orders = []                                   # 兑换订单（防伪）：[{"no","ts","cid","uid","item","price","bal"}]，只留最近 500 条
game_flows = []                                      # 游戏对局人对人净转移（德州/金花等，审查"通过游戏故意输牌送分"用），只留最近 2000 条

# ---------- 群组管理数据 ----------
member_profiles = defaultdict(lambda: defaultdict(dict))  # member_profiles[cid][uid] = {"name","first","last","msgs"}
whitelist = defaultdict(set)                         # whitelist[cid] = {uid} 白名单（免疫禁言等）
leave_records = defaultdict(list)                    # leave_records[cid] = [{"ts","uid","name"}] 退群记录(每群留100)
join_requests = defaultdict(list)                    # join_requests[cid] = [{"ts","uid","name"}] 入群申请(每群留100)
# 谁把机器人拉进群：bot_added_by[cid] = {"by": 邀请人uid, "ts": "2026-09-11 13:00", "title": "群名"}
# 用于追溯「未授权群却有人发命令」是谁拉进来的（my_chat_member 事件记录，普通群也抓得到）。
bot_added_by = {}
member_joined_at = defaultdict(lambda: defaultdict(float))  # member_joined_at[cid][uid] = 入群时间戳（新成员观察期用，运行时态）
_bot_app = None   # 运行中的 Application（网页后台跨线程调 bot API 用，post_init 里赋值）
_bot_loop = None  # bot 主事件循环
_BOT_USERNAME = ""  # bot 用户名缓存（兑换按钮跳私聊深链 https://t.me/<用户名>?start=... 用）
admin_logs = []                                      # [{"ts","cid","admin","action","target"}] 管理员操作记录(留300)

# ---------- 防小号资金监管 ----------
BOT_BOOT_TS = time.time()                            # 进程启动时间（/status 运行时长用）
ledger = []                                          # 资金流台账 [{"ts","cid","frm","to","amt","typ"}] 红包领取/转赠逐笔(留5000)
inherit_daily = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # inherit_daily[date][cid][uid] = 当日累计转赠支出
user_first_seen = {}                                 # uid -> 首次与 bot 互动的时间戳（兑换门槛用）
backup_msg_ids = []                                  # 自动备份文件消息ID（管理员私聊，轮换只留7份）
settings_backup_msg_ids = []                         # 设置备份文件消息ID（单独轮换只留7份）
web_pending_otp = {}                                 # 后台二次验证待确认：otp_token -> {"code","exp","ip"}
web_magic_tokens = {}                                # /后台 一键登录：token -> {"uid","exp"}（2分钟、一次性）
# 群组抽奖：每群同时最多一个进行中活动
# lotteries[cid] = {title, prizes, fee, keyword, start_ts, end_ts, msg_id,
#                   participants:[(uid, ts, name)], status, winners, creator, chat_id}
lotteries = {}


_DYN_CMD_OWNED = {}  # gname -> 上次注册的动态指令名（改名后移除旧指令）


# ⚠️ 2026-09-12 「改默认模板 = 看不出效果」的坑（抽奖界面第 2 版）：
#   公告/结果模板是**可保存的设置项**，只要有人点过网页上的「保存全部抽奖设置」，
#   当时的模板就被写进 bot_settings.json 的 fields，之后 load_settings 会用**存档值覆盖
#   模块默认值** —— 于是我把默认模板改成阿福版式，线上看到的还是旧的横幅版式。
#   这里做幂等迁移：只认「旧版内置默认」的特征签名（横幅时代的专属 emoji+标签组合），
#   命中就升到当前默认；**自定义模板绝不匹配、绝不覆盖**（不拿用户数据冒险）。
_LOTTERY_TPL_SIGS = {
    # 设置键 → (旧内置默认的特征签名, 当前默认)
    "lottery_msg_start": ("⏰ 开奖时间：", None),
    "lottery_msg_result": ("🎊━━━", None),
}




BEIJING_TZ = timezone(timedelta(hours=8))
HAND_NAME_CN = {"High Card":"高牌", "Pair":"一对", "One Pair":"一对", "Two Pair":"两对", "Three of a Kind":"三条", "Straight":"顺子", "Flush":"同花", "Full House":"葫芦", "Four of a Kind":"四条", "Straight Flush":"同花顺", "Royal Flush":"皇家同花顺"}
RANK_ICONS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]




# ---------- 数据 ----------
game_chips = defaultdict(lambda: defaultdict(lambda: sget("GAME_STARTING_CHIPS")))  # 统一积分钱包（全游戏/签到/红包/商城共用，初始 5W）
AUTHORIZED_GROUPS = set()
BLACKLISTED_USERS = set()  # 被拉黑、禁止使用该机器人的用户（管理员可解封）
race_history = defaultdict(list)
blackjack_history = defaultdict(list) # 新增 21点历史
race_daily_stats = defaultdict(lambda: [0] * sget("HORSE_COUNT"))
poker_profit_by_date = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
race_profit_by_date = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
blackjack_profit_by_date = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
jinhua_profit_by_date = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
race_jackpot = defaultdict(int)
# 赛车系统加奖已发额：race_subsidy_by_day[日期][群] += 金额（用于「每日上限」判定，2026-09-11 用户要求）
race_subsidy_by_day = defaultdict(lambda: defaultdict(int))
hourly_race_enabled = defaultdict(lambda: False)
# 各调度任务的"作用对象"（网页可配）：每日重置/德州日榜=作用群；备份=接收私聊的管理员
daily_reset_groups = set()    # 默认全授权群，启动时懒填
leaderboard_groups = set()
backup_admins = set()         # 默认 {ADMIN_USER_ID}，启动时懒填
# 调度任务调试：每群最后成功开赛时间 + 跳过原因计数（/定时任务 调试命令读这些）
race_last_sent = {}                                  # cid -> "YYYY-MM-DD HH:MM"
race_skip_stats = defaultdict(lambda: defaultdict(int))  # cid -> {reason: count}
daily_emergency_used = defaultdict(lambda: defaultdict(bool))
# 已实扣的游戏下注，用于全系游戏在重启时自动退款。
# 按游戏类型分条存储，避免多游戏并发时记录互相覆盖：
# pending_game_bets[群ID][用户ID]["21"/"horse"] = {"amount": 100, "mode": "official"}
pending_game_bets = defaultdict(lambda: defaultdict(dict))
last_business_date = ""
active_poker_games, active_horse_races = {}, {}
active_blackjack_games = {}
active_jinhua_games = {}
# 牌桌面板登记表：cid -> 本群当前活着的牌桌消息 id。
# 【为什么必须有】`game_msg_id` 挂在 game **实例**上，但「全群只有一条牌桌」这条不变量
# 的作用域是**群（cid）**。换局（/dz 覆盖 active_poker_games[cid]）时新 game 的
# game_msg_id=None，结构上无法清理上一局的孤儿牌桌；而结算用延迟删除，窗口期内旧牌桌
# 仍可见可点，点一次又刷一条 → 2026-09-12 用户截图「连续 4 条重复牌桌」。
# 以 cid 为键后，任何一次渲染都能清掉本群上一张牌桌（哪怕它属于已被换掉的旧 game）。
_live_panel_msg = {}


recent_poker_reveals = defaultdict(list)  # 德州单赢结算后临时保存赢家牌（每群一个队列，供可选亮牌按钮使用，新单赢不再覆盖旧的）
# ---------- 德州赛季状态（独立账本，每日重置不触碰） ----------
season_active = False
season_id = None
season_name = ""
season_start_ts = 0
season_end_ts = 0
season_points = defaultdict(lambda: defaultdict(int))    # season_points[cid][uid] 赛季分（下注用）
season_games = defaultdict(lambda: defaultdict(int))     # season_games[cid][uid] 参赛局数
season_joined = defaultdict(set)                          # season_joined[cid] = {uid} 报名集合
season_rebuy = defaultdict(lambda: defaultdict(int))      # season_rebuy[cid][uid] 已用应急补分次数
season_lobby_msg = {}                                       # season_lobby_msg[cid] = 赛季大厅看板消息 id（UI 态，不持久化）
season_profit_by_date = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # season_profit_by_date[date][cid][uid] = 当日盈亏（赛季每日重置成 2W 前记录；赛季总排行=7日累计之和）
season_exchange_daily = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # season_exchange_daily[date][cid][uid] = 当日已消耗的聊天积分（兑换每日上限用）
season_exchange_bonus = defaultdict(lambda: defaultdict(int))  # season_exchange_bonus[cid][uid] = 本赛季累计兑换得到的赛季分（「额外底分」：每日重置保留、不计入盈亏榜）
# ★ 结算可靠性两个「运行时态」标记（不持久化：结算只在单进程内发生，重启后 season_active 仍 True 会自动重试）
season_settling = False     # 结算进行中 → 防并发/重叠重入（season_settle 一进来检查）
season_settled_cids = set()  # 本轮结算已推过最终榜的群；推榜失败重试时跳过它们，防重复推榜/重复加冕
# 累计积分账本（只增不减）：等级按它算，兑换实物/商城消费不掉级
# 语义：玩家在本群「历史累计赚到过多少积分」——不含游戏退款/下注返还等原路退回，
#       也不含转赠收到的分（那是他人分的转移，不是新产出）。
total_earned = defaultdict(lambda: defaultdict(int))   # total_earned[cid][uid] = 累计积分
games_played = defaultdict(lambda: defaultdict(int))   # games_played[cid][uid] = 累计参与局数（归零赠送门槛用）
# ---------- 赌神称号（全局唯一，跨群共享荣誉） ----------
user_titles = {}               # user_titles[uid] = {"🎰赌神", ...}  每人拥有的称号集合（赌神全局唯一，其余称号可叠加）
champions_history = []         # [{"season_id","uid","name","score","streak"}] 历届荣誉墙
TITLE_GAMBLING_GOD = "🔱赌神"
title_expiry = {}              # title_expiry[uid][称号] = 到期时间戳（仅限时称号；永久称号不在此）
title_equipped = {}            # title_equipped[uid] = 当前佩戴的称号（玩家手动选择，可覆盖默认显示）
# ---------- 积分商店（称号兑换）：price 价格 / duration 时限秒或 None=永久（统一积分支付） ----------
SHOP_TITLES = {
    # 积分支付（永久，价格从低到高）
    "赌狗":     {"price": 5000, "currency": "game", "duration": None},
    "赌鬼":     {"price": 10000, "currency": "game", "duration": None},
    "赌徒":     {"price": 15000, "currency": "game", "duration": None},
    "散财童子": {"price": 20000, "currency": "game", "duration": None},
    "小赌怡情": {"price": 25000, "currency": "game", "duration": None},
    "见好就收": {"price": 30000, "currency": "game", "duration": None},
    "幸运星":   {"price": 35000, "currency": "game", "duration": None},
    "一夜暴富": {"price": 40000, "currency": "game", "duration": None},
    "鸿运当头": {"price": 45000, "currency": "game", "duration": None},
    "财神爷":   {"price": 55000, "currency": "game", "duration": None},
    "赌怪":     {"price": 65000, "currency": "game", "duration": None},
    "赌侠":     {"price": 85000, "currency": "game", "duration": None},
    "快枪手":   {"price": 105000, "currency": "game", "duration": None},
    "常胜将军": {"price": 135000, "currency": "game", "duration": None},
    "老千":     {"price": 165000, "currency": "game", "duration": None},
    "赌王":     {"price": 200000, "currency": "game", "duration": None},
    "赌霸":     {"price": 240000, "currency": "game", "duration": None},
    "赌魔":     {"price": 280000, "currency": "game", "duration": None},
    "赌圣":     {"price": 320000, "currency": "game", "duration": None},
    "赌尊":     {"price": 360000, "currency": "game", "duration": None},
    "赌皇":     {"price": 400000, "currency": "game", "duration": None},
    "赌帝":     {"price": 450000, "currency": "game", "duration": None},
    "赌仙":     {"price": 500000, "currency": "game", "duration": None},
    "赌魂":     {"price": 550000, "currency": "game", "duration": None},
    "千王之王": {"price": 600000, "currency": "game", "duration": None},
    # 炸金花 / 梭哈系列（永久）
    "金花":     {"price": 20000, "currency": "game", "duration": None},
    "豹子":     {"price": 30000, "currency": "game", "duration": None},
    "一把梭":   {"price": 50000, "currency": "game", "duration": None},
    "闷牌大师": {"price": 80000, "currency": "game", "duration": None},
    "偷鸡圣手": {"price": 100000, "currency": "game", "duration": None},
    "明牌博弈": {"price": 120000, "currency": "game", "duration": None},
    "二三五":   {"price": 150000, "currency": "game", "duration": None},
    "梭哈王":   {"price": 250000, "currency": "game", "duration": None},
    # 德州系列（永久）
    "德州新手": {"price": 2000, "currency": "game", "duration": None},
    "德州小将": {"price": 4000, "currency": "game", "duration": None},
    "德州老千": {"price": 6000, "currency": "game", "duration": None},
    "诈唬大师": {"price": 8000, "currency": "game", "duration": None},
    "葫芦王":   {"price": 10000, "currency": "game", "duration": None},
    "四条王":   {"price": 12000, "currency": "game", "duration": None},
    "同花顺王": {"price": 15000, "currency": "game", "duration": None},
    "皇家同花顺": {"price": 18000, "currency": "game", "duration": None},
    "德扑之王": {"price": 20000, "currency": "game", "duration": None},
    "河牌之王": {"price": 20000, "currency": "game", "duration": None},
}

# 称号图标（展示层）：与 SHOP_TITLES 的 key 一一对应，缺省为空串。赌神自带 🎰 无需在此。
TITLE_ICONS = {
    # 积分支付
    "赌狗": "🐶", "赌鬼": "👻", "赌徒": "🎲", "散财童子": "💸",
    "小赌怡情": "🍵", "见好就收": "🧘", "幸运星": "⭐", "一夜暴富": "💰",
    "鸿运当头": "🍀", "财神爷": "🧧", "赌怪": "👾", "赌侠": "🦸",
    "快枪手": "🔫", "常胜将军": "🏆", "老千": "🃏", "赌王": "👑",
    "赌霸": "🐯", "赌魔": "😈", "赌圣": "✨", "赌尊": "🏔️",
    "赌皇": "🐲", "赌帝": "⚜️", "赌仙": "🧚", "赌魂": "🔥",
    "千王之王": "🎴",
    # 炸金花 / 梭哈系列
    "金花": "🌸", "豹子": "🐆", "一把梭": "💥", "闷牌大师": "🕶️",
    "偷鸡圣手": "🐓", "明牌博弈": "👁️", "二三五": "☄️", "梭哈王": "⚔️",
    # 德州系列
    "德州新手": "🌱", "德州小将": "🎖️", "德州老千": "🎭", "诈唬大师": "😏",
    "葫芦王": "🏠", "四条王": "🀄", "同花顺王": "♠️", "皇家同花顺": "💎",
    "德扑之王": "🤴", "河牌之王": "🌊",
}



# ---------- 称号加封/撤销（网页后台专用入口：给称号不扣积分，与「商店兑换」解耦） ----------

# ---------- 昵称缓存（持久化）：群里每条消息/回调直接拿 effective_user 真名，避免 get_chat 失败回退成"玩家{uid}" ----------
user_names = {}                # user_names[uid] = "真名"（原始串，输出时再 html.escape）
chat_name_cache = {}           # chat_name_cache[cid] = 群名（入站消息自动缓存，授权列表等无需再调 get_chat）
# 用于老虎机等功能的冷却时间限制。
# 高性能保存逻辑变量
data_dirty = False
save_event = None # 延迟初始化
data_save_lock = threading.Lock()
background_tasks = set()
# 用户级钱包锁：防止同一用户同时进入多个扣款/派彩路径导致并发负分
wallet_locks = defaultdict(asyncio.Lock)



logger.info("数据文件：%s", _data_file_status())
load_data()
logger.info("数据加载完成：%s｜授权群 %d 个 · 管理员 %d 名 · 玩家名缓存 %d 条",
            _data_file_status(), len(AUTHORIZED_GROUPS), len(BOT_ADMINS), len(user_names))
archive_old_profit_data()
force_save_now()  # 归档结果立即物理落盘，避免启动后 60 秒内崩溃丢失归档

# ---------- Telegram 工具 ----------



# Telegram HTML 允许的成对标签。跨分片补开/补闭**只认这些**——
# 用白名单而非"任意 <x>"，避免把消息正文里恰好形如 <foo> 的普通文本误当标签补闭。
_TG_PAIR_TAGS = frozenset({
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre", "a", "span", "tg-spoiler", "blockquote", "tg-emoji",
})
_HTML_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^<>]*)?>")



TURN_NOTICE_DELETE_SECONDS = 60   # 「轮到谁行动」提醒的存活秒数（2026-09-12 用户指定 1 分钟）



# ══════════════════════════════════════════════════════════════════════════════
# 自动删除：从「opt-in」改成「opt-out」（2026-09-11 用户第 N 次追问后根治）
#
# 【用户的问题】「为什么新加的东西永远不会自动删除？是代码的问题还是我的问题？」
# 【答案】是**代码的架构问题**。旧设计里「删除」是**白名单**：
#   只有走 send_reply / send_settle / schedule_notice_delete 的消息才会被排入删除队列；
#   任何一处直接 `bot.send_message(...)` / `safe_send(...)` 的新功能，**默认就是永不删除**。
#   于是每加一个新功能，只要作者（我）忘了手写一行 schedule_delete，消息就永久留在群里。
#   —— 这不是用户设置错了，是「默认不删」这个默认值错了。
#
# 【根治】把默认值反过来：**群消息 + 没有按钮 ⇒ 默认排入删除**。
#   · 有按钮 = 交互面板（牌桌/下注/翻页/抢红包），删了就没法玩 ⇒ 永不默认删；
#   · 私聊（cid > 0）= 玩家自己的收件箱 ⇒ 不删；
#   · 需要长期保留 / 自己管生命周期的，由**发送点自己显式声明**（见下）；
#   · 时长由后台「通用与应急 → 其他消息默认删除(秒)」控制，0 = 关闭这套默认（回到旧行为）。
#   ⇒ 以后**新增**的任何群消息，默认就会被回收，不需要我再记得写删除。
#
# 【2026-09-14 再改：不再「猜是谁发的」】原先豁免靠**调用方函数名**去白名单里查
#   （_AUTODEL_KEEP_FUNCS / _AUTODEL_OWN_FUNCS / _AUTODEL_MARKUP_IGNORE_FUNCS +
#   sys._getframe 回溯）。缺陷：**函数一改名 / 被合并 / 搬走，白名单就静默失配** ——
#   该删的不删、不该删的被删，不报错、不打日志（这是「自动删除反复出问题」的真正根因）。
#   现已整层删除，改成「发送时自己声明」，调用方名字不再参与任何判断：
#     · 行内声明：autodel_keep=True（永久保留）/ autodel_secs=N（指定秒数）/
#                autodel_own=True（自己管）；
#     · 拿得到消息对象时：own_messages(app, cid, ids, seconds)（0 = 撤销默认删除）。
#   守卫：test_autodel_explicit.py（含 AST 契约，落点多一处少一处都红）。
# ══════════════════════════════════════════════════════════════════════════════
# 补丁包装函数的统一前缀（send_message / send_photo / ... 各有一个包装）。
# ⚠️ 2026-09-14 起它只用于「幂等：别套两层补丁」与包装函数命名，**不再用于调用方溯源**。
_AUTODEL_PATCH_PREFIX = "_autodel_patched_"
# 要打补丁的 Bot API 清单。⚠️ 只补 send_message 的话，用 send_photo / send_document 发的
# 新功能又会「永不删除」—— 这正是 2026-09-11 用户第二次追问「怎么又没删」的根因。
_AUTODEL_PATCH_METHODS = (
    "send_message", "send_photo", "send_animation", "send_document", "send_video",
    "send_voice", "send_audio", "send_video_note", "send_sticker",
    "copy_message", "forward_message",
)




# ---------- 德州界面 / 流程 ----------

# ==================== 赛车 ====================

# ---------- 权限与命令 ----------


# ---------- 21点 界面与逻辑 ----------


# ==================== 大话骰（吹牛·港式标准） ====================
# 2026-09-11 按《大话骰规则_港式标准.md》实现：
# 万能1 / 叫「X个1」翻倍计且1不当万能 / 首手禁叫1 / 严格越叫越大 /
# 开骰无平局（实际≥叫的→开骰者输；实际<叫的→被开者输） /
# 掉骰子多轮制（输家掉1骰、全员重摇、输家先叫、归零出局、幸存者通吃奖池）。

DICE_ANTE = 200           # 底注（开局一次性扣进奖池，弃局作废）
# 2026-09-13 用户要求「两人局 / 多人局底注分开设定」：
# 下面两个为 0 时回落到 DICE_ANTE（默认 0 ⇒ 行为与改造前完全一致，老群不受影响）。
DICE_ANTE_DUEL = 0        # 两人局底注（0=用 DICE_ANTE）
DICE_ANTE_MULTI = 0       # 多人局（3 人及以上）底注（0=用 DICE_ANTE）
DICE_DICE_COUNT = 5       # 每人骰子数
DICE_WILD_ONE = 1         # 1万能牌开关（关=无万能局：1 就是普通点数，首手可叫1）
DICE_STRAIGHT_ZERO = 1    # 顺子算0个（0=关 / 1=仅两人局 / 2=所有人数；含“假顺”=用万能1补位凑成）
DICE_LEOPARD_BONUS = 1    # 豹子加成（0=关 / 1=仅两人局 / 2=所有人数）：纯豹+2、花豹+1（2026-09-11 群友口径）
DICE_DROP_DICE = 0        # 掉骰子多轮制（0=关：任何人数都一把定胜负、一局即结算；1=开：三人以上才掉骰）
DICE_THINK_SECONDS = 60   # 叫牌思考秒数（超时自动开骰/最小叫牌，防卡死）
DICE_MAX_PLAYERS = 10     # 单桌最多人数（2026-09-11 群友要求：最多 10 个人）
DICE_ENABLED, DICE_ADMIN_ONLY = 1, 0

active_dice_games = {}

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}



# ==================== 通用 sendDice 游戏（足球/篮球/飞镖/保龄球） ====================

# 配置：每个游戏一个条目。bets 每项为 (key, 按钮标签, 赢的value集合, 赔率分子, 赔率分母)
























# ==================== 梭哈（Five Card Stud） ====================
# treys suit_int 是位掩码（s=1,h=2,d=4,c=8），映射为梭哈花色优先级 ♠>♥>♦>♣
























# ==================== 炸金花（三张牌，闷牌偷鸡） ====================
JINHUA_ANTE = 200        # 炸金花底注
JINHUA_BASE = 100        # 闷牌单位（看牌者跟注/加注金额为其 2 倍）
RACE_ODDS_CAP = 10.0     # 赔率上限（倍）。0=无上限。防低胜率马一把押中爆出几万分冲垮经济
# 赔率里「真实胜率」的权重（0~100，默认 35）。0 = 只看押注金额（2026-09-11 起的旧行为）；
# 100 = 只看胜率。2026-09-13 用户报障「胜率不同、赔率却一样」——旧口径赔率只认注额，
# 两匹马注额一样时胜率 35% 与 18% 的赔率完全相同，看着就是个 bug。详见 HorseRace.odds()。
RACE_ODDS_RATE_WEIGHT = 35
JINHUA_SEEN_DOUBLE = 1   # 炸金花看牌者投注加倍开关（1=经典规则看牌×2；0=看牌闷牌同价）
                         # ⚠️ 2026-09-13 用户选 B「恢复看牌×2」，默认值由 0 改回 1。
                         #   这里原先注释写「群反馈中途看牌被加倍劝退」——**归因错误**。
                         #   群友真正烦的是「一有人看牌，牌桌界面就被删掉重发」，跟加倍没关系；
                         #   那条已在 2026-09-11 改成 edit_only（原地编辑、绝不删消息）修好了。
                         #   守卫：test_rank_paging.py 第 8 组、features/jinhua/__init__.py _sync_jinhua_msg。
DAILY_RESET_TIME = "00:00"   # 每日重置时刻（赛季分重置、德州日榜翻日等）
DAILY_RESET_ENABLED = 1      # 每日重置开关（后台「定时任务」可关；关闭期间到点跳过，重开后从下一周期生效）
LEADERBOARD_TIME = "23:50"   # 德州当日榜定时推送时刻
LEADERBOARD_ENABLED = 1      # 德州日榜推送开关
RACE_HOURLY_MINUTE = 0       # 赛车每小时自动开赛：整点后第几分钟
RACE_AUTO_ENABLED = 1        # 赛车自动开赛总开关（仍受时段/分钟与赛车游戏开关限制）
RACE_HOURLY_START = 18       # 自动开赛时段-起始点(几点,含)，如 9 = 9点起才开赛
                             # 2026-09-13 用户确认：默认值由「全天(0→23)」改为 18→2（只在傍晚到凌晨开赛）。
RACE_HOURLY_END = 2          # 自动开赛时段-结束点(几点,含)，如 22 = 22点那场仍开；
                             # 起始>结束 = **跨午夜档**（如 18→2 = 18:00 到次日 02:59），2026-09-11 用户要求
                             # 2026-09-13 用户确认默认值 18→2（见上一行 RACE_HOURLY_START）。



# ---------- 赛车系统加奖（2026-09-11 用户要求：群友嫌赛车没人玩，加个"白拿"钩子） ----------
RACE_SUBSIDY_ENABLED = 1      # 系统加奖开关
RACE_SUBSIDY_AMOUNT = 100     # 每场加奖金额（积分）——押中者按注额比例分，无人押中则不发、不计额度
RACE_SUBSIDY_MIN_PLAYERS = 2  # 加奖生效的最少下注人数（防单人自押自薅）
RACE_SUBSIDY_DAILY_CAP = 1000 # 每群每日加奖上限（0=不限），防连续开赛把积分放水
RACE_SUBSIDY_AUTO_ONLY = 1    # 加奖只给「定时自动开赛」的赛车（2026-09-11 用户要求：个人发起的赛车不派奖）
RACE_PARIMUTUEL = 1           # 赛车赔率模型（2026-09-11 用户+群友反馈「赔率不是动态的、不是根据下注金额的」）：
                              #   1 = 押注池（parimutuel）：赔率 = 总池 ÷ 该马注额，押得越少赔率越高，
                              #       全体押中者合计恰好分完总池 —— 不再需要「系统补分」。
                              #   0 = 旧模型（1/胜率 × 注额压力因子 + 单调约束 + 同显示胜率分组统一），
                              #       会把赔率抹平（四匹马赔率全等），仅在需要回退时使用。
RACE_RAKE_PERCENT = 5         # 赛车抽水比例（%）。口径 = 群友原话（2026-09-11 21:50）：
                              #   「反正就是投注总数，全部赔给押中的人，然后抽5%」
                              #   ⇒ 基数 = **派彩**：总池 1000 就抽 50、押中者合计到手 950。
                              #   （不是按「净赢」抽 —— 那个口径只在重仓独中时才抽得到钱，不是群友要的。）
                              # ⚠️ 抽出来的钱**不销毁**：进底池（race_jackpot），下一期并入赔付池、
                              #   由押中者按注额比例瓜分。否则「抽水 + 无人押中的整池」会永久冻死，
                              #   就是群友骂的「貔貅，只进不出」（见 settle() 与 odds()）。
                              # 0 = 赛车不抽水。全局 RAKE_PERCENT（10%）仍只管别的游戏。
                              # 为什么赛车要单独一档：别的游戏是「对赌」（钱从输家到赢家，抽水是正常回收），
                              #   赛车是**押注池**（钱本来就是玩家自己的池子），抽多了就是白拿群友的分。



INHERIT_DAILY_LIMIT = 0      # 每人每日转赠总额上限（0=不限，防小号互刷）
MALL_MIN_AGE_DAYS = 0        # 商城兑换门槛：与机器人首次互动满 N 天（0=不限）
MALL_MIN_ACTIVE_DAYS = 0     # 商城兑换门槛：有游戏盈亏记录的天数 ≥N（0=不限）
FUND_FLOW_ALERT = 10000      # 资金流审查页：单对单向累计超过此值标红
BROADCAST_ENABLED = 1        # 大奖战报自动广播开关（推送到其他授权群，制造气氛）
BROADCAST_MIN_AMOUNT = 20000 # 战报阈值：单局净赢 ≥ 此值才广播
BACKUP_INTERVAL_HOURS = 24   # 自动备份间隔（小时），启动时读取
BACKUP_ENABLED = 1           # 自动备份开关（job 常驻，回调里查开关，保存即时生效）
RAKE_ENABLED = 1             # 游戏抽水总开关（官方模式结算后对赢家净赢抽成，回收销毁不回流奖池）
RAKE_PERCENT = 10            # 抽水比例（%）：赢家净赢 × 比例
RAKE_MIN_NET = 0             # 抽水门槛：单局净赢低于此值不抽（0=全抽）
JINHUA_HAND_NAMES = {5: "豹子", 4: "同花顺", 3: "金花", 2: "顺子", 1: "对子", 0: "散牌"}







# ==================== 牛牛 PVP ====================




























# ---------- 德州赛季命令 ----------



FLOW_PEER_MAX = 8



# 网页「积分加减分」最近操作记录（仅内存展示用，重启即清空 —— 不是账本，账本在 ledger）
WEB_POINT_ADJ_LOG = []




# ══════════ 排行榜分页（2026-09-11 用户要求：群里 100+ 人，榜单太长刷屏，改「一页 10 人 + 翻页」） ══════════
# 设计：榜单消息只有一条，翻页 = **原地编辑**同一条（不会刷屏、也不会产生「删除了消息」提示）。
# 消息**不自动删除**（要留给人翻页），所以不走 send_reply / REPLY_DELETE_SECONDS。
RANK_PAGE_SIZE = 10
# 网页后台「群组管理」4 张记录表每页条数（2026-09-11 用户：别写死条数，要能翻页看全部）

_RANK_TITLES = {
    "points":       "💰 积分榜",
    "profit":       "🏆 累计盈利榜",
    "texas_day":    "🃏 德州当日盈亏",
    "other_profit": "🎮 其他游戏累计盈亏",
}
# 榜单副标题：一句话说清「这个榜排的是哪个数」（2026-09-13 用户选 Q15=A：
# 「每个榜都写清楚它排的是哪个数」）。放标题下面**单独一行** ——
# 标题和按钮都不能加长，会把消息撑宽（见下面 _RANK_BTNS 的注释）。
_RANK_SUBTITLES = {
    "points":       "排的是「当前积分余额」（花掉的会扣）",
    "profit":       "排的是「游戏累计赢输」：炸金花 + 21点 + 赛车",
    "texas_day":    "排的是「今天德州的赢输」（每天 0 点重新开始）",
    "other_profit": "排的是「游戏累计赢输」：炸金花 + 21点 + 赛车",
}
# 按钮用**短标签**（2026-09-11 用户截图「这么宽吗 UI都不会做了？」）：
# 按钮文案过长会把整条消息撑宽，且 Telegram 会把超长按钮截断成「累计盈利榜（总数…」。
_RANK_BTNS = {
    "points":       "💰 积分榜",
    "profit":       "🏆 累计盈利",
    "texas_day":    "🃏 德州当日",
    "other_profit": "🎮 其他累计",
}
# 每种榜可切换到哪个榜（切换按钮行）；单元素 = 无切换按钮
_RANK_GROUPS = {
    "points":       ("points", "profit"),
    "profit":       ("points", "profit"),
    "texas_day":    ("texas_day", "other_profit"),
    "other_profit": ("texas_day", "other_profit"),
}
# 榜单里昵称最长显示多少字（长昵称会把消息撑得很宽，超出用 … 收尾）

_RANK_SIGNED = {"profit", "other_profit", "texas_day"}   # 盈亏榜带 +/-；积分榜不带




# 进群判定：目标状态白名单。**必须含 restricted** —— 群若开启「新成员默认限制」，
# Telegram 推的入群事件 new.status 就是 restricted；只认 member 会把这类新人整条跳过
# （不发验证、不记 member_joined_at → 观察期/强制订阅「只拦新人」也一起失效）。
_JOIN_IN_CHAT = ("member", "restricted", "administrator", "creator")




# ==================== 群管中心：敏感词 / 域名白名单 / 入群验证 / 观察期巡检 ====================
_URL_HOST_RE = re.compile(r"(?:https?://)?([a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+)(?:[:/]|\s|$)", re.I)



_FSUB_RE_URL = re.compile(r"(?:t\.me/|telegram\.me/)(?:s/)?([A-Za-z0-9_]{4,})")
_FSUB_RE_HANDLE = re.compile(r"@?([A-Za-z0-9_]{4,})")



_gate_kicked_ts = {}   # "cid:uid" -> 上次硬门槛踢人的时间戳（双事件源去重，运行时态）





# ---------- 积分系统（统一钱包） ----------
# 累计积分账本：所有「真产出」入口调用 _earn_add 记账；等级按累计积分算，
# 因此花积分兑换实物/商城消费不会掉级（此前按余额算，消费即降级 = 反激励）。


# ── Telegram 成员标签（setChatMemberTag）─────────────────────────────────────
# 🚨 2026-09-11 用户报「有积分的人也没标签」——**根因：这个方法在 PTB 20.8 里根本不存在**
#    （成员标签是 Bot API 9.1 才加的新能力），旧代码 `await app.bot.set_chat_member_tag(...)`
#    每次都抛 AttributeError，又被 `except Exception` 静默吞掉（只写 debug 日志）
#    ⇒ **上线以来一次都没成功过**，且不报错、不写警告，完全无声。
#    修法：走 PTB 官方通用出口 `Bot.do_api_request`（20.8 新增，文档明说"用于本库还没
#    封装的新方法"）；若将来 PTB 升级并原生支持，则自动优先用原生方法。
TAG_MAX_LEN = 16       # 官方限制：0-16 字符，且**不允许 emoji**
TAG_SYNC_MAX = 300     # 单次批量同步人数上限
# 🔴 2026-09-11 生产实测（用户截图）：间隔 0.06 秒 = 每秒 16.7 次
#    ⇒ 第 27 个人就被「Flood control exceeded. Retry in 38 seconds」挡下。
#    Telegram 对「管理员类操作」约 **20 次/分钟/群**，故间隔取 3.2 秒（≈18.7 次/分）。
#    ⚠️ **这个数字是查出来的，不是我拍脑袋定的**——上一轮写 0.06 就是没查资料，
#    教训：凡是"速率/上限/配额"类参数，必须先查平台文档或实测，不能凭感觉填个小的。
TAG_SYNC_GAP = 3.2
TAG_SYNC_RETRIES = 2   # 遇限速：同一个人最多等 RetryAfter 后重试几次
_TAG_SYNCING = set()   # 正在同步的群 ID（防重复触发：用户连点两次会打爆配额）




_BG_TASKS = set()   # 后台长任务引用（**必须持引用**，否则可能被 GC 回收，见 SKILL §4.28）




# =============== 群组抽奖 ===============

POKER_WATCHDOG_SECONDS = 20   # 德州看门狗巡检间隔（秒）




# ---------- 积分转赠 / 购买积分（统一钱包） ----------
    # 转赠是「人对人转移」，不产生新积分 → 不写入 total_earned（否则小号互转即可刷等级）
    # 因此这里不做等级变动检查：等级只看真实获得，不看分在谁手上。



# ---------- 群组管理（禁言/封禁/白名单/退群记录/操作记录） ----------

# ---------- 邀请系统：专属链接追踪进群、合格结算、排行 ----------
# 记录状态语义（2026-09-08 改造，替代 audit 审核层 + 前置死字段）：
#   qualified=True = 已合格（本群达标）· rejected=True = 进群硬门槛不满足(无头像/用户名)永不计
#   award>0 = 已发放奖励；旧存档 audit=="ok" 视为合格、audit=="rejected" 视为拒绝（兼容读取）。



# ---------- 定时任务调试 ----------

# ---------- 数据备份/恢复 ----------

# Telegram 原生 / 菜单（网页「命令管理」页可改，存 bot_settings.json 的 tg_menu；命令仅限英文小写/数字/下划线）
DEFAULT_TG_MENU = [
    ("start", "开始"), ("help", "功能帮助"), ("dz", "德州扑克"), ("sc", "赛车"), ("21", "21点"), ("mylv", "我的等级"), ("jifen", "积分兑换"),
    ("jinhua", "炸金花"), ("sign", "每日签到"), ("mypoints", "我的积分"), ("mall", "积分商城"),
    ("end", "结束当前游戏"), ("add", "加/减积分(正加负减)"), ("cx", "盈亏查询"), ("ph", "排行榜"),
    ("sq", "授权群组"), ("qxsh", "取消授权"), ("addadmin", "添加机器人管理员"), ("deladmin", "移除机器人管理员"),
    ("adminlist", "查看管理员列表"), ("authlist", "查看已授权群"), ("autosm", "切换整点自动赛车"),
    ("backup", "备份数据"), ("restore", "恢复数据"), ("season", "德州赛季"), ("seasonjoin", "赛季报名"),
    ("seasonrank", "赛季榜"), ("seasonhelp", "赛季帮助"), ("seasonstart", "赛季强制开赛(管理员)"),
    ("seasonend", "赛季提前结算(管理员)"), ("god", "赌神称号/荣誉墙"), ("godgrant", "封赌神(管理员)"),
    ("godrevoke", "撤赌神(管理员)"), ("shop", "积分商店-称号兑换"), ("redeem", "兑换称号"),
    ("mytitles", "查看我的称号"), ("equip", "佩戴称号"), ("seasonpoints", "加减赛季分(管理员)"),
    ("ban", "拉黑玩家(管理员)"), ("unban", "解封玩家(管理员)"), ("banlist", "查看黑名单(管理员)"),
    ("list", "管理总览(管理员/群/黑名单)"),
    ("record", "个人战绩"), ("status", "机器人自检(管理员)"),
    ("report", "举报消息给管理(回复一条消息用)"),
]
TG_MENU = [list(t) for t in DEFAULT_TG_MENU]


# 命令路由：支持中文命令（Telegram 命令菜单只认拉丁字符，故用 MessageHandler 解析 /中文）
CMD_ALIASES = {
    # 中文命令
    "开始": cmd_start, "菜单": cmd_help, "帮助": cmd_help, "help": cmd_help,
    "德州": cmd_dz, "德州扑克": cmd_dz,
    "赛车": cmd_sm, "sc": cmd_sm, "赛马": cmd_sm,
    "21点": cmd_21, "二十一点": cmd_21,
    "结束": cmd_end, "终止": cmd_end, "结束游戏": cmd_end, "终止游戏": cmd_end, "终止比赛": cmd_end,
    "加积分": cmd_add, "加分": cmd_add,
    "盈亏": cmd_cx, "查询": cmd_cx,
    "排行": cmd_ph, "排行榜": cmd_ph, "积分榜": cmd_ph, "积分": cmd_ph,
    "授权": cmd_sq,
    "取消授权": cmd_qxshouquan,
    "授权列表": cmd_auth_list, "authlist": cmd_auth_list,
    "拉黑": cmd_ban, "ban": cmd_ban,
    "解黑": cmd_unban, "解封": cmd_unban, "取消拉黑": cmd_unban, "unban": cmd_unban,
    "黑名单": cmd_banlist, "黑名单列表": cmd_banlist, "banlist": cmd_banlist,
    "列表": cmd_list_all, "list": cmd_list_all, "总览": cmd_list_all,
    "举报": cmd_report, "report": cmd_report, "投诉": cmd_report,
    "加管理员": cmd_addadmin,
    "减管理员": cmd_deladmin,
    "管理员列表": cmd_admin_list, "管理员": cmd_admin_list,
    "adminlist": cmd_admin_list,
    "备份": cmd_backup,
    "恢复": cmd_restore,
    "炸金花": cmd_jinhua, "jinhua": cmd_jinhua, "zjh": cmd_jinhua, "金花": cmd_jinhua,
    "大话骰": cmd_dice, "大話骰": cmd_dice, "吹牛": cmd_dice, "摇骰": cmd_dice,
    "签到": cmd_sign, "每日签到": cmd_sign, "签到排行": cmd_sign_rank,
    "我的积分": cmd_my_points, "积分排行": cmd_points_rank, "我的等级": cmd_my_level, "积分兑换": cmd_points_redeem, "jifen": cmd_points_redeem,
    "积分商城": cmd_mall, "商城": cmd_mall, "购买": cmd_mall_buy,
    "红包": cmd_redpacket, "发红包": cmd_redpacket,
    "战绩": cmd_record, "个人战绩": cmd_record, "record": cmd_record,
    "自检": cmd_status, "运行状态": cmd_status, "status": cmd_status,
    "网页码": cmd_webcode, "验证码": cmd_webcode, "登录码": cmd_webcode, "webcode": cmd_webcode,
    "开奖": cmd_lottery, "抽奖": cmd_lottery, "lottery": cmd_lottery,
    "后台": cmd_weblogin, "登录后台": cmd_weblogin, "后台登录": cmd_weblogin, "weblogin": cmd_weblogin,
    "赛季": cmd_season_play, "赛季赛": cmd_season_play,
    "赛季报名": cmd_season_join, "报名赛季": cmd_season_join,
    "赛季榜": cmd_season_rank, "赛季排名": cmd_season_rank,
    "赛季帮助": cmd_season_help, "赛季说明": cmd_season_help, "赛季赛帮助": cmd_season_help,
    "赛季开赛": cmd_season_start, "赛季结束": cmd_season_end,
    "赌神": cmd_god, "荣誉墙": cmd_god,
    "封赌神": cmd_god_grant, "撤赌神": cmd_god_revoke,
    "商店": cmd_shop, "积分商店": cmd_shop, "称号商店": cmd_shop, "shop": cmd_shop,
    "兑换": cmd_redeem, "兑换称号": cmd_redeem, "redeem": cmd_redeem,
    "我的称号": cmd_my_titles, "我的头衔": cmd_my_titles, "mytitles": cmd_my_titles,
    "佩戴": cmd_equip, "佩戴称号": cmd_equip, "equip": cmd_equip,
    "赛季分": cmd_season_points, "加赛季分": cmd_season_points, "减赛季分": cmd_season_points, "seasonpoints": cmd_season_points,
    "兑换赛季": cmd_season_exchange, "赛季兑换": cmd_season_exchange, "积分换赛季": cmd_season_exchange,
    "兑换赛季分": cmd_season_exchange, "seasonexchange": cmd_season_exchange,
    # 2026-09-10 用户指定：聊天积分→赛季分 的主推触发词（更好记）
    "游戏积分兑换": cmd_season_exchange, "游戏积分换赛季": cmd_season_exchange, "赛季分兑换": cmd_season_exchange,
    # 旧英文/数字别名（保留兼容，仍可用）
    "start": cmd_start, "help": cmd_help, "dz": cmd_dz, "sm": cmd_sm,
    "21": cmd_21, "end": cmd_end,
    "END": cmd_end, "add": cmd_add, "adddz": cmd_add,
    "cx": cmd_cx, "ph": cmd_ph, "sq": cmd_sq, "qxsh": cmd_qxshouquan,
    "addadmin": cmd_addadmin, "deladmin": cmd_deladmin,
    "autosm": cmd_autosm, "backup": cmd_backup, "restore": cmd_restore,
    "season": cmd_season_play, "seasonplay": cmd_season_play,
    "seasonjoin": cmd_season_join, "seasonrank": cmd_season_rank,
    "seasonstart": cmd_season_start, "seasonend": cmd_season_end,
    "god": cmd_god, "godgrant": cmd_god_grant, "godrevoke": cmd_god_revoke,
    "sign": cmd_sign, "signrank": cmd_sign_rank, "mypoints": cmd_my_points,
    "pointsrank": cmd_points_rank, "mall": cmd_mall, "buy": cmd_mall_buy,
    "禁言": cmd_mute, "mute": cmd_mute, "解禁": cmd_unmute, "unmute": cmd_unmute,
    "群封": cmd_groupban, "groupban": cmd_groupban, "群解封": cmd_groupunban, "groupunban": cmd_groupunban,
    "放行": cmd_jv_pass, "验证放行": cmd_jv_pass, "jvpass": cmd_jv_pass,
    "白名单": cmd_whitelist, "加白": cmd_whitelist_add, "删白": cmd_whitelist_del,
    "群管理员": cmd_adminlist_tg, "admins": cmd_adminlist_tg,
    "转赠": cmd_inherit, "继承": cmd_inherit, "转让": cmd_inherit, "transfer": cmd_inherit,
    "充值": cmd_buy_points, "购买积分": cmd_buy_points, "topup": cmd_buy_points,
    "link": cmd_invite_link, "邀请链接": cmd_invite_link, "邀请": cmd_invite_link,
    "my_invite": cmd_my_invite, "我的邀请": cmd_my_invite, "邀请进度": cmd_my_invite,
    "invite_debug": cmd_invite_debug, "邀请调试": cmd_invite_debug,
    "invite_test": cmd_invite_test, "测试邀请": cmd_invite_test, "邀请自测": cmd_invite_test,
    "invite_report": cmd_invite_report, "报备入群": cmd_invite_report, "邀请报备": cmd_invite_report,
    "schedule_status": cmd_schedule_status, "定时任务": cmd_schedule_status, "调度状态": cmd_schedule_status,
    "流水": cmd_points_flow, "积分流水": cmd_points_flow,
    "同步标签": cmd_sync_tags, "同步头衔": cmd_sync_tags, "刷新标签": cmd_sync_tags,
    "synctags": cmd_sync_tags,
    "今日邀请排行": cmd_invite_rank_today, "本月邀请排行": cmd_invite_rank_month, "总邀请排行": cmd_invite_rank_all,
}
# 动态指令接管默认名：网页改指令后，旧默认名同步失效
_DYN_CMD_OWNED.update({"QUERY_CMD": "我的积分", "SIGN_CMD": "签到", "RANK_CMD": "积分排行",
                       "INVITE_LINK_CMD": "link", "INVITE_RANK_TODAY_CMD": "今日邀请排行",
                       "INVITE_RANK_MONTH_CMD": "本月邀请排行", "INVITE_RANK_ALL_CMD": "总邀请排行"})

# ---------- 命令管理：别名覆盖层（网页「命令管理」页编辑，保存立即生效） ----------
BASE_CMD_ALIASES = dict(CMD_ALIASES)   # 出厂别名基线（只读）
_HANDLERS_BY_NAME = {}
for _fn in set(BASE_CMD_ALIASES.values()):
    _HANDLERS_BY_NAME[_fn.__name__] = _fn
CMD_ALIAS_OVERRIDES = {}               # 处理函数名 -> "别名1,别名2,..."

_CMD_FACTORY_DEFAULTS = {"QUERY_CMD": "我的积分", "SIGN_CMD": "签到", "RANK_CMD": "积分排行",
                         "LEVEL_CMD": "我的等级", "REDEEM_CMD": "积分兑换",
                         "INVITE_LINK_CMD": "link", "INVITE_RANK_TODAY_CMD": "今日邀请排行",
                         "INVITE_RANK_MONTH_CMD": "本月邀请排行", "INVITE_RANK_ALL_CMD": "总邀请排行"}



# ---------- 云平台保活 + 云端持久化（Render / Zeabur 等无持久磁盘的平台用）----------

_net_err_log = []   # 最近网络类异常的时间戳（5 分钟滑动窗口，用于告警降噪）

_NET_ERR_NAMES = ("NetworkError", "TimedOut", "ReadError", "ConnectError", "WriteError",
                  "ReadTimeout", "ConnectTimeout", "PoolTimeout", "RemoteProtocolError")



def main():
    global save_event
    load_settings()  # 先套用网页端保存的设置，再启动 bot
    token = os.environ.get("BOT_TOKEN")
    if not token: logger.error("未设置 BOT_TOKEN"); return
    
    # 在主循环启动前初始化 Event
    save_event = asyncio.Event()
    
    # 资金系统：关闭并发更新，串行处理所有 update handler，消除「检查余额→扣款」之间的竞态
    # （后台任务如赛车动画、定时调度仍为并发；仅 handler 之间不再交错，杜绝并发负分）。
    # 超时放大到 30s：默认 5s 在部分云平台（Railway 美西等）首次连 api.telegram.org 会
    # 直接 ReadTimeout 导致启动即崩；get_updates_read_timeout 必须 > getUpdates 的 timeout(10s)
    builder = (Application.builder().token(token)
               .concurrent_updates(False)
               .connect_timeout(30.0)
               .read_timeout(30.0)
               .write_timeout(30.0)
               .get_updates_read_timeout(42)
               .pool_timeout(10.0)
               .post_init(post_init)
               .post_shutdown(post_shutdown))
    app = builder.build()

    # 云平台保活：健康检查服务，供 UptimeRobot 定时 ping 防止休眠
    start_health_server()

    # 云端持久化：每 24 小时自动把数据备份发给管理员，容器重启可用 /restore 恢复
    if getattr(app, "job_queue", None) is not None:
        app.job_queue.run_repeating(auto_backup, interval=max(1, int(sget("BACKUP_INTERVAL_HOURS"))) * 3600, first=60)
        logger.info("自动备份任务已注册：每 %s 小时一次", sget("BACKUP_INTERVAL_HOURS"))
        # 群管中心：入群验证超时巡检（60s）+ 观察期到期巡检（10 分钟）
        app.job_queue.run_repeating(join_verify_sweep, interval=60, first=90)
        app.job_queue.run_repeating(observe_check_sweep, interval=600, first=180)
        app.job_queue.run_repeating(announce_sweep, interval=60, first=30)      # 定时群公告（每分钟对表，一天一次）
        app.job_queue.run_repeating(lurker_sweep, interval=6 * 3600, first=600)  # 潜水号清理（每 6 小时）
        logger.info("群管巡检任务已注册：入群验证超时(60s) / 观察期到期(10min) / 定时公告(60s) / 潜水清理(6h)")
    else:
        logger.warning("JobQueue 不可用，自动备份未启用（需安装 python-telegram-bot[job-queue]）")

    # 关键：handler 分组（PTB 语义「每个 group 内最多只有一个 handler 被调用，先匹配者 break，
    # 但不同 group 之间都会执行」）。此前全部注册在 group 0，而 on_media 的
    # ~TEXT & ~COMMAND 会先匹配「入群服务消息」并 break，导致注册在其后的 on_new_members_msg
    # 永远不触发 → 普通群入群验证/硬门槛/防突袭/观察期起点全部静默失效（无报错、无日志，
    # 用户只能看到「开关开了没用」）。
    # 现在：group 0=文本命令与按钮，group 1=媒体类自动删除，group 2=成员/入群事件。
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^/'), route_command))
    # 榜单翻页按钮：必须注册在 on_button 之前（同 group 内先匹配者 break），否则 rk_* 会被 on_button 当未知操作
    app.add_handler(CallbackQueryHandler(on_rank_page, pattern=r"^rk_"))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r'^/'), on_text))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, on_media), group=1)  # 自动删除规则中心：媒体类
    # 关键：chat_member_types 必须显式传 ANY_CHAT_MEMBER（默认 -1=MY_CHAT_MEMBER 只听 bot 自身状态变化，
    # 普通新成员入群/退群触发的 chat_member 更新会被静默丢弃，调试里"最近事件"无埋点）
    app.add_handler(ChatMemberHandler(on_member_event, chat_member_types=ChatMemberHandler.ANY_CHAT_MEMBER), group=2)
    # 谁把 bot 拉进群：MY_CHAT_MEMBER 只听 bot 自身成员状态变化（普通群也触发，操作者在 from_user）；
    # 单独放 group=3，避免与上面的 ANY_CHAT_MEMBER 在同一 group 内被先匹配 break 掉（用户 2026-09-11 需求）
    app.add_handler(ChatMemberHandler(on_my_chat_member, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER), group=3)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members_msg), group=2)  # 普通群入群兜底
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left_member_msg), group=2)  # 退群提示清理
    app.add_handler(ChatJoinRequestHandler(on_join_request), group=2)  # 入群申请事件（群需开「申请加入」）
    app.add_error_handler(on_app_error)  # 全局错误兜底：handler 异常不再静默
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__": main()
