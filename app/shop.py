"""YQ Shop — the shareable catalog as an ordering system (see docs/SHOP.md).

Everything here derives from the same Focus uploads that drive the portal: prices from the
price book (v_catalog), stock from the latest stock_balance snapshot (v_catalog_stock), landed
cost from MRN uploads (v_catalog_cost), velocity from the sales day book (v_catalog_velocity).
There is no sync job — the payload is rebuilt from the views (60 s cache, invalidated on every
data refresh / admin edit).

Hard rules:
  * quantities and costs NEVER leave the server — the public payload carries a stock STATUS only;
  * ONE pricing engine (price_cart) serves both /quote and /order — client totals are ignored;
  * a margin floor (landed cost x (1 + shop_min_margin_pct)) clamps every discount;
  * the shop never mutates stock — Focus stays the system of record, the salesman confirms.
Business numbers (thresholds, prefixes, badges) live in app_settings ('shop_*' keys), never here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.catalog import CATEGORY_ORDER, public_url, share_token, thumb_path, THUMB_SIZES
from app.config import settings as cfg
from app.database import get_client
from app.db_read import exec_sql, exec_sql_params

log = logging.getLogger(__name__)

STOCK_IN, STOCK_LOW, STOCK_OUT = "in_stock", "low_stock", "out_of_stock"
# DB values stay short and stable; STATUS_LABELS is what the merchant reads. Owner, 16-Sep-2026:
# the storekeeper issues stock to the salesman (Preparing), the salesman delivers (On the way).
STATUSES = ("new", "confirmed", "packed", "out_for_delivery", "delivered", "cancelled")
NEXT_STATUS: dict[str, tuple[str, ...]] = {
    "new": ("confirmed", "cancelled"),
    "confirmed": ("packed", "out_for_delivery", "delivered", "cancelled"),
    "packed": ("out_for_delivery", "delivered", "cancelled"),
    "out_for_delivery": ("delivered", "cancelled"),
    "delivered": (),
    "cancelled": (),
}
STATUS_LABELS = {"new": "Received", "confirmed": "Confirmed", "packed": "Preparing",
                 "out_for_delivery": "On the way", "delivered": "Delivered", "cancelled": "Cancelled"}
TRACK_STEPS = ("new", "confirmed", "packed", "out_for_delivery", "delivered")
# Statuses a non-admin staff role may set. Salesmen: every transition on their own orders; the
# storekeeper only moves goods (Preparing / On the way); admins: everything.
ROLE_STATUSES: dict[str, tuple[str, ...]] = {"storekeeper": ("packed", "out_for_delivery")}
SOURCES = ("referral", "dropdown", "default", "salesman", "market")
# How the salesman on an order was decided — the precedence lives in resolve_salesman().
ATTRIBUTION = ("customer_admin", "focus_map", "sticky", "session_ref", "checkout_pick", "staff", "default",
               "unassigned")
LINE_STATUSES = ("ok", "changed", "removed", "backorder")
EVENTS = ("view", "item", "add", "checkout", "order", "search", "search_zero", "remove", "qty", "cart",
          "checkout_start", "rail_click", "reco_click", "share", "install", "reorder", "cancel", "vitals",
          "push_subscribe", "error")
# event meta keys we keep (short strings / numbers only — never free text that could carry PII)
_META_KEYS = frozenset({"q", "rail", "pos", "results", "count", "value", "lcp", "inp", "cls", "reason",
                        "from", "to", "code", "attribution", "where"})
MAX_LINES = 60
MAX_QTY = 9999

# Mirrors the seeds in scripts/shop_migration.sql — defaults only; the DB value wins.
SETTING_DEFAULTS: dict[str, str] = {
    "shop_stale_days": "3",          # Telegram the owner when the stock snapshot is older than this
    "shop_min_margin_pct": "0.20",
    "shop_vat_rate": "0.10",
    "shop_low_stock_units": "10",
    "shop_low_stock_days_cover": "30",
    "shop_allow_backorder": "1",
    "shop_min_order_bhd": "0",
    "shop_free_delivery_threshold_bhd": "0",
    "shop_delivery_fee_bhd": "0",
    "shop_default_salesman": "",
    "shop_order_prefix": "YQ",
    "shop_social_proof_min_customers": "5",
    "shop_show_retail_compare": "1",
    "shop_best_seller_top_n": "3",
    "shop_trending_growth_pct": "30",
    "shop_trending_min_units": "10",
    "shop_new_days": "30",
    # marketplace v2 (16-Sep-2026): honest merchandising signals
    "shop_price_drop_days": "30",        # a real trade-price cut inside this window shows "Was → Now"
    "shop_clearance_days_cover": "365",  # stock on hand covers this many days of sales → aging
    "shop_clearance_min_units": "12",    # …and there are at least this many units to clear
    "shop_clearance_max": "24",          # the worst N by cover wear the Clearance badge
    "shop_clearance_show_retail": "1",   # clearance cards show the real retail price as the anchor even if compare is off
    # marketplace (16-Sep-2026)
    "shop_sticky_days": "90",
    "shop_public_tiers": "1",
    "shop_phone_daily_cap": "10",
    "shop_device_daily_cap": "20",
    "shop_assign_sla_min": "30",
    "shop_market_enabled": "1",
    # the merchant-facing origin (the marketplace's hostname); empty = fall back to APP_BASE_URL
    "shop_market_url": "",
    "shop_areas": ("Manama,Muharraq,Riffa,Isa Town,Hamad Town,Sitra,Budaiya,Saar,Hidd,Jidhafs,Sanabis,Aali,"
                   "Zallaq,Salmabad,Tubli,Seef,Juffair,Adliya,Gudaibiya,Hoora,Galali,Arad,Busaiteen,Askar"),
    # marketplace v2.1 (16-Sep-2026): the promise bar — only claims the data can back. JSON list of
    # {key, en, ar, icon, to}. The delivery line switches itself when a free-delivery threshold exists.
    # v3 (17-Sep-2026): wholesale-first — "No minimum order" gave way to trade prices and the rep's
    # confirmation (the wholesale minimum is real now). Validated on save by validate_promises().
    # 20-Sep-2026: the stock line no longer says "Live warehouse stock". The catalog carries a DATED
    # snapshot (`stock_as_of`, printed in the market footer — 14-Sep while today is 20-Sep), so "live"
    # contradicted the same page. The claim we can back is that the counts are the warehouse's real ones.
    # A cadence claim ("Stock updated daily") is NOT the fix: the snapshot is six days old, so it would
    # contradict the same footer. Only promise freshness once the upload is provably daily. Mirrors:
    # web/src/pages/Settings.tsx DEFAULT_PROMISES and S.about.intro in web/src/market/strings.ts.
    "shop_market_promises": json.dumps([
        {"key": "delivery", "en": "Free delivery across Bahrain", "ar": "توصيل مجاني في كل البحرين", "icon": "truck", "to": "/about#delivery"},
        {"key": "trade", "en": "Trade prices for shops", "ar": "أسعار الجملة للمحلات", "icon": "tag", "to": "/about#trade"},
        {"key": "stock", "en": "Real warehouse stock", "ar": "مخزون حقيقي من المستودع", "icon": "pulse", "to": "/shop?f=instock"},
        {"key": "rep", "en": "Every order confirmed by your rep", "ar": "كل طلب يؤكده مندوبك", "icon": "shield", "to": "/about#how"},
    ], ensure_ascii=False),
    "shop_market_ai_enabled": "0",       # phase C: the concierge; off until the office switches it on
    # wholesale order engine (16-Sep-2026): the minimum is a sales engine, not a wall
    "shop_small_order_mode": "request",  # request = accept + flag for the rep · allow = accept silently · block = refuse
    "shop_small_order_fee_bhd": "0",     # optional handling fee the rep may apply when confirming a small order
    "shop_gap_suggestions": "6",         # how many add-ons the cart suggests to close the gap to the minimum
}

_TTL = 60.0


class ShopError(ValueError):
    """A customer-facing validation problem (HTTP 400 at the edge)."""


# ── money helpers ─────────────────────────────────────────────────────────────

def money(x) -> float:
    """BHD is a 3-decimal currency — round half-up at every boundary."""
    if x is None:
        return 0.0
    return float(Decimal(str(x)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _i(x, default: int = 0) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


def _parse_ts(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ── settings ──────────────────────────────────────────────────────────────────

_settings_cache: dict = {"at": 0.0, "vals": None}


def shop_settings(force: bool = False) -> dict[str, str]:
    """All shop_* settings as strings (DB value over default). Cached 60 s."""
    now = time.time()
    if not force and _settings_cache["vals"] is not None and now - _settings_cache["at"] < _TTL:
        return _settings_cache["vals"]
    vals = dict(SETTING_DEFAULTS)
    try:
        rows = (get_client().table("app_settings").select("key,value")
                .like("key", "shop_%").execute().data or [])
        for r in rows:
            if r.get("key") in vals and r.get("value") is not None:
                vals[r["key"]] = str(r["value"])
    except Exception as e:  # noqa: BLE001 — defaults keep the shop working
        log.warning("shop settings read failed (defaults): %s", e)
    _settings_cache.update(at=now, vals=vals)
    return vals


PROMISES_MAX = 6          # stored; the bar itself shows the first 4 (promises_payload)
_PROMISE_OPTIONAL = ("ar", "icon", "to")


def validate_promises(raw) -> str:
    """The promise bar as it may be stored: a JSON list (≤ PROMISES_MAX) of objects, each with a
    non-empty text `key` and `en`, optional text `ar` / `icon` / `to`. Returns the normalised JSON
    (trimmed, known fields only, Arabic kept as-is). Raises ShopError with a message the admin can
    act on — the market used to blank the whole bar silently when the stored value was bad."""
    rows = raw
    if raw is None or isinstance(raw, (str, bytes)):
        try:
            rows = json.loads(raw or "")
        except ValueError as e:
            raise ShopError("Promises must be valid JSON: a list of {key, en, ar, icon, to} objects.") from e
    if not isinstance(rows, list):
        raise ShopError("Promises must be a JSON list of {key, en, ar, icon, to} objects.")
    if len(rows) > PROMISES_MAX:
        raise ShopError(f"At most {PROMISES_MAX} promises (the bar shows the first 4).")
    out: list[dict] = []
    seen: set[str] = set()
    for n, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            raise ShopError(f"Promise {n} must be an object with a key and English text (en).")
        key, en = r.get("key"), r.get("en")
        if not isinstance(key, str) or not key.strip():
            raise ShopError(f"Promise {n} needs a key (text, e.g. delivery).")
        key = key.strip()
        if not isinstance(en, str) or not en.strip():
            raise ShopError(f"Promise {n} ({key}) needs English text (en).")
        if key.lower() in seen:
            raise ShopError(f"Promise keys must be unique — '{key}' appears twice.")
        seen.add(key.lower())
        row = {"key": key, "en": en.strip()}
        for f in _PROMISE_OPTIONAL:
            v = r.get(f)
            if v is None:
                continue
            if not isinstance(v, str):
                raise ShopError(f"Promise {n} ({key}): {f} must be text.")
            if v.strip():
                row[f] = v.strip()
        to = row.get("to")
        if to and not (to.startswith("/") or to.startswith("https://")):
            raise ShopError(f"Promise {n} ({key}): the link must be a marketplace path (/about#delivery) or an https URL.")
        out.append(row)
    return json.dumps(out, ensure_ascii=False)


def small_order_mode(vals: dict) -> str:
    """shop_small_order_mode normalised: request (accept + flag for the rep) · allow · block."""
    mode = str(vals.get("shop_small_order_mode") or "request").strip().lower()
    return mode if mode in ("request", "allow", "block") else "request"


def update_shop_settings(changes: dict[str, str], by: str = "") -> dict[str, str]:
    """Upsert known shop_* keys (unknown keys are ignored). Everything is validated before the
    first write, so a bad value never leaves the settings half-saved. Raises ShopError."""
    changes = dict(changes or {})
    if "shop_market_promises" in changes:
        changes["shop_market_promises"] = validate_promises(changes["shop_market_promises"])
    client = get_client()
    for k, v in changes.items():
        if k not in SETTING_DEFAULTS:
            continue
        client.table("app_settings").upsert(
            {"key": k, "value": str(v if v is not None else ""), "updated_by": by,
             "updated_at": _iso()}, on_conflict="key").execute()
    invalidate()
    return shop_settings(force=True)


def _flag(vals: dict, key: str) -> bool:
    return str(vals.get(key, "")).strip() in ("1", "true", "True", "yes")


# ── stock status (units → status; cover → "selling fast" badge) ───────────────

def stock_status_for(qty, low_units: int) -> str:
    q = _f(qty, 0.0)
    if q <= 0:
        return STOCK_OUT
    if q <= low_units:
        return STOCK_LOW
    return STOCK_IN


def selling_fast(qty, sold_90d, low_days: int) -> bool:
    q, s = _f(qty, 0.0), _f(sold_90d, 0.0)
    if q <= 0 or s <= 0 or low_days <= 0:
        return False
    return (q / (s / 90.0)) < low_days


# ── context: items, rules, salesmen (one cached load feeds payload + pricing) ──

# gen counts invalidations, so a background refresh that started before one never
# overwrites the fresh copy with data it read before the change.
_ctx_cache: dict = {"at": 0.0, "ctx": None, "gen": 0}


def invalidate() -> None:
    """Forget the cached catalog context (called after refresh, photo upload, rule/salesman edits)."""
    _ctx_cache.update(at=0.0, ctx=None, gen=_ctx_cache["gen"] + 1)
    _settings_cache.update(at=0.0, vals=None)
    _salesman_cache.clear()


def _load_items() -> list[dict]:
    """Every sellable SKU, in the order a customer browses them.

    `ci.hidden` is the owner's "not a product" switch (display stands, samples):
    the row stays in the item master and in the price-book mirror, it just never
    reaches a catalog. The shelf order is CATEGORY_ORDER, not the alphabet — the
    volume lines lead, accessories trail."""
    rows = exec_sql(
        "SELECT c.item_code, c.display_name, c.spec, c.category, c.brand, c.standard_rate, c.b2c_rate, "
        "c.product_image_url, c.package_image_url, c.sort_order, c.created_at::text AS created_at, "
        "ci.moq, ci.pack_size, s.stock_qty, s.as_of_date::text AS stock_as_of, "
        "v.sold_30d, v.prev_30d, v.sold_90d, v.customers_30d "
        "FROM v_catalog c "
        "JOIN catalog_items ci ON ci.item_code = c.item_code "
        "LEFT JOIN v_catalog_stock s ON s.item_code = c.item_code "
        "LEFT JOIN v_catalog_velocity v ON v.item_code = c.item_code "
        "WHERE c.is_active AND NOT COALESCE(ci.hidden, false) "
        "ORDER BY c.category, c.sort_order NULLS LAST, c.item_code"
    ) or []
    # Sold-out SKUs go to the very end of the catalog (owner, 15-Sep): every screen a
    # merchant scrolls should open on something he can have today. Within each half
    # the shelf order holds.
    rows.sort(key=lambda r: (_f(r.get("stock_qty"), 0.0) <= 0,
                             CATEGORY_ORDER.index(r["category"]) if r.get("category") in CATEGORY_ORDER else 99,
                             str(r.get("category") or ""),
                             _i(r.get("sort_order"), 10 ** 6), str(r["item_code"])))
    return rows


def _load_costs() -> dict[str, float]:
    """Latest landed cost per code via the service client (the view is never granted to agents)."""
    out: dict[str, float] = {}
    try:
        rows = (get_client().table("mrn_landed_costs")
                .select("sku_code,landed_cost_bhd,effective_date,id")
                .order("sku_code").order("effective_date", desc=True).order("id", desc=True)
                .limit(5000).execute().data or [])
        for r in rows:
            code = r.get("sku_code")
            if code and code not in out and r.get("landed_cost_bhd") is not None:
                out[code] = _f(r["landed_cost_bhd"])
    except Exception as e:  # noqa: BLE001
        log.warning("landed costs unavailable (no margin floor): %s", e)
    return out


def _load_rules() -> list[dict]:
    try:
        rows = (get_client().table("discount_rules").select("*").eq("is_active", True)
                .order("priority").order("id").execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("discount_rules unavailable: %s", e)
        return []
    now = _now()
    out = []
    for r in rows:
        st, en = _parse_ts(r.get("starts_at")), _parse_ts(r.get("ends_at"))
        if st and st > now:
            continue
        if en and en < now:
            continue
        if r.get("max_uses") is not None and _i(r.get("uses")) >= _i(r.get("max_uses")):
            continue
        scope = r.get("scope") or {}
        if isinstance(scope, str):
            try:
                scope = json.loads(scope)
            except ValueError:
                scope = {}
        r["scope"] = {
            "item_codes": [str(c).strip().upper() for c in (scope.get("item_codes") or [])],
            "categories": [str(c).strip().upper() for c in (scope.get("categories") or [])],
            "referral_codes": [str(c).strip().lower() for c in (scope.get("referral_codes") or [])],
        }
        out.append(r)
    return out


def _load_price_drops(days: int = 30) -> dict[str, dict]:
    """Real trade-price cuts (MA_base) inside the window, keyed by upper-cased code:
    {"was": prev, "now": current, "on": date}. The marketplace shows "Was → Now" only when
    `now` still equals the live price, so a later change can never leave a stale anchor."""
    try:
        rows = exec_sql(
            "SELECT sku_code, changed_on::text AS changed_on, current_price_bhd, prev_price_bhd FROM v_price_change "
            f"WHERE current_price_bhd < prev_price_bhd AND changed_on >= CURRENT_DATE - INTERVAL '{int(days)} days'"
        ) or []
    except Exception as e:  # noqa: BLE001
        log.warning("price drops unavailable: %s", e)
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        code = str(r.get("sku_code") or "").strip().upper()
        if code:
            out[code] = {"was": _f(r.get("prev_price_bhd")), "now": _f(r.get("current_price_bhd")), "on": r.get("changed_on")}
    return out


def _load_pairs(active_codes: set[str], top_n: int = 3) -> dict[str, list[str]]:
    """Top co-purchase partners per code (both directions), active items only."""
    try:
        rows = exec_sql("SELECT item_a, item_b, n_invoices FROM v_catalog_pairs "
                        "ORDER BY n_invoices DESC LIMIT 3000") or []
    except Exception as e:  # noqa: BLE001
        log.debug("pairs unavailable: %s", e)
        return {}
    partners: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        a, b, n = str(r.get("item_a") or ""), str(r.get("item_b") or ""), _i(r.get("n_invoices"))
        if a in active_codes and b in active_codes:
            partners.setdefault(a, []).append((b, n))
            partners.setdefault(b, []).append((a, n))
    return {c: [code for code, _n in sorted(v, key=lambda t: -t[1])[:top_n]] for c, v in partners.items()}


CAMPAIGN_PLACEMENTS = ("hero", "strip", "aside", "category")
CAMPAIGN_AUDIENCES = ("all", "recognized", "new")
# v3 creative (scripts/marketplace_campaign_creative_migration.sql): an uploaded photo is framed with
# `image_fit` (never cropped unless the admin says cover); a composed creative lays up to
# CAMPAIGN_MAX_PRODUCTS product photos on a `canvas`. The first value of each tuple is the DB default.
CAMPAIGN_IMAGE_FITS = ("contain", "cover")
CAMPAIGN_CANVASES = ("lilac", "apricot", "mint", "plum", "night")
CAMPAIGN_MAX_PRODUCTS = 3


def _load_campaigns() -> list[dict]:
    """Active campaign rows (the window is applied per request so a 60 s cache never shows a
    campaign a minute late or early)."""
    try:
        return (get_client().table("shop_campaigns").select("*").eq("is_active", True)
                .order("sort_order").order("id").execute().data or [])
    except Exception as e:  # noqa: BLE001 — the market works without the table
        log.warning("shop_campaigns unavailable: %s", e)
        return []


def _campaign_live_codes(ctx: dict, codes) -> list[str] | None:
    """A composed creative's products, in the catalog's own spelling, keeping only codes the public
    catalog still carries (a hidden, deactivated or renamed SKU simply drops out). None when nothing
    is left, so the market can test the field for truthiness."""
    items = ctx.get("items") or {}
    if not items or not isinstance(codes, (list, tuple)):
        return None
    idx = ctx.get("by_upper") or {str(k).upper(): k for k in items}
    out: list[str] = []
    for raw in codes:
        code = idx.get(str(raw or "").strip().upper())
        if code and code not in out:
            out.append(code)
    return out[:CAMPAIGN_MAX_PRODUCTS] or None


def campaigns_payload(ctx: dict, now: datetime | None = None) -> list[dict]:
    """Campaigns live right now, in the shape the market renders. A campaign tied to a discount
    rule inherits the rule's real end time, so a countdown is never invented."""
    now = now or _now()
    rules = {r.get("id"): r for r in ctx.get("rules", [])}
    out = []
    for c in ctx.get("campaigns", []) or []:
        st, en = _parse_ts(c.get("starts_at")), _parse_ts(c.get("ends_at"))
        if st and st > now:
            continue
        if en and en < now:
            continue
        rule = rules.get(c.get("rule_id")) if c.get("rule_id") else None
        if c.get("rule_id") and rule is None:
            continue    # the offer behind it ended or was switched off — the banner goes with it
        ends = c.get("ends_at") or (rule.get("ends_at") if rule else None)
        placement = [x for x in (c.get("placement") or []) if x in CAMPAIGN_PLACEMENTS] or ["strip"]
        out.append({
            "id": c["id"], "title": c.get("title") or "", "title_ar": c.get("title_ar") or None,
            "line": c.get("line") or None, "line_ar": c.get("line_ar") or None,
            "image_url": c.get("image_url") or None,
            "image_url_600": c.get("image_url_600") or None,
            "image_fit": c.get("image_fit") if c.get("image_fit") in CAMPAIGN_IMAGE_FITS else CAMPAIGN_IMAGE_FITS[0],
            "product_codes": _campaign_live_codes(ctx, c.get("product_codes")),
            "canvas": c.get("canvas") if c.get("canvas") in CAMPAIGN_CANVASES else CAMPAIGN_CANVASES[0],
            "cta_label": c.get("cta_label") or None, "cta_label_ar": c.get("cta_label_ar") or None,
            "cta_to": c.get("cta_to") or "/shop",
            "placement": placement, "category": c.get("category") or None,
            "audience": c.get("audience") if c.get("audience") in CAMPAIGN_AUDIENCES else "all",
            "rule_id": c.get("rule_id"), "sponsored": bool(c.get("sponsored")),
            "sponsor_name": c.get("sponsor_name") if c.get("sponsored") else None,
            "ends_at": ends,
        })
    return out


