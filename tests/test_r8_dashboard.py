"""R8 — the Dashboard shows Mobile Accessories only (owner, 7-Oct-2026: "no other numbers except mobile acc").

    python -m tests.test_r8_dashboard

Same lightweight runner as tests/test_r7d_profit.py. Every test runs on in-memory stand-ins: the read-only
RPC (exec_sql) is a function that records each SQL it is sent and answers by its shape. Nothing reaches a
database or the network — the Supabase URL is pinned to ci.invalid before any app module loads (CI sets the
same). All data is synthetic. One opt-in check reads production READ ONLY through the SELECT-only RPC
(YQ_R8_LIVE=1 with the real .env) and SKIPs otherwise.

Covered:
  * the Cash / Credit chips — PAYMENT_SPLIT_ACC_SQL (Accessories only, ex-VAT, giveaways out of the money
    and of the invoice counts, the free units counted apart, the month of the latest loaded sale), the pure
    assembly, a failed read is None;
  * low stock — app.digest's division filter: the Dashboard's alerts are built for Accessories, the email
    digests' own SQL is byte for byte what it was, a made-up division is refused;
  * momentum — both lists read v_stock_health for Accessories only;
  * business health — DSO divides by Accessories sales per day (VAT-inclusive like the receivables,
    giveaways out); the receivables are never narrowed; dead stock reads the Accessories stock;
  * the payload — payment_split_acc and scope are ADDED; every older key still travels (by_payment,
    by_division, kpis.rev_*); kpis.low_stock_count is the Accessories count and the action text follows;
    nothing new is hidden from a login without 'Margins';
  * the page (source checks), the node half (web/scripts/numbers_ui_test.mjs), and CI running both.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import traceback
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Offline by construction: unless the read-only production check is asked for, the Supabase URL is pinned
# before any app module loads (app.config's load_dotenv never overrides it), so a read that a stand-in
# missed could reach nothing real.
LIVE = os.environ.get("YQ_R8_LIVE") == "1"
if LIVE:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
else:
    os.environ["SUPABASE_URL"] = "https://ci.invalid"

TESTS: list = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _raises(fn, exc=Exception, want: str = ""):
    try:
        fn()
    except exc as e:  # noqa: BLE001
        assert want.lower() in str(e).lower(), (want, str(e))
        return e
    raise AssertionError(f"expected {exc.__name__} containing {want!r}")


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


def _safe_select(sql: str, relations: set[str]) -> None:
    """One read-only statement over the granted views: no CTE (the RPC's relation check reads FROM / JOIN
    names), no second statement, no write word, no column aliased `t` (the RPC's own row alias)."""
    assert sql.lstrip().upper().startswith("SELECT"), "a subquery, not a CTE"
    assert ";" not in sql and not re.search(r"\b(insert|update|delete|drop|alter|grant|create)\b", sql, re.I)
    rels = set(re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)\b(?!\.)", sql, re.I))
    assert rels == relations, rels ^ relations
    assert not re.search(r"\bAS\s+t\b", sql, re.I)
    # the data date, never the server clock
    assert not re.search(r"current_date|now\(\)|current_timestamp", sql, re.I)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the Cash / Credit chips: Accessories only, ex-VAT, giveaways out
# ═══════════════════════════════════════════════════════════════════════════════

# a month as the SQL returns it (made-up figures): credit first only to prove the ordering
SPLIT_ROWS = [{"sale_type": "credit", "invoices": 14, "net_bhd": 410.25, "giveaway_qty": 0},
              {"sale_type": "cash", "invoices": 9, "net_bhd": 120.5, "giveaway_qty": 0}]
SPLIT = {"division": "Accessories", "basis": "net_ex_vat", "giveaway_qty": 0.0,
         "rows": [{"sale_type": "cash", "net_bhd": 120.5, "invoices": 9},
                  {"sale_type": "credit", "net_bhd": 410.25, "invoices": 14}]}


@test("chips SQL: Accessories only, ex-VAT net_bhd, giveaways out of money and invoices, free units apart, the data's month")
def _():
    from app import reports
    sql = reports.PAYMENT_SPLIT_ACC_SQL
    for s in ("division = 'Accessories'",
              "COUNT(DISTINCT invoice_no) FILTER (WHERE NOT is_giveaway) AS invoices",
              "SUM(net_bhd) FILTER (WHERE NOT is_giveaway)", "AS net_bhd",
              "SUM(quantity) FILTER (WHERE is_giveaway)", "AS giveaway_qty",
              "(SELECT MAX(sale_date) AS mx FROM v_sales) d", "sale_date >= date_trunc('month', d.mx)::date",
              "GROUP BY sale_type"):
        assert s in sql, s
    assert "revenue_bhd" not in sql, "ex-VAT, never the VAT-inclusive figure"
    # the division filter is on the WHERE, so no other division's row can come back at all
    where = sql.split(" WHERE ", 1)[1].split(" GROUP BY ", 1)[0]
    assert where.endswith("AND division = 'Accessories'"), where
    _safe_select(sql, {"v_sales"})
    # the every-division split is left as it was: other readers use it, _pace reads its Accessories row
    import inspect
    old = inspect.getsource(reports.sales_split_mtd)
    assert "GROUP BY sale_type" in old and "GROUP BY division" in old and "division = 'Accessories'" not in old


@test("chips assembly: cash first, money to the fils, the chips add up to the Accessories month; the input is not changed")
def _():
    from app import reports
    rows = [dict(r) for r in SPLIT_ROWS]
    out = reports.payment_split_acc(rows)
    assert out == SPLIT, out
    assert rows == SPLIT_ROWS, "new dicts: the SQL rows are not changed"
    assert sum((Decimal(str(r["net_bhd"])) for r in out["rows"]), Decimal(0)) == Decimal("530.75")
    assert sum(r["invoices"] for r in out["rows"]) == 23
    # the RPC's text numerics and a half-fils: half-up to the fils, exactly
    out = reports.payment_split_acc([{"sale_type": "Cash", "invoices": "3", "net_bhd": "10.0005", "giveaway_qty": "0"}])
    assert out["rows"] == [{"sale_type": "cash", "net_bhd": 10.001, "invoices": 3}]
    # a sale type the page does not know still gets its chip, after cash and credit
    out = reports.payment_split_acc(SPLIT_ROWS + [{"sale_type": "benefit", "invoices": 1, "net_bhd": 5, "giveaway_qty": 0}])
    assert [r["sale_type"] for r in out["rows"]] == ["cash", "credit", "benefit"]


@test("chips assembly: free units are counted apart and never make a chip of their own; an empty month is no chips")
def _():
    from app import reports
    # cash: paid lines plus 12 free units; credit: its only Accessories lines this month were 4 free units
    out = reports.payment_split_acc([{"sale_type": "cash", "invoices": 2, "net_bhd": 30.5, "giveaway_qty": 12},
                                     {"sale_type": "credit", "invoices": 0, "net_bhd": 0, "giveaway_qty": 4.0}])
    assert out["rows"] == [{"sale_type": "cash", "net_bhd": 30.5, "invoices": 2}], "no BHD 0 credit chip"
    assert out["giveaway_qty"] == 16 and isinstance(out["giveaway_qty"], float)
    for empty in ([], None):
        out = reports.payment_split_acc(empty)
        assert out == {"division": "Accessories", "basis": "net_ex_vat", "rows": [], "giveaway_qty": 0.0}
    # junk never raises: the row's figures read as zero and it makes no chip
    out = reports.payment_split_acc([{"sale_type": None, "invoices": None, "net_bhd": "n/a", "giveaway_qty": None}])
    assert out["rows"] == [] and out["giveaway_qty"] == 0.0


@test("chips read: the one SQL through the read-only RPC; a failed read is None (no chips), never the every-division split")
def _():
    from app import reports
    seen: list = []

    def rpc(sql):
        seen.append(sql)
        return [dict(r) for r in SPLIT_ROWS]

    with _Patched((reports, "exec_sql", rpc)):
        assert reports.payment_split_acc_mtd() == SPLIT
    assert seen == [reports.PAYMENT_SPLIT_ACC_SQL]

    def fail(sql):
        raise RuntimeError("statement timeout")

    with _Patched((reports, "exec_sql", fail)):
        assert reports.payment_split_acc_mtd() is None
    with _Patched((reports, "exec_sql", lambda sql: None)):
        assert reports.payment_split_acc_mtd()["rows"] == [], "a month with no Accessories sale yet"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. low stock: the Dashboard counts Accessories, the email digests count what they always did
# ═══════════════════════════════════════════════════════════════════════════════

# the digests' SQL exactly as it was before R8 (app/digest.py at e26b35c)
OLD_ITEMS_SQL = ("SELECT item_name, current_stock, sold_90d, days_cover, suggested_reorder_qty, status "
                 "FROM v_stock_health WHERE status IN ('urgent_out_of_stock','low_stock') "
                 "ORDER BY days_cover ASC NULLS FIRST LIMIT 60")
OLD_TOTAL_SQL = ("SELECT COUNT(*) AS n FROM v_stock_health "
                 "WHERE status IN ('urgent_out_of_stock','low_stock') LIMIT 1")


@test("low stock: division='Accessories' narrows the count and the list; with no division the SQL is what it always was")
def _():
    from app import digest
    seen: list = []

    def rpc(sql):
        seen.append(sql)
        if "COUNT(*) AS n" in sql:
            return [{"n": 12 if "division = 'Accessories'" in sql else 15}]
        return [{"item_name": "Item A", "current_stock": 0, "sold_90d": 9, "days_cover": 0,
                 "suggested_reorder_qty": 3, "status": "urgent_out_of_stock"}]

    with _Patched((digest, "exec_sql", rpc)):
        assert digest.low_stock_total("Accessories") == 12 and digest.low_stock_total() == 15
        assert digest.low_stock_items("Accessories")[0]["item_name"] == "Item A" and digest.low_stock_items()
    acc_total, old_total, acc_items, old_items = seen
    assert old_total == OLD_TOTAL_SQL and old_items == OLD_ITEMS_SQL, "the digests' reads, byte for byte"
    assert acc_total == OLD_TOTAL_SQL.replace(" LIMIT 1", " AND division = 'Accessories' LIMIT 1")
    assert acc_items == OLD_ITEMS_SQL.replace(" ORDER BY", " AND division = 'Accessories' ORDER BY")
    for sql in (acc_total, acc_items):
        _safe_select(sql, {"v_stock_health"})


@test("low stock: a division that is not a plain word is refused before any SQL is sent")
def _():
    from app import digest
    sent: list = []
    with _Patched((digest, "exec_sql", lambda sql: sent.append(sql) or [])):
        for bad in ("Accessories' OR '1'='1", "SIM; DROP TABLE orders", "", "Mobile Accessories", 5, ["Accessories"]):
            _raises(lambda b=bad: digest.low_stock_total(b), ValueError, "Unknown division")
            _raises(lambda b=bad: digest.low_stock_items(b), ValueError, "Unknown division")
    assert sent == [], "nothing reached the RPC"
    assert digest._in_division(None) == "" and digest._in_division("Accessories") == " AND division = 'Accessories'"


@test("alerts: all_alerts(division) narrows low stock only; all_alerts() — the endpoint and the email digest — is every division")
def _():
    from app import digest
    asked: list = []

    def items(division=None):
        asked.append(("items", division))
        return [{"item_name": "Item A"}] if division else [{"item_name": "Item A"}, {"item_name": "Starter Pack"}]

    def total(division=None):
        asked.append(("total", division))
        return 12 if division else 15

    overdue = [{"account": "Shop A", "overdue_bhd": 12.5}]
    with _Patched((digest, "low_stock_items", items), (digest, "low_stock_total", total),
                  (digest, "overdue_receivables", lambda: overdue), (digest, "negative_margins", lambda: [])):
        acc = digest.all_alerts("Accessories")
        every = digest.all_alerts()
    assert asked == [("items", "Accessories"), ("total", "Accessories"), ("items", None), ("total", None)]
    assert acc["low_stock_count"] == 12 and [r["item_name"] for r in acc["low_stock"]] == ["Item A"]
    assert every["low_stock_count"] == 15 and len(every["low_stock"]) == 2
    # receivables are per customer: the same either way
    assert acc["overdue_receivables"] == every["overdue_receivables"] == overdue and acc["overdue_total_bhd"] == 12.5
    assert set(acc) == set(every) == {"low_stock", "low_stock_count", "overdue_receivables", "overdue_count",
                                      "overdue_total_bhd", "negative_margins", "negative_margin_count", "has_alerts"}
    # the endpoint and the email sender still call it bare
    main = _read("app/main.py").split('@app.get("/digest/alerts")', 1)[1].split("@app.get(", 1)[0]
    assert "return all_alerts()" in main
    assert "data = all_alerts()" in _read("scripts/send_digest.py")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. momentum: Accessories only
# ═══════════════════════════════════════════════════════════════════════════════

@test("movers: both lists read v_stock_health for Accessories only (no starter pack can ride in)")
def _():
    from app import reports
    seen: list = []

    def rpc(sql):
        seen.append(sql)
        return [{"item_name": "Item A", "sold_30d": 9, "sold_90d": 9, "status": "healthy", "momentum": 3.0}]

    with _Patched((reports, "exec_sql", rpc)):
        out = reports.movers(5)
    rising, falling = seen
    for sql in seen:
        assert " FROM v_stock_health WHERE division = 'Accessories' AND sold_90d > 0" in sql, sql
        assert "AS momentum" in sql and "sold_30d / NULLIF(sold_90d / 3.0, 0)" in sql
        _safe_select(sql, {"v_stock_health"})
    assert "AND sold_30d > 0 ORDER BY momentum DESC LIMIT 5" in rising and rising.count("division = 'Accessories'") == 1
    assert falling.endswith("AND sold_90d > 0 ORDER BY momentum ASC LIMIT 5")
    assert set(out) == {"rising", "falling"} and out["rising"][0]["item_name"] == "Item A"
    with _Patched((reports, "exec_sql", lambda sql: None)):
        assert reports.movers(3) == {"rising": [], "falling": []}


# ═══════════════════════════════════════════════════════════════════════════════
# 4. business health: DSO on Accessories sales per day, dead stock on the Accessories stock
# ═══════════════════════════════════════════════════════════════════════════════

STOCK_ROWS = [
    {"item_name": "Item A", "division": "Accessories", "status": "healthy", "current_stock": 10, "sell_value_bhd": 30,
     "sold_90d": 9, "days_cover": 100, "cost_bhd": 1.5, "cost_source": "focus_avg"},
    {"item_name": "Item Dead", "division": "Accessories", "status": "dead_stock", "current_stock": 5, "sell_value_bhd": 40,
     "sold_90d": 0, "days_cover": None, "cost_bhd": 4.25, "cost_source": "mrn"},
    # STOCK_SQL also returns Accessories items that are not on hand: the summary leaves them out
    {"item_name": "Item Gone", "division": "Accessories", "status": "urgent_out_of_stock", "current_stock": 0,
     "sell_value_bhd": 0, "sold_90d": 4, "days_cover": 0, "cost_bhd": 2.0, "cost_source": "focus_avg"},
]


def _health_rpc(per_day=50.0, total=1000.0, overdue=400.0, fail=()):
    """An exec_sql stand-in for business_health(): records every SQL, answers by shape."""
    seen: list = []

    def q(sql):
        seen.append(sql)
        for key in fail:
            if key in sql:
                raise RuntimeError(f"{key} unavailable")
        if "FROM v_receivables" in sql:
            return [{"total": total, "overdue": overdue}]
        if "/90.0 AS d" in sql:
            return [{"d": per_day}]
        if "FROM v_stock_health h" in sql:
            return [dict(r) for r in STOCK_ROWS]
        return []            # the margin reads: not this suite's subject

    return q, seen


@test("DSO SQL: Accessories only, giveaways out, VAT-inclusive like the receivables, the 90 days to the latest sale")
def _():
    from app import reports
    sql = reports.DSO_SALES_PER_DAY_SQL
    for s in ("SUM(revenue_bhd)", "/90.0 AS d", "division = 'Accessories' AND NOT is_giveaway",
              "sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90"):
        assert s in sql, s
    assert "net_bhd" not in sql, "receivables are VAT-inclusive: the sales they are divided by stay VAT-inclusive"
    _safe_select(sql, {"v_sales"})
    b = reports.DSO_BASIS
    assert "every customer balance in Focus" in b and "Accessories sales per day" in b and "VAT-inclusive" in b


@test("business health: DSO = every customer balance ÷ Accessories sales per day; the receivables are never narrowed")
def _():
    from app import reports
    q, seen = _health_rpc()
    with _Patched((reports, "exec_sql", q)):
        h = reports.business_health()
    assert [s for s in seen if "/90.0 AS d" in s] == [reports.DSO_SALES_PER_DAY_SQL]
    assert h["dso_days"] == 20.0 and h["ar_overdue_pct"] == 40.0
    assert (h["dso_receivables_bhd"], h["dso_sales_per_day_bhd"], h["dso_basis"]) == (1000.0, 50.0, reports.DSO_BASIS)
    ar_sql = [s for s in seen if "FROM v_receivables" in s]
    assert len(ar_sql) == 1 and "division" not in ar_sql[0] and "v_sales" not in ar_sql[0], \
        "no made-up 'accessories receivables': the balances are per customer"
    # the inputs leave to the fils; the days are computed on the unrounded figures
    q, _ = _health_rpc(per_day=123.45678, total=7407.41, overdue=2500.0)
    with _Patched((reports, "exec_sql", q)):
        h = reports.business_health()
    assert round(h["dso_days"], 3) == 60.0 and h["dso_sales_per_day_bhd"] == 123.457 and h["dso_receivables_bhd"] == 7407.41
    # Batelco SIM cash in the denominator is what made collection look fast: the same balances on twice
    # the daily sales read half the days
    q, _ = _health_rpc(per_day=100.0)
    with _Patched((reports, "exec_sql", q)):
        assert reports.business_health()["dso_days"] == 10.0
    # no Accessories sale in the window: 0 days, never a division by zero
    q, _ = _health_rpc(per_day=0)
    with _Patched((reports, "exec_sql", q)):
        assert reports.business_health()["dso_days"] == 0.0


@test("business health: dead stock reads the Accessories stock (the Command Centre's STOCK_SQL), at cost")
def _():
    from app import metrics, reports
    q, seen = _health_rpc()
    with _Patched((reports, "exec_sql", q)):
        h = reports.business_health()
    stock_sql = [s for s in seen if "FROM v_stock_health h" in s]
    assert stock_sql == [metrics.STOCK_SQL] and stock_sql[0].endswith("WHERE h.division = 'Accessories'")
    assert metrics.STOCK_HELD_SQL not in seen, "the any-division read stays with the Inventory page"
    assert (h["dead_stock_bhd"], h["dead_stock_count"], h["dead_stock_sell_bhd"]) == (21.25, 1, 40.0)
    assert h["stock_basis"] == "cost" and h["dead_stock_uncosted"] == 0
    # the Inventory page is a different reader and keeps every division held
    import inspect
    assert "metrics.STOCK_HELD_SQL" in inspect.getsource(reports.inventory)
    assert "STOCK_HELD_SQL" not in inspect.getsource(reports.business_health)
    # the stock read failing still costs only its own tile
    q, _ = _health_rpc(fail=("FROM v_stock_health h",))
    with _Patched((reports, "exec_sql", q)):
        h = reports.business_health()
    assert h["stock_basis"] is None and h["dead_stock_bhd"] == 0.0 and h["dso_days"] == 20.0


# ═══════════════════════════════════════════════════════════════════════════════
# 5. the payload: two keys ADDED, every older key still travels
# ═══════════════════════════════════════════════════════════════════════════════

# the payload's keys as R7e left them (tests/test_r7d_profit.py pins the same list)
R7E_DASH_KEYS = {"data_as_of", "data_stale", "data_days_behind", "actions", "kpis", "health", "movers", "top_customers",
                 "revenue_trend", "by_channel", "by_salesman", "by_salesman_scope", "alerts", "daily_mtd", "by_payment",
                 "by_division", "pace", "attainment", "command", "revenue_trend_acc", "top_customers_acc"}
KPI_KEYS = {"rev_today", "net_today", "orders_today", "rev_yesterday", "orders_yesterday", "rev_mtd", "net_mtd",
            "orders_mtd", "rev_prev_month", "rev_prev_month_mtd", "prev_month_through", "total_receivables",
            "low_stock_count", "overdue_count", "overdue_total_bhd", "current_receivables_bhd"}
# the every-division month as sales_split_mtd returns it (made-up, VAT-inclusive): SIM cash rides in both
# lists — 3,300.000 of the 3,432.550 "cash" is starter packs
BY_PAYMENT = [{"sale_type": "cash", "orders": 15, "revenue_bhd": 3432.55}, {"sale_type": "credit", "orders": 14, "revenue_bhd": 451.28}]
BY_DIVISION = [{"division": "SIM", "orders": 6, "revenue_bhd": 3300.0, "net_ex_vat_bhd": 3000.0, "giveaway_qty": 0},
               {"division": "Accessories", "orders": 23, "revenue_bhd": 583.83, "net_ex_vat_bhd": 530.75, "giveaway_qty": 0}]


def _dash_base() -> dict:
    s = {"rev_today": 110.0, "net_today": 100.0, "orders_today": 3, "rev_yesterday": 55.0, "orders_yesterday": 2,
         "rev_mtd": 3883.83, "net_mtd": 3530.75, "orders_mtd": 29, "rev_prev_month": 2000.0, "rev_prev_month_mtd": 1500.0,
         "prev_month_through": "2026-09-06", "total_receivables": 9000.0, "overdue_accounts": 20,
         "overdue_receivables_bhd": 3600.0, "current_receivables_bhd": 5400.0, "top_customers": [], "data_date": "2026-10-06"}
    # `a` is what dashboard() gets from all_alerts('Accessories'): 12 of the 15 are accessories
    return {"s": s, "a": {"low_stock_count": 12, "negative_margin_count": 0, "low_stock": [{"item_name": "Item A"}]},
            "health": {}, "movers": {"rising": [], "falling": []}, "trend": [], "channel": [],
            "fresh": {"stale": False, "days_behind": 1}, "daily_mtd": [], "attainment": {"rows": [], "error": None},
            "split": {"by_payment": BY_PAYMENT, "by_division": BY_DIVISION}}


@test("payload: payment_split_acc and scope are ADDED; by_payment, by_division and kpis.rev_* still travel untouched")
def _():
    from app import reports
    from app import settings as app_settings
    with _Patched((app_settings, "setting", lambda key: 10000.0 if key == "monthly_sales_target_bhd" else None)):
        out = reports._assemble_dashboard({**_dash_base(), "split_acc": SPLIT})
        old = reports._assemble_dashboard(_dash_base())
    assert set(out) == R7E_DASH_KEYS | {"payment_split_acc", "scope"}, set(out) ^ R7E_DASH_KEYS
    assert out["payment_split_acc"] == SPLIT
    assert out["scope"] == {"division": "Accessories", "text": reports.DASHBOARD_SCOPE["text"]}
    # the older keys, exactly as the every-division reads returned them
    assert out["by_payment"] == BY_PAYMENT and out["by_division"] == BY_DIVISION
    k = out["kpis"]
    assert set(k) == KPI_KEYS, set(k) ^ KPI_KEYS
    assert (k["rev_today"], k["rev_yesterday"], k["rev_mtd"], k["net_mtd"], k["rev_prev_month"], k["rev_prev_month_mtd"]) \
        == (110.0, 55.0, 3883.83, 3530.75, 2000.0, 1500.0)
    assert k["total_receivables"] == 9000.0, "receivables stay every customer balance"
    # _pace still reads the Accessories row of by_division: the chips add up to the very same month
    assert out["pace"]["mtd_bhd"] == 530.75 == float(sum(Decimal(str(r["net_bhd"])) for r in SPLIT["rows"]))
    # an older caller's dict (no split_acc: test_r7b_command, test_r3_reps, test_r7d_profit) still assembles
    assert old["payment_split_acc"] is None and old["scope"]["division"] == "Accessories"
    assert set(old) == set(out)


@test("payload: kpis.low_stock_count is the Accessories count the alerts were built on; the Reorder action follows")
def _():
    from app import reports
    from app import settings as app_settings
    with _Patched((app_settings, "setting", lambda key: None)):
        out = reports._assemble_dashboard(_dash_base())
    assert out["kpis"]["low_stock_count"] == out["alerts"]["low_stock_count"] == 12
    assert {"action": "Reorder 12 low / out-of-stock items", "to": "/orders", "bhd": 0, "urgency": 3} in out["actions"]
    assert [a["action"] for a in out["actions"] if a["to"] == "/orders"] == ["Reorder 12 low / out-of-stock items"]


@test("scope: division 'Accessories' and one plain sentence that names the receivables exception; the payload gets a copy")
def _():
    from app import reports
    from app import settings as app_settings
    sc = reports.DASHBOARD_SCOPE
    assert reports.DASHBOARD_DIVISION == "Accessories" and set(sc) == {"division", "text"} and sc["division"] == "Accessories"
    t = sc["text"]
    assert t.startswith("Mobile Accessories only") and "Batelco SIM" in t and "left out" in t
    assert "receivables are the one exception" in t and "one balance per customer" in t
    assert t.endswith(".") and t.count(". ") == 0, "one sentence"
    with _Patched((app_settings, "setting", lambda key: None)):
        out = reports._assemble_dashboard(_dash_base())
    out["scope"]["division"] = "changed"
    assert reports.DASHBOARD_SCOPE["division"] == "Accessories", "a cached payload can never change the constant"


def _recorder(calls: dict, name: str, ret):
    def f(*a, **kw):
        calls[name] = (a, kw)
        return ret
    return f


@test("dashboard(): the alerts are built for Accessories, the chips are read, every section still assembles")
def _():
    from app import reports
    from app import settings as app_settings
    base, calls = _dash_base(), {}
    patches = [(reports, fn, _recorder(calls, fn, ret)) for fn, ret in (
        ("daily_summary", base["s"]), ("all_alerts", base["a"]), ("business_health", {"dso_days": 60.0}),
        ("movers", base["movers"]), ("revenue_trend", []), ("sales_by_channel", []), ("data_freshness", base["fresh"]),
        ("daily_sales_mtd", []), ("sales_split_mtd", base["split"]), ("salesman_attainment_result", base["attainment"]),
        ("command_basis", None), ("revenue_trend_acc", []), ("top_customers_acc_mtd", []),
        ("payment_split_acc_mtd", SPLIT))]
    saved = dict(reports._dash_cache)
    try:
        with _Patched(*patches, (app_settings, "setting", lambda key: None)):
            out = reports.dashboard(force=True)
    finally:                                   # this process's cache is left as it was found
        reports._dash_cache.clear()
        reports._dash_cache.update(saved)
    assert calls["all_alerts"] == (("Accessories",), {}), "the page's low stock is asked for its division"
    assert calls["payment_split_acc_mtd"] == ((), {}) and calls["movers"] == ((5,), {})
    assert calls["business_health"] == ((), {}) and calls["sales_split_mtd"] == ((), {})
    assert out["payment_split_acc"] == SPLIT and out["scope"]["division"] == "Accessories"
    assert out["kpis"]["low_stock_count"] == 12 and out["health"] == {"dso_days": 60.0}
    assert out["by_payment"] == BY_PAYMENT and out["by_division"] == BY_DIVISION


@test("viewer cut: a login without 'Margins' still gets the scope, the chips, the DSO basis and the low-stock count")
def _():
    from app import reports
    cached = {"kpis": {"low_stock_count": 12}, "payment_split_acc": SPLIT, "scope": dict(reports.DASHBOARD_SCOPE),
              "health": {"gp_bhd": 40.0, "gp_pct": 37.6, "dso_days": 60.0, "dso_basis": reports.DSO_BASIS,
                         "dso_receivables_bhd": 9000.0, "dso_sales_per_day_bhd": 150.0, "dead_stock_bhd": 5.0,
                         "dead_stock_count": 3, "dead_stock_sell_bhd": 9.0, "stock_basis": "cost"},
              "alerts": {"low_stock_count": 12, "negative_margins": [], "negative_margin_count": 0}, "actions": []}
    shown = reports.dashboard_for_viewer(cached, sees_cost=False)
    assert shown["payment_split_acc"] == SPLIT and shown["scope"] == reports.DASHBOARD_SCOPE
    assert shown["kpis"]["low_stock_count"] == 12 and shown["alerts"]["low_stock_count"] == 12
    h = shown["health"]
    assert (h["dso_days"], h["dso_basis"], h["dso_receivables_bhd"], h["dso_sales_per_day_bhd"]) == \
        (60.0, reports.DSO_BASIS, 9000.0, 150.0), "sales and receivables are not cost"
    assert h["gp_pct"] is None and h["dead_stock_bhd"] is None and h["cost_hidden"] is True
    for key in ("dso_basis", "dso_receivables_bhd", "dso_sales_per_day_bhd"):
        assert key not in reports.DASHBOARD_HEALTH_COST_KEYS
    assert reports.dashboard_for_viewer(cached, sees_cost=True) is cached


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the page (source checks), the node half, CI
# ═══════════════════════════════════════════════════════════════════════════════

@test("web: the Dashboard reads payment_split_acc and scope, never the every-division split; the pill and labels follow the API")
def _():
    dash = _read("web/src/pages/Dashboard.tsx")
    for gone in ("by_payment", "by_division", "PaymentRow", "DivisionRow", "gross_bhd", "gross, VAT-incl"):
        assert gone not in dash, gone
    assert not re.search(r"every[\s-]+division", dash, re.I)
    for s in ("payment_split_acc?: PaymentSplitAcc | null", "scope?: Scope | null",
              "const split = data?.payment_split_acc ?? null",
              "const accOnly = data?.scope?.division === 'Accessories'",
              "Month to date · Accessories · ex-VAT:", "{bhd(p.net_bhd, 0)}",
              "${num(p.invoices)} invoice${p.invoices === 1 ? '' : 's'}",
              "{split.giveaway_qty > 0 && (", "free unit{split.giveaway_qty === 1 ? '' : 's'}",
              "title={data?.scope?.text}", "Mobile Accessories only",
              "label={accOnly ? 'Low-stock items · Accessories' : 'Low-stock items'}",
              "{accOnly ? 'Accessories items' : 'items'} under 30 days cover",
              "'every customer balance in Focus, not split by product'", "basis={arBasis}",
              "data.health.dso_basis", "Number(r.acc_net_bhd ?? r.acc_bhd ?? 0)"):
        assert s in dash, s
    # the pill is shown only when the API states the scope (an older API: no claim), inside the page header
    header = dash.split("<PageHeader title={greeting}", 1)[1].split("/>\n\n", 1)[0]
    assert "actions={accOnly ? (" in header and "Mobile Accessories only" in header and ") : undefined}" in header
    # the strings the other suites pin on this page stay
    for s in ("pace.basis_text", "acc_net_bhd", "business_days_total", "Capital frozen in dead stock · at cost",
              "Accessories sales this month · ex-VAT", "top_customers_acc", "revenue_trend_acc"):
        assert s in dash, s


@test("node: the Dashboard can never render the every-division chips again (web/scripts/numbers_ui_test.mjs)")
def _():
    node = shutil.which("node")
    script = ROOT / "web" / "scripts" / "numbers_ui_test.mjs"
    src = script.read_text(encoding="utf-8")
    for s in ("pages/Dashboard.tsx", "by_payment", "by_division", "every", "Month to date · Accessories · ex-VAT"):
        assert s in src, s
    if not node or not (ROOT / "web" / "node_modules" / "typescript").exists():
        print("        SKIP: node or web/node_modules not available (the web job runs it after npm ci)")
        return
    r = subprocess.run([node, str(script)], cwd=str(ROOT / "web"), capture_output=True, text=True, encoding="utf-8",
                       timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "node number-wording tests failed:\n" + out[-2000:]
    assert re.search(r"\b\d+ passed, 0 failed", out), out[-400:]


@test("ci: this suite runs in the api job and the node half in the web job")
def _():
    ci = _read(".github/workflows/ci.yml")
    api, web = ci.split("\n  web:\n", 1)
    assert "python -m tests.test_r8_dashboard" in api
    assert "run: node scripts/numbers_ui_test.mjs" in web
    assert web.index("run: npm ci") < web.index("run: node scripts/numbers_ui_test.mjs"), "the node test needs node_modules"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. opt-in: the new reads READ ONLY against production (YQ_R8_LIVE=1)
# ═══════════════════════════════════════════════════════════════════════════════

@test("live (read-only, opt-in): the chips add up to the Accessories month, low stock ≤ every division, every mover is an accessory")
def _():
    if not LIVE or "ci.invalid" in os.environ.get("SUPABASE_URL", "ci.invalid"):
        print("    SKIP (set YQ_R8_LIVE=1 with the real .env for the read-only production check)")
        return
    from app import digest, reports
    from app.db_read import exec_sql
    split = reports.payment_split_acc_mtd()
    assert split is not None, "the chips SQL did not run"
    month = sum((Decimal(str(d.get("acc_net_bhd") or 0)) for d in reports.daily_sales_mtd()), Decimal(0))
    chips = sum((Decimal(str(r["net_bhd"])) for r in split["rows"]), Decimal(0))
    assert chips == month, (chips, month)
    acc, every = digest.low_stock_total("Accessories"), digest.low_stock_total()
    assert 0 <= acc <= every, (acc, every)
    mv = reports.movers(8)
    names = sorted({str(r["item_name"]) for r in mv["rising"] + mv["falling"]})
    other = exec_sql("SELECT item_name, division FROM v_stock_health WHERE division <> 'Accessories'") or []
    assert not {r["item_name"] for r in other} & set(names), "a non-accessory in the momentum lists"
    print(f"    chips {chips} = month {month}; low stock {acc} of {every}; {len(names)} movers, all Accessories")


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
