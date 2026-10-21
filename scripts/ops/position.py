"""The stock beside the main store that the order position counts: the vans, the two outlets and the stock
moving between them (the owner, 21-Oct-2026: all sellable stock). The damage store, the samples, SIM and every
other store config never counts stay out. /yq-stock adds it to the main store's in each item's position; the daily
rate, the days in stock, KPI 1 cover, fast-mover availability and the ageing stay main-store figures.

PURE, as scripts.ops.pairing is: no scripts.ops.stock import (stock imports this), no database, no config read
(the caller passes the stores and the rules in), no clock. The same arguments give the same answer.

CONTRACT (scripts.ops.stock relies on it; scripts.ops.moves hands it on)
  position_beside_main(rows, items, d, *, store_rules, stores=POSITION_KINDS, floor_days=0, rules=None,
                       result=None) -> {ledger item: {"vans", "outlets", "transit", "counted", "clamped"}}
      rows         the stock ledger (scripts.ops.ledger movements); only the rows dated up to d are read
      items        the ledger names of the accessories (scripts.ops.items.accessory_items), every one of them: the
                   transfers are paired on their lines of these items alone (a voucher that also holds SIM is
                   paired on its accessory lines, as /yq-moves and the portal pair it)
      d            the day the position is as on: the ledger's last day, or an earlier one (stock.build's as_of)
      store_rules  pairing.StoreRules: the main store, the damage store, the stores never counted, the aliases
      stores       the parts counted, of POSITION_KINDS ('vans', 'outlets', 'transit'); () counts none
      floor_days   a van's working minimum in days of its own sales (config 'stock.van_floor_days'), 0 or more
      rules        pairing.Rules (None: the plan's); result: pair() at d of these rows' lines of `items`
                   (legs_from_rows with scope None, as /yq-moves makes it) when the caller has it (None: worked
                   out here; one as on another day is refused)
    Every item of `items` has an entry, in their order (Decimal units):
      vans      the vans' stock of it at the end of d, each van never below nought, less its floor
      outlets   the outlets' (StoreRules.kind 'outlet') the same way, with no floor
      transit   the units of the transfers still inside their deadline at d (pairing IN_GRACE: one issue, or
                the slots of identical issues that have no receipt yet) sent to the main store, a van or an
                outlet. A transfer past its deadline is not counted: it is on the action list instead
      counted   the parts `stores` names, summed
      clamped   the vans and outlets read as nought instead of below it (a balance below nought, or below its
                floor), as StoreRules.canon writes them, sorted. A van is named after its salesman: the caller
                never prints these names
  stores_beside_main(rows, items, d, *, store_rules, floor_days=0) -> {store: {"kind": "vans" | "outlets",
                                                                             "units": Decimal}}
      every van and outlet that holds some of `items` at the end of d, as position_beside_main counts it (each
      item never below nought, less the floor), by StoreRules.canon; the units above nought only

HOW A STORE'S STOCK IS READ. A store's balance of an item is received minus issued up to and including d, every
voucher type, as scripts.ops.ledger.balances counts it, the store's spellings joined by StoreRules.canon. The
floor of a van is ceil(floor_days x the van's own Sales Invoice units of the item over the FLOOR_WINDOW days to d
/ FLOOR_WINDOW), never below nought: with floor_days 0 (the owner's choice) there is none. An issue is between
stores from its day until its receipt, and is counted only while pairing says it is in grace.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from scripts.ops import pairing

SALE = "Sales Invoice"                       # the voucher type of a sale (scripts.ops.ledger.SALE)
POSITION_KINDS = ("vans", "outlets", "transit")      # what the position may count (config's own POSITION_STORES)
PART_OF_KIND = {"van": "vans", "outlet": "outlets"}  # a store's kind (pairing.StoreRules.kind) -> its part
TRANSIT_TO = frozenset({"main", "van", "outlet"})    # where a transfer's units count while they are between stores
FLOOR_WINDOW = 90                            # a van's floor rests on its own sales over this many days to d
ZERO = Decimal(0)


def _day(x) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    raise ValueError(f"position: not a date: {type(x).__name__}")


def _parts(stores) -> tuple:
    """The parts named, in POSITION_KINDS' order; ValueError for a word it does not know."""
    if isinstance(stores, str):
        raise TypeError("position: stores is a collection of 'vans', 'outlets' and 'transit', not one word")
    named = set(stores or ())
    unknown = named - set(POSITION_KINDS)
    if unknown:
        raise ValueError(f"position: not a part of the position: {', '.join(sorted(map(str, unknown)))}")
    return tuple(p for p in POSITION_KINDS if p in named)


def _floor_days(floor_days) -> int:
    if isinstance(floor_days, bool) or not isinstance(floor_days, int) or floor_days < 0:
        raise ValueError("position: floor_days is a whole number of days, nought or more")
    return floor_days


