"""The Stock & Order Plan workbook: one editable file per data date, for the owner to work on and share.
Built from the stock Model's facts (scripts.ops.stock, stock_plan, stock_review, stock_damage) with
the report engine (book.py: every formula carries its cached value; save never overwrites).

CONTRACT (scripts.ops.stock relies on it)
  day_file(model, force=False) -> dict
      {"path": str | None, "made": bool, "made_at": "HH:MM" | None, "error": str | None, "controls": [str]}
      One file per data date under exports/reports/stock/, named "YQ Stock and Order Plan <YYYY-MM-DD>.xlsx":
      when one exists (any "YQ Stock and Order Plan <date>*.xlsx" there, "~$" and "~build-" names aside) it is
      not built again: made False, the newest such file and its modified time (Bahrain). force takes the next
      free " (2)" ... " (99)", as the monthly report does. A failure (XlsxWriter missing, a formula the
      evaluator refuses, a name taken, a folder that cannot be written) is one sentence in "error", never an
      exception: the screen always prints. "controls" are controls(), one sentence each.
  build_book(model, made=None) -> Book
      The workbook in memory, NOT evaluated: a test can type into a yellow cell first, then evaluate().
  controls(book, model) -> list[str]
      The book evaluated, then every Order row's Suggested set against the screen's quantity, each supplier's
      Items / Units / Value against the screen's sums over the same rows (Your qty blank), and every formula
      that came out as an error value. Nothing differs on a sound build: the list is empty.
  LAYOUT_TAG "stock-plan v1" sits in Notes!B5 (TAG_SHEET, TAG_CELL): release 2 reads it back to know where
  the owner's quantities and comments are.

THE SHEETS (in this order; every table cell has a thin grid border, typed-in cells are yellow)
  Summary      the file, the stock (portal items and units, at selling price, at landed cost), KPI 1 cover,
               the fast movers in stock this month, the order list (Items / Units / Value are formulas on
               the Order sheet's totals, and a block per supplier the same way), the ageing, the damage
               store, the lead time and its basis.
  Order        THE editable sheet. By supplier (C:L): Items = COUNTIFS of Final above nought, Units and Value
               = SUMIFS of Final and Value over the whole item table (the add rows too), a yellow Budget and
               Minimum order, Against budget = Value - Budget, Minimum check. Then the order list, one row
               per plan["order"] row (facts["order"] with supplier Other when the plan is missing), and 15
               rows to add an item by its code (looked up on All items). Joins and Suggested work the
               screen's rule out again from the rate to six decimals and the supplier's Join below / Fill to
               on Notes (INDEX/MATCH), so they equal the screen to the unit; Final = Your qty, else
               Suggested; Value = Final x landed cost to the fils (the screen's rounding). Frozen below the
               item header, a filter on the item table.
  All items    plan["all_items"] (model.items when the plan is missing), with the item's landed cost (the
               add rows look it up); Status coloured. With vans never below nought (information only).
  Fast movers  facts["fast"]["list"] with the days available this month (stock.available_days).
  Ageing       the review rows (with the three stores that sold most: the owner's own file), the cut
               batches, the six month ends; a yellow column for his action.
  Damage       the headline, the twelve months, the items (a yellow Decision list), what was never booked.
  Notes        the layout tag, the settings per supplier (yellow Lead days and Order every; the Order sheet
               reads them), how every figure is worked out, what is estimated, where the data comes from.
  A part that is missing, a stub (built False) or failed (an "error" key) is one plain line on its sheet.

Only the workbook is written, and only under exports/ of the data root (paths.allowed_output).
"""
from __future__ import annotations

import math
import os
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from scripts.ops import fmt, paths
from scripts.ops.workbook import book as bk
from scripts.ops.workbook import formula as fx
from scripts.ops.workbook import style
from scripts.ops.workbook.book import Book, Sheet

TITLE = "YQ Bahrain — Stock & Order Plan"
STEM = "YQ Stock and Order Plan {d}"
LAYOUT_TAG = "stock-plan v1"
TAG_SHEET, TAG_CELL = "Notes", "B5"
SHEETS = ("Summary", "Order", "All items", "Fast movers", "Ageing", "Damage", "Notes")
COMPANY = "YQ Bahrain W.L.L"
OTHER = "Other"                      # the supplier of an item nothing names (stock_plan.OTHER)
ADD_ROWS = 15                        # rows under the order list to add an item by its code
STAR = "★"
DECISIONS = ("Claim from supplier", "Write off", "Repair", "Sell as B-grade", "Keep")
NOT_WORKED = "not worked out this time"
WH_SUFFIX = " - Acc WH"              # a van's store name less this is its salesman (workbook.model.WH_SUFFIX)
DAMAGE_NOTES = (                     # stock_damage.NOTES, when that module does not give its own
    "Counted when sent (the issue leg), so a receipt booked late never hides damage.",
    "Nothing here is booked: claiming from the supplier, writing off, repairing or selling as B-grade is your "
    "decision.",
)
Q3 = Decimal("0.001")
Q6 = Decimal("0.000001")
ZERO = Decimal(0)
ONE = Decimal(1)

# The Order sheet's item table (0-based columns A to T)
(O_SUPPLIER, O_CODE, O_ITEM, O_CATEGORY, O_FAST, O_MAIN, O_VANS, O_WAY, O_SOLD, O_RATE, O_STAR, O_COVER,
 O_JOINS, O_SUGGESTED, O_YOURS, O_FINAL, O_COST, O_VALUE, O_RECEIPT, O_COMMENT) = range(20)
# ... and its By supplier block above it (columns C to J; Minimum check spans J:L)
B_NAME, B_ITEMS, B_UNITS, B_VALUE, B_BUDGET, B_MIN, B_AGAINST, B_CHECK = range(2, 10)
# The settings per supplier on Notes (columns A to G), the header on NOTES_SET_HEAD, one row per supplier after it
S_NAME, S_LEAD, S_EVERY, S_LOW, S_HIGH, S_JOIN, S_FILL = range(7)
NOTES_SET_HEAD = 7
# The All items table (columns A to V), the header on ALL_HEAD
(A_SUPPLIER, A_CODE, A_ITEM, A_CATEGORY, A_STATUS, A_MAIN, A_VANS, A_DAMAGE, A_WAY, A_SOLD30, A_SOLD90,
 A_SOLD365, A_RATE, A_COVER, A_COST, A_VALUE, A_OLDEST, A_LAST_SALE, A_LAST_RECEIPT, A_RANK, A_ACTIVE,
 A_HIDDEN) = range(22)
ALL_HEAD = 4

STATUS_STYLES = (("Sold out", "cf_red"), ("Order now", "cf_amber"), ("No stock, no sale", "cf_grey"),
                 ("No sale 90 d", "cf_grey"), ("OK", "cf_green"))
REVIEW_STYLES = (("Not moving", "cf_red"), ("Slow", "cf_amber"), ("On track", "cf_green"), ("Cleared", "cf_grey"))
PART_ERRORS = (KeyError, TypeError, AttributeError, ValueError, IndexError, ArithmeticError)


# ── small helpers ─────────────────────────────────────────────────────────────

def _stock():
    """scripts.ops.stock, imported when used (it imports this module when it makes the file)."""
    from scripts.ops import stock
    return stock


def _L(c: int) -> str:
    return fx.col_letters(c)


def _n(x):
    """A figure as the file holds it: a Decimal is an int when whole, else a float; None stays a blank."""
    if x is None or isinstance(x, (bool, str, date)):
        return x
    if isinstance(x, Decimal):
        if not x.is_finite():
            return None
        return int(x) if x == x.to_integral_value() else float(x)
    return x


def _put(ws: Sheet, r: int, c: int, value, fmt_name: str) -> None:
    """A plain value in a grid cell; None (or empty text) is a blank that keeps the cell's border."""
    v = _n(value)
    if v is None or v == "":
        ws.blank(r, c, fmt_name)
    elif isinstance(v, str):
        ws.text(r, c, v, fmt_name)
    else:
        ws.put(r, c, v, fmt_name)


def _round6(x) -> Decimal:
    return Decimal(x).quantize(Q6, rounding=ROUND_HALF_UP)


def _day(x) -> str:
    d = fmt.as_date(x)
    return d.strftime("%a %d %b %Y") if d else "n/a"


def _plural(n, one: str, many: str | None = None) -> str:
    return f"{fmt.units(n)} {one if n == 1 else (many or one + 's')}"


def esc(ref: str) -> str:
    """A cell's text as a MATCH / SUMIFS pattern that matches only itself: ~ * ? escaped."""
    return f'SUBSTITUTE(SUBSTITUTE(SUBSTITUTE({ref},"~","~~"),"*","~*"),"?","~?")'


def _lines_needed(text: str, width_units: float) -> int:
    per = max(int(width_units * 1.15), 10)          # Arial 9.5: about 1.15 characters a unit of width
    return max(1, math.ceil(len(text) / per))


def _height(text: str, width_units: float) -> float | None:
    n = _lines_needed(text, width_units)
    return None if n == 1 else 12.6 * n + 3


def _part(model, key: str) -> tuple[dict | None, str | None]:
    """(facts, None) for a part that was worked out; (None, why) for one missing, a stub or one that failed."""
    part = (model.facts or {}).get(key)
    if not isinstance(part, dict) or not part:
        return None, NOT_WORKED
    if "error" in part:
        return None, f"{NOT_WORKED} ({part['error']})"
    if part.get("built") is False:
        return None, NOT_WORKED
    return part, None


