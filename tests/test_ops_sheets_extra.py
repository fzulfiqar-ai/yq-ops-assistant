"""Ops engine: the Stock and Movements sheets the weekly and the month-end workbook share
(scripts.ops.workbook.sheets_extra), on a synthetic world.

    python -m tests.test_ops_sheets_extra

Same lightweight runner as tests/test_ops_stock.py, and ITS stand-ins (imported, not copied): the invented stock
ledger, the database stand-in that answers every read from it (World), the temporary data root and the model
(model_of). Every stock_totals call runs inside a temporary data root (it reads config.json; totals_of() refuses
to run anywhere else). The movements are an invented period table in the shape scripts.ops.moves.period_table
gives it; the module itself is a stand-in put in sys.modules, or marked missing there. SUPABASE_URL, SUPABASE_KEY
and DATABASE_URL are pinned to ci.invalid BEFORE any import: no database, no network, no .env. All names and
numbers are invented; every folder is a temporary one.

The stock world is the stock suite's (D = Sun 15 Mar 2026): five items in the main store, 505 units, BHD 412.800
at landed cost; ITEM-B on the order list (65 units, BHD 162.500); the fast movers ITEM-A (in stock) and ITEM-F (out
since September). As on Sat 28 Feb: four items, 504 units, BHD 343.100 at today's costs.

Covered:
  * stock_totals: every field of today's model; as on an earlier day built here from the reads (the weekly
    builder's way) and from a model (the month pack's way), the same totals; the fast movers over a period as two
    dates or as 'A..B'; a period outside the day series, a model that cannot be built (an as_of after or before
    the ledger, a read or a setting that fails) and facts not the contract's shape: one sentence, never an
    exception, and never another module's words or config's path; the model is left as it was;
  * the parts: the vans and outlets of the order position (there, failed, none counted, absent), the review, the
    damage store (failed, missing, not set up) and the plan, each its "not worked out this time (...)" line;
  * stock_sheet: every figure typed, or a formula with its value (no error value); the refs; the formats; the one
    line of totals that failed or are not the contract's shape (what was drawn undone);
  * moves_totals: every field of an invented period table; the stores as the month workbook names salesmen, vans
    then outlets; the totals the sums of the rows; exceptions summed by kind (the module's list, the pairing
    summary, neither, none); the module read lazily, missing or raising; every malformed table one sentence that
    names no voucher;
  * movements_sheet: the check column reads 0 on every row of both blocks; the total row is the sum of the rows;
    a row that does not balance shows by how much; no store, no exception, a failure;
  * no item, code or voucher on either sheet or in either totals dict (every cell scanned, with the weekly
    builder's own voucher pattern), every text through the wording guard; the weekly builder's call by keyword
    names (its offered() hand-over); the saved file carries every formula with its value (openpyxl).
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
import tempfile
import traceback
import types
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

# The stock suite's stand-ins. Importing it also sets stdout to UTF-8 and imports scripts.ops.db first.
from tests.test_ops_stock import (  # noqa: E402
    CODES, D, LONG, NAMES, NINE, TEMP_ROOTS, World, L, data_root, model_of, standing_in)
from scripts.ops import fmt, paths, stock  # noqa: E402
from scripts.ops.workbook import book as bk  # noqa: E402
from scripts.ops.workbook import formula as fx  # noqa: E402
from scripts.ops.workbook import model as mdl  # noqa: E402
from scripts.ops.workbook import sheets_extra as sx  # noqa: E402

stock.RAISE_PARTS = True              # a part of the stock model that fails fails the test

TESTS: list[tuple[str, object]] = []
ZERO = Decimal(0)
FEB = date(2026, 2, 28)               # a Saturday, a month end: the month pack's day, inside the ledger
SAT = date(2026, 3, 14)               # the Saturday before D: the weekly builder's week, 8 to 14 March
WEEK = (date(2026, 3, 9), D)          # the seven days to D
MOVES = "scripts.ops.moves"
ABSENT = object()
TWO_DATES = "the period is two dates, or 'YYYY-MM-DD..YYYY-MM-DD'"
# the weekly builder's own voucher pattern (scripts/ops/workbook/week.py VOUCHER) and a stricter one of our own
VOUCHER = re.compile(r"\b[A-Za-z]{1,6}(?:-[A-Za-z]{1,6})*-\d{2}-\d{2}-\d+\b")
NUMBERED = re.compile(r"\b[A-Za-z]{1,6}\s*:\s*[A-Za-z0-9]+(?:-\d+)+\b")
CODE = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(sorted(CODES.values(), key=len, reverse=True)) + r")(?![A-Za-z0-9])",
                  re.I)


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── helpers ───────────────────────────────────────────────────────────────────

def totals_of(model=None, **kw) -> dict:
    """sx.stock_totals, only inside a temporary data root: it reads config.json, never the project folder's."""
    assert TEMP_ROOTS and paths.data_root() == TEMP_ROOTS[-1], "stock_totals ran outside data_root()"
    return sx.stock_totals(model, **kw)


def at(book, ref: str):
    """The value of the cell a ref names ("Stock!$B$7"), the book evaluated."""
    r = fx.parse_range(ref, "")
    return book.value(r.sheet, r.r1, r.c1)


def cell_of(book, ref: str):
    r = fx.parse_range(ref, "")
    return book.get(r.sheet).cells[(r.r1, r.c1)]


def column(book, ref: str) -> list:
    """The values down a one-column block a ref names."""
    r = fx.parse_range(ref, "")
    return [book.value(r.sheet, row, r.c1) for row in range(r.r1, r.r2 + 1)]


def texts(ws) -> list[str]:
    return [c.value for c in ws.cells.values() if isinstance(c.value, str)]


def line_starting(ws, start: str) -> str:
    hits = [t for t in texts(ws) if t.startswith(start)]
    assert hits, f"{ws.name}: no text starts {start!r}"
    return hits[0]


def stock_book(totals, heading: str = "Stock at the end of the day") -> tuple:
    """(book, sheet, refs): the Stock sheet of these totals, evaluated; no formula may give an error value."""
    book = bk.Book()
    ws = book.sheet("Stock")
    refs = sx.stock_sheet(ws, totals, heading)
    errors = book.evaluate()
    assert errors == [], errors
    return book, ws, refs


def moves_book(totals, heading: str = "Movements in the week") -> tuple:
    book = bk.Book()
    ws = book.sheet("Movements")
    refs = sx.movements_sheet(ws, totals, heading)
    errors = book.evaluate()
    assert errors == [], errors
    return book, ws, refs


def close(a, b) -> bool:
    return abs(float(a) - float(b)) < 1e-9


