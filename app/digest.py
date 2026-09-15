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


def low_stock_items() -> list[dict]:
    """Velocity-aware low stock (matches the Monthly workbook): < 30 days of cover,
    including fast movers already out of stock."""
    return exec_sql(
        "SELECT item_name, current_stock, sold_90d, days_cover, suggested_reorder_qty, status "
        "FROM v_stock_health WHERE status IN ('urgent_out_of_stock','low_stock') "
        "ORDER BY days_cover ASC NULLS FIRST LIMIT 60"
    )


def low_stock_total() -> int:
    """UNCAPPED count of low/out-of-stock items.

    low_stock_items() is display-capped at 60, and taking len() of it pinned the dashboard
    KPI, the footer strip and the "Reorder N items" action at exactly 60 once the real count
    crossed it — a silent ceiling on the number that triggers reordering. overdue_count was
    already fixed this way (see the note in app/reports.py); this closes the same hole here.
    """
    r = exec_sql(
        "SELECT COUNT(*) AS n FROM v_stock_health "
        "WHERE status IN ('urgent_out_of_stock','low_stock') LIMIT 1"
    )
    return int((r or [{}])[0].get("n") or 0)


def overdue_receivables() -> list[dict]:
    """Trade debtors with an amount past due (from the AR ageing buckets)."""
    return exec_sql(
        "SELECT account, outstanding_bhd, overdue_bhd, over_90_bhd, group_name "
        "FROM v_receivables WHERE overdue_bhd > 0 ORDER BY overdue_bhd DESC LIMIT 30"
    )


def negative_margins() -> list[dict]:
    """Items selling below cost — Focus's own gross-margin %, NOT price-vs-total-COGS."""
    return exec_sql(
        "SELECT item_name, gp_margin_pct, np_margin_pct, cogs_bhd, list_price_bhd, category_name "
        "FROM v_product_margin WHERE gp_margin_pct < 0 ORDER BY gp_margin_pct ASC LIMIT 20"
    )


def all_alerts() -> dict:
    """Combined alert payload for /digest/alerts endpoint."""
    low = low_stock_items()
    overdue = overdue_receivables()
    neg = negative_margins()
    return {
        "low_stock": low,
        # Uncapped SQL count, NOT len(low) — the list above stops at 60.
        "low_stock_count": low_stock_total(),
        "overdue_receivables": overdue,
        "overdue_count": len(overdue),
        "overdue_total_bhd": sum(float(r.get("overdue_bhd") or 0) for r in overdue),
        "negative_margins": neg,
        "negative_margin_count": len(neg),
        "has_alerts": bool(low or overdue or neg),
    }
