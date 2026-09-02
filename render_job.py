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
import re
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
from sf_render import render_sf_onto

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

# ── TRANSCRIPT SPELLING: UNDO OUR OWN PHONETICS ──────────────────────
# We feed ElevenLabs deliberate misspellings so a name is SAID correctly -
# "Bathla" is voiced from a phonetic form that sounds like BARTH-la. Whisper then
# transcribes what it hears, so the phonetic spelling comes back in the
# transcript, and every downstream consumer inherits it: the Claude planner, the
# infographic card titles, and the on-screen overlays.
#
# It shipped. The Bathla campaign video opened with a full-frame overlay reading
# "BATHLER / OWES YOU / MONEY" while the captions beneath it, which came from
# OpusClip and had been corrected by hand, read "IF THE BATHLA GROUP". Same
# moment, two spellings, one of them the client's name.
#
# Fixed at the transcript, not at the overlay. Correcting it later would fix the
# overlays and leave the planner still reasoning about a company called Bathler.
# Add a row here whenever a pronunciation dictionary gets a new proper noun.
TRANSCRIPT_SPELLING = [
    (r"\bBARTHLARS\b", "Bathla's"),
    (r"\bBATHLERS\b", "Bathla's"),
    (r"\bBARTHLAR\b", "Bathla"),
    (r"\bBARTHLER\b", "Bathla"),
    (r"\bBARTHLA\b", "Bathla"),
    (r"\bBATHLER\b", "Bathla"),
    (r"\bBATHLAR\b", "Bathla"),
]


