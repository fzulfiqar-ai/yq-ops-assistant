"""R1 order write safety — atomic create, compare-and-swap status moves, salesman delete guard,
the is_test flag (plan §9 Step 1 "Order atomicity"; audit TXN-05/D-12, TXN-06/C-09).

    python -m tests.test_r1_orders

Same lightweight runner as tests/test_v3.py. EVERY test runs against _FakeDB, a table-aware
stand-in for the PostgREST client with real eq/in/is filters, inserts that hand out ids,
updates/deletes that report the rows they touched and the shop_orders cascade. Nothing here
ever reaches a database: app.shop, app.database and app.catalog are all pointed at the fake
for the duration of each test (see _patched).
"""
from __future__ import annotations

import io
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

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── a table-aware fake PostgREST client ───────────────────────────────────────

def _same(a, b) -> bool:
    return a == b or str(a) == str(b)


class _Query:
    """One builder chain. eq / neq / in_ / is_ / ilike (and .not_) are real filters; order,
    range, gte and friends are recorded and ignored (the fixtures are tiny and pre-sorted)."""

    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.negate = "select", None, [], False
        self.cols, self.want_count, self.lim = "*", None, None

    def select(self, cols="*", count=None):
        self.op, self.cols, self.want_count = "select", cols, count
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, **_kw):
        self.op, self.payload = "upsert", payload
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

    def neq(self, col, val):
        return self._filter("neq", col, val)

    def is_(self, col, val):
        return self._filter("is", col, val)

    def in_(self, col, vals):
        return self._filter("in", col, list(vals))

    def ilike(self, col, val):
        return self._filter("ilike", col, val)

    def __getattr__(self, name):          # gte, lte, gt, lt, like, or_, order, …
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*a, **_kw):
            if name in ("gte", "gt", "lte", "lt", "like", "or_"):
                return self._filter("noop", a[0] if a else None, a[1] if len(a) > 1 else None)
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
            elif kind == "neq":
                ok = not _same(got, val)
            elif kind == "is":
                ok = (got is None) if str(val).lower() == "null" else (bool(got) == (str(val).lower() == "true"))
            elif kind == "in":
                ok = any(_same(got, v) for v in val)
            elif kind == "ilike":
                ok = str(got or "").lower() == str(val).lower().strip("%")
            else:
                ok = True
            if neg:
                ok = not ok
            if not ok:
                return False
        return True

    def execute(self):
        db = self.db
        # (op, table, payload-or-select-columns, filters) — what the assertions read back
        db.calls.append((self.op, self.table, self.cols if self.op == "select" else self.payload, list(self.filters)))
        fail = db.fail.get((self.op, self.table))
        if fail is not None:
            raise fail
        rows = db.tables.setdefault(self.table, [])
        if self.op == "select":
            known = db.columns.get(self.table)
            if known is not None and self.cols != "*":
                for c in (x.strip() for x in str(self.cols).split(",")):
                    if c and c not in known:      # what PostgREST answers for a column that is not there yet
                        raise RuntimeError(f'column {self.table}.{c} does not exist')
            out = [dict(r) for r in rows if self._match(r)]
            n = db.selects[self.table] = db.selects.get(self.table, 0) + 1
            hook = db.after_select.get(self.table)
            if hook:
                hook(n)      # the other writer: runs after this read was taken, before the caller's write
            return SimpleNamespace(data=(out[: self.lim] if self.lim else out),
                                   count=(len(out) if self.want_count else None))
        if self.op in ("insert", "upsert"):
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
            if self.table == "shop_orders":       # lines + events cascade (shop_migration.sql)
                ids = {r["id"] for r in gone}
                for child in ("shop_order_lines", "shop_order_events"):
                    db.tables[child] = [r for r in db.tables.get(child, []) if r.get("order_id") not in ids]
            return SimpleNamespace(data=[dict(r) for r in gone], count=None)
        return SimpleNamespace(data=[], count=None)


