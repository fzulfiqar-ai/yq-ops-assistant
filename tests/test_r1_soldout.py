"""Release R1, the sold-out rule — tests.

    python -m tests.test_r1_soldout

Same lightweight runner as tests/test_v3.py (no pytest). Everything here is pure: the pricing
engine runs on a synthetic context (no DB), the catalog loader on a stubbed exec_sql, the copy and
harness checks read the source files, and the listing helpers (facets / search / quickParse) run in
plain node through web/scripts/soldout_order_test.mjs — that one SKIPS (prints, never fails) when
node or web/node_modules is not there.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── synthetic context (the shape tests/test_shop.py uses) ─────────────────────

def _item(code, price, stock=100, cat="CABLE", moq=1, sort_order=None):
    return {"item_code": code, "display_name": code, "spec": f"{code} spec", "category": cat, "brand": "VFAN",
            "standard_rate": price, "b2c_rate": None, "product_image_url": None, "package_image_url": None,
            "sort_order": sort_order, "created_at": "2026-07-03T00:00:00+00:00", "moq": moq, "pack_size": None,
            "stock_qty": stock, "stock_as_of": "2026-09-14", "sold_30d": 0, "prev_30d": 0,
            "sold_90d": 0, "customers_30d": 0}


def _ctx(items, **settings):
    from app.shop import SETTING_DEFAULTS
    vals = dict(SETTING_DEFAULTS)
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: i for i in items},
            "order": [i["item_code"] for i in items], "costs": {}, "rules": [],
            "salesmen": [{"id": 1, "name": "Furqan Ahmed", "referral_code": "furqan", "is_active": True}],
            "loaded_at": ""}


# ── settings ───────────────────────────────────────────────────────────────────

@test("settings: shop_allow_backorder_staff defaults to '1'; the merchant switch is untouched ('1' until release)")
def _():
    from app.shop import SETTING_DEFAULTS, allow_backorder
    assert SETTING_DEFAULTS["shop_allow_backorder_staff"] == "1"
    assert SETTING_DEFAULTS["shop_allow_backorder"] == "1"
    vals = dict(SETTING_DEFAULTS, shop_allow_backorder="0")
    assert allow_backorder(vals) is False and allow_backorder(vals, staff=True) is True
    vals["shop_allow_backorder_staff"] = "0"
    assert allow_backorder(vals, staff=True) is False
    assert allow_backorder(dict(SETTING_DEFAULTS)) is True


# ── price_cart: the merchant path ──────────────────────────────────────────────

@test("price_cart: merchant, backorder off — a sold-out line is unavailable with the plain reason and blocks the send")
def _():
    from app.shop import SOLD_OUT_REASON, SOLD_OUT_SHORT, price_cart
    ctx = _ctx([_item("C01", 0.5, stock=0), _item("X01", 1.0)], shop_allow_backorder="0")
    q = price_cart([{"item_code": "C01", "qty": 12}, {"item_code": "X01", "qty": 2}], ctx=ctx)
    c01 = next(ln for ln in q["lines"] if ln["item_code"] == "C01")
    assert c01["unavailable"] and not c01["backorder"], c01
    assert c01["blocked_reason"] == SOLD_OUT_REASON, c01["blocked_reason"]
    assert c01["stock_status"] == "out_of_stock" and c01["qty"] == 12 and c01["line_total_bhd"] == 0.0, c01
    # the line stays visible in cart order; the rest of the cart is priced as normal
    assert [ln["item_code"] for ln in q["lines"]] == ["C01", "X01"]
    assert q["total_bhd"] == 2.0 and q["items"] == 1 and q["units"] == 2, q
    assert q["can_submit"] is False and not q["has_backorder"]
    assert "C01" in q["block_reason"] and SOLD_OUT_SHORT in q["block_reason"], q["block_reason"]
    assert q["block_reason"].startswith("Remove C01 to send this order"), q["block_reason"]
    assert "sold out" not in " ".join(q["warnings"]).lower()


@test("price_cart: merchant, backorder on (production today) — nothing changes: a priced backorder line + warning")
def _():
    from app.shop import price_cart
    ctx = _ctx([_item("C01", 0.5, stock=0), _item("X01", 1.0)])
    q = price_cart([{"item_code": "C01", "qty": 12}, {"item_code": "X01", "qty": 2}], ctx=ctx)
    c01 = next(ln for ln in q["lines"] if ln["item_code"] == "C01")
    assert c01["backorder"] and not c01["unavailable"] and c01["blocked_reason"] is None, c01
    assert c01["line_total_bhd"] == 6.0 and q["total_bhd"] == 8.0 and q["items"] == 2, q
    assert q["can_submit"] and q["has_backorder"] and q["block_reason"] is None
    assert any("C01" in w and "sold out" in w for w in q["warnings"]), q["warnings"]


@test("price_cart: several blocked lines — the cart-level reason names them and stays short")
def _():
    from app.shop import price_cart
    ctx = _ctx([_item("C01", 0.5, stock=0), _item("C02", 0.6, stock=0), _item("X01", 1.0)], shop_allow_backorder="0")
    q = price_cart([{"item_code": "C01", "qty": 1}, {"item_code": "C02", "qty": 1}, {"item_code": "X01", "qty": 1}], ctx=ctx)
    assert q["block_reason"] == "Remove C01, C02 to send this order.", q["block_reason"]
    assert q["can_submit"] is False and q["total_bhd"] == 1.0
    # a MOQ block keeps its own wording
    q = price_cart([{"item_code": "X01", "qty": 1}], ctx=_ctx([_item("X01", 1.0, moq=6)]))
    assert q["block_reason"] == "Remove X01 to send this order — minimum order is 6.", q["block_reason"]


# ── price_cart: the salesman/staff path ────────────────────────────────────────

@test("price_cart: staff keeps its backorder while merchants are blocked — until shop_allow_backorder_staff is 0 too")
def _():
    from app.shop import SOLD_OUT_REASON, price_cart
    lines = [{"item_code": "C01", "qty": 12}, {"item_code": "X01", "qty": 2}]
    ctx = _ctx([_item("C01", 0.5, stock=0), _item("X01", 1.0)], shop_allow_backorder="0")
    merchant = price_cart(lines, ctx=ctx)
    staff = price_cart(lines, ctx=ctx, staff=True)
    assert merchant["can_submit"] is False and staff["can_submit"] is True
    c01 = next(ln for ln in staff["lines"] if ln["item_code"] == "C01")
    assert c01["backorder"] and not c01["unavailable"] and staff["has_backorder"] and staff["total_bhd"] == 8.0, staff
    ctx2 = _ctx([_item("C01", 0.5, stock=0), _item("X01", 1.0)], shop_allow_backorder="0", shop_allow_backorder_staff="0")
    staff2 = price_cart(lines, ctx=ctx2, staff=True)
    c01 = next(ln for ln in staff2["lines"] if ln["item_code"] == "C01")
    assert staff2["can_submit"] is False and c01["unavailable"] and c01["blocked_reason"] == SOLD_OUT_REASON
    # the reverse holds too: staff off, merchants on
    ctx3 = _ctx([_item("C01", 0.5, stock=0), _item("X01", 1.0)], shop_allow_backorder_staff="0")
    assert price_cart(lines, ctx=ctx3)["can_submit"] and not price_cart(lines, ctx=ctx3, staff=True)["can_submit"]


@test("catalog_payload: the staff catalog's allow_backorder follows the staff switch (the UI must agree with the quote)")
def _():
    from app import shop
    vals = dict(shop.SETTING_DEFAULTS, shop_allow_backorder="0")
    assert shop.allow_backorder(vals, staff=False) is False
    assert shop.allow_backorder(vals, staff=True) is True
    src = (ROOT / "app" / "shop.py").read_text(encoding="utf-8")
    assert '"allow_backorder": allow_backorder(vals, staff)' in src, "catalog_payload must read the path-specific switch"


@test("callers: the staff quote, create_order and the rep's confirm re-price pass the staff flag")
def _():
    api = (ROOT / "app" / "shop_api.py").read_text(encoding="utf-8")
    src = (ROOT / "app" / "shop.py").read_text(encoding="utf-8")
    staff_call = "shop.price_cart([ln.model_dump() for ln in body.lines], body.coupon_code, (sm or {}).get(\"referral_code\"), staff=True)"
    merchant_call = "shop.price_cart([ln.model_dump() for ln in body.lines], body.coupon_code, body.referral_code)\n"
    assert api.count(staff_call) == 1, "/shop/quote must price as staff"
    assert api.count(merchant_call) == 2, "the two merchant quote routes (/public/market/quote, /public/shop/{token}/quote) stay on the merchant switch"
    assert "referral_code, ctx=ctx, staff=staff)" in src, "create_order must pass its staff flag"
    assert 'o.get("referral_code"), staff=True)' in src, "confirm_order re-price must run as staff"


# ── the payload order: sold out last, shelf order inside each half ─────────────

@test("_load_items: sold-out SKUs go last, CATEGORY_ORDER then sort_order then code inside each half")
def _():
    from app import shop
    rows = [
        _item("Z1", 1, stock=5, cat="CABLE", sort_order=2),
        _item("A9", 1, stock=0, cat="CABLE", sort_order=1),
        _item("B2", 1, stock=None, cat="CHARGER"),        # no stock row = sold out
        _item("B1", 1, stock=3, cat="CHARGER", sort_order=None),
        _item("Q1", 1, stock=1, cat="OTHERTHING"),        # unknown category trails the known ones
        _item("C5", 1, stock=9, cat=shop.CATEGORY_ORDER[0], sort_order=1),
    ]
    real = shop.exec_sql
    shop.exec_sql = lambda *a, **k: [dict(r) for r in rows]
    try:
        out = shop._load_items()
    finally:
        shop.exec_sql = real
    codes = [r["item_code"] for r in out]
    avail = [c for c in codes if shop._f(next(r for r in rows if r["item_code"] == c)["stock_qty"], 0.0) > 0]
    sold = [c for c in codes if c not in avail]
    assert codes == avail + sold, codes
    assert sold == ["A9", "B2"], sold
    assert avail[0] == "C5" and avail[-1] == "Q1", avail
    cable_i, charger_i = shop.CATEGORY_ORDER.index("CABLE"), shop.CATEGORY_ORDER.index("CHARGER")
    if cable_i < charger_i:
        assert avail.index("Z1") < avail.index("B1"), avail
    else:
        assert avail.index("B1") < avail.index("Z1"), avail


# ── the copy ───────────────────────────────────────────────────────────────────

@test("copy: 'Sold out' (+ «نفدت الكمية») everywhere in the market build, never 'Out of stock'")
def _():
    strings = (ROOT / "web" / "src" / "market" / "strings.ts").read_text(encoding="utf-8")
    assert "stockOut: 'Sold out'" in strings and "soldOut: 'Sold out'" in strings
    assert "stockOutAr: 'نفدت الكمية'" in strings
    assert "backorderNote: 'Sold out —" in strings
    market = ROOT / "web" / "src" / "market"
    for f in list(market.rglob("*.ts")) + list(market.rglob("*.tsx")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "out of stock" not in line.lower():
                continue
            stripped = line.strip()
            # a string literal or JSX text with the phrase is a rendered word; a comment is not
            in_comment = stripped.startswith(("//", "*", "/*", "{/*")) or "/**" in stripped
            assert in_comment, f"{f.relative_to(ROOT)}:{n} renders the banned phrase: {stripped[:100]}"
    fmt = (ROOT / "web" / "src" / "market" / "lib" / "format.ts").read_text(encoding="utf-8")
    assert "out_of_stock: { label: S.card.stockOut, tone: 'grey' }" in fmt, "sold out is grey, never red"


@test("cards: every MarketCard carries data-stock, so the harness can read the order it was rendered in")
def _():
    card = (ROOT / "web" / "src" / "market" / "components" / "MarketCard.tsx").read_text(encoding="utf-8")
    assert card.count("data-stock={item.stock_status || 'in_stock'}") == 2, "both the list row and the card"
    assert "const tellUrl" not in card and "S.card.tellRep" not in card, "one sold-out action: the restock request"
    panel = (ROOT / "web" / "src" / "market" / "components" / "ProductPanel.tsx").read_text(encoding="utf-8")
    assert "{S.card.stockOut}" in panel and "postRestock(" in panel and "const tellUrl" not in panel


@test("quick order: Enter never picks a sold-out line; suggestions and pasted rows are partitioned")
def _():
    page = (ROOT / "web" / "src" / "market" / "pages" / "QuickOrderPage.tsx").read_text(encoding="utf-8")
    assert "row.suggestions.find((s) => !isOut(s)) || row.candidates.find((s) => !isOut(s))" in page
    assert "partitionByAvailability(hits.map(" in page
    assert "if (!orderable(item)) return" in page, "lockRow must refuse a sold-out line where backorder is off"
    assert "{S.card.stockOut}" in page and "{S.card.backorder}" in page, "stock chips on the rows"
    qp = (ROOT / "web" / "src" / "market" / "lib" / "quickParse.ts").read_text(encoding="utf-8")
    assert "if (exact && !isOut(exact)) return { item: exact, candidates: [] }" in qp
    assert "if (clear && !isOut(top.it)) return { item: top.it, candidates }" in qp


@test("home grid: HomeBelow orders through homeGridOrder (rail products never sink below the sold-out block)")
def _():
    home = (ROOT / "web" / "src" / "market" / "pages" / "HomeBelow.tsx").read_text(encoding="utf-8")
    assert "homeGridOrder(r, filtering ? new Set<string>() : railCodes)" in home
    assert "...r.filter((i) => railCodes.has(i.item_code))" not in home
    facets = (ROOT / "web" / "src" / "market" / "lib" / "facets.ts").read_text(encoding="utf-8")
    for fn in ("availabilityRank", "byAvailability", "withinAvailability", "partitionByAvailability", "homeGridOrder"):
        assert f"export function {fn}" in facets, fn
    search = (ROOT / "web" / "src" / "market" / "lib" / "search.ts").read_text(encoding="utf-8")
    assert "const av = availabilityRank(a.it) - availabilityRank(b.it)" in search
    groups = (ROOT / "web" / "src" / "market" / "lib" / "searchGroups.ts").read_text(encoding="utf-8")
    assert "partitionByAvailability(items.filter((i) => codeKey(i.item_code).startsWith(key)))" in groups


# ── the QA harness ─────────────────────────────────────────────────────────────

@test("market_qa: 'out of stock' is banned copy and the sold-out order is asserted on home, Browse and a category")
def _():
    qa = (ROOT / "scripts" / "qa" / "market_qa.py").read_text(encoding="utf-8")
    banned = re.search(r'^BANNED = r"(.+)"$', qa, re.M).group(1)
    assert re.search(banned, "Out of stock", re.I), banned
    assert re.search(banned, "Save 20%") and not re.search(banned, "Sold out", re.I)
    assert "def soldout_order_check" in qa and "def expand_listing" in qa
    assert 'SOLDOUT_ROOTS = {"home": \'section[aria-labelledby="home-all"]\', "shop": "main", "category": "main"}' in qa
    assert "soldout_order_check(run, page, SOLDOUT_ROOTS[st.key])" in qa
    assert '"card.soldOut": "Sold out"' in qa and '"card.tellBack": "Tell me when back"' in qa


# ── the migration (written, never applied here) ────────────────────────────────

@test("migration: r1_soldout seeds the staff switch only, self-checks, and has a reverse")
def _():
    mig = (ROOT / "scripts" / "r1_soldout_migration.sql").read_text(encoding="utf-8")
    rev = (ROOT / "scripts" / "r1_soldout_reverse.sql").read_text(encoding="utf-8")
    assert "('shop_allow_backorder_staff', '1'" in mig and "on conflict (key) do nothing" in mig
    assert "do $$" in mig and "raise exception" in mig and "grantee in ('anon', 'authenticated')" in mig
    assert not re.search(r"^\s*update app_settings", mig, re.M), "the flip is a release step, not part of the migration"
    assert "delete from app_settings where key = 'shop_allow_backorder_staff'" in rev


# ── the listing helpers, in plain node ─────────────────────────────────────────

@test("node: facets / search / quickParse keep sold out last on every listing (web/scripts/soldout_order_test.mjs)")
def _():
    node = shutil.which("node")
    script = ROOT / "web" / "scripts" / "soldout_order_test.mjs"
    if not node or not (ROOT / "web" / "node_modules" / "minisearch").exists():
        print("        SKIP: node or web/node_modules not available")
        return
    r = subprocess.run([node, str(script)], cwd=str(ROOT / "web"), capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
    assert r.returncode == 0, "node ordering tests failed"
    assert "0 failed" in r.stdout, r.stdout


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
