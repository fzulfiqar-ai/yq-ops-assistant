"""Agnes AI — free omni-modal API (OpenAI-compatible) for AI image + video generation.

Config-gated: inert until AGNES_API_KEY is set. Used by app/video_gen.py as the premium
video backend (image-to-video from the branded product card), with the FFmpeg ken-burns
renderer as the always-free fallback.

Verified contract (2026-07-07):
  base   = https://apihub.agnes-ai.com/v1   ·   auth = Authorization: Bearer <key>
  image  = POST /v1/images/generations  {model, prompt}  -> sync, data[0].url
  video  = POST /v1/videos  {model, prompt, image?, height, width, num_frames, frame_rate}
             -> {video_id, status:"queued"}   (async, ~90s)
  poll   = GET  /agnesapi?video_id=<video_id>
             -> {status, progress, remixed_from_video_id}   (MP4 URL lives in
                remixed_from_video_id when status == "completed")
"""
from __future__ import annotations

import logging
import os
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

BASE = "https://apihub.agnes-ai.com/v1"
POLL = "https://apihub.agnes-ai.com/agnesapi"
IMAGE_MODEL = "agnes-image-2.1-flash"
VIDEO_MODEL = "agnes-video-v2.0"


def _key() -> str:
    return os.getenv("AGNES_API_KEY", "")


def enabled() -> bool:
    return bool(_key())


def _headers() -> dict:
    return {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}


def generate_image(prompt: str, model: str = IMAGE_MODEL) -> str | None:
    """Synchronous text-to-image → hosted URL (None on failure)."""
    if not enabled():
        return None
    try:
        r = requests.post(f"{BASE}/images/generations", headers=_headers(),
                          json={"model": model, "prompt": prompt[:1200]}, timeout=90)
        r.raise_for_status()
        data = (r.json().get("data") or [])
        return data[0].get("url") if data else None
    except Exception as e:  # noqa: BLE001
        log.warning("agnes image failed: %s", str(e)[:200])
        return None


def start_video(prompt: str, image_url: str | None = None, *, seconds: float = 5.0,
                fps: int = 16, portrait: bool = True) -> str | None:
    """Kick off (image-to-)video generation. Returns the video_id to poll, or None.

    num_frames must satisfy the 8n+1 rule (Agnes constraint); duration = frames / fps."""
    if not enabled():
        return None
    frames = int(round(seconds * fps))
    frames = max(9, min(441, ((frames - 1) // 8) * 8 + 1))  # snap to 8n+1, cap 441
    body: dict = {
        "model": VIDEO_MODEL, "prompt": prompt[:1200],
        "height": 1152 if portrait else 768, "width": 768 if portrait else 1152,
        "num_frames": frames, "frame_rate": fps,
    }
    if image_url:
        body["image"] = image_url
    try:
        r = requests.post(f"{BASE}/videos", headers=_headers(), json=body, timeout=60)
        r.raise_for_status()
        j = r.json()
        return j.get("video_id") or j.get("id")
    except Exception as e:  # noqa: BLE001
        log.warning("agnes start_video failed: %s", str(e)[:200])
        return None


def poll_video(video_id: str) -> dict:
    """Return {status, progress, url}. status ∈ in_progress|completed|failed.
    url is the final MP4 (from remixed_from_video_id) once completed. Transient issues
    (429 rate-limit, timeouts) map to in_progress so the caller retries — only an explicit
    rejection (e.g. content_policy_violation) or Agnes 'failed' marks the render dead."""
    if not enabled() or not video_id:
        return {"status": "in_progress", "url": None, "progress": 0}
    try:
        r = requests.get(f"{POLL}?video_id={quote(video_id, safe='')}", headers=_headers(), timeout=30)
        if r.status_code == 429:
            return {"status": "in_progress", "url": None, "progress": 0}  # poll rate limit — retry
        if r.status_code == 400:
            msg = ""
            try:
                msg = (r.json().get("error") or {}).get("message", "")
            except Exception:  # noqa: BLE001
                pass
            return {"status": "failed", "url": None, "progress": 0, "error": msg or "rejected"}
        r.raise_for_status()
        j = r.json()
        status = j.get("status") or "in_progress"
        # Completed response carries the MP4 in `url` (older/remix responses used
        # `remixed_from_video_id`) — read both to be safe.
        url = (j.get("url") or j.get("remixed_from_video_id")) if status == "completed" else None
        return {"status": status, "progress": j.get("progress") or 0,
                "url": url, "error": j.get("error")}
    except Exception as e:  # noqa: BLE001
        log.info("agnes poll transient: %s", str(e)[:160])
        return {"status": "in_progress", "url": None, "progress": 0}  # transient — retry later
