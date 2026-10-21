"""Ops engine: the pairing of transfer vouchers (scripts.ops.pairing), the rules and helpers it stands on, and
the rest of the seam the movement, weekly and KPI releases build on.

    python -m tests.test_ops_pairing

Same lightweight runner as the other suites (no pytest). No database, no network, no .env
(SUPABASE_URL / SUPABASE_KEY / DATABASE_URL are pinned to ci.invalid before any import). Every folder is
a temporary one; all names and numbers are invented, except in the last test, which reads the real ledger
and its golden file when they are on this machine (SKIP otherwise).

Covered:
  * the constants: the two legs named as the ledger names them, the damage grace equal to /yq-stock
    damage's own, the plan's defaults in Rules;
  * StoreRules: canon (an alias first, matched folded; case and runs of spaces folded; a trailing code
    dropped) and kind (main, damage, not counted, SIM, van, outlet, other: the first that holds);
  * voucher numbers: the key's total order, the distance only within one series and month;
  * deadline: every day of the week, a holiday, two working days of grace, the damage store's week;
  * dec and narr_key equal to the ledger's own on the same cells, an ID-shaped number included;
  * legs_from_rows: whole vouchers equal to ledger.vouchers' (lines, value, unvalued units, narration),
    stores through canon, the order of day and number; every reason to set a voucher aside, the first
    that holds; the scope (out of scope, partly out of scope, scope=None);
  * pair: step A (received, late across Thursday and Friday and the damage store's week, the earliest
    free receipt, the window's two ends, the most pairs); step B through each of the gate's three ways,
    its refusals and its ranking, back at the source; step C at an overlap of exactly one half and
    below, its gate, its ranking, the quantities item by item; twins of two and of three (one open item,
    every number named, the pairs ambiguous; in grace only while every issue of the set is; no twins
    without a receipt; twins all received); the exchange after B (a store that booked in another's
    transfer and issued it again: received elsewhere and received; refused by the gate; a set of three;
    the oldest set first); an issue sent to its own store and the receipt set aside with it (the nearest
    number; at another store, or refused by the gate, an orphan); the damage store's grace held to
    /yq-stock damage's on the same rows; the rest (in grace on the deadline, not received after it, the
    possible matches and their window, orphans before the ledger); the counts add up to the legs; by_issue
    and by_receipt name every leg once; shuffled rows and a second run give an equal Result;
  * summarise: the whole span equals counts(); a window of issue dates; a store out, in and either way;
    a receipt with no issue and a voucher set aside counted where they belong; its refusals;
  * pairing.py imports nothing but the allowed standard library modules, reads no file and no clock;
  * the seam: the later releases' config keys are optional and checked once present; the count
    sheets' folder;
  * the real 7-Oct ledger (SKIP without it or golden/pairing.json): the counts, every exception, the cases
    the exchange reproduces, and the one cause, recorded voucher by voucher, that accounts for each
    difference left from the 8-Oct measurement; the real config's aliases all joined by canon alone.
"""
from __future__ import annotations

import ast
import dataclasses
import io
import json
import os
import random
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

from scripts.ops import db  # noqa: E402,F401  (first, as every command does)
from scripts.ops import config, ledger, pairing, paths, stock_damage  # noqa: E402

TESTS: list[tuple[str, object]] = []
PAIRING = ROOT / "scripts" / "ops" / "pairing.py"
ALLOWED_IMPORTS = {"__future__", "dataclasses", "datetime", "decimal", "fractions", "hashlib", "re", "bisect",
                   "collections", "typing"}

MAIN, DAMAGE, SAMPLES = "Main Store", "Damage Store", "Samples Store"
# Invented digit runs, put together here so that this file holds none: two shaped like personal ID numbers
# that share their last three digits, and one shaped like a card number.
ID_A = "12345" + "6789"
ID_B = "98765" + "4789"
CARD = "4" + "1" * 15
# One week, Sunday to Saturday (the stores work Saturday to Thursday; Friday is off).
SUN, MON, TUE, WED, THU, FRI, SAT = (date(2026, 10, 11) + timedelta(days=n) for n in range(7))


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def raises(kind, fn) -> Exception:
    try:
        fn()
    except kind as exc:
        return exc
    raise AssertionError(f"expected {kind.__name__}")


# ── the constants and the rules ───────────────────────────────────────────────

@test("constants: the two legs as the ledger names them; the damage grace is /yq-stock damage's own; the plan's defaults")
def _():
    assert pairing.ISSUE == ledger.ISSUE and pairing.RECEIVE == ledger.RECEIVE
    assert pairing.NARR_LEN == ledger.NARR_LEN
    r = pairing.Rules()
    assert r.damage_grace_days == stock_damage.GRACE_DAYS == 7
    assert (r.lag_max, r.dn_max, r.quick_lag, r.overlap_min, r.possible_lag_min, r.grace_working_days) == \
        (21, 8, 1, Fraction(1, 2), -7, 1)
    assert r.weekend == (4,) and date(2026, 10, 16).weekday() in r.weekend and r.holidays == frozenset()
    raises(dataclasses.FrozenInstanceError, lambda: setattr(r, "lag_max", 30))
    assert hash(r) == hash(pairing.Rules()) and r == pairing.Rules() and pairing.Rules(lag_max=14) != r
    assert pairing.KINDS == ("main", "damage", "not_counted", "sim", "van", "outlet", "other")


# ── stores ────────────────────────────────────────────────────────────────────

@test("canon: an alias first (matched folded), then case and runs of spaces folded, then a trailing code dropped")
def _():
    s = pairing.StoreRules(MAIN, aliases={"Shop  1 S-1": "Shop 1", "Old Van": "Van B - Acc WH 20-2", "": "x",
                                          "Blank": "  "})
    assert s.canon("shop 1 s-1") == s.canon("  SHOP   1   S-1 ") == "shop 1"
    assert s.canon("old  VAN") == "van b - acc wh"                         # the alias's target is folded too
    assert s.canon("Van A - Acc WH 10-1") == s.canon("Van A  -  ACC WH") == "van a - acc wh"
    assert s.canon("Store 10-1 East") == "store 10-1 east"                 # a code only counts at the end
    assert s.canon("Store 10-1 22-3") == "store 10-1"                      # one code, the last one
    assert s.canon("Store-10-1") == "store-10-1" and s.canon("10-1") == "10-1"
    assert s.canon("Blank") == "blank" and s.canon("") == s.canon(None) == s.canon("   ") == ""
    # with no aliases at all, a report's spelling (the name and its code) is the name the ledger writes
    bare = pairing.StoreRules(MAIN)
    for spelt, name in (("Main Store 01-1", "Main Store"), ("Van A - Acc WH 10-1", "Van A - Acc WH"),
                        ("Causeway 30-3", "Causeway")):
        assert bare.canon(spelt) == bare.canon(name), spelt
    assert s.aliases["Shop  1 S-1"] == "Shop 1"                            # the field keeps what it was given


@test("kind: main, damage, not counted, SIM, van, outlet, other: the first that holds, compared after canon")
def _():
    s = pairing.StoreRules(MAIN, damage=DAMAGE, not_counted=[DAMAGE, SAMPLES, "Van Z - Acc WH"],
                           aliases={"Main Store M-1": MAIN})
    cases = {"MAIN  store": "main", "Main Store 01-1": "main", "main store m-1": "main",
             "damage store": "damage",                       # in not_counted too: damage comes first
             "Samples Store": "not_counted", "Van Z - Acc WH 10-9": "not_counted",   # before the van rule
             "Van A - SIM WH": "sim", "SIM Warehouse": "sim", "sim  warehouse 30-3": "sim",
             "Van A - Acc WH": "van", "Van A - Acc WH 10-1": "van", "van a  -  acc wh": "van",
             "Causeway": "outlet", "YQ ROADSHOW": "outlet", "YQ Roadshow 40-4": "outlet",
             "Head Office": "other", "Acc WH": "other", "": "other", None: "other"}
    for name, kind in cases.items():
        assert s.kind(name) == kind, (name, s.kind(name), kind)
    assert pairing.StoreRules("Causeway").kind("causeway") == "main"          # the main store comes first
    assert pairing.StoreRules(MAIN).kind(DAMAGE) == "other"                   # no damage store set up
    assert pairing.StoreRules(MAIN, damage="").kind("") == "other"
    raises(ValueError, lambda: pairing.StoreRules("  "))
    raises(ValueError, lambda: pairing.StoreRules(None))
    # a list from config and a tuple are one rule set, and it can key a table
    one = pairing.StoreRules(MAIN, not_counted=[SAMPLES], aliases={"a": "b"})
    two = pairing.StoreRules(MAIN, not_counted=(SAMPLES,), aliases={"a": "b"})
    assert one == two and hash(one) == hash(two) and one.not_counted == (SAMPLES,)
    assert one != pairing.StoreRules(MAIN, not_counted=(SAMPLES,))            # other aliases, other rules
    raises(dataclasses.FrozenInstanceError, lambda: setattr(one, "main", "x"))


# ── voucher numbers ───────────────────────────────────────────────────────────

