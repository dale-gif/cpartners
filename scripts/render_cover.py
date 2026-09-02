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
import re
import sys
import time
import urllib.error
import urllib.request

from PIL import Image, ImageDraw, ImageFont

C_WHITE = "#F7F7F5"
C_SILVER = "#C9C9C7"
C_CHARCOAL = "#242424"

# A 9:16 cover is 1080x1920, but Instagram's profile grid shows a CENTRE SQUARE
# of it: 1080x1080, keeping y 420..1500 and discarding the top and bottom 420.
# The portrait layout used to start its text at 0.048*H = y92, which is entirely
# inside the discarded strip - so on the grid the headline was sliced and read
# as broken text, the exact fault the library was reset over. Pinterest (2:3)
# and some Facebook previews crop the same way.
#
# 420/1920 = 0.21875. Keeping the whole text block between these two lines means
# any centre crop, square or 4:5, keeps the headline whole.
CROP_SAFE = 0.219

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


def drive_direct(url):
    """Rewrite a Drive share link onto the usercontent host.

    drive.google.com/uc?export=download answers with a "Virus scan warning" HTML page instead of
    the bytes once a file passes ~100MB. Plates are small enough today that the old host works,
    but it cost us four MF renders on the video path, so nothing here relies on it either.
    """
    if "drive.google.com" not in url:
        return url
    m = re.search(r"[?&]id=([^&]+)", url)
    if not m:
        return url
    return ("https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t"
            % m.group(1))


def fetch(src, timeout=60, attempts=5):
    """Load the plate from a local path or a URL, retrying transient Drive failures.

    The plates live on Drive and are pulled unauthenticated. Under load Drive answers
    with 503 or 429 rather than the file, and a single 503 here used to take the whole
    render down: the cover step exited non-zero, so the title card and the GitHub
    release were both skipped and the watcher then 404d looking for the release. One
    throttled HTTP GET should not cost a finished video.

    Retrying is safe precisely because this is a GET. Nothing is created and nothing is
    published, so a second attempt cannot duplicate anything - unlike the Drive upload
    and the Metricool post, which must never be retried blind.
    """
    if os.path.exists(src):
        return Image.open(src).convert("RGB")
    src = drive_direct(src)

    RETRY_STATUS = {429, 500, 502, 503, 504}
    last = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "crp-cover/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            im = Image.open(io.BytesIO(data))
            # Drive can answer uc?export=download with an HTML interstitial instead of
            # bytes. load() forces the decode now so we fail here rather than publishing
            # a broken asset. It is also retryable: the interstitial is what Drive serves
            # when it is throttling, and the next attempt often returns the real file.
            im.load()
            if attempt > 1:
                print("[cover] plate fetched on attempt %d" % attempt)
            return im.convert("RGB")
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in RETRY_STATUS or attempt == attempts:
                raise
            print("[cover] plate HTTP %d, attempt %d/%d" % (e.code, attempt, attempts),
                  file=sys.stderr)
        except (urllib.error.URLError, OSError) as e:
            # OSError covers PIL's UnidentifiedImageError on a throttle interstitial.
            last = e
            if attempt == attempts:
                raise
            print("[cover] plate fetch failed (%s), attempt %d/%d"
                  % (type(e).__name__, attempt, attempts), file=sys.stderr)
        time.sleep(min(2 ** attempt, 16))
    raise last


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
        # A headline like "REAL CASES. REAL RESULTS." carries its own break points. Pure
        # width-balancing sets it as REAL / CASES. REAL / RESULTS., running a sentence
        # across a line break; the approved reference breaks at the full stop. Penalise any
        # line holding a terminator that is not its last character, heavily enough to beat
        # the evenness term. Headlines with no internal terminator are unaffected.
        straddle = sum(1 for l in lines if re.search(r'[.?!]\s', l))
        score = max(lens) * 10 + (max(lens) - min(lens)) + straddle * 400
        if score < best_score:
            best_score = score
            best = lines
    return best or [text]


def cap_height(font):
    bb = font.getbbox("H")
    return bb[3] - bb[1]


# Typeface sets, in order of preference. Sohne is the brand face but is a LICENSED font that only
# ships to the runner once the licence covers it; Inter is the metric-compatible stand-in. Keeping
# both here means deploying Sohne is a matter of dropping the files in, with no code change - and
# if they are ever absent the render falls back rather than dying mid-pipeline.
# head_portrait is a SEPARATE role, not a replacement for head. Larry approved the
# lighter headline on the 9:16 reference only; landscape covers already in circulation
# keep Extrafett so this change cannot silently restyle them. Unifying the two is a
# one-line change here if that is ever wanted.
FONT_SETS = {
    "sohne": {"head": "Sohne-Extrafett", "head_portrait": "Sohne-Halbfett",
              "chip": "Sohne-Halbfett", "body": "Sohne-Buch"},
    # Inter-Bold is NOT committed to fonts/; render.yml downloads it into fonts/ at build
    # time along with Regular, Black and ExtraBold. So this set resolves in CI even though
    # it cannot resolve against a bare checkout - run render_cover.py locally with
    # --font-dir fonts and it will correctly report the set incomplete.
    "inter": {"head": "Inter-Black", "head_portrait": "Inter-ExtraBold",
              "chip": "Inter-Bold", "body": "Inter-Regular"},
}
FONT_ORDER = ["sohne", "inter"]


