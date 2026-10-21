"""The two legs of a stock transfer, paired by what they hold: one rule for /yq-moves and the portal.

A transfer in Focus is two vouchers that never point at each other: a Stock Issue Voucher, where stock
leaves one store for another, and a Stock Receive Voucher, where the receiving store books it in. Which
receipt belongs to which issue is worked out from what they hold (items and units, the destination, the
narration's hash, the voucher numbers, the days between them), never from the numbers alone. /yq-moves
and the portal's Stock Moves page both call this module, so they apply one rule:

  Rules         the limits of the pairing, one frozen value: the lag, the number and lag gates, the overlap
                of other quantities, the window of a possible match, the grace before a receipt is late
  StoreRules    one name per store (canon) and what kind of store it is (kind)
  dec, narr_key   held equal to scripts.ops.ledger's own (_dec, _narr_key): a leg shaped from the export
                  and a leg shaped here (from the database, in the portal) compare equal
  parse_number, number_distance   a voucher number as a key that sorts, and how far apart two numbers are
  deadline, within_grace   the day by which an issue's receipt is due; whether an issue with none is still
                in grace on the data date
  legs_from_rows  ledger rows -> whole vouchers (Leg), the ones that cannot be paired set aside (Aside)
  pair          the legs -> what became of every issue and every receipt (Result)
  summarise     a Result read over a window of issue dates, for one store or all

HOW THE LEGS ARE PAIRED. The signature of a leg is its lines: every item with its units. Steps, in order;
each step takes only legs no earlier step took:
  A  received where it was sent: an issue and a receipt of the same signature, the receipt in the store
     the issue names, booked 0 to lag_max days after it. Per signature and store, the issues in order
     each take the earliest receipt still free (the most pairs there can be, the windows being equal).
     RECEIVED, or LATE when the receipt is booked after the issue's deadline.
  B  received elsewhere: the same signature in another store (back in the issuing store as well),
     booked 0 to lag_max days after, and the gate holds. ELSEWHERE.
     The exchange, then: identical issues to one store still short of receipts (twins, below), one of
     them paired in step A and able to take a free receipt of the same contents in another store (the
     gate holding), another with none able to take its receipt: another store booked in the transfer
     meant for this one and the goods were issued again. The first is received elsewhere, the other
     takes its receipt. The oldest set first, its earliest members first.
  C  other quantities: the store the issue names, booked 0 to lag_max days after, the gate holds, and
     the two share at least overlap_min of their items (Jaccard). QTY_DIFFERS, item by item.
  The gate holds on any of: the same narration (not blank); numbers at most dn_max apart within one
  series and month; booked within quick_lag days. B and C rank the candidates (C by the share of items
  first; then the narration before the number before the date alone, the distance, the lag, the
  issue's and the receipt's order) and take the best first.
  An issue sent to its own store is set aside (legs_from_rows), and so is the free receipt that books its
  contents in at that store when the gate holds (the nearest number, then the nearest day): never a
  receipt with no issue.
  Twins: identical issues to one store that step A cannot tell apart (they reach the same receipts,
  directly or through one another), one or more of them still without a receipt after the three steps.
  They are ONE open item, "x of N identical issues have no receipt", every number named, never which
  one; the pairs among them are marked ambiguous. The set is in grace only while every one of its
  issues is.
  The rest: an issue with no receipt is IN_GRACE while within_grace() holds (on or before its deadline;
  to the damage store, before it, as /yq-stock damage counts), then NOT_RECEIVED; a receipt with no
  issue is an ORPHAN. Each names up to three legs of the same contents that no step could take (outside
  the lag, or the gate refused them) as possible matches.
Every choice is made in a total order (day, number, then the leg's order), so the same rows in any order
give the same Result.

PURE. The standard library only: no scripts.* import, no database, no config read (the caller passes the
stores and the rules in), no clock. The same arguments give the same answer, every time.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

# Voucher type names as Focus writes them in the ledger's "Voucher name" column (scripts.ops.ledger's own).
ISSUE = "Stock Issue Voucher"                # stock leaves a store for another
RECEIVE = "Stock Receive Voucher"            # the receiving store books it in; it names no issue

NARR_LEN = 12                                # a narration's hash, in letters (scripts.ops.ledger.NARR_LEN)
ZERO = Decimal(0)
Q3 = Decimal("0.001")                        # three decimals: the fils (scripts.ops.fmt.Q3)
_DIGIT_TO_LETTER = str.maketrans("0123456789", "ghijklmnop")     # scripts.ops.loadplan.letters_only

# What kind of store a name is: the first that holds, in this order (StoreRules.kind).
KINDS = ("main", "damage", "not_counted", "sim", "van", "outlet", "other")
SIM_SUFFIX = " - sim wh"                     # a van's SIM store, folded
SIM_STORE = "sim warehouse"
VAN_SUFFIX = " - acc wh"                     # a salesman's van, folded
OUTLETS = frozenset({"causeway", "yq roadshow"})
_CODE = re.compile(r" [0-9]+-[0-9]+$")       # the store's code one report writes after its name

# A voucher number: '<prefix>:<series>-<yy>-<mm>-<n>', the series as long as it can be.
_NUMBER = re.compile(r"^([^:]+):(.*)-(\d{2})-(\d{2})-(\d+)$", re.ASCII)


@dataclass(frozen=True)
class Rules:
    """The limits of the pairing; the defaults are the approved plan's. A proof passes others."""
    lag_max: int = 21                        # a receipt is booked from nought to this many days after its issue
    dn_max: int = 8                          # the number gate: numbers this close, within one series-month
    quick_lag: int = 1                       # the lag gate: booked in within this many days
    overlap_min: Fraction = Fraction(1, 2)   # other quantities: the items two vouchers share (Jaccard), at least
    possible_lag_min: int = -7               # a possible match: booked from this lag (before the issue) to lag_max
    grace_working_days: int = 1              # a receipt is due by this working day after the issue
    weekend: tuple = (4,)                    # the days no store works, as date.weekday() counts them (Friday)
    holidays: frozenset = frozenset()        # the dates no store works
    damage_grace_days: int = 7               # to the damage store: due this many days after (stock_damage.GRACE_DAYS)


