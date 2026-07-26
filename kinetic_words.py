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

# Overlay is authored at 1920x1080; compose scales it by 0.667 so it fills
# the composited 1280x720 frame. Font sizes are source-space (post-scale in
# the final MP4 they render at ~2/3 these values).
OVERLAY_W, OVERLAY_H = 1920, 1080
FONT_SIZE = 180  # ≈120px in final composited output (well above Larry's 104px spec)
LINE_GAP = 40
MARGIN = 120
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
    """Left-anchored black gradient that fades to transparent on the right.

    Solid black covers the left 60% (comfortably behind Larry's frame-left
    text zone). Ramps to transparent between 60% and 92%. This keeps text
    fully readable on any background Stacey is filmed against.
    """
    w, h = img.size
    grad = Image.new("L", (w, 1), 0)
    for x in range(w):
        if x < w * 0.60:
            alpha = 235
        elif x < w * 0.92:
            t = (x - w * 0.60) / (w * 0.32)
            alpha = int(235 * (1 - t))
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

    # Auto-shrink font so every line fits within the readable area.
    # For 'black-gradient' the black fill only covers the left ~55% of the
    # canvas (fades to transparent after), so text must fit inside that zone.
    # For 'white' the whole canvas is a clean overlay so more width is usable.
    if style == "black-gradient":
        max_width = int(OVERLAY_W * 0.5) - MARGIN
    else:
        max_width = int(OVERLAY_W * 0.85) - MARGIN
    size = FONT_SIZE
    font = _load_font(font_dir, size)
    while size > 40:
        widths = [draw.textbbox((0, 0), line, font=font)[2] for line in padded if line]
        if not widths or max(widths) <= max_width:
            break
        size -= 4
        font = _load_font(font_dir, size)

    # Anchor text to TOP of canvas so it stays above OpusClip's caption band
    # once composited. The overlay is placed at (OVERLAY_X, OVERLAY_Y) in the
    # frame; keeping text near y=60 in the canvas gives predictable framing.
    y = 60
    for line in padded:
        draw.text((MARGIN, y), line, font=font, fill=WHITE)
        y += size + LINE_GAP

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
