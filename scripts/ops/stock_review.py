"""Smarter ageing: every old, unsold or price-cut item, its pace and how soon it clears, whether its
price cut helped, and what the sales team must sell of it each week. The owner's rules (20-Oct-2026,
his price-cut review): the clear target is config 'stock.clear_target_days' (the plan's default 90);
the only lever is the sales team, so no screen here ever suggests a further price cut or a bundle.
READ-ONLY and pure: scripts.ops.stock gathers the reads and calls review_facts(model, fl, cuts).
Every line it returns passes fmt.careful; no screen line names a salesman (the sellers are for the
owner's own workbook only).

CONTRACT (scripts.ops.stock and the workbook rely on it)
  review_facts(model, fl, cuts) -> dict
      model  the stock Model (items, d, cfg.clear_days, facts["fast"]["rates"], facts["ageing"])
      fl     stockflow.Flows for the same items (sellable_at, main_at, sellers)
      cuts   v_price_change rows of price CUTS, [{code, item_name, changed_on, was, now}] (text), or None
             when the read failed
    {"clear_days": int, "data_date": date (D), "cuts_read": bool,
     "cuts_later": int,                                    # cuts of these items dated after D: left out
     "rows": [{"item": Item, "sellable": Decimal, "main": Decimal, "over_180": Decimal,
               "value": Decimal | None,                  # sellable x landed cost
               "last_sale": date | None,
               "cut": {"date": date, "was": Decimal, "now": Decimal, "pct": Decimal} | None,   # pct: (now-was)/was
               "pace_basis": "cut" | "30 days", "pace_days": int, "pace_units": Decimal, "pace_week": Decimal,
               "days_to_clear": Decimal | None,
               "status": "Cleared" | "Not moving" | "On track" | "Slow",
               "before_week": Decimal | None,            # units a week in the 90 days before the cut
               "in_stock_before_days": int | None,       # of those 90 days, how many ended with sellable stock
               "since_week": Decimal | None,             # units a week since the cut (= pace_week on basis "cut")
               "change": Decimal | None,                 # pace since ÷ pace before (None: nothing sold before, or
                                                         #   in stock on fewer than 30 of the 90 days before)
               "stock_at_cut": Decimal | None,           # sellable at the end of the day before the cut
               "received_since_cut": Decimal | None,     # the main store's supplier receipts, the cut to D
               "sold_since_cut": Decimal | None,
               "pct_sold": Decimal | None,               # sold since ÷ (the stock at the cut + received since)
               "push_week": int | None,                  # units a week that clear it within clear_days
               "action": str,                            # "Push sales team: N a week" | "Keep selling" | ...
               "sellers": [(store, units)]}],            # the 3 stores that sold most in 90 days (owner's file)
     "batches": [{"date": date, "items": int, "counts": {status: n}, "stock_at_cut": Decimal,
                  "received_since": Decimal, "sold": Decimal,
                  "pct_sold": Decimal | None,            # sold ÷ (stock at the cut + received since)
                  "before_week": Decimal, "since_week": Decimal,     # summed over all its items
                  "in_stock_before_items": int,          # its items in stock on 30 of the 90 days before the cut
                  "compared_items": int, "compared_before_week": Decimal, "compared_since_week": Decimal,
                  "change": Decimal | None}],            # one per cut date in the last 180 days, newest first;
                                                         # change = compared since ÷ compared before
     "trend": [{"month_end": date, "over_180_value": Decimal, "value": Decimal, "share": Decimal | None,
                "no_receipt_units": Decimal, "uncosted_units": Decimal}],   # the last 6 month ends, oldest first
     "now": {"over_180_value": Decimal, "value": Decimal, "share": Decimal | None},   # the AGEING line's own figure
     "counts": {status: n}, "push_units_week": int}
  headline_lines(facts) -> list[str]        at most 3 indented lines under the AGEING title of /yq-stock
  review_lines(facts, model) -> list[str]   appended to the /yq-stock ageing view
  item_lines(facts, ledger_name) -> list[str]   0 to 2 lines for /yq-stock <code>
  facts_json(facts) -> dict                 the facts saved beside the screen (items by name and code, no seller)

DEFINITIONS (D = the ledger's last day; "the N days to D" = D-(N-1) to D, both included; Decimal throughout)
  * Price cuts: the rows that read (a date, two prices) and cut the price (0 < now < was), matched to an item
    by its code (upper case, ends trimmed), the newest per code dated up to D (one date: the lower price). A
    cut dated after D waits for the ledger to reach it (counted in cuts_later). cuts None: every item is
    "never cut" and cuts_read is False.
  * Rows: the plannable items (no display item) that have main-store units over 180 days old (the ageing's
    "oldest"), or no sale in the 90 days to D (rate 0) with sellable stock, a new arrival aside (first received
    in those 90 days: too soon to call it unsold, as the ageing's "new stock: nothing to do yet"), or a price
    cut dated in the 180 days to D.
  * Sellable = stockflow's pool at the end of D: the main store, the vans, the outlets and goods moving between
    them; damage and samples left out (the basis of the owner's review). Main = the main store (the ledger).
  * Pace: with a cut dated in the 120 days to D, the units sold from the cut to D over those days; else the
    units sold in the 30 days to D over 30. A week = units x 7 / days. Days to clear = sellable / (units a day).
  * Status: Cleared (no sellable stock), else Not moving (none sold in the pace window), else On track (clears
    within clear_days), else Slow. Push = sellable / clear_days x 7, to 6 decimals, then up to a whole unit,
    for Slow and Not moving only (90 -> 7, 91 -> 8 at 90 days).
  * A cut's effect: before = units sold in the 90 days before the cut, a week; since = units sold from the cut
    to D, a week (the pace itself on basis "cut"); change = since / before, only for an item in the sellable
    stock at the end of 30 or more of those 90 days (else "not in stock before": a pace before that rests on
    a few days of stock says nothing); the stock at the cut = sellable at the end of the day before it; the
    share sold = sold since / (that stock, nought when below, + the main store's supplier receipts dated from
    the cut to D): stock that arrived after the cut was on sale too.
  * Batches: the rows' cuts dated in the 180 days to D, one per date: the statuses counted, the stock at the
    cut, received and sold since, before and since a week summed; the share sold = sold / (stock + received);
    change = since / before summed over the items whose own change is defined (None when none is).
  * Trend: at the end of each of the 6 months before D's month, the main store of every plannable item on its
    supplier receipts dated up to that day (stock.allocate: newest first); units older than 180 days are old,
    units no receipt explains are stock but not old (as the ageing bands); both at today's cost, an item
    without a cost left out of both. "now" is the AGEING line's own figure (its 181-365 and over-365 bands).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from scripts.ops import fmt

ZERO = Decimal(0)
SEVEN = Decimal(7)
Q1 = Decimal("0.1")

PACE_DAYS = 30            # an item with no recent cut: its pace is the units sold in the 30 days to D
CUT_PACE_DAYS = 120       # a cut dated in the 120 days to D sets the pace: the units sold since it
CUT_REVIEW_DAYS = 180     # a cut dated in the 180 days to D puts the item on the review and makes a batch
BEFORE_DAYS = 90          # the pace before a cut: the units sold in the 90 days before it
IN_STOCK_BEFORE_DAYS = 30  # ... compared with the pace since only for an item in stock on 30 of those days
BEFORE_MIN_UNITS = 13      # ... and that sold about one a week then (13 in the 90 days): fewer, and a multiple is noise
OLD_DAYS = 180            # a unit longer than this since its supplier receipt is old (stock.AGE_LIMITS[1])
TREND_MONTHS = 6          # the share of value over OLD_DAYS at the last six month ends
SELLERS_KEPT = 3          # the stores that sold an item most (the owner's own file, never a screen)
NAME_WIDTH = 34

CLEARED, NOT_MOVING, ON_TRACK, SLOW = "Cleared", "Not moving", "On track", "Slow"
STATUS_ORDER = (NOT_MOVING, SLOW, ON_TRACK, CLEARED)          # how the rows are sorted

EXPLAIN_LINES = (
    "  Sellable = the main store, the vans, the outlets and goods moving between them; damage and samples left out.",
    f"  A week = units sold since the item's price cut when it fell in the last {CUT_PACE_DAYS} days, else in the "
    f"last {PACE_DAYS} days; days to clear = sellable / that pace; push = sellable / {{clear}} x 7 a week, rounded up.",
    f"  Before a week = units a week in the {BEFORE_DAYS} days before the cut ('not in stock before' when the item was "
    f"in stock on fewer than {IN_STOCK_BEFORE_DAYS} of them); sold since cut = units sold since it, and their share of "
    "the stock at the cut plus what was received since.",
    f"  Pace = since / before a week, over the items in stock on {IN_STOCK_BEFORE_DAYS} or more of the {BEFORE_DAYS} "
    "days before the cut that sold in them.",
)


# ── the figures ───────────────────────────────────────────────────────────────

def _number(x) -> Decimal | None:
    """A price as the read gives it (text): a finite Decimal, else None (the row is passed over)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = x if isinstance(x, Decimal) else Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return v if v.is_finite() else None


