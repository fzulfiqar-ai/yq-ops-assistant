"""The load's reconcile step (scripts/reconcile_check.py): what it compares, and what it leaves to the refresh.

    python -m tests.test_ops_reconcile

Same lightweight runner as the other suites (no pytest). SUPABASE_URL / SUPABASE_KEY / DATABASE_URL are pinned to
ci.invalid before any import, and the Supabase client is scripts.ops.db's guarded one on a transport that answers
nothing: every read goes to a stand-in for the read-only RPC that answers by the shape of its SQL, so nothing
reaches a database or the network (a request that slips past a stand-in is recorded and fails the suite). Every
figure is invented, made up at run time.

Covered:
  * layer 2 reads the Dashboard's two receivables tiles alone (reconcile_check.dashboard_receivables): the data
    date and the one v_receivables sum, the SQL byte for byte what app.digest.daily_summary sends, converted as it
    converts them; the very figures dashboard() shows on the same book (kpis.total_receivables,
    kpis.overdue_total_bhd); zero while no sale is loaded, as there; never the whole payload;
  * --verified-run <id>, which the load passes after its own refresh verified: layer 1 is not run again and the
    output says so in one line; layer 2 still compares the tiles and the collections agent and still finds a drift;
  * without it everything runs as before: layer 1 on the folder, its rows first, its drift a FAIL with its WHY;
  * the command line: the folder and the option in the load's own order (scripts/yq_load_go.py), an id that is no
    whole number above 0 refused before anything runs, a missing folder still exit 1; CI runs this suite.
"""
from __future__ import annotations

import contextlib
import io
import os
import random
import re
import sys
import tempfile
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

import httpx  # noqa: E402

from scripts.ops import db  # noqa: E402  (first, as every command does)

REACHED: list[str] = []          # requests that got past every stand-in: none may


def _no_network(request: httpx.Request) -> httpx.Response:
    REACHED.append(f"{request.method} {request.url.path}")
    return httpx.Response(599, json={"message": "this suite has no network"})


db.install(inner=httpx.MockTransport(_no_network), url="https://ci.invalid", key="ci-key")

from app import agents, digest, reports  # noqa: E402
from app import settings as app_settings  # noqa: E402
from scripts import ingest, reconcile_check as rc, verify_numbers as vn  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class _Patched:
    """Swap module attributes for the duration of a with-block."""

    def __init__(self, *triples):
        self.triples = triples
        self.saved: list = []

    def __enter__(self):
        for mod, name, val in self.triples:
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, val)
        return self

    def __exit__(self, *exc):
        for mod, name, val in reversed(self.saved):
            setattr(mod, name, val)
        return False


def _raises(fn, kind=Exception) -> BaseException:
    try:
        fn()
    except kind as exc:
        return exc
    raise AssertionError(f"expected {kind.__name__}")


OVERDUE_BUCKETS = ("bucket_31_60", "bucket_61_90", "bucket_91_120", "bucket_121_150", "bucket_151_180",
                   "bucket_181_210", "bucket_over_210")
# the Dashboard's other sections, each a stand-in: only daily_summary (the tiles' source) is the real code
SECTIONS = (("all_alerts", {"low_stock_count": 3, "negative_margin_count": 0, "low_stock": []}),
            ("business_health", {}), ("movers", {"rising": [], "falling": []}), ("revenue_trend", []),
            ("sales_by_channel", []), ("data_freshness", {"stale": False, "days_behind": 1}), ("daily_sales_mtd", []),
            ("sales_split_mtd", {"by_payment": [], "by_division": []}),
            ("salesman_attainment_result", {"rows": [], "error": None}), ("command_basis", None),
            ("revenue_trend_acc", []), ("top_customers_acc_mtd", []), ("payment_split_acc_mtd", None))


def _book(seed: int, data_date: str | None = "2026-10-19"):
    """An invented receivables book (made up at run time) and a stand-in for the read-only RPC that answers by
    the shape of the SQL: the data date, the v_receivables sum (in the RPC's text numerics), a zero for every
    v_sales sum. -> (book, rpc, the SQL it was sent)"""
    rng = random.Random(seed)
    total = round(rng.uniform(4000, 20000), 3)
    book = {"total": total, "overdue": round(total * rng.uniform(0.2, 0.6), 3), "accounts": rng.randint(4, 40)}
    seen: list[str] = []

    def rpc(sql: str):
        seen.append(sql)
        if "MAX(sale_date) AS d FROM v_sales" in sql:
            return [{"d": data_date}]
        if "FROM v_receivables" in sql:
            return [{"total": str(book["total"]), "overdue": str(book["overdue"]), "overdue_accounts": book["accounts"]}]
        return [] if "GROUP BY" in sql else [{"rev": 0, "net": 0, "orders": 0}]
    return book, rpc, seen


