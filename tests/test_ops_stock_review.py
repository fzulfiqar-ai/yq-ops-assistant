"""Ops engine: the ageing review (scripts.ops.stock_review: pace, the clear target, the price cuts' effect),
on synthetic worlds.

    python -m tests.test_ops_stock_review

Same lightweight runner as tests/test_ops_stock.py, whose world helpers it borrows (Book, World, data_root,
run, model_of): no pytest, no database, no network, no .env. SUPABASE_URL, SUPABASE_KEY and DATABASE_URL
are pinned to ci.invalid BEFORE any import and scripts.ops.db is imported first; db.rpc, db.session and
ledger.for_audit are stood in for by the invented world, every statement through the read-only gate.
All names, codes, prices and numbers are invented; every folder is a temporary one.

The review world (D is Sun 15 Mar 2026, the clear target 90 days; a unit is issued from the main store to a
van and sold there the same day unless said otherwise; every cost is the latest receipt rate):
  * RS1 Slow: 200 received 1 Mar 2025 at 0.5, 94 sold in April 2025, 15 in the last 30 days (two vans):
    91 left, all over 365 days old. 15 / 30 x 7 = 3.5 a week, 91 / 0.5 a day = 182 days; push
    91 / 90 x 7 = 7.08, up to 8. A price RISE row: passed over, never cut.
  * RZ2 Not moving: 50 received 1 Feb 2025 at 0.8, never sold: push 50 / 90 x 7 = 3.89, up to 4. Its last
    cut was 1 May 2025 (318 days before D): shown, with no batch; a row with no change is passed over.
  * RU8 Not moving: 10 received 1 Feb 2025 with no value, so no cost: value None, sorted last of its status.
  * RQ3 Not moving: 90 received 1 Dec 2025 at 2.0, never sold: on the review for no sale in 90 days only
    (it is no new arrival); push 90 / 90 x 7 = 7 exactly. Its cut dated after D is left out.
  * RT4 On track: 138 received 20 Oct 2025 at 1.5; 9 + 9 sold before its cut of 15 Jan 2026 (2.000 to
    1.700), 50 sent to the van on 10 Jan; 20 + 20 + 20 sold since the cut: 60 sellable (50 in the main
    store, 10 in the van), 60 x 7 / 60 = 7 a week since the cut, 60 days to clear.
  * RX5 Cleared: 48 received 1 Nov 2025 at 3.0; 9 + 9 sold before the same cut (5.000 to 4.000), the 30
    left sold since in four stores (12 van A, 9 van B, 6 an outlet, 3 the main store).
  * RP6 Slow, cut 150 days before D (16 Oct 2025, 1.250 to 1.000): the pace is the last 30 days (6 sold:
    1.4 a week); 66 left over 180 days old, 66 / 0.2 a day = 330 days; push 66 / 90 x 7 = 5.13, up to 6.
  * RF7 a young steady seller whose last cut is 226 days before D (two more rows do not read): not on it.
  * RW9 a new arrival (1 Mar 2026), not sold yet: not on the review (too soon to call it unsold).
  * DS1 a display item (config) with a cut: never on the review.
None of those cut items received stock after its cut, and each was in stock on 30 or more of the 90 days
before it. The cut world shows the two cases where that fails (both cut on 15 Jan 2026):
  * CK1: 82 at the cut and 200 received on 1 Feb; 141 sold since: 50% of the 282 there were to sell.
  * CO2: sold out from July until 5 Jan, so in stock on 10 of the 90 days before: its pace before is no
    basis, and the pace change is left out ("not in stock before"), of the batch's too.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

from scripts.ops import db  # noqa: E402  (first, as every command does)
from scripts.ops import config, fmt, ledger, paths, stock, stock_review  # noqa: E402
# tests.test_ops_stock sets UTF-8 output when it is imported
from tests.test_ops_stock import (CODES, D, DAMAGE, L, MAIN, NAMES, SAMPLES, VAN, VAN_B, Book, World,  # noqa: E402
                                  data_root, days, model_of, raises, run, world)

stock.RAISE_PARTS = True              # a part that fails fails the test, never stands aside quietly

TESTS: list[tuple[str, object]] = []

OUTLET = "Outlet Q - Acc WH"          # an outlet: in the sellable pool, like a van
ITEMS = {  # key: (stock name, code); the ledger names an item "<stock name> <code>"
    "S": ("REV-S Cable Slow", "RS1"), "Z": ("REV-Z Holder Still", "RZ2"), "U": ("REV-U Strap Uncosted", "RU8"),
    "Q": ("REV-Q Stand Quiet", "RQ3"), "T": ("REV-T Charger Track", "RT4"), "X": ("REV-X Speaker Gone", "RX5"),
    "P": ("REV-P Case Past", "RP6"), "F": ("REV-F Fan Fresh", "RF7"), "W": ("REV-W Lamp New", "RW9"),
    "DISP": ("REV-D Counter Display", "DS1"),
}
CUTS = [  # (code, item name, changed on, was, now): what v_price_change returns
    ("RT4", "REV-T Charger Track", "2026-01-15", "2.000", "1.700"),     # 59 days before D: the pace is since it
    ("rx5", "REV-X Speaker Gone", "2026-01-15", "5.000", "4.000"),      # the same date: one batch
    ("RP6", "REV-P Case Past", "2025-10-16", "1.250", "1.000"),         # 150 days before D: a batch, pace on 30 days
    ("RZ2", "REV-Z Holder Still", "2025-05-01", "1.000", "0.800"),      # 318 days before D: shown, no batch
    ("RZ2", "REV-Z Holder Still", "2026-02-01", "0.800", "0.800"),      # no change: passed over
    ("RS1", "REV-S Cable Slow", "2026-02-01", "0.900", "1.100"),        # a rise: passed over
    ("DS1", "REV-D Counter Display", "2026-01-15", "12.000", "9.000"),  # a display item: never on the review
    ("RF7", "REV-F Fan Fresh", "2025-08-01", "0.800", "0.700"),         # 226 days before D: not on the review
    ("RF7", "REV-F Fan Fresh", "not a date", "0.800", "0.600"),         # does not read
    ("RF7", "REV-F Fan Fresh", "2026-02-01", "abc", "0.600"),           # does not read
    ("RQ3", "REV-Q Stand Quiet", "2026-03-20", "3.000", "2.500"),       # after D: left out until the ledger reaches it
    ("ZZ9", "Nobody Item", "2026-01-15", "1.000", "0.500"),             # no such item
]
BANNED = ("mark it down", "markdown", "mark down", "bundle", "cut the price", "lower the price", "reduce the price",
          "discount")


def R(key: str) -> str:
    """The ledger's name of a review item: its stock name, a space, its code."""
    name, code = ITEMS[key]
    return f"{name} {code}"


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def review_book() -> Book:
    b = Book()
    # RS1: 200 - 94 - 5 - 5 - 5 = 91
    b.receipt(R("S"), date(2025, 3, 1), 200, 100, "MRN:R-25-03-1")
    b.issue(R("S"), date(2025, 4, 1), 94).sale(R("S"), date(2025, 4, 1), 94)
    b.issue(R("S"), date(2026, 2, 20), 5).sale(R("S"), date(2026, 2, 20), 5)
    b.issue(R("S"), date(2026, 3, 1), 5, to=VAN_B).sale(R("S"), date(2026, 3, 1), 5, wh=VAN_B)
    b.issue(R("S"), date(2026, 3, 10), 5).sale(R("S"), date(2026, 3, 10), 5)
    # RZ2 and RU8: never sold (RU8's receipt states no value)
    b.receipt(R("Z"), date(2025, 2, 1), 50, 40, "MRN:R-25-02-1")
    b.receipt(R("U"), date(2025, 2, 1), 10, 0, "MRN:R-25-02-1")
    # RQ3: never sold
    b.receipt(R("Q"), date(2025, 12, 1), 90, 180, "MRN:R-25-12-1")
    # RT4: main store 138 - 9 - 9 - 50 - 20 = 50; the van 50 - 20 - 20 + 20 - 20 = 10
    b.receipt(R("T"), date(2025, 10, 20), 138, 207, "MRN:R-25-10-1")
    b.issue(R("T"), date(2025, 11, 10), 9).sale(R("T"), date(2025, 11, 10), 9)
    b.issue(R("T"), date(2025, 12, 10), 9).sale(R("T"), date(2025, 12, 10), 9)
    b.issue(R("T"), date(2026, 1, 10), 50)
    b.sale(R("T"), date(2026, 1, 20), 20).sale(R("T"), date(2026, 2, 10), 20)
    b.issue(R("T"), date(2026, 3, 1), 20).sale(R("T"), date(2026, 3, 1), 20)
    # RX5: 48 - 9 - 9 - (12 + 9 + 6 + 3) = 0 (39 in the main store on 30 Nov)
    b.receipt(R("X"), date(2025, 11, 1), 48, 144, "MRN:R-25-11-1")
    b.issue(R("X"), date(2025, 11, 15), 9).sale(R("X"), date(2025, 11, 15), 9)
    b.issue(R("X"), date(2025, 12, 1), 9).sale(R("X"), date(2025, 12, 1), 9)
    b.issue(R("X"), date(2026, 2, 1), 12).sale(R("X"), date(2026, 2, 1), 12)
    b.issue(R("X"), date(2026, 2, 1), 9, to=VAN_B).sale(R("X"), date(2026, 2, 1), 9, wh=VAN_B)
    b.issue(R("X"), date(2026, 2, 1), 6, to=OUTLET).sale(R("X"), date(2026, 2, 1), 6, wh=OUTLET)
    b.sale(R("X"), date(2026, 2, 1), 3, wh=MAIN)
    # RP6: 100 - 18 - 10 - 6 = 66
    b.receipt(R("P"), date(2025, 6, 1), 100, 100, "MRN:R-25-06-1")
    b.issue(R("P"), date(2025, 8, 1), 18).sale(R("P"), date(2025, 8, 1), 18)
    b.issue(R("P"), date(2025, 11, 1), 10).sale(R("P"), date(2025, 11, 1), 10)
    b.issue(R("P"), date(2026, 3, 1), 6).sale(R("P"), date(2026, 3, 1), 6)
    # RF7: 100 - 10 - 10 - 5 = 75 (its sale on D makes D the ledger's last day)
    b.receipt(R("F"), date(2026, 1, 1), 100, 50, "MRN:R-26-01-1")
    b.issue(R("F"), date(2026, 2, 1), 10).sale(R("F"), date(2026, 2, 1), 10)
    b.issue(R("F"), date(2026, 3, 1), 10).sale(R("F"), date(2026, 3, 1), 10)
    b.issue(R("F"), D, 5).sale(R("F"), D, 5)
    # RW9: new, not sold yet; DS1: the display item
    b.receipt(R("W"), date(2026, 3, 1), 20, 20, "MRN:R-26-03-1")
    b.receipt(R("DISP"), date(2025, 1, 1), 5, 50, "MRN:R-25-01-1")
    return b


