"""R1 data-honesty tests (24-Sep-2026): the day-first twin rule, the Focus price-book snapshot
void, the badge evidence floors, honest social proof, the covered-lines discount cap, the
purchase_costs floor fallback, the verify price checks and the alias matcher.

    python -m tests.test_r1_data

Same lightweight runner as tests/test_v3.py (no pytest). Every test here is pure -- no database
-- except the last one, which reads production read-only through the yq_readonly RPC and SKIPs
(prints, never fails) without credentials. Nothing in this file writes anywhere.
"""
from __future__ import annotations

import io
import os
import sys
import traceback
import types
from datetime import datetime, timedelta, timezone
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


# ── synthetic context (same shape as tests/test_shop.py, plus the evidence columns) ──

def _item(code, price, stock=100, cat="CABLE", moq=1, sold_90d=0, sold_30d=0, prev_30d=0, customers_30d=0,
          created_at="2025-07-03T00:00:00+00:00", **extra):
    it = {"item_code": code, "display_name": code, "spec": f"{code} spec", "category": cat, "brand": "VFAN",
          "standard_rate": price, "b2c_rate": None, "product_image_url": None, "package_image_url": None,
          "sort_order": None, "created_at": created_at, "moq": moq, "pack_size": None,
          "stock_qty": stock, "stock_as_of": "2026-09-21", "sold_30d": sold_30d, "prev_30d": prev_30d,
          "sold_90d": sold_90d, "customers_30d": customers_30d}
    it.update(extra)
    return it


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
            "order": [i["item_code"] for i in items], "costs": dict(costs or {}), "rules": list(rules),
            "salesmen": [], "loaded_at": ""}     # loaded_at "" = synthetic: never touches the database


def _sp(sku, start, rate=1.0, book="MA_base", src="MASellingPriceBook (40).xlsx", cust=None, wh=None, id=None):
    return {"id": id, "sku_code": sku, "price_book": book, "customer_code": cust, "warehouse_name": wh,
            "start_date": start, "rate_bhd": rate, "source_file": src}


# ── 1. the day-first twin rule ────────────────────────────────────────────────

@test("twin rule: a (40) row with day/month swapped, same key and rate, is the twin of a _36_ row")
def _():
    from scripts.load_supabase import is_dayfirst_twin
    ph = _sp("UK10 C", "2026-09-06", 2.0, src="MASellingPriceBook _36_.xlsx")      # day-first reading of 9-Jun
    assert is_dayfirst_twin(ph, _sp("UK10 C", "2026-06-09", 2.0))
    assert is_dayfirst_twin(_sp("M20", "2026-10-05", 1.7, src="x"), _sp("M20", "2026-05-10", 1.7))   # the 5-Oct change
    assert is_dayfirst_twin(_sp("F04", "2026-11-01", 3.0, src="x"), _sp("F04", "2026-01-11", 3.0))   # the 1-Nov reverts
    assert not is_dayfirst_twin(ph, _sp("UK10 C", "2026-06-09", 1.9))                   # rate differs
    assert not is_dayfirst_twin(ph, _sp("UK10 L", "2026-06-09", 2.0))                   # SKU differs
    assert not is_dayfirst_twin(ph, _sp("UK10 C", "2026-06-09", 2.0, wh="Causeway"))    # layer differs
    assert not is_dayfirst_twin(ph, _sp("UK10 C", "2026-06-09", 2.0, book="modern_trade"))
    assert not is_dayfirst_twin(ph, _sp("UK10 C", "2026-09-06", 2.0))                   # same date = no swap
    assert not is_dayfirst_twin(_sp("A", "2026-09-17", 1.0), _sp("A", "2026-09-17", 1.0))   # day > 12: unambiguous
    assert not is_dayfirst_twin(_sp("A", "2026-03-03", 1.0), _sp("A", "2026-03-03", 1.0))   # day == month
    assert not is_dayfirst_twin(_sp("A", None, 1.0), _sp("A", "2026-03-04", 1.0))