def _dashboard_kpis(rpc) -> dict:
    """dashboard()'s kpis on the stand-in book: the real daily_summary, every other section a stand-in."""
    patches = [(reports, fn, (lambda ret: (lambda *a, **kw: ret))(ret)) for fn, ret in SECTIONS]
    saved = dict(reports._dash_cache)
    try:
        with _Patched(*patches, (digest, "exec_sql", rpc), (app_settings, "setting", lambda key: None)):
            assert reports.daily_summary is digest.daily_summary, "the payload's summary is the digest's own"
            return reports.dashboard(force=True)["kpis"]
    finally:                                   # this process's cache is left as it was found
        reports._dash_cache.clear()
        reports._dash_cache.update(saved)


# ── layer 2: the two tiles alone ──────────────────────────────────────────────────────────────

@test("layer 2's two reads are daily_summary's own, byte for byte: its data date, then the one v_receivables sum")
def _():
    _book_, rpc, seen = _book(2010)
    with _Patched((digest, "exec_sql", rpc)):
        digest.daily_summary()
        sent_by_summary = list(seen)
        seen.clear()
        rc.dashboard_receivables()
    assert [s for s in sent_by_summary if "v_receivables" in s] == [rc.RECEIVABLES_SQL], sent_by_summary
    assert seen == [sent_by_summary[0], rc.RECEIVABLES_SQL], seen
    assert sent_by_summary[0] == "SELECT MAX(sale_date) AS d FROM v_sales LIMIT 1"
    sql = rc.RECEIVABLES_SQL                   # a SELECT the read-only RPC takes: one statement, no write word, no alias t
    assert sql.startswith("SELECT ") and ";" not in sql and not re.search(r"\bAS\s+t\b", sql)
    assert not re.search(r"\b(insert|update|delete|drop|alter|grant|create)\b", sql, re.I)


@test("layer 2's figures are the Dashboard's own: dashboard() on the same book shows kpis.total_receivables and "
      "kpis.overdue_total_bhd equal to dashboard_receivables(), which reads two things where the payload reads many")
def _():
    book, rpc, seen = _book(1910)
    kpis = _dashboard_kpis(rpc)
    payload_reads = len(seen)
    seen.clear()
    with _Patched((digest, "exec_sql", rpc)):
        alone = rc.dashboard_receivables()
    assert alone == {"total_receivables": kpis["total_receivables"], "overdue_total_bhd": kpis["overdue_total_bhd"]}
    assert (alone["total_receivables"], alone["overdue_total_bhd"]) == (book["total"], book["overdue"])
    assert len(seen) == 2 < payload_reads, (seen, payload_reads)
    # no sale loaded yet: daily_summary, the tiles' source, has no data date and reads them as zero; so does this,
    # reading nothing else (the payload itself cannot be assembled without a sale: its sales tiles are missing)
    book, rpc, seen = _book(1911, data_date=None)
    with _Patched((digest, "exec_sql", rpc)):
        summary = digest.daily_summary()
        seen.clear()
        alone = rc.dashboard_receivables()
    assert alone == {"total_receivables": summary["total_receivables"],
                     "overdue_total_bhd": summary["overdue_receivables_bhd"]} == {"total_receivables": 0,
                                                                                 "overdue_total_bhd": 0}
    assert len(seen) == 1 and "MAX(sale_date)" in seen[0], seen


# ── the whole reconcile, on stand-ins ─────────────────────────────────────────────────────────

def _reconcile(seed: int, verified_run: int | None = None, *, drift: float = 0.0, layer1=None):
    """reconcile_check.run on stand-ins: an AR report of invented rows (made up at run time), the database's book
    through the read-only RPC stand-in (`drift`: the tile's total that much off the report), the collections agent,
    and verify_numbers.run_checks (`layer1` = what it answers; None = it must not be called). dashboard() raises
    and the RPC refuses any read but the tiles' two: layer 2 reads them alone.
    -> (exit code, what it printed, the folders run_checks was given, the folder)"""
    rng = random.Random(seed)
    ar = []
    for _ in range(3):
        row = {b: round(rng.uniform(0, 400), 3) for b in OVERDUE_BUCKETS}
        row["balance_bhd"] = round(sum(row.values()) + rng.uniform(100, 900), 3)
        ar.append(row)
    total = sum(r["balance_bhd"] for r in ar)
    overdue = sum(r[b] for r in ar for b in OVERDUE_BUCKETS)

    def rpc(sql: str):
        if "MAX(sale_date) AS d FROM v_sales" in sql:
            return [{"d": "2026-10-19"}]
        if sql == rc.RECEIVABLES_SQL:
            return [{"total": str(total * (1 + drift)), "overdue": str(overdue), "overdue_accounts": len(ar)}]
        raise AssertionError(f"layer 2 read more than the two tiles: {sql[:80]}")

    ran: list = []

    def checks(src):
        if layer1 is None:
            raise AssertionError("layer 1 ran again")
        ran.append(src)
        return layer1

    def whole_dashboard(*a, **kw):
        raise AssertionError("layer 2 built the whole dashboard")

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "Customer_summary_ageing_by_due_date1.xlsx").write_bytes(b"an AR report as exported")
        out = io.StringIO()
        with _Patched((vn, "run_checks", checks), (ingest, "read_grid", lambda f: ("grid", Path(f).name)),
                      (ingest, "parse_receivables", lambda grid, label: [dict(r) for r in ar]),
                      (agents, "collections", lambda: {"total_overdue_bhd": overdue, "count": len(ar)}),
                      (digest, "exec_sql", rpc), (reports, "dashboard", whole_dashboard)), \
                contextlib.redirect_stdout(out):
            code = rc.run(folder, verified_run=verified_run)
    return code, out.getvalue(), ran, folder


