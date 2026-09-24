"""R1b "Coming soon" (WEKOME) tests — payload whitelist, auto-retire, labels, reserved slugs,
routes, migration shape and the importer's dry-run invariants.

    python -m tests.test_r1b_upcoming

Same lightweight runner as tests/test_v3.py (no pytest). Pure tests need no database. The
importer test reads the shipment workbooks (Wekome/Wekome Shipments/, or YQ_WEKOME_DIR) and
SKIPS when they are not on this machine; it never writes anywhere.
"""
from __future__ import annotations

import io
import json
import re
import sys
import traceback
from datetime import date
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


def _row(**kw) -> dict:
    """A fat DB-shaped row — with keys that must NEVER reach the public shape."""
    base = {"id": 7, "brand": "WEKOME", "model_code": "WS-55", "category": "Wireless Audio",
            "name_en": "WK Design TWS Wireless Earbuds (Gen 5)", "name_ar": "سماعات أذن لاسلكية TWS من WK Design (الجيل 5)",
            "spec_en": "Bluetooth 5.3 · 230 mAh case · about 4 h per charge", "spec_ar": "Bluetooth 5.3 · علبة شحن 230 mAh",
            "variants": [{"label": "Black", "label_ar": "أسود", "comps": {"colour": "Black"}, "qty1": 500, "unit_price": 1.2},
                         {"label": "White", "label_ar": "أبيض", "comps": {"colour": "White"}}],
            "photo_url": "https://x/catalog/upcoming/WS-55-product.jpg",
            "photo_thumb_urls": {"160": "u160", "320": "u320", "512": "u512"},
            "box_url": "https://x/catalog/upcoming/WS-55-box.jpg", "shipment_ref": "AS2026072701",
            "expected_month": "2026-10-01", "expected_label_en": None, "expected_label_ar": None,
            "status": "published", "catalog_item_code": None, "sort_order": 3, "created_by": "import",
            "created_at": "2026-09-24T00:00:00+00:00", "updated_at": "2026-09-24T00:00:00+00:00",
            # would-be leaks: none of these columns exist, but a future join must not slip through either
            "unit_price_bhd": 1.234, "landed_cost": 0.9, "qty_ordered": 1200, "invoice_amount": 1440.0}
    base.update(kw)
    return base


# ── public shape ───────────────────────────────────────────────────────────────

@test("upcoming: the public item is the whitelist only — no price, cost, qty or internal field")
def _():
    from app.upcoming import PUBLIC_FIELDS, assert_no_money, public_item
    it = public_item(_row(), today=date(2026, 9, 24))
    assert set(it) == set(PUBLIC_FIELDS), sorted(set(it) ^ set(PUBLIC_FIELDS))
    for leak in ("unit_price_bhd", "landed_cost", "qty_ordered", "invoice_amount", "shipment_ref", "created_by", "catalog_item_code"):
        assert leak not in it, leak
    assert it["variants"] == [{"label": "Black", "label_ar": "أسود"}, {"label": "White", "label_ar": "أبيض"}], it["variants"]
    assert_no_money(it)          # keys anywhere, recursively
    assert it["expected_label_en"] == "Arriving October" and it["expected_label_ar"] == "تصل في أكتوبر"
    assert it["photo_thumb_urls"]["320"] == "u320"


@test("upcoming: public_payload counts live cards and carries the month as data; assert_no_money catches a leak")
def _():
    from app.upcoming import assert_no_money, public_payload
    rows = [_row(id=1, model_code="WS-55"), _row(id=2, model_code="WS-56", status="draft"),
            _row(id=3, model_code="WDC-C37", category="Data Cables", expected_month="2026-11-01"),
            _row(id=4, model_code="WG-08", status="withdrawn")]
    p = public_payload(rows, set(), today=date(2026, 9, 24))
    assert p["count"] == 2 and [i["model_code"] for i in p["items"]] == ["WS-55", "WDC-C37"], p
    assert p["brand"] == "WEKOME" and p["expected_label_en"] in ("Arriving October", "Arriving November")
    assert_no_money(p)
    off = public_payload(rows, set(), enabled=False)
    assert off["count"] == 0 and off["items"] == [] and off["enabled"] is False
    try:
        assert_no_money({"items": [{"model_code": "X", "price_bhd": 1}]})
    except AssertionError as e:
        assert "price_bhd" in str(e)
    else:
        raise AssertionError("a price key slipped through assert_no_money")


# ── the rules ──────────────────────────────────────────────────────────────────

