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
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.catalog import CATEGORY_ORDER, public_url, share_token, thumb_path
from app.config import settings as cfg
from app.database import get_client
from app.db_read import exec_sql, exec_sql_params

log = logging.getLogger(__name__)

STOCK_IN, STOCK_LOW, STOCK_OUT = "in_stock", "low_stock", "out_of_stock"
STATUSES = ("new", "confirmed", "packed", "delivered", "cancelled")
NEXT_STATUS: dict[str, tuple[str, ...]] = {
    "new": ("confirmed", "cancelled"),
    "confirmed": ("packed", "delivered", "cancelled"),
    "packed": ("delivered", "cancelled"),
    "delivered": (),
    "cancelled": (),
}
SOURCES = ("referral", "dropdown", "default")
EVENTS = ("view", "item", "add", "checkout", "order")
MAX_LINES = 60
MAX_QTY = 9999

# Mirrors the seeds in scripts/shop_migration.sql — defaults only; the DB value wins.
SETTING_DEFAULTS: dict[str, str] = {
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


def update_shop_settings(changes: dict[str, str], by: str = "") -> dict[str, str]:
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

_ctx_cache: dict = {"at": 0.0, "ctx": None}


def invalidate() -> None:
    """Forget the cached catalog context (called after refresh, photo upload, rule/salesman edits)."""
    _ctx_cache.update(at=0.0, ctx=None)
    _settings_cache.update(at=0.0, vals=None)


def _load_items() -> list[dict]:
    return exec_sql(
        "SELECT c.item_code, c.display_name, c.spec, c.category, c.brand, c.standard_rate, c.b2c_rate, "
        "c.product_image_url, c.package_image_url, c.sort_order, c.created_at::text AS created_at, "
        "ci.moq, ci.pack_size, s.stock_qty, s.as_of_date::text AS stock_as_of, "
        "v.sold_30d, v.prev_30d, v.sold_90d, v.customers_30d "
        "FROM v_catalog c "
        "JOIN catalog_items ci ON ci.item_code = c.item_code "
        "LEFT JOIN v_catalog_stock s ON s.item_code = c.item_code "
        "LEFT JOIN v_catalog_velocity v ON v.item_code = c.item_code "
        "WHERE c.is_active "
        "ORDER BY c.category, c.sort_order NULLS LAST, c.item_code"
    ) or []


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


def _load_salesmen(active_only: bool = True) -> list[dict]:
    try:
        q = get_client().table("salesmen").select("*")
        if active_only:
            q = q.eq("is_active", True)
        return q.order("sort_order", nullsfirst=False).order("name").execute().data or []
    except Exception as e:  # noqa: BLE001
        log.warning("salesmen unavailable: %s", e)
        return []


def context(force: bool = False) -> dict:
    now = time.time()
    if not force and _ctx_cache["ctx"] is not None and now - _ctx_cache["at"] < _TTL:
        return _ctx_cache["ctx"]
    vals = shop_settings(force=force)
    items = _load_items()
    ctx = {
        "settings": vals,
        "items": {str(r["item_code"]): r for r in items},
        "order": [str(r["item_code"]) for r in items],
        "costs": _load_costs(),
        "rules": _load_rules(),
        "salesmen": _load_salesmen(),
        "loaded_at": _iso(),
    }
    ctx["pairs"] = _load_pairs(set(ctx["order"]))
    _ctx_cache.update(at=now, ctx=ctx)
    return ctx


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
    return out


def _thumb(item: dict) -> str | None:
    return public_url(thumb_path(str(item["item_code"]), "product")) if item.get("product_image_url") else None


def resolve_ref(ctx: dict, referral_code: str | None) -> dict | None:
    code = (referral_code or "").strip().lower()
    if not code:
        return None
    for s in ctx["salesmen"]:
        if str(s.get("referral_code") or "").lower() == code:
            return {"referral_code": code, "salesman_id": s["id"], "salesman_name": s["name"]}
    return None


def catalog_payload(token: str, referral_code: str | None = None) -> dict | None:
    """The public catalog. Backward compatible with the pre-shop payload. None = bad token."""
    good = share_token(create=False)
    if not good or not secrets.compare_digest(token, good):
        return None
    ctx = context()
    vals = ctx["settings"]
    low_units = _i(vals.get("shop_low_stock_units"), 10)
    proof_min = _i(vals.get("shop_social_proof_min_customers"), 5)
    show_compare = _flag(vals, "shop_show_retail_compare")
    badges = _badges(ctx)
    items = []
    stock_as_of = None
    for code in ctx["order"]:
        it = ctx["items"][code]
        lp = it.get("standard_rate")
        b2c = it.get("b2c_rate")
        compare = money(b2c) if (show_compare and lp is not None and b2c is not None and _f(b2c) > _f(lp)) else None
        save_pct = int(round((1 - _f(lp) / _f(b2c)) * 100)) if compare else None
        cust = _i(it.get("customers_30d"))
        stock_as_of = stock_as_of or it.get("stock_as_of")
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
            "product_image_url": it.get("product_image_url"),
            "package_image_url": it.get("package_image_url"),
            "thumb_url": _thumb(it),
            "stock_status": stock_status_for(it.get("stock_qty"), low_units),
            "moq": max(_i(it.get("moq"), 1), 1),
            "pack_size": _i(it.get("pack_size")) or None,
            "tiers": item_tiers(ctx, it),
            "badges": badges.get(code, []),
            "social_proof": f"Ordered by {cust} shops this month" if cust >= proof_min else None,
        })
    cats = sorted({i.get("category") or "OTHER" for i in items},
                  key=lambda c: (CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99, c))
    upd = (exec_sql("SELECT MAX(start_date)::text AS d FROM selling_prices "
                    "WHERE price_book = 'MA_base' AND start_date <= CURRENT_DATE") or [{}])[0].get("d")
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
    return {
        "items": items, "categories": cats, "brand": "VFAN", "company": "YQ Bahrain",
        "prices_updated": upd, "stock_as_of": stock_as_of,
        "salesmen": [{"id": s["id"], "name": s["name"], "referral_code": s.get("referral_code")}
                     for s in ctx["salesmen"]],
        "offers": offers,
        "pairs": [{"item_code": c, "with": w} for c, w in ctx.get("pairs", {}).items() if w],
        "settings": {
            "currency": "BHD",
            "min_order_bhd": money(vals.get("shop_min_order_bhd")),
            "free_delivery_threshold_bhd": money(vals.get("shop_free_delivery_threshold_bhd")),
            "delivery_fee_bhd": money(vals.get("shop_delivery_fee_bhd")),
            "allow_backorder": _flag(vals, "shop_allow_backorder"),
            "show_retail_compare": show_compare,
        },
        "ref": resolve_ref(ctx, referral_code),
    }


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
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ShopError("Your cart is empty.")
    if len(raw_lines) > MAX_LINES:
        raise ShopError(f"Too many lines (max {MAX_LINES}).")
    merged: dict[str, int] = {}
    for ln in raw_lines:
        if not isinstance(ln, dict):
            raise ShopError("Bad line.")
        code = str(ln.get("item_code") or "").strip().upper()[:64]
        qty = _i(ln.get("qty"))
        if not code:
            raise ShopError("Bad line: missing item code.")
        if qty <= 0 or qty > MAX_QTY:
            raise ShopError(f"Bad quantity for {code}.")
        merged[code] = merged.get(code, 0) + qty
    return list(merged.items())


