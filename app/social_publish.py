"""Social publishing — Instagram + Facebook via the (free) Meta Graph API, TikTok via
Telegram hand-off (Marketing Phases 3/4).

Config-gated: needs FB_PAGE_ID + FB_PAGE_TOKEN (+ IG_BUSINESS_ID for Instagram). A Meta
app in dev mode publishes to YOUR OWN Page/IG account without app review — enough for
the shop's accounts. TikTok's Content Posting API requires an audited app for public
posts, so the honest free path is: we deliver the rendered video + caption to Telegram
and the owner posts it in 30 seconds.

Inbound (Phase 4): comment/DM webhook events → LLM reply draft → Telegram for one-tap
manual posting (auto-reply needs Meta Advanced Access review — upgrade later).
"""
from __future__ import annotations

import logging
import os
import time

import requests

from app.database import get_client

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"


def configured() -> dict:
    return {"facebook": bool(os.getenv("FB_PAGE_ID") and os.getenv("FB_PAGE_TOKEN")),
            "instagram": bool(os.getenv("IG_BUSINESS_ID") and os.getenv("FB_PAGE_TOKEN")),
            "tiktok_handoff": True}


def _tok() -> str:
    return os.getenv("FB_PAGE_TOKEN", "")


# ── Facebook Page ─────────────────────────────────────────────────────────────

def post_facebook(media_url: str, caption: str, kind: str) -> dict:
    page = os.getenv("FB_PAGE_ID", "")
    if not page or not _tok():
        return {"ok": False, "reason": "fb_not_configured"}
    if kind == "video":
        r = requests.post(f"{GRAPH}/{page}/videos",
                          data={"file_url": media_url, "description": caption,
                                "access_token": _tok()}, timeout=120)
    else:
        r = requests.post(f"{GRAPH}/{page}/photos",
                          data={"url": media_url, "message": caption,
                                "access_token": _tok()}, timeout=60)
    ok = r.status_code in (200, 201)
    return {"ok": ok, "id": (r.json().get("id") if ok else None),
            "error": (None if ok else r.text[:300])}


# ── Instagram (Business account linked to the Page) ───────────────────────────

def post_instagram(media_url: str, caption: str, kind: str) -> dict:
    """Container → poll → publish. Reels for video, feed photo for images."""
    ig = os.getenv("IG_BUSINESS_ID", "")
    if not ig or not _tok():
        return {"ok": False, "reason": "ig_not_configured"}
    params = {"caption": caption[:2200], "access_token": _tok()}
    if kind == "video":
        params.update({"media_type": "REELS", "video_url": media_url})
    else:
        params["image_url"] = media_url
    r = requests.post(f"{GRAPH}/{ig}/media", data=params, timeout=60)
    if r.status_code not in (200, 201):
        return {"ok": False, "error": r.text[:300]}
    container = r.json().get("id")
    # video containers take time to process — poll status (max ~2 min)
    for _ in range(24 if kind == "video" else 3):
        s = requests.get(f"{GRAPH}/{container}",
                         params={"fields": "status_code", "access_token": _tok()},
                         timeout=20).json().get("status_code")
        if s == "FINISHED":
            break
        if s == "ERROR":
            return {"ok": False, "error": "container processing failed"}
        time.sleep(5)
    p = requests.post(f"{GRAPH}/{ig}/media_publish",
                      data={"creation_id": container, "access_token": _tok()}, timeout=60)
    ok = p.status_code in (200, 201)
    return {"ok": ok, "id": (p.json().get("id") if ok else None),
            "error": (None if ok else p.text[:300])}


# ── TikTok hand-off (honest free path) ────────────────────────────────────────