def _raise_parts() -> bool:
    return bool(getattr(_stock(), "RAISE_PARTS", False))


def _title(ws: Sheet, model, purpose: str) -> None:
    """The three lines on top of every sheet: the title, the data date and the ledger's day, the purpose."""
    ws.text(0, 0, TITLE, "sheet_title")
    ws.row(0, 21)
    balance = f"stock balance as on {_day(model.stock_as_of)}" if model.stock_as_of else "no stock balance on file"
    ws.text(1, 0, f"Data date {_day(model.d)} · ledger to {_day(model.d)} · {balance}", "sheet_sub")
    ws.text(2, 0, purpose, "note")


def _head(ws: Sheet, r: int, c0: int, labels, height: float | None = 27) -> None:
    """A header row: (text, right-aligned) per column from c0."""
    for i, (text, right) in enumerate(labels):
        ws.text(r, c0 + i, text, "g_head_r" if right else "g_head")
    if height:
        ws.row(r, height)


def _guarded(build, ws: Sheet, model, words: str) -> None:
    """A sheet drawn from another module's facts: if those facts are not what the contract says, the sheet
    says so in one line instead of the whole file failing (stock.RAISE_PARTS: it raises, so tests see it)."""
    before = (set(ws.cells), len(ws.merges), len(ws.conds), len(ws.validations), dict(ws.page), list(ws.cols),
              dict(ws.rows))
    try:
        build(ws, model)
    except PART_ERRORS as exc:
        if _raise_parts():
            raise
        cells, merges, conds, checks, page, cols, rows = before
        for key in [k for k in ws.cells if k not in cells]:
            del ws.cells[key]
        del ws.merges[merges:]
        del ws.conds[conds:]
        del ws.validations[checks:]
        ws.page, ws.cols, ws.rows = page, cols, rows
        ws.text(4, 0, f"{words} could not be put in this file this time ({type(exc).__name__}).", "note")


def _safe(fn, default=None):
    try:
        return fn()
    except PART_ERRORS:
        if _raise_parts():
            raise
        return default


# ── the rows ──────────────────────────────────────────────────────────────────

def order_lines(model) -> tuple[list[dict], bool]:
    """(lines, from the plan): the order list as the Order sheet holds it, one dict per row: supplier, item,
    rate, rate6, cover, qty, cost_bhd. The plan's rows (by supplier) when the plan was worked out and holds
    every row of facts["order"]; else facts["order"] as it is, every row under supplier Other."""
    base = model.facts.get("order") or []
    plan, _why = _part(model, "plan")
    rows = None
    if plan is not None:
        po = plan.get("order")
        if (isinstance(po, list) and len(po) == len(base)
                and all(isinstance(x, dict) and x.get("item") is not None and x.get("supplier") for x in po)):
            rows = po
    from_plan = rows is not None
    if rows is None:
        rows = [dict(x, supplier=OTHER) for x in base]
    out = []
    for x in rows:
        r6 = x.get("rate6")
        out.append({"supplier": " ".join(str(x.get("supplier") or OTHER).split()) or OTHER, "item": x["item"],
                    "rate": x["rate"], "rate6": Decimal(r6) if r6 is not None else _round6(x["rate"].rate),
                    "cover": x.get("cover"), "qty": Decimal(x["qty"]), "cost_bhd": x.get("cost_bhd")})
    return out, from_plan


def _flow_of(model, it):
    fl = getattr(model, "flows", None)
    return fl.get(it.ledger_name) if fl is not None else None


def all_rows(model) -> tuple[list[dict], bool]:
    """(rows, from the plan): plan["all_items"] when the plan was worked out; else every item of the model with
    what scripts.ops.stock has (no supplier but Other, no status)."""
    plan, _why = _part(model, "plan")
    if plan is not None and isinstance(plan.get("all_items"), list):
        return plan["all_items"], True
    st = _stock()
    from scripts.ops.workbook import categories
    d = model.d
    rates = (model.facts.get("fast") or {}).get("rates") or {}
    out = []
    for it in sorted(model.items, key=lambda it: (it.name.casefold(), it.name)):
        flow = _flow_of(model, it)
        rate = rates.get(it.ledger_name)
        chunks = st.allocate(it.on_hand, it.receipts) if it.on_hand > 0 else []
        sold = [max(st.sold_between(it, d - timedelta(days=n - 1), d), ZERO) for n in (30, 90, 365)]
        out.append({
            "item": it, "code": it.code or "", "name": it.name, "category": categories.by_code(it.code or "") or "",
            "supplier": OTHER, "status": "", "main": it.on_hand,
            "with_vans": (flow.sellable_at(d) - it.on_hand) if flow is not None else None,
            "damage": flow.damage_at(d) if flow is not None else None, "on_order": it.on_order,
            "sold_30": sold[0], "sold_90": sold[1], "sold_365": sold[2], "rate": rate,
            "cover": st.cover_of(max(it.on_hand, ZERO), rate.rate) if rate is not None else None,
            "value": (it.on_hand * it.cost if it.on_hand > 0 else ZERO) if it.cost is not None else None,
            "oldest_days": (d - chunks[-1][0]).days if chunks and chunks[-1][0] is not None else None,
            "last_sale": it.last_sale, "last_receipt": it.last_receipt, "rank": it.rank,
            "active": None, "hidden": None, "catalog_only": False})
    return out, False


def _vans(x) -> Decimal | None:
    """With vans as the file shows it: information only, never below nought (a van or ledger timing gap)."""
    if x is None:
        return None
    v = Decimal(x)
    return v if v > 0 else ZERO


def _suppliers(lines: list[dict], rows: list[dict]) -> list[str]:
    """Every supplier the file names, once each (letter case aside: the file's lookups ignore it),
    alphabetically, Other last. The order list's and All items' suppliers, so an added item has its row."""
    seen: dict[str, str] = {}
    for name in [x["supplier"] for x in lines] + [" ".join(str(r.get("supplier") or OTHER).split()) or OTHER
                                                  for r in rows]:
        seen.setdefault(name.casefold(), name)
    names = list(seen.values()) or [OTHER]
    return sorted(names, key=lambda s: (s.casefold() == OTHER.casefold(), s.casefold(), s))


class OrderLayout:
    """Where the Order sheet's parts sit (0-based rows)."""

    def __init__(self, suppliers: list[str], n_lines: int):
        self.suppliers = suppliers
        self.block_head = 4                                       # row 3: the "By supplier" heading
        self.block_rows = {s.casefold(): self.block_head + 1 + i for i, s in enumerate(suppliers)}
        self.total = self.block_head + 1 + len(suppliers)
        self.head = self.total + 3                                # a blank row and the "Order list" heading
        self.first = self.head + 1
        self.rows = [self.first + i for i in range(n_lines)]
        self.add_first = self.first + n_lines
        self.add_last = self.add_first + ADD_ROWS - 1

    def col(self, c: int) -> str:
        """The item table's column c, every row of it (the add rows too), as an absolute range."""
        return f"${_L(c)}${self.first + 1}:${_L(c)}${self.add_last + 1}"


def _settings_rows(suppliers: list[str]) -> dict[str, int]:
    return {s.casefold(): NOTES_SET_HEAD + 1 + i for i, s in enumerate(suppliers)}


# ── the book ──────────────────────────────────────────────────────────────────

def build_book(model, made: datetime | None = None) -> Book:
    """The workbook in memory (not evaluated). Where the Order sheet's rows are goes in book.controls."""
    if made is None:
        from scripts.ops import status
        made = datetime.now(status.BAHRAIN)
    book = Book()
    for name in ("cf_red", "cf_amber", "cf_green", "cf_grey"):
        book.dxf(name)
    book.properties = {"title": f"Stock & Order Plan {model.d.isoformat()}", "subject": "Stock and order plan",
                       "company": COMPANY, "comments": f"layout {LAYOUT_TAG}",
                       "created": made.replace(tzinfo=None, microsecond=0)}
    tabs = {"Summary": style.BLUE, "Notes": style.MUTED}
    sheets = {name: book.sheet(name, tab_color=tabs.get(name)) for name in SHEETS}
    lines, from_plan = order_lines(model)
    rows, rows_from_plan = all_rows(model)
    suppliers = _suppliers(lines, rows)
    lay = OrderLayout(suppliers, len(lines))
    set_rows = _settings_rows(suppliers)
    all_span = (ALL_HEAD + 1, ALL_HEAD + max(len(rows), 1))
    _summary(sheets["Summary"], model, lay, lines, made)
    _order(sheets["Order"], model, lay, lines, rows, set_rows, all_span, from_plan)
    _all_items(sheets["All items"], model, rows, rows_from_plan)
    _fast(sheets["Fast movers"], model, lines)
    ws = sheets["Ageing"]
    _title(ws, model, "Old, unsold and price-cut items: their pace, how soon they clear, and what the sales team "
                      "must sell of them each week.")
    _guarded(_ageing, ws, model, "The ageing review")
    ws = sheets["Damage"]
    _title(ws, model, "The damage store: what it holds, what is sent to it for every 100 units sold, and what was "
                      "sent but never booked in.")
    _guarded(_damage, ws, model, "The damage figures")
    _notes(sheets["Notes"], model, suppliers, set_rows, rows)
    book.controls = {"order": {"rows": list(lay.rows), "suppliers": dict(lay.block_rows), "total": lay.total,
                               "head": lay.head, "add": (lay.add_first, lay.add_last)},
                     "settings": dict(set_rows), "all_items": all_span}
    return book


