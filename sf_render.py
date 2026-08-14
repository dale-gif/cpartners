"""SF portrait render orchestrator: plan JSON + portrait base MP4 -> final MP4.

Self-contained SF (9:16) path. Burns the Larry-approved on-screen-text
placements (sf_overlay) onto a portrait avatar clip via ffmpeg, with a 0.5s
alpha fade in/out on every line (never slide) — the same locked transition
timing as MF/LF, at portrait dimensions.

This module does NOT import or modify the landscape MF/LF path
(godtier_lf / compose_from_plan / kinetic_words). Turning SF on cannot change
MF output.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

from sf_overlay import assign_placements, render_sf_overlay

W, H = 1080, 1920           # portrait 9:16
FADE = 0.5                  # alpha fade in/out (Larry: ~12 frames @25fps, never slide)
OVERLAY_HOLD = 2.5          # SF on-screen text hold (punchy; Larry keeps text <=3s)


@dataclass
class _Timed:
    png: Path
    start: float
    hold: float


def _download(url: str, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            shutil.copyfileobj(r.raw, f)
    return out


def _overlay_text(ov: dict) -> str:
    lines = ov.get("lines")
    if isinstance(lines, list):
        return " ".join(str(l) for l in lines if l)
    return str(ov.get("text") or "")


def _build_filter(assets: list[_Timed]) -> str:
    """Chain portrait overlays with alpha fade in/out; PNG i is input index i+1.

    The base is scaled to COVER 1080x1920 then cropped, so a portrait avatar
    clip fills the frame exactly (and a stray landscape base is safely
    center-cropped to portrait rather than letterboxed).
    """
    parts = [
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[base];"
    ]
    prev = "[base]"
    for i, a in enumerate(assets, start=1):
        end = a.start + a.hold
        fade_out = max(a.start, end - FADE)
        parts.append(
            f"[{i}:v]scale={W}:{H},format=yuva420p,"
            f"fade=t=in:st={a.start}:d={FADE}:alpha=1,"
            f"fade=t=out:st={fade_out}:d={FADE}:alpha=1[a{i}];"
        )
        cur = f"[v{i}]"
        parts.append(
            f"{prev}[a{i}]overlay=x=0:y=0:"
            f"enable='between(t,{a.start},{end})':shortest=0{cur};"
        )
        prev = cur
    graph = "".join(parts).rstrip(";")
    return (
        graph.replace(prev, "[vout]", 1)
        if prev != "[base]"
        else graph.replace("[base]", "[vout]", 1)
    )


def _composite(base_video: Path, assets: list[_Timed], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not assets:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(base_video),
             "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
             str(out_path)],
            check=True)
        return out_path

    cmd: list[str] = ["ffmpeg", "-y", "-i", str(base_video)]
    for a in assets:
        stream_len = a.start + a.hold + FADE + 0.5
        cmd += ["-loop", "1", "-t", f"{stream_len:.3f}", "-i", str(a.png)]
    cmd += [
        "-filter_complex", _build_filter(assets),
        "-map", "[vout]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest",
        "-movflags", "+faststart", str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path


def render_sf_onto(base_video: Path, plan: dict, out_dir: Path, font_dir: Path) -> Path:
    """Burn the SF OST overlays from `plan` onto a LOCAL portrait base video.

    Reusable core — no download. The GitHub render path calls this directly with
    an already-downloaded clip; render_job_sf() wraps it with a URL download.

    `plan.text_overlays` = [{timestamp, lines|text, hold?, placement?, punchy?}].
    Placement is content-aware unless the overlay pins it explicitly.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    overlays = list(plan.get("text_overlays") or [])
    placements = assign_placements(overlays)

    assets: list[_Timed] = []
    for i, (ov, place) in enumerate(zip(overlays, placements)):
        png = render_sf_overlay(
            _overlay_text(ov), place, Path(font_dir), out_dir / f"sf_ov_{i}.png")
        assets.append(_Timed(
            png=png,
            start=float(ov.get("timestamp", 0)),
            hold=float(ov.get("hold", OVERLAY_HOLD)),
        ))
    assets.sort(key=lambda a: a.start)

    out = out_dir / "final.mp4"
    _composite(base_video, assets, out)
    return out


def render_job_sf(job_id: str, plan: dict, base_video_url: str,
                  work_root: Path, font_dir: Path) -> Path:
    """SF portrait pipeline for one job (URL in). Returns the final MP4 path."""
    work = Path(work_root) / job_id
    work.mkdir(parents=True, exist_ok=True)
    base_video = _download(base_video_url, work / "base.mp4")
    return render_sf_onto(base_video, plan, work, font_dir)
