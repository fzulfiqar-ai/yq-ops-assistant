"""Release R8 — the API notices a data load made outside it and drops its cached numbers.

    python -m tests.test_r8_datastamp

Same lightweight runner as tests/test_perf_transport.py (no pytest). Nothing here reaches a database
or the network: the stamp reader is a stand-in, and the real reader runs against httpx.MockTransport.
All data is synthetic.

The fault this covers (7-Oct-2026): October was loaded from a developer's PC, and the API went on
answering from its in-process caches — the Dashboard showed September for about half an hour.

Covered (app/datastamp.py and its call sites):
  * off until watch(): no read in scripts, crons or the other suites; app.main arms it at boot;
  * the first look only notes the stamp; the same stamp drops nothing; a changed stamp drops every
    in-process cache flush_cache() drops (reports + dashboard, the Command Centre, the reps' sales
    block, the shop context and its settings, the assistant's "data as of" label);
  * one read per 30 s per process whatever the traffic; a reader that raises changes nothing, is
    not retried before the next look, and warns once per run of failures;
  * requests arriving together: one read, one drop; the request beside the looker gives the look a
    moment and then reads what is already dropped (a page sends GET /freshness and its report
    together: without the moment one of the two was always answered from the old payload); a look
    that hangs holds nobody past LOOK_WAIT_S from its start, and LOOK_WAIT_S = 0 switches it off;
  * the cached readers look on entry, BEFORE they answer from the cache (the report cache, the
    dashboard, the prewarm, the Command Centre overview / attention, the header chip, the rep's
    sales block, the shop context);
  * an upload through the API works as before: flush_cache() = the same drop + the query_cache
    delete, and it never reads the stamp;
  * noticing a load never imports the assistant (app.ai pulls in the LLM client);
  * the real reader is ONE GET for the highest ingest_runs.id, with the SDK's own 503 retry off.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import types
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")   # app.main builds a JWKS URL from it; nothing is fetched

import httpx  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


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


class _Stamp:
    """A stand-in for datastamp.newest_load: answers `value` (raises it when it is an exception)
    and counts how often it was asked."""

    def __init__(self, value=None):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


def _caches() -> list[dict]:
    from app import ai, metrics, reports, shop
    return [reports._report_cache, reports._dash_cache, reports._report_failed,
            metrics._focus_cache, metrics._live_cache, metrics._fresh_cache, metrics._view_probe,
            shop._focus_cache, shop._ctx_cache, shop._settings_cache, shop._salesman_cache,
            ai._data_date_cache]


class _Sandbox:
    """datastamp's own state and every cache these tests fill, put back as they were afterwards.
    With a reader, the process watches for loads for the length of the test."""

    def __init__(self, read=None):
        self.read = read

    def __enter__(self):
        from app import datastamp
        self.seen = dict(datastamp._seen)
        self.saved = [(d, dict(d)) for d in _caches()]
        if self.read is not None:
            datastamp.watch(self.read)
        return self

    def __exit__(self, *exc):
        from app import datastamp
        for d, was in self.saved:
            d.clear()
            d.update(was)
        datastamp._seen.clear()
        datastamp._seen.update(self.seen)
        return False


def _due() -> None:
    """Make the next sync() a real look: the last one was more than CHECK_EVERY_S ago."""
    from app import datastamp
    datastamp._seen["at"] = time.monotonic() - datastamp.CHECK_EVERY_S - 1


def _fill() -> dict:
    """Put a recognisable payload in every in-process cache; returns the generations before any drop."""
    from app import ai, metrics, reports, shop
    now = time.time()
    reports._report_cache["_t"] = (now, {"v": "old"})
    reports._dash_cache.update(at=now, payload={"v": "old"})
    reports._report_failed["_x"] = (now, RuntimeError("old"))
    metrics._focus_cache["mtd"] = (now, {"v": "old"})
    metrics._live_cache["mtd"] = (now, {"v": "old"})
    metrics._fresh_cache["plain"] = (now, {"focus_to": "2026-09-30"})
    metrics._view_probe.update(at=now, ok=False)
    shop._focus_cache["Rep One"] = (now, {"revenue_90d_bhd": 1.0})
    shop._ctx_cache.update(at=now, ctx={"v": "old"})
    shop._settings_cache.update(at=now, vals={"shop_market_enabled": "1"})
    shop._salesman_cache["rep@example.com"] = (time.monotonic() + 60, {"id": 1})
    ai._data_date_cache.update(d="30 Sep 2026", at=now)
    return {"report": reports._report_gen, "ctx": shop._ctx_cache["gen"]}


def _kept(gen: dict) -> bool:
    from app import ai, metrics, reports, shop
    return (reports._report_cache.get("_t", (0, None))[1] == {"v": "old"} and reports._dash_cache["payload"] == {"v": "old"}
            and "_x" in reports._report_failed and reports._report_gen == gen["report"]
            and "mtd" in metrics._focus_cache and "mtd" in metrics._live_cache and "plain" in metrics._fresh_cache
            and "Rep One" in shop._focus_cache and shop._ctx_cache["ctx"] == {"v": "old"}
            and shop._ctx_cache["gen"] == gen["ctx"] and shop._settings_cache["vals"] is not None
            and "rep@example.com" in shop._salesman_cache and ai._data_date_cache["d"] == "30 Sep 2026")


def _assert_dropped(gen: dict, drops: int = 1) -> None:
    from app import ai, metrics, reports, shop
    assert not reports._report_cache and not reports._report_failed, "the report cache is empty"
    assert reports._dash_cache["payload"] is None, "the dashboard payload is gone"
    assert reports._report_gen == gen["report"] + drops, "a build that started before the load is not stored"
    assert not metrics._focus_cache and not metrics._live_cache and not metrics._fresh_cache, "the Command Centre caches are empty"
    assert metrics._view_probe["ok"] is None, "the Command Centre view is probed again"
    assert not shop._focus_cache, "the reps' sales block is gone"
    assert shop._ctx_cache["ctx"] is None and shop._ctx_cache["gen"] == gen["ctx"] + drops, "the shop context is rebuilt"
    assert shop._settings_cache["vals"] is None and not shop._salesman_cache
    assert ai._data_date_cache["d"] == "", "the assistant's data-as-of label is read again"


# ── the switch ─────────────────────────────────────────────────────────────────

@test("off until watch(): sync() answers at once and never reads (scripts, crons, the other suites)")
def _():
    from app import datastamp
    with _Sandbox():
        datastamp._seen.update(read=None, at=float("-inf"))
        with _Patched((datastamp, "newest_load", lambda: (_ for _ in ()).throw(AssertionError("read while off")))):
            gen = _fill()
            assert [datastamp.sync() for _ in range(3)] == [False, False, False]
            assert _kept(gen) and datastamp._seen["stamp"] is datastamp._UNSEEN


@test("boot: app.main arms it in a startup handler, with the real reader and a clean slate")
def _():
    import app.main as m
    from app import datastamp
    assert m._watch_data_loads in m.app.router.on_startup, "registered with @app.on_event('startup')"
    with _Sandbox():
        datastamp._seen.update(read=None, stamp=41, at=time.monotonic(), failing=True)
        asyncio.run(m._watch_data_loads())
        assert datastamp._seen["read"] is datastamp.newest_load
        assert datastamp._seen["stamp"] is datastamp._UNSEEN and datastamp._seen["at"] == float("-inf")
        assert datastamp._seen["failing"] is False


# ── noticing a load ────────────────────────────────────────────────────────────

@test("the first look after boot only notes the stamp: nothing is dropped")
def _():
    from app import datastamp
    stamp = _Stamp(58)
    with _Sandbox(stamp):
        gen = _fill()
        assert datastamp.sync() is False and stamp.calls == 1
        assert datastamp._seen["stamp"] == 58 and _kept(gen)


@test("the same stamp at the next look: one read, nothing dropped")
def _():
    from app import datastamp
    stamp = _Stamp(58)
    with _Sandbox(stamp):
        datastamp.sync()
        gen = _fill()
        for n in (2, 3):
            _due()
            assert datastamp.sync() is False and stamp.calls == n
        assert _kept(gen)


@test("a changed stamp: every in-process cache flush_cache() drops is dropped, once")
def _():
    from app import datastamp
    stamp = _Stamp(58)
    with _Sandbox(stamp):
        datastamp.sync()
        gen = _fill()
        stamp.value = 59                       # a load landed somewhere else
        _due()
        assert datastamp.sync() is True and stamp.calls == 2
        _assert_dropped(gen)
        assert datastamp._seen["stamp"] == 59
        _due()
        assert datastamp.sync() is False, "the new stamp is remembered: the same load is not noticed twice"
        _assert_dropped(gen)


@test("any change counts: an empty table then its first row, and a stamp that goes down")
def _():
    from app import datastamp, reports
    stamp = _Stamp(None)                       # an empty ingest_runs is a real answer, not "unknown"
    with _Sandbox(stamp):
        assert datastamp.sync() is False and datastamp._seen["stamp"] is None
        gen = reports._report_gen
        stamp.value = 1
        _due()
        assert datastamp.sync() is True and reports._report_gen == gen + 1
        stamp.value = None                     # rows deleted: the newest load is no longer the one we knew
        _due()
        assert datastamp.sync() is True and reports._report_gen == gen + 2


@test("one read per 30 s per process, however many callers; the look after that sees the load")
def _():
    from app import datastamp
    assert datastamp.CHECK_EVERY_S == 30.0
    stamp = _Stamp(58)
    with _Sandbox(stamp):
        datastamp.sync()
        gen = _fill()
        stamp.value = 59
        assert not any(datastamp.sync() for _ in range(200)) and stamp.calls == 1
        with datastamp._lock:       # a request that judged the look due, then got the lock after another had looked
            assert datastamp._look(stamp) is False and stamp.calls == 1, "the look is not made twice"
        datastamp._seen["at"] = time.monotonic() - (datastamp.CHECK_EVERY_S - 2)
        assert datastamp.sync() is False and stamp.calls == 1 and _kept(gen), "28 s after the last look: not yet"
        datastamp._seen["at"] = time.monotonic() - (datastamp.CHECK_EVERY_S + 0.5)
        assert datastamp.sync() is True and stamp.calls == 2
        _assert_dropped(gen)


class _Log(logging.Handler):
    """Collects what app.datastamp logs (levels and text) and keeps the expected warnings out of the
    test output."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.levels: list[int] = []
        self.lines: list[str] = []

    def emit(self, record):
        self.levels.append(record.levelno)
        self.lines.append(record.getMessage())

    def __enter__(self):
        self.log = logging.getLogger("app.datastamp")
        self.was = (self.log.level, self.log.propagate)
        self.log.addHandler(self)
        self.log.setLevel(logging.DEBUG)
        self.log.propagate = False
        return self

    def __exit__(self, *exc):
        self.log.removeHandler(self)
        self.log.setLevel(self.was[0])
        self.log.propagate = self.was[1]
        return False