@test("voucher numbers: a key with a total order; the distance only within one series, year and month")
def _():
    assert pairing.parse_number("SIO:X-26-01-1") == (0, "x", 26, 1, 1, "SIO:X-26-01-1")
    assert pairing.parse_number("STRV:AB-CD-26-10-15") == (0, "ab-cd", 26, 10, 15, "STRV:AB-CD-26-10-15")
    assert pairing.parse_number("SIO:X:Y-26-10-3")[1] == "x:y"              # the first colon ends the prefix
    for junk in ("ShoStk 12", "SIO:X-26-1-1", "X-26-01-1", "SIO:X-26-01-", "SIO:X-26-01-1\n", "SIO:X-2a-01-1"):
        assert pairing.parse_number(junk) == (1, junk), junk
    assert pairing.parse_number("") == (1, "") and pairing.parse_number(None) == (1, "")
    numbers = ["SIO:A-26-01-5", "SIO:X-25-12-9", "SIO:X-26-01-2", "SIO:X-26-01-10", "STRV:X-26-01-10",
               "SIO:X-26-02-1", "junk", "zz"]
    for seed in range(5):                                       # the same order from any order
        shuffled = numbers[:]
        random.Random(seed).shuffle(shuffled)
        assert sorted(shuffled, key=pairing.parse_number) == numbers, shuffled
    assert len({pairing.parse_number(n) for n in numbers}) == len(numbers)    # two numbers never share a key
    d = pairing.number_distance
    assert d("SIO:X-26-01-5", "STRV:X-26-01-3") == d("STRV:X-26-01-3", "SIO:X-26-01-5") == 2   # prefix aside
    assert d("SIO:X-26-01-5", "SIO:X-26-01-5") == 0 and d("SIO:x-26-01-5", "STRV:X-26-01-12") == 7
    assert d("SIO:X-26-01-5", "STRV:X-26-02-5") is None                       # another month
    assert d("SIO:X-25-01-5", "STRV:X-26-01-5") is None                       # another year
    assert d("SIO:X-26-01-5", "STRV:Y-26-01-5") is None                       # another series
    assert d("junk", "SIO:X-26-01-1") is None and d(None, None) is None


# ── deadlines ─────────────────────────────────────────────────────────────────

@test("deadline: the first working day after the issue (Friday off, holidays as a parameter); the damage store a week")
def _():
    due = {SUN: MON, MON: TUE, TUE: WED, WED: THU, THU: SAT, FRI: SAT, SAT: SUN + timedelta(days=7)}
    for issued, by in due.items():
        assert issued.weekday() == (6, 0, 1, 2, 3, 4, 5)[list(due).index(issued)]
        for kind in ("van", "outlet", "main", "other", "not_counted", "sim"):
            assert pairing.deadline(issued, kind) == by, (issued, kind)
        assert pairing.deadline(issued, "van", pairing.Rules()) == by
    holiday = pairing.Rules(holidays=frozenset({THU}))
    assert pairing.deadline(WED, "van", holiday) == SAT                     # Thursday a holiday, Friday off
    assert pairing.deadline(TUE, "van", holiday) == WED and pairing.deadline(THU, "van", holiday) == SAT
    two = pairing.Rules(grace_working_days=2)
    assert pairing.deadline(THU, "van", two) == SUN + timedelta(days=7) and pairing.deadline(WED, "van", two) == SAT
    assert pairing.deadline(WED, "van", pairing.Rules(grace_working_days=0)) == WED
    assert pairing.deadline(datetime(2026, 10, 15, 9, 30), "van") == SAT         # a time of day is set aside
    # the damage store: the issue day plus the grace, every day counted
    assert pairing.deadline(THU, "damage") == THU + timedelta(days=7)
    assert pairing.deadline(THU, "damage", holiday) == THU + timedelta(days=7)
    assert pairing.deadline(THU, "damage", pairing.Rules(damage_grace_days=3)) == THU + timedelta(days=3)
    raises(ValueError, lambda: pairing.deadline(THU, "van", pairing.Rules(weekend=tuple(range(7)))))


# ── held equal to the ledger ──────────────────────────────────────────────────

@test("dec is ledger._dec: the same Decimal, written the same way, for every kind of cell; the same refusal")
def _():
    grouped = "1,234" + ".5678"                       # built here, as the repository scan would read a literal
    cells = [None, "", float("nan"), 0, 0.0, -0.0, 1, 7, 12.5, 0.1 + 0.2, 1.1 * 3, 2.0005, -2.0005, grouped,
             " 7 ", "100", "-0.0004", Decimal("2.0005"), Decimal("-3.14159"), Decimal("1E+3"), 10 ** 3, True,
             Decimal("0.000")]
    for x in cells:
        mine, theirs = pairing.dec(x), ledger._dec(x)
        assert mine == theirs and repr(mine) == repr(theirs), (x, mine, theirs)
    assert pairing.dec(0.1 + 0.2) == Decimal("0.3") and repr(pairing.dec(Decimal("1E+3"))) == "Decimal('1000')"
    for bad in ("abc", "inf", float("inf"), Decimal("NaN"), "1e999999", "1.2.3"):
        mine = raises(ValueError, lambda b=bad: pairing.dec(b))
        theirs = raises(ValueError, lambda b=bad: ledger._dec(b))
        assert str(mine) == str(theirs), bad


@test("narr_key is ledger._narr_key: the mask before the hash, spaces and capitals folded, twelve letters")
def _():
    from scripts import ingest                      # the loader's own mask (pandas: imported only here)
    mask = ingest.mask_personal_ids
    texts = [None, float("nan"), "", "   ", "zebra run", "  Zebra   RUN ", "sent to " + ID_A, "sent to " + ID_B,
             "card " + CARD, 42, "Ünïcode Straße", "line\nbreak"]
    for t in texts:
        assert pairing.narr_key(t, mask) == ledger._narr_key(t, mask), t
        assert pairing.narr_key(t) == ledger._narr_key(t, lambda s: s), t     # no mask: the text as it is
    assert pairing.narr_key("zebra run", mask) == pairing.narr_key("  Zebra   RUN ", mask) is not None
    assert pairing.narr_key(None) is pairing.narr_key("   ", mask) is pairing.narr_key(float("nan")) is None
    # two ID numbers alike in their last three digits are one masked text, so one hash; unmasked, two
    assert pairing.narr_key("sent to " + ID_A, mask) == pairing.narr_key("sent to " + ID_B, mask)
    assert pairing.narr_key("sent to " + ID_A) != pairing.narr_key("sent to " + ID_B)
    key = pairing.narr_key("card " + CARD, mask)
    assert len(key) == pairing.NARR_LEN and set(key) <= ledger.HASH_LETTERS and not any(c.isdigit() for c in key)


# ── the legs ──────────────────────────────────────────────────────────────────

VAN_A, VAN_B, VAN_C = "Van A - Acc WH", "Van B - Acc WH", "Van C - Acc WH"
CABLE, CHARGER, BANK, BUDS, CASE = ("ITEM-01 Cable One C 01", "ITEM-02 Charger Two CH2", "ITEM-03 Power Bank PB3",
                                    "ITEM-04 Ear Buds EB4", "ITEM-05 Case Five CS5")
STORES = pairing.StoreRules(MAIN, damage=DAMAGE, not_counted=[DAMAGE, SAMPLES], aliases={"Van A ACC": VAN_A})
ZEBRA, WALRUS = pairing.narr_key("zebra run"), pairing.narr_key("walrus walk")
NIL = Decimal(0)


def at(n: int) -> date:
    """A day counted from the Sunday of the test week (at(0) is SUN)."""
    return SUN + timedelta(days=n)


def num(kind: str, n: int, series: str = "T", month: int = 10) -> str:
    """An invented voucher number: kind 'i' an issue (SIO), 'r' a receipt (STRV)."""
    return f"{'SIO' if kind == 'i' else 'STRV'}:{series}-26-{month:02d}-{n}"


def mv(day, number, item, wh, units, to=None, *, value=None, narr=None, vtype=None, q_in=None, q_out=None) -> dict:
    """One ledger row shaped as scripts.ops.ledger shapes it. An issue (SIO...) moves `units` out of `wh` to
    `to`; anything else books them into `wh`. value: the BHD the line states (None: one a unit; 0: none)."""
    issue = number.startswith("SIO")
    units = Decimal(units)
    stated = units if value is None else Decimal(value)
    return {"item": item, "date": day, "voucher": number,
            "vtype": vtype or (pairing.ISSUE if issue else pairing.RECEIVE), "wh": wh, "to_wh": to,
            "qty_in": Decimal(q_in) if q_in is not None else (NIL if issue else units),
            "qty_out": Decimal(q_out) if q_out is not None else (units if issue else NIL),
            "value_in": NIL if issue else stated, "value_out": stated if issue else NIL, "narr": narr}


def issue(n, day, to, lines, *, frm=MAIN, narr=None, series="T", month=10) -> list[dict]:
    return [mv(day, num("i", n, series, month), item, frm, units, to, narr=narr) for item, units in lines]


def receipt(n, day, wh, lines, *, narr=None, series="T", month=10) -> list[dict]:
    return [mv(day, num("r", n, series, month), item, wh, units, narr=narr) for item, units in lines]


def run(rows, data_date=None, scope=None, rules=None):
    """(Legs, Result) of the rows, the data date being the last day of the rows unless given."""
    legs = pairing.legs_from_rows(rows, stores=STORES, scope=scope)
    return legs, pairing.pair(legs, data_date=data_date or max(r["date"] for r in rows),
                              rules=rules or pairing.Rules())


def got(result, number):
    """What became of a leg: its Pair, Open or Orphan."""
    return result.by_issue[number] if number.startswith("SIO") else result.by_receipt[number]


