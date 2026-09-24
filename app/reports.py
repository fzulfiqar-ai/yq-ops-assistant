"""Shared, read-only report queries.

Single source of truth consumed by BOTH the Streamlit dashboard and the React
`GET /report/{key}` API, so the two never drift. All revenue is Gross (VAT-incl)
with ex-VAT alongside; all windows anchor to the data's latest date.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from app.db_read import exec_sql
from app.database import get_client
from app.digest import all_alerts, daily_summary

log = logging.getLogger(__name__)


def search(q: str, features: set[str] | None = None) -> list[dict]:
    """Global search across customers, items and salesmen for the ⌘K palette.
    Uses the parameterized client (.ilike) — never string-interpolated SQL.
    `features` scopes result groups to the caller's pages (None = unrestricted)."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    c = get_client()
    pat = f"%{q}%"
    out: list[dict] = []
    sales_ok = features is None or "Sales" in features
    inv_ok = features is None or "Inventory" in features
    try:
        if sales_ok:
            for r in (c.table("v_top_customers").select("customer_name,gross_bhd")
                      .ilike("customer_name", pat).limit(6).execute().data or []):
                name = r.get("customer_name") or ""
                if name.lower().startswith("cash customer"):
                    continue
                out.append({"type": "customer", "label": name,
                            "sub": f"BHD {float(r.get('gross_bhd') or 0):,.0f} revenue"})
        if inv_ok:
            for r in (c.table("v_stock_health").select("item_name,current_stock,status")
                      .ilike("item_name", pat).limit(6).execute().data or []):
                out.append({"type": "item", "label": r.get("item_name") or "",
                            "sub": f"{int(float(r.get('current_stock') or 0))} on hand · {str(r.get('status') or '').replace('_', ' ')}"})
        if sales_ok:
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


def division_summary() -> list[dict]:
    """Accessories vs SIM, side by side — stock AND sales, on one screen.

    This exists because the SIM/Batelco starter-pack stock is OWNED (owner-confirmed
    06-Sep-2026), not consignment, and it is the majority of the stock book against a tiny
    share of revenue. The platform used to either hide it (agents drop it when
    app_settings.agent_exclude_sim is on, which is the default) or silently blend it into
    the Dashboard total — so the two surfaces disagreed with no explanation on screen.

    Every figure is computed by v_division_summary from the loaded data. Nothing here is a
    hardcoded constant: months_of_cover divides each division's stock value by its OWN
    average monthly revenue over the actual span of the sales data.
    """
    return exec_sql(
        "SELECT division, stock_value_bhd, stock_units, stock_skus, revenue_bhd, "
        "net_ex_vat_bhd, invoices, stock_share_pct, revenue_share_pct, months_of_cover "
        "FROM v_division_summary ORDER BY stock_value_bhd DESC NULLS LAST"
    )


def agents_status() -> list[dict]:
    """Latest run per agent for the Agent Performance panel (from agent_runs — the
    per-run memory table; audit_log is deliberately NOT readable via the SQL RPC)."""
    try:
        rows = (get_client().table("agent_runs")
                .select("agent,ran_at,summary")
                .order("ran_at", desc=True).limit(300).execute().data or [])
    except Exception:  # noqa: BLE001
        return []
    latest: dict[str, dict] = {}
    for r in rows:
        a = r.get("agent")
        if a and a not in latest:
            latest[a] = {"agent": a, "last_run": r.get("ran_at"), "summary": r.get("summary")}
    return sorted(latest.values(), key=lambda x: x["agent"])


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


