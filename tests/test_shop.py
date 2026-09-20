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
    ctx = _ctx([_item("T02", 10.0)], shop_free_delivery_threshold_bhd="50", shop_delivery_fee_bhd="2", shop_min_order_bhd="15", shop_small_order_mode="block")
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


@test("badges: a real price cut → price_drop + was_bhd; a stale anchor never shows")
def _():
    from app.shop import _badges, _was_bhd
    ctx = _ctx([_item("UK15", 1.0, stock=50, sold_90d=30), _item("X01", 0.75, stock=50, sold_90d=30)])
    ctx["drops"] = {"UK15": {"was": 1.2, "now": 1.0, "on": "2026-09-10"}, "X01": {"was": 0.9, "now": 0.7, "on": "2026-09-10"}}
    b = _badges(ctx)
    assert "price_drop" in b["UK15"] and "price_drop" in b["X01"]
    assert _was_bhd(ctx, "UK15", 1.0) == 1.2                 # live price == cut price → honest anchor
    assert _was_bhd(ctx, "X01", 0.75) is None                # price moved again since → no anchor
    assert _was_bhd(ctx, "NOPE", 1.0) is None


@test("badges: clearance = real stock with a year+ of cover, worst first, capped, never a best seller")
def _():
    from app.shop import _badges
    items = [
        _item("SLOW1", 1.0, stock=600, sold_90d=10),     # 5400 days of cover
        _item("SLOW2", 1.0, stock=200, sold_90d=0),      # never sold → infinite cover
        _item("SLOW3", 1.0, stock=100, sold_90d=20),     # 450 days
        _item("FAST", 1.0, stock=100, sold_90d=900),     # 10 days → not aging
        _item("TINY", 1.0, stock=5, sold_90d=0),         # below the unit floor
        _item("BEST", 1.0, stock=500, sold_90d=1000),    # best seller in its category
    ]
    ctx = _ctx(items, shop_clearance_max=2, shop_best_seller_top_n=1)
    b = _badges(ctx)
    tagged = {c for c in ctx["order"] if "clearance" in b[c]}
    assert tagged == {"SLOW2", "SLOW1"}, tagged             # the two worst covers, capped at 2
    assert "clearance" not in b["FAST"] and "clearance" not in b["TINY"] and "clearance" not in b["BEST"]
    b3 = _badges(_ctx(items, shop_clearance_max=5, shop_best_seller_top_n=1))
    assert "clearance" in b3["SLOW3"]


@test("payload: a clearance item shows the real retail anchor even with retail compare off")
def _():
    from app.shop import catalog_payload  # noqa: F401
    src = (ROOT / "app" / "shop.py").read_text(encoding="utf-8")
    assert 'anchor_ok = show_compare or (clearance_retail and "clearance" in badges.get(code, []))' in src
    assert "shop_clearance_show_retail" in src


@test("payload: thumb_urls carries the 160/320/512 WebP set only when a product photo exists")
def _():
    from app.catalog import THUMB_SIZES, thumb_path
    from app.shop import _thumb_urls
    assert THUMB_SIZES == (160, 320, 512)
    assert _thumb_urls({"item_code": "X01", "product_image_url": None}) is None
    urls = _thumb_urls({"item_code": "X01", "product_image_url": "https://x/items/X01-product-1.jpg"})
    assert set(urls) == {"160", "320", "512"}, urls
    assert all(u.endswith(f"/thumbs/X01-product-{s}.webp") for s, u in urls.items()), urls
    assert thumb_path("X01", "product") == "thumbs/X01-product.jpg"      # legacy 256 JPEG path unchanged
    assert thumb_path("UK 15/A", "package", 320) == "thumbs/UK_15_A-package-320.webp"


@test("payload: the public item block carries thumb_urls next to thumb_url")
def _():
    src = (ROOT / "app" / "shop.py").read_text(encoding="utf-8")
    block = src.split("items.append({", 1)[1].split("})", 1)[0]
    assert '"thumb_urls": _thumb_urls(it)' in block


# ── marketplace: attribution, lifecycle, storefront card (pure) ───────────────

def _cust(**kw):
    base = {"id": 7, "phone": "97333001122", "salesman_id": None, "focus_salesman_name": None,
            "sticky_salesman_id": None, "last_order_at": None, "orders_count": 1}
    base.update(kw)
    return base