def _fold(name) -> str:
    return " ".join(str(name or "").split()).casefold()


@dataclass(frozen=True)
class StoreRules:
    """The stores as the owner's config names them: the main store, the damage store, the stores whose
    stock never counts, and the aliases (a spelling one report uses -> the name used everywhere else).
    The caller reads them from config; nothing here does."""
    main: str
    damage: str | None = None
    not_counted: tuple = ()
    aliases: Mapping = field(default_factory=dict, hash=False)

    def __post_init__(self):
        object.__setattr__(self, "not_counted", tuple(self.not_counted or ()))
        table = {_fold(k): str(v) for k, v in (self.aliases or {}).items() if _fold(k) and _fold(v)}
        object.__setattr__(self, "_alias", table)
        main = self.canon(self.main)
        if not main:
            raise ValueError("the main store has no name")
        object.__setattr__(self, "_main", main)
        object.__setattr__(self, "_damage", self.canon(self.damage))
        object.__setattr__(self, "_not_counted", frozenset(c for c in map(self.canon, self.not_counted) if c))

    def canon(self, name) -> str:
        """One spelling of a store as it is compared: through the aliases (a spelling matched with its case
        and spaces folded), then case and runs of spaces folded, then the code a report writes after the
        name (' 10-1') dropped. '' for no name."""
        text = _fold(name)
        if not text:
            return ""
        return _CODE.sub("", _fold(self._alias.get(text, text)))

    def kind(self, name) -> str:
        """'main', 'damage', 'not_counted', 'sim', 'van', 'outlet' or 'other' (KINDS): the first that holds,
        compared after canon. A SIM store ends ' - SIM WH' or is the SIM Warehouse, a van ends ' - Acc WH',
        the outlets are Causeway and YQ Roadshow. No name is 'other'."""
        c = self.canon(name)
        if not c:
            return "other"
        if c == self._main:
            return "main"
        if self._damage and c == self._damage:
            return "damage"
        if c in self._not_counted:
            return "not_counted"
        if c.endswith(SIM_SUFFIX) or c == SIM_STORE:
            return "sim"
        if c.endswith(VAN_SUFFIX):
            return "van"
        if c in OUTLETS:
            return "outlet"
        return "other"


# ── held equal to scripts.ops.ledger ──────────────────────────────────────────

def dec(x) -> Decimal:
    """A quantity or a value as a leg holds it: a Decimal of at most three decimals, rounded half up and
    written the short way (100, 12.5, 0.3). Blank is 0. A float is taken as it is written, never its binary
    tail. ValueError for anything that is not a number. The same as scripts.ops.ledger._dec, line for line."""
    if x is None or x == "":
        return ZERO
    if isinstance(x, float) and x != x:
        return ZERO                           # a blank cell (NaN)
    try:
        if isinstance(x, float):
            # as the float is written, not its binary tail; float() because a numpy float's repr names its type
            number = Decimal(repr(float(x)))
        elif isinstance(x, (Decimal, int)):
            number = Decimal(x)
        else:
            number = Decimal(str(x).replace(",", "").strip())
        if not number.is_finite():
            raise ArithmeticError
        number = number.quantize(Q3, rounding=ROUND_HALF_UP)
    except ArithmeticError:                   # words, infinity, or a figure too long to be one
        raise ValueError("stock ledger: a quantity or a value is not a number") from None
    if not number:
        return ZERO                           # -0.000 as well
    whole = number.to_integral_value()
    return whole if whole == number else number.normalize()


def narr_key(text, mask=None) -> str | None:
    """A transfer narration as a short hash in letters; None when blank. `mask` is the loader's
    mask_personal_ids (None: the text as it is), applied before anything else, so a number typed into a
    narration is hashed in its masked form. Then spaces collapsed, lower case, sha256, the hex digits
    written as letters, the first NARR_LEN. The same as scripts.ops.ledger._narr_key."""
    if text is None or (isinstance(text, float) and text != text):
        return None
    shown = str(text) if mask is None else mask(str(text))
    folded = " ".join(str(shown or "").split()).lower()
    if not folded:
        return None
    return hashlib.sha256(folded.encode("utf-8")).hexdigest().translate(_DIGIT_TO_LETTER)[:NARR_LEN]


# ── voucher numbers ───────────────────────────────────────────────────────────

def parse_number(no) -> tuple:
    """A voucher number as a key with a total order: (0, series folded, yy, mm, n, the number as given) for
    '<prefix>:<series>-<yy>-<mm>-<n>' (SIO:X-26-01-7), else (1, the number as given), after every number
    that reads. Within a series and a month the numbers sort as numbers (2 before 10)."""
    raw = "" if no is None else str(no)
    m = _NUMBER.match(raw)
    if not m or m.end() != len(raw):          # `$` would let one trailing line break through
        return (1, raw)
    _prefix, series, yy, mm, n = m.groups()
    return (0, series.casefold(), int(yy), int(mm), int(n), raw)


