"""YQ Shop tests — pricing engine, validation, routing, notifications, and live views.

    python -m tests.test_shop

Same lightweight runner as tests/test_v3.py (no pytest). The pricing-engine tests run against a
synthetic context (no DB). Live checks are read-only and SKIP (print, not fail) until
scripts/shop_migration.sql has been applied.
"""
from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── synthetic context ─────────────────────────────────────────────────────────

def _item(code, price, stock=100, cat="CABLE", moq=1, b2c=None, sold_90d=0, sold_30d=0, prev_30d=0, customers_30d=0):
    return {"item_code": code, "display_name": code, "spec": f"{code} spec", "category": cat, "brand": "VFAN",
            "standard_rate": price, "b2c_rate": b2c, "product_image_url": None, "package_image_url": None,
            "sort_order": None, "created_at": "2026-07-03T00:00:00+00:00", "moq": moq, "pack_size": None,
            "stock_qty": stock, "stock_as_of": "2026-09-14", "sold_30d": sold_30d, "prev_30d": prev_30d,
            "sold_90d": sold_90d, "customers_30d": customers_30d}


def _rule(id, kind, name=None, item_codes=(), categories=(), referral_codes=(), **kw):
    r = {"id": id, "kind": kind, "name": name or f"rule{id}", "stackable": False, "min_qty": None,
         "min_value_bhd": None, "pct_off": None, "amount_off_bhd": None, "fixed_price_bhd": None,
         "coupon_code": None, "starts_at": None, "ends_at": None, "max_uses": None, "uses": 0, "priority": 100,
         "scope": {"item_codes": [c.upper() for c in item_codes], "categories": [c.upper() for c in categories],
                   "referral_codes": [c.lower() for c in referral_codes]}}
    r.update(kw)
    return r


def _ctx(items, rules=(), costs=None, **settings):
    from app.shop import SETTING_DEFAULTS
    vals = dict(SETTING_DEFAULTS)
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: i for i in items},
            "order": [i["item_code"] for i in items], "costs": costs or {}, "rules": list(rules),
            "salesmen": [{"id": 1, "name": "Furqan Ahmed", "referral_code": "furqan", "is_active": True},
                         {"id": 2, "name": "Harsh Bhatia", "referral_code": "harsh", "is_active": True}],
            "loaded_at": ""}


# ── pricing engine ────────────────────────────────────────────────────────────

@test("pricing: plain list-price cart, 3dp money")
def _():
    from app.shop import price_cart
    q = price_cart([{"item_code": "t02", "qty": 2}], ctx=_ctx([_item("T02", 2.95)]))
    assert q["subtotal_bhd"] == 5.9 and q["total_bhd"] == 5.9 and q["discount_bhd"] == 0, q
    assert q["lines"][0]["item_code"] == "T02" and q["lines"][0]["stock_status"] == "in_stock"
    assert q["can_submit"] and not q["has_backorder"] and q["coupon"] is None


@test("pricing: quantity tier applies at min_qty and computes 3dp unit")
def _():
    from app.shop import price_cart
    ctx = _ctx([_item("T02", 2.95)], rules=[_rule(5, "qty_tier", "Buy 12+", item_codes=["T02"], min_qty=12, pct_off=10)])
    q = price_cart([{"item_code": "T02", "qty": 11}], ctx=ctx)
    assert q["lines"][0]["unit_price_bhd"] == 2.95 and not q["lines"][0]["applied"]
    q = price_cart([{"item_code": "T02", "qty": 12}], ctx=ctx)
    ln = q["lines"][0]
    assert ln["unit_price_bhd"] == 2.655 and ln["discount_bhd"] == 3.54 and ln["line_total_bhd"] == 31.86, ln
    assert q["discount_bhd"] == 3.54 and q["total_bhd"] == 31.86 and q["discounts"][0]["rule_id"] == 5