def fix_spelling(text):
    """Undo phonetic spellings in one string. Case-insensitive, case-normalising."""
    if not text:
        return text
    for pattern, replacement in TRANSCRIPT_SPELLING:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def normalise_transcript(transcript: dict) -> dict:
    """Apply fix_spelling to every text field Whisper returns.

    verbose_json carries the same words in three places - the flat `text`, each
    `segments[].text`, and each `words[].word` (both top level and per segment).
    Miss one and a stale spelling survives in whichever field a consumer reads.
    """
    if not isinstance(transcript, dict):
        return transcript

    def fix_words(container):
        for w in (container.get("words") or []):
            if isinstance(w, dict) and "word" in w:
                w["word"] = fix_spelling(w["word"])

    if "text" in transcript:
        transcript["text"] = fix_spelling(transcript["text"])
    fix_words(transcript)
    for seg in (transcript.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        if "text" in seg:
            seg["text"] = fix_spelling(seg["text"])
        fix_words(seg)
    return transcript


# ── COLUMN F: ON-SCREEN SUBSTITUTIONS (Larry v2 dictionary) ──────────────────
# The avatar speaks plain English ("director penalty notices", "the tax office")
# so the voice sounds professional. On-screen text overlays, however, must use
# the punchy acronym ("DPN", "ATO") so the visual stays short and doesn't
# overtake the avatar's face. This is Larry's Column F "on-screen display"
# layer from CRP_AU_Script_Dictionary_v2.xlsx. Applied deterministically to
# every overlay line after Claude plans them — not left to the AI to comply.
COLUMN_F_SUBSTITUTIONS = [
    (r"\bDIRECTOR PENALTY NOTICES\b", "DPNs"),
    (r"\bDIRECTOR PENALTY NOTICE\b", "DPN"),
    (r"\bTHE TAX OFFICE\b", "ATO"),
    (r"\bBUSINESS ACTIVITY STATEMENTS\b", "BAS"),
    (r"\bBUSINESS ACTIVITY STATEMENT\b", "BAS"),
    (r"\bPAY[- ]AS[- ]YOU[- ]GO TAX\b", "PAYG"),
    (r"\bPAY AS YOU GO TAX\b", "PAYG"),
    (r"\bGOODS AND SERVICES TAX\b", "GST"),
    (r"\bSECURITY OF PAYMENT ACT\b", "SOPA"),
    (r"\bSECURITY OF PAYMENT\b", "SOPA"),
    (r"\bA DEED OF COMPANY ARRANGEMENT\b", "DOCA"),
    (r"\bDEED OF COMPANY ARRANGEMENT\b", "DOCA"),
    (r"\bVOLUNTARY ADMINISTRATION\b", "VA"),
    (r"\bTHE CORPORATE REGULATOR\b", "ASIC"),
    (r"\bAUSTRALIAN DOLLARS\b", "AUD"),
    (r"\bAUSTRALIAN DOLLAR\b", "AUD"),
    (r"\bPURCHASE ORDER NUMBER\b", "PO NUMBER"),
    (r"\bPURCHASE ORDER\b", "PO"),
    (r"\bSUPERANNUATION GUARANTEE\b", "SGC"),
]

# On-screen text is bounded HARD. The AI planner sometimes emits long lines
# (e.g. "85,000 DIRECTOR PENALTY NOTICES") which cover the avatar's face when
# rendered full-frame as a peak hook. We enforce below the AI's judgement.
MAX_OVERLAY_WORDS = 3       # per line, for standard black-gradient overlays
MAX_OVERLAY_CHARS = 14      # per line, matches the AI prompt's stated limit
MAX_PEAK_WORDS = 3          # per line, for full-frame peak (big-text) overlays
MAX_PEAK_CHARS = 12         # per line, for full-frame peak - extra strict
#
# MAX_PEAK_WORDS was 2. The CHARACTER cap is the real visual constraint - it is
# what stops text covering the avatar - and at 12 it is already stricter for peak
# than the 14 used elsewhere. The word cap added nothing on top of it and did
# real damage: "TO THE END" is ten characters but three words, so it was silently
# chopped to "TO THE" and the Bathla campaign video shipped a hook reading
# "OWED MONEY? / WATCH THIS / TO THE". Larry saw it before we did.


def apply_column_f(text: str) -> str:
    """Substitute long spoken phrases with punchier on-screen acronyms."""
    if not text:
        return text
    upper = text.upper()
    for pattern, replacement in COLUMN_F_SUBSTITUTIONS:
        upper = re.sub(pattern, replacement, upper)
    return upper


def enforce_overlay_lines(lines, is_peak: bool = False):
    """Fit an overlay's text to the caps by REFLOWING it, never by truncating.

    The previous version capped each line independently and threw away whatever
    did not fit. That silently produced fragments, and they went to air:

        planner  ["OWED MONEY?", "WATCH THIS", "TO THE END"]
        shipped   OWED MONEY? / WATCH THIS / TO THE          <- sentence beheaded

        planner  ["85,000", "DIRECTOR PENALTY", "NOTICES"]
        shipped   85,000 / DIRECTOR PEN / NOTICES            <- word chopped in half

    Truncation is never the right answer for on-screen copy: a cut-off line reads
    to a viewer as a broken render, which is worse than no overlay at all. So the
    words are re-flowed across the available lines, and if they genuinely cannot
    fit, the overlay is DROPPED and the avatar plays clean.
    """
    max_words = MAX_PEAK_WORDS if is_peak else MAX_OVERLAY_WORDS
    max_chars = MAX_PEAK_CHARS if is_peak else MAX_OVERLAY_CHARS

    joined = " ".join(str(l).strip() for l in (lines or []) if str(l).strip())
    words = apply_column_f(joined).split()
    if not words:
        return []

    out = []
    cur = ""
    for w in words:
        if len(w) > max_chars:
            # A single word wider than the frame allows. Clipping it would show
            # half a word, so drop the whole overlay instead.
            return []
        cand = (cur + " " + w).strip()
        if len(cand) <= max_chars and len(cand.split()) <= max_words:
            cur = cand
            continue
        out.append(cur)
        cur = w
        if len(out) == 3:
            return []
    if cur:
        out.append(cur)
    return out if len(out) <= 3 else []


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
    "TEXT OVERLAY SCHEMA: exactly 3 lines per overlay; each line 1-3 words, "
    "<=14 chars. Punchy. Rule of 3. Break aggressively: "
    "'THE RELATIONSHIP IS DAMAGED YOU FEEL IT' becomes "
    "['RELATIONSHIP', 'ALREADY DAMAGED', 'YOU FEEL IT']. "
    "  ON-SCREEN SUBSTITUTIONS (Larry's Column F — HARD): when the avatar says "
    "a long spoken phrase, the on-screen text MUST use the acronym version "
    "so the visual stays short and does NOT overtake the avatar's face:\n"
    "    'director penalty notice(s)' -> 'DPN' / 'DPNs'\n"
    "    'the tax office' -> 'ATO'\n"
    "    'business activity statement(s)' -> 'BAS'\n"
    "    'pay-as-you-go tax' -> 'PAYG'\n"
    "    'goods and services tax' -> 'GST'\n"
    "    'security of payment (act)' -> 'SOPA'\n"
    "    'a deed of company arrangement' -> 'DOCA'\n"
    "    'voluntary administration' -> 'VA'\n"
    "    'personal property (securities) register' -> 'PPSR'\n"
    "    'Australian dollars' -> 'AUD'\n"
    "  NEVER put a full spoken phrase like '85,000 DIRECTOR PENALTY NOTICES' "
    "on-screen — write '85,000 DPNs' instead. Keep the frame clear of the "
    "avatar. This is enforced post-plan; failing to comply means your line "
    "will be truncated automatically.\n"
    "  PEAK OVERLAY CHAR LIMIT (HARDER): peak-marked overlays go FULL-FRAME "
    "opaque covering the avatar. Each peak line MUST be 1-2 words AND <=12 "
    "chars. If your punchy line needs more, DO NOT set peak=true — use the "
    "black-gradient side overlay instead so the avatar stays visible.\n"
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


def video_dimensions(video: Path) -> tuple[int, int]:
    """Return (width, height) of the first video stream via ffprobe."""
    r = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(video),
        ],
        capture_output=True, text=True, check=True,
    )
    w, h = r.stdout.strip().split("x")[:2]
    return int(w), int(h)


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
    # Correct our own phonetics before ANY consumer sees the transcript.
    return normalise_transcript(r.json())


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

    # Column F substitution + hard char/word cap enforcement.
    # Runs AFTER style assignment so peak overlays get the stricter cap.
    # This is what prevents "85,000 DIRECTOR PENALTY NOTICES" from ever
    # reaching the compositor as a full-frame black takeover covering the
    # avatar — long spoken phrases collapse to acronyms, and anything still
    # over the limit gets truncated. Deterministic — not left to the AI.
    substituted = 0
    truncated = 0
    for ov in plan["text_overlays"]:
        original = list(ov.get("lines", []) or [])
        is_peak = ov.get("style") == "big-text"
        new_lines = enforce_overlay_lines(original, is_peak=is_peak)
        if new_lines != [str(x).strip().upper() for x in original[:3] if str(x).strip()]:
            substituted += 1
        for orig, new in zip(original, new_lines):
            if len(str(orig)) > len(new):
                truncated += 1
        ov["lines"] = new_lines
    if substituted or truncated:
        log(f"column F: {substituted} overlay(s) had lines substituted; "
            f"{truncated} line(s) truncated to fit cap")

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


