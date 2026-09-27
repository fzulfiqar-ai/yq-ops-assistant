"""R7d — the offer ledger and ops polish (plan §16 prerequisites, §23 item 4, §25 P2; audit OFF-2,
OFF-3, OFF-4, OFF-6, OFF-9, OFF-16, UX-10, SEC-11, SEC-19).

    python -m tests.test_r7d_ops

Same lightweight runner as tests/test_r7c_order_heart.py: an in-memory stand-in for the PostgREST
client (eq / is / in / contains / not filters, inserts that hand out ids, upserts that ignore
duplicates, updates that report what they touched, optional column lists so a read or write naming a
missing column raises like PostgREST, tables that do not exist yet, and the coupon RPCs with their
real conditions). Nothing reaches a database or the network (CI: SUPABASE_URL=https://ci.invalid).
All data is synthetic — made-up shops, reps, items, devices and numbers.

Covered:
  * the pricing engine's ledger: a line's discount split over the rules that made it (the shares add
    up to the line's discount exactly, a stackable rule is never credited with the whole line), the
    floor clamp flagged, cart-level rows, and a random-cart property test;
  * hold-outs (scope.bucket): validation, a stable uniform split, held / offered / no key, the quote
    and the order priced alike for one device, exp/var on the order event and the ledger snapshot,
    never on a coupon;
  * create_order writes the ledger in one insert (line ids mapped, placed vs born-confirmed), works
    before the migration, and never fails an order for its measurement row;
  * the coupon counter: reserved before anything is written, refused when none is left (nothing
    written), given back when the order fails after it, given back on cancel, taken again on an admin
    reopen, and the old counter only while the RPC is missing;
  * the rep's confirm / amend / delivery refresh the confirmed amounts (the order heart's own maths);
  * soft delete: a rule any order used is never deleted (409), archive / restore, archived rows out of
    pricing, the catalog and the lists, campaigns archived; has_coupons on the catalog;
  * the per-rule readout and the follow-up readout (pre-migration answers included);
  * follow-up exposures: once per rep per day, served and held-back rows, only the rep's own read;
  * rate limits per real client: CF-Connecting-IP behind the trusted proxy, per device / per order
    token buckets on the order, cancel and restock routes, the per-address net;
  * the nightly backup: never in OneDrive, 14 daily + 8 weekly kept, AES-256 7-Zip or a loud warning;
  * the Salesmen table (status pill, no one-click deactivate, pinned actions) and the Offers page as
    source checks;
  * the migration and its reverse as text, and a local Postgres replay (SKIPs without a cluster).
"""
from __future__ import annotations

import io
import json
import os
import random
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

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


MIGRATION = ROOT / "scripts" / "r7d_offer_ledger_migration.sql"
REVERSE = ROOT / "scripts" / "r7d_offer_ledger_reverse.sql"


# ── a table-aware fake PostgREST client ─────────────────────────────────────────

def _same(a, b) -> bool:
    return a == b or str(a) == str(b)


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.negate = "select", None, [], False
        self.cols, self.want_count, self.lim, self.desc = "*", None, None, False
        self.on_conflict, self.ignore_dups = "", False

    def select(self, cols="*", count=None, head=False):
        self.op, self.cols, self.want_count = "select", cols, count
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict="", ignore_duplicates=False, **_kw):
        self.op, self.payload, self.on_conflict, self.ignore_dups = "upsert", payload, on_conflict, ignore_duplicates
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

    def contains(self, col, val):
        return self._filter("contains", col, val)

    def gte(self, col, val):
        return self._filter("gte", col, val)

    def gt(self, col, val):
        return self._filter("gt", col, val)

    def lte(self, col, val):
        return self._filter("lte", col, val)

    def lt(self, col, val):
        return self._filter("lt", col, val)

    def order(self, col, desc=False, **_kw):
        self.desc = bool(desc)
        return self

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
            elif kind == "contains":
                want = json.loads(val) if isinstance(val, str) else list(val)
                have = got if isinstance(got, list) else (json.loads(got) if isinstance(got, str) else [])
                ok = all(w in have for w in want)
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

    def _check_cols(self, names):
        known = self.db.columns.get(self.table)
        for c in names:
            if known is not None and c and c not in known:
                raise RuntimeError(f"42703: column {self.table}.{c} does not exist")

    def execute(self):
        db = self.db
        db.calls.append((self.op, self.table, self.cols if self.op == "select" else self.payload, list(self.filters)))
        fail = db.fail.get((self.op, self.table))
        if fail is not None:
            raise fail
        if self.table in db.missing:
            raise RuntimeError(f'{{"code": "42P01", "message": "relation \\"public.{self.table}\\" does not exist"}}')
        rows = db.tables.setdefault(self.table, [])
        if self.op == "select":
            if self.cols != "*":
                self._check_cols([x.strip() for x in str(self.cols).split(",")])
            out = [dict(r) for r in rows if self._match(r)]
            if self.desc:
                out.reverse()
            data = out[: self.lim] if self.lim else out
            return SimpleNamespace(data=data, count=(len(out) if self.want_count else None))
        items = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.op in ("insert", "upsert", "update"):
            for p in items:
                self._check_cols(list(p or {}))
        if self.op in ("insert", "upsert"):
            keys = [k.strip() for k in self.on_conflict.split(",") if k.strip()]
            out = []
            for p in items:
                if self.op == "upsert" and keys:
                    dup = next((r for r in rows if all(_same(r.get(k), p.get(k)) for k in keys)), None)
                    if dup is not None:
                        if not self.ignore_dups:
                            dup.update(p)
                        continue
                r = dict(p)
                r.setdefault("id", db.next_id(self.table))
                if self.table == "shop_order_events":
                    r.setdefault("ts", db.clock())
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
            return SimpleNamespace(data=[dict(r) for r in gone], count=None)
        return SimpleNamespace(data=[], count=None)


class _FakeDB:
    def __init__(self, tables: dict | None = None, columns: dict | None = None, missing: set | None = None,
                 rpcs: bool = True):
        self.tables: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.columns: dict[str, set] = {k: set(v) for k, v in (columns or {}).items()}
        self.missing: set[str] = set(missing or ())
        self.calls: list[tuple] = []
        self.fail: dict[tuple, Exception] = {}
        self.rpc_calls: list[tuple] = []
        self.rpcs = rpcs
        self._ids: dict[str, int] = {}
        self._tick = 0

    def clock(self) -> str:
        self._tick += 1
        return (datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc) + timedelta(seconds=self._tick)).isoformat()

    def table(self, name):
        return _Query(self, name)

    def _rule(self, rid):
        return next((r for r in self.tables.get("discount_rules", []) if _same(r.get("id"), rid)), None)

    def rpc(self, name, params=None):
        self.rpc_calls.append((name, dict(params or {})))
        self.calls.append(("rpc", name, dict(params or {}), []))
        params = params or {}

        def run():
            if name == "shop_next_order_no":
                return SimpleNamespace(data=f"YQ-2609-{len(self.tables.get('shop_orders', [])) + 101:04d}", count=None)
            if not self.rpcs and name in ("shop_coupon_reserve", "shop_coupon_release"):
                raise RuntimeError("PGRST202: Could not find the function public." + name + " in the schema cache")
            if name == "shop_coupon_reserve":
                r = self._rule(params.get("p_rule_id"))
                if r is None or r.get("kind") != "coupon":
                    return SimpleNamespace(data=None, count=None)
                if not params.get("p_force") and (not r.get("is_active") or r.get("archived_at")
                                                   or (r.get("max_uses") is not None
                                                       and int(r.get("uses") or 0) >= int(r["max_uses"]))):
                    return SimpleNamespace(data=None, count=None)
                r["uses"] = int(r.get("uses") or 0) + 1
                return SimpleNamespace(data=r["uses"], count=None)
            if name == "shop_coupon_release":
                r = self._rule(params.get("p_rule_id"))
                if r is None or r.get("kind") != "coupon":
                    return SimpleNamespace(data=None, count=None)
                r["uses"] = max(int(r.get("uses") or 0) - 1, 0)
                return SimpleNamespace(data=r["uses"], count=None)
            raise RuntimeError(f"PGRST202: Could not find the function public.{name}")
        return SimpleNamespace(execute=run)

    def next_id(self, table) -> int:
        cur = max([int(r.get("id") or 0) for r in self.tables.get(table, [])] + [self._ids.get(table, 0)])
        self._ids[table] = cur + 1
        return cur + 1

    def writes(self, table: str | None = None) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("insert", "update", "delete", "upsert")
                and (table is None or c[1] == table)]

    def rows(self, table: str) -> list[dict]:
        return self.tables.get(table, [])


# ── synthetic fixtures ──────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)
REP, ADMIN = "rep@example.com", "admin@example.com"
SALESMEN = [
    {"id": 1, "name": "Rep One", "referral_code": "rep-one", "is_active": True, "focus_name": "REP ONE",
     "user_email": REP, "whatsapp": "97300000001"},
    {"id": 2, "name": "Rep Two", "referral_code": "rep-two", "is_active": True, "focus_name": "REP TWO",
     "user_email": "other@example.com", "whatsapp": "97300000002"},
]
USERS = [
    {"email": REP, "role": "salesman", "features": ["Catalog", "Shop Orders"], "status": "active", "full_name": "Rep One",
     "must_reset": False},
    {"email": ADMIN, "role": "admin", "features": [], "status": "active", "full_name": "Admin", "must_reset": False},
]


def _item(code, price, stock=100, cat="CABLE"):
    return {"item_code": code, "display_name": f"{code} name", "spec": f"{code} spec", "category": cat,
            "brand": "VFAN", "standard_rate": price, "b2c_rate": None, "product_image_url": None,
            "package_image_url": None, "sort_order": None, "created_at": "2026-07-03T00:00:00+00:00", "moq": 1,
            "pack_size": None, "stock_qty": stock, "stock_as_of": "2026-09-26", "sold_30d": 0, "prev_30d": 0,
            "sold_90d": 0, "customers_30d": 0}


ITEMS = [_item("T02", 2.95), _item("X05", 2.0), _item("C18", 1.25), _item("UK21", 5.25, cat="CHARGER")]
COSTS = {"T02": 1.1, "X05": 0.8, "C18": 0.5, "UK21": 2.6}


def _rule(id, kind, *, pct=None, amount=None, fixed=None, min_qty=None, min_value=None, stackable=False,
          items=(), cats=(), refs=(), coupon=None, bucket=None, **over):
    scope = {"item_codes": [c.upper() for c in items], "categories": [c.upper() for c in cats],
             "referral_codes": [r.lower() for r in refs]}
    if bucket:
        scope["bucket"] = bucket
    r = {"id": id, "name": f"Rule {id}", "kind": kind, "scope": scope, "pct_off": pct, "amount_off_bhd": amount,
         "fixed_price_bhd": fixed, "min_qty": min_qty, "min_value_bhd": min_value, "stackable": stackable,
         "coupon_code": coupon, "priority": 100, "is_active": True, "starts_at": None, "ends_at": None,
         "max_uses": None, "uses": 0}
    r.update(over)
    return r


def _ctx(rules=(), items=None, **settings):
    from app.shop import SETTING_DEFAULTS
    items = items if items is not None else ITEMS
    vals = dict(SETTING_DEFAULTS)
    vals.update({"shop_min_order_bhd": "0"})
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: dict(i) for i in items},
            "order": [i["item_code"] for i in items], "by_upper": {i["item_code"].upper(): i["item_code"] for i in items},
            "costs": dict(COSTS), "cost_sources": {k: "mrn" for k in COSTS},
            "rules": [dict(r) for r in rules], "salesmen": [dict(s) for s in SALESMEN], "pairs": {}, "drops": {},
            "campaigns": [], "loaded_at": "", "share_token": "tok-test-token-value", "_velocity_extra": {},
            "prices_updated": None, "reserved_slugs": frozenset()}


def _db(missing: set | None = None, rpcs: bool = True, columns: dict | None = None, **tables) -> _FakeDB:
    base = {"salesmen": SALESMEN, "app_settings": [], "shop_customers": [], "shop_customer_phones": [],
            "shop_orders": [], "shop_order_lines": [], "shop_order_events": [], "shop_events": [], "audit_log": [],
            "shop_admin_audit": [], "shop_notifications": [], "user_roles": USERS, "discount_rules": [],
            "shop_campaigns": [], "shop_order_discounts": [], "followup_exposures": [], "shop_badge_log": []}
    base.update(tables)
    return _FakeDB(base, columns=columns, missing=missing, rpcs=rpcs)


