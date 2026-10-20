"""Ops engine: the damage store (scripts.ops.stock_damage), on a synthetic world.

    python -m tests.test_ops_stock_damage

Same lightweight runner as tests/test_ops_stock.py, and ITS stand-ins (imported, not copied): the invented stock
ledger (Book), the database stand-in that answers every read from it (World), the temporary data root and the
command (run). SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import; no
database, no network, no .env. All names and numbers are invented; every folder is a temporary one.

Two kinds of world:
  * the damage world: the stock suite's world (the data date D is Sun 15 Mar 2026; ITEM-A sells two a day)
    with eight issues to the damage store added by hand, some booked, one booked two days late, some never
    (dmg_book: one comment line each). The command runs on it end to end and the figures are worked out by
    hand in the tests;
  * small worlds: a few hand-made items (stock.Item) and a Book of movements, run through stockflow.flows
    and damage_facts alone, for the cases that need exact control (the trend, the 7 days' grace, a
    write-off, the sort order, the quality check, the 90-day window).

Covered:
  * what the store holds: units, value at landed cost, the share of the main store's stock at cost (none
    when that is nought), items without a cost counted apart, items that hold some;
  * the twelve months (fewer when the ledger starts later, the first and the current month flagged as part
    months), sent split between the main store and elsewhere, booked in, per 100 sold;
  * the trend: rising, falling, steady (the 120% / 80% edges are not "rising" / "falling"), and each reason
    for none (too few units this month or before, no sale, a ledger too young);
  * not booked: sent up to 7 days before D, less booked by D (a late receive leg is not listed), newest
    issue, the order; nothing left (a write-off or a return ends it; nothing booked, no line);
  * by item: units in the store or sent in the 90 days to D, value order (none last), the quality check
    (twice the company's rate AND 5 units; twice exactly is not), the window's edges;
  * the screens: not set up, no movement, sent but never booked, the headline block, the full view, one item
    (the words, singular and plural), the wording guard (a data word it refuses is blanked), no van named;
  * the facts saved beside the screen: items by name and code, Decimals as text, dates as ISO, no float.
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
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

# The stock suite's stand-ins. Importing it also sets stdout to UTF-8 (so this file does not) and imports
# scripts.ops.db first, as every command does.
from tests.test_ops_stock import (  # noqa: E402
    D, DAMAGE, LONG, MAIN, NAMES, NINE, SAMPLES, VAN, VAN_B, Book, World, data_root, L, model_of, run, snapshot_rows,
    world)
from scripts.ops import config, fmt, ledger, paths, stock, stock_damage, stockflow  # noqa: E402

stock.RAISE_PARTS = True              # a part that fails fails the test, never stands aside quietly

TESTS: list[tuple[str, object]] = []

NOT_SET_UP = "DAMAGE  not set up: config 'warehouses.damage' names the damage store in Focus"
A, B, C, N, F = L("A"), L("B"), L("C"), L("N"), L("F")        # the ledger names of five items of the world
DEC, JAN, FEB, MAR = (2025, 12), (2026, 1), (2026, 2), (2026, 3)


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── the damage world ──────────────────────────────────────────────────────────

def dmg_book() -> Book:
    """The stock suite's world plus eight issues to the damage store (D stays Sun 15 Mar 2026: the world's last
    rows are that day's sales). The units leave the main store only when `frm` is the main store."""
    b = world()
    b.to_damage(A, date(2025, 11, 5), 4)                                  # a1  from the main store, booked
    b.to_damage(A, date(2026, 1, 8), 6, frm=VAN)                          # a2  from elsewhere (a van), booked
    b.to_damage(A, date(2026, 3, 2), 5, booked_on=date(2026, 3, 4))       # a3  main store, booked two days late
    b.to_damage(B, date(2026, 2, 10), 6)                                  # b1  main store, booked
    b.to_damage(B, date(2026, 3, 12), 2, frm=VAN_B, booked=False)         # b2  elsewhere, three days ago, not booked
    b.to_damage(C, date(2026, 2, 20), 7, booked=False)                    # c1  main store, never booked
    b.to_damage(N, date(2026, 3, 10), 5)                                  # n1  main store, booked
    b.to_damage(F, date(2025, 10, 1), 7, frm=VAN, booked=False)           # f1  elsewhere, never booked
    return b


# The main store after those issues (the stock suite's 394 / 20 / 50 / 36 less what left it for the damage store):
# A 394 - 4 - 5 = 385, B 20 - 6 = 14, C 50 - 7 = 43, ITEM-N 36 - 5 = 31; the van-sourced issues take nothing from it.
SNAP = snapshot_rows({"A": 385, "B": 14, "C": 43, "N": 31})
_MODEL: list = []
_RUNS: dict = {}


def dmg_world() -> World:
    return World(book=dmg_book(), snapshot=SNAP)


def dmg():
    """(the Model, its damage facts) of the damage world, built once."""
    if not _MODEL:
        with data_root():
            _MODEL.append(model_of(dmg_world())[0])
    return _MODEL[0], _MODEL[0].facts["damage"]


def dmg_run(*spec) -> dict:
    """The command's answer on the damage world, once per spec (its files go with the temporary root)."""
    if spec not in _RUNS:
        with data_root():
            _RUNS[spec] = run(dmg_world(), *spec)
    return _RUNS[spec]


# ── small worlds ──────────────────────────────────────────────────────────────

def mk_item(tag, cost="1", on_hand=0, sales=None) -> stock.Item:
    """An accessory of a hand-made model; its ledger name is '<name> <code>' as the real ones are."""
    name, code = f"ITEM-{tag} Test {tag}", f"T{tag}"
    return stock.Item(ledger_name=f"{name} {code}", name=name, code=code,
                      cost=None if cost is None else Decimal(str(cost)), on_hand=Decimal(on_hand),
                      sales={k: Decimal(v) for k, v in (sales or {}).items()})


def facts_for(items, book, d, damage=DAMAGE):
    """damage_facts for a hand-made model: the items, the movements of `book`, the data date `d`."""
    with data_root():
        fl = stockflow.flows(book.rows, [it.ledger_name for it in items], damage, [DAMAGE, SAMPLES], d)
    model = SimpleNamespace(d=d, cfg=SimpleNamespace(damage=damage), items=items, facts={})
    return stock_damage.damage_facts(model, fl), model


def mini(sold, sent, d=date(2026, 3, 20), first=date(2025, 1, 1)):
    """One item: `sold` {(year, month): units} are sales on the 10th, `sent` {(year, month): units} issues to the
    damage store from the main store on the 12th (booked), and the ledger starts with a receipt on `first`."""
    it = mk_item("M", cost="0.4", on_hand=100, sales={date(y, m, 10): u for (y, m), u in sold.items()})
    b = Book()
    if first is not None:
        b.receipt(it.ledger_name, first, 5000, 2000, "MRN:T-MINI")
    for (y, m), units in sent.items():
        b.to_damage(it.ledger_name, date(y, m, 12), units)
    return facts_for([it], b, d)[0]


