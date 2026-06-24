"""Phase 3 — the YQ Bahrain AI agent team.

Each agent is a read-only function that turns the curated views into an
actionable briefing. They are called by:
  - GET /agents              → list available agents
  - GET /agents/{name}       → run one agent (n8n / GitHub Actions schedulers)
  - the dashboard agent panel

All SQL targets EXISTING semantic views only (scripts/views.sql). Every query is
wrapped so a single failure degrades gracefully to an empty result instead of
breaking the whole briefing. No agent mutates data in this version — they advise;
humans (or a future approval step) act.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agent_base import AgentSpec
from app.ai import exec_sql


def _q(sql: str) -> list[dict[str, Any]]:
    """Run a read-only query; return [] on any error."""
    try:
        return exec_sql(sql) or []
    except Exception:
        return []


def _f(row: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return default


# ── Collections agent ────────────────────────────────────────────────────────

def collections() -> dict:
    """Overdue receivables (trade debtors, from the AR ageing report) + a drafted
    reminder per account. Uses bucket-based aging, not a raw ledger balance."""
    rows = _q(
        "SELECT account, outstanding_bhd, overdue_bhd, over_90_bhd, group_name "
        "FROM v_receivables WHERE overdue_bhd > 0 "
        "ORDER BY overdue_bhd DESC LIMIT 40"
    )
    items = []
    for r in rows:
        acct = str(r.get("account", "")).strip()
        amt = _f(r, "outstanding_bhd")
        overdue = _f(r, "overdue_bhd")
        items.append({
            "account": acct,
            "outstanding_bhd": amt,
            "overdue_bhd": overdue,
            "over_90_bhd": _f(r, "over_90_bhd"),
            "group": r.get("group_name"),
            "draft_message": (
                f"Dear {acct}, our records show an outstanding balance of "
                f"BHD {amt:,.3f}, of which BHD {overdue:,.3f} is overdue (30+ days). "
                f"We'd appreciate settlement at your earliest convenience. "
                f"Thank you — YQ Bahrain."
            ),
        })
    total = sum(i["overdue_bhd"] for i in items)
    return {
        "count": len(items),
        "total_overdue_bhd": total,
        "summary": f"{len(items)} accounts with overdue balances — BHD {total:,.2f} past due.",
        "items": items,
    }


# ── Inventory / reorder agent ────────────────────────────────────────────────

def inventory_reorder() -> dict:
    """Velocity-aware reorder list. A fast mover at zero stock is URGENT (lost sales);
    days_cover = current stock ÷ 90-day average daily sales; alert under 30 days."""
    rows = _q(
        "SELECT item_name, current_stock, sold_90d, days_cover, suggested_reorder_qty, status "
        "FROM v_stock_health WHERE status IN ('urgent_out_of_stock','low_stock') "
        "ORDER BY (status='urgent_out_of_stock') DESC, days_cover ASC NULLS FIRST LIMIT 80"
    )
    items = [{
        "item_name": r.get("item_name"),
        "current_stock": _f(r, "current_stock"),
        "sold_90d": _f(r, "sold_90d"),
        "days_cover": r.get("days_cover"),
        "suggested_reorder_qty": _f(r, "suggested_reorder_qty"),
        "status": r.get("status"),
    } for r in rows]
    urgent = [i for i in items if i["status"] == "urgent_out_of_stock"]
    return {
        "count": len(items),
        "urgent_count": len(urgent),
        "summary": (f"{len(items)} items need reordering (<30 days cover) — "
                    f"{len(urgent)} URGENT: out of stock but still selling."),
        "items": items,
    }


# ── Margin Guardian ──────────────────────────────────────────────────────────

def margin_guardian(thin_threshold: float = 5.0) -> dict:
    """Negative and thin-margin products (Focus COGS basis)."""
    rows = _q(
        "SELECT item_name, product_name, category_name, gp_margin_pct, np_margin_pct, "
        "cogs_bhd, list_price_bhd FROM v_product_margin "
        f"WHERE gp_margin_pct IS NOT NULL AND gp_margin_pct < {thin_threshold} "
        "ORDER BY gp_margin_pct ASC LIMIT 40"
    )
    negative = [r for r in rows if _f(r, "gp_margin_pct") < 0]
    thin = [r for r in rows if 0 <= _f(r, "gp_margin_pct") < thin_threshold]
    return {
        "negative_count": len(negative),
        "thin_count": len(thin),
        "summary": f"{len(negative)} products selling below cost, {len(thin)} thin (<{thin_threshold:.0f}%).",
        "negative_margins": negative,
        "thin_margins": thin,
    }


# ── Sales Insights ───────────────────────────────────────────────────────────

def sales_insights() -> dict:
    """Recent monthly trend (gross + ex-VAT) + this month's top named customers.
    Month-on-month only — the data is <12 months so year-on-year isn't available."""
    trend = _q(
        "SELECT period_month, gross_bhd, net_revenue_bhd, order_count, total_qty "
        "FROM v_sales_by_period ORDER BY period_month DESC LIMIT 6"
    )
    top = _q(
        "SELECT customer_name, gross_bhd AS total_revenue_bhd, order_count FROM v_top_customers "
        "WHERE last_order_date >= DATE_TRUNC('month',(SELECT MAX(sale_date) FROM v_sales)) "
        "AND customer_name NOT ILIKE 'cash customer%' ORDER BY gross_bhd DESC NULLS LAST LIMIT 10"
    )
    this_m = _f(trend[0], "gross_bhd") if trend else 0.0
    prev_m = _f(trend[1], "gross_bhd") if len(trend) > 1 else 0.0
    delta = ((this_m - prev_m) / prev_m * 100) if prev_m else 0.0
    return {
        "summary": f"This month gross BHD {this_m:,.0f} ({delta:+.1f}% MoM).",
        "month_revenue_bhd": this_m,
        "prev_month_revenue_bhd": prev_m,
        "delta_pct": delta,
        "trend": list(reversed(trend)),
        "top_customers": top,
    }


