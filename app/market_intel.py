"""Market Intelligence v1 (release R7b, plan §18): Product Finds + Field Notes merged into one capture.

A rep taps "Spotted" in the salesman app, takes 1-4 photos, adds a one-line note and (optionally) a
kind, a price, a brand, a demand level or a barcode typed off the pack. `capture()` answers at once
with a deterministic result card — nothing here calls an AI model or sends a photo anywhere:

    already_in_yq     the typed code / name is a YQ catalog item: the sighting is attached to it
                      (a competitor price, a shop's ask, feedback)
    seen_before       exact barcode, or the same kind + brand + category + normalised name within
                      30 days: "Added your sighting to X (now 4 shops)"
    new_find          text, no match: a new item, status New
    saved_for_review  a photo and no text: kept un-identified for the office (and the AI Head)

Photos: magic bytes checked (app.uploads.content_matches), decoded with Pillow, turned upright, cut to
1600 px and re-encoded WITHOUT any metadata (EXIF GPS never reaches storage), a 64-bit dHash is taken
and compared with the stored ones (Hamming <= 8 = "near-duplicate photo", a flag only — nothing ever
merges on fuzzy evidence). They live in the PRIVATE 'finds' bucket under market/; readers get
short-lived signed URLs, exactly like the Finds board.

Who sees what (the routes enforce the same): a salesman sees his own sightings and the aggregated
items (counts, never another rep's name, shop or note); the office (admin, member, operations,
sales manager, management) sees everything. Reviewers (admin / member / operations / sales manager)
change statuses and edit; management may only approve an Opportunity with an action.

The tables arrive with scripts/r7b_market_intel_migration.sql. Until then `available()` is False, the
reads answer empty + MISSING_HINT, and a capture is saved to the legacy product_finds / field_notes
tables (scripts/market_intel_import.py copies those in later), so the API may deploy first.
"""
from __future__ import annotations

import io
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.database import get_client

log = logging.getLogger(__name__)

# ── vocabulary ────────────────────────────────────────────────────────────────

KINDS = ("new_product", "competitor_price", "promotion", "shop_asked", "complaint", "other")
KIND_LABELS = {"new_product": "New product", "competitor_price": "Competitor price", "promotion": "Promotion",
               "shop_asked": "Shop asked for", "complaint": "Complaint", "other": "Other"}
STATUSES = ("new", "researching", "opportunity", "approved", "rejected", "merged")
STATUS_LABELS = {"new": "New", "researching": "Researching", "opportunity": "Opportunity", "approved": "Approved",
                 "rejected": "Rejected", "merged": "Merged"}
# what the rep reads on "My signals"
REP_STATUS_LABELS = {"new": "Office will review", "researching": "Office looking into it",
                     "opportunity": "Became an opportunity", "approved": "Office is acting on it",
                     "rejected": "Not taken forward", "merged": "Joined to an earlier sighting",
                     None: "Office will identify it"}
# New -> Researching -> Opportunity -> Approved | Rejected | Merged; a decision can be reopened.
NEXT_STATUS: dict[str, tuple[str, ...]] = {
    "new": ("researching", "opportunity", "rejected", "merged"),
    "researching": ("new", "opportunity", "rejected", "merged"),
    "opportunity": ("researching", "approved", "rejected", "merged"),
    "approved": ("opportunity",),
    "rejected": ("new",),
    "merged": (),
}
ACTIONS = ("source", "price_response", "promo", "ignore")
ACTION_LABELS = {"source": "Source it", "price_response": "Price response", "promo": "Run a promotion",
                 "ignore": "Ignore"}
DEMAND_LEVELS = ("low", "medium", "high")
RESULTS = ("already_in_yq", "seen_before", "new_find", "saved_for_review")
SOURCES = ("rep", "import", "system")
SIGNALS = ("search_zero", "restock_ask")

# Reviewers change statuses and edit items. Management reads everything and may approve an
# Opportunity with an action (plan §7: market_intel "read + approve actions"), nothing else.
REVIEW_ROLES = frozenset({"admin", "member", "operations", "sales_manager"})
APPROVE_ROLES = REVIEW_ROLES | {"management"}

# The capture sheet's brand picklist starts here; brands already on items are added to it.
DEFAULT_BRANDS = ("Anker", "Apple", "Baseus", "Borofone", "Earldom", "Green Lion", "Hoco", "Joyroom", "Ldnio",
                  "Porodo", "Powerology", "Remax", "Samsung", "Ugreen", "WEKOME", "Xiaomi")

MAX_PHOTOS = 4
MAX_EDGE = 1600                 # px, longest side (the client already resizes; this is the guard)
MAX_PIXELS = 40_000_000         # a decoded photo larger than this is refused (decompression bomb)
PHASH_NEAR = 8                  # Hamming distance: at or below = near-duplicate photo
PHASH_SCAN = 5000               # newest stored hashes compared per capture
SEEN_WINDOW_DAYS = 30
BUCKET = "finds"                # PRIVATE (created by app.product_finds); new captures go to market/...
BUCKETS = ("finds", "field-notes")
SIGNED_TTL = 60 * 60
MIGRATION = "scripts/r7b_market_intel_migration.sql"
MISSING_HINT = "Market Intel is not set up yet: apply scripts/r7b_market_intel_migration.sql."
# The 'system' demand signals and the import derive their client_uuid from a stable key (uuid5), so
# rolling the same week twice, or importing twice, writes nothing new.
UUID_NS = uuid.UUID("6b1d3f0e-2f49-4b8e-9d0c-5a7e0c9b7d21")

_PHOTO_EXTS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_STOP = frozenset({"the", "a", "an", "and", "for", "with", "of", "new", "pcs", "pc", "piece", "pieces",
                   "bd", "bhd", "fils", "only", "price"})
_THOUSANDTH = Decimal("0.001")


class MarketIntelError(ValueError):
    """A refusal the route turns into an HTTP answer (400 unless `status` says otherwise)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── availability probe (the migration may land after the API) ────────────────

_probe: dict = {"until": 0.0, "ok": False}
_PROBE_HIT_S, _PROBE_MISS_S = 600.0, 60.0


def is_missing_table(e: Exception) -> bool:
    """PostgREST's answers for a relation that is not there: 42P01 (undefined_table) and PGRST205
    ("Could not find the table ... in the schema cache")."""
    msg = f"{getattr(e, 'code', '')} {e}"
    return "42P01" in msg or "PGRST205" in msg or ("relation" in msg and "does not exist" in msg) \
        or "Could not find the table" in msg


def available(force: bool = False) -> bool:
    """True when the R7b tables exist. A hit is remembered for 10 minutes, a miss for one."""
    now = time.monotonic()
    if not force and _probe["until"] > now:
        return bool(_probe["ok"])
    try:
        get_client().table("market_observations").select("id").limit(1).execute()
        ok = True
    except Exception as e:  # noqa: BLE001 — a missing table (or a blip) means "not yet"
        log.debug("market intel probe failed: %s", e)
        ok = False
    _probe.update(until=now + (_PROBE_HIT_S if ok else _PROBE_MISS_S), ok=ok)
    return ok


def forget_probe() -> None:
    _probe.update(until=0.0, ok=False)


def _missing(e: Exception) -> bool:
    """A read or write met a missing table: forget the cached hit (the reverse ran)."""
    if is_missing_table(e):
        forget_probe()
        return True
    return False


# ── small helpers ─────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _parse_ts(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def clean_text(v, limit: int) -> str | None:
    """Control characters out, whitespace collapsed, capped; '' -> None."""
    s = re.sub(r"\s+", " ", _CTRL.sub("", str(v or ""))).strip()
    return s[:limit] or None


def money3(v) -> Decimal | None:
    """A BHD amount to exactly 3 dp (Decimal, never float). None / '' -> None; junk or out of range
    -> MarketIntelError."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        d = Decimal(str(v).strip().replace(",", "")).quantize(_THOUSANDTH, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise MarketIntelError("The price must be a number, like 2.500.") from None
    if not d.is_finite() or d < 0 or d >= 100000:
        raise MarketIntelError("The price must be between 0 and 99,999.999 BHD.")
    return d