@test("twin rule: the migration voids by the exact reason, asserts 165, never deletes, checks grants")
def _():
    sql = (ROOT / "scripts" / "selling_prices_void_migration.sql").read_text(encoding="utf-8")
    low = sql.lower()
    assert "'dayfirst-parse 14-sep (twin of masellingpricebook (40))'" in low
    assert "n_voided <> 165" in low and "raise exception" in low
    assert not any(s in low for s in ("delete from", "drop table", "drop view", "drop column", "truncate"))
    assert "add column if not exists voided_at" in low and "add column if not exists void_reason" in low
    assert "grantee in ('anon', 'authenticated')" in low
    assert "insert into audit_log" in low
    assert low.count("create or replace view v_price_list_by_book") == 1
    assert low.count("create or replace view v_price_change") == 1
    # the reverse file exists and restores both views without the filter
    rev = (ROOT / "scripts" / "selling_prices_void_reverse.sql").read_text(encoding="utf-8").lower()
    assert "create or replace view v_price_list_by_book" in rev and "set voided_at = null" in rev
    vel = (ROOT / "scripts" / "catalog_velocity_v2_migration.sql").read_text(encoding="utf-8").lower()
    assert "shops_30d" in vel and "shops_90d" in vel and "coalesce(v.is_cash_customer, false) = false" in vel
    assert (ROOT / "scripts" / "catalog_velocity_v2_reverse.sql").exists()


# ── 2. the Focus book snapshot void (loader) ──────────────────────────────────

@test("snapshot void: file names — kind and download counter, portal underscore variant included")
def _():
    from scripts.load_supabase import focus_book_kind, focus_book_seq, pricebook_key
    assert focus_book_kind("MASellingPriceBook (40).xlsx") == "MA_base"
    assert focus_book_kind("MASellingPriceBook _36_.xlsx") == "MA_base"
    assert focus_book_kind("ModernTradeSellerBook _7_.xlsx") == "modern_trade"
    assert focus_book_kind("ModernTradeSellerBook.xlsx") == "modern_trade"
    assert focus_book_kind("YQ_MRN_Landing_Cost_Analysis Sep 2026.xlsx") is None
    assert focus_book_kind(None) is None and focus_book_kind("") is None
    assert focus_book_seq("MASellingPriceBook (40).xlsx") == 40
    assert focus_book_seq("MASellingPriceBook _36_.xlsx") == 36
    assert focus_book_seq("ModernTradeSellerBook.xlsx") == 0
    k = pricebook_key({"sku_code": " T02 ", "price_book": "MA_base", "customer_code": float("nan"),
                       "warehouse_name": None, "start_date": "2026-03-26T00:00:00"})
    assert k == ("T02", "MA_base", "", "", "2026-03-26"), k
    assert k == pricebook_key({"sku_code": "T02", "price_book": "MA_base", "customer_code": "",
                               "warehouse_name": "", "start_date": "2026-03-26"})


@test("snapshot void: older Focus rows not in the file are voided; file rows, workbook rows, newer books, other books stay")
def _():
    from scripts.load_supabase import stale_pricebook_rows
    loaded = [_sp("T02", "2026-03-26", 2.95, src="MASellingPriceBook _41_.xlsx"),
              _sp("T02", "2026-03-26", 3.55, src="MASellingPriceBook _41_.xlsx", wh="Causeway"),
              _sp("UK10 C", "2026-06-09", 2.0, src="MASellingPriceBook _41_.xlsx")]
    existing = [
        _sp("T02", "2026-03-26", 2.95, src="MASellingPriceBook (40).xlsx", id=1),          # still in file → keep
        _sp("T02", "2026-03-26", 3.55, src="MASellingPriceBook (40).xlsx", wh="Causeway", id=2),   # keep
        _sp("UK10 C", "2026-09-06", 2.0, src="MASellingPriceBook _36_.xlsx", id=3),        # phantom, not in file → void
        _sp("UK10 C", "2026-06-09", 2.0, src="MASellingPriceBook (40).xlsx", id=4),        # in file → keep
        _sp("M20", "2026-05-10", 1.7, src="MASellingPriceBook (40).xlsx", id=5),           # dropped by Focus → void
        _sp("M20", "2026-09-14", 1.6, src="YQ_MRN_Landing_Cost_Analysis Sep 2026.xlsx", id=6),  # workbook → keep
        _sp("X01", "2026-09-17", 0.6, src="MASellingPriceBook (42).xlsx", id=7),           # NEWER export → keep
        _sp("T02", "2026-05-13", 3.9, src="ModernTradeSellerBook _7_.xlsx", book="modern_trade", id=8),  # other book → keep
        _sp("T02", "2026-03-26", 2.95, src="MASellingPriceBook _41_.xlsx", id=9),          # this file → keep
    ]
    stale = stale_pricebook_rows(loaded, existing)
    assert sorted(r["id"] for r in stale) == [3, 5], [r["id"] for r in stale]
    # a load with no Focus book (workbook import only) voids nothing
    assert stale_pricebook_rows([_sp("M20", "2026-09-14", 1.6, src="YQ_MRN_x.xlsx")], existing) == []
    # re-uploading an OLDER export never voids the newer one's rows
    older = [_sp("T02", "2026-03-26", 2.95, src="MASellingPriceBook (32).xlsx")]
    assert stale_pricebook_rows(older, existing[:2] + existing[6:7]) == []
    # the same counter through the portal spelling: identical keys → nothing; a mis-parsed key → voided
    same = [_sp("T02", "2026-03-26", 2.95, src="MASellingPriceBook _40_.xlsx")]
    assert [r["id"] for r in stale_pricebook_rows(same, existing[:1])] == []
    assert [r["id"] for r in stale_pricebook_rows(same, [existing[0], existing[4]])] == [5]