def snapshot_of(book: Book, items: dict) -> list[dict]:
    """The stock balance that matches a book's main store: one row per item held, by its stock name."""
    names = {f"{name} {code}": name for name, code in items.values()}
    held: dict[str, Decimal] = {}
    for r in book.rows:
        if r["wh"] == MAIN:
            held[r["item"]] = held.get(r["item"], Decimal(0)) + r["qty_in"] - r["qty_out"]
    return [{"item_name": names.get(item, item), "warehouse_name": MAIN, "net_qty": str(q), "value_bhd": str(q),
             "as_of_date": D.isoformat()} for item, q in held.items() if q]


class ReviewWorld(World):
    """tests.test_ops_stock's stand-in for the command's reads, with this file's own items: its price book
    names them (item codes), the stock balance matches the book's main store, and no MRN, pricing sheet,
    supplier invoice, catalog or portal cost row is on file (every cost is the latest receipt rate)."""

    def __init__(self, book: Book | None = None, items: dict | None = None, cuts=None, **kw):
        self.book_items = dict(ITEMS if items is None else items)
        book = book or review_book()
        for key, default in (("snapshot", snapshot_of(book, self.book_items)), ("mrn", []), ("sheet", []),
                             ("invoices", []), ("portal_cost", []), ("catalog", [])):
            kw.setdefault(key, default)
        super().__init__(book=book, cuts=CUTS if cuts is None else cuts, **kw)

    def rpc(self, sql: str) -> list[dict]:
        if sql == stock.PRICE_BOOK_SQL:
            db.check_sql(sql)
            self.sql.append(sql)
            return [{"sku_code": code, "item_name": name} for name, code in self.book_items.values()]
        return super().rpc(sql)


def review_of(w: World | None = None, cfg: dict | None = None):
    with data_root(cfg):
        m, _inp = model_of(w or ReviewWorld())
    return m, m.facts["review"]


def row(facts: dict, key: str, name: str | None = None) -> dict:
    wanted = name or R(key)
    return next(r for r in facts["rows"] if r["item"].ledger_name == wanted)


def grid_text(headers, rows, aligns) -> str:
    """A table as the review prints it: fmt.grid, four spaces in."""
    return "\n".join("    " + ln for ln in fmt.grid(headers, rows, aligns).splitlines())


def screen(text: str, first: str) -> list[str]:
    lines = text.splitlines()
    return lines[next(n for n, ln in enumerate(lines) if ln.startswith(first)):]


# ── the rows, the statuses, the push ──────────────────────────────────────────

@test("the rows: over 180 days old, no sale in 90 days with stock (a new arrival aside), a cut in 180 days; no display")
def _():
    m, f = review_of()
    assert [r["item"].ledger_name for r in f["rows"]] == [R(k) for k in ("Q", "Z", "U", "P", "S", "T", "X")], \
        [r["item"].ledger_name for r in f["rows"]]
    assert {x["item"].ledger_name for x in m.facts["ageing"]["oldest"]} == {R("S"), R("Z"), R("U"), R("P")}
    rates = m.facts["fast"]["rates"]
    assert all(rates[R(k)].rate == 0 for k in ("Z", "U", "Q", "W"))     # RQ3 is on it for that alone
    assert stock.is_new(next(it for it in m.items if it.ledger_name == R("W")), D)   # RW9: too soon to judge
    for key in ("F", "W", "DISP"):
        assert R(key) not in {r["item"].ledger_name for r in f["rows"]}, key
    assert (f["clear_days"], f["data_date"], f["cuts_read"]) == (90, D, True)


