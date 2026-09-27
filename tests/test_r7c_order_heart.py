"""R7c — the order heart (Sprint 3, owner-simplified 27-Sep-2026): Received → Confirmed → Delivered.

    python -m tests.test_r7c_order_heart

Same lightweight runner and the same idea as tests/test_r3_pipeline.py / tests/test_r7a_focus.py:
every test runs against an in-memory stand-in for the PostgREST client (eq / is / in filters,
inserts that hand out ids, updates that report what they touched, an optional column list so a
read or write naming a column the table does not have raises like PostgREST, a line_status CHECK
like the live one, per-call failure injection and a "racing writer" hook after a read). Nothing
here reaches a database or the network (CI: SUPABASE_URL=https://ci.invalid). All data is
synthetic — made-up shops, reps, items and numbers.

Covered:
  * the vocabulary — three visible stages (packed / out_for_delivery read "Confirmed"), what each
    role is offered (next_statuses, actions), the ETA chips resolved to a Bahrain date;
  * confirm — the one editor: reasons required for every change ('other' needs a note), qty 0 =
    unavailable, substitute, backorder, added lines, every line removed refused;
  * the price lock — a price-book change after placement never reaches the order; a reduction
    keeps the ordered (tier) unit price; an item since unlisted / unpriced is never zeroed; an
    added item the book cannot price is refused;
  * the adverse tick — refused without "Shop agreed" (and nothing written), accepted with it and
    recorded on the event; not needed for a plain stock cut, an order already under the minimum,
    or an added line;
  * amend — from Confirmed / Preparing only, an 'amended' event with per-line before/after;
  * deliver — one tap (delivered = confirmed) and with changes (shortfall reason, lines added at
    the shop, the delivered value becomes the agreed total, the minimum tick);
  * compare-and-swap — status and updated_at races lose with 409 and write nothing; a failure
    after the header swap puts the header and the lines back and removes the added rows;
  * the status route — 'confirmed' → use /confirm, the stamps are the storekeeper's alone;
  * rep-placed orders born Confirmed and never reminded; reopen (admin, 7 days, reason); "Tell
    the shop" logged; the payloads (rep, desk, public) and one effective_money rule;
  * the pre-migration fallbacks (new statuses mapped, R7c columns never named, 'below_minimum'
    kept on the event) and a column vanishing under a cached probe;
  * a Decimal property test over confirm → amend → deliver (Σ lines = total to the fils);
  * the migration and its reverse as text, and a local Postgres replay (SKIPs without a cluster).
"""
from __future__ import annotations

import io
import os
import random
import re
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
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


MIGRATION = ROOT / "scripts" / "r7c_order_lines_qty_migration.sql"
REVERSE = ROOT / "scripts" / "r7c_order_lines_qty_reverse.sql"

# ── a table-aware fake PostgREST client ─────────────────────────────────────────

LIVE_LINE_STATUSES = ("ok", "changed", "removed", "backorder")                   # read from production, 27-Sep
R7C_LINE_STATUSES = LIVE_LINE_STATUSES + ("substituted", "added", "unavailable")


def _same(a, b) -> bool:
    return a == b or str(a) == str(b)


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.negate = "select", None, [], False
        self.cols, self.want_count, self.lim, self.head, self.desc = "*", None, None, False, False

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

    def order(self, col, desc=False, **_kw):
        self.desc = bool(desc)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.lim = b - a + 1
        return self

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*a, **_kw):
            if name in ("gte", "gt", "lte", "lt"):
                return self._filter(name, a[0], a[1])
            return self
        return call

    def _match(self, row: dict) -> bool:
        for kind, col, val, neg in self.filters:
            got = row.get(col)
            if kind == "eq":
                ok = _same(got, val)
            elif kind == "is":
                ok = (got is None) if str(val).lower() == "null" else (bool(got) == (str(val).lower() == "true"))
            elif kind == "in":
                ok = any(_same(got, v) for v in val)
            elif kind in ("lt", "lte", "gt", "gte"):
                if got is None:
                    ok = False
                else:
                    a, b = str(got), str(val)
                    ok = {"lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[kind]
            else:
                ok = True
            if neg:
                ok = not ok
            if not ok:
                return False
        return True

    def _check_payload(self):
        known = self.db.columns.get(self.table)
        items = self.payload if isinstance(self.payload, list) else [self.payload]
        for p in items:
            for c in p or {}:
                if known is not None and c not in known:
                    raise RuntimeError(f"PGRST204: Could not find the '{c}' column of '{self.table}' in the schema cache")
            if self.table == "shop_order_lines" and "line_status" in (p or {}):
                allowed = self.db.line_statuses
                if p["line_status"] not in allowed:
                    raise RuntimeError('23514: new row for relation "shop_order_lines" violates check constraint '
                                       '"shop_order_lines_line_status_check"')

    def execute(self):
        db = self.db
        db.calls.append((self.op, self.table, self.cols if self.op == "select" else self.payload, list(self.filters)))
        n_call = db.ncalls[(self.op, self.table)] = db.ncalls.get((self.op, self.table), 0) + 1
        fail = db.fail.get((self.op, self.table))
        if callable(fail) and not isinstance(fail, BaseException):
            fail = fail(n_call)
        if fail is not None:
            raise fail  # type: ignore[misc]
        if self.table in db.missing:
            raise RuntimeError(f'{{"code": "42P01", "message": "relation \\"public.{self.table}\\" does not exist"}}')
        rows = db.tables.setdefault(self.table, [])
        if self.op == "select":
            known = db.columns.get(self.table)
            if known is not None and self.cols != "*":
                for c in (x.strip() for x in str(self.cols).split(",")):
                    if c and c not in known:
                        raise RuntimeError(f"42703: column {self.table}.{c} does not exist")
            out = [dict(r) for r in rows if self._match(r)]
            if self.desc:
                out.reverse()
            n = db.selects[self.table] = db.selects.get(self.table, 0) + 1
            hook = db.after_select.get(self.table)
            if hook:
                hook(n)
            data = [] if self.head else (out[: self.lim] if self.lim else out)
            return SimpleNamespace(data=data, count=(len(out) if self.want_count else None))
        if self.op == "insert":
            self._check_payload()
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for p in items:
                r = dict(p)
                r.setdefault("id", db.next_id(self.table))
                if self.table == "shop_order_events":
                    r.setdefault("ts", db.clock())
                rows.append(r)
                out.append(dict(r))
            return SimpleNamespace(data=out, count=None)
        if self.op == "update":
            self._check_payload()
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload)
                    out.append(dict(r))
            return SimpleNamespace(data=out, count=None)
        if self.op == "delete":
            gone = [r for r in rows if self._match(r)]
            rows[:] = [r for r in rows if not self._match(r)]
            return SimpleNamespace(data=[dict(r) for r in gone], count=None)
        return SimpleNamespace(data=[], count=None)


class _FakeDB:
    def __init__(self, tables: dict | None = None, columns: dict | None = None, missing: set | None = None,
                 pre_r7c: bool = False):
        self.tables: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.columns: dict[str, set] = {k: set(v) for k, v in (columns or {}).items()}
        self.missing: set[str] = set(missing or ())
        self.calls: list[tuple] = []
        self.fail: dict[tuple, object] = {}
        self.after_select: dict[str, object] = {}
        self.selects: dict[str, int] = {}
        self.ncalls: dict[tuple, int] = {}
        self.rpc_results = {"shop_next_order_no": "YQ-2609-0101"}
        self._ids: dict[str, int] = {}
        self._tick = 0
        self.line_statuses = LIVE_LINE_STATUSES if pre_r7c else R7C_LINE_STATUSES
        if pre_r7c:
            self.columns.setdefault("shop_order_lines", set(LINE_COLS_PRE))
            self.columns.setdefault("shop_orders", set(ORDER_COLS_PRE))

    def clock(self) -> str:
        """Event timestamps that strictly increase (the real column is now() per insert)."""
        self._tick += 1
        return (datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc) + timedelta(seconds=self._tick)).isoformat()

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

    def rows(self, table: str) -> list[dict]:
        return self.tables.get(table, [])


class _patched:
    """Point app.shop / app.database / app.catalog / app.upcoming at the fake, serve `ctx` as the
    cached catalog context, and reset every cache the order path consults."""

    def __init__(self, fake: _FakeDB, ctx: dict | None = None):
        self.fake, self.ctx = fake, ctx

    def __enter__(self):
        import app.catalog as cat
        import app.database as db
        import app.shop as s
        import app.shop_notify as sn
        import app.upcoming as up
        self.mods = (s, db, cat, up, sn)
        self.saved = (s.get_client, db.get_client, cat.get_client, up.get_client, s.context,
                      s._ctx_cache["ctx"], s._ctx_cache["at"])
        s.get_client = db.get_client = cat.get_client = up.get_client = lambda: self.fake
        ctx = self.ctx if self.ctx is not None else _ctx()
        s._ctx_cache.update(ctx=ctx, at=time.time() + 10 ** 6)
        s.context = lambda force=False: ctx
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        db.invalidate_user_cache()
        sn.forget_notifications_probe()
        return self.fake

    def __exit__(self, *_exc):
        s, db, cat, up, sn = self.mods
        s.get_client, db.get_client, cat.get_client, up.get_client, s.context = self.saved[:5]
        s._ctx_cache.update(ctx=self.saved[5], at=self.saved[6])
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        db.invalidate_user_cache()
        sn.forget_notifications_probe()
        return False


@contextmanager
def _swap(obj, **attrs):
    saved = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(obj, k, v)


# ── synthetic fixtures ──────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)
REP, OTHER, ADMIN, STORE, MGMT = ("rep@example.com", "other@example.com", "admin@example.com",
                                  "store@example.com", "mgmt@example.com")
SALESMEN = [
    {"id": 1, "name": "Rep One", "referral_code": "rep-one", "is_active": True, "focus_name": "REP ONE",
     "user_email": REP, "whatsapp": "97300000001", "email": "rep1@example.test"},
    {"id": 2, "name": "Rep Two", "referral_code": "rep-two", "is_active": True, "focus_name": "REP TWO",
     "user_email": OTHER, "whatsapp": "97300000002"},
]
USERS = [
    {"email": REP, "role": "salesman", "features": ["Catalog", "Shop Orders"], "status": "active", "full_name": "Rep One",
     "must_reset": False},
    {"email": OTHER, "role": "salesman", "features": ["Catalog", "Shop Orders"], "status": "active",
     "full_name": "Rep Two", "must_reset": False},
    {"email": STORE, "role": "storekeeper", "features": ["Storekeeper", "Shop Orders"], "status": "active",
     "full_name": "Store Keeper", "must_reset": False},
    {"email": MGMT, "role": "management", "features": ["Shop Orders"], "status": "active", "full_name": "Boss",
     "must_reset": False},
]


def _item(code, price, stock=100, cat="CABLE", moq=1):
    return {"item_code": code, "display_name": f"{code} name", "spec": f"{code} spec", "category": cat,
            "brand": "VFAN", "standard_rate": price, "b2c_rate": None, "product_image_url": None,
            "package_image_url": None, "sort_order": None, "created_at": "2026-07-03T00:00:00+00:00", "moq": moq,
            "pack_size": None, "stock_qty": stock, "stock_as_of": "2026-09-26", "sold_30d": 0, "prev_30d": 0,
            "sold_90d": 0, "customers_30d": 0}


ITEMS = [_item("T02", 2.95), _item("X05", 2.0), _item("C18", 1.25), _item("UK20", 4.5), _item("UK20N", 4.5),
         _item("UK21", 5.25), _item("SOLD0", 3.0, stock=0), _item("POR", None)]