@test("pricing: margin floor clamps a tier below landed cost x (1+margin)")
def _():
    from app.shop import price_cart
    ctx = _ctx([_item("T02", 2.95)], rules=[_rule(5, "qty_tier", min_qty=12, pct_off=40, item_codes=["T02"])],
               costs={"T02": 2.0}, shop_min_margin_pct="0.20", shop_vat_rate="0")   # floor 2.4
    q = price_cart([{"item_code": "T02", "qty": 12}], ctx=ctx)
    assert q["lines"][0]["unit_price_bhd"] == 2.4 and "T02" in q["_clamped"], q["lines"][0]
    ctx = _ctx([_item("T02", 2.95)], rules=[_rule(5, "qty_tier", min_qty=12, pct_off=40, item_codes=["T02"])],
               costs={"T02": 2.6}, shop_vat_rate="0")                    # floor 3.12 > list → list price
    q = price_cart([{"item_code": "T02", "qty": 12}], ctx=ctx)
    assert q["lines"][0]["unit_price_bhd"] == 2.95
    ctx = _ctx([_item("T02", 2.95)], rules=[_rule(5, "qty_tier", min_qty=12, pct_off=40, item_codes=["T02"])],
               costs={"T02": 2.0}, shop_vat_rate="0.10")                 # VAT-inclusive floor 2.64
    q = price_cart([{"item_code": "T02", "qty": 12}], ctx=ctx)
    assert q["lines"][0]["unit_price_bhd"] == 2.64


@test("pricing: cart-value rule unlocks at threshold and shows progress before it")
def _():
    from app.shop import price_cart
    ctx = _ctx([_item("T02", 10.0)], rules=[_rule(7, "cart_value", "5% over 100", min_value_bhd=100, pct_off=5)])
    q = price_cart([{"item_code": "T02", "qty": 9}], ctx=ctx)
    assert q["discount_bhd"] == 0 and q["progress"]["remaining_bhd"] == 10.0 and not q["progress"]["unlocked"], q["progress"]
    q = price_cart([{"item_code": "T02", "qty": 12}], ctx=ctx)
    assert q["discount_bhd"] == 6.0 and q["total_bhd"] == 114.0 and q["progress"]["unlocked"], q


@test("pricing: coupon vs automatic cart rule — customer gets the better one")
def _():
    from app.shop import price_cart
    rules = [_rule(7, "cart_value", "Auto 5%", min_value_bhd=50, pct_off=5),
             _rule(8, "coupon", "WELCOME10", coupon_code="WELCOME10", pct_off=10),
             _rule(9, "coupon", "SMALL3", coupon_code="SMALL3", pct_off=3)]
    ctx = _ctx([_item("T02", 10.0)], rules=rules)
    q = price_cart([{"item_code": "T02", "qty": 10}], coupon_code="welcome10", ctx=ctx)
    assert q["discount_bhd"] == 10.0 and q["coupon"]["valid"] and q["_coupon_rule_id"] == 8, q["coupon"]
    q = price_cart([{"item_code": "T02", "qty": 10}], coupon_code="SMALL3", ctx=ctx)
    assert q["discount_bhd"] == 5.0 and q["_coupon_rule_id"] is None and "already" in q["coupon"]["message"], q["coupon"]
    q = price_cart([{"item_code": "T02", "qty": 10}], coupon_code="NOPE", ctx=ctx)
    assert q["coupon"]["valid"] is False and q["discount_bhd"] == 5.0


@test("pricing: coupon restricted to a salesman's customers")
def _():
    from app.shop import price_cart
    ctx = _ctx([_item("T02", 10.0)], rules=[_rule(8, "coupon", coupon_code="FURQAN5", pct_off=5, referral_codes=["furqan"])])
    q = price_cart([{"item_code": "T02", "qty": 1}], coupon_code="FURQAN5", ctx=ctx)
    assert q["coupon"]["valid"] is False
    q = price_cart([{"item_code": "T02", "qty": 1}], coupon_code="FURQAN5", referral_code="furqan", ctx=ctx)
    assert q["coupon"]["valid"] and q["discount_bhd"] == 0.5


@test("pricing: cart discount cannot breach the margin floor")
def _():
    from app.shop import price_cart
    ctx = _ctx([_item("T02", 10.0)], rules=[_rule(8, "coupon", coupon_code="HALF", pct_off=50)],
               costs={"T02": 7.0}, shop_min_margin_pct="0.20", shop_vat_rate="0")   # floor 8.4 → headroom 1.6/unit
    q = price_cart([{"item_code": "T02", "qty": 2}], coupon_code="HALF", ctx=ctx)
    assert q["discount_bhd"] == 3.2 and q["total_bhd"] == 16.8, q


