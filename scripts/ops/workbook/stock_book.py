"""The Stock & Order Plan workbook: one editable file per data date, for the owner to work on and share.
Built from the stock Model's facts (scripts.ops.stock, stock_plan, stock_review, stock_damage) with
the report engine (book.py: every formula carries its cached value; save never overwrites).

CONTRACT (scripts.ops.stock relies on it)
  day_file(model, force=False) -> dict
      {"path": str | None, "made": bool, "made_at": "HH:MM" | None, "error": str | None, "controls": [str]}
      One file per data date under exports/reports/stock/, named "YQ Stock and Order Plan <YYYY-MM-DD>.xlsx":
      when one exists (any "YQ Stock and Order Plan <date>*.xlsx" there; "~$" and "~build-" names and the team
      copies, " - Team" in the name, aside) and the latest of them (the highest copy number, then the latest
      save: readback.newest_plan's order) carries today's figures stamp (Notes!B6, figures_digest), it is not
      built again: made False, that file and the time it was made (Bahrain). When its stamp differs, or it holds
      none (an older layout, a file that is no workbook), the next free name is built (made True) and the first
      control line says why: "the figures changed since <made at>: a new file". Without openpyxl nothing can be
      compared and the file stands. force takes the next free " (2)" ... " (99)", as the monthly report does. A
      failure (XlsxWriter missing, a formula the evaluator refuses, a name taken, a folder that cannot be
      written) is one sentence in "error", never an exception: the screen always prints. "controls" are
      controls(), one sentence each.
  figures_digest(model) -> str | None     the figures stamp: 16 hex digits (see the function)
  read_stamp(path) -> (bool, str | None)  a made file's stamp, read with openpyxl
  build_book(model, made=None) -> Book
      The workbook in memory, NOT evaluated: a test can type into a yellow cell first, then evaluate().
  controls(book, model) -> list[str]
      The book evaluated, then every Order row's Suggested set against the screen's quantity, each supplier's
      Items / Units / Value against the screen's sums over the same rows (Your qty blank), in a release-2 file
      each supplier's Join / Fill season days on Notes against stock_season.season_days, and every formula
      that came out as an error value. Nothing differs on a sound build: the list is empty.
  release2(model) -> dict | None
      Release 2's facts when scripts.ops.stock gave them (facts["season"] with its "lifts" and
      facts["supplier_rules"]), else None: the file is then release 1's cell for cell, but for the figures stamp
      (a hidden row) and the Ageing sheet's guarded paces (below).
  The layout tag sits in Notes!B5 (TAG_SHEET, TAG_CELL): LAYOUT_TAG "stock-plan v1" without release 2's facts,
  LAYOUT_TAG_V2 "stock-plan v2" with them. The file is read back by it (where the owner's quantities and
  comments are): keep the Order sheet's header texts and the rows to add an item as they are. The figures stamp
  sits in Notes!B6 (STAMP_CELL), its row hidden (height 0), in both layouts.

RELEASE 2 (layout "stock-plan v2", the owner's decisions of 20-Oct-2026): the order rhythm (an order to a supplier
about once a month), the season (a lift per calendar month) and goods on the way. An item joins when its demand
from the data date reaches its position (Main stock, never below nought, + On the way) within lead + rhythm + the
low end of the KPI band, each day's demand being the rate x the lift of that day's month; it is topped up to lead
+ rhythm + the high end of that demand. With no rhythm and every lift 1.00 it is release 1's rule.
  facts["season"]          {"lifts": {1..12: {"lift": Decimal (2 dp), "basis", "days", "year"}}, "warn": [month],
                            "applied": bool}
  facts["supplier_rules"]  {supplier: {"lead", "lead_basis", "every", "low", "high"[, "trig_days", "fill_days"]},
                            "*": the rule of any other supplier}; a supplier it does not name takes its own lead
                            time from facts["leads"] and its rhythm from config 'stock.supplier_every_days' where
                            they are set, else the "*" rule (stock.build fills the rules of the order's suppliers only)
  facts["on_the_way"]      stock_orders.fifo(): {"lines", "logged", "counted", "unmatched", "log"}
  order rows               + "rate_basis" ("90 days" | "longer history from <date>") and "pack" (units a carton)
  Notes: the settings per supplier (rows 9 on, then one row "Any other supplier" for the "*" rule) gain Join
  season days (H) and Fill season days (I) = SUMPRODUCT(the window's days in each calendar month, the lifts);
  the LIFTS table (Jan to Dec in B to M, yellow, two decimals, a "Measured on" row and a "Stock-outs" row: "stock-
  outs held this month down" for facts["season"]["warn"]); the window table: a fixed cell with the data date, then
  each supplier's Join below and Fill to days in each month, formulas of that date, the settings and the month
  (month_days: this year's and next year's month m against (d, d + MIN(days, 365)], so a window of up to a year is
  exact even when d's own month comes round again). A yellow cell typed in recomputes this file only.
  Order: Joins = AND(Rate > 0, ROUND(ROUND(Pos - Rate x Join season days, 8), 6) < 0); Suggested =
  IF(Joins, ROUNDUP(MAX(ROUND(ROUND(Rate x Fill season days - Pos, 8), 6), 0), 0), 0), in whole cartons where
  Carton is above 1 (rule_formulas). A 6-decimal rate times a 2-decimal season day count has 8 decimals: rounded
  to 8 first, the evaluator's binary arithmetic gives Python's exact Decimal figure, then stock_plan's one rule
  (round6, then up). The supplier lookups fall back to the "*" row: IFERROR(MATCH(...), its position), so a
  Supplier cell typed over never shows #N/A. The columns Rate basis (U) and Carton (V) come after Comment; nothing
  before them moves. All items: Supplier from (W, the row's "supplier_basis"; blank without it).
  On the way (after Order): the logged lines still due, by supplier; one plain line when nothing is logged.
  Ageing (both layouts): Before / Since a week as the screen guards them: "not in stock before" (in stock on fewer
  than 30 of the 90 days before the cut), "too few before" (no pace to compare), "too soon" (a cut too recent);
  a cut batch's Change in the same words; never a multiple read off a pace that says nothing.

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
               item header, a filter on the item table. Release 2: the season days and Rate basis, Carton.
  On the way   (release 2 only) the orders he logged that are still due, by supplier (stock_orders.fifo).
  All items    plan["all_items"] (model.items when the plan is missing), with the item's landed cost (the
               add rows look it up); Status coloured. With vans never below nought (information only).
  Fast movers  facts["fast"]["list"] with the days available this month (stock.available_days).
  Ageing       the review rows (with the three stores that sold most: the owner's own file), the cut
               batches, the six month ends; a yellow column for his action.
  Damage       the headline, the twelve months, the items (a yellow Decision list), what was never booked.
  Notes        the layout tag, the settings per supplier (yellow Lead days and Order every; the Order sheet
               reads them), release 2's lifts and windows, how every figure is worked out, what is estimated,
               where the data comes from.
  A part that is missing, a stub (built False) or failed (an "error" key) is one plain line on its sheet.

Only the workbook is written, and only under exports/ of the data root (paths.allowed_output).
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from scripts.ops import fmt, paths, stock_season
from scripts.ops.workbook import book as bk
from scripts.ops.workbook import formula as fx
from scripts.ops.workbook import style
from scripts.ops.workbook.book import Book, Sheet

TITLE = "YQ Bahrain — Stock & Order Plan"
STEM = "YQ Stock and Order Plan {d}"
LAYOUT_TAG = "stock-plan v1"                  # release 1's layout (no release-2 facts)
LAYOUT_TAG_V2 = "stock-plan v2"               # the rhythm, the season and goods on the way
LAYOUT_TAGS = (LAYOUT_TAG, LAYOUT_TAG_V2)
TAG_SHEET, TAG_CELL = "Notes", "B5"
STAMP_CELL = "B6"                             # the figures stamp (Notes, in a hidden row): day_file reads it back
TEAM_MARK = " - Team"                         # readback.TEAM_MARK: a team copy's name holds it (never the day's file)
ANY_SUPPLIER = "Any other supplier"           # release 2: the settings row of the "*" rule
SHEETS = ("Summary", "Order", "All items", "Fast movers", "Ageing", "Damage", "Notes")
SHEETS_V2 = ("Summary", "Order", "On the way", "All items", "Fast movers", "Ageing", "Damage", "Notes")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WINDOW_CAP = 365                              # a window counts at most a year of days
WARN_WORDS = "stock-outs held this month down"
EMPTY_LOG = ("Nothing logged yet: when you place an order, say so (/yq-stock ordered <supplier>) and it shows here "
             "until the goods arrive.")
LONGER_NOTE = ("~ out of stock most of the last 90 days: the rate of a longer history (back to the first window "
               "holding 30 days in stock, up to a year).")      # stock.LONGER_NOTE, when that module has none
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
Q2 = Decimal("0.01")
Q3 = Decimal("0.001")
Q6 = Decimal("0.000001")
ZERO = Decimal(0)
ONE = Decimal(1)

# The Order sheet's item table (0-based columns A to T)
(O_SUPPLIER, O_CODE, O_ITEM, O_CATEGORY, O_FAST, O_MAIN, O_VANS, O_WAY, O_SOLD, O_RATE, O_STAR, O_COVER,
 O_JOINS, O_SUGGESTED, O_YOURS, O_FINAL, O_COST, O_VALUE, O_RECEIPT, O_COMMENT) = range(20)
O_RATE_BASIS, O_CARTON = 20, 21       # release 2: after Comment (U, V)
# ... and its By supplier block above it (columns C to J; Minimum check spans J:L)
B_NAME, B_ITEMS, B_UNITS, B_VALUE, B_BUDGET, B_MIN, B_AGAINST, B_CHECK = range(2, 10)
# The settings per supplier on Notes (columns A to G; release 2 to I), the header on NOTES_SET_HEAD, one row per
# supplier after it
S_NAME, S_LEAD, S_EVERY, S_LOW, S_HIGH, S_JOIN, S_FILL = range(7)
S_JSEASON, S_FSEASON = 7, 8           # release 2: Join season days, Fill season days
NOTES_SET_HEAD = 7
M_FIRST, M_LAST, M_TOTAL = 1, 12, 13  # release 2's month tables on Notes: Jan to Dec in B to M, a window's days in N
# The On the way sheet (release 2), the header on W_HEAD
(W_SUPPLIER, W_CODE, W_ITEM, W_DATE, W_QTY, W_RECEIVED, W_DUE, W_EXPECTED, W_LATE, W_REF) = range(10)
W_HEAD = 4
# The All items table (columns A to V), the header on ALL_HEAD
(A_SUPPLIER, A_CODE, A_ITEM, A_CATEGORY, A_STATUS, A_MAIN, A_VANS, A_DAMAGE, A_WAY, A_SOLD30, A_SOLD90,
 A_SOLD365, A_RATE, A_COVER, A_COST, A_VALUE, A_OLDEST, A_LAST_SALE, A_LAST_RECEIPT, A_RANK, A_ACTIVE,
 A_HIDDEN) = range(22)
A_SUPPLIER_FROM = 22                  # release 2: where the item's supplier comes from (after Hidden, W)
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


# ── release 2 ─────────────────────────────────────────────────────────────────

def release2(model) -> dict | None:
    """Release 2's facts (see the module docstring), or None: without the season and the rules of the suppliers
    the file is release 1's. {"lifts": the facts' lifts, "table": {month: the lift the rule uses}, "warn":
    [month], "applied": bool, "rules": facts["supplier_rules"], "on_the_way": facts["on_the_way"] or None}."""
    f = getattr(model, "facts", None) or {}
    season, rules = f.get("season"), f.get("supplier_rules")
    if not isinstance(season, dict) or not isinstance(rules, dict) or not rules:
        return None
    if isinstance(season.get("error"), str) or isinstance(rules.get("error"), str):
        return None
    lifts = season.get("lifts")
    if not isinstance(lifts, dict):
        return None
    warn = set()
    for m in season.get("warn") or []:
        try:
            if 1 <= int(m) <= 12:
                warn.add(int(m))
        except (TypeError, ValueError):
            continue
    return {"lifts": lifts, "table": {m: stock_season.lift_of(lifts, date(2000, m, 1)) for m in range(1, 13)},
            "warn": sorted(warn), "applied": bool(season.get("applied")), "rules": rules,
            "on_the_way": f.get("on_the_way")}


def _named(table, name: str):
    """The entry of a supplier in a {supplier: row} table, its letter case aside ("*" is never a supplier's)."""
    if not isinstance(table, dict):
        return None
    key = name.casefold()
    for s, row in table.items():
        if s != stock_season.OTHER and isinstance(row, dict) and " ".join(str(s).split()).casefold() == key:
            return row
    return None


def _rule_of(model, r2: dict, name: str) -> dict:
    """A supplier's rule as the Notes sheet holds it: {"lead", "lead_basis", "every", "low", "high", "trig_days",
    "fill_days"} (whole days). facts["supplier_rules"] of the supplier; else its own lead time (facts["leads"])
    and rhythm (config, stock.Settings.supplier_every) where they are set over the rule of any other supplier
    ("*"), as scripts.ops.stock works a supplier's rule out."""
    f, cfg = model.facts, model.cfg
    lead = f.get("lead") or {}

    def whole(rule: dict) -> dict:
        out = {"lead": int(rule.get("lead", lead.get("days", 0))),
               "lead_basis": rule.get("lead_basis") or lead.get("basis"),
               "every": int(rule.get("every", 0)), "low": int(rule.get("low", cfg.low)),
               "high": int(rule.get("high", cfg.high))}
        out["trig_days"] = int(rule.get("trig_days", out["lead"] + out["every"] + out["low"]))
        out["fill_days"] = int(rule.get("fill_days", out["lead"] + out["every"] + out["high"]))
        return out

    own = _named(r2["rules"], name)
    if own is not None:
        return whole(own)
    star = r2["rules"].get(stock_season.OTHER)
    base = whole(star if isinstance(star, dict) else {"every": getattr(cfg, "every_days", 0)})
    measured = _named(f.get("leads"), name)
    every = next((v for s, v in (getattr(cfg, "supplier_every", None) or {}).items()
                  if " ".join(str(s).split()).casefold() == name.casefold()), None)
    if measured is None and every is None:
        return base
    out = dict(base)
    if measured is not None and measured.get("days") is not None:
        out["lead"], out["lead_basis"] = int(measured["days"]), measured.get("basis") or base["lead_basis"]
    if every is not None:
        out["every"] = int(every)
    out["trig_days"] = out["lead"] + out["every"] + out["low"]
    out["fill_days"] = out["lead"] + out["every"] + out["high"]
    return out


def _longer(x: dict) -> bool:
    """The row's rate is the rate of a longer history (stock_season.long_rate), not the 90 days'."""
    return str(x.get("rate_basis") or "").startswith("longer")


# ── the figures stamp ─────────────────────────────────────────────────────────

def _canon(x) -> str | None:
    """A figure as one text whatever its type or trailing noughts (1, 1.0 and Decimal("1.00") are all "1")."""
    if x is None or isinstance(x, (bool, str)):
        return x if not isinstance(x, bool) else str(x)
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    try:
        v = x if isinstance(x, Decimal) else Decimal(str(x))
    except (ArithmeticError, ValueError):
        return str(x)
    return format(v.normalize(), "f") if v.is_finite() else str(v)


def _digest(model) -> str:
    r2 = release2(model)
    lines, _from_plan = order_lines(model)
    rows, _rows_from_plan = all_rows(model)
    data: dict = {"layout": LAYOUT_TAG_V2 if r2 is not None else LAYOUT_TAG, "d": model.d.isoformat(), "order": [
        [x["supplier"], x["item"].code or x["item"].ledger_name, _canon(x["qty"]),
         _canon(max(x["item"].on_hand, ZERO) + x["item"].on_order), _canon(x["rate6"]), _canon(x["item"].cost),
         _pack(x.get("pack")), x.get("rate_basis") or ""] for x in lines]}
    if r2 is None:
        f = model.facts
        data["rule"] = [_canon(f["lead"]["days"]), _canon(f.get("trigger_days")), _canon(f.get("fill_days"))]
    else:
        names = _suppliers(lines, rows) + [stock_season.OTHER]
        data["rules"] = [[s, [_rule_of(model, r2, s)[k] for k in ("lead", "every", "low", "high", "trig_days",
                                                                  "fill_days")]] for s in names]
        data["lifts"] = [_canon(r2["table"][m]) for m in range(1, 13)]
        ow = r2.get("on_the_way")
        data["way"] = ([[ln.get("ref"), ln.get("code"), _canon(ln.get("qty")), _canon(ln.get("received")),
                         _canon(ln.get("due")), _canon(ln.get("date")), _canon(ln.get("expected_by"))]
                        for ln in ow.get("lines") or [] if isinstance(ln, dict)]
                       if isinstance(ow, dict) else None)
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def figures_digest(model) -> str | None:
    """The figures stamp: 16 hexadecimal digits of the sha256 of what the Order sheet rests on (the layout, the
    data date, every order row's supplier, code, quantity, position, rate, landed cost, carton and rate basis, the
    settings of every supplier the file names, and in release 2 the lifts and the goods on the way). A same-date
    reload, an order logged or a cost loaded later moves it; the owner's typing never does (it is not in the
    model). None when the facts are not what the contract says (stock.RAISE_PARTS: it raises)."""
    return _safe(lambda: _digest(model), None)


def read_stamp(path) -> tuple[bool, str | None]:
    """(compared, stamp): the figures stamp of a workbook made before (Notes!B6, read with openpyxl). (True, None)
    for a file that holds none (an older layout) or cannot be read as a workbook; (False, None) when openpyxl is
    not installed, so nothing can be compared and the file stands."""
    try:
        import openpyxl
    except ImportError:
        return False, None
    wb = None
    try:
        wb = openpyxl.load_workbook(bk._long(Path(path)), read_only=True, data_only=True)
        if TAG_SHEET not in wb.sheetnames:
            return True, None
        value = wb[TAG_SHEET][STAMP_CELL].value
        text = str(value).strip() if value is not None else ""
        return True, (text or None)
    except Exception:  # noqa: BLE001  (a file that is no workbook, or one half written: it holds no stamp)
        return True, None
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:  # noqa: BLE001
                pass


def _pack(value) -> int:
    """Units a carton (config 'stock.pack_sizes'): a whole number, 0 for none."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        v = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return 0
    return int(v) if v.is_finite() and v == v.to_integral_value() and v > 0 else 0


# ── the rows ──────────────────────────────────────────────────────────────────

def order_lines(model) -> tuple[list[dict], bool]:
    """(lines, from the plan): the order list as the Order sheet holds it, one dict per row: supplier, item,
    rate, rate6, cover, qty, cost_bhd, rate_basis, pack. The plan's rows (by supplier) when the plan was worked
    out and holds every row of facts["order"]; else facts["order"] as it is, every row under supplier Other
    (release 2: under the supplier the row names, whose rule its quantity rests on)."""
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
        own = release2(model) is not None
        rows = [dict(x, supplier=(x.get("supplier") or OTHER) if own else OTHER) for x in base]
    out = []
    for x in rows:
        r6 = x.get("rate6")
        out.append({"supplier": " ".join(str(x.get("supplier") or OTHER).split()) or OTHER, "item": x["item"],
                    "rate": x["rate"], "rate6": Decimal(r6) if r6 is not None else _round6(x["rate"].rate),
                    "cover": x.get("cover"), "qty": Decimal(x["qty"]), "cost_bhd": x.get("cost_bhd"),
                    "rate_basis": x.get("rate_basis"), "pack": x.get("pack")})
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
    r2 = release2(model)
    tag = LAYOUT_TAG_V2 if r2 is not None else LAYOUT_TAG
    book = Book()
    for name in ("cf_red", "cf_amber", "cf_green", "cf_grey"):
        book.dxf(name)
    book.properties = {"title": f"Stock & Order Plan {model.d.isoformat()}", "subject": "Stock and order plan",
                       "company": COMPANY, "comments": f"layout {tag}",
                       "created": made.replace(tzinfo=None, microsecond=0)}
    tabs = {"Summary": style.BLUE, "Notes": style.MUTED}
    sheets = {name: book.sheet(name, tab_color=tabs.get(name)) for name in (SHEETS_V2 if r2 is not None else SHEETS)}
    lines, from_plan = order_lines(model)
    rows, rows_from_plan = all_rows(model)
    suppliers = _suppliers(lines, rows)
    lay = OrderLayout(suppliers, len(lines))
    set_rows = _settings_rows(suppliers)
    all_span = (ALL_HEAD + 1, ALL_HEAD + max(len(rows), 1))
    _summary(sheets["Summary"], model, lay, lines, made, r2)
    _order(sheets["Order"], model, lay, lines, rows, set_rows, all_span, from_plan, r2)
    if r2 is not None:
        ws = sheets["On the way"]
        _title(ws, model, "The orders you logged (/yq-stock ordered) whose goods are still due, by supplier, until "
                          "the main store's receipts cover them.")
        _guarded(lambda sheet, m: _on_the_way(sheet, m, r2), ws, model, "Goods on the way")
    _all_items(sheets["All items"], model, rows, rows_from_plan, r2)
    _fast(sheets["Fast movers"], model, lines)
    ws = sheets["Ageing"]
    _title(ws, model, "Old, unsold and price-cut items: their pace, how soon they clear, and what the sales team "
                      "must sell of them each week.")
    _guarded(_ageing, ws, model, "The ageing review")
    ws = sheets["Damage"]
    _title(ws, model, "The damage store: what it holds, what is sent to it for every 100 units sold, and what was "
                      "sent but never booked in.")
    _guarded(_damage, ws, model, "The damage figures")
    stamp = figures_digest(model) or ""
    season = _notes(sheets["Notes"], model, suppliers, set_rows, rows, r2, stamp)
    book.controls = {"order": {"rows": list(lay.rows), "suppliers": dict(lay.block_rows), "total": lay.total,
                               "head": lay.head, "add": (lay.add_first, lay.add_last)},
                     "settings": dict(set_rows), "all_items": all_span, "stamp": stamp}
    if r2 is not None:
        book.controls["layout"] = tag
        book.controls["order"]["columns"] = {"rate_basis": O_RATE_BASIS, "carton": O_CARTON}
        book.controls["season"] = season
    return book


# ── Summary ───────────────────────────────────────────────────────────────────

def _summary(ws: Sheet, model, lay: OrderLayout, lines: list[dict], made: datetime, r2: dict | None = None) -> None:
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
    if r2 is None:
        line("Layout", LAYOUT_TAG, note="Release 2 reads the file back by this tag: keep the sheets and columns "
                                        "where they are.")
    else:
        line("Layout", LAYOUT_TAG_V2, note="/yq-stock ordered reads your quantities back by this tag: keep the sheets "
                                           "and columns where they are.")

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
    if r2 is None:
        line("Joins below, days of sales", f["trigger_days"], "g_int",
             f"The lead time + {cfg.low}, the low end of your KPI band (per supplier on the Notes sheet).")
        line("Filled to, days of sales", f["fill_days"], "g_int",
             f"The lead time + {cfg.high}, the top of your KPI band.")
        line("Goods on order", _on_order_words(f["on_order"]))
    else:
        star = _rule_of(model, r2, stock_season.OTHER)
        line("Order every, days", star["every"], "g_int",
             "The order rhythm: the days to the next order to the supplier (each supplier's on the Notes sheet).")
        line("Joins below, days of sales", star["trig_days"], "g_int",
             f"The lead time + order every + {star['low']}, the low end of your KPI band; each day at its month's "
             f"lift (per supplier on the Notes sheet).")
        line("Filled to, days of sales", star["fill_days"], "g_int",
             f"The lead time + order every + {star['high']}, the top of your KPI band.")
        line("Season", "on" if r2["applied"] else "off", note=_season_words(r2))
        line("Goods on the way", _way_words(r2.get("on_the_way")))
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


def _season_words(r2: dict) -> str:
    """The season in a sentence: its lifts in short, or that every lift is 1.00."""
    if not r2["applied"]:
        return "Every lift is 1.00: a day is a day (the lifts are on the Notes sheet)."
    lifts = ", ".join(f"{MONTHS[m - 1]} {r2['table'][m]:.2f}" for m in range(1, 13))
    held = (f" Held down by stock-outs: {', '.join(MONTHS[m - 1] for m in r2['warn'])}." if r2["warn"] else "")
    return f"Each day counts at its month's lift: {lifts} (Notes).{held}"


def _due(x: dict) -> Decimal:
    try:
        return Decimal(x.get("due") or 0)
    except (ArithmeticError, TypeError, ValueError):
        return ZERO


def _way_words(ow) -> str:
    """Goods on the way in a few words (facts["on_the_way"], stock_orders.fifo)."""
    if not isinstance(ow, dict) or isinstance(ow.get("error"), str) or "lines" not in ow:
        return NOT_WORKED
    lines = ow.get("lines") or []
    if not lines and not ow.get("logged"):
        return "nothing logged yet (/yq-stock ordered <supplier> logs an order you placed)"
    due = [x for x in lines if _due(x) > 0]
    if not due:
        return f"every logged order has arrived ({_plural(len(lines), 'line')})"
    words = f"{_plural(ow.get('counted') or 0, 'line')} still due, deducted (the On the way sheet)"
    n = ow.get("unmatched") or 0
    if n:
        words += f"; {_plural(n, 'line')} {'matches' if n == 1 else 'match'} no item"
    return words


def _damage_this(damage: dict):
    tr = damage.get("trend") or {}
    if tr.get("this") is not None:
        return tr["this"]
    months = damage.get("months") or []
    return months[-1].get("per100") if months else None


# ── Order ─────────────────────────────────────────────────────────────────────

def _order(ws: Sheet, model, lay: OrderLayout, lines: list[dict], rows: list[dict], set_rows: dict,
           all_span: tuple[int, int], from_plan: bool, r2: dict | None = None) -> None:
    st = _stock()
    f, cfg = model.facts, model.cfg
    widths = {O_SUPPLIER: 14, O_CODE: 11, O_ITEM: 44, O_CATEGORY: 16, O_FAST: 9, O_MAIN: 11, O_VANS: 11, O_WAY: 11,
              O_SOLD: 11, O_RATE: 10, O_STAR: 3, O_COVER: 9, O_JOINS: 8, O_SUGGESTED: 11, O_YOURS: 10, O_FINAL: 10,
              O_COST: 11, O_VALUE: 12, O_RECEIPT: 12, O_COMMENT: 40}
    if r2 is not None:
        widths.update({O_RATE_BASIS: 28, O_CARTON: 8})
    last_col = O_CARTON if r2 is not None else O_COMMENT
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
    labels = [("Supplier", False), ("Code", False), ("Item", False), ("Category", False),
              ("Fast", False), ("Main stock", True), ("With vans", True), ("On the way", True),
              ("Sold 90 d", True), ("A day", True), ("*", False), ("Cover days", True),
              ("Joins", False), ("Suggested", True), ("Your qty", True), ("Final", True),
              ("Landed cost", True), ("Value, BHD", True), ("Last receipt", True), ("Comment", False)]
    if r2 is not None:
        labels += [("Rate basis", False), ("Carton", True)]
    _head(ws, lay.head, 0, labels)
    s1, s2 = min(set_rows.values()) + 1, max(set_rows.values()) + 1
    if r2 is not None:
        s2 += 1                               # the row of any other supplier ("*"), right after the suppliers'
    names = f"Notes!${_L(S_NAME)}${s1}:${_L(S_NAME)}${s2}"
    join_col, fill_col = (S_JSEASON, S_FSEASON) if r2 is not None else (S_JOIN, S_FILL)
    joins_at = f"Notes!${_L(join_col)}${s1}:${_L(join_col)}${s2}"
    fill_at = f"Notes!${_L(fill_col)}${s1}:${_L(fill_col)}${s2}"
    by_item = {row["item"].ledger_name: row for row in rows if row.get("item") is not None}
    F, H, J, M, N, O, P, Q = (_L(c) for c in (O_MAIN, O_WAY, O_RATE, O_JOINS, O_SUGGESTED, O_YOURS, O_FINAL, O_COST))
    V = _L(O_CARTON)
    for x, r in zip(lines, lay.rows):
        it, rate = x["item"], x["rate"]
        n = r + 1
        info = by_item.get(it.ledger_name) or {}
        match = f"MATCH({esc(f'${_L(O_SUPPLIER)}{n}')},{names},0)"
        if r2 is not None:                    # a supplier Notes does not name takes the rule of any other supplier
            match = f"IFERROR({match},{s2 - s1 + 1})"
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
        if r2 is None:
            _put(ws, r, O_STAR, "*" if rate.floored else None, "g_text")
        else:
            _put(ws, r, O_STAR, "~" if _longer(x) else ("*" if rate.floored else None), "g_text")
        _put(ws, r, O_COVER, x["cover"], "g_int")
        if r2 is None:
            ws.formula(r, O_JOINS, f"AND({J}{n}>0,ROUND({pos}-{J}{n}*INDEX({joins_at},{match}),6)<0)", "g_text")
            ws.formula(r, O_SUGGESTED, f"IF({M}{n},ROUNDUP(ROUND({J}{n}*INDEX({fill_at},{match})-({pos}),6),0),0)",
                       "g_int")
        else:
            joins, suggested = rule_formulas(f"{J}{n}", pos, f"INDEX({joins_at},{match})", f"INDEX({fill_at},{match})",
                                             f"{M}{n}", f"{V}{n}")
            ws.formula(r, O_JOINS, joins, "g_text")
            ws.formula(r, O_SUGGESTED, suggested, "g_int")
        ws.blank(r, O_YOURS, "input_qty")
        ws.formula(r, O_FINAL, f'IF({O}{n}="",{N}{n},{O}{n})', "g_int")
        _put(ws, r, O_COST, it.cost, "g_money3")
        ws.formula(r, O_VALUE, f'IF({Q}{n}="","",ROUND({P}{n}*{Q}{n},3))', "g_money3")
        _put(ws, r, O_RECEIPT, it.last_receipt, "g_date")
        ws.blank(r, O_COMMENT, "input_note")
        if r2 is not None:
            _put(ws, r, O_RATE_BASIS, x.get("rate_basis") or f"{st.RATE_DAYS} days", "g_text")
            pack = _pack(x.get("pack"))
            _put(ws, r, O_CARTON, pack if pack > 1 else None, "g_int")

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
        if r2 is not None:
            ws.blank(r, O_RATE_BASIS, "g_text")
            ws.blank(r, O_CARTON, "g_int")
    ws.validate(lay.first, O_YOURS, lay.add_last, O_YOURS,
                {"validate": "integer", "criteria": ">=", "value": 0, "input_title": "Your quantity",
                 "input_message": "Leave blank to take the suggested quantity.", "error_title": "Your quantity",
                 "error_message": "A whole number of units, nought or more."})

    if r2 is not None:
        r = lay.add_last + 2
        for text in _order_notes_v2(model, r2, lines, from_plan):
            ws.text(r, 0, text, "note")
            r += 1
        ws.page.update({"freeze": (lay.head + 1, 0), "autofilter": f"A{lay.head + 1}:{_L(last_col)}{lay.add_last + 1}",
                        "landscape": True, "fit": (1, 0), "repeat_rows": (lay.head, lay.head)})
        return

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


def rule_formulas(rate: str, pos: str, join_days: str, fill_days: str, joins: str, carton: str) -> tuple[str, str]:
    """Release 2's Joins and Suggested of one Order row: (Joins, Suggested) formula texts from the references (or
    expressions) of its rate, its position, its supplier's Join / Fill season days, its own Joins cell and its
    Carton. The rate has 6 decimals and the season days 2, so their product has 8: rounded to 8 first, the binary
    arithmetic gives the exact figure, then stock_plan's one rule (round6, then a whole unit up), in whole cartons
    where Carton is above 1. Nought, never below, as stock_plan.units_up."""
    units = f"ROUNDUP(MAX(ROUND(ROUND({rate}*{fill_days}-({pos}),8),6),0),0)"
    return (f"AND({rate}>0,ROUND(ROUND({pos}-{rate}*{join_days},8),6)<0)",
            f"IF({joins},IF({carton}>1,ROUNDUP({units}/{carton},0)*{carton},{units}),0)")


def _order_notes_v2(model, r2: dict, lines: list[dict], from_plan: bool) -> list[str]:
    """The rule in plain words under the Order sheet's table (release 2)."""
    st = _stock()
    star = _rule_of(model, r2, stock_season.OTHER)
    lift = ", each day at its month's lift," if r2["applied"] else ""
    cartons = any(_pack(x.get("pack")) > 1 for x in lines)
    notes = []
    if not lines:
        notes.append("Nothing joins the order list today: no item's demand over its supplier's Join below days reaches "
                     "its Main stock + On the way.")
    notes += [
        f"The order list: an item that sold in the last {st.RATE_DAYS} days joins when its demand over its supplier's "
        f"Join below days{lift} reaches Main stock + On the way; Suggested tops it up to the demand of its Fill to "
        "days, from its rate (A day), rounded up to a whole unit" + (
            ", then to whole cartons (Carton, config 'stock.pack_sizes')" if cartons else "") + ".",
        f"{ANY_SUPPLIER} (Notes) joins at lead time + order every + {star['low']} = {star['trig_days']} days and is "
        f"filled to lead time + order every + {star['high']} = {star['fill_days']} days; each supplier's own days are "
        "on the Notes sheet.",
        "On the way = the goods of the orders you logged (/yq-stock ordered), until the main store's receipts of the "
        "item dated after the order cover them: the On the way sheet lists them.",
        "Lead days, Order every and the lifts are on the Notes sheet, per supplier: change one there and the season "
        "days, Joins, Suggested and Final follow (this file only). Your qty replaces Suggested; the supplier totals "
        "add up Final and Value.",
        "A longer lead time or rhythm typed on Notes changes the quantities of these rows; items that would join only "
        "come with the next file: tell Claude to keep the new setting.",
        f"A Supplier cell typed over to a name the Notes sheet does not hold takes the rule of {ANY_SUPPLIER.lower()} "
        "(Notes), and its units count in no supplier's total.",
        f"Rate basis: the {st.RATE_DAYS} days, or a longer history for an item out of stock most of them (~). No "
        "purchase order is drafted.",
    ]
    if any(x["rate"].floored and not _longer(x) for x in lines):
        notes.append(st.FLOOR_NOTE.strip())
    if any(_longer(x) for x in lines):
        notes.append(str(getattr(st, "LONGER_NOTE", LONGER_NOTE)).strip())
    if not from_plan:
        notes.append(f"The order plan was {_part(model, 'plan')[1] or NOT_WORKED}: the items are under the supplier "
                     "their rule rests on.")
    return notes