def _ctx(items=None, min_order="20", rules=(), **settings):
    from app.shop import SETTING_DEFAULTS
    items = items if items is not None else ITEMS
    vals = dict(SETTING_DEFAULTS)
    vals.update({"shop_min_order_bhd": str(min_order)})
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: i for i in items},
            "order": [i["item_code"] for i in items], "by_upper": {i["item_code"].upper(): i["item_code"] for i in items},
            "costs": {"T02": 1.1, "X05": 0.8, "C18": 0.5, "UK20N": 2.2, "UK21": 2.6},
            "cost_sources": {"T02": "mrn", "X05": "purchase_costs", "C18": "mrn", "UK20N": "mrn", "UK21": "mrn"},
            "rules": list(rules), "salesmen": [dict(s) for s in SALESMEN], "pairs": {}, "drops": {},
            "campaigns": [], "loaded_at": "", "share_token": "tok-test-token-value"}


def _order(id, status="new", **over):
    o = {"id": id, "order_no": f"YQ-2609-{id:04d}", "token": f"tok{id}".ljust(24, "x"), "status": status,
         "customer_name": "Test Shop", "customer_phone": "97333001122", "customer_shop": "Test Shop",
         "customer_area": "Manama", "customer_email": None, "salesman_id": 1, "salesman_name": "Rep One",
         "source": "market", "referral_code": "rep-one", "src": None, "coupon_code": None,
         "subtotal_bhd": 37.5, "discount_bhd": 0, "delivery_bhd": 0, "total_bhd": 37.5, "items_count": 2,
         "units_count": 14, "has_backorder": False, "created_at": (NOW - timedelta(hours=3)).isoformat(),
         "updated_at": (NOW - timedelta(hours=3)).isoformat(), "confirmed_at": None, "delivered_at": None,
         "cancelled_at": None, "cancelled_by": None, "cancel_reason": None, "cancel_reason_code": None,
         "subtotal_confirmed_bhd": None, "total_confirmed_bhd": None, "expected_delivery": None,
         "is_test": False, "customer_id": None, "payment_status": "unpaid", "focus_invoice_no": None,
         "order_kind": "standard", "minimum_gap_bhd": None, "notify_result": None, "placed_by": None}
    if status in ("confirmed", "packed", "out_for_delivery", "delivered"):
        o.update(confirmed_at=(NOW - timedelta(hours=2)).isoformat(), subtotal_confirmed_bhd=37.5,
                 total_confirmed_bhd=37.5)
    if status == "delivered":
        o["delivered_at"] = (NOW - timedelta(hours=1)).isoformat()
    if status == "cancelled":
        o.update(cancelled_at=(NOW - timedelta(hours=1)).isoformat(), cancelled_by="staff",
                 cancel_reason="Duplicate order", cancel_reason_code="duplicate")
    o.update(over)
    return o


def _line(lid, order_id, code, qty, unit, *, lp=None, confirmed=False, **over):
    lp = unit if lp is None else lp
    ln = {"id": lid, "order_id": order_id, "item_code": code, "display_name": f"{code} name", "spec": None,
          "image_url": None, "qty": qty, "list_price_bhd": lp, "unit_price_bhd": unit,
          "discount_bhd": float((Decimal(str(lp)) - Decimal(str(unit))) * qty),
          "line_total_bhd": float(Decimal(str(unit)) * qty), "stock_status": "in_stock", "backorder": False,
          "rule_ids": None, "qty_confirmed": None, "line_status": "ok", "note": None,
          "unit_price_confirmed": None, "line_total_confirmed": None}
    if confirmed:
        ln.update(qty_confirmed=qty, unit_price_confirmed=unit, line_total_confirmed=ln["line_total_bhd"])
    ln.update(over)
    return ln


def _lines(order_id, confirmed=False):
    """T02 10 × 2.950 = 29.500 + X05 4 × 2.000 = 8.000 → 37.500 (the order total)."""
    return [_line(order_id * 10 + 1, order_id, "T02", 10, 2.95, confirmed=confirmed),
            _line(order_id * 10 + 2, order_id, "X05", 4, 2.0, confirmed=confirmed)]


LINE_COLS_PRE = set(_line(1, 1, "T02", 1, 1.0)) | {"note"}
ORDER_COLS_PRE = set(_order(1)) | {"note", "cancelled_by", "packed_at", "out_for_delivery_at", "issued_to_salesman_id",
                                   "issued_at", "assigned_at", "assigned_by", "session_ref", "ua", "ip_hash",
                                   "sla_notified_at", "small_order_fee_bhd", "confirm_notified_at",
                                   "notify_attempts", "notified_at", "device_id", "client_order_id",
                                   "attribution_source", "attribution_conflict", "paid_at", "returned_bhd",
                                   "payment_method"}


def _db(pre_r7c: bool = False, missing: set | None = None, **tables) -> _FakeDB:
    base = {"salesmen": SALESMEN, "app_settings": [], "shop_customers": [], "shop_orders": [],
            "shop_order_lines": [], "shop_order_events": [], "shop_events": [], "audit_log": [],
            "shop_admin_audit": [], "shop_notifications": [], "user_roles": USERS}
    base.update(tables)
    return _FakeDB(base, missing=missing, pre_r7c=pre_r7c)


def _one(status="new", pre_r7c=False, **over) -> _FakeDB:
    confirmed = status in ("confirmed", "packed", "out_for_delivery", "delivered")
    lines = _lines(1, confirmed=confirmed)
    return _db(pre_r7c=pre_r7c, shop_orders=[_order(1, status, **over)], shop_order_lines=lines)


def _raises(fn, want: str):
    from app.shop import ShopError
    try:
        fn()
    except ShopError as e:
        assert want.lower() in str(e).lower(), (want, str(e))
        return e
    raise AssertionError(f"expected ShopError containing {want!r}")


def _events(fake, order_id=None, name=None):
    return [e for e in fake.rows("shop_order_events")
            if (order_id is None or e.get("order_id") == order_id) and (name is None or e.get("event") == name)]


def _lines_by(fake) -> dict:
    return {ln["id"]: ln for ln in fake.rows("shop_order_lines")}


def D(x) -> Decimal:
    return Decimal(str(x))


def _client(role_email: str, role: str):
    from fastapi.testclient import TestClient
    import app.main as m
    from app.auth import CurrentUser, get_current_user
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="u", email=role_email, role=role)
    return TestClient(m.app)


def _drop_client():
    import app.main as m
    from app.auth import get_current_user
    m.app.dependency_overrides.pop(get_current_user, None)


@contextmanager
def _quiet_notify():
    """Background notifications never leave the test (they run inside TestClient)."""
    import app.shop_notify as sn
    sent: list = []
    with _swap(sn, notify_status=lambda *a, **k: sent.append(a) or {},
               notify_new_order=lambda *a, **k: sent.append(("new",) + a) or {}):
        yield sent


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the vocabulary: three visible stages, what each role is offered, the ETA
# ═══════════════════════════════════════════════════════════════════════════════

@test("stages: Received → Confirmed → Delivered; packed / out_for_delivery read 'Confirmed'; cancelled has no current step")
def _():
    from app import shop, shop_heart as h
    assert shop.TRACK_STEPS == ("new", "confirmed", "delivered")
    assert [h.status_label(s) for s in shop.STATUSES] == ["Received", "Confirmed", "Confirmed", "Confirmed",
                                                          "Delivered", "Cancelled"]
    assert shop.STATUS_LABELS["packed"] == "Preparing", "the storekeeper's pick list keeps its own stamp words"
    steps = shop.order_steps({"status": "out_for_delivery", "created_at": "a", "confirmed_at": "b",
                              "out_for_delivery_at": "c"})
    assert [s["label"] for s in steps] == ["Received", "Confirmed", "Delivered"]
    assert [s["done"] for s in steps] == [True, False, False] and [s["current"] for s in steps] == [False, True, False]
    assert steps[1]["at"] == "b" and steps[2]["at"] is None
    done = shop.order_steps({"status": "delivered", "created_at": "a", "confirmed_at": "b", "delivered_at": "z"})
    assert all(s["done"] for s in done) and done[-1]["current"] and done[-1]["at"] == "z"
    gone = shop.order_steps({"status": "cancelled", "created_at": "a"})
    assert not any(s["current"] or s["done"] for s in gone)


@test("offered moves: reps and the desk get the forward step + cancel; the storekeeper his stamps; management nothing")
def _():
    from app import shop_heart as h
    for role in ("salesman", "admin"):
        assert h.next_statuses("new", role) == ["confirmed", "cancelled"], "Confirmed is reached through /confirm"
        for st in ("confirmed", "packed", "out_for_delivery"):
            assert h.next_statuses(st, role) == ["delivered", "cancelled"], (role, st)
            assert "packed" not in h.next_statuses(st, role) and "out_for_delivery" not in h.next_statuses(st, role)
        assert h.next_statuses("delivered", role) == [] and h.next_statuses("cancelled", role) == []
    assert h.next_statuses("confirmed", "storekeeper") == ["packed", "out_for_delivery"]
    assert h.next_statuses("packed", "storekeeper") == ["out_for_delivery"]
    assert h.next_statuses("new", "storekeeper") == []
    assert h.next_statuses("confirmed", "management", read_only=True) == []
    o = _order(1, "new")
    assert h.actions(o, "salesman") == ["confirm", "cancel", "tell_shop"]
    assert h.actions(_order(1, "packed"), "salesman") == ["deliver", "amend", "cancel", "tell_shop"]
    assert h.actions(_order(1, "delivered"), "salesman") == ["tell_shop"], "a rep never reopens"
    assert h.actions(_order(1, "delivered"), "admin", is_admin=True) == ["reopen", "tell_shop"]
    late = _order(1, "delivered", delivered_at=(NOW - timedelta(days=8)).isoformat())
    assert h.actions(late, "admin", is_admin=True) == ["tell_shop"], "past 7 days nothing reopens"
    assert h.actions(o, "management", read_only=True) == [] and h.actions(o, "storekeeper") == []


@test("ETA chips resolve to a Bahrain date: Today / Tomorrow / Day after tomorrow / a weekday / an ISO date; next visit = none")
def _():
    from app.shop_heart import resolve_eta
    t0 = date(2026, 9, 27)                           # a Sunday
    assert resolve_eta("Today", today=t0) == t0
    assert resolve_eta("Tomorrow", today=t0) == date(2026, 9, 28)
    assert resolve_eta("tomorrow morning", today=t0) == date(2026, 9, 28)
    assert resolve_eta("Day after tomorrow", today=t0) == date(2026, 9, 29)
    assert resolve_eta("Thursday morning", today=t0) == date(2026, 10, 1)
    assert resolve_eta("sunday", today=t0) == date(2026, 10, 4), "a weekday is its NEXT occurrence"
    assert resolve_eta("by 2026-10-05", today=t0) == date(2026, 10, 5)
    assert resolve_eta("With my next visit", today=t0) is None and resolve_eta("", today=t0) is None
    assert resolve_eta("2026-09-01", today=t0) is None, "a date in the past is not an ETA"
    assert resolve_eta("Tomorrow", "2026-10-02", today=t0) == date(2026, 10, 2), "a picked date wins"
    assert resolve_eta("Tomorrow", "2027-09-02", today=t0) == date(2026, 9, 28), "a picked date past 90 days is ignored"
    assert resolve_eta("غداً", today=t0) == date(2026, 9, 28)