class _FakeDB:
    def __init__(self, tables: dict | None = None, columns: dict | None = None):
        self.tables: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        # table → the columns it has; a select naming any other raises like PostgREST does.
        # Tables not listed accept every column (the migration is "applied").
        self.columns: dict[str, set] = {k: set(v) for k, v in (columns or {}).items()}
        self.calls: list[tuple] = []
        self.fail: dict[tuple, Exception] = {}        # (op, table) → exception to raise at execute()
        self.after_select: dict[str, object] = {}     # table → fn(nth select) — the racing writer
        self.selects: dict[str, int] = {}
        self.rpc_results = {"shop_next_order_no": "YQ-2609-0101"}
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
        return [c for c in self.calls if c[0] in ("insert", "update", "delete", "upsert")
                and (table is None or c[1] == table)]

    def rows(self, table: str) -> list[dict]:
        return self.tables.get(table, [])


class _patched:
    """Point app.shop / app.database / app.catalog at the fake, serve `ctx` as the cached catalog
    context, and reset every cache the order path consults (settings, salesman, column probes)."""

    def __init__(self, fake: _FakeDB, ctx: dict | None = None):
        self.fake, self.ctx = fake, ctx

    def __enter__(self):
        import app.catalog as cat
        import app.database as db
        import app.shop as s
        self.mods = (s, db, cat)
        self.saved = (s.get_client, db.get_client, cat.get_client, s._ctx_cache["ctx"], s._ctx_cache["at"])
        s.get_client = db.get_client = cat.get_client = lambda: self.fake
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        if self.ctx is not None:
            s._ctx_cache.update(ctx=self.ctx, at=time.time() + 10 ** 6)
        return self.fake

    def __exit__(self, *_exc):
        s, db, cat = self.mods
        s.get_client, db.get_client, cat.get_client = self.saved[:3]
        s._ctx_cache.update(ctx=self.saved[3], at=self.saved[4])
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        return False


# ── fixtures ──────────────────────────────────────────────────────────────────

def _item(code, price, stock=100, cat="CABLE"):
    return {"item_code": code, "display_name": code, "spec": f"{code} spec", "category": cat, "brand": "VFAN",
            "standard_rate": price, "b2c_rate": None, "product_image_url": None, "package_image_url": None,
            "sort_order": None, "created_at": "2026-07-03T00:00:00+00:00", "moq": 1, "pack_size": None,
            "stock_qty": stock, "stock_as_of": "2026-09-14", "sold_30d": 0, "prev_30d": 0, "sold_90d": 0,
            "customers_30d": 0}


SALESMEN = [{"id": 1, "name": "Furqan Ahmed", "referral_code": "furqan", "is_active": True, "focus_name": "FURQAN",
             "user_email": "rep@example.com", "whatsapp": "97300000001"},
            {"id": 2, "name": "Harsh Bhatia", "referral_code": "harsh", "is_active": True, "focus_name": "HARSH B",
             "user_email": None}]


def _ctx(items, **settings):
    from app.shop import SETTING_DEFAULTS
    vals = dict(SETTING_DEFAULTS)
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: i for i in items},
            "order": [i["item_code"] for i in items], "by_upper": {i["item_code"].upper(): i["item_code"] for i in items},
            "costs": {}, "rules": [], "salesmen": [dict(s) for s in SALESMEN], "pairs": {}, "drops": {},
            "campaigns": [], "loaded_at": ""}


def _body(**over):
    b = {"lines": [{"item_code": "T02", "qty": 3}, {"item_code": "X05", "qty": 2}],
         "customer": {"name": "Test Shop", "phone": "33001122", "shop": "Test Shop", "area": "Manama"},
         "device_id": "dev-1", "client_order_id": "coid-1", "session_ref": "furqan", "session_id": "sess-1"}
    b.update(over)
    return b


NOW = datetime.now(timezone.utc)


def _order(id, status="new", **over):
    o = {"id": id, "order_no": f"YQ-2609-{id:04d}", "token": f"tok{id}".ljust(24, "x"), "status": status,
         "customer_name": "Test Shop", "customer_phone": "97333001122", "customer_shop": "Test Shop",
         "customer_area": "Manama", "customer_email": None, "salesman_id": 1, "salesman_name": "Furqan Ahmed",
         "source": "market", "referral_code": "furqan", "src": None, "coupon_code": None,
         "subtotal_bhd": 12.85, "discount_bhd": 0, "delivery_bhd": 0, "total_bhd": 12.85, "items_count": 2,
         "units_count": 5, "has_backorder": False, "device_id": "dev-1", "client_order_id": "coid-1",
         "created_at": (NOW - timedelta(hours=2)).isoformat(), "updated_at": None, "total_confirmed_bhd": None,
         "attribution_source": "session_ref", "is_test": False}
    o.update(over)
    return o


