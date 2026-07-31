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
    while size > BT_MIN_SIZE:
        font = _font(font_dir, "Inter-Black.ttf", size)
        lines = _bt_layout(words, font, d, BT_MAX_W)
        widest = max(d.textbbox((0, 0), " ".join(l), font=font)[2] for l in lines)
        if widest <= BT_MAX_W:
            break
        size -= 6
    font = _font(font_dir, "Inter-Black.ttf", size)
    lines = _bt_layout(words, font, d, BT_MAX_W)
    asc, desc = font.getmetrics()
    line_h = asc + desc
    total_h = line_h * len(lines) + BT_LINE_GAP * (len(lines) - 1)
    y = (H - total_h) // 2
    space_w = d.textbbox((0, 0), " ", font=font)[2]
    boxes = []
    for line in lines:
        lw = d.textbbox((0, 0), " ".join(line), font=font)[2]
        x = (W - lw) // 2
        for w in line:
            boxes.append((w, x, y))
            x += d.textbbox((0, 0), w, font=font)[2] + space_w
        y += line_h + BT_LINE_GAP
    return font, boxes


def render_big_text_frames(full_text, font_dir, out_dir, entrance_secs):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    font, boxes = _bt_boxes(full_text, font_dir)
    n = int(entrance_secs * FPS)
    for f in range(n):
        t = f / FPS
        img = Image.new("RGBA", (W, H), BLACK)
        for i, (word, bx, by) in enumerate(boxes):
            img = _blur_in_text(img, (bx, by), word, font, t,
                                BT_BASE_DELAY + i * BT_STAGGER)
        img.convert("RGB").save(out_dir / f"f_{f:04d}.png")
    return n


# ----------------------------------------------------------------------------
# TITLE — line-draw + mask-wipe
# ----------------------------------------------------------------------------
T_SIZE, T_LINE_X, T_TEXT_X, T_MAX_W, T_GAP, T_LINE_W = 118, 150, 190, 1100, 16, 4
T_LINE_DRAW, T_TEXT_START, T_TEXT_WIPE, T_SLIDE = 0.35, 0.28, 0.55, 26


def _t_layout(text, font_dir):
    d = ImageDraw.Draw(Image.new("RGBA", (W, H)))
    font = _font(font_dir, "Inter-Black.ttf", T_SIZE)
    lines, cur = [], []
    for w in text.upper().split():
        if d.textbbox((0, 0), " ".join(cur + [w]), font=font)[2] > T_MAX_W and cur:
            lines.append(" ".join(cur)); cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    asc, desc = font.getmetrics()
    line_h = asc + desc
    total_h = line_h * len(lines) + T_GAP * (len(lines) - 1)
    return font, lines, line_h, (H - total_h) // 2, total_h


def render_title_frames(text, font_dir, out_dir, entrance_secs):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    font, lines, line_h, y0, total_h = _t_layout(text, font_dir)
    n = int(entrance_secs * FPS)
    for f in range(n):
        t = f / FPS
        img = Image.new("RGBA", (W, H), BLACK)
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
        img.convert("RGB").save(out_dir / f"f_{f:04d}.png")
    return n


# ----------------------------------------------------------------------------
# INFOGRAPHIC — staggered card reveal (three-cards layout; default)
# ----------------------------------------------------------------------------
IG_HX, IG_HY = 90, 96
IG_CARD_W, IG_CARD_H, IG_GAP, IG_CARDS_Y, IG_STROKE = 480, 520, 60, 360, 3
IG_HEADER_AT, IG_SUB_AT, IG_CARD0_AT, IG_CARD_STAGGER = 0.10, 0.34, 0.68, 0.55