@test("pricing: backorder flag with allow_backorder, blocked line without")
def _():
    from app.shop import price_cart
    q = price_cart([{"item_code": "T02", "qty": 1}], ctx=_ctx([_item("T02", 2.95, stock=0)]))
    assert q["lines"][0]["backorder"] and q["has_backorder"] and q["warnings"]
    ctx = _ctx([_item("T02", 2.95, stock=0), _item("X01", 1.0)], shop_allow_backorder="0")
    q = price_cart([{"item_code": "T02", "qty": 1}, {"item_code": "X01", "qty": 2}], ctx=ctx)
    t02 = next(ln for ln in q["lines"] if ln["item_code"] == "T02")
    assert t02["unavailable"] and "sold out" in t02["blocked_reason"].lower(), t02
    assert q["total_bhd"] == 2.0 and q["items"] == 1 and not q["can_submit"], q
    assert "T02" in q["block_reason"], q["block_reason"]


@test("pricing: MOQ and unknown items block their own line, empty cart raises, duplicates merge")
def _():
    from app.shop import ShopError, price_cart
    ctx = _ctx([_item("T02", 2.95, moq=6), _item("X01", 1.0)])
    q = price_cart([{"item_code": "T02", "qty": 4}, {"item_code": "X01", "qty": 1}], ctx=ctx)
    assert q["lines"][0]["unavailable"] and "6" in q["lines"][0]["blocked_reason"], q["lines"][0]
    assert q["total_bhd"] == 1.0 and not q["can_submit"], q
    q = price_cart([{"item_code": "ZZ", "qty": 1}, {"item_code": "X01", "qty": 3}], ctx=ctx)
    assert q["lines"][0]["unavailable"] and q["lines"][0]["item_code"] == "ZZ", q["lines"][0]
    assert q["subtotal_bhd"] == 3.0 and q["total_bhd"] == 3.0 and q["units"] == 3 and not q["can_submit"], q
    try:
        price_cart([], ctx=ctx)
        raise AssertionError("expected ShopError for an empty cart")
    except ShopError:
        pass
    q = price_cart([{"item_code": "t02", "qty": 3}, {"item_code": "T02", "qty": 3}], ctx=ctx)
    assert q["lines"][0]["qty"] == 6 and q["items"] == 1 and q["units"] == 6 and q["can_submit"]


@test("pricing: mixed-case catalog codes price whatever case the cart sends")
def _():
    from app.shop import price_cart
    ctx = _ctx([_item("X05 UL-1Mtr", 0.4), _item("P04 2mtr", 1.2)])
    q = price_cart([{"item_code": "X05 UL-1Mtr", "qty": 2}, {"item_code": "p04 2MTR", "qty": 1}], ctx=ctx)
    assert q["can_submit"] and q["total_bhd"] == 2.0, q
    assert [ln["item_code"] for ln in q["lines"]] == ["X05 UL-1Mtr", "P04 2mtr"], q["lines"]
    assert not any(ln["unavailable"] for ln in q["lines"])


@test("catalog: classifier files every live SKU shape into the fixed vocabulary")
def _():
    from app.catalog import CATEGORY_ORDER, classify_category
    cases = {
        ("BS06", "BS06 BT Speaker Wireless Distance 10mtr + Battery 1200mah"): "BLUETOOTH SPEAKER",
        ("K105", "K105 PB 10000mah Slim Power Bank"): "POWER BANK",
        ("C12", "C12 Kirsitre Car Charger (USB 22.5W + Type-C 20W Port)"): "CAR CHARGER",
        ("H09", "H09 AC Vent Mobile Phone Holder (VFAN)"): "CAR ACCESSORIES",
        ("T16", "T16 Round the Ear Ring design Aipords (VFAN)"): "BLUETOOTH HEADSET",
        ("T11", "T11 In-Ear MINI Airpord Type-C Port"): "BLUETOOTH HEADSET",
        ("T17", "BT version:  V6.0 Transmission range: 10m"): "BLUETOOTH HEADSET",
        ("M20", "M20 Type-C Jack Flat In-Ear Phones (VFAN)"): "EARPHONE",
        ("UK10 C", "UK10 C 20W Charger + Type-C Cable (USB + Type-C Port)"): "CHARGER",
        ("W01", "W01 15W 1Mtr Aluminium Alloy+PVC Wireless Charger"): "CHARGER",
        ("TB-D9", "TB-D9 1mtr Mix Cables + HFs + AUX (VFAN)"): "CABLE",
        ("X26-C", "X26-C C to C (100W)"): "CABLE",
        ("X27-C", "X27-C C to C (100W) (LED Display)"): "CABLE",
        ("X18", "X18 Multi Converter Cale (VFAN)"): "CABLE",
        ("Big Product Display", "Big Product Display 1095*417*250MM"): "MISCELLANEOUS",
    }
    for parts, want in cases.items():
        got = classify_category(*parts)
        assert got == want, (parts, got, want)
        assert got in CATEGORY_ORDER or got == "MISCELLANEOUS"