# ── Summary ───────────────────────────────────────────────────────────────────

def _summary(ws: Sheet, model, lay: OrderLayout, lines: list[dict], made: datetime) -> None:
    st = _stock()
    f, cfg = model.facts, model.cfg
    ws.col(0, 0, 44)
    ws.col(1, 3, 14)
    ws.col(4, 4, 52)
    note_width = 14 + 14 + 52
    _title(ws, model, "The day's stock and order list in short. The order figures follow your quantities on the "
                      "Order sheet.")
    r = 4

    def section(text: str) -> None:
        nonlocal r
        if r > 4:
            r += 1
        ws.text(r, 0, text, "subhead")
        r += 1

    def line(label: str, value=None, fmt_name: str = "g_text", note: str | None = None, formula: str | None = None):
        nonlocal r
        ws.text(r, 0, label, "g_text")
        if formula is not None:
            ws.formula(r, 1, formula, fmt_name)
        else:
            _put(ws, r, 1, value, fmt_name)
        ws.merge(r, 2, r, 4, note or None, "g_text_wrap")
        h = _height(note or "", note_width)
        if h:
            ws.row(r, h)
        r += 1

    def words(text: str) -> None:
        nonlocal r
        ws.text(r, 0, text, "note")
        r += 1

    section("This file")
    line("Data date (the stock ledger's last day)", model.d, "g_date")
    line("Made at (Bahrain)", made.strftime("%a %d %b %Y, %H:%M"))
    line("Stock balance as on", model.stock_as_of, "g_date",
         None if model.stock_as_of else "No stock balance is on file.")
    line("Layout", LAYOUT_TAG, note="Release 2 reads the file back by this tag: keep the sheets and columns where "
                                    "they are.")

    h = f["headline"]
    section(f"Stock in the main store ({_main_name()})")
    if not h.get("found"):
        words("The portal holds no stock of the main store in its newest snapshot.")
    line("Items (the portal's figure)", h["items"], "g_int")
    line("Units (the portal's figure)", h["units"], "g_int")
    line("At selling price, BHD (the portal's figure)", h["sell_bhd"], "g_money3")
    label, basis = st.cost_words({"mrn": h["cost_mrn_items"], "sheet": h["cost_sheet_items"],
                                  "receipt": h["cost_receipt_items"], "none": h["uncosted_items"]},
                                 h["uncosted_sell_bhd"])
    line(f"{label[0].upper() + label[1:]}, BHD", h["cost_bhd"], "g_money3", basis)
    if h.get("portal_cost_bhd") is not None and h["portal_cost_bhd"] != h["cost_bhd"]:
        extra = (f"; {_plural(h['portal_uncosted_items'], 'item')} without a cost there"
                 if h.get("portal_uncosted_items") else "")
        line("The portal at cost, BHD", h["portal_cost_bhd"], "g_money3",
             f"Focus average cost, else the latest landed cost{extra}.")
    if h.get("display_items"):
        line("Display items (in the figures above, nowhere else)", h["display_items"], "g_int",
             f"{fmt.units(h['display_units'])} units: left out of cover, the order list and the ageing.")

    k = f["kpi_cover"]
    now, last = k["now"], k["last"]
    section(f"Cover (KPI 1: {k['low']} to {k['high']} days)")
    days = now["days"]
    was = None
    if last.get("days") is not None:
        was = f"On {_day(k['last_month_end'])}: {st.days_text(last['days'])} days. New arrivals are apart."
    line("Aggregate cover, days", _whole(days), "g_int",
         was if days is not None else "No item with a cost sold in the window: cover cannot be worked out.")
    share = (now["above_value"] / now["value"]) if now["value"] else None
    line(f"Share of the value above {k['high']} days", share, "g_pct",
         f"BHD {fmt.bhd(now['above_value'])} of BHD {fmt.bhd(now['value'])} at cost (or no sale in the window).")

    fa = f["fast"]
    section(f"Fast movers in stock (the {fa['n']} best sellers)")
    a = fa["now"]
    line("This month so far", (Decimal(a["available"]) / Decimal(a["sku_days"])) if a["sku_days"] else None, "g_pct",
         f"Of SKU-days available; every item that sold: {fmt.pct(a['wide_available'], a['wide_sku_days'])}.")
    b = fa["last"]
    line(f"{st.month_name(b['first'])} (that month's fast movers)",
         (Decimal(b["available"]) / Decimal(b["sku_days"])) if b["sku_days"] else None, "g_pct",
         f"Every item that sold: {fmt.pct(b['wide_available'], b['wide_sku_days'])}.")

    section("Order list (follows your quantities on the Order sheet)")
    tot = lay.total + 1
    line("Items", None, "g_int", formula=f"Order!{_L(B_ITEMS)}{tot}")
    line("Units", None, "g_int", formula=f"Order!{_L(B_UNITS)}{tot}")
    line("Value at landed cost, BHD", None, "g_money3", formula=f"Order!{_L(B_VALUE)}{tot}",
         note="Items without a landed cost are counted, not valued.")
    units = sum((x["qty"] for x in lines), ZERO)
    value = sum((x["cost_bhd"] for x in lines if x["cost_bhd"] is not None), ZERO)
    line("As the plan suggests", f"{_plural(len(lines), 'item')}, {fmt.units(units)} units, BHD {fmt.bhd(value)}",
         note="The screen's figures, before your quantities.")
    lead = f["lead"]
    line("Lead time, days", lead["days"], "g_int", _lead_basis(lead))
    line("Joins below, days of sales", f["trigger_days"], "g_int",
         f"The lead time + {cfg.low}, the low end of your KPI band (per supplier on the Notes sheet).")
    line("Filled to, days of sales", f["fill_days"], "g_int", f"The lead time + {cfg.high}, the top of your KPI band.")
    line("Goods on order", _on_order_words(f["on_order"]))
    r += 1
    _head(ws, r, 0, [("Supplier", False), ("Items", True), ("Units", True), ("Value, BHD", True)], None)
    r += 1
    first = r
    for s in lay.suppliers:
        n = lay.block_rows[s.casefold()] + 1
        ws.text(r, 0, s, "g_text")
        ws.formula(r, 1, f"Order!{_L(B_ITEMS)}{n}", "g_int")
        ws.formula(r, 2, f"Order!{_L(B_UNITS)}{n}", "g_int")
        ws.formula(r, 3, f"Order!{_L(B_VALUE)}{n}", "g_money3")
        r += 1
    ws.text(r, 0, "Total", "g_total_int")
    for c, fmt_name in ((1, "g_total_int"), (2, "g_total_int"), (3, "g_total_money3")):
        ws.formula(r, c, f"SUM({_L(c)}{first + 1}:{_L(c)}{r})", fmt_name)
    r += 1

    section("Ageing")
    ag = f["ageing"]
    over = ag["rows"][2]["value"] + ag["rows"][3]["value"]
    line("Value at cost over 180 days, BHD", over, "g_money3", "Units on their newest supplier receipts.")
    line("Its share of the value at cost", (over / ag["value"]) if ag["value"] else None, "g_pct")
    review, why = _part(model, "review")
    counts = _safe(lambda: review["counts"], None) if review is not None else None
    if counts is None:
        words(f"The ageing review was {why or NOT_WORKED}.")
    else:
        for status, _fmt in REVIEW_STYLES:
            line(f"Review: {status}", counts.get(status, 0), "g_int")
        line("Push a week, units (the sales team)", review.get("push_units_week", 0), "g_int",
             f"To clear Slow and Not moving items within {review.get('clear_days', cfg.clear_days)} days.")

    section("Damage store")
    damage, why = _part(model, "damage")
    if damage is None:
        words(f"The damage figures were {why or NOT_WORKED}.")
    elif not damage.get("set_up"):
        words("The damage store is not set up: config 'warehouses.damage' names it.")
    else:
        def damage_lines():
            now_d = damage["now"]
            line("Units in the damage store", now_d["units"], "g_int")
            line("At landed cost, BHD", now_d["value"], "g_money3",
                 f"{fmt.units(now_d['uncosted_units'])} units without a cost." if now_d.get("uncosted_units") else None)
            line("Share of the main store's value", now_d.get("share_of_main"), "g_pct")
            line("Sent this month per 100 sold", _damage_this(damage), "g_dec2")
            tr = damage.get("trend") or {}
            line("Trend", tr.get("word") or "none yet", note=None if tr.get("word") else tr.get("why_none"))
            return True
        if _safe(damage_lines, None) is None:
            words(f"The damage figures were {NOT_WORKED}.")


def _main_name() -> str:
    try:
        from scripts.ops import config
        return str(config.get("warehouses.main"))
    except Exception:  # noqa: BLE001  (a heading's words, never the file)
        return "the main store"


def _lead_basis(lead: dict) -> str:
    if lead.get("basis") == "measured":
        n = len(lead.get("shown") or [])
        return (f"Measured on the last {_plural(n, 'supplier invoice')} (the median of their days to the receipt); "
                f"the setting is {lead.get('default')} days.")
    return ("The setting (config 'stock.lead_time_days'): too few supplier invoices are matched to a receipt to "
            "measure it.")


def _on_order_words(oo: dict) -> str:
    if oo.get("counted"):
        return f"deducted: {_plural(oo['counted'], 'promise line')} of the KPI log"
    return "not deducted (none logged)"


def _damage_this(damage: dict):
    tr = damage.get("trend") or {}
    if tr.get("this") is not None:
        return tr["this"]
    months = damage.get("months") or []
    return months[-1].get("per100") if months else None


