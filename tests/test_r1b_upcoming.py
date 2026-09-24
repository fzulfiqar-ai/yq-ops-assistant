"""R1b "Coming soon" (WEKOME) tests — payload whitelist, auto-retire, labels, reserved slugs,
routes, migration shape, the admin/interest functions against a fake client (update_item,
add_interest, list_interest, list_admin, the kill switch, a missing table), list_restock's
exclusion of notify-me rows, and the importer's dry-run invariants.

    python -m tests.test_r1b_upcoming

Same lightweight runner as tests/test_v3.py (no pytest). Pure tests need no database; the fake
client (_Fake) records every builder call and never reaches the network. The importer test reads
the shipment workbooks (Wekome/Wekome Shipments/, or YQ_WEKOME_DIR) and SKIPS when they are not
on this machine; it never writes anywhere.
"""
from __future__ import annotations

import io
import json
import re
import sys
import traceback
from contextlib import contextmanager
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


class _Fake:
    """A table-aware stand-in for the Supabase client: table(name) picks the canned rows that
    execute() returns, every builder call is recorded as (table, method, args, kwargs) and
    returns self, an update() is merged into the returned rows (what PostgREST does), and a
    table named in `fail` raises on execute() the way a not-yet-migrated table does. `not_` is
    a property on the real builder, so it is one here too. Nothing reaches the network."""

    def __init__(self, tables=None, fail=()):
        self.tables = dict(tables or {})
        self.fail = set(fail)
        self.calls: list[tuple] = []
        self._table = None
        self._update = None

    @property
    def not_(self):
        return self

    def table(self, name):
        self._table, self._update = name, None
        self.calls.append((name, "table", (name,), {}))
        return self

    def __getattr__(self, name):
        def call(*a, **kw):
            self.calls.append((self._table, name, a, kw))
            if name == "update" and a:
                self._update = a[0]
            return self
        return call

    def execute(self):
        from types import SimpleNamespace
        if self._table in self.fail:
            raise RuntimeError(f'relation "public.{self._table}" does not exist')
        rows = [dict(r) for r in self.tables.get(self._table, [])]
        if self._update is not None:
            rows = [dict(r, **self._update) for r in rows]
        return SimpleNamespace(data=rows, count=None)

    def of(self, method: str, table: str | None = None) -> list[tuple]:
        return [c for c in self.calls if c[1] == method and (table is None or c[0] == table)]


@contextmanager
def _patched(fake: _Fake, enabled: str = "1", live_codes=("WK-WS55",)):
    """app.upcoming against `fake`, the kill switch at `enabled`, and shop.context() serving
    `live_codes` as the catalog — restored afterwards, caches dropped both ways."""
    import app.shop as s
    import app.upcoming as u
    real_client, real_settings, real_ctx = u.get_client, u.settings, s.context
    u.get_client = lambda: fake
    u.settings = lambda: {"upcoming_enabled": enabled}
    s.context = lambda force=False: {"items": {c: {"item_code": c} for c in live_codes}, "settings": {}, "salesmen": []}
    u.invalidate()
    try:
        yield
    finally:
        u.get_client, u.settings, s.context = real_client, real_settings, real_ctx
        u.invalidate()


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
    assert it["box_thumb_urls"] is None
    # jsonb may come back as text; the box has its own size set for the card's Box toggle
    it2 = public_item(_row(box_thumb_urls='{"160": "b160", "320": "b320"}', photo_thumb_urls="not json"), today=date(2026, 9, 24))
    assert it2["box_thumb_urls"] == {"160": "b160", "320": "b320"} and it2["photo_thumb_urls"] is None


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

