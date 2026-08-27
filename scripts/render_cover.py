#!/usr/bin/env python3
"""Render a CRP cover: draw the copy onto a base plate.

Runs HERE, on the render runner, rather than in n8n. n8n Cloud carries only the Microsoft core
fonts and cannot have any installed, which capped us at Arial Black. The runner already installs
Inter for the video overlays, so covers use the same face and the brand finally matches end to
end. Moving to a licensed Sohne later is a --font-dir change, nothing more.

The plate carries the photography, the CRP lockup and the Themis mark, baked in by the designer.
This script only ever draws text, so a mark can never drift, mis-scale or land on a face.

Layout is a port of the n8n engine: every dimension is a FRACTION OF THE CANVAS, so a new ratio
needs no new numbers. Text is measured with the real font via PIL, which is why there is no glyph
width table here - that table only existed because ImageMagick inside n8n could not measure for us.
"""
import argparse
import io
import os
import sys
import urllib.request

from PIL import Image, ImageDraw, ImageFont

C_WHITE = "#F7F7F5"
C_SILVER = "#C9C9C7"
C_CHARCOAL = "#242424"

# A bare stopword stranded on its own headline line reads as a mistake.
STOP = set("A AN THE TO OF AND OR IS ARE ON IN FOR AT BY IT MY YOUR NO WE OUR".split())

# Which elements each template renders. T1 is headline ONLY - if a cover comes out with no
# sub-header, check the template before anything else.
TEMPLATES = {
    "T1": dict(eyebrow=False, body=False, rule=False),   # Hook Question
    "T2": dict(eyebrow=True, body=True, rule=True),      # Problem Statement
    "T3": dict(eyebrow=False, body=True, rule=True),     # Solution Insight
    "T4": dict(eyebrow=True, body=True, rule=True),      # Legal Explainer
    "T5": dict(eyebrow=True, body=True, rule=True),      # Brand / Authority
    "T6": dict(eyebrow=False, body=True, rule=True),     # Engagement / CTA
}


# The five master sizes from the brand guide. A plate is snapped to whichever it matches, so a
# cover is always spec size regardless of what the designer exported at.
MASTERS = [(1080, 1920), (1080, 1350), (1080, 1080), (1920, 1080), (1000, 1500)]


def snap_to_master(im):
    """Cover-fit the plate to its nearest master size.

    Plates arrive at whatever the designer exported - Lisa's 16:9 is 1672x941, Stacy's 9:16 is
    1080x1920. Without this the cover inherits the plate's size, so thumbnails vary per presenter.
    Scale to FILL then crop; never squash. On a plate already at master size this is a no-op.
    """
    W, H = im.size
    aspect = W / float(H)
    tw, th = min(MASTERS, key=lambda m: abs((m[0] / float(m[1])) - aspect))
    if (W, H) == (tw, th):
        return im, False
    scale = max(tw / float(W), th / float(H))
    fw = max(tw, int(round(W * scale)))
    fh = max(th, int(round(H * scale)))
    im = im.resize((fw, fh), Image.LANCZOS)
    # Bias the vertical crop up - heads live in the top of the frame.
    x = max(0, min(fw - tw, int(round((fw - tw) * 0.5))))
    y = max(0, min(fh - th, int(round((fh - th) * 0.15))))
    return im.crop((x, y, x + tw, y + th)), True


