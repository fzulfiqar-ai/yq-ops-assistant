"""python -m scripts.ops moves [7d | mtd | YYYY-MM | YYYY-MM-DD..YYYY-MM-DD] [rep:<name>]     (the command behind /yq-moves)

    (nothing)   the last 7 days to the stock ledger's last day: every transfer of the window paired by what its two
                vouchers hold; each van's and outlet's stock from its opening to its closing, with its check; the
                main store's line; and what is to check, largest first in BHD at landed cost
    7d | mtd | YYYY-MM | YYYY-MM-DD..YYYY-MM-DD   another window (the brief's other window words read the same way):
                its days to the ledger's last day at the latest (sales.resolve_window, D = the ledger's last day)
    rep:<name>  one van or outlet (a salesman's name, or the outlet's): its row, its transfers of the window with
                what became of each and on what basis, and its items to check
    'again' and 'top:' are the brief's words: refused here in one sentence.

THE BASIS. Accessories only: an item that has ever moved in the main store (config 'warehouses.main'), less config
'items.not_accessories' (scripts.ops.items.accessory_items through the stock bridge of /yq-stock); SIM packs never
move there, so SIM is out by that rule, never by a word in a name. An accessory the bridge cannot place (no code in
the price book, no name in the stock balance) stays in every figure and is listed in its own block (UNMAPPED). The
stock ledger is read through scripts.ops.ledger.for_audit: the loaded folder's file when it is proven equal to what
production holds, else production's own table. The figures of each store are printed only when the ledger came from
the database or the file passed that comparison (source["per_store_ok"]); otherwise one line says why, and the
transfers and the items to check still stand.

THE PAIRING is scripts.ops.pairing's, one rule for /yq-moves and the portal: the ledger's rows are cut to the
accessory lines FIRST, then legs_from_rows(those rows, scope=None) - as the portal pairs the accessory lines of its
view - so a transfer voucher that also holds other items (SIM) is paired on its accessory lines, never set aside as
partly out of scope; then pair() with the data date = the ledger's last day. The stores are read through StoreRules
built from config 'warehouses.main', 'damage', 'not_counted' and 'aliases': a van is a store whose name ends
" - Acc WH", an outlet is Causeway or YQ Roadshow (StoreRules.kind). Pairs are made by contents, never by number.
An issue booked in under two receipts (pairing step S: Pair.also) is one pair: both receipts are received at its
store, the store's list names both on the issue's line, and the second is never a receipt with no issue.

EACH STORE (each van and outlet, and the main store), in units and in BHD, for the window:
  opening           balances(as_of = the day before the window), received minus issued, every voucher
  received          the store's own receive legs (whatever issue they belong to, or none)
  issued_to         units OTHER stores issued to it (issue legs whose destination is the store; "Sent to it" on the
                    screen, beside received where the two differ; no part of the check)
  back_to_main / to_damage / to_other   its issues, by the kind of store that ACTUALLY booked them in (the pairing:
                    a transfer received elsewhere counts where it was received, not where it was sent)
  not_received_yet  its issues with no receipt yet, inside their deadline
  not_received      its issues with no receipt after the deadline, the identical issues left without one, and the
                    issues the pairing set aside (they cannot be paired) - but an issue sent to its own store whose
                    receipt booked it back in there (its mirror, set aside with it: pairing.MIRRORS_OWN) leaves by
                    the store's own kind, as any issue booked back in at its source; its mirror is received
                    there, so the two move nothing. Set-aside vouchers are never items to check.
  sold              Sales Invoice units out less in; customer_returns: Sales Return units in less out
  counts            Excesses in Stocks less Shortages in Stock; other: any other voucher type (a supplier receipt
                    in a van, ...)
  closing           balances(as_of = the window's last day)
  check             closing - (opening + received + customer_returns + counts + other - every issue out - sold).
                    Nought by construction, in units and in BHD: anything else means a movement fell in no column or
                    in two, a fault of this program, and the screen prints it "to check".
  Shares (units): sold / (opening + received); back to main / (opening + received). No figure is named for selling
  through. Behaviour, counts per store and never an item to check: transfer vouchers dated on a Friday (the
  stores' day off: pairing.Rules().weekend); back-dated numbers (a voucher dated before one its series numbered
  earlier, every item of the ledger reading the series); round trips (goods sent to the store and sent back,
  the same lines, within BEHAVIOUR_DAYS) and re-issues (sent back and sent again); month-end returns (issues back
  to the main store in the last MONTH_END_DAYS days of a month).

AT LANDED COST = /yq-stock's basis through its seam: stock.cost_reads() (the price book, the snapshot, the MRN
landed costs, the pricing sheet), stock.accessory_table() and stock.apply_costs() - the code's MRN landed cost,
else the pricing sheet, else the latest main-store receipt rate - each item's cost held to six decimals so that
every BHD figure is exact and each store's check is exactly nought (the screen rounds to the fils). Units of an
item with no cost are counted apart in `uncosted`, never valued at nought; the words of every figure at cost come
from stock.cost_words(stock.basis_counts(...)).

TO CHECK: exceptions(audit), the single builder (/yq-watch records from it). One exception per issue (per receipt
for the last kind), dated by its issue (the receipt), in the audit's window:
  not_received        an issue with no receipt after its deadline (the first working day after it)
  damage_not_booked   an issue to the damage store not booked in there within its 7 days
  twins               identical issues to one store, fewer receipts: one item, "x of N", every number named and
                      never which one (missing = x, of = N; `open` repeats missing for the action list)
  qty_differs         received where it was sent, with other quantities: the lines that differ
  elsewhere           the same contents booked in by another store
  orphan              a receipt no issue was found for (before_ledger: its issue may be older than the ledger)
  {kind, issues, receipt, from, to, received_at, date, deadline, lag, lines [{item, units}], units, bhd, uncosted,
   basis, possible}: BHD = the lines x cost (qty_differs: the units that differ; twins: missing x one issue's
  lines); sorted by BHD, largest first, then by date and number; an exception with no cost at all comes last.

THE API (other commands build on it; nothing here writes but run()'s copies of the screen and the hidden golden):
  audit(window=None, *, rep=None, prefer="auto") -> Audit   window None: the whole ledger (every exception of it)
  period_table(first, last)       the per-store table of a period (the shared Movements sheet)
  store_balances(as_of, kinds)    {store: {item: units}} at the end of a day
  books_only(as_of, held_days)    [{store, units, bhd, uncosted, held_over}]: the book side of an uncounted store
  brief_facts(days=7)             the brief's MOVEMENTS line; exception_items(first, last): the items on an exception
  exceptions(audit)               the single builder of the items to check
Audits are memoised in the process per (ledger sha, or 'db:' + its last day; the window; the store): the pairing,
the costs and the indexes are worked out once per ledger.

The hidden option --golden-build (never in the skill) writes business_data/ops/golden/pairs_accessories.json (every
exception, every pair booked in under two receipts, and the first GOLDEN_CLEAN clean pairs by the sha256 of the
issue number, each leg with its lines) and a review text under exports/ops/<D>/ (its SAMPLE: GOLDEN_PER_KIND of
each kind of exception and of the split pairs, then clean pairs, GOLDEN_SAMPLE in all), both local and
git-ignored, from the ledger --ledger names (default: the folder golden/pairing.json names, else the loaded
folder).

Every owner-facing line passes the wording guard (fmt.careful): "to check", "differs", "not received yet", never
an accusation. Screens and facts go to exports/ops/<D>/ (sales.save_screen); store and item names appear there and on
the screen, nowhere else. The counts in this file are the plan's (7 days, 12 items shown, 7 and 3 days of
behaviour, 50 clean pairs); no threshold of the owner's lives here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import textwrap
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from scripts.ops import db  # noqa: F401  (first: it blanks the side channels before any app import)
from scripts.ops import config, fmt, ledger, pairing, paths, sales, stock

from app import metrics  # noqa: E402

try:                    # the order position (another package's module): re-exported here once it is there
    from scripts.ops.position import position_beside_main  # noqa: F401
except ModuleNotFoundError as _missing:
    if _missing.name != "scripts.ops.position":
        raise

COMMAND = "moves"
DEFAULT_WINDOW = "7d"
TOP_SHOWN = 12                 # TO CHECK: the largest items on the screen; the rest are counted (/yq-watch lists all)
STORE_MOVES_SHOWN = 40         # rep: the store's transfers on the screen (the facts file keeps every one)
ASIDE_SHOWN = 10
UNMAPPED_SHOWN = 8
BEHAVIOUR_DAYS = 7             # a round trip or a re-issue: the second voucher within this many days of the first
MONTH_END_DAYS = 3             # a month-end return: an issue back to the main store in the last three days of a month
COST_DP = Decimal("0.000001")  # a unit's cost held to six decimals: every BHD figure exact, every check nought
GOLDEN_CLEAN = 50              # the golden list: the clean pairs kept beside every exception
GOLDEN_SAMPLE = 20             # the review text: the cases read with the owner
GOLDEN_PER_KIND = 2            # ... at most this many of each kind of exception among them
NAME_WIDTH = 30
WRAP = 118                     # a line of words is wrapped at this width (the tables are as wide as their figures)
ZERO = Decimal(0)

# A store's row (StoreRow), in this order; the Movements sheet of the workbooks reads the same keys.
MOVE_KEYS = ("opening", "received", "issued_to", "back_to_main", "to_damage", "to_other", "not_received_yet",
             "not_received", "sold", "customer_returns", "counts", "other", "closing", "check")
CHECK_PLUS = ("opening", "received", "customer_returns", "counts", "other")
CHECK_MINUS = ("back_to_main", "to_damage", "to_other", "not_received_yet", "not_received", "sold")
BEHAVIOUR_KEYS = ("friday", "backdated", "round_trips", "reissued", "month_end_back")
TABLE_KINDS = ("van", "outlet")
EXCEPTION_KINDS = ("not_received", "damage_not_booked", "twins", "qty_differs", "elsewhere", "orphan")
WAITING = ("not_received", "damage_not_booked", "twins")       # the kinds that wait for a receipt

PAIRED_BY = ("Pairs are made by what the two vouchers hold (items, units, the store, the days between them), never by "
             "the voucher number.")
PORTAL_LINE = "The portal's Stock Movement page still pairs by voucher number: ignore its Pending and van Gap figures."
NOT_MEASURED = "Not measured: the time from a Stock Request to its issue (Focus exports no request vouchers)."
REFUSED_WORDS = ("/yq-moves takes a window and rep:<name> only: 'again' and 'top:' are words of /yq-brief.")
BASIS_WORDS = {"narration": "the same narration", "number": "numbers close", "date": "booked within a day"}
BASIS_SHORT = {"narration": "narration", "number": "number", "date": "day"}
ROLE_WORDS = {"main": "main store", "damage": "damage store"}
_DAYS = re.compile(r"^(\d{1,3})d$")
_VAN_TAIL = re.compile(r"\s*-\s*acc\s+wh\s*$", re.I)


class MovesInput(ValueError):
    """Something the command cannot answer: one plain sentence, printed as it is."""


# ── small helpers ─────────────────────────────────────────────────────────────

def _plural(n, one: str, many: str | None = None) -> str:
    return f"{fmt.units(n)} {one if n == 1 else (many or one + 's')}"


def _indent(block: str, by: str = "  ") -> list[str]:
    return [by + ln for ln in block.splitlines()]


def _para(text: str, first: str = "", rest: str | None = None, width: int = WRAP) -> list[str]:
    """A line of words wrapped at `width`: `first` before its first line, `rest` (two spaces more than `first` by
    default) before the others. Words are never broken."""
    rest = (first + "  ") if rest is None else rest
    return textwrap.wrap(str(text), width=width, initial_indent=first, subsequent_indent=rest,
                         break_long_words=False, break_on_hyphens=False) or [first.rstrip()]


def _guarded(line: str) -> str:
    """Every line shown passes the wording guard (fmt.careful); a word from the data that the guard refuses is
    blanked, never printed (stock's and the brief's own rule)."""
    try:
        return fmt.careful(line)
    except ValueError:
        out = []
        for part in re.split(r"(\s+)", line):
            try:
                out.append(fmt.careful(part))
            except ValueError:
                out.append("-" * len(part))
        return "".join(out)


def _day(x, ref: date | None = None) -> str:
    """'Tue 06 Oct', with the year when it is not the year of `ref`."""
    d = fmt.as_date(x)
    if d is None:
        return "n/a"
    return fmt.day(d) + (f" {d.year}" if ref is not None and d.year != ref.year else "")


def short_store(name) -> str:
    """A store as the screen names it: a van by its salesman (" - Acc WH" left off), any other store as it is."""
    text = " ".join(str(name or "").split())
    return _VAN_TAIL.sub("", text) or text


def store_word(name, stores: pairing.StoreRules | None = None) -> str:
    """A store in a sentence: the main store and the damage store by their role, a van by its salesman, any other
    store by its name."""
    if name is None:
        return ""
    if stores is not None:
        role = ROLE_WORDS.get(stores.kind(name))
        if role:
            return role
    return short_store(name)


def _basis_short(basis) -> str:
    return ", ".join(BASIS_SHORT[b] for b in basis) or "contents"


def _short(text, width: int = NAME_WIDTH) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= width else t[: width - 3].rstrip() + "..."


def _cost6(cost) -> Decimal | None:
    return None if cost is None else Decimal(cost).quantize(COST_DP, rounding=ROUND_HALF_UP)


def _units_value(x: Decimal):
    """Units as a plain value: a whole number as an int, else the Decimal as text."""
    x = Decimal(x)
    return int(x) if x == x.to_integral_value() else str(x)


def _money(x) -> str | None:
    return None if x is None else str(fmt.q3(x))


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, date):
        return x.isoformat()
    return x


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower().replace("..", "-to-")).strip("-")


def _date_arg(x, what: str) -> date:
    d = fmt.as_date(x)
    if d is None:
        raise MovesInput(f"The {what} is written YYYY-MM-DD: {x!r} is not one.")
    return d


def _month_end(d: date) -> date:
    first_next = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def _empty() -> dict:
    return dict.fromkeys(MOVE_KEYS, ZERO)


# ── a store's row ─────────────────────────────────────────────────────────────

@dataclass
class StoreRow:
    """One store's figures over a window (see EACH STORE at the top). units and bhd hold every key of MOVE_KEYS;
    bhd is exact (units x the cost held to six decimals); uncosted holds the units of items with no cost, which are
    in `units` and in no BHD figure."""
    store: str                                         # its name as the ledger spells it
    key: str                                           # its one name as the pairing compares it (StoreRules.canon)
    kind: str                                          # StoreRules.kind: 'van', 'outlet', 'main', ...
    units: dict = field(default_factory=_empty)
    bhd: dict = field(default_factory=_empty)
    uncosted: dict = field(default_factory=_empty)
    behaviour: dict = field(default_factory=lambda: dict.fromkeys(BEHAVIOUR_KEYS, 0))
    items: frozenset = frozenset()                     # the items held or moved there in the window

    @property
    def uncosted_units(self) -> Decimal:
        """Units of items with no cost that the store started the window with or took in during it (by the check,
        what it closed with, sold or sent on): counted in units, never valued."""
        return max(sum((self.uncosted[k] for k in CHECK_PLUS), ZERO), ZERO)

    @property
    def balanced(self) -> bool:
        return not (self.units["check"] or self.bhd["check"] or self.uncosted["check"])

    @property
    def active(self) -> bool:
        return any(self.units[k] for k in MOVE_KEYS) or any(self.behaviour.values())

    def shares(self) -> dict:
        """{sold, back}: units sold, and units sent back to the main store, over opening + received (None when that
        is nought or less)."""
        base = self.units["opening"] + self.units["received"]
        if base <= 0:
            return {"sold": None, "back": None}
        return {"sold": self.units["sold"] / base, "back": self.units["back_to_main"] / base}

    def as_dict(self, exact: bool = True) -> dict:
        """The row as plain figures: exact BHD (the Movements sheet balances on them), or to the fils (facts)."""
        bhd = dict(self.bhd) if exact else {k: fmt.q3(v) for k, v in self.bhd.items()}
        shares = self.shares()
        return {"store": self.store, "kind": self.kind, "units": dict(self.units), "bhd": bhd,
                "uncosted": dict(self.uncosted), "uncosted_units": self.uncosted_units,
                "shares": shares if exact else {k: (None if v is None else v.quantize(Decimal("0.0001"),
                                                                                       rounding=ROUND_HALF_UP))
                                                for k, v in shares.items()},
                "behaviour": dict(self.behaviour), "items": len(self.items), "balanced": self.balanced}


def _total(rows: list[StoreRow], name: str) -> StoreRow:
    out = StoreRow(store=name, key="", kind="total")
    held: set = set()
    for r in rows:
        for block in ("units", "bhd", "uncosted"):
            mine, theirs = getattr(out, block), getattr(r, block)
            for k in MOVE_KEYS:
                mine[k] += theirs[k]
        for k in BEHAVIOUR_KEYS:
            out.behaviour[k] += r.behaviour[k]
        held |= r.items
    out.items = frozenset(held)
    return out


# ── the ledger and what is worked out once per ledger ─────────────────────────

@dataclass
class _Base:
    key: str                       # the ledger file's sha256, or 'db:<its last day>'
    led: ledger.Ledger
    rows: list                     # the whole ledger, every item
    acc_rows: list                 # its accessory lines: what the pairing and every figure here read
    d: date
    first: date
    stores: pairing.StoreRules
    rules: pairing.Rules
    scope: frozenset               # the accessories (ledger item names)
    table: dict                    # {ledger item name: stock.Item} (codes, costs)
    costs: dict                    # {ledger item name: Decimal held to six decimals, or None}
    unread: list                   # the cost reads that failed, in /yq-stock's words
    bridge_read: bool              # the stock bridge had names to go on (the snapshot or the price book was read)
    unmapped: list                 # accessories the bridge cannot place
    legs: pairing.Legs
    result: pairing.Result
    names: dict                    # canon -> the store's name as the ledger spells it
    bucket: dict                   # issue number -> the column its units leave the store by
    backdated: frozenset           # transfer voucher numbers dated before one their series numbered earlier
    _canon: dict = field(default_factory=dict, repr=False)

    def canon_of(self, spelt) -> str:
        got = self._canon.get(spelt)
        if got is None:
            got = self._canon[spelt] = self.stores.canon(spelt)
        return got

    def name_of(self, canon) -> str | None:
        if canon is None:
            return None
        return self.names.get(canon, canon)

    def value(self, lines) -> tuple[Decimal | None, Decimal]:
        """(units x cost over the lines that have one, None when none has; the units with no cost)."""
        total, priced, uncosted = ZERO, False, ZERO
        for item, units in lines:
            c = self.costs.get(item)
            if c is None:
                uncosted += units
            else:
                total += units * c
                priced = True
        return (total if priced else None), uncosted


_BASES: dict[str, _Base] = {}
_AUDITS: dict[tuple, "Audit"] = {}


def forget() -> None:
    """Drop every memoised ledger and audit (tests; a process that knows the data changed)."""
    _BASES.clear()
    _AUDITS.clear()


def store_rules() -> pairing.StoreRules:
    """The stores as config names them, read the pairing's way."""
    return pairing.StoreRules(main=config.get("warehouses.main"), damage=config.get("warehouses.damage") or None,
                              not_counted=tuple(config.get("warehouses.not_counted")),
                              aliases=config.get("warehouses.aliases"))