@test("legs_from_rows: whole vouchers as ledger.vouchers groups them (lines, value, unvalued units, narration), stores through canon, in order of day then number")
def _():
    sale = mv(at(-3), "SI:SI-T-26-10-1", CABLE, VAN_B, 0, vtype=ledger.SALE, q_in=0, q_out=2)
    rows = [sale]
    rows += [mv(MON, num("i", 7), CABLE, MAIN, 6, "VAN A  -  acc wh 10-1"),
             mv(MON, num("i", 7), CABLE, MAIN, 4, "VAN A  -  acc wh 10-1"),
             mv(MON, num("i", 7), CHARGER, MAIN, 5, "VAN A  -  acc wh 10-1", value=0),   # a line that states no value
             mv(MON, num("i", 7), BANK, MAIN, 0, "VAN A  -  acc wh 10-1")]               # a line of nothing
    rows += receipt(10, TUE, "Van A ACC", [(CABLE, 10), (CHARGER, 5)]) + receipt(2, TUE, "van a acc", [(BUDS, 1)])
    rows += issue(3, SUN, MAIN, [(BUDS, 1)], frm=VAN_B, narr=ZEBRA)
    legs = pairing.legs_from_rows(iter(rows), stores=STORES, scope=None)          # any iterable will do
    assert [leg.no for leg in legs.issues] == [num("i", 3), num("i", 7)]
    assert [leg.no for leg in legs.receipts] == [num("r", 2), num("r", 10)]       # 2 before 10: numbers sort as numbers
    assert [leg.order for leg in legs.issues + legs.receipts] == [0, 1, 0, 1]
    i7 = legs.issues[1]
    assert (i7.kind, i7.day, i7.wh, i7.to, i7.to_kind, i7.narr) == ("issue", MON, "main store", "van a - acc wh", "van", None)
    assert i7.lines == ((CABLE, Decimal(10)), (CHARGER, Decimal(5))) and i7.items == {CABLE, CHARGER}
    assert (i7.value, i7.unvalued, i7.units) == (Decimal(10), Decimal(5), Decimal(15))
    i3 = legs.issues[0]
    assert (i3.wh, i3.to, i3.to_kind, i3.narr) == ("van b - acc wh", "main store", "main", ZEBRA)
    r2 = legs.receipts[0]
    assert (r2.kind, r2.wh, r2.to, r2.to_kind, r2.lines) == ("receipt", "van a - acc wh", None, None, ((BUDS, Decimal(1)),))
    assert (legs.first, legs.last, legs.aside, legs.out_of_scope, legs.stores) == (at(-3), TUE, (), 0, STORES)
    # the same vouchers as the ledger's own grouping (its aliases are the same)
    config.use({"warehouses.aliases": {"Van A ACC": VAN_A}})
    try:
        whole = ledger.vouchers(rows, (ledger.ISSUE, ledger.RECEIVE))
    finally:
        config.use(None)
    assert set(whole) == {leg.no for leg in legs.issues + legs.receipts}
    for leg in legs.issues + legs.receipts:
        v = whole[leg.no]
        assert {item: units for item, units in v["lines"].items() if units} == dict(leg.lines), leg.no
        assert (v["value"], v["unvalued"], v["narr"], v["date"]) == (leg.value, leg.unvalued, leg.narr, leg.day), leg.no
    raises(dataclasses.FrozenInstanceError, lambda: setattr(i7, "day", SUN))
    raises(TypeError, lambda: pairing.legs_from_rows(rows, stores=None, scope=None))
    raises(TypeError, lambda: pairing.legs_from_rows(rows, stores=STORES, scope=CABLE))     # one name is no scope
    raises(TypeError, lambda: pairing.legs_from_rows(rows, stores=STORES))                  # the scope is said, always


@test("legs_from_rows: a voucher is set aside with the first reason that holds (mixed, wrong way, no store, no units, no or its own destination, partly in scope)")
def _():
    cases = {   # number -> (its rows, the reason)
        num("i", 1): ([mv(SUN, num("i", 1), CABLE, MAIN, 1, VAN_A), mv(MON, num("i", 1), CHARGER, MAIN, 1, VAN_A)],
                      "mixed: date"),
        num("i", 2): ([mv(SUN, num("i", 2), CABLE, MAIN, 1, VAN_A, narr=ZEBRA), mv(SUN, num("i", 2), CHARGER, MAIN, 1, VAN_A)],
                      "mixed: narration"),
        num("i", 3): ([mv(SUN, num("i", 3), CABLE, MAIN, 1, VAN_A), mv(SUN, num("i", 3), CHARGER, VAN_B, 1, VAN_C)],
                      "mixed: store, destination"),
        num("i", 4): ([mv(SUN, num("i", 4), CABLE, MAIN, 1, VAN_A), mv(SUN, num("i", 4), CHARGER, VAN_A, 1, vtype=pairing.RECEIVE,
                                                                        q_in=1, q_out=0)],
                      "mixed: type, store"),
        num("i", 5): ([mv(SUN, num("i", 5), CABLE, MAIN, 1, VAN_A, q_in=1)], "moves the wrong way"),
        num("i", 6): ([mv(SUN, num("i", 6), CABLE, MAIN, -2, VAN_A)], "moves the wrong way"),       # units below nought
        num("r", 7): ([mv(SUN, num("r", 7), CABLE, VAN_A, 1, q_out=1)], "moves the wrong way"),
        num("r", 8): ([mv(SUN, num("r", 8), CABLE, "  ", 1)], "names no store"),
        num("i", 9): ([mv(SUN, num("i", 9), CABLE, MAIN, 0, VAN_A), mv(SUN, num("i", 9), BANK, MAIN, 0, VAN_A)], "no units"),
        num("i", 10): (issue(10, SUN, None, [(CABLE, 1)]), "names no destination"),
        num("i", 11): (issue(11, SUN, "VAN A - ACC WH 10-1", [(CABLE, 1)], frm=VAN_A), "sent to its own store"),
        # one store under two spellings (an alias) is not mixed; two destinations are
        num("i", 12): (issue(12, SUN, VAN_B, [(CABLE, 1)], frm="Van A ACC") + [mv(SUN, num("i", 12), CHARGER, VAN_A, 0, VAN_A)],
                       "mixed: destination"),
        "": ([mv(SUN, "", CABLE, MAIN, 1, VAN_A)], "no number"),
        # the first reason that holds: mixed before the wrong way
        num("i", 13): ([mv(SUN, num("i", 13), CABLE, MAIN, 1, VAN_A, q_in=1), mv(MON, num("i", 13), BANK, MAIN, 1, VAN_A)],
                       "mixed: date"),
    }
    rows = [r for lines, _reason in cases.values() for r in lines] + issue(20, SUN, VAN_A, [(CABLE, 1)])
    legs = pairing.legs_from_rows(rows, stores=STORES, scope=None)
    told = {a.voucher: a.reason for a in legs.aside}
    assert told == {n: reason for n, (_lines, reason) in cases.items()}, told
    assert [leg.no for leg in legs.issues] == [num("i", 20)] and legs.receipts == () and legs.out_of_scope == 0
    own = next(a for a in legs.aside if a.voucher == num("i", 11))
    assert (own.kind, own.day, own.wh, own.to) == ("issue", SUN, "van a - acc wh", "van a - acc wh")
    assert (own.lines, own.narr, own.partner) == (((CABLE, Decimal(1)),), None, None)       # its lines are kept
    two = next(a for a in legs.aside if a.voucher == num("i", 2))
    assert two.lines == ((CABLE, Decimal(1)), (CHARGER, Decimal(1))) and two.narr == ZEBRA  # every line, summed
    assert next(a for a in legs.aside if a.voucher == num("i", 9)).lines == ()             # nothing moved
    assert next(a for a in legs.aside if a.voucher == num("r", 7)).kind == "receipt"
    assert [a.voucher for a in legs.aside] == sorted(told, key=lambda n: (next(a.day for a in legs.aside if a.voucher == n),
                                                                          pairing.parse_number(n)))
    for reason in (pairing.MIXED, pairing.WRONG_WAY, pairing.NO_STORE, pairing.NO_UNITS, pairing.NO_DESTINATION,
                   pairing.OWN_STORE, pairing.NO_NUMBER):
        assert any(r.startswith(reason) for r in told.values()), reason


@test("legs_from_rows: the scope - no item in it is out of scope (and only that), some of them is set aside, None keeps every item")
def _():
    rows = (issue(1, SUN, VAN_A, [(CABLE, 2), (CHARGER, 1)])                         # partly in scope
            + issue(2, SUN, VAN_A, [(BANK, 1)])                                      # out of scope
            + [mv(SUN, num("i", 3), BANK, MAIN, 1, VAN_A), mv(MON, num("i", 3), BANK, MAIN, 1, VAN_A)]   # mixed, out of scope
            + issue(4, SUN, VAN_A, [(CABLE, 3), (BANK, 0)])                          # its out-of-scope line moves nothing
            + receipt(5, SUN, VAN_A, [(BANK, 1)]) + receipt(6, SUN, VAN_A, [(CABLE, 3)]))
    legs = pairing.legs_from_rows(rows, stores=STORES, scope=[CABLE])
    assert {a.voucher: a.reason for a in legs.aside} == {num("i", 1): "partly out of scope"}
    assert legs.out_of_scope == 3                                                    # SIO-2, SIO-3, STRV-5
    assert [leg.no for leg in legs.issues] == [num("i", 4)] and legs.issues[0].lines == ((CABLE, Decimal(3)),)
    assert [leg.no for leg in legs.receipts] == [num("r", 6)]
    every = pairing.legs_from_rows(rows, stores=STORES, scope=None)
    assert every.out_of_scope == 0 and {a.reason for a in every.aside} == {"mixed: date"}
    assert [leg.no for leg in every.issues] == [num("i", 1), num("i", 2), num("i", 4)]
    assert pairing.legs_from_rows(rows, stores=STORES, scope=frozenset()).out_of_scope == 6


