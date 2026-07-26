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

# Overlay canvas matches the frame-left "safe zone" red-box exactly (900x760).
# Composited 1:1 into a 1920x1080 frame at position (OVERLAY_X, OVERLAY_Y),
# it stays clear of Stacey (frame-right, from x≈940) and OpusClip's bottom
# caption band (from y≈820).
OVERLAY_W, OVERLAY_H = 900, 760
FONT_SIZE = 140
LINE_GAP = 25
MARGIN = 30
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


# _draw_black_gradient removed — Larry's approved style is clean white text
# on a fully-transparent overlay (no background box, no gradient, no shadow).


def render_overlay(
    lines: list[str],
    style: str,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """Render one text-overlay PNG. Returns the PNG path.

    Larry-approved style: clean bold WHITE text on a fully-transparent canvas
    — no black-gradient background, no shadow, no box. The text sits directly
    over Stacey (frame-left, safe zone). Style parameter is accepted for
    back-compat but ignored — everything renders the same clean way.

    Auto-shrinks the font so the longest line fits within the canvas width.
    Keeps the "Rule of 3": always renders 3 lines (pads with blanks).
    """
    _ = style  # intentionally unused; kept for back-compat
    img = Image.new("RGBA", (OVERLAY_W, OVERLAY_H), TRANSPARENT)
    draw = ImageDraw.Draw(img)

    # Normalize to exactly 3 lines
    padded = (list(lines) + ["", "", ""])[:3]

    # Auto-shrink font so every line fits within the canvas width
    max_width = OVERLAY_W - 2 * MARGIN
    size = FONT_SIZE
    font = _load_font(font_dir, size)
    while size > 40:
        widths = [draw.textbbox((0, 0), line, font=font)[2] for line in padded if line]
        if not widths or max(widths) <= max_width:
            break
        size -= 4
        font = _load_font(font_dir, size)

    # Top-anchored inside the canvas so it stays above OpusClip's caption band
    y = 40
    for line in padded:
        draw.text((MARGIN, y), line, font=font, fill=WHITE)
        y += size + LINE_GAP

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
