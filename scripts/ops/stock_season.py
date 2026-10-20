"""The arithmetic of the order rule's second release (the Stock & Order Plan, the owner's decisions of
20-Oct-2026): the order rhythm and the season, a longer history for an item that was out of stock most of
the window, and the lead time per supplier. PURE: items, flows and numbers in, numbers out. Nothing is read,
written or printed here; scripts.ops.stock gathers the reads and calls these functions, and the workbook
works the same sums out again from what they give (one rounding rule: round6 first, then a whole unit up,
scripts.ops.stock_plan). Quantities are Decimals, never floats; day counts are whole numbers.

THE ORDER RULE, with the rhythm and the season. An order goes to one supplier about once a month (R days, the
supplier's own setting, 30 by default; the caller passes it). An item JOINS the supplier's order when its
demand from the data date D would use up more than its position (the main store's units on hand plus the
goods on the way) within the trigger days = lead time + R + the low end of the KPI band (30); it is TOPPED UP
to cover the fill days = lead time + R + the high end of the band (45). Demand is the item's daily rate
times the season's days. With no season a day is a day, and the rule is today's (rate x days - position);
with a season every day after D counts for its month's lift (season_days).

  * The lift of a month = the company's units a day in that month last year, over the company's units a day
    in the RATE_DAYS (90) days to D (the base). Company units = every item's sales, every warehouse, a day's
    total never below nought. "That month last year" is the latest occurrence of that calendar month in the
    twelve months that end with D's month, so each month number has exactly one: for D = 20-Oct-2026, Nov and
    Dec are those of 2025, Jan to Sep those of 2026, and Oct is October 2026 up to D (a partial month: its
    lift stands for October 2027, and for the rest of this month it is this month's own pace). Only the days
    of that month from the ledger's first day to D count (a ledger that starts mid-month, a month still
    running); with fewer than LIFT_MIN_DAYS (15) of them, or with no sales in the base window at all, a month
    has no lift of its own: 1.00, basis "no data" (no earlier year is tried). If the ledger starts inside the
    base window, the base divides by the days it covers. Two decimals, half up. A lift is never nought or
    below: a month that sold nothing, or that rounds to 0.00, is 0.01 (basis "last year").
    The owner's settings win: config lifts (month number -> number above nought) give basis "config".
    A lift is measured against today's base, so the business's growth since that month is inside it: a month
    that was seasonally ordinary last year reads under 1.00 when sales have grown since.
  * The long rate. The daily rate of scripts.ops.stock divides the units sold in the RATE_DAYS (90) days to D
    by the days the main store held the item at the day's end, but by no fewer than RATE_MIN_DAYS (30): an
    item in stock on fewer than 30 of those days has a rate that can understate its demand (the star on the
    screens). long_rate() reaches back one day at a time, up to a year, until the window holds 30 days in
    stock, and divides the units sold in the whole window (every warehouse, returns netted, never below
    nought) by those 30 days. It answers None when the 90 days already hold 30 days in stock (the plain rate
    stands) and when even a year holds fewer (the floor rule stands).
  * Availability. The share of item-days (the given items, the given days) on which the main store held the
    item at the day's end. A month in which it is under AVAILABILITY_WARN (0.80) may have a lift held down by
    stock-outs (a shelf that is empty cannot sell); the caller words that warning, nothing here decides it.
  * The lead time per supplier. A supplier whose supplier invoices have at least three matched receipts
    (scripts.ops.stock.measure_lead_times; one row per invoice) takes the median of its newest ten (round half
    up to whole days, exactly as scripts.ops.stock.lead_time), basis "measured". Otherwise its configured lead
    (config, days above nought), basis "config"; otherwise the overall measured lead, basis "overall"; otherwise
    the setting (config 'stock.lead_time_days'), basis "setting". Any other supplier ("*") takes the last two.
    A supplier is named as scripts.ops.stock_plan names it: spaces collapsed, then the owner's aliases (the
    spelling compared folded: case and spaces ignored).

CONTRACT (scripts.ops.stock and the workbook rely on it)
  company_units(items, first, last) -> Decimal
      items   any iterable of stock.Item (it.sales {date: units sold, every warehouse})
      the units sold on the days first..last (both included); a day's total below nought counts as nought
  measure_lifts(items, d, first_day, overrides=None) -> {month number 1..12: lift row}
      d          the data date; first_day  the ledger's first day (None: no limit)
      overrides  {month number (int or "1".."12"): number above nought}; any other entry is ignored
    lift row {"lift": Decimal (two decimals, above nought), "basis": "last year" | "no data" | "config",
              "days": int (days of the month between first_day and d), "year": int (the year the lift was
              measured on: the year of the occurrence above; an override leaves days and year as measured)}
  lift_of(lifts, day) -> Decimal            the lift of that day's month; 1.00 when missing, nought or not a number
      lifts   the dict above, or a plain {month number: number}; None is no season
  season_days(start, n, lifts) -> Decimal   the sum of lift_of(start + k days) for k = 1..n: the days AFTER start
  joins(position, rate6, start, trig_days, lifts) -> bool
      round6(position - rate6 x season_days(start, trig_days, lifts)) < 0; False when rate6 is nought or below
  suggested(position, rate6, start, fill_days, lifts) -> Decimal
      units_up(rate6 x season_days(start, fill_days, lifts) - position); 0 when rate6 is nought or below
      (position = max(on hand, 0) + on order; rate6 = the daily rate to six decimals, stock_plan.round6)
  seasonal_cover(position, rate, start, lifts, cap=COVER_CAP) -> int | None
      the first day count k (1 to cap) at which rate x the lifts of the days start+1..start+k reaches
      position (compared to six decimals, as joins does); None when rate is nought or below; 0 when position
      is nought or below (nothing to cover); cap when it is never reached
  long_rate(it, flow, d, rate_days=90, min_days=30, max_days=365) -> dict | None
      it   stock.Item; flow  stockflow.Flow of the item (None: never in stock)
    {"rate": Decimal (nought when the window sold nothing), "first": date, "last": d,
     "in_stock_days": int (= min_days), "sold": Decimal}
  availability(items, flows, first, last) -> Decimal | None
      flows   stockflow.Flows (or any {ledger name: Flow} dict); an item with no flow was never in stock
      None for no items or no days
  lead_by_supplier(measured, vendor_of_invoice, configured, overall, default, aliases=None) -> {name: lead row}
      measured           scripts.ops.stock.measure_lead_times rows, oldest receipt first
      vendor_of_invoice  {supplier invoice number: supplier}      configured  {supplier: days}
      overall            scripts.ops.stock.lead_time(measured, default)      default  the setting, in days
      aliases            {a supplier's spelling: its one name}
    lead row {"days": int, "basis": "measured" | "config" | "overall" | "setting",
              "shown": [the newest <= 10 matched rows of that supplier, oldest first] (basis "measured"
              only, else []), "matched": int (that supplier's matched invoices), "default": int}
    One row per supplier seen in vendor_of_invoice or configured (by name, in alphabetical order), and a row
    "*" last for any other supplier (matched 0).
  Constants: RATE_DAYS, RATE_MIN_DAYS (the plain rate's, equal to scripts.ops.stock's), LONG_MAX_DAYS, LIFT_MIN_DAYS,
  COVER_CAP, AVAILABILITY_WARN, OTHER ("*"), LIFT_BASES, LEAD_BASES.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from scripts.ops import stock_plan      # the one rounding rule: stock_plan.round6, then stock_plan.units_up

ZERO = Decimal(0)
Q2 = Decimal("0.01")
NEUTRAL = Decimal("1.00")             # a lift that changes nothing
SMALLEST = Decimal("0.01")            # no lift is ever nought or below
OTHER = "*"                           # the lead row of any supplier nothing names

RATE_DAYS = 90                        # the base window = stock.RATE_DAYS (a test holds the two together)
RATE_MIN_DAYS = 30                    # the plain rate's floor = stock.RATE_MIN_DAYS (a test holds the two together)
LONG_MAX_DAYS = 365                   # a longer history never reaches back further than a year
LIFT_MIN_DAYS = 15                    # a month with fewer days of data than this has no lift of its own
COVER_CAP = 730                       # seasonal_cover counts at most this many days
AVAILABILITY_WARN = Decimal("0.80")   # the caller warns when a month's availability is under this

LIFT_BASES = ("last year", "no data", "config")
LEAD_BASES = ("measured", "config", "overall", "setting")


# ── small helpers ─────────────────────────────────────────────────────────────

def _stock():
    """scripts.ops.stock, imported when used: it imports this module."""
    from scripts.ops import stock
    return stock


def _dec(x) -> Decimal:
    """A number as a Decimal: a float goes through its text, so 1.1 is 1.1."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _clean(text) -> str:
    """A name with its spaces collapsed ('' for nothing)."""
    return " ".join(str(text or "").split())