def number_distance(a, b) -> int | None:
    """How far apart two voucher numbers are: the difference of their last parts when the series, the
    year and the month are the same (the SIO / STRV prefix aside), else None."""
    ka, kb = parse_number(a), parse_number(b)
    if ka[0] or kb[0] or ka[1:4] != kb[1:4]:
        return None
    return abs(ka[4] - kb[4])


# ── when a receipt is due ─────────────────────────────────────────────────────

def deadline(issue_day: date, dest_kind: str, rules: Rules | None = None) -> date:
    """The day by which the receipt of an issue sent on `issue_day` is due: the first working day strictly
    after it (rules.grace_working_days of them; the weekend and the holidays skipped, so a Thursday's and a
    Friday's issue are due on the Saturday); to the damage store, the issue day plus
    rules.damage_grace_days, as /yq-stock damage counts them. A receipt booked after it is late; whether
    an issue with no receipt is still in grace is within_grace()."""
    rules = rules or Rules()
    if isinstance(issue_day, datetime):
        issue_day = issue_day.date()
    if dest_kind == "damage":
        return issue_day + timedelta(days=rules.damage_grace_days)
    off = frozenset(rules.weekend)
    if all(wd in off for wd in range(7)):
        raise ValueError("the rules leave no working day in the week")
    day, left = issue_day, rules.grace_working_days
    while left > 0:
        day += timedelta(days=1)
        if day.weekday() not in off and day not in rules.holidays:
            left -= 1
    return day


def within_grace(issue_day: date, dest_kind: str | None, data_date: date, rules: Rules | None = None) -> bool:
    """Is an issue sent on `issue_day` with no receipt still in grace when the data runs to `data_date`?
    To a store that works the calendar, while data_date is on or before its deadline (a Thursday's issue is
    still in grace on the Saturday). To the damage store, only while data_date is BEFORE it: /yq-stock
    damage lists a unit sent up to D - damage_grace_days and not booked by D as not booked
    (scripts.ops.stock_damage), and the two say the same of every issue."""
    due = deadline(issue_day, dest_kind, rules)
    if isinstance(data_date, datetime):
        data_date = data_date.date()
    return data_date < due if dest_kind == "damage" else data_date <= due


# ── the legs: whole vouchers, as the pairing reads them ───────────────────────

LEG_KIND = {ISSUE: "issue", RECEIVE: "receipt"}      # the voucher type -> Leg.kind

# Why a voucher is set aside (Aside.reason): the first that holds, in this order.
NO_NUMBER = "no number"                    # its number is blank
MIXED = "mixed"                            # "mixed: date, narration": what differs between its lines
WRONG_WAY = "moves the wrong way"          # units in on an issue, units out on a receipt (or below nought)
NO_STORE = "names no store"                # the store whose stock moved is blank
NO_UNITS = "no units"                      # every item sums to nothing
NO_DESTINATION = "names no destination"    # an issue that says nowhere
OWN_STORE = "sent to its own store"        # an issue to the store it leaves
PART_SCOPE = "partly out of scope"         # some of its items are in scope, some are not
MIXED_FIELDS = ("type", "date", "store", "destination", "narration")
# A receipt set aside by pair(): it books in, at that store, what an issue sent to its own store holds
MIRRORS_OWN = "its issue was sent to its own store"


def _day_of(x) -> date:
    """A row's or an argument's date as a date (a time of day set aside)."""
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    raise ValueError(f"pairing: not a date: {type(x).__name__}")


def _text(x) -> str:
    return "" if x is None else str(x)


@dataclass(frozen=True)
class Leg:
    """One whole transfer voucher, as the pairing reads it.

    no        the Focus number (SIO:... for an issue, STRV:... for a receipt)
    kind      'issue' or 'receipt'
    day       its date
    wh        the store whose stock moved (canon): the sender of an issue, the receiver of a receipt
    to        where an issue was sent (canon); None on a receipt, which never says where it came from
    lines     ((item, units), ...) by item: units summed per item, an item that sums to nothing left
              out. Two legs with equal lines hold the same contents: the signature they pair on
    value     BHD as the export states it on the lines; unvalued: units on lines that state no value
              (scripts.ops.ledger.vouchers' own reading: a receive leg often states none)
    narr      the narration's hash, or None
    order     the leg's place among the legs of its kind, by day and then number: every tie is broken on it
    to_kind   StoreRules.kind of the destination (issues only): it sets the deadline"""
    no: str
    kind: str
    day: date
    wh: str
    to: str | None
    lines: tuple
    value: Decimal
    unvalued: Decimal
    narr: str | None
    order: int
    to_kind: str | None = None

    @property
    def items(self) -> frozenset:
        return frozenset(item for item, _units in self.lines)

    @property
    def units(self) -> Decimal:
        return sum((units for _item, units in self.lines), ZERO)


@dataclass(frozen=True)
class Aside:
    """A voucher that is not paired, and why (one of the reasons above). day, wh, to and narr as on its
    first line (canon; to: issues only), so a window or a store can still find it; lines as a Leg holds
    them (every line of the voucher summed per item, nothing-lines left out). partner: the other leg it is
    set aside with (an issue sent to its own store and the receipt that books it in there), else None."""
    voucher: str
    kind: str
    reason: str
    day: date | None = None
    wh: str = ""
    to: str | None = None
    lines: tuple = ()
    narr: str | None = None
    partner: str | None = None


@dataclass(frozen=True)
class Legs:
    """What legs_from_rows makes of the rows: the issues and the receipts (each in its order), the vouchers
    set aside, how many vouchers held no item in scope, the first and the last date of all the rows given
    (any voucher type: the ledger's own span), and the store rules the names were read with."""
    issues: tuple
    receipts: tuple
    aside: tuple = ()
    out_of_scope: int = 0
    first: date | None = None
    last: date | None = None
    stores: StoreRules | None = None


