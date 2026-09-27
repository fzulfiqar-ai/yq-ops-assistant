"""R7d — shop analytics done properly (plan §13; audit ANA-4/5, OFF-8, INT-13).

    python -m tests.test_r7d_analytics

Same lightweight runner as tests/test_r7c_order_heart.py (no pytest). Nothing here reaches a database
or the network (CI: SUPABASE_URL=https://ci.invalid): the shop reads run against an in-memory
PostgREST stand-in that pages like the real one (max 1,000 rows per request, offsets honoured) and
answers the read-only RPC from canned view rows. All data is synthetic — made-up shops, reps, items,
phones and numbers.

Covered:
  * the window (Bahrain calendar days, today included) and the one money rule (confirmed ?? requested;
    a confirmed line = its confirmed total, else its ordered total pro rata; Decimal half-up);
  * the Python twins of the view rules: final typed searches (typing within 30 s, repeats, chips and
    facets left out), the funnel (a session counted once per Bahrain day, a phone's day credited to its
    first rep link, checkout = checkout_start, the order step on the device), rails;
  * analytics() before the views: the raw events PAGED (the 1,000-row cap made it see a third of the
    week), the rep scope filtered by the database, test orders out, every key the portal and the rep's
    link-week card read kept, the note that says so;
  * analytics() on the views: the SQL bound with the window and the rep as parameters, no raw event
    read beyond the first-event probe, a missing view remembered, a failing section costs a note not
    the page, links and merchants in the company scope only, no phone / e-mail / person's name;
  * the merchant profile: facts (cadence from distinct order days, due / overdue, the all-shops
    fallback, the dormant rule, windows, top categories / items, the rep precedence), INSIGHTS with
    their evidence and rule, the trend against its own usual, the monthly series, the route's gates;
  * the migration and its reverse as text, the portal sources, and a local Postgres replay that
    checks the views against the Python twins on synthetic data (SKIPs without a local cluster).

The view bodies were also run read-only on production on 27-Sep-2026 (as plain SELECTs inside CTEs,
nothing created) and agree with the Python twins on every event, search, rail and merchant there.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []
MIGRATION = ROOT / "scripts" / "r7d_analytics_views_migration.sql"
REVERSE = ROOT / "scripts" / "r7d_analytics_views_reverse.sql"
BAHRAIN = timezone(timedelta(hours=3))


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── an in-memory PostgREST stand-in that pages like the real one ────────────────

MAX_ROWS = 1000          # PostgREST's max-rows: a request never answers more, whatever .limit() says


def _same(a, b) -> bool:
    return a == b or str(a) == str(b)


class _Query:
    def __init__(self, db: "_DB", table: str):
        self.db, self.table = db, table
        self.cols, self.filters, self.lim, self.off, self.sort = "*", [], None, 0, None

    def select(self, cols="*", count=None, head=False):
        self.cols = cols
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def gte(self, col, val):
        self.filters.append(("gte", col, val))
        return self

    def like(self, col, val):
        return self

    def order(self, col, desc=False, **_kw):
        self.sort = (col, bool(desc))
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.off, self.lim = a, b - a + 1
        return self

    def _match(self, row: dict) -> bool:
        for kind, col, val in self.filters:
            got = row.get(col)
            if kind == "eq" and not _same(got, val):
                return False
            if kind == "in" and not any(_same(got, v) for v in val):
                return False
            if kind == "gte" and (got is None or str(got) < str(val)):
                return False
        return True

    def execute(self):
        db = self.db
        db.calls.append(("select", self.table, self.cols, list(self.filters), (self.off, self.lim)))
        fail = db.fail.get(self.table)
        if fail is not None:
            raise fail
        known = db.columns.get(self.table)
        if known is not None and self.cols != "*":
            for c in (x.strip() for x in str(self.cols).split(",")):
                if c and c not in known:
                    raise RuntimeError(f"column {self.table}.{c} does not exist")
        rows = [dict(r) for r in db.tables.get(self.table, []) if self._match(r)]
        if self.sort:
            col, desc = self.sort
            rows.sort(key=lambda r: (r.get(col) is None, str(r.get(col)) if not isinstance(r.get(col), (int, float)) else r.get(col)),
                      reverse=desc)
        n = min(self.lim or MAX_ROWS, MAX_ROWS)
        return SimpleNamespace(data=rows[self.off:self.off + n], count=None)


class _DB:
    def __init__(self, rpc=None, columns: dict | None = None, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.columns = {k: set(v) for k, v in (columns or {}).items()}
        self.calls: list[tuple] = []
        self.rpc_calls: list[tuple[str, list]] = []
        self.fail: dict[str, Exception] = {}
        self.rpc_handler = rpc

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, args=None):
        assert name == "run_readonly_query_params", name
        sql, params = args["sql_text"], args["params"]
        self.rpc_calls.append((sql, params))

        def run():
            if self.rpc_handler is None:
                raise RuntimeError('relation "v_shop_funnel_daily" does not exist')
            return SimpleNamespace(data=self.rpc_handler(sql, params))
        return SimpleNamespace(execute=run)

    def selects(self, table: str) -> list[tuple]:
        return [c for c in self.calls if c[1] == table]


@contextmanager
def _patched(fake: _DB, ctx_items: dict | None = None):
    """Point app.shop / app.database at the fake; reset every cache analytics consults."""
    import app.database as db
    import app.shop as s
    from app import followups
    from app import shop_analytics as sa
    saved = (s.get_client, db.get_client, db.reset_client, s._ctx_cache["ctx"], s._ctx_cache["at"])
    s.get_client = db.get_client = lambda: fake
    db.reset_client = lambda: None
    s._col_cache.clear()
    s._salesman_cache.clear()
    s._settings_cache.update(at=0.0, vals=None)
    sa._views.update(at=0.0, ok=None)
    sa._first_event.update(day=None)
    followups.invalidate()
    if ctx_items is not None:
        s._ctx_cache.update(ctx={"items": ctx_items, "salesmen": []}, at=time.time() + 10 ** 6)
    try:
        yield fake
    finally:
        s.get_client, db.get_client, db.reset_client = saved[:3]
        s._ctx_cache.update(ctx=saved[3], at=saved[4])
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        sa._views.update(at=0.0, ok=None)
        sa._first_event.update(day=None)
        followups.invalidate()


# ── synthetic fixtures ─────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)
TODAY_BH = (NOW + timedelta(hours=3)).date()
# a safe instant: yesterday 12:00 in Bahrain — inside every window, never across a midnight
NOON = datetime(TODAY_BH.year, TODAY_BH.month, TODAY_BH.day, 12, tzinfo=BAHRAIN) - timedelta(days=1)
REP = {"id": 1, "name": "Rep One", "referral_code": "repone", "is_active": True}
OTHER = {"id": 2, "name": "Rep Two", "referral_code": "reptwo", "is_active": True}
PHONES = ("97300000101", "97300000102", "97300000103")


def _at(minutes=0, days=0, base: datetime | None = None) -> str:
    return ((base or NOON) + timedelta(minutes=minutes, days=days)).astimezone(timezone.utc).isoformat()


def _ev(event, session, dev=None, minutes=0, ref="repone", sid=None, eid=None, **meta):
    _ev.n = getattr(_ev, "n", 0) + 1
    return {"id": eid or _ev.n, "ts": _at(minutes), "event": event, "session_id": session,
            "item_code": meta.pop("item_code", None), "referral_code": ref, "salesman_id": sid, "src": "direct",
            "device_id": dev if dev is not None else f"dev-{session}", "meta": meta or None}


def _order(oid, **over):
    o = {"id": oid, "order_no": f"YQ-TEST-{oid:04d}", "status": "new", "total_bhd": 10.0, "total_confirmed_bhd": None,
         "units_count": 4, "salesman_id": 1, "salesman_name": "Rep One", "referral_code": "repone", "src": None,
         "coupon_code": None, "customer_phone": PHONES[oid % 3], "customer_shop": f"Test Shop {oid % 3}",
         "customer_area": "Test Area", "customer_name": "Test Person", "customer_email": "t@example.com",
         "created_at": _at(oid), "has_backorder": False, "source": "market", "attribution_source": "session_ref",
         "attribution_conflict": False, "assigned_at": None, "confirmed_at": None, "cancelled_by": None,
         "customer_id": 50 + oid % 3, "device_id": f"dev-o{oid}", "is_test": False, "items_count": 1}
    o.update(over)
    return o


def _line(lid, order_id, code="T01", qty=4, total=10.0, **over):
    ln = {"id": lid, "order_id": order_id, "item_code": code, "display_name": f"Test {code}", "qty": qty,
          "line_total_bhd": total, "unit_price_bhd": round(total / qty, 3) if qty else 0, "qty_confirmed": None,
          "line_status": "ok", "line_total_confirmed": None}
    ln.update(over)
    return ln


def _base_tables(**over) -> dict:
    t = {"salesmen": [REP, OTHER], "app_settings": [], "shop_events": [], "shop_orders": [], "shop_order_lines": [],
         "shop_customers": []}
    t.update(over)
    return t


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the window and the money rule
# ═══════════════════════════════════════════════════════════════════════════════

@test("window: Bahrain calendar days with today included; clamped 1..365; midnight in Bahrain as UTC")
def _():
    from app import shop_analytics as sa
    assert sa.window(7, date(2026, 9, 27)) == (7, date(2026, 9, 21), date(2026, 9, 27))
    assert sa.window(1, date(2026, 9, 27)) == (1, date(2026, 9, 27), date(2026, 9, 27))
    assert sa.window(0, date(2026, 9, 27))[0] == 1 and sa.window(9999, date(2026, 9, 27))[0] == 365
    assert sa.window("junk", date(2026, 9, 27))[0] == 30
    assert sa.bahrain_midnight(date(2026, 9, 21)) == "2026-09-20T21:00:00+00:00"
    assert sa.bahrain_day("2026-09-20T21:30:00+00:00") == date(2026, 9, 21), "00:30 in Bahrain is the next day"
    assert sa.p75([4, 1, 3, 2]) == 3.0 and sa.p75([1800, 2600]) == 2600.0 and sa.p75([]) is None, "nearest rank = percentile_disc"


@test("money: an order is confirmed ?? requested; a line its confirmed total, else its ordered total pro rata (Decimal)")
def _():
    from app import shop_analytics as sa
    assert sa.order_value({"total_bhd": 12.5, "total_confirmed_bhd": None}) == Decimal("12.500")
    assert sa.order_value({"total_bhd": 12.5, "total_confirmed_bhd": 9.25}) == Decimal("9.250")
    assert sa.order_value({"total_bhd": 12.5, "total_confirmed_bhd": 0}) == Decimal("0.000"), "a confirmed 0 is 0, not the request"
    received = {"status": "new"}
    confirmed = {"status": "confirmed"}
    ln = {"qty": 6, "line_total_bhd": 17.7}
    assert sa.line_effective(ln, received) == (6, Decimal("17.700"))
    assert sa.line_effective(ln, confirmed) == (6, Decimal("17.700")), "no confirmed figure: as ordered"
    assert sa.line_effective({**ln, "qty_confirmed": 4}, confirmed) == (4, Decimal("11.800")), "pro rata at the ordered unit price"
    assert sa.line_effective({**ln, "qty_confirmed": 1}, confirmed) == (1, Decimal("2.950"))
    assert sa.line_effective({"qty": 3, "line_total_bhd": 1.0, "qty_confirmed": 1}, confirmed) == (1, Decimal("0.333"))
    assert sa.line_effective({"qty": 3, "line_total_bhd": 2.0, "qty_confirmed": 1}, confirmed) == (1, Decimal("0.667")), "half up"
    assert sa.line_effective({**ln, "qty_confirmed": 4, "line_total_confirmed": 11.0}, confirmed) == (4, Decimal("11.000"))
    assert sa.line_effective({**ln, "line_status": "removed"}, confirmed) == (0, Decimal("0.000"))
    assert sa.line_effective({**ln, "line_status": "unavailable"}, {"status": "delivered"})[0] == 0
    assert sa.line_effective({**ln, "qty_confirmed": 2}, {"status": "cancelled", "confirmed_at": "2026-09-22T10:00:00Z"}) \
        == (2, Decimal("5.900")), "confirmed before it was cancelled: its confirmed figures"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. the Python twins of the view rules
# ═══════════════════════════════════════════════════════════════════════════════

@test("searches: only the final typed search counts — typing within 30 s, repeats and chip / facet taps are dropped")
def _():
    from app import shop_analytics as sa

    def s(q, seconds, dev="d1", zero=False, **meta):
        return {"id": seconds * 10 + len(q), "ts": (NOON + timedelta(seconds=seconds)).isoformat(),
                "event": "search_zero" if zero else "search", "session_id": f"s-{dev}", "device_id": dev,
                "meta": {"q": q, **meta}}
    evs = [s("uk", 0), s("uk1", 1), s("UK12", 2),                   # typing -> "uk12"
           s("uk12", 20),                                           # the same again within 30 s -> still one
           s("t16", 100), s("t16", 131),                            # 31 s apart -> two searches
           s("sandisk", 200, zero=True), s("sand", 205),            # backspacing: both stay (not an extension)
           s("instock", 300, rail="quick_filter"),                  # a chip
           s("CABLE:conn=typec", 310, rail="facet"),                # a facet
           s("", 320), s("uk12", 5, dev="d2")]                      # empty; another device's own search
    rows = {r["term"]: r for r in sa.search_rows_from_events(evs)}
    assert set(rows) == {"uk12", "t16", "sandisk", "sand"}, rows
    assert rows["uk12"]["searches"] == 2 and rows["uk12"]["devices"] == 2, "one on each device"
    assert rows["t16"]["searches"] == 2
    assert rows["sandisk"]["zero"] == 1 and rows["sand"]["zero"] == 0
    assert sa.SEARCH_TYPING_GAP_S == 30
    p = sa.search_payload(list(rows.values()))
    assert p["searches"] == 6 and p["zero_results"] == 1 and p["zero_rate_pct"] == 16.7
    assert [t["term"] for t in p["zero_terms"]] == ["sandisk"] and p["terms"][0]["term"] in ("uk12", "t16")


@test("funnel: a session once per Bahrain day, a phone's day credited to its first rep link, checkout = checkout_start")
def _():
    from app import shop_analytics as sa
    evs = [
        # phone A: direct first, then through Rep One's link the same day -> the whole day is Rep One's
        _ev("view", "sA", "devA", minutes=0, ref=None), _ev("view", "sA", "devA", minutes=5, ref="repone", sid=1),
        _ev("item", "sA", "devA", minutes=6, ref=None), _ev("add", "sA", "devA", minutes=7, ref=None),
        _ev("cart", "sA", "devA", minutes=8, ref=None), _ev("checkout_start", "sA", "devA", minutes=9, ref=None),
        # the API's order event: no session, the device stands in
        {**_ev("order", None, "devA", minutes=10, ref="repone", sid=1), "session_id": None},
        # the same session the next day: another visit
        {**_ev("view", "sA", "devA", ref=None), "ts": _at(days=1)},
        # phone B: Rep Two; an error ping never counts
        _ev("view", "sB", "devB", minutes=1, ref="reptwo", sid=2), _ev("error", "sB", "devB", minutes=2, ref="reptwo"),
        # "checkout" is never sent, but counts if it ever is
        _ev("checkout", "sB", "devB", minutes=3, ref="reptwo", sid=2),
    ]
    first = sa.first_seen_map(evs)
    f = sa.funnel_from_events(evs, first)
    t = f["total"]
    assert t["view_sessions"] == 3, t                      # sA day 1, sA day 2, sB
    assert t["sessions"] == 3 and t["devices"] == 3, t      # devA twice (two days), devB once
    assert t["item_sessions"] == 1 and t["add_sessions"] == 1 and t["cart_sessions"] == 1
    assert t["checkout_sessions"] == 2, "checkout_start (and checkout) is the checkout step"
    assert t["order_sessions"] == 1 and t["order_devices"] == 1, "the order event is counted on its device"
    assert t["new_devices"] == 2, "each phone's first event, once"
    assert set(f["by_ref"]) == {"repone", "reptwo", None}, f["by_ref"]
    assert f["by_ref"]["repone"]["view_sessions"] == 1 and f["by_ref"]["repone"]["add_sessions"] == 1
    assert f["by_ref"][None]["view_sessions"] == 1, "the next day came with no link"
    rep = sa.funnel_from_events(evs, first, (1, "repone"))["total"]
    assert rep["view_sessions"] == 1 and rep["order_sessions"] == 1 and rep["checkout_sessions"] == 1, rep
    other = sa.funnel_from_events(evs, first, (2, ""))["total"]
    assert other["view_sessions"] == 1 and other["add_sessions"] == 0, other


@test("rails: taps (rail_click, reco_click, reorder) and adds made from a rail; a search or view carrying a rail is not a tap")
def _():
    from app import shop_analytics as sa
    evs = [_ev("rail_click", "s1", rail="best"), _ev("rail_click", "s1", rail="best"), _ev("reco_click", "s2", rail="best"),
           _ev("add", "s2", rail="best"), _ev("add", "s3"), _ev("reorder", "s3"), _ev("search", "s4", rail="quick_filter", q="deals"),
           _ev("view", "s4", rail="view_mode")]
    rows = {r["rail"]: r for r in sa.rails_payload(sa.rail_rows_from_events(evs))}
    assert set(rows) == {"best", "reorder"}, rows
    assert rows["best"] == {"rail": "best", "clicks": 3, "adds": 1, "sessions": 2}
    assert rows["reorder"]["clicks"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 3. analytics() before the views: the raw events, paged
# ═══════════════════════════════════════════════════════════════════════════════

OLD_KEYS = ("search", "rails", "engagement", "ops", "identity", "vitals", "days", "since", "funnel", "orders", "cancelled",
            "value_bhd", "aov_bhd", "units", "customers", "backorder_rate_pct", "top_products", "leaderboard",
            "attribution", "daily")


def _analytics_tables(n_views: int = 3) -> dict:
    events = [_ev("view", f"v{i}", minutes=i % 50) for i in range(n_views)]
    events += [_ev("item", "v0", item_code="T01"), _ev("add", "v0", item_code="T01"), _ev("cart", "v0"),
               _ev("checkout_start", "v0"), _ev("search", "v1", q="Type C"), _ev("search_zero", "v2", q="unknown thing"),
               _ev("rail_click", "v0", rail="best"), _ev("share", "v1"),
               _ev("vitals", "v0", lcp=1800, inp=120, cls=0.02, catalog_src="edge"), _ev("vitals", "v1", catalog_src="none"),
               _ev("view", "x1", ref="reptwo", sid=2)]
    orders = [_order(1, total_bhd=10.5), _order(2, status="confirmed", total_bhd=8.0, total_confirmed_bhd=6.0,
                                                 confirmed_at=_at(30)),
              _order(3, status="cancelled", cancelled_by="customer"), _order(4, is_test=True, total_bhd=999.0),
              _order(5, salesman_id=2, salesman_name="Rep Two", referral_code="reptwo", total_bhd=7.0),
              _order(6, source="salesman", placed_by="rep", referral_code=None, total_bhd=5.0)]
    lines = [_line(11, 1, "T01", 3, 7.5), _line(12, 1, "T02", 1, 3.0), _line(21, 2, "T01", 4, 8.0, qty_confirmed=3),
             _line(41, 4, "T09", 1, 999.0), _line(51, 5, "T03", 5, 7.0), _line(61, 6, "T01", 2, 5.0)]
    return _base_tables(shop_events=events, shop_orders=orders, shop_order_lines=lines)


@test("before the views: the raw events are read PAGED — 2,500 events all count (the old read stopped at 1,000)")
def _():
    from app import shop
    fake = _DB(**_analytics_tables(n_views=2500))
    with _patched(fake):
        a = shop.analytics(7)
    assert a["source"] == "events" and any("not set up yet" in n for n in a["notes"]), a["notes"]
    assert a["funnel"]["sessions"] == 2500 + 1, a["funnel"]            # every view page, plus Rep Two's
    pages = [c for c in fake.selects("shop_events") if c[2] != "ts"]
    assert len(pages) == 3 and [p[4][0] for p in pages] == [0, 1000, 2000], [p[4] for p in pages]
    # the same data through a single capped read is what the page used to see
    assert len(fake.table("shop_events").select("*").limit(20000).execute().data) == 1000


@test("before the views: every key the portal and the link-week card read, test orders out, money confirmed ?? requested")
def _():
    from app import shop
    fake = _DB(**_analytics_tables())
    with _patched(fake):
        a = shop.analytics(30)
    for k in OLD_KEYS + ("window", "source", "notes", "money_basis", "links", "merchants"):
        assert k in a, f"payload lost {k!r}"
    assert set(a["attribution"]) == {"by_referral", "by_src", "by_coupon"}
    assert a["orders"] == 4 and a["cancelled"] == 1, (a["orders"], a["cancelled"])      # 1, 2, 5, 6 (4 is a test)
    assert a["value_bhd"] == 28.5, a["value_bhd"]                                         # 10.5 + 6.0 + 7.0 + 5.0
    assert a["aov_bhd"] == 7.125
    assert a["money_basis"] == "confirmed_else_requested"
    assert a["funnel"]["checkouts"] == 1 and a["engagement"]["checkout_start"] == 1, "checkout_start is the checkout"
    assert a["funnel"]["orders"] == 3, "the rep's own order (source salesman) is not a storefront conversion"
    assert a["funnel"]["carts"] == 1 and a["engagement"]["new_devices"] is None, "new devices need the views"
    tp = {p["item_code"]: p for p in a["top_products"]}
    assert tp["T01"]["units"] == 3 + 3 + 2 and tp["T01"]["value_bhd"] == 7.5 + 6.0 + 5.0, tp["T01"]
    assert "T09" not in tp, "a test order's lines never count"
    assert a["search"]["searches"] == 2 and a["search"]["zero_results"] == 1
    assert a["rails"][0] == {"rail": "best", "clicks": 1, "adds": 0, "sessions": 1}
    assert a["vitals"]["samples"] == 1 and a["vitals"]["visits"] == 2 and a["vitals"]["lcp_ms_p75"] == 1800.0
    assert a["window"]["days"] == 30 and a["window"]["end"] == TODAY_BH.isoformat() and a["since"] == a["window"]["start"]
    day = [d for d in a["daily"] if d["orders"]]
    assert day and all(set(d) == {"date", "orders", "value_bhd", "sessions"} for d in a["daily"])
    json.dumps(a)


@test("before the views: a rep's scope — his orders filtered by the database, his link's events, no merchant list")
def _():
    from app import shop
    fake = _DB(**_analytics_tables())
    with _patched(fake):
        a = shop.analytics(7, salesman=REP)
    reads = [c for c in fake.selects("shop_orders") if "salesman_id" in str(c[3])]
    assert reads and ("eq", "salesman_id", 1) in reads[-1][3], "the rep scope is a database filter"
    assert a["orders"] == 3 and a["value_bhd"] == 21.5, (a["orders"], a["value_bhd"])     # 1, 2, 6
    assert a["funnel"]["sessions"] == 3, a["funnel"]                                     # his link's three visits
    assert "links" not in a and "merchants" not in a


@test("link-week: the rep's card reads the same analytics — checkout_start, confirmed money, plus carts and the source")
def _():
    from app import followups
    fake = _DB(**_analytics_tables())
    with _patched(fake):
        out = followups.link_week(REP, 7, force=True)
    for key in ("days", "since", "sessions", "item_views", "adds", "checkouts", "orders", "cancelled", "value_bhd",
                "aov_bhd", "customers", "conversion_pct", "shares", "top_products", "carts", "source"):
        assert key in out, key
    assert out["checkouts"] == 1 and out["carts"] == 1 and out["value_bhd"] == "21.500" and out["source"] == "events"
    assert out["top_products"][0]["item_code"] == "T01"


@test("before the views: the is_test probe is the only read naming it; a column dropped under a cached hit costs one re-read")
def _():
    import app.shop as s
    cols = set(s.ANALYTICS_ORDER_COLS.split(","))

    def pre_migration() -> dict:
        t = _analytics_tables()
        t["shop_orders"] = [{k: v for k, v in o.items() if k != "is_test"} for o in t["shop_orders"]]
        return t
    fake = _DB(columns={"shop_orders": cols}, **pre_migration())
    with _patched(fake):
        a = s.analytics(7)
        assert a["orders"] == 5, "without the column every order counts (the old reader's rule)"
        reads = fake.selects("shop_orders")
        assert reads[0][2] == "is_test" and all("is_test" not in str(c[2]) for c in reads[1:]), [c[2] for c in reads]
    fake = _DB(**pre_migration())
    with _patched(fake):
        assert s.has_column("shop_orders", "is_test") is True
        fake.columns["shop_orders"] = cols                                   # the reverse dropped it
        assert s.analytics(7)["orders"] == 5
        reads = fake.selects("shop_orders")
        assert "is_test" in str(reads[-2][2]) and "is_test" not in str(reads[-1][2])
        assert "shop_orders.is_test" not in s._col_cache


# ═══════════════════════════════════════════════════════════════════════════════
# 4. analytics() on the views
# ═══════════════════════════════════════════════════════════════════════════════

def _funnel_row(g, day=None, ref=None, **vals):
    from app import shop_analytics as sa
    return {"g": g, "day": day, "ref": ref, **{k: vals.get(k, 0) for k in sa.FUNNEL_KEYS}}


def _view_answers(fail: set | None = None):
    """An RPC stand-in: answers each of the API's SQL with rows shaped like the views'."""
    from app import shop_analytics as sa
    d1 = (TODAY_BH - timedelta(days=1)).isoformat()
    fail = fail or set()

    def handler(sql, params):
        name = {sa.FUNNEL_SQL: "funnel", sa.SEARCH_SQL: "search", sa.RAILS_SQL: "rails", sa.VITALS_SQL: "vitals",
                sa.MERCHANTS_SQL: "merchants", sa.MERCHANT_COUNTS_SQL: "counts", sa.MERCHANT_SQL: "merchant"}.get(sql)
        assert name, f"unexpected SQL: {sql[:80]}"
        if name in fail:
            raise RuntimeError(f"{name} timed out")
        if name == "funnel":
            return [_funnel_row("all", view_sessions=40, item_sessions=20, add_sessions=9, cart_sessions=7,
                                checkout_sessions=5, order_sessions=3, devices=38, new_devices=30, sessions=41,
                                search_sessions=6, share_sessions=2),
                    _funnel_row("day", day=d1, view_sessions=40, devices=38),
                    _funnel_row("ref", ref="repone", view_sessions=25, devices=24),
                    _funnel_row("ref", ref=None, view_sessions=15, devices=14)]
        if name == "search":
            return [{"term": "uk12", "searches": 5, "zero": 0, "sessions": 4, "devices": 4, "last_seen": _at(1),
                     "total_searches": 9, "total_zero": 3, "total_terms": 3},
                    {"term": "sandisk", "searches": 3, "zero": 3, "sessions": 2, "devices": 2, "last_seen": _at(2),
                     "total_searches": 9, "total_zero": 3, "total_terms": 3}]
        if name == "rails":
            return [{"rail": "best", "clicks": 12, "adds": 3, "sessions": 8}]
        if name == "vitals":
            return [{"visits": 50, "samples": 48, "lcp_ms_p75": 2204, "inp_ms_p75": 168, "cls_p75": 0.003,
                     "lcp_ttfb_ms_p75": None, "lcp_delay_ms_p75": None, "lcp_load_ms_p75": None,
                     "lcp_render_ms_p75": None, "catalog_ms_p75": 900,
                     "catalog_src": [{"src": "pre-edge-hit", "visits": 30}, {"src": "none", "visits": 20}]}]
        if name == "merchants":
            return [{"customer_id": 51, "shop": "Test Shop 1", "area": "Test Area", "rep_id": 1, "rep_name": "Rep One",
                     "orders": 5, "open_orders": 0, "value_bhd": 80.0, "value_90d_bhd": 60.0, "value_30d_bhd": 10.0,
                     "aov_bhd": 16.0, "first_order_at": _at(days=-100), "last_order_at": _at(days=-40),
                     "days_since_last": 41, "order_days": 5, "cadence_days": 14.0, "cadence_basis": "own",
                     "due_status": "overdue", "dormant": True, "dormant_signals": 2, "is_new": False},
                    {"customer_id": 52, "shop": "Test Shop 2", "area": None, "rep_id": 2, "rep_name": "Rep Two",
                     "orders": 1, "open_orders": 1, "value_bhd": 7.0, "value_90d_bhd": 7.0, "value_30d_bhd": 7.0,
                     "aov_bhd": 7.0, "first_order_at": _at(), "last_order_at": _at(), "days_since_last": 1,
                     "order_days": 1, "cadence_days": None, "cadence_basis": None, "due_status": "unknown",
                     "dormant": False, "dormant_signals": 0, "is_new": True}]
        if name == "counts":
            return [{"merchants": 2, "new": 1, "returning": 1, "due": 0, "overdue": 1, "dormant": 1, "with_cadence": 1}]
        return []
    return handler


@test("on the views: the SQL is bound with the window and the scope, and no raw event is read beyond the first-event probe")
def _():
    from app import shop
    from app import shop_analytics as sa
    fake = _DB(rpc=_view_answers(), **_analytics_tables())
    with _patched(fake):
        a = shop.analytics(7)
    assert a["source"] == "views" and a["notes"] == [], a["notes"]
    start = (TODAY_BH - timedelta(days=6)).isoformat()
    for sql, params in fake.rpc_calls:
        if sql in (sa.FUNNEL_SQL, sa.SEARCH_SQL, sa.RAILS_SQL, sa.VITALS_SQL):
            assert params == [start, TODAY_BH.isoformat(), "", ""], params
    assert {sql for sql, _ in fake.rpc_calls} >= {sa.FUNNEL_SQL, sa.SEARCH_SQL, sa.RAILS_SQL, sa.VITALS_SQL,
                                                  sa.MERCHANTS_SQL, sa.MERCHANT_COUNTS_SQL}
    ev_reads = fake.selects("shop_events")
    assert [c[2] for c in ev_reads] == ["ts"], "only the first-event probe touches shop_events"
    assert a["funnel"]["sessions"] == 40 and a["funnel"]["checkouts"] == 5 and a["funnel"]["carts"] == 7
    assert a["funnel"]["orders"] == 3 and a["funnel"]["conversion_pct"] == 7.5, a["funnel"]   # 1, 2, 5 on the market
    assert a["engagement"]["new_devices"] == 30 and a["engagement"]["devices"] == 38
    assert a["search"]["searches"] == 9 and a["search"]["zero_results"] == 3 and a["search"]["zero_rate_pct"] == 33.3
    assert a["search"]["zero_terms"][0]["term"] == "sandisk"
    assert a["rails"] == [{"rail": "best", "clicks": 12, "adds": 3, "sessions": 8}]
    assert a["vitals"]["lcp_ms_p75"] == 2204.0 and a["vitals"]["catalog_src"][0] == {"src": "pre-edge-hit", "visits": 30}
    links = {r["key"]: r for r in a["links"]}
    assert links["repone"]["rep"] == "Rep One" and links["repone"]["sessions"] == 25 and links["repone"]["orders"] == 2
    assert links["reptwo"]["orders"] == 1 and links["reptwo"]["sessions"] == 0 and links["direct"]["sessions"] == 15
    assert links["repone"]["conversion_pct"] == 8.0
    m = a["merchants"]
    assert m["available"] is True and m["counts"]["overdue"] == 1
    assert [w["customer_id"] for w in m["watch"]] == [51], "due / overdue / dormant only"
    act = {r["customer_id"]: r for r in m["active"]}
    assert act[51]["due_status"] == "overdue" and act[51]["dormant"] is True and act[52]["is_new"] is True
    assert a["daily"][-1]["date"] <= TODAY_BH.isoformat()


@test("on the views: a rep's scope binds his id and code; no merchant or link list; the link-week card reads it")
def _():
    from app import followups, shop
    from app import shop_analytics as sa
    fake = _DB(rpc=_view_answers(), **_analytics_tables())
    with _patched(fake):
        a = shop.analytics(7, salesman=REP)
        card = followups.link_week(REP, 7, force=True)
    scoped = [p for sql, p in fake.rpc_calls if sql == sa.FUNNEL_SQL]
    assert scoped and all(p[2:] == ["1", "repone"] for p in scoped), scoped
    assert not any(sql in (sa.MERCHANTS_SQL, sa.MERCHANT_COUNTS_SQL) for sql, _ in fake.rpc_calls)
    assert "merchants" not in a and "links" not in a
    assert card["sessions"] == 40 and card["checkouts"] == 5 and card["source"] == "views"


@test("on the views: a missing view is remembered (no probe per request), a stand-in without the total row falls back")
def _():
    from app import shop
    from app import shop_analytics as sa
    fake = _DB(rpc=None, **_analytics_tables())                 # the RPC answers "relation does not exist"
    with _patched(fake):
        assert shop.analytics(7)["source"] == "events"
        n = len(fake.rpc_calls)
        assert shop.analytics(7)["source"] == "events" and len(fake.rpc_calls) == n, "remembered for 5 minutes"
        sa._views.update(at=time.time() - sa.VIEWS_MISS_TTL_S - 1)
        shop.analytics(7)
        assert len(fake.rpc_calls) == n + 1, "re-probed after"
    fake = _DB(rpc=lambda sql, params: [], **_analytics_tables())
    with _patched(fake):
        a = shop.analytics(7)
        assert a["source"] == "events" and sa._views["ok"] is not False, "an empty answer is not the view, and not 'missing'"


@test("on the views: a failing section costs a note, never the page")
def _():
    from app import shop
    fake = _DB(rpc=_view_answers(fail={"search", "vitals", "merchants"}), **_analytics_tables())
    with _patched(fake):
        a = shop.analytics(7)
    assert a["source"] == "views" and a["funnel"]["sessions"] == 40
    assert a["search"]["searches"] == 0 and a["vitals"]["samples"] == 0 and a["vitals"]["catalog_src"] == []
    assert a["merchants"]["available"] is False and a["merchants"]["active"], "the period's merchants still come from its orders"
    assert any("Search terms" in n for n in a["notes"]) and any("merchant list" in n for n in a["notes"])


@test("the window: 'before_history' when the window starts before the first event; the first event is read once")
def _():
    from app import shop
    evs = _analytics_tables()["shop_events"]
    first = min(e["ts"] for e in evs)
    fake = _DB(rpc=_view_answers(), **_analytics_tables())
    with _patched(fake):
        a = shop.analytics(30)
        shop.analytics(7)
    first_day = (datetime.fromisoformat(first) + timedelta(hours=3)).date().isoformat()
    assert a["window"]["events_since"] == first_day
    assert a["window"]["before_history"] is True
    assert len([c for c in fake.selects("shop_events") if c[2] == "ts"]) == 1, "remembered once known"


@test("privacy: no phone, e-mail or person's name in the analytics payload (company scope, merchants included)")
def _():
    from app import shop
    fake = _DB(rpc=_view_answers(), **_analytics_tables())
    with _patched(fake):
        text = json.dumps(shop.analytics(30))
    for bad in PHONES + ("t@example.com", "Test Person", "customer_phone", "customer_email", "customer_name", "dev-"):
        assert bad not in text, f"{bad} leaked into the analytics payload"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. the merchant profile
# ═══════════════════════════════════════════════════════════════════════════════

T = date(2026, 9, 27)


def _mo(oid, day: date, value=10.0, status="delivered", **over):
    ts = datetime(day.year, day.month, day.day, 11, tzinfo=BAHRAIN).astimezone(timezone.utc).isoformat()
    o = {"id": oid, "customer_id": 70, "status": status, "created_at": ts, "total_bhd": value,
         "total_confirmed_bhd": value if status in ("confirmed", "delivered") else None, "salesman_id": 1,
         "is_test": False, "order_no": f"YQ-T-{oid}", "units_count": 2, "items_count": 1, "salesman_name": "Rep One"}
    o.update(over)
    return o


def _facts(orders, lines=(), customer=None, **kw):
    from app import shop_analytics as sa
    customer = customer or {"id": 70, "shop": "Test Shop", "area": "Test Area", "salesman_id": None,
                            "sticky_salesman_id": None, "focus_customer_id": None}
    return sa.merchant_facts(customer, list(orders), list(lines), categories={"T01": "CABLE", "T02": "CHARGER"},
                             rep_names={1: "Rep One", 2: "Rep Two"}, today=kw.pop("today", T), **kw)


@test("merchant facts: cadence from distinct order DAYS; own rhythm from 4 days; ok / due / overdue at 1.5x and 2x")
def _():
    days = [T - timedelta(days=d) for d in (84, 63, 42, 21)]
    orders = [_mo(i, d) for i, d in enumerate(days, 1)] + [_mo(9, days[-1], value=5.0)]   # a split order the same day
    f = _facts(orders)
    assert f["order_days"] == 4 and f["orders"] == 5 and f["median_gap_days"] == 21.0
    assert f["cadence_days"] == 21.0 and f["cadence_basis"] == "own" and f["days_since_last"] == 21
    assert f["due_status"] == "ok"
    assert _facts(orders, today=T + timedelta(days=10))["due_status"] == "ok", "31 days is not more than 1.5 x 21"
    due = _facts(orders, today=T + timedelta(days=13))
    assert due["days_since_last"] == 34 and due["due_status"] == "due" and due["sig_overdue"] is False
    late = _facts(orders, today=T + timedelta(days=22))
    assert late["days_since_last"] == 43 and late["due_status"] == "overdue" and late["sig_overdue"] is True
    three = _facts(orders[1:4])
    assert three["cadence_days"] is None and three["due_status"] == "unknown" and three["median_gap_days"] == 21.0
    borrowed = _facts(orders[1:4], all_gaps=[10] * 10)
    assert borrowed["cadence_days"] == 10.0 and borrowed["cadence_basis"] == "all_shops"
    assert _facts(orders[1:4], all_gaps=[10] * 9)["cadence_basis"] is None, "fewer than 10 gaps: no all-shops cadence"


@test("merchant facts: money confirmed ?? requested, test and cancelled out, windows, the dormant rule (2 of 3 signs)")
def _():
    orders = [_mo(1, T - timedelta(days=100), 40.0), _mo(2, T - timedelta(days=90), 40.0), _mo(3, T - timedelta(days=75), 20.0),
              _mo(4, T - timedelta(days=65), 20.0), _mo(5, T - timedelta(days=50), 10.0, total_confirmed_bhd=8.0),
              _mo(6, T - timedelta(days=5), 7.0, status="new"), _mo(7, T - timedelta(days=4), 500.0, is_test=True),
              _mo(8, T - timedelta(days=3), 9.0, status="cancelled")]
    lines = [_line(1, 1, "T01", 4, 20.0), _line(2, 1, "T02", 2, 20.0), _line(3, 2, "T02", 2, 40.0),
             _line(4, 3, "T01", 2, 20.0), _line(5, 4, "T02", 1, 20.0), _line(6, 5, "T01", 5, 10.0, qty_confirmed=4),
             _line(7, 6, "T01", 1, 7.0), _line(8, 7, "T09", 1, 500.0)]
    f = _facts(orders, lines)
    assert f["orders"] == 6 and f["cancelled_orders"] == 1 and f["open_orders"] == 1
    assert f["value_bhd"] == 40 + 40 + 20 + 20 + 8 + 7 and f["aov_bhd"] == round(135 / 6, 3)
    assert f["confirmed_value_bhd"] == 128.0 and f["delivered_value_bhd"] == 128.0
    assert f["value_60d_bhd"] == 15.0 and f["value_prev_60d_bhd"] == 120.0 and f["value_90d_bhd"] == 55.0, \
        (f["value_60d_bhd"], f["value_prev_60d_bhd"], f["value_90d_bhd"])
    assert f["cadence_days"] == 15.0 and f["sig_overdue"] is False, "gaps 10, 15, 10, 15, 45 days"
    assert f["sig_value_drop"] is True, "15 < 60 % of 120"
    assert f["skus_60d"] == 1 and f["skus_prev_60d"] == 2 and f["sig_range_drop"] is True, "1 < 70 % of 2"
    assert f["dormant_signals"] == 2 and f["dormant"] is True
    assert f["top_categories"][0] == {"category": "CHARGER", "value_bhd": 80.0, "units": 5}, f["top_categories"]
    assert f["top_skus"][0]["item_code"] == "T02" and f["top_skus"][1]["units"] == 4 + 2 + 4 + 1, f["top_skus"]
    assert f["top_skus"][1]["value_bhd"] == 20.0 + 20.0 + 8.0 + 7.0, "the confirmed line pro rata: 4 of 5 of 10.000"
    assert "T09" not in {s["item_code"] for s in f["top_skus"]}


@test("merchant facts: the rep — assigned, else the sticky rep, else the last order's; is_new within 30 days")
def _():
    orders = [_mo(1, T - timedelta(days=10), salesman_id=2), _mo(2, T - timedelta(days=2), salesman_id=1)]
    base = {"id": 70, "shop": " Test Shop ", "area": "", "salesman_id": None, "sticky_salesman_id": None}
    f = _facts(orders, customer=dict(base))
    assert (f["rep_id"], f["rep_name"], f["rep_source"]) == (1, "Rep One", "last_order")
    assert f["shop"] == "Test Shop" and f["area"] is None and f["is_new"] is True
    assert _facts(orders, customer={**base, "sticky_salesman_id": 2})["rep_source"] == "sticky"
    assert _facts(orders, customer={**base, "salesman_id": 2, "sticky_salesman_id": 1})[("rep_name")] == "Rep Two"
    none = _facts([], customer=dict(base))
    assert none["orders"] == 0 and none["due_status"] == "unknown" and none["aov_bhd"] is None and none["dormant"] is False


@test("insights: rules with evidence and the rule that fired — 'usually orders every ~21 days — now 34 days'")
def _():
    from app import shop_analytics as sa
    days = [T - timedelta(days=d) for d in (84, 63, 42, 21)]
    late = _facts([_mo(i, d, 30.0) for i, d in enumerate(days, 1)], [_line(i, i, "T01", 2, 30.0) for i in range(1, 5)],
                  today=T + timedelta(days=13))
    ins = sa.insights(late)
    cad = next(x for x in ins if x["kind"] == "cadence")
    assert cad["level"] == "watch" and cad["title"] == "Usually orders every ~21 days — now 34 days.", cad["title"]
    assert "median gap is 21 days" in cad["evidence"] and cad["rule"].startswith("Due")
    later = [x for x in sa.insights(_facts([_mo(i, d, 30.0) for i, d in enumerate(days, 1)],
                                           today=T + timedelta(days=22))) if x["kind"] == "cadence"][0]
    assert later["level"] == "act" and later["rule"].startswith("Overdue") and "now 43 days" in later["title"]
    mix = next(x for x in ins if x["kind"] == "mix")
    assert "Cable" in mix["title"] and "100 %" in mix["title"]
    for x in ins:
        assert set(x) == {"kind", "level", "title", "evidence", "rule"} and x["evidence"] and x["rule"], x
        assert x["level"] in ("act", "watch", "info")
    once = sa.insights(_facts([_mo(1, T - timedelta(days=40))]))
    assert [x["kind"] for x in once] == ["one_order"] and "40 days ago" in once[0]["title"]
    fresh = sa.insights(_facts([_mo(1, T - timedelta(days=3))]))
    assert {x["kind"] for x in fresh} == {"rhythm", "new"}
    assert sa.insights(_facts([])) == []


@test("trend: too early before 60 days of history; then the last 30 days against its own usual 30 days")
def _():
    from app import shop_analytics as sa
    young = _facts([_mo(1, T - timedelta(days=20), 50.0)])
    t = sa.trend(young, T)
    assert t["enough_history"] is False and t["baseline_30d_bhd"] is None and "Too early" in t["text"]
    old = _facts([_mo(1, T - timedelta(days=119), 90.0), _mo(2, T - timedelta(days=10), 15.0)])
    t = sa.trend(old, T)
    assert t["enough_history"] is True and t["baseline_30d_bhd"] == 30.0 and t["change_pct"] == -50.0, t
    assert "against its usual BHD 30.000 per 30 days" in t["text"]


@test("monthly: the last six Bahrain months, empty months included, test and cancelled orders out")
def _():
    from app import shop_analytics as sa
    rows = sa.monthly([_mo(1, date(2026, 9, 2), 10.0), _mo(2, date(2026, 7, 31), 5.0, total_confirmed_bhd=4.0),
                       _mo(3, date(2026, 9, 3), 99.0, is_test=True), _mo(4, date(2026, 9, 4), 7.0, status="cancelled"),
                       _mo(5, date(2026, 1, 5), 1.0)], 6, date(2026, 9, 27))
    assert [r["month"] for r in rows] == ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    assert rows[-1] == {"month": "2026-09", "orders": 1, "value_bhd": 10.0} and rows[3]["value_bhd"] == 4.0
    assert rows[4]["orders"] == 0


def _profile_tables() -> dict:
    days = [TODAY_BH - timedelta(days=d) for d in (40, 30, 20, 10)]
    orders = [_mo(i, d, 12.0, customer_id=70, customer_phone=PHONES[0], customer_name="Test Person",
                  customer_email="t@example.com") for i, d in enumerate(days, 1)]
    orders.append(_mo(9, TODAY_BH, 3.0, customer_id=71))
    lines = [_line(i, i, "T01", 2, 12.0) for i in range(1, 5)]
    customers = [{"id": 70, "shop": "Test Shop", "area": "Test Area", "salesman_id": None, "sticky_salesman_id": 2,
                  "focus_customer_id": 900, "phone": PHONES[0], "name": "Test Person", "email": "t@example.com"}]
    return _base_tables(shop_orders=orders, shop_order_lines=lines, shop_customers=customers,
                        customers=[{"id": 900, "name": "TEST FOCUS SHOP"}])


@test("merchant profile before the views: computed from its own orders, with the Focus link, a note, and no contact")
def _():
    from app import shop_analytics as sa
    fake = _DB(**_profile_tables())
    with _patched(fake, ctx_items={"T01": {"category": "CABLE"}}):
        p = sa.merchant_profile(70)
        assert sa.merchant_profile(12345) is None
    m = p["merchant"]
    assert p["source"] == "orders" and p["notes"], p["notes"]
    assert m["orders"] == 4 and m["value_bhd"] == 48.0 and m["cadence_days"] == 10.0 and m["cadence_basis"] == "own"
    assert m["rep_name"] == "Rep Two" and m["rep_source"] == "sticky" and m["focus_customer_name"] == "TEST FOCUS SHOP"
    assert m["top_categories"][0]["category"] == "CABLE"
    assert [o["order_no"] for o in p["orders"]][0] == "YQ-T-4" and p["orders"][0]["status_label"] == "Delivered"
    assert len(p["monthly"]) == 6 and p["insights"] and p["trend"]["text"]
    reads = fake.selects("shop_orders")
    assert all(("eq", "customer_id", 70) in c[3] or "is_test" == c[2] or ("eq", "customer_id", 12345) in c[3] for c in reads)
    text = json.dumps(p)
    for bad in PHONES + ("t@example.com", "Test Person"):
        assert bad not in text, f"{bad} leaked into the merchant profile"


@test("merchant profile on the view: the v_merchant_360 row, orders from the database, an unknown id is None")
def _():
    from app import shop_analytics as sa
    row = {"customer_id": 70, "shop": "Test Shop", "orders": 4, "order_days": 4, "value_bhd": 48.0,
           "value_30d_bhd": 24.0, "value_60d_bhd": 48.0, "value_prev_60d_bhd": 0, "days_since_last": 10,
           "cadence_days": 10.0, "cadence_basis": "own", "due_status": "ok", "dormant": False, "is_new": False,
           "first_order_at": _at(days=-40), "last_order_at": _at(days=-10),
           "top_categories": json.dumps([{"category": "CABLE", "value_bhd": 48.0, "units": 8}]), "top_skus": "[]"}

    def rpc(sql, params):
        assert sql == sa.MERCHANT_SQL
        return [row] if params == ["70"] else []
    fake = _DB(rpc=rpc, **_profile_tables())
    with _patched(fake):
        p = sa.merchant_profile(70)
        assert sa.merchant_profile(71) is None, "the view answers for every merchant: no row = no merchant"
    assert p["source"] == "views" and p["notes"] == [] and p["merchant"]["top_categories"][0]["category"] == "CABLE"
    assert p["insights"][0]["kind"] == "cadence" and p["insights"][0]["level"] == "info"
    assert len(p["orders"]) == 4 and all(o["value_bhd"] == 12.0 for o in p["orders"])
    assert not fake.selects("shop_customers"), "the view carries the merchant: no second read"


@test("route: GET /shop/analytics/merchant/{id} — admin and management 200, a rep 403, unknown 404; gated by Shop Admin | Shop Orders")
def _():
    from fastapi.routing import APIRoute
    from fastapi.testclient import TestClient
    import app.main as m
    from app import user_auth
    from app.auth import CurrentUser, get_current_user
    route = next(r for r in m.app.routes if isinstance(r, APIRoute) and r.path == "/shop/analytics/merchant/{customer_id}")
    gate = [getattr(d.call, "features", None) for d in route.dependant.dependencies]
    assert ("Shop Admin", "Shop Orders") in gate, gate
    users = {"boss": CurrentUser(user_id="a", email="boss@example.com", role="admin"),
             "mgmt": CurrentUser(user_id="m", email="mgmt@example.com", role="management"),
             "rep": CurrentUser(user_id="r", email="rep@example.com", role="salesman")}
    saved_row = user_auth._user_row
    user_auth._user_row = lambda email: {"email": email, "features": ["Shop Orders"], "status": "active", "role": "x"}
    fake = _DB(**_profile_tables())
    try:
        with _patched(fake, ctx_items={"T01": {"category": "CABLE"}}):
            c = TestClient(m.app)
            for who, want in (("boss", 200), ("mgmt", 200), ("rep", 403)):
                m.app.dependency_overrides[get_current_user] = (lambda u=users[who]: u)
                r = c.get("/shop/analytics/merchant/70")
                assert r.status_code == want, (who, r.status_code, r.text[:200])
            m.app.dependency_overrides[get_current_user] = lambda: users["boss"]
            assert c.get("/shop/analytics/merchant/12345").status_code == 404
            body = c.get("/shop/analytics/merchant/70").text
            assert PHONES[0] not in body and "t@example.com" not in body
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)
        user_auth._user_row = saved_row


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the SQL, the migration and its reverse, as text
# ═══════════════════════════════════════════════════════════════════════════════

def _code(sql: str) -> str:
    """The SQL without its -- comments, lower-cased (the checks read statements, not prose)."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines()).lower()


