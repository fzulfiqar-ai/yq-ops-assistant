"""Phase C.5 — deterministic what-if price simulator.

"What if we raise X05 by 10%?" — projected qty / revenue / margin from REAL price history,
never a fabricated number. Method:
  1. Build price epochs from the versioned MA_base price book (start_date, rate) for the code.
  2. Measure qty sold per day inside each epoch's active window (from v_sales).
  3. Arc elasticity (midpoint formula) between adjacent epochs with enough evidence.
  4. Median elasticity, clamped to [-3, 0] (demand falls, or is flat, as price rises).
  5. Project against the CURRENT price + trailing-90d run-rate, costing margin from the
     landed cost (purchase_costs / mrn_landed_costs — the docs/CLAUDE.md source of truth).
If there aren't ≥2 usable epochs, it REFUSES ("insufficient price history") rather than guess.

The LLM is not involved — this is pure arithmetic over the data.
"""
from __future__ import annotations

import logging
from statistics import median

from app.db_read import exec_sql_params

log = logging.getLogger(__name__)

MIN_EPOCH_DAYS = 14        # an epoch needs at least this many days of selling to count
MIN_EPOCH_QTY = 5          # ...and at least this much volume
ELASTICITY_FLOOR = -3.0
ELASTICITY_CAP = 0.0


def _code(item: str) -> str:
    return (item or "").strip().split(" ")[0].upper()


def _landed_cost(code: str) -> float | None:
    # Prefer the real MRN landed unit cost (all freight in); fall back to v_landed_margin's
    # blended unit cost. Both keyed on the leading code, like the rest of the schema.
    rows = exec_sql_params(
        "SELECT (SUM(qty*landed_unit_bhd)/NULLIF(SUM(qty),0)) AS c FROM mrn_lines "
        "WHERE UPPER(SPLIT_PART(sku_code,' ',1)) = $1 AND landed_unit_bhd > 0", [code]) or []
    c = rows[0].get("c") if rows else None
    if c:
        return float(c)
    rows = exec_sql_params(
        "SELECT unit_cost_bhd FROM v_landed_margin "
        "WHERE UPPER(SPLIT_PART(item_name,' ',1)) = $1 AND unit_cost_bhd > 0 "
        "ORDER BY unit_cost_bhd LIMIT 1", [code]) or []
    return float(rows[0]["unit_cost_bhd"]) if rows else None


def _epochs(code: str) -> list[dict]:
    """Price epochs (rate + active window) for a base code, oldest first."""
    return exec_sql_params(
        "SELECT start_date, MAX(rate_bhd) AS rate FROM selling_prices "
        "WHERE price_book='MA_base' AND rate_bhd > 0 "
        "AND UPPER(SPLIT_PART(sku_code,' ',1)) = $1 AND start_date IS NOT NULL "
        "GROUP BY start_date ORDER BY start_date", [code]) or []


def _qty_between(code: str, d_from, d_to) -> float:
    rows = exec_sql_params(
        "SELECT COALESCE(SUM(quantity),0) AS q FROM v_sales "
        "WHERE UPPER(SPLIT_PART(item_name,' ',1)) = $1 AND sale_date >= $2::date "
        + ("AND sale_date < $3::date " if d_to else ""),
        [code, str(d_from)] + ([str(d_to)] if d_to else [])) or []
    return float(rows[0]["q"] or 0)


def _current_baseline(code: str) -> tuple[float | None, float]:
    """Current MA_base price + trailing-90d monthly run-rate qty."""
    price_rows = exec_sql_params(
        "SELECT rate_bhd FROM selling_prices WHERE price_book='MA_base' AND rate_bhd>0 "
        "AND UPPER(SPLIT_PART(sku_code,' ',1)) = $1 ORDER BY start_date DESC NULLS LAST LIMIT 1",
        [code]) or []
    price = float(price_rows[0]["rate_bhd"]) if price_rows else None
    q_rows = exec_sql_params(
        "SELECT COALESCE(SUM(quantity),0) AS q FROM v_sales "
        "WHERE UPPER(SPLIT_PART(item_name,' ',1)) = $1 "
        "AND sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90", [code]) or []
    monthly_qty = float(q_rows[0]["q"] or 0) / 3.0
    return price, monthly_qty