def legs_from_rows(rows, *, stores: StoreRules, scope: Collection[str] | None) -> Legs:
    """Ledger rows (scripts.ops.ledger: item, date, voucher, vtype, wh, to_wh, qty_in, qty_out, value_in,
    value_out, narr) -> whole vouchers of the two transfer types, as ledger.vouchers groups them. The rows
    are put in one total order first (date, voucher, item, store, then every other field), so their order
    as given never shows. Each store is read through stores.canon.

    scope    the item names that count (scripts.ops.items.accessory_items); None keeps every item (the
             proof option: SIM as well). A voucher with no item in scope counts in out_of_scope and
             nowhere else; one with only some of its items in scope is set aside.
    A voucher is set aside (Aside) when its number is blank, its lines differ in type, date, store,
    destination or narration (the same rule as ledger.vouchers' `mixed`), a line moves the wrong way,
    the store whose stock moved is blank, nothing is left once every item is summed, or an issue names
    no destination or its own store. Items are judged for the scope by the lines that move units (all of
    them when none does)."""
    if not isinstance(stores, StoreRules):
        raise TypeError("legs_from_rows: stores is a StoreRules")
    if isinstance(scope, str):
        raise TypeError("legs_from_rows: scope is a collection of item names, not one name")
    wanted = None if scope is None else frozenset(scope)
    numbers: dict[tuple, Decimal] = {}

    def num(x) -> Decimal:
        """dec(x), worked out once per distinct cell: by type and value, so 1 and 1.0 are two cells."""
        try:
            key = (type(x), x)
            got = numbers.get(key)
        except TypeError:                         # a cell that cannot key a table: dec() says what it is
            return dec(x)
        if got is None:
            got = numbers[key] = dec(x)
        return got

    first = last = None
    shaped = []
    for r in rows:
        day = _day_of(r["date"])
        if first is None or day < first:
            first = day
        if last is None or day > last:
            last = day
        vtype = r["vtype"]
        if vtype not in LEG_KIND:
            continue
        shaped.append((day, _text(r["voucher"]), _text(r["item"]), _text(r["wh"]), vtype, _text(r["to_wh"]),
                       num(r["qty_in"]), num(r["qty_out"]), num(r["value_in"]), num(r["value_out"]),
                       _text(r["narr"])))
    shaped.sort()

    groups: dict[str, list] = {}
    for line in shaped:
        groups.setdefault(line[1], []).append(line)
    names: dict[str, str] = {}

    def canon(spelt: str) -> str:
        if spelt not in names:
            names[spelt] = stores.canon(spelt)
        return names[spelt]

    built: list[tuple] = []
    aside: list[Aside] = []
    out_of_scope = 0
    for number, lines in groups.items():
        head = lines[0]
        kind = LEG_KIND[head[4]]
        totals: dict[str, Decimal] = {}
        value = unvalued = ZERO
        wrong = False
        for _day, _no, item, _wh, vtype, _to, q_in, q_out, v_in, v_out, _narr in lines:
            units, stated = q_in + q_out, v_in + v_out
            totals[item] = totals.get(item, ZERO) + units
            value += stated
            if units != 0 and stated == 0:
                unvalued += units
            if vtype == ISSUE:
                wrong = wrong or q_in != 0 or q_out < 0
            else:
                wrong = wrong or q_out != 0 or q_in < 0
        moved = {item: units for item, units in totals.items() if units != 0}
        held = set(moved) or set(totals)
        if wanted is not None and not held & wanted:
            out_of_scope += 1
            continue
        seen = {"type": {ln[4] for ln in lines}, "date": {ln[0] for ln in lines},
                "store": {canon(ln[3]) for ln in lines},
                "destination": {canon(ln[5]) for ln in lines if ln[4] == ISSUE},
                "narration": {ln[10] for ln in lines}}
        mixed = [word for word in MIXED_FIELDS if len(seen[word]) > 1]
        wh = canon(head[3])
        to = canon(head[5]) if kind == "issue" else None
        if not number:
            reason = NO_NUMBER
        elif mixed:
            reason = f"{MIXED}: {', '.join(mixed)}"
        elif wrong:
            reason = WRONG_WAY
        elif not wh:
            reason = NO_STORE
        elif not moved:
            reason = NO_UNITS
        elif kind == "issue" and not to:
            reason = NO_DESTINATION
        elif kind == "issue" and to == wh:
            reason = OWN_STORE
        elif wanted is not None and held - wanted:
            reason = PART_SCOPE
        else:
            reason = None
        if reason is not None:
            aside.append(Aside(number, kind, reason, head[0], wh, to or None, lines=tuple(sorted(moved.items())),
                               narr=head[10] or None))
            continue
        built.append((kind, head[0], number, wh, to, tuple(sorted(moved.items())), value, unvalued,
                      head[10] or None, stores.kind(head[5]) if kind == "issue" else None))

    def legs_of(kind: str) -> tuple:
        mine = sorted((b for b in built if b[0] == kind), key=lambda b: (b[1], parse_number(b[2])))
        return tuple(Leg(no=no, kind=k, day=day, wh=wh, to=to, lines=lines, value=value, unvalued=unvalued,
                         narr=narr, order=n, to_kind=to_kind)
                     for n, (k, day, no, wh, to, lines, value, unvalued, narr, to_kind) in enumerate(mine))

    aside.sort(key=lambda a: (a.day, parse_number(a.voucher)))
    return Legs(issues=legs_of("issue"), receipts=legs_of("receipt"), aside=tuple(aside),
                out_of_scope=out_of_scope, first=first, last=last, stores=stores)


