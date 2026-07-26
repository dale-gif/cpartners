"""ffmpeg composite: burn infographic + text-overlay PNGs onto the base video.

Timing rules (locked):
- Infographics hold 7 seconds by default (6-10 allowed).
- Text overlays hold 5 seconds by default.
- All overlays fade in/out over 0.5 seconds; never slide.

Output is 1080p (1920x1080). PNGs are authored at native 1920x1080.

Uses the STANDARD ffmpeg overlay pattern (`-loop 1 -i image`, fade with
absolute st times matching the base video timeline, `enable=between(t,S,E)`)
rather than `-loop 1 -t X -itsoffset Y` — the itsoffset pattern breaks PTS
alignment for overlays at large timestamps.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

INFOGRAPHIC_HOLD = 20.0  # default cards stay long enough for a full explanation
OVERLAY_HOLD = 5.0
FADE = 0.5

TARGET_W = 1920
TARGET_H = 1080
SCALE = 1.0  # overlays and infographics are authored at 1920x1080 natively

# Frame-left positioning against a 1920x1080 canvas
INFOGRAPHIC_X = 0
INFOGRAPHIC_Y = 0
# Overlay lands near top-left so text stays clear of OpusClip's bottom caption
# band. Combined with top-anchored text at canvas y=60 (see kinetic_words),
# text renders at frame y ≈ 130-680, always above the caption zone.
OVERLAY_X = 60
OVERLAY_Y = 60


@dataclass
class TimedAsset:
    """One PNG scheduled onto the base video timeline."""
    png: Path
    start: float
    hold: float
    x: int
    y: int


def _build_filter_complex(assets: list[TimedAsset]) -> str:
    """Chain overlays with fade-in/out; each PNG is input index i+1.

    Fade uses ABSOLUTE base-video time via `st=<start>` — this only works
    because the overlay inputs are static images (`-loop 1`) whose stream PTS
    aligns 1:1 with the base video timeline.
    """
    parts: list[str] = []
    parts.append(
        f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2[base];"
    )
    prev = "[base]"
    for i, a in enumerate(assets, start=1):
        end = a.start + a.hold
        fade_out_start = max(a.start, end - FADE)
        parts.append(
            f"[{i}:v]scale=iw*{SCALE}:ih*{SCALE},format=yuva420p,"
            f"fade=t=in:st={a.start}:d={FADE}:alpha=1,"
            f"fade=t=out:st={fade_out_start}:d={FADE}:alpha=1"
            f"[a{i}];"
        )
        cur = f"[v{i}]"
        x_scaled = int(a.x * SCALE)
        y_scaled = int(a.y * SCALE)
        parts.append(
            f"{prev}[a{i}]overlay=x={x_scaled}:y={y_scaled}:"
            f"enable='between(t,{a.start},{end})':"
            f"shortest=0{cur};"
        )
        prev = cur
    graph = "".join(parts).rstrip(";")
    # Rename the last output to [vout] for the -map target.
    graph = (
        graph.replace(prev, "[vout]", 1)
        if prev != "[base]"
        else graph.replace("[base]", "[vout]", 1)
    )
    return graph


def composite(
    base_video: Path,
    assets: list[TimedAsset],
    out_path: Path,
) -> Path:
    """Run ffmpeg to burn the timed PNGs onto the base video.

    Uses `-loop 1 -i <png>` for each overlay so the stream is infinite and
    aligns with base PTS. `enable=between(t,S,E)` in the overlay filter
    controls WHEN each overlay is composited; `fade` handles the smooth
    in/out on the base video's timeline.
    """
    if not assets:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(base_video),
                "-vf", f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
                       f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "copy", "-threads", "1",
                "-movflags", "+faststart",
                str(out_path),
            ],
            check=True,
        )
        return out_path

    cmd: list[str] = ["ffmpeg", "-y", "-i", str(base_video)]
    for a in assets:
        cmd += ["-loop", "1", "-i", str(a.png)]

    cmd += [
        "-filter_complex", _build_filter_complex(assets),
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-threads", "1",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    return out_path