@test("attribution: admin assignment > focus map > sticky (inside window) > link > pick > default > unassigned")
def _():
    from datetime import datetime, timedelta, timezone
    from app.shop import resolve_salesman
    ctx = _ctx([_item("T02", 2.95)], shop_default_salesman="")
    ctx["salesmen"][0]["focus_name"] = "Furqan"
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    # no customer, no link, no pick → unassigned (queue)
    sm, how, conflict = resolve_salesman(ctx, phone="97333001122", session_ref=None, customer=_cust())
    assert sm is None and how == "unassigned" and not conflict
    # link wins for an unknown merchant
    sm, how, conflict = resolve_salesman(ctx, phone=None, session_ref="harsh", customer=_cust())
    assert sm["name"] == "Harsh Bhatia" and how == "session_ref" and not conflict
    # checkout pick and the default setting
    sm, how, _c = resolve_salesman(ctx, phone=None, session_ref=None, pick_id=2, customer=_cust())
    assert sm["id"] == 2 and how == "checkout_pick"
    ctx2 = _ctx([_item("T02", 2.95)], shop_default_salesman="Furqan Ahmed")
    sm, how, _c = resolve_salesman(ctx2, phone=None, session_ref=None, customer=_cust())
    assert sm["id"] == 1 and how == "default"
    # sticky inside the window beats a different rep's link, flagged as a conflict
    sm, how, conflict = resolve_salesman(ctx, phone=None, session_ref="harsh",
                                         customer=_cust(sticky_salesman_id=1, last_order_at=recent))
    assert sm["id"] == 1 and how == "sticky" and conflict
    # sticky past the window loses to the live link, still flagged
    sm, how, conflict = resolve_salesman(ctx, phone=None, session_ref="harsh",
                                         customer=_cust(sticky_salesman_id=1, last_order_at=old))
    assert sm["id"] == 2 and how == "session_ref" and conflict
    # sticky past the window with no link still routes to the sticky rep
    sm, how, conflict = resolve_salesman(ctx, phone=None, session_ref=None,
                                         customer=_cust(sticky_salesman_id=1, last_order_at=old))
    assert sm["id"] == 1 and how == "sticky" and not conflict
    # admin assignment beats everything, conflict flagged when the link disagrees
    sm, how, conflict = resolve_salesman(ctx, phone=None, session_ref="harsh",
                                         customer=_cust(salesman_id=1, sticky_salesman_id=2, last_order_at=recent))
    assert sm["id"] == 1 and how == "customer_admin" and conflict
    # focus map placeholder resolves by focus_name / name
    sm, how, _c = resolve_salesman(ctx, phone=None, session_ref=None, customer=_cust(focus_salesman_name="furqan"))
    assert sm["id"] == 1 and how == "focus_map"


@test("lifecycle: five merchant stages, storekeeper limited, every transition table entry is a real status")
def _():
    from app.shop import NEXT_STATUS, ROLE_STATUSES, STATUSES, STATUS_LABELS, TRACK_STEPS, order_steps
    assert set(STATUS_LABELS) == set(STATUSES)
    assert all(t in STATUSES for nxt in NEXT_STATUS.values() for t in nxt)
    assert "out_for_delivery" in NEXT_STATUS["confirmed"] and "out_for_delivery" in NEXT_STATUS["packed"]
    assert NEXT_STATUS["out_for_delivery"] == ("delivered", "cancelled")
    assert NEXT_STATUS["delivered"] == () and NEXT_STATUS["cancelled"] == ()
    assert ROLE_STATUSES["storekeeper"] == ("packed", "out_for_delivery")
    assert TRACK_STEPS == ("new", "confirmed", "packed", "out_for_delivery", "delivered")
    steps = order_steps({"status": "packed", "created_at": "a", "confirmed_at": "b", "packed_at": "c"})
    assert [s["done"] for s in steps] == [True, True, False, False, False]
    assert [s["current"] for s in steps] == [False, False, True, False, False]
    assert steps[2]["at"] == "c" and steps[3]["at"] is None
    delivered = order_steps({"status": "delivered", "created_at": "a", "delivered_at": "z"})
    assert all(s["done"] for s in delivered) and delivered[-1]["current"] and delivered[-1]["at"] == "z"
    cancelled = order_steps({"status": "cancelled", "created_at": "a"})
    assert not any(s["current"] for s in cancelled) and not any(s["done"] for s in cancelled)


@test("storefront: reserved slugs are refused, the rep card hides WhatsApp until the rep opts in")
def _():
    from app.shop import is_reserved_slug, rep_card
    ctx = _ctx([_item("T02", 2.95)])
    assert is_reserved_slug("cart", ctx) and is_reserved_slug("Search", ctx) and is_reserved_slug("", ctx)
    assert not is_reserved_slug("furqan", ctx)
    ctx["salesmen"][0].update({"phone": "97337158552", "public_whatsapp": False, "title": None})
    card = rep_card(ctx, "furqan")
    assert card["name"] == "Furqan Ahmed" and card["first_name"] == "Furqan" and card["whatsapp_url"] is None
    assert card["title"] == "YQ sales representative" and card["slug"] == "furqan"
    ctx["salesmen"][0]["public_whatsapp"] = True
    assert rep_card(ctx, "FURQAN")["whatsapp_url"].startswith("https://wa.me/97337158552?text=")
    ctx["salesmen"][0]["public_profile"] = False
    assert rep_card(ctx, "furqan") is None
    assert rep_card(ctx, "nobody") is None


