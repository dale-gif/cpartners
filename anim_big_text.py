"""Animated Big Text renderer — word-by-word blur-in on black.

Emits a TRUE 25fps frame sequence matching the Larry-approved Big Text
templates: each word enters one at a time with a gaussian blur-in that
resolves to crisp, staggered ~0.28s apart.

This is the real motion — not a cross-fade of static PNGs.
"""
from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1920, 1080
FPS = 25

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)

MAX_W = 1500          # text block max width before wrapping
MAX_SIZE = 210        # starting font size (auto-shrinks to fit)
MIN_SIZE = 70
LINE_GAP = 24

# ---- animation timing (seconds) ----
BASE_DELAY = 0.15     # before first word enters
STAGGER = 0.28        # gap between word entrances
ENTRANCE = 0.34       # how long one word takes to resolve
MAX_BLUR = 34         # peak gaussian blur radius at font scale
RISE = 22             # px the word rises as it settles


def _ease_out(p: float) -> float:
    """Cubic ease-out."""
    return 1 - (1 - p) ** 3


def _load_font(font_dir: Path, size: int) -> ImageFont.FreeTypeFont:
    for name in ("Inter-Black.ttf", "Inter-ExtraBold.ttf"):
        p = font_dir / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)


def _layout(words, font, draw):
    """Wrap words to <=MAX_W. Return (lines, size-derived line height)."""
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        tw = draw.textbbox((0, 0), trial, font=font)[2]
        if tw > MAX_W and cur:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def _measure(text, font, draw):
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0], b[3] - b[1]


def build_word_boxes(full_text, font_dir):
    """Compute per-word screen positions. Returns (font, [ (word, x, y) ])."""
    words = full_text.upper().split()
    scratch = Image.new("RGBA", (W, H))
    draw = ImageDraw.Draw(scratch)

    size = MAX_SIZE
    while size > MIN_SIZE:
        font = _load_font(font_dir, size)
        lines = _layout(words, font, draw)
        widest = max(_measure(" ".join(l), font, draw)[0] for l in lines)
        if widest <= MAX_W:
            break
        size -= 6
    font = _load_font(font_dir, size)
    lines = _layout(words, font, draw)

    # ascent/line height
    asc, desc = font.getmetrics()
    line_h = asc + desc
    total_h = line_h * len(lines) + LINE_GAP * (len(lines) - 1)
    y = (H - total_h) // 2

    boxes = []
    space_w = draw.textbbox((0, 0), " ", font=font)[2]
    for line in lines:
        line_text = " ".join(line)
        lw = _measure(line_text, font, draw)[0]
        x = (W - lw) // 2
        for w in line:
            ww = draw.textbbox((0, 0), w, font=font)[2]
            boxes.append((w, x, y))
            x += ww + space_w
        y += line_h + LINE_GAP
    return font, boxes


def render_frame(t, font, boxes):
    """Render the frame at time t seconds."""
    img = Image.new("RGBA", (W, H), BLACK)
    for i, (word, bx, by) in enumerate(boxes):
        start = BASE_DELAY + i * STAGGER
        age = t - start
        if age < 0:
            continue
        p = min(age / ENTRANCE, 1.0)
        e = _ease_out(p)
        blur = (1 - e) * MAX_BLUR
        alpha = int(255 * min(e * 1.15, 1.0))
        y_off = int((1 - e) * RISE)

        # render this word on its own layer so blur doesn't bleed neighbors
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.text((bx, by - y_off), word, font=font, fill=(255, 255, 255, alpha))
        if blur > 0.5:
            layer = layer.filter(ImageFilter.GaussianBlur(blur))
        img = Image.alpha_composite(img, layer)
    return img


def render_clip(full_text, font_dir, out_dir, seconds):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    font, boxes = build_word_boxes(full_text, Path(font_dir))
    n = int(seconds * FPS)
    for f in range(n):
        t = f / FPS
        frame = render_frame(t, font, boxes)
        frame.convert("RGB").save(out_dir / f"f_{f:04d}.png")
    return n


if __name__ == "__main__":
    import sys
    text = sys.argv[1] if len(sys.argv) > 1 else "YOU ARE NOT POWERLESS"
    outd = sys.argv[2] if len(sys.argv) > 2 else "/home/claude/anim_out/bigtext"
    secs = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
    n = render_clip(text, "fonts", outd, secs)
    print(f"rendered {n} frames to {outd}")