def _lines(order_id):
    return [{"id": order_id * 10 + 1, "order_id": order_id, "item_code": "T02", "display_name": "T02", "qty": 3,
             "list_price_bhd": 2.95, "unit_price_bhd": 2.95, "discount_bhd": 0, "line_total_bhd": 8.85,
             "stock_status": "in_stock", "backorder": False, "line_status": "ok", "qty_confirmed": None},
            {"id": order_id * 10 + 2, "order_id": order_id, "item_code": "X05", "display_name": "X05", "qty": 2,
             "list_price_bhd": 2.0, "unit_price_bhd": 2.0, "discount_bhd": 0, "line_total_bhd": 4.0,
             "stock_status": "in_stock", "backorder": False, "line_status": "ok", "qty_confirmed": None}]


def _db(**tables) -> _FakeDB:
    base = {"salesmen": SALESMEN, "app_settings": [{"key": "shop_market_url", "value": "https://yqmarketplace.com"}],
            "shop_customers": [], "shop_customer_phones": [], "shop_orders": [], "shop_order_lines": [],
            "shop_order_events": [], "shop_events": []}
    base.update(tables)
    return _FakeDB(base)


def _raises(fn, want: str):
    from app.shop import ShopError
    try:
        fn()
    except ShopError as e:
        assert want in str(e), (want, str(e))
        return e
    raise AssertionError(f"expected ShopError containing {want!r}")


# ── 1. atomic create ──────────────────────────────────────────────────────────

@test("create: a failed lines insert takes the header back out — no line-less order, error re-raised")
def _():
    from app.shop import create_order
    fake = _db()
    fake.fail[("insert", "shop_order_lines")] = RuntimeError("lines insert died")
    with _patched(fake, _ctx([_item("T02", 2.95), _item("X05", 2.0)])):
        try:
            create_order(_body(), market=True)
            raise AssertionError("expected the lines failure to propagate")
        except RuntimeError as e:
            assert "lines insert died" in str(e), e      # the original error, not a swallowed one
        assert fake.rows("shop_orders") == [], fake.rows("shop_orders")
        assert fake.rows("shop_order_lines") == [] and fake.rows("shop_order_events") == []
        deletes = [c for c in fake.writes("shop_orders") if c[0] == "delete"]
        assert len(deletes) == 1 and ("eq", "id", 1, False) in deletes[0][3], deletes
        # the merchant's retry (same device + client_order_id) now places the order properly
        fake.fail.clear()
        o = create_order(_body(), market=True)
        assert not o.get("duplicate") and len(fake.rows("shop_orders")) == 1
        assert [ln["order_id"] for ln in fake.rows("shop_order_lines")] == [o["id"], o["id"]]
        assert [e["event"] for e in fake.rows("shop_order_events")] == ["created"]


@test("create: a failed created-event insert also removes the header (lines cascade with it)")
def _():
    from app.shop import create_order
    fake = _db()
    fake.fail[("insert", "shop_order_events")] = RuntimeError("event insert died")
    with _patched(fake, _ctx([_item("T02", 2.95), _item("X05", 2.0)])):
        try:
            create_order(_body(), market=True)
            raise AssertionError("expected the event failure to propagate")
        except RuntimeError:
            pass
        assert fake.rows("shop_orders") == [] and fake.rows("shop_order_lines") == []


@test("create: a duplicate header with zero lines is not an order — it is removed and the order placed")
def _():
    from app.shop import create_order
    stale = _order(7, device_id="dev-1", client_order_id="coid-1")
    fake = _db(shop_orders=[stale])                    # header only: the debris of a dead attempt
    with _patched(fake, _ctx([_item("T02", 2.95), _item("X05", 2.0)])):
        o = create_order(_body(), market=True)
        assert not o.get("duplicate"), "a line-less header must not be returned as the created order"
        assert o["id"] != 7 and [r["id"] for r in fake.rows("shop_orders")] == [o["id"]]
        assert len([ln for ln in fake.rows("shop_order_lines") if ln["order_id"] == o["id"]]) == 2
        # …and the real duplicate path still answers the retry with the order it already has
        again = create_order(_body(), market=True)
        assert again.get("duplicate") is True and again["id"] == o["id"], again.get("id")
        assert len(again["lines"]) == 2 and again["totals"]["total_bhd"] == o["totals"]["total_bhd"]
        assert len(fake.rows("shop_orders")) == 1