# ── pairing ───────────────────────────────────────────────────────────────────

@test("step A: received where it was sent; late after the deadline (Thursday to Saturday, Friday, the damage store's week); the basis can be the contents alone")
def _():
    rows = (issue(1, SUN, VAN_A, [(CABLE, 10)], narr=ZEBRA) + receipt(1, MON, VAN_A, [(CABLE, 10)], narr=ZEBRA)
            + issue(2, SUN, VAN_A, [(CABLE, 2)]) + receipt(30, TUE, VAN_A, [(CABLE, 2)])         # a day late, nothing else holds
            + issue(3, THU, VAN_B, [(CHARGER, 1)]) + receipt(3, SAT, VAN_B, [(CHARGER, 1)])      # Thursday: due Saturday
            + issue(4, THU, VAN_B, [(CHARGER, 2)]) + receipt(4, at(7), VAN_B, [(CHARGER, 2)])    # ... the Sunday is late
            + issue(5, FRI, VAN_B, [(CHARGER, 3)]) + receipt(5, SAT, VAN_B, [(CHARGER, 3)])      # a Friday voucher
            + issue(6, SUN, DAMAGE, [(BANK, 1)]) + receipt(6, at(7), DAMAGE, [(BANK, 1)])        # the damage store: a week
            + issue(7, SUN, DAMAGE, [(BANK, 2)]) + receipt(7, at(8), DAMAGE, [(BANK, 2)]))       # ... and a day more
    _legs, res = run(rows, data_date=at(9))
    outcome = {p.issue.no: p.outcome for p in res.pairs}
    assert outcome == {num("i", 1): "received", num("i", 2): "late", num("i", 3): "received", num("i", 4): "late",
                       num("i", 5): "received", num("i", 6): "received", num("i", 7): "late"}, outcome
    assert all(p.step == "A" and p.received_at == p.issue.to and not p.diffs and not p.back_at_source for p in res.pairs)
    one = got(res, num("i", 1))
    assert (one.lag, one.dn, one.same_narr, one.basis) == (1, 0, True, ("narration", "number", "date"))
    two = got(res, num("i", 2))
    assert (two.lag, two.dn, two.same_narr, two.basis) == (2, 28, False, ())             # paired on its contents alone
    assert (res.open, res.orphans, res.aside) == ((), (), ())


@test("step A: issues in order take the earliest free receipt in 0..21 days (the window's two ends), the most pairs there can be")
def _():
    rows = (issue(1, SUN, VAN_A, [(CABLE, 1)]) + receipt(60, at(21), VAN_A, [(CABLE, 1)])     # 21 days: in
            + issue(2, SUN, VAN_A, [(CHARGER, 2)]) + receipt(61, at(22), VAN_A, [(CHARGER, 2)])   # 22 days: out
            + issue(3, MON, VAN_A, [(BUDS, 3)]) + receipt(62, SUN, VAN_A, [(BUDS, 3)])        # a day before: out
            # two issues, two receipts: the first issue takes the earlier receipt, so the second still has one
            # (had it taken the later one, the second issue's window would hold none)
            + issue(4, SUN, VAN_B, [(BANK, 4)]) + issue(5, at(15), VAN_B, [(BANK, 4)])
            + receipt(70, at(5), VAN_B, [(BANK, 4)]) + receipt(71, at(20), VAN_B, [(BANK, 4)]))
    _legs, res = run(rows, data_date=at(30))
    pairs = {p.issue.no: p.receipt.no for p in res.pairs}
    assert pairs == {num("i", 1): num("r", 60), num("i", 4): num("r", 70), num("i", 5): num("r", 71)}, pairs
    assert got(res, num("i", 1)).lag == 21 and got(res, num("i", 1)).outcome == "late"
    assert got(res, num("i", 2)).outcome == got(res, num("i", 3)).outcome == "not_received"
    assert {o.receipt.no for o in res.orphans} == {num("r", 61), num("r", 62)}
    # the two issues reach one receipt in common: twins, all received, so nothing is ambiguous
    assert got(res, num("i", 4)).twins == got(res, num("i", 5)).twins == (num("i", 4), num("i", 5))
    assert not any(p.ambiguous for p in res.pairs)
    # the receipt a day before is a possible match of its issue (and the issue of the receipt); 22 days is not
    assert [r.no for r in got(res, num("i", 3)).possible] == [num("r", 62)]
    assert [i.no for i in got(res, num("r", 62)).possible] == [num("i", 3)]
    assert got(res, num("i", 2)).possible == () and got(res, num("r", 61)).possible == ()


@test("step B: received elsewhere through each way of the gate (narration, number, date); refused when none holds; back at the source")
def _():
    rows = (issue(1, SUN, VAN_A, [(CABLE, 1)], narr=ZEBRA) + receipt(41, WED, VAN_B, [(CABLE, 1)], narr=ZEBRA)   # narration
            + issue(5, SUN, VAN_A, [(CABLE, 2)]) + receipt(9, at(5), VAN_C, [(CABLE, 2)])          # numbers 4 apart
            + issue(20, SUN, VAN_A, [(CABLE, 3)]) + receipt(80, MON, VAN_B, [(CABLE, 3)])          # booked the next day
            + issue(30, SUN, VAN_A, [(CABLE, 4)], narr=ZEBRA) + receipt(39, TUE, VAN_B, [(CABLE, 4)], narr=WALRUS)  # 9 apart, 2 days
            + issue(31, SUN, VAN_A, [(CABLE, 5)]) + receipt(90, TUE, VAN_B, [(CABLE, 5)])          # blank narrations are no match
            + issue(32, SUN, VAN_A, [(CABLE, 6)]) + receipt(32, WED, VAN_B, [(CABLE, 6)], month=11)   # another month: no distance
            + issue(33, SUN, VAN_A, [(BANK, 1)]) + receipt(33, SUN, MAIN, [(BANK, 1)]))            # booked back in by the sender
    _legs, res = run(rows, data_date=at(9))
    b = {p.issue.no: p for p in res.pairs}
    assert set(b) == {num("i", 1), num("i", 5), num("i", 20), num("i", 33)} and all(p.outcome == "elsewhere" and p.step == "B"
                                                                                    for p in b.values())
    assert b[num("i", 1)].basis == ("narration",) and b[num("i", 1)].same_narr and b[num("i", 1)].dn == 40
    assert b[num("i", 5)].basis == ("number",) and b[num("i", 5)].dn == 4 and b[num("i", 5)].received_at == "van c - acc wh"
    assert b[num("i", 20)].basis == ("date",) and b[num("i", 20)].lag == 1
    assert b[num("i", 33)].back_at_source and not b[num("i", 1)].back_at_source
    assert b[num("i", 33)].basis == ("number", "date")
    refused = {num("i", 30): num("r", 39), num("i", 31): num("r", 90), num("i", 32): num("r", 32, month=11)}
    for i_no, r_no in refused.items():
        assert got(res, i_no).outcome == "not_received" and isinstance(got(res, r_no), pairing.Orphan), i_no
        assert [r.no for r in got(res, i_no).possible] == [r_no]          # each one shown as a possible match
        assert [i.no for i in got(res, r_no).possible] == [i_no]


@test("step B ranks the candidates: the narration before the number before the date alone; then the distance, the lag, the order")
def _():
    rows = (issue(50, SUN, VAN_A, [(CASE, 3)], narr=ZEBRA) + receipt(90, THU, VAN_B, [(CASE, 3)], narr=ZEBRA)
            + receipt(51, SUN, VAN_C, [(CASE, 3)])                                   # the number, the same day: second
            + issue(60, SUN, VAN_A, [(BANK, 2)]) + receipt(63, TUE, VAN_B, [(BANK, 2)]) + receipt(61, WED, VAN_C, [(BANK, 2)])
            + issue(70, SUN, VAN_A, [(BUDS, 2)]) + receipt(5, MON, VAN_B, [(BUDS, 2)], series="U")
            + receipt(6, SUN, VAN_C, [(BUDS, 2)], series="U")                        # no distance for either: the lag
            + issue(80, SUN, VAN_A, [(CHARGER, 9)]) + receipt(2, SUN, VAN_B, [(CHARGER, 9)], series="U")
            + receipt(1, SUN, VAN_C, [(CHARGER, 9)], series="U"))                    # all equal: the order
    _legs, res = run(rows, data_date=at(9))
    taken = {p.issue.no: p.receipt.no for p in res.pairs}
    assert taken == {num("i", 50): num("r", 90), num("i", 60): num("r", 61), num("i", 70): num("r", 6, "U"),
                     num("i", 80): num("r", 1, "U")}, taken
    assert {o.receipt.no for o in res.orphans} == {num("r", 51), num("r", 63), num("r", 5, "U"), num("r", 2, "U")}