def _views_in(sql: str) -> dict[str, str]:
    return dict(re.findall(r"create or replace view (\w+) as\n(.*?);\n\n", sql, re.S))


def _select_names(body: str) -> list[str]:
    """The output column names of a view body's final SELECT (its `as name` aliases, in order)."""
    last = body[body.rindex("\nselect "):] if "\nselect " in body else body
    head = last.split("\nfrom ", 1)[0]
    return re.findall(r"\bas (\w+)\s*(?:,|$)", head, re.M) or re.findall(r"^\s*(?:\w+\.)?(\w+),?\s*$", head, re.M)


@test("API SQL: bound parameters only ($1..$4), the grand-total row always asked for, the views named")
def _():
    from app import shop_analytics as sa
    for name in ("FUNNEL_SQL", "SEARCH_SQL", "RAILS_SQL", "VITALS_SQL"):
        sql = getattr(sa, name)
        assert set(re.findall(r"\$(\d)", sql)) == {"1", "2", "3", "4"}, name
        assert "{" not in sql and "%s" not in sql, name
    assert "GROUPING SETS ((day), (referral_code), ())" in sa.FUNNEL_SQL
    assert re.findall(r"\$(\d)", sa.MERCHANT_SQL) == ["1"] and "$" not in sa.MERCHANTS_SQL + sa.MERCHANT_COUNTS_SQL
    for view, sql in (("v_shop_funnel_daily", sa.FUNNEL_SQL), ("v_shop_search_daily", sa.SEARCH_SQL),
                      ("v_shop_rail_perf", sa.RAILS_SQL), ("v_shop_vitals", sa.VITALS_SQL), ("v_merchant_360", sa.MERCHANTS_SQL)):
        assert view in sql, view
    assert "shop_events" not in " ".join((sa.FUNNEL_SQL, sa.SEARCH_SQL, sa.RAILS_SQL, sa.VITALS_SQL)), "views only"


