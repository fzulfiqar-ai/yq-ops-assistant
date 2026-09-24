"""Below-cost and thin-margin items on the COMPUTED margin (R2, 24-Sep-2026).

The Focus Product Profitability report cannot be trusted for the two columns everyone read:
'Gross Profit' loses its minus sign on loss items (UK03 20W Charger: net 439.80, COGS 483.20,
GP shown +43.40) and 'GP Margin %' is not a percentage (median 4,835), so every "below cost"
query on gp_margin_pct < 0 returned nothing, forever. economics_v2_migration.sql appends
gp_computed_bhd / net_ex_vat_bhd / gp_ex_vat_bhd / margin_ex_vat_pct / is_below_cost to
v_product_margin; this module is the one place the agents, the digest and the reports read them.

Before the migration is applied the same figures are computed inline from the base columns
(net_amount_bhd / 1.1 -- the VAT rule v_sales applies -- against COGS), so the callers work either
way and UK03 is flagged either way. Every row carries `gp_margin_pct` = the computed ex-VAT margin
so consumers written for the old column (agent_actions, escalation, the feed) keep working, and
`focus_gp_margin_pct` = the report's own figure, for reference only.
"""
from __future__ import annotations

import time

from app.db_read import exec_sql

# the columns the migration appends; selected first, computed inline when the view predates it
_COLS = ("item_name, sku_code, product_name, category_name, list_price_bhd, net_amount_bhd, cogs_bhd, "
         "gross_profit_bhd, np_margin_pct, gp_margin_pct AS focus_gp_margin_pct, "
         "net_ex_vat_bhd, gp_computed_bhd, gp_ex_vat_bhd, margin_ex_vat_pct, is_below_cost, ex_vat_source")
_COLS_INLINE = ("item_name, sku_code, product_name, category_name, list_price_bhd, net_amount_bhd, cogs_bhd, "
                "gross_profit_bhd, np_margin_pct, gp_margin_pct AS focus_gp_margin_pct, "
                "ROUND((net_amount_bhd / 1.1)::numeric, 3) AS net_ex_vat_bhd, "
                "ROUND((net_amount_bhd - cogs_bhd)::numeric, 3) AS gp_computed_bhd, "
                "ROUND((net_amount_bhd / 1.1 - cogs_bhd)::numeric, 3) AS gp_ex_vat_bhd, "
                "ROUND((100.0 * (net_amount_bhd / 1.1 - cogs_bhd) / NULLIF(net_amount_bhd / 1.1, 0))::numeric, 2) "
                "AS margin_ex_vat_pct, "
                "(net_amount_bhd / 1.1 < cogs_bhd) AS is_below_cost, 'vat_rate'::text AS ex_vat_source")
_BASE_WHERE = "cogs_bhd IS NOT NULL AND net_amount_bhd IS NOT NULL"

# the whole-report totals (never limited by a row cap): view columns first, then the inline form
_TOTALS = ("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE is_below_cost) AS below, "
           "COALESCE(SUM(net_amount_bhd), 0) AS net, COALESCE(SUM(net_ex_vat_bhd), 0) AS net_ex, "
           "COALESCE(SUM(gp_ex_vat_bhd), 0) AS gp_ex, COALESCE(SUM(gp_computed_bhd), 0) AS gp_rep "
           f"FROM v_product_margin WHERE {_BASE_WHERE}")
_TOTALS_INLINE = ("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE net_amount_bhd / 1.1 < cogs_bhd) AS below, "
                  "COALESCE(SUM(net_amount_bhd), 0) AS net, "
                  "COALESCE(SUM(ROUND((net_amount_bhd / 1.1)::numeric, 3)), 0) AS net_ex, "
                  "COALESCE(SUM(ROUND((net_amount_bhd / 1.1 - cogs_bhd)::numeric, 3)), 0) AS gp_ex, "
                  "COALESCE(SUM(ROUND((net_amount_bhd - cogs_bhd)::numeric, 3)), 0) AS gp_rep "
                  f"FROM v_product_margin WHERE {_BASE_WHERE}")