def _held(rows, wanted: frozenset, day: date, store_rules: pairing.StoreRules, floor_days: int) -> dict:
    """{(store, item): (part, units counted, read as nought)} for every van and outlet that moved an item of
    `wanted` up to `day`: the balance at the end of day, less the floor (vans), never below nought."""
    kinds: dict[str, tuple] = {}                 # a spelling of the rows -> (canon, its part or None)
    balance: dict[tuple, Decimal] = {}
    sold: dict[tuple, Decimal] = {}
    since = day - timedelta(days=FLOOR_WINDOW - 1)
    for r in rows:
        item = r["item"]
        if item not in wanted:
            continue
        when = _day(r["date"])
        if when > day:
            continue
        spelt = r["wh"]
        known = kinds.get(spelt)
        if known is None:
            known = kinds[spelt] = (store_rules.canon(spelt), PART_OF_KIND.get(store_rules.kind(spelt)))
        store, part = known
        if part is None:
            continue
        key = (store, item)
        balance[key] = balance.get(key, ZERO) + r["qty_in"] - r["qty_out"]
        if floor_days and part == "vans" and r["vtype"] == SALE and when >= since:
            sold[key] = sold.get(key, ZERO) + r["qty_out"] - r["qty_in"]
    parts = {store: part for store, part in kinds.values() if part is not None}
    out: dict[tuple, tuple] = {}
    for key in sorted(balance):
        store, _item = key
        part = parts[store]
        floor = ZERO
        if floor_days and part == "vans":
            floor = (Decimal(floor_days) * max(sold.get(key, ZERO), ZERO) / FLOOR_WINDOW).to_integral_value(
                rounding=ROUND_CEILING)
        left = balance[key] - floor
        out[key] = (part, max(left, ZERO), left < 0)
    return out


def position_beside_main(rows, items, d, *, store_rules, stores=POSITION_KINDS, floor_days=0, rules=None,
                         result=None) -> dict[str, dict]:
    """The stock beside the main store per item, as the order position counts it (see the module docstring)."""
    if not isinstance(store_rules, pairing.StoreRules):
        raise TypeError("position: store_rules is a pairing.StoreRules")
    if isinstance(items, str):
        raise TypeError("position: items is a collection of ledger item names, not one name")
    day = _day(d)
    chosen = _parts(stores)
    floor_days = _floor_days(floor_days)
    names = list(dict.fromkeys(items))
    wanted = frozenset(names)
    rows = rows if isinstance(rows, (list, tuple)) else list(rows)
    out = {name: {"vans": ZERO, "outlets": ZERO, "transit": ZERO, "counted": ZERO, "clamped": []} for name in names}
    for (store, item), (part, units, nought) in _held(rows, wanted, day, store_rules, floor_days).items():
        out[item][part] += units
        if nought:
            out[item]["clamped"].append(store)

    # between stores: what pairing has still in grace at d, sent where it counts. The accessory lines first, then
    # every voucher on them (scope None), as /yq-moves pairs them: a voucher that also holds SIM counts its accessories
    if result is None:
        upto = [r for r in rows if r["item"] in wanted and _day(r["date"]) <= day]
        legs = pairing.legs_from_rows(upto, stores=store_rules, scope=None)
        result = pairing.pair(legs, data_date=day, rules=rules if rules is not None else pairing.Rules())
    elif not isinstance(result, pairing.Result):
        raise TypeError("position: result is what pairing.pair gives")
    elif result.data_date != day:
        raise ValueError(f"position: the pairing is as on {result.data_date.isoformat()}, not {day.isoformat()}")
    for o in result.open:
        if o.outcome != pairing.IN_GRACE or not o.issues or o.issues[0].to_kind not in TRANSIT_TO:
            continue
        for item, units in o.issues[0].lines:          # identical issues: each missing slot holds the same lines
            if item in out:
                out[item]["transit"] += units * o.missing

    for row in out.values():
        row["counted"] = sum((row[p] for p in chosen), ZERO)
        row["clamped"].sort()
    return out


def stores_beside_main(rows, items, d, *, store_rules, floor_days=0) -> dict[str, dict]:
    """{store: {"kind": "vans" | "outlets", "units"}}: every van and outlet holding some of `items` at the end of d,
    as position_beside_main counts it (see the module docstring)."""
    if not isinstance(store_rules, pairing.StoreRules):
        raise TypeError("position: store_rules is a pairing.StoreRules")
    if isinstance(items, str):
        raise TypeError("position: items is a collection of ledger item names, not one name")
    held = _held(rows if isinstance(rows, (list, tuple)) else list(rows), frozenset(items), _day(d), store_rules,
                 _floor_days(floor_days))
    out: dict[str, dict] = {}
    for (store, _item), (part, units, _nought) in held.items():
        if units > 0:
            row = out.setdefault(store, {"kind": part, "units": ZERO})
            row["units"] += units
    return dict(sorted(out.items()))
