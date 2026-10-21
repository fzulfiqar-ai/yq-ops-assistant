"""The Stock and Movements sheets that the weekly and the month-end workbook share: totals only.

Both workbooks are forwarded, so neither sheet names an item, a voucher or the cost of one item: every figure
is a count, units or BHD, and every figure at cost names the basis it rests on. The figures are worked out first,
into a plain dict (stock_totals, moves_totals), and the sheet is drawn from that dict (stock_sheet,
movements_sheet): the weekly builder and the month-end pack work the figures out once, may read them for lines of
their own, and lay out the same sheet. The report engine writes it (book.py): typed values, every formula written
with the value Book.evaluate() works out for it, the grid formats of style.py (g_*), BHD to three decimals, units
whole.

CONTRACT (the weekly builder, scripts.ops.workbook.week, and the month-end pack rely on it; the weekly builder
passes keyword arguments by name: as_of to stock_totals, first and last to moves_totals)
  stock_totals(model=None, *, as_of=None, window=None) -> dict      never raises
      model   a stock Model: scripts.ops.stock.build(inp, cfg) (today), or build(inp, cfg, as_of=day), the stock at
              the end of an earlier day (a closed week or month: the ledger cut there, the costs today's) - the
              month-end pack's way. None: the model is built here, stock.build(stock.gather(), stock.settings(),
              as_of=as_of) (the weekly builder's way)
      as_of   a date or 'YYYY-MM-DD' (or None: the ledger's last day), only when model is None
      window  (first, last) as dates or 'YYYY-MM-DD', or 'YYYY-MM-DD..YYYY-MM-DD' (period_of), inside the model's
              day series (model.series_start to model.d): the fast movers' availability over that period too (the
              weekly builder hands its week over so); None: the month's only
    A model that cannot be built (stock.build refuses an as_of after the ledger's last day or before its first in
    one sentence; a read or a setting that fails) or facts that are not the contract's shape give
    {"error": why, "as_of": the date asked, "store"}: the sheet then reads "The stock figures: not worked out this
    time (why)." Otherwise counts, units (Decimal) and BHD (Decimal, three decimals); no item name, no Item, no
    voucher (the store's name is read from config here; the sheet functions read nothing):
    {"as_of": date (the model's day), "ledger_to": date (the ledger's last day), "cut": bool (built as on an
     earlier day), "store": str (config 'warehouses.main'),
     "cost_label": "at landed cost" | "at cost", "at today's ..." when cut,
     "basis": str (the label and what it rests on, as the /yq-stock headline words it),
     "main":   {"items", "units", "value", "uncosted_items"}: the main store at the end of as_of, from the ledger
               (the items held above nought, display items in; the value on the basis; items with no cost apart),
     "beside": {"stores", "vans", "outlets", "transit", "counted", "value", "uncosted"} | None: the stock beside
               the main store as the order position counts it (facts["position"]); None while the model has none,
     "cover":  {"days", "low", "high", "value", "above_value", "above_share", "last_days", "last_end",
                "new_items", "uncosted_items"}: KPI 1 (days None when no item with a cost sold in the window),
     "fast":   {"n", "listed", "month": {"first", "last", "days", "available", "sku_days", "share", "out_all"},
                "window": {"first", "last", "days", "available", "sku_days", "share"} | None,
                "out_now": the fast movers without a unit in the main store at the end of as_of},
     "ageing": {"bands": [{"band", "items", "units", "value", "uncosted_units"}] (stock.AGE_BANDS, five),
                "units", "value" (the bands' sums), "over_180_value", "over_180_share", "display_items",
                "display_units"},
     "review": {"counts": {status: n}, "items", "push_units_week", "clear_days"} | None,
     "damage": {"units", "value", "uncosted_units", "month", "partial", "sold", "sent", "per100", "trend"} | None,
     "order":  {"items", "units", "value", "uncosted_items", "join_days", "fill_days"},
     "rate_days", "rank_months": the windows of the daily rate and the fast movers' ranking (stock's own),
     "parts":  {"position", "damage", "review", "plan"[, "window" when one is asked]: OK or why}}
    A part that failed, is missing or is not set up is None (beside, review, damage, the window) and its why is in
    parts: "not worked out this time (<the error's type>)", "not set up (...)". The plan (the suppliers) is not on
    the Stock sheet: its state is in parts only. Shares are exact Decimals of the rounded figures, so the sheet's
    formulas and the dict agree.
  period_of(window) -> (first, last)      the window's two days; Shape (a ValueError) in one sentence otherwise

  moves_totals(table=None, *, first=None, last=None) -> dict      never raises
      table   scripts.ops.moves.period_table(first, last): {"stores": [{"store", "kind" 'van' | 'outlet',
              "units": {MOVE_KEYS}, "bhd": {MOVE_KEYS}, "uncosted_units"}], "totals": {...},
              "counts": scripts.ops.pairing.summarise(...)}; and, when the module gives them, "exceptions"
              [{"kind", "units", "bhd", ...}], "first", "last", "basis" - the month-end pack's way. None: the table
              is read here, scripts.ops.moves.period_table(first, last), the module imported only now (it may not
              be there yet: a module that is missing or raises is the failure below) - the weekly builder's way
      first, last   the period, dates or 'YYYY-MM-DD'; with a table they stand in where it gives none of its own
    {"first", "last": date | None, "basis": str | None,
     "stores": [{"store": the ledger's spelling less " - Acc WH" (as the month workbook names a salesman),
                 "ledger_name", "kind", "units": {MOVE_KEYS}, "bhd": {MOVE_KEYS}, "uncosted_units"}]: vans, then
                 outlets, each by name; every "check" worked out here from the row's own figures (CHECK below),
     "total": {"units": {MOVE_KEYS}, "bhd": {MOVE_KEYS}, "uncosted_units"}: the sums of the rows,
     "exceptions": [{"kind", "words", "count", "units", "bhd" | None}] by kind in EXCEPTIONS order (the module's
                 list summed by kind, else the pairing summary's counts and units, unvalued); None when the table
                 brings neither,
     "unbalanced": int (rows whose units or BHD check is not nought), "note": str | None (the line the sheet adds),
     "error": None | why}
    A table that is not that shape, a figure that is not a number, no period to read one for, a module that is
    missing or raises: "error" says why, stores [] and the sheet reads one line "The movements of the vans and
    outlets: not worked out this time (why)." An exception's vouchers and items are never kept.

  stock_sheet(ws, totals, heading) -> {name: ref}
  movements_sheet(ws, totals, heading) -> {name: ref}
      The sheet drawn on ws (an empty book.Sheet) from the totals, `heading` its title; the refs name the cells
      another sheet may point at, absolute and with the sheet's name ("Stock!$B$7"). Totals that are None or carry
      an "error" (stock_totals itself failed: the caller passes {"error": why}) are one line. The formulas: the
      shares (IF(den>0, num/den, "")), the total rows (SUM), the over-180 value and the Movements check column
      (ROUND(..., 3): 0 on a row that balances); every other figure is typed. Neither sheet names an item or a
      voucher; Movements names the stores (vans and outlets), as the month workbook names salesmen.

THE CHECK (Movements, one row per store, in units and in BHD)
  opening + received + customer_returns + counts + other
    - back_to_main - to_damage - to_other - not_received_yet - not_received - sold - closing = 0
  issued_to (what other stores issued to the store) stands beside it: the store's own receipts are "received".
"""
from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from scripts.ops import fmt
from scripts.ops.workbook import model as mdl
from scripts.ops.workbook.book import Sheet, a1, aa