@test("payload: volume tiers follow the shop_public_tiers switch; has_tiers stays")
def _():
    from app.shop import catalog_payload
    import app.shop as s
    ctx = _ctx([_item("T02", 2.95)], [_rule(5, "qty_tier", min_qty=12, pct_off=10)], shop_public_tiers="0")
    ctx["share_token"] = "tok-test-token-value"
    old = s._ctx_cache["ctx"]
    s._ctx_cache["ctx"], s._ctx_cache["at"] = ctx, s.time.time() + 10 ** 6
    try:
        p = catalog_payload("tok-test-token-value")
        it = p["items"][0]
        assert it["tiers"] == [] and it["has_tiers"] is True and p["settings"]["public_tiers"] is False
        assert p["rep"] is None and "areas" in p["settings"]
        ctx["settings"]["shop_public_tiers"] = "1"
        p = catalog_payload("tok-test-token-value")
        assert p["items"][0]["tiers"][0]["min_qty"] == 12 and p["settings"]["public_tiers"] is True
    finally:
        s._ctx_cache["ctx"], s._ctx_cache["at"] = old, 0.0


@test("links: shop_market_url turns rep links into /{slug} storefronts and tracking URLs onto the marketplace")
def _():
    import app.shop as s
    from app.shop_notify import _status_url
    real = s.shop_settings
    try:
        s.shop_settings = lambda force=False: {**s.SETTING_DEFAULTS, "shop_market_url": "https://market.example/"}
        assert s.market_base() == "https://market.example"
        assert s.salesman_link({"referral_code": "furqan"}) == "https://market.example/furqan"
        assert _status_url({"token": "abc123"}) == "https://market.example/o/abc123"
        s.shop_settings = lambda force=False: dict(s.SETTING_DEFAULTS)
        assert "/c/" in s.salesman_link({"referral_code": "furqan"}) or s.salesman_link({"referral_code": "furqan"}).endswith("?ref=furqan")
    finally:
        s.shop_settings = real


# ── rate limiting (pure) ──────────────────────────────────────────────────────

def _req(headers: dict, host: str = "10.0.0.9", path: str = "/x", method: str = "GET"):
    from starlette.requests import Request
    scope = {"type": "http", "method": method, "path": path, "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": (host, 1234), "server": ("testserver", 80), "scheme": "http"}
    return Request(scope)


@test("ratelimit: client ip is the N-th X-Forwarded-For entry from the right, never the left end")
def _():
    from app import ratelimit
    from app.config import settings
    old = settings.trusted_proxy_hops
    try:
        settings.trusted_proxy_hops = 1
        assert ratelimit.client_ip(_req({"x-forwarded-for": "9.9.9.9, 1.2.3.4"})) == "1.2.3.4"   # spoofed left entry ignored
        assert ratelimit.client_ip(_req({"x-forwarded-for": "1.2.3.4"})) == "1.2.3.4"
        assert ratelimit.client_ip(_req({})) == "10.0.0.9"                                        # no header → socket peer
        settings.trusted_proxy_hops = 0
        assert ratelimit.client_ip(_req({"x-forwarded-for": "1.2.3.4"})) == "10.0.0.9"            # header not trusted locally
        settings.trusted_proxy_hops = 2
        assert ratelimit.client_ip(_req({"x-forwarded-for": "5.5.5.5, 1.2.3.4, 7.7.7.7"})) == "1.2.3.4"
        assert ratelimit.client_ip(_req({"x-forwarded-for": "7.7.7.7"})) == "10.0.0.9"            # fewer entries than hops → peer
    finally:
        settings.trusted_proxy_hops = old


@test("ratelimit: bearer calls are keyed per user, public calls per client ip")
def _():
    from app import ratelimit
    from app.config import settings
    old = settings.trusted_proxy_hops
    try:
        settings.trusted_proxy_hops = 1
        k1 = ratelimit.rate_limit_key(_req({"authorization": "Bearer eyJhbGciOi.first-token.signature-1234567890"}))
        k2 = ratelimit.rate_limit_key(_req({"authorization": "Bearer eyJhbGciOi.other-token.signature-1234567890"}))
        k1b = ratelimit.rate_limit_key(_req({"authorization": "Bearer eyJhbGciOi.first-token.signature-1234567890",
                                             "x-forwarded-for": "8.8.8.8"}))
        assert k1.startswith("u:") and k1 != k2 and k1 == k1b, (k1, k2, k1b)          # same user, different ip → same bucket
        assert ratelimit.rate_limit_key(_req({"x-forwarded-for": "1.2.3.4"})) == "1.2.3.4"
        assert ratelimit.rate_limit_key(_req({"authorization": "Bearer x"})) == "10.0.0.9"    # junk header → ip
    finally:
        settings.trusted_proxy_hops = old