@test("the four statuses, the pace, days to clear and the push (90 -> 7, 91 -> 8); the value at cost")
def _():
    _m, f = review_of()

    def figures(key):
        r = row(f, key)
        return (r["sellable"], r["main"], r["over_180"], r["pace_basis"], r["pace_days"], r["pace_units"],
                r["pace_week"], r["days_to_clear"], r["status"], r["push_week"], r["action"], r["value"])

    # 15 sold in the 30 days: 15 x 7 / 30 = 3.5 a week; 91 x 30 / 15 = 182 days; 91 / 90 x 7 = 7.08 -> 8; 91 x 0.5
    assert figures("S") == (91, 91, 91, "30 days", 30, 15, Decimal("3.5"), 182, "Slow", 8,
                            "Push sales team: 8 a week", Decimal("45.5")), figures("S")
    # 6 sold in the 30 days (its cut is 150 days old): 1.4 a week; 66 x 30 / 6 = 330 days; 66 / 90 x 7 = 5.13 -> 6
    assert figures("P") == (66, 66, 66, "30 days", 30, 6, Decimal("1.4"), 330, "Slow", 6,
                            "Push sales team: 6 a week", 66), figures("P")
    # since the cut of 15 Jan (60 days): 60 sold, 7 a week; 60 sellable (10 in the van) x 60 / 60 = 60 days; 60 x 1.5
    assert figures("T") == (60, 50, 0, "cut", 60, 60, 7, 60, "On track", None, "Keep selling", 90), figures("T")
    # nothing left: 30 sold since the cut, 3.5 a week, nought days
    assert figures("X") == (0, 0, 0, "cut", 60, 30, Decimal("3.5"), 0, "Cleared", None, "Nothing left to push", 0)
    # never sold: 90 / 90 x 7 = 7 exactly; 50 / 90 x 7 = 3.89 -> 4; 10 / 90 x 7 = 0.78 -> 1 (no cost: no value)
    assert figures("Q") == (90, 90, 0, "30 days", 30, 0, 0, None, "Not moving", 7, "Push sales team: 7 a week", 180)
    assert figures("Z") == (50, 50, 50, "30 days", 30, 0, 0, None, "Not moving", 4, "Push sales team: 4 a week", 40)
    assert figures("U") == (10, 10, 10, "30 days", 30, 0, 0, None, "Not moving", 1, "Push sales team: 1 a week", None)
    assert f["counts"] == {"Not moving": 3, "Slow": 2, "On track": 1, "Cleared": 1}
    assert f["push_units_week"] == 7 + 4 + 1 + 6 + 8 == 26
    # the rule itself: to 6 decimals, then up to a whole unit
    push = stock_review.push_per_week
    assert (push(Decimal(90), 90), push(Decimal(91), 90), push(Decimal("0.5"), 90), push(Decimal(0), 90)) == (7, 8, 1, 0)
    assert push(Decimal(-3), 90) == 0 and push(Decimal(9), 63) == 1                  # 9 / 63 x 7 = 1, not 2
    raises(ArithmeticError, lambda: push(Decimal(5), 0))                               # settings refuse a target of 0
    status = stock_review.status_of
    assert status(Decimal(0), Decimal(5), Decimal(0), 90) == "Cleared"
    assert status(Decimal(5), Decimal(0), None, 90) == "Not moving"
    assert status(Decimal(45), Decimal(15), Decimal(90), 90) == "On track"           # exactly the target: on track
    assert status(Decimal(46), Decimal(15), Decimal(92), 90) == "Slow"


@test("the clear target is config 'stock.clear_target_days': at 60 days the push and the words follow it")
def _():
    _m, f = review_of(cfg={"stock.clear_target_days": 60})
    s, t = row(f, "S"), row(f, "T")
    assert f["clear_days"] == 60 and s["push_week"] == 11                 # 91 / 60 x 7 = 10.62 -> 11
    assert t["status"] == "On track" and t["days_to_clear"] == 60         # 60 days: still within 60
    with data_root({"stock.clear_target_days": 60}):
        text = run(ReviewWorld(), "ageing")["text"]
    assert "  Clear target 60 days at today's pace:" in text and "push = sellable / 60 x 7 a week" in text


# ── the price cuts ────────────────────────────────────────────────────────────

@test("price cuts: the newest cut of each code up to D; a rise, no change, after D, unreadable, unknown are passed over")
def _():
    _m, f = review_of()
    assert row(f, "T")["cut"] == {"date": date(2026, 1, 15), "was": Decimal("2.000"), "now": Decimal("1.700"),
                                  "pct": Decimal("-0.15")}                  # (1.7 - 2) / 2
    assert row(f, "X")["cut"] == {"date": date(2026, 1, 15), "was": 5, "now": 4, "pct": Decimal("-0.2")}
    assert row(f, "P")["cut"] == {"date": date(2025, 10, 16), "was": Decimal("1.25"), "now": 1, "pct": Decimal("-0.2")}
    assert row(f, "Z")["cut"]["date"] == date(2025, 5, 1)                   # the row with no change is passed over
    for key in ("S", "Q", "U"):                                              # a rise; after D; none
        assert row(f, key)["cut"] is None, key
    assert f["cuts_later"] == 1                                              # RQ3's, dated 20 Mar
    cut_of, later = stock_review.price_cuts([
        {"code": " rt4 ", "changed_on": "2026-01-15T00:00:00", "was": "2.000", "now": "1.700"},
        {"code": "RT4", "changed_on": "2026-01-10", "was": "2.100", "now": "2.000"},     # older: the newest wins
        {"code": "RT4", "changed_on": "2026-01-15", "was": "2.000", "now": "1.800"},     # one date: the lower price
        {"code": "RS1", "changed_on": "2026-02-01", "was": "0.9", "now": "1.1"},         # a rise
        {"code": "RS1", "changed_on": "2026-02-02", "was": "1,100", "now": "0"},          # to nought: no cut
        {"code": "RQ3", "changed_on": "2026-03-16", "was": "3", "now": "2"},              # after D
        {"code": "RZ2", "changed_on": None, "was": "1", "now": "0.5"},                    # no date
        {"code": "RZ2", "changed_on": "2026-01-01", "was": "NaN", "now": "0.5"},          # not a number
        {"code": "ZZ9", "changed_on": "2026-01-01", "was": "1", "now": "0.5"},            # no such item
        "not a row",
    ], {"RT4", "RS1", "RQ3", "RZ2"}, D)
    assert set(cut_of) == {"RT4"} and cut_of["RT4"]["now"] == Decimal("1.700") and later == 1, (cut_of, later)
    assert stock_review.price_cuts(None, {"RT4"}, D) == ({}, 0)


@test("the pace: since a cut dated in the 120 days to D (119 days back), else the last 30 days (120 days back)")
def _():
    def pace_world(cut_day: date) -> ReviewWorld:
        b = Book()
        b.receipt(R("T"), date(2025, 6, 1), 500, 500, "MRN:B-25-06-1")
        for day in days(date(2025, 6, 1), D):                              # one a day, every day
            b.issue(R("T"), day, 1).sale(R("T"), day, 1)
        return ReviewWorld(book=b, cuts=[("RT4", ITEMS["T"][0], cut_day.isoformat(), "2.000", "1.500")])

    _m, f = review_of(pace_world(date(2025, 11, 16)))                     # D - 119
    t = row(f, "T")
    assert (t["pace_basis"], t["pace_days"], t["pace_units"], t["pace_week"]) == ("cut", 120, 120, 7)
    # before: one a day from 18 Aug to 15 Nov, 90 x 7 / 90 = 7: the pace did not change; the stock at the cut:
    # 500 less one a day from 1 Jun to 15 Nov (168 days) = 332, and 120 of them sold since
    assert (t["before_week"], t["change"], t["stock_at_cut"], t["sold_since_cut"]) == (7, 1, 332, 120)
    _m, f = review_of(pace_world(date(2025, 11, 15)))                     # D - 120
    t = row(f, "T")
    assert (t["pace_basis"], t["pace_days"], t["pace_units"], t["pace_week"]) == ("30 days", 30, 30, 7)
    assert (t["since_week"], t["sold_since_cut"]) == (7, 121)              # 121 x 7 / 121: since the cut all the same