def _metrics(out: str) -> list[str]:
    """The metric names of the table reconcile prints, in order."""
    return [ln.split("  ")[0] for ln in out.splitlines() if " PASS (" in ln or " FAIL (" in ln]


@test("--verified-run: layer 1 is not run again and the output says so in one line; layer 2 still compares the two "
      "tiles and the collections agent with the AR report, and a drift is still found")
def _():
    code, out, ran, _folder = _reconcile(20, verified_run=4321)
    assert code == 0 and ran == [], "verify_numbers ran a second time"
    assert ("(layer 1 skipped: the refresh's own verify of ingest run 4321 compared every report with the DB views, "
            "and it passed)") in out.splitlines(), out
    assert _metrics(out) == ["Dashboard receivables", "Dashboard overdue >30d", "Collections agent overdue"], out
    assert out.rstrip().endswith("RECONCILED — platform numbers match the Focus reports."), out
    # the line the load's journal quotes when a reconcile needs a look carries none of the words it picks
    skip = next(ln for ln in out.splitlines() if ln.startswith("(layer 1 skipped"))
    assert not any(w in skip for w in ("FAIL", "WHY:", "MISMATCH", "ERROR")), skip
    # a tile that drifted is found all the same, said with its WHY, and the exit code is still 2
    code, out, ran, _folder = _reconcile(21, verified_run=4321, drift=0.02)
    assert code == 2 and ran == []
    assert "FAIL (2.00%)" in out and "WHY: dashboard tile drifted from AR report" in out, out
    assert out.rstrip().endswith("MISMATCHES FOUND — see WHY hints above."), out


@test("without the option: as before - layer 1 runs on the folder and its rows head the table; its drift fails the "
      "reconcile with its own WHY")
def _():
    good = [{"metric": "Sales (invented)", "report": 10.0, "db": 10.0, "diff_pct": 0.0, "passed": True}]
    code, out, ran, folder = _reconcile(22, layer1=(True, good))
    assert code == 0 and ran == [folder] and "layer 1 skipped" not in out, out
    assert _metrics(out) == ["Sales (invented)", "Dashboard receivables", "Dashboard overdue >30d",
                             "Collections agent overdue"], out
    bad = [{"metric": "Stock (invented)", "report": 10.0, "db": 12.0, "diff_pct": 20.0, "passed": False}]
    code, out, ran, folder = _reconcile(23, layer1=(False, bad))
    assert code == 2 and ran == [folder] and "WHY: report vs DB view drift — re-run scripts.refresh" in out, out


@test("the command line: the folder, then --verified-run <id> as the load passes it (either order); an id that is no "
      "whole number above 0 is refused before anything runs; a missing folder is still exit 1")
def _():
    got: list = []

    def run(src, verified_run=None):
        got.append((src, verified_run))
        return 0

    with tempfile.TemporaryDirectory() as tmp, _Patched((rc, "run", run)):
        assert rc.main([tmp, "--verified-run", "9"]) == 0          # the worker's own order (scripts/yq_load_go.py)
        assert rc.main(["--verified-run", "12", tmp]) == 0
        assert rc.main([tmp]) == 0
        for bad in (["--verified-run", "0", tmp], ["--verified-run", "-3", tmp], ["--verified-run", "nine", tmp],
                    [tmp, "--verified-run"]):
            with contextlib.redirect_stderr(io.StringIO()):
                assert _raises(lambda: rc.main(bad), SystemExit).code == 2, bad
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert rc.main([str(Path(tmp) / "absent")]) == 1
    assert got == [(Path(tmp), 9), (Path(tmp), 12), (Path(tmp), None)], got
    assert out.getvalue().startswith("ERROR: folder not found:"), out.getvalue()


@test("CI runs this suite in the ops loop")
def _():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    loop = next(line for line in ci.splitlines() if line.strip().startswith("for suite in"))
    assert re.search(r"\breconcile\b", loop), loop


@test("LAST: no request got past a stand-in")
def _():
    assert REACHED == [], REACHED


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