def price_cart(raw_lines, coupon_code: str | None = None, referral_code: str | None = None,
               ctx: dict | None = None) -> dict:
    """Price a cart server-side. Raises ShopError for anything the customer must fix."""
    ctx = ctx or context()
    vals = ctx["settings"]
    low_units = _i(vals.get("shop_low_stock_units"), 10)
    allow_bo = _flag(vals, "shop_allow_backorder")
    ref = (referral_code or "").strip().lower() or None
    lines_in = normalize_lines(raw_lines)

    lines: list[dict] = []
    warnings: list[str] = []
    clamped: list[str] = []
    subtotal = 0.0
    for code, qty in lines_in:
        it = ctx["items"].get(code)
        if not it:
            raise ShopError(f"{code} is no longer available.")
        lp = it.get("standard_rate")
        if lp is None or _f(lp) <= 0:
            raise ShopError(f"{code} has no current price — ask your salesman.")
        lp = money(lp)
        moq = max(_i(it.get("moq"), 1), 1)
        if qty < moq:
            raise ShopError(f"Minimum order for {code} is {moq}.")
        status = stock_status_for(it.get("stock_qty"), low_units)
        backorder = status == STOCK_OUT
        if backorder and not allow_bo:
            raise ShopError(f"{code} is out of stock.")
        if backorder:
            warnings.append(f"{code} is out of stock — it will be backordered and confirmed by your salesman.")
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
        lines.append({
            "item_code": code, "display_name": it.get("display_name") or code, "spec": it.get("spec"),
            "image_url": it.get("product_image_url"), "qty": qty, "moq": moq,
            "list_price_bhd": lp, "unit_price_bhd": unit, "discount_bhd": money((lp - unit) * qty),
            "line_total_bhd": line_total, "stock_status": status, "backorder": backorder,
            "applied": applied, "warning": None, "_floor": floor,
        })
    subtotal = money(subtotal)
    item_discount = money(sum(ln["discount_bhd"] for ln in lines))
    net = money(subtotal - item_discount)

    # cart-level: automatic cart_value rules vs coupon — customer gets the better unless stackable
    discounts = [{"rule_id": a["rule_id"], "name": a["name"], "kind": a["kind"],
                  "amount_bhd": ln["discount_bhd"]}
                 for ln in lines for a in ln["applied"]]
    headroom = 0.0
    unlimited = False
    for ln in lines:
        if ln["_floor"] is None:
            unlimited = True
        else:
            headroom += max(0.0, (ln["unit_price_bhd"] - ln["_floor"]) * ln["qty"])
    cap = float("inf") if unlimited else headroom

    def _cart_amount(rule: dict) -> float:
        eligible = sum(ln["line_total_bhd"] for ln in lines if _rule_matches_item(rule, ctx["items"][ln["item_code"]]))
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
    delivery = fee if (threshold > 0 and fee > 0 and net_after < threshold) else 0.0
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
    can_submit = net_after >= min_order
    block_reason = None
    if not can_submit:
        block_reason = f"Minimum order is BHD {min_order:.3f} — add BHD {money(min_order - net_after):.3f} more."
        warnings.append(block_reason)
    if clamped:
        log.info("shop margin floor clamped: %s", ", ".join(clamped))
    for ln in lines:
        ln.pop("_floor", None)
    return {
        "ok": True, "lines": lines,
        "subtotal_bhd": subtotal, "discount_bhd": discount_total, "delivery_bhd": delivery,
        "total_bhd": total, "units": sum(ln["qty"] for ln in lines), "items": len(lines),
        "discounts": discounts, "coupon": coupon_out, "progress": progress,
        "warnings": warnings, "can_submit": can_submit, "min_order_bhd": min_order,
        "block_reason": block_reason,
        "has_backorder": any(ln["backorder"] for ln in lines),
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


# ── order creation ────────────────────────────────────────────────────────────

def create_order(body: dict, ip: str | None = None, ua: str | None = None) -> dict:
    """Validate → re-price → persist. Returns the stored order (+lines) and the priced totals."""
    if clean(body.get("website"), 10):
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
    ctx = context()
    quote = price_cart(body.get("lines"), body.get("coupon_code"), body.get("referral_code"), ctx=ctx)
    if not quote["can_submit"]:
        raise ShopError(" ".join(quote["warnings"]) or "Order cannot be submitted.")
    sm, source = salesman_for(ctx, body.get("referral_code"), _i(body.get("salesman_id")) or None)
    prefix = str(ctx["settings"].get("shop_order_prefix") or "YQ")
    client = get_client()
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
        "customer_name": name, "customer_phone": phone,
        "customer_shop": clean(cust.get("shop"), 120) or None,
        "customer_area": clean(cust.get("area"), 120) or None,
        "customer_email": email, "note": clean(body.get("note"), 1000) or None,
        "salesman_id": sm["id"] if sm else None, "salesman_name": sm["name"] if sm else None,
        "source": source, "referral_code": clean(body.get("referral_code"), 32).lower() or None,
        "src": clean(body.get("src"), 80) or None,
        "coupon_code": (quote["coupon"] or {}).get("code") if (quote["coupon"] or {}).get("valid") else None,
        "subtotal_bhd": quote["subtotal_bhd"], "discount_bhd": quote["discount_bhd"],
        "delivery_bhd": quote["delivery_bhd"], "total_bhd": quote["total_bhd"],
        "items_count": quote["items"], "units_count": quote["units"],
        "has_backorder": quote["has_backorder"], "ip_hash": _ip_hash(ip), "ua": (ua or "")[:200],
        "created_at": _iso(), "updated_at": _iso(),
    }
    order = client.table("shop_orders").insert(row).execute().data[0]
    lines = [{
        "order_id": order["id"], "item_code": ln["item_code"], "display_name": ln["display_name"],
        "spec": ln.get("spec"), "image_url": ln.get("image_url"), "qty": ln["qty"],
        "list_price_bhd": ln["list_price_bhd"], "unit_price_bhd": ln["unit_price_bhd"],
        "discount_bhd": ln["discount_bhd"], "line_total_bhd": ln["line_total_bhd"],
        "stock_status": ln["stock_status"], "backorder": ln["backorder"],
        "rule_ids": [a["rule_id"] for a in ln["applied"]] or None,
    } for ln in quote["lines"]]
    client.table("shop_order_lines").insert(lines).execute()
    client.table("shop_order_events").insert({
        "order_id": order["id"], "actor": "customer", "event": "created",
        "detail": {"source": source, "clamped": quote.get("_clamped") or []}}).execute()
    if quote.get("_coupon_rule_id"):
        try:
            r = client.table("discount_rules").select("uses").eq("id", quote["_coupon_rule_id"]).execute().data
            client.table("discount_rules").update({"uses": _i((r or [{}])[0].get("uses")) + 1}) \
                .eq("id", quote["_coupon_rule_id"]).execute()
        except Exception as e:  # noqa: BLE001
            log.warning("coupon use count failed: %s", e)
    record_event({"event": "order", "session_id": body.get("session_id"),
                  "referral_code": row["referral_code"], "salesman_id": row["salesman_id"],
                  "src": row["src"]}, ip=ip, ua=ua)
    order["lines"] = quote["lines"]
    order["salesman"] = sm
    order["status_url"] = f"{_base_url()}/o/{token}" if _base_url() else f"/o/{token}"
    order["totals"] = {k: v for k, v in quote.items() if not k.startswith("_")}
    return order


