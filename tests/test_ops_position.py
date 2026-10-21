"""Ops engine: the stock beside the main store that the order position counts (scripts.ops.position).

    python -m tests.test_ops_position

Same lightweight runner as the other suites (no pytest). No database, no network, no .env
(SUPABASE_URL / SUPABASE_KEY / DATABASE_URL are pinned to ci.invalid before any import). The module is pure, so
every test hands it rows made here and a pairing.StoreRules; only the check against ledger.balances sets a config
fixture (the ledger reads its aliases from config). All names and numbers are invented.

Covered:
  * a store's stock: received minus issued up to d, every voucher type, its spellings joined through the aliases;
    the vans and the outlets each never below nought (read as nought, named in clamped); the damage store, the
    samples, a SIM store and any other store never; equal to ledger.balances store by store;
  * between stores: an issue still in grace counts until its deadline (a Thursday's until the Saturday), then not;
    to the main store, a van or an outlet only; the missing slots of identical issues; a receipt booked after d
    leaves its issue in grace at d; a voucher that also holds SIM is paired on its accessory lines, as /yq-moves
    pairs it (in grace its accessory units count; booked in, they are the van's); a pairing passed in gives the
    same answer, one as on another day or of another kind is refused;
  * a van's working minimum: ceil(days x its own sales over the 90 days to d / 90), sales before the window and an
    outlet's never; below it the van reads nought;
  * the parts counted, in any order; the refusals; every item asked for answered, in its order, once;
  * stores_beside_main: the units per van and outlet as the position counts them;
  * the same answer for shuffled rows and for rows given as an iterator;
  * position.py imports the standard library and scripts.ops.pairing only (never stock), opens no file, reads no
    clock; its constants are the ledger's, config's and /yq-stock's.
"""
from __future__ import annotations

import ast
import io
import os
import random
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
from scripts.ops import config, ledger, pairing, position  # noqa: E402

TESTS: list[tuple[str, object]] = []
POSITION = ROOT / "scripts" / "ops" / "position.py"
ALLOWED_IMPORTS = {"__future__", "datetime", "decimal"}          # and scripts.ops.pairing, nothing else

MAIN, DAMAGE, SAMPLES = "Main Store", "Damage Store", "Samples Store"
VAN_A, VAN_B, OUTLET, SHOW = "Van A - Acc WH", "Van B - Acc WH", "Causeway", "YQ Roadshow"
SIM_WH, ELSEWHERE = "Van A - SIM WH", "Head Office"
STORES = pairing.StoreRules(MAIN, damage=DAMAGE, not_counted=[DAMAGE, SAMPLES], aliases={"Van A ACC": VAN_A})
CABLE, CHARGER, BANK, SIM = ("ITEM-01 Cable One C01", "ITEM-02 Charger Two CH2", "ITEM-03 Power Bank PB3",
                             "SIM-09 Starter Pack SP9")
ITEMS = (CABLE, CHARGER, BANK)
# One week, Sunday to Saturday (the stores work Saturday to Thursday; Friday is off).
SUN, MON, TUE, WED, THU, FRI, SAT = (date(2026, 10, 11) + timedelta(days=n) for n in range(7))
NIL = Decimal(0)


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


class Rows:
    """Ledger rows shaped as scripts.ops.ledger shapes them, numbered in one series."""

    def __init__(self):
        self.rows: list[dict] = []
        self.n = 0

    def _row(self, item, day, vtype, wh, voucher, qin=0, qout=0, to=None):
        self.rows.append({"item": item, "date": day, "voucher": voucher, "vtype": vtype, "wh": wh, "to_wh": to,
                          "qty_in": Decimal(qin), "qty_out": Decimal(qout), "value_in": Decimal(qin),
                          "value_out": Decimal(qout), "narr": None})
        return self

    def number(self, kind: str) -> str:
        self.n += 1
        return f"{kind}:T-26-10-{self.n}"

    def receipt(self, item, day, units, wh=MAIN):
        return self._row(item, day, ledger.SUPPLIER_RECEIPT, wh, self.number("MRN"), qin=units)

    def move(self, item, day, units, frm, to, booked_on=None, received=True):
        """A transfer: its issue, and its receive leg on booked_on (else the same day) when `received`."""
        n = self.number("SIO")
        self._row(item, day, ledger.ISSUE, frm, n, qout=units, to=to)
        if received:
            self._row(item, booked_on or day, ledger.RECEIVE, to, n.replace("SIO", "STRV"), qin=units)
        return self

    def lines(self, day, frm, to, lines, received=True):
        """One transfer of several lines (one issue voucher, one receive voucher when `received`)."""
        n = self.number("SIO")
        for item, units in lines:
            self._row(item, day, ledger.ISSUE, frm, n, qout=units, to=to)
            if received:
                self._row(item, day, ledger.RECEIVE, to, n.replace("SIO", "STRV"), qin=units)
        return self

    def sale(self, item, day, units, wh):
        return self._row(item, day, ledger.SALE, wh, self.number("SI"), qout=units)


