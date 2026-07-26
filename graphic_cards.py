"""Render GODTIER infographic graphics as transparent PNGs.

Larry-approved reference (from the WHY THEY DON'T PAY frame): three bordered
cards side-by-side, each with a big number, a thin white rule, a punchy CAPS
title, and a 2-3 phrase description in grey. Overall CAPS title + short
subtitle sit above the row.

Everything is pure black-and-white. Everything is CAPS. Descriptions are
punchy (~2-3 short phrases). Titles are 1-3 words.

Dispatch:
    render_infographic(spec, font_dir, out_path)
      spec.template = "three-cards"  -> _render_three_cards (default, approved)
      spec.template = "bordered-list" -> _render_bordered_list (legacy fallback)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# ---- Canvas sizing --------------------------------------------------------
# Infographic PNG is authored at 1920x1080; compose_from_plan scales it by
# SCALE (0.667) so it lands as a 1280x720 full-frame overlay.
CANVAS_W = 1920
CANVAS_H = 1080

# ---- Palette --------------------------------------------------------------
WHITE = (255, 255, 255, 255)
GREY = (170, 170, 170, 255)
BLACK_OPAQUE = (0, 0, 0, 255)      # cutaway backgrounds
BLACK_TRANSLUCENT = (0, 0, 0, 235) # bordered cards
TRANSPARENT = (0, 0, 0, 0)

# ---- Font loading ---------------------------------------------------------
_DEJAVU_FALLBACK = {
    "Regular":   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "Black":     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "ExtraBold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
}


def _load_font(font_dir: Path, weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = font_dir / f"Inter-{weight}.ttf"
    if path.exists():
        return ImageFont.truetype(str(path), size)
    fallback = _DEJAVU_FALLBACK.get(weight)
    if fallback and Path(fallback).exists():
        return ImageFont.truetype(fallback, size)
    print(f"[graphic_cards] WARNING: no scalable font for {weight}, falling back tiny")
    return ImageFont.load_default()


def _shrink_to_fit(draw, text, weight, font_dir, max_width, start_size, min_size=20):
    size = start_size
    font = _load_font(font_dir, weight, size)
    while size > min_size:
        w = draw.textbbox((0, 0), text, font=font)[2]
        if w <= max_width:
            return font, size
        size -= 2
        font = _load_font(font_dir, weight, size)
    return font, size


def _wrap(text: str, font, draw, max_width: int) -> list[str]:
    """Greedy word-wrap a single string into lines fitting max_width."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


# ---- Public dispatch ------------------------------------------------------

def render_infographic(spec: dict[str, Any], font_dir: Path, out_path: Path) -> Path:
    template = (spec.get("template") or "three-cards").lower()
    if template in ("three-cards", "three_cards", "cards"):
        return _render_three_cards(spec, font_dir, out_path)
    # Legacy single-column fallback (kept so older Claude outputs still render)
    return _render_bordered_list(spec, font_dir, out_path)


# ---- Three-cards template (Larry-approved) --------------------------------