@test("migration: additive, idempotent views; revoked from anon/authenticated, granted to yq_readonly; nothing personal")
def _():
    sql = MIGRATION.read_text(encoding="utf-8")
    low = _code(sql)
    views = _views_in(sql)
    assert set(views) == {"v_shop_funnel_daily", "v_shop_search_daily", "v_shop_search_terms", "v_shop_rail_perf",
                          "v_shop_vitals", "v_merchant_360"}, set(views)
    for bad in ("drop ", "cascade", "delete from", "truncate", "insert into", "alter table", "with (security_invoker"):
        assert bad not in low, bad
    assert not re.search(r"\bupdate\s+\w+\s+set\b", low)
    for v in ("v_shop_funnel_daily", "v_shop_search_daily", "v_shop_vitals", "v_merchant_360"):
        assert f"revoke all on {v} from anon, authenticated;" in low, v
        assert f"grant select on {v} to yq_readonly;" in low, v
    for name, body in views.items():
        for personal in ("customer_phone", "customer_email", "customer_name", "ip_hash", "ua", "device_ids", "phone",
                         "email"):
            assert not re.search(rf"\b{personal}\b", body), (name, personal)
    assert "raise exception" in low and "must be owned by postgres" in low and "security_invoker" in low
    assert "'asia/bahrain'" in low and "checkout_start" in low and "interval '30 seconds'" in low
    assert "not coalesce(e.meta ? 'rail', false)" in low, "chip / facet pings are not typed searches"