@test("the 180-day window: a cut 179 days before D puts an item on the review and in a batch, 180 days does not")
def _():
    def with_cut(day: date) -> ReviewWorld:
        cuts = [c for c in CUTS if c[0] != "RF7"] + [("RF7", ITEMS["F"][0], day.isoformat(), "0.800", "0.700")]
        return ReviewWorld(cuts=cuts)

    _m, f = review_of(with_cut(date(2025, 9, 17)))                         # D - 179
    r = row(f, "F")
    # 15 sold in the 30 days (10 on 1 Mar, 5 on D): 75 x 30 / 15 = 150 days, slow; 75 / 90 x 7 = 5.83 -> 6
    assert (r["status"], r["pace_basis"], r["days_to_clear"], r["push_week"]) == ("Slow", "30 days", 150, 6)
    # first received 1 Jan 2026, after the cut: nothing at the cut, its 100 received since were on sale (25 sold:
    # 25 / (0 + 100) = 25%); in stock on none of the 90 days before: no pace to compare
    assert (r["stock_at_cut"], r["received_since_cut"], r["sold_since_cut"], r["pct_sold"]) == (0, 100, 25,
                                                                                               Decimal("0.25"))
    assert (r["before_week"], r["in_stock_before_days"], r["change"]) == (0, 0, None)
    assert [b["date"] for b in f["batches"]] == [date(2026, 1, 15), date(2025, 10, 16), date(2025, 9, 17)]
    b = f["batches"][-1]
    assert (b["received_since"], b["pct_sold"], b["in_stock_before_items"], b["compared_items"], b["change"]) == \
        (100, Decimal("0.25"), 0, 0, None)
    with data_root():
        text = run(with_cut(date(2025, 9, 17)), "ageing")["text"]
        head = run(with_cut(date(2025, 9, 17)))["text"]
        item = run(with_cut(date(2025, 9, 17)), "RF7")["text"]
    sep = next(ln for ln in text.splitlines() if ln.startswith("    17 Sep 2025 "))
    # 25 of 0 + 100; nothing in stock before: no pace change; since 25 x 7 / 180 = 0.97 a week
    assert re.split(r"\s{2,}", sep.strip()) == ["17 Sep 2025", "1", "0", "0", "1", "0", "0", "100", "25", "25.0%", "0.0",
                                                "1.0", "not in stock before"], sep
    fresh = next(ln for ln in text.splitlines() if ln.startswith("    REV-F Fan Fresh "))
    assert re.split(r"\s{2,}", fresh.strip()) == ["REV-F Fan Fresh", "RF7", "75", "75", "0", "3.5", "150", "Slow",
                                                  "17 Sep 2025 -12.5%", "not in stock before", "25 (25.0%)",
                                                  "Push sales team: 6 a week"], fresh
    assert screen(head, "AGEING  ")[2].endswith("0 slow, 0 not moving (+2 earlier cut dates: /yq-stock ageing)"), head
    assert ("  Price cut 17 Sep 2025: 0.800 to 0.700 (-12.5%); 25.0% of the stock at the cut (+100 received since) "
            "sold since; not in stock before it (in stock on 0 of the 90 days before)") in item.splitlines(), item
    _m, f = review_of(with_cut(date(2025, 9, 16)))                         # D - 180
    assert R("F") not in {r["item"].ledger_name for r in f["rows"]} and len(f["batches"]) == 2


CUT_ITEMS = {"K": ("CUT-K Cable Kept", "CK1"), "O": ("CUT-O Case Out", "CO2")}
CUT_CUTS = [("CK1", "CUT-K Cable Kept", "2026-01-15", "1.300", "1.200"),
            ("CO2", "CUT-O Case Out", "2026-01-15", "2.500", "2.000")]


def C(key: str) -> str:
    name, code = CUT_ITEMS[key]
    return f"{name} {code}"


def cut_book() -> Book:
    b = Book()
    # CK1: in stock on all the 90 days before its cut of 15 Jan; 200 more received after it
    b.receipt(C("K"), date(2025, 10, 1), 100, 100, "MRN:C-25-10-1")
    b.issue(C("K"), date(2025, 11, 15), 18).sale(C("K"), date(2025, 11, 15), 18)
    b.issue(C("K"), date(2026, 1, 20), 41).sale(C("K"), date(2026, 1, 20), 41)
    b.receipt(C("K"), date(2026, 2, 1), 200, 200, "MRN:C-26-02-1")
    b.issue(C("K"), date(2026, 2, 10), 50).sale(C("K"), date(2026, 2, 10), 50)
    b.issue(C("K"), D, 50).sale(C("K"), D, 50)
    # CO2: sold out on 1 Jul 2025 until 30 came on 5 Jan: in stock on 10 of the 90 days before the same cut
    b.receipt(C("O"), date(2025, 6, 1), 10, 20, "MRN:C-25-06-1")
    b.issue(C("O"), date(2025, 7, 1), 10).sale(C("O"), date(2025, 7, 1), 10)
    b.receipt(C("O"), date(2026, 1, 5), 30, 60, "MRN:C-26-01-1")
    b.issue(C("O"), date(2026, 1, 10), 3).sale(C("O"), date(2026, 1, 10), 3)
    b.issue(C("O"), date(2026, 2, 1), 9).sale(C("O"), date(2026, 2, 1), 9)
    b.issue(C("O"), date(2026, 3, 1), 9).sale(C("O"), date(2026, 3, 1), 9)
    return b


@test("a receipt after the cut counts in the share sold; an item out of stock before its cut has no pace change")
def _():
    def cut_world() -> ReviewWorld:
        return ReviewWorld(book=cut_book(), items=CUT_ITEMS, cuts=CUT_CUTS)

    _m, f = review_of(cut_world())
    k, o = row(f, "", C("K")), row(f, "", C("O"))
    # CK1: 100 - 18 = 82 at the cut, 200 received on 1 Feb, 41 + 50 + 50 = 141 sold since: 141 / (82 + 200) = 50%
    # (over the stock at the cut alone it would read 172%)
    assert (k["stock_at_cut"], k["received_since_cut"], k["sold_since_cut"], k["pct_sold"]) == (82, 200, 141,
                                                                                               Decimal("0.5"))
    # in stock on all 90 days before it, 18 sold then: 18 x 7 / 90 = 1.4 a week; since 141 x 7 / 60 = 16.45: 11.75x
    assert (k["in_stock_before_days"], k["before_week"], k["since_week"], k["change"]) == \
        (90, Decimal("1.4"), Decimal("16.45"), Decimal("11.75"))
    assert (k["sellable"], k["days_to_clear"], k["status"]) == (141, 60, "On track")        # 141 x 60 / 141
    # CO2: in stock on 10 of the 90 days before (5 to 14 Jan), 3 sold then: 3 x 7 / 90 = 0.23 a week against
    # 18 x 7 / 60 = 2.1 since would read 9.0x: no change; 30 - 3 = 27 at the cut, none received, 18 sold: 18 / 27
    assert (o["in_stock_before_days"], o["before_week"], o["since_week"], o["change"]) == \
        (10, Decimal(21) / 90, Decimal("2.1"), None)
    assert (o["stock_at_cut"], o["received_since_cut"], o["sold_since_cut"], o["pct_sold"]) == (27, 0, 18,
                                                                                               Decimal(18) / 27)
    (b,) = f["batches"]
    # 82 + 27 at the cut, 200 received, 141 + 18 sold: 159 / 309 = 51.5%; the change on CK1 alone: 16.45 / 1.4
    assert (b["items"], b["stock_at_cut"], b["received_since"], b["sold"]) == (2, 109, 200, 159)
    assert b["pct_sold"] == Decimal(159) / 309 and (b["in_stock_before_items"], b["compared_items"]) == (1, 1)
    assert (b["compared_before_week"], b["compared_since_week"], b["change"]) == \
        (Decimal("1.4"), Decimal("16.45"), Decimal("11.75"))
    assert (b["before_week"], b["since_week"]) == (Decimal("1.4") + Decimal(21) / 90, Decimal("18.55"))   # all items
    with data_root():
        head = run(cut_world())["text"]
        text = run(cut_world(), "ageing")["text"]
        items = {code: run(cut_world(), code)["text"] for code in ("CK1", "CO2")}
    assert screen(head, "AGEING  ")[1:3] == [
        "  Clear target 90 days at today's pace: 2 on track, 0 slow, 0 not moving, 0 cleared; nothing for the sales "
        "team to push",
        "  Price cut 15 Jan (2 items): 51.5% of the stock at the cut (+200 received since) sold since; 11.8x the pace "
        "before (1 of the 2 items); 0 cleared, 2 on track, 0 slow, 0 not moving"], screen(head, "AGEING  ")[1:3]

    def cells(start: str) -> list[str]:
        return re.split(r"\s{2,}", next(ln for ln in text.splitlines() if ln.startswith("    " + start)).strip())

    assert cells("15 Jan ") == ["15 Jan", "2", "0", "2", "0", "0", "109", "200", "159", "51.5%", "1.6", "18.6",
                                "11.8x (1 of 2)"], cells("15 Jan ")
    assert cells("CUT-K ") == ["CUT-K Cable Kept", "CK1", "141", "141", "0", "16.5", "60", "On track", "15 Jan -7.7%",
                               "1.4", "141 (50.0%)", "Keep selling"], cells("CUT-K ")
    assert cells("CUT-O ") == ["CUT-O Case Out", "CO2", "9", "9", "0", "2.1", "30", "On track", "15 Jan -20.0%",
                               "not in stock before", "18 (66.7%)", "Keep selling"], cells("CUT-O ")
    got = {code: [ln for ln in t.splitlines() if ln.startswith("  Price cut ")] for code, t in items.items()}
    assert got == {
        "CK1": ["  Price cut 15 Jan: 1.300 to 1.200 (-7.7%); 50.0% of the stock at the cut (+200 received since) sold "
                "since; 11.8x the pace of the 90 days before"],
        "CO2": ["  Price cut 15 Jan: 2.500 to 2.000 (-20.0%); 66.7% of the stock at the cut sold since; not in stock "
                "before it (in stock on 10 of the 90 days before)"]}, got


