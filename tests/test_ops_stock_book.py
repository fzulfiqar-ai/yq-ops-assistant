"""Ops engine: the Stock & Order Plan workbook (scripts.ops.workbook.stock_book), on a synthetic world.

    python -m tests.test_ops_stock_book

Same lightweight runner as tests/test_ops_stock.py, and ITS stand-ins (imported, not copied): the invented stock
ledger (Book), the database stand-in that answers every read from it (World), the temporary data root and the
command (run). SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import; no
database, no network, no .env. All names and numbers are invented; every folder is a temporary one.

The world is the stock suite's (the data date D is Sun 15 Mar 2026): one item on the order list, ITEM-B (CB2),
20 on hand, one a day, so 85 x 1 - 20 = 65 to order at 2.5 = BHD 162.500 from VFAN; the catalog's upcoming
ZN9 is WEKOME's. The damage world adds six of ITEM-A found damaged in the main store (booked) and three of
ITEM-B sent from a van and never booked.

Covered:
  * the day's file: one per data date, made once ("made now", then "made at" its modified time), the next
    free " (2)" with /yq-stock file, never over an existing file, Excel's lock files and the engine's
    temporary files passed over, only exports/ written;
  * the seven sheets in order, the title on each, every formula with its cached value and none an error;
  * the Order sheet: Suggested equals the screen's quantity (a rate that is not whole too), Final a formula,
    the yellow cells, the validation, the supplier totals equal the screen's; typing a quantity, a budget, a
    minimum or a code (looked up on All items, case aside, wildcards escaped) moves Final, Value, the
    totals and the Summary, worked out again by the evaluator;
  * the settings per supplier on Notes feed Joins and Suggested; controls() is empty on a sound file and
    names each difference when a setting moves the file away from the screen;
  * All items: every row of the plan, the statuses coloured, With vans never below nought (and the Notes
    line that counts them); the plan missing: the order list under Other and the items of the ledger;
  * the review and the damage store missing, a stub, not set up or failing: one plain line on their sheet;
  * failures: XlsxWriter missing, a folder that cannot be written, a name taken while writing, a formula
    the evaluator refuses: one sentence, the screen still prints;
  * no van or person named on the Order, All items, Summary or Damage sheets (the Ageing sheet names the
    stores that sold most: the owner's own file).
"""
from __future__ import annotations

import os
import re
import sys
import traceback
import zipfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

# The stock suite's stand-ins. Importing it also sets stdout to UTF-8 (so this file does not) and imports
# scripts.ops.db first, as every command does.
from tests.test_ops_stock import (  # noqa: E402
    CODES, D, LONG, NAMES, NINE, VAN, VAN_B, L, World, data_root, model_of, run, world)
from scripts.ops import paths, status, stock  # noqa: E402
from scripts.ops.workbook import book as bk  # noqa: E402
from scripts.ops.workbook import formula as fx  # noqa: E402
from scripts.ops.workbook import stock_book as sb  # noqa: E402

stock.RAISE_PARTS = True              # a part that fails fails the test, never stands aside quietly

TESTS: list[tuple[str, object]] = []
FILE = f"YQ Stock and Order Plan {D.isoformat()}.xlsx"
SECOND = FILE.replace(".xlsx", " (2).xlsx")
MADE = datetime(2026, 3, 16, 7, 45, tzinfo=status.BAHRAIN)
YELLOW = "FFFFF2CC"
PEOPLE = ("Van A", "Van B", VAN, VAN_B)


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def raises(kind, fn) -> Exception:
    try:
        fn()
    except kind as exc:
        return exc
    raise AssertionError(f"expected {kind.__name__}")


def folder(root: Path) -> Path:
    return root / "exports" / "reports" / "stock"


def books(root: Path) -> list[str]:
    f = folder(root)
    return sorted(p.name for p in f.iterdir()) if f.is_dir() else []


def damage_world():
    w = world()
    w.to_damage(L("A"), date(2026, 3, 2), 6)                         # found in the main store, booked
    w.to_damage(L("B"), date(2026, 3, 1), 3, frm=VAN, booked=False)  # sent from a van, never booked
    return w


CUTS = [("HG3", NAMES["C"], "2026-02-01", "1.0", "0.8")]           # ITEM-C's dealer price cut by a fifth
_SHARED: dict = {}


