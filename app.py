"""FastAPI wrapper: exposes /render, /status, /result, /whisper for n8n to call."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from godtier_lf import render_job

BASE_DIR = Path(__file__).parent
WORK_ROOT = Path(os.environ.get("WORK_ROOT", BASE_DIR / "work"))
FONT_DIR = Path(os.environ.get("FONT_DIR", BASE_DIR / "fonts"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

app = FastAPI(title="GODTIER Render Server")

_lock = threading.Lock()


def _state_path(job_id: str) -> Path:
    return WORK_ROOT / job_id / "state.json"


def _read_state(job_id: str) -> dict[str, Any] | None:
    """Read job state from disk. Survives container restarts."""
    path = _state_path(job_id)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_state(job_id: str, **kw: Any) -> None:
    """Merge kw into the job state on disk."""
    with _lock:
        path = _state_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        current = _read_state(job_id) or {}
        current.update(kw)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(current, f)
        tmp.replace(path)


class RenderRequest(BaseModel):
    plan: dict = Field(..., description="Claude planner output JSON")
    opusClipUrl: str = Field(..., description="OpusClip MP4 URL to composite onto")
    videoId: str | None = Field(default=None, description="Notion page id (optional)")


class WhisperRequest(BaseModel):
    opusClipUrl: str = Field(..., description="OpusClip MP4 URL to transcribe")


def _run(job_id: str, req: RenderRequest) -> None:
    _write_state(job_id, status="running")
    try:
        out = render_job(
            job_id=job_id,
            plan=req.plan,
            opus_clip_url=req.opusClipUrl,
            work_root=WORK_ROOT,
            font_dir=FONT_DIR,
        )
        _write_state(job_id, status="done", path=str(out))
    except Exception as e:
        _write_state(
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
    _write_state(job_id, status="queued", videoId=req.videoId)
    background.add_task(_run, job_id, req)
    return {"job_id": job_id, "status": "queued"}


@app.get("/status")
def status(job_id: str) -> dict[str, Any]:
    job = _read_state(job_id)
    if job is None:
        # Fallback: reconstruct status from the filesystem if state.json is
        # gone but the job directory or output survived a restart.
        job_dir = WORK_ROOT / job_id
        final_mp4 = job_dir / "final.mp4"
        if final_mp4.exists():
            return {"job_id": job_id, "status": "done", "path": str(final_mp4)}
        if job_dir.exists():
            return {"job_id": job_id, "status": "running", "note": "recovered from disk"}
        raise HTTPException(status_code=404, detail="unknown job_id")
    return {"job_id": job_id, **job}


@app.get("/result")
def result(job_id: str, request: Request) -> dict[str, str]:
    job = _read_state(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail=f"job {job.get('status')}")
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return {"job_id": job_id, "url": f"{base}/files/{job_id}.mp4"}


@app.post("/whisper")
def whisper(req: WhisperRequest, request: Request) -> dict[str, Any]:
    """Download OpusClip MP4 -> extract compact mono MP3 -> POST to OpenAI Whisper.

    Solves the 25 MB Whisper hard limit: a 12-min LF MP4 (~232 MB) becomes a
    ~4 MB mono 48 kbps MP3 after extraction, well under the cap.

    Key source, in order of preference:
      1. OPENAI_API_KEY env var on the server (recommended)
      2. Authorization: Bearer <key> header from the caller
      3. x-openai-key header
    """
    openai_key = (
        os.environ.get("OPENAI_API_KEY")
        or (request.headers.get("authorization", "").split(" ", 1)[-1].strip()
            if request.headers.get("authorization", "").lower().startswith("bearer ") else "")
        or request.headers.get("x-openai-key", "")
    ).strip()
    if not openai_key:
        raise HTTPException(
            status_code=401,
            detail="No OpenAI key. Set OPENAI_API_KEY env var on the server, or pass Authorization: Bearer <key>, or x-openai-key: <key>.",
        )

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            video_path = tmp_dir / "video.mp4"
            audio_path = tmp_dir / "audio.mp3"

            with requests.get(req.opusClipUrl, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(video_path, "wb") as f:
                    shutil.copyfileobj(r.raw, f)

            try:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", str(video_path),
                        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
                        str(audio_path),
                    ],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"ffmpeg extract failed: {e.stderr.decode('utf-8', 'replace')[-500:]}",
                )

            audio_bytes = audio_path.stat().st_size

            with open(audio_path, "rb") as f:
                resp = requests.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {openai_key}"},
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
            if not resp.ok:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"whisper API returned {resp.status_code}: {resp.text[:500]} (audio was {audio_bytes} bytes)",
                )
            return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"{type(e).__name__}: {str(e)[:500]}",
        )


@app.get("/files/{job_id}.mp4")
def file(job_id: str) -> FileResponse:
    job = _read_state(job_id) or {}
    path = job.get("path") or str(WORK_ROOT / job_id / "final.mp4")
    if not Path(path).exists():
        raise HTTPException(status_code=404, detail="file not ready")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"godtier_{job_id}.mp4",
    )
