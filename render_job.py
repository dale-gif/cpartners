"""Full GODTIER render pipeline for GitHub Actions.

Reads env vars:
  OPUS_CLIP_URL       — signed OpusClip MP4 URL (from n8n dispatch)
  VIDEO_ID            — Notion page id / asset id (used in output filename)
  OPENAI_API_KEY      — for Whisper transcription
  ANTHROPIC_API_KEY   — for Claude graphic planner
  GOOGLE_DRIVE_CREDENTIALS — service account JSON (as string)
  OUTPUT_FOLDER_ID    — Google Drive folder id to upload final MP4 to

Runs: download → extract audio → Whisper → Claude planner → composite → upload.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import requests

from compose_from_plan import (
    INFOGRAPHIC_HOLD,
    OVERLAY_HOLD,
    TimedClip,
    composite_clips,
)
from animations import render_infographic_clip, render_overlay_clip

# How long each cutaway's entrance animation runs before it holds on the final
# frame. The clip's total on-screen time is `hold`; entrance is the first
# ENTRANCE_SECS of that. Infographic entrance stays at 3.0s so all 3 staggered
# cards fully resolve before the freeze (2.0s cuts the 3rd card off mid-reveal);
# it still fits inside the new 5-8s holds. Overlay entrance trimmed to 1.0s so
# the shorter <=3s text holds still leave readable freeze time.
INFOGRAPHIC_ENTRANCE = 3.0
OVERLAY_ENTRANCE = 1.0

OPUS_URL = os.environ["OPUS_CLIP_URL"]
VIDEO_ID = os.environ["VIDEO_ID"]
OPENAI_KEY = os.environ["OPENAI_API_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
FONT_DIR = Path(os.environ.get("FONT_DIR", "fonts"))
WORK = Path("work")

CLAUDE_SYSTEM = (
    "You are the GODTIER graphic planner for CRP videos. You read a Whisper "
    "verbose_json transcript and emit a JSON plan that chooses infographic "
    "moments and text overlay moments.\n\n"
    "GLOBAL STYLE (Larry-locked): pure black and white; ALL CAPS everywhere; "
    "punchy phrasing; infographics hold 5-8s, text overlays hold <=3s; appear "
    "(fade/scale), never slide; no kickers, no page counters, no type labels, "
    "no emoji. Keep it minimal. Focus on clarity. Clean. Professional. Reusable.\n\n"
    "RULE OF 3 (LOCKED): every infographic contains EXACTLY 3 cards. Never 2, "
    "never 4, never 5. If Stacey lists more than 3 things, pick the 3 most "
    "important. If she lists fewer, expand to 3 supporting beats. The overall "
    "title MUST NOT reference a count that isn't 3 — avoid 'THE FOUR STEPS' / "
    "'FIVE THINGS'. Prefer counted framings only when they say THREE ('THREE "
    "WAYS TO GET PAID') or use countless framings ('WHY THEY DON'T PAY', "
    "'PATHWAYS TO GET PAID', 'WHAT GOOD LOOKS LIKE').\n\n"
    "INFOGRAPHIC SCHEMA (per moment):\n"
    "  timestamp      : seconds — when Stacey STARTS explaining this concept.\n"
    "  hold           : seconds — 5-8s depending on how much the card carries "
    "                  (a light 3-card beat ~5s; a dense one up to 8s). PUNCHY: "
    "                  the card makes its point and cuts back to Stacey — it does "
    "                  NOT linger for the whole explanation.\n"
    "  overall_title  : 1-3 words CAPS, <= 22 chars. Example: WHY THEY DON'T PAY\n"
    "  subtitle       : short CAPS phrase, <= 40 chars. Example: IT IS A CHOICE, NOT AN ACCIDENT\n"
    "  template       : PICK ONE — see TEMPLATES below\n"
    "  cards[3]       : EXACTLY 3. Each: title 1-2 words CAPS <=12 chars; "
    "                  description 2-3 short phrases CAPS <=60 chars total.\n\n"
    "TEMPLATES (pick the one that best fits the beat):\n"
    "  * three-cards      — DEFAULT. 3 bordered cards side-by-side with centered numbers, dividers, titles. Use for lists, principles, categories.\n"
    "  * three-columns    — 3 columns with a CONTEXT ICON in a circle at the top of each, big number, title, description, vertical dividers. Use for tips or benefits. For THIS template ONLY, each card MUST include an \"icon\" field (see ICONS below) chosen to fit that card's meaning.\n"
    "  * timeline         — 3 numbered CIRCLES connected by a horizontal line. Use for sequential steps or a journey.\n"
    "  * numbered-list    — Vertical 01/02/03 rows with divider bars + horizontal separators. Use for instructions or ranked items.\n"
    "  * circle-diagram   — 3-segment ring on left + numbered legend with dividers on right. Use for parts of a whole or overlapping ideas.\n"
    "  * problem-solution — Problem / Cause / Solution with large icon boxes (warning, magnifier, badge). Use for issue analysis.\n"
    "  * checklist        — 3 rows with plus-sign icons in bordered squares + horizontal separators. Use for best-practices or a to-do.\n\n"
    "ICONS (for three-columns cards only): pick the closest match per card from "
    "this exact list — document (report/record/paperwork), search (investigate/"
    "find), warning (risk/danger), check (verify/done/approve), shield (protect/"
    "defend), scales (legal/court/liability), person (director/individual), "
    "people (team/parties), phone (contact/call), clock (time/deadline), money "
    "(payment/debt/cost), lock (secure/safe), flag (report/mark), mail (letter/"
    "notice), chart (growth/results), edit (sign/write), folder (file/case), "
    "target (goal/focus), calendar (schedule/date), star (best practice/quality), "
    "building (company/business). Use the single word only (e.g. \"scales\").\n\n"
    "TEXT OVERLAY SCHEMA: exactly 3 lines per overlay; each line 1-5 words, "
    "<=14 chars. Punchy. Rule of 3. Break aggressively: "
    "'THE RELATIONSHIP IS DAMAGED YOU FEEL IT' becomes "
    "['RELATIONSHIP', 'ALREADY DAMAGED', 'YOU FEEL IT']. "
    "  timestamp : seconds — when the line lands.\n"
    "  hold      : seconds — how long the text stays up. ~3s (MAXIMUM 3). "
    "              Punchy: long enough to read a 1-3 word hook, then cut.\n"
    "TEXT OVERLAY STYLES — pick the best fit:\n"
    "  * 'black-gradient' (DEFAULT) — transparent letterbox overlay. Stacey stays "
    "visible frame-right. Text is bold white, frame-left. Use for most overlays.\n"
    "  * 'big-text' — OPAQUE full-frame cutaway. Massive centered text on solid "
    "black, fills the screen. Use for the HOOK overlay and for major impact "
    "statements / sentiment peaks. The text is joined into 1-2 giant lines.\n"
    "  * 'title' — OPAQUE section title cutaway. Vertical white line on left, "
    "large bold text beside it, on solid black. Use sparingly for section/chapter "
    "transitions if the video has distinct segments.\n\n"
    "FRAME: infographic fills the frame (cutaway from Stacey). Text overlays "
    "in 'black-gradient' style land frame-left with Stacey visible frame-right. "
    "Text overlays in 'big-text' or 'title' style are FULL-FRAME cutaways.\n\n"
    "OUTRO (HARD): the last 20 seconds of the video are the OpusClip CTA "
    "outro. NEVER place an infographic or text overlay whose (timestamp + "
    "hold) crosses into the last 20s. Keep every moment fully clear of that "
    "zone.\n\n"
    "NO STATIC SCREEN (HARD — Larry): the plain talking head must NEVER be "
    "alone on screen for more than ~10 seconds. Space infographics and text "
    "overlays so a visual element (card or hook) lands often enough to break up "
    "every stretch of bare avatar footage — no gap larger than ~10s between the "
    "end of one element and the start of the next (outside the reserved outro). "
    "Lean toward MORE supporting infographics wherever Stacey explains a "
    "process, list, timeline, comparison or statistic.\n\n"
    "HOOK CADENCE (HARD): a text overlay MUST land roughly every ~12 seconds "
    "across the whole video, up to the outro. In each ~12s window pick the "
    "single most scroll-stopping thing Stacey actually says in that stretch and "
    "distil it to a punchy 1-3 word hook (e.g. 'DON\\'T WAIT', 'IT COSTS YOU', "
    "'STOP GUESSING', 'THEY WON\\'T PAY'). Use the real words/idea from that "
    "segment — do NOT repeat a hook. Each holds ~3s (max 3). Break into 1-3 "
    "lines, Rule of 3 (a single killer word alone is fine).\n"
    "  * PEAK HOOKS: mark ONLY the 1-3 single most scroll-stopping lines in the "
    "WHOLE video with \"peak\":true — the gut-punch line, the killer number, the "
    "moment that makes someone stop scrolling. These few get the dramatic black "
    "full-frame takeover; pick the VERY BEST words, nothing weaker. Every other "
    "hook is a white side overlay. Be stingy — fewer, better. Do NOT set any "
    "other style field; it is assigned automatically.\n"
    "  * NEVER place a hook so its window (timestamp .. timestamp+hold) "
    "overlaps an infographic's window. Hooks and infographics must never share "
    "the screen — leave a clean gap around every infographic.\n\n"
    "Return ONLY a single JSON object matching this schema and nothing else:\n"
    "{\"infographics\":["
    "{\"timestamp\":<seconds>,\"hold\":<seconds>,"
    "\"template\":\"three-cards|three-columns|timeline|"
    "numbered-list|circle-diagram|problem-solution|checklist\","
    "\"overall_title\":\"CAPS\",\"subtitle\":\"CAPS\","
    "\"cards\":[{\"title\":\"CAPS\",\"description\":\"CAPS\",\"icon\":\"<only for three-columns>\"},"
    "{\"title\":\"CAPS\",\"description\":\"CAPS\",\"icon\":\"...\"},"
    "{\"title\":\"CAPS\",\"description\":\"CAPS\",\"icon\":\"...\"}]}"
    "],"
    "\"text_overlays\":["
    "{\"timestamp\":<seconds>,\"hold\":<seconds>,"
    "\"lines\":[\"LINE 1\",\"LINE 2\",\"LINE 3\"],"
    "\"peak\":<true for ONLY the 1-3 very best hooks; omit otherwise>}"
    "]}"
)

# Duration thresholds for auto-format detection (seconds).
# CRP formats: MF = medium form (3-6 min), LF = long form (6-12 min).
LF_MIN_SECONDS = 360.0  # 6 min

# Reserve the last N seconds for the CTA outro. The HeyGen CTA card was removed;
# OpusClip now appends a 20s CTA at the tail of the clip, and this render runs
# on the OpusClip output — so keep the last 20s fully clear of graphics/overlays.
OUTRO_RESERVED_SECONDS = 20.0

# Every clip must have at least one punchy text overlay ("hook") landing in
# the first N seconds, capturing the sentiment peak of the opening beat.
HOOK_WINDOW_SECONDS = 20.0

# ── PACING (Larry edit notes, Aug 2026) ────────────────────────────────────
# Tune these to trade a "busier" vs "calmer" edit. Larry's brief: ~7 visual
# elements/min, and NEVER leave the plain talking head static for >8-10s.
#
# A punchy hook lands every ~12s; infographics are interspersed roughly every
# ~90s. Combined they keep a visual element on screen often enough that no
# bare-avatar gap exceeds MAX_STATIC_GAP. Hooks still ALTERNATE style so the
# rare black full-frame takeover ('big-text') stays special and the white side
# overlay ('black-gradient') carries the rest — never overlapping an infographic.
OVERLAY_EVERY_SECONDS = 12.0   # was 30 — Larry: kill the long static gaps
MIN_OVERLAY_HOLD = 2.5         # Larry: on-screen text <=3s (2.5-3.0)
MAX_OVERLAY_HOLD = 3.0
# Infographics: hold 5-8s (punchy) and appear more often to support the VO.
INFOGRAPHIC_HOLD_MIN = 5.0
INFOGRAPHIC_HOLD_MAX = 8.0
IG_EVERY_SECONDS = 90.0        # ~1 supporting infographic per ~90s of runtime
MAX_INFOGRAPHICS = 8           # cap so the planner still finds strong beats
# Hard ceiling on bare-avatar time. Larry: no static screen for >8-10s.
MAX_STATIC_GAP = 10.0
# No overlay may sit within this many seconds of an infographic block.
OVERLAY_IG_BUFFER = 1.5
# The black full-frame takeover is RARE — reserved for the very best "peak"
# hooks only, capped and spaced far apart so it stays a special moment.
MAX_BLACK_HOOKS = 3
BLACK_MIN_GAP = 90.0


def detect_format(duration: float) -> tuple[str, int, int]:
    """Return (format_label, infographic_count, text_overlay_count).

    Both counts scale with usable runtime (everything before the outro) so the
    edit stays dense enough to satisfy Larry's "no static screen >8-10s" rule:
      * one hook overlay per OVERLAY_EVERY_SECONDS (~12s)
      * one supporting infographic per IG_EVERY_SECONDS (~90s), capped
    MF keeps a floor of 2 infographics, LF a floor of 3.
    """
    usable = max(0.0, duration - OUTRO_RESERVED_SECONDS)
    overlay_count = max(3, round(usable / OVERLAY_EVERY_SECONDS))
    ig_by_runtime = round(usable / IG_EVERY_SECONDS)
    if duration < LF_MIN_SECONDS:
        return "MF", min(MAX_INFOGRAPHICS, max(2, ig_by_runtime)), overlay_count
    return "LF", min(MAX_INFOGRAPHICS, max(3, ig_by_runtime)), overlay_count


def log(msg: str) -> None:
    print(f"[crp-render] {msg}", flush=True)


def download_video(url: str, out: Path) -> None:
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    log(f"downloaded {out.stat().st_size / 1024 / 1024:.1f} MB → {out}")


def extract_audio(video: Path, audio: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video),
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
            str(audio),
        ],
        check=True,
        capture_output=True,
    )
    log(f"audio → {audio} ({audio.stat().st_size / 1024:.1f} KB)")


def video_duration(video: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def whisper_transcribe(audio: Path) -> dict:
    with open(audio, "rb") as f:
        r = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}"},
            files={"file": ("audio.mp3", f, "audio/mpeg")},
            data=[
                ("model", "whisper-1"),
                ("response_format", "verbose_json"),
                ("timestamp_granularities[]", "word"),
                ("timestamp_granularities[]", "segment"),
                ("language", "en"),
            ],
            timeout=600,
        )
    if not r.ok:
        raise RuntimeError(f"whisper {r.status_code}: {r.text[:500]}")
    return r.json()


def claude_plan(transcript: dict, duration: float, fmt: str,
                infographic_count: int, overlay_count: int) -> dict:
    safe_end = max(0.0, duration - OUTRO_RESERVED_SECONDS)
    user_msg = (
        f"Here is the Whisper verbose_json for this video.\n\n"
        f"VIDEO_FORMAT: {fmt}\n"
        f"VIDEO_DURATION_SECONDS: {duration:.1f}\n"
        f"OUTRO_STARTS_AT_SECONDS: {safe_end:.1f}  "
        f"(last {OUTRO_RESERVED_SECONDS:.0f}s is CRP outro — NEVER overlap it)\n"
        f"HOOK_WINDOW_SECONDS: 0..{HOOK_WINDOW_SECONDS:.0f}  "
        f"(exactly ONE text overlay must land in this window)\n\n"
        f"OUTPUT COUNTS FOR THIS {fmt}: exactly {infographic_count} infographic "
        f"moments and exactly {overlay_count} text overlay moments.\n\n"
        f"HARD CONSTRAINTS:\n"
        f"  * Every (timestamp + hold) MUST be <= {safe_end:.1f}. No item may "
        f"cross into the outro zone.\n"
        f"  * Place exactly {overlay_count} text overlays — ONE roughly every "
        f"~{OVERLAY_EVERY_SECONDS:.0f}s up to {safe_end:.1f}s. Each captures the "
        f"punchiest 1-3 word hook Stacey says in THAT window; do not repeat.\n"
        f"  * NO STATIC SCREEN: never leave the plain talking head alone for "
        f">{MAX_STATIC_GAP:.0f}s. Space overlays and infographics so a visual "
        f"element lands often enough that no bare-avatar gap exceeds "
        f"{MAX_STATIC_GAP:.0f}s.\n"
        f"  * A hook's window (timestamp..timestamp+hold) MUST NOT overlap any "
        f"infographic's window. Keep hooks and infographics on separate "
        f"moments with a clean gap — they must never share the screen.\n"
        f"  * Each text overlay holds ~3s (MAX 3). Mark ONLY the "
        f"1-3 single best scroll-stopping lines with \"peak\":true (they get "
        f"the rare black takeover); do not set any style field.\n"
        f"  * Each infographic holds 5-8s (punchy — never linger).\n"
        f"  * Space the {infographic_count} infographics across "
        f"{HOOK_WINDOW_SECONDS:.0f}..{safe_end:.1f}s to support the moments "
        f"where Stacey explains a process, list, timeline, comparison or stat, "
        f"well clear of each other.\n\n"
        f"TRANSCRIPT_JSON:\n{json.dumps(transcript)}"
    )
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-opus-4-5",
            "max_tokens": 4096,
            "system": CLAUDE_SYSTEM,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=300,
    )
    if not r.ok:
        raise RuntimeError(f"claude {r.status_code}: {r.text[:500]}")
    text = r.json()["content"][0]["text"].strip()
    first, last = text.find("{"), text.rfind("}")
    if first == -1 or last == -1:
        raise RuntimeError("No JSON object in Claude output")
    plan = json.loads(text[first : last + 1])

    # Safety net: drop anything that would land on top of the outro. For items
    # that start before the outro but would run past it, clip the hold so the
    # overlay fades out cleanly before the outro card appears. Log everything
    # we drop or clip.
    safe_end = max(0.0, duration - OUTRO_RESERVED_SECONDS)

    def _guard(items: list, kind: str, default_hold: float,
               min_hold: float = 0.0, max_hold: float = 1e9) -> list:
        kept, dropped, clipped = [], [], []
        for it in items or []:
            ts = float(it.get("timestamp", -1))
            # Clamp the planned hold into Larry's allowed band first.
            hold = min(max(float(it.get("hold", default_hold)), min_hold), max_hold)
            it["hold"] = hold
            if ts < 0 or ts >= safe_end:
                dropped.append(ts)
                continue
            end = ts + hold
            if end > safe_end:
                new_hold = max(1.0, safe_end - ts)
                it["hold"] = new_hold
                clipped.append((ts, hold, new_hold))
            kept.append(it)
        if dropped:
            log(f"dropped {len(dropped)} {kind} outside 0..{safe_end:.1f}s "
                f"(reserved last {OUTRO_RESERVED_SECONDS:.0f}s for outro): {dropped}")
        if clipped:
            log(f"clipped {len(clipped)} {kind} to avoid outro: {clipped}")
        return kept

    plan["infographics"] = _guard(plan.get("infographics"), "infographics",
                                   INFOGRAPHIC_HOLD,
                                   min_hold=INFOGRAPHIC_HOLD_MIN,
                                   max_hold=INFOGRAPHIC_HOLD_MAX)
    plan["text_overlays"] = _guard(plan.get("text_overlays"), "text_overlays",
                                    OVERLAY_HOLD, min_hold=MIN_OVERLAY_HOLD,
                                    max_hold=MAX_OVERLAY_HOLD)

    # HARD de-collision: a hook must NEVER overlap an infographic block or
    # another hook. Drop any that would (we have plenty of hooks), then
    # ALTERNATE styles so the black full-frame takeover ('big-text') lands
    # ~every 60s and the white transparent side overlay ('black-gradient')
    # fills the ~30s gaps — balanced, and always clear of the graphics.
    ig_windows = sorted(
        (float(g.get("timestamp", 0)),
         float(g.get("timestamp", 0)) + float(g.get("hold", INFOGRAPHIC_HOLD)))
        for g in plan["infographics"]
    )

    def _hits_ig(s: float, e: float) -> bool:
        return any(s < be + OVERLAY_IG_BUFFER and e > bs - OVERLAY_IG_BUFFER
                   for bs, be in ig_windows)

    kept_ov, last_end, dropped_ov = [], -1e9, 0
    for ov in sorted(plan["text_overlays"], key=lambda o: float(o.get("timestamp", 0))):
        s = float(ov.get("timestamp", 0))
        e = s + float(ov.get("hold", MIN_OVERLAY_HOLD))
        if _hits_ig(s, e) or s < last_end + 0.5:
            dropped_ov += 1
            continue
        kept_ov.append(ov)
        last_end = e
    # Style assignment: the black full-frame takeover ('big-text') is RARE and
    # reserved for the planner's very best "peak" hooks — the single most
    # scroll-stopping lines — spaced well apart. Everything else is the white
    # transparent side overlay. If the planner marked none, promote the opening
    # hook so the video still opens on a black impact beat.
    last_black = -1e9
    n_black = 0
    for ov in kept_ov:
        s = float(ov.get("timestamp", 0))
        if (ov.get("peak") and n_black < MAX_BLACK_HOOKS
                and s - last_black >= BLACK_MIN_GAP):
            ov["style"] = "big-text"
            last_black = s
            n_black += 1
        else:
            ov["style"] = "black-gradient"
    if n_black == 0 and kept_ov:
        kept_ov[0]["style"] = "big-text"
        n_black = 1
    if dropped_ov:
        log(f"de-collide: dropped {dropped_ov} overlay(s) overlapping an "
            f"infographic or another overlay")
    plan["text_overlays"] = kept_ov
    log(f"overlays: {len(kept_ov)} total — {n_black} rare black takeover(s), "
        f"{len(kept_ov) - n_black} white side; all clear of infographics")

    hook_count = sum(
        1 for ov in plan["text_overlays"]
        if float(ov.get("timestamp", 999)) < HOOK_WINDOW_SECONDS
    )
    if hook_count == 0:
        log(f"WARNING: no hook overlay in first {HOOK_WINDOW_SECONDS:.0f}s "
            f"(Claude should have placed one — check prompt output)")

    # STATIC-GAP AUDIT (Larry: no bare-avatar screen >~10s). We don't silently
    # inject filler — the planner is instructed to prevent this — but we log any
    # residual gap so a thin plan shows up in the run output instead of hiding.
    windows = sorted(
        (float(x.get("timestamp", 0)),
         float(x.get("timestamp", 0)) + float(x.get("hold", 0)))
        for x in (plan.get("infographics") or []) + (plan.get("text_overlays") or [])
    )
    gaps, cursor = [], 0.0
    for s, e in windows:
        if s - cursor > MAX_STATIC_GAP:
            gaps.append((round(cursor, 1), round(s, 1), round(s - cursor, 1)))
        cursor = max(cursor, e)
    if safe_end - cursor > MAX_STATIC_GAP:
        gaps.append((round(cursor, 1), round(safe_end, 1), round(safe_end - cursor, 1)))
    if gaps:
        log(f"STATIC-GAP WARNING: {len(gaps)} bare-avatar gap(s) > "
            f"{MAX_STATIC_GAP:.0f}s remain (start,end,len): {gaps}")
    else:
        log(f"static-gap check: OK — no bare-avatar gap > {MAX_STATIC_GAP:.0f}s")

    return plan


def build_assets(plan: dict) -> list[TimedClip]:
    """Render each planned moment as a TRUE animated cutaway clip.

    Infographics and text overlays are each rendered to their own opaque MP4
    (entrance motion baked in + final frame frozen to fill the hold). The
    compositor overlays these clips onto the base video — no static-PNG
    cross-fade fakery.
    """
    clips: list[TimedClip] = []
    for i, card in enumerate(plan.get("infographics") or []):
        hold = float(card.get("hold", INFOGRAPHIC_HOLD))
        clip = render_infographic_clip(
            card=card,
            font_dir=FONT_DIR,
            work_dir=WORK / f"ig_{i}_frames",
            clip_path=WORK / f"ig_{i}.mp4",
            entrance_secs=min(INFOGRAPHIC_ENTRANCE, hold),
            hold_secs=hold,
        )
        clips.append(TimedClip(clip=clip, start=float(card.get("timestamp", 0)), hold=hold))

    for i, ov in enumerate(plan.get("text_overlays") or []):
        hold = float(ov.get("hold", OVERLAY_HOLD))
        style = ov.get("style") or "black-gradient"
        # 'title' and 'black-gradient' are see-through side overlays (avatar
        # stays visible); 'big-text' is the opaque full-frame cutaway.
        transparent = style in ("title", "black-gradient")
        clip = render_overlay_clip(
            lines=list(ov.get("lines") or []),
            style=style,
            font_dir=FONT_DIR,
            work_dir=WORK / f"ov_{i}_frames",
            clip_path=WORK / f"ov_{i}.mp4",
            entrance_secs=min(OVERLAY_ENTRANCE, hold),
            hold_secs=hold,
        )
        clips.append(TimedClip(clip=clip, start=float(ov.get("timestamp", 0)),
                               hold=hold, transparent=transparent))

    clips.sort(key=lambda c: c.start)
    return clips


def main() -> None:
    WORK.mkdir(exist_ok=True)
    video = WORK / "input.mp4"
    audio = WORK / "audio.mp3"
    final = WORK / "final.mp4"

    log("[1/5] download OpusClip MP4")
    download_video(OPUS_URL, video)

    log("[2/5] extract audio")
    extract_audio(video, audio)

    duration = video_duration(video)
    fmt, card_count, overlay_count = detect_format(duration)
    log(f"video duration: {duration:.1f}s → format={fmt} "
        f"({card_count} cards + {overlay_count} overlays)")

    log("[3/5] whisper transcription")
    transcript = whisper_transcribe(audio)
    (WORK / "whisper.json").write_text(json.dumps(transcript))

    log("[4/5] claude planner")
    plan = claude_plan(transcript, duration, fmt, card_count, overlay_count)
    (WORK / "plan.json").write_text(json.dumps(plan, indent=2))
    log(f"plan: {len(plan.get('infographics') or [])} cards, "
        f"{len(plan.get('text_overlays') or [])} overlays")
    for i, c in enumerate(plan.get("infographics") or []):
        titles = [x.get("title") for x in (c.get("cards") or [])]
        log(f"  infographic {i}: t={c.get('timestamp')}s "
            f"overall_title={c.get('overall_title') or c.get('title')!r} "
            f"card_titles={titles}")
    for i, ov in enumerate(plan.get("text_overlays") or []):
        log(f"  overlay {i}: t={ov.get('timestamp')}s style={ov.get('style')!r} "
            f"lines={ov.get('lines')}")

    log("[5/5] composite")
    clips = build_assets(plan)
    composite_clips(video, clips, final)
    log(f"final → {final} ({final.stat().st_size / 1024 / 1024:.1f} MB)")

    # Rename to the target filename and put it at a stable location the
    # workflow YAML picks up for the Release upload step.
    filename = f"{VIDEO_ID}_CRP_MFLF.mp4"
    out = Path("out") / filename
    out.parent.mkdir(exist_ok=True)
    final.rename(out)
    log(f"ready for release upload: {out}")

    with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as f:
        f.write(f"filename={filename}\n")
        f.write(f"output_path={out}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FAILED: {type(e).__name__}: {e}")
        sys.exit(1)