def price_cuts(cuts, codes, d: date) -> tuple[dict, int]:
    """({code: {date, was, now, pct}}, later): the newest price cut of each of these codes dated up to D,
    and how many cut rows of these codes are dated after D. codes: item codes as stock.code_key gives them.
    A row whose date or prices do not read, or that cuts nothing (now at or above was, or nought), is passed
    over; so is a code not in `codes`."""
    from scripts.ops import stock as _stock
    best: dict[str, dict] = {}
    later = 0
    for r in cuts or []:
        if not isinstance(r, dict):
            continue
        code = _stock.code_key(r.get("code"))
        if not code or code not in codes:
            continue
        day = fmt.as_date(r.get("changed_on"))
        was, now = _number(r.get("was")), _number(r.get("now"))
        if day is None or was is None or now is None or not ZERO < now < was:
            continue
        if day > d:
            later += 1
            continue
        old = best.get(code)
        if old is None or (day, -now) > (old["date"], -old["now"]):
            best[code] = {"date": day, "was": was, "now": now, "pct": (now - was) / was}
    return best, later


def status_of(sellable: Decimal, units: Decimal, days_to_clear: Decimal | None, clear_days: int) -> str:
    """Cleared / Not moving / On track / Slow: the first that holds."""
    if sellable <= 0:
        return CLEARED
    if units <= 0 or days_to_clear is None:
        return NOT_MOVING
    return ON_TRACK if days_to_clear <= clear_days else SLOW