def promises_payload(vals: dict) -> list[dict]:
    """The promise bar. Only true claims: when a free-delivery threshold exists the delivery line
    says so instead of promising free delivery on everything."""
    try:
        rows = json.loads(vals.get("shop_market_promises") or "[]")
    except (TypeError, ValueError):
        rows = []
    thr = money(vals.get("shop_free_delivery_threshold_bhd"))
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("en"):
            continue
        r = dict(r)
        if r.get("key") == "delivery" and thr > 0:
            r["en"] = f"Free delivery over BHD {thr:.3f}"
            r["ar"] = f"توصيل مجاني للطلبات فوق {thr:.3f} د.ب"
        out.append({"key": r.get("key") or "", "en": r["en"], "ar": r.get("ar") or None,
                    "icon": r.get("icon") or None, "to": r.get("to") or None})
    return out[:4]


def _load_salesmen(active_only: bool = True) -> list[dict]:
    try:
        q = get_client().table("salesmen").select("*")
        if active_only:
            q = q.eq("is_active", True)
        return q.order("sort_order", nullsfirst=False).order("name").execute().data or []
    except Exception as e:  # noqa: BLE001
        log.warning("salesmen unavailable: %s", e)
        return []


# First URL segments a referral code may never take: the marketplace serves /{slug} as a
# salesman storefront, so a slug that shadows an app route would break that route for everyone.
_RESERVED_FALLBACK = frozenset({
    "c", "o", "p", "t", "s", "f", "search", "cart", "checkout", "orders", "order", "join", "api", "public",
    "shop", "admin", "assets", "static", "me", "health", "share", "optout", "login", "invite", "catalog",
    "market", "marketplace", "track", "app", "sw.js", "version.json", "manifest.webmanifest", "robots.txt",
    "quick", "fonts", "about", "help", "ask", "saved",
    # marketplace brand pages (24-Sep-2026, R1b): /brands/{brand}, /wekome and /coming-soon redirect there
    "brands", "wekome", "coming-soon",
})


def _load_reserved_slugs() -> frozenset[str]:
    try:
        rows = get_client().table("shop_reserved_slugs").select("slug").limit(500).execute().data or []
        return frozenset({str(r["slug"]).lower() for r in rows if r.get("slug")} | set(_RESERVED_FALLBACK))
    except Exception as e:  # noqa: BLE001 — the table arrives with marketplace_migration.sql
        log.debug("reserved slugs unavailable, using the built-in list: %s", e)
        return _RESERVED_FALLBACK


def is_reserved_slug(slug: str | None, ctx: dict | None = None) -> bool:
    s = str(slug or "").strip().lower()
    if not s:
        return True
    reserved = (ctx or {}).get("reserved_slugs") if ctx else None
    return s in (reserved or _RESERVED_FALLBACK)


_ctx_lock = threading.Lock()


def _build_context() -> dict:
    """One full load: ~7 round trips to Supabase (~1.5 s from Render). Everything a
    public request needs is in here, so a warm request touches the database zero times."""
    vals = shop_settings(force=True)
    items = _load_items()
    ctx = {
        "settings": vals,
        "items": {str(r["item_code"]): r for r in items},
        "order": [str(r["item_code"]) for r in items],
        "costs": _load_costs(),
        "rules": _load_rules(),
        "salesmen": _load_salesmen(),
        "campaigns": _load_campaigns(),
        "loaded_at": _iso(),
    }
    # Item codes are stored exactly as the price book spells them ("X05 UL-1Mtr"),
    # so every lookup that starts from user input goes through this index.
    ctx["by_upper"] = {code.upper(): code for code in ctx["order"]}
    ctx["pairs"] = _load_pairs(set(ctx["order"]))
    ctx["drops"] = _load_price_drops(_i(vals.get("shop_price_drop_days"), 30))
    ctx["reserved_slugs"] = _load_reserved_slugs()
    # Both used to be a database call on EVERY catalog request (~350 ms together).
    ctx["share_token"] = share_token(create=False)
    ctx["prices_updated"] = (exec_sql("SELECT MAX(start_date)::text AS d FROM selling_prices "
                                      "WHERE price_book = 'MA_base' AND start_date <= CURRENT_DATE")
                             or [{}])[0].get("d")
    ctx["public_json"] = {}     # serialized public payloads, per referral code — see public_catalog_json
    return ctx


def _refresh_in_background(gen: int) -> None:
    if not _ctx_lock.acquire(blocking=False):
        return                  # a refresh is already running
    def run() -> None:
        try:
            ctx = _build_context()
            if _ctx_cache["gen"] == gen:     # an invalidate() meanwhile means this copy is already stale
                _ctx_cache.update(at=time.time(), ctx=ctx)
        except Exception as e:  # noqa: BLE001 — keep serving the previous copy
            log.warning("background catalog refresh failed, serving the previous copy: %s", e)
        finally:
            _ctx_lock.release()
    threading.Thread(target=run, name="shop-context-refresh", daemon=True).start()


def context(force: bool = False) -> dict:
    """The cached catalog context, stale-while-revalidate.

    Past its 60 s TTL the current copy is still returned at once while ONE background
    thread rebuilds it — no visitor ever waits for the rebuild. Only a cold process or an
    explicit invalidate() (price/stock upload, photo, rule or salesman edit) builds
    synchronously, so an owner's change is still visible on the very next request."""
    cached = _ctx_cache["ctx"]
    if not force and cached is not None:
        if time.time() - _ctx_cache["at"] >= _TTL:
            _refresh_in_background(_ctx_cache["gen"])
        return cached
    with _ctx_lock:             # one cold build at a time; everyone queued behind it reuses it
        if not force and _ctx_cache["ctx"] is not None:
            return _ctx_cache["ctx"]
        gen = _ctx_cache["gen"]
        ctx = _build_context()
        if _ctx_cache["gen"] == gen:
            _ctx_cache.update(at=time.time(), ctx=ctx)
        return ctx


def public_catalog_json(token: str | None, referral_code: str | None = None) -> bytes | None:
    """The public catalog as ready-to-send JSON bytes, built once per context refresh and
    per salesman link. Every visitor between two refreshes gets the same answer, so none of
    them should pay for building and serializing 180 items. None = bad token."""
    ctx = context()
    good = ctx.get("share_token")
    if not (token and good and secrets.compare_digest(token, good)):
        return None if catalog_payload(token, referral_code) is None else _serialize(token, referral_code)
    # unknown ?ref= values all share the no-ref copy, so junk links cannot grow the cache
    key = (resolve_ref(ctx, referral_code) or {}).get("referral_code") or ""
    cached = ctx["public_json"].get(key)
    if cached is None:
        cached = ctx["public_json"][key] = _serialize(token, key or None)
    return cached


def _serialize(token: str | None, referral_code: str | None) -> bytes | None:
    payload = catalog_payload(token, referral_code)
    return None if payload is None else json.dumps(payload, separators=(",", ":"), default=str).encode()


def resolve_code(ctx: dict, raw) -> str | None:
    """The catalog's own spelling of an item code, whatever case it arrives in."""
    idx = ctx.get("by_upper")
    if idx is None:     # a hand-built context (tests, scripts) — index it once
        idx = ctx["by_upper"] = {str(code).upper(): code for code in ctx["items"]}
    return idx.get(str(raw or "").strip().upper())


# ── rule matching ─────────────────────────────────────────────────────────────

def _rule_matches_item(rule: dict, item: dict) -> bool:
    sc = rule["scope"]
    if not sc["item_codes"] and not sc["categories"]:
        return True
    code = str(item.get("item_code") or "").upper()
    cat = str(item.get("category") or "OTHER").upper()
    return code in sc["item_codes"] or cat in sc["categories"]


def _rule_matches_ref(rule: dict, referral_code: str | None) -> bool:
    refs = rule["scope"]["referral_codes"]
    if not refs:
        return True
    return bool(referral_code) and referral_code.lower() in refs


def _discounted_unit(rule: dict, list_price: float) -> float:
    if rule.get("fixed_price_bhd") is not None:
        return min(list_price, _f(rule["fixed_price_bhd"]))
    if rule.get("pct_off") is not None:
        return list_price * (1 - _f(rule["pct_off"]) / 100.0)
    if rule.get("amount_off_bhd") is not None:
        return list_price - _f(rule["amount_off_bhd"])
    return list_price


def item_tiers(ctx: dict, item: dict) -> list[dict]:
    """Quantity tiers that apply to an item (public — no salesman-scoped rules)."""
    lp = _f(item.get("standard_rate"))
    tiers = []
    for r in ctx["rules"]:
        if r["kind"] != "qty_tier" or not r.get("min_qty") or not _rule_matches_item(r, item):
            continue
        if r["scope"]["referral_codes"]:
            continue
        unit = money(max(0.0, _discounted_unit(r, lp)))
        if lp and unit < lp:
            tiers.append({"min_qty": _i(r["min_qty"]), "unit_price_bhd": unit,
                          "label": f"{_i(r['min_qty'])}+ → BHD {unit:.3f}", "rule_id": r["id"]})
    tiers.sort(key=lambda t: t["min_qty"])
    # keep only tiers that actually improve on the previous one
    out: list[dict] = []
    for t in tiers:
        if not out or t["unit_price_bhd"] < out[-1]["unit_price_bhd"]:
            out.append(t)
    return out


# ── public payload ────────────────────────────────────────────────────────────

def _badges(ctx: dict) -> dict[str, list[str]]:
    vals = ctx["settings"]
    top_n = max(_i(vals.get("shop_best_seller_top_n"), 3), 0)
    growth = _f(vals.get("shop_trending_growth_pct"), 30.0)
    min_units = _f(vals.get("shop_trending_min_units"), 10.0)
    new_days = _i(vals.get("shop_new_days"), 30)
    low_days = _i(vals.get("shop_low_stock_days_cover"), 30)
    drops = ctx.get("drops") or {}
    cl_days = _i(vals.get("shop_clearance_days_cover"), 365)
    cl_min = _i(vals.get("shop_clearance_min_units"), 12)
    cl_max = _i(vals.get("shop_clearance_max"), 24)
    out: dict[str, list[str]] = {c: [] for c in ctx["order"]}
    by_cat: dict[str, list[dict]] = {}
    for code in ctx["order"]:
        it = ctx["items"][code]
        by_cat.setdefault(str(it.get("category") or "OTHER"), []).append(it)
    for _cat, items in by_cat.items():
        ranked = sorted((i for i in items if _f(i.get("sold_90d")) > 0),
                        key=lambda i: _f(i.get("sold_90d")), reverse=True)[:top_n]
        for i in ranked:
            out[str(i["item_code"])].append("best_seller")
    cutoff = _now() - timedelta(days=new_days)
    offer_codes = {c for c in ctx["order"]
                   if any(r["kind"] in ("qty_tier", "cart_value") and not r["scope"]["referral_codes"]
                          and _rule_matches_item(r, ctx["items"][c]) and r["scope"]["item_codes"]
                          for r in ctx["rules"])}
    for code in ctx["order"]:
        it = ctx["items"][code]
        s30, p30 = _f(it.get("sold_30d")), _f(it.get("prev_30d"))
        if s30 >= min_units and (p30 <= 0 or (s30 - p30) / p30 * 100.0 >= growth):
            out[code].append("trending")
        created = _parse_ts(it.get("created_at"))
        if created and created >= cutoff:
            out[code].append("new")
        if selling_fast(it.get("stock_qty"), it.get("sold_90d"), low_days):
            out[code].append("selling_fast")
        if code in offer_codes:
            out[code].append("on_offer")
        drop = drops.get(code.upper())
        if drop and drop["was"] > drop["now"] > 0:
            out[code].append("price_drop")
    # Clearance: real stock that the last 90 days of sales would take a year+ to move. The worst
    # movers first, capped, and never something that is also a best seller / trending / new.
    aging: list[tuple[float, str]] = []
    for code in ctx["order"]:
        if any(b in out[code] for b in ("best_seller", "trending", "new")):
            continue
        it = ctx["items"][code]
        qty, s90 = _f(it.get("stock_qty")), _f(it.get("sold_90d"))
        if qty < cl_min:
            continue
        cover = qty / (s90 / 90.0) if s90 > 0 else float("inf")
        if cover >= cl_days:
            aging.append((cover, code))
    for _cover, code in sorted(aging, key=lambda t: (-t[0], t[1]))[:cl_max]:
        out[code].append("clearance")
    return out


def _was_bhd(ctx: dict, code: str, price) -> float | None:
    """The previous trade price when a REAL cut happened inside the window and the live price is
    still the cut price. Never a made-up anchor: this is the price-book history."""
    drop = (ctx.get("drops") or {}).get(str(code).upper())
    if not drop or price is None:
        return None
    if abs(_f(price) - drop["now"]) > 0.0005 or drop["was"] <= drop["now"]:
        return None
    return money(drop["was"])


def _thumb(item: dict) -> str | None:
    return public_url(thumb_path(str(item["item_code"]), "product")) if item.get("product_image_url") else None


def _thumb_urls(item: dict) -> dict[str, str] | None:
    """The marketplace's WebP size set for srcset ({"160": url, "320": url, "512": url}).
    URL construction only — the files are written by upload_thumb / scripts.make_market_thumbs."""
    if not item.get("product_image_url"):
        return None
    code = str(item["item_code"])
    return {str(s): public_url(thumb_path(code, "product", s)) for s in THUMB_SIZES}