OK = "ok"
NOT_WORKED = "not worked out this time"
NOT_SET_UP = "not set up (config 'warehouses.damage' names the damage store)"
MAIN_ONLY = f"{NOT_WORKED} (this model counts the main store only)"
ZERO = Decimal(0)
Q3 = Decimal("0.001")
PART_ERRORS = (KeyError, TypeError, AttributeError, ValueError, IndexError, ArithmeticError)

# A store's row of the movements (scripts.ops.moves StoreRow), in the order of the sheet's columns.
MOVE_KEYS = ("opening", "received", "issued_to", "back_to_main", "to_damage", "to_other", "not_received_yet",
             "not_received", "sold", "customer_returns", "counts", "other", "closing", "check")
CHECK_PLUS = ("opening", "received", "customer_returns", "counts", "other")
CHECK_MINUS = ("back_to_main", "to_damage", "to_other", "not_received_yet", "not_received", "sold", "closing")
MOVE_HEADS = {"opening": "Opening", "received": "Received", "issued_to": "Issued to it", "back_to_main": "Back to main",
              "to_damage": "To damage", "to_other": "To other stores", "not_received_yet": "Not received yet",
              "not_received": "Not received", "sold": "Sold", "customer_returns": "Customer returns",
              "counts": "Counts", "other": "Other", "closing": "Closing", "check": "Check"}
KINDS = {"van": "Van", "outlet": "Outlet"}
KIND_ORDER = {"van": 0, "outlet": 1}

# What is to check on the transfers, by kind (the pairing's outcomes, the watch rules' keys as aliases).
EXCEPTIONS = (
    ("not_received", "Not received by its deadline"),
    ("elsewhere", "Received by another store"),
    ("qty_differs", "Received in other quantities"),
    ("twins", "Identical issues, fewer receipts"),
    ("orphan", "A receipt with no issue"),
)
EXCEPTION_WORDS = dict(EXCEPTIONS)
EXCEPTION_ALIASES = {"xfer_open": "not_received", "open": "not_received", "qty_diff": "qty_differs",
                     "xfer_elsewhere": "elsewhere", "xfer_twins": "twins", "twin_items": "twins",
                     "rcpt_orphan": "orphan", "orphans": "orphan"}
SUMMARY_COUNTS = {"not_received": "not_received", "elsewhere": "elsewhere", "qty_differs": "qty_differs",
                  "twins": "twin_items", "orphan": "orphans"}       # pairing.summarise()["counts"] key per kind

# The Stock sheet: label (A), value (B), a note merged over C:F; the small tables use A to E.
S_LABEL, S_VALUE, S_NOTE_FIRST, S_NOTE_LAST = 0, 1, 2, 5
S_WIDTHS = ((0, 0, 46), (1, 4, 14), (5, 5, 36))
# The Movements sheet: Store (A), Kind (B), the figures from C in MOVE_KEYS order; the BHD block adds Q.
M_STORE, M_KIND, M_FIRST = 0, 1, 2
M_COL = {k: M_FIRST + i for i, k in enumerate(MOVE_KEYS)}
M_UNCOSTED = M_FIRST + len(MOVE_KEYS)
FIRST_ROW = 4                         # the first row under the title, the sub-line and the purpose line
_SHEET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


# ── small helpers ─────────────────────────────────────────────────────────────

def _stock():
    """scripts.ops.stock, imported when used (it reads the database client's module at import)."""
    from scripts.ops import stock
    return stock


class Shape(ValueError):
    """Facts or a table that are not the contract's shape, in words of this module only: its sentence may reach a
    sheet. Any other error reaches a sheet by its type alone (another module's words may name a voucher)."""


def _not_worked(why=None) -> str:
    """'not worked out this time (why)': a reason's own full stop left out of the brackets."""
    why = " ".join(str(why or "").split()).rstrip(".")
    return f"{NOT_WORKED} ({why})" if why else NOT_WORKED


def _reason(exc: BaseException) -> str:
    return " ".join(str(exc).split()) if isinstance(exc, Shape) and str(exc).strip() else type(exc).__name__


def _dec(x, what: str = "a figure") -> Decimal:
    """A quantity or BHD as these sheets hold it: a Decimal to three decimals, half up; blank is nought. Anything
    that is not a number is a Shape error (the part then fails, never a silent nought)."""
    if x is None or x == "":
        return ZERO
    if isinstance(x, bool):
        raise Shape(f"{what} is not a number")
    try:
        if isinstance(x, float):
            v = Decimal(repr(x))
        elif isinstance(x, (Decimal, int)):
            v = Decimal(x)
        else:
            v = Decimal(str(x).replace(",", "").strip())
        if not v.is_finite():
            raise ArithmeticError
        v = v.quantize(Q3, rounding=ROUND_HALF_UP)
    except ArithmeticError:
        raise Shape(f"{what} is not a number") from None
    return v if v else ZERO


def _q3(x) -> Decimal | None:
    return None if x is None else fmt.q3(x)


def _count(x) -> int:
    """A count the facts hold either as a collection (a set of ledger names) or as a number already."""
    return len(x) if isinstance(x, (set, frozenset, list, tuple, dict)) else int(x or 0)


def _share(part, whole) -> Decimal | None:
    part, whole = Decimal(part), Decimal(whole)
    return part / whole if whole > 0 else None


