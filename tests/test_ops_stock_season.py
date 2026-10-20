"""Ops engine: the season, a longer history and the lead time per supplier (scripts.ops.stock_season).

    python -m tests.test_ops_stock_season

Same lightweight runner as the other suites (no pytest). No database, no network, no .env:
SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import and
scripts.ops.db is imported first. Everything is invented: items, days, suppliers. A flow is a real
stockflow.Flow built from its main-store movements, so main_at() is the engine's own.

The world (the data date D is Tue 20 Oct 2026; the ledger's first day, 1 Jan 2025). Two items sell the
company's units; "a day" is the two together (A + B):
    1 Jan - 28 Feb 2025    40 a day   a very busy February, older than the twelve months a lift uses
    1 Mar - 30 Nov 2025     8 a day   (5 + 3)
    1 - 31 Dec 2025        12 a day   (8 + 4)
    Jan 2026                8 a day
    Feb 2026               16 a day   (10 + 6): a busy February
    Mar 2026                8 a day, except the 10th: net returns of 5 (-3 and -2), a day that counts as nought
    Apr 2026                nothing sold
    May 2026                8 a day
    Jun 2026                9 a day   (5 + 4)
    1 Jul - 20 Oct 2026     8 a day
The base window (the 90 days to D, 23 Jul - 20 Oct) holds 90 x 8 = 720 units: the base is 8 a day, so a lift is
the month's units a day over 8. The figures below are worked out by hand from those lines.

Covered:
  * the lifts: a busy February (16 / 8 = 2.00), a returns day that counts as nought (240 / 31 / 8 = 0.97), a month
    that sold nothing (0.01), a half that rounds up (9 / 8 = 1.125 -> 1.13), which year a month uses (the latest
    occurrence in the twelve months that end with the data date's month: for 20 Oct 2026 Nov and Dec are 2025's and
    Oct is this month up to D; for 31 Jan 2026 only Jan is 2026's), under 15 days of data = "no data", a partial first
    month divided by its days, a base window the ledger only partly covers, no sale in the window at all, the
    owner's config lifts and the ones that are ignored;
  * lift_of and season_days: the days AFTER the data date, across a month, a year and a leap February, against the
    day-by-day sum on random starts and lifts;
  * joins and suggested: today's rule (rate6 x days - position) when every lift is 1, a hand-worked order with a
    season, the round6 boundary, no rate; seasonal_cover against the same sums, its cap and its edges;
  * long_rate (an item out of stock 70 of the 90 days takes a longer window; None when the plain one is enough or
    when even a year is not), availability (the share of item-days, with a missing flow), and the lead time per
    supplier (measured at three matched invoices, else config, else the overall lead, else the setting; the "*" row;
    the newest ten; half up; names collapsed and aliased; the rows scripts.ops.stock.measure_lead_times gives);
  * the module is pure and can be imported before scripts.ops.stock (the engine will import it).
"""
from __future__ import annotations

import copy
import io
import os
import random
import re
import subprocess
import sys
import traceback
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

from scripts.ops import db  # noqa: E402,F401  (first, as every command does)
from scripts.ops import stock, stock_plan, stock_season as ss, stockflow  # noqa: E402

TESTS: list[tuple[str, object]] = []

D = date(2026, 10, 20)
FIRST = date(2025, 1, 1)


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def span(first: date, last: date):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def item(key: str) -> stock.Item:
    return stock.Item(ledger_name=f"ITEM-{key} Test {key}1", name=f"ITEM-{key} Test", code=f"{key}1")


def sell(it: stock.Item, first: date, last: date, units) -> None:
    for day in span(first, last):
        it.sales[day] = Decimal(units)


def company(*plan) -> list[stock.Item]:
    """Items A and B selling a and b units a day on every day of each (first, last, a, b) of the plan."""
    a, b = item("A"), item("B")
    for first, last, ua, ub in plan:
        sell(a, first, last, ua)
        sell(b, first, last, ub)
    return [a, b]


def two_year_world() -> list[stock.Item]:
    world = company(
        (date(2025, 1, 1), date(2025, 2, 28), 20, 20),
        (date(2025, 3, 1), date(2025, 11, 30), 5, 3),
        (date(2025, 12, 1), date(2025, 12, 31), 8, 4),
        (date(2026, 1, 1), date(2026, 1, 31), 5, 3),
        (date(2026, 2, 1), date(2026, 2, 28), 10, 6),
        (date(2026, 3, 1), date(2026, 3, 31), 5, 3),
        (date(2026, 5, 1), date(2026, 5, 31), 5, 3),
        (date(2026, 6, 1), date(2026, 6, 30), 5, 4),
        (date(2026, 7, 1), date(2026, 10, 20), 5, 3))
    world[0].sales[date(2026, 3, 10)], world[1].sales[date(2026, 3, 10)] = Decimal(-3), Decimal(-2)
    return world


def flow(*moves) -> stockflow.Flow:
    """A Flow from its main-store movements: (day, units in or out)."""
    return stockflow.Flow(main=[(day, Decimal(u)) for day, u in moves])


def lifts_of(rows: dict) -> dict:
    """The lift numbers of a measure_lifts answer as text, month by month (for a short assert)."""
    return {m: str(r["lift"]) for m, r in rows.items()}


# ── the constants and the module's place in the engine ─────────────────────────

@test("the constants the module shares with scripts.ops.stock are the same numbers")
def _():
    assert ss.RATE_DAYS == stock.RATE_DAYS == 90
    assert ss.RATE_MIN_DAYS == stock.RATE_MIN_DAYS == 30
    assert ss.LONG_MAX_DAYS == 365 and ss.LIFT_MIN_DAYS == 15 and ss.COVER_CAP == 730
    assert ss.AVAILABILITY_WARN == Decimal("0.80")
    assert stock.LEAD_MIN == 3 and stock.LEAD_SHOWN == 10          # the lead rule lead_by_supplier leans on
    assert ss.LIFT_BASES == ("last year", "no data", "config") and ss.LEAD_BASES == ("measured", "config", "overall", "setting")