@test("money: compute_totals — each line at its locked unit, the cart discount pro rata on requested lines, delivery as ordered")
def _():
    from app.shop_heart import compute_totals
    order = {"discount_bhd": 3.0, "delivery_bhd": 1.5}           # 1.000 item-level + 2.000 cart-level
    a = _line(1, 1, "T02", 10, 2.85, lp=2.95)                   # 28.500, discount 1.000
    b = _line(2, 1, "X05", 4, 2.0)                              # 8.000
    add = _line(3, 1, "C18", 3, 1.25, added_at_stage="amend", line_status="added")
    t = compute_totals(order, [a, b], lambda ln: ln["qty"])
    assert t["items_bhd"] == D("36.500") and t["cart_discount_bhd"] == D("2.000") and t["total_bhd"] == D("36.000")
    # 5 of 10 T02: 14.250 + 8 = 22.250 → cart share 2 × 22.25 / 36.5 = 1.21917… → 1.219
    t = compute_totals(order, [a, b, add], lambda ln: {1: 5, 2: 4, 3: 3}[ln["id"]])
    assert t["items_bhd"] == D("26.000") and t["cart_discount_bhd"] == D("1.219"), t
    assert t["total_bhd"] == D("26.281") == t["items_bhd"] - t["cart_discount_bhd"] + t["delivery_bhd"]
    assert t["subtotal_bhd"] == D("26.500") and t["units"] == 12 and t["items"] == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 2. confirm: the one editor
# ═══════════════════════════════════════════════════════════════════════════════

@test("confirm as ordered: every line confirmed at its ordered price, total to the fils, ETA date, one event")
def _():
    from app.shop import confirm_order
    fake = _one("new")
    with _patched(fake):
        out = confirm_order(1, [], "Tomorrow", "See you tomorrow", actor=REP)
    row = fake.rows("shop_orders")[0]
    assert row["status"] == "confirmed" and row["confirmed_at"] and row["total_confirmed_bhd"] == 37.5
    assert row["subtotal_confirmed_bhd"] == 37.5 and row["expected_delivery"] == "Tomorrow"
    from app.shop import bahrain_today
    assert row["expected_delivery_date"] == (bahrain_today() + timedelta(days=1)).isoformat()
    by = _lines_by(fake)
    assert [(by[i]["qty_confirmed"], by[i]["unit_price_confirmed"], by[i]["line_total_confirmed"]) for i in (11, 12)] \
        == [(10, 2.95, 29.5), (4, 2.0, 8.0)]
    assert all(ln["line_status"] == "ok" and ln["qty"] in (10, 4) for ln in by.values()), "requested never changes"
    ev = _events(fake, 1)
    assert [e["event"] for e in ev] == ["status:confirmed"] and ev[0]["actor"] == REP
    d = ev[0]["detail"]
    assert d["lines"] == [] and d["added"] == [] and d["shop_agreed"] is None and d["adverse"] == []
    assert d["total_before"] == 37.5 == d["total_after"] and d["note"] == "See you tomorrow"
    assert out["totals"]["total_bhd"] == 37.5 and out["changed"] == [] and out["removed"] == []


@test("confirm: every changed line needs a reason chip ('other' needs a note) — refused before anything is written")
def _():
    from app.shop import confirm_order
    fake = _one("new")
    with _patched(fake):
        _raises(lambda: confirm_order(1, [{"line_id": 11, "qty_confirmed": 7}], None, None, actor=REP),
                "Pick a reason for T02")
        _raises(lambda: confirm_order(1, [{"line_id": 11, "qty_confirmed": 7, "reason": "bogus"}], None, None, actor=REP),
                "Pick a reason for T02")
        _raises(lambda: confirm_order(1, [{"line_id": 11, "qty_confirmed": 7, "reason": "other"}], None, None, actor=REP),
                "note for T02")
        _raises(lambda: confirm_order(1, [{"line_id": 99, "qty_confirmed": 7, "reason": "price"}], None, None, actor=REP),
                "Unknown order line")
        _raises(lambda: confirm_order(1, [{"line_id": 11, "qty_confirmed": 7, "reason": "price"},
                                          {"line_id": 11, "qty_confirmed": 6, "reason": "price"}], None, None, actor=REP),
                "listed twice")
        assert fake.writes() == [], "a refused confirm writes nothing"
        # a line sent unchanged needs no reason
        out = confirm_order(1, [{"line_id": 12, "qty_confirmed": 4}, {"line_id": 11, "qty_confirmed": 7,
                                                                       "reason": "other", "note": "shelf count"}],
                            None, None, actor=REP)
    by = _lines_by(fake)
    assert by[11]["qty_confirmed"] == 7 and by[11]["line_status"] == "changed" and by[11]["change_reason"] == "other"
    assert by[11]["note"] == "shelf count" and by[11]["qty"] == 10
    assert by[12]["line_status"] == "ok" and by[12].get("change_reason") is None
    assert out["changed"] == [{"item_code": "T02", "from": 10, "to": 7}]
    assert fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 28.65      # 7 × 2.950 + 8.000


@test("confirm: 0 (or legacy 'removed') = unavailable, confirmed money 0; every line out is refused — cancel instead")
def _():
    from app.shop import confirm_order
    fake = _one("new")
    with _patched(fake):
        _raises(lambda: confirm_order(1, [{"line_id": 11, "qty_confirmed": 0, "reason": "out_of_stock"},
                                          {"line_id": 12, "line_status": "removed", "reason": "out_of_stock"}],
                                      None, None, actor=REP), "cancel the order instead")
        assert fake.writes() == []
        out = confirm_order(1, [{"line_id": 12, "line_status": "removed", "reason": "discontinued"}], None, None, actor=REP)
    by = _lines_by(fake)
    assert by[12]["line_status"] == "unavailable" and by[12]["qty_confirmed"] == 0
    assert by[12]["line_total_confirmed"] == 0.0 and by[12]["change_reason"] == "discontinued"
    assert out["removed"] == ["X05"] and fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 29.5
    ev = _events(fake, 1, "status:confirmed")[0]["detail"]["lines"]
    assert ev == [{"line_id": 12, "item_code": "X05", "before": {"qty_confirmed": 4, "line_status": "ok"},
                   "after": {"qty_confirmed": 0, "line_status": "unavailable"}, "requested": 4,
                   "reason": "discontinued", "note": None, "substitute_item_code": None, "unit_price_bhd": 2.0}]


@test("price lock: a price-book change after placement never reaches the order; a cut below a tier keeps the tier price")
def _():
    from app.shop import confirm_order
    # placed at a 10 %-off tier (2.655 at 10+); since then the book went to 3.500
    tier = [_line(11, 1, "T02", 10, 2.655, lp=2.95, rule_ids=[5]), _line(12, 1, "X05", 4, 2.0)]
    fake = _db(shop_orders=[_order(1, "new", subtotal_bhd=37.5, discount_bhd=2.95, total_bhd=34.55)],
               shop_order_lines=tier)
    items = [_item("T02", 3.5), _item("X05", 2.4)]
    rules = [{"id": 5, "name": "10 off at 10", "kind": "qty_tier", "min_qty": 10, "pct_off": 10,
              "scope": {"item_codes": ["T02"], "categories": [], "referral_codes": []}, "stackable": False}]
    with _patched(fake, _ctx(items, rules=rules)):
        confirm_order(1, [{"line_id": 11, "qty_confirmed": 6, "reason": "out_of_stock"}], None, None, actor=REP)
    by = _lines_by(fake)
    assert by[11]["unit_price_confirmed"] == 2.655 and by[11]["line_total_confirmed"] == 15.93, by[11]
    assert by[12]["unit_price_confirmed"] == 2.0, "never today's 2.400"
    row = fake.rows("shop_orders")[0]
    assert row["total_confirmed_bhd"] == 23.93 and row["subtotal_confirmed_bhd"] == 25.7, row
    assert _events(fake, 1, "status:confirmed")[0]["detail"]["adverse"] == [], "a stock cut is never adverse"


@test("no silent zero: an item since unlisted / unpriced keeps its ordered price; an added item the book can't price is refused")
def _():
    from app.shop import confirm_order
    fake = _one("new")
    items = [_item("X05", None)]                     # T02 left the catalog, X05 became 'price on request'
    with _patched(fake, _ctx(items)):
        confirm_order(1, [{"line_id": 11, "qty_confirmed": 9, "reason": "out_of_stock"}], None, None, actor=REP)
    by = _lines_by(fake)
    assert by[11]["line_total_confirmed"] == 26.55 and by[12]["line_total_confirmed"] == 8.0
    assert fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 34.55
    for code, want in (("NOPE", "No longer in the catalog"), ("POR", "Price on request")):
        fake = _one("new")
        with _patched(fake):
            _raises(lambda c=code: confirm_order(1, [], None, None, actor=REP,
                                                 added_lines=[{"item_code": c, "qty": 2}]), want)
            assert fake.writes() == [], "nothing is written for a refused add"
    bare = {**_line(11, 1, "T02", 2, 1.0), "unit_price_bhd": None, "list_price_bhd": None}
    fake = _db(shop_orders=[_order(1, "new")], shop_order_lines=[bare])
    with _patched(fake):
        _raises(lambda: confirm_order(1, [], None, None, actor=REP), "has no price on this order")


@test("adverse: a substitute at a different price needs 'Shop agreed' — refused without (nothing written), recorded with it")
def _():
    from app.shop import confirm_order
    from app.shop_heart import ADVERSE_MSG
    change = [{"line_id": 12, "substitute_item_code": "UK21", "reason": "out_of_stock"}]
    fake = _one("new")
    with _patched(fake):
        e = _raises(lambda: confirm_order(1, change, None, None, actor=REP), "Shop agreed")
        assert str(e) == ADVERSE_MSG
        assert fake.writes() == []
        _raises(lambda: confirm_order(1, change, None, None, actor=REP, shop_agreed={"via": "email"}), "WhatsApp, phone or visit")
        out = confirm_order(1, change, None, None, actor=REP, shop_agreed={"via": "whatsapp"})
    by = _lines_by(fake)
    assert by[12]["line_status"] == "substituted" and by[12]["qty_confirmed"] == 0 and by[12]["qty"] == 4
    assert by[12]["substitute_item_code"] == "UK21" and by[12]["change_reason"] == "out_of_stock"
    new = [ln for ln in by.values() if ln.get("substitute_for_line") == 12]
    assert len(new) == 1
    n = new[0]
    assert (n["item_code"], n["qty"], n["qty_confirmed"], n["line_status"], n["added_at_stage"], n["change_reason"]) \
        == ("UK21", 4, 4, "added", "confirm", "substituted")
    assert n["unit_price_bhd"] == 5.25 and n["line_total_confirmed"] == 21.0 and n["unit_cost_bhd"] == 2.6
    assert n["cost_source"] == "mrn"
    row = fake.rows("shop_orders")[0]
    assert row["total_confirmed_bhd"] == 50.5 and row["total_bhd"] == 37.5, "the total as ordered never moves"
    d = _events(fake, 1, "status:confirmed")[0]["detail"]
    assert d["adverse"] == ["substitute_price"] and d["shop_agreed"]["via"] == "whatsapp"
    assert d["shop_agreed"]["by"] == REP and d["shop_agreed"]["for"] == ["substitute_price"]
    assert d["added"][0]["substitute_for_line"] == 12 and out["adverse"][0]["from"] == 2.0 and out["adverse"][0]["to"] == 5.25
    # the same price is not adverse: no tick needed
    fake = _db(shop_orders=[_order(1, "new", total_bhd=47.5, subtotal_bhd=47.5)],
               shop_order_lines=[_line(11, 1, "T02", 10, 2.95), _line(12, 1, "UK20", 4, 4.5)])
    with _patched(fake):
        confirm_order(1, [{"line_id": 12, "substitute_item_code": "UK20N"}], None, None, actor=REP)
    assert _events(fake, 1, "status:confirmed")[0]["detail"]["adverse"] == []
    assert _lines_by(fake)[12]["change_reason"] == "substituted", "a substitute's default reason is 'substituted'"