@test("upcoming: a card retires the moment its catalog code names a live item; unlinked or dead codes keep it")
def _():
    from app.upcoming import is_retired, visible_items
    live = {"WK-WS55", "X05"}
    assert is_retired(_row(catalog_item_code="wk-ws55"), live)            # case-insensitive
    assert not is_retired(_row(catalog_item_code=None), live)
    assert not is_retired(_row(catalog_item_code="WK-WS99"), live)         # linked to a code that is not (yet) live
    rows = [_row(id=1, model_code="WS-55", catalog_item_code="WK-WS55"), _row(id=2, model_code="WS-56", sort_order=1),
            _row(id=3, model_code="WS-57", status="arrived"), _row(id=4, model_code="WS-58", sort_order=None)]
    vis = visible_items(rows, live, today=date(2026, 9, 24))
    assert [v["model_code"] for v in vis] == ["WS-56", "WS-58"], [v["model_code"] for v in vis]


@test("upcoming: the arrival label derives from the month and drops to 'Arriving soon' once it has passed")
def _():
    from app.upcoming import UpcomingError, expected_labels, parse_month
    assert expected_labels("2026-10-01", today=date(2026, 9, 24)) == ("Arriving October", "تصل في أكتوبر")
    assert expected_labels("2026-10", today=date(2026, 10, 31)) == ("Arriving October", "تصل في أكتوبر")   # still that month
    assert expected_labels("2026-10-01", today=date(2026, 11, 1)) == ("Arriving soon", "تصل قريبًا")     # passed
    assert expected_labels(None) == ("Arriving soon", "تصل قريبًا")
    assert expected_labels("2026-10-01", "Landing mid-October", "منتصف أكتوبر", today=date(2026, 9, 24)) == ("Landing mid-October", "منتصف أكتوبر")
    assert expected_labels("2026-10-01", "Landing mid-October", None, today=date(2026, 11, 2)) == ("Arriving soon", "تصل قريبًا")
    assert parse_month("2026-10") == date(2026, 10, 1) and parse_month(date(2026, 10, 15)) == date(2026, 10, 1)
    for bad in ("October", "2026-13", "10/2026"):
        try:
            parse_month(bad)
        except UpcomingError:
            pass
        else:
            raise AssertionError(f"accepted {bad!r}")


# ── reserved slugs in every place ─────────────────────────────────────────────

@test("reserved: brands / wekome / coming-soon are reserved in shop.py, MarketApp.tsx, catalog-prefetch.js and the migration")
def _():
    from app.shop import _RESERVED_FALLBACK, is_reserved_slug
    slugs = ("brands", "wekome", "coming-soon")
    for s in slugs:
        assert s in _RESERVED_FALLBACK and is_reserved_slug(s), s
        assert is_reserved_slug(s.upper()), s
    app_tsx = (ROOT / "web" / "src" / "MarketApp.tsx").read_text(encoding="utf-8")
    m = re.search(r"export const RESERVED = new Set\(\[(.*?)\]\)", app_tsx, re.S)
    assert m, "RESERVED set not found in MarketApp.tsx"
    web = set(re.findall(r"'([^']+)'", m.group(1)))
    prefetch = (ROOT / "web" / "public" / "catalog-prefetch.js").read_text(encoding="utf-8")
    m2 = re.search(r"var reserved = \{(.*?)\}", prefetch, re.S)
    assert m2, "reserved map not found in catalog-prefetch.js"
    pre = set(re.findall(r"'?([a-z][a-z0-9-]*)'?\s*:\s*1", m2.group(1)))
    sql = (ROOT / "scripts" / "shop_upcoming_migration.sql").read_text(encoding="utf-8")
    for s in slugs:
        assert s in web, f"{s} missing from MarketApp.tsx RESERVED"
        assert s in pre, f"{s} missing from catalog-prefetch.js"
        assert f"('{s}'" in sql, f"{s} missing from the migration insert"
    # the three lists agree with each other on the marketplace's own words
    assert web == pre, sorted(web ^ pre)
    assert {"wekome", "coming-soon"} <= _RESERVED_FALLBACK
    # the routes exist and redirect to the brand page
    assert '/brands/:brand' in app_tsx and 'path="/wekome"' in app_tsx and 'path="/coming-soon"' in app_tsx


# ── routes ────────────────────────────────────────────────────────────────────

@test("api: the four upcoming routes register on a bare app with the right methods")
def _():
    from fastapi import FastAPI
    from slowapi import Limiter
    from app.ratelimit import rate_limit_key
    from app.shop_api import register
    api = FastAPI()
    lim = Limiter(key_func=rate_limit_key)
    api.state.limiter = lim
    register(api, lim)
    routes = {(r.path, m) for r in api.routes for m in (getattr(r, "methods", None) or ())}
    for want in (("/public/market/upcoming", "GET"), ("/public/market/upcoming/interest", "POST"),
                 ("/shop/upcoming", "GET"), ("/shop/upcoming/{item_id}", "PATCH"), ("/shop/upcoming/interest", "GET")):
        assert want in routes, want