@test("ratelimit: middleware limits undecorated routes per client and returns 429 with CORS")
def _():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.testclient import TestClient
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from app.config import settings
    from app.ratelimit import rate_limit_key
    import app.main as m
    assert any(mw.cls is SlowAPIMiddleware for mw in m.app.user_middleware), "SlowAPIMiddleware not installed on the app"
    assert m.limiter._default_limits, "no default limit configured"
    old = settings.trusted_proxy_hops
    try:
        settings.trusted_proxy_hops = 1
        lim = Limiter(key_func=rate_limit_key, default_limits=["2/minute"])
        api = FastAPI()
        api.state.limiter = lim
        api.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        api.add_middleware(SlowAPIMiddleware)
        api.add_middleware(CORSMiddleware, allow_origins=["https://shop.example"], allow_methods=["*"], allow_headers=["*"])

        @api.get("/plain")
        def plain():
            return {"ok": True}

        c = TestClient(api)
        a = {"x-forwarded-for": "1.1.1.1", "origin": "https://shop.example"}
        b = {"x-forwarded-for": "2.2.2.2", "origin": "https://shop.example"}
        assert c.get("/plain", headers=a).status_code == 200
        assert c.get("/plain", headers=a).status_code == 200
        r = c.get("/plain", headers=a)
        assert r.status_code == 429, r.status_code
        assert r.headers.get("access-control-allow-origin") == "https://shop.example", "429 must keep CORS headers"
        assert c.get("/plain", headers=b).status_code == 200, "a different client must have its own bucket"
    finally:
        settings.trusted_proxy_hops = old


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
    if not _migrated():
        print("   SKIP — no database (CI without secrets) or scripts/shop_migration.sql not applied")
        return
    from app.shop import STATUSES
    r = list_orders(status="confirmed,packed", limit=5)
    assert "counts" in r and set(r["counts"]) == set(STATUSES), r.get("counts")
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
        try:   # the merchant record the order created (marketplace_migration.sql)
            c.table("shop_customers").delete().eq("phone", "97333001122").execute()
        except Exception:  # noqa: BLE001
            pass


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



# ── campaigns + promises (marketplace v2.1) ───────────────────────────────────

@test("campaigns: live window shown, future/past hidden, rule-linked inherits the real end")
def _():
    from datetime import datetime, timedelta, timezone
    from app.shop import campaigns_payload
    now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    rule = _rule(7, "cart_value", ends_at=(now + timedelta(days=3)).isoformat(), min_value_bhd=30, pct_off=5)
    ctx = {"rules": [rule], "campaigns": [
        {"id": 1, "title": "Clearance week", "placement": ["strip", "aside"], "audience": "all", "cta_to": "/shop?f=clearance"},
        {"id": 2, "title": "Future", "starts_at": (now + timedelta(days=1)).isoformat(), "placement": ["strip"]},
        {"id": 3, "title": "Past", "ends_at": (now - timedelta(days=1)).isoformat(), "placement": ["strip"]},
        {"id": 4, "title": "With rule", "rule_id": 7, "placement": ["hero"], "sponsored": True, "sponsor_name": "VFAN"},
        {"id": 5, "title": "Dead rule", "rule_id": 99, "placement": ["strip"]},
        {"id": 6, "title": "Bad placement", "placement": ["banner"], "audience": "vip"},
    ]}
    out = campaigns_payload(ctx, now)
    ids = [c["id"] for c in out]
    assert ids == [1, 4, 6], ids
    by = {c["id"]: c for c in out}
    assert by[4]["ends_at"] == rule["ends_at"] and by[4]["sponsor_name"] == "VFAN"
    assert by[6]["placement"] == ["strip"] and by[6]["audience"] == "all"
    assert by[1]["sponsor_name"] is None


@test("promises: defaults are the four wholesale claims with Arabic; a threshold rewrites the delivery line")
def _():
    from app.shop import SETTING_DEFAULTS, promises_payload
    vals = dict(SETTING_DEFAULTS)
    p = promises_payload(vals)
    assert [x["key"] for x in p] == ["delivery", "trade", "stock", "rep"], p
    assert p[0]["en"] == "Free delivery across Bahrain" and p[0]["ar"]
    assert all(x["en"] and x["ar"] and x["icon"] and x["to"] for x in p), p
    assert [x["icon"] for x in p] == ["truck", "tag", "pulse", "shield"], p
    assert not any("minimum" in x["en"].lower() for x in p), "the wholesale minimum is real — never promise 'no minimum'"
    # 20-Sep-2026: the catalog ships a DATED stock snapshot (`stock_as_of`, printed in the market
    # footer), so no promise may claim the stock is live/real-time — the page would contradict itself.
    import re
    for x in p:
        low = f"{x['en']} {x['ar'] or ''}".lower()          # \b so "delivery" is not read as "live"
        for word in (r"\blive\b", r"\breal[- ]?time\b", "مباشر", "لحظي"):
            assert not re.search(word, low), f"the stock snapshot is dated — a promise may not claim {word}: {x}"
    vals["shop_free_delivery_threshold_bhd"] = "25"
    p2 = promises_payload(vals)
    assert p2[0]["en"] == "Free delivery over BHD 25.000", p2[0]
    vals["shop_market_promises"] = "not json"
    assert promises_payload(vals) == []


