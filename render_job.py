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
# ENTRANCE_SECS of that.
INFOGRAPHIC_ENTRANCE = 3.0
OVERLAY_ENTRANCE = 1.6

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
    "punchy phrasing; hold 6-10s; appear (fade/scale), never slide; no kickers, "
    "no page counters, no type labels, no emoji. Keep it minimal. Focus on "
    "clarity. Clean. Professional. Reusable.\n\n"
    "RULE OF 3 (LOCKED): every infographic contains EXACTLY 3 cards. Never 2, "
    "never 4, never 5. If Stacey lists more than 3 things, pick the 3 most "
    "important. If she lists fewer, expand to 3 supporting beats. The overall "
    "title MUST NOT reference a count that isn't 3 — avoid 'THE FOUR STEPS' / "
    "'FIVE THINGS'. Prefer counted framings only when they say THREE ('THREE "
    "WAYS TO GET PAID') or use countless framings ('WHY THEY DON'T PAY', "
    "'PATHWAYS TO GET PAID', 'WHAT GOOD LOOKS LIKE').\n\n"
    "INFOGRAPHIC SCHEMA (per moment):\n"
    "  timestamp      : seconds — when Stacey STARTS explaining this concept.\n"
    "  hold           : seconds — how long the card stays up. Match how long "
    "                  Stacey spends on this concept. Typical: 15-30 seconds "
    "                  for a multi-part explanation, up to 40s if she goes deep. "
    "                  The card should stay on-screen for the WHOLE discussion, "
    "                  not just the moment she introduces it.\n"
    "  overall_title  : 1-3 words CAPS, <= 22 chars. Example: WHY THEY DON'T PAY\n"
    "  subtitle       : short CAPS phrase, <= 40 chars. Example: IT IS A CHOICE, NOT AN ACCIDENT\n"
    "  template       : PICK ONE — see TEMPLATES below\n"
    "  cards[3]       : EXACTLY 3. Each: title 1-2 words CAPS <=12 chars; "
    "                  description 2-3 short phrases CAPS <=60 chars total.\n\n"
    "TEMPLATES (pick the one that best fits the beat):\n"
    "  * three-cards      — DEFAULT. 3 bordered cards side-by-side with centered numbers, dividers, titles. Use for lists, principles, categories.\n"
    "  * three-columns    — 3 columns with circle icon placeholders + vertical dividers between columns. Use for tips or benefits.\n"
    "  * timeline         — 3 numbered CIRCLES connected by a horizontal line. Use for sequential steps or a journey.\n"
    "  * numbered-list    — Vertical 01/02/03 rows with divider bars + horizontal separators. Use for instructions or ranked items.\n"
    "  * circle-diagram   — 3-segment ring on left + numbered legend with dividers on right. Use for parts of a whole or overlapping ideas.\n"
    "  * problem-solution — Problem / Cause / Solution with large icon boxes (warning, magnifier, badge). Use for issue analysis.\n"
    "  * checklist        — 3 rows with plus-sign icons in bordered squares + horizontal separators. Use for best-practices or a to-do.\n\n"
    "TEXT OVERLAY SCHEMA: exactly 3 lines per overlay; each line 1-5 words, "
    "<=14 chars. Punchy. Rule of 3. Break aggressively: "
    "'THE RELATIONSHIP IS DAMAGED YOU FEEL IT' becomes "
    "['RELATIONSHIP', 'ALREADY DAMAGED', 'YOU FEEL IT']. "
    "  timestamp : seconds — when the line lands.\n"
    "  hold      : seconds — how long the text stays up. Typical 4-8s. "
    "              Long enough to read, short enough to feel like a beat.\n"
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
    "OUTRO (HARD): the last 15 seconds of the video are the CRP outro "
    "(\"DM US TODAY\" + disclaimer). NEVER place an infographic or text "
    "overlay whose (timestamp + hold) crosses into the last 15s. Keep every "
    "moment fully clear of that zone.\n\n"
    "HOOK OVERLAY (HARD): exactly ONE text overlay MUST land in the first 20 "
    "seconds. Pick the sentiment peak of the opening beat — the punchiest "
    "1-3 word phrase Stacey says early (e.g. 'DON\\'T WAIT', 'IT COSTS YOU', "
    "'STOP GUESSING'). Break into 3 lines of 1-3 words each using the Rule "
    "of 3 (e.g. ['DON\\'T', 'WAIT', 'ACT NOW']). The hook overlay MUST use "
    "style 'big-text' — massive centered text on black for maximum impact.\n\n"
    "TITLE CARD (HARD): exactly ONE text overlay MUST use style 'title'. It is "
    "a section/chapter title that introduces the main topic — a 2-4 word CAPS "
    "phrase naming what this segment is about (e.g. 'THE PHOENIX TRAP', 'HOW "
    "IT HAPPENS', 'WHAT YOU CAN DO'). Place it at a natural section start — "
    "right after the hook (roughly 20-40s in) or at the first clear topic "
    "transition. Give it 1-2 lines. This is separate from and in addition to "
    "the hook; the hook is 'big-text', the title card is 'title'.\n\n"
    "Return ONLY a single JSON object matching this schema and nothing else:\n"
    "{\"infographics\":["
    "{\"timestamp\":<seconds>,\"hold\":<seconds>,"
    "\"template\":\"three-cards|three-columns|timeline|"
    "numbered-list|circle-diagram|problem-solution|checklist\","
    "\"overall_title\":\"CAPS\",\"subtitle\":\"CAPS\","
    "\"cards\":[{\"title\":\"CAPS\",\"description\":\"CAPS\"},"
    "{\"title\":\"CAPS\",\"description\":\"CAPS\"},"
    "{\"title\":\"CAPS\",\"description\":\"CAPS\"}]}"
    "],"
    "\"text_overlays\":["
    "{\"timestamp\":<seconds>,\"hold\":<seconds>,"
    "\"lines\":[\"LINE 1\",\"LINE 2\",\"LINE 3\"],"
    "\"style\":\"black-gradient|big-text|title\"}"
    "]}"
)