@test("a reader that raises: nothing changes, nothing is raised, no retry before the next look, one warning per run of failures")
def _():
    from app import datastamp
    stamp = _Stamp(RuntimeError("supabase is down"))
    with _Sandbox(stamp), _Log() as log:
        gen = _fill()
        assert datastamp.sync() is False and stamp.calls == 1
        assert datastamp._seen["stamp"] is datastamp._UNSEEN and _kept(gen)
        assert datastamp.sync() is False and stamp.calls == 1, "a failed read is not tried again on every request"
        _due()
        assert datastamp.sync() is False and stamp.calls == 2 and _kept(gen)
        assert log.levels == [logging.WARNING, logging.DEBUG], log.levels
        assert "supabase is down" in log.lines[0]
        stamp.value = 58                   # back: the first good look still only notes the stamp
        _due()
        assert datastamp.sync() is False and datastamp._seen["stamp"] == 58 and _kept(gen)
        stamp.value = TimeoutError("slow")
        _due()
        assert datastamp.sync() is False and datastamp._seen["stamp"] == 58, "the last known stamp is kept"
        assert log.levels[2:] == [logging.WARNING], "a new run of failures warns again"
        stamp.value = 59                   # the load that landed during the outage is still noticed
        _due()
        assert datastamp.sync() is True
        _assert_dropped(gen)
        assert log.levels[3:] == [logging.INFO] and "59" in log.lines[3], "the notice says which load"