# ── pairing ───────────────────────────────────────────────────────────────────

# What became of an issue (Pair.outcome, Open.outcome) or of a receipt (ORPHAN).
RECEIVED = "received"                # step A: where it was sent, by its deadline
LATE = "late"                        # step A: where it was sent, after its deadline
ELSEWHERE = "elsewhere"              # step B: the same contents booked in by another store
QTY_DIFFERS = "qty_differs"          # step C: where it was sent, other quantities
IN_GRACE = "in_grace"                # no receipt yet, and the deadline has not passed
NOT_RECEIVED = "not_received"        # no receipt, and the deadline has passed
TWINS = "twins"                      # identical issues to one store, fewer receipts, a deadline passed
ORPHAN = "orphan"                    # a receipt no issue was found for
OUTCOMES = (RECEIVED, LATE, ELSEWHERE, QTY_DIFFERS, IN_GRACE, NOT_RECEIVED, TWINS, ORPHAN)
# Result.counts() and summarise()["counts"]: the issue outcomes count issues (twin_items counts open items),
# orphans count receipts, aside counts vouchers.
COUNT_KEYS = ("received", "late", "elsewhere", "qty_differs", "in_grace", "not_received", "twin_items",
              "twin_slots", "orphans", "aside")
BASES = ("narration", "number", "date")     # what can hold between two legs (Pair.basis), in this order
GATE_RANK = {"narration": 0, "number": 1, "date": 2}
NO_DISTANCE = 99                            # the rank of two numbers with no distance (another series or month)
DIRECTIONS = ("out", "in", "any")           # summarise: issues from the store, to it, or either


@dataclass(frozen=True)
class Pair:
    """An issue and the receipt it was paired with.

    outcome   RECEIVED or LATE (step A), ELSEWHERE (B), QTY_DIFFERS (C)
    lag       days from the issue to the receipt; dn: how far apart the numbers are (None: no distance)
    basis     what held between the two, of BASES: the same narration, numbers within dn_max, booked
              within quick_lag days. Step A pairs on the contents, so its basis can be empty
    received_at   the store that booked it in (canon)
    diffs     step C: ((item, issued, received), ...) over every item of either leg, by item
    twins     the numbers of the identical issues this one could not be told apart from (step A), when
              there are two or more of them; ambiguous: some of those have no receipt, so which receipt
              belongs to which of them is not known"""
    issue: Leg
    receipt: Leg
    outcome: str
    step: str
    lag: int
    dn: int | None
    same_narr: bool
    basis: tuple
    received_at: str
    diffs: tuple = ()
    ambiguous: bool = False
    twins: tuple = ()

    @property
    def back_at_source(self) -> bool:
        """Booked back in by the store that issued it (a step B pair)."""
        return self.receipt.wh == self.issue.wh


@dataclass(frozen=True)
class Open:
    """Issues with no receipt. One issue, or a set of twins: identical issues to one store, `missing` of
    which have no receipt, every one of them named in `issues` (which ones are missing is not known).

    outcome   IN_GRACE while within_grace() holds on the data date (for a set: for every one of its
              issues, since which of them have no receipt is not known), then NOT_RECEIVED (one issue) or
              TWINS (a set)
    deadline  when the receipt is due (deadline()); for a set, the earliest deadline of its issues
    possible  up to three receipts of the same contents, in any store, booked from possible_lag_min to
              lag_max days after the issue (for a set: after one of its issues, the smallest such lag
              counting), that no step could take: by lag, then order
    twins     the numbers of the set (() for one issue)"""
    issues: tuple
    missing: int
    outcome: str
    deadline: date
    possible: tuple = ()
    twins: tuple = ()

    @property
    def store(self) -> str | None:
        """Where the issues were sent (canon)."""
        return self.issues[0].to if self.issues else None


@dataclass(frozen=True)
class Orphan:
    """A receipt no issue was found for. possible: up to three issues of the same contents, to any store,
    with no receipt, the receipt booked from possible_lag_min to lag_max days after them: by lag, then
    order. before_ledger: booked within lag_max days of the ledger's first date, so its issue can be
    older than the ledger."""
    receipt: Leg
    possible: tuple = ()
    before_ledger: bool = False


@dataclass(frozen=True)
class Result:
    """Everything pair() worked out. aside: the vouchers legs_from_rows set aside, with the receipts pair()
    set aside beside them (MIRRORS_OWN, each naming its issue as partner), by day and number.
    by_issue: {issue number: its Pair or its Open}, every issue once; by_receipt: {receipt number: its
    Pair, its Orphan, or its Aside}, every receipt once."""
    data_date: date
    rules: Rules
    legs: Legs = field(repr=False)
    pairs: tuple = field(repr=False)
    open: tuple = field(repr=False)
    orphans: tuple = field(repr=False)
    aside: tuple = field(repr=False)
    out_of_scope: int = 0
    by_issue: dict = field(default_factory=dict, repr=False, hash=False)
    by_receipt: dict = field(default_factory=dict, repr=False, hash=False)

    def counts(self) -> dict:
        """{received, late, elsewhere, qty_differs, in_grace, not_received, twin_items, twin_slots,
        orphans, aside}. Issues: received + late + elsewhere + qty_differs + in_grace + not_received +
        twin_slots. Receipts: received + late + elsewhere + qty_differs + orphans + the receipts set aside
        by pair() (MIRRORS_OWN). aside counts every voucher set aside, of either kind."""
        out = dict.fromkeys(COUNT_KEYS, 0)
        for p in self.pairs:
            out[p.outcome] += 1
        for o in self.open:
            if o.outcome == TWINS:
                out["twin_items"] += 1
                out["twin_slots"] += o.missing
            else:
                out[o.outcome] += o.missing
        out["orphans"] = len(self.orphans)
        out["aside"] = len(self.aside)
        return out


