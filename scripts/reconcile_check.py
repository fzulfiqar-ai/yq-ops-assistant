"""End-to-end reconciliation: Focus reports → DB views → dashboard API payload.

    python -m scripts.reconcile_check ["Planning 030726"]

Layer 1 (report vs DB views)      — scripts.verify_numbers.run_checks (sales / AR / stock / channel).
Layer 2 (DB vs dashboard payload) — the numbers the OWNER actually sees:
    • Receivables tile total   == AR report balance total
    • Overdue >30d on the tile == AR report buckets 31-60 … >210 (same basis as collections agent)
    • Collections agent total  == the same figure (tile and agent can never disagree)
Prints a mismatch table with a WHY hint per failure; exit 0 = everything reconciles.
"""
from __future__ import annotations

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


def run(src_dir: Path) -> int:
    from scripts.ingest import parse_receivables, read_grid
    from scripts.verify_numbers import _find, run_checks

    rows: list[dict] = []

    # Layer 1 — report vs DB views
    ok1, l1 = run_checks(src_dir)
    for r in l1:
        rows.append({**r, "why": "report vs DB view drift — re-run scripts.refresh" if not r["passed"] else ""})

    # Layer 2 — dashboard payload vs the AR report
    f = _find("customer_summary_ageing_by_due_date", src_dir)
    if f:
        ar = parse_receivables(read_grid(f), "x")
        rpt_total = sum(_nn(r.get("balance_bhd")) for r in ar)
        rpt_overdue = sum(_nn(r.get(b)) for r in ar for b in BUCKETS_OVERDUE)

        from app.agents import collections
        from app.reports import dashboard
        k = dashboard()["kpis"]
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


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Planning 030726"
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print(f"ERROR: folder not found: {src}")
        return 1
    return run(src)


if __name__ == "__main__":
    raise SystemExit(main())
