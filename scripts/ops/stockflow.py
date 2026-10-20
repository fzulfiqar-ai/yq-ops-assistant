"""Stock beside the main store, read from the stock ledger: the sellable pool, the damage store's in
and out, and who sold an item. READ-ONLY and pure: ledger rows in, figures out (scripts.ops.stock
calls it; scripts.ops.stock_damage, stock_review and stock_plan read what it gives).

THE SELLABLE POOL. Every unit the business can still sell: the main store, the vans, the outlets and
the goods on their way between them. The damage store (config 'warehouses.damage') and the other
stores of config 'warehouses.not_counted' (samples, consignment) are outside it.

A transfer is two vouchers with independent numbers (an issue and its receipt), and pairing them is
/yq-moves' work. The pool needs no pairing: a transfer between two stores of the pool moves nothing in
or out of it, and only the ISSUE leg says where it went (a receipt does not say where it came from).
So the pool is
    every movement that is no transfer, in a store of the pool (supplier receipts, sales, customer
    returns, excesses and shortages)
  - units issued from the pool to a store outside it
  + units issued from a store outside it to the pool,
and a receive leg is never read for it. Goods issued and not yet received stay in the pool, which is
what "goods moving between them" means. An issue that names no destination stays in the pool too, and
is counted (no_destination) so a screen can say so.

THE DAMAGE STORE. Sent = units issued to it, from any store, by the issue date (the day someone found
them damaged), with where they came from: the main store, or elsewhere (a van, an outlet, another
store). Booked = units the damage store itself received, any voucher. Left = units that went out of
it, any voucher (issued back, written off). Its stock on a day = booked - left up to that day.

SELLERS. Sales Invoice units of the last SELLER_DAYS days per store that sold them (a van is named
after its salesman): for the owner's own file only; no screen prints them.

Every figure is per ledger item name; the caller passes the accessories (scripts.ops.items), so SIM
never enters.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from scripts.ops import ledger

SELLER_DAYS = 90
ZERO = Decimal(0)

POOL, MAIN, DAMAGE, OUTSIDE = "pool", "main", "damage", "outside"


@dataclass
class Flow:
    """One item's movements as the stock planning screens read them (all lists oldest first)."""
    pool: list = field(default_factory=list)            # [(day, units in or out of the sellable pool)]
    main: list = field(default_factory=list)            # [(day, units in or out of the main store)]
    sent_damage: list = field(default_factory=list)     # [(issue day, 'main' | 'elsewhere', units)]
    booked_damage: list = field(default_factory=list)   # [(day, units the damage store received)]
    left_damage: list = field(default_factory=list)     # [(day, units that went out of the damage store)]
    sellers: dict = field(default_factory=dict)         # {store: units sold in the SELLER_DAYS days to D}
    _sums: dict = field(default_factory=dict, repr=False)

    def _at(self, name: str, day: date) -> Decimal:
        """Units of one running total at the end of `day`."""
        cache = self._sums.get(name)
        if cache is None:
            moves = getattr(self, name)
            days, totals, run = [], [], ZERO
            for d, units in moves:
                run += units
                if days and days[-1] == d:
                    totals[-1] = run
                else:
                    days.append(d)
                    totals.append(run)
            cache = self._sums[name] = (days, totals)
        days, totals = cache
        i = bisect_right(days, day)
        return totals[i - 1] if i else ZERO

    def sellable_at(self, day: date) -> Decimal:
        """Units in the sellable pool at the end of `day`."""
        return self._at("pool", day)

    def main_at(self, day: date) -> Decimal:
        """Units in the main store at the end of `day` (received minus issued, every voucher)."""
        return self._at("main", day)

    def damage_at(self, day: date) -> Decimal:
        """Units in the damage store at the end of `day`: booked minus left."""
        return self._sum(self.booked_damage, None, day) - self._sum(self.left_damage, None, day)

    def sent_between(self, first: date | None, last: date, source: str | None = None) -> Decimal:
        """Units sent to the damage store with an issue dated `first` to `last` (both included; first None:
        from the start), from one source ('main' | 'elsewhere') or from any."""
        return sum((u for d, src, u in self.sent_damage
                    if (first is None or d >= first) and d <= last and (source is None or src == source)), ZERO)

    def booked_between(self, first: date | None, last: date) -> Decimal:
        return self._sum(self.booked_damage, first, last)

    def left_between(self, first: date | None, last: date) -> Decimal:
        return self._sum(self.left_damage, first, last)

    @staticmethod
    def _sum(moves: list, first: date | None, last: date) -> Decimal:
        return sum((u for d, u in moves if (first is None or d >= first) and d <= last), ZERO)