# ── 2. compare-and-swap status moves ──────────────────────────────────────────

@test("cas: set_status loses to a writer who moved the order first — nothing stamped, no event")
def _():
    from app.shop import CAS_CONFLICT_MSG, set_status
    fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1))
    fake.after_select["shop_orders"] = lambda n: fake.rows("shop_orders")[0].update(status="cancelled") if n == 1 else None
    with _patched(fake):
        _raises(lambda: set_status(1, "confirmed", "ok", actor="rep@example.com"), CAS_CONFLICT_MSG)
        row = fake.rows("shop_orders")[0]
        assert row["status"] == "cancelled" and "confirmed_at" not in row, row
        assert fake.rows("shop_order_events") == [] and fake.writes("shop_order_events") == []
        # the same move with nobody racing goes through and writes exactly one event
        fake.after_select.clear()
        fake.rows("shop_orders")[0]["status"] = "new"
        out = set_status(1, "confirmed", "ok", actor="rep@example.com")
        assert out["status"] == "confirmed" and out.get("confirmed_at")
        assert [e["event"] for e in fake.rows("shop_order_events")] == ["status:confirmed"]
        assert ("eq", "status", "new", False) in [c for c in fake.writes("shop_orders") if c[0] == "update"][-1][3]


@test("cas: confirm_order loses the race — no line update, no header change, no event")
def _():
    from app.shop import CAS_CONFLICT_MSG, confirm_order
    fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1))
    fake.after_select["shop_orders"] = lambda n: fake.rows("shop_orders")[0].update(status="cancelled") if n == 1 else None
    with _patched(fake, _ctx([_item("T02", 2.95), _item("X05", 2.0)])):
        _raises(lambda: confirm_order(1, [{"line_id": 11, "qty_confirmed": 2}], "Tomorrow", None, actor="rep@example.com"),
                CAS_CONFLICT_MSG)
        assert fake.writes("shop_order_lines") == [] and fake.writes("shop_order_events") == []
        assert all(ln["qty_confirmed"] is None for ln in fake.rows("shop_order_lines"))
        row = fake.rows("shop_orders")[0]
        assert row["status"] == "cancelled" and row["total_confirmed_bhd"] is None and "expected_delivery" not in row


@test("cas: confirm_order that wins re-prices at confirmed quantities and stamps the confirmed prices per line")
def _():
    from app.shop import confirm_order
    fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1))
    with _patched(fake, _ctx([_item("T02", 2.95), _item("X05", 2.0)])):
        out = confirm_order(1, [{"line_id": 11, "qty_confirmed": 2}, {"line_id": 12, "line_status": "removed"}],
                            "Tomorrow", "trimmed", actor="rep@example.com")
        assert out["status"] == "confirmed" and out["removed"] == ["X05"]
        assert out["changed"] == [{"item_code": "T02", "from": 3, "to": 2}]
        assert out["total_confirmed_bhd"] == 5.9, out["total_confirmed_bhd"]
        by = {ln["id"]: ln for ln in fake.rows("shop_order_lines")}
        assert by[11]["qty_confirmed"] == 2 and by[11]["unit_price_confirmed"] == 2.95 and by[11]["line_total_confirmed"] == 5.9
        assert by[12]["line_status"] == "removed" and by[12]["qty_confirmed"] == 0 and "unit_price_confirmed" not in by[12]
        assert [e["event"] for e in fake.rows("shop_order_events")] == ["status:confirmed"]
        # the header update was filtered on the status that was read
        upd = [c for c in fake.writes("shop_orders") if c[0] == "update"]
        assert len(upd) == 1 and ("eq", "status", "new", False) in upd[0][3], upd


