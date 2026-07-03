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

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from app.agent_base import AgentSpec
from app.ai import exec_sql

log = logging.getLogger(__name__)

# Per-run query-failure counter. thread-local so it stays isolated when the orchestrator runs
# several agents concurrently (ThreadPoolExecutor). run_agent() resets it before each run and
# annotates the summary if anything failed — so a transient DB error can no longer masquerade
# as "0 accounts overdue".
_run_state = threading.local()


def _q(sql: str) -> list[dict[str, Any]]:
    """Run a read-only query; return [] on error AND record the failure (see _run_state) so the
    agent's summary can flag partial data instead of silently reporting zero."""
    try:
        return exec_sql(sql) or []
    except Exception as exc:  # noqa: BLE001
        log.warning("agent query failed (%s): %s", type(exc).__name__, str(exc)[:200])
        _run_state.errors = getattr(_run_state, "errors", 0) + 1
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
    # Whole-book totals in SQL — the list above is capped at 40, so summing it would
    # under-report and disagree with the dashboard whenever more accounts are overdue.
    tot = _q(
        "SELECT COUNT(*) AS n, COALESCE(SUM(overdue_bhd),0) AS overdue, "
        "COALESCE(SUM(outstanding_bhd),0) AS book FROM v_receivables WHERE overdue_bhd > 0"
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
    n = int(_f(tot[0], "n")) if tot else len(items)
    total = _f(tot[0], "overdue") if tot else sum(i["overdue_bhd"] for i in items)
    book = _f(tot[0], "book") if tot else sum(i["outstanding_bhd"] for i in items)
    return {
        "count": n,
        "total_overdue_bhd": total,
        "overdue_book_bhd": book,
        "summary": (f"{n} accounts with overdue balances — BHD {total:,.2f} past due (>30d)"
                    + (f"; showing top {len(items)}." if n > len(items) else ".")),
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
    """Sales-push targeting: for every slow/dead SKU, WHO should sell it and TO WHOM.
    Matches each push item to customers who already buy that category (last 180d),
    grouped per salesman route, with a clearance price and the capital it frees —
    a rep-ready push list, not just a slow-mover report. Giveaway lines excluded."""
    top = _q(
        "SELECT item_name, category_name, SUM(quantity) AS qty_sold, "
        "SUM(revenue_bhd) AS revenue_bhd FROM v_sales "
        "WHERE sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90 AND item_name IS NOT NULL "
        "AND NOT is_giveaway "
        "GROUP BY item_name, category_name ORDER BY qty_sold DESC NULLS LAST LIMIT 15"
    )
    restock = _q(
        "SELECT item_name, sold_90d, suggested_reorder_qty FROM v_stock_health "
        "WHERE status='urgent_out_of_stock' ORDER BY sold_90d DESC LIMIT 20"
    )
    clear = _q(
        "SELECT h.item_name, h.current_stock, h.stock_value, h.sold_90d, a.days_since_sale "
        "FROM v_stock_health h LEFT JOIN v_inventory_aging a ON a.item_name = h.item_name "
        "WHERE h.status IN ('dead_stock','overstock') ORDER BY h.stock_value DESC LIMIT 12"
    )
    # One query: for each push item, the 4 best-matched customers (bought its category
    # recently), tagged with their salesman — so each rep gets a concrete call list.
    targets = _q(
        "WITH push AS ("
        "  SELECT item_name FROM v_stock_health WHERE status IN ('dead_stock','overstock') "
        "  ORDER BY stock_value DESC LIMIT 12), "
        "cat AS ("
        "  SELECT DISTINCT ON (s.item_name) s.item_name, s.category_name "
        "  FROM v_sales s JOIN push p ON p.item_name = s.item_name "
        "  WHERE s.category_name IS NOT NULL), "
        "buyers AS ("
        "  SELECT c.item_name, s.salesman_resolved AS salesman, s.customer_name, "
        "         SUM(s.revenue_bhd) AS spent_bhd, MAX(s.sale_date) AS last_buy "
        "  FROM v_sales s JOIN cat c ON c.category_name = s.category_name "
        "  WHERE s.customer_name NOT ILIKE 'cash customer%' AND NOT s.is_giveaway "
        "    AND s.sale_date > (SELECT MAX(sale_date) FROM v_sales) - 180 "
        "  GROUP BY 1, 2, 3) "
        "SELECT item_name, salesman, customer_name, spent_bhd, last_buy FROM ("
        "  SELECT b.*, ROW_NUMBER() OVER (PARTITION BY b.item_name ORDER BY b.spent_bhd DESC) AS rn "
        "  FROM buyers b) x WHERE rn <= 4"
    )
    by_item: dict[str, list] = {}
    for t in targets:
        by_item.setdefault(t.get("item_name") or "", []).append(
            {"customer": t.get("customer_name"), "salesman": t.get("salesman"),
             "spent_bhd": _f(t, "spent_bhd"), "last_buy": t.get("last_buy")})
    items, trapped, freed = [], 0.0, 0.0
    for r in clear:
        val = _f(r, "stock_value")
        d = r.get("days_since_sale")
        md = 0.40 if d is None else (0.50 if _f(r, "days_since_sale") > 180 else 0.30)
        trapped += val
        freed += val * (1 - md)
        items.append({
            "item_name": r.get("item_name"), "current_stock": _f(r, "current_stock"),
            "stock_value_bhd": val, "sold_90d": _f(r, "sold_90d"),
            "suggested_markdown_pct": int(md * 100),
            "est_recovery_bhd": round(val * (1 - md), 1),
            "target_customers": by_item.get(r.get("item_name") or "", []),
        })
    # per-salesman rollup: their personal push list
    per_rep: dict[str, list] = {}
    for it in items:
        for t in it["target_customers"]:
            rep = t.get("salesman") or "(unassigned)"
            per_rep.setdefault(rep, []).append(
                {"item_name": it["item_name"], "customer": t["customer"],
                 "markdown_pct": it["suggested_markdown_pct"]})
    matched = sum(1 for i in items if i["target_customers"])
    return {
        "summary": (
            f"{len(items)} slow lines tying up BHD {trapped:,.0f} — {matched} matched to named "
            f"buyers across {len(per_rep)} salesmen; a targeted push at the suggested markdowns "
            f"recovers ~BHD {freed:,.0f}. Plus {len(restock)} fast movers OUT of stock to reorder."
        ),
        "count": len(items),
        "trapped_value_bhd": round(trapped, 1),
        "est_recovery_bhd": round(freed, 1),
        "push_list": items,
        "per_salesman": [{"salesman": k, "targets": v} for k, v in
                         sorted(per_rep.items(), key=lambda kv: -len(kv[1]))],
        "top_sellers": top,
        "restock_opportunities": restock,
    }


def _catalog_link() -> str:
    """Public RRP-only catalog URL for outreach messages ('' if unavailable)."""
    try:
        import os
        from app.catalog import share_token
        tok = share_token()
        base = os.getenv("APP_BASE_URL", "").rstrip("/")
        return f"{base}/c/{tok}" if (tok and base) else ""
    except Exception:  # noqa: BLE001
        return ""


def sales_outreach() -> dict:
    """AI sales agent (drafts only — a human sends): customers who are DUE to reorder
    (past 1.5× their usual buying cycle), what they usually buy, and a ready EN/AR
    WhatsApp message with the public catalog link. Ranked by lifetime value."""
    due = _q(
        "WITH mx AS (SELECT MAX(sale_date) AS d FROM v_sales), "
        "cust AS ("
        "  SELECT customer_name, COUNT(DISTINCT sale_date) AS visit_days, "
        "         MIN(sale_date) AS first_order, MAX(sale_date) AS last_order, "
        "         SUM(revenue_bhd) AS lifetime_bhd "
        "  FROM v_sales WHERE customer_name IS NOT NULL "
        "    AND customer_name NOT ILIKE 'cash customer%' AND NOT is_giveaway "
        "  GROUP BY 1 HAVING COUNT(DISTINCT sale_date) >= 3) "
        "SELECT c.customer_name, c.lifetime_bhd, c.last_order, "
        "       ((SELECT d FROM mx) - c.last_order) AS days_since, "
        "       ROUND(((c.last_order - c.first_order)::numeric / NULLIF(c.visit_days - 1, 0)), 1) "
        "         AS cycle_days "
        "FROM cust c "
        "WHERE (c.last_order - c.first_order) > 0 "
        "  AND ((SELECT d FROM mx) - c.last_order) > "
        "      1.5 * ((c.last_order - c.first_order)::numeric / NULLIF(c.visit_days - 1, 0)) "
        "ORDER BY c.lifetime_bhd DESC LIMIT 12"
    )
    usuals = _q(
        "WITH mx AS (SELECT MAX(sale_date) AS d FROM v_sales), "
        "cust AS ("
        "  SELECT customer_name, COUNT(DISTINCT sale_date) AS visit_days, "
        "         MIN(sale_date) AS first_order, MAX(sale_date) AS last_order "
        "  FROM v_sales WHERE customer_name IS NOT NULL "
        "    AND customer_name NOT ILIKE 'cash customer%' AND NOT is_giveaway "
        "  GROUP BY 1 HAVING COUNT(DISTINCT sale_date) >= 3), "
        "due AS ("
        "  SELECT customer_name FROM cust "
        "  WHERE (last_order - first_order) > 0 "
        "    AND ((SELECT d FROM mx) - last_order) > "
        "        1.5 * ((last_order - first_order)::numeric / NULLIF(visit_days - 1, 0)) "
        "  ORDER BY 1 LIMIT 40) "
        "SELECT customer_name, item_name, qty FROM ("
        "  SELECT s.customer_name, s.item_name, SUM(s.quantity) AS qty, "
        "         ROW_NUMBER() OVER (PARTITION BY s.customer_name "
        "                            ORDER BY SUM(s.quantity) DESC) AS rn "
        "  FROM v_sales s JOIN due d ON d.customer_name = s.customer_name "
        "  WHERE s.item_name IS NOT NULL AND NOT s.is_giveaway "
        "  GROUP BY s.customer_name, s.item_name) x WHERE rn <= 3"
    )
    top_items: dict[str, list[str]] = {}
    for u in usuals:
        top_items.setdefault(u.get("customer_name") or "", []).append(str(u.get("item_name") or ""))
    link = _catalog_link()
    link_line_en = f"\nOur latest catalog with prices: {link}" if link else ""
    link_line_ar = f"\nأحدث كتالوج بالأسعار: {link}" if link else ""
    drafts = []
    for r in due:
        name = str(r.get("customer_name") or "").strip()
        items = [i.split(" (")[0] for i in top_items.get(name, [])][:3]
        usual = ", ".join(items) if items else "your usual items"
        days = int(_f(r, "days_since"))
        drafts.append({
            "customer": name,
            "lifetime_bhd": _f(r, "lifetime_bhd"),
            "days_since_order": days,
            "usual_cycle_days": _f(r, "cycle_days"),
            "usual_items": items,
            "message_en": (
                f"Hello {name}, it's YQ Bahrain. It's been {days} days since your last order — "
                f"we have fresh stock of {usual} and new VFAN arrivals. "
                f"Shall we prepare your usual order?{link_line_en}"
            ),
            "message_ar": (
                f"مرحباً {name}، معكم YQ البحرين. مضى {days} يوماً على آخر طلبية — "
                f"لدينا مخزون جديد من {usual} ووصلات VFAN جديدة. "
                f"هل نجهز طلبيتكم المعتادة؟{link_line_ar}"
            ),
        })
    value = sum(d["lifetime_bhd"] for d in drafts)
    return {
        "count": len(drafts),
        "summary": (f"{len(drafts)} customers are past their usual reorder cycle "
                    f"(BHD {value:,.0f} lifetime value) — WhatsApp drafts ready to send."
                    if drafts else
                    "No customers are overdue for a reorder — the active book is buying on cycle."),
        "drafts": drafts,
        "catalog_link": link,
    }


def growth_plan() -> dict:
    """The weekly growth plan: ONE ranked list of this week's money moves, assembled
    from the specialist agents (collections, clearance push, win-back, repricing,
    restock) with the BHD at stake and where to act. Answers 'what do I do this week?'"""
    plan: list[dict] = []

    c = collections()
    if c.get("count"):
        plan.append({
            "move": f"Chase BHD {c['total_overdue_bhd']:,.0f} overdue across {c['count']} accounts "
                    f"(drafted reminders ready)",
            "impact_bhd": round(_f(c, "total_overdue_bhd"), 0), "owner": "Finance", "link": "/receivables"})

    sp = sales_push()
    if sp.get("count"):
        plan.append({
            "move": f"Run the targeted clearance push — {sp['count']} slow lines matched to named "
                    f"buyers per salesman (frees ~BHD {sp['est_recovery_bhd']:,.0f})",
            "impact_bhd": round(_f(sp, "est_recovery_bhd"), 0), "owner": "Sales", "link": "/agents"})
    if sp.get("restock_opportunities"):
        n = len(sp["restock_opportunities"])
        plan.append({
            "move": f"Reorder {n} fast movers that are OUT of stock — every day out is lost sales",
            "impact_bhd": 0, "owner": "Supply", "link": "/orders"})

    so = sales_outreach()
    if so.get("count"):
        v = sum(d["lifetime_bhd"] for d in so["drafts"])
        plan.append({
            "move": f"Send the {so['count']} reorder nudges — customers past their buying cycle "
                    f"(BHD {v:,.0f} lifetime value)",
            "impact_bhd": round(v * 0.05, 0), "owner": "Sales", "link": "/agents"})

    wb = winback()
    if wb.get("count"):
        plan.append({
            "move": f"Win back {wb['count']} lapsed/at-risk accounts "
                    f"(BHD {_f(wb, 'lifetime_value_bhd'):,.0f} lifetime value to protect)",
            "impact_bhd": 0, "owner": "Sales", "link": "/sales"})

    pd = price_drift()
    if pd.get("count"):
        plan.append({
            "move": f"Reprice {pd['count']} SKUs whose landed cost rose with no price response "
                    f"— silent margin erosion",
            "impact_bhd": 0, "owner": "Finance", "link": "/margins"})

    plan.sort(key=lambda p: -p["impact_bhd"])
    at_stake = sum(p["impact_bhd"] for p in plan)
    return {
        "count": len(plan),
        "summary": (f"This week's plan: {len(plan)} moves with ~BHD {at_stake:,.0f} directly at stake. "
                    f"Top: {plan[0]['move']}" if plan else
                    "Nothing urgent this week — book is collected, stock is moving, prices hold."),
        "plan": plan,
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


# ── Procurement agent ─────────────────────────────────────────────────────────

def procurement() -> dict:
    """What we buy: recent purchases, cost increases vs last order, and what to reorder
    (velocity-driven) with the last vendor + price so you can raise the next shipment."""
    cost_up = _q(
        "SELECT item_name, vendor, prev_cost_bhd, current_cost_bhd, cost_change_pct "
        "FROM v_cost_change WHERE cost_change_pct > 5 ORDER BY cost_change_pct DESC LIMIT 15"
    )
    recent = _q(
        "SELECT item_name, purchased_on, qty, cost_bhd, vendor FROM v_purchase_history "
        "ORDER BY purchased_on DESC LIMIT 10"
    )
    reorder = _q(
        "SELECT item_name, current_stock, sold_90d, suggested_reorder_qty FROM v_stock_health "
        "WHERE status IN ('urgent_out_of_stock','low_stock') ORDER BY sold_90d DESC NULLS LAST LIMIT 15"
    )
    return {
        "cost_increase_count": len(cost_up),
        "reorder_count": len(reorder),
        "summary": (f"{len(reorder)} items to reorder · {len(cost_up)} with cost up >5% vs last "
                    f"order · {len(recent)} recent receipts."),
        "cost_increases": cost_up,
        "to_reorder": reorder,
        "recent_purchases": recent,
    }


# ── Fraud / leakage agent ─────────────────────────────────────────────────────

def fraud_scan() -> dict:
    """Transaction-integrity signals: products SOLD below cost, negative (phantom) stock,
    and items sold at a wide price spread (possible unauthorised discounting)."""
    below_cost = _q(
        "SELECT item_name, gp_margin_pct, net_amount_bhd FROM v_product_margin "
        "WHERE gp_margin_pct < 0 ORDER BY gp_margin_pct ASC LIMIT 15"
    )
    neg_stock = _q(
        "SELECT item_name, warehouse_name, balance_qty FROM v_current_stock "
        "WHERE balance_qty < 0 ORDER BY balance_qty ASC LIMIT 15"
    )
    price_spread = _q(
        "SELECT item_name, "
        "ROUND(MIN(net_bhd/NULLIF(quantity,0))::numeric,3) AS min_unit_price, "
        "ROUND(MAX(net_bhd/NULLIF(quantity,0))::numeric,3) AS max_unit_price, COUNT(*) AS lines "
        "FROM v_sales WHERE quantity > 0 AND net_bhd > 0 AND item_name IS NOT NULL "
        "GROUP BY item_name HAVING COUNT(*) >= 5 "
        "AND MIN(net_bhd/NULLIF(quantity,0)) < 0.5 * MAX(net_bhd/NULLIF(quantity,0)) "
        "ORDER BY (MAX(net_bhd/NULLIF(quantity,0)) - MIN(net_bhd/NULLIF(quantity,0))) DESC LIMIT 15"
    )
    n = len(below_cost) + len(neg_stock) + len(price_spread)
    return {
        "anomaly_count": n,
        "summary": (f"{len(below_cost)} sold below cost · {len(neg_stock)} negative-stock · "
                    f"{len(price_spread)} items with >2x price spread (discount leakage?)."),
        "sold_below_cost": below_cost,
        "negative_stock": neg_stock,
        "price_spread": price_spread,
    }


# ── Salesman stock reconciliation (fraud + ops) ───────────────────────────────

def salesman_stock_recon() -> dict:
    """Reconcile stock ISSUED to each salesman's van warehouse against what they SOLD,
    sent back, and still HOLD. Focus's own 'Shortages in Stock' counts are the HARD leakage
    signal (BHD); a large 'unexplained' gap (issued − returned − sold − on-hand) is a SOFT one
    to investigate. Built on the Stock Issue/Receive vouchers Focus records per transfer."""
    # Hard signal: Focus's own physical-count shortages, any warehouse (incl. the central store).
    shortages = _q(
        "SELECT salesman, shortage_qty, shortage_value_bhd FROM v_salesman_stock_recon "
        "WHERE shortage_value_bhd > 0 ORDER BY shortage_value_bhd DESC LIMIT 20"
    )
    short_total = sum(_f(r, "shortage_value_bhd") for r in shortages)
    # Soft signal: stock issued to a salesman's VAN that isn't sold, returned, or on hand.
    leakage = _q(
        "SELECT salesman, transferred_in_qty, transferred_in_value_bhd, sold_qty, on_hand_qty, "
        "unexplained_qty FROM v_salesman_stock_recon "
        "WHERE is_van AND unexplained_qty > 0 ORDER BY unexplained_qty DESC LIMIT 20"
    )
    # Fraud route: good stock written off as 'damaged' (moved into a Damage warehouse).
    damaged = _q(
        "SELECT to_warehouse, COUNT(*) AS lines, ROUND(SUM(qty)::numeric,0) AS qty, "
        "ROUND(SUM(value_bhd)::numeric,3) AS value_bhd FROM v_stock_transfers "
        "WHERE to_warehouse ILIKE '%damage%' GROUP BY to_warehouse ORDER BY value_bhd DESC LIMIT 10"
    )
    dmg_val = sum(_f(r, "value_bhd") for r in damaged)
    recent_transfers = _q(
        "SELECT transfer_date, item_name, from_warehouse, to_warehouse, qty, value_bhd "
        "FROM v_stock_transfers ORDER BY transfer_date DESC LIMIT 15"
    )
    parts = []
    if shortages:
        parts.append(f"BHD {short_total:,.2f} of counted stock shortages across "
                     f"{len(shortages)} warehouse(s) — biggest: {shortages[0].get('salesman', '-')}")
    if leakage:
        parts.append(f"{len(leakage)} salesman van(s) with unexplained stock gaps to review "
                     f"(top: {leakage[0].get('salesman', '-')}, {_f(leakage[0],'unexplained_qty'):,.0f} units)")
    if dmg_val > 0:
        parts.append(f"BHD {dmg_val:,.2f} of stock written to damage")
    summary = ("Stock reconciliation: " + "; ".join(parts) + "."
               if parts else "Stock reconciliation: no shortages or unexplained gaps — vans reconcile cleanly.")
    return {
        "shortage_count": len(shortages),
        "shortage_value_bhd": short_total,
        "leakage_count": len(leakage),
        "damaged_value_bhd": dmg_val,
        "summary": summary,
        "shortages": shortages,
        "salesman_leakage": leakage,
        "damaged_to_warehouses": damaged,
        "recent_transfers": recent_transfers,
    }


# ── Trend agent (growth) ──────────────────────────────────────────────────────

def trend_scan() -> dict:
    """Momentum: which items are RISING vs FADING (last 30 days vs the prior 30), and the
    overall monthly revenue trend. Anchored to the data's latest date."""
    momentum = _q(
        "SELECT item_name, "
        "SUM(CASE WHEN sale_date > (SELECT MAX(sale_date) FROM v_sales)-30 THEN quantity ELSE 0 END) AS q30, "
        "SUM(CASE WHEN sale_date > (SELECT MAX(sale_date) FROM v_sales)-60 "
        "  AND sale_date <= (SELECT MAX(sale_date) FROM v_sales)-30 THEN quantity ELSE 0 END) AS q_prev "
        "FROM v_sales WHERE item_name IS NOT NULL GROUP BY item_name "
        "HAVING SUM(CASE WHEN sale_date > (SELECT MAX(sale_date) FROM v_sales)-60 THEN quantity ELSE 0 END) > 0 "
        "LIMIT 400"
    )
    rising, fading = [], []
    for r in momentum:
        q30, qp = _f(r, "q30"), _f(r, "q_prev")
        if q30 > 1.3 * qp and q30 - qp >= 5:
            rising.append({"item_name": r.get("item_name"), "q30": q30, "q_prev": qp})
        elif qp > 0 and q30 < 0.5 * qp and qp - q30 >= 5:
            fading.append({"item_name": r.get("item_name"), "q30": q30, "q_prev": qp})
    rising.sort(key=lambda x: x["q30"] - x["q_prev"], reverse=True)
    fading.sort(key=lambda x: x["q_prev"] - x["q30"], reverse=True)
    revenue_trend = _q(
        "SELECT period_month, gross_bhd FROM v_sales_by_period ORDER BY period_month DESC LIMIT 6"
    )
    return {
        "rising_count": len(rising),
        "fading_count": len(fading),
        "summary": (f"{len(rising)} items rising, {len(fading)} fading (last 30d vs prior 30d). "
                    f"Top riser: {rising[0]['item_name'] if rising else '-'}."),
        "rising": rising[:12],
        "fading": fading[:12],
        "revenue_trend": list(reversed(revenue_trend)),
    }


# ── Marketing agent (growth) ──────────────────────────────────────────────────

def marketing() -> dict:
    """Promo & push ideas: high-margin SKUs worth pushing, slow/overstock to clear with an
    offer, and the best sellers to bundle around. Deterministic inputs; phrasing is yours."""
    high_margin = _q(
        "SELECT sku_code, item_name, price_bhd, margin_pct FROM v_product_economics "
        "WHERE margin_pct IS NOT NULL AND margin_pct >= 30 ORDER BY margin_pct DESC LIMIT 12"
    )
    clear = _q(
        "SELECT item_name, current_stock, stock_value FROM v_stock_health "
        "WHERE status IN ('dead_stock','overstock') ORDER BY stock_value DESC LIMIT 12"
    )
    bestsellers = _q(
        "SELECT item_name, SUM(quantity) AS qty FROM v_sales "
        "WHERE sale_date > (SELECT MAX(sale_date) FROM v_sales)-90 AND item_name IS NOT NULL "
        "GROUP BY item_name ORDER BY qty DESC NULLS LAST LIMIT 8"
    )
    clear_val = sum(_f(r, "stock_value") for r in clear)
    return {
        "summary": (f"{len(high_margin)} high-margin SKUs to push · {len(clear)} slow lines to "
                    f"clear (BHD {clear_val:,.0f}) · {len(bestsellers)} best sellers to bundle."),
        "push_high_margin": high_margin,
        "clear_with_offer": clear,
        "bundle_around": bestsellers,
    }


# ── Catalog Watch (change detector) ───────────────────────────────────────────

def catalog_watch() -> dict:
    """What CHANGED in the catalogue: selling-price changes, purchase-cost changes, and —
    via the memory layer's item_keys diff — newly added / removed SKUs since the last run.
    This is the agent the daily upload triggers to tell you 'what's new/changed today'."""
    skus = _q("SELECT sku_code FROM v_price_list ORDER BY sku_code")
    sku_list = [str(r.get("sku_code")) for r in skus if r.get("sku_code")]
    price_changes = _q(
        "SELECT sku_code, item_name, prev_price_bhd, current_price_bhd, price_change_pct "
        "FROM v_price_change ORDER BY ABS(price_change_pct) DESC NULLS LAST LIMIT 25"
    )
    cost_changes = _q(
        "SELECT item_name, vendor, prev_cost_bhd, current_cost_bhd, cost_change_pct "
        "FROM v_cost_change WHERE ABS(cost_change_pct) >= 5 "
        "ORDER BY ABS(cost_change_pct) DESC NULLS LAST LIMIT 25"
    )
    return {
        "sku_count": len(sku_list),
        "item_keys": sku_list,        # memory layer diffs this -> new/removed SKUs vs last run
        "price_change_count": len(price_changes),
        "cost_change_count": len(cost_changes),
        "summary": (f"{len(sku_list)} active SKUs · {len(price_changes)} selling-price changes · "
                    f"{len(cost_changes)} purchase-cost changes (≥5%)."),
        "price_changes": price_changes,
        "cost_changes": cost_changes,
    }


# ── Vendor Sourcing (growth, web-tool) ────────────────────────────────────────

def vendor_sourcing() -> dict:
    """Find potential NEW suppliers/products for our fast-moving categories via web search.
    The only agent that reaches OUTSIDE our data — needs a free TAVILY_API_KEY."""
    from app.tools import web_search, web_search_enabled
    if not web_search_enabled():
        return {
            "enabled": False,
            "summary": ("Vendor sourcing is ready but needs a free web-search key — add "
                        "TAVILY_API_KEY (tavily.com) to .env to let me scout new suppliers."),
            "leads": [],
        }
    movers = _q(
        "SELECT item_name FROM v_stock_health WHERE status='urgent_out_of_stock' "
        "ORDER BY sold_90d DESC NULLS LAST LIMIT 5"
    )
    leads: list[dict] = []
    queries = [
        "wholesale mobile phone accessories suppliers distributor",
        "bulk USB type-c lightning cable manufacturer supplier",
        "power bank wholesale supplier distributor Middle East",
    ]
    for q in queries:
        for r in web_search(q, 3):
            leads.append({"query": q, **r})
    return {
        "enabled": True,
        "summary": f"{len(leads)} potential supplier leads found for our fast movers.",
        "fast_movers": [m.get("item_name") for m in movers],
        "leads": leads,
    }


# ── Demand forecast / reorder-point ───────────────────────────────────────────

def demand_forecast(lead_time_days: int = 21) -> dict:
    """Predicted stock-outs + an 'order by' date. days_cover = stock / avg_daily; order by =
    stock-out date minus lead time (default 21d, until shipment lead-times are tracked)."""
    rows = _q(
        "SELECT item_name, current_stock, sold_90d, avg_daily, days_cover, suggested_reorder_qty "
        "FROM v_stock_health WHERE avg_daily > 0 AND current_stock > 0 AND days_cover IS NOT NULL "
        "AND days_cover < 45 ORDER BY days_cover ASC LIMIT 40"
    )
    items, order_now = [], 0
    for r in rows:
        dc = _f(r, "days_cover")
        order_in = dc - lead_time_days  # days until we must place the order
        if order_in <= 7:
            order_now += 1
        items.append({
            "item_name": r.get("item_name"), "current_stock": _f(r, "current_stock"),
            "avg_daily": _f(r, "avg_daily"), "days_cover": dc,
            "order_by_days": round(order_in, 1),
            "suggested_reorder_qty": _f(r, "suggested_reorder_qty"),
        })
    return {
        "count": len(items), "order_now_count": order_now,
        "summary": (f"{len(items)} items run out within 45 days; {order_now} must be ordered THIS WEEK "
                    f"(>{lead_time_days}d lead time) to avoid a stock-out."),
        "items": items,
    }


# ── ABC / XYZ inventory classification ─────────────────────────────────────────

def abc_xyz() -> dict:
    """ABC by revenue Pareto (A=top 80%, B=next 15%, C=last 5%) × XYZ by demand variability
    (coefficient of variation of monthly qty: X<0.5 stable, Y<1.0, Z erratic). Drives policy:
    AX = protect stock; CZ = minimise."""
    rows = _q(
        "WITH item_rev AS (SELECT item_name, SUM(revenue_bhd) AS rev, SUM(quantity) AS qty "
        "  FROM v_sales WHERE item_name IS NOT NULL GROUP BY item_name), "
        "monthly AS (SELECT item_name, DATE_TRUNC('month', sale_date) AS m, SUM(quantity) AS q "
        "  FROM v_sales WHERE item_name IS NOT NULL GROUP BY item_name, DATE_TRUNC('month', sale_date)), "
        "cov AS (SELECT item_name, CASE WHEN AVG(q) > 0 THEN STDDEV_POP(q)/AVG(q) END AS cv "
        "  FROM monthly GROUP BY item_name), "
        "ranked AS (SELECT ir.item_name, ir.rev, c.cv, "
        "  SUM(ir.rev) OVER (ORDER BY ir.rev DESC) / NULLIF(SUM(ir.rev) OVER (),0) AS cum "
        "  FROM item_rev ir LEFT JOIN cov c ON c.item_name = ir.item_name) "
        "SELECT item_name, ROUND(rev::numeric,0) AS revenue_bhd, ROUND(cv::numeric,2) AS cv, "
        "  CASE WHEN cum <= 0.8 THEN 'A' WHEN cum <= 0.95 THEN 'B' ELSE 'C' END AS abc, "
        "  CASE WHEN cv IS NULL THEN '-' WHEN cv < 0.5 THEN 'X' WHEN cv < 1.0 THEN 'Y' ELSE 'Z' END AS xyz "
        "FROM ranked ORDER BY rev DESC LIMIT 200"
    )
    a = [r for r in rows if r.get("abc") == "A"]
    ax = [r for r in rows if r.get("abc") == "A" and r.get("xyz") == "X"]
    cz = [r for r in rows if r.get("abc") == "C" and r.get("xyz") == "Z"]
    return {
        "count": len(rows), "a_count": len(a), "ax_count": len(ax), "cz_count": len(cz),
        "summary": (f"{len(a)} A-items drive 80% of revenue; {len(ax)} are AX (high-value + steady → "
                    f"never run out); {len(cz)} are CZ (low-value + erratic → stock minimally / to order)."),
        "items": rows,
    }


# ── Dead-stock liquidation ─────────────────────────────────────────────────────

def deadstock_liquidation() -> dict:
    """Idle stock (no sale 90d+) with a suggested clearance markdown and the capital it frees.
    Markdown by age: >180d → 50%, >90d → 30%, never sold → 40%."""
    rows = _q(
        "SELECT item_name, current_stock, stock_value, days_since_sale FROM v_inventory_aging "
        "WHERE days_since_sale IS NULL OR days_since_sale > 90 ORDER BY stock_value DESC LIMIT 40"
    )
    items, trapped, freed = [], 0.0, 0.0
    for r in rows:
        val = _f(r, "stock_value")
        d = r.get("days_since_sale")
        md = 0.40 if d is None else (0.50 if _f(r, "days_since_sale") > 180 else 0.30)
        recover = val * (1 - md)
        trapped += val
        freed += recover
        items.append({"item_name": r.get("item_name"), "current_stock": _f(r, "current_stock"),
                      "stock_value_bhd": val, "days_since_sale": d,
                      "suggested_markdown_pct": int(md * 100), "est_recovery_bhd": round(recover, 1)})
    return {
        "count": len(items), "trapped_value_bhd": trapped, "est_recovery_bhd": freed,
        "summary": (f"{len(items)} dead-stock lines tie up BHD {trapped:,.0f}; a phased clearance could "
                    f"recover ~BHD {freed:,.0f} of working capital."),
        "items": items,
    }


# ── Customer win-back (lapsed accounts) ────────────────────────────────────────

def winback() -> dict:
    """Warm win-back list from the RFM/LTV model: customers scored 'lapsed' or
    'at_risk_high_value' (recently quiet but valuable), ranked by lifetime spend. Reads
    v_customer_ltv (Phase C) so the segmentation is consistent across the platform."""
    rows = _q(
        "SELECT customer_name, segment, monetary_bhd AS lifetime_bhd, recency_days AS days_since, "
        "last_order, rfm_total FROM v_customer_ltv "
        "WHERE segment IN ('at_risk_high_value','lapsed') "
        "ORDER BY (segment='at_risk_high_value') DESC, monetary_bhd DESC LIMIT 30"
    )
    at_risk = sum(_f(r, "lifetime_bhd") for r in rows)
    high = [r for r in rows if r.get("segment") == "at_risk_high_value"]
    return {
        "count": len(rows), "lifetime_value_bhd": at_risk, "high_value_count": len(high),
        "summary": (f"{len(rows)} customers to win back ({len(high)} high-value at-risk) — "
                    f"BHD {at_risk:,.0f} of lifetime spend to protect. Reach the high-value ones first."),
        "lapsed_customers": rows,
    }


# ── Credit exposure (receivables risk) ─────────────────────────────────────────

def credit_exposure() -> dict:
    """Where the receivables RISK sits: accounts with the largest aged (90+) debt and the
    concentration of exposure. (Adds true credit-limit breach once limits are loaded.)"""
    rows = _q(
        "SELECT account, outstanding_bhd, overdue_bhd, over_90_bhd, "
        "ROUND(100.0*over_90_bhd/NULLIF(outstanding_bhd,0),0) AS pct_over_90 "
        "FROM v_receivables WHERE outstanding_bhd > 0 "
        "ORDER BY over_90_bhd DESC NULLS LAST, outstanding_bhd DESC LIMIT 30"
    )
    total = sum(_f(r, "outstanding_bhd") for r in rows)
    aged = [r for r in rows if _f(r, "over_90_bhd") > 0]
    top_share = (_f(rows[0], "outstanding_bhd") / total * 100) if (rows and total) else 0.0
    return {
        "count": len(rows), "aged_count": len(aged),
        "over_90_total_bhd": sum(_f(r, "over_90_bhd") for r in rows),
        "summary": (f"{len(aged)} accounts carry 90+ day debt; top account = {top_share:.0f}% of the "
                    f"book. Tighten terms / hold supply on the most-aged before extending more credit."),
        "exposure": rows,
    }


# ── Working capital (cash tied up) ─────────────────────────────────────────────

def working_capital() -> dict:
    """Cash tied up in the business = receivables + inventory, and how much is 'stuck' (aged 90+
    debt + idle stock). Clearing the stuck portion releases working capital."""
    ar = _q("SELECT COALESCE(SUM(outstanding_bhd),0) AS ar, COALESCE(SUM(over_90_bhd),0) AS aged FROM v_receivables")
    stock = _q("SELECT COALESCE(SUM(balance_value_bhd),0) AS stock FROM v_current_stock")
    idle = _q("SELECT COALESCE(SUM(stock_value),0) AS idle FROM v_inventory_aging WHERE days_since_sale > 60")
    ar_total = _f(ar[0], "ar") if ar else 0.0
    ar_aged = _f(ar[0], "aged") if ar else 0.0
    stock_val = _f(stock[0], "stock") if stock else 0.0
    idle_val = _f(idle[0], "idle") if idle else 0.0
    tied, stuck = ar_total + stock_val, ar_aged + idle_val
    return {
        "working_capital_bhd": tied,
        "releasable_bhd": stuck,
        "summary": (f"BHD {tied:,.0f} tied up — BHD {ar_total:,.0f} receivables (BHD {ar_aged:,.0f} aged 90+) "
                    f"+ BHD {stock_val:,.0f} inventory (BHD {idle_val:,.0f} idle >60d). Clearing the aged debt "
                    f"+ dead stock would release ~BHD {stuck:,.0f}."),
        "breakdown": [
            {"component": "Receivables", "value_bhd": ar_total, "stuck_bhd": ar_aged},
            {"component": "Inventory", "value_bhd": stock_val, "stuck_bhd": idle_val},
        ],
        "top_debtors": _q("SELECT account, outstanding_bhd, over_90_bhd FROM v_receivables ORDER BY outstanding_bhd DESC LIMIT 5"),
        "top_idle_stock": _q("SELECT item_name, stock_value, days_since_sale FROM v_inventory_aging WHERE days_since_sale > 60 ORDER BY stock_value DESC LIMIT 5"),
    }


# ── Pricing optimization (mispricing signals) ─────────────────────────────────

def pricing_optimization() -> dict:
    """Mispricing from price-vs-cost margin × 90-day velocity (joined on SKU): thin-margin
    fast-movers to RAISE, fat-margin non-movers to CUT/clear. Heuristic review candidates."""
    rows = _q(
        "WITH vel AS (SELECT sku_code, SUM(quantity) AS sold_90d FROM v_sales "
        "  WHERE sale_date > (SELECT MAX(sale_date) FROM v_sales)-90 AND sku_code IS NOT NULL GROUP BY sku_code) "
        "SELECT e.sku_code, e.item_name, e.price_bhd, e.cost_bhd, e.margin_pct, COALESCE(v.sold_90d,0) AS sold_90d "
        "FROM v_product_economics e LEFT JOIN vel v ON v.sku_code = e.sku_code "
        "WHERE e.margin_pct IS NOT NULL ORDER BY e.margin_pct ASC LIMIT 200"
    )
    raise_p = [r for r in rows if _f(r, "margin_pct") < 15 and _f(r, "sold_90d") >= 10]
    cut_p = [r for r in rows if _f(r, "margin_pct") > 45 and _f(r, "sold_90d") == 0]
    return {
        "raise_count": len(raise_p), "cut_count": len(cut_p),
        "summary": (f"{len(raise_p)} fast-movers on thin margin (<15%) — room to RAISE price; "
                    f"{len(cut_p)} high-margin items not selling — consider a price CUT or clearance."),
        "raise_price": raise_p[:15], "cut_or_clear": cut_p[:15],
    }


# ── Purchase order tracking + cost comparison across orders ───────────────────

def purchase_tracker() -> dict:
    """Order tracking + per-item COST COMPARISON across POs — so you never manually compare old
    orders again. Surfaces items whose rate changed vs the PREVIOUS order, recent orders, and what
    is still on order (not yet received)."""
    cost_changes = _q(
        "SELECT item_code, description, prev_rate_bhd, current_rate_bhd, rate_delta_bhd, "
        "rate_change_pct, prev_ordered, last_ordered FROM v_po_cost_change "
        "WHERE rate_change_pct IS NOT NULL ORDER BY ABS(rate_change_pct) DESC LIMIT 25"
    )
    recent = _q(
        "SELECT po_no, po_date, vendor, COUNT(*) AS lines, ROUND(SUM(gross_bhd)::numeric,3) AS value_bhd "
        "FROM purchase_orders GROUP BY po_no, po_date, vendor ORDER BY po_date DESC LIMIT 10"
    )
    on_order = _q(
        "SELECT po_no, code, qty_ordered, rate_bhd, po_date FROM v_purchase_lifecycle "
        "WHERE status = 'on_order' ORDER BY po_date DESC LIMIT 20"
    )
    up = [c for c in cost_changes if _f(c, "rate_change_pct") > 0]
    down = [c for c in cost_changes if _f(c, "rate_change_pct") < 0]
    return {
        "cost_change_count": len(cost_changes), "on_order_count": len(on_order),
        "summary": (f"{len(recent)} order(s) tracked · {len(cost_changes)} items changed price vs the "
                    f"previous order ({len(up)} up, {len(down)} down) · {len(on_order)} line(s) still on order."),
        "cost_changes": cost_changes, "recent_orders": recent, "on_order": on_order,
    }


# ── Reorder proposal (drafts an order to review) ──────────────────────────────

def reorder_proposal(target_days_cover: int = 45, lead_time_days: int = 21) -> dict:
    """A DRAFTED purchase order to review: items running low/out (velocity-driven), each with a
    suggested qty to cover ~`target_days_cover` days, enriched with the LAST vendor + rate from your
    PO history (fuzzy item match), grouped by vendor and costed. The agent initiates the order from
    actual sales; you review, adjust, and raise it with the vendor (advise-not-act)."""
    # One query: reorder candidates LEFT-JOINed to their most recent PO line (match stays in-DB, so
    # no name interpolation). last_vendor / last_rate are null until POs are uploaded — still proposes.
    rows = _q(
        "WITH cand AS ("
        "  SELECT item_name, current_stock, sold_30d, sold_90d, avg_daily, days_cover, status "
        "  FROM v_stock_health WHERE status IN ('urgent_out_of_stock','reorder_soon') AND sold_30d > 0 "
        "  ORDER BY (status='urgent_out_of_stock') DESC, days_cover ASC NULLS FIRST LIMIT 60) "
        "SELECT c.item_name, c.current_stock, c.sold_30d, c.sold_90d, c.avg_daily, c.days_cover, c.status, "
        "       lp.vendor AS last_vendor, lp.rate_bhd AS last_rate_bhd, lp.po_date AS last_ordered "
        "FROM cand c LEFT JOIN LATERAL ("
        "  SELECT vendor, rate_bhd, po_date FROM v_po_item po "
        # stock names embed the vendor code (e.g. '... (VFAN) X12'); match the code as a token,
        # then fall back to description text. v_po_item = one blended unit rate per code per PO.
        "  WHERE c.item_name ~* ('(^|[^a-z0-9])' || po.code || '([^a-z0-9]|$)') "
        "     OR po.description ILIKE '%' || c.item_name || '%' "
        "  ORDER BY po.po_date DESC NULLS LAST LIMIT 1) lp ON TRUE"
    )
    # ── Money lookups (keyed by leading product code) so each line carries the full picture ──
    from app.settings import all_settings, rmb_to_bhd
    RMB_BHD = rmb_to_bhd()                              # ¥→BHD via the USD leg (owner's sheet)
    FREIGHT = 1 + all_settings()["landing_vat_pct"]     # landing + VAT uplift on base cost
    vfan = {x["model"]: x for x in (_q(
        "SELECT model, latest_rmb, change_pct FROM v_supplier_price_history") or [])}
    landed = {x["code"]: x["c"] for x in (_q(
        "SELECT SPLIT_PART(sku_code,' ',1) AS code, ROUND(AVG(landed_cost_bhd)::numeric,4) AS c "
        "FROM mrn_landed_costs WHERE landed_cost_bhd > 0 GROUP BY 1") or [])}
    sell_book = {x["code"]: x["s"] for x in (_q(
        "SELECT sku_code AS code, MAX(rate_bhd) AS s FROM selling_prices "
        "WHERE price_book='MA_base' AND warehouse_name IS NULL AND rate_bhd > 0 GROUP BY sku_code") or [])}

    NO_VENDOR = "(vendor to confirm)"
    lines: list[dict] = []
    for r in rows:
        avg = _f(r, "avg_daily")
        stock = _f(r, "current_stock")
        qty = max(int(round(avg * target_days_cover - stock)), 0) or max(int(round(_f(r, "sold_30d"))), 1)
        rate = r.get("last_rate_bhd")
        urgent = r.get("status") == "urgent_out_of_stock"
        dc = r.get("days_cover")
        code = (r.get("item_name") or "").split(" ")[0].upper()

        v = vfan.get(code)
        vfan_rmb = float(v["latest_rmb"]) if v and v.get("latest_rmb") else None
        vfan_chg = float(v["change_pct"]) if v and v.get("change_pct") is not None else None
        # cost basis: real landed cost > VFAN ¥ price (× rate × freight) > last PO rate
        cost = (landed.get(code)
                or (round(vfan_rmb * RMB_BHD * FREIGHT, 4) if vfan_rmb else None)
                or rate)
        cost = float(cost) if cost is not None else None
        sell = float(sell_book[code]) if sell_book.get(code) else None
        margin = round((sell - cost) / sell * 100, 1) if (sell and cost) else None
        est = round(qty * cost, 3) if cost is not None else None
        cover_at_qty = round((stock + qty) / avg, 0) if avg else None
        flags = []
        if vfan_chg is not None and abs(vfan_chg) >= 5:
            flags.append("price_up" if vfan_chg > 0 else "price_down")
        if margin is not None and margin < 20:
            flags.append("thin_margin")
        if cover_at_qty is not None and cover_at_qty > 120:
            flags.append("overstock_qty")

        lines.append({
            "item_name": r.get("item_name"),
            "current_stock": stock,
            "avg_daily": avg,
            "days_cover": dc,
            "suggested_qty": qty,
            "cover_at_qty_days": cover_at_qty,
            "last_vendor": r.get("last_vendor") or None,
            "last_rate_bhd": rate,
            "last_ordered": r.get("last_ordered"),
            "vfan_rmb": vfan_rmb,
            "vfan_change_pct": vfan_chg,
            "cost_bhd": cost,
            "sell_bhd": sell,
            "margin_pct": margin,
            "est_cost_bhd": est,
            "flags": flags,
            "urgency": "urgent" if urgent else "soon",
            "reason": (f"Out of stock — sells ~{avg:.1f}/day, recover lost sales" if urgent
                       else f"{dc:g} days' cover left (reorder point {lead_time_days}d)"),
        })
    # Group into one proposed order per vendor so each can be raised in a single PO.
    by_vendor: dict[str, dict] = {}
    for ln in lines:
        v = ln["last_vendor"] or NO_VENDOR
        g = by_vendor.setdefault(v, {"vendor": v, "lines": 0, "est_total_bhd": 0.0, "items": []})
        g["lines"] += 1
        g["est_total_bhd"] = round(g["est_total_bhd"] + (ln["est_cost_bhd"] or 0), 3)
        g["items"].append(ln)
    for g in by_vendor.values():
        head = f"Proposed order — {g['vendor']} ({g['lines']} item(s), est. BHD {g['est_total_bhd']:,.3f}):"
        body = "\n".join(
            f"  • {i['item_name']} ×{i['suggested_qty']}"
            + (f" @ BHD {i['cost_bhd']:,.3f} = BHD {i['est_cost_bhd']:,.3f}"
               if (i.get("cost_bhd") is not None and i.get("est_cost_bhd") is not None) else "  (rate to confirm)")
            for i in g["items"])
        g["draft_message"] = head + "\n" + body
    vendors = sorted(by_vendor.values(), key=lambda x: (x["vendor"] == NO_VENDOR, -x["est_total_bhd"]))
    est_total = round(sum(g["est_total_bhd"] for g in vendors), 3)
    urgent_n = sum(1 for ln in lines if ln["urgency"] == "urgent")
    return {
        "count": len(lines),
        "urgent_count": urgent_n,
        "vendor_count": len([v for v in vendors if v["vendor"] != NO_VENDOR]),
        "est_total_bhd": est_total,
        "summary": (f"Proposed order: {len(lines)} item(s) to reorder ({urgent_n} urgent) across "
                    f"{len(vendors)} vendor group(s), est. BHD {est_total:,.0f}. Review and raise."),
        "lines": lines,
        "by_vendor": vendors,
    }


# ── Procurement status (pipeline + stuck-order nudges) ────────────────────────

def procurement_status() -> dict:
    """Procurement pipeline status: open orders + the ones STUCK past their stage SLA (e.g. raised
    with the vendor but no advance paid in 5 days), each with the suggested next action. Feeds the
    morning briefing so orders never stall silently between stages."""
    from app import procurement
    b = procurement.board()
    stuck = [r for r in b["orders"] if r.get("is_stuck")]
    next_action = {
        "proposed": "Review and confirm what to order",
        "reviewed": "Raise the order with the vendor & get it confirmed",
        "raised": "Pay the vendor 100% to lock the order",
        "paid": "Raise the PO in Focus & follow up on the shipment",
        "received": "Reconcile the MRN vs PO, then close the order",
    }
    nudges = [{
        "ref": r.get("ref"), "title": r.get("title"), "vendor": r.get("vendor"),
        "stage": r.get("stage"), "days_in_stage": r.get("days_in_stage"),
        "est_value_bhd": _f(r, "est_value_bhd"),
        "next_action": next_action.get(r.get("stage"), "Follow up"),
    } for r in stuck]
    # POs raised but not fully received (goods on the way); overdue ones need chasing
    try:
        from app.orders import pending_orders
        pending = pending_orders()
    except Exception:  # noqa: BLE001
        pending = []
    overdue = [p for p in pending if p.get("overdue")]
    parts = []
    if b["open_count"]:
        parts.append(f"{b['open_count']} open order(s) ~BHD {b['pipeline_value_bhd']:,.0f}; {len(stuck)} stuck")
    if pending:
        parts.append(f"{len(pending)} awaiting receipt ({len(overdue)} overdue)")
    return {
        "open_count": b["open_count"], "stuck_count": len(stuck),
        "pipeline_value_bhd": b["pipeline_value_bhd"],
        "pending_count": len(pending), "overdue_count": len(overdue),
        "summary": ("; ".join(parts) + "." if parts else "No open procurement orders to track."),
        "nudges": nudges,
        "pending_orders": pending,
        "pipeline": b["orders"],
    }


# ── Consolidated agents (NOW#3) ───────────────────────────────────────────────
# Two field-union merges that strictly expose every predecessor field, so callers, the
# escalation rules and the orchestrator keep working. Retired names still resolve via
# AGENT_ALIASES below (schedules / n8n / tool calls never break).

def risk_watch() -> dict:
    """Risk & integrity in one pass — merges the former 'anomaly' (audit: below-cost, negative
    & dead stock) and 'fraud' (leakage: sold-below-cost, negative stock, wide price spreads)."""
    a = anomaly_scan()
    f = fraud_scan()
    below = a.get("priced_below_cost") or []
    dead = a.get("dead_stock") or []
    neg = a.get("negative_stock") or f.get("negative_stock") or []
    spread = f.get("price_spread") or []
    n = len(below) + len(neg) + len(dead) + len(spread)
    return {
        "anomaly_count": n,
        "summary": (f"{len(below)} below cost · {len(neg)} negative-stock · {len(dead)} dead-stock · "
                    f"{len(spread)} wide price-spread (leakage?)."),
        "priced_below_cost": below,          # consumed by escalation._r_below_cost
        "sold_below_cost": f.get("sold_below_cost") or [],
        "negative_stock": neg,
        "dead_stock": dead,
        "price_spread": spread,
    }


def purchase_insights() -> dict:
    """Purchasing in one view — merges 'procurement' (what to reorder, cost vs last receipt,
    recent receipts) and 'purchase_tracker' (PO cost comparison across orders + what's on order)."""
    p = procurement()
    t = purchase_tracker()
    # per-order PO≠MRN reconciliation + margin-on-arrival on the latest orders
    try:
        from app.orders import order_attention
        attention = order_attention()
    except Exception:  # noqa: BLE001
        attention = []
    att_note = f" · {len(attention)} order(s) need a look (PO vs MRN / thin margin)" if attention else ""
    return {
        "reorder_count": p.get("reorder_count", 0),
        "cost_increase_count": p.get("cost_increase_count", 0),
        "on_order_count": t.get("on_order_count", 0),
        "cost_change_count": t.get("cost_change_count", 0),
        "attention_count": len(attention),
        "summary": (f"{p.get('reorder_count', 0)} to reorder · {p.get('cost_increase_count', 0)} receipts "
                    f"cost up >5% · {t.get('cost_change_count', 0)} PO price changes · "
                    f"{t.get('on_order_count', 0)} line(s) on order{att_note}."),
        "orders_attention": attention,
        "to_reorder": p.get("to_reorder") or [],
        "cost_increases": p.get("cost_increases") or [],
        "recent_purchases": p.get("recent_purchases") or [],
        "cost_changes": t.get("cost_changes") or [],
        "recent_orders": t.get("recent_orders") or [],
        "on_order": t.get("on_order") or [],
    }


# ── Growth agents (NEXT) ──────────────────────────────────────────────────────

def cross_sell() -> dict:
    """Market-basket affinity: which products sell TOGETHER in the same invoice. Drives bundle
    offers + 'customers who bought X also buy Y' attach suggestions — pure incremental revenue on
    orders you already win. Co-occurrence on the invoice grain of v_sales."""
    pairs = _q(
        "SELECT a.item_name AS item_a, b.item_name AS item_b, "
        "COUNT(DISTINCT a.invoice_no) AS together, "
        "ROUND(SUM(b.revenue_bhd)::numeric, 3) AS attach_revenue_bhd "
        "FROM v_sales a JOIN v_sales b "
        "  ON a.invoice_no = b.invoice_no AND a.item_name < b.item_name "
        "WHERE a.item_name IS NOT NULL AND b.item_name IS NOT NULL "
        "GROUP BY a.item_name, b.item_name HAVING COUNT(DISTINCT a.invoice_no) >= 3 "
        "ORDER BY together DESC, attach_revenue_bhd DESC LIMIT 20"
    )
    items = [{
        "item_a": p.get("item_a"), "item_b": p.get("item_b"),
        "together": int(_f(p, "together")),
        "attach_revenue_bhd": _f(p, "attach_revenue_bhd"),
        "suggestion": f"Bundle '{p.get('item_a')}' with '{p.get('item_b')}' — bought together "
                      f"{int(_f(p, 'together'))} times.",
    } for p in pairs]
    # Bundle-offer targets: the champion/loyal accounts worth proactively pitching these bundles to
    # (highest-value, still-active), from the RFM model (Phase C).
    targets = _q(
        "SELECT customer_name, segment, monetary_bhd FROM v_customer_ltv "
        "WHERE segment IN ('champion','loyal') ORDER BY monetary_bhd DESC LIMIT 10"
    )
    return {
        "count": len(items),
        "summary": (f"{len(items)} strong cross-sell pairs found — pitch these bundles to your "
                    f"{len(targets)} top active accounts to lift basket size." if items
                    else "Not enough repeat baskets yet to find reliable cross-sell pairs."),
        "pairs": items,
        "bundle_targets": targets,
    }


def vendor_scorecard() -> dict:
    """Vendor performance: spend, order count, recency and cost stability per supplier — so you
    negotiate with the ones whose costs are creeping and lean on the stable, reliable ones."""
    spend = _q(
        "SELECT vendor, COUNT(*) AS lines, ROUND(SUM(cost_bhd)::numeric, 3) AS spend_bhd, "
        "MAX(purchased_on) AS last_order FROM v_purchase_history WHERE vendor IS NOT NULL "
        "GROUP BY vendor ORDER BY spend_bhd DESC NULLS LAST LIMIT 25"
    )
    creep = _q(
        "SELECT vendor, COUNT(*) AS items_up, ROUND(AVG(cost_change_pct)::numeric, 1) AS avg_cost_up_pct "
        "FROM v_cost_change WHERE cost_change_pct > 0 AND vendor IS NOT NULL "
        "GROUP BY vendor ORDER BY avg_cost_up_pct DESC LIMIT 25"
    )
    creep_by = {str(r.get("vendor")): r for r in creep}
    rows = []
    for s in spend:
        v = str(s.get("vendor"))
        cu = creep_by.get(v, {})
        rows.append({
            "vendor": v, "lines": int(_f(s, "lines")), "spend_bhd": _f(s, "spend_bhd"),
            "last_order": s.get("last_order"),
            "items_cost_up": int(_f(cu, "items_up")) if cu else 0,
            "avg_cost_up_pct": _f(cu, "avg_cost_up_pct") if cu else 0.0,
        })
    rising = [r for r in rows if r["avg_cost_up_pct"] > 5]
    return {
        "count": len(rows), "rising_count": len(rising),
        "summary": (f"{len(rows)} vendors scored · {len(rising)} with costs creeping >5% — "
                    f"renegotiate or re-source those before the next order."),
        "vendors": rows,
        "cost_creeping": rising,
    }


def trend_radar() -> dict:
    """What's HEATING UP — products whose recent sales are ACCELERATING (last-30d run-rate vs the
    90-day baseline), cross-referenced with stock position so you restock what's rising BEFORE it
    runs out. Optional free external signals (web search) when a TAVILY_API_KEY is set. Advise-only."""
    rising = _q(
        "SELECT item_name, sold_30d, sold_90d, current_stock, days_cover, status, "
        "ROUND((sold_30d / NULLIF(sold_90d / 3.0, 0))::numeric, 2) AS momentum "
        "FROM v_stock_health WHERE sold_90d > 0 AND sold_30d > 0 "
        "AND sold_30d > 1.2 * (sold_90d / 3.0) "
        "ORDER BY momentum DESC, sold_30d DESC LIMIT 20"
    )
    act_now = [r for r in rising if str(r.get("status")) in ("urgent_out_of_stock", "low_stock")]
    external: list[dict] = []
    try:  # best-effort, bounded; only if Tavily is configured
        from app.tools import web_search, web_search_enabled
        if web_search_enabled():
            external = [{"title": h.get("title"), "url": h.get("url")}
                        for h in web_search("trending phone accessories 2026 GCC Bahrain", max_results=4)]
    except Exception:  # noqa: BLE001
        pass
    # YouTube video signals — accessory unboxing/review volume (free quota: 10,000 units/day).
    yt_signals: list[dict] = []
    try:
        from app.config import settings as _s
        import urllib.request, json as _json
        if _s.youtube_api_key:
            _url = (
                "https://www.googleapis.com/youtube/v3/search"
                f"?part=snippet&q=mobile+accessories+2026+review&type=video"
                f"&order=viewCount&maxResults=5&key={_s.youtube_api_key}"
            )
            with urllib.request.urlopen(_url, timeout=6) as _r:
                _data = _json.loads(_r.read())
            yt_signals = [
                {"title": i["snippet"]["title"], "channel": i["snippet"]["channelTitle"]}
                for i in _data.get("items", [])
            ]
    except Exception:  # noqa: BLE001
        pass
    note = "" if (external or yt_signals) else " (add TAVILY_API_KEY / YOUTUBE_API_KEY for external trend signals)"
    return {
        "count": len(rising), "act_now_count": len(act_now),
        "summary": (f"{len(rising)} products gaining momentum; {len(act_now)} are rising AND low on "
                    f"stock — restock these now to ride the trend.{note}"),
        "rising": rising,
        "stock_up_now": act_now,
        "external_signals": external,
        "youtube_signals": yt_signals,
    }


def lead_gen() -> dict:
    """Drafts a prioritised B2B call/visit list from the highest-fit NEW leads (discovered free
    from OpenStreetMap), each with a ready opener. Advise-only — the team calls/visits, the agent
    just hands them the list, best first."""
    try:
        from app.leadgen import ATTRIBUTION, list_leads, pipeline
    except Exception:  # noqa: BLE001
        return {"count": 0, "summary": "Lead-gen module unavailable.", "leads": []}
    leads = list_leads(status="new", limit=30)
    pipe = pipeline()
    items = [{
        "name": l.get("name"), "category": l.get("category"), "area": l.get("area"),
        "phone": l.get("phone"), "website": l.get("website"), "fit_score": l.get("fit_score"),
        "opener": (f"Hi {l.get('name')}, this is YQ Bahrain — we wholesale fast-moving mobile "
                   f"accessories (power banks, cables, earbuds) to retailers. Can I share our top "
                   f"sellers and trade prices?"),
    } for l in leads]
    return {
        "count": len(items), "pipeline_total": pipe.get("total", 0),
        "summary": (f"{len(items)} high-fit new leads ready to contact (pipeline: "
                    f"{pipe.get('total', 0)} total). Work the top-scored first." if items
                    else "No new leads yet — open the Leads page and run discovery to find B2B buyers."),
        "leads": items,
        "attribution": ATTRIBUTION,
    }


def research_scout() -> dict:
    """Scouts the web for new product ideas, competitor moves and useful free sources, and DRAFTS
    the findings for a human to review. ADVISE-ONLY — it never adopts anything automatically. Needs
    a free TAVILY_API_KEY; degrades to a clear 'not configured' note otherwise."""
    try:
        from app.tools import web_search, web_search_enabled
    except Exception:  # noqa: BLE001
        return {"count": 0, "summary": "Research tools unavailable.", "findings": []}
    if not web_search_enabled():
        return {"count": 0,
                "summary": "Web research not configured — add a free TAVILY_API_KEY to switch the scout on.",
                "findings": []}
    queries = [
        "best selling phone accessories 2026 GCC",
        "new mobile accessory product launches 2026",
        "phone accessory wholesale trends Bahrain Gulf",
    ]
    findings = []
    for qy in queries:
        for h in web_search(qy, max_results=3):
            findings.append({"topic": qy, "title": h.get("title"), "url": h.get("url"),
                             "snippet": (h.get("content") or "")[:200]})
    return {
        "count": len(findings),
        "summary": (f"{len(findings)} web signals on new products, trends & competitors — review and "
                    f"decide what to act on (the scout advises, you choose)."),
        "findings": findings,
    }


def ops_sentinel_agent() -> dict:
    """Phase F — platform self-monitoring (thin wrapper so it registers like any agent)."""
    from app.ops_sentinel import ops_sentinel
    return ops_sentinel()


def price_drift() -> dict:
    """Price Sentry — SKUs whose LANDED COST has crept up (>=5%) but whose SELLING PRICE hasn't
    followed, so margin is silently eroding. The real-data equivalent of 'competitor price watch':
    we can't see competitors, but we CAN catch our own cost/price drift before it eats profit.
    Cross-refs v_cost_change (cost up) against v_price_change (did price move?) by leading code."""
    DRIFT_PCT = 5.0
    cost_up = _q(
        "SELECT item_name, vendor, current_cost_bhd, prev_cost_bhd, cost_change_pct, last_bought_on "
        f"FROM v_cost_change WHERE cost_change_pct >= {DRIFT_PCT} ORDER BY cost_change_pct DESC LIMIT 60")
    # codes whose selling price DID move up recently (so they're not drifting)
    priced_up = {r["code"] for r in _q(
        "SELECT SPLIT_PART(item_name,' ',1) AS code FROM v_price_change WHERE price_change_pct > 0") if r.get("code")}
    drifting = []
    for r in cost_up:
        code = str(r.get("item_name") or "").split(" ")[0]
        if code in priced_up:
            continue  # price already responded to the cost rise
        drifting.append({
            "item_name": r.get("item_name"), "code": code, "vendor": r.get("vendor"),
            "current_cost_bhd": _f(r, "current_cost_bhd"), "prev_cost_bhd": _f(r, "prev_cost_bhd"),
            "cost_change_pct": _f(r, "cost_change_pct"), "last_bought_on": r.get("last_bought_on"),
        })
    top = drifting[:25]
    return {
        "count": len(drifting), "cost_change_count": len(cost_up),
        "summary": (f"{len(drifting)} SKU(s) had landed cost rise ≥{DRIFT_PCT:.0f}% with NO matching "
                    f"price increase — margin is eroding. Review and reprice." if drifting
                    else "No silent cost/price drift — selling prices have kept pace with landed costs."),
        "drifting": top,
    }


def returns_investigator() -> dict:
    """Returns-Investigator — ranks SKUs by return rate (a quality-failure signal), aggregates the
    blame by vendor, and cross-checks per-salesman leakage. A batch of one product coming back at a
    high rate is a QUALITY problem to flag before more of it ships. Reads v_return_rates (Phase C)."""
    HIGH_RATE = 10.0
    rows = _q(
        "SELECT item_name, code, vendor, ret_qty, sold_qty, return_rate_pct "
        "FROM v_return_rates WHERE ret_qty > 0 ORDER BY return_rate_pct DESC LIMIT 40")
    high = [r for r in rows if _f(r, "return_rate_pct") >= HIGH_RATE]
    # vendor roll-up: which supplier's goods come back most
    by_vendor: dict[str, dict] = {}
    for r in rows:
        v = r.get("vendor") or "unknown"
        agg = by_vendor.setdefault(v, {"vendor": v, "ret_qty": 0.0, "sold_qty": 0.0, "skus": 0})
        agg["ret_qty"] += _f(r, "ret_qty"); agg["sold_qty"] += _f(r, "sold_qty"); agg["skus"] += 1
    vendors = sorted(
        ({**a, "return_rate_pct": round(100.0 * a["ret_qty"] / a["sold_qty"], 2) if a["sold_qty"] else 0.0}
         for a in by_vendor.values()), key=lambda x: x["return_rate_pct"], reverse=True)
    # per-salesman leakage (shortage/unexplained) — ties into the van-stock recon
    leakage = _q(
        "SELECT salesman, shortage_qty, shortage_value_bhd, unexplained_qty FROM v_salesman_stock_recon "
        "WHERE shortage_qty > 0 ORDER BY shortage_value_bhd DESC NULLS LAST LIMIT 10")
    return {
        "count": len(high), "flagged_skus": len(high),
        "high_return_items": high,
        "by_vendor": vendors[:10],
        "salesman_leakage": leakage,
        "summary": (f"{len(high)} SKU(s) returning at ≥{HIGH_RATE:.0f}% — likely quality issues. "
                    f"Worst: {high[0]['code']} at {high[0]['return_rate_pct']}%. Flag the batch/vendor "
                    f"before reordering." if high
                    else "No SKU is returning at a worrying rate."),
    }


# Retired agent name → its successor. run_agent() resolves these so existing schedules,
# n8n flows, tool calls and audit history keep working after consolidation.
AGENT_ALIASES: dict[str, str] = {
    "anomaly": "risk_watch", "fraud": "risk_watch",
    "procurement": "purchase_insights", "purchase_tracker": "purchase_insights",
}


AGENTS: dict[str, AgentSpec] = {
    "collections": AgentSpec("collections", "Overdue receivables + drafted reminder messages", collections),
    "inventory": AgentSpec("inventory", "Velocity-aware reorder (urgent out-of-stock first)", inventory_reorder),
    "margin": AgentSpec("margin", "Negative & thin-margin products", margin_guardian),
    "sales_insights": AgentSpec("sales_insights", "Monthly sales trend (MoM) + top customers", sales_insights),
    "sales_push": AgentSpec("sales_push", "Targeted push lists: slow stock matched to the customers who buy that category, per salesman, with clearance pricing", sales_push),
    "sales_outreach": AgentSpec("sales_outreach", "AI sales agent (drafts): customers due to reorder + EN/AR WhatsApp messages with the catalog link", sales_outreach, category="growth"),
    "growth_plan": AgentSpec("growth_plan", "The weekly growth plan: one ranked list of this week's money moves with BHD at stake", growth_plan, category="growth", in_brief=False),
    "customer_health": AgentSpec("customer_health", "Named customers with declining spend (churn risk)", customer_health),
    "cashflow": AgentSpec("cashflow", "Receivables aging buckets + debtor concentration", cashflow_forecast),
    "risk_watch": AgentSpec("risk_watch", "Risk & integrity: below-cost, negative & dead stock, discount/price-spread leakage", risk_watch),
    "inventory_aging": AgentSpec("inventory_aging", "On-hand stock idle by days since last sale", inventory_aging),
    "salesman_performance": AgentSpec("salesman_performance", "Per-salesman value+volume + B2C/B2B", salesman_performance),
    "purchase_insights": AgentSpec("purchase_insights", "Purchasing: what to reorder, cost vs last receipt, PO cost comparison across orders + what's on order", purchase_insights),
    "salesman_stock_recon": AgentSpec("salesman_stock_recon", "Reconcile stock issued to each salesman vs sold/returned/on-hand; flags shortages & leakage", salesman_stock_recon),
    "trend": AgentSpec("trend", "Rising vs fading products (momentum) + revenue trend", trend_scan, category="growth"),
    "marketing": AgentSpec("marketing", "Promo ideas: high-margin to push, slow stock to clear, bundles", marketing, category="growth"),
    "catalog_watch": AgentSpec("catalog_watch", "What changed: new SKUs, selling-price & purchase-cost changes", catalog_watch),
    "vendor_sourcing": AgentSpec("vendor_sourcing", "Scout new suppliers/products via web search (needs Tavily key)", vendor_sourcing, category="growth", in_brief=False),
    "demand_forecast": AgentSpec("demand_forecast", "Predicted stock-outs + 'order by' date (lead-time aware)", demand_forecast),
    "abc_xyz": AgentSpec("abc_xyz", "ABC (revenue Pareto) × XYZ (demand variability) inventory classification", abc_xyz),
    "deadstock_liquidation": AgentSpec("deadstock_liquidation", "Idle stock + suggested clearance markdown + capital freed", deadstock_liquidation, category="growth"),
    "winback": AgentSpec("winback", "Lapsed customers (warm re-engagement list, by lifetime value)", winback, category="growth"),
    "credit_exposure": AgentSpec("credit_exposure", "Receivables risk: aged-debt accounts + exposure concentration", credit_exposure),
    "working_capital": AgentSpec("working_capital", "Cash tied up in receivables + inventory; what's releasable", working_capital),
    "pricing_optimization": AgentSpec("pricing_optimization", "Mispriced SKUs: thin-margin fast-movers to raise, fat-margin non-movers to cut", pricing_optimization, category="growth"),
    "reorder_proposal": AgentSpec("reorder_proposal", "Drafts a purchase order to review: what to reorder + qty + last vendor/rate, grouped & costed by vendor", reorder_proposal),
    "procurement_status": AgentSpec("procurement_status", "Procurement pipeline status + nudges for orders stuck past their stage SLA", procurement_status),
    "cross_sell": AgentSpec("cross_sell", "Market-basket affinity: products bought together → bundle & attach suggestions to lift basket size", cross_sell, category="growth"),
    "vendor_scorecard": AgentSpec("vendor_scorecard", "Vendor performance: spend, recency & cost-stability per supplier; flags cost creep to renegotiate", vendor_scorecard),
    "trend_radar": AgentSpec("trend_radar", "What's heating up: accelerating SKUs cross-referenced with stock → restock rising items before they run out (+ optional web signals)", trend_radar, category="growth"),
    "lead_gen": AgentSpec("lead_gen", "Free B2B lead-gen: prioritised call/visit list of new retailers (from OpenStreetMap) with openers — find new buyers", lead_gen, category="growth"),
    "research_scout": AgentSpec("research_scout", "Web scout (advise-only): new product ideas, competitor moves & trends drafted for review — needs a free Tavily key", research_scout, category="growth", in_brief=False),
    "price_drift": AgentSpec("price_drift", "Price Sentry: landed cost rose but selling price didn't — silent margin erosion to reprice", price_drift),
    "returns_investigator": AgentSpec("returns_investigator", "Returns-Investigator: SKUs returning at high rates (quality signal) + vendor & salesman roll-up", returns_investigator),
    "ops_sentinel": AgentSpec("ops_sentinel", "Platform self-monitor: ingest/agent/event health + drafts KB articles for repeated unanswered questions", ops_sentinel_agent, in_brief=False),
}

# Org-map grouping (CEO → departments → agents). Used by the Agents page; default = Operations.
_DEPARTMENTS: dict[str, str] = {
    "collections": "Finance", "cashflow": "Finance", "credit_exposure": "Finance", "margin": "Finance",
    "working_capital": "Finance",
    "inventory": "Supply", "inventory_aging": "Supply", "purchase_insights": "Supply",
    "demand_forecast": "Supply", "deadstock_liquidation": "Supply", "catalog_watch": "Supply",
    "abc_xyz": "Supply", "reorder_proposal": "Supply",
    "procurement_status": "Supply",
    "sales_insights": "Sales & Growth", "sales_push": "Sales & Growth", "customer_health": "Sales & Growth",
    "sales_outreach": "Sales & Growth", "growth_plan": "Sales & Growth",
    "salesman_performance": "Sales & Growth", "trend": "Sales & Growth", "marketing": "Sales & Growth",
    "winback": "Sales & Growth", "vendor_sourcing": "Sales & Growth", "pricing_optimization": "Sales & Growth",
    "cross_sell": "Sales & Growth", "vendor_scorecard": "Supply", "trend_radar": "Sales & Growth",
    "lead_gen": "Sales & Growth", "research_scout": "Sales & Growth",
    "risk_watch": "Risk", "salesman_stock_recon": "Risk", "returns_investigator": "Risk",
    "price_drift": "Finance", "ops_sentinel": "Operations",
}
for _n, _spec in AGENTS.items():
    _spec.department = _DEPARTMENTS.get(_n, "Operations")


def list_agents() -> list[dict]:
    return [{"name": s.name, "description": s.description, "category": s.category,
             "department": s.department} for s in AGENTS.values()]


def run_agent(name: str, triggered_by: str = "user", _event_chain_depth: int = 0) -> dict:
    """Run one agent and wrap with metadata. Raises KeyError for unknown agents.

    `triggered_by` ('user'|'schedule'|'escalation'|'event') is recorded by the memory layer.
    `_event_chain_depth` is set when this run is a reaction to an event (Phase B), so any
    events it emits carry depth+1 and the dispatcher can stop cascades."""
    name = AGENT_ALIASES.get(name, name)  # retired names resolve to their successor
    if name not in AGENTS:
        raise KeyError(name)
    spec = AGENTS[name]
    _run_state.errors = 0
    result = spec.run()
    failed = getattr(_run_state, "errors", 0)
    if failed:
        note = f" ⚠ {failed} data " + ("query" if failed == 1 else "queries") + \
               " failed — figures may be incomplete."
        result = {**result, "summary": (result.get("summary") or "").rstrip() + note,
                  "data_partial": True}
    wrapped = {
        "agent": name,
        "description": spec.description,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    # Interactive runs (portal button / assistant chat) skip the memory + event round-trips:
    # they exist to track change over time, and scheduled/escalation runs already do that.
    # This halves the DB trips on the latency-sensitive paths.
    if triggered_by == "user":
        return wrapped
    # Memory: diff vs the last baseline + record this run (best-effort — never break the run).
    try:
        from app import memory
        snap = memory.snapshot(spec, wrapped)
        wrapped["changes"] = memory.diff(memory.last_snapshot(name), snap)
        memory.record(name, wrapped.get("summary"), snap, triggered_by)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("memory hook failed for %s: %s", name, e)
    # Event backbone: emit a typed event from this run's diff (best-effort — never break run).
    try:
        from app import events
        wrapped["_event_chain_depth"] = _event_chain_depth
        events.emit_from_run(name, wrapped)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("event hook failed for %s: %s", name, e)
    finally:
        wrapped.pop("_event_chain_depth", None)
    return wrapped