def held(rows, d, **kw):
    return position.position_beside_main(rows, ITEMS, d, store_rules=STORES, **kw)


def row(vans=0, outlets=0, transit=0, counted=None, clamped=()) -> dict:
    """A position_beside_main entry; counted is every part when not given."""
    parts = {"vans": Decimal(vans), "outlets": Decimal(outlets), "transit": Decimal(transit)}
    total = sum(parts.values(), NIL) if counted is None else Decimal(counted)
    return dict(parts, counted=total, clamped=list(clamped))


# ── a store's stock ───────────────────────────────────────────────────────────

def stores_world() -> Rows:
    b = Rows().receipt(CABLE, SUN, 100)
    b.move(CABLE, SUN, 30, MAIN, VAN_A).sale(CABLE, MON, 10, VAN_A)       # the first van: 20
    b.move(CABLE, MON, 1, MAIN, "Van A ACC")                               # its other spelling: 21
    b.move(CABLE, SUN, 5, MAIN, VAN_B).move(CABLE, TUE, 8, VAN_B, MAIN)   # the second van sends back 8 of 5: -3
    b.move(CABLE, SUN, 7, MAIN, OUTLET)                                    # an outlet: 7
    b.move(CABLE, SUN, 2, MAIN, SHOW).sale(CABLE, TUE, 2, SHOW)           # the other outlet: sold out
    for store, units in ((DAMAGE, 4), (SAMPLES, 3), (SIM_WH, 6), (ELSEWHERE, 2)):
        b.move(CABLE, SUN, units, MAIN, store)                             # never counted beside the main store
    b.move(CABLE, THU, 9, MAIN, VAN_A)                                     # after d (WED): not yet
    return b


@test("a store's stock: received minus issued to d, its spellings joined; the vans and the outlets never below "
      "nought; damage, samples, SIM and any other store never")
def _():
    b = stores_world()
    got = held(b.rows, WED)
    assert got[CABLE] == row(vans=21, outlets=7, clamped=["van b - acc wh"]), got[CABLE]
    assert got[CHARGER] == got[BANK] == row()                             # never moved there: nought, nothing named
    assert held(b.rows, THU)[CABLE] == row(vans=30, outlets=7, clamped=["van b - acc wh"])    # the 9 of Thursday
    assert held(b.rows, SUN)[CABLE] == row(vans=35, outlets=9)            # Sunday's: nothing sold or sent back yet
    # store by store, the same as ledger.balances (which reads the aliases from config)
    try:
        config.use({"warehouses.main": MAIN, "warehouses.aliases": {"Van A ACC": VAN_A}})
        bal = ledger.balances(b.rows, as_of=WED)
    finally:
        config.use(None)
    want = {"vans": NIL, "outlets": NIL}
    for (wh, item), units in bal.items():
        kind = STORES.kind(wh)
        if item == CABLE and kind in ("van", "outlet"):
            want["vans" if kind == "van" else "outlets"] += max(units, NIL)
    assert (got[CABLE]["vans"], got[CABLE]["outlets"]) == (want["vans"], want["outlets"]) == (21, 7)
    shops = position.stores_beside_main(b.rows, ITEMS, WED, store_rules=STORES)
    assert shops == {"causeway": {"kind": "outlets", "units": 7}, "van a - acc wh": {"kind": "vans", "units": 21}}


# ── between stores ────────────────────────────────────────────────────────────

@test("between stores: an issue in grace counts until its deadline (a Thursday's until the Saturday), to the main "
      "store, a van or an outlet only")
def _():
    b = Rows()
    b.move(CHARGER, THU, 10, MAIN, VAN_A, received=False)                 # due on the Saturday (Friday is off)
    b.move(BANK, THU, 4, VAN_B, MAIN, received=False)                     # back to the main store: counts too
    b.move(BANK, THU, 3, MAIN, OUTLET, received=False)                    # to an outlet: counts
    for store, units in ((DAMAGE, 2), (SAMPLES, 1), (ELSEWHERE, 5), (SIM_WH, 6)):
        b.move(CABLE, THU, units, MAIN, store, received=False)            # never
    for d, charger, bank in ((THU, 10, 7), (FRI, 10, 7), (SAT, 10, 7), (SAT + timedelta(days=1), 0, 0)):
        got = held(b.rows, d)
        assert (got[CHARGER]["transit"], got[BANK]["transit"], got[CABLE]["transit"]) == (charger, bank, 0), d
    # the van that sent back 4 it never held stands at -4 in the ledger: read as nought, and named
    assert held(b.rows, SAT)[BANK] == row(transit=7, clamped=["van b - acc wh"])


