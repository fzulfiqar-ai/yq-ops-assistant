"""Ops engine: the rules and helpers the pairing of transfer vouchers stands on (scripts.ops.pairing), and the
rest of the seam the movement, weekly and KPI releases build on.

    python -m tests.test_ops_pairing

Same lightweight runner as the other suites (no pytest). No database, no network, no .env
(SUPABASE_URL / SUPABASE_KEY / DATABASE_URL are pinned to ci.invalid before any import). Every folder is
a temporary one; all names and numbers are invented.

Covered:
  * the constants: the two legs named as the ledger names them, the damage grace equal to /yq-stock
    damage's own, the plan's defaults in Rules;
  * StoreRules: canon (an alias first, matched folded; case and runs of spaces folded; a trailing code
    dropped) and kind (main, damage, not counted, SIM, van, outlet, other: the first that holds);
  * voucher numbers: the key's total order, the distance only within one series and month;
  * deadline: every day of the week, a holiday, two working days of grace, the damage store's week;
  * dec and narr_key equal to the ledger's own on the same cells, an ID-shaped number included;
  * pairing.py imports nothing but the allowed standard library modules, reads no file and no clock;
  * the seam: the later releases' config keys are optional and checked once present; the count
    sheets' folder.
"""
from __future__ import annotations

import ast
import dataclasses
import io
import os
import random
import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

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