@test("cas: before the M2 migration confirm_order stores totals only (no *_confirmed line columns)")
def _():
    from app.shop import confirm_order
    fake = _FakeDB({**_db(shop_orders=[_order(1)], shop_order_lines=_lines(1)).tables},
                   columns={"shop_order_lines": set(_lines(1)[0]) | {"note"}})
    with _patched(fake, _ctx([_item("T02", 2.95), _item("X05", 2.0)])):
        out = confirm_order(1, [], None, None, actor="rep@example.com")
        assert out["status"] == "confirmed" and out["total_confirmed_bhd"] == 12.85
        for ln in fake.rows("shop_order_lines"):
            assert ln["qty_confirmed"] == ln["qty"] and "unit_price_confirmed" not in ln, ln


@test("cas: the merchant's cancel loses to the rep's confirm on either read — no cancel is written")
def _():
    from app.shop import CAS_CONFLICT_MSG, cancel_by_customer
    for flip_at in (2, 3):     # after the token read (pinned expected status) / after set_status's own read (CAS)
        fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1))
        fake.after_select["shop_orders"] = (lambda n, f=fake, k=flip_at:
                                            f.rows("shop_orders")[0].update(status="confirmed") if n == k else None)
        with _patched(fake):
            _raises(lambda: cancel_by_customer("tok1".ljust(24, "x"), "changed my mind"), CAS_CONFLICT_MSG)
            row = fake.rows("shop_orders")[0]
            assert row["status"] == "confirmed" and "cancelled_at" not in row and "cancelled_by" not in row, (flip_at, row)
            assert fake.writes("shop_order_events") == [] and fake.writes("shop_order_lines") == [], flip_at
            upd = [c for c in fake.writes("shop_orders") if c[0] == "update"]
            if flip_at == 2:      # the pinned read short-circuits: not even the UPDATE is attempted
                assert upd == [], upd
            else:                 # the UPDATE ran but its status filter matched nothing
                assert len(upd) == 1 and ("eq", "status", "new", False) in upd[0][3], upd
    fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1))
    with _patched(fake):
        out = cancel_by_customer("tok1".ljust(24, "x"), "changed my mind")
        assert out["status"] == "cancelled" and out["cancelled_by"] == "customer" and out["cancel_reason"] == "changed my mind"
        assert [e["event"] for e in fake.rows("shop_order_events")] == ["status:cancelled"]


# ── 3. salesman delete guard ──────────────────────────────────────────────────

@test("salesmen: delete is refused while any order or merchant points at the rep; clean reps go, with name + focus_name")
def _():
    from app.shop import delete_salesman
    refusal = "Has orders or merchants — deactivate instead"
    cases = {
        "orders.salesman_id": {"shop_orders": [_order(1, salesman_id=2)]},
        "orders.issued_to_salesman_id": {"shop_orders": [_order(1, salesman_id=1, issued_to_salesman_id=2)]},
        "customers.salesman_id": {"shop_customers": [{"id": 5, "phone": "97333001122", "salesman_id": 2}]},
        "customers.sticky_salesman_id": {"shop_customers": [{"id": 5, "phone": "97333001122", "sticky_salesman_id": 2}]},
    }
    for label, tables in cases.items():
        fake = _db(**tables)
        with _patched(fake):
            _raises(lambda: delete_salesman(2), refusal)
            assert fake.writes("salesmen") == [], (label, fake.writes("salesmen"))
            assert [s["id"] for s in fake.rows("salesmen")] == [1, 2], label
    # a count that cannot be taken is never read as "nothing references this rep"
    fake = _db()
    fake.fail[("select", "shop_customers")] = RuntimeError("down")
    with _patched(fake):
        _raises(lambda: delete_salesman(2), "Could not check")
        assert fake.writes("salesmen") == []
    # unreferenced: removed, and the audit fields come back because the row is gone afterwards
    fake = _db(shop_orders=[_order(1, salesman_id=1)])
    with _patched(fake):
        gone = delete_salesman(2)
        assert gone == {"id": 2, "name": "Harsh Bhatia", "focus_name": "HARSH B"}, gone
        assert [s["id"] for s in fake.rows("salesmen")] == [1]
        _raises(lambda: delete_salesman(2), "not found")


