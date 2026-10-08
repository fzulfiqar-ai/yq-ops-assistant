"""R8 — the daily sales chart in the Management Command Centre (the "sales.daily" tile).

    python -m tests.test_r8_daily

Same lightweight runner as tests/test_r7b_command.py, and its stand-in for the read-only RPC (the
FakeRPC that answers every SQL app/metrics.py sends and records the parameters): the world here is
that one with the Focus data running to Tuesday 6 Oct 2026 and a handful of sale days of its own.
Nothing reaches a database or the network (CI: SUPABASE_URL=https://ci.invalid). All data is
synthetic — made-up amounts on real calendar days.

Covered:
  * the series: one point per CALENDAR day for the 30 days ending on the data date, oldest first, a
    day with no sale (a weekend, a quiet Thursday) a 0 and never a gap; a row outside the window
    never enters it; across a year end;
  * Friday and Saturday are the weekend (2 and 3 Oct 2026), Sunday to Thursday business days;
  * only the data date is the latest day and the tile's value is that day's — also when the data
    date is a Saturday, or a day with no Accessories sale of its own;
  * the total, the business days in the window, the average per business day (the month pace's
    rule: every sale of the window, weekend sales too, over its business days — so it compares
    with the target for one business day; to the fils half-up) and the best day (a tie to the
    later day, none when nothing sold);
  * a failed read costs this tile only (unavailable, with a note) and every other tile is what a
    healthy build makes; no data date, no tile and no read;
  * the SQL constant: division = 'Accessories', NOT is_giveaway, ex-VAT, the 30 days to $1, one
    read-only statement over v_sales; sent once per build with the data date, whatever the period,
    and kept with the Focus half (a live refresh does not re-read it, an upload does);
  * the payload: the tile sits in Sales after the 13-week trend, nothing else changed shape, every
    Command Centre login reads it (sales carry no cost);
  * the target line's rule: the monthly target over the month's business days (October 2026: 21);
  * the web: one "Accessories sales" card with a Daily / Weekly control, Daily first, the choice
    kept in the browser, both views 210 px, the weekly chart alone without a daily tile; the daily
    chart; the Details tile; the pure helpers in plain node (web/scripts/numbers_ui_test.mjs).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")

# The borrowed suite re-wraps sys.stdout at import; keep both wrappers alive so the earlier one is
# never garbage-collected (which would close the shared buffer).
_KEEP: list = [sys.stdout, sys.stderr]
from tests import test_r7b_command as C  # noqa: E402
_KEEP += [sys.stdout, sys.stderr]

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── the synthetic world ─────────────────────────────────────────────────────────
# Wednesday 7 Oct 2026, 10:00 in Bahrain; the Focus data runs to Tuesday 6 Oct, so the 30 days are
# Monday 7 Sep to Tuesday 6 Oct: 22 business days and four weekends (the last one 2 and 3 Oct).

NOW = datetime(2026, 10, 7, 7, 0, tzinfo=timezone.utc)
FOCUS_TO = "2026-10-06"
FIRST_DAY = date(2026, 9, 7)
ANCHOR = {**C.ANCHOR, "focus_to": FOCUS_TO, "stock_as_of": FOCUS_TO, "ar_as_of": FOCUS_TO, "margin_report_date": FOCUS_TO}

# the days that sold, as DAILY_SQL answers them (a quiet day has no row)
DAILY = [
    {"day": "2026-09-07", "net_bhd": 40, "invoices": 3},          # the first day of the window
    {"day": "2026-09-08", "net_bhd": 100.5, "invoices": 2},
    {"day": "2026-09-14", "net_bhd": "250.250", "invoices": 5},   # a numeric may arrive as text
    {"day": "2026-09-29", "net_bhd": 900, "invoices": 20},        # the best day
    {"day": "2026-09-30", "net_bhd": 600.125, "invoices": 12},
    {"day": "2026-10-01", "net_bhd": 7.5, "invoices": 1},
    {"day": "2026-10-02", "net_bhd": 120, "invoices": 4},         # Friday: a weekend sale
    {"day": "2026-10-03", "net_bhd": 80, "invoices": 2},          # Saturday: a weekend sale
    {"day": "2026-10-04", "net_bhd": 300, "invoices": 9},
    {"day": "2026-10-06", "net_bhd": 110, "invoices": 7},         # the data date
]
TOTAL = 2508.375                    # every row above
WEEKEND_SALES = 200.0               # 2 and 3 Oct
BUSINESS_DAYS = 22                  # Sunday to Thursday days from 7 Sep to 6 Oct
# the tile's basis line with the data to 6 Oct: what the days are, and how the average is made
BASIS = ("Accessories · ex-VAT · giveaways out · each of the 30 days to 6 Oct · Friday and Saturday are the weekend · "
         "the average is all 30 days' sales, weekend sales too, divided by the business days (Sun–Thu) in them: the "
         "month pace's rule")


def _fake(**over) -> C.FakeRPC:
    return C.FakeRPC(**{"anchor": ANCHOR, "daily": DAILY, **over})


def _ov(fake: C.FakeRPC | None = None, period: str = "mtd", now: datetime = NOW) -> dict:
    return C._overview(fake or _fake(), period, now)


def _daily(fake: C.FakeRPC | None = None, **kw) -> dict:
    return C._tile(_ov(fake, **kw), "sales.daily")


def _by(tile: dict) -> dict:
    return {p["day"]: p for p in tile["series"]}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the series
# ═══════════════════════════════════════════════════════════════════════════════

@test("series: one point per calendar day for the 30 days to the data date, oldest first; a day with no sale is a 0, never a gap")
def _():
    t = _daily()
    s = t["series"]
    assert len(s) == 30 == t["days"]
    assert [p["day"] for p in s] == [(FIRST_DAY + timedelta(days=i)).isoformat() for i in range(30)], "every day, in order"
    assert s[0] == {"day": "2026-09-07", "label": "7 Sep", "weekday": "Mon", "value": 40.0, "invoices": 3,
                    "business_day": True, "latest": False}
    by = _by(t)
    assert by["2026-09-10"] == {"day": "2026-09-10", "label": "10 Sep", "weekday": "Thu", "value": 0.0, "invoices": 0,
                                "business_day": True, "latest": False}, "a quiet Thursday is there, at 0"
    assert by["2026-09-12"]["value"] == 0.0 and by["2026-09-12"]["invoices"] == 0, "so is a Saturday with no sale"
    assert [p["day"] for p in s if p["value"] > 0] == [r["day"] for r in DAILY], "the ten days that sold, nothing invented"
    assert (by["2026-09-14"]["value"], by["2026-09-14"]["invoices"]) == (250.25, 5), "text or number, the same amount"
    assert (by["2026-09-30"]["label"], by["2026-10-01"]["label"]) == ("30 Sep", "1 Oct"), "labels as the basis lines write days"
    json.dumps(t)                                     # floats, ints, strings and booleans only


@test("series: a row outside the 30 days never enters it; the window crosses a year end like any other month")
def _():
    stray = DAILY + [{"day": "2026-09-06", "net_bhd": 999, "invoices": 9}, {"day": "2026-10-07", "net_bhd": 888, "invoices": 8}]
    t = _daily(_fake(daily=stray))
    assert len(t["series"]) == 30 and t["total"] == TOTAL and "2026-09-06" not in _by(t) and "2026-10-07" not in _by(t)
    assert t["best"]["value"] == 900.0, "nor the best day"
    t = _daily(_fake(anchor={**ANCHOR, "focus_to": "2026-01-05"}, daily=[{"day": "2025-12-31", "net_bhd": 5, "invoices": 1}]),
               now=datetime(2026, 1, 6, 7, 0, tzinfo=timezone.utc))
    s = t["series"]
    assert (s[0]["day"], s[0]["label"], s[-1]["day"], s[-1]["label"]) == ("2025-12-07", "7 Dec", "2026-01-05", "5 Jan")
    assert [p["day"] for p in s] == sorted(p["day"] for p in s) and len({p["day"] for p in s}) == 30
    assert t["best"] == {"day": "2025-12-31", "label": "31 Dec", "value": 5.0} and "5 Jan" in t["basis"]


@test("weekend: Friday and Saturday are the weekend (2 and 3 Oct 2026); Sunday to Thursday are business days")
def _():
    assert date(2026, 10, 2).weekday() == 4 and date(2026, 10, 3).weekday() == 5      # Fri, Sat
    t = _daily()
    by = _by(t)
    assert (by["2026-10-02"]["weekday"], by["2026-10-02"]["business_day"]) == ("Fri", False)
    assert (by["2026-10-03"]["weekday"], by["2026-10-03"]["business_day"]) == ("Sat", False)
    assert (by["2026-10-02"]["value"], by["2026-10-03"]["value"]) == (120.0, 80.0), "a weekend sale is still shown"
    assert (by["2026-10-01"]["weekday"], by["2026-10-01"]["business_day"]) == ("Thu", True)
    assert (by["2026-10-04"]["weekday"], by["2026-10-04"]["business_day"]) == ("Sun", True)
    assert [p["day"] for p in t["series"] if not p["business_day"]] == [
        "2026-09-11", "2026-09-12", "2026-09-18", "2026-09-19", "2026-09-25", "2026-09-26", "2026-10-02", "2026-10-03"]
    for p in t["series"]:
        assert p["business_day"] == (p["weekday"] not in ("Fri", "Sat")), p
    assert t["business_days"] == BUSINESS_DAYS == sum(1 for p in t["series"] if p["business_day"])
    assert "Friday and Saturday are the weekend" in t["basis"]


@test("latest: only the data date is the latest day, and the tile's value is that day's")
def _():
    t = _daily()
    s = t["series"]
    assert [p["day"] for p in s if p["latest"]] == [FOCUS_TO] and s[-1]["latest"] is True
    assert t["value"] == s[-1]["value"] == 110.0 and s[-1]["weekday"] == "Tue" and s[-1]["invoices"] == 7
    # the data date is a Saturday: the window follows it, and the latest day is a weekend day
    t = _daily(_fake(anchor={**ANCHOR, "focus_to": "2026-10-03"}), now=datetime(2026, 10, 4, 7, 0, tzinfo=timezone.utc))
    s = t["series"]
    assert (s[0]["day"], s[0]["weekday"], s[-1]["day"]) == ("2026-09-04", "Fri", "2026-10-03") and len(s) == 30
    assert s[-1] == {"day": "2026-10-03", "label": "3 Oct", "weekday": "Sat", "value": 80.0, "invoices": 2,
                     "business_day": False, "latest": True}
    assert t["value"] == 80.0 and sum(1 for p in s if p["latest"]) == 1
    assert t["business_days"] == 20 and "the 30 days to 3 Oct" in t["basis"]
    # the data date had no Accessories sale of its own (a SIM-only day): the latest day is a 0, still the latest
    t = _daily(_fake(anchor={**ANCHOR, "focus_to": "2026-10-05"}), now=datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc))
    assert t["series"][-1] == {"day": "2026-10-05", "label": "5 Oct", "weekday": "Mon", "value": 0.0, "invoices": 0,
                               "business_day": True, "latest": True}
    assert t["value"] == 0.0 and t["available"] is True


@test("figures: the total, the average per business day (the pace rule: weekend sales count, the divisor is business days) and the best day")
def _():
    from app import metrics as m
    t = _daily()
    assert t["total"] == TOTAL
    assert t["business_days"] == BUSINESS_DAYS
    # 2,508.375 ÷ 22 business days = 114.0170…: every sale of the 30 days over its business days, 12 of which sold nothing
    assert t["avg_business_day"] == 114.017 == round(TOTAL / BUSINESS_DAYS, 3), t["avg_business_day"]
    # the 200.000 sold on the weekend is in it: left out, the average read 104.926 beside a target line that
    # counts weekend sales (the monthly target ÷ the month's business days)
    assert t["avg_business_day"] != round((TOTAL - WEEKEND_SALES) / BUSINESS_DAYS, 3)
    assert abs(t["avg_business_day"] * t["business_days"] - t["total"]) < 0.011, "average × business days = the total shown beside it"
    # the same rule as the month pace (month_pace): a whole month's sales, weekend ones too, over its business days.
    # A window that IS September (data to 30 Sep, 22 business days): the two figures are one
    sept = [r for r in DAILY if r["day"] < "2026-10-01"] + [{"day": "2026-09-05", "net_bhd": 50, "invoices": 1}]   # a Saturday
    t9 = _daily(_fake(anchor={**ANCHOR, "focus_to": "2026-09-30"}, daily=sept), now=datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc))
    pace = m.month_pace(t9["total"], date(2026, 9, 30), 10000)
    assert (t9["series"][0]["day"], t9["business_days"], pace["business_days_done"], pace["business_days_total"]) == ("2026-09-01", 22, 22, 22)
    assert t9["avg_business_day"] == round(pace["projected_bhd"] / pace["business_days_total"], 3) == round(1940.875 / 22, 3)
    assert t["best"] == {"day": "2026-09-29", "label": "29 Sep", "value": 900.0}
    assert set(t["best"]) == {"day", "label", "value"}
    # to the fils, half-up: 0.011 over 22 business days is 0.0005 → 0.001
    t = _daily(_fake(daily=[{"day": FOCUS_TO, "net_bhd": "0.011", "invoices": 1}]))
    assert (t["total"], t["avg_business_day"], t["value"]) == (0.011, 0.001, 0.011), t
    # a weekend day can be the best day: its sales count in the average, the day itself is not in the divisor
    t = _daily(_fake(daily=[{"day": "2026-10-02", "net_bhd": 700, "invoices": 5}, {"day": "2026-09-08", "net_bhd": 440, "invoices": 2}]))
    assert t["best"] == {"day": "2026-10-02", "label": "2 Oct", "value": 700.0}
    assert (t["total"], t["avg_business_day"]) == (1140.0, 51.818), "1,140 ÷ 22: the Friday's 700 counts, as it does toward the month"
    # a tie goes to the later day
    t = _daily(_fake(daily=[{"day": "2026-09-08", "net_bhd": 500, "invoices": 1}, {"day": "2026-09-29", "net_bhd": 500, "invoices": 1}]))
    assert t["best"]["day"] == "2026-09-29"


@test("figures: nothing sold in the 30 days — zeros all the way, no best day, and the tile is still there")
def _():
    t = _daily(_fake(daily=[]))
    assert t["available"] is True and len(t["series"]) == 30
    assert all(p["value"] == 0.0 and p["invoices"] == 0 for p in t["series"])
    assert (t["value"], t["total"], t["avg_business_day"], t["best"]) == (0.0, 0.0, 0.0, None)
    assert t["business_days"] == BUSINESS_DAYS and t["series"][-1]["latest"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# 2. a failed read, the SQL, the load
# ═══════════════════════════════════════════════════════════════════════════════

@test("unavailable: a failed read costs this tile only — a note in its place, every other tile as a healthy build makes it")
def _():
    ok = _ov()
    bad = _ov(_fake(fail={"daily": RuntimeError("statement timeout")}))
    assert ok["unavailable"] == [] and bad["unavailable"] == ["daily"]
    t = C._tile(bad, "sales.daily")
    assert t["available"] is False and t["value"] is None and t["note"], t
    assert "series" not in t and "best" not in t and "total" not in t, "no figure on a tile that did not build"
    assert (t["label"], t["unit"]) == ("Daily sales", "series") and t["basis"] and "{" not in t["basis"]
    assert [m["key"] for m in ok["modules"]] == [m["key"] for m in bad["modules"]]
    for a, b in zip(ok["modules"], bad["modules"]):
        assert a.get("items") == b.get("items"), "Needs attention is the same"
        assert [x["key"] for x in a["tiles"]] == [x["key"] for x in b["tiles"]]
        for x, y in zip(a["tiles"], b["tiles"]):
            if x["key"] != "sales.daily":
                assert x == y, x["key"]
    assert C._tile(bad, "sales.trend")["available"] is True and C._tile(bad, "sales.accessories")["value"] == 930.0


@test("unavailable: no data date — no window to anchor, so no tile and no read")
def _():
    fake = _fake(fail={"anchor": RuntimeError("down")})
    ov = _ov(fake)
    t = C._tile(ov, "sales.daily")
    assert t["available"] is False and t["value"] is None and t["note"]
    assert "daily" not in fake.names(), "nothing to ask the database for"
    assert "no data" in t["basis"]


@test("SQL: Accessories only, giveaways out, ex-VAT, the 30 days to $1 — one read-only statement over v_sales")
def _():
    from app import metrics as m
    s = m.DAILY_SQL
    assert "v.division = 'Accessories'" in s, "SIM never counts"
    assert "NOT v.is_giveaway" in s
    assert "SUM(v.net_bhd)" in s and "gross" not in s.lower() and "total_amount" not in s, "ex-VAT: net_bhd"
    assert "v.sale_date <= $1::date" in s and "v.sale_date > $1::date - 30" in s, "the 30 days ending on $1"
    assert m.DAILY_DAYS == 30 and f"$1::date - {m.DAILY_DAYS} " in s, "the window and the tile's `days` are one constant"
    assert s.count("$1") == 2 and not re.search(r"\$[2-9]", s), "one parameter: the data date"
    assert "CURRENT_DATE" not in s.upper() and "now()" not in s.lower(), "anchored on the data date, never on the calendar"
    assert "v.sale_date::text AS day" in s and "COUNT(DISTINCT v.invoice_no) AS invoices" in s
    assert "ROUND(COALESCE(SUM(v.net_bhd), 0)::numeric, 3) AS net_bhd" in s, "summed in SQL, to the fils"
    assert "GROUP BY 1" in s
    rels = set(re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)\b(?!\.)", s, re.I))
    assert rels == {"v_sales"}, rels                 # a view yq_readonly is already granted: no migration
    assert ";" not in s and not re.search(r"\b(insert|update|delete|drop|alter|grant|create)\b", s, re.I)
    assert " AS t" not in s and " t " not in s, "the RPC wraps the statement in a subquery named t"


@test("load: read once per build under its own source with the data date as $1 — whatever the period")
def _():
    from app import metrics as m
    fake = _fake()
    _ov(fake)
    assert fake.names().count("daily") == 1 and fake.params("daily") == [FOCUS_TO]
    assert next(s for n, s, _p in fake.calls if n == "daily") == m.DAILY_SQL
    assert fake.params("trend") == [FOCUS_TO], "the weekly trend beside it is read as before"
    for period in ("today", "7d", "last_month", "quarter"):
        fake = _fake()
        t = C._tile(_ov(fake, period), "sales.daily")
        assert fake.params("daily") == [FOCUS_TO], period
        assert (t["series"][0]["day"], t["series"][-1]["day"], t["total"]) == ("2026-09-07", FOCUS_TO, TOTAL), period
        assert t["basis"] == BASIS


@test("caching: the daily read is kept with the Focus half — a live refresh re-reads the orders only, an upload re-reads it")
def _():
    from app import metrics, reports
    fake = _fake()
    real_reader = metrics.Reader

    def reader_factory(*a, **k):
        return real_reader(q=fake.q, qp=fake.qp, salesmen_fn=lambda: [dict(s) for s in C.SALESMEN], now=NOW)

    metrics.invalidate()
    with C._Patched((metrics, "Reader", reader_factory)):
        a = metrics.overview("mtd")
        n = len(fake.calls)
        metrics._live_cache.clear()                   # the live half expires after a minute
        b = metrics.overview("mtd")
        new = [c[0] for c in fake.calls[n:]]
        assert "orders" in new and "daily" not in new and "trend" not in new, new
        assert C._tile(b, "sales.daily") == C._tile(a, "sales.daily")
        reports.invalidate_dashboard_cache()          # what every upload through the API calls
        metrics.overview("mtd")
        assert fake.names().count("daily") == 2, "a new upload shows its day at once"
    metrics.invalidate()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the payload
# ═══════════════════════════════════════════════════════════════════════════════

@test("payload: the tile sits in Sales after the 13-week trend; a series tile with its basis; nothing else changed shape")
def _():
    from app import command_api, metrics as m
    ov = _ov()
    assert {"freshness", "generated_at", "modules", "notes", "period", "unavailable"} <= set(ov)
    assert [x["key"] for x in ov["modules"]] == ["attention", "sales", "orders", "team", "customers", "products",
                                                "profitability", "receivables"]
    sales = C._module(ov, "sales")
    assert [t["key"] for t in sales["tiles"]] == ["sales.accessories", "sales.pace", "sales.channels", "sales.sim",
                                                  "sales.trend", "sales.daily"]
    t = sales["tiles"][-1]
    assert (t["label"], t["unit"], t["drill"], t["available"]) == ("Daily sales", "series", None, True)
    assert t["basis"] == BASIS and "weekend sales too" in BASIS, "the basis says how the average is made"
    assert set(t) == {"key", "label", "unit", "basis", "drill", "available", "value", "series", "days", "total",
                      "business_days", "avg_business_day", "best"}
    assert set(t["series"][0]) == {"day", "label", "weekday", "value", "invoices", "business_day", "latest"}
    assert m.METRICS["sales.daily"].module == "sales" and "sales" not in m.LIVE_MODULES
    json.dumps(ov)                                    # no Decimal, date or datetime left in the payload
    # the weekly trend is what it was: 13 Sunday weeks ending on the data date's week
    trend = C._tile(ov, "sales.trend")
    assert len(trend["series"]) == 13 and set(trend["series"][0]) == {"week_start", "label", "value", "partial"}
    assert trend["series"][-1]["week_start"] == "2026-10-04" and trend["series"][-1]["partial"] is True
    assert set(trend) == {"key", "label", "unit", "basis", "drill", "available", "value", "series"}
    # sales carry no cost: a login with neither 'Margins' nor 'Receivables' reads the daily tile whole
    narrow = command_api.scope_overview(ov, frozenset())
    assert C._tile(narrow, "sales.daily") == t


@test("target line: the monthly target over the month's business days — October 2026 has 21, so BHD 10,000 is BHD 476 a day")
def _():
    from app import metrics as m
    assert m.business_days(date(2026, 10, 1), date(2026, 10, 31)) == 21
    pace = C._tile(_ov(), "sales.pace")
    assert (pace["target_bhd"], pace["business_days_total"], pace["month"]) == (10000.0, 21, "October 2026")
    assert round(pace["target_bhd"] / pace["business_days_total"]) == 476
    # no company target: the pace tile says so and the page draws no line
    pace = C._tile(_ov(_fake(anchor={**ANCHOR, "target_bhd": None})), "sales.pace")
    assert pace["target_bhd"] is None and pace["business_days_total"] == 21


# ═══════════════════════════════════════════════════════════════════════════════
# 4. the web (source checks; the build itself and the node half run in CI's web job)
# ═══════════════════════════════════════════════════════════════════════════════

def _web(rel: str) -> str:
    return (ROOT / "web" / "src" / rel).read_text(encoding="utf-8")


def _fn(page: str, name: str) -> str:
    return page.split(f"function {name}(", 1)[1].split("\n}\n", 1)[0]


@test("web card: one 'Accessories sales' card — a Daily / Weekly control, Daily first, the choice kept in the browser")
def _():
    page = _web("pages/CommandCentre.tsx")
    card = _fn(page, "SalesChartCard")
    assert "{both ? 'Accessories sales' : view === 'daily' ? 'Daily Accessories sales' : 'Weekly Accessories sales'}" in card
    assert ">Weekly Accessories sales</h2>" not in page and 'id="fs-weekly"' not in page, "the one-chart card is gone"
    # a group of two real buttons, the shown one pressed
    assert 'role="group"' in card and 'type="button"' in card and "aria-pressed={view === v.key}" in card
    views = page.split("const CHART_VIEWS", 1)[1].split("\n]\n", 1)[0]
    assert 0 < views.index("label: 'Daily'") < views.index("label: 'Weekly'")
    # Daily unless this browser chose Weekly; storage may be missing or blocked, the card still draws
    read = _fn(page, "readChartView")
    assert "=== 'weekly' ? 'weekly' : 'daily'" in read and read.index("try {") < read.index("localStorage.getItem")
    assert read.index("} catch {") < read.index("return 'daily'")
    assert "useState<ChartView>(readChartView)" in card
    assert card.index("try {") < card.index("localStorage.setItem(CHART_VIEW_KEY, v)") < card.index("} catch {")
    assert "const CHART_VIEW_KEY = 'yq-command-sales-chart'" in page
    # the three facts and the spoken summary are the tile's figures, worded by lib/dailySales.ts
    assert "dailyFacts(dayPoints(daily), daily, fmtBhd)" in card and "dailySummary(facts, span)" in card
    assert "ex-VAT · one bar a day · lighter bars are Friday and Saturday" in card
    assert "const span = daily?.days ?? dayPoints(daily).length" in card
    # the facts stay on the card under the weekly view too: the row names the days they cover itself, first,
    # under either view (the daily caption is hidden under Weekly, and a phone has no hover)
    facts = card.split("{daily && !!facts.length && (", 1)[1]
    assert "<dd>{windowText(dayPoints(daily), span)}:</dd>" in facts and '<dt className="sr-only">Days covered</dt>' in facts
    assert facts.index("windowText(") < facts.index("{facts.map((f) => (")
    assert "'invisible'" not in facts, "the facts row is never hidden: the same card height under both views"


@test("web card: no daily tile — the weekly chart alone, no control; switching views never moves the card")
def _():
    page = _web("pages/CommandCentre.tsx")
    card = _fn(page, "SalesChartCard")
    assert "daily && (picked === 'daily' || !trend) ? 'daily' : trend ? 'weekly' : null" in card
    assert "const both = !!daily && !!trend" in card and "{both && (" in card, "the control needs both charts"
    first = _fn(page, "FirstScreen")
    assert "const charted = (t?: Tile) => (t?.series?.length ? t : undefined)" in first
    assert "charted(findTile(ov, 'sales.trend'))" in first and "charted(findTile(ov, 'sales.daily'))" in first
    assert "<SalesChartCard daily={daily} trend={trend} pace={findTile(ov, 'sales.pace')}" in first
    # findTile leaves out a tile that did not build or that this login may not read
    find = _fn(page, "findTile")
    assert "t.restricted || !t.available ? undefined : t" in find
    # the same height under either view: both charts 210 px, both captions in one grid cell, the facts stay put
    daily, weekly = _fn(page, "DailyChart"), _fn(page, "WeeklyChart")
    assert daily.count("h-[210px]") == 1 and weekly.count("h-[210px]") == 1
    assert card.count("col-start-1 row-start-1") == 2 and card.count("'invisible'") == 2
    assert "{daily && !!facts.length && (" in card
    # the weekly chart is the chart it was — its bars, its target line ...
    for needle in ('dataKey="label" interval="preserveStartEnd" minTickGap={10}',
                   "(pace.target_bhd / pace.business_days_total) * 5",
                   "fill={p.partial ? '#824FAB' : '#6D4091'} fillOpacity={p.partial ? 0.45 : 1}",
                   "radius={[4, 4, 0, 0]} maxBarSize={34}",
                   '<ReferenceLine y={weekly} stroke="#d97706" strokeDasharray="5 4" ifOverflow="extendDomain" />'):
        assert needle in weekly, needle
    assert "const WEEKLY_CAPTION = 'ex-VAT · Sunday–Saturday weeks · the lighter bar is this week so far'" in page


@test("web charts: the target's key sits above the plot on both views (never printed across a bar), and the y ticks never round 1,050 to 1.1k")
def _():
    page = _web("pages/CommandCentre.tsx")
    daily, weekly, key = _fn(page, "DailyChart"), _fn(page, "WeeklyChart"), _fn(page, "TargetKey")
    # one key for both charts: above the plot at the right, out of the pointer's way
    assert "pointer-events-none absolute right-4 top-0" in key and "border-dashed border-[#d97706]" in key and "{children}" in key
    assert "{target != null && <TargetKey>target per business day {fmtBhd0(target)}</TargetKey>}" in daily
    assert "{weekly != null && <TargetKey>weekly target {fmtBhd0(weekly)}</TargetKey>}" in weekly
    # a label on the weekly line itself was printed across the bar of a week that beat the target near the right edge
    assert "label={{" not in weekly and "insideBottomRight" not in weekly and "insideTopRight" not in weekly
    # both plots leave the key its room, and both wrappers are what the key is placed against
    assert "margin={{ top: 18, right: 8, left: 0, bottom: 0 }}" in weekly and "margin={{ top: 18, right: 16, left: 0, bottom: 0 }}" in daily
    assert '<div className="relative mt-2 h-[210px] min-w-0">' in weekly and '<div className="relative mt-2 h-[210px] min-w-0 ' in daily
    # the y axis of both charts: the helper in lib/dailySales.ts (three decimals at most, trailing zeros dropped)
    assert daily.count("tickFormatter={kTick}") == 1 and weekly.count("tickFormatter={kTick}") == 1
    assert "const kTick" not in page and ".toFixed(1)}k" not in page, "the page has no formatter of its own"
    lib = _web("lib/dailySales.ts")
    assert "export function kTick(v: number): string {" in lib and "Number((v / 1000).toFixed(3))" in lib


@test("web chart: plum bars, the weekend lighter, the latest day marked, the target for one business day from the pace tile")
def _():
    page = _web("pages/CommandCentre.tsx")
    chart = _fn(page, "DailyChart")
    assert 'fill="#6D4091" />' in chart and 'fill="#6D4091" fillOpacity={0.45}' in chart and "p.business_day" in chart
    assert "minPointSize={2}" in chart, "a day with no sale leaves a mark, so every weekend shows"
    assert "<ReferenceArea x1={latest.day} x2={latest.day}" in chart
    # the Dashboard's rule, only when a company target is set; the same dashed amber line as the weekly view
    assert "targetPerBusinessDay(pace?.target_bhd, pace?.business_days_total)" in chart
    assert chart.count("{target != null && ") == 2 and "target per business day {fmtBhd0(target)}" in chart
    assert '<ReferenceLine y={target} stroke="#d97706" strokeDasharray="5 4" ifOverflow="extendDomain" />' in chart
    # the tooltip: weekday, date and the invoices on one line, the exact amount on the next
    assert "`${p.weekday} ${p.label}${p.business_day ? '' : ' · weekend'} · ${invoicesText(p.invoices)}`" in chart
    assert "formatter={(v) => [fmtBhd(Number(v)), 'Accessories ex-VAT']}" in chart
    # read aloud: the summary on the chart itself; x labels sparse, picked for the plot's own width
    assert "aria-label={summary}" in chart and "tick={<DayTick days={days} />}" in chart
    # a click or a tap leaves no browser frame round the latest day's band; the keyboard ring stays
    assert "[&_.recharts-surface:focus:not(:focus-visible)]:outline-none [&_.recharts-surface_g:focus]:outline-none" in chart
    tick = _fn(page, "DayTick")
    assert "dayTicks(days, dayTickStep(plot?.width ?? 0, days.length))[day]" in tick and "usePlotArea()" in tick
    lib = _web("lib/dailySales.ts")
    assert not re.search(r"^\s*import\b", lib, re.M), "pure and import-free: the node test loads it on its own"
    assert "target / businessDays" in lib and "reduce(" not in lib, "it words the tile's figures; it adds nothing up"


@test("web details: sales.daily has a body — the 30-day strip and the last seven days — on a full-width tile")
def _():
    page = _web("pages/CommandCentre.tsx")
    assert "    case 'sales.daily':\n      return <DailyDetail t={t} />\n" in page
    detail = _fn(page, "DailyDetail")
    assert "<DayStrip days={days} />" in detail and "days.slice(-7).reverse()" in detail and "Last seven days" in detail
    assert "fmtBhd(t.total)" in detail and "invoicesText(p.invoices)" in detail
    # the average with its divisor beside the total: the line reconciles by itself (total ÷ business days)
    assert "f.key === 'average' && t.business_days ? ` over ${t.business_days} business days` : ''" in detail
    strip = _fn(page, "DayStrip")
    assert 'role="img"' in strip and "aria-label={`Daily Accessories sales, " in strip
    assert "p.business_day ? 'bg-[#6D4091]' : 'bg-[#6D4091]/45" in strip
    assert "const FULL = new Set(['sales.daily'])" in page and "full && 'md:col-span-2 xl:col-span-3'" in page
    assert "full={FULL.has(t.key)}" in page
    assert "const dayPoints = (t?: Tile): DayPoint[] =>" in page


@test("node: the chart's pure helpers and the card's heading (web/scripts/numbers_ui_test.mjs)")
def _():
    node = shutil.which("node")
    script = ROOT / "web" / "scripts" / "numbers_ui_test.mjs"
    assert script.exists() and "lib/dailySales.ts" in script.read_text(encoding="utf-8")
    if not node or not (ROOT / "web" / "node_modules" / "typescript").exists():
        print("        SKIP: node or web/node_modules not available (the web job runs it after npm ci)")
        return
    r = subprocess.run([node, str(script)], cwd=str(ROOT / "web"), capture_output=True, text=True, encoding="utf-8",
                       timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "node number-wording tests failed:\n" + out[-2000:]
    assert re.search(r"\b\d+ passed, 0 failed", out), out[-400:]
    assert "daily chart: x labels count back from the latest day" in out


@test("ci: this suite runs in the backend job; its node half in the web job")
def _():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    api, web = ci.split("\n  web:\n", 1)
    assert re.search(r"\n\s+run: python -m tests\.test_r8_daily\n", api), "its own step in the api job"
    step = api.split("python -m tests.test_r8_daily", 1)[0].rsplit("- name:", 1)[1]
    assert "SUPABASE_URL: https://ci.invalid" in step
    assert "node scripts/numbers_ui_test.mjs" in web


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