# ── 3. badges, social proof, settings ─────────────────────────────────────────

@test("badges: best seller needs invoices_90d >= 10 and shops_90d >= 5 when known; unknown keeps the volume rule")
def _():
    from app.shop import _badges
    items = [_item("BIG", 1.0, sold_90d=900, invoices_90d=3, shops_90d=1),     # one shop's restock
             _item("REAL", 1.0, sold_90d=300, invoices_90d=40, shops_90d=12),
             _item("THIN", 1.0, sold_90d=250, invoices_90d=12, shops_90d=4)]   # too few shops
    b = _badges(_ctx(items, shop_best_seller_top_n="2"))
    assert "best_seller" in b["REAL"] and "best_seller" not in b["BIG"] and "best_seller" not in b["THIN"], b
    # the floors are settings
    b = _badges(_ctx(items, shop_best_seller_top_n="2", shop_best_seller_min_invoices="1", shop_best_seller_min_shops="1"))
    assert "best_seller" in b["BIG"] and "best_seller" in b["REAL"] and "best_seller" not in b["THIN"]
    # no evidence columns yet (pre-migration rows) → the pre-R1 rule: top N by units
    b = _badges(_ctx([_item("A", 1.0, sold_90d=50), _item("B", 1.0, sold_90d=5)], shop_best_seller_top_n="1"))
    assert "best_seller" in b["A"] and "best_seller" not in b["B"]


@test("badges: selling fast needs invoices_90d >= 10 when known")
def _():
    from app.shop import _badges
    fast = _item("F", 1.0, stock=20, sold_90d=300, invoices_90d=30)           # 6 days of cover
    lone = _item("L", 1.0, stock=20, sold_90d=300, invoices_90d=2)            # same cover, 2 invoices
    old = _item("O", 1.0, stock=20, sold_90d=300)                             # no evidence column
    b = _badges(_ctx([fast, lone, old], shop_best_seller_top_n="0"))
    assert "selling_fast" in b["F"] and "selling_fast" not in b["L"] and "selling_fast" in b["O"], b
    b = _badges(_ctx([lone], shop_best_seller_top_n="0", shop_selling_fast_min_invoices="2"))
    assert "selling_fast" in b["L"]


@test("badges: no clearance on an item younger than shop_clearance_min_age_days (default 180); 0 disables the guard")
def _():
    from app.shop import _badges
    young = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    items = [_item("NEWLINE", 1.0, stock=600, sold_90d=10, created_at=young),     # 5,400 days of cover, 40 days old
             _item("OLDLINE", 1.0, stock=600, sold_90d=10)]                      # same numbers, a year old
    b = _badges(_ctx(items, shop_best_seller_top_n="0"))
    assert "clearance" in b["OLDLINE"] and "clearance" not in b["NEWLINE"], b
    b = _badges(_ctx(items, shop_best_seller_top_n="0", shop_clearance_min_age_days="30"))
    assert "clearance" in b["NEWLINE"]
    b = _badges(_ctx(items, shop_best_seller_top_n="0", shop_clearance_min_age_days="0"))
    assert "clearance" in b["NEWLINE"]
    # an item with no created_at cannot be judged → not blocked by the guard
    b = _badges(_ctx([_item("NODATE", 1.0, stock=600, sold_90d=10, created_at=None)], shop_best_seller_top_n="0"))
    assert "clearance" in b["NODATE"]


