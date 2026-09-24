"""R3b — order pipeline states, customer attribution, admin audit (trust plan §9 Step 3 items
5-7; audit C-06, C-07, B-08, SEC-10, critic #8, TXN-08).

    python -m tests.test_r3_pipeline

Same lightweight runner as tests/test_r1_orders.py, and the same idea: EVERY test runs against
an in-memory stand-in for the PostgREST client (real eq/in/is filters, inserts that hand out
ids, updates that report what they touched, an optional column list so a select or a write
naming a column the table does not have raises like PostgREST). Nothing here reaches a
database or the network: the pure tests pass with no .env at all (CI sets SUPABASE_URL only).

Covered:
  * cancel reasons — a staff cancel needs a code, 'other' needs a note, the merchant's cancel
    is customer_request; the code reaches the column when it exists and the event always; the
    shop's cancel email carries the reason's label only, never the internal note; a cancel
    still lands after the reverse script drops the column under a cached probe;
  * payment — unpaid | partial | paid as a recorded fact (+ event), never a status; a request
    without a method keeps the method on the row; 'paid' survives a dropped paid_at;
  * returns — a 'returned' EVENT with lines / qty / reason, Decimal money, delivered only;
    never more than delivered across calls; returned_bhd = Σ events, not prev + this;
  * the Focus invoice at Delivered and the reconciliation list (empty + hint pre-migration;
    invoice_reused; the ledger's as-of date);
  * the public rep picker (pickable = active + public profile + linked login; id + name only;
    a pick of an unlisted rep is ignored) and the checkout still receives what it reads;
  * the attribution precedence matrix: link A then B, saved details, a new phone, a recognised
    phone, a staff order for another rep's shop (conflict + event), reassignment — and the
    sticky rep settling at confirm / deliver, never at request, never over an existing value;
  * customer-level assignment (reason required, audit_log + shop_admin_audit);
  * the admin audit helper (never raises, strips, diffs, lists with a hint pre-migration) and
    the call sites (settings, rules, campaigns, salesmen, upcoming);
  * routes: every new endpoint is admin-gated and a rep gets 403; the status route carries
    reason_code / focus_invoice_no; assign's also_customer.
"""
from __future__ import annotations

import io
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

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── a table-aware fake PostgREST client (tests/test_r1_orders.py, trimmed) ──────