@test("step C: other quantities where it was sent; the items shared at least one half (exactly one half passes, two fifths do not); the gate; overlap first")
def _():
    rows = (issue(100, MON, VAN_A, [(CABLE, 10), (CHARGER, 5)], narr=WALRUS)
            + receipt(100, MON, VAN_A, [(CABLE, 10), (CHARGER, 4)], narr=WALRUS)
            + issue(110, MON, VAN_B, [(BANK, 2)]) + receipt(111, MON, VAN_B, [(BANK, 2), (CASE, 1)])      # Jaccard 1/2
            + issue(120, MON, VAN_C, [(BUDS, 1), (CASE, 2)])
            + receipt(121, MON, VAN_C, [(BUDS, 1), (CASE, 2), (BANK, 1), (CHARGER, 1), (CABLE, 1)])     # Jaccard 2/5
            + issue(130, SUN, DAMAGE, [(CABLE, 3), (BANK, 3)]) + receipt(190, WED, DAMAGE, [(CABLE, 3), (BANK, 2)])  # gate
            + issue(140, TUE, MAIN, [(CABLE, 7), (CHARGER, 7)], frm=VAN_B)
            + receipt(142, TUE, MAIN, [(CABLE, 7), (CHARGER, 7), (CASE, 1)])         # Jaccard 2/3, the nearer number
            + receipt(149, TUE, MAIN, [(CABLE, 7), (CHARGER, 6)]))                   # Jaccard 1: taken first
    _legs, res = run(rows, data_date=at(9))
    c = {p.issue.no: p for p in res.pairs}
    assert set(c) == {num("i", 100), num("i", 110), num("i", 140)} and all(p.outcome == "qty_differs" and p.step == "C"
                                                                           for p in c.values())
    assert c[num("i", 100)].diffs == ((CABLE, Decimal(10), Decimal(10)), (CHARGER, Decimal(5), Decimal(4)))
    assert c[num("i", 100)].basis == ("narration", "number", "date")
    assert c[num("i", 110)].diffs == ((BANK, Decimal(2), Decimal(2)), (CASE, Decimal(0), Decimal(1)))
    assert c[num("i", 140)].receipt.no == num("r", 149) and c[num("i", 140)].received_at == "main store"
    assert got(res, num("i", 120)).outcome == "not_received" and isinstance(got(res, num("r", 121)), pairing.Orphan)
    assert got(res, num("i", 130)).outcome == "not_received" and isinstance(got(res, num("r", 190)), pairing.Orphan)
    assert got(res, num("i", 130)).deadline == at(7)               # to the damage store: a week
    assert got(res, num("i", 120)).possible == ()                  # other contents are never a possible match
    # one half is the rule's own figure: raised a little, the half no longer passes
    _legs, stricter = run(rows, data_date=at(9), rules=pairing.Rules(overlap_min=Fraction(51, 100)))
    assert num("i", 110) not in {p.issue.no for p in stricter.pairs}


@test("twins: two identical issues to one store and one receipt are ONE open item naming both; the pair is ambiguous; in grace until the deadline")
def _():
    rows = (issue(1, SUN, VAN_A, [(CABLE, 2)]) + issue(2, SUN, VAN_A, [(CABLE, 2)]) + receipt(1, MON, VAN_A, [(CABLE, 2)])
            + issue(3, SUN, VAN_B, [(BANK, 1)]) + issue(4, SUN, VAN_B, [(BANK, 1)]))     # no receipt at all: two issues
    _legs, res = run(rows, data_date=WED)
    twins = [o for o in res.open if o.twins]
    assert len(twins) == 1
    t = twins[0]
    assert (t.outcome, t.missing, [i.no for i in t.issues], t.twins) == ("twins", 1, [num("i", 1), num("i", 2)],
                                                                          (num("i", 1), num("i", 2)))
    assert t.deadline == MON and t.store == "van a - acc wh" and t.possible == ()
    p = got(res, num("i", 1))
    assert isinstance(p, pairing.Pair) and p.ambiguous and p.twins == t.twins
    assert got(res, num("i", 2)) is t                        # the open item, which never says which issue it is
    singles = [o for o in res.open if not o.twins]
    assert [(o.outcome, o.missing, o.issues[0].no) for o in singles] == [("not_received", 1, num("i", 3)),
                                                                         ("not_received", 1, num("i", 4))]
    c = res.counts()
    assert (c["twin_items"], c["twin_slots"], c["not_received"], c["received"]) == (1, 1, 2, 1)
    _legs, early = run(rows, data_date=MON)                    # on the deadline: in grace, still one item
    t = next(o for o in early.open if o.twins)
    assert t.outcome == "in_grace" and t.missing == 1 and early.counts()["in_grace"] == 3
    assert early.counts()["twin_items"] == early.counts()["twin_slots"] == 0


@test("twins: three identical issues and one receipt are 2 of 3; a receipt elsewhere the gate refused is their possible match")
def _():
    rows = (issue(1, SUN, VAN_A, [(CASE, 1)]) + issue(2, SUN, VAN_A, [(CASE, 1)]) + issue(3, MON, VAN_A, [(CASE, 1)])
            + receipt(40, MON, VAN_A, [(CASE, 1)])
            + receipt(90, at(5), VAN_C, [(CASE, 1)]))                # 4 to 5 days on, numbers far apart: refused
    _legs, res = run(rows, data_date=at(9))
    (t,) = res.open
    assert (t.outcome, t.missing, len(t.issues), t.twins) == ("twins", 2, 3, (num("i", 1), num("i", 2), num("i", 3)))
    assert [r.no for r in t.possible] == [num("r", 90)] and t.deadline == MON
    assert [p.issue.no for p in res.pairs] == [num("i", 1)] and res.pairs[0].ambiguous
    assert res.by_issue[num("i", 2)] is res.by_issue[num("i", 3)] is t
    c = res.counts()
    assert (c["twin_items"], c["twin_slots"], c["orphans"]) == (1, 2, 1)


@test("twins: a set is in grace only while every issue of it is, never by the ones step A happened to leave without a receipt")
def _():
    rows = (issue(1, SUN, VAN_A, [(CASE, 2)]) + issue(2, WED, VAN_A, [(CASE, 2)]) + receipt(1, WED, VAN_A, [(CASE, 2)]))
    _legs, res = run(rows, data_date=THU)
    (t,) = res.open
    assert [leg.no for leg in t.issues if not isinstance(res.by_issue[leg.no], pairing.Pair)] == [num("i", 2)]
    assert pairing.within_grace(WED, "van", THU)                  # the one left without a receipt is inside ...
    assert (t.outcome, t.missing, t.deadline) == ("twins", 1, MON)  # ... the other is not: which is missing is not known
    assert res.counts()["in_grace"] == 0 and res.counts()["twin_slots"] == 1


@test("the exchange: a store booked in a transfer meant for another, which issued it again - received elsewhere and received, no twins, no orphan; oldest set first")
def _():
    forwarded = (issue(1, SUN, VAN_A, [(CABLE, 2)]) + receipt(2, SUN, VAN_B, [(CABLE, 2)])          # the wrong van books it
                 + issue(3, MON, VAN_A, [(CABLE, 2)], frm=VAN_B) + receipt(4, MON, VAN_A, [(CABLE, 2)]))   # and sends it on
    _legs, res = run(forwarded, data_date=at(9))
    first, again = got(res, num("i", 1)), got(res, num("i", 3))
    assert (first.outcome, first.step, first.receipt.no, first.received_at) == ("elsewhere", "B", num("r", 2), "van b - acc wh")
    assert (again.outcome, again.step, again.receipt.no) == ("received", "A", num("r", 4))
    assert first.twins == again.twins == (num("i", 1), num("i", 3)) and not first.ambiguous and not again.ambiguous
    assert (res.open, res.orphans) == ((), ())
    # the gate refuses the other store's receipt for either issue (two days and more on, numbers far apart): no
    # exchange, a twin item and an orphan
    refused = [r if r["voucher"] != num("r", 2) else dict(r, voucher=num("r", 40), date=WED) for r in forwarded]
    _legs, res = run(refused, data_date=at(9))
    assert [(o.outcome, o.missing) for o in res.open] == [("twins", 1)] and [o.receipt.no for o in res.orphans] == [num("r", 40)]
    # three identical issues: one exchange, then the set still misses one receipt
    three = forwarded + issue(5, MON, VAN_A, [(CABLE, 2)])
    _legs, res = run(three, data_date=at(9))
    (t,) = res.open
    assert (t.outcome, t.missing, t.twins) == ("twins", 1, (num("i", 1), num("i", 3), num("i", 5)))
    assert got(res, num("i", 1)).outcome == "elsewhere" and got(res, num("i", 3)).receipt.no == num("r", 4)
    assert got(res, num("i", 5)) is t and all(p.ambiguous for p in res.pairs) and res.orphans == ()
    # two short sets want one receipt elsewhere: the oldest set takes it
    rival = (forwarded + issue(5, SUN, VAN_C, [(CABLE, 2)]) + issue(6, MON, VAN_C, [(CABLE, 2)], frm=VAN_B)
             + receipt(7, MON, VAN_C, [(CABLE, 2)]))
    _legs, res = run(rival, data_date=at(9))
    assert got(res, num("i", 1)).outcome == "elsewhere" and got(res, num("i", 5)).outcome == "received"
    assert [(o.twins, o.missing) for o in res.open] == [((num("i", 5), num("i", 6)), 1)] and res.orphans == ()