# ── Order ─────────────────────────────────────────────────────────────────────

def _order(ws: Sheet, model, lay: OrderLayout, lines: list[dict], rows: list[dict], set_rows: dict,
           all_span: tuple[int, int], from_plan: bool) -> None:
    st = _stock()
    f, cfg = model.facts, model.cfg
    widths = {O_SUPPLIER: 14, O_CODE: 11, O_ITEM: 44, O_CATEGORY: 16, O_FAST: 9, O_MAIN: 11, O_VANS: 11, O_WAY: 11,
              O_SOLD: 11, O_RATE: 10, O_STAR: 3, O_COVER: 9, O_JOINS: 8, O_SUGGESTED: 11, O_YOURS: 10, O_FINAL: 10,
              O_COST: 11, O_VALUE: 12, O_RECEIPT: 12, O_COMMENT: 40}
    for c, w in widths.items():
        ws.col(c, c, w)
    _title(ws, model, "What to order, by supplier. Type your quantity in a yellow cell (leave it blank to take the "
                      "suggestion) and a comment; Final, Value and the totals follow.")

    # By supplier: the totals follow Final and Value over the whole item table, the add rows too
    ws.text(lay.block_head - 1, B_NAME, "By supplier", "subhead")
    _head(ws, lay.block_head, B_NAME, [("Supplier", False), ("Items", True), ("Units", True), ("Value, BHD", True),
                                       ("Budget, BHD", True), ("Minimum order, BHD", True), ("Against budget", True)])
    ws.merge(lay.block_head, B_CHECK, lay.block_head, B_CHECK + 2, "Minimum check", "g_head")
    sup, final, val = lay.col(O_SUPPLIER), lay.col(O_FINAL), lay.col(O_VALUE)
    for s in lay.suppliers:
        r = lay.block_rows[s.casefold()]
        n = r + 1
        crit = f'"="&{esc(f"${_L(B_NAME)}{n}")}'
        ws.text(r, B_NAME, s, "g_text")
        ws.formula(r, B_ITEMS, f'COUNTIFS({sup},{crit},{final},">0")', "g_int")
        ws.formula(r, B_UNITS, f"SUMIFS({final},{sup},{crit})", "g_int")
        ws.formula(r, B_VALUE, f"SUMIFS({val},{sup},{crit})", "g_money3")
        ws.blank(r, B_BUDGET, "input_money3")
        ws.blank(r, B_MIN, "input_money3")
        v, bud, low = f"{_L(B_VALUE)}{n}", f"{_L(B_BUDGET)}{n}", f"{_L(B_MIN)}{n}"
        ws.formula(r, B_AGAINST, f'IF({bud}="","",{v}-{bud})', "g_money3")
        ws.merge(r, B_CHECK, r, B_CHECK + 2, None, "g_text",
                 formula=f'IF({low}="","",IF({v}<{low},"below the minimum",""))')
    t = lay.total
    first, last = lay.block_head + 2, lay.total               # Excel rows of the supplier lines
    ws.text(t, B_NAME, "Total", "g_total_int")
    for c, fmt_name in ((B_ITEMS, "g_total_int"), (B_UNITS, "g_total_int"), (B_VALUE, "g_total_money3")):
        ws.formula(t, c, f"SUM({_L(c)}{first}:{_L(c)}{last})", fmt_name)
    for c in (B_BUDGET, B_MIN, B_AGAINST):
        ws.blank(t, c, "g_total_money3")
    ws.merge(t, B_CHECK, t, B_CHECK + 2, None, "g_total_int")
    against = f"{_L(B_AGAINST)}{first}"
    ws.cond(f"{_L(B_AGAINST)}{first}:{_L(B_AGAINST)}{last}",
            {"type": "formula", "criteria": f"=AND(ISNUMBER({against}),{against}>0)", "format": "cf_red"})
    ws.cond(f"{_L(B_CHECK)}{first}:{_L(B_CHECK)}{last}",
            {"type": "cell", "criteria": "equal to", "value": '"below the minimum"', "format": "cf_amber"})

    # the order list
    ws.text(lay.head - 1, 0, "Order list: type your quantity and a comment in the yellow cells; add an item in the "
                             "yellow Code cells under it", "subhead")
    _head(ws, lay.head, 0, [("Supplier", False), ("Code", False), ("Item", False), ("Category", False),
                            ("Fast", False), ("Main stock", True), ("With vans", True), ("On the way", True),
                            ("Sold 90 d", True), ("A day", True), ("*", False), ("Cover days", True),
                            ("Joins", False), ("Suggested", True), ("Your qty", True), ("Final", True),
                            ("Landed cost", True), ("Value, BHD", True), ("Last receipt", True), ("Comment", False)])
    s1, s2 = min(set_rows.values()) + 1, max(set_rows.values()) + 1
    names = f"Notes!${_L(S_NAME)}${s1}:${_L(S_NAME)}${s2}"
    joins_at = f"Notes!${_L(S_JOIN)}${s1}:${_L(S_JOIN)}${s2}"
    fill_at = f"Notes!${_L(S_FILL)}${s1}:${_L(S_FILL)}${s2}"
    by_item = {row["item"].ledger_name: row for row in rows if row.get("item") is not None}
    F, H, J, M, N, O, P, Q = (_L(c) for c in (O_MAIN, O_WAY, O_RATE, O_JOINS, O_SUGGESTED, O_YOURS, O_FINAL, O_COST))
    for x, r in zip(lines, lay.rows):
        it, rate = x["item"], x["rate"]
        n = r + 1
        info = by_item.get(it.ledger_name) or {}
        match = f"MATCH({esc(f'${_L(O_SUPPLIER)}{n}')},{names},0)"
        pos = f"MAX({F}{n},0)+{H}{n}"
        ws.text(r, O_SUPPLIER, x["supplier"], "g_text")
        _put(ws, r, O_CODE, it.code, "g_text")
        ws.text(r, O_ITEM, it.name, "g_text")
        _put(ws, r, O_CATEGORY, info.get("category") or _category(it.code), "g_text")
        _put(ws, r, O_FAST, STAR if it.rank and it.rank <= cfg.fast_n else None, "g_text")
        _put(ws, r, O_MAIN, it.on_hand, "g_int")
        if info:
            vans = info.get("with_vans")
        else:
            flow = _flow_of(model, it)
            vans = (flow.sellable_at(model.d) - it.on_hand) if flow is not None else None
        _put(ws, r, O_VANS, _vans(vans), "g_int")
        _put(ws, r, O_WAY, it.on_order, "g_int")
        _put(ws, r, O_SOLD, rate.sold, "g_int")
        ws.put(r, O_RATE, float(x["rate6"]), "g_dec2")
        _put(ws, r, O_STAR, "*" if rate.floored else None, "g_text")
        _put(ws, r, O_COVER, x["cover"], "g_int")
        ws.formula(r, O_JOINS, f"AND({J}{n}>0,ROUND({pos}-{J}{n}*INDEX({joins_at},{match}),6)<0)", "g_text")
        ws.formula(r, O_SUGGESTED, f"IF({M}{n},ROUNDUP(ROUND({J}{n}*INDEX({fill_at},{match})-({pos}),6),0),0)",
                   "g_int")
        ws.blank(r, O_YOURS, "input_qty")
        ws.formula(r, O_FINAL, f'IF({O}{n}="",{N}{n},{O}{n})', "g_int")
        _put(ws, r, O_COST, it.cost, "g_money3")
        ws.formula(r, O_VALUE, f'IF({Q}{n}="","",ROUND({P}{n}*{Q}{n},3))', "g_money3")
        _put(ws, r, O_RECEIPT, it.last_receipt, "g_date")
        ws.blank(r, O_COMMENT, "input_note")

    # add an item: its code typed in, the rest looked up on All items
    a1, a2 = all_span

    def on_all(c: int) -> str:
        return f"'All items'!${_L(c)}${a1 + 1}:${_L(c)}${a2 + 1}"

    for r in range(lay.add_first, lay.add_last + 1):
        n = r + 1
        code = f"${_L(O_CODE)}{n}"
        found = f"MATCH({esc(code)},{on_all(A_CODE)},0)"

        def text_of(c: int, missing: str = "") -> str:
            return f'IF({code}="","",IFERROR(INDEX({on_all(c)},{found})&"","{missing}"))'

        def number_of(c: int) -> str:
            return f'IF({code}="","",IFERROR(INDEX({on_all(c)},{found}),""))'

        ws.formula(r, O_SUPPLIER, text_of(A_SUPPLIER), "g_text")
        ws.blank(r, O_CODE, "input_note")
        ws.formula(r, O_ITEM, text_of(A_ITEM, "code not found"), "g_text")
        ws.formula(r, O_CATEGORY, text_of(A_CATEGORY), "g_text")
        ws.blank(r, O_FAST, "g_text")
        ws.formula(r, O_MAIN, number_of(A_MAIN), "g_int")
        for c in (O_VANS, O_WAY, O_SOLD, O_COVER):
            ws.blank(r, c, "g_int")
        ws.blank(r, O_RATE, "g_dec2")
        ws.blank(r, O_STAR, "g_text")
        ws.blank(r, O_JOINS, "g_text")
        ws.put(r, O_SUGGESTED, 0, "g_int")
        ws.blank(r, O_YOURS, "input_qty")
        ws.formula(r, O_FINAL, f'IF({O}{n}="",0,{O}{n})', "g_int")
        ws.formula(r, O_COST, number_of(A_COST), "g_money3")
        ws.formula(r, O_VALUE, f'IF(OR({Q}{n}="",{Q}{n}=0,{P}{n}=0),"",ROUND({P}{n}*{Q}{n},3))', "g_money3")
        ws.blank(r, O_RECEIPT, "g_date")
        ws.blank(r, O_COMMENT, "input_note")
    ws.validate(lay.first, O_YOURS, lay.add_last, O_YOURS,
                {"validate": "integer", "criteria": ">=", "value": 0, "input_title": "Your quantity",
                 "input_message": "Leave blank to take the suggested quantity.", "error_title": "Your quantity",
                 "error_message": "A whole number of units, nought or more."})

    # the rule in plain words, under the table
    r = lay.add_last + 2
    oo = f["on_order"]
    goods = ("goods on order are deducted while they are logged in the KPI log" if oo.get("counted")
             else "goods on order are not deducted unless logged (none is logged)")
    notes = []
    if not lines:
        notes.append(f"No item is below {f['trigger_days']} days of cover today.")
    notes += [
        f"The order list: an item that sold in the last {st.RATE_DAYS} days joins when Main stock + On the way is below "
        f"its supplier's Join below days of its sales (lead time + {cfg.low} = {f['trigger_days']} days now), and "
        f"Suggested fills it to Fill to days (lead time + {cfg.high} = {f['fill_days']} days now), from its rate "
        f"(A day), rounded up to a whole unit; {goods}.",
        "Join below and Fill to are on the Notes sheet, per supplier: change a Lead days cell there and Joins, "
        "Suggested and Final follow. Your qty replaces Suggested; the supplier totals add up Final and Value.",
        "No purchase order is drafted.",
        "Release 2 adds the order rhythm, the season and goods on order when you say an order is placed.",
    ]
    if any(x["rate"].floored for x in lines):
        notes.append(st.FLOOR_NOTE.strip())
    if not from_plan:
        notes.append(f"The order plan was {_part(model, 'plan')[1] or NOT_WORKED}: every item is under supplier Other.")
    for text in notes:
        ws.text(r, 0, text, "note")
        r += 1
    ws.page.update({"freeze": (lay.head + 1, 0), "autofilter": f"A{lay.head + 1}:{_L(O_COMMENT)}{lay.add_last + 1}",
                    "landscape": True, "fit": (1, 0), "repeat_rows": (lay.head, lay.head)})


