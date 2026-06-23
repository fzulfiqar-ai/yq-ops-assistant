"""Deterministic SQL templates for the top ~20 operational questions.

Questions matching a template are answered instantly with no LLM call,
no quota burn, and no redaction needed. The LLM is invoked only for the
long tail.

Usage:
    from app.templates import match
    result = match("what are total sales this month?")
    if result:
        label, sql = result
        # execute sql directly against Supabase
"""
from __future__ import annotations

import re

# Data is historical — anchor "today/week/month" to the latest day WITH data, not the
# server clock (which runs ahead and would return zero).
_DD = "(SELECT MAX(sale_date) FROM v_sales)"
_THIS_MONTH = f"DATE_TRUNC('month', sale_date) = DATE_TRUNC('month', {_DD})"
_THIS_WEEK  = f"sale_date > {_DD} - 7"
_TODAY      = f"sale_date = {_DD}"


def _p(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE)


TEMPLATES: list[dict] = [
    # ── Sales totals ──────────────────────────────────────────────────────────
    {
        "pattern": _p(
            r"total sales (for )?today", r"sales today", r"today'?s? sales",
            r"how much.{0,20}sold today",
        ),
        "label": "Total sales today",
        "sql": (
            "SELECT COUNT(DISTINCT invoice_no) AS orders, "
            "SUM(quantity) AS total_qty, "
            "SUM(revenue_bhd) AS revenue_bhd, "
            "SUM(vat_amount_bhd) AS vat_bhd "
            f"FROM v_sales WHERE {_TODAY} LIMIT 1"
        ),
    },
    {
        "pattern": _p(
            r"total sales (for |this )?week", r"sales this week", r"weekly sales",
            r"how much.{0,20}sold this week",
        ),
        "label": "Total sales this week",
        "sql": (
            "SELECT COUNT(DISTINCT invoice_no) AS orders, "
            "SUM(quantity) AS total_qty, "
            "SUM(revenue_bhd) AS revenue_bhd "
            f"FROM v_sales WHERE {_THIS_WEEK} LIMIT 1"
        ),
    },
    {
        "pattern": _p(
            r"total sales (for |this )?month", r"sales this month",
            r"monthly sales total", r"how much.{0,20}sold this month",
        ),
        "label": "Total sales this month",
        "sql": (
            "SELECT COUNT(DISTINCT invoice_no) AS orders, "
            "SUM(quantity) AS total_qty, "
            "SUM(revenue_bhd) AS revenue_bhd, "
            "SUM(vat_amount_bhd) AS vat_bhd "
            f"FROM v_sales WHERE {_THIS_MONTH} LIMIT 1"
        ),
    },
    {
        "pattern": _p(
            r"sales by month", r"monthly (sales )?trend",
            r"revenue by month", r"sales trend", r"month.{0,10}breakdown",
        ),
        "label": "Monthly sales trend",
        "sql": "SELECT * FROM v_sales_by_period ORDER BY period_month DESC LIMIT 24",
    },
    # ── Revenue breakdowns ────────────────────────────────────────────────────
    {
        "pattern": _p(
            r"(revenue|sales) (by|per) (salesman|sales person|rep)",
            r"salesman performance", r"sales by salesman", r"which salesman",
        ),
        "label": "Revenue by salesman",
        "sql": (
            "SELECT salesman_resolved AS salesman, COUNT(DISTINCT invoice_no) AS orders, "
            "SUM(quantity) AS qty, SUM(total_amount_bhd) AS revenue_bhd "
            "FROM v_sales WHERE salesman_resolved IS NOT NULL "
            "GROUP BY salesman_resolved ORDER BY revenue_bhd DESC LIMIT 20"
        ),
    },
    {
        "pattern": _p(
            r"best.?sell(ing)? products?", r"top.?sell(ing)? products?",
            r"most sold", r"top products? by (qty|quantity|volume)",
        ),
        "label": "Best-selling products",
        "sql": (
            "SELECT item_name, SUM(quantity) AS qty_sold, "
            "SUM(revenue_bhd) AS revenue_bhd "
            "FROM v_sales GROUP BY item_name "
            "ORDER BY qty_sold DESC LIMIT 20"
        ),
    },
    {
        "pattern": _p(
            r"top \d* ?customers?", r"best customers?", r"biggest customers?",
            r"most valuable customers?",
        ),
        "label": "Top customers by revenue",
        "sql": "SELECT * FROM v_top_customers LIMIT 20",
    },
    {
        "pattern": _p(
            r"(sales|revenue) by categor(y|ies)",
            r"categor(y|ies) (sales|revenue|breakdown)",
            r"which categor(y|ies)",
        ),
        "label": "Sales by category",
        "sql": (
            "SELECT category_name, COUNT(DISTINCT invoice_no) AS orders, "
            "SUM(quantity) AS qty, SUM(total_amount_bhd) AS revenue_bhd "
            "FROM v_sales WHERE category_name IS NOT NULL "
            "GROUP BY category_name ORDER BY revenue_bhd DESC LIMIT 20"
        ),
    },
    # ── Stock ─────────────────────────────────────────────────────────────────
    {
        "pattern": _p(
            r"(current )?stock (levels?|summary|report|status)",
            r"inventory (levels?|summary|report)", r"what.{0,20}in stock",
            r"show (all )?stock",
        ),
        "label": "Current stock summary",
        "sql": (
            "SELECT item_name, warehouse_name, balance_qty, "
            "avg_rate_bhd, as_of_date "
            "FROM v_current_stock WHERE balance_qty > 0 "
            "ORDER BY balance_qty DESC LIMIT 100"
        ),
    },
    {
        "pattern": _p(
            r"low stock", r"(items?|products?) (running )?low",
            r"(re)?order.{0,10}needed", r"out of stock",
            r"stock.{0,10}alert", r"(items?|products?).{0,10}below",
        ),
        "label": "Low stock & out-of-stock (velocity-aware)",
        "sql": (
            "SELECT item_name, current_stock, sold_90d, days_cover, suggested_reorder_qty, status "
            "FROM v_stock_health WHERE status IN ('urgent_out_of_stock','low_stock') "
            "ORDER BY (status='urgent_out_of_stock') DESC, days_cover ASC NULLS FIRST LIMIT 50"
        ),
    },
    {
        "pattern": _p(
            r"(total )?stock value", r"inventory value",
            r"(total )?(stock|inventory) worth", r"value of (stock|inventory)",
        ),
        "label": "Total stock value",
        "sql": (
            "SELECT SUM(balance_value_bhd) AS total_stock_value_bhd, "
            "COUNT(DISTINCT item_name) AS distinct_items "
            "FROM v_current_stock WHERE balance_qty > 0 LIMIT 1"
        ),
    },
    # ── Margins ───────────────────────────────────────────────────────────────
    {
        "pattern": _p(
            r"negative margin", r"loss.?making", r"(items?|products?) losing money",
            r"(items?|products?) (with )?negative", r"(items?|products?) below zero",
        ),
        "label": "Negative-margin products",
        "sql": (
            "SELECT item_name, gp_margin_pct, gross_profit_bhd, "
            "net_profit_bhd, np_margin_pct, report_date "
            "FROM v_product_margin WHERE gp_margin_pct < 0 "
            "ORDER BY gp_margin_pct ASC LIMIT 50"
        ),
    },
    {
        "pattern": _p(
            r"(product )?margins?( report| summary| overview)?",
            r"(show |all )?profitability", r"(all )?gp (margin|%|percentages?)",
            r"gross profit (by product|summary|report)",
        ),
        "label": "Product margins",
        "sql": (
            "SELECT item_name, gp_margin_pct, gross_profit_bhd, "
            "net_profit_bhd, np_margin_pct, report_date "
            "FROM v_product_margin ORDER BY gp_margin_pct DESC LIMIT 50"
        ),
    },
    # ── Receivables ───────────────────────────────────────────────────────────
    {
        "pattern": _p(
            r"total (outstanding|receivables?|amount (owed|due))",
            r"how much (is )?owed (to us|total)",
        ),
        "label": "Total outstanding receivables",
        "sql": (
            "SELECT COUNT(*) AS debtor_count, "
            "SUM(outstanding_bhd) AS total_outstanding_bhd "
            "FROM v_receivables LIMIT 1"
        ),
    },
    {
        "pattern": _p(
            r"(outstanding |overdue )?(receivables?|debtors?|amounts? (owed|due))",
            r"who owes( us)?", r"unpaid (invoices?|balances?)",
            r"customer.{0,10}balances?",
        ),
        "label": "Outstanding receivables",
        "sql": (
            "SELECT account, group_name, outstanding_bhd, overdue_bhd, over_90_bhd "
            "FROM v_receivables ORDER BY outstanding_bhd DESC LIMIT 50"
        ),
    },
    # ── Shipments ─────────────────────────────────────────────────────────────
    {
        "pattern": _p(
            r"(recent )?shipments?", r"goods? receiv(ed)?",
            r"(stock |items? )?receiv(ed)?", r"(recent )?mrn",
            r"material receipt",
        ),
        "label": "Recent shipments",
        "sql": (
            "SELECT received_date, mrn_no, item_name, received_qty, "
            "received_rate_bhd, warehouse_name "
            "FROM shipments ORDER BY received_date DESC LIMIT 50"
        ),
    },
]


def match(question: str) -> tuple[str, str] | None:
    """Return (label, sql) if the question matches a deterministic template, else None."""
    for t in TEMPLATES:
        if t["pattern"].search(question):
            return t["label"], t["sql"]
    return None