@test("one part of the drop failing never keeps the others, and sync() still does not raise")
def _():
    from app import datastamp, reports, shop

    def boom():
        raise RuntimeError("reports broke")
    stamp = _Stamp(58)
    with _Sandbox(stamp), _Log() as log:
        datastamp.sync()
        gen = _fill()
        stamp.value = 59
        _due()
        with _Patched((reports, "invalidate_dashboard_cache", boom)):
            assert datastamp.sync() is True
        assert shop._ctx_cache["ctx"] is None and shop._ctx_cache["gen"] == gen["ctx"] + 1
        assert reports._report_cache, "the part that failed is the only one left"
        assert logging.WARNING in log.levels and any("reports broke" in line for line in log.lines)


# ── concurrency ────────────────────────────────────────────────────────────────

class _Parked:
    """A stamp read that stays in flight until the test lets it go."""

    def __init__(self, value):
        self.value = value
        self.calls = 0
        self.entered, self.release = threading.Event(), threading.Event()

    def __call__(self):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(20), "the test never released the read"
        return self.value


@test("a page's requests arrive together: the one beside the looker gives the look a moment, then reads what is already dropped (one read, one drop)")
def _():
    from app import datastamp, reports, shop
    read = _Parked(59)
    # a long LOOK_WAIT_S here: the test, not the clock, decides when the read comes back
    with _Sandbox(_Stamp(58)), _Patched((datastamp, "LOOK_WAIT_S", 60.0)):
        datastamp.sync()
        gen = _fill()
        datastamp._seen["read"] = read
        _due()
        first, second = [], []

        def beside():           # e.g. GET /report/dashboard, a moment after GET /freshness began the look
            dropped = datastamp.sync()
            second.append((dropped, reports._report_cache.get("_t"), shop._ctx_cache["ctx"]))
        a = threading.Thread(target=lambda: first.append(datastamp.sync()))
        b = threading.Thread(target=beside)
        a.start()
        try:
            assert read.entered.wait(10), "the first request never started its read"
            b.start()
            b.join(0.3)
            assert b.is_alive() and not second, "it does not answer from a payload that is about to be dropped"
            assert _kept(gen)
        finally:
            read.release.set()
            a.join(10)
            if b.ident is not None:
                b.join(10)
        assert first == [True] and second == [(False, None, None)], second
        assert read.calls == 1
        _assert_dropped(gen)