@test("salesmen: the list carries reference counts so the page can offer Deactivate instead of Delete")
def _():
    from app.shop import list_salesmen
    fake = _db(shop_orders=[_order(1, salesman_id=1), _order(2, salesman_id=1, issued_to_salesman_id=1)],
               shop_customers=[{"id": 5, "phone": "97333001122", "sticky_salesman_id": 1}])
    with _patched(fake):
        rows = {s["id"]: s for s in list_salesmen()}
        assert rows[1]["references"] == {"orders": 3, "merchants": 1}, rows[1]["references"]
        assert rows[2]["references"] == {"orders": 0, "merchants": 0}, rows[2]["references"]
        assert rows[1]["link"] == "https://yqmarketplace.com/furqan"
    fake = _db()
    fake.fail[("select", "shop_customers")] = RuntimeError("down")
    with _patched(fake):
        assert all(s["references"] is None for s in list_salesmen()), "unknown counts must read as unknown, not zero"


@test("salesmen: DELETE route answers 409 with the reason and audits name + focus_name on success")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app.auth import CurrentUser, get_current_user
    admin = CurrentUser(user_id="t", email="admin@example.com", role="admin")
    m.app.dependency_overrides[get_current_user] = lambda: admin
    try:
        fake = _db(shop_orders=[_order(1, salesman_id=2)])
        with _patched(fake):
            r = TestClient(m.app).delete("/shop/salesmen/2")
            assert r.status_code == 409 and "deactivate instead" in r.json()["detail"], (r.status_code, r.text[:200])
            assert fake.writes("salesmen") == []
        fake = _db()
        with _patched(fake):
            r = TestClient(m.app).delete("/shop/salesmen/2")
            assert r.status_code == 200 and r.json() == {"ok": True}, r.text[:200]
            audit = [c[2] for c in fake.writes("audit_log")]
            assert audit and audit[-1]["event"] == "shop.salesman_delete", audit
            assert audit[-1]["detail"] == {"id": 2, "name": "Harsh Bhatia", "focus_name": "HARSH B"}, audit[-1]
            r = TestClient(m.app).delete("/shop/salesmen/2")
            assert r.status_code == 404, r.text[:200]
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)


# ── 4. is_test ────────────────────────────────────────────────────────────────

@test("is_test: analytics, the rep's KPIs and the quick-pick leave flagged orders out")
def _():
    from app.shop import analytics, me_payload, recent_customers
    real = _order(1, status="delivered", total_bhd=10.0, customer_phone="97311111111",
                  created_at=(NOW - timedelta(days=1)).isoformat())
    tst = _order(2, status="delivered", total_bhd=999.0, customer_phone="97322222222", is_test=True,
                 created_at=(NOW - timedelta(days=1)).isoformat())
    fake = _db(shop_orders=[real, tst], shop_order_lines=_lines(1) + _lines(2))
    with _patched(fake):
        a = analytics(30)
        assert a["orders"] == 1 and a["value_bhd"] == 10.0 and a["customers"] == 1 and a["cancelled"] == 0, \
            {k: a[k] for k in ("orders", "value_bhd", "customers", "cancelled")}
        assert a["leaderboard"][0]["orders"] == 1
        me = me_payload("rep@example.com")
        assert me["kpis"]["orders_30d"] == 1 and me["kpis"]["value_30d_bhd"] == 10.0 and me["kpis"]["customers_30d"] == 1
        picks = recent_customers(1)
        assert [p["phone"] for p in picks] == ["97311111111"], picks


# shop_orders as it is before scripts/shop_orders_ops_migration.sql: every column the readers name,
# minus the R1 ones (the fake raises for a select that names anything else, like PostgREST).
ORDER_COLS_PRE_M2 = (set(_order(1)) | {
    "units_count", "salesman_id", "salesman_name", "referral_code", "src", "coupon_code", "has_backorder", "source",
    "attribution_source", "attribution_conflict", "assigned_at", "confirmed_at", "cancelled_by", "customer_id",
    "device_id", "customer_email", "expected_delivery", "order_kind", "minimum_gap_bhd",
}) - {"is_test", "confirm_notified_at", "notify_attempts"}