class _patched:
    """Point app.shop / app.database / app.catalog / app.upcoming at the fake, serve `ctx` as the
    cached catalog context, and reset every cache and probe the order path consults."""

    def __init__(self, fake: _FakeDB, ctx: dict | None = None):
        self.fake, self.ctx = fake, ctx

    def _reset(self):
        import app.database as db
        import app.followup_lift as fl
        import app.offers as of
        import app.shop as s
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        db.invalidate_user_cache()
        of._rpc_missing.clear()
        of._badge_done.clear()
        fl.forget()

    def __enter__(self):
        import app.catalog as cat
        import app.database as db
        import app.shop as s
        import app.upcoming as up
        self.mods = (s, db, cat, up)
        self.saved = (s.get_client, db.get_client, cat.get_client, up.get_client, s.context, s.invalidate,
                      s._ctx_cache["ctx"], s._ctx_cache["at"])
        s.get_client = db.get_client = cat.get_client = up.get_client = lambda: self.fake
        ctx = self.ctx if self.ctx is not None else _ctx()
        s._ctx_cache.update(ctx=ctx, at=time.time() + 10 ** 6)
        s.context = lambda force=False: ctx
        self.fake.invalidated = 0

        def _inv():
            self.fake.invalidated += 1
        s.invalidate = _inv
        self._reset()
        return self.fake

    def __exit__(self, *_exc):
        s, db, cat, up = self.mods
        (s.get_client, db.get_client, cat.get_client, up.get_client, s.context, s.invalidate) = self.saved[:6]
        s._ctx_cache.update(ctx=self.saved[6], at=self.saved[7])
        self._reset()
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


def _raises(fn, want: str):
    from app.shop import ShopError
    try:
        fn()
    except ShopError as e:
        assert want.lower() in str(e).lower(), (want, str(e))
        return e
    raise AssertionError(f"expected ShopError containing {want!r}")


def D(x) -> Decimal:
    return Decimal(str(x))


def _market_body(lines, *, device="dev-a", coupon=None, cid="c-1", phone="33001122"):
    return {"customer": {"name": "Test Shop", "phone": phone, "shop": "Test Shop", "area": "Manama"},
            "lines": [{"item_code": c, "qty": q} for c, q in lines], "device_id": device,
            "client_order_id": cid, "coupon_code": coupon}


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
def _api(fake, ctx=None, email=ADMIN, role="admin"):
    import app.main as m
    import app.shop_notify as sn
    with _patched(fake, ctx), _swap(m.limiter, enabled=False), \
            _swap(sn, notify_new_order=lambda *a, **k: {}, notify_status=lambda *a, **k: {},
                  notify_customer_cancel=lambda *a, **k: {}):
        c = _client(email, role)
        try:
            yield c
        finally:
            _drop_client()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the pricing engine's ledger
# ═══════════════════════════════════════════════════════════════════════════════

@test("ledger: one tier on a line — one line row with the line's whole discount, level line, not clamped")
def _():
    from app.shop import price_cart
    ctx = _ctx([_rule(1, "qty_tier", pct=10, min_qty=10, items=["T02"])])
    q = price_cart([{"item_code": "T02", "qty": 10}], ctx=ctx)
    ln = q["lines"][0]
    assert ln["unit_price_bhd"] == 2.655 and ln["discount_bhd"] == 2.95
    assert q["discounts"] == [{"rule_id": 1, "name": "Rule 1", "kind": "qty_tier", "amount_bhd": 2.95,
                               "level": "line", "item_code": "T02"}]
    assert q["_ledger"] == [{"level": "line", "line_index": 0, "item_code": "T02", "rule_id": 1, "name": "Rule 1",
                             "kind": "qty_tier", "amount_bhd": 2.95, "clamped": False}]
    assert q["experiments"] == []
    assert not any(k.startswith("_") for k in ln), "the line's working keys never reach a client"


@test("ledger: a stackable rule on top of a tier — the line's discount split by what each took off, to the fils, never both credited in full")
def _():
    from app.shop import price_cart
    ctx = _ctx([_rule(1, "qty_tier", pct=10, min_qty=10, items=["T02"]),
                _rule(2, "qty_tier", pct=5, min_qty=5, items=["T02"], stackable=True)])
    q = price_cart([{"item_code": "T02", "qty": 10}], ctx=ctx)
    ln = q["lines"][0]
    # 2.950 → 2.655 (tier) → 2.52225 (stack) → 2.522 booked; the line gives (2.950 − 2.522) × 10 = 4.280
    assert ln["unit_price_bhd"] == 2.522 and ln["discount_bhd"] == 4.28
    amounts = [(d["rule_id"], d["amount_bhd"]) for d in q["discounts"]]
    # shares ∝ 0.295 : 0.13275 → 2.952 and the remainder 1.328 (used to be 4.280 each = 8.560 shown)
    assert amounts == [(1, 2.952), (2, 1.328)], amounts
    assert sum(D(a) for _r, a in amounts) == D("4.28")
    assert [e["amount_bhd"] for e in q["_ledger"]] == [2.952, 1.328]


@test("ledger: the margin floor clamps a line — the row says clamped and carries what was really given")
def _():
    from app.shop import price_cart
    ctx = _ctx([_rule(3, "bundle_price", pct=50, items=["X05"])])
    q = price_cart([{"item_code": "X05", "qty": 4}], ctx=ctx)
    ln = q["lines"][0]
    # floor = 0.8 × 1.2 × 1.1 = 1.056; 50 % off would be 1.000
    assert ln["unit_price_bhd"] == 1.056 and ln["discount_bhd"] == 3.776
    assert q["_ledger"][0]["clamped"] is True and q["_ledger"][0]["amount_bhd"] == 3.776
    assert "X05" in q["_clamped"]


@test("ledger: a coupon is a cart row; capped by the floor it says clamped; a code that gives nothing is not booked")
def _():
    from app.shop import price_cart
    ctx = _ctx([_rule(4, "coupon", amount=5, min_value=10, coupon="SAVE5")])
    q = price_cart([{"item_code": "T02", "qty": 10}], "SAVE5", ctx=ctx)
    assert q["coupon"]["valid"] and q["_coupon_rule_id"] == 4
    assert q["_ledger"] == [{"level": "cart", "line_index": None, "item_code": None, "rule_id": 4, "name": "Rule 4",
                             "kind": "coupon", "amount_bhd": 5.0, "clamped": False}]
    assert q["discounts"][-1]["level"] == "cart"
    # headroom above the floor on T02 × 10 = (2.950 − 1.452) × 10 = 14.980; 60 % would be 17.700
    ctx = _ctx([_rule(5, "coupon", pct=60, min_value=10, coupon="BIG")])
    q = price_cart([{"item_code": "T02", "qty": 10}], "BIG", ctx=ctx)
    assert q["_ledger"][0]["amount_bhd"] == 14.98 and q["_ledger"][0]["clamped"] is True
    # an item already at its floor: nothing to give — no row, no use spent
    ctx = _ctx([_rule(6, "coupon", pct=10, min_value=1, coupon="NOPE")], items=[_item("C18", 0.66)])
    q = price_cart([{"item_code": "C18", "qty": 10}], "NOPE", ctx=ctx)
    assert q["_ledger"] == [] and q["_coupon_rule_id"] is None and q["coupon"]["valid"] is False


@test("ledger property: 1,500 random carts — every line's rows add up to its discount, cart rows to the cart discounts, all ≥ 0 and 3 dp")
def _():
    from app.shop import price_cart
    rng = random.Random(7)
    kinds = ["qty_tier", "bundle_price", "cart_value", "coupon"]
    checked = stacked = cart_rows = 0
    for n in range(1500):
        items = [_item(f"I{i}", round(rng.uniform(0.3, 30), 3), stock=rng.choice([0, 5, 100])) for i in range(6)]
        rules = []
        for rid in range(1, rng.randint(1, 6)):
            kind = rng.choice(kinds)
            codes = rng.sample([i["item_code"] for i in items], rng.randint(0, 3))
            disc = rng.choice([{"pct": rng.choice([5, 10, 25, 60])}, {"amount": rng.choice([0.1, 0.5, 3])}])
            rules.append(_rule(rid, kind, min_qty=rng.choice([None, 2, 5]) if kind == "qty_tier" else None,
                               min_value=rng.choice([1, 20]) if kind in ("cart_value", "coupon") else None,
                               stackable=rng.random() < 0.4, items=codes,
                               coupon=f"C{rid}" if kind == "coupon" else None, **disc))
        ctx = _ctx(rules, items=items)
        ctx["costs"] = {i["item_code"]: round(i["standard_rate"] * rng.uniform(0.2, 0.9), 3)
                        for i in items if rng.random() < 0.8}
        lines = [{"item_code": c["item_code"], "qty": rng.randint(1, 12)} for c in rng.sample(items, rng.randint(1, 5))]
        coupon = rng.choice([None] + [r["coupon_code"] for r in rules if r["kind"] == "coupon"])
        q = price_cart(lines, coupon, ctx=ctx, force_backorder=True)
        by_line: dict[int, Decimal] = {}
        for e in q["_ledger"]:
            a = D(e["amount_bhd"])
            assert a >= 0 and a == a.quantize(D("0.001")), e
            if e["level"] == "line":
                by_line[e["line_index"]] = by_line.get(e["line_index"], D(0)) + a
        for i, ln in enumerate(q["lines"]):
            want = D(ln["discount_bhd"])
            got = by_line.get(i, D(0))
            if ln["applied"]:
                assert got == want, (n, i, got, want, q["_ledger"])
                stacked += len(ln["applied"]) > 1
            else:
                assert got == 0
        cart_l = sum((D(e["amount_bhd"]) for e in q["_ledger"] if e["level"] == "cart"), D(0))
        cart_d = sum((D(d["amount_bhd"]) for d in q["discounts"] if d["level"] == "cart"), D(0))
        assert cart_l == cart_d, (n, cart_l, cart_d)
        line_d = sum((D(d["amount_bhd"]) for d in q["discounts"] if d["level"] == "line"), D(0))
        assert line_d == sum((D(ln["discount_bhd"]) for ln in q["lines"] if ln["applied"]), D(0)), n
        assert D(q["discount_bhd"]) == line_d + cart_d or not q["lines"], n
        cart_rows += sum(1 for e in q["_ledger"] if e["level"] == "cart")
        checked += 1
    assert checked == 1500 and stacked > 50 and cart_rows > 100, (stacked, cart_rows)
    print(f"      {checked:,} carts · {stacked:,} stacked lines · {cart_rows:,} cart rows")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. hold-outs (scope.bucket)
# ═══════════════════════════════════════════════════════════════════════════════

def _key_in(mod: int, want_bucket: int, prefix="d:dev-") -> str:
    from app.offers import bucket_of
    for i in range(10_000):
        k = f"{prefix}{i}"
        if bucket_of(k, mod) == want_bucket:
            return k
    raise AssertionError("no key found")


@test("buckets: {mod, hold} validated in plain sentences; a stable, near-uniform sha1 split; keys by customer, else device")
def _():
    from app.offers import bucket_key, bucket_of, norm_bucket
    assert norm_bucket(None) is None and norm_bucket({}) is None
    assert norm_bucket({"mod": "10", "hold": [3, 0, 3]}) == {"mod": 10, "hold": [0, 3]}
    for bad, want in (({"mod": 1, "hold": [0]}, "2 to 100"), ({"mod": 101, "hold": [0]}, "2 to 100"),
                      ({"mod": 5, "hold": []}, "at least one"), ({"mod": 5, "hold": [5]}, "0 to 4"),
                      ({"mod": 2, "hold": [0, 1]}, "must get the offer"), ("x", "mod and hold")):
        try:
            norm_bucket(bad)
            raise AssertionError(bad)
        except ValueError as e:
            assert want in str(e), (bad, str(e))
    counts = [0] * 10
    for i in range(5000):
        counts[bucket_of(f"c:{i}", 10)] += 1
    assert all(400 <= c <= 600 for c in counts), counts
    assert bucket_of("d:abc", 7) == bucket_of("d:abc", 7)
    assert bucket_key(42, "dev") == "c:42" and bucket_key(None, " dev ") == "d:dev" and bucket_key(None, None) is None


