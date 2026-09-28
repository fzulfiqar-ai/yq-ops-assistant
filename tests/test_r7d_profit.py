"""R7d — profitability + master data (plan §17, §24 r7_master_data, §28; 27-Sep-2026).

    python -m tests.test_r7d_profit

Same lightweight runner as tests/test_r7c_order_heart.py. Every test runs on in-memory stand-ins: the
read-only RPC (exec_sql) is a function answering by SQL shape, the Supabase client a tiny table store.
Nothing reaches a database or the network (CI: SUPABASE_URL=https://ci.invalid); all data is synthetic.
One opt-in check reads production READ ONLY (YQ_R7D_LIVE=1 + DATABASE_URL) and SKIPs otherwise.

Covered:
  * the ex-VAT unit margin — one formula (app/margin_truth.py) equal to the migration's SQL, the cost
    sanity rule (missing / implausible under 10 % of the price), the Price Tracker recompute, the VAT
    setting read, the Check cost list;
  * one official margin (app/metrics.py) — Focus COGS ex-VAT headline with 100 % coverage, the landed
    (MRN) margin secondary with its coverage, the same figures on the Profitability page, the Dashboard
    tile and the Command Centre;
  * stock at COST — Focus average cost, else landed, never an implausible cost; the Inventory tiles and
    the Dashboard's "capital frozen" at cost, the selling value labelled beside it; failures isolated;
  * the margin floor — a flagged or missing cost is "no floor — needs a cost": never clamped, no
    headroom for a cart discount, listed by the rule preview; margin health tests "below floor" with the
    very floor the engine uses (a MARKUP on landed cost) and says "Min markup 20% on landed cost";
  * the category master — the plan (Accessories only, SIM never), apply with its log, the Focus pass
    leaving governed products alone, a no-op before the migration;
  * the three migrations and their reverses as text (live column lists, no DROP VIEW / CASCADE /
    security_invoker, REVOKE from anon + authenticated, log-first data changes, dry-run SELECTs);
  * the web pages' labels (official / landed / at cost / at selling price / Check cost / Min markup).
"""
from __future__ import annotations

import io
import os
import random
import re
import sys
import traceback
import types
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

TESTS: list = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class _Patched:
    """Swap module attributes for the duration of a with-block."""

    def __init__(self, *triples):
        self.triples = triples
        self.saved: list = []

    def __enter__(self):
        for mod, name, val in self.triples:
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, val)
        return self

    def __exit__(self, *exc):
        for mod, name, val in reversed(self.saved):
            setattr(mod, name, val)
        return False


# ── synthetic shop context (the same shape as tests/test_shop.py) ────────────────

def _item(code, price, stock=100, cat="CABLE", sold_90d=0):
    return {"item_code": code, "display_name": code, "spec": f"{code} spec", "category": cat, "brand": "VFAN",
            "standard_rate": price, "b2c_rate": None, "product_image_url": None, "package_image_url": None,
            "sort_order": None, "created_at": "2025-07-03T00:00:00+00:00", "moq": 1, "pack_size": None,
            "stock_qty": stock, "stock_as_of": "2026-09-14", "sold_30d": 0, "prev_30d": 0,
            "sold_90d": sold_90d, "customers_30d": 0}


def _rule(id, kind, item_codes=(), **kw):
    r = {"id": id, "kind": kind, "name": f"rule{id}", "stackable": False, "min_qty": None, "min_value_bhd": None,
         "pct_off": None, "amount_off_bhd": None, "fixed_price_bhd": None, "coupon_code": None, "starts_at": None,
         "ends_at": None, "max_uses": None, "uses": 0, "priority": 100,
         "scope": {"item_codes": [c.upper() for c in item_codes], "categories": [], "referral_codes": []}}
    r.update(kw)
    return r


def _ctx(items, rules=(), costs=None, sources=None, flags: bool = True, **settings):
    """A context as _build_context makes it: costs, their sources and (flags=True) the cost flags."""
    from app import shop
    vals = dict(shop.SETTING_DEFAULTS)
    vals.update({k: str(v) for k, v in settings.items()})
    ctx = {"settings": vals, "items": {i["item_code"]: i for i in items}, "order": [i["item_code"] for i in items],
           "costs": dict(costs or {}), "cost_sources": dict(sources or {}), "rules": list(rules), "salesmen": [],
           "loaded_at": ""}
    if flags:
        ctx["cost_flags"] = shop.cost_flags(ctx)
    return ctx


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the ex-VAT unit margin and the cost sanity rule (app/margin_truth.py)
# ═══════════════════════════════════════════════════════════════════════════════

@test("margin: the unit margin is on the ex-VAT price — T06 3.000 / 1.983 reads 27.3 %, not 33.9 %")
def _():
    from app import margin_truth as mt
    assert mt.ex_vat_margin_pct("3.000", "1.983") == 27.3
    assert mt.ex_vat_margin_pct(3.0, 1.983, Decimal("0")) == 33.9          # VAT 0 = the old VAT-inclusive reading
    assert mt.ex_vat(Decimal("3.000")) == Decimal("2.727") and mt.ex_vat("2.800") == Decimal("2.545")
    assert mt.ex_vat_margin_pct(None, 1) is None and mt.ex_vat_margin_pct(0, 1) is None
    assert mt.ex_vat_margin_pct(1.0, None) is None
    assert mt.ex_vat_margin_pct("1.100", "1.100") == -10.0                   # a cost at the VAT-inclusive price loses the VAT
    assert mt.ex_vat_margin_pct("1.100", "1.000") == 0.0


@test("margin: equal to the exact (p/(1+v) − c) ÷ (p/(1+v)), ties away from zero like SQL round() — 3,000 random fils prices")
def _():
    from fractions import Fraction
    from app import margin_truth as mt
    rng = random.Random(7)
    for _ in range(3000):
        p = Decimal(rng.randint(50, 30000)) / 1000
        c = Decimal(rng.randint(1, 30000)) / 1000
        v = Decimal(rng.choice([0, 5, 10, 15])) / 100
        px = Fraction(p) / (1 + Fraction(v))
        tenths = (px - Fraction(c)) / px * 1000                  # the margin in tenths of a percent, exactly
        mag = int(abs(tenths) + Fraction(1, 2))                  # half away from zero (numeric round, ROUND_HALF_UP)
        want = float(Decimal(mag if tenths >= 0 else -mag) / 10)
        assert mt.ex_vat_margin_pct(p, c, v) == want, (p, c, v, want)
    # the dividing-twice form rounds a true tie the wrong way; the one-division form does not
    assert mt.ex_vat_margin_pct(Decimal("11.364"), Decimal("29.357"), Decimal("0.05")) == -171.3


@test("cost sanity: missing / implausible (under 10 % of the price) / trusted — the one threshold everyone reads")
def _():
    from app import margin_truth as mt, shop
    assert mt.IMPLAUSIBLE_COST_SHARE == 0.10 and shop.COST_SANITY_SHARE == mt.IMPLAUSIBLE_COST_SHARE
    assert mt.cost_flag("2.800", "0.0114") == "implausible"
    assert mt.cost_flag("0.400", "0.012571") == "implausible"
    assert mt.cost_flag("18.000", "0.0113") == "implausible"
    assert mt.cost_flag("1.000", "0.2481") is None and mt.cost_flag("1.000", "0.100") is None   # exactly 10 % is trusted
    assert mt.cost_flag("1.000", None) == "missing" and mt.cost_flag("1.000", 0) == "missing"
    assert mt.cost_flag(None, "0.001") is None                                 # no price: nothing to compare with


@test("price tracker: margins recomputed ex-VAT with the view's own cost columns; cost_flag and sell_now_ex_vat added")
def _():
    from app import margin_truth as mt
    rows = [{"sku_code": "T06", "sell_now": 3.0, "sell_prev": 2.95, "cost_now": 1.983, "cost_prev": 1.6,
             "margin_now_pct": 33.9, "margin_before_pct": 45.8},
            {"sku_code": "UK20 (New)", "sell_now": 2.8, "sell_prev": None, "cost_now": 0.0114, "cost_prev": None,
             "margin_now_pct": 99.6, "margin_before_pct": None},
            {"sku_code": "NOCOST", "sell_now": 1.0, "sell_prev": None, "cost_now": None, "cost_prev": None,
             "margin_now_pct": None, "margin_before_pct": None}]
    out = {r["sku_code"]: r for r in mt.apply_ex_vat_margins(rows, Decimal("0.10"))}
    assert out["T06"]["margin_now_pct"] == 27.3 and out["T06"]["margin_before_pct"] == 40.3
    assert out["T06"]["sell_now_ex_vat"] == 2.727 and out["T06"]["cost_flag"] is None
    assert out["UK20 (New)"]["cost_flag"] == "implausible" and out["UK20 (New)"]["margin_before_pct"] is None
    assert out["NOCOST"]["cost_flag"] == "missing" and out["NOCOST"]["margin_now_pct"] is None