def _category(code) -> str | None:
    from scripts.ops.workbook import categories
    return categories.by_code(code or "") or None


# ── On the way (release 2) ────────────────────────────────────────────────────

def _on_the_way(ws: Sheet, _model, r2: dict) -> None:
    """The logged lines still due (facts["on_the_way"], stock_orders.fifo), by supplier, then oldest first."""
    for c, w in ((W_SUPPLIER, 14), (W_CODE, 11), (W_ITEM, 44), (W_DATE, 12), (W_QTY, 10), (W_RECEIVED, 11),
                 (W_DUE, 10), (W_EXPECTED, 12), (W_LATE, 7), (W_REF, 30)):
        ws.col(c, c, w)
    ow = r2.get("on_the_way")
    if not isinstance(ow, dict) or isinstance(ow.get("error"), str) or "lines" not in ow:
        ws.text(W_HEAD, 0, f"Goods on the way were {NOT_WORKED}.", "note")
        return
    lines = list(ow.get("lines") or [])
    if not lines and not ow.get("logged"):
        ws.text(W_HEAD, 0, EMPTY_LOG, "note")
        return
    due = sorted((x for x in lines if _due(x) > 0),
                 key=lambda x: (str(x.get("supplier") or "").casefold(), fmt.as_date(x.get("date")) or date.min,
                                str(x.get("ref") or "")))
    notes = []
    r = W_HEAD
    if due:
        _head(ws, W_HEAD, 0, [("Supplier", False), ("Code", False), ("Item", False), ("Ordered on", True),
                              ("Ordered", True), ("Received since", True), ("Still due", True), ("Expected by", True),
                              ("Late", False), ("Ref", False)])
        for r, x in enumerate(due, W_HEAD + 1):
            for c, value, fmt_name in ((W_SUPPLIER, x.get("supplier"), "g_text"), (W_CODE, x.get("code"), "g_text"),
                                       (W_ITEM, x.get("item_name") or "no item has this code", "g_text"),
                                       (W_DATE, fmt.as_date(x.get("date")), "g_date"), (W_QTY, x.get("qty"), "g_int"),
                                       (W_RECEIVED, x.get("received"), "g_int"), (W_DUE, x.get("due"), "g_int"),
                                       (W_EXPECTED, fmt.as_date(x.get("expected_by")), "g_date"),
                                       (W_LATE, "yes" if x.get("late") else None, "g_text"),
                                       (W_REF, x.get("ref"), "g_text")):
                _put(ws, r, c, value, fmt_name)
        total = r + 1
        ws.text(total, W_SUPPLIER, "Total", "g_total_int")
        for c in range(W_CODE, W_REF + 1):
            if c in (W_QTY, W_RECEIVED, W_DUE):
                ws.formula(total, c, f"SUM({_L(c)}{W_HEAD + 2}:{_L(c)}{total})", "g_total_int")
            else:
                ws.blank(total, c, "g_total_int")
        late = f"{_L(W_LATE)}{W_HEAD + 2}:{_L(W_LATE)}{total}"
        ws.cond(late, {"type": "cell", "criteria": "equal to", "value": '"yes"', "format": "cf_red"})
        ws.page.update({"freeze": (W_HEAD + 1, 0), "autofilter": f"A{W_HEAD + 1}:{_L(W_REF)}{total}",
                        "landscape": True, "fit": (1, 0)})
        r = total + 1
        late_days = getattr(_orders(), "LATE_DAYS", None)
        notes += [
            "Received since = the main store's supplier receipts of the item dated after the order, the oldest order "
            "paid first; Still due is what the Order sheet's On the way deducts.",
            "Expected by = the order date + the supplier's lead time (the Notes sheet); Late = still due "
            + (f"more than {late_days} days after that" if late_days is not None else "after that") + ", to check.",
        ]
        future = sum(1 for x in due if x.get("future"))
        if future:
            notes.append(f"{_plural(future, 'line')} dated after the data date: the ledger cannot hold "
                         f"{'its' if future == 1 else 'their'} receipt yet.")
        unmatched = sum(1 for x in due if not x.get("item_name"))
        if unmatched:
            notes.append(f"{_plural(unmatched, 'line')} {'matches' if unmatched == 1 else 'match'} no item of the "
                         "main store by its code: due in full, deducted from nothing (check the code).")
    elif lines:
        notes.append(f"Every logged order has arrived: {_plural(len(lines), 'line')}, received in full.")
    arrived = len(lines) - len(due)
    if due and arrived:
        notes.append(f"{_plural(arrived, 'logged line')} received in full {'is' if arrived == 1 else 'are'} not "
                     "shown.")
    unread = int(ow.get("logged") or 0) - len(lines)
    if unread > 0:
        notes.append(f"{_plural(unread, 'logged line')} could not be read (a quantity or an order date): not shown.")
    r += 1
    for text in notes:
        ws.text(r, 0, text, "note")
        r += 1


