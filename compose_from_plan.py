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

# Frame-left "safe zone" (Larry-approved) — the red box in reference shots.
# All overlays and infographics must sit entirely within this box in the
# 1920x1080 output frame:
#   x ∈ [40, 940]    (900 wide — leaves Stacey clear at frame-right)
#   y ∈ [40, 800]    (760 tall — leaves OpusClip caption band clear)
SAFE_X = 40
SAFE_Y = 40
SAFE_W = 900
SAFE_H = 760

# Infographics are FULL-FRAME cutaways (Larry-approved reference): they
# cover Stacey entirely and fill the top of the frame, leaving only the
# bottom caption band clear. Card PNGs are authored at 1920x1080 and scaled
# to 1920x900 (slight vertical compression is acceptable and matches the
# approved layout).
INFOGRAPHIC_TARGET_W = 1920
INFOGRAPHIC_TARGET_H = 900
INFOGRAPHIC_X = 0
INFOGRAPHIC_Y = 0

# Text overlays are FULL-FRAME PNGs (1920x1080) with a Larry-approved
# letterbox treatment: subtle full-frame darken, solid thin top bar, and a
# semi-transparent bottom bar that mutes OpusClip's baked-in caption band.
# The text itself lives frame-left inside the safe zone; Stacey remains
# visible frame-right through the soft darken.
OVERLAY_X = 0
OVERLAY_Y = 0


@dataclass
class TimedAsset:
    """One PNG scheduled onto the base video timeline.

    target_w / target_h: if target_w > 0, ffmpeg scales the PNG to
    (target_w x target_h) before compositing. Use target_h=-2 for aspect-
    preserving height. If target_w == 0 the PNG is composited at its native
    dimensions.
    """
    png: Path
    start: float
    hold: float
    x: int
    y: int
    target_w: int = 0
    target_h: int = 0


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
        # Per-asset scaling: infographics scale down to fit the safe zone;
        # overlays are already authored at the right size.
        if a.target_w > 0:
            scale_expr = f"scale={a.target_w}:{a.target_h}"
        else:
            scale_expr = f"scale=iw*{SCALE}:ih*{SCALE}"
        parts.append(
            f"[{i}:v]{scale_expr},format=yuva420p,"
            f"fade=t=in:st={a.start}:d={FADE}:alpha=1,"
            f"fade=t=out:st={fade_out_start}:d={FADE}:alpha=1"
            f"[a{i}];"
        )
        cur = f"[v{i}]"
        parts.append(
            f"{prev}[a{i}]overlay=x={a.x}:y={a.y}:"
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