def has_row(lines, *cells) -> bool:
    """Is there a table row with these cells, in this order, apart by spaces only?"""
    rx = re.compile(r"^\s+" + r"\s+".join(re.escape(c) for c in cells) + r"$")
    return any(rx.match(ln) for ln in lines)


def floats(x, found=None):
    """Every float anywhere in a structure (there must be none: money and units are Decimals)."""
    found = [] if found is None else found
    if isinstance(x, float):
        found.append(x)
    elif isinstance(x, dict):
        for v in x.values():
            floats(v, found)
    elif isinstance(x, (list, tuple)):
        for v in x:
            floats(v, found)
    return found


# ── what the store holds ──────────────────────────────────────────────────────

@test("now: units in the store, at landed cost, as a share of the main store's stock at cost; items that hold some")
def _():
    m, f = dmg()
    assert f["set_up"] is True and f["store"] == DAMAGE and f["found"] is True
    for key, units in (("A", 385), ("B", 14), ("C", 43), ("N", 31)):
        assert next(it for it in m.items if it.ledger_name == L(key)).on_hand == units       # the main store after the issues
    now = f["now"]
    # booked less left: A 4 + 6 + 5 = 15, B 6, C 0 (its 7 were never booked), ITEM-N 5, F 0 (its 7 never booked)
    assert now["units"] == 26 and now["items"] == 3 and now["uncosted_units"] == 0
    # at landed cost: A 15 x 0.4 + B 6 x 2.5 + ITEM-N 5 x 3.2 = 6 + 15 + 16
    assert now["value"] == Decimal("37")
    # the main store at cost: 385 x 0.4 + 14 x 2.5 + 43 x 0.8 + 31 x 3.2 + display 5 x 10 = 154 + 35 + 34.4 + 99.2 + 50
    assert now["share_of_main"] == Decimal("37") / Decimal("372.6")
    assert f["sent_ever"] == 42                      # 4 + 6 + 5 + 6 + 2 + 7 + 5 + 7
    # a store that holds nothing of the main store's value: no share (not a division by nought)
    z = mk_item("Z", cost=2, on_hand=0)
    fz, _m = facts_for([z], Book().to_damage(z.ledger_name, date(2026, 3, 1), 3), date(2026, 3, 15))
    assert fz["now"]["units"] == 3 and fz["now"]["value"] == 6 and fz["now"]["share_of_main"] is None
    assert stock_damage.headline_lines(fz)[0] == "DAMAGE  Damage Store: 3 units, BHD 6.000 at landed cost"


@test("now: items without a cost are counted in units, left out of the value, and said so; sorted by value, none last")
def _():
    d = date(2026, 3, 15)                             # the 90 days to D: 16 Dec 2025 to 15 Mar 2026
    p, q = mk_item("P", cost=5), mk_item("Q", cost=2, on_hand=100)
    v1, v2, t, u = mk_item("V1", cost=1), mk_item("V2", cost=1), mk_item("T", cost=1), mk_item("U", cost=1)
    r, s = mk_item("R", cost=None), mk_item("S", cost=None)
    b = Book()
    b.to_damage(p.ledger_name, date(2025, 12, 1), 2)                         # 2 in the store, nothing sent in the 90 days
    b.to_damage(q.ledger_name, date(2025, 12, 2), 5)
    b.to_damage(v1.ledger_name, date(2026, 1, 5), 1)
    b.to_damage(v2.ledger_name, date(2026, 1, 6), 1)
    b.to_damage(t.ledger_name, date(2026, 3, 1), 3, booked=False)            # sent in the 90 days, never booked: 0 in store
    b.to_damage(r.ledger_name, date(2025, 11, 1), 3)
    b.to_damage(s.ledger_name, date(2025, 11, 2), 9)
    f, model = facts_for([v2, s, t, u, q, r, v1, p], b, d)                   # handed over in no order at all
    now = f["now"]
    # units 2 + 5 + 1 + 1 + 0 + 3 + 9 = 21, valued 2 x 5 + 5 x 2 + 1 + 1 = 22; R and S (12 units) have no cost
    assert (now["units"], now["value"], now["uncosted_units"], now["items"]) == (21, 22, 12, 6)
    assert now["share_of_main"] == Decimal("22") / Decimal("200")            # only Q is held in the main store: 100 x 2
    # value desc: P and Q tie at 10, so more units first (Q 5, then P 2, against their names); V1 before V2 by name (1 unit
    # each); T (value 0.00: it has a cost) before the items with none, which come last, more units first (S 9, then R 3)
    # against their names; U moved nothing and is not listed
    got = [(x["item"].code, x["in_store"], x["value"], x["sent_90"]) for x in f["by_item"]]
    assert got == [("TQ", 5, 10, 0), ("TP", 2, 10, 0), ("TV1", 1, 1, 1), ("TV2", 1, 1, 1), ("TT", 0, 0, 3),
                   ("TS", 9, None, 0), ("TR", 3, None, 0)], got
    head = stock_damage.headline_lines(f)
    assert head == [
        "DAMAGE  Damage Store: 21 units, BHD 22.000 at landed cost (11.0% of the main store's value; 12 units with no "
        "cost on file left out of the value)",
        "  This month: 3 sent to damage, nothing sold yet",
        "  To check: 3 units sent to damage were not booked in there after 7 days (1 item)",
        f"  Nothing has left the damage store since {fmt.day(date(2025, 11, 1))} 2025: no write-off, claim or return is "
        "booked"], head                                                       # 1 Nov 2025: the year before D
    lines = stock_damage.damage_lines(f, model)                              # no cost: "n/a"; nothing sold: no rate
    assert has_row(lines, "ITEM-S Test S", "TS", "9", "n/a", "0", "n/a")
    assert has_row(lines, "ITEM-T Test T", "TT", "0", "0.000", "3", "n/a")
    assert has_row(lines, "ITEM-T Test T", "TT", "3", "01 Mar")              # not booked: 3 units, last sent 1 Mar
    assert lines[0].endswith("(11.0% of the main store's value; 12 units with no cost on file left out of the value)")


# ── the months ────────────────────────────────────────────────────────────────