def _estimate_elasticity(code: str) -> tuple[float | None, int, str]:
    """Median arc elasticity across adjacent epochs. Returns (elasticity, n_usable, confidence)."""
    epochs = _epochs(code)
    if len(epochs) < 2:
        return None, 0, "none"
    # attach a qty/day for each epoch window
    for i, e in enumerate(epochs):
        nxt = epochs[i + 1]["start_date"] if i + 1 < len(epochs) else None
        days = None
        try:
            from datetime import date
            d0 = date.fromisoformat(str(e["start_date"])[:10])
            d1 = date.fromisoformat(str(nxt)[:10]) if nxt else None
            days = (d1 - d0).days if d1 else None
        except Exception:  # noqa: BLE001
            days = None
        qty = _qty_between(code, e["start_date"], nxt)
        e["qty"], e["days"] = qty, days

    elasticities: list[float] = []
    for i in range(len(epochs) - 1):
        a, b = epochs[i], epochs[i + 1]
        if (a["days"] or 0) < MIN_EPOCH_DAYS or (a["qty"] or 0) < MIN_EPOCH_QTY:
            continue
        if (b["qty"] or 0) < MIN_EPOCH_QTY:
            continue
        pa, pb = float(a["rate"]), float(b["rate"])
        # normalize both epochs to a per-day rate so unequal windows compare fairly
        qa = a["qty"] / max(a["days"] or 1, 1)
        qb = b["qty"] / max(b["days"] or 1, 1) if b["days"] else b["qty"] / 30.0
        if pa <= 0 or pb <= 0 or (pa + pb) == 0 or (qa + qb) == 0:
            continue
        pct_q = (qb - qa) / ((qa + qb) / 2)
        pct_p = (pb - pa) / ((pa + pb) / 2)
        if abs(pct_p) < 1e-6:
            continue
        el = max(ELASTICITY_FLOOR, min(ELASTICITY_CAP, pct_q / pct_p))
        elasticities.append(el)

    if not elasticities:
        return None, 0, "none"
    n = len(elasticities)
    conf = "high" if n >= 3 else ("medium" if n == 2 else "low")
    return round(median(elasticities), 3), n, conf


def simulate(item: str, new_price: float) -> dict:
    """Project the impact of moving `item` to `new_price`. Refuses without price evidence."""
    code = _code(item)
    if not code:
        return {"ok": False, "reason": "No item given."}
    cur_price, monthly_qty = _current_baseline(code)
    if not cur_price:
        return {"ok": False, "code": code, "reason": "No current price on file for this item."}
    try:
        new_price = float(new_price)
    except (TypeError, ValueError):
        return {"ok": False, "code": code, "reason": "new_price must be a number."}
    if new_price <= 0:
        return {"ok": False, "code": code, "reason": "new_price must be positive."}

    elasticity, n_epochs, confidence = _estimate_elasticity(code)
    if elasticity is None:
        return {"ok": False, "code": code, "current_price_bhd": round(cur_price, 3),
                "reason": "insufficient price history to estimate demand response — "
                          "need at least two price periods with enough sales."}

    cost = _landed_cost(code)
    pct_p = (new_price - cur_price) / cur_price
    projected_qty = max(0.0, monthly_qty * (1 + elasticity * pct_p))

    cur_rev = monthly_qty * cur_price
    new_rev = projected_qty * new_price
    out = {
        "ok": True, "code": code, "confidence": confidence,
        "elasticity": elasticity, "price_epochs_used": n_epochs,
        "current_price_bhd": round(cur_price, 3), "new_price_bhd": round(new_price, 3),
        "price_change_pct": round(pct_p * 100, 1),
        "baseline_monthly_qty": round(monthly_qty, 1),
        "projected_monthly_qty": round(projected_qty, 1),
        "current_monthly_revenue_bhd": round(cur_rev, 3),
        "projected_monthly_revenue_bhd": round(new_rev, 3),
        "revenue_delta_bhd": round(new_rev - cur_rev, 3),
    }
    if cost:
        cur_margin = (cur_price - cost) * monthly_qty
        new_margin = (new_price - cost) * projected_qty
        out.update({
            "landed_cost_bhd": round(cost, 3),
            "current_monthly_margin_bhd": round(cur_margin, 3),
            "projected_monthly_margin_bhd": round(new_margin, 3),
            "margin_delta_bhd": round(new_margin - cur_margin, 3),
        })
    else:
        out["margin_note"] = "No landed cost on file — margin impact not shown."

    direction = "raise" if pct_p > 0 else "cut"
    rev_dir = "grow" if out["revenue_delta_bhd"] > 0 else "shrink"
    out["summary"] = (
        f"{code}: a {abs(out['price_change_pct'])}% price {direction} "
        f"(BHD {cur_price:.3f}→{new_price:.3f}) is projected to {rev_dir} monthly revenue by "
        f"BHD {abs(out['revenue_delta_bhd']):,.0f} "
        f"(elasticity {elasticity}, {confidence} confidence from {n_epochs} price move(s))."
    )
    return out