@test("adverse: a backorder (marked, or an added sold-out item) and a cut below the BHD 20 minimum need the tick")
def _():
    from app.shop import confirm_order
    cases = [
        ([{"line_id": 12, "line_status": "backorder", "reason": "out_of_stock"}], None, "backorder"),
        ([], [{"item_code": "SOLD0", "qty": 2}], "backorder"),
        ([{"line_id": 11, "qty_confirmed": 3, "reason": "out_of_stock"}], None, "below_minimum"),   # 8.85 + 8 = 16.85
    ]
    for changes, added, kind in cases:
        fake = _one("new")
        with _patched(fake):
            _raises(lambda c=changes, a=added: confirm_order(1, c, None, None, actor=REP, added_lines=a), "Shop agreed")
            assert fake.writes() == [], kind
            confirm_order(1, changes, None, None, actor=REP, added_lines=added, shop_agreed={"via": "phone"})
        d = _events(fake, 1, "status:confirmed")[0]["detail"]
        assert d["adverse"] == [kind] and d["shop_agreed"]["via"] == "phone", (kind, d["adverse"])
    by = _lines_by(fake)
    assert by[11]["qty_confirmed"] == 3 and fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 16.85


@test("not adverse: a plain stock cut above the minimum, a cut of an order already under it, a line added — no tick")
def _():
    from app.shop import confirm_order
    fake = _one("new")
    with _patched(fake):
        confirm_order(1, [{"line_id": 11, "qty_confirmed": 5, "reason": "out_of_stock"}], None, None, actor=REP)
    assert fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 22.75
    small = [_line(11, 1, "T02", 4, 2.95), _line(12, 1, "X05", 2, 2.0)]              # 15.80, placed as a small order
    fake = _db(shop_orders=[_order(1, "new", total_bhd=15.8, subtotal_bhd=15.8, order_kind="small")],
               shop_order_lines=small)
    with _patched(fake):
        confirm_order(1, [{"line_id": 11, "qty_confirmed": 2, "reason": "out_of_stock"}], None, None, actor=REP)
    assert fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 9.9
    fake = _one("new")
    with _patched(fake):
        out = confirm_order(1, [], None, None, actor=REP, added_lines=[{"item_code": "c18", "qty": 6}])
    add = [ln for ln in fake.rows("shop_order_lines") if ln.get("added_at_stage")][0]
    assert (add["item_code"], add["qty"], add["qty_confirmed"], add["line_status"], add["change_reason"]) \
        == ("C18", 6, 6, "added", "customer_changed")
    assert add["unit_price_bhd"] == 1.25 and add["unit_cost_bhd"] == 0.5 and add["substitute_for_line"] is None
    assert fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 45.0 and out["adverse"] == []
    assert fake.rows("shop_orders")[0]["total_bhd"] == 37.5


@test("confirm only from Received: a confirmed order points to Amend, a delivered one is refused")
def _():
    from app.shop import confirm_order
    for status, want in (("confirmed", "use Amend"), ("packed", "use Amend"), ("delivered", "Cannot confirm"),
                         ("cancelled", "Cannot confirm")):
        fake = _one(status)
        with _patched(fake):
            _raises(lambda: confirm_order(1, [], None, None, actor=REP), want)
        assert fake.writes() == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. amend: the same editor after confirming
# ═══════════════════════════════════════════════════════════════════════════════

@test("amend: from Confirmed / Preparing / On the way only; status unchanged; 'amended' event with per-line before/after")
def _():
    from app.shop import amend_order, public_order_view, get_order
    for status, want in (("new", "Confirm the order first"), ("delivered", "reopen it first"),
                         ("cancelled", "reopen it first")):
        fake = _one(status)
        with _patched(fake):
            _raises(lambda: amend_order(1, [], None, None, actor=REP), want)
        assert fake.writes() == []
    for status in ("confirmed", "packed", "out_for_delivery"):
        fake = _one(status)
        with _patched(fake):
            out = amend_order(1, [{"line_id": 11, "qty_confirmed": 6, "reason": "damaged"}], None,
                              "Six left after the count", actor=REP,
                              added_lines=[{"item_code": "C18", "qty": 2, "reason": "customer_changed"}])
            pub = public_order_view(get_order(1))
        row = fake.rows("shop_orders")[0]
        assert row["status"] == status and out["status"] == status
        assert row["total_confirmed_bhd"] == 28.2, row["total_confirmed_bhd"]            # 17.70 + 8.00 + 2.50
        ev = _events(fake, 1, "amended")
        assert len(ev) == 1 and ev[0]["detail"]["from"] == status and ev[0]["detail"]["stage"] == "amend"
        ln = ev[0]["detail"]["lines"][0]
        assert ln["before"] == {"qty_confirmed": 10, "line_status": "ok"} and ln["after"] == {"qty_confirmed": 6,
                                                                                              "line_status": "changed"}
        assert ev[0]["detail"]["total_before"] == 37.5 and ev[0]["detail"]["total_after"] == 28.2
        add = [x for x in fake.rows("shop_order_lines") if x.get("added_at_stage")]
        assert add[0]["added_at_stage"] == "amend"
        assert [t["event"] for t in pub["timeline"]] == ["amended"] and pub["timeline"][0]["note"] == "Six left after the count"
        assert pub["status_label"] == "Confirmed"


@test("amend: restoring a line needs a reason; a second amend compares with the confirmed state, not the request")
def _():
    from app.shop import amend_order
    fake = _one("confirmed")
    with _patched(fake):
        amend_order(1, [{"line_id": 11, "qty_confirmed": 0, "reason": "out_of_stock"}], None, None, actor=REP,
                    shop_agreed={"via": "phone"})                   # 8.000 left: under the minimum
        assert _lines_by(fake)[11]["line_status"] == "unavailable"
        _raises(lambda: amend_order(1, [{"line_id": 11, "qty_confirmed": 10}], None, None, actor=REP), "reason for T02")
        amend_order(1, [{"line_id": 11, "qty_confirmed": 10, "reason": "customer_changed"},
                        {"line_id": 12, "qty_confirmed": 4}], "Thursday", None, actor=REP)
    by = _lines_by(fake)
    assert by[11]["line_status"] == "ok" and by[11]["qty_confirmed"] == 10 and by[11]["line_total_confirmed"] == 29.5
    row = fake.rows("shop_orders")[0]
    assert row["total_confirmed_bhd"] == 37.5 and row["expected_delivery"] == "Thursday"
    evs = _events(fake, 1, "amended")
    assert [len(e["detail"]["lines"]) for e in evs] == [1, 1], "X05 sent unchanged is not a change"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. deliver: one tap, or with changes
# ═══════════════════════════════════════════════════════════════════════════════

@test("deliver in one tap: qty_delivered = qty_confirmed on every line (0 for an unavailable one); the money stays")
def _():
    from app.shop import deliver_with_changes, set_status
    lines = [_line(11, 1, "T02", 10, 2.95, qty_confirmed=6, unit_price_confirmed=2.95, line_total_confirmed=17.7,
                   line_status="changed"),
             _line(12, 1, "X05", 4, 2.0, qty_confirmed=0, unit_price_confirmed=2.0, line_total_confirmed=0.0,
                   line_status="unavailable")]
    fake = _db(shop_orders=[_order(1, "confirmed", total_confirmed_bhd=17.7)], shop_order_lines=lines)
    with _patched(fake):
        out = deliver_with_changes(1, actor=REP)
    by = _lines_by(fake)
    assert out["status"] == "delivered" and by[11]["qty_delivered"] == 6 and by[12]["qty_delivered"] == 0
    assert fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 17.7
    assert [e["event"] for e in _events(fake, 1)] == ["status:delivered"]
    # the status route's Delivered is the same one tap
    fake = _one("packed")
    with _patched(fake):
        set_status(1, "delivered", None, actor=REP)
    assert {ln["qty_delivered"] for ln in fake.rows("shop_order_lines")} == {10, 4}


@test("deliver with changes: a shortfall needs a reason; lines added at the shop; the delivered value becomes the total")
def _():
    from app.shop import deliver_with_changes, get_order, public_order_view
    fake = _one("confirmed")
    with _patched(fake):
        _raises(lambda: deliver_with_changes(1, [{"line_id": 11, "qty_delivered": 8}], actor=REP), "reason for T02")
        _raises(lambda: deliver_with_changes(1, [{"line_id": 11, "qty_delivered": 0}, {"line_id": 12, "qty_delivered": 0,
                                                                                         "reason": "damaged"}],
                                             [], actor=REP, ), "reason for T02")
        assert fake.writes() == []
        out = deliver_with_changes(1, [{"line_id": 11, "qty_delivered": 8, "reason": "damaged", "note": "2 cracked"}],
                                   [{"item_code": "C18", "qty": 4}], actor=REP, note="left with the owner")
        pub = public_order_view(get_order(1))
    row = fake.rows("shop_orders")[0]
    by = _lines_by(fake)
    assert out["status"] == "delivered" and row["delivered_at"]
    assert by[11]["qty_delivered"] == 8 and by[11]["qty_confirmed"] == 10 and by[11]["qty"] == 10
    assert by[11]["change_reason"] == "damaged" and by[11]["note"] == "2 cracked" and by[12]["qty_delivered"] == 4
    add = [x for x in by.values() if x.get("added_at_stage") == "delivery"][0]
    assert (add["qty"], add["qty_confirmed"], add["qty_delivered"], add["line_total_bhd"]) == (4, 4, 4, 5.0)
    assert row["total_confirmed_bhd"] == 36.6 and row["total_bhd"] == 37.5            # 23.60 + 8.00 + 5.00
    d = _events(fake, 1, "status:delivered")[0]["detail"]
    assert d["with_changes"] and d["delivered"][0]["qty_delivered"] == 8 and d["added"][0]["item_code"] == "C18"
    assert d["total_before"] == 37.5 and d["total_after"] == 36.6 and d["note"] == "left with the owner"
    t02 = next(x for x in pub["lines"] if x["item_code"] == "T02")
    assert (t02["qty"], t02["qty_confirmed"], t02["qty_delivered"], t02["reason_label"]) == (10, 10, 8, "Damaged in stock")
    assert t02["line_total_delivered"] == 23.6 and "note" not in t02 and pub["has_changes"]
    assert pub["total_effective_bhd"] == 36.6 and pub["total_bhd"] == 37.5


@test("deliver: under the minimum needs the tick; from Received it is refused; before the migration only the one tap")
def _():
    from app.shop import deliver_with_changes
    fake = _one("confirmed")
    with _patched(fake):
        cut = [{"line_id": 11, "qty_delivered": 2, "reason": "customer_changed"}]      # 5.90 + 8 = 13.90 < 20
        _raises(lambda: deliver_with_changes(1, cut, actor=REP), "Shop agreed")
        assert fake.writes() == []
        deliver_with_changes(1, cut, shop_agreed={"via": "visit"}, actor=REP)
    d = _events(fake, 1, "status:delivered")[0]["detail"]
    assert d["adverse"] == ["below_minimum"] and d["shop_agreed"]["via"] == "visit"
    fake = _one("new")
    with _patched(fake):
        _raises(lambda: deliver_with_changes(1, actor=REP), "Confirm the order first")
    fake = _one("confirmed", pre_r7c=True)
    with _patched(fake):
        _raises(lambda: deliver_with_changes(1, [{"line_id": 11, "qty_delivered": 8, "reason": "damaged"}], actor=REP),
                "r7c_order_lines_qty_migration")
        # the list the one-tap sends (every line as confirmed) is the one tap, migration or not
        out = deliver_with_changes(1, [{"line_id": 11, "qty_delivered": 10}, {"line_id": 12, "qty_delivered": 4}],
                                   actor=REP)
    assert out["status"] == "delivered" and all("qty_delivered" not in ln for ln in fake.rows("shop_order_lines"))