FEW_ITEMS = {"L": ("FEW-L Lamp Few", "FL1")}


def few_world(before: int) -> ReviewWorld:
    """FL1: in stock on all the 90 days before its cut of 1 Feb 2026, `before` sold in them, 43 sold since."""
    item = "FEW-L Lamp Few FL1"
    b = Book()
    b.receipt(item, date(2025, 9, 1), 100, 100, "MRN:F-25-09-1")
    b.issue(item, date(2025, 12, 1), before).sale(item, date(2025, 12, 1), before)
    b.issue(item, date(2026, 2, 10), 20).sale(item, date(2026, 2, 10), 20)
    b.issue(item, date(2026, 3, 1), 20).sale(item, date(2026, 3, 1), 20)
    b.issue(item, D, 3).sale(item, D, 3)
    return ReviewWorld(book=b, items=FEW_ITEMS, cuts=[("FL1", "FEW-L Lamp Few", "2026-02-01", "1.000", "0.900")])


@test("too few sold before the cut (12, in stock all 90 days): no pace change for the item or its batch; 13 is enough")
def _():
    _m, f = review_of(few_world(12))
    r = f["rows"][0]
    # in stock on all 90 days before the cut (3 Nov to 31 Jan), 12 sold then: 12 x 7 / 90 = 0.93 a week; against
    # 43 x 7 / 43 = 7 since it would read 7.5x, on 12 units: no change
    assert (r["in_stock_before_days"], r["before_units"], r["before_week"], r["since_week"], r["change"]) == \
        (90, 12, Decimal(84) / 90, 7, None)
    # 100 - 12 = 88 at the cut, 43 sold since: 43 / 88 = 48.9%; 45 left at one a day: 45 days, on track
    assert (r["stock_at_cut"], r["sold_since_cut"], r["pct_sold"]) == (88, 43, Decimal(43) / 88)
    assert (r["sellable"], r["pace_days"], r["days_to_clear"], r["status"]) == (45, 43, 45, "On track")
    (b,) = f["batches"]
    assert (b["in_stock_before_items"], b["compared_items"], b["change"]) == (1, 0, None)
    with data_root():
        head = run(few_world(12))["text"]
        text = run(few_world(12), "ageing")["text"]
        item = run(few_world(12), "FL1")["text"]
    assert screen(head, "AGEING  ")[2] == (
        "  Price cut 01 Feb (1 item): 48.9% of the stock at the cut sold since; too few sold in the 90 days before to "
        "compare; 0 cleared, 1 on track, 0 slow, 0 not moving"), screen(head, "AGEING  ")[2]
    batch_row = next(ln for ln in text.splitlines() if ln.startswith("    01 Feb "))
    assert re.split(r"\s{2,}", batch_row.strip()) == ["01 Feb", "1", "0", "1", "0", "0", "88", "0", "43", "48.9%",
                                                      "0.9", "7.0", "too few before"], batch_row
    assert ("  Price cut 01 Feb: 1.000 to 0.900 (-10.0%); 48.9% of the stock at the cut sold since; too few sold in the "
            "90 days before to compare the pace (12)") in item.splitlines(), item
    # 13 sold before: enough; 7 / (13 x 7 / 90) = 90 / 13 = 6.9x, and the batch rests on it
    _m, f = review_of(few_world(13))
    r = f["rows"][0]
    assert r["before_units"] == 13 and r["change"] == Decimal(7) / (Decimal(91) / 90) and str(r["change"])[:5] == "6.923"
    assert f["batches"][0]["compared_items"] == 1 and f["batches"][0]["change"] == r["change"]


@test("the cut's effect: a week in the 90 days before and since, the change, the stock at the cut, the share sold")
def _():
    _m, f = review_of()
    t = row(f, "T")
    # before: 9 (10 Nov) + 9 (10 Dec) in 17 Oct .. 14 Jan: 18 x 7 / 90 = 1.4; since: 60 x 7 / 60 = 7; 7 / 1.4 = 5
    # the stock at the cut: 138 - 18 = 120 at the end of 14 Jan (the 50 sent to the van on 10 Jan count); 60 sold: 50%
    assert (t["before_week"], t["since_week"], t["change"]) == (Decimal("1.4"), 7, 5)
    assert (t["stock_at_cut"], t["sold_since_cut"], t["pct_sold"]) == (120, 60, Decimal("0.5"))
    # in stock from its receipt on 20 Oct: 87 of the 90 days before the cut (enough to compare); no receipt since
    assert (t["in_stock_before_days"], t["received_since_cut"]) == (87, 0)
    x = row(f, "X")
    # before: 9 (15 Nov) + 9 (1 Dec) = 18, at least 13: 1.4 a week; since: 30 x 7 / 60 = 3.5; 3.5 / 1.4 = 2.5;
    # at the cut 48 - 18 = 30, all 30 sold: 100%
    assert (x["before_units"], x["before_week"], x["since_week"], x["change"]) == (18, Decimal("1.4"), Decimal("3.5"),
                                                                                  Decimal("2.5"))
    assert (x["stock_at_cut"], x["sold_since_cut"], x["pct_sold"]) == (30, 30, 1)
    assert (x["in_stock_before_days"], x["received_since_cut"]) == (75, 0)        # in stock from 1 Nov
    p = row(f, "P")
    # before: 18 (1 Aug) in 18 Jul .. 15 Oct: 1.4; since 16 Oct (151 days): 10 + 6 = 16, 16 x 7 / 151 = 0.74 a week
    # (the pace since the cut, though its pace window is the last 30 days); 0.74 / 1.4 = 0.53; at the cut 100 - 18 = 82
    assert p["before_week"] == Decimal("1.4") and p["since_week"] == Decimal(16) * 7 / 151
    assert p["change"] == p["since_week"] / Decimal("1.4") and str(p["change"])[:6] == "0.5298"
    assert (p["stock_at_cut"], p["sold_since_cut"], p["pct_sold"]) == (82, 16, Decimal(16) / 82)
    assert (p["in_stock_before_days"], p["received_since_cut"]) == (90, 0)
    z = row(f, "Z")
    # cut 1 May 2025, never sold: in stock on 89 of the 90 days before (from 1 Feb) but none sold then, fewer
    # than 13 (no change), its 50 at the cut, none sold since
    assert (z["before_week"], z["change"], z["stock_at_cut"], z["sold_since_cut"], z["pct_sold"]) == (0, None, 50, 0, 0)
    assert z["before_units"] == 0 and t["before_units"] == 18 and p["before_units"] == 18
    assert (z["in_stock_before_days"], z["received_since_cut"]) == (89, 0)
    s = row(f, "S")
    assert all(s[k] is None for k in ("cut", "before_week", "since_week", "change", "stock_at_cut", "sold_since_cut",
                                      "pct_sold", "in_stock_before_days", "received_since_cut")), s


