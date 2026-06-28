"""Shared, read-only report queries.

Single source of truth consumed by BOTH the Streamlit dashboard and the React
`GET /report/{key}` API, so the two never drift. All revenue is Gross (VAT-incl)
with ex-VAT alongside; all windows anchor to the data's latest date.
"""
from __future__ import annotations

from collections import Counter

from app.ai import exec_sql
from app.database import get_client
from app.digest import all_alerts, daily_summary


def search(q: str) -> list[dict]:
    """Global search across customers, items and salesmen for the ⌘K palette.
    Uses the parameterized client (.ilike) — never string-interpolated SQL."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    c = get_client()
    pat = f"%{q}%"
    out: list[dict] = []
    try:
        for r in (c.table("v_top_customers").select("customer_name,gross_bhd")
                  .ilike("customer_name", pat).limit(6).execute().data or []):
            name = r.get("customer_name") or ""
            if name.lower().startswith("cash customer"):
                continue
            out.append({"type": "customer", "label": name,
                        "sub": f"BHD {float(r.get('gross_bhd') or 0):,.0f} revenue"})
        for r in (c.table("v_stock_health").select("item_name,current_stock,status")
                  .ilike("item_name", pat).limit(6).execute().data or []):
            out.append({"type": "item", "label": r.get("item_name") or "",
                        "sub": f"{int(float(r.get('current_stock') or 0))} on hand · {str(r.get('status') or '').replace('_', ' ')}"})
        for r in (c.table("v_sales_by_salesman").select("salesman,revenue_bhd")
                  .ilike("salesman", pat).limit(4).execute().data or []):
            out.append({"type": "salesman", "label": r.get("salesman") or "",
                        "sub": f"BHD {float(r.get('revenue_bhd') or 0):,.0f} gross"})
    except Exception:
        pass
    return out[:16]


def data_as_of() -> str | None:
    rows = exec_sql("SELECT MAX(sale_date) AS d FROM v_sales LIMIT 1")
    return (rows or [{}])[0].get("d")


# Brief/dashboard advise on the LAST upload — if it's old, every figure is stale. STALE_AFTER_DAYS
# is lenient (the server clock can run a day ahead of the data); >3 days = an upload was missed.
STALE_AFTER_DAYS = 3


def data_freshness() -> dict:
    """How current the loaded data is — drives the stale-data guard on the brief + dashboard."""
    from datetime import date
    d = data_as_of()
    if not d:
        return {"data_until": None, "days_behind": None, "stale": True}
    try:
        days = (date.today() - date.fromisoformat(str(d)[:10])).days
    except Exception:
        return {"data_until": str(d)[:10], "days_behind": None, "stale": False}
    return {"data_until": str(d)[:10], "days_behind": days, "stale": days > STALE_AFTER_DAYS}


def dashboard_actions(alerts: dict, kpis: dict) -> list[dict]:
    """Lightweight 'today's priority actions' for the Dashboard hero — derived from the alert
    payload the dashboard already computes (no extra agent runs). Ranked by urgency then BHD."""
    acts: list[dict] = []
    if (alerts.get("negative_margin_count") or 0) > 0:
        acts.append({"action": f"Fix pricing on {alerts['negative_margin_count']} items selling below cost",
                     "to": "/margins", "bhd": 0, "urgency": 3})
    if (kpis.get("low_stock_count") or 0) > 0:
        acts.append({"action": f"Reorder {kpis['low_stock_count']} low / out-of-stock items",
                     "to": "/orders", "bhd": 0, "urgency": 3})
    if (kpis.get("overdue_count") or 0) > 0:
        acts.append({"action": f"Chase {kpis['overdue_count']} overdue accounts",
                     "to": "/receivables", "bhd": round(float(kpis.get("overdue_total_bhd") or 0), 0), "urgency": 2})
    acts.sort(key=lambda a: (a["urgency"], a["bhd"]), reverse=True)
    return acts[:5]


# Per-report freshness for the Zoho-style upload panel: which Focus report, its cadence, and how
# fresh its data is in the DB. (report key, label, cadence, base table, date col, price_book filter)
_COVERAGE_SPECS = [
    ("Sales_day_book", "Sales - line items", "daily", "order_lines", "line_date", None),
    ("Summary_sales_register", "Sales - salesman / header", "daily", "orders", "order_date", None),
    ("Stock_balance_by_warehouse", "Stock balance (current)", "daily", "stock_balance", "as_of_date", None),
    ("Stock_ledger", "Stock movements + transfers", "daily", "stock_movements", "move_date", None),
    ("Customer_summary_ageing_by_due_date", "Receivables (ageing)", "daily", "ar_ageing", "as_of_date", None),
    ("Product_Profitability_Report", "Margins (profitability)", "daily", "product_profitability", "report_date", None),
    ("MASellingPriceBook", "Price book - standard", "weekly", "selling_prices", "imported_at", "MA_base"),
    ("ModernTradeSellerBook", "Price book - modern trade", "weekly", "selling_prices", "imported_at", "modern_trade"),
]


def coverage() -> list[dict]:
    """How fresh each Focus report's data is in the DB (drives the Data-page 'data until' panel).
    Status is lenient (current if <= 2 days behind) to absorb the server clock running a day ahead."""
    from datetime import date
    c = get_client()
    today = date.today()
    out: list[dict] = []
    for key, label, cadence, table, col, book in _COVERAGE_SPECS:
        d = None
        try:
            q = c.table(table).select(col)
            if book:
                q = q.eq("price_book", book)
            r = q.order(col, desc=True).limit(1).execute().data
            d = (r or [{}])[0].get(col)
        except Exception:
            d = None
        ds, days_behind, status = (str(d)[:10] if d else None), None, "never"
        if ds:
            try:
                days_behind = (today - date.fromisoformat(ds)).days
                status = "current" if days_behind <= 2 else ("behind" if days_behind <= 7 else "stale")
            except Exception:
                status = "current"
        out.append({"report": key, "label": label, "cadence": cadence,
                    "data_until": ds, "days_behind": days_behind, "status": status})
    return out


def revenue_trend(months: int = 12) -> list[dict]:
    rows = exec_sql(
        "SELECT period_month, gross_bhd, net_revenue_bhd, order_count, total_qty "
        f"FROM v_sales_by_period ORDER BY period_month DESC LIMIT {int(months)}"
    )
    return list(reversed(rows or []))


def sales_by_salesman() -> list[dict]:
    return exec_sql(
        "SELECT salesman, orders, qty, revenue_bhd, net_bhd FROM v_sales_by_salesman LIMIT 40"
    )


def sales_by_channel() -> list[dict]:
    return exec_sql("SELECT channel, orders, qty, revenue_bhd, net_bhd FROM v_sales_by_channel")


def top_sellers(limit: int = 15) -> list[dict]:
    return exec_sql(
        "SELECT item_name, category_name, SUM(quantity) AS qty, SUM(revenue_bhd) AS revenue_bhd "
        "FROM v_sales WHERE sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90 "
        "AND item_name IS NOT NULL GROUP BY item_name, category_name "
        f"ORDER BY qty DESC NULLS LAST LIMIT {int(limit)}"
    )


def stock_by_warehouse() -> list[dict]:
    return exec_sql(
        "SELECT warehouse_name, COALESCE(SUM(total_value_bhd),0) AS value_bhd, "
        "COALESCE(SUM(net_qty),0) AS qty, COUNT(*) AS items FROM stock_balance "
        "WHERE as_of_date=(SELECT MAX(as_of_date) FROM stock_balance) "
        "GROUP BY warehouse_name ORDER BY value_bhd DESC"
    )


def agents_status() -> list[dict]:
    """Latest run per agent for the Agent Performance panel (from audit_log)."""
    return exec_sql(
        "SELECT DISTINCT ON (detail->>'agent') detail->>'agent' AS agent, ts AS last_run, "
        "detail->>'summary' AS summary FROM audit_log "
        "WHERE event='agent' AND detail->>'agent' IS NOT NULL "
        "ORDER BY detail->>'agent', ts DESC"
    )


def business_health() -> dict:
    """CEO-grade health metrics the totals don't show: TRUE (landed-cost) margin, cash efficiency,
    and capital frozen in dead stock. Read-only.

    Margin is on a LANDED-COST basis (supplier + freight + customs + clearing) via v_landed_margin,
    ex-VAT — the real margin, not the Focus-GP illusion. cost_coverage_pct says how much of revenue
    is costed (grows as MRN receipts are ingested); below_cost_count is the real below-cost count."""
    # TRUE gross margin — revenue minus landed cost (ex-VAT)
    lm = (exec_sql("SELECT COALESCE(SUM(net_revenue_bhd),0) AS net, COALESCE(SUM(gross_profit_bhd),0) AS gp, "
                   "COUNT(*) FILTER (WHERE gross_profit_bhd < 0) AS below FROM v_landed_margin LIMIT 1") or [{}])[0]
    net, gp = float(lm.get("net") or 0), float(lm.get("gp") or 0)
    gp_pct = (gp / net * 100) if net else 0.0
    below_cost = int(lm.get("below") or 0)
    total_net = float((exec_sql("SELECT COALESCE(SUM(net_bhd),0) AS net FROM v_sales WHERE item_name IS NOT NULL")
                       or [{}])[0].get("net") or 0)
    coverage_pct = (net / total_net * 100) if total_net else 0.0
    # Cash efficiency: how much AR is overdue, and crude DSO (AR ÷ avg daily gross over last 90d)
    ar = (exec_sql("SELECT COALESCE(SUM(outstanding_bhd),0) AS total, COALESCE(SUM(overdue_bhd),0) AS overdue "
                   "FROM v_receivables LIMIT 1") or [{}])[0]
    ar_total, ar_overdue = float(ar.get("total") or 0), float(ar.get("overdue") or 0)
    overdue_pct = (ar_overdue / ar_total * 100) if ar_total else 0.0
    dpr = float((exec_sql("SELECT COALESCE(SUM(revenue_bhd),0)/90.0 AS d FROM v_sales "
                          "WHERE sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90 LIMIT 1")
                 or [{}])[0].get("d") or 0)
    dso = (ar_total / dpr) if dpr else 0.0
    # Capital frozen in dead stock (no sale in the velocity window)
    dead = (exec_sql("SELECT COALESCE(SUM(stock_value),0) AS v, COUNT(*) AS n "
                     "FROM v_stock_health WHERE status='dead_stock' LIMIT 1") or [{}])[0]
    return {
        "gp_bhd": gp, "gp_pct": gp_pct,
        "margin_basis": "landed", "cost_coverage_pct": coverage_pct, "below_cost_count": below_cost,
        "ar_overdue_pct": overdue_pct, "dso_days": dso,
        "dead_stock_bhd": float(dead.get("v") or 0), "dead_stock_count": int(dead.get("n") or 0),
    }


def movers(k: int = 5) -> dict:
    """What's accelerating vs fading — momentum = last-30d run-rate ÷ the 90-day baseline.
    Risers to restock & push; fallers to investigate before stock goes dead."""
    cols = ("item_name, sold_30d, sold_90d, status, "
            "ROUND((sold_30d / NULLIF(sold_90d / 3.0, 0))::numeric, 2) AS momentum")
    rising = exec_sql(f"SELECT {cols} FROM v_stock_health WHERE sold_90d > 0 AND sold_30d > 0 "
                      f"ORDER BY momentum DESC LIMIT {int(k)}") or []
    falling = exec_sql(f"SELECT {cols} FROM v_stock_health WHERE sold_90d > 0 "
                       f"ORDER BY momentum ASC LIMIT {int(k)}") or []
    return {"rising": rising, "falling": falling}


def dashboard() -> dict:
    s = daily_summary()
    a = all_alerts()
    kpis = {
        "rev_today": s["rev_today"], "net_today": s["net_today"], "orders_today": s["orders_today"],
        "rev_yesterday": s["rev_yesterday"], "orders_yesterday": s["orders_yesterday"],
        "rev_mtd": s["rev_mtd"], "net_mtd": s["net_mtd"], "orders_mtd": s["orders_mtd"],
        "rev_prev_month": s["rev_prev_month"],
        "total_receivables": s["total_receivables"],
        "low_stock_count": a["low_stock_count"],
        "overdue_count": a["overdue_count"],
        "overdue_total_bhd": a["overdue_total_bhd"],
    }
    fresh = data_freshness()
    return {
        "data_as_of": s.get("data_date"),
        "data_stale": fresh["stale"],
        "data_days_behind": fresh["days_behind"],
        "actions": dashboard_actions(a, kpis),
        "kpis": kpis,
        "health": business_health(),
        "movers": movers(5),
        "top_customers": s["top_customers"],
        "revenue_trend": revenue_trend(12),
        "by_channel": sales_by_channel(),
        "by_salesman": sales_by_salesman()[:8],
        "agents": agents_status(),
        "alerts": a,
    }


def inventory() -> dict:
    rows = exec_sql(
        "SELECT item_name, current_stock, stock_value, sold_90d, days_cover, "
        "suggested_reorder_qty, status FROM v_stock_health "
        "ORDER BY (CASE status WHEN 'urgent_out_of_stock' THEN 0 WHEN 'low_stock' THEN 1 "
        "WHEN 'dead_stock' THEN 2 WHEN 'overstock' THEN 3 ELSE 4 END), days_cover ASC NULLS FIRST "
        "LIMIT 300"
    )
    tv = exec_sql(
        "SELECT COALESCE(SUM(total_value_bhd),0) AS v, COALESCE(SUM(net_qty),0) AS q "
        "FROM stock_balance WHERE as_of_date=(SELECT MAX(as_of_date) FROM stock_balance) LIMIT 1"
    )
    t = (tv or [{}])[0]
    # Inventory at landed COST (capital invested) — from the real MRN costs (mrn_landed_costs),
    # matched by NORMALISED ProdCode prefix (longest wins) so variants don't collide. Partial
    # coverage is fine. (Same cost source as v_landed_margin — kept consistent on purpose.)
    cv = exec_sql(
        "WITH cost AS ("
        "  SELECT landed_cost_bhd, REPLACE(REPLACE(REPLACE(UPPER(sku_code),' ',''),'-',''),'.','') AS nkey "
        "  FROM mrn_landed_costs WHERE landed_cost_bhd IS NOT NULL), "
        "item_cost AS ("
        "  SELECT DISTINCT ON (sb.ctid) sb.net_qty, c.landed_cost_bhd "
        "  FROM stock_balance sb JOIN cost c "
        "  ON REPLACE(REPLACE(REPLACE(UPPER(sb.item_name),' ',''),'-',''),'.','') LIKE c.nkey || '%' "
        "  WHERE sb.as_of_date=(SELECT MAX(as_of_date) FROM stock_balance) "
        "  ORDER BY sb.ctid, LENGTH(c.nkey) DESC) "
        "SELECT COALESCE(SUM(net_qty * landed_cost_bhd),0) AS v FROM item_cost"
    )
    return {
        "rows": rows,
        "by_status": dict(Counter(r["status"] for r in rows)),
        "stock_value": float(t.get("v", 0)),
        "stock_value_cost": float((cv or [{}])[0].get("v", 0)),
        "stock_qty": float(t.get("q", 0)),
        "by_warehouse": stock_by_warehouse(),
    }


def sales() -> dict:
    return {
        "trend": revenue_trend(12),
        "by_salesman": sales_by_salesman(),
        "by_channel": sales_by_channel(),
        "top_sellers": top_sellers(15),
        "top_customers": exec_sql(
            "SELECT customer_name, gross_bhd AS total_revenue_bhd, order_count, last_order_date "
            "FROM v_top_customers WHERE customer_name NOT ILIKE 'cash customer%' "
            "ORDER BY gross_bhd DESC NULLS LAST LIMIT 50"
        ),
    }


def margins() -> dict:
    rows = exec_sql(
        "SELECT item_name, category_name, gp_margin_pct, np_margin_pct, gross_profit_bhd, "
        "net_amount_bhd, cogs_bhd FROM v_product_margin WHERE gp_margin_pct IS NOT NULL "
        "ORDER BY gp_margin_pct ASC LIMIT 200"
    )
    neg = [r for r in rows if (r.get("gp_margin_pct") or 0) < 0]
    tt = exec_sql(
        "SELECT COALESCE(SUM(net_amount_bhd),0) AS net, COALESCE(SUM(gross_profit_bhd),0) AS gp, "
        "COALESCE(SUM(cogs_bhd),0) AS cogs FROM v_product_margin LIMIT 1"
    )
    t = (tt or [{}])[0]
    net, gp = float(t.get("net", 0)), float(t.get("gp", 0))
    return {
        "rows": rows, "count": len(rows), "negative_count": len(neg),
        "total_net_bhd": net, "total_gp_bhd": gp,
        "gp_pct": (gp / net * 100) if net else 0.0,
    }


def receivables() -> dict:
    rows = exec_sql(
        "SELECT account, group_name, outstanding_bhd, overdue_bhd, over_90_bhd, "
        "b_0_30, b_31_60, b_61_90, b_91_120, b_121_150, b_151_180, b_181_210, b_over_210 "
        "FROM v_receivables ORDER BY outstanding_bhd DESC LIMIT 200"
    )
    total = sum(float(r.get("outstanding_bhd") or 0) for r in rows)
    over90 = sum(float(r.get("over_90_bhd") or 0) for r in rows)
    buckets = {k: sum(float(r.get(k) or 0) for r in rows) for k in
               ("b_0_30", "b_31_60", "b_61_90", "b_91_120", "b_121_150", "b_151_180", "b_181_210", "b_over_210")}
    overdue = [r for r in rows if (r.get("overdue_bhd") or 0) > 0]
    return {
        "rows": rows, "count": len(rows), "total": total, "over_90": over90,
        "overdue_count": len(overdue), "buckets": buckets,
    }


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