@test("the module is pure and can be imported before scripts.ops.stock: nothing at its top level names stock")
def _():
    src = (ROOT / "scripts" / "ops" / "stock_season.py").read_text(encoding="utf-8")
    top = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert top and not [ln for ln in top if re.search(r"\bstock\b", ln)], top
    for word in ("import psycopg", "import httpx", "supabase", "subprocess", "yq_load_go", ".table(", "insert", "upsert",
                 "open(", "print(", "scripts.ops import db", "random"):
        assert word not in src, word
    code = ("import sys\n"
            "from scripts.ops import stock_season\n"
            "assert 'scripts.ops.stock' not in sys.modules, 'stock_season imported stock'\n"
            "from scripts.ops import stock\n"
            "assert stock_season.lead_by_supplier(None, None, None, None, 50)['*']['days'] == 50\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=dict(os.environ), capture_output=True,
                          text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-800:]


@test("nothing is changed: the items' sales, the lifts and the flows stand as they were")
def _():
    world = two_year_world()
    before = [dict(it.sales) for it in world]
    lifts = ss.measure_lifts(world, D, FIRST)
    frozen = copy.deepcopy(lifts)
    f = flow((date(2026, 5, 1), 50), (date(2026, 6, 10), -50), (date(2026, 10, 1), 40))
    ss.company_units(world, FIRST, D)
    ss.season_days(D, 90, lifts)
    ss.joins(10, Decimal("1.5"), D, 40, lifts)
    ss.suggested(10, Decimal("1.5"), D, 60, lifts)
    ss.seasonal_cover(10, Decimal("1.5"), D, lifts)
    ss.long_rate(world[0], f, D)
    assert [dict(it.sales) for it in world] == before and lifts == frozen
    assert ss.measure_lifts(iter(world), D, FIRST) == lifts                   # any iterable of items


# ── company units ──────────────────────────────────────────────────────────────

@test("company units: both end days count, a day is netted across the items and never counts below nought")
def _():
    a, b = item("A"), item("B")
    a.sales = {date(2026, 1, 1): Decimal(10), date(2026, 1, 2): Decimal(2), date(2026, 1, 3): Decimal(4),
               date(2026, 1, 4): Decimal(100)}
    b.sales = {date(2026, 1, 1): Decimal(-3), date(2026, 1, 2): Decimal(-5), date(2026, 1, 3): Decimal(1)}
    # 1 Jan: 10 - 3 = 7 (netted across the items, not 10); 2 Jan: 2 - 5 = -3 -> 0; 3 Jan: 4 + 1 = 5; 4 Jan: 100
    assert ss.company_units([a, b], date(2026, 1, 1), date(2026, 1, 2)) == 7
    assert ss.company_units([a, b], date(2026, 1, 1), date(2026, 1, 3)) == 12
    assert ss.company_units([a, b], date(2026, 1, 3), date(2026, 1, 4)) == 105               # both end days
    assert ss.company_units([a, b], date(2026, 1, 2), date(2026, 1, 2)) == 0                 # one day, below nought
    assert ss.company_units([a, b], date(2026, 1, 4), date(2026, 1, 1)) == 0                 # last before first
    assert ss.company_units([a, b], date(2025, 1, 1), date(2025, 12, 31)) == 0               # no line in the range
    assert ss.company_units([], date(2026, 1, 1), date(2026, 1, 4)) == 0
    assert isinstance(ss.company_units([], date(2026, 1, 1), date(2026, 1, 4)), Decimal)


# ── the lifts ──────────────────────────────────────────────────────────────────

@test("the lifts of a two-year history: a busy February, a returns day, a quiet April, a half that rounds up")
def _():
    world = two_year_world()
    assert D - timedelta(days=89) == date(2026, 7, 23)
    assert ss.company_units(world, date(2026, 7, 23), D) == 720                              # 90 days x 8: the base is 8
    lifts = ss.measure_lifts(world, D, FIRST)
    assert list(lifts) == list(range(1, 13))
    assert all(set(r) == {"lift", "basis", "days", "year"} for r in lifts.values())
    # every lift is month units / days / 8; days = days of the month between the first day and D
    assert lifts_of(lifts) == {
        1: "1.00",       # Jan 2026: 31 x 8 = 248 / 31 / 8
        2: "2.00",       # Feb 2026: 28 x 16 = 448 / 28 / 8 = 2.00 (Feb 2025's 40 a day is older than the twelve months)
        3: "0.97",       # Mar 2026: 30 x 8 = 240 (the returns day counts 0, not -5) / 31 / 8 = 0.9677 (-5 would give 0.95)
        4: "0.01",       # Apr 2026: nothing sold = 0.00, and no lift is ever nought: 0.01, still "last year"
        5: "1.00",       # May 2026
        6: "1.13",       # Jun 2026: 30 x 9 = 270 / 30 / 8 = 1.125 exactly: half up is 1.13 (half even would be 1.12)
        7: "1.00", 8: "1.00", 9: "1.00",
        10: "1.00",      # Oct 2026 up to D: 20 x 8 = 160 / 20 / 8
        11: "1.00",      # Nov 2025
        12: "1.50"}      # Dec 2025: 31 x 12 = 372 / 31 / 8 = 1.50
    assert {m: r["days"] for m, r in lifts.items()} == {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30,
                                                       10: 20, 11: 30, 12: 31}
    assert all(r["basis"] == "last year" for r in lifts.values())
    assert all(isinstance(r["lift"], Decimal) and r["lift"].as_tuple().exponent == -2 for r in lifts.values())


@test("which year a month uses: the latest occurrence in the twelve months that end with the data date's month")
def _():
    lifts = ss.measure_lifts(two_year_world(), D, FIRST)
    # d = 20 Oct 2026: Nov and Dec are those of 2025, Jan to Sep those of 2026, and Oct is this month up to D
    assert {m: r["year"] for m, r in lifts.items()} == dict.fromkeys(range(1, 11), 2026) | {11: 2025, 12: 2025}
    # d = 31 Jan 2026: the base window is 3 Nov 2025 - 31 Jan 2026 = 28 x 8 (Nov) + 31 x 12 (Dec) + 31 x 8 (Jan) = 844
    world = two_year_world()
    d = date(2026, 1, 31)
    assert d - timedelta(days=89) == date(2025, 11, 3)
    assert ss.company_units(world, date(2025, 11, 3), d) == 844
    lifts = ss.measure_lifts(world, d, FIRST)
    assert {m: r["year"] for m, r in lifts.items()} == {1: 2026} | dict.fromkeys(range(2, 13), 2025)
    # Feb 2025 (the busy 40 a day, 28 days): 1120 x 90 / (28 x 844) = 4.2654; Dec 2025: 372 x 90 / (31 x 844) = 1.2796
    assert lifts_of(lifts) == {1: "0.85", 2: "4.27", 3: "0.85", 4: "0.85", 5: "0.85", 6: "0.85", 7: "0.85", 8: "0.85",
                               9: "0.85", 10: "0.85", 11: "0.85", 12: "1.28"}                      # the rest: 8 x 90 / 844
    assert lifts[1]["days"] == 31 and lifts[2]["days"] == 28 and lifts[12]["days"] == 31
    # on the last day of a month the month is whole
    assert ss.measure_lifts(world, date(2026, 2, 28), FIRST)[2]["days"] == 28
    assert ss.measure_lifts(world, date(2026, 2, 28), FIRST)[3]["year"] == 2025


