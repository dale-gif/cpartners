# GODTIER Render Server

FastAPI service that composites GODTIER graphics onto an OpusClip MP4. The `CRP GODTIER VIDEO EDIT PIPELINE` n8n workflow calls this over HTTP.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/render` | Enqueue a render job. Body: `{ plan, opusClipUrl, videoId? }`. Returns `{ job_id }`. |
| GET | `/status?job_id=…` | `{ status: queued\|running\|done\|error, ... }` |
| GET | `/result?job_id=…` | When done: `{ url: "https://.../files/<job>.mp4" }` |
| GET | `/files/<job_id>.mp4` | Serves the finished MP4. |

`plan` matches the Claude planner output the n8n workflow already produces:

```json
{
  "infographics": [
    { "timestamp": 56, "template": "bordered-list", "title": "BEFORE YOU CHASE", "items": ["ITEM 1", "ITEM 2", "ITEM 3"] }
  ],
  "text_overlays": [
    { "timestamp": 156, "lines": ["time is", "not your", "friend"], "style": "white" }
  ]
}
```

## Local run

```bash
brew install ffmpeg
mkdir fonts && cd fonts
curl -LO https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Regular.ttf
curl -LO https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Black.ttf
curl -LO https://github.com/rsms/inter/raw/master/docs/font-files/Inter-ExtraBold.ttf
cd ..
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

Test it end-to-end:

```bash
curl -X POST http://localhost:8000/render \
  -H 'content-type: application/json' \
  -d '{"opusClipUrl":"https://example.com/base.mp4","plan":{"infographics":[{"timestamp":5,"title":"BEFORE YOU CHASE","items":["Verify the debt","Check the contract","Send a demand"]}],"text_overlays":[{"timestamp":15,"lines":["time is","not your","friend"],"style":"white"}]}}'
```

## Deploy — Docker (works on any VPS / Render / Fly / Railway)

```bash
docker build -t godtier-render .
docker run -p 8000:8000 -e PUBLIC_BASE_URL=https://your-domain godtier-render
```

### Render.com

1. New → Web Service → connect this repo.
2. Environment: `Docker`.
3. Add env var `PUBLIC_BASE_URL` = your assigned `.onrender.com` URL.
4. Deploy. The URL is what you paste into the n8n workflow's 3 render nodes.

### Fly.io

```bash
fly launch --dockerfile Dockerfile --no-deploy
fly secrets set PUBLIC_BASE_URL=https://<app>.fly.dev
fly deploy
```

### Railway

`railway up` from this directory. Set `PUBLIC_BASE_URL` in the dashboard.

## Wire into n8n

Once deployed, replace `{{ $env.RENDER_SERVER_URL }}` in these 3 nodes with the hardcoded URL (n8n Cloud on the free plan doesn't support `$vars`):

- `Trigger Godtier Render` → `POST <URL>/render`
- `Check Render Status` → `GET <URL>/status`
- `Get Final Video URL` → `GET <URL>/result`

## Storage note

Finished MP4s live on the container's local disk under `work/<job_id>/final.mp4`. On Render/Fly ephemeral filesystems this survives until the next deploy. For long-term storage add an S3 upload step at the end of `godtier_lf.render_job` and return that URL from `/result`.

## Design rules (locked by Larry)

Enforced in `graphic_cards.py` and `kinetic_words.py`:
- Pure black and white; bordered cards only; CAPS titles; hold 6–10s; fade/scale, never slide.
- Text overlays: exactly 3 lines; Inter ExtraBold; 104px at 1920 wide; frame-left; two styles (`white`, `black-gradient`).
- Stacey is frame-right; overlays land frame-left; clear of the bottom caption band.

If Larry changes his mind, edit the constants at the top of each file — do not scatter magic numbers.

## SF portrait on-screen text (9:16) — NEW

Short-form (SF) videos get their own **portrait 1080×1920** on-screen-text pass, separate from the landscape MF/LF path above. It lives in two self-contained modules (`sf_overlay.py`, `sf_render.py`) and only runs when a request opts in — **turning it on cannot change MF/LF output.**

**Trigger:** POST `/render` with `"format": "sf"` (or `"portrait"`). Omit it (or send anything else) and you get the unchanged landscape path.

```json
{
  "format": "sf",
  "opusClipUrl": "https://.../brad_portrait.mp4",
  "videoId": "CRP-D-...-SC",
  "plan": {
    "text_overlays": [
      { "timestamp": 2,  "lines": ["STILL OWE"],   "placement": "top" },
      { "timestamp": 9,  "lines": ["THEY DO PAY"],  "punchy": true },
      { "timestamp": 18, "lines": ["30 DAYS"] },
      { "timestamp": 40, "lines": ["STILL OWE"],   "placement": "bottom" }
    ]
  }
}
```

**Placements (Larry-approved):** `top`, `center`, `lower_third` (over/under rule bars), `bottom`. Big Inter Black, white, uppercase, horizontally centered, soft drop shadow.

**Content-aware placement** (`sf_overlay.assign_placements`): lines rotate TOP → LOWER_THIRD → BOTTOM to keep the presenter's face clear; **CENTER is reserved for one short punchy standalone line** (`"punchy": true`, or ≤3 words). Pin any line with an explicit `"placement"`.

**Timing:** each line holds ~2.5s with a **0.5s alpha fade in/out** (never slide) — the same locked transition as MF/LF.

**Tuning:** sizes/positions/bars are constants at the top of `sf_overlay.py`; hold/fade at the top of `sf_render.py`. No scattered magic numbers.

**Preview the placements:** `python sf_overlay.py sf_preview` renders the 4 zones over a mock backdrop to `sf_preview/`.