def _read(prefer) -> tuple[ledger.Ledger, dict]:
    """The ledger and its source: {source, note, per_store_ok, why, origin, file, cache_note}. prefer: 'auto',
    'file' or 'db' (ledger.for_audit), or a Stock_ledger workbook or its folder, read as it is (not compared)."""
    choice = str(prefer or "auto")
    try:
        if choice in ledger.PREFER:
            led, info = ledger.for_audit(choice)
        else:
            led = ledger.load(Path(choice))
            info = {"source": "file", "parity": None,
                    "note": f"The stock ledger is read from the file {led.source}, as asked: it was not compared "
                            "with the portal."}
    except ledger.NoLedgerFile as exc:
        raise MovesInput(f"No stock ledger to read: {exc}.") from None
    parity = info.get("parity")
    ok = info["source"] == "database" or bool(isinstance(parity, dict) and parity.get("ok"))
    why = None if ok else (f"the stock ledger file {led.source} was read as it is and not compared with the portal, "
                           "so its figures per store are not proven to be what production holds")
    return led, {"source": info["source"], "note": info["note"], "per_store_ok": ok, "why": why,
                 "origin": led.origin, "file": led.source, "cache_note": led.cache_note}


def _backdated(rows) -> frozenset:
    """Every transfer voucher of the ledger (every item: the series is one numbering) dated before a voucher of the
    same kind, series, year and month that carries a lower number."""
    heads: dict[str, tuple] = {}
    for r in rows:
        if r["vtype"] in (ledger.ISSUE, ledger.RECEIVE):
            seen = heads.get(r["voucher"])
            if seen is None or r["date"] < seen[1]:
                heads[r["voucher"]] = (r["vtype"], r["date"])
    groups: dict[tuple, list] = {}
    for number, (vtype, day) in heads.items():
        k = pairing.parse_number(number)
        if k[0] == 0:
            groups.setdefault((vtype, k[1], k[2], k[3]), []).append((k[4], number, day))
    out = set()
    for group in groups.values():
        group.sort()
        latest = None
        for _n, number, day in group:
            if latest is not None and day < latest:
                out.add(number)
            if latest is None or day > latest:
                latest = day
    return frozenset(out)