def resolve_ref(ctx: dict, referral_code: str | None) -> dict | None:
    code = (referral_code or "").strip().lower()
    if not code:
        return None
    for s in ctx["salesmen"]:
        if str(s.get("referral_code") or "").lower() == code:
            return {"referral_code": code, "salesman_id": s["id"], "salesman_name": s["name"]}
    return None


def rep_card(ctx: dict, slug: str | None) -> dict | None:
    """The public storefront card behind /{slug}: name, title, photo and — only when the rep
    opted in — a WhatsApp link. Nothing else about a salesman ever leaves the server; the slug
    selects attribution, it is not a credential."""
    ref = resolve_ref(ctx, slug)
    if not ref:
        return None
    s = next((x for x in ctx["salesmen"] if int(x["id"]) == int(ref["salesman_id"])), None)
    if not s or s.get("public_profile", True) is False:
        return None
    wa = None
    if s.get("public_whatsapp") and (s.get("whatsapp") or s.get("phone")):
        from app.shop_notify import wa_url
        wa = wa_url(s.get("whatsapp") or s.get("phone"), "Hello, I am ordering from your YQ Marketplace link.")
    name = str(s.get("name") or "")
    return {"slug": ref["referral_code"], "salesman_id": s["id"], "name": name,
            "first_name": name.split(" ")[0] if name else "", "title": s.get("title") or "YQ sales representative",
            "photo_url": s.get("photo_url"), "whatsapp_url": wa}


def catalog_payload(token: str | None, referral_code: str | None = None, *,
                    staff_email: str | None = None) -> dict | None:
    """The catalog payload. Public: token-gated, backward compatible with the pre-shop payload,
    NEVER carries quantities. Staff (`staff_email` = a logged-in user): no token, the user's own
    salesman row becomes `ref`, and every item carries `stock_qty` (exact units) — they sell against it.
    None = bad token."""
    staff = bool(staff_email)
    ctx = context()
    if not staff:
        good = ctx.get("share_token")
        if not (good and token and secrets.compare_digest(token, good)):
            # the cached token can trail a rotation made by another process: ask the table before refusing
            good = share_token(create=False)
            if not good or not token or not secrets.compare_digest(token, good):
                return None
    vals = ctx["settings"]
    low_units = _i(vals.get("shop_low_stock_units"), 10)
    proof_min = _i(vals.get("shop_social_proof_min_customers"), 5)
    show_compare = _flag(vals, "shop_show_retail_compare")
    clearance_retail = _flag(vals, "shop_clearance_show_retail")
    # owner decision 16-Sep-2026: trade prices are public; the volume ladders are a switch
    public_tiers = staff or _flag(vals, "shop_public_tiers")
    badges = _badges(ctx)
    items = []
    stock_as_of = None
    for code in ctx["order"]:
        it = ctx["items"][code]
        lp = it.get("standard_rate")
        b2c = it.get("b2c_rate")
        # The struck-through anchor is always a REAL price: the price book's retail (B2C) rate.
        # Shown everywhere when the compare setting is on; on Clearance items also when it is off.
        anchor_ok = show_compare or (clearance_retail and "clearance" in badges.get(code, []))
        compare = money(b2c) if (anchor_ok and lp is not None and b2c is not None and _f(b2c) > _f(lp)) else None
        save_pct = int(round((1 - _f(lp) / _f(b2c)) * 100)) if compare else None
        cust = _i(it.get("customers_30d"))
        stock_as_of = stock_as_of or it.get("stock_as_of")
        tiers = item_tiers(ctx, it)
        items.append({
            "item_code": code,
            "display_name": it.get("display_name"),
            "spec": it.get("spec"),
            "category": it.get("category"),
            "brand": it.get("brand"),
            "price_bhd": money(lp) if lp is not None else None,
            # owner (15-Sep): the public catalog mentions the B2B price only unless the compare setting is on
            "b2c_bhd": money(b2c) if (show_compare and b2c is not None) else None,
            "compare_at_bhd": compare,
            "save_pct": save_pct if save_pct and save_pct > 0 else None,
            "was_bhd": _was_bhd(ctx, code, lp),
            "product_image_url": it.get("product_image_url"),
            "package_image_url": it.get("package_image_url"),
            "thumb_url": _thumb(it),
            "thumb_urls": _thumb_urls(it),
            "stock_status": stock_status_for(it.get("stock_qty"), low_units),
            "moq": max(_i(it.get("moq"), 1), 1),
            "pack_size": _i(it.get("pack_size")) or None,
            "tiers": tiers if public_tiers else [],
            "has_tiers": bool(tiers),
            "badges": badges.get(code, []),
            "social_proof": f"Ordered by {cust} shops this month" if cust >= proof_min else None,
        })
        if staff:   # added outside the public literal on purpose — the public block must never carry it
            items[-1]["stock_qty"] = max(_i(it.get("stock_qty")), 0)
    cats = sorted({i.get("category") or "OTHER" for i in items},
                  key=lambda c: (CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99, c))
    upd = ctx.get("prices_updated")      # read once per context refresh, not once per visitor
    offers = []
    for r in ctx["rules"]:
        if r["kind"] not in ("cart_value", "qty_tier") or r["scope"]["referral_codes"]:
            continue
        offers.append({
            "id": r["id"], "name": r["name"], "kind": r["kind"], "summary": rule_summary(r),
            "ends_at": r.get("ends_at"), "min_value_bhd": r.get("min_value_bhd"),
            "min_qty": r.get("min_qty"), "pct_off": r.get("pct_off"),
            "amount_off_bhd": r.get("amount_off_bhd"), "coupon_code": None,
            "scope_codes": r["scope"]["item_codes"], "scope_categories": r["scope"]["categories"],
        })
    out = {
        "items": items, "categories": cats, "brand": "VFAN", "company": "YQ Bahrain",
        "prices_updated": upd, "stock_as_of": stock_as_of,
        "salesmen": [{"id": s["id"], "name": s["name"], "referral_code": s.get("referral_code")}
                     for s in ctx["salesmen"]],
        "offers": offers,
        "campaigns": campaigns_payload(ctx),
        "pairs": [{"item_code": c, "with": w} for c, w in ctx.get("pairs", {}).items() if w],
        "settings": {
            "currency": "BHD",
            "promises": promises_payload(vals),
            "min_order_bhd": money(vals.get("shop_min_order_bhd")),
            # how an order under the minimum is treated, so the market can say "BHD x away" before a quote
            "small_order_mode": small_order_mode(vals),
            "free_delivery_threshold_bhd": money(vals.get("shop_free_delivery_threshold_bhd")),
            "delivery_fee_bhd": money(vals.get("shop_delivery_fee_bhd")),
            "allow_backorder": _flag(vals, "shop_allow_backorder"),
            "show_retail_compare": show_compare,
            "public_tiers": public_tiers,
            "areas": [a.strip() for a in str(vals.get("shop_areas") or "").split(",") if a.strip()],
        },
        "ref": resolve_ref(ctx, referral_code),
        "rep": rep_card(ctx, referral_code),
    }
    if staff:
        sm = salesman_for_user(staff_email)
        out["mode"] = "salesman"
        out["me"] = {"salesman_id": sm["id"] if sm else None, "salesman_name": sm["name"] if sm else None,
                     "is_admin": False}   # the route overwrites is_admin from the auth role
        out["ref"] = ({"referral_code": sm.get("referral_code"), "salesman_id": sm["id"],
                       "salesman_name": sm["name"]} if sm else None)
    return out


def rule_summary(r: dict) -> str:
    what = (f"{_f(r['pct_off']):g}% off" if r.get("pct_off") is not None else
            f"BHD {_f(r['amount_off_bhd']):.3f} off" if r.get("amount_off_bhd") is not None else
            f"BHD {_f(r['fixed_price_bhd']):.3f} each" if r.get("fixed_price_bhd") is not None else "offer")
    if r["kind"] == "qty_tier":
        return f"{what} when you buy {_i(r.get('min_qty'))}+"
    if r["kind"] in ("cart_value", "coupon"):
        mv = _f(r.get("min_value_bhd"))
        return f"{what} on orders over BHD {mv:.3f}" if mv > 0 else what
    return what


# ── pricing engine ────────────────────────────────────────────────────────────

def _floor_for(ctx: dict, code: str) -> float | None:
    """Lowest VAT-inclusive unit price allowed: landed cost x (1 + margin) x (1 + VAT).
    The price book is VAT-inclusive (owner's workbook), so the floor must be too."""
    cost = ctx["costs"].get(code)
    if cost is None or cost <= 0:
        return None
    vals = ctx["settings"]
    return money(cost * (1 + _f(vals.get("shop_min_margin_pct"), 0.2)) * (1 + _f(vals.get("shop_vat_rate"), 0.0)))


def normalize_lines(raw_lines) -> list[tuple[str, int]]:
    """(item_code, qty) pairs, deduplicated. The code keeps the spelling it arrived
    with — the catalog's own casing is restored later by resolve_code(), so a cart
    holding "X05 UL-1Mtr" prices exactly like one holding "x05 ul-1mtr"."""
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ShopError("Your cart is empty.")
    if len(raw_lines) > MAX_LINES:
        raise ShopError(f"Too many lines (max {MAX_LINES}).")
    merged: dict[str, tuple[str, int]] = {}
    for ln in raw_lines:
        if not isinstance(ln, dict):
            raise ShopError("Bad line.")
        code = str(ln.get("item_code") or "").strip()[:64]
        qty = _i(ln.get("qty"))
        if not code:
            raise ShopError("Bad line: missing item code.")
        if qty <= 0 or qty > MAX_QTY:
            raise ShopError(f"Bad quantity for {code}.")
        seen, total = merged.get(code.upper(), (code, 0))
        merged[code.upper()] = (seen, total + qty)
    return list(merged.values())


def gap_fillers(ctx: dict, cart_codes: list[str], remaining: float, referral_code: str | None = None,
                *, limit: int = 6) -> list[dict]:
    """Products that would close the gap to the wholesale minimum, best first: pairs of what is
    already in the cart, then best sellers from the same categories, then anything in stock —
    each ranked by how close one default quantity lands on the gap without overshooting by more
    than one pack. Never a cart line, never out of stock, never the same product twice. On an
    exact tie (same tier, same closes-the-gap flag, same overshoot) a clearing line ranks first —
    the minimum doubles as the clearance engine, at the price-book price."""
    items = ctx.get("items") or {}
    if remaining <= 0 or not items:
        return []
    in_cart = set(cart_codes)
    pairs_map = ctx.get("pairs") or {}
    badges = ctx.get("_badges_cache")
    if not isinstance(badges, dict):
        try:
            badges = _badges(ctx)
        except Exception:  # noqa: BLE001
            badges = {}
        ctx["_badges_cache"] = badges
    cats = {str((items.get(c) or {}).get("category") or "") for c in cart_codes}
    cands: dict[str, int] = {}     # code -> tier (0 pairs, 1 same-category best seller, 2 anything)
    for c in cart_codes:
        for w in pairs_map.get(c, []) or []:
            cands.setdefault(w, 0)
    for code, it in items.items():
        if code in cands:
            continue
        if str(it.get("category") or "") in cats and "best_seller" in (badges.get(code) or []):
            cands.setdefault(code, 1)
    if len(cands) < limit * 2:
        for code in items:
            cands.setdefault(code, 2)
    scored = []
    for code, tier in cands.items():
        if code in in_cart:
            continue
        it = items.get(code) or {}
        price = _f(it.get("standard_rate"))
        stock = _f(it.get("stock_qty"))
        if price <= 0 or stock <= 0:
            continue
        pack = max(_i(it.get("pack_size"), 1), 1)
        moq = max(_i(it.get("moq"), 1), 1)
        # the smallest multiple of the pack, at least the MOQ, whose value reaches the gap; else all of one pack
        qty = max(moq, pack)
        while price * qty < remaining and qty < 999:
            qty += pack
        value = money(price * qty)
        over = value - remaining
        if over > price * pack + 1e-9 and value > remaining:      # more than one pack past the gap: try a smaller step
            qty = max(moq, pack)
            value = money(price * qty)
            over = value - remaining
        closes = value >= remaining
        clearing = "clearance" in (badges.get(code) or [])
        scored.append((tier, 0 if closes else 1, abs(over), 0 if clearing else 1, code, qty, value))
    scored.sort()
    out = []
    for tier, _c, _o, _cl, code, qty, value in scored[:max(limit, 0)]:
        it = items[code]
        out.append({"item_code": code, "display_name": it.get("display_name") or code, "qty": qty,
                    "unit_price_bhd": money(it.get("standard_rate")), "value_bhd": value,
                    "closes_gap": value >= remaining,
                    "why": "pairs" if tier == 0 else "popular" if tier == 1 else "in_stock"})
    return out