def _month_end(day: date) -> date:
    """The last day of day's month."""
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1) - timedelta(days=1)


def _span(first: date, last: date) -> int:
    """The days first..last, both included (nought when last is before first)."""
    return max((last - first).days + 1, 0)


def _entry(lifts, month: int):
    """The lifts' entry of a month number (a key may be a whole number or its text); None when missing."""
    if not lifts:
        return None
    return lifts[month] if month in lifts else lifts.get(str(month))


def _lift(entry) -> Decimal:
    """A lift row (or a plain number) as the lift it holds; 1.00 for anything that is no number above nought."""
    if isinstance(entry, Mapping):
        entry = entry.get("lift")
    if entry is None:
        return NEUTRAL
    try:
        value = _dec(entry)
    except ArithmeticError:
        return NEUTRAL
    return value if value.is_finite() and value > 0 else NEUTRAL


def _table(lifts) -> dict[int, Decimal]:
    """{month number 1..12: lift}: what lift_of would answer for each."""
    return {m: _lift(_entry(lifts, m)) for m in range(1, 13)}


def _at_least_smallest(lift: Decimal) -> Decimal:
    return lift if lift > 0 else SMALLEST


# ── the season ────────────────────────────────────────────────────────────────

def _daily(items, first: date, last: date) -> dict[date, Decimal]:
    """{day: units the items sold together that day, returns netted} for the days first..last on which any
    item has a line."""
    out: dict[date, Decimal] = {}
    for it in items:
        for day, q in it.sales.items():
            if first <= day <= last:
                out[day] = out.get(day, ZERO) + q
    return out