def _base_for(led: ledger.Ledger) -> _Base:
    """What is worked out once per ledger: the stores, the accessories with their costs, the pairing, the indexes."""
    d, start = led.last_date, led.first_date
    if d is None:
        raise MovesInput("No stock ledger is loaded: there is no movement to audit.")
    key = led.sha or f"db:{d.isoformat()}"
    got = _BASES.get(key)
    if got is not None:
        return got
    rows = led.rows
    stores = store_rules()
    unread: list[str] = []
    try:
        reads = stock.cost_reads()
    except Exception as exc:  # noqa: BLE001  (no costs is a part of the screen, never the screen)
        reads = {"price_book": None, "snapshot": [], "mrn": None, "sheet": None,
                 "unread": [f"the stock balance snapshot (the item bridge and the costs) ({type(exc).__name__})"]}
    unread += list(reads.get("unread") or [])
    try:
        table, bridge, _main_snap = stock.accessory_table(rows, reads.get("price_book"), reads.get("snapshot") or [])
    except ValueError as exc:
        raise MovesInput(str(exc)) from None
    stock.apply_costs(table, reads.get("mrn"), reads.get("sheet"))
    costs = {name: _cost6(it.cost) for name, it in table.items()}
    scope = frozenset(table)
    bridge_read = bool(reads.get("snapshot")) or bool(reads.get("price_book"))
    unmapped = sorted(n for n, it in table.items() if it.code is None and bridge.get(n) is None) if bridge_read else []
    # the accessory lines first, then every voucher on them (scope None): a voucher that also holds SIM is paired on its
    # accessory lines, as the portal pairs the accessory lines of its view
    acc_rows = [r for r in rows if r["item"] in scope]
    legs = pairing.legs_from_rows(acc_rows, stores=stores, scope=None)
    result = pairing.pair(legs, data_date=d)
    names: dict[str, str] = {}
    for display in sorted(set(ledger.warehouse_names(rows).values())):
        names.setdefault(stores.canon(display), display)
    bucket: dict[str, str] = {}
    for number, got_ in result.by_issue.items():
        if isinstance(got_, pairing.Pair):
            kind = stores.kind(got_.received_at)
            bucket[number] = "back_to_main" if kind == "main" else "to_damage" if kind == "damage" else "to_other"
        else:
            bucket[number] = "not_received_yet" if got_.outcome == pairing.IN_GRACE else "not_received"
    for a in result.aside:
        if a.kind != "issue":
            continue
        if a.partner:                  # sent to its own store and booked back in there (its mirror receipt, set aside
            kind = stores.kind(a.wh)   # with it): it leaves like any issue booked back in at its source
            bucket.setdefault(a.voucher, "back_to_main" if kind == "main" else "to_damage" if kind == "damage"
                              else "to_other")
        else:
            bucket.setdefault(a.voucher, "not_received")
    base = _Base(key=key, led=led, rows=rows, acc_rows=acc_rows, d=d, first=start, stores=stores, rules=result.rules,
                 scope=scope, table=table, costs=costs, unread=unread, bridge_read=bridge_read, unmapped=unmapped,
                 legs=legs, result=result, names=names, bucket=bucket, backdated=_backdated(rows))
    forget()                       # one ledger at a time: a new one leaves nothing of the old one behind
    _BASES[key] = base
    return base


# ── the window and the store asked for ────────────────────────────────────────

def _moves_words(text: str) -> str:
    return text.replace("No sales are loaded", "No stock movement is loaded")


def resolve(window, d: date, start: date) -> dict:
    """{word, title, first, last, notes} of a window on a ledger that runs from `start` to `d`. None: the whole
    ledger. A word: '<n>d' (the n days to d), else sales.resolve_window's words with D = d. A pair of days (dates or
    'YYYY-MM-DD') is the range between them. A window that starts before the ledger starts there, and says so."""
    notes: list[str] = []
    if window is None:
        return {"word": None, "title": "The whole ledger", "first": start, "last": d, "notes": notes}
    if isinstance(window, (tuple, list)):
        if len(window) != 2:
            raise MovesInput("A window is two days: the first and the last, written YYYY-MM-DD.")
        a, b = (_date_arg(x, "window's day") for x in window)
        word = f"{a.isoformat()}..{b.isoformat()}"
    else:
        word = " ".join(str(window).split()).lower()
    m = _DAYS.match(word)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise MovesInput("A window of days is one day or more, for example 7d.")
        first, last, title = d - timedelta(days=n - 1), d, ("Latest day" if n == 1 else f"Last {n} days")
    else:
        try:
            win, title, said = sales.resolve_window(word, d, d, d)
        except sales.BriefInput as exc:
            raise MovesInput(_moves_words(str(exc))) from None
        first, last = win.start, win.end
        notes += [_moves_words(x) for x in said]
    if last < start:
        raise MovesInput(f"The stock ledger starts on {fmt.day_long(start)}: the window ends before it, so it holds "
                         "no movement.")
    if first < start:
        notes.append(f"The stock ledger starts on {_day(start, d)}: the window starts there.")
        first = start
    return {"word": word, "title": title, "first": first, "last": last, "notes": notes}


def _rep_words(text: str) -> str:
    return (text.replace("No salesman fits", "No van or outlet fits")
            .replace("Names in the sales data", "Vans and outlets in the stock ledger")
            .replace("More than one salesman fits", "More than one van or outlet fits"))


def resolve_store(typed: str, base: _Base) -> str:
    """The van or outlet a typed name means (sales.resolve_rep over the vans' and outlets' names: a salesman's name,
    its start, any case): its one name (canon). MovesInput, one sentence, when none or several fit."""
    pool = {c: n for c, n in base.names.items() if base.stores.kind(c) in TABLE_KINDS}
    try:
        got = sales.resolve_rep(typed, sorted(pool.values()))
    except sales.BriefInput as exc:
        raise MovesInput(_rep_words(str(exc))) from None
    for canon, display in sorted(pool.items()):
        if display == got or metrics.rep_base(display) == got:
            return canon
    raise MovesInput(f"No van or outlet fits '{typed}'.")


# ── the figures of a window ───────────────────────────────────────────────────

def _canon_balances(base: _Base, as_of: date) -> dict[str, dict[str, Decimal]]:
    """{store (canon): {item: units}} of the accessories at the end of `as_of`: ledger.balances, read per store."""
    out: dict[str, dict[str, Decimal]] = {}
    for (wh, item), units in ledger.balances(base.acc_rows, as_of=as_of).items():
        if not units or item not in base.scope:
            continue
        slot = out.setdefault(base.canon_of(wh), {})
        slot[item] = slot.get(item, ZERO) + units
    return out


def _row(base: _Base, store: str, buckets: dict) -> StoreRow:
    units, bhd, unc = _empty(), _empty(), _empty()
    held: set = set()
    for bucket, by_item in buckets.items():
        for item, u in by_item.items():
            units[bucket] += u
            c = base.costs.get(item)
            if c is None:
                unc[bucket] += u
            else:
                bhd[bucket] += u * c
            held.add(item)
    for block in (units, bhd, unc):
        block["check"] = block["closing"] - (sum((block[k] for k in CHECK_PLUS), ZERO)
                                             - sum((block[k] for k in CHECK_MINUS), ZERO))
    return StoreRow(store=base.name_of(store) or store, key=store, kind=base.stores.kind(store), units=units, bhd=bhd,
                    uncosted=unc, items=frozenset(held))


def _behaviour(base: _Base, first: date, last: date) -> dict[str, dict[str, int]]:
    """{store: {behaviour: count}} over the window (see the top of the module)."""
    out: dict[str, dict[str, int]] = {}

    def bump(store: str, key: str, n: int = 1) -> None:
        if n:
            out.setdefault(store, dict.fromkeys(BEHAVIOUR_KEYS, 0))[key] += n

    off = frozenset(base.rules.weekend)
    vouchers = [(leg.no, leg.day, leg.wh) for leg in base.legs.issues + base.legs.receipts]
    legs = {number for number, _day, _store in vouchers}            # a mirror receipt is a leg set aside: once
    vouchers += [(a.voucher, a.day, a.wh) for a in base.result.aside if a.day is not None and a.voucher not in legs]
    for number, day, store in vouchers:
        if first <= day <= last:
            if day.weekday() in off:
                bump(store, "friday")
            if number in base.backdated:
                bump(store, "backdated")
    by_route: dict[tuple, list] = {}
    for i in base.legs.issues:
        by_route.setdefault((i.wh, i.to), []).append(i)
        if (first <= i.day <= last and i.to_kind == "main" and base.stores.kind(i.wh) != "main"
                and i.day >= _month_end(i.day) - timedelta(days=MONTH_END_DAYS - 1)):
            bump(i.wh, "month_end_back")
    for (src, dst), sent in by_route.items():
        back = by_route.get((dst, src))
        if not back:
            continue
        days = [j.day for j in back]                    # the legs come in their order: by day, then number
        used: set = set()
        matched = 0
        for i in sent:                                  # each in its order: the earliest free one coming back
            if not first <= i.day <= last:
                continue
            k = bisect_left(days, i.day)
            while k < len(back) and (back[k].day - i.day).days <= BEHAVIOUR_DAYS:
                j = back[k]
                if j.no not in used and j.lines == i.lines:
                    used.add(j.no)
                    matched += 1
                    break
                k += 1
        bump(dst, "round_trips", matched)               # sent to dst and sent back by it
        bump(src, "reissued", matched)                  # src sent it and had it sent again (from its own side)
    return out


