"""WEKOME "Coming soon" (trust plan §6b, release R1b): the announced range before it lands.

Rows live in shop_upcoming_items (scripts/shop_upcoming_migration.sql) — brand, model code, copy in
EN/AR, variant chips, photos, an expected month and a status — and NOTHING about money or
quantities: the table has no price, cost or qty column, and PUBLIC_FIELDS below is the whitelist
that reaches the marketplace. Merchants say "notify me" through the existing restock table
(shop_restock_requests + upcoming_id, optional non-binding qty_interest), so the rep sees interest
on his Today screen the way he sees sold-out requests.

Rules kept here (pure functions, tested without a database in tests/test_r1b_upcoming.py):
  * only `published` items are public;
  * a card retires the moment its catalog_item_code names a live catalog item (is_retired), so a
    product can never show as both "coming soon" and a real product;
  * the arrival label DERIVES from expected_month ("Arriving October") and drops to "Arriving
    soon" once that month has passed, so no stale promise stays up (expected_labels). The importer
    never writes a label; changing the month clears any hand-written label unless a new one is
    sent with it (update_item), so the page can never keep saying "October" in November.
  * "notify me" needs a phone (normalised the way /public/market/recognize does), otherwise the
    rep has nobody to message when the stock lands.

Routes are registered by app/shop_api.py (its "Coming soon" block). The public payload is cached
for 60 s (plain max-age, no stale-while-revalidate: the kill switch must bite within a minute),
and is a separate request so the home page's LCP never waits for it. Every read tolerates the
table not existing yet (the API may deploy before the migration): it logs and returns nothing.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime, timezone

from app.database import get_client

log = logging.getLogger(__name__)


class UpcomingError(ValueError):
    """An admin-facing validation problem (HTTP 400 at the edge)."""


BRAND_DEFAULT = "WEKOME"
STATUSES = ("draft", "published", "arrived", "withdrawn")
TABLE = "shop_upcoming_items"

# What a merchant's browser may ever receive about an upcoming item. Anything else on the row
# (shipment_ref, created_by, catalog_item_code, timestamps) stays inside.
PUBLIC_FIELDS = (
    "id", "brand", "model_code", "category", "name_en", "name_ar", "spec_en", "spec_ar",
    "variants", "photo_url", "photo_thumb_urls", "box_url", "box_thumb_urls",
    "expected_label_en", "expected_label_ar", "sort_order",
)
# A key that must never appear anywhere in the public payload (defence in depth: the table has no
# such column, and the importer refuses to build a row with one).
FORBIDDEN_KEY = re.compile(r"price|cost|qty|quantity|amount|value|margin|rmb|usd|bhd|pcs|carton", re.I)
VARIANT_PUBLIC = ("label", "label_ar")

# app_settings keys this module owns (read straight from the table, not via shop.SETTING_DEFAULTS).
# upcoming_enabled = "0" is the kill switch for the launch window: the public payload empties, a
# rep's card list and interest list empty, "notify me" stops taking requests — without touching
# any item's status. It bites within about a minute (60 s public max-age + the portal's 60 s
# staleTime); admins keep seeing every card in the desk so they can switch it back on.
SETTING_DEFAULTS = {"upcoming_enabled": "1"}

_TTL = 60.0

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]
MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر",
             "أكتوبر", "نوفمبر", "ديسمبر"]
SOON_EN, SOON_AR = "Arriving soon", "تصل قريبًا"


# ── pure helpers ──────────────────────────────────────────────────────────────

def parse_month(value) -> date | None:
    """'2026-10' / '2026-10-01' / a date → the first day of that month; None when blank."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().replace(day=1)
    if isinstance(value, date):
        return value.replace(day=1)
    s = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$", s)
    if not m:
        raise UpcomingError("expected_month must be YYYY-MM.")
    y, mo = int(m.group(1)), int(m.group(2))
    if not 1 <= mo <= 12:
        raise UpcomingError("expected_month must be YYYY-MM.")
    return date(y, mo, 1)


