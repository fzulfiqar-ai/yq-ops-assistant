"""R7a stream B1 — reliability: the crashes and the "Failed to fetch" errors.

    python -m tests.test_r7a_reliability

Same lightweight runner as tests/test_r3_pipeline.py (no pytest), and the same rule: nothing here
reaches a database or the network. The shop reads run against a small in-memory PostgREST
stand-in, the SQL reads against a stub of exec_sql, the error middleware through FastAPI's
TestClient on the real app (with throwaway routes added for the test and removed after). No .env
is read; SUPABASE_URL only has to exist for app.main to import (ci.yml does the same).

Covered:
  * /shop/analytics and /shop/me/link-week: the vitals counter no longer overwrites the order
    attribution `by_src` (every call was a TypeError 500) — admin scope, a rep's scope, and the
    link-week card on the same events; the payload keeps every key the portal reads;
  * the JSON 500 {"detail": "Server error", "ref"} answered INSIDE CORS (allow-origin present),
    logged under the same ref; an HTTPException and a streaming body pass through untouched; a
    stream that fails after it started is not rewritten; the middleware order itself;
  * retry-once on a dropped Supabase connection: exec_sql / exec_sql_params, the order list,
    the status counts and the merchant's order-status read — once, WITHOUT a client reset (perf-2609:
    the HTTP/1.1 pool drops a broken connection itself), and never for an ordinary error or a
    timeout; reset_client() really drops the cached client;
  * the Inventory page: no SQL reads the ungranted base table stock_movements, the arrivals come
    from the granted `shipments` view, and a failing arrivals card no longer fails the page;
  * the web side, from the sources: ApiError carries the ref, the shared error line, the report
    pages' error panel, and the link-week card's retry state.
"""
from __future__ import annotations

import functools
import io
import json
import logging
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# app.main only needs the URL shape at import (the JWKS client); nothing is fetched
os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import httpx  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class _Patched:
    """Temporarily set module attributes; restores on exit."""

    def __init__(self, *triples):
        self.triples = triples
        self.saved = []

    def __enter__(self):
        for mod, name, value in self.triples:
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
        return self

    def __exit__(self, *exc):
        for mod, name, old in reversed(self.saved):
            setattr(mod, name, old)
        return False


# ── a small in-memory PostgREST stand-in (tests/test_r3_pipeline.py, reads only) ─

def _same(a, b) -> bool:
    return a == b or str(a) == str(b)


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.cols, self.count, self.filters, self.lim, self.desc = "*", None, [], None, False

    def select(self, cols="*", count=None, head=False):
        self.cols, self.count = cols, count
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
        # like / or_ / ilike: accepted, not applied (these tests never depend on them)
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: self

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
        self.db.calls.append(self)
        exc = self.db.fault_for(self)
        if exc is not None:
            raise exc
        rows = [dict(r) for r in self.db.tables.get(self.table, []) if self._match(r)]
        if self.desc:
            rows.reverse()
        total = len(rows)
        if self.lim:
            rows = rows[: self.lim]
        return SimpleNamespace(data=rows, count=total if self.count else None)