def price_cart(raw_lines, coupon_code: str | None = None, referral_code: str | None = None,
               ctx: dict | None = None) -> dict:
    """Price a cart server-side. Raises ShopError for anything the customer must fix."""
    ctx = ctx or context()
    vals = ctx["settings"]
    low_units = _i(vals.get("shop_low_stock_units"), 10)
    allow_bo = _flag(vals, "shop_allow_backorder")
    ref = (referral_code or "").strip().lower() or None
    lines_in = normalize_lines(raw_lines)

    lines: list[dict] = []      # every line the customer can see, in cart order
    good: list[dict] = []       # the priced ones — all the arithmetic below uses these
    warnings: list[str] = []
    clamped: list[str] = []
    subtotal = 0.0

    def _blocked(code: str, qty: int, reason: str, **extra) -> dict:
        """A line that cannot be ordered as it stands. It stays visible with its
        reason and a price of zero — one dead line must never zero the whole cart,
        which is what a customer reads as 'the site is broken'."""
        ln = {"item_code": code, "display_name": code, "spec": None, "image_url": None,
              "qty": qty, "moq": 1, "list_price_bhd": 0.0, "unit_price_bhd": 0.0,
              "discount_bhd": 0.0, "line_total_bhd": 0.0, "stock_status": STOCK_OUT,
              "backorder": False, "applied": [], "warning": None,
              "unavailable": True, "blocked_reason": reason, "_floor": None}
        ln.update(extra)
        return ln

    for raw_code, qty in lines_in:
        code = resolve_code(ctx, raw_code)
        it = ctx["items"].get(code) if code else None
        if not it:
            lines.append(_blocked(raw_code, qty, "No longer in the catalog."))
            continue
        lp = it.get("standard_rate")
        if lp is None or _f(lp) <= 0:
            lines.append(_blocked(code, qty, "Price on request — ask your salesman.",
                                  display_name=it.get("display_name") or code, spec=it.get("spec"),
                                  image_url=it.get("product_image_url")))
            continue
        lp = money(lp)
        moq = max(_i(it.get("moq"), 1), 1)
        status = stock_status_for(it.get("stock_qty"), low_units)
        backorder = status == STOCK_OUT
        if qty < moq or (backorder and not allow_bo):
            # owner, 15-Sep: customers read "Sold out" (true, and it says the line sells)
            reason = (f"Minimum order is {moq}." if qty < moq else "Sold out.")
            lines.append(_blocked(code, qty, reason,
                                  display_name=it.get("display_name") or code, spec=it.get("spec"),
                                  image_url=it.get("product_image_url"), moq=moq,
                                  list_price_bhd=lp, stock_status=status))
            continue
        if backorder:
            warnings.append(f"{code} is sold out — it will be backordered and confirmed by your salesman.")
        # item-level rules: best non-stackable, then stackables on top
        cands = [r for r in ctx["rules"] if r["kind"] in ("qty_tier", "salesman_offer", "bundle_price")
                 and _rule_matches_item(r, it) and _rule_matches_ref(r, ref)
                 and (r["kind"] != "qty_tier" or qty >= _i(r.get("min_qty"), 1))
                 and (r["kind"] != "salesman_offer" or (ref and r["scope"]["referral_codes"]))]
        unit = lp
        applied: list[dict] = []
        best = None
        for r in cands:
            if r.get("stackable"):
                continue
            u = _discounted_unit(r, lp)
            if u < unit:
                unit, best = u, r
        if best:
            applied.append({"rule_id": best["id"], "name": best["name"], "kind": best["kind"]})
        for r in cands:
            if r.get("stackable"):
                u = _discounted_unit(r, unit)
                if u < unit:
                    unit = u
                    applied.append({"rule_id": r["id"], "name": r["name"], "kind": r["kind"]})
        unit = money(max(unit, 0.0))
        floor = _floor_for(ctx, code)
        if floor is not None and unit < floor:
            unit = min(lp, floor)
            clamped.append(code)
        line_total = money(unit * qty)
        subtotal += money(lp * qty)
        line = {
            "item_code": code, "display_name": it.get("display_name") or code, "spec": it.get("spec"),
            "image_url": it.get("product_image_url"), "qty": qty, "moq": moq,
            "list_price_bhd": lp, "unit_price_bhd": unit, "discount_bhd": money((lp - unit) * qty),
            "line_total_bhd": line_total, "stock_status": status, "backorder": backorder,
            "applied": applied, "warning": None, "unavailable": False, "blocked_reason": None,
            "_floor": floor,
        }
        lines.append(line)
        good.append(line)
    subtotal = money(subtotal)
    item_discount = money(sum(ln["discount_bhd"] for ln in good))
    net = money(subtotal - item_discount)

    # cart-level: automatic cart_value rules vs coupon — customer gets the better unless stackable
    discounts = [{"rule_id": a["rule_id"], "name": a["name"], "kind": a["kind"],
                  "amount_bhd": ln["discount_bhd"]}
                 for ln in good for a in ln["applied"]]
    headroom = 0.0
    unlimited = False
    for ln in good:
        if ln["_floor"] is None:
            unlimited = True
        else:
            headroom += max(0.0, (ln["unit_price_bhd"] - ln["_floor"]) * ln["qty"])
    cap = float("inf") if unlimited else headroom

    def _cart_amount(rule: dict) -> float:
        eligible = sum(ln["line_total_bhd"] for ln in good if _rule_matches_item(rule, ctx["items"][ln["item_code"]]))
        if rule.get("min_value_bhd") is not None and net < _f(rule["min_value_bhd"]):
            return 0.0
        if rule.get("pct_off") is not None:
            return eligible * _f(rule["pct_off"]) / 100.0
        if rule.get("amount_off_bhd") is not None and eligible > 0:
            return min(eligible, _f(rule["amount_off_bhd"]))
        return 0.0

    auto = [r for r in ctx["rules"] if r["kind"] == "cart_value" and _rule_matches_ref(r, ref)]
    best_auto, best_amt = None, 0.0
    stack_auto: list[tuple[dict, float]] = []
    for r in auto:
        amt = _cart_amount(r)
        if amt <= 0:
            continue
        if r.get("stackable"):
            stack_auto.append((r, amt))
        elif amt > best_amt:
            best_auto, best_amt = r, amt

    coupon_out = None
    coupon_rule, coupon_amt = None, 0.0
    cc = (coupon_code or "").strip().upper()
    if cc:
        match = next((r for r in ctx["rules"] if r["kind"] == "coupon"
                      and str(r.get("coupon_code") or "").upper() == cc), None)
        if not match:
            coupon_out = {"code": cc, "valid": False, "message": "This code is not valid or has expired."}
        elif not _rule_matches_ref(match, ref):
            coupon_out = {"code": cc, "valid": False, "message": "This code is for a different salesman's customers."}
        else:
            amt = _cart_amount(match)
            if amt <= 0:
                mv = _f(match.get("min_value_bhd"))
                msg = (f"Add BHD {money(mv - net):.3f} more to use this code." if mv > net
                       else "This code does not apply to these items.")
                coupon_out = {"code": cc, "valid": False, "message": msg}
            else:
                coupon_rule, coupon_amt = match, amt

    cart_discounts: list[tuple[dict, float]] = []
    if coupon_rule and best_auto:
        if coupon_rule.get("stackable") or best_auto.get("stackable"):
            cart_discounts += [(best_auto, best_amt), (coupon_rule, coupon_amt)]
        elif coupon_amt >= best_amt:
            cart_discounts.append((coupon_rule, coupon_amt))
        else:
            cart_discounts.append((best_auto, best_amt))
            coupon_out = {"code": cc, "valid": True,
                          "message": f"{best_auto['name']} already gives you more — code not needed."}
            coupon_rule = None
    elif coupon_rule:
        cart_discounts.append((coupon_rule, coupon_amt))
    elif best_auto:
        cart_discounts.append((best_auto, best_amt))
    cart_discounts += stack_auto

    cart_total = 0.0
    for r, amt in cart_discounts:
        amt = money(min(amt, max(0.0, cap - cart_total)))
        if amt < money(_cart_amount(r)):
            clamped.append(f"cart:{r['id']}")
        if amt <= 0:
            continue
        cart_total += amt
        discounts.append({"rule_id": r["id"], "name": r["name"], "kind": r["kind"], "amount_bhd": amt})
    if coupon_rule and coupon_out is None:
        coupon_out = {"code": cc, "valid": True, "message": f"{rule_summary(coupon_rule)} applied"}

    discount_total = money(item_discount + cart_total)
    net_after = money(subtotal - discount_total)
    threshold = money(vals.get("shop_free_delivery_threshold_bhd"))
    fee = money(vals.get("shop_delivery_fee_bhd"))
    delivery = fee if (good and threshold > 0 and fee > 0 and net_after < threshold) else 0.0
    total = money(net_after + delivery)

    progress = None
    if threshold > 0:
        remaining = money(max(0.0, threshold - net_after))
        progress = {"kind": "free_delivery", "threshold_bhd": threshold, "remaining_bhd": remaining,
                    "unlocked": remaining <= 0,
                    "label": ("Free delivery unlocked" if remaining <= 0
                              else f"Add BHD {remaining:.3f} more for free delivery")}
    else:
        nxt = sorted((r for r in auto if r.get("min_value_bhd") is not None and _f(r["min_value_bhd"]) > net_after),
                     key=lambda r: _f(r["min_value_bhd"]))
        if nxt:
            r = nxt[0]
            remaining = money(_f(r["min_value_bhd"]) - net_after)
            progress = {"kind": "cart_value", "threshold_bhd": money(r["min_value_bhd"]),
                        "remaining_bhd": remaining, "unlocked": False,
                        "label": f"Add BHD {remaining:.3f} more to unlock {r['name']}"}
        elif best_auto:
            progress = {"kind": "cart_value", "threshold_bhd": money(best_auto.get("min_value_bhd")),
                        "remaining_bhd": 0.0, "unlocked": True, "label": f"{best_auto['name']} applied"}

    min_order = money(vals.get("shop_min_order_bhd"))
    small_mode = small_order_mode(vals)
    # One thing at a time, in the order the customer can act on it: clear the dead
    # line first, then top up to the minimum. Totals above are already the price of
    # what IS orderable, so the number on the button stays true either way.
    blocked = [ln for ln in lines if ln.get("unavailable")]
    block_reason = None
    if blocked:
        names = ", ".join(ln["item_code"] for ln in blocked[:3])
        more = f" and {len(blocked) - 3} more" if len(blocked) > 3 else ""
        block_reason = (f"Remove {names}{more} to send this order — "
                        f"{blocked[0]['blocked_reason'].rstrip('.').lower()}."
                        if len(blocked) == 1 else
                        f"Remove {names}{more} to send this order.")
    elif not good:
        block_reason = "Your order is empty."
    elif net_after < min_order and small_mode == "block":
        block_reason = f"Minimum order is BHD {min_order:.3f} — add BHD {money(min_order - net_after):.3f} more."
    can_submit = block_reason is None
    # The wholesale minimum as a sales engine: how far to go, and what would close the gap.
    minimum = None
    gap_suggestions: list[dict] = []
    if min_order > 0 and good:
        remaining = money(max(0.0, min_order - net_after))
        under = remaining > 0
        minimum = {"value_bhd": min_order, "remaining_bhd": remaining, "met": not under,
                   "mode": small_mode, "kind": ("small" if under and small_mode != "block" else "standard"),
                   "fee_bhd": money(vals.get("shop_small_order_fee_bhd")) if under and small_mode == "request" else 0.0}
        if under:
            gap_suggestions = gap_fillers(ctx, [ln["item_code"] for ln in good], remaining, referral_code,
                                          limit=_i(vals.get("shop_gap_suggestions"), 6))
    if clamped:
        log.info("shop margin floor clamped: %s", ", ".join(clamped))
    for ln in lines:
        ln.pop("_floor", None)
    return {
        "ok": True, "lines": lines,
        "subtotal_bhd": subtotal, "discount_bhd": discount_total, "delivery_bhd": delivery,
        "total_bhd": total, "units": sum(ln["qty"] for ln in good), "items": len(good),
        "discounts": discounts, "coupon": coupon_out, "progress": progress,
        "warnings": warnings, "can_submit": can_submit, "min_order_bhd": min_order,
        "block_reason": block_reason, "minimum": minimum, "gap_suggestions": gap_suggestions,
        "has_backorder": any(ln["backorder"] for ln in good),
        "_coupon_rule_id": coupon_rule["id"] if coupon_rule else None,
        "_clamped": clamped,
    }


# ── salesman resolution ───────────────────────────────────────────────────────

def salesman_for(ctx: dict, referral_code: str | None, salesman_id: int | None) -> tuple[dict | None, str]:
    """(salesman row | None, source). Precedence: referral link → dropdown → default setting."""
    ref = (referral_code or "").strip().lower()
    if ref:
        for s in ctx["salesmen"]:
            if str(s.get("referral_code") or "").lower() == ref:
                return s, "referral"
    if salesman_id:
        for s in ctx["salesmen"]:
            if int(s["id"]) == int(salesman_id):
                return s, "dropdown"
    default = str(ctx["settings"].get("shop_default_salesman") or "").strip().lower()
    if default:
        for s in ctx["salesmen"]:
            if str(s.get("name") or "").lower() == default:
                return s, "default"
    return None, "default"


def _salesman_by(ctx: dict, sid) -> dict | None:
    if not sid:
        return None
    return next((s for s in ctx["salesmen"] if int(s["id"]) == _i(sid)), None)


def recognize_phone(raw_phone: str | None, device_id: str | None) -> dict | None:
    """A returning merchant on a new phone: the shop name, area and first name for a number we
    already know — nothing else (no email, no history). The device joins the merchant's list so
    the next visit is recognised without typing. None for an unknown number."""
    digits = re.sub(r"\D", "", str(raw_phone or ""))
    phone = ("973" + digits) if len(digits) == 8 else digits
    if len(phone) < 8:
        return None
    cust = _customer_by_phone(phone)
    if not cust:
        return None
    if device_id:
        try:
            devs = [d for d in (cust.get("device_ids") or []) if isinstance(d, str) and d != device_id]
            get_client().table("shop_customers").update({"device_ids": ([device_id] + devs)[:10], "updated_at": _iso()}) \
                .eq("id", cust["id"]).execute()
        except Exception as e:  # noqa: BLE001
            log.debug("recognize device update failed: %s", e)
    first = (str(cust.get("name") or "").strip().split(" ") or [""])[0]
    return {"shop": cust.get("shop") or None, "area": cust.get("area") or None, "first_name": first or None,
            "orders_count": int(cust.get("orders_count") or 0)}


def _customer_by_phone(phone: str | None) -> dict | None:
    """The merchant record for a normalised phone: the primary number first, then the extra
    numbers table (one shop, several phones). None when unknown or when the tables are missing."""
    if not phone:
        return None
    try:
        c = get_client()
        r = c.table("shop_customers").select("*").eq("phone", phone).limit(1).execute().data
        if r:
            return r[0]
        m = c.table("shop_customer_phones").select("customer_id").eq("phone", phone).limit(1).execute().data
        if m:
            r = c.table("shop_customers").select("*").eq("id", m[0]["customer_id"]).limit(1).execute().data
            return r[0] if r else None
    except Exception as e:  # noqa: BLE001
        log.debug("customer lookup failed: %s", e)
    return None


def resolve_salesman(ctx: dict, *, phone: str | None, session_ref: str | None, pick_id=None,
                     customer: dict | None = None) -> tuple[dict | None, str, bool]:
    """(salesman | None, attribution_source, conflict) for a merchant-placed order.

    Precedence (plan §M): the merchant's admin assignment → an official Focus mapping (placeholder
    column) → the sticky first-touch rep while the merchant's last order is within
    shop_sticky_days → the rep whose link / slug this session arrived with → an explicit pick at
    checkout → the shop_default_salesman setting → unassigned (the admin queue).
    `conflict` is True when the session's rep differs from the recorded one, so management sees a
    merchant being courted by two reps instead of the order switching silently."""
    cust = customer if customer is not None else _customer_by_phone(phone)
    ref = (session_ref or "").strip().lower()
    ref_sm = next((s for s in ctx["salesmen"] if ref and str(s.get("referral_code") or "").lower() == ref), None)

    def _conflict(chosen: dict | None) -> bool:
        return bool(ref_sm and chosen and int(ref_sm["id"]) != int(chosen["id"]))

    if cust:
        admin_sm = _salesman_by(ctx, cust.get("salesman_id"))
        if admin_sm:
            return admin_sm, "customer_admin", _conflict(admin_sm)
        focus_name = str(cust.get("focus_salesman_name") or "").strip().lower()
        if focus_name:
            fm = next((s for s in ctx["salesmen"]
                       if focus_name in (str(s.get("focus_name") or "").lower(), str(s.get("name") or "").lower())), None)
            if fm:
                return fm, "focus_map", _conflict(fm)
        sticky = _salesman_by(ctx, cust.get("sticky_salesman_id"))
        if sticky:
            days = _i(ctx["settings"].get("shop_sticky_days"), 90)
            last = _parse_ts(cust.get("last_order_at"))
            inside = days <= 0 or (last is not None and (_now() - last).days <= days)
            if inside or not ref_sm:
                return sticky, "sticky", _conflict(sticky)
            # past the window a live link from another rep wins — flagged, so it can be reviewed
            return ref_sm, "session_ref", True
    if ref_sm:
        return ref_sm, "session_ref", False
    pick = _salesman_by(ctx, pick_id)
    if pick:
        return pick, "checkout_pick", False
    default = str(ctx["settings"].get("shop_default_salesman") or "").strip().lower()
    if default:
        d = next((s for s in ctx["salesmen"] if str(s.get("name") or "").lower() == default), None)
        if d:
            return d, "default", False
    return None, "unassigned", False


def upsert_customer_from_order(row: dict, sm: dict | None, attribution: str, device_id: str | None,
                               existing: dict | None) -> int | None:
    """Create or refresh the merchant record behind an order. Anonymous orders never overwrite
    what is on file: profile fields are write-if-blank, the sticky rep is written once, and the
    admin assignment is never touched here. Returns the customer id, or None when the memory
    layer is unavailable — the order itself must never fail because of it."""
    c = get_client()
    now = _iso()
    sticky_ok = sm is not None and attribution in ("session_ref", "checkout_pick", "default", "staff")
    try:
        if existing:
            upd: dict = {"orders_count": _i(existing.get("orders_count")) + 1, "last_order_at": now,
                         "total_bhd": money(_f(existing.get("total_bhd")) + _f(row.get("total_bhd"))),
                         "updated_at": now}
            for k, v in (("name", row.get("customer_name")), ("shop", row.get("customer_shop")),
                         ("area", row.get("customer_area")), ("email", row.get("customer_email"))):
                if v and not existing.get(k):
                    upd[k] = v
            if sticky_ok and not existing.get("sticky_salesman_id"):
                upd["sticky_salesman_id"] = sm["id"]
                upd["first_ref"] = existing.get("first_ref") or row.get("session_ref")
            devs = [d for d in (existing.get("device_ids") or []) if isinstance(d, str)]
            if device_id and device_id not in devs:
                upd["device_ids"] = ([device_id] + devs)[:10]
            c.table("shop_customers").update(upd).eq("id", existing["id"]).execute()
            return existing["id"]
        ins = {"phone": row["customer_phone"], "name": row.get("customer_name"), "shop": row.get("customer_shop"),
               "area": row.get("customer_area"), "email": row.get("customer_email"),
               "sticky_salesman_id": sm["id"] if sticky_ok else None,
               "first_ref": row.get("session_ref"), "first_order_at": now, "last_order_at": now,
               "orders_count": 1, "total_bhd": money(row.get("total_bhd")),
               "device_ids": [device_id] if device_id else [], "created_at": now, "updated_at": now}
        got = c.table("shop_customers").insert(ins).execute().data
        return got[0]["id"] if got else None
    except Exception as e:  # noqa: BLE001
        log.warning("customer upsert failed (order still created): %s", e)
        return None


def _recent_order_count(field: str, value: str | None, hours: int = 24) -> int:
    if not value:
        return 0
    try:
        since = (_now() - timedelta(hours=hours)).isoformat()
        r = (get_client().table("shop_orders").select("id", count="exact").eq(field, value)
             .gte("created_at", since).limit(1).execute())
        return r.count or 0
    except Exception:  # noqa: BLE001
        return 0


# ── validation helpers ────────────────────────────────────────────────────────

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(s, limit: int) -> str:
    return _CTRL.sub("", str(s or "")).strip()[:limit]


def phone_digits(raw: str | None) -> str | None:
    d = re.sub(r"\D", "", str(raw or ""))
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 8:                      # Bahrain local
        d = "973" + d
    if not (10 <= len(d) <= 15):
        return None
    return d


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    salt = cfg.optout_secret or (cfg.supabase_key or "")[:24] or "yq"
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


def _base_url() -> str:
    return (os.getenv("APP_BASE_URL", "") or "").rstrip("/")