@test("a look that hangs holds nobody for long: the request beside it leaves LOOK_WAIT_S after the look began, later ones do not wait at all")
def _():
    from app import datastamp
    assert datastamp.LOOK_WAIT_S == 1.0
    read = _Parked(59)
    with _Sandbox(_Stamp(58)), _Patched((datastamp, "LOOK_WAIT_S", 0.4)):
        datastamp.sync()
        gen = _fill()
        datastamp._seen["read"] = read
        _due()
        a = threading.Thread(target=datastamp.sync)
        a.start()
        try:
            assert read.entered.wait(10), "the first request never started its read"
            t0 = time.monotonic()
            assert datastamp.sync() is False
            assert time.monotonic() - datastamp._seen["at"] >= 0.35, "it gave the look its moment"
            assert time.monotonic() - t0 < 5.0 and _kept(gen), "then it answered from what is cached"
            t1 = time.monotonic()
            assert datastamp.sync() is False            # past LOOK_WAIT_S: nothing to wait for
            _due()                                      # the next look falls due while this one still hangs
            assert datastamp._lock.locked() and datastamp.sync() is False
            assert time.monotonic() - t1 < 0.3, "no request queues behind a hung look"
            assert read.calls == 1 and _kept(gen)
        finally:
            read.release.set()
            a.join(10)
        _assert_dropped(gen)


@test("LOOK_WAIT_S = 0 switches the moment off: the request beside the looker answers at once from what is cached")
def _():
    from app import datastamp
    read = _Parked(59)
    with _Sandbox(_Stamp(58)), _Patched((datastamp, "LOOK_WAIT_S", 0.0)):
        datastamp.sync()
        gen = _fill()
        datastamp._seen["read"] = read
        _due()
        a = threading.Thread(target=datastamp.sync)
        a.start()
        try:
            assert read.entered.wait(10), "the first request never started its read"
            t0 = time.monotonic()
            assert datastamp.sync() is False
            _due()
            assert datastamp._lock.locked() and datastamp.sync() is False
            assert time.monotonic() - t0 < 0.3 and _kept(gen) and read.calls == 1
        finally:
            read.release.set()
            a.join(10)
        _assert_dropped(gen)