@test("api: the six upcoming routes register on a bare app; the public payload is cached a plain 60 s; no phone → 400")
def _():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from slowapi import Limiter
    import app.shop as s
    from app import upcoming as u
    from app.ratelimit import rate_limit_key
    from app.shop_api import register
    api = FastAPI()
    lim = Limiter(key_func=rate_limit_key)
    api.state.limiter = lim
    register(api, lim)
    routes = {(r.path, m) for r in api.routes for m in (getattr(r, "methods", None) or ())}
    for want in (("/public/market/upcoming", "GET"), ("/public/market/upcoming/interest", "POST"),
                 ("/shop/upcoming", "GET"), ("/shop/upcoming/{item_id}", "PATCH"), ("/shop/upcoming/interest", "GET"),
                 ("/shop/upcoming/settings", "POST")):
        assert want in routes, want
    # the kill switch must bite within a minute: max-age=60 and NO stale-while-revalidate window
    real_me, real_json = s.market_enabled, u.public_json
    fake = _Fake({"shop_upcoming_items": [_row(id=1)]})
    try:
        s.market_enabled = lambda: True
        u.public_json = lambda force=False: b'{"enabled":true,"items":[]}'
        with _patched(fake):
            c = TestClient(api)
            r = c.get("/public/market/upcoming")
            assert r.status_code == 200, r.text
            cc = r.headers.get("cache-control", "")
            assert "max-age=60" in cc and "stale-while-revalidate" not in cc, cc
            assert r.headers.get("x-robots-tag", "").startswith("noindex")
            # "notify me" without a usable phone is refused with the reason, and nothing is inserted
            r = c.post("/public/market/upcoming/interest", json={"upcoming_id": 1, "phone": "1234"})
            assert r.status_code == 400 and "phone" in r.json()["detail"].lower(), (r.status_code, r.text)
            assert not fake.of("insert"), fake.calls
            r = c.post("/public/market/upcoming/interest", json={"upcoming_id": 1, "phone": "3300 1122", "device_id": "d1"})
            assert r.status_code == 200 and r.json() == {"ok": True}, r.text
            assert fake.of("insert", "shop_restock_requests")[0][2][0]["phone"] == "97333001122"
    finally:
        s.market_enabled, u.public_json = real_me, real_json


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
                "photo_thumb_urls", "box_url", "box_thumb_urls", "shipment_ref", "expected_month", "expected_label_en",
                "expected_label_ar", "status", "catalog_item_code", "sort_order", "created_by", "created_at", "updated_at"):
        assert col in cols, col
    assert "'box_thumb_urls'" in sql and "n <> 21" in sql, "the self-check must count the box thumb column"
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
    # no literal label is ever written: the month is the source and the label derives from it
    assert all(r["expected_month"] == "2026-10-01" and r["expected_label_en"] is None and r["expected_label_ar"] is None for r in rows)
    assert public_item({**rows[0], "id": 1, "status": "published"}, today=date(2026, 9, 24))["expected_label_en"] == "Arriving October"
    # object names carry the master's content hash, product and box each their own
    from scripts.import_upcoming import content_tag, object_paths
    im = {"product": b"product-bytes", "box": b"box-bytes", "thumbs": {160: b"t", 320: b"t", 512: None}, "box_thumbs": {160: b"b", 320: None, 512: None}}
    paths = object_paths("WS-55", im)
    tp, tb = content_tag(b"product-bytes"), content_tag(b"box-bytes")
    assert len(tp) == 8 and tp != tb
    assert paths["product"] == f"upcoming/WS-55-product-{tp}.jpg" and paths["box"] == f"upcoming/WS-55-box-{tb}.jpg"
    assert paths["thumbs"] == {160: f"upcoming/thumbs/WS-55-product-{tp}-160.webp", 320: f"upcoming/thumbs/WS-55-product-{tp}-320.webp"}
    assert paths["box_thumbs"] == {160: f"upcoming/thumbs/WS-55-box-{tb}-160.webp"}
    assert object_paths("WS-55", {**im, "product": b"fixed-photo"})["product"] != paths["product"], "a changed photo must be a new object"
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


# ── admin + interest against the fake client ──────────────────────────────────