@test("price tracker: the endpoint applies the ex-VAT margins with the VAT setting, and says so")
def _():
    from app import db_read
    import app.main as m
    seen: list[str] = []

    def q(sql):
        seen.append(sql)
        if "FROM app_settings" in sql:
            return [{"value": "0.10"}]
        if "SELECT DISTINCT division" in sql:
            return [{"division": "Accessories", "brand": "VFAN", "category": "CABLE"}]
        return [{"sku_code": "T06", "sell_now": 3.0, "sell_prev": None, "cost_now": 1.983, "cost_prev": None,
                 "margin_now_pct": 33.9, "margin_before_pct": None, "sell_change_pct": 0, "cost_change_pct": 0}]
    with _Patched((db_read, "exec_sql", q), (db_read, "exec_sql_params", lambda s, p: q(s))):
        out = m.prices_tracker(None, None, None, False, None)
    assert out["rows"][0]["margin_now_pct"] == 27.3 and out["margin_basis"] == "ex_vat" and out["vat_rate"] == 0.1
    assert out["check_cost"] == 0 and any("v_price_tracker" in s for s in seen)


@test("vat_rate: the setting when it is a sane fraction, else 0.10 — never a crash")
def _():
    from app import margin_truth as mt
    assert mt.vat_rate(lambda s: [{"value": "0.05"}]) == Decimal("0.05")
    assert mt.vat_rate(lambda s: [{"value": "ten"}]) == Decimal("0.10")
    assert mt.vat_rate(lambda s: [{"value": "10"}]) == Decimal("0.10")        # 10 is a percentage typo, not a fraction
    assert mt.vat_rate(lambda s: []) == Decimal("0.10")

    def boom(s):
        raise RuntimeError("rpc down")
    assert mt.vat_rate(boom) == Decimal("0.10")


@test("check cost: implausible first, then missing, each with the reason in words; [] when the view fails")
def _():
    from app import margin_truth as mt
    assert "FROM v_product_economics" in mt.CHECK_COST_SQL and "0.1 * price_bhd" in mt.CHECK_COST_SQL
    rows = [{"sku_code": "UK20 (New)", "item_name": "UK20", "price_bhd": 2.8, "cost_bhd": 0.0114, "cost_source": "mrn"},
            {"sku_code": "NEW1", "item_name": "New cable", "price_bhd": 1.0, "cost_bhd": None, "cost_source": None},
            {"sku_code": "OK1", "item_name": "Fine", "price_bhd": 1.0, "cost_bhd": 0.5, "cost_source": "mrn"}]
    out = mt.check_cost_rows(lambda s: rows)
    assert [r["sku_code"] for r in out] == ["UK20 (New)", "NEW1"]
    assert out[0]["reason"] == "Cost BHD 0.0114 is under 10% of the BHD 2.800 price" and out[0]["cost_source"] == "mrn"
    assert out[1]["cost_flag"] == "missing" and out[1]["reason"].startswith("No cost on file")

    def boom(s):
        raise RuntimeError("no view")
    assert mt.check_cost_rows(boom) == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. one official margin (app/metrics.py)
# ═══════════════════════════════════════════════════════════════════════════════

TOTALS = {"n": 158, "below": 1, "net": 64904.83, "net_ex": 59005.23, "gp_ex": 22212.03, "gp_rep": 28108.17}
LANDED = {"costed_net_bhd": 55568.96, "gp_bhd": 18923.402, "acc_net_bhd": 58877.13}


@test("official margin: Focus COGS ex-VAT, 100 % coverage; landed margin secondary with its coverage")
def _():
    from app import metrics as m
    om = m.official_margin(TOTALS)
    assert om["pct"] == 37.6 and om["coverage_pct"] == 100.0 and om["below_cost"] == 1 and om["items"] == 158
    assert om["gp_bhd"] == 22212.03 and om["basis"] == "focus_cogs_ex_vat"
    lm = m.landed_margin(LANDED)
    assert lm["pct"] == 34.1 and lm["coverage_pct"] == 94.4 and lm["basis"] == "landed_mrn_ex_vat"
    assert m.official_margin(None) is None and m.landed_margin(None) is None and m.landed_margin({}) is None
    assert m.official_margin({"n": 0, "net_ex": 0, "gp_ex": 0})["pct"] is None


def _rpc(stock=None, fail=()):
    """An exec_sql stand-in answering every SQL the R7d report paths send."""
    stock = stock if stock is not None else STOCK_ROWS

    def q(sql):
        s = sql.strip()
        for key in fail:
            if key in s:
                raise RuntimeError(f"{key} unavailable")
        if "FROM v_product_margin" in s and "COUNT(*) AS n" in s:
            return [TOTALS]
        if "FROM v_product_margin" in s and "is_below_cost" in s and "SUM(" not in s:
            return [{"item_name": "UK03 20W Charger", "margin_ex_vat_pct": -20.9, "gp_ex_vat_bhd": -83.4,
                     "is_below_cost": True, "net_ex_vat_bhd": 399.8, "cogs_bhd": 483.2, "net_amount_bhd": 439.8}]
        if "AS costed_net_bhd" in s:
            return [LANDED]
        if "FROM v_stock_health h" in s:
            return stock
        if "FROM v_product_economics" in s and "cost_flag" in s:
            return [{"sku_code": "UK20 (New)", "item_name": "UK20", "price_bhd": 2.8, "cost_bhd": 0.0114, "cost_source": "mrn"}]
        if "FROM v_receivables" in s:
            return [{"total": 1000, "overdue": 400}]
        if "/90.0 AS d" in s:
            return [{"d": 50}]
        if "FROM v_stock_health" in s:
            return [{"item_name": "Item A", "current_stock": 10, "stock_value": 30, "sold_90d": 9, "days_cover": 100,
                     "suggested_reorder_qty": 0, "status": "healthy"},
                    {"item_name": "Item Dead", "current_stock": 5, "stock_value": 40, "sold_90d": 0, "days_cover": None,
                     "suggested_reorder_qty": 0, "status": "dead_stock"}]
        if "FROM stock_balance" in s:
            return [{"v": 70, "q": 15}]
        return []
    return q


STOCK_ROWS = [
    # item, division, status, on hand, selling value, cost, source
    {"item_name": "Item A", "division": "Accessories", "status": "healthy", "current_stock": 10, "sell_value_bhd": 30,
     "sold_90d": 9, "days_cover": 100, "cost_bhd": 1.5, "cost_source": "focus_avg"},
    {"item_name": "Item Dead", "division": "Accessories", "status": "dead_stock", "current_stock": 5, "sell_value_bhd": 40,
     "sold_90d": 0, "days_cover": None, "cost_bhd": 4.25, "cost_source": "mrn"},
    {"item_name": "Cardboard", "division": "Accessories", "status": "dead_stock", "current_stock": 26, "sell_value_bhd": 468,
     "sold_90d": 0, "days_cover": None, "cost_bhd": None, "cost_source": None},       # implausible cost: uncosted
    {"item_name": "Gone", "division": "Accessories", "status": "urgent_out_of_stock", "current_stock": 0,
     "sell_value_bhd": 0, "sold_90d": 4, "days_cover": 0, "cost_bhd": 2.0, "cost_source": "focus_avg"},
]


@test("profitability(): the portal reads the same official + landed figures; one failing half never costs the other")
def _():
    from app import metrics as m
    p = m.profitability(q=_rpc())
    assert p["official"]["pct"] == 37.6 and p["landed"]["pct"] == 34.1 and p["landed"]["coverage_pct"] == 94.4
    assert "Returns not yet deducted" in p["returns_note"] and "Focus COGS" in p["definition"]["official"]
    p = m.profitability(q=_rpc(fail=("AS costed_net_bhd",)))
    assert p["official"]["pct"] == 37.6 and p["landed"] is None
    p = m.profitability(q=_rpc(fail=("FROM v_product_margin",)))
    assert p["official"] is None and p["landed"]["pct"] == 34.1


@test("command centre: the official and landed tiles are built from the same two functions")
def _():
    from app import metrics as m
    r = m.Reader(q=lambda s: [], qp=lambda s, p: [], salesmen_fn=lambda: [])
    r.memo.update({"margin_totals": TOTALS, "landed": LANDED, "anchor": {"focus_to": "2026-09-24"}})
    ctx = m._make_ctx(r, "mtd")
    off = m.tile(ctx, "profit.official")
    land = m.tile(ctx, "profit.landed")
    assert off["value"] == m.official_margin(TOTALS)["pct"] == 37.6 and off["coverage_pct"] == 100.0
    assert land["value"] == m.landed_margin(LANDED)["pct"] and land["coverage_pct"] == 94.4
    assert off["label"] == m.OFFICIAL_MARGIN_LABEL and "Focus COGS" in off["basis"]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. stock at COST
# ═══════════════════════════════════════════════════════════════════════════════

