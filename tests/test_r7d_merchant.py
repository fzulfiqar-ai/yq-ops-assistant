"""R7d — merchant ordering (plan §12, §23 items 7 and 9): the marketplace side of the order heart.

    python -m tests.test_r7d_merchant

Same lightweight runner and the same idea as tests/test_r7c_order_heart.py: every test runs against
an in-memory stand-in for the PostgREST client (eq / in / gte filters, counts, inserts that hand out
ids, upserts, CHECK constraints like the live ones on shop_events.event and
shop_orders.attribution_source). Nothing here reaches a database or the network (CI:
SUPABASE_URL=https://ci.invalid). All data is synthetic — made-up shops, reps, items and numbers.

Covered:
  * the tracking payload — per line the PUBLIC reason key (never 'other', never the rep's note),
    `rep_alerted` from the notify result (a channel that reaches the REP; the owner copy and
    Telegram do not count; None while the fan-out has not run), and still no phone / token / device;
  * the placed response — `rep_alerted` None for a new assigned order (the alert runs after the
    response), False with no rep, the stored outcome on a re-submit;
  * "Same as last time" — the phone reused on the SERVER from the device's own earlier order
    (reuse_token), empty fields filled, typed fields kept, another device / an unknown token refused
    with nothing written, the daily phone cap still applies, staff and the legacy link never reuse,
    and no response ever carries the phone; My orders carries name · shop · area, never the phone;
  * area → rep (shop_area_reps) — parsing, validation on save, the precedence step (after the
    session link and the checkout pick, before the default; a recorded rep still wins), the order
    stored with attribution_source 'default' (the live CHECK) while its events say 'area', and the
    public checkout map (first names, public reps only);
  * 'product_request' — the event kind, the pre-migration fallback to a 'search_zero' row marked
    where='product_request', no fallback for any other refused event, the migration and its reverse;
  * wording — "Sold Out" (capital O) in the API reasons and the market, "Clearing line(s)" instead
    of "Last chance" in both languages, and the harness bans the old words;
  * the pages (source checks) and the node half (web/scripts/merchant_test.mjs — "What changed",
    the ETA date, adopting a tracking link, the words).
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
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


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


MARKET = ROOT / "web" / "src" / "market"
MIGRATION = "scripts/r7d_product_request_migration.sql"
REVERSE = "scripts/r7d_product_request_reverse.sql"

# the live CHECK lists, read read-only from production on 27-Sep-2026
LIVE_EVENTS = ("view", "item", "add", "checkout", "order", "search", "search_zero", "remove", "qty", "cart",
               "checkout_start", "rail_click", "reco_click", "share", "install", "reorder", "cancel", "vitals",
               "push_subscribe", "error")
LIVE_ATTRIBUTION = ("customer_admin", "focus_map", "sticky", "session_ref", "checkout_pick", "staff", "default",
                    "unassigned")

# ── a table-aware fake PostgREST client ─────────────────────────────────────────


def _same(a, b) -> bool:
    return a == b or str(a) == str(b)


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters = "select", None, []
        self.cols, self.want_count, self.lim, self.desc = "*", None, None, False

    def select(self, cols="*", count=None, head=False):
        self.op, self.cols, self.want_count = "select", cols, count
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.op, self.payload, self.on_conflict = "upsert", payload, on_conflict
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
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
        return lambda *a, **kw: self          # like / is_ / or_ … read as "no filter" here

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

    def _check(self, rows: list[dict]) -> None:
        for p in rows:
            if self.table == "shop_events" and p.get("event") not in self.db.events_allowed:
                raise RuntimeError('{"code": "23514", "message": "new row for relation \\"shop_events\\" violates '
                                   'check constraint \\"shop_events_event_check\\""}')
            if self.table == "shop_orders" and "attribution_source" in p and \
                    p["attribution_source"] is not None and p["attribution_source"] not in LIVE_ATTRIBUTION:
                raise RuntimeError('{"code": "23514", "message": "new row for relation \\"shop_orders\\" violates '
                                   'check constraint \\"shop_orders_attribution_source_check\\""}')

    def execute(self):
        db = self.db
        db.calls.append((self.op, self.table, self.payload if self.op != "select" else self.cols, list(self.filters)))
        fail = db.fail.get((self.op, self.table))
        if fail is not None:
            raise fail
        rows = db.tables.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in rows if self._match(r)]
            if self.desc:
                out.reverse()
            data = out[: self.lim] if self.lim else out
            return SimpleNamespace(data=data, count=(len(out) if self.want_count else None))
        items = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.op in ("insert", "upsert"):
            self._check(items)
            out = []
            for p in items:
                if self.op == "upsert" and self.on_conflict:
                    hit = next((r for r in rows if _same(r.get(self.on_conflict), p.get(self.on_conflict))), None)
                    if hit is not None:
                        hit.update(p)
                        out.append(dict(hit))
                        continue
                r = dict(p)
                r.setdefault("id", db.next_id(self.table))
                rows.append(r)
                out.append(dict(r))
            return SimpleNamespace(data=out, count=None)
        if self.op == "update":
            self._check(items)
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
    def __init__(self, tables: dict | None = None, events_allowed=None):
        self.tables: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.calls: list[tuple] = []
        self.fail: dict[tuple, Exception] = {}
        self.events_allowed = tuple(events_allowed) if events_allowed is not None else LIVE_EVENTS + ("product_request",)
        self._ids: dict[str, int] = {}

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        self.calls.append(("rpc", name, params, []))
        n = len([c for c in self.calls if c[0] == "rpc" and c[1] == "shop_next_order_no"])
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=f"YQ-2609-{700 + n:04d}", count=None))

    def next_id(self, table) -> int:
        cur = max([int(r.get("id") or 0) for r in self.tables.get(table, [])] + [self._ids.get(table, 0)])
        self._ids[table] = cur + 1
        return cur + 1

    def rows(self, table: str) -> list[dict]:
        return self.tables.get(table, [])

    def inserts(self, table: str) -> list:
        return [c for c in self.calls if c[0] in ("insert", "upsert") and c[1] == table]


class _patched:
    """Point app.shop / app.database / app.catalog / app.upcoming at the fake, serve `ctx` as the
    cached catalog context, and reset every cache the order path consults."""

    def __init__(self, fake: _FakeDB, ctx: dict | None = None):
        self.fake, self.ctx = fake, ctx

    def __enter__(self):
        import app.catalog as cat
        import app.database as db
        import app.shop as s
        import app.upcoming as up
        self.mods = (s, db, cat, up)
        self.saved = (s.get_client, db.get_client, cat.get_client, up.get_client, s.context,
                      s._ctx_cache["ctx"], s._ctx_cache["at"])
        s.get_client = db.get_client = cat.get_client = up.get_client = lambda: self.fake
        ctx = self.ctx if self.ctx is not None else _ctx()
        s._ctx_cache.update(ctx=ctx, at=time.time() + 10 ** 6)
        s.context = lambda force=False: ctx
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        return self.fake

    def __exit__(self, *_exc):
        s, db, cat, up = self.mods
        s.get_client, db.get_client, cat.get_client, up.get_client, s.context = self.saved[:5]
        s._ctx_cache.update(ctx=self.saved[5], at=self.saved[6])
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
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
PHONE = "97333001122"            # a made-up Bahrain number; it must never reach a response
SALESMEN = [
    {"id": 1, "name": "Rep One", "referral_code": "rep-one", "is_active": True, "user_email": "rep@example.com",
     "whatsapp": "97300000001", "email": "rep1@example.test", "focus_name": "REP ONE"},
    {"id": 2, "name": "Rep Two", "referral_code": "rep-two", "is_active": True, "user_email": "other@example.com",
     "whatsapp": "97300000002", "focus_name": "REP TWO"},
    {"id": 3, "name": "Quiet Rep", "referral_code": "quiet", "is_active": True, "user_email": "quiet@example.com",
     "public_profile": False},
]


def _item(code, price, stock=100, cat="CABLE", moq=1):
    return {"item_code": code, "display_name": f"{code} name", "spec": f"{code} spec", "category": cat,
            "brand": "VFAN", "standard_rate": price, "b2c_rate": None, "product_image_url": None,
            "package_image_url": None, "sort_order": None, "created_at": "2026-07-03T00:00:00+00:00", "moq": moq,
            "pack_size": None, "stock_qty": stock, "stock_as_of": "2026-09-26", "sold_30d": 0, "prev_30d": 0,
            "sold_90d": 0, "customers_30d": 0}


ITEMS = [_item("T02", 2.95), _item("X05", 2.0), _item("C18", 1.25)]


def _ctx(salesmen=None, **settings):
    from app.shop import SETTING_DEFAULTS
    vals = dict(SETTING_DEFAULTS)
    vals.update({"shop_min_order_bhd": "0"})
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: i for i in ITEMS}, "order": [i["item_code"] for i in ITEMS],
            "by_upper": {i["item_code"].upper(): i["item_code"] for i in ITEMS},
            "costs": {"T02": 1.1, "X05": 0.8, "C18": 0.5}, "cost_sources": {"T02": "mrn", "X05": "mrn", "C18": "mrn"},
            "rules": [], "salesmen": [dict(s) for s in (salesmen if salesmen is not None else SALESMEN)],
            "pairs": {}, "drops": {}, "campaigns": [], "loaded_at": "", "share_token": "tok-test-token-value"}


def _prev_order(id=1, token="prev-token-aaaaaaaaaaaa", device="dev-A", **over):
    o = {"id": id, "order_no": f"YQ-2609-{id:04d}", "token": token, "status": "delivered", "customer_name": "Sara Owner",
         "customer_phone": PHONE, "customer_shop": "Test Shop", "customer_area": "Riffa", "customer_email": None,
         "salesman_id": 1, "salesman_name": "Rep One", "source": "market", "device_id": device,
         "total_bhd": 29.5, "total_confirmed_bhd": 29.5, "items_count": 1, "units_count": 10,
         "created_at": (NOW - timedelta(days=9)).isoformat(), "updated_at": (NOW - timedelta(days=8)).isoformat(),
         "order_kind": "standard", "expected_delivery": None}
    o.update(over)
    return o


def _db(**tables) -> _FakeDB:
    base = {"salesmen": SALESMEN, "app_settings": [], "shop_customers": [], "shop_customer_phones": [],
            "shop_orders": [], "shop_order_lines": [], "shop_order_events": [], "shop_events": []}
    base.update(tables)
    events_allowed = base.pop("_events_allowed", None)
    return _FakeDB(base, events_allowed=events_allowed)


def _body(**over):
    b = {"lines": [{"item_code": "T02", "qty": 10}], "customer": {"name": "", "phone": ""},
         "device_id": "dev-A", "client_order_id": "coid-1"}
    b.update(over)
    return b


def _raises(fn, want: str):
    from app.shop import ShopError
    try:
        fn()
    except ShopError as e:
        assert want.lower() in str(e).lower(), (want, str(e))
        return e
    raise AssertionError(f"expected ShopError containing {want!r}")


def _client():
    from fastapi.testclient import TestClient
    import app.main as m
    return TestClient(m.app)


@contextmanager
def _quiet_notify():
    import app.shop_notify as sn
    sent: list = []
    with _swap(sn, notify_new_order=lambda *a, **k: sent.append(a) or {}):
        yield sent


def _line(lid, code, qty, unit, **over):
    ln = {"id": lid, "order_id": 9, "item_code": code, "display_name": f"{code} name", "qty": qty,
          "list_price_bhd": unit, "unit_price_bhd": unit, "discount_bhd": 0, "line_total_bhd": round(unit * qty, 3),
          "stock_status": "in_stock", "backorder": False, "qty_confirmed": qty, "line_status": "ok", "note": None,
          "unit_price_confirmed": unit, "line_total_confirmed": round(unit * qty, 3)}
    ln.update(over)
    return ln


def _tracked(notify_result=None, salesman=True, **over):
    lines = [_line(91, "T02", 10, 2.95, qty_confirmed=5, line_total_confirmed=14.75, line_status="changed",
                   change_reason="out_of_stock", note="only 5 on the shelf — internal"),
             _line(92, "X05", 4, 2.0, qty_confirmed=0, line_total_confirmed=0.0, line_status="unavailable",
                   change_reason="other", note="rep's own words"),
             _line(93, "C18", 6, 1.25)]
    o = {"id": 9, "order_no": "YQ-2609-0009", "token": "track-token-bbbbbbbbbb", "status": "confirmed",
         "customer_name": "Sara Owner", "customer_phone": PHONE, "customer_shop": "Test Shop", "customer_area": "Riffa",
         "customer_email": "owner@example.test", "salesman_id": 1 if salesman else None, "device_id": "dev-A",
         "ip_hash": "abc", "ua": "UA", "notify_result": notify_result, "created_at": NOW.isoformat(),
         "confirmed_at": NOW.isoformat(), "total_bhd": 45.0, "total_confirmed_bhd": 22.25, "subtotal_bhd": 45.0,
         "discount_bhd": 0, "delivery_bhd": 0, "expected_delivery": "Tomorrow", "expected_delivery_date": "2026-09-28",
         "order_kind": "standard", "lines": lines, "events": [],
         "salesman": SALESMEN[0] if salesman else None}
    o.update(over)
    return o


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the tracking payload: public reason keys, rep_alerted, nothing private
# ═══════════════════════════════════════════════════════════════════════════════

@test("tracking: each line carries the PUBLIC reason key ('other' and the rep's note never), the three numbers and the totals")
def _():
    from app import shop
    with _patched(_db()):
        v = shop.public_order_view(_tracked())
    by = {ln["item_code"]: ln for ln in v["lines"]}
    assert by["T02"]["reason_code"] == "out_of_stock" and by["T02"]["disposition"] == "reduced", by["T02"]
    assert by["T02"]["qty"] == 10 and by["T02"]["qty_confirmed"] == 5, by["T02"]
    assert by["X05"]["reason_code"] is None and by["X05"]["disposition"] == "unavailable", "'other' is never public"
    assert by["C18"]["reason_code"] is None and by["C18"]["disposition"] == "as_ordered"
    dump = json.dumps(v)
    for private in ("rep's own words", "only 5 on the shelf", "internal"):
        assert private not in dump, private
    assert v["total_bhd"] == 45.0 and v["total_confirmed_bhd"] == 22.25 and v["total_effective_bhd"] == 22.25
    assert v["expected_delivery_date"] == "2026-09-28" and v["visible_status"] == "confirmed"


@test("tracking: no phone, e-mail, device, notify result or fingerprint of the merchant reaches the page")
def _():
    from app import shop
    with _patched(_db()):
        v = shop.public_order_view(_tracked(notify_result={"email_rep": {"sent": True}, "recipients": ["alerts@example.test"],
                                                           "email_owner": {"sent": True, "to": "alerts@example.test"}}))
    dump = json.dumps(v)
    # (the rep's own WhatsApp / e-mail links are his public contact on this page, by design, and the
    # message they prefill carries this page's own link — the token the viewer already holds)
    for secret in (PHONE, "33001122", "owner@example.test", "dev-A", "alerts@example.test"):
        assert secret not in dump, secret
    for key in ("notify_result", "device_id", "ip_hash", "ua", "token", "customer_phone", "customer_email"):
        assert key not in v, key


@test("rep_alerted: True only when a channel that reaches the REP delivered; owner copy / Telegram are not the rep; None while pending")
def _():
    from app.shop import REP_CHANNELS, rep_alerted
    assert REP_CHANNELS == ("email_rep", "whatsapp")
    cases = [
        ({"email_rep": {"sent": True}}, True, True),
        ({"email_rep": {"sent": False, "reason": "bounced"}, "whatsapp": {"sent": True}}, True, True),
        ({"email_owner": {"sent": True}, "telegram": {"sent": True}}, True, False),
        ({"email_rep": {"sent": False}, "whatsapp": {"sent": False, "reason": "cloud_api_not_configured"}}, True, False),
        ({"error": "RuntimeError: boom", "attempts": ["x"]}, True, False),
        (None, True, None),
        (None, False, False),
    ]
    for nr, has_rep, want in cases:
        got = rep_alerted({"notify_result": nr, "salesman_id": 1 if has_rep else None})
        assert got is want, (nr, has_rep, got)
    from app import shop
    with _patched(_db()):
        assert shop.public_order_view(_tracked(notify_result={"whatsapp": {"sent": True}}))["rep_alerted"] is True
        assert shop.public_order_view(_tracked())["rep_alerted"] is None


@test("placed response: rep_alerted None for a new assigned order (the alert runs after it), False with no rep, stored on a re-submit")
def _():
    import app.shop as s
    import app.shop_notify as n
    base = {"id": 1, "order_no": "YQ-2609-0102", "token": "v" * 24, "status": "new", "status_url": "/o/" + "v" * 24,
            "attribution_source": "session_ref", "has_backorder": False, "totals": {"total_bhd": 29.5}}
    body = {"lines": [{"item_code": "T02", "qty": 1}], "customer": {"name": "Test Shop", "phone": ""},
            "device_id": "dev-test", "client_order_id": "coid-test", "reuse_token": "prev-token-aaaaaaaaaaaa"}
    seen: list = []

    def fake_create(b, **kw):
        seen.append(b)
        return dict(state["order"])
    state = {"order": {**base, "salesman": SALESMEN[0], "salesman_id": 1}}
    with _swap(s, create_order=fake_create, market_enabled=lambda: True), \
            _swap(n, notify_new_order=lambda *a, **k: {}, customer_to_salesman_wa_url=lambda o: None,
                  customer_to_salesman_email_url=lambda o: None):
        c = _client()
        r = c.post("/public/market/order", json=body)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["rep_alerted"] is None and r.json()["assigned"] is True, r.json()
        assert seen[-1]["reuse_token"] == "prev-token-aaaaaaaaaaaa", "the model passes reuse_token through"
        state["order"] = {**base, "salesman": None, "salesman_id": None, "attribution_source": "unassigned"}
        assert c.post("/public/market/order", json=body).json()["rep_alerted"] is False
        state["order"] = {**base, "salesman": SALESMEN[0], "salesman_id": 1, "duplicate": True,
                          "notify_result": {"email_rep": {"sent": True}}}
        assert c.post("/public/market/order", json=body).json()["rep_alerted"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# 2. "Same as last time": the phone reused on the server, never shown
# ═══════════════════════════════════════════════════════════════════════════════

@test("reuse: an empty phone + the device's own earlier order → that phone; empty name / shop / area filled, typed ones kept")
def _():
    from app import shop
    fake = _db(shop_orders=[_prev_order()])
    with _patched(fake):
        o = shop.create_order(_body(reuse_token="prev-token-aaaaaaaaaaaa"), market=True)
        o2 = shop.create_order(_body(reuse_token="prev-token-aaaaaaaaaaaa", client_order_id="coid-2",
                                     customer={"name": "Ali", "phone": "", "area": "Manama"}), market=True)
    row = next(r for r in fake.rows("shop_orders") if r["id"] == o["id"])
    assert row["customer_phone"] == PHONE and row["customer_name"] == "Sara Owner", row
    assert row["customer_shop"] == "Test Shop" and row["customer_area"] == "Riffa", row
    row2 = next(r for r in fake.rows("shop_orders") if r["id"] == o2["id"])
    assert row2["customer_phone"] == PHONE and row2["customer_name"] == "Ali" and row2["customer_area"] == "Manama"
    assert row2["customer_shop"] == "Test Shop", "an empty shop is filled from the last order"


@test("reuse: another device's order, an unknown or short token → refused with the plain message and NOTHING written")
def _():
    from app import shop
    for token, device in (("prev-token-aaaaaaaaaaaa", "dev-B"), ("no-such-token-cccccccc", "dev-A"), ("short", "dev-A"),
                          ("prev-token-aaaaaaaaaaaa", "")):
        fake = _db(shop_orders=[_prev_order()])
        with _patched(fake):
            _raises(lambda: shop.create_order(_body(reuse_token=token, device_id=device), market=True),
                    "Please enter your phone number")
        assert not fake.inserts("shop_orders") and not fake.inserts("shop_customers"), (token, device)
        assert not fake.inserts("shop_order_lines") and not [c for c in fake.calls if c[0] == "rpc"], "no order number spent"


@test("reuse: a typed phone wins; staff and the legacy token link never reuse; the daily phone cap still counts the reused phone")
def _():
    from app import shop
    fake = _db(shop_orders=[_prev_order(device="dev-B")])
    with _patched(fake):
        o = shop.create_order(_body(reuse_token="prev-token-aaaaaaaaaaaa",
                                    customer={"name": "Ali", "phone": "39001122"}), market=True)
    assert next(r for r in fake.rows("shop_orders") if r["id"] == o["id"])["customer_phone"] == "97339001122"
    fake = _db(shop_orders=[_prev_order()])
    with _patched(fake):
        _raises(lambda: shop.create_order(_body(reuse_token="prev-token-aaaaaaaaaaaa", customer={"name": "Ali", "phone": ""}),
                                          staff_email="rep@example.com"), "valid phone")
        _raises(lambda: shop.create_order(_body(reuse_token="prev-token-aaaaaaaaaaaa", customer={"name": "Ali", "phone": ""})),
                "valid phone")
    today = _prev_order(created_at=NOW.isoformat())
    fake = _db(shop_orders=[today])
    with _patched(fake, _ctx(shop_phone_daily_cap="1")):
        _raises(lambda: shop.create_order(_body(reuse_token="prev-token-aaaaaaaaaaaa"), market=True), "many orders today")


@test("reuse over the route: the response carries no phone; My orders carries name · shop · area and never the phone")
def _():
    from app import shop
    fake = _db(shop_orders=[_prev_order()])
    body = {**_body(reuse_token="prev-token-aaaaaaaaaaaa"), "session_ref": "rep-one"}
    with _patched(fake), _quiet_notify() as sent, _swap(shop, market_enabled=lambda: True):
        c = _client()
        r = c.post("/public/market/order", json=body)
        assert r.status_code == 200, r.text[:300]
        assert PHONE not in r.text and "33001122" not in r.text, "the reused phone never travels back"
        assert r.json()["rep_alerted"] is None and sent, "a new order: the alert is queued, its outcome not known yet"
        mine = c.post("/public/shop/my-orders", json={"tokens": ["prev-token-aaaaaaaaaaaa", r.json()["token"]]})
        assert mine.status_code == 200 and PHONE not in mine.text and "33001122" not in mine.text, mine.text[:300]
        rows = {o["token"]: o for o in mine.json()["orders"]}
        assert rows["prev-token-aaaaaaaaaaaa"]["customer"] == {"name": "Sara Owner", "shop": "Test Shop", "area": "Riffa"}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. area → rep for direct traffic (shop_area_reps)
# ═══════════════════════════════════════════════════════════════════════════════

@test("area reps: parsed case-blind; a bad stored value reads as empty; validation normalises, clears and refuses")
def _():
    from app.shop import ShopError, area_reps, validate_area_reps
    assert area_reps({"shop_area_reps": '{" Riffa ": 2, "Muharraq": "1", "Sitra": 0, "": 3}'}) == {"riffa": 2, "muharraq": 1}
    for bad in ("not json", "[1, 2]", "", None, "42"):
        assert area_reps({"shop_area_reps": bad}) == {}, bad
    roster = [{"id": 1}, {"id": 2}]
    assert json.loads(validate_area_reps('{" Riffa ": "2", "Manama": 1, "Sitra": ""}', roster)) == {"Riffa": 2, "Manama": 1}
    assert validate_area_reps("", roster) == "{}" and validate_area_reps("{}", roster) == "{}"
    assert json.loads(validate_area_reps({"Riffa": 9}, None)) == {"Riffa": 9}, "no roster to check against: ids pass"
    for bad, want in (('{"Riffa": 9}', "not on the roster"), ("[1]", "JSON object"), ("nope", "must be JSON"),
                      ('{"Riffa": -1}', "pick a salesman"), ('{"Riffa": true}', "pick a salesman"),
                      ('{"Riffa": 1, "riffa": 2}', "appears twice"), (json.dumps({"x" * 61: 1}), "characters")):
        try:
            validate_area_reps(bad, roster)
        except ShopError as e:
            assert want in str(e), (bad, str(e))
        else:
            raise AssertionError(f"accepted {bad!r}")


@test("area reps: after the session link and the checkout pick, before the default; a recorded rep and an inactive rep")
def _():
    from app.shop import resolve_salesman
    ctx = _ctx(shop_area_reps='{"Riffa": 2}', shop_default_salesman="Rep One")
    sm, how, _c = resolve_salesman(ctx, phone=None, session_ref=None, area="riffa ")
    assert sm["id"] == 2 and how == "area", (sm, how)
    sm, how, _c = resolve_salesman(ctx, phone=None, session_ref="rep-one", area="Riffa")
    assert sm["id"] == 1 and how == "session_ref", "a rep link beats the area"
    # a link from a rep with NO public profile (no rep card on the page) still beats the area — so the
    # checkout must not name the area's rep while any ref is set (web/src/market/lib/areaRep.ts)
    sm, how, _c = resolve_salesman(ctx, phone=None, session_ref="quiet", area="Riffa")
    assert sm["id"] == 3 and how == "session_ref", "a hidden-profile rep link beats the area"
    sm, how, _c = resolve_salesman(ctx, phone=None, session_ref=None, pick_id=1, area="Riffa")
    assert sm["id"] == 1 and how == "checkout_pick", "the merchant's own pick beats the area"
    sm, how, _c = resolve_salesman(ctx, phone=None, session_ref=None, area="Manama")
    assert sm["id"] == 1 and how == "default", "an unmapped area falls to the default"
    cust = {"id": 5, "phone": PHONE, "salesman_id": 1}
    sm, how, _c = resolve_salesman(ctx, phone=PHONE, session_ref=None, customer=cust, area="Riffa")
    assert sm["id"] == 1 and how == "customer_admin", "the shop's recorded rep wins"
    gone = _ctx(salesmen=[s for s in SALESMEN if s["id"] != 2], shop_area_reps='{"Riffa": 2}')
    sm, how, _c = resolve_salesman(gone, phone=None, session_ref=None, area="Riffa")
    assert sm is None and how == "unassigned", "an inactive area rep is skipped"
    sm, how, _c = resolve_salesman(_ctx(), phone=None, session_ref=None, area="Riffa")
    assert sm is None and how == "unassigned", "no map: exactly as before"


@test("area reps: the order stores attribution_source 'default' (the live CHECK), its events say 'area', the shop's first_ref")
def _():
    from app import shop
    fake = _db()
    with _patched(fake, _ctx(shop_area_reps='{"Riffa": 2}')):
        o = shop.create_order(_body(customer={"name": "New Shop", "phone": "39001122", "area": "Riffa"}), market=True)
    row = next(r for r in fake.rows("shop_orders") if r["id"] == o["id"])
    assert row["salesman_id"] == 2 and row["attribution_source"] == "default", row
    created = next(e for e in fake.rows("shop_order_events") if e["event"] == "created")
    assert created["detail"]["attribution"] == "area", created
    ev = next(e for e in fake.rows("shop_events") if e["event"] == "order")
    assert ev["meta"]["attribution"] == "area" and ev["salesman_id"] == 2, ev
    cust = fake.rows("shop_customers")[0]
    assert cust["first_ref"] == "rep-two" and cust.get("sticky_salesman_id") is None, cust


@test("area reps: the public checkout map is {area: first name} for public reps only — no ids, no phones")
def _():
    from app import shop
    ctx = _ctx(shop_area_reps='{"Riffa": 2, "Sitra": 3, "Hidd": 99}')
    with _patched(_db(), ctx):
        p = shop.catalog_payload("tok-test-token-value")
    assert p["settings"]["area_reps"] == {"riffa": "Rep"}, p["settings"]["area_reps"]
    dump = json.dumps(p["settings"])
    assert "97300000002" not in dump and '"id"' not in dump


@test("area reps: saved through the settings writer — normalised JSON, an unknown salesman refused before any write")
def _():
    from app import shop
    fake = _db()
    with _patched(fake):
        vals = shop.update_shop_settings({"shop_area_reps": '{" Riffa ": "2"}'}, by="admin@example.com")
        assert json.loads(vals["shop_area_reps"]) == {"Riffa": 2}, vals["shop_area_reps"]
        stored = next(r for r in fake.rows("app_settings") if r["key"] == "shop_area_reps")
        assert json.loads(stored["value"]) == {"Riffa": 2}
        n = len(fake.inserts("app_settings"))
        _raises(lambda: shop.update_shop_settings({"shop_area_reps": '{"Riffa": 77}', "shop_order_prefix": "ZZ"}),
                "not on the roster")
        assert len(fake.inserts("app_settings")) == n, "nothing half-saved"
    assert "shop_area_reps" in shop.SETTING_DEFAULTS and shop.SETTING_DEFAULTS["shop_area_reps"] == "{}", "seeded empty"
    st = _read("web/src/pages/Settings.tsx")
    assert "function AreaRepsCard()" in st and "{ settings: { shop_area_reps: json } }" in st
    assert "{me?.role === 'admin' && <AreaRepsCard />}" in st


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 'product_request' — the zero-result search's demand signal
# ═══════════════════════════════════════════════════════════════════════════════

@test("product_request: an event kind with meta.q only; the rep comes from the referral code, never from the body")
def _():
    from app import shop
    assert "product_request" in shop.EVENTS and shop.PRODUCT_REQUEST == "product_request"
    fake = _db()
    with _patched(fake):
        ok = shop.record_event({"event": "product_request", "session_id": "s1", "referral_code": "rep-two",
                                "salesman_id": 1, "meta": {"q": "memory card 64gb", "phone": "33001122", "note": "x"}})
    assert ok
    ev = fake.rows("shop_events")[0]
    assert ev["event"] == "product_request" and ev["meta"] == {"q": "memory card 64gb"}, ev
    assert ev["salesman_id"] == 2, "attribution is derived from the referral code"


@test("product_request before the migration: the CHECK refuses it → the same request as 'search_zero' with where='product_request'")
def _():
    from app import shop
    fake = _db(_events_allowed=LIVE_EVENTS)
    with _patched(fake):
        assert shop.record_event({"event": "product_request", "session_id": "s1", "meta": {"q": "sandisk"}}) is True
        assert shop.record_event({"event": "search_zero", "session_id": "s2", "meta": {"q": "sd card"}}) is True
    rows = fake.rows("shop_events")
    assert [r["event"] for r in rows] == ["search_zero", "search_zero"], rows
    assert rows[0]["meta"] == {"q": "sandisk", "where": "product_request"} and rows[1]["meta"] == {"q": "sd card"}
    # any other refused event is NOT rewritten into something else
    narrow = _db(_events_allowed=("order",))
    with _patched(narrow):
        assert shop.record_event({"event": "view", "session_id": "s3"}) is False
    assert narrow.rows("shop_events") == [] and len(narrow.inserts("shop_events")) == 1


@test("product_request over the public event route (the 16-character event field fits it)")
def _():
    from app import shop
    fake = _db()
    with _patched(fake), _swap(shop, market_enabled=lambda: True):
        r = _client().post("/public/market/event", json={"event": "product_request", "session_id": "s9",
                                                         "meta": {"q": "tripod"}})
    assert r.status_code == 200 and r.json() == {"ok": True}, r.text[:200]
    assert fake.rows("shop_events")[0]["event"] == "product_request"


@test("migration: the live 20 values + 'product_request' == app.shop.EVENTS; the reverse narrows NOT VALID and deletes nothing")
def _():
    from app.shop import EVENTS

    def values(sql: str) -> list[str]:
        m = re.search(r"check \(event in \((.*?)\)\)", sql, re.S)
        assert m, "no CHECK list"
        return re.findall(r"'([a-z_]+)'", m.group(1))
    mig, rev = _read(MIGRATION), _read(REVERSE)
    assert values(mig) == list(EVENTS) and values(mig)[:20] == list(LIVE_EVENTS), values(mig)
    assert values(rev) == list(LIVE_EVENTS), values(rev)
    assert "not valid;" in rev and "delete from" not in rev.lower() and "update shop_events" not in rev.lower()
    for sql in (mig, rev):
        assert not re.search(r"^\s*(commit|end)\s*;", sql, re.I | re.M), "no COMMIT: --rehearse must roll it back"
        assert "set lock_timeout = '2s';" in sql and "raise exception" in sql
        assert "drop constraint if exists shop_events_event_check" in sql
        assert "cascade" not in sql.lower() and "security_invoker" not in sql.lower()
    assert "grantee in ('anon', 'authenticated')" in mig, "the self-check keeps shop_events closed to the public keys"
    assert "r7d_product_request_reverse.sql" in mig
    doc = _read("docs/MIGRATIONS.md")
    assert "r7d_product_request_migration.sql" in doc and "shop_area_reps" in doc


# ═══════════════════════════════════════════════════════════════════════════════
# 5. wording: "Sold Out", and clearing lines instead of "Last chance"
# ═══════════════════════════════════════════════════════════════════════════════

@test("Sold Out: the API's reasons, the share preview and the market labels all say 'Sold Out' (owner, 27-Sep)")
def _():
    from app.shop import SOLD_OUT_REASON, SOLD_OUT_SHORT, sold_out_reason
    assert SOLD_OUT_REASON.startswith("Sold Out — ") and SOLD_OUT_SHORT == "Sold Out"
    assert sold_out_reason({"fresh": False, "label": "21 Sep"}).startswith("Sold Out as of 21 Sep — ")
    assert '"out_of_stock": "Sold Out"' in _read("app/shop_api.py")
    en = _read("web/src/market/i18n/en.ts")
    for key in ("soldOut", "stockOut"):
        assert f"{key}: 'Sold Out'," in en, key
    assert "`Sold Out · stock as of ${d}`" in en and "backorderNote: 'Sold Out —" in en
    assert not re.search(r"['`]Sold out", en), "a 'Sold out' label is left in en.ts"
    ar = _read("web/src/market/i18n/ar.ts")
    assert "soldOut: 'نفدت الكمية'," in ar and "stockOut: 'نفدت الكمية'," in ar


@test("clearing lines: no 'Last chance' / «فرصة أخيرة» in either locale; «تصفية» in Arabic; the harness bans both; no sticker")
def _():
    en, ar = _read("web/src/market/i18n/en.ts"), _read("web/src/market/i18n/ar.ts")
    code_en = "\n".join(ln for ln in en.splitlines() if not ln.strip().startswith(("*", "/*", "//", "/**")))
    assert not re.search(r"last.chance", code_en, re.I), "a 'Last chance' string is left in en.ts"
    assert "lastChance: 'Clearing lines · trade price'," in en and "badge: 'Clearing line'," in en
    assert "clearance: 'Clearing lines'," in en
    assert "فرصة أخيرة" not in ar and "الفرصة الأخيرة" not in ar and "badge: 'تصفية'," in ar
    for f in list(MARKET.rglob("*.ts")) + list(MARKET.rglob("*.tsx")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if re.search(r"last.chance", s, re.I) and not s.startswith(("//", "*", "/*", "{/*")) and "/**" not in s:
                raise AssertionError(f"{f.relative_to(ROOT)}:{n} renders 'last chance': {s[:100]}")
    qa = _read("scripts/qa/market_qa.py")
    banned = re.search(r'^BANNED = r"(.+)"$', qa, re.M).group(1)
    assert re.search(banned, "Last chance", re.I) and re.search(banned, "LAST-CHANCE STOCK", re.I)
    assert not re.search(banned, "Clearing lines · trade price", re.I) and not re.search(banned, "Sold Out", re.I)
    banned_ar = re.search(r'^BANNED_AR = r"(.+)"$', qa, re.M).group(1)
    assert re.search(banned_ar, "بضاعة الفرصة الأخيرة") and not re.search(banned_ar, "أصناف التصفية")
    slides = _read("web/src/market/lib/slides.ts")
    assert "id: 'd:last'" in slides and "art(deals.lastChance, true), sticker: null" in slides
    assert "products: deals.lastChance, sticker: null" in slides
    campaigns = _read("web/src/pages/shop-ops/CampaignsSection.tsx")
    assert "Last chance" not in campaigns and "Last-chance" not in campaigns and "last-chance" not in campaigns


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the pages (source checks)
# ═══════════════════════════════════════════════════════════════════════════════

@test("placed screen: 'Sent to {rep}' only on rep_alerted, else 'Received by YQ — {rep} will confirm'; one WhatsApp + Track")
def _():
    src = _read("web/src/market/pages/TrackingPage.tsx")
    assert "const repAlerted = data?.rep_alerted ?? placed?.rep_alerted ?? null" in src
    assert "repAlerted ? S.placed.sentTo(first) : S.placed.receivedBy(first)" in src
    assert src.count("S.placed.sentTo(") == 1, "no other path says Sent"
    hero = src.split("{placed && (", 1)[1].split("</section>", 1)[0]
    assert hero.count("<AnchorButton") == 1 and "variant=\"wa\"" in hero, "one primary action: WhatsApp the rep"
    assert hero.count("<Button") == 1 and "{S.placed.track}" in hero and "onClick={toTracking}" in hero
    for gone in ("S.placed.copy", "S.placed.install", "S.placed.continue", "S.placed.email", "promptInstall"):
        assert gone not in hero, gone
    assert "ALERT_RECHECK_MS" in src and "!placed.assigned || placed.duplicate || alertKnown" in src
    # the pinned bidi / status wiring stays
    for keep in ("<Ltr>{placed.order_no}</Ltr>", "<Ltr>{data.order_no}</Ltr>", "statusLabel(data.status, data.status_label)",
                 "statusLabel(s.status, s.label)", "m.addMany(entries, 'reorder')"):
        assert keep in src, keep


@test("tracking page: 'What changed' first, the dated ETA, adopting the link into My orders with a toast")
def _():
    src = _read("web/src/market/pages/TrackingPage.tsx")
    assert "{!placed && data && <WhatChanged data={data} className=\"mb-4\" />}" in src
    assert "const eta = fmtDay(data?.expected_delivery_date) || data?.expected_delivery || null" in src
    assert "adoptOrder({ token, order_no: data.order_no" in src and "toast(S.track.adopted, 'success')" in src
    assert "if (data.cancelled) return" in src.split("adopted.current = true", 1)[1][:200]
    wc = _read("web/src/market/components/WhatChanged.tsx")
    assert "orderChanges(data)" in wc and "changeText(S.track, r)" in wc and "S.track.reasons[r.reason]" in wc
    oc = _read("web/src/market/lib/orderChanges.ts")
    assert "if (!d || d.cancelled || d.status === 'cancelled') return empty" in oc
    dev = _read("web/src/market/lib/device.ts")
    assert "export function adoptOrder(" in dev and "rememberedOrders().find((o) => !o.adopted)" in dev
    fmt = _read("web/src/market/lib/format.ts")
    assert "const VISIBLE_STATUS: Record<string, string> = { packed: 'confirmed', out_for_delivery: 'confirmed' }" in fmt
    assert "if (locale.lang === 'en') return apiLabel || S.status[key] || key" in fmt
    assert "return S.status[VISIBLE_STATUS[key] || key] || apiLabel || key" in fmt


@test("checkout: 'Same as last time' sends reuse_token and an empty phone; the area representative line; the pinned wiring")
def _():
    src = _read("web/src/market/pages/CheckoutPage.tsx")
    assert "const [lastMine] = useState(() => lastPlacedOrder())" in src
    assert "phone: reuse ? '' : cleanPhone(customer.phone)" in src and "reuse_token: reuse || undefined," in src
    assert "{S.checkout.sameAsLast}" in src and "{S.checkout.samePhone}" in src
    assert "S.checkout.areaRep(areaRepName)" in src
    # the area line only when the area is what resolve_salesman routes by: never with a rep link
    # (m.ref - a hidden-profile rep has no card but still wins), a pick, or a merchant this device knows
    assert "areaRepFor({ hasRepCard: Boolean(rep), ref: m.ref, pick, recognized, known: Boolean(known), reuse: Boolean(reuse)," in src
    arl = _read("web/src/market/lib/areaRep.ts")
    assert "i.hasRepCard || (i.ref || '').trim() || i.pick !== '' || i.recognized || i.known || i.reuse" in arl
    assert "Your area representative" not in _read("web/src/market/i18n/en.ts"), "never promise 'your' rep from the area"
    assert 'id="yq-phone" type="tel" inputMode="tel" autoComplete="tel" dir="ltr"' in src
    assert "{areaLabel(a)}" in src and "set('area', a)" in src and "s.referral_code" not in src
    api = _read("web/src/lib/shopApi.ts")
    for want in ("| 'product_request'", "reuse_token?: string", "rep_alerted?: boolean | null", "area_reps?: Record<string, string> | null",
                 "expected_delivery_date?: string | null", "reason_code?: string | null"):
        assert want in api, want


@test("search: zero results say 'We don't stock “q” yet', offer ONE tell-the-rep action and log a product_request once per query")
def _():
    src = _read("web/src/market/components/SearchResults.tsx")
    assert "track('product_request', { meta: { q: q.trim().slice(0, 60) } })" in src
    assert "if (key.length < 2 || requested.has(key)) return" in src
    assert "onClick={() => logRequest(query)}" in src and "S.shop.askHave(first, query)" in src
    assert "{S.shop.notOnShelf(query)}" in src and "S.shop.noted(query)" in src
    # with no public WhatsApp the button only records a product_request for YQ: it never names the rep
    assert "S.card.tellRep(" not in src, "'Tell {rep}' would promise a message nobody sends"
    assert "{S.shop.tellNeed(askUrl ? first : '')}" in src and "{S.shop.tellYq}" in src
    en = _read("web/src/market/i18n/en.ts")
    assert "notOnShelf: (q: string) => `We don’t stock “${q}” yet`," in en


# ═══════════════════════════════════════════════════════════════════════════════
# 7. the node half
# ═══════════════════════════════════════════════════════════════════════════════

@test("node: What changed, the ETA day, adopting a link, the words (web/scripts/merchant_test.mjs)")
def _():
    node = shutil.which("node")
    script = ROOT / "web" / "scripts" / "merchant_test.mjs"
    assert script.exists()
    if not node or not (ROOT / "web" / "node_modules" / "typescript").exists():
        print("        SKIP: node or web/node_modules not available (the web job runs it after npm ci)")
        return
    r = subprocess.run([node, str(script)], cwd=str(ROOT / "web"), capture_output=True, text=True, encoding="utf-8",
                       timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "node merchant tests failed:\n" + out[-2000:]
    assert re.search(r"\b\d+ passed, 0 failed", out), out[-400:]


# ── runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