def _relation(no: str, day: date, narr: str | None, r: Leg, rules: Rules) -> tuple[int, int | None, bool, tuple]:
    """(lag, dn, same narration, basis) of an issue (its number, day and narration) and a receipt."""
    lag = (r.day - day).days
    dn = number_distance(no, r.no)
    same = bool(narr) and narr == r.narr
    held = {"narration": same, "number": dn is not None and dn <= rules.dn_max,
            "date": 0 <= lag <= rules.quick_lag}
    return lag, dn, same, tuple(word for word in BASES if held[word])


def _between(i: Leg, r: Leg, rules: Rules) -> tuple[int, int | None, bool, tuple]:
    """(lag, dn, same narration, basis) of an issue and a receipt."""
    return _relation(i.no, i.day, i.narr, r, rules)


def _twin_sets(group: list, reach: Mapping) -> list[tuple]:
    """The twin sets of one step-A group (issues of one signature to one store, in their order): the
    issues that reach the same receipts, directly or through one another, one set per connected part of
    the graph of issue -> the receipts in its window. Only the sets of two issues or more, each a tuple
    of numbers in the issues' order."""
    parent: dict[tuple, tuple] = {}

    def root(x: tuple) -> tuple:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in group:
        for r in reach[i.no]:
            a, b = root(("issue", i.no)), root(("receipt", r.no))
            if a != b:
                parent[max(a, b)] = min(a, b)
    sets: dict[tuple, list[str]] = {}
    for i in group:
        sets.setdefault(root(("issue", i.no)), []).append(i.no)
    return [tuple(members) for members in sets.values() if len(members) >= 2]


def _possible(sources, pool: Mapping, rank: Mapping, rules: Rules, issues_side: bool) -> tuple:
    """Up to three legs of `pool` (by signature) with the signature of the `sources`, booked within the
    possible-match window of one of them: by the smallest such lag, then their order. issues_side: the
    sources are issues and the pool receipts (else the other way round); a lag is always receipt day
    minus issue day."""
    found: dict[str, tuple] = {}
    for src in sources:
        for cand in pool.get(src.lines, ()):
            lag = (cand.day - src.day).days if issues_side else (src.day - cand.day).days
            if rules.possible_lag_min <= lag <= rules.lag_max:
                best = found.get(cand.no)
                if best is None or lag < best[0]:
                    found[cand.no] = (lag, rank[cand.no], cand)
    return tuple(cand for _lag, _rank, cand in sorted(found.values(), key=lambda t: (t[0], t[1]))[:3])