def leaks(text: str) -> list[str]:
    """What a forwarded text may never hold: an item's name or code, a voucher number, an ID-shaped number."""
    low = " ".join(str(text).split()).casefold()
    found = [n for n in list(NAMES.values()) + [L(k) for k in NAMES] if n.casefold() in low]
    for name, rx in (("code", CODE), ("voucher", VOUCHER), ("numbered", NUMBERED), ("nine", NINE), ("long", LONG)):
        if rx.search(str(text)):
            found.append(name)
    return found


def walk(x):
    """Every value inside a totals dict, keys too."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from walk(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from walk(v)
    else:
        yield x


def assert_clean_totals(totals: dict) -> None:
    for v in walk(totals):
        assert not isinstance(v, stock.Item), "an Item in the totals"
        if isinstance(v, str):
            assert not leaks(v), (v, leaks(v))


def assert_clean_sheet(ws) -> None:
    for t in texts(ws):
        assert not leaks(t), (ws.name, t, leaks(t))
        fmt.careful(t)


def facts_of(m) -> str:
    return json.dumps(stock.facts_out(m, "headline", None), sort_keys=True, default=str)


# ── the movements: an invented period table ───────────────────────────────────

def store(name: str, kind: str, rate: str, uncosted=0, closing=None, **figures) -> dict:
    """A store's row as moves.period_table gives it: its units (closing worked out so that it balances, unless
    given), and its BHD at `rate` a unit."""
    units = {k: Decimal(str(figures.get(k, 0))) for k in sx.MOVE_KEYS if k not in ("closing", "check")}
    balanced = sum((units[k] for k in sx.CHECK_PLUS), ZERO) - sum((units[k] for k in sx.CHECK_MINUS if k != "closing"),
                                                                  ZERO)
    units["closing"] = balanced if closing is None else Decimal(str(closing))
    units["check"] = balanced - units["closing"]
    return {"store": name, "kind": kind, "units": units,
            "bhd": {k: (v * Decimal(rate)).quantize(Decimal("0.001")) for k, v in units.items()},
            "uncosted_units": Decimal(uncosted)}


def stores() -> list[dict]:
    return [store("Van B - Acc WH", "van", "0.5", opening=40, received=20, issued_to=22, back_to_main=5, to_damage=1,
                  not_received_yet=2, sold=30, customer_returns=1),                      # closes on 23
            store("Shop East", "outlet", "1.25", uncosted=3, opening=10, received=5, issued_to=5, not_received=1,
                  sold=6, counts=-1),                                                     # closes on 7
            store("Van A - Acc WH", "van", "2", opening=12, received=8, issued_to=8, to_other=2, sold=15)]  # on 3


# the module's own exceptions, with their vouchers and items (never to be kept), one kind by an alias
EXCEPTION_ROWS = [
    {"kind": "xfer_open", "voucher": "SIO:X-26-03-7", "item": L("B"), "date": date(2026, 3, 10), "units": 1,
     "bhd": "1.250"},
    {"kind": "qty_differs", "voucher": "SIO:X-26-03-9", "item": L("A"), "units": 4, "bhd": "2"},
    {"kind": "not_received", "voucher": "SIO:X-26-03-11", "item": L("C"), "units": 2, "bhd": None},
]


def table(**extra) -> dict:
    return dict({"first": WEEK[0], "last": WEEK[1], "stores": stores(), "totals": {},
                 "exceptions": [dict(x) for x in EXCEPTION_ROWS]}, **extra)


def summary(**counts) -> dict:
    """pairing.summarise()'s shape: counts and the outcomes' units."""
    keys = ("received", "late", "elsewhere", "qty_differs", "in_grace", "not_received", "twin_items", "twin_slots",
            "orphans", "aside")
    units = {"not_received": 9, "elsewhere": 4, "qty_differs": 0, "twins": 6, "orphan": 2, "received": 40, "late": 3,
             "in_grace": 5}
    return {"first": WEEK[0], "last": WEEK[1], "store": None, "direction": "out",
            "counts": {k: counts.get(k, 0) for k in keys},
            "outcomes": {o: {"vouchers": 1, "lines": 1, "units": Decimal(u)} for o, u in units.items()},
            "issues": {"vouchers": 9, "lines": 12, "units": Decimal(60)}}


class moves_module:
    """scripts.ops.moves for the length of a with-block: a stand-in module, or marked missing (None)."""

    def __init__(self, period_table=None, missing: bool = False, bare: bool = False):
        self.period_table, self.missing, self.bare = period_table, missing, bare
        self.calls: list[tuple] = []

    def __enter__(self):
        self.saved = sys.modules.get(MOVES, ABSENT)
        if self.missing:
            sys.modules[MOVES] = None
        else:
            mod = types.ModuleType(MOVES)
            if not self.bare:
                def period_table(first, last):
                    self.calls.append((first, last))
                    return self.period_table(first, last)
                mod.period_table = period_table
            sys.modules[MOVES] = mod
        return self

    def __exit__(self, *exc):
        if self.saved is ABSENT:
            sys.modules.pop(MOVES, None)
        else:
            sys.modules[MOVES] = self.saved
        return False


# ── stock_totals ──────────────────────────────────────────────────────────────

@test("stock totals: every field of today's model, counts and values only, the model left as it was")
def _():
    with data_root():
        m, _inp = model_of(World())
        before = facts_of(m)
        t = totals_of(m)
        assert facts_of(m) == before
    f = m.facts
    assert (t["as_of"], t["ledger_to"], t["cut"], t["store"]) == (D, D, False, "Main Store")
    assert t["cost_label"] == "at landed cost"
    assert t["basis"] == ("at landed cost (MRN; the pricing sheet where no MRN: 1 item; the latest receipt rate where "
                          "neither: 2 items)"), t["basis"]
    assert (t["rate_days"], t["rank_months"]) == (stock.RATE_DAYS, stock.RANK_MONTHS) == (90, 12)
    # 394 x 0.4 + 20 x 2.5 + 50 x 0.8 + 36 x 3.2 + 5 x 10, display items in
    assert t["main"] == {"items": 5, "units": 505, "value": Decimal("412.800"), "uncosted_items": 0}, t["main"]
    assert t["beside"] is None and t["parts"]["position"] == sx.MAIN_ONLY
    k = f["kpi_cover"]
    c = t["cover"]
    assert c == {"days": fmt.q3(k["now"]["days"]), "low": 30, "high": 45, "value": fmt.q3(k["now"]["value"]),
                 "above_value": fmt.q3(k["now"]["above_value"]),
                 "above_share": fmt.q3(k["now"]["above_value"]) / fmt.q3(k["now"]["value"]),
                 "last_days": fmt.q3(k["last"]["days"]), "last_end": date(2026, 2, 28),
                 "new_items": k["now"]["new_items"], "uncosted_items": 0}, c
    assert c["new_items"] == 1                                  # ITEM-N, first received on 1 Mar
    fa = t["fast"]
    # ITEM-A in stock on all 15 days of March, ITEM-F on none (out since September): out on every day, out now
    assert fa == {"n": 2, "listed": 2, "window": None, "out_now": 1,
                  "month": {"first": date(2026, 3, 1), "last": D, "days": 15, "available": 15, "sku_days": 30,
                            "share": Decimal("0.5"), "out_all": 1}}, fa
    ag = t["ageing"]
    assert [b["band"] for b in ag["bands"]] == list(stock.AGE_BANDS)
    assert ag["units"] + ag["display_units"] == t["main"]["units"] == 505
    assert ag["value"] == sum(b["value"] for b in ag["bands"]) == Decimal("362.800")
    assert ag["over_180_value"] == ag["bands"][2]["value"] + ag["bands"][3]["value"] == Decimal("117.600")
    assert ag["over_180_share"] == ag["over_180_value"] / ag["value"]
    assert (ag["display_items"], ag["display_units"]) == (1, 5)
    assert [b["items"] for b in ag["bands"]] == [len(r["items"]) for r in f["ageing"]["rows"]]
    rv = f["review"]
    assert t["review"] == {"counts": rv["counts"], "items": len(rv["rows"]), "push_units_week": 34, "clear_days": 90}
    assert list(t["review"]["counts"]) == ["Not moving", "Slow", "On track", "Cleared"]
    assert t["damage"] == {"units": 0, "value": 0, "uncosted_units": 0, "month": date(2026, 3, 1), "partial": True,
                           "sold": f["damage"]["months"][-1]["sold"], "sent": 0, "per100": Decimal("0.00"),
                           "trend": None}, t["damage"]
    assert t["order"] == {"items": 1, "units": 65, "value": Decimal("162.500"), "uncosted_items": 0, "join_days": 70,
                          "fill_days": 85}, t["order"]
    assert t["parts"] == {"position": sx.MAIN_ONLY, "damage": "ok", "review": "ok", "plan": "ok"}, t["parts"]
    assert "error" not in t
    assert_clean_totals(t)


@test("as on an earlier day: built here from the reads (the weekly way) and from a model (the month pack's way), alike")
def _():
    with data_root():
        with standing_in(World()):
            built = totals_of(as_of=FEB.isoformat())
            cfg, inp = stock.settings(), stock.gather()
        m = stock.build(inp, cfg, as_of=FEB)
        given = totals_of(m)
        typed = totals_of(m, as_of=date(2026, 1, 1))              # with a model, as_of is not the model's day
    assert built == given == typed, [k for k in built if built[k] != given.get(k)]
    assert (built["as_of"], built["ledger_to"], built["cut"]) == (FEB, D, True)
    assert built["cost_label"] == "at today's landed cost" and built["basis"].startswith("at today's landed cost (MRN;")
    # 414 x 0.4 + 35 x 2.5 + 50 x 0.8 + 5 x 10, at today's costs (ITEM-N arrives on 1 Mar)
    assert built["main"] == {"items": 4, "units": 504, "value": Decimal("343.100"), "uncosted_items": 0}
    assert built["cover"]["last_end"] == date(2026, 1, 31)
    month = built["fast"]["month"]
    assert (month["first"], month["last"], month["days"]) == (date(2026, 2, 1), FEB, 28)
    assert built["ageing"]["units"] + built["ageing"]["display_units"] == 504
    assert built["order"]["items"] == len(m.facts["order"])
    assert_clean_totals(built)
    book, ws, refs = stock_book(built)
    assert at(book, refs["main_units"]) == 504 and at(book, refs["main_value"]) == 343.1
    assert ws.cells[(1, 0)].value == ("Main Store · stock as on Sat 28 Feb 2026 · stock ledger to Sun 15 Mar 2026 · "
                                      "at today's landed cost"), ws.cells[(1, 0)].value
    assert line_starting(ws, "As on Sat 28 Feb 2026: the stock ledger is cut at the end of that day (it runs to Sun 15 "
                             "Mar 2026); every unit is valued at today's cost.")
    assert line_starting(ws, "Month, 1 Feb to 28 Feb 2026")                 # a whole month: no "to date"
    assert line_starting(ws, "Fast movers out on every day of the month") == "Fast movers out on every day of the month"


@test("the fast movers over a period: two dates or 'A..B'; outside the day series one line; not asked, no part")
def _():
    with data_root():
        m, _inp = model_of(World())
        a = totals_of(m, window=WEEK)
        b = totals_of(m, window=f"{WEEK[0].isoformat()}..{WEEK[1].isoformat()}")
        c = totals_of(m, window=[WEEK[0].isoformat(), WEEK[1].isoformat()])
        plain = totals_of(m)
        with standing_in(World()):
            feb = totals_of(as_of=FEB, window=(date(2026, 2, 22), FEB))
        bad = [(why, totals_of(m, window=w)) for why, w in (
            ("the period ends after Sun 15 Mar 2026, the stock's day", (WEEK[0], D + timedelta(days=1))),
            ("the period ends before it starts", (D, WEEK[0])),
            (f"the period starts before {sx._day(m.series_start)}, the first day of the day series",
             (m.series_start - timedelta(days=1), D)),
            (TWO_DATES, "the week"), (TWO_DATES, ("2026-03-09",)), (TWO_DATES, "2026-03-09..soon"),
            (TWO_DATES, (WEEK[0], None)))]
    # ITEM-A in stock on all seven days (hundreds of units, two a day), ITEM-F on none
    week = {"first": WEEK[0], "last": D, "days": 7, "available": 7, "sku_days": 14, "share": Decimal("0.5")}
    assert a["fast"]["window"] == b["fast"]["window"] == c["fast"]["window"] == week, a["fast"]["window"]
    assert a["parts"]["window"] == "ok" and a == b == c
    assert feb["fast"]["window"] == {"first": date(2026, 2, 22), "last": FEB, "days": 7, "available": 7,
                                     "sku_days": 14, "share": Decimal("0.5")}
    for why, t in bad:
        assert t["fast"]["window"] is None and t["parts"]["window"] == f"not worked out this time ({why})", \
            (why, t["parts"]["window"])
        assert t["fast"]["month"] == a["fast"]["month"]             # the month stands
    book, ws, refs = stock_book(a)
    assert line_starting(ws, "Week, 9 Mar to 15 Mar 2026") and line_starting(ws, "Month to date, 1 Mar to 15 Mar 2026")
    assert (at(book, refs["fast_window_available"]), at(book, refs["fast_window_sku_days"]),
            at(book, refs["fast_window_share"])) == (7, 14, 0.5)
    book, ws, refs = stock_book(bad[1][1])
    assert line_starting(ws, "The fast movers over the period:") == ("The fast movers over the period: not worked out "
                                                                     "this time (the period ends before it starts).")
    assert "fast_window_share" not in refs and "fast_month_share" in refs
    book, ws, refs = stock_book(plain)
    assert not [x for x in texts(ws) if x.startswith("The fast movers over the period")]
    assert "window" not in plain["parts"] and plain["fast"]["window"] is None
    assert sx.period_of("2026-03-09 .. 2026-03-15") == WEEK and sx.period_of((WEEK[0], "2026-03-15")) == WEEK


@test("a model that cannot be built, or facts not the contract's shape: one sentence, never another module's words")
def _():
    with data_root():
        with standing_in(World()):
            after = totals_of(as_of=D + timedelta(days=1))
            before = totals_of(as_of="2025-01-31")
            typed = totals_of(as_of="15-03-2026")
        with standing_in(World(fail={stock.SNAPSHOT_SQL: RuntimeError("SIO:X-26-03-7 could not be read")})):
            down = totals_of(as_of=FEB)
        m, _inp = model_of(World())
        good = totals_of(m)
        m.facts.pop("kpi_cover")
        broken = totals_of(m)
    with data_root(drop=("stock.lead_time_days",)):
        with standing_in(World()):
            unset = totals_of(as_of=FEB)
    assert after == {"error": "The stock ledger runs to 15 March 2026: it cannot show the stock as on 16 March 2026.",
                     "as_of": D + timedelta(days=1), "store": "Main Store"}, after
    assert before["error"] == ("The stock ledger starts on 1 February 2025: it cannot show the stock as on 31 January "
                               "2025.")
    assert typed["error"] == "The date is written YYYY-MM-DD: '15-03-2026' is not one." and typed["as_of"] is None
    assert down == {"error": "RuntimeError", "as_of": FEB, "store": "Main Store"}, down    # its words never shown
    # config's own sentence names the file's path: the key only
    assert unset["error"] == "config 'stock.lead_time_days' is not set, or not valid", unset
    assert broken == {"error": "KeyError", "as_of": D, "store": "Main Store"}, broken
    for t, why in ((after, "The stock ledger runs to 15 March 2026: it cannot show the stock as on 16 March 2026"),
                   (down, "RuntimeError"), (unset, unset["error"]), (broken, "KeyError")):
        book, ws, refs = stock_book(t)
        assert refs == {} and ws.cells[(4, 0)].value == f"The stock figures: not worked out this time ({why}).", \
            ws.cells[(4, 0)].value
        assert not [k for k in ws.cells if k[0] > 4]                    # nothing else on the sheet
        assert_clean_sheet(ws)
    book, ws, refs = stock_book(after, "Stock at the end of Saturday 16 March")
    assert ws.cells[(0, 0)].value == "Stock at the end of Saturday 16 March"
    assert ws.cells[(1, 0)].value == "Main Store · stock as on Mon 16 Mar 2026"
    # totals that are not the contract's shape: what was drawn is undone, the title and one line stand
    for nothing, why in ((None, ""), ({}, ""), ("not a dict", ""), ({"error": ""}, " (KeyError)"),
                         ({"as_of": D}, " (KeyError)"), (dict(good, main=None), " (TypeError)"),
                         (dict(good, ageing=dict(good["ageing"], bands=[])), " (IndexError)")):
        book, ws, refs = stock_book(nothing)
        assert refs == {} and ws.cells[(4, 0)].value == f"The stock figures: not worked out this time{why}.", \
            (why, ws.cells[(4, 0)].value)
        assert not [k for k in ws.cells if k[0] > 4] and not ws.merges and not ws.conds, why
    assert ws.cells[(1, 0)].value == "Main Store · stock as on Sun 15 Mar 2026"


# ── the parts ─────────────────────────────────────────────────────────────────

POSITION = {"stores": ["vans", "outlets", "transit"], "floor_days": 0, "items": {},
            "totals": {"vans": Decimal(30), "outlets": Decimal(12), "transit": Decimal(4), "counted": Decimal(46),
                       "cost_bhd": Decimal("23.400"), "uncosted": Decimal(2)}, "error": None}


@test("the vans and outlets of the order position: there, failed, none counted, not the shape, absent")
def _():
    cases = {"there": dict(POSITION), "failed": dict(POSITION, error="RuntimeError"),
             "none": dict(POSITION, stores=[]), "shape": {"stores": ["vans"], "error": None}, "absent": None}
    got = {}
    with data_root():
        m, _inp = model_of(World())
        for name, pos in cases.items():
            m.facts["position"] = pos
            if pos is None:
                m.facts.pop("position")
            got[name] = totals_of(m)
    there = got["there"]
    assert there["beside"] == {"stores": ["vans", "outlets", "transit"], "vans": 30, "outlets": 12, "transit": 4,
                               "counted": 46, "value": Decimal("23.400"), "uncosted": 2}, there["beside"]
    assert there["parts"]["position"] == "ok"
    whys = {"failed": "not worked out this time (RuntimeError)",
            "none": "not counted (config 'stock.position_stores' names no store beside the main store)",
            "shape": "not worked out this time (KeyError)", "absent": sx.MAIN_ONLY}
    for name, why in whys.items():
        assert got[name]["beside"] is None and got[name]["parts"]["position"] == why, (name, got[name]["parts"])
        book, ws, refs = stock_book(got[name])
        assert line_starting(ws, "The vans and outlets:") == f"The vans and outlets: {why}."
        assert not [k for k in refs if k.startswith("beside_")] and refs["main_units"]
    book, ws, refs = stock_book(there)
    assert [at(book, refs[k]) for k in ("beside_vans", "beside_outlets", "beside_transit", "beside_value",
                                        "beside_uncosted")] == [30, 12, 4, 23.4, 2]
    r = fx.parse_range(refs["beside_value"], "")
    assert ws.cells[(r.r1, 2)].value == "Counted in the order position: vans, outlets, transit."
    assert cell_of(book, refs["beside_value"]).fmt == "g_money3"


@test("the review, the damage store and the plan: failed, missing, a stub or not set up, each one line; the rest stands")
def _():
    with data_root():
        m, _inp = model_of(World())
        m.facts["review"] = {"error": "KeyError"}
        m.facts["damage"] = {"error": "ZeroDivisionError"}
        m.facts["plan"] = {"error": "TypeError"}
        failed = totals_of(m)
        m.facts["review"] = {"clear_days": 90, "rows": [], "built": False}           # a stub
        m.facts.pop("plan")                                                          # missing
        m.facts["damage"] = {"set_up": False, "store": None, "found": False}         # not set up
        stub = totals_of(m)
        m.facts["review"] = {"rows": [], "push_units_week": 0, "clear_days": 90}     # no counts
        m.facts["damage"] = {"set_up": True, "store": "Damage Store", "found": True}  # no "now"
        shape = totals_of(m)
    t = failed
    assert (t["review"], t["damage"]) == (None, None)
    assert t["parts"] == {"position": sx.MAIN_ONLY, "review": "not worked out this time (KeyError)",
                          "damage": "not worked out this time (ZeroDivisionError)",
                          "plan": "not worked out this time (TypeError)"}, t["parts"]
    assert t["order"]["items"] == 1                         # the order list is the model's own, the plan aside
    book, ws, refs = stock_book(t)
    assert line_starting(ws, "The ageing review:") == "The ageing review: not worked out this time (KeyError)."
    assert line_starting(ws, "The damage store:") == "The damage store: not worked out this time (ZeroDivisionError)."
    assert line_starting(ws, "The order plan") == ("The order plan (each item's supplier): not worked out this time "
                                                   "(TypeError).")
    assert at(book, refs["order_units"]) == 65 and at(book, refs["main_units"]) == 505
    assert not [k for k in refs if k.startswith(("review_", "damage_"))]
    assert stub["parts"]["review"] == "not worked out this time" and stub["parts"]["plan"] == "not worked out this time"
    assert stub["parts"]["damage"] == sx.NOT_SET_UP and stub["damage"] is None
    book, ws, refs = stock_book(stub)
    assert line_starting(ws, "The damage store:") == ("The damage store: not set up (config 'warehouses.damage' names "
                                                      "the damage store).")
    assert line_starting(ws, "The ageing review:") == "The ageing review: not worked out this time."
    assert shape["parts"]["review"] == shape["parts"]["damage"] == "not worked out this time (KeyError)", shape["parts"]
    assert_clean_sheet(stock_book(shape)[1])


# ── stock_sheet ───────────────────────────────────────────────────────────────

@test("the Stock sheet: every figure typed or a formula with its value, the refs, the formats, the page")
def _():
    with data_root():
        m, _inp = model_of(World())
        t = totals_of(m, window=WEEK)
    heading = "Stock at the end of Sunday 15 March 2026"
    book, ws, refs = stock_book(t, heading)
    assert ws.cells[(0, 0)].value == heading and ws.cells[(0, 0)].fmt == "sheet_title"
    assert ws.cells[(1, 0)].value == ("Main Store · stock as on Sun 15 Mar 2026 · stock ledger to Sun 15 Mar 2026 · "
                                      "at landed cost")
    c, ag, rv = t["cover"], t["ageing"], t["review"]
    want = {"main_items": 5, "main_units": 505, "main_value": 412.8, "main_uncosted": 0,
            "cover_days": sx._whole(c["days"]), "cover_value": c["value"], "cover_above_value": c["above_value"],
            "cover_above_share": c["above_share"], "cover_last_days": sx._whole(c["last_days"]),
            "fast_month_available": 15, "fast_month_sku_days": 30, "fast_month_share": 0.5,
            "fast_window_available": 7, "fast_window_sku_days": 14, "fast_window_share": 0.5,
            "fast_out_now": 1, "fast_out_all": 1,
            "ageing_units": 505, "ageing_value": ag["value"], "over_180_value": ag["over_180_value"],
            "over_180_share": ag["over_180_share"],
            "review_not_moving": rv["counts"]["Not moving"], "review_slow": rv["counts"]["Slow"],
            "review_on_track": rv["counts"]["On track"], "review_cleared": rv["counts"]["Cleared"], "review_push": 34,
            "damage_units": 0, "damage_value": 0, "damage_per100": 0,
            "order_items": 1, "order_units": 65, "order_value": 162.5, "order_uncosted": 0}
    assert set(refs) == set(want), set(refs) ^ set(want)
    for key, value in want.items():
        assert close(at(book, refs[key]), value), (key, at(book, refs[key]), value)
        assert refs[key].startswith("Stock!$"), refs[key]
    assert (want["cover_days"], want["cover_last_days"]) == (75, 89)
    # the shares, the totals and the over-180 value are formulas; every other figure is typed
    formulas = {"cover_above_share", "fast_month_share", "fast_window_share", "ageing_units", "ageing_value",
                "over_180_value", "over_180_share"}
    for key in want:
        kind = cell_of(book, refs[key]).kind
        assert kind == ("f" if key in formulas else "n"), (key, kind)
    for key, name in (("main_value", "g_money3"), ("main_units", "g_int"), ("cover_above_share", "g_pct"),
                      ("ageing_value", "g_total_money3"), ("ageing_units", "g_total_int"), ("damage_per100", "g_dec2"),
                      ("order_value", "g_money3"), ("over_180_share", "g_pct")):
        assert cell_of(book, refs[key]).fmt == name, (key, cell_of(book, refs[key]).fmt)
    assert cell_of(book, refs["over_180_value"]).formula.count("+") == 1
    # the ageing table adds up to the main store: the five bands and the display items
    r = fx.parse_range(refs["ageing_units"], "")
    assert cell_of(book, refs["ageing_units"]).formula == f"SUM(C{r.r1 - 5}:C{r.r1})", cell_of(book, refs["ageing_units"])
    assert ws.page["fit"] == (1, 0) and not ws.page["landscape"] and "Stock" in ws.page["footer"]
    assert line_starting(ws, "Cover: stock at cost ÷ the daily cost of sales of the last 90 days; fast movers: the "
                             "best sellers by units over the 12 full months before the month.")
    assert_clean_sheet(ws)
    # a share with nothing to divide by is blank, never an error value
    t0 = dict(t, cover=dict(c, value=Decimal(0), above_value=Decimal(0), above_share=None, days=None, last_days=None))
    book, ws, refs = stock_book(t0)
    assert at(book, refs["cover_above_share"]) == "" and cell_of(book, refs["cover_days"]).kind == "b"
    assert line_starting(ws, "No item with a cost sold in the window")


# ── moves_totals ──────────────────────────────────────────────────────────────

@test("movement totals: every field; stores named as the month workbook names salesmen, vans then outlets; sums")
def _():
    given = table()
    mt = sx.moves_totals(given)
    assert (mt["first"], mt["last"], mt["basis"], mt["error"], mt["unbalanced"]) == (WEEK[0], D, None, None, 0)
    assert [(s["store"], s["ledger_name"], s["kind"]) for s in mt["stores"]] == [
        ("Van A", "Van A - Acc WH", "van"), ("Van B", "Van B - Acc WH", "van"), ("Shop East", "Shop East", "outlet")]
    # the month workbook's salesman: the warehouse less its " - Acc WH" (workbook.model.WH_SUFFIX)
    assert [s["store"] for s in mt["stores"]] == [n.replace(mdl.WH_SUFFIX, "") for n in ("Van A - Acc WH",
                                                                                          "Van B - Acc WH", "Shop East")]
    by_name = {r["store"]: r for r in given["stores"]}
    for s in mt["stores"]:
        src = by_name[s["ledger_name"]]
        assert s["units"] == src["units"] and s["bhd"] == src["bhd"], s["store"]
        assert s["units"]["check"] == s["bhd"]["check"] == 0
        assert s["uncosted_units"] == src["uncosted_units"]
    for block in ("units", "bhd"):
        assert mt["total"][block] == {k: sum((r[block][k] for r in given["stores"]), ZERO) for k in sx.MOVE_KEYS}
    assert mt["total"]["units"]["closing"] == 33 and mt["total"]["bhd"]["closing"] == Decimal("26.250")
    assert mt["total"]["uncosted_units"] == 3
    assert mt["exceptions"] == [
        {"kind": "not_received", "words": "Not received by its deadline", "count": 2, "units": 3,
         "bhd": Decimal("1.250")},
        {"kind": "qty_differs", "words": "Received in other quantities", "count": 1, "units": 4, "bhd": Decimal(2)}]
    assert mt["note"] == "3 units without a cost on file: counted in units, not valued in BHD."
    assert_clean_totals(mt)
    assert "SIO:X-26-03-7" not in json.dumps(mt, default=str)
    # the period from the arguments where the table has none; the table's own wins
    bare = {k: v for k, v in given.items() if k not in ("first", "last")}
    assert (sx.moves_totals(bare, first="2026-03-01", last="2026-03-31")["first"], sx.moves_totals(bare)["first"]) == (
        date(2026, 3, 1), None)
    assert sx.moves_totals(given, first="2026-03-01", last="2026-03-31")["first"] == WEEK[0]
    # a table's basis words, any spacing
    assert sx.moves_totals(table(basis="  at landed cost   (MRN) "))["basis"] == "at landed cost (MRN)"


@test("exceptions: the pairing summary when the table brings no list; neither; none; no store at all")
def _():
    mt = sx.moves_totals(table(exceptions=None, counts=summary(received=5, late=1, elsewhere=1, in_grace=2,
                                                               not_received=2, twin_items=1, twin_slots=2, orphans=1)))
    assert mt["exceptions"] == [
        {"kind": "not_received", "words": "Not received by its deadline", "count": 2, "units": 9, "bhd": None},
        {"kind": "elsewhere", "words": "Received by another store", "count": 1, "units": 4, "bhd": None},
        {"kind": "twins", "words": "Identical issues, fewer receipts", "count": 1, "units": 6, "bhd": None},
        {"kind": "orphan", "words": "A receipt with no issue", "count": 1, "units": 2, "bhd": None}], mt["exceptions"]
    book, ws, refs = moves_book(mt)
    assert (at(book, refs["exceptions_count"]), at(book, refs["exceptions_units"]), at(book, refs["exceptions_bhd"])) \
        == (5, 21, 0)
    r = fx.parse_range(refs["exceptions_bhd"], "")
    assert all(ws.cells[(row, 3)].kind == "b" for row in range(r.r1 - 4, r.r1))      # not valued: blank
    assert line_starting(ws, "A blank BHD: not valued (its units are counted).")
    # the summary's period stands in for a table with none
    plain = {k: v for k, v in table(exceptions=None, counts=summary()).items() if k not in ("first", "last")}
    assert (sx.moves_totals(plain)["first"], sx.moves_totals(plain)["last"]) == WEEK
    neither = sx.moves_totals(table(exceptions=None))
    assert neither["exceptions"] is None and neither["error"] is None
    assert line_starting(moves_book(neither)[1], "What is to check on the transfers:") == (
        "What is to check on the transfers: not worked out this time (the pairing gave no summary).")
    for empty in (table(exceptions=[]), table(exceptions=None, counts=summary(received=5))):
        mt = sx.moves_totals(empty)
        assert mt["exceptions"] == [], mt["exceptions"]
        book, ws, refs = moves_book(mt)
        assert line_starting(ws, "Nothing to check on the transfers in the period.") and "exceptions_count" not in refs
    none = sx.moves_totals(table(stores=[]))
    assert none["stores"] == [] and none["total"]["units"]["closing"] == 0 and none["error"] is None
    book, ws, refs = moves_book(none)
    assert line_starting(ws, "No van or outlet held or moved stock in the period.")
    assert not [k for k in refs if k.startswith(("units_", "bhd_"))]


@test("the movements module, read lazily: there, missing, raising, without period_table; no period to read")
def _():
    with moves_module(lambda first, last: table(first=None, last=None)) as mod:
        mt = sx.moves_totals(first="2026-03-09", last=D)
    assert mod.calls == [WEEK] and (mt["first"], mt["last"]) == WEEK and mt["error"] is None, mod.calls
    assert [s["store"] for s in mt["stores"]] == ["Van A", "Van B", "Shop East"]
    cases = {}
    with moves_module(missing=True):
        cases["the movements module is not there yet"] = sx.moves_totals(first=WEEK[0], last=WEEK[1])

    def raising(first, last):
        raise RuntimeError("SIO:X-26-03-7 of Van A could not be paired")
    with moves_module(raising):
        cases["RuntimeError"] = sx.moves_totals(first=WEEK[0], last=WEEK[1])
    with moves_module(bare=True):
        cases["AttributeError"] = sx.moves_totals(first=WEEK[0], last=WEEK[1])
    with moves_module(lambda first, last: "not a table"):
        cases["the movements table is not the shape it should be"] = sx.moves_totals(first=WEEK[0], last=WEEK[1])
    for why, mt in cases.items():
        assert mt == {"first": WEEK[0], "last": WEEK[1], "basis": None, "stores": [], "total": None,
                      "exceptions": None, "unbalanced": 0, "note": f"not worked out this time ({why})", "error": why}, \
            (why, mt)
        book, ws, refs = moves_book(mt)
        assert refs == {} and ws.cells[(4, 0)].value == (f"The movements of the vans and outlets: not worked out this "
                                                         f"time ({why})."), ws.cells[(4, 0)].value
        assert ws.cells[(1, 0)].value == "Vans and outlets · 9 Mar to 15 Mar 2026"
        assert_clean_sheet(ws)
    period = "no period to read: a first and a last day, the first on or before the last"
    with moves_module(lambda first, last: table()) as mod:
        for args in ({}, {"first": WEEK[0]}, {"first": D, "last": WEEK[0]}, {"first": "soon", "last": D}):
            assert sx.moves_totals(**args)["error"] == period, args
    assert mod.calls == []
    # totals that are not the contract's shape: the title and one line, what was drawn undone
    for nothing, why in ((None, ""), ({"error": "RuntimeError"}, " (RuntimeError)"), ({"stores": "x"}, ""),
                         ({"stores": [{"store": "Van A"}]}, " (KeyError)"),
                         (dict(sx.moves_totals(table()), exceptions=[{"words": "x"}]), " (KeyError)")):
        book, ws, refs = moves_book(nothing)
        assert refs == {} and ws.cells[(4, 0)].value == (f"The movements of the vans and outlets: not worked out this "
                                                         f"time{why}."), (why, ws.cells[(4, 0)].value)
        assert not [k for k in ws.cells if k[0] > 4] and not ws.merges and not ws.conds, why


@test("a table that is not the contract's shape: one sentence of this module, never a voucher of the data")
def _():
    bad_row = dict(stores()[0])
    cases = {
        "the movements table is not the shape it should be": [[], {"stores": None}, {"no": "stores"}],
        "a store's row is not a table row": [table(stores=["SIO:X-26-03-7"])],
        "a store's row names no store": [table(stores=[dict(bad_row, store="  ")])],
        "a store's row has no units": [table(stores=[dict(bad_row, units=None)])],
        "a store's row has no bhd": [table(stores=[dict(bad_row, bhd=[1, 2])])],
        "a store's units is not a number": [
            table(stores=[dict(bad_row, units=dict(bad_row["units"], sold="SIO:X-26-03-7"))]),
            table(stores=[dict(bad_row, units=dict(bad_row["units"], sold=True))])],
        "a store's bhd is not a number": [table(stores=[dict(bad_row, bhd=dict(bad_row["bhd"], closing="Infinity"))])],
        "a store's units not valued is not a number": [table(stores=[dict(bad_row, uncosted_units="few")])],
        "an exception names no kind": [table(exceptions=[{"voucher": "SIO:X-26-03-7", "units": 1}])],
        "an exception's count is not a number": [table(exceptions=[{"kind": "qty_differs", "count": "SIO:X-26-03-7"}])],
        "an exception's units is not a number": [table(exceptions=[{"kind": "qty_differs", "units": "SIO:X-26-03-7"}])],
        "an exception's BHD is not a number": [table(exceptions=[{"kind": "qty_differs", "bhd": "SIO:X-26-03-7"}])],
        "a pairing count is not a number": [table(exceptions=None, counts={"counts": {"not_received": "SIO:X-26-03-7"}})],
    }
    for why, tables in cases.items():
        for t in tables:
            mt = sx.moves_totals(t)
            assert mt["error"] == why and mt["note"] == f"not worked out this time ({why})", (why, mt["error"])
            assert mt["stores"] == [] and not leaks(mt["note"]), mt["note"]


# ── movements_sheet ───────────────────────────────────────────────────────────

@test("the Movements sheet: the check column reads 0 on every row; the total row is the sum of the rows")
def _():
    mt = sx.moves_totals(table())
    heading = "Movements in the week ending Sunday 15 March 2026"
    book, ws, refs = moves_book(mt, heading)
    assert ws.cells[(0, 0)].value == heading
    assert ws.cells[(1, 0)].value == ("Vans and outlets · 9 Mar to 15 Mar 2026 · units, and BHD at landed cost on the "
                                      "/yq-stock basis")
    for block, fmt_name, total_fmt in (("units", "g_int", "g_total_int"), ("bhd", "g_money3", "g_total_money3")):
        checks = column(book, refs[f"{block}_checks"])
        assert checks == [0, 0, 0], (block, checks)
        rows = fx.parse_range(refs[f"{block}_rows"], "")
        assert [ws.cells[(r, 0)].value for r in range(rows.r1, rows.r2 + 1)] == ["Van A", "Van B", "Shop East"]
        assert [ws.cells[(r, 1)].value for r in range(rows.r1, rows.r2 + 1)] == ["Van", "Van", "Outlet"]
        for r, s in zip(range(rows.r1, rows.r2 + 1), mt["stores"]):
            check = ws.cells[(r, sx.M_COL["check"])]
            assert check.kind == "f" and check.fmt == fmt_name and check.formula.startswith("ROUND("), check.formula
            # the check is the formula over the row's own cells: plus less minus, the closing among the minus
            plus = sum(book.value(ws.name, r, sx.M_COL[k]) for k in sx.CHECK_PLUS)
            minus = sum(book.value(ws.name, r, sx.M_COL[k]) for k in sx.CHECK_MINUS)
            assert close(plus - minus, 0) and close(book.value(ws.name, r, sx.M_COL["closing"]),
                                                    s[block]["closing"]), (block, s["store"])
            for k in sx.MOVE_KEYS:
                if k != "check":
                    cell = ws.cells[(r, sx.M_COL[k])]
                    assert cell.kind == "n" and cell.fmt == fmt_name and close(cell.value, s[block][k]), (block, k)
        for k in sx.MOVE_KEYS:
            total = cell_of(book, refs[f"{block}_{k}"])
            col = sx.M_COL[k]
            assert total.kind == "f" and total.fmt == total_fmt and total.formula.startswith("SUM(")
            down = sum(book.value(ws.name, r, col) for r in range(rows.r1, rows.r2 + 1))
            assert close(at(book, refs[f"{block}_{k}"]), down) and close(down, mt["total"][block][k]), (block, k)
    letter = fx.col_letters(sx.M_COL["check"])
    assert len([1 for ref_, opts in ws.conds if opts["criteria"] == "not equal to" and opts["format"] == "down"
                and ref_.startswith(letter)]) == 2
    assert at(book, refs["uncosted_units"]) == 3
    assert (at(book, refs["exceptions_count"]), at(book, refs["exceptions_units"]),
            close(at(book, refs["exceptions_bhd"]), 3.25)) == (3, 7, True)
    assert ws.page["landscape"] and ws.page["fit"] == (1, 0) and "Movements" in ws.page["footer"]
    assert line_starting(ws, "3 units without a cost on file")
    assert_clean_sheet(ws)


@test("a row that does not balance shows by how much: the check is the formula, not a typed nought")
def _():
    rows = stores()
    rows[0] = store("Van B - Acc WH", "van", "0.5", opening=40, received=20, back_to_main=5, to_damage=1,
                    not_received_yet=2, sold=30, customer_returns=1, closing=22)        # one unit short
    rows[0]["bhd"]["closing"] = Decimal("11.500")                                         # the BHD as it balanced
    rows[0]["units"]["check"] = rows[0]["bhd"]["check"] = ZERO                            # what a module may claim
    mt = sx.moves_totals(table(stores=rows))
    van_b = next(s for s in mt["stores"] if s["store"] == "Van B")
    assert (van_b["units"]["check"], van_b["bhd"]["check"]) == (1, 0)                      # worked out, never read
    assert mt["unbalanced"] == 1
    assert mt["note"].startswith("1 store does not balance: the Check column shows by how much"), mt["note"]
    book, ws, refs = moves_book(mt)
    assert column(book, refs["units_checks"]) == [0, 1, 0] and at(book, refs["units_check"]) == 1
    assert column(book, refs["bhd_checks"]) == [0, 0, 0]
    rows = stores()
    rows[1]["units"]["sold"] += 2
    rows[2]["bhd"]["opening"] += Decimal("0.004")
    mt = sx.moves_totals(table(stores=rows))
    assert mt["unbalanced"] == 2 and mt["note"].startswith("2 stores do not balance")
    book, _ws, refs = moves_book(mt)
    assert column(book, refs["units_checks"]) == [0, 0, -2] and column(book, refs["bhd_checks"]) == [0.004, 0, 0]


# ── what the sheets may never say ─────────────────────────────────────────────

@test("no item, code or voucher on either sheet or in either totals; every text through the wording guard")
def _():
    with data_root():
        m, _inp = model_of(World())
        m.facts["position"] = dict(POSITION)
        t = totals_of(m, window=WEEK)
    receipts = {v for it in m.items for (_d, v, _u, _x) in it.receipts}
    assert receipts and all(NUMBERED.search(v) for v in receipts)          # the pattern does find the world's vouchers
    for sheet in (stock_book(t)[1], moves_book(sx.moves_totals(table()))[1],
                  moves_book(sx.moves_totals(table(exceptions=None, counts=summary(not_received=2))))[1]):
        assert_clean_sheet(sheet)
        everything = " | ".join(texts(sheet))
        assert not any(v in everything for v in receipts | {x["voucher"] for x in EXCEPTION_ROWS})
    assert_clean_totals(t)
    # a word of the data that the guard refuses is blanked, never written
    rows = [store("Leakproof Van - Acc WH", "van", "1", opening=1, closing=1)]
    mt = sx.moves_totals(table(stores=rows, exceptions=[]))
    assert mt["stores"][0]["store"] == "Leakproof Van"
    book, ws, refs = moves_book(mt)
    shown = fx.parse_range(refs["units_rows"], "")
    assert ws.cells[(shown.r1, 0)].value == "--------- Van"
    src = (ROOT / "scripts" / "ops" / "workbook" / "sheets_extra.py").read_text(encoding="utf-8")
    for word in ("import psycopg", "import httpx", "supabase", "subprocess", "yq_load_go", ".table(", "insert",
                 "upsert", "db.rpc", "write_text"):
        assert word not in src, word


@test("the weekly builder's call: the arguments by name out of its hand-over, the sheets on a trial book")
def _():
    period = mdl.Period.week(SAT)
    offered = {"as_of": period.end, "saturday": period.end, "week_ending": period.end, "first": period.start,
               "last": period.end, "start": period.start, "end": period.end, "period": period, "data_date": D,
               "window": f"{period.start.isoformat()}..{period.end.isoformat()}", "rep": None, "prefer": "auto"}

    def call_with(fn, offer: dict):
        """week.call_with's rule: each parameter the function names, by name, from what is offered."""
        kwargs = {}
        for p in inspect.signature(fn).parameters.values():
            if p.name in offer:
                kwargs[p.name] = offer[p.name]
            else:
                assert p.default is not p.empty, f"{fn.__name__} asks for {p.name}"
        return fn(**kwargs)

    sig = inspect.signature(sx.stock_totals).parameters
    assert [(p.name, p.kind.name, p.default) for p in sig.values()] == [
        ("model", "POSITIONAL_OR_KEYWORD", None), ("as_of", "KEYWORD_ONLY", None), ("window", "KEYWORD_ONLY", None)]
    sig = inspect.signature(sx.moves_totals).parameters
    assert [(p.name, p.kind.name, p.default) for p in sig.values()] == [
        ("table", "POSITIONAL_OR_KEYWORD", None), ("first", "KEYWORD_ONLY", None), ("last", "KEYWORD_ONLY", None)]
    for fn in (sx.stock_sheet, sx.movements_sheet):
        assert list(inspect.signature(fn).parameters) == ["ws", "totals", "heading"]
    with data_root():
        with standing_in(World()):
            assert TEMP_ROOTS and paths.data_root() == TEMP_ROOTS[-1]
            st = call_with(sx.stock_totals, offered)
        with moves_module(lambda first, last: table(first=first, last=last)) as mod:
            mv = call_with(sx.moves_totals, offered)
    assert st["as_of"] == SAT and st["cut"] and "error" not in st
    assert st["fast"]["window"]["first"] == date(2026, 3, 8) and st["fast"]["window"]["days"] == 7
    assert st["parts"]["window"] == "ok"
    assert mod.calls == [(date(2026, 3, 8), SAT)] and (mv["first"], mv["last"]) == (date(2026, 3, 8), SAT)
    for kind, totals, writer in (("Stock", st, sx.stock_sheet), ("Movements", mv, sx.movements_sheet)):
        trial = bk.Book()
        ws = trial.sheet(kind)
        writer(ws, totals, f"{kind} in the week ending {SAT:%d %B %Y}")
        assert trial.evaluate() == []
        assert_clean_sheet(ws)