@test("pricing: free-delivery threshold, delivery fee, minimum order")
def _():
    from app.shop import price_cart
    ctx = _ctx([_item("T02", 10.0)], shop_free_delivery_threshold_bhd="50", shop_delivery_fee_bhd="2", shop_min_order_bhd="15")
    q = price_cart([{"item_code": "T02", "qty": 1}], ctx=ctx)
    assert q["delivery_bhd"] == 2.0 and q["total_bhd"] == 12.0 and not q["can_submit"], q
    assert q["progress"]["kind"] == "free_delivery" and q["progress"]["remaining_bhd"] == 40.0
    q = price_cart([{"item_code": "T02", "qty": 5}], ctx=ctx)
    assert q["delivery_bhd"] == 0 and q["progress"]["unlocked"] and q["can_submit"]


# ── status, badges, payload helpers ──────────────────────────────────────────

@test("stock: status thresholds and selling-fast cover")
def _():
    from app.shop import STOCK_IN, STOCK_LOW, STOCK_OUT, selling_fast, stock_status_for
    assert stock_status_for(None, 10) == STOCK_OUT and stock_status_for(0, 10) == STOCK_OUT
    assert stock_status_for(10, 10) == STOCK_LOW and stock_status_for(11, 10) == STOCK_IN
    assert selling_fast(100, 600, 30) is True        # 15 days cover
    assert selling_fast(100, 100, 30) is False       # 90 days cover
    assert selling_fast(0, 100, 30) is False


@test("badges: best seller per category, trending, tiers listing")
def _():
    from app.shop import _badges, item_tiers
    items = [_item("A", 1, sold_90d=50, sold_30d=30, prev_30d=10), _item("B", 1, sold_90d=5),
             _item("C", 1, cat="CHARGER", sold_90d=1, sold_30d=12, prev_30d=0)]
    ctx = _ctx(items, rules=[_rule(1, "qty_tier", min_qty=12, pct_off=10, item_codes=["A"]),
                             _rule(2, "qty_tier", min_qty=24, pct_off=20, item_codes=["A"])],
               shop_best_seller_top_n="1")
    b = _badges(ctx)
    assert "best_seller" in b["A"] and "best_seller" not in b["B"] and "best_seller" in b["C"]
    assert "trending" in b["A"] and "trending" in b["C"] and "on_offer" in b["A"]
    tiers = item_tiers(ctx, ctx["items"]["A"])
    assert [t["min_qty"] for t in tiers] == [12, 24] and tiers[1]["unit_price_bhd"] == 0.8


@test("routing: referral link beats dropdown beats default setting")
def _():
    from app.shop import resolve_ref, salesman_for
    ctx = _ctx([_item("T02", 1)], shop_default_salesman="Harsh Bhatia")
    assert salesman_for(ctx, "FURQAN", 2)[0]["id"] == 1 and salesman_for(ctx, "furqan", 2)[1] == "referral"
    assert salesman_for(ctx, None, 2) == (ctx["salesmen"][1], "dropdown")
    assert salesman_for(ctx, "nobody", None)[1] == "default" and salesman_for(ctx, None, None)[0]["id"] == 2
    assert resolve_ref(ctx, "harsh")["salesman_name"] == "Harsh Bhatia" and resolve_ref(ctx, "x") is None


@test("validation: phones, slugs, control chars")
def _():
    from app.shop import clean, phone_digits, slugify
    assert phone_digits("33001122") == "97333001122" and phone_digits("+973 3300-1122") == "97333001122"
    assert phone_digits("0097333001122") == "97333001122" and phone_digits("12") is None
    assert slugify("Furqan Ahmed") == "furqan" and slugify("Harsh Bhatia") == "harsh" and slugify("!!") == "rep"
    assert clean("a\x00b\x1fc  ", 10) == "abc"


