"""LATER — Coaching Brain (Three-Tiered Brain · Tier 3). A per-account PRE-VISIT brief for the
rep: what they buy, what they owe, what to cross-sell next, and the talking points — so every
call or store visit converts harder. Read-only + advise-only.

The account name is user-supplied, so it is escaped (Postgres string-literal safe: single quotes
doubled) before it is interpolated into the read-only query. Every query still runs through the
SELECT-only run_readonly_query RPC.
"""
from __future__ import annotations

import logging

from app.ai import exec_sql

log = logging.getLogger(__name__)


def _safe(s: object) -> str:
    """Postgres string-literal escape (double single-quotes) + length cap."""
    return str(s or "").replace("'", "''")[:120]


def accounts(limit: int = 250) -> list[dict]:
    """Account picker — named customers by revenue (excludes the walk-in cash bucket)."""
    return exec_sql(
        "SELECT customer_name, total_revenue_bhd, order_count, last_order_date "
        "FROM v_top_customers WHERE customer_name NOT ILIKE 'cash customer%' "
        f"ORDER BY total_revenue_bhd DESC NULLS LAST LIMIT {int(limit)}"
    ) or []


def brief(account: str) -> dict:
    """Build the pre-visit brief for one account."""
    a = _safe(account)
    if not a:
        return {"account": account, "summary": "No account selected.", "talking_points": []}

    profile = (exec_sql(
        "SELECT COUNT(DISTINCT invoice_no) AS orders, ROUND(SUM(revenue_bhd)::numeric, 3) AS revenue_bhd, "
        "MAX(sale_date) AS last_order, MIN(sale_date) AS first_order "
        f"FROM v_sales WHERE customer_name = '{a}'") or [{}])[0]

    top_items = exec_sql(
        "SELECT item_name, SUM(quantity) AS qty, ROUND(SUM(revenue_bhd)::numeric, 3) AS revenue_bhd "
        f"FROM v_sales WHERE customer_name = '{a}' AND item_name IS NOT NULL "
        "GROUP BY item_name ORDER BY revenue_bhd DESC LIMIT 10") or []
    bought = [r["item_name"] for r in top_items if r.get("item_name")]

    # Cross-sell: items that co-occur (in invoices across the business) with what THEY buy, but that
    # they haven't bought yet — the natural next products to pitch. Checks both pair directions.
    cross: list[dict] = []
    if bought:
        names = ",".join("'" + _safe(b) + "'" for b in bought[:10])
        fwd = exec_sql(
            "SELECT item_b AS suggest, SUM(bought_together) AS together FROM v_basket_affinity "
            f"WHERE item_a IN ({names}) AND item_b NOT IN ({names}) "
            "GROUP BY item_b ORDER BY together DESC LIMIT 6") or []
        rev = exec_sql(
            "SELECT item_a AS suggest, SUM(bought_together) AS together FROM v_basket_affinity "
            f"WHERE item_b IN ({names}) AND item_a NOT IN ({names}) "
            "GROUP BY item_a ORDER BY together DESC LIMIT 6") or []
        merged: dict[str, dict] = {}
        for r in fwd + rev:
            k = r.get("suggest")
            if k and (k not in merged or (r.get("together") or 0) > merged[k]["together"]):
                merged[k] = {"suggest": k, "together": int(r.get("together") or 0)}
        cross = sorted(merged.values(), key=lambda x: x["together"], reverse=True)[:6]

    # Open receivables (best-effort fuzzy match — AR account name vs sales customer name).
    ar_rows = exec_sql(
        "SELECT account, outstanding_bhd, overdue_bhd FROM v_receivables "
        f"WHERE account ILIKE '%{a}%' ORDER BY outstanding_bhd DESC LIMIT 1") or []
    ar = ar_rows[0] if ar_rows else {}

    # Ground-truth from the field (rep notes etc.), via local-embedding recall.
    notes = ""
    try:
        from app.knowledge import recall_text
        notes = recall_text(account, k=3)
    except Exception:  # noqa: BLE001
        pass

    points: list[str] = []
    if profile.get("last_order"):
        points.append(f"Last ordered {profile['last_order']} — open with what's new since then.")
    if ar.get("outstanding_bhd"):
        overdue = float(ar.get("overdue_bhd") or 0)
        od = f", BHD {overdue:,.3f} overdue" if overdue > 0 else ""
        points.append(f"Open balance BHD {float(ar['outstanding_bhd']):,.3f}{od} — collect tactfully.")
    if cross:
        points.append(f"Pitch {cross[0]['suggest']} — buyers of their items often add it.")
    if top_items:
        points.append("Reorder their staples: " + ", ".join(t["item_name"] for t in top_items[:3]) + ".")

    return {
        "account": account,
        "profile": profile,
        "top_items": top_items,
        "cross_sell": cross,
        "open_ar": ar,
        "field_notes": notes,
        "talking_points": points,
        "summary": (f"{account}: {profile.get('orders', 0)} orders, "
                    f"BHD {float(profile.get('revenue_bhd') or 0):,.0f} lifetime · {len(cross)} cross-sell ideas."),
    }