def _same(a, b) -> bool:
    return a == b or str(a) == str(b)


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.negate = "select", None, [], False
        self.cols, self.want_count, self.lim, self.head = "*", None, None, False
        self.desc = False

    def select(self, cols="*", count=None, head=False):
        self.op, self.cols, self.want_count, self.head = "select", cols, count, head
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

    def order(self, col, desc=False, **_kw):
        self.desc = bool(desc)
        return self

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*a, **_kw):
            if name in ("gte", "gt", "lte", "lt"):
                return self._filter(name, a[0], a[1])
            if name in ("like", "or_"):
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

    def _check_payload_columns(self):
        known = self.db.columns.get(self.table)
        if known is None:
            return
        items = self.payload if isinstance(self.payload, list) else [self.payload]
        for p in items:
            for c in p or {}:
                if c not in known:
                    raise RuntimeError(f"PGRST204: Could not find the '{c}' column of '{self.table}' in the schema cache")

    def execute(self):
        db = self.db
        db.calls.append((self.op, self.table, self.cols if self.op == "select" else self.payload, list(self.filters),
                         {"count": self.want_count, "head": self.head, "limit": self.lim}))
        fail = db.fail.get((self.op, self.table))
        if fail is not None:
            raise fail  # type: ignore[misc]
        if self.table in db.missing:
            raise RuntimeError(f'relation "public.{self.table}" does not exist')
        rows = db.tables.setdefault(self.table, [])
        if self.op == "select":
            known = db.columns.get(self.table)
            if known is not None and self.cols != "*":
                for c in (x.strip() for x in str(self.cols).split(",")):
                    if c and c not in known:
                        raise RuntimeError(f"column {self.table}.{c} does not exist")
            out = [dict(r) for r in rows if self._match(r)]
            if self.desc:
                out.reverse()
            data = [] if self.head else (out[: self.lim] if self.lim else out)
            return SimpleNamespace(data=data, count=(len(out) if self.want_count else None))
        if self.op in ("insert", "upsert"):
            self._check_payload_columns()
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for p in items:
                r = dict(p)
                if self.op == "upsert" and "key" in r:      # app_settings-style upsert on key
                    hit = next((x for x in rows if x.get("key") == r["key"]), None)
                    if hit:
                        hit.update(r)
                        out.append(dict(hit))
                        continue
                r.setdefault("id", db.next_id(self.table))
                rows.append(r)
                out.append(dict(r))
            return SimpleNamespace(data=out, count=None)
        if self.op == "update":
            self._check_payload_columns()
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
    def __init__(self, tables: dict | None = None, columns: dict | None = None, missing: set | None = None):
        self.tables: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.columns: dict[str, set] = {k: set(v) for k, v in (columns or {}).items()}
        self.missing: set[str] = set(missing or ())       # tables the migration has not created yet
        self.calls: list[tuple] = []
        self.fail: dict[tuple, object] = {}
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
    context, and reset every cache the order path consults."""

    def __init__(self, fake: _FakeDB, ctx: dict | None = None):
        self.fake, self.ctx = fake, ctx

    def __enter__(self):
        import app.catalog as cat
        import app.database as db
        import app.shop as s
        import app.upcoming as up
        self.mods = (s, db, cat, up)
        self.saved = (s.get_client, db.get_client, cat.get_client, s._ctx_cache["ctx"], s._ctx_cache["at"], s.context,
                      up.get_client)
        s.get_client = db.get_client = cat.get_client = up.get_client = lambda: self.fake
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        db.invalidate_user_cache()
        if self.ctx is not None:
            s._ctx_cache.update(ctx=self.ctx, at=time.time() + 10 ** 6)
            # an admin edit calls invalidate() then reads context() again (rule impact, the roster):
            # keep serving the synthetic context instead of rebuilding from the catalog views
            s.context = lambda force=False: self.ctx
        return self.fake

    def __exit__(self, *_exc):
        s, db, cat, up = self.mods
        s.get_client, db.get_client, cat.get_client = self.saved[:3]
        s.context, up.get_client = self.saved[5], self.saved[6]
        s._ctx_cache.update(ctx=self.saved[3], at=self.saved[4])
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        db.invalidate_user_cache()
        return False


# ── fixtures ──────────────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)


def _item(code, price, stock=100, cat="CABLE"):
    return {"item_code": code, "display_name": code, "spec": f"{code} spec", "category": cat, "brand": "VFAN",
            "standard_rate": price, "b2c_rate": None, "product_image_url": None, "package_image_url": None,
            "sort_order": None, "created_at": "2026-07-03T00:00:00+00:00", "moq": 1, "pack_size": None,
            "stock_qty": stock, "stock_as_of": "2026-09-14", "sold_30d": 0, "prev_30d": 0, "sold_90d": 0,
            "customers_30d": 0}


# 1 Furqan: linked login, public → pickable.  2 Harsh: no login → NOT pickable (but his slug works).
# 3 Karrar: login but profile hidden → not pickable.  4 Old: inactive (never in the ctx).
SALESMEN = [
    {"id": 1, "name": "Furqan Ahmed", "referral_code": "furqan", "is_active": True, "focus_name": "FURQAN",
     "user_email": "rep@example.com", "whatsapp": "97300000001", "public_profile": True},
    {"id": 2, "name": "Harsh Bhatia", "referral_code": "harsh", "is_active": True, "focus_name": "HARSH B",
     "user_email": None, "public_profile": True},
    {"id": 3, "name": "Karrar Ali", "referral_code": "karrar", "is_active": True, "focus_name": "KARRAR",
     "user_email": "karrar@example.com", "public_profile": False},
]


def _ctx(items=None, **settings):
    from app.shop import SETTING_DEFAULTS
    items = items if items is not None else [_item("T02", 2.95), _item("X05", 2.0)]
    vals = dict(SETTING_DEFAULTS)
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: i for i in items},
            "order": [i["item_code"] for i in items], "by_upper": {i["item_code"].upper(): i["item_code"] for i in items},
            "costs": {i["item_code"]: 0.001 for i in items}, "rules": [], "salesmen": [dict(s) for s in SALESMEN],
            "pairs": {}, "drops": {}, "campaigns": [], "loaded_at": "", "share_token": "tok-test-token-value"}


def _body(**over):
    b = {"lines": [{"item_code": "T02", "qty": 3}, {"item_code": "X05", "qty": 2}],
         "customer": {"name": "Test Shop", "phone": "33001122", "shop": "Test Shop", "area": "Manama"},
         "device_id": "dev-1", "client_order_id": "coid-1", "session_ref": "furqan", "session_id": "sess-1"}
    b.update(over)
    return b


def _order(id, status="new", **over):
    o = {"id": id, "order_no": f"YQ-2609-{id:04d}", "token": f"tok{id}".ljust(24, "x"), "status": status,
         "customer_name": "Test Shop", "customer_phone": "97333001122", "customer_shop": "Test Shop",
         "customer_area": "Manama", "customer_email": None, "salesman_id": 1, "salesman_name": "Furqan Ahmed",
         "source": "market", "referral_code": "furqan", "src": None, "coupon_code": None,
         "subtotal_bhd": 12.85, "discount_bhd": 0, "delivery_bhd": 0, "total_bhd": 12.85, "items_count": 2,
         "units_count": 5, "has_backorder": False, "device_id": "dev-1", "client_order_id": "coid-1",
         "created_at": (NOW - timedelta(hours=2)).isoformat(), "updated_at": None, "total_confirmed_bhd": None,
         "attribution_source": "session_ref", "attribution_conflict": False, "is_test": False,
         "payment_status": "unpaid", "payment_method": None, "focus_invoice_no": None, "cancel_reason": None,
         "customer_id": None, "order_kind": "standard", "minimum_gap_bhd": None, "notify_result": None,
         "expected_delivery": None, "placed_by": None}
    o.update(over)
    return o


def _lines(order_id):
    return [{"id": order_id * 10 + 1, "order_id": order_id, "item_code": "T02", "display_name": "T02", "qty": 3,
             "list_price_bhd": 2.95, "unit_price_bhd": 2.95, "discount_bhd": 0, "line_total_bhd": 8.85,
             "stock_status": "in_stock", "backorder": False, "line_status": "ok", "qty_confirmed": None},
            {"id": order_id * 10 + 2, "order_id": order_id, "item_code": "X05", "display_name": "X05", "qty": 2,
             "list_price_bhd": 2.0, "unit_price_bhd": 2.0, "discount_bhd": 0, "line_total_bhd": 4.0,
             "stock_status": "in_stock", "backorder": False, "line_status": "ok", "qty_confirmed": None}]


# every column an order row has BEFORE scripts/r3_pipeline_migration.sql
ORDER_COLS_PRE_R3 = set(_order(1)) | {"cancelled_by", "cancelled_at", "confirmed_at", "delivered_at", "packed_at",
                                      "out_for_delivery_at", "issued_to_salesman_id", "issued_at",
                                      "subtotal_confirmed_bhd", "assigned_at", "assigned_by", "session_ref", "ua",
                                      "ip_hash", "note", "sla_notified_at", "small_order_fee_bhd", "confirm_notified_at",
                                      "notify_attempts", "notified_at"}
ORDER_COLS_R3 = ORDER_COLS_PRE_R3 | {"cancel_reason_code", "paid_at", "returned_bhd"}


def _db(missing: set | None = None, **tables) -> _FakeDB:
    base = {"salesmen": SALESMEN, "app_settings": [{"key": "shop_market_url", "value": "https://yqmarketplace.com"}],
            "shop_customers": [], "shop_customer_phones": [], "shop_orders": [], "shop_order_lines": [],
            "shop_order_events": [], "shop_events": [], "audit_log": [], "shop_admin_audit": [],
            "salesman_kickback_statements": [], "user_roles": []}
    base.update(tables)
    return _FakeDB(base, missing=missing)


def _raises(fn, want: str):
    from app.shop import ShopError
    try:
        fn()
    except ShopError as e:
        assert want.lower() in str(e).lower(), (want, str(e))
        return e
    raise AssertionError(f"expected ShopError containing {want!r}")


def _events(fake, order_id=None):
    return [e for e in fake.rows("shop_order_events") if order_id is None or e.get("order_id") == order_id]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. cancel reasons
# ═══════════════════════════════════════════════════════════════════════════════

@test("cancel: a staff cancel needs a reason code from the fixed list; 'other' needs a note; the merchant's is customer_request")
def _():
    from app.shop_pipeline import CANCEL_REASONS, validate_cancel
    assert list(CANCEL_REASONS) == ["out_of_stock", "customer_request", "duplicate", "test", "price_issue", "other"]
    _raises(lambda: validate_cancel(None, None), "reason")
    _raises(lambda: validate_cancel("bogus", "x"), "reason")
    _raises(lambda: validate_cancel("other", None), "note")
    _raises(lambda: validate_cancel("other", "no"), "note")
    assert validate_cancel("out_of_stock", None) == ("out_of_stock", "Out of stock")
    assert validate_cancel("OUT_OF_STOCK ", " no X24 left ") == ("out_of_stock", "no X24 left")
    assert validate_cancel("other", "shop closed down") == ("other", "shop closed down")
    # the merchant's own cancel: never refused for a missing code
    assert validate_cancel(None, None, cancelled_by="customer") == ("customer_request", None)
    assert validate_cancel(None, "changed my mind", cancelled_by="customer") == ("customer_request", "changed my mind")
    assert validate_cancel("duplicate", None, cancelled_by="customer")[0] == "duplicate"


@test("cancel: set_status writes the code to the column when it exists, the text to cancel_reason, the code to the event always")
def _():
    from app.shop import set_status
    # column present (migration applied)
    fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1))
    fake.columns["shop_orders"] = ORDER_COLS_R3
    with _patched(fake):
        _raises(lambda: set_status(1, "cancelled", None, actor="rep@example.com"), "reason")
        assert fake.rows("shop_orders")[0]["status"] == "new" and _events(fake) == [], "a refused cancel writes nothing"
        _raises(lambda: set_status(1, "cancelled", "", actor="rep@example.com", reason_code="other"), "note")
        o = set_status(1, "cancelled", "shop asked", actor="rep@example.com", reason_code="customer_request")
        assert o["status"] == "cancelled" and o["cancel_reason"] == "shop asked" and o["cancelled_by"] == "staff"
        assert fake.rows("shop_orders")[0]["cancel_reason_code"] == "customer_request"
        ev = _events(fake)[-1]
        assert ev["event"] == "status:cancelled" and ev["detail"]["reason_code"] == "customer_request"
        assert ev["detail"]["note"] == "shop asked" and ev["detail"]["from"] == "new"
    # column absent (API deployed before the migration): the code lives on the event only
    fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1))
    fake.columns["shop_orders"] = ORDER_COLS_PRE_R3
    with _patched(fake):
        o = set_status(1, "cancelled", None, actor="rep@example.com", reason_code="out_of_stock")
        assert o["cancel_reason"] == "Out of stock" and "cancel_reason_code" not in fake.rows("shop_orders")[0]
        assert _events(fake)[-1]["detail"]["reason_code"] == "out_of_stock"
    # the reverse script drops the column under a cached hit: the cancel still lands (one retry
    # without the column, the code on the event, the probe forgotten) — never a 500 for the ten
    # minutes a stale hit would otherwise last
    import app.shop as s
    fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1))
    fake.columns["shop_orders"] = ORDER_COLS_R3
    with _patched(fake):
        assert s.has_column("shop_orders", "cancel_reason_code") is True
        fake.columns["shop_orders"] = ORDER_COLS_PRE_R3
        o = set_status(1, "cancelled", "same as 0002", actor="rep@example.com", reason_code="duplicate")
        assert o["status"] == "cancelled" and o["cancel_reason"] == "same as 0002"
        assert "cancel_reason_code" not in fake.rows("shop_orders")[0]
        ups = [c for c in fake.writes("shop_orders") if c[0] == "update"]
        assert len(ups) == 2 and "cancel_reason_code" in ups[0][2] and "cancel_reason_code" not in ups[1][2], ups
        assert _events(fake)[-1]["detail"]["reason_code"] == "duplicate"
        assert "shop_orders.cancel_reason_code" not in s._col_cache


@test("cancel: the merchant's own cancel (cancel_by_customer) still needs no code and records customer_request")
def _():
    from app.shop import cancel_by_customer
    fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1))
    fake.columns["shop_orders"] = ORDER_COLS_R3
    with _patched(fake):
        o = cancel_by_customer(_order(1)["token"], "changed my mind")
        assert o["status"] == "cancelled" and o["cancelled_by"] == "customer" and o["cancel_reason"] == "changed my mind"
        assert fake.rows("shop_orders")[0]["cancel_reason_code"] == "customer_request"
        assert _events(fake)[-1]["detail"]["reason_code"] == "customer_request"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. payment
# ═══════════════════════════════════════════════════════════════════════════════

@test("payment: paid | partial | unpaid is a recorded fact with method, amount and an event — never a status")
def _():
    from app.shop_pipeline import set_payment
    fake = _db(shop_orders=[_order(1, status="delivered", total_confirmed_bhd=12.0)], shop_order_lines=_lines(1))
    fake.columns["shop_orders"] = ORDER_COLS_R3
    with _patched(fake):
        _raises(lambda: set_payment(1, "settled", None, None, None, "admin@example.com"), "unpaid, partial or paid")
        _raises(lambda: set_payment(1, "paid", "crypto", None, None, "admin@example.com"), "method")
        _raises(lambda: set_payment(1, "partial", "cash", -1, None, "admin@example.com"), "negative")
        _raises(lambda: set_payment(404, "paid", None, None, None, "admin@example.com"), "not found")
        o = set_payment(1, "partial", "cash", 5, "first half", "admin@example.com")
        assert o["status"] == "delivered" and o["payment_status"] == "partial" and o["payment_method"] == "cash"
        assert o["payment_label"] == "Partly paid" and fake.rows("shop_orders")[0]["paid_at"] is None
        o = set_payment(1, "PAID", "benefit", None, None, "admin@example.com")
        assert o["payment_status"] == "paid" and fake.rows("shop_orders")[0]["paid_at"], "paid stamps paid_at"
        evs = [e for e in _events(fake) if e["event"] == "payment"]
        assert [e["detail"]["to"] for e in evs] == ["partial", "paid"] and evs[0]["detail"]["from"] == "unpaid"
        assert evs[0]["detail"]["amount_bhd"] == 5.0 and evs[0]["detail"]["note"] == "first half"
        assert evs[1]["detail"]["amount_bhd"] == 12.0, "paid with no amount = the confirmed total"
        o = set_payment(1, "unpaid", None, None, "bounced", "admin@example.com")
        assert o["payment_status"] == "unpaid" and fake.rows("shop_orders")[0]["paid_at"] is None
        assert o["payment_method"] == "benefit", "no method in the request = the method on the row stays"
        assert [e["detail"]["method"] for e in _events(fake) if e["event"] == "payment"] == ["cash", "benefit", "benefit"]
        assert o["payment_label"] == "Payment not recorded", "the default state says what is true, never 'owes'"
    # a cancelled order has nothing to pay; before the migration paid_at is never named
    fake = _db(shop_orders=[_order(1, status="cancelled")], shop_order_lines=_lines(1))
    with _patched(fake):
        _raises(lambda: set_payment(1, "paid", None, None, None, "admin@example.com"), "cancelled")
    fake = _db(shop_orders=[_order(1, status="delivered")], shop_order_lines=_lines(1))
    fake.columns["shop_orders"] = ORDER_COLS_PRE_R3
    with _patched(fake):
        o = set_payment(1, "paid", "cash", None, None, "admin@example.com")
        assert o["payment_status"] == "paid" and "paid_at" not in fake.rows("shop_orders")[0]
    # the reverse script drops paid_at under a cached hit: 'paid' still lands (one retry, probe forgotten)
    import app.shop as s
    fake = _db(shop_orders=[_order(1, status="delivered")], shop_order_lines=_lines(1))
    fake.columns["shop_orders"] = ORDER_COLS_R3
    with _patched(fake):
        assert s.has_column("shop_orders", "paid_at") is True
        fake.columns["shop_orders"] = ORDER_COLS_PRE_R3
        o = set_payment(1, "paid", "cash", None, None, "admin@example.com")
        assert o["payment_status"] == "paid" and "paid_at" not in fake.rows("shop_orders")[0]
        ups = [c for c in fake.writes("shop_orders") if c[0] == "update"]
        assert len(ups) == 2 and "paid_at" in ups[0][2] and "paid_at" not in ups[1][2], ups
        assert "shop_orders.paid_at" not in s._col_cache


# ═══════════════════════════════════════════════════════════════════════════════
# 3. returns
# ═══════════════════════════════════════════════════════════════════════════════

@test("return: a 'returned' event with lines, qty and reason on a delivered order; Decimal value; status untouched")
def _():
    from decimal import Decimal
    from app.shop_pipeline import record_return
    lines = _lines(1)
    lines[0].update(qty_confirmed=2, unit_price_confirmed=2.9, line_total_confirmed=5.8)   # confirmed 2 of 3 at 2.900
    lines[1].update(qty_confirmed=2)
    fake = _db(shop_orders=[_order(1, status="delivered")], shop_order_lines=lines)
    fake.columns["shop_orders"] = ORDER_COLS_R3
    with _patched(fake):
        _raises(lambda: record_return(1, [{"line_id": 11, "qty": 1}], "", "admin@example.com"), "reason")
        _raises(lambda: record_return(1, [], "damaged", "admin@example.com"), "at least one")
        _raises(lambda: record_return(1, [{"line_id": 11, "qty": 3}], "damaged", "admin@example.com"), "between 1 and 2")
        _raises(lambda: record_return(1, [{"line_id": 99, "qty": 1}], "damaged", "admin@example.com"), "unknown")
        _raises(lambda: record_return(1, [{"line_id": 11, "qty": 1}, {"line_id": 11, "qty": 1}], "damaged", "admin@example.com"), "repeated")
        out = record_return(1, [{"line_id": 11, "qty": 2}, {"line_id": 12, "qty": 1}], "damaged boxes", "admin@example.com")
        # 2 × 2.900 (the CONFIRMED price) + 1 × 2.000 = 7.800, exact to the fils
        assert out["value_bhd"] == 7.8 and Decimal(str(out["value_bhd"])) == Decimal("7.800"), out
        assert [x["value_bhd"] for x in out["lines"]] == [5.8, 2.0]
        row = fake.rows("shop_orders")[0]
        assert row["status"] == "delivered" and row["returned_bhd"] == 7.8, "an event, never a status"
        ev = [e for e in _events(fake) if e["event"] == "returned"][-1]
        assert ev["detail"]["reason"] == "damaged boxes" and ev["detail"]["units"] == 3 and ev["detail"]["value_bhd"] == 7.8
        assert fake.rows("audit_log")[-1]["event"] == "shop.order_return"
        # a second return adds up on the row
        record_return(1, [{"line_id": 12, "qty": 1}], "wrong colour", "admin@example.com")
        assert fake.rows("shop_orders")[0]["returned_bhd"] == 9.8
        # and nothing can come back beyond what was delivered, across calls (11: 2 of 2 back; 12: 1 + 1 of 2)
        _raises(lambda: record_return(1, [{"line_id": 11, "qty": 1}], "again", "admin@example.com"), "already returned")
        _raises(lambda: record_return(1, [{"line_id": 12, "qty": 1}], "again", "admin@example.com"), "already returned")
        assert fake.rows("shop_orders")[0]["returned_bhd"] == 9.8 and len([e for e in _events(fake) if e["event"] == "returned"]) == 2
    # already-returned units cap the next call; the row value is Σ of the events, never prev + this
    from app.shop_pipeline import returned_so_far, returned_value
    fake = _db(shop_orders=[_order(2, status="delivered", returned_bhd=None)], shop_order_lines=_lines(2))
    fake.columns["shop_orders"] = ORDER_COLS_R3
    with _patched(fake):
        record_return(2, [{"line_id": 21, "qty": 1}], "damaged", "admin@example.com")          # 1 of 3 back
        assert fake.rows("shop_orders")[0]["returned_bhd"] == 2.95
        _raises(lambda: record_return(2, [{"line_id": 21, "qty": 3}], "more", "admin@example.com"),
                "between 1 and 2 (1 of 3 already returned)")
        fake.rows("shop_orders")[0]["returned_bhd"] = 0.5          # a stale row value must not compound
        record_return(2, [{"line_id": 21, "qty": 2}], "the rest", "admin@example.com")
        assert fake.rows("shop_orders")[0]["returned_bhd"] == 8.85, "recomputed from the events"
        evs = _events(fake, 2)
        assert returned_so_far(evs) == {21: 3} and str(returned_value(evs)) == "8.850"
        _raises(lambda: record_return(2, [{"line_id": 21, "qty": 1}], "again", "admin@example.com"), "already returned")
    # delivered only; before the migration the value stays on the event (no column named)
    fake = _db(shop_orders=[_order(1, status="confirmed")], shop_order_lines=_lines(1))
    with _patched(fake):
        _raises(lambda: record_return(1, [{"line_id": 11, "qty": 1}], "damaged", "admin@example.com"), "delivered")
    fake = _db(shop_orders=[_order(1, status="delivered")], shop_order_lines=_lines(1))
    fake.columns["shop_orders"] = ORDER_COLS_PRE_R3
    with _patched(fake):
        out = record_return(1, [{"line_id": 11, "qty": 3}], "damaged", "admin@example.com")
        assert out["value_bhd"] == 8.85 and "returned_bhd" not in fake.rows("shop_orders")[0]
        assert not [c for c in fake.writes("shop_orders")], "no row write without the column"


@test("return: money never drifts — 10,000 random return lines equal a pure-Decimal reference")
def _():
    import random
    from decimal import ROUND_HALF_UP, Decimal
    from app.shop_pipeline import q3
    rng = random.Random(2609)
    for _i in range(10_000):
        unit = Decimal(rng.randint(1, 99999)) / Decimal(1000)
        qty = rng.randint(1, 500)
        ref = (unit * qty).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        assert q3(float(unit) * 1) * qty == unit * qty and q3(unit * qty) == ref, (unit, qty)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. the Focus invoice + reconciliation
# ═══════════════════════════════════════════════════════════════════════════════

@test("invoice: Delivered may carry the Focus invoice number; it can be added later on a delivered order only")
def _():
    from app.shop import set_status
    from app.shop_pipeline import set_invoice
    fake = _db(shop_orders=[_order(1, status="out_for_delivery")], shop_order_lines=_lines(1))
    with _patched(fake):
        o = set_status(1, "delivered", None, actor="admin@example.com", focus_invoice_no="  SI-26-09-0042 ")
        assert o["status"] == "delivered" and o["focus_invoice_no"] == "SI-26-09-0042"
        assert _events(fake)[-1]["detail"]["focus_invoice_no"] == "SI-26-09-0042"
        o = set_invoice(1, "SI-26-09-0043", "admin@example.com")
        assert o["focus_invoice_no"] == "SI-26-09-0043"
        ev = [e for e in _events(fake) if e["event"] == "invoice"][-1]
        assert ev["detail"] == {"from": "SI-26-09-0042", "to": "SI-26-09-0043"}
        n = len(fake.writes())
        set_invoice(1, "SI-26-09-0043", "admin@example.com")
        assert len(fake.writes()) == n, "the same number again writes nothing"
        _raises(lambda: set_invoice(1, "   ", "admin@example.com"), "enter")
    fake = _db(shop_orders=[_order(2, status="confirmed")], shop_order_lines=_lines(2))
    with _patched(fake):
        _raises(lambda: set_invoice(2, "SI-1", "admin@example.com"), "delivered")
        # an invoice on a non-delivered move is ignored, not an error
        o = set_status(2, "packed", None, actor="admin@example.com", focus_invoice_no="SI-1")
        assert o["status"] == "packed" and o.get("focus_invoice_no") is None


@test("recon: empty + hint before the view exists; flags and the issue count once it answers")
def _():
    from app.shop_pipeline import RECON_HINT, focus_recon
    fake = _db(missing={"v_shop_focus_recon"})
    with _patched(fake):
        r = focus_recon()
        assert r == {"rows": [], "count": 0, "issues": 0, "hint": RECON_HINT}
    rows = [
        {"order_id": 1, "order_no": "YQ-1", "delivered_at": "2026-09-20", "customer_shop": "A", "salesman_name": "Furqan Ahmed",
         "salesman_focus_name": "FURQAN", "order_total_bhd": 12.85, "payment_status": "unpaid", "focus_invoice_no": None,
         "invoice_total_bhd": None, "missing_invoice": True, "invoice_not_found": False, "salesman_mismatch": False,
         "amount_mismatch": False, "amount_diff_bhd": None, "is_test": False},
        {"order_id": 2, "order_no": "YQ-2", "delivered_at": "2026-09-21", "customer_shop": "B", "salesman_name": "Furqan Ahmed",
         "salesman_focus_name": "FURQAN", "order_total_bhd": 10.0, "payment_status": "paid", "focus_invoice_no": "SI-9",
         "invoice_total_bhd": 10.0, "focus_salesman": "FURQAN", "missing_invoice": False, "invoice_not_found": False,
         "salesman_mismatch": False, "amount_mismatch": False, "amount_diff_bhd": 0, "is_test": False},
        {"order_id": 3, "order_no": "YQ-3", "delivered_at": "2026-09-22", "customer_shop": "C", "salesman_name": "Furqan Ahmed",
         "salesman_focus_name": "FURQAN", "order_total_bhd": 10.0, "payment_status": "paid", "focus_invoice_no": "SI-10",
         "invoice_total_bhd": 9.5, "focus_salesman": "HARSH B", "missing_invoice": False, "invoice_not_found": False,
         "salesman_mismatch": True, "amount_mismatch": True, "amount_diff_bhd": -0.5, "is_test": False},
        # SI-9 again on a second delivered order: both rows read invoice_reused
        {"order_id": 4, "order_no": "YQ-4", "delivered_at": "2026-09-23", "customer_shop": "D", "salesman_name": "Furqan Ahmed",
         "salesman_focus_name": "FURQAN", "order_total_bhd": 10.0, "payment_status": "unpaid", "focus_invoice_no": "si-9",
         "invoice_total_bhd": 10.0, "focus_salesman": "FURQAN", "missing_invoice": False, "invoice_not_found": False,
         "salesman_mismatch": False, "amount_mismatch": False, "amount_diff_bhd": 0, "is_test": False, "invoice_reused": True},
    ]
    rows[1]["invoice_reused"] = True
    fake = _db(v_shop_focus_recon=rows)
    with _patched(fake):
        r = focus_recon()
        assert r["count"] == 4 and r["issues"] == 4 and "hint" not in r
        by = {x["order_no"]: x for x in r["rows"]}
        assert by["YQ-1"]["flags"] == ["missing_invoice"] and by["YQ-2"]["flags"] == ["invoice_reused"]
        assert by["YQ-3"]["flags"] == ["salesman_mismatch", "amount_mismatch"] and by["YQ-3"]["amount_diff_bhd"] == -0.5
        assert by["YQ-4"]["flags"] == ["invoice_reused"]
        assert "ledger_as_of" in r and r["ledger_as_of"] is None, "no ledger RPC on the fake: the key is there, the date is not"
        fake.rpc_results["run_readonly_query"] = [{"d": "2026-09-22"}]
        assert focus_recon()["ledger_as_of"] == "2026-09-22"
        fake.rpc_results["run_readonly_query"] = '[{"d": "2026-09-23T00:00:00"}]'    # the RPC may answer json text
        assert focus_recon()["ledger_as_of"] == "2026-09-23"


@test("recon: the migration file defines the view, the append-only audit and the three columns, with a self-check and a reverse")
def _():
    mig = (ROOT / "scripts" / "r3_pipeline_migration.sql").read_text(encoding="utf-8")
    rev = (ROOT / "scripts" / "r3_pipeline_reverse.sql").read_text(encoding="utf-8")
    low = mig.lower()
    assert "create table if not exists shop_admin_audit" in low and "before update or delete on shop_admin_audit" in low
    # TRUNCATE bypasses row triggers and the service role holds it: a statement trigger refuses it too
    assert "before truncate on shop_admin_audit" in low and "for each statement execute function shop_admin_audit_no_rewrite" in low
    assert "tg_op = 'truncate'" in low, "the shared trigger function must not touch OLD on a statement-level call"
    for col in ("cancel_reason_code", "paid_at", "returned_bhd"):
        assert f"add column if not exists {col}" in low, col
    assert "create or replace view v_shop_focus_recon" in low and "from v_sales" in low
    assert "as invoice_reused" in low and low.index("o.is_test") < low.index("as invoice_reused"), \
        "CREATE OR REPLACE VIEW may only append columns: invoice_reused comes after is_test"
    assert "revoke all on v_shop_focus_recon from anon, authenticated" in low
    check = low.split("self-check")[-1]
    assert "shop_admin_audit_no_truncate" in check and "invoice_reused" in check, "the self-check covers the new objects"
    import re
    assert not re.search(r"^\s*grant\s", low, re.M), "no GRANT statement in this migration (service role only)"
    assert low.count("do $$") == 1 and "raise exception" in low, "closing self-check"
    assert "commit;" not in low and "\nend;" not in low, "rehearsable (no COMMIT)"
    rl = rev.lower()
    for stmt in ("drop view if exists v_shop_focus_recon", "drop table if exists shop_admin_audit",
                 "drop column if exists cancel_reason_code", "drop column if exists paid_at", "drop column if exists returned_bhd"):
        assert stmt in rl, stmt
    assert "delete from" not in rl and "truncate" not in rl, "the reverse never deletes rows"
    assert rl.index("drop table if exists shop_admin_audit") < rl.index("drop function if exists shop_admin_audit_no_rewrite"), \
        "the table (and both triggers with it) goes before the function they depend on"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. the public rep picker
# ═══════════════════════════════════════════════════════════════════════════════

@test("picker: pickable = active + public profile + linked login; the public list is id + name only; staff keep the roster")
def _():
    from app.attribution import pickable, public_reps
    from app.shop import catalog_payload
    assert pickable(SALESMEN[0]) and not pickable(SALESMEN[1]) and not pickable(SALESMEN[2])
    assert not pickable({**SALESMEN[0], "is_active": False}) and not pickable(None)
    assert pickable({"id": 9, "name": "x", "user_email": "x@y.z"}), "a row without the profile column reads as public"
    ctx = _ctx()
    assert public_reps(ctx) == [{"id": 1, "name": "Furqan Ahmed"}]
    fake = _db()
    with _patched(fake, ctx):
        p = catalog_payload("tok-test-token-value")
        assert p["salesmen"] == [{"id": 1, "name": "Furqan Ahmed"}], p["salesmen"]
        assert all("referral_code" not in s for s in p["salesmen"]), "referral codes never leave through the public payload"
        # the market and share-link checkouts read exactly `id` + `name` from this list (web/src/market/pages/CheckoutPage.tsx,
        # web/src/pages/shop/CartDrawer.tsx): the keys the UI needs are there
        assert set(p["salesmen"][0]) == {"id", "name"}
        staff = catalog_payload(None, staff_email="rep@example.com")
        assert [s["id"] for s in staff["salesmen"]] == [1, 2, 3] and staff["salesmen"][1]["referral_code"] == "harsh"
    src = (ROOT / "web" / "src" / "market" / "pages" / "CheckoutPage.tsx").read_text(encoding="utf-8")
    assert "s.referral_code" not in src, "the checkout must not depend on referral_code in the picker"


@test("picker: a pick of a rep the picker never listed is ignored (checkout pick and the legacy dropdown)")
def _():
    from app.shop import resolve_salesman, salesman_for
    ctx = _ctx()
    sm, how, _c = resolve_salesman(ctx, phone=None, session_ref=None, pick_id=1, customer=None)
    assert sm["id"] == 1 and how == "checkout_pick"
    for hidden in (2, 3):
        sm, how, _c = resolve_salesman(ctx, phone=None, session_ref=None, pick_id=hidden, customer=None)
        assert sm is None and how == "unassigned", (hidden, how)
        assert salesman_for(ctx, None, hidden) == (None, "default")
    assert salesman_for(ctx, None, 1)[1] == "dropdown"
    # a slug still routes (the storefront link is the rep's, whoever may be picked)
    sm, how, _c = resolve_salesman(ctx, phone=None, session_ref="harsh", pick_id=None, customer=None)
    assert sm["id"] == 2 and how == "session_ref"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the attribution precedence matrix + the sticky rep
# ═══════════════════════════════════════════════════════════════════════════════

@test("matrix: link A then link B — the sticky rep is proposed at request (first_ref), written at confirm, then B is a flagged conflict")
def _():
    from app.shop import confirm_order, create_order, resolve_salesman, set_status
    fake = _db()
    with _patched(fake, _ctx()):
        o1 = create_order(_body(session_ref="furqan"), market=True)
        assert o1["salesman_id"] == 1 and o1["attribution_source"] == "session_ref" and not o1["attribution_conflict"]
        cust = fake.rows("shop_customers")[0]
        assert cust["sticky_salesman_id"] is None and cust["first_ref"] == "furqan", "proposed, not written"
        # a second request from the same phone before anything is confirmed: still the live link, no sticky yet
        o2 = create_order(_body(session_ref="harsh", client_order_id="coid-2"), market=True)
        assert o2["salesman_id"] == 2 and o2["attribution_source"] == "session_ref" and not o2["attribution_conflict"]
        assert fake.rows("shop_customers")[0]["sticky_salesman_id"] is None
        assert fake.rows("shop_customers")[0]["first_ref"] == "furqan", "the first slug stays the first slug"
        # rep A confirms o1 → the merchant's sticky rep settles to A
        confirm_order(o1["id"], [], "Tomorrow", None, actor="rep@example.com")
        assert fake.rows("shop_customers")[0]["sticky_salesman_id"] == 1
        # link B for the same phone now: sticky A wins inside the window, flagged
        cust = fake.rows("shop_customers")[0]
        sm, how, conflict = resolve_salesman(_ctx(), phone=cust["phone"], session_ref="harsh", customer=cust)
        assert sm["id"] == 1 and how == "sticky" and conflict
        o3 = create_order(_body(session_ref="harsh", client_order_id="coid-3"), market=True)
        assert o3["salesman_id"] == 1 and o3["attribution_source"] == "sticky" and o3["attribution_conflict"]
        # o2 (rep B's) delivered later: the sticky value on file is NOT overwritten
        set_status(o2["id"], "confirmed", None, actor="admin@example.com")
        set_status(o2["id"], "delivered", None, actor="admin@example.com")
        assert fake.rows("shop_customers")[0]["sticky_salesman_id"] == 1


@test("matrix: saved details + a NEW phone = a new merchant (link, pick, default, unassigned); a recognised phone follows its record")
def _():
    from app.shop import create_order, resolve_salesman
    ctx = _ctx()
    # a returning device with saved details but a phone nobody has seen: no record → the live link
    fake = _db()
    with _patched(fake, ctx):
        o = create_order(_body(customer={"name": "New Shop", "phone": "39001122", "shop": "New Shop", "area": "Riffa"},
                               session_ref="furqan"), market=True)
        assert o["attribution_source"] == "session_ref" and o["salesman_id"] == 1
        assert fake.rows("shop_customers")[0]["phone"] == "97339001122" and fake.rows("shop_customers")[0]["sticky_salesman_id"] is None
    # no link: an explicit pick of a pickable rep; else the default setting; else unassigned
    fake = _db()
    with _patched(fake, ctx):
        o = create_order(_body(session_ref=None, salesman_id=1), market=True)
        assert o["attribution_source"] == "checkout_pick" and o["salesman_id"] == 1
    fake = _db()
    with _patched(fake, _ctx(shop_default_salesman="Harsh Bhatia")):
        o = create_order(_body(session_ref=None), market=True)
        assert o["attribution_source"] == "default" and o["salesman_id"] == 2
    fake = _db()
    with _patched(fake, ctx):
        o = create_order(_body(session_ref=None), market=True)
        assert o["attribution_source"] == "unassigned" and o["salesman_id"] is None
        assert fake.rows("shop_customers")[0]["first_ref"] is None
    # a recognised phone: admin assignment > sticky > link, whatever the device remembers
    recent = (NOW - timedelta(days=5)).isoformat()
    rec = {"id": 7, "phone": "97333001122", "salesman_id": None, "sticky_salesman_id": 2, "last_order_at": recent, "orders_count": 3}
    sm, how, conflict = resolve_salesman(ctx, phone="97333001122", session_ref=None, customer=rec)
    assert sm["id"] == 2 and how == "sticky" and not conflict
    sm, how, conflict = resolve_salesman(ctx, phone="97333001122", session_ref="furqan", customer=rec)
    assert sm["id"] == 2 and how == "sticky" and conflict
    sm, how, conflict = resolve_salesman(ctx, phone="97333001122", session_ref="furqan", customer={**rec, "salesman_id": 3})
    assert sm["id"] == 3 and how == "customer_admin" and conflict


@test("matrix: a staff order for a shop whose recorded rep is someone else is flagged and gets a 'conflict' event")
def _():
    from app.shop import create_order
    from app.attribution import staff_conflict
    assert not staff_conflict(None, SALESMEN[0]) and not staff_conflict({"sticky_salesman_id": 1}, SALESMEN[0])
    assert staff_conflict({"sticky_salesman_id": 2}, SALESMEN[0]) and staff_conflict({"salesman_id": 2, "sticky_salesman_id": 1}, SALESMEN[0])
    assert not staff_conflict({"salesman_id": 2}, None)
    cust = {"id": 7, "phone": "97333001122", "salesman_id": None, "sticky_salesman_id": 2, "orders_count": 1,
            "last_order_at": (NOW - timedelta(days=3)).isoformat(), "device_ids": []}
    fake = _db(shop_customers=[cust])
    with _patched(fake, _ctx()):
        body = {"lines": [{"item_code": "T02", "qty": 3}], "customer": {"name": "Test Shop", "phone": "33001122"}}
        o = create_order(body, staff_email="rep@example.com")       # rep 1 orders for rep 2's shop
        assert o["salesman_id"] == 1 and o["attribution_source"] == "staff" and o["attribution_conflict"] is True
        evs = _events(fake, o["id"])
        assert [e["event"] for e in evs] == ["created", "conflict"], evs
        assert evs[0]["detail"]["conflict"] is True
        assert evs[1]["detail"] == {"recorded_salesman_id": 2, "placed_for_salesman_id": 1, "placed_by": "rep@example.com"}
        assert fake.rows("shop_customers")[0]["sticky_salesman_id"] == 2, "the shop's record is untouched"
        ins = [c for c in fake.writes("shop_order_events") if c[0] == "insert"]
        assert len(ins) == 1 and isinstance(ins[0][2], list) and [x["event"] for x in ins[0][2]] == ["created", "conflict"], \
            "created + conflict go in ONE insert: an informational row can never discard a complete order on its own"
        # the same rep for his own shop: no flag, no extra event
        fake.rows("shop_customers")[0]["sticky_salesman_id"] = 1
        o = create_order({**body, "customer": {"name": "Test Shop", "phone": "33001122"}}, staff_email="rep@example.com")
        assert o["attribution_conflict"] is False and [e["event"] for e in _events(fake, o["id"])] == ["created"]


@test("matrix: reassigning an order never touches the merchant; the explicit customer assignment does, with a reason and two audit rows")
def _():
    from app.attribution import assign_customer, customer_rep
    from app.shop import assign_order, resolve_salesman
    cust = {"id": 7, "phone": "97333001122", "shop": "Test Shop", "salesman_id": None, "sticky_salesman_id": None,
            "orders_count": 1, "last_order_at": NOW.isoformat(), "device_ids": []}
    fake = _db(shop_customers=[cust], shop_orders=[_order(1, salesman_id=None, salesman_name=None, customer_id=7)],
               shop_order_lines=_lines(1))
    with _patched(fake, _ctx()):
        o = assign_order(1, 2, actor="admin@example.com", reason="territory", is_admin=True)
        assert o["salesman_id"] == 2
        assert fake.rows("shop_customers")[0]["sticky_salesman_id"] is None and fake.rows("shop_customers")[0]["salesman_id"] is None
        assert not fake.writes("shop_customers"), "an order assignment writes nothing on the merchant"
        _raises(lambda: assign_customer(7, 2, "admin@example.com", ""), "reason")
        _raises(lambda: assign_customer(7, 2, "admin@example.com", "ok"), "reason")
        _raises(lambda: assign_customer(7, 99, "admin@example.com", "unknown rep"), "unknown or inactive")
        _raises(lambda: assign_customer(404, 2, "admin@example.com", "no such shop"), "unknown merchant")
        assert fake.rows("audit_log") == [] and fake.rows("shop_admin_audit") == [], "a refused assignment audits nothing"
        out = assign_customer(7, 2, "admin@example.com", "Harsh covers Manama now")
        assert out["changed"] and out["from"] is None and out["to"] == 2 and out["to_name"] == "Harsh Bhatia"
        c = fake.rows("shop_customers")[0]
        assert c["salesman_id"] == 2 and c["assigned_by"] == "admin@example.com" and c["assigned_at"]
        a = fake.rows("audit_log")[-1]
        assert a["event"] == "shop.customer_assign" and a["user_email"] == "admin@example.com"
        assert a["detail"]["from"] is None and a["detail"]["to"] == 2 and a["detail"]["reason"] == "Harsh covers Manama now"
        m = fake.rows("shop_admin_audit")[-1]
        assert (m["entity"], m["entity_id"], m["action"]) == ("shop_customer", "7", "assign")
        assert m["before"]["salesman_id"] is None and m["after"]["salesman_id"] == 2 and m["after"]["reason"] == "Harsh covers Manama now"
        # the admin assignment now beats a live link from another rep — flagged, not switched
        sm, how, conflict = resolve_salesman(_ctx(), phone=c["phone"], session_ref="furqan", customer=c)
        assert sm["id"] == 2 and how == "customer_admin" and conflict
        # same rep again: nothing changes, nothing audited twice
        n = len(fake.rows("shop_admin_audit"))
        out = assign_customer(7, 2, "admin@example.com", "already his")
        assert out["changed"] is False and len(fake.rows("shop_admin_audit")) == n
        # reassign to rep 1 records from → to with names
        out = assign_customer(7, 1, "admin@example.com", "Harsh left")
        assert out["from"] == 2 and out["from_name"] == "Harsh Bhatia" and out["to"] == 1
        rep = customer_rep(7)
        assert rep["salesman_id"] == 1 and rep["salesman_name"] == "Furqan Ahmed" and rep["sticky_salesman_id"] is None


@test("sticky: settles once at confirm or deliver, never on other moves, never over an admin assignment or an existing value")
def _():
    from app.attribution import settle_sticky
    from app.shop import set_status
    fake = _db(shop_customers=[{"id": 7, "phone": "97333001122", "salesman_id": None, "sticky_salesman_id": None}],
               shop_orders=[_order(1, customer_id=7)], shop_order_lines=_lines(1))
    with _patched(fake):
        assert settle_sticky(fake, _order(1, customer_id=7), "packed") is False
        assert settle_sticky(fake, _order(1, customer_id=None), "confirmed") is False
        assert settle_sticky(fake, _order(1, customer_id=7, salesman_id=None), "confirmed") is False
        assert settle_sticky(fake, _order(1, customer_id=7, is_test=True), "confirmed") is False, "a test order settles nothing"
        assert fake.rows("shop_customers")[0]["sticky_salesman_id"] is None and not fake.writes("shop_customers")
        set_status(1, "confirmed", None, actor="rep@example.com")
        assert fake.rows("shop_customers")[0]["sticky_salesman_id"] == 1
        # one conditional write, no read-then-write: the row must still have no rep on file when the
        # database applies it, so two reps confirming a new merchant's orders at once cannot both win
        w = [c for c in fake.writes("shop_customers") if c[0] == "update"][-1]
        assert ("is", "salesman_id", "null", False) in w[3] and ("is", "sticky_salesman_id", "null", False) in w[3], w[3]
        assert not [c for c in fake.calls if c[0] == "select" and c[1] == "shop_customers" and "sticky_salesman_id" in str(c[2])
                    and c[4].get("limit") == 1], "no pre-read the write could go stale against"
        assert settle_sticky(fake, _order(1, customer_id=7, salesman_id=2), "delivered") is False, "existing value untouched"
        assert fake.rows("shop_customers")[0]["sticky_salesman_id"] == 1
    fake = _db(shop_customers=[{"id": 7, "phone": "97333001122", "salesman_id": 3, "sticky_salesman_id": None}])
    with _patched(fake):
        assert settle_sticky(fake, _order(1, customer_id=7), "confirmed") is False, "an admin assignment is on file"
        assert fake.rows("shop_customers")[0]["sticky_salesman_id"] is None
    # the memory layer failing never fails the status move
    fake = _db(shop_customers=[{"id": 7, "phone": "97333001122", "salesman_id": None, "sticky_salesman_id": None}],
               shop_orders=[_order(1, customer_id=7, status="out_for_delivery")], shop_order_lines=_lines(1))
    fake.fail[("update", "shop_customers")] = RuntimeError("customers down")
    with _patched(fake):
        o = set_status(1, "delivered", None, actor="rep@example.com")
        assert o["status"] == "delivered"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. the admin audit
# ═══════════════════════════════════════════════════════════════════════════════

@test("audit: record never raises (missing table, failing insert), strips volatile keys, diffs, lists newest first with a hint pre-migration")
def _():
    from app import shop_audit
    fake = _db(missing={"shop_admin_audit"})
    with _patched(fake):
        assert shop_audit.record("admin@example.com", "salesman", 1, "update", {"a": 1}, {"a": 2}) is False
        assert shop_audit.list_audit() == {"rows": [], "count": 0, "hint": shop_audit.MISSING_HINT}
        assert shop_audit.snapshot("salesmen", 1)["name"] == "Furqan Ahmed" and shop_audit.snapshot("salesmen", 99) is None
        assert shop_audit.snapshot("salesmen", None) is None
    fake = _db()
    with _patched(fake):
        ok = shop_audit.record("admin@example.com", "salesman", 1, "update",
                               {"name": "A", "updated_at": "t1", "phone": "1", "link": "x"},
                               {"name": "B", "updated_at": "t2", "phone": "1", "link": "y", "ua": "Mozilla"})
        assert ok is True
        row = fake.rows("shop_admin_audit")[0]
        assert row["actor"] == "admin@example.com" and row["entity_id"] == "1" and row["action"] == "update"
        assert row["before"] == {"name": "A", "phone": "1"} and row["after"] == {"name": "B", "phone": "1"}
        shop_audit.record("admin@example.com", "campaign", 5, "delete", {"title": "x"}, None)
        out = shop_audit.list_audit()
        assert out["count"] == 2 and [r["entity"] for r in out["rows"]] == ["campaign", "salesman"]
        assert out["rows"][1]["changes"] == {"name": {"from": "A", "to": "B"}}
        assert out["rows"][0]["changes"] == {"title": {"from": "x", "to": None}}
        assert shop_audit.list_audit(entity="campaign")["count"] == 1
        # a huge value is truncated, the row still identifiable
        shop_audit.record("admin@example.com", "campaign", 6, "create", None, {"id": 6, "title": "t", "blob": "x" * 20_000})
        big = fake.rows("shop_admin_audit")[-1]["after"]
        assert big["id"] == 6 and big["_truncated"] is True and "blob" not in big
    assert shop_audit.diff({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == {"b": {"from": 2, "to": 3}, "c": {"from": None, "to": 4}}
    assert shop_audit.trim(None) is None and shop_audit.trim("x") == {"value": "x"}


@test("audit: settings_change records only the keys the request named and only when they moved; target imports snapshot each row")
def _():
    from app import shop_audit
    fake = _db()
    with _patched(fake):
        cur = {"shop_min_order_bhd": "20", "shop_order_prefix": "YQ"}
        assert shop_audit.settings_change("admin@example.com", cur, {"shop_min_order_bhd": "25"},
                                          {"shop_min_order_bhd": "25", "shop_order_prefix": "YQ"}) is True
        row = fake.rows("shop_admin_audit")[-1]
        assert row["entity"] == "settings" and row["entity_id"] == "shop"
        assert row["before"] == {"shop_min_order_bhd": "20"} and row["after"] == {"shop_min_order_bhd": "25"}
        assert shop_audit.settings_change("admin@example.com", cur, {"shop_order_prefix": "YQ"}, cur) is False
        assert shop_audit.settings_change("admin@example.com", cur, {"unknown_key": "1"}, cur) is False
        assert len(fake.rows("shop_admin_audit")) == 1
    fake = _db(salesman_targets=[{"id": 1, "salesman": "FURQAN", "period": "", "target_bhd": 1000, "kickback_t1": 0.05}])
    with _patched(fake):
        rows = [{"salesman": "FURQAN", "period": "", "target_bhd": 1200, "kickback_t1": 0.05},
                {"salesman": "HARSH B", "period": "2026-10", "target_bhd": 800}]
        # the script's order: snapshot, upsert, THEN audit — an upsert that fails leaves no audit row
        # (the table is append-only, so a row for an import that never happened could never be fixed)
        befores = shop_audit.target_import_snapshots(fake, rows)
        assert befores == {"FURQAN|standing": fake.rows("salesman_targets")[0], "HARSH B|2026-10": None}
        fake.fail[("upsert", "salesman_targets")] = RuntimeError("targets down")
        try:
            fake.table("salesman_targets").upsert(rows, on_conflict="salesman,period").execute()
            raise AssertionError("the upsert was meant to fail")
        except RuntimeError:
            pass
        assert fake.rows("shop_admin_audit") == [], "no audit row for an import that did not happen"
        fake.fail.pop(("upsert", "salesman_targets"))
        fake.table("salesman_targets").upsert(rows, on_conflict="salesman,period").execute()
        n = shop_audit.record_target_import(fake, rows, actor="fahmed · import Targets Oct.xlsx", befores=befores)
        assert n == 2
        got = fake.rows("shop_admin_audit")
        assert got[0]["entity_id"] == "FURQAN|standing" and got[0]["before"]["target_bhd"] == 1000 and got[0]["after"]["target_bhd"] == 1200
        assert got[1]["entity_id"] == "HARSH B|2026-10" and got[1]["before"] is None and got[1]["action"] == "import"
        assert all(r["actor"] == "fahmed · import Targets Oct.xlsx" for r in got), "the operator is on the row, not only the file"
    src = (ROOT / "scripts" / "import_targets.py").read_text(encoding="utf-8")
    assert src.index("target_import_snapshots(c, rows)") < src.index('upsert(rows, on_conflict="salesman,period")') \
        < src.index("record_target_import(c, rows"), "snapshot → upsert → audit, in that order"
    assert '"--actor"' in src and "getpass.getuser()" in src, "the operator comes from --actor or the OS user"


@test("audit: the call sites — settings, rules, campaigns, salesmen and upcoming edits each leave a before/after row")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app.auth import CurrentUser, get_current_user
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="a", email="admin@example.com", role="admin")
    try:
        rule = {"id": 3, "name": "Cable tier", "kind": "qty_tier", "scope": {"item_codes": ["T02"]}, "min_qty": 12, "pct_off": 5,
                "is_active": True, "priority": 100, "stackable": False, "uses": 0}
        camp = {"id": 4, "title": "Cables week", "placement": ["hero"], "audience": "all", "is_active": True, "cta_to": "/shop"}
        fake = _db(discount_rules=[rule], shop_campaigns=[camp],
                   shop_upcoming_items=[{"id": 5, "name_en": "W1", "status": "draft", "sort_order": 1}],
                   app_settings=[{"key": "shop_market_url", "value": "https://yqmarketplace.com"},
                                 {"key": "shop_min_order_bhd", "value": "20"}])
        with _patched(fake, _ctx()):
            c = TestClient(m.app)
            r = c.put("/settings/shop", json={"settings": {"shop_min_order_bhd": "25"}})
            assert r.status_code == 200, r.text[:200]
            r = c.patch("/shop/rules/3", json={"pct_off": 7})
            assert r.status_code == 200, r.text[:200]
            r = c.patch("/shop/campaigns/4", json={"title": "Cables month"})
            assert r.status_code == 200, r.text[:200]
            r = c.patch("/shop/salesmen/2", json={"user_email": "harsh@example.com"})
            assert r.status_code == 200, r.text[:200]
            r = c.patch("/shop/upcoming/5", json={"status": "published"})
            assert r.status_code == 200, r.text[:200]
            r = c.post("/shop/upcoming/settings", json={"upcoming_enabled": False})
            assert r.status_code == 200, r.text[:200]
            r = c.delete("/shop/rules/3")
            assert r.status_code == 200, r.text[:200]
            # the two admin settings writes outside shop_api (app/main.py) are audited too
            r = c.put("/settings/costing", json={"dealer_discount": 0.2})
            assert r.status_code == 200, r.text[:200]
            r = c.put("/settings/agents", json={"exclude_sim": False})
            assert r.status_code == 200, r.text[:200]
            r = c.put("/settings/agents", json={"exclude_sim": False})        # unchanged: no second row
            assert r.status_code == 200, r.text[:200]
            rows = fake.rows("shop_admin_audit")
            got = [(x["entity"], x["entity_id"], x["action"]) for x in rows]
            assert got == [("settings", "shop", "update"), ("discount_rule", "3", "update"), ("campaign", "4", "update"),
                           ("salesman", "2", "update"), ("upcoming", "5", "update"), ("settings", "upcoming", "update"),
                           ("discount_rule", "3", "delete"), ("settings", "costing", "update"), ("settings", "agents", "update")], got
            assert rows[7]["before"] == {"dealer_discount": 0.18} and rows[7]["after"] == {"dealer_discount": 0.2}
            assert rows[8]["before"] == {"exclude_sim": True} and rows[8]["after"] == {"exclude_sim": False}
            assert rows[0]["before"] == {"shop_min_order_bhd": "20"} and rows[0]["after"] == {"shop_min_order_bhd": "25"}
            assert rows[1]["before"]["pct_off"] == 5 and rows[1]["after"]["pct_off"] == 7
            assert rows[2]["before"]["title"] == "Cables week" and rows[2]["after"]["title"] == "Cables month"
            assert rows[3]["before"]["user_email"] is None and rows[3]["after"]["user_email"] == "harsh@example.com"
            assert rows[4]["before"]["status"] == "draft" and rows[4]["after"]["status"] == "published"
            assert rows[5]["after"] == {"upcoming_enabled": "0"}
            assert rows[6]["before"]["name"] == "Cable tier" and rows[6]["after"] is None
            assert all(x["actor"] == "admin@example.com" for x in rows)
            # the audit list route
            r = c.get("/shop/audit?entity=discount_rule")
            assert r.status_code == 200 and r.json()["count"] == 2 and r.json()["rows"][0]["changes"]["name"]["to"] is None
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)
        import app.settings as app_settings
        app_settings._cache.update(at=0.0, vals=None)       # the costing write above must not leak into later tests


@test("routes: a staff cancel emails the shop the reason's label only — the note is the office's record; other moves carry the note")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    import app.shop_notify as notify
    from app.auth import CurrentUser, get_current_user
    from app.shop_pipeline import customer_cancel_text
    assert customer_cancel_text("out_of_stock") == "Out of stock" and customer_cancel_text("customer_request") == "As you asked"
    assert customer_cancel_text("duplicate") == "Duplicate order" and customer_cancel_text("price_issue") == "Price issue"
    assert customer_cancel_text("other") is None and customer_cancel_text("test") is None and customer_cancel_text(None) is None
    sent: list[tuple] = []
    saved = notify.notify_status
    notify.notify_status = lambda order_id, status, note=None: sent.append((order_id, status, note)) or {}
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="a", email="admin@example.com", role="admin")
    try:
        fake = _db(shop_orders=[_order(i, customer_email="shop@example.com") for i in (1, 2, 3)],
                   shop_order_lines=_lines(1) + _lines(2) + _lines(3))
        fake.columns["shop_orders"] = ORDER_COLS_R3
        with _patched(fake, _ctx()):
            c = TestClient(m.app)
            r = c.post("/shop/orders/1/status", json={"status": "cancelled", "reason_code": "duplicate", "note": "duplicate / fake number"})
            assert r.status_code == 200, r.text[:200]
            r = c.post("/shop/orders/2/status", json={"status": "cancelled", "reason_code": "other", "note": "price too low for this shop"})
            assert r.status_code == 200, r.text[:200]
            r = c.post("/shop/orders/3/status", json={"status": "confirmed", "note": "Thursday with Furqan"})
            assert r.status_code == 200, r.text[:200]
        assert sent == [(1, "cancelled", "Duplicate order"), (2, "cancelled", None), (3, "confirmed", "Thursday with Furqan")], sent
        # the note itself is the record — cancel_reason and the event — and never the email
        assert fake.rows("shop_orders")[0]["cancel_reason"] == "duplicate / fake number"
        assert fake.rows("shop_orders")[1]["cancel_reason"] == "price too low for this shop"
        assert _events(fake, 2)[-1]["detail"]["note"] == "price too low for this shop"
        # the UI says so where the note is typed (both order views share the picker)
        src = (ROOT / "web" / "src" / "pages" / "shop-ops" / "OrderActions.tsx").read_text(encoding="utf-8")
        assert "Internal note" in src and "This note stays in the office record" in src
    finally:
        notify.notify_status = saved
        m.app.dependency_overrides.pop(get_current_user, None)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. routes and gates
# ═══════════════════════════════════════════════════════════════════════════════

ADMIN_ROUTES = {
    ("POST", "/shop/orders/{order_id}/payment"), ("POST", "/shop/orders/{order_id}/return"),
    ("POST", "/shop/orders/{order_id}/invoice"), ("POST", "/shop/customers/{customer_id}/assign"),
    ("GET", "/shop/focus-recon"), ("GET", "/shop/audit"),
}


@test("gates: every new endpoint is require_admin; a salesman login gets 403 on each and never reaches the data")
def _():
    from fastapi.routing import APIRoute
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth
    from app.auth import CurrentUser, get_current_user
    table = {}
    for r in m.app.routes:
        if isinstance(r, APIRoute):
            for meth in r.methods:
                table[(meth, r.path)] = [d.call for d in r.dependant.dependencies]
    for key in ADMIN_ROUTES:
        assert key in table, f"route missing: {key}"
        assert auth.require_admin in table[key], f"{key} must be admin-gated"
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="r", email="rep@example.com", role="salesman")
    try:
        fake = _db(shop_orders=[_order(1, status="delivered")], shop_order_lines=_lines(1),
                   shop_customers=[{"id": 7, "phone": "97333001122"}])
        with _patched(fake, _ctx()):
            c = TestClient(m.app)
            calls = [
                c.post("/shop/orders/1/payment", json={"status": "paid"}),
                c.post("/shop/orders/1/return", json={"lines": [{"line_id": 11, "qty": 1}], "reason": "damaged"}),
                c.post("/shop/orders/1/invoice", json={"focus_invoice_no": "SI-1"}),
                c.post("/shop/customers/7/assign", json={"salesman_id": 1, "reason": "mine now"}),
                c.get("/shop/focus-recon"), c.get("/shop/audit"),
            ]
            assert [r.status_code for r in calls] == [403] * 6, [(r.status_code, r.text[:80]) for r in calls]
            assert fake.writes() == [] and fake.rows("shop_order_events") == [], "nothing written, nothing read back"
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)


@test("routes: an admin records payment, a return and an invoice; the order detail carries the labels and the shop's rep")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app.auth import CurrentUser, get_current_user
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="a", email="admin@example.com", role="admin")
    try:
        fake = _db(missing={"v_shop_focus_recon"},
                   shop_orders=[_order(1, status="delivered", customer_id=7)], shop_order_lines=_lines(1),
                   shop_customers=[{"id": 7, "phone": "97333001122", "shop": "Test Shop", "salesman_id": None, "sticky_salesman_id": 1}])
        fake.columns["shop_orders"] = ORDER_COLS_R3
        with _patched(fake, _ctx()):
            c = TestClient(m.app)
            r = c.post("/shop/orders/1/payment", json={"status": "paid", "method": "cash"})
            assert r.status_code == 200 and r.json()["payment_label"] == "Paid", r.text[:200]
            r = c.post("/shop/orders/1/payment", json={"status": "later"})
            assert r.status_code == 400 and "unpaid, partial or paid" in r.json()["detail"]
            r = c.post("/shop/orders/1/return", json={"lines": [{"line_id": 12, "qty": 1}], "reason": "wrong colour"})
            assert r.status_code == 200 and r.json()["value_bhd"] == 2.0, r.text[:200]
            r = c.post("/shop/orders/1/return", json={"lines": [{"line_id": 12, "qty": 5}], "reason": "wrong colour"})
            assert r.status_code == 400
            r = c.post("/shop/orders/1/invoice", json={"focus_invoice_no": "SI-26-09-0042"})
            assert r.status_code == 200 and r.json()["focus_invoice_no"] == "SI-26-09-0042"
            r = c.post("/shop/orders/404/invoice", json={"focus_invoice_no": "SI-1"})
            assert r.status_code == 404
            d = c.get("/shop/orders/1").json()
            assert d["payment_status"] == "paid" and d["payment_label"] == "Paid" and d["returned_bhd"] == 2.0
            assert d["focus_invoice_no"] == "SI-26-09-0042" and d["cancel_reasons"]["other"] == "Other"
            assert d["customer"]["sticky_name"] == "Furqan Ahmed" and d["customer"]["salesman_id"] is None
            assert [e["event"] for e in d["events"]] == ["payment", "returned", "invoice"]
            assert "ip_hash" not in d
            audit = [a["event"] for a in fake.rows("audit_log")]
            assert audit == ["shop.order_payment", "shop.order_return", "shop.order_invoice"], audit
            r = c.get("/shop/focus-recon")
            assert r.status_code == 200 and r.json()["rows"] == [] and "hint" in r.json(), "no view on the fake: hint, not 500"
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)


@test("routes: the status route needs a cancel reason (400 with the list) and passes the Focus invoice at Delivered")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app.auth import CurrentUser, get_current_user
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="a", email="admin@example.com", role="admin")
    try:
        fake = _db(shop_orders=[_order(1), _order(2, status="out_for_delivery")], shop_order_lines=_lines(1) + _lines(2))
        fake.columns["shop_orders"] = ORDER_COLS_R3
        with _patched(fake, _ctx()):
            c = TestClient(m.app)
            r = c.post("/shop/orders/1/status", json={"status": "cancelled"})
            assert r.status_code == 400 and "out of stock" in r.json()["detail"].lower(), r.text[:200]
            assert fake.rows("shop_orders")[0]["status"] == "new"
            r = c.post("/shop/orders/1/status", json={"status": "cancelled", "reason_code": "other"})
            assert r.status_code == 400 and "note" in r.json()["detail"].lower()
            r = c.post("/shop/orders/1/status", json={"status": "cancelled", "reason_code": "duplicate", "note": "same as 0002"})
            assert r.status_code == 200 and r.json()["order"]["cancel_reason"] == "same as 0002"
            assert fake.rows("shop_orders")[0]["cancel_reason_code"] == "duplicate"
            r = c.post("/shop/orders/2/status", json={"status": "delivered", "focus_invoice_no": "SI-7"})
            assert r.status_code == 200 and r.json()["order"]["focus_invoice_no"] == "SI-7"
            a = [x for x in fake.rows("audit_log") if x["event"] == "shop.order_status"]
            assert a[0]["detail"]["reason_code"] == "duplicate" and a[1]["detail"]["focus_invoice_no"] == "SI-7"
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)


@test("routes: assign with also_customer — an admin binds the shop (audited); a rep taking an order cannot")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app.auth import CurrentUser, get_current_user
    cust = {"id": 7, "phone": "97333001122", "shop": "Test Shop", "salesman_id": None, "sticky_salesman_id": None, "device_ids": []}
    fake = _db(shop_customers=[cust], shop_orders=[_order(1, salesman_id=None, salesman_name=None, customer_id=7)],
               shop_order_lines=_lines(1),
               user_roles=[{"email": "rep@example.com", "role": "salesman", "status": "active", "features": ["Shop Orders"]}])
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="r", email="rep@example.com", role="salesman")
    try:
        with _patched(fake, _ctx()):
            c = TestClient(m.app)
            r = c.post("/shop/orders/1/assign", json={"salesman_id": 1, "also_customer": True})
            assert r.status_code == 403, r.text[:200]
            assert fake.rows("shop_orders")[0]["salesman_id"] is None
            r = c.post("/shop/orders/1/assign", json={"salesman_id": 1})
            assert r.status_code == 200 and r.json()["customer_assign"] is None
            assert fake.rows("shop_customers")[0]["salesman_id"] is None and fake.rows("shop_customers")[0]["sticky_salesman_id"] is None
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="a", email="admin@example.com", role="admin")
    try:
        with _patched(fake, _ctx()):
            c = TestClient(m.app)
            r = c.post("/shop/orders/1/assign", json={"salesman_id": 2, "reason": "Harsh's area", "also_customer": True})
            assert r.status_code == 200, r.text[:200]
            ca = r.json()["customer_assign"]
            assert ca["changed"] and ca["to"] == 2 and ca["reason"] == "Harsh's area"
            assert fake.rows("shop_customers")[0]["salesman_id"] == 2 and fake.rows("shop_orders")[0]["salesman_id"] == 2
            evs = [a["event"] for a in fake.rows("audit_log")]
            assert evs[-2:] == ["shop.customer_assign", "shop.order_assign"] or evs[-2:] == ["shop.order_assign", "shop.customer_assign"], evs
            assert fake.rows("shop_admin_audit")[-1]["entity"] == "shop_customer"
            # no reason typed: the order is the reason
            r = c.post("/shop/orders/1/assign", json={"salesman_id": 1, "also_customer": True})
            assert r.status_code == 200 and r.json()["customer_assign"]["reason"] == "Assigned with order YQ-2609-0001"
            r = c.post("/shop/customers/7/assign", json={"salesman_id": 2, "reason": ""})
            assert r.status_code == 400 and "reason" in r.json()["detail"].lower()
            r = c.post("/shop/customers/404/assign", json={"salesman_id": 2, "reason": "no such shop"})
            assert r.status_code == 404
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)


@test("list: rows carry payment_status, the gap columns and min_order_bhd; the R3 columns join once they exist and leave after a reverse")
def _():
    import app.shop as s
    from app.shop import has_column, list_orders
    rows = [_order(1, order_kind="small", minimum_gap_bhd=7.15, total_bhd=12.85),
            _order(2, status="delivered", payment_status="paid")]
    fake = _db(shop_orders=rows, app_settings=[{"key": "shop_min_order_bhd", "value": "20"}])
    fake.columns["shop_orders"] = ORDER_COLS_PRE_R3
    with _patched(fake):
        out = list_orders()
        assert out["min_order_bhd"] == 20.0 and out["count"] == 2
        by = {r["id"]: r for r in out["orders"]}
        assert by[1]["order_kind"] == "small" and by[1]["minimum_gap_bhd"] == 7.15 and by[1]["payment_status"] == "unpaid"
        assert by[2]["payment_status"] == "paid" and "returned_bhd" not in by[2]
        assert "notify_result" not in by[1] and by[1]["notify_failed"] is not None
    for r in rows:
        r.update(returned_bhd=None, cancel_reason_code=None)
    fake = _db(shop_orders=rows)
    fake.columns["shop_orders"] = ORDER_COLS_R3
    with _patched(fake):
        assert has_column("shop_orders", "returned_bhd") is True
        out = list_orders()
        assert all("returned_bhd" in r and "cancel_reason_code" in r for r in out["orders"])
        # the reverse script drops the columns under a cached hit: one re-read, no page lost
        fake.columns["shop_orders"] = ORDER_COLS_PRE_R3
        n = len(fake.calls)
        out = list_orders()
        assert out["count"] == 2
        reads = [c for c in fake.calls[n:] if c[0] == "select" and c[1] == "shop_orders" and c[4]["count"] == "exact"]
        assert len(reads) == 2 and "returned_bhd" in str(reads[0][2]) and "returned_bhd" not in str(reads[1][2]), \
            [c[2] for c in reads]
        assert "shop_orders.returned_bhd" not in s._col_cache and "shop_orders.cancel_reason_code" not in s._col_cache


# ═══════════════════════════════════════════════════════════════════════════════
# 9. local Postgres replay (SKIPs cleanly without the local cluster)
# ═══════════════════════════════════════════════════════════════════════════════

# A scratch database on the portable local cluster (port 55432) holding a SCHEMA-ONLY copy of the
# production tables the reconciliation view reads (pg_dump --schema-only, no rows, no PII) with
# scripts/r3_pipeline_migration.sql applied. Everything below runs inside one transaction that is
# rolled back, so the database is untouched afterwards and the test can run again.
LOCAL_DSN = os.environ.get("YQ_LOCAL_PG", "postgresql://postgres@localhost:55432/r3_pipeline")


@test("local replay: v_shop_focus_recon flags (incl. invoice_reused), the append-only audit (update/delete/truncate) and the cancel-code CHECK on a schema copy (SKIP without the cluster)")
def _():
    try:
        import psycopg
    except ImportError:
        print("  SKIP: psycopg not installed")
        return
    try:
        conn = psycopg.connect(LOCAL_DSN, connect_timeout=2)
    except Exception as e:  # noqa: BLE001 — no local cluster / no scratch database here
        print(f"  SKIP: local Postgres unreachable ({type(e).__name__})")
        return
    try:
        cur = conn.cursor()
        cur.execute("select to_regclass('public.v_shop_focus_recon'), to_regclass('public.v_sales'), to_regclass('public.shop_admin_audit')")
        if None in cur.fetchone():
            print("  SKIP: r3_pipeline database has no schema copy + migration")
            return
        cur.execute("""
            insert into salesmen (id, name, referral_code, focus_name, user_email) overriding system value
            values (901, 'Rep A', 'rep-a', 'REP A', 'a@example.com'), (902, 'Rep B', 'rep-b', 'REP B', null)""")
        cur.execute("""
            insert into shop_orders (id, order_no, token, status, customer_name, customer_phone, customer_shop, salesman_id,
                                     salesman_name, total_bhd, total_confirmed_bhd, delivered_at, focus_invoice_no)
            overriding system value values
              (9001, 'YQ-R3-1', 'tokR3-1xxxxxxxxxxxxxxxxxx', 'delivered', 'C1', '97311111111', 'Shop 1', 901, 'Rep A', 12.850, 12.850, now(), null),
              (9002, 'YQ-R3-2', 'tokR3-2xxxxxxxxxxxxxxxxxx', 'delivered', 'C2', '97322222222', 'Shop 2', 901, 'Rep A', 10.000, 10.000, now(), 'SI-9'),
              (9003, 'YQ-R3-3', 'tokR3-3xxxxxxxxxxxxxxxxxx', 'delivered', 'C3', '97333333333', 'Shop 3', 901, 'Rep A', 12.000, 10.000, now(), ' si-10 '),
              (9004, 'YQ-R3-4', 'tokR3-4xxxxxxxxxxxxxxxxxx', 'delivered', 'C4', '97344444444', 'Shop 4', 902, 'Rep B', 5.000, null, now(), 'SI-404'),
              (9005, 'YQ-R3-5', 'tokR3-5xxxxxxxxxxxxxxxxxx', 'confirmed', 'C5', '97355555555', 'Shop 5', 901, 'Rep A', 7.000, null, null, 'SI-9'),
              (9006, 'YQ-R3-6', 'tokR3-6xxxxxxxxxxxxxxxxxx', 'delivered', 'C6', '97366666666', 'Shop 6', 901, 'Rep A', 10.000, 10.000, now(), ' si-9 ')""")
        cur.execute("""
            insert into orders (id, invoice_no, order_date, customer_name, salesman) overriding system value
            values (9101, 'SI-9', '2026-09-20', 'Shop 2', 'REP A'), (9102, 'SI-10', '2026-09-21', 'Shop 3', 'REP B')""")
        cur.execute("""
            insert into order_lines (id, invoice_no, order_id, line_no, line_date, item_name, quantity, rate_bhd, gross_bhd,
                                     taxable_bhd, total_amount_bhd, warehouse_name) overriding system value
            values (9201, 'SI-9', 9101, 1, '2026-09-20', 'X', 2, 5.0, 10.0, 9.091, 10.0, 'REP A'),
                   (9202, 'SI-9', 9101, 2, '2026-09-20', 'Y', 0, 0, 0, 0, 0, 'REP A'),
                   (9203, 'SI-10', 9102, 1, '2026-09-21', 'X', 1, 9.5, 9.5, 8.636, 9.5, 'REP B')""")
        cur.execute("""
            select order_no, missing_invoice, invoice_not_found, salesman_mismatch, amount_mismatch, invoice_total_bhd,
                   focus_salesman, amount_diff_bhd, order_total_bhd, invoice_reused
            from v_shop_focus_recon where order_no like 'YQ-R3-%' order by order_no""")
        rows = {r[0]: r[1:] for r in cur.fetchall()}
        assert set(rows) == {"YQ-R3-1", "YQ-R3-2", "YQ-R3-3", "YQ-R3-4", "YQ-R3-6"}, "delivered orders only"
        r1, r2, r3, r4, r6 = rows["YQ-R3-1"], rows["YQ-R3-2"], rows["YQ-R3-3"], rows["YQ-R3-4"], rows["YQ-R3-6"]
        assert r1[:4] == (True, False, False, False) and r1[4] is None and r1[8] is False, r1
        assert r2[:4] == (False, False, False, False) and float(r2[4]) == 10.0 and r2[5] == "REP A" and float(r2[6]) == 0, r2
        # ' si-10 ' joins SI-10 (case / whitespace insensitive); Focus says REP B and 9.500 vs the CONFIRMED 10.000
        assert r3[:4] == (False, False, True, True) and float(r3[4]) == 9.5 and r3[5] == "REP B" and float(r3[6]) == -0.5, r3
        assert float(r3[7]) == 10.0, "the confirmed total is what Focus is compared with"
        assert r4[:4] == (False, True, False, False) and r4[4] is None and float(r4[7]) == 5.0 and r4[8] is False, r4
        # SI-9 sits on two DELIVERED orders (9002 and 9006, ' si-9 ' normalised): both read invoice_reused;
        # the confirmed 9005 with the same number does not count, and neither order is otherwise flagged
        assert r2[8] is True and r6[8] is True and r6[:4] == (False, False, False, False), (r2, r6)
        assert r3[8] is False, "one order per invoice reads clean"
        # the audit is append-only even for the owner of the table — rows, and the table as a whole
        cur.execute("insert into shop_admin_audit (actor, entity, entity_id, action, before, after) "
                    "values ('t@example.com', 'settings', 'shop', 'update', '{\"a\": 1}', '{\"a\": 2}') returning id")
        aid = cur.fetchone()[0]
        for stmt in (f"update shop_admin_audit set actor = 'x' where id = {aid}", f"delete from shop_admin_audit where id = {aid}",
                     "truncate shop_admin_audit"):
            cur.execute("savepoint s1")
            try:
                cur.execute(stmt)
                raise AssertionError(f"{stmt} must be refused")
            except psycopg.Error as e:
                assert "append-only" in str(e), str(e)
                cur.execute("rollback to savepoint s1")
        cur.execute("select count(*) from shop_admin_audit where id = %s", (aid,))
        assert cur.fetchone()[0] == 1, "the row is still there after the refused statements"
        # the cancel code CHECK
        cur.execute("savepoint s2")
        try:
            cur.execute("update shop_orders set cancel_reason_code = 'bogus' where id = 9001")
            raise AssertionError("an unknown cancel code must be refused")
        except psycopg.Error as e:
            assert "cancel_reason_code" in str(e), str(e)
            cur.execute("rollback to savepoint s2")
        cur.execute("update shop_orders set cancel_reason_code = 'out_of_stock', paid_at = now(), returned_bhd = 1.5 where id = 9001")
        assert cur.rowcount == 1
        # no grants to anon / authenticated / yq_readonly on the new objects
        cur.execute("""select count(*) from information_schema.role_table_grants
                       where table_schema = 'public' and table_name in ('shop_admin_audit', 'v_shop_focus_recon')
                         and grantee in ('anon', 'authenticated', 'yq_readonly')""")
        assert cur.fetchone()[0] == 0
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