# ── Sales Push agent ─────────────────────────────────────────────────────────

def sales_push() -> dict:
    """Where to push sales: best sellers, fast movers we've run OUT of (restock = recover
    lost sales), and dead/overstock to clear. Windows anchor to the data's latest date."""
    top = _q(
        "SELECT item_name, category_name, SUM(quantity) AS qty_sold, "
        "SUM(revenue_bhd) AS revenue_bhd FROM v_sales "
        "WHERE sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90 AND item_name IS NOT NULL "
        "GROUP BY item_name, category_name ORDER BY qty_sold DESC NULLS LAST LIMIT 15"
    )
    restock = _q(
        "SELECT item_name, sold_90d, suggested_reorder_qty FROM v_stock_health "
        "WHERE status='urgent_out_of_stock' ORDER BY sold_90d DESC LIMIT 20"
    )
    clear = _q(
        "SELECT item_name, current_stock, stock_value, sold_90d FROM v_stock_health "
        "WHERE status IN ('dead_stock','overstock') ORDER BY stock_value DESC LIMIT 20"
    )
    return {
        "summary": (
            f"{len(top)} top sellers · {len(restock)} fast movers OUT of stock (restock to "
            f"recover sales) · {len(clear)} slow/overstock lines to clear."
        ),
        "top_sellers": top,
        "restock_opportunities": restock,
        "clear_slow_overstock": clear,
    }


# ── Customer Health agent ────────────────────────────────────────────────────

