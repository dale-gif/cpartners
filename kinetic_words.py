"""Render GODTIER kinetic-word text overlays as transparent PNGs.

Design rules:
- Rule of 3: exactly three lines. Extras clip; shorter is padded.
- Plain text: no varied sizes, no italics, no cascade.
- Inter 800 bold, 104px at 1920 wide.
- Two approved styles:
    - "white":          white text sitting directly over Stacey
    - "black-gradient": white text on black gradient blended into frame
- Frame-left, cleared of the bottom caption band.
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OVERLAY_W, OVERLAY_H = 1080, 720
FONT_SIZE = 104
LINE_GAP = 24
MARGIN = 72
WHITE = (255, 255, 255, 255)
TRANSPARENT = (0, 0, 0, 0)


_DEJAVU_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _load_font(font_dir: Path, size: int) -> ImageFont.FreeTypeFont:
    path = font_dir / "Inter-ExtraBold.ttf"
    if path.exists():
        return ImageFont.truetype(str(path), size)
    if Path(_DEJAVU_BOLD).exists():
        print(f"[kinetic_words] WARNING: Inter-ExtraBold not at {path}, using DejaVu")
        return ImageFont.truetype(_DEJAVU_BOLD, size)
    print(f"[kinetic_words] ERROR: no scalable font found (Inter or DejaVu)")
    return ImageFont.load_default()


def _draw_black_gradient(img: Image.Image) -> None:
    """Left-anchored black gradient that fades to transparent on the right."""
    w, h = img.size
    grad = Image.new("L", (w, 1), 0)
    for x in range(w):
        # Full black on the left third, ramp to 0 across the middle third
        if x < w * 0.55:
            alpha = 230
        elif x < w * 0.9:
            t = (x - w * 0.55) / (w * 0.35)
            alpha = int(230 * (1 - t))
        else:
            alpha = 0
        grad.putpixel((x, 0), alpha)
    grad = grad.resize((w, h))
    black_layer = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    black_layer.putalpha(grad)
    img.alpha_composite(black_layer)


def render_overlay(
    lines: list[str],
    style: str,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """Render one text-overlay PNG. Returns the PNG path.

    Auto-shrinks the font so the longest line fits within the canvas width.
    Keeps the "Rule of 3": always renders 3 lines (pads with blanks).
    """
    img = Image.new("RGBA", (OVERLAY_W, OVERLAY_H), TRANSPARENT)

    if style == "black-gradient":
        _draw_black_gradient(img)

    draw = ImageDraw.Draw(img)

    # Normalize to exactly 3 lines
    padded = (list(lines) + ["", "", ""])[:3]

    # Auto-shrink font so every line fits within the horizontal margin
    max_width = OVERLAY_W - 2 * MARGIN
    size = FONT_SIZE
    font = _load_font(font_dir, size)
    while size > 40:
        widths = [draw.textbbox((0, 0), line, font=font)[2] for line in padded if line]
        if not widths or max(widths) <= max_width:
            break
        size -= 4
        font = _load_font(font_dir, size)

    total_h = size * 3 + LINE_GAP * 2
    y = (OVERLAY_H - total_h) // 2

    for line in padded:
        draw.text((MARGIN, y), line, font=font, fill=WHITE)
        y += size + LINE_GAP

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