@test("admin: update_item — month → 2026-11 re-derives 'Arriving November' (labels cleared), a sent label is kept, bad input refused")
def _():
    from app.upcoming import UpcomingError, public_item, update_item
    # an old-style row with literal labels (what the first importer wrote): they must not survive a month change
    row = _row(id=7, expected_month="2026-10-01", expected_label_en="Arriving October", expected_label_ar="تصل في أكتوبر")
    fake = _Fake({"shop_upcoming_items": [row]})
    with _patched(fake):
        out = update_item(7, {"expected_month": "2026-11"}, by="office@yq")
        patch = fake.of("update", "shop_upcoming_items")[-1][2][0]
        assert patch["expected_month"] == "2026-11-01" and patch["expected_label_en"] is None and patch["expected_label_ar"] is None, patch
        assert "updated_at" in patch and fake.of("eq")[-1][2] == ("id", 7)
        pub = public_item(out, today=date(2026, 11, 10))
        assert (pub["expected_label_en"], pub["expected_label_ar"]) == ("Arriving November", "تصل في نوفمبر"), pub
        assert out["retired"] is False and out["by"] == "office@yq"
        # clearing the month clears the wording too — "Arriving soon", never "Arriving October" for good
        out = update_item(7, {"expected_month": ""})
        assert public_item(out, today=date(2027, 3, 1))["expected_label_en"] == "Arriving soon"
        # a label sent WITH the month is kept; the other language, not sent, derives again
        out = update_item(7, {"expected_month": "2026-11", "expected_label_en": " Landing mid-November "})
        patch = fake.of("update", "shop_upcoming_items")[-1][2][0]
        assert patch["expected_label_en"] == "Landing mid-November" and patch["expected_label_ar"] is None, patch
        pub = public_item(out, today=date(2026, 11, 10))
        assert (pub["expected_label_en"], pub["expected_label_ar"]) == ("Landing mid-November", "تصل في نوفمبر")
        # the editor's blank label → derived; copy fields trimmed
        out = update_item(7, {"name_en": " WK Design TWS ", "expected_label_en": "", "expected_label_ar": ""})
        patch = fake.of("update", "shop_upcoming_items")[-1][2][0]
        assert patch == {"name_en": "WK Design TWS", "expected_label_en": None, "expected_label_ar": None, "updated_at": patch["updated_at"]}, patch
        # validation, nothing written
        n = len(fake.of("update"))
        for bad in ({"status": "live"}, {"name_en": "  "}, {"not_a_field": 1}, {}, {"expected_month": "October"}):
            try:
                update_item(7, bad)
            except UpcomingError:
                pass
            else:
                raise AssertionError(f"accepted {bad!r}")
        assert len(fake.of("update")) == n
        # the catalog link (upper-cased) retires the card while that code is live
        out = update_item(7, {"catalog_item_code": " wk-ws55 "})
        assert out["catalog_item_code"] == "WK-WS55" and out["retired"] is True
        assert update_item(7, {"status": "arrived", "sort_order": ""})["sort_order"] is None
    assert not [c for c in fake.calls if c[0] not in ("shop_upcoming_items",)], "update_item touches no other table"


@test("interest: add_interest needs a phone (normalised like recognize), takes only a live card while enabled, stores BRAND:MODEL, qty only when positive")
def _():
    from app.upcoming import UpcomingError, add_interest, normalise_phone
    assert normalise_phone(" 3300 1122 ") == "97333001122" and normalise_phone("+973 3300-1122") == "97333001122"
    assert normalise_phone("0044 7700 900123") == "00447700900123"[:32]      # international: digits as given, like recognize
    assert normalise_phone("1234567") is None and normalise_phone(None) is None and normalise_phone("abc") is None
    rows = [_row(id=1, model_code="WS-55"), _row(id=2, model_code="WS-56", status="withdrawn"),
            _row(id=3, model_code="WS-57", catalog_item_code="WK-WS55"),      # retired: live in the catalog
            _row(id=4, model_code="WS-58", status="draft")]
    fake = _Fake({"shop_upcoming_items": rows})
    with _patched(fake):
        assert add_interest(1, "3300 1122", "dev-1", "Furqan", 0) is True
        rec = fake.of("insert", "shop_restock_requests")[-1][2][0]
        assert rec == {"item_code": "WEKOME:WS-55", "upcoming_id": 1, "phone": "97333001122", "device_id": "dev-1",
                       "referral_code": "furqan", "qty_interest": None}, rec
        assert add_interest(1, "+973 3300 1122", None, None, 250) is True
        rec = fake.of("insert", "shop_restock_requests")[-1][2][0]
        assert rec["phone"] == "97333001122" and rec["qty_interest"] == 250 and rec["device_id"] is None and rec["referral_code"] is None
        assert add_interest(1, "33001122", None, None, -5) is True
        assert fake.of("insert", "shop_restock_requests")[-1][2][0]["qty_interest"] is None
        n = len(fake.of("insert"))
        for uid in (2, 3, 4, 99):            # withdrawn, retired, draft, unknown
            assert add_interest(uid, "33001122", None, None, None) is False, uid
        for bad in (None, "", "1234567", "abc"):
            try:
                add_interest(1, bad, None, None, None)
            except UpcomingError as e:
                assert "phone" in str(e).lower()
            else:
                raise AssertionError(f"accepted phone {bad!r}")
        assert len(fake.of("insert")) == n, "a refused request inserts nothing"
    with _patched(fake, enabled="0"):        # the kill switch: a live card takes nothing
        assert add_interest(1, "33001122", None, None, None) is False
        assert len(fake.of("insert")) == n