def market_base() -> str:
    """The origin merchants should see in links (storefronts, tracking pages): the marketplace
    hostname from the `shop_market_url` setting when the owner has set it, else the portal's
    APP_BASE_URL, which also serves /o/{token} and /c/{token}."""
    try:
        u = str(shop_settings().get("shop_market_url") or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001
        u = ""
    return u or _base_url()


# ── order creation ────────────────────────────────────────────────────────────

def _totals_from_order(o: dict) -> dict:
    """A quote-shaped summary rebuilt from a stored order (used when a retry hits an order that
    already exists, so the client gets the same answer it missed)."""
    return {"ok": True, "subtotal_bhd": money(o.get("subtotal_bhd")), "discount_bhd": money(o.get("discount_bhd")),
            "delivery_bhd": money(o.get("delivery_bhd")), "total_bhd": money(o.get("total_bhd")),
            "units": _i(o.get("units_count")), "items": _i(o.get("items_count")),
            "lines": [{"item_code": ln["item_code"], "display_name": ln.get("display_name"), "qty": ln["qty"],
                       "unit_price_bhd": money(ln.get("unit_price_bhd")), "line_total_bhd": money(ln.get("line_total_bhd")),
                       "stock_status": ln.get("stock_status"), "backorder": bool(ln.get("backorder"))}
                      for ln in o.get("lines", [])],
            "has_backorder": bool(o.get("has_backorder")), "can_submit": True, "warnings": [], "discounts": [],
            "coupon": None, "progress": None, "block_reason": None, "min_order_bhd": 0.0}


def create_order(body: dict, ip: str | None = None, ua: str | None = None, *,
                 staff_email: str | None = None, market: bool = False) -> dict:
    """Validate → re-price → persist. Returns the stored order (+lines) and the priced totals.
    `staff_email` = a logged-in salesman/admin placing the order FOR a shop (source 'salesman').
    `market` = the token-less marketplace (source 'market'): attribution through resolve_salesman,
    device + client_order_id idempotency, per-phone/per-device caps, and the merchant record
    behind the order. The legacy token link goes through the same path with market=False."""
    staff = bool(staff_email)
    if not staff and clean(body.get("website"), 10):
        raise ShopError("Invalid submission.")
    cust = body.get("customer") or {}
    name = clean(cust.get("name"), 120)
    phone = phone_digits(cust.get("phone"))
    if len(name) < 2:
        raise ShopError("Please enter your name.")
    if not phone:
        raise ShopError("Please enter a valid phone number (8-digit Bahrain or international).")
    email = clean(cust.get("email"), 160) or None
    if email and not _EMAIL.match(email):
        raise ShopError("Please enter a valid email or leave it blank.")
    device_id = clean(body.get("device_id"), 64) or None
    client_order_id = clean(body.get("client_order_id"), 64) or None
    client = get_client()
    if device_id and client_order_id:
        # A retry after a timeout must return the order this exact submission already created.
        try:
            dup = (client.table("shop_orders").select("id").eq("device_id", device_id)
                   .eq("client_order_id", client_order_id).limit(1).execute().data)
        except Exception:  # noqa: BLE001
            dup = None
        if dup:
            o = get_order(dup[0]["id"]) or {}
            o["duplicate"] = True
            o["status_url"] = f"{_base_url()}/o/{o.get('token')}" if _base_url() else f"/o/{o.get('token')}"
            o["totals"] = _totals_from_order(o)
            return o
    ctx = context()
    vals = ctx["settings"]
    if not staff:
        cap_phone = _i(vals.get("shop_phone_daily_cap"), 10)
        cap_dev = _i(vals.get("shop_device_daily_cap"), 20)
        if cap_phone > 0 and _recent_order_count("customer_phone", phone) >= cap_phone:
            raise ShopError("This phone number has placed many orders today — please contact your salesman.")
        if device_id and cap_dev > 0 and _recent_order_count("device_id", device_id) >= cap_dev:
            raise ShopError("Too many orders from this device today — please contact your salesman.")
    session_ref = clean(body.get("session_ref") or body.get("referral_code"), 32).lower() or None
    existing_cust = _customer_by_phone(phone)
    conflict = False
    if staff:
        sm = salesman_for_user(staff_email)
        if not sm and body.get("salesman_id"):     # an admin with no linked salesman row picks one
            sm = next((s for s in ctx["salesmen"] if int(s["id"]) == _i(body.get("salesman_id"))), None)
        source, attribution = "salesman", "staff"
        referral_code = (sm or {}).get("referral_code")   # so salesman-scoped offers still apply
    else:
        sm, attribution, conflict = resolve_salesman(ctx, phone=phone, session_ref=session_ref,
                                                     pick_id=body.get("salesman_id"), customer=existing_cust)
        # offers scoped to a rep follow the rep who owns the order, not the link that was clicked
        referral_code = (sm or {}).get("referral_code") if attribution in ("customer_admin", "focus_map", "sticky") \
            else session_ref
        if market:
            source = "market"
        else:   # the legacy token link keeps its coarse channel values
            source = {"session_ref": "referral", "checkout_pick": "dropdown"}.get(attribution, "default")
    quote = price_cart(body.get("lines"), body.get("coupon_code"), referral_code, ctx=ctx)
    if not quote["can_submit"]:
        raise ShopError(quote["block_reason"] or " ".join(quote["warnings"]) or "Order cannot be submitted.")
    minimum = quote.get("minimum") or {}
    order_kind = "small" if minimum and not minimum.get("met") else "standard"
    prefix = str(vals.get("shop_order_prefix") or "YQ")
    try:
        order_no = client.rpc("shop_next_order_no", {"p_prefix": prefix}).execute().data
    except Exception as e:  # noqa: BLE001
        log.error("order number RPC failed: %s", e)
        raise ShopError("Ordering is temporarily unavailable — please try again in a minute.") from e
    if isinstance(order_no, (list, dict)):
        order_no = json.dumps(order_no)
    token = secrets.token_urlsafe(24)
    row = {
        "order_no": str(order_no), "token": token, "status": "new",
        "order_kind": order_kind,
        "minimum_gap_bhd": money(minimum.get("remaining_bhd")) if order_kind == "small" else None,
        "customer_name": name, "customer_phone": phone,
        "customer_shop": clean(cust.get("shop"), 120) or None,
        "customer_area": clean(cust.get("area"), 120) or None,
        "customer_email": email, "note": clean(body.get("note"), 1000) or None,
        "salesman_id": sm["id"] if sm else None, "salesman_name": sm["name"] if sm else None,
        "source": source, "referral_code": clean(referral_code, 32).lower() or None,
        "src": "salesman" if staff else (clean(body.get("src"), 80) or None),
        "placed_by": staff_email if staff else None,
        "coupon_code": (quote["coupon"] or {}).get("code") if (quote["coupon"] or {}).get("valid") else None,
        "subtotal_bhd": quote["subtotal_bhd"], "discount_bhd": quote["discount_bhd"],
        "delivery_bhd": quote["delivery_bhd"], "total_bhd": quote["total_bhd"],
        "items_count": quote["items"], "units_count": quote["units"],
        "has_backorder": quote["has_backorder"], "ip_hash": _ip_hash(ip), "ua": (ua or "")[:200],
        "device_id": device_id, "client_order_id": client_order_id, "session_ref": session_ref,
        "attribution_source": attribution, "attribution_conflict": bool(conflict),
        "assigned_at": _iso() if sm else None, "assigned_by": "system" if sm else None,
        "created_at": _iso(), "updated_at": _iso(),
    }
    row["customer_id"] = upsert_customer_from_order(row, sm, attribution, device_id, existing_cust)
    order = client.table("shop_orders").insert(row).execute().data[0]
    lines = [{
        "order_id": order["id"], "item_code": ln["item_code"], "display_name": ln["display_name"],
        "spec": ln.get("spec"), "image_url": ln.get("image_url"), "qty": ln["qty"],
        "list_price_bhd": ln["list_price_bhd"], "unit_price_bhd": ln["unit_price_bhd"],
        "discount_bhd": ln["discount_bhd"], "line_total_bhd": ln["line_total_bhd"],
        "stock_status": ln["stock_status"], "backorder": ln["backorder"],
        "line_status": "backorder" if ln["backorder"] else "ok",
        "rule_ids": [a["rule_id"] for a in ln["applied"]] or None,
    } for ln in quote["lines"]]
    client.table("shop_order_lines").insert(lines).execute()
    client.table("shop_order_events").insert({
        "order_id": order["id"], "actor": (staff_email if staff else "customer"), "event": "created",
        "detail": {"source": source, "attribution": attribution, "conflict": bool(conflict),
                   "clamped": quote.get("_clamped") or []}}).execute()
    if quote.get("_coupon_rule_id"):
        try:
            r = client.table("discount_rules").select("uses").eq("id", quote["_coupon_rule_id"]).execute().data
            client.table("discount_rules").update({"uses": _i((r or [{}])[0].get("uses")) + 1}) \
                .eq("id", quote["_coupon_rule_id"]).execute()
        except Exception as e:  # noqa: BLE001
            log.warning("coupon use count failed: %s", e)
    record_event({"event": "order", "session_id": body.get("session_id"),
                  "referral_code": row["referral_code"], "salesman_id": row["salesman_id"],
                  "src": row["src"], "device_id": device_id, "customer_id": row.get("customer_id"),
                  "meta": {"attribution": attribution}}, ip=ip, ua=ua)
    order["lines"] = quote["lines"]
    order["salesman"] = sm
    order["status_url"] = f"{market_base()}/o/{token}" if market_base() else f"/o/{token}"
    order["totals"] = {k: v for k, v in quote.items() if not k.startswith("_")}
    return order


# ── events ────────────────────────────────────────────────────────────────────

def record_event(body: dict, ip: str | None = None, ua: str | None = None) -> bool:
    ev = str(body.get("event") or "").strip().lower()
    if ev not in EVENTS:
        return False
    meta = body.get("meta")
    if isinstance(meta, dict):
        # small and PII-free by construction: known keys, short scalars only
        meta = {k: (clean(v, 120) if isinstance(v, str) else v)
                for k, v in list(meta.items())[:12]
                if k in _META_KEYS and isinstance(v, (str, int, float, bool))}
    else:
        meta = None
    try:
        get_client().table("shop_events").insert({
            "session_id": clean(body.get("session_id"), 64) or None, "event": ev,
            "item_code": clean(body.get("item_code"), 64).upper() or None,
            "referral_code": clean(body.get("referral_code"), 32).lower() or None,
            "salesman_id": _i(body.get("salesman_id")) or None,
            "src": clean(body.get("src"), 80) or None,
            "device_id": clean(body.get("device_id"), 64) or None,
            "customer_id": _i(body.get("customer_id")) or None,
            "meta": meta or None,
            "ua": (ua or "")[:200], "ip_hash": _ip_hash(ip),
        }).execute()
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("shop event insert failed: %s", e)
        return False


# ── reads ─────────────────────────────────────────────────────────────────────

def _order_lines(order_id: int) -> list[dict]:
    return (get_client().table("shop_order_lines").select("*").eq("order_id", order_id)
            .order("id").execute().data or [])


def _order_events(order_id: int) -> list[dict]:
    return (get_client().table("shop_order_events").select("ts,actor,event,detail")
            .eq("order_id", order_id).order("id").execute().data or [])


def get_order(order_id: int) -> dict | None:
    r = get_client().table("shop_orders").select("*").eq("id", order_id).limit(1).execute().data
    if not r:
        return None
    o = r[0]
    o["lines"] = _order_lines(o["id"])
    o["events"] = _order_events(o["id"])
    o["salesman"] = _salesman_by_id(o.get("salesman_id"))
    return o


def get_order_by_token(token: str) -> dict | None:
    if not token or len(token) < 16:
        return None
    r = get_client().table("shop_orders").select("*").eq("token", token).limit(1).execute().data
    if not r:
        return None
    return get_order(r[0]["id"])


def _salesman_by_id(sid) -> dict | None:
    if not sid:
        return None
    r = get_client().table("salesmen").select("*").eq("id", sid).limit(1).execute().data
    return r[0] if r else None


_STAGE_TS = {"new": "created_at", "confirmed": "confirmed_at", "packed": "packed_at",
             "out_for_delivery": "out_for_delivery_at", "delivered": "delivered_at"}


def order_steps(o: dict) -> list[dict]:
    """The five merchant-facing stages with done/current flags and timestamps. A cancelled order
    keeps the stages it reached and carries `cancelled` on the order itself."""
    status = o.get("status")
    reached = TRACK_STEPS.index(status) if status in TRACK_STEPS else (-1 if status == "cancelled" else 0)
    last = len(TRACK_STEPS) - 1
    # 'packed' is optional: an order that went straight to On the way counts Preparing as passed;
    # Delivered is both the current and a completed step.
    steps = []
    for i, s in enumerate(TRACK_STEPS):
        steps.append({"status": s, "label": STATUS_LABELS[s],
                      "done": reached > i or (reached == last and i == last),
                      "current": reached == i,
                      "at": o.get(_STAGE_TS[s]) if reached >= i else None})
    return steps


def public_order_view(o: dict) -> dict:
    """What the customer's status page may see (no phone/email/ip of anyone). The customer only
    holds this order's token, so nothing here reaches beyond this one order."""
    from app.shop_notify import customer_to_salesman_email_url, customer_to_salesman_wa_url
    sm = o.get("salesman") or {}
    wa = customer_to_salesman_wa_url(o)   # falls back to the owner number when no salesman is set
    mail = customer_to_salesman_email_url(o)
    confirmed_total = o.get("total_confirmed_bhd")
    name = str(sm.get("name") or "")
    return {
        "order_no": o["order_no"], "status": o["status"],
        "status_label": STATUS_LABELS.get(o["status"], o["status"]),
        "steps": order_steps(o), "cancelled": o["status"] == "cancelled",
        "can_cancel": o["status"] == "new",
        "expected_delivery": o.get("expected_delivery"),
        "created_at": o.get("created_at"), "updated_at": o.get("updated_at"),
        "salesman": ({"name": name or "YQ Bahrain", "first_name": (name.split(" ")[0] if name else "YQ Bahrain"),
                      "whatsapp_url": wa, "email_url": mail} if (sm or wa) else None),
        "customer": {"name": o.get("customer_name"), "shop": o.get("customer_shop"), "area": o.get("customer_area")},
        "lines": [{"item_code": ln["item_code"], "display_name": ln.get("display_name"), "qty": ln["qty"],
                   "qty_confirmed": ln.get("qty_confirmed"), "line_status": ln.get("line_status") or "ok",
                   "unit_price_bhd": money(ln["unit_price_bhd"]), "line_total_bhd": money(ln["line_total_bhd"]),
                   "stock_status": ln.get("stock_status"), "backorder": bool(ln.get("backorder")),
                   "image_url": ln.get("image_url")} for ln in o.get("lines", [])],
        "subtotal_bhd": money(o.get("subtotal_bhd")), "discount_bhd": money(o.get("discount_bhd")),
        "delivery_bhd": money(o.get("delivery_bhd")), "total_bhd": money(o.get("total_bhd")),
        "total_confirmed_bhd": money(confirmed_total) if confirmed_total is not None else None,
        "has_changes": any((ln.get("line_status") or "ok") in ("changed", "removed") for ln in o.get("lines", [])),
        "has_backorder": bool(o.get("has_backorder")), "note": o.get("note"),
        "order_kind": o.get("order_kind") or "standard", "minimum_gap_bhd": o.get("minimum_gap_bhd"),
        "timeline": [{"ts": e.get("ts"), "event": e.get("event"),
                      "note": (e.get("detail") or {}).get("note") if isinstance(e.get("detail"), dict) else None}
                     for e in o.get("events", [])],
    }


def orders_by_tokens(tokens) -> list[dict]:
    """Summaries for the order tokens a device holds — 'My orders' without an account. A token is
    a capability for that one order, so this never lists anything the device did not create."""
    toks = [clean(t, 64) for t in (tokens or []) if isinstance(t, str) and len(clean(t, 64)) >= 16][:20]
    if not toks:
        return []
    rows = (get_client().table("shop_orders")
            .select("order_no,token,status,total_bhd,total_confirmed_bhd,items_count,units_count,created_at,"
                    "updated_at,salesman_name,expected_delivery,order_kind")
            .in_("token", toks).order("created_at", desc=True).execute().data or [])
    out = []
    for r in rows:
        total = r.get("total_confirmed_bhd") if r.get("total_confirmed_bhd") is not None else r.get("total_bhd")
        out.append({"order_no": r["order_no"], "token": r["token"], "status": r["status"],
                    "status_label": STATUS_LABELS.get(r["status"], r["status"]), "total_bhd": money(total),
                    "items": _i(r.get("items_count")), "units": _i(r.get("units_count")),
                    "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
                    "salesman": r.get("salesman_name"), "expected_delivery": r.get("expected_delivery"),
                    "can_cancel": r["status"] == "new",
                    "order_kind": r.get("order_kind") or "standard"})
    return out


def list_orders(status: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0,
                salesman_id: int | None = None) -> dict:
    qry = get_client().table("shop_orders").select(
        "id,order_no,status,customer_name,customer_phone,customer_shop,customer_area,salesman_id,"
        "salesman_name,total_bhd,items_count,units_count,has_backorder,created_at,updated_at,source,"
        "referral_code,coupon_code,placed_by,customer_id,attribution_source,attribution_conflict,"
        "expected_delivery,total_confirmed_bhd,order_kind,minimum_gap_bhd", count="exact")
    wanted = [s.strip().lower() for s in str(status or "").split(",") if s.strip().lower() in STATUSES]
    if len(wanted) == 1:
        qry = qry.eq("status", wanted[0])
    elif wanted:
        qry = qry.in_("status", wanted)
    if salesman_id is not None:
        qry = qry.eq("salesman_id", salesman_id)
    if q:
        s = clean(q, 60).replace("%", "").replace(",", " ")
        if s:
            qry = qry.or_(f"order_no.ilike.%{s}%,customer_name.ilike.%{s}%,customer_shop.ilike.%{s}%,"
                          f"customer_phone.ilike.%{s}%")
    res = qry.order("created_at", desc=True).range(offset, offset + max(1, min(limit, 200)) - 1).execute()
    return {"orders": res.data or [], "count": res.count if res.count is not None else len(res.data or []),
            "counts": status_counts(salesman_id)}


def status_counts(salesman_id: int | None = None) -> dict[str, int]:
    """Orders per status for the scope — feeds the New / In progress / Done buckets and the tab badge."""
    counts = {s: 0 for s in STATUSES}
    try:
        qry = get_client().table("shop_orders").select("status")
        if salesman_id is not None:
            qry = qry.eq("salesman_id", salesman_id)
        for r in qry.limit(10000).execute().data or []:
            if r.get("status") in counts:
                counts[r["status"]] += 1
    except Exception as e:  # noqa: BLE001
        log.debug("status counts failed: %s", e)
    return counts


def recent_customers(salesman_id: int | None = None, limit: int = 20) -> list[dict]:
    """A salesman's recent shops (one row per phone, latest first) for the checkout quick-pick."""
    qry = get_client().table("shop_orders").select(
        "customer_name,customer_phone,customer_shop,customer_area,customer_email,created_at")
    if salesman_id is not None:
        qry = qry.eq("salesman_id", salesman_id)
    rows = qry.order("created_at", desc=True).limit(500).execute().data or []
    seen: dict[str, dict] = {}
    for r in rows:
        key = r.get("customer_phone") or r.get("customer_name")
        if not key:
            continue
        if key in seen:
            seen[key]["orders"] += 1
            continue
        seen[key] = {"name": r.get("customer_name"), "phone": r.get("customer_phone"),
                     "shop": r.get("customer_shop"), "area": r.get("customer_area"),
                     "email": r.get("customer_email"), "orders": 1, "last_order_at": r.get("created_at")}
    return list(seen.values())[:max(1, min(limit, 100))]


def set_status(order_id: int, status: str, note: str | None, actor: str, *,
               allowed: tuple[str, ...] | None = None, cancelled_by: str = "staff") -> dict:
    """Move an order along the lifecycle. `allowed` narrows the statuses this actor's role may set
    (the storekeeper only moves goods). Each stage stamps its own timestamp; Preparing records
    which salesman the storekeeper issued the goods to."""
    o = get_order(order_id)
    if not o:
        raise ShopError("Order not found.")
    status = str(status or "").strip().lower()
    if status not in STATUSES:
        raise ShopError("Unknown status.")
    if allowed is not None and status not in allowed:
        raise ShopError("Your role cannot set this status.")
    if status not in NEXT_STATUS.get(o["status"], ()):
        raise ShopError(f"Cannot move an order from {o['status']} to {status}.")
    now = _iso()
    upd: dict = {"status": status, "updated_at": now}
    stamp = {"confirmed": "confirmed_at", "packed": "packed_at", "out_for_delivery": "out_for_delivery_at",
             "delivered": "delivered_at", "cancelled": "cancelled_at"}.get(status)
    if stamp:
        upd[stamp] = now
    if status == "packed" and o.get("salesman_id"):
        upd["issued_to_salesman_id"] = o["salesman_id"]      # who holds the goods from here on
        upd["issued_at"] = now
    if status == "confirmed" and o.get("total_confirmed_bhd") is None:
        upd["subtotal_confirmed_bhd"] = o.get("subtotal_bhd")
        upd["total_confirmed_bhd"] = o.get("total_bhd")
    if status == "cancelled":
        upd["cancelled_by"] = cancelled_by
        upd["cancel_reason"] = clean(note, 300) or None
    client = get_client()
    client.table("shop_orders").update(upd).eq("id", order_id).execute()
    client.table("shop_order_events").insert({
        "order_id": order_id, "actor": actor, "event": f"status:{status}",
        "detail": {"note": clean(note, 500) or None, "from": o["status"]}}).execute()
    return get_order(order_id) or {}


def cancel_by_customer(order_token: str, reason: str | None) -> dict:
    """The merchant may cancel while the order is still Received; after that the salesman has
    started work and a change goes through him."""
    o = get_order_by_token(order_token)
    if not o:
        raise ShopError("Order not found.")
    if o["status"] != "new":
        raise ShopError("This order is already being processed — please message your salesman to change it.")
    return set_status(o["id"], "cancelled", reason, actor="customer", cancelled_by="customer")


def confirm_order(order_id: int, changes, expected_delivery: str | None, note: str | None, actor: str) -> dict:
    """Confirm with changes: per-line confirmed quantities (or removal), an expected-delivery
    text, then a re-price at the confirmed quantities so tiers move with them and the margin
    floor still applies. Untouched lines are confirmed as ordered."""
    o = get_order(order_id)
    if not o:
        raise ShopError("Order not found.")
    if "confirmed" not in NEXT_STATUS.get(o["status"], ()):
        raise ShopError(f"Cannot confirm an order that is {o['status']}.")
    client = get_client()
    by_id = {int(ln["id"]): ln for ln in o["lines"]}
    changed: list[dict] = []
    removed: list[str] = []
    for ch in (changes or []):
        ln = by_id.get(_i((ch or {}).get("line_id")))
        if not ln:
            raise ShopError("Unknown order line.")
        st = str((ch or {}).get("line_status") or "").strip().lower()
        upd: dict = {"note": clean((ch or {}).get("note"), 200) or None}
        if st == "removed":
            upd.update(line_status="removed", qty_confirmed=0)
            removed.append(ln["item_code"])
        else:
            qc = _i((ch or {}).get("qty_confirmed"), _i(ln["qty"]))
            if qc <= 0:
                raise ShopError(f"Confirmed quantity for {ln['item_code']} must be positive — or remove the line.")
            if qc > MAX_QTY:
                raise ShopError(f"Confirmed quantity for {ln['item_code']} is too large.")
            upd.update(qty_confirmed=qc,
                       line_status=("changed" if qc != _i(ln["qty"]) else ("backorder" if ln.get("backorder") else "ok")))
            if qc != _i(ln["qty"]):
                changed.append({"item_code": ln["item_code"], "from": _i(ln["qty"]), "to": qc})
        client.table("shop_order_lines").update(upd).eq("id", ln["id"]).execute()
        ln.update(upd)
    for ln in o["lines"]:
        if ln.get("qty_confirmed") is None:
            client.table("shop_order_lines").update({"qty_confirmed": ln["qty"]}).eq("id", ln["id"]).execute()
            ln["qty_confirmed"] = ln["qty"]
    live = [{"item_code": ln["item_code"], "qty": _i(ln.get("qty_confirmed"))}
            for ln in o["lines"] if (ln.get("line_status") or "ok") != "removed" and _i(ln.get("qty_confirmed")) > 0]
    if not live:
        raise ShopError("Every line was removed — cancel the order instead.")
    totals = None
    try:
        q = price_cart(live, o.get("coupon_code"), o.get("referral_code"))
        totals = {k: v for k, v in q.items() if not k.startswith("_")}
    except ShopError as e:
        log.info("confirm re-price skipped for %s: %s", o.get("order_no"), e)
    now = _iso()
    upd_o: dict = {"status": "confirmed", "confirmed_at": now, "updated_at": now,
                   "expected_delivery": clean(expected_delivery, 120) or None,
                   "subtotal_confirmed_bhd": totals["subtotal_bhd"] if totals else o.get("subtotal_bhd"),
                   "total_confirmed_bhd": totals["total_bhd"] if totals else o.get("total_bhd")}
    client.table("shop_orders").update(upd_o).eq("id", order_id).execute()
    client.table("shop_order_events").insert({
        "order_id": order_id, "actor": actor, "event": "status:confirmed",
        "detail": {"note": clean(note, 500) or None, "from": o["status"], "changed": changed, "removed": removed,
                   "expected_delivery": upd_o["expected_delivery"]}}).execute()
    out = get_order(order_id) or {}
    out["totals"] = totals
    out["changed"] = changed
    out["removed"] = removed
    return out


def assign_order(order_id: int, salesman_id, actor: str, reason: str | None, *, is_admin: bool,
                 actor_salesman_id: int | None = None) -> dict:
    """Assign or reassign an order. Admins may move any open order; a salesman may only take an
    unassigned order, and only for himself. The first assignment also becomes the merchant's
    sticky rep when none is recorded yet."""
    o = get_order(order_id)
    if not o:
        raise ShopError("Order not found.")
    if o["status"] in ("delivered", "cancelled"):
        raise ShopError("Closed orders cannot be reassigned.")
    ctx = context()
    sm = _salesman_by(ctx, salesman_id) or _salesman_by_id(salesman_id)
    if not sm or not sm.get("is_active", True):
        raise ShopError("Unknown or inactive salesman.")
    if not is_admin:
        if o.get("salesman_id") or not actor_salesman_id or int(actor_salesman_id) != int(sm["id"]):
            raise ShopError("Only an admin can reassign an order.")
    now = _iso()
    client = get_client()
    client.table("shop_orders").update({
        "salesman_id": sm["id"], "salesman_name": sm["name"], "assigned_at": now, "assigned_by": actor,
        "attribution_conflict": False, "updated_at": now}).eq("id", order_id).execute()
    client.table("shop_order_events").insert({
        "order_id": order_id, "actor": actor, "event": "assigned",
        "detail": {"from": o.get("salesman_name"), "to": sm["name"], "reason": clean(reason, 300) or None}}).execute()
    if o.get("customer_id"):
        try:
            c = (client.table("shop_customers").select("salesman_id,sticky_salesman_id")
                 .eq("id", o["customer_id"]).limit(1).execute().data)
            if c and not c[0].get("salesman_id") and not c[0].get("sticky_salesman_id"):
                client.table("shop_customers").update({"sticky_salesman_id": sm["id"], "updated_at": now}) \
                    .eq("id", o["customer_id"]).execute()
        except Exception as e:  # noqa: BLE001
            log.debug("sticky update after assignment failed: %s", e)
    return get_order(order_id) or {}


def assignment_queue() -> dict:
    """Unassigned open orders with a suggested rep: the rep who served this phone before, else the
    rep with the most orders in the merchant's area over 90 days. No AI, just history."""
    ctx = context()
    client = get_client()
    rows = (client.table("shop_orders")
            .select("id,order_no,status,created_at,customer_shop,customer_area,customer_phone,customer_id,total_bhd,"
                    "units_count,items_count,session_ref,referral_code,src,attribution_source,attribution_conflict,"
                    "sla_notified_at")
            .is_("salesman_id", "null").in_("status", ["new", "confirmed"]).order("created_at")
            .limit(200).execute().data or [])
    since = (_now() - timedelta(days=90)).isoformat()
    out = []
    for r in rows:
        sugg_id, why = None, None
        phone = r.pop("customer_phone", None)
        prev = (client.table("shop_orders").select("salesman_id,salesman_name").eq("customer_phone", phone)
                .not_.is_("salesman_id", "null").order("created_at", desc=True).limit(1).execute().data
                if phone else [])
        if prev:
            sugg_id, why = prev[0]["salesman_id"], f"earlier orders from this number went to {prev[0]['salesman_name']}"
        elif r.get("customer_area"):
            area_rows = (client.table("shop_orders").select("salesman_id,salesman_name")
                         .ilike("customer_area", r["customer_area"]).gte("created_at", since)
                         .not_.is_("salesman_id", "null").limit(500).execute().data or [])
            tally: dict[int, list] = {}
            for a in area_rows:
                t = tally.setdefault(a["salesman_id"], [0, a.get("salesman_name")])
                t[0] += 1
            if tally:
                sugg_id = max(tally, key=lambda k: tally[k][0])
                why = f"{tally[sugg_id][1]} has the most orders in {r['customer_area']} (90 days)"
        age = _parse_ts(r.get("created_at"))
        r["age_min"] = int((_now() - age).total_seconds() // 60) if age else None
        r["suggested_salesman_id"], r["suggested_reason"] = sugg_id, why
        out.append(r)
    return {"orders": out, "count": len(out),
            "salesmen": [{"id": s["id"], "name": s["name"]} for s in ctx["salesmen"]],
            "sla_min": _i(ctx["settings"].get("shop_assign_sla_min"), 30)}


def picklist(salesman_id: int | None = None) -> dict:
    """The storekeeper's list: confirmed and preparing orders grouped by the salesman who will
    take the goods, with a total per item to pick. Quantities are the CONFIRMED ones."""
    client = get_client()
    q = (client.table("shop_orders")
         .select("id,order_no,status,customer_shop,customer_name,customer_area,salesman_id,salesman_name,"
                 "expected_delivery,confirmed_at,total_confirmed_bhd,total_bhd,units_count")
         .in_("status", ["confirmed", "packed"]).order("confirmed_at"))
    if salesman_id:
        q = q.eq("salesman_id", salesman_id)
    orders = q.limit(500).execute().data or []
    ids = [o["id"] for o in orders]
    lines: list[dict] = []
    for i in range(0, len(ids), 200):
        lines += (client.table("shop_order_lines").select("order_id,item_code,display_name,qty,qty_confirmed,line_status")
                  .in_("order_id", ids[i:i + 200]).execute().data or [])
    by_order: dict[int, list] = {}
    totals: dict[str, dict] = {}
    for ln in lines:
        if (ln.get("line_status") or "ok") == "removed":
            continue
        qty = _i(ln.get("qty_confirmed"), _i(ln.get("qty")))
        by_order.setdefault(ln["order_id"], []).append({"item_code": ln["item_code"], "display_name": ln.get("display_name"), "qty": qty})
        t = totals.setdefault(ln["item_code"], {"item_code": ln["item_code"], "display_name": ln.get("display_name"),
                                                "qty": 0, "orders": 0})
        t["qty"] += qty
        t["orders"] += 1
    groups: dict[str, dict] = {}
    for o in orders:
        key = o.get("salesman_name") or "Unassigned"
        g = groups.setdefault(key, {"salesman_id": o.get("salesman_id"), "salesman": key, "orders": []})
        g["orders"].append({**o, "status_label": STATUS_LABELS.get(o["status"], o["status"]),
                            "lines": by_order.get(o["id"], [])})
    return {"groups": sorted(groups.values(), key=lambda g: g["salesman"]),
            "totals_by_item": sorted(totals.values(), key=lambda t: -t["qty"]), "count": len(orders)}


# ── marketplace: the token-less front door ────────────────────────────────────

def market_enabled() -> bool:
    return _flag(shop_settings(), "shop_market_enabled")


def market_json(referral_code: str | None) -> bytes | None:
    """The public marketplace payload without a token in the URL: the current share token is
    resolved here, so a rotation never breaks the storefront or the pre-JS prefetch. None when
    the marketplace is switched off (shop_market_enabled)."""
    if not market_enabled():
        return None
    ctx = context()
    tok = ctx.get("share_token") or share_token(create=True)
    return public_catalog_json(tok, referral_code)


# ── salesmen admin ────────────────────────────────────────────────────────────

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return (s.split("-")[0] if s else "rep")[:32] or "rep"


def salesman_link(s: dict) -> str:
    """The rep's personal link. With the marketplace live (`shop_market_url` set) it is the
    storefront `/{slug}` — no token, safe to print on a QR card; otherwise the legacy token link."""
    market = str(shop_settings().get("shop_market_url") or "").strip().rstrip("/")
    if market and s.get("referral_code"):
        return f"{market}/{s['referral_code']}"
    tok = share_token() or ""
    base = _base_url()
    path = f"/c/{tok}?ref={s.get('referral_code')}"
    return f"{base}{path}" if base else path


def list_salesmen() -> list[dict]:
    rows = _load_salesmen(active_only=False)
    since = (_now() - timedelta(days=30)).isoformat()
    counts: dict[int, int] = {}
    try:
        for o in (get_client().table("shop_orders").select("salesman_id").gte("created_at", since)
                  .limit(5000).execute().data or []):
            if o.get("salesman_id"):
                counts[o["salesman_id"]] = counts.get(o["salesman_id"], 0) + 1
    except Exception:  # noqa: BLE001
        pass
    for s in rows:
        s["link"] = salesman_link(s)
        s["orders_30d"] = counts.get(s["id"], 0)
    return rows


def upsert_salesman(payload: dict, by: str = "", salesman_id: int | None = None) -> dict:
    client = get_client()
    fields: dict = {}
    if "name" in payload or salesman_id is None:
        name = clean(payload.get("name"), 80)
        if len(name) < 2:
            raise ShopError("Name is required.")
        fields["name"] = name
    for k, lim in (("phone", 32), ("email", 160), ("whatsapp", 32), ("user_email", 160), ("focus_name", 120),
                   ("title", 80), ("photo_url", 400)):
        if k in payload:
            v = clean(payload.get(k), lim) or None
            if k == "email" and v and not _EMAIL.match(v):
                raise ShopError("Invalid email.")
            if k in ("phone", "whatsapp") and v:
                d = phone_digits(v)
                if not d:
                    raise ShopError(f"Invalid {k} number.")
                v = d
            if k == "user_email" and v:
                v = v.lower()
            if k == "photo_url" and v and not v.startswith("https://"):
                raise ShopError("Photo URL must start with https://")
            fields[k] = v
    if "referral_code" in payload or salesman_id is None:
        code = clean(payload.get("referral_code"), 32).lower() or slugify(fields.get("name") or payload.get("name") or "")
        if not _SLUG.match(code):
            raise ShopError("Referral code: 2-32 chars, letters, numbers and dashes.")
        if is_reserved_slug(code, context()):
            raise ShopError("That referral code is a marketplace address (like /cart) — choose another.")
        fields["referral_code"] = code
    for k in ("is_active", "notify_email", "notify_whatsapp", "public_profile", "public_whatsapp"):
        if k in payload:
            fields[k] = bool(payload.get(k))
    if "sort_order" in payload:
        fields["sort_order"] = _i(payload.get("sort_order")) or None
    if salesman_id is None and not fields.get("whatsapp") and fields.get("phone"):
        fields["whatsapp"] = fields["phone"]
    fields["updated_at"] = _iso()
    try:
        if salesman_id is None:
            row = client.table("salesmen").insert(fields).execute().data[0]
        else:
            row = client.table("salesmen").update(fields).eq("id", salesman_id).execute().data[0]
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "salesmen_name_key" in msg or "salesmen_referral_code_key" in msg or "duplicate" in msg.lower():
            raise ShopError("A salesman with that name or referral code already exists.") from e
        raise
    invalidate()
    row["link"] = salesman_link(row)
    return row


def delete_salesman(salesman_id: int) -> None:
    get_client().table("salesmen").delete().eq("id", salesman_id).execute()
    invalidate()


# Every salesman request resolves the login to its salesmen row (scope, KPIs, link). The Today
# screen fires three of those at once, so remember the answer for a minute per login; every
# salesman edit path calls invalidate(), which also clears this.
_SALESMAN_TTL = 60.0
_salesman_cache: dict[str, tuple[float, dict | None]] = {}


def salesman_for_user(email: str | None) -> dict | None:
    if not email:
        return None
    key = email.strip().lower()
    hit = _salesman_cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    r = (get_client().table("salesmen").select("*").ilike("user_email", email.strip())
         .limit(1).execute().data or [])
    row = r[0] if r else None
    _salesman_cache[key] = (now + _SALESMAN_TTL, row)
    return row


# Owner decision 24-Sep-2026: the kickback base is EX-VAT (taxable) Accessories sales, net of
# Focus Sales Returns once a Sales Return register is loaded (never estimated), with the reached
# tier's rate applied to the whole month. `net_bhd` in v_sales = COALESCE(taxable_bhd, gross/1.1).
KICKBACK_BASIS = "net_ex_vat"
_BASIS_COLUMN = {"net_ex_vat": "net_bhd", "vat_incl_display": "revenue_bhd"}


def bahrain_today() -> date:
    """Today in Bahrain (UTC+3 all year, no DST) without depending on tzdata being installed."""
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()


def tier_progress(target: dict | None, mtd: float, data_date: str | None,
                  today: date | None = None, basis: str = KICKBACK_BASIS) -> dict | None:
    """Where a rep stands this month against the tiered kickback scheme (21-Sep-2026):
    tiers = monthly sales thresholds (target_bhd = Tier 1, tier2_bhd, tier3_bhd), each paying
    kickback_tN of the WHOLE month's sales once reached (owner confirmed 24-Sep-2026).
    Pure: no I/O, unit-tested in tests/test_shop.py.

    `mtd` is on `basis` (ex-VAT by default). `today` (Bahrain date) drives days_left: the month of
    the data can lag the calendar, and the countdown must follow the calendar, not the data.
    Returns None when the rep has no target row. `progress_pct` is against the TOP tier so the bar
    never sits at 100% before the last tier; `next_tier` is None once the top tier is reached.
    The figure is always an ESTIMATE until a statement is approved (is_estimate)."""
    from decimal import Decimal, ROUND_HALF_UP
    if not target:
        return None
    tiers: list[dict] = []
    for n, (tk, kk) in enumerate((("target_bhd", "kickback_t1"), ("tier2_bhd", "kickback_t2"),
                                  ("tier3_bhd", "kickback_t3")), start=1):
        thr = _f(target.get(tk))
        if thr > 0:
            tiers.append({"n": n, "bhd": money(thr), "pct": _f(target.get(kk))})
    if not tiers:
        return None
    mtd = max(0.0, _f(mtd))
    reached = [t for t in tiers if mtd >= t["bhd"]]
    ahead = [t for t in tiers if mtd < t["bhd"]]
    top = tiers[-1]["bhd"]
    nxt = ahead[0] if ahead else None
    days_left = None
    month = None
    data_age = None
    if data_date:
        try:
            import calendar
            d = date.fromisoformat(str(data_date)[:10])
            month = d.strftime("%Y-%m")
            if today is None:
                days_left = calendar.monthrange(d.year, d.month)[1] - d.day
            else:
                data_age = max(0, (today - d).days)
                if (today.year, today.month) == (d.year, d.month):
                    days_left = calendar.monthrange(today.year, today.month)[1] - today.day
                else:
                    days_left = 0            # the data's month is over; figures await the final load
        except Exception:  # noqa: BLE001
            pass
    rate = reached[-1]["pct"] if reached else 0.0
    kick = (Decimal(str(mtd)) * Decimal(str(rate))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return {
        "team": target.get("team") or "normal",
        "month": month, "data_through": str(data_date)[:10] if data_date else None, "days_left": days_left,
        "data_age_days": data_age,
        "basis": basis, "is_estimate": True, "returns_deducted": False,
        "mtd_bhd": money(mtd), "tiers": tiers,
        "tier_reached": reached[-1]["n"] if reached else 0,
        "kickback_pct": rate, "kickback_bhd": float(kick),
        "next_tier": ({"n": nxt["n"], "bhd": nxt["bhd"], "gap_bhd": money(nxt["bhd"] - mtd),
                       "pct": nxt["pct"]} if nxt else None),
        "progress_pct": round(min(100.0, mtd / top * 100), 1) if top else 0.0,
    }


def rep_month_sales(focus_name: str, basis: str = KICKBACK_BASIS, period: str | None = None) -> tuple[float, str | None]:
    """(Accessories sales on `basis`, last loaded sale date) for a rep's month: `period` 'YYYY-MM',
    default = the month of the latest loaded sale. SIM never counts (owner, 21-Sep); giveaways excluded."""
    col = _BASIS_COLUMN[basis]
    month_start = ("to_date($2 || '-01', 'YYYY-MM-DD')" if period
                   else "date_trunc('month', (SELECT MAX(sale_date) FROM v_sales))::date")
    params = [focus_name] + ([period] if period else [])
    rows = exec_sql_params(
        f"SELECT COALESCE(SUM({col}),0) AS amt, (SELECT MAX(sale_date) FROM v_sales)::text AS d "
        f"FROM v_sales WHERE sale_date >= {month_start} "
        f"AND sale_date < ({month_start} + interval '1 month')::date "
        "AND NOT is_giveaway AND division = 'Accessories' "
        "AND (salesman_resolved = $1 OR salesman_resolved LIKE $1 || ' - %')", params)
    r0 = (rows or [{}])[0]
    return _f(r0.get("amt")), r0.get("d")


def rep_target(focus_name: str, period: str | None) -> dict | None:
    rows = exec_sql_params(
        "SELECT salesman, period, team, target_bhd, tier2_bhd, tier3_bhd, "
        "kickback_t1, kickback_t2, kickback_t3 FROM salesman_targets "
        "WHERE salesman = $1 AND period IN ('', $2) ORDER BY period DESC LIMIT 1",
        [focus_name, str(period or "")[:7]])
    return (rows or [None])[0]


def me_payload(email: str) -> dict:
    sm = salesman_for_user(email)
    client = get_client()
    now = _now()
    d7, d30 = (now - timedelta(days=7)).isoformat(), (now - timedelta(days=30)).isoformat()
    q = client.table("shop_orders").select("id,total_bhd,customer_phone,created_at,status").gte("created_at", d30)
    if sm:
        q = q.eq("salesman_id", sm["id"])
    rows = q.limit(5000).execute().data or []
    live = [o for o in rows if o.get("status") != "cancelled"]
    kpis = {
        "orders_7d": sum(1 for o in live if str(o.get("created_at")) >= d7),
        "orders_30d": len(live),
        "value_30d_bhd": money(sum(_f(o.get("total_bhd")) for o in live)),
        "customers_30d": len({o.get("customer_phone") for o in live}),
    }
    focus = None
    if sm and sm.get("focus_name"):
        try:
            # Rep figures are MOBILE ACCESSORIES only (owner, 21-Sep-2026): Batelco SIM sales
            # never count towards a rep's revenue or target, so both queries filter the division.
            # Ex-VAT, like the kickback base (owner, 24-Sep-2026), so one screen never mixes bases.
            rev = exec_sql_params(
                "SELECT COALESCE(SUM(net_bhd),0) AS rev FROM v_sales "
                "WHERE sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90 AND division = 'Accessories' "
                "AND NOT is_giveaway "
                "AND (salesman_resolved = $1 OR salesman_resolved LIKE $1 || ' - %')", [sm["focus_name"]])
            focus = {"revenue_90d_bhd": money((rev or [{}])[0].get("rev")), "basis": KICKBACK_BASIS}
            # Tiered kickback: this month's accessories sales (month of the latest loaded sale,
            # giveaways excluded, EX-VAT) vs the rep's standing or month-specific target row.
            amt, data_date = rep_month_sales(sm["focus_name"])
            focus["target"] = tier_progress(rep_target(sm["focus_name"], str(data_date or "")[:7]), amt,
                                            data_date, today=bahrain_today())
        except Exception as e:  # noqa: BLE001
            log.warning("focus kpis failed for %s: %s", sm.get("focus_name"), e)
            focus = {"error": "Sales figures are unavailable right now; the office has been notified."}
    return {"salesman": sm, "link": salesman_link(sm) if sm else None,
            "qr_url": f"/shop/salesmen/{sm['id']}/qr.png" if sm else None, "kpis": kpis, "focus": focus}


def qr_png(url: str) -> bytes:
    import io

    import segno
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=8, border=2, dark="#1a1430", light="#ffffff")
    return buf.getvalue()


def unpriced_stock() -> list[dict]:
    return exec_sql("SELECT item_name, warehouse_name, stock_qty, value_bhd, matched_code, match_source, "
                    "as_of_date::text AS as_of_date FROM v_shop_unpriced_stock "
                    "ORDER BY value_bhd DESC NULLS LAST, stock_qty DESC") or []


# ── discount rules admin ──────────────────────────────────────────────────────

RULE_KINDS = ("qty_tier", "cart_value", "coupon", "bundle_price", "salesman_offer")
_COUPON = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,31}$")


def _rule_status(r: dict) -> str:
    now = _now()
    if not r.get("is_active"):
        return "inactive"
    st, en = _parse_ts(r.get("starts_at")), _parse_ts(r.get("ends_at"))
    if st and st > now:
        return "scheduled"
    if en and en < now:
        return "expired"
    if r.get("max_uses") is not None and _i(r.get("uses")) >= _i(r.get("max_uses")):
        return "exhausted"
    return "live"


def _norm_scope(scope) -> dict:
    if isinstance(scope, str):
        try:
            scope = json.loads(scope)
        except ValueError:
            scope = {}
    scope = scope or {}
    return {
        "item_codes": sorted({clean(c, 64).upper() for c in (scope.get("item_codes") or []) if clean(c, 64)}),
        "categories": sorted({clean(c, 64).upper() for c in (scope.get("categories") or []) if clean(c, 64)}),
        "referral_codes": sorted({clean(c, 32).lower() for c in (scope.get("referral_codes") or []) if clean(c, 32)}),
    }


def list_rules() -> list[dict]:
    rows = (get_client().table("discount_rules").select("*").order("priority").order("id")
            .execute().data or [])
    for r in rows:
        r["scope"] = _norm_scope(r.get("scope"))
        r["summary"] = rule_summary(r)
        r["status"] = _rule_status(r)
    return rows


# fields an edit may clear by sending null explicitly (the API passes explicitly-set keys through)
_NULLABLE = ("starts_at", "ends_at", "max_uses", "min_qty", "min_value_bhd", "coupon_code",
             "pct_off", "amount_off_bhd", "fixed_price_bhd")


def validate_rule(payload: dict, existing: dict | None = None) -> dict:
    """Return the DB row to write. Raises ShopError with a human message."""
    cur = dict(existing or {})
    cur.update({k: v for k, v in payload.items() if v is not None or k in _NULLABLE})
    name = clean(cur.get("name"), 80)
    if len(name) < 2:
        raise ShopError("Give the rule a name.")
    kind = str(cur.get("kind") or "").strip().lower()
    if kind not in RULE_KINDS:
        raise ShopError("Unknown rule kind.")
    pct, amt, fixed = cur.get("pct_off"), cur.get("amount_off_bhd"), cur.get("fixed_price_bhd")
    blank = (None, "", 0, "0")
    given = [v for v in (pct, amt, fixed) if v not in blank]
    if len(given) != 1:
        raise ShopError("Set exactly one of: % off, BHD off, or fixed price.")
    if pct not in blank and not (0 < _f(pct) <= 100):
        raise ShopError("% off must be between 0 and 100.")
    if amt not in blank and _f(amt) <= 0:
        raise ShopError("BHD off must be positive.")
    if fixed not in blank and _f(fixed) <= 0:
        raise ShopError("Fixed price must be positive.")
    scope = _norm_scope(cur.get("scope"))
    row: dict = {
        "name": name, "kind": kind, "scope": scope,
        "pct_off": money(pct) if pct not in blank else None,
        "amount_off_bhd": money(amt) if amt not in blank else None,
        "fixed_price_bhd": money(fixed) if fixed not in blank else None,
        "min_qty": _i(cur.get("min_qty")) or None,
        "min_value_bhd": money(cur.get("min_value_bhd")) if cur.get("min_value_bhd") not in (None, "") else None,
        "stackable": bool(cur.get("stackable")),
        "priority": _i(cur.get("priority"), 100),
        "is_active": bool(cur.get("is_active", True)),
        "max_uses": _i(cur.get("max_uses")) or None,
        "coupon_code": None, "starts_at": None, "ends_at": None,
    }
    if kind == "qty_tier" and (row["min_qty"] or 0) < 2:
        raise ShopError("A quantity tier needs a minimum quantity of 2 or more.")
    if kind == "cart_value" and not row["min_value_bhd"]:
        raise ShopError("A cart-value discount needs a minimum order value.")
    if kind == "coupon":
        code = clean(cur.get("coupon_code"), 32).upper()
        if not _COUPON.match(code):
            raise ShopError("Coupon code: 3-32 letters, numbers or dashes.")
        row["coupon_code"] = code
    if kind == "salesman_offer" and not scope["referral_codes"]:
        raise ShopError("A salesman offer needs at least one referral code in its scope.")
    if kind == "bundle_price" and not scope["item_codes"]:
        raise ShopError("A bundle price needs the item codes it applies to.")
    for k in ("starts_at", "ends_at"):
        v = cur.get(k)
        if v not in (None, ""):
            d = _parse_ts(v)
            if not d:
                raise ShopError(f"Bad date for {k.replace('_', ' ')}.")
            row[k] = d.isoformat()
    if row["starts_at"] and row["ends_at"] and row["starts_at"] > row["ends_at"]:
        raise ShopError("The offer ends before it starts.")
    return row


def rule_impact(row: dict, ctx: dict | None = None) -> dict:
    """How many items a rule touches and which would be clamped by the margin floor."""
    ctx = ctx or context()
    r = dict(row)
    r["scope"] = _norm_scope(r.get("scope"))
    touched, breaches = [], []
    for code in ctx["order"]:
        it = ctx["items"][code]
        if r["kind"] in ("qty_tier", "bundle_price", "salesman_offer", "cart_value") and _rule_matches_item(r, it):
            touched.append(code)
            lp = _f(it.get("standard_rate"))
            if lp <= 0:
                continue
            unit = _discounted_unit(r, lp)
            floor = _floor_for(ctx, code)
            if floor is not None and unit < floor:
                breaches.append({"item_code": code, "unit_bhd": money(unit), "floor_bhd": floor})
    return {"items": len(touched), "breaches": breaches[:50], "breach_count": len(breaches)}


def upsert_rule(payload: dict, by: str = "", rule_id: int | None = None) -> dict:
    client = get_client()
    existing = None
    if rule_id is not None:
        got = client.table("discount_rules").select("*").eq("id", rule_id).limit(1).execute().data
        if not got:
            raise ShopError("Rule not found.")
        existing = got[0]
    row = validate_rule(payload, existing)
    row["updated_at"] = _iso()
    try:
        if rule_id is None:
            row["created_by"] = by
            out = client.table("discount_rules").insert(row).execute().data[0]
        else:
            out = client.table("discount_rules").update(row).eq("id", rule_id).execute().data[0]
    except Exception as e:  # noqa: BLE001
        if "coupon_code" in str(e) or "duplicate" in str(e).lower():
            raise ShopError("That coupon code is already used by another rule.") from e
        raise
    invalidate()
    out["scope"] = _norm_scope(out.get("scope"))
    out["summary"] = rule_summary(out)
    out["status"] = _rule_status(out)
    out["impact"] = rule_impact(out)
    return out


# ── campaigns (admin) ─────────────────────────────────────────────────────────

def list_campaigns() -> list[dict]:
    rows = (get_client().table("shop_campaigns").select("*").order("sort_order").order("id")
            .execute().data or [])
    now = _now()
    for r in rows:
        st, en = _parse_ts(r.get("starts_at")), _parse_ts(r.get("ends_at"))
        r["status"] = ("paused" if not r.get("is_active") else "scheduled" if st and st > now
                       else "ended" if en and en < now else "live")
    return rows


_CAMPAIGN_NULLABLE = ("title_ar", "line", "line_ar", "image_url", "cta_label", "cta_label_ar", "category",
                      "rule_id", "sponsor_name", "starts_at", "ends_at", "image_url_600", "product_codes")


def _campaign_codes(raw) -> list[str] | None:
    """A composed creative's item codes as the admin typed them: trimmed at the ends only (codes such
    as "P05 1Mtr" or "UK20 (New)" keep their case and inner spaces), blanks dropped, duplicates
    dropped, at most CAMPAIGN_MAX_PRODUCTS. None = no composition (an uploaded photo or plain text)."""
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raise ShopError("Products must be a list of item codes.")
    out: list[str] = []
    for c in raw:
        if not isinstance(c, str):
            raise ShopError("Products must be a list of item codes.")
        code = c.strip()
        if len(code) > 64:
            raise ShopError(f"'{code[:24]}…' is not an item code.")
        if code and code.upper() not in {x.upper() for x in out}:
            out.append(code)
    if len(out) > CAMPAIGN_MAX_PRODUCTS:
        raise ShopError(f"Pick at most {CAMPAIGN_MAX_PRODUCTS} products for a composed creative.")
    return out or None


def validate_campaign(payload: dict, existing: dict | None = None) -> dict:
    cur = dict(existing or {})
    cur.update({k: v for k, v in payload.items() if v is not None or k in _CAMPAIGN_NULLABLE})
    title = str(cur.get("title") or "").strip()
    if len(title) < 3:
        raise ShopError("Give the campaign a title (3+ characters).")
    to = str(cur.get("cta_to") or "/shop").strip()
    if not (to.startswith("/") or to.startswith("https://")):
        raise ShopError("The link must be a marketplace path (/shop?f=clearance) or an https URL.")
    placement = [str(x) for x in (cur.get("placement") or ["strip"]) if str(x) in CAMPAIGN_PLACEMENTS]
    if not placement:
        raise ShopError("Pick at least one placement.")
    audience = cur.get("audience") or "all"
    if audience not in CAMPAIGN_AUDIENCES:
        raise ShopError("Audience must be all, recognized or new.")
    image_fit = cur.get("image_fit") or CAMPAIGN_IMAGE_FITS[0]
    if image_fit not in CAMPAIGN_IMAGE_FITS:
        raise ShopError("Image fit must be contain or cover.")
    canvas = cur.get("canvas") or CAMPAIGN_CANVASES[0]
    if canvas not in CAMPAIGN_CANVASES:
        raise ShopError("Canvas must be lilac, apricot, mint, plum or night.")
    product_codes = _campaign_codes(cur.get("product_codes"))
    st, en = _parse_ts(cur.get("starts_at")), _parse_ts(cur.get("ends_at"))
    if st and en and en <= st:
        raise ShopError("The end must be after the start.")
    if cur.get("sponsored") and not str(cur.get("sponsor_name") or "").strip():
        raise ShopError("A sponsored campaign needs the sponsor's name — it is shown to merchants.")
    if "category" in placement and not str(cur.get("category") or "").strip():
        raise ShopError("A category placement needs the category.")
    row = {
        "title": title[:80], "title_ar": (cur.get("title_ar") or None),
        "line": (cur.get("line") or None), "line_ar": (cur.get("line_ar") or None),
        "image_url": (cur.get("image_url") or None),
        "image_url_600": (str(cur.get("image_url_600") or "").strip() or None),
        "image_fit": image_fit, "product_codes": product_codes, "canvas": canvas,
        "cta_label": (cur.get("cta_label") or None), "cta_label_ar": (cur.get("cta_label_ar") or None),
        "cta_to": to[:300], "placement": placement,
        "category": (str(cur.get("category") or "").strip().upper() or None),
        "audience": audience, "rule_id": cur.get("rule_id") or None,
        "sponsored": bool(cur.get("sponsored")), "sponsor_name": (str(cur.get("sponsor_name") or "").strip() or None),
        "starts_at": st.isoformat() if st else None, "ends_at": en.isoformat() if en else None,
        "is_active": bool(cur.get("is_active", True)), "sort_order": int(cur.get("sort_order") or 100),
    }
    for k in ("title_ar", "line", "line_ar", "cta_label", "cta_label_ar"):
        if row[k]:
            row[k] = str(row[k]).strip()[:160] or None
    return row


def upsert_campaign(payload: dict, by: str = "", campaign_id: int | None = None) -> dict:
    client = get_client()
    existing = None
    if campaign_id is not None:
        got = client.table("shop_campaigns").select("*").eq("id", campaign_id).limit(1).execute().data
        if not got:
            raise ShopError("Campaign not found.")
        existing = got[0]
    row = validate_campaign(payload, existing)
    row["updated_at"] = _iso()
    if campaign_id is None:
        row["created_by"] = by
        out = client.table("shop_campaigns").insert(row).execute().data[0]
    else:
        out = client.table("shop_campaigns").update(row).eq("id", campaign_id).execute().data[0]
    invalidate()
    return out


def delete_campaign(campaign_id: int) -> None:
    get_client().table("shop_campaigns").delete().eq("id", campaign_id).execute()
    invalidate()


# ── restock requests ("tell me when back") ────────────────────────────────────

def add_restock(item_code: str, phone: str | None, device_id: str | None, referral_code: str | None) -> bool:
    ctx = context()
    code = resolve_code(ctx, item_code)
    if not code:
        return False
    row = {"item_code": code, "phone": (phone or "").strip()[:32] or None,
           "device_id": (device_id or "").strip()[:64] or None,
           "referral_code": (referral_code or "").strip().lower()[:32] or None}
    try:
        get_client().table("shop_restock_requests").insert(row).execute()
    except Exception as e:  # noqa: BLE001 — the unique index makes a repeat a no-op
        if "duplicate" not in str(e).lower() and "unique" not in str(e).lower():
            log.warning("restock insert failed: %s", e)
            return False
    return True


def list_restock(referral_code: str | None = None) -> list[dict]:
    """Open requests, one row per product, newest first — with the live stock so the rep sees
    which ones came back. Scoped to a rep's referral code when given."""
    q = get_client().table("shop_restock_requests").select("*").is_("notified_at", "null")
    if referral_code:
        q = q.eq("referral_code", referral_code.lower())
    rows = q.order("created_at", desc=True).limit(500).execute().data or []
    ctx = context()
    by: dict[str, dict] = {}
    for r in rows:
        g = by.setdefault(r["item_code"], {"item_code": r["item_code"], "count": 0, "phones": [], "first_at": r["created_at"], "ids": []})
        g["count"] += 1
        g["ids"].append(r["id"])
        if r.get("phone") and r["phone"] not in g["phones"]:
            g["phones"].append(r["phone"])
        g["first_at"] = min(g["first_at"], r["created_at"])
    out = []
    for code, g in by.items():
        it = ctx["items"].get(code) or {}
        g["display_name"] = it.get("display_name") or code
        g["stock_qty"] = it.get("stock_qty")
        g["back_in_stock"] = bool((it.get("stock_qty") or 0) > 0)
        out.append(g)
    out.sort(key=lambda g: (not g["back_in_stock"], g["first_at"]))
    return out


def resolve_restock(ids: list[int]) -> int:
    if not ids:
        return 0
    get_client().table("shop_restock_requests").update({"notified_at": _iso()}).in_("id", ids[:200]).execute()
    return len(ids[:200])


def delete_rule(rule_id: int) -> None:
    get_client().table("discount_rules").delete().eq("id", rule_id).execute()
    invalidate()


# ── share preview (Open Graph + JSON-LD) ──────────────────────────────────────

def share_item(item_code: str) -> dict | None:
    ctx = context()
    it = ctx["items"].get(resolve_code(ctx, item_code) or "")
    if not it:
        return None
    low_units = _i(ctx["settings"].get("shop_low_stock_units"), 10)
    status = stock_status_for(it.get("stock_qty"), low_units)
    price = money(it.get("standard_rate")) if it.get("standard_rate") is not None else None
    return {"item_code": it["item_code"], "spec": it.get("spec") or it.get("display_name") or it["item_code"],
            "category": it.get("category"), "price_bhd": price,
            "image": it.get("product_image_url"), "stock_status": status}


# ── margin & price health (replaces the hand-built landed-cost workbook) ─────

def margin_health() -> dict:
    ctx = context()
    vals = ctx["settings"]
    vat = _f(vals.get("shop_vat_rate"), 0.0)
    min_margin = _f(vals.get("shop_min_margin_pct"), 0.2)
    low_units = _i(vals.get("shop_low_stock_units"), 10)
    rows = []
    for code in ctx["order"]:
        it = ctx["items"][code]
        lp = it.get("standard_rate")
        cost = ctx["costs"].get(code)
        ex_vat = money(_f(lp) / (1 + vat)) if lp is not None else None
        profit = money(ex_vat - cost) if (ex_vat is not None and cost) else None
        margin = round(profit / ex_vat, 4) if (profit is not None and ex_vat) else None
        markup = round(profit / cost, 4) if (profit is not None and cost) else None
        rows.append({
            "item_code": code, "spec": it.get("spec"), "category": it.get("category"),
            "price_incl_vat_bhd": money(lp) if lp is not None else None, "price_ex_vat_bhd": ex_vat,
            "landed_cost_bhd": money(cost) if cost else None, "profit_bhd": profit,
            "margin_pct": margin, "markup_pct": markup, "floor_bhd": _floor_for(ctx, code),
            "stock_status": stock_status_for(it.get("stock_qty"), low_units),
            "sold_90d": _i(it.get("sold_90d")),
            "status": ("no_cost" if not cost else "no_price" if lp is None else
                       "below_floor" if (margin is not None and margin < min_margin) else "ok"),
        })
    rows.sort(key=lambda r: (r["status"] != "below_floor", r["margin_pct"] if r["margin_pct"] is not None else 9))
    return {"rows": rows, "summary": {
        "items": len(rows), "with_cost": sum(1 for r in rows if r["landed_cost_bhd"]),
        "below_floor": sum(1 for r in rows if r["status"] == "below_floor"),
        "vat_rate": vat, "min_margin_pct": min_margin}}


# ── analytics (funnel, AOV, top products, salesman leaderboard, attribution) ──

def analytics(days: int = 30, salesman: dict | None = None) -> dict:
    """Shop analytics for the last N days. When `salesman` is given, scope to that salesman
    (orders by salesman_id, funnel events by their referral code)."""
    days = max(1, min(_i(days, 30), 365))
    since = (_now() - timedelta(days=days)).isoformat()
    client = get_client()
    ev = (client.table("shop_events").select("ts,event,session_id,item_code,referral_code,salesman_id,src,device_id,meta")
          .gte("ts", since).limit(20000).execute().data or [])
    orders = (client.table("shop_orders")
              .select("id,status,total_bhd,units_count,salesman_id,salesman_name,referral_code,src,coupon_code,"
                      "customer_phone,created_at,has_backorder,source,attribution_source,attribution_conflict,"
                      "assigned_at,confirmed_at,cancelled_by,customer_id,device_id")
              .gte("created_at", since).limit(5000).execute().data or [])
    if salesman:
        code = str(salesman.get("referral_code") or "").lower()
        ev = [e for e in ev if (e.get("salesman_id") == salesman["id"]) or
              (code and str(e.get("referral_code") or "").lower() == code)]
        orders = [o for o in orders if o.get("salesman_id") == salesman["id"]]
    live = [o for o in orders if o.get("status") != "cancelled"]
    lines: list[dict] = []
    ids = [o["id"] for o in live]
    for i in range(0, len(ids), 200):
        lines += (client.table("shop_order_lines").select("order_id,item_code,display_name,qty,line_total_bhd")
                  .in_("order_id", ids[i:i + 200]).execute().data or [])

    def _sessions(kind: str) -> int:
        return len({e.get("session_id") or f"anon-{e.get('ts')}" for e in ev if e.get("event") == kind})

    sessions = _sessions("view")
    n = len(live)
    value = money(sum(_f(o.get("total_bhd")) for o in live))
    funnel = {"sessions": sessions, "item_views": _sessions("item"), "adds": _sessions("add"),
              "checkouts": _sessions("checkout"), "orders": n,
              "conversion_pct": round(n / sessions * 100, 1) if sessions else None}
    prod: dict[str, dict] = {}
    for ln in lines:
        p = prod.setdefault(ln["item_code"], {"item_code": ln["item_code"], "display_name": ln.get("display_name"),
                                              "units": 0, "value_bhd": 0.0, "orders": 0})
        p["units"] += _i(ln.get("qty"))
        p["value_bhd"] = money(p["value_bhd"] + _f(ln.get("line_total_bhd")))
        p["orders"] += 1
    top_products = sorted(prod.values(), key=lambda p: (-p["value_bhd"], -p["units"]))[:15]
    reps: dict[str, dict] = {}
    for o in live:
        r = reps.setdefault(o.get("salesman_name") or "Unassigned",
                            {"salesman": o.get("salesman_name") or "Unassigned", "orders": 0, "value_bhd": 0.0,
                             "customers": set()})
        r["orders"] += 1
        r["value_bhd"] = money(r["value_bhd"] + _f(o.get("total_bhd")))
        r["customers"].add(o.get("customer_phone"))
    leaderboard = sorted(({**r, "customers": len(r["customers"]),
                           "aov_bhd": money(r["value_bhd"] / r["orders"]) if r["orders"] else 0.0}
                          for r in reps.values()), key=lambda r: -r["value_bhd"])
    by_ref: dict[str, dict] = {}
    for o in live:
        key = (f"salesman:{o.get('salesman_name') or 'staff'}" if o.get("source") == "salesman"
               else o.get("referral_code") or ("dropdown" if o.get("source") == "dropdown" else "direct"))
        a = by_ref.setdefault(key, {"key": key, "orders": 0, "value_bhd": 0.0})
        a["orders"] += 1
        a["value_bhd"] = money(a["value_bhd"] + _f(o.get("total_bhd")))
    by_src: dict[str, dict] = {}
    for o in live:
        key = o.get("src") or "direct"
        a = by_src.setdefault(key, {"key": key, "orders": 0, "value_bhd": 0.0})
        a["orders"] += 1
        a["value_bhd"] = money(a["value_bhd"] + _f(o.get("total_bhd")))
    coupons: dict[str, dict] = {}
    for o in live:
        if o.get("coupon_code"):
            c = coupons.setdefault(o["coupon_code"], {"key": o["coupon_code"], "orders": 0, "value_bhd": 0.0})
            c["orders"] += 1
            c["value_bhd"] = money(c["value_bhd"] + _f(o.get("total_bhd")))
    daily: dict[str, dict] = {}
    for o in live:
        d = str(o.get("created_at") or "")[:10]
        x = daily.setdefault(d, {"date": d, "orders": 0, "value_bhd": 0.0})
        x["orders"] += 1
        x["value_bhd"] = money(x["value_bhd"] + _f(o.get("total_bhd")))

    # ── the marketplace learning loop (plan §R): what merchants search for, which rails they use,
    # where orders are attributed, how fast they are taken, who comes back, how the site performs.
    def _meta(e: dict) -> dict:
        m = e.get("meta")
        return m if isinstance(m, dict) else {}

    def _ts(v) -> datetime | None:
        if not v:
            return None
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    terms: dict[str, dict] = {}
    for e in ev:
        if e.get("event") in ("search", "search_zero"):
            q = str(_meta(e).get("q") or "").strip().lower()[:60]
            if q:
                t = terms.setdefault(q, {"term": q, "searches": 0, "zero": 0})
                t["searches"] += 1
                t["zero"] += 1 if e.get("event") == "search_zero" else 0
    n_search = sum(t["searches"] for t in terms.values())
    n_zero = sum(t["zero"] for t in terms.values())
    search = {"searches": n_search, "zero_results": n_zero,
              "zero_rate_pct": round(n_zero / n_search * 100, 1) if n_search else None,
              "terms": sorted(terms.values(), key=lambda t: (-t["searches"], -t["zero"]))[:20],
              "zero_terms": sorted((t for t in terms.values() if t["zero"]), key=lambda t: -t["zero"])[:10]}

    rails: dict[str, dict] = {}
    for e in ev:
        if e.get("event") in ("rail_click", "reco_click", "reorder"):
            rail = str(_meta(e).get("rail") or ("reorder" if e.get("event") == "reorder" else "unknown"))[:40]
            r = rails.setdefault(rail, {"rail": rail, "clicks": 0, "sessions": set()})
            r["clicks"] += 1
            r["sessions"].add(e.get("session_id"))
    rail_perf = sorted(({**r, "sessions": len(r["sessions"])} for r in rails.values()), key=lambda r: -r["clicks"])
    engagement = {k: _sessions(k) for k in ("search", "share", "install", "reorder", "cancel", "checkout_start")}
    engagement["devices"] = len({e.get("device_id") for e in ev if e.get("device_id")})

    by_attr: dict[str, dict] = {}
    for o in live:
        key = o.get("attribution_source") or ("staff" if o.get("source") == "salesman" else "legacy")
        a = by_attr.setdefault(key, {"key": key, "orders": 0, "value_bhd": 0.0})
        a["orders"] += 1
        a["value_bhd"] = money(a["value_bhd"] + _f(o.get("total_bhd")))
    sla_min = _i(shop_settings().get("shop_assign_sla_min"), 30)
    now = _now()
    breaches = 0
    for o in live:
        created = _ts(o.get("created_at"))
        if not created:
            continue
        assigned = _ts(o.get("assigned_at"))
        if o.get("salesman_id") and not assigned:
            continue  # attributed at creation: nobody waited
        waited_min = ((assigned or now) - created).total_seconds() / 60
        if waited_min > sla_min:
            breaches += 1
    confirm_mins = sorted((_ts(o["confirmed_at"]) - _ts(o["created_at"])).total_seconds() / 60
                          for o in live if _ts(o.get("confirmed_at")) and _ts(o.get("created_at")))
    cancelled = [o for o in orders if o.get("status") == "cancelled"]
    ops = {"by_attribution": sorted(by_attr.values(), key=lambda a: -a["value_bhd"]),
           "unassigned_now": sum(1 for o in live if not o.get("salesman_id") and o.get("status") in ("new", "confirmed")),
           "conflicts": sum(1 for o in live if o.get("attribution_conflict")),
           "sla_min": sla_min, "sla_breaches": breaches,
           "median_time_to_confirm_min": round(confirm_mins[len(confirm_mins) // 2], 1) if confirm_mins else None,
           "cancelled_by_customer": sum(1 for o in cancelled if o.get("cancelled_by") == "customer"),
           "cancelled_by_staff": sum(1 for o in cancelled if o.get("cancelled_by") != "customer")}

    per_customer: dict = {}
    for o in live:
        k = o.get("customer_id") or o.get("customer_phone")
        per_customer[k] = per_customer.get(k, 0) + 1
    repeat = sum(1 for c in per_customer.values() if c >= 2)
    identity = {"customers": len(per_customer), "repeat_customers": repeat,
                "repeat_rate_pct": round(repeat / len(per_customer) * 100, 1) if per_customer else None,
                "market_orders": sum(1 for o in live if o.get("source") in ("market", "slug")),
                "staff_orders": sum(1 for o in live if o.get("source") == "salesman"),
                "legacy_orders": sum(1 for o in live if o.get("source") not in ("market", "slug", "salesman"))}

    def _p75(key: str):
        vals = sorted(_f(_meta(e).get(key)) for e in ev if e.get("event") == "vitals" and _meta(e).get(key) is not None)
        return round(vals[min(len(vals) - 1, int(len(vals) * 0.75))], 3) if vals else None
    vitals = {"samples": sum(1 for e in ev if e.get("event") == "vitals"),
              "lcp_ms_p75": _p75("lcp"), "inp_ms_p75": _p75("inp"), "cls_p75": _p75("cls")}
    return {
        "search": search, "rails": rail_perf, "engagement": engagement, "ops": ops, "identity": identity, "vitals": vitals,
        "days": days, "since": since[:10],
        "funnel": funnel,
        "orders": n, "cancelled": len(orders) - n, "value_bhd": value,
        "aov_bhd": money(value / n) if n else 0.0,
        "units": sum(_i(o.get("units_count")) for o in live),
        "customers": len({o.get("customer_phone") for o in live}),
        "backorder_rate_pct": round(sum(1 for o in live if o.get("has_backorder")) / n * 100, 1) if n else 0.0,
        "top_products": top_products,
        "leaderboard": leaderboard,
        "attribution": {"by_referral": sorted(by_ref.values(), key=lambda a: -a["value_bhd"]),
                        "by_src": sorted(by_src.values(), key=lambda a: -a["value_bhd"]),
                        "by_coupon": sorted(coupons.values(), key=lambda a: -a["value_bhd"])},
        "daily": sorted(daily.values(), key=lambda x: x["date"]),
    }