# Duration thresholds for auto-format detection (seconds).
# CRP formats: MF = medium form (3-6 min), LF = long form (6-12 min).
LF_MIN_SECONDS = 360.0  # 6 min

# Reserve the last N seconds for CRP's outro card ("DM US TODAY" + disclaimer).
# No overlay or infographic may extend into this zone.
OUTRO_RESERVED_SECONDS = 15.0

# Every clip must have at least one punchy text overlay ("hook") landing in
# the first N seconds, capturing the sentiment peak of the opening beat.
HOOK_WINDOW_SECONDS = 20.0


def detect_format(duration: float) -> tuple[str, int, int]:
    """Return (format_label, infographic_count, text_overlay_count).

    MF (< 6 min): 2 cards + 3 text overlays.
    LF (≥ 6 min): 3 cards + 6 text overlays.
    """
    if duration < LF_MIN_SECONDS:
        return "MF", 2, 3
    return "LF", 3, 6


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
        f"  * Exactly one of the {overlay_count} text overlays MUST have "
        f"timestamp < {HOOK_WINDOW_SECONDS:.0f} (the opening hook). Capture "
        f"the sentiment peak in 1-3 words per line.\n"
        f"  * Distribute the remaining {infographic_count} infographics and "
        f"{overlay_count - 1} body overlays evenly across "
        f"{HOOK_WINDOW_SECONDS:.0f}..{safe_end:.1f}s.\n\n"
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

    def _guard(items: list, kind: str, default_hold: float) -> list:
        kept, dropped, clipped = [], [], []
        for it in items or []:
            ts = float(it.get("timestamp", -1))
            hold = float(it.get("hold", default_hold))
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

    plan["infographics"] = _guard(plan.get("infographics"), "infographics", INFOGRAPHIC_HOLD)
    plan["text_overlays"] = _guard(plan.get("text_overlays"), "text_overlays", OVERLAY_HOLD)

    hook_count = sum(
        1 for ov in plan["text_overlays"]
        if float(ov.get("timestamp", 999)) < HOOK_WINDOW_SECONDS
    )
    if hook_count == 0:
        log(f"WARNING: no hook overlay in first {HOOK_WINDOW_SECONDS:.0f}s "
            f"(Claude should have placed one — check prompt output)")
    elif hook_count > 1:
        log(f"note: {hook_count} overlays landed in the hook window "
            f"(only 1 required, but fine)")

    title_count = sum(
        1 for ov in plan["text_overlays"] if ov.get("style") == "title"
    )
    if title_count == 0:
        log("WARNING: no 'title' style overlay (Claude must include exactly one "
            "section title card — check prompt output)")
    else:
        log(f"title card(s): {title_count}")
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
        clip = render_overlay_clip(
            lines=list(ov.get("lines") or []),
            style=(ov.get("style") or "big-text"),
            font_dir=FONT_DIR,
            work_dir=WORK / f"ov_{i}_frames",
            clip_path=WORK / f"ov_{i}.mp4",
            entrance_secs=min(OVERLAY_ENTRANCE, hold),
            hold_secs=hold,
        )
        clips.append(TimedClip(clip=clip, start=float(ov.get("timestamp", 0)), hold=hold))

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