@test("stock at cost: Focus average cost first, else landed; an implausible cost is never used; subqueries only")
def _():
    from app import metrics as m
    sql = m.STOCK_HELD_SQL
    assert "THEN f.avg_cost WHEN" in sql and "THEN e.cost_bhd END AS cost_bhd" in sql and "'focus_avg'" in sql
    assert "pm.cogs_bhd / u.units" in sql and "pm.ex_vat_source = 'day_book'" in sql
    assert sql.count("0.1 * e.price_bhd") == 4 and "COALESCE(e.price_bhd, 0) > 0" in sql
    assert "WITH " not in sql.upper().split("SELECT")[0], "no CTE: the RPC's relation check reads FROM / JOIN names"
    granted = {"v_stock_health", "v_product_economics", "v_product_margin", "v_sales"}
    for s in (m.STOCK_SQL, m.STOCK_HELD_SQL, m.LANDED_SQL, m.LANDED_LATEST_SQL):
        rels = set(re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)\b(?!\.)", s, re.I))
        assert rels <= granted, rels - granted
        assert ";" not in s and not re.search(r"\b(insert|update|delete|drop|alter|grant|create)\b", s, re.I)
    assert m.STOCK_SQL.endswith("WHERE h.division = 'Accessories'") and m.STOCK_HELD_SQL.endswith("WHERE h.current_stock > 0")
    assert "FROM v_stock_health h" in m.STOCK_SQL          # the Command Centre's stand-in keys on it
    assert m.LANDED_SQL.endswith("s.sale_date > $1::date - 365 AND s.sale_date <= $1::date")


@test("stock at cost: the summary values costed items only, counts the uncosted apart, dead stock at cost")
def _():
    from app import metrics as m
    s = m.stock_cost_summary(STOCK_ROWS)
    assert s["cost_value_bhd"] == 36.25 and s["sell_value_bhd"] == 538.0          # 10×1.5 + 5×4.25; 30+40+468
    assert (s["items_held"], s["items_costed"], s["items_uncosted"]) == (3, 2, 1)
    assert s["uncosted_sell_value_bhd"] == 468.0 and s["cost_coverage_pct"] == 66.7
    assert s["dead_cost_bhd"] == 21.25 and s["dead_sell_bhd"] == 508.0 and s["dead_count"] == 2 and s["dead_uncosted"] == 1
    assert s["by_source"] == {"focus_avg": 1, "mrn": 1}
    per = m.stock_cost_by_item(STOCK_ROWS)
    assert per == {"Item A": {"unit_cost_bhd": 1.5, "cost_value_bhd": 15.0, "cost_source": "focus_avg"},
                   "Item Dead": {"unit_cost_bhd": 4.25, "cost_value_bhd": 21.25, "cost_source": "mrn"}}
    assert m.stock_cost_summary([])["cost_value_bhd"] == 0.0 and m.stock_cost_summary(None)["items_held"] == 0


@test("dashboard health: official margin headline, landed beside it, dead stock at COST with the selling value labelled")
def _():
    from app import reports
    with _Patched((reports, "exec_sql", _rpc())):
        h = reports.business_health()
    assert h["gp_pct"] == 37.6 and h["margin_basis"] == "focus_cogs_ex_vat" and h["margin_available"] is True
    assert h["below_cost_count"] == 1 and h["landed_gp_pct"] == 34.1 and h["landed_coverage_pct"] == 94.4
    assert h["dead_stock_bhd"] == 21.25 and h["dead_stock_count"] == 2 and h["dead_stock_uncosted"] == 1
    assert h["dead_stock_sell_bhd"] == 508.0 and h["stock_basis"] == "cost"
    assert h["dso_days"] == 20.0 and h["ar_overdue_pct"] == 40.0
    # the stock read failing: no selling-price stand-in, the tile says so
    with _Patched((reports, "exec_sql", _rpc(fail=("FROM v_stock_health h",)))):
        h = reports.business_health()
    assert h["stock_basis"] is None and h["dead_stock_bhd"] == 0.0 and h["gp_pct"] == 37.6


@test("inventory: stock value at selling price AND at cost; each row carries its value at cost; a failing cost read keeps the page")
def _():
    from app import reports
    with _Patched((reports, "exec_sql", _rpc()), (reports, "recent_receipts", lambda: []),
                  (reports, "reserved_stock", lambda: {"available": False}), (reports, "stock_by_warehouse", lambda: [])):
        inv = reports.inventory()
        assert inv["stock_value"] == 70.0 and inv["stock_value_basis"] == "selling_price"
        assert inv["stock_value_cost"] == 36.25 and inv["stock_cost"]["items_uncosted"] == 1
        rows = {r["item_name"]: r for r in inv["rows"]}
        assert rows["Item A"]["cost_value_bhd"] == 15.0 and rows["Item A"]["cost_source"] == "focus_avg"
        assert rows["Item Dead"]["cost_value_bhd"] == 21.25
    with _Patched((reports, "exec_sql", _rpc(fail=("FROM v_stock_health h",))), (reports, "recent_receipts", lambda: []),
                  (reports, "reserved_stock", lambda: {"available": False}), (reports, "stock_by_warehouse", lambda: [])):
        inv = reports.inventory()
    assert inv["stock_cost"] is None and inv["stock_value_cost"] == 0.0 and inv["rows"] and inv["stock_value"] == 70.0


@test("profitability page: gp_pct is the official margin; landed + Check cost ride along; either failing leaves them empty")
def _():
    from app import margin_truth as mt, reports
    q = _rpc()
    with _Patched((reports, "exec_sql", q), (mt, "exec_sql", q)):
        out = reports.margins()
    assert round(out["gp_pct"], 1) == out["official"]["pct"] == 37.6
    assert out["landed"]["pct"] == 34.1 and out["landed"]["coverage_pct"] == 94.4
    assert [r["sku_code"] for r in out["check_cost"]] == ["UK20 (New)"] and "Returns not yet deducted" in out["returns_note"]
    q2 = _rpc(fail=("AS costed_net_bhd", "cost_flag"))
    with _Patched((reports, "exec_sql", q2), (mt, "exec_sql", q2)):
        out = reports.margins()
    assert out["landed"] is None and out["check_cost"] == [] and out["official"]["pct"] == 37.6


# ═══════════════════════════════════════════════════════════════════════════════
# 4. the margin floor: no floor on a flagged / missing cost, and one definition
# ═══════════════════════════════════════════════════════════════════════════════

@test("floor: a trusted cost floors at cost × 1.2 × 1.1; a flagged or missing cost has NO floor (floor_state says why)")
def _():
    from app import shop
    # costs are stored under the exact AND the UPPER-cased code, as app/shop.py _load_costs does
    ctx = _ctx([_item("T02", 2.95), _item("UK20 (New)", 2.8), _item("NOCOST", 1.0)],
               costs={"T02": 2.0, "UK20 (New)": 0.0114, "UK20 (NEW)": 0.0114},
               shop_min_margin_pct="0.20", shop_vat_rate="0.10")
    assert shop.floor_state(ctx, "T02") == "ok" and shop._floor_for(ctx, "T02") == 2.64
    assert shop.floor_state(ctx, "UK20 (New)") == "check_cost" and shop._floor_d(ctx, "UK20 (New)") is None
    assert shop.floor_state(ctx, "uk20 (new)") == "check_cost"       # the flag is found whatever the casing
    assert shop.floor_state(ctx, "NOCOST") == "no_cost" and shop._floor_d(ctx, "NOCOST") is None
    # a context without computed flags (a synthetic cart) keeps the old behaviour: the cost floors
    bare = _ctx([_item("UK20 (New)", 2.8)], costs={"UK20 (New)": 0.0114}, flags=False, shop_vat_rate="0.10")
    assert shop._floor_for(bare, "UK20 (New)") == 0.015


@test("floor: a flagged line is never clamped and lends no headroom to a cart coupon (cap from trusted lines only)")
def _():
    from app import shop
    items = [_item("GOOD", 10.0), _item("BAD", 10.0)]
    rules = [_rule(8, "coupon", coupon_code="HALF", pct_off=50),
             _rule(9, "qty_tier", item_codes=["BAD"], min_qty=5, fixed_price_bhd=0.1)]
    costs = {"GOOD": 7.0, "BAD": 0.5}                   # 0.5 is 5 % of 10.000: flagged
    flagged = _ctx(items, rules, costs, shop_min_margin_pct="0.20", shop_vat_rate="0")
    assert set(flagged["cost_flags"]) == {"BAD"}
    q = shop.price_cart([{"item_code": "GOOD", "qty": 1}, {"item_code": "BAD", "qty": 1}], coupon_code="HALF", ctx=flagged)
    assert q["discount_bhd"] == 1.6 and q["total_bhd"] == 18.4, q        # GOOD's headroom only (10 − 8.4)
    unflagged = _ctx(items, rules, costs, flags=False, shop_min_margin_pct="0.20", shop_vat_rate="0")
    q = shop.price_cart([{"item_code": "GOOD", "qty": 1}, {"item_code": "BAD", "qty": 1}], coupon_code="HALF", ctx=unflagged)
    assert q["discount_bhd"] == 10.0, q                                  # the bogus cost used to lend 9.4 of headroom
    # the tier on BAD: no floor, so it is not clamped (and the rule editor lists it as "no floor")
    q = shop.price_cart([{"item_code": "BAD", "qty": 5}], ctx=flagged)
    assert q["lines"][0]["unit_price_bhd"] == 0.1, q["lines"][0]
    q = shop.price_cart([{"item_code": "BAD", "qty": 5}], ctx=unflagged)
    assert q["lines"][0]["unit_price_bhd"] == 0.6, q["lines"][0]         # clamped against the bogus 0.5 × 1.2


