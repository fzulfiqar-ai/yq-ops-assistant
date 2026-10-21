"""The blind count sheets as a workbook, and the same workbook read back once the counts are typed in.

Built with the report engine (book.py: save never overwrites a file) from a scripts.ops.counts.Prep. One sheet per
van and per outlet, then the main store's list, then Notes. Nothing on any sheet is a book quantity: the only
number in the whole file is each sheet's Prepared on date (tests/test_ops_counts.py holds it to that).

A SHEET (rows 1-based, as Excel shows them)
  1        the title: "Stock count: <sheet>"
  2 to 6   Store (the store as the ledger names it), Prepared on (the prep date), and the yellow cells Count date
           (a date from the prep date to a year after: a data validation), Counted by and Checked by
  7        HIDDEN (height 0): the layout tag (counts.TAG), the prep id, the checksum (counts.checksum)
  8        one line on how to fill it in
  9        the header: Code | Item | Counted | Note
  10 on    one row per item (Code and Item printed; Counted and Note yellow), then ADD_ROWS empty yellow rows for an
           item found that is not listed, then an empty row and two signature lines
  Counted takes a whole number, nought or more (a data validation). Printed A4 portrait, one page wide, the header
  row repeated on every page, the store and the page number in the footer; the panes frozen under the header.
  Notes: how the lists were made, how to fill the sheets in, where the file goes back.

READ-BACK (read_file)
  The bytes are read once (their sha256 is the result's); a file another program holds is copied aside under
  exports/ of the data root first, read and removed. Cells are read as typed (openpyxl data_only=False): a formula
  stays a formula. On each sheet the tag is found by its text (a cell starting 'count-sheet', the prep id and the
  checksum in the two cells after it); the labels of the top block by their text (the value in the cell after);
  the table by its header texts, never by fixed letters. Every row under the header is read, so a sorted sheet
  reads the same; a signature line (its first cell starting 'Signature') is marked as one. A sheet with no tag is
  no count sheet (Notes). CountFileError: one sentence, the file's name in it, for a file that is missing, a
  folder, held by another program even for a copy, or no workbook.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from scripts.ops import counts, fmt, paths
from scripts.ops.workbook import book as bk
from scripts.ops.workbook import style
from scripts.ops.workbook.book import Book, Sheet

TITLE = "Stock count"
COMPANY = "YQ Bahrain W.L.L"
STEM = "YQ Count Sheets"
NOTES = "Notes"
MAIN_TAB = "Main store"
VAN_SUFFIX = " - acc wh"                # a van's store name less this is the name on its tab (folded)
TAB_MAX = 31                            # Excel's limit on a sheet's name
L_STORE, L_PREPARED, L_DATE, L_BY, L_CHECKED = counts.TOP_LABELS     # the labels of the top block
TOP_LABELS = counts.TOP_LABELS
H_CODE, H_ITEM, H_COUNTED, H_NOTE = "Code", "Item", "Counted", "Note"
HEADS = (H_CODE, H_ITEM, H_COUNTED, H_NOTE)
SIGN_COUNTER, SIGN_CHECKER = "Signature of the counter", "Signature of the checker"
SIGN_PREFIX = "signature"
# 0-based rows and columns of a sheet
R_TITLE, R_STORE, R_PREPARED, R_DATE, R_BY, R_CHECKED, R_TAG, R_HELP, R_HEAD = range(9)
C_CODE, C_ITEM, C_COUNTED, C_NOTE = range(4)
WIDTHS = (16, 54, 11, 30)
SCAN = 40                               # the rows the tag and the labels are looked for in
HEAD_SCAN = 80                          # ... and the header
HELP = ("Type the number found in Counted: a whole number, 0 when there is none; leave it blank when not counted. An "
        "item found that is not listed: its code and the number found in an empty row at the end.")


class CountFileError(ValueError):
    """The file cannot be read as count sheets: one plain sentence, printed as it is."""


# ── the workbook ──────────────────────────────────────────────────────────────

def tab_name(store: str, kind: str, taken: set) -> str:
    """The sheet's name: a van's store less ' - Acc WH', an outlet's name, 'Main store'; what Excel refuses in a
    sheet name blanked, cut to 31 letters, and ' (2)' ... where two would meet (case aside)."""
    if kind == "main":
        base = MAIN_TAB
    else:
        base = counts.clean(store)
        if kind == "van" and counts.fold(base).endswith(VAN_SUFFIX):
            base = base[: len(base) - len(VAN_SUFFIX)]
    base = counts.clean(re.sub(r"[\[\]:*?/\\]", " ", base)).strip("'") or "Store"
    base = base[:TAB_MAX].rstrip()
    name, n = base, 2
    while name.casefold() in taken:
        suffix = f" ({n})"
        name, n = base[: TAB_MAX - len(suffix)].rstrip() + suffix, n + 1
    taken.add(name.casefold())
    return name


def sheet_titles(prep: counts.Prep) -> list[str]:
    """The tab of each of prep.sheets, in their order (Notes is taken first)."""
    taken = {NOTES.casefold()}
    return [tab_name(s.store, s.kind, taken) for s in prep.sheets]


def _what(s: counts.Sheet, kind: str) -> str:
    if s.kind == "main":
        return "the main store's full count" if kind == "full" else "the main store's cycle list"
    return "a van" if s.kind == "van" else "an outlet"


def _footer(store: str) -> str:
    """The footer: the store on the left (an '&' in it written '&&', as Excel reads a footer), the page on the right."""
    return f"&L{store.replace('&', '&&')}&RPage &P of &N"


def _count_sheet(ws: Sheet, s: counts.Sheet, prep: counts.Prep) -> None:
    for c, width in enumerate(WIDTHS):
        ws.col(c, c, width)
    ws.text(R_TITLE, 0, f"{TITLE}: {ws.name}", "sheet_title")
    ws.row(R_TITLE, 21)
    ws.text(R_TITLE, C_NOTE, f"{_what(s, prep.kind)}", "meta_r")
    for r, label in ((R_STORE, L_STORE), (R_PREPARED, L_PREPARED), (R_DATE, L_DATE), (R_BY, L_BY),
                     (R_CHECKED, L_CHECKED)):
        ws.text(r, C_CODE, label, "g_head")
    ws.text(R_STORE, C_ITEM, s.store, "g_text")
    ws.put(R_PREPARED, C_ITEM, prep.prep_date, "g_date")
    for r in (R_DATE, R_BY, R_CHECKED):
        ws.blank(r, C_ITEM, "input_note")
    ws.text(R_DATE, C_COUNTED, "the day you count", "note")
    ws.text(R_BY, C_COUNTED, "who counted", "note")
    ws.text(R_CHECKED, C_COUNTED, "who checked", "note")
    last_day = prep.prep_date + timedelta(days=counts.DATE_RANGE_DAYS)
    ws.validate(R_DATE, C_ITEM, R_DATE, C_ITEM,
                {"validate": "date", "criteria": "between", "minimum": prep.prep_date, "maximum": last_day,
                 "input_title": L_DATE, "input_message": "The day the count is done.", "error_title": L_DATE,
                 "error_message": f"Type a date from {fmt.day_long(prep.prep_date)} to {fmt.day_long(last_day)}."})
    # the hidden row: the layout tag, the prep id and the checksum (read back; never a quantity)
    ws.text(R_TAG, C_CODE, counts.TAG)
    ws.text(R_TAG, C_ITEM, prep.prep_id)
    ws.text(R_TAG, C_COUNTED, s.checksum)
    ws.row(R_TAG, 0)
    ws.text(R_HELP, C_CODE, HELP, "note")
    for c, head in enumerate(HEADS):
        ws.text(R_HEAD, c, head, "g_head_r" if c == C_COUNTED else "g_head")
    ws.row(R_HEAD, 18)
    r = R_HEAD + 1
    for ln in s.lines:
        ws.text(r, C_CODE, ln.code, "g_text")
        ws.text(r, C_ITEM, ln.item, "g_text")
        ws.blank(r, C_COUNTED, "input_qty")
        ws.blank(r, C_NOTE, "input_note")
        r += 1
    for _ in range(counts.ADD_ROWS):
        for c in (C_CODE, C_ITEM, C_NOTE):
            ws.blank(r, c, "input_note")
        ws.blank(r, C_COUNTED, "input_qty")
        r += 1
    last_input = r - 1
    ws.validate(R_HEAD + 1, C_COUNTED, last_input, C_COUNTED,
                {"validate": "integer", "criteria": ">=", "value": 0, "input_title": H_COUNTED,
                 "input_message": "The number found: a whole number, 0 when there is none. Blank: not counted.",
                 "error_title": H_COUNTED, "error_message": "Type a whole number, 0 or more."})
    r += 1
    for label in (SIGN_COUNTER, SIGN_CHECKER):
        r += 1
        ws.text(r, C_CODE, label, "note")
        ws.blank(r, C_ITEM, "rule_medium")
        ws.row(r, 24)
    ws.page.update({"freeze": (R_HEAD + 1, 0), "fit": (1, 0), "repeat_rows": (R_HEAD, R_HEAD), "paper": 9,
                    "print_area": f"A1:D{r + 1}", "margins": {"left": 0.5, "right": 0.5, "top": 0.6, "bottom": 0.6},
                    "footer": _footer(s.store)})


def _notes(ws: Sheet, prep: counts.Prep, titles: list[str]) -> None:
    ws.col(0, 0, 26)
    ws.col(1, 1, 90)
    ws.text(0, 0, f"{TITLE}: how these sheets were made", "sheet_title")
    ws.row(0, 21)
    held, full = counts.HELD_DAYS, counts.FULL_DAYS
    window = f"the {full} days" if prep.kind == "full" else f"the {held} days"
    parts = prep.main_parts
    if prep.kind == "full":
        main = "Every accessory the main store holds (a ledger balance other than nought)"
    else:
        bits = [f"The {parts.get(counts.WHY_FAST, 0)} fast movers of /yq-stock",
                f"the {parts.get(counts.WHY_VALUE, 0)} highest-value items (units on hand at landed cost)"]
        if counts.WHY_EXCEPTION in parts:
            bits.append(f"the {parts[counts.WHY_EXCEPTION]} items on a movement exception of the month")
        main = ", ".join(bits[:-1]) + " and " + bits[-1]
    by_kind = {k: [s for s in prep.sheets if s.kind == k] for k in ("van", "outlet", "main")}
    rows = [
        ("Prepared on", f"{fmt.day_long(prep.prep_date)} ({'a full count' if prep.kind == 'full' else 'a cycle count'})"),
        ("Stock ledger", f"to {fmt.day_long(prep.data_date)}"),
        ("Blind", "No sheet shows a book quantity. The counts are set beside the books when this file is read back: "
                  "the ledger at the end of the day before the Count date."),
        ("Vans and outlets", f"Every accessory held there at the end of {fmt.day_long(prep.data_date)}, or moved there "
                             f"in {window} to it."),
        ("Main store", main + "." if prep.kind == "full" else main + ", each once."),
        ("Accessories only", "The items /yq-stock plans: ever moved in the main store, less those config names as not "
                             "accessories."),
        ("Sheets", f"{len(prep.sheets)}: {counts.plural(len(by_kind['van']), 'van')}, "
                   f"{counts.plural(len(by_kind['outlet']), 'outlet')}{', the main store' if by_kind['main'] else ''}; "
                   f"{counts.plural(prep.lines, 'line')} to count."),
        ("Fill in", "Count date, Counted by and Checked by in the yellow cells at the top. In Counted the number found "
                    "(a whole number, 0 when there is none); blank is not counted. An item found that is not listed: "
                    "its code and the number found in an empty yellow row at the end. Note: anything to say."),
        ("Give it back", "Save the file with the counts typed in, then: /yq-month count and the file's name. Sorting the "
                         "rows is fine; do not delete a row or change the Store cell."),
    ]
    if prep.skipped:
        rows.append(("No sheet", f"{counts.plural(len(prep.skipped), 'store')} held nothing and moved nothing in "
                                 f"{window}: " + ", ".join(prep.skipped) + "."))
    for note in prep.notes:
        rows.append(("Note", note))
    rows.append(("Prep", prep.prep_id))
    r = 2
    for label, text in rows:
        ws.text(r, 0, label, "g_head")
        ws.text(r, 1, text, "g_text_wrap")
        lines = max(1, -(-len(text) // 100))
        if lines > 1:
            ws.row(r, 13 * lines + 3)
        r += 1
    r += 1
    ws.text(r, 0, "Sheet", "g_head")
    ws.text(r, 1, "Store, and the lines on it", "g_head")
    for title, s in zip(titles, prep.sheets):
        r += 1
        ws.text(r, 0, title, "g_text")
        ws.text(r, 1, f"{s.store}: {counts.plural(len(s.lines), 'line')}", "g_text")
    ws.page.update({"fit": (1, 0), "paper": 9})


def build(prep: counts.Prep, made: datetime | None = None) -> Book:
    """The workbook in memory (nothing written): one sheet per prep.sheets entry, in its order, then Notes."""
    from scripts.ops import status
    book = Book()
    stamp = (made or datetime.now(status.BAHRAIN)).replace(tzinfo=None, microsecond=0)
    book.properties = {"title": f"Count sheets {prep.prep_date.isoformat()} {prep.kind}", "subject": "Stock count",
                       "company": COMPANY, "comments": f"layout {counts.TAG}", "created": stamp}
    titles = sheet_titles(prep)
    for title, s in zip(titles, prep.sheets):
        _count_sheet(book.sheet(title, tab_color=style.BLUE if s.kind == "main" else None), s, prep)
    _notes(book.sheet(NOTES, tab_color=style.MUTED), prep, titles)
    errors = book.evaluate()
    if errors:                                  # there is no formula in the file: an error here is a bug
        raise ValueError(f"the count sheets came out with an error value: {errors[0]}")
    return book


def target_path(folder: Path, prep_date: date, kind: str) -> Path:
    """The first free name: 'YQ Count Sheets <date> <cycle|full>.xlsx', then ' (2)' ... ' (99)'."""
    base = f"{STEM} {prep_date.isoformat()} {kind}"
    for k in range(1, 100):
        p = Path(folder) / (base + (f" ({k})" if k > 1 else "") + ".xlsx")
        if not bk.exists(p):
            return p
    raise FileExistsError(f"ninety-nine copies of {base} already exist in {folder}")


def newest(folder) -> Path | None:
    """The newest count workbook in folder (the latest save), Excel's '~$' files and the engine's '~build-' ones
    aside; None when there is none."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    found = [p for p in folder.iterdir() if p.is_file() and p.name.startswith(STEM + " ")
             and p.suffix.lower() == ".xlsx"]
    return max(found, key=lambda p: (os.stat(bk._long(p)).st_mtime_ns, p.name)) if found else None