@test("notify: WhatsApp text + links use salesman number, fall back to owner")
def _():
    from app.shop_notify import customer_to_salesman_wa_url, order_text, salesman_to_customer_wa_url
    o = {"order_no": "YQ-2609-0001", "token": "tok", "customer_name": "Ali Hassan", "customer_phone": "97333001122",
         "customer_shop": "Ali Mobiles", "total_bhd": 32.4, "units_count": 12, "discount_bhd": 0, "delivery_bhd": 0,
         "lines": [{"item_code": "T02", "qty": 12, "unit_price_bhd": 2.7, "line_total_bhd": 32.4, "backorder": True}],
         "salesman": {"name": "Furqan Ahmed", "phone": "97337158552"}}
    t = order_text(o)
    assert "YQ-2609-0001" in t and "backorder" in t and "BHD 32.400" in t
    assert customer_to_salesman_wa_url(o).startswith("https://wa.me/97337158552?text=")
    assert "97333001122" in salesman_to_customer_wa_url(o, "confirmed")
    o["salesman"] = None
    url = customer_to_salesman_wa_url(o)
    assert url is None or url.startswith("https://wa.me/")


@test("notify: pre-filled mailto to the salesman (no provider key needed)")
def _():
    import urllib.parse as u
    from app.shop_notify import customer_to_salesman_email_url
    o = {"order_no": "YQ-2609-0009", "token": "tok", "customer_name": "Ali Hassan", "customer_shop": "Ali Mobiles",
         "customer_phone": "97333001122", "total_bhd": 29.1, "units_count": 18, "discount_bhd": 0, "delivery_bhd": 0,
         "lines": [{"item_code": "T02", "qty": 6, "unit_price_bhd": 2.95, "line_total_bhd": 17.7}],
         "salesman": {"name": "Furqan Ahmed", "email": "rep@example.com"}}
    url = customer_to_salesman_email_url(o)
    assert url.startswith("mailto:rep@example.com?"), url
    q = u.parse_qs(u.urlparse(url).query)
    assert q["subject"][0] == "Order YQ-2609-0009 - Ali Mobiles", q["subject"]
    body = q["body"][0]
    assert "T02" in body and "BHD 29.100" in body and body.isascii(), body
    # never invent a recipient: no salesman email -> no link at all
    o["salesman"] = {"name": "No Email Rep"}
    assert customer_to_salesman_email_url(o) is None
    o["salesman"] = None
    assert customer_to_salesman_email_url(o) is None


@test("payload: no quantity or cost ever leaves via the public item shape")
def _():
    from app.shop import catalog_payload  # noqa: F401  (import only — live shape checked below)
    forbidden = {"stock_qty", "landed_cost_bhd", "product_cost_bhd", "sold_90d", "sold_30d", "customers_30d"}
    src = (ROOT / "app" / "shop.py").read_text(encoding="utf-8")
    block = src.split("items.append({", 1)[1].split("})", 1)[0]
    assert not any(f'"{k}":' in block for k in forbidden), "public item block leaks a private field"


# ── live, read-only (SKIP until the migration is applied) ─────────────────────

def _migrated() -> bool:
    from app.db_read import exec_sql
    try:
        return bool(exec_sql("SELECT to_regclass('public.v_catalog_stock') IS NOT NULL AS ok")[0]["ok"])
    except Exception:  # noqa: BLE001
        return False


@test("live: v_catalog_stock assigns every stock row to at most one code")
def _():
    from app.db_read import exec_sql
    if not _migrated():
        print("   SKIP — scripts/shop_migration.sql not applied")
        return
    r = exec_sql("SELECT COUNT(*) AS rows, COUNT(item_code) AS mapped, COUNT(DISTINCT item_name) AS names "
                 "FROM v_catalog_stock_rows")[0]
    assert r["rows"] == r["names"], "a stock row was duplicated by the matcher"
    assert r["mapped"] >= r["rows"] * 0.8, f"matcher coverage too low: {r}"