def _units(daily: dict, first: date, last: date) -> Decimal:
    return sum((v for day, v in daily.items() if first <= day <= last and v > 0), ZERO)


def company_units(items, first: date, last: date) -> Decimal:
    """The units the items sold on the days first..last (both included), every warehouse; a day whose total is
    below nought (more returned than sold) counts as nought."""
    return _units(_daily(items, first, last), first, last)


def _source_month(d: date, m: int) -> date:
    """The first day of the month the lift of month number m is measured on: the occurrence of m among the
    twelve calendar months that end with d's month (d's own month, up to d, included)."""
    return date(d.year if m <= d.month else d.year - 1, m, 1)


def _overrides(overrides) -> dict[int, Decimal]:
    """{month number: lift} from the owner's settings; an entry that is not a month 1..12 with a number above
    nought is passed over."""
    out: dict[int, Decimal] = {}
    for key, value in (overrides or {}).items():
        try:
            m = int(str(key).strip())
            lift = _dec(value)
            if not 1 <= m <= 12 or not lift.is_finite() or lift <= 0:
                continue
            out[m] = _at_least_smallest(lift.quantize(Q2, rounding=ROUND_HALF_UP))
        except (ArithmeticError, ValueError):
            continue
    return out


def measure_lifts(items, d: date, first_day: date | None, overrides=None) -> dict[int, dict]:
    """The lift of each month number 1..12 (see the module docstring): {"lift", "basis", "days", "year"}."""
    window_first = d - timedelta(days=RATE_DAYS - 1)
    base_first = window_first if first_day is None else max(window_first, first_day)
    base_days = _span(base_first, d)
    sources = {m: _source_month(d, m) for m in range(1, 13)}
    daily = _daily(items, min([window_first] + list(sources.values())), d)
    base_units = _units(daily, base_first, d) if base_days else ZERO
    fixed = _overrides(overrides)

    out: dict[int, dict] = {}
    for m in range(1, 13):
        first = sources[m]
        a = first if first_day is None else max(first, first_day)
        b = min(_month_end(first), d)
        n = _span(a, b)
        if base_units > 0 and n >= LIFT_MIN_DAYS:
            # (units / n) / (base_units / base_days), as one division so a half is never lost in between
            lift = (_units(daily, a, b) * base_days / (Decimal(n) * base_units)).quantize(Q2, rounding=ROUND_HALF_UP)
            lift, basis = _at_least_smallest(lift), "last year"
        else:
            lift, basis = NEUTRAL, "no data"
        if m in fixed:
            lift, basis = fixed[m], "config"
        out[m] = {"lift": lift, "basis": basis, "days": n, "year": first.year}
    return out