def _period(base: _Base, first: date, last: date) -> dict[str, StoreRow]:
    """{store (canon): StoreRow} for every store that held or moved an accessory in the window."""
    acc: dict[str, dict[str, dict[str, Decimal]]] = {}

    def add(store: str, bucket: str, item: str, units: Decimal) -> None:
        if units:
            slot = acc.setdefault(store, {}).setdefault(bucket, {})
            slot[item] = slot.get(item, ZERO) + units

    for store, held in _canon_balances(base, first - timedelta(days=1)).items():
        for item, units in held.items():
            add(store, "opening", item, units)
    for store, held in _canon_balances(base, last).items():
        for item, units in held.items():
            add(store, "closing", item, units)
    canon = base.canon_of
    for r in base.acc_rows:
        day = r["date"]
        if day < first or day > last:
            continue
        item, vtype = r["item"], r["vtype"]
        moved = r["qty_in"] - r["qty_out"]
        store = canon(r["wh"])
        if vtype == ledger.RECEIVE:
            add(store, "received", item, moved)
        elif vtype == ledger.ISSUE:
            add(store, base.bucket.get(r["voucher"], "not_received"), item, -moved)
            to = canon(r["to_wh"]) if r["to_wh"] else ""
            if to and to != store:                     # what OTHER stores issued to it (never its own voucher)
                add(to, "issued_to", item, -moved)
        elif vtype == ledger.SALE:
            add(store, "sold", item, -moved)
        elif vtype == ledger.SALES_RETURN:
            add(store, "customer_returns", item, moved)
        elif vtype in (ledger.COUNT_OVER, ledger.COUNT_SHORT):
            add(store, "counts", item, moved)
        else:
            add(store, "other", item, moved)
    out = {store: _row(base, store, buckets) for store, buckets in acc.items()}
    for store, counts in _behaviour(base, first, last).items():
        row = out.get(store)
        if row is None:
            row = out[store] = _row(base, store, {})
        row.behaviour = counts
    return out


# ── the audit ─────────────────────────────────────────────────────────────────

@dataclass
class Audit:
    """One window of the movement audit (see the top of the module). The fields other commands read: d, window
    {word, title, first, last, notes}, source {source, note, per_store_ok, why, origin, file, cache_note}, sha,
    result (pairing.Result of the whole ledger), rows (the whole ledger, every item), stores (StoreRow of each van
    and outlet; the one asked for with rep), main (the main store's StoreRow), totals (the vans and outlets added
    up), exceptions, costs ({item: cost held to six decimals, or None}), unmapped, aside (the window's vouchers set
    aside), rules, store_rules, movements (the brief's figures of the window), transfers (pairing.summarise)."""
    d: date
    window: dict
    source: dict
    sha: str
    result: pairing.Result = field(repr=False)
    rows: list = field(repr=False)
    stores: list
    main: StoreRow
    totals: StoreRow
    costs: dict = field(repr=False)
    unmapped: list
    aside: list
    rules: pairing.Rules
    store_rules: pairing.StoreRules
    rep: str | None = None                    # the store asked for, as the ledger spells it
    transfers: dict = field(default_factory=dict, repr=False)
    cost_label: str = "at cost"
    cost_basis: str = "nothing to value"
    cost_counts: dict = field(default_factory=dict)
    unread: list = field(default_factory=list)
    exceptions: list = field(default_factory=list, repr=False)
    movements: dict = field(default_factory=dict)
    store_moves: list = field(default_factory=list, repr=False)
    base: _Base | None = field(default=None, repr=False)
    rep_key: str | None = None

    def store(self, name) -> StoreRow | None:
        """A van's or an outlet's row by its name (any spelling the stores read as one)."""
        want = self.store_rules.canon(name)
        return next((r for r in self.stores if r.key == want), None)

    def exception_items(self, first, last) -> set[str]:
        """The ledger item names on any exception of this audit dated `first` to `last` (both included)."""
        lo, hi = _date_arg(first, "first day"), _date_arg(last, "last day")
        return {ln["item"] for e in self.exceptions if lo <= e["date"] <= hi for ln in e["lines"]}

    @property
    def unbalanced(self) -> list[str]:
        return [r.store for r in self.stores + [self.main] if not r.balanced]

    def facts(self) -> dict:
        """What the screen rests on, as plain figures (the copy under exports/ops/<D>/ keeps it)."""
        ok = self.source["per_store_ok"]
        return {"command": COMMAND, "data_date": self.d, "ledger_first": self.base.first if self.base else None,
                "window": dict(self.window), "rep": self.rep,
                "source": {k: self.source.get(k) for k in ("source", "note", "per_store_ok", "why", "origin", "file")},
                "transfers": self.transfers, "movements": self.movements,
                "cost": {"label": self.cost_label, "basis": self.cost_basis, "counts": dict(self.cost_counts),
                         "unread": list(self.unread), "held_to": str(COST_DP)},
                "main": self.main.as_dict(exact=False) if ok else None,
                "stores": [r.as_dict(exact=False) for r in self.stores] if ok else [],
                "totals": self.totals.as_dict(exact=False) if ok else None,
                "unbalanced": self.unbalanced if ok else [],
                "exceptions": [_exception_json(e) for e in self.exceptions],
                "aside": [{"voucher": a.voucher, "kind": a.kind, "reason": a.reason, "day": a.day, "partner": a.partner,
                           "store": self.base.name_of(a.wh) if self.base else a.wh,
                           "to": self.base.name_of(a.to) if self.base else a.to} for a in self.aside],
                "unmapped": list(self.unmapped), "store_moves": [dict(m) for m in self.store_moves]}


def _exception_json(e: dict) -> dict:
    out = dict(e)
    out["lines"] = [dict(ln) for ln in e["lines"]]
    return out


def _make(base: _Base, source: dict, win: dict, rep_key: str | None) -> Audit:
    first, last = win["first"], win["last"]
    by_store = _period(base, first, last)
    main_key = base.stores.canon(base.stores.main)
    main = by_store.get(main_key) or _row(base, main_key, {})
    if rep_key is not None:
        rows = [by_store.get(rep_key) or _row(base, rep_key, {})]
    else:
        rows = [r for r in by_store.values() if r.kind in TABLE_KINDS and r.active]
        rows.sort(key=lambda r: (TABLE_KINDS.index(r.kind), r.store.casefold(), r.store))
    totals = _total(rows, rows[0].store if rep_key is not None else "Vans and outlets")
    involved = set(main.items) | set(totals.items)
    counts = stock.basis_counts(base.table[n] for n in sorted(involved) if n in base.table)
    label, basis = stock.cost_words(counts)
    aside = [a for a in base.result.aside if a.day is not None and first <= a.day <= last
             and (rep_key is None or rep_key in (a.wh, a.to))]
    a = Audit(d=base.d, window=dict(win), source=dict(source), sha=base.key, result=base.result, rows=base.rows,
              stores=rows, main=main, totals=totals, costs=dict(base.costs), unmapped=list(base.unmapped),
              aside=aside, rules=base.rules, store_rules=base.stores,
              rep=base.name_of(rep_key) if rep_key is not None else None,
              transfers=pairing.summarise(base.result, first=first, last=last, store=rep_key,
                                          direction="any" if rep_key is not None else "out"),
              cost_label=label, cost_basis=basis, cost_counts=counts, unread=list(base.unread), base=base,
              rep_key=rep_key)
    a.exceptions = exceptions(a)
    a.movements = _movements(a)
    if rep_key is not None:
        a.store_moves = _store_moves(a)
    return a


def audit(window=None, *, rep=None, prefer="auto") -> Audit:
    """The movement audit of a window: None is the whole ledger (every exception of it: the action list records from
    that one), a window word ('7d', 'mtd', 'YYYY-MM', 'YYYY-MM-DD..YYYY-MM-DD', the brief's others, '<n>d') or a pair
    of days; rep: one van or outlet (a typed name, as /yq-brief reads one); prefer: where the ledger is read ('auto',
    'file', 'db', or a Stock_ledger workbook or folder). Memoised per (ledger, window, store). MovesInput: one plain
    sentence for what cannot be answered."""
    led, source = _read(prefer)
    base = _base_for(led)
    win = resolve(window, base.d, base.first)
    rep_key = resolve_store(rep, base) if rep not in (None, "") else None
    key = (base.key, source["source"], source["per_store_ok"], win["first"], win["last"], win["title"], rep_key)
    got = _AUDITS.get(key)
    if got is None:
        got = _AUDITS[key] = _make(base, source, win, rep_key)
    return got


# ── what is to check ──────────────────────────────────────────────────────────

def _lines(pairs) -> list[dict]:
    return [{"item": item, "units": units} for item, units in pairs]


def _exception(base: _Base, kind: str, *, issues, receipt, frm, to, received_at, day, deadline, lag, lines, units,
               bhd, uncosted, basis=(), possible=(), **more) -> dict:
    out = {"kind": kind, "issues": list(issues), "receipt": receipt, "from": base.name_of(frm),
           "to": base.name_of(to), "received_at": base.name_of(received_at), "date": day, "deadline": deadline,
           "lag": lag, "lines": lines, "units": units, "bhd": None if bhd is None else fmt.q3(bhd),
           "uncosted": uncosted, "basis": list(basis), "possible": list(possible)}
    out.update(more)
    return out


def _order(e: dict) -> tuple:
    number = e["issues"][0] if e["issues"] else (e["receipt"] or "")
    return (e["bhd"] is None, -(e["bhd"] or ZERO), e["date"], pairing.parse_number(number))