@test("migration: the two REPLACED views keep their live columns in order; the rail view only appends")
def _():
    views = _views_in(MIGRATION.read_text(encoding="utf-8"))
    terms = _select_names(views["v_shop_search_terms"])
    assert terms == ["term", "searches", "zero_results", "sessions", "last_seen"], terms
    rail = _select_names(views["v_shop_rail_perf"])
    assert rail[:6] == ["rail", "day", "clicks", "reco_clicks", "adds", "sessions"], rail
    assert rail[6:] == ["referral_code", "salesman_id", "reorders", "devices"], rail
    assert "sum(d.searches)::bigint" in views["v_shop_search_terms"], "bigint, like the live column"


@test("reverse: the 16-Sep definitions back (search terms first), rail view dropped and re-created with its grants, no CASCADE")
def _():
    rev = REVERSE.read_text(encoding="utf-8")
    low = _code(rev)
    orig = _read("scripts/marketplace_migration.sql")
    terms_orig = orig.split("create or replace view v_shop_search_terms as\n", 1)[1].split(";", 1)[0]
    rail_orig = orig.split("create or replace view v_shop_rail_perf as\n", 1)[1].split(";", 1)[0]
    assert terms_orig.strip() in rev and rail_orig.strip() in rev, "the reverse restores the exact 16-Sep bodies"
    assert low.index("create or replace view v_shop_search_terms") < low.index("drop view if exists v_shop_search_daily")
    for v in ("v_shop_funnel_daily", "v_shop_search_daily", "v_shop_vitals", "v_merchant_360", "v_shop_rail_perf"):
        assert f"drop view if exists {v};" in low, v
    assert "cascade" not in low and "delete from" not in low and "truncate" not in low
    assert "grant select on v_shop_rail_perf to yq_readonly;" in low
    assert "revoke all on v_shop_rail_perf from anon, authenticated;" in low
    assert "raise exception" in low