def render_infographic_frames(header, subtitle, cards, font_dir, out_dir, entrance_secs):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    fonts = {
        "h": _font(font_dir, "Inter-ExtraBold.ttf", 52),
        "sub": _font(font_dir, "Inter-Regular.ttf", 26),
        "num": _font(font_dir, "Inter-Black.ttf", 92),
        "title": _font(font_dir, "Inter-ExtraBold.ttf", 42),
        "desc": _font(font_dir, "Inter-Regular.ttf", 25),
    }
    cards = (list(cards) + [{"title": "", "description": ""}] * 3)[:3]
    n = int(entrance_secs * FPS)
    total_w = 3 * IG_CARD_W + 2 * IG_GAP
    x0 = (W - total_w) // 2
    for f in range(n):
        t = f / FPS
        img = Image.new("RGBA", (W, H), BLACK)
        img = _blur_in_text(img, (IG_HX, IG_HY), header.upper(), fonts["h"], t, IG_HEADER_AT)
        sage = t - IG_SUB_AT
        if sage >= 0:
            se = _ease(min(sage / ENTRANCE, 1.0))
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(lay).text((IG_HX, IG_HY + 78), subtitle, font=fonts["sub"],
                                     fill=(*GREY, int(255 * se)))
            img = Image.alpha_composite(img, lay)
        for i, card in enumerate(cards):
            cstart = IG_CARD0_AT + i * IG_CARD_STAGGER
            cx = x0 + i * (IG_CARD_W + IG_GAP)
            bage = t - cstart
            if bage < 0:
                continue
            be = _ease(min(bage / ENTRANCE, 1.0))
            scale = 0.96 + 0.04 * be
            cw, ch = int(IG_CARD_W * scale), int(IG_CARD_H * scale)
            ox, oy = cx + (IG_CARD_W - cw) // 2, IG_CARDS_Y + (IG_CARD_H - ch) // 2
            bl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(bl).rounded_rectangle(
                [ox, oy, ox + cw, oy + ch], radius=8,
                outline=(255, 255, 255, int(255 * be)), width=IG_STROKE)
            img = Image.alpha_composite(img, bl)
            img = _blur_in_text(img, (cx + 40, IG_CARDS_Y + 44), f"0{i+1}",
                                fonts["num"], t, cstart + 0.10)
            uage = t - (cstart + 0.20)
            if uage >= 0:
                ue = _ease(min(uage / 0.3, 1.0))
                ul = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ImageDraw.Draw(ul).rectangle(
                    [cx + 42, IG_CARDS_Y + 150, cx + 42 + int(60 * ue), IG_CARDS_Y + 156],
                    fill=(255, 255, 255, 255))
                img = Image.alpha_composite(img, ul)
            img = _blur_in_text(img, (cx + 40, IG_CARDS_Y + 200),
                                (card.get("title") or "").upper(), fonts["title"],
                                t, cstart + 0.26)
            dage = t - (cstart + 0.40)
            if dage >= 0:
                de = _ease(min(dage / ENTRANCE, 1.0))
                dl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                dd = ImageDraw.Draw(dl)
                words = (card.get("description") or "").split()
                lines, cur = [], []
                for w in words:
                    if dd.textbbox((0, 0), " ".join(cur + [w]), font=fonts["desc"])[2] > IG_CARD_W - 90 and cur:
                        lines.append(" ".join(cur)); cur = [w]
                    else:
                        cur.append(w)
                if cur:
                    lines.append(" ".join(cur))
                yy = IG_CARDS_Y + 290
                for ln in lines:
                    dd.text((cx + 40, yy), ln, font=fonts["desc"], fill=(*GREY, int(255 * de)))
                    yy += 34
                img = Image.alpha_composite(img, dl)
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


# public high-level helpers ---------------------------------------------------
def render_overlay_clip(lines, style, font_dir, work_dir, clip_path,
                        entrance_secs, hold_secs):
    """Render an animated text-overlay cutaway clip. Returns clip_path.

    style: 'big-text' | 'title' (opaque cutaways). 'black-gradient' falls back
    to big-text motion on black for now (transparent letterbox variant TBD).
    """
    work_dir = Path(work_dir)
    text = " ".join(l.strip() for l in lines if l and l.strip())
    if style == "title":
        render_title_frames(text, font_dir, work_dir, entrance_secs)
    else:  # big-text (and black-gradient fallback)
        render_big_text_frames(text, font_dir, work_dir, entrance_secs)
    return encode_clip(work_dir, clip_path, entrance_secs, hold_secs)


def render_infographic_clip(card, font_dir, work_dir, clip_path,
                            entrance_secs, hold_secs):
    """Render an animated infographic cutaway clip. Returns clip_path."""
    header = card.get("overall_title") or card.get("title") or ""
    subtitle = card.get("subtitle") or ""
    cards = card.get("cards") or []
    render_infographic_frames(header, subtitle, cards, font_dir, work_dir, entrance_secs)
    return encode_clip(work_dir, clip_path, entrance_secs, hold_secs)
