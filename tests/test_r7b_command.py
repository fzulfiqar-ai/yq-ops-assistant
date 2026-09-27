"""R7b — the Management Command Centre (Sprint 4, plan §8, §9, §14, §17, §27, §33).

    python -m tests.test_r7b_command

Same lightweight runner as tests/test_r7a_focus.py. Every test runs against an in-memory stand-in
for the read-only RPC: a router that answers each SQL the metric dictionary sends (app/metrics.py)
with synthetic rows and records the parameters it was given. Nothing here reaches a database or the
network; the pure tests pass with no .env. All data is synthetic (made-up reps, shops, items and
amounts).

Covered:
  * business days (Fri + Sat off), the period windows and their comparisons (same business days
    last month / last quarter, previous business day, the 7 days before, the month before);
  * money to the fils half-up, shares and changes to one decimal, nearest-rank percentiles, the rep
    name rule ('<name>' or '<name> - …'), Postgres timestamps;
  * every module against one synthetic world: Accessories-only ex-VAT sales vs the comparison, SIM
    apart, B2B/B2C, business-day pace vs the company target, the 13-week trend; the order funnel
    (test orders out), accepted rate, confirm-time P50/P90 with open orders at their age, orders
    waiting over 24 business hours across a weekend, invoice match, self-orders; the rep table
    (attainment + named-shop share + marketplace orders + last invoice); active / new / dormant
    accounts, top-10 concentration, Cash Customer share; movers, sold out with demand, stock shape
    at cost; official and landed margin; receivables with and without Focus's Grand Total, over 90,
    top overdue, unowned over-90 AR;
  * Needs attention: every exception, ranked alerts first; stale data; a failed source costs one
    tile, never the page;
  * before the r7b migration: the agent-view fallback (no accepted rate, no match rate, test orders
    not told apart, said so), and the probe remembered;
  * caching: per period, cleared by an upload (reports.invalidate_dashboard_cache);
  * the routes: admin and management 200 (the read-only login passes the central gate on GET),
    member / salesman / storekeeper 403, a bad period 400; /freshness for every login with the
    match rate for the command roles only; the route → gate table sees a plain user dependency;
  * the migration pair as text (additive, idempotent, grants, nothing personal, no CASCADE, no
    security_invoker) and a local Postgres replay that SKIPs without a scratch cluster;
  * the web: the nav regroup (§33), management's nav and home, the AI tools / Archive group
    admin-only, the header chip replacing the quotes and the "Live" pill, the page's order.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


MIGRATION = ROOT / "scripts" / "r7b_command_views_migration.sql"
REVERSE = ROOT / "scripts" / "r7b_command_views_reverse.sql"
WEB = ROOT / "web" / "src"


class _Patched:
    """Temporarily set module attributes; restores on exit."""

    def __init__(self, *triples):
        self.triples = triples
        self.saved = []

    def __enter__(self):
        for mod, name, value in self.triples:
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
        return self

    def __exit__(self, *exc):
        for mod, name, old in reversed(self.saved):
            setattr(mod, name, old)
        return False


def _raises(fn, exc=Exception, want: str = ""):
    try:
        fn()
    except exc as e:  # noqa: BLE001
        assert want.lower() in str(e).lower(), (want, str(e))
        return e
    raise AssertionError(f"expected {exc.__name__} containing {want!r}")


# ── the synthetic world ─────────────────────────────────────────────────────────
# Sunday 27 Sep 2026, 10:00 in Bahrain; the Focus data runs to Thursday 24 Sep.

NOW = datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)
FOCUS_TO = "2026-09-24"
MISSING = RuntimeError('{"code": "42P01", "message": "relation \\"public.v_command_orders\\" does not exist"}')

ANCHOR = {"focus_to": FOCUS_TO, "stock_as_of": FOCUS_TO, "ar_as_of": FOCUS_TO, "margin_report_date": FOCUS_TO,
          "target_bhd": "10000.0"}

SALES = [
    {"k": "cur", "channel": "B2B", "division": "Accessories", "net_bhd": 900, "invoices": 40, "cash_invoices": 10, "cash_net_bhd": 50},
    {"k": "cur", "channel": "B2C", "division": "Accessories", "net_bhd": 30, "invoices": 5, "cash_invoices": 5, "cash_net_bhd": 30},
    {"k": "cur", "channel": "B2B", "division": "SIM", "net_bhd": 500, "invoices": 3, "cash_invoices": 0, "cash_net_bhd": 0},
    {"k": "cmp", "channel": "B2B", "division": "Accessories", "net_bhd": 600, "invoices": 30, "cash_invoices": 8, "cash_net_bhd": 40},
    {"k": "cmp", "channel": "B2C", "division": "Accessories", "net_bhd": 20, "invoices": 4, "cash_invoices": 4, "cash_net_bhd": 20},
    {"k": "cmp", "channel": "B2B", "division": "SIM", "net_bhd": 250, "invoices": 2, "cash_invoices": 0, "cash_net_bhd": 0},
    {"k": "month", "channel": "B2B", "division": "Accessories", "net_bhd": 900, "invoices": 40, "cash_invoices": 10, "cash_net_bhd": 50},
    {"k": "month", "channel": "B2C", "division": "Accessories", "net_bhd": 30, "invoices": 5, "cash_invoices": 5, "cash_net_bhd": 30},
]
TREND = [{"week_start": "2026-09-20", "net_bhd": 500}, {"week_start": "2026-09-13", "net_bhd": 400}]

SALESMEN = [
    {"id": 1, "name": "Rep One", "focus_name": "Rep One", "is_active": True},
    {"id": 2, "name": "Rep Two", "focus_name": "Rep Two", "is_active": True},
    {"id": 3, "name": "Rep Idle", "focus_name": "Rep Idle", "is_active": True},     # never invoiced
    {"id": 4, "name": "Rep Old", "focus_name": "Rep Old", "is_active": False},      # switched off: never flagged
    {"id": 5, "name": "No Focus", "focus_name": None, "is_active": True},           # no Focus name: never flagged
]
REPS = [
    {"rep": "Rep One - Acc WH", "last_invoice": "2026-09-24", "month_net_bhd": 300, "month_named_bhd": 240},
    {"rep": "Rep Two - Acc WH", "last_invoice": "2026-09-10", "month_net_bhd": 100, "month_named_bhd": 100},
    {"rep": "Rep Gone - Acc WH", "last_invoice": "2026-04-02", "month_net_bhd": 0, "month_named_bhd": 0},
    {"rep": "Counter B2C", "last_invoice": "2026-09-24", "month_net_bhd": 20, "month_named_bhd": 0},
]


def _att(name, net, target=None, tiers=(None, None), kick=(0.01, 0.02, 0.03), invoices=5, shops=3):
    return {"salesman": name, "target_period": "" if target else None, "team": "normal" if target else None,
            "target_bhd": target, "tier2_bhd": tiers[0], "tier3_bhd": tiers[1],
            "kickback_t1": kick[0] if target else None, "kickback_t2": kick[1] if target else None,
            "kickback_t3": kick[2] if target else None, "net_bhd": net, "gross_bhd": net * 1.1,
            "invoices": invoices, "shops": shops, "last_sale": FOCUS_TO, "no_target": target is None,
            "period": "2026-09", "data_through": FOCUS_TO}


ATTAINMENT = [_att("Rep One", 300, 100, (200, 300)), _att("Rep Two", 100, 500, (None, None)),
              _att("Counter B2C", 20, None, invoices=2, shops=0)]

CUSTOMERS = ([
    {"customer_name": "Shop A", "first_invoice": "2025-10-01", "last_invoice": "2026-09-20", "net_12m": 1000},
    {"customer_name": "Shop B", "first_invoice": "2026-09-01", "last_invoice": "2026-09-15", "net_12m": 50},
    {"customer_name": "Shop C", "first_invoice": "2025-10-01", "last_invoice": "2026-08-01", "net_12m": 800},
    {"customer_name": "Shop D", "first_invoice": "2025-01-01", "last_invoice": "2026-06-01", "net_12m": 300},
    {"customer_name": "Shop E", "first_invoice": "2024-01-01", "last_invoice": "2025-06-01", "net_12m": 0},
] + [{"customer_name": f"Shop {i:02d}", "first_invoice": "2025-11-01", "last_invoice": "2026-09-10", "net_12m": 10}
     for i in range(1, 13)])

MOVERS = [
    {"item_name": "Item Up", "qty_30": 20, "net_30": 100, "qty_prev": 5, "net_prev": 20, "qty_60": 25, "net_60": 120},
    {"item_name": "Item Down", "qty_30": 1, "net_30": 5, "qty_prev": 10, "net_prev": 60, "qty_60": 11, "net_60": 65},
    {"item_name": "Item Gone", "qty_30": 0, "net_30": 0, "qty_prev": 4, "net_prev": 16, "qty_60": 4, "net_60": 16},
]
STOCK = [
    {"item_name": "Item Up", "current_stock": 50, "sold_90d": 30, "days_cover": 150, "cost_bhd": 2.0},
    {"item_name": "Item Down", "current_stock": 400, "sold_90d": 11, "days_cover": 3272.7, "cost_bhd": 1.5},
    {"item_name": "Item Gone", "current_stock": 0, "sold_90d": 4, "days_cover": 0, "cost_bhd": 1.0},
    {"item_name": "Item Dead", "current_stock": 10, "sold_90d": 0, "days_cover": None, "cost_bhd": 3.0},
    {"item_name": "Item NoCost", "current_stock": 5, "sold_90d": 1, "days_cover": 450, "cost_bhd": None},
]
MARGIN_TOTALS = {"n": 10, "below": 1, "net": 1100, "net_ex": 1000, "gp_ex": 376, "gp_rep": 400}
BELOW_COST = [{"item_name": "Item Down", "gp_ex_vat_bhd": -5.5, "margin_ex_vat_pct": -12.5, "is_below_cost": True}]
LANDED = {"costed_net_bhd": 800, "gp_bhd": 272, "acc_net_bhd": 1000}
RECEIVABLES = [
    {"account": "Acct Alpha", "outstanding_bhd": 500, "overdue_bhd": 400, "over_90_bhd": 300, "last_receipt_date": None},
    {"account": "Acct Beta", "outstanding_bhd": 200, "overdue_bhd": 150, "over_90_bhd": 150, "last_receipt_date": "2026-05-01"},
    {"account": "Acct Gamma", "outstanding_bhd": 100, "overdue_bhd": 0, "over_90_bhd": 0, "last_receipt_date": "2026-09-01"},
    {"account": "Acct Counter", "outstanding_bhd": 50, "overdue_bhd": 50, "over_90_bhd": 50, "last_receipt_date": "2026-09-10"},
]
AR_OWNER = [
    {"account": "Acct Alpha", "rep": "Rep Gone - Acc WH", "channel": "B2B", "last_sale": "2026-04-02"},
    {"account": "Acct Beta", "rep": "Rep One - Acc WH", "channel": "B2B", "last_sale": "2026-09-01"},
    {"account": "Acct Gamma", "rep": "Rep Two - Acc WH", "channel": "B2B", "last_sale": "2026-09-02"},
    {"account": "Acct Counter", "rep": "Counter B2C", "channel": "B2C", "last_sale": "2026-09-03"},
]
AR_TOTALS = {"as_of_date": FOCUS_TO, "focus_total_bhd": 800, "focus_over90_bhd": 480, "rows_total_bhd": 850}


def _o(id, status, created, rep=1, total=10.0, units=5, units_c=None, confirmed=None, delivered=None,
       source="market", test=False, links=0, typed=False, total_c=None):
    names = {1: "Rep One", 2: "Rep Two"}
    return {"id": id, "order_no": f"YQ-2609-{9000 + id}", "status": status, "source": source, "salesman_id": rep,
            "salesman_name": names.get(rep), "is_test": test, "created_at": created, "confirmed_at": confirmed,
            "delivered_at": delivered, "cancelled_at": None, "total_bhd": total, "total_confirmed_bhd": total_c,
            "units_ordered": units, "units_confirmed": units_c, "has_invoice_no": typed, "focus_links_n": links}


ORDERS = [
    _o(1, "delivered", "2026-09-21T07:00:00+00:00", 1, 12.5, 10, 10, "2026-09-21T09:00:00+00:00",
       "2026-09-22T07:00:00+00:00", links=1, total_c=12.5),
    _o(2, "delivered", "2026-09-22T07:00:00+00:00", 2, 9.0, 8, 6, "2026-09-22T13:00:00+00:00",
       "2026-09-23T06:00:00+00:00", total_c=7.2),
    _o(3, "new", "2026-09-23T06:00:00+00:00", 1, 20.0, 5),         # Wed 09:00 → Sun 10:00 = 49 business hours
    _o(4, "new", "2026-09-24 17:00:00+00", 2, 3.0, 2),             # Thu 20:00 → Sun 10:00 = 14 business hours
    _o(5, "cancelled", "2026-09-22T08:00:00+00:00", 1, 2.0, 3),
    _o(6, "delivered", "2026-09-21T08:00:00+00:00", 1, 99.0, 50, 50, "2026-09-21T08:30:00+00:00",
       "2026-09-21T09:00:00+00:00", test=True, total_c=99.0),   # a test order: never counted
    _o(7, "confirmed", "2026-09-24T06:00:00+00:00", 2, 4.0, 4, 4, "2026-09-24T08:00:00+00:00",
       source="salesman", typed=True, total_c=4.0),
]
MATCH = {"eligible": 2, "matched": 1, "unmatched_bhd": 7.2, "invoiced_open": 1, "last_order_at": "2026-09-24 17:00:00+00"}


class FakeRPC:
    """Answers every SQL app/metrics.py sends, from the synthetic world. `fail` maps a source name to an
    exception; `view=False` makes v_command_orders missing (before the r7b migration)."""

    def __init__(self, view: bool = True, fail: dict | None = None, ar_totals: bool = True, **over):
        self.view, self.fail, self.ar_totals = view, dict(fail or {}), ar_totals
        self.data = {"anchor": ANCHOR, "sales": SALES, "trend": TREND, "reps": REPS, "attainment": ATTAINMENT,
                     "customers": CUSTOMERS, "movers": MOVERS, "stock": STOCK, "margin_totals": MARGIN_TOTALS,
                     "below_cost": BELOW_COST, "landed": LANDED, "receivables": RECEIVABLES, "ar_owner": AR_OWNER,
                     "orders": ORDERS, "match": MATCH}
        self.data.update(over)
        self.calls: list[tuple[str, str, list | None]] = []

    def _source(self, sql: str) -> str:
        from app import metrics as m
        from app import reports
        s = sql.strip()
        if s == m.ANCHOR_SQL:
            return "anchor"
        if s.startswith("SELECT (SELECT MAX(sale_date) FROM v_sales)::text AS focus_to") and "stock_as_of" not in s:
            return "fresh_anchor"
        table = [("jsonb_to_recordset", "sales"), ("AS week_start", "trend"), ("AS month_named_bhd", "reps"),
                 ("AS net_12m", "customers"), ("AS qty_30", "movers"), ("FROM v_stock_health h", "stock"),
                 ("AS costed_net_bhd", "landed"), ("FROM ar_ageing_totals", "ar_totals"),
                 ("DISTINCT ON (v.customer_name)", "ar_owner"), ("AS invoiced_open", "match"),
                 ("FROM v_command_orders", "orders"), ("units_count AS units_ordered", "orders_fallback")]
        for needle, name in table:
            if needle in s:
                return name
        if s == m.RECEIVABLES_SQL:
            return "receivables"
        if s == m.LAST_ORDER_SQL:
            return "last_order"
        if s == reports.ATTAINMENT_SQL.strip():
            return "attainment"
        if "FROM v_product_margin" in s and "COUNT(*) AS n" in s:
            return "margin_totals"
        if "FROM v_product_margin" in s and "is_below_cost" in s:
            return "below_cost"
        raise AssertionError(f"unexpected SQL: {s[:160]}")

    def _answer(self, sql: str, params: list | None):
        name = self._source(sql)
        self.calls.append((name, sql, params))
        if name in self.fail:
            raise self.fail[name]
        if name in ("orders", "match") and not self.view:
            raise MISSING
        if name == "ar_totals":
            if not self.ar_totals:
                raise RuntimeError('relation "ar_ageing_totals" does not exist')
            return [AR_TOTALS]
        if name == "fresh_anchor":
            return [{"focus_to": self.data["anchor"]["focus_to"]}]
        if name == "last_order":
            return [{"last_order_at": "2026-09-24 17:00:00+00"}]
        if name == "orders_fallback":
            # the agent view: no is_test, no confirmed units, no links
            keep = ("id", "order_no", "status", "source", "salesman_name", "created_at", "confirmed_at",
                    "delivered_at", "cancelled_at", "total_bhd", "units_ordered")
            return [{**{k: o.get(k) for k in keep}, "salesman_id": None} for o in self.data["orders"]]
        val = self.data[name]
        return [dict(val)] if isinstance(val, dict) else [dict(r) for r in val]

    def q(self, sql):
        return self._answer(sql, None)

    def qp(self, sql, params):
        assert len(params) <= 8, "the RPC binds $1..$8 only"
        return self._answer(sql, list(params))

    def params(self, name) -> list | None:
        return next((p for n, _s, p in self.calls if n == name), None)

    def names(self) -> list[str]:
        return [n for n, _s, _p in self.calls]


def _reader(fake: FakeRPC, now: datetime = NOW):
    from app import metrics
    return metrics.Reader(q=fake.q, qp=fake.qp, salesmen_fn=lambda: [dict(s) for s in SALESMEN], now=now)


def _overview(fake: FakeRPC | None = None, period: str = "mtd", now: datetime = NOW) -> dict:
    from app import metrics
    metrics.invalidate()
    return metrics.overview(period, reader=_reader(fake or FakeRPC(), now), use_cache=False)


def _module(ov: dict, key: str) -> dict:
    return next(m for m in ov["modules"] if m["key"] == key)


def _tile(ov: dict, key: str) -> dict:
    for m in ov["modules"]:
        for t in m.get("tiles") or []:
            if t["key"] == key:
                return t
    raise AssertionError(f"no tile {key}")


def _items(ov: dict) -> dict:
    return {i["key"]: i for i in ov["modules"][0]["items"]}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. business days and the period windows
# ═══════════════════════════════════════════════════════════════════════════════

@test("business days: Sunday to Thursday count, Friday and Saturday never do")
def _():
    from app import metrics as m
    assert date(2026, 9, 25).weekday() == 4 and date(2026, 9, 26).weekday() == 5      # Fri, Sat
    assert not m.is_business_day(date(2026, 9, 25)) and not m.is_business_day(date(2026, 9, 26))
    assert m.is_business_day(date(2026, 9, 27)) and m.is_business_day(date(2026, 9, 24))
    assert m.business_days(date(2026, 9, 1), date(2026, 9, 24)) == 18
    assert m.business_days(date(2026, 9, 1), date(2026, 9, 30)) == 22
    assert m.business_days(date(2026, 9, 25), date(2026, 9, 26)) == 0
    assert m.business_days(date(2026, 9, 24), date(2026, 9, 1)) == 0
    assert m.nth_business_day(date(2026, 8, 1), 18, date(2026, 8, 31)) == date(2026, 8, 25)
    assert m.nth_business_day(date(2026, 2, 1), 40, date(2026, 2, 28)) == date(2026, 2, 28), "capped at the month end"
    assert m.prev_business_day(date(2026, 9, 27)) == date(2026, 9, 24), "Sunday's previous business day is Thursday"
    assert m.prev_business_day(date(2026, 9, 24)) == date(2026, 9, 23)


@test("windows: every period with its comparison, anchored on the data date")
def _():
    from app import metrics as m
    a = date(2026, 9, 24)
    cur, cmp = m.windows("mtd", a)
    assert (cur.start, cur.end) == (date(2026, 9, 1), a) and (cmp.start, cmp.end) == (date(2026, 8, 1), date(2026, 8, 25))
    assert cur.business_days == cmp.business_days == 18 and "same 18 business days last month" in cmp.basis
    cur, cmp = m.windows("today", a)
    assert (cur.start, cur.end, cmp.start, cmp.end) == (a, a, date(2026, 9, 23), date(2026, 9, 23))
    cur, cmp = m.windows("today", date(2026, 9, 27))
    assert cmp.start == date(2026, 9, 24), "Sunday compares with Thursday, not the weekend"
    cur, cmp = m.windows("7d", a)
    assert (cur.start, cur.end, cmp.start, cmp.end) == (date(2026, 9, 18), a, date(2026, 9, 11), date(2026, 9, 17))
    cur, cmp = m.windows("last_month", a)
    assert (cur.start, cur.end, cmp.start, cmp.end) == (date(2026, 8, 1), date(2026, 8, 31), date(2026, 7, 1), date(2026, 7, 31))
    cur, cmp = m.windows("last_month", date(2026, 1, 15))
    assert (cur.start, cur.end, cmp.start) == (date(2025, 12, 1), date(2025, 12, 31), date(2025, 11, 1)), "across a year"
    cur, cmp = m.windows("quarter", a)
    assert (cur.start, cmp.start) == (date(2026, 7, 1), date(2026, 4, 1))
    assert cmp.business_days == cur.business_days == 62 and cmp.end == date(2026, 6, 25)
    assert cur.label == "1 Jul – 24 Sep" and m.windows("mtd", a)[0].label == "1–24 Sep"
    assert m.span_label(date(2025, 12, 28), date(2026, 1, 3)) == "28 Dec 2025 – 3 Jan 2026"


@test("pace: business days so far and in the month, against the company target")
def _():
    from app import metrics as m
    p = m.month_pace(930, date(2026, 9, 24), "10000.0")
    assert (p["business_days_done"], p["business_days_total"], p["business_days_left"]) == (18, 22, 4)
    assert p["projected_bhd"] == 1136.667, p               # 930 / 18 × 22, to the fils half-up
    assert p["pct_of_target"] == 9.3 and p["projected_pct_of_target"] == 11.4 and p["on_track"] is False
    assert p["needed_per_business_day_bhd"] == 2267.5      # (10,000 − 930) ÷ 4
    none = m.month_pace(930, date(2026, 9, 24), None)
    assert none["target_bhd"] is None and none["on_track"] is None and none["pct_of_target"] is None
    assert m.month_pace(12000, date(2026, 9, 24), 10000)["needed_per_business_day_bhd"] == 0.0


@test("numbers: fils half-up, one-decimal shares and changes, nearest-rank percentiles, rep names, timestamps")
def _():
    from app import metrics as m
    assert m.money(0.0005) == 0.001 and m.money("2.9995") == 3.0 and m.money(None) == 0.0
    assert m.share(1, 3) == 33.3 and m.share(2, 3) == 66.7 and m.share(5, 0) is None
    assert m.change_pct(930, 620) == 50.0 and m.change_pct(1, 0) is None and m.change_pct(0, 10) == -100.0
    assert m.percentile([2, 2, 6, 14, 49], 50) == 6 and m.percentile([2, 2, 6, 14, 49], 90) == 49
    assert m.percentile([], 50) is None and m.percentile([7], 90) == 7
    assert m.rep_matches("Rep One - Acc WH", "Rep One") and m.rep_matches("Rep One", "Rep One")
    assert not m.rep_matches("Rep Oneill - Acc WH", "Rep One") and not m.rep_matches("", "Rep One")
    assert m.rep_base("Rep Gone - Acc WH") == "Rep Gone"
    t = m.parse_ts("2026-09-24 17:00:00.25+00")
    assert t == datetime(2026, 9, 24, 17, 0, 0, 250000, tzinfo=timezone.utc)
    assert m.iso_ts("2026-09-24 17:00:00+00") == "2026-09-24T17:00:00+00:00"
    assert m.bahrain_day("2026-09-24T21:30:00+00:00") == date(2026, 9, 25), "00:30 in Bahrain is the next day"
    assert m.short("A very long item name that goes on (and on)", 20) == "A very long item…"
    assert m.short("Short", 20) == "Short"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. the modules
# ═══════════════════════════════════════════════════════════════════════════════

@test("overview: Needs attention first, then the modules in the plan's order; JSON-clean")
def _():
    ov = _overview()
    assert [m["key"] for m in ov["modules"]] == ["attention", "sales", "orders", "team", "customers", "products",
                                                "profitability", "receivables"]
    json.dumps(ov)                                    # no Decimal, date or datetime left in the payload
    assert ov["unavailable"] == [] and ov["notes"] == []
    assert ov["period"]["key"] == "mtd" and ov["period"]["focus"]["label"] == "1–24 Sep"
    assert ov["period"]["live"]["label"] == "1–27 Sep", "marketplace figures follow the calendar"
    assert [o["key"] for o in ov["period"]["options"]] == ["today", "7d", "mtd", "last_month", "quarter"]
    for m in ov["modules"][1:]:
        for t in m["tiles"]:
            assert t["basis"] and "{" not in t["basis"], (t["key"], t["basis"])
            assert t["available"] is True or t.get("note"), t["key"]


@test("sales: Accessories ex-VAT vs the same business days last month; SIM apart; B2B / B2C; the windows sent")
def _():
    fake = FakeRPC()
    ov = _overview(fake)
    acc = _tile(ov, "sales.accessories")
    assert acc["value"] == 930.0 and acc["compare"]["value"] == 620.0 and acc["delta_pct"] == 50.0
    assert acc["basis"] == "Accessories · ex-VAT · 1–24 Sep vs the same 18 business days last month · data to 24 Sep"
    assert acc["invoices"] == 45
    sim = _tile(ov, "sales.sim")
    assert sim["value"] == 500.0 and sim["delta_pct"] == 100.0 and "never counted toward a target" in sim["basis"]
    ch = {c["label"]: c for c in _tile(ov, "sales.channels")["chips"]}
    assert ch["B2B"]["value"] == 900.0 and ch["B2C"]["value"] == 30.0 and ch["B2C"]["share_pct"] == 3.2
    wins = {w["k"]: (w["s"], w["e"]) for w in json.loads(fake.params("sales")[0])}
    assert wins == {"cur": ("2026-09-01", "2026-09-24"), "cmp": ("2026-08-01", "2026-08-25"),
                    "month": ("2026-09-01", "2026-09-24")}
    from app import metrics as m
    assert "NOT v.is_giveaway" in m.SALES_SQL and "v.division = 'Accessories'" in m.TREND_SQL
    pace = _tile(ov, "sales.pace")
    assert pace["value"] == 1136.667 and pace["target_bhd"] == 10000.0 and pace["on_track"] is False
    trend = _tile(ov, "sales.trend")["series"]
    assert len(trend) == 13 and trend[-1]["week_start"] == "2026-09-20" and trend[-1]["partial"] is True
    assert trend[-1]["value"] == 500.0 and trend[-2]["value"] == 400.0 and trend[0]["value"] == 0.0
    assert trend[0]["week_start"] == "2026-06-28"


@test("sales: last month compares with the month before; today with the previous business day")
def _():
    fake = FakeRPC()
    _overview(fake, "last_month")
    wins = {w["k"]: (w["s"], w["e"]) for w in json.loads(fake.params("sales")[0])}
    assert wins["cur"] == ("2026-08-01", "2026-08-31") and wins["cmp"] == ("2026-07-01", "2026-07-31")
    assert wins["month"] == ("2026-09-01", "2026-09-24"), "the pace always reads the data's own month"
    fake = FakeRPC()
    ov = _overview(fake, "today")
    wins = {w["k"]: (w["s"], w["e"]) for w in json.loads(fake.params("sales")[0])}
    assert wins["cur"] == ("2026-09-24", "2026-09-24") and wins["cmp"] == ("2026-09-23", "2026-09-23")
    assert ov["period"]["live"]["label"] == "27 Sep"


@test("order health: funnel without the test order, accepted rate, confirm time with open orders, waiting, match")
def _():
    ov = _overview()
    f = _tile(ov, "orders.funnel")
    st = {s["key"]: s for s in f["stages"]}
    assert (st["submitted"]["orders"], st["submitted"]["units"], st["submitted"]["bhd"]) == (6, 32, 50.5)
    assert (st["confirmed"]["orders"], st["confirmed"]["units"], st["confirmed"]["bhd"]) == (3, 20, 23.7)
    assert (st["delivered"]["orders"], st["delivered"]["units"], st["delivered"]["bhd"]) == (2, 16, 19.7)
    assert f["cancelled"] == 1 and f["open"] == 2 and [s["label"] for s in f["stages"]] == ["Placed", "Confirmed", "Delivered"]
    assert "test orders left out" in f["basis"]
    acc = _tile(ov, "orders.accepted")
    assert acc["value"] == 90.9 and (acc["units_accepted"], acc["units_ordered"]) == (20, 22)
    ct = _tile(ov, "orders.confirm_time")
    assert (ct["p50_hours"], ct["p90_hours"], ct["orders"], ct["open_included"]) == (6.0, 49.0, 5, 2)
    w = _tile(ov, "orders.waiting")
    assert w["value"] == 1 and w["bhd"] == 20.0 and w["oldest_hours"] == 49.0
    assert w["by_rep"] == [{"rep": "Rep One", "orders": 1, "bhd": 20.0}]
    mr = _tile(ov, "orders.match_rate")
    assert mr["value"] == 50.0 and mr["matched"] == 1 and mr["eligible"] == 2
    so = _tile(ov, "orders.self_order")
    assert so["value"] == 83.3 and so["orders"] == 6
    assert ov["freshness"]["match_rate"] == {"available": True, "pct": 50.0, "matched": 1, "eligible": 2}
    assert ov["freshness"]["last_order_at"] == "2026-09-24T17:00:00+00:00" and ov["freshness"]["marketplace_live"] is True


@test("order health: a Thursday-evening order is not late on Sunday morning (the weekend never counts)")
def _():
    from app import metrics as m
    fake = FakeRPC(orders=[_o(40, "new", "2026-09-24T17:00:00+00:00", 2, 3.0, 2)])
    ov = _overview(fake)
    assert _tile(ov, "orders.waiting")["value"] == 0 and "orders_waiting" not in _items(ov)
    later = NOW + timedelta(hours=11)                      # Sunday 21:00: 4 h Thursday + 21 h Sunday = 25 h
    ov = _overview(FakeRPC(orders=[_o(40, "new", "2026-09-24T17:00:00+00:00", 2, 3.0, 2)]), now=later)
    assert _tile(ov, "orders.waiting")["value"] == 1 and _items(ov)["orders_waiting"]["count"] == 1
    assert m.SLA_BUSINESS_HOURS == 24


@test("team: attainment rows with named-shop share, last invoice, silence and marketplace orders per rep")
def _():
    ov = _overview()
    t = _tile(ov, "team.reps")
    rows = {r["salesman"]: r for r in t["rows"]}
    one, two, counter = rows["Rep One"], rows["Rep Two"], rows["Counter B2C"]
    assert [r["salesman"] for r in t["rows"]] == ["Rep One", "Rep Two", "Counter B2C"], "by ex-VAT sales"
    assert one["tier_reached"] == 3 and one["next_tier"] is None and one["named_share_pct"] == 80.0
    assert one["marketplace_orders"] == 2 and one["marketplace_bhd"] == 32.5, "orders 1 and 3; the test order is out"
    assert one["last_invoice"] == "2026-09-24" and one["business_days_since_invoice"] == 0
    assert two["tier_reached"] == 0 and two["next_tier"] == 1 and two["gap_to_next_bhd"] == 400.0
    assert two["marketplace_orders"] == 3 and two["marketplace_bhd"] == 14.2
    assert two["business_days_since_invoice"] == 10 and two["named_share_pct"] == 100.0
    assert counter["no_target"] is True and counter["marketplace_orders"] == 0
    assert t["totals"] == {"net_bhd": 420.0, "marketplace_orders": 5}
    assert "kickback" not in json.dumps(t).lower(), "the rep table shows tiers and gaps, not the money owed"


@test("customers: active 30/60/90 and new, dormant high-value, top-10 concentration, Cash Customer share")
def _():
    ov = _overview()
    act = _tile(ov, "customers.active")
    assert [c["value"] for c in act["chips"]] == [14, 15, 15] and act["new_30d"] == 1
    d = _tile(ov, "customers.dormant")
    assert d["value"] == 2 and [x["account"] for x in d["items"]] == ["Shop C", "Shop D"]
    assert d["items"][0]["days"] == 54 and d["bhd"] == 1100.0, "a dormant account with no 12-month sales is not listed"
    c = _tile(ov, "customers.concentration")
    assert c["value"] == 97.4 and c["top10_bhd"] == 2210.0 and c["named_bhd"] == 2270.0
    cash = _tile(ov, "customers.cash")
    assert cash["invoices_pct"] == 33.3 and cash["sales_pct"] == 8.6 and (cash["cash_invoices"], cash["invoices"]) == (15, 45)


@test("products: movers both ways, sold out with 60-day demand, stock shape at cost")
def _():
    ov = _overview()
    mv = _tile(ov, "products.movers")
    assert [x["item"] for x in mv["rising"]] == ["Item Up"] and mv["rising"][0]["delta_bhd"] == 80.0
    assert [x["item"] for x in mv["falling"]] == ["Item Down", "Item Gone"]
    so = _tile(ov, "products.sold_out")
    assert so["value"] == 1 and so["bhd"] == 16.0 and so["items"][0]["item"] == "Item Gone"
    sh = _tile(ov, "products.stock_shape")
    assert sh["value"] == 730.0 and sh["items_held"] == 4 and sh["items_uncosted"] == 1
    chips = {c["label"]: c for c in sh["chips"]}
    assert chips["Over a year of cover"]["value"] == 600.0 and chips["Over a year of cover"]["share_pct"] == 82.2
    assert chips["No sale in 90 days"]["value"] == 30.0 and chips["No sale in 90 days"]["items"] == 1


@test("profitability: official GM on Focus COGS, landed GM with its coverage, the below-cost list")
def _():
    ov = _overview()
    off = _tile(ov, "profit.official")
    assert off["value"] == 37.6 and off["below_cost"] == 1 and "Focus COGS" in off["basis"]
    land = _tile(ov, "profit.landed")
    assert land["value"] == 34.0 and land["coverage_pct"] == 80.0 and "MRN" in land["basis"]
    below = _tile(ov, "profit.below_cost")
    assert below["value"] == 1 and below["items"][0] == {"item": "Item Down", "gp_bhd": -5.5, "margin_pct": -12.5}


@test("receivables: Focus's Grand Total when stored (gap noted), the row sum with a note otherwise; over 90; top overdue")
def _():
    ov = _overview()
    tot = _tile(ov, "ar.total")
    assert tot["value"] == 800.0 and tot["source"] == "focus_total" and tot["gap_bhd"] == 50.0 and "credits" in tot["note"]
    assert _tile(ov, "ar.over90")["value"] == 60.0
    top = _tile(ov, "ar.top_overdue")
    assert [x["account"] for x in top["items"]] == ["Acct Alpha", "Acct Beta", "Acct Counter"] and top["bhd"] == 600.0
    nr = _tile(ov, "ar.no_receipt")
    assert nr["value"] == 1 and nr["bhd"] == 500.0
    ov = _overview(FakeRPC(ar_totals=False))
    tot = _tile(ov, "ar.total")
    assert tot["value"] == 850.0 and tot["source"] == "rows" and "Grand Total is not stored" in tot["note"]
    assert _tile(ov, "ar.over90")["value"] == 58.8          # 500 ÷ 850 from the rows
    assert ov["unavailable"] == [], "a missing totals table is not an error"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. needs attention
# ═══════════════════════════════════════════════════════════════════════════════

@test("attention: every exception, alerts first then by money")
def _():
    ov = _overview()
    items = ov["modules"][0]["items"]
    assert [i["key"] for i in items] == ["unowned_ar", "orders_waiting", "below_cost", "sold_out",
                                        "delivered_no_invoice", "rep_silence", "invoiced_open"], [i["key"] for i in items]
    by = _items(ov)
    un = by["unowned_ar"]
    assert un["bhd"] == 300.0 and un["count"] == 1 and un["rows"] == [{"label": "Rep Gone", "sub": "1 account", "bhd": 300.0}]
    assert "60.0 % of the over-90 book" in un["detail"], "the B2C counter's account and the active rep's are owned"
    w = by["orders_waiting"]
    assert w["count"] == 1 and w["rows"][0]["to"] == "/shop-orders?open=3" and w["drill"]["to"] == "/shop-orders"
    assert by["delivered_no_invoice"]["count"] == 1 and by["delivered_no_invoice"]["bhd"] == 7.2
    assert by["invoiced_open"]["count"] == 1
    rs = by["rep_silence"]
    assert rs["count"] == 2 and [r["label"] for r in rs["rows"]] == ["Rep Idle", "Rep Two"], rs
    assert "Rep Old" not in json.dumps(rs) and "No Focus" not in json.dumps(rs)
    so = by["sold_out"]
    assert so["count"] == 1 and so["rows"][0]["to"] == "/inventory?q=Item%20Gone"
    assert by["below_cost"]["count"] == 1
    assert ov["modules"][0]["all_clear"] is False
    from app import metrics
    assert metrics.attention(reader=_reader(FakeRPC()), use_cache=False)["items"] == items


@test("attention: stale Focus data is flagged; a quiet world is all clear")
def _():
    ov = _overview(FakeRPC(anchor={**ANCHOR, "focus_to": "2026-09-20"}))
    st = _items(ov)["stale_data"]
    assert "7 days ago" in st["detail"] and ov["freshness"]["stale"] is True and ov["freshness"]["focus_days_behind"] == 7
    quiet = FakeRPC(orders=[], match={"eligible": 0, "matched": 0, "unmatched_bhd": 0, "invoiced_open": 0, "last_order_at": None},
                    reps=[{"rep": f"{s['focus_name']} - Acc WH", "last_invoice": FOCUS_TO, "month_net_bhd": 1,
                           "month_named_bhd": 1} for s in SALESMEN if s["focus_name"]],
                    receivables=[r for r in RECEIVABLES if r["account"] != "Acct Alpha"], movers=[], below_cost=[],
                    margin_totals={**MARGIN_TOTALS, "below": 0})
    ov = _overview(quiet)
    assert ov["modules"][0]["items"] == [] and ov["modules"][0]["all_clear"] is True
    assert _tile(ov, "orders.match_rate")["value"] is None, "no delivered order old enough: no rate, not 0 %"


@test("a failing source costs its tiles only: the page still builds and names what is missing")
def _():
    ov = _overview(FakeRPC(fail={"movers": RuntimeError("statement timeout"), "receivables": RuntimeError("boom")}))
    assert ov["unavailable"] == ["movers", "receivables"]
    assert _tile(ov, "products.movers")["available"] is False and _tile(ov, "products.sold_out")["available"] is False
    assert _tile(ov, "ar.total")["available"] is False and _tile(ov, "ar.total")["note"]
    assert _tile(ov, "products.stock_shape")["available"] is True and _tile(ov, "sales.accessories")["value"] == 930.0
    assert "sold_out" not in _items(ov) and "unowned_ar" not in _items(ov)
    ov = _overview(FakeRPC(fail={"anchor": RuntimeError("down")}))
    assert _tile(ov, "sales.accessories")["available"] is False and ov["freshness"]["focus_to"] is None
    assert "stale_data" in _items(ov), "no Focus date at all is an exception too"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. before the r7b migration, and caching
# ═══════════════════════════════════════════════════════════════════════════════

@test("before the migration: the agent view answers, test orders cannot be told apart, no accepted / match rate")
def _():
    from app import metrics
    metrics.invalidate()
    fake = FakeRPC(view=False)
    ov = metrics.overview("mtd", reader=_reader(fake), use_cache=False)
    assert "orders_fallback" in fake.names() and ov["unavailable"] == []
    assert any("r7b migration" in n for n in ov["notes"])
    f = _tile(ov, "orders.funnel")
    assert f["stages"][0]["orders"] == 7, "the test order is counted: the older view cannot tell"
    assert "not yet told apart" in f["basis"]
    assert _tile(ov, "orders.accepted")["available"] is False and _tile(ov, "orders.match_rate")["value"] is None
    assert ov["freshness"]["match_rate"]["available"] is False and ov["freshness"]["marketplace_live"] is True
    assert "delivered_no_invoice" not in _items(ov) and "invoiced_open" not in _items(ov)
    team = {r["salesman"]: r for r in _tile(ov, "team.reps")["rows"]}
    assert team["Rep One"]["marketplace_orders"] == 3, "matched by the rep's name when the view has no id"
    # the miss is remembered: the next build goes straight to the agent view
    fake2 = FakeRPC(view=False)
    metrics.overview("mtd", reader=_reader(fake2), use_cache=False)
    assert "orders" not in fake2.names() and "orders_fallback" in fake2.names()
    metrics.invalidate()
    fake3 = FakeRPC(view=True)
    metrics.overview("mtd", reader=_reader(fake3), use_cache=False)
    assert "orders" in fake3.names() and "orders_fallback" not in fake3.names(), "an upload re-probes"


@test("caching: per period until the next upload; reports.invalidate_dashboard_cache clears it")
def _():
    from app import metrics, reports
    fake = FakeRPC()
    calls = []
    real_reader = metrics.Reader

    def reader_factory(*a, **k):
        calls.append(1)
        return real_reader(q=fake.q, qp=fake.qp, salesmen_fn=lambda: [dict(s) for s in SALESMEN], now=NOW)

    metrics.invalidate()
    with _Patched((metrics, "Reader", reader_factory)):
        a = metrics.overview("mtd")
        n = len(fake.calls)
        b = metrics.overview("mtd")
        assert a is b and len(fake.calls) == n, "a warm hit costs no read"
        metrics.overview("7d")
        assert len(fake.calls) > n, "each period has its own entry"
        n2 = len(fake.calls)
        metrics._live_cache.clear()                   # the live half expires after a minute
        metrics.overview("mtd")
        new = [c[0] for c in fake.calls[n2:]]
        assert set(new) <= {"orders", "match"} and "orders" in new, f"only the live half is re-read: {new}"
        reports.invalidate_dashboard_cache()
        assert not metrics._focus_cache and not metrics._live_cache
    # a build that missed a source (the database blinked) is kept a minute, not until the next upload
    fake.fail["movers"] = RuntimeError("statement timeout")
    with _Patched((metrics, "Reader", reader_factory)):
        metrics.overview("mtd")
        age = metrics.time.time() - metrics._focus_cache["mtd"][0]
        assert metrics.FOCUS_TTL_S - metrics.ERROR_TTL_S - 2 <= age <= metrics.FOCUS_TTL_S - metrics.ERROR_TTL_S + 2, age
    metrics.invalidate()


@test("freshness: the chip for every login; the match rate only for admin and management")
def _():
    from app import metrics
    metrics.invalidate()
    plain = metrics.freshness(False, reader=_reader(FakeRPC()), use_cache=False)
    assert plain["focus_to"] == FOCUS_TO and plain["focus_label"] == "24 Sep" and plain["focus_days_behind"] == 3
    assert plain["marketplace_live"] is True and plain["match_rate"] is None and plain["stale"] is False
    full = metrics.freshness(True, reader=_reader(FakeRPC()), use_cache=False)
    assert full["match_rate"] == {"available": True, "pct": 50.0, "matched": 1, "eligible": 2}
    before = metrics.freshness(True, reader=_reader(FakeRPC(view=False)), use_cache=False)
    assert before["match_rate"]["available"] is False
    down = metrics.freshness(False, reader=_reader(FakeRPC(fail={"last_order": RuntimeError("down")})), use_cache=False)
    assert down["marketplace_live"] is False
    metrics.invalidate()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. routes
# ═══════════════════════════════════════════════════════════════════════════════

def _client_as(rows: dict):
    """TestClient whose bearer token text is the user's local part; user_roles rows from `rows`."""
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth, database
    database.invalidate_user_cache()
    p = _Patched((auth, "_decode_token", lambda tok: {"sub": "uid-" + tok, "email": tok + "@example.com"}),
                 (database, "_select_user_row", lambda email: rows.get(email)),
                 (m.limiter, "enabled", False))
    return TestClient(m.app), p