# ═══════════════════════════════════════════════════════════════════════════════
# 5. compare-and-swap: races lose and write nothing; failures put everything back
# ═══════════════════════════════════════════════════════════════════════════════

@test("cas: confirm / amend / deliver lose to a status move or an updated_at change — 409, nothing written")
def _():
    from app.shop import CAS_CONFLICT_MSG, amend_order, confirm_order, deliver_with_changes
    cases = [
        ("new", lambda: confirm_order(1, [{"line_id": 11, "qty_confirmed": 5, "reason": "out_of_stock"}], None, None,
                                      actor=REP), {"status": "cancelled"}),
        ("new", lambda: confirm_order(1, [], None, None, actor=REP), {"updated_at": NOW.isoformat()}),   # an assignment
        ("confirmed", lambda: amend_order(1, [{"line_id": 11, "qty_confirmed": 5, "reason": "out_of_stock"}], None,
                                          None, actor=REP), {"updated_at": NOW.isoformat()}),
        ("confirmed", lambda: deliver_with_changes(1, [{"line_id": 11, "qty_delivered": 9, "reason": "damaged"}],
                                                   actor=REP), {"status": "cancelled"}),
        ("packed", lambda: deliver_with_changes(1, actor=REP), {"updated_at": NOW.isoformat()}),
    ]
    for status, call, race in cases:
        fake = _one(status)
        before = dict(fake.rows("shop_orders")[0])
        lines_before = [dict(x) for x in fake.rows("shop_order_lines")]
        fake.after_select["shop_orders"] = lambda n, f=fake, r=race: f.rows("shop_orders")[0].update(r) if n == 1 else None
        with _patched(fake):
            _raises(call, CAS_CONFLICT_MSG)
        assert fake.writes("shop_order_lines") == [] and fake.writes("shop_order_events") == [], (status, race)
        assert fake.rows("shop_orders")[0] == {**before, **race}, "only the racing writer's change is on the row"
        assert fake.rows("shop_order_lines") == lines_before


def _norm(rows) -> list[dict]:
    """Rows compared on their values: a key a revert wrote back as NULL equals a key never written."""
    return [{k: v for k, v in r.items() if v is not None} for r in rows]


@test("cas: a failure after the header swap puts the header and every line back and removes the added rows; the retry works")
def _():
    from app.shop import amend_order, confirm_order, deliver_with_changes
    change = [{"line_id": 11, "qty_confirmed": 5, "reason": "out_of_stock"}]
    for where in (("insert", "shop_order_events"), ("insert", "shop_order_lines")):
        fake = _one("new")
        before_lines = [dict(x) for x in fake.rows("shop_order_lines")]
        before_row = dict(fake.rows("shop_orders")[0])
        fake.fail[where] = RuntimeError("blip")
        with _patched(fake):
            try:
                confirm_order(1, change, "Today", None, actor=REP, added_lines=[{"item_code": "C18", "qty": 2}])
                raise AssertionError("expected the failure to propagate")
            except RuntimeError as e:
                assert "blip" in str(e)
            assert _norm([fake.rows("shop_orders")[0]]) == _norm([before_row]), where
            assert _norm(fake.rows("shop_order_lines")) == _norm(before_lines), where
            assert _events(fake, 1) == []
            fake.fail.clear()
            confirm_order(1, change, "Today", None, actor=REP, added_lines=[{"item_code": "C18", "qty": 2}])
        assert fake.rows("shop_orders")[0]["status"] == "confirmed" and len(fake.rows("shop_order_lines")) == 3
    # amend and deliver: the same promise
    fake = _one("confirmed")
    snap = [dict(x) for x in fake.rows("shop_order_lines")], dict(fake.rows("shop_orders")[0])
    fake.fail[("insert", "shop_order_events")] = RuntimeError("blip")
    with _patched(fake):
        for call in (lambda: amend_order(1, change, None, None, actor=REP),
                     lambda: deliver_with_changes(1, [{"line_id": 11, "qty_delivered": 9, "reason": "damaged"}],
                                                  [{"item_code": "C18", "qty": 1}], actor=REP)):
            try:
                call()
                raise AssertionError("expected the failure to propagate")
            except RuntimeError:
                pass
            assert _norm(fake.rows("shop_order_lines")) == _norm(snap[0])
            assert _norm([fake.rows("shop_orders")[0]]) == _norm([snap[1]])


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the routes
# ═══════════════════════════════════════════════════════════════════════════════

@test("status route: 'confirmed' → use /confirm; Preparing / On the way are the storekeeper's alone; Delivered and Cancel stay")
def _():
    from app.shop_heart import STAMP_ONLY_MSG, USE_CONFIRM_MSG
    try:
        with _quiet_notify() as sent:
            fake = _db(shop_orders=[_order(1, "new"), _order(2, "confirmed"), _order(3, "confirmed")],
                       shop_order_lines=_lines(1) + _lines(2, True) + _lines(3, True))
            with _patched(fake):
                c = _client(ADMIN, "admin")
                r = c.post("/shop/orders/1/status", json={"status": "confirmed"})
                assert r.status_code == 400 and r.json()["detail"] == USE_CONFIRM_MSG
                for st in ("packed", "out_for_delivery"):
                    r = c.post("/shop/orders/2/status", json={"status": st})
                    assert r.status_code == 400 and r.json()["detail"] == STAMP_ONLY_MSG, st
                c = _client(REP, "salesman")
                r = c.post("/shop/orders/2/status", json={"status": "packed"})
                assert r.status_code == 400
                r = c.post("/shop/orders/2/status", json={"status": "delivered"})
                assert r.status_code == 200, r.text[:200]
                o = r.json()["order"]
                assert o["status"] == "delivered" and o["next_statuses"] == [] and r.json()["next_action"] == "tell_shop"
                assert all(ln["qty_delivered"] == ln["qty_confirmed"] for ln in o["lines"])
                c = _client(STORE, "storekeeper")
                r = c.post("/shop/orders/3/status", json={"status": "packed"})
                assert r.status_code == 200 and r.json()["order"]["status_label"] == "Confirmed"
                assert r.json()["order"]["stamp_label"] == "Preparing"
                assert r.json()["order"]["next_statuses"] == ["out_for_delivery"]
                assert fake.rows("shop_orders")[0]["status"] == "new"
            assert [s[1] for s in sent] == ["delivered"], "the storekeeper's stamp never emails the shop"
    finally:
        _drop_client()


@test("routes: confirm → amend → deliver → customer-notified over HTTP; adverse 400; payloads carry the three numbers")
def _():
    from app.shop_heart import ADVERSE_MSG
    try:
        with _quiet_notify():
            fake = _one("new")
            with _patched(fake):
                c = _client(REP, "salesman")
                r = c.get("/shop/orders/1")
                assert r.status_code == 200
                j = r.json()
                assert j["actions"] == ["confirm", "cancel", "tell_shop"] and j["next_statuses"] == ["confirmed", "cancelled"]
                assert j["change_reasons"]["out_of_stock"] == "Out of stock" and j["agreed_via"] == ["whatsapp", "phone", "visit"]
                assert j["min_order_bhd"] == 20.0 and j["status_label"] == "Received" and j["shop_told"] is False
                assert j["lines"][0]["qty_confirmed"] is None and j["total_effective_bhd"] == 37.5
                body = {"lines": [{"line_id": 11, "qty_confirmed": 3, "reason": "out_of_stock"}],
                        "expected_delivery": "Tomorrow"}
                r = c.post("/shop/orders/1/confirm", json=body)
                assert r.status_code == 400 and r.json()["detail"] == ADVERSE_MSG
                r = c.post("/shop/orders/1/confirm", json={**body, "shop_agreed": {"via": "whatsapp"}})
                assert r.status_code == 200, r.text[:300]
                j = r.json()
                assert j["order"]["status"] == "confirmed" and j["adverse"][0]["kind"] == "below_minimum"
                assert j["shop_agreed"]["via"] == "whatsapp" and j["next_action"] == "tell_shop"
                assert j["whatsapp_url"].startswith("https://wa.me/97333001122") and "10%20-%3E%203" in j["whatsapp_url"]
                ln = next(x for x in j["order"]["lines"] if x["id"] == 11)
                assert (ln["qty"], ln["qty_confirmed"], ln["qty_unavailable"], ln["disposition"], ln["reason_label"]) \
                    == (10, 3, 7, "reduced", "Out of stock")
                assert j["order"]["actions"] == ["deliver", "amend", "cancel", "tell_shop"]
                r = c.post("/shop/orders/1/customer-notified", json={"channel": "whatsapp"})
                assert r.status_code == 200 and r.json()["ok"] and r.json()["logged"] is True
                assert c.get("/shop/orders/1").json()["shop_told"] is True
                r = c.post("/shop/orders/1/amend", json={"lines": [{"line_id": 11, "qty_confirmed": 5,
                                                                    "reason": "customer_changed"}]})
                assert r.status_code == 200, r.text[:300]
                assert r.json()["order"]["shop_told"] is False, "told before the amendment is not told"
                assert r.json()["order"]["total_confirmed_bhd"] == 22.75
                r = c.post("/shop/orders/1/deliver")
                assert r.status_code == 200 and r.json()["order"]["status"] == "delivered"
                assert r.json()["order"]["actions"] == ["tell_shop"]
            n = [x for x in fake.rows("shop_notifications")]
            assert len(n) == 1 and n[0]["kind"] == "status_update" and n[0]["recipient_role"] == "merchant"
            assert n[0]["provider"] == "rep_tap" and n[0]["status"] == "sent" and n[0]["channel"] == "whatsapp"
            ev = [e["event"] for e in _events(fake, 1)]
            assert ev == ["status:confirmed", "customer_notified", "amended", "status:delivered"], ev
    finally:
        _drop_client()


@test("routes: amend / deliver / customer-notified — management and the storekeeper 403, another rep's order 404")
def _():
    try:
        fake = _db(shop_orders=[_order(1, "confirmed"), _order(2, "confirmed", salesman_id=2, salesman_name="Rep Two")],
                   shop_order_lines=_lines(1, True) + _lines(2, True))
        with _quiet_notify(), _patched(fake):
            calls = (("/shop/orders/1/amend", {"lines": []}), ("/shop/orders/1/deliver", {}),
                     ("/shop/orders/1/customer-notified", {"channel": "phone"}))
            for email, role in ((MGMT, "management"), (STORE, "storekeeper")):
                c = _client(email, role)
                for path, body in calls:
                    r = c.post(path, json=body)
                    assert r.status_code == 403, (role, path, r.status_code, r.text[:120])
            c = _client(REP, "salesman")
            for path, body in calls:
                assert c.post(path.replace("/1/", "/2/"), json=body).status_code == 404, path
            r = c.post("/shop/orders/1/customer-notified", json={"channel": "fax"})
            assert r.status_code == 400
        assert fake.writes() == [] or all(w[1] in ("audit_log",) for w in fake.writes())
    finally:
        _drop_client()


@test("gates: the new routes carry their dependency (Shop Orders; reopen admin-only)")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    from app import auth
    table = {}
    for r in m.app.routes:
        if isinstance(r, APIRoute):
            for meth in r.methods:
                table[(meth, r.path)] = [d.call for d in r.dependant.dependencies]
    for path in ("/shop/orders/{order_id}/amend", "/shop/orders/{order_id}/deliver",
                 "/shop/orders/{order_id}/customer-notified", "/shop/orders/{order_id}/confirm"):
        deps = table[("POST", path)]
        assert any(getattr(d, "feature", None) == "Shop Orders" for d in deps), path
    assert auth.require_admin in table[("POST", "/shop/orders/{order_id}/reopen")]