@test("rule preview: breaches as before, plus every touched item with no floor and why")
def _():
    from app import shop
    ctx = _ctx([_item("T02", 2.95), _item("UK20 (New)", 2.8), _item("NOCOST", 1.0), _item("OTHER", 1.0, cat="CHARGER")],
               costs={"T02": 2.0, "UK20 (New)": 0.0114}, shop_min_margin_pct="0.20", shop_vat_rate="0.10")
    rule = {"kind": "qty_tier", "min_qty": 10, "pct_off": 20, "scope": {"categories": ["CABLE"]}}
    imp = shop.rule_impact(rule, ctx)
    assert imp["items"] == 3 and imp["breach_count"] == 1 and imp["breaches"][0]["item_code"] == "T02"
    assert imp["breaches"][0]["floor_bhd"] == 2.64 and imp["breaches"][0]["unit_bhd"] == 2.36
    assert imp["no_floor_count"] == 2
    assert {(x["item_code"], x["reason"]) for x in imp["no_floor"]} == {("UK20 (New)", "check_cost"), ("NOCOST", "no_cost")}
    assert {x["label"] for x in imp["no_floor"]} == {"cost flagged: check it", "no cost on file"}


@test("margin health: 'below floor' is the engine's own floor (a MARKUP on landed cost); a 22 % markup is fine")
def _():
    from app import shop
    # vat 0.10, min markup 0.20. MARK22: cost 2.0 → floor 2.64; price 2.684 ex-VAT 2.44 = a 22 % markup (18 % margin)
    ctx = _ctx([_item("MARK22", 2.684), _item("UNDER", 2.6), _item("UK20 (New)", 2.8), _item("NOCOST", 1.0)],
               costs={"MARK22": 2.0, "UNDER": 2.0, "UK20 (New)": 0.0114}, shop_min_margin_pct="0.20", shop_vat_rate="0.10")
    with _Patched((shop, "context", lambda: ctx)):
        mh = shop.margin_health()
    rows = {r["item_code"]: r for r in mh["rows"]}
    assert rows["MARK22"]["status"] == "ok" and rows["MARK22"]["margin_pct"] < 0.2 and rows["MARK22"]["markup_pct"] >= 0.2
    assert rows["UNDER"]["status"] == "below_floor" and rows["UNDER"]["floor_bhd"] == 2.64
    assert rows["UK20 (New)"]["status"] == "check_cost" and rows["UK20 (New)"]["floor_bhd"] is None
    assert rows["UK20 (New)"]["cost_flag"] and rows["NOCOST"]["status"] == "no_cost"
    assert [r["item_code"] for r in mh["rows"]][:2] == ["UNDER", "UK20 (New)"]      # below floor first, then check cost
    sm = mh["summary"]
    assert sm["floor_label"] == "Min markup 20% on landed cost" and sm["floor_basis"] == "markup_on_landed"
    assert sm["min_markup_pct"] == sm["min_margin_pct"] == 0.2
    assert (sm["below_floor"], sm["check_cost"], sm["no_cost"], sm["flagged_costs"]) == (1, 1, 1, 1)


@test("margin health agrees with the engine: below_floor ⇔ the list price is under _floor_d (500 random items)")
def _():
    from app import shop
    rng = random.Random(11)
    items, costs = [], {}
    for i in range(500):
        price = Decimal(rng.randint(100, 20000)) / 1000
        items.append(_item(f"R{i}", float(price)))
        if rng.random() < 0.9:
            costs[f"R{i}"] = float(Decimal(rng.randint(1, 20000)) / 1000)
    ctx = _ctx(items, costs=costs, shop_min_margin_pct=rng.choice(["0.15", "0.20", "0.30"]), shop_vat_rate="0.10")
    with _Patched((shop, "context", lambda: ctx)):
        mh = shop.margin_health()
    for r in mh["rows"]:
        floor = shop._floor_d(ctx, r["item_code"])
        lp = Decimal(str(ctx["items"][r["item_code"]]["standard_rate"]))
        assert (r["status"] == "below_floor") == (floor is not None and lp < floor), r
        assert (r["floor_bhd"] is None) == (floor is None)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. the category master
# ═══════════════════════════════════════════════════════════════════════════════

CATS = [{"id": 2, "name": "Cable", "division": "Accessories"}, {"id": 4, "name": "Car Charger", "division": "Accessories"},
        {"id": 9, "name": "Power Bank", "division": "Accessories"}, {"id": 10, "name": "Sim", "division": "SIM"},
        {"id": 11, "name": "Wireless HFs", "division": "Accessories"}, {"id": 8, "name": "Postpaid Giveaway", "division": "Giveaway"}]
MASTER = [{"catalog_category": "CABLE", "category_id": 2}, {"catalog_category": "POWER BANK", "category_id": 9},
          {"catalog_category": "BLUETOOTH HEADSET", "category_id": 11}, {"catalog_category": "ODD", "category_id": 8}]
CATALOG = [{"id": 1, "item_code": "F16", "category": "POWER BANK", "division": "Accessories"},
           {"id": 2, "item_code": "X24 CC", "category": "CABLE", "division": "Accessories"},
           {"id": 3, "item_code": "T02", "category": "BLUETOOTH HEADSET", "division": "Accessories"},
           {"id": 4, "item_code": "SIMKIT", "category": "CABLE", "division": "Accessories"},
           {"id": 5, "item_code": "X01", "category": "CABLE", "division": "Accessories"},
           {"id": 6, "item_code": "GIFT", "category": "ODD", "division": "Accessories"},
           {"id": 7, "item_code": "NOTACC", "category": "CABLE", "division": "SIM"}]
PRODUCTS = [{"id": 101, "sku_code": "F16", "category_id": 2},         # Focus: Cable → Power Bank
            {"id": 102, "sku_code": "x24 cc", "category_id": 4},      # Focus: Car Charger → Cable (case-insensitive code)
            {"id": 103, "sku_code": "T02", "category_id": None},      # uncategorised → Wireless HFs
            {"id": 104, "sku_code": "SIMKIT", "category_id": 10},     # a SIM product: never moved
            {"id": 105, "sku_code": "X01", "category_id": 2},         # already right
            {"id": 106, "sku_code": "GIFT", "category_id": None},     # the master points outside Accessories: not moved
            {"id": 107, "sku_code": "NOTACC", "category_id": None},   # a non-Accessories catalog row: not moved
            {"id": 108, "sku_code": "NOCAT", "category_id": 4}]       # not in the catalog: Focus stands


@test("category master: the plan moves Accessories products to the catalog's category — never SIM, never out of Accessories")
def _():
    from scripts import category_backfill as cb
    plan = cb.plan_category_master(MASTER, CATS, CATALOG, PRODUCTS)
    assert [(x["product_id"], x["old_category_id"], x["new_category_id"]) for x in plan] == [
        (101, 2, 9), (102, 4, 2), (103, None, 11)]
    assert set(cb.master_targets(MASTER, CATS, CATALOG, PRODUCTS)) == {101, 102, 103, 105}


class _Q:
    def __init__(self, store, name):
        self.store, self.name, self.op, self.payload, self.ids = store, name, "select", None, None

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def upsert(self, row, **k):
        self.op, self.payload = "upsert", row
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def update(self, vals):
        self.op, self.payload = "update", vals
        return self

    def in_(self, col, ids):
        self.ids = list(ids)
        return self

    def execute(self):
        rows = self.store.tables[self.name]
        if self.op == "select":
            a, b = getattr(self, "rng", (0, 10 ** 6))
            return types.SimpleNamespace(data=[dict(r) for r in rows][a:b + 1])
        if self.op == "insert":
            self.store.writes.append((self.name, "insert", list(self.payload)))
            rows.extend(dict(r) for r in self.payload)
            return types.SimpleNamespace(data=self.payload)
        if self.op == "update":
            self.store.writes.append((self.name, "update", dict(self.payload), list(self.ids)))
            for r in rows:
                if r.get("id") in self.ids:
                    r.update(self.payload)
            return types.SimpleNamespace(data=[])
        if self.op == "upsert":
            self.store.writes.append((self.name, "upsert", dict(self.payload)))
            return types.SimpleNamespace(data=[])
        raise AssertionError(self.op)