@test("twenty requests arriving together when a look is due: one read, one drop")
def _():
    from app import datastamp
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.05)
        return 59

    with _Sandbox(_Stamp(58)):
        datastamp.sync()
        gen = _fill()
        datastamp._seen["read"] = slow
        _due()
        gate = threading.Barrier(20)
        out = []

        def go():
            gate.wait(10)
            out.append(datastamp.sync())
        threads = [threading.Thread(target=go) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert len(out) == 20 and out.count(True) == 1 and len(calls) == 1, (out.count(True), len(calls))
        _assert_dropped(gen)


# ── the call sites ─────────────────────────────────────────────────────────────

@test("every cached reader looks for a new load on entry")
def _():
    from app import datastamp, metrics, reports, shop
    looks = []
    live = {"modules": [{"items": []}], "freshness": {}, "generated_at": "x"}
    with _Sandbox(), _Patched((datastamp, "sync", lambda: looks.append(1))):
        now = time.time()
        saved = dict(reports.REPORTS)
        reports.REPORTS.clear()
        reports.REPORTS["_t"] = lambda: {"v": "new"}
        try:
            reports._report_cache["_t"] = (now, {"v": "old"})
            assert reports.cached_report("_t") == {"v": "old"} and len(looks) == 1
            reports.prewarm_reports()                       # everything is warm: only the look happens
            assert len(looks) == 2
        finally:
            reports.REPORTS.clear()
            reports.REPORTS.update(saved)
        reports._dash_cache.update(at=now, payload={"v": "old"})
        assert reports.dashboard() == {"v": "old"} and len(looks) == 3
        metrics._focus_cache["mtd"] = (now, {"v": "old"})
        metrics._live_cache["mtd"] = (now, live)
        assert metrics.overview("mtd") is live and len(looks) == 4
        assert metrics.attention()["generated_at"] == "x" and len(looks) == 5
        metrics._fresh_cache["plain"] = (now, {"focus_to": "2026-09-30"})
        assert metrics.freshness(False)["focus_to"] == "2026-09-30" and len(looks) == 6
        shop._focus_cache["Rep One"] = (now, {"revenue_90d_bhd": 1.0})
        assert shop._rep_focus("Rep One") == {"revenue_90d_bhd": 1.0} and len(looks) == 7
        shop._ctx_cache.update(at=now, ctx={"v": "old"})
        assert shop.context() == {"v": "old"} and len(looks) == 8


@test("the look comes BEFORE the cache is answered from: a page, the header chip and the catalog are fresh on the same request")
def _():
    from app import datastamp, metrics, reports, shop
    stamp = _Stamp(58)

    def chip_reader(day):
        return metrics.Reader(q=lambda sql: [{"focus_to": day, "last_order_at": None}], qp=lambda sql, p: [],
                              salesmen_fn=lambda: [], now=datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc))

    with _Sandbox(stamp):
        saved = dict(reports.REPORTS)
        reports.REPORTS["_t"] = lambda: {"sales_to": "2026-09-30"}
        try:
            metrics._fresh_cache.clear()
            assert reports.cached_report("_t") == {"sales_to": "2026-09-30"}          # first look: the stamp is noted
            assert metrics.freshness(False, reader=chip_reader("2026-09-30"))["focus_to"] == "2026-09-30"
            shop._ctx_cache.update(at=time.time(), ctx={"loaded": "september"})
            # October is loaded from another machine: new data, a new ingest_runs row
            reports.REPORTS["_t"] = lambda: {"sales_to": "2026-10-06"}
            stamp.value = 59
            assert reports.cached_report("_t") == {"sales_to": "2026-09-30"}, "inside the 30 s: still the cached page"
            assert metrics.freshness(False, reader=chip_reader("2026-10-06"))["focus_to"] == "2026-09-30"
            _due()
            assert reports.cached_report("_t") == {"sales_to": "2026-10-06"}, "the page that noticed the load is already fresh"
            assert metrics.freshness(False, reader=chip_reader("2026-10-06"))["focus_to"] == "2026-10-06"
            with _Patched((shop, "_build_context", lambda: {"loaded": "october"})):
                assert shop.context() == {"loaded": "october"}
            assert stamp.calls == 2, "one look served all three"
        finally:
            reports.REPORTS.clear()
            reports.REPORTS.update(saved)


# ── an upload through the API ──────────────────────────────────────────────────

class _CacheTable:
    """The one chain flush_cache() runs on the client: table('query_cache').delete().neq(...).execute()."""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.calls: list[tuple] = []

    def table(self, name):
        self.calls.append(("table", name))
        return self

    def delete(self):
        self.calls.append(("delete",))
        return self

    def neq(self, col, val):
        self.calls.append(("neq", col, val))
        return self

    def execute(self):
        self.calls.append(("execute",))
        if self.fail:
            raise self.fail
        return types.SimpleNamespace(data=[])