@test("batches: one per cut date in the 180 days to D, newest first, with the statuses, stock at the cut and pace")
def _():
    _m, f = review_of()
    assert [b["date"] for b in f["batches"]] == [date(2026, 1, 15), date(2025, 10, 16)]   # RZ2's cut: too old
    b1, b2 = f["batches"]
    # 15 Jan: RT4 and RX5 (the display item's cut left out); at the cut 120 + 30, sold 60 + 30: 60%;
    # before 1.4 + 1.4 = 2.8 a week, since 7 + 3.5 = 10.5: 3.75x
    assert b1["items"] == 2 and b1["counts"] == {"Not moving": 0, "Slow": 0, "On track": 1, "Cleared": 1}
    assert (b1["stock_at_cut"], b1["sold"], b1["pct_sold"]) == (150, 90, Decimal("0.6"))
    assert (b1["before_week"], b1["since_week"], b1["change"]) == (Decimal("2.8"), Decimal("10.5"), Decimal("3.75"))
    # nothing received since; both in stock and selling 13 or more before: the change rests on both
    assert (b1["received_since"], b1["in_stock_before_items"], b1["compared_items"]) == (0, 2, 2)
    assert (b1["compared_before_week"], b1["compared_since_week"]) == (Decimal("2.8"), Decimal("10.5"))
    # 16 Oct 2025: RP6 alone; its since is the pace since the cut, never nought for a batch older than 120 days
    assert b2["items"] == 1 and b2["counts"]["Slow"] == 1 and (b2["stock_at_cut"], b2["sold"]) == (82, 16)
    assert b2["since_week"] == Decimal(16) * 7 / 151 and b2["change"] == b2["since_week"] / Decimal("1.4")
    assert (b2["received_since"], b2["in_stock_before_items"], b2["compared_items"]) == (0, 1, 1)


# ── the trend ─────────────────────────────────────────────────────────────────

TREND_ITEMS = {"A": ("TRD-A Cable Aged", "TA1"), "Y": ("TRD-Y Stand Young", "TY2"),
               "U": ("TRD-U Clip Uncosted", "TU3"), "V": ("TRD-V Clip Also", "TV4")}


def G(key: str) -> str:
    name, code = TREND_ITEMS[key]
    return f"{name} {code}"


def trend_book() -> Book:
    b = Book()
    b.receipt(G("A"), date(2025, 1, 10), 100, 80, "MRN:G-25-01-1")              # 0.8 a unit then
    b.receipt(G("U"), date(2025, 1, 10), 20, 0, "MRN:G-25-01-1")                 # no value: no cost on file
    b.receipt(G("V"), date(2025, 1, 10), 20, 0, "MRN:G-25-01-1")
    b.receipt(G("Y"), date(2025, 9, 1), 60, 120, "MRN:G-25-09-1")                # 2.0 a unit
    b._row(G("A"), date(2025, 9, 15), ledger.COUNT_OVER, MAIN, "EX:G-1", qin=30)  # found on a count: no receipt
    b.issue(G("A"), date(2025, 10, 1), 40).sale(G("A"), date(2025, 10, 1), 40)
    b.receipt(G("A"), date(2025, 11, 20), 50, 50, "MRN:G-25-11-1")               # 1.0 a unit: today's cost
    b.issue(G("A"), D, 5).sale(G("A"), D, 5)
    return b


@test("the trend: the share of value over 180 days at the last six month ends, at today's cost, oldest first")
def _():
    _m, f = review_of()
    # the review world (display item out, RU8 without a cost out; costs S 0.5, Z 0.8, Q 2, T 1.5, X 3, P 1, F 0.5):
    #   30 Sep: S 106 + Z 50 old (53 + 40), P 82 (121 days: not old)                    93 of 175
    #   31 Oct: + T 138 (207)                                                             93 of 382
    #   30 Nov: S 53 + Z 40 + P 72 (182 days: old) = 165; T 129 (193.5), X 39 (117)      165 of 475.5
    #   31 Dec: + Q 90 (180), T 120 (180), X 30 (90)                                      165 of 615
    #   31 Jan: T 70 (105), F 100 (50)                                                    165 of 590
    #   28 Feb: S 101 (50.5 old), X 0, F 90 (45)                                          162.5 of 492.5
    expect = [(date(2025, 9, 30), "93", "175"), (date(2025, 10, 31), "93", "382"), (date(2025, 11, 30), "165", "475.5"),
              (date(2025, 12, 31), "165", "615"), (date(2026, 1, 31), "165", "590"),
              (date(2026, 2, 28), "162.5", "492.5")]
    got = [(t["month_end"], t["over_180_value"], t["value"]) for t in f["trend"]]
    assert got == [(e, Decimal(o), Decimal(v)) for e, o, v in expect], got
    assert [fmt.pct(t["over_180_value"], t["value"]) for t in f["trend"]] == \
        ["53.1%", "24.3%", "34.7%", "26.8%", "28.0%", "33.0%"]
    assert [t["share"] for t in f["trend"]][0] == Decimal(93) / Decimal(175)
    assert all(t["uncosted_units"] == 10 and t["no_receipt_units"] == 0 for t in f["trend"])
    # now: the AGEING line's own figure: S 45.5 + Z 40 + P 66 old of 464 (+ Q 180, T 75, F 37.5, W 20)
    assert f["now"] == {"over_180_value": Decimal("151.5"), "value": Decimal("464"),
                        "share": Decimal("151.5") / Decimal("464")}
    # a world of its own: units no receipt explains, today's cost, exactly 180 days, no cost
    _m, f = review_of(ReviewWorld(book=trend_book(), items=TREND_ITEMS, cuts=[]))
    #   30 Sep: A 100 + 30 found = 130 at 1.0 (today's cost, not the receipt's 0.8): 100 old (263 days), 30 on no
    #           receipt (stock, not old); Y 60 x 2 = 120, 29 days                       100 of 250
    #   31 Oct: A 90 on the January receipt (old)                                        90 of 210
    #   30 Nov to 28 Feb: A 140 = 50 (20 Nov) + 90 old; Y at 180 days on 28 Feb: not old 90 of 260
    got = [(t["month_end"], t["over_180_value"], t["value"], t["no_receipt_units"]) for t in f["trend"]]
    assert got == [(date(2025, 9, 30), 100, 250, 30), (date(2025, 10, 31), 90, 210, 0), (date(2025, 11, 30), 90, 260, 0),
                   (date(2025, 12, 31), 90, 260, 0), (date(2026, 1, 31), 90, 260, 0), (date(2026, 2, 28), 90, 260, 0)], got
    assert all(t["uncosted_units"] == 40 for t in f["trend"])                # TU3 and TV4, left out of both
    assert f["trend"][0]["share"] == Decimal("0.4") and f["trend"][1]["share"] == Decimal(90) / Decimal(210)