def _row(email, role):
    return {"email": email, "role": role, "features": [], "status": "active", "full_name": email.split("@")[0]}


ROWS = {f"{r}@example.com": _row(f"{r}@example.com", r) for r in ("admin", "management", "member", "salesman", "storekeeper")}


@test("routes: admin and management read the Command Centre; nobody else; a bad period is a 400")
def _():
    from app import metrics
    seen: list = []
    client, p = _client_as(ROWS)
    with p, _Patched((metrics, "overview", lambda period="mtd", **k: seen.append(period) or {"ok": period}),
                     (metrics, "attention", lambda **k: {"items": []})):
        for who in ("admin", "management"):
            r = client.get("/management/overview?period=7d", headers={"Authorization": f"Bearer {who}"})
            assert r.status_code == 200 and r.json() == {"ok": "7d"}, (who, r.text)
            assert client.get("/management/attention", headers={"Authorization": f"Bearer {who}"}).status_code == 200
        assert client.get("/management/overview", headers={"Authorization": "Bearer admin"}).json() == {"ok": "mtd"}
        for who in ("member", "salesman", "storekeeper"):
            for path in ("/management/overview", "/management/attention"):
                r = client.get(path, headers={"Authorization": f"Bearer {who}"})
                assert r.status_code == 403, (who, path, r.status_code)
        r = client.get("/management/overview?period=forever", headers={"Authorization": "Bearer admin"})
        assert r.status_code == 400 and "today, 7d, mtd, last_month, quarter" in r.json()["detail"]
        assert client.get("/management/overview").status_code == 401
        # management is read-only: a write to the same prefix is refused by the central gate
        r = client.post("/management/overview", headers={"Authorization": "Bearer management"})
        assert r.status_code in (403, 405), r.status_code
    assert seen == ["7d", "7d", "mtd"]