def push_per_week(sellable: Decimal, clear_days: int) -> int:
    """Units a week that clear the sellable stock within clear_days: sellable / clear_days x 7, to 6 decimals,
    then up to a whole unit (stock_plan.units_up: 90 -> 7, 91 -> 8 at 90 days); nought with nothing left."""
    from scripts.ops import stock_plan as _plan
    return int(_plan.units_up(max(sellable, ZERO) / Decimal(clear_days) * SEVEN))


def _row(it, flow, d: date, clear: int, over_180: Decimal, cut: dict | None) -> dict:
    """One item of the review: its stock, pace, status, the cut's effect and the push."""
    from scripts.ops import stock as _stock
    sellable = flow.sellable_at(d)
    left = max(sellable, ZERO)
    if cut is not None and cut["date"] >= d - timedelta(days=CUT_PACE_DAYS - 1):
        basis, first = "cut", cut["date"]
    else:
        basis, first = "30 days", d - timedelta(days=PACE_DAYS - 1)
    pace_days = (d - first).days + 1
    units = max(_stock.sold_between(it, first, d), ZERO)
    pace_week = units * SEVEN / pace_days
    days_to_clear = left * pace_days / units if units > 0 else None
    status = status_of(sellable, units, days_to_clear, clear)

    before_week = since_week = change = stock_at_cut = received = sold_since = pct_sold = in_stock_before = None
    if cut is not None:
        day = cut["date"]
        first_before = day - timedelta(days=BEFORE_DAYS)
        before = max(_stock.sold_between(it, first_before, day - timedelta(days=1)), ZERO)
        before_week = before * SEVEN / BEFORE_DAYS
        in_stock_before = sum(1 for n in range(BEFORE_DAYS) if flow.sellable_at(first_before + timedelta(days=n)) > 0)
        sold_since = max(_stock.sold_between(it, day, d), ZERO)
        since_week = sold_since * SEVEN / ((d - day).days + 1)
        if in_stock_before >= IN_STOCK_BEFORE_DAYS and before >= BEFORE_MIN_UNITS:
            change = since_week / before_week
        stock_at_cut = flow.sellable_at(day - timedelta(days=1))
        received = sum((units for on, _voucher, units, _value in it.receipts if day <= on <= d), ZERO)
        available = max(stock_at_cut, ZERO) + received
        pct_sold = sold_since / available if available > 0 else None

    push = push_per_week(sellable, clear) if status in (SLOW, NOT_MOVING) else None
    if push is not None:
        action = f"Push sales team: {push} a week"
    else:
        action = "Keep selling" if status == ON_TRACK else "Nothing left to push"
    sellers = sorted(((store, u) for store, u in flow.sellers.items() if u > 0),
                     key=lambda x: (-x[1], str(x[0])))[:SELLERS_KEPT]
    return {"item": it, "sellable": sellable, "main": it.on_hand, "over_180": over_180,
            "value": left * it.cost if it.cost is not None else None, "last_sale": it.last_sale,
            "cut": dict(cut) if cut is not None else None,
            "pace_basis": basis, "pace_days": pace_days, "pace_units": units, "pace_week": pace_week,
            "days_to_clear": days_to_clear, "status": status,
            "before_week": before_week, "before_units": before if cut is not None else None,
            "in_stock_before_days": in_stock_before, "since_week": since_week,
            "change": change, "stock_at_cut": stock_at_cut, "received_since_cut": received,
            "sold_since_cut": sold_since, "pct_sold": pct_sold,
            "push_week": push, "action": action, "sellers": sellers}


