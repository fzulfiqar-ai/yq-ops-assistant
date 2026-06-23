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
from typing import Any, Callable

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

def inventory_reorder(target_level: int = 25) -> dict:
    """Low-stock items with a suggested reorder quantity (advisory)."""
    rows = _q(
        "SELECT item_name, product_name, sku_code, category_name, warehouse_name, "
        "balance_qty, as_of_date FROM v_low_stock LIMIT 60"
    )
    items = []
    for r in rows:
        bal = _f(r, "balance_qty")
        suggest = max(int(target_level - bal), 0)
        items.append({
            "item_name": r.get("item_name"),
            "sku_code": r.get("sku_code"),
            "category_name": r.get("category_name"),
            "warehouse_name": r.get("warehouse_name"),
            "balance_qty": bal,
            "suggested_reorder_qty": suggest,
        })
    return {
        "count": len(items),
        "summary": f"{len(items)} items at/below minimum stock — reorder suggested.",
        "target_level": target_level,
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
    """Recent monthly trend + this month's top customers."""
    trend = _q(
        "SELECT period_month, net_revenue_bhd, order_count, total_qty "
        "FROM v_sales_by_period ORDER BY period_month DESC LIMIT 6"
    )
    top = _q(
        "SELECT customer_name, total_revenue_bhd, order_count FROM v_top_customers "
        "WHERE last_order_date >= DATE_TRUNC('month',CURRENT_DATE) LIMIT 10"
    )
    this_m = _f(trend[0], "net_revenue_bhd") if trend else 0.0
    prev_m = _f(trend[1], "net_revenue_bhd") if len(trend) > 1 else 0.0
    delta = ((this_m - prev_m) / prev_m * 100) if prev_m else 0.0
    return {
        "summary": f"MTD revenue BHD {this_m:,.0f} ({delta:+.1f}% vs last month).",
        "month_revenue_bhd": this_m,
        "prev_month_revenue_bhd": prev_m,
        "delta_pct": delta,
        "trend": list(reversed(trend)),
        "top_customers": top,
    }


# ── Sales Push agent ─────────────────────────────────────────────────────────

def sales_push() -> dict:
    """Top sellers, similar-but-not-moving stock (cross-sell), and aging reports."""
    top = _q(
        "SELECT item_name, category_name, SUM(quantity) AS qty_sold, "
        "SUM(total_amount_bhd) AS revenue_bhd FROM v_sales "
        "WHERE sale_date >= CURRENT_DATE - INTERVAL '90 days' "
        "GROUP BY item_name, category_name "
        "ORDER BY qty_sold DESC NULLS LAST LIMIT 15"
    )
    hot_categories = {str(r.get("category_name")) for r in top if r.get("category_name")}

    slow = _q(
        "SELECT cs.item_name, cs.category_name, cs.balance_qty, cs.as_of_date "
        "FROM v_current_stock cs "
        "WHERE cs.balance_qty > 0 AND cs.item_name NOT IN ("
        "  SELECT DISTINCT item_name FROM v_sales "
        "  WHERE sale_date >= CURRENT_DATE - INTERVAL '60 days' AND item_name IS NOT NULL"
        ") ORDER BY cs.balance_qty DESC LIMIT 30"
    )
    # "similar but not moving" = slow stock in a category that has a current top seller
    cross_sell = [r for r in slow if str(r.get("category_name")) in hot_categories]

    inv_aging = _q(
        "SELECT item_name, MAX(move_date) AS last_receipt, "
        "(CURRENT_DATE - MAX(move_date)) AS days_since_receipt "
        "FROM shipments WHERE item_name IS NOT NULL "
        "GROUP BY item_name ORDER BY days_since_receipt DESC NULLS LAST LIMIT 20"
    )
    return {
        "summary": (
            f"{len(top)} top sellers · {len(cross_sell)} slow items to cross-sell · "
            f"{len(inv_aging)} aging stock lines."
        ),
        "top_sellers": top,
        "slow_movers": slow,
        "cross_sell_opportunities": cross_sell,
        "inventory_aging": inv_aging,
    }


# ── Customer Health agent ────────────────────────────────────────────────────

def customer_health() -> dict:
    """Customers whose last-30-day spend dropped vs the prior 30 days (churn risk)."""
    rows = _q(
        "SELECT customer_name, "
        "SUM(CASE WHEN sale_date >= CURRENT_DATE - INTERVAL '30 days' "
        "         THEN total_amount_bhd ELSE 0 END) AS rev_30, "
        "SUM(CASE WHEN sale_date >= CURRENT_DATE - INTERVAL '60 days' "
        "         AND sale_date < CURRENT_DATE - INTERVAL '30 days' "
        "         THEN total_amount_bhd ELSE 0 END) AS rev_prev_30, "
        "MAX(sale_date) AS last_order "
        "FROM v_sales WHERE customer_name IS NOT NULL "
        "GROUP BY customer_name "
        "HAVING SUM(CASE WHEN sale_date >= CURRENT_DATE - INTERVAL '60 days' "
        "         AND sale_date < CURRENT_DATE - INTERVAL '30 days' "
        "         THEN total_amount_bhd ELSE 0 END) > 0 "
        "ORDER BY (SUM(CASE WHEN sale_date >= CURRENT_DATE - INTERVAL '60 days' "
        "         AND sale_date < CURRENT_DATE - INTERVAL '30 days' "
        "         THEN total_amount_bhd ELSE 0 END) "
        "       - SUM(CASE WHEN sale_date >= CURRENT_DATE - INTERVAL '30 days' "
        "         THEN total_amount_bhd ELSE 0 END)) DESC LIMIT 25"
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
    """Pricing and data anomalies that warrant a human look."""
    below_cost = _q(
        "SELECT item_name, list_price_bhd, cogs_bhd FROM v_product_margin "
        "WHERE list_price_bhd IS NOT NULL AND cogs_bhd IS NOT NULL "
        "AND list_price_bhd < cogs_bhd ORDER BY (cogs_bhd - list_price_bhd) DESC LIMIT 20"
    )
    negative_stock = _q(
        "SELECT item_name, warehouse_name, balance_qty FROM v_current_stock "
        "WHERE balance_qty < 0 ORDER BY balance_qty ASC LIMIT 20"
    )
    n = len(below_cost) + len(negative_stock)
    return {
        "anomaly_count": n,
        "summary": f"{len(below_cost)} priced below cost · {len(negative_stock)} negative-stock lines.",
        "priced_below_cost": below_cost,
        "negative_stock": negative_stock,
    }


# ── Registry ─────────────────────────────────────────────────────────────────

AGENTS: dict[str, tuple[Callable[[], dict], str]] = {
    "collections": (collections, "Overdue receivables + drafted reminder messages"),
    "inventory": (inventory_reorder, "Low stock + suggested reorder quantities"),
    "margin": (margin_guardian, "Negative & thin-margin products"),
    "sales_insights": (sales_insights, "Monthly sales trend + top customers"),
    "sales_push": (sales_push, "Top sellers, slow movers to cross-sell, aging reports"),
    "customer_health": (customer_health, "Customers with declining spend (churn risk)"),
    "cashflow": (cashflow_forecast, "Receivables aging buckets + debtor concentration"),
    "anomaly": (anomaly_scan, "Pricing/data anomalies (below cost, negative stock)"),
}


def list_agents() -> list[dict]:
    return [{"name": name, "description": desc} for name, (_fn, desc) in AGENTS.items()]


def run_agent(name: str) -> dict:
    """Run one agent and wrap with metadata. Raises KeyError for unknown agents."""
    if name not in AGENTS:
        raise KeyError(name)
    fn, desc = AGENTS[name]
    result = fn()
    return {
        "agent": name,
        "description": desc,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