def _render_three_cards(spec: dict[str, Any], font_dir: Path, out_path: Path) -> Path:
    overall_title = (spec.get("overall_title") or spec.get("title") or "").upper().strip()
    subtitle = (spec.get("subtitle") or "").upper().strip()

    raw_cards = spec.get("cards") or []
    # Back-compat: if Claude still uses items[], synthesize cards from them.
    if not raw_cards and spec.get("items"):
        raw_cards = [{"title": "", "description": str(x).upper()} for x in spec["items"][:3]]

    # Always render exactly 3 cards (pad with blanks so layout stays balanced)
    cards = (list(raw_cards) + [{}, {}, {}])[:3]

    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), BLACK_OPAQUE)
    draw = ImageDraw.Draw(img)

    # ---- Header (title + subtitle) ----
    header_x = 100
    header_y = 100
    title_font = _load_font(font_dir, "Black", 88)
    if overall_title:
        title_font, _ = _shrink_to_fit(
            draw, overall_title, "Black", font_dir,
            max_width=CANVAS_W - 2 * header_x, start_size=88,
        )
        draw.text((header_x, header_y), overall_title, font=title_font, fill=WHITE)

    sub_font = _load_font(font_dir, "Regular", 34)
    if subtitle:
        draw.text((header_x, header_y + 110), subtitle, font=sub_font, fill=GREY)

    # ---- Cards row ----
    outer_pad = 100
    gap = 60
    row_top = 350
    row_bottom = CANVAS_H - 120
    card_h = row_bottom - row_top
    card_w = (CANVAS_W - 2 * outer_pad - 2 * gap) // 3

    for i in range(3):
        cx = outer_pad + i * (card_w + gap)
        _render_card_box(draw, cx, row_top, card_w, card_h, i, cards[i], font_dir)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def _render_card_box(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    index: int,
    card: dict[str, Any],
    font_dir: Path,
) -> None:
    # Thin white border
    border = 3
    for i in range(border):
        draw.rectangle([x + i, y + i, x + w - 1 - i, y + h - 1 - i], outline=WHITE)

    pad = 60
    inner_x = x + pad
    inner_w = w - 2 * pad
    cursor_y = y + pad

    # Big number (01, 02, 03)
    num = f"{index + 1:02d}"
    num_font = _load_font(font_dir, "Black", 120)
    draw.text((inner_x, cursor_y), num, font=num_font, fill=WHITE)
    cursor_y += 140

    # Thin white rule under number
    rule_w = 90
    draw.rectangle([inner_x, cursor_y, inner_x + rule_w, cursor_y + 3], fill=WHITE)
    cursor_y += 40

    # Title (short, 1-3 words, CAPS, shrink to fit)
    title = (card.get("title") or "").upper().strip()
    if title:
        tfont, tsize = _shrink_to_fit(
            draw, title, "Black", font_dir,
            max_width=inner_w, start_size=64,
        )
        draw.text((inner_x, cursor_y), title, font=tfont, fill=WHITE)
        cursor_y += tsize + 30

    # Description (2-3 short phrases, CAPS, word-wrapped, grey)
    desc = (card.get("description") or "").upper().strip()
    if desc:
        dfont = _load_font(font_dir, "Regular", 34)
        lines = _wrap(desc, dfont, draw, inner_w)
        for line in lines[:5]:
            draw.text((inner_x, cursor_y), line, font=dfont, fill=GREY)
            cursor_y += 44


# ---- Legacy fallback ------------------------------------------------------

def _render_bordered_list(spec: dict[str, Any], font_dir: Path, out_path: Path) -> Path:
    """Old single-column layout, kept so pre-refactor plans keep rendering."""
    title = (spec.get("title") or spec.get("overall_title") or "").upper().strip()
    items = [str(x).upper() for x in (spec.get("items") or []) if str(x).strip()][:5]

    W, H = 780, 900
    margin = 56
    img = Image.new("RGBA", (W, H), BLACK_TRANSLUCENT)
    draw = ImageDraw.Draw(img)
    for i in range(4):
        draw.rectangle([i, i, W - 1 - i, H - 1 - i], outline=WHITE)

    title_font, tsize = _shrink_to_fit(
        draw, title, "Black", font_dir,
        max_width=W - 2 * margin, start_size=54,
    )
    y = margin + 8
    draw.text((margin, y), title, font=title_font, fill=WHITE)
    y += tsize + 20
    draw.rectangle([margin, y, W - margin, y + 2], fill=WHITE)
    y += 40

    num_font = _load_font(font_dir, "Black", 96)
    text_font = _load_font(font_dir, "Regular", 42)
    for i, item in enumerate(items, start=1):
        draw.text((margin, y), f"{i:02d}", font=num_font, fill=WHITE)
        text_x = margin + 140
        text_y = y + 24
        lines = _wrap(item, text_font, draw, W - text_x - margin)
        for line in lines:
            draw.text((text_x, text_y), line, font=text_font, fill=GREY)
            text_y += 46
        y += 130

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


# Back-compat alias so existing render_job.py keeps working during rollout.
def render_card(title: str, items: list, font_dir: Path, out_path: Path) -> Path:
    return _render_bordered_list(
        {"title": title, "items": items}, font_dir, out_path,
    )
