"""Damage stock: what the damage store holds, how much is sent to it for every 100 units sold and
whether that is rising, what was sent and never booked in, and the items it happens to most.
READ-ONLY and pure: scripts.ops.stock gathers the reads and calls damage_facts(model, fl); this module
works the figures out and words them. Every line it returns passes fmt.careful (stock.run guards them
again); it never names a person or a van: a line marked "to check" says what differs, never who.

CONTRACT (scripts.ops.stock and the workbook rely on it)
  damage_facts(model, fl) -> dict
      model  the stock Model (model.items, model.d, model.cfg.damage, model.facts["fast"]["rates"])
      fl     stockflow.Flows for the same items
    {"set_up": bool,                 # False when config 'warehouses.damage' names no store
     "store": str | None,            # the damage store's one name
     "found": bool,                  # the ledger moved stock in it at least once
     "now": {"units", "value", "uncosted_units", "items", "share_of_main"},   # Decimal; share a fraction or None
     "months": [{"month": date (the 1st), "sold", "sent", "sent_main", "sent_elsewhere", "booked",
                 "per100": Decimal | None, "partial": bool}],   # the last 12 months, oldest first; the newest
                                                                 # is the month of D (partial when D is no month end)
     "trend": {"word": "rising" | "falling" | "steady" | None, "this": Decimal | None,
               "prior3": Decimal | None, "why_none": str | None},
     "not_booked": [{"item": Item, "units": Decimal, "last_sent": date}],   # sent up to D-7, not booked by D
     "not_booked_units": Decimal,
     "nothing_left_since": date | None,   # the first booking, while nothing ever went out of the store
     "left_units": Decimal,               # units that went out of the store, ever
     "by_item": [{"item": Item, "in_store": Decimal, "value": Decimal | None, "sent_90": Decimal,
                  "sold_90": Decimal, "per100_90": Decimal | None, "quality_check": bool}],
     "company_per100_90": Decimal | None,
     "sent_ever": Decimal,                # units sent to the store by D (two more keys than the list above)
     "as_of": date}                       # D itself: a date of any other year is printed with its year
  headline_lines(facts) -> list[str]        the DAMAGE block of the plain /yq-stock screen (title + 3 lines)
  damage_lines(facts, model) -> list[str]   the full /yq-stock damage view (stock.py adds the footer)
  item_lines(facts, ledger_name) -> list[str]   0 to 2 lines for /yq-stock <code>
  facts_json(facts) -> dict                 the facts saved beside the screen (items by name and code)

THE DEFINITIONS (the owner's, D = the ledger's last day; every figure a Decimal, never a float)
  * Sent = units issued to the damage store, counted on the issue day (a receipt booked late never hides
    damage), from the main store or from elsewhere (vans and outlets). Booked = units the damage store
    itself received. In store = booked - left up to a day.
  * Per 100 sold = units sent / units sold x 100 to two decimals (sold: every warehouse). A month is
    partial when it is the month of D and D is no month end, or when the ledger starts inside it.
  * Trend: this month's per 100 against the same figure pooled over the 3 whole months before it
    (sent / sold of the three together). Strictly above 120% of it: rising; strictly below 80%: falling;
    else steady. None (this and prior3 None too), with the reason in why_none, when this month or those 3
    months had fewer than 5 units sent, when there were no sales to divide by, or when the ledger holds
    fewer than 3 whole months before this one (a first month the ledger starts inside is no whole month).
  * A store that is set up but has neither a booking nor an issue to it in the ledger is one line, "no
    movement" (sent_ever carries the second half of that test: units issued to a store that never booked
    anything are movement, and the check line says they are not in there).
  * Not booked = per item, units sent up to D-7 less units booked up to D, when above nought: an issue
    has 7 days for its receive leg to be posted before it is listed.
  * By item = items with units in the store or sent in the 90 days to D (D-89 to D). The quality check
    marks an item whose per 100 sold in those 90 days is above twice the company's with 5 or more units
    sent; the two rates are compared as printed (two decimals).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from scripts.ops import fmt

ZERO = Decimal(0)
Q2 = Decimal("0.01")

MONTHS = 12                    # the table: the last twelve calendar months, up to the month of D
TREND_MONTHS = 3               # the whole months before this one that the trend is set against (pooled)
TREND_MIN_UNITS = 5            # a trend needs this many units sent, this month and in those months
TREND_BAND = Decimal("0.2")    # above 120% of the months before: rising; below 80%: falling
GRACE_DAYS = 7                 # an issue may be booked in up to this many days later without being listed
WINDOW_DAYS = 90               # the by-item window: the 90 days to D
QUALITY_TIMES = 2              # an item is marked above this many times the company's rate ...
QUALITY_MIN_UNITS = 5          # ... with at least this many units sent in the window
BY_ITEM_ROWS = 15              # rows of the by-item table on the full view
NOT_BOOKED_ROWS = 15           # rows of the not-booked table on the full view

NOT_SET_UP = "DAMAGE  not set up: config 'warehouses.damage' names the damage store in Focus"
QUALITY_WORDS = "to check with the supplier"
NOTES = (
    "Counted when sent (the issue leg), so a receipt booked late never hides damage.",
    "Nothing here is booked: claiming from the supplier, writing off, repairing or selling as B-grade is your "
    "decision.",
)


# ── the facts ─────────────────────────────────────────────────────────────────

def _shape(set_up: bool, store: str | None, found: bool, why: str | None = None, as_of: date | None = None) -> dict:
    """The facts with every key in place and nothing in them (a store that is not set up has no figures)."""
    return {"set_up": set_up, "store": store, "found": found, "as_of": as_of, "sent_ever": ZERO,
            "now": {"units": ZERO, "value": ZERO, "uncosted_units": ZERO, "items": 0, "share_of_main": None},
            "months": [], "trend": {"word": None, "this": None, "prior3": None, "why_none": why},
            "not_booked": [], "not_booked_units": ZERO, "nothing_left_since": None, "left_units": ZERO,
            "by_item": [], "company_per100_90": None}


def _month(day: date, back: int = 0) -> date:
    """The first day of day's month, `back` months earlier (a negative `back`: later)."""
    n = day.year * 12 + (day.month - 1) - back
    return date(n // 12, n % 12 + 1, 1)


def _per100(part: Decimal, whole: Decimal) -> Decimal | None:
    """part / whole x 100 to two decimals, half up; None when nothing was sold to divide by."""
    if whole is None or whole <= 0:
        return None
    return (part / whole * 100).quantize(Q2, rounding=ROUND_HALF_UP)


def _ledger_first(pairs) -> date | None:
    """The ledger's first day as the model sees it: the earliest sale or movement of any item."""
    firsts = []
    for it, f in pairs:
        if it.sales:
            firsts.append(min(it.sales))
        for moves in (f.pool, f.main, f.sent_damage, f.booked_damage, f.left_damage):
            if moves:
                firsts.append(min(m[0] for m in moves))
    return min(firsts) if firsts else None


def _few_sent(units: Decimal, where: str) -> str:
    """'only 4 units were sent to damage this month, and a trend needs 5' (the reason a trend is not called)."""
    if units == 0:
        head = "no unit was"
    elif units == 1:
        head = "only 1 unit was"
    else:
        head = f"only {fmt.units(units)} units were"
    return f"{head} sent to damage {where}, and a trend needs {TREND_MIN_UNITS}"


def _trend(months: list[dict]) -> dict:
    """This month's per 100 sold against the 3 whole months before it, pooled; or why there is none."""
    def none(why: str) -> dict:
        return {"word": None, "this": None, "prior3": None, "why_none": why}

    if not months:
        return none("the ledger holds no month")
    cur, prior = months[-1], months[-1 - TREND_MONTHS:-1]
    if cur["sent"] < TREND_MIN_UNITS:
        return none(_few_sent(cur["sent"], "this month"))
    if len(prior) < TREND_MONTHS or any(m["partial"] for m in prior):
        return none(f"the ledger holds fewer than {TREND_MONTHS} whole months before this one")
    sent = sum((m["sent"] for m in prior), ZERO)
    sold = sum((m["sold"] for m in prior), ZERO)
    if sent < TREND_MIN_UNITS:
        return none(_few_sent(sent, f"in the {TREND_MONTHS} months before"))
    if cur["sold"] <= 0:
        return none("no unit was sold this month")
    if sold <= 0:
        return none(f"no unit was sold in the {TREND_MONTHS} months before")
    this, prior3 = cur["per100"], _per100(sent, sold)
    if this > prior3 * (1 + TREND_BAND):
        word = "rising"
    elif this < prior3 * (1 - TREND_BAND):
        word = "falling"
    else:
        word = "steady"
    return {"word": word, "this": this, "prior3": prior3, "why_none": None}


def damage_facts(model, fl) -> dict:
    """Everything the damage screens and the workbook show, worked out from the model's items (their
    sales, cost and main-store stock) and the flows (what was sent to, booked in and taken out of the
    damage store). See the module docstring for the contract and the definitions."""
    d = model.d
    if not model.cfg.damage:
        return _shape(False, None, False, "the damage store is not set up", d)
    facts = _shape(True, fl.damage_store or str(model.cfg.damage).strip(), bool(fl.damage_found), None, d)
    pairs = [(it, fl.get(it.ledger_name)) for it in model.items]

    # now: what the store holds at the end of D, at landed cost, against the main store's stock at cost
    held = [f.damage_at(d) for _it, f in pairs]
    value = sum((u * it.cost for (it, _f), u in zip(pairs, held) if it.cost is not None), ZERO)
    main_value = sum((max(it.on_hand, ZERO) * it.cost for it, _f in pairs if it.cost is not None), ZERO)
    facts["now"] = {"units": sum(held, ZERO), "value": value,
                    "uncosted_units": sum((u for (it, _f), u in zip(pairs, held) if it.cost is None), ZERO),
                    "items": sum(1 for u in held if u > 0),
                    "share_of_main": (value / main_value) if main_value > 0 else None}
    facts["sent_ever"] = sum((f.sent_between(None, d) for _it, f in pairs), ZERO)

    # units sold: by calendar month, and in the 90 days to D, per item (every warehouse)
    win_first = d - timedelta(days=WINDOW_DAYS - 1)
    sold_month: dict[tuple[int, int], Decimal] = {}
    sold_90: list[Decimal] = []
    for it, _f in pairs:
        in_window = ZERO
        for day, units in it.sales.items():
            sold_month[(day.year, day.month)] = sold_month.get((day.year, day.month), ZERO) + units
            if win_first <= day <= d:
                in_window += units
        sold_90.append(in_window)

    # the last twelve months, oldest first, from the ledger's first month when it starts later
    first_day = _ledger_first(pairs)
    cur = _month(d)
    m = _month(d, MONTHS - 1)
    if first_day is not None and _month(first_day) > m:
        m = _month(first_day)
    months = []
    while m <= cur:
        last = _month(m, -1) - timedelta(days=1)
        sent = sent_main = sent_else = booked = ZERO
        for _it, f in pairs:
            sent += f.sent_between(m, last)
            sent_main += f.sent_between(m, last, "main")
            sent_else += f.sent_between(m, last, "elsewhere")
            booked += f.booked_between(m, last)
        sold = sold_month.get((m.year, m.month), ZERO)
        starts_inside = first_day is not None and _month(first_day) == m and first_day > m
        months.append({"month": m, "sold": sold, "sent": sent, "sent_main": sent_main, "sent_elsewhere": sent_else,
                       "booked": booked, "per100": _per100(sent, sold),
                       "partial": (m == cur and d < last) or starts_inside})
        m = _month(m, -1)
    facts["months"] = months
    facts["trend"] = _trend(months)

    # sent and never booked in: an issue has GRACE_DAYS for its receive leg to be posted
    cut = d - timedelta(days=GRACE_DAYS)
    not_booked = []
    for it, f in pairs:
        units = f.sent_between(None, cut) - f.booked_between(None, d)
        if units > 0:
            not_booked.append({"item": it, "units": units,
                               "last_sent": max((day for day, _src, _u in f.sent_damage if day <= cut), default=cut)})
    not_booked.sort(key=lambda x: (-x["units"], x["item"].name))
    facts["not_booked"] = not_booked
    facts["not_booked_units"] = sum((x["units"] for x in not_booked), ZERO)

    # what went out of the store (written off, claimed, returned), and since when nothing has
    facts["left_units"] = sum((f.left_between(None, d) for _it, f in pairs), ZERO)
    first_booking = [min(day for day, _u in f.booked_damage) for _it, f in pairs if f.booked_damage]
    facts["nothing_left_since"] = min(first_booking) if first_booking and facts["left_units"] == 0 else None

    # by item: units in the store or sent in the 90 days to D, against what the item sold in them
    sent_90 = [f.sent_between(win_first, d) for _it, f in pairs]
    company = _per100(sum(sent_90, ZERO), sum(sold_90, ZERO))
    facts["company_per100_90"] = company
    by_item = []
    for (it, _f), in_store, sent, sold in zip(pairs, held, sent_90, sold_90):
        if in_store > 0 or sent > 0:
            rate = _per100(sent, sold)
            by_item.append({"item": it, "in_store": in_store,
                            "value": in_store * it.cost if it.cost is not None else None,
                            "sent_90": sent, "sold_90": sold, "per100_90": rate,
                            "quality_check": (rate is not None and company is not None and sent >= QUALITY_MIN_UNITS
                                              and rate > QUALITY_TIMES * company)})
    by_item.sort(key=lambda x: (x["value"] is None, -(x["value"] or ZERO), -x["in_store"], x["item"].name))
    facts["by_item"] = by_item
    return facts


# ── the screens ───────────────────────────────────────────────────────────────

def _stock():
    """scripts.ops.stock, imported when a screen is worded (stock imports this module at its own top)."""
    from scripts.ops import stock
    return stock


def _guarded(lines: list[str]) -> list[str]:
    """Every line shown passes the wording guard; a word of the data that the guard refuses is blanked."""
    guard = _stock()._guarded
    return [guard(ln) for ln in lines]


def _rate(x) -> str:
    return "n/a" if x is None else f"{x:,.2f}"


def _cost(x) -> str:
    return "n/a" if x is None else fmt.bhd(x)


def _short_line(facts: dict) -> list[str]:
    """The one line of a store that is not set up, or that has not moved a unit; [] when there is more to say."""
    if not facts.get("set_up"):
        return [NOT_SET_UP]
    if not facts.get("found") and not facts.get("sent_ever"):
        return [f"DAMAGE  {facts['store']}: no movement in the stock ledger"]
    return []


def _title(facts: dict, ledger_day: date | None = None) -> str:
    now, plural = facts["now"], _stock()._plural
    notes = []
    if now["share_of_main"] is not None:
        notes.append(f"{fmt.pct(now['share_of_main'], 1)} of the main store's value")
    if now["uncosted_units"]:
        notes.append(f"{plural(now['uncosted_units'], 'unit')} with no cost on file left out of the value")
    where = f", ledger to {fmt.day(ledger_day)}" if ledger_day else ""       # D itself: always in D's year
    return (f"DAMAGE  {facts['store']}{where}: {plural(now['units'], 'unit')}, BHD {fmt.bhd(now['value'])} at landed "
            f"cost" + (f" ({'; '.join(notes)})" if notes else ""))


def _month_line(facts: dict) -> str | None:
    """'This month: ...' with its trend, or the reason there is none."""
    if not facts["months"]:
        return None
    cur, tr = facts["months"][-1], facts["trend"]
    text = f"  This month: {fmt.units(cur['sent'])} sent to damage"
    if cur["per100"] is None:
        return text + ", nothing sold yet"
    text += f", {_rate(cur['per100'])} for every 100 sold"
    if tr["word"]:
        return text + f"; {tr['word']} against the {TREND_MONTHS} months before ({_rate(tr['prior3'])})"
    return text + f"; no trend yet ({tr['why_none']})"


def _check_line(facts: dict) -> str | None:
    units = facts["not_booked_units"]
    if units <= 0:
        return None
    stock = _stock()
    return (f"  To check: {stock._plural(units, 'unit')} sent to damage {'was' if units == 1 else 'were'} not booked in "
            f"there after {GRACE_DAYS} days ({stock._plural(len(facts['not_booked']), 'item')})")


def _when(day: date, ref: date | None) -> str:
    """'Sun 19 Oct' for a day in the year of the data date, 'Sun 19 Oct 2025' for any other (or with no data date
    to compare with): a day of last year must never read like a recent one."""
    return fmt.day(day) + ("" if ref is not None and day.year == ref.year else f" {day.year}")


def _left_line(facts: dict, ref: date | None) -> str | None:
    if facts["nothing_left_since"] is None:
        return None
    return (f"  Nothing has left the damage store since {_when(facts['nothing_left_since'], ref)}: no write-off, claim "
            "or return is booked")


def headline_lines(facts: dict) -> list[str]:
    """The DAMAGE block of the plain screen: the title, then up to three lines."""
    short = _short_line(facts)
    if short:
        return _guarded(short)
    lines = [_title(facts)]
    lines += [ln for ln in (_month_line(facts), _check_line(facts), _left_line(facts, facts.get("as_of"))) if ln]
    return _guarded(lines)


def _trend_lines(tr: dict) -> list[str]:
    if tr["word"]:
        return [f"  Trend: this month's {_rate(tr['this'])} for every 100 sold is {tr['word']} against the "
                f"{TREND_MONTHS} months before ({_rate(tr['prior3'])}, pooled).",
                f"  (Rising or falling = more than {int(TREND_BAND * 100)}% above or below the {TREND_MONTHS} months "
                "before.)"]
    return [f"  Trend: none yet, because {tr['why_none']}."]


def damage_lines(facts: dict, model) -> list[str]:
    """The full /yq-stock damage view: the store, twelve months, the items, what is not booked in."""
    short = _short_line(facts)
    if short:
        return _guarded(short)
    stock = _stock()
    d = model.d
    this_month = _month(d)
    lines = [_title(facts, d),
             "  Sent to damage = units issued to the damage store, counted on the issue day: from the main store, or "
             "from elsewhere (vans and outlets).",
             "  Booked in = units the damage store itself received that month, on any voucher."]
    rows = []
    for m in facts["months"]:
        label = stock.month_name(m["month"])
        if m["partial"]:
            label += " (to date)" if m["month"] == this_month else " (part month)"
        rows.append([label, fmt.units(m["sold"]), fmt.units(m["sent"]), fmt.units(m["sent_main"]),
                     fmt.units(m["sent_elsewhere"]), _rate(m["per100"]), fmt.units(m["booked"])])
    lines += stock._indent(fmt.grid(["Month", "Sold", "Sent to damage", "From the main store", "From elsewhere",
                                     "Per 100 sold", "Booked in"], rows, "lrrrrrr"))
    lines += _trend_lines(facts["trend"])

    by_item = facts["by_item"]
    if by_item:
        lines += ["", f"BY ITEM  {stock._plural(len(by_item), 'item')} in the damage store or sent to it in the last "
                      f"{WINDOW_DAYS} days, most value first"]
        rows = [[stock.short(x["item"].name, 34), x["item"].code or "", fmt.units(x["in_store"]), _cost(x["value"]),
                 fmt.units(x["sent_90"]), _rate(x["per100_90"]), QUALITY_WORDS if x["quality_check"] else ""]
                for x in by_item[:BY_ITEM_ROWS]]
        lines += stock._indent(fmt.grid(["Item", "Code", "In damage", "At cost", f"Sent {WINDOW_DAYS} d",
                                         "Per 100 sold", "Quality"], rows, "llrrrrl"))
        if len(by_item) > BY_ITEM_ROWS:
            lines.append(f"  (+{len(by_item) - BY_ITEM_ROWS} more)")
        if facts["company_per100_90"] is not None:
            times = "twice" if QUALITY_TIMES == 2 else f"{QUALITY_TIMES} times"
            lines.append(f"  Company-wide: {_rate(facts['company_per100_90'])} for every 100 sold in the last "
                         f"{WINDOW_DAYS} days. Marked: an item above {times} that rate with "
                         f"{QUALITY_MIN_UNITS} or more units sent.")
    else:
        lines += ["", f"BY ITEM  no item is in the damage store or was sent to it in the last {WINDOW_DAYS} days"]

    held_back = facts["not_booked"]
    if held_back:
        units = facts["not_booked_units"]
        lines += ["", f"NOT BOOKED  {stock._plural(units, 'unit')} of {stock._plural(len(held_back), 'item')} sent to "
                      f"damage over {GRACE_DAYS} days ago {'is' if units == 1 else 'are'} not booked in the damage "
                      "store: to check"]
        rows = [[stock.short(x["item"].name, 34), x["item"].code or "", fmt.units(x["units"]),
                 stock._date_short(x["last_sent"], d)] for x in held_back[:NOT_BOOKED_ROWS]]
        lines += stock._indent(fmt.grid(["Item", "Code", "Units", "Last sent"], rows, "llrl"))
        if len(held_back) > NOT_BOOKED_ROWS:
            lines.append(f"  (+{len(held_back) - NOT_BOOKED_ROWS} more)")
        lines.append(f"  (An issue is left alone for {GRACE_DAYS} days: a receive leg posted a few days late is not "
                     "listed.)")
    else:
        lines += ["", f"NOT BOOKED  none: every unit sent to damage over {GRACE_DAYS} days ago is booked in the damage "
                      "store"]
    left = _left_line(facts, d)
    if left:
        lines += ["", left]
    lines += [""] + ["  " + note for note in NOTES]
    return _guarded(lines)


def item_lines(facts: dict, ledger_name: str) -> list[str]:
    """0 to 2 lines for /yq-stock <code>: what the item holds in the damage store and what was sent lately."""
    x = next((r for r in facts.get("by_item") or [] if r["item"].ledger_name == ledger_name), None)
    if x is None:
        return []
    stock = _stock()
    held = f"{stock._plural(x['in_store'], 'unit')} in the damage store" if x["in_store"] > 0 \
        else "none in the damage store now"
    if x["sent_90"] > 0:
        sent = f"{fmt.units(x['sent_90'])} sent in the last {WINDOW_DAYS} days"
        if x["per100_90"] is not None:
            sent += f" ({_rate(x['per100_90'])} for every 100 sold)"
    else:
        sent = f"none sent in the last {WINDOW_DAYS} days"
    lines = [f"  Damage: {held}; {sent}"]
    if x["quality_check"]:
        lines.append(f"  Damage rate above twice the company's: {QUALITY_WORDS}")
    return _guarded(lines)


# ── the facts saved beside the screen ─────────────────────────────────────────

def _plain(x):
    """The facts with no Item in them: an item becomes its name and its code."""
    if isinstance(x, dict):
        out = {}
        for key, value in x.items():
            if hasattr(value, "ledger_name") and hasattr(value, "code"):
                out[key], out["code"] = value.name, value.code
            else:
                out[key] = _plain(value)
        return out
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def facts_json(facts: dict) -> dict:
    """JSON-ready: plain dicts and lists (stock._jsonable turns the Decimals into text and the dates into ISO)."""
    return _plain(facts)