@test("between stores: the missing slots of identical issues count; a receipt booked after d leaves its issue in grace")
def _():
    b = Rows()
    for _ in range(3):
        b.move(CABLE, SAT, 2, MAIN, VAN_A, received=False)                 # three identical issues ...
    b._row(CABLE, SAT, ledger.RECEIVE, VAN_A, "STRV:T-26-10-90", qin=2)   # ... one receipt of the same: 2 slots open
    got = held(b.rows, SAT)[CABLE]
    assert got == row(vans=2, transit=4), got
    c = Rows().move(CHARGER, SAT, 6, MAIN, VAN_B, booked_on=SAT + timedelta(days=1))
    assert held(c.rows, SAT)[CHARGER] == row(transit=6)                   # as on Saturday: booked in on the Sunday
    assert held(c.rows, SAT + timedelta(days=1))[CHARGER] == row(vans=6)


def accessory_legs(rows):
    """The legs as /yq-moves makes them: the accessory lines first, then every voucher on them (scope None)."""
    return pairing.legs_from_rows([r for r in rows if r["item"] in ITEMS], stores=STORES, scope=None)


@test("a voucher that also holds SIM is paired on its accessory lines, as /yq-moves pairs it: in grace its accessory "
      "units count between stores, booked in they are the van's")
def _():
    mixed = Rows().lines(SAT, MAIN, VAN_A, [(BANK, 5), (SIM, 9)], received=False)
    assert held(mixed.rows, SAT)[BANK] == row(transit=5)                  # in grace: the 5 accessories count
    assert held(mixed.rows, SAT + timedelta(days=1))[BANK] == row(transit=5)   # due on the Sunday: still in grace
    assert held(mixed.rows, SAT + timedelta(days=2))[BANK] == row()       # past its deadline: on the action list
    booked = Rows().lines(SAT, MAIN, VAN_A, [(BANK, 5), (SIM, 9)])
    assert held(booked.rows, SAT)[BANK] == row(vans=5)                    # booked in with its SIM line: the van's
    part = Rows().lines(SAT, MAIN, VAN_A, [(BANK, 5), (SIM, 9)], received=False)
    part._row(BANK, SAT, ledger.RECEIVE, VAN_A, "STRV:T-26-10-1", qin=5)  # booked in on the accessory line only
    assert held(part.rows, SAT)[BANK] == row(vans=5)
    # the pairing /yq-moves hands in gives the same answer; it holds the issue on its accessory line alone
    res = pairing.pair(accessory_legs(mixed.rows), data_date=SAT)
    assert held(mixed.rows, SAT, result=res) == held(mixed.rows, SAT)
    assert [o.issues[0].lines for o in res.open] == [((BANK, Decimal(5)),)] and res.aside == ()


@test("a pairing passed in gives the same answer; one as on another day, or not a pairing at all, is refused")
def _():
    b = Rows()
    b.move(CHARGER, THU, 10, MAIN, VAN_A, received=False).move(CABLE, THU, 3, MAIN, VAN_B)
    legs = accessory_legs(b.rows)
    res = pairing.pair(legs, data_date=FRI)
    assert held(b.rows, FRI, result=res) == held(b.rows, FRI)
    exc = raises(ValueError, lambda: held(b.rows, SAT, result=res))
    assert "as on 2026-10-16, not 2026-10-17" in str(exc), str(exc)
    raises(TypeError, lambda: held(b.rows, FRI, result=legs))
    # the plan's rules by default: a two-working-day grace (passed in) keeps Thursday's issue in grace on Sunday
    two = pairing.Rules(grace_working_days=2)
    assert held(b.rows, SAT + timedelta(days=1), rules=two)[CHARGER]["transit"] == 10
    assert held(b.rows, SAT + timedelta(days=1))[CHARGER]["transit"] == 0


# ── a van's working minimum ───────────────────────────────────────────────────

@test("a van's working minimum: ceil(days x its own sales over the 90 days to d / 90); never an outlet's; below it, "
      "nought")
def _():
    d = SAT
    b = Rows().move(CABLE, d - timedelta(days=200), 200, MAIN, VAN_A).move(CABLE, d - timedelta(days=200), 50, MAIN,
                                                                              OUTLET)
    b.sale(CABLE, d - timedelta(days=100), 30, VAN_A)                      # before the window: not counted
    for n in range(45):                                                    # 45 in the window, one every other day
        b.sale(CABLE, d - timedelta(days=2 * n), 1, VAN_A)
    b.sale(CABLE, d, 10, OUTLET)                                           # the outlet sells: still no floor there
    b.move(CABLE, d - timedelta(days=10), 105, VAN_A, MAIN)                # the van holds 200 - 30 - 45 - 105 = 20
    for days, vans, clamped in ((0, 20, []), (1, 19, []), (2, 19, []), (3, 18, []), (40, 0, []),
                                (41, 0, ["van a - acc wh"])):
        got = held(b.rows, d, floor_days=days)[CABLE]
        assert (got["vans"], got["outlets"], got["clamped"]) == (vans, 40, clamped), (days, got)
    assert position.stores_beside_main(b.rows, ITEMS, d, store_rules=STORES, floor_days=40) == {
        "causeway": {"kind": "outlets", "units": 40}}                      # the van at its floor holds nothing