@test("docs: the migration is listed in docs/MIGRATIONS.md and the payload in docs/SHOP.md")
def _():
    mig = _read("docs/MIGRATIONS.md")
    assert "r7d_analytics_views_migration.sql" in mig and "r7d_analytics_views_reverse.sql" in mig
    shop = _read("docs/SHOP.md")
    assert "/shop/analytics/merchant/{id}" in shop and "money_basis" in shop and "v_merchant_360" in shop


# ═══════════════════════════════════════════════════════════════════════════════
# 7. the portal, from the sources
# ═══════════════════════════════════════════════════════════════════════════════

@test("portal: period chips that say 'since', the real funnel, searches + zero-result demand, rails, rep links, site speed footnote")
def _():
    src = _read("web/src/pages/ShopAnalytics.tsx")
    for needle in ("periodChips", "Since ${dayMonth(since)}", "all the history there is", "'Started checkout'",
                   "Asked for, not found", "Rep links", "Home rails", "Worth a call", "function SiteSpeed",
                   "/shop-analytics/merchant/", "from '@/components/LoadError'", "Confirmed, else as ordered"):
        assert needle in src, needle
    assert "Speed (LCP p75)" not in src, "the vitals tile moved to the Site speed footnote"
    assert "function errorText" not in src


@test("portal: the merchant page labels INSIGHTS with evidence and the rule; the route is gated for the office and management")
def _():
    src = _read("web/src/pages/MerchantProfile.tsx")
    for needle in ("INSIGHT", "Rule: {x.rule}", "{x.evidence}", "not a prediction", "/shop/analytics/merchant/",
                   "Against its own usual", "Not linked to a Focus customer yet", "from '@/components/LoadError'"):
        assert needle in src, needle
    for pii in ("customer_phone", "whatsapp", "mailto:"):
        assert pii not in src, pii
    app_src = _read("web/src/App.tsx")
    assert 'path="shop-analytics/merchant/:id"' in app_src and "function ShopAnalyticsGate" in app_src
    assert "isManagement(me) ? 'Shop Orders' : 'Shop Admin'" in app_src


