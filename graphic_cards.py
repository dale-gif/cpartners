"""GODTIER infographic renderer — 7 template variants.

All templates are rendered on a 1920x1080 black canvas, all-CAPS, white/grey
on black, with the Larry-approved "clean, professional, reusable" aesthetic.

Templates dispatched by spec.template:
  1. three-cards      — 3 bordered cards side-by-side (Larry's approved default)
  2. three-columns    — 3 columns with a simple icon on top, no boxes
  3. timeline         — 3 numbered SQUARES side-by-side (no connecting line)
  4. numbered-list    — vertical numbered list (01/02/03 rows)
  5. circle-diagram   — 3-segment ring with labels around it
  6. problem-solution — problem → cause → solution flow with icon boxes
  7. checklist        — 3 checkbox rows

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


def _header(draw, font_dir, title: str, subtitle: str, x: int = 100, y: int = 100) -> int:
    """Draw overall title + subtitle. Returns the y just below the header."""
    title = title.upper().strip()
    subtitle = subtitle.upper().strip()
    if title:
        tfont, _ = _shrink_to_fit(
            draw, title, "Black", font_dir,
            max_width=CANVAS_W - 2 * x, start_size=110,
        )
        draw.text((x, y), title, font=tfont, fill=WHITE)
    if subtitle:
        sfont = _load_font(font_dir, "Regular", 40)
        draw.text((x, y + 130), subtitle, font=sfont, fill=GREY)
    return y + (240 if subtitle else 180)


def _normalize_cards(spec: dict, n: int = 3) -> list[dict]:
    """Coerce spec into exactly `n` cards (Larry-locked Rule of 3)."""
    raw = list(spec.get("cards") or [])
    if not raw and spec.get("items"):
        raw = [{"title": "", "description": str(x).upper()} for x in spec["items"][:n]]
    kept = [c for c in raw if c.get("title") or c.get("description")]
    return (kept + [{}, {}, {}])[:n]


# ---- Public dispatch ------------------------------------------------------

def render_infographic(spec: dict[str, Any], font_dir: Path, out_path: Path) -> Path:
    template = (spec.get("template") or "three-cards").lower().replace("_", "-")
    renderer = _TEMPLATES.get(template, _render_three_cards)
    return renderer(spec, font_dir, out_path)


# ---- Template 1: three-cards (Larry-approved default) ---------------------

def _render_three_cards(spec: dict, font_dir: Path, out_path: Path) -> Path:
    img, draw = _new_canvas()
    body_y = _header(draw, font_dir,
                     spec.get("overall_title") or spec.get("title") or "",
                     spec.get("subtitle") or "")

    cards = _normalize_cards(spec, 3)
    outer_pad = 100
    gap = 60
    row_top = max(body_y + 20, 380)
    row_bottom = CANVAS_H - 120
    card_h = row_bottom - row_top
    card_w = (CANVAS_W - 2 * outer_pad - 2 * gap) // 3

    for i in range(3):
        cx = outer_pad + i * (card_w + gap)
        _card_box(draw, cx, row_top, card_w, card_h, i, cards[i], font_dir)

    _save(img, out_path)
    return out_path


def _card_box(draw, x, y, w, h, index, card, font_dir):
    for i in range(3):
        draw.rectangle([x + i, y + i, x + w - 1 - i, y + h - 1 - i], outline=WHITE)
    pad = 60
    ix, iw = x + pad, w - 2 * pad
    cy = y + pad
    num_font = _load_font(font_dir, "Black", 140)
    draw.text((ix, cy), f"{index + 1:02d}", font=num_font, fill=WHITE)
    cy += 160
    draw.rectangle([ix, cy, ix + 100, cy + 4], fill=WHITE)
    cy += 50
    title = (card.get("title") or "").upper().strip()
    if title:
        tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir, iw, 70)
        draw.text((ix, cy), title, font=tfont, fill=WHITE)
        cy += tsize + 30
    desc = (card.get("description") or "").upper().strip()
    if desc:
        dfont = _load_font(font_dir, "Regular", 38)
        for line in _wrap(desc, dfont, draw, iw)[:5]:
            draw.text((ix, cy), line, font=dfont, fill=GREY)
            cy += 50


# ---- Template 2: three-columns (icons on top, no boxes) -------------------

def _render_three_columns(spec: dict, font_dir: Path, out_path: Path) -> Path:
    img, draw = _new_canvas()
    body_y = _header(draw, font_dir,
                     spec.get("overall_title") or spec.get("title") or "",
                     spec.get("subtitle") or "")
    cards = _normalize_cards(spec, 3)
    col_w = CANVAS_W // 3
    icons = ["check", "target", "chart"]  # simple geometric icons
    for i in range(3):
        cx = i * col_w + col_w // 2  # center of column
        top = max(body_y + 60, 420)
        _draw_icon(draw, icons[i], cx, top, 90, WHITE)
        # Number
        num_font = _load_font(font_dir, "Black", 100)
        num = f"{i+1:02d}"
        nw = draw.textbbox((0, 0), num, font=num_font)[2]
        draw.text((cx - nw // 2, top + 130), num, font=num_font, fill=WHITE)
        # Title
        title = (cards[i].get("title") or "").upper().strip()
        if title:
            tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir, col_w - 80, 66)
            tw = draw.textbbox((0, 0), title, font=tfont)[2]
            draw.text((cx - tw // 2, top + 270), title, font=tfont, fill=WHITE)
        # Desc
        desc = (cards[i].get("description") or "").upper().strip()
        if desc:
            dfont = _load_font(font_dir, "Regular", 34)
            y = top + 360
            for line in _wrap(desc, dfont, draw, col_w - 80)[:4]:
                lw = draw.textbbox((0, 0), line, font=dfont)[2]
                draw.text((cx - lw // 2, y), line, font=dfont, fill=GREY)
                y += 46
    _save(img, out_path)
    return out_path


# ---- Template 3: timeline (3 numbered SQUARES, no connecting line) --------

def _render_timeline(spec: dict, font_dir: Path, out_path: Path) -> Path:
    img, draw = _new_canvas()
    body_y = _header(draw, font_dir,
                     spec.get("overall_title") or spec.get("title") or "",
                     spec.get("subtitle") or "")
    cards = _normalize_cards(spec, 3)
    center_y = max(body_y + 120, 500)
    half = 80  # square half-size (160x160 outer)
    x_positions = [CANVAS_W // 4, CANVAS_W // 2, 3 * CANVAS_W // 4]
    for i, cx in enumerate(x_positions):
        # White square outline (4px stroke)
        for w in range(4):
            draw.rectangle(
                [cx - half + w, center_y - half + w,
                 cx + half - w, center_y + half - w],
                outline=WHITE,
            )
        # Number inside the square
        num_font = _load_font(font_dir, "Black", 70)
        num = f"{i+1:02d}"
        nb = draw.textbbox((0, 0), num, font=num_font)
        draw.text((cx - (nb[2] - nb[0]) // 2, center_y - (nb[3] - nb[1]) // 2 - 5),
                  num, font=num_font, fill=WHITE)
        # Title under square
        title = (cards[i].get("title") or "").upper().strip()
        if title:
            tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir, CANVAS_W // 4, 60)
            tw = draw.textbbox((0, 0), title, font=tfont)[2]
            draw.text((cx - tw // 2, center_y + half + 40), title, font=tfont, fill=WHITE)
        # Desc under title
        desc = (cards[i].get("description") or "").upper().strip()
        if desc:
            dfont = _load_font(font_dir, "Regular", 32)
            y = center_y + half + 130
            for line in _wrap(desc, dfont, draw, CANVAS_W // 4 - 40)[:3]:
                lw = draw.textbbox((0, 0), line, font=dfont)[2]
                draw.text((cx - lw // 2, y), line, font=dfont, fill=GREY)
                y += 42
    _save(img, out_path)
    return out_path


# ---- Template 4: numbered-list (vertical) ---------------------------------

def _render_numbered_list(spec: dict, font_dir: Path, out_path: Path) -> Path:
    img, draw = _new_canvas()
    body_y = _header(draw, font_dir,
                     spec.get("overall_title") or spec.get("title") or "",
                     spec.get("subtitle") or "")
    cards = _normalize_cards(spec, 3)
    x = 200
    row_top = max(body_y + 40, 380)
    row_h = (CANVAS_H - row_top - 120) // 3
    num_font = _load_font(font_dir, "Black", 130)
    for i, card in enumerate(cards):
        y = row_top + i * row_h
        num = f"{i+1:02d}"
        draw.text((x, y), num, font=num_font, fill=WHITE)
        text_x = x + 250
        text_y = y + 20
        title = (card.get("title") or "").upper().strip()
        if title:
            tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir,
                                          CANVAS_W - text_x - 100, 70)
            draw.text((text_x, text_y), title, font=tfont, fill=WHITE)
            text_y += tsize + 15
        desc = (card.get("description") or "").upper().strip()
        if desc:
            dfont = _load_font(font_dir, "Regular", 36)
            for line in _wrap(desc, dfont, draw, CANVAS_W - text_x - 100)[:2]:
                draw.text((text_x, text_y), line, font=dfont, fill=GREY)
                text_y += 48
    _save(img, out_path)
    return out_path


# ---- Template 5: circle-diagram (3-segment ring) --------------------------

def _render_circle_diagram(spec: dict, font_dir: Path, out_path: Path) -> Path:
    img, draw = _new_canvas()
    body_y = _header(draw, font_dir,
                     spec.get("overall_title") or spec.get("title") or "",
                     spec.get("subtitle") or "")
    cards = _normalize_cards(spec, 3)
    cx_ring = 500
    cy_ring = max(body_y + 320, 700)
    outer_r = 260
    inner_r = 90
    # Ring outline
    for w in range(3):
        draw.ellipse([cx_ring - outer_r + w, cy_ring - outer_r + w,
                      cx_ring + outer_r - w, cy_ring + outer_r - w], outline=WHITE)
    # 3 segment dividers (120° apart starting at top)
    for k in range(3):
        angle = -math.pi / 2 + k * (2 * math.pi / 3)
        x1 = cx_ring + inner_r * math.cos(angle)
        y1 = cy_ring + inner_r * math.sin(angle)
        x2 = cx_ring + outer_r * math.cos(angle)
        y2 = cy_ring + outer_r * math.sin(angle)
        draw.line([(x1, y1), (x2, y2)], fill=WHITE, width=3)
    # Number in the center of each wedge
    num_font = _load_font(font_dir, "Black", 80)
    wedge_r = (inner_r + outer_r) / 2
    for i in range(3):
        angle = -math.pi / 2 + i * (2 * math.pi / 3) + math.pi / 3  # center of wedge
        nx = cx_ring + wedge_r * math.cos(angle)
        ny = cy_ring + wedge_r * math.sin(angle)
        num = f"{i+1:02d}"
        nb = draw.textbbox((0, 0), num, font=num_font)
        draw.text((nx - (nb[2] - nb[0]) // 2, ny - (nb[3] - nb[1]) // 2 - 5),
                  num, font=num_font, fill=WHITE)
    # Right side: numbered legend
    lx = cx_ring + outer_r + 120
    ly = cy_ring - outer_r
    num_l = _load_font(font_dir, "Black", 60)
    for i, card in enumerate(cards):
        num = f"{i+1:02d}"
        draw.text((lx, ly), num, font=num_l, fill=WHITE)
        title = (card.get("title") or "").upper().strip()
        if title:
            tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir,
                                          CANVAS_W - lx - 200, 54)
            draw.text((lx + 140, ly + 5), title, font=tfont, fill=WHITE)
            desc_y = ly + tsize + 20
        else:
            desc_y = ly + 70
        desc = (card.get("description") or "").upper().strip()
        if desc:
            dfont = _load_font(font_dir, "Regular", 32)
            for line in _wrap(desc, dfont, draw, CANVAS_W - lx - 260)[:2]:
                draw.text((lx + 140, desc_y), line, font=dfont, fill=GREY)
                desc_y += 42
        ly += 200
    _save(img, out_path)
    return out_path


# ---- Template 6: problem → cause → solution -------------------------------

def _render_problem_solution(spec: dict, font_dir: Path, out_path: Path) -> Path:
    img, draw = _new_canvas()
    body_y = _header(draw, font_dir,
                     spec.get("overall_title") or spec.get("title") or "",
                     spec.get("subtitle") or "")
    cards = _normalize_cards(spec, 3)
    labels = ["PROBLEM", "CAUSE", "SOLUTION"]
    icons = ["warning", "search", "check"]
    slot_w = CANVAS_W // 3
    box_size = 220
    top = max(body_y + 100, 440)
    for i in range(3):
        cx = slot_w * i + slot_w // 2
        # Box
        x0, y0 = cx - box_size // 2, top
        x1, y1 = cx + box_size // 2, top + box_size
        for w in range(3):
            draw.rectangle([x0 + w, y0 + w, x1 - w, y1 - w], outline=WHITE)
        # Icon in center
        _draw_icon(draw, icons[i], cx, y0 + box_size // 2, 90, WHITE)
        # Arrow between boxes
        if i < 2:
            arr_y = top + box_size // 2
            arr_x1 = x1 + 30
            arr_x2 = arr_x1 + slot_w - box_size - 60
            draw.line([(arr_x1, arr_y), (arr_x2, arr_y)], fill=WHITE, width=5)
            # Arrowhead
            draw.polygon([(arr_x2, arr_y - 15), (arr_x2, arr_y + 15),
                          (arr_x2 + 25, arr_y)], fill=WHITE)
        # Label under box
        lfont = _load_font(font_dir, "Black", 44)
        lw = draw.textbbox((0, 0), labels[i], font=lfont)[2]
        draw.text((cx - lw // 2, y1 + 40), labels[i], font=lfont, fill=WHITE)
        # Card content
        title = (cards[i].get("title") or "").upper().strip()
        desc = (cards[i].get("description") or "").upper().strip()
        content_y = y1 + 110
        if title:
            tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir, slot_w - 80, 44)
            tw = draw.textbbox((0, 0), title, font=tfont)[2]
            draw.text((cx - tw // 2, content_y), title, font=tfont, fill=WHITE)
            content_y += tsize + 20
        if desc:
            dfont = _load_font(font_dir, "Regular", 30)
            for line in _wrap(desc, dfont, draw, slot_w - 80)[:3]:
                lw = draw.textbbox((0, 0), line, font=dfont)[2]
                draw.text((cx - lw // 2, content_y), line, font=dfont, fill=GREY)
                content_y += 40
    _save(img, out_path)
    return out_path


# ---- Template 7: checklist ------------------------------------------------

def _render_checklist(spec: dict, font_dir: Path, out_path: Path) -> Path:
    img, draw = _new_canvas()
    body_y = _header(draw, font_dir,
                     spec.get("overall_title") or spec.get("title") or "",
                     spec.get("subtitle") or "")
    cards = _normalize_cards(spec, 3)
    x = 200
    row_top = max(body_y + 60, 400)
    row_h = (CANVAS_H - row_top - 120) // 3
    check_size = 100
    for i, card in enumerate(cards):
        y = row_top + i * row_h
        # Checkbox
        for w in range(4):
            draw.rectangle([x + w, y + w, x + check_size - w, y + check_size - w],
                           outline=WHITE)
        # Checkmark inside
        cm_pad = 22
        draw.line([(x + cm_pad, y + check_size // 2),
                   (x + check_size // 2 - 8, y + check_size - cm_pad)],
                  fill=WHITE, width=6)
        draw.line([(x + check_size // 2 - 8, y + check_size - cm_pad),
                   (x + check_size - cm_pad, y + cm_pad)],
                  fill=WHITE, width=6)
        # Title + desc to the right
        text_x = x + check_size + 70
        title = (card.get("title") or "").upper().strip()
        text_y = y + 5
        if title:
            tfont, tsize = _shrink_to_fit(draw, title, "Black", font_dir,
                                          CANVAS_W - text_x - 100, 60)
            draw.text((text_x, text_y), title, font=tfont, fill=WHITE)
            text_y += tsize + 15
        desc = (card.get("description") or "").upper().strip()
        if desc:
            dfont = _load_font(font_dir, "Regular", 34)
            for line in _wrap(desc, dfont, draw, CANVAS_W - text_x - 100)[:2]:
                draw.text((text_x, text_y), line, font=dfont, fill=GREY)
                text_y += 46
    _save(img, out_path)
    return out_path


# ---- Simple geometric icons (no external assets) --------------------------

def _draw_icon(draw, kind: str, cx: int, cy: int, size: int, color) -> None:
    r = size // 2
    if kind == "check":
        for w in range(4):
            draw.ellipse([cx - r + w, cy - r + w, cx + r - w, cy + r - w], outline=color)
        pad = r * 0.4
        draw.line([(cx - pad, cy), (cx - pad * 0.2, cy + pad * 0.7)], fill=color, width=6)
        draw.line([(cx - pad * 0.2, cy + pad * 0.7), (cx + pad, cy - pad * 0.5)],
                  fill=color, width=6)
    elif kind == "target":
        for w in range(4):
            draw.ellipse([cx - r + w, cy - r + w, cx + r - w, cy + r - w], outline=color)
        draw.ellipse([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], outline=color, width=3)
        draw.ellipse([cx - r // 6, cy - r // 6, cx + r // 6, cy + r // 6], fill=color)
    elif kind == "chart":
        # 3 rising bars
        bar_w = size // 6
        gap = size // 12
        base = cy + r
        for i, h in enumerate([r // 2, r, r * 1.4]):
            x0 = cx - r + i * (bar_w + gap * 2) + gap * 2
            draw.rectangle([x0, base - h, x0 + bar_w, base], fill=color)
    elif kind == "warning":
        # Triangle with exclamation
        pts = [(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)]
        for w in range(4):
            draw.polygon(pts, outline=color)
        draw.rectangle([cx - 5, cy - r // 4, cx + 5, cy + r // 3], fill=color)
        draw.ellipse([cx - 7, cy + r // 2, cx + 7, cy + r // 2 + 14], fill=color)
    elif kind == "search":
        # Magnifying glass
        gr = int(r * 0.7)
        for w in range(4):
            draw.ellipse([cx - gr - r // 4 + w, cy - gr + w,
                          cx + gr - r // 4 - w, cy + gr - w], outline=color)
        # Handle
        hx = cx + gr - r // 4
        hy = cy + gr - r // 4
        draw.line([(hx - 5, hy - 5), (cx + r, cy + r)], fill=color, width=8)


def _save(img: Image.Image, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")


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
    "bordered-list":     _render_numbered_list,  # legacy alias
    "problem-cause-solution": _render_problem_solution,
}


# Back-compat alias so nothing else breaks during rollout.
def render_card(title: str, items: list, font_dir: Path, out_path: Path) -> Path:
    return _render_numbered_list(
        {"overall_title": title, "cards": [{"title": "", "description": str(x)} for x in items]},
        font_dir, out_path,
    )
