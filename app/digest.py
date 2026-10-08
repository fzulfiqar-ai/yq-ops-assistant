"""Phase 2.5 — Proactive alert and daily digest data layer.

Called by:
  - GET /digest/daily   → daily ops summary
  - GET /digest/alerts  → low-stock + overdue + negative-margin alerts
  - scripts/send_digest.py (standalone email sender, no Railway needed)

All time windows anchor to the DATA's latest date (MAX(sale_date)), never the system
clock — the server can run a day ahead of the last loaded Focus export, which would
otherwise make "today" read zero. Revenue is reported both Gross (VAT-incl) and ex-VAT.
"""
from __future__ import annotations

import datetime

from app.db_read import exec_sql


def _data_date() -> str | None:
    rows = exec_sql("SELECT MAX(sale_date) AS d FROM v_sales LIMIT 1")
    return (rows or [{}])[0].get("d")


def daily_summary() -> dict:
    """Revenue (gross + ex-VAT) + orders for the latest day, yesterday and MTD."""
    data_date = _data_date()
    if not data_date:
        return {"data_date": None, "rev_today": 0, "orders_today": 0, "rev_mtd": 0,
                "orders_mtd": 0, "rev_prev_month": 0, "rev_prev_month_mtd": 0,
                "prev_month_through": None, "top_customers": [], "total_receivables": 0,
                "overdue_receivables_bhd": 0, "current_receivables_bhd": 0, "overdue_accounts": 0}
    d = datetime.date.fromisoformat(str(data_date)[:10])
    yday = (d - datetime.timedelta(days=1)).isoformat()
    month_start = d.replace(day=1).isoformat()
    prev_end = d.replace(day=1) - datetime.timedelta(days=1)
    prev_start = prev_end.replace(day=1).isoformat()
    # Same slice of the PREVIOUS month, for an honest like-for-like comparison: MTD covers only
    # d.day days, so comparing it against the whole previous month always reads as a collapse
    # early in the month and "recovers" by the 30th purely as an artefact. Clamp to prev_end so
    # 31-Mar vs a 28-day February does not overshoot.
    prev_same_day = min(prev_end.replace(day=1) + datetime.timedelta(days=d.day - 1), prev_end)

    mtd = exec_sql(
        "SELECT COALESCE(SUM(revenue_bhd),0) AS rev, COALESCE(SUM(net_bhd),0) AS net, "
        f"COUNT(DISTINCT invoice_no) AS orders FROM v_sales WHERE sale_date >= DATE '{month_start}' LIMIT 1"
    )
    today = exec_sql(
        "SELECT COALESCE(SUM(revenue_bhd),0) AS rev, COALESCE(SUM(net_bhd),0) AS net, "
        f"COUNT(DISTINCT invoice_no) AS orders FROM v_sales WHERE sale_date = DATE '{d.isoformat()}' LIMIT 1"
    )
    yesterday = exec_sql(
        "SELECT COALESCE(SUM(revenue_bhd),0) AS rev, "
        f"COUNT(DISTINCT invoice_no) AS orders FROM v_sales WHERE sale_date = DATE '{yday}' LIMIT 1"
    )
    prev = exec_sql(
        "SELECT COALESCE(SUM(revenue_bhd),0) AS rev FROM v_sales "
        f"WHERE sale_date >= DATE '{prev_start}' AND sale_date <= DATE '{prev_end.isoformat()}' LIMIT 1"
    )
    # Previous month truncated to the same day-of-month as the current data date.
    prev_mtd = exec_sql(
        "SELECT COALESCE(SUM(revenue_bhd),0) AS rev FROM v_sales "
        f"WHERE sale_date >= DATE '{prev_start}' AND sale_date <= DATE '{prev_same_day.isoformat()}' LIMIT 1"
    )
    # Top customers this month — exclude the walk-in "Cash Customer" bucket (it's a
    # channel, not an account, and would otherwise dominate every list).
    # v_top_customers aggregates over ALL loaded history with no date filter, so the old
    # query here (WHERE last_order_date >= month_start) only chose WHICH customers appear —
    # every BHD figure and the ranking itself stayed lifetime. A customer buying 1,000/month
    # for a year showed as ~12,000 under a card whose empty state says "this month".
    # Aggregate the window itself instead.
    top = exec_sql(
        "SELECT COALESCE(customer_name, customer_account) AS customer_name, "
        "COALESCE(SUM(revenue_bhd),0) AS total_revenue_bhd, "
        "COUNT(DISTINCT invoice_no) AS order_count FROM v_sales "
        f"WHERE sale_date >= DATE '{month_start}' AND NOT is_cash_customer "
        "GROUP BY 1 HAVING COALESCE(SUM(revenue_bhd),0) > 0 "
        "ORDER BY 2 DESC NULLS LAST LIMIT 5"
    )
    # Total book + past-due split on the SAME bucket basis as the collections agent,
    # so the dashboard tile and the agent can never disagree again.
    recv = exec_sql(
        "SELECT COALESCE(SUM(outstanding_bhd),0) AS total, "
        "COALESCE(SUM(overdue_bhd),0) AS overdue, "
        "COUNT(*) FILTER (WHERE overdue_bhd > 0) AS overdue_accounts "
        "FROM v_receivables LIMIT 1"
    )

    g = lambda rs, k, default=0: (rs or [{}])[0].get(k, default)  # noqa: E731
    return {
        "data_date": str(data_date)[:10],
        "rev_today": float(g(today, "rev")),
        "net_today": float(g(today, "net")),
        "orders_today": int(g(today, "orders")),
        "rev_yesterday": float(g(yesterday, "rev")),
        "orders_yesterday": int(g(yesterday, "orders")),
        "rev_mtd": float(g(mtd, "rev")),
        "net_mtd": float(g(mtd, "net")),
        "orders_mtd": int(g(mtd, "orders")),
        "rev_prev_month": float(g(prev, "rev")),
        "rev_prev_month_mtd": float(g(prev_mtd, "rev")),
        "prev_month_through": prev_same_day.isoformat(),
        "top_customers": top or [],
        "total_receivables": float(g(recv, "total")),
        "overdue_receivables_bhd": float(g(recv, "overdue")),
        "current_receivables_bhd": float(g(recv, "total")) - float(g(recv, "overdue")),
        "overdue_accounts": int(g(recv, "overdue_accounts")),
    }