@test("a month with fewer than 15 days of data has no lift of its own; a partial first month divides by its days")
def _():
    # the ledger's first day is 10 Nov 2025: Nov has 21 days (10-30), at 16 a day = 336 units / 21 / 8 = 2.00 (over the
    # 30 days of the month it would read 1.40); the rest sells 8 a day
    world = company((date(2025, 11, 10), date(2025, 11, 30), 8, 8), (date(2025, 12, 1), date(2026, 10, 20), 5, 3))
    lifts = ss.measure_lifts(world, D, date(2025, 11, 10))
    assert (str(lifts[11]["lift"]), lifts[11]["basis"], lifts[11]["days"]) == ("2.00", "last year", 21)
    assert all(str(lifts[m]["lift"]) == "1.00" and lifts[m]["basis"] == "last year" for m in range(1, 11))
    # 16 Nov: 15 days (16-30) is enough; 17 Nov: 14 days is not, and the lift is neutral whatever those days sold
    for first, days, lift, basis in ((date(2025, 11, 16), 15, "2.00", "last year"), (date(2025, 11, 17), 14, "1.00", "no data")):
        world = company((first, date(2025, 11, 30), 8, 8), (date(2025, 12, 1), date(2026, 10, 20), 5, 3))
        got = ss.measure_lifts(world, D, first)[11]
        assert (str(got["lift"]), got["basis"], got["days"], got["year"]) == (lift, basis, days, 2025), (first, got)
    # a ledger that starts after the month: no days at all
    got = ss.measure_lifts(world, D, date(2025, 12, 1))
    assert (str(got[11]["lift"]), got[11]["basis"], got[11]["days"]) == ("1.00", "no data", 0)
    assert got[12]["basis"] == "last year"                                                     # 31 days from 1 Dec
    # the current month counts the days up to D: on the 14th it is "no data", on the 15th it is a lift
    world = two_year_world()
    assert ss.measure_lifts(world, date(2026, 10, 14), FIRST)[10]["basis"] == "no data"
    assert ss.measure_lifts(world, date(2026, 10, 14), FIRST)[10]["days"] == 14
    assert ss.measure_lifts(world, date(2026, 10, 15), FIRST)[10]["basis"] == "last year"


@test("the base divides by the days the ledger covers when it starts inside the window; no sale in it: no lift at all")
def _():
    # the ledger starts on 20 Sep 2026: the base window covers 20 Sep - 20 Oct = 31 days. 20-30 Sep sold 10 a day, 1-20 Oct 15
    # a day: base = (110 + 300) / 31 = 13.226 a day; Oct = 300 / 20 = 15 a day -> 300 x 31 / (20 x 410) = 1.134 -> 1.13
    # (divided by 90 the base would be 4.56 and the lift 3.29). Sep has 11 days only: "no data"
    world = company((date(2026, 9, 20), date(2026, 9, 30), 6, 4), (date(2026, 10, 1), date(2026, 10, 20), 9, 6))
    lifts = ss.measure_lifts(world, D, date(2026, 9, 20))
    assert (str(lifts[10]["lift"]), lifts[10]["basis"], lifts[10]["days"]) == ("1.13", "last year", 20)
    assert (str(lifts[9]["lift"]), lifts[9]["basis"], lifts[9]["days"]) == ("1.00", "no data", 11)
    assert [lifts[m]["basis"] for m in range(1, 13) if m != 10] == ["no data"] * 11
    # no sale in the base window (the last one was on 30 Jun): every month is neutral, though Feb has its data
    world = company((date(2025, 1, 1), date(2025, 2, 28), 20, 20), (date(2025, 3, 1), date(2026, 2, 28), 10, 6),
                    (date(2026, 3, 1), date(2026, 6, 30), 5, 3))
    assert ss.company_units(world, date(2026, 7, 23), D) == 0
    lifts = ss.measure_lifts(world, D, FIRST)
    assert all(str(r["lift"]) == "1.00" and r["basis"] == "no data" for r in lifts.values())
    assert lifts[2]["days"] == 28                                                              # the data is there, the base is not
    assert ss.measure_lifts([], D, FIRST)[2]["basis"] == "no data"                             # no items
    assert ss.measure_lifts(two_year_world(), D, None)[2]["lift"] == Decimal("2.00")           # no first day: no limit
    assert ss.measure_lifts(two_year_world(), D, date(2026, 11, 1))[2]["basis"] == "no data"   # first day after D


@test("config lifts win with basis config; a number not above nought and a key that is no month are ignored")
def _():
    world = two_year_world()
    plain = ss.measure_lifts(world, D, FIRST)
    got = ss.measure_lifts(world, D, FIRST, overrides={
        "2": 1.6, 12: "1.25", 11: Decimal("0.5"), "6": 1.005,                    # used (a float goes through its text; half up)
        7: 0, 8: -1, 9: None, 3: True, 5: "x", "13": 2, 0: 2, "x": 3, 1: float("nan")})        # all passed over
    assert {m: (str(r["lift"]), r["basis"]) for m, r in got.items() if r["basis"] == "config"} == {
        2: ("1.60", "config"), 12: ("1.25", "config"), 11: ("0.50", "config"), 6: ("1.01", "config")}
    for m in (1, 3, 4, 5, 7, 8, 9, 10):
        assert got[m] == plain[m], m                                                           # untouched
    assert got[2]["days"] == 28 and got[2]["year"] == 2026                                     # days and year stay as measured
    assert ss.measure_lifts(world, D, FIRST, overrides={}) == plain and ss.measure_lifts(world, D, FIRST, None) == plain
    assert str(ss.measure_lifts(world, D, FIRST, overrides={"4": 0.004})[4]["lift"]) == "0.01"   # never nought
    # a config lift stands where there is no data at all
    empty = ss.measure_lifts([], D, FIRST, overrides={"2": 1.4})
    assert (str(empty[2]["lift"]), empty[2]["basis"]) == ("1.40", "config") and empty[3]["basis"] == "no data"