def lift_of(lifts, day: date) -> Decimal:
    """The lift of the month `day` is in; 1.00 when the lifts do not hold it (or hold nought or no number)."""
    return _lift(_entry(lifts, day.month))


def season_days(start: date, n: int, lifts) -> Decimal:
    """The lift-weighted days after `start`: the sum of lift_of(start + k days) for k = 1..n. Exact. Worked
    month by month (a month's days all have its lift), so it costs a dozen steps, not a year of them."""
    table = _table(lifts)
    total = ZERO
    left = int(n)
    day = start + timedelta(days=1)
    while left > 0:
        take = min(left, (_month_end(day) - day).days + 1)
        total += table[day.month] * take
        day += timedelta(days=take)
        left -= take
    return total


# ── the order rule, with the season ───────────────────────────────────────────

def joins(position, rate6, start: date, trig_days: int, lifts) -> bool:
    """True when the item's demand over the trigger days (rate6 x the season's days after `start`) is more than
    its position, to six decimals (stock_plan's one rounding rule). No rate, no join."""
    rate6 = _dec(rate6)
    if rate6 <= 0:
        return False
    return stock_plan.round6(_dec(position) - rate6 * season_days(start, trig_days, lifts)) < 0


def suggested(position, rate6, start: date, fill_days: int, lifts) -> Decimal:
    """The units that bring the position up to the demand over the fill days, in whole units (round6 first,
    then up: stock_plan.units_up). No rate, no units."""
    rate6 = _dec(rate6)
    if rate6 <= 0:
        return ZERO
    return stock_plan.units_up(rate6 * season_days(start, fill_days, lifts) - _dec(position))


def seasonal_cover(position, rate, start: date, lifts, cap: int = COVER_CAP) -> int | None:
    """The days the position lasts at the season's pace: the first k (1 to cap) at which rate x the lifts of the
    days start+1..start+k reaches position (to six decimals, so a third of a unit a day never misses a unit by
    the last digit). None when there is no rate, 0 when there is nothing to cover, cap when it never reaches."""
    rate = _dec(rate)
    if rate <= 0:
        return None
    position = _dec(position)
    if position <= 0:
        return 0
    table = _table(lifts)
    total = ZERO
    day = start
    for k in range(1, int(cap) + 1):
        day += timedelta(days=1)
        total += rate * table[day.month]
        if stock_plan.round6(position - total) <= 0:
            return k
    return max(int(cap), 0)


# ── a longer history ──────────────────────────────────────────────────────────