# ═══════════════════════════════════════════════════════════════════════════════
# 7. rep-placed orders, reopen
# ═══════════════════════════════════════════════════════════════════════════════

def _body(**over):
    b = {"lines": [{"item_code": "T02", "qty": 10}, {"item_code": "X05", "qty": 4}],
         "customer": {"name": "Test Shop", "phone": "33001122", "shop": "Test Shop", "area": "Manama"}}
    b.update(over)
    return b


@test("a rep-placed order is born Confirmed (prices, confirmed_at, one insert of created + status:confirmed) and never reminded")
def _():
    from app import shop, shop_jobs
    fake = _db()
    with _patched(fake):
        o = shop.create_order(_body(), staff_email=REP)
        m = shop.create_order(_body(device_id="dev-9", client_order_id="c-9", session_ref="rep-one"), market=True)
    row = next(r for r in fake.rows("shop_orders") if r["id"] == o["id"])
    assert row["status"] == "confirmed" and row["confirmed_at"] and row["source"] == "salesman"
    assert row["total_confirmed_bhd"] == row["total_bhd"] == 37.5 and row["subtotal_confirmed_bhd"] == 37.5
    lines = [ln for ln in fake.rows("shop_order_lines") if ln["order_id"] == o["id"]]
    assert all(ln["qty_confirmed"] == ln["qty"] and ln["unit_price_confirmed"] == ln["unit_price_bhd"] for ln in lines)
    t02 = next(ln for ln in lines if ln["item_code"] == "T02")
    assert t02["unit_cost_bhd"] == 1.1 and t02["cost_source"] == "mrn"
    ins = [c for c in fake.writes("shop_order_events") if c[0] == "insert" and c[2][0]["order_id"] == o["id"]]
    assert len(ins) == 1 and [x["event"] for x in ins[0][2]] == ["created", "status:confirmed"]
    assert ins[0][2][1]["actor"] == REP and ins[0][2][1]["detail"]["born_confirmed"] is True
    mrow = next(r for r in fake.rows("shop_orders") if r["id"] == m["id"])
    assert mrow["status"] == "new" and mrow.get("total_confirmed_bhd") is None, "a merchant's order waits for the rep"
    mlines = [ln for ln in fake.rows("shop_order_lines") if ln["order_id"] == m["id"]]
    assert all(ln.get("qty_confirmed") is None and ln["unit_cost_bhd"] is not None for ln in mlines)
    # the reminder job: Thursday 13:00 Bahrain, SLA long passed — the rep's own order is never chased
    staff_row = dict(row, status="new", assigned_at=(NOW - timedelta(days=1)).isoformat())   # even if it were Received
    keep, excluded = shop_jobs._not_chased([staff_row])
    assert keep == [] and excluded == {"staff": 1}
    thu = datetime(2026, 9, 24, 13, 0, tzinfo=timezone(timedelta(hours=3))).astimezone(timezone.utc)
    import app.shop_notify as sn
    sends: list = []
    with _patched(fake), _swap(shop_jobs, get_client=lambda: fake, _now=lambda: thu), \
            _swap(sn, _email=lambda *a, **k: sends.append(a) or {"sent": True},
                  _telegram=lambda *a, **k: sends.append(a) or {"sent": True},
                  _whatsapp_cloud=lambda *a, **k: sends.append(a) or {"sent": True}):
        for r in fake.rows("shop_orders"):
            r["created_at"] = (thu - timedelta(days=1)).isoformat()
        out = shop_jobs.unconfirmed_reminder()
    reminded = [x for x in out.get("reminded", [])]
    assert o["order_no"] not in str(reminded) and o["order_no"] not in str(sends)


@test("reopen: admin only, within 7 days, reason required — Delivered → Confirmed, Cancelled → Received; audited")
def _():
    from app.shop import CAS_CONFLICT_MSG, reopen_order
    from app.shop_heart import REOPEN_REASON_MSG, REOPEN_STATUS_MSG, REOPEN_WINDOW_MSG
    lines = [_line(11, 1, "T02", 10, 2.95, confirmed=True, qty_delivered=8, change_reason="damaged"),
             _line(12, 1, "X05", 4, 2.0, confirmed=True, qty_delivered=4)]
    fake = _db(shop_orders=[_order(1, "delivered", total_confirmed_bhd=31.6, focus_invoice_no="SI-T-1"),
                            _order(2, "cancelled"), _order(3, "confirmed"),
                            _order(4, "delivered", delivered_at=(NOW - timedelta(days=8)).isoformat())],
               shop_order_lines=lines)
    with _patched(fake):
        _raises(lambda: reopen_order(1, "", ADMIN), REOPEN_REASON_MSG)
        _raises(lambda: reopen_order(3, "wrong tap", ADMIN), REOPEN_STATUS_MSG)
        _raises(lambda: reopen_order(4, "wrong tap", ADMIN), REOPEN_WINDOW_MSG)
        assert fake.writes() == []
        o = reopen_order(1, "Rep tapped Delivered by mistake", ADMIN)
        c = reopen_order(2, "Shop still wants it", ADMIN)
    assert o["status"] == "confirmed" and o["delivered_at"] is None and o["reopened_by"] == ADMIN
    assert o["reopen_reason"] == "Rep tapped Delivered by mistake" and o["reopened_at"]
    assert o["focus_invoice_no"] == "SI-T-1", "the Focus link is untouched"
    assert o["total_confirmed_bhd"] == 37.5, "back to the confirmed state (the delivered value was 31.60)"
    assert all(ln["qty_delivered"] is None for ln in fake.rows("shop_order_lines"))
    assert c["status"] == "new" and c["cancelled_at"] is None and c["cancel_reason"] is None and c["cancel_reason_code"] is None
    ev = _events(fake, 1, "reopened")[0]
    assert ev["detail"]["from"] == "delivered" and ev["detail"]["to"] == "confirmed"
    assert ev["detail"]["before"]["total_confirmed_bhd"] == 31.6 and ev["detail"]["lines"][0]["qty_delivered"] == 8
    audit = fake.rows("shop_admin_audit")
    assert [(a["entity"], a["entity_id"], a["action"]) for a in audit] == [("order", "1", "reopen"), ("order", "2", "reopen")]
    assert audit[0]["before"]["status"] == "delivered" and audit[0]["after"]["status"] == "confirmed"
    # a lost race
    fake = _db(shop_orders=[_order(1, "delivered")], shop_order_lines=_lines(1, True))
    fake.after_select["shop_orders"] = lambda n: fake.rows("shop_orders")[0].update(updated_at=NOW.isoformat()) if n == 1 else None
    with _patched(fake):
        _raises(lambda: reopen_order(1, "wrong tap", ADMIN), CAS_CONFLICT_MSG)
    assert fake.writes("shop_order_events") == [] and fake.rows("shop_admin_audit") == []
    # the route: admin only, 404 / 400
    try:
        fake = _db(shop_orders=[_order(1, "delivered")], shop_order_lines=_lines(1, True))
        with _patched(fake):
            assert _client(REP, "salesman").post("/shop/orders/1/reopen", json={"reason": "x wrong"}).status_code == 403
            c = _client(ADMIN, "admin")
            assert c.post("/shop/orders/9/reopen", json={"reason": "wrong tap"}).status_code == 404
            assert c.post("/shop/orders/1/reopen", json={"reason": ""}).status_code == 400
            r = c.post("/shop/orders/1/reopen", json={"reason": "wrong tap"})
            assert r.status_code == 200 and r.json()["order"]["status"] == "confirmed"
            assert "deliver" in r.json()["order"]["actions"]
            assert any(a["event"] == "shop.order_reopen" for a in fake.rows("audit_log"))
    finally:
        _drop_client()


# ═══════════════════════════════════════════════════════════════════════════════
# 8. payloads: public, desk, one effective-money rule
# ═══════════════════════════════════════════════════════════════════════════════

@test("public view: three numbers per line, the public reason (never the note, never cost), totals, 3 steps, stamps hidden")
def _():
    from app.shop import amend_order, confirm_order, get_order, public_order_view, set_status
    fake = _one("new")
    with _patched(fake):
        confirm_order(1, [{"line_id": 11, "qty_confirmed": 6, "reason": "out_of_stock", "note": "INTERNAL do not show"},
                          {"line_id": 12, "substitute_item_code": "UK20N", "reason": "discontinued"}],
                      "Today", "Six today", actor=REP, shop_agreed={"via": "phone"})
        set_status(1, "packed", None, actor=STORE)
        amend_order(1, [], None, None, actor=REP, added_lines=[{"item_code": "C18", "qty": 2}])
        pub = public_order_view(get_order(1))
    assert pub["status"] == "packed" and pub["status_label"] == "Confirmed" and pub["visible_status"] == "confirmed"
    assert [s["label"] for s in pub["steps"]] == ["Received", "Confirmed", "Delivered"] and pub["steps"][1]["current"]
    assert [t["event"] for t in pub["timeline"]] == ["status:confirmed", "amended"], "the stamp stays internal"
    text = str(pub)
    assert "INTERNAL" not in text and "unit_cost" not in text and "cost_source" not in text and "'id'" not in text
    by = {ln["item_code"]: ln for ln in pub["lines"]}
    assert (by["T02"]["qty"], by["T02"]["qty_confirmed"], by["T02"]["qty_unavailable"], by["T02"]["reason_label"]) \
        == (10, 6, 4, "Out of stock")
    assert by["X05"]["disposition"] == "substituted" and by["X05"]["substitute_item_code"] == "UK20N"
    assert by["X05"]["reason_label"] == "No longer available" and by["X05"]["qty_confirmed"] == 0
    assert by["UK20N"]["substitute_for"] == "X05" and by["UK20N"]["disposition"] == "added"
    assert by["C18"]["added_at_stage"] == "amend" and by["C18"]["reason_label"] == "As you asked"
    # 6 × 2.95 + 4 × 4.50 + 2 × 1.25 = 17.70 + 18.00 + 2.50 = 38.20
    assert pub["total_bhd"] == 37.5 and pub["total_confirmed_bhd"] == 38.2 == pub["total_effective_bhd"]
    assert sum(D(ln["line_total_confirmed"]) for ln in pub["lines"]) == D("38.2")
    assert pub["has_changes"] and pub["expected_delivery_date"]


@test("one effective-money rule: my-orders, the WhatsApp message and a 'paid' record read confirmed ?? original")
def _():
    from app import shop, shop_notify, shop_pipeline
    from app.shop_heart import effective_money, order_total
    assert effective_money(None, 12.3456) == 12.346 and effective_money(0, 12.0) == 0.0
    assert order_total({"total_bhd": 37.5, "total_confirmed_bhd": 22.75}) == 22.75
    fake = _db(shop_orders=[_order(1, "confirmed", total_confirmed_bhd=22.75)], shop_order_lines=_lines(1, True))
    with _patched(fake):
        mine = shop.orders_by_tokens([_order(1)["token"]])
        assert mine[0]["total_bhd"] == 22.75 and mine[0]["status_label"] == "Confirmed"
        o = shop.get_order(1)
        url = shop_notify.salesman_to_customer_wa_url(o, "confirmed")
        assert "22.750" in url
        assert "prepared" not in shop_notify.salesman_to_customer_wa_url(dict(o, status="packed"))
        paid = shop_pipeline.set_payment(1, "paid", "cash", None, None, actor=ADMIN)
    assert _events(fake, 1, "payment")[0]["detail"]["amount_bhd"] == 22.75 and paid["payment_status"] == "paid"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. before the migration (and a column vanishing under a cached probe)