# Salesman attainment (R3a, 24-Sep-2026): ONE SQL over the effective target row per rep
# (a month-specific row beats the standing '' row) LEFT JOINed to the current month's
# Accessories sales — the month of the latest loaded sale, giveaways out, SIM never, ex-VAT
# net_bhd — with the rep matched exactly as app.shop.rep_month_sales does (`name` or
# `name - %`, because Focus names carry a warehouse suffix). A rep with sales but no target
# row still appears (no_target = true), so nobody's month is invisible. Every row then goes
# through app.shop.tier_progress, the same function the rep's Today card uses.
ATTAINMENT_SQL = """
WITH mx AS (SELECT MAX(sale_date) AS d FROM v_sales),
per AS (SELECT to_char(d, 'YYYY-MM') AS period, date_trunc('month', d)::date AS m0,
               (date_trunc('month', d) + interval '1 month')::date AS m1, d FROM mx),
tg AS (
  SELECT DISTINCT ON (t.salesman) t.salesman, t.period, t.team, t.target_bhd, t.tier2_bhd, t.tier3_bhd,
         t.kickback_t1, t.kickback_t2, t.kickback_t3
  FROM salesman_targets t, per WHERE t.period IN ('', per.period)
  ORDER BY t.salesman, t.period DESC),
s AS (
  SELECT v.salesman_resolved AS rep, SUM(v.net_bhd) AS net_bhd, SUM(v.revenue_bhd) AS gross_bhd,
         COUNT(DISTINCT v.invoice_no) AS invoices,
         COUNT(DISTINCT v.customer_name) FILTER (WHERE NOT v.is_cash_customer) AS shops,
         MAX(v.sale_date) AS last_sale
  FROM v_sales v, per
  WHERE v.sale_date >= per.m0 AND v.sale_date < per.m1 AND v.division = 'Accessories' AND NOT v.is_giveaway
  GROUP BY v.salesman_resolved),
m AS (
  SELECT s.*, t.salesman AS target_salesman
  FROM s LEFT JOIN LATERAL (
    SELECT salesman FROM tg WHERE s.rep = tg.salesman OR s.rep LIKE tg.salesman || ' - %'
    ORDER BY length(tg.salesman) DESC LIMIT 1) t ON true),
agg AS (
  SELECT COALESCE(target_salesman, rep) AS salesman, SUM(net_bhd) AS net_bhd, SUM(gross_bhd) AS gross_bhd,
         SUM(invoices) AS invoices, SUM(shops) AS shops, MAX(last_sale) AS last_sale,
         bool_and(target_salesman IS NULL) AS no_target
  FROM m GROUP BY 1)
SELECT COALESCE(tg.salesman, agg.salesman) AS salesman, tg.period AS target_period, tg.team,
       tg.target_bhd, tg.tier2_bhd, tg.tier3_bhd, tg.kickback_t1, tg.kickback_t2, tg.kickback_t3,
       ROUND(COALESCE(agg.net_bhd, 0)::numeric, 3) AS net_bhd, ROUND(COALESCE(agg.gross_bhd, 0)::numeric, 3) AS gross_bhd,
       COALESCE(agg.invoices, 0) AS invoices, COALESCE(agg.shops, 0) AS shops, agg.last_sale::text AS last_sale,
       (tg.salesman IS NULL) AS no_target, per.period, per.d::text AS data_through
FROM tg FULL OUTER JOIN agg ON agg.salesman = tg.salesman CROSS JOIN per
ORDER BY net_bhd DESC, salesman
"""

_TARGET_KEYS = ("salesman", "period", "team", "target_bhd", "tier2_bhd", "tier3_bhd",
                "kickback_t1", "kickback_t2", "kickback_t3")