def month_label_en(month: date) -> str:
    return f"Arriving {MONTHS_EN[month.month - 1]}"


def month_label_ar(month: date) -> str:
    return f"تصل في {MONTHS_AR[month.month - 1]}"


def expected_labels(expected_month, label_en: str | None = None, label_ar: str | None = None,
                    today: date | None = None) -> tuple[str, str]:
    """(en, ar) arrival label. A month that has already passed reads "Arriving soon" whatever the
    stored text says — the owner sets the month, the page never keeps a stale promise. The free
    text labels win while the month is still ahead (or when no month is set)."""
    today = today or date.today()
    month = parse_month(expected_month) if expected_month not in (None, "") else None
    if month is not None and (month.year, month.month) < (today.year, today.month):
        return SOON_EN, SOON_AR
    en = (label_en or "").strip() or (month_label_en(month) if month else SOON_EN)
    ar = (label_ar or "").strip() or (month_label_ar(month) if month else SOON_AR)
    return en, ar


def is_retired(row: dict, active_codes: set[str]) -> bool:
    """Linked to a live catalog item → the card is gone (the URL then shows the real product)."""
    code = str(row.get("catalog_item_code") or "").strip().upper()
    return bool(code) and code in active_codes


def normalise_phone(raw) -> str | None:
    """The phone a "notify me" request must carry, normalised exactly as /public/market/recognize
    does (shop.recognize_phone): digits only, an 8-digit Bahrain number gets its 973 prefix, and
    anything shorter than 8 digits is no phone at all (None)."""
    digits = re.sub(r"\D", "", str(raw or ""))
    phone = ("973" + digits) if len(digits) == 8 else digits
    if len(phone) < 8:
        return None
    return phone[:32]