@test("social proof: named B2B shops only, 'in the last 30 days', never 0, customers_30d until the migration")
def _():
    from app.shop import social_proof_text
    ctx = _ctx([])
    assert social_proof_text(ctx, _item("A", 1.0, customers_30d=9, shops_30d=7), 5) == "Ordered by 7 shops in the last 30 days"
    assert social_proof_text(ctx, _item("A", 1.0, customers_30d=9, shops_30d=4), 5) is None     # 9 names, 4 real shops
    assert social_proof_text(ctx, _item("A", 1.0, customers_30d=6), 5) == "Ordered by 6 shops in the last 30 days"
    assert social_proof_text(ctx, _item("A", 1.0, customers_30d=0, shops_30d=0), 0) is None
    assert "this month" not in (social_proof_text(ctx, _item("A", 1.0, shops_30d=12), 5) or "")


@test("settings: the four badge keys are defaults and the retail anchor default is off (owner 24-Sep)")
def _():
    from app.shop import SETTING_DEFAULTS as D
    assert D["shop_clearance_min_age_days"] == "180" and D["shop_best_seller_min_invoices"] == "10"
    assert D["shop_best_seller_min_shops"] == "5" and D["shop_selling_fast_min_invoices"] == "10"
    assert D["shop_clearance_show_retail"] == "0"
    sql = (ROOT / "scripts" / "catalog_velocity_v2_migration.sql").read_text(encoding="utf-8")
    for k in ("shop_clearance_min_age_days", "shop_best_seller_min_invoices",
              "shop_best_seller_min_shops", "shop_selling_fast_min_invoices"):
        assert f"('{k}'," in sql, k


@test("evidence: a synthetic context never queries the database; a cached read is reused")
def _():
    from app import shop
    ctx = _ctx([_item("A", 1.0)])
    assert shop._velocity_extra(ctx) == {} and ctx["_velocity_extra"] == {}
    ctx["_velocity_extra"] = {"A": {"shops_30d": 8}}
    assert shop._evidence(ctx, ctx["items"]["A"], "shops_30d") == 8
    assert shop._evidence(ctx, ctx["items"]["A"], "invoices_90d") is None


# ── 4. margin floor: covered-lines cap and the purchase_costs fallback ────────

@test("pricing: the cart cap comes from covered lines only — an uncovered line adds 0 headroom, never unlimited")
def _():
    from app.shop import price_cart
    coupon = _rule(8, "coupon", "HALF", coupon_code="HALF", pct_off=50)
    # A: cost 7 → floor 8.4 → 1.6/unit headroom; B: no cost on file → nothing
    ctx = _ctx([_item("A", 10.0), _item("B", 10.0)], rules=[coupon], costs={"A": 7.0},
               shop_min_margin_pct="0.20", shop_vat_rate="0")
    q = price_cart([{"item_code": "A", "qty": 2}, {"item_code": "B", "qty": 2}], coupon_code="HALF", ctx=ctx)
    assert q["discount_bhd"] == 3.2 and q["total_bhd"] == 36.8 and "cart:8" in q["_clamped"], q
    # nothing covered → no cart-level discount at all (before R1 this cart got BHD 20 off)
    ctx = _ctx([_item("A", 10.0), _item("B", 10.0)], rules=[coupon], costs={})
    q = price_cart([{"item_code": "A", "qty": 2}, {"item_code": "B", "qty": 2}], coupon_code="HALF", ctx=ctx)
    assert q["discount_bhd"] == 0 and q["total_bhd"] == 40.0 and "cart:8" in q["_clamped"], q
    # fully covered → the full headroom, as before
    ctx = _ctx([_item("A", 10.0), _item("B", 10.0)], rules=[coupon], costs={"A": 7.0, "B": 7.0},
               shop_min_margin_pct="0.20", shop_vat_rate="0")
    q = price_cart([{"item_code": "A", "qty": 2}, {"item_code": "B", "qty": 2}], coupon_code="HALF", ctx=ctx)
    assert q["discount_bhd"] == 6.4, q
    # a stackable cart rule shares the same cap
    auto = _rule(7, "cart_value", "5% over 10", min_value_bhd=10, pct_off=5, stackable=True)
    ctx = _ctx([_item("A", 10.0), _item("B", 10.0)], rules=[coupon, auto], costs={"A": 7.0},
               shop_min_margin_pct="0.20", shop_vat_rate="0")
    q = price_cart([{"item_code": "A", "qty": 2}, {"item_code": "B", "qty": 2}], coupon_code="HALF", ctx=ctx)
    assert q["discount_bhd"] == 3.2, q


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return types.SimpleNamespace(data=self._rows)