# ── SF (portrait 9:16) path ─────────────────────────────────────────────────
# Larry-approved short-form on-screen text: pure white ALL-CAPS 1-3 word hooks,
# placed top / center / lower-third / bottom (content-aware), 0.5s fades. The
# planner picks the hooks VERBATIM from the clip's own speech.
SF_SYSTEM = (
    "You are the CRP short-form (SF) on-screen-text planner. You read a Whisper "
    "verbose_json transcript of a ~1 minute vertical (9:16) Australian "
    "construction debt-recovery clip and choose the punchiest on-screen-text "
    "'hooks'.\n\n"
    "STYLE (Larry-locked): white ALL-CAPS text, HARD LIMIT 1-3 words TOTAL per "
    "hook (never 4, never 'lines that stack' — one short phrase, one line, "
    "period), taken VERBATIM from what the presenter actually says — never "
    "paraphrase, never invent words. Punchy and scroll-stopping.\n\n"
    "EACH HOOK MUST READ AS A COMPLETE THOUGHT ON ITS OWN. It is shown alone "
    "on screen with nothing before or after it, so a hook that hangs mid-idea "
    "looks like a broken render, which is the one thing Larry has asked us to "
    "fix. Choose the 1-3 words INSIDE the sentence that carry the punch, not "
    "the first 1-3 words of it.\n\n"
    "  GOOD: NO PHONE CALL / 21 DAYS / PERSONALLY LIABLE / THEY DONT PAY\n"
    "  BAD:  YOU GET NO (hangs) / THE MONEY GOES (hangs) / GOES STRAIGHT "
    "(starts mid-clause) / OF PAYMENT (starts on a preposition) / OFFICE "
    "(a stray word, not a thought)\n\n"
    "Never END a hook on: the, a, an, of, to, and, or, your, you, is, are, "
    "get, no, goes, still, that. Never START one on: and, or, of, to, that, "
    "is, are. If the punchy words cannot be cut to 3 and still read whole, "
    "pick a different moment in the clip instead.\n\n"
    "WHAT TO PICK: the emotional / high-impact peaks — the lines that make "
    "someone stop scrolling (e.g. STILL OWE, THEY DON'T PAY, YOU WAITED, GET "
    "PAID, 30 DAYS). Cut each to 1-3 words. Space them roughly one every "
    "8-12 seconds across the clip.\n\n"
    "QUOTA: 4-6 hooks for a ~60s clip (about one per 10s); never fewer than 3. "
    "Mark EXACTLY ONE — the single most impactful gut-punch line — with "
    "\"punchy\":true (it gets the big centered treatment). Never flag more than "
    "one.\n\n"
    "TIMING: timestamp = the second the presenter says that idea (use the "
    "transcript word timings). Keep the LAST 4 seconds clear for the CTA end "
    "frame — no hook may land there.\n\n"
    "Return ONLY a JSON object and nothing else:\n"
    "{\"text_overlays\":[{\"timestamp\":<seconds>,\"lines\":[\"WORD\",\"WORD\"],"
    "\"punchy\":<true for the single best; omit otherwise>}]}"
)