@test("is_test: before the migration the readers never name the column and count every order")
def _():
    from app.shop import analytics, has_column, me_payload, recent_customers
    rows = [{k: v for k, v in _order(i, status="delivered", total_bhd=5.0, customer_phone=f"9731111111{i}",
                                     created_at=(NOW - timedelta(days=1)).isoformat()).items() if k != "is_test"}
            for i in (1, 2)]
    fake = _FakeDB({**_db().tables, "shop_orders": rows, "shop_order_lines": _lines(1) + _lines(2)},
                   columns={"shop_orders": ORDER_COLS_PRE_M2})
    with _patched(fake):
        assert has_column("shop_orders", "is_test") is False
        assert analytics(30)["orders"] == 2
        assert me_payload("rep@example.com")["kpis"]["orders_30d"] == 2
        assert len(recent_customers(1)) == 2
        reads = [c for c in fake.calls if c[0] == "select" and c[1] == "shop_orders"]
        assert len(reads) >= 4, "the probe plus the three readers"
        # the probe is the only read that names the column; everything after it degrades
        assert reads[0][2] == "is_test" and all("is_test" not in str(c[2]) for c in reads[1:]), [c[2] for c in reads]
    # once the column exists the probe says so and is remembered
    fake = _db(shop_orders=[_order(1)])
    with _patched(fake):
        assert has_column("shop_orders", "is_test") is True
        n = len(fake.calls)
        assert has_column("shop_orders", "is_test") is True and len(fake.calls) == n, "the probe must be cached"


@test("is_test: PATCH /shop/orders/{id}/test is admin-only, flips the flag once and writes the audit + order event")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app.auth import CurrentUser, get_current_user
    admin = CurrentUser(user_id="t", email="admin@example.com", role="admin")
    rep = CurrentUser(user_id="r", email="rep@example.com", role="salesman")
    try:
        m.app.dependency_overrides[get_current_user] = lambda: rep
        fake = _db(shop_orders=[_order(90, status="delivered")], shop_order_lines=_lines(90))
        with _patched(fake):
            r = TestClient(m.app).patch("/shop/orders/90/test", json={"is_test": True})
            assert r.status_code == 403, (r.status_code, r.text[:200])
            assert fake.writes() == [] and fake.rows("shop_orders")[0]["is_test"] is False
        m.app.dependency_overrides[get_current_user] = lambda: admin
        with _patched(fake):
            r = TestClient(m.app).patch("/shop/orders/90/test", json={"is_test": True})
            assert r.status_code == 200 and r.json()["is_test"] is True and r.json()["order_no"] == "YQ-2609-0090", r.text[:200]
            assert fake.rows("shop_orders")[0]["is_test"] is True
            ev = fake.rows("shop_order_events")
            assert [e["event"] for e in ev] == ["test_flag"] and ev[0]["detail"] == {"is_test": True, "was": False}, ev
            audit = [c[2] for c in fake.writes("audit_log")]
            assert audit and audit[-1]["event"] == "shop.order_test_flag" and audit[-1]["detail"]["is_test"] is True
            # setting the same value again is a no-op: no second event
            r = TestClient(m.app).patch("/shop/orders/90/test", json={"is_test": True})
            assert r.status_code == 200 and len(fake.rows("shop_order_events")) == 1
            r = TestClient(m.app).patch("/shop/orders/90/test", json={"is_test": False})
            assert r.status_code == 200 and fake.rows("shop_orders")[0]["is_test"] is False
            assert len(fake.rows("shop_order_events")) == 2
            r = TestClient(m.app).patch("/shop/orders/404/test", json={"is_test": True})
            assert r.status_code == 404, r.text[:200]
        # before the migration: a clear 400, nothing written
        row = {k: v for k, v in _order(97).items() if k != "is_test"}
        fake = _FakeDB({**_db().tables, "shop_orders": [row]}, columns={"shop_orders": set(row)})
        with _patched(fake):
            r = TestClient(m.app).patch("/shop/orders/97/test", json={"is_test": True})
            assert r.status_code == 400 and "shop_orders_ops_migration" in r.json()["detail"], r.text[:200]
            assert fake.writes() == []
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)


@test("is_test: the status routes answer 409 on a lost compare-and-swap")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app.auth import CurrentUser, get_current_user
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="t", email="admin@example.com", role="admin")
    try:
        fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1))
        # the route reads once for visibility, set_status reads again — flip after that second read
        fake.after_select["shop_orders"] = lambda n: fake.rows("shop_orders")[0].update(status="cancelled") if n == 2 else None
        with _patched(fake):
            r = TestClient(m.app).post("/shop/orders/1/status", json={"status": "confirmed"})
            assert r.status_code == 409 and "refresh" in r.json()["detail"], (r.status_code, r.text[:200])
            assert fake.rows("shop_order_events") == []
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=3)
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