class _Store:
    def __init__(self, tables):
        self.tables, self.writes = tables, []

    def table(self, name):
        if name not in self.tables:
            raise RuntimeError(f'relation "{name}" does not exist')
        return _Q(self, name)


def _store(master=True):
    t = {"categories": [dict(c) for c in CATS], "catalog_items": [dict(c) for c in CATALOG],
         "products": [dict(p) for p in PRODUCTS], "product_aliases": []}
    if master:
        t["category_master"] = [dict(m) for m in MASTER]
        t["category_master_log"] = []
    return _Store(t)


@test("category master: apply logs every move first, then re-points; a second run changes nothing; no-op before the migration")
def _():
    from scripts import category_backfill as cb
    st = _store()
    out = cb.apply_category_master(st, source="test")
    assert out == {"ok": True, "changed": 3}
    kinds = [(w[0], w[1]) for w in st.writes]
    assert kinds[0] == ("category_master_log", "insert") and all(k[0] == "products" for k in kinds[1:])
    assert {r["product_id"] for r in st.tables["category_master_log"]} == {101, 102, 103}
    assert all(r["source"] == "test" for r in st.tables["category_master_log"])
    cats = {p["id"]: p["category_id"] for p in st.tables["products"]}
    assert cats == {101: 9, 102: 2, 103: 11, 104: 10, 105: 2, 106: None, 107: None, 108: 4}
    assert cb.apply_category_master(st) == {"ok": True, "changed": 0}
    none = _store(master=False)
    out = cb.apply_category_master(none)
    assert out["ok"] is False and not none.writes


@test("category backfill: the Focus item-group pass leaves master-governed products alone, then the master applies")
def _():
    from scripts import category_backfill as cb
    st = _store()
    st.tables["product_aliases"] = [{"alias_text": "F16 PB 20000mah", "product_id": 101},
                                    {"alias_text": "NOCAT thing", "product_id": 108}]
    pairs = [("F16 PB 20000mah", "Cable"), ("NOCAT thing", "Cable")]
    with _Patched((cb, "parse_categories", lambda folder: ("x.xlsx", pairs)), (cb, "get_client", lambda: st)):
        out = cb.backfill("anywhere")
    assert out["ok"] and out["matched"] == 2 and out["category_master"] == {"ok": True, "changed": 3}
    focus_updates = [w for w in st.writes if w[0] == "products" and w[1] == "update" and 108 in w[3]]
    assert focus_updates and not [w for w in st.writes if w[0] == "products" and w[1] == "update"
                                  and 101 in w[3] and w[2]["category_id"] == 2], "F16 must not flip back to Cable"
    cats = {p["id"]: p["category_id"] for p in st.tables["products"]}
    assert cats[101] == 9 and cats[108] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the migrations and their reverses, as text
# ═══════════════════════════════════════════════════════════════════════════════

ECON_COLS = ["sku_code", "item_name", "price_bhd", "cost_bhd", "margin_bhd", "margin_pct", "cost_source",
             "cost_effective_date", "cost_doc_no"]
TRACKER_COLS = ["sku_code", "item_name", "brand", "division", "category", "sell_now", "sell_prev", "sell_changed_on",
                "sell_change_pct", "cost_now", "cost_prev", "last_bought_on", "cost_source", "cost_change_pct",
                "margin_now_pct", "margin_before_pct"]


def _view_body(sql: str, view: str) -> str:
    sql = re.sub(r"--[^\n]*", "", sql)
    m = re.search(rf"create or replace view {view} as\s+(.*?);\s*\n", sql, re.S | re.I)
    assert m, f"{view} not re-created"
    return m.group(1)