@test("the months: the last twelve, sold, sent (main store / elsewhere), booked in, per 100 sold; the current one to date")
def _():
    _m, f = dmg()
    got = [(x["month"].strftime("%Y-%m"), x["sold"], x["sent"], x["sent_main"], x["sent_elsewhere"], x["booked"],
            str(x["per100"]), x["partial"]) for x in f["months"]]
    assert got == [
        ("2025-04", 120, 0, 0, 0, 0, "0.00", False),           # sold: A 60 + F 60
        ("2025-05", 124, 0, 0, 0, 0, "0.00", False),           # A 62 + F 62
        ("2025-06", 150, 0, 0, 0, 0, "0.00", False),           # A 60 + F 60 + B 30
        ("2025-07", 124, 0, 0, 0, 0, "0.00", False),
        ("2025-08", 124, 0, 0, 0, 0, "0.00", False),
        ("2025-09", 92, 0, 0, 0, 0, "0.00", False),            # A 60 + F 32 (out of stock after the 16th)
        ("2025-10", 62, 7, 0, 7, 0, "11.29", False),           # f1: 7 from a van, never booked; 7 / 62 x 100
        ("2025-11", 60, 4, 4, 0, 4, "6.67", False),            # a1: 4 / 60
        ("2025-12", 62, 0, 0, 0, 0, "0.00", False),
        ("2026-01", 64, 6, 0, 6, 6, "9.38", False),            # a2: 6 / 64 = 9.375, half up
        ("2026-02", 84, 13, 13, 0, 6, "15.48", False),         # b1 6 + c1 7 (c1 never booked); 13 / 84
        ("2026-03", 49, 12, 10, 2, 10, "24.49", True),         # a3 5 + n1 5 + b2 2 (van); booked a3 5 + n1 5; 12 / 49
    ], got
    assert all(isinstance(x["per100"], Decimal) and x["month"].day == 1 for x in f["months"])


@test("the months: fewer when the ledger starts later, the first and the current month flagged; none sold: no rate")
def _():
    f = mini({JAN: 100, FEB: 100, MAR: 100}, {JAN: 6, MAR: 6}, d=date(2026, 3, 20), first=date(2026, 1, 5))
    assert [(x["month"], x["partial"]) for x in f["months"]] == [
        (date(2026, 1, 1), True), (date(2026, 2, 1), False), (date(2026, 3, 1), True)]      # starts on the 5th; D is the 20th
    lines = stock_damage.damage_lines(f, SimpleNamespace(d=date(2026, 3, 20)))              # the screen says which is which
    assert has_row(lines, "Jan 2026 (part month)", "100", "6", "6", "0", "6.00", "6")
    assert has_row(lines, "Feb 2026", "100", "0", "0", "0", "0.00", "0")
    assert has_row(lines, "Mar 2026 (to date)", "100", "6", "6", "0", "6.00", "6")
    # a ledger that starts on the 1st has no part first month; D on a month end is no part month
    f = mini({DEC: 100, JAN: 100, FEB: 100, MAR: 100}, {MAR: 6}, d=date(2026, 3, 31), first=date(2025, 12, 1))
    assert [x["partial"] for x in f["months"]] == [False, False, False, False]
    assert mini({MAR: 100}, {MAR: 6}, d=date(2026, 3, 30))["months"][-1]["partial"] is True       # 30 Mar is no month end
    # a ledger older than the year: twelve months, April to March, and a month with no sale has no rate
    f = mini({MAR: 100}, {MAR: 6})
    assert len(f["months"]) == 12
    assert (f["months"][0]["month"], f["months"][-1]["month"]) == (date(2025, 4, 1), date(2026, 3, 1))
    assert [x["per100"] for x in f["months"][:-1]] == [None] * 11 and f["months"][-1]["per100"] == Decimal("6.00")


# ── the trend ─────────────────────────────────────────────────────────────────

@test("the trend: this month against the 3 months before, pooled; above 120% rising, below 80% falling, else steady")
def _():
    sold = {DEC: 1000, JAN: 1000, FEB: 1000}
    sent = {DEC: 10, JAN: 10, FEB: 10}                # 30 units in 3,000 sold: 1.00 for every 100 sold

    def trend(mar_sold, mar_sent):
        return mini({**sold, MAR: mar_sold}, {**sent, MAR: mar_sent})["trend"]

    t = trend(500, 10)                                # 10 / 500 = 2.00 against 1.00: above 1.20
    assert (t["word"], t["this"], t["prior3"], t["why_none"]) == ("rising", Decimal("2.00"), Decimal("1.00"), None)
    t = trend(1000, 5)                                # 5 / 1000 = 0.50: below 0.80
    assert (t["word"], t["this"], t["prior3"]) == ("falling", Decimal("0.50"), Decimal("1.00"))
    assert trend(1000, 11)["word"] == "steady"        # 1.10
    # the words on the screens: the headline's line and the full view's sentence
    f = mini({**sold, MAR: 1000}, {**sent, MAR: 5})
    assert stock_damage.headline_lines(f)[1] == \
        "  This month: 5 sent to damage, 0.50 for every 100 sold; falling against the 3 months before (1.00)"
    lines = stock_damage.damage_lines(f, SimpleNamespace(d=date(2026, 3, 20)))
    assert "  Trend: this month's 0.50 for every 100 sold is falling against the 3 months before (1.00, pooled)." in lines
    f = mini({**sold, MAR: 1000}, {**sent, MAR: 11})
    assert "  Trend: this month's 1.10 for every 100 sold is steady against the 3 months before (1.00, pooled)." in \
        stock_damage.damage_lines(f, SimpleNamespace(d=date(2026, 3, 20)))
    t = trend(500, 6)
    assert (t["word"], t["this"]) == ("steady", Decimal("1.20"))      # 6 / 500 = 1.20: not above 1.20
    t = trend(1250, 10)
    assert (t["word"], t["this"]) == ("steady", Decimal("0.80"))      # 10 / 1250 = 0.80: not below 0.80
    # pooled, not an average of the months: 10 / 1000, 10 / 1000, 10 / 100 would average 4.00 but pool to 30 / 2100
    t = mini({DEC: 1000, JAN: 1000, FEB: 100, MAR: 100}, {**sent, MAR: 5})["trend"]
    assert t["prior3"] == Decimal("1.43") and t["this"] == Decimal("5.00") and t["word"] == "rising"     # 30 / 2100