class _FakeDB:
    def __init__(self, **tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.calls: list[_Query] = []
        self.faults: list[tuple] = []          # (predicate(query) -> bool, exception), each used once

    def table(self, name):
        return _Query(self, name)

    def fail_once(self, pred, exc) -> None:
        self.faults.append((pred, exc))

    def fault_for(self, q: _Query):
        for i, (pred, exc) in enumerate(self.faults):
            if pred(q):
                self.faults.pop(i)
                return exc
        return None

    def selects(self, table: str, pred=lambda q: True) -> list[_Query]:
        return [q for q in self.calls if q.table == table and pred(q)]


class _patched_shop:
    """Point app.shop and app.database at the fake and reset the caches the reads consult;
    count reset_client() calls (retry_read must no longer make any)."""

    def __init__(self, fake: _FakeDB):
        self.fake = fake
        self.resets = 0

    def __enter__(self):
        import app.database as db
        import app.shop as s

        def _reset():
            self.resets += 1
        self.mods = (s, db)
        self.saved = (s.get_client, db.get_client, db.reset_client)
        s.get_client = db.get_client = lambda: self.fake
        db.reset_client = _reset
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        return self

    def __exit__(self, *_exc):
        s, db = self.mods
        s.get_client, db.get_client, db.reset_client = self.saved
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        return False


def _dropped() -> httpx.RemoteProtocolError:
    """What httpx raises when Supabase closed the HTTP/2 connection under a request."""
    return httpx.RemoteProtocolError("<ConnectionTerminated error_code:0, last_stream_id:1, additional_data:None>")


# ── synthetic fixtures (no real shop, rep, phone or amount) ─────────────────────

NOW = datetime.now(timezone.utc)
REP = {"id": 1, "name": "Rep One", "referral_code": "repone", "is_active": True}
OTHER = {"id": 2, "name": "Rep Two", "referral_code": "reptwo", "is_active": True}


def _ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def _order(oid, **over):
    o = {"id": oid, "order_no": f"YQ-TEST-{oid:04d}", "token": f"tok{oid}".ljust(24, "x"), "status": "new",
         "total_bhd": 10.5, "units_count": 4, "items_count": 2, "salesman_id": 1, "salesman_name": "Rep One",
         "referral_code": "repone", "src": None, "coupon_code": None, "customer_phone": f"9730000{oid:04d}",
         "customer_name": "Test Shop", "customer_shop": "Test Shop", "customer_area": "Test Area",
         "created_at": _ago(hours=oid), "updated_at": None, "has_backorder": False, "source": "market",
         "attribution_source": "session_ref", "attribution_conflict": False, "assigned_at": None,
         "confirmed_at": None, "cancelled_by": None, "customer_id": None, "device_id": f"dev-{oid}",
         "is_test": False, "notify_result": None, "total_confirmed_bhd": None, "order_kind": "standard",
         "minimum_gap_bhd": None, "expected_delivery": None, "payment_status": "unpaid",
         "focus_invoice_no": None, "cancel_reason": None, "placed_by": None}
    o.update(over)
    return o


def _event(event, session, ref="repone", sid=None, **meta):
    return {"ts": _ago(hours=1), "event": event, "session_id": session, "item_code": meta.pop("item_code", None),
            "referral_code": ref, "salesman_id": sid, "src": None, "device_id": f"dev-{session}", "meta": meta}


def _analytics_db() -> _FakeDB:
    orders = [
        _order(1, src="wa", total_bhd=10.5),
        _order(2, src=None, total_bhd=4.25, status="confirmed", confirmed_at=_ago(minutes=30)),
        _order(3, src="wa", status="cancelled", cancelled_by="customer"),
        _order(4, salesman_id=2, salesman_name="Rep Two", referral_code="reptwo", src="qr", total_bhd=7.0),
    ]
    lines = [
        {"order_id": 1, "item_code": "T01", "display_name": "Test cable", "qty": 3, "line_total_bhd": 7.5},
        {"order_id": 1, "item_code": "T02", "display_name": "Test charger", "qty": 1, "line_total_bhd": 3.0},
        {"order_id": 2, "item_code": "T01", "display_name": "Test cable", "qty": 2, "line_total_bhd": 4.25},
        {"order_id": 4, "item_code": "T03", "display_name": "Test case", "qty": 5, "line_total_bhd": 7.0},
    ]
    events = [
        _event("view", "s1"), _event("view", "s2"), _event("view", "s3", ref="reptwo"),
        _event("item", "s1", item_code="T01"), _event("add", "s1", item_code="T01"),
        _event("checkout_start", "s1"), _event("checkout", "s1"), _event("order", "s1"),
        _event("search", "s1", q="Type C"), _event("search_zero", "s2", q="unknown thing"),
        _event("rail_click", "s1", rail="best", item_code="T01"), _event("share", "s2"),
        # three beacons: two with metrics (edge catalog), one abandoned visit (no catalog)
        _event("vitals", "s1", lcp=1800, inp=120, cls=0.02, catalog_src="edge", catalog_ms=300),
        _event("vitals", "s2", lcp=2600, inp=90, cls=0.1, catalog_src="edge", catalog_ms=500),
        _event("vitals", "s3", ref="reptwo", catalog_src="none"),
    ]
    return _FakeDB(shop_events=events, shop_orders=orders, shop_order_lines=lines, app_settings=[],
                   salesmen=[REP, OTHER])


def _assert_analytics_shape(a: dict) -> None:
    for key in ("search", "rails", "engagement", "ops", "identity", "vitals", "days", "since", "funnel",
                "orders", "cancelled", "value_bhd", "aov_bhd", "units", "customers", "backorder_rate_pct",
                "top_products", "leaderboard", "attribution", "daily"):
        assert key in a, f"payload lost {key!r}"
    assert set(a["attribution"]) == {"by_referral", "by_src", "by_coupon"}, a["attribution"]
    for row in a["attribution"]["by_src"]:
        assert set(row) == {"key", "orders", "value_bhd"}, row          # ShopAnalytics.tsx AttributionRow
    for row in a["vitals"]["catalog_src"]:
        assert set(row) == {"src", "visits"}, row
    json.dumps(a)                                                         # the route returns it as JSON


# ── 1. analytics / link-week ──────────────────────────────────────────────────

@test("analytics: admin scope — by_src is the order attribution again, the vitals keep their own counter")
def _():
    from app import shop
    with _patched_shop(_analytics_db()):
        a = shop.analytics(30)
    _assert_analytics_shape(a)
    by_src = {r["key"]: r for r in a["attribution"]["by_src"]}
    assert set(by_src) == {"wa", "direct", "qr"}, by_src                # the cancelled order is not counted
    assert by_src["wa"]["orders"] == 1 and by_src["wa"]["value_bhd"] == 10.5
    assert by_src["direct"]["value_bhd"] == 4.25 and by_src["qr"]["value_bhd"] == 7.0
    assert a["vitals"]["catalog_src"] == [{"src": "edge", "visits": 2}, {"src": "none", "visits": 1}], a["vitals"]
    assert a["vitals"]["samples"] == 2 and a["vitals"]["visits"] == 3
    assert a["vitals"]["lcp_ms_p75"] == 2600
    assert a["orders"] == 3 and a["cancelled"] == 1 and a["value_bhd"] == 21.75
    assert a["search"]["searches"] == 2 and a["search"]["zero_results"] == 1
    assert a["rails"][0]["rail"] == "best" and a["rails"][0]["clicks"] == 1
    assert a["engagement"]["checkout_start"] == 1 and a["engagement"]["share"] == 1


@test("analytics: the median time to confirm leaves out orders a rep placed himself (born Confirmed, R7b review)")
def _():
    from app import shop
    db = _analytics_db()
    with _patched_shop(db):
        a0 = shop.analytics(30)
    assert a0["ops"]["median_time_to_confirm_min"] == 90.0, a0["ops"]      # order 2: created 2 h ago, confirmed 30 min ago
    db2 = _analytics_db()
    staff = [_order(5, source="salesman", status="confirmed", created_at=_ago(hours=1), confirmed_at=_ago(hours=1)),
             _order(6, source="salesman", status="confirmed", created_at=_ago(minutes=40), confirmed_at=_ago(minutes=40))]
    db2.tables["shop_orders"] = db2.tables["shop_orders"] + staff
    with _patched_shop(db2):
        a1 = shop.analytics(30)
    assert a1["ops"]["median_time_to_confirm_min"] == 90.0, "with them the median read 0 min"
    assert a1["identity"]["staff_orders"] == 2 and a1["orders"] == a0["orders"] + 2, "still counted as orders"


@test("analytics: a rep's scope — his orders and his link's events only, same shape")
def _():
    from app import shop
    with _patched_shop(_analytics_db()):
        a = shop.analytics(30, salesman=REP)
    _assert_analytics_shape(a)
    assert a["orders"] == 2 and a["value_bhd"] == 14.75, (a["orders"], a["value_bhd"])
    assert {r["key"] for r in a["attribution"]["by_src"]} == {"wa", "direct"}
    assert a["vitals"]["catalog_src"] == [{"src": "edge", "visits": 2}], a["vitals"]
    assert a["funnel"]["sessions"] == 2 and a["leaderboard"][0]["salesman"] == "Rep One"


@test("link-week: the rep's card builds on the same events (it called the crashing analytics)")
def _():
    from app import followups
    with _patched_shop(_analytics_db()):
        out = followups.link_week(REP, 7, force=True)
    for key in ("days", "since", "sessions", "item_views", "adds", "checkouts", "orders", "cancelled",
                "value_bhd", "aov_bhd", "customers", "conversion_pct", "shares", "top_products"):
        assert key in out, key
    assert out["days"] == 7 and out["sessions"] == 2 and out["orders"] == 2 and out["cancelled"] == 1
    assert out["value_bhd"] == "14.750" and out["shares"] == 1, out
    assert out["top_products"][0]["item_code"] == "T01" and out["top_products"][0]["value_bhd"] == "11.750"
    json.dumps(out)


@test("analytics: no name in analytics() is bound to two different shapes (the b6040b9 bug class)")
def _():
    import ast
    src = _read("app/shop.py")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "analytics")
    annotated: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            annotated.setdefault(node.target.id, set()).add(ast.unparse(node.annotation))
    clash = {k: v for k, v in annotated.items() if len(v) > 1}
    assert not clash, f"re-annotated with a different type: {clash}"
    assert "vitals_by_src" in annotated


