"""Render CRP MF LF kinetic-word text overlays as transparent PNGs.

Design rules (Larry-approved reference):
- Rule of 3: exactly three lines. Extras clip; shorter is padded.
- Plain text: no varied sizes, no italics, no cascade.
- Inter 800 bold, WHITE, top-left.
- Letterbox treatment while the overlay is on screen:
    * subtle 24% darken across the top of the frame ONLY (up to y=880)
    * solid thin black bar top (~40px letterbox line)
    * NO bottom bar — the OpusClip caption band (y=880..1080) stays fully
      clean and bright, unmuted by the overlay
- Two styles accepted:
    * "black-gradient" (DEFAULT): the letterbox treatment above
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

# Letterbox tuning — the caption zone (y=CAPTION_ZONE_Y..OVERLAY_H) is left
# completely untouched so OpusClip's baked-in captions stay bright and clean.
DARKEN_ALPHA = 60        # ~24% black over content area only
TOP_BAR_H = 40           # solid thin letterbox line at very top
CAPTION_ZONE_Y = 880     # y at which the OpusClip caption band begins

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
    """Larry-approved letterbox: soft darken above the caption zone + top bar.

    Explicitly does NOT touch the OpusClip caption band at the bottom — that
    zone (y >= CAPTION_ZONE_Y) stays fully transparent so captions render
    bright and unmuted, matching Larry's approved reference.
    """
    w, _h = img.size
    # Subtle darken across the content area only (top of frame up to caption
    # zone). Bottom band is untouched.
    darken = Image.new("RGBA", (w, CAPTION_ZONE_Y), (0, 0, 0, DARKEN_ALPHA))
    img.alpha_composite(darken, (0, 0))
    # Solid thin letterbox line at very top
    top_bar = Image.new("RGBA", (w, TOP_BAR_H), (0, 0, 0, 255))
    img.alpha_composite(top_bar, (0, 0))


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