class _FakeClient:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        rows = self.tables.get(name)
        if rows is None:
            raise RuntimeError(f"table {name} unavailable")
        return _FakeQuery(rows)


@test("costs: MRN wins; purchase_costs fills the gaps by MAX(id), case-insensitively; _floor_for finds both")
def _():
    from app import shop
    real = shop.get_client
    shop.get_client = lambda: _FakeClient({
        "mrn_landed_costs": [{"sku_code": "T02", "landed_cost_bhd": 2.0, "effective_date": "2026-09-14", "id": 9}],
        "purchase_costs": [                                              # ordered id desc, as the query asks
            {"id": 30, "sku_code": "t02", "landed_cost_bhd": 9.9},       # covered by MRN → ignored
            {"id": 29, "sku_code": "X05 UL-1MTR", "landed_cost_bhd": 0.31},
            {"id": 28, "sku_code": "X05 UL-1MTR", "landed_cost_bhd": 0.25},   # older row → ignored
            {"id": 27, "sku_code": "M20", "landed_cost_bhd": 0},              # no real cost → ignored
        ]})
    try:
        costs = shop._load_costs()
    finally:
        shop.get_client = real
    assert costs["T02"] == 2.0 and costs["X05 UL-1MTR"] == 0.31 and "M20" not in costs, costs
    ctx = _ctx([_item("X05 UL-1Mtr", 0.6), _item("T02", 2.95), _item("M20", 1.0)], costs=costs,
               shop_min_margin_pct="0.20", shop_vat_rate="0.10")
    assert shop.cost_for(ctx, "X05 UL-1Mtr") == 0.31 and shop.cost_for(ctx, "T02") == 2.0 and shop.cost_for(ctx, "M20") is None
    assert shop._floor_for(ctx, "X05 UL-1Mtr") == 0.409 and shop._floor_for(ctx, "T02") == 2.64
    assert shop._floor_for(ctx, "M20") is None
    # the fallback survives purchase_costs being unavailable
    shop.get_client = lambda: _FakeClient({"mrn_landed_costs": [{"sku_code": "T02", "landed_cost_bhd": 2.0}]})
    try:
        assert shop._load_costs() == {"T02": 2.0}
    finally:
        shop.get_client = real


# ── 5. verify_numbers: price checks ───────────────────────────────────────────

def _pb(sku, start, rate, end=None, wh=None, status="Authorized"):
    return {"sku_code": sku, "item_name": sku, "price_book": "MA_base", "customer_code": None, "warehouse_name": wh,
            "start_date": start, "end_date": end, "rate_bhd": rate, "status": status, "source_file": "MASellingPriceBook (40).xlsx"}


@test("verify: current_prices mirrors v_price_list_by_book (base layer first, latest start, later row, live window)")
def _():
    from scripts.verify_numbers import current_prices
    rows = [
        _pb("T02", "2026-03-26", 3.55, end="2027-11-10"),
        _pb("T02", "2026-03-26", 2.95),                         # same key later in the file → the id-desc winner
        _pb("T02", "2026-03-26", 4.50, wh="Causeway"),          # outlet layer never beats the base layer
        _pb("UK10 C", "2026-06-09", 2.0),
        _pb("UK10 C", "2026-09-06", 1.9),                       # later start wins
        _pb("UK10 C", "2026-11-01", 1.5),                       # not started yet
        _pb("OLD", "2025-01-01", 9.0, end="2026-01-01"),        # ended
        _pb("NOPE", "2026-01-01", 1.0, status="Cancelled"),
        _pb("ZERO", "2026-01-01", 0),
    ]
    cur = current_prices(rows, today="2026-09-24")
    assert cur == {"T02": 2.95, "UK10 C": 1.9}, cur
    assert current_prices(rows, today="2026-11-02")["UK10 C"] == 1.5