def exceptions(audit: Audit) -> list[dict]:
    """THE single builder of what is to check (the module's TO CHECK): the exceptions of the audit's window, of every
    store or of the store asked for (sent from it, to it or booked in by it), sorted by BHD, largest first, then by
    date and number, the ones with no cost at all last. /yq-watch records from this list; nothing else builds one."""
    base = audit.base
    first, last = audit.window["first"], audit.window["last"]
    rep = audit.rep_key
    rules = base.rules

    def wanted(day, *stores) -> bool:
        return first <= day <= last and (rep is None or rep in stores)

    out: list[dict] = []
    for p in base.result.pairs:
        i, r = p.issue, p.receipt
        if p.outcome not in (pairing.ELSEWHERE, pairing.QTY_DIFFERS) or not wanted(i.day, i.wh, i.to, p.received_at):
            continue
        due = pairing.deadline(i.day, i.to_kind, rules)
        if p.outcome == pairing.ELSEWHERE:
            bhd, unc = base.value(i.lines)
            out.append(_exception(base, "elsewhere", issues=[i.no], receipt=r.no, frm=i.wh, to=i.to,
                                  received_at=p.received_at, day=i.day, deadline=due, lag=p.lag, lines=_lines(i.lines),
                                  units=i.units, bhd=bhd, uncosted=unc, basis=p.basis))
            continue
        differ = [(item, sent, booked) for item, sent, booked in p.diffs if sent != booked]
        gap = [(item, abs(sent - booked)) for item, sent, booked in differ]
        bhd, unc = base.value(gap)
        out.append(_exception(base, "qty_differs", issues=[i.no], receipt=r.no, frm=i.wh, to=i.to,
                              received_at=p.received_at, day=i.day, deadline=due, lag=p.lag,
                              lines=[{"item": item, "units": abs(sent - booked), "issued": sent, "received": booked}
                                     for item, sent, booked in differ],
                              units=sum((u for _item, u in gap), ZERO), bhd=bhd, uncosted=unc, basis=p.basis))
    for o in base.result.open:
        if o.outcome == pairing.IN_GRACE:
            continue                                   # not late yet: "not received yet", never an item to check
        head = o.issues[0]
        day = min(i.day for i in o.issues)
        senders = sorted({i.wh for i in o.issues})     # identical issues share their contents and store, not always a sender
        if not wanted(day, *senders, head.to):
            continue
        possible = [r.no for r in o.possible]
        if o.outcome == pairing.TWINS:
            one, unc1 = base.value(head.lines)
            sent_by = " and ".join(base.name_of(s) for s in senders)
            out.append(_exception(base, "twins", issues=[i.no for i in o.issues], receipt=None, frm=sent_by,
                                  to=head.to, received_at=None, day=day, deadline=o.deadline, lag=None,
                                  lines=_lines(head.lines), units=o.missing * head.units,
                                  bhd=None if one is None else o.missing * one, uncosted=o.missing * unc1,
                                  possible=possible, missing=o.missing, of=len(o.issues), open=o.missing))
            continue
        bhd, unc = base.value(head.lines)
        kind = "damage_not_booked" if head.to_kind == "damage" else "not_received"
        out.append(_exception(base, kind, issues=[head.no], receipt=None, frm=head.wh, to=head.to, received_at=None,
                              day=head.day, deadline=o.deadline, lag=None, lines=_lines(head.lines),
                              units=head.units, bhd=bhd, uncosted=unc, possible=possible))
    for o in base.result.orphans:
        r = o.receipt
        if not wanted(r.day, r.wh):
            continue
        bhd, unc = base.value(r.lines)
        out.append(_exception(base, "orphan", issues=[], receipt=r.no, frm=None, to=None, received_at=r.wh,
                              day=r.day, deadline=None, lag=None, lines=_lines(r.lines), units=r.units, bhd=bhd,
                              uncosted=unc, possible=[i.no for i in o.possible], before_ledger=o.before_ledger))
    out.sort(key=_order)
    return out


def _movements(a: Audit) -> dict:
    """The brief's figures of the window, by what each issue says (its sender and its destination): from the main
    store to the vans and outlets, back to it from them, to the damage store from any store; the units not received
    after the deadline (the kinds that wait for a receipt) and every item to check, with their BHD."""
    base, first, last, rep = a.base, a.window["first"], a.window["last"], a.rep_key
    to_stores = back = damage = ZERO
    to_stores_bhd, to_stores_unc = ZERO, ZERO
    for i in base.legs.issues:
        if not first <= i.day <= last or (rep is not None and rep not in (i.wh, i.to)):
            continue
        sender = base.stores.kind(i.wh)
        if sender == "main" and i.to_kind in TABLE_KINDS:
            to_stores += i.units
            value, unc = base.value(i.lines)
            to_stores_bhd += value or ZERO
            to_stores_unc += unc
        elif sender in TABLE_KINDS and i.to_kind == "main":
            back += i.units
        if i.to_kind == "damage":
            damage += i.units
    waiting = [e for e in a.exceptions if e["kind"] in WAITING]
    return {"to_stores_units": to_stores, "to_stores_bhd": fmt.q3(to_stores_bhd), "to_stores_uncosted": to_stores_unc,
            "back_units": back, "damage_units": damage,
            "not_received_units": sum((e["units"] for e in waiting), ZERO),
            "not_received_bhd": fmt.q3(sum((e["bhd"] or ZERO for e in waiting), ZERO)),
            "not_received_uncosted": sum((e["uncosted"] for e in waiting), ZERO),
            "to_check_count": len(a.exceptions),
            "to_check_bhd": fmt.q3(sum((e["bhd"] or ZERO for e in a.exceptions), ZERO)),
            "to_check_uncosted": sum(1 for e in a.exceptions if e["uncosted"])}


def _outcome_words(base: _Base, got) -> tuple[str, str | None, tuple, str]:
    """(what became of an issue, its receipt number, the further receipts of a pair booked in under two (step S),
    the basis words) for a store's list of transfers."""
    if isinstance(got, pairing.Pair):
        basis = _basis_short(got.basis)
        words = {pairing.RECEIVED: "received", pairing.LATE: "received after its deadline",
                 pairing.QTY_DIFFERS: "received with other quantities, to check",
                 pairing.ELSEWHERE: f"booked in at {store_word(base.name_of(got.received_at), base.stores)}, "
                                    "to check"}[got.outcome]
        if got.also:
            words += f", under {len(got.also) + 1} receipts"
        if got.ambiguous:
            words += " (one of identical issues)"
        return words, got.receipt.no, tuple(x.no for x in got.also), basis
    if got.outcome == pairing.IN_GRACE:
        return f"not received yet (due {_day(got.deadline, base.d)})", None, (), ""
    if got.outcome == pairing.TWINS:
        return (f"{got.missing} of {len(got.issues)} identical issues without a receipt, to check", None, (), "")
    return f"not received after its deadline ({_day(got.deadline, base.d)}), to check", None, (), ""


def _store_moves(a: Audit) -> list[dict]:
    """rep: the store's transfers of the window (issues from it or to it, its receipts), oldest first."""
    base, first, last, store = a.base, a.window["first"], a.window["last"], a.rep_key
    out: list[dict] = []
    listed: set = set()
    for i in base.legs.issues:
        if first <= i.day <= last and store in (i.wh, i.to):
            words, receipt, also, basis = _outcome_words(base, base.result.by_issue[i.no])
            out.append({"date": i.day, "number": i.no, "from": base.name_of(i.wh), "to": base.name_of(i.to),
                        "units": i.units, "outcome": words, "receipt": receipt, "also": list(also), "basis": basis})
            if receipt:                                # each receipt of the pair on this line, never on its own
                listed.update((receipt,) + also)
    for x in base.result.aside:                        # vouchers set aside: an own-store move on one line, its mirror named
        if x.kind == "issue" and x.day is not None and first <= x.day <= last and store in (x.wh, x.to):
            words = f"set aside: {x.reason}" + ("; booked back in there" if x.partner else "")   # its Receipt column
            out.append({"date": x.day, "number": x.voucher, "from": base.name_of(x.wh), "to": base.name_of(x.to),
                        "units": sum((u for _item, u in x.lines), ZERO), "outcome": words, "receipt": x.partner,
                        "also": [], "basis": ""})
            if x.partner:
                listed.add(x.partner)
    for r in base.legs.receipts:
        if not (first <= r.day <= last and r.wh == store) or r.no in listed:
            continue
        got = base.result.by_receipt[r.no]
        if isinstance(got, pairing.Orphan):
            words, issue_no = "a receipt with no issue found, to check", None
        elif isinstance(got, pairing.Aside):
            words, issue_no = f"set aside: books in {got.partner}, sent to its own store", got.partner
        else:                                          # a pair whose issue is not on the list: of another window
            issue_no = got.issue.no
            others = [n for n in (got.receipt.no,) + tuple(x.no for x in got.also) if n != r.no]
            words = (f"booked in for {issue_no} of {_day(got.issue.day, base.d)}"
                     + (f", with {' + '.join(others)}" if others else ""))
        out.append({"date": r.day, "number": r.no, "from": None, "to": base.name_of(r.wh), "units": r.units,
                    "outcome": words, "receipt": None, "also": [], "basis": "", "issue": issue_no})
    out.sort(key=lambda m: (m["date"], pairing.parse_number(m["number"])))
    return out


# ── the API the other commands build on ───────────────────────────────────────

def _require_per_store(source: dict) -> None:
    if not source["per_store_ok"]:
        raise MovesInput(f"The figures per store are not worked out: {source['why']}.")


def _day_in(base: _Base, as_of) -> date:
    day = _date_arg(as_of, "day")
    if day > base.d:
        raise MovesInput(f"The stock ledger runs to {fmt.day_long(base.d)}: it cannot show the stock as on "
                         f"{fmt.day_long(day)}.")
    if day < base.first:
        raise MovesInput(f"The stock ledger starts on {fmt.day_long(base.first)}: it cannot show the stock as on "
                         f"{fmt.day_long(day)}.")
    return day


def exceptions_by_kind(found: list[dict]) -> list[dict]:
    """The items to check summed by kind, in EXCEPTION_KINDS order (a kind with none left out): [{kind, count, units,
    bhd, uncosted}]; bhd at landed cost over the ones with a cost, None when not one of them has a cost."""
    out = []
    for kind in EXCEPTION_KINDS:
        mine = [e for e in found if e["kind"] == kind]
        if not mine:
            continue
        valued = [e["bhd"] for e in mine if e["bhd"] is not None]
        out.append({"kind": kind, "count": len(mine), "units": sum((e["units"] for e in mine), ZERO),
                    "bhd": fmt.q3(sum(valued, ZERO)) if valued else None,
                    "uncosted": sum((e["uncosted"] for e in mine), ZERO)})
    return out


def period_table(first, last) -> dict:
    """The per-store table of a period (both days included; dates or 'YYYY-MM-DD'), for the workbooks' Movements
    sheet: {first, last, basis, stores [{store, kind, units {MOVE_KEYS}, bhd {MOVE_KEYS}, uncosted, uncosted_units,
    shares, behaviour, items, balanced}] (vans, then outlets, by name), totals, main, counts (pairing.summarise of
    the period), exceptions (exceptions_by_kind: {kind, count, units, bhd, uncosted}; the items themselves are
    audit(...).exceptions), per_store_ok, note, notes}. BHD exact (each check exactly nought). MovesInput when the
    figures per store are not proven (source per_store_ok)."""
    a = audit((_date_arg(first, "first day"), _date_arg(last, "last day")))
    _require_per_store(a.source)
    return {"first": a.window["first"], "last": a.window["last"], "basis": f"{a.cost_label} ({a.cost_basis})",
            "stores": [r.as_dict() for r in a.stores], "totals": a.totals.as_dict(), "main": a.main.as_dict(),
            "counts": a.transfers, "exceptions": exceptions_by_kind(a.exceptions), "per_store_ok": True,
            "note": a.source["note"], "notes": list(a.window["notes"])}


def _current(prefer="auto") -> tuple[_Base, dict]:
    led, source = _read(prefer)
    return _base_for(led), source