@test("campaigns: validation catches the mistakes an admin would make")
def _():
    from app.shop import ShopError, validate_campaign
    def bad(payload, why):
        try:
            validate_campaign(payload)
        except ShopError as e:
            assert why in str(e).lower(), (why, str(e))
        else:
            raise AssertionError(f"expected error {why!r} for {payload}")
    bad({"title": "ab"}, "title")
    bad({"title": "Sale", "cta_to": "javascript:alert(1)"}, "link")
    bad({"title": "Sale", "placement": ["banner"]}, "placement")
    bad({"title": "Sale", "audience": "vip"}, "audience")
    bad({"title": "Sale", "starts_at": "2026-09-20T00:00:00+00:00", "ends_at": "2026-09-10T00:00:00+00:00"}, "end")
    bad({"title": "Sale", "sponsored": True}, "sponsor")
    bad({"title": "Sale", "placement": ["category"]}, "category")
    row = validate_campaign({"title": " Back to school ", "placement": ["strip", "hero"], "category": "cable", "cta_to": "/shop?f=new", "sponsored": False})
    assert row["title"] == "Back to school" and row["category"] == "CABLE" and row["placement"] == ["strip", "hero"]
    assert row["is_active"] is True and row["sort_order"] == 100


# ── wholesale order engine ────────────────────────────────────────────────────

@test("wholesale: under the minimum in request mode the quote is submittable, flagged small, with gap fillers")
def _():
    from app.shop import price_cart
    items = [_item("A", 1.0, stock=50, cat="CABLE"), _item("B", 2.5, stock=50, cat="CABLE", sold_90d=500),
             _item("C", 10.0, stock=0, cat="CHARGER"), _item("D", 4.0, stock=20, cat="CHARGER", sold_90d=900)]
    ctx = _ctx(items, shop_min_order_bhd="20", shop_small_order_mode="request", shop_small_order_fee_bhd="1.5")
    ctx["pairs"] = {"A": ["B"]}
    q = price_cart([{"item_code": "A", "qty": 5}], ctx=ctx)
    assert q["can_submit"] is True and q["block_reason"] is None, q["block_reason"]
    m = q["minimum"]
    assert m and m["met"] is False and abs(m["remaining_bhd"] - 15.0) < 1e-6 and m["kind"] == "small" and m["fee_bhd"] == 1.5
    codes = [g["item_code"] for g in q["gap_suggestions"]]
    assert codes and codes[0] == "B", codes                       # the pair comes first
    assert "C" not in codes and "A" not in codes                   # never out of stock, never a cart line
    for g in q["gap_suggestions"]:
        assert g["value_bhd"] >= 0 and g["qty"] >= 1
    assert q["gap_suggestions"][0]["closes_gap"] is True


@test("wholesale: block mode keeps the refusal; allow mode is silent; met minimum is standard")
def _():
    from app.shop import price_cart
    items = [_item("A", 1.0, stock=50)]
    ctx = _ctx(items, shop_min_order_bhd="20", shop_small_order_mode="block")
    q = price_cart([{"item_code": "A", "qty": 5}], ctx=ctx)
    assert q["can_submit"] is False and "Minimum order" in q["block_reason"]
    ctx = _ctx(items, shop_min_order_bhd="20", shop_small_order_mode="allow")
    q = price_cart([{"item_code": "A", "qty": 5}], ctx=ctx)
    assert q["can_submit"] is True and q["minimum"]["kind"] == "small" and q["minimum"]["fee_bhd"] == 0.0
    q = price_cart([{"item_code": "A", "qty": 25}], ctx=ctx)
    assert q["minimum"]["met"] is True and q["minimum"]["kind"] == "standard" and q["gap_suggestions"] == []
    ctx = _ctx(items)   # no minimum configured → no engine
    q = price_cart([{"item_code": "A", "qty": 1}], ctx=ctx)
    assert q["minimum"] is None and q["gap_suggestions"] == []


# ── marketplace v3 (17-Sep-2026) ──────────────────────────────────────────────

