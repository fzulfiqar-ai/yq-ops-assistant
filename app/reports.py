"""Shared, read-only report queries.

Single source of truth consumed by BOTH the Streamlit dashboard and the React
`GET /report/{key}` API, so the two never drift. All SQL targets the curated
semantic views only.
"""
from __future__ import annotations

from app.ai import exec_sql
from app.digest import all_alerts, daily_summary


def revenue_trend(months: int = 6) -> list[dict]:
    rows = exec_sql(
        "SELECT period_month, net_revenue_bhd, order_count, total_qty "
        f"FROM v_sales_by_period ORDER BY period_month DESC LIMIT {int(months)}"
    )
    return list(reversed(rows or []))  # chronological for charting


def data_as_of() -> str | None:
    """Latest sale date in the warehouse — powers the 'Data as of' trust banner."""
    rows = exec_sql("SELECT MAX(sale_date) AS d FROM v_sales LIMIT 1")
    return (rows or [{}])[0].get("d")


def dashboard() -> dict:
    s = daily_summary()
    a = all_alerts()
    return {
        "data_as_of": data_as_of(),
        "kpis": {
            "rev_mtd": s["rev_mtd"],
            "rev_prev_month": s["rev_prev_month"],
            "orders_mtd": s["orders_mtd"],
            "rev_today": s["rev_today"],
            "orders_today": s["orders_today"],
            "total_receivables": s["total_receivables"],
            "low_stock_count": a["low_stock_count"],
            "overdue_count": a["overdue_count"],
            "overdue_total_bhd": a["overdue_total_bhd"],
        },
        "top_customers": s["top_customers"],
        "revenue_trend": revenue_trend(6),
        "alerts": a,
    }


def inventory() -> dict:
    rows = exec_sql(
        "SELECT item_name, warehouse_name, balance_qty, avg_rate_bhd, category_name, as_of_date "
        "FROM v_current_stock ORDER BY balance_qty ASC LIMIT 200"
    )
    low = [r for r in rows if (r.get("balance_qty") or 0) <= 10]
    return {"rows": rows, "count": len(rows), "low_stock_count": len(low)}


def sales() -> dict:
    return {
        "trend": revenue_trend(12),
        "top_customers": exec_sql(
            "SELECT customer_name, total_revenue_bhd, order_count, last_order_date "
            "FROM v_top_customers ORDER BY total_revenue_bhd DESC LIMIT 50"
        ),
    }


def margins() -> dict:
    rows = exec_sql(
        "SELECT item_name, category_name, gp_margin_pct, np_margin_pct, cogs_bhd, list_price_bhd "
        "FROM v_product_margin WHERE gp_margin_pct IS NOT NULL ORDER BY gp_margin_pct ASC LIMIT 200"
    )
    neg = [r for r in rows if (r.get("gp_margin_pct") or 0) < 0]
    return {"rows": rows, "count": len(rows), "negative_count": len(neg)}


def receivables() -> dict:
    rows = exec_sql(
        "SELECT account, outstanding_bhd, days_outstanding, salesman, last_entry_date "
        "FROM v_receivables ORDER BY outstanding_bhd DESC LIMIT 200"
    )
    total = sum(float(r.get("outstanding_bhd") or 0) for r in rows)
    overdue = [r for r in rows if (r.get("days_outstanding") or 0) >= 30]
    return {"rows": rows, "count": len(rows), "total": total, "overdue_count": len(overdue)}


REPORTS = {
    "dashboard": dashboard,
    "inventory": inventory,
    "sales": sales,
    "margins": margins,
    "receivables": receivables,
}

# report key -> the feature a member must have to read it
REPORT_FEATURE = {
    "dashboard": "Dashboard",
    "inventory": "Inventory",
    "sales": "Sales",
    "margins": "Margins",
    "receivables": "Receivables",
}