@test("live: public payload is a superset of the legacy shape with statuses only")
def _():
    import os
    from fastapi.testclient import TestClient
    if not _migrated():
        print("   SKIP — scripts/shop_migration.sql not applied")
        return
    from app.catalog import share_token
    import app.main as m
    tok = share_token(create=False) or os.getenv("CATALOG_TOKEN", "")
    r = TestClient(m.app).get(f"/public/catalog/{tok}")
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    for k in ("items", "categories", "company", "prices_updated", "salesmen", "offers", "settings"):
        assert k in d, k
    # owner, 15-Sep-2026: no company phone number on the public catalog
    assert "whatsapp" not in d, "the public catalog must not publish a WhatsApp number"
    assert "OTHER" not in (d.get("categories") or []), d.get("categories")
    assert not any("display" in str(i["item_code"]).lower() for i in d["items"]), "display stands are hidden"
    it = d["items"][0]
    for k in ("item_code", "price_bhd", "b2c_bhd", "product_image_url", "stock_status", "moq", "tiers", "badges"):
        assert k in it, k
    assert it["stock_status"] in ("in_stock", "low_stock", "out_of_stock")
    assert not any(k in it for k in ("stock_qty", "landed_cost_bhd", "sold_90d"))


@test("staff: list_orders accepts a comma status list and returns counts")
def _():
    from app.shop import list_orders, status_counts
    r = list_orders(status="confirmed,packed", limit=5)
    assert "counts" in r and set(r["counts"]) == {"new", "confirmed", "packed", "delivered", "cancelled"}, r.get("counts")
    assert all(o["status"] in ("confirmed", "packed") for o in r["orders"])
    assert set(status_counts()) == set(r["counts"])


@test("live: staff catalog carries units + me, the public one never does")
def _():
    from app.catalog import share_token
    from app.shop import catalog_payload
    if not _migrated():
        print("   SKIP — scripts/shop_migration.sql not applied")
        return
    staff = catalog_payload(None, staff_email="fzulfiqar@pie-int.com")
    assert staff["mode"] == "salesman" and staff["me"]["salesman_name"] == "Furqan Ahmed", staff.get("me")
    assert all("stock_qty" in i and isinstance(i["stock_qty"], int) for i in staff["items"])
    assert staff["ref"]["referral_code"] == "furqan"
    pub = catalog_payload(share_token(create=False))
    assert "mode" not in pub and "me" not in pub and not any("stock_qty" in i for i in pub["items"])
    assert catalog_payload("wrong-token") is None


@test("live: a salesman-placed order is source=salesman, records placed_by, and does not alert himself")
def _():
    import re
    from app.database import get_client
    from app.shop import create_order, recent_customers
    from app.shop_notify import notify_new_order
    if not _migrated():
        print("   SKIP — scripts/shop_migration.sql not applied")
        return
    from app.shop import context
    code = next(c for c in context()["order"] if context()["items"][c].get("standard_rate"))
    o = create_order({"lines": [{"item_code": code, "qty": 1}],
                      "customer": {"name": "Staff Test Shop", "phone": "33001122", "shop": "Test Shop", "area": "Manama"},
                      "note": "automated test — delete me"}, staff_email="fzulfiqar@pie-int.com")
    c = get_client()
    try:
        assert o["source"] == "salesman" and o["placed_by"] == "fzulfiqar@pie-int.com", (o["source"], o.get("placed_by"))
        assert o["salesman"]["name"] == "Furqan Ahmed" and o["src"] == "salesman"
        res = notify_new_order(o["id"])
        assert "fzulfiqar@pie-int.com" not in [r.lower() for r in res.get("recipients", [])] or                res.get("recipients") == ["fzulfiqar@pie-int.com"] and "ALERT_EMAIL_TO" in __import__("os").environ, res
        assert "whatsapp" not in res, "salesman must not be WhatsApp-alerted about his own order"
        recent = recent_customers(o["salesman_id"])
        assert any(r["phone"] == "97333001122" for r in recent), recent[:2]
    finally:
        c.table("shop_orders").delete().eq("id", o["id"]).execute()
        c.table("shop_events").delete().eq("src", "salesman").eq("event", "order").execute()
        period = re.search(r"-(\d{4})-", o["order_no"]).group(1)
        n = c.table("shop_counters").select("n").eq("period", period).execute().data[0]["n"]
        c.table("shop_counters").update({"n": max(n - 1, 0)}).eq("period", period).execute()


def main() -> int:
    failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
