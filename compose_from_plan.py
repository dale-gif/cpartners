"""ffmpeg composite: burn infographic + text-overlay PNGs onto the base video.

Timing rules (locked):
- Infographics hold 7 seconds by default (6-10 allowed).
- Text overlays hold 5 seconds by default.
- All overlays fade in/out over 0.5 seconds; never slide.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

INFOGRAPHIC_HOLD = 7.0
OVERLAY_HOLD = 5.0
FADE = 0.5

# Frame-left positioning against a 1920x1080 canvas
INFOGRAPHIC_X = 80
INFOGRAPHIC_Y = 90
OVERLAY_X = 60
OVERLAY_Y = 200


@dataclass
class TimedAsset:
    """One PNG scheduled onto the base video timeline."""
    png: Path
    start: float
    hold: float
    x: int
    y: int


def _build_filter_complex(assets: list[TimedAsset]) -> str:
    """Chain overlays with fade-in/out; each PNG is input index i+1."""
    parts: list[str] = []
    prev = "[0:v]"
    for i, a in enumerate(assets, start=1):
        fade_out_start = max(0.0, a.hold - FADE)
        parts.append(
            f"[{i}:v]format=yuva420p,"
            f"fade=t=in:st=0:d={FADE}:alpha=1,"
            f"fade=t=out:st={fade_out_start}:d={FADE}:alpha=1"
            f"[a{i}];"
        )
        end = a.start + a.hold
        cur = f"[v{i}]"
        parts.append(
            f"{prev}[a{i}]overlay=x={a.x}:y={a.y}:"
            f"enable='between(t,{a.start},{end})':"
            f"shortest=0{cur};"
        )
        prev = cur
    # Trim trailing ; and label final output as [vout]
    graph = "".join(parts).rstrip(";")
    graph = graph.replace(prev, "[vout]", 1) if prev != "[0:v]" else "[0:v]null[vout]"
    return graph


def composite(
    base_video: Path,
    assets: list[TimedAsset],
    out_path: Path,
) -> Path:
    """Run ffmpeg to burn the timed PNGs onto the base video."""
    if not assets:
        # No overlays -> straight copy
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(base_video), "-c", "copy", str(out_path)],
            check=True,
        )
        return out_path

    cmd: list[str] = ["ffmpeg", "-y", "-i", str(base_video)]
    for a in assets:
        cmd += ["-loop", "1", "-t", f"{a.hold}", "-itsoffset", f"{a.start}", "-i", str(a.png)]

    cmd += [
        "-filter_complex", _build_filter_complex(assets),
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    return out_path
