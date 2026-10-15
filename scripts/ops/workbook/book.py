"""The workbook model: cells, formats, merges, rules, sparklines, charts and page setup, kept in memory,
every formula evaluated (scripts/ops/workbook/formula.py), then written once with XlsxWriter.

A sheet builder never writes a number for a formula: it writes the formula, and Book.evaluate() works
out the value from the formula text against the cells, so the cached value is what the formula says.

Book.save() writes to a temporary file next to the target and moves it into place, and refuses a
target that exists: a report the owner may have open (or has forwarded) is never overwritten.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from scripts.ops.workbook import formula as fx
from scripts.ops.workbook import style

_PENDING = object()                 # a formula cell not evaluated yet


@dataclass
class Cell:
    kind: str                       # 's' text, 'n' number, 'f' formula, 'b' blank, 'bool'
    value: object = None            # the typed value, or the formula's value once evaluated
    formula: str | None = None      # without the leading '='
    fmt: str | None = None          # a name in style.FORMATS


def a1(r: int, c: int, abs_row: bool = False, abs_col: bool = False) -> str:
    """0-based (row, col) -> 'B6' / '$B6' / 'B$6' / '$B$6'."""
    return f"{'$' if abs_col else ''}{fx.col_letters(c)}{'$' if abs_row else ''}{r + 1}"


def aa(r: int, c: int) -> str:
    return a1(r, c, True, True)


def rng(r1: int, c1: int, r2: int, c2: int, absolute: bool = False) -> str:
    return f"{a1(r1, c1, absolute, absolute)}:{a1(r2, c2, absolute, absolute)}"


def user_width(stored: float) -> float:
    """A column width as the file stores it (Excel's own number) -> the width XlsxWriter is given."""
    pixels = round(stored * 7) - 5
    return pixels / 7


@dataclass
class Sheet:
    name: str
    cells: dict = field(default_factory=dict)          # (r, c) -> Cell
    cols: list = field(default_factory=list)           # (c1, c2, stored width)
    rows: dict = field(default_factory=dict)           # r -> height (points)
    default_row: float | None = None
    merges: list = field(default_factory=list)         # (r1, c1, r2, c2)
    conds: list = field(default_factory=list)          # (range text, options)
    sparklines: list = field(default_factory=list)     # options
    charts: list = field(default_factory=list)         # (row, col, chart spec, insert options)
    page: dict = field(default_factory=dict)
    tab_color: str | None = None

    # -- writing cells (0-based row and column)
    def put(self, r: int, c: int, value, fmt: str | None = None) -> None:
        """A typed value: text, a number, a date (stored as its serial number), or None for a blank."""
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            self.cells[(r, c)] = Cell("n", fx.serial(value), None, fmt)
        elif isinstance(value, bool):
            self.cells[(r, c)] = Cell("bool", value, None, fmt)
        elif isinstance(value, (int, float)):
            self.cells[(r, c)] = Cell("n", value, None, fmt)
        elif value is None:
            self.cells[(r, c)] = Cell("b", None, None, fmt)
        else:
            self.cells[(r, c)] = Cell("s", str(value), None, fmt)

    def text(self, r: int, c: int, value: str, fmt: str | None = None) -> None:
        """Text, even when it is empty (an empty string is a cell, not a blank)."""
        self.cells[(r, c)] = Cell("s", "" if value is None else str(value), None, fmt)

    def formula(self, r: int, c: int, text: str, fmt: str | None = None) -> None:
        self.cells[(r, c)] = Cell("f", _PENDING, text.lstrip("="), fmt)

    def blank(self, r: int, c: int, fmt: str | None = None) -> None:
        self.cells[(r, c)] = Cell("b", None, None, fmt)

    def blanks(self, r: int, c1: int, c2: int, fmt: str | None = None) -> None:
        for c in range(c1, c2 + 1):
            self.blank(r, c, fmt)

    def merge(self, r1: int, c1: int, r2: int, c2: int, value=None, fmt: str | None = None, *,
              formula: str | None = None) -> None:
        """A merged block: the top-left cell holds the value (or formula); the rest are blanks in fmt."""
        self.merges.append((r1, c1, r2, c2))
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                self.blank(r, c, fmt)
        if formula is not None:
            self.formula(r1, c1, formula, fmt)
        elif value is not None:
            self.put(r1, c1, value, fmt)

    def col(self, c1: int, c2: int, stored_width: float) -> None:
        self.cols.append((c1, c2, stored_width))

    def row(self, r: int, height: float) -> None:
        self.rows[r] = height

    def cond(self, ref: str, options: dict) -> None:
        self.conds.append((ref, options))

    def used_rows(self) -> int:
        return 1 + max((r for r, _c in self.cells), default=-1)


class Book:
    """The whole workbook: sheets in order, defined names, document properties."""

    def __init__(self):
        self.sheets: list[Sheet] = []
        self.names: dict[str, str] = {}               # name -> 'Sheet!$A$1:$A$9'
        self.properties: dict = {}
        self.commentary: dict[str, list[str]] = {}    # sheet -> the cells that hold written words
        self._by_name: dict[str, Sheet] = {}
        self._dense: dict[tuple[str, int], list] = {}
        self._frontier: dict[tuple[str, int], int] = {}
        self._busy: set = set()
        self._ranges: dict[str, fx.Range] = {}
        self.evaluator = fx.Evaluator(self)

    def sheet(self, name: str, tab_color: str | None = None) -> Sheet:
        ws = Sheet(name, tab_color=tab_color)
        self.sheets.append(ws)
        self._by_name[name] = ws
        return ws

    def get(self, name: str) -> Sheet:
        return self._by_name[name]

    def define(self, name: str, ref: str) -> None:
        self.names[name] = ref.lstrip("=")
        self._ranges.pop(name, None)

    # -- the evaluator's cell source
    def name(self, name: str) -> fx.Range | None:
        key = name
        hit = self._ranges.get(key)
        if hit is None:
            ref = self.names.get(name)
            if ref is None:
                for k, v in self.names.items():           # names are case-blind in Excel
                    if k.lower() == name.lower():
                        ref = v
                        break
            if ref is None:
                return None
            hit = fx.parse_range(ref, "")
            self._ranges[key] = hit
        return hit

    def _column(self, sheet: str, col: int) -> list:
        key = (sheet, col)
        dense = self._dense.get(key)
        if dense is None:
            ws = self._by_name.get(sheet)
            if ws is None:
                raise fx.FormulaError(f"a formula refers to the sheet {sheet!r}, which is not in the book")
            n = ws.used_rows()
            dense = [None] * n
            for (r, c), cell in ws.cells.items():
                if c == col:
                    dense[r] = cell.value if cell.kind != "b" else None
            self._dense[key] = dense
            self._frontier[key] = 0
        return dense

    def value(self, sheet: str, r: int, c: int):
        dense = self._column(sheet, c)
        if r >= len(dense):
            return None
        v = dense[r]
        if v is _PENDING:
            v = self._evaluate_cell(sheet, r, c)
        return v

    def column(self, sheet: str, c: int, r1: int, r2: int) -> list:
        dense = self._column(sheet, c)
        key = (sheet, c)
        start = self._frontier[key] if r1 <= self._frontier[key] else r1
        for r in range(start, min(r2, len(dense) - 1) + 1):
            if dense[r] is _PENDING:
                self._evaluate_cell(sheet, r, c)
        if r1 <= self._frontier[key]:
            self._frontier[key] = max(self._frontier[key], r2 + 1)
        out = dense[r1:r2 + 1]
        if len(out) < r2 - r1 + 1:
            out = out + [None] * (r2 - r1 + 1 - len(out))
        return out

    def _evaluate_cell(self, sheet: str, r: int, c: int):
        key = (sheet, r, c)
        if key in self._busy:
            raise fx.FormulaError(f"circular reference at {sheet}!{a1(r, c)}")
        cell = self._by_name[sheet].cells[(r, c)]
        self._busy.add(key)
        try:
            v = self.evaluator.evaluate(cell.formula, sheet)
        except fx.FormulaError as exc:
            raise fx.FormulaError(f"{sheet}!{a1(r, c)} ={cell.formula}: {exc}") from None
        finally:
            self._busy.discard(key)
        cell.value = v
        self._column(sheet, c)[r] = v
        return v

    def evaluate(self) -> list[str]:
        """Work out every formula's value. Returns the cells that came out as an error value."""
        errors: list[str] = []
        for ws in self.sheets:
            for (r, c), cell in sorted(ws.cells.items()):
                if cell.kind == "f":
                    v = self.value(ws.name, r, c)
                    if isinstance(v, fx.Err):
                        errors.append(f"{ws.name}!{a1(r, c)} = {v.code} ({cell.formula[:60]})")
        return errors

    # -- writing the file
    def save(self, target: Path) -> Path:
        """Write the workbook to `target` through a temporary file in the same folder. A target that
        exists is never replaced: the caller picks a new name."""
        target = Path(target)
        if target.exists():
            raise FileExistsError(f"{target} exists: a report is never overwritten")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="~build-", suffix=".xlsx", dir=str(target.parent))
        os.close(fd)
        try:
            self._write(Path(tmp))
            if target.exists():
                raise FileExistsError(f"{target} appeared while the report was written: not replaced")
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return target

    def _write(self, path: Path) -> None:
        import xlsxwriter

        # in_memory: the parts are assembled in memory (no temporary files of XlsxWriter's own, and the
        # XML keeps its plain line ends on Windows too)
        wb = xlsxwriter.Workbook(str(path), {"strings_to_numbers": False, "strings_to_formulas": False,
                                             "strings_to_urls": False, "in_memory": True,
                                             "default_format_properties": style.DEFAULT_FONT})
        formats: dict[str, object] = {}
        dxfs: dict[str, object] = {}
        for name in style.DXF:                  # numbered once, in the Sep file's order: n/a, rise, fall
            dxfs[name] = wb.add_format(style.dxf(name))
            number = getattr(dxfs[name], "_get_dxf_index", None)
            if callable(number):
                number()

        def f(name):
            if name is None:
                return None
            if name not in formats:
                formats[name] = wb.add_format(style.props(name))
            return formats[name]

        if self.properties:
            wb.set_properties(self.properties)
        sheets = {}
        for ws in self.sheets:
            sheets[ws.name] = wb.add_worksheet(ws.name)
        for ws in self.sheets:
            xs = sheets[ws.name]
            self._write_sheet(wb, xs, ws, f, dxfs)
        for name, ref in self.names.items():
            wb.define_name(name, "=" + ref)
        wb.close()

    def _write_sheet(self, wb, xs, ws: Sheet, f, dxfs: dict) -> None:
        page = ws.page
        if ws.tab_color:
            xs.set_tab_color(ws.tab_color)
        if page.get("hide_gridlines", True):
            xs.hide_gridlines(2)
        if page.get("hide_headers"):
            xs.hide_row_col_headers()
        if ws.default_row is not None:
            xs.set_default_row(ws.default_row)
        for c1, c2, stored in ws.cols:
            xs.set_column(c1, c2, user_width(stored))
        for r1, c1, r2, c2 in ws.merges:
            # the merge is recorded first; the cell loop below then writes every cell of it, the
            # top-left one with its value or formula
            top = ws.cells.get((r1, c1))
            xs.merge_range(r1, c1, r2, c2, "", f(top.fmt if top else None))
        for (r, c), cell in sorted(ws.cells.items()):
            fmt = f(cell.fmt)
            if cell.kind == "s":
                xs.write_string(r, c, cell.value, fmt)
            elif cell.kind == "n":
                xs.write_number(r, c, cell.value, fmt)
            elif cell.kind == "bool":
                xs.write_boolean(r, c, cell.value, fmt)
            elif cell.kind == "b":
                xs.write_blank(r, c, None, fmt)
            elif cell.kind == "f":
                v = cell.value
                if v is _PENDING:
                    raise RuntimeError(f"{ws.name}!{a1(r, c)} was never evaluated")
                xs.write_formula(r, c, "=" + cell.formula, fmt, cached(v))
        # row heights after the cells: a height set first would stretch the sheet's used range to column A
        for r, h in sorted(ws.rows.items()):
            xs.set_row(r, h)
        for ref, options in ws.conds:
            opts = dict(options)
            if "format" in opts:
                opts["format"] = dxfs[opts["format"]]
            first = ref
            if " " in ref.strip():                  # several ranges, one rule: XlsxWriter's multi_range
                opts["multi_range"] = ref.strip()
                first = ref.split()[0]
            xs.conditional_format(first, opts)
        for options in ws.sparklines:
            opts = dict(options)
            r, c = fx.parse_cell(opts.pop("location"))
            xs.add_sparkline(r, c, opts)
        for r, c, spec, insert in ws.charts:
            chart = style.build_chart(wb, spec)
            xs.insert_chart(r, c, chart, dict(insert))
        _page_setup(xs, page)


