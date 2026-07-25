"""Render GODTIER bordered infographic cards as transparent PNGs.

Design rules (Larry-approved, locked):
- Pure black and white. No colour. No glow.
- Bordered card only.
- CAPS title, thin white rule, list items with big numbers.
- Large type but margined; never edge-to-edge.
- Sits frame-left (Stacey is frame-right).
- Clear of the bottom caption band.
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

CARD_W, CARD_H = 780, 900
BORDER = 4
MARGIN = 56
TITLE_SIZE = 54
ITEM_NUM_SIZE = 96
ITEM_TEXT_SIZE = 42
RULE_H = 2
WHITE = (255, 255, 255, 255)
GREY = (170, 170, 170, 255)
BLACK_TRANSLUCENT = (0, 0, 0, 220)
TRANSPARENT = (0, 0, 0, 0)


def _load_font(font_dir: Path, weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = font_dir / f"Inter-{weight}.ttf"
    if not path.exists():
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size)


def render_card(
    title: str,
    items: list[str],
    font_dir: Path,
    out_path: Path,
) -> Path:
    """Render one bordered infographic card. Returns the PNG path."""
    img = Image.new("RGBA", (CARD_W, CARD_H), TRANSPARENT)
    draw = ImageDraw.Draw(img)

    # Black semi-opaque card fill so it reads on any footage
    draw.rectangle([0, 0, CARD_W, CARD_H], fill=BLACK_TRANSLUCENT)

    # White bordered rectangle
    for i in range(BORDER):
        draw.rectangle(
            [i, i, CARD_W - 1 - i, CARD_H - 1 - i],
            outline=WHITE,
        )

    title_font = _load_font(font_dir, "Black", TITLE_SIZE)
    num_font = _load_font(font_dir, "Black", ITEM_NUM_SIZE)
    text_font = _load_font(font_dir, "Regular", ITEM_TEXT_SIZE)

    y = MARGIN + 8
    caps_title = (title or "").upper().strip()
    draw.text((MARGIN, y), caps_title, font=title_font, fill=WHITE)

    # Thin white rule under title
    y += TITLE_SIZE + 20
    draw.rectangle(
        [MARGIN, y, CARD_W - MARGIN, y + RULE_H],
        fill=WHITE,
    )

    # Items: big number, then text on the right
    y += 40
    row_gap = 30
    for i, item in enumerate(items, start=1):
        num_str = f"{i:02d}"
        num_bbox = draw.textbbox((0, 0), num_str, font=num_font)
        num_w = num_bbox[2] - num_bbox[0]
        num_h = num_bbox[3] - num_bbox[1]

        draw.text((MARGIN, y), num_str, font=num_font, fill=WHITE)

        text_x = MARGIN + num_w + 28
        text_y = y + (num_h // 2) - (ITEM_TEXT_SIZE // 2)
        max_text_w = CARD_W - text_x - MARGIN

        # Naive word-wrap
        words = item.upper().split()
        line = ""
        line_y = text_y
        for w in words:
            trial = f"{line} {w}".strip()
            trial_bbox = draw.textbbox((0, 0), trial, font=text_font)
            if trial_bbox[2] - trial_bbox[0] > max_text_w and line:
                draw.text((text_x, line_y), line, font=text_font, fill=GREY)
                line_y += ITEM_TEXT_SIZE + 4
                line = w
            else:
                line = trial
        if line:
            draw.text((text_x, line_y), line, font=text_font, fill=GREY)

        y += num_h + row_gap

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