@test("the saved file carries every formula with its value (openpyxl)")
def _():
    import openpyxl
    with data_root():
        m, _inp = model_of(World())
        t = totals_of(m, window=WEEK)
    book = bk.Book()
    stock_refs = sx.stock_sheet(book.sheet("Stock"), t, "Stock")
    move_refs = sx.movements_sheet(book.sheet("Movements"), sx.moves_totals(table()), "Movements")
    assert book.evaluate() == []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = book.save(Path(tmp) / "Shared sheets.xlsx")
        formulas, values = openpyxl.load_workbook(path), openpyxl.load_workbook(path, data_only=True)

        def read(wb, ref: str):
            sheet, cell = ref.split("!")
            return wb[sheet.strip("'")][cell.replace("$", "")].value

        for refs in (stock_refs, move_refs):
            for key, ref in refs.items():
                if ":" in ref:
                    continue
                assert close(read(values, ref) or 0, at(book, ref) or 0), (key, read(values, ref), at(book, ref))
        for ref in (move_refs["units_checks"], move_refs["bhd_checks"]):
            first, last = ref.split("!")[1].replace("$", "").split(":")
            for row in values["Movements"][f"{first}:{last}"]:
                assert row[0].value == 0
            for row in formulas["Movements"][f"{first}:{last}"]:
                assert str(row[0].value).startswith("=ROUND(")
        assert str(read(formulas, stock_refs["cover_above_share"])).startswith("=IF(")
        assert read(values, stock_refs["main_units"]) == 505
        formulas.close()
        values.close()


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
    paths.set_data_root(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
