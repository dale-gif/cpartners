"""SF (portrait 9:16) on-screen-text overlays — Larry-approved placements.

Renders ONE short OST line per PNG at 1080x1920: big Inter Black, white,
UPPERCASE, horizontally centered, at one of four vertical placements —

    top          attention grab on entry
    center       high-impact; RESERVED for a single punchy standalone line
    lower_third  over/under rule bars; keeps the presenter's face visible
    bottom       strong anchor

PNGs are transparent RGBA so they composite straight over the portrait avatar
clip. Fades (0.5s in/out) are applied at composite time in sf_render.py — never
slide, matching Larry's locked transition spec.

SF ONLY — this module is self-contained and does NOT touch the landscape MF/LF
path (kinetic_words.py / compose_from_plan.py).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---- portrait canvas (9:16) ----
W, H = 1080, 1920

WHITE = (255, 255, 255, 255)

# ---- text sizing ----
MAX_SIZE = 140          # starting font size; auto-shrinks to fit width
MIN_SIZE = 72           # floor for single-line fits (still bold + readable)
WRAP_MIN_SIZE = 56      # deeper floor allowed only when forced to wrap
SIDE_MARGIN = 72        # keep text off the frame edges
MAX_W = W - 2 * SIDE_MARGIN
LINE_GAP = 14
MAX_LINES = 2           # NEVER stack more than two lines

# ---- vertical safe zone (block stays inside these bounds) ----
# Larry-flagged BLACK DANGER ZONE: bottom ~30% of the frame is reserved for
# OpusClip's subtitle track + CTA + progress bar. Our OSTs must stay ABOVE
# the danger line (y ~= 1360). SAFE_BOTTOM = H - danger_line = 1920 - 1360.
SAFE_TOP = 108
SAFE_BOTTOM = 560

# ---- vertical anchor per placement (fraction of H = the text-block CENTER) ----
# All four placements now live ABOVE the danger line at 0.71H.
PLACEMENTS = {
    "top": 0.17,          # attention grab, well clear of the presenter
    "center": 0.45,       # punchy standalone — reserved, and pulled up a touch
    "lower_third": 0.56,  # over the presenter's chest/hands
    "bottom": 0.66,       # strong anchor — the last safe rung above the danger zone
}

# ---- lower-third rule bars ----
BAR_W = 640
BAR_H = 6
BAR_GAP = 30            # gap between a bar and the text block

# ---- soft blurred drop shadow (legibility over any background) ----
SHADOW_OFFSET = 5
SHADOW_BLUR = 12
SHADOW_ALPHA = 170

# CENTER is reserved for short, punchy standalone lines.
CENTER_MAX_WORDS = 3
FACE_SAFE_ROTATION = ("top", "lower_third", "bottom")


def _load_font(font_dir: Path, size: int) -> ImageFont.FreeTypeFont:
    for name in ("Inter-Black.ttf", "Inter-ExtraBold.ttf"):
        p = font_dir / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _wrap(words, font, draw, max_w):
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        if cur and draw.textbbox((0, 0), trial, font=font)[2] > max_w:
            lines.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    return lines


def _fit(text, font_dir, draw):
    """Prefer ONE line at the biggest size that fits. Only wrap when a
    single-line fit at MIN_SIZE still overflows. Never returns >MAX_LINES.
    """
    words = text.upper().split()
    if not words:
        return _load_font(font_dir, MIN_SIZE), []

    joined = " ".join(words)

    # 1) Single line — biggest size that fits horizontally.
    for size in range(MAX_SIZE, MIN_SIZE - 1, -4):
        font = _load_font(font_dir, size)
        if draw.textbbox((0, 0), joined, font=font)[2] <= MAX_W:
            return font, [joined]

    # 2) Forced wrap — shrink further, cap at MAX_LINES lines.
    for size in range(MAX_SIZE, WRAP_MIN_SIZE - 1, -4):
        font = _load_font(font_dir, size)
        lines = _wrap(words, font, draw, MAX_W)
        if len(lines) <= MAX_LINES:
            widest = max(draw.textbbox((0, 0), ln, font=font)[2] for ln in lines)
            if widest <= MAX_W:
                return font, lines

    # 3) Fallback: force even 2-line split at the floor size.
    font = _load_font(font_dir, WRAP_MIN_SIZE)
    mid = (len(words) + 1) // 2
    return font, [" ".join(words[:mid]), " ".join(words[mid:])]


def render_sf_overlay(text: str, placement: str, font_dir: Path, out_path: Path) -> Path:
    """Render one portrait OST line PNG at `placement`. Returns the PNG path."""
    placement = (placement or "bottom").lower()
    if placement not in PLACEMENTS:
        placement = "bottom"

    font_dir = Path(font_dir)
    out_path = Path(out_path)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    text = (text or "").strip()
    if not text:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        return out_path

    font, lines = _fit(text, font_dir, draw)

    asc, desc = font.getmetrics()
    line_h = asc + desc
    block_h = line_h * len(lines) + LINE_GAP * (len(lines) - 1)

    cy = int(H * PLACEMENTS[placement])
    top_y = cy - block_h // 2

    # Clamp inside the safe frame so no line ever clips off top or bottom.
    max_top = H - SAFE_BOTTOM - block_h
    top_y = max(SAFE_TOP, min(top_y, max_top))

    def _line_x(line: str) -> int:
        lw = draw.textbbox((0, 0), line, font=font)[2]
        return (W - lw) // 2

    # 1) soft blurred drop shadow (all lines) on its own layer
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    y = top_y
    for line in lines:
        x = _line_x(line)
        sd.text((x + SHADOW_OFFSET, y + SHADOW_OFFSET), line, font=font,
                fill=(0, 0, 0, SHADOW_ALPHA))
        y += line_h + LINE_GAP
    shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    img = Image.alpha_composite(img, shadow)
    draw = ImageDraw.Draw(img)

    # 2) lower-third rule bars (crisp white, over + under the block)
    if placement == "lower_third":
        bx = (W - BAR_W) // 2
        draw.rectangle([bx, top_y - BAR_GAP - BAR_H, bx + BAR_W, top_y - BAR_GAP],
                       fill=WHITE)
        draw.rectangle([bx, top_y + block_h + BAR_GAP, bx + BAR_W,
                        top_y + block_h + BAR_GAP + BAR_H], fill=WHITE)

    # 3) crisp white text
    y = top_y
    for line in lines:
        draw.text((_line_x(line), y), line, font=font, fill=WHITE)
        y += line_h + LINE_GAP

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def _overlay_text(ov: dict) -> str:
    lines = ov.get("lines")
    if isinstance(lines, list):
        return " ".join(str(l) for l in lines if l)
    return str(ov.get("text") or "")


def assign_placements(overlays: list[dict]) -> list[str]:
    """Content-aware placement for a sequence of OST lines (Larry-approved).

    - An explicit `placement` on an overlay always wins.
    - CENTER is reserved for ONE short, punchy standalone line (flagged
      `punchy: true`, or <= CENTER_MAX_WORDS words) and never twice in a row.
    - Everything else rotates TOP -> LOWER_THIRD -> BOTTOM so the presenter's
      face stays visible.
    """
    out: list[str] = []
    rot = 0
    center_used = False
    for ov in overlays:
        explicit = (ov.get("placement") or "").lower()
        if explicit in PLACEMENTS:
            out.append(explicit)
            center_used = center_used or explicit == "center"
            continue
        words = _overlay_text(ov).split()
        # CENTER is deliberate: only a line explicitly flagged punchy (and short
        # enough to land clean) takes it — never just because a line is short.
        punchy = bool(ov.get("punchy")) and len(words) <= CENTER_MAX_WORDS
        if punchy and not center_used and (not out or out[-1] != "center"):
            out.append("center")
            center_used = True
        else:
            out.append(FACE_SAFE_ROTATION[rot % len(FACE_SAFE_ROTATION)])
            rot += 1
    return out


# ---- preview: render the 4 approved placements over a mock office backdrop ----
if __name__ == "__main__":
    import sys

    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "sf_preview")
    out_dir.mkdir(parents=True, exist_ok=True)
    font_dir = Path("fonts")

    # Real failure cases from Dale's screenshots — must render single-line
    # and stay inside the safe frame.
    samples = [
        ("STILL OWE YOU MONEY", "top"),      # was clipping off top of frame
        ("OWED MONEY", "lower_third"),       # was stacking to 2 huge lines
        ("YOUR MONEY", "center"),            # was stacking to 2 huge lines
        ("HALF", "bottom"),
        ("BE RECOVERED", "lower_third"),     # was colliding with HALF at bottom
        ("GET PAID", "top"),
        ("MISTAKE", "bottom"),
        ("30 DAYS", "lower_third"),
    ]

    def _backdrop() -> Image.Image:
        # dark vertical gradient mimicking the reference office frame (fast: 1xH then resize)
        top, bot = (36, 38, 42), (13, 14, 16)
        col = Image.new("RGBA", (1, H))
        cp = col.load()
        for yy in range(H):
            t = yy / H
            cp[0, yy] = tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3)) + (255,)
        bg = col.resize((W, H))
        # visualise the OpusClip danger zone so we can eyeball placements
        danger_y = H - SAFE_BOTTOM
        d = ImageDraw.Draw(bg)
        d.rectangle([0, danger_y, W, H], fill=(70, 0, 0, 90))
        d.line([(0, danger_y), (W, danger_y)], fill=(255, 60, 60, 255), width=4)
        return bg

    for idx, (text, place) in enumerate(samples):
        slug = text.lower().replace(" ", "_")
        ov_png = out_dir / f"_ov_{idx:02d}_{place}_{slug}.png"
        render_sf_overlay(text, place, font_dir, ov_png)
        comp = Image.alpha_composite(_backdrop(), Image.open(ov_png).convert("RGBA"))
        comp.convert("RGB").save(out_dir / f"sf_{idx:02d}_{place}_{slug}.png")
        print(f"rendered {place}: {text}")
    print(f"previews written to {out_dir}")