def store_balances(as_of, kinds=TABLE_KINDS) -> dict[str, dict[str, Decimal]]:
    """{store (as the ledger spells it): {item: units}} of the accessories in each store of the given kinds
    (StoreRules.kind: 'van', 'outlet', 'main', ...) at the end of `as_of`, from the ledger's balances; a store and
    an item that stand at nought are left out. MovesInput when the figures per store are not proven, or the day is
    outside the ledger."""
    base, source = _current()
    _require_per_store(source)
    day = _day_in(base, as_of)
    out: dict[str, dict[str, Decimal]] = {}
    for store, held in _canon_balances(base, day).items():
        if base.stores.kind(store) in kinds and held:
            out[base.name_of(store)] = dict(sorted(held.items()))
    return dict(sorted(out.items()))


def books_only(as_of, held_days: int = 30) -> list[dict]:
    """The book side of a store nobody counted (the month-end reconciliation): one entry per van and outlet that held
    an accessory at the end of `as_of` or moved one in the `held_days` days to it, vans then outlets, by name:
        {store, units, bhd, uncosted, held_over: [{item, code, units, last_sale, days}]}
    units: on the books at the end of `as_of`; bhd: those of them with a cost at landed cost (/yq-stock's basis, to
    the fils; None when the store holds units and not one of them has a cost); uncosted: the units with no cost.
    held_over: the items it holds above nought that it has not sold in the `held_days` days to `as_of` and last
    received there `held_days` days ago or more (a Stock Receive Voucher or a supplier receipt; none on file counts
    as old): item and code as /yq-stock names them, units, last_sale (None: never sold there) and days (since that
    sale, else since that receipt; None with neither), the longest first."""
    base, source = _current()
    _require_per_store(source)
    day = _day_in(base, as_of)
    n = max(int(held_days), 1)
    since = day - timedelta(days=n - 1)
    held = _canon_balances(base, day)
    moved: set = set()
    last_sale: dict[tuple, date] = {}
    last_in: dict[tuple, date] = {}
    for r in base.acc_rows:
        when = r["date"]
        if when > day:
            continue
        store = base.canon_of(r["wh"])
        if when >= since:
            moved.add(store)
        key = (store, r["item"])
        if r["vtype"] == ledger.SALE and r["qty_out"] > 0:
            if key not in last_sale or when > last_sale[key]:
                last_sale[key] = when
        elif r["vtype"] in (ledger.RECEIVE, ledger.SUPPLIER_RECEIPT) and r["qty_in"] > 0:
            if key not in last_in or when > last_in[key]:
                last_in[key] = when
    out: list[tuple] = []
    for store in set(held) | moved:
        kind = base.stores.kind(store)
        if kind not in TABLE_KINDS:
            continue
        mine = held.get(store, {})
        value, priced, uncosted = ZERO, False, ZERO
        over = []
        for item, units in sorted(mine.items()):
            cost = base.costs.get(item)
            if cost is None:
                uncosted += units
            else:
                value += units * cost
                priced = True
            if units <= 0:
                continue
            sold, got_in = last_sale.get((store, item)), last_in.get((store, item))
            if (sold is not None and sold >= since) or (got_in is not None and (day - got_in).days < n):
                continue
            it = base.table.get(item)
            mark = sold or got_in
            over.append({"item": it.name if it is not None else item, "code": it.code if it is not None else None,
                         "units": units, "last_sale": sold, "days": (day - mark).days if mark is not None else None})
        over.sort(key=lambda x: (x["days"] is None, -(x["days"] or 0), -x["units"], x["item"]))
        units = sum(mine.values(), ZERO)
        bhd = fmt.q3(value) if priced or not units else None
        name = base.name_of(store)
        out.append(((TABLE_KINDS.index(kind), name.casefold(), name),
                    {"store": name, "units": units, "bhd": bhd, "uncosted": uncosted, "held_over": over}))
    return [entry for _key, entry in sorted(out, key=lambda t: t[0])]


def brief_facts(days: int = 7) -> dict:
    """The brief's MOVEMENTS line: the stock ledger's last `days` days (memoised per ledger), as plain values:
    {first, last, ledger_to, to_stores_units, to_stores_bhd, back_units, damage_units, not_received_units,
     not_received_bhd, to_check_count, to_check_bhd, per_store_ok, note}. BHD at landed cost as text to the fils."""
    n = int(days)
    if n < 1:
        raise ValueError("brief_facts: days is one or more")
    a = audit(f"{n}d")
    m = a.movements
    notes = []
    if not a.source["per_store_ok"]:
        notes.append(f"The figures per store are not printed: {a.source['why']}.")
    unc = m["to_stores_uncosted"] + m["not_received_uncosted"]
    if unc:
        notes.append(f"{_plural(unc, 'unit')} with no cost on file are counted, not valued.")
    return {"first": a.window["first"].isoformat(), "last": a.window["last"].isoformat(), "ledger_to": a.d.isoformat(),
            "to_stores_units": _units_value(m["to_stores_units"]), "to_stores_bhd": _money(m["to_stores_bhd"]),
            "back_units": _units_value(m["back_units"]), "damage_units": _units_value(m["damage_units"]),
            "not_received_units": _units_value(m["not_received_units"]),
            "not_received_bhd": _money(m["not_received_bhd"]), "to_check_count": m["to_check_count"],
            "to_check_bhd": _money(m["to_check_bhd"]), "per_store_ok": bool(a.source["per_store_ok"]),
            "note": " ".join(notes) or None}


def exception_items(first, last) -> set[str]:
    """The ledger item names on any exception (of the whole ledger) dated `first` to `last`: the count sheets list
    them on the main store's cycle list."""
    return audit(None).exception_items(first, last)


# ── the screen ────────────────────────────────────────────────────────────────

UNIT_COLUMNS = (("Opening", "opening"), ("Received", "received"), ("Sent to it", "issued_to"), ("Sold", "sold"),
                ("Returns", "customer_returns"), ("To main", "back_to_main"), ("To damage", "to_damage"),
                ("To others", "to_other"), ("Not yet", "not_received_yet"), ("Not received", "not_received"),
                ("Counts", "counts"), ("Other", "other"), ("Closing", "closing"), ("Check", "check"))
ALWAYS = frozenset({"opening", "received", "sold", "closing", "check"})


def _check_cell(x: Decimal, money: bool = False) -> str:
    if not x:
        return "0"
    return f"{fmt.bhd(x) if money else fmt.units(x)} to check"


def units_table(rows: list[StoreRow]) -> list[str]:
    """The units of each row, every column that holds a figure (opening, received, sold, closing and check always);
    'Sent to it' (issued_to) only where it differs from what the store received."""
    differs = {r.key for r in rows if r.units["issued_to"] != r.units["received"]}
    cols = [(h, k) for h, k in UNIT_COLUMNS
            if (k in ALWAYS) or (k == "issued_to" and differs)
            or (k != "issued_to" and any(r.units[k] or r.bhd[k] or r.uncosted[k] for r in rows))]
    grid = []
    for r in rows:
        cells = [short_store(r.store)]
        for _h, k in cols:
            if k == "issued_to":
                cells.append(fmt.units(r.units[k]) if r.key in differs else "")
            elif k == "check":
                cells.append(_check_cell(r.units[k]))
            else:
                cells.append(fmt.units(r.units[k]))
        grid.append(cells)
    return _indent(fmt.grid(["Store"] + [h for h, _k in cols], grid, "l" + "r" * len(cols)))


def bhd_table(rows: list[StoreRow]) -> list[str]:
    """The same rows at landed cost: opening + received + returns and counts - sold - sent on - not received
    = closing (each to the fils)."""
    grid = []
    for r in rows:
        b = r.bhd
        grid.append([short_store(r.store), fmt.bhd(b["opening"]), fmt.bhd(b["received"]),
                     fmt.bhd(b["customer_returns"] + b["counts"] + b["other"]), fmt.bhd(b["sold"]),
                     fmt.bhd(b["back_to_main"]), fmt.bhd(b["to_damage"]), fmt.bhd(b["to_other"]),
                     fmt.bhd(b["not_received_yet"] + b["not_received"]), fmt.bhd(b["closing"]),
                     _check_cell(b["check"], money=True)])
    heads = ["Store", "Opening", "Received", "Returns, counts", "Sold", "To main", "To damage", "To others",
             "Not received", "Closing", "Check"]
    return _indent(fmt.grid(heads, grid, "l" + "r" * (len(heads) - 1)))


def behaviour_table(rows: list[StoreRow]) -> list[str]:
    grid = []
    for r in rows:
        s = r.shares()
        grid.append([short_store(r.store), "n/a" if s["sold"] is None else fmt.pct(s["sold"], 1),
                     "n/a" if s["back"] is None else fmt.pct(s["back"], 1)]
                    + [fmt.units(r.behaviour[k]) for k in BEHAVIOUR_KEYS])
    heads = ["Store", "Sold share", "Back share", "Friday", "Back-dated", "Round trips", "Re-issued", "Month-end back"]
    return _indent(fmt.grid(heads, grid, "l" + "r" * (len(heads) - 1)))


BEHAVIOUR_NOTE = (f"Shares: units sold, and units sent back to the main store, over the opening stock plus what the "
                  f"store received. Counted, never an item to check: transfer vouchers dated on a Friday; vouchers "
                  f"dated before one its series numbered earlier (back-dated); goods sent to the store and sent back "
                  f"within {BEHAVIOUR_DAYS} days (round trips), or sent back and sent again (re-issued); issues back to "
                  f"the main store in the last {MONTH_END_DAYS} days of a month.")


def _transfer_lines(a: Audit) -> list[str]:
    t = a.transfers
    c = t["counts"]
    parts = []
    for key, words in (("received", "received"), ("late", "received after the deadline"),
                       ("elsewhere", "booked in at another store"), ("qty_differs", "received with other quantities"),
                       ("in_grace", "not received yet (inside the deadline)"),
                       ("not_received", "not received after the deadline")):
        if c[key]:
            parts.append(f"{fmt.units(c[key])} {words}")
    if c["twin_slots"]:
        parts.append(f"{fmt.units(c['twin_slots'])} without a receipt among "
                     f"{_plural(c['twin_items'], 'set')} of identical issues")
    where = f" from or to {short_store(a.rep)}" if a.rep else ""
    lines = _para(f"TRANSFERS  {_plural(t['issues']['vouchers'], 'issue')}{where} dated in the window"
                  + (": " + ", ".join(parts) if parts else ""), rest="  ")
    more = []
    if c["orphans"]:
        more.append(f"{_plural(c['orphans'], 'receipt')} with no issue found")
    if c["aside"]:
        more.append(f"{_plural(c['aside'], 'voucher')} set aside (they cannot be paired)")
    if more:
        lines += _para("Besides: " + "; ".join(more) + ".", "  ")
    m = a.movements
    if a.rep is None:
        unc = f" (+{_plural(m['to_stores_uncosted'], 'unit')} with no cost)" if m["to_stores_uncosted"] else ""
        lines += _para(f"From the main store to the vans and outlets {fmt.units(m['to_stores_units'])} units "
                       f"(BHD {fmt.bhd(m['to_stores_bhd'])} {a.cost_label}{unc}); back to it from them "
                       f"{fmt.units(m['back_units'])}; to the damage store {fmt.units(m['damage_units'])}.", "  ")
    lines += _para(PAIRED_BY, "  ")
    return lines


