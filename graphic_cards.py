"""GODTIER infographic renderer — 7 template variants (Larry-approved).

All templates are rendered on a 1920x1080 black canvas, white text on black,
with the Larry-approved "clean, professional, reusable" aesthetic.

ANIMATION SUPPORT: Each template can render multiple frames for staggered
element reveal. render_infographic() returns a list of PNG paths:
  frame 0: header only (background)
  frame 1: header + first element
  frame 2: header + first two elements
  frame 3: header + all three elements (full state)

Templates dispatched by spec.template:
  1. three-cards      — 3 bordered cards side-by-side (Larry's approved default)
  2. three-columns    — 3 columns with circle ICON placeholder + vertical dividers
  3. timeline         — 3 numbered CIRCLES connected by horizontal line
  4. numbered-list    — vertical 01/02/03 rows with divider bars + separators
  5. circle-diagram   — 3-segment ring + numbered legend with dividers
  6. problem-solution — problem / cause / solution with large icon boxes
  7. checklist        — 3 rows with PLUS icons in bordered squares + separators

Card count is fixed at 3 across all templates (Rule of 3 — Larry-locked).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# ---- Canvas + palette -----------------------------------------------------
CANVAS_W = 1920
CANVAS_H = 1080

WHITE = (255, 255, 255, 255)
GREY = (170, 170, 170, 255)
BLACK_OPAQUE = (0, 0, 0, 255)
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


def _new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), BLACK_OPAQUE)
    return img, ImageDraw.Draw(img)


def _centered_text(draw, text, font, cx, y, fill=WHITE):
    """Draw text centered horizontally at cx."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, y), text, font=font, fill=fill)


def _header(draw, font_dir, title: str, subtitle: str, x: int = 80, y: int = 50) -> int:
    """Draw overall title + subtitle in top-left. Returns the y just below."""
    title = title.upper().strip()
    subtitle = subtitle.upper().strip()
    if title:
        tfont, _ = _shrink_to_fit(
            draw, title, "Black", font_dir,
            max_width=CANVAS_W - 2 * x, start_size=52,
        )
        draw.text((x, y), title, font=tfont, fill=WHITE)
    if subtitle:
        sfont = _load_font(font_dir, "Regular", 28)
        draw.text((x, y + 60), subtitle, font=sfont, fill=GREY)
    return y + (110 if subtitle else 80)


def _normalize_cards(spec: dict, n: int = 3) -> list[dict]:
    """Coerce spec into exactly `n` cards (Larry-locked Rule of 3)."""
    raw = list(spec.get("cards") or [])
    if not raw and spec.get("items"):
        raw = [{"title": "", "description": str(x).upper()} for x in spec["items"][:n]]
    kept = [c for c in raw if c.get("title") or c.get("description")]
    return (kept + [{}, {}, {}])[:n]