class _FakeDB:
    """A stand-in for the Supabase client: every builder call is recorded and returns itself;
    execute() hands back canned rows. Nothing here ever reaches the network."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def call(*a, **kw):
            self.calls.append((name, a, kw))
            return self
        return call

    def execute(self):
        from types import SimpleNamespace
        return SimpleNamespace(data=self.rows, count=None)


def _with_cached_ctx(ctx):
    """Serve `ctx` as the cached catalog context; returns a restore function."""
    import app.shop as s
    old = s._ctx_cache["ctx"]
    s._ctx_cache["ctx"], s._ctx_cache["at"] = ctx, s.time.time() + 10 ** 6

    def restore():
        s._ctx_cache["ctx"], s._ctx_cache["at"] = old, 0.0
    return restore


@test("promises: validation rejects what would blank the bar, accepts and normalises a good list")
def _():
    import json
    from app.shop import SETTING_DEFAULTS, ShopError, validate_promises

    def bad(raw, why):
        try:
            validate_promises(raw)
        except ShopError as e:
            assert why in str(e).lower(), (why, str(e))
        else:
            raise AssertionError(f"expected error {why!r} for {raw!r}")
    bad("not json", "json")
    bad("", "json")
    bad(None, "json")
    bad('{"key": "delivery", "en": "Free"}', "list")
    bad("[1, 2]", "object")
    bad('[{"en": "No key"}]', "key")
    bad('[{"key": "  ", "en": "Blank key"}]', "key")
    bad('[{"key": "delivery"}]', "english")
    bad('[{"key": "delivery", "en": ""}]', "english")
    bad('[{"key": "delivery", "en": "Free", "ar": 5}]', "text")
    bad('[{"key": "a", "en": "A"}, {"key": "A", "en": "B"}]', "unique")
    bad('[{"key": "x", "en": "X", "to": "javascript:alert(1)"}]', "link")
    bad(json.dumps([{"key": f"k{i}", "en": "x"} for i in range(7)]), "at most 6")
    # the shipped default passes and survives a round trip unchanged (Arabic kept, not \u-escaped)
    default = SETTING_DEFAULTS["shop_market_promises"]
    assert json.loads(validate_promises(default)) == json.loads(default)
    assert "توصيل" in validate_promises(default)
    good = validate_promises('[{"key": " trade ", "en": " Trade prices for shops ", "ar": "", "icon": null, "extra": 1}]')
    assert json.loads(good) == [{"key": "trade", "en": "Trade prices for shops"}], good
    assert validate_promises("[]") == "[]"                    # an empty bar is a deliberate choice, allowed
    assert json.loads(validate_promises([{"key": "rep", "en": "Rep", "to": "/about#how"}]))[0]["to"] == "/about#how"


@test("promises: update_shop_settings validates before the first write; the route answers 400")
def _():
    import json
    from fastapi.testclient import TestClient
    import app.database as db
    import app.main as m
    import app.shop as s
    from app.auth import CurrentUser, require_admin
    from app.shop import ShopError
    real_client, real_settings, real_db_client = s.get_client, s.shop_settings, db.get_client
    fake = _FakeDB()
    # Both halves run against the fake — the shop's client AND app.database's (the audit log imports it
    # at call time) — so even a regressed validator can never write to the live settings/audit tables.
    s.get_client = db.get_client = lambda: fake
    s.shop_settings = lambda force=False: dict(s.SETTING_DEFAULTS)
    m.app.dependency_overrides[require_admin] = lambda: CurrentUser(user_id="test", email="test@example.com", role="admin")
    try:
        try:
            s.update_shop_settings({"shop_min_order_bhd": "20", "shop_market_promises": "not json"}, by="test")
            raise AssertionError("expected ShopError")
        except ShopError:
            pass
        assert not any(c[0] == "upsert" for c in fake.calls), "a bad promise list must not half-save the settings"
        s.update_shop_settings({"shop_market_promises": '[{"key": "trade", "en": " Trade prices "}]',
                                "not_a_shop_key": "x"}, by="test")
        ups = [c[1][0] for c in fake.calls if c[0] == "upsert"]
        assert [u["key"] for u in ups] == ["shop_market_promises"], ups
        assert json.loads(ups[0]["value"]) == [{"key": "trade", "en": "Trade prices"}], ups[0]
        # the route: ShopError → HTTP 400 with the message, and nothing is written (checked on the fake)
        fake.calls.clear()
        r = TestClient(m.app).put("/settings/shop", json={"settings": {"shop_market_promises": "[{\"en\": \"no key\"}]"}})
        assert not any(c[0] in ("upsert", "insert", "update") for c in fake.calls), \
            ("an invalid promise list must not be written", fake.calls)
        assert r.status_code == 400, (r.status_code, r.text[:200])
        assert "key" in r.json()["detail"].lower(), r.json()
    finally:
        m.app.dependency_overrides.pop(require_admin, None)
        s.get_client, s.shop_settings, db.get_client = real_client, real_settings, real_db_client


@test("payload: catalog settings carry small_order_mode, normalised to request|allow|block")
def _():
    from app.shop import catalog_payload
    ctx = _ctx([_item("T02", 2.95)], shop_small_order_mode=" ALLOW ")
    ctx["share_token"] = "tok-test-token-value"
    restore = _with_cached_ctx(ctx)
    try:
        p = catalog_payload("tok-test-token-value")
        assert p["settings"]["small_order_mode"] == "allow", p["settings"]
        ctx["settings"]["shop_small_order_mode"] = "block"
        assert catalog_payload("tok-test-token-value")["settings"]["small_order_mode"] == "block"
        ctx["settings"]["shop_small_order_mode"] = "nonsense"
        assert catalog_payload("tok-test-token-value")["settings"]["small_order_mode"] == "request"
        ctx["settings"]["shop_small_order_mode"] = ""
        assert catalog_payload("tok-test-token-value")["settings"]["small_order_mode"] == "request"
    finally:
        restore()


@test("wholesale: on an exact gap-filler tie a clearing line ranks first — and only on an exact tie")
def _():
    from app.shop import _badges, gap_fillers
    items = [
        _item("CART", 1.0, stock=5, cat="CHARGER"),            # the cart line
        _item("AAA", 4.0, stock=5, cat="CABLE"),               # 16.000 → overshoot 1, not clearing (below the unit floor)
        _item("NEAR", 3.0, stock=5, cat="CABLE"),              # 15.000 → closes exactly, not clearing
        _item("ZZZ", 4.0, stock=500, cat="CABLE"),             # 16.000 → overshoot 1, CLEARING (never sold, 500 on hand)
        _item("FAR", 9.0, stock=5, cat="CABLE"),               # 18.000 → overshoot 3
    ]
    ctx = _ctx(items)
    b = _badges(ctx)
    assert "clearance" in b["ZZZ"] and "clearance" not in b["AAA"] and "clearance" not in b["NEAR"], b
    out = [g["item_code"] for g in gap_fillers(ctx, ["CART"], 15.0)]
    # NEAR closes with no overshoot, so the clearing line never jumps it; ZZZ beats AAA on the exact tie
    # (alphabetically AAA used to win); FAR overshoots more and stays behind both.
    assert out == ["NEAR", "ZZZ", "AAA", "FAR"], out
    # without the clearance badge the old alphabetical tie-break is unchanged
    ctx2 = _ctx(items, shop_clearance_max=0)
    assert [g["item_code"] for g in gap_fillers(ctx2, ["CART"], 15.0)] == ["NEAR", "AAA", "ZZZ", "FAR"]
    # a tier beats clearance: a pair of the cart line ranks above a clearing line with the same numbers
    ctx3 = _ctx(items)
    ctx3["pairs"] = {"CART": ["AAA"]}
    assert [g["item_code"] for g in gap_fillers(ctx3, ["CART"], 15.0)][0] == "AAA"


@test("campaigns: creative fields are optional, enums and the 3-product cap are enforced")
def _():
    from app.shop import ShopError, validate_campaign

    def bad(payload, why):
        try:
            validate_campaign(payload)
        except ShopError as e:
            assert why in str(e).lower(), (why, str(e))
        else:
            raise AssertionError(f"expected error {why!r} for {payload}")
    bad({"title": "Sale", "image_fit": "stretch"}, "fit")
    bad({"title": "Sale", "canvas": "neon"}, "canvas")
    bad({"title": "Sale", "product_codes": ["A", "B", "C", "D"]}, "at most 3")
    bad({"title": "Sale", "product_codes": "X01"}, "list")
    bad({"title": "Sale", "product_codes": ["X01", 5]}, "list")
    # nothing creative given → the DB defaults, no composition
    row = validate_campaign({"title": "Plain"})
    assert row["image_fit"] == "contain" and row["canvas"] == "lilac", row
    assert row["product_codes"] is None and row["image_url_600"] is None
    assert {"image_url_600", "image_fit", "product_codes", "canvas"} <= set(row)
    # codes keep their case and inner spaces; blanks and duplicates drop before the cap is counted
    row = validate_campaign({"title": "Compose", "product_codes": ["  P05 1Mtr ", "UK20 (New)", "", "p05 1mtr", "X13-C"],
                             "canvas": "night", "image_fit": "cover", "image_url_600": " https://x/c-600.webp "})
    assert row["product_codes"] == ["P05 1Mtr", "UK20 (New)", "X13-C"], row["product_codes"]
    assert row["canvas"] == "night" and row["image_fit"] == "cover" and row["image_url_600"] == "https://x/c-600.webp"
    # editing: an explicit null clears the composition and the 600 w image; an absent/null fit or canvas keeps the stored one
    existing = dict(row, id=9, placement=["hero"], audience="all", cta_to="/shop")
    upd = validate_campaign({"product_codes": None, "image_url_600": None, "canvas": None}, existing)
    assert upd["product_codes"] is None and upd["image_url_600"] is None and upd["canvas"] == "night", upd
    assert validate_campaign({"line": "New line"}, existing)["product_codes"] == ["P05 1Mtr", "UK20 (New)", "X13-C"]
    assert validate_campaign({"product_codes": []}, existing)["product_codes"] is None


@test("campaigns: payload carries the creative with defaults; codes the catalog no longer has drop out")
def _():
    from datetime import datetime, timezone
    from app.shop import campaigns_payload
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    ctx = _ctx([_item("P05 1Mtr", 0.8), _item("UK04-C", 1.2), _item("X13-L", 0.5)])
    ctx["campaigns"] = [
        {"id": 1, "title": "Legacy row", "placement": ["strip"]},                              # pre-migration shape
        {"id": 2, "title": "Composed", "placement": ["hero"], "canvas": "mint", "image_fit": "cover",
         "product_codes": ["p05 1mtr", "GONE-1", "UK04-C", "UK04-C", "X13-L", "P05 1Mtr"],
         "image_url": "https://x/c.webp", "image_url_600": "https://x/c-600.webp"},
        {"id": 3, "title": "Bad values", "placement": ["strip"], "canvas": "neon", "image_fit": "fill",
         "product_codes": ["GONE-1", "GONE-2"]},
    ]
    out = {c["id"]: c for c in campaigns_payload(ctx, now)}
    for c in out.values():
        assert {"image_url_600", "image_fit", "product_codes", "canvas"} <= set(c), c
    assert out[1]["image_fit"] == "contain" and out[1]["canvas"] == "lilac"
    assert out[1]["product_codes"] is None and out[1]["image_url_600"] is None
    assert out[2]["product_codes"] == ["P05 1Mtr", "UK04-C", "X13-L"], out[2]["product_codes"]   # catalog spelling, deduped, ≤3
    assert out[2]["canvas"] == "mint" and out[2]["image_fit"] == "cover" and out[2]["image_url_600"] == "https://x/c-600.webp"
    assert out[3]["canvas"] == "lilac" and out[3]["image_fit"] == "contain" and out[3]["product_codes"] is None
    # a hand-built context without items (like the window test above) still renders
    assert campaigns_payload({"rules": [], "campaigns": [ctx["campaigns"][1]]}, now)[0]["product_codes"] is None


@test("orders: orders_by_tokens selects order_kind and returns it (standard when unset)")
def _():
    import app.shop as s
    rows = [{"order_no": "YQ-2609-0101", "token": "t" * 24, "status": "new", "total_bhd": 12.5,
             "total_confirmed_bhd": None, "items_count": 2, "units_count": 7, "created_at": "2026-09-17T08:00:00+00:00",
             "updated_at": None, "salesman_name": None, "expected_delivery": None, "order_kind": "small"},
            {"order_no": "YQ-2609-0100", "token": "u" * 24, "status": "confirmed", "total_bhd": 40.0,
             "total_confirmed_bhd": 38.0, "items_count": 5, "units_count": 30, "created_at": "2026-09-16T08:00:00+00:00",
             "updated_at": None, "salesman_name": "Furqan Ahmed", "expected_delivery": None, "order_kind": None}]
    fake = _FakeDB(rows)
    real = s.get_client
    try:
        s.get_client = lambda: fake
        out = s.orders_by_tokens(["t" * 24, "u" * 24, "short"])
    finally:
        s.get_client = real
    select = next(c[1][0] for c in fake.calls if c[0] == "select")
    assert "order_kind" in [x.strip() for x in select.split(",")], select
    assert [o["order_kind"] for o in out] == ["small", "standard"], out
    assert out[1]["total_bhd"] == 38.0 and out[0]["can_cancel"] is True


@test("orders: the marketplace order response carries order_kind")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    import app.shop as s
    import app.shop_notify as n
    placed = {"id": 1, "order_no": "YQ-2609-0102", "token": "v" * 24, "status": "new", "status_url": "/o/" + "v" * 24,
              "order_kind": "small", "attribution_source": "unassigned", "salesman": None, "has_backorder": False,
              "totals": {"ok": True, "total_bhd": 12.5}}
    real = (s.create_order, s.market_enabled, n.notify_new_order, n.customer_to_salesman_wa_url,
            n.customer_to_salesman_email_url)
    body = {"lines": [{"item_code": "T02", "qty": 1}], "customer": {"name": "Test Shop", "phone": "33001122"},
            "device_id": "dev-test", "client_order_id": "coid-test"}
    try:
        s.create_order = lambda *a, **kw: dict(placed)
        s.market_enabled = lambda: True
        n.notify_new_order = lambda *a, **kw: {}
        n.customer_to_salesman_wa_url = lambda o: None
        n.customer_to_salesman_email_url = lambda o: None
        c = TestClient(m.app)
        r = c.post("/public/market/order", json=body)
        assert r.status_code == 200, (r.status_code, r.text[:200])
        assert r.json()["order_kind"] == "small", r.json()
        s.create_order = lambda *a, **kw: {**placed, "order_kind": None, "duplicate": True}
        r = c.post("/public/market/order", json=body)
        assert r.status_code == 200 and r.json()["order_kind"] == "standard", r.text[:200]
    finally:
        (s.create_order, s.market_enabled, n.notify_new_order, n.customer_to_salesman_wa_url,
         n.customer_to_salesman_email_url) = real


@test("payload: a clearance item's price_bhd is the price-book trade rate — never a markdown")
def _():
    from app.shop import catalog_payload, money
    items = [_item("CLR", 0.4, stock=600, b2c=1.5), _item("REG", 1.25, stock=20, sold_90d=300, b2c=2.0)]
    ctx = _ctx(items, shop_show_retail_compare="0", shop_clearance_show_retail="1")
    ctx["share_token"] = "tok-test-token-value"
    restore = _with_cached_ctx(ctx)
    try:
        for compare in ("0", "1"):
            ctx["settings"]["shop_show_retail_compare"] = compare
            p = {i["item_code"]: i for i in catalog_payload("tok-test-token-value")["items"]}
            assert "clearance" in p["CLR"]["badges"], p["CLR"]
            assert p["CLR"]["price_bhd"] == money(items[0]["standard_rate"]) == 0.4, p["CLR"]
            assert p["CLR"]["compare_at_bhd"] == 1.5 and p["CLR"]["was_bhd"] is None, p["CLR"]
            assert p["REG"]["price_bhd"] == 1.25
    finally:
        restore()
    if not _migrated():
        print("   SKIP (live half) — no database or scripts/shop_migration.sql not applied")
        return
    from app.catalog import share_token
    from app.shop import context
    live = context()
    pub = catalog_payload(share_token(create=False))
    for it in pub["items"]:
        if "clearance" in it["badges"]:
            want = live["items"][it["item_code"]].get("standard_rate")
            assert it["price_bhd"] == (money(want) if want is not None else None), (it["item_code"], it["price_bhd"], want)

if __name__ == "__main__":
    sys.exit(main())
