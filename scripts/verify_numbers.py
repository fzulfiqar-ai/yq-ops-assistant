"""Crosscheck the live DB metrics against the source Focus reports.

A regression guard for accuracy: each check parses the authoritative Focus export and
asserts the DB view total matches within tolerance. Run after any ingest.

  python -m scripts.verify_numbers
"""
from __future__ import annotations

import glob
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

from app.database import get_client  # noqa: E402
from scripts.ingest import (  # noqa: E402
    parse_order_lines, parse_receivables, parse_stock_balance, read_grid,
)

NEW = ROOT / "Focus ERP Updated Reports"


def _nn(v) -> float:
    try:
        f = float(v) if v not in (None, "") else 0.0
        return 0.0 if math.isnan(f) else f
    except Exception:
        return 0.0


def _find(key: str) -> str | None:
    for p in sorted(glob.glob(str(NEW / "*.xls*"))):
        if key in os.path.basename(p).lower():
            return p
    return None


def _db_sum(view: str, col: str) -> float:
    c = get_client()
    rows, off = [], 0
    while True:
        b = c.table(view).select(col).range(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return sum(_nn(r.get(col)) for r in rows)


def main() -> int:
    checks: list[tuple[str, float, float, float]] = []  # name, report, db, tol%

    # Sales gross (day-book line gross) vs DB v_sales.revenue_bhd
    ol = parse_order_lines(read_grid(_find("sales_day_book")), "x")
    checks.append(("Sales gross BHD", sum(_nn(r["gross_bhd"]) for r in ol),
                   _db_sum("v_sales", "revenue_bhd"), 0.5))

    # Receivables total vs AR ageing report
    ar = parse_receivables(read_grid(_find("customer_summary_ageing_by_due_date")), "x")
    checks.append(("Receivables BHD", sum(_nn(r["balance_bhd"]) for r in ar),
                   _db_sum("v_receivables", "outstanding_bhd"), 0.5))

    # Stock selling-value vs Stock_balance report
    sb = parse_stock_balance(read_grid(_find("stock_balance_by_warehouse")), "x")
    checks.append(("Stock value BHD", sum(_nn(r["total_value_bhd"]) for r in sb),
                   _db_sum("stock_balance", "total_value_bhd"), 0.5))

    # Channel split should reconcile to total sales gross
    c = get_client()
    ch = c.table("v_sales_by_channel").select("revenue_bhd").execute().data or []
    checks.append(("Channel = sales gross", _db_sum("v_sales", "revenue_bhd"),
                   sum(_nn(r["revenue_bhd"]) for r in ch), 0.5))

    print("=" * 64)
    print(f"{'METRIC':24} {'REPORT':>14} {'DB':>14}  RESULT")
    print("-" * 64)
    ok = True
    for name, report, db, tol in checks:
        diff_pct = abs(report - db) / report * 100 if report else (0 if db == 0 else 100)
        passed = diff_pct <= tol
        ok = ok and passed
        print(f"{name:24} {report:>14,.2f} {db:>14,.2f}  {'PASS' if passed else 'FAIL'} ({diff_pct:.2f}%)")
    print("=" * 64)
    print("ALL CHECKS PASS" if ok else "SOME CHECKS FAILED - investigate before trusting the dashboard.")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