def _outer_select_cols(body: str) -> list[str]:
    """The output column names of the outermost SELECT (the last top-level 'select … from')."""
    depth, i, start = 0, 0, None
    low = body.lower()
    while i < len(low):
        ch = low[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and low.startswith("select", i) and (i == 0 or not low[i - 1].isalnum()):
            start = i + 6
        i += 1
    frm = re.search(r"\n\s*from\s", low[start:])
    items, cur, depth = [], "", 0
    for ch in body[start:start + frm.start()]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    out = []
    for it in items:
        it = " ".join(it.split())
        m = re.search(r"\bas\s+([a-z_][a-z0-9_]*)$", it, re.I)
        out.append(m.group(1) if m else it.split(".")[-1])
    return out


@test("migration r7d_margin_exvat: the live 9 + 16 columns in order, ex-VAT formula, grants restated, no drop / cascade")
def _():
    sql = _read("scripts/r7d_margin_exvat_migration.sql")
    low = re.sub(r"--[^\n]*", "", sql).lower()
    econ, tracker = _view_body(sql, "v_product_economics"), _view_body(sql, "v_price_tracker")
    assert _outer_select_cols(econ) == ECON_COLS, _outer_select_cols(econ)
    assert _outer_select_cols(tracker) == TRACKER_COLS, _outer_select_cols(tracker)
    assert "(pl.price_bhd - c.cost_bhd * (1 + vat.rate)) / pl.price_bhd" in econ
    assert "* (1 + vat.rate))" in tracker and "(sh.sell_prev - coalesce(po.cost_prev, mrn.cost_prev) * (1 + vat.rate))" in tracker
    assert low.count("key = 'shop_vat_rate'") == 2 and low.count("0.10) as rate") == 2
    for bad in ("drop view", "cascade", "security_invoker", "delete ", "insert ", "update ", "truncate"):
        assert bad not in low, bad
    assert "revoke all on v_product_economics, v_price_tracker from anon, authenticated;" in low
    assert "grant select on v_product_economics, v_price_tracker to yq_readonly;" in low
    assert "comment on column v_product_economics.margin_pct is" in low and "ex-vat price" in low
    assert "comment on column v_stock_health.stock_value is" in low and "at selling price" in low
    assert "not a percentage" in low and "raise exception" in low


@test("reverse r7d_margin_exvat: the pre-R7d VAT-inclusive bodies, same columns, comments cleared, no drop")
def _():
    sql = _read("scripts/r7d_margin_exvat_reverse.sql")
    low = re.sub(r"--[^\n]*", "", sql).lower()
    econ, tracker = _view_body(sql, "v_product_economics"), _view_body(sql, "v_price_tracker")
    assert _outer_select_cols(econ) == ECON_COLS and _outer_select_cols(tracker) == TRACKER_COLS
    assert "round(100.0 * (pl.price_bhd - c.cost_bhd) / pl.price_bhd, 1)" in econ and "vat" not in econ.lower()
    assert "/ s.sell_now * 100, 1)" in tracker and "vat.rate" not in tracker.lower() and "shop_vat_rate" not in tracker
    assert "drop view" not in low and "cascade" not in low
    assert low.count(" is null;") == 14 and "revoke all on v_product_economics, v_price_tracker from anon, authenticated;" in low


@test("migration r7d_category_master: master + log (RLS, no grants), log-first re-point, Accessories only, dry run, preview")
def _():
    sql = _read("scripts/r7d_category_master_migration.sql")
    body = re.sub(r"--[^\n]*", "", sql).lower()
    assert "create table if not exists category_master (" in body and "create table if not exists category_master_log (" in body
    assert "alter table category_master enable row level security;" in body
    assert "alter table category_master_log enable row level security;" in body
    assert "revoke all on category_master from anon, authenticated;" in body
    assert "revoke all on category_master_log from anon, authenticated;" in body
    assert "grant " not in body.replace("role_table_grants", "")
    assert "on conflict (catalog_category) do nothing" in body
    i_log, i_upd = body.index("insert into category_master_log"), body.index("update products p")
    assert i_log < i_upd, "every move is logged before products change"
    assert "coalesce(cur.division, 'accessories') = 'accessories'" in body and "cat.division = 'accessories'" in body
    assert "delete " not in body and "drop " not in body and "cascade" not in body and "truncate" not in body
    for s in ("-- dry run (read-only", "58 products move", "f04, f16, f25, k105", "no division total"):
        assert s in sql.lower(), s
    for cat, name in (("CABLE", "Cable"), ("POWER BANK", "Power Bank"), ("BLUETOOTH HEADSET", "Wireless HFs"),
                      ("EARPHONE", "Headphones"), ("BLUETOOTH SPEAKER", "Wireless Speaker")):
        assert re.search(rf"\('{cat}',\s+'{name}'", sql), cat


@test("reverse r7d_category_master: restore from the FIRST log row while the product still holds the LAST, then drop, no cascade")
def _():
    sql = _read("scripts/r7d_category_master_reverse.sql")
    body = re.sub(r"--[^\n]*", "", sql).lower()
    assert "order by product_id, id asc" in body and "order by product_id, id desc" in body
    assert "p.category_id is not distinct from l.new_category_id" in body
    assert "drop table if exists category_master_log;" in body and "drop table if exists category_master;" in body
    assert "cascade" not in body and "delete " not in body


@test("migration r7d_master_data: only-NULL fills logged first, the rep columns, salesman_id + FK + trigger, dry run")
def _():
    sql = _read("scripts/r7d_master_data_migration.sql")
    body = re.sub(r"--[^\n]*", "", sql).lower()
    assert "create table if not exists master_data_seed_log (" in body
    assert "alter table master_data_seed_log enable row level security;" in body
    assert "revoke all on master_data_seed_log from anon, authenticated;" in body
    assert body.count("c.segment is null") == 2 and body.count("c.area is null") == 2
    assert body.index("insert into master_data_seed_log") < body.index("update customers c")
    assert "alter table salesmen add column if not exists territory     text;" in body
    assert "alter table salesmen add column if not exists focus_aliases text[];" in body
    assert "alter table salesman_targets add column if not exists salesman_id bigint;" in body
    assert "references salesmen(id) on delete set null" in body
    assert "create index if not exists salesman_targets_salesman_id_period_idx on salesman_targets (salesman_id, period);" in body
    assert "revoke all on function salesman_targets_fill_salesman_id() from public, anon, authenticated;" in body
    assert "before insert or update on salesman_targets" in body and "set search_path = public" in body
    assert not re.search(r"\bdelete\s+from\b", body) and "drop table" not in body and "cascade" not in body
    assert "security definer" not in body and "on delete set null" in body
    assert "primary key (salesman" not in body, "the name stays the key; no constraint is replaced"
    for s in ("-- dry run (read-only)", "retail 89, key account 9, cash customer group 2, mt 1", "15 rows, all 15 resolve"):
        assert s in sql.lower(), s


@test("reverse r7d_master_data: clear only what the seed wrote, drop trigger / function / index / FK / columns, no cascade")
def _():
    sql = _read("scripts/r7d_master_data_reverse.sql")
    body = re.sub(r"--[^\n]*", "", sql).lower()
    assert "c.segment is not distinct from l.new_value" in body and "c.area is not distinct from l.new_value" in body
    for s in ("drop trigger if exists salesman_targets_fill_salesman_id on salesman_targets;",
              "drop function if exists salesman_targets_fill_salesman_id();",
              "drop index if exists salesman_targets_salesman_id_period_idx;",
              "alter table salesman_targets drop column if exists salesman_id;",
              "alter table salesmen drop column if exists territory;",
              "alter table salesmen drop column if exists focus_aliases;",
              "drop table if exists master_data_seed_log;"):
        assert s in body, s
    assert "cascade" not in body and "delete " not in body


@test("docs: MIGRATIONS.md names the R7d owner of both margin views and warns against re-running the old files")
def _():
    doc = _read("docs/MIGRATIONS.md")
    assert "| `v_price_tracker` | `r7d_margin_exvat_migration.sql`" in doc
    assert "| `v_product_economics` | `r7d_margin_exvat_migration.sql`" in doc
    assert "## Release R7d" in doc and "r7d_category_master_migration.sql" in doc and "r7d_master_data_migration.sql" in doc


# ═══════════════════════════════════════════════════════════════════════════════
# 7. the portal pages
# ═══════════════════════════════════════════════════════════════════════════════

@test("web: Profitability headline is official, landed shows its coverage, Check cost + returns note")
def _():
    src = _read("web/src/pages/Margins.tsx")
    for s in ("Gross margin (official)", "Landed margin (MRN)", "of Accessories sales", "Check cost",
              "Returns not yet deducted", "every item costed", "data.check_cost"):
        assert s in src, s


@test("web: Inventory and Dashboard value stock at cost and label the selling-price figure")
def _():
    inv = _read("web/src/pages/Inventory.tsx")
    for s in ("Stock value (at cost)", "Stock value (at selling price)", "Dead stock (at cost)", "label: 'At cost'",
              "· at selling price"):
        assert s in inv, s
    assert "Stock value (selling)" not in inv
    dash = _read("web/src/pages/Dashboard.tsx")
    assert "Gross margin · ex-VAT on Focus COGS" in dash and "Capital frozen in dead stock · at cost" in dash
    assert "True gross margin · landed cost" not in dash and "at selling price" in dash


@test("rc review (8): dead stock at cost says how many dead items have no cost (the count and the value cover the same items)")
def _():
    inv = _read("web/src/pages/Inventory.tsx")
    assert "deadUncostedNote(sc.dead_count, sc.dead_uncosted)" in inv and "from '@/lib/basisText'" in inv
    dash = _read("web/src/pages/Dashboard.tsx")
    assert "deadUncostedNote(data.health.dead_stock_count, data.health.dead_stock_uncosted)" in dash
    assert "from '@/lib/basisText'" in dash
    helper = _read("web/src/lib/basisText.ts")
    assert "without a usable cost, left out of the value" in helper
    # the API already carries both numbers (the pure summary counts an uncosted dead item apart)
    from app import metrics as m
    s = m.stock_cost_summary([
        {"item_name": "Synthetic A", "status": "dead_stock", "current_stock": 4, "sell_value_bhd": 8, "cost_bhd": 1.0},
        {"item_name": "Synthetic B", "status": "dead_stock", "current_stock": 2, "sell_value_bhd": 6, "cost_bhd": None},
    ])
    assert (s["dead_count"], s["dead_uncosted"], s["dead_cost_bhd"]) == (2, 1, 4.0), s


@test("web: Price Tracker margins say ex-VAT and flag an implausible cost; the rule editor and margin health say no floor / min markup")
def _():
    pt = _read("web/src/pages/PriceTracker.tsx")
    assert "Margin now (ex-VAT)" in pt and "Check cost" in pt and "r.cost_flag === 'implausible'" in pt
    assert "Margin = (sell − landed base cost) ÷ sell" not in pt
    sr = _read("web/src/pages/ShopRules.tsx")
    for s in ("no floor — needs a cost", "Min markup", "check_cost: 'Check cost'", "no_floor_count", "floor_label"):
        assert s in sr, s
    assert 'label="Min margin"' not in sr
    st = _read("web/src/pages/Settings.tsx")
    assert "label: 'Minimum markup'" in st and "label: 'Minimum margin'" not in st


# ═══════════════════════════════════════════════════════════════════════════════
# 7b. R7e: the admin Dashboard on the Command Centre's basis (only ADDED fields)
# ═══════════════════════════════════════════════════════════════════════════════

# the kpis block exactly as it was: daily_summary feeds the email digests, and scripts/reconcile_check.py
# compares kpis.total_receivables against the AR report
OLD_KPI_KEYS = {"rev_today", "net_today", "orders_today", "rev_yesterday", "orders_yesterday", "rev_mtd", "net_mtd",
                "orders_mtd", "rev_prev_month", "rev_prev_month_mtd", "prev_month_through", "total_receivables",
                "low_stock_count", "overdue_count", "overdue_total_bhd", "current_receivables_bhd"}
OLD_DASH_KEYS = {"data_as_of", "data_stale", "data_days_behind", "actions", "kpis", "health", "movers", "top_customers",
                 "revenue_trend", "by_channel", "by_salesman", "by_salesman_scope", "alerts", "daily_mtd", "by_payment",
                 "by_division", "pace", "attainment"}


def _dash_base() -> dict:
    s = {"rev_today": 110.0, "net_today": 100.0, "orders_today": 3, "rev_yesterday": 55.0, "orders_yesterday": 2,
         "rev_mtd": 1573.0, "net_mtd": 1430.0, "orders_mtd": 40, "rev_prev_month": 2000.0, "rev_prev_month_mtd": 1500.0,
         "prev_month_through": "2026-08-27", "total_receivables": 10495.0, "overdue_accounts": 7,
         "overdue_receivables_bhd": 4000.0, "current_receivables_bhd": 6495.0,
         "top_customers": [{"customer_name": "Shop A", "total_revenue_bhd": 500.0, "order_count": 4}],
         "data_date": "2026-09-27"}
    return {"s": s, "a": {"low_stock_count": 36, "negative_margin_count": 0}, "health": {}, "movers": {},
            "trend": [{"period_month": "2026-09-01", "gross_bhd": 1573.0}], "channel": [{"channel": "B2B"}],
            "fresh": {"stale": False, "days_behind": 1}, "daily_mtd": [], "attainment": {"rows": [], "error": None},
            "split": {"by_payment": [], "by_division": [{"division": "Accessories", "net_ex_vat_bhd": 930.0}]}}


CC_STUB = {"sales_mtd": {"key": "sales.accessories", "value": 930.0, "delta_pct": 5.2, "invoices": 41,
                         "basis": "Accessories · ex-VAT · 1–27 Sep vs the same 19 business days last month"},
           "sales_day": {"key": "sales.accessories", "value": 120.0, "invoices": 3}, "channels": None,
           "ar_total": {"key": "ar.total", "value": 10050.0, "source": "focus_total"}, "ar_over90": None,
           "compare_basis": "the same 19 business days last month", "focus": {"label": "1–27 Sep"},
           "day": {"end": "2026-09-27"}, "day_compare": "24 Sep"}


@test("R7e dashboard payload: command / revenue_trend_acc / top_customers_acc are ADDED; kpis and the old fields unchanged; agents gone")
def _():
    from app import reports
    from app import settings as app_settings
    trend = [{"period_month": "2026-09-01", "acc_net_bhd": 930.0, "invoices": 41, "partial": True, "through": "2026-09-27"}]
    top = [{"customer_name": "Shop A", "net_bhd": 420.0, "invoices": 4}]
    # the pace reads the monthly target setting: a stand-in, never the database
    with _Patched((app_settings, "setting", lambda key: 10000.0 if key == "monthly_sales_target_bhd" else None)):
        out = reports._assemble_dashboard({**_dash_base(), "cc": CC_STUB, "trend_acc": trend, "top_acc": top})
        old = reports._assemble_dashboard({**_dash_base(), "agents": []})
    assert set(out) == OLD_DASH_KEYS | {"command", "revenue_trend_acc", "top_customers_acc"}, set(out) ^ OLD_DASH_KEYS
    assert "agents" not in out, "the AI Agent Team panel is off the Dashboard"
    assert out["command"] is CC_STUB and out["revenue_trend_acc"] == trend and out["top_customers_acc"] == top
    k = out["kpis"]
    assert set(k) == OLD_KPI_KEYS, set(k) ^ OLD_KPI_KEYS
    assert (k["rev_mtd"], k["total_receivables"], k["overdue_count"], k["overdue_total_bhd"]) == (1573.0, 10495.0, 7, 4000.0)
    # the older VAT-inclusive fields still travel, untouched
    assert out["top_customers"][0]["total_revenue_bhd"] == 500.0 and out["revenue_trend"][0]["gross_bhd"] == 1573.0
    assert out["by_channel"] == [{"channel": "B2B"}] and out["pace"]["mtd_bhd"] == 930.0
    assert out["pace"]["target_bhd"] == 10000.0 and out["pace"]["business_days_total"] == 22
    # an older caller's dict (no cc / trend_acc / top_acc: test_r7b_command, test_r3_reps) still assembles
    assert old["command"] is None and old["revenue_trend_acc"] == [] and old["top_customers_acc"] == []
    # review: a FAILED top-customers read travels as None (the card says "could not be read just now"), a real
    # month with no named-account sales as [] — never the same thing on the page
    with _Patched((app_settings, "setting", lambda key: None)):
        failed = reports._assemble_dashboard({**_dash_base(), "top_acc": None})
        empty = reports._assemble_dashboard({**_dash_base(), "top_acc": []})
    assert failed["top_customers_acc"] is None and empty["top_customers_acc"] == []
    assert "agents" not in old
    # the command block is sales and receivables only: no cost, margin, kickback or referral field rides along
    blob = str(out["command"]).lower()
    for word in ("kickback", "referral", "gp_bhd", "margin", "cost"):
        assert word not in blob, word


def _ov(period: str, focus: dict, compare: dict, modules: list) -> dict:
    return {"period": {"key": period, "label": period, "focus": focus, "compare": compare}, "modules": modules}


def _t(key: str, available: bool = True, value: float = 1.0, **kw) -> dict:
    """A tile as app.metrics.tile returns it (an unavailable one carries no figures)."""
    base = {"key": key, "label": key, "unit": "bhd", "basis": f"{key} basis", "drill": None, "available": available}
    return {**base, "value": value, **kw} if available else {**base, "value": None, "note": "Could not be computed."}


def _cc_overviews(receivables: bool = True, day_ok: bool = True) -> dict:
    mtd_mods = [{"key": "attention", "tiles": [], "items": []},
                {"key": "sales", "tiles": [
                    _t("sales.accessories", value=930.0, delta_pct=5.2, invoices=41,
                       compare={"value": 884.0, "label": "1–25 Aug"}),
                    _t("sales.pace", value=1136.667),
                    _t("sales.channels", value=930.0, chips=[{"label": "B2B", "value": 700.0, "unit": "bhd", "share_pct": 75.3},
                                                             {"label": "B2C", "value": 230.0, "unit": "bhd", "share_pct": 24.7}])]}]
    if receivables:
        mtd_mods.append({"key": "receivables", "tiles": [_t("ar.total", value=10050.0, source="focus_total", accounts=44),
                                                          _t("ar.over90", value=12.5, over_90_bhd=1256.25)]})
    day_mods = [{"key": "sales", "tiles": [_t("sales.accessories", available=day_ok, value=120.0, delta_pct=-10.0,
                                              invoices=3, compare={"value": 133.333, "label": "24 Sep"})]}]
    return {
        "mtd": _ov("mtd", {"start": "2026-09-01", "end": "2026-09-27", "label": "1–27 Sep", "business_days": 19},
                   {"start": "2026-08-02", "end": "2026-08-27", "label": "2–27 Aug", "business_days": 19,
                    "basis": "the same 19 business days last month"}, mtd_mods),
        "today": _ov("today", {"start": "2026-09-27", "end": "2026-09-27", "label": "27 Sep", "business_days": 1},
                     {"start": "2026-09-24", "end": "2026-09-24", "label": "24 Sep", "business_days": 1,
                      "basis": "the previous business day"}, day_mods),
    }


@test("R7e command_basis: the Command Centre's own tiles (mtd + latest day), a missing module or tile is None, a failure is None")
def _():
    from app import metrics, reports
    calls: list = []
    ovs = _cc_overviews()

    def fake(period="mtd", reader=None, use_cache=True):
        calls.append(period)
        return ovs[period]

    with _Patched((metrics, "overview", fake)):
        out = reports.command_basis()
    assert sorted(calls) == ["mtd", "today"], "the two cached overviews, shared with the Command Centre"
    assert out["sales_mtd"]["value"] == 930.0 and out["sales_mtd"]["delta_pct"] == 5.2 and out["sales_mtd"]["invoices"] == 41
    assert out["sales_day"]["value"] == 120.0 and out["sales_day"]["compare"] == {"value": 133.333, "label": "24 Sep"}
    assert [c["label"] for c in out["channels"]["chips"]] == ["B2B", "B2C"] and out["channels"]["chips"][0]["share_pct"] == 75.3
    assert out["ar_total"]["value"] == 10050.0 and out["ar_total"]["source"] == "focus_total"
    assert out["ar_over90"]["value"] == 12.5
    assert out["compare_basis"] == "the same 19 business days last month"
    assert out["focus"]["label"] == "1–27 Sep" and out["day"]["end"] == "2026-09-27" and out["day_compare"] == "24 Sep"
    assert out["day_compare_on"] == "2026-09-24", "the compared day's date: the page names its weekday"
    assert out["sales_mtd"]["basis"] == "sales.accessories basis", "the API's own basis line travels with the figure"
    out["sales_mtd"]["value"] = -1
    assert ovs["mtd"]["modules"][1]["tiles"][0]["value"] == 930.0, "a copy: the cached overview is never changed"
    # a missing receivables module, or a tile the build could not compute, leaves ONLY those fields None
    ovs = _cc_overviews(receivables=False, day_ok=False)
    with _Patched((metrics, "overview", fake)):
        out = reports.command_basis()
    assert out["ar_total"] is None and out["ar_over90"] is None and out["sales_day"] is None, "not computed = None"
    assert out["sales_mtd"]["value"] == 930.0 and out["channels"]["value"] == 930.0

    def boom(period="mtd", reader=None, use_cache=True):
        raise RuntimeError("the database blinked")

    with _Patched((metrics, "overview", boom)):
        assert reports.command_basis() is None


@test("R7e _flag_partial: data to 27 Sep = September partial (so far, to 27 Sep); data to 30 Sep = a whole month")
def _():
    from app import reports
    rows = [{"period_month": "2026-08-01", "acc_net_bhd": 5000.0}, {"period_month": "2026-09-01", "acc_net_bhd": 930.0}]
    out = reports._flag_partial(rows, "2026-09-27")
    assert [(r["partial"], r["through"]) for r in out] == [(False, None), (True, "2026-09-27")]
    assert "partial" not in rows[1], "new dicts: the input is not changed"
    assert [r["partial"] for r in reports._flag_partial(rows, "2026-09-30")] == [False, False]
    assert [r["partial"] for r in reports._flag_partial(rows, None)] == [False, False]
    assert reports._flag_partial([{"period_month": "2026-02-01"}], "2026-02-28")[0]["partial"] is False, "Feb 28 ends Feb"
    assert reports._flag_partial([{"period_month": "2028-02"}], "2028-02-28")[0]["partial"] is True, "a leap February runs to 29"
    assert reports._flag_partial([], "2026-09-27") == []


@test("R7e revenue_trend_acc / top_customers_acc_mtd: data_date stripped, the partial month flagged; a failed read is [] / None")
def _():
    from app import reports
    seen: list = []

    def rpc(sql):
        seen.append(sql)
        if "period_month" in sql:
            return [{"period_month": "2026-08-01", "acc_net_bhd": 5000.0, "invoices": 90, "data_date": "2026-09-27"},
                    {"period_month": "2026-09-01", "acc_net_bhd": 930.0, "invoices": 41, "data_date": "2026-09-27"}]
        return [{"customer_name": "Shop A", "net_bhd": 420.0, "invoices": 4}]

    with _Patched((reports, "exec_sql", rpc)):
        trend = reports.revenue_trend_acc()
        top = reports.top_customers_acc_mtd(7)
    assert [(r["period_month"], r["partial"], r["through"]) for r in trend] == [
        ("2026-08-01", False, None), ("2026-09-01", True, "2026-09-27")]
    assert all("data_date" not in r for r in trend)
    assert top == [{"customer_name": "Shop A", "net_bhd": 420.0, "invoices": 4}] and seen[-1].endswith("LIMIT 7")

    def fail(sql):
        raise RuntimeError("permission denied")

    with _Patched((reports, "exec_sql", fail)):
        assert reports.revenue_trend_acc() == []
        # review: None, not [] — an empty list is a month with no named-account sales, a failure is not
        assert reports.top_customers_acc_mtd() is None
    with _Patched((reports, "exec_sql", lambda sql: [])):
        assert reports.top_customers_acc_mtd() == [], "a real empty month stays []"


@test("R7e SQL: Accessories only, giveaways out, ex-VAT net_bhd, Cash Customer out; subqueries, not CTEs")
def _():
    import inspect
    from app import reports
    trend = reports.REVENUE_TREND_ACC_SQL
    for s in ("division = 'Accessories'", "NOT is_giveaway", "SUM(net_bhd)", "interval '11 months'",
              "(SELECT MAX(sale_date) FROM v_sales)", "AS acc_net_bhd", "AS period_month"):
        assert s in trend, s
    top = reports.TOP_CUSTOMERS_ACC_SQL
    for s in ("division = 'Accessories'", "NOT is_giveaway", "NOT is_cash_customer", "SUM(net_bhd)",
              "date_trunc('month', d.mx)", "AS net_bhd", "AS invoices"):
        assert s in top, s
    for sql in (trend, top):
        assert not sql.lstrip().upper().startswith("WITH") and "revenue_bhd" not in sql, "ex-VAT, never the VAT-inclusive"
    dash = inspect.getsource(reports.dashboard)
    assert "command_basis" in dash and "revenue_trend_acc" in dash and "top_customers_acc_mtd" in dash
    assert "agents_status" not in dash
    # the email digests' source is untouched: daily_summary still answers the keys they read
    from app import digest
    ds = inspect.getsource(digest.daily_summary)
    for key in ('"rev_mtd"', '"total_receivables"', '"top_customers"', '"overdue_receivables_bhd"'):
        assert key in ds, key


@test("review: the Dashboard tells a failed top-customers read from an empty month; Latest day names both weekdays")
def _():
    dash = _read("web/src/pages/Dashboard.tsx")
    assert "top_customers_acc?: TopCustomerAcc[] | null" in dash
    assert "const topCustomersFailed = data?.top_customers_acc === null" in dash
    card = dash.split("<Crown size={18} className=\"text-amber-500\" /> Top customers", 1)[1].split("</Card>", 1)[0]
    assert card.index("topCustomersFailed ?") < card.index("topCustomers.length === 0 ?"), "the failure is checked first"
    assert "Top customers could not be read just now." in card and "No named-account Accessories sales this month." in card
    # 'Sat 26 Sep vs Sat 19 Sep': a weekend day reads as one, and the compared day is named with its weekday
    assert "const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']" in dash and "getUTCDay()" in dash
    assert "day_compare_on?: string | null" in dash
    assert "cc?.day_compare_on ? weekdayLabel(cc.day_compare_on) : cc?.day_compare" in dash
    # the pace chip is the month SO FAR (the Command Centre's Month pace chip says 'projected')
    assert "{data.pace.target_pct}% of {bhd(data.pace.target_bhd, 0)} target so far" in dash


@test("R7e web: the Dashboard reads the Command Centre's basis, one freshness element, no agent panel")
def _():
    dash = _read("web/src/pages/Dashboard.tsx")
    for gone in ("AI Agent Team", "DataBanner", "Data as of", "agentLabel(", "relTime(", "data_stale",
                 "Revenue this month (gross)"):
        assert gone not in dash, gone
    for s in ("['freshness']", "apiGet<Freshness>('/freshness')", "revenue_trend_acc", "top_customers_acc",
              "data?.command", "Accessories sales this month · ex-VAT", "Focus invoices this month (Accessories)",
              "Latest day · ${weekdayLabel(cc.day.end)}", "cc?.compare_basis", "arTile?.source === 'focus_total'",
              "k?.total_receivables", "chTile?.chips", "Accessories · ex-VAT · month to date",
              "so far (to ${dayLabel(", "ReferenceLine y={monthTarget}", "r.partial ? "):
        assert s in dash, s
    # the strings other suites pin stay (test_r7b_command, this file's own 'web:' tests)
    for s in ("pace.basis_text", "acc_net_bhd", "business_days_total", "Gross margin · ex-VAT on Focus COGS",
              "Capital frozen in dead stock · at cost", "at selling price",
              "deadUncostedNote(data.health.dead_stock_count, data.health.dead_stock_uncosted)", "from '@/lib/basisText'"):
        assert s in dash, s
    # the chip and the banner share the one query key (so /freshness is read once for both)
    assert "queryKey: ['freshness']" in _read("web/src/components/FreshnessChip.tsx")


@test("visual QA: the month axis reads 7.5k (not 8k), target labels sit above their lines, the daily line shows over every bar, a delta keeps its arrow")
def _():
    dash = _read("web/src/pages/Dashboard.tsx")
    assert "const kTick = (v: number) => (v >= 1000 ? `${Number((v / 1000).toFixed(3))}k` : `${v}`)" in dash
    # every axis of the page (daily, cumulative, monthly) uses it: no rounded 8k / 23k / 1.1k-for-1,050
    assert dash.count("tickFormatter={kTick}") == 3 and "(v / 1000).toFixed(" not in dash.replace("Number((v / 1000).toFixed(3))", "")
    # above the line (insideBottom…) the label never lands on the tallest bar's top; both charts get room at the top
    assert "position: 'insideTopRight'" not in dash and dash.count("position: 'insideBottomRight', offset: 5") == 2
    assert dash.count("margin={{ top: 18, right: 8, left: 8, bottom: 0 }}") == 2
    assert '<ReferenceLine y={dailyTarget} stroke="#d97706" strokeDasharray="5 4" ifOverflow="extendDomain"' in dash
    # the arrow stays with its %, 'BHD' with its amount
    delta = dash.split("function Delta(", 1)[1].split("\n}\n", 1)[0]
    assert '<span className="whitespace-nowrap">' in delta and "vs?: React.ReactNode" in delta
    assert "<Amount>({bhd(salesDay.compare.value, 0)})</Amount>" in dash and "<Amount>{bhd(k.overdue_total_bhd, 0)}</Amount>" in dash


# ═══════════════════════════════════════════════════════════════════════════════
# 8. opt-in: the new view bodies READ ONLY against production (YQ_R7D_LIVE=1)
# ═══════════════════════════════════════════════════════════════════════════════

@test("live (read-only, opt-in): the migration's SELECT bodies keep every live column and match the Python formula")
def _():
    url = os.environ.get("DATABASE_URL")
    if os.environ.get("YQ_R7D_LIVE") != "1" or not url:
        print("    SKIP (set YQ_R7D_LIVE=1 with DATABASE_URL for the read-only production check)")
        return
    import psycopg
    from psycopg.rows import dict_row
    from app import margin_truth as mt
    sql = _read("scripts/r7d_margin_exvat_migration.sql")
    conn = psycopg.connect(url, connect_timeout=15)
    conn.read_only = True
    try:
        cur = conn.cursor(row_factory=dict_row)
        for view, cols in (("v_product_economics", ECON_COLS), ("v_price_tracker", TRACKER_COLS)):
            rows = cur.execute(f"select * from ({_view_body(sql, view)}) x").fetchall()
            desc = [(d.name, d.type_code) for d in cur.description]
            assert [n for n, _t in desc] == cols
            live = cur.execute("select atttypid from pg_attribute where attrelid = %s::regclass and attnum > 0 "
                               "and not attisdropped order by attnum", (view,)).fetchall()
            assert [r["atttypid"] for r in live] == [t for _n, t in desc]
            for r in rows:
                price, cost, got = ((r["price_bhd"], r["cost_bhd"], r["margin_pct"]) if view == "v_product_economics"
                                    else (r["sell_now"], r["cost_now"], r["margin_now_pct"]))
                if cost is not None:
                    assert mt.ex_vat_margin_pct(price, cost) == (float(got) if got is not None else None), r
    finally:
        conn.rollback()
        conn.close()


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