def _exception_text(e: dict, d: date, stores: pairing.StoreRules | None = None) -> str:
    kind, day = e["kind"], _day(e["date"], d)
    route = f"{store_word(e['from'], stores)} to {store_word(e['to'], stores)}"
    if kind == "not_received":
        text = f"{e['issues'][0]} {day}, {route}: not received after its deadline ({_day(e['deadline'], d)})"
    elif kind == "damage_not_booked":
        text = (f"{e['issues'][0]} {day}, {route}: not booked in by the damage store by its deadline "
                f"({_day(e['deadline'], d)})")
    elif kind == "twins":
        text = (f"{e['missing']} of {e['of']} identical issues {day}, {route}, without a receipt: "
                + ", ".join(e["issues"]))
    elif kind == "qty_differs":
        diff = "; ".join(f"{_short(ln['item'], 24)} {fmt.units(ln['issued'])} sent, {fmt.units(ln['received'])} booked"
                         for ln in e["lines"][:2])
        more = f" (+{len(e['lines']) - 2} more lines)" if len(e["lines"]) > 2 else ""
        text = f"{e['issues'][0]} {day}, {route}: received under {e['receipt']} with other quantities: {diff}{more}"
    elif kind == "elsewhere":
        basis = ", ".join(BASIS_WORDS[b] for b in e["basis"]) or "by contents"
        text = (f"{e['issues'][0]} {day}, {route}: booked in at {store_word(e['received_at'], stores)} under "
                f"{e['receipt']} ({basis})")
    else:
        text = f"{e['receipt']} {day} at {store_word(e['received_at'], stores)}: no issue found"
        if e.get("before_ledger"):
            text += " (in the ledger's first weeks: its issue may be older than the ledger)"
    if e["possible"]:
        text += "; possible match " + ", ".join(e["possible"])
    return text


def aside_words(x: pairing.Aside, d: date) -> str:
    """A voucher set aside, in a few words: an issue sent to its own store names the receipt that booked it back in
    there (its mirror, set aside with it), and the mirror names its issue."""
    if x.partner and x.kind == "issue":
        why = f"sent to its own store; booked back in there under {x.partner}"
    elif x.partner:
        why = f"books in {x.partner}, sent to its own store"
    else:
        why = x.reason
    return f"{x.voucher} {_day(x.day, d)} ({why})"


def to_check_lines(a: Audit) -> list[str]:
    ex = a.exceptions
    if not ex:
        return ["TO CHECK  nothing in the window."]
    total = sum((e["bhd"] or ZERO for e in ex), ZERO)
    unvalued = sum(1 for e in ex if e["bhd"] is None)
    head = (f"TO CHECK  {_plural(len(ex), 'item')}, BHD {fmt.bhd(total)} {a.cost_label}, largest first"
            + (f" ({unvalued} with no cost on file, last)" if unvalued else ""))
    shown = ex[:TOP_SHOWN]
    cells = [("no cost" if e["bhd"] is None else fmt.bhd(e["bhd"]), fmt.units(e["units"])) for e in shown]
    wb = max([len("BHD")] + [len(b) for b, _u in cells])
    wu = max([len("Units")] + [len(u) for _b, u in cells])
    lines = [head, f"  {'BHD':>{wb}}  {'Units':>{wu}}  What"]
    hang = " " * (2 + wb + 2 + wu + 2)
    for e, (b, u) in zip(shown, cells):
        lines += _para(_exception_text(e, a.d, a.store_rules), f"  {b:>{wb}}  {u:>{wu}}  ", hang)
    if len(ex) > TOP_SHOWN:
        lines.append(f"  (+{len(ex) - TOP_SHOWN} more on /yq-watch)")
    return lines


def _store_move_lines(a: Audit) -> list[str]:
    moves = a.store_moves
    if not moves:
        return ["ITS TRANSFERS  none in the window."]
    stores = a.store_rules
    rows = [[_day(m["date"], a.d), m["number"], store_word(m["from"], stores), store_word(m["to"], stores),
             fmt.units(m["units"]), " + ".join([m["receipt"]] + list(m.get("also") or [])) if m.get("receipt") else "",
             m["basis"], m["outcome"]]
            for m in moves[:STORE_MOVES_SHOWN]]
    lines = [f"ITS TRANSFERS  {_plural(len(moves), 'voucher')} in the window, oldest first"]
    lines += _indent(fmt.grid(["Date", "Voucher", "From", "To", "Units", "Receipt", "Basis", "What became of it"], rows,
                              "llllrlll"))
    if len(moves) > STORE_MOVES_SHOWN:
        lines.append(f"  (+{len(moves) - STORE_MOVES_SHOWN} more in the facts file)")
    return lines


def screen_lines(a: Audit) -> list[str]:
    d, win = a.d, a.window
    who = f"{a.rep}, " if a.rep else ""
    lines = _para(f"MOVES  {who}{win['title']}: {_day(win['first'], d)} to {_day(win['last'], d)}; the stock "
                  f"ledger runs to {_day(d)}", rest="  ") + _para(a.source["note"], "  ")
    for n in win["notes"]:
        lines += _para(n, "  ")
    lines += [""] + _transfer_lines(a)
    rows = a.stores
    if not a.source["per_store_ok"]:
        lines += [""] + _para(f"Per-store figures not printed: {a.source['why']}.", rest="  ")
    else:
        title = "THE STORE" if a.rep else "VANS AND OUTLETS"
        if not rows:
            lines += ["", f"{title}  no van or outlet held or moved an accessory in the window."]
        else:
            lines += ["", f"{title}  units"] + units_table(rows)
            lines += [""] + _para(f"{a.cost_label[0].upper() + a.cost_label[1:]}, BHD ({a.cost_basis})", "  ")
            lines += bhd_table(rows)
            lines += ["", "  Shares and behaviour"] + behaviour_table(rows) + _para(BEHAVIOUR_NOTE, "  ")
            unc = a.totals.uncosted_units
            if unc:
                lines += _para(f"{_plural(unc, 'unit')} of items with no cost on file are in the units, never in BHD.",
                               "  ")
        if a.rep is None:
            lines += ["", f"MAIN STORE  {a.main.store}: units"] + units_table([a.main])
            lines += [f"  {a.cost_label[0].upper() + a.cost_label[1:]}, BHD"] + bhd_table([a.main])
        off = a.unbalanced
        if off:
            lines += _para(f"The check is not 0 for {', '.join(short_store(s) for s in off)}: a movement fell in no "
                           "column or in two, a fault of this program, to check before these figures are used.", "  ")
    if a.rep:
        lines += [""] + _store_move_lines(a)
    lines += [""] + to_check_lines(a)
    if a.aside:
        shown = ", ".join(aside_words(x, d) for x in a.aside[:ASIDE_SHOWN])
        more = f" (+{len(a.aside) - ASIDE_SHOWN} more)" if len(a.aside) > ASIDE_SHOWN else ""
        lines += [""] + _para(f"SET ASIDE  {_plural(len(a.aside), 'voucher')} of the window could not be paired (an "
                              f"issue among them that nothing booked in counts as not received): {shown}{more}",
                              rest="  ")
    if a.unmapped and a.rep is None:
        names = ", ".join(_short(n, 34) for n in a.unmapped[:UNMAPPED_SHOWN])
        more = f" (+{len(a.unmapped) - UNMAPPED_SHOWN} more)" if len(a.unmapped) > UNMAPPED_SHOWN else ""
        lines += [""] + _para(f"UNMAPPED  {_plural(len(a.unmapped), 'accessory', 'accessories')} the item bridge "
                              f"cannot place (no code in the price book, no name in the stock balance): in every "
                              f"figure, valued only by a receipt rate: {names}{more}", rest="  ")
    lines += [""] + _para("Basis: accessories (items that ever moved in the main store), SIM out; units sold = Sales "
                          "Invoice units, customer returns apart. At landed cost = /yq-stock's basis, each cost held to "
                          "six decimals.", rest="  ")
    for part in a.unread:
        lines += _para(f"Not read this time: {part}.", rest="  ")
    lines += [PORTAL_LINE, NOT_MEASURED]
    return lines


# ── the command ───────────────────────────────────────────────────────────────

def parse_words(words) -> tuple[str | None, str | None]:
    """(window, rep) from the words after `moves`, in any order (sales.parse_spec reads them); 'again' and 'top:'
    are the brief's and refused in one sentence."""
    try:
        window, rep, again, top = sales.parse_spec(words)
    except sales.BriefInput as exc:
        raise MovesInput(str(exc)) from None
    if again or top is not None:
        raise MovesInput(REFUSED_WORDS)
    return window, rep


def file_stem(window: str | None, rep: str | None) -> str:
    """moves, moves-<window>, moves-rep-<name> (a name with no Latin letter stood for by its hash)."""
    from scripts.ops import alerts
    stem = COMMAND + (f"-{_slug(window)}" if window else "")
    return stem + (f"-rep-{_slug(rep) or alerts.ref_hash(rep)}" if rep else "")


def add_arguments(parser) -> None:
    parser.add_argument("spec", nargs="*", metavar="word",
                        help="a window (7d, mtd, YYYY-MM, YYYY-MM-DD..YYYY-MM-DD; the brief's other windows too) and "
                             "rep:NAME for one van or outlet")
    parser.add_argument("--ledger", default="auto", metavar="SOURCE",
                        help="where the stock ledger is read: auto (the file when it equals production), file, db, or "
                             "a Stock_ledger workbook or its folder (read as it is, not compared)")
    parser.add_argument("--golden-build", action="store_true", help=argparse.SUPPRESS)