# ── the read-back ─────────────────────────────────────────────────────────────

@dataclass
class RowRead:
    row: int                                # the Excel row
    code: str | None
    item: str | None
    counted: object                         # as typed: None, a number, text, a date, TRUE/FALSE, '=...' for a formula
    counted_formula: bool
    note: str | None
    sign: bool                              # a signature line, never an item


@dataclass
class SheetRead:
    sheet: str
    tag: str | None                         # None: no count sheet (Notes, or a sheet of his own)
    prep_id: str | None = None
    checksum: str | None = None
    top: dict = field(default_factory=dict)             # label -> the value as typed
    top_formulas: set = field(default_factory=set)      # the labels whose value holds a formula
    rows: list = field(default_factory=list)
    layout: str | None = None               # what of the layout is not found (the words after the sheet's name)


@dataclass
class FileRead:
    path: str
    name: str
    sha256: str
    saved_at: str | None
    open_in_excel: bool
    sheets: list


def _read_bytes(path: Path) -> bytes:
    with open(bk._long(path), "rb") as f:
        return f.read()


def _bytes_of(path: Path) -> bytes:
    name = path.name
    if path.is_dir():
        raise CountFileError(f"{name} is a folder, not a count-sheet workbook.")
    try:
        return _read_bytes(path)
    except FileNotFoundError:
        raise CountFileError(f"{name} is not there: check the name, or the folder it was saved in.") from None
    except PermissionError:
        pass
    except OSError as exc:
        raise CountFileError(f"{name} could not be read ({type(exc).__name__}).") from None
    tmp = paths.exports_dir() / f"~read-{uuid.uuid4().hex[:12]}.xlsx"
    try:
        paths.copy_file(path, tmp)
        return _read_bytes(tmp)
    except OSError:
        raise CountFileError(f"{name} is held by another program: save and close it in Excel, then try again.") from None
    finally:
        try:
            paths.remove_file(tmp)
        except OSError:
            pass