def long_rate(it, flow, d: date, rate_days: int = RATE_DAYS, min_days: int = RATE_MIN_DAYS,
              max_days: int = LONG_MAX_DAYS) -> dict | None:
    """The rate over a longer window for an item in stock on fewer than min_days of the rate_days to d (see the
    module docstring); None when the plain window holds enough, or when max_days do not either."""
    def in_stock(day: date) -> bool:
        return flow is not None and flow.main_at(day) > 0

    first = d - timedelta(days=rate_days - 1)
    held = sum(1 for k in range(rate_days) if in_stock(first + timedelta(days=k)))
    if held >= min_days:
        return None
    length = rate_days
    while length < max_days:
        length += 1
        first = d - timedelta(days=length - 1)
        if in_stock(first):
            held += 1
        if held >= min_days:
            sold = max(sum((q for day, q in it.sales.items() if first <= day <= d), ZERO), ZERO)
            return {"rate": sold / held, "first": first, "last": d, "in_stock_days": held, "sold": sold}
    return None


def availability(items, flows, first: date, last: date) -> Decimal | None:
    """The share of item-days, first..last (both included), on which the main store held the item at the day's
    end; None for no items or no days. The caller warns below AVAILABILITY_WARN."""
    items = list(items)
    n = _span(first, last)
    if not items or not n:
        return None
    days = [first + timedelta(days=k) for k in range(n)]
    held = 0
    for it in items:
        flow = flows.get(it.ledger_name) if flows is not None else None
        if flow is not None:
            held += sum(1 for day in days if flow.main_at(day) > 0)
    return Decimal(held) / Decimal(len(items) * n)


# ── the lead time per supplier ────────────────────────────────────────────────

def _days(value) -> int | None:
    """A configured lead in whole days (half up); None for anything that is no number above nought."""
    try:
        v = _dec(value)
        whole = int(v.quantize(Decimal(1), rounding=ROUND_HALF_UP)) if v.is_finite() else 0
    except (ArithmeticError, ValueError):
        return None
    return whole if whole > 0 else None


def _oldest_first(row: dict) -> tuple:
    return (row.get("receipt_date") or date.min, row.get("invoice_date") or date.min, str(row.get("invoice") or ""))


def lead_by_supplier(measured, vendor_of_invoice, configured, overall, default, aliases=None) -> dict[str, dict]:
    """The lead time of each supplier (see the module docstring) and of any other ("*")."""
    st = _stock()
    fold = st.fold
    alias = {fold(k): _clean(v) for k, v in (aliases or {}).items() if fold(k) and _clean(v)}

    def named(text) -> str:
        name = _clean(text)
        return alias.get(fold(name), name) if name else ""

    owner: dict[str, str] = {}                      # a supplier invoice number (folded) -> its supplier
    for number, vendor in (vendor_of_invoice or {}).items():
        key, name = fold(number), named(vendor)
        if key and name:
            owner[key] = name
    set_lead: dict[str, int] = {}                   # supplier -> its configured lead
    seen = set(owner.values())
    for supplier, value in (configured or {}).items():
        name = named(supplier)
        if name:
            seen.add(name)
            days = _days(value)
            if days is not None:
                set_lead[name] = days
    matched: dict[str, list[dict]] = {}
    for row in measured or []:
        name = owner.get(fold(row.get("invoice")))
        if name:
            matched.setdefault(name, []).append(row)

    setting = int(default)
    if overall and overall.get("basis") == "measured":
        beyond = (int(overall["days"]), "overall")
    else:
        beyond = (setting, "setting")

    def lead_row(days: int, basis: str, shown: list, count: int) -> dict:
        return {"days": int(days), "basis": basis, "shown": shown, "matched": count, "default": setting}

    out: dict[str, dict] = {}
    for name in sorted(seen, key=lambda s: (s.casefold(), s)):
        own = sorted(matched.get(name, []), key=_oldest_first)
        measured_here = st.lead_time(own, setting)
        if measured_here["basis"] == "measured":
            out[name] = lead_row(measured_here["days"], "measured", measured_here["shown"], len(own))
        elif name in set_lead:
            out[name] = lead_row(set_lead[name], "config", [], len(own))
        else:
            out[name] = lead_row(beyond[0], beyond[1], [], len(own))
    out[OTHER] = lead_row(beyond[0], beyond[1], [], 0)
    return out