@test("buckets: a held-back merchant is priced without the offer, an offered one with it, no key = held; exp/var listed only when the cart meets the rule")
def _():
    from app.shop import price_cart
    b = {"mod": 2, "hold": [0]}
    ctx = _ctx([_rule(7, "qty_tier", pct=10, min_qty=2, items=["T02"], bucket=b)])
    hold, offer = _key_in(2, 0), _key_in(2, 1)
    qh = price_cart([{"item_code": "T02", "qty": 4}], ctx=ctx, bucket_key=hold)
    qo = price_cart([{"item_code": "T02", "qty": 4}], ctx=ctx, bucket_key=offer)
    qn = price_cart([{"item_code": "T02", "qty": 4}], ctx=ctx)
    assert qh["discount_bhd"] == 0 and qh["experiments"] == [{"rule_id": 7, "var": "hold"}]
    assert qo["discount_bhd"] == 1.18 and qo["experiments"] == [{"rule_id": 7, "var": "offer"}]
    assert qn["discount_bhd"] == 0 and qn["experiments"] == [{"rule_id": 7, "var": "none"}]
    q_other = price_cart([{"item_code": "X05", "qty": 4}], ctx=ctx, bucket_key=offer)
    assert q_other["experiments"] == [], "a cart the rule cannot touch is no exposure"
    from app.offers import experiment_meta
    assert experiment_meta(qo["experiments"]) == {"exp": "r7", "var": "offer"} and experiment_meta([]) == {}


@test("buckets: a coupon can never carry a hold-out; an automatic rule keeps a valid one through validate / list")
def _():
    from app.shop import ShopError, _norm_scope, validate_rule
    try:
        validate_rule({"name": "Hold coupon", "kind": "coupon", "coupon_code": "HOLD1", "amount_off_bhd": 1,
                       "min_value_bhd": 5, "scope": {"bucket": {"mod": 10, "hold": [0]}}})
        raise AssertionError("accepted")
    except ShopError as e:
        assert "coupon cannot have one" in str(e)
    row = validate_rule({"name": "Tier test", "kind": "qty_tier", "min_qty": 3, "pct_off": 5,
                         "scope": {"item_codes": ["t02"], "bucket": {"mod": 10, "hold": [0, 1]}}})
    assert row["scope"] == {"item_codes": ["T02"], "categories": [], "referral_codes": [],
                            "bucket": {"mod": 10, "hold": [0, 1]}}
    _raises(lambda: validate_rule({"name": "Tier bad", "kind": "qty_tier", "min_qty": 3, "pct_off": 5,
                                   "scope": {"bucket": {"mod": 3, "hold": [0, 1, 2]}}}), "must get the offer")
    assert "bucket" not in _norm_scope({"bucket": {"mod": 0}})


@test("buckets: the quote and the order price one device alike (the device's customer once known); the order event carries exp/var and the ledger the arm")
def _():
    from app.offers import bucket_of
    from app.shop import create_order, price_cart
    b = {"mod": 2, "hold": [0]}
    rule = _rule(7, "qty_tier", pct=10, min_qty=2, items=["T02"], bucket=b)
    ctx = _ctx([rule])
    fake = _db(discount_rules=[rule])
    dev = next(f"dev-{i}" for i in range(500) if bucket_of(f"d:dev-{i}", 2) == 1)
    from app import offers
    with _patched(fake, ctx):
        key = offers.key_for_device(ctx, dev)
        assert key == f"d:{dev}"
        quote = price_cart([{"item_code": "T02", "qty": 4}], ctx=ctx, bucket_key=key)
        o = create_order(_market_body([("T02", 4)], device=dev), market=True)
    assert quote["total_bhd"] == o["totals"]["total_bhd"] == 10.62
    ev = [e for e in fake.rows("shop_events") if e["event"] == "order"][0]
    assert ev["meta"]["exp"] == "r7" and ev["meta"]["var"] == "offer"
    led = fake.rows("shop_order_discounts")
    assert len(led) == 1 and led[0]["rule_snapshot"]["arm"] == "offer" and led[0]["rule_snapshot"]["scope"]["bucket"] == b
    # the device is now in the merchant's device list: the next quote keys on the customer
    with _patched(fake, ctx):
        assert offers.key_for_device(ctx, dev) == f"c:{fake.rows('shop_customers')[0]['id']}"
    # no bucketed rule live: no lookup at all
    with _patched(fake, _ctx([])):
        n = len(fake.calls)
        assert offers.key_for_device(_ctx([]), dev) is None and len(fake.calls) == n


# ═══════════════════════════════════════════════════════════════════════════════
# 3. create_order writes the ledger
# ═══════════════════════════════════════════════════════════════════════════════

def _tier_and_coupon(**coupon_over):
    return [_rule(1, "qty_tier", pct=10, min_qty=10, items=["T02"]),
            _rule(4, "coupon", amount=2, min_value=10, coupon="SAVE2", **coupon_over)]