# Words a hook must not END on: the phrase is left hanging and reads as a
# truncation even though every word is complete. Larry's complaint about
# "WATCH THIS TO THE" is this exact shape. Also words it must not START on,
# which produce the mirror-image dangle ("OF PAYMENT", "AND SUPPLIERS").
_SF_DANGLE_END = {
    "the", "a", "an", "of", "to", "in", "on", "at", "by", "for", "with", "from",
    "and", "or", "but", "if", "so", "than", "that", "this", "these", "those",
    "your", "you", "my", "our", "their", "his", "her", "its", "it",
    "is", "are", "was", "were", "be", "been", "am", "do", "does", "did",
    "have", "has", "had", "will", "would", "can", "could", "should", "may",
    "get", "gets", "got", "no", "not", "very", "just", "still", "any", "some",
    "goes", "go", "went", "come", "comes", "came", "keep", "keeps", "make",
    "makes", "take", "takes", "want", "wants", "need", "needs", "see", "sees",
    "too", "straight", "every", "more", "most", "own", "same", "both", "each",
    "into", "over", "under", "about", "before", "after", "when", "where",
    "how", "why", "what", "who", "there", "here",
}
_SF_DANGLE_START = {
    "and", "or", "but", "of", "to", "in", "on", "at", "by", "for", "with",
    "from", "than", "that", "so", "if", "is", "are", "was", "were", "be",
    "been", "am", "do", "does", "did", "have", "has", "had", "will", "would",
    "can", "could", "should", "may", "get", "gets", "got", "goes", "go",
    "went", "keep", "keeps", "make", "makes", "take", "takes", "see", "sees",
    "want", "wants", "need", "needs", "just", "very", "any", "some",
}


def sf_best_phrase(phrase: str, cap: int = 3):
    """Pick the best COMPLETE sub-phrase of `phrase` at up to `cap` words.

    The old behaviour was `" ".join(words[:cap])` - a blind cut at three words.
    That is what manufactured the unfinished hooks: "THE MONEY GOES STRAIGHT
    THERE" became "THE MONEY GOES", and "YOU GET NO PHONE CALL" became "YOU GET
    NO", when the strong hook sitting inside it was "NO PHONE CALL".

    So instead of cutting at a fixed position, consider every contiguous window
    of 1..cap words and score them. A window is only eligible if it does not
    dangle at either end. Among the eligible ones, prefer more words (more
    context), then the LATEST window - in speech the payoff lands at the end of
    the clause, so "you get no PHONE CALL" and "it is TOO LATE" are the hooks,
    not "GET NO PHONE" and "IT IS TOO".

    Returns (phrase, None) on success or (None, reason) when nothing in the
    hook reads as a complete thought - in which case the caller drops it.
    Dropping is right: a missing hook is invisible, a truncated one is the
    defect Larry asked us to remove.
    """
    words = phrase.split()
    if not words:
        return None, "empty"

    def dangles(ws):
        return (ws[-1].strip(".,!?:;'\"").lower() in _SF_DANGLE_END
                or ws[0].strip(".,!?:;'\"").lower() in _SF_DANGLE_START)

    if len(words) <= cap and not dangles(words):
        return " ".join(words), None

    best = None
    for n in range(min(cap, len(words)), 0, -1):
        for i in range(0, len(words) - n + 1):
            win = words[i : i + n]
            if dangles(win):
                continue
            score = (n, i)                      # longer first, then LATEST
            if best is None or score > best[0]:
                best = (score, " ".join(win))
        if best is not None:
            break                               # longest clean window wins
    if best is None:
        return None, "no complete phrase within %d words" % cap
    return best[1], None