# ── migration shape ───────────────────────────────────────────────────────────

@test("migration: shop_upcoming_items has no price/cost/qty column, RLS, unique(brand, model_code), a self-check, and a reverse")
def _():
    sql = (ROOT / "scripts" / "shop_upcoming_migration.sql").read_text(encoding="utf-8")
    body = re.search(r"create table if not exists shop_upcoming_items \((.*?)\n\);", sql, re.S)
    assert body, "table body not found"
    cols = [ln.strip().split()[0] for ln in body.group(1).splitlines() if ln.strip() and not ln.strip().startswith(("--", "unique", "check"))]
    for c in cols:
        assert not re.search(r"price|cost|qty|quantity|amount|value|margin|rmb|usd|bhd|pcs|carton", c, re.I), c
    for col in ("brand", "model_code", "name_en", "name_ar", "spec_en", "spec_ar", "variants", "photo_url",
                "photo_thumb_urls", "box_url", "shipment_ref", "expected_label_en", "expected_label_ar",
                "status", "catalog_item_code", "sort_order", "created_by", "created_at", "updated_at"):
        assert col in cols, col
    assert "unique (brand, model_code)" in sql
    assert "alter table shop_upcoming_items enable row level security" in sql
    assert "revoke all on shop_upcoming_items from anon, authenticated" in sql
    assert "add column if not exists upcoming_id" in sql and "add column if not exists qty_interest" in sql
    assert "raise exception" in sql and "grantee in ('anon', 'authenticated')" in sql
    assert sql.rstrip().endswith("end $$;"), "the file must close with the DO self-check"
    rev = (ROOT / "scripts" / "shop_upcoming_reverse.sql").read_text(encoding="utf-8")
    assert "drop table if exists shop_upcoming_items" in rev and "drop column if exists upcoming_id" in rev
    dele = re.search(r"delete from shop_reserved_slugs where slug in \((.*?)\)", rev)
    assert dele and "'brands'" not in dele.group(1), "brands pre-dates this migration and must stay reserved"


# ── the importer (dry run, in memory) ─────────────────────────────────────────

@test("importer: dry run builds 34 unique codes, 0 shared photo hashes, merged variants, no price keys")
def _():
    from scripts import wekome_source as wk
    if not wk.WK_SHIP1.exists() or not wk.WK_SHIP2.exists():
        print("   SKIP — shipment workbooks not on this machine (set YQ_WEKOME_DIR)")
        return
    from app.upcoming import assert_no_money, public_item
    from scripts.import_upcoming import EXPECTED_MODELS, WK_ARABIC, build_rows, check_invariants, load_products, photo_hashes
    products, ships = load_products()
    stats = check_invariants(products)
    assert stats["models"] == EXPECTED_MODELS == 34 and stats["shared"] == 0, stats
    assert all(len(v) == 1 for v in photo_hashes(products).values())
    codes = [p["code"] for p in products]
    assert len(set(codes)) == 34 and set(codes) == set(wk.WK_CATALOG) == set(WK_ARABIC)
    # the duplicate WDC-67 black lines merged into one variant; shipment 2 added no model
    wdc67 = next(p for p in products if p["code"] == "WDC-67")
    assert len([v for v in wdc67["variants"] if v["comps"].get("colour") == "Black"]) == 1
    assert {ln["code"] for ln in ships[1]["lines"]} <= set(codes)
    rows = build_rows(products, ships, "2026-10")
    assert len(rows) == 34 and len({r["model_code"] for r in rows}) == 34
    assert_no_money(rows)
    assert all(r["status"] == "draft" and r["brand"] == "WEKOME" and r["name_ar"] and r["spec_en"] for r in rows)
    assert all(r["expected_label_en"] == "Arriving October" for r in rows)
    # every card that varies has chips, and screen protectors carry sizes
    wtp = next(r for r in rows if r["model_code"] == "WTP-137")
    assert wtp["variants"] and all('"' in v["label"] for v in wtp["variants"]), wtp["variants"]
    for r in rows:      # what the marketplace would get from these rows, once published
        it = public_item({**r, "id": 1, "status": "published"}, today=date(2026, 9, 24))
        assert_no_money(it)
        assert "shipment_ref" not in it and "comps" not in json.dumps(it["variants"])
    for r in rows:      # codes, wattage, mAh stay Latin inside the Arabic lines
        for tech in re.findall(r"\b\d+\s?(?:W|mAh|mm|m)\b", r["spec_en"]):
            assert tech.replace(" ", "") in r["spec_ar"].replace(" ", "") or tech.split()[0] in r["spec_ar"], (r["model_code"], tech, r["spec_ar"])


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=2)
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
