"""End-to-end reconciliation: Focus reports → DB views → dashboard API payload.

    python -m scripts.reconcile_check ["Planning 030726"] [--verified-run <ingest_runs id>]

Layer 1 (report vs DB views)      — scripts.verify_numbers.run_checks (sales / AR / stock / channel).
    --verified-run: not run again. The load (scripts/yq_load_go.py) passes the id of its refresh's own
    run record only when that refresh ran this very comparison on the data now loaded and it passed;
    the output says so.
Layer 2 (DB vs dashboard payload) — the numbers the OWNER actually sees:
    • Receivables tile total   == AR report balance total
    • Overdue >30d on the tile == AR report buckets 31-60 … >210 (same basis as collections agent)
    • Collections agent total  == the same figure (tile and agent can never disagree)
    The two tiles are read alone (dashboard_receivables: the two reads dashboard() takes them from,
    without the rest of its payload).
Prints a mismatch table with a WHY hint per failure; exit 0 = everything reconciles.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TOL_PCT = 0.5

BUCKETS_OVERDUE = ("bucket_31_60", "bucket_61_90", "bucket_91_120", "bucket_121_150",
                   "bucket_151_180", "bucket_181_210", "bucket_over_210")


def _nn(v) -> float:
    try:
        f = float(v) if v not in (None, "") else 0.0
        return 0.0 if math.isnan(f) else f
    except Exception:  # noqa: BLE001
        return 0.0


# dashboard()'s kpis.total_receivables and kpis.overdue_total_bhd come from app.digest.daily_summary: its
# data date, then this one sum over v_receivables (tests/test_ops_reconcile.py pins it to daily_summary's
# own SQL, byte for byte).
RECEIVABLES_SQL = ("SELECT COALESCE(SUM(outstanding_bhd),0) AS total, "
                   "COALESCE(SUM(overdue_bhd),0) AS overdue, "
                   "COUNT(*) FILTER (WHERE overdue_bhd > 0) AS overdue_accounts "
                   "FROM v_receivables LIMIT 1")


def dashboard_receivables() -> dict:
    """The Dashboard's two receivables tiles alone, read as daily_summary reads them, through the digest's
    own reader: zero while no sale is loaded (it has no data date then), else the one sum. Two reads where
    the whole payload takes about seventy."""
    from app import digest
    if not digest._data_date():
        return {"total_receivables": 0, "overdue_total_bhd": 0}
    row = (digest.exec_sql(RECEIVABLES_SQL) or [{}])[0]
    return {"total_receivables": float(row.get("total", 0)), "overdue_total_bhd": float(row.get("overdue", 0))}


def run(src_dir: Path, verified_run: int | None = None) -> int:
    from scripts.ingest import parse_receivables, read_grid
    from scripts.verify_numbers import _find, run_checks

    rows: list[dict] = []

    # Layer 1 — report vs DB views. The load's own refresh ran this very comparison on the data now
    # loaded (scripts/refresh.py, step 4): when it passed, the load says so and it is not run twice.
    if verified_run is None:
        ok1, l1 = run_checks(src_dir)
        for r in l1:
            rows.append({**r, "why": "report vs DB view drift — re-run scripts.refresh" if not r["passed"] else ""})
    else:
        print(f"(layer 1 skipped: the refresh's own verify of ingest run {verified_run} compared every report "
              "with the DB views, and it passed)")

    # Layer 2 — dashboard payload vs the AR report
    f = _find("customer_summary_ageing_by_due_date", src_dir)
    if f:
        ar = parse_receivables(read_grid(f), "x")
        rpt_total = sum(_nn(r.get("balance_bhd")) for r in ar)
        rpt_overdue = sum(_nn(r.get(b)) for r in ar for b in BUCKETS_OVERDUE)

        from app.agents import collections
        k = dashboard_receivables()       # the Dashboard's two tiles, read alone
        col = collections()

        def check(metric: str, report: float, db: float, why: str) -> None:
            diff = abs(report - db) / report * 100 if report else (0 if db == 0 else 100)
            rows.append({"metric": metric, "report": report, "db": db,
                         "diff_pct": diff, "passed": diff <= TOL_PCT, "why": "" if diff <= TOL_PCT else why})

        check("Dashboard receivables", rpt_total, _nn(k.get("total_receivables")),
              "dashboard tile drifted from AR report — check v_receivables snapshot date")
        check("Dashboard overdue >30d", rpt_overdue, _nn(k.get("overdue_total_bhd")),
              "tile overdue must equal report buckets 31-60…>210")
        check("Collections agent overdue", rpt_overdue, _nn(col.get("total_overdue_bhd")),
              "agent must SUM whole book, not its display list")
    else:
        print("(no AR ageing report in folder — layer 2 skipped)")

    ok = all(r["passed"] for r in rows)
    print("=" * 78)
    print(f"{'METRIC':28} {'REPORT':>14} {'DB/APP':>14}  RESULT")
    print("-" * 78)
    for r in rows:
        print(f"{r['metric']:28} {r['report']:>14,.2f} {r['db']:>14,.2f}  "
              f"{'PASS' if r['passed'] else 'FAIL'} ({r['diff_pct']:.2f}%)"
              + (f"\n{'':28}   WHY: {r['why']}" if r.get("why") else ""))
    print("=" * 78)
    print("RECONCILED — platform numbers match the Focus reports." if ok
          else "MISMATCHES FOUND — see WHY hints above.")
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.reconcile_check",
                                 description="Focus reports vs the DB views and the dashboard's receivables tiles.")
    ap.add_argument("folder", nargs="?", default=str(ROOT / "business_data" / "Planning 030726"),
                    help="the folder of Focus reports (absolute, or under the project folder)")
    ap.add_argument("--verified-run", type=int, metavar="ID",
                    help="the ingest_runs id of a refresh whose own verify of this folder passed: layer 1 is "
                         "not run again")
    a = ap.parse_args(argv)
    if a.verified_run is not None and a.verified_run <= 0:
        ap.error("--verified-run takes an ingest_runs id (a whole number above 0)")
    src = Path(a.folder)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print(f"ERROR: folder not found: {src}")
        return 1
    return run(src, verified_run=a.verified_run)


if __name__ == "__main__":
    raise SystemExit(main())