def review_rows(model, fl, cut_of: dict) -> list[dict]:
    """The rows of the review (see DEFINITIONS), sorted Not moving, Slow, On track, Cleared; then by value,
    the highest first (no cost last); then by name."""
    from scripts.ops import stock as _stock
    d, clear = model.d, int(model.cfg.clear_days)
    rates = model.facts["fast"]["rates"]
    old = {x["item"].ledger_name: x["units"] for x in model.facts["ageing"]["oldest"]}
    review_from = d - timedelta(days=CUT_REVIEW_DAYS - 1)
    rows = []
    for it in model.items:
        if it.display:
            continue
        flow = fl.get(it.ledger_name)
        cut = cut_of.get(_stock.code_key(it.code)) if it.code else None
        rate = rates.get(it.ledger_name)
        unsold = (rate is not None and rate.rate <= 0 and not _stock.is_new(it, d)
                  and flow.sellable_at(d) > 0)
        recent_cut = cut is not None and cut["date"] >= review_from
        if it.ledger_name in old or unsold or recent_cut:
            rows.append(_row(it, flow, d, clear, old.get(it.ledger_name, ZERO), cut))
    rank = {s: n for n, s in enumerate(STATUS_ORDER)}
    rows.sort(key=lambda r: (rank[r["status"]], r["value"] is None, -(r["value"] or ZERO), r["item"].name))
    return rows


