"""Render CRP MF LF kinetic-word text overlays as transparent PNGs.

Design rules (Larry-approved reference):
- Rule of 3: exactly three lines. Extras clip; shorter is padded.
- Plain text: no varied sizes, no italics, no cascade.
- Inter 800 bold, WHITE, top-left.
- FULL-FRAME letterbox treatment while the overlay is on screen:
    * subtle 24% darken over the whole frame (Stacey still clearly visible)
    * solid thin black bar top (~40px letterbox line)
    * semi-transparent black bar bottom (~180px, mutes but does not hide
      OpusClip's baked-in caption band)
- Two styles accepted:
    * "black-gradient" (DEFAULT): the full-frame letterbox treatment above
    * "white":          clean white text with no darkening at all
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Overlay canvas is now FULL-FRAME 1920x1080. The overlay is composited at
# (0, 0) so the letterbox bars and full-frame darken line up with the video.
OVERLAY_W, OVERLAY_H = 1920, 1080

# Text is confined to the frame-left safe zone so Stacey (frame-right)
# stays clear even during the overlay.
TEXT_X = 100
TEXT_Y = 180  # sits below the top letterbox bar
TEXT_MAX_W = 900  # leaves Stacey clear from x≈1040 onwards

FONT_SIZE = 140
LINE_GAP = 25
WHITE = (255, 255, 255, 255)
TRANSPARENT = (0, 0, 0, 0)

# Full-frame letterbox tuning
DARKEN_ALPHA = 60        # ~24% black over whole frame
TOP_BAR_H = 40           # solid thin letterbox line
BOTTOM_BAR_H = 180       # covers OpusClip caption zone
BOTTOM_BAR_ALPHA = 200   # semi-transparent — mutes but doesn't hide caption

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


def _draw_letterbox(img: Image.Image) -> None:
    """Larry-approved full-frame letterbox: soft darken + top/bottom bars."""
    w, h = img.size
    # Subtle full-frame darken — keeps Stacey visible, gives text contrast
    darken = Image.new("RGBA", (w, h), (0, 0, 0, DARKEN_ALPHA))
    img.alpha_composite(darken)
    # Solid thin letterbox line at top
    top_bar = Image.new("RGBA", (w, TOP_BAR_H), (0, 0, 0, 255))
    img.alpha_composite(top_bar, (0, 0))
    # Semi-transparent bar at bottom so OpusClip caption bleeds through muted
    bottom_bar = Image.new("RGBA", (w, BOTTOM_BAR_H), (0, 0, 0, BOTTOM_BAR_ALPHA))
    img.alpha_composite(bottom_bar, (0, h - BOTTOM_BAR_H))


def render_overlay(
    lines: list[str],
    style: str,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """Render one text-overlay PNG. Returns the PNG path."""
    img = Image.new("RGBA", (OVERLAY_W, OVERLAY_H), TRANSPARENT)

    if style != "white":  # default: black-gradient letterbox
        _draw_letterbox(img)

    draw = ImageDraw.Draw(img)

    padded = (list(lines) + ["", "", ""])[:3]

    # Auto-shrink so the widest line fits inside TEXT_MAX_W
    size = FONT_SIZE
    font = _load_font(font_dir, size)
    while size > 40:
        widths = [draw.textbbox((0, 0), line, font=font)[2] for line in padded if line]
        if not widths or max(widths) <= TEXT_MAX_W:
            break
        size -= 4
        font = _load_font(font_dir, size)

    y = TEXT_Y
    for line in padded:
        draw.text((TEXT_X, y), line, font=font, fill=WHITE)
        y += size + LINE_GAP

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