def _day(x) -> str:
    d = fmt.as_date(x)
    return d.strftime("%a %d %b %Y") if d else "n/a"


def _span(first, last) -> str:
    """'1 Mar to 15 Mar 2026' (both years when they differ), as the month workbook writes a span."""
    a, b = fmt.as_date(first), fmt.as_date(last)
    if a is None or b is None:
        return "n/a"
    head = f"{a.day} {a:%b}" + (f" {a.year}" if a.year != b.year else "")
    return f"{head} to {b.day} {b:%b %Y}"


def _plural(n, one: str, many: str | None = None) -> str:
    return f"{fmt.units(n)} {one if n == 1 else (many or one + 's')}"


def _said(text: str) -> str:
    """A line as the sheet writes it: through the wording guard (fmt.careful); a word of the data that the guard
    refuses is blanked, never written (as scripts.ops.stock words its screens)."""
    text = " ".join(str(text or "").split())
    try:
        return fmt.careful(text)
    except ValueError:
        out = []
        for part in re.split(r"(\s+)", text):
            try:
                out.append(fmt.careful(part))
            except ValueError:
                out.append("-" * len(part))
        return "".join(out)


def _n(x):
    """A figure as the file holds it: a whole Decimal is an int, another a float; None stays a blank."""
    if x is None or isinstance(x, (bool, str, date)):
        return x
    if isinstance(x, Decimal):
        if not x.is_finite():
            return None
        return int(x) if x == x.to_integral_value() else float(x)
    return x


def _put(ws: Sheet, r: int, c: int, value, fmt_name: str) -> None:
    """A typed value in a grid cell; None (or empty text) is a blank that keeps the cell's border."""
    v = _n(value)
    if v is None or v == "":
        ws.blank(r, c, fmt_name)
    elif isinstance(v, str):
        ws.text(r, c, _said(v), fmt_name)
    else:
        ws.put(r, c, v, fmt_name)


def ref(ws: Sheet, r: int, c: int, r2: int | None = None, c2: int | None = None) -> str:
    """An absolute reference to a cell (or a block) of ws, with the sheet's name: Stock!$B$7, 'My sheet'!$B$7."""
    name = ws.name if _SHEET_NAME.match(ws.name) else "'" + ws.name.replace("'", "''") + "'"
    cell = aa(r, c) if r2 is None else f"{aa(r, c)}:{aa(r2, c if c2 is None else c2)}"
    return f"{name}!{cell}"