def _category(code) -> str | None:
    from scripts.ops.workbook import categories
    return categories.by_code(code or "") or None


# ── All items ─────────────────────────────────────────────────────────────────

def _all_items(ws: Sheet, model, rows: list[dict], from_plan: bool) -> None:
    widths = {A_SUPPLIER: 14, A_CODE: 11, A_ITEM: 44, A_CATEGORY: 16, A_STATUS: 18, A_MAIN: 10, A_VANS: 10,
              A_DAMAGE: 9, A_WAY: 10, A_SOLD30: 9, A_SOLD90: 9, A_SOLD365: 9, A_RATE: 9, A_COVER: 9, A_COST: 11,
              A_VALUE: 12, A_OLDEST: 9, A_LAST_SALE: 12, A_LAST_RECEIPT: 12, A_RANK: 8, A_ACTIVE: 8, A_HIDDEN: 8}
    for c, w in widths.items():
        ws.col(c, c, w)
    _title(ws, model, "Every item of the catalog and every item that ever moved in the main store, with its status.")
    plan, why = _part(model, "plan")
    if from_plan and plan is not None:
        counts = plan.get("statuses") or {}
        ws.text(3, 0, "Status: " + "; ".join(f"{s} {fmt.units(n)}" for s, n in counts.items()) + ".", "note")
    else:
        ws.text(3, 0, f"The order plan was {why or NOT_WORKED}: the items of the stock ledger, with no supplier and "
                      "no status.", "note")
    _head(ws, ALL_HEAD, 0, [("Supplier", False), ("Code", False), ("Item", False), ("Category", False),
                            ("Status", False), ("Main stock", True), ("With vans", True), ("Damage", True),
                            ("On the way", True), ("Sold 30 d", True), ("Sold 90 d", True), ("Sold 12 m", True),
                            ("A day", True), ("Cover", True), ("Landed cost", True), ("Value at cost", True),
                            ("Oldest (days)", True), ("Last sale", True), ("Last receipt", True), ("Fast rank", True),
                            ("Active", False), ("Hidden", False)])
    r = ALL_HEAD
    for r, row in enumerate(rows, ALL_HEAD + 1):
        it = row.get("item")
        rate = row.get("rate")
        cells = ((A_SUPPLIER, row.get("supplier"), "g_text"), (A_CODE, row.get("code"), "g_text"),
                 (A_ITEM, row.get("name"), "g_text"), (A_CATEGORY, row.get("category"), "g_text"),
                 (A_STATUS, row.get("status"), "g_text"), (A_MAIN, row.get("main"), "g_int"),
                 (A_VANS, _vans(row.get("with_vans")), "g_int"), (A_DAMAGE, row.get("damage"), "g_int"),
                 (A_WAY, row.get("on_order"), "g_int"), (A_SOLD30, row.get("sold_30"), "g_int"),
                 (A_SOLD90, row.get("sold_90"), "g_int"), (A_SOLD365, row.get("sold_365"), "g_int"),
                 (A_RATE, rate.rate if rate is not None and rate.rate > 0 else None, "g_dec2"),
                 (A_COVER, row.get("cover"), "g_int"),
                 (A_COST, it.cost if it is not None else None, "g_money3"),
                 (A_VALUE, row.get("value"), "g_money3"), (A_OLDEST, row.get("oldest_days"), "g_int"),
                 (A_LAST_SALE, row.get("last_sale"), "g_date"), (A_LAST_RECEIPT, row.get("last_receipt"), "g_date"),
                 (A_RANK, row.get("rank"), "g_int"), (A_ACTIVE, _yes(row.get("active")), "g_text"),
                 (A_HIDDEN, _yes(row.get("hidden")), "g_text"))
        for c, value, fmt_name in cells:
            _put(ws, r, c, value, fmt_name)
    last = max(r, ALL_HEAD + 1)
    status = f"{_L(A_STATUS)}{ALL_HEAD + 2}:{_L(A_STATUS)}{last + 1}"
    for word, name in STATUS_STYLES:
        ws.cond(status, {"type": "cell", "criteria": "equal to", "value": f'"{word}"', "format": name})
    ws.text(last + 2, 0, "With vans: information only (the vans, the outlets and goods on their way, from the stock "
                         "ledger); the order list counts the main store only.", "note")
    ws.page.update({"freeze": (ALL_HEAD + 1, 0), "autofilter": f"A{ALL_HEAD + 1}:{_L(A_HIDDEN)}{last + 1}",
                    "landscape": True, "fit": (1, 0), "repeat_rows": (ALL_HEAD, ALL_HEAD)})


def _yes(flag) -> str | None:
    return None if flag is None else ("Yes" if flag else "No")


# ── Fast movers ───────────────────────────────────────────────────────────────

def _fast(ws: Sheet, model, lines: list[dict]) -> None:
    st = _stock()
    fa = model.facts["fast"]
    now, last = fa["now"], fa["last"]
    for c, w in enumerate((7, 11, 44, 13, 10, 10, 3, 9, 13, 11)):
        ws.col(c, c, w)
    _title(ws, model, f"The {fa['n']} best sellers by units, {st.month_name(fa['rank_first'])} to "
                      f"{st.month_name(fa['rank_last'])}, and how often the main store had them this month.")
    ws.text(3, 0, f"This month so far: {fmt.pct(now['available'], now['sku_days'])} of SKU-days available (every "
                  f"item that sold: {fmt.pct(now['wide_available'], now['wide_sku_days'])}); "
                  f"{st.month_name(last['first'])}: {fmt.pct(last['available'], last['sku_days'])} (that month's "
                  f"fast movers).", "note")
    head = 5
    _head(ws, head, 0, [("Rank", True), ("Code", False), ("Item", False), ("Units sold (12 months)", True),
                        ("Main stock", True), ("A day", True), ("*", False), ("Cover", True),
                        (f"Days available (of {now['days']})", True), ("On the order list", False)])
    listed = {x["item"].ledger_name for x in lines}
    rates = fa["rates"]
    r = head
    for r, it in enumerate(fa["list"], head + 1):
        rate = rates[it.ledger_name]
        here = st.available_days(it, model.series_start, now["first"], now["last"], rate.rate)
        _put(ws, r, 0, it.rank, "g_int")
        _put(ws, r, 1, it.code, "g_text")
        ws.text(r, 2, it.name, "g_text")
        _put(ws, r, 3, it.sold_rank, "g_int")
        _put(ws, r, 4, it.on_hand, "g_int")
        _put(ws, r, 5, rate.rate if rate.rate > 0 else None, "g_dec2")
        _put(ws, r, 6, "*" if rate.floored else None, "g_text")
        _put(ws, r, 7, st.cover_of(max(it.on_hand, ZERO), rate.rate), "g_int")
        _put(ws, r, 8, here, "g_int")
        _put(ws, r, 9, "Yes" if it.ledger_name in listed else None, "g_text")
    notes = []
    if now["out_all"]:
        notes.append("Out every day this month: " + ", ".join(it.name for it in now["out_all"]) + ".")
    if last["out_all"]:
        notes.append(f"Out all of {st.month_name(last['first'])} (that month's fast movers): "
                     + ", ".join(it.name for it in last["out_all"]) + ".")
    if any(rates[it.ledger_name].floored for it in fa["list"]):
        notes.append(st.FLOOR_NOTE.strip())
    if fa.get("partial"):
        notes.append("The ledger starts inside that year: the ranking counts the months it holds.")
    for k, text in enumerate(notes):
        ws.text(r + 2 + k, 0, text, "note")
    ws.page.update({"freeze": (head + 1, 0), "autofilter": f"A{head + 1}:{_L(9)}{max(r, head + 1) + 1}"})