# ═══════════════════════════════════════════════════════════════════════════════

R7C_LINE = ("qty_delivered", "change_reason", "substitute_item_code", "substitute_for_line", "added_at_stage",
            "unit_cost_bhd", "cost_source")
R7C_ORDER = ("expected_delivery_date", "reopened_at", "reopened_by", "reopen_reason")


def _named(fake, cols) -> list:
    return [c for c in fake.writes() if any(k in (c[2] if isinstance(c[2], dict) else {}) for k in cols)
            or (isinstance(c[2], list) and any(k in x for x in c[2] for k in cols))]


@test("pre-migration: confirm / amend / deliver / cancel / reopen / create work; the new statuses map to the old; no R7c column is named")
def _():
    from app import shop
    fake = _one("new", pre_r7c=True)
    with _patched(fake):
        shop.confirm_order(1, [{"line_id": 12, "qty_confirmed": 0, "reason": "out_of_stock"},
                               {"line_id": 11, "qty_confirmed": 8, "reason": "damaged"}], "Tomorrow", None, actor=REP)
        by = _lines_by(fake)
        assert by[12]["line_status"] == "removed" and by[12]["qty_confirmed"] == 0, "the live check has no 'unavailable'"
        assert by[11]["line_status"] == "changed" and "change_reason" not in by[11]
        _raises(lambda: shop.amend_order(1, [], None, None, actor=REP, added_lines=[{"item_code": "C18", "qty": 1}]),
                "r7c_order_lines_qty_migration")
        _raises(lambda: shop.amend_order(1, [{"line_id": 11, "substitute_item_code": "UK21", "reason": "price"}], None,
                                         None, actor=REP), "r7c_order_lines_qty_migration")
        shop.amend_order(1, [{"line_id": 11, "qty_confirmed": 7, "reason": "damaged"}], None, None, actor=REP)
        pub = shop.public_order_view(shop.get_order(1))
        assert {ln["item_code"]: ln["reason_label"] for ln in pub["lines"]} == {"T02": "Damaged in stock",
                                                                                 "X05": "Out of stock"}, \
            "the reason still reaches the shop — from the event"
        shop.set_status(1, "delivered", None, actor=REP)
        shop.reopen_order(1, "wrong tap", ADMIN)
        shop.set_status(1, "cancelled", "total fell to 14", actor=REP, reason_code="below_minimum")
        o = shop.create_order(_body(), staff_email=REP)
    row = fake.rows("shop_orders")[0]
    assert row["status"] == "cancelled" and row["cancel_reason_code"] == "other", "the live check has no below_minimum"
    assert _events(fake, 1, "status:cancelled")[0]["detail"]["reason_code"] == "below_minimum"
    assert o["status"] == "confirmed"
    assert _named(fake, R7C_LINE + R7C_ORDER) == [], _named(fake, R7C_LINE + R7C_ORDER)[:2]
    assert fake.rows("shop_admin_audit")[0]["action"] == "reopen"
    # after the migration the code lands in the column
    fake = _one("confirmed")
    with _patched(fake):
        shop.set_status(1, "cancelled", None, actor=REP, reason_code="below_minimum")
    assert fake.rows("shop_orders")[0]["cancel_reason_code"] == "below_minimum"


@test("a column dropped under a cached probe (reverse script): the write retries without it and forgets the probe")
def _():
    import app.shop as s
    from app.shop import amend_order, has_column
    fake = _one("confirmed")
    with _patched(fake):
        assert has_column("shop_order_lines", "change_reason") and has_column("shop_order_lines", "unit_price_confirmed")
        fake.columns["shop_order_lines"] = set(LINE_COLS_PRE) - {"unit_price_confirmed", "line_total_confirmed"}
        amend_order(1, [{"line_id": 11, "qty_confirmed": 5, "reason": "damaged"}], None, None, actor=REP)
    assert _lines_by(fake)[11]["qty_confirmed"] == 5 and fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 22.75
    assert "shop_order_lines.change_reason" not in s._col_cache and "shop_order_lines.unit_price_confirmed" not in s._col_cache


# ═══════════════════════════════════════════════════════════════════════════════
# 10. the Decimal property test: Σ lines = total to the fils, confirm → amend → deliver
# ═══════════════════════════════════════════════════════════════════════════════

_Q = Decimal("0.001")


def _r(x: Decimal) -> Decimal:
    return x.quantize(_Q, rounding=ROUND_HALF_UP)


def _ref_total(order: dict, lines: list[dict], key: str) -> Decimal:
    """An independent pure-Decimal statement of the price lock (not app code): each line at its
    locked unit × the chosen quantity, the order's cart-level discount shared pro rata over the
    REQUESTED lines, delivery as ordered."""
    unit = {ln["id"]: D(ln["unit_price_confirmed"] if ln.get("unit_price_confirmed") is not None else ln["unit_price_bhd"])
            for ln in lines}
    orig = [ln for ln in lines if not ln.get("added_at_stage")]
    s0 = sum((D(ln["line_total_bhd"]) for ln in orig), Decimal(0))
    cart0 = max(Decimal(0), _r(D(order["discount_bhd"]) - sum((D(ln["discount_bhd"]) for ln in orig), Decimal(0))))
    q = {ln["id"]: int(ln.get(key) if ln.get(key) is not None else 0) for ln in lines}
    s1 = sum((_r(unit[ln["id"]] * q[ln["id"]]) for ln in orig), Decimal(0))
    items = sum((_r(unit[ln["id"]] * q[ln["id"]]) for ln in lines), Decimal(0))
    cart = min(_r(cart0 * s1 / s0), s1) if cart0 > 0 and s0 > 0 else Decimal(0)
    return _r(items - cart + D(order["delivery_bhd"]))


def _random_order(rng: random.Random, oid: int, with_cart: bool):
    lines, sub, disc = [], Decimal(0), Decimal(0)
    for k in range(rng.randint(1, 7)):
        lp = Decimal(rng.randint(50, 25000)) / 1000
        unit = lp if rng.random() < 0.6 else _r(lp * Decimal(rng.randint(80, 99)) / 100)
        qty = rng.randint(1, 60)
        ln = _line(oid * 100 + k, oid, f"R{k}", qty, float(unit), lp=float(lp))
        lines.append(ln)
        sub += _r(lp * qty)
        disc += _r((lp - unit) * qty)
    items = sum((D(ln["line_total_bhd"]) for ln in lines), Decimal(0))
    cart = _r(items * Decimal(rng.randint(1, 15)) / 100) if with_cart else Decimal(0)
    delivery = Decimal(rng.choice([0, 500, 1250])) / 1000 if with_cart else Decimal(0)
    total = _r(items - cart + delivery)
    o = _order(oid, "new", subtotal_bhd=float(sub), discount_bhd=float(disc + cart), delivery_bhd=float(delivery),
               total_bhd=float(total))
    return o, lines


def _check_money(fake, oid: int, key: str, plain: bool) -> None:
    row = next(r for r in fake.rows("shop_orders") if r["id"] == oid)
    lines = [ln for ln in fake.rows("shop_order_lines") if ln["order_id"] == oid]
    want = _ref_total(row, lines, key)
    got = D(row["total_confirmed_bhd"])
    assert got == want, (key, got, want)
    for ln in lines:
        for k in ("unit_price_confirmed", "line_total_confirmed"):
            v = D(ln[k])
            assert v == _r(v), (k, v)
        assert D(ln["line_total_confirmed"]) == _r(D(ln["unit_price_confirmed"]) * int(ln["qty_confirmed"]))
    if plain:          # no cart discount, no delivery: the stored lines add up to the total exactly
        col = "line_total_confirmed" if key == "qty_confirmed" else None
        if col:
            assert sum((D(ln[col]) for ln in lines), Decimal(0)) == got
        else:
            assert sum((_r(D(ln["unit_price_confirmed"]) * int(ln["qty_delivered"])) for ln in lines), Decimal(0)) == got


@test("property: 250 random orders through confirm → amend → deliver — every stored figure 3 dp, Σ lines = total to the fils")
def _():
    from app.shop import amend_order, confirm_order, deliver_with_changes
    rng = random.Random(20260927)
    pool = [_item(f"P{i}", float(Decimal(rng.randint(100, 9000)) / 1000)) for i in range(6)]
    for n in range(250):
        with_cart = n % 3 == 0
        o, lines = _random_order(rng, 1, with_cart)
        items = [_item(ln["item_code"], ln["list_price_bhd"]) for ln in lines] + pool
        fake = _db(shop_orders=[o], shop_order_lines=lines)
        agreed = {"via": "phone"}

        def edits(cur_lines, stage):
            ch = []
            for ln in cur_lines:
                if rng.random() < 0.35:
                    continue
                q = rng.randint(0, int(ln["qty"]) + 5)
                ch.append({"line_id": ln["id"], "qty_confirmed": q, "reason": rng.choice(["out_of_stock", "damaged",
                                                                                           "customer_changed"])})
            add = [{"item_code": rng.choice(pool)["item_code"], "qty": rng.randint(1, 9)}
                   for _ in range(rng.choice([0, 0, 1, 2]))]
            asked = {c["line_id"]: c["qty_confirmed"] for c in ch}
            live = [asked.get(ln["id"], ln["qty"] if ln.get("qty_confirmed") is None else ln["qty_confirmed"])
                    for ln in cur_lines]
            if not add and not any(q > 0 for q in live):          # never "every line removed"
                ch.append({"line_id": cur_lines[0]["id"], "qty_confirmed": 1, "reason": "customer_changed"})
                ch = [c for c in ch if c["line_id"] != cur_lines[0]["id"]] + [ch[-1]]
            return ch, add
        with _patched(fake, _ctx(items, min_order="0")):
            ch, add = edits(lines, "confirm")
            confirm_order(1, ch, None, None, actor=REP, added_lines=add, shop_agreed=agreed)
            _check_money(fake, 1, "qty_confirmed", plain=not with_cart)
            cur = [dict(x) for x in fake.rows("shop_order_lines")]
            ch, add = edits(cur, "amend")
            amend_order(1, ch, None, None, actor=REP, added_lines=add, shop_agreed=agreed)
            _check_money(fake, 1, "qty_confirmed", plain=not with_cart)
            cur = [dict(x) for x in fake.rows("shop_order_lines")]
            dl = [{"line_id": ln["id"], "qty_delivered": max(0, int(ln["qty_confirmed"]) - rng.randint(0, 2)),
                   "reason": "damaged"} for ln in cur if rng.random() < 0.5]
            if not any(int(x["qty_confirmed"]) for x in cur if x["id"] not in {d["line_id"] for d in dl}) \
                    and not any(d["qty_delivered"] for d in dl):
                dl = []
            deliver_with_changes(1, dl, [], shop_agreed=agreed, actor=REP)
            _check_money(fake, 1, "qty_delivered", plain=not with_cart)
        assert fake.rows("shop_orders")[0]["status"] == "delivered"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. the migration and its reverse, as text
# ═══════════════════════════════════════════════════════════════════════════════

def _code(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql).lower()