@test("interest: list_interest is scoped to the rep's code, lists every phone once with the ids to resolve; admins see all; empty while off")
def _():
    from app.upcoming import list_interest
    items = [_row(id=1, model_code="WS-55"), _row(id=2, model_code="WDC-C37", status="arrived", catalog_item_code="WK-C37")]
    req = [
        {"id": 13, "upcoming_id": 1, "item_code": "WEKOME:WS-55", "phone": "97333001122", "referral_code": "furqan", "qty_interest": 50, "created_at": "2026-09-24T12:00:00+00:00", "notified_at": None},
        {"id": 12, "upcoming_id": 1, "item_code": "WEKOME:WS-55", "phone": "97333001133", "referral_code": "furqan", "qty_interest": None, "created_at": "2026-09-24T11:00:00+00:00", "notified_at": None},
        {"id": 11, "upcoming_id": 1, "item_code": "WEKOME:WS-55", "phone": "97333001122", "referral_code": "furqan", "qty_interest": 100, "created_at": "2026-09-24T10:00:00+00:00", "notified_at": None},   # same shop, another device
        {"id": 14, "upcoming_id": 2, "item_code": "WEKOME:WDC-C37", "phone": "97333001144", "referral_code": "harsh", "qty_interest": None, "created_at": "2026-09-23T10:00:00+00:00", "notified_at": None},
    ]
    fake = _Fake({"shop_upcoming_items": items, "shop_restock_requests": req})
    with _patched(fake):
        out = list_interest("Furqan")
        # the query itself is scoped and open-only (the fake does not filter; the calls prove the intent)
        assert ("shop_restock_requests", "eq", ("referral_code", "furqan"), {}) in fake.calls, fake.calls
        assert ("shop_restock_requests", "is_", ("upcoming_id", "null"), {}) in fake.calls      # after not_
        assert ("shop_restock_requests", "is_", ("notified_at", "null"), {}) in fake.calls
        assert [g["upcoming_id"] for g in out] == [1, 2], out
        g = out[0]
        assert g["count"] == 3 and g["ids"] == [13, 12, 11] and g["qty_interest"] == 150, g
        assert g["phones"] == ["97333001122", "97333001133"], "every phone once, newest first — each one gets its own WhatsApp link"
        assert g["first_at"] == "2026-09-24T10:00:00+00:00" and g["model_code"] == "WS-55" and g["status"] == "published"
        assert out[1]["status"] == "arrived" and out[1]["catalog_item_code"] == "WK-C37" and out[1]["phones"] == ["97333001144"]
        fake.calls.clear()
        assert len(list_interest(None)) == 2 and not fake.of("eq"), "the admin is not scoped"
    with _patched(fake, enabled="0"):
        assert list_interest("furqan") == [] and list_interest(None) == []