# ── lift_of and season_days ────────────────────────────────────────────────────

@test("lift_of: the lift of that day's month; 1.00 for a month the lifts do not hold or hold no number above nought")
def _():
    lifts = ss.measure_lifts(two_year_world(), D, FIRST)
    assert ss.lift_of(lifts, date(2027, 2, 14)) == Decimal("2.00") and ss.lift_of(lifts, date(2026, 12, 31)) == Decimal("1.50")
    assert str(ss.lift_of({}, date(2027, 2, 14))) == "1.00" and str(ss.lift_of(None, date(2027, 2, 14))) == "1.00"
    assert str(ss.lift_of({2: lifts[2]}, date(2027, 3, 1))) == "1.00"                          # March is missing
    assert ss.lift_of({2: 1.5, "3": "0.75"}, date(2027, 2, 1)) == Decimal("1.5")              # a plain number table
    assert ss.lift_of({2: 1.5, "3": "0.75"}, date(2027, 3, 1)) == Decimal("0.75")              # a month key may be its text
    for bad in (0, -1, "x", None, {"lift": 0}, {"lift": "x"}, {}, Decimal("NaN"), [1]):
        assert str(ss.lift_of({2: bad}, date(2027, 2, 1))) == "1.00", bad


@test("season_days: the days AFTER the data date, month by month, across a month, a year and a leap February")
def _():
    sd = ss.season_days
    jan_feb = {1: Decimal("2.00"), 2: Decimal("0.50")}
    # start 28 Jan, 5 days: 29, 30, 31 Jan (3 x 2.00 = 6.00) and 1, 2 Feb (2 x 0.50 = 1.00) = 7.00; the 28th itself is not in
    assert sd(date(2026, 1, 28), 5, jan_feb) == Decimal("7.00")
    assert sd(date(2026, 1, 31), 1, jan_feb) == Decimal("0.50")                                # only 1 Feb
    assert sd(date(2026, 1, 30), 1, jan_feb) == Decimal("2.00")                                # only 31 Jan
    assert sd(date(2026, 1, 28), 3, jan_feb) == Decimal("6.00") and sd(date(2026, 1, 28), 2, jan_feb) == Decimal("4.00")
    # the year's end: 30, 31 Dec (2 x 1.50) and 1, 2, 3 Jan (3 x 2.00) = 9.00
    assert sd(date(2026, 12, 29), 5, {12: Decimal("1.50"), 1: Decimal("2.00")}) == Decimal("9.00")
    # a leap February has 29 days: 1-29 Feb at 0.50 = 14.50, and 1 Mar (no lift held: 1.00) makes 15.50
    assert sd(date(2028, 1, 31), 29, jan_feb) == Decimal("14.50") and sd(date(2028, 1, 31), 30, jan_feb) == Decimal("15.50")
    assert sd(date(2027, 1, 31), 28, jan_feb) == Decimal("14.00")                              # 28 days in 2027
    # nothing: no days, or a negative count, or no lifts at all
    assert sd(D, 0, jan_feb) == 0 and sd(D, -5, jan_feb) == 0
    assert sd(D, 40, {}) == 40 and sd(D, 40, None) == 40 and isinstance(sd(D, 40, None), Decimal)
    # a whole year from D: Oct 21-31 (11) + Nov (30) + Dec (31) + Jan (31) + Feb (28) + Mar..Sep (31+30+31+30+31+31+30) + Oct 1-20 (20)
    year = {m: Decimal(m) / 10 for m in range(1, 13)}
    assert sd(D, 365, year) == (Decimal("1.0") * 11 + Decimal("1.1") * 30 + Decimal("1.2") * 31 + Decimal("0.1") * 31
                                + Decimal("0.2") * 28 + Decimal("0.3") * 31 + Decimal("0.4") * 30 + Decimal("0.5") * 31
                                + Decimal("0.6") * 30 + Decimal("0.7") * 31 + Decimal("0.8") * 31 + Decimal("0.9") * 30
                                + Decimal("1.0") * 20)


@test("season_days equals the day-by-day sum on random starts, counts and lifts")
def _():
    rng = random.Random(20261020)
    for _n in range(300):
        table = {m: Decimal(rng.randint(1, 400)) / 100 for m in range(1, 13) if rng.random() < 0.8}
        start = date(2025, 1, 1) + timedelta(days=rng.randint(0, 1500))
        n = rng.randint(0, 800)
        walk = Decimal(0)
        for k in range(1, n + 1):
            walk += table.get((start + timedelta(days=k)).month, Decimal(1))
        assert ss.season_days(start, n, table) == walk, (start, n)


# ── the order rule ─────────────────────────────────────────────────────────────

@test("joins and suggested are today's rule (rate6 x days - position) when every lift is 1")
def _():
    rng = random.Random(1020)
    neutral = {m: {"lift": Decimal("1.00"), "basis": "no data", "days": 0, "year": 2025} for m in range(1, 13)}
    joined = 0
    for _n in range(500):
        position = Decimal(rng.randint(0, 5000)) / Decimal(rng.choice([1, 2, 10, 100, 1000]))
        rate6 = Decimal(rng.randint(0, 3_000_000)) / Decimal(1_000_000)
        trig = rng.randint(1, 200)
        fill = trig + rng.randint(0, 60)
        start = date(2026, 1, 1) + timedelta(days=rng.randint(0, 700))
        today_joins = stock_plan.round6(position - Decimal(trig) * rate6) < 0                  # stock.build's rule, negated
        today_qty = stock_plan.units_up(Decimal(fill) * rate6 - position)
        for lifts in ({}, None, neutral):
            assert ss.joins(position, rate6, start, trig, lifts) is today_joins, (position, rate6, trig)
            assert ss.suggested(position, rate6, start, fill, lifts) == today_qty, (position, rate6, fill)
        joined += today_joins
    assert 50 < joined < 450                                                                   # both outcomes are exercised
    # the boundary: 2.5 x 40 = 100 -> a position of exactly 100 is covered (not listed); 99 is not
    assert ss.joins(100, Decimal("2.5"), D, 40, {}) is False and ss.joins(99, Decimal("2.5"), D, 40, {}) is True
    assert ss.suggested(99, Decimal("2.5"), D, 60, {}) == 51 and ss.suggested(99.5, Decimal("2.5"), D, 60, {}) == 51
    assert ss.suggested(100, Decimal("2.5"), D, 60, {}) == 50 and ss.suggested(150, Decimal("2.5"), D, 60, {}) == 0
    assert isinstance(ss.suggested(99, Decimal("2.5"), D, 60, {}), Decimal)