# ═══════════════════════════════════════════════════════════════════════════════
# 8. a local Postgres replay (SKIPs cleanly without a local cluster — never production)
# ═══════════════════════════════════════════════════════════════════════════════

LOCAL_DSN = os.environ.get("YQ_LOCAL_PG_R7D", os.environ.get("YQ_LOCAL_PG", "postgresql://postgres@localhost:55432/postgres"))

BASE_SCHEMA = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then create role yq_readonly nologin; end if;
end $$;
create table salesmen (id bigint primary key, name text, referral_code text);
create table customers (id bigint primary key, name text);
create table catalog_items (id bigint generated always as identity primary key, item_code text, category text);
create table shop_customers (id bigint primary key, phone text, name text, shop text, area text, salesman_id bigint,
  sticky_salesman_id bigint, focus_customer_id bigint);
create table shop_events (id bigint primary key, ts timestamptz not null, session_id text, event text not null,
  item_code text, referral_code text, salesman_id bigint, src text, ua text, ip_hash text, device_id text,
  customer_id bigint, meta jsonb);
create table shop_orders (id bigint primary key, customer_id bigint, salesman_id bigint, status text not null,
  created_at timestamptz not null, total_bhd numeric(12,3) not null, total_confirmed_bhd numeric(12,3),
  confirmed_at timestamptz, is_test boolean default false);