@test("migration: additive + idempotent (if not exists / drop-if-exists-then-add), checks widened not narrowed, revokes, self-check, no row writes")
def _():
    sql = MIGRATION.read_text(encoding="utf-8")
    low = _code(sql)
    for col, typ in (("qty_delivered", "integer"), ("change_reason", "text"), ("substitute_item_code", "text"),
                     ("substitute_for_line", "bigint"), ("added_at_stage", "text"), ("unit_cost_bhd", "numeric(12,4)"),
                     ("cost_source", "text")):
        assert re.search(rf"alter table shop_order_lines add column if not exists {col}\s+{re.escape(typ)};", low), col
    for col, typ in (("expected_delivery_date", "date"), ("reopened_at", "timestamptz"), ("reopened_by", "text"),
                     ("reopen_reason", "text")):
        assert re.search(rf"alter table shop_orders add column if not exists {col}\s+{typ};", low), col
    assert "not null" not in low.split("do $$")[0], "no NOT NULL anywhere in the DDL"
    assert not re.search(r"add column[^;]*(default|not null)", low), "every new column nullable, no default: no row rewritten"
    # every constraint added is dropped first (re-runnable)
    added = re.findall(r"add constraint (\w+)", low)
    for name in added:
        assert f"drop constraint if exists {name};" in low, name
    assert set(added) >= {"shop_order_lines_line_status_check", "shop_orders_cancel_reason_code_check",
                          "shop_order_lines_change_reason_check", "shop_order_lines_added_at_stage_check",
                          "shop_order_lines_qty_delivered_check"}
    ls = re.search(r"shop_order_lines_line_status_check\s+check \(line_status in \(([^)]*)\)\)", low).group(1)
    assert [x.strip(" '") for x in ls.split(",")] == list(R7C_LINE_STATUSES), "every live status kept, three added"
    cr = re.search(r"shop_orders_cancel_reason_code_check\s+check \(cancel_reason_code is null or cancel_reason_code in\s+"
                   r"\(([^)]*)\)\)", low).group(1)
    from app.shop_pipeline import CANCEL_REASONS
    assert {x.strip(" '\n") for x in cr.split(",")} == set(CANCEL_REASONS), "the check and the chips agree"
    ch = re.search(r"change_reason in\s+\(([^)]*)\)", low).group(1)
    from app.shop_heart import ADDED_STAGES, CHANGE_REASONS
    assert [x.strip(" '\n") for x in ch.split(",")] == list(CHANGE_REASONS)
    st = re.search(r"added_at_stage in \(([^)]*)\)", low).group(1)
    assert tuple(x.strip(" '") for x in st.split(",")) == ADDED_STAGES
    assert "revoke all on shop_orders, shop_order_lines from anon, authenticated;" in low
    assert not re.search(r"^\s*grant\s", low, re.M) and "cascade" not in low and "security_invoker" not in low
    assert not re.search(r"^\s*(insert into|update\s+\w+\s+set|delete from|truncate)\b", low, re.M), "no row is written"
    assert not re.search(r"^\s*(commit|end)\s*;", low, re.M), "rehearsable (no COMMIT)"
    assert low.count("do $$") == 1 and "raise exception" in low.split("do $$")[1], "a self-check that raises"
    assert "on delete set null" in low, "a substitute never blocks (or takes) another line"


@test("review: every view that counts R7c line columns reads them through to_jsonb, so this reverse never meets a dependent view")
def _():
    # v_command_orders (units the shop asked for vs rep-added, delivered units) and v_agent_shop_lines read
    # added_at_stage / qty_delivered. A direct column reference would pin the column: the reverse's DROP COLUMN
    # would then fail (no CASCADE, ever), and a view created before this migration could not read them at all.
    readers = set()
    for path in sorted((ROOT / "scripts").glob("*.sql")):
        if path.name.startswith("r7c_order_lines_qty_"):
            continue
        for name, body in re.findall(r"create or replace view (\w+) as\n(.*?);\n", _code(path.read_text(encoding="utf-8")),
                                     re.S):
            if "shop_order_lines" not in body:
                continue            # e.g. v_product_economics has its own cost_source, unrelated to the order lines
            for col in R7C_LINE:
                if col not in body:
                    continue
                readers.add(name)
                bare = re.sub(rf"->> '{col}'|\bas {col}\b", "", body)       # the jsonb key and the output alias
                assert not re.search(rf"\b{col}\b", bare), f"{path.name}: {name} reads {col} directly"
    assert {"v_command_orders", "v_agent_shop_lines"} <= readers, readers
    head = MIGRATION.read_text(encoding="utf-8").split("alter table", 1)[0]
    assert "to_jsonb(l)" in head and "v_command_orders" in head and "v_agent_shop_lines" in head
    assert "to_jsonb(l)" in REVERSE.read_text(encoding="utf-8").split("alter table", 1)[0]


@test("reverse: drops the 11 columns, restores both checks NOT VALID (no row rewritten), never CASCADE / row deletes")
def _():
    rev = _code(REVERSE.read_text(encoding="utf-8"))
    for col in R7C_LINE:
        assert f"alter table shop_order_lines drop column if exists {col};" in rev, col
    for col in R7C_ORDER:
        assert f"alter table shop_orders drop column if exists {col};" in rev, col
    assert re.search(r"check \(line_status in \('ok', 'changed', 'removed', 'backorder'\)\) not valid;", rev)
    assert re.search(r"'price_issue', 'other'\)\) not valid;", rev)
    assert "cascade" not in rev and "delete from" not in rev and "truncate" not in rev and not re.search(r"\bupdate\s", rev)
    assert "do $$" in rev and "raise exception" in rev
    assert "db_backup" in REVERSE.read_text(encoding="utf-8"), "the header asks for a backup first"


# ═══════════════════════════════════════════════════════════════════════════════
# 12. a local Postgres replay (SKIPs cleanly without a local cluster — never production)
# ═══════════════════════════════════════════════════════════════════════════════

LOCAL_DSN = os.environ.get("YQ_LOCAL_PG_R7C", os.environ.get("YQ_LOCAL_PG", "postgresql://postgres@localhost:55432/postgres"))

BASE_SCHEMA = """
create role anon nologin; create role authenticated nologin;
create table shop_orders (
  id bigint generated always as identity primary key, order_no text unique not null, status text not null default 'new'
    check (status in ('new','confirmed','packed','out_for_delivery','delivered','cancelled')),
  total_bhd numeric(12,3) not null default 0, cancel_reason_code text, updated_at timestamptz default now());
alter table shop_orders add constraint shop_orders_cancel_reason_code_check
  check (cancel_reason_code is null or cancel_reason_code in
         ('out_of_stock','customer_request','duplicate','test','price_issue','other'));
create table shop_order_lines (
  id bigint generated always as identity primary key,
  order_id bigint not null references shop_orders(id) on delete cascade,
  item_code text not null, qty integer not null check (qty > 0), unit_price_bhd numeric not null,
  line_total_bhd numeric not null, qty_confirmed integer, line_status text not null default 'ok',
  unit_price_confirmed numeric(12,3), line_total_confirmed numeric(12,3));
alter table shop_order_lines add constraint shop_order_lines_line_status_check
  check (line_status in ('ok','changed','removed','backorder'));
alter table shop_orders enable row level security; alter table shop_order_lines enable row level security;
insert into shop_orders (order_no, status, total_bhd, cancel_reason_code) values
  ('YQ-T-1', 'new', 37.5, null), ('YQ-T-2', 'cancelled', 12, 'duplicate');
insert into shop_order_lines (order_id, item_code, qty, unit_price_bhd, line_total_bhd, line_status) values
  (1, 'T02', 10, 2.95, 29.5, 'ok'), (1, 'X05', 4, 2.0, 8.0, 'backorder');
"""


@test("local replay: apply twice (idempotent), the new statuses / reasons / FK work, bad values refused, reverse keeps rows (SKIP without a cluster)")
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
    mig, rev = MIGRATION.read_text(encoding="utf-8"), REVERSE.read_text(encoding="utf-8")
    try:
        with conn.cursor() as cur:
            cur.execute("select to_regclass('public.shop_orders') is not null")
            if cur.fetchone()[0]:
                print("  SKIP: this database already has shop_orders (the replay builds its own synthetic tables)")
                return
            cur.execute("select 1 from pg_roles where rolname = 'anon'")
            if cur.fetchone():
                cur.execute(BASE_SCHEMA.split("\n", 2)[2])
            else:
                cur.execute(BASE_SCHEMA)
            digest_sql = "select md5(string_agg(row(l.*)::text, '|' order by id)) from shop_order_lines l"
            cur.execute(digest_sql)
            before = cur.fetchone()[0]
            cur.execute(mig)
            cur.execute(mig)                                      # idempotent: the second run changes nothing
            cur.execute(digest_sql.replace("row(l.*)", "row(l.id, l.order_id, l.item_code, l.qty, l.unit_price_bhd, "
                                                          "l.line_total_bhd, l.qty_confirmed, l.line_status, "
                                                          "l.unit_price_confirmed, l.line_total_confirmed)"))
            assert cur.fetchone()[0] == before, "no existing line changed"
            cur.execute("update shop_order_lines set qty_confirmed = 0, line_status = 'substituted', "
                        "substitute_item_code = 'UK21', change_reason = 'out_of_stock' where id = 2")
            cur.execute("insert into shop_order_lines (order_id, item_code, qty, unit_price_bhd, line_total_bhd, "
                        "qty_confirmed, line_status, change_reason, substitute_for_line, added_at_stage, unit_cost_bhd, "
                        "cost_source) values (1, 'UK21', 4, 5.25, 21, 4, 'added', 'substituted', 2, 'confirm', 2.6, 'mrn')")
            cur.execute("update shop_order_lines set qty_delivered = 8, change_reason = 'damaged' where id = 1")
            cur.execute("update shop_orders set status = 'cancelled', cancel_reason_code = 'below_minimum', "
                        "expected_delivery_date = date '2026-09-28', reopened_at = now(), reopened_by = 'a@example.com', "
                        "reopen_reason = 'test' where id = 1")
            for bad in ("update shop_order_lines set change_reason = 'lazy' where id = 1",
                        "update shop_order_lines set added_at_stage = 'later' where id = 1",
                        "update shop_order_lines set qty_delivered = -1 where id = 1",
                        "update shop_order_lines set line_status = 'gone' where id = 1",
                        "update shop_orders set cancel_reason_code = 'bored' where id = 1"):
                cur.execute("savepoint s")
                try:
                    cur.execute(bad)
                    raise AssertionError(f"accepted: {bad}")
                except psycopg.errors.CheckViolation:
                    cur.execute("rollback to savepoint s")
            cur.execute("delete from shop_order_lines where id = 2")   # the FK sets the substitute's pointer to null
            cur.execute("select substitute_for_line from shop_order_lines where item_code = 'UK21'")
            assert cur.fetchone()[0] is None
            cur.execute("select count(*) from information_schema.role_table_grants where table_schema = 'public' "
                        "and table_name in ('shop_orders', 'shop_order_lines') "
                        "and grantee in ('anon', 'authenticated')")
            assert cur.fetchone()[0] == 0
            # the reverse: rows written with R7c values stay (NOT VALID), new writes held to the old lists
            cur.execute(rev)
            cur.execute("select line_status from shop_order_lines where item_code = 'UK21'")
            assert cur.fetchone()[0] == "added", "an existing row is never rewritten"
            cur.execute("select cancel_reason_code from shop_orders where id = 1")
            assert cur.fetchone()[0] == "below_minimum"
            cur.execute("savepoint s")
            try:
                cur.execute("insert into shop_order_lines (order_id, item_code, qty, unit_price_bhd, line_total_bhd, "
                            "line_status) values (1, 'C18', 1, 1, 1, 'unavailable')")
                raise AssertionError("the narrowed check must hold new rows")
            except psycopg.errors.CheckViolation:
                cur.execute("rollback to savepoint s")
            cur.execute(mig)                                       # and forward again after a reverse
        print("  replay: migration x2, reverse, migration again — all inside one rolled-back transaction")
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
