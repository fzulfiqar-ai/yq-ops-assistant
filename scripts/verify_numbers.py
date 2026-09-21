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
    parse_order_lines, parse_orders, parse_receivables, parse_stock, parse_stock_balance, read_grid,
)

DATA_DIR = ROOT / "business_data"
CLEAN_DIR = ROOT / "data" / "clean"

# Report types that have NO cross-check rule here. They still load (data/clean/<table>.csv), so a
# refresh made only of these must not be reported as a FAIL -- it is "loaded, nothing to compare".
_UNCHECKED = {
    "product_profitability": "Product_Profitability_Report",
    "selling_prices": "price book",
    "ledger_entries": "Ledger",
}


def _latest_source_dir() -> Path:
    """The most recent dated Focus drop, e.g. business_data/Focus ERP 2026-09-14.

    The default used to be hard-pinned to "Focus ERP Updated Reports" (a June export). Running
    this with no argument therefore crosschecked TODAY's database against MONTHS-old reports and
    reported FAIL on receivables and stock -- a scary red result caused purely by a stale default.
    Prefer the newest 'Focus ERP <date>' folder, which is what an upload actually loaded.
    """
    dated = sorted(
        (p for p in DATA_DIR.glob("Focus ERP 20*") if p.is_dir()),
        key=lambda p: p.name,
    )
    if dated:
        return dated[-1]
    return DATA_DIR / "Focus ERP Updated Reports"


NEW = _latest_source_dir()


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
    f = _find("summary_sales_register", src)
    if f:  # Invoice headers -- gross per invoice, scoped to the file's own date range
        od = parse_orders(read_grid(f), "x")
        report = sum(_nn(r["gross_bhd"]) for r in od)
        dts = sorted(str(r["order_date"])[:10] for r in od if r.get("order_date"))
        db = (_db_sum_between("orders", "gross_bhd", "order_date", dts[0], dts[-1])
              if dts else _db_sum("orders", "gross_bhd"))
        checks.append(("Invoice headers gross BHD", report, db, 0.5))
    f = _find("stock_ledger", src)
    if f:  # Stock movements -- units received + issued inside the file's date range. This is the
        # check that was missing on 21-Sep-2026: a Stock_ledger-only upload had nothing to compare
        # against and was reported as "NO SOURCE REPORTS FOUND" even though it loaded cleanly.
        sm = parse_stock(read_grid(f), "x")
        report = sum(_nn(r["received_qty"]) + _nn(r["issued_qty"]) for r in sm)
        dts = sorted(str(r["move_date"])[:10] for r in sm if r.get("move_date"))
        if dts:
            db = (_db_sum_between("stock_movements", "received_qty", "move_date", dts[0], dts[-1])
                  + _db_sum_between("stock_movements", "issued_qty", "move_date", dts[0], dts[-1]))
        else:
            db = _db_sum("stock_movements", "received_qty") + _db_sum("stock_movements", "issued_qty")
        checks.append(("Stock movements units", report, db, 0.5))
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

    # How many checks above are backed by an actual source FILE. Everything after this point
    # is DB-vs-DB and cannot detect a bad load, so this is the number that decides whether the
    # run proved anything at all.
    source_backed = len(checks)

    # Channel split vs total sales gross. NOTE: this is DB-vs-DB and very nearly a tautology —
    # v_sales_by_channel is `select channel, sum(revenue_bhd) from v_sales group by channel`
    # with no WHERE and no NULL branch in the channel CASE, so summing its groups is the same
    # number by construction. It is kept only as a guard against the view being redefined with
    # a filter or a channel that can go NULL. It must never be counted as evidence of a good
    # load, which is why it is added AFTER source_backed is taken.
    c = get_client()
    ch = c.table("v_sales_by_channel").select("revenue_bhd").execute().data or []
    checks.append(("Channel = sales gross (DB-only)", _db_sum("v_sales", "revenue_bhd"),
                   sum(_nn(r["revenue_bhd"]) for r in ch), 0.5))

    rows, ok = [], True
    for name, report, db, tol in checks:
        diff_pct = abs(report - db) / report * 100 if report else (0 if db == 0 else 100)
        passed = diff_pct <= tol
        ok = ok and passed
        rows.append({"metric": name, "report": report, "db": db, "diff_pct": diff_pct, "passed": passed})

    # No source file matched a cross-check rule. Reporting a bare PASS here would be the worst
    # outcome (a green light meaning "I found nothing to compare"), but a bare FAIL is wrong too
    # when the upload was made only of report types that have no rule (price books, profitability):
    # those loaded fine. So: PASS with an explicit "loaded, nothing to compare" line when the
    # ingest step produced rows for such a report; FAIL only when nothing at all was loaded.
    if source_backed == 0:
        loaded = _unchecked_loaded()
        if loaded:
            rows.append({"metric": "Loaded, no totals to cross-check: " + ", ".join(loaded),
                         "report": 0.0, "db": 0.0, "diff_pct": 0.0, "passed": True,
                         "note": "These report types have no verification rule; row counts are shown above."})
        else:
            ok = False
            rows.append({"metric": "NO SOURCE REPORTS FOUND", "report": 0.0, "db": 0.0,
                         "diff_pct": 100.0, "passed": False,
                         "note": "None of the uploaded files produced rows. Check the file names and contents."})
    return ok, rows


def _unchecked_loaded() -> list[str]:
    """Human labels of unchecked report types whose ingest CSV holds at least one row."""
    out: list[str] = []
    for table, label in _UNCHECKED.items():
        p = CLEAN_DIR / f"{table}.csv"
        try:
            if p.exists() and sum(1 for _ in p.open(encoding="utf-8")) > 1:
                out.append(label)
        except Exception:  # noqa: BLE001
            continue
    return out


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