@test("create_order: one ledger insert right after the lines — line rows carry the inserted line id, the cart row none; placed, not confirmed")
def _():
    from app.shop import create_order
    rules = _tier_and_coupon()
    fake = _db(discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        o = create_order(_market_body([("T02", 10), ("X05", 2)], coupon="SAVE2"), market=True)
    lines = {ln["item_code"]: ln for ln in fake.rows("shop_order_lines")}
    led = fake.rows("shop_order_discounts")
    assert [(r["level"], r["rule_id"], r["line_id"], r["amount_bhd"]) for r in led] == [
        ("line", 1, lines["T02"]["id"], 2.95), ("cart", 4, None, 2.0)], led
    assert all(r["order_id"] == o["id"] and r["stage"] == "placed" and r["amount_confirmed_bhd"] is None for r in led)
    assert led[0]["rule_snapshot"]["pct_off"] == 10 and led[0]["rule_snapshot"]["name"] == "Rule 1"
    assert "arm" not in led[0]["rule_snapshot"], "not an experiment: no arm"
    inserts = [c for c in fake.writes("shop_order_discounts") if c[0] == "insert"]
    assert len(inserts) == 1 and len(inserts[0][2]) == 2, "one insert, every row"
    # 29.500 + 4.000 − 2.950 (tier) − 2.000 (coupon)
    assert o["totals"]["total_bhd"] == 28.55 and "_ledger" not in o["totals"]


@test("create_order: a rep-placed order is born confirmed — its ledger rows are confirmed at the placed amounts")
def _():
    from app.shop import create_order
    rules = _tier_and_coupon()
    fake = _db(discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        create_order({"customer": {"name": "Test Shop", "phone": "33001122"},
                      "lines": [{"item_code": "T02", "qty": 10}]}, staff_email=REP)
    led = fake.rows("shop_order_discounts")
    assert len(led) == 1 and led[0]["stage"] == "confirmed" and led[0]["amount_confirmed_bhd"] == led[0]["amount_bhd"] == 2.95


@test("create_order before the migration: no ledger table — the order is placed exactly as before, nothing named that is not there")
def _():
    from app.shop import create_order
    rules = _tier_and_coupon()
    fake = _db(missing={"shop_order_discounts"}, rpcs=False, discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        o = create_order(_market_body([("T02", 10)], coupon="SAVE2"), market=True)
    assert o["order_no"] and len(fake.rows("shop_order_lines")) == 1
    assert not [c for c in fake.writes() if c[1] == "shop_order_discounts"]
    # the old counter ran (no RPC): the use is counted once, after the order
    assert fake.rows("discount_rules")[1]["uses"] == 1
    assert [n for n, _p in fake.rpc_calls if n != "shop_next_order_no"] == ["shop_coupon_reserve"]


@test("create_order: a ledger insert that fails never fails the order (logged, the order and its lines stand)")
def _():
    from app.shop import create_order
    rules = _tier_and_coupon()
    fake = _db(discount_rules=rules)
    fake.fail[("insert", "shop_order_discounts")] = RuntimeError("boom")
    with _patched(fake, _ctx(rules)):
        o = create_order(_market_body([("T02", 10)]), market=True)
    assert o["id"] and fake.rows("shop_orders")[0]["status"] == "new" and len(fake.rows("shop_order_lines")) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. the coupon counter
# ═══════════════════════════════════════════════════════════════════════════════

@test("coupon: the use is reserved through the RPC before anything is written — counted once, the old counter silent")
def _():
    from app.shop import create_order
    rules = _tier_and_coupon(max_uses=5)
    fake = _db(discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        create_order(_market_body([("T02", 10)], coupon="SAVE2"), market=True)
    assert fake.rows("discount_rules")[1]["uses"] == 1
    assert not [c for c in fake.writes("discount_rules")], "no read-then-write counter while the RPC exists"
    reserve = next(i for i, c in enumerate(fake.calls) if c[0] == "rpc" and c[1] == "shop_coupon_reserve")
    number = next(i for i, c in enumerate(fake.calls) if c[0] == "rpc" and c[1] == "shop_next_order_no")
    first_write = next(i for i, c in enumerate(fake.calls) if c[0] in ("insert", "update", "upsert", "delete"))
    assert reserve < number < first_write, (reserve, number, first_write)
    assert fake.calls[reserve][2] == {"p_rule_id": 4, "p_force": False}


@test("coupon: none left → the order is refused in a plain sentence, nothing is written, the rules cache is dropped")
def _():
    from app import offers
    from app.shop import create_order
    rules = _tier_and_coupon(max_uses=3, uses=3)
    fake = _db(discount_rules=rules)
    ctx = _ctx(_tier_and_coupon(max_uses=3, uses=2))       # the 60 s cache still thinks one is left
    with _patched(fake, ctx):
        _raises(lambda: create_order(_market_body([("T02", 10)], coupon="SAVE2"), market=True), "no longer available")
        assert fake.invalidated == 1
    assert fake.writes() == [] and fake.rows("shop_orders") == []
    assert offers.COUPON_GONE_MSG.endswith("Remove it to place your order.")


@test("coupon: an order that fails after its reservation gives the use back (order number, header, lines)")
def _():
    from app.shop import create_order
    for where in (("insert", "shop_orders"), ("insert", "shop_order_lines")):
        rules = _tier_and_coupon(max_uses=5)
        fake = _db(discount_rules=rules)
        fake.fail[where] = RuntimeError("wire dropped")
        with _patched(fake, _ctx(rules)):
            try:
                create_order(_market_body([("T02", 10)], coupon="SAVE2"), market=True)
                raise AssertionError("no error")
            except RuntimeError:
                pass
        assert fake.rows("discount_rules")[1]["uses"] == 0, where
        assert [n for n, _p in fake.rpc_calls if "coupon" in n] == ["shop_coupon_reserve", "shop_coupon_release"], where
        assert fake.rows("shop_order_lines") == []


@test("coupon: a cancel gives the use back (staff and merchant), found through the ledger; an admin reopen takes it again")
def _():
    from app.shop import cancel_by_customer, create_order, reopen_order, set_status
    rules = _tier_and_coupon(max_uses=5)
    fake = _db(discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        o1 = create_order(_market_body([("T02", 10)], coupon="SAVE2", cid="c-1"), market=True)
        o2 = create_order(_market_body([("T02", 10)], coupon="SAVE2", cid="c-2"), market=True)
        assert fake.rows("discount_rules")[1]["uses"] == 2
        set_status(o1["id"], "cancelled", "Duplicate", actor=ADMIN, reason_code="duplicate")
        assert fake.rows("discount_rules")[1]["uses"] == 1
        cancel_by_customer(o2["token"], "changed my mind")
        assert fake.rows("discount_rules")[1]["uses"] == 0
        reopen_order(o1["id"], "cancelled by mistake", ADMIN)
        assert fake.rows("discount_rules")[1]["uses"] == 1
    assert ("shop_coupon_reserve", {"p_rule_id": 4, "p_force": True}) in fake.rpc_calls
    # an order without a coupon never touches the counter
    fake2 = _db(discount_rules=rules)
    with _patched(fake2, _ctx(rules)):
        o = create_order(_market_body([("T02", 10)]), market=True)
        set_status(o["id"], "cancelled", "Duplicate", actor=ADMIN, reason_code="duplicate")
    assert [n for n, _p in fake2.rpc_calls if "coupon" in n] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 5. confirmed amounts
# ═══════════════════════════════════════════════════════════════════════════════

@test("confirm / amend / deliver with changes: the ledger's confirmed amounts follow the agreed quantities (the order heart's own cart share)")
def _():
    from app.shop import confirm_order, create_order, deliver_with_changes, set_status
    rules = _tier_and_coupon()
    fake = _db(discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        o = create_order(_market_body([("T02", 10), ("X05", 5)], coupon="SAVE2"), market=True)
        lines = {ln["item_code"]: ln for ln in fake.rows("shop_order_lines")}
        # T02 10 → 6 (out of stock): the tier's 0.295 a unit × 6 = 1.770; the coupon shared pro rata
        confirm_order(o["id"], [{"line_id": lines["T02"]["id"], "qty_confirmed": 6, "reason": "out_of_stock"}],
                      None, None, actor=ADMIN)
        led = {r["level"]: r for r in fake.rows("shop_order_discounts")}
        assert led["line"]["stage"] == "confirmed" and led["line"]["amount_confirmed_bhd"] == 1.77
        # items 26.550 + 10.000 = 36.550 placed; 6 × 2.655 + 10 = 25.930 confirmed → 2 × 25.930 / 36.550
        assert led["cart"]["amount_confirmed_bhd"] == 1.419, led["cart"]
        assert led["line"]["amount_bhd"] == 2.95 and led["cart"]["amount_bhd"] == 2.0, "placed amounts never move"
        set_status(o["id"], "delivered", None, actor=ADMIN)                   # plain Delivered: nothing moves
        assert {r["level"]: r["amount_confirmed_bhd"] for r in fake.rows("shop_order_discounts")} == {"line": 1.77,
                                                                                                    "cart": 1.419}
    fake = _db(discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        o = create_order(_market_body([("T02", 10)], coupon="SAVE2"), market=True)
        lid = fake.rows("shop_order_lines")[0]["id"]
        confirm_order(o["id"], [], None, None, actor=ADMIN)
        assert [r["amount_confirmed_bhd"] for r in fake.rows("shop_order_discounts")] == [2.95, 2.0]
        deliver_with_changes(o["id"], [{"line_id": lid, "qty_delivered": 5, "reason": "damaged"}], actor=ADMIN)
        assert [r["amount_confirmed_bhd"] for r in fake.rows("shop_order_discounts")] == [1.475, 1.0]


@test("confirmed amounts (pure): a line's rows keep their placed proportions; rows of an unknown line are left alone")
def _():
    from app.offers import allocate, confirmed_amounts
    assert allocate(D("1.000"), [D("1"), D("1"), D("1")]) == [D("0.333"), D("0.333"), D("0.334")]
    assert allocate(D("0"), [D("2"), D("1")]) == [D("0.000"), D("0.000")]
    assert allocate(D("4.280"), []) == [] and allocate(D("0.5"), [D("0")]) == [D("0.500")]
    order = {"id": 9, "status": "confirmed", "discount_bhd": 4.28, "delivery_bhd": 0}
    lines = [{"id": 1, "item_code": "T02", "qty": 10, "qty_confirmed": 5, "list_price_bhd": 2.95, "unit_price_bhd": 2.522,
              "unit_price_confirmed": 2.522, "discount_bhd": 4.28, "line_total_bhd": 25.22, "line_status": "changed"}]
    ledger = [{"id": 11, "line_id": 1, "level": "line", "amount_bhd": 2.952},
              {"id": 12, "line_id": 1, "level": "line", "amount_bhd": 1.328},
              {"id": 13, "line_id": 77, "level": "line", "amount_bhd": 1.0}]
    out = confirmed_amounts(order, lines, ledger)
    assert out == {11: 1.476, 12: 0.664}, out            # 0.428 × 5 = 2.140, split 2.952 : 1.328


# ═══════════════════════════════════════════════════════════════════════════════
# 5b. review fixes (R7d money stream): the coupon use, hold-outs on shared surfaces, reopen,
#     added / substitute lines in the ledger, a flagged cost never snapshotted
# ═══════════════════════════════════════════════════════════════════════════════

@test("coupon 'code not needed': the order does not carry the code, nothing is reserved, and its cancel gives no use back")
def _():
    from app.shop import create_order, price_cart, set_status
    # a non-stackable automatic 20 % beats the BHD 2 code; the code has no uses left (5 of 5)
    rules = [_rule(3, "cart_value", pct=20, min_value=5),
             _rule(4, "coupon", amount=2, min_value=10, coupon="SAVE2", max_uses=5, uses=5)]
    fake = _db(discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        q = price_cart([{"item_code": "T02", "qty": 10}], "SAVE2", ctx=_ctx(rules))
        assert q["coupon"]["valid"] is True and "not needed" in q["coupon"]["message"] and q["_coupon_rule_id"] is None
        o = create_order(_market_body([("T02", 10)], coupon="SAVE2"), market=True)
        assert fake.rows("shop_orders")[0]["coupon_code"] is None, "a code that gave nothing is not the order's code"
        assert [r["kind"] for r in fake.rows("shop_order_discounts")] == ["cart_value"]
        set_status(o["id"], "cancelled", "Duplicate", actor=ADMIN, reason_code="duplicate")
    assert [n for n, _p in fake.rpc_calls if "coupon" in n] == [], fake.rpc_calls
    assert fake.rows("discount_rules")[1]["uses"] == 5, "no phantom use handed back"


@test("coupon_rule_for_order: a ledger without a coupon row = no use to give back; no ledger rows at all = the code's rule")
def _():
    from app import offers
    rules = [_rule(3, "cart_value", pct=20, min_value=5), _rule(4, "coupon", amount=2, min_value=10, coupon="SAVE2")]
    # an order written before the fix: it carries the code, but the ledger shows only the automatic offer
    fake = _db(discount_rules=rules, shop_order_discounts=[
        {"id": 1, "order_id": 9, "rule_id": 3, "kind": "cart_value", "level": "cart", "amount_bhd": 5.9}])
    with _patched(fake):
        assert offers.coupon_rule_for_order(fake, {"id": 9, "coupon_code": "SAVE2"}) is None
        assert offers.release_for_order(fake, {"id": 9, "coupon_code": "SAVE2"}) is False
        assert offers.retake_for_order(fake, {"id": 9, "coupon_code": "SAVE2"}) is False
        # a coupon row → its rule; no ledger rows at all (before the ledger) → the rule holding the code
        fake.rows("shop_order_discounts").append({"id": 2, "order_id": 10, "rule_id": 4, "kind": "coupon", "level": "cart"})
        assert offers.coupon_rule_for_order(fake, {"id": 10, "coupon_code": "SAVE2"}) == 4
        assert offers.coupon_rule_for_order(fake, {"id": 11, "coupon_code": "save2"}) == 4
    assert [n for n, _p in fake.rpc_calls if "coupon" in n] == []


@test("hold-out rules stay off every shared surface: no catalogue tier, no offer-list entry, no 'on_offer' badge (staff too); a plain rule still shows")
def _():
    from app.shop import catalog_payload, is_hold_out, item_tiers
    held = _rule(7, "qty_tier", pct=10, min_qty=2, items=["T02"], bucket={"mod": 2, "hold": [0]})
    plain = _rule(7, "qty_tier", pct=10, min_qty=2, items=["T02"])
    assert is_hold_out(held) and not is_hold_out(plain)
    ctx = _ctx([held])
    assert item_tiers(ctx, ctx["items"]["T02"]) == []
    with _patched(_db(), ctx):
        for staff in (False, True):
            p = (catalog_payload(None, staff_email=REP) if staff else catalog_payload("tok-test-token-value"))
            t02 = next(i for i in p["items"] if i["item_code"] == "T02")
            assert [o["id"] for o in p["offers"]] == [], (staff, p["offers"])
            assert t02["has_tiers"] is False and t02["tiers"] == [] and "on_offer" not in t02["badges"], (staff, t02)
    ctx = _ctx([plain])                                  # the control: the same rule without a bucket
    assert [t["min_qty"] for t in item_tiers(ctx, ctx["items"]["T02"])] == [2]
    with _patched(_db(), ctx):
        p = catalog_payload("tok-test-token-value")
        t02 = next(i for i in p["items"] if i["item_code"] == "T02")
        assert [o["id"] for o in p["offers"]] == [7] and t02["has_tiers"] is True and "on_offer" in t02["badges"]


@test("reopen refreshes the ledger: a delivery undone reads the confirmed quantities again; a cancel undone goes back to the placed amounts")
def _():
    from app.shop import confirm_order, create_order, deliver_with_changes, reopen_order, set_status
    rules = [_rule(1, "qty_tier", pct=10, min_qty=10, items=["T02"])]
    fake = _db(discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        o = create_order(_market_body([("T02", 10)]), market=True)
        lid = fake.rows("shop_order_lines")[0]["id"]
        confirm_order(o["id"], [], None, None, actor=ADMIN)
        deliver_with_changes(o["id"], [{"line_id": lid, "qty_delivered": 5, "reason": "damaged"}], actor=ADMIN)
        assert [r["amount_confirmed_bhd"] for r in fake.rows("shop_order_discounts")] == [1.475]
        out = reopen_order(o["id"], "delivered by mistake", ADMIN)
        assert out["status"] == "confirmed"
        led = fake.rows("shop_order_discounts")
        assert [(r["amount_confirmed_bhd"], r["stage"]) for r in led] == [(2.95, "confirmed")], led
        # a confirmed order cancelled, then reopened to Received: the placed amounts until confirmed again
        o2 = create_order(_market_body([("T02", 10)], cid="c-2"), market=True)
        lid2 = next(r["id"] for r in fake.rows("shop_order_lines") if r["order_id"] == o2["id"])
        confirm_order(o2["id"], [{"line_id": lid2, "qty_confirmed": 6, "reason": "out_of_stock"}], None, None,
                      actor=ADMIN)
        row2 = next(r for r in fake.rows("shop_order_discounts") if r["order_id"] == o2["id"])
        assert row2["amount_confirmed_bhd"] == 1.77 and row2["stage"] == "confirmed"
        set_status(o2["id"], "cancelled", "Duplicate", actor=ADMIN, reason_code="duplicate")
        assert reopen_order(o2["id"], "cancelled by mistake", ADMIN)["status"] == "new"
        row2 = next(r for r in fake.rows("shop_order_discounts") if r["order_id"] == o2["id"])
        assert row2["amount_confirmed_bhd"] is None and row2["stage"] == "placed", row2
        assert row2["amount_bhd"] == 2.95, "the placed amount never moves"


@test("ledger: a substitute (Confirm), an added line (Amend) and a line added at the door each get their offer's row; reopen zeroes the door line")
def _():
    from app.shop import amend_order, confirm_order, create_order, deliver_with_changes, reopen_order
    rules = [_rule(1, "qty_tier", pct=10, min_qty=10, items=["T02", "X05", "C18", "UK21"])]
    fake = _db(discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        o = create_order(_market_body([("T02", 10)]), market=True)
        t02 = fake.rows("shop_order_lines")[0]["id"]
        confirm_order(o["id"], [{"line_id": t02, "substitute_item_code": "X05", "substitute_qty": 10,
                                 "reason": "out_of_stock"}], None, None, actor=ADMIN, shop_agreed={"via": "whatsapp"})
        by_code = {ln["item_code"]: ln["id"] for ln in fake.rows("shop_order_lines")}
        led = {r["line_id"]: r for r in fake.rows("shop_order_discounts")}
        assert led[t02]["amount_confirmed_bhd"] == 0.0, "the substituted line gives nothing now"
        sub = led[by_code["X05"]]                        # 10 × (2.000 − 1.800)
        assert (sub["rule_id"], sub["kind"], sub["level"], sub["amount_bhd"], sub["amount_confirmed_bhd"],
                sub["stage"], sub["item_code"]) == (1, "qty_tier", "line", 2.0, 2.0, "confirmed", "X05"), sub
        assert sub["rule_snapshot"]["pct_off"] == 10 and sub["order_id"] == o["id"]
        amend_order(o["id"], [], None, None, actor=ADMIN, added_lines=[{"item_code": "C18", "qty": 10}])
        by_code = {ln["item_code"]: ln["id"] for ln in fake.rows("shop_order_lines")}
        led = {r["line_id"]: r for r in fake.rows("shop_order_discounts")}
        assert led[by_code["C18"]]["amount_confirmed_bhd"] == 1.25        # 10 × (1.250 − 1.125)
        deliver_with_changes(o["id"], added=[{"item_code": "UK21", "qty": 10}], actor=ADMIN)
        by_code = {ln["item_code"]: ln["id"] for ln in fake.rows("shop_order_lines")}
        led = {r["line_id"]: r for r in fake.rows("shop_order_discounts")}
        assert led[by_code["UK21"]]["amount_confirmed_bhd"] == 5.25       # 10 × (5.250 − 4.725)
        assert len(fake.rows("shop_order_discounts")) == 4
        # the performance view sums these rows: the rule's discount now = what the order really gives
        assert round(sum(r["amount_confirmed_bhd"] for r in fake.rows("shop_order_discounts")), 3) == 8.5
        reopen_order(o["id"], "delivered by mistake", ADMIN)             # the door line goes out again
        led = {r["line_id"]: r for r in fake.rows("shop_order_discounts")}
        assert led[by_code["UK21"]]["amount_confirmed_bhd"] == 0.0 and led[by_code["X05"]]["amount_confirmed_bhd"] == 2.0
    # before the migration nothing is written for an added line, and the edit still goes through
    fake = _db(missing={"shop_order_discounts"}, discount_rules=rules)
    with _patched(fake, _ctx(rules)):
        o = create_order(_market_body([("T02", 10)]), market=True)
        confirm_order(o["id"], [], None, None, actor=ADMIN, added_lines=[{"item_code": "C18", "qty": 10}])
    assert not [c for c in fake.writes() if c[1] == "shop_order_discounts"]
    assert len(fake.rows("shop_order_lines")) == 2


@test("added-line ledger rows (pure): mapped by position and item code, line-level only, born confirmed; a mismatch is skipped")
def _():
    from app.offers import added_ledger_rows
    pl = {"item_code": "X05", "_ledger": [
        {"level": "line", "line_index": 0, "item_code": "X05", "rule_id": 1, "name": "Rule 1", "kind": "qty_tier",
         "amount_bhd": 1.2, "clamped": False},
        {"level": "line", "line_index": 0, "item_code": "X05", "rule_id": 2, "name": "Rule 2", "kind": "salesman_offer",
         "amount_bhd": 0.8, "clamped": True}]}
    rows = added_ledger_rows(5, [{"id": 41, "item_code": "X05"}], [pl], {1: _rule(1, "qty_tier", pct=10)})
    assert [(r["line_id"], r["rule_id"], r["amount_bhd"], r["amount_confirmed_bhd"], r["stage"], r["clamped"])
            for r in rows] == [(41, 1, 1.2, 1.2, "confirmed", False), (41, 2, 0.8, 0.8, "confirmed", True)]
    assert rows[0]["rule_snapshot"]["pct_off"] == 10 and rows[1]["rule_snapshot"]["name"] == "Rule 2"
    assert added_ledger_rows(5, [{"id": 41, "item_code": "C18"}], [pl], {}) == []
    assert added_ledger_rows(5, [{"id": None, "item_code": "X05"}], [pl], {}) == []
    assert added_ledger_rows(5, [{"id": 41, "item_code": "X05"}], [{"item_code": "X05"}], {}) == []


@test("a cost flagged implausible (or zero) is never snapshotted as real cost: the line is uncosted, like a missing cost")
def _():
    from app import shop
    from app.shop import create_order
    from app.shop_heart import _cost_fields
    ctx = _ctx([])
    ctx["costs"].update(T02=0.0126, C18=0.0)          # 0.0126 against a 2.950 price: a per-carton / typo row
    ctx["cost_flags"] = shop.cost_flags(ctx)
    assert "T02" in ctx["cost_flags"]
    assert _cost_fields(ctx, "T02") == {"unit_cost_bhd": None, "cost_source": None}
    assert _cost_fields(ctx, "C18") == {"unit_cost_bhd": None, "cost_source": None}
    assert _cost_fields(ctx, "NOPE") == {"unit_cost_bhd": None, "cost_source": None}
    assert _cost_fields(ctx, "X05") == {"unit_cost_bhd": 0.8, "cost_source": "mrn"}   # a trusted cost stands
    fake = _db()
    with _patched(fake, ctx):
        create_order(_market_body([("T02", 3), ("X05", 3)]), market=True)
    got = {ln["item_code"]: (ln.get("unit_cost_bhd"), ln.get("cost_source")) for ln in fake.rows("shop_order_lines")}
    assert got == {"T02": (None, None), "X05": (0.8, "mrn")}, got


# ═══════════════════════════════════════════════════════════════════════════════
# 6. soft delete
# ═══════════════════════════════════════════════════════════════════════════════

@test("delete: a rule any order used is refused (uses, a ledger row, a line's rule_ids) — archive instead; an unused draft is deleted")
def _():
    from app import offers
    from app.shop import delete_rule
    base = [_rule(1, "qty_tier", pct=5, min_qty=2), _rule(2, "coupon", amount=1, min_value=5, coupon="X1", uses=1),
            _rule(3, "qty_tier", pct=5, min_qty=2), _rule(4, "qty_tier", pct=5, min_qty=2)]
    fake = _db(discount_rules=base, shop_order_discounts=[{"id": 1, "order_id": 5, "rule_id": 3, "level": "line"}],
               shop_order_lines=[{"id": 9, "order_id": 6, "item_code": "T02", "rule_ids": [1]}])
    with _patched(fake):
        for rid in (1, 2, 3):
            _raises(lambda rid=rid: delete_rule(rid), "archive it instead")
        delete_rule(4)
        _raises(lambda: delete_rule(99), "not found")
    assert [r["id"] for r in fake.rows("discount_rules")] == [1, 2, 3]
    assert offers.RULE_REFERENCED_MSG.startswith("Orders used this offer")


@test("archive / restore: switched off + archived_at, out of pricing, the catalog and the list; restore brings it back still off")
def _():
    from app import offers
    from app.shop import _load_rules, _load_campaigns, rules_payload
    rules = [_rule(1, "qty_tier", pct=5, min_qty=2), _rule(2, "cart_value", pct=3, min_value=50)]
    camps = [{"id": 5, "title": "Old", "is_active": True, "sort_order": 1, "rule_id": 1}]
    fake = _db(discount_rules=rules, shop_campaigns=camps)
    with _patched(fake):
        row = offers.archive("discount_rules", 1, ADMIN)
        assert row["archived_at"] and row["is_active"] is False and row["archived_by"] == ADMIN
        fake.rows("discount_rules")[0]["is_active"] = True                  # even switched back on by hand …
        assert [r["id"] for r in _load_rules()] == [2]                      # … an archived rule never prices
        p = rules_payload()
        assert [r["id"] for r in p["rules"]] == [2] and p["archived"] == 1 and p["can_archive"] is True
        assert {r["id"]: r["status"] for r in rules_payload(include_archived=True)["rules"]} == {1: "archived", 2: "live"}
        offers.archive("shop_campaigns", 5, ADMIN)
        assert _load_campaigns() == []
        fake.rows("discount_rules")[0]["is_active"] = False
        back = offers.archive("discount_rules", 1, ADMIN, restore=True)
        assert back["archived_at"] is None and back["is_active"] is False, "restore never switches an offer on"
        assert {r["id"]: r["status"] for r in rules_payload()["rules"]} == {1: "inactive", 2: "live"}
        _raises(lambda: offers.archive("discount_rules", 42, ADMIN), "not found")
    # before the migration: a sentence, nothing written
    fake = _db(discount_rules=rules, columns={"discount_rules": set(rules[0])})
    with _patched(fake):
        _raises(lambda: offers.archive("discount_rules", 1, ADMIN), "offer-ledger update")
    assert fake.writes() == []


@test("campaigns: DELETE archives once the column exists; before it, deleted unless an order used its offer")
def _():
    from app.shop import delete_campaign, list_campaigns
    camps = [{"id": 5, "title": "A", "is_active": True, "sort_order": 1, "rule_id": 1},
             {"id": 6, "title": "B", "is_active": True, "sort_order": 2, "rule_id": None}]
    fake = _db(shop_campaigns=camps, discount_rules=[_rule(1, "qty_tier", pct=5, min_qty=2, uses=0)])
    with _patched(fake):
        assert delete_campaign(5, by=ADMIN) == {"archived": True}
        assert [c["id"] for c in list_campaigns()] == [6]
        assert [c["status"] for c in list_campaigns(include_archived=True)] == ["archived", "live"]
    assert len(fake.rows("shop_campaigns")) == 2
    cols = {"shop_campaigns": {"id", "title", "is_active", "sort_order", "rule_id"}}
    fake = _db(columns=cols, shop_campaigns=camps,
               discount_rules=[_rule(1, "qty_tier", pct=5, min_qty=2)],
               shop_order_lines=[{"id": 1, "order_id": 1, "item_code": "T02", "rule_ids": [1]}])
    with _patched(fake):
        _raises(lambda: delete_campaign(5), "switch it off instead")
        assert delete_campaign(6) == {"archived": False}
    assert [c["id"] for c in fake.rows("shop_campaigns")] == [5]


@test("api: DELETE a used rule → 409; archive / restore routes; ?archived=1; the gates are Shop Admin")
def _():
    rules = [_rule(1, "qty_tier", pct=5, min_qty=2, uses=0), _rule(2, "coupon", amount=1, min_value=5, coupon="U1", uses=2)]
    fake = _db(discount_rules=rules)
    with _api(fake) as c:
        r = c.delete("/shop/rules/2")
        assert r.status_code == 409 and "archive it instead" in r.json()["detail"], r.text
        r = c.post("/shop/rules/2/archive")
        assert r.status_code == 200 and r.json()["archived_at"], r.text
        r = c.get("/shop/rules")
        assert [x["id"] for x in r.json()["rules"]] == [1] and r.json()["archived"] == 1
        assert r.json()["rules"][0]["referenced"] is False and r.json()["rules"][0]["orders"] == 0
        assert [x["id"] for x in c.get("/shop/rules?archived=1").json()["rules"]] == [1, 2]
        assert c.post("/shop/rules/2/restore").status_code == 200
        assert c.delete("/shop/rules/1").status_code == 200
        assert c.post("/shop/rules/77/archive").status_code == 404
    audit = [a["action"] for a in fake.rows("shop_admin_audit")]
    assert audit == ["archive", "restore", "delete"], audit
    from fastapi.routing import APIRoute
    import app.main as m
    gates = {(meth, r.path): r for r in m.app.routes if isinstance(r, APIRoute) for meth in r.methods}
    for key in (("POST", "/shop/rules/{rule_id}/archive"), ("POST", "/shop/rules/{rule_id}/restore"),
                ("POST", "/shop/campaigns/{campaign_id}/restore"), ("GET", "/shop/offers/performance"),
                ("GET", "/shop/followups/lift")):
        deps = [d.call for d in gates[key].dependant.dependencies]
        assert any(getattr(d, "feature", None) == "Shop Admin" for d in deps), key


@test("catalog: has_coupons is true only while a coupon this storefront can use is live (no dead coupon field)")
def _():
    from app.shop import catalog_payload
    with _patched(_db(), _ctx([])):
        assert catalog_payload("tok-test-token-value")["settings"]["has_coupons"] is False
    with _patched(_db(), _ctx([_rule(4, "coupon", amount=1, min_value=5, coupon="ALL1")])):
        assert catalog_payload("tok-test-token-value")["settings"]["has_coupons"] is True
    ctx = _ctx([_rule(4, "coupon", amount=1, min_value=5, coupon="REP2", refs=["rep-two"])])
    with _patched(_db(), ctx):
        assert catalog_payload("tok-test-token-value", "rep-one")["settings"]["has_coupons"] is False
        assert catalog_payload("tok-test-token-value", "rep-two")["settings"]["has_coupons"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# 7. the readouts
# ═══════════════════════════════════════════════════════════════════════════════

@test("readouts: v_offer_performance / v_followup_lift answered honestly — not switched on yet, nothing ran yet, totals over complete weeks only")
def _():
    import app.db_read as dbr
    from app import followup_lift, offers

    def boom(_sql):
        raise RuntimeError('relation "v_offer_performance" does not exist')
    with _swap(dbr, exec_sql=boom):
        p = offers.performance()
        assert p == {"available": False, "ran": False, "rows": [], "note": "The offer ledger is not switched on yet."}
        assert followup_lift.lift()["available"] is False
    rows = [{"rule_id": 1, "orders": 0}, {"rule_id": 2, "orders": 0}]
    with _swap(dbr, exec_sql=lambda sql: rows if "v_offer_performance" in sql else []):
        p = offers.performance()
        assert p["available"] and p["ran"] is False and p["rows"] == rows and "200 merchants" in p["note"]
    lift = [{"arm": "served", "shops": 20, "bought_14d": 8, "net_bhd_14d": "100.500", "complete": True},
            {"arm": "holdout", "shops": 5, "bought_14d": 1, "net_bhd_14d": "10.000", "complete": True},
            {"arm": "served", "shops": 9, "bought_14d": 9, "net_bhd_14d": "999.000", "complete": False}]
    with _swap(dbr, exec_sql=lambda sql: lift):
        out = followup_lift.lift()
    assert out["totals"] == {"served": {"shops": 20, "bought_14d": 8, "net_bhd_14d": 100.5, "buy_rate_pct": 40.0,
                                        "net_bhd_per_shop": 5.025},
                             "holdout": {"shops": 5, "bought_14d": 1, "net_bhd_14d": 10.0, "buy_rate_pct": 20.0,
                                         "net_bhd_per_shop": 2.0}}
    assert "large" in out["note"]


# ═══════════════════════════════════════════════════════════════════════════════
# 8. follow-up exposures
# ═══════════════════════════════════════════════════════════════════════════════

LISTING = {"due": [{"shop": "ALPHA MOBILE", "rank": 1, "holdout": False}, {"shop": "BETA PHONES", "rank": 2, "holdout": True},
                   {"shop": "GAMMA TEL", "rank": 3, "holdout": False}],
           "lapsed": [{"shop": "DELTA", "rank": 1, "holdout": True}]}


@test("exposures (pure): served and held-back rows, kind and combined rank, keyed like the book ('f:<Focus name>')")
def _():
    from app.followup_lift import exposure_rows
    rows = exposure_rows({"id": 1}, LISTING, "2026-09-27")
    assert [(r["shop_key"], r["kind"], r["rank"], r["holdout"]) for r in rows] == [
        ("f:ALPHA MOBILE", "due", 1, False), ("f:BETA PHONES", "due", 2, True), ("f:GAMMA TEL", "due", 3, False),
        ("f:DELTA", "lapsed", 1, True)]
    assert all(r["served_on"] == "2026-09-27" and r["salesman_id"] == 1 for r in rows)


@test("exposures: written once per rep per Bahrain day (memo, then the table), skipped before the migration, never raises")
def _():
    from app import followup_lift, followups
    calls = []

    def fake_followups(name, **kw):
        calls.append((name, kw))
        return LISTING
    sm = dict(SALESMEN[0])
    fake = _db()
    now = datetime(2026, 9, 27, 20, 30, tzinfo=timezone.utc)          # 23:30 in Bahrain
    with _patched(fake), _swap(followups, followups=fake_followups):
        out = followup_lift.log_exposures(sm, now)
        assert out == {"served_on": "2026-09-27", "rows": 4, "holdout": 2}, out
        assert followup_lift.log_exposures(sm, now)["skipped"] == "already logged today"
        followup_lift.forget()                                        # another process, same day
        assert followup_lift.log_exposures(sm, now)["skipped"] == "already logged today"
        nxt = followup_lift.log_exposures(sm, now + timedelta(hours=1))   # 00:30 the next Bahrain day
        assert nxt["served_on"] == "2026-09-28"
    assert calls[0] == ("REP ONE", {"include_holdout": True})
    assert len(fake.rows("followup_exposures")) == 8
    with _patched(_db(missing={"followup_exposures"})), _swap(followups, followups=fake_followups):
        assert followup_lift.log_exposures(sm, now)["skipped"] == "followup_exposures not installed"
    fake = _db()
    fake.fail[("upsert", "followup_exposures")] = RuntimeError("down")
    with _patched(fake), _swap(followups, followups=fake_followups):
        assert "error" in followup_lift.log_exposures(sm, now)
    assert followup_lift.log_exposures(None)["skipped"] == "no rep"
    assert followup_lift.log_exposures({"id": 3, "focus_name": " "})["skipped"] == "no Focus name"


@test("exposures via the API: the rep's own Due read and Today card log (after the answer); an admin's view never does")
def _():
    from app import followup_lift, followups
    logged = []
    fake = _db()
    with _api(fake, email=REP, role="salesman") as c, \
            _swap(followups, followups=lambda name, **kw: {"due": [], "lapsed": [], "counts": {}, "rep": name}), \
            _swap(followups, today=lambda sm, email, **kw: {"errors": {}, "due": []}), \
            _swap(followup_lift, log_exposures=lambda sm, now=None: logged.append(sm["id"])):
        assert c.get("/shop/me/followups").status_code == 200
        assert c.get("/shop/me/today").status_code == 200
    assert logged == [1, 1], logged
    logged.clear()
    with _api(fake) as c, \
            _swap(followups, followups=lambda name, **kw: {"due": [], "lapsed": [], "counts": {}}), \
            _swap(followup_lift, log_exposures=lambda sm, now=None: logged.append(sm["id"])):
        assert c.get("/shop/me/followups?rep=REP%20ONE").status_code == 200
        assert c.get("/shop/me/followups").status_code == 200
    assert logged == [], "an admin's look at a rep's list is not an exposure"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. rate limits per real client
# ═══════════════════════════════════════════════════════════════════════════════

def _req(headers: dict, host: str = "10.0.0.9", path: str = "/x", path_params: dict | None = None):
    from starlette.requests import Request
    scope = {"type": "http", "method": "POST", "path": path, "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": (host, 1234), "server": ("testserver", 80), "scheme": "http",
             "path_params": path_params or {}}
    return Request(scope)


@test("client ip: CF-Connecting-IP behind the trusted proxy (the right-most entry is a Cloudflare edge); never trusted locally; switchable; junk ignored")
def _():
    from app import ratelimit
    from app.config import settings
    old = (settings.trusted_proxy_hops, settings.trust_cf_connecting_ip)
    try:
        settings.trusted_proxy_hops, settings.trust_cf_connecting_ip = 1, True
        edge = {"x-forwarded-for": "41.1.1.1, 172.70.1.1"}
        assert ratelimit.client_ip(_req(edge)) == "172.70.1.1", "without the header: the hop rule, as before"
        assert ratelimit.client_ip(_req({**edge, "cf-connecting-ip": "41.1.1.1"})) == "41.1.1.1"
        assert ratelimit.client_ip(_req({**edge, "cf-connecting-ip": "2a02:4780::1"})) == "2a02:4780::1"
        assert ratelimit.client_ip(_req({**edge, "cf-connecting-ip": "not-an-ip"})) == "172.70.1.1"
        settings.trust_cf_connecting_ip = False
        assert ratelimit.client_ip(_req({**edge, "cf-connecting-ip": "41.1.1.1"})) == "172.70.1.1"
        settings.trusted_proxy_hops, settings.trust_cf_connecting_ip = 0, True
        assert ratelimit.client_ip(_req({**edge, "cf-connecting-ip": "41.1.1.1"})) == "10.0.0.9", "no proxy: no header"
        settings.trusted_proxy_hops = 1
        body = _req({"cf-connecting-ip": "41.1.1.1"})
        body._json = {"device_id": "dev-a"}
        k = ratelimit.device_key(body)
        assert k.startswith("41.1.1.1|d:") and "dev-a" not in k
        assert ratelimit.device_key(_req({"cf-connecting-ip": "41.1.1.1"})) == "41.1.1.1"
        assert ratelimit.device_key(_req({"cf-connecting-ip": "41.1.1.1", "x-device-id": "dev-b"})) != "41.1.1.1"
        tk = ratelimit.order_token_key(_req({"cf-connecting-ip": "41.1.1.1"}, path_params={"order_token": "t" * 32}))
        assert tk.startswith("41.1.1.1|o:") and "tttt" not in tk
    finally:
        settings.trusted_proxy_hops, settings.trust_cf_connecting_ip = old


@test("client ip: the first request of a process logs the header SHAPE once — never an address")
def _():
    import logging
    from app import ratelimit
    from app.config import settings
    records = []

    class H(logging.Handler):
        def emit(self, rec):
            records.append(rec.getMessage())
    h = H()
    ratelimit.log.addHandler(h)
    old_level = ratelimit.log.level
    ratelimit.log.setLevel(logging.INFO)
    old = settings.trusted_proxy_hops
    try:
        settings.trusted_proxy_hops = 1
        with _swap(ratelimit, _shape_logged=False):
            ratelimit.client_ip(_req({"x-forwarded-for": "41.1.1.1, 172.70.1.1", "cf-connecting-ip": "41.1.1.1"}))
            ratelimit.client_ip(_req({"x-forwarded-for": "41.1.1.2"}))
    finally:
        settings.trusted_proxy_hops = old
        ratelimit.log.removeHandler(h)
        ratelimit.log.setLevel(old_level)
    assert len(records) == 1 and "entries=2" in records[0] and "cf-connecting-ip=present" in records[0], records
    assert "41.1.1" not in records[0] and "172.70" not in records[0]


@contextmanager
def _bare_api(fake, ctx=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from app.config import settings
    from app.ratelimit import rate_limit_key
    from app.shop_api import register
    api = FastAPI()
    lim = Limiter(key_func=rate_limit_key)
    api.state.limiter = lim
    api.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    register(api, lim)
    old = (settings.trusted_proxy_hops, settings.trust_cf_connecting_ip)
    settings.trusted_proxy_hops, settings.trust_cf_connecting_ip = 1, True
    try:
        with _patched(fake, ctx):
            yield TestClient(api)
    finally:
        settings.trusted_proxy_hops, settings.trust_cf_connecting_ip = old


@test("limits: restock — one bucket per device behind one carrier address, the address alone without a device, and a per-address net")
def _():
    import app.shop as s
    hdr = {"x-forwarded-for": "41.1.1.1, 172.70.1.1", "cf-connecting-ip": "41.1.1.1"}
    with _bare_api(_db()) as c, _swap(s, add_restock=lambda *a, **k: True):
        def hit(dev, h=hdr):
            body = {"item_code": "T02"} if dev is None else {"item_code": "T02", "device_id": dev}
            return c.post("/public/market/restock", json=body, headers=h).status_code
        assert [hit("dev-a") for _ in range(20)] == [200] * 20
        assert hit("dev-a") == 429, "the device's own 20 a minute"
        assert hit("dev-b") == 200, "a neighbour behind the same address is not blocked"
        other = {**hdr, "cf-connecting-ip": "41.9.9.9"}
        assert hit("dev-a", other) == 200, "the same device elsewhere is a different client"
        codes = [hit(f"dev-{i}") for i in range(60)]
        # the 60-a-minute net per address: 21 or 22 already spent above (whether the refused 21st
        # dev-a request reached the net depends on which limit is checked first)
        assert 429 in codes and codes.count(200) in (38, 39), codes.count(200)
    with _bare_api(_db()) as c, _swap(s, add_restock=lambda *a, **k: True):
        codes = [c.post("/public/market/restock", json={"item_code": "T02"}, headers=hdr).status_code for _ in range(21)]
        assert codes[:20] == [200] * 20 and codes[20] == 429, "no device: exactly the old per-address limit"


@test("limits: cancel is keyed per order token (3 an hour each) under a 20-an-hour address net; the market order per device")
def _():
    import app.shop as s
    from app.shop import ShopError
    hdr = {"x-forwarded-for": "41.1.1.1, 172.70.1.1", "cf-connecting-ip": "41.1.1.1"}

    def nope(*_a, **_k):
        raise ShopError("Order not found.")
    with _bare_api(_db()) as c, _swap(s, cancel_by_customer=nope):
        def cancel(tok):
            return c.post(f"/public/shop/order/{tok}/cancel", json={}, headers=hdr).status_code
        assert [cancel("a" * 30) for _ in range(3)] == [400] * 3
        assert cancel("a" * 30) == 429
        assert cancel("b" * 30) == 400, "another shop's order behind the same address still cancels"
    with _bare_api(_db()) as c, _swap(s, create_order=nope, market_enabled=lambda: True):
        def order(dev):
            return c.post("/public/market/order", json=_market_body([("T02", 1)], device=dev), headers=hdr).status_code
        assert [order("dev-a") for _ in range(10)] == [400] * 10 and order("dev-a") == 429
        assert order("dev-b") == 400


@test("limits: the stacked decorators are really on the four merchant routes (and keep the per-address net)")
def _():
    import app.main as m
    from app import ratelimit
    want = {"app.shop_api.market_order": {("30 per 1 minute", "rate_limit_key"), ("10 per 1 minute", "device_key")},
            "app.shop_api.shop_order": {("20 per 1 minute", "rate_limit_key"), ("5 per 1 minute", "device_key")},
            "app.shop_api.shop_order_cancel": {("20 per 1 hour", "rate_limit_key"), ("3 per 1 hour", "order_token_key")},
            "app.shop_api.market_restock": {("60 per 1 minute", "rate_limit_key"), ("20 per 1 minute", "device_key")},
            "app.shop_api.market_upcoming_interest": {("60 per 1 minute", "rate_limit_key"),
                                                      ("20 per 1 minute", "device_key")}}
    for name, lims in want.items():
        got = {(str(lim.limit), lim.key_func.__name__) for lim in m.limiter._route_limits.get(name, [])}
        assert got == lims, (name, got)
    assert ratelimit.device_key.__module__ == "app.ratelimit"


# ═══════════════════════════════════════════════════════════════════════════════
# 10. the badge log
# ═══════════════════════════════════════════════════════════════════════════════

@test("badge log: one row per item per Bahrain day (badges, stock, 90-day units), then skipped; a shop_jobs job; skipped before the migration")
def _():
    from app import offers, shop_jobs
    fake = _db()
    now = datetime(2026, 9, 27, 5, 0, tzinfo=timezone.utc)
    with _patched(fake):
        out = offers.log_badges_daily(now)
        assert out["rows"] == len(ITEMS) and out["as_of"] == "2026-09-27"
        assert offers.log_badges_daily(now)["skipped"] == "already logged today"
        offers._badge_done.clear()
        assert offers.log_badges_daily(now)["skipped"] == "already logged today"
    rows = fake.rows("shop_badge_log")
    assert len(rows) == len(ITEMS) and {r["item_code"] for r in rows} == {i["item_code"] for i in ITEMS}
    assert all(isinstance(r["badges"], list) and "stock_qty" in r and r["price_bhd"] for r in rows)
    with _patched(_db(missing={"shop_badge_log"})):
        assert offers.log_badges_daily(now)["skipped"] == "shop_badge_log not installed"
    assert ("badge_log", shop_jobs.badge_log) in shop_jobs.JOBS


# ═══════════════════════════════════════════════════════════════════════════════
# 11. the nightly backup
# ═══════════════════════════════════════════════════════════════════════════════

@test("backup: the destination is %LOCALAPPDATA%\\yq-backups or YQ_BACKUP_DIR, and anything inside OneDrive is refused")
def _():
    from scripts import nightly_backup as nb
    env = {"LOCALAPPDATA": r"C:\Users\x\AppData\Local", "OneDrive": r"C:\Users\x\OneDrive - YqBahrain"}
    assert str(nb.backup_root(env)).endswith(os.path.join("Local", "yq-backups"))
    for bad in (r"C:\Users\x\OneDrive - YqBahrain\backups", r"D:\Sync\OneDrive\b", r"E:\Dropbox\yq"):
        e2 = {**env, "YQ_BACKUP_DIR": bad, "YQ_BACKUP_SYNCED_DIRS": r"E:\Dropbox"}
        try:
            nb.backup_root(e2)
            raise AssertionError(bad)
        except SystemExit as e:
            assert "REFUSING" in str(e)
    assert str(nb.backup_root({**env, "YQ_BACKUP_DIR": r"D:\yq-backups"})) == r"D:\yq-backups"


@test("backup: retention keeps the newest archive of each of the last 14 days and of each of the last 8 weeks; nothing else is ours to delete")
def _():
    from scripts import nightly_backup as nb
    today = date(2026, 9, 27)                                   # a Sunday
    names = []
    for back in range(0, 120):
        d = today - timedelta(days=back)
        names.append(f"yq-backup_{d.isoformat()}_0230.7z")
    names += ["yq-backup_2026-09-27_0100.7z", "yq-backup_2026-09-20_2359.zip", "notes.txt", "yq-backup_bad.7z",
              "UNENCRYPTED-BACKUP-WARNING.txt"]
    keep, delete = nb.plan_retention(names, today)
    days = sorted({k[10:20] for k in keep})
    assert "yq-backup_2026-09-27_0100.7z" in delete and "yq-backup_2026-09-27_0230.7z" in keep, "newest of the day"
    daily = [d for d in days if d >= "2026-09-14"]
    assert len(daily) == 14
    weekly = [d for d in days if d < "2026-09-14"]
    # weeks before the 14 days, up to 8 weeks back (Mondays 2026-08-03 .. 2026-09-21): their Sundays
    assert weekly == ["2026-08-09", "2026-08-16", "2026-08-23", "2026-08-30", "2026-09-06", "2026-09-13"], weekly
    assert "yq-backup_2026-09-20_2359.zip" in keep and "yq-backup_2026-09-20_0230.7z" in delete
    assert "notes.txt" not in keep + delete and "yq-backup_bad.7z" not in keep + delete
    assert len(keep) == 20 and len(keep) + len(delete) == 122


@test("backup: 7-Zip AES-256 with hidden names when there is a password; a loud warning file and a plain zip otherwise; never uploads")
def _():
    import tempfile
    from scripts import nightly_backup as nb
    cmd = nb.seven_zip_cmd("7z.exe", Path("C:/b/yq-backup_x.7z"), Path("C:/b/work"), "s3cret")
    assert cmd[:4] == ["7z.exe", "a", "-t7z", "-mx=7"] and "-mhe=on" in cmd and "-ps3cret" in cmd
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "work").mkdir()
        (tmp / "work" / "shop_orders.csv").write_text("id\n1\n", encoding="utf-8")
        archive, encrypted = nb.make_archive(tmp / "work", tmp, "2026-09-27_0230", password=None, exe="7z.exe")
        assert not encrypted and archive.name == "yq-backup_2026-09-27_0230.zip" and archive.exists()
        warn = (tmp / nb.WARNING_FILE).read_text(encoding="utf-8")
        assert "NOT ENCRYPTED" in warn and nb.PASSWORD_ENV in warn
    src = (ROOT / "scripts" / "nightly_backup.py").read_text(encoding="utf-8")
    for banned in ("requests", "httpx", "boto", "urllib", "smtplib", "supabase", "upload("):
        assert banned not in src, banned
    assert "read_only = True" in src and "db_backup.backup(None, folder, all_tables=True)" in src
    assert "password" not in nb.AUTH_SQL.lower() and "encrypted_password" not in nb.AUTH_SQL
    cmd_file = (ROOT / "scripts" / "nightly_backup.cmd").read_text(encoding="utf-8")
    assert "python -m scripts.nightly_backup" in cmd_file and "yq-backups" in cmd_file


# ═══════════════════════════════════════════════════════════════════════════════
# 12. the portal pages (source checks)
# ═══════════════════════════════════════════════════════════════════════════════

def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@test("Salesmen table: a status pill instead of the one-click Active toggle, name + code in one cell, contacts in the dialog, pinned actions, help collapsed")
def _():
    src = _read("web/src/pages/Salesmen.tsx")
    table = src[src.index("const cols: Column<Salesman>[]"):src.index("// R3a: the rollups")]
    assert "toggleActive.mutate(r)} label" not in table and "<Toggle" not in table, "no inline Active toggle"
    assert "StatusPill" in table and "'Not linked'" in src and "'Inactive'" in src
    assert "key: 'phone'" not in table and "key: 'email'" not in table, "phone and email live in the edit dialog"
    assert "referral_code" in table[:table.index("key: 'link'")], "name and code share the first cell"
    assert "pinLast" in src and "How rep links work" in src and "<details" in src
    dt = _read("web/src/components/DataTable.tsx")
    assert "pinLast" in dt and "sticky right-0" in dt


@test("Offers & Rules: the per-rule readout with an honest empty state, archive instead of delete for a used rule, and the hold-out picker")
def _():
    src = _read("web/src/pages/ShopRules.tsx")
    assert "/shop/offers/performance" in src and "No offer has run yet" in src
    assert "/shop/rules/${r.id}/${restore ? 'restore' : 'archive'}" in src and "r.referenced" in src
    assert "?archived=1" in src and "Show archived" in src
    assert "bucket" in src and "Hold-out" in src
    camp = _read("web/src/pages/shop-ops/CampaignsSection.tsx")
    assert "Archive" in camp
    lift = _read("web/src/pages/SalesmenLift.tsx")
    assert "/shop/followups/lift" in lift and "No complete 14-day window yet" in lift
    assert "FollowupLiftCard" in _read("web/src/pages/Salesmen.tsx")


# ═══════════════════════════════════════════════════════════════════════════════
# 13. the migration and its reverse, as text
# ═══════════════════════════════════════════════════════════════════════════════

@test("migration text: additive and idempotent, guarded on R7c, RLS + revoke on every new table, views for yq_readonly only, no CASCADE / security_invoker")
def _():
    sql = MIGRATION.read_text(encoding="utf-8")
    body = "\n".join(ln for ln in sql.splitlines() if not ln.strip().startswith("--"))
    low = body.lower()
    assert "cascade" not in low.replace("on delete cascade", ""), "no DROP ... CASCADE"
    assert not re.search(r"\bdrop\s+(table|view|column|function)\b", low), "a forward migration drops nothing"
    assert "security_invoker" not in low.replace("must not be security_invoker", "").replace("'%security_invoker%'", "")
    assert "delete from" not in low and "update shop_orders" not in low and "update shop_order_lines" not in low
    assert "apply scripts/r7c_order_lines_qty_migration.sql first" in body
    for t in ("shop_order_discounts", "shop_badge_log", "followup_exposures"):
        assert f"create table if not exists {t}" in body and f"alter table {t} enable row level security" in body
        assert f"revoke all on {t} from anon, authenticated" in body
    for v in ("v_offer_performance", "v_followup_lift"):
        assert f"create or replace view {v}" in body
        assert f"revoke all on {v} from anon, authenticated" in body and f"grant select on {v} to yq_readonly" in body
    for f in ("shop_coupon_reserve(bigint, boolean)", "shop_coupon_release(bigint)"):
        assert f"revoke all on function {f} from public, anon, authenticated" in body
        assert f"grant execute on function {f} to service_role" in body
    assert body.count("add column if not exists archived_at") == 2
    assert "(max_uses is null or coalesce(uses, 0) < max_uses)" in body and "greatest(coalesce(uses, 0) - 1, 0)" in body
    assert "unique (served_on, salesman_id, shop_key, kind)" in body and "primary key (as_of, item_code)" in body


@test("reverse text: refuses while any ledger / badge / exposure row or archived row exists unless yq.offer_ledger_drop = 'yes'; drops only its own objects")
def _():
    sql = REVERSE.read_text(encoding="utf-8")
    low = "\n".join(ln for ln in sql.lower().splitlines() if not ln.strip().startswith("--"))
    assert "yq.offer_ledger_drop" in sql and "raise exception" in low
    assert "cascade" not in low
    for obj in ("drop view if exists v_followup_lift", "drop view if exists v_offer_performance",
                "drop function if exists shop_coupon_reserve(bigint, boolean)", "drop function if exists shop_coupon_release(bigint)",
                "drop table if exists followup_exposures", "drop table if exists shop_badge_log",
                "drop table if exists shop_order_discounts", "alter table discount_rules drop column if exists archived_at",
                "alter table shop_campaigns drop column if exists archived_at"):
        assert obj in sql, obj
    for base in ("shop_orders", "shop_order_lines", "shop_order_events", "shop_customers", "salesmen", "statements"):
        assert f"drop table if exists {base}" not in low and f"delete from {base}" not in low


# ═══════════════════════════════════════════════════════════════════════════════
# 14. a local Postgres replay (SKIPs cleanly without a local cluster — never production)
# ═══════════════════════════════════════════════════════════════════════════════

LOCAL_DSN = os.environ.get("YQ_LOCAL_PG_R7D", os.environ.get("YQ_LOCAL_PG", "postgresql://postgres@localhost:55432/postgres"))

BASE_SCHEMA = """
create role anon nologin; create role authenticated nologin; create role service_role nologin; create role yq_readonly nologin;
create table app_settings (key text primary key, value text);
create table salesmen (id bigint generated always as identity primary key, name text not null unique);
create table discount_rules (
  id bigint generated always as identity primary key, name text not null, kind text not null
    check (kind in ('qty_tier','cart_value','coupon','bundle_price','salesman_offer')),
  scope jsonb not null default '{}'::jsonb, pct_off numeric, amount_off_bhd numeric, coupon_code text unique,
  starts_at timestamptz, ends_at timestamptz, max_uses integer, uses integer not null default 0,
  is_active boolean not null default true, updated_at timestamptz default now());
create table shop_campaigns (id bigint generated always as identity primary key, title text not null,
  is_active boolean not null default true, rule_id bigint references discount_rules(id));
create table shop_orders (
  id bigint generated always as identity primary key, order_no text unique not null, status text not null default 'new',
  salesman_id bigint, customer_id bigint, customer_phone text, is_test boolean default false,
  created_at timestamptz default now());
create table shop_order_lines (
  id bigint generated always as identity primary key,
  order_id bigint not null references shop_orders(id) on delete cascade,
  item_code text not null, qty integer not null, list_price_bhd numeric, unit_price_bhd numeric not null,
  line_total_bhd numeric not null, qty_confirmed integer, unit_price_confirmed numeric(12,3),
  qty_delivered integer, unit_cost_bhd numeric(12,4), added_at_stage text);
create table audit_log (id bigint generated always as identity primary key, ts timestamptz default now(),
  user_email text, event text, detail jsonb);
create table sales_lines (invoice_no text, sale_date date, customer_name text, net_bhd numeric,
  division text, is_giveaway boolean, is_cash_customer boolean);
create view v_sales as select * from sales_lines;
alter table shop_orders enable row level security; alter table shop_order_lines enable row level security;
"""

SEED = """
insert into app_settings values ('shop_vat_rate', '0.10');
insert into salesmen (name) values ('Rep One'), ('Rep Two');
insert into discount_rules (name, kind, pct_off, amount_off_bhd, coupon_code, max_uses, uses) values
  ('Tier 10', 'qty_tier', 10, null, null, null, 0),
  ('Save 2', 'coupon', null, 2, 'SAVE2', 2, 1),
  ('Unused', 'cart_value', 5, null, null, null, 0);
insert into shop_orders (order_no, status, salesman_id, customer_id, customer_phone) values
  ('YQ-1', 'confirmed', 1, 10, '97300000001'), ('YQ-2', 'new', 2, null, '97300000002'),
  ('YQ-3', 'cancelled', 1, 11, '97300000003');
insert into shop_order_lines (order_id, item_code, qty, list_price_bhd, unit_price_bhd, line_total_bhd,
                              qty_confirmed, unit_price_confirmed, unit_cost_bhd) values
  (1, 'T02', 10, 2.95, 2.655, 26.55, 6, 2.655, 1.1),
  (1, 'X05', 5, 2.0, 2.0, 10.0, 5, 2.0, 0.8),
  (2, 'T02', 10, 2.95, 2.655, 26.55, null, null, null),
  (3, 'T02', 10, 2.95, 2.655, 26.55, null, null, 1.1);
-- B = the Monday three weeks back (date_trunc('week', current_date)::date - 21): every date below is
-- B + n, so the exposures of one shop always fall in one week whatever day the test runs
insert into sales_lines values
  ('INV-1', date_trunc('week', current_date)::date - 19, 'ALPHA MOBILE', 50, 'Accessories', false, false),
  ('INV-2', date_trunc('week', current_date)::date - 17, 'BETA PHONES', 30, 'Accessories', false, false),
  ('INV-3', date_trunc('week', current_date)::date - 17, 'BETA PHONES', 5, 'Accessories', true, false),
  ('INV-4', date_trunc('week', current_date)::date - 1, 'ALPHA MOBILE', 1, 'Accessories', false, false);
"""

LEDGER = """
insert into shop_order_discounts (order_id, line_id, item_code, rule_id, rule_snapshot, kind, level, amount_bhd,
                                  amount_confirmed_bhd, clamped, stage) values
  (1, 1, 'T02', 1, '{"id": 1}', 'qty_tier', 'line', 2.95, 1.77, false, 'confirmed'),
  (1, null, null, 2, '{"id": 2}', 'coupon', 'cart', 2.0, 1.419, true, 'confirmed'),
  (2, 3, 'T02', 1, '{"id": 1}', 'qty_tier', 'line', 2.95, null, false, 'placed'),
  (3, 4, 'T02', 1, '{"id": 1}', 'qty_tier', 'line', 2.95, null, false, 'placed');
insert into followup_exposures (served_on, salesman_id, shop_key, kind, rank, holdout) values
  (date_trunc('week', current_date)::date - 21, 1, 'f:ALPHA MOBILE', 'due', 1, false),
  (date_trunc('week', current_date)::date - 20, 1, 'f:ALPHA MOBILE', 'due', 1, false),
  (date_trunc('week', current_date)::date - 21, 1, 'f:BETA PHONES', 'due', 2, true),
  (date_trunc('week', current_date)::date - 21, 1, 'f:GAMMA TEL', 'due', 3, false);
insert into audit_log (ts, event, detail) values
  (((date_trunc('week', current_date)::date - 20)::timestamp + interval '10 hours') at time zone 'Asia/Bahrain',
   'followup.tap', '{"salesman_id": 1, "shop": "ALPHA MOBILE", "channel": "wa"}');
"""


def _local_conn():
    try:
        import psycopg
    except ImportError:
        print("  SKIP: psycopg not installed")
        return None
    try:
        conn = psycopg.connect(LOCAL_DSN, connect_timeout=2)
    except Exception as e:  # noqa: BLE001
        print(f"  SKIP: local Postgres unreachable ({type(e).__name__})")
        return None
    with conn.cursor() as cur:
        cur.execute("select to_regclass('public.shop_orders') is not null")
        if cur.fetchone()[0]:
            print("  SKIP: this database already has shop_orders (the replay builds its own synthetic tables)")
            conn.close()
            return None
    return conn


@test("local replay: apply twice, the per-rule and follow-up numbers, the coupon RPCs, the FK on a used rule, the reverse guard (SKIP without a cluster)")
def _():
    import psycopg
    conn = _local_conn()
    if conn is None:
        return
    mig = MIGRATION.read_text(encoding="utf-8")
    rev = REVERSE.read_text(encoding="utf-8")
    try:
        with conn.cursor() as cur:
            cur.execute("select 1 from pg_roles where rolname = 'anon'")
            cur.execute(BASE_SCHEMA.split("\n", 2)[2] if cur.fetchone() else BASE_SCHEMA)
            cur.execute(SEED)
            cur.execute(mig)
            cur.execute(mig)                                  # idempotent
            cur.execute(LEDGER)
            cur.execute("select rule_id, orders, units, merchants, reps, list_value_bhd, discount_bhd, discount_placed_bhd, "
                        "revenue_ex_vat_bhd, cost_bhd, gm_bhd, gm_pct, uncosted_lines, clamp_count "
                        "from v_offer_performance order by rule_id")
            got = cur.fetchall()
            # rule 1: YQ-1 line (6 × 2.655 = 15.93, cost 6.6) + YQ-2 line (10 × 2.655, no cost); YQ-3 cancelled
            r1 = got[0]
            assert r1[0] == 1 and r1[1] == 2 and r1[2] == 16 and r1[3] == 2 and r1[4] == 2, r1
            assert r1[5] == Decimal("47.200") and r1[6] == Decimal("4.720") and r1[7] == Decimal("5.900"), r1
            assert r1[8] == Decimal("14.482") and r1[9] == Decimal("6.600") and r1[10] == Decimal("7.882"), r1
            assert r1[12] == 1 and r1[13] == 0, r1
            # rule 2 (cart): YQ-1 whole order 25.93 − 1.419 = 24.511 incl. VAT → 22.283 ex; cost 6.6 + 4.0
            r2 = got[1]
            assert r2[1] == 1 and r2[2] == 11 and r2[5] == Decimal("27.700") and r2[6] == Decimal("1.419"), r2
            assert r2[8] == Decimal("22.283") and r2[9] == Decimal("10.600") and r2[13] == 1, r2
            assert got[2][1] == 0 and got[2][8] is None, "an unused rule is a row of zeros"
            # the follow-up readout: ALPHA once for the week (first day), bought on day −20 (and −1 is past 14 days)
            cur.execute("select arm, shops, bought_14d, net_bhd_14d, tapped, complete from v_followup_lift order by arm")
            lift = cur.fetchall()
            assert lift == [("holdout", 1, 1, Decimal("30.000"), 0, True), ("served", 2, 1, Decimal("50.000"), 1, True)], lift
            # the coupon counter: max 2, 1 used → one more, then none; force ignores the cap; release floors at 0
            cur.execute("select shop_coupon_reserve(2)")
            assert cur.fetchone()[0] == 2
            cur.execute("select shop_coupon_reserve(2)")
            assert cur.fetchone()[0] is None
            cur.execute("select shop_coupon_reserve(2, true)")
            assert cur.fetchone()[0] == 3
            cur.execute("select shop_coupon_reserve(1)")
            assert cur.fetchone()[0] is None, "a tier is not a coupon"
            cur.execute("update discount_rules set archived_at = now() where id = 2")
            cur.execute("select shop_coupon_reserve(2)")
            assert cur.fetchone()[0] is None, "an archived coupon is never taken"
            for _ in range(5):
                cur.execute("select shop_coupon_release(2)")
            cur.execute("select uses from discount_rules where id = 2")
            assert cur.fetchone()[0] == 0
            # a used rule can never be hard-deleted; checks hold
            for bad, exc in (("delete from discount_rules where id = 1", psycopg.errors.ForeignKeyViolation),
                             ("insert into shop_order_discounts (order_id, rule_id, kind, level, amount_bhd) "
                              "values (1, 1, 'qty_tier', 'row', 1)", psycopg.errors.CheckViolation),
                             ("insert into shop_order_discounts (order_id, rule_id, kind, level, amount_bhd) "
                              "values (1, 1, 'qty_tier', 'line', -1)", psycopg.errors.CheckViolation),
                             ("insert into followup_exposures (served_on, salesman_id, shop_key, kind, holdout) "
                              "values (date_trunc('week', current_date)::date - 21, 1, 'f:ALPHA MOBILE', 'due', false)",
                              psycopg.errors.UniqueViolation)):
                cur.execute("savepoint s")
                try:
                    cur.execute(bad)
                    raise AssertionError(f"accepted: {bad}")
                except exc:
                    cur.execute("rollback to savepoint s")
            cur.execute("select count(*) from information_schema.role_table_grants where table_schema = 'public' "
                        "and table_name in ('shop_order_discounts', 'followup_exposures', 'shop_badge_log', "
                        "'v_offer_performance', 'v_followup_lift') and grantee in ('anon', 'authenticated')")
            assert cur.fetchone()[0] == 0
            # the reverse refuses while there is data …
            cur.execute("savepoint s")
            try:
                cur.execute(rev)
                raise AssertionError("the reverse dropped data without the switch")
            except psycopg.errors.RaiseException as e:
                assert "yq.offer_ledger_drop" in str(e)
                cur.execute("rollback to savepoint s")
            cur.execute("select count(*) from shop_orders")
            n_orders = cur.fetchone()[0]
            # … and drops only its own objects with it
            cur.execute("set local yq.offer_ledger_drop = 'yes'")
            cur.execute(rev)
            cur.execute("select count(*) from shop_orders")
            assert cur.fetchone()[0] == n_orders
            cur.execute("select to_regclass('public.shop_order_discounts'), to_regclass('public.v_offer_performance')")
            assert cur.fetchone() == (None, None)
            cur.execute(mig)                                   # and forward again after a reverse
        print("  replay: migration x2, numbers, RPCs, constraints, reverse guard, reverse, migration — one rolled-back transaction")
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