@dataclass
class Flows:
    """flows() answer: one Flow per item, and what the screens may need to say about the stores."""
    items: dict                                         # {ledger item name: Flow}
    damage_store: str | None                            # its one name, None when config names none
    damage_found: bool                                  # the ledger moved stock in it at least once
    outside: list                                       # the stores outside the pool, by their one name
    no_destination: int = 0                             # issues from the pool that name no destination

    def get(self, name: str) -> Flow:
        return self.items.get(name) or Flow()


def flows(rows, items, damage: str | None, not_counted, d: date) -> Flows:
    """One pass over the ledger rows for the given items (ledger names; any other item is passed over).

    damage       config 'warehouses.damage' (a name as any report spells it); '' or None: no damage store
    not_counted  config 'warehouses.not_counted': the stores outside the pool (the damage store is outside
                 it whether or not the list names it)
    d            the ledger's last day (sellers count the SELLER_DAYS days to it)"""
    wanted = set(items)
    rows = rows if isinstance(rows, (list, tuple)) else list(rows)
    names = ledger.warehouse_names(rows)
    outside_cfg = [w for w in (not_counted or []) if str(w or "").strip()]
    damage = str(damage or "").strip() or None
    kinds: dict[str, str] = {}

    def kind(spelt) -> str:
        k = kinds.get(spelt)
        if k is None:
            if spelt is None or not str(spelt).strip():
                k = POOL
            elif ledger.is_main(spelt):
                k = MAIN
            elif damage and ledger.same_warehouse(spelt, damage):
                k = DAMAGE
            elif any(ledger.same_warehouse(spelt, w) for w in outside_cfg):
                k = OUTSIDE
            else:
                k = POOL
            kinds[spelt] = k
        return k

    first_seller_day = d - timedelta(days=SELLER_DAYS - 1)
    out: dict[str, Flow] = {}
    no_destination = 0
    damage_found = False
    for r in rows:
        name = r["item"]
        if name not in wanted:
            continue
        f = out.get(name)
        if f is None:
            f = out[name] = Flow()
        day, wh, vtype = r["date"], r["wh"], r["vtype"]
        here = kind(wh)
        moved = r["qty_in"] - r["qty_out"]
        if here == MAIN:
            f.main.append((day, moved))
        if here == DAMAGE:
            damage_found = True
            if r["qty_in"] > 0:
                f.booked_damage.append((day, r["qty_in"]))
            if r["qty_out"] > 0:
                f.left_damage.append((day, r["qty_out"]))
        if vtype == ledger.ISSUE:
            to = r.get("to_wh")
            there = kind(to) if to else None
            inside_from = here in (MAIN, POOL)
            if there == DAMAGE and r["qty_out"] > 0:
                f.sent_damage.append((day, "main" if here == MAIN else "elsewhere", r["qty_out"]))
            if inside_from and there in (DAMAGE, OUTSIDE):
                f.pool.append((day, -r["qty_out"]))
            elif not inside_from and there in (MAIN, POOL):
                f.pool.append((day, r["qty_out"]))
            elif inside_from and there is None:
                no_destination += 1
        elif vtype == ledger.RECEIVE:
            pass                                       # the issue leg carries every transfer
        elif here in (MAIN, POOL):
            f.pool.append((day, moved))
        if vtype == ledger.SALE and r["qty_out"] > 0 and first_seller_day <= day <= d:
            store = names.get(wh, wh)
            f.sellers[store] = f.sellers.get(store, ZERO) + r["qty_out"] - r["qty_in"]
    for f in out.values():
        for moves in (f.pool, f.main, f.booked_damage, f.left_damage):
            moves.sort(key=lambda m: m[0])
        f.sent_damage.sort(key=lambda m: m[0])
    damage_name = None
    if damage:
        damage_name = next((names.get(s, s) for s, k in kinds.items() if k == DAMAGE and s), damage)
    outside_names = sorted({names.get(s, s) for s, k in kinds.items() if k in (OUTSIDE, DAMAGE) and s})
    return Flows(items=out, damage_store=damage_name, damage_found=damage_found, outside=outside_names,
                 no_destination=no_destination)