def pair(legs: Legs, *, data_date, rules: Rules = Rules()) -> Result:
    """Pair the issues of `legs` with its receipts (steps A, B, C, the twins and the rest: see the top of
    this module). data_date: the day the data runs to (the ledger's last date): an issue with no receipt
    is IN_GRACE while it is on or before the issue's deadline. ValueError when two legs of one kind share
    a number or a leg is filed under the other kind."""
    if not isinstance(legs, Legs):
        raise TypeError("pair: legs is what legs_from_rows gives")
    if not isinstance(rules, Rules):
        raise TypeError("pair: rules is a Rules")
    data_date = _day_of(data_date)
    issues = sorted(legs.issues, key=lambda leg: (leg.order, leg.day, parse_number(leg.no)))
    receipts = sorted(legs.receipts, key=lambda leg: (leg.order, leg.day, parse_number(leg.no)))
    for kind, mine in (("issue", issues), ("receipt", receipts)):
        if any(leg.kind != kind for leg in mine):
            raise ValueError(f"pair: a leg filed with the {kind}s is not an {kind}" if kind == "issue"
                             else f"pair: a leg filed with the {kind}s is not a {kind}")
        if len({leg.no for leg in mine}) != len(mine):
            raise ValueError(f"pair: two {kind} legs share a number")
    irank = {leg.no: n for n, leg in enumerate(issues)}
    rrank = {leg.no: n for n, leg in enumerate(receipts)}
    issue_of = {leg.no: leg for leg in issues}
    match: dict[str, tuple[Leg, str]] = {}        # issue number -> (its receipt, the step)
    took: set[str] = set()                         # receipt numbers already paired
    twins_of: dict[str, tuple] = {}                # issue number -> the issue numbers of its twin set

    # A: the same signature, received where it was sent, the earliest free receipt in the window
    by_dest: dict[tuple, list[Leg]] = {}
    for r in receipts:
        by_dest.setdefault((r.lines, r.wh), []).append(r)
    groups: dict[tuple, list[Leg]] = {}
    for i in issues:
        groups.setdefault((i.lines, i.to), []).append(i)
    for key, group in groups.items():
        cands = by_dest.get(key, ())
        reach = {i.no: [r for r in cands if 0 <= (r.day - i.day).days <= rules.lag_max] for i in group}
        for i in group:
            for r in reach[i.no]:                  # the receipts in their order: the first free is the earliest
                if r.no not in took:
                    match[i.no] = (r, "A")
                    took.add(r.no)
                    break
        for members in _twin_sets(group, reach):
            for no in members:
                twins_of[no] = members

    def gate(i: Leg, r: Leg) -> tuple | None:
        """The rank of a candidate when it is in the window and the gate holds, else None."""
        lag, dn, _same, basis = _between(i, r, rules)
        if not 0 <= lag <= rules.lag_max or not basis:
            return None
        return (GATE_RANK[basis[0]], NO_DISTANCE if dn is None else dn, lag, irank[i.no], rrank[r.no])

    def take(edges: list, step: str) -> None:
        for _key, i, r in sorted(edges, key=lambda e: e[0]):
            if i.no not in match and r.no not in took:
                match[i.no] = (r, step)
                took.add(r.no)

    # B: the same signature, booked in by another store
    free_by_sig: dict[tuple, list[Leg]] = {}
    for r in receipts:
        if r.no not in took:
            free_by_sig.setdefault(r.lines, []).append(r)
    edges = []
    for i in issues:
        if i.no in match:
            continue
        for r in free_by_sig.get(i.lines, ()):
            if r.wh != i.to:
                key = gate(i, r)
                if key is not None:
                    edges.append((key, i, r))
    take(edges, "B")

    # the exchange: a twin set still short of receipts, one of whose step-A members has a free receipt of the
    # same contents in another store that the gate lets it take, while a member with none could take that
    # member's receipt (another store booked in a transfer meant for this one and the goods were issued again):
    # the member is received elsewhere and the other takes its receipt. The oldest set first, its earliest
    # members first, until the set has no such exchange left.
    def exchange(members: tuple) -> bool:
        waiting = [issue_of[no] for no in members if no not in match]
        if not waiting:
            return False
        for no in members:
            got = match.get(no)
            if got is None or got[1] != "A":
                continue
            m, kept = issue_of[no], got[0]
            takers = [u for u in waiting if 0 <= (kept.day - u.day).days <= rules.lag_max]
            if not takers:
                continue
            best = None
            for r in free_by_sig.get(m.lines, ()):
                if r.no in took or r.wh == m.to:
                    continue
                key = gate(m, r)
                if key is not None and (best is None or key < best[0]):
                    best = (key, r)
            if best is None:
                continue
            match[m.no] = (best[1], "B")
            took.add(best[1].no)
            match[takers[0].no] = (kept, "A")
            return True
        return False

    short_sets = {members for members in twins_of.values() if any(no not in match for no in members)}
    for members in sorted(short_sets, key=lambda members: irank[members[0]]):
        while exchange(members):
            pass

    # C: other quantities, booked in where it was sent
    free_by_wh: dict[str, list[Leg]] = {}
    for r in receipts:
        if r.no not in took:
            free_by_wh.setdefault(r.wh, []).append(r)
    edges = []
    for i in issues:
        if i.no in match:
            continue
        for r in free_by_wh.get(i.to, ()):
            key = gate(i, r)
            if key is None:
                continue
            overlap = Fraction(len(i.items & r.items), len(i.items | r.items))
            if overlap >= rules.overlap_min:
                edges.append(((-overlap,) + key, i, r))
    take(edges, "C")

    # a receipt that books in, at that store, what an issue sent to its own store holds: set aside with that issue
    # (the gate holding, the nearest number first, then the nearest day), never left as a receipt with no issue
    mirrors: dict[str, Aside] = {}                 # receipt number -> its Aside
    partner_of: dict[str, str] = {}                # the issue's number -> the receipt's
    free_at: dict[tuple, list[Leg]] = {}
    for r in receipts:
        if r.no not in took:
            free_at.setdefault((r.lines, r.wh), []).append(r)
    for a in legs.aside:
        if a.kind != "issue" or a.reason != OWN_STORE or not a.lines or a.day is None:
            continue
        best = None
        for r in free_at.get((a.lines, a.wh), ()):
            if r.no in mirrors:
                continue
            lag, dn, _same, basis = _relation(a.voucher, a.day, a.narr, r, rules)
            if 0 <= lag <= rules.lag_max and basis:
                key = (dn is None, 0 if dn is None else dn, lag, rrank[r.no])
                if best is None or key < best[0]:
                    best = (key, r)
        if best is not None:
            r = best[1]
            mirrors[r.no] = Aside(r.no, "receipt", MIRRORS_OWN, r.day, r.wh, None, lines=r.lines, narr=r.narr,
                                  partner=a.voucher)
            partner_of[a.voucher] = r.no

    # the twin sets that still miss a receipt; the pairs among them are ambiguous
    short = {members for members in twins_of.values() if any(no not in match for no in members)}
    pairs: list[Pair] = []
    for i in issues:
        got = match.get(i.no)
        if got is None:
            continue
        r, step = got
        lag, dn, same, basis = _between(i, r, rules)
        diffs: tuple = ()
        if step == "A":
            outcome = LATE if r.day > deadline(i.day, i.to_kind, rules) else RECEIVED
        elif step == "B":
            outcome = ELSEWHERE
        else:
            outcome = QTY_DIFFERS
            sent, booked = dict(i.lines), dict(r.lines)
            diffs = tuple((item, sent.get(item, ZERO), booked.get(item, ZERO))
                          for item in sorted(set(sent) | set(booked)))
        members = twins_of.get(i.no, ())
        pairs.append(Pair(issue=i, receipt=r, outcome=outcome, step=step, lag=lag, dn=dn, same_narr=same,
                          basis=basis, received_at=r.wh, diffs=diffs, ambiguous=members in short, twins=members))

    # the rest: the issues with no receipt, the receipts with no issue
    left_r: dict[tuple, list[Leg]] = {}
    for r in receipts:
        if r.no not in took and r.no not in mirrors:
            left_r.setdefault(r.lines, []).append(r)
    opens: list[Open] = []
    told: set[tuple] = set()
    for i in issues:
        if i.no in match:
            continue
        members = twins_of.get(i.no)
        if members is None:
            inside = within_grace(i.day, i.to_kind, data_date, rules)
            opens.append(Open(issues=(i,), missing=1, outcome=IN_GRACE if inside else NOT_RECEIVED,
                              deadline=deadline(i.day, i.to_kind, rules),
                              possible=_possible((i,), left_r, rrank, rules, True)))
            continue
        if members in told:
            continue
        told.add(members)
        # which members have no receipt is never claimed: the set is in grace only while every one of them is
        in_set = tuple(issue_of[no] for no in members)
        inside = all(within_grace(leg.day, leg.to_kind, data_date, rules) for leg in in_set)
        opens.append(Open(issues=in_set, missing=sum(1 for leg in in_set if leg.no not in match),
                          outcome=IN_GRACE if inside else TWINS,
                          deadline=min(deadline(leg.day, leg.to_kind, rules) for leg in in_set),
                          possible=_possible(in_set, left_r, rrank, rules, True), twins=members))

    left_i: dict[tuple, list[Leg]] = {}
    for i in issues:
        if i.no not in match:
            left_i.setdefault(i.lines, []).append(i)
    days = [leg.day for leg in issues + receipts]
    first = legs.first if legs.first is not None else (min(days) if days else None)
    orphans = tuple(Orphan(receipt=r, possible=_possible((r,), left_i, irank, rules, False),
                           before_ledger=first is not None and (r.day - first).days < rules.lag_max)
                    for r in receipts if r.no not in took and r.no not in mirrors)
    aside = [replace(a, partner=partner_of[a.voucher]) if a.voucher in partner_of else a for a in legs.aside]
    aside = tuple(sorted(aside + list(mirrors.values()),
                         key=lambda a: (a.day is None, a.day or date.min, parse_number(a.voucher))))

    by_issue: dict[str, object] = {p.issue.no: p for p in pairs}
    for o in opens:
        for leg in o.issues:
            if leg.no not in match:
                by_issue[leg.no] = o
    by_receipt: dict[str, object] = {p.receipt.no: p for p in pairs}
    by_receipt.update((o.receipt.no, o) for o in orphans)
    by_receipt.update(mirrors)
    return Result(data_date=data_date, rules=rules, legs=legs, pairs=tuple(pairs), open=tuple(opens),
                  orphans=orphans, aside=aside, out_of_scope=legs.out_of_scope,
                  by_issue={i.no: by_issue[i.no] for i in issues},
                  by_receipt={r.no: by_receipt[r.no] for r in receipts})