@test("joins and suggested with a season: a hand-worked order, the round6 boundary, no rate")
def _():
    lifts = {10: Decimal("1.10"), 11: Decimal("1.30"), 12: Decimal("0.90")}
    rate6 = Decimal(2)
    # 40 trigger days after 20 Oct: 21-31 Oct (11 x 1.10 = 12.10) + 1-29 Nov (29 x 1.30 = 37.70) = 49.80 season days
    assert ss.season_days(D, 40, lifts) == Decimal("49.80")
    #   demand 2 x 49.80 = 99.60: a position of 100 lasts (100 - 99.6 = 0.4), 99 does not; with no season it needs 80, so 99 lasts
    assert ss.joins(100, rate6, D, 40, lifts) is False and ss.joins(99, rate6, D, 40, lifts) is True
    assert ss.joins(99, rate6, D, 40, {}) is False
    # 60 fill days: 12.10 + 30 x 1.30 (39.00) + 1-19 Dec (19 x 0.90 = 17.10) = 68.20; demand 136.40; 100 on hand: 36.4 -> 37
    assert ss.season_days(D, 60, lifts) == Decimal("68.20")
    assert ss.suggested(100, rate6, D, 60, lifts) == 37 and ss.suggested(100, rate6, D, 60, {}) == 20
    assert ss.suggested(136.4, rate6, D, 60, lifts) == 0 and ss.suggested(Decimal("136.39"), rate6, D, 60, lifts) == 1
    # round6 first: a demand of 0.0000004 is nought units and no join; 0.0000005 rounds up to 0.000001: one unit, a join
    tiny = {1: Decimal("0.10")}                                                                # 1 Jan at 0.10, the day after 31 Dec
    new_year = date(2026, 12, 31)
    assert ss.joins(0, Decimal("0.000004"), new_year, 1, tiny) is False
    assert ss.suggested(0, Decimal("0.000004"), new_year, 1, tiny) == 0
    assert ss.joins(0, Decimal("0.000005"), new_year, 1, tiny) is True
    assert ss.suggested(0, Decimal("0.000005"), new_year, 1, tiny) == 1
    # no rate, no join and no units, whatever the position
    for rate in (0, Decimal(0), Decimal("-1")):
        assert ss.joins(0, rate, D, 40, lifts) is False and ss.joins(-5, rate, D, 40, lifts) is False
        assert ss.suggested(0, rate, D, 60, lifts) == 0 and ss.suggested(-5, rate, D, 60, lifts) == 0
    # the measured lifts feed it: Feb 2.00 doubles the days of a 28-day February
    measured = ss.measure_lifts(two_year_world(), D, FIRST)
    assert ss.season_days(date(2027, 1, 31), 28, measured) == Decimal("56.00")


@test("seasonal cover: the day the season uses the position up, its cap and its edges")
def _():
    lifts = {10: Decimal("1.10"), 11: Decimal("1.30")}
    # rate 2: 21-31 Oct at 2.2 a day = 24.2 in 11 days; then Nov at 2.6 a day: 75.8 more = 29.2 days -> the 30th of Nov (41st day)
    assert ss.seasonal_cover(100, Decimal(2), D, lifts) == 41
    assert ss.seasonal_cover(24.2, Decimal(2), D, lifts) == 11 and ss.seasonal_cover(24.3, Decimal(2), D, lifts) == 12
    assert ss.seasonal_cover(100, Decimal(2), D, {}) == 50 and ss.seasonal_cover(10, Decimal(1), D, {}) == 10
    assert ss.seasonal_cover(10.5, Decimal(1), D, {}) == 11 and ss.seasonal_cover(0.5, Decimal(1), D, {}) == 1
    # a third of a unit a day: 3 days reach 1 unit (to six decimals), not 4
    assert ss.seasonal_cover(1, Decimal(1) / Decimal(3), D, {}) == 3
    # never reached: the cap
    assert ss.seasonal_cover(10_000, Decimal(1), D, {}) == 730 and ss.seasonal_cover(10_000, Decimal(1), D, {}, cap=100) == 100
    assert ss.seasonal_cover(100, Decimal(1), D, {}, cap=100) == 100 and ss.seasonal_cover(100, Decimal(1), D, {}, cap=99) == 99
    assert ss.seasonal_cover(5, Decimal(1), D, {}, cap=0) == 0 and ss.seasonal_cover(5, Decimal(1), D, {}, cap=-3) == 0
    # no rate: None; nothing to cover: 0
    assert ss.seasonal_cover(5, Decimal(0), D, lifts) is None and ss.seasonal_cover(5, Decimal("-1"), D, lifts) is None
    assert ss.seasonal_cover(0, Decimal(1), D, lifts) == 0 and ss.seasonal_cover(-4, Decimal(1), D, lifts) == 0
    assert ss.seasonal_cover(5, 1, D, lifts) == 5 and isinstance(ss.seasonal_cover(5, 1, D, lifts), int)    # plain numbers do
    # against the season_days sums, on random cases: k is the first count at which the demand reaches the position
    rng = random.Random(7)
    for _n in range(300):
        table = {m: Decimal(rng.randint(5, 300)) / 100 for m in range(1, 13)}
        start = date(2026, 1, 1) + timedelta(days=rng.randint(0, 700))
        rate = Decimal(rng.randint(1, 4000)) / 1000
        position = Decimal(rng.randint(1, 20_000)) / 100
        k = ss.seasonal_cover(position, rate, start, table)
        assert 1 <= k <= 730
        reached = stock_plan.round6(position - rate * ss.season_days(start, k, table)) <= 0
        before = k == 1 or stock_plan.round6(position - rate * ss.season_days(start, k - 1, table)) > 0
        assert (reached and before) or (k == 730 and not reached), (position, rate, start, k)
    # and with joins: a position that lasts more than the trigger days does not join, one that lasts less does
    tab = {11: Decimal("1.30"), 12: Decimal("1.50")}
    seen = set()
    for position in (20, 50, 80, 99, 100, 120, 130, 160, 200, 300):
        cover = ss.seasonal_cover(position, Decimal(2), D, tab)
        joined = ss.joins(position, Decimal(2), D, 60, tab)
        if cover > 60:
            assert not joined, (position, cover)
        if cover < 60:
            assert joined, (position, cover)
        seen.add(joined)
    assert seen == {True, False}