@test("admin: list_admin — a rep gets published cards only and none while off; the desk gets every status + phones/ids; a missing table reads as no cards")
def _():
    from app.upcoming import add_interest, list_admin, list_interest, public_upcoming, set_enabled
    items = [_row(id=1, model_code="WS-55"), _row(id=2, model_code="WS-56", status="draft"), _row(id=3, model_code="WS-57", status="withdrawn")]
    req = [{"id": 11, "upcoming_id": 1, "phone": "97333001122", "referral_code": "furqan", "created_at": "2026-09-24T10:00:00+00:00", "notified_at": None},
           {"id": 12, "upcoming_id": 1, "phone": "97333001122", "referral_code": None, "created_at": "2026-09-24T11:00:00+00:00", "notified_at": None},
           {"id": 13, "upcoming_id": 1, "phone": None, "referral_code": None, "created_at": "2026-09-24T12:00:00+00:00", "notified_at": None}]
    fake = _Fake({"shop_upcoming_items": items, "shop_restock_requests": req})
    with _patched(fake):
        rep = list_admin(all_statuses=False)
        assert [r["model_code"] for r in rep] == ["WS-55"], rep
        assert rep[0]["interest_count"] == 3 and rep[0]["interest_shops"] == 1 and rep[0]["retired"] is False
        assert "interest_phones" not in rep[0] and "interest_ids" not in rep[0], "phones are for the desk; a rep's own come through list_interest"
        desk = list_admin(all_statuses=True)
        assert [r["status"] for r in desk] == ["published", "draft", "withdrawn"]
        assert desk[0]["interest_phones"] == ["97333001122"] and desk[0]["interest_ids"] == [11, 12, 13], desk[0]
        assert desk[1]["interest_count"] == 0 and desk[1]["interest_phones"] == []
        assert desk[0]["variants"] == [{"label": "Black", "label_ar": "أسود"}, {"label": "White", "label_ar": "أبيض"}]
    with _patched(fake, enabled="0"):
        assert list_admin(all_statuses=False) == [], "the kill switch empties the rep's list"
        assert len(list_admin(all_statuses=True)) == 3, "the desk still sees everything, to switch it back on"
        assert public_upcoming(force=True)["items"] == [] and public_upcoming()["enabled"] is False
    # the API may deploy before the migration: every read answers empty, nothing raises
    with _patched(_Fake({}, fail={"shop_upcoming_items", "shop_restock_requests"})):
        assert list_admin(True) == [] and list_admin(False) == []
        assert list_interest(None) == [] and list_interest("furqan") == []
        assert add_interest(1, "33001122", None, None, None) is False
        assert public_upcoming(force=True)["count"] == 0
    # the portal's kill switch writes the one setting, on conflict by key
    fake = _Fake({"app_settings": []})
    with _patched(fake):
        set_enabled(False, by="office@yq")
        up = fake.of("upsert", "app_settings")[-1]
        assert up[2][0]["key"] == "upcoming_enabled" and up[2][0]["value"] == "0" and up[2][0]["updated_by"] == "office@yq" and up[3] == {"on_conflict": "key"}, up
        set_enabled(True)
        assert fake.of("upsert", "app_settings")[-1][2][0]["value"] == "1"


@test("restock: list_restock ignores notify-me rows (upcoming_id set) — filtered in Python, so it answers before the migration too")
def _():
    import app.shop as s
    rows = [
        {"id": 1, "item_code": "WEKOME:WS-55", "upcoming_id": 7, "phone": "97333001122", "created_at": "2026-09-24T10:00:00+00:00", "notified_at": None},
        {"id": 2, "item_code": "T02", "upcoming_id": None, "phone": "97333001133", "created_at": "2026-09-24T09:00:00+00:00", "notified_at": None},
        {"id": 3, "item_code": "T02", "phone": None, "created_at": "2026-09-24T08:00:00+00:00", "notified_at": None},    # a pre-migration row: no such key at all
    ]
    fake = _Fake({"shop_restock_requests": rows})
    real_client, real_ctx = s.get_client, s.context
    try:
        s.get_client = lambda: fake
        s.context = lambda force=False: {"items": {"T02": {"display_name": "Test 02", "stock_qty": 5}}, "settings": {}, "salesmen": []}
        out = s.list_restock(None)
        scoped = s.list_restock("Furqan")
    finally:
        s.get_client, s.context = real_client, real_ctx
    assert [g["item_code"] for g in out] == ["T02"], out
    assert out[0]["count"] == 2 and out[0]["ids"] == [2, 3] and out[0]["phones"] == ["97333001133"] and out[0]["back_in_stock"] is True
    assert [g["item_code"] for g in scoped] == ["T02"]
    assert ("shop_restock_requests", "eq", ("referral_code", "furqan"), {}) in fake.calls
    # never a query-side filter on the column (it may not exist yet): no is_("upcoming_id", …) reaches the builder
    assert not [c for c in fake.calls if c[1] == "is_" and c[2][:1] == ("upcoming_id",)], fake.calls


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
