"""GODTIER LF orchestrator: plan JSON + OpusClip MP4 -> composited MP4."""
from __future__ import annotations

import shutil
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


def _download(url: str, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            shutil.copyfileobj(r.raw, f)
    return out


def render_job(
    job_id: str,
    plan: dict,
    opus_clip_url: str,
    work_root: Path,
    font_dir: Path,
) -> Path:
    """Full pipeline for one job. Returns the final MP4 path."""
    work = work_root / job_id
    work.mkdir(parents=True, exist_ok=True)

    base_video = _download(opus_clip_url, work / "base.mp4")

    assets: list[TimedAsset] = []

    for i, card in enumerate(plan.get("infographics") or []):
        png = render_card(
            title=card.get("title") or "",
            items=list(card.get("items") or [])[:5],
            font_dir=font_dir,
            out_path=work / f"card_{i}.png",
        )
        assets.append(TimedAsset(
            png=png,
            start=float(card.get("timestamp", 0)),
            hold=float(card.get("hold", INFOGRAPHIC_HOLD)),
            x=INFOGRAPHIC_X,
            y=INFOGRAPHIC_Y,
        ))

    for i, ov in enumerate(plan.get("text_overlays") or []):
        png = render_overlay(
            lines=list(ov.get("lines") or []),
            style=(ov.get("style") or "white"),
            font_dir=font_dir,
            out_path=work / f"overlay_{i}.png",
        )
        assets.append(TimedAsset(
            png=png,
            start=float(ov.get("timestamp", 0)),
            hold=float(ov.get("hold", OVERLAY_HOLD)),
            x=OVERLAY_X,
            y=OVERLAY_Y,
        ))

    # Sort by start so the filter chain applies in order
    assets.sort(key=lambda a: a.start)

    out = work / "final.mp4"
    composite(base_video, assets, out)
    return out
