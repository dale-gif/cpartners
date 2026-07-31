"""Animated Title renderer — line-draw + mask-wipe reveal on black.

Matches the Larry-approved Title template:
  1. thin vertical white line draws in from the top
  2. text reveals left-to-right, wiping out from behind the line
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FPS = 25
BLACK = (0, 0, 0, 255)

TITLE_SIZE = 118
LINE_X = 150
TEXT_X = 190
MAX_W = 1500
LINE_GAP = 16
LINE_W = 4

# timing (seconds)
LINE_DRAW = 0.35        # line grows top->bottom
TEXT_START = 0.28       # text begins wiping shortly after line starts
TEXT_WIPE = 0.55        # full left->right reveal duration
SLIDE = 26              # px the text slides right as it reveals


def _ease_out(p):
    return 1 - (1 - p) ** 3


def _load_font(font_dir, size):
    for name in ("Inter-Black.ttf", "Inter-ExtraBold.ttf"):
        p = font_dir / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)


def _layout(text, font_dir):
    scratch = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(scratch)
    size = TITLE_SIZE
    font = _load_font(Path(font_dir), size)
    words = text.upper().split()
    # wrap to <= MAX_W
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        if d.textbbox((0, 0), trial, font=font)[2] > MAX_W and cur:
            lines.append(" ".join(cur)); cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    asc, desc = font.getmetrics()
    line_h = asc + desc
    total_h = line_h * len(lines) + LINE_GAP * (len(lines) - 1)
    y0 = (H - total_h) // 2
    return font, lines, line_h, y0, total_h


def render_frame(t, font, lines, line_h, y0, total_h):
    img = Image.new("RGBA", (W, H), BLACK)
    draw = ImageDraw.Draw(img)

    # 1. vertical line draws top->bottom
    lp = _ease_out(min(t / LINE_DRAW, 1.0))
    line_top = y0 - 18
    line_full = total_h + 36
    line_bot = line_top + int(line_full * lp)
    if lp > 0:
        draw.rectangle([LINE_X, line_top, LINE_X + LINE_W, line_bot], fill=(255, 255, 255, 255))

    # 2. text mask-wipe left->right out from the line
    tp_raw = (t - TEXT_START) / TEXT_WIPE
    tp = _ease_out(min(max(tp_raw, 0.0), 1.0))
    if tp > 0:
        text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(text_layer)
        x_off = int((1 - tp) * SLIDE)
        y = y0
        for ln in lines:
            td.text((TEXT_X - x_off, y), ln, font=font, fill=(255, 255, 255, 255))
            y += line_h + LINE_GAP
        # reveal mask: rectangle growing from line rightward
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        reveal_w = int((W - TEXT_X) * tp) + 60
        md.rectangle([0, 0, TEXT_X + reveal_w, H], fill=255)
        text_layer.putalpha(Image.composite(text_layer.getchannel("A"),
                                             Image.new("L", (W, H), 0), mask))
        img = Image.alpha_composite(img, text_layer)
    return img


def render_clip(text, font_dir, out_dir, seconds):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    font, lines, line_h, y0, total_h = _layout(text, font_dir)
    n = int(seconds * FPS)
    for f in range(n):
        frame = render_frame(f / FPS, font, lines, line_h, y0, total_h)
        frame.convert("RGB").save(out_dir / f"f_{f:04d}.png")
    return n


if __name__ == "__main__":
    import sys
    text = sys.argv[1] if len(sys.argv) > 1 else "THE DEBT OR RESPOND"
    outd = sys.argv[2] if len(sys.argv) > 2 else "/home/claude/anim_out/title"
    secs = float(sys.argv[3]) if len(sys.argv) > 3 else 2.5
    print(f"rendered {render_clip(text, 'fonts', outd, secs)} frames to {outd}")