def _saved_at(path: Path) -> str | None:
    from scripts.ops import status
    try:
        stamp = os.stat(bk._long(path)).st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, status.BAHRAIN).isoformat(timespec="seconds")


def _open_in_excel(path: Path) -> bool:
    name = path.name
    return any(bk.exists(path.with_name("~$" + n)) for n in (name, name[2:]) if n)


def _text(v) -> str | None:
    """A cell as text: None when blank; a whole number without '.0'; a date as YYYY-MM-DD."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = " ".join(str(v).split())
    return s or None


def _cells(ws) -> tuple[dict, set]:
    """{(row, col): value as typed} (1-based) of the cells that hold something, and the formula cells."""
    values: dict = {}
    formulas: set = set()
    for row in ws.iter_rows():
        for cell in row:
            v = cell.value
            if v is None:
                continue
            key = (cell.row, cell.column)
            if cell.data_type == "f":
                text = v if isinstance(v, str) else getattr(v, "text", None)     # an array formula keeps its text
                formulas.add(key)
                v = text if isinstance(text, str) and text else "="
                if not v.startswith("="):
                    v = "=" + v
            elif isinstance(v, str) and not v.strip():
                continue
            values[key] = v
    return values, formulas


def _read_sheet(ws) -> SheetRead:
    """One sheet as typed. A sheet with neither the tag nor the look of a count sheet (the Store cell and the table
    header) is none (tag None); one that looks like a count sheet and has lost its tag gets tag '' and a layout
    problem, so that its counts are never passed over without a word."""
    cells, formulas = _cells(ws)
    out = SheetRead(sheet=ws.title, tag=None)
    near = sorted((k, v) for k, v in cells.items() if k[0] <= SCAN)
    tag_at = next((k for k, v in near if isinstance(v, str) and k not in formulas
                   and counts.fold(v).startswith(counts.TAG_PREFIX)), None)
    if tag_at is not None:
        r, c = tag_at
        out.tag = _text(cells[tag_at])
        out.prep_id = _text(cells.get((r, c + 1)))
        out.checksum = _text(cells.get((r, c + 2)))
    labels = {counts.fold(label): label for label in TOP_LABELS}
    for (r, c), v in near:
        label = labels.get(counts.fold(v)) if isinstance(v, str) and (r, c) not in formulas else None
        if label is not None and label not in out.top:
            out.top[label] = cells.get((r, c + 1))
            if (r, c + 1) in formulas:
                out.top_formulas.add(label)
    want = [counts.fold(h) for h in HEADS]
    by_row: dict[int, dict] = {}
    for (r, c), v in sorted(cells.items()):
        if r <= HEAD_SCAN and isinstance(v, str) and (r, c) not in formulas:
            by_row.setdefault(r, {}).setdefault(counts.fold(v), c)
    head = next((r for r in sorted(by_row) if all(h in by_row[r] for h in want)), None)
    if out.tag is None:
        if head is None or L_STORE not in out.top:
            return SheetRead(sheet=ws.title, tag=None)          # no count sheet (Notes, or a sheet of his own)
        out.tag = ""
        out.layout = ("has lost its hidden layout row (deleted with the rows around it?): type its counts into a fresh "
                      "copy of the sheets")
    if L_STORE not in out.top:
        out.layout = out.layout or "has no Store cell at the top: use the file as it was made"
        return out
    if head is None:
        out.layout = out.layout or f"has no table header ({', '.join(HEADS)}): use the file as it was made"
        return out
    col = {h: by_row[head][counts.fold(h)] for h in HEADS}
    last = max((r for r, _c in cells), default=head)
    for r in range(head + 1, last + 1):
        code, item = cells.get((r, col[H_CODE])), cells.get((r, col[H_ITEM]))
        counted, note = cells.get((r, col[H_COUNTED])), cells.get((r, col[H_NOTE]))
        if code is None and item is None and counted is None and note is None:
            continue
        code_text = _text(code)
        sign = bool(code_text) and counts.fold(code_text).startswith(SIGN_PREFIX)
        note_text = None if note is None else (str(note).strip() if isinstance(note, str) else _text(note)) or None
        out.rows.append(RowRead(row=r, code=code_text, item=_text(item), counted=counted,
                                counted_formula=(r, col[H_COUNTED]) in formulas, note=note_text, sign=sign))
    return out


def read_file(path) -> FileRead:
    """The count sheets of a typed copy, as typed (see READ-BACK). Raises CountFileError only."""
    path = Path(path)
    try:
        import openpyxl
    except ImportError:
        raise CountFileError("openpyxl is not installed on this PC (pip install openpyxl): the counts cannot be read "
                             "back.") from None
    data = _bytes_of(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")              # openpyxl's notes on parts it does not keep
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=False)
    except Exception as exc:  # noqa: BLE001  (a file that is no workbook fails in many ways: one sentence)
        raise CountFileError(f"{path.name} cannot be read as an Excel workbook ({type(exc).__name__}).") from None
    try:
        sheets = [_read_sheet(ws) for ws in wb.worksheets]
    except (KeyError, TypeError, AttributeError, IndexError, ValueError) as exc:
        raise CountFileError(f"{path.name} could not be read back ({type(exc).__name__}): it holds something this "
                             "engine does not know.") from None
    return FileRead(path=str(path), name=path.name, sha256=hashlib.sha256(data).hexdigest(),
                    saved_at=_saved_at(path), open_in_excel=_open_in_excel(path), sheets=sheets)