def run(args) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    if getattr(args, "golden_build", False):
        return golden_build(args)
    try:
        window, typed_rep = parse_words(getattr(args, "spec", None))
        a = audit(window or DEFAULT_WINDOW, rep=typed_rep, prefer=getattr(args, "ledger", None) or "auto")
    except MovesInput as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    text = "\n".join(_guarded(ln).rstrip() for ln in screen_lines(a))
    files = sales.save_screen(a.d, file_stem(window, short_store(a.rep) if a.rep else None), text, a.facts())
    warnings = [a.source["cache_note"]] if a.source.get("cache_note") else []
    flags = {"window": a.window["word"], "first": a.window["first"].isoformat(), "last": a.window["last"].isoformat(),
             "rep": bool(a.rep), "per_store_ok": a.source["per_store_ok"], "to_check": len(a.exceptions),
             "unbalanced": len(a.unbalanced) if a.source["per_store_ok"] else 0}
    return dict(out, data_date=a.d.isoformat(), text=text, flags=flags, files=[str(f) for f in files],
                warnings=warnings)


# ── the golden list (hidden: --golden-build) ──────────────────────────────────

def _golden_source(choice) -> Path:
    """The ledger the golden list is built on: --ledger when it names a workbook or a folder; otherwise the folder
    golden/pairing.json names, else the loaded folder."""
    if choice and str(choice) not in ledger.PREFER:
        return Path(choice)
    named = paths.golden_dir() / "pairing.json"
    if named.is_file():
        try:
            folder = json.loads(named.read_text(encoding="utf-8")).get("folder")
        except (OSError, ValueError):
            folder = None
        if folder:
            candidate = paths.data_root() / "business_data" / str(folder)
            if candidate.is_dir():
                return candidate
    folder = ledger.loaded_folder()
    if folder is None:
        raise MovesInput("No loaded folder to build the golden list from: name a Stock_ledger file with --ledger.")
    return folder


def _hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _leg_json(base: _Base, leg) -> dict:
    return {"number": leg.no, "date": leg.day, "store": base.name_of(leg.wh), "to": base.name_of(leg.to),
            "lines": _lines(leg.lines), "narration_hash": leg.narr}


def _pair_json(base: _Base, p: pairing.Pair, kind: str) -> dict:
    """A pair as the golden list records it: the issue, its receipt and the further receipts of a pair booked in
    under two (step S, Pair.also), every leg's lines."""
    return {"kind": kind, "outcome": p.outcome, "step": p.step, "issue": p.issue.no, "receipt": p.receipt.no,
            "also": [x.no for x in p.also], "lag": p.lag, "basis": list(p.basis), "issue_leg": _leg_json(base, p.issue),
            "receipt_leg": _leg_json(base, p.receipt), "also_legs": [_leg_json(base, x) for x in p.also],
            "sha256_of_issue": "sha256:" + _hash(p.issue.no)}


def _golden_cases(a: Audit) -> tuple[list[dict], list[dict], list[dict]]:
    """(every exception of the audit, with both legs; the clean pairs kept: step A, not one of identical issues,
    the first GOLDEN_CLEAN by the sha256 of the issue number; every pair booked in under two receipts, step S, by
    the day and number of its issue)."""
    base = a.base
    issue_of = {i.no: i for i in base.legs.issues}
    receipt_of = {r.no: r for r in base.legs.receipts}
    cases = []
    for e in a.exceptions:
        case = _exception_json(e)
        case["issue_legs"] = [_leg_json(base, issue_of[n]) for n in e["issues"] if n in issue_of]
        case["receipt_leg"] = _leg_json(base, receipt_of[e["receipt"]]) if e["receipt"] in receipt_of else None
        cases.append(case)
    clean = [p for p in base.result.pairs if p.step == "A" and not p.ambiguous]
    clean.sort(key=lambda p: (_hash(p.issue.no), p.issue.no))
    split = sorted((p for p in base.result.pairs if p.also), key=lambda p: (p.issue.day, pairing.parse_number(p.issue.no)))
    return cases, [_pair_json(base, p, "clean") for p in clean[:GOLDEN_CLEAN]], [_pair_json(base, p, "split")
                                                                                  for p in split]


def _sample(cases: list[dict], clean: list[dict], split: list[dict] = ()) -> list[tuple[str, dict]]:
    """The cases to read with the owner: at most GOLDEN_PER_KIND of each kind of exception and of the pairs booked in
    under two receipts (each by the sha256 of its number), the rest clean pairs (in their order), GOLDEN_SAMPLE in
    all."""
    picked: list[tuple[str, dict]] = []
    for kind in EXCEPTION_KINDS:
        mine = [c for c in cases if c["kind"] == kind]
        mine.sort(key=lambda c: _hash(c["issues"][0] if c["issues"] else c["receipt"]))
        picked += [(kind, c) for c in mine[:GOLDEN_PER_KIND]]
    picked += [("split", c) for c in sorted(split, key=lambda c: _hash(c["issue"]))[:GOLDEN_PER_KIND]]
    picked = picked[:GOLDEN_SAMPLE]
    picked += [("clean", c) for c in clean[:GOLDEN_SAMPLE - len(picked)]]
    return picked


def _leg_text(leg: dict | None, d: date) -> str:
    if not leg:
        return "none"
    lines = ", ".join(f"{ln['item']} ({fmt.units(ln['units'])})" for ln in leg["lines"])
    where = leg["store"] + (f" to {leg['to']}" if leg.get("to") else "")
    return f"{leg['number']} {_day(leg['date'], d)}, {where}: {lines}"


def golden_review(a: Audit, cases: list[dict], clean: list[dict], split: list[dict] = ()) -> list[str]:
    d = a.d
    sample = _sample(cases, clean, split)
    lines = [f"GOLDEN REVIEW  {a.source['file']} (the ledger runs to {_day(d)}): {_plural(len(cases), 'exception')}, "
             f"{_plural(len(split), 'pair')} booked in under two receipts and {_plural(len(clean), 'clean pair')}, "
             "accessories only. Local only: never committed or sent.", "",
             f"SAMPLE  {len(sample)} cases to read with Furqan ({GOLDEN_PER_KIND} of each kind of exception and of the "
             "pairs booked in under two receipts where there are, then clean pairs; chosen by the sha256 of the voucher "
             "number)"]
    grid = []
    for n, (kind, c) in enumerate(sample, 1):
        if kind in ("clean", "split"):
            grid.append([str(n), f"{kind} ({c['outcome']})", c["issue"], " + ".join([c["receipt"]] + c["also"]),
                         ", ".join(BASIS_WORDS[b] for b in c["basis"]) or "by contents"])
        else:
            grid.append([str(n), kind.replace("_", " "), ", ".join(c["issues"]) or "-", c["receipt"] or "-",
                         ", ".join(BASIS_WORDS[b] for b in c["basis"]) or "-"])
    lines += _indent(fmt.grid(["#", "Kind", "Issue", "Receipt", "Basis"], grid, "rllll"))
    lines += ["", "CASES  both legs of every case: number, day, store, lines"]
    for n, c in enumerate(cases, 1):
        lines.append(f"  [{n}] {c['kind'].replace('_', ' ')}: {_exception_text(c, d, a.store_rules)}")
        for leg in c["issue_legs"]:
            lines.append(f"      issue    {_leg_text(leg, d)}")
        lines.append(f"      receipt  {_leg_text(c['receipt_leg'], d)}")
    for n, c in enumerate(list(split) + list(clean), len(cases) + 1):
        basis = ", ".join(BASIS_WORDS[b] for b in c["basis"]) or "by contents"
        lines.append(f"  [{n}] {c['kind']}, {c['outcome']}, lag {c['lag']} days, {basis}")
        lines.append(f"      issue    {_leg_text(c['issue_leg'], d)}")
        lines.append(f"      receipt  {_leg_text(c['receipt_leg'], d)}")
        for leg in c["also_legs"]:
            lines.append(f"      and      {_leg_text(leg, d)}")
    return lines


def golden_build(args) -> dict:
    """--golden-build: the golden list of the accessories' pairing and its review text (see the top of the module)."""
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {"golden": True}, "files": [],
           "warnings": []}
    if paths.is_worktree() and not paths.root_is_set():
        return dict(out, ok=False, exit=1, text="This is a code worktree: the golden list belongs to the project folder "
                                                "(to build one, name another folder with --data-root).")
    try:
        source = _golden_source(getattr(args, "ledger", None))
        a = audit(None, prefer=str(source))
    except MovesInput as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    cases, clean, split = _golden_cases(a)
    base = a.base
    by_kind = {k: sum(1 for c in cases if c["kind"] == k) for k in EXCEPTION_KINDS}
    doc = {"version": 2, "folder": source.name if source.is_dir() else source.parent.name, "file": a.source["file"],
           "sha256": base.led.sha, "first_date": base.first, "data_date": a.d, "scope": "accessories",
           "rules": {"lag_max": base.rules.lag_max, "dn_max": base.rules.dn_max, "quick_lag": base.rules.quick_lag,
                     "damage_grace_days": base.rules.damage_grace_days},
           "counts": base.result.counts(), "issues": len(base.legs.issues), "receipts": len(base.legs.receipts),
           "out_of_scope": base.legs.out_of_scope, "exceptions_by_kind": by_kind,
           "aside": [{"voucher": x.voucher, "kind": x.kind, "reason": x.reason, "partner": x.partner}
                     for x in base.result.aside],
           "exceptions": cases, "split": split,
           "clean_rule": f"step A, not among identical issues: the first {GOLDEN_CLEAN} by the sha256 of the issue number",
           "clean": clean}
    target = paths.golden_dir() / "pairs_accessories.json"
    review = paths.exports_dir(a.d) / "moves-golden-review.txt"
    text_review = "\n".join(_guarded(ln).rstrip() for ln in golden_review(a, cases, clean, split)) + "\n"
    files = [paths.write_text(target, json.dumps(_jsonable(doc), indent=1, ensure_ascii=False) + "\n"),
             paths.write_text(review, text_review)]
    kinds = ", ".join(f"{k.replace('_', ' ')} {n}" for k, n in by_kind.items())
    text = "\n".join(_guarded(ln) for ln in (
        f"GOLDEN  {doc['folder']}, the ledger to {_day(a.d)}: {_plural(len(cases), 'exception')} ({kinds}), "
        f"{_plural(len(split), 'pair')} booked in under two receipts and {_plural(len(clean), 'clean pair')}",
        f"  Written: {target.relative_to(paths.data_root()) if paths.allowed_output(target) else target}",
        f"  Review:  {review.relative_to(paths.data_root()) if paths.allowed_output(review) else review}",
        "  Both are local (git-ignored): the review names stores, items and vouchers."))
    return dict(out, data_date=a.d.isoformat(), text=text, files=[str(f) for f in files],
                flags={"golden": True, "exceptions": by_kind, "split": len(split), "clean": len(clean),
                       "sample": [k for k, _c in _sample(cases, clean, split)]})
