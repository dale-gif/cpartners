"""CRP animation engine — TRUE 25fps motion for all overlay/infographic styles.

Replaces the old static-PNG + ffmpeg-crossfade fake animation. Each renderer
emits a frame sequence and encodes an opaque cutaway clip (entrance baked in,
last frame frozen to fill the hold). The compositor overlays these clips onto
the base video.

Motion styles (Larry-approved):
  big-text     — word-by-word gaussian blur-in on black
  title        — vertical line draws in, text mask-wipes out from the line
  infographic  — header blur-in, then staggered card reveal (box + number +
                 title + description), Rule of 3
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1920, 1080
FPS = 25
BLACK = (0, 0, 0, 255)
GREY = (150, 150, 150)

# shared entrance tuning (approved on the Big Text proof)
ENTRANCE = 0.34
MAX_BLUR = 32
RISE = 20


def _ease(p: float) -> float:
    return 1 - (1 - p) ** 3


def _font(font_dir, name, size):
    p = Path(font_dir) / name
    if p.exists():
        return ImageFont.truetype(str(p), size)
    return ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)


def _blur_in_text(img, xy, text, font, t, start, fill=(255, 255, 255),
                  entrance=ENTRANCE, max_blur=MAX_BLUR, rise=RISE):
    """Composite `text` onto img with a blur-in based on age = t - start."""
    age = t - start
    if age < 0:
        return img
    e = _ease(min(age / entrance, 1.0))
    blur = (1 - e) * max_blur
    alpha = int(255 * min(e * 1.15, 1.0))
    y_off = int((1 - e) * rise)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((xy[0], xy[1] - y_off), text, font=font,
                               fill=(*fill, alpha))
    if blur > 0.5:
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(img, layer)


# ----------------------------------------------------------------------------
# BIG TEXT — word-by-word blur-in
# ----------------------------------------------------------------------------
BT_MAX_W, BT_MAX_SIZE, BT_MIN_SIZE, BT_LINE_GAP = 1500, 210, 70, 24
BT_BASE_DELAY, BT_STAGGER = 0.15, 0.28

# Cutaway content must stay ABOVE the caption band (a "danger zone" margin sits
# between content and captions). Content is vertically centered in this box.
SAFE_TOP, SAFE_BOTTOM = 70, 806
SAFE_CENTER = (SAFE_TOP + SAFE_BOTTOM) // 2
SAFE_H = SAFE_BOTTOM - SAFE_TOP
# Big-text is centered in the SAFE CONTENT AREA (the zone above the caption
# band), so the block reads as optically centered on-frame with balanced space
# above and below — matching the approved reference. Tall multi-line hooks are
# clamped so they never cross SAFE_BOTTOM.
BT_VCENTER = SAFE_CENTER


def _bt_layout(words, font, draw, max_w):
    lines, cur = [], []
    for w in words:
        if draw.textbbox((0, 0), " ".join(cur + [w]), font=font)[2] > max_w and cur:
            lines.append(cur); cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def _bt_boxes(full_text, font_dir):
    words = full_text.upper().split()
    d = ImageDraw.Draw(Image.new("RGBA", (W, H)))
    size = BT_MAX_SIZE
    # Shrink until the text fits BOTH the width AND the safe height (so tall
    # multi-line hooks never spill into the caption band).
    while size > BT_MIN_SIZE:
        font = _font(font_dir, "Inter-Black.ttf", size)
        lines = _bt_layout(words, font, d, BT_MAX_W)
        widest = max(d.textbbox((0, 0), " ".join(l), font=font)[2] for l in lines)
        asc, desc = font.getmetrics()
        block_h = (asc + desc) * len(lines) + BT_LINE_GAP * (len(lines) - 1)
        if widest <= BT_MAX_W and block_h <= SAFE_H:
            break
        size -= 6
    font = _font(font_dir, "Inter-Black.ttf", size)
    lines = _bt_layout(words, font, d, BT_MAX_W)
    # Center the ACTUAL INK of the caps, not the font metric box (which has big
    # ascender/descender padding). Otherwise short hooks look top-heavy with
    # dead space below. Probe cap ink extent for this font size:
    probe = d.textbbox((0, 0), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", font=font)
    top_pad = probe[1]                    # origin → top of ink
    cap_h = probe[3] - probe[1]           # visible cap height
    advance = cap_h + BT_LINE_GAP
    total_visual = cap_h * len(lines) + BT_LINE_GAP * (len(lines) - 1)
    ink_top = BT_VCENTER - total_visual // 2
    ink_top = max(SAFE_TOP, min(ink_top, SAFE_BOTTOM - total_visual))
    space_w = d.textbbox((0, 0), " ", font=font)[2]
    boxes = []
    for i, line in enumerate(lines):
        lw = d.textbbox((0, 0), " ".join(line), font=font)[2]
        x = (W - lw) // 2
        y = ink_top + i * advance - top_pad   # draw origin so ink lands centered
        for w in line:
            boxes.append((w, x, y))
            x += d.textbbox((0, 0), w, font=font)[2] + space_w
    return font, boxes


def render_big_text_frames(full_text, font_dir, out_dir, entrance_secs):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    font, boxes = _bt_boxes(full_text, font_dir)
    # Adaptive stagger: EVERY word must fully resolve (its start + ENTRANCE) a
    # hair before the final entrance frame — otherwise the last words freeze
    # mid-blur-in and read as faint/ghosted (the "missing main words" bug).
    # Keep the approved 0.28s gap when the hook is short enough to fit; compress
    # the gap only when there are too many words for the entrance window.
    n_words = len(boxes)
    latest_start = max(0.0, entrance_secs - ENTRANCE - 0.12)  # last word starts by here
    base = min(BT_BASE_DELAY, latest_start)
    if n_words > 1:
        room = max(0.0, latest_start - base)
        stagger = min(BT_STAGGER, room / (n_words - 1))
    else:
        stagger = BT_STAGGER
    n = int(entrance_secs * FPS)
    for f in range(n):
        t = f / FPS
        img = Image.new("RGBA", (W, H), BLACK)
        for i, (word, bx, by) in enumerate(boxes):
            img = _blur_in_text(img, (bx, by), word, font, t,
                                base + i * stagger)
        img.convert("RGB").save(out_dir / f"f_{f:04d}.png")
    return n


# ----------------------------------------------------------------------------
# TITLE — line-draw + mask-wipe
# ----------------------------------------------------------------------------
# Title sits on the LEFT only — text must never reach the avatar (frame-right).
# T_MAX_W keeps each line inside the left ~800px; text is balanced into 2 rows.
T_SIZE, T_LINE_X, T_TEXT_X, T_MAX_W, T_GAP, T_LINE_W = 112, 150, 190, 780, 18, 4
T_LINE_DRAW, T_TEXT_START, T_TEXT_WIPE, T_SLIDE = 0.35, 0.28, 0.55, 26


def _t_layout(text, font_dir):
    d = ImageDraw.Draw(Image.new("RGBA", (W, H)))
    words = text.upper().split()
    # Always balance into TWO rows (unless it's a single word), so the title
    # reads as a compact block on the left rather than one long line.
    if len(words) <= 1:
        lines = [words[0]] if words else [""]
    else:
        best = None
        for k in range(1, len(words)):
            l1, l2 = " ".join(words[:k]), " ".join(words[k:])
            diff = abs(len(l1) - len(l2))
            if best is None or diff < best[0]:
                best = (diff, [l1, l2])
        lines = best[1]
    # Shrink font until every line fits inside the left safe zone (clear of avatar).
    size = T_SIZE
    font = _font(font_dir, "Inter-Black.ttf", size)
    while size > 56:
        if max(d.textbbox((0, 0), l, font=font)[2] for l in lines) <= T_MAX_W:
            break
        size -= 6
        font = _font(font_dir, "Inter-Black.ttf", size)
    asc, desc = font.getmetrics()
    line_h = asc + desc
    total_h = line_h * len(lines) + T_GAP * (len(lines) - 1)
    return font, lines, line_h, (H - total_h) // 2, total_h


def render_title_frames(text, font_dir, out_dir, entrance_secs, transparent=False):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    font, lines, line_h, y0, total_h = _t_layout(text, font_dir)
    n = int(entrance_secs * FPS)
    bg = (0, 0, 0, 0) if transparent else BLACK
    for f in range(n):
        t = f / FPS
        img = Image.new("RGBA", (W, H), bg)
        draw = ImageDraw.Draw(img)
        lp = _ease(min(t / T_LINE_DRAW, 1.0))
        if lp > 0:
            top = y0 - 18
            draw.rectangle([T_LINE_X, top, T_LINE_X + T_LINE_W,
                            top + int((total_h + 36) * lp)], fill=(255, 255, 255, 255))
        tp = _ease(min(max((t - T_TEXT_START) / T_TEXT_WIPE, 0.0), 1.0))
        if tp > 0:
            tl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            td = ImageDraw.Draw(tl)
            x_off = int((1 - tp) * T_SLIDE)
            y = y0
            for ln in lines:
                td.text((T_TEXT_X - x_off, y), ln, font=font, fill=(255, 255, 255, 255))
                y += line_h + T_GAP
            mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mask).rectangle(
                [0, 0, T_TEXT_X + int((W - T_TEXT_X) * tp) + 60, H], fill=255)
            tl.putalpha(Image.composite(tl.getchannel("A"),
                                        Image.new("L", (W, H), 0), mask))
            img = Image.alpha_composite(img, tl)
        if transparent:
            img.save(out_dir / f"f_{f:04d}.png")           # keep alpha
        else:
            img.convert("RGB").save(out_dir / f"f_{f:04d}.png")
    return n


# ----------------------------------------------------------------------------
# INFOGRAPHIC — 7 templates, each a staggered reveal on black.
#   header blur-in -> subtitle fade -> 3 items revealed in sequence.
# All templates share timing: item i enters at IG_ITEM0 + i*IG_STAGGER.
# ----------------------------------------------------------------------------
IG_HX, IG_HY = 90, 60
IG_HEADER_AT, IG_SUB_AT, IG_ITEM0, IG_STAGGER = 0.10, 0.34, 0.68, 0.55


def _lerp_alpha(t, start, dur=ENTRANCE):
    age = t - start
    if age < 0:
        return 0.0
    return _ease(min(age / dur, 1.0))


def _fade_layer(img, draw_fn, alpha):
    if alpha <= 0:
        return img
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(lay), int(255 * min(alpha, 1.0)))
    return Image.alpha_composite(img, lay)


def _wrap(draw, text, font, max_w):
    words, lines, cur = (text or "").split(), [], []
    for w in words:
        if draw.textbbox((0, 0), " ".join(cur + [w]), font=font)[2] > max_w and cur:
            lines.append(" ".join(cur)); cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    return lines


def _desc_block(img, x, y, text, font, t, start, max_w, center=False, line_h=34):
    a = _lerp_alpha(t, start)
    if a <= 0:
        return img
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for ln in _wrap(d, text, font, max_w):
        lx = x - d.textbbox((0, 0), ln, font=font)[2] // 2 if center else x
        d.text((lx, y), ln, font=font, fill=(*GREY, int(255 * a)))
        y += line_h
    return Image.alpha_composite(img, lay)


def _header(img, header, subtitle, fonts, t):
    img = _blur_in_text(img, (IG_HX, IG_HY), header.upper(), fonts["h"], t, IG_HEADER_AT)
    a = _lerp_alpha(t, IG_SUB_AT)
    if a > 0:
        img = _fade_layer(img, lambda d, al: d.text(
            (IG_HX, IG_HY + 78), subtitle, font=fonts["sub"], fill=(*GREY, al)), a)
    return img


def _num(i):
    return f"0{i+1}"


def _tw(font, text):
    return ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)[2]


def _blur_center(img, cx, y, text, font, t, start, fill=(255, 255, 255)):
    return _blur_in_text(img, (cx - _tw(font, text) // 2, y), text, font, t, start, fill)


# Icon names the planner can choose from (context-driven line-art glyphs).
ICON_NAMES = [
    "document", "search", "warning", "check", "shield", "scales", "person",
    "people", "phone", "clock", "money", "lock", "flag", "mail", "chart",
    "edit", "folder", "target", "calendar", "star", "building",
]


def _draw_icon(d, name, cx, cy, s, col, w=3):
    """Draw a simple white line-art icon centered at (cx, cy), extent ~s."""
    name = (name or "document").lower().strip()

    def L(x1, y1, x2, y2):
        d.line([(cx + x1 * s, cy + y1 * s), (cx + x2 * s, cy + y2 * s)], fill=col, width=w)

    def C(x, y, r, fill_=None):
        d.ellipse([cx + (x - r) * s, cy + (y - r) * s, cx + (x + r) * s, cy + (y + r) * s],
                  outline=col, width=w, fill=fill_)

    def P(pts, closed=True):
        pp = [(cx + px * s, cy + py * s) for px, py in pts]
        if closed:
            pp.append(pp[0])
        d.line(pp, fill=col, width=w, joint="curve")

    def A(x, y, r, a0, a1):
        d.arc([cx + (x - r) * s, cy + (y - r) * s, cx + (x + r) * s, cy + (y + r) * s],
              a0, a1, fill=col, width=w)

    def RR(x0, y0, x1, y1, r=0.15):
        d.rounded_rectangle([cx + x0 * s, cy + y0 * s, cx + x1 * s, cy + y1 * s],
                            radius=int(r * s), outline=col, width=w)

    if name in ("document", "report", "record", "paperwork", "contract", "file"):
        P([(-0.6, -0.9), (0.35, -0.9), (0.6, -0.6), (0.6, 0.9), (-0.6, 0.9)])
        L(0.35, -0.9, 0.35, -0.6); L(0.35, -0.6, 0.6, -0.6)
        L(-0.35, -0.15, 0.35, -0.15); L(-0.35, 0.15, 0.35, 0.15); L(-0.35, 0.45, 0.15, 0.45)
    elif name in ("search", "investigate", "find", "magnifier"):
        C(-0.15, -0.15, 0.55); L(0.28, 0.28, 0.75, 0.75)
    elif name in ("warning", "risk", "danger", "alert"):
        P([(0, -0.9), (0.92, 0.7), (-0.92, 0.7)]); L(0, -0.28, 0, 0.25); C(0, 0.5, 0.07, col)
    elif name in ("check", "verify", "done", "approve", "success"):
        C(0, 0, 0.85); L(-0.36, 0.02, -0.08, 0.34); L(-0.08, 0.34, 0.42, -0.3)
    elif name in ("shield", "protect", "defend", "secure"):
        P([(0, -0.92), (0.78, -0.55), (0.78, 0.15), (0, 0.92), (-0.78, 0.15), (-0.78, -0.55)])
        L(-0.32, 0.0, -0.06, 0.3); L(-0.06, 0.3, 0.36, -0.28)
    elif name in ("scales", "legal", "court", "liability", "law", "justice"):
        L(0, -0.85, 0, 0.72); L(-0.5, 0.72, 0.5, 0.72); L(-0.82, -0.62, 0.82, -0.62)
        L(0, -0.85, 0, -0.62)
        L(-0.82, -0.62, -0.82, -0.32); A(-0.82, -0.32, 0.3, 0, 180)
        L(0.82, -0.62, 0.82, -0.32); A(0.82, -0.32, 0.3, 0, 180)
    elif name in ("person", "director", "individual", "user"):
        C(0, -0.45, 0.33); A(0, 0.95, 0.62, 180, 360)
    elif name in ("people", "team", "parties", "group"):
        # overlapping two-figure (back-right smaller, front-left larger) so it
        # reads clearly as people and not a symmetrical "face"
        C(0.34, -0.36, 0.2); A(0.34, 0.72, 0.36, 200, 340)
        C(-0.2, -0.12, 0.27); A(-0.2, 1.02, 0.5, 180, 360)
    elif name in ("phone", "contact", "call"):
        RR(-0.5, -0.85, 0.5, 0.85, 0.18); L(-0.16, 0.62, 0.16, 0.62)
    elif name in ("clock", "time", "deadline", "urgent"):
        C(0, 0, 0.85); L(0, 0, 0, -0.5); L(0, 0, 0.4, 0.16)
    elif name in ("money", "payment", "debt", "cost", "cash", "dollar"):
        C(0, 0, 0.85); L(0, -0.62, 0, 0.62); A(0, -0.28, 0.3, 60, 300); A(0, 0.28, 0.3, -120, 120)
    elif name in ("lock", "protection", "safe"):
        RR(-0.58, -0.05, 0.58, 0.8, 0.1); A(0, -0.05, 0.36, 180, 360)
        L(-0.36, -0.05, -0.36, -0.22); L(0.36, -0.05, 0.36, -0.22)
        C(0, 0.28, 0.08, col); L(0, 0.32, 0, 0.55)
    elif name in ("flag", "mark", "milestone"):
        L(-0.5, -0.9, -0.5, 0.9); P([(-0.5, -0.9), (0.62, -0.58), (-0.5, -0.26)])
    elif name in ("mail", "letter", "notice", "envelope", "message"):
        RR(-0.85, -0.6, 0.85, 0.6, 0.06); L(-0.85, -0.5, 0, 0.1); L(0, 0.1, 0.85, -0.5)
    elif name in ("chart", "growth", "results", "data", "graph"):
        L(-0.72, -0.8, -0.72, 0.72); L(-0.72, 0.72, 0.8, 0.72)
        RR(-0.55, 0.2, -0.28, 0.72, 0.0); RR(-0.12, -0.15, 0.15, 0.72, 0.0); RR(0.32, -0.55, 0.6, 0.72, 0.0)
    elif name in ("edit", "sign", "pencil", "write"):
        L(-0.62, 0.62, 0.42, -0.42); L(-0.42, 0.78, 0.58, -0.26)
        L(-0.62, 0.62, -0.42, 0.78); L(0.42, -0.42, 0.72, -0.58); L(0.58, -0.26, 0.72, -0.58)
    elif name in ("folder", "organize", "case"):
        P([(-0.8, -0.45), (-0.15, -0.45), (0.02, -0.2), (0.8, -0.2), (0.8, 0.6), (-0.8, 0.6)])
    elif name in ("target", "goal", "focus", "aim"):
        C(0, 0, 0.9); C(0, 0, 0.5); C(0, 0, 0.1, col)
    elif name in ("calendar", "schedule", "date", "book"):
        RR(-0.75, -0.62, 0.75, 0.72, 0.08); L(-0.75, -0.28, 0.75, -0.28)
        L(-0.4, -0.85, -0.4, -0.45); L(0.4, -0.85, 0.4, -0.45)
        for gx in (-0.4, 0.0, 0.4):
            for gy in (0.05, 0.42):
                C(gx, gy, 0.07, col)
    elif name in ("star", "best", "quality", "premium"):
        pts = []
        for k in range(5):
            ao = math.radians(-90 + 72 * k)
            pts.append((0.95 * math.cos(ao), 0.95 * math.sin(ao)))
            ai = math.radians(-90 + 72 * k + 36)
            pts.append((0.42 * math.cos(ai), 0.42 * math.sin(ai)))
        P(pts)
    elif name in ("building", "company", "business", "office"):
        RR(-0.6, -0.85, 0.6, 0.8, 0.0)
        for gx in (-0.3, 0.05, 0.4):
            for gy in (-0.55, -0.2, 0.15):
                d.rectangle([cx + (gx - 0.09) * s, cy + (gy - 0.09) * s,
                             cx + (gx + 0.09) * s, cy + (gy + 0.09) * s], outline=col, width=w)
        d.rectangle([cx - 0.16 * s, cy + 0.45 * s, cx + 0.16 * s, cy + 0.8 * s], outline=col, width=w)
    else:  # default → document
        _draw_icon(d, "document", cx, cy, s, col, w)


# Content fills the caption-safe area (top ~880px); captions ride below.
# Elements are sized/spread to match the Larry-approved templates.

# --- template: three-cards ---------------------------------------------------
def _tpl_three_cards(img, t, cards, fonts):
    CW, CH, GAP, CY = 500, 580, 50, 190
    x0 = (W - (3 * CW + 2 * GAP)) // 2
    for i, card in enumerate(cards):
        s = IG_ITEM0 + i * IG_STAGGER
        cx = x0 + i * (CW + GAP)
        ctr = cx + CW // 2
        be = _lerp_alpha(t, s)
        if be <= 0:
            continue
        sc = 0.97 + 0.03 * be
        cw, ch = int(CW * sc), int(CH * sc)
        ox, oy = cx + (CW - cw) // 2, CY + (CH - ch) // 2
        img = _fade_layer(img, lambda d, al, b=[ox, oy, cw, ch]: d.rounded_rectangle(
            [b[0], b[1], b[0] + b[2], b[1] + b[3]], radius=10, outline=(255, 255, 255, al), width=3), be)
        img = _blur_center(img, ctr, CY + 70, _num(i), fonts["num"], t, s + 0.10)
        ue = _lerp_alpha(t, s + 0.20, 0.3)
        if ue > 0:
            img = _fade_layer(img, lambda d, al, c=ctr, w=int(70 * ue): d.rectangle(
                [c - w // 2, CY + 215, c + w // 2, CY + 221], fill=(255, 255, 255, 255)), 1.0)
        img = _blur_center(img, ctr, CY + 285, (card.get("title") or "").upper(),
                           fonts["title"], t, s + 0.26)
        img = _desc_block(img, ctr, CY + 385, card.get("description"), fonts["desc"],
                          t, s + 0.40, CW - 90, center=True, line_h=38)
    return img


# --- template: three-columns -------------------------------------------------
def _tpl_three_columns(img, t, cards, fonts):
    COLW, GAP = 380, 80
    x0 = (W - (3 * COLW + 2 * GAP)) // 2
    ICON_Y, NUM_Y, UND_Y, TITLE_Y, DESC_Y = 230, 400, 530, 578, 660
    for i, card in enumerate(cards):
        s = IG_ITEM0 + i * IG_STAGGER
        a = _lerp_alpha(t, s)
        if a <= 0:
            continue
        cx = x0 + i * (COLW + GAP)
        mid = cx + COLW // 2
        if i > 0:
            he = _lerp_alpha(t, s, 0.3)
            img = _fade_layer(img, lambda d, al, x=cx - GAP // 2, h=int(620 * he): d.line(
                [(x, 210), (x, 210 + h)], fill=(120, 120, 120, 255), width=2), 1.0)
        img = _fade_layer(img, lambda d, al, m=mid: d.ellipse(
            [m - 60, ICON_Y, m + 60, ICON_Y + 120], outline=(255, 255, 255, al), width=3), a)
        # context icon chosen by the planner (falls back to a document glyph)
        ia = _lerp_alpha(t, s + 0.06)
        if ia > 0:
            img = _fade_layer(img, lambda d, al, m=mid, ic=card.get("icon"): _draw_icon(
                d, ic, m, ICON_Y + 60, 34, (255, 255, 255, al), w=3), ia)
        img = _blur_center(img, mid, NUM_Y, _num(i), fonts["num"], t, s + 0.12)
        ue = _lerp_alpha(t, s + 0.22, 0.3)
        if ue > 0:
            img = _fade_layer(img, lambda d, al, m=mid, w=int(60 * ue): d.line(
                [(m - w // 2, UND_Y), (m + w // 2, UND_Y)], fill=(255, 255, 255, 255), width=4), 1.0)
        img = _blur_center(img, mid, TITLE_Y, (card.get("title") or "").upper(),
                           fonts["title"], t, s + 0.28)
        img = _desc_block(img, mid, DESC_Y, card.get("description"), fonts["desc"],
                          t, s + 0.42, COLW - 30, center=True, line_h=38)
    return img


# --- template: timeline ------------------------------------------------------
def _tpl_timeline(img, t, cards, fonts):
    CY, R = 430, 82
    xs = [W // 2 - 540, W // 2, W // 2 + 540]
    for i, card in enumerate(cards):
        s = IG_ITEM0 + i * IG_STAGGER
        a = _lerp_alpha(t, s)
        if a <= 0:
            continue
        cx = xs[i]
        if i > 0:
            le = _lerp_alpha(t, s, 0.3)
            x1, x2 = xs[i - 1] + R, cx - R
            img = _fade_layer(img, lambda d, al, a1=x1, a2=x1 + int((x2 - x1) * le): d.line(
                [(a1, CY), (a2, CY)], fill=(255, 255, 255, 255), width=3), 1.0)
        img = _fade_layer(img, lambda d, al, x=cx: d.ellipse(
            [x - R, CY - R, x + R, CY + R], outline=(255, 255, 255, al), width=3), a)
        img = _blur_center(img, cx, CY - 38, _num(i), fonts["num_s"], t, s + 0.10)
        img = _blur_center(img, cx, CY + R + 55, (card.get("title") or "").upper(),
                           fonts["title"], t, s + 0.24)
        img = _desc_block(img, cx, CY + R + 135, card.get("description"), fonts["desc"],
                          t, s + 0.40, 380, center=True, line_h=38)
    return img


# --- template: numbered-list -------------------------------------------------
def _tpl_numbered_list(img, t, cards, fonts):
    x0, y0, rowh = 150, 205, 195
    for i, card in enumerate(cards):
        s = IG_ITEM0 + i * IG_STAGGER
        a = _lerp_alpha(t, s)
        if a <= 0:
            continue
        ry = y0 + i * rowh
        img = _blur_in_text(img, (x0, ry), _num(i), fonts["num"], t, s + 0.08)
        img = _fade_layer(img, lambda d, al, y=ry: d.line(
            [(x0 + 175, y + 12), (x0 + 175, y + 100)], fill=(255, 255, 255, al), width=4), a)
        img = _blur_in_text(img, (x0 + 215, ry + 6), (card.get("title") or "").upper(),
                            fonts["title"], t, s + 0.20)
        img = _desc_block(img, x0 + 215, ry + 72, card.get("description"), fonts["desc"],
                          t, s + 0.34, 1480, line_h=38)
        se = _lerp_alpha(t, s + 0.30, 0.4)
        if se > 0:
            img = _fade_layer(img, lambda d, al, y=ry + 170, w=int((W - 2 * x0) * se): d.line(
                [(x0, y), (x0 + w, y)], fill=(90, 90, 90, 255), width=2), 1.0)
    return img


# --- template: circle-diagram ------------------------------------------------
def _tpl_circle_diagram(img, t, cards, fonts):
    import math
    cxr, cyr, R, inner = 540, 490, 270, 56
    # segment number positions (screen deg, y-down): 01 upper-left, 02 upper-right, 03 bottom
    label_ang = [210, 330, 90]
    spoke_ang = [270, 150, 30]  # dividers between the three segments
    ring_a = _lerp_alpha(t, IG_ITEM0)
    if ring_a > 0:
        img = _fade_layer(img, lambda d, al: d.ellipse(
            [cxr - R, cyr - R, cxr + R, cyr + R], outline=(255, 255, 255, al), width=3), ring_a)
        img = _fade_layer(img, lambda d, al: d.ellipse(
            [cxr - inner, cyr - inner, cxr + inner, cyr + inner], outline=(255, 255, 255, al), width=3), ring_a)
    lx = 1120
    ly0, lrow = 300, 165
    for i, card in enumerate(cards):
        s = IG_ITEM0 + i * IG_STAGGER
        a = _lerp_alpha(t, s)
        if a <= 0:
            continue
        sa = math.radians(spoke_ang[i])
        x1, y1 = cxr + inner * math.cos(sa), cyr + inner * math.sin(sa)
        x2, y2 = cxr + R * math.cos(sa), cyr + R * math.sin(sa)
        img = _fade_layer(img, lambda d, al, p=(x1, y1, x2, y2): d.line(
            [(p[0], p[1]), (p[2], p[3])], fill=(255, 255, 255, al), width=3), a)
        na = math.radians(label_ang[i])
        nx, ny = cxr + (R * 0.63) * math.cos(na), cyr + (R * 0.63) * math.sin(na)
        img = _blur_center(img, int(nx), int(ny) - 34, _num(i), fonts["num_s"], t, s + 0.10)
        ly = ly0 + i * lrow
        img = _blur_in_text(img, (lx, ly), _num(i), fonts["num"], t, s + 0.14)
        img = _fade_layer(img, lambda d, al, y=ly: d.line(
            [(lx + 150, y + 10), (lx + 150, y + 90)], fill=(255, 255, 255, al), width=4), a)
        img = _blur_in_text(img, (lx + 185, ly + 6), (card.get("title") or "").upper(),
                            fonts["title_s"], t, s + 0.24)
        img = _desc_block(img, lx + 185, ly + 60, card.get("description"), fonts["desc"],
                          t, s + 0.38, 600, line_h=36)
        se = _lerp_alpha(t, s + 0.32, 0.4)
        if se > 0:
            img = _fade_layer(img, lambda d, al, y=ly + 140, w=int(640 * se): d.line(
                [(lx, y), (lx + w, y)], fill=(90, 90, 90, 255), width=2), 1.0)
    return img


# --- template: problem-solution ----------------------------------------------
def _tpl_problem_solution(img, t, cards, fonts):
    BOXW, BOXH, GAP, BY = 400, 360, 70, 200
    x0 = (W - (3 * BOXW + 2 * GAP)) // 2
    labels = ["PROBLEM", "CAUSE", "SOLUTION"]
    for i, card in enumerate(cards):
        s = IG_ITEM0 + i * IG_STAGGER
        a = _lerp_alpha(t, s)
        if a <= 0:
            continue
        bx = x0 + i * (BOXW + GAP)
        cx, cy = bx + BOXW // 2, BY + BOXH // 2
        img = _fade_layer(img, lambda d, al, b=bx: d.rectangle(
            [b, BY, b + BOXW, BY + BOXH], outline=(255, 255, 255, al), width=3), a)
        ia = _lerp_alpha(t, s + 0.10)

        def _icon(d, al, idx=i, ccx=cx, ccy=cy):
            col = (255, 255, 255, al)
            if idx == 0:  # warning triangle
                d.polygon([(ccx, ccy - 62), (ccx - 68, ccy + 54), (ccx + 68, ccy + 54)], outline=col, width=4)
                d.line([(ccx, ccy - 16), (ccx, ccy + 24)], fill=col, width=4)
                d.ellipse([ccx - 4, ccy + 34, ccx + 4, ccy + 42], fill=col)
            elif idx == 1:  # magnifier
                d.ellipse([ccx - 54, ccy - 54, ccx + 14, ccy + 14], outline=col, width=4)
                d.line([(ccx + 10, ccy + 10), (ccx + 54, ccy + 54)], fill=col, width=5)
            else:  # badge check
                d.ellipse([ccx - 56, ccy - 56, ccx + 56, ccy + 56], outline=col, width=4)
                d.line([(ccx - 24, ccy), (ccx - 6, ccy + 22)], fill=col, width=5)
                d.line([(ccx - 6, ccy + 22), (ccx + 30, ccy - 22)], fill=col, width=5)
        if ia > 0:
            img = _fade_layer(img, _icon, ia)
        img = _blur_center(img, cx, BY + BOXH + 40, labels[i], fonts["title"], t, s + 0.24)
        img = _desc_block(img, cx, BY + BOXH + 115, card.get("description"), fonts["desc"],
                          t, s + 0.40, BOXW, center=True, line_h=38)
    return img


# --- template: checklist -----------------------------------------------------
def _tpl_checklist(img, t, cards, fonts):
    x0, y0, rowh, box = 150, 205, 195, 120
    for i, card in enumerate(cards):
        s = IG_ITEM0 + i * IG_STAGGER
        a = _lerp_alpha(t, s)
        if a <= 0:
            continue
        ry = y0 + i * rowh
        img = _fade_layer(img, lambda d, al, y=ry: d.rectangle(
            [x0, y, x0 + box, y + box], outline=(255, 255, 255, al), width=3), a)
        pe = _lerp_alpha(t, s + 0.10)
        if pe > 0:
            cx, cy = x0 + box // 2, ry + box // 2
            img = _fade_layer(img, lambda d, al, X=cx, Y=cy: (
                d.line([(X - 32, Y), (X + 32, Y)], fill=(255, 255, 255, al), width=6),
                d.line([(X, Y - 32), (X, Y + 32)], fill=(255, 255, 255, al), width=6)), pe)
        img = _blur_in_text(img, (x0 + box + 45, ry + 8), (card.get("title") or "").upper(),
                            fonts["big"], t, s + 0.22)
        img = _desc_block(img, x0 + box + 45, ry + 88, card.get("description"), fonts["desc"],
                          t, s + 0.36, 1480, line_h=38)
        se = _lerp_alpha(t, s + 0.30, 0.4)
        if se > 0:
            img = _fade_layer(img, lambda d, al, y=ry + box + 42, w=int((W - 2 * x0) * se): d.line(
                [(x0, y), (x0 + w, y)], fill=(90, 90, 90, 255), width=2), 1.0)
    return img


_TEMPLATES = {
    "three-cards": _tpl_three_cards,
    "three-columns": _tpl_three_columns,
    "timeline": _tpl_timeline,
    "numbered-list": _tpl_numbered_list,
    "circle-diagram": _tpl_circle_diagram,
    "problem-solution": _tpl_problem_solution,
    "checklist": _tpl_checklist,
}


def render_infographic_frames(header, subtitle, cards, font_dir, out_dir,
                              entrance_secs, template="three-cards"):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    fonts = {
        "h": _font(font_dir, "Inter-ExtraBold.ttf", 54),
        "sub": _font(font_dir, "Inter-Regular.ttf", 27),
        "num": _font(font_dir, "Inter-Black.ttf", 100),
        "num_s": _font(font_dir, "Inter-Black.ttf", 66),
        "title": _font(font_dir, "Inter-ExtraBold.ttf", 46),
        "title_s": _font(font_dir, "Inter-ExtraBold.ttf", 40),
        "big": _font(font_dir, "Inter-Black.ttf", 66),
        "ic": _font(font_dir, "Inter-Regular.ttf", 27),
        "desc": _font(font_dir, "Inter-Regular.ttf", 27),
    }
    cards = (list(cards) + [{"title": "", "description": ""}] * 3)[:3]
    draw_tpl = _TEMPLATES.get(template, _tpl_three_cards)
    n = int(entrance_secs * FPS)
    for f in range(n):
        t = f / FPS
        img = Image.new("RGBA", (W, H), BLACK)
        img = _header(img, header, subtitle, fonts, t)
        img = draw_tpl(img, t, cards, fonts)
        img.convert("RGB").save(out_dir / f"f_{f:04d}.png")
    return n


# ----------------------------------------------------------------------------
# ENCODE — frames + freeze-to-hold → opaque cutaway MP4
# ----------------------------------------------------------------------------
def encode_clip(frames_dir, out_path, entrance_secs, hold_secs, fps=FPS):
    """Encode frame sequence to an opaque MP4; clone the last frame so the
    clip lasts `hold_secs` total (entrance animation + freeze)."""
    frames_dir = Path(frames_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    freeze = max(0.0, hold_secs - entrance_secs)
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps), "-i", str(frames_dir / "f_%04d.png"),
         "-vf", f"tpad=stop_mode=clone:stop_duration={freeze:.3f}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
         str(out_path)],
        check=True, capture_output=True)
    return out_path


def encode_clip_alpha(frames_dir, out_path, entrance_secs, hold_secs, fps=FPS):
    """Encode an RGBA frame sequence to a VP9 .webm that PRESERVES ALPHA, so
    the overlay can sit on top of the avatar video with the background showing
    through. Last frame is cloned to fill the hold."""
    frames_dir = Path(frames_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    freeze = max(0.0, hold_secs - entrance_secs)
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps), "-i", str(frames_dir / "f_%04d.png"),
         "-vf", f"tpad=stop_mode=clone:stop_duration={freeze:.3f}",
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "24",
         str(out_path)],
        check=True, capture_output=True)
    return out_path


# public high-level helpers ---------------------------------------------------
def render_overlay_clip(lines, style, font_dir, work_dir, clip_path,
                        entrance_secs, hold_secs):
    """Render an animated text-overlay cutaway clip. Returns clip_path.

    style: 'big-text' (opaque full-frame cutaway). 'title' and 'black-gradient'
    are TRANSPARENT side overlays — the avatar stays visible frame-right while
    the text animates in frame-left. 'black-gradient' is the recurring hook
    look (Larry-approved); 'title' is the section title card.
    """
    work_dir = Path(work_dir)
    clip_path = Path(clip_path)
    text = " ".join(l.strip() for l in lines if l and l.strip())
    if style in ("title", "black-gradient"):
        # Transparent side overlay — avatar stays visible behind it.
        render_title_frames(text, font_dir, work_dir, entrance_secs, transparent=True)
        webm = clip_path.with_suffix(".webm")
        return encode_clip_alpha(work_dir, webm, entrance_secs, hold_secs)
    # big-text: opaque full-frame cutaway
    render_big_text_frames(text, font_dir, work_dir, entrance_secs)
    return encode_clip(work_dir, clip_path, entrance_secs, hold_secs)


def render_infographic_clip(card, font_dir, work_dir, clip_path,
                            entrance_secs, hold_secs):
    """Render an animated infographic cutaway clip. Returns clip_path."""
    header = card.get("overall_title") or card.get("title") or ""
    subtitle = card.get("subtitle") or ""
    cards = card.get("cards") or []
    template = card.get("template") or "three-cards"
    render_infographic_frames(header, subtitle, cards, font_dir, work_dir,
                              entrance_secs, template=template)
    return encode_clip(work_dir, clip_path, entrance_secs, hold_secs)