def cached(v):
    """A formula's value as the file carries it: numbers to 15 significant digits (as a spreadsheet
    shows them), whole numbers without '.0', errors by their code, booleans as such."""
    if isinstance(v, fx.Err):
        return v.code
    if v is None:
        return 0
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        x = fx.r15(float(v))
        return int(x) if x.is_integer() and abs(x) < 1e15 else x
    return str(v)


def _page_setup(xs, page: dict) -> None:
    if page.get("landscape"):
        xs.set_landscape()
    else:
        xs.set_portrait()
    xs.set_paper(page.get("paper", 9))
    m = page.get("margins")
    if m:
        xs.set_margins(**m)
    if page.get("footer"):
        xs.set_footer(page["footer"], {"margin": page.get("footer_margin", 0.25)})
    if page.get("center"):
        xs.center_horizontally()
    if page.get("scale"):
        xs.set_print_scale(page["scale"])
    if page.get("fit"):
        xs.fit_to_pages(*page["fit"])
    if page.get("print_area"):
        xs.print_area(page["print_area"])
    if page.get("repeat_rows"):
        xs.repeat_rows(*page["repeat_rows"])
    if page.get("breaks"):
        xs.set_h_pagebreaks(list(page["breaks"]))
    if page.get("freeze"):
        xs.freeze_panes(*page["freeze"])
    if page.get("autofilter"):
        xs.autofilter(page["autofilter"])
    if page.get("selection"):
        xs.set_selection(page["selection"])
    xs.ignore_errors({"number_stored_as_text": "A1:Z2000", "formula_differs": "A1:Z2000",
                      "formula_range": "A1:Z2000", "empty_cell_reference": "A1:Z2000"})