def _in_division(division: str | None) -> str:
    """The low-stock reads' division filter: '' = every division (the email digests' own basis, as it
    always was), a division name = that division of v_stock_health only, and of it only the items
    the division SOLD in the 90 days the cover is measured on.

    The portal Dashboard passes 'Accessories' (owner, 7-Oct-2026: that page is Mobile Accessories
    only). Batelco SIM stock is not loaded, so every SIM pack sold in the last 90 days reads "out of
    stock" and rode in the count the owner reorders on. The name is a fixed word from our own code,
    never user input; anything that is not plain letters is refused.

    Sold = a paid line in v_sales, the page's own basis (giveaways out). v_stock_health counts every
    unit that left as sold, the free ones too, and files an item no rule places under Accessories:
    a handset that was only ever given away read "out of stock, reorder" at the head of the list.
    A row of v_stock_health with sales carries the name its sales lines carry, the one v_sales has,
    so the two match as they are."""
    if division is None:
        return ""
    if not (isinstance(division, str) and division.isalpha()):
        raise ValueError(f"Unknown division {division!r}.")
    return (f" AND division = '{division}' AND item_name IN (SELECT item_name FROM v_sales "
            f"WHERE division = '{division}' AND NOT is_giveaway "
            "AND sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90)")


def low_stock_items(division: str | None = None) -> list[dict]:
    """Velocity-aware low stock (matches the Monthly workbook): < 30 days of cover,
    including fast movers already out of stock. `division`: see _in_division (None = every division)."""
    return exec_sql(
        "SELECT item_name, current_stock, sold_90d, days_cover, suggested_reorder_qty, status "
        f"FROM v_stock_health WHERE status IN ('urgent_out_of_stock','low_stock'){_in_division(division)} "
        "ORDER BY days_cover ASC NULLS FIRST LIMIT 60"
    )


def low_stock_total(division: str | None = None, q=None) -> int:
    """UNCAPPED count of low/out-of-stock items.

    low_stock_items() is display-capped at 60, and taking len() of it pinned the dashboard
    KPI, the footer strip and the "Reorder N items" action at exactly 60 once the real count
    crossed it — a silent ceiling on the number that triggers reordering. overdue_count was
    already fixed this way (see the note in app/reports.py); this closes the same hole here.
    `division`: see _in_division (None = every division). `q`: the caller's own read-only reader
    (the Inventory report passes its own), else this module's.
    """
    r = (q or exec_sql)(
        "SELECT COUNT(*) AS n FROM v_stock_health "
        f"WHERE status IN ('urgent_out_of_stock','low_stock'){_in_division(division)} LIMIT 1"
    )
    return int((r or [{}])[0].get("n") or 0)


def overdue_receivables() -> list[dict]:
    """Trade debtors with an amount past due (from the AR ageing buckets)."""
    return exec_sql(
        "SELECT account, outstanding_bhd, overdue_bhd, over_90_bhd, group_name "
        "FROM v_receivables WHERE overdue_bhd > 0 ORDER BY overdue_bhd DESC LIMIT 30"
    )


def negative_margins(division: str | None = None) -> list[dict]:
    """Items selling below cost on the COMPUTED ex-VAT margin (app/margin_truth.py): the report's
    own GP % is not a percentage and its GP loses the sign on loss items, so this list was always
    empty. gp_margin_pct on each row = the computed ex-VAT margin %.
    `division` 'Accessories' (the portal Dashboard) leaves out the report's items filed under another
    division (margin_truth.ACCESSORIES_ONLY); None is the whole report, as it always was."""
    from app.margin_truth import ACCESSORIES_ONLY, below_cost_rows
    return below_cost_rows(20, ACCESSORIES_ONLY if division == "Accessories" else "")


def all_alerts(division: str | None = None) -> dict:
    """Combined alert payload for /digest/alerts endpoint.

    `division` narrows the LOW-STOCK list with its count, and the below-cost list, to one division
    (the portal Dashboard passes 'Accessories'). None — the endpoint, the email digest, the Streamlit
    page — is every division, as before. Overdue receivables are never narrowed: Focus keeps one
    balance per customer."""
    low = low_stock_items(division)
    overdue = overdue_receivables()
    neg = negative_margins(division)
    return {
        "low_stock": low,
        # Uncapped SQL count, NOT len(low) — the list above stops at 60.
        "low_stock_count": low_stock_total(division),
        "overdue_receivables": overdue,
        "overdue_count": len(overdue),
        "overdue_total_bhd": sum(float(r.get("overdue_bhd") or 0) for r in overdue),
        "negative_margins": neg,
        "negative_margin_count": len(neg),
        "has_alerts": bool(low or overdue or neg),
    }
