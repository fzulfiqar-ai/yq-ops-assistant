"""Field intelligence (LATER bucket) — capture reps' market observations and feed them into RAG.

A note (competitor pricing, a stock-out seen on a shelf, demand for a product, a complaint) is
stored in field_notes AND embedded into kb_chunks, so the assistant recalls it as context
("reps report rising demand for X", "competitor priced Y at Z"). Turns the agents from data-only
to data + ground truth. Embedding is local (no PII egress); recalled chunks are redacted before any
external LLM call by the orchestrator.
"""
from __future__ import annotations

import logging
import uuid

from app.database import get_client

log = logging.getLogger(__name__)

CATEGORIES = ("competitor_price", "stockout", "demand", "complaint", "new_product", "other")

_BUCKET = "field-notes"          # PRIVATE storage bucket for rep photos
_SIGNED_TTL = 60 * 60            # 1h signed URLs — long enough to view, short enough to expire


def ensure_bucket() -> None:
    """Create the private photo bucket on first use (idempotent, best-effort)."""
    try:
        get_client().storage.create_bucket(_BUCKET, options={"public": False})
    except Exception:  # noqa: BLE001 — already exists / race → fine
        pass


def upload_photo(data: bytes, ext: str, content_type: str) -> str | None:
    """Upload image bytes to the private bucket; return the stored object path (not a URL)."""
    ensure_bucket()
    path = f"notes/{uuid.uuid4().hex}{ext}"
    try:
        get_client().storage.from_(_BUCKET).upload(
            path, data, {"content-type": content_type, "upsert": "false"})
        return path
    except Exception as e:  # noqa: BLE001
        log.warning("field note photo upload failed: %s", e)
        return None


def _sign(path: str | None) -> str | None:
    """Short-lived signed URL for a stored photo path (access-controlled, PDPL-safe)."""
    if not path:
        return None
    try:
        res = get_client().storage.from_(_BUCKET).create_signed_url(path, _SIGNED_TTL)
        return (res or {}).get("signedURL") or (res or {}).get("signedUrl")
    except Exception:  # noqa: BLE001
        return None


def add_note(note: str, category: str = "other", by: str = "", image_path: str | None = None) -> dict:
    note = (note or "").strip()
    if not note and not image_path:
        return {"ok": False, "reason": "empty note"}
    cat = category if category in CATEGORIES else "other"
    try:
        get_client().table("field_notes").insert(
            {"note": note[:2000], "category": cat, "created_by": by, "image_path": image_path}).execute()
    except Exception as e:  # noqa: BLE001
        log.warning("field note insert failed: %s", e)
        return {"ok": False, "reason": "save failed"}
    # embed into RAG so the chat/agents recall field feedback as context (best-effort).
    # The note text records that a photo backs it, so recall surfaces the evidence exists.
    try:
        from app.knowledge import remember
        photo_tag = " [photo attached]" if image_path else ""
        remember(f"Field note [{cat}]: {note}{photo_tag}", kind="knowledge",
                 meta={"source": "field_note", "category": cat, "by": by, "has_photo": bool(image_path)})
    except Exception as e:  # noqa: BLE001
        log.warning("field note embed failed: %s", e)
    return {"ok": True, "category": cat}


def list_notes(limit: int = 50) -> list[dict]:
    try:
        rows = (get_client().table("field_notes")
                .select("id,note,category,created_by,created_at,image_path")
                .order("created_at", desc=True).limit(limit).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("list field notes failed: %s", e)
        return []
    # swap the private path for a short-lived signed URL the browser can render
    for r in rows:
        r["image_url"] = _sign(r.pop("image_path", None))
    return rows