def _height(text: str, width_units: float) -> float | None:
    per = max(int(width_units * 1.15), 10)          # Arial 9.5: about 1.15 characters a unit of width
    n = max(1, -(-len(text) // per))
    return None if n == 1 else 12.6 * n + 3


def _main_name() -> str:
    try:
        from scripts.ops import config
        return str(config.get("warehouses.main"))
    except Exception:  # noqa: BLE001  (a heading's words, never the figures)
        return "the main store"


def _page(ws: Sheet, landscape: bool) -> None:
    """Margins and footer as the month workbook's sheets have them, one page wide."""
    from scripts.ops.workbook import sheets_core        # when used: the month pack's builder may import this module
    ws.page.update({"margins": dict(sheets_core.MARGINS), "footer": sheets_core.footer(ws.name), "footer_margin": 0.25,
                    "landscape": landscape, "fit": (1, 0)})


def _title(ws: Sheet, heading: str, sub: str, purpose: str) -> None:
    ws.text(0, 0, _said(heading), "sheet_title")
    ws.row(0, 21)
    ws.text(1, 0, _said(sub), "sheet_sub")
    ws.text(2, 0, _said(purpose), "note")


def _failed_line(ws: Sheet, what: str, why) -> None:
    why = why if str(why or "").startswith(NOT_WORKED) else _not_worked(why)
    ws.text(FIRST_ROW, 0, _said(f"{what}: {why}."), "note")


def _drawn(ws: Sheet, draw, what: str, heading: str, sub: str, purpose: str) -> dict:
    """draw() on ws; totals that are not the shape the contract says (a key missing, a figure of the wrong kind)
    undo what it wrote and leave the title and one line instead, so a sheet never stops the workbook."""
    before = (dict(ws.cells), list(ws.merges), list(ws.conds), dict(ws.rows), dict(ws.page), list(ws.validations))
    try:
        return draw()
    except PART_ERRORS as exc:
        ws.cells, ws.merges, ws.conds = dict(before[0]), list(before[1]), list(before[2])
        ws.rows, ws.page, ws.validations = dict(before[3]), dict(before[4]), list(before[5])
        _title(ws, heading, sub, purpose)
        _failed_line(ws, what, _reason(exc))
        return {}


# ── the stock totals ──────────────────────────────────────────────────────────

def _part(facts: dict, key: str) -> tuple[dict | None, str]:
    """(the part's facts, OK), or (None, why) for a part that is missing, a stub or failed."""
    part = facts.get(key)
    if not isinstance(part, dict) or not part:
        return None, NOT_WORKED
    if "error" in part:
        return None, _not_worked(part["error"])
    if part.get("built") is False:
        return None, NOT_WORKED
    return part, OK


def _beside(pos) -> tuple[dict | None, str]:
    """The order position's stock beside the main store (facts["position"]), or why there is none."""
    if pos is None:
        return None, MAIN_ONLY
    if not isinstance(pos, dict):
        return None, NOT_WORKED
    if pos.get("error"):
        return None, _not_worked(pos["error"])
    stores = [str(s) for s in pos.get("stores") or []]
    if not stores:
        return None, "not counted (config 'stock.position_stores' names no store beside the main store)"
    t = pos["totals"]
    cost = t.get("cost_bhd")
    return {"stores": stores, "vans": _dec(t.get("vans")), "outlets": _dec(t.get("outlets")),
            "transit": _dec(t.get("transit")), "counted": _dec(t.get("counted")),
            "value": None if cost is None else _dec(cost), "uncosted": _dec(t.get("uncosted"))}, OK


def period_of(window) -> tuple[date, date]:
    """(first, last) of a period given as two dates (or 'YYYY-MM-DD' texts) or as one text 'YYYY-MM-DD..YYYY-MM-DD'
    (the weekly builder hands its week over so). Shape, in one sentence, for anything else."""
    if isinstance(window, str):
        ends = window.split("..")
        window = tuple(x.strip() for x in ends) if len(ends) == 2 else ()
    if not isinstance(window, (tuple, list)) or len(window) != 2:
        raise Shape("the period is two dates, or 'YYYY-MM-DD..YYYY-MM-DD'")
    first, last = (fmt.as_date(x) for x in window)
    if first is None or last is None:
        raise Shape("the period is two dates, or 'YYYY-MM-DD..YYYY-MM-DD'")
    return first, last


def _window(model, window, st) -> dict:
    """The fast movers' availability over (first, last): a day counts when the main store's day-end stock is above
    nought and covers one day of the item's rate as on the period's last day (stock.build's own rule)."""
    first, last = period_of(window)
    if last < first:
        raise Shape("the period ends before it starts")
    if last > model.d:
        raise Shape(f"the period ends after {_day(model.d)}, the stock's day")
    if first < model.series_start:
        raise Shape(f"the period starts before {_day(model.series_start)}, the first day of the day series")
    fast = model.facts["fast"]["list"]
    have = 0
    for it in fast:
        rate = st.rate_at(it, model.series_start, last).rate
        have += st.available_days(it, model.series_start, first, last, rate)
    days = (last - first).days + 1
    total = days * len(fast)
    return {"first": first, "last": last, "days": days, "available": have, "sku_days": total,
            "share": _share(have, total)}


_CONFIG_KEY = re.compile(r"'([a-z_]+(?:\.[a-z_]+)+)'")


def _why(exc: BaseException, st) -> str:
    """Why the stock model could not be built, for the sheet's one line: stock.build's own sentence (a day outside
    the ledger, no ledger loaded); a setting by its key only (the config error names the file's path); anything
    else by its type alone (another module's words may name a voucher)."""
    text = " ".join(str(exc).split())
    if isinstance(exc, st.StockInput) and text:
        return text
    from scripts.ops import config
    if isinstance(exc, config.ConfigMissing):
        key = _CONFIG_KEY.search(text)
        return f"config '{key.group(1)}' is not set, or not valid" if key else "a setting in config.json is not valid"
    return type(exc).__name__


def stock_totals(model=None, *, as_of=None, window=None) -> dict:
    """The Stock sheet's figures (see the module docstring): counts, units and BHD only. Never raises: a model that
    cannot be built, or facts that are not the contract's shape, give {"error": why, "as_of": the day asked}."""
    try:
        st = _stock()
    except Exception as exc:  # noqa: BLE001  (the stock engine could not be loaded: one line, never the workbook)
        return {"error": type(exc).__name__, "as_of": fmt.as_date(as_of), "store": _main_name()}
    if model is None:
        try:
            cfg = st.settings()
            model = st.build(st.gather(), cfg, as_of=as_of)
        except Exception as exc:  # noqa: BLE001  (the stock is one part of the workbook, never the workbook)
            return {"error": _why(exc, st), "as_of": fmt.as_date(as_of), "store": _main_name()}
    try:
        return _totals_of(model, window, st)
    except Exception as exc:  # noqa: BLE001  (facts not the contract's shape: one line, never the workbook)
        return {"error": _reason(exc), "as_of": fmt.as_date(getattr(model, "d", None) or as_of),
                "store": _main_name()}


def _totals_of(model, window, st) -> dict:
    f = model.facts
    d = model.d
    cut = f.get("as_of") if isinstance(f.get("as_of"), dict) else None
    h = f["headline"]
    label, detail = st.cost_words({"mrn": h["cost_mrn_items"], "sheet": h["cost_sheet_items"],
                                   "receipt": h["cost_receipt_items"], "none": h["uncosted_items"]},
                                  h.get("uncosted_sell_bhd"))
    if cut:
        label = label.replace("at ", "at today's ", 1)
    ag = f["ageing"]
    parts: dict[str, str] = {}
    out: dict = {"as_of": d, "ledger_to": fmt.as_date((cut or {}).get("ledger_to")) or d, "cut": cut is not None,
                 "store": _main_name(), "cost_label": label, "basis": f"{label} ({detail})",
                 "rate_days": int(st.RATE_DAYS), "rank_months": int(st.RANK_MONTHS)}
    out["main"] = {"items": sum(1 for it in model.items if it.on_hand > 0), "units": Decimal(ag["main_units"]),
                   "value": fmt.q3(h["cost_bhd"]), "uncosted_items": int(h["uncosted_items"])}
    out["beside"], parts["position"] = _guard(lambda: _beside(f.get("position")))

    k = f["kpi_cover"]
    now, last = k["now"], k["last"]
    value, above = fmt.q3(now["value"]), fmt.q3(now["above_value"])
    out["cover"] = {"days": _q3(now["days"]), "low": int(k["low"]), "high": int(k["high"]), "value": value,
                    "above_value": above, "above_share": _share(above, value), "last_days": _q3(last["days"]),
                    "last_end": k["last_month_end"], "new_items": int(now["new_items"]),
                    "uncosted_items": int(now["uncosted_items"])}

    fa = f["fast"]
    a = fa["now"]
    out["fast"] = {"n": int(fa["n"]), "listed": len(fa["list"]),
                   "month": {"first": a["first"], "last": a["last"], "days": int(a["days"]),
                             "available": int(a["available"]), "sku_days": int(a["sku_days"]),
                             "share": _share(a["available"], a["sku_days"]), "out_all": len(a["out_all"])},
                   "window": None, "out_now": sum(1 for it in fa["list"] if it.on_hand <= 0)}
    if window is not None:
        try:
            out["fast"]["window"] = _window(model, window, st)
            parts["window"] = OK
        except PART_ERRORS as exc:
            parts["window"] = _not_worked(_reason(exc))

    bands = [{"band": r["band"], "items": _count(r["items"]), "units": Decimal(r["units"]), "value": fmt.q3(r["value"]),
              "uncosted_units": Decimal(r.get("uncosted_units") or 0)} for r in ag["rows"]]
    total = sum((b["value"] for b in bands), ZERO)
    over = bands[2]["value"] + bands[3]["value"]         # 181 to 365 days, over 365 days (stock.AGE_BANDS)
    out["ageing"] = {"bands": bands, "units": sum((b["units"] for b in bands), ZERO), "value": total,
                     "over_180_value": over, "over_180_share": _share(over, total),
                     "display_items": int(ag["display_items"]), "display_units": Decimal(ag["display_units"])}

    review, why = _part(f, "review")
    out["review"] = None
    if review is not None:
        out["review"], why = _guard(lambda: ({
            "counts": {str(s): int(n) for s, n in review["counts"].items()}, "items": len(review["rows"]),
            "push_units_week": int(review["push_units_week"]), "clear_days": int(review["clear_days"])}, OK))
    parts["review"] = why

    damage, why = _part(f, "damage")
    out["damage"] = None
    if damage is not None and not damage.get("set_up"):
        why = NOT_SET_UP
    elif damage is not None:
        def damage_totals():
            here = damage["now"]
            months = damage.get("months") or []
            m = months[-1] if months else {}
            return {"units": _dec(here["units"]), "value": fmt.q3(here["value"]),
                    "uncosted_units": _dec(here.get("uncosted_units")), "month": m.get("month"),
                    "partial": bool(m.get("partial")), "sold": _dec(m.get("sold")), "sent": _dec(m.get("sent")),
                    "per100": m.get("per100"), "trend": (damage.get("trend") or {}).get("word")}, OK
        out["damage"], why = _guard(damage_totals)
    parts["damage"] = why

    parts["plan"] = _part(f, "plan")[1]

    order = f["order"]
    out["order"] = {"items": len(order), "units": sum((Decimal(x["qty"]) for x in order), ZERO),
                    "value": sum((fmt.q3(x["cost_bhd"]) for x in order if x.get("cost_bhd") is not None), ZERO),
                    "uncosted_items": sum(1 for x in order if x.get("cost_bhd") is None),
                    "join_days": f.get("trigger_days"), "fill_days": f.get("fill_days")}
    out["parts"] = parts
    return out


def _guard(work) -> tuple:
    """(value, OK) from work(), or (None, why) when the facts are not the shape the contract says."""
    try:
        return work()
    except PART_ERRORS as exc:
        return None, _not_worked(_reason(exc))


# ── the movements totals ──────────────────────────────────────────────────────

def _moves_failed(first, last, why) -> dict:
    return {"first": fmt.as_date(first), "last": fmt.as_date(last), "basis": None, "stores": [], "total": None,
            "exceptions": None, "unbalanced": 0, "note": _not_worked(why), "error": " ".join(str(why).split())}


def _check(figures: dict) -> Decimal:
    return sum((figures[k] for k in CHECK_PLUS), ZERO) - sum((figures[k] for k in CHECK_MINUS), ZERO)


def _store_row(row) -> dict:
    if not isinstance(row, Mapping):
        raise Shape("a store's row is not a table row")
    name = " ".join(str(row.get("store") or "").split())
    if not name:
        raise Shape("a store's row names no store")
    kind = str(row.get("kind") or "").strip().lower() or "other"
    out = {"store": name.replace(mdl.WH_SUFFIX, "").strip() or name, "ledger_name": name, "kind": kind}
    for block in ("units", "bhd"):
        given = row.get(block)
        if not isinstance(given, Mapping):
            raise Shape(f"a store's row has no {block}")
        figures = {k: _dec(given.get(k), f"a store's {block}") for k in MOVE_KEYS if k != "check"}
        figures["check"] = _check(figures)
        out[block] = figures
    out["uncosted_units"] = _dec(row.get("uncosted_units"), "a store's units not valued")
    return out


def _exceptions(table) -> list[dict] | None:
    """What is to check, summed by kind: the module's own list (each entry's units and BHD; its vouchers and items
    left behind), else the pairing summary's counts and units (no BHD). None when the table brings neither."""
    given = table.get("exceptions")
    by_kind: dict[str, dict] = {}

    def slot(kind: str) -> dict:
        kind = EXCEPTION_ALIASES.get(kind, kind)
        if kind not in by_kind:
            by_kind[kind] = {"kind": kind, "words": EXCEPTION_WORDS.get(kind, kind.replace("_", " ").capitalize()),
                             "count": 0, "units": ZERO, "bhd": None}
        return by_kind[kind]

    if isinstance(given, (list, tuple)):
        for x in given:
            if not isinstance(x, Mapping) or not str(x.get("kind") or "").strip():
                raise Shape("an exception names no kind")
            s = slot(str(x["kind"]).strip().lower())
            s["count"] += int(_dec(x["count"], "an exception's count")) if x.get("count") is not None else 1
            s["units"] += _dec(x.get("units"), "an exception's units")
            if x.get("bhd") is not None:
                s["bhd"] = (s["bhd"] or ZERO) + _dec(x["bhd"], "an exception's BHD")
    else:
        summary = table.get("counts")
        if not isinstance(summary, Mapping) or not isinstance(summary.get("counts"), Mapping):
            return None
        counts, outcomes = summary["counts"], summary.get("outcomes") or {}
        for kind, key in SUMMARY_COUNTS.items():
            n = int(_dec(counts.get(key), "a pairing count"))
            if n:
                s = slot(kind)
                s["count"] = n
                s["units"] = _dec((outcomes.get(kind) or {}).get("units"), "an outcome's units")
    order = {k: i for i, (k, _w) in enumerate(EXCEPTIONS)}
    return sorted(by_kind.values(), key=lambda s: (order.get(s["kind"], len(order)), s["kind"]))


def _moves_module():
    """scripts.ops.moves, imported only when the sheet is made (another release builds it)."""
    return importlib.import_module("scripts.ops.moves")


def moves_totals(table=None, *, first=None, last=None) -> dict:
    """The Movements sheet's figures from moves.period_table's table, read here when none is given (see the module
    docstring). Never raises."""
    lo, hi = fmt.as_date(first), fmt.as_date(last)
    if table is None:
        if lo is None or hi is None or hi < lo:
            return _moves_failed(lo, hi, "no period to read: a first and a last day, the first on or before the last")
        try:
            work = _moves_module().period_table
        except ModuleNotFoundError as exc:
            missing = exc.name == "scripts.ops.moves"
            return _moves_failed(lo, hi, "the movements module is not there yet" if missing else type(exc).__name__)
        except Exception as exc:  # noqa: BLE001
            return _moves_failed(lo, hi, type(exc).__name__)
        try:
            table = work(lo, hi)
        except Exception as exc:  # noqa: BLE001  (the movements are one part of the workbook, never the workbook)
            return _moves_failed(lo, hi, type(exc).__name__)
    out = _moves_of(table)
    out["first"] = out["first"] or lo
    out["last"] = out["last"] or hi
    return out


def _moves_of(table) -> dict:
    first = last = None
    try:
        if not isinstance(table, Mapping) or not isinstance(table.get("stores"), (list, tuple)):
            raise Shape("the movements table is not the shape it should be")
        summary = table.get("counts") if isinstance(table.get("counts"), Mapping) else {}
        first = fmt.as_date(table.get("first") or summary.get("first"))
        last = fmt.as_date(table.get("last") or summary.get("last"))
        stores = [_store_row(r) for r in table["stores"]]
        stores.sort(key=lambda s: (KIND_ORDER.get(s["kind"], len(KIND_ORDER)), s["store"].casefold(), s["store"]))
        total = {block: {k: sum((s[block][k] for s in stores), ZERO) for k in MOVE_KEYS} for block in ("units", "bhd")}
        total["uncosted_units"] = sum((s["uncosted_units"] for s in stores), ZERO)
        exceptions = _exceptions(table)
        basis = table.get("basis")
        unbalanced = sum(1 for s in stores if s["units"]["check"] or s["bhd"]["check"])
    except Exception as exc:  # noqa: BLE001  (one part of the workbook, never the workbook)
        return _moves_failed(first, last, _reason(exc))
    notes = []
    if unbalanced:
        notes.append(f"{_plural(unbalanced, 'store')} {'does' if unbalanced == 1 else 'do'} not balance: the Check "
                     "column shows by how much, to check.")
    if total["uncosted_units"]:
        notes.append(f"{_plural(total['uncosted_units'], 'unit')} without a cost on file: counted in units, not "
                     "valued in BHD.")
    return {"first": first, "last": last, "basis": " ".join(basis.split()) if isinstance(basis, str) else None,
            "stores": stores, "total": total, "exceptions": exceptions, "unbalanced": unbalanced,
            "note": " ".join(notes) or None, "error": None}


# ── the sheets ────────────────────────────────────────────────────────────────

class _Lines:
    """A sheet written top to bottom: sections, label | value | note lines, small grid tables; refs by name."""

    def __init__(self, ws: Sheet, note_width: float):
        self.ws, self.r, self.refs, self.note_width = ws, FIRST_ROW, {}, note_width

    def section(self, text: str) -> None:
        if self.r > FIRST_ROW:
            self.r += 1
        self.ws.text(self.r, 0, _said(text), "subhead")
        self.r += 1

    def line(self, label: str, value=None, fmt_name: str = "g_int", note: str | None = None,
             formula: str | None = None, key: str | None = None) -> int:
        ws, r = self.ws, self.r
        ws.text(r, S_LABEL, _said(label), "g_text")
        if formula is not None:
            ws.formula(r, S_VALUE, formula, fmt_name)
        else:
            _put(ws, r, S_VALUE, value, fmt_name)
        text = _said(note) if note else None
        ws.merge(r, S_NOTE_FIRST, r, S_NOTE_LAST, text, "g_text_wrap")
        h = _height(text or "", self.note_width)
        if h:
            ws.row(r, h)
        if key:
            self.refs[key] = ref(ws, r, S_VALUE)
        self.r += 1
        return r

    def words(self, text: str) -> None:
        self.ws.text(self.r, 0, _said(text), "note")
        self.r += 1

    def head(self, labels) -> int:
        """A header row: (text, right-aligned) per column from A."""
        for c, (text, right) in enumerate(labels):
            self.ws.text(self.r, c, _said(text), "g_head_r" if right else "g_head")
        self.ws.row(self.r, 27)
        self.r += 1
        return self.r - 1


def stock_sheet(ws: Sheet, totals, heading: str) -> dict:
    """The Stock sheet (see the module docstring); returns the refs of its figures by name. Never raises on the
    totals: failed ones, or ones that are not the contract's shape, are the title and one line."""
    for c1, c2, width in S_WIDTHS:
        ws.col(c1, c2, width)
    _page(ws, landscape=False)
    given = totals if isinstance(totals, Mapping) else {}
    asked = fmt.as_date(given.get("as_of"))
    sub = str(given.get("store") or "Main store") + (f" · stock as on {_day(asked)}" if asked else "")
    purpose = "Totals only: no item is named on this sheet."
    if not given or given.get("error"):
        _title(ws, heading, sub, purpose)
        _failed_line(ws, "The stock figures", given.get("error"))
        return {}
    return _drawn(ws, lambda: _stock_body(ws, given, heading), "The stock figures", heading, sub, purpose)


def _stock_body(ws: Sheet, t: Mapping, heading: str) -> dict:
    store = t.get("store") or "the main store"
    _title(ws, heading, f"{store} · stock as on {_day(t['as_of'])} · stock ledger to {_day(t['ledger_to'])} · "
                        f"{t['cost_label']}",
           "Totals only: no item is named on this sheet. Every figure at cost names the basis it rests on.")
    w = _Lines(ws, note_width=14 * 3 + 36)
    parts = t.get("parts") or {}

    def part_line(key: str, words: str) -> None:
        w.words(f"{words}: {parts.get(key) or NOT_WORKED}.")

    m = t["main"]
    w.section(f"Main store ({store}), end of {_day(t['as_of'])}")
    w.line("Items in stock", m["items"], key="main_items", note="Items with units above nought; display items in.")
    w.line("Units", m["units"], key="main_units")
    w.line("At cost, BHD", m["value"], "g_money3", key="main_value", note=t["basis"])
    w.line("Items without a cost", m["uncosted_items"], key="main_uncosted", note="Counted, not valued.")

    w.section("Beside the main store: vans, outlets and goods between them")
    b = t.get("beside")
    if b is None:
        part_line("position", "The vans and outlets")
    else:
        counted = ", ".join(str(s) for s in b.get("stores") or [])
        w.line("Vans, units", b["vans"], key="beside_vans")
        w.line("Outlets, units", b["outlets"], key="beside_outlets")
        w.line("Between stores, inside their deadline, units", b["transit"], key="beside_transit",
               note="Issued and not yet received, still inside the deadline.")
        w.line("At cost, BHD", b["value"], "g_money3", key="beside_value",
               note=f"Counted in the order position: {counted}." if counted else None)
        w.line("Without a cost", b["uncosted"], key="beside_uncosted", note="Counted, not valued.")

    c = t["cover"]
    w.section(f"Cover (KPI 1: {c['low']} to {c['high']} days)")
    days = c["days"]
    w.line("Aggregate cover, days", None if days is None else _whole(days), key="cover_days",
           note=("No item with a cost sold in the window: cover cannot be worked out." if days is None else
                 "Stock at cost ÷ the daily cost of sales; new arrivals and display items apart."))
    rv = w.line("Stock in the cover figure, at cost, BHD", c["value"], "g_money3", key="cover_value")
    ra = w.line(f"Above {c['high']} days or no sale, at cost, BHD", c["above_value"], "g_money3",
                key="cover_above_value")
    w.line(f"Share above {c['high']} days", None, "g_pct", key="cover_above_share",
           formula=f'IF({a1(rv, S_VALUE)}>0,{a1(ra, S_VALUE)}/{a1(rv, S_VALUE)},"")')
    last = c["last_days"]
    w.line(f"Cover on {_day(c['last_end'])}, days", None if last is None else _whole(last), key="cover_last_days",
           note="With today's costs." if last is not None else "Nothing with a cost sold in its window.")

    fa = t["fast"]
    w.section(f"Fast movers in stock (the {fa['n']} best sellers)")
    w.head([("Period", False), ("Available SKU-days", True), ("SKU-days", True), ("Share", True)])
    rows = [("month", fa["month"])] + ([("window", fa["window"])] if fa.get("window") else [])
    for key, x in rows:
        r = w.r
        kind = "Month to date" if key == "month" else ("Week" if x["days"] == 7 else "Period")
        if key == "month" and _month_end(x["last"]):
            kind = "Month"
        ws.text(r, 0, _said(f"{kind}, {_span(x['first'], x['last'])}"), "g_text")
        _put(ws, r, 1, x["available"], "g_int")
        _put(ws, r, 2, x["sku_days"], "g_int")
        ws.formula(r, 3, f'IF({a1(r, 2)}>0,{a1(r, 1)}/{a1(r, 2)},"")', "g_pct")
        w.refs.update({f"fast_{key}_available": ref(ws, r, 1), f"fast_{key}_sku_days": ref(ws, r, 2),
                       f"fast_{key}_share": ref(ws, r, 3)})
        w.r += 1
    if "window" in parts and parts["window"] != OK:
        part_line("window", "The fast movers over the period")
    w.line(f"Fast movers without a unit in the main store, end of {_day(t['as_of'])}", fa["out_now"],
           key="fast_out_now", note=f"Of the {fa['listed']} ranked." if fa["listed"] != fa["n"] else None)
    so_far = "" if _month_end(fa["month"]["last"]) else " so far"
    w.line(f"Fast movers out on every day of the month{so_far}", fa["month"]["out_all"], key="fast_out_all")

    ag = t["ageing"]
    w.section("Ageing: units on their newest supplier receipts")
    w.head([("Age", False), ("Items", True), ("Units", True), ("At cost, BHD", True), ("Units without a cost", True)])
    first = w.r
    band_rows = []
    for band in ag["bands"]:
        r = w.r
        ws.text(r, 0, _said(str(band["band"]).capitalize()), "g_text")
        _put(ws, r, 1, band["items"], "g_int")
        _put(ws, r, 2, band["units"], "g_int")
        _put(ws, r, 3, band["value"], "g_money3")
        _put(ws, r, 4, band["uncosted_units"], "g_int")
        band_rows.append(r)
        w.r += 1
    if ag["display_items"]:
        r = w.r
        ws.text(r, 0, "Display items (left out of the ageing)", "g_text")
        _put(ws, r, 1, ag["display_items"], "g_int")
        _put(ws, r, 2, ag["display_units"], "g_int")
        ws.blank(r, 3, "g_money3")
        ws.blank(r, 4, "g_int")
        w.r += 1
    tot = w.r
    ws.text(tot, 0, "Total", "g_total_int")
    ws.blank(tot, 1, "g_total_int")
    for col, fmt_name in ((2, "g_total_int"), (3, "g_total_money3"), (4, "g_total_int")):
        ws.formula(tot, col, f"SUM({a1(first, col)}:{a1(tot - 1, col)})", fmt_name)
    w.refs.update({"ageing_units": ref(ws, tot, 2), "ageing_value": ref(ws, tot, 3)})
    w.r += 1
    ro = w.line("Over 180 days, at cost, BHD", None, "g_money3", key="over_180_value",     # 181 to 365, over 365
                formula=f"{a1(band_rows[2], 3)}+{a1(band_rows[3], 3)}")
    w.line("Its share of the value at cost", None, "g_pct", key="over_180_share",
           formula=f'IF({a1(tot, 3)}>0,{a1(ro, S_VALUE)}/{a1(tot, 3)},"")')
    w.words("An item whose units came on two receipts counts in both bands; a return from a van never makes stock "
            "younger.")

    rvw = t.get("review")
    w.section("Ageing review" + (f" (clear target {rvw['clear_days']} days)" if rvw else ""))
    if rvw is None:
        part_line("review", "The ageing review")
    else:
        for status, n in rvw["counts"].items():
            w.line(f"Review: {status}", n, key=f"review_{_key(status)}")
        w.line("Push a week, units (the sales team)", rvw["push_units_week"], key="review_push",
               note=f"Clears the slow and not-moving items within {rvw['clear_days']} days.")

    dm = t.get("damage")
    w.section("Damage store")
    if dm is None:
        part_line("damage", "The damage store")
    else:
        w.line("Units in the damage store", dm["units"], key="damage_units")
        w.line("At landed cost, BHD", dm["value"], "g_money3", key="damage_value",
               note=f"{_plural(dm['uncosted_units'], 'unit')} without a cost." if dm["uncosted_units"] else None)
        month = fmt.as_date(dm.get("month"))
        when = month.strftime("%b %Y") if month else "this month"
        w.line(f"Sent per 100 sold, {when}" + (f" (to {_day(t['as_of'])})" if dm.get("partial") else ""),
               dm["per100"], "g_dec2", key="damage_per100",
               note=None if dm["per100"] is not None else "Nothing sold to divide by.")

    o = t["order"]
    w.section("Order list")
    joins = (f"Items below {o['join_days']} days of cover, ordered up to {o['fill_days']} days."
             if o.get("join_days") is not None and o.get("fill_days") is not None else None)
    w.line("Items", o["items"], key="order_items", note=joins)
    w.line("Units", o["units"], key="order_units")
    w.line("At cost, BHD", o["value"], "g_money3", key="order_value")
    w.line("Items without a cost", o["uncosted_items"], key="order_uncosted", note="Counted, not valued.")
    if parts.get("plan", OK) != OK:
        part_line("plan", "The order plan (each item's supplier)")

    w.r += 1
    if t.get("cut"):
        w.words(f"As on {_day(t['as_of'])}: the stock ledger is cut at the end of that day (it runs to "
                f"{_day(t['ledger_to'])}); every unit is valued at today's cost.")
    w.words("The main store is config 'warehouses.main'; an item is an accessory when it has ever moved in it.")
    w.words(f"Cover: stock at cost ÷ the daily cost of sales of the last {t.get('rate_days', 90)} days; fast movers: "
            f"the best sellers by units over the {t.get('rank_months', 12)} full months before the month.")
    return w.refs


def _whole(x) -> int:
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _month_end(x) -> bool:
    d = fmt.as_date(x)
    return d is not None and (d + timedelta(days=1)).day == 1


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).casefold()).strip("_")