# ── Ageing ────────────────────────────────────────────────────────────────────

def _seller(store) -> str:
    name = " ".join(str(store or "").split())
    return name[: -len(WH_SUFFIX)].strip() if name.endswith(WH_SUFFIX) else name


def _ageing(ws: Sheet, model) -> None:
    review, why = _part(model, "review")
    if review is None:
        ws.text(4, 0, f"The ageing review was {why}.", "note")
        return
    widths = (44, 11, 12, 10, 9, 10, 11, 9, 10, 12, 9, 9, 9, 10, 10, 10, 9, 10, 28, 34, 40)
    for c, w in enumerate(widths):
        ws.col(c, c, w)
    clear = review.get("clear_days") or model.cfg.clear_days
    ws.text(3, 0, f"Clear target {clear} days. The only lever is the sales team: no price cut, no bundle (your rule).",
            "note")
    head = 5
    _head(ws, head, 0, [("Item", False), ("Code", False), ("Status", False), ("Sellable", True), ("Main", True),
                        ("Over 180 d", True), ("Value, BHD", True), ("A week", True), ("Days to clear", True),
                        ("Cut date", True), ("Was", True), ("Now", True), ("Cut %", True), ("Before a week", True),
                        ("Since a week", True), ("Sold since cut", True), ("% sold", True), ("Push a week", True),
                        ("Action", False), ("Sold most by", False), ("Your action / comment", False)])
    rows = review.get("rows") or []
    r = head
    for r, row in enumerate(rows, head + 1):
        it = row["item"]
        cut = row.get("cut") or {}
        sellers = ", ".join(f"{_seller(store)} ({fmt.units(units)})" for store, units in (row.get("sellers") or []))
        cells = ((0, it.name, "g_text"), (1, it.code, "g_text"), (2, row.get("status"), "g_text"),
                 (3, row.get("sellable"), "g_int"), (4, row.get("main"), "g_int"), (5, row.get("over_180"), "g_int"),
                 (6, row.get("value"), "g_money3"), (7, row.get("pace_week"), "g_dec2"),
                 (8, _whole(row.get("days_to_clear")), "g_int"), (9, cut.get("date"), "g_date"),
                 (10, cut.get("was"), "g_money3"), (11, cut.get("now"), "g_money3"), (12, cut.get("pct"), "g_pct"),
                 (13, row.get("before_week"), "g_dec2"), (14, _since_week(row), "g_dec2"),
                 (15, row.get("sold_since_cut"), "g_int"), (16, row.get("pct_sold"), "g_pct"),
                 (17, row.get("push_week"), "g_int"), (18, row.get("action"), "g_text"), (19, sellers, "g_text"))
        for c, value, fmt_name in cells:
            _put(ws, r, c, value, fmt_name)
        ws.blank(r, 20, "input_note")
    last = max(r, head + 1)
    for word, name in REVIEW_STYLES:
        ws.cond(f"C{head + 2}:C{last + 1}", {"type": "cell", "criteria": "equal to", "value": f'"{word}"',
                                              "format": name})
    if not rows:
        ws.text(head + 1, 0, "No item is old, unsold or price-cut today.", "note")
    ws.page.update({"freeze": (head + 1, 0), "autofilter": f"A{head + 1}:{_L(20)}{last + 1}", "landscape": True,
                    "fit": (1, 0)})

    r = last + 2
    batches = review.get("batches") or []
    ws.text(r, 0, "Price cuts in the last 180 days, by cut date", "subhead")
    r += 1
    if not batches:
        ws.text(r, 0, "No price cut in the last 180 days.", "note")
        r += 1
    else:
        _head(ws, r, 0, [("Cut", False), ("Items", True), ("Not moving", True), ("Slow", True), ("On track", True),
                         ("Cleared", True), ("Stock at cut", True), ("Sold since", True), ("% sold", True),
                         ("Before a week", True), ("Since a week", True), ("Change (times)", True)])
        r += 1
        for b in batches:
            counts = b.get("counts") or {}
            cells = ((0, f"Cut of {_day(b['date'])}", "g_text"), (1, b.get("items"), "g_int"),
                     (2, counts.get("Not moving", 0), "g_int"), (3, counts.get("Slow", 0), "g_int"),
                     (4, counts.get("On track", 0), "g_int"), (5, counts.get("Cleared", 0), "g_int"),
                     (6, b.get("stock_at_cut"), "g_int"), (7, b.get("sold"), "g_int"), (8, b.get("pct_sold"), "g_pct"),
                     (9, b.get("before_week"), "g_dec2"), (10, b.get("since_week"), "g_dec2"),
                     (11, b.get("change"), "g_dec2"))
            for c, value, fmt_name in cells:
                _put(ws, r, c, value, fmt_name)
            r += 1
    r += 1
    trend = review.get("trend") or []
    ws.text(r, 0, "Value over 180 days at the last six month ends", "subhead")
    r += 1
    _head(ws, r, 0, [("Month end", False), ("Over 180 d, BHD", True), ("Value, BHD", True), ("Share", True)])
    r += 1
    for t in trend:
        for c, value, fmt_name in ((0, _day(t.get("month_end")), "g_text"), (1, t.get("over_180_value"), "g_money3"),
                                   (2, t.get("value"), "g_money3"), (3, t.get("share"), "g_pct")):
            _put(ws, r, c, value, fmt_name)
        r += 1
    now = review.get("now")
    if isinstance(now, dict):
        for c, value, fmt_name in ((0, f"Now ({_day(model.d)})", "g_text"), (1, now.get("over_180_value"), "g_money3"),
                                   (2, now.get("value"), "g_money3"), (3, now.get("share"), "g_pct")):
            _put(ws, r, c, value, fmt_name)
        r += 1
    r += 1
    for text in (f"A week = units sold since the item's price cut when it fell in the last 120 days, else in the last "
                 f"30 days; days to clear = sellable / that pace; Push a week = sellable / {clear} x 7, rounded up, for "
                 "Slow and Not moving items.",
                 "Sellable = the main store, the vans, the outlets and goods moving between them; damage and samples "
                 "left out. Sold most by = the three stores that sold the item most in 90 days (your own file).",
                 "The only lever is the sales team: no price cut, no bundle (your rule)."):
        ws.text(r, 0, text, "note")
        r += 1


def _whole(x):
    if x is None:
        return None
    return int(Decimal(x).quantize(ONE, rounding=ROUND_HALF_UP))


def _since_week(row: dict):
    if row.get("since_week") is not None:
        return row["since_week"]
    if row.get("pace_basis") == "cut":
        return row.get("pace_week")
    return None


# ── Damage ────────────────────────────────────────────────────────────────────