# ── 2. the JSON 500 inside CORS ───────────────────────────────────────────────

ORIGIN = "http://localhost:5173"          # one of app.main._DEV_ORIGINS


class _routes:
    """Add throwaway routes to the real app for one test; remove them after."""

    def __init__(self, app, *specs):
        self.app, self.specs = app, specs

    def __enter__(self):
        self.before = list(self.app.router.routes)
        for path, fn in self.specs:
            self.app.add_api_route(path, fn, methods=["GET"])
        return self

    def __exit__(self, *_exc):
        self.app.router.routes[:] = self.before
        return False


class _LogCatcher(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@test("errors: an unhandled exception is JSON 500 {detail, ref} WITH the allow-origin header, logged under the ref")
def _():
    from fastapi.testclient import TestClient
    import app.main as m

    def boom():
        raise RuntimeError("synthetic failure")

    catcher = _LogCatcher()
    lg = logging.getLogger("app.errors")
    lg.addHandler(catcher)
    try:
        with _routes(m.app, ("/__r7a/boom", boom)):
            r = TestClient(m.app).get("/__r7a/boom", headers={"Origin": ORIGIN})
    finally:
        lg.removeHandler(catcher)
    assert r.status_code == 500, r.status_code
    assert r.headers.get("access-control-allow-origin") == ORIGIN, dict(r.headers)
    body = r.json()
    assert body["detail"] == "Server error" and set(body) == {"detail", "ref"}, body
    assert re.fullmatch(r"[0-9a-f]{6}", body["ref"]), body["ref"]
    assert "synthetic failure" not in r.text, "the exception text never reaches the browser"
    msgs = [rec.getMessage() for rec in catcher.records]
    assert any(body["ref"] in s and "/__r7a/boom" in s for s in msgs), msgs
    assert any(rec.exc_info for rec in catcher.records), "the traceback is logged"


@test("errors: an HTTPException is not swallowed; a streaming body streams; a stream that fails late is not rewritten")
def _():
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse
    from fastapi.testclient import TestClient
    import app.main as m

    def teapot():
        raise HTTPException(status_code=409, detail="Someone else moved this order first.")

    def stream():
        return StreamingResponse(iter([b"one ", b"two ", b"three"]), media_type="text/plain")

    def late():
        def gen():
            yield b"partial "
            raise RuntimeError("failed after the headers went out")
        return StreamingResponse(gen(), media_type="text/plain")

    with _routes(m.app, ("/__r7a/http", teapot), ("/__r7a/stream", stream), ("/__r7a/late", late)):
        c = TestClient(m.app)
        r = c.get("/__r7a/http", headers={"Origin": ORIGIN})
        assert r.status_code == 409 and r.json() == {"detail": "Someone else moved this order first."}, r.text
        assert r.headers.get("access-control-allow-origin") == ORIGIN
        r = c.get("/__r7a/stream", headers={"Origin": ORIGIN})
        assert r.status_code == 200 and r.text == "one two three", r.text
        assert r.headers.get("access-control-allow-origin") == ORIGIN
        try:
            c.get("/__r7a/late")
            raise AssertionError("a stream that failed after it started must not be turned into a new response")
        except RuntimeError as e:
            assert "after the headers" in str(e)
    # normal routes still answer as before
    r = TestClient(m.app).get("/health", headers={"Origin": ORIGIN})
    assert r.status_code == 200 and r.json()["status"] == "ok"


@test("errors: the middleware sits inside CORS (added before it; the last added is outermost)")
def _():
    from fastapi.middleware.cors import CORSMiddleware
    import app.main as m
    order = [mw.cls for mw in m.app.user_middleware]            # index 0 = outermost
    assert m.ServerErrorJSON in order and CORSMiddleware in order
    assert order.index(CORSMiddleware) < order.index(m.ServerErrorJSON), order


# ── 3. retry once on a dropped connection ─────────────────────────────────────

class _RpcClient:
    """rpc(name, args).execute() that raises the queued errors first, then returns `data`."""

    def __init__(self, data, *errors):
        self.data, self.errors, self.calls = data, list(errors), []

    def rpc(self, name, args):
        self.calls.append((name, args))

        def execute():
            if self.errors:
                raise self.errors.pop(0)
            return SimpleNamespace(data=self.data)
        return SimpleNamespace(execute=execute)


class _resets:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1


@test("retry: exec_sql / exec_sql_params read once more after a dropped connection, without resetting the client")
def _():
    from app import database, db_read
    for exc in (_dropped(), httpx.ReadError("reset by peer"), httpx.ConnectError("refused")):
        cli, resets = _RpcClient([{"n": 1}], exc), _resets()
        with _Patched((db_read, "get_client", lambda: cli), (database, "reset_client", resets)):
            assert db_read.exec_sql("SELECT 1 AS n") == [{"n": 1}]
        assert len(cli.calls) == 2 and resets.n == 0, (type(exc).__name__, cli.calls, resets.n)
    cli, resets = _RpcClient(json.dumps([{"n": 2}]), _dropped()), _resets()
    with _Patched((db_read, "get_client", lambda: cli), (database, "reset_client", resets)):
        assert db_read.exec_sql_params("SELECT $1::int AS n", [2]) == [{"n": 2}]
    assert [c[0] for c in cli.calls] == ["run_readonly_query_params"] * 2 and resets.n == 0
    assert cli.calls[1][1] == {"sql_text": "SELECT $1::int AS n", "params": ["2"]}


@test("retry: only once, and never for an ordinary error or a timeout")
def _():
    from app import database, db_read
    cli, resets = _RpcClient([], _dropped(), _dropped()), _resets()
    with _Patched((db_read, "get_client", lambda: cli), (database, "reset_client", resets)):
        try:
            db_read.exec_sql("SELECT 1")
            raise AssertionError("two drops in a row must surface")
        except httpx.RemoteProtocolError:
            pass
    assert len(cli.calls) == 2 and resets.n == 0
    for exc in (RuntimeError("permission denied for table stock_movements"), httpx.ReadTimeout("slow")):
        cli, resets = _RpcClient([], exc), _resets()
        with _Patched((db_read, "get_client", lambda: cli), (database, "reset_client", resets)):
            try:
                db_read.exec_sql("SELECT 1")
                raise AssertionError(f"{type(exc).__name__} must not be retried")
            except type(exc):
                pass
        assert len(cli.calls) == 1 and resets.n == 0, type(exc).__name__


@test("retry: reset_client() drops the process client so the next get_client() builds a new one")
def _():
    from app import database
    sentinel = object()
    saved = database._client
    try:
        database._client = sentinel
        assert database.get_client() is sentinel     # built once, then reused
        database.reset_client()
        assert database._client is None
    finally:
        database._client = saved


@test("retry: the order list and its status counts survive one dropped connection")
def _():
    from app import shop
    db = _FakeDB(shop_orders=[_order(1), _order(2, status="confirmed")], app_settings=[], salesmen=[REP])
    db.fail_once(lambda q: q.table == "shop_orders" and q.count == "exact", _dropped())
    db.fail_once(lambda q: q.table == "shop_orders" and q.cols == "status", httpx.ReadError("reset"))
    with _patched_shop(db) as p:
        out = shop.list_orders()
    assert sorted(o["id"] for o in out["orders"]) == [1, 2] and out["count"] == 2, out
    assert out["counts"]["new"] == 1 and out["counts"]["confirmed"] == 1, out["counts"]
    assert p.resets == 0
    assert len(db.selects("shop_orders", lambda q: q.count == "exact")) == 2


@test("retry: an ordinary error on the order list is not retried (it surfaces as before)")
def _():
    from app import shop
    db = _FakeDB(shop_orders=[_order(1)], app_settings=[], salesmen=[REP])
    db.fail_once(lambda q: q.table == "shop_orders" and q.count == "exact", RuntimeError("boom"))
    with _patched_shop(db) as p:
        try:
            shop.list_orders()
            raise AssertionError("expected the error")
        except RuntimeError as e:
            assert str(e) == "boom"
    assert p.resets == 0 and len(db.selects("shop_orders", lambda q: q.count == "exact")) == 1


@test("retry: the merchant's order-status read (by token) and 'my orders' survive one dropped connection")
def _():
    from app import shop
    o = _order(7)
    db = _FakeDB(shop_orders=[o], app_settings=[], salesmen=[REP], shop_order_events=[],
                 shop_order_lines=[{"id": 71, "order_id": 7, "item_code": "T01", "qty": 2,
                                    "unit_price_bhd": 1.5, "line_total_bhd": 3.0}])
    by_token = (lambda q: q.table == "shop_orders" and ("eq", "token", o["token"]) in q.filters)
    db.fail_once(by_token, _dropped())
    with _patched_shop(db) as p:
        got = shop.get_order_by_token(o["token"])
        assert got and got["id"] == 7 and got["lines"][0]["item_code"] == "T01", got
        assert p.resets == 0 and len(db.selects("shop_orders", by_token)) == 2
        # a drop in the middle (the lines) re-reads the whole order, still reads only
        db.fail_once(lambda q: q.table == "shop_order_lines", httpx.ReadError("reset"))
        got = shop.get_order_by_token(o["token"])
        assert got and len(got["lines"]) == 1 and p.resets == 0
        db.fail_once(lambda q: q.table == "shop_orders" and any(f[1] == "token" and f[0] == "in" for f in q.filters),
                     _dropped())
        mine = shop.orders_by_tokens([o["token"]])
        assert [x["order_no"] for x in mine] == [o["order_no"]] and p.resets == 0
        assert shop.get_order_by_token("short") is None


@test("retry: shop.py wraps reads only — no retry_read() call site contains a write")
def _():
    src = _read("app/shop.py")
    lines = src.splitlines()
    sites = [i for i, ln in enumerate(lines) if "retry_read(" in ln and not ln.lstrip().startswith(("#", "from "))]
    assert len(sites) >= 4, sites
    for i in sites:
        # the wrapped read is the call itself or the nested _read() just above it
        window = "\n".join(lines[max(0, i - 8): i + 4])
        for verb in (".insert(", ".update(", ".upsert(", ".delete(", "_update_optional("):
            assert verb not in window, f"shop.py:{i + 1} retries near a write ({verb})"


# ── 4. the Inventory page ─────────────────────────────────────────────────────

def _inventory_with(stub):
    from app import reports
    with _Patched((reports, "exec_sql", stub)):
        return reports.inventory()


@test("inventory: no SQL reads the ungranted base table stock_movements; arrivals come from `shipments`")
def _():
    from app import reports
    seen: list[str] = []

    def stub(sql):
        seen.append(sql)
        if "FROM shipments" in sql:
            return [{"voucher": "MRN:TEST-1", "received_on": "2026-09-20", "items": 3, "units": 40, "value_bhd": 12.5}]
        if "v_stock_health" in sql:
            return [{"item_name": "T01", "status": "low_stock"}]
        return []
    out = _inventory_with(stub)
    assert seen and not [s for s in seen if re.search(r"\bstock_movements\b", s)], seen
    assert out["recent_receipts"] == [{"voucher": "MRN:TEST-1", "received_on": "2026-09-20", "items": 3,
                                       "units": 40, "value_bhd": 12.5}], out["recent_receipts"]
    assert out["by_status"] == {"low_stock": 1}
    sql = reports.RECENT_RECEIPTS_SQL
    assert "FROM shipments" in sql and "v_stock_daily_movement" in sql and "mrn_no AS voucher" in sql
    # the columns the view really has (scripts/schema.sql: create or replace view shipments)
    view = re.search(r"create or replace view shipments as\s+select (.+?)\s+from stock_movements",
                     _read("scripts/schema.sql"), re.S | re.I)
    cols = {c.strip().split(" as ")[-1] for c in view.group(1).replace("\n", " ").split(",")}
    for col in ("received_date", "mrn_no", "item_name", "received_qty", "received_value_bhd"):
        assert col in cols and col in sql, (col, cols)
    # and nowhere else in the report module
    assert not re.search(r"FROM\s+stock_movements", _read("app/reports.py"), re.I)


@test("inventory: a failing arrivals card hides the card instead of failing the page")
def _():
    def stub(sql):
        if "FROM shipments" in sql:
            raise RuntimeError("permission denied for view shipments")
        if "v_stock_health" in sql:
            return [{"item_name": "T01", "status": "healthy"}]
        return []
    out = _inventory_with(stub)
    assert out["recent_receipts"] == [] and out["rows"] and "reserved" in out and "by_warehouse" in out


# ── 5. the web side (read from the sources) ───────────────────────────────────

@test("web: ApiError carries the server's ref; the shared error line; report pages show an error panel")
def _():
    api = _read("web/src/lib/api.ts")
    assert "ref: string | null" in api and "this.ref = refOf(body)" in api
    et = _read("web/src/lib/errorText.ts")
    assert "Could not reach the server. Check your connection and retry." in et
    assert "Server error (ref ${ref}). Please retry." in et
    assert "e instanceof TypeError" in et and "e.status >= 500" in et
    for page in ("ShopAnalytics", "ShopRules"):
        src = _read(f"web/src/pages/{page}.tsx")
        assert "from '@/lib/errorText'" in src or "from '@/components/LoadError'" in src, page
        assert "function errorText" not in src, f"{page} keeps a private copy of the error line"
    for page in ("Inventory", "Sales", "Margins", "Receivables"):
        src = _read(f"web/src/pages/{page}.tsx")
        assert "<LoadError" in src and "isError && !data" in src, page


@test("web: the link-week card shows a small retry line on error instead of vanishing")
def _():
    src = _read("web/src/pages/sales/FollowUps.tsx")
    body = src[src.index("export function LinkThisWeek"):]
    # R7b: the card reads Today's one /shop/me/today call and gets its error + retry as props
    assert "error && !d" in body and "Couldn't load" in body and "onClick={onRetry}" in body, body[:600]
    assert body.index("error && !d") < body.index("if (!d || d.hint) return null")


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