@test("an issue sent to its own store: the free receipt that books it in there is set aside with it (the nearest number); at another store, or refused by the gate, it stays an orphan")
def _():
    rows = (issue(11, MON, VAN_A, [(CASE, 3)], frm=VAN_A) + receipt(11, MON, VAN_A, [(CASE, 3)])
            + receipt(30, MON, VAN_A, [(CASE, 3)])                           # the same day too, a number further away
            + issue(12, TUE, VAN_B, [(BANK, 2)], frm=VAN_B) + receipt(12, TUE, MAIN, [(BANK, 2)])   # booked elsewhere
            + issue(13, TUE, VAN_C, [(BUDS, 1)], frm=VAN_C) + receipt(60, FRI, VAN_C, [(BUDS, 1)]))  # 3 days on, far apart
    legs, res = run(rows, data_date=SAT)
    assert [(a.voucher, a.kind, a.reason, a.partner) for a in res.aside] == [
        (num("i", 11), "issue", "sent to its own store", num("r", 11)),
        (num("r", 11), "receipt", "its issue was sent to its own store", num("i", 11)),
        (num("i", 12), "issue", "sent to its own store", None),
        (num("i", 13), "issue", "sent to its own store", None)]
    mirror = res.by_receipt[num("r", 11)]
    assert isinstance(mirror, pairing.Aside) and (mirror.day, mirror.wh, mirror.lines) == (MON, "van a - acc wh",
                                                                                          ((CASE, Decimal(3)),))
    assert [o.receipt.no for o in res.orphans] == [num("r", 30), num("r", 12), num("r", 60)]
    assert [a.partner for a in legs.aside] == [None, None, None]              # the legs themselves are not changed
    c = res.counts()
    assert (c["aside"], c["orphans"]) == (4, 3)
    booked = c["received"] + c["late"] + c["elsewhere"] + c["qty_differs"] + c["orphans"] + \
        sum(a.kind == "receipt" for a in res.aside)
    assert booked == len(legs.receipts) == 4
    assert pairing.summarise(res, store=VAN_A, direction="in")["counts"]["aside"] == 2   # the issue, and its receipt there
    assert pairing.summarise(res, store=VAN_A, direction="out")["counts"]["aside"] == 1
    assert pairing.summarise(res, store=VAN_A, direction="in")["counts"]["orphans"] == 1


@test("the damage store's grace is /yq-stock damage's: in grace only before the issue day plus the grace days; parity on the same rows")
def _():
    assert pairing.within_grace(THU, "van", SAT) and not pairing.within_grace(THU, "van", at(7))
    assert pairing.within_grace(SUN, "damage", at(6)) and not pairing.within_grace(SUN, "damage", at(7))
    assert pairing.within_grace(SUN, "damage", datetime(2026, 10, 17, 23, 0))         # a time of day is set aside
    assert pairing.within_grace(SUN, "damage", at(2), pairing.Rules(damage_grace_days=3))
    assert not pairing.within_grace(SUN, "damage", at(3), pairing.Rules(damage_grace_days=3))
    rows = issue(1, SUN, DAMAGE, [(CASE, 1)])
    for d, outcome in ((at(6), "in_grace"), (at(7), "not_received")):
        o = run(rows, data_date=d)[1].open[0]
        assert (o.outcome, o.deadline) == (outcome, at(7)), d
    # the same rows through /yq-stock damage's own rule (scripts.ops.stock_damage: sent up to D-7 and not booked)
    from scripts.ops import stock, stock_damage, stockflow
    d = at(20)
    sent = {"A": 10, "B": 9, "C": 8, "D": 7, "E": 6, "F": 3, "G": 0}                    # days before d
    names = {tag: f"ITEM-{tag} Test {tag} T{tag}" for tag in list(sent) + ["H"]}
    rows = [mv(at(0), "MRN:T-26-10-1", names[tag], MAIN, 5, vtype=ledger.SUPPLIER_RECEIPT) for tag in names]
    for n, (tag, back) in enumerate(sent.items(), start=1):
        rows += issue(n, d - timedelta(days=back), DAMAGE, [(names[tag], 2)])
    rows += issue(20, d - timedelta(days=12), DAMAGE, [(names["H"], 2)]) + receipt(20, d - timedelta(days=11), DAMAGE,
                                                                                  [(names["H"], 2)])
    config.use({"warehouses.main": MAIN, "warehouses.aliases": {}})
    try:
        fl = stockflow.flows(rows, list(names.values()), DAMAGE, [DAMAGE, SAMPLES], d)
    finally:
        config.use(None)
    items_ = [stock.Item(ledger_name=name, name=name.rsplit(" ", 1)[0], code=name.rsplit(" ", 1)[1], cost=Decimal(1))
              for name in names.values()]
    facts = stock_damage.damage_facts(SimpleNamespace(d=d, cfg=SimpleNamespace(damage=DAMAGE), items=items_, facts={}), fl)
    theirs = {x["item"].ledger_name: x["units"] for x in facts["not_booked"]}
    res = run(rows, data_date=d)[1]
    mine = {o.issues[0].lines[0][0]: o.issues[0].lines[0][1] for o in res.open if o.outcome == "not_received"}
    assert mine == theirs == {names[t]: Decimal(2) for t in "ABCD"}, (mine, theirs)
    assert {o.issues[0].lines[0][0] for o in res.open if o.outcome == "in_grace"} == {names[t] for t in "EFG"}
    assert stock_damage.GRACE_DAYS == pairing.Rules().damage_grace_days


@test("the rest: in grace on the deadline, not received after it; up to three possible matches in -7..21 days, by lag then order; orphans before the ledger")
def _():
    first = mv(SUN, "MRN:T-26-10-1", CABLE, MAIN, 50, vtype=ledger.SUPPLIER_RECEIPT)       # the ledger's first date
    rows = [first] + issue(1, at(10), VAN_A, [(BANK, 5)])
    rows += (receipt(80, at(8), VAN_A, [(BANK, 5)]) + receipt(81, at(3), VAN_A, [(BANK, 5)])   # -2 in, -7 in (out of the three)
             + receipt(82, at(14), VAN_B, [(BANK, 5)]) + receipt(83, at(13), VAN_C, [(BANK, 5)])   # +4 and +3, gate refused
             + receipt(84, at(2), VAN_A, [(BANK, 5)]) + receipt(85, at(32), VAN_B, [(BANK, 5)]))   # -8 and +22: outside
    _legs, res = run(rows, data_date=at(11))
    o = got(res, num("i", 1))
    assert (o.outcome, o.deadline, o.missing, o.twins) == ("in_grace", at(11), 1, ())
    assert [r.no for r in o.possible] == [num("r", 81), num("r", 80), num("r", 83)]          # lags -7, -2, +3
    assert run(rows, data_date=at(12))[1].by_issue[num("i", 1)].outcome == "not_received"
    orphans = {x.receipt.no: x for x in res.orphans}
    assert set(orphans) == {num("r", n) for n in range(80, 86)}
    assert [x.before_ledger for x in (orphans[num("r", 84)], orphans[num("r", 81)])] == [True, True]   # days 2 and 3
    assert [i.no for i in orphans[num("r", 82)].possible] == [num("i", 1)] and orphans[num("r", 84)].possible == ()
    edge = [first] + receipt(1, at(20), VAN_A, [(CASE, 1)]) + receipt(2, at(21), VAN_A, [(CASE, 1)])
    _legs, res = run(edge)
    assert [x.before_ledger for x in res.orphans] == [True, False]       # within the first 21 days, then not
    assert run(receipt(1, at(30), VAN_A, [(CASE, 1)]))[1].orphans[0].before_ledger      # the legs' own first day


def busy() -> list[dict]:
    """One small ledger with every outcome (data date: the Saturday)."""
    rows = [mv(SUN, "MRN:T-26-10-1", CABLE, MAIN, 100, vtype=ledger.SUPPLIER_RECEIPT)]
    rows += issue(1, SUN, VAN_A, [(CABLE, 10)]) + receipt(1, SUN, VAN_A, [(CABLE, 10)])                 # received
    rows += issue(2, SUN, VAN_A, [(CHARGER, 4)]) + receipt(40, TUE, VAN_A, [(CHARGER, 4)])             # late
    rows += issue(3, MON, VAN_A, [(BANK, 2)], narr=ZEBRA) + receipt(80, WED, VAN_B, [(BANK, 2)], narr=ZEBRA)   # elsewhere
    rows += issue(4, MON, VAN_B, [(CABLE, 5), (CASE, 2)]) + receipt(4, MON, VAN_B, [(CABLE, 5), (CASE, 1)])   # other quantities
    rows += issue(5, TUE, VAN_A, [(BUDS, 1)]) + issue(6, TUE, VAN_A, [(BUDS, 1)]) + receipt(5, TUE, VAN_A, [(BUDS, 1)])  # twins
    rows += issue(7, WED, DAMAGE, [(CASE, 1)])                                                          # in grace
    rows += issue(8, MON, VAN_B, [(CHARGER, 9)])                                                        # not received
    rows += receipt(9, THU, VAN_B, [(BANK, 7)])                                                         # orphan
    rows += issue(11, WED, VAN_A, [(CASE, 3)], frm=VAN_A)                                               # aside: own store
    rows += [mv(WED, num("i", 12), CABLE, VAN_B, 1, MAIN), mv(THU, num("i", 12), CASE, VAN_B, 1, MAIN)]  # aside: mixed
    rows += [mv(SAT, "SI:SI-T-26-10-1", CABLE, VAN_A, 0, vtype=ledger.SALE, q_in=0, q_out=3)]           # not a transfer
    return rows


