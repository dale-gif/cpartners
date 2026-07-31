"""Animated infographic renderer — staggered card reveal on black.

Matches the Larry-approved "Three Cards" template motion:
  1. header title blur-in, subtitle fade
  2. each card enters in sequence: box draws/fades in, number blur-in,
     underline grows, title blur-in, description fade
Rule of 3: always exactly 3 cards.
"""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1920, 1080
FPS = 25
BLACK = (0, 0, 0, 255)
GREY = (150, 150, 150, 255)

# header
HX, HY = 90, 96

# cards
CARD_W, CARD_H, GAP = 480, 520, 60
CARDS_Y = 360
STROKE = 3

# timing
HEADER_AT = 0.10
SUB_AT = 0.34
CARD0_AT = 0.68
CARD_STAGGER = 0.55
ENTRANCE = 0.34
MAX_BLUR = 30


def _ease(p):
    return 1 - (1 - p) ** 3


def _f(font_dir, name, size):
    p = Path(font_dir) / name
    if p.exists():
        return ImageFont.truetype(str(p), size)
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)


def _blur_text(img, xy, text, font, t, start, fill=(255, 255, 255)):
    """Composite text with blur-in based on age."""
    age = t - start
    if age < 0:
        return img
    e = _ease(min(age / ENTRANCE, 1.0))
    blur = (1 - e) * MAX_BLUR
    alpha = int(255 * min(e * 1.15, 1.0))
    y_off = int((1 - e) * 16)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((xy[0], xy[1] - y_off), text, font=font, fill=(*fill, alpha))
    if blur > 0.5:
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(img, layer)


def render_frame(t, header, subtitle, cards, fonts):
    img = Image.new("RGBA", (W, H), BLACK)

    # header + subtitle
    img = _blur_text(img, (HX, HY), header.upper(), fonts["h"], t, HEADER_AT)
    # subtitle simple fade
    sage = t - SUB_AT
    if sage >= 0:
        se = _ease(min(sage / ENTRANCE, 1.0))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.text((HX, HY + 78), subtitle, font=fonts["sub"], fill=(*GREY[:3], int(255 * se)))
        img = Image.alpha_composite(img, layer)

    total_w = 3 * CARD_W + 2 * GAP
    x0 = (W - total_w) // 2

    for i, card in enumerate(cards[:3]):
        cstart = CARD0_AT + i * CARD_STAGGER
        cx = x0 + i * (CARD_W + GAP)
        # box: fade + slight scale-up
        bage = t - cstart
        if bage < 0:
            continue
        be = _ease(min(bage / ENTRANCE, 1.0))
        box_alpha = int(255 * be)
        scale = 0.96 + 0.04 * be
        cw, ch = int(CARD_W * scale), int(CARD_H * scale)
        ox = cx + (CARD_W - cw) // 2
        oy = CARDS_Y + (CARD_H - ch) // 2
        blayer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(blayer)
        bd.rounded_rectangle([ox, oy, ox + cw, oy + ch], radius=8,
                             outline=(255, 255, 255, box_alpha), width=STROKE)
        img = Image.alpha_composite(img, blayer)

        # number blur-in
        img = _blur_text(img, (cx + 40, CARDS_Y + 44), f"0{i+1}", fonts["num"],
                         t, cstart + 0.10)
        # underline dash grows
        uage = t - (cstart + 0.20)
        if uage >= 0:
            ue = _ease(min(uage / 0.3, 1.0))
            ul = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ud = ImageDraw.Draw(ul)
            ud.rectangle([cx + 42, CARDS_Y + 150, cx + 42 + int(60 * ue), CARDS_Y + 156],
                         fill=(255, 255, 255, 255))
            img = Image.alpha_composite(img, ul)
        # title blur-in
        img = _blur_text(img, (cx + 40, CARDS_Y + 200), card["title"], fonts["title"],
                         t, cstart + 0.26)
        # description fade
        dage = t - (cstart + 0.40)
        if dage >= 0:
            de = _ease(min(dage / ENTRANCE, 1.0))
            dl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dd = ImageDraw.Draw(dl)
            # wrap desc to card width
            words = card["desc"].split()
            lines, cur = [], []
            for w in words:
                if dd.textbbox((0, 0), " ".join(cur + [w]), font=fonts["desc"])[2] > CARD_W - 90 and cur:
                    lines.append(" ".join(cur)); cur = [w]
                else:
                    cur.append(w)
            if cur:
                lines.append(" ".join(cur))
            yy = CARDS_Y + 290
            for ln in lines:
                dd.text((cx + 40, yy), ln, font=fonts["desc"], fill=(*GREY[:3], int(255 * de)))
                yy += 34
            img = Image.alpha_composite(img, dl)
    return img


def render_clip(header, subtitle, cards, font_dir, out_dir, seconds):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    fonts = {
        "h": _f(font_dir, "Inter-ExtraBold.ttf", 52),
        "sub": _f(font_dir, "Inter-Regular.ttf", 26),
        "num": _f(font_dir, "Inter-Black.ttf", 92),
        "title": _f(font_dir, "Inter-ExtraBold.ttf", 42),
        "desc": _f(font_dir, "Inter-Regular.ttf", 25),
    }
    n = int(seconds * FPS)
    for f in range(n):
        render_frame(f / FPS, header, subtitle, cards, fonts).convert("RGB").save(
            out_dir / f"f_{f:04d}.png")
    return n


if __name__ == "__main__":
    cards = [
        {"title": "Your Title Here", "desc": "Short description or supporting text here"},
        {"title": "Your Title Here", "desc": "Short description or supporting text here"},
        {"title": "Your Title Here", "desc": "Short description or supporting text here"},
    ]
    n = render_clip("1. Three Cards", "Good for: Steps, Tips, Issues", cards,
                    "fonts", "/home/claude/anim_out/threecards_anim", 3.2)
    print(f"rendered {n} frames")
