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
  * low stock — app.digest's division filter: the Dashboard's alerts are built for Accessories, and of it
    the items it SOLD in the 90 days (a paid line: an item only ever given away is not in the count); the
    email digests' own SQL is byte for byte what it was, a made-up division is refused; the Inventory page
    the card opens carries the Dashboard's own count beside its every-division stats;
  * momentum — both lists read v_stock_health for Accessories only; ties in momentum go by units, then by
    name (the same five every time); a faller has stock to go dead, never an item of the reorder count;
  * business health — DSO divides by Accessories sales per day (VAT-inclusive like the receivables,
    giveaways out); the receivables are never narrowed, and they are the Receivables card's own figure
    (Focus's Grand Total when stored): one receivables total on the page; dead stock reads the Accessories stock;
    the margin, the below-cost count and list leave out report items filed outside Accessories, by code and
    not only by what the report happens to hold (every other reader still reads the whole report);
  * an open tab — the page asks again while the header chip's Focus date is ahead of its own, and on coming
    back to the tab; no standing poll;
  * the payload — payment_split_acc and scope are ADDED; every older key still travels (by_payment,
    by_division, kpis.rev_*); kpis.low_stock_count is the Accessories count and the action text follows;
    nothing new is hidden from a login without 'Margins';
  * the page (source checks, the Momentum card and the alerts strip at phone width among them), the node
    half (web/scripts/numbers_ui_test.mjs), and CI running both.
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


# what a division adds to a v_stock_health read: the division, and of it only the items it SOLD (a paid line in
# v_sales, giveaways out) in the 90 days the cover is measured on
ACC_ITEMS = (" AND division = 'Accessories' AND item_name IN (SELECT item_name FROM v_sales "
             "WHERE division = 'Accessories' AND NOT is_giveaway "
             "AND sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90)")


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
    assert acc_total == OLD_TOTAL_SQL.replace(" LIMIT 1", ACC_ITEMS + " LIMIT 1")
    assert acc_items == OLD_ITEMS_SQL.replace(" ORDER BY", ACC_ITEMS + " ORDER BY")
    for sql in (acc_total, acc_items):
        _safe_select(sql, {"v_stock_health", "v_sales"})


@test("low stock: an item the division only ever gave away is not in its count — a paid sale in the 90 days, the page's own basis")
def _():
    from app import digest
    f = digest._in_division("Accessories")
    assert f == ACC_ITEMS
    # v_stock_health counts every unit that left as sold, free ones too, and files an unplaced item under
    # Accessories: the count is narrowed to the names v_sales has a PAID Accessories line for
    paid = f.split("item_name IN (", 1)[1]
    assert paid.startswith("SELECT item_name FROM v_sales WHERE division = 'Accessories' AND NOT is_giveaway ")
    assert paid.endswith("AND sale_date > (SELECT MAX(sale_date) FROM v_sales) - 90)"), "the data date, the cover's own 90 days"
    assert f.count("division = 'Accessories'") == 2 and "revenue_bhd" not in f and "net_bhd" not in f
    # the same filter for any division asked for; none at all for the email digests
    assert digest._in_division("SIM") == ACC_ITEMS.replace("'Accessories'", "'SIM'") and digest._in_division(None) == ""


@test("low stock: a division that is not a plain word is refused before any SQL is sent")
def _():
    from app import digest
    sent: list = []
    with _Patched((digest, "exec_sql", lambda sql: sent.append(sql) or [])):
        for bad in ("Accessories' OR '1'='1", "SIM; DROP TABLE orders", "", "Mobile Accessories", 5, ["Accessories"]):
            _raises(lambda b=bad: digest.low_stock_total(b), ValueError, "Unknown division")
            _raises(lambda b=bad: digest.low_stock_items(b), ValueError, "Unknown division")
    assert sent == [], "nothing reached the RPC"
    assert digest._in_division(None) == "" and digest._in_division("Accessories") == ACC_ITEMS


@test("alerts: all_alerts(division) narrows low stock and the below-cost list; all_alerts() — the endpoint and the email digest — is every division")
def _():
    from app import digest
    asked: list = []

    def items(division=None):
        asked.append(("items", division))
        return [{"item_name": "Item A"}] if division else [{"item_name": "Item A"}, {"item_name": "Starter Pack"}]

    def total(division=None):
        asked.append(("total", division))
        return 12 if division else 15

    def below(division=None):
        asked.append(("below", division))
        return [{"item_name": "Item A"}] if division else [{"item_name": "Item A"}, {"item_name": "Starter Pack"}]

    overdue = [{"account": "Shop A", "overdue_bhd": 12.5}]
    with _Patched((digest, "low_stock_items", items), (digest, "low_stock_total", total),
                  (digest, "overdue_receivables", lambda: overdue), (digest, "negative_margins", below)):
        acc = digest.all_alerts("Accessories")
        every = digest.all_alerts()
    assert asked == [("items", "Accessories"), ("below", "Accessories"), ("total", "Accessories"),
                     ("items", None), ("below", None), ("total", None)]
    assert acc["low_stock_count"] == 12 and [r["item_name"] for r in acc["low_stock"]] == ["Item A"]
    assert every["low_stock_count"] == 15 and len(every["low_stock"]) == 2
    assert (acc["negative_margin_count"], every["negative_margin_count"]) == (1, 2), "a SIM item below cost is not the page's to fix"
    # receivables are per customer: the same either way
    assert acc["overdue_receivables"] == every["overdue_receivables"] == overdue and acc["overdue_total_bhd"] == 12.5
    assert set(acc) == set(every) == {"low_stock", "low_stock_count", "overdue_receivables", "overdue_count",
                                      "overdue_total_bhd", "negative_margins", "negative_margin_count", "has_alerts"}
    # the endpoint and the email sender still call it bare
    main = _read("app/main.py").split('@app.get("/digest/alerts")', 1)[1].split("@app.get(", 1)[0]
    assert "return all_alerts()" in main
    assert "data = all_alerts()" in _read("scripts/send_digest.py")


@test("inventory: the page the low-stock card opens carries the Dashboard's own count beside its every-division stats")
def _():
    from app import reports
    seen: list = []

    def rpc(sql):
        seen.append(sql)
        if "COUNT(*) AS n FROM v_stock_health" in sql:
            return [{"n": 12}]
        if sql.startswith("SELECT item_name, current_stock, stock_value"):       # the page's rows: every division
            return [{"item_name": "Item A", "status": "low_stock"}, {"item_name": "Starter Pack", "status": "urgent_out_of_stock"}]
        return []

    def inventory(q):
        with _Patched((reports, "exec_sql", q), (reports, "recent_receipts", lambda: []),
                      (reports, "reserved_stock", lambda: {"available": False}), (reports, "stock_by_warehouse", lambda: [])):
            return reports.inventory()

    inv = inventory(rpc)
    # the card said 12; the page it opens says 2 for every division AND carries the card's 12
    assert inv["low_stock_accessories"] == 12 and inv["by_status"] == {"low_stock": 1, "urgent_out_of_stock": 1}
    assert [s for s in seen if "COUNT(*) AS n" in s] == [OLD_TOTAL_SQL.replace(" LIMIT 1", ACC_ITEMS + " LIMIT 1")], \
        "app.digest's one definition, through the report's own reader"
    assert "division" not in next(s for s in seen if s.startswith("SELECT item_name, current_stock, stock_value")), \
        "the page's rows and stats stay every division"

    def no_count(sql):
        if "COUNT(*) AS n FROM v_stock_health" in sql:
            raise RuntimeError("statement timeout")
        return rpc(sql)
    with _Patched((reports.log, "disabled", True)):                              # its warning is expected
        inv2 = inventory(no_count)
    assert inv2["low_stock_accessories"] is None and len(inv2["rows"]) == 2, "a failed count costs the caption only"
    # a count is not a cost: a login without 'Margins' reads it too
    assert reports.inventory_for_viewer(inv, sees_cost=False)["low_stock_accessories"] == 12
    # the page says both; the Dashboard's card still opens it
    page = _read("web/src/pages/Inventory.tsx")
    assert "low_stock_accessories?: number | null" in page and "{data.low_stock_accessories != null" in page
    assert "`every division · ${num(data.low_stock_accessories)} are Mobile Accessories, the Dashboard's count`" in page
    assert "label={accOnly ? 'Low-stock items · Accessories' : 'Low-stock items'} to=\"/inventory\"" in _read("web/src/pages/Dashboard.tsx")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. momentum: Accessories only
# ═══════════════════════════════════════════════════════════════════════════════

@test("movers: both lists read v_stock_health for Accessories only — items it sold, not handed out (no starter pack can ride in)")
def _():
    from app import reports
    seen: list = []

    def rpc(sql):
        seen.append(sql)
        return [{"item_name": "Item A", "sold_30d": 9, "sold_90d": 9, "current_stock": 4, "status": "healthy", "momentum": 3.0}]

    with _Patched((reports, "exec_sql", rpc)):
        out = reports.movers(5)
    rising, falling = seen
    for sql in seen:
        assert " FROM v_stock_health WHERE sold_90d > 0" + ACC_ITEMS + " AND " in sql, sql
        assert "AS momentum" in sql and "sold_30d / NULLIF(sold_90d / 3.0, 0)" in sql
        assert sql.startswith("SELECT item_name, sold_30d, sold_90d, current_stock, status, ROUND("), "the units travel with the %"
        _safe_select(sql, {"v_stock_health", "v_sales"})
    assert set(out) == {"rising", "falling"} and out["rising"][0]["item_name"] == "Item A"
    with _Patched((reports, "exec_sql", lambda sql: None)):
        assert reports.movers(3) == {"rising": [], "falling": []}


@test("movers: ties in momentum go by units, then by name — the same five every time, the biggest first; a faller has stock to go dead")
def _():
    from app import reports
    seen: list = []
    with _Patched((reports, "exec_sql", lambda sql: seen.append(sql) or [])):
        reports.movers(5)
        reports.movers(8)
    rising, falling, rising8, falling8 = seen
    # momentum stops at 3.00 and at 0.00, so much of the pool ties: ordered by momentum alone, the database
    # chose which five came back (and a small seller could push out the biggest riser)
    assert rising.endswith(" AND sold_30d > 0 ORDER BY momentum DESC, sold_30d DESC, item_name LIMIT 5"), rising
    assert falling.endswith(" AND status NOT IN ('urgent_out_of_stock','low_stock') "
                            "ORDER BY momentum ASC, sold_90d DESC, item_name LIMIT 5"), falling
    assert rising8.endswith(" LIMIT 8") and falling8.endswith(" LIMIT 8")
    for sql in seen:
        order = sql.split(" ORDER BY ", 1)[1].rsplit(" LIMIT ", 1)[0]
        assert order.split(", ")[-1] == "item_name", "the last key is unique: no two rows can swap places"
    # a faller is never an item of the page's own reorder count (the low-stock statuses, app.digest):
    # one that sold out, or runs low, stopped selling for want of stock
    import inspect
    from app import digest
    assert "status IN ('urgent_out_of_stock','low_stock')" in inspect.getsource(digest.low_stock_total)
    assert "status NOT IN" not in rising, "a riser may be low on stock: that is the point of 'restock'"


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


def _health_rpc(per_day=50.0, total=1000.0, overdue=400.0, fail=(), focus_total=None):
    """An exec_sql stand-in for business_health(): records every SQL, answers by shape. `total` / `overdue` are
    the account rows added up; `focus_total` is Focus's own Grand Total for the snapshot (None = none stored)."""
    seen: list = []

    def q(sql):
        seen.append(sql)
        for key in fail:
            if key in sql:
                raise RuntimeError(f"{key} unavailable")
        if "FROM ar_ageing_totals" in sql:
            return [] if focus_total is None else [{"as_of_date": "2026-10-07", "focus_total_bhd": focus_total,
                                                    "focus_over90_bhd": 0, "rows_total_bhd": total}]
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
    for b in (reports.DSO_BASIS, reports.DSO_BASIS_ROWS):
        assert "every customer balance in Focus" in b and "Accessories sales per day" in b and "VAT-inclusive" in b
    # the basis names which receivables figure is divided
    assert "Focus's Grand Total" in reports.DSO_BASIS and "account rows added up" in reports.DSO_BASIS_ROWS


@test("business health: DSO = every customer balance ÷ Accessories sales per day; the receivables are never narrowed")
def _():
    from app import reports
    q, seen = _health_rpc()
    with _Patched((reports, "exec_sql", q)):
        h = reports.business_health()
    assert [s for s in seen if "/90.0 AS d" in s] == [reports.DSO_SALES_PER_DAY_SQL]
    assert h["dso_days"] == 20.0 and h["ar_overdue_pct"] == 40.0
    assert (h["dso_receivables_bhd"], h["dso_sales_per_day_bhd"], h["dso_basis"]) == (1000.0, 50.0, reports.DSO_BASIS_ROWS)
    ar_sql = [s for s in seen if "v_receivables" in s]
    assert len(ar_sql) == 2 and all("division" not in s and "v_sales" not in s for s in ar_sql), \
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


@test("business health: ONE receivables figure on the page — the DSO and the overdue share divide the Receivables card's own (Focus's Grand Total when stored)")
def _():
    from app import metrics, reports
    # the account rows add up to more than Focus's Grand Total (the export prints credits as positive balances)
    q, seen = _health_rpc(per_day=50.0, total=1000.0, overdue=400.0, focus_total=800.0)
    with _Patched((reports, "exec_sql", q)):
        h = reports.business_health()
    assert (h["dso_receivables_bhd"], h["dso_receivables_source"]) == (800.0, "focus_total")
    assert h["dso_days"] == 16.0, "800 ÷ 50 a day: the figure the Receivables card shows, not the rows' 1,000"
    assert h["ar_overdue_pct"] == 50.0, "and the overdue share is of that same figure: 400 of 800"
    assert h["dso_basis"] == reports.DSO_BASIS and "Focus's Grand Total" in h["dso_basis"]
    # the very read the Command Centre's Receivables tile makes (app.metrics ar.total), so the two cannot part
    assert [s for s in seen if "FROM ar_ageing_totals" in s] == [metrics.AR_TOTALS_SQL]
    # no Grand Total stored for the snapshot: the rows, as the tile then shows them, and the basis says so
    q, _ = _health_rpc(focus_total=None)
    with _Patched((reports, "exec_sql", q)):
        h = reports.business_health()
    assert (h["dso_receivables_bhd"], h["dso_receivables_source"], h["dso_basis"]) == (1000.0, "rows", reports.DSO_BASIS_ROWS)
    assert h["dso_days"] == 20.0 and h["ar_overdue_pct"] == 40.0
    # the totals read failing (the table predates economics_v2) costs nothing: the rows again
    q, _ = _health_rpc(fail=("FROM ar_ageing_totals",))
    with _Patched((reports, "exec_sql", q)):
        h = reports.business_health()
    assert h["dso_receivables_source"] == "rows" and h["dso_days"] == 20.0
    # a snapshot with nothing owed: 0 days and 0 %, never a division by zero
    q, _ = _health_rpc(total=0.0, overdue=0.0, focus_total=0.0)
    with _Patched((reports, "exec_sql", q)):
        h = reports.business_health()
    assert (h["dso_days"], h["ar_overdue_pct"], h["dso_receivables_source"]) == (0.0, 0.0, "focus_total")


@test("margin: the page's margin, below-cost count and list leave out report items filed outside Accessories; every other reader reads the whole report")
def _():
    import inspect
    from app import digest, metrics, reports
    from app import margin_truth as mt
    cond = mt.ACCESSORIES_ONLY
    # NOT IN the other divisions, never IN Accessories: a name the resolver does not know stays in the margin
    assert cond == "item_name NOT IN (SELECT item_name FROM v_item_division WHERE division <> 'Accessories')"
    seen: list = []

    def q(sql):                         # a report of three costed items, one of them a starter pack sold below cost
        seen.append(sql)
        acc = cond in sql
        if "COUNT(*) AS n" in sql:
            return [{"n": 2 if acc else 3, "below": 1 if acc else 2, "net": 110, "net_ex": 100 if acc else 200,
                     "gp_ex": 40 if acc else 50, "gp_rep": 50}]
        if "FROM v_product_margin" in sql and "is_below_cost" in sql:
            rows = [{"item_name": "Item A", "margin_ex_vat_pct": -5.0, "is_below_cost": True}]
            return rows if acc else rows + [{"item_name": "Starter Pack", "margin_ex_vat_pct": -9.0, "is_below_cost": True}]
        return []

    probe = dict(mt._probe)
    try:
        tot, basis = mt.margin_totals(q=q, where=cond)
        assert (basis, tot["n"], tot["below"]) == ("view", 2, 1) and seen[-1] == mt._TOTALS + f" AND ({cond})"
        whole, _ = mt.margin_totals(q=q)
        assert whole["n"] == 3 and seen[-1] == mt._TOTALS, "the whole report, the SQL it always was, for every other reader"
        _safe_select(mt._TOTALS + f" AND ({cond})", {"v_product_margin", "v_item_division"})
        # the one margin definition takes the condition; the Dashboard's health read is the caller that passes it
        assert metrics.profitability(q=q, where=cond)["official"]["pct"] == 40.0
        assert metrics.profitability(q=q)["official"]["pct"] == 25.0
        with _Patched((reports, "exec_sql", q)):
            h = reports.business_health()
        assert (h["gp_pct"], h["gp_bhd"], h["below_cost_count"]) == (40.0, 40.0, 1), "the starter pack is not in the page's margin"
        assert "where=margin_truth.ACCESSORIES_ONLY" in inspect.getsource(reports.business_health)
        # the below-cost list behind the alerts strip and the "Fix pricing" action: narrowed for the page only
        with _Patched((mt, "exec_sql", q)):
            acc, every = digest.negative_margins("Accessories"), digest.negative_margins()
        assert [r["item_name"] for r in acc] == ["Item A"] and [r["item_name"] for r in every] == ["Item A", "Starter Pack"]
        assert f" AND (is_below_cost AND ({cond})) ORDER BY gp_ex_vat_bhd ASC LIMIT 20" in seen[-2]
        assert " AND (is_below_cost) ORDER BY gp_ex_vat_bhd ASC LIMIT 20" in seen[-1], "the digests' read, as it was"
    finally:
        mt._probe.clear()
        mt._probe.update(probe)
    # the Command Centre and the Profitability page go on reading the whole report
    assert "margin_truth.margin_totals(q=r.q)[0]" in inspect.getsource(metrics.load_focus)
    assert "tot, _ = margin_totals()" in inspect.getsource(reports.margins)


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
    for key in ("dso_basis", "dso_receivables_bhd", "dso_sales_per_day_bhd", "dso_receivables_source"):
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


@test("web (phone): a long accessory name is cut, its units and momentum % stay on the card; 'BHD' stays with its amount")
def _():
    dash = _read("web/src/pages/Dashboard.tsx")
    # the Momentum lists are all accessories now (long VFAN names): an auto grid column grew to the name's
    # full width at 360–390 px and pushed the % off the card; grid-cols-1 is minmax(0, 1fr)
    card = dash.split("<Flame size={18}", 1)[1].split("</Card>", 1)[0]
    assert '<div className="grid grid-cols-1 gap-x-10 gap-y-4 md:grid-cols-2">' in card
    row = dash.split("function MoverList(", 1)[1].split("\n}\n", 1)[0]
    assert '<span className="min-w-0 truncate font-medium" title={r.item_name}>' in row
    assert '<span className="shrink-0 whitespace-nowrap tabular-nums" title={detail}>' in row, "the figures never wrap or shrink"
    assert '<span className="whitespace-nowrap font-semibold">{bhd(k.overdue_total_bhd, 0)}</span>' in dash


@test("web: a momentum row says its units beside the % (several rows tie at the same %); the DSO tile says what it divides on the tile")
def _():
    dash = _read("web/src/pages/Dashboard.tsx")
    row = dash.split("function MoverList(", 1)[1].split("\n}\n", 1)[0]
    # a riser: what it sold in the 30 days; a fader: what is still on hand (an older API sends no stock: the % alone)
    assert "const units = up ? `${num(r.sold_30d)} sold` : r.current_stock != null ? `${num(r.current_stock)} on hand` : null" in row
    assert "{units && <span" in row and "{pct > 0 ? '+' : ''}{pct}%" in row
    assert "${num(r.sold_30d)} sold in the last 30 days, ${num(r.sold_90d)} in the last 90" in row, "both windows on hover"
    assert "current_stock?: number" in dash
    # the DSO: its two parts on hover, and on the tile itself what they are (a phone has no hover)
    tile = dash.split('label="Collection speed · DSO"', 1)[1].split(' to="/receivables" />', 1)[0]
    assert "{data.health.dso_basis && <>receivables ÷ a day of Accessories sales · </>}" in tile
    assert "bhd(data.health.dso_receivables_bhd, 0)} ÷ ${bhd(data.health.dso_sales_per_day_bhd, 0)} a day" in tile
    assert "{data.health.ar_overdue_pct.toFixed(0)}%</span> of receivables overdue" in tile


@test("web: a Dashboard tab left open catches up with a data load — it asks again while the header chip's date is ahead, and on coming back to the tab")
def _():
    dash = _read("web/src/pages/Dashboard.tsx")
    assert "import { behindFocus } from '@/lib/dataDate'" in dash and "import { useEffect, useMemo, useState } from 'react'" in dash
    q = dash.split("queryKey: ['report', 'dashboard'],", 1)[1].split("})", 1)[0]
    assert "staleTime: 60_000," in q and "refetchOnWindowFocus: true," in q
    # no standing poll (it would have the API rebuild the payload for as long as a tab stays open): the page asks
    # every CATCH_UP_MS only while it is behind the chip, and stops once level
    assert "refetchInterval: (q) => (behindFocus(fresh?.focus_to, q.state.data) ? CATCH_UP_MS : false)," in q
    assert "const CATCH_UP_MS = 30_000" in dash and not re.search(r"refetchInterval:\s*\d", dash)
    # ...and at once when the chip moves ahead: `behind` flips, the effect runs once
    assert "const behind = behindFocus(fresh?.focus_to, data)" in dash
    effect = dash.split("const behind = behindFocus(fresh?.focus_to, data)", 1)[1].split("\n\n", 1)[0]
    assert "useEffect(() => {\n    if (behind) void refetch()\n  }, [behind, refetch])" in effect
    assert "const { data, isLoading, refetch } = useQuery({" in dash
    # the chip's query (the same key, read once between the chip and the page) is declared before the report's
    assert dash.index("queryKey: ['freshness'],") < dash.index("queryKey: ['report', 'dashboard'],")
    lib = _read("web/src/lib/dataDate.ts")
    assert not re.search(r"^\s*import\b", lib, re.M), "pure and import-free: the node test loads it on its own"
    assert "return chip !== '' && chip > isoDay(page.data_as_of)" in lib and "if (!page) return false" in lib


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
    # the review's fixes on real data: the same lists every time, ties by units, no faller from the reorder count,
    # and the Inventory page carries the very count the Dashboard shows
    assert mv == reports.movers(8), "the momentum lists change between two reads of the same data"
    for side, units, sign in (("rising", "sold_30d", -1), ("falling", "sold_90d", 1)):
        keys = [(sign * float(r["momentum"]), -float(r[units])) for r in mv[side]]
        assert keys == sorted(keys), (side, keys)
    low = {str(r["item_name"]) for r in digest.low_stock_items("Accessories")}
    assert not low & {str(r["item_name"]) for r in mv["falling"]}, "a faller that is in the page's own reorder count"
    assert reports.inventory()["low_stock_accessories"] == acc
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
