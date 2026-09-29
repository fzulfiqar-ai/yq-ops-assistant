"""perf-2609 — the Supabase transport and the /report/inventory crash.

    python -m tests.test_perf_transport

Same lightweight runner as tests/test_r7a_reliability.py (no pytest), and the same rule: nothing
here reaches a database or the network (httpx.MockTransport stands in for Supabase).

Covered:
  * the process client talks HTTP/1.1 (the shared HTTP/2 connection was the "Waking the server"
    incident: threads racing on stream ids), with a bounded pool and a 60 s ceiling;
  * READS (GET/HEAD and the two read-only RPCs) are re-sent on a broken connection and on an HTML
    error page from the edge, at most 3 tries; WRITES are never re-sent, whatever happened;
  * reserved_stock() reads its totals by name (no `AS t` alias — it shadowed the RPC wrapper's
    row alias and every /report/inventory was a 500), and survives a bare-number row;
  * no read SQL in app/ names an output column `t`;
  * a report build that raised is remembered for 30 s, and an upload forgets it.
"""
from __future__ import annotations

import io
import os
import re
import sys
import time
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")
os.environ.setdefault("SUPABASE_KEY", "ci")

import httpx  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TESTS: list = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class _Script:
    """A MockTransport handler that plays back a list of outcomes (an exception or a Response)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


def _json(status=200, body=b"[]"):
    return httpx.Response(status, content=body, headers={"content-type": "application/json"})


def _html(status=400):
    return httpx.Response(status, content=b"<html><center>cloudflare</center></html>",
                          headers={"content-type": "text/html"})


def _client(script: _Script) -> httpx.Client:
    from app.database import RetryReadsTransport
    return httpx.Client(transport=RetryReadsTransport(httpx.MockTransport(script)),
                        base_url="https://x.supabase.co")


def _no_sleep():
    import app.database as d
    d.time.sleep = lambda _s: None  # the backoff is milliseconds, but tests needn't wait at all


@test("transport: the process client is HTTP/1.1 with a bounded pool and a 60 s ceiling")
def _():
    from app import database
    hc = database._http_client()
    inner = hc._transport._inner
    assert isinstance(hc._transport, database.RetryReadsTransport)
    assert inner._pool._http2 is False and inner._pool._http1 is True
    assert inner._pool._max_connections == 20
    assert hc.timeout.read == 60.0 and hc.timeout.connect == 5.0
    hc.close()


@test("transport: a GET is re-sent after a dropped connection")
def _():
    _no_sleep()
    for exc in (httpx.RemoteProtocolError("<ConnectionTerminated error_code:1>"),
                httpx.ReadError("reset"), httpx.LocalProtocolError("StreamIDTooLowError")):
        s = _Script(exc, _json(body=b'[{"id":1}]'))
        r = _client(s).get("/rest/v1/shop_orders?select=id")
        assert r.status_code == 200 and r.json() == [{"id": 1}] and len(s.calls) == 2, type(exc).__name__


@test("transport: a read RPC is re-sent on an HTML 400 from the edge, with the same body")
def _():
    _no_sleep()
    s = _Script(_html(400), _json(body=b'[{"one":1}]'))
    r = _client(s).post("/rest/v1/rpc/run_readonly_query", json={"sql_text": "select 1 as one"})
    assert r.json() == [{"one": 1}] and len(s.calls) == 2
    assert s.calls[0].content == s.calls[1].content == b'{"sql_text":"select 1 as one"}'
    s = _Script(httpx.RemoteProtocolError("gone"), _json())
    _client(s).post("/rest/v1/rpc/run_readonly_query_params", json={"sql_text": "x", "params": []})
    assert len(s.calls) == 2


@test("transport: gives up after 3 tries (raises the last error / returns the last page)")
def _():
    _no_sleep()
    s = _Script(*(httpx.RemoteProtocolError("gone") for _ in range(3)))
    try:
        _client(s).get("/rest/v1/x")
        raise AssertionError("three drops must surface")
    except httpx.RemoteProtocolError:
        pass
    assert len(s.calls) == 3
    s = _Script(_html(400), _html(400), _html(400))
    assert _client(s).get("/rest/v1/x").status_code == 400 and len(s.calls) == 3


@test("transport: WRITES are never re-sent — inserts, updates, deletes, other RPCs")
def _():
    _no_sleep()
    writes = [("POST", "/rest/v1/shop_orders"), ("PATCH", "/rest/v1/shop_orders?id=eq.1"),
              ("DELETE", "/rest/v1/shop_orders?id=eq.1"), ("POST", "/rest/v1/rpc/place_order"),
              ("POST", "/rest/v1/rpc/run_readonly_query_evil")]
    for method, path in writes:
        s = _Script(httpx.RemoteProtocolError("gone"))
        try:
            _client(s).request(method, path, json={"a": 1})
            raise AssertionError(f"{method} {path} must surface")
        except httpx.RemoteProtocolError:
            pass
        assert len(s.calls) == 1, (method, path)
        s = _Script(_html(400))
        assert _client(s).request(method, path, json={"a": 1}).status_code == 400 and len(s.calls) == 1


@test("transport: a JSON error (PostgREST's own) is answered as is — only HTML pages are retried")
def _():
    _no_sleep()
    s = _Script(_json(400, b'{"code":"42703","message":"column does not exist"}'))
    r = _client(s).get("/rest/v1/user_roles?select=must_reset")
    assert r.status_code == 400 and len(s.calls) == 1
    s = _Script(httpx.ReadTimeout("slow"))
    try:
        _client(s).get("/rest/v1/x")
        raise AssertionError("a timeout must surface")
    except httpx.ReadTimeout:
        pass
    assert len(s.calls) == 1


@test("reserved_stock: totals are read by name, and a bare-number row cannot crash the page")
def _():
    from app import reports
    saved = reports.exec_sql
    try:
        answers = [[{"item_code": "A1", "reserved": 2}],
                   [{"reserved_units": 3, "in_transit_units": 4, "reserved_items": 1, "open_orders": 2,
                     "as_of": "2026-09-28"}]]
        reports.exec_sql = lambda sql: answers.pop(0)
        out = reports.reserved_stock()
        assert out["available"] and out["units"] == 3.0 and out["in_transit"] == 4.0 and out["items"] == 1
        assert out["stock_as_of"] == "2026-09-28" and out["rows"][0]["item_code"] == "A1"
        answers = [[], [0]]                      # what the RPC returned while the alias was `t`
        reports.exec_sql = lambda sql: answers.pop(0)
        out = reports.reserved_stock()
        assert out["available"] and out["units"] == 0.0 and out["items"] == 0
    finally:
        reports.exec_sql = saved


@test("SQL: no string in app/ names an output column `t` (it shadows run_readonly_query's row alias)")
def _():
    bad = re.compile(r"\bAS\s+\"?t\"?\s*(,|FROM\b|$)", re.I | re.M)
    hits = []
    for path in (ROOT / "app").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        if "exec_sql" not in src and "run_readonly_query" not in src:
            continue                              # e.g. ingest_batch.py: psycopg, no RPC wrapper
        for m in bad.finditer(src):
            hits.append(f"{path.relative_to(ROOT)}: {m.group(0)!r}")
    assert not hits, hits


@test("reports: a failed build is remembered for 30 s, and an upload forgets it")
def _():
    from app import reports
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("view missing")
    saved = dict(reports.REPORTS)
    reports.REPORTS["_t"] = boom
    try:
        for _ in range(3):
            try:
                reports.cached_report("_t")
                raise AssertionError("must raise")
            except RuntimeError:
                pass
        assert len(calls) == 1, calls             # the 2nd and 3rd answer from the failure cache
        reports._report_failed["_t"] = (time.time() - reports._REPORT_FAIL_S - 1, RuntimeError("old"))
        try:
            reports.cached_report("_t")
        except RuntimeError:
            pass
        assert len(calls) == 2                    # expired: rebuilt
        reports.invalidate_dashboard_cache()
        assert "_t" not in reports._report_failed
        reports.REPORTS["_t"] = lambda: {"ok": 1}
        assert reports.cached_report("_t") == {"ok": 1} and "_t" not in reports._report_failed
    finally:
        reports.REPORTS.clear()
        reports.REPORTS.update(saved)
        reports._report_cache.pop("_t", None)
        reports._report_failed.pop("_t", None)


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
