"""R7b Sprint 5 — "rep speed" (plan §10, §11, §23 items 2-3): the merged shop book, the usual
basket behind "Order again" and "Send restock link", Today in one call, and the idempotent staff
checkout.

    python -m tests.test_r7b_rep

Same lightweight runner as tests/test_r7a_focus.py. Every test runs against pure functions or an
in-memory stand-in for the PostgREST client (eq / in / is / ilike / lt… filters, inserts that hand
out ids, updates that report what they touched); nothing reaches a database or the network, and
the suite needs no .env (CI pins SUPABASE_URL=https://ci.invalid). All data is synthetic: made-up
shops, reps, phones and amounts.

Covered:
  * name_key — case, punctuation and legal suffixes fold; different shops stay apart;
  * merge_book — an app customer folds into the Focus shop with the same phone or shop name; app-
    only shops keep a row; holdout shops are listed but never due (the flag only in the admin
    view); due first, then the latest purchase; the over-90 credit chip; the later last order;
  * load_credit — one bound JSON parameter, over-90 only, 3-dp strings, {} on a failed read;
  * the basket — usual lines due first at the median made orderable (MOQ, packs), catalog lines
    only; the suggestion (last app order when newer, else the due regulars); sold-out lines apart
    and never in the restock link; the link's format;
  * money_strip — "BHD X more → +Y kickback" in Decimal; top tier; stale data;
  * the routes — /shop/me/book, /shop/me/basket, /shop/me/book/phone, /shop/me/today: scoping,
    404s, fill-blank phone writes with an audit row that carries no digits, the Today aggregate
    (oldest first, SLA flag, heavy half cached, queue live, failures named), gates;
  * the staff checkout — client_order_id + staff:<email> make a re-submit return the same order
    (one row, one alert) and the staff device never joins a merchant's device list.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")
os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")   # app.main builds a JWKS URL from it; nothing is fetched

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class _Patched:
    def __init__(self, *triples):
        self.triples, self.saved = triples, []

    def __enter__(self):
        for mod, name, value in self.triples:
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
        return self

    def __exit__(self, *exc):
        for mod, name, old in reversed(self.saved):
            setattr(mod, name, old)
        return False


# ── a table-aware fake PostgREST client (tests/test_r1_orders.py, trimmed) ─────

def _same(a, b) -> bool:
    return a == b or str(a) == str(b)


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.negate = "select", None, [], False
        self.cols, self.want_count, self.lim, self.head = "*", None, None, False

    def select(self, cols="*", count=None, head=False):
        self.op, self.cols, self.want_count, self.head = "select", cols, count, head
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    @property
    def not_(self):
        self.negate = True
        return self

    def _filter(self, kind, col, val):
        self.filters.append((kind, col, val, self.negate))
        self.negate = False
        return self

    def eq(self, col, val):
        return self._filter("eq", col, val)

    def is_(self, col, val):
        return self._filter("is", col, val)

    def in_(self, col, vals):
        return self._filter("in", col, list(vals))

    def ilike(self, col, val):
        return self._filter("ilike", col, val)

    def __getattr__(self, name):          # gte, lte, gt, lt, order, …
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*a, **_kw):
            if name in ("gte", "gt", "lte", "lt"):
                return self._filter(name, a[0], a[1])
            return self
        return call

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.lim = b - a + 1
        return self

    def _match(self, row: dict) -> bool:
        for kind, col, val, neg in self.filters:
            got = row.get(col)
            if kind == "eq":
                ok = _same(got, val)
            elif kind == "is":
                ok = (got is None) if str(val).lower() == "null" else (bool(got) == (str(val).lower() == "true"))
            elif kind == "in":
                ok = any(_same(got, v) for v in val)
            elif kind == "ilike":
                ok = str(got or "").lower() == str(val).lower().strip("%")
            elif kind in ("lt", "lte", "gt", "gte"):
                a, b = str(got), str(val)
                ok = got is not None and {"lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[kind]
            else:
                ok = True
            if neg:
                ok = not ok
            if not ok:
                return False
        return True

    def execute(self):
        db = self.db
        db.calls.append((self.op, self.table, self.cols if self.op == "select" else self.payload, list(self.filters)))
        fail = db.fail.get((self.op, self.table))
        if fail is not None:
            raise fail
        rows = db.tables.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in rows if self._match(r)]
            data = [] if self.head else (out[: self.lim] if self.lim else out)
            return SimpleNamespace(data=data, count=(len(out) if self.want_count else None))
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for p in items:
                r = dict(p)
                r.setdefault("id", db.next_id(self.table))
                rows.append(r)
                out.append(dict(r))
            return SimpleNamespace(data=out, count=None)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload)
                    out.append(dict(r))
            return SimpleNamespace(data=out, count=None)
        if self.op == "delete":
            gone = [r for r in rows if self._match(r)]
            rows[:] = [r for r in rows if not self._match(r)]
            return SimpleNamespace(data=gone, count=None)
        return SimpleNamespace(data=[], count=None)


class _FakeDB:
    def __init__(self, tables: dict | None = None):
        self.tables: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.calls: list[tuple] = []
        self.fail: dict[tuple, Exception] = {}
        self.rpc_results = {"shop_next_order_no": "YQ-2709-0101"}
        self._ids: dict[str, int] = {}

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        self.calls.append(("rpc", name, params, []))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.rpc_results.get(name), count=None))

    def next_id(self, table) -> int:
        cur = max([int(r.get("id") or 0) for r in self.tables.get(table, [])] + [self._ids.get(table, 0)])
        self._ids[table] = cur + 1
        return cur + 1

    def writes(self, table: str | None = None) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("insert", "update", "delete") and (table is None or c[1] == table)]

    def reads(self, table: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == "select" and c[1] == table]

    def rows(self, table: str) -> list[dict]:
        return self.tables.get(table, [])


class _patched:
    """Point app.shop / app.database / app.catalog at the fake, serve `ctx` as the cached catalog
    context, and reset every cache the paths under test consult (settings, salesman, column
    probes, the follow-up / book / Today cache)."""

    def __init__(self, fake: _FakeDB, ctx: dict | None = None):
        self.fake, self.ctx = fake, ctx

    def __enter__(self):
        import app.catalog as cat
        import app.customer_contacts as cc
        import app.database as db
        import app.followups as fu
        import app.shop as s
        self.mods = (s, db, cat, fu, cc)
        self.saved = (s.get_client, db.get_client, cat.get_client, cc.get_client, s._ctx_cache["ctx"], s._ctx_cache["at"])
        s.get_client = db.get_client = cat.get_client = cc.get_client = lambda: self.fake
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        fu.invalidate()
        db.invalidate_user_cache()
        if self.ctx is not None:
            s._ctx_cache.update(ctx=self.ctx, at=time.time() + 10 ** 6)
        return self.fake

    def __exit__(self, *_exc):
        s, db, cat, fu, cc = self.mods
        s.get_client, db.get_client, cat.get_client, cc.get_client = self.saved[:4]
        s._ctx_cache.update(ctx=self.saved[4], at=self.saved[5])
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        fu.invalidate()
        db.invalidate_user_cache()
        return False


# ── synthetic fixtures (no real shop, rep, phone or amount) ─────────────────────

DT = "2026-09-24"
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)          # a Sunday: business minutes count
REP = "rep@example.com"
SALESMEN = [{"id": 1, "name": "Rep One", "referral_code": "rep-one", "is_active": True, "focus_name": "Rep One",
             "user_email": REP, "whatsapp": "97300000001"},
            {"id": 2, "name": "Rep Two", "referral_code": "rep-two", "is_active": True, "focus_name": "Rep Two",
             "user_email": "two@example.com"}]


def _shop(name, rep="Rep One - Acc WH", days_since=10, gap=20.0, net_180d="600", visits=6, last="2026-09-14"):
    return {"customer_name": name, "rep": rep, "rep_visits": visits, "visits": visits, "last_date": last,
            "first_date": "2025-10-01", "days_since": days_since, "median_gap": gap, "net_180d": net_180d,
            "invoices_180d": 4, "data_through": DT}


def _cust(name, phone, shop=None, orders=1, last="2026-09-20T08:00:00+00:00", area="Area A"):
    return {"name": name, "phone": phone, "shop": shop, "area": area, "email": None, "orders": orders,
            "last_order_at": last}


def _item(code, price, stock=100, moq=1, pack=None, cat="CABLE"):
    return {"item_code": code, "display_name": f"{code} name", "spec": f"{code} spec", "category": cat, "brand": "VFAN",
            "standard_rate": price, "b2c_rate": None, "product_image_url": None, "package_image_url": None,
            "sort_order": None, "created_at": "2026-07-03T00:00:00+00:00", "moq": moq, "pack_size": pack,
            "stock_qty": stock, "stock_as_of": "2026-09-24", "sold_30d": 0, "prev_30d": 0, "sold_90d": 0,
            "customers_30d": 0}


def _ctx(items, **settings):
    from app.shop import SETTING_DEFAULTS
    vals = dict(SETTING_DEFAULTS)
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: i for i in items},
            "order": [i["item_code"] for i in items], "by_upper": {i["item_code"].upper(): i["item_code"] for i in items},
            "costs": {}, "rules": [], "salesmen": [dict(s) for s in SALESMEN], "pairs": {}, "drops": {},
            "campaigns": [], "loaded_at": ""}


def _reg(shop, code, times=4, median_qty=5, due=True, days_since=25):
    return {"customer_name": shop, "item_code": code, "times_bought": times, "median_qty": median_qty,
            "cadence_days": 21.0, "last_bought": "2026-08-30", "days_since": days_since, "due": due}


def _user(role="salesman", email=REP):
    from app import auth
    return auth.CurrentUser(user_id="uid-" + email, email=email, role=role, status="active", must_reset=False)


def _features(*feats):
    from app import user_auth
    return (user_auth, "_user_row", lambda email: {"email": email, "features": list(feats)})


def _raises(fn, exc_type, contains: str | None = None):
    try:
        fn()
    except exc_type as e:
        if contains is not None:
            assert contains in str(e), f"expected {contains!r} in {str(e)!r}"
        return e
    raise AssertionError(f"expected {exc_type.__name__}")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the merged book (pure)
# ═══════════════════════════════════════════════════════════════════════════════

@test("name_key: case, punctuation and legal suffixes fold; different shops stay apart")
def _():
    from app.followups import name_key
    assert name_key("AL-NOOR MOBILE W.L.L.") == name_key("Al Noor Mobile") == "alnoormobile"
    assert name_key("Star Phones S.P.C") == name_key("star phones") == name_key("STAR  PHONES CO.")
    assert name_key("Blue & Green Mobiles") == name_key("blue and green mobiles")
    assert name_key("Star Phones") != name_key("Star Phone Centre")
    assert name_key("") == name_key(None) == ""


@test("merge_book: an app customer folds into the Focus shop by phone or by shop name; app-only shops keep a row")
def _():
    from app.followups import merge_book
    shops = [_shop("ALPHA MOBILE W.L.L"), _shop("BETA PHONES", days_since=3)]
    contacts = {"ALPHA MOBILE W.L.L": {"phone": "+973 3300 0001"}}
    market = [_cust("Ali", "97333000001", shop="Alpha shop typed differently", orders=2),   # same phone
              _cust("Badr", "97333000002", shop="Beta Phones"),                              # same name
              _cust("Carl", "97333000003", shop="Gamma Store", last="2026-09-25T10:00:00+00:00")]
    rows = {r["key"]: r for r in merge_book(shops, market, contacts)}
    assert set(rows) == {"f:ALPHA MOBILE W.L.L", "f:BETA PHONES", "m:97333000003"}, set(rows)
    a = rows["f:ALPHA MOBILE W.L.L"]
    assert a["sources"] == ["focus", "app"] and a["phone"] == "97333000001" and a["contact_name"] == "Ali"
    assert a["app_orders"] == 2 and a["last_order_date"] == "2026-09-20", "the later of Focus and app"
    b = rows["f:BETA PHONES"]
    assert b["phone"] == "97333000002" and b["area"] == "Area A", "a Focus shop without a phone learns it from the app"
    g = rows["m:97333000003"]
    assert g["sources"] == ["app"] and g["focus_name"] is None and g["name"] == "Gamma Store" and not g["due"]
    assert "holdout" not in a, "the rep view never carries the holdout flag"


@test("merge_book: holdout shops are in the book but never due; only the admin view carries the flag")
def _():
    from app.followups import is_holdout, merge_book
    names = [f"SHOP {n:03d}" for n in range(60)]
    held = [n for n in names if is_holdout("Rep One", n)]
    shown = [n for n in names if not is_holdout("Rep One", n)]
    assert held and shown
    shops = [_shop(n, days_since=40, gap=20.0) for n in names]            # all overdue by their rhythm
    rows = merge_book(shops, [])
    assert len(rows) == len(names), "every shop is in the book, held back or not"
    by = {r["name"]: r for r in rows}
    assert all(not by[n]["due"] and by[n]["status"] == "ok" for n in held), "a holdout shop is never nudged"
    assert all(by[n]["due"] for n in shown)
    assert all("Usually every" not in (by[n]["why"] or "") for n in held), "no cadence hint on a held shop"
    admin = {r["name"]: r for r in merge_book(shops, [], include_holdout=True)}
    assert all(admin[n]["holdout"] and not admin[n]["due"] for n in held)
    assert not any(admin[n]["holdout"] for n in shown)


@test("merge_book: due first (overdue × value), then the latest purchase; the credit chip only where over-90 exists")
def _():
    from app.followups import merge_book, is_holdout
    names = [n for n in ("K1 SHOP", "K2 SHOP", "K3 SHOP", "K4 SHOP", "K5 SHOP", "K6 SHOP", "K7 SHOP")
             if not is_holdout("Rep One", n)][:4]
    assert len(names) == 4
    shops = [_shop(names[0], days_since=30, gap=20.0, net_180d="600"),      # ratio 1.5 × 100/month
             _shop(names[1], days_since=40, gap=20.0, net_180d="1200"),     # ratio 2.0 × 200/month → first
             _shop(names[2], days_since=2, gap=20.0, last="2026-09-22"),    # fresh, recent
             _shop(names[3], days_since=5, gap=20.0, last="2026-09-19")]    # fresh, older
    credit = {names[0]: {"over_90_bhd": "45.500", "outstanding_bhd": "120.000", "as_of": DT}}
    rows = merge_book(shops, [_cust("Z", "97333000009", shop="Zulu", last="2026-09-23T08:00:00+00:00")], credit=credit)
    order = [r["name"] for r in rows]
    assert order[:2] == [names[1], names[0]], order
    assert order[2:] == ["Zulu", names[2], names[3]], "then the latest purchase first"
    assert [r["rank"] for r in rows[:2]] == [1, 2] and rows[2]["rank"] is None
    assert rows[1]["credit"]["over_90_bhd"] == "45.500" and rows[0]["credit"] is None
    assert rows[1]["monthly_value_bhd"] == "100.000"


@test("load_credit: one bound JSON parameter, over-90 only, 3-dp strings; a failed read is {}")
def _():
    from app import followups
    seen = []

    def fake(sql, params):
        seen.append((sql, params))
        return [{"account": "A", "outstanding_bhd": 10, "over_90_bhd": "4.25", "as_of": DT},
                {"account": "B", "outstanding_bhd": 5, "over_90_bhd": 0, "as_of": DT}]
    with _Patched((followups, "exec_sql_params", fake)):
        out = followups.load_credit(["A", "B", "C'; DROP TABLE x; --"])
    assert out == {"A": {"over_90_bhd": "4.250", "outstanding_bhd": "10.000", "as_of": DT}}
    sql, params = seen[0]
    assert "v_receivables" in sql and "$1::jsonb" in sql and "DROP" not in sql, "names are bound, never interpolated"
    assert json.loads(params[0])[2].startswith("C'"), params

    def boom(sql, params):
        raise RuntimeError("permission denied")
    with _Patched((followups, "exec_sql_params", boom)):
        assert followups.load_credit(["A"]) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. the usual basket (pure)
# ═══════════════════════════════════════════════════════════════════════════════

ITEMS = {"C18": _item("C18", 1.5), "UK15": _item("UK15", 2.25, moq=6), "P04": _item("P04", 3.0, pack=4),
         "X05": _item("X05 UC", 1.0, stock=0), "T02": _item("T02", 0.75)}
ITEMS = {k: {**v, "item_code": k} for k, v in ITEMS.items()}


@test("basket: usual lines due first at the median made orderable (MOQ, packs); catalog lines only")
def _():
    from app.followups import basket_payload
    row = {"key": "f:ALPHA", "name": "ALPHA", "focus_name": "ALPHA", "last_focus_date": "2026-09-10"}
    regs = [_reg("ALPHA", "C18", times=9, median_qty=10, due=False),
            _reg("ALPHA", "UK15", times=3, median_qty=2, due=True),       # MOQ 6 → 6
            _reg("ALPHA", "P04", times=5, median_qty=5, due=True),        # packs of 4 → 8
            _reg("ALPHA", "SIM-BATELCO", times=20, median_qty=50, due=True)]   # not sold on the app
    out = basket_payload(row, regs, None, ITEMS, "https://market.example/rep-one")
    assert [ln["item_code"] for ln in out["usual"]] == ["P04", "UK15", "C18"], out["usual"]
    assert {ln["item_code"]: ln["qty"] for ln in out["usual"]} == {"P04": 8, "UK15": 6, "C18": 10}
    assert out["suggested_from"] == "due_regulars" and [ln["item_code"] for ln in out["suggested"]] == ["P04", "UK15"]
    assert out["value_bhd"] == "37.500", out["value_bhd"]                  # 8×3.000 + 6×2.250
    assert out["restock_link"] == "https://market.example/rep-one?order=P04:8,UK15:6"


@test("basket: the last app order wins when newer (removed lines skipped, confirmed qty used); sold-out lines listed apart, never in the link")
def _():
    from app.followups import basket_payload
    row = {"key": "f:ALPHA", "name": "ALPHA", "focus_name": "ALPHA", "last_focus_date": "2026-09-10"}
    last = {"order_no": "YQ-1", "created_at": "2026-09-20T09:00:00+00:00", "status": "delivered",
            "lines": [{"item_code": "C18", "qty": 12, "qty_confirmed": 10, "line_status": "changed"},
                      {"item_code": "T02", "qty": 5, "qty_confirmed": None, "line_status": "removed"},
                      {"item_code": "X05", "qty": 3, "qty_confirmed": None, "line_status": "ok"},
                      {"item_code": "GONE1", "qty": 3, "qty_confirmed": None, "line_status": "ok"}]}
    out = basket_payload(row, [_reg("ALPHA", "UK15")], last, ITEMS, "https://old.example/c/tok?ref=rep-one")
    assert out["suggested_from"] == "last_order"
    assert [ln["item_code"] for ln in out["suggested"]] == ["C18", "X05"], "removed and off-catalog lines dropped"
    assert out["suggested"][0]["qty"] == 10, "what was confirmed, not what was asked"
    assert [ln["item_code"] for ln in out["sold_out"]] == ["X05"] and [ln["item_code"] for ln in out["available"]] == ["C18"]
    assert out["restock_link"] == "https://old.example/c/tok?ref=rep-one&order=C18:10", "& after an existing query"
    # an older app order loses to newer Focus regulars
    old = dict(last, created_at="2026-09-01T09:00:00+00:00")
    assert basket_payload(row, [_reg("ALPHA", "UK15")], old, ITEMS, None)["suggested_from"] == "due_regulars"
    # nothing orderable → no link at all
    assert basket_payload(row, [], {**last, "lines": last["lines"][2:3]}, ITEMS, "https://m.example/r")["restock_link"] is None


@test("restock_link: codes are URL-encoded (a space, a slash) the way the checkout's ready order does it")
def _():
    from app.followups import restock_link
    link = restock_link("https://m.example/rep-one", [{"item_code": "X05 UC-1Mtr", "qty": 6}, {"item_code": "A/B", "qty": 2}])
    assert link == "https://m.example/rep-one?order=X05%20UC-1Mtr:6,A%2FB:2", link
    assert restock_link(None, [{"item_code": "C18", "qty": 1}]) is None and restock_link("https://m", []) is None


@test("money_strip: BHD X more → +Y kickback in Decimal; the top tier has no next; stale after a day")
def _():
    from app.followups import money_strip
    t = {"month": "2026-09", "mtd_bhd": 1925.5, "tier_reached": 1, "kickback_pct": 0.01, "kickback_bhd": 19.255,
         "next_tier": {"n": 2, "bhd": 2000.0, "gap_bhd": 74.5, "pct": 0.015}, "data_through": DT, "data_age_days": 3,
         "days_left": 3, "basis": "net_ex_vat", "returns_deducted": False}
    m = money_strip(t)
    assert m["next_gap_bhd"] == "74.500" and m["next_gain_bhd"] == "10.745", m   # 2000 × 1.5 % − 19.255
    assert m["stale"] and m["tier"] == 1 and m["mtd_bhd"] == "1925.500"
    top = money_strip({**t, "next_tier": None, "data_age_days": 0})
    assert top["next_gain_bhd"] is None and top["next_gap_bhd"] is None and not top["stale"]
    assert money_strip(None) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the routes
# ═══════════════════════════════════════════════════════════════════════════════

def _book_db(**tables):
    base = {"salesmen": SALESMEN, "app_settings": [{"key": "shop_market_url", "value": "https://market.example"}],
            "customer_contacts": [{"customer_name": "ALPHA MOBILE", "phone": "+973 3300 0001", "source": "tavily"}],
            "shop_orders": [
                {"id": 1, "order_no": "YQ-1", "status": "delivered", "salesman_id": 1, "customer_name": "Ali",
                 "customer_phone": "97333000001", "customer_shop": "Alpha", "customer_area": "A",
                 "customer_email": None, "created_at": "2026-09-20T08:00:00+00:00", "is_test": False},
                {"id": 2, "order_no": "YQ-2", "status": "new", "salesman_id": 2, "customer_name": "Other",
                 "customer_phone": "97333000077", "customer_shop": "Theirs", "customer_area": "B",
                 "customer_email": None, "created_at": "2026-09-21T08:00:00+00:00", "is_test": False}],
            "shop_order_lines": [{"id": 11, "order_id": 1, "item_code": "C18", "qty": 12, "qty_confirmed": None,
                                  "line_status": "ok"}],
            "v_customer_regulars": [_reg("ALPHA MOBILE", "UK15"), _reg("ALPHA MOBILE", "C18", due=False)]}
    base.update(tables)
    return _FakeDB(base)


def _fake_shops(seen):
    def load(name, run=None, *, all_reps=False):
        seen.append(name)
        return {"Rep One": [_shop("ALPHA MOBILE", days_since=30), _shop("NOPHONE SHOP", days_since=3)],
                "Rep Two": [_shop("THEIR SHOP", rep="Rep Two")]}.get(name, [])
    return load


@test("book route: a rep gets his own Focus shops and his own app customers; unlinked and management get the hint")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth, followups
    seen: list = []
    fake = _book_db()
    with _patched(fake, _ctx(list(ITEMS.values()))), \
            _Patched((followups, "load_shops", _fake_shops(seen)), (followups, "load_credit", lambda names: {}),
                     _features("Catalog", "Shop Orders")):
        c = TestClient(m.app)
        m.app.dependency_overrides[auth.get_current_user] = lambda: _user()
        try:
            r = c.get("/shop/me/book")
            assert r.status_code == 200, r.text[:200]
            body = r.json()
            keys = [s["key"] for s in body["shops"]]
            assert seen == ["Rep One"], seen
            assert "f:ALPHA MOBILE" in keys and "f:NOPHONE SHOP" in keys and "f:THEIR SHOP" not in keys
            assert "m:97333000077" not in keys, "another rep's app customer never leaks into the book"
            alpha = next(s for s in body["shops"] if s["key"] == "f:ALPHA MOBILE")
            assert alpha["phone"] == "97333000001" and "app" in alpha["sources"], alpha
            # cached per rep: a second call reads nothing
            n = len(fake.calls)
            c.get("/shop/me/book")
            assert len(fake.calls) == n and seen == ["Rep One"], "the book is cached per rep"
            for who, role in (("nobody@example.com", "salesman"), ("boss@example.com", "management")):
                m.app.dependency_overrides[auth.get_current_user] = lambda who=who, role=role: _user(role, who)
                n = len(fake.calls)
                r = c.get("/shop/me/book").json()
                assert r["shops"] == [] and "not linked" in r["hint"], r
                assert not [x for x in fake.calls[n:] if x[1] in ("shop_orders", "customer_contacts")], "nothing read"
            # an admin without a salesman row: every app customer, no Focus shops
            m.app.dependency_overrides[auth.get_current_user] = lambda: _user("admin", "owner@example.com")
            body = c.get("/shop/me/book").json()
            assert {s["key"] for s in body["shops"]} == {"m:97333000001", "m:97333000077"}, body
        finally:
            m.app.dependency_overrides.clear()


@test("basket route: a shop outside the caller's book is 404; his own shop answers with the suggestion and the restock link")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth, followups
    fake = _book_db()
    with _patched(fake, _ctx(list(ITEMS.values()))), \
            _Patched((followups, "load_shops", _fake_shops([])), (followups, "load_credit", lambda names: {}),
                     _features("Catalog")):
        c = TestClient(m.app)
        m.app.dependency_overrides[auth.get_current_user] = lambda: _user()
        try:
            assert c.get("/shop/me/basket", params={"shop": "f:THEIR SHOP"}).status_code == 404
            assert c.get("/shop/me/basket", params={"shop": "m:97333000077"}).status_code == 404
            r = c.get("/shop/me/basket", params={"shop": "f:ALPHA MOBILE"})
            assert r.status_code == 200, r.text[:200]
            b = r.json()
            assert b["shop"]["key"] == "f:ALPHA MOBILE" and b["last_order"]["order_no"] == "YQ-1"
            assert b["suggested_from"] == "last_order" and [ln["item_code"] for ln in b["suggested"]] == ["C18"]
            assert b["restock_link"] == "https://market.example/rep-one?order=C18:12", b["restock_link"]
            # the old app-only key still finds the merged row (by its phone)
            assert c.get("/shop/me/basket", params={"shop": "m:97333000001"}).json()["shop"]["key"] == "f:ALPHA MOBILE"
        finally:
            m.app.dependency_overrides.clear()


@test("phone route: fills a blank phone for his own Focus shop, never overwrites, 404 outside his book, 400 on a bad number; audited without digits")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth, followups
    fake = _book_db()
    with _patched(fake, _ctx(list(ITEMS.values()))), \
            _Patched((followups, "load_shops", _fake_shops([])), (followups, "load_credit", lambda names: {}),
                     _features("Catalog", "Shop Orders")):
        c = TestClient(m.app)
        m.app.dependency_overrides[auth.get_current_user] = lambda: _user()
        try:
            before = c.get("/shop/me/book").json()
            assert next(s for s in before["shops"] if s["key"] == "f:NOPHONE SHOP")["phone"] is None
            r = c.post("/shop/me/book/phone", json={"shop": "NOPHONE SHOP", "phone": "3300 0005"})
            assert r.status_code == 200 and r.json()["saved"] is True, r.text[:200]
            row = next(x for x in fake.rows("customer_contacts") if x["customer_name"] == "NOPHONE SHOP")
            assert row["phone"] == "+973 3300 0005" and row["updated_by"] == REP and row["source"] == "rep"
            after = c.get("/shop/me/book").json()
            assert next(s for s in after["shops"] if s["key"] == "f:NOPHONE SHOP")["phone"] == "97333000005", \
                "the saved number is in the book at once (its cache was dropped)"
            n = len(fake.writes("customer_contacts"))
            r = c.post("/shop/me/book/phone", json={"shop": "ALPHA MOBILE", "phone": "33009999"})
            assert r.status_code == 200 and r.json()["saved"] is False and len(fake.writes("customer_contacts")) == n
            assert next(x for x in fake.rows("customer_contacts") if x["customer_name"] == "ALPHA MOBILE")["phone"] == "+973 3300 0001"
            assert c.post("/shop/me/book/phone", json={"shop": "THEIR SHOP", "phone": "33001111"}).status_code == 404
            assert c.post("/shop/me/book/phone", json={"shop": "NOPHONE SHOP", "phone": "12"}).status_code == 400
            audits = [x for x in fake.rows("audit_log") if x["event"] == "shop.book_phone"]
            assert audits and all(not any(ch.isdigit() for ch in json.dumps(a["detail"]).replace('"salesman_id": 1', ""))
                                  for a in audits), audits
        finally:
            m.app.dependency_overrides.clear()
    from app.auth import read_only_refuses
    assert read_only_refuses("POST", "/shop/me/book/phone"), "management can never write a phone"


@test("today route: one call carries every card; To confirm oldest first with age and the SLA flag; heavy half cached, queue live; a failing card is named, not fatal")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth, followups, shop, statements
    orders = [
        {"id": 5, "order_no": "YQ-5", "status": "new", "salesman_id": 1, "customer_name": "N", "customer_shop": "New Shop",
         "customer_area": "A", "total_bhd": 21.5, "items_count": 3, "units_count": 18, "order_kind": "standard",
         "created_at": (NOW - timedelta(minutes=30)).isoformat(), "assigned_at": None, "is_test": False},
        {"id": 3, "order_no": "YQ-3", "status": "new", "salesman_id": 1, "customer_name": "O", "customer_shop": "Old Shop",
         "customer_area": "A", "total_bhd": 40, "items_count": 5, "units_count": 30, "order_kind": "standard",
         "created_at": (NOW - timedelta(hours=5)).isoformat(), "assigned_at": None, "is_test": False},
        {"id": 4, "order_no": "YQ-4", "status": "confirmed", "salesman_id": 1, "customer_shop": "Busy",
         "created_at": (NOW - timedelta(hours=9)).isoformat(), "is_test": False},
        {"id": 6, "order_no": "YQ-6", "status": "new", "salesman_id": 2, "customer_shop": "Not mine",
         "created_at": (NOW - timedelta(hours=30)).isoformat(), "is_test": False}]
    fake = _FakeDB({"salesmen": SALESMEN, "app_settings": [{"key": "shop_confirm_sla_min", "value": "120"}],
                    "shop_orders": orders})
    calls = {"me": 0}

    def me_payload(email, *, is_admin=False):
        calls["me"] += 1
        return {"salesman": SALESMEN[0], "link": "https://market.example/rep-one", "kpis": {"orders_7d": 2},
                "focus": {"target": {"month": "2026-09", "mtd_bhd": 900.0, "tier_reached": 0, "kickback_pct": 0.0,
                                     "kickback_bhd": 0.0, "next_tier": {"n": 1, "bhd": 1000.0, "gap_bhd": 100.0, "pct": 0.01},
                                     "data_through": DT, "data_age_days": 3}}}

    def fu(name, **kw):
        return {"due": [{"shop": "ALPHA MOBILE", "top_skus": [{"display_name": "C18 name", "median_qty": 6}],
                         "phone": None, "rank": 1}], "counts": {"due": 7}, "data_through": DT}

    def broken(sm):
        raise RuntimeError("shop_events unavailable")
    real_now = followups.datetime
    with _patched(fake), _Patched(
            (shop, "me_payload", me_payload), (statements, "rep_summary", lambda sm: {"last_closed": None, "draft": None}),
            (followups, "followups", fu), (followups, "baskets_not_sent", broken),
            (followups, "link_week", lambda sm, days=7: {"days": 7, "orders": 1}),
            (shop, "list_restock", lambda ref: [{"item_code": "C18", "ref": ref}]),
            _features("Shop Orders")):
        class _Clock(real_now):
            @classmethod
            def now(cls, tz=None):
                return NOW
        followups.datetime = _Clock
        c = TestClient(m.app)
        m.app.dependency_overrides[auth.get_current_user] = lambda: _user()
        try:
            r = c.get("/shop/me/today")
            assert r.status_code == 200, r.text[:300]
            t = r.json()
            assert [w["order_no"] for w in t["waiting"]] == ["YQ-3", "YQ-5"], "oldest first, the rep's own only"
            assert t["waiting"][0]["age_min"] == 300 and t["waiting"][0]["overdue"] is True
            assert t["waiting"][1]["age_min"] == 30 and t["waiting"][1]["overdue"] is False
            assert t["waiting_count"] == 2 and t["in_progress"] == 1 and t["sla_min"] == 120
            assert t["money"]["next_gap_bhd"] == "100.000" and t["money"]["next_gain_bhd"] == "10.000"
            assert t["due"][0]["key"] == "f:ALPHA MOBILE" and t["due"][0]["wa_text"].startswith("Hello, Rep from YQ")
            assert t["due_count"] == 7 and t["due_counts"] == {"due": 7}, "the lapsed count rides along for 'N to win back'"
            assert t["link_week"]["orders"] == 1 and t["restock"][0]["ref"] == "rep-one"
            assert t["baskets"] is None and "baskets" in t["errors"], "a failing card is named, never fatal"
            # the heavy half is cached (15 s after a failed card, 60 s otherwise); the queue is live
            fake.tables["shop_orders"][0]["status"] = "confirmed"
            t2 = c.get("/shop/me/today").json()
            assert calls["me"] == 1, "me_payload ran once"
            assert [w["order_no"] for w in t2["waiting"]] == ["YQ-3"] and t2["in_progress"] == 2
        finally:
            followups.datetime = real_now
            m.app.dependency_overrides.clear()


@test("today route: an unlinked login gets the hint and no reads; management is not a rep")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth
    fake = _FakeDB({"salesmen": SALESMEN})
    with _patched(fake), _Patched(_features("Shop Orders")):
        c = TestClient(m.app)
        try:
            for who, role in (("nobody@example.com", "salesman"), ("boss@example.com", "management")):
                m.app.dependency_overrides[auth.get_current_user] = lambda who=who, role=role: _user(role, who)
                t = c.get("/shop/me/today").json()
                assert t["waiting"] == [] and "not linked" in t["hint"], t
            assert not fake.reads("shop_orders"), "no order was read for a login that is not a rep"
        finally:
            m.app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. the staff checkout
# ═══════════════════════════════════════════════════════════════════════════════

@test("staff order: client_order_id + staff:<email> make a re-submit return the same order — one row, one alert; the staff device never joins a merchant's device list")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth, shop_notify
    fake = _FakeDB({"salesmen": SALESMEN, "app_settings": [], "shop_customers": [], "shop_customer_phones": [],
                    "shop_orders": [], "shop_order_lines": [], "shop_order_events": [], "shop_events": []})
    alerts: list = []
    body = {"lines": [{"item_code": "C18", "qty": 4}, {"item_code": "T02", "qty": 2}],
            "customer": {"name": "Shop Contact", "phone": "33000010", "shop": "Delta Mobile", "area": "A"},
            "client_order_id": "2f0c2f7e-0000-4000-8000-000000000001"}
    with _patched(fake, _ctx(list(ITEMS.values()))), \
            _Patched((shop_notify, "notify_new_order", lambda oid: alerts.append(oid)), _features("Catalog")):
        c = TestClient(m.app)
        m.app.dependency_overrides[auth.get_current_user] = lambda: _user()
        try:
            r1 = c.post("/shop/order", json=body)
            assert r1.status_code == 200, r1.text[:300]
            r2 = c.post("/shop/order", json=body)
            assert r2.status_code == 200, r2.text[:300]
            a, b = r1.json(), r2.json()
            assert not a["duplicate"] and b["duplicate"] and a["order_no"] == b["order_no"] and a["order_id"] == b["order_id"]
            assert len(fake.rows("shop_orders")) == 1 and alerts == [a["order_id"]], "one row, one alert"
            row = fake.rows("shop_orders")[0]
            assert row["device_id"] == "staff:rep@example.com" and row["client_order_id"] == body["client_order_id"]
            assert row["source"] == "salesman" and row["placed_by"] == REP
            cust = fake.rows("shop_customers")[0]
            assert cust["device_ids"] == [], "a rep's login is never a merchant's device (recognize_phone)"
            assert all(e.get("device_id") is None for e in fake.rows("shop_events")), "no staff device in the funnel"
            # a new checkout (a new id) is a new order; an older client with no id still works
            r3 = c.post("/shop/order", json={**body, "client_order_id": "2f0c2f7e-0000-4000-8000-000000000002"})
            assert r3.status_code == 200 and not r3.json()["duplicate"] and len(fake.rows("shop_orders")) == 2
            r4 = c.post("/shop/order", json={k: v for k, v in body.items() if k != "client_order_id"})
            assert r4.status_code == 200 and fake.rows("shop_orders")[-1]["device_id"] is None
            assert c.post("/shop/order", json={**body, "client_order_id": "x" * 65}).status_code == 422
        finally:
            m.app.dependency_overrides.clear()


@test("cache: an order or a saved phone drops that rep's book and Today only")
def _():
    from app import followups
    followups.invalidate()
    followups._cache_put(("book", "1", False), 60, {"x": 1})
    followups._cache_put(("today", "1"), 60, {"x": 1})
    followups._cache_put(("book", "2", False), 60, {"x": 2})
    followups._cache_put(("link", "1", 7), 60, {"x": 3})
    followups._cache_put(("Rep One", False), 60, {"due": []})
    followups.forget_rep({"id": 1})
    assert followups._cache_get(("book", "1", False)) is None and followups._cache_get(("today", "1")) is None
    assert followups._cache_get(("book", "2", False)) == {"x": 2} and followups._cache_get(("link", "1", 7)) == {"x": 3}
    followups.invalidate_followups()
    assert followups._cache_get(("Rep One", False)) is None, "the ranked lists (they carry phones) are dropped"
    assert followups._cache_get(("book", "2", False)) == {"x": 2}, "…and nothing else"
    followups.invalidate()


@test("gates: the book, basket and phone routes need Catalog or Shop Orders; Today needs Shop Orders; nothing new is ungated")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    table = {}
    for r in m.app.routes:
        if isinstance(r, APIRoute):
            for meth in r.methods:
                table[(meth, r.path)] = [getattr(d.call, "feature", None) or getattr(d.call, "features", None)
                                         for d in r.dependant.dependencies]
    assert table[("GET", "/shop/me/book")] == [("Catalog", "Shop Orders")]
    assert table[("GET", "/shop/me/basket")] == [("Catalog", "Shop Orders")]
    assert table[("POST", "/shop/me/book/phone")] == [("Catalog", "Shop Orders")]
    assert table[("GET", "/shop/me/today")] == ["Shop Orders"]
    assert table[("POST", "/shop/order")] == ["Catalog"], "the staff checkout keeps its gate"


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