def attainment_rows(sql_rows: list[dict], salesmen: list[dict] | None = None, today=None) -> list[dict]:
    """Pure: ATTAINMENT_SQL rows → the table the portal shows. `tier` is app.shop.tier_progress
    (None when the rep has no target row); `salesman_id` / `name` / `is_active` come from the
    salesmen table when a row's focus_name matches. Sorted by ex-VAT sales, highest first."""
    from decimal import Decimal, ROUND_HALF_UP
    from app import shop
    today = today or shop.bahrain_today()
    by_focus = {str(s.get("focus_name") or "").strip(): s for s in (salesmen or []) if s.get("focus_name")}
    out: list[dict] = []
    for r in sql_rows or []:
        name = str(r.get("salesman") or "")
        no_target = bool(r.get("no_target")) or r.get("target_bhd") is None
        target = None if no_target else {k: r.get(k) for k in _TARGET_KEYS}
        if target is not None:
            target["period"] = r.get("target_period") if r.get("target_period") is not None else ""
        net = float(Decimal(str(r.get("net_bhd") or 0)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
        tier = shop.tier_progress(target, net, r.get("data_through"), today=today) if target else None
        sm = by_focus.get(name) or {}
        out.append({
            "salesman": name, "salesman_id": sm.get("id"), "name": sm.get("name") or name,
            "is_active": sm.get("is_active"), "referral_code": sm.get("referral_code"),
            "team": (target or {}).get("team") or None, "target_period": (target or {}).get("period"),
            "no_target": no_target, "period": r.get("period"), "data_through": r.get("data_through"),
            "net_bhd": net, "gross_bhd": float(r.get("gross_bhd") or 0), "basis": shop.KICKBACK_BASIS,
            "invoices": int(float(r.get("invoices") or 0)), "shops": int(float(r.get("shops") or 0)),
            "last_sale": r.get("last_sale"),
            "tier": tier,
            "tier_reached": tier["tier_reached"] if tier else None,
            "kickback_pct": tier["kickback_pct"] if tier else None,
            "kickback_bhd": tier["kickback_bhd"] if tier else None,
            "next_tier": tier["next_tier"] if tier else None,
            "progress_pct": tier["progress_pct"] if tier else None,
            "days_left": tier["days_left"] if tier else None,
        })
    out.sort(key=lambda x: (-x["net_bhd"], x["salesman"]))
    return out


ATTAINMENT_ERROR = "Attainment could not be computed — the sales view or the targets table did not answer."


def salesman_attainment_result() -> dict:
    """Per-rep current-month attainment (Accessories, ex-VAT) with the tier reached and the
    estimated kickback — one SQL, then app.shop.tier_progress per row. {"rows": [], "error": msg}
    when the SQL fails, so a screen says "could not compute" rather than "no sales loaded"."""
    try:
        rows = exec_sql(ATTAINMENT_SQL) or []
    except Exception as e:  # noqa: BLE001
        log.warning("attainment unavailable: %s", e)
        return {"rows": [], "error": ATTAINMENT_ERROR}
    salesmen: list[dict] = []
    try:
        salesmen = (get_client().table("salesmen").select("id,name,focus_name,is_active,referral_code")
                    .limit(500).execute().data or [])
    except Exception as e:  # noqa: BLE001 — names only; the figures do not depend on it
        log.debug("salesmen unavailable for attainment names: %s", e)
    return {"rows": attainment_rows(rows, salesmen), "error": None}


def salesman_attainment() -> list[dict]:
    """The attainment rows alone ([] on failure) — see salesman_attainment_result."""
    return salesman_attainment_result()["rows"]


def top_salesmen_from_attainment(rows: list[dict], limit: int = 8) -> list[dict]:
    """The Dashboard's "Top salesmen" from the attainment rows: Accessories, the current month,
    ex-VAT `net_bhd` beside the VAT-inclusive `revenue_bhd`, `orders` = invoices. Names with no
    sales this month are left out; outlets without a target row still show (no_target). Nothing
    about money owed travels here: the tier, kickback and referral code stay behind Shop Admin on
    /shop/attainment — the Dashboard is a default member page."""
    out = [{"salesman": r["salesman"], "orders": r["invoices"], "qty": None,
            "revenue_bhd": r["gross_bhd"], "net_bhd": r["net_bhd"], "no_target": r["no_target"]}
           for r in rows if float(r.get("net_bhd") or 0) > 0]
    return out[:limit]


def daily_sales_mtd() -> list[dict]:
    """One row per day of the current month (anchored to the data's latest date) —
    the owner's 'daily current-month sales' dashboard chart."""
    # acc_bhd = Mobile Accessories only. Targets never include Batelco SIM sales (owner,
    # 21-Sep-2026), so the dashboard's daily bars and pace read acc_bhd; gross_bhd stays the
    # all-division total for the tooltip and the division chips.
    return exec_sql(
        "WITH d AS (SELECT MAX(sale_date) AS mx FROM v_sales) "
        "SELECT sale_date::text AS day, ROUND(SUM(revenue_bhd)::numeric, 2) AS gross_bhd, "
        "ROUND(SUM(CASE WHEN division = 'Accessories' THEN revenue_bhd ELSE 0 END)::numeric, 2) AS acc_bhd, "
        "ROUND(SUM(net_bhd)::numeric, 2) AS net_bhd, COUNT(DISTINCT invoice_no) AS orders "
        "FROM v_sales, d WHERE sale_date >= date_trunc('month', d.mx)::date "
        "GROUP BY sale_date ORDER BY sale_date"
    ) or []


def sales_split_mtd() -> dict:
    """MTD cash/credit + division split (giveaways counted apart so free Batelco
    stock can't distort the revenue story)."""
    win = ("FROM v_sales, (SELECT MAX(sale_date) AS mx FROM v_sales) d "
           "WHERE sale_date >= date_trunc('month', d.mx)::date")
    pay = exec_sql(
        f"SELECT sale_type, COUNT(DISTINCT invoice_no) AS orders, "
        f"ROUND(SUM(revenue_bhd)::numeric, 2) AS revenue_bhd {win} GROUP BY sale_type") or []
    div = exec_sql(
        f"SELECT division, COUNT(DISTINCT invoice_no) AS orders, "
        f"ROUND(SUM(revenue_bhd)::numeric, 2) AS revenue_bhd, "
        f"SUM(CASE WHEN is_giveaway THEN quantity ELSE 0 END) AS giveaway_qty "
        f"{win} GROUP BY division ORDER BY revenue_bhd DESC") or []
    return {"by_payment": pay, "by_division": div}


def _pace(kpis: dict, data_date: str | None) -> dict:
    """MTD pace vs target — 'on track for BHD X'. The target is a MOBILE ACCESSORIES target
    (owner, 21-Sep-2026): Batelco SIM sales never count, so mtd_bhd here is the Accessories
    division only (kpis['rev_mtd_acc']); the all-division figure stays on the revenue tile."""
    import calendar
    from datetime import date
    try:
        from app.settings import setting
        target = float(setting("monthly_sales_target_bhd") or 0)
    except Exception:  # noqa: BLE001
        target = 0.0
    mtd = float(kpis.get("rev_mtd_acc") if kpis.get("rev_mtd_acc") is not None else kpis.get("rev_mtd") or 0)
    prev = float(kpis.get("rev_prev_month") or 0)
    out = {"target_bhd": target, "mtd_bhd": mtd, "prev_month_bhd": prev, "basis": "Accessories",
           "projected_bhd": None, "target_pct": None, "on_track": None}
    try:
        d = date.fromisoformat(str(data_date)[:10])
        days_in_month = calendar.monthrange(d.year, d.month)[1]
        projected = mtd / d.day * days_in_month if d.day else mtd
        out["projected_bhd"] = round(projected, 0)
        if target > 0:
            out["target_pct"] = round(mtd / target * 100, 1)
            out["on_track"] = projected >= target
    except Exception:  # noqa: BLE001
        pass
    return out


# The dashboard payload is ~20 view queries; each is a PostgREST round-trip. Build the
# independent sections concurrently and serve warm hits from an in-process cache (zero
# round-trips). flush on ingest via invalidate_dashboard_cache() (ai.flush_cache calls it).
_DASH_TTL_S = 300
_dash_cache: dict = {"at": 0.0, "payload": None}


def invalidate_dashboard_cache() -> None:
    _dash_cache.update(at=0.0, payload=None)
    _report_cache.clear()


def dashboard(force: bool = False) -> dict:
    if not force and _dash_cache["payload"] is not None and time.time() - _dash_cache["at"] < _DASH_TTL_S:
        return _dash_cache["payload"]
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="dash") as ex:
        futs = {
            "s": ex.submit(daily_summary),
            "a": ex.submit(all_alerts),
            "health": ex.submit(business_health),
            "movers": ex.submit(movers, 5),
            "trend": ex.submit(revenue_trend, 12),
            "channel": ex.submit(sales_by_channel),
            "agents": ex.submit(agents_status),
            "fresh": ex.submit(data_freshness),
            "daily_mtd": ex.submit(daily_sales_mtd),
            "split": ex.submit(sales_split_mtd),
            "attainment": ex.submit(salesman_attainment_result),
        }
        r = {k: f.result() for k, f in futs.items()}
    out = _assemble_dashboard(r)
    _dash_cache.update(at=time.time(), payload=out)
    return out


def _assemble_dashboard(r: dict) -> dict:
    s, a = r["s"], r["a"]
    kpis = {
        "rev_today": s["rev_today"], "net_today": s["net_today"], "orders_today": s["orders_today"],
        "rev_yesterday": s["rev_yesterday"], "orders_yesterday": s["orders_yesterday"],
        "rev_mtd": s["rev_mtd"], "net_mtd": s["net_mtd"], "orders_mtd": s["orders_mtd"],
        "rev_prev_month": s["rev_prev_month"],
        # Same slice of last month as MTD covers — the only like-for-like MoM basis.
        "rev_prev_month_mtd": s.get("rev_prev_month_mtd", 0),
        "prev_month_through": s.get("prev_month_through"),
        "total_receivables": s["total_receivables"],
        "low_stock_count": a["low_stock_count"],
        # Whole-book SQL sums (daily_summary), NOT the capped alert list — keeps the
        # tile on the exact same basis as the collections agent.
        "overdue_count": s["overdue_accounts"],
        "overdue_total_bhd": s["overdue_receivables_bhd"],
        "current_receivables_bhd": s["current_receivables_bhd"],
    }
    fresh = r["fresh"]
    # `attainment` is {"rows", "error"} from salesman_attainment_result (a bare list is tolerated)
    att = r.get("attainment")
    att_rows = att.get("rows") if isinstance(att, dict) else (att or [])
    att_error = att.get("error") if isinstance(att, dict) else None
    return {
        "data_as_of": s.get("data_date"),
        "data_stale": fresh["stale"],
        "data_days_behind": fresh["days_behind"],
        "actions": dashboard_actions(a, kpis),
        "kpis": kpis,
        "health": r["health"],
        "movers": r["movers"],
        "top_customers": s["top_customers"],
        "revenue_trend": r["trend"],
        "by_channel": r["channel"],
        # R3a (24-Sep-2026): the dashboard's top salesmen are the CURRENT MONTH, ACCESSORIES ONLY
        # (SIM never counts towards a rep), ex-VAT beside gross — derived from the attainment rows
        # so the widget and the Salesmen page can never disagree. The all-time, all-division
        # v_sales_by_salesman rollup stays on the Sales page (reports.sales()).
        "by_salesman": top_salesmen_from_attainment(att_rows),
        "by_salesman_scope": {
            "division": "Accessories", "basis": "net_ex_vat",
            "period": (att_rows[0].get("period") if att_rows else None),
            "data_through": (att_rows[0].get("data_through") if att_rows else None),
            "error": att_error,
        },
        "agents": r["agents"],
        "alerts": a,
        "daily_mtd": r["daily_mtd"],
        "by_payment": r["split"]["by_payment"],
        "by_division": r["split"]["by_division"],
        "pace": _pace({**kpis, "rev_mtd_acc": sum(
            float(d.get("revenue_bhd") or 0) for d in (r["split"]["by_division"] or [])
            if str(d.get("division") or "") == "Accessories")}, s.get("data_date")),
        # Kept for the payload's shape only. The per-rep rows (kickback, tier, referral code)
        # are money and live behind Shop Admin on /shop/attainment; the Dashboard feature is a
        # default member grant, so they never travel with it (re-review, 24-Sep-2026).
        "attainment": [],
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
    # New arrivals: Material Receipt Notes posted in the last 14 days (one row per MRN voucher),
    # so the team can see a shipment landed (LC1716_196 = MRN:YQ-26-09-2, 20-Sep-2026) without
    # opening Stock Moves. Quantities come from the Focus ledger, never from the shop.
    arrivals = exec_sql(
        "SELECT voucher, MIN(move_date)::text AS received_on, COUNT(DISTINCT item_name) AS items, "
        "COALESCE(SUM(received_qty),0) AS units, ROUND(COALESCE(SUM(received_value_bhd),0)::numeric, 2) AS value_bhd "
        "FROM stock_movements WHERE voucher_type = 'Material Receipt Note' "
        "AND move_date >= (SELECT MAX(move_date) FROM stock_movements) - 14 "
        "GROUP BY voucher ORDER BY received_on DESC LIMIT 6"
    ) or []
    return {
        "rows": rows,
        "by_status": dict(Counter(r["status"] for r in rows)),
        "stock_value": float(t.get("v", 0)),
        "stock_value_cost": float((cv or [{}])[0].get("v", 0)),
        "stock_qty": float(t.get("q", 0)),
        "by_warehouse": stock_by_warehouse(),
        "recent_receipts": arrivals,
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
        # per-day per-salesman gross this month (the Sales page's daily chart). Targets were
        # retired 21-Sep-2026 pending the owner's target file -- see scripts/import_targets.py.
        "daily_by_salesman": exec_sql(
            "WITH d AS (SELECT MAX(sale_date) AS mx FROM v_sales) "
            "SELECT sale_date::text AS day, salesman_resolved AS salesman, "
            "ROUND(SUM(revenue_bhd)::numeric, 2) AS gross_bhd "
            "FROM v_sales, d WHERE sale_date >= date_trunc('month', d.mx)::date "
            "AND NOT is_giveaway GROUP BY 1, 2 ORDER BY 1"
        ) or [],
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

# Every page payload is served from this in-process cache between uploads — the data
# only changes on ingest (which calls invalidate_dashboard_cache), so repeat clicks
# cost ZERO DB round-trips. TTL is a safety net, not the real invalidation.
_REPORT_TTL_S = 300
_report_cache: dict[str, tuple[float, object]] = {}


def cached_report(key: str):
    hit = _report_cache.get(key)
    if hit and time.time() - hit[0] < _REPORT_TTL_S:
        return hit[1]
    out = REPORTS[key]()
    _report_cache[key] = (time.time(), out)
    return out

# report key -> the feature a member must have to read it
REPORT_FEATURE = {
    "dashboard": "Dashboard",
    "inventory": "Inventory",
    "sales": "Sales",
    "margins": "Margins",
    "receivables": "Receivables",
}