# ── the parts counted, the refusals, the items ────────────────────────────────

@test("the parts counted, in any order; the refusals; every item asked for answered once, in its order")
def _():
    b = stores_world()
    b.move(CHARGER, WED, 10, MAIN, VAN_A, received=False)
    every = held(b.rows, WED)
    for stores, want in (((), 0), (["vans"], 21), (["transit", "vans"], 21), (["outlets", "transit"], 7),
                         (["transit"], 0), (position.POSITION_KINDS, 28)):
        got = held(b.rows, WED, stores=stores)
        assert got[CABLE]["counted"] == want, (stores, got[CABLE])
        assert {k: v for k, v in got[CABLE].items() if k != "counted"} == {
            k: v for k, v in every[CABLE].items() if k != "counted"}         # the parts themselves never change
    assert held(b.rows, WED, stores=["transit"])[CHARGER]["counted"] == 10
    for bad in (["damage"], ["Vans"], ["vans", "boats"]):
        raises(ValueError, lambda bad=bad: held(b.rows, WED, stores=bad))
    raises(TypeError, lambda: held(b.rows, WED, stores="vans"))
    for bad in (-1, 1.5, True, "2"):
        raises(ValueError, lambda bad=bad: held(b.rows, WED, floor_days=bad))
    raises(TypeError, lambda: position.position_beside_main(b.rows, ITEMS, WED, store_rules={"main": MAIN}))
    raises(TypeError, lambda: position.position_beside_main(b.rows, CABLE, WED, store_rules=STORES))
    raises(ValueError, lambda: position.position_beside_main(b.rows, ITEMS, "2026-10-14", store_rules=STORES))
    got = position.position_beside_main(b.rows, [BANK, CABLE, BANK, "ITEM-99 Never Moved X9"], WED, store_rules=STORES)
    assert list(got) == [BANK, CABLE, "ITEM-99 Never Moved X9"] and got["ITEM-99 Never Moved X9"] == row()
    assert all(isinstance(v, Decimal) for r in got.values() for k, v in r.items() if k != "clamped")


@test("the same answer for shuffled rows and for rows given as an iterator")
def _():
    b = stores_world()
    b.move(CHARGER, WED, 10, MAIN, VAN_A, received=False)
    for _ in range(3):
        b.move(BANK, WED, 2, MAIN, VAN_B, received=False)
    b._row(BANK, WED, ledger.RECEIVE, VAN_B, "STRV:T-26-10-95", qin=2)
    want = held(b.rows, WED, floor_days=3)
    rows = list(b.rows)
    for seed in (1, 2, 3):
        random.Random(seed).shuffle(rows)
        assert held(rows, WED, floor_days=3) == want
    assert held(iter(b.rows), WED, floor_days=3) == want
    assert position.stores_beside_main(iter(b.rows), ITEMS, WED, store_rules=STORES) == \
        position.stores_beside_main(b.rows, ITEMS, WED, store_rules=STORES)


# ── the module itself ─────────────────────────────────────────────────────────

@test("position.py imports the standard library and scripts.ops.pairing only (never stock), opens no file, reads no "
      "clock; its constants are the ledger's, config's and /yq-stock's")
def _():
    tree = ast.parse(POSITION.read_text(encoding="utf-8"), filename=str(POSITION))
    seen, scripts = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module, "a relative import"
            if node.module.startswith("scripts"):
                scripts |= {f"{node.module}.{a.name}" for a in node.names}
            else:
                seen.add(node.module)
        elif isinstance(node, ast.Call):
            fn = node.func
            assert not (isinstance(fn, ast.Name) and fn.id in ("open", "eval", "exec", "__import__")), fn.id
            assert not (isinstance(fn, ast.Attribute) and fn.attr in ("today", "now", "utcnow", "read_text",
                                                                      "open")), fn.attr
    assert {name.split(".")[0] for name in seen} <= ALLOWED_IMPORTS, seen
    assert scripts == {"scripts.ops.pairing"}, scripts
    from scripts.ops import stock
    assert position.SALE == ledger.SALE and position.POSITION_KINDS == config.POSITION_STORES
    assert position.FLOOR_WINDOW == stock.RATE_DAYS and stock.position is position
    assert set(position.PART_OF_KIND) <= set(pairing.KINDS) and position.TRANSIT_TO <= set(pairing.KINDS)


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
    config.use(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