# ── events ────────────────────────────────────────────────────────────────────

def record_event(body: dict, ip: str | None = None, ua: str | None = None) -> bool:
    ev = str(body.get("event") or "").strip().lower()
    if ev not in EVENTS:
        return False
    try:
        get_client().table("shop_events").insert({
            "session_id": clean(body.get("session_id"), 64) or None, "event": ev,
            "item_code": clean(body.get("item_code"), 64).upper() or None,
            "referral_code": clean(body.get("referral_code"), 32).lower() or None,
            "salesman_id": _i(body.get("salesman_id")) or None,
            "src": clean(body.get("src"), 80) or None,
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


def public_order_view(o: dict) -> dict:
    """What the customer's status page may see (no phone/email/ip of anyone)."""
    from app.shop_notify import customer_to_salesman_wa_url
    sm = o.get("salesman") or {}
    wa = customer_to_salesman_wa_url(o)   # falls back to the owner number when no salesman is set
    return {
        "order_no": o["order_no"], "status": o["status"],
        "created_at": o.get("created_at"), "updated_at": o.get("updated_at"),
        "salesman": ({"name": sm.get("name") or "YQ Bahrain", "whatsapp_url": wa} if (sm or wa) else None),
        "customer": {"name": o.get("customer_name"), "shop": o.get("customer_shop"), "area": o.get("customer_area")},
        "lines": [{"item_code": ln["item_code"], "display_name": ln.get("display_name"), "qty": ln["qty"],
                   "unit_price_bhd": money(ln["unit_price_bhd"]), "line_total_bhd": money(ln["line_total_bhd"]),
                   "stock_status": ln.get("stock_status"), "backorder": bool(ln.get("backorder")),
                   "image_url": ln.get("image_url")} for ln in o.get("lines", [])],
        "subtotal_bhd": money(o.get("subtotal_bhd")), "discount_bhd": money(o.get("discount_bhd")),
        "delivery_bhd": money(o.get("delivery_bhd")), "total_bhd": money(o.get("total_bhd")),
        "has_backorder": bool(o.get("has_backorder")), "note": o.get("note"),
        "timeline": [{"ts": e.get("ts"), "event": e.get("event"),
                      "note": (e.get("detail") or {}).get("note") if isinstance(e.get("detail"), dict) else None}
                     for e in o.get("events", [])],
    }


def list_orders(status: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0,
                salesman_id: int | None = None) -> dict:
    qry = get_client().table("shop_orders").select(
        "id,order_no,status,customer_name,customer_phone,customer_shop,customer_area,salesman_id,"
        "salesman_name,total_bhd,items_count,units_count,has_backorder,created_at,updated_at,source,"
        "referral_code,coupon_code", count="exact")
    if status and status in STATUSES:
        qry = qry.eq("status", status)
    if salesman_id is not None:
        qry = qry.eq("salesman_id", salesman_id)
    if q:
        s = clean(q, 60).replace("%", "").replace(",", " ")
        if s:
            qry = qry.or_(f"order_no.ilike.%{s}%,customer_name.ilike.%{s}%,customer_shop.ilike.%{s}%,"
                          f"customer_phone.ilike.%{s}%")
    res = qry.order("created_at", desc=True).range(offset, offset + max(1, min(limit, 200)) - 1).execute()
    return {"orders": res.data or [], "count": res.count if res.count is not None else len(res.data or [])}


def set_status(order_id: int, status: str, note: str | None, actor: str) -> dict:
    o = get_order(order_id)
    if not o:
        raise ShopError("Order not found.")
    status = str(status or "").strip().lower()
    if status not in STATUSES:
        raise ShopError("Unknown status.")
    if status not in NEXT_STATUS.get(o["status"], ()):
        raise ShopError(f"Cannot move an order from {o['status']} to {status}.")
    client = get_client()
    client.table("shop_orders").update({"status": status, "updated_at": _iso()}).eq("id", order_id).execute()
    client.table("shop_order_events").insert({
        "order_id": order_id, "actor": actor, "event": f"status:{status}",
        "detail": {"note": clean(note, 500) or None, "from": o["status"]}}).execute()
    return get_order(order_id) or {}


# ── salesmen admin ────────────────────────────────────────────────────────────

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return (s.split("-")[0] if s else "rep")[:32] or "rep"


def salesman_link(s: dict) -> str:
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
    for k, lim in (("phone", 32), ("email", 160), ("whatsapp", 32), ("user_email", 160), ("focus_name", 120)):
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
            fields[k] = v
    if "referral_code" in payload or salesman_id is None:
        code = clean(payload.get("referral_code"), 32).lower() or slugify(fields.get("name") or payload.get("name") or "")
        if not _SLUG.match(code):
            raise ShopError("Referral code: 2-32 chars, letters, numbers and dashes.")
        fields["referral_code"] = code
    for k in ("is_active", "notify_email", "notify_whatsapp"):
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


def salesman_for_user(email: str | None) -> dict | None:
    if not email:
        return None
    r = (get_client().table("salesmen").select("*").ilike("user_email", email.strip())
         .limit(1).execute().data or [])
    return r[0] if r else None


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
            rev = exec_sql_params(
                "SELECT COALESCE(SUM(revenue_bhd),0) AS rev FROM v_sales "
                "WHERE sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90 "
                "AND (salesman_resolved = $1 OR salesman_resolved LIKE $1 || ' - %')", [sm["focus_name"]])
            tgt = exec_sql_params("SELECT target_bhd FROM salesman_targets WHERE salesman = $1 LIMIT 1",
                                  [sm["focus_name"]])
            focus = {"revenue_90d_bhd": money((rev or [{}])[0].get("rev")),
                     "target_bhd": money((tgt or [{}])[0].get("target_bhd")) if tgt else None}
        except Exception as e:  # noqa: BLE001
            log.debug("focus kpis failed: %s", e)
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


def delete_rule(rule_id: int) -> None:
    get_client().table("discount_rules").delete().eq("id", rule_id).execute()
    invalidate()


# ── share preview (Open Graph + JSON-LD) ──────────────────────────────────────

def share_item(item_code: str) -> dict | None:
    ctx = context()
    it = ctx["items"].get(str(item_code or "").strip().upper())
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
    ev = (client.table("shop_events").select("ts,event,session_id,item_code,referral_code,salesman_id,src")
          .gte("ts", since).limit(20000).execute().data or [])
    orders = (client.table("shop_orders")
              .select("id,status,total_bhd,units_count,salesman_id,salesman_name,referral_code,src,coupon_code,"
                      "customer_phone,created_at,has_backorder,source")
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
        key = o.get("referral_code") or ("dropdown" if o.get("source") == "dropdown" else "direct")
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
    return {
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
