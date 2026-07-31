"""Render CRP MF LF kinetic-word text overlays as PNGs.

THREE STYLES (Larry-approved):

1. "black-gradient" (DEFAULT) — transparent overlay ON TOP of video:
   - Subtle 24% darken across top of frame (up to y=880)
   - Solid thin black bar top (~40px)
   - NO bottom bar — OpusClip caption band stays clean
   - Inter ExtraBold, WHITE, frame-left safe zone
   - Exactly 3 lines, Rule of 3

2. "big-text" — OPAQUE full-frame cutaway:
   - Solid black background — fully covers the video
   - Massive bold white text, CENTER-ALIGNED
   - Auto-sizes to fill frame width (1-2 lines)
   - No letterbox, no gradient — pure black + white text
   - Used for impact statements / sentiment peaks

3. "title" — OPAQUE section title cutaway:
   - Solid black background — fully covers the video
   - Thin vertical white line on left of text
   - Large bold white text, LEFT-ALIGNED, vertically centered
   - Used for chapter/section title cards
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Overlay canvas is FULL-FRAME 1920x1080.
OVERLAY_W, OVERLAY_H = 1920, 1080

# ---- black-gradient style constants ----
TEXT_X = 100
TEXT_Y = 180
TEXT_MAX_W = 900

FONT_SIZE = 140
LINE_GAP = 25
WHITE = (255, 255, 255, 255)
BLACK_OPAQUE = (0, 0, 0, 255)
TRANSPARENT = (0, 0, 0, 0)

DARKEN_ALPHA = 60
TOP_BAR_H = 40
CAPTION_ZONE_Y = 880

# ---- big-text style constants ----
BIG_TEXT_MAX_SIZE = 200
BIG_TEXT_MAX_W = 1600
BIG_TEXT_LINE_GAP = 20

# ---- title style constants ----
TITLE_FONT_SIZE = 120
TITLE_LINE_X = 80
TITLE_TEXT_X = 110
TITLE_MAX_W = 1400
TITLE_LINE_GAP = 15

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
    """Larry-approved letterbox: soft darken above caption zone + top bar."""
    w, _h = img.size
    darken = Image.new("RGBA", (w, CAPTION_ZONE_Y), (0, 0, 0, DARKEN_ALPHA))
    img.alpha_composite(darken, (0, 0))
    top_bar = Image.new("RGBA", (w, TOP_BAR_H), (0, 0, 0, 255))
    img.alpha_composite(top_bar, (0, 0))


def _render_black_gradient(lines: list[str], font_dir: Path, img: Image.Image) -> None:
    """Original black-gradient style: letterbox + frame-left text."""
    _draw_letterbox(img)
    draw = ImageDraw.Draw(img)
    padded = (list(lines) + ["", "", ""])[:3]

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


def _render_big_text(lines: list[str], font_dir: Path, img: Image.Image) -> None:
    """Big Text style: opaque black, massive centered text.

    All text joined into 1-2 massive lines, auto-sized to fill frame.
    Vertically and horizontally centered.
    """
    draw = ImageDraw.Draw(img)

    full_text = " ".join(line.strip() for line in lines if line.strip()).upper()
    if not full_text:
        return

    # Try as single line first
    size = BIG_TEXT_MAX_SIZE
    font = _load_font(font_dir, size)

    while size > 60:
        bbox = draw.textbbox((0, 0), full_text, font=font)
        tw = bbox[2] - bbox[0]
        if tw <= BIG_TEXT_MAX_W:
            break
        size -= 4
        font = _load_font(font_dir, size)

    bbox = draw.textbbox((0, 0), full_text, font=font)
    tw = bbox[2] - bbox[0]

    if tw > BIG_TEXT_MAX_W:
        # Split into 2 lines at best word break
        words = full_text.split()
        best_split = len(words) // 2
        display_lines = [
            " ".join(words[:best_split]),
            " ".join(words[best_split:]),
        ]
        size = BIG_TEXT_MAX_SIZE
        font = _load_font(font_dir, size)
        while size > 60:
            widths = [draw.textbbox((0, 0), l, font=font)[2] for l in display_lines]
            if max(widths) <= BIG_TEXT_MAX_W:
                break
            size -= 4
            font = _load_font(font_dir, size)
    else:
        display_lines = [full_text]

    # Calculate total text block height
    line_heights = []
    for line in display_lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_heights.append(bbox[3] - bbox[1])
    total_h = sum(line_heights) + BIG_TEXT_LINE_GAP * (len(display_lines) - 1)

    # Vertically center
    y = (OVERLAY_H - total_h) // 2

    for i, line in enumerate(display_lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (OVERLAY_W - tw) // 2
        draw.text((x, y), line, font=font, fill=WHITE)
        y += line_heights[i] + BIG_TEXT_LINE_GAP


def _render_title(lines: list[str], font_dir: Path, img: Image.Image) -> None:
    """Title style: opaque black, vertical line + left-aligned text.

    Thin vertical white line on the left, large bold text beside it,
    vertically centered.
    """
    draw = ImageDraw.Draw(img)

    display_lines = [line.strip().upper() for line in lines if line.strip()]
    if not display_lines:
        return
    display_lines = display_lines[:3]

    size = TITLE_FONT_SIZE
    font = _load_font(font_dir, size)
    while size > 40:
        widths = [draw.textbbox((0, 0), l, font=font)[2] for l in display_lines]
        if max(widths) <= TITLE_MAX_W:
            break
        size -= 4
        font = _load_font(font_dir, size)

    # Calculate total text block height
    line_heights = []
    for line in display_lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_heights.append(bbox[3] - bbox[1])
    total_h = sum(line_heights) + TITLE_LINE_GAP * (len(display_lines) - 1)

    # Vertically center
    y_start = (OVERLAY_H - total_h) // 2

    # Vertical line
    line_top = y_start - 20
    line_bot = y_start + total_h + 20
    draw.line([(TITLE_LINE_X, line_top), (TITLE_LINE_X, line_bot)],
              fill=WHITE, width=3)

    # Draw text
    y = y_start
    for i, line in enumerate(display_lines):
        draw.text((TITLE_TEXT_X, y), line, font=font, fill=WHITE)
        y += line_heights[i] + TITLE_LINE_GAP


def render_overlay(
    lines: list[str],
    style: str,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """Render one text-overlay PNG. Returns the PNG path.

    Styles:
      - "black-gradient": transparent overlay with letterbox (DEFAULT)
      - "big-text": opaque black cutaway with massive centered text
      - "title": opaque black cutaway with vertical line + left text
    """
    if style in ("big-text", "title"):
        img = Image.new("RGBA", (OVERLAY_W, OVERLAY_H), BLACK_OPAQUE)
    else:
        img = Image.new("RGBA", (OVERLAY_W, OVERLAY_H), TRANSPARENT)

    if style == "big-text":
        _render_big_text(lines, font_dir, img)
    elif style == "title":
        _render_title(lines, font_dir, img)
    else:
        _render_black_gradient(lines, font_dir, img)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