# Whether v_product_margin carries the computed columns (economics_v2_migration.sql applied): probed
# once per _PROBE_TTL_S and remembered by every margin_rows() call, so the chat templates and the
# LLM schema hint can name the view's own columns once they exist and the inline formula before.
_PROBE_TTL_S = 300
_probe: dict = {"at": 0.0, "ok": None}


def _remember(ok: bool) -> None:
    _probe.update(at=time.time(), ok=ok)


def view_computes_margin(q=None, force: bool = False) -> bool:
    """True once v_product_margin has is_below_cost (the migration is applied). False when the
    probe cannot run (no database configured, the RPC down): callers then use the inline formula,
    which works on either version of the view."""
    if not force and _probe["ok"] is not None and time.time() - _probe["at"] < _PROBE_TTL_S:
        return bool(_probe["ok"])
    q = q or exec_sql
    try:
        q("SELECT is_below_cost FROM v_product_margin LIMIT 1")
        ok = True
    except Exception:  # noqa: BLE001 -- column missing, or no database here
        ok = False
    _remember(ok)
    return ok


def _finish(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["gp_margin_pct"] = r.get("margin_ex_vat_pct")
        r["is_below_cost"] = bool(r.get("is_below_cost"))
    return rows


def margin_rows(where: str = "", order: str = "margin_ex_vat_pct ASC NULLS LAST", limit: int = 200,
                q=None) -> tuple[list[dict], str]:
    """Rows of v_product_margin with the computed columns, (rows, basis) where basis is 'view'
    (migration applied) or 'inline' (computed from the base columns). `where` may reference the
    computed column names; inline mode rewrites them to their expressions."""
    q = q or exec_sql
    extra = f" AND ({where})" if where else ""
    try:
        rows = q(f"SELECT {_COLS} FROM v_product_margin WHERE {_BASE_WHERE}{extra} "
                 f"ORDER BY {order} LIMIT {int(limit)}") or []
        _remember(True)
        return _finish(rows), "view"
    except Exception:  # noqa: BLE001 -- the view predates economics_v2_migration.sql
        _remember(False)
    sub = (f"SELECT {_COLS_INLINE} FROM v_product_margin WHERE {_BASE_WHERE}")
    rows = q(f"SELECT * FROM ({sub}) m WHERE TRUE{extra} ORDER BY {order} LIMIT {int(limit)}") or []
    return _finish(rows), "inline"


def margin_totals(q=None) -> tuple[dict, str]:
    """Whole-report sums over every costed item of v_product_margin, independent of any row limit
    (the Margins page shows 200 rows ordered by margin ascending; the report has 161 items today
    and 186 active codes, so a row-capped sum would drop the best margins first and bias
    'Overall margin' low). Returns ({n, below, net, net_ex, gp_ex, gp_rep} as floats, basis)."""
    q = q or exec_sql
    try:
        row = (q(_TOTALS) or [{}])[0]
        basis = "view"
        _remember(True)
    except Exception:  # noqa: BLE001 -- the view predates the migration
        _remember(False)
        row = (q(_TOTALS_INLINE) or [{}])[0]
        basis = "inline"
    out = {k: float(row.get(k) or 0) for k in ("net", "net_ex", "gp_ex", "gp_rep")}
    out["n"] = int(float(row.get("n") or 0))
    out["below"] = int(float(row.get("below") or 0))
    return out, basis


def below_cost_rows(limit: int = 20) -> list[dict]:
    """Items whose ex-VAT sales are below COGS on the latest report, worst first."""
    rows, _ = margin_rows("is_below_cost", "gp_ex_vat_bhd ASC", limit)
    return rows


def thin_margin_rows(threshold_pct: float = 5.0, limit: int = 40) -> list[dict]:
    """Items under `threshold_pct` ex-VAT margin (negative ones included), thinnest first."""
    rows, _ = margin_rows(f"margin_ex_vat_pct < {float(threshold_pct)}", "margin_ex_vat_pct ASC", limit)
    return rows