@test("verify: export_date is the earlier of docProps.created and the file mtime")
def _():
    import tempfile
    from openpyxl import Workbook
    from scripts.verify_numbers import export_date
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "MASellingPriceBook (40).xlsx"
        wb = Workbook()
        wb.properties.created = datetime(2026, 9, 21, 11, 48, 38)
        wb.save(p)
        assert export_date(p) == "2026-09-21", export_date(p)       # today's mtime is later
        wb.properties.created = datetime(2099, 1, 1)
        wb.save(p)
        assert export_date(p) == datetime.now().date().isoformat()  # a clock ahead of the file never wins


@test("verify: the price-book checks pass on a matching load and fail on a ghost row dated after the export")
def _():
    from scripts import verify_numbers as vn
    rows = [_pb("T02", "2026-03-26", 2.95), _pb("UK10 C", "2026-06-09", 2.0), _pb("F04", "2026-11-01", 3.0)]
    saved = (vn._find, vn.read_grid, vn.parse_pricebook, vn.export_date, vn._db_book_prices, vn._db_rows_after)
    vn._find = lambda key, src: "MASellingPriceBook (40).xlsx" if key == "masellingpricebook" else None
    vn.read_grid = lambda f: None
    vn.parse_pricebook = lambda grid, src, book: rows
    vn.export_date = lambda f: "2026-09-21"
    try:
        vn._db_book_prices = lambda book: {"T02": 2.95, "UK10 C": 2.0}
        vn._db_rows_after = lambda book, exported: [_pb("F04", "2026-11-01", 3.0)]     # in the file: a scheduled price
        checks: list = []
        vn._price_book_checks(Path("."), checks)
        assert [(c[0].split(" ")[0], c[1], c[2]) for c in checks] == [("MA_base", 2.0, 2.0), ("MA_base", 0.0, 0.0)], checks
        # a phantom: the DB shows a different current price and carries a future row the file lacks
        vn._db_book_prices = lambda book: {"T02": 2.95, "UK10 C": 1.9}
        vn._db_rows_after = lambda book, exported: [_pb("F04", "2026-11-01", 3.0), _pb("M20", "2026-10-05", 1.7)]
        checks = []
        vn._price_book_checks(Path("."), checks)
        assert checks[0][1:3] == (2.0, 1.0) and checks[1][1:3] == (0.0, 1.0), checks
        # no price book in the upload → no price check at all
        vn._find = lambda key, src: None
        checks = []
        vn._price_book_checks(Path("."), checks)
        assert checks == []
    finally:
        vn._find, vn.read_grid, vn.parse_pricebook, vn.export_date, vn._db_book_prices, vn._db_rows_after = saved
    assert "selling_prices" not in vn._UNCHECKED


# ── 6. alias matcher and the backfill preview ─────────────────────────────────

@test("matcher: exact name, longest code on a token boundary, whole-name prefix; the 40-char shortcut is gone")
def _():
    from scripts.reconcile_products import build_index, match_alias
    idx = build_index([
        ("T17", "T17 Open Ear Clip On - Type-C Port (VFAN) T17"),
        ("X24 CC", "X24 2Mtr Cable (Type-C to Type-C) (VFAN) X24 CC"),
        ("X24 CC 1Mtr", "X24 CC 1Mtr Cable (Type-C to Type-C) (VFAN) X24 CC"),
        ("X02-M", "X02 3A 1.8Mtr NYLON BRAIDED MICRO USB Cable (VFAN)"),
        ("X02-L", "X02 3A 1.8Mtr NYLON BRAIDED LIGHTNING USB Cable (VFAN)"),
        ("LONG", "A very long product name that runs past forty characters and then keeps going"),
    ])
    assert match_alias("T17 Open Ear Clip On - Type-C Port (VFAN)  T17", idx)[:2] == ("T17", "exact_name")
    assert match_alias("X24 CC 1Mtr Cable (Type-C to Type-C) (VFAN) X24 CC", idx)[:2] == ("X24 CC 1Mtr", "exact_name")
    assert match_alias("X24 CC 1Mtr something new", idx)[:2] == ("X24 CC 1Mtr", "code")      # longest code, not X24 CC
    assert match_alias("X24 CC 2Mtr something new", idx)[:2] == ("X24 CC", "code")
    assert match_alias("T170 other", idx)[0] is None                                          # T17 is not a prefix token
    assert match_alias("X02-M 3A cable", idx)[:2] == ("X02-M", "code")
    assert match_alias("X02 3A 1.8Mtr NYLON BRAIDED LIGHTNING USB Cable (VFAN) X02-L", idx)[:2] == ("X02-L", "name_prefix")
    assert match_alias("X02 something else entirely", idx)[:2] == ("X02-M", "code_family")
    # a string sharing only the first 40 characters of a longer product name is NOT that product any more
    forty = "A very long product name that runs past forty characters and then diverges"
    assert match_alias(forty, idx)[0] is None, match_alias(forty, idx)
    assert match_alias("A very", idx)[0] is None                       # a short string no product name starts
    assert match_alias("", idx) == (None, "unmatched", 0.0)
    sku, method, conf = match_alias("T17 Open Ear Clip On - Type-C Port (VFAN) T17", idx)
    assert conf == 1.0 and match_alias("X02 zzz", idx)[2] == 0.6