def _orders():
    from scripts.ops import stock_orders
    return stock_orders


# ── All items ─────────────────────────────────────────────────────────────────

def _all_items(ws: Sheet, model, rows: list[dict], from_plan: bool, r2: dict | None = None) -> None:
    widths = {A_SUPPLIER: 14, A_CODE: 11, A_ITEM: 44, A_CATEGORY: 16, A_STATUS: 18, A_MAIN: 10, A_VANS: 10,
              A_DAMAGE: 9, A_WAY: 10, A_SOLD30: 9, A_SOLD90: 9, A_SOLD365: 9, A_RATE: 9, A_COVER: 9, A_COST: 11,
              A_VALUE: 12, A_OLDEST: 9, A_LAST_SALE: 12, A_LAST_RECEIPT: 12, A_RANK: 8, A_ACTIVE: 8, A_HIDDEN: 8}
    if r2 is not None:
        widths[A_SUPPLIER_FROM] = 15
    last_col = A_SUPPLIER_FROM if r2 is not None else A_HIDDEN
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
    labels = [("Supplier", False), ("Code", False), ("Item", False), ("Category", False),
              ("Status", False), ("Main stock", True), ("With vans", True), ("Damage", True),
              ("On the way", True), ("Sold 30 d", True), ("Sold 90 d", True), ("Sold 12 m", True),
              ("A day", True), ("Cover", True), ("Landed cost", True), ("Value at cost", True),
              ("Oldest (days)", True), ("Last sale", True), ("Last receipt", True), ("Fast rank", True),
              ("Active", False), ("Hidden", False)]
    if r2 is not None:
        labels.append(("Supplier from", False))
    _head(ws, ALL_HEAD, 0, labels)
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
        if r2 is not None:                  # config / catalog / purchase order / none (stock_plan); blank without it
            cells += ((A_SUPPLIER_FROM, row.get("supplier_basis"), "g_text"),)
        for c, value, fmt_name in cells:
            _put(ws, r, c, value, fmt_name)
    last = max(r, ALL_HEAD + 1)
    status = f"{_L(A_STATUS)}{ALL_HEAD + 2}:{_L(A_STATUS)}{last + 1}"
    for word, name in STATUS_STYLES:
        ws.cond(status, {"type": "cell", "criteria": "equal to", "value": f'"{word}"', "format": name})
    ws.text(last + 2, 0, "With vans: information only (the vans, the outlets and goods on their way, from the stock "
                         "ledger); the order list counts the main store only.", "note")
    if r2 is not None:
        ws.text(last + 3, 0, "Supplier from: where the item's supplier comes from: config ('stock.suppliers'), its "
                             "newest purchase order, the catalog's brand, or none (Other).", "note")
    ws.page.update({"freeze": (ALL_HEAD + 1, 0), "autofilter": f"A{ALL_HEAD + 1}:{_L(last_col)}{last + 1}",
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
                 (13, _before_cell(row), "g_dec2"), (14, _since_cell(row), "g_dec2"),
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
            soon = bool(b.get("too_soon"))           # no share sold and no pace since on a few days (the screen's)
            cells = ((0, f"Cut of {_day(b['date'])}", "g_text"), (1, b.get("items"), "g_int"),
                     (2, counts.get("Not moving", 0), "g_int"), (3, counts.get("Slow", 0), "g_int"),
                     (4, counts.get("On track", 0), "g_int"), (5, counts.get("Cleared", 0), "g_int"),
                     (6, b.get("stock_at_cut"), "g_int"), (7, b.get("sold"), "g_int"),
                     (8, None if soon else b.get("pct_sold"), "g_pct"),
                     (9, b.get("before_week"), "g_dec2"), (10, "too soon" if soon else b.get("since_week"), "g_dec2"),
                     (11, _batch_change(b), "g_dec2"))
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
                 f"Before a week reads \"not in stock before\" when the item was in stock on fewer than "
                 f"{_in_stock_before()} of the 90 days before its cut, and \"too few before\" when too few sold in "
                 "them to compare; Since a week reads \"too soon\" for a cut too recent to judge: no pace multiple is "
                 "read off those (the screen's rule).",
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


def _in_stock_before() -> int:
    """stock_review.IN_STOCK_BEFORE_DAYS: a pace before a cut counts only for an item in stock on that many days."""
    from scripts.ops import stock_review
    return int(getattr(stock_review, "IN_STOCK_BEFORE_DAYS", 30))


def _before_cell(row: dict):
    """Before a week, guarded as the screen guards it: no multiple can be read off a pace before that says
    nothing. "not in stock before" (in stock on fewer than 30 of the 90 days before the cut), "too few before"
    (no pace to compare: fewer than 13 sold in them), else the units a week. A too recent cut keeps its pace
    before (its Since a week says "too soon")."""
    if row.get("cut") is None:
        return row.get("before_week")
    days = row.get("in_stock_before_days")
    if days is not None and days < _in_stock_before():
        return "not in stock before"
    if row.get("change") is None and not row.get("too_soon"):
        return "too few before"
    return row.get("before_week")


def _since_cell(row: dict):
    """Since a week, guarded: "too soon" for a cut younger than the review judges (stock_review.CUT_MIN_DAYS)."""
    if row.get("cut") is not None and row.get("too_soon"):
        return "too soon"
    return _since_week(row)


def _batch_change(b: dict):
    """A cut batch's pace since over before, or the screen's words where there is none to give."""
    if b.get("too_soon"):
        return "too soon to judge"
    if b.get("change") is not None:
        return b["change"]
    if not b.get("in_stock_before_items"):
        return "not in stock before"
    return "too few before"


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

def _notes(ws: Sheet, model, suppliers: list[str], set_rows: dict, rows: list[dict],
           r2: dict | None = None, stamp: str = "") -> dict | None:
    """The Notes sheet. Release 2: where its season tables are (book.controls["season"]); else None."""
    st = _stock()
    f, cfg = model.facts, model.cfg
    lead = f["lead"]
    ws.col(0, 0, 30)
    if r2 is None:
        ws.col(1, 6, 13)
        ws.col(7, 7, 40)
        text_width, text_last = 6 * 13 + 40, 7
    else:
        ws.col(M_FIRST, M_TOTAL, 11)
        text_width, text_last = (M_LAST - M_FIRST + 1) * 11, M_LAST
    _title(ws, model, "How every figure is worked out, the settings per supplier, and what is estimated.")
    tr, tc = fx.parse_cell(TAG_CELL)
    ws.text(tr, tc - 1, "Layout of this file", "g_text")
    ws.text(tr, tc, LAYOUT_TAG if r2 is None else LAYOUT_TAG_V2, "g_text")
    sr, sc = fx.parse_cell(STAMP_CELL)              # the figures stamp, in a row kept hidden (height 0)
    ws.text(sr, sc - 1, "Figures stamp (a new file is made when they change)", "g_text")
    ws.text(sr, sc, stamp, "g_text")
    ws.row(sr, 0)
    season = None
    if r2 is not None:
        r, season = _settings_v2(ws, model, suppliers, set_rows, r2)
    else:
        r = _settings_v1(ws, model, suppliers, set_rows)

    def block(title: str, entries) -> None:
        nonlocal r
        r += 1
        ws.text(r, 0, title, "subhead")
        r += 1
        for label, text in entries:
            ws.text(r, 0, label, "g_text_wrap")
            ws.merge(r, 1, r, text_last, text, "g_text_wrap")
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
    how = [
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
    ]
    estimated = [
        ("A starred rate (*)", f"In stock on fewer than {st.RATE_MIN_DAYS} of the last {st.RATE_DAYS} days: the rate "
                               f"divides by {st.RATE_MIN_DAYS}, so it may understate the demand of an item that was out "
                               f"most of the window."),
        ("Ageing", "Units on hand are set on the item's newest supplier receipts first; a return from a van never "
                   "makes stock younger; units no receipt explains are counted apart."),
        ("Damage", "Counted when it is sent, before the damage store books it in."),
        ("Lead time", "One lead time for every supplier until release 2 measures each one."),
    ]
    if r2 is not None:
        how, estimated = _how_v2(model, r2, how, estimated)
    block("How each figure is worked out", how)
    block("What is estimated", estimated)
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
    return season


def _settings_v1(ws: Sheet, model, suppliers: list[str], set_rows: dict) -> int:
    """Release 1's settings per supplier and their notes; the next free row."""
    f, cfg = model.facts, model.cfg
    lead = f["lead"]
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
    return r


LEAD_WORDS = {"measured": "measured on its own supplier invoices",
              "config": "set in config ('stock.supplier_lead_days')",
              "overall": "every supplier's measured lead time", "setting": "the setting ('stock.lead_time_days')"}


def month_days(d_ref: str, days_ref: str, m: int) -> str:
    """The formula of the days of calendar month m in the window (d, d + MIN(days, WINDOW_CAP)]: month m of d's year
    and of the next one (a window of up to a year lies inside those two years), each MAX(0, MIN(its last day, the
    window's last day) - MAX(its first day - 1, d)). DATE(y, m + 1, 0) is the last day of month m, DATE(y, m, 0) the
    day before its first."""
    end = f"{d_ref}+MIN({days_ref},{WINDOW_CAP})"
    return "+".join(f"MAX(0,MIN(DATE({y},{m + 1},0),{end})-MAX(DATE({y},{m},0),{d_ref}))"
                    for y in (f"YEAR({d_ref})", f"YEAR({d_ref})+1"))


def _basis_text(lifts, m: int) -> str | None:
    """What a month's lift rests on, in a few words: the month it was measured on (with its days when the ledger
    holds fewer than the whole month), "no data", or "your setting"."""
    row = lifts.get(m, lifts.get(str(m))) if isinstance(lifts, dict) else None
    if not isinstance(row, dict):
        return None
    basis = row.get("basis")
    if basis == "last year":
        try:
            year, days = int(row.get("year")), int(row.get("days"))
        except (TypeError, ValueError):
            return "last year"
        whole = calendar.monthrange(year, m)[1]
        return f"{MONTHS[m - 1]} {year}" + (f" ({days} d)" if 0 < days < whole else "")
    if basis == "config":
        return "your setting"
    return str(basis) if basis else None


def _settings_v2(ws: Sheet, model, suppliers: list[str], set_rows: dict, r2: dict) -> tuple[int, dict]:
    """Release 2's settings per supplier (and a row for any other supplier), the lifts and the windows (see the
    module docstring); (the next free row, where they are: book.controls["season"])."""
    rules = {s.casefold(): _rule_of(model, r2, s) for s in suppliers}
    rules[stock_season.OTHER] = _rule_of(model, r2, stock_season.OTHER)
    rows_of = dict(set_rows)
    rows_of[stock_season.OTHER] = max(set_rows.values()) + 1           # the "*" rule, after the suppliers
    named = [(s, s.casefold()) for s in suppliers] + [(ANY_SUPPLIER, stock_season.OTHER)]

    ws.text(NOTES_SET_HEAD - 1, 0, "Settings per supplier (the Order sheet reads these cells)", "subhead")
    _head(ws, NOTES_SET_HEAD, 0, [("Supplier", False), ("Lead days", True), ("Order every (days)", True),
                                  ("Safety low (days)", True), ("Top up to (days)", True), ("Join below (days)", True),
                                  ("Fill to (days)", True), ("Join season days", True), ("Fill season days", True)])
    s_first, s_last = min(rows_of.values()), max(rows_of.values())
    ws.validate(s_first, S_LEAD, s_last, S_EVERY, {"validate": "integer", "criteria": ">=", "value": 0,
                                                    "error_title": "Days",
                                                    "error_message": "A whole number of days, nought or more."})
    leads = "; ".join(f"{label} {rules[key]['lead']} days, {LEAD_WORDS.get(rules[key]['lead_basis'], 'the setting')}"
                      for label, key in named)
    r = s_last + 1
    for text in (f"Lead days start at each supplier's lead time: {leads}.",
                 "Order every starts at each supplier's order rhythm, the days to its next order (config "
                 "'stock.order_every_days', about monthly; 'stock.supplier_every_days' for one supplier).",
                 "Safety low and Top up to are your KPI band (config 'stock.cover_kpi_days').",
                 "Join below = lead days + order every + safety low; Fill to = lead days + order every + top up to. "
                 "Join season days and Fill season days count those days after the data date at their month's lift "
                 "(the tables below): the Order sheet's Joins and Suggested read them.",
                 f"{ANY_SUPPLIER}: the rule of a supplier this sheet does not name (a Supplier cell typed over on the "
                 "Order sheet takes it; its units then count in no supplier's total).",
                 "A change in a yellow cell (Lead days, Order every, a lift) recomputes this file only: a longer lead "
                 "time or rhythm changes the quantities of the rows on the Order sheet, but items that would join only "
                 "come with the next file, which takes the settings in config: tell Claude to keep the new setting "
                 "(a lead time, a rhythm or a lift).",
                 "Budget and Minimum order (Order sheet) are yours to type: Against budget = Value - Budget (above "
                 "nought, in red: over the budget); Minimum check says when the value is below the minimum."):
        ws.text(r, 0, text, "note")
        r += 1

    # the lifts: one per calendar month, the rule's own, two decimals
    r += 1
    ws.text(r, 0, "The season: a lift for each calendar month (the season days read these cells)", "subhead")
    r += 1
    head = r
    _head(ws, head, 0, [("Month", False)] + [(name, True) for name in MONTHS])
    lift_row, basis_row, warn_row = head + 1, head + 2, head + 3
    ws.text(lift_row, 0, "Lift", "g_text")
    ws.text(basis_row, 0, "Measured on", "g_text")
    ws.text(warn_row, 0, "Stock-outs", "g_text")
    for m in range(1, 13):
        c = M_FIRST + m - 1
        ws.put(lift_row, c, _n(r2["table"][m]), "input_dec2")
        _put(ws, basis_row, c, _basis_text(r2["lifts"], m), "g_text_wrap")
        _put(ws, warn_row, c, WARN_WORDS if m in r2["warn"] else None, "g_text_wrap")
    ws.row(basis_row, 27)
    if r2["warn"]:
        ws.row(warn_row, 52)
    ws.validate(lift_row, M_FIRST, lift_row, M_LAST,
                {"validate": "decimal", "criteria": ">", "value": 0, "input_title": "Lift",
                 "input_message": "1.00 leaves the month as it is; 1.20 counts a day of it as 1.2 days.",
                 "error_title": "Lift", "error_message": "A number above nought, such as 1.00 or 1.25."})
    ws.cond(f"{_L(M_FIRST)}{warn_row + 1}:{_L(M_LAST)}{warn_row + 1}",
            {"type": "cell", "criteria": "equal to", "value": f'"{WARN_WORDS}"', "format": "cf_amber"})
    lifts = f"${_L(M_FIRST)}${lift_row + 1}:${_L(M_LAST)}${lift_row + 1}"

    # the windows: each supplier's Join below and Fill to days in each calendar month, formulas of the data date
    r = warn_row + 2
    ws.text(r, 0, "The days of each window in each calendar month, after the data date", "subhead")
    r += 1
    date_row = r
    ws.text(date_row, 0, "Data date (the windows start the day after)", "g_text")
    ws.put(date_row, M_FIRST, model.d, "g_date")
    d_ref = f"${_L(M_FIRST)}${date_row + 1}"
    r += 1
    _head(ws, r, 0, [("Supplier: window", False)] + [(name, True) for name in MONTHS] + [("Days", True)])
    r += 1
    windows: dict[str, tuple[int, int]] = {}
    for label_s, key in named:
        n = rows_of[key] + 1
        pair = []
        for label, col in (("join below", S_JOIN), ("fill to", S_FILL)):
            ws.text(r, 0, f"{label_s}: {label}", "g_text")
            for m in range(1, 13):
                ws.formula(r, M_FIRST + m - 1, month_days(d_ref, f"${_L(col)}${n}", m), "g_int")
            ws.formula(r, M_TOTAL, f"SUM({_L(M_FIRST)}{r + 1}:{_L(M_LAST)}{r + 1})", "g_int")
            pair.append(r)
            r += 1
        windows[key] = (pair[0], pair[1])

    # the settings rows, which read the windows
    for label_s, key in named:
        row, rule = rows_of[key], rules[key]
        n = row + 1
        ws.text(row, S_NAME, label_s, "g_text")
        ws.put(row, S_LEAD, rule["lead"], "input_qty")
        ws.put(row, S_EVERY, rule["every"], "input_qty")
        ws.put(row, S_LOW, rule["low"], "g_int")
        ws.put(row, S_HIGH, rule["high"], "g_int")
        b, c, d, e = (f"{_L(k)}{n}" for k in (S_LEAD, S_EVERY, S_LOW, S_HIGH))
        ws.formula(row, S_JOIN, f"{b}+{c}+{d}", "g_int")
        ws.formula(row, S_FILL, f"{b}+{c}+{e}", "g_int")
        for col, w in ((S_JSEASON, windows[key][0]), (S_FSEASON, windows[key][1])):
            ws.formula(row, col, f"SUMPRODUCT(${_L(M_FIRST)}${w + 1}:${_L(M_LAST)}${w + 1},{lifts})", "g_dec2")

    r += 1
    for text in (f"Lift = the company's units a day in that month last year (its latest occurrence in the twelve "
                 f"months to the data date; the data date's own month up to it) over its units a day in the last "
                 f"{_stock().RATE_DAYS} days, every item and every warehouse. 1.00 leaves a month as it is; 1.20 "
                 f"counts a day of it as 1.2 days of today's pace.",
                 "The lifts are last year's months against today's pace: the growth since then is inside them, so a "
                 "month that was ordinary last year reads under 1.00 when sales have grown.",
                 f"Measured on: the month a lift was measured on (its days when the ledger holds fewer); no data = "
                 f"fewer than {stock_season.LIFT_MIN_DAYS} days of it in the ledger (1.00); your setting = config "
                 f"'stock.season_lift'.",
                 f"Stock-outs: \"{WARN_WORDS}\" = the best sellers were in stock on fewer than "
                 f"{int(stock_season.AVAILABILITY_WARN * 100)}% of that month's days, so it sold less than it could: "
                 f"its lift may be low.",
                 f"The windows: the days of Join below and of Fill to after the data date "
                 f"({_L(M_FIRST)}{date_row + 1}) that fall in each calendar month, from the data date and the "
                 f"settings; a window counts at most {WINDOW_CAP} days. Season days = each month's days x its lift, "
                 f"added up.",
                 "Keep a number above nought in every lift: a lift left blank counts its month as nought."):
        ws.text(r, 0, text, "note")
        r += 1
    return r, {"date": (date_row, M_FIRST), "head": head, "lifts": lift_row, "basis": basis_row, "warn": warn_row,
               "months": (M_FIRST, M_LAST), "windows": windows, "settings": rows_of, "any": rows_of[stock_season.OTHER],
               "rules": {key: (rule["trig_days"], rule["fill_days"]) for key, rule in rules.items()}}


def _how_v2(model, r2: dict, how: list, estimated: list) -> tuple[list, list]:
    """Release 2's words in the Notes blocks: the order list with the rhythm, the season and goods on the way, the
    longer history, the lead time per supplier."""
    st = _stock()
    lead = model.facts["lead"]
    late = getattr(_orders(), "LATE_DAYS", None)
    own = {
        "A day (the daily rate)": (
            f"Units sold in the {st.RATE_DAYS} days to the data date / the days of that window on which the main "
            f"store held the item at the day's end, never fewer than {st.RATE_MIN_DAYS}. An item in stock on fewer "
            f"than {st.RATE_MIN_DAYS} of those days takes the rate of a longer history instead (back to the first "
            f"window holding {st.RATE_MIN_DAYS} days in stock, up to a year: Rate basis, marked ~). The Order sheet "
            f"holds the rate to six decimals, as the screen works the order out, so the two agree to the unit."),
        "Order list": (
            f"An item that sold in the last {st.RATE_DAYS} days joins when its demand over its supplier's Join below "
            f"days reaches Main stock + On the way; Suggested tops it up to the demand of the Fill to days, rounded up "
            f"to a whole unit (to whole cartons where config 'stock.pack_sizes' gives the units in a carton). The "
            f"demand of a day = the daily rate x the lift of that day's month: the Order sheet reads the Join and Fill "
            f"season days. No purchase order is drafted."),
        "Lead time": (
            f"Per supplier (the settings above): the median of its newest ten supplier invoices matched to a receipt "
            f"when it has at least three; else config 'stock.supplier_lead_days'; else every supplier's measured lead "
            f"time ({lead['days']} days now, {lead['basis']}); else the setting."),
    }
    how = [(label, own.get(label, text)) for label, text in how]
    at = next(k for k, (label, _t) in enumerate(how) if label == "Order list") + 1
    how[at:at] = [
        ("The order rhythm", "Order every = the days to the next order to the supplier (about monthly): what is "
                             "ordered now must last until the order after it arrives, so an item joins that much "
                             "earlier and is filled for it too."),
        ("The season", "Each calendar month has a lift (the table above): last year's same month against the pace of "
                       "the last 90 days, company-wide. Its Stock-outs row names a month whose best sellers were out "
                       "a fifth of the time or more: that month's lift may be held down."),
        ("On the way", "The goods of the orders you logged (/yq-stock ordered <supplier>), each item's later supplier "
                       "receipts paying the oldest order first; Expected by = the order date + the supplier's lead "
                       "time" + (f", late more than {late} days after it" if late is not None else "") +
                       f". Now: {_way_words(r2.get('on_the_way'))}."),
    ]
    estimated = [(label, text) for label, text in estimated if label != "Lead time"]
    estimated[1:1] = [("A rate marked ~", "A longer history stands in for an item that was out of stock most of the "
                                          "last 90 days: it reaches back to when the item last sold in stock.")]
    estimated += [("Lead time", "A supplier with fewer than three matched invoices takes the lead time of config or of "
                                "every supplier."),
                  ("The season", "This year is taken to follow last year's months at today's pace; a month the "
                                 "ledger does not hold has no lift of its own (1.00).")]
    return how, estimated


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
    season = (book.controls or {}).get("season") if isinstance(book.controls, dict) else None
    r2 = release2(model) if season else None
    if r2 is not None:
        # each supplier's season days (the month-day formulas and the lifts) against the screen's own sum
        notes = book.get("Notes")
        for key, row in (season.get("settings") or {}).items():
            days = (season.get("rules") or {}).get(key)
            if days is None:
                continue
            cell = notes.cells.get((row, S_NAME))
            name = cell.value if cell is not None else key
            for what, c, n in (("join", S_JSEASON, days[0]), ("fill", S_FSEASON, days[1])):
                cell = notes.cells.get((row, c))
                got = None if cell is None else cell.value
                want = stock_season.season_days(model.d, n, r2["lifts"])
                if not _same(got, want, Q2):
                    shown = got.code if isinstance(got, fx.Err) else (
                        fmt.bhd(_decimal(got), 2) if _decimal(got) is not None else repr(got))
                    out.append(f"Notes sheet: {name} {what} season days {shown} in the file, {fmt.bhd(want, 2)} by the "
                               "screen's rule")
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
    """The day's workbooks already made ("<base>*.xlsx"; Excel's "~$" lock files, the engine's "~build-" temporary
    files and the team copies, " - Team" in the name, aside), the latest last: by copy number, then by modified
    time (readback.newest_plan's order, so the file named, compared and read back is the same one)."""
    if not folder.is_dir():
        return []
    low, team = base.casefold(), TEAM_MARK.casefold()
    found = []
    for p in folder.iterdir():
        name = p.name
        if name.startswith("~$") or name.startswith("~build-") or team in name.casefold():
            continue
        if name.casefold().startswith(low) and name.casefold().endswith(".xlsx") and p.is_file():
            found.append(p)
    return sorted(found, key=lambda p: (_copy_number(p.name), _mtime(p), p.name))


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
    changed = None
    if made_before and not force:
        newest = made_before[-1]
        # made once per data date, unless the figures the Order sheet rests on changed since (a same-date reload,
        # an order logged, a cost loaded later): then the next free name, and a line says why
        today = figures_digest(model)
        compared, theirs = read_stamp(newest) if today else (False, None)
        if not compared or theirs == today:
            return {"path": str(newest), "made": False, "made_at": _clock(newest), "error": None, "controls": []}
        changed = (f"the figures changed since {_clock(newest)}: a new file" if theirs else
                   f"the file made at {_clock(newest)} carries no figures stamp (an older layout): a new file")
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
    if changed:
        checks = [changed] + checks
    return {"path": str(target), "made": True, "made_at": made.strftime("%H:%M"), "error": None, "controls": checks}
