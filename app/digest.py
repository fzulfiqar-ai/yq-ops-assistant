"""Phase 2.5 — Proactive alert and daily digest data layer.

Called by:
  - GET /digest/daily   → daily ops summary
  - GET /digest/alerts  → low-stock + overdue + negative-margin alerts
  - scripts/send_digest.py (standalone email sender, no Railway needed)
"""
from __future__ import annotations

from app.ai import exec_sql


def daily_summary() -> dict:
    """Revenue + orders today and MTD, top 3 customers this month."""
    mtd = exec_sql(
        "SELECT COALESCE(SUM(total_amount_bhd),0) AS rev_mtd, "
        "COUNT(DISTINCT invoice_no) AS orders_mtd FROM v_sales "
        "WHERE DATE_TRUNC('month',sale_date)=DATE_TRUNC('month',CURRENT_DATE) LIMIT 1"
    )
    today = exec_sql(
        "SELECT COALESCE(SUM(total_amount_bhd),0) AS rev_today, "
        "COUNT(DISTINCT invoice_no) AS orders_today FROM v_sales "
        "WHERE sale_date=CURRENT_DATE LIMIT 1"
    )
    prev = exec_sql(
        "SELECT COALESCE(SUM(total_amount_bhd),0) AS rev_prev FROM v_sales "
        "WHERE DATE_TRUNC('month',sale_date)=DATE_TRUNC('month',CURRENT_DATE-INTERVAL '1 month') LIMIT 1"
    )
    top = exec_sql(
        "SELECT customer_name, total_revenue_bhd, order_count FROM v_top_customers "
        "WHERE last_order_date >= DATE_TRUNC('month',CURRENT_DATE) LIMIT 5"
    )
    recv_total = exec_sql(
        "SELECT COALESCE(SUM(outstanding_bhd),0) AS total FROM v_receivables LIMIT 1"
    )
    return {
        "rev_today": float((today or [{}])[0].get("rev_today", 0)),
        "orders_today": int((today or [{}])[0].get("orders_today", 0)),
        "rev_mtd": float((mtd or [{}])[0].get("rev_mtd", 0)),
        "orders_mtd": int((mtd or [{}])[0].get("orders_mtd", 0)),
        "rev_prev_month": float((prev or [{}])[0].get("rev_prev", 0)),
        "top_customers": top or [],
        "total_receivables": float((recv_total or [{}])[0].get("total", 0)),
    }


def low_stock_items(threshold: int = 10) -> list[dict]:
    return exec_sql(
        f"SELECT item_name, warehouse_name, balance_qty, as_of_date "
        f"FROM v_low_stock WHERE balance_qty <= {threshold} ORDER BY balance_qty ASC LIMIT 50"
    )


def overdue_receivables(days: int = 30) -> list[dict]:
    return exec_sql(
        f"SELECT account, outstanding_bhd, days_outstanding, salesman "
        f"FROM v_receivables WHERE days_outstanding >= {days} "
        f"ORDER BY outstanding_bhd DESC LIMIT 30"
    )


def negative_margins() -> list[dict]:
    return exec_sql(
        "SELECT item_name, gp_margin_pct, np_margin_pct, cogs_bhd, list_price_bhd, category_name "
        "FROM v_product_margin WHERE gp_margin_pct < 0 ORDER BY gp_margin_pct ASC LIMIT 20"
    )


def all_alerts() -> dict:
    """Combined alert payload for /digest/alerts endpoint."""
    low = low_stock_items()
    overdue = overdue_receivables(30)
    neg = negative_margins()
    return {
        "low_stock": low,
        "low_stock_count": len(low),
        "overdue_receivables": overdue,
        "overdue_count": len(overdue),
        "overdue_total_bhd": sum(float(r.get("outstanding_bhd", 0)) for r in overdue),
        "negative_margins": neg,
        "negative_margin_count": len(neg),
        "has_alerts": bool(low or overdue or neg),
    }