# ── a longer history ───────────────────────────────────────────────────────────

# An item with 50 units from 1 May, all gone on 10 Jun (in stock 1 May - 9 Jun at the day's end), 40 more from 1 Oct.
OUT_MOST = ((date(2026, 5, 1), 50), (date(2026, 6, 10), -50), (date(2026, 10, 1), 40))


def out_most_item() -> stock.Item:
    it = item("L")
    it.sales = {date(2026, 5, 30): Decimal(100), date(2026, 5, 31): Decimal(2),
                **{day: Decimal(3) for day in span(date(2026, 6, 1), date(2026, 6, 9))},
                date(2026, 8, 15): Decimal(4),                                         # sold from a van while the main store was out
                **{day: Decimal(2) for day in span(date(2026, 10, 1), D)}}
    return it


@test("long_rate: out of stock 70 of the 90 days, the window grows back one day at a time until it holds 30 in stock")
def _():
    it, f = out_most_item(), flow(*OUT_MOST)
    # the 90 days (23 Jul - 20 Oct) hold 20 days in stock (1-20 Oct): the plain rate is 44 sold / 30 (the floor) = 1.467
    assert sum(1 for day in span(date(2026, 7, 23), D) if f.main_at(day) > 0) == 20
    got = ss.long_rate(it, f, D)
    # back from 22 Jul: out of stock until 9 Jun, then 9 Jun ... 31 May = 10 more days in stock -> 30; the window is 31 May - 20 Oct
    # (143 days). Sold in it: 2 (31 May) + 27 (1-9 Jun) + 4 (15 Aug, a van) + 40 (Oct) = 73; the 100 of 30 May is outside.
    assert got == {"rate": Decimal(73) / Decimal(30), "first": date(2026, 5, 31), "last": D, "in_stock_days": 30,
                   "sold": Decimal(73)}
    assert (got["last"] - got["first"]).days + 1 == 143 and str(got["rate"].quantize(Decimal("0.0001"))) == "2.4333"
    assert got["rate"] > Decimal(44) / Decimal(30)                                             # the plain, floored rate
    # max_days: it needs a window of 143 days
    assert ss.long_rate(it, f, D, max_days=143)["first"] == date(2026, 5, 31)
    assert ss.long_rate(it, f, D, max_days=142) is None and ss.long_rate(it, f, D, max_days=100) is None
    assert ss.long_rate(it, f, D, max_days=90) is None
    # units sold never below nought: more returned than sold in the window is a rate of nought
    back = item("R")
    back.sales = {date(2026, 10, 5): Decimal(-6), date(2026, 6, 2): Decimal(1)}
    got = ss.long_rate(back, f, D)
    assert got["sold"] == 0 and got["rate"] == 0 and got["in_stock_days"] == 30 and got["first"] == date(2026, 5, 31)
    assert isinstance(got["rate"], Decimal) and isinstance(got["sold"], Decimal)


@test("long_rate: None when the plain window holds enough days in stock, or when even a year does not")
def _():
    it = out_most_item()
    # in stock the whole time: nothing to do
    assert ss.long_rate(it, flow((date(2026, 6, 1), 100)), D) is None
    # exactly 30 days in the 90 (21 Sep - 20 Oct: 10 + 20) is enough; 29 is one short, and one more day back (22 Jul) finds it
    assert ss.long_rate(it, flow((date(2026, 9, 21), 10)), D) is None
    f29 = flow((date(2026, 7, 22), 5), (date(2026, 7, 23), -5), (date(2026, 9, 22), 10))
    assert sum(1 for day in span(date(2026, 7, 23), D) if f29.main_at(day) > 0) == 29
    got = ss.long_rate(it, f29, D)
    assert got["first"] == date(2026, 7, 22) and got["in_stock_days"] == 30
    assert got["sold"] == Decimal(4) + Decimal(40) and got["rate"] == Decimal(44) / Decimal(30)    # 15 Aug and October
    # never 30 days in stock in the whole year: the floor rule stands
    assert ss.long_rate(it, flow((date(2026, 10, 9), 12)), D) is None
    assert ss.long_rate(it, flow(), D) is None and ss.long_rate(it, None, D) is None            # no movements, no flow
    assert ss.long_rate(it, flow((date(2025, 1, 1), 5), (date(2025, 1, 2), -5), (date(2026, 10, 9), 12)), D) is None
    # the year's edge: 20 days in stock in October plus 10 in October 2025. A window of 365 days starts on 21 Oct 2025
    # (D - 364): in stock 21-30 Oct 2025 it is found at exactly 365 days, not at 364; from 20 Oct 2025 it needs 366
    assert D - timedelta(days=364) == date(2025, 10, 21)
    edge = flow((date(2025, 10, 21), 5), (date(2025, 10, 31), -5), (date(2026, 10, 1), 40))
    assert ss.long_rate(it, edge, D)["first"] == date(2025, 10, 21)
    assert ss.long_rate(it, edge, D, max_days=364) is None
    assert ss.long_rate(it, edge, D, max_days=400)["first"] == date(2025, 10, 21)               # a longer reach changes nothing
    too_old = flow((date(2025, 10, 20), 5), (date(2025, 10, 30), -5), (date(2026, 10, 1), 40))
    assert ss.long_rate(it, too_old, D) is None and ss.long_rate(it, too_old, D, max_days=366)["first"] == date(2025, 10, 20)
    # the window and the floor are parameters: 30 days of window, 6 days in stock (10 Sep, and 16-20 Oct)
    p = item("P")
    p.sales = {date(2026, 9, 10): Decimal(1), **{day: Decimal(2) for day in span(date(2026, 10, 16), D)}}   # 1 + 5 x 2 = 11
    f2 = flow((date(2026, 9, 10), 1), (date(2026, 9, 11), -1), (date(2026, 10, 16), 9))
    assert ss.long_rate(p, f2, D, rate_days=30, min_days=5) is None                           # 5 days in the 30: enough
    assert ss.long_rate(p, f2, D, rate_days=30, min_days=6, max_days=40) is None              # 10 Sep is 41 days back
    found = ss.long_rate(p, f2, D, rate_days=30, min_days=6, max_days=41)
    assert found["first"] == date(2026, 9, 10) and found["in_stock_days"] == 6
    assert found["sold"] == 11 and found["rate"] == Decimal(11) / Decimal(6)
    assert ss.long_rate(p, f2, D, min_days=0) is None                                         # no floor to reach


