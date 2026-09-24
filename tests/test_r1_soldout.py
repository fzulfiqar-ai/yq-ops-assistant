"""Release R1, the sold-out rule — tests.

    python -m tests.test_r1_soldout

Same lightweight runner as tests/test_v3.py (no pytest). Everything here is pure: the pricing
engine runs on a synthetic context (no DB), the catalog loader on a stubbed exec_sql, the copy and
harness checks read the source files, and the listing helpers (facets / search / quickParse) run in
plain node through web/scripts/soldout_order_test.mjs — that one SKIPS (prints, never fails) when
node or web/node_modules is not there; .github/workflows/ci.yml runs it for real in the web job.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import traceback
from datetime import timedelta
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


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── synthetic context (the shape tests/test_shop.py uses) ─────────────────────

def _today():
    from app.shop import bahrain_today
    return bahrain_today()


def _item(code, price, stock=100, cat="CABLE", moq=1, sort_order=None, as_of=None):
    """`stock=None` = no v_catalog_stock row (absent from the snapshot) — the verified zero.
    `as_of` = the snapshot date the row carries (today by default, so the snapshot is fresh)."""
    return {"item_code": code, "display_name": code, "spec": f"{code} spec", "category": cat, "brand": "VFAN",
            "standard_rate": price, "b2c_rate": None, "product_image_url": None, "package_image_url": None,
            "sort_order": sort_order, "created_at": "2026-07-03T00:00:00+00:00", "moq": moq, "pack_size": None,
            "stock_qty": stock, "stock_as_of": (as_of or _today().isoformat()) if stock is not None else None,
            "sold_30d": 0, "prev_30d": 0, "sold_90d": 0, "customers_30d": 0}


def _ctx(items, **settings):
    from app.shop import SETTING_DEFAULTS
    vals = dict(SETTING_DEFAULTS)
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: i for i in items},
            "order": [i["item_code"] for i in items], "costs": {}, "rules": [],
            "salesmen": [{"id": 1, "name": "Furqan Ahmed", "referral_code": "furqan", "is_active": True}],
            "loaded_at": ""}


def _label(d) -> str:
    return f"{d.day} {d.strftime('%b')}"


# ── settings ───────────────────────────────────────────────────────────────────

@test("settings: shop_allow_backorder_staff and shop_stock_fresh_days default in code; the merchant switch is untouched ('1' until release)")
def _():
    from app.shop import SETTING_DEFAULTS, allow_backorder
    assert SETTING_DEFAULTS["shop_allow_backorder_staff"] == "1"
    assert SETTING_DEFAULTS["shop_allow_backorder"] == "1"
    assert SETTING_DEFAULTS["shop_stock_fresh_days"] == "3"
    vals = dict(SETTING_DEFAULTS, shop_allow_backorder="0")
    assert allow_backorder(vals) is False and allow_backorder(vals, staff=True) is True
    vals["shop_allow_backorder_staff"] = "0"
    assert allow_backorder(vals, staff=True) is False
    assert allow_backorder(dict(SETTING_DEFAULTS)) is True


# ── the verified zero and the snapshot's freshness ─────────────────────────────

@test("stock: a SKU absent from the snapshot (no stock row) is sold out — Focus omits zero-balance items")
def _():
    from app.shop import STOCK_OUT, STOCK_IN, stock_status_for
    assert stock_status_for(None, 10) == STOCK_OUT
    assert stock_status_for(0, 10) == STOCK_OUT
    assert stock_status_for(-1, 10) == STOCK_OUT
    assert stock_status_for(11, 10) == STOCK_IN


@test("stock_snapshot: the latest as-of the items carry, fresh within shop_stock_fresh_days (Bahrain calendar), labelled '21 Sep'")
def _():
    from app.shop import stock_snapshot
    today = _today()
    ctx = _ctx([_item("C01", 0.5, stock=None), _item("X01", 1.0), _item("X02", 1.0, as_of=(today - timedelta(days=1)).isoformat())])
    snap = stock_snapshot(ctx)
    assert snap["as_of"] == today.isoformat() and snap["fresh"] is True and snap["age_days"] == 0 and snap["fresh_days"] == 3, snap
    assert snap["label"] == _label(today), snap
    old = today - timedelta(days=10)
    stale = stock_snapshot(_ctx([_item("X01", 1.0, as_of=old.isoformat())]))
    assert stale["fresh"] is False and stale["age_days"] == 10 and stale["label"] == _label(old), stale
    wide = stock_snapshot(_ctx([_item("X01", 1.0, as_of=old.isoformat())], shop_stock_fresh_days="30"))
    assert wide["fresh"] is True and wide["fresh_days"] == 30, wide
    none = stock_snapshot(_ctx([_item("C01", 0.5, stock=None)]))
    assert none["as_of"] is None and none["fresh"] is False and none["label"] is None, none
    src = _read("app/shop.py")
    assert '"stock_fresh": snap["fresh"]' in src, "catalog_payload must tell the market whether the snapshot is stale"


# ── price_cart: the merchant path ──────────────────────────────────────────────

@test("price_cart: merchant, backorder off — a sold-out line is unavailable with the owner wording and blocks the send")
def _():
    from app.shop import SOLD_OUT_REASON, SOLD_OUT_SHORT, price_cart
    assert SOLD_OUT_REASON == "Sold out — can't be ordered right now. Remove it to send your order."
    assert SOLD_OUT_SHORT == "Sold out"
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
    assert q["block_reason"] == "Remove C01 to send this order — sold out.", q["block_reason"]
    assert "since you added it" not in q["block_reason"] and "since you added it" not in c01["blocked_reason"]
    assert "sold out" not in " ".join(q["warnings"]).lower()


@test("price_cart: a SKU with NO stock row (absent from the snapshot) is the same sold-out line — blocked off, backorder on")
def _():
    from app.shop import SOLD_OUT_REASON, price_cart
    off = _ctx([_item("C01", 0.5, stock=None), _item("X01", 1.0)], shop_allow_backorder="0")
    q = price_cart([{"item_code": "C01", "qty": 6}, {"item_code": "X01", "qty": 1}], ctx=off)
    c01 = next(ln for ln in q["lines"] if ln["item_code"] == "C01")
    assert c01["unavailable"] and c01["blocked_reason"] == SOLD_OUT_REASON and c01["stock_status"] == "out_of_stock", c01
    assert q["can_submit"] is False and q["total_bhd"] == 1.0
    on = _ctx([_item("C01", 0.5, stock=None), _item("X01", 1.0)])
    q = price_cart([{"item_code": "C01", "qty": 6}, {"item_code": "X01", "qty": 1}], ctx=on)
    c01 = next(ln for ln in q["lines"] if ln["item_code"] == "C01")
    assert c01["backorder"] and not c01["unavailable"] and q["can_submit"] and q["has_backorder"], c01


@test("price_cart: a stale snapshot keeps the status and names its date in the reason (line and cart level)")
def _():
    from app.shop import SOLD_OUT_REASON, price_cart
    old = _today() - timedelta(days=10)
    ctx = _ctx([_item("C01", 0.5, stock=0, as_of=old.isoformat()), _item("X01", 1.0, as_of=old.isoformat())], shop_allow_backorder="0")
    q = price_cart([{"item_code": "C01", "qty": 1}, {"item_code": "X01", "qty": 1}], ctx=ctx)
    c01 = next(ln for ln in q["lines"] if ln["item_code"] == "C01")
    want = f"Sold out as of {_label(old)} — can't be ordered right now. Remove it to send your order."
    assert c01["unavailable"] and c01["blocked_reason"] == want, c01["blocked_reason"]
    assert q["block_reason"] == f"Remove C01 to send this order — sold out as of {_label(old)}.", q["block_reason"]
    assert q["can_submit"] is False
    # a wider freshness window makes the same snapshot fresh again: the plain reason
    ctx2 = _ctx([_item("C01", 0.5, stock=0, as_of=old.isoformat()), _item("X01", 1.0, as_of=old.isoformat())], shop_allow_backorder="0", shop_stock_fresh_days="30")
    q2 = price_cart([{"item_code": "C01", "qty": 1}], ctx=ctx2)
    assert q2["lines"][0]["blocked_reason"] == SOLD_OUT_REASON, q2["lines"][0]["blocked_reason"]
    # a MOQ block never carries the snapshot date
    q3 = price_cart([{"item_code": "X01", "qty": 1}], ctx=_ctx([_item("X01", 1.0, moq=6, as_of=old.isoformat())]))
    assert q3["block_reason"] == "Remove X01 to send this order — minimum order is 6.", q3["block_reason"]


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


@test("price_cart: force_backorder (the rep's confirmation re-price) prices a sold-out line as a backorder with BOTH switches off")
def _():
    from app.shop import price_cart
    lines = [{"item_code": "C01", "qty": 12}, {"item_code": "X01", "qty": 2}]
    ctx = _ctx([_item("C01", 0.5, stock=0), _item("X01", 1.0)], shop_allow_backorder="0", shop_allow_backorder_staff="0")
    assert price_cart(lines, ctx=ctx, staff=True)["can_submit"] is False
    q = price_cart(lines, ctx=ctx, staff=True, force_backorder=True)
    c01 = next(ln for ln in q["lines"] if ln["item_code"] == "C01")
    assert q["can_submit"] and c01["backorder"] and not c01["unavailable"], q
    assert q["subtotal_bhd"] == 8.0 and q["total_bhd"] == 8.0 and q["items"] == 2, "the confirmed totals must not be understated"
    src = _read("app/shop.py")
    assert 'o.get("referral_code"), staff=True, force_backorder=True)' in src, "confirm_order's re-price must always allow backorder"


@test("catalog_payload: the staff catalog's allow_backorder follows the staff switch (the UI must agree with the quote)")
def _():
    from app import shop
    vals = dict(shop.SETTING_DEFAULTS, shop_allow_backorder="0")
    assert shop.allow_backorder(vals, staff=False) is False
    assert shop.allow_backorder(vals, staff=True) is True
    src = _read("app/shop.py")
    assert '"allow_backorder": allow_backorder(vals, staff)' in src, "catalog_payload must read the path-specific switch"


@test("callers: the staff quote and create_order pass the staff flag; the merchant quote routes stay on the merchant switch")
def _():
    api = _read("app/shop_api.py")
    src = _read("app/shop.py")
    staff_call = "shop.price_cart([ln.model_dump() for ln in body.lines], body.coupon_code, (sm or {}).get(\"referral_code\"), staff=True)"
    merchant_call = "shop.price_cart([ln.model_dump() for ln in body.lines], body.coupon_code, body.referral_code)\n"
    assert api.count(staff_call) == 1, "/shop/quote must price as staff"
    assert api.count(merchant_call) == 2, "the two merchant quote routes (/public/market/quote, /public/shop/{token}/quote) stay on the merchant switch"
    assert "referral_code, ctx=ctx, staff=staff)" in src, "create_order must pass its staff flag"


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

@test("copy: 'Sold out' (+ «نفدت الكمية») everywhere in the market build, never 'Out of stock', never 'since you added it'")
def _():
    strings = _read("web/src/market/strings.ts")
    assert "stockOut: 'Sold out'" in strings and "soldOut: 'Sold out'" in strings
    assert "stockOutAr: 'نفدت الكمية'" in strings
    assert "backorderNote: 'Sold out —" in strings
    assert "soldOutAsOf: (d: string) => `Sold out · stock as of ${d}`" in strings
    assert "leftOut: (n: number) =>" in strings
    market = ROOT / "web" / "src" / "market"
    for f in list(market.rglob("*.ts")) + list(market.rglob("*.tsx")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "out of stock" not in line.lower():
                continue
            stripped = line.strip()
            # a string literal or JSX text with the phrase is a rendered word; a comment is not
            in_comment = stripped.startswith(("//", "*", "/*", "{/*")) or "/**" in stripped
            assert in_comment, f"{f.relative_to(ROOT)}:{n} renders the banned phrase: {stripped[:100]}"
    fmt = _read("web/src/market/lib/format.ts")
    assert "out_of_stock: { label: S.card.stockOut, tone: 'grey' }" in fmt, "sold out is grey, never red"
    for rel in ("app/shop.py", "web/src/market/strings.ts", "docs/SHOP.md"):
        assert "since you added it" not in _read(rel), f"{rel}: the old, sometimes-false wording is back"


@test("cards: data-stock on every MarketCard; 'Tell me when back' goes through useTellBack (a phone first, the rep on WhatsApp second)")
def _():
    card = _read("web/src/market/components/MarketCard.tsx")
    assert card.count("data-stock={item.stock_status || 'in_stock'}") == 2, "both the list row and the card"
    assert "useTellBack(item)" in card and "postRestock(" not in card, "the card never posts a restock request itself"
    assert card.count("{m.soldOutLabel}") == 2, "the sold-out chip carries the snapshot date once it is stale"
    panel = _read("web/src/market/components/ProductPanel.tsx")
    assert "{S.card.stockOut}" in panel and "useTellBack(item)" in panel and "postRestock(" not in panel
    assert "{out ? m.soldOutLabel : stock.label}" in panel and "S.states.stockAsOf(" in panel
    ask = _read("web/src/market/components/RestockAsk.tsx")
    assert "if (isPhone(kept)) send(kept)" in ask and "else setOpen(true)" in ask, "no phone → the sheet, never a contact-less post"
    assert "if (ok) onSend(cleanPhone(phone))" in ask, "the sheet posts only a valid number"
    assert "export function tellRepUrl" in ask and "S.card.tellRepWa(" in ask and "S.card.tellRep(" in ask, "the rep on WhatsApp stays the secondary route"
    market = ROOT / "web" / "src" / "market"
    for f in list(market.rglob("*.ts")) + list(market.rglob("*.tsx")):
        assert "phone: readCustomer().phone || null" not in f.read_text(encoding="utf-8"), f"{f.name}: a restock request without a number"


@test("quick order: Enter never substitutes — a typed sold-out code adds nothing; greyed rows offer 'Tell me when back'; loaded lists say what was left out")
def _():
    page = _read("web/src/market/pages/QuickOrderPage.tsx")
    assert "const pick = row.item || row.best" in page, "Enter takes the query's best match only"
    assert "row.suggestions.find(" not in page and "row.suggestions[0]" not in page and "row.candidates[0]" not in page
    assert "const r = resolveQuick(value, m.items, m.index)" in page
    assert "exactOut: r.exactOut" in page and "<TellBackButton item={exactOut} />" in page, "the typed sold-out SKU is shown with Tell me when back"
    assert "{!can && <TellBackButton item={s} />}" in page, "a greyed sold-out suggestion row offers Tell me when back"
    assert "if (!orderable(item)) return" in page, "lockRow must refuse a sold-out line where backorder is off"
    assert "toast(S.card.leftOut(raw.length - lines.length), 'info')" in page, "?load= and saved lists say how many lines were left out"
    assert "{m.soldOutLabel}" in page and "{S.card.backorder}" in page, "stock chips on the rows"
    qp = _read("web/src/market/lib/quickParse.ts")
    assert "export function resolveQuick" in qp
    assert "const best = exactOut ? null : item || (top && !isOut(top) ? top : null)" in qp
    assert "if (exact && !isOut(exact)) return { item: exact, candidates: [], exact }" in qp
    assert "if (clear && !isOut(top.it)) return { item: top.it, candidates, exact: null }" in qp


@test("MarketContext: add/addMany leave sold-out lines out once backorder is off — every path (palette ⇧Enter, Order again, Quick order) and a toast")
def _():
    ctx = _read("web/src/market/MarketContext.tsx")
    assert "if (isOut(item) && !allowBackorder) {" in ctx and "toast(S.card.leftOut(1), 'info')" in ctx and "return 0" in ctx
    assert "const kept = allowBackorder ? wanted : wanted.filter((e) => !isOut(e.item))" in ctx
    assert "if (wanted.length > kept.length) toast(S.card.leftOut(wanted.length - kept.length), 'info')" in ctx
    assert "stockStale: boolean" in ctx and "soldOutLabel: string" in ctx and "data?.stock_fresh === false" in ctx
    palette = _read("web/src/market/components/SearchPalette.tsx")
    assert palette.count("m.add(row, undefined, 'palette')") == 2, "⇧Enter goes through m.add (the guard)"
    tracking = _read("web/src/market/pages/TrackingPage.tsx")
    assert "m.addMany(entries, 'reorder')" in tracking, "Order again goes through m.addMany (the guard)"
    api = _read("web/src/lib/shopApi.ts")
    assert "stock_fresh?: boolean | null" in api


@test("home grid: HomeBelow orders through homeGridOrder (rail products never sink below the sold-out block)")
def _():
    home = _read("web/src/market/pages/HomeBelow.tsx")
    assert "homeGridOrder(r, filtering ? new Set<string>() : railCodes)" in home
    assert "...r.filter((i) => railCodes.has(i.item_code))" not in home
    facets = _read("web/src/market/lib/facets.ts")
    for fn in ("availabilityRank", "byAvailability", "withinAvailability", "partitionByAvailability", "homeGridOrder"):
        assert f"export function {fn}" in facets, fn
    search = _read("web/src/market/lib/search.ts")
    assert "const av = availabilityRank(a.it) - availabilityRank(b.it)" in search
    groups = _read("web/src/market/lib/searchGroups.ts")
    assert "partitionByAvailability(items.filter((i) => codeKey(i.item_code).startsWith(key)))" in groups


# ── admin, docs, CI ────────────────────────────────────────────────────────────

@test("admin: Settings.tsx has both backorder toggles (merchant hint names 'merchants (marketplace + share link)') and the freshness days")
def _():
    st = _read("web/src/pages/Settings.tsx")
    assert "key: 'shop_allow_backorder', label:" in st and "merchants (marketplace + share link)" in st
    assert "key: 'shop_allow_backorder_staff', label:" in st and "type: 'toggle'" in st
    assert "key: 'shop_stock_fresh_days', label:" in st
    assert "out-of-stock" not in st.split("shop_allow_backorder")[1].split("\n")[0].lower()


@test("docs + CI: SHOP.md describes both switches, the verified zero and the freshness window; ci.yml runs this suite and the node test")
def _():
    doc = _read("docs/SHOP.md")
    for needle in ("shop_allow_backorder_staff", "shop_stock_fresh_days", "Sold out — can't be ordered right now. Remove it to send your order.", "verified zero", "Tell me when back"):
        assert needle in doc, needle
    ci = _read(".github/workflows/ci.yml")
    assert "run: python -m tests.test_r1_soldout" in ci
    assert "run: node scripts/soldout_order_test.mjs" in ci
    assert ci.index("run: npm ci") < ci.index("run: node scripts/soldout_order_test.mjs"), "the node test needs node_modules first"


# ── the QA harness ─────────────────────────────────────────────────────────────

@test("market_qa: 'out of stock' is banned copy; the sold-out order check runs LAST, on a fresh page, on home, Browse and a category")
def _():
    qa = _read("scripts/qa/market_qa.py")
    banned = re.search(r'^BANNED = r"(.+)"$', qa, re.M).group(1)
    assert re.search(banned, "Out of stock", re.I), banned
    assert re.search(banned, "Save 20%") and not re.search(banned, "Sold out", re.I)
    assert "def soldout_order_check" in qa and "def expand_listing" in qa
    assert 'SOLDOUT_ROOTS = {"home": \'section[aria-labelledby="home-all"]\', "shop": "main", "category": "main"}' in qa
    assert "soldout_order_check(run, fresh, SOLDOUT_ROOTS[st.key])" in qa, "the check runs on its own page"
    assert "soldout_order_check(run, page," not in qa, "never on the page the shots are taken from"
    body = qa[qa.index("def run_state("):]
    assert body.index("slider_timing(run, page)") < body.index("soldout_order_check(run, fresh"), "last of all"
    assert body.index('shot(run, page, out, "full", full=True)') < body.index("soldout_order_check(run, fresh"), "after the full-page shot"
    assert "fresh = ctx.new_page()" in body and "install_mocks(fresh, st.order_kind)" in body and "fresh.close()" in body
    assert '"card.soldOut": "Sold out"' in qa and '"card.tellBack": "Tell me when back"' in qa


# ── the migration (written, never applied here) ────────────────────────────────

@test("migration: r1_soldout seeds the staff switch and the freshness days only, self-checks, and has a reverse")
def _():
    mig = _read("scripts/r1_soldout_migration.sql")
    rev = _read("scripts/r1_soldout_reverse.sql")
    assert "('shop_allow_backorder_staff', '1'" in mig and "('shop_stock_fresh_days', '3'" in mig and "on conflict (key) do nothing" in mig
    assert "do $$" in mig and "raise exception" in mig and "grantee in ('anon', 'authenticated')" in mig
    assert not re.search(r"^\s*update app_settings", mig, re.M), "the flip is a release step, not part of the migration"
    assert "delete from app_settings where key in ('shop_allow_backorder_staff', 'shop_stock_fresh_days')" in rev


# ── the listing helpers, in plain node ─────────────────────────────────────────

@test("node: facets / search / quickParse keep sold out last and Enter never substitutes (web/scripts/soldout_order_test.mjs)")
def _():
    node = shutil.which("node")
    script = ROOT / "web" / "scripts" / "soldout_order_test.mjs"
    if not node or not (ROOT / "web" / "node_modules" / "minisearch").exists() or not (ROOT / "web" / "node_modules" / "typescript").exists():
        print("        SKIP: node or web/node_modules not available (ci.yml runs it in the web job)")
        return
    r = subprocess.run([node, str(script)], cwd=str(ROOT / "web"), capture_output=True, text=True, encoding="utf-8", timeout=180)
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