def _save(img: Image.Image, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")


# ---- Public dispatch ------------------------------------------------------

def render_infographic(spec: dict[str, Any], font_dir: Path, out_path: Path) -> list[Path]:
    """Render infographic frames. Returns list of PNG paths (4 frames for animation).

    Frame 0: header/background only
    Frame 1: header + element 1
    Frame 2: header + elements 1-2
    Frame 3: header + all 3 elements (full state)
    """
    template = (spec.get("template") or "three-cards").lower().replace("_", "-")
    renderer = _TEMPLATES.get(template, _render_three_cards)
    return renderer(spec, font_dir, out_path)


def render_infographic_single(spec: dict[str, Any], font_dir: Path, out_path: Path) -> Path:
    """Back-compat: render a single full-state PNG. Returns one path."""
    frames = render_infographic(spec, font_dir, out_path)
    return frames[-1]  # last frame is the full state


# ---- Template 1: three-cards (Larry-approved default) ---------------------

def _render_three_cards(spec: dict, font_dir: Path, out_path: Path) -> list[Path]:
    cards = _normalize_cards(spec, 3)
    frames = []

    for visible_count in range(4):  # 0=header only, 1-3=progressive reveal
        img, draw = _new_canvas()
        body_y = _header(draw, font_dir,
                         spec.get("overall_title") or spec.get("title") or "",
                         spec.get("subtitle") or "")

        outer_pad = 80
        gap = 40
        row_top = max(body_y + 40, 160)
        row_bottom = CANVAS_H - 60
        card_h = row_bottom - row_top
        card_w = (CANVAS_W - 2 * outer_pad - 2 * gap) // 3

        for i in range(min(visible_count, 3)):
            cx = outer_pad + i * (card_w + gap)
            _card_box(draw, cx, row_top, card_w, card_h, i, cards[i], font_dir)

        stem = out_path.stem
        suffix = out_path.suffix
        frame_path = out_path.parent / f"{stem}_f{visible_count}{suffix}"
        _save(img, frame_path)
        frames.append(frame_path)

    return frames


def _card_box(draw, x, y, w, h, index, card, font_dir):
    """Approved design: bordered card with centered 01/02/03, divider line,
    title, and multi-line description. All text centered."""
    # Card border (3px white)
    for i in range(3):
        draw.rectangle([x + i, y + i, x + w - 1 - i, y + h - 1 - i], outline=WHITE)

    cx = x + w // 2  # center x of card
    pad_top = 40

    # Large number (01, 02, 03) — centered
    num_font = _load_font(font_dir, "Black", 120)
    num = f"{index + 1:02d}"
    _centered_text(draw, num, num_font, cx, y + pad_top)

    # Short divider line under number — centered
    div_y = y + pad_top + 140
    div_w = 80
    draw.rectangle([cx - div_w // 2, div_y, cx + div_w // 2, div_y + 3], fill=WHITE)

    # Title — centered, bold
    title = (card.get("title") or "").upper().strip()
    title_y = div_y + 40
    if title:
        tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir, w - 60, 48)
        _centered_text(draw, title, tfont, cx, title_y)
        desc_y = title_y + tsize + 25
    else:
        desc_y = title_y + 60

    # Description — centered, regular weight, grey
    desc = (card.get("description") or "").upper().strip()
    if desc:
        dfont = _load_font(font_dir, "Regular", 30)
        for line in _wrap(desc, dfont, draw, w - 80)[:4]:
            _centered_text(draw, line, dfont, cx, desc_y, fill=GREY)
            desc_y += 42


# ---- Template 2: three-columns (circle ICON + vertical dividers) ----------

def _render_three_columns(spec: dict, font_dir: Path, out_path: Path) -> list[Path]:
    cards = _normalize_cards(spec, 3)
    frames = []

    for visible_count in range(4):
        img, draw = _new_canvas()
        body_y = _header(draw, font_dir,
                         spec.get("overall_title") or spec.get("title") or "",
                         spec.get("subtitle") or "")

        col_w = CANVAS_W // 3
        content_top = max(body_y + 40, 160)

        for i in range(min(visible_count, 3)):
            cx = i * col_w + col_w // 2

            # Vertical divider line between columns (left side of col 1 and 2)
            if i > 0:
                div_x = i * col_w
                draw.line([(div_x, content_top), (div_x, CANVAS_H - 80)],
                          fill=(100, 100, 100, 255), width=1)

            # Circle with "ICON" text placeholder
            circle_r = 55
            circle_y = content_top + 60
            for w in range(2):
                draw.ellipse([cx - circle_r + w, circle_y - circle_r + w,
                              cx + circle_r - w, circle_y + circle_r - w],
                             outline=WHITE)
            icon_font = _load_font(font_dir, "Regular", 24)
            _centered_text(draw, "ICON", icon_font, cx, circle_y - 12)

            # Number below circle
            num_y = circle_y + circle_r + 30
            num_font = _load_font(font_dir, "Black", 90)
            num = f"{i+1:02d}"
            _centered_text(draw, num, num_font, cx, num_y)

            # Short divider line under number
            div_y = num_y + 100
            div_w = 70
            draw.rectangle([cx - div_w // 2, div_y, cx + div_w // 2, div_y + 3], fill=WHITE)

            # Title — centered, bold
            title = (cards[i].get("title") or "").upper().strip()
            title_y = div_y + 35
            if title:
                tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir, col_w - 80, 44)
                _centered_text(draw, title, tfont, cx, title_y)
                desc_y = title_y + tsize + 20
            else:
                desc_y = title_y + 50

            # Description — centered, regular, grey
            desc = (cards[i].get("description") or "").upper().strip()
            if desc:
                dfont = _load_font(font_dir, "Regular", 28)
                for line in _wrap(desc, dfont, draw, col_w - 80)[:4]:
                    _centered_text(draw, line, dfont, cx, desc_y, fill=GREY)
                    desc_y += 38

        stem = out_path.stem
        suffix = out_path.suffix
        frame_path = out_path.parent / f"{stem}_f{visible_count}{suffix}"
        _save(img, frame_path)
        frames.append(frame_path)

    return frames


# ---- Template 3: timeline (CIRCLES connected by horizontal line) ----------

def _render_timeline(spec: dict, font_dir: Path, out_path: Path) -> list[Path]:
    cards = _normalize_cards(spec, 3)
    frames = []

    circle_r = 70
    center_y = 420
    x_positions = [CANVAS_W // 4, CANVAS_W // 2, 3 * CANVAS_W // 4]

    for visible_count in range(4):
        img, draw = _new_canvas()
        body_y = _header(draw, font_dir,
                         spec.get("overall_title") or spec.get("title") or "",
                         spec.get("subtitle") or "")

        # Draw connecting horizontal line (full width between first and last circle)
        if visible_count >= 2:
            line_y = center_y
            line_x1 = x_positions[0]
            line_x2 = x_positions[min(visible_count - 1, 2)]
            draw.line([(line_x1, line_y), (line_x2, line_y)],
                      fill=WHITE, width=3)

        for i in range(min(visible_count, 3)):
            cx = x_positions[i]

            # Circle outline (white, 2-3px)
            for w in range(3):
                draw.ellipse([cx - circle_r + w, center_y - circle_r + w,
                              cx + circle_r - w, center_y + circle_r - w],
                             outline=WHITE)

            # Number inside circle
            num_font = _load_font(font_dir, "Black", 70)
            num = f"{i+1:02d}"
            _centered_text(draw, num, num_font, cx, center_y - 30)

            # Title below circle — centered, bold
            title = (cards[i].get("title") or "").upper().strip()
            title_y = center_y + circle_r + 40
            if title:
                tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir,
                                              CANVAS_W // 4 - 40, 44)
                _centered_text(draw, title, tfont, cx, title_y)
                desc_y = title_y + tsize + 15
            else:
                desc_y = title_y + 50

            # Description — centered, regular, grey
            desc = (cards[i].get("description") or "").upper().strip()
            if desc:
                dfont = _load_font(font_dir, "Regular", 28)
                for line in _wrap(desc, dfont, draw, CANVAS_W // 4 - 40)[:3]:
                    _centered_text(draw, line, dfont, cx, desc_y, fill=GREY)
                    desc_y += 38

        stem = out_path.stem
        suffix = out_path.suffix
        frame_path = out_path.parent / f"{stem}_f{visible_count}{suffix}"
        _save(img, frame_path)
        frames.append(frame_path)

    return frames


# ---- Template 4: numbered-list (divider bars + horizontal separators) -----

def _render_numbered_list(spec: dict, font_dir: Path, out_path: Path) -> list[Path]:
    cards = _normalize_cards(spec, 3)
    frames = []

    for visible_count in range(4):
        img, draw = _new_canvas()
        body_y = _header(draw, font_dir,
                         spec.get("overall_title") or spec.get("title") or "",
                         spec.get("subtitle") or "")

        left_margin = 100
        row_top = max(body_y + 60, 200)
        row_h = (CANVAS_H - row_top - 80) // 3

        for i in range(min(visible_count, 3)):
            y = row_top + i * row_h

            # Large number (01, 02, 03)
            num_font = _load_font(font_dir, "Black", 100)
            num = f"{i+1:02d}"
            draw.text((left_margin, y + 10), num, font=num_font, fill=WHITE)

            # Vertical divider bar between number and text
            divider_x = left_margin + 180
            draw.line([(divider_x, y + 10), (divider_x, y + row_h - 40)],
                      fill=WHITE, width=2)

            # Title — bold
            text_x = divider_x + 30
            title = (cards[i].get("title") or "").upper().strip()
            text_y = y + 15
            if title:
                tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir,
                                              CANVAS_W - text_x - 100, 52)
                draw.text((text_x, text_y), title, font=tfont, fill=WHITE)
                text_y += tsize + 10

            # Description — regular, grey
            desc = (cards[i].get("description") or "").upper().strip()
            if desc:
                dfont = _load_font(font_dir, "Regular", 32)
                for line in _wrap(desc, dfont, draw, CANVAS_W - text_x - 100)[:2]:
                    draw.text((text_x, text_y), line, font=dfont, fill=GREY)
                    text_y += 42

            # Horizontal separator line below this row
            sep_y = y + row_h - 10
            draw.line([(left_margin, sep_y), (CANVAS_W - 80, sep_y)],
                      fill=WHITE, width=1)

        stem = out_path.stem
        suffix = out_path.suffix
        frame_path = out_path.parent / f"{stem}_f{visible_count}{suffix}"
        _save(img, frame_path)
        frames.append(frame_path)

    return frames


# ---- Template 5: circle-diagram (3-segment ring + legend) -----------------

def _render_circle_diagram(spec: dict, font_dir: Path, out_path: Path) -> list[Path]:
    cards = _normalize_cards(spec, 3)
    frames = []

    cx_ring = 380
    cy_ring = 520
    outer_r = 230
    inner_r = 60

    for visible_count in range(4):
        img, draw = _new_canvas()
        body_y = _header(draw, font_dir,
                         spec.get("overall_title") or spec.get("title") or "",
                         spec.get("subtitle") or "")

        # Always draw the full ring structure
        # Outer circle
        for w in range(3):
            draw.ellipse([cx_ring - outer_r + w, cy_ring - outer_r + w,
                          cx_ring + outer_r - w, cy_ring + outer_r - w], outline=WHITE)
        # Inner circle (center hole)
        for w in range(2):
            draw.ellipse([cx_ring - inner_r + w, cy_ring - inner_r + w,
                          cx_ring + inner_r - w, cy_ring + inner_r - w], outline=WHITE)

        # 3 segment dividers (120 degrees apart starting at top)
        for k in range(3):
            angle = -math.pi / 2 + k * (2 * math.pi / 3)
            x1 = cx_ring + inner_r * math.cos(angle)
            y1 = cy_ring + inner_r * math.sin(angle)
            x2 = cx_ring + outer_r * math.cos(angle)
            y2 = cy_ring + outer_r * math.sin(angle)
            draw.line([(x1, y1), (x2, y2)], fill=WHITE, width=2)

        # Numbers in each wedge
        num_font = _load_font(font_dir, "Black", 60)
        wedge_r = (inner_r + outer_r) / 2
        for k in range(3):
            angle = -math.pi / 2 + k * (2 * math.pi / 3) + math.pi / 3
            nx = cx_ring + wedge_r * math.cos(angle)
            ny = cy_ring + wedge_r * math.sin(angle)
            num = f"{k+1:02d}"
            _centered_text(draw, num, num_font, int(nx), int(ny) - 20)

        # Right side: numbered legend with divider bars + horizontal separators
        legend_x = cx_ring + outer_r + 100
        legend_top = 250
        legend_row_h = (CANVAS_H - legend_top - 100) // 3

        for i in range(min(visible_count, 3)):
            ly = legend_top + i * legend_row_h

            # Number
            num_l = _load_font(font_dir, "Black", 70)
            num = f"{i+1:02d}"
            draw.text((legend_x, ly + 5), num, font=num_l, fill=WHITE)

            # Vertical divider bar
            div_x = legend_x + 110
            draw.line([(div_x, ly + 5), (div_x, ly + legend_row_h - 30)],
                      fill=WHITE, width=2)

            # Title
            text_x = div_x + 25
            title = (cards[i].get("title") or "").upper().strip()
            text_y = ly + 10
            if title:
                tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir,
                                              CANVAS_W - text_x - 80, 44)
                draw.text((text_x, text_y), title, font=tfont, fill=WHITE)
                text_y += tsize + 8

            # Description
            desc = (cards[i].get("description") or "").upper().strip()
            if desc:
                dfont = _load_font(font_dir, "Regular", 28)
                for line in _wrap(desc, dfont, draw, CANVAS_W - text_x - 80)[:2]:
                    draw.text((text_x, text_y), line, font=dfont, fill=GREY)
                    text_y += 36

            # Horizontal separator
            sep_y = ly + legend_row_h - 10
            draw.line([(legend_x, sep_y), (CANVAS_W - 80, sep_y)],
                      fill=WHITE, width=1)

        stem = out_path.stem
        suffix = out_path.suffix
        frame_path = out_path.parent / f"{stem}_f{visible_count}{suffix}"
        _save(img, frame_path)
        frames.append(frame_path)

    return frames


# ---- Template 6: problem / cause / solution (large icon boxes) ------------

def _render_problem_solution(spec: dict, font_dir: Path, out_path: Path) -> list[Path]:
    cards = _normalize_cards(spec, 3)
    labels = ["PROBLEM", "CAUSE", "SOLUTION"]
    frames = []

    for visible_count in range(4):
        img, draw = _new_canvas()
        body_y = _header(draw, font_dir,
                         spec.get("overall_title") or spec.get("title") or "",
                         spec.get("subtitle") or "")

        slot_w = CANVAS_W // 3
        box_w = 340
        box_h = 290
        top = max(body_y + 60, 200)

        for i in range(min(visible_count, 3)):
            cx = slot_w * i + slot_w // 2
            x0 = cx - box_w // 2
            y0 = top
            x1 = cx + box_w // 2
            y1 = top + box_h

            # Box border (white, 2px)
            for w in range(2):
                draw.rectangle([x0 + w, y0 + w, x1 - w, y1 - w], outline=WHITE)

            # Large icon inside box
            icon_cx = cx
            icon_cy = y0 + box_h // 2
            if i == 0:
                _draw_warning_triangle(draw, icon_cx, icon_cy, 100)
            elif i == 1:
                _draw_magnifying_glass(draw, icon_cx, icon_cy, 100)
            else:
                _draw_badge_check(draw, icon_cx, icon_cy, 100)

            # Label below box (PROBLEM / CAUSE / SOLUTION) — bold, centered
            label_font = _load_font(font_dir, "Black", 40)
            label_y = y1 + 30
            _centered_text(draw, labels[i], label_font, cx, label_y)

            # Description — centered, regular, grey
            desc = (cards[i].get("description") or "").upper().strip()
            if not desc:
                desc = (cards[i].get("title") or "").upper().strip()
            if desc:
                dfont = _load_font(font_dir, "Regular", 26)
                desc_y = label_y + 55
                for line in _wrap(desc, dfont, draw, slot_w - 60)[:3]:
                    _centered_text(draw, line, dfont, cx, desc_y, fill=GREY)
                    desc_y += 36

        stem = out_path.stem
        suffix = out_path.suffix
        frame_path = out_path.parent / f"{stem}_f{visible_count}{suffix}"
        _save(img, frame_path)
        frames.append(frame_path)

    return frames


# ---- Template 7: checklist (PLUS icons + separators) ----------------------

def _render_checklist(spec: dict, font_dir: Path, out_path: Path) -> list[Path]:
    cards = _normalize_cards(spec, 3)
    frames = []

    for visible_count in range(4):
        img, draw = _new_canvas()
        body_y = _header(draw, font_dir,
                         spec.get("overall_title") or spec.get("title") or "",
                         spec.get("subtitle") or "")

        left_margin = 80
        row_top = max(body_y + 40, 160)
        row_h = (CANVAS_H - row_top - 60) // 3
        icon_size = 90

        for i in range(min(visible_count, 3)):
            y = row_top + i * row_h
            icon_y = y + (row_h - icon_size) // 2 - 20

            # Bordered square with PLUS sign
            for w in range(2):
                draw.rectangle([left_margin + w, icon_y + w,
                                left_margin + icon_size - w, icon_y + icon_size - w],
                               outline=WHITE)
            # Plus sign inside
            plus_cx = left_margin + icon_size // 2
            plus_cy = icon_y + icon_size // 2
            plus_arm = 22
            draw.line([(plus_cx - plus_arm, plus_cy), (plus_cx + plus_arm, plus_cy)],
                      fill=WHITE, width=3)
            draw.line([(plus_cx, plus_cy - plus_arm), (plus_cx, plus_cy + plus_arm)],
                      fill=WHITE, width=3)

            # Title — large bold
            text_x = left_margin + icon_size + 40
            title = (cards[i].get("title") or "").upper().strip()
            text_y = icon_y + 5
            if title:
                tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir,
                                              CANVAS_W - text_x - 80, 56)
                draw.text((text_x, text_y), title, font=tfont, fill=WHITE)
                text_y += tsize + 10

            # Description — regular, grey
            desc = (cards[i].get("description") or "").upper().strip()
            if desc:
                dfont = _load_font(font_dir, "Regular", 30)
                for line in _wrap(desc, dfont, draw, CANVAS_W - text_x - 80)[:2]:
                    draw.text((text_x, text_y), line, font=dfont, fill=GREY)
                    text_y += 40

            # Horizontal separator line below this row
            sep_y = y + row_h - 5
            draw.line([(left_margin, sep_y), (CANVAS_W - 80, sep_y)],
                      fill=WHITE, width=1)

        stem = out_path.stem
        suffix = out_path.suffix
        frame_path = out_path.parent / f"{stem}_f{visible_count}{suffix}"
        _save(img, frame_path)
        frames.append(frame_path)

    return frames


# ---- Icon drawing functions (Larry-approved large detailed icons) ----------

def _draw_warning_triangle(draw, cx, cy, size):
    """Large warning triangle with exclamation mark — for PROBLEM."""
    r = size
    # Triangle points
    top = (cx, cy - r)
    bl = (cx - r, cy + r * 0.7)
    br = (cx + r, cy + r * 0.7)
    # Outer triangle (stroke)
    draw.line([top, bl], fill=WHITE, width=4)
    draw.line([bl, br], fill=WHITE, width=4)
    draw.line([br, top], fill=WHITE, width=4)
    # Exclamation mark
    exc_top = cy - r * 0.35
    exc_bot = cy + r * 0.15
    draw.line([(cx, exc_top), (cx, exc_bot)], fill=WHITE, width=5)
    # Dot
    dot_y = cy + r * 0.35
    dot_r = 5
    draw.ellipse([cx - dot_r, dot_y - dot_r, cx + dot_r, dot_y + dot_r], fill=WHITE)


def _draw_magnifying_glass(draw, cx, cy, size):
    """Large magnifying glass — for CAUSE."""
    r = size
    glass_r = int(r * 0.6)
    # Offset glass slightly up-left
    gcx = cx - r * 0.1
    gcy = cy - r * 0.15
    # Glass circle
    for w in range(4):
        draw.ellipse([gcx - glass_r + w, gcy - glass_r + w,
                      gcx + glass_r - w, gcy + glass_r - w], outline=WHITE)
    # Handle going down-right
    handle_start_x = gcx + glass_r * 0.65
    handle_start_y = gcy + glass_r * 0.65
    handle_end_x = cx + r * 0.7
    handle_end_y = cy + r * 0.7
    draw.line([(handle_start_x, handle_start_y), (handle_end_x, handle_end_y)],
              fill=WHITE, width=8)


def _draw_badge_check(draw, cx, cy, size):
    """Badge/seal with checkmark — for SOLUTION."""
    r = size
    # Draw a starburst/badge shape (8-pointed)
    points = []
    num_pts = 16
    for k in range(num_pts):
        angle = -math.pi / 2 + k * (2 * math.pi / num_pts)
        # Alternate between outer and inner radius for starburst
        radius = r * 0.95 if k % 2 == 0 else r * 0.75
        px = cx + radius * math.cos(angle)
        py = cy + radius * math.sin(angle)
        points.append((px, py))
    # Draw outline
    for k in range(len(points)):
        p1 = points[k]
        p2 = points[(k + 1) % len(points)]
        draw.line([p1, p2], fill=WHITE, width=3)
    # Checkmark inside
    check_size = r * 0.4
    draw.line([(cx - check_size, cy),
               (cx - check_size * 0.15, cy + check_size * 0.6)],
              fill=WHITE, width=5)
    draw.line([(cx - check_size * 0.15, cy + check_size * 0.6),
               (cx + check_size, cy - check_size * 0.4)],
              fill=WHITE, width=5)


_TEMPLATES = {
    "three-cards":       _render_three_cards,
    "three-columns":     _render_three_columns,
    "timeline":          _render_timeline,
    "numbered-list":     _render_numbered_list,
    "circle-diagram":    _render_circle_diagram,
    "problem-solution":  _render_problem_solution,
    "checklist":         _render_checklist,
    # aliases
    "process":           _render_timeline,
    "columns":           _render_three_columns,
    "bordered-list":     _render_numbered_list,
    "problem-cause-solution": _render_problem_solution,
}


# Back-compat alias so nothing else breaks during rollout.
def render_card(title: str, items: list, font_dir: Path, out_path: Path) -> Path:
    return render_infographic_single(
        {"overall_title": title, "cards": [{"title": "", "description": str(x)} for x in items]},
        font_dir, out_path,
    )