def _check_formula(r: int) -> str:
    plus = "+".join(a1(r, M_COL[k]) for k in CHECK_PLUS)
    minus = "".join(f"-{a1(r, M_COL[k])}" for k in CHECK_MINUS)
    return f"ROUND({plus}{minus},3)"


def _moves_block(ws: Sheet, w: _Lines, totals: dict, block: str, title: str) -> None:
    fmt_name, total_fmt = ("g_int", "g_total_int") if block == "units" else ("g_money3", "g_total_money3")
    w.section(title)
    heads = [("Store", False), ("Kind", False)] + [(MOVE_HEADS[k], True) for k in MOVE_KEYS]
    if block == "bhd":
        heads.append(("Units without a cost", True))
    w.head(heads)
    first = w.r
    last_col = M_UNCOSTED if block == "bhd" else M_COL["check"]
    for s in totals["stores"]:
        r = w.r
        ws.text(r, M_STORE, _said(s["store"]), "g_text")
        ws.text(r, M_KIND, _said(KINDS.get(s["kind"], s["kind"].capitalize())), "g_text")
        for k in MOVE_KEYS:
            if k == "check":
                ws.formula(r, M_COL[k], _check_formula(r), fmt_name)
            else:
                _put(ws, r, M_COL[k], s[block][k], fmt_name)
        if block == "bhd":
            _put(ws, r, M_UNCOSTED, s["uncosted_units"], "g_int")
        w.r += 1
    tot = w.r
    ws.text(tot, M_STORE, "Total", "g_total_int")
    ws.blank(tot, M_KIND, "g_total_int")
    for c in range(M_FIRST, last_col + 1):
        ws.formula(tot, c, f"SUM({a1(first, c)}:{a1(tot - 1, c)})", "g_total_int" if c == M_UNCOSTED else total_fmt)
    ws.cond(f"{a1(first, M_COL['check'])}:{a1(tot, M_COL['check'])}",
            {"type": "cell", "criteria": "not equal to", "value": 0, "format": "down"})    # a check not 0: red
    w.refs[f"{block}_checks"] = ref(ws, first, M_COL["check"], tot - 1)       # the stores' check cells
    w.refs[f"{block}_rows"] = ref(ws, first, M_STORE, tot - 1, last_col)
    for k in MOVE_KEYS:                                                         # the total row, by key
        w.refs[f"{block}_{k}"] = ref(ws, tot, M_COL[k])
    if block == "bhd":
        w.refs["uncosted_units"] = ref(ws, tot, M_UNCOSTED)
    w.r += 1