def shared() -> dict:
    """The damage world's day (with ITEM-C's price cut), run ONCE for the tests that only read its file (opening a
    workbook is the slow part): the screen, the files the run wrote (relative to the data root), the model, the
    file re-opened twice (its formulas, its cached values), its sheets' XML and its shared strings."""
    if not _SHARED:
        with data_root() as root:
            before = {p for p in root.rglob("*") if p.is_file()}
            out = run(World(book=damage_world(), cuts=CUTS))
            m, _inp = model_of(World(book=damage_world(), cuts=CUTS))
            after = {p.relative_to(root) for p in root.rglob("*") if p.is_file() and p not in before}
            path = Path(out["flags"]["file"])
            assert path == folder(root) / FILE, path
            wb, vb = opened(path)
            with zipfile.ZipFile(path) as z:
                xml = [z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
                strings = z.read("xl/sharedStrings.xml").decode("utf-8")
        _SHARED.update(out=out, model=m, after=after, wb=wb, vb=vb, xml=xml, strings=strings)
    return _SHARED


def built(w: World | None = None):
    """(model, book): the workbook of a world in memory, not evaluated (inside a temporary data root)."""
    with data_root():
        m, _inp = model_of(w)
        return m, sb.build_book(m, MADE)


def first_line(out: dict) -> str:
    return out["text"].splitlines()[0]


def value(book, sheet: str, r: int, c: int):
    cell = book.get(sheet).cells.get((r, c))
    return None if cell is None else cell.value


def row_of(book, sheet: str, c: int, text: str) -> int:
    """The 0-based row of the first cell of column c holding `text`."""
    hits = sorted(r for (r, cc), cell in book.get(sheet).cells.items() if cc == c and cell.value == text)
    assert hits, f"{sheet}: no {text!r} in column {fx.col_letters(c)}"
    return hits[0]


def opened(path: Path):
    import openpyxl
    return openpyxl.load_workbook(path), openpyxl.load_workbook(path, data_only=True)


def cell_rows(ws, col: str, first: int):
    """Excel rows from `first` down while column `col` holds something."""
    r = first
    while ws[f"{col}{r}"].value not in (None, ""):
        yield r
        r += 1


# ── the day's file ────────────────────────────────────────────────────────────

@test("the day's file: one per data date, made now; a second run makes none (made at); /yq-stock file makes (2)")
def _():
    with data_root() as root:
        out = run(World())
        line = first_line(out)
        assert line.startswith("FILE  exports") and line.endswith(f"{FILE} (made now)"), line
        assert books(root) == [FILE]
        path = folder(root) / FILE
        assert out["flags"]["file"] == str(path) and path.is_file()
        data = path.read_bytes()
        again = run(World())
        line = first_line(again)
        assert re.search(rf"{re.escape(FILE)} \(made at \d\d:\d\d; /yq-stock file makes a new one\)$", line), line
        assert books(root) == [FILE] and path.read_bytes() == data
        forced = run(World(), "file")
        assert first_line(forced).endswith(f"{SECOND} (made now)"), first_line(forced)
        assert books(root) == sorted([FILE, SECOND]) and path.read_bytes() == data   # never overwritten
        third = run(World())
        assert f"{SECOND} (made at " in first_line(third), first_line(third)            # the newest is named
        for spec in (("reorder",), ("ageing",), ("damage",), ("CB2",)):                # only the headline makes one
            run(World(), *spec)
        assert books(root) == sorted([FILE, SECOND])


@test("day_file: its answer, made_at the file's own time, lock and temporary files passed over, the next free name")
def _():
    with data_root() as root:
        m, _inp = model_of()
        f = folder(root)
        f.mkdir(parents=True)
        for name in (f"~${FILE}", "~build-1x2y.xlsx", "YQ Stock and Order Plan 2026-03-14.xlsx"):
            (f / name).write_bytes(b"not a workbook")
        res = sb.day_file(m)
        assert set(res) == {"path", "made", "made_at", "error", "controls"}, res          # no "built" key
        assert res["made"] and res["error"] is None and res["controls"] == [] and Path(res["path"]) == f / FILE
        assert re.fullmatch(r"\d\d:\d\d", res["made_at"]), res
        made_at = res["made_at"]
        stamp = MADE.timestamp()
        os.utime(f / FILE, (stamp, stamp))               # he saves his edits: the file's modified time moves ...
        res = sb.day_file(m)
        # ... and "made at" stays the build's own time where the system keeps a creation time (Windows)
        expect = made_at if (os.name == "nt" or hasattr(os.stat(f / FILE), "st_birthtime")) else "07:45"
        assert (res["made"], res["made_at"], Path(res["path"]).name) == (False, expect, FILE), res
        res = sb.day_file(m, force=True)
        assert res["made"] and Path(res["path"]).name == SECOND
        assert sb.target_path(f, sb.STEM.format(d=D.isoformat())).name == FILE.replace(".xlsx", " (3).xlsx")
        assert sorted(p.name for p in sb.existing(f, sb.STEM.format(d=D.isoformat()))) == sorted([FILE, SECOND])


@test("a difference controls() finds is printed under the FILE line, one 'To check in the file' line each")
def _():
    saved = sb.controls
    sb.controls = lambda book, model: ["Order sheet: CB2 suggests 66 in the file, 65 on the screen"]
    try:
        with data_root():
            out = run(World())
    finally:
        sb.controls = saved
    lines = out["text"].splitlines()
    assert lines[0].endswith(f"{FILE} (made now)") and lines[2] == "", lines[:3]
    assert lines[1] == "  To check in the file: Order sheet: CB2 suggests 66 in the file, 65 on the screen", lines[1]


@test("an empty order list: the rows to add an item under the header, the totals nought, one line says so")
def _():
    with data_root({"stock.display_items": ["DS1", "CB2"]}):                  # CB2 a display item: nothing to order
        m, _inp = model_of()
        book = sb.build_book(m, MADE)
    assert m.facts["order"] == [] and sb.controls(book, m) == []
    pos = book.controls["order"]
    assert pos["rows"] == [] and pos["add"] == (pos["head"] + 1, pos["head"] + sb.ADD_ROWS)
    assert [value(book, "Order", pos["total"], c) for c in (sb.B_ITEMS, sb.B_UNITS, sb.B_VALUE)] == [0, 0, 0]
    notes = [value(book, "Order", r, 0) for r in range(pos["add"][1] + 2, pos["add"][1] + 4)]
    assert notes[0] == "No item is below 70 days of cover today.", notes
    r = row_of(book, "Summary", 0, "As the plan suggests")
    assert value(book, "Summary", r, 1) == "0 items, 0 units, BHD 0.000"


@test("the file: seven sheets in order, the title on each, every formula with its cached value and none an error")
def _():
    m, book = built(World(book=damage_world(), cuts=CUTS))
    assert [ws.name for ws in book.sheets] == list(sb.SHEETS)
    assert book.evaluate() == [] and sb.controls(book, m) == []
    s = shared()
    wb, sheets, strings = s["wb"], s["xml"], s["strings"]
    assert "To check in the file" not in s["out"]["text"] and first_line(s["out"]).endswith(f"{FILE} (made now)")
    assert wb.sheetnames == ["Summary", "Order", "All items", "Fast movers", "Ageing", "Damage", "Notes"]
    for ws in wb:
        assert ws["A1"].value == "YQ Bahrain — Stock & Order Plan", ws.title
        assert ws["A2"].value.startswith("Data date Sun 15 Mar 2026 · ledger to Sun 15 Mar 2026"), ws["A2"].value
        assert ws["A3"].value, ws.title                                              # its purpose in one line
    formulas = 0
    for xml in sheets:
        for cell in re.findall(r"<c [^>]*>(.*?)</c>", xml):
            if "<f>" in cell:
                formulas += 1
                assert "<v>" in cell, cell[:120]                                     # its value goes with it
                assert not re.search(r"<v>#(N/A|VALUE!|REF!|DIV/0!|NUM!)</v>", cell), cell[:120]
    assert formulas > 100
    assert not NINE.search(strings) and not LONG.search(strings)


# ── the Order sheet ───────────────────────────────────────────────────────────

@test("Order: CB2 suggests 65 as the screen does, Final is a formula, the yellow cells, the validation, the totals")
def _():
    s = shared()
    out, m = s["out"], s["model"]
    ws, vs = s["wb"]["Order"], s["vb"]["Order"]
    head = next(r for r in range(1, 40) if ws[f"A{r}"].value == "Supplier" and ws[f"B{r}"].value == "Code")
    assert ws.freeze_panes == f"A{head + 1}" and ws.auto_filter.ref == f"A{head}:T{head + 1 + sb.ADD_ROWS}"
    cb2 = head + 1
    assert (ws[f"B{cb2}"].value, ws[f"C{cb2}"].value, ws[f"A{cb2}"].value) == (CODES["B"], NAMES["B"], "VFAN")
    assert ws[f"D{cb2}"].value == "Chargers" and ws[f"F{cb2}"].value == 20 and ws[f"J{cb2}"].value == 1
    assert ws[f"G{cb2}"].value == 32 and ws[f"I{cb2}"].value == 45 and ws[f"L{cb2}"].value == 20   # 35 - 3 damaged
    assert vs[f"N{cb2}"].value == 65 and vs[f"P{cb2}"].value == 65 and vs[f"R{cb2}"].value == 162.5
    assert vs[f"M{cb2}"].value is True and ws[f"Q{cb2}"].value == 2.5
    assert ws[f"P{cb2}"].value == f'=IF(O{cb2}="",N{cb2},O{cb2})'
    assert ws[f"R{cb2}"].value == f'=IF(Q{cb2}="","",ROUND(P{cb2}*Q{cb2},3))'
    assert ws[f"M{cb2}"].value.startswith(f"=AND(J{cb2}>0,ROUND(MAX(F{cb2},0)+H{cb2}-J{cb2}*INDEX(Notes!$F$9:$F$10,"
                                          f'MATCH(SUBSTITUTE(SUBSTITUTE(SUBSTITUTE($A{cb2},"~","~~")'), ws[f"M{cb2}"].value
    assert ws[f"N{cb2}"].value.startswith(f"=IF(M{cb2},ROUNDUP(ROUND(J{cb2}*INDEX(Notes!$G$9:$G$10,")
    for col in ("O", "T"):                                                   # Your qty and Comment: yellow, blank
        assert ws[f"{col}{cb2}"].fill.fgColor.rgb == YELLOW and ws[f"{col}{cb2}"].value is None, col
    assert ws[f"P{cb2}"].fill.fgColor.rgb != YELLOW
    add = cb2 + 1                                                            # the first row to add an item
    assert ws[f"B{add}"].fill.fgColor.rgb == YELLOW and ws[f"N{add}"].value == 0
    assert ws[f"C{add}"].value.startswith(f'=IF($B{add}="","",IFERROR(INDEX(\'All items\'!$C$6:$C$12,')
    dv = {str(v.sqref): v for v in ws.data_validations.dataValidation}
    yours = dv[f"O{cb2}:O{cb2 + sb.ADD_ROWS}"]
    assert (yours.type, yours.operator, yours.formula1) == ("whole", "greaterThanOrEqual", "0")
    assert yours.prompt == "Leave blank to take the suggested quantity." and yours.showInputMessage
    # By supplier: VFAN's line equals the plan's totals (the screen's), WEKOME has nothing on the list
    plan = m.facts["plan"]["supplier_totals"]
    assert [(t["supplier"], t["items"], t["units"], t["value"]) for t in plan] == [("VFAN", 1, 65, 162.5)]
    assert [ws[f"{c}5"].value for c in "CDEFGHIJ"] == ["Supplier", "Items", "Units", "Value, BHD", "Budget, BHD",
                                                       "Minimum order, BHD", "Against budget", "Minimum check"]
    lines = {ws[f"C{r}"].value: (vs[f"D{r}"].value, vs[f"E{r}"].value, vs[f"F{r}"].value) for r in range(6, head)
             if ws[f"C{r}"].value}
    assert lines == {"VFAN": (1, 65, 162.5), "WEKOME": (0, 0, 0), "Total": (1, 65, 162.5)}, lines
    assert ws["E6"].value == '=SUMIFS($P$12:$P$27,$A$12:$A$27,"="&SUBSTITUTE(SUBSTITUTE(SUBSTITUTE($C6,"~","~~"),' \
                             '"*","~*"),"?","~?"))', ws["E6"].value
    assert ws["G6"].fill.fgColor.rgb == YELLOW and ws["H6"].fill.fgColor.rgb == YELLOW      # budget, minimum
    note = " ".join(str(ws[f"A{r}"].value) for r in range(head + sb.ADD_ROWS + 2, head + sb.ADD_ROWS + 9)
                    if ws[f"A{r}"].value)
    for words in ("No purchase order is drafted.", "Release 2 adds the order rhythm, the season and goods on order "
                  "when you say an order is placed.", "goods on order are not deducted unless logged"):
        assert words in note, words
    assert "To check in the file" not in out["text"]


@test("Order: typing a quantity moves Final, Value, the supplier's totals and the Summary (the evaluator)")
def _():
    m, book = built()
    pos = book.controls["order"]
    r, vf, tot = pos["rows"][0], pos["suppliers"]["vfan"], pos["total"]
    ws = book.get("Order")
    ws.put(r, sb.O_YOURS, 100, "input_qty")
    assert book.evaluate() == []
    assert value(book, "Order", r, sb.O_FINAL) == 100 and value(book, "Order", r, sb.O_VALUE) == 250
    assert value(book, "Order", r, sb.O_SUGGESTED) == 65                         # the suggestion stays beside it
    assert [value(book, "Order", vf, c) for c in (sb.B_ITEMS, sb.B_UNITS, sb.B_VALUE)] == [1, 100, 250]
    assert [value(book, "Order", tot, c) for c in (sb.B_ITEMS, sb.B_UNITS, sb.B_VALUE)] == [1, 100, 250]
    summary = book.get("Summary")
    units = next(cell for cell in summary.cells.values() if cell.formula == f"Order!E{tot + 1}")
    vfan = next(cell for cell in summary.cells.values() if cell.formula == f"Order!F{vf + 1}")
    assert units.value == 100 and vfan.value == 250
    # a nought takes the item off the order: no item, no unit, no value
    m, book = built()
    book.get("Order").put(r, sb.O_YOURS, 0, "input_qty")
    assert book.evaluate() == []
    assert [value(book, "Order", vf, c) for c in (sb.B_ITEMS, sb.B_UNITS, sb.B_VALUE)] == [0, 0, 0]
    # a budget and a minimum order: against the budget, and the check
    m, book = built()
    ws = book.get("Order")
    ws.put(vf, sb.B_BUDGET, 100, "input_money3")
    ws.put(vf, sb.B_MIN, 200, "input_money3")
    assert book.evaluate() == []
    assert value(book, "Order", vf, sb.B_AGAINST) == 62.5 and value(book, "Order", vf, sb.B_CHECK) == "below the minimum"
    assert value(book, "Order", pos["suppliers"]["wekome"], sb.B_AGAINST) == ""   # no budget typed: nothing said


@test("Order: a code typed in an add row is looked up on All items (case aside, wildcards escaped); the totals count it")
def _():
    m, book = built()
    pos = book.controls["order"]
    a = pos["add"][0]
    ws = book.get("Order")
    typed = (("zn9", 12), ("NOPE-1", 5), ("CA1", 10), ("C*", 3), ("", 7))
    for k, (code, qty) in enumerate(typed):
        if code:
            ws.text(a + k, sb.O_CODE, code, "input_note")
        ws.put(a + k, sb.O_YOURS, qty, "input_qty")
    assert book.evaluate() == []
    got = [tuple(value(book, "Order", a + k, c) for c in (sb.O_SUPPLIER, sb.O_ITEM, sb.O_CATEGORY, sb.O_MAIN,
                                                            sb.O_COST, sb.O_FINAL, sb.O_VALUE)) for k in range(5)]
    assert got[0] == ("WEKOME", "ZZ-NEW Speaker Upcoming", "Speakers", 0, None, 12, ""), got[0]  # no cost yet
    assert got[1][:2] == ("", "code not found") and got[1][5:] == (5, ""), got[1]
    assert got[2] == ("VFAN", NAMES["A"], "Cables", 394, 0.4, 10, 4.0), got[2]
    assert got[3][:2] == ("", "code not found"), got[3]                  # "C*" is no wildcard: CA1 and CB2 stay out
    assert got[4][:2] == ("", "") and got[4][5] == 7, got[4]             # no code: nothing looked up
    sup = pos["suppliers"]
    assert [value(book, "Order", sup["vfan"], c) for c in (sb.B_ITEMS, sb.B_UNITS, sb.B_VALUE)] == [2, 75, 166.5]
    assert [value(book, "Order", sup["wekome"], c) for c in (sb.B_ITEMS, sb.B_UNITS, sb.B_VALUE)] == [1, 12, 0]
    assert value(book, "Order", pos["total"], sb.B_UNITS) == 87          # a code not found counts nowhere


@test("Order: a rate that is not whole (46/45 a day) gives the screen's 67, to the unit, and controls() is empty")
def _():
    w = world()
    w.sale(L("B"), D, 1)
    m, book = built(World(book=w))
    x = m.facts["order"][0]
    assert x["qty"] == 67 and x["rate6"] == stock.stock_plan.round6(x["rate"].rate)
    assert sb.controls(book, m) == []
    r = book.controls["order"]["rows"][0]
    assert value(book, "Order", r, sb.O_RATE) == float(x["rate6"]) == 1.022222
    assert value(book, "Order", r, sb.O_SUGGESTED) == 67


@test("Notes: the layout tag in B5; the settings per supplier feed Joins and Suggested, and controls() says so")
def _():
    m, book = built()
    assert value(book, "Notes", 4, 1) == sb.LAYOUT_TAG == "stock-plan v1" and (sb.TAG_SHEET, sb.TAG_CELL) == ("Notes", "B5")
    s = book.controls["settings"]
    assert sorted(s) == ["vfan", "wekome"] and min(s.values()) == sb.NOTES_SET_HEAD + 1
    assert sb.controls(book, m) == []
    assert [value(book, "Notes", s["vfan"], c) for c in (sb.S_LEAD, sb.S_EVERY, sb.S_LOW, sb.S_HIGH, sb.S_JOIN,
                                                          sb.S_FILL)] == [40, 0, 30, 45, 70, 85]
    m, book = built()
    book.get("Notes").put(s["vfan"], sb.S_LEAD, 60, "input_qty")         # 60 + 30 = 90 to join, filled to 105
    checks = sb.controls(book, m)
    assert value(book, "Order", book.controls["order"]["rows"][0], sb.O_SUGGESTED) == 85
    assert checks == ["Order sheet: CB2 suggests 85 in the file, 65 on the screen",
                      "Order sheet: VFAN units 85 in the file, 65 on the screen",
                      "Order sheet: VFAN value BHD 212.500 in the file, BHD 162.500 on the screen"], checks
    m, book = built()
    book.get("Notes").put(s["vfan"], sb.S_LEAD, 0, "input_qty")          # 0 + 30 = 30: 20 on hand still joins
    sb.controls(book, m)
    assert value(book, "Order", book.controls["order"]["rows"][0], sb.O_SUGGESTED) == 25
    m, book = built()
    book.get("Notes").put(s["vfan"], sb.S_EVERY, 0, "input_qty")
    book.get("Notes").put(s["vfan"], sb.S_LEAD, 0, "input_qty")
    book.get("Order").put(book.controls["order"]["rows"][0], sb.O_MAIN, 40, "g_int")   # 40 on hand: above 30 days
    sb.controls(book, m)
    r = book.controls["order"]["rows"][0]
    assert value(book, "Order", r, sb.O_JOINS) is False and value(book, "Order", r, sb.O_SUGGESTED) == 0
    # a supplier typed over that the settings do not hold: its lookups come out as #N/A, and the checks say where
    m, book = built()
    r = book.controls["order"]["rows"][0]
    book.get("Order").text(r, sb.O_SUPPLIER, "Nobody", "g_text")
    checks = sb.controls(book, m)
    n = r + 1
    assert checks[:2] == ["Order sheet: CB2 suggests #N/A in the file, 65 on the screen",
                          "Order sheet: VFAN items 0 in the file, 1 on the screen"], checks
    assert f"Order!M{n} came out as #N/A" in checks and f"Order!N{n} came out as #N/A" in checks, checks


# ── All items, Fast movers, Ageing, Damage, Notes ─────────────────────────────

@test("All items: every row of the plan, statuses coloured, With vans never below nought (and the Notes line counts it)")
def _():
    s = shared()
    m, wb = s["model"], s["wb"]
    ws = wb["All items"]
    first = sb.ALL_HEAD + 2
    rows = list(cell_rows(ws, "C", first))
    plan = m.facts["plan"]["all_items"]
    assert len(rows) == len(plan) == 7 and [ws[f"C{r}"].value for r in rows] == [p["name"] for p in plan]
    assert ws.freeze_panes == f"A{first}" and ws.auto_filter.ref == f"A{first - 1}:V{rows[-1]}"
    by_code = {ws[f"B{r}"].value: r for r in rows}
    a = by_code["CA1"]
    assert next(p for p in plan if p["code"] == "CA1")["with_vans"] == -10                 # the van gave 10 back
    assert ws[f"G{a}"].value == 0 and ws[f"G{by_code['CB2']}"].value == 32                # read as nought
    assert ws[f"O{by_code['CB2']}"].value == 2.5 and ws[f"O{by_code['ZN9']}"].value is None   # landed cost
    assert ws[f"E{by_code['CB2']}"].value == "Order now" and ws[f"E{by_code['ZN9']}"].value == "Not in main store yet"
    rules = {(r.formula[0], r.operator) : r.dxf for cf in ws.conditional_formatting for r in cf.rules}
    assert {k[0] for k in rules} == {'"Sold out"', '"Order now"', '"No stock, no sale"', '"No sale 90 d"', '"OK"'}
    assert rules[('"Sold out"', "equal")].fill.bgColor.rgb == "FFF8D7DA"
    assert rules[('"Order now"', "equal")].fill.bgColor.rgb == "FFFFF3CD"
    assert rules[('"OK"', "equal")].fill.bgColor.rgb == "FFD4EDDA"
    assert rules[('"No sale 90 d"', "equal")].font.color.rgb == "FF6C757D"
    notes = " ".join(str(c.value) for row in wb["Notes"].iter_rows() for c in row if c.value)
    assert ("With vans is for information only (vans, outlets and goods on their way, from the stock ledger); where "
            "the ledger shows them below nought it reads 0 (1 item).") in notes
    assert wb["Order"]["G12"].value == 32                                                   # CB2 with the vans


@test("Fast movers, Ageing, Damage: their tables, the review's sellers (the owner's file), the decision list")
def _():
    wb = shared()["wb"]
    fast = wb["Fast movers"]
    assert [fast[f"B{r}"].value for r in (7, 8)] == ["CA1", "FO5"] and fast["I7"].value == 15 and fast["I8"].value == 0
    assert fast["I6"].value == "Days available (of 15)" and fast["J7"].value is None
    ageing = wb["Ageing"]
    rows = {ageing[f"B{r}"].value: r for r in cell_rows(ageing, "A", 7)}
    assert set(rows) == {"HG3", "CA1"}, rows
    c, a = rows["HG3"], rows["CA1"]
    assert ageing[f"C{c}"].value == "Not moving" and ageing[f"M{c}"].value == -0.2 and ageing[f"R{c}"].value == 4
    assert ageing[f"S{a}"].value == "Push sales team: 30 a week" and ageing[f"T{a}"].value == "Van A (180)"
    assert ageing[f"U{a}"].fill.fgColor.rgb == YELLOW
    words = [str(x.value) for row in ageing.iter_rows() for x in row if x.value]
    assert "The only lever is the sales team: no price cut, no bundle (your rule)." in words
    assert "Cut of Sun 01 Feb 2026" in words and "Now (Sun 15 Mar 2026)" in words
    for word in ("mark it down", "markdown", "cut the price", "lower the price"):
        assert not any(word in w.lower() for w in words), word
    damage = wb["Damage"]
    labels = {damage[f"A{r}"].value: damage[f"B{r}"].value for r in range(5, 11)}
    assert labels["Units in the damage store"] == 6 and labels["Sent and not booked in (7 days on), units"] == 3
    assert labels["Sent this month per 100 sold"] == 18.37 and labels["Trend"] == "none yet"
    dv = [v for v in damage.data_validations.dataValidation if v.type == "list"]
    assert len(dv) == 1 and dv[0].formula1 == '"Claim from supplier,Write off,Repair,Sell as B-grade,Keep"'
    head = next(r for r in range(1, 60) if damage[f"H{r}"].value == "Decision")
    assert str(dv[0].sqref) == f"H{head + 1}:H{head + 2}" and damage[f"H{head + 1}"].fill.fgColor.rgb == YELLOW
    assert [damage[f"B{r}"].value for r in (head + 1, head + 2)] == ["CA1", "CB2"]
    months = [damage[f"A{r}"].value for r in range(14, 26)]
    assert months[0] == "Apr 2025" and months[-1] == "Mar 2026 (part)"
    texts = [str(x.value) for row in damage.iter_rows() for x in row if x.value]
    assert any(t.startswith("Counted when sent") for t in texts) and any(t.startswith("Nothing here is booked") for t in texts)


@test("no van or person is named on the Order, All items, Summary or Damage sheets; only exports/ is written")
def _():
    s = shared()
    out, after, wb = s["out"], s["after"], s["wb"]
    assert out["ok"] and Path("exports", "reports", "stock", FILE) in after
    assert all(p.parts[0] == "exports" for p in after), sorted(str(p) for p in after)
    assert not [p for p in after if p.name.startswith("~")]                      # no temporary file left
    for name in ("Order", "All items", "Summary", "Damage"):
        for row in wb[name].iter_rows():
            for c in row:
                text = str(c.value or "")
                assert not any(who in text for who in PEOPLE), (name, c.coordinate, text)


@test("Summary: the file, the stock, cover, fast movers, the order list and suppliers (formulas), ageing, damage")
def _():
    m, book = built(World(book=damage_world()))
    assert book.evaluate() == []
    ws = book.get("Summary")

    def at(label: str):
        return value(book, "Summary", row_of(book, "Summary", 0, label), 1)

    assert at("Data date (the stock ledger's last day)") == fx.serial(D)
    assert at("Made at (Bahrain)") == "Mon 16 Mar 2026, 07:45" and at("Layout") == "stock-plan v1"
    h = m.facts["headline"]
    assert (at("Items (the portal's figure)"), at("Units (the portal's figure)")) == (5, h["units"])
    assert at("At landed cost, BHD") == float(h["cost_bhd"]) and at("The portal at cost, BHD") == 381.9
    assert at("Aggregate cover, days") == 74 and at("This month so far") == 0.5
    assert (at("Items"), at("Units"), at("Value at landed cost, BHD")) == (1, 65, 162.5)
    assert at("As the plan suggests") == "1 item, 65 units, BHD 162.500"
    assert at("Lead time, days") == 40
    r = row_of(book, "Summary", 0, "Lead time, days")
    assert value(book, "Summary", r, 2).startswith("Measured on the last 5 supplier invoices")
    assert (at("Joins below, days of sales"), at("Filled to, days of sales")) == (70, 85)
    vfan = row_of(book, "Summary", 0, "VFAN")
    assert ws.cells[(vfan, 2)].formula == f"Order!E{book.controls['order']['suppliers']['vfan'] + 1}"
    assert [value(book, "Summary", vfan, c) for c in (1, 2, 3)] == [1, 65, 162.5]
    assert (at("Review: Not moving"), at("Review: Slow"), at("Review: On track")) == (1, 1, 0)
    assert at("Push a week, units (the sales team)") == 34                     # HG3 4 (50 / 90 x 7) + CA1 30
    assert at("Units in the damage store") == 6 and at("Trend") == "none yet"


@test("a part missing, a stub, not set up or failing is one line on its sheet; the plan missing puts all under Other")
def _():
    with data_root():
        m, _inp = model_of()
        m.facts["plan"] = {"error": "KeyError"}
        m.facts["review"] = {"clear_days": 90, "rows": [], "built": False}
        m.facts["damage"] = {"set_up": False, "store": None, "found": False}
        book = sb.build_book(m, MADE)
    assert sb.controls(book, m) == []
    pos = book.controls["order"]
    assert list(pos["suppliers"]) == ["other"] and value(book, "Order", pos["rows"][0], sb.O_SUPPLIER) == "Other"
    assert value(book, "Order", pos["rows"][0], sb.O_SUGGESTED) == 65
    first, last = book.controls["all_items"]
    assert last - first + 1 == len(m.items) == 6
    assert value(book, "All items", 3, 0).startswith("The order plan was not worked out this time (KeyError)")
    assert value(book, "Ageing", 4, 0) == "The ageing review was not worked out this time."
    assert value(book, "Damage", 4, 0).startswith("The damage store is not set up yet: config 'warehouses.damage'")
    assert not [k for k in book.get("Damage").cells if k[0] > 5]                 # nothing else on the sheet
    with data_root():
        m, _inp = model_of()
        m.facts["plan"] = {"built": False}
        m.facts["damage"] = {"error": "ValueError"}
        book = sb.build_book(m, MADE)
    assert sb.controls(book, m) == []
    assert book.controls["all_items"] == (sb.ALL_HEAD + 1, sb.ALL_HEAD + 6)
    assert value(book, "Damage", 4, 0) == "The damage figures were not worked out this time (ValueError)."
    # facts that are not what the contract says: one line on that sheet, the rest of the file stands
    # (stock.RAISE_PARTS, as in every test, makes it raise instead, so a test sees it)
    with data_root():
        m, _inp = model_of()
    m.facts["review"] = dict(m.facts["review"], rows=[{"bogus": 1}])
    with data_root():
        raises(KeyError, lambda: sb.build_book(m, MADE))
        stock.RAISE_PARTS = False
        try:
            book = sb.build_book(m, MADE)
        finally:
            stock.RAISE_PARTS = True
    assert value(book, "Ageing", 4, 0) == "The ageing review could not be put in this file this time (KeyError)."
    assert not [k for k in book.get("Ageing").cells if k[0] > 4] and sb.controls(book, m) == []


# ── failures ──────────────────────────────────────────────────────────────────

@test("XlsxWriter missing: one sentence; the screen still prints, with FILE  not made this time")
def _():
    had, saved = "xlsxwriter" in sys.modules, sys.modules.get("xlsxwriter")
    sys.modules["xlsxwriter"] = None                     # `import xlsxwriter` now fails, as on a PC without it
    try:
        with data_root() as root:
            out = run(World())
            files = books(root)
    finally:
        if had:
            sys.modules["xlsxwriter"] = saved
        else:
            del sys.modules["xlsxwriter"]
    assert first_line(out) == "FILE  not made this time: XlsxWriter is not installed on this PC (pip install xlsxwriter)."
    assert out["ok"] and "STOCK  main store, Main Store" in out["text"] and "ORDER LIST" in out["text"]
    assert files == [] and "file" not in out["flags"]


@test("a folder that cannot be written, a name taken meanwhile, a formula refused: one sentence each, nothing left")
def _():
    with data_root() as root:
        f = folder(root)
        f.parent.mkdir(parents=True)
        f.write_text("a file where the folder should be", encoding="utf-8")
        out = run(World())
        line = first_line(out)
        assert line.startswith("FILE  not made this time: it could not be written in exports/reports/stock "
                               "(FileExistsError"), line
        assert out["ok"] and "ORDER LIST" in out["text"]
    saved_save, saved_eval = bk.Book.save, bk.Book.evaluate

    def locked(self, target):
        raise PermissionError(13, "Permission denied")

    def taken(self, target):
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"someone else's file")
        raise FileExistsError(f"{target} appeared while the report was written: not replaced")

    def refused(self):
        raise fx.FormulaError("Order!M12 =NOPE(1): the function NOPE is not implemented here")

    try:
        for patch, words in ((("save", locked), "it could not be written in exports/reports/stock (PermissionError: "
                                                "Permission denied)"),
                             (("save", taken), "a file of that name appeared while this one was written; /yq-stock "
                                               "file makes the next one"),
                             (("evaluate", refused), "a formula could not be worked out (Order!M12 =NOPE(1): the "
                                                     "function NOPE is not implemented here)")):
            setattr(bk.Book, *patch)
            with data_root() as root:
                with_screen = run(World())
                res = sb.day_file(model_of()[0], force=True)       # the run's "taken" file is there: a new name
                left = [p.name for p in folder(root).iterdir()] if folder(root).is_dir() else []
            bk.Book.save, bk.Book.evaluate = saved_save, saved_eval
            assert first_line(with_screen) == f"FILE  not made this time: {words}.", first_line(with_screen)
            assert res == {"path": None, "made": False, "made_at": None, "error": words, "controls": []}, res
            assert not [n for n in left if n.startswith("~")] and with_screen["ok"]
    finally:
        bk.Book.save, bk.Book.evaluate = saved_save, saved_eval
    assert paths.allowed_output(ROOT / "exports" / "x.xlsx") is not None    # the guard itself is paths'


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
    paths.set_data_root(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