@test("flush_cache(): the same in-process drop, then the query_cache delete; it never reads the stamp")
def _():
    from app import ai, datastamp
    stamp = _Stamp(58)
    with _Sandbox(stamp):
        datastamp.sync()
        db = _CacheTable()
        with _Patched((ai, "get_client", lambda: db)):
            gen = _fill()
            assert ai.flush_cache() == 1
        _assert_dropped(gen)
        assert db.calls == [("table", "query_cache"), ("delete",), ("neq", "query_hash", ""), ("execute",)]
        assert stamp.calls == 1 and datastamp._seen["stamp"] == 58, "an upload leaves the watch alone"
        down = _CacheTable(RuntimeError("no database"))
        with _Patched((ai, "get_client", lambda: down), (ai.log, "disabled", True)):   # its warning is expected
            gen = _fill()
            assert ai.flush_cache() == 0, "the delete failed: 0, as before"
        _assert_dropped(gen)


@test("the drop touches the assistant's label only where the assistant is loaded, half-imported included")
def _():
    from app import ai, datastamp
    with _Sandbox():
        real = sys.modules["app.ai"]
        try:
            del sys.modules["app.ai"]
            ai._data_date_cache.update(d="30 Sep 2026", at=time.time())
            datastamp.drop_process_caches()
            assert "app.ai" not in sys.modules, "noticing a load must not import the assistant"
            assert ai._data_date_cache["d"] == "30 Sep 2026", "a process without the assistant has no label to drop"
            sys.modules["app.ai"] = types.ModuleType("app.ai")      # another thread is half-way through importing it
            datastamp.drop_process_caches()
        finally:
            sys.modules["app.ai"] = real
        datastamp.drop_process_caches()
        assert ai._data_date_cache["d"] == ""


@test("a fresh process: not watching until told to, and noticing a load never loads the LLM client (subprocess, no network)")
def _():
    code = ("import sys; from app import datastamp, metrics, reports, shop; "
            "assert datastamp._seen['read'] is None and datastamp.sync() is False, 'watching without watch()'; "
            "datastamp.watch(lambda: 1); datastamp.sync(); "
            "datastamp._seen.update(read=lambda: 2, at=float('-inf')); assert datastamp.sync() is True; "
            "print('ai' if 'app.ai' in sys.modules else 'no-ai', 'openai' if 'openai' in sys.modules else 'no-openai')")
    env = dict(os.environ, SUPABASE_URL="https://ci.invalid", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=180)
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    assert r.stdout.split()[-2:] == ["no-ai", "no-openai"], r.stdout[-300:]


# ── the real reader ────────────────────────────────────────────────────────────

def _sdk_client(handler):
    """The pinned SDK on httpx.MockTransport: the request leaves supabase-py exactly as in production."""
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions
    return create_client("https://ci.invalid", "aaa.bbb.ccc",
                         options=SyncClientOptions(httpx_client=httpx.Client(transport=httpx.MockTransport(handler))))


@test("newest_load(): ONE GET for the highest ingest_runs.id; an empty table is None")
def _():
    from app import database, datastamp
    seen: list[httpx.Request] = []
    answer = [b'[{"id":59}]']

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=answer[0], headers={"content-type": "application/json"})
    saved = database._client
    database._client = _sdk_client(handler)
    try:
        assert datastamp.newest_load() == 59
        assert len(seen) == 1 and seen[0].method == "GET", "a read, never a write"
        assert seen[0].url.path == "/rest/v1/ingest_runs"
        assert dict(seen[0].url.params) == {"select": "id", "order": "id.desc", "limit": "1"}
        answer[0] = b"[]"
        assert datastamp.newest_load() is None
    finally:
        database._client = saved


@test("newest_load(): a 503 is answered at once (the SDK's 1 + 2 + 4 s retry is off) and sync() swallows it")
def _():
    from app import database, datastamp
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(503, content=b'{"code":"PGRST002","message":"down","details":null,"hint":null}',
                              headers={"content-type": "application/json"})
    saved = database._client
    database._client = _sdk_client(handler)
    try:
        t0, raised = time.monotonic(), False
        try:
            datastamp.newest_load()
        except Exception:  # noqa: BLE001 -- postgrest's APIError
            raised = True
        assert raised, "a 503 surfaces from the reader (sync() is what swallows it)"
        assert len(seen) == 1 and time.monotonic() - t0 < 0.9, (len(seen), time.monotonic() - t0)
        with _Sandbox(), _Log() as log:
            datastamp.watch()
            assert datastamp._seen["read"] is datastamp.newest_load
            assert datastamp.sync() is False and len(seen) == 2
            assert log.levels == [logging.WARNING] and "PGRST002" in log.lines[0]
    finally:
        database._client = saved


# ── CI ─────────────────────────────────────────────────────────────────────────

@test("ci: this suite runs in the api job")
def _():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python -m tests.test_r8_datastamp" in ci.split("\n  web:\n", 1)[0]


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
