"""The two legs of a stock transfer: the rules and the helpers that pairing them stands on.

A transfer in Focus is two vouchers that never point at each other: a Stock Issue Voucher, where stock
leaves one store for another, and a Stock Receive Voucher, where the receiving store books it in. Which
receipt belongs to which issue is worked out from what they hold (items and units, the destination, the
narration's hash, the voucher numbers, the days between them), never from the numbers alone. The steps
that pair whole vouchers come with /yq-moves and stand on what is here, so that /yq-moves and the
portal's Stock Moves page apply one rule:

  Rules         the limits of the pairing, one frozen value: the lag, the number and lag gates, the overlap
                of other quantities, the window of a possible match, the grace before a receipt is late
  StoreRules    one name per store (canon) and what kind of store it is (kind)
  dec, narr_key   held equal to scripts.ops.ledger's own (_dec, _narr_key): a leg shaped from the export
                  and a leg shaped here (from the database, in the portal) compare equal
  parse_number, number_distance   a voucher number as a key that sorts, and how far apart two numbers are
  deadline      the day by which an issue's receipt is due

PURE. The standard library only: no scripts.* import, no database, no config read (the caller passes the
stores and the rules in), no clock. The same arguments give the same answer, every time.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    rules.damage_grace_days, as /yq-stock damage counts them."""
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