def movements_sheet(ws: Sheet, totals, heading: str) -> dict:
    """The Movements sheet (see the module docstring); returns the refs of its totals by name. Never raises on the
    totals: failed ones, or ones that are not the contract's shape, are the title and one line."""
    ws.col(M_STORE, M_STORE, 24)
    ws.col(M_KIND, M_KIND, 9)
    ws.col(M_FIRST, M_UNCOSTED, 11)
    _page(ws, landscape=True)
    given = totals if isinstance(totals, Mapping) else {}
    sub = f"Vans and outlets · {_span(given.get('first'), given.get('last'))}"
    purpose = "Totals per store: no item and no voucher is named on this sheet."
    what = "The movements of the vans and outlets"
    if given.get("error") or not isinstance(given.get("stores"), list):
        _title(ws, heading, sub, purpose)
        _failed_line(ws, what, given.get("error"))
        return {}
    return _drawn(ws, lambda: _moves_body(ws, given, heading, purpose), what, heading, sub, purpose)


def _moves_body(ws: Sheet, t: Mapping, heading: str, purpose: str) -> dict:
    basis = f" ({t['basis']})" if t.get("basis") else ""
    _title(ws, heading, f"Vans and outlets · {_span(t['first'], t['last'])} · units, and BHD at landed cost on the "
                        f"/yq-stock basis{basis}", purpose)
    w = _Lines(ws, note_width=11 * 4)
    if t["stores"]:
        _moves_block(ws, w, t, "units", "Units")
        _moves_block(ws, w, t, "bhd", "BHD at landed cost")
    else:
        w.words("No van or outlet held or moved stock in the period.")

    w.section("To check: the transfers")
    ex = t.get("exceptions")
    if ex is None:
        w.words(f"What is to check on the transfers: {NOT_WORKED} (the pairing gave no summary).")
    elif not ex:
        w.words("Nothing to check on the transfers in the period.")
    else:
        w.head([("Kind", False), ("Count", True), ("Units on them", True), ("At cost, BHD", True)])
        first = w.r
        for x in ex:
            r = w.r
            ws.text(r, 0, _said(x["words"]), "g_text")
            _put(ws, r, 1, x["count"], "g_int")
            _put(ws, r, 2, x["units"], "g_int")
            _put(ws, r, 3, x["bhd"], "g_money3")
            w.r += 1
        tot = w.r
        ws.text(tot, 0, "Total", "g_total_int")
        for c, fmt_name in ((1, "g_total_int"), (2, "g_total_int"), (3, "g_total_money3")):
            ws.formula(tot, c, f"SUM({a1(first, c)}:{a1(tot - 1, c)})", fmt_name)
        w.refs.update({"exceptions_count": ref(ws, tot, 1), "exceptions_units": ref(ws, tot, 2),
                       "exceptions_bhd": ref(ws, tot, 3)})
        w.r += 1
        if any(x["bhd"] is None for x in ex):
            w.words("A blank BHD: not valued (its units are counted).")

    w.r += 1
    w.words("Check = opening + received + customer returns + counts + other - back to main - to damage - to other "
            "stores - not received yet - not received - sold - closing: 0 on every row.")
    w.words("Issued to it: what other stores issued to the store; Received: what the store booked in itself. Issued "
            "to it is not in the check.")
    w.words("Not received yet: sent by the store and still inside its deadline; Not received: past it.")
    if t.get("note"):
        w.words(t["note"])
    return w.refs