def resolve_fonts(font_dir, want="auto"):
    """Return (role -> path, name) for the first COMPLETE set found. A half-deployed family is
    skipped entirely - mixing Sohne headlines with Inter body text is worse than either alone."""
    names = FONT_ORDER if want == "auto" else [want]
    for name in names:
        found = {}
        for role, stem in FONT_SETS[name].items():
            for ext in (".otf", ".ttf"):
                cand = os.path.join(font_dir, stem + ext)
                if os.path.exists(cand):
                    found[role] = cand
                    break
        want = len(FONT_SETS[name])
        if len(found) == want:
            return found, name
        if found:
            print("[cover] %s is incomplete (%d/%d weights), skipping"
                  % (name, len(found), want), file=sys.stderr)
    return None, None


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
    # The rule is a SEPARATOR, so it only earns its place when there is something under it to
    # separate. Without this gate a sub-header dropped upstream leaves a stray dash hanging below
    # the headline, which reads as a broken render rather than a deliberate headline-only cover.
    show_rule = el["rule"] and show_body
    # The approved 9:16 reference sets the sub-header directly under the headline with no
    # separator. Landscape keeps the rule.
    if not land:
        show_rule = False

    # Geometry. Portrait is measured off the approved 9:16 reference, not derived from landscape.
    # The bottom band is RESERVED for the lockup baked into the plate.
    if land:
        colX, colW, top = px(0.055 * W), px(0.46 * W), px(0.10 * H)
        band, chip_f, body_f, body_floor, max_body, lock = 0.44, 0.052, 0.030, 0.020, 3, 0.16
        bodyW = colW
    else:
        # 9:16 measured off Mohi's approved reference (2026-08-28), by solving font sizes
        # from where his lines actually BREAK. Reading cap heights off the sample overshot
        # twice - 141px against a true 127, and 47px against a true 38 - because a
        # screenshot's cap height is a soft edge while a line break is exact.
        #
        # The headline runs to 0.831W and DELIBERATELY crosses the subject. An earlier
        # attempt confined it to the plate's black column (ends 0.463W) and Larry called it
        # too small; that one invented constraint cost ~40% of the size.
        #
        # colX 0.071 is the plate's OWN margin - CRP_*_0916_* bake their lockup rule from
        # x=0.0546W and the Themis mark from 0.0537W, so copy and lockup share a left edge.
        colX, colW, top = px(0.071 * W), px(0.760 * W), px(CROP_SAFE * H)
        band, chip_f, body_f, body_floor, max_body, lock = 0.225, 0.039, 0.0198, 0.017, 3, 0.135
        # The sub-header wraps NARROWER than the headline on the reference, which is the
        # only reason bodyW exists rather than everything sharing colW.
        bodyW = px(0.580 * W)

    area_lock = lock if land else max(lock, CROP_SAFE)
    areaH = (H - px(area_lock * H)) - top
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
                if d.textlength(t, font=body_font) <= bodyW or not cur:
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

    # The chip's slot is RESERVED whether or not a chip is drawn.
    #
    # Without this, a template with no eyebrow (T1, T3, T6) starts its headline higher AND
    # gets a taller title band, so the binary search sizes the type up and it drifts right
    # across the presenter's face. T2 carries a chip, which pushes the stack down and shrinks
    # the band, so it lands correctly. On the first rotation sample Larry's three eyebrow-less
    # templates were all marked and T2 was left alone - that asymmetry, not the crossing
    # itself, was the defect. The approved reference does cross the subject on purpose.
    #
    # Reserving the slot gives all four templates one baseline and one title band, so the
    # rotation varies the layout without moving where the type sits.
    above = px(chip_f * H) + gap
    headH = max(px(0.08 * H), min(areaH - below - above, px(band * H)))

    head_path = fonts["head"] if land else fonts.get("head_portrait", fonts["head"])

    best = None
    for n in range(1, min(4, len(headline.split())) + 1):
        ls = balanced(headline, n)
        if len(ls) != n:
            continue
        lo, hi = 20, 900
        while lo < hi:                      # binary search the largest size that fits
            mid = (lo + hi + 1) // 2
            f = ImageFont.truetype(head_path, mid)
            wmax = max(d.textlength(l, font=f) for l in ls)
            hgt = (n - 1) * mid * 1.02 + cap_height(f)
            if wmax <= colW and hgt <= headH:
                lo = mid
            else:
                hi = mid - 1
        f = ImageFont.truetype(head_path, lo)
        fill = min(
            max(d.textlength(l, font=f) for l in ls) / colW,
            ((n - 1) * lo * 1.02 + cap_height(f)) / headH,
        )
        score = max(0, 0.92 - fill) * 2000 - lo
        if best is None or score < best[0]:
            best = (score, ls, lo, n)

    _, h_lines, h_size, h_n = best
    head_font = ImageFont.truetype(head_path, h_size)
    h_cap = cap_height(head_font)
    h_lh = px(h_size * 1.02)

    # Stack, top anchored.
    y = top
    if not show_eyebrow:
        y += above          # keep the reserved slot empty, same baseline as a chipped cover
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
    p.add_argument("--font-set", default="auto", choices=["auto"] + list(FONT_SETS),
                   help="auto prefers Sohne when it is deployed, else falls back to Inter")
    a = p.parse_args()

    fonts, face = resolve_fonts(a.font_dir, a.font_set)
    if fonts is None:
        print("[cover] FATAL: no complete font set in %s" % a.font_dir, file=sys.stderr)
        return 2
    print("[cover] typeface: %s" % face)

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