@test("sorting: Not moving, Slow, On track, Cleared; then the value, the highest first (no cost last); then the name")
def _():
    _m, f = review_of()
    assert [(r["status"], r["value"]) for r in f["rows"]] == [
        ("Not moving", 180), ("Not moving", 40), ("Not moving", None), ("Slow", 66), ("Slow", Decimal("45.5")),
        ("On track", 90), ("Cleared", 0)]
    _m, f = review_of(ReviewWorld(book=trend_book(), items=TREND_ITEMS, cuts=[]))
    # TY2 120 at cost; TU3 and TV4 no cost: by name; TA1 slow (5 sold in 30 days, 135 left: 810 days), value 135
    assert [r["item"].ledger_name for r in f["rows"]] == [G("Y"), G("U"), G("V"), G("A")]
    assert row(f, "", G("A"))["status"] == "Slow" and row(f, "", G("A"))["value"] == 135


# ── the screens ───────────────────────────────────────────────────────────────

@test("the headline: three lines under AGEING (the statuses and the push, the newest cut batch, the trend)")
def _():
    with data_root():
        text = run(ReviewWorld())["text"]
    assert screen(text, "AGEING  ")[:4] == [
        "AGEING  32.7% of the value at cost has been in stock over 180 days (units on their newest supplier receipts). "
        "Full table: /yq-stock ageing",
        "  Clear target 90 days at today's pace: 1 on track, 2 slow, 3 not moving, 1 cleared; the sales team needs "
        "26 units a week for the slow and the not moving",
        "  Price cut 15 Jan (2 items): 60.0% of the stock at the cut sold since; 3.8x the pace before; 1 cleared, "
        "1 on track, 0 slow, 0 not moving (+1 earlier cut date: /yq-stock ageing)",
        "  Value over 180 days: 32.7% now; 33.0% at the end of Feb, 28.0% at the end of Jan",
    ], screen(text, "AGEING  ")[:4]
    for name in (VAN, VAN_B, OUTLET):
        assert name not in text, name
    # nothing to review, no cut: still three lines
    b = Book()
    b.receipt(R("F"), date(2026, 1, 1), 100, 50, "MRN:R-26-01-1")
    b.issue(R("F"), date(2026, 2, 1), 10).sale(R("F"), date(2026, 2, 1), 10)
    b.issue(R("F"), D, 5).sale(R("F"), D, 5)
    with data_root():
        text = run(ReviewWorld(book=b, cuts=[]))["text"]
    assert screen(text, "AGEING  ")[1:4] == [
        "  Clear target 90 days: no old, unsold or price-cut item to review.",
        "  No price cut in the 180 days to Sun 15 Mar.",
        "  Value over 180 days: 0.0% now; 0.0% at the end of Feb, 0.0% at the end of Jan"]


@test("the ageing view: the REVIEW block (batches, every item, the trend); no price advice, no store or salesman")
def _():
    with data_root():
        text = run(ReviewWorld(), "ageing")["text"]
    review = screen(text, "REVIEW  ")
    assert review[:2] == [
        "REVIEW  old, unsold and price-cut items against the clear target of 90 days at today's pace; the only lever "
        "is the sales team (a weekly push per item)",
        "  Clear target 90 days at today's pace: 1 on track, 2 slow, 3 not moving, 1 cleared; the sales team needs "
        "26 units a week for the slow and the not moving"]
    batches = grid_text(["Price cut", "Items", "Cleared", "On track", "Slow", "Not moving", "Stock at cut",
                         "Received since", "Sold since", "Sold", "Before a week", "Since a week", "Pace"], [
        ["15 Jan", "2", "1", "1", "0", "0", "150", "0", "90", "60.0%", "2.8", "10.5", "3.8x"],
        ["16 Oct 2025", "1", "0", "0", "1", "0", "82", "0", "16", "19.5%", "1.4", "0.7", "0.5x"]], "lrrrrrrrrrrrr")
    assert batches in text, text
    items = grid_text(["Item", "Code", "Sellable", "Main", "Over 180 d", "A week", "Days to clear", "Status", "Cut",
                       "Before a week", "Sold since cut", "Action"], [
        ["REV-Q Stand Quiet", "RQ3", "90", "90", "0", "0.0", "no sale", "Not moving", "never", "", "",
         "Push sales team: 7 a week"],
        ["REV-Z Holder Still", "RZ2", "50", "50", "50", "0.0", "no sale", "Not moving", "01 May 2025 -20.0%", "0.0",
         "0 (0.0%)", "Push sales team: 4 a week"],
        ["REV-U Strap Uncosted", "RU8", "10", "10", "10", "0.0", "no sale", "Not moving", "never", "", "",
         "Push sales team: 1 a week"],
        ["REV-P Case Past", "RP6", "66", "66", "66", "1.4", "330", "Slow", "16 Oct 2025 -20.0%", "1.4", "16 (19.5%)",
         "Push sales team: 6 a week"],
        ["REV-S Cable Slow", "RS1", "91", "91", "91", "3.5", "182", "Slow", "never", "", "",
         "Push sales team: 8 a week"],
        ["REV-T Charger Track", "RT4", "60", "50", "0", "7.0", "60", "On track", "15 Jan -15.0%", "1.4", "60 (50.0%)",
         "Keep selling"],
        ["REV-X Speaker Gone", "RX5", "0", "0", "0", "3.5", "-", "Cleared", "15 Jan -20.0%", "1.4", "30 (100.0%)",
         "Nothing left to push"]], "llrrrrrllrrl")
    assert items in text, text
    trend = grid_text(["Month end", "Over 180 d at cost", "Stock at cost", "Share"], [
        ["30 Sep 2025", "93.000", "175.000", "53.1%"], ["31 Oct 2025", "93.000", "382.000", "24.3%"],
        ["30 Nov 2025", "165.000", "475.500", "34.7%"], ["31 Dec 2025", "165.000", "615.000", "26.8%"],
        ["31 Jan", "165.000", "590.000", "28.0%"], ["28 Feb", "162.500", "492.500", "33.0%"],
        ["15 Mar (now)", "151.500", "464.000", "32.7%"]], "lrrr")
    assert trend in text, text
    for line in ("  1 price cut dated after Sun 15 Mar is left out until the ledger reaches it.",
                 "  Sellable = the main store, the vans, the outlets and goods moving between them; damage and samples "
                 "left out.",
                 "  A week = units sold since the item's price cut when it fell in the last 120 days, else in the last 30 "
                 "days; days to clear = sellable / that pace; push = sellable / 90 x 7 a week, rounded up.",
                 "  Before a week = units a week in the 90 days before the cut ('not in stock before' when the item was "
                 "in stock on fewer than 30 of them); sold since cut = units sold since it, and their share of the "
                 "stock at the cut plus what was received since.",
                 "  Pace = since / before a week, over the items in stock on 30 or more of the 90 days before the cut "
                 "that sold in them."):
        assert line in text.splitlines(), line
    for word in BANNED:                                     # the only lever is the sales team (the owner, 20-Oct)
        assert word not in text.lower(), word
    assert "Van" not in text and VAN not in text and VAN_B not in text and OUTLET not in text
    for line in text.splitlines():
        fmt.careful(line)


