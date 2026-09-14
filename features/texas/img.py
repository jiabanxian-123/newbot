# -*- coding: utf-8 -*-
"""features.texas.img —— 德州扑克新界面渲染层（PIL **纯函数**）。

2026-09-14 用户拍板「德州界面改新版」，本层负责出图五件套：
    card_face      ① 真牌图（红花色红、黑花色黑、T 显示 10）
    hand_popup     ② 弹窗样式私聊手牌图（深色遮罩+白窗+大牌+保密注记）
    table_img      ③ 椭圆毛毡桌面图（公牌槽+玩家盒；手牌一律画牌背防偷看）
    showdown_img   ④ 摊牌+结算摘要图（赢家金框/弃牌灰底/净盈亏红灰条）
    reveal_img     ⑤ 单赢可选亮牌图（赢家大牌+公牌，替代纯文本亮牌）

纪律（重要）：
    - **纯函数**：数据进 → PNG bytes 出；零网络、零全局状态、可安全 to_thread。
    - 顶层只 import 标准库 / PIL / hub；hub 取值全部在函数体内（抽缝约定）。
    - 字体 NotoSansCJKsc-Regular.otf（OFL 许可）随包捆绑在 assets/；缺失时本层
      直接抛错，由调用方回退到原文本渲染 —— 图片只是锦上添花，**永远不能挡牌局**。
    - 图内**禁用 emoji**（捆绑的 CJK 字体没有彩色 emoji 字形，画出来是方框）；
      状态符号用几何字符 ●（在局）/ ✕（弃牌）/ ▶（行动中）/ ◆（牌背花纹）。
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

from core import hub   # noqa: F401  抽缝守卫要求每个抽出模块都导入 hub

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_FONT_FILE = os.path.join(_ASSETS, "NotoSansCJKsc-Regular.otf")
_fonts = {}


def _font(size):
    """字体缓存：同尺寸只解析一次（OTF 解析不便宜，一次开局会画几十次字）。"""
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(_FONT_FILE, size)
    return _fonts[size]


# ── 调色板（浅色平面风，与网页后台 app.css 同族）─────────────────────────
RED = (178, 44, 44)        # ♥♦ / 赢
BLACK = (48, 47, 45)       # ♠♣ / 正文
INK = (58, 57, 54)         # 主文字
GREY = (128, 126, 120)     # 次要文字
GREY_BG = (226, 225, 220)  # 弃牌灰底 / 暗槽
BG = (242, 241, 237)       # 画面底色
WHITE = (255, 255, 255)
GOLD = (188, 148, 54)      # 赢家金框
GOLD_BG = (250, 240, 220)  # 赢家金底
MASK = (68, 68, 65)        # 弹窗深色遮罩（设计稿 #444441）
FELT = (53, 128, 98)       # 桌面毛毡绿
FELT_EDGE = (43, 106, 80)  # 毛毡内圈
WOOD = (125, 88, 58)       # 桌沿木色
LIVE = (63, 138, 96)       # ● 在局
ALLIN = (198, 124, 38)     # ● 全下
LOSE_GREY = (150, 148, 142)

PHASE_CN = {"preflop": "翻牌前", "flop": "翻牌圈", "turn": "转牌圈",
            "river": "河牌圈", "showdown": "摊牌", "waiting": "等待中"}

_CARD_W, _CARD_H = 110, 154   # 标准牌


def _suit_rank(card):
    """treys 牌 int → (花色字符, 点数字符串)。与 core/cards.card_str 同源逻辑
    （走 hub.Card 而非直接 import，测试补丁 m.Card = fake 可实时穿透）。"""
    raw = hub.Card.int_to_pretty_str(card).strip("[]").strip()
    return raw[-1], raw[:-1].replace("T", "10")


def _png(im):
    bio = io.BytesIO()
    im.save(bio, "PNG")
    return bio.getvalue()


def _rrect(d, box, r, **kw):
    d.rounded_rectangle(box, radius=r, **kw)


def _card(d, x, y, w, h, card=None, back=False, gold=False, dim=False):
    """万能牌绘制件：card=真牌图 / back=牌背 / 都不给=空槽。
    gold=赢家金框；dim=未发的公牌暗槽。"""
    r = max(8, w // 12)
    if dim:
        _rrect(d, (x, y, x + w, y + h), r, fill=(214, 222, 217), outline=(190, 200, 194), width=2)
        d.text((x + w / 2, y + h / 2), "·", font=_font(int(h * 0.3)), fill=(168, 180, 172), anchor="mm")
        return
    if back:
        _rrect(d, (x, y, x + w, y + h), r, fill=(74, 104, 148), outline=(52, 76, 114), width=3)
        _rrect(d, (x + 9, y + 9, x + w - 9, y + h - 9), max(6, r - 4),
               outline=(120, 148, 190), width=2)
        d.text((x + w / 2, y + h / 2), "◆", font=_font(int(w * 0.34)),
               fill=(126, 154, 196), anchor="mm")
        return
    # 真牌面
    _rrect(d, (x, y, x + w, y + h), r, fill=WHITE,
           outline=GOLD if gold else (205, 204, 199), width=4 if gold else 2)
    suit, rank = _suit_rank(card)
    color = RED if suit in ("♥", "♦") else BLACK
    pad = max(6, int(w * 0.09))
    rank_sz = max(13, int(h * 0.20))
    suit_sz = max(11, int(h * 0.155))
    step_r, step_s = int(rank_sz * 1.12), int(suit_sz * 1.12)
    # 左上角标：点数上、花色下（"la"=顶端对齐，两行拉开不重叠）
    d.text((x + pad, y + pad * 0.5), rank, font=_font(rank_sz), fill=color, anchor="la")
    d.text((x + pad, y + pad * 0.5 + step_r), suit, font=_font(suit_sz), fill=color, anchor="la")
    # 右下角标：自底向上排（花色上、点数下），整块收在牌内
    yb = y + h - pad * 0.5 - (step_r + step_s)
    d.text((x + w - pad, yb), suit, font=_font(suit_sz), fill=color, anchor="la")
    d.text((x + w - pad, yb + step_s), rank, font=_font(rank_sz), fill=color, anchor="la")
    d.text((x + w / 2, y + h / 2), suit, font=_font(int(h * 0.40)), fill=color, anchor="mm")


def card_face(card, w=_CARD_W, h=_CARD_H):
    """单张真牌图 → PNG bytes（通用出口，测试与外部复用）。"""
    im = Image.new("RGB", (w, h), BG)
    _card(ImageDraw.Draw(im), 0, 0, w, h, card=card)
    return _png(im)


def hand_popup(name, hand):
    """弹窗样式手牌图：深色遮罩 + 白窗 +「你的手牌」+ 两张大牌 + 保密注记。
    name 仅用于私聊场景备注（图面保持设计稿文案「你的手牌」）。"""
    W, H = 900, 1240
    im = Image.new("RGB", (W, H), MASK)
    d = ImageDraw.Draw(im)
    # 白窗
    _rrect(d, (80, 150, W - 80, H - 150), 28, fill=WHITE)
    # 标题 + 关闭符
    d.text((W / 2, 245), "你的手牌", font=_font(44), fill=INK, anchor="mm")
    d.text((W - 140, 235), "×", font=_font(46), fill=(190, 189, 184), anchor="mm")
    _rrect(d, (W / 2 - 40, 290, W / 2 + 40, 294), 2, fill=GOLD)
    # 两张大牌
    cw, ch, gap = 250, 350, 34
    x0 = (W - cw * 2 - gap) // 2
    y0 = 380
    for i, card in enumerate(hand):
        _card(d, x0 + i * (cw + gap), y0, cw, ch, card=card, gold=False)
    # 保密注记（图内禁 emoji —— 方框 tofu 翻过车）
    d.text((W / 2, y0 + ch + 130), "只有你能看 · 群消息里你是两张牌背",
           font=_font(30), fill=GREY, anchor="mm")
    d.text((W / 2, y0 + ch + 185), "点群里的「手牌」按钮可随时再看",
           font=_font(26), fill=(170, 169, 163), anchor="mm")
    return _png(im)


def _badges(uid, game):
    """玩家状态符号与颜色（图内不用 emoji：● 在局/全下，弃牌用汉字——
    ✕ 等符号在 CJK 字体里是 tofu 方框，实测翻车过一次）。"""
    if uid in game.folded: return "弃", LOSE_GREY
    if uid in game.all_in: return "●", ALLIN
    return "●", LIVE


def table_img(game, names):
    """桌面图：标题行（赛段/奖池）+ 椭圆毛毡 + 公牌槽 + 底部玩家盒。
    手牌**一律画牌背** —— 群图人人可见，真牌只走私聊。"""
    W = 1280
    n = len(game.players)
    cols = n if n <= 5 else (n + 1) // 2
    rows = (n + cols - 1) // cols
    box_h, box_gap = 104, 16
    H = 620 + rows * (box_h + box_gap) + 8
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # 标题行
    prefix = "赛季德州" if game.season else "德州扑克"
    d.text((36, 34), f"{prefix}｜{PHASE_CN.get(game.phase, game.phase)}",
           font=_font(36), fill=INK, anchor="lm")
    d.text((W - 36, 34), f"奖池 {game.pot}｜下注 {game.current_bet}",
           font=_font(30), fill=(120, 88, 40), anchor="rm")
    # 椭圆毛毡桌
    d.ellipse((70, 120, W - 70, 580), fill=WOOD)
    d.ellipse((92, 138, W - 92, 562), fill=FELT)
    d.ellipse((118, 160, W - 118, 540), outline=FELT_EDGE, width=3)
    # 公牌槽（未发光暗槽）
    cw, ch, gap = 120, 168, 14
    board = list(game.board)[:5]
    x0 = W / 2 - (cw * 5 + gap * 4) / 2
    y0 = 340 - ch / 2
    for i in range(5):
        card = board[i] if i < len(board) else None
        _card(d, x0 + i * (cw + gap), y0, cw, ch, card=card, dim=card is None)
    d.text((W / 2, y0 + ch + 42), f"公共牌  {len(board)}/5",
           font=_font(24), fill=(196, 224, 208), anchor="mm")
    # 玩家盒（手牌画牌背）
    box_w = min(300, (W - 72 - (cols - 1) * box_gap) / cols)
    y = 620
    for idx, uid in enumerate(game.players):
        row, col = divmod(idx, cols)
        bx = 36 + col * (box_w + box_gap)
        by = y + row * (box_h + box_gap)
        acting = (uid == game.current())
        gold = acting
        fill = GOLD_BG if gold else (WHITE if uid not in game.folded else GREY_BG)
        _rrect(d, (bx, by, bx + box_w, by + box_h), 16, fill=fill,
               outline=GOLD if gold else (216, 215, 210), width=4 if gold else 2)
        badge, bcolor = _badges(uid, game)
        d.text((bx + 20, by + 24), badge, font=_font(26), fill=bcolor, anchor="lm")
        d.text((bx + 56, by + 24), f"{idx + 1}. {names.get(uid, uid)}",
               font=_font(27), fill=INK if uid not in game.folded else LOSE_GREY,
               anchor="lm")
        if acting:
            d.text((bx + box_w - 18, by + 24), "▶", font=_font(22), fill=GOLD, anchor="rm")
        d.text((bx + 20, by + 68), f"投 {game.total_bet[uid]}｜余 {game.chips[uid]}",
               font=_font(23), fill=GREY, anchor="lm")
        for k in range(2):
            _card(d, bx + box_w - 96 + k * 47, by + 34, 42, 58, back=True)
    return _png(im)


def showdown_img(game, names, result, nets=None):
    """摊牌+结算摘要图：公牌 + 每位玩家一行（赢家金框+胜章，弃牌灰底牌背）
    + 右侧净盈亏（赢红/输灰）。result = showdown() 返回列表。"""
    W = 1280
    rmap = {row[0]: row for row in result}
    winners = {row[0] for row in result if row[2] > 0}
    rows_n = len(game.players)
    row_h, row_gap = 150, 14
    H = 250 + rows_n * (row_h + row_gap) + 40
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((36, 40), "摊牌", font=_font(40), fill=INK, anchor="lm")
    pot_txt = f"底池 {game.pot}"
    d.text((W - 36, 40), pot_txt, font=_font(32), fill=(120, 88, 40), anchor="rm")
    # 公牌
    cw, ch, gap = 110, 154, 12
    x0 = W / 2 - (cw * max(5, len(game.board)) + gap * (max(5, len(game.board)) - 1)) / 2
    y0 = 96
    board = list(game.board)
    for i in range(max(5, len(board))):
        card = board[i] if i < len(board) else None
        _card(d, x0 + i * (cw + gap), y0, cw, ch, card=card, dim=card is None)
    # 玩家行
    y = 250
    for idx, uid in enumerate(game.players):
        by = y + idx * (row_h + row_gap)
        win = uid in winners
        folded = uid in game.folded
        fill = GOLD_BG if win else (GREY_BG if folded else WHITE)
        _rrect(d, (36, by, W - 36, by + row_h), 16, fill=fill,
               outline=GOLD if win else (216, 215, 210), width=4 if win else 2)
        # 牌（存活=真牌+金框；弃牌=牌背）
        hand = game.hands.get(uid)
        for k in range(2):
            cx = 68 + k * (cw * 0.82 + 10)
            cy = by + (row_h - ch) / 2
            if folded or not hand:
                _card(d, cx, cy, int(cw * 0.82), int(ch * 0.82), back=True)
            else:
                _card(d, cx, cy, int(cw * 0.82), int(ch * 0.82),
                      card=hand[k], gold=win)
        tx = 68 + 2 * (cw * 0.82 + 10) + 24
        d.text((tx, by + 44), f"{idx + 1}. {names.get(uid, uid)}",
               font=_font(30), fill=INK if not folded else LOSE_GREY, anchor="lm")
        hand_name = rmap.get(uid, ("", ""))[1] if uid in rmap else ""
        d.text((tx, by + 98), hand_name or ("已弃牌" if folded else ""),
               font=_font(24), fill=GREY, anchor="lm")
        # 净盈亏
        net = (nets or {}).get(uid, 0)
        ncolor = RED if net > 0 else (LOSE_GREY if net < 0 else GREY)
        d.text((W - 56, by + row_h / 2), f"{net:+d}",
               font=_font(42), fill=ncolor, anchor="rm")
        if win:
            # 「胜」章盖在第二张牌右上角（贴纸感），不遮右下角的净盈亏大字
            card_w = int(cw * 0.82)
            wx = 68 + (card_w + 10) * 2 - 24
            ws = 52
            d.ellipse((wx, by + 4, wx + ws, by + 4 + ws), fill=RED)
            d.text((wx + ws / 2, by + 4 + ws / 2), "胜", font=_font(28), fill=WHITE, anchor="mm")
    return _png(im)


def reveal_img(name, hand, board):
    """单赢可选亮牌图：{name} 亮牌 + 两张大牌（金框）+ 公牌行。"""
    W = 1080
    H = 840   # 底注「收全部底池」曾贴边被裁，加高留足空间
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text((W / 2, 90), f"{name} 亮牌", font=_font(44), fill=INK, anchor="mm")
    cw, ch = 230, 322
    x0 = (W - cw * 2 - 30) // 2
    for k, card in enumerate(hand or []):
        _card(d, x0 + k * (cw + 30), 150, cw, ch, card=card, gold=True)
    d.text((W / 2, 150 + ch + 70), "公牌", font=_font(26), fill=GREY, anchor="mm")
    bw, bh, gap = 92, 129, 10
    bx0 = W / 2 - (bw * 5 + gap * 4) / 2
    by = 150 + ch + 108
    board = list(board or [])
    for i in range(5):
        card = board[i] if i < len(board) else None
        _card(d, bx0 + i * (bw + gap), by, bw, bh, card=card, dim=card is None)
    d.text((W / 2, by + bh + 54), "收全部底池", font=_font(28), fill=(120, 88, 40), anchor="mm")
    return _png(im)
