"""Product Finds — new / unique products the sales team spots in the field.

Reps used to dump a photo + a price into a WhatsApp group; this captures the same thing in
the portal so management can browse it as a gallery, share a tokenized public link (like the
catalog) and promote a good find into the real catalog. Photos live in a PRIVATE Supabase
Storage bucket ('finds'); we store only the object PATH and hand out short-lived SIGNED URLs
at read time (PDPL: competitor tags / shop-fronts / people may appear).
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone

from app.database import get_client

log = logging.getLogger(__name__)

_BUCKET = "finds"               # PRIVATE storage bucket for field product photos
_SIGNED_TTL = 60 * 60           # 1h signed URLs — long enough to view, short enough to expire
_TOKEN_KEY = "finds_share_token"
STATUSES = ("new", "reviewing", "promoted", "archived")

_LIST_FIELDS = ("id,name,price_bhd,currency,note,category,source,image_path,status,"
                "promoted_item_code,posted_by,posted_at,reviewed_by")


def ensure_bucket() -> None:
    """Create the private photo bucket on first use (idempotent, best-effort)."""
    try:
        get_client().storage.create_bucket(_BUCKET, options={"public": False})
    except Exception:  # noqa: BLE001 — already exists / race → fine
        pass


def upload_photo(data: bytes, ext: str, content_type: str) -> str | None:
    """Upload image bytes to the private bucket; return the stored object path (not a URL)."""
    ensure_bucket()
    path = f"finds/{uuid.uuid4().hex}{ext}"
    try:
        get_client().storage.from_(_BUCKET).upload(
            path, data, {"content-type": content_type, "upsert": "false"})
        return path
    except Exception as e:  # noqa: BLE001
        log.warning("product find photo upload failed: %s", e)
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


def _clean_item(it: dict) -> dict | None:
    """Normalise one incoming find; require an image_path (the photo is the point)."""
    path = (it.get("image_path") or "").strip()
    if not path:
        return None
    price = it.get("price_bhd")
    try:
        price = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price = None
    return {
        "image_path": path,
        "name": (it.get("name") or "").strip()[:160] or None,
        "price_bhd": price,
        "currency": (it.get("currency") or "BHD").strip()[:8] or "BHD",
        "note": (it.get("note") or "").strip()[:2000] or None,
        "category": ((it.get("category") or "").strip().upper() or None),
        "source": (it.get("source") or "").strip()[:80] or None,
    }


def bulk_add(items: list[dict], by: str = "") -> dict:
    """Insert one or many finds (each = photo + optional name/price/note)."""
    rows = [r for r in (_clean_item(it) for it in (items or [])) if r]
    if not rows:
        return {"ok": False, "reason": "no valid items"}
    for r in rows:
        r["posted_by"] = by
    try:
        get_client().table("product_finds").insert(rows).execute()
    except Exception as e:  # noqa: BLE001
        log.warning("product finds insert failed: %s", e)
        return {"ok": False, "reason": "save failed"}
    # best-effort: surface a Live Feed event so management sees new finds arrive
    try:
        from app.events import emit
        who = (by or "sales").split("@")[0]
        emit("product_finds", "finds.added", entity_type="finds", severity="info",
             payload={"summary": f"{len(rows)} new product find(s) posted by {who}",
                      "count": len(rows)}, dedupe=False)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "count": len(rows)}


def list_finds(status: str | None = None, limit: int = 500) -> list[dict]:
    try:
        q = (get_client().table("product_finds").select(_LIST_FIELDS)
             .order("posted_at", desc=True).limit(limit))
        if status in STATUSES:
            q = q.eq("status", status)
        rows = q.execute().data or []
    except Exception as e:  # noqa: BLE001
        log.warning("list product finds failed: %s", e)
        return []
    # swap the private path for a short-lived signed URL the browser can render
    for r in rows:
        r["image_url"] = _sign(r.pop("image_path", None))
    return rows


def update_find(find_id: int, fields: dict, by: str = "") -> dict:
    upd: dict = {}
    if fields.get("status") in STATUSES:
        upd["status"] = fields["status"]
        upd["reviewed_by"] = by
    for k in ("name", "note", "source"):
        if k in fields:
            v = fields[k]
            upd[k] = (str(v).strip() or None) if v is not None else None
    if "category" in fields:                       # normalise like bulk_add (_clean_item)
        v = fields["category"]
        upd["category"] = (str(v).strip().upper() or None) if v is not None else None
    if "price_bhd" in fields:
        v = fields["price_bhd"]
        try:
            upd["price_bhd"] = float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            pass
    if not upd:
        return {"ok": False, "reason": "nothing to update"}
    upd["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        get_client().table("product_finds").update(upd).eq("id", find_id).execute()
    except Exception as e:  # noqa: BLE001
        log.warning("update find failed: %s", e)
        return {"ok": False, "reason": "update failed"}
    return {"ok": True}


def delete_find(find_id: int) -> dict:
    try:
        r = (get_client().table("product_finds").select("image_path")
             .eq("id", find_id).limit(1).execute().data or [])
        get_client().table("product_finds").delete().eq("id", find_id).execute()
        if r and r[0].get("image_path"):
            try:
                get_client().storage.from_(_BUCKET).remove([r[0]["image_path"]])
            except Exception:  # noqa: BLE001 — orphaned object is cheap; row removal is what matters
                pass
    except Exception as e:  # noqa: BLE001
        log.warning("delete find failed: %s", e)
        return {"ok": False, "reason": "delete failed"}
    return {"ok": True}


# ── public share link (management / owner opens on any device, no login) ───────

def share_token(create: bool = True) -> str | None:
    """Stable random token in app_settings; the public finds URL embeds it."""
    try:
        r = get_client().table("app_settings").select("value").eq(
            "key", _TOKEN_KEY).limit(1).execute().data
        if r:
            return r[0]["value"]
        if not create:
            return None
        tok = secrets.token_urlsafe(18)
        get_client().table("app_settings").upsert(
            {"key": _TOKEN_KEY, "value": tok,
             "description": "Public product-finds share-link token (rotate to revoke old links)"},
            on_conflict="key").execute()
        return tok
    except Exception as e:  # noqa: BLE001
        log.warning("finds share_token failed: %s", e)
        return None


def rotate_share_token() -> str:
    tok = secrets.token_urlsafe(18)
    get_client().table("app_settings").upsert(
        {"key": _TOKEN_KEY, "value": tok,
         "description": "Public product-finds share-link token (rotate to revoke old links)"},
        on_conflict="key").execute()
    return tok


def public_finds(token: str) -> dict | None:
    """No-auth gallery for whoever holds the share link. None = bad token. Emails and other
    internal fields are stripped; archived finds are hidden."""
    good = share_token(create=False)
    if not good or not secrets.compare_digest(token, good):
        return None
    items = []
    for r in list_finds():
        if r.get("status") == "archived":
            continue
        items.append({
            "id": r["id"], "name": r.get("name"), "price_bhd": r.get("price_bhd"),
            "currency": r.get("currency"), "note": r.get("note"),
            "category": r.get("category"), "status": r.get("status"),
            "image_url": r.get("image_url"),
        })
    cats = sorted({(r.get("category") or "OTHER") for r in items})
    return {"items": items, "categories": cats, "company": "YQ Bahrain", "count": len(items)}


# ── promote a find into the real catalog ───────────────────────────────────────

def promote(find_id: int, item_code: str, by: str = "") -> dict:
    """Seed a catalog_items DRAFT (is_active=false) from a find and carry its photo across
    to the public 'catalog' bucket. The item stays hidden until its SKU enters the MA_base
    price book (which is what makes a catalog item live) — so promote never creates a phantom
    live product, and price-book sync will reactivate it once the SKU shows up."""
    code = (item_code or "").strip().upper()
    if not code:
        return {"ok": False, "reason": "item_code required"}
    row = (get_client().table("product_finds").select("*").eq("id", find_id)
           .limit(1).execute().data or [])
    if not row:
        return {"ok": False, "reason": "find not found"}
    find = row[0]
    from app.catalog import item_exists, upload_image, upsert_item
    # 1) create/complete the catalog row first (upload_image updates it by item_code)
    if not item_exists(code):
        upsert_item({
            "item_code": code,
            "display_name": find.get("name") or code,
            "spec": find.get("note"),
            "category": find.get("category"),
            "is_active": False,          # hidden until the SKU is in the price book
        }, by=by)
    # 2) copy the photo from the private finds bucket → public catalog bucket
    try:
        data = get_client().storage.from_(_BUCKET).download(find["image_path"])
        ext = (find["image_path"].rsplit(".", 1)[-1] or "jpg").lower()
        ctype = {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
        upload_image(code, "product", data, ctype, by=by)
    except Exception as e:  # noqa: BLE001
        log.warning("promote photo copy failed: %s", e)
    # 3) mark the find promoted
    get_client().table("product_finds").update(
        {"status": "promoted", "promoted_item_code": code, "reviewed_by": by,
         "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", find_id).execute()
    return {"ok": True, "item_code": code}