def batches(rows: list[dict], d: date) -> list[dict]:
    """One per cut date in the CUT_REVIEW_DAYS days to D among the rows' cuts, newest first."""
    review_from = d - timedelta(days=CUT_REVIEW_DAYS - 1)
    by_date: dict[date, dict] = {}
    for r in rows:
        cut = r["cut"]
        if cut is None or cut["date"] < review_from:
            continue
        b = by_date.get(cut["date"])
        if b is None:
            b = by_date[cut["date"]] = {"date": cut["date"], "items": 0, "counts": dict.fromkeys(STATUS_ORDER, 0),
                                        "stock_at_cut": ZERO, "received_since": ZERO, "sold": ZERO,
                                        "before_week": ZERO, "since_week": ZERO, "in_stock_before_items": 0,
                                        "compared_items": 0, "compared_before_week": ZERO, "compared_since_week": ZERO}
        b["items"] += 1
        b["counts"][r["status"]] += 1
        b["stock_at_cut"] += max(r["stock_at_cut"], ZERO)
        b["received_since"] += r["received_since_cut"]
        b["sold"] += r["sold_since_cut"]
        b["before_week"] += r["before_week"]
        b["since_week"] += r["since_week"]
        if r["in_stock_before_days"] >= IN_STOCK_BEFORE_DAYS:
            b["in_stock_before_items"] += 1
        if r["change"] is not None:                    # in stock before and selling then: a pace to compare
            b["compared_items"] += 1
            b["compared_before_week"] += r["before_week"]
            b["compared_since_week"] += r["since_week"]
    out = []
    for day in sorted(by_date, reverse=True):
        b = by_date[day]
        available = b["stock_at_cut"] + b["received_since"]
        b["pct_sold"] = b["sold"] / available if available > 0 else None
        b["change"] = b["compared_since_week"] / b["compared_before_week"] if b["compared_items"] else None
        out.append(b)
    return out


def trend(model, fl) -> list[dict]:
    """The share of the main store's value at cost over OLD_DAYS at the last TREND_MONTHS month ends before D,
    oldest first (see DEFINITIONS)."""
    from scripts.ops import stock as _stock
    plannable = [it for it in model.items if not it.display]
    out = []
    for back in range(TREND_MONTHS - 1, -1, -1):
        end = _stock.month_start(model.d, back) - timedelta(days=1)
        old_value = value = no_receipt = uncosted = ZERO
        for it in plannable:
            units = fl.get(it.ledger_name).main_at(end)
            if units <= 0:
                continue
            if it.cost is None:
                uncosted += units
                continue
            old = ZERO
            for day, _voucher, chunk in _stock.allocate(units, [r for r in it.receipts if r[0] <= end]):
                if day is None:
                    no_receipt += chunk
                elif (end - day).days > OLD_DAYS:
                    old += chunk
            value += units * it.cost
            old_value += old * it.cost
        out.append({"month_end": end, "over_180_value": old_value, "value": value,
                    "share": old_value / value if value > 0 else None,
                    "no_receipt_units": no_receipt, "uncosted_units": uncosted})
    return out


def review_facts(model, fl, cuts) -> dict:
    from scripts.ops import stock as _stock
    d = model.d
    codes = {_stock.code_key(it.code) for it in model.items if not it.display and it.code}
    codes.discard("")
    cut_of, later = price_cuts(cuts, codes, d)
    rows = review_rows(model, fl, cut_of)
    counts = dict.fromkeys(STATUS_ORDER, 0)
    for r in rows:
        counts[r["status"]] += 1
    ag = model.facts["ageing"]
    over_now = ag["rows"][2]["value"] + ag["rows"][3]["value"]       # 181 to 365 days, over 365 days
    now = {"over_180_value": over_now, "value": ag["value"],
           "share": over_now / ag["value"] if ag["value"] > 0 else None}
    return {"clear_days": int(model.cfg.clear_days), "data_date": d, "cuts_read": cuts is not None,
            "cuts_later": later, "rows": rows, "batches": batches(rows, d), "trend": trend(model, fl), "now": now,
            "counts": counts, "push_units_week": sum(r["push_week"] or 0 for r in rows)}


# ── the words ─────────────────────────────────────────────────────────────────

def _week(x) -> str:
    """Units a week: one decimal."""
    return f"{Decimal(x).quantize(Q1, rounding=ROUND_HALF_UP):,}"