@test("counts add up to the legs; by_issue and by_receipt name every leg once; shuffled rows and a second run give an equal Result")
def _():
    rows = busy()
    legs, res = run(rows, data_date=SAT)
    c = res.counts()
    assert c == {"received": 2, "late": 1, "elsewhere": 1, "qty_differs": 1, "in_grace": 1, "not_received": 1,
                 "twin_items": 1, "twin_slots": 1, "orphans": 1, "aside": 2}, c
    assert tuple(c) == pairing.COUNT_KEYS
    issued = sum(c[k] for k in ("received", "late", "elsewhere", "qty_differs", "in_grace", "not_received", "twin_slots"))
    booked = sum(c[k] for k in ("received", "late", "elsewhere", "qty_differs", "orphans"))
    assert (issued, booked) == (len(legs.issues), len(legs.receipts)) == (8, 6)
    assert list(res.by_issue) == [i.no for i in legs.issues] and list(res.by_receipt) == [r.no for r in legs.receipts]
    assert all(isinstance(v, (pairing.Pair, pairing.Open)) for v in res.by_issue.values())
    assert all(isinstance(v, (pairing.Pair, pairing.Orphan)) for v in res.by_receipt.values())
    assert all(res.by_issue[p.issue.no] is p is res.by_receipt[p.receipt.no] for p in res.pairs)
    assert (res.aside, res.out_of_scope, res.data_date, res.rules) == (legs.aside, 0, SAT, pairing.Rules())
    assert {a.reason for a in res.aside} == {"sent to its own store", "mixed: date"}
    for seed in range(6):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        again_legs, again = run(shuffled, data_date=SAT)
        assert again_legs == legs and again == res, seed
        assert again.counts() == c and again.pairs == res.pairs and again.open == res.open
    assert run(rows, data_date=SAT)[1] == res
    assert pairing.pair(legs, data_date=datetime(2026, 10, 17, 18, 0)) == res          # a time of day is set aside


@test("pair refuses what is not its own: other arguments, two legs of one number, a leg filed under the other kind")
def _():
    legs, _res = run(busy(), data_date=SAT)
    raises(TypeError, lambda: pairing.pair(list(legs.issues), data_date=SAT))
    raises(TypeError, lambda: pairing.pair(legs, data_date=SAT, rules={}))
    raises(ValueError, lambda: pairing.pair(legs, data_date="2026-10-17"))
    twice = dataclasses.replace(legs, issues=legs.issues + legs.issues[:1])
    assert "share a number" in str(raises(ValueError, lambda: pairing.pair(twice, data_date=SAT)))
    crossed = dataclasses.replace(legs, issues=legs.issues + legs.receipts[:1])
    assert "not an issue" in str(raises(ValueError, lambda: pairing.pair(crossed, data_date=SAT)))
    empty = pairing.pair(pairing.Legs(issues=(), receipts=()), data_date=SAT)
    assert empty.counts() == dict.fromkeys(pairing.COUNT_KEYS, 0) and empty.by_issue == {} == empty.by_receipt


# ── summarise ─────────────────────────────────────────────────────────────────

@test("summarise: the whole span is counts(); a window of issue dates; a store out, in and either way; vouchers, lines and units by outcome")
def _():
    _legs, res = run(busy(), data_date=SAT)
    whole = pairing.summarise(res)
    assert whole["counts"] == res.counts() and (whole["first"], whole["last"], whole["store"], whole["direction"]) == \
        (None, None, None, "out")
    assert set(whole["outcomes"]) == set(pairing.OUTCOMES)
    assert whole["outcomes"]["qty_differs"] == {"vouchers": 1, "lines": 2, "units": Decimal(7)}
    assert whole["outcomes"]["twins"] == {"vouchers": 1, "lines": 1, "units": Decimal(1)}
    assert whole["outcomes"]["orphan"] == {"vouchers": 1, "lines": 1, "units": Decimal(7)}
    assert whole["issues"] == {"vouchers": 8, "lines": 9, "units": Decimal(35)}
    assert sum(b["vouchers"] for o, b in whole["outcomes"].items() if o != "orphan") == 8
    window = pairing.summarise(res, first=SUN, last=MON)                     # both days included
    assert window["counts"] == {"received": 1, "late": 1, "elsewhere": 1, "qty_differs": 1, "in_grace": 0,
                                "not_received": 1, "twin_items": 0, "twin_slots": 0, "orphans": 0, "aside": 0}
    assert pairing.summarise(res, first=TUE, last=TUE)["counts"] == dict(
        dict.fromkeys(pairing.COUNT_KEYS, 0), received=1, twin_items=1, twin_slots=1)
    to_a = pairing.summarise(res, store="VAN A - ACC WH 10-1", direction="in")      # read through the store rules
    assert to_a["store"] == "van a - acc wh" and to_a["counts"] == dict(
        dict.fromkeys(pairing.COUNT_KEYS, 0), received=2, late=1, elsewhere=1, twin_items=1, twin_slots=1, aside=1)
    from_main = pairing.summarise(res, store=MAIN)
    assert from_main["issues"]["vouchers"] == 8 and from_main["counts"]["orphans"] == 0     # an orphan names no sender
    at_b = pairing.summarise(res, store=VAN_B, direction="in")
    assert (at_b["counts"]["orphans"], at_b["counts"]["qty_differs"], at_b["counts"]["not_received"]) == (1, 1, 1)
    from_b = pairing.summarise(res, store=VAN_B, direction="out")
    assert from_b["issues"]["vouchers"] == 0 and from_b["counts"]["aside"] == 1             # the mixed one
    assert from_b["counts"]["orphans"] == 0 == from_b["outcomes"]["orphan"]["vouchers"]     # booked there, sent by no one known
    either = pairing.summarise(res, store=VAN_B, direction="any")
    assert either["counts"]["orphans"] == 1 and either["counts"]["aside"] == 1 and either["issues"]["vouchers"] == 2
    assert pairing.summarise(res, first=datetime(2026, 10, 12, 9), last=MON)["counts"]["late"] == 0
    assert "direction" in str(raises(ValueError, lambda: pairing.summarise(res, direction="both")))
    raises(ValueError, lambda: pairing.summarise(res, first=MON, last=SUN))
    raises(ValueError, lambda: pairing.summarise(res, store="  "))


# ── the real ledger (read-only; SKIP when it is not on this machine) ──────────

REAL_ROOT = paths.data_root()          # the main checkout above a worktree, or YQ_MAIN_CHECKOUT


@test("real 7-Oct ledger (SKIP without it or golden/pairing.json): the counts, every exception, the exchange's cases, and the one cause left of the differences from the 8-Oct measurement")
def _():
    golden_file = REAL_ROOT / "business_data" / "ops" / "golden" / "pairing.json"
    config_file = REAL_ROOT / "business_data" / "ops" / "config.json"
    if not golden_file.is_file() or not config_file.is_file():
        print("  SKIP: no golden/pairing.json (or no config.json) under the data root")
        return
    golden = json.loads(golden_file.read_text(encoding="utf-8"))
    assert golden.get("version") == 2, "golden/pairing.json was written for the pairing before the review: rebuild it"
    book = ledger.find_file(REAL_ROOT / "business_data" / golden["folder"])
    if book is None:
        print(f"  SKIP: no Stock_ledger file in {golden['folder']} on this machine")
        return
    cfg = json.loads(config_file.read_text(encoding="utf-8"))
    stores = pairing.StoreRules(cfg["warehouses.main"], cfg.get("warehouses.damage") or None,
                                cfg.get("warehouses.not_counted", []), cfg.get("warehouses.aliases", {}))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        paths.set_data_root(tmp)                     # the ledger's cache goes to a temporary folder, never the data root
        try:
            led = ledger.load(book)
        finally:
            paths.set_data_root(None)
    if led.sha != golden["sha256"]:
        print("    golden describes another export of this folder: set aside")
        return
    t0 = time.perf_counter()
    legs = pairing.legs_from_rows(led.rows, stores=stores, scope=None)
    res = pairing.pair(legs, data_date=led.last_date)
    took = time.perf_counter() - t0
    c = res.counts()
    assert c == golden["counts"], c
    assert (len(legs.issues), len(legs.receipts), legs.out_of_scope) == (golden["issues"], golden["receipts"],
                                                                         golden["out_of_scope"])
    assert (res.data_date.isoformat(), legs.first.isoformat()) == (golden["data_date"], golden["first_date"])
    vouchers = (len(legs.issues) + sum(a.kind == "issue" for a in legs.aside),
                len(legs.receipts) + sum(a.kind == "receipt" for a in legs.aside))
    assert vouchers == (golden["issue_vouchers"], golden["receive_vouchers"])
    ledger_golden_file = golden_file.with_name("ledger.json")
    if ledger_golden_file.is_file():                 # the ledger's own golden counts every voucher of the two kinds
        lg = json.loads(ledger_golden_file.read_text(encoding="utf-8"))
        if lg.get("sha256") == led.sha and "issue_vouchers" in lg:
            assert vouchers == (lg["issue_vouchers"], lg["receive_vouchers"]), vouchers
    # every exception, voucher by voucher
    ex = golden["exceptions"]
    for outcome in ("late", "elsewhere", "qty_differs"):
        mine = sorted(([p.issue.no, p.receipt.no] for p in res.pairs if p.outcome == outcome),
                      key=lambda t: pairing.parse_number(t[0]))
        assert mine == ex[outcome], outcome
    assert [{"issues": [i.no for i in o.issues], "missing": o.missing, "outcome": o.outcome,
             "deadline": o.deadline.isoformat()} for o in res.open] == ex["open"]
    assert [{"receipt": o.receipt.no, "before_ledger": o.before_ledger} for o in res.orphans] == ex["orphans"]
    assert [{"voucher": a.voucher, "kind": a.kind, "reason": a.reason, "partner": a.partner}
            for a in res.aside] == ex["aside"]
    rules = res.rules
    booked = c["received"] + c["late"] + c["elsewhere"] + c["qty_differs"] + c["orphans"] + \
        sum(a.kind == "receipt" for a in res.aside)
    assert booked == len(legs.receipts)
    # the one cause left: an issue sent to its own store is set aside, and so is the receipt that books it in there
    own = golden["differences"]["own_store"]["cases"]
    aside = {a.voucher: a for a in res.aside}
    whole: dict = {}
    for r in led.rows:
        if r["voucher"] in {case["issue"] for case in own}:
            whole[(r["voucher"], r["item"])] = whole.get((r["voucher"], r["item"]), Decimal(0)) + r["qty_out"]
    for case in own:
        a, theirs = aside[case["issue"]], res.by_receipt[case["receipt"]]
        assert a.reason == pairing.OWN_STORE and a.wh == a.to, case
        assert a.lines == tuple(sorted((item, units) for (no, item), units in whole.items() if no == a.voucher and units))
        if case["measured_as"] == "A":                 # booked in at its own store: set aside with it, each naming the other
            assert isinstance(theirs, pairing.Aside) and theirs.reason == pairing.MIRRORS_OWN, case
            assert (theirs.partner, a.partner, theirs.wh, theirs.lines) == (a.voucher, theirs.voucher, a.wh, a.lines), case
        else:                                          # booked in by another store: a receipt with no issue left
            assert isinstance(theirs, pairing.Orphan) and a.partner is None, case
            assert theirs.receipt.wh != a.wh and theirs.receipt.lines == a.lines, case
            assert 0 <= (theirs.receipt.day - a.day).days <= rules.lag_max, case
    # reproduced by the exchange: the first issue received elsewhere, its re-issue received where it was sent
    again_cases = golden["differences"]["reproduced_by_the_exchange"]["cases"]
    for case in again_cases:
        first, again = res.by_issue[case["issue"]], res.by_issue[case["reissue"]]
        assert isinstance(first, pairing.Pair) and (first.step, first.receipt.no) == ("B", case["elsewhere"]), case
        assert isinstance(again, pairing.Pair) and (again.step, again.receipt.no) == ("A", case["receipt"]), case
        assert first.twins == again.twins and {first.issue.no, again.issue.no} <= set(first.twins), case
        assert again.receipt.wh == first.issue.to != first.receipt.wh, case
    # the one cause accounts for every difference left from the 8-Oct measurement
    m = golden["measured_8_oct"]
    own_a = sum(case["measured_as"] == "A" for case in own)
    own_b = len(own) - own_a
    assert c["received"] + c["late"] + own_a == m["received_or_late"]
    assert c["elsewhere"] + own_b == m["elsewhere"] and c["qty_differs"] == m["qty_differs"]
    assert c["in_grace"] + c["not_received"] + c["twin_slots"] == m["unmatched"]
    assert c["orphans"] - own_b == m["orphans"] and c["aside"] - len(own) - own_a == m["aside"]
    assert (vouchers[0], vouchers[1]) == (m["issues"], m["receipts"])
    # the whole span read through summarise is counts(); each issue counts once, by the store that sent it
    assert pairing.summarise(res)["counts"] == c
    senders = sorted({i.wh for i in legs.issues})
    assert sum(pairing.summarise(res, store=s)["issues"]["vouchers"] for s in senders) == len(legs.issues)
    assert all((p.received_at == p.issue.to) == (p.step != "B") for p in res.pairs)
    assert took < 10, f"legs and pairing took {took:.1f}s"
    print(f"    golden present ({len(own)} sent to their own store, {len(again_cases)} reproduced by the exchange; "
          f"pairing {took:.1f}s)")