# ── a window of it ────────────────────────────────────────────────────────────

def summarise(result: Result, *, first=None, last=None, store=None, direction: str = "out") -> dict:
    """The Result over the issues dated `first` to `last` (both included; None: no limit), for one store
    or all (store=None):

        {first, last, store (canon or None), direction,
         counts:   {received, late, elsewhere, qty_differs, in_grace, not_received, twin_items, twin_slots,
                    orphans, aside}, as Result.counts() counts them,
         outcomes: {outcome: {vouchers, lines, units}} for every outcome of OUTCOMES (units as issued;
                   an orphan's as received),
         issues:   {vouchers, lines, units} of every issue in the window}

    direction  'out': the issues the store sent; 'in': the issues sent to it; 'any': either. A receipt
               with no issue counts by the store that booked it in, so it counts for 'in' and 'any' only.
               A voucher set aside counts by its day, and by its store ('out') or its destination ('in';
               a receipt by its store).
    The store is read through the Result's store rules (as legs_from_rows read every store)."""
    if direction not in DIRECTIONS:
        raise ValueError(f"summarise: direction is one of {', '.join(DIRECTIONS)}, not {direction!r}")
    lo = None if first is None else _day_of(first)
    hi = None if last is None else _day_of(last)
    if lo is not None and hi is not None and hi < lo:
        raise ValueError("summarise: the last day is before the first")
    want = None
    if store is not None:
        stores = result.legs.stores
        want = stores.canon(store) if stores is not None else _fold(store)
        if not want:
            raise ValueError("summarise: the store has no name")

    def inside(day) -> bool:
        return day is not None and (lo is None or day >= lo) and (hi is None or day <= hi)

    def sends(wh, to, kind="issue") -> bool:
        if want is None:
            return True
        sent_from = kind == "issue" and wh == want
        sent_to = (to == want) if kind == "issue" else (wh == want)
        return {"out": sent_from, "in": sent_to, "any": sent_from or sent_to}[direction]

    counts = dict.fromkeys(COUNT_KEYS, 0)
    outcomes = {o: {"vouchers": 0, "lines": 0, "units": ZERO} for o in OUTCOMES}
    issued = {"vouchers": 0, "lines": 0, "units": ZERO}
    twin_sets: set[tuple] = set()

    def add(block: dict, leg: Leg) -> None:
        block["vouchers"] += 1
        block["lines"] += len(leg.lines)
        block["units"] += leg.units

    for i in result.legs.issues:
        if not (inside(i.day) and sends(i.wh, i.to)):
            continue
        got = result.by_issue[i.no]
        add(outcomes[got.outcome], i)
        add(issued, i)
        if isinstance(got, Pair) or got.outcome in (IN_GRACE, NOT_RECEIVED):
            counts[got.outcome] += 1
        else:
            counts["twin_slots"] += 1
            twin_sets.add(got.twins)
    counts["twin_items"] = len(twin_sets)
    for o in result.orphans:
        r = o.receipt
        if inside(r.day) and (want is None or (direction != "out" and r.wh == want)):
            counts["orphans"] += 1
            add(outcomes[ORPHAN], r)
    counts["aside"] = sum(1 for a in result.aside if inside(a.day) and sends(a.wh, a.to, a.kind))
    return {"first": lo, "last": hi, "store": want, "direction": direction, "counts": counts,
            "outcomes": outcomes, "issues": issued}