def _times(x) -> str:
    return f"{Decimal(x).quantize(Q1, rounding=ROUND_HALF_UP):,}x"


def _month(day: date, d: date) -> str:
    return day.strftime("%b") if day.year == d.year else day.strftime("%b %Y")


def _whom(counts: dict) -> str:
    slow, still = counts.get(SLOW, 0), counts.get(NOT_MOVING, 0)
    if slow and still:
        return "the slow and the not moving"
    return "the slow items" if slow else "the items not moving"


def _counts_line(facts: dict) -> str:
    c, clear = facts["counts"], facts["clear_days"]
    if not facts["rows"]:
        return f"  Clear target {clear} days: no old, unsold or price-cut item to review."
    push = facts["push_units_week"]
    from scripts.ops import stock as _stock
    tail = (f"the sales team needs {_stock._plural(push, 'unit')} a week for {_whom(c)}" if push
            else "nothing for the sales team to push")
    return (f"  Clear target {clear} days at today's pace: {c[ON_TRACK]} on track, {c[SLOW]} slow, "
            f"{c[NOT_MOVING]} not moving, {c[CLEARED]} cleared; {tail}")


def _available(stock_at_cut, received) -> Decimal:
    """The units there were to sell since a cut: the stock at the cut (nought when below) + received since."""
    return max(stock_at_cut or ZERO, ZERO) + (received or ZERO)


def _sold_words(sold, stock_at_cut, received) -> str:
    available = _available(stock_at_cut, received)
    if available <= 0:
        return "nothing was in stock at the cut or received since"
    extra = f" (+{fmt.units(received)} received since)" if received else ""
    return f"{fmt.pct(sold, available)} of the stock at the cut{extra} sold since"


def _batch_pace(b: dict, cell: bool = False) -> str:
    """The batch's pace since against before: over the items it can be compared on (in stock and selling before)."""
    if b["change"] is not None:
        part = b["compared_items"] < b["items"]
        if cell:
            return _times(b["change"]) + (f" ({b['compared_items']} of {b['items']})" if part else "")
        return f"{_times(b['change'])} the pace before" + (f" ({b['compared_items']} of the {b['items']} items)"
                                                            if part else "")
    if not b["in_stock_before_items"]:
        return "not in stock before" if cell else "not in stock before the cut"
    return "too few before" if cell else f"too few sold in the {BEFORE_DAYS} days before to compare"


def _batch_words(b: dict, d: date) -> str:
    from scripts.ops import stock as _stock
    k = b["counts"]
    return (f"Price cut {_stock._date_short(b['date'], d)} ({_stock._plural(b['items'], 'item')}): "
            f"{_sold_words(b['sold'], b['stock_at_cut'], b['received_since'])}; {_batch_pace(b)}; "
            f"{k[CLEARED]} cleared, {k[ON_TRACK]} on track, {k[SLOW]} slow, {k[NOT_MOVING]} not moving")


def _not_read_line() -> str:
    return "  The price cuts were not read this time: every item counts as never cut."


def headline_lines(facts: dict) -> list[str]:
    """Three lines under the AGEING title: the statuses and the week's push, the newest cut batch, the trend."""
    if not facts or "rows" not in facts:
        return []
    from scripts.ops import stock as _stock
    d = facts["data_date"]
    lines = [_counts_line(facts)]
    if not facts["cuts_read"]:
        lines.append(_not_read_line())
    elif facts["batches"]:
        more = len(facts["batches"]) - 1
        lines.append("  " + _batch_words(facts["batches"][0], d)
                     + (f" (+{_stock._plural(more, 'earlier cut date')}: /yq-stock ageing)" if more else ""))
    else:
        lines.append(f"  No price cut in the {CUT_REVIEW_DAYS} days to {fmt.day(d)}.")
    now = facts["now"]
    ends = ", ".join(f"{fmt.pct(t['over_180_value'], t['value'])} at the end of {_month(t['month_end'], d)}"
                     for t in reversed(facts["trend"][-2:]))
    lines.append(f"  Value over {OLD_DAYS} days: {fmt.pct(now['over_180_value'], now['value'])} now"
                 + (f"; {ends}" if ends else ""))
    return lines