@test("real config (SKIP without it): StoreRules.canon alone, with no aliases, joins every spelling config 'warehouses.aliases' names")
def _():
    config_file = REAL_ROOT / "business_data" / "ops" / "config.json"
    if not config_file.is_file():
        print("  SKIP: no config.json under the data root")
        return
    cfg = json.loads(config_file.read_text(encoding="utf-8"))
    aliases = cfg.get("warehouses.aliases") or {}
    if not aliases or not cfg.get("warehouses.main"):
        print("  SKIP: the config names no warehouse aliases")
        return
    bare = pairing.StoreRules(cfg["warehouses.main"])
    apart = sum(1 for spelt, name in aliases.items() if not bare.canon(spelt) or bare.canon(spelt) != bare.canon(name))
    assert apart == 0, f"{apart} of {len(aliases)} aliases are not joined by canon alone"      # never a name in the line
    with_them = pairing.StoreRules(cfg["warehouses.main"], aliases=aliases)
    assert all(with_them.canon(spelt) == bare.canon(name) for spelt, name in aliases.items())
    print(f"    {len(aliases)} alias(es), every one joined by canon alone")


# ── the module itself ─────────────────────────────────────────────────────────

@test("pairing.py imports only the allowed standard library modules (no scripts.*, nothing relative), opens no file, reads no clock")
def _():
    tree = ast.parse(PAIRING.read_text(encoding="utf-8"), filename=str(PAIRING))
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module, "a relative import"
            seen.add(node.module)
        elif isinstance(node, ast.Call):
            fn = node.func
            assert not (isinstance(fn, ast.Name) and fn.id in ("open", "eval", "exec", "__import__")), fn.id
            assert not (isinstance(fn, ast.Attribute) and fn.attr in ("today", "now", "utcnow", "read_text",
                                                                      "open")), fn.attr
    tops = {name.split(".")[0] for name in seen}
    assert tops <= ALLOWED_IMPORTS, sorted(tops - ALLOWED_IMPORTS)
    assert "scripts" not in tops and seen, seen


# ── the seam the later releases build on ──────────────────────────────────────

NEW_KEYS = {               # key -> (values it takes, values it refuses)
    "watch.floor_bhd": ([0, 12.5], [-1, float("nan"), float("inf"), "5", True]),
    "watch.act_bhd": ([0, 100], [-0.5]),
    "watch.act_days": ([0, 3], [-1, 2.5]),
    "stock.position_stores": ([[], ["vans"], ["transit", "vans", "outlets"]],
                              [["Vans"], ["vans", "vans"], ["damage"], [1], "vans"]),
    "stock.van_floor_days": ([0, 2], [-1, 1.5]),
    "report.bulk_invoice_bhd": ([0, 50], [-1, "50"]),
    "kpi.weekly_due_weekday": (["Sunday", " sunday ", "SUNDAY"], ["Sun", "x", "", 6]),
    "kpi.monthly_due_working_day": ([1, 5], [0, -1, 5.0]),
    "kpi.working_days": ([["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"], ["sunday"]],
                         [[], ["Funday"], ["Sunday", "sunday"], [6], "Sunday"]),
    "kpi.fast_mover_availability_min": ([0, 0.5, 1], [-0.1, 1.01, float("nan")]),
    "kpi.new_skus_band": ([[5, 10], [0, 0], [3, 3]], [[10, 5], [5], [5, 10, 15], [-1, 5], [5.0, 10], [True, 5], "5-10"]),
    "kpi.major_purchase_bhd": ([0, 1000], [-1]),
    "kpi.shop_min_bhd": ([0, 50], [-1]),
    "kpi.supplier_register": (["", "D:/registers/suppliers.xlsx"], [5, None]),
    "kpi.plan_targets": ([{}, {"2026-11": {"sales_bhd": 100}}, {"2026-12": [1]}],
                         [{"2026-1": {}}, {"Nov 2026": {}}, {"2026-13": {}}, [], "2026-11"]),
}


@test("the seam, config: the later releases' keys are optional (never missing) and a value present must hold what its key says")
def _():
    assert set(NEW_KEYS) <= set(config.KEYS) and set(NEW_KEYS) <= config.OPTIONAL
    try:
        config.use({})
        assert not set(config.missing_keys()) & set(NEW_KEYS)                # the selftest stays clean
        for key in NEW_KEYS:
            if key in config.DEFAULTS:                                       # an empty table or text stands in
                assert config.get(key) == config.DEFAULTS[key] and config.DEFAULTS[key] in ({}, ""), key
            else:                                                            # the caller's own default stands in
                exc = raises(config.ConfigMissing, lambda k=key: config.get(k))
                assert f"'{key}'" in str(exc), exc
        assert set(config.DEFAULTS) & set(NEW_KEYS) == {"kpi.plan_targets", "kpi.supplier_register"}
        for key, (good, bad) in NEW_KEYS.items():
            for value in good:
                config.use({key: value})
                assert config.get(key) == value, (key, value)
            for value in bad:
                config.use({key: value})
                exc = raises(config.ConfigMissing, lambda k=key: config.get(k))
                said = str(exc)
                assert said.startswith(f"config: '{key}' must be ") and "\n" not in said, (key, value, said)
        config.use({"watch.act_bhd": 100})
        assert config.number("watch.act_bhd") == Decimal("100")
    finally:
        config.use(None)
    assert [config.weekday(d) for d in ("Monday", " sunday ", "SATURDAY", "friday")] == [0, 6, 5, 4]
    assert all(config.weekday(name) == date(2026, 10, 12 + n).weekday() for n, name in enumerate(config.WEEKDAYS))
    for bad in ("Sun", "", None, 3):
        raises(ValueError, lambda b=bad: config.weekday(b))
    assert config.POSITION_STORES == ("vans", "outlets", "transit")


@test("the seam, paths: the count sheets have their folder under exports/reports, inside the write guard; other kinds stay refused")
def _():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp).resolve()
        paths.set_data_root(root)
        try:
            folder = paths.reports_dir("counts")
            assert folder == root / "exports" / "reports" / "counts" and paths.allowed_output(folder / "a.xlsx")
            assert paths.write_text(folder / "note.txt", "x\n").read_text(encoding="utf-8") == "x\n"
            for kind in ("weekly", "monthly", "stock"):
                assert paths.reports_dir(kind) == root / "exports" / "reports" / kind
            exc = raises(ValueError, lambda: paths.reports_dir("count"))
            assert "'counts'" in str(exc) and "'stock'" in str(exc)
        finally:
            paths.set_data_root(None)


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