@test("the trend: none, and why, when this month or the 3 before sent under 5 units, nothing was sold, or the ledger is young")
def _():
    sold = {DEC: 1000, JAN: 1000, FEB: 1000, MAR: 1000}
    sent = {DEC: 10, JAN: 10, FEB: 10}

    def none(why, f):
        t = f["trend"]
        assert (t["word"], t["this"], t["prior3"]) == (None, None, None) and t["why_none"] == why, t

    none("only 4 units were sent to damage this month, and a trend needs 5", mini(sold, {**sent, MAR: 4}))
    none("only 1 unit was sent to damage this month, and a trend needs 5", mini(sold, {**sent, MAR: 1}))
    none("no unit was sent to damage this month, and a trend needs 5", mini(sold, sent))
    none("only 4 units were sent to damage in the 3 months before, and a trend needs 5",
         mini(sold, {DEC: 1, JAN: 1, FEB: 2, MAR: 10}))                       # 4 in the 3 months before
    none("no unit was sold this month", mini({DEC: 1000, JAN: 1000, FEB: 1000}, {**sent, MAR: 10}))
    none("no unit was sold in the 3 months before", mini({MAR: 1000}, {DEC: 10, MAR: 10}))
    # a ledger with fewer than 3 whole months before this one: two months, or the third one starts mid-month
    young = mini({FEB: 100, MAR: 100}, {MAR: 10}, first=date(2026, 2, 5))
    none("the ledger holds fewer than 3 whole months before this one", young)
    assert [x["partial"] for x in young["months"]] == [True, True]            # Feb (starts the 5th), Mar (to date)
    none("the ledger holds fewer than 3 whole months before this one",
         mini({DEC: 1000, JAN: 1000, FEB: 1000, MAR: 500}, {**sent, MAR: 10}, first=date(2025, 12, 5)))
    # the same ledger starting on the 1st: December is whole, and the trend is called
    whole = mini({DEC: 1000, JAN: 1000, FEB: 1000, MAR: 500}, {**sent, MAR: 10}, first=date(2025, 12, 1))
    assert whole["trend"]["word"] == "rising" and not whole["months"][0]["partial"]
    # the damage world: 12 sent this month (24.49) against 19 in 210 sold (9.05)
    _m, f = dmg()
    assert (f["trend"]["word"], f["trend"]["this"], f["trend"]["prior3"], f["trend"]["why_none"]) == (
        "rising", Decimal("24.49"), Decimal("9.05"), None)                  # 19 / 210 x 100 = 9.0476


# ── not booked, nothing left ──────────────────────────────────────────────────