@test("availability: the share of item-days on which the main store held the item; None for no items or no days")
def _():
    p, q, r = item("P"), item("Q"), item("R")
    held = {p.ledger_name: flow((date(2026, 9, 1), 50)),                                      # in stock the whole time
            q.ledger_name: flow((date(2026, 9, 1), 5), (date(2026, 10, 5), -5))}              # in stock to 4 Oct, out from the 5th
    flows = stockflow.Flows(items=held, damage_store=None, damage_found=False, outside=[])
    first, last = date(2026, 10, 1), date(2026, 10, 10)
    # P 10 days, Q 4 days (1-4 Oct) = 14 of 20 item-days = 0.70, under the 0.80 the caller warns at
    got = ss.availability([p, q], flows, first, last)
    assert got == Decimal("0.7") and isinstance(got, Decimal) and got < ss.AVAILABILITY_WARN
    # an item the flows hold nothing for (Flows.get answers an empty flow; a plain dict answers None) was never in stock
    assert ss.availability([p, q, r], flows, first, last) == Decimal(14) / Decimal(30)
    assert ss.availability([p, q, r], held, first, last) == Decimal(14) / Decimal(30)
    assert ss.availability([p, q], held, first, last) == Decimal("0.7")
    assert ss.availability([p, q, r], None, first, last) == 0
    # one day, two days; the day's end counts (Q held 4 units at the end of the 4th and none at the end of the 5th)
    assert ss.availability([p], flows, date(2026, 10, 3), date(2026, 10, 3)) == 1
    assert ss.availability([q], flows, date(2026, 10, 4), date(2026, 10, 5)) == Decimal("0.5")
    assert ss.availability([p, q], flows, date(2026, 10, 5), date(2026, 10, 5)) == Decimal("0.5")
    assert ss.availability(iter([p, q]), flows, first, last) == Decimal("0.7")                # any iterable of items
    # none: no items, or the last day before the first
    assert ss.availability([], flows, first, last) is None
    assert ss.availability([p], flows, last, first) is None
    # 0.80 is where the caller warns: 8 of 10 days is not under it
    assert ss.availability([q], flows, date(2026, 10, 1), date(2026, 10, 10)) == Decimal("0.4")
    assert not Decimal("0.8") < ss.AVAILABILITY_WARN and Decimal("0.79") < ss.AVAILABILITY_WARN


# ── the lead time per supplier ─────────────────────────────────────────────────

def mrow(invoice: str, receipt: date, days: int) -> dict:
    """A row as stock.measure_lead_times gives it: the invoice, its receipt and the days between."""
    return {"invoice": invoice, "invoice_date": receipt - timedelta(days=days), "receipt": f"MRN:T-{invoice}",
            "receipt_date": receipt, "days": days, "same": 1, "models": 1}


def oldest_first(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["receipt_date"], r["invoice_date"], r["invoice"]))


ALPHA = [mrow("A1", date(2025, 3, 1), 38), mrow("A2", date(2025, 6, 1), 41), mrow("A3", date(2025, 9, 1), 40),
         mrow("A4", date(2026, 1, 1), 43)]
BETA = [mrow("B1", date(2025, 5, 1), 70), mrow("B2", date(2025, 12, 1), 72)]
DELTA = [mrow("D1", date(2025, 8, 1), 45)]
VENDORS = {"A1": "Alpha Co", "A2": "Alpha Co", "A3": "Alpha Co", "A4": "Alpha Co", "B1": "Beta Ltd", "B2": "Beta Ltd",
           "D1": "Delta Inc", "E1": "Epsilon"}                                            # E1: an invoice with no matched receipt


