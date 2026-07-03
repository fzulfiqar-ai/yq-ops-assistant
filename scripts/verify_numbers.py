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


def _find(key: str, src_dir: Path) -> str | None:
    for p in sorted(glob.glob(str(src_dir / "*.xls*"))):
        if key in os.path.basename(p).lower():
            return p
    return None


def _sql_sum(sql: str, params: list | None = None) -> float | None:
    """Single-query SUM via the read-only RPC — deterministic. The old approach paged
    PostgREST with .range() and NO ORDER BY; Postgres gives no stable order without one,
    so pages could overlap/skip and the same sum came back different between runs
    (observed: v_sales 53,446 vs 52,909 seconds apart → spurious verify FAILs)."""
    try:
        from app.db_read import exec_sql, exec_sql_params
        rows = exec_sql_params(sql, params) if params else exec_sql(sql)
        return _nn((rows or [{}])[0].get("s"))
    except Exception:  # noqa: BLE001 — RPC missing/ungranted → ordered REST fallback
        return None


def _rest_sum(view: str, col: str, order_col: str, filters=None) -> float:
    """Paginated REST fallback with an explicit ORDER BY so pages are stable."""
    c = get_client()
    rows, off = [], 0
    while True:
        q = c.table(view).select(col).order(order_col)
        for f in (filters or []):
            q = f(q)
        b = q.range(off, off + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return sum(_nn(r.get(col)) for r in rows)


def _db_sum(view: str, col: str) -> float:
    s = _sql_sum(f"SELECT COALESCE(SUM({col}),0) AS s FROM {view}")
    return s if s is not None else _rest_sum(view, col, col)


def _db_sum_between(view: str, col: str, datecol: str, dmin: str, dmax: str) -> float:
    """Sum `col` over rows whose `datecol` is in [dmin, dmax]. Scopes the check to the uploaded
    file's own date range, so an incremental (partial) day-book validates against the same span
    instead of the whole cumulative DB (which made a small top-up look like a huge 'drift')."""
    s = _sql_sum(
        f"SELECT COALESCE(SUM({col}),0) AS s FROM {view} "
        f"WHERE {datecol} >= $1::date AND {datecol} <= $2::date", [dmin, dmax])
    if s is not None:
        return s
    return _rest_sum(view, col, datecol,
                     filters=[lambda q: q.gte(datecol, dmin), lambda q: q.lte(datecol, dmax)])


def _latest_as_of(table: str) -> str | None:
    c = get_client()
    r = c.table(table).select("as_of_date").order("as_of_date", desc=True).limit(1).execute().data
    return (r or [{}])[0].get("as_of_date")


def _db_sum_eq(table: str, col: str, eqcol: str, eqval) -> float:
    """Sum `col` over rows where `eqcol` = `eqval`. Scopes a snapshot table (stock_balance) to its
    latest as_of_date so retained earlier snapshots aren't double-counted on the next upload."""
    s = _sql_sum(f"SELECT COALESCE(SUM({col}),0) AS s FROM {table} WHERE {eqcol} = $1", [str(eqval)])
    if s is not None:
        return s
    return _rest_sum(table, col, "id", filters=[lambda q: q.eq(eqcol, eqval)])


def run_checks(src_dir: Path | None = None) -> tuple[bool, list[dict]]:
    """Crosscheck DB view totals against the source Focus reports in src_dir.

    Returns (all_ok, rows) where each row = {metric, report, db, diff_pct, passed}. A missing
    report file is skipped (not failed) so a partial refresh still validates what it loaded.
    Importable so the refresh engine can gate/annotate on the result."""
    src = src_dir or NEW
    checks: list[tuple[str, float, float, float]] = []  # name, report, db, tol%

    f = _find("sales_day_book", src)
    if f:  # Sales gross — scope the DB sum to the file's own date range (partial-upload safe)
        ol = parse_order_lines(read_grid(f), "x")
        report = sum(_nn(r["gross_bhd"]) for r in ol)
        dts = sorted(str(r["line_date"])[:10] for r in ol if r.get("line_date"))
        db = (_db_sum_between("v_sales", "revenue_bhd", "sale_date", dts[0], dts[-1])
              if dts else _db_sum("v_sales", "revenue_bhd"))
        checks.append(("Sales gross BHD", report, db, 0.5))
    f = _find("customer_summary_ageing_by_due_date", src)
    if f:  # Receivables total vs AR ageing report (v_receivables already = latest snapshot)
        ar = parse_receivables(read_grid(f), "x")
        checks.append(("Receivables BHD", sum(_nn(r["balance_bhd"]) for r in ar),
                       _db_sum("v_receivables", "outstanding_bhd"), 0.5))
    f = _find("stock_balance_by_warehouse", src)
    if f:  # Stock selling-value — scope the DB sum to the latest snapshot (don't sum retained history)
        sb = parse_stock_balance(read_grid(f), "x")
        report = sum(_nn(r["total_value_bhd"]) for r in sb)
        aod = _latest_as_of("stock_balance")
        db = (_db_sum_eq("stock_balance", "total_value_bhd", "as_of_date", aod)
              if aod else _db_sum("stock_balance", "total_value_bhd"))
        checks.append(("Stock value BHD", report, db, 0.5))

    # Channel split should reconcile to total sales gross (DB-only — always run)
    c = get_client()
    ch = c.table("v_sales_by_channel").select("revenue_bhd").execute().data or []
    checks.append(("Channel = sales gross", _db_sum("v_sales", "revenue_bhd"),
                   sum(_nn(r["revenue_bhd"]) for r in ch), 0.5))

    rows, ok = [], True
    for name, report, db, tol in checks:
        diff_pct = abs(report - db) / report * 100 if report else (0 if db == 0 else 100)
        passed = diff_pct <= tol
        ok = ok and passed
        rows.append({"metric": name, "report": report, "db": db, "diff_pct": diff_pct, "passed": passed})
    return ok, rows


def main() -> int:
    # Optional folder argument, same as scripts.refresh:  python -m scripts.verify_numbers "Planning 030726"
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if src is not None and not src.is_absolute():
        src = ROOT / src
    ok, rows = run_checks(src)
    print("=" * 64)
    print(f"{'METRIC':24} {'REPORT':>14} {'DB':>14}  RESULT")
    print("-" * 64)
    for r in rows:
        print(f"{r['metric']:24} {r['report']:>14,.2f} {r['db']:>14,.2f}  "
              f"{'PASS' if r['passed'] else 'FAIL'} ({r['diff_pct']:.2f}%)")
    print("=" * 64)
    print("ALL CHECKS PASS" if ok else "SOME CHECKS FAILED - investigate before trusting the dashboard.")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