def _cut_cell(r: dict, d: date) -> str:
    from scripts.ops import stock as _stock
    cut = r["cut"]
    if cut is None:
        return "never"
    return f"{_stock._date_short(cut['date'], d)} {fmt.pct(cut['now'] - cut['was'], cut['was'])}"


def _before_cell(r: dict) -> str:
    if r["cut"] is None:
        return ""
    if r["in_stock_before_days"] < IN_STOCK_BEFORE_DAYS:
        return "not in stock before"
    return _week(r["before_week"])


def _sold_cell(r: dict) -> str:
    if r["cut"] is None:
        return ""
    sold, available = r["sold_since_cut"], _available(r["stock_at_cut"], r["received_since_cut"])
    return fmt.units(sold) + (f" ({fmt.pct(sold, available)})" if available > 0 else "")


def _clear_cell(r: dict) -> str:
    from scripts.ops import stock as _stock
    return "-" if r["status"] == CLEARED else _stock.days_text(r["days_to_clear"])


def review_lines(facts: dict, model) -> list[str]:
    """The REVIEW block of /yq-stock ageing: the cut batches, every item on the review, the trend."""
    if not facts or "rows" not in facts:
        return []
    from scripts.ops import stock as _stock
    d, clear = facts["data_date"], facts["clear_days"]
    lines = [f"REVIEW  old, unsold and price-cut items against the clear target of {clear} days at today's pace; "
             "the only lever is the sales team (a weekly push per item)",
             _counts_line(facts)]

    lines += ["", f"  Price cuts in the {CUT_REVIEW_DAYS} days to {fmt.day(d)}, one line per date (the price book's "
                  "latest change of each code):"]
    if not facts["cuts_read"]:
        lines.append(_not_read_line())
    elif not facts["batches"]:
        lines.append("    none")
    else:
        rows = []
        for b in facts["batches"]:
            k = b["counts"]
            rows.append([_stock._date_short(b["date"], d), str(b["items"]), str(k[CLEARED]), str(k[ON_TRACK]),
                         str(k[SLOW]), str(k[NOT_MOVING]), fmt.units(b["stock_at_cut"]), fmt.units(b["received_since"]),
                         fmt.units(b["sold"]), fmt.pct(b["sold"], _available(b["stock_at_cut"], b["received_since"])),
                         _week(b["before_week"]), _week(b["since_week"]), _batch_pace(b, cell=True)])
        lines += _stock._indent(fmt.grid(["Price cut", "Items", "Cleared", "On track", "Slow", "Not moving",
                                          "Stock at cut", "Received since", "Sold since", "Sold", "Before a week",
                                          "Since a week", "Pace"], rows, "lrrrrrrrrrrrr"), "    ")
    if facts["cuts_later"]:
        n = facts["cuts_later"]
        lines.append(f"  {_stock._plural(n, 'price cut')} dated after {fmt.day(d)} {'is' if n == 1 else 'are'} left "
                     f"out until the ledger reaches {'it' if n == 1 else 'them'}.")

    lines += ["", f"  Every item on the review: main-store units over {OLD_DAYS} days old, no sale in the "
                  f"{BEFORE_DAYS} days to {fmt.day(d)} (new arrivals aside), or a price cut in the last "
                  f"{CUT_REVIEW_DAYS} days:"]
    if not facts["rows"]:
        lines.append("    none")
    else:
        rows = [[_stock.short(r["item"].name, NAME_WIDTH), r["item"].code or "", fmt.units(r["sellable"]),
                 fmt.units(r["main"]), fmt.units(r["over_180"]), _week(r["pace_week"]), _clear_cell(r), r["status"],
                 _cut_cell(r, d), _before_cell(r), _sold_cell(r), r["action"]] for r in facts["rows"]]
        lines += _stock._indent(fmt.grid(["Item", "Code", "Sellable", "Main", "Over 180 d", "A week", "Days to clear",
                                          "Status", "Cut", "Before a week", "Sold since cut", "Action"], rows,
                                         "llrrrrrllrrl"), "    ")

    now = facts["now"]
    lines += ["", f"  Value over {OLD_DAYS} days at the month ends: the main store on its newest supplier receipts, "
                  "at today's cost:"]
    rows = [[_stock._date_short(t["month_end"], d), fmt.bhd(t["over_180_value"]), fmt.bhd(t["value"]),
             fmt.pct(t["over_180_value"], t["value"])] for t in facts["trend"]]
    rows.append([f"{_stock._date_short(d, d)} (now)", fmt.bhd(now["over_180_value"]), fmt.bhd(now["value"]),
                 fmt.pct(now["over_180_value"], now["value"])])
    lines += _stock._indent(fmt.grid(["Month end", "Over 180 d at cost", "Stock at cost", "Share"], rows, "lrrr"),
                            "    ")
    lines.append("  (units no supplier receipt explains count in the stock, not in the old; an item without a cost is "
                 "left out)")
    lines += [""] + [ln.replace("{clear}", str(clear)) for ln in EXPLAIN_LINES]
    return lines


