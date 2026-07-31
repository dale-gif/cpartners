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
class TimedClip:
    """One ANIMATED clip scheduled onto the base video timeline.

    clip: an opaque MP4 (entrance animation + frozen last frame) authored at
    1920x1080. It is overlaid onto the base video starting at `start`, covering
    the frame for `hold` seconds, with a short alpha fade in/out so the cut
    to/from the talking head is smooth. The entrance motion is baked into the
    clip itself — no ffmpeg cross-fade fakery.
    """
    clip: Path
    start: float
    hold: float


def composite_clips(base_video: Path, clips: list["TimedClip"], out_path: Path) -> Path:
    """Overlay animated cutaway clips onto the base video.

    Each clip is a real video input (not `-loop 1` on a static PNG). We shift
    its PTS to `start`, fade its alpha in/out over FADE seconds for a clean cut
    to/from the base, and enable the overlay only within its window.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not clips:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(base_video),
             "-vf", f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
                    f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
             str(out_path)],
            check=True)
        return out_path

    clips = sorted(clips, key=lambda c: c.start)
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(base_video)]
    for c in clips:
        cmd += ["-i", str(c.clip)]

    parts = [
        f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2[base];"
    ]
    prev = "[base]"
    for i, c in enumerate(clips, start=1):
        end = c.start + c.hold
        fade_out = max(c.start, end - FADE)
        parts.append(
            f"[{i}:v]scale={TARGET_W}:{TARGET_H},format=yuva420p,"
            f"setpts=PTS+{c.start}/TB,"
            f"fade=t=in:st={c.start}:d={FADE}:alpha=1,"
            f"fade=t=out:st={fade_out}:d={FADE}:alpha=1[a{i}];"
        )
        cur = f"[v{i}]"
        parts.append(
            f"{prev}[a{i}]overlay=enable='between(t,{c.start},{end})':shortest=0{cur};"
        )
        prev = cur
    graph = "".join(parts).rstrip(";").replace(prev, "[vout]", 1)

    cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest",
            "-movflags", "+faststart", str(out_path)]
    subprocess.run(cmd, check=True)
    return out_path


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
                "-pix_fmt", "yuv420p", "-c:a", "copy",
                "-movflags", "+faststart",
                str(out_path),
            ],
            check=True,
        )
        return out_path

    # Cap each image stream so it only decodes long enough to cover its
    # enable window (start + hold + FADE tail). Without a cap, `-loop 1`
    # would decode a static image for the WHOLE video for every asset —
    # e.g. 9 assets on a 10-min video = 90 min of wasted decode work.
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(base_video)]
    for a in assets:
        stream_len = a.start + a.hold + FADE + 0.5
        cmd += ["-loop", "1", "-t", f"{stream_len:.3f}", "-i", str(a.png)]

    cmd += [
        "-filter_complex", _build_filter_complex(assets),
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    return out_path