def fetch(src, timeout=60):
    """Load the plate from a local path or a URL."""
    if os.path.exists(src):
        return Image.open(src).convert("RGB")
    req = urllib.request.Request(src, headers={"User-Agent": "crp-cover/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    im = Image.open(io.BytesIO(data))
    # Drive can answer uc?export=download with an HTML interstitial instead of bytes. load()
    # forces the decode now so we fail loudly here rather than publishing a broken asset.
    im.load()
    return im.convert("RGB")


def balanced(text, n):
    """Every distribution of n-1 breaks; reject bare stopword lines; prefer short even ones."""
    words = text.split()
    if len(words) <= 1 or n <= 1:
        return [text]
    if n >= len(words):
        return list(words)
    slots = len(words) - 1
    need = n - 1
    best = None
    best_score = float("inf")
    for mask in range(1 << slots):
        if bin(mask).count("1") != need:
            continue
        lines = []
        cur = [words[0]]
        for b in range(slots):
            if mask >> b & 1:
                lines.append(" ".join(cur))
                cur = [words[b + 1]]
            else:
                cur.append(words[b + 1])
        lines.append(" ".join(cur))
        if any(l.strip("?!.,") in STOP for l in lines):
            continue
        lens = [len(l) for l in lines]
        score = max(lens) * 10 + (max(lens) - min(lens))
        if score < best_score:
            best_score = score
            best = lines
    return best or [text]


def cap_height(font):
    bb = font.getbbox("H")
    return bb[3] - bb[1]


def render(plate, headline, eyebrow, body, template, fonts, out_path):
    im = plate
    W, H = im.size
    land = W > H
    d = ImageDraw.Draw(im)

    def px(v):
        return max(0, round(v))

    el = TEMPLATES.get(template, TEMPLATES["T1"])
    show_eyebrow = el["eyebrow"] and bool(eyebrow)
    show_body = el["body"] and bool(body)
    show_rule = el["rule"]

    # Geometry. Portrait is measured off the approved 9:16 reference, not derived from landscape.
    # The bottom band is RESERVED for the lockup baked into the plate.
    if land:
        colX, colW, top = px(0.055 * W), px(0.46 * W), px(0.10 * H)
        band, chip_f, body_f, body_floor, max_body, lock = 0.44, 0.052, 0.030, 0.020, 3, 0.16
    else:
        colX, colW, top = px(0.035 * W), px(0.625 * W), px(0.200 * H)
        band, chip_f, body_f, body_floor, max_body, lock = 0.27, 0.036, 0.024, 0.017, 4, 0.14

    areaH = (H - px(lock * H)) - top
    gap = px(0.026 * H)
    rule_gap = px(0.042 * H)

    # Eyebrow chip: charcoal box, white text, square corners. Hugs its own text.
    chipH = px(chip_f * H) if show_eyebrow else 0
    chip_font = ImageFont.truetype(fonts["chip"], px(chipH * 0.52)) if show_eyebrow else None
    chip_pad = px(0.018 * W)
    chipW = px(d.textlength(eyebrow, font=chip_font) + chip_pad * 2) if show_eyebrow else 0

    # Sub-header: shrink until it fits the line budget, keeping the sentence whole.
    lines = []
    bf = 0
    b_lh = 0
    bodyH = 0
    body_font = None
    if show_body:
        bf = px(body_f * H)
        floor = px(body_floor * H)
        while True:
            body_font = ImageFont.truetype(fonts["body"], bf)
            lines = []
            cur = ""
            for wd in body.split():
                t = (cur + " " + wd).strip()
                if d.textlength(t, font=body_font) <= colW or not cur:
                    cur = t
                else:
                    lines.append(cur)
                    cur = wd
            if cur:
                lines.append(cur)
            if len(lines) <= max_body or bf <= floor:
                break
            bf = px(bf * 0.92)
        body_font = ImageFont.truetype(fonts["body"], bf)
        lines = lines[:max_body]
        b_lh = px(bf * 1.45)
        bodyH = (len(lines) - 1) * b_lh + cap_height(body_font)

    ruleH = max(3, px(0.0030 * H)) if show_rule else 0
    ruleW = px(0.075 * W)

    # THE TITLE BAND. The headline gets a fixed share of the canvas, never the leftovers, so a
    # longer headline sizes DOWN instead of pushing the chip and sub-header out of the way.
    below = (rule_gap + ruleH if show_rule else 0) + (rule_gap + bodyH if show_body else 0)
    above = (chipH + gap) if show_eyebrow else 0
    headH = max(px(0.08 * H), min(areaH - below - above, px(band * H)))

    best = None
    for n in range(1, min(4, len(headline.split())) + 1):
        ls = balanced(headline, n)
        if len(ls) != n:
            continue
        lo, hi = 20, 900
        while lo < hi:                      # binary search the largest size that fits
            mid = (lo + hi + 1) // 2
            f = ImageFont.truetype(fonts["head"], mid)
            wmax = max(d.textlength(l, font=f) for l in ls)
            hgt = (n - 1) * mid * 1.02 + cap_height(f)
            if wmax <= colW and hgt <= headH:
                lo = mid
            else:
                hi = mid - 1
        f = ImageFont.truetype(fonts["head"], lo)
        fill = min(
            max(d.textlength(l, font=f) for l in ls) / colW,
            ((n - 1) * lo * 1.02 + cap_height(f)) / headH,
        )
        score = max(0, 0.92 - fill) * 2000 - lo
        if best is None or score < best[0]:
            best = (score, ls, lo, n)

    _, h_lines, h_size, h_n = best
    head_font = ImageFont.truetype(fonts["head"], h_size)
    h_cap = cap_height(head_font)
    h_lh = px(h_size * 1.02)

    # Stack, top anchored.
    y = top
    if show_eyebrow:
        d.rectangle([colX, y, colX + chipW, y + chipH], fill=C_CHARCOAL)
        cc = cap_height(chip_font)
        d.text(
            (colX + chip_pad, y + (chipH - cc) // 2 - chip_font.getbbox("H")[1]),
            eyebrow, font=chip_font, fill=C_WHITE,
        )
        y += chipH + gap
    for i, l in enumerate(h_lines):
        d.text((colX, y + i * h_lh - head_font.getbbox("H")[1]), l, font=head_font, fill=C_WHITE)
    y += (h_n - 1) * h_lh + h_cap
    if show_rule:
        y += rule_gap
        d.rectangle([colX, y, colX + ruleW, y + ruleH], fill=C_WHITE)
        y += ruleH
    if show_body:
        y += rule_gap
        for i, l in enumerate(lines):
            d.text((colX, y + i * b_lh - body_font.getbbox("H")[1]), l, font=body_font, fill=C_SILVER)

    im.save(out_path)
    return dict(
        width=W, height=H,
        headline_size=h_size, headline_lines=h_n,
        body_size=bf, body_lines=len(lines),
    )


def main():
    p = argparse.ArgumentParser(description="Render a CRP cover onto a base plate.")
    p.add_argument("--plate", required=True, help="plate URL or local path")
    p.add_argument("--out", required=True)
    p.add_argument("--headline", required=True)
    p.add_argument("--eyebrow", default="")
    p.add_argument("--body", default="")
    p.add_argument("--template", default="T2")
    p.add_argument("--font-dir", default="fonts")
    a = p.parse_args()

    fd = a.font_dir
    fonts = dict(
        head=os.path.join(fd, "Inter-Black.ttf"),
        chip=os.path.join(fd, "Inter-Bold.ttf"),
        body=os.path.join(fd, "Inter-Regular.ttf"),
    )
    for k, v in fonts.items():
        if not os.path.exists(v):
            print("[cover] FATAL: missing %s font -> %s" % (k, v), file=sys.stderr)
            return 2

    print("[cover] plate: %s" % a.plate)
    plate = fetch(a.plate)
    print("[cover] plate loaded %dx%d" % plate.size)
    plate, resized = snap_to_master(plate)
    if resized:
        print("[cover] snapped to master %dx%d" % plate.size)
    info = render(
        plate, a.headline.strip(), a.eyebrow.strip(), a.body.strip(),
        a.template.strip().upper(), fonts, a.out,
    )
    print("[cover] wrote %s  headline %dpx on %d lines, body %dpx on %d lines"
          % (a.out, info["headline_size"], info["headline_lines"],
             info["body_size"], info["body_lines"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