def sf_plan(transcript: dict, duration: float) -> dict:
    """Whisper transcript -> SF OST plan (short punchy hooks). SF path only."""
    safe_end = max(0.0, duration - 4.0)
    user_msg = (
        f"Whisper verbose_json for a {duration:.0f}s vertical SF clip.\n"
        f"Every hook timestamp must be <= {safe_end:.1f} (the last 4s is the CTA "
        f"end frame). Pick 4-6 verbatim 1-3 word hooks; flag exactly one "
        f"punchy.\n\nTRANSCRIPT_JSON:\n{json.dumps(transcript)}"
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
            "max_tokens": 1500,
            "system": SF_SYSTEM,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=180,
    )
    if not r.ok:
        raise RuntimeError(f"claude SF plan {r.status_code}: {r.text[:500]}")
    text = r.json()["content"][0]["text"].strip()
    first, last = text.find("{"), text.rfind("}")
    if first == -1 or last == -1:
        raise RuntimeError("No JSON object in SF planner output")
    plan = json.loads(text[first : last + 1])

    kept, dropped, punchy_seen, trimmed = [], 0, False, 0
    for ov in plan.get("text_overlays") or []:
        ts = float(ov.get("timestamp", -1))
        if ts < 0 or ts > safe_end:
            dropped += 1
            continue
        # Enforce Larry's HARD 1-3 word cap: flatten `lines` to a single line
        # and trim to the first 3 words. Prevents 4+ word hooks (like
        # "STILL OWE YOU MONEY") that force awkward multi-line wraps.
        raw = ov.get("lines")
        if isinstance(raw, list):
            phrase = " ".join(str(x) for x in raw if x).strip()
        else:
            phrase = str(ov.get("text") or "").strip()
        fixed, why = sf_best_phrase(phrase, cap=3)
        if fixed is None:
            log(f"SF plan: dropped hook {phrase!r} ({why})")
            dropped += 1
            continue
        if fixed != phrase:
            log(f"SF plan: reshaped {phrase!r} -> {fixed!r}")
            trimmed += 1
        ov["lines"] = [fixed]
        if ov.get("punchy"):
            if punchy_seen:          # enforce a single centered punchy line
                ov.pop("punchy", None)
            else:
                punchy_seen = True
        kept.append(ov)
    kept.sort(key=lambda o: float(o.get("timestamp", 0)))
    plan["text_overlays"] = kept
    if dropped:
        log(f"SF plan: dropped {dropped} hook(s) outside 0..{safe_end:.1f}s")
    if trimmed:
        log(f"SF plan: reshaped {trimmed} hook(s) to a complete phrase within 3 words")
    log(f"SF plan: {len(kept)} OST hook(s), punchy={'yes' if punchy_seen else 'none'}")
    return plan


def render_sf_portrait(video: Path, audio: Path, w: int, h: int) -> None:
    """SF (portrait 9:16) branch: auto-detected -> Whisper -> SF OST -> composite."""
    log(f"portrait {w}x{h} detected -> SF on-screen-text path")

    log("[2/4] extract audio")
    extract_audio(video, audio)
    duration = video_duration(video)
    log(f"SF clip duration: {duration:.1f}s")

    log("[3/4] whisper transcription")
    transcript = whisper_transcribe(audio)
    (WORK / "whisper.json").write_text(json.dumps(transcript))

    plan = sf_plan(transcript, duration)
    (WORK / "plan.json").write_text(json.dumps(plan, indent=2))
    for i, ov in enumerate(plan.get("text_overlays") or []):
        log(f"  OST {i}: t={ov.get('timestamp')}s punchy={bool(ov.get('punchy'))} "
            f"lines={ov.get('lines')}")

    log("[4/4] composite (SF portrait)")
    final = render_sf_onto(video, plan, WORK, FONT_DIR)

    filename = f"{VIDEO_ID}_CRP_SF.mp4"
    out = Path("out") / filename
    out.parent.mkdir(exist_ok=True)
    final.rename(out)
    log(f"ready for release upload: {out}")

    with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as f:
        f.write(f"filename={filename}\n")
        f.write(f"output_path={out}\n")


def main() -> None:
    WORK.mkdir(exist_ok=True)
    video = WORK / "input.mp4"
    audio = WORK / "audio.mp3"
    final = WORK / "final.mp4"

    log("[1/5] download OpusClip MP4")
    download_video(OPUS_URL, video)

    # Auto-detect orientation: portrait (9:16) -> SF on-screen-text path;
    # landscape stays on the MF/LF flow below, unchanged.
    w, h = video_dimensions(video)
    if h > w:
        render_sf_portrait(video, audio, w, h)
        return

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