@test("not booked: units sent up to 7 days before D less units booked by D; a late receive leg is not listed")
def _():
    d = date(2026, 3, 15)                             # D - 7 = Sun 8 Mar
    it = {t: mk_item(t) for t in ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7")}
    b = Book()
    b.to_damage(it["G0"].ledger_name, date(2026, 3, 1), 5, booked=False)             # 5 sent on 1 Mar, never booked: listed
    b.to_damage(it["G1"].ledger_name, date(2026, 3, 8), 5, booked=False)             # sent on D - 7 exactly: listed
    b.to_damage(it["G2"].ledger_name, date(2026, 3, 9), 4, booked=False)             # sent on D - 6: still inside the 7 days
    b.to_damage(it["G3"].ledger_name, date(2026, 3, 1), 6, booked_on=date(2026, 3, 12))   # booked 11 days late, but by D
    b.to_damage(it["G4"].ledger_name, date(2026, 2, 1), 10)                           # booked
    b.to_damage(it["G4"].ledger_name, date(2026, 2, 2), 6, booked=False)              # 6 never booked
    b.to_damage(it["G4"].ledger_name, date(2026, 3, 14), 3, booked=False)             # 3 more, but too new to list
    b._row(it["G5"].ledger_name, date(2026, 3, 3), ledger.COUNT_OVER, DAMAGE, "EX:T-1", qin=3)   # booked, never sent
    b.to_damage(it["G6"].ledger_name, date(2026, 3, 12), 6, booked_on=date(2026, 3, 14))          # two days late: not listed
    b.to_damage(it["G7"].ledger_name, date(2026, 3, 8), 4)                            # sent and booked on D - 7: cleared
    backwards = list(reversed(list(it.values())))                                      # the order must not decide the ties
    f, _m = facts_for(backwards, b, d)
    got = [(x["item"].code, x["units"], x["last_sent"]) for x in f["not_booked"]]
    # units desc, then the name: G4 first (16 sent up to 8 Mar - 10 booked = 6; newest issue 2 Feb), then G0 and G1 (5 each)
    assert got == [("TG4", 6, date(2026, 2, 2)), ("TG0", 5, date(2026, 3, 1)), ("TG1", 5, date(2026, 3, 8))], got
    assert f["not_booked_units"] == 16
    assert stock_damage.headline_lines(f)[-2] == \
        "  To check: 16 units sent to damage were not booked in there after 7 days (3 items)", stock_damage.headline_lines(f)
    # two days later the 9 Mar issue is a week old: G2 joins (4); G4 stays at 6 (D - 7 = 10 Mar: its 14 Mar issue is not due)
    f2, _m = facts_for(backwards, b, date(2026, 3, 17))
    assert [(x["item"].code, x["units"]) for x in f2["not_booked"]] == [("TG4", 6), ("TG0", 5), ("TG1", 5), ("TG2", 4)]


@test("not booked: the week's bookings are matched to the week's sends first; a booked new transfer hides no old one")
def _():
    d = date(2026, 3, 15)                             # D - 7 = 8 Mar; the grace window is 9 Mar to 15 Mar, both included
    tags = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")
    it = {t: mk_item(t) for t in tags}
    name = {t: it[t].ledger_name for t in tags}
    b = Book()
    # R1, the review's case: 10 sent on D - 10 and never received; 10 sent on D - 3, received on D - 2. Sent up to
    # D - 7 (10) less booked up to D (10) was 0 and hid it; the 10 booked this week belong to the 10 sent this week.
    b.to_damage(name["R1"], date(2026, 3, 5), 10, booked=False)
    b.to_damage(name["R1"], date(2026, 3, 12), 10, booked_on=date(2026, 3, 13))
    # R2: the same 10 sent on D - 10, its own receive leg posted late, on D - 2; nothing sent this week: cleared
    b.to_damage(name["R2"], date(2026, 3, 5), 10, booked_on=date(2026, 3, 13))
    # R3: 10 old never received; 4 sent on D - 3 and received on D - 2, and 6 more booked straight in on D - 1 (a
    # return): the week's sends explain 4 of the 10 booked, the other 6 clear older units, 4 stay listed
    b.to_damage(name["R3"], date(2026, 3, 5), 10, booked=False)
    b.to_damage(name["R3"], date(2026, 3, 12), 4, booked_on=date(2026, 3, 13))
    b._row(name["R3"], date(2026, 3, 14), ledger.COUNT_OVER, DAMAGE, "EX:T-2", qin=6)
    # R4: the window ends on D itself: sent on D - 1, received on D
    b.to_damage(name["R4"], date(2026, 3, 5), 10, booked=False)
    b.to_damage(name["R4"], date(2026, 3, 14), 10, booked_on=date(2026, 3, 15))
    # R5: the window starts on D - 6: sent and received on 9 Mar
    b.to_damage(name["R5"], date(2026, 3, 5), 10, booked=False)
    b.to_damage(name["R5"], date(2026, 3, 9), 5)
    # R6: D - 7 itself is old, not part of the window: sent and received on 8 Mar, nothing to list
    b.to_damage(name["R6"], date(2026, 3, 8), 4)
    # R7: 10 sent on D - 10 and received on D - 7 (the day before the window); 6 more sent this week, not yet booked
    b.to_damage(name["R7"], date(2026, 3, 5), 10, booked_on=date(2026, 3, 8))
    b.to_damage(name["R7"], date(2026, 3, 12), 6, booked=False)
    # R8: 6 sent on 10 Feb and booked at once; 2 sent this week, not yet booked: the 6 are not unbooked for that
    b.to_damage(name["R8"], date(2026, 2, 10), 6)
    b.to_damage(name["R8"], date(2026, 3, 12), 2, booked=False)
    f, model = facts_for(list(reversed(list(it.values()))), b, d)
    got = [(x["item"].code, x["units"], x["last_sent"]) for x in f["not_booked"]]
    # R1, R4 and R5 hold 10 each (by name), then R3's 4; the oldest issue of all four is 5 Mar
    assert got == [("TR1", 10, date(2026, 3, 5)), ("TR4", 10, date(2026, 3, 5)), ("TR5", 10, date(2026, 3, 5)),
                   ("TR3", 4, date(2026, 3, 5))], got
    assert f["not_booked_units"] == 34
    lines = stock_damage.damage_lines(f, model)
    assert ("  To check: 34 units sent to damage were not booked in there after 7 days (4 items)"
            in stock_damage.headline_lines(f))
    assert ("NOT BOOKED  34 units of 4 items sent to damage over 7 days ago are not booked in the damage store: to check"
            in lines)
    assert has_row(lines, "ITEM-R1 Test R1", "TR1", "10", "05 Mar") and has_row(lines, "ITEM-R3 Test R3", "TR3", "4", "05 Mar")
    table = lines[[i for i, ln in enumerate(lines) if ln.startswith("NOT BOOKED")][0]:]       # below its heading only
    assert sorted(ln.split()[0] for ln in table if ln.strip().startswith("ITEM-R")) == [
        "ITEM-R1", "ITEM-R3", "ITEM-R4", "ITEM-R5"]                                             # R2, R6, R7, R8 are not listed
    # a week later the sends of this week are old themselves, and what stayed unbooked of them is listed
    f2, _m = facts_for(list(it.values()), b, date(2026, 3, 22))
    later = {x["item"].code: x["units"] for x in f2["not_booked"]}
    assert later == {"TR1": 10, "TR3": 4, "TR4": 10, "TR5": 10, "TR7": 6, "TR8": 2}, later      # R7's 6 and R8's 2 are due now


@test("nothing left: a write-off or a return to the main store ends it; with nothing booked there is no such line")
def _():
    d = date(2026, 3, 15)
    k1, k2, k3 = mk_item("K1"), mk_item("K2"), mk_item("K3")
    b = Book()
    b.to_damage(k1.ledger_name, date(2026, 3, 1), 10)                        # booked on 1 Mar
    b.to_damage(k2.ledger_name, date(2026, 2, 20), 4)                        # booked on 20 Feb: the first booking of all
    b.to_damage(k3.ledger_name, date(2026, 3, 5), 3, booked=False)           # never booked
    f, _m = facts_for([k1, k2, k3], b, d)
    assert f["left_units"] == 0 and f["nothing_left_since"] == date(2026, 2, 20)
    assert stock_damage.headline_lines(f)[-1] == (
        f"  Nothing has left the damage store since {fmt.day(date(2026, 2, 20))}: no write-off, claim or return is booked")
    # a shortage counted in the damage store (a write-off): 2 units out
    b._row(k1.ledger_name, date(2026, 3, 10), ledger.COUNT_SHORT, DAMAGE, "SH:T-1", qout=2)
    f, _m = facts_for([k1, k2, k3], b, d)
    assert f["left_units"] == 2 and f["nothing_left_since"] is None
    assert f["now"]["units"] == 12                                           # 10 - 2 + 4
    assert not any("Nothing has left" in ln for ln in stock_damage.headline_lines(f))
    # a return: an issue from the damage store back to the main store
    b2 = Book()
    b2.to_damage(k1.ledger_name, date(2026, 3, 1), 10)
    b2.move(k1.ledger_name, date(2026, 3, 12), 3, DAMAGE, MAIN)
    f, _m = facts_for([k1], b2, d)
    assert f["left_units"] == 3 and f["nothing_left_since"] is None and f["now"]["units"] == 7
    # nothing booked at all (issues only): nothing was ever there to leave, so no line
    b3 = Book()
    b3.to_damage(k3.ledger_name, date(2026, 3, 5), 3, booked=False)
    f, _m = facts_for([k3], b3, d)
    assert f["left_units"] == 0 and f["nothing_left_since"] is None


@test("dates: a day outside D's year carries its year (nothing left since, last sent); a day in D's year stays short")
def _():
    d = date(2026, 3, 15)                             # D: Sun 15 Mar 2026

    def screens(first_booking, last_issue):
        """One item with 4 units booked on `first_booking` (nothing ever leaves) and 3 sent on `last_issue`, never booked."""
        it = mk_item("Y")
        b = Book()
        b.to_damage(it.ledger_name, first_booking, 4)
        b.to_damage(it.ledger_name, last_issue, 3, booked=False)
        f, model = facts_for([it], b, d)
        return f, stock_damage.headline_lines(f), stock_damage.damage_lines(f, model)

    tail = ": no write-off, claim or return is booked"
    # the first booking is in the year before D: 19 Oct 2025 must not read like a recent Sunday
    f, head, lines = screens(date(2025, 10, 19), date(2025, 12, 1))
    assert f["as_of"] == d and f["nothing_left_since"] == date(2025, 10, 19)
    assert head[-1] == "  Nothing has left the damage store since Sun 19 Oct 2025" + tail
    assert "  Nothing has left the damage store since Sun 19 Oct 2025" + tail in lines
    assert has_row(lines, "ITEM-Y Test Y", "TY", "3", "01 Dec 2025")         # the last issue: year too, as it is not D's
    # both in D's year: the short form stays
    f, head, lines = screens(date(2026, 2, 20), date(2026, 3, 1))
    assert head[-1] == "  Nothing has left the damage store since Fri 20 Feb" + tail
    assert "  Nothing has left the damage store since Fri 20 Feb" + tail in lines
    assert has_row(lines, "ITEM-Y Test Y", "TY", "3", "01 Mar")
    assert lines[0].startswith("DAMAGE  Damage Store, ledger to Sun 15 Mar: ")     # D itself is in D's year
    # the edge of the year: 31 Dec 2025 has its year, 1 Jan 2026 has not
    head = screens(date(2025, 12, 31), date(2026, 1, 2))[1]
    assert head[-1] == "  Nothing has left the damage store since Wed 31 Dec 2025" + tail
    f, head, _l = screens(date(2026, 1, 1), date(2026, 1, 2))
    assert head[-1] == "  Nothing has left the damage store since Thu 01 Jan" + tail
    # facts with no data date to compare with (made by hand): the year is always printed
    bare = {k: v for k, v in f.items() if k != "as_of"}
    assert stock_damage.headline_lines(bare)[-1] == "  Nothing has left the damage store since Thu 01 Jan 2026" + tail


# ── by item ───────────────────────────────────────────────────────────────────

@test("by item: the damage world's items by value, with their 90 days; the company's rate; the quality mark")
def _():
    _m, f = dmg()
    got = [(x["item"].name, x["in_store"], x["value"], x["sent_90"], x["sold_90"], x["per100_90"], x["quality_check"])
           for x in f["by_item"]]
    # 90 days to D: 16 Dec 2025 to 15 Mar 2026. ITEM-F (7 sent in Oct, nothing in store) is not listed.
    assert got == [
        (NAMES["N"], 5, Decimal("16"), 5, 4, Decimal("125.00"), True),      # 5 x 3.2; 5 sent in 4 sold; 125.00 > 2 x 13.54
        (NAMES["B"], 6, Decimal("15"), 8, 45, Decimal("17.78"), False),     # 6 x 2.5; b1 6 + b2 2; 8 / 45
        (NAMES["A"], 15, Decimal("6"), 11, 180, Decimal("6.11"), False),    # 15 x 0.4; a2 6 + a3 5 (a1 is in Nov); 11 / 180
        (NAMES["C"], 0, Decimal("0"), 7, 0, None, False),                   # nothing in store (never booked), no sale
    ], got
    # company: (11 + 8 + 7 + 5) / (180 + 45 + 0 + 4) x 100 = 31 / 229 = 13.5371
    assert f["company_per100_90"] == Decimal("13.54")


@test("quality: a rate above twice the company's with 5 or more units sent is marked; twice exactly, or fewer units, is not")
def _():
    d = date(2026, 3, 15)

    def scene(rows):
        items, b = [], Book()
        for tag, sold, sent in rows:
            items.append(mk_item(tag, sales={date(2026, 2, 1): sold} if sold else {}))
            if sent:
                b.to_damage(items[-1].ledger_name, date(2026, 2, 5), sent)
        f, _m = facts_for(items, b, d)
        return f, {x["item"].code[1:]: (x["per100_90"], x["quality_check"]) for x in f["by_item"]}

    # company: (10 + 4 + 5 + 2) / (1000 + 100 + 50 + 100 + 50) x 100 = 21 / 1300 = 1.6154 -> 1.62; twice = 3.24
    f, got = scene([("W0", 1000, 0), ("W1", 100, 10), ("W2", 50, 4), ("W3", 100, 5), ("W4", 50, 2)])
    assert f["company_per100_90"] == Decimal("1.62")
    assert got == {"W1": (Decimal("10.00"), True),              # 10 sent: marked
                   "W2": (Decimal("8.00"), False),              # above 3.24 but only 4 sent
                   "W3": (Decimal("5.00"), True),               # 5 sent is enough
                   "W4": (Decimal("4.00"), False)}, got         # above 3.24 but only 2 sent
    # twice exactly: (5 + 5) / (125 + 375) = 2.00, and ITEM-Y1 sits at 5 / 125 = 4.00
    f, got = scene([("Y1", 125, 5), ("Y2", 375, 5)])
    assert f["company_per100_90"] == Decimal("2.00")
    assert got == {"Y1": (Decimal("4.00"), False), "Y2": (Decimal("1.33"), False)}
    # nothing sold in the 90 days: no rate, nothing to compare, nothing marked
    f, got = scene([("Z1", 0, 8)])
    assert f["company_per100_90"] is None and got == {"Z1": (None, False)}


@test("the 90 days to D include D - 89 and leave out D - 90, for what was sold and what was sent")
def _():
    d = date(2026, 3, 15)                             # D - 89 = Tue 16 Dec 2025; D - 90 = Mon 15 Dec 2025
    it = mk_item("BND", sales={date(2025, 12, 15): 7, date(2025, 12, 16): 3})
    b = Book()
    b.to_damage(it.ledger_name, date(2025, 12, 15), 2)
    b.to_damage(it.ledger_name, date(2025, 12, 16), 4)
    f, _m = facts_for([it], b, d)
    x = f["by_item"][0]
    assert (x["sold_90"], x["sent_90"], x["in_store"]) == (3, 4, 6)
    assert x["per100_90"] == Decimal("133.33") and f["company_per100_90"] == Decimal("133.33")      # 4 / 3 x 100


# ── the screens ───────────────────────────────────────────────────────────────

@test("not set up: one line on every screen, no figures, nothing to say about an item")
def _():
    with data_root(drop=("warehouses.damage",)):
        m, _inp = model_of(dmg_world())
        head = run(dmg_world())["text"]
        view = run(dmg_world(), "damage")["text"]
        one = run(dmg_world(), "CA1")["text"]
    f = m.facts["damage"]
    assert f["set_up"] is False and f["store"] is None and f["found"] is False and f["by_item"] == [] and f["months"] == []
    assert f["as_of"] == D                                   # even a store that is not set up knows the data date
    assert f["now"]["units"] == 0 and f["now"]["share_of_main"] is None
    assert stock_damage.headline_lines(f) == [NOT_SET_UP]
    assert stock_damage.damage_lines(f, m) == [NOT_SET_UP] and stock_damage.item_lines(f, A) == []
    assert NOT_SET_UP in head.splitlines() and view.splitlines()[0] == NOT_SET_UP
    assert "Damage:" not in one and json.dumps(stock._jsonable(stock_damage.facts_json(f)))


@test("no movement: the store is set up but the ledger never moved a unit into or out of it")
def _():
    with data_root():
        m, _inp = model_of(World())
        view = run(World(), "damage")["text"]
        head = run(World())["text"].splitlines()
    f = m.facts["damage"]
    line = "DAMAGE  Damage Store: no movement in the stock ledger"
    assert f["set_up"] and f["store"] == DAMAGE and not f["found"] and f["sent_ever"] == 0
    assert f["by_item"] == [] and f["not_booked"] == [] and f["nothing_left_since"] is None
    assert len(f["months"]) == 12 and all(x["sent"] == 0 for x in f["months"])
    assert stock_damage.headline_lines(f) == [line] and stock_damage.damage_lines(f, m) == [line]
    assert view.splitlines()[0] == line and line in head and head[head.index(line) - 1] == ""
    assert stock_damage.item_lines(f, A) == []


@test("sent but never booked: not 'no movement'; the title says nothing is in the store and the check line says why")
def _():
    s1 = mk_item("S1", cost=2, on_hand=50, sales={date(2026, 3, 2): 40})
    b = Book()
    b.to_damage(s1.ledger_name, date(2026, 3, 1), 1, booked=False)
    f, model = facts_for([s1], b, date(2026, 3, 15))
    assert f["found"] is False and f["sent_ever"] == 1
    # 1 unit in 40 sold = 2.50; one unit is too few for a trend; the unit is 14 days old and nowhere in the store
    assert stock_damage.headline_lines(f) == [
        "DAMAGE  Damage Store: 0 units, BHD 0.000 at landed cost (0.0% of the main store's value)",
        "  This month: 1 sent to damage, 2.50 for every 100 sold; no trend yet (only 1 unit was sent to damage this "
        "month, and a trend needs 5)",
        "  To check: 1 unit sent to damage was not booked in there after 7 days (1 item)"]
    lines = stock_damage.damage_lines(f, model)                              # the same facts, in the full view: singular words
    assert has_row(lines, "Mar 2026 (to date)", "40", "1", "1", "0", "2.50", "0")
    assert "  Trend: none yet, because only 1 unit was sent to damage this month, and a trend needs 5." in lines
    assert "BY ITEM  1 item in the damage store or sent to it in the last 90 days, most value first" in lines
    assert "NOT BOOKED  1 unit of 1 item sent to damage over 7 days ago is not booked in the damage store: to check" in lines


@test("the full view with nothing to list says so; the tables stop at 15 rows and say how many more")
def _():
    # 4 units booked and written off in full: nothing in the store, nothing sent in the 90 days, nothing waiting
    k = mk_item("K", cost=2, on_hand=10)
    b = Book()
    b.to_damage(k.ledger_name, date(2025, 11, 1), 4)
    b._row(k.ledger_name, date(2025, 11, 20), ledger.COUNT_SHORT, DAMAGE, "SH:T-1", qout=4)
    f, model = facts_for([k], b, date(2026, 3, 15))
    assert f["by_item"] == [] and f["not_booked"] == [] and f["now"]["units"] == 0 and f["left_units"] == 4
    lines = stock_damage.damage_lines(f, model)
    assert "BY ITEM  no item is in the damage store or was sent to it in the last 90 days" in lines
    assert "NOT BOOKED  none: every unit sent to damage over 7 days ago is booked in the damage store" in lines
    assert not any("Nothing has left" in ln for ln in lines)
    assert stock_damage.headline_lines(f) == [
        "DAMAGE  Damage Store: 0 units, BHD 0.000 at landed cost (0.0% of the main store's value)",
        "  This month: 0 sent to damage, nothing sold yet"]
    # 17 items sent 5 units each on 1 Mar and never booked: 15 rows in each table, "(+2 more)" under each
    its = [mk_item(f"{i:02d}") for i in range(1, 18)]
    b = Book()
    for it in its:
        b.to_damage(it.ledger_name, date(2026, 3, 1), 5, booked=False)
    f, model = facts_for(its, b, date(2026, 3, 15))
    lines = stock_damage.damage_lines(f, model)
    assert len(f["by_item"]) == 17 and len(f["not_booked"]) == 17 and f["not_booked_units"] == 85
    assert sum(1 for ln in lines if ln.strip().startswith("ITEM-")) == 30 and lines.count("  (+2 more)") == 2
    assert any("ITEM-15 Test 15" in ln for ln in lines) and not any("ITEM-16 Test 16" in ln for ln in lines)
    kept = stock_damage.facts_json(f)                                        # the file keeps every row the screen cuts
    assert len(kept["by_item"]) == 17 and len(kept["not_booked"]) == 17


@test("the headline block: the title and three lines in the damage world")
def _():
    _m, f = dmg()
    # the share: 37 / 372.6 = 9.93% of the main store's value at cost
    block = [
        "DAMAGE  Damage Store: 26 units, BHD 37.000 at landed cost (9.9% of the main store's value)",
        "  This month: 12 sent to damage, 24.49 for every 100 sold; rising against the 3 months before (9.05)",
        "  To check: 14 units sent to damage were not booked in there after 7 days (2 items)",
        "  Nothing has left the damage store since Wed 05 Nov 2025: no write-off, claim or return is booked"]
    assert stock_damage.headline_lines(f) == block
    assert fmt.day(date(2025, 11, 5)) == "Wed 05 Nov"
    lines = dmg_run()["text"].splitlines()
    i = lines.index(block[0])
    assert lines[i:i + 4] == block and lines[i - 1] == "" and lines[i + 4] == ""      # after the ageing, before the footer
    assert len(stock_damage.headline_lines(f)) <= 4                                    # the title and at most three lines


@test("the full view (/yq-stock damage): the title, 12 months, the trend, the items, what is not booked, the two notes")
def _():
    out = dmg_run("damage")
    assert out["ok"] and out["command"] == "stock" and out["flags"]["view"] == "damage" and out["data_date"] == "2026-03-15"
    text, lines = out["text"], out["text"].splitlines()
    assert lines[0] == ("DAMAGE  Damage Store, ledger to Sun 15 Mar: 26 units, BHD 37.000 at landed cost "
                        "(9.9% of the main store's value)")
    head = next(ln for ln in lines if ln.startswith("  Month"))
    assert head.split() == ["Month", "Sold", "Sent", "to", "damage", "From", "the", "main", "store", "From", "elsewhere",
                            "Per", "100", "sold", "Booked", "in"]
    assert len([ln for ln in lines if re.match(r"  [A-Z][a-z]{2} 20\d\d( \(|\s)", ln)]) == 12
    assert has_row(lines, "Oct 2025", "62", "7", "0", "7", "11.29", "0")
    assert has_row(lines, "Jan 2026", "64", "6", "0", "6", "9.38", "6")
    assert has_row(lines, "Mar 2026 (to date)", "49", "12", "10", "2", "24.49", "10")
    assert "  Trend: this month's 24.49 for every 100 sold is rising against the 3 months before (9.05, pooled)." in lines
    assert "  (Rising or falling = more than 20% above or below the 3 months before.)" in lines
    assert "BY ITEM  4 items in the damage store or sent to it in the last 90 days, most value first" in lines
    by = next(ln for ln in lines if ln.startswith("  Item") and "Quality" in ln)
    assert by.split() == ["Item", "Code", "In", "damage", "At", "cost", "Sent", "90", "d", "Per", "100", "sold", "Quality"]
    assert has_row(lines, NAMES["N"], "SN4", "5", "16.000", "5", "125.00", "to check with the supplier")
    assert has_row(lines, NAMES["B"], "CB2", "6", "15.000", "8", "17.78")
    assert has_row(lines, NAMES["A"], "CA1", "15", "6.000", "11", "6.11")
    assert has_row(lines, NAMES["C"], "HG3", "0", "0.000", "7", "n/a")
    section = lines[lines.index("BY ITEM  4 items in the damage store or sent to it in the last 90 days, most value first"):
                    lines.index("NOT BOOKED  14 units of 2 items sent to damage over 7 days ago are not booked in the "
                                "damage store: to check")]
    assert not any(NAMES["F"] in ln for ln in section)                 # F: 7 sent in October, none in store, none in 90 days
    assert ("  Company-wide: 13.54 for every 100 sold in the last 90 days. Marked: an item above twice that rate with "
            "5 or more units sent.") in lines
    assert ("NOT BOOKED  14 units of 2 items sent to damage over 7 days ago are not booked in the damage store: to check"
            in lines)
    nb = next(ln for ln in lines if ln.startswith("  Item") and "Last sent" in ln)
    assert nb.split() == ["Item", "Code", "Units", "Last", "sent"]
    assert has_row(lines, NAMES["C"], "HG3", "7", "20 Feb") and has_row(lines, NAMES["F"], "FO5", "7", "01 Oct 2025")
    assert "  Nothing has left the damage store since Wed 05 Nov 2025: no write-off, claim or return is booked" in lines
    assert "  Counted when sent (the issue leg), so a receipt booked late never hides damage." in lines
    assert ("  Nothing here is booked: claiming from the supplier, writing off, repairing or selling as B-grade is your "
            "decision.") in lines
    assert "elsewhere (vans and outlets)" in text


@test("the wording: every line passes the guard, no van or person is named, no long digit run; a data word is blanked")
def _():
    texts = [dmg_run()["text"], dmg_run("damage")["text"], dmg_run("CA1")["text"], dmg_run("SN4")["text"]]
    for text in texts:
        for line in text.splitlines():
            fmt.careful(line)
        assert not NINE.search(text) and not LONG.search(text)
    damage_part = "\n".join(texts[1].splitlines()[:40])
    for bad in (VAN, VAN_B, "Van A", "Van B", "Acc WH", "salesman", "driver"):
        assert bad not in damage_part, bad
    # a name of the data that the guard refuses never reaches a line
    leak = stock.Item(ledger_name="ITEM-L Leakproof Case TL", name="ITEM-L Leakproof Case", code="TL", cost=Decimal(1),
                      on_hand=Decimal(10))
    b = Book()
    b.to_damage(leak.ledger_name, date(2026, 3, 1), 6)
    fl, model = facts_for([leak], b, date(2026, 3, 15))
    lines = stock_damage.damage_lines(fl, model) + stock_damage.headline_lines(fl)
    for line in lines:
        fmt.careful(line)
    assert any(f"ITEM-L {'-' * len('Leakproof')} Case" in ln for ln in lines) and not any("Leakproof" in ln for ln in lines)


@test("one item: what it holds in the damage store and what was sent lately; the quality mark; nothing for an untouched item")
def _():
    # CA1: 15 in the store, 11 sent in the 90 days, 6.11 for every 100 sold
    a = [ln for ln in dmg_run("CA1")["text"].splitlines() if "amage" in ln and "Damage rate" not in ln]
    assert "  Damage: 15 units in the damage store; 11 sent in the last 90 days (6.11 for every 100 sold)" in a, a
    n = dmg_run("SN4")["text"].splitlines()
    assert "  Damage: 5 units in the damage store; 5 sent in the last 90 days (125.00 for every 100 sold)" in n
    i = n.index("  Damage: 5 units in the damage store; 5 sent in the last 90 days (125.00 for every 100 sold)")
    assert n[i + 1] == "  Damage rate above twice the company's: to check with the supplier"
    # HG3: nothing in the store (never booked), 7 sent, no sale to set them against: no rate in brackets
    assert "  Damage: none in the damage store now; 7 sent in the last 90 days" in dmg_run("HG3")["text"].splitlines()
    # FO5 was sent 7 in October: nothing in the store and nothing in the 90 days: no line
    assert not any(ln.startswith("  Damage") for ln in dmg_run("FO5")["text"].splitlines())
    assert not any(ln.startswith("  Damage rate") for ln in dmg_run("CB2")["text"].splitlines())
    _m, f = dmg()
    assert stock_damage.item_lines(f, "no such item") == [] and len(stock_damage.item_lines(f, N)) == 2
    assert len(stock_damage.item_lines(f, B)) == 1


# ── the facts saved beside the screen ─────────────────────────────────────────

@test("the facts: items by name and code, Decimals as text, dates as ISO, no float anywhere; the saved file agrees")
def _():
    m, f = dmg()
    assert floats(f) == [] and floats(stock_damage.facts_json(f)) == []
    plain = stock_damage.facts_json(f)
    text = json.dumps(stock._jsonable(plain))
    assert "Item(" not in text
    data = json.loads(text)
    assert data["now"]["units"] == "26" and Decimal(data["now"]["value"]) == 37 and data["now"]["items"] == 3
    assert data["months"][-1]["month"] == "2026-03-01" and data["months"][-1]["partial"] is True
    assert data["trend"] == {"word": "rising", "this": "24.49", "prior3": "9.05", "why_none": None}
    assert data["not_booked"][0] == {"item": NAMES["C"], "code": "HG3", "units": "7", "last_sent": "2026-02-20"}
    assert data["by_item"][0]["item"] == NAMES["N"] and data["by_item"][0]["code"] == "SN4"
    assert data["by_item"][0]["quality_check"] is True and data["by_item"][2]["per100_90"] == "6.11"
    assert data["nothing_left_since"] == "2025-11-05" and data["left_units"] == "0" and data["company_per100_90"] == "13.54"
    assert data["as_of"] == "2026-03-15"
    with data_root() as root:
        out = run(dmg_world(), "damage")
        folder = root / "exports" / "ops" / "2026-03-15"
        assert {Path(p) for p in out["files"]} == {folder / "stock-damage.txt", folder / "stock-damage.json"}
        saved = json.loads((folder / "stock-damage.json").read_text(encoding="utf-8"))
        assert saved["view"] == "damage" and saved["damage"] == data
        headline = run(dmg_world())["text"]
        saved = json.loads((folder / "stock.json").read_text(encoding="utf-8"))
        assert saved["damage"] == data and "DAMAGE  Damage Store: 26 units" in headline


@test("the module reads and writes nothing, and works the figures out without changing the model")
def _():
    src = (ROOT / "scripts" / "ops" / "stock_damage.py").read_text(encoding="utf-8")
    for word in ("import psycopg", "import httpx", "supabase", "subprocess", "yq_load_go", ".table(", "insert", "upsert",
                 "open(", "write_text", "float("):
        assert word not in src, word
    m, f = dmg()
    keep = [(it.ledger_name, dict(it.sales), it.on_hand, it.cost, it.name, it.code) for it in m.items]
    sent = {k: list(v.sent_damage) for k, v in m.flows.items.items()}
    again = stock_damage.damage_facts(m, m.flows)
    assert [(it.ledger_name, dict(it.sales), it.on_hand, it.cost, it.name, it.code) for it in m.items] == keep
    assert {k: list(v.sent_damage) for k, v in m.flows.items.items()} == sent
    assert stock_damage.facts_json(again) == stock_damage.facts_json(f)               # the same answer twice


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