def customer_health() -> dict:
    """Named accounts whose last-30-day spend dropped vs the prior 30 days (churn risk).
    Anchored to the data's latest date; excludes the walk-in 'Cash Customer' bucket."""
    rows = _q(
        "SELECT customer_name, "
        "SUM(CASE WHEN sale_date > (SELECT MAX(sale_date) FROM v_sales) - 30 "
        "         THEN revenue_bhd ELSE 0 END) AS rev_30, "
        "SUM(CASE WHEN sale_date > (SELECT MAX(sale_date) FROM v_sales) - 60 "
        "         AND sale_date <= (SELECT MAX(sale_date) FROM v_sales) - 30 "
        "         THEN revenue_bhd ELSE 0 END) AS rev_prev_30, "
        "MAX(sale_date) AS last_order "
        "FROM v_sales WHERE customer_name IS NOT NULL AND NOT is_cash_customer "
        "GROUP BY customer_name "
        "HAVING SUM(CASE WHEN sale_date > (SELECT MAX(sale_date) FROM v_sales) - 60 "
        "         AND sale_date <= (SELECT MAX(sale_date) FROM v_sales) - 30 "
        "         THEN revenue_bhd ELSE 0 END) > 0 "
        "ORDER BY (SUM(CASE WHEN sale_date > (SELECT MAX(sale_date) FROM v_sales) - 60 "
        "         AND sale_date <= (SELECT MAX(sale_date) FROM v_sales) - 30 "
        "         THEN revenue_bhd ELSE 0 END) "
        "       - SUM(CASE WHEN sale_date > (SELECT MAX(sale_date) FROM v_sales) - 30 "
        "         THEN revenue_bhd ELSE 0 END)) DESC LIMIT 25"
    )
    at_risk = [r for r in rows if _f(r, "rev_30") < 0.5 * _f(r, "rev_prev_30")]
    return {
        "at_risk_count": len(at_risk),
        "summary": f"{len(at_risk)} customers spending <50% of their prior month — follow up.",
        "at_risk": at_risk,
    }


# ── Cash-flow / receivables forecast ─────────────────────────────────────────

def cashflow_forecast() -> dict:
    """Receivables aging buckets + debtor concentration."""
    buckets = _q(
        "SELECT SUM(b_0_30) AS b_0_30, SUM(b_31_60) AS b_31_60, SUM(b_61_90) AS b_61_90, "
        "SUM(over_90_bhd) AS b_90_plus, SUM(outstanding_bhd) AS total FROM v_receivables LIMIT 1"
    )
    top_debtors = _q(
        "SELECT account, outstanding_bhd, over_90_bhd FROM v_receivables "
        "ORDER BY outstanding_bhd DESC LIMIT 5"
    )
    b = buckets[0] if buckets else {}
    total = _f(b, "total")
    top_share = (_f(top_debtors[0], "outstanding_bhd") / total * 100) if (top_debtors and total) else 0.0
    return {
        "summary": (
            f"BHD {total:,.0f} receivable — {_f(b,'b_90_plus'):,.0f} over 90 days; "
            f"top debtor = {top_share:.0f}% of book."
        ),
        "aging": {
            "0_30": _f(b, "b_0_30"),
            "31_60": _f(b, "b_31_60"),
            "61_90": _f(b, "b_61_90"),
            "90_plus": _f(b, "b_90_plus"),
            "total": total,
        },
        "top_debtors": top_debtors,
        "top_debtor_share_pct": top_share,
    }


# ── Anomaly / audit agent ────────────────────────────────────────────────────

def anomaly_scan() -> dict:
    """Pricing/data anomalies. Below-cost uses Focus's OWN gross-margin % (gp_margin_pct
    < 0), NOT per-unit price vs cumulative-period COGS (which gave false positives)."""
    below_cost = _q(
        "SELECT item_name, gp_margin_pct, net_amount_bhd, cogs_bhd FROM v_product_margin "
        "WHERE gp_margin_pct < 0 ORDER BY gp_margin_pct ASC LIMIT 20"
    )
    negative_stock = _q(
        "SELECT item_name, warehouse_name, balance_qty FROM v_current_stock "
        "WHERE balance_qty < 0 ORDER BY balance_qty ASC LIMIT 20"
    )
    dead_stock = _q(
        "SELECT item_name, current_stock, stock_value FROM v_stock_health "
        "WHERE status='dead_stock' ORDER BY stock_value DESC LIMIT 20"
    )
    n = len(below_cost) + len(negative_stock) + len(dead_stock)
    dead_val = sum(_f(r, "stock_value") for r in dead_stock)
    return {
        "anomaly_count": n,
        "summary": (f"{len(below_cost)} below cost · {len(negative_stock)} negative-stock · "
                    f"{len(dead_stock)} dead-stock lines (BHD {dead_val:,.0f} idle)."),
        "priced_below_cost": below_cost,
        "negative_stock": negative_stock,
        "dead_stock": dead_stock,
    }