def tiktok_handoff(media_url: str, caption: str) -> dict:
    try:
        from app.notify import send_telegram
        ok = send_telegram(
            f"🎵 <b>TikTok post ready</b> — download, then upload in the TikTok app:\n"
            f"{media_url}\n\n<b>Caption (copy):</b>\n{caption[:800]}")
        return {"ok": ok, "via": "telegram"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


# ── Publish one approved social_posts row ─────────────────────────────────────

def publish(post_id: int, by: str = "") -> dict:
    rows = get_client().table("social_posts").select("*").eq("id", post_id).execute().data or []
    if not rows:
        return {"ok": False, "reason": "not_found"}
    post = rows[0]
    if post["status"] == "posted":
        return {"ok": False, "reason": "already_posted"}
    caption = post.get("caption_en") or ""
    results: dict[str, dict] = {}
    for platform in (post.get("platforms") or []):
        if platform == "facebook":
            results["facebook"] = post_facebook(post["media_url"], caption, post["kind"])
        elif platform == "instagram":
            results["instagram"] = post_instagram(post["media_url"], caption, post["kind"])
        elif platform == "tiktok":
            results["tiktok"] = tiktok_handoff(post["media_url"], caption)
    any_ok = any(r.get("ok") for r in results.values())
    from datetime import datetime, timezone
    get_client().table("social_posts").update({
        "status": "posted" if any_ok else "failed",
        "posted_at": datetime.now(timezone.utc).isoformat() if any_ok else None,
        "meta": {**(post.get("meta") or {}), "publish_results": results, "published_by": by},
    }).eq("id", post_id).execute()
    return {"ok": any_ok, "results": results}


# ── Inbound social webhook (Phase 4, stage 1: Telegram hand-off) ──────────────

_LEAD_WORDS = ("price", "how much", "بكم", "السعر", "buy", "order", "dm", "interested")


def handle_meta_webhook(payload: dict) -> dict:
    """Comments/messages from the Page/IG webhook → log a lead + Telegram a drafted
    reply. Auto-posting replies needs Advanced Access — this stage keeps a human in."""
    handled = 0
    try:
        for entry in payload.get("entry", []):
            texts: list[tuple[str, str]] = []  # (author, text)
            for change in entry.get("changes", []):
                v = change.get("value", {})
                if change.get("field") == "feed" and v.get("item") == "comment":
                    texts.append((v.get("from", {}).get("name", "someone"),
                                  v.get("message", "")))
                if change.get("field") == "comments":  # instagram
                    texts.append(((v.get("from") or {}).get("username", "someone"),
                                  v.get("text", "")))
            for m in entry.get("messaging", []):  # page DMs
                if m.get("message", {}).get("text"):
                    texts.append((str(m.get("sender", {}).get("id", "user")),
                                  m["message"]["text"]))
            for author, text in texts:
                if not text:
                    continue
                handled += 1
                interested = any(w in text.lower() for w in _LEAD_WORDS)
                draft = ""
                try:
                    from app.agents import _catalog_link
                    from app.llm_router import chat
                    draft = chat([
                        {"role": "system", "content":
                            "Draft a one-sentence friendly reply from YQ Bahrain (mobile "
                            "accessories, Bahrain) to this social comment. Invite them to "
                            f"WhatsApp/catalog: {_catalog_link()}. Reply only with the text."},
                        {"role": "user", "content": text[:300]},
                    ], tier=1, task="write", max_tokens=90)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from app.notify import send_telegram
                    send_telegram(
                        f"💬 <b>Social {'lead' if interested else 'comment'}</b> from {author}:\n"
                        f"“{text[:200]}”\n\n<b>Suggested reply (copy):</b>\n{draft or '—'}")
                except Exception:  # noqa: BLE001
                    pass
                if interested:
                    try:
                        get_client().table("leads").upsert(
                            {"name": author, "source": "manual",
                             "source_ref": f"social:{author}", "status": "new",
                             "notes": f"Social comment: {text[:180]}"},
                            on_conflict="source,source_ref").execute()
                    except Exception:  # noqa: BLE001
                        pass
    except Exception as e:  # noqa: BLE001
        log.warning("meta webhook processing error: %s", e)
    return {"handled": handled}