def _thumbs(raw) -> dict | None:
    """A {"160": url, "320": url, "512": url} set, or None (a jsonb column may come back as text)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) and raw else None


def _clean_variants(raw) -> list[dict]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    out = []
    for v in raw or []:
        if isinstance(v, str):
            out.append({"label": v})
        elif isinstance(v, dict):
            out.append({k: v[k] for k in VARIANT_PUBLIC if v.get(k)})
    return [v for v in out if v.get("label")]


def public_item(row: dict, today: date | None = None) -> dict:
    """The whitelisted public shape of one row. Computed fields only: the expected label follows
    the month rule; variants keep their labels and nothing else."""
    en, ar = expected_labels(row.get("expected_month"), row.get("expected_label_en"), row.get("expected_label_ar"), today)
    out = {}
    for k in PUBLIC_FIELDS:
        if FORBIDDEN_KEY.search(k):      # cannot happen with the list above; keeps a future edit honest
            continue
        out[k] = row.get(k)
    out["variants"] = _clean_variants(row.get("variants"))
    out["photo_thumb_urls"] = _thumbs(row.get("photo_thumb_urls"))
    out["box_thumb_urls"] = _thumbs(row.get("box_thumb_urls"))
    out["expected_label_en"], out["expected_label_ar"] = en, ar
    return out


def visible_items(rows: list[dict], active_codes: set[str], today: date | None = None) -> list[dict]:
    """Published, not retired, in shelf order."""
    live = [r for r in rows if r.get("status") == "published" and not is_retired(r, active_codes)]
    live.sort(key=lambda r: (r.get("sort_order") if r.get("sort_order") is not None else 10**6, int(r.get("id") or 0)))
    return [public_item(r, today) for r in live]


def public_payload(rows: list[dict], active_codes: set[str], enabled: bool = True, today: date | None = None) -> dict:
    """What GET /public/market/upcoming returns. The headline count and month are DATA — the number
    of live cards and the label most of them carry — never a literal in the page."""
    items = visible_items(rows, active_codes, today) if enabled else []
    brand = items[0]["brand"] if items else BRAND_DEFAULT
    label_en, label_ar = SOON_EN, SOON_AR
    if items:
        tally: dict[tuple[str, str], int] = {}
        for it in items:
            key = (it["expected_label_en"], it["expected_label_ar"])
            tally[key] = tally.get(key, 0) + 1
        label_en, label_ar = max(tally.items(), key=lambda kv: kv[1])[0]
    return {"enabled": bool(enabled), "brand": brand, "count": len(items),
            "expected_label_en": label_en, "expected_label_ar": label_ar, "items": items}


def assert_no_money(obj, path: str = "payload") -> None:
    """Raise if any key anywhere in `obj` looks like a price/cost/quantity field."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if FORBIDDEN_KEY.search(str(k)):
                raise AssertionError(f"money/quantity key {k!r} at {path}")
            assert_no_money(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_no_money(v, f"{path}[{i}]")


# ── settings ──────────────────────────────────────────────────────────────────

def settings() -> dict[str, str]:
    vals = dict(SETTING_DEFAULTS)
    try:
        rows = (get_client().table("app_settings").select("key,value")
                .in_("key", list(SETTING_DEFAULTS)).execute().data or [])
        for r in rows:
            if r.get("key") in vals and r.get("value") is not None:
                vals[r["key"]] = str(r["value"])
    except Exception as e:  # noqa: BLE001 — defaults keep the page working
        log.warning("upcoming settings read failed (defaults): %s", e)
    return vals


def enabled(vals: dict | None = None) -> bool:
    return str((vals or settings()).get("upcoming_enabled", "1")).strip() in ("1", "true", "True", "yes")


def set_enabled(on: bool, by: str = "") -> dict[str, str]:
    """The kill switch from the portal (no database session needed): upcoming_enabled = 1 | 0.
    Item statuses are untouched; the public cache is dropped so the next request answers."""
    get_client().table("app_settings").upsert(
        {"key": "upcoming_enabled", "value": "1" if on else "0", "updated_by": by, "updated_at": _iso()},
        on_conflict="key").execute()
    invalidate()
    return settings()


# ── rows ──────────────────────────────────────────────────────────────────────

def _rows() -> list[dict]:
    return (get_client().table(TABLE).select("*")
            .order("sort_order", nullsfirst=False).order("id").limit(500).execute().data or [])


def _rows_safe() -> list[dict]:
    """_rows() for the routes that must answer 200 before the migration lands (the rep's Today
    page loads them on every visit): a missing table is logged once per call and reads as no
    cards, never as a 500."""
    try:
        return _rows()
    except Exception as e:  # noqa: BLE001 — the table arrives with the migration
        log.warning("upcoming rows unavailable (migration not applied?): %s", e)
        return []


def _active_codes() -> set[str]:
    """Item codes the marketplace sells today (the cached catalog context)."""
    from app import shop
    try:
        return {str(c).upper() for c in shop.context()["items"]}
    except Exception as e:  # noqa: BLE001 — no catalog means nothing can be retired yet
        log.warning("upcoming: catalog context unavailable (%s); nothing retired", e)
        return set()


_cache: dict = {"at": 0.0, "payload": None}
_lock = threading.Lock()


def invalidate() -> None:
    _cache.update(at=0.0, payload=None)


def public_upcoming(force: bool = False) -> dict:
    """The public payload, cached 60 s. A failed refresh keeps the last copy on screen."""
    now = time.time()
    if not force and _cache["payload"] is not None and now - _cache["at"] < _TTL:
        return _cache["payload"]
    with _lock:
        if not force and _cache["payload"] is not None and time.time() - _cache["at"] < _TTL:
            return _cache["payload"]
        try:
            payload = public_payload(_rows(), _active_codes(), enabled=enabled())
        except Exception as e:  # noqa: BLE001 — the table arrives with the migration
            if _cache["payload"] is not None:
                log.warning("upcoming refresh failed, serving the last copy: %s", e)
                return _cache["payload"]
            log.warning("upcoming unavailable: %s", e)
            payload = public_payload([], set())
        _cache.update(at=time.time(), payload=payload)
        return payload


def public_json(force: bool = False) -> bytes:
    return json.dumps(public_upcoming(force), separators=(",", ":"), ensure_ascii=False, default=str).encode()


# ── admin ─────────────────────────────────────────────────────────────────────

EDITABLE = ("status", "expected_month", "expected_label_en", "expected_label_ar", "name_en", "name_ar",
            "spec_en", "spec_ar", "category", "catalog_item_code", "sort_order")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_admin(all_statuses: bool = True, vals: dict | None = None) -> list[dict]:
    """Every row (the office desk) — or, for a rep (all_statuses=False), the published ones only,
    and none at all while the kill switch is off, so nobody keeps sharing a link to a page that
    says nothing is announced. Each row carries the interest count, the number of distinct phones
    and the retired flag; the office desk also gets the phones themselves (interest_phones), so
    interest that came in without a rep's link can still be answered by someone."""
    if not all_statuses and not enabled(vals):
        return []
    rows = _rows_safe()
    if not rows:
        return []
    active = _active_codes()
    counts = interest_counts()
    out = []
    for r in rows:
        if not all_statuses and r.get("status") != "published":
            continue
        row = dict(r)
        row["variants"] = _clean_variants(r.get("variants"))
        row["expected_label_en"], row["expected_label_ar"] = expected_labels(
            r.get("expected_month"), r.get("expected_label_en"), r.get("expected_label_ar"))
        row["retired"] = is_retired(r, active)
        c = counts.get(int(r["id"]), {})
        row["interest_count"] = c.get("count", 0)
        row["interest_shops"] = c.get("shops", 0)
        if all_statuses:
            row["interest_phones"] = list(c.get("phones") or [])
            row["interest_ids"] = list(c.get("ids") or [])
        out.append(row)
    return out


def update_item(item_id: int, changes: dict, by: str = "") -> dict:
    patch: dict = {}
    for k, v in changes.items():
        if k not in EDITABLE:
            continue
        if k == "status":
            if v not in STATUSES:
                raise UpcomingError(f"status must be one of {', '.join(STATUSES)}.")
            patch[k] = v
        elif k == "expected_month":
            m = parse_month(v)
            patch[k] = m.isoformat() if m else None
        elif k == "sort_order":
            patch[k] = int(v) if v not in (None, "") else None
        elif k == "catalog_item_code":
            patch[k] = str(v or "").strip().upper()[:64] or None
        else:
            patch[k] = str(v or "").strip()[:400] or None
    if not patch:
        raise UpcomingError("Nothing to change.")
    if patch.get("name_en") is None and "name_en" in patch:
        raise UpcomingError("name_en cannot be blank.")
    if "expected_month" in patch:
        # the month moved: any label that did not come with it derives from the month again, so
        # "Arriving October" (or its Arabic) can never outlive a move to November
        for k in ("expected_label_en", "expected_label_ar"):
            patch.setdefault(k, None)
    patch["updated_at"] = _iso()
    res = get_client().table(TABLE).update(patch).eq("id", int(item_id)).execute().data or []
    if not res:
        raise UpcomingError("Upcoming item not found.")
    invalidate()
    row = res[0]
    row["retired"] = is_retired(row, _active_codes())
    row["by"] = by
    return row


# ── interest ("notify me when it lands") ──────────────────────────────────────

def _visible_row(upcoming_id: int) -> dict | None:
    for r in _rows_safe():
        if int(r.get("id") or 0) == int(upcoming_id):
            return r if r.get("status") == "published" and not is_retired(r, _active_codes()) else None
    return None


def add_interest(upcoming_id: int, phone: str | None, device_id: str | None, referral_code: str | None,
                 qty_interest: int | None, vals: dict | None = None) -> bool:
    """One row per device and item (the restock table's open unique index dedupes a repeat).
    Only a live card takes interest, only while the feature is on, and only with a phone the rep
    can message (UpcomingError otherwise — the sheet promises "your representative messages you").
    The quantity is optional and never a commitment."""
    norm = normalise_phone(phone)
    if not norm:
        raise UpcomingError("A phone number is needed so your representative can reach you.")
    if not enabled(vals):
        return False
    row = _visible_row(upcoming_id)
    if not row:
        return False
    qty = int(qty_interest) if qty_interest else None
    rec = {"item_code": f"{row['brand']}:{row['model_code']}"[:64], "upcoming_id": int(row["id"]),
           "phone": norm,
           "device_id": (device_id or "").strip()[:64] or None,
           "referral_code": (referral_code or "").strip().lower()[:32] or None,
           "qty_interest": qty if qty and qty > 0 else None}
    try:
        get_client().table("shop_restock_requests").insert(rec).execute()
    except Exception as e:  # noqa: BLE001 — the unique index makes a repeat a no-op
        if "duplicate" not in str(e).lower() and "unique" not in str(e).lower():
            log.warning("upcoming interest insert failed: %s", e)
            return False
    return True


def _interest_rows(referral_code: str | None = None) -> list[dict]:
    q = (get_client().table("shop_restock_requests").select("*")
         .not_.is_("upcoming_id", "null").is_("notified_at", "null"))
    if referral_code:
        q = q.eq("referral_code", referral_code.lower())
    try:
        return q.order("created_at", desc=True).limit(1000).execute().data or []
    except Exception as e:  # noqa: BLE001 — before the migration the column does not exist
        log.debug("upcoming interest unavailable: %s", e)
        return []


def interest_counts() -> dict[int, dict]:
    """Per card: open requests, distinct phones (newest first, no repeats) and the row ids —
    what the office desk shows and can mark as told."""
    out: dict[int, dict] = {}
    for r in _interest_rows():
        g = out.setdefault(int(r["upcoming_id"]), {"count": 0, "shops": 0, "phones": [], "ids": []})
        g["count"] += 1
        g["ids"].append(r["id"])
        if r.get("phone") and r["phone"] not in g["phones"]:
            g["phones"].append(r["phone"])
    for g in out.values():
        g["shops"] = len(g["phones"])
    return out


def list_interest(referral_code: str | None = None, vals: dict | None = None) -> list[dict]:
    """"Shops interested from your link": open interest grouped per upcoming item, newest first,
    every phone listed so the rep can message each shop. Scoped to the rep's referral code; the
    admin (None) sees everything. Empty while the kill switch is off or before the migration."""
    if not enabled(vals):
        return []
    rows = _interest_rows(referral_code)
    if not rows:
        return []
    items = {int(r["id"]): r for r in _rows_safe()}
    by: dict[int, dict] = {}
    for r in rows:
        uid = int(r["upcoming_id"])
        it = items.get(uid) or {}
        g = by.setdefault(uid, {"upcoming_id": uid, "brand": it.get("brand"), "model_code": it.get("model_code"),
                                "name_en": it.get("name_en"), "status": it.get("status"),
                                "catalog_item_code": it.get("catalog_item_code"),
                                "count": 0, "qty_interest": 0, "phones": [], "first_at": r["created_at"], "ids": []})
        g["count"] += 1
        g["ids"].append(r["id"])
        g["qty_interest"] += int(r.get("qty_interest") or 0)
        if r.get("phone") and r["phone"] not in g["phones"]:
            g["phones"].append(r["phone"])
        g["first_at"] = min(g["first_at"], r["created_at"])
    out = list(by.values())
    out.sort(key=lambda g: (-g["count"], g["first_at"]))
    return out