@test("preview: propose() keeps the exact item string, resolves product ids and carries the money")
def _():
    from scripts.alias_backfill_preview import propose
    products = [{"id": 11, "sku_code": "T17", "item_name": "T17 Open Ear Clip On - Type-C Port (VFAN) T17"},
                {"id": 12, "sku_code": "X31 CC 1 Mtr", "item_name": "X31 CC 1 Mtr PD Fast (60W) (Type-C to Type-C) (VFAN) X31 CC"}]
    una = [{"item_name": "T17 Open Ear Clip On - Type-C Port (VFAN) T17", "division": "Accessories", "lines": 47,
            "units": 177, "rev_365": 1200.5, "rev_90": 766.7, "last_sold": "2026-09-20"},
           {"item_name": "X34 CC 1 Mtr Invisible Stand Cable (240W) (VFAN) X34 CC", "division": "Accessories",
            "lines": 55, "units": 300, "rev_365": 480.5, "rev_90": 480.5, "last_sold": "2026-09-22"},
           {"item_name": "Batelco Vanilla Sim", "division": "SIM", "lines": 67, "units": 8288, "rev_365": 2900.8,
            "rev_90": 700.0, "last_sold": "2026-09-24"}]
    out = propose(una, products)
    assert [o["proposed_code"] for o in out] == ["T17", None, None]
    assert out[0]["product_id"] == 11 and out[0]["confidence"] == 1.0 and out[0]["rev_90"] == 766.7
    assert out[0]["item_name"] == una[0]["item_name"]              # alias_text must be the exact Focus string
    assert out[2]["division"] == "SIM" and out[1]["method"] == "unmatched"


# ── 7. production, read-only (SKIPs without credentials) ──────────────────────

@test("live (read-only): the twin rule selects exactly 165 rows today and the two phantom pills")
def _():
    if not (os.getenv("SUPABASE_URL") and (os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY"))):
        print("    SKIP: no Supabase credentials in this checkout")
        return
    from app.db_read import exec_sql
    n = exec_sql("""
        SELECT COUNT(*) AS n FROM selling_prices p
         WHERE p.source_file = 'MASellingPriceBook _36_.xlsx' AND p.price_book = 'MA_base'
           AND EXISTS (SELECT 1 FROM selling_prices t
                        WHERE t.source_file = 'MASellingPriceBook (40).xlsx' AND t.price_book = p.price_book
                          AND t.sku_code = p.sku_code
                          AND t.customer_code IS NOT DISTINCT FROM p.customer_code
                          AND t.warehouse_name IS NOT DISTINCT FROM p.warehouse_name
                          AND t.rate_bhd = p.rate_bhd AND t.start_date <> p.start_date
                          AND t.start_date = CASE WHEN EXTRACT(day FROM p.start_date) <= 12
                                THEN make_date(EXTRACT(year FROM p.start_date)::int, EXTRACT(day FROM p.start_date)::int,
                                               EXTRACT(month FROM p.start_date)::int) END)""")[0]["n"]
    assert int(n) == 165, f"twin rule selects {n} rows, the migration asserts 165"


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
