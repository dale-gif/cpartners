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
    INFOGRAPHIC_X,
    INFOGRAPHIC_Y,
    OVERLAY_HOLD,
    OVERLAY_X,
    OVERLAY_Y,
    TimedAsset,
    composite,
)
from graphic_cards import render_card
from kinetic_words import render_overlay

OPUS_URL = os.environ["OPUS_CLIP_URL"]
VIDEO_ID = os.environ["VIDEO_ID"]
OPENAI_KEY = os.environ["OPENAI_API_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
FONT_DIR = Path(os.environ.get("FONT_DIR", "fonts"))
WORK = Path("work")

CLAUDE_SYSTEM = (
    "You are the GODTIER graphic planner for CRP videos. "
    "You read a Whisper verbose_json transcript and emit a JSON plan that "
    "chooses infographic moments and text overlay moments. "
    "STRICT DESIGN RULES: pure black and white only; bordered cards only; "
    "CAPS titles; exactly 3 items per infographic card unless Stacey names a "
    "different count (match her spoken count exactly); hold cards 6-10 seconds; "
    "appear (fade/scale), never slide; no kickers, page counters, or type labels; "
    "large margined type. TEXT OVERLAYS: exactly 3 lines per overlay; plain text; "
    "Inter 800; 104px at 1920 wide; sync to speech; style is either 'white' "
    "(white text over Stacey) or 'black-gradient' (white text on black gradient "
    "blended into her). FRAME: Stacey is frame-right; graphics/text land "
    "frame-left; keep clear of the bottom caption band. "
    "LENGTH LIMITS (hard): infographic title <= 22 characters (fits on one line at "
    "big type); infographic item <= 24 characters; text overlay line <= 18 "
    "characters (fits big and readable). If you can't fit the idea, break it "
    "into fewer words or use a punchier phrasing. "
    "Return ONLY a single JSON object matching this schema and nothing else: "
    "{\"infographics\":[{\"timestamp\":<seconds>,\"template\":\"bordered-list\","
    "\"title\":\"CAPS TITLE\",\"items\":[\"ITEM 1\",\"ITEM 2\",\"ITEM 3\"]}],"
    "\"text_overlays\":[{\"timestamp\":<seconds>,\"lines\":[\"line one\","
    "\"line two\",\"line three\"],\"style\":\"white|black-gradient\"}]}"
)

# Duration thresholds for auto-format detection (seconds).
# CRP formats: MF = medium form (3-6 min), LF = long form (6-12 min).
LF_MIN_SECONDS = 360.0  # 6 min


def detect_format(duration: float) -> tuple[str, int, int]:
    """Return (format_label, infographic_count, text_overlay_count)."""
    if duration < LF_MIN_SECONDS:
        return "MF", 2, 2
    return "LF", 3, 3


def log(msg: str) -> None:
    print(f"[godtier] {msg}", flush=True)


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
    user_msg = (
        f"Here is the Whisper verbose_json for this video.\n\n"
        f"VIDEO_FORMAT: {fmt}\n"
        f"VIDEO_DURATION_SECONDS: {duration:.1f}\n\n"
        f"OUTPUT COUNTS FOR THIS {fmt}: exactly {infographic_count} infographic "
        f"moments and exactly {overlay_count} text overlay moments.\n\n"
        f"HARD CONSTRAINT: every timestamp you emit MUST be a number between "
        f"0 and {duration:.1f}. Distribute the {infographic_count} infographics "
        f"and {overlay_count} text overlays evenly across that range. Do not "
        f"use timestamps past {duration:.1f}s — the video ends there.\n\n"
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

    # Safety net: drop anything past the end. Log what we drop.
    def _in_range(items: list, kind: str) -> list:
        kept, dropped = [], []
        for it in items or []:
            ts = float(it.get("timestamp", -1))
            if 0 <= ts < duration:
                kept.append(it)
            else:
                dropped.append(ts)
        if dropped:
            log(f"dropped {len(dropped)} {kind} outside 0..{duration:.1f}s: {dropped}")
        return kept
    plan["infographics"] = _in_range(plan.get("infographics"), "infographics")
    plan["text_overlays"] = _in_range(plan.get("text_overlays"), "text_overlays")
    return plan


def build_assets(plan: dict) -> list[TimedAsset]:
    assets: list[TimedAsset] = []
    for i, card in enumerate(plan.get("infographics") or []):
        png = render_card(
            title=card.get("title") or "",
            items=list(card.get("items") or [])[:5],
            font_dir=FONT_DIR,
            out_path=WORK / f"card_{i}.png",
        )
        assets.append(TimedAsset(
            png=png,
            start=float(card.get("timestamp", 0)),
            hold=float(card.get("hold", INFOGRAPHIC_HOLD)),
            x=INFOGRAPHIC_X, y=INFOGRAPHIC_Y,
        ))
    for i, ov in enumerate(plan.get("text_overlays") or []):
        png = render_overlay(
            lines=list(ov.get("lines") or []),
            style=(ov.get("style") or "white"),
            font_dir=FONT_DIR,
            out_path=WORK / f"overlay_{i}.png",
        )
        assets.append(TimedAsset(
            png=png,
            start=float(ov.get("timestamp", 0)),
            hold=float(ov.get("hold", OVERLAY_HOLD)),
            x=OVERLAY_X, y=OVERLAY_Y,
        ))
    assets.sort(key=lambda a: a.start)
    return assets


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
        log(f"  card {i}: t={c.get('timestamp')}s title={c.get('title')!r} "
            f"items={c.get('items')}")
    for i, ov in enumerate(plan.get("text_overlays") or []):
        log(f"  overlay {i}: t={ov.get('timestamp')}s style={ov.get('style')!r} "
            f"lines={ov.get('lines')}")

    log("[5/5] composite")
    assets = build_assets(plan)
    composite(video, assets, final)
    log(f"final → {final} ({final.stat().st_size / 1024 / 1024:.1f} MB)")

    # Rename to the target filename and put it at a stable location the
    # workflow YAML picks up for the Release upload step.
    filename = f"{VIDEO_ID}_GODTIER.mp4"
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
