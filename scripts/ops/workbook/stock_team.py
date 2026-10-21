"""The team copy of his Stock & Order Plan: his quantities and comments, with no cost, no value and no name.

/yq-stock team makes it from his newest plan (or the file he names): a NEW workbook beside it, named after it,
"<plan name> - Team.xlsx" (a second one " (2)", then " (3)" ...), never over a file, and only under exports/ or
business_data/ of the data root (paths.allowed_output). It is built from the VALUES his file holds, read back by
scripts.ops.workbook.readback from one read of its bytes, with the report engine (book.py) and the plan's look:
the three title rows, the grid formats, a frozen header and a filter.

THE SHEETS
  Order      his order list and the items he added by code, in his file's order: Supplier, Code, Item, Category,
             Main stock, Vans and outlets (units: his file's column of that header, a v3 plan's; blank for a v1 or
             v2 plan), On the way, Sold 90 d, Cover days, Suggested, His quantity (his final: what he typed, else
             the suggestion), His comment, and a yellow Team comment for the team's reply. No landed cost, value,
             budget, minimum order, margin, price or store name.
  Push list  his Ageing sheet's review, row by row: Item, Code, Status, Sellable, A week, Push a week, Action.
             No value, and no Sold most by (the sellers' names). One line when his file holds no review.
  Notes      what the team copy is, the data date, the file it was made from; costs and values are left out;
             reply with your comments in the Team comment column; what Suggested rests on (a v3 plan: the stock in
             the main store, the vans and the outlets). Its layout tag (Notes!B5) is TEAM_TAG, which readback
             refuses as a plan: a team copy is never read back as his order.

NOT MADE (one sentence in "error", nothing written): a file readback refuses; a plan with a problem (a quantity
that is not a whole number would show the suggestion as his); a comment of his that names money (BHD, BD, fils,
dinar, or an amount to the fils); a plan outside exports/ and business_data/ (the copy goes beside it); ninety-
nine copies already; XlsxWriter missing; a write that fails; a header that would name a cost (leaks(), checked on
every copy before it is written).

CONTRACT (scripts.ops.stock relies on it)
  team_file(plan_path) -> {"path": str | None, "made": bool, "error": str | None}
      made True with the new file's path; else path None, made False and error one sentence (no full stop: the
      screen adds it). Never raises for a file that cannot be made.
  build_team(book, plan, made=None) -> Book     the team workbook in memory, evaluated: book is readback.load(),
                                                plan is readback.plan_of(book)
  leaks(book_or_path) -> [str]                  every header naming cost, value, BHD, margin, budget, price,
                                                minimum, a salesman or sold most by, as "Sheet!A5 'Landed cost'":
                                                a Book's header cells (g_head, g_head_r); in a file, the first row
                                                of each filter and every cell in the header colour. Empty for a
                                                sound team copy.
  team_name(plan_path) -> str                   the plan's name less ".xlsx", then readback.TEAM_MARK
  TEAM_TAG = "stock-plan team v1"
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

from scripts.ops import fmt, paths
from scripts.ops.workbook import book as bk
from scripts.ops.workbook import formula as fx
from scripts.ops.workbook import readback, style
from scripts.ops.workbook.book import Book, Sheet

TITLE = "YQ Bahrain — Stock & Order Plan, team copy"
TEAM_TAG = "stock-plan team v1"
COMPANY = "YQ Bahrain W.L.L"
SHEETS = ("Order", "Push list", "Notes")
HEAD = 4                                   # the header row (0-based) of the Order and Push list tables
LEAK_WORDS = ("cost", "value", "bhd", "margin", "budget", "price", "minimum", "salesman", "salesmen", "sold most by")
HEAD_FORMATS = ("g_head", "g_head_r")
MONEY = re.compile(r"\b(?:BHD|BD|fils|dinars?)\b|(?<![\w.])\d+\.\d{3}(?!\d)", re.I)
REVIEW_STYLES = (("Not moving", "cf_red"), ("Slow", "cf_amber"), ("On track", "cf_green"), ("Cleared", "cf_grey"))
NUMBER_FORMATS = ("g_int", "g_dec2")
VANS_HEAD = "Vans and outlets"             # stock_book.VANS_HEAD: a v3 plan's column G (a v1 or v2 plan has none)
POSITION_LAYOUT = "stock-plan v3"          # stock_book.LAYOUT_TAG_V3: Suggested counts the vans and the outlets

# The Order sheet: (header, right-aligned, width, format, where the value comes from): "row:<key>" is a key of
# readback's row; any other text is a column of his item table, found by its header (blank when his file has none).
ORDER_COLUMNS = (
    ("Supplier", False, 14, "g_text", "row:supplier"),
    ("Code", False, 11, "g_text", "row:code"),
    ("Item", False, 44, "g_text", "row:item"),
    ("Category", False, 16, "g_text", "Category"),
    ("Main stock", True, 11, "g_int", "Main stock"),
    (VANS_HEAD, True, 11, "g_int", VANS_HEAD),
    ("On the way", True, 11, "g_int", "On the way"),
    ("Sold 90 d", True, 11, "g_int", "Sold 90 d"),
    ("Cover days", True, 9, "g_int", "Cover days"),
    ("Suggested", True, 11, "g_int", "row:suggested"),
    ("His quantity", True, 11, "g_int", "row:final"),
    ("His comment", False, 40, "g_text_wrap", "row:comment"),
    ("Team comment", False, 40, "input_note", None),
)
# The Push list: (header, right-aligned, width, format), each a column of his Ageing sheet by the same header
PUSH_COLUMNS = (("Item", False, 44, "g_text"), ("Code", False, 11, "g_text"), ("Status", False, 12, "g_text"),
                ("Sellable", True, 10, "g_int"), ("A week", True, 9, "g_dec2"), ("Push a week", True, 11, "g_int"),
                ("Action", False, 34, "g_text"))
AGEING_SHEET = "Ageing"


# ── small helpers ─────────────────────────────────────────────────────────────

def team_name(plan_path) -> str:
    name = Path(plan_path).name
    return (name[: -len(".xlsx")] if name.lower().endswith(".xlsx") else name) + readback.TEAM_MARK


def _column_width(header: str) -> float:
    """The width of the Order sheet's column of that header (ORDER_COLUMNS)."""
    return next(col[2] for col in ORDER_COLUMNS if col[0] == header)