@test("routes: /freshness answers every login; only admin and management get the match rate")
def _():
    from app import metrics
    got: list = []
    client, p = _client_as(ROWS)
    with p, _Patched((metrics, "freshness", lambda show_match, **k: got.append(show_match) or {"m": show_match})):
        for who in ("admin", "management", "member", "storekeeper", "salesman"):
            r = client.get("/freshness", headers={"Authorization": f"Bearer {who}"})
            assert r.status_code == 200, (who, r.text)
    assert got == [True, True, False, False, False]


@test("routes: end to end through the real metrics on the stand-in RPC")
def _():
    from app import metrics
    fake = FakeRPC()
    metrics.invalidate()
    client, p = _client_as(ROWS)
    with p, _Patched((metrics, "exec_sql", fake.q), (metrics, "exec_sql_params", fake.qp),
                     (metrics, "_default_salesmen", lambda: [dict(s) for s in SALESMEN])):
        r = client.get("/management/overview?period=quarter", headers={"Authorization": "Bearer management"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["period"]["key"] == "quarter" and body["modules"][0]["key"] == "attention"
        assert _tile(body, "sales.accessories")["available"] is True
        r = client.get("/management/attention", headers={"Authorization": "Bearer admin"})
        assert r.status_code == 200 and isinstance(r.json()["items"], list)
    metrics.invalidate()


@test("routes: the route table reads a plain user dependency (the role check sits in the route)")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    from app import auth
    found = {}
    for r in m.app.routes:
        if isinstance(r, APIRoute) and r.path in ("/management/overview", "/management/attention", "/freshness"):
            found[r.path] = [d.call for d in r.dependant.dependencies]
            assert r.methods == {"GET"}, (r.path, r.methods)
    assert set(found) == {"/management/overview", "/management/attention", "/freshness"}
    assert all(calls == [auth.get_current_user] for calls in found.values()), found
    assert not auth.read_only_refuses("GET", "/management/overview"), "management may read it"
    from app import metrics
    assert metrics.COMMAND_ROLES == {"admin", "management"}


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the migration pair, as text
# ═══════════════════════════════════════════════════════════════════════════════

def _sql_code(path: Path) -> str:
    """The SQL without -- comments (the prose may name what the code must never do)."""
    return "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


@test("migration: one additive view, yq_readonly only, nothing personal, no CASCADE, no security_invoker")
def _():
    sql = _sql_code(MIGRATION).lower()
    assert "create or replace view v_command_orders as" in sql
    assert re.search(r"revoke all on v_command_orders from anon, authenticated;", sql)
    assert re.search(r"^grant select on v_command_orders to yq_readonly;", sql, re.M), "a plain GRANT, not inside a DO block"
    assert "cascade" not in sql and "drop " not in sql and "delete " not in sql and "update " not in sql
    assert "insert " not in sql and "truncate" not in sql and "alter table" not in sql
    assert "with (security_invoker" not in sql and "security_invoker = " not in sql and "security_invoker=" not in sql
    body = sql.split("create or replace view v_command_orders as", 1)[1].split(";", 1)[0]
    for col in ("customer_name", "customer_phone", "customer_email", "customer_shop", "customer_area", "o.token",
                "ip_hash", "o.note", "device_id"):
        assert col not in body, f"{col} must not be in the view"
    assert "o.focus_invoice_no" in body and "is not null) as has_invoice_no" in body, "only a flag for the invoice"
    assert "state = 'confirmed'" in body and "group by order_id" in body
    assert len(re.findall(r"^\s*grant\s", sql, re.M)) == 1, "one GRANT, to yq_readonly, nothing else"
    assert "r7_focus_links_migration.sql first" in MIGRATION.read_text(encoding="utf-8")
    raw = MIGRATION.read_text(encoding="utf-8")
    assert raw.count("do $$") == 2 and "raise exception" in raw


@test("migration reverse: drops the view only, no CASCADE, and checks itself")
def _():
    sql = _sql_code(REVERSE).lower()
    assert "drop view if exists v_command_orders;" in sql and "cascade" not in sql
    assert sql.count("drop ") == 1 and "delete " not in sql and "table" not in sql.replace("to_regclass", "")
    assert "raise exception" in sql


@test("the metric SQL only reads views yq_readonly is granted (plus the new one)")
def _():
    from app import metrics as m
    granted = {"v_sales", "stock_balance", "v_receivables", "v_product_margin", "app_settings", "v_stock_health",
               "v_product_economics", "ar_ageing_totals", "v_command_orders", "v_shop_orders_agent"}
    sqls = [m.ANCHOR_SQL, m.SALES_SQL, m.TREND_SQL, m.REPS_SQL, m.CUSTOMERS_SQL, m.MOVERS_SQL, m.STOCK_SQL,
            m.LANDED_SQL, m.RECEIVABLES_SQL, m.AR_TOTALS_SQL, m.AR_OWNER_SQL, m.ORDERS_SQL, m.ORDERS_FALLBACK_SQL,
            m.MATCH_SQL, m.LAST_ORDER_SQL]
    for s in sqls:
        rels = set(re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)\b(?!\.)", s, re.I))
        rels -= {"jsonb_to_recordset"}
        assert rels <= granted, (rels - granted, s[:80])
        assert ";" not in s, "the RPC wraps each statement: no second statement"
        assert not re.search(r"\b(insert|update|delete|drop|alter|grant|create)\b", s, re.I), s[:80]
    assert "orders" not in {r.lower() for s in sqls for r in re.findall(r"\bfrom\s+([a-z_]+)", s, re.I)}


# ═══════════════════════════════════════════════════════════════════════════════
# 7. local Postgres replay (SKIPs cleanly without a scratch cluster)
# ═══════════════════════════════════════════════════════════════════════════════

LOCAL_DSN = os.environ.get("YQ_LOCAL_PG_R7B", os.environ.get("YQ_LOCAL_PG", "postgresql://postgres@localhost:55432/postgres"))

MINIMAL_SCHEMA = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then create role yq_readonly nologin; end if;
end $$;
grant anon, authenticated to current_user;
create table shop_orders (id bigint primary key, order_no text not null, token text, status text not null,
  source text, placed_by text, salesman_id bigint, salesman_name text, customer_id bigint, customer_name text,
  customer_phone text, customer_shop text, customer_area text, customer_email text, note text, ip_hash text, ua text,
  device_id text, is_test boolean not null default false, created_at timestamptz not null default now(),
  assigned_at timestamptz, confirmed_at timestamptz, delivered_at timestamptz, cancelled_at timestamptz,
  total_bhd numeric(12,3) not null default 0, total_confirmed_bhd numeric(12,3), focus_invoice_no text);
alter table shop_orders enable row level security;
create table shop_order_lines (id bigint generated by default as identity primary key, order_id bigint not null
  references shop_orders(id), qty integer not null, qty_confirmed integer);
create table shop_order_focus_links (id bigint generated by default as identity primary key,
  order_id bigint not null references shop_orders(id), invoice_key text not null, state text not null default 'suggested',
  created_at timestamptz not null default now(), decided_at timestamptz);
"""


def _local():
    try:
        import psycopg
    except ImportError:
        return None
    try:
        return psycopg.connect(LOCAL_DSN, connect_timeout=2)
    except Exception:  # noqa: BLE001
        return None


@test("local: the migration on a scratch Postgres — one row per order, units, links, flags, grants, idempotent, reverse")
def _():
    conn = _local()
    if conn is None:
        print("  SKIP  (no scratch Postgres at YQ_LOCAL_PG_R7B / YQ_LOCAL_PG)")
        return
    from app import metrics as m
    try:
        cur = conn.cursor()
        cur.execute("select to_regclass('public.shop_orders') is not null")
        if cur.fetchone()[0]:
            print("  SKIP  (the scratch database already has shop_orders: point YQ_LOCAL_PG_R7B at an empty one)")
            return
        cur.execute(MINIMAL_SCHEMA)
        cur.execute("""
            insert into shop_orders (id, order_no, token, status, source, salesman_id, salesman_name, customer_id,
                customer_name, customer_phone, is_test, created_at, confirmed_at, delivered_at, total_bhd,
                total_confirmed_bhd, focus_invoice_no) values
            (1, 'YQ-T-1', 't1', 'delivered', 'market', 1, 'Rep One', 10, 'Synthetic', '97300000000', false,
             '2026-09-21 10:00+03', '2026-09-21 12:00+03', '2026-09-22 10:00+03', 12.5, 12.5, null),
            (2, 'YQ-T-2', 't2', 'delivered', 'market', 2, 'Rep Two', 11, 'Synthetic', '97300000000', false,
             '2026-09-22 10:00+03', '2026-09-22 16:00+03', '2026-09-23 09:00+03', 9.0, 7.2, '  '),
            (3, 'YQ-T-3', 't3', 'new', 'market', 1, 'Rep One', null, 'Synthetic', '97300000000', false,
             '2026-09-23 09:00+03', null, null, 20.0, null, null),
            (4, 'YQ-T-4', 't4', 'confirmed', 'salesman', 2, 'Rep Two', 12, 'Synthetic', '97300000000', false,
             '2026-09-24 09:00+03', '2026-09-24 11:00+03', null, 4.0, 4.0, 'SI-T-4'),
            (5, 'YQ-T-5', 't5', 'delivered', 'market', 1, 'Rep One', 10, 'Synthetic', '97300000000', true,
             '2026-09-21 11:00+03', '2026-09-21 11:30+03', '2026-09-21 12:00+03', 99.0, 99.0, null),
            (6, 'YQ-T-6', 't6', 'new', 'market', null, null, null, 'Synthetic', '97300000000', false,
             '2026-09-24 20:00+03', null, null, 3.0, null, null)""")
        cur.execute("""
            insert into shop_order_lines (order_id, qty, qty_confirmed) values
              (1, 4, 4), (1, 6, 6), (2, 5, 5), (2, 3, 1), (3, 5, null), (4, 4, 4), (5, 50, 50), (6, 1, null), (6, 1, null)""")
        cur.execute("""
            insert into shop_order_focus_links (order_id, invoice_key, state, decided_at) values
              (1, 'SI-T-1', 'confirmed', '2026-09-23 10:00+03'), (1, 'SI-T-1B', 'confirmed', '2026-09-24 10:00+03'),
              (2, 'SI-T-2', 'rejected', now()), (3, 'SI-T-3', 'suggested', null)""")
        sql = MIGRATION.read_text(encoding="utf-8")
        cur.execute(sql)
        cur.execute(sql)                                          # idempotent
        cur.execute("select id, units_ordered, units_confirmed, lines_n, has_invoice_no, focus_links_n, is_test, "
                    "has_customer from v_command_orders order by id")
        got = [tuple(r) for r in cur.fetchall()]
        assert got == [(1, 10, 10, 2, False, 2, False, True), (2, 8, 6, 2, False, 0, False, True),
                       (3, 5, None, 1, False, 0, False, False), (4, 4, 4, 1, True, 0, False, True),
                       (5, 50, 50, 1, False, 0, True, True), (6, 2, None, 2, False, 0, False, False)], got
        cur.execute("select has_table_privilege('yq_readonly', 'public.v_command_orders', 'SELECT'), "
                    "has_table_privilege('anon', 'public.v_command_orders', 'SELECT'), "
                    "has_table_privilege('authenticated', 'public.v_command_orders', 'SELECT')")
        assert cur.fetchone() == (True, False, False)
        # the Command Centre's own SQL, as the RPC runs it: wrapped, as yq_readonly
        cur.execute("set local role yq_readonly")
        for s, params in ((m.ORDERS_SQL, ["2026-09-01T00:00:00+03:00"]), (m.MATCH_SQL, ["2026-09-27T10:00:00+03:00"])):
            q = s
            for i in range(len(params), 0, -1):
                q = q.replace(f"${i}", "%s")
            cur.execute(f"select coalesce(json_agg(t), '[]'::json) from ({q}) t", params)
            rows = cur.fetchone()[0]
            if s is m.MATCH_SQL:
                assert rows[0]["eligible"] == 2 and rows[0]["matched"] == 1 and rows[0]["invoiced_open"] == 1, rows
                assert float(rows[0]["unmatched_bhd"]) == 7.2
            else:
                assert sorted(r["id"] for r in rows) == [1, 2, 3, 4, 5, 6]
        cur.execute("reset role")
        cur.execute(REVERSE.read_text(encoding="utf-8"))
        cur.execute(REVERSE.read_text(encoding="utf-8"))           # idempotent
        cur.execute("select to_regclass('public.v_command_orders')")
        assert cur.fetchone()[0] is None
        cur.execute("select count(*) from shop_orders")
        assert cur.fetchone()[0] == 6, "the reverse never touches an order"
        cur.execute(sql)                                          # re-apply after the reverse
        cur.execute("select count(*) from v_command_orders")
        assert cur.fetchone()[0] == 6
        # without the R7a links table the migration stops with a clear message instead of half-applying
        cur.execute("savepoint s")
        cur.execute("drop view v_command_orders")
        cur.execute("drop table shop_order_focus_links")
        try:
            cur.execute(sql)
            raise AssertionError("the migration must refuse without shop_order_focus_links")
        except AssertionError:
            raise
        except Exception as e:  # noqa: BLE001
            assert "r7_focus_links_migration.sql first" in str(e), e
        cur.execute("rollback to savepoint s")
    finally:
        conn.rollback()
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 8. the web (source checks: the build itself runs in CI's web job)
# ═══════════════════════════════════════════════════════════════════════════════

def _web(path: str) -> str:
    return (WEB / path).read_text(encoding="utf-8")


@test("web nav: §33 groups in order, Command Centre first; the dormant AI tools admin-only under 'AI tools / Archive'")
def _():
    nav = _web("lib/nav.ts")
    body = nav.split("export const NAV: NavItem[] = [", 1)[1].split("\n]\n", 1)[0]
    sections = []
    for s in re.findall(r"section: '([^']+)'", body):
        if s not in sections:
            sections.append(s)
    assert sections == ["Command Centre", "Customer orders", "Order for a shop", "Catalog", "Inventory & stock moves",
                        "Purchasing", "Profitability", "Receivables", "Team performance", "Market Intel", "Offers & campaigns",
                        "Reports", "Admin", "AI tools / Archive"], sections
    first = re.search(r"\{[^}]*\}", body).group(0)
    assert "to: '/command'" in first and "label: 'Command Centre'" in first
    for label in ("Live Feed", "AI Agents", "Leads", "Marketing", "Coach"):
        line = next(ln for ln in body.splitlines() if f"label: '{label}'" in ln)
        assert "section: 'AI tools / Archive'" in line and "roles: ['admin']" in line, line


@test("web nav: management sees its eight pages (+ Market Intel when granted) and lands on the Command Centre")
def _():
    nav = _web("lib/nav.ts")
    block = nav.split("export const MANAGEMENT_NAV", 1)[1].split("]\n", 1)[0]
    assert re.findall(r"to: '([^']+)'", block) == ["/command", "/shop-orders", "/shop-analytics", "/command/team", "/command/customers",
                                                   "/inventory", "/margins", "/receivables", "/market-intel"]
    assert re.findall(r"label: '([^']+)'", block) == ["Command Centre", "Customer orders", "Shop analytics", "Team", "Customers",
                                                      "Products & stock", "Profitability", "Receivables", "Market Intel"]
    # Market Intel is feature-gated: management sees it only when an admin grants the page (R7b market intel)
    assert "feature: 'Market Intel'" in block.split("'/market-intel'", 1)[1].split("}", 1)[0]
    # R7d Shop analytics: read-only, behind the same grant as Customer orders (the API lets management read it)
    assert "feature: 'Shop Orders'" in block.split("'/shop-analytics'", 1)[1].split("}", 1)[0]
    assert "if (isManagement(me)) return '/command'" in nav
    app = _web("App.tsx")
    assert 'path="command"' in app and 'path="command/team"' in app and 'path="command/customers"' in app
    assert "isManagement(me)" in app, "management's home redirects to the Command Centre"


@test("web shell: the header chip replaces the rotating quotes and the always-green Live pill")
def _():
    shell = _web("components/AppShell.tsx")
    assert "HEADER_QUOTES" not in shell and "HeaderMotivator" not in shell
    assert "> Live\n" not in shell and "bg-success\" /> Live" not in shell
    assert "FreshnessChip" in shell and "'/freshness'" in _web("components/FreshnessChip.tsx")
    chip = _web("components/FreshnessChip.tsx")
    assert "Focus data to" in chip and "Marketplace live" in chip and "Invoice match" in chip


@test("web page: the Command Centre reads the overview, labels every tile with its basis, Needs attention first")
def _():
    page = _web("pages/CommandCentre.tsx")
    assert "/management/overview?period=" in page and "t.basis" in page
    assert "#6D4091" in page, "the plum marketplace look, not the grey one"
    for s in ("today", "7d", "mtd", "last_month", "quarter"):
        assert s in _web("pages/CommandCentre.tsx")


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
