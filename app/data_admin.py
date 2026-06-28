"""Admin data repair — remove a report's rows for a date (or the undated junk a bad export leaves),
so a wrong/partial upload can be deleted and re-uploaded cleanly.

Every Focus daily report maps to ONE fact/snapshot table keyed by a date column. Purge is scoped to
that one table + date range (or the null-date rows), never an unbounded delete. Admin-only, logged,
and followed by a cache flush so the dashboard recomputes. Re-uploading the correct file then upserts
on the natural key — no duplicates.
"""
from __future__ import annotations

from app.database import get_client

# report key (matches the Data page + scripts.ingest.classify) -> (table, date column, label)
PURGE_TARGETS: dict[str, tuple[str, str, str]] = {
    "sales_day_book": ("order_lines", "line_date", "Sales — line items (day book)"),
    "summary_sales_register": ("orders", "order_date", "Sales — header (register)"),
    "stock_ledger": ("stock_movements", "move_date", "Stock ledger (movements)"),
    "ledger": ("ledger_entries", "entry_date", "Accounts ledger"),
    "stock_balance_by_warehouse": ("stock_balance", "as_of_date", "Stock balance (snapshot)"),
    "customer_summary_ageing_by_due_date": ("ar_ageing", "as_of_date", "Receivables ageing (snapshot)"),
    "product_profitability": ("product_profitability", "report_date", "Profitability (snapshot)"),
}


def targets() -> list[dict]:
    """The purge-able reports, for the UI dropdown."""
    return [{"key": k, "table": t, "date_col": c, "label": lbl}
            for k, (t, c, lbl) in PURGE_TARGETS.items()]


def purge_by_date(report: str, date_from: str | None = None,
                  date_to: str | None = None, blanks: bool = False) -> int:
    """Delete `report`'s rows for [date_from, date_to] (single day if date_to omitted), or the rows
    with a NULL date when `blanks` (the junk a dateless/partial export leaves). Returns rows deleted."""
    if report not in PURGE_TARGETS:
        raise ValueError(f"Unknown report '{report}'.")
    table, col, _ = PURGE_TARGETS[report]
    c = get_client()
    q = c.table(table).delete()
    if blanks:
        q = q.is_(col, "null")
    elif date_from:
        q = q.gte(col, date_from).lte(col, date_to or date_from)
    else:
        raise ValueError("Pick a date, or choose the blank/no-date rows.")
    res = q.execute()
    return len(res.data or [])