def target_path(folder: Path, base: str) -> Path:
    """The first free name: <base>.xlsx, then <base> (2).xlsx ... (99)."""
    for k in range(1, 100):
        p = Path(folder) / (base + (f" ({k})" if k > 1 else "") + ".xlsx")
        if not bk.exists(p):
            return p
    raise FileExistsError(f"ninety-nine copies of {base} already exist in {folder}")


def _failed(words: str) -> dict:
    return {"path": None, "made": False, "error": words}


def _sentence(exc: Exception) -> str:
    """An exception's words as one sentence without its full stop (the screen adds it)."""
    text = " ".join(str(exc).split())
    return text[:-1] if text.endswith(".") else text


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{fmt.units(n)} {one if n == 1 else (many or one + 's')}"


def _number(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _text_of(v) -> str | None:
    """A cell's value as text, as his file shows it: the ends trimmed (line breaks inside kept), a whole number
    without '.0', a date as YYYY-MM-DD; None when blank."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime):
        return v.date().isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    s = str(v).strip()
    return s or None


def _put(ws: Sheet, r: int, c: int, value, fmt_name: str) -> None:
    """A value in a grid cell: a number in a number column (anything else a blank there), text elsewhere."""
    if fmt_name in NUMBER_FORMATS:
        v = _number(value)
        if v is None:
            ws.blank(r, c, fmt_name)
        else:
            ws.put(r, c, int(v) if float(v).is_integer() else v, fmt_name)
        return
    text = _text_of(value)
    if text is None:
        ws.blank(r, c, fmt_name)
    else:
        ws.text(r, c, text, fmt_name)


def _height(text: str | None, width_units: float) -> float | None:
    """A row height that shows wrapped text whole (Arial 9.5: about 1.15 characters a unit of width)."""
    if not text:
        return None
    per = max(int(width_units * 1.15), 10)
    n = sum(max(1, math.ceil(len(part) / per)) for part in str(text).split("\n"))
    return None if n == 1 else 12.6 * n + 3


def _day(x) -> str:
    d = fmt.as_date(x)
    return d.strftime("%a %d %b %Y") if d else "n/a"


def _saved_words(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%a %d %b %Y, %H:%M") if iso else "n/a"
    except ValueError:
        return "n/a"


def _title(ws: Sheet, sub: str, purpose: str) -> None:
    """The three lines on top of every sheet, as on the plan: the title, the data date and the file, the purpose."""
    ws.text(0, 0, TITLE, "sheet_title")
    ws.row(0, 21)
    ws.text(1, 0, sub, "sheet_sub")
    ws.text(2, 0, purpose, "note")


def _head(ws: Sheet, r: int, columns) -> None:
    for c, col in enumerate(columns):
        ws.text(r, c, col[0], "g_head_r" if col[1] else "g_head")
    ws.row(r, 27)


# ── the book ──────────────────────────────────────────────────────────────────

def build_team(book: readback.PlanBook, plan: dict, made: datetime | None = None) -> Book:
    """The team workbook in memory, evaluated (see the module docstring)."""
    if made is None:
        from scripts.ops import status
        made = datetime.now(status.BAHRAIN)
    out = Book()
    for name in ("cf_red", "cf_amber", "cf_green", "cf_grey"):
        out.dxf(name)
    d = plan.get("data_date")
    out.properties = {"title": f"Stock & Order Plan {d}, team copy" if d else "Stock & Order Plan, team copy",
                      "subject": "Stock and order plan, team copy", "company": COMPANY,
                      "comments": f"layout {TEAM_TAG}", "created": made.replace(tzinfo=None, microsecond=0)}
    sheets = {name: out.sheet(name, tab_color=style.MUTED if name == "Notes" else None) for name in SHEETS}
    sub = (f"Data date {_day(plan.get('data_date'))} · made from {Path(plan['path']).name}, saved "
           f"{_saved_words(plan.get('saved_at'))}")
    _order(sheets["Order"], book, plan, sub)
    _push(sheets["Push list"], book, sub)
    _notes(sheets["Notes"], plan, sub, made)
    out.evaluate()
    return out


def _order(ws: Sheet, book: readback.PlanBook, plan: dict, sub: str) -> None:
    for c, col in enumerate(ORDER_COLUMNS):
        ws.col(c, c, col[2])
    _title(ws, sub, "His order list by supplier. His quantity is what he will order (what he typed, else the plan's "
                    "suggestion). Type your reply in the yellow Team comment cells.")
    rows = plan.get("rows") or []
    totals: dict[str, list] = {}
    for row in rows:
        t = totals.setdefault(row.get("supplier") or "(no supplier)", [0, 0])
        if row.get("final"):
            t[0] += 1
            t[1] += row["final"]
    if totals:
        ws.text(3, 0, "His quantities by supplier: " + "; ".join(
            f"{name} {_plural(units, 'unit')} ({_plural(items, 'item')})" for name, (items, units) in totals.items())
            + ".", "note")
    _head(ws, HEAD, ORDER_COLUMNS)
    cols = book.order[1]
    r = HEAD
    for r, row in enumerate(rows, HEAD + 1):
        for c, (_label, _right, _width, fmt_name, source) in enumerate(ORDER_COLUMNS):
            if source is None:
                ws.blank(r, c, fmt_name)
                continue
            if source.startswith("row:"):
                value = row.get(source[4:])
            else:
                at = cols.get(readback.fold(source))
                value = book.value_or(readback.ORDER_SHEET, row["row"], at) if at else None
            _put(ws, r, c, value, fmt_name)
        h = _height(row.get("comment"), _column_width("His comment"))
        if h:
            ws.row(r, h)
    if not rows:
        r = HEAD + 1
        ws.text(r, 0, "Nothing is on his order list in this file.", "note")
    last = max(r, HEAD + 1)
    ws.page.update({"freeze": (HEAD + 1, 0), "autofilter": f"A{HEAD + 1}:{fx.col_letters(len(ORDER_COLUMNS) - 1)}"
                                                           f"{last + 1}",
                    "landscape": True, "fit": (1, 0), "repeat_rows": (HEAD, HEAD)})


def _push(ws: Sheet, book: readback.PlanBook, sub: str) -> None:
    for c, col in enumerate(PUSH_COLUMNS):
        ws.col(c, c, col[2])
    _title(ws, sub, "Old, unsold and price-cut items from his ageing review: A week is what each sells a week now, "
                    "Push a week what the sales team must sell of it each week to clear it in time.")
    found = readback.header_row(book, AGEING_SHEET, [col[0] for col in PUSH_COLUMNS])
    if found is None:
        ws.text(HEAD, 0, "No push list in this plan: its ageing review was not worked out.", "note")
        return
    head, cols = found
    target = None
    for (r, _c), v in sorted(book.typed[AGEING_SHEET].items()):
        m = re.match(r"\s*Clear target (\d+) days", str(v)) if r < head else None
        if m:
            target = int(m.group(1))
            break
    ws.text(3, 0, (f"Clear target {target} days. " if target else "") + "The only lever is the sales team: no price "
                                                                          "cut, no bundle.", "note")
    _head(ws, HEAD, PUSH_COLUMNS)
    at = {col[0]: cols[readback.fold(col[0])] for col in PUSH_COLUMNS}
    r, src = HEAD, head + 1
    while True:                                        # the review's rows: down to the first row with no item
        item = book.value_or(AGEING_SHEET, src, at["Item"])
        if item is None or str(item).strip() == "":
            break
        sellable = book.value_or(AGEING_SHEET, src, at["Sellable"])
        if (book.value_or(AGEING_SHEET, src, at["Status"]) not in (None, "")
                and (sellable is None or _number(sellable) is not None)):    # a note or another table's header: no
            r += 1
            for c, (label, _right, _width, fmt_name) in enumerate(PUSH_COLUMNS):
                _put(ws, r, c, book.value_or(AGEING_SHEET, src, at[label]), fmt_name)
        src += 1
    if r == HEAD:
        r = HEAD + 1
        ws.text(r, 0, "No item is old, unsold or price-cut in this plan.", "note")
    else:
        for word, name in REVIEW_STYLES:
            ws.cond(f"C{HEAD + 2}:C{r + 1}", {"type": "cell", "criteria": "equal to", "value": f'"{word}"',
                                              "format": name})
    ws.page.update({"freeze": (HEAD + 1, 0), "autofilter": f"A{HEAD + 1}:{fx.col_letters(len(PUSH_COLUMNS) - 1)}"
                                                           f"{max(r, HEAD + 1) + 1}",
                    "landscape": True, "fit": (1, 0), "repeat_rows": (HEAD, HEAD)})


def _notes(ws: Sheet, plan: dict, sub: str, made: datetime) -> None:
    ws.col(0, 0, 30)
    ws.col(1, 6, 13)
    ws.col(7, 7, 40)
    text_width = 6 * 13 + 40
    _title(ws, sub, "What this copy is, and what it leaves out.")
    tr, tc = fx.parse_cell(readback.TAG_CELL)
    ws.text(tr, tc - 1, "Layout of this file", "g_text")
    ws.text(tr, tc, TEAM_TAG, "g_text")
    r = tr + 2
    ws.text(r, 0, "This file", "subhead")
    r += 1
    # a v3 plan counts the stores its settings name beside the main store: say so without naming which
    held = ("the main store and the stores counted beside it (its Vans and outlets column)"
            if plan.get("layout") == POSITION_LAYOUT else "the main store")
    entries = (
        ("What it is", "The team copy of his Stock & Order Plan: his order list with his quantities and comments (the "
                       "Order sheet) and the items to push (the Push list)."),
        ("Data date", f"{_day(plan.get('data_date'))} (the stock ledger's last day)"),
        ("Made from", f"{Path(plan['path']).name}, saved {_saved_words(plan.get('saved_at'))} (Bahrain)"),
        ("Made at", f"{made.strftime('%a %d %b %Y, %H:%M')} (Bahrain)"),
        ("Left out", "Costs and values are left out; reply with your comments in the Team comment column."),
        ("His quantity", "What he will order: the quantity he typed in his file, else the plan's suggestion. Suggested "
                         f"is worked out from each item's sales a day and its stock in {held}."),
        ("Push a week", "What the sales team must sell of an item each week to clear it within the clear target; A "
                        "week is what it sells a week now."),
    )
    for label, text in entries:
        ws.text(r, 0, label, "g_text_wrap")
        ws.merge(r, 1, r, 7, text, "g_text_wrap")
        h = _height(text, text_width)
        if h:
            ws.row(r, h)
        r += 1
    ws.page.update({"fit": (1, 0)})


# ── the guard ─────────────────────────────────────────────────────────────────

def _file_heads(path: Path) -> list[tuple[str, str, str]]:
    import openpyxl
    wb = openpyxl.load_workbook(bk._long(path))
    fill = "FF" + style.FORMATS["g_head"]["bg_color"].lstrip("#").upper()
    out = []
    for ws in wb.worksheets:
        band = None
        if ws.auto_filter is not None and ws.auto_filter.ref:
            rng = fx.parse_range(ws.auto_filter.ref, ws.title)
            band = (rng.r1 + 1, rng.c1 + 1, rng.c2 + 1)
        for row in ws.iter_rows():
            for cell in row:
                if not isinstance(cell.value, str):
                    continue
                in_band = band is not None and cell.row == band[0] and band[1] <= cell.column <= band[2]
                colour = getattr(getattr(cell.fill, "fgColor", None), "rgb", None)
                if in_band or (isinstance(colour, str) and colour.upper() == fill):
                    out.append((ws.title, cell.coordinate, cell.value))
    return out


def leaks(book_or_path) -> list[str]:
    """Every header that names a cost, a value, money or a person (see the CONTRACT); [] for a sound team copy."""
    if isinstance(book_or_path, Book):
        heads = [(ws.name, bk.a1(r, c), cell.value) for ws in book_or_path.sheets
                 for (r, c), cell in sorted(ws.cells.items()) if cell.fmt in HEAD_FORMATS and cell.kind == "s"]
    else:
        heads = _file_heads(Path(book_or_path))
    return [f"{sheet}!{ref} {text!r}" for sheet, ref, text in heads
            if any(word in readback.fold(text) for word in LEAK_WORDS)]


# ── the file ──────────────────────────────────────────────────────────────────

def team_file(plan_path) -> dict:
    """The team copy of the plan at plan_path, made beside it (see the module docstring). Never raises for a copy
    that cannot be made: that is one sentence in "error"."""
    plan_path = Path(plan_path)
    try:
        book = readback.load(plan_path)
        plan = readback.plan_of(book)
    except readback.PlanFileError as exc:
        return _failed(_sentence(exc))
    problems = plan["problems"]
    if problems:
        more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
        return _failed(f"{plan_path.name} has {_plural(len(problems), 'thing')} to fix first: {problems[0]}{more}")
    for row in plan["rows"]:
        m = MONEY.search(row.get("comment") or "")
        if m:
            return _failed(f"your comment in row {row['row']} names money ({m.group(0)}): the team copy carries none; "
                           "reword it in your file, then try again")
    try:
        import xlsxwriter  # noqa: F401  (Book.save needs it: said before anything is built)
    except ImportError:
        return _failed("XlsxWriter is not installed on this PC (pip install xlsxwriter)")
    folder = plan_path.parent
    try:
        target = target_path(folder, team_name(plan_path))
    except FileExistsError:
        return _failed(f"ninety-nine team copies of {plan_path.name} already exist beside it")
    if not paths.allowed_output(target):
        return _failed(f"the team copy goes beside the plan, and {folder} is outside exports/ and business_data/, the "
                       "only places scripts/ops writes")
    try:
        team = build_team(book, plan)
    except fx.FormulaError as exc:
        return _failed(f"a formula could not be worked out ({_sentence(exc)[:160]})")
    except (KeyError, TypeError, AttributeError, IndexError, ArithmeticError) as exc:
        return _failed(f"the team copy could not be built from {plan_path.name} ({type(exc).__name__})")
    found = leaks(team)
    if found:
        return _failed(f"the team copy would show {found[0]}: not made")
    try:
        team.save(target)
    except ImportError:
        return _failed("XlsxWriter is not installed on this PC (pip install xlsxwriter)")
    except fx.FormulaError as exc:
        return _failed(f"a formula could not be worked out ({_sentence(exc)[:160]})")
    except FileExistsError as exc:
        if bk.exists(target):
            return _failed("a file of that name appeared while the team copy was written; ask again for the next "
                           "name")
        why = f": {exc.strerror}" if getattr(exc, "strerror", None) else ""
        return _failed(f"it could not be written beside the plan ({type(exc).__name__}{why})")
    except OSError as exc:
        why = f": {exc.strerror}" if getattr(exc, "strerror", None) else ""
        return _failed(f"it could not be written beside the plan ({type(exc).__name__}{why})")
    except ValueError as exc:
        return _failed(f"the workbook engine refused it ({_sentence(exc)[:160]})")
    return {"path": str(target), "made": True, "error": None}
