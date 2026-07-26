"""ffmpeg composite: burn infographic + text-overlay PNGs onto the base video.

Timing rules (locked):
- Infographics hold 7 seconds by default (6-10 allowed).
- Text overlays hold 5 seconds by default.
- All overlays fade in/out over 0.5 seconds; never slide.

Output is scaled to 720p (1280x720) so it fits in 512 MB RAM on Render's free
tier. Overlays and positions scale proportionally with SCALE.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

INFOGRAPHIC_HOLD = 7.0
OVERLAY_HOLD = 5.0
FADE = 0.5

# Target output resolution; base video and overlays are scaled to fit.
# GitHub Actions runners (7GB RAM) handle 1080p ffmpeg composite comfortably.
TARGET_W = 1920
TARGET_H = 1080
SCALE = 1.0  # overlays and infographics are authored at 1920x1080 natively

# Frame-left positioning against a 1920x1080 canvas; scaled at composite time
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
    """Chain overlays with fade-in/out; each PNG is input index i+1.

    Base video is scaled to TARGET_W x TARGET_H first; overlays are scaled by
    SCALE; positions are scaled by SCALE. Everything ends up on a 720p canvas.
    """
    parts: list[str] = []
    parts.append(f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2[base];")
    prev = "[base]"
    for i, a in enumerate(assets, start=1):
        fade_out_start = max(0.0, a.hold - FADE)
        parts.append(
            f"[{i}:v]scale=iw*{SCALE}:ih*{SCALE},format=yuva420p,"
            f"fade=t=in:st=0:d={FADE}:alpha=1,"
            f"fade=t=out:st={fade_out_start}:d={FADE}:alpha=1"
            f"[a{i}];"
        )
        end = a.start + a.hold
        cur = f"[v{i}]"
        x_scaled = int(a.x * SCALE)
        y_scaled = int(a.y * SCALE)
        parts.append(
            f"{prev}[a{i}]overlay=x={x_scaled}:y={y_scaled}:"
            f"enable='between(t,{a.start},{end})':"
            f"shortest=0{cur};"
        )
        prev = cur
    # Trim trailing ; and label final output as [vout]
    graph = "".join(parts).rstrip(";")
    graph = graph.replace(prev, "[vout]", 1) if prev != "[base]" else graph.replace("[base]", "[vout]", 1)
    return graph


def composite(
    base_video: Path,
    assets: list[TimedAsset],
    out_path: Path,
) -> Path:
    """Run ffmpeg to burn the timed PNGs onto the base video."""
    if not assets:
        # No overlays -> still transcode to 720p so output size matches other jobs
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
        cmd += ["-loop", "1", "-t", f"{a.hold}", "-itsoffset", f"{a.start}", "-i", str(a.png)]

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
        "-movflags", "+faststart",
        str(out_path),
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    return out_path
