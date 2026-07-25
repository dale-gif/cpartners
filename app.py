"""FastAPI wrapper: exposes /render, /status, /result for n8n to call."""
from __future__ import annotations

import os
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from godtier_lf import render_job

BASE_DIR = Path(__file__).parent
WORK_ROOT = Path(os.environ.get("WORK_ROOT", BASE_DIR / "work"))
FONT_DIR = Path(os.environ.get("FONT_DIR", BASE_DIR / "fonts"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

app = FastAPI(title="GODTIER Render Server")

JOBS: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


class RenderRequest(BaseModel):
    plan: dict = Field(..., description="Claude planner output JSON")
    opusClipUrl: str = Field(..., description="OpusClip MP4 URL to composite onto")
    videoId: str | None = Field(default=None, description="Notion page id (optional)")


def _set(job_id: str, **kw: Any) -> None:
    with _lock:
        JOBS.setdefault(job_id, {}).update(kw)


def _run(job_id: str, req: RenderRequest) -> None:
    _set(job_id, status="running")
    try:
        out = render_job(
            job_id=job_id,
            plan=req.plan,
            opus_clip_url=req.opusClipUrl,
            work_root=WORK_ROOT,
            font_dir=FONT_DIR,
        )
        _set(job_id, status="done", path=str(out))
    except Exception as e:
        _set(
            job_id,
            status="error",
            error=str(e),
            traceback=traceback.format_exc(limit=6),
        )


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "godtier-render", "status": "ok"}


@app.post("/render")
def render(req: RenderRequest, background: BackgroundTasks) -> dict[str, str]:
    job_id = uuid.uuid4().hex[:12]
    _set(job_id, status="queued", videoId=req.videoId)
    background.add_task(_run, job_id, req)
    return {"job_id": job_id, "status": "queued"}


@app.get("/status")
def status(job_id: str) -> dict[str, Any]:
    with _lock:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return {"job_id": job_id, **job}


@app.get("/result")
def result(job_id: str, request: Request) -> dict[str, str]:
    with _lock:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job_id")
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail=f"job {job.get('status')}")
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return {"job_id": job_id, "url": f"{base}/files/{job_id}.mp4"}


@app.get("/files/{job_id}.mp4")
def file(job_id: str) -> FileResponse:
    with _lock:
        job = JOBS.get(job_id) or {}
    path = job.get("path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="file not ready")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"godtier_{job_id}.mp4",
    )