def item_lines(facts: dict, ledger_name: str) -> list[str]:
    """The item's review: its status, pace and push; and its price cut, when it has one."""
    if not facts or "rows" not in facts:
        return []
    row = next((r for r in facts["rows"] if r["item"].ledger_name == ledger_name), None)
    if row is None:
        return []
    from scripts.ops import stock as _stock
    d, clear = facts["data_date"], facts["clear_days"]
    stock_words = f"{_stock._plural(row['sellable'], 'unit')} sellable (main store {fmt.units(row['main'])})"
    if row["status"] == CLEARED:
        first = f"  Ageing review: {CLEARED}; {stock_words}; nothing left to push"
    else:
        cut = row["cut"]
        window = (f"since the price cut on {_stock._date_short(cut['date'], d)}" if row["pace_basis"] == "cut"
                  else f"over the last {PACE_DAYS} days")
        if row["status"] == NOT_MOVING:
            pace = f"none sold {window}"
        else:
            pace = (f"{_week(row['pace_week'])} a week {window}; {_stock.days_text(row['days_to_clear'])} days to "
                    f"clear against the {clear}-day target")
        first = f"  Ageing review: {row['status']}; {stock_words}; {pace}; {row['action'][0].lower()}{row['action'][1:]}"
    lines = [first]
    cut = row["cut"]
    if cut is not None:
        if row["change"] is not None:
            pace = f"{_times(row['change'])} the pace of the {BEFORE_DAYS} days before"
        elif row["in_stock_before_days"] < IN_STOCK_BEFORE_DAYS:
            pace = (f"not in stock before it (in stock on {row['in_stock_before_days']} of the {BEFORE_DAYS} days "
                    "before)")
        else:
            pace = (f"too few sold in the {BEFORE_DAYS} days before to compare the pace "
                    f"({fmt.units(row.get('before_units') or ZERO)})")
        lines.append(f"  Price cut {_stock._date_short(cut['date'], d)}: {fmt.bhd(cut['was'])} to {fmt.bhd(cut['now'])} "
                     f"({fmt.pct(cut['now'] - cut['was'], cut['was'])}); "
                     f"{_sold_words(row['sold_since_cut'], row['stock_at_cut'], row['received_since_cut'])}; {pace}")
    return lines


def facts_json(facts: dict) -> dict:
    """The facts as they are saved beside the screen: each row's item by name and code, never its sellers."""
    out = {k: v for k, v in facts.items() if k != "rows"}
    out["rows"] = [dict({"item": r["item"].name, "code": r["item"].code},
                        **{k: v for k, v in r.items() if k not in ("item", "sellers")})
                   for r in facts.get("rows") or []]
    return out