def _damage(ws: Sheet, model) -> None:
    damage, why = _part(model, "damage")
    for c, w in enumerate((44, 11, 12, 12, 12, 12, 26, 22)):
        ws.col(c, c, w)
    if damage is None:
        ws.text(4, 0, f"The damage figures were {why}.", "note")
        return
    if not damage.get("set_up"):
        ws.text(4, 0, "The damage store is not set up yet: config 'warehouses.damage' names the Focus damage "
                      "warehouse.", "note")
        ws.text(5, 0, "Until it is, nothing is counted here; the rest of the file does not depend on it.", "note")
        return
    from scripts.ops import stock_damage
    now = damage.get("now") or {}
    r = 4
    ws.text(r - 1, 0, f"Damage store: {damage.get('store') or 'n/a'}", "subhead")
    tr = damage.get("trend") or {}
    uncosted = now.get("uncosted_units")
    head_rows = (("Units in the damage store", now.get("units"), "g_int", None),
                 ("At landed cost, BHD", now.get("value"), "g_money3",
                  f"{fmt.units(uncosted)} units without a cost are counted, not valued." if uncosted else None),
                 ("Share of the main store's value", now.get("share_of_main"), "g_pct", None),
                 ("Sent this month per 100 sold", _damage_this(damage), "g_dec2", None),
                 ("Trend", tr.get("word") or "none yet", "g_text", None if tr.get("word") else tr.get("why_none")),
                 ("Sent and not booked in (7 days on), units", damage.get("not_booked_units"), "g_int", None))
    for label, value, fmt_name, note in head_rows:
        ws.text(r, 0, label, "g_text")
        _put(ws, r, 1, value, fmt_name)
        if note:
            ws.text(r, 2, note, "note")
        r += 1
    if not damage.get("found"):
        ws.text(r, 0, "The ledger has never moved stock in the damage store.", "note")
        r += 1
    r += 1
    ws.text(r, 0, "The last twelve months (sent = units issued to the damage store, on the issue day)", "subhead")
    r += 1
    _head(ws, r, 0, [("Month", False), ("Sold", True), ("Sent to damage", True), ("From the main store", True),
                     ("From elsewhere", True), ("Per 100 sold", True), ("Booked in", True)])
    r += 1
    for m in damage.get("months") or []:
        label = m["month"].strftime("%b %Y") + (" (part)" if m.get("partial") else "")
        for c, value, fmt_name in ((0, label, "g_text"), (1, m.get("sold"), "g_int"), (2, m.get("sent"), "g_int"),
                                   (3, m.get("sent_main"), "g_int"), (4, m.get("sent_elsewhere"), "g_int"),
                                   (5, m.get("per100"), "g_dec2"), (6, m.get("booked"), "g_int")):
            _put(ws, r, c, value, fmt_name)
        r += 1
    r += 1
    items = damage.get("by_item") or []
    quality = getattr(stock_damage, "QUALITY_WORDS", "to check with the supplier")
    company = damage.get("company_per100_90")
    ws.text(r, 0, "By item: in the damage store, or sent in the last 90 days" +
            (f" (the company: {fmt.bhd(company, 2)} per 100 sold)" if company is not None else ""), "subhead")
    r += 1
    _head(ws, r, 0, [("Item", False), ("Code", False), ("In damage", True), ("Value, BHD", True), ("Sent 90 d", True),
                     ("Per 100 sold", True), ("Quality", False), ("Decision", False)])
    head = r
    r += 1
    for x in items:
        it = x["item"]
        for c, value, fmt_name in ((0, it.name, "g_text"), (1, it.code, "g_text"), (2, x.get("in_store"), "g_int"),
                                   (3, x.get("value"), "g_money3"), (4, x.get("sent_90"), "g_int"),
                                   (5, x.get("per100_90"), "g_dec2"),
                                   (6, quality if x.get("quality_check") else None, "g_text")):
            _put(ws, r, c, value, fmt_name)
        ws.blank(r, 7, "input_note")
        r += 1
    if items:
        ws.validate(head + 1, 7, r - 1, 7, {"validate": "list", "source": list(DECISIONS), "input_title": "Decision",
                                            "input_message": "Your decision: nothing is booked from this file.",
                                            "error_title": "Decision",
                                            "error_message": "Pick one of the list."})
        ws.page["autofilter"] = f"A{head + 1}:H{r}"
    else:
        ws.text(r, 0, "No item is in the damage store or was sent in the last 90 days.", "note")
        r += 1
    r += 1
    ws.text(r, 0, "Sent and not booked in the damage store 7 days on (to check)", "subhead")
    r += 1
    missing = damage.get("not_booked") or []
    if missing:
        _head(ws, r, 0, [("Item", False), ("Code", False), ("Units", True), ("Last sent", True)])
        r += 1
        for x in missing:
            it = x["item"]
            for c, value, fmt_name in ((0, it.name, "g_text"), (1, it.code, "g_text"), (2, x.get("units"), "g_int"),
                                       (3, x.get("last_sent"), "g_date")):
                _put(ws, r, c, value, fmt_name)
            r += 1
    else:
        ws.text(r, 0, "Nothing: every unit sent was booked in.", "note")
        r += 1
    r += 1
    for text in getattr(stock_damage, "NOTES", DAMAGE_NOTES) or DAMAGE_NOTES:
        ws.text(r, 0, text, "note")
        r += 1


# ── Notes ─────────────────────────────────────────────────────────────────────

def _notes(ws: Sheet, model, suppliers: list[str], set_rows: dict, rows: list[dict]) -> None:
    st = _stock()
    f, cfg = model.facts, model.cfg
    lead = f["lead"]
    ws.col(0, 0, 30)
    ws.col(1, 6, 13)
    ws.col(7, 7, 40)
    text_width = 6 * 13 + 40
    _title(ws, model, "How every figure is worked out, the settings per supplier, and what is estimated.")
    tr, tc = fx.parse_cell(TAG_CELL)
    ws.text(tr, tc - 1, "Layout of this file", "g_text")
    ws.text(tr, tc, LAYOUT_TAG, "g_text")

    ws.text(NOTES_SET_HEAD - 1, 0, "Settings per supplier (the Order sheet reads these cells)", "subhead")
    _head(ws, NOTES_SET_HEAD, 0, [("Supplier", False), ("Lead days", True), ("Order every (days)", True),
                                  ("Safety low (days)", True), ("Top up to (days)", True), ("Join below (days)", True),
                                  ("Fill to (days)", True)])
    for s in suppliers:
        r = set_rows[s.casefold()]
        n = r + 1
        ws.text(r, S_NAME, s, "g_text")
        ws.put(r, S_LEAD, int(lead["days"]), "input_qty")
        ws.put(r, S_EVERY, 0, "input_qty")
        ws.put(r, S_LOW, int(cfg.low), "g_int")
        ws.put(r, S_HIGH, int(cfg.high), "g_int")
        b, c, d, e = (f"{_L(k)}{n}" for k in (S_LEAD, S_EVERY, S_LOW, S_HIGH))
        ws.formula(r, S_JOIN, f"{b}+{c}+{d}", "g_int")
        ws.formula(r, S_FILL, f"{b}+{c}+{e}", "g_int")
    s_first, s_last = min(set_rows.values()), max(set_rows.values())
    ws.validate(s_first, S_LEAD, s_last, S_EVERY, {"validate": "integer", "criteria": ">=", "value": 0,
                                                    "error_title": "Days",
                                                    "error_message": "A whole number of days, nought or more."})
    r = s_last + 1
    basis_words = _lead_basis(lead)
    for text in (f"Lead days start at the lead time the screen uses, {lead['days']} days: "
                 f"{basis_words[0].lower() + basis_words[1:]}",
                 "Safety low and Top up to are your KPI band (config 'stock.cover_kpi_days').",
                 "Order every is 0 until release 2 adds the order rhythm. Join below = lead days + order every + "
                 "safety low; Fill to = lead days + order every + top up to.",
                 "Budget and Minimum order (Order sheet) are yours to type: Against budget = Value - Budget (above "
                 "nought, in red: over the budget); Minimum check says when the value is below the minimum."):
        ws.text(r, 0, text, "note")
        r += 1

    def block(title: str, entries) -> None:
        nonlocal r
        r += 1
        ws.text(r, 0, title, "subhead")
        r += 1
        for label, text in entries:
            ws.text(r, 0, label, "g_text_wrap")
            ws.merge(r, 1, r, 7, text, "g_text_wrap")
            h = _height(text, text_width)
            if h:
                ws.row(r, h)
            r += 1

    oo = f["on_order"]
    clear = cfg.clear_days
    negative = sum(1 for row in rows if row.get("with_vans") is not None and Decimal(row["with_vans"]) < 0)
    vans_words = (f"With vans is for information only (vans, outlets and goods on their way, from the stock ledger); "
                  f"where the ledger shows them below nought it reads 0 ("
                  + (_plural(negative, "item") if negative else "no item") + "). The order list counts the main store "
                  "only (your choice) until the movement audit counts the vans.")
    basis = [b.split(": ", 1)[1] if b.startswith("Basis: ") else b for b in st.BASIS_LINES]
    basis = [b[:1].upper() + b[1:] for b in basis]
    lead_words = _lead_basis(lead)
    block("How each figure is worked out", (
        ("Items and units", basis[0] + " Display items (config 'stock.display_items') stay in the stock figures and "
                                       "are left out of cover, the order list, the fast movers and the ageing."),
        ("At landed cost", basis[1]),
        ("A day (the daily rate)", f"Units sold in the {st.RATE_DAYS} days to the data date / the days of that window "
                                   f"on which the main store held the item at the day's end, never fewer than "
                                   f"{st.RATE_MIN_DAYS}. The Order sheet holds it to six decimals, as the screen works "
                                   f"the order out, so the two agree to the unit."),
        ("Cover days", "Main stock + On the way / the daily rate (Main stock below nought counts as nought)."),
        ("Order list", f"An item that sold in the last {st.RATE_DAYS} days joins when Main stock + On the way is below "
                       f"Join below days of its daily rate; Suggested brings it to Fill to days, rounded up to a whole "
                       f"unit. On the way = goods on order logged in the KPI log (one promise line per item), until a "
                       f"receipt of the item is dated after them; now {_on_order_words(oo)}. No purchase order is "
                       f"drafted."),
        ("Your quantity", "Your qty replaces Suggested when you type one (a whole number, nought or more); blank takes "
                          "Suggested. Final = your quantity, else Suggested; Value = Final x landed cost, to the fils; "
                          "the supplier totals add up Final and Value, and the Summary sheet reads them."),
        ("Add an item", "Type its code in a yellow Code cell under the order list and your quantity beside it: the "
                        "item, its supplier, category, main stock and landed cost come from the All items sheet "
                        "(\"code not found\" when it is not there)."),
        ("With vans", vans_words),
        ("Lead time", f"{lead['days']} days, {lead_words[0].lower() + lead_words[1:]}"),
        ("Fast movers", f"The {cfg.fast_n} best sellers by units over the {st.RANK_MONTHS} full months before this "
                        f"month (display items out). Days available = the days this month on which the main store's "
                        f"day-end balance covered one day of the item's rate."),
        ("Status (All items)", "The first that holds: Display / Not in main store yet (catalog only) / New arrival "
                               "(first received in the last 90 days) / Sold out (none on hand, sold in 90 days) / "
                               "Order now (on the order list) / No stock, no sale / No sale 90 d / Overstock (cover "
                               "over 180 days) / OK."),
        ("Ageing review", f"Old (over 180 days on its supplier receipt), unsold (no sale in 90 days) or price-cut (in "
                          f"the last 180 days) items. Status: Cleared, Not moving, On track (clears within the clear "
                          f"target, {clear} days) or Slow; Push a week = sellable / {clear} x 7, rounded up. The only "
                          f"lever is the sales team: no price cut, no bundle (your rule)."),
        ("Damage", "Counted when sent (the issue leg), so a receipt booked late never hides damage; per 100 sold = "
                   "units sent / units sold x 100. Units sent and not booked in the damage store 7 days later are "
                   "listed to check. Nothing here is booked: claiming from the supplier, writing off, repairing or "
                   "selling as B-grade is your decision."),
    ))
    block("What is estimated", (
        ("A starred rate (*)", f"In stock on fewer than {st.RATE_MIN_DAYS} of the last {st.RATE_DAYS} days: the rate "
                               f"divides by {st.RATE_MIN_DAYS}, so it may understate the demand of an item that was out "
                               f"most of the window."),
        ("Ageing", "Units on hand are set on the item's newest supplier receipts first; a return from a van never "
                   "makes stock younger; units no receipt explains are counted apart."),
        ("Damage", "Counted when it is sent, before the damage store books it in."),
        ("Lead time", "One lead time for every supplier until release 2 measures each one."),
    ))
    unread = f.get("unread") or []
    defaults = f.get("defaults_used") or []
    parts = [f"{words}: {why}" for key, words in (("plan", "The order plan"), ("review", "The ageing review"),
                                                   ("damage", "The damage figures"))
             for why in [_part(model, key)[1]] if why]
    entries = [("Data date", f"{_day(model.d)} (the stock ledger's last day)"),
               ("Stock ledger", getattr(model, "ledger_note", None) or f"To {_day(model.d)}; where it was read from is "
                                                                       "on the /yq-stock screen."),
               ("Stock balance", f"As on {_day(model.stock_as_of)}" if model.stock_as_of else "None on file")]
    if defaults:
        entries.append(("Settings not in config.json", ", ".join(f"'{k}'" for k in defaults) + ": the plan's value "
                                                                                              "stands in."))
    if unread:
        entries.append(("Not read this time", "; ".join(unread) + "."))
    if parts:
        entries.append(("Not worked out", "; ".join(parts) + "."))
    for k, text in enumerate(list(model.notes or []) + list(model.warnings or [])):
        entries.append(("Note" if k == 0 else "", text))
    block("This file", entries)
    ws.page.update({"fit": (1, 0)})