@test("lead per supplier: measured at three matched invoices, else config, else the overall lead, else the setting")
def _():
    measured = oldest_first(ALPHA + BETA + DELTA)
    overall = stock.lead_time(measured, 50)
    # all seven: sorted 38 40 41 43 45 70 72 -> the median is 43
    assert overall["basis"] == "measured" and overall["days"] == 43
    got = ss.lead_by_supplier(measured, VENDORS, {"Beta Ltd": 55, "Gamma": 60}, overall, 50)
    assert list(got) == ["Alpha Co", "Beta Ltd", "Delta Inc", "Epsilon", "Gamma", "*"]
    #   Alpha: 4 matched, sorted 38 40 41 43 -> (40 + 41) / 2 = 40.5, half up 41 (half even would say 40)
    assert (got["Alpha Co"]["days"], got["Alpha Co"]["basis"], got["Alpha Co"]["matched"]) == (41, "measured", 4)
    assert [r["invoice"] for r in got["Alpha Co"]["shown"]] == ["A1", "A2", "A3", "A4"]      # oldest first
    #   Beta: 2 matched is too few: its own config. Gamma: config only, nothing matched
    assert (got["Beta Ltd"]["days"], got["Beta Ltd"]["basis"], got["Beta Ltd"]["matched"]) == (55, "config", 2)
    assert (got["Gamma"]["days"], got["Gamma"]["basis"], got["Gamma"]["matched"]) == (60, "config", 0)
    #   Delta (1 matched, no config) and Epsilon (none matched): the overall lead; any other supplier: the same
    for name, matched in (("Delta Inc", 1), ("Epsilon", 0), ("*", 0)):
        assert (got[name]["days"], got[name]["basis"], got[name]["matched"]) == (43, "overall", matched), name
    assert all(r["shown"] == [] for n, r in got.items() if n != "Alpha Co") and all(r["default"] == 50 for r in got.values())
    assert all(type(r["days"]) is int and r["basis"] in ss.LEAD_BASES for r in got.values())
    # the overall lead is not measured (fewer than three matched anywhere): the setting
    none_yet = stock.lead_time(measured[:2], 50)
    assert none_yet["basis"] == "config"
    got = ss.lead_by_supplier(measured[:2], VENDORS, {"Gamma": 60}, none_yet, 50)
    assert (got["Delta Inc"]["days"], got["Delta Inc"]["basis"]) == (50, "setting")
    assert (got["Epsilon"]["days"], got["Epsilon"]["basis"]) == (50, "setting") and got["*"]["basis"] == "setting"
    assert got["Gamma"]["basis"] == "config" and got["Gamma"]["days"] == 60
    # two matched is not three; three is
    two = ss.lead_by_supplier(oldest_first(ALPHA[:2]), VENDORS, {"Alpha Co": 47}, None, 50)["Alpha Co"]
    three = ss.lead_by_supplier(oldest_first(ALPHA[:3]), VENDORS, {"Alpha Co": 47}, None, 50)["Alpha Co"]
    #   three, by receipt date: 38 41 40, sorted 38 40 41 -> the median is 40
    assert (two["days"], two["basis"]) == (47, "config") and (three["days"], three["basis"]) == (40, "measured")
    assert stock.lead_time(oldest_first(ALPHA[:3]), 50)["days"] == 40
    # nothing at all: only the "*" row, on the setting
    only = ss.lead_by_supplier(None, None, None, None, 50)
    assert only == {"*": {"days": 50, "basis": "setting", "shown": [], "matched": 0, "default": 50}}
    # a configured lead that is no number above nought is passed over (the supplier still has its row)
    for bad in (0, -3, "x", None, float("nan"), True, 0.4):
        row = ss.lead_by_supplier([], {}, {"Gamma": bad}, overall, 50)["Gamma"]
        assert (row["days"], row["basis"]) == (43, "overall"), bad
    assert ss.lead_by_supplier([], {}, {"Gamma": 44.5}, overall, 50)["Gamma"]["days"] == 45      # whole days, half up
    assert ss.lead_by_supplier([], {}, {"Gamma": "44"}, overall, 50)["Gamma"]["days"] == 44


@test("lead per supplier: the newest ten, in any order, half up, names collapsed and aliased, the engine's own rows")
def _():
    # twelve invoices a month apart: the oldest two took 190 days, the newest ten 30 to 39: the median of the newest ten is
    # (34 + 35) / 2 = 34.5 -> 35 (with all twelve it would be 35.5 -> 36)
    zeta = [mrow(f"Z{n:02d}", date(2025, 1, 1) + timedelta(days=30 * n), 190 if n < 2 else 28 + n) for n in range(12)]
    vendors = {r["invoice"]: "Zeta" for r in zeta}
    got = ss.lead_by_supplier(oldest_first(zeta), vendors, {}, None, 50)["Zeta"]
    assert (got["days"], got["basis"], got["matched"], len(got["shown"])) == (35, "measured", 12, 10)
    assert [r["invoice"] for r in got["shown"]] == [f"Z{n:02d}" for n in range(2, 12)]
    assert stock.lead_time(oldest_first(zeta), 50)["days"] == 35
    shuffled = list(zeta)
    random.Random(3).shuffle(shuffled)
    assert ss.lead_by_supplier(shuffled, vendors, {}, None, 50)["Zeta"] == got                # the rows need not come sorted
    # names: spaces collapsed, then the aliases (compared folded); an invoice number is compared folded too
    spelt = {"a1": "ALPHA CO", "A2 ": "Alpha  Co", " A3": " alpha co ", "A4": "Alpha Co", "B1": "Beta Ltd", "B2": "Beta Ltd"}
    measured = oldest_first(ALPHA + BETA)
    alias = {"Alpha CO": " Alpha  Co ", "": "nothing", "x": ""}                              # a value is collapsed too
    merged = ss.lead_by_supplier(measured, spelt, {}, None, 50, aliases=alias)
    assert list(merged) == ["Alpha Co", "Beta Ltd", "*"]
    assert (merged["Alpha Co"]["days"], merged["Alpha Co"]["basis"], merged["Alpha Co"]["matched"]) == (41, "measured", 4)
    # without the alias three spellings stand apart ("Alpha  Co" and "Alpha Co" collapse to one): none has three invoices
    apart = ss.lead_by_supplier(measured, spelt, {}, None, 50)
    assert list(apart) == ["ALPHA CO", "Alpha Co", "alpha co", "Beta Ltd", "*"]       # alphabetical, then by spelling
    assert apart["Alpha Co"]["matched"] == 2 and all(r["basis"] == "setting" for r in apart.values())
    # an alias also names a configured supplier
    assert ss.lead_by_supplier([], {}, {"  ALPHA co ": 61}, None, 50, aliases=alias)["Alpha Co"]["days"] == 61
    # the engine's own rows: invoices set beside the receipts that hold them (stock.measure_lead_times)
    invoices = [{"invoice_no": f"INV-{n}", "invoice_date": day, "model": f"MODEL-{n}", "qty": str(10 * n)}
                for n, day in ((1, "2025-01-01"), (2, "2025-04-01"), (3, "2025-07-01"), (4, "2025-09-01"))]
    receipts = {f"MRN:T-{n}": {"date": day, "codes": {f"MODEL-{n}": Decimal(10 * n)}}
                for n, day in ((1, date(2025, 2, 10)), (2, date(2025, 5, 15)), (3, date(2025, 8, 18)), (4, date(2025, 10, 31)))}
    real = stock.measure_lead_times(invoices, receipts)
    assert [r["days"] for r in real] == [40, 44, 48, 60]                                       # the lags: 40, 44, 48 and 60 days
    owner = {"INV-1": "Alpha Co", "INV-2": "Alpha Co", "INV-3": "Alpha Co", "INV-4": "Beta Ltd"}
    got = ss.lead_by_supplier(real, owner, {}, stock.lead_time(real, 50), 50)
    assert (got["Alpha Co"]["days"], got["Alpha Co"]["basis"]) == (44, "measured")            # median of 40, 44, 48
    #   Beta has one invoice: the overall lead, the median of 40 44 48 60 = (44 + 48) / 2 = 46
    assert (got["Beta Ltd"]["days"], got["Beta Ltd"]["basis"], got["*"]["days"]) == (46, "overall", 46)


# ── the runner ─────────────────────────────────────────────────────────────────

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