def money_out(v) -> str | None:
    """A stored amount as the API answers it: a 3-dp string ("2.500"), never a float."""
    if v is None or v == "":
        return None
    try:
        return str(Decimal(str(v)).quantize(_THOUSANDTH, rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def norm_barcode(v) -> str | None:
    """Digits only; 6-20 of them (EAN-8/13, UPC-A, ITF-14 ...). Anything else is not a barcode key."""
    d = re.sub(r"\D", "", str(v or ""))
    return d if 6 <= len(d) <= 20 else None


def norm_name(text) -> str | None:
    """The "seen before" key: lower case, prices and punctuation out, stop words out, unique tokens
    sorted ("20W Anker charger" == "anker charger 20w"). Arabic letters are kept."""
    t = str(text or "").lower()
    t = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:bd|bhd|fils)\b", " ", t)
    t = re.sub(r"[^0-9a-z؀-ۿ]+", " ", t)
    toks = sorted({w for w in t.split() if w not in _STOP})
    key = " ".join(toks)[:160]
    return key or None


def _ci(v) -> str:
    return str(v or "").strip().lower()


def parse_uuid(v) -> str | None:
    try:
        return str(uuid.UUID(str(v).strip()))
    except (ValueError, AttributeError, TypeError):
        return None


def stable_uuid(key: str) -> str:
    return str(uuid.uuid5(UUID_NS, key))


# ── photos: validate, strip, resize, hash ─────────────────────────────────────

def dhash64(img) -> int:
    """64-bit difference hash: 9x8 greyscale, each pixel compared with its right neighbour. Unsigned
    0..2^64-1 (to_signed64 for the bigint column)."""
    from PIL import Image
    g = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = g.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = px[row * 9 + col], px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def to_signed64(u: int) -> int:
    return u - (1 << 64) if u >= (1 << 63) else u


def hamming(a: int | None, b: int | None) -> int:
    if a is None or b is None:
        return 64
    return bin((int(a) ^ int(b)) & ((1 << 64) - 1)).count("1")


def _webp_ok() -> bool:
    try:
        from PIL import features
        return bool(features.check("webp"))
    except Exception:  # noqa: BLE001
        return False


def process_photo(data: bytes, filename: str) -> dict:
    """Validate one uploaded photo and return it ready to store:
    {"data", "ext", "content_type", "width", "height", "bytes", "phash" (signed 64-bit)}.
    The bytes are re-encoded from pixels only, so no EXIF (GPS, device, time) survives."""
    from app.uploads import content_matches
    name = (filename or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in _PHOTO_EXTS:
        raise MarketIntelError("Please send a photo (JPG, PNG or WEBP).")
    if not data or not content_matches(name, data) or (ext == ".webp" and data[8:12] != b"WEBP"):
        raise MarketIntelError("That file isn't a valid photo.")
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
        w0, h0 = img.size
        if w0 * h0 > MAX_PIXELS:
            raise MarketIntelError("That photo is too large. Take it again with the camera.")
        img.load()
    except MarketIntelError:
        raise
    except Exception:  # noqa: BLE001 — truncated / corrupt / an unsupported codec
        raise MarketIntelError("That photo could not be read. Take it again with the camera.") from None
    img = ImageOps.exif_transpose(img)          # upright, as the rep saw it
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.split()[-1])
        img = base
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > MAX_EDGE:
        img.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    img.info = {}                               # nothing from the original file rides along
    out = io.BytesIO()
    if _webp_ok():
        img.save(out, format="WEBP", quality=80, method=4, exif=b"")
        ext_out, ctype = ".webp", "image/webp"
    else:
        img.save(out, format="JPEG", quality=82, optimize=True, progressive=True, exif=b"")
        ext_out, ctype = ".jpg", "image/jpeg"
    blob = out.getvalue()
    return {"data": blob, "ext": ext_out, "content_type": ctype, "width": img.size[0], "height": img.size[1],
            "bytes": len(blob), "phash": to_signed64(dhash64(img))}


def _upload(p: dict, now: datetime) -> str:
    """Store one processed photo in the private bucket; returns the object path."""
    from app.product_finds import ensure_bucket
    ensure_bucket()
    path = f"market/{now:%Y/%m}/{uuid.uuid4().hex}{p['ext']}"
    get_client().storage.from_(BUCKET).upload(path, p["data"], {"content-type": p["content_type"], "upsert": "false"})
    return path


def sign_many(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """{(bucket, path): signed URL} — one request per 100 paths per bucket, per-path as a fallback."""
    out: dict[tuple[str, str], str] = {}
    by_bucket: dict[str, list[str]] = {}
    for b, p in pairs:
        if b in BUCKETS and p and p not in by_bucket.setdefault(b, []):
            by_bucket[b].append(p)
    for b, paths in by_bucket.items():
        store = get_client().storage.from_(b)
        for i in range(0, len(paths), 100):
            chunk = paths[i:i + 100]
            try:
                for it in (store.create_signed_urls(chunk, SIGNED_TTL) or []):
                    url = it.get("signedURL") or it.get("signedUrl")
                    if it.get("path") and url and not it.get("error"):
                        out[(b, it["path"])] = url
            except Exception as e:  # noqa: BLE001
                log.warning("market intel batch sign failed (%d paths): %s", len(chunk), e)
                for p in chunk:
                    try:
                        res = store.create_signed_url(p, SIGNED_TTL) or {}
                        url = res.get("signedURL") or res.get("signedUrl")
                        if url:
                            out[(b, p)] = url
                    except Exception:  # noqa: BLE001
                        pass
    return out


# ── the catalog: "already in YQ" ──────────────────────────────────────────────

def _catalog_ctx() -> dict | None:
    """The shop's cached catalog context (items by code); None when it cannot be read."""
    try:
        from app import shop
        return shop.context()
    except Exception as e:  # noqa: BLE001
        log.info("market intel: catalog unavailable for matching: %s", e)
        return None


def _compact(s) -> str:
    return re.sub(r"[^0-9a-z]", "", str(s or "").lower())


def match_catalog(text, brand=None, ctx: dict | None = None) -> dict | None:
    """The YQ catalog item a typed code or name means, or None. Deterministic and conservative:
      1. the whole text is a code ("X05 UC-1Mtr", "x05uc1mtr");
      2. the first word is a code ("X05 cable") and no other brand was named;
      3. the normalised text equals a catalog display name.
    A different brand than the item's never matches by code (a "Joyroom T10" is not YQ's T10)."""
    t = clean_text(text, 160)
    if not t:
        return None
    ctx = ctx if ctx is not None else _catalog_ctx()
    items = (ctx or {}).get("items") or {}
    if not items:
        return None
    idx = ctx.get("_mi_index")
    if idx is None:
        idx = ctx["_mi_index"] = {
            "compact": {_compact(c): c for c in items if len(_compact(c)) >= 2},
            "names": {},
        }
        for c, it in items.items():
            k = norm_name(it.get("display_name"))
            if k:
                idx["names"].setdefault(k, c)       # first wins: a shared name never picks at random
    b = _ci(brand)

    def ok_brand(code: str) -> bool:
        ib = _ci(items[code].get("brand"))
        return not b or not ib or b == ib

    code = idx["compact"].get(_compact(t))
    if code and ok_brand(code):
        return items[code]
    first = t.split(" ", 1)[0]
    code = idx["compact"].get(_compact(first))
    if code and len(_compact(first)) >= 3 and ok_brand(code) and not (b and b not in ("vfan", "yq")):
        return items[code]
    code = idx["names"].get(norm_name(t) or "")
    if code and ok_brand(code):
        return items[code]
    return None


def yq_card(it: dict | None) -> dict | None:
    if not it:
        return None
    thumb = None
    try:
        from app.shop import _thumb
        thumb = _thumb(it)
    except Exception:  # noqa: BLE001
        pass
    return {"item_code": str(it.get("item_code")), "display_name": it.get("display_name") or str(it.get("item_code")),
            "category": it.get("category"), "brand": it.get("brand"), "thumb_url": thumb}


# ── people ────────────────────────────────────────────────────────────────────

def _salesman_for(email: str) -> dict | None:
    try:
        from app.shop import salesman_for_user
        return salesman_for_user(email)
    except Exception:  # noqa: BLE001
        return None


def _salesmen_names() -> dict[int, str]:
    try:
        rows = get_client().table("salesmen").select("id,name").execute().data or []
    except Exception:  # noqa: BLE001
        return {}
    return {int(r["id"]): r.get("name") or f"Rep {r['id']}" for r in rows if r.get("id") is not None}


def _shop_customer_id(phone) -> int | None:
    """shop_customers.id for a phone the capture sheet sent (the current shop pill). The phone
    itself is never stored on the sighting."""
    if not phone:
        return None
    try:
        from app.shop import phone_digits
        d = phone_digits(str(phone))
        if not d:
            return None
        got = get_client().table("shop_customers").select("id").eq("phone", d).limit(1).execute().data or []
        return int(got[0]["id"]) if got else None
    except Exception:  # noqa: BLE001
        return None


# ── capture ───────────────────────────────────────────────────────────────────

def clean_fields(raw: dict) -> dict:
    """The capture sheet's fields, validated. Unknown kind -> 'other'."""
    kind = str(raw.get("kind") or "").strip().lower() or "other"
    if kind not in KINDS:
        raise MarketIntelError("Unknown kind.")
    level = str(raw.get("demand_level") or "").strip().lower() or None
    if level is not None and level not in DEMAND_LEVELS:
        raise MarketIntelError("Demand is low, medium or high.")
    qty_raw = raw.get("demand_qty")
    qty = None
    if qty_raw not in (None, ""):
        try:
            qty = int(str(qty_raw).strip())
        except ValueError:
            raise MarketIntelError("The quantity must be a whole number.") from None
        if not 0 < qty <= 100000:
            raise MarketIntelError("The quantity must be between 1 and 100,000.")
    category = clean_text(raw.get("category"), 60)
    barcode_raw = clean_text(raw.get("barcode"), 40)
    return {
        "kind": kind,
        "note": clean_text(raw.get("note"), 1000),
        "title": clean_text(raw.get("title"), 160),
        "brand": clean_text(raw.get("brand"), 60),
        "competitor": clean_text(raw.get("competitor"), 80),
        "category": category.upper() if category else None,
        "price": money3(raw.get("price_bhd")),
        "demand_level": level,
        "demand_qty": qty,
        "shop_name": clean_text(raw.get("shop_name"), 120),
        "shop_phone": clean_text(raw.get("shop_phone"), 32),
        "area": clean_text(raw.get("area"), 60),
        "barcode_raw": barcode_raw,
        "barcode": norm_barcode(barcode_raw),
    }


def _obs_by_uuid(cu: str) -> dict | None:
    got = (get_client().table("market_observations").select("*").eq("client_uuid", cu).limit(1).execute().data or [])
    return got[0] if got else None


def _item(item_id) -> dict | None:
    if item_id in (None, ""):
        return None
    got = get_client().table("market_items").select("*").eq("id", int(item_id)).limit(1).execute().data or []
    return got[0] if got else None


def _live(item: dict | None, hops: int = 5) -> dict | None:
    """Follow merged_into to the item the sighting now belongs to."""
    while item and item.get("status") == "merged" and item.get("merged_into") and hops > 0:
        item = _item(item["merged_into"])
        hops -= 1
    return item


def find_item(f: dict, yq_code: str | None, now: datetime) -> dict | None:
    """The existing item a sighting belongs to, by the plan's keys in order: exact barcode, then the
    YQ SKU (+ kind), then kind + brand + category + normalised name seen within 30 days."""
    c = get_client()
    if f.get("barcode"):
        rows = c.table("market_items").select("*").eq("barcode", f["barcode"]).order("id").limit(5).execute().data or []
        for r in rows:
            live = _live(r)
            if live:
                return live
    if yq_code:
        rows = (c.table("market_items").select("*").eq("yq_item_code", yq_code).eq("kind", f["kind"])
                .order("last_seen", desc=True).limit(20).execute().data or [])
        rows = [r for r in rows if r.get("status") not in ("rejected", "merged")]
        if rows:
            return rows[0]
    key = norm_name(f.get("title") or f.get("note"))
    if key:
        since = now - timedelta(days=SEEN_WINDOW_DAYS)
        rows = (c.table("market_items").select("*").eq("kind", f["kind"]).eq("norm_key", key)
                .order("last_seen", desc=True).limit(20).execute().data or [])
        for r in rows:
            if r.get("status") in ("rejected", "merged"):
                continue
            seen = _parse_ts(r.get("last_seen")) or _parse_ts(r.get("created_at"))
            if seen and seen < since:
                continue
            if _ci(r.get("brand")) == _ci(f.get("brand")) and _ci(r.get("category")) == _ci(f.get("category")):
                return r
    return None


def near_duplicate(hashes: list[int], exclude_obs: int | None = None) -> dict | None:
    """The closest stored photo within PHASH_NEAR of any new hash: {observation_id, photo_id, distance}."""
    if not hashes:
        return None
    rows = (get_client().table("market_photos").select("id,observation_id,phash")
            .order("id", desc=True).limit(PHASH_SCAN).execute().data or [])
    best = None
    for r in rows:
        if r.get("phash") is None or r.get("observation_id") == exclude_obs:
            continue
        d = min(hamming(h, int(r["phash"])) for h in hashes)
        if d <= PHASH_NEAR and (best is None or d < best["distance"]):
            best = {"observation_id": r["observation_id"], "photo_id": r["id"], "distance": d}
    return best


def refresh_item(item_id: int) -> dict:
    """Recount an item from its sightings (distinct shops and reps) and cache it on the row."""
    obs = (get_client().table("market_observations")
           .select("id,observed_at,price_bhd,shop_customer_id,shop_name,salesman_id,created_by,source")
           .eq("item_id", item_id).execute().data or [])
    shops = {f"c{o['shop_customer_id']}" if o.get("shop_customer_id") else f"n{_ci(o.get('shop_name'))}"
             for o in obs if o.get("shop_customer_id") or _ci(o.get("shop_name"))}
    reps = {f"s{o['salesman_id']}" if o.get("salesman_id") else f"e{_ci(o.get('created_by'))}"
            for o in obs if o.get("source") == "rep" and (o.get("salesman_id") or _ci(o.get("created_by")))}
    prices = [Decimal(str(o["price_bhd"])) for o in obs if o.get("price_bhd") not in (None, "")]
    seen = sorted(t for t in (_parse_ts(o.get("observed_at")) for o in obs) if t)
    stats = {"obs_count": len(obs), "shop_count": len(shops), "rep_count": len(reps),
             "price_min": str(min(prices)) if prices else None, "price_max": str(max(prices)) if prices else None,
             "first_seen": _iso(seen[0]) if seen else None, "last_seen": _iso(seen[-1]) if seen else None}
    get_client().table("market_items").update({**stats, "updated_at": _iso()}).eq("id", item_id).execute()
    return stats


def _new_item(f: dict, yq_code: str | None, actor: str, now: datetime, title: str | None = None) -> dict:
    # Only a typed name (or the catalog's) becomes the title: a free-text note can name a shop or a
    # person, and item titles are what other reps see. The office reads the note as `first_note`.
    title = title or f.get("title") or None
    row = {"kind": f["kind"], "title": title, "norm_key": norm_name(f.get("title") or f.get("note")),
           "brand": f.get("brand"), "competitor": f.get("competitor"), "category": f.get("category"),
           "barcode": f.get("barcode"), "yq_item_code": yq_code, "status": "new",
           "first_seen": _iso(now), "last_seen": _iso(now), "created_by": actor or None, "updated_by": actor or None}
    return (get_client().table("market_items").insert(row).execute().data or [row])[0]


def _card(result: str, obs: dict, item: dict | None, stats: dict | None, yq: dict | None,
          near: dict | None, photos: int, *, replayed: bool = False) -> dict:
    shops = int((stats or {}).get("shop_count") or (item or {}).get("shop_count") or 0)
    title = (item or {}).get("title") or (yq or {}).get("display_name")
    if result == "already_in_yq":
        msg = f"We already sell this: {(yq or {}).get('display_name') or title or 'a YQ item'}. Your sighting is attached to it for the office."
    elif result == "seen_before":
        msg = (f"Added your sighting to “{title}”" if title else "Added your sighting to one the team already reported") \
            + (f" (now {shops} shop{'s' if shops != 1 else ''})." if shops else ".")
    elif result == "new_find":
        msg = "New find saved. The office will review it."
    else:
        msg = "Saved. The office will identify it from the photo."
    if near:
        msg += " This photo looks like one sent before; the office will check."
    out_item = None
    if item:
        out_item = {"id": item.get("id"), "title": item.get("title"), "kind": item.get("kind"),
                    "kind_label": KIND_LABELS.get(item.get("kind"), "Other"),
                    "status": item.get("status"), "status_label": REP_STATUS_LABELS.get(item.get("status"), ""),
                    "observations": int((stats or {}).get("obs_count") or item.get("obs_count") or 0),
                    "shops": shops, "reps": int((stats or {}).get("rep_count") or item.get("rep_count") or 0)}
    return {"ok": True, "result": result, "message": msg, "observation_id": obs.get("id"),
            "client_uuid": obs.get("client_uuid"), "item": out_item, "yq_item": yq,
            "near_duplicate": ({"observation_id": near["observation_id"]} if near else None),
            "photos": photos, "replayed": replayed}


def _replay(obs: dict) -> dict:
    """The card for a capture that already exists (a retry with the same client_uuid)."""
    item = _live(_item(obs.get("item_id")))
    yq = yq_card(((_catalog_ctx() or {}).get("items") or {}).get(obs.get("yq_item_code") or (item or {}).get("yq_item_code") or ""))
    try:
        n = len(get_client().table("market_photos").select("id").eq("observation_id", obs["id"]).execute().data or [])
    except Exception:  # noqa: BLE001
        n = 0
    near = {"observation_id": obs["near_duplicate_of"]} if obs.get("near_duplicate_of") else None
    return _card(obs.get("result") or ("saved_for_review" if not item else "new_find"), obs, item, None, yq, near, n,
                 replayed=True)


def _unique_violation(e: Exception) -> bool:
    msg = f"{getattr(e, 'code', '')} {e}"
    return "23505" in msg or "duplicate key" in msg


def capture(actor: str, raw: dict, photos: list[tuple[str, bytes]], *, now: datetime | None = None) -> dict:
    """One sighting from the capture sheet -> the instant result card (see the module doc).
    `photos` = [(filename, bytes)], at most MAX_PHOTOS. Every photo is validated before anything is
    written; a retry with the same client_uuid returns the stored card and writes nothing."""
    now = now or _now()
    actor = (actor or "").strip().lower()
    if len(photos) > MAX_PHOTOS:
        raise MarketIntelError(f"At most {MAX_PHOTOS} photos per sighting.")
    f = clean_fields(raw)
    cu = parse_uuid(raw.get("client_uuid")) or str(uuid.uuid4())
    if not photos and not (f["note"] or f["title"] or f["barcode"] or f["price"] is not None):
        raise MarketIntelError("Add a photo or a note.")
    processed = [process_photo(data, name) for name, data in photos]

    if not available():
        return legacy_capture(actor, f, processed, cu)

    try:
        existing = _obs_by_uuid(cu)
    except Exception as e:  # noqa: BLE001
        if _missing(e):
            return legacy_capture(actor, f, processed, cu)
        raise
    if existing:
        return _replay(existing)

    sm = _salesman_for(actor)
    ctx = _catalog_ctx()
    yq_item = match_catalog(f.get("title"), f.get("brand"), ctx) if f.get("title") else None
    yq_code = str(yq_item["item_code"]) if yq_item else None
    item = find_item(f, yq_code, now)
    if item and not yq_item and item.get("yq_item_code"):
        yq_item = ((ctx or {}).get("items") or {}).get(item["yq_item_code"])
        yq_code = str(yq_item["item_code"]) if yq_item else None
    has_text = bool(f["title"] or f["note"] or f["barcode"])
    if yq_item:
        result = "already_in_yq"
    elif item:
        result = "seen_before"
    elif has_text:
        result = "new_find"
    else:
        result = "saved_for_review"
    near = near_duplicate([p["phash"] for p in processed])

    paths: list[str] = []
    for p in processed:
        try:
            paths.append(_upload(p, now))
        except Exception as e:  # noqa: BLE001
            log.warning("market intel photo upload failed: %s", e)
            raise MarketIntelError("Could not save the photo. Please try again.", 503) from None

    obs_row = {
        "client_uuid": cu, "source": "rep", "kind": f["kind"],
        "salesman_id": (sm or {}).get("id"), "created_by": actor or None,
        "shop_customer_id": _shop_customer_id(f.get("shop_phone")), "shop_name": f.get("shop_name"),
        "area": f.get("area"), "title": f.get("title"), "brand": f.get("brand"), "competitor": f.get("competitor"),
        "category": f.get("category"), "note": f.get("note"),
        "price_bhd": str(f["price"]) if f["price"] is not None else None,
        "demand_level": f.get("demand_level"), "demand_qty": f.get("demand_qty"),
        "barcode_raw": f.get("barcode_raw"), "yq_item_code": yq_code, "result": result,
        "item_id": item.get("id") if item else None,
        "near_duplicate_of": near["observation_id"] if near else None,
        "observed_at": _iso(now),
    }
    try:
        obs = (get_client().table("market_observations").insert(obs_row).execute().data or [obs_row])[0]
    except Exception as e:  # noqa: BLE001
        if _unique_violation(e):            # the same capture raced itself: the first one wins
            again = _obs_by_uuid(cu)
            if again:
                return _replay(again)
        if _missing(e):
            return legacy_capture(actor, f, processed, cu)
        raise
    if not item and result in ("new_find", "already_in_yq"):
        item = _new_item(f, yq_code, actor, now, title=(yq_item or {}).get("display_name") if yq_item else None)
        get_client().table("market_observations").update({"item_id": item["id"]}).eq("id", obs["id"]).execute()
        obs["item_id"] = item["id"]
    if paths:
        get_client().table("market_photos").insert([
            {"observation_id": obs["id"], "bucket": BUCKET, "path": path, "position": i,
             "width": p["width"], "height": p["height"], "bytes": p["bytes"], "phash": p["phash"]}
            for i, (path, p) in enumerate(zip(paths, processed))]).execute()
    stats = refresh_item(int(item["id"])) if item else None
    return _card(result, obs, item, stats, yq_card(yq_item), near, len(paths))


# ── before the migration: the legacy boards keep the capture ──────────────────

_LEGACY_NOTE_CATEGORY = {"competitor_price": "competitor_price", "shop_asked": "demand", "new_product": "new_product",
                         "complaint": "complaint", "promotion": "other", "other": "other"}


def _legacy_note(f: dict) -> str:
    bits = [f"[{KIND_LABELS.get(f['kind'], 'Other')}]"]
    for label, key in (("", "note"), ("brand", "brand"), ("competitor", "competitor"), ("shop", "shop_name"),
                       ("area", "area"), ("barcode", "barcode_raw"), ("demand", "demand_level")):
        v = f.get(key)
        if v:
            bits.append(f"{label}: {v}" if label else str(v))
    if f.get("demand_qty"):
        bits.append(f"qty: {f['demand_qty']}")
    return " · ".join(bits)[:2000]


def legacy_capture(actor: str, f: dict, processed: list[dict], cu: str) -> dict:
    """Before scripts/r7b_market_intel_migration.sql: photos become Product Finds rows (source_file
    'mi:<client_uuid>:<n>', which the import script groups back into one sighting), a note-only capture
    a Field Note. A retry with the same client_uuid finds its rows and writes nothing."""
    from app import product_finds
    c = get_client()
    card = {"ok": True, "result": "saved_for_review", "fallback": "legacy", "item": None, "yq_item": None,
            "near_duplicate": None, "observation_id": None, "client_uuid": cu, "photos": len(processed),
            "message": "Saved. The office will review it.", "replayed": False}
    note = _legacy_note(f)
    if processed:
        tag = f"mi:{cu}:"
        if c.table("product_finds").select("id").eq("source_file", tag + "0").limit(1).execute().data:
            return {**card, "replayed": True}
        rows = []
        for i, p in enumerate(processed):
            path = product_finds.upload_photo(p["data"], p["ext"], p["content_type"])
            if not path:
                raise MarketIntelError("Could not save the photo. Please try again.", 503)
            rows.append({"image_path": path, "name": f.get("title"),
                         "price_bhd": str(f["price"]) if f.get("price") is not None else None,
                         "currency": "BHD", "note": note, "category": f.get("category"), "source": "Market Intel",
                         "source_file": tag + str(i), "posted_by": actor or None})
        c.table("product_finds").insert(rows).execute()
        return card
    text = note if not f.get("title") else f"{f['title']} · {note}"
    if c.table("field_notes").select("id").eq("created_by", actor).eq("note", text[:2000]).limit(1).execute().data:
        return {**card, "photos": 0, "replayed": True}
    c.table("field_notes").insert({"note": text[:2000], "category": _LEGACY_NOTE_CATEGORY.get(f["kind"], "other"),
                                   "created_by": actor or None}).execute()
    return {**card, "photos": 0}


# ── reading: the board, the item, the review queue, my signals ───────────────

def _cluster_out(r: dict, signed: dict, *, office: bool) -> dict:
    pmin, pmax = money_out(r.get("price_min")), money_out(r.get("price_max"))
    cover = signed.get((r.get("cover_bucket") or "", r.get("cover_path") or "")) if r.get("cover_path") else None
    # an item without a typed name shows the office its first note; a rep only ever sees a typed name
    title = r.get("title") or ((clean_text(r.get("first_note"), 80) if office else None))
    out = {"item_id": r.get("item_id"), "kind": r.get("kind"), "kind_label": KIND_LABELS.get(r.get("kind"), "Other"),
           "title": title, "brand": r.get("brand"), "competitor": r.get("competitor"),
           "category": r.get("category"), "barcode": r.get("barcode"), "yq_item_code": r.get("yq_item_code"),
           "status": r.get("status"),
           "status_label": (STATUS_LABELS if office else REP_STATUS_LABELS).get(r.get("status"), ""),
           "action": r.get("action"), "action_label": ACTION_LABELS.get(r.get("action")) if r.get("action") else None,
           "observations": int(r.get("observations") or 0), "shops": int(r.get("shops") or 0),
           "reps": int(r.get("reps") or 0), "price_min": pmin, "price_max": pmax,
           "first_seen": r.get("first_seen"), "last_seen": r.get("last_seen"),
           "photos": int(r.get("photos") or 0), "cover_url": cover,
           "system_signals": int(r.get("system_signals") or 0), "demand_qty": r.get("demand_qty")}
    if office:
        out.update(near_duplicates=int(r.get("near_duplicates") or 0), merged_into=r.get("merged_into"),
                   has_ai_suggestion=bool(r.get("has_ai_suggestion")), ai_confidence=r.get("ai_confidence"),
                   verified=bool(r.get("verified")), photos_people=int(r.get("photos_people") or 0))
    out["summary"] = cluster_summary(out)
    return out


def _day(v) -> str | None:
    d = _parse_ts(v)
    return f"{d.day} {d:%b}" if d else None


def cluster_summary(c: dict) -> str:
    """'7 sightings across 5 shops by 3 reps, BHD 2.500-3.000, first seen 12 Oct'."""
    n = int(c.get("observations") or 0)
    bits = [f"{n} sighting{'s' if n != 1 else ''}"]
    if c.get("shops"):
        bits.append(f"across {c['shops']} shop{'s' if c['shops'] != 1 else ''}")
    if c.get("reps"):
        bits.append(f"by {c['reps']} rep{'s' if c['reps'] != 1 else ''}")
    text = " ".join(bits)
    if c.get("price_min"):
        text += f", BHD {c['price_min']}" + (f"–{c['price_max']}" if c.get("price_max") and c["price_max"] != c["price_min"] else "")
    first = _day(c.get("first_seen"))
    if first:
        text += f", first seen {first}"
    return text


def _unavailable(extra: dict | None = None) -> dict:
    return {"available": False, "hint": MISSING_HINT, **(extra or {})}


def board(*, office: bool, actor: str = "", filters: dict | None = None, limit: int = 300) -> dict:
    """The Market Intel board: clusters (one per item), newest sighting first, with filters
    kind / status / brand / category / rep / area / since / q (text, barcode, code)."""
    f = filters or {}
    if not available():
        return _unavailable({"items": [], "facets": {}, "queue": {"new_items": 0, "unassigned": 0}})
    c = get_client()
    try:
        rows = c.table("v_market_clusters").select("*").order("last_seen", desc=True).limit(2000).execute().data or []
    except Exception as e:  # noqa: BLE001
        if _missing(e):
            return _unavailable({"items": [], "facets": {}, "queue": {"new_items": 0, "unassigned": 0}})
        raise
    all_rows = rows
    status = f.get("status")
    if status in STATUSES:
        rows = [r for r in rows if r.get("status") == status]
    else:
        rows = [r for r in rows if r.get("status") != "merged"]
    if f.get("kind") in KINDS:
        rows = [r for r in rows if r.get("kind") == f["kind"]]
    if f.get("brand"):
        rows = [r for r in rows if _ci(r.get("brand")) == _ci(f["brand"]) or _ci(r.get("competitor")) == _ci(f["brand"])]
    if f.get("category"):
        rows = [r for r in rows if _ci(r.get("category")) == _ci(f["category"])]
    since = _parse_ts(f.get("since"))
    if since:
        rows = [r for r in rows if (_parse_ts(r.get("last_seen")) or since) >= since]
    q = _ci(f.get("q"))
    if q:
        qd = re.sub(r"\D", "", q)
        rows = [r for r in rows if q in _ci(r.get("title")) or q in _ci(r.get("brand")) or q in _ci(r.get("competitor"))
                or q in _ci(r.get("yq_item_code")) or (qd and len(qd) >= 6 and qd in str(r.get("barcode") or ""))]
    mine_ids: set[int] = set()
    if not office and actor:
        own = (c.table("market_observations").select("item_id").eq("created_by", actor.strip().lower())
               .limit(2000).execute().data or [])
        mine_ids = {int(o["item_id"]) for o in own if o.get("item_id") is not None}
    if office and (f.get("rep") or f.get("area")):
        q2 = c.table("market_observations").select("item_id,salesman_id,area")
        if f.get("rep"):
            try:
                q2 = q2.eq("salesman_id", int(f["rep"]))
            except (TypeError, ValueError):
                raise MarketIntelError("Unknown rep.") from None
        obs = q2.limit(5000).execute().data or []
        if f.get("area"):
            obs = [o for o in obs if _ci(o.get("area")) == _ci(f["area"])]
        keep = {int(o["item_id"]) for o in obs if o.get("item_id") is not None}
        rows = [r for r in rows if int(r["item_id"]) in keep]
    if f.get("mine") and not office:
        rows = [r for r in rows if int(r["item_id"]) in mine_ids]
    rows = rows[: max(1, min(int(limit or 300), 1000))]
    signed = sign_many([(r.get("cover_bucket"), r.get("cover_path")) for r in rows if r.get("cover_path")])
    items = []
    for r in rows:
        it = _cluster_out(r, signed, office=office)
        if not office:
            it["mine"] = int(r["item_id"]) in mine_ids
        items.append(it)
    return {"available": True, "items": items, "count": len(items),
            "facets": facets(all_rows, office=office), "queue": queue_counts() if office else None}


def facets(rows: list[dict], *, office: bool) -> dict:
    status_counts = {s: 0 for s in STATUSES}
    for r in rows:
        if r.get("status") in status_counts:
            status_counts[r["status"]] += 1
    brands = sorted({(r.get("brand") or "").strip() for r in rows if (r.get("brand") or "").strip()}
                    | set(DEFAULT_BRANDS), key=str.lower)
    try:
        from app.catalog import CATEGORY_ORDER
        cats = list(CATEGORY_ORDER)
    except Exception:  # noqa: BLE001
        cats = []
    cats += sorted({r["category"] for r in rows if r.get("category") and r["category"] not in cats})
    out = {"kinds": [{"value": k, "label": KIND_LABELS[k]} for k in KINDS],
           "statuses": [{"value": s, "label": STATUS_LABELS[s], "count": status_counts[s]} for s in STATUSES],
           "actions": [{"value": a, "label": ACTION_LABELS[a]} for a in ACTIONS],
           "categories": cats, "brands": brands, "demand_levels": list(DEMAND_LEVELS)}
    if office:
        try:
            obs = (get_client().table("market_observations").select("salesman_id,area").eq("source", "rep")
                   .limit(5000).execute().data or [])
        except Exception:  # noqa: BLE001
            obs = []
        names = _salesmen_names()
        rep_ids = sorted({int(o["salesman_id"]) for o in obs if o.get("salesman_id") is not None})
        out["reps"] = [{"id": i, "name": names.get(i, f"Rep {i}")} for i in rep_ids]
        out["areas"] = sorted({o["area"] for o in obs if o.get("area")}, key=str.lower)
    return out


def queue_counts() -> dict:
    c = get_client()
    try:
        new_items = c.table("market_items").select("id", count="exact").eq("status", "new").limit(1).execute()
        unassigned = (c.table("market_observations").select("id,source").is_("item_id", "null")
                      .limit(5000).execute().data or [])
    except Exception as e:  # noqa: BLE001
        _missing(e)
        return {"new_items": 0, "unassigned": 0, "library": 0}
    return {"new_items": int(new_items.count if new_items.count is not None else len(new_items.data or [])),
            "unassigned": sum(1 for o in unassigned if o.get("source") != "import"),
            "library": sum(1 for o in unassigned if o.get("source") == "import")}


def _photos_for(obs_ids: list[int]) -> dict[int, list[dict]]:
    if not obs_ids:
        return {}
    out: dict[int, list[dict]] = {}
    for i in range(0, len(obs_ids), 200):
        rows = (get_client().table("market_photos").select("id,observation_id,bucket,path,position,width,height,has_people_flag")
                .in_("observation_id", obs_ids[i:i + 200]).execute().data or [])
        for r in rows:
            out.setdefault(int(r["observation_id"]), []).append(r)
    for v in out.values():
        v.sort(key=lambda p: (p.get("position") or 0, p.get("id") or 0))
    return out


def _photo_out(p: dict, signed: dict, *, office: bool) -> dict | None:
    if p.get("has_people_flag") and not office:
        return None
    return {"id": p.get("id"), "url": signed.get((p.get("bucket"), p.get("path"))), "width": p.get("width"),
            "height": p.get("height"), "has_people": bool(p.get("has_people_flag"))}


def _obs_out(o: dict, photos: list[dict], signed: dict, names: dict[int, str], *, office: bool, actor: str) -> dict:
    mine = bool(actor) and _ci(o.get("created_by")) == _ci(actor)
    out = {"id": o.get("id"), "kind": o.get("kind"), "kind_label": KIND_LABELS.get(o.get("kind"), "Other"),
           "source": o.get("source"), "signal": o.get("signal"), "title": o.get("title"), "note": o.get("note"),
           "brand": o.get("brand"), "competitor": o.get("competitor"), "category": o.get("category"),
           "price_bhd": money_out(o.get("price_bhd")), "demand_level": o.get("demand_level"),
           "demand_qty": o.get("demand_qty"), "barcode": o.get("barcode_raw"), "yq_item_code": o.get("yq_item_code"),
           "result": o.get("result"), "observed_at": o.get("observed_at"), "area": o.get("area"),
           "shop_name": o.get("shop_name"), "item_id": o.get("item_id"), "mine": mine,
           "near_duplicate_of": o.get("near_duplicate_of") if office else None,
           "photos": [x for x in (_photo_out(p, signed, office=office) for p in photos) if x]}
    if office:
        sid = o.get("salesman_id")
        if o.get("source") == "system":
            who = "Marketplace demand"
        elif o.get("source") == "import":
            who = "Imported from Finds" if str(o.get("legacy_ref") or "").startswith("product_finds") else "Imported note"
        else:
            who = names.get(int(sid)) if sid is not None else (str(o.get("created_by") or "").split("@")[0] or "Office")
        out["by"] = who
    return out


def item_detail(item_id: int, *, office: bool, actor: str = "") -> dict:
    """One item: the cluster, the photo strip, the sightings (a rep: only his own), the shops seen at,
    the linked YQ SKU or "gap", the AI suggestion (Unverified) and the decision history (office)."""
    if not available():
        raise MarketIntelError(MISSING_HINT, 409)
    c = get_client()
    item = _item(item_id)
    if not item:
        raise MarketIntelError("That item does not exist.", 404)
    cl = c.table("v_market_clusters").select("*").eq("item_id", item_id).limit(1).execute().data or []
    q = c.table("market_observations").select("*").eq("item_id", item_id)
    if not office:
        q = q.eq("created_by", (actor or "").strip().lower())
    obs = q.order("observed_at", desc=True).order("id", desc=True).limit(500).execute().data or []
    photos = _photos_for([int(o["id"]) for o in obs])
    signed = sign_many([(p.get("bucket"), p.get("path")) for ps in photos.values() for p in ps
                        if office or not p.get("has_people_flag")] +
                       ([(cl[0].get("cover_bucket"), cl[0].get("cover_path"))] if cl and cl[0].get("cover_path") else []))
    names = _salesmen_names() if office else {}
    cluster = _cluster_out(cl[0], signed, office=office) if cl else None
    ctx_items = (_catalog_ctx() or {}).get("items") or {}
    out = {"available": True, "item": {
               "id": item["id"], "kind": item.get("kind"), "kind_label": KIND_LABELS.get(item.get("kind"), "Other"),
               "title": item.get("title") or (clean_text((cl[0] if cl else {}).get("first_note"), 80) if office else None),
               "brand": item.get("brand"), "competitor": item.get("competitor"),
               "category": item.get("category"), "barcode": item.get("barcode"),
               "yq_item_code": item.get("yq_item_code"), "status": item.get("status"),
               "status_label": (STATUS_LABELS if office else REP_STATUS_LABELS).get(item.get("status"), ""),
               "action": item.get("action"), "action_label": ACTION_LABELS.get(item.get("action")) if item.get("action") else None,
               "merged_into": item.get("merged_into"), "next_statuses": list(NEXT_STATUS.get(item.get("status"), ())) if office else []},
           "cluster": cluster,
           "yq_item": yq_card(ctx_items.get(item.get("yq_item_code") or "")) if item.get("yq_item_code") else None,
           "gap": not item.get("yq_item_code"),
           "observations": [_obs_out(o, photos.get(int(o["id"]), []), signed, names, office=office, actor=actor) for o in obs]}
    if office:
        shops: dict[str, dict] = {}
        for o in obs:
            name = o.get("shop_name")
            if not name and not o.get("shop_customer_id"):
                continue
            k = f"c{o['shop_customer_id']}" if o.get("shop_customer_id") else f"n{_ci(name)}"
            s = shops.setdefault(k, {"shop": name or "Shop", "area": o.get("area"), "sightings": 0})
            s["sightings"] += 1
        out["shops"] = sorted(shops.values(), key=lambda s: -s["sightings"])
        ai = item.get("ai_suggestion")
        out["ai_suggestion"] = ({"suggestion": ai, "confidence": item.get("ai_confidence"),
                                 "verified": bool(item.get("verified")),
                                 "label": "Verified" if item.get("verified") else "Unverified"} if ai else None)
        try:
            dec = (c.table("market_item_decisions").select("*").eq("item_id", item_id)
                   .order("created_at", desc=True).limit(200).execute().data or [])
        except Exception:  # noqa: BLE001
            dec = []
        out["decisions"] = [{"id": d.get("id"), "event": d.get("event"), "from_status": d.get("from_status"),
                             "to_status": d.get("to_status"), "action": d.get("action"),
                             "action_label": ACTION_LABELS.get(d.get("action")) if d.get("action") else None,
                             "reason": d.get("reason"), "actor": str(d.get("actor") or "").split("@")[0],
                             "actor_role": d.get("actor_role"), "detail": d.get("detail"),
                             "created_at": d.get("created_at")} for d in dec]
    return out


def review_queue(*, library: bool = False, limit: int = 60, offset: int = 0) -> dict:
    """Office: the new items and the un-identified sightings (photo only). library=True browses the
    imported baseline (the old Finds board) instead."""
    if not available():
        return _unavailable({"new_items": [], "unassigned": [], "count": 0})
    c = get_client()
    lim = max(1, min(int(limit or 60), 200))
    off = max(0, int(offset or 0))
    obs = (c.table("market_observations").select("*").is_("item_id", "null")
           .order("observed_at", desc=True).order("id", desc=True).limit(5000).execute().data or [])
    obs = [o for o in obs if (o.get("source") == "import") == library]
    total = len(obs)
    page = obs[off:off + lim]
    photos = _photos_for([int(o["id"]) for o in page])
    new_rows = [] if library else (c.table("v_market_clusters").select("*").eq("status", "new")
                                   .order("last_seen", desc=True).limit(200).execute().data or [])
    signed = sign_many([(p.get("bucket"), p.get("path")) for ps in photos.values() for p in ps] +
                       [(r.get("cover_bucket"), r.get("cover_path")) for r in new_rows if r.get("cover_path")])
    names = _salesmen_names()
    return {"available": True,
            "new_items": [_cluster_out(r, signed, office=True) for r in new_rows],
            "unassigned": [_obs_out(o, photos.get(int(o["id"]), []), signed, names, office=True, actor="") for o in page],
            "count": total, "offset": off, "limit": lim}


def mine(actor: str, limit: int = 50) -> dict:
    """The rep's own sightings, newest first, with what became of each ("My signals")."""
    if not available():
        return _unavailable({"observations": []})
    c = get_client()
    obs = (c.table("market_observations").select("*").eq("created_by", (actor or "").strip().lower())
           .order("created_at", desc=True).order("id", desc=True)
           .limit(max(1, min(int(limit or 50), 200))).execute().data or [])
    item_ids = sorted({int(o["item_id"]) for o in obs if o.get("item_id") is not None})
    items: dict[int, dict] = {}
    if item_ids:
        for r in c.table("market_items").select("id,title,status,merged_into,shop_count,obs_count").in_("id", item_ids).execute().data or []:
            items[int(r["id"])] = r
    photos = _photos_for([int(o["id"]) for o in obs])
    signed = sign_many([(p.get("bucket"), p.get("path")) for ps in photos.values() for p in ps if not p.get("has_people_flag")])
    out = []
    for o in obs:
        it = items.get(int(o["item_id"])) if o.get("item_id") is not None else None
        row = _obs_out(o, photos.get(int(o["id"]), []), signed, {}, office=False, actor=actor)
        row["shop_name"] = o.get("shop_name")         # his own shop: fine to show back to him
        row["item_title"] = (it or {}).get("title")
        row["item_status"] = (it or {}).get("status")
        row["status_label"] = REP_STATUS_LABELS.get((it or {}).get("status"))
        row["item_shops"] = int((it or {}).get("shop_count") or 0)
        out.append(row)
    return {"available": True, "observations": out}


# ── deciding: statuses, edits, identify, approve, photo flag ─────────────────

_EDITABLE = ("kind", "title", "brand", "competitor", "category", "barcode", "yq_item_code")


def _decision(item_id: int, event: str, actor: str, role: str, *, from_status=None, to_status=None, action=None,
              reason=None, detail=None) -> None:
    get_client().table("market_item_decisions").insert(
        {"item_id": item_id, "event": event, "from_status": from_status, "to_status": to_status, "action": action,
         "reason": reason, "actor": actor or "unknown", "actor_role": role, "detail": detail}).execute()


def _clean_edit(k: str, v):
    if k == "kind":
        v = str(v or "").strip().lower()
        if v not in KINDS:
            raise MarketIntelError("Unknown kind.")
        return v
    if k == "barcode":
        if v in (None, ""):
            return None
        b = norm_barcode(v)
        if not b:
            raise MarketIntelError("A barcode is 6 to 20 digits.")
        return b
    if k == "category":
        v = clean_text(v, 60)
        return v.upper() if v else None
    if k == "yq_item_code":
        v = clean_text(v, 64)
        if not v:
            return None
        from app.shop import resolve_code
        ctx = _catalog_ctx()
        code = resolve_code(ctx, v) if ctx else None
        if not code:
            raise MarketIntelError(f"{v} is not a YQ catalog code.")
        return code
    return clean_text(v, 160 if k == "title" else 80)


def update_item(item_id: int, changes: dict, actor: str, role: str) -> dict:
    """Reviewer edit: fields and/or a status move (with an action for Approved, a target for Merged,
    and an optional reason). Every change leaves a decision row."""
    if role not in REVIEW_ROLES:
        raise MarketIntelError("Only the office can change Market Intel items.", 403)
    if not available():
        raise MarketIntelError(MISSING_HINT, 409)
    item = _item(item_id)
    if not item:
        raise MarketIntelError("That item does not exist.", 404)
    reason = clean_text(changes.get("reason"), 500)
    upd: dict = {}
    diffs: dict = {}
    for k in _EDITABLE:
        if k in changes:
            v = _clean_edit(k, changes[k])
            if v != item.get(k):
                upd[k] = v
                diffs[k] = {"from": item.get(k), "to": v}
    if "title" in upd:
        upd["norm_key"] = norm_name(upd["title"])
    if changes.get("verified") is not None:
        # a person confirms (or withdraws) the AI Head's reading; there is nothing to verify without one
        want = bool(changes["verified"])
        if want and not item.get("ai_suggestion"):
            raise MarketIntelError("There is no AI suggestion to verify.")
        if want != bool(item.get("verified")):
            upd["verified"] = want
            diffs["verified"] = {"from": bool(item.get("verified")), "to": want}
    new_status = changes.get("status")
    moved: list[int] = []
    target = None
    if new_status is not None and new_status != item.get("status"):
        if new_status not in STATUSES:
            raise MarketIntelError("Unknown status.")
        cur = item.get("status")
        if new_status not in NEXT_STATUS.get(cur, ()):
            raise MarketIntelError(f"An item that is {STATUS_LABELS.get(cur, cur)} cannot move to "
                                   f"{STATUS_LABELS[new_status]}.", 409)
        action = changes.get("action")
        if new_status == "approved":
            if action not in ACTIONS:
                raise MarketIntelError("Choose the action: source it, price response, promotion or ignore.")
            upd["action"] = action
        elif cur == "approved":
            upd["action"] = None
        if new_status == "merged":
            try:
                target = _live(_item(int(changes.get("merged_into"))))
            except (TypeError, ValueError):
                target = None
            if not target or int(target["id"]) == int(item_id):
                raise MarketIntelError("Pick the item this one duplicates.")
            upd["merged_into"] = int(target["id"])
        upd.update(status=new_status, status_reason=reason, status_changed_at=_iso(), status_changed_by=actor)
    if not upd:
        return {"ok": True, "changed": False, "item_id": item_id}
    upd.update(updated_at=_iso(), updated_by=actor)
    got = (get_client().table("market_items").update(upd).eq("id", item_id).eq("status", item.get("status"))
           .execute().data)
    if got is not None and len(got) == 0:
        raise MarketIntelError("Someone changed this item meanwhile. Reload and try again.", 409)
    if diffs:
        _decision(item_id, "edit", actor, role, reason=reason, detail=diffs)
    if "status" in upd:
        if new_status == "merged" and target:
            own = (get_client().table("market_observations").select("id").eq("item_id", item_id).execute().data or [])
            moved = [int(o["id"]) for o in own]
            if moved:
                get_client().table("market_observations").update({"item_id": int(target["id"])}).eq("item_id", item_id).execute()
            refresh_item(int(target["id"]))
            refresh_item(item_id)
            _decision(item_id, "merge", actor, role, from_status=item.get("status"), to_status="merged", reason=reason,
                      detail={"merged_into": int(target["id"]), "moved_observations": moved})
        else:
            _decision(item_id, "approve" if new_status == "approved" else "status", actor, role,
                      from_status=item.get("status"), to_status=new_status, action=upd.get("action"), reason=reason)
    return {"ok": True, "changed": True, "item_id": item_id, "status": upd.get("status", item.get("status")),
            "moved_observations": len(moved)}


def approve_item(item_id: int, action: str, reason: str | None, actor: str, role: str) -> dict:
    """Management (or a reviewer) chooses what to do about an Opportunity: source it, a price response,
    a promotion, or ignore it. Only Opportunity -> Approved; nothing else about the item changes."""
    if role not in APPROVE_ROLES:
        raise MarketIntelError("Only the office or management can approve an action.", 403)
    if action not in ACTIONS:
        raise MarketIntelError("Choose the action: source it, price response, promotion or ignore.")
    if not available():
        raise MarketIntelError(MISSING_HINT, 409)
    item = _item(item_id)
    if not item:
        raise MarketIntelError("That item does not exist.", 404)
    if item.get("status") != "opportunity":
        raise MarketIntelError("Only an Opportunity can be approved.", 409)
    why = clean_text(reason, 500)
    got = (get_client().table("market_items").update(
        {"status": "approved", "action": action, "status_reason": why, "status_changed_at": _iso(),
         "status_changed_by": actor, "updated_at": _iso(), "updated_by": actor})
        .eq("id", item_id).eq("status", "opportunity").execute().data)
    if got is not None and len(got) == 0:
        raise MarketIntelError("Someone changed this item meanwhile. Reload and try again.", 409)
    _decision(item_id, "approve", actor, role, from_status="opportunity", to_status="approved", action=action, reason=why)
    return {"ok": True, "item_id": item_id, "status": "approved", "action": action}


def identify_observation(obs_id: int, body: dict, actor: str, role: str) -> dict:
    """Reviewer: put an un-identified sighting on an item — an existing one (item_id) or a new one
    made from title / kind / brand / category / barcode / yq_item_code."""
    if role not in REVIEW_ROLES:
        raise MarketIntelError("Only the office can identify sightings.", 403)
    if not available():
        raise MarketIntelError(MISSING_HINT, 409)
    c = get_client()
    got = c.table("market_observations").select("*").eq("id", obs_id).limit(1).execute().data or []
    if not got:
        raise MarketIntelError("That sighting does not exist.", 404)
    obs = got[0]
    before = obs.get("item_id")
    if body.get("item_id") not in (None, ""):
        try:
            item = _live(_item(int(body["item_id"])))
        except (TypeError, ValueError):
            item = None
        if not item:
            raise MarketIntelError("That item does not exist.", 404)
        created = False
    else:
        f = {"kind": body.get("kind") or obs.get("kind") or "other", "title": clean_text(body.get("title"), 160),
             "note": None, "brand": clean_text(body.get("brand"), 60) or obs.get("brand"),
             "competitor": clean_text(body.get("competitor"), 80) or obs.get("competitor"),
             "category": (clean_text(body.get("category"), 60) or obs.get("category") or "").upper() or None,
             "barcode": norm_barcode(body.get("barcode")) or norm_barcode(obs.get("barcode_raw"))}
        if f["kind"] not in KINDS:
            raise MarketIntelError("Unknown kind.")
        if not f["title"]:
            raise MarketIntelError("Name the item (or pick an existing one).")
        code = _clean_edit("yq_item_code", body.get("yq_item_code")) if body.get("yq_item_code") else None
        item = _new_item(f, code, actor, _parse_ts(obs.get("observed_at")) or _now())
        created = True
    c.table("market_observations").update({"item_id": int(item["id"])}).eq("id", obs_id).execute()
    refresh_item(int(item["id"]))
    if before and int(before) != int(item["id"]):
        refresh_item(int(before))
    _decision(int(item["id"]), "identify", actor, role, to_status=item.get("status"),
              detail={"observation_id": obs_id, "created_item": created, "previous_item": before})
    return {"ok": True, "observation_id": obs_id, "item_id": int(item["id"]), "created": created}


def flag_photo(photo_id: int, flag: bool, actor: str, role: str) -> dict:
    """Reviewer: people in a photo -> kept for the office, excluded from every report and the agent view."""
    if role not in REVIEW_ROLES:
        raise MarketIntelError("Only the office can flag photos.", 403)
    if not available():
        raise MarketIntelError(MISSING_HINT, 409)
    got = (get_client().table("market_photos").update({"has_people_flag": bool(flag)})
           .eq("id", photo_id).execute().data)
    if got is not None and len(got) == 0:
        raise MarketIntelError("That photo does not exist.", 404)
    # on the item's history too, when the photo's sighting is on an item (who flagged it, and when)
    try:
        oid = (got or [{}])[0].get("observation_id")
        obs = get_client().table("market_observations").select("item_id").eq("id", oid).limit(1).execute().data or []
        if obs and obs[0].get("item_id"):
            _decision(int(obs[0]["item_id"]), "edit", actor, role,
                      detail={"photo_people": {"photo_id": photo_id, "to": bool(flag)}})
    except Exception as e:  # noqa: BLE001 — the flag itself is saved; the history line is best-effort
        log.info("market intel photo flag history not written: %s", e)
    return {"ok": True, "photo_id": photo_id, "has_people": bool(flag)}


# ── system demand signals (zero-result searches, restock asks) ────────────────

def _week_start(dt: datetime) -> str:
    d = dt.astimezone(timezone.utc).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def _search_terms(rows: list[dict]) -> dict[str, dict]:
    """Zero-result searches grouped by a cleaned term. A term that only starts a longer one in the
    same batch ("san" -> "sandisk", "weko" -> "wekome") is folded into it: it was being typed."""
    terms: dict[str, dict] = {}
    for r in rows:
        meta = r.get("meta") or {}
        t = re.sub(r"[^0-9a-z؀-ۿ ]+", " ", str(meta.get("q") or "").lower())
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) < 3 or t.isdigit():
            continue
        g = terms.setdefault(t, {"n": 0, "sessions": set(), "last": None})
        g["n"] += 1
        if r.get("session_id"):
            g["sessions"].add(r["session_id"])
        ts = _parse_ts(r.get("ts"))
        if ts and (g["last"] is None or ts > g["last"]):
            g["last"] = ts
    for t in sorted(terms, key=len):
        longer = [o for o in terms if o != t and o.startswith(t)]
        if longer:
            dst = terms[max(longer, key=lambda o: terms[o]["n"])]
            src = terms.pop(t)
            dst["n"] += src["n"]
            dst["sessions"] |= src["sessions"]
            if src["last"] and (dst["last"] is None or src["last"] > dst["last"]):
                dst["last"] = src["last"]
    return terms


def roll_demand_signals(days: int = 7, *, now: datetime | None = None, dry_run: bool = False) -> dict:
    """Roll the marketplace's free demand signals into sightings with source='system': zero-result
    searches and "tell {rep}" product requests (per cleaned term) and "tell me when back" restock asks (per SKU) over the last `days`.
    One sighting per term / SKU per ISO week (client_uuid = uuid5 of the key), so running it twice in
    a week writes nothing new. Callable from app/shop_jobs.py later; NOT scheduled yet."""
    now = now or _now()
    if not available():
        return {"ok": False, "reason": MISSING_HINT}
    since = now - timedelta(days=max(1, int(days)))
    week = _week_start(now)
    c = get_client()
    # R7d: a "tell {rep}" tap on an empty search is logged as product_request (meta.q = the words) once
    # r7d_product_request_migration.sql is applied; before it, the same tap is a search_zero row
    ev = (c.table("shop_events").select("ts,session_id,meta").in_("event", ["search_zero", "product_request"])
          .gte("ts", _iso(since)).limit(20000).execute().data or [])
    asks = (c.table("shop_restock_requests").select("item_code,phone,device_id,created_at")
            .gte("created_at", _iso(since)).limit(20000).execute().data or [])
    ctx = _catalog_ctx()
    items = (ctx or {}).get("items") or {}
    planned: list[dict] = []
    for term, g in sorted(_search_terms(ev).items()):
        planned.append({"signal": "search_zero", "key": f"system|search_zero|{term}|{week}", "title": term,
                        "yq": None, "n": g["n"], "who": len(g["sessions"]), "last": g["last"] or now,
                        "note": f"{g['n']} marketplace search{'es' if g['n'] != 1 else ''} found nothing "
                                f"({len(g['sessions'])} visitor{'s' if len(g['sessions']) != 1 else ''}), week of {week}."})
    by_code: dict[str, dict] = {}
    for a in asks:
        code = str(a.get("item_code") or "").strip()
        if not code:
            continue
        g = by_code.setdefault(code, {"n": 0, "who": set(), "last": None})
        g["n"] += 1
        g["who"].add(a.get("phone") or a.get("device_id") or f"#{g['n']}")
        ts = _parse_ts(a.get("created_at"))
        if ts and (g["last"] is None or ts > g["last"]):
            g["last"] = ts
    for code, g in sorted(by_code.items()):
        it = items.get(code)
        planned.append({"signal": "restock_ask", "key": f"system|restock_ask|{code}|{week}",
                        "title": (it or {}).get("display_name") or code, "yq": code,
                        "n": g["n"], "who": len(g["who"]), "last": g["last"] or now,
                        "note": f"{len(g['who'])} merchant{'s' if len(g['who']) != 1 else ''} asked to be told when "
                                f"{code} is back ({g['n']} ask{'s' if g['n'] != 1 else ''}), week of {week}."})
    written = {"search_zero": 0, "restock_ask": 0}
    skipped = 0
    for p in planned:
        cu = stable_uuid(p["key"])
        if _obs_by_uuid(cu):
            skipped += 1
            continue
        if dry_run:
            written[p["signal"]] += 1
            continue
        f = {"kind": "shop_asked", "title": p["title"], "note": p["note"], "brand": None, "competitor": None,
             "category": (items.get(p["yq"]) or {}).get("category") if p["yq"] else None, "barcode": None}
        item = find_item(f, p["yq"], now)
        row = {"client_uuid": cu, "source": "system", "signal": p["signal"], "kind": "shop_asked",
               "title": p["title"], "note": p["note"], "category": f["category"], "demand_qty": max(1, int(p["n"])),
               "yq_item_code": p["yq"], "result": "already_in_yq" if p["yq"] else ("seen_before" if item else "new_find"),
               "item_id": item.get("id") if item else None, "observed_at": _iso(p["last"]),
               "meta": {"count": p["n"], "distinct": p["who"], "week": week}}
        try:
            obs = (c.table("market_observations").insert(row).execute().data or [row])[0]
        except Exception as e:  # noqa: BLE001
            if _unique_violation(e):
                skipped += 1
                continue
            raise
        if not item:
            item = _new_item(f, p["yq"], "system", p["last"])
            c.table("market_observations").update({"item_id": item["id"]}).eq("id", obs["id"]).execute()
        refresh_item(int(item["id"]))
        written[p["signal"]] += 1
    return {"ok": True, "dry_run": dry_run, "week": week, "since": _iso(since), "written": written,
            "skipped": skipped, "planned": len(planned)}