# ── the checks ────────────────────────────────────────────────────────────────

def _decimal(v) -> Decimal | None:
    if v is None:
        return ZERO
    if isinstance(v, bool) or isinstance(v, (str, fx.Err)):
        return None
    if isinstance(v, float):
        return Decimal(repr(v)) if math.isfinite(v) else None
    return Decimal(v)


def _same(got, want, q: Decimal = ONE) -> bool:
    g = _decimal(got)
    return g is not None and g.quantize(q, rounding=ROUND_HALF_UP) == Decimal(want).quantize(q, rounding=ROUND_HALF_UP)


def _shown(v, money: bool = False) -> str:
    if isinstance(v, fx.Err):
        return v.code
    g = _decimal(v)
    if g is None:
        return repr(v)
    return f"BHD {fmt.bhd(g)}" if money else fmt.units(g.quantize(Q3, rounding=ROUND_HALF_UP).normalize())


def controls(book: Book, model) -> list[str]:
    """The book evaluated; the file's figures set against the screen's (see the module docstring)."""
    out: list[str] = []
    errors = book.evaluate()
    pos = (book.controls or {}).get("order") if isinstance(book.controls, dict) else None
    if pos:
        lines, _from_plan = order_lines(model)
        ws = book.get("Order")

        def at(r: int, c: int):
            cell = ws.cells.get((r, c))
            return None if cell is None else cell.value

        for x, r in zip(lines, pos["rows"]):
            got, want = at(r, O_SUGGESTED), x["qty"]
            if not _same(got, want):
                label = x["item"].code or x["item"].name
                out.append(f"Order sheet: {label} suggests {_shown(got)} in the file, {fmt.units(want)} on the screen")
        for key, r in pos["suppliers"].items():
            mine = [x for x in lines if x["supplier"].casefold() == key]
            name = (ws.cells.get((r, B_NAME)).value if ws.cells.get((r, B_NAME)) else key)
            items = Decimal(len(mine))
            units = sum((x["qty"] for x in mine), ZERO)
            value = sum((x["cost_bhd"] for x in mine if x["cost_bhd"] is not None), ZERO)
            for what, c, want, q, money in (("items", B_ITEMS, items, ONE, False), ("units", B_UNITS, units, ONE, False),
                                            ("value", B_VALUE, value, Q3, True)):
                got = at(r, c)
                if not _same(got, want, q):
                    screen = f"BHD {fmt.bhd(want)}" if money else fmt.units(want)
                    out.append(f"Order sheet: {name} {what} {_shown(got, money)} in the file, {screen} on the screen")
    for e in errors[:5]:                            # "Order!M12 = #N/A (AND(...)" -> "Order!M12 came out as #N/A"
        where, _sep, rest = e.partition(" = ")
        out.append(f"{where} came out as {rest.split(' (', 1)[0]}")
    if len(errors) > 5:
        out.append(f"{len(errors) - 5} more formula(s) came out as an error value")
    return out


# ── the file ──────────────────────────────────────────────────────────────────

_COPY = re.compile(r" \((\d+)\)\.xlsx$", re.I)


def _copy_number(name: str) -> int:
    m = _COPY.search(name)
    return int(m.group(1)) if m else 1


def _mtime(p: Path) -> int:
    try:
        return os.stat(bk._long(p)).st_mtime_ns
    except OSError:
        return 0


def existing(folder: Path, base: str) -> list[Path]:
    """The day's workbooks already made ("<base>*.xlsx"; Excel's "~$" lock files and the engine's "~build-"
    temporary files aside), oldest first (by modified time, then copy number)."""
    if not folder.is_dir():
        return []
    low = base.casefold()
    found = []
    for p in folder.iterdir():
        name = p.name
        if name.startswith("~$") or name.startswith("~build-"):
            continue
        if name.casefold().startswith(low) and name.casefold().endswith(".xlsx") and p.is_file():
            found.append(p)
    return sorted(found, key=lambda p: (_mtime(p), _copy_number(p.name), p.name))


def target_path(folder: Path, base: str) -> Path:
    """The first free name: <base>.xlsx, then <base> (2).xlsx ... (99) (the monthly report's rule)."""
    for k in range(1, 100):
        p = folder / (base + (f" ({k})" if k > 1 else "") + ".xlsx")
        if not bk.exists(p):
            return p
    raise FileExistsError(f"ninety-nine copies of {base} already exist in {folder}")


def _made(p: Path) -> float:
    """When the file was made: its creation time where the system keeps one (Windows), else its last change. The
    owner saves his edits into the file, so its modified time would show his save, not the build."""
    try:
        st = os.stat(bk._long(p))
    except OSError:
        return 0.0
    born = getattr(st, "st_birthtime", None)
    if born is None and os.name == "nt":
        born = st.st_ctime                        # on Windows st_ctime is the creation time
    return born or st.st_mtime


def _clock(p: Path) -> str:
    from scripts.ops import status
    return datetime.fromtimestamp(_made(p), status.BAHRAIN).strftime("%H:%M")


def _failed(words: str) -> dict:
    return {"path": None, "made": False, "made_at": None, "error": words, "controls": []}


def _short(exc: Exception, width: int = 160) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def day_file(model, force: bool = False) -> dict:
    """The day's workbook: made once per data date (see the module docstring). Never raises for a file that
    cannot be made: that is one sentence in "error"."""
    from scripts.ops import status
    folder = paths.reports_dir("stock")
    base = STEM.format(d=model.d.isoformat())
    try:
        made_before = existing(folder, base)
    except OSError as exc:
        return _failed(f"exports/reports/stock could not be read ({type(exc).__name__})")
    if made_before and not force:
        newest = made_before[-1]
        return {"path": str(newest), "made": False, "made_at": _clock(newest), "error": None, "controls": []}
    try:
        import xlsxwriter  # noqa: F401  (Book.save needs it: say so before anything is built)
    except ImportError:
        return _failed("XlsxWriter is not installed on this PC (pip install xlsxwriter)")
    try:
        target = target_path(folder, base)
    except FileExistsError:
        return _failed(f"ninety-nine copies of {base} already exist in exports/reports/stock")
    if not paths.allowed_output(target):
        return _failed(f"{target} is outside exports/ and business_data/, the only places scripts/ops writes")
    made = datetime.now(status.BAHRAIN)
    try:
        book = build_book(model, made)
        book.evaluate()                             # every formula carries its value before the checks and the save
        checks = controls(book, model)
        book.save(target)
    except ImportError:
        return _failed("XlsxWriter is not installed on this PC (pip install xlsxwriter)")
    except fx.FormulaError as exc:
        return _failed(f"a formula could not be worked out ({_short(exc)})")
    except FileExistsError as exc:
        if bk.exists(target):                       # Book.save found the name taken: someone else's file
            return _failed("a file of that name appeared while this one was written; /yq-stock file makes the "
                           "next one")
        why = f": {exc.strerror}" if getattr(exc, "strerror", None) else ""
        return _failed(f"it could not be written in exports/reports/stock ({type(exc).__name__}{why})")
    except OSError as exc:
        why = f": {exc.strerror}" if getattr(exc, "strerror", None) else ""
        return _failed(f"it could not be written in exports/reports/stock ({type(exc).__name__}{why})")
    except ValueError as exc:
        return _failed(f"the workbook engine refused it ({_short(exc)})")
    return {"path": str(target), "made": True, "made_at": made.strftime("%H:%M"), "error": None, "controls": checks}