# ── Registry ─────────────────────────────────────────────────────────────────

# ── Inventory Aging agent ─────────────────────────────────────────────────────

def inventory_aging() -> dict:
    """On-hand stock by days since last sale — capital sitting idle on the shelf."""
    rows = _q(
        "SELECT item_name, current_stock, stock_value, last_sold, days_since_sale "
        "FROM v_inventory_aging WHERE days_since_sale IS NULL OR days_since_sale > 60 "
        "ORDER BY stock_value DESC LIMIT 40"
    )
    idle = sum(_f(r, "stock_value") for r in rows)
    return {
        "count": len(rows),
        "idle_value_bhd": idle,
        "summary": f"{len(rows)} items idle >60 days — BHD {idle:,.0f} of stock not moving.",
        "items": rows,
    }


# ── Salesman Performance agent ────────────────────────────────────────────────

def salesman_performance() -> dict:
    """Per-salesman value + volume and the B2C/B2B channel split."""
    by_sm = _q("SELECT salesman, orders, qty, revenue_bhd, net_bhd FROM v_sales_by_salesman LIMIT 30")
    by_ch = _q("SELECT channel, orders, qty, revenue_bhd, net_bhd FROM v_sales_by_channel")
    total = sum(_f(r, "revenue_bhd") for r in by_sm)
    top = by_sm[0] if by_sm else {}
    return {
        "count": len(by_sm),
        "summary": (f"{len(by_sm)} salesmen · top: {top.get('salesman', '-')} "
                    f"BHD {_f(top, 'revenue_bhd'):,.0f} · total BHD {total:,.0f}."),
        "by_salesman": by_sm,
        "by_channel": by_ch,
    }


AGENTS: dict[str, AgentSpec] = {
    "collections": AgentSpec("collections", "Overdue receivables + drafted reminder messages", collections),
    "inventory": AgentSpec("inventory", "Velocity-aware reorder (urgent out-of-stock first)", inventory_reorder),
    "margin": AgentSpec("margin", "Negative & thin-margin products", margin_guardian),
    "sales_insights": AgentSpec("sales_insights", "Monthly sales trend (MoM) + top customers", sales_insights),
    "sales_push": AgentSpec("sales_push", "Best sellers, fast movers out of stock, slow/overstock to clear", sales_push),
    "customer_health": AgentSpec("customer_health", "Named customers with declining spend (churn risk)", customer_health),
    "cashflow": AgentSpec("cashflow", "Receivables aging buckets + debtor concentration", cashflow_forecast),
    "anomaly": AgentSpec("anomaly", "Below-cost (Focus GP%), negative & dead stock", anomaly_scan),
    "inventory_aging": AgentSpec("inventory_aging", "On-hand stock idle by days since last sale", inventory_aging),
    "salesman_performance": AgentSpec("salesman_performance", "Per-salesman value+volume + B2C/B2B", salesman_performance),
}


def list_agents() -> list[dict]:
    return [{"name": s.name, "description": s.description, "category": s.category} for s in AGENTS.values()]


def run_agent(name: str, triggered_by: str = "user") -> dict:
    """Run one agent and wrap with metadata. Raises KeyError for unknown agents.

    `triggered_by` ('user'|'schedule'|'escalation') is recorded by the memory layer (Phase B)."""
    if name not in AGENTS:
        raise KeyError(name)
    spec = AGENTS[name]
    result = spec.run()
    return {
        "agent": name,
        "description": spec.description,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