create table shop_order_lines (id bigint primary key, order_id bigint not null, item_code text not null, display_name text,
  qty integer not null, line_total_bhd numeric(12,3) not null, qty_confirmed integer, line_status text default 'ok',
  line_total_confirmed numeric(12,3));
"""


def _replay_rows():
    """Synthetic events / orders / lines exercising every rule (typing, chips, a session over two days,
    a ref switch, the order event without a session, split same-day orders, a confirmed cut)."""
    base = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)
    ev = []

    def e(i, minutes, event, sess, dev, ref=None, sid=None, meta=None):
        ev.append({"id": i, "ts": (base + timedelta(minutes=minutes)).isoformat(), "event": event, "session_id": sess,
                   "item_code": None, "referral_code": ref, "salesman_id": sid, "src": "direct", "device_id": dev,
                   "meta": meta})
    e(1, 0, "view", "s1", "d1")
    e(2, 1, "view", "s1", "d1", "repone", 1)
    e(3, 2, "add", "s1", "d1", meta={"rail": "grid"})
    e(4, 3, "checkout_start", "s1", "d1")
    e(5, 4, "order", None, "d1", "repone", 1, {"attribution": "session_ref"})
    e(6, 5, "search", "s1", "d1", meta={"q": "uk", "results": 5})
    e(7, 5.2, "search", "s1", "d1", meta={"q": "uk12", "results": 2})
    e(8, 6, "search", "s1", "d1", meta={"q": "instock", "rail": "quick_filter"})
    e(9, 7, "search_zero", "s1", "d1", meta={"q": "sandisk", "results": 0})
    e(10, 8, "rail_click", "s1", "d1", meta={"rail": "best"})
    e(11, 9, "vitals", "s1", "d1", meta={"lcp": 1800, "inp": 120, "cls": 0.02, "catalog_src": "edge"})
    e(12, 60 * 24, "view", "s1", "d1")                                         # the next day, same session
    e(13, 30, "view", "s2", "d2", "reptwo", 2)
    e(14, 31, "error", "s2", "d2", "reptwo", 2)
    e(15, 32, "item", "s2", "d2", "reptwo", 2)
    e(16, 33, "view", None, None)                                              # neither device nor session
    orders = [(1, 1, 1, "delivered", "2026-09-21T08:00:00+00", 10.0, 10.0, "2026-09-21T09:00:00+00", False),
              (2, 1, 1, "confirmed", "2026-09-21T08:05:00+00", 12.0, 9.0, "2026-09-21T09:00:00+00", False),
              (3, 1, 1, "new", "2026-09-25T08:00:00+00", 5.0, None, None, False),
              (4, 2, 2, "cancelled", "2026-09-22T08:00:00+00", 7.0, None, None, False),
              (5, 2, 2, "delivered", "2026-09-22T08:00:00+00", 99.0, 99.0, "2026-09-22T09:00:00+00", True)]
    lines = [(1, 1, "T01", "Test T01", 4, 10.0, 4, "ok", 10.0), (2, 2, "T02", "Test T02", 6, 12.0, 4, "ok", None),
             (3, 2, "T01", "Test T01", 1, 0.5, None, "removed", None), (4, 3, "T01", "Test T01", 2, 5.0, None, "ok", None),
             (5, 5, "T09", "Test T09", 1, 99.0, 1, "ok", 99.0)]
    return ev, orders, lines


@test("local replay: apply twice, views == the Python twins on synthetic data, yq_readonly reads them, reverse twice, apply again (SKIP without a cluster)")
def _():
    try:
        import psycopg
    except ImportError:
        print("  SKIP: psycopg not installed")
        return
    try:
        conn = psycopg.connect(LOCAL_DSN, connect_timeout=2)
    except Exception as e:  # noqa: BLE001
        print(f"  SKIP: local Postgres unreachable ({type(e).__name__})")
        return
    from app import shop_analytics as sa
    mig, rev = MIGRATION.read_text(encoding="utf-8"), REVERSE.read_text(encoding="utf-8")
    orig = _read("scripts/marketplace_migration.sql")
    old_terms = "create or replace view v_shop_search_terms as\n" + orig.split("create or replace view v_shop_search_terms as\n", 1)[1].split(";", 1)[0] + ";"
    old_rail = "create or replace view v_shop_rail_perf as\n" + orig.split("create or replace view v_shop_rail_perf as\n", 1)[1].split(";", 1)[0] + ";"
    ev, orders, lines = _replay_rows()
    try:
        with conn.cursor() as cur:
            cur.execute("select to_regclass('public.shop_events') is not null")
            if cur.fetchone()[0]:
                print("  SKIP: this database already has shop_events (the replay builds its own synthetic tables)")
                return
            cur.execute("select current_user")
            if cur.fetchone()[0] != "postgres":
                print("  SKIP: the replay needs to run as postgres (the migration checks the views' owner)")
                return
            cur.execute(BASE_SCHEMA)
            cur.execute("insert into salesmen values (1, 'Rep One', 'repone'), (2, 'Rep Two', 'reptwo')")
            cur.execute("insert into customers values (900, 'TEST FOCUS')")
            cur.execute("insert into catalog_items (item_code, category) values ('T01', 'CABLE'), ('T02', 'CHARGER')")
            cur.execute("insert into shop_customers values (1, '97300000101', 'Test Person', 'Test Shop', 'Test Area', null, 2, 900),"
                        " (2, '97300000102', 'Test Person 2', 'Shop Two', null, null, null, null),"
                        " (3, '97300000103', null, 'Never Ordered', null, null, null, null)")
            for x in ev:
                cur.execute("insert into shop_events (id, ts, event, session_id, referral_code, salesman_id, src, device_id, meta)"
                            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (x["id"], x["ts"], x["event"], x["session_id"], x["referral_code"], x["salesman_id"], x["src"],
                             x["device_id"], json.dumps(x["meta"]) if x["meta"] is not None else None))
            for o in orders:
                cur.execute("insert into shop_orders values (%s, %s, %s, %s, %s, %s, %s, %s, %s)", o)
            for ln in lines:
                cur.execute("insert into shop_order_lines values (%s, %s, %s, %s, %s, %s, %s, %s, %s)", ln)
            cur.execute(old_terms + "\n" + old_rail + "\ngrant select on v_shop_search_terms, v_shop_rail_perf to yq_readonly;")
            cur.execute(mig)
            cur.execute(mig)                                            # idempotent
            # the funnel view against the Python twin
            cur.execute("select " + ", ".join(f"coalesce(sum({k}),0)::bigint" for k in sa.FUNNEL_KEYS) + " from v_shop_funnel_daily")
            got = dict(zip(sa.FUNNEL_KEYS, cur.fetchone()))
            twin = sa.funnel_from_events(ev, sa.first_seen_map(ev))["total"]
            assert got == twin, {k: (got[k], twin[k]) for k in sa.FUNNEL_KEYS if got[k] != twin[k]}
            assert got["view_sessions"] == 3 and got["checkout_sessions"] == 1 and got["order_sessions"] == 1
            cur.execute("select term, searches, zero_results from v_shop_search_terms order by term")
            assert cur.fetchall() == [("sandisk", 1, 1), ("uk12", 1, 0)]
            cur.execute("select rail, clicks, adds from v_shop_rail_perf order by rail")
            assert cur.fetchall() == [("best", 1, 0), ("grid", 0, 1)]
            cur.execute("select customer_id, orders, value_bhd::float, confirmed_value_bhd::float, cancelled_orders, order_days,"
                        " rep_name, rep_source, focus_customer_name from v_merchant_360 order by customer_id")
            rows = cur.fetchall()
            assert rows[0] == (1, 3, 24.0, 19.0, 0, 2, "Rep Two", "sticky", "TEST FOCUS"), rows[0]
            assert rows[1][:5] == (2, 0, 0.0, 0.0, 1) and rows[2][1] == 0, rows
            # the API's own SQL, run as yq_readonly exactly as the RPC would (params as text)
            cur.execute("set local role yq_readonly")
            sqlp = sa.FUNNEL_SQL.replace("$1", "%(a)s").replace("$2", "%(b)s").replace("$3", "%(c)s").replace("$4", "%(d)s")
            cur.execute(sqlp, {"a": "2026-09-01", "b": "2026-10-31", "c": "", "d": ""})
            assert any(r[0] == "all" for r in cur.fetchall())
            cur.execute("reset role")
            cur.execute("select has_table_privilege('anon', 'public.v_merchant_360', 'SELECT')")
            assert cur.fetchone()[0] is False
            cur.execute(rev)
            cur.execute(rev)                                            # idempotent
            cur.execute("select to_regclass('public.v_merchant_360'), to_regclass('public.v_shop_funnel_daily')")
            assert cur.fetchone() == (None, None)
            cur.execute("select count(*) from v_shop_search_terms")
            assert cur.fetchone()[0] == 4, "the old rule again: every ping with a q — uk, uk12, the chip 'instock', sandisk"
            cur.execute("select count(*) from shop_events")
            assert cur.fetchone()[0] == len(ev), "no row touched"
            cur.execute(mig)                                            # forward again after a reverse
        print("  replay: migration x2, twins agree, yq_readonly reads, reverse x2, migration again — one rolled-back transaction")
    finally:
        conn.rollback()
        conn.close()


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