@test("one item: /yq-stock <code> carries its review line, and its price cut line when it has one")
def _():
    with data_root():
        texts = {code: run(ReviewWorld(), code)["text"] for code in ("RS1", "RT4", "RP6", "RX5", "RQ3", "RZ2", "RF7")}
    expect = {
        "RS1": ["  Ageing review: Slow; 91 units sellable (main store 91); 3.5 a week over the last 30 days; 182 days to "
                "clear against the 90-day target; push sales team: 8 a week"],
        "RT4": ["  Ageing review: On track; 60 units sellable (main store 50); 7.0 a week since the price cut on 15 Jan; "
                "60 days to clear against the 90-day target; keep selling",
                "  Price cut 15 Jan: 2.000 to 1.700 (-15.0%); 50.0% of the stock at the cut sold since; 5.0x the pace of "
                "the 90 days before"],
        "RP6": ["  Ageing review: Slow; 66 units sellable (main store 66); 1.4 a week over the last 30 days; 330 days to "
                "clear against the 90-day target; push sales team: 6 a week",
                "  Price cut 16 Oct 2025: 1.250 to 1.000 (-20.0%); 19.5% of the stock at the cut sold since; 0.5x the "
                "pace of the 90 days before"],
        "RX5": ["  Ageing review: Cleared; 0 units sellable (main store 0); nothing left to push",
                "  Price cut 15 Jan: 5.000 to 4.000 (-20.0%); 100.0% of the stock at the cut sold since; 2.5x the pace "
                "of the 90 days before"],
        "RQ3": ["  Ageing review: Not moving; 90 units sellable (main store 90); none sold over the last 30 days; push "
                "sales team: 7 a week"],
        "RZ2": ["  Ageing review: Not moving; 50 units sellable (main store 50); none sold over the last 30 days; push "
                "sales team: 4 a week",
                "  Price cut 01 May 2025: 1.000 to 0.800 (-20.0%); 0.0% of the stock at the cut sold since; too few sold "
                "in the 90 days before to compare the pace (0)"],
        "RF7": [],
    }
    for code, lines in expect.items():
        got = [ln for ln in texts[code].splitlines() if ln.startswith(("  Ageing review:", "  Price cut "))]
        assert got == lines, (code, got)
        assert texts[code].startswith(f"ITEM  {code}: ") and VAN not in texts[code] and VAN_B not in texts[code]


@test("cuts not read: every item is never cut, the cut-only items leave the review, and the screen says so")
def _():
    w = ReviewWorld(fail={stock.PRICE_CUT_SQL: RuntimeError("down")})
    m, f = review_of(w)
    assert (f["cuts_read"], f["cuts_later"], f["batches"]) == (False, 0, [])
    assert [r["item"].ledger_name for r in f["rows"]] == [R(k) for k in ("Q", "Z", "U", "P", "S")]   # RT4, RX5: cut only
    assert all(r["cut"] is None and r["pace_basis"] == "30 days" for r in f["rows"])
    assert (row(f, "P")["status"], row(f, "P")["push_week"], f["push_units_week"]) == ("Slow", 6, 26)
    again = stock_review.review_facts(m, m.flows, None)                        # the same, called directly
    assert again["cuts_read"] is False and [r["item"] for r in again["rows"]] == [r["item"] for r in f["rows"]]
    with data_root():
        text = run(ReviewWorld(fail={stock.PRICE_CUT_SQL: RuntimeError("down")}))["text"]
    assert "  The price cuts were not read this time: every item counts as never cut." in text
    assert "Not read this time: the price book's changes (price cuts) (RuntimeError)." in text


@test("the saved facts: items by name and code, never a seller; the sellers stay for the owner's own file")
def _():
    _m, f = review_of()
    assert row(f, "X")["sellers"] == [(VAN, 12), (VAN_B, 9), (OUTLET, 6)]   # the 3 sold in the main store: fourth
    assert row(f, "S")["sellers"] == [(VAN, 10), (VAN_B, 5)] and row(f, "T")["sellers"] == [(VAN, 60)]
    out = stock_review.facts_json(f)
    text = json.dumps(stock._jsonable(out))
    for word in ("sellers", VAN, VAN_B, OUTLET):
        assert word not in text, word
    back = json.loads(text)
    assert (back["rows"][0]["item"], back["rows"][0]["code"], back["rows"][0]["push_week"]) == ("REV-Q Stand Quiet",
                                                                                               "RQ3", 7)
    assert back["batches"][0]["date"] == "2026-01-15" and back["counts"]["Not moving"] == 3
    assert "sellers" in f["rows"][0]                                          # facts_json never changes the facts
    with data_root() as root:
        run(ReviewWorld(), "ageing")
        saved = json.loads((paths.exports_dir(D) / "stock-ageing.json").read_text(encoding="utf-8"))
        assert str(paths.exports_dir(D)).startswith(str(root))
    review = json.dumps(saved["review"])
    assert saved["review"]["rows"][0]["code"] == "RQ3" and "sellers" not in review and VAN not in review


# ── the standard world, and what the sellable stock leaves out ───────────────

@test("the standard world (tests/test_ops_stock): ITEM-C not moving, ITEM-A slow; a cut on ITEM-B, units in a van")
def _():
    cuts = [(CODES["B"], NAMES["B"], "2026-02-15", "3.000", "2.700")]
    _m, f = review_of(World(book=world(), cuts=cuts))
    assert [r["item"].ledger_name for r in f["rows"]] == [L("C"), L("A"), L("B")]   # F sold out, N new, the display
    b = row(f, "", L("B"))
    # 130 received, 75 sold: 55 sellable, 35 of them still in the second van: the main store holds 20
    assert (b["sellable"], b["main"]) == (55, 20)
    # since 15 Feb (29 days): one a day, 7 a week; 55 x 29 / 29 = 55 days: on track
    assert (b["pace_basis"], b["pace_days"], b["pace_units"], b["pace_week"], b["days_to_clear"], b["status"]) == \
        ("cut", 29, 29, 7, 55, "On track")
    # before: 30 Jan .. 14 Feb, 16 sold: 16 x 7 / 90; at the cut 130 - 30 - 16 = 84, 29 sold since
    assert b["before_week"] == Decimal(16) * 7 / 90 and (b["stock_at_cut"], b["sold_since_cut"]) == (84, 29)
    # sold out from July to its receipt of 30 Jan: in stock on 16 of the 90 days before the cut, so the pace
    # before (1.2 a week) is no basis: no change (it would read 5.6x)
    assert (b["in_stock_before_days"], b["change"], b["received_since_cut"]) == (16, None, 0)
    assert f["batches"][0]["change"] is None and f["batches"][0]["in_stock_before_items"] == 0
    a = row(f, "", L("A"))
    # 1200 received, 816 sold: 384 sellable; the main store 394 (the van that sent 10 back held none)
    # 60 in 30 days: 384 x 30 / 60 = 192 days, slow; 384 / 90 x 7 = 29.87 -> 30
    assert (a["sellable"], a["main"], a["days_to_clear"], a["status"], a["push_week"]) == (384, 394, 192, "Slow", 30)
    c = row(f, "", L("C"))
    assert (c["status"], c["push_week"], c["value"], c["last_sale"]) == ("Not moving", 4, 40, None)
    assert f["counts"] == {"Not moving": 1, "Slow": 1, "On track": 1, "Cleared": 0} and f["push_units_week"] == 34


@test("the sellable stock leaves the damage store and the samples out, and keeps goods on their way to a van")
def _():
    b = review_book()
    b.to_damage(R("S"), date(2026, 3, 12), 1)                            # from the main store to the damage store
    b.move(R("S"), date(2026, 3, 12), 2, MAIN, SAMPLES)                   # out to the samples store
    b.move(R("S"), date(2026, 3, 13), 4, MAIN, VAN_B, received=False)     # on its way to a van: still sellable
    m, f = review_of(ReviewWorld(book=b))
    s = row(f, "S")
    # 91 - 1 - 2 = 88 sellable; the main store 88 - 4 = 84, all of it over 365 days old
    assert (s["sellable"], s["main"], s["over_180"]) == (88, 84, 84)
    # 88 x 30 / 15 = 176 days: still slow; 88 / 90 x 7 = 6.84 -> 7
    assert (s["days_to_clear"], s["status"], s["push_week"]) == (176, "Slow", 7)
    assert m.flows.damage_store == DAMAGE and m.flows.get(R("S")).damage_at(D) == 1


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
    config.use(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
