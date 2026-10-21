"""Ops engine: the blind count sheets (python -m scripts.ops recon prep / recon count), on a synthetic world.

    python -m tests.test_ops_counts

Same lightweight runner as tests/test_ops_stock.py, and ITS stand-ins (imported, not copied): the invented stock
ledger, the database stand-in that answers every read from it (World), the temporary data root. SUPABASE_URL,
SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import; no database, no network, no .env. All
names and numbers are invented; every folder is a temporary one.

The world is the stock suite's (the data date D is Sun 15 Mar 2026: Van A sells ITEM-A, ITEM-B and ITEM-N every
day and sent ten ITEM-A back, Van B holds 35 ITEM-B) with an outlet: on 11 Mar 9000 ITEM-C come in, on 12 Mar 4817
of them go to the outlet and on 13 Mar 2963 to Van B, so the main store holds 1270 of them. Those three figures are
the ones the blindness test looks for.

Covered:
  * the words: prep, prep full, count with a file of several words, a month, nothing; anything else one sentence;
    the month-end reconciliations handed on once scripts.ops.recon_blocks exists, one line (exit 1) until then;
  * what a sheet lists: a van or an outlet holds an item at the end of D or moved it in the 30 days (full: 365);
    accessories only; a store with nothing gets no sheet; the main store's cycle list (the fast movers, the 20
    highest-value items, the movement exceptions when given, one note line when not); full: every item held; one
    code per item (an item with none, or with a shared one, is listed by its ledger name);
  * the workbook: the sheets in order, the top block with its yellow inputs and the date validation, the hidden
    tag row, the header, the rows to add an item, the signature lines, the print setup; the next free name;
  * BLIND: no number in any cell but the prep date, hidden cells included; no book figure in a text, in the XML or
    in prep.json;
  * the command: files under exports/ and business_data/ only; a code worktree makes nothing;
  * the read-back of a filled copy (sorted, a column inserted, a count date that lost its format), the book side
    as of the day before the count, the differences in units and BHD through the cost seam, the results file and
    its sha256, the screen's totals; the Notes never printed; the differences only in exports/ops/<D>/;
  * every refusal, each one sentence and nothing kept; the waits-for-a-load case; vouchers on the count day; the
    BHD column dropped with a note when the cost seam is missing or fails; a file held by another program; nothing
    typed; the newest result of each store in a month.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import traceback
import types
import zipfile
from contextlib import redirect_stdout
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

# The stock suite's stand-ins. Importing it also sets stdout to UTF-8 and imports scripts.ops.db first.
from tests.test_ops_stock import (  # noqa: E402
    D, LONG, MAIN, NAMES, NINE, VAN, VAN_B, Book as Moves, L, World, data_root, standing_in, world)
from scripts.ops import __main__ as cli  # noqa: E402
from scripts.ops import counts, paths, recon, stock  # noqa: E402
from scripts.ops.workbook import count_book  # noqa: E402

import openpyxl  # noqa: E402

stock.RAISE_PARTS = True              # a part that fails fails the test, never stands aside quietly

TESTS: list[tuple[str, object]] = []
OUTLET = "Causeway"
SECRET = (4817, 2963, 1270)           # the outlet's, Van B's and the main store's ITEM-C at the end of D
PREP_DAY = D                          # the sheets are prepared on the ledger's last day
YELLOW = "FFFFF2CC"
TABS = ["Van A", "Van B", OUTLET, "Main store", "Notes"]
NOTE_WORDS = "zebra-quartz remark"    # a Note typed on a sheet: it is never printed
COUNTER = "Counter Person"            # an invented name typed in Counted by: never printed


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


def count_world() -> Moves:
    w = world()
    w.receipt(L("C"), date(2026, 3, 11), 9000, 900, "MRN:T-26-03-7")
    w.move(L("C"), date(2026, 3, 12), SECRET[0], MAIN, OUTLET)
    w.move(L("C"), date(2026, 3, 13), SECRET[1], MAIN, VAN_B)
    return w


class Args:
    def __init__(self, *spec, date_=None, ledger_="auto"):
        self.spec, self.date, self.ledger, self.json, self.data_root = list(spec), date_, ledger_, False, None


class on_day:
    """counts.today() is this day (a Count date after it is in the future)."""

    def __init__(self, day: date):
        self.day = day

    def __enter__(self):
        self.saved = counts.today
        counts.today = lambda: self.day
        return self

    def __exit__(self, *exc):
        counts.today = self.saved
        return False


class seam:
    """The stock module's cost seam set by hand: `costs` {ledger name: unit cost} through fakes of cost_reads,
    accessory_table and apply_costs; None takes the three away; an exception makes cost_reads raise it."""
    NAMES = recon.COST_SEAM

    def __init__(self, costs=None, fail: Exception | None = None):
        self.costs, self.fail = costs, fail

    def __enter__(self):
        self.saved = {n: getattr(stock, n, None) for n in self.NAMES}
        self.had = {n: hasattr(stock, n) for n in self.NAMES}
        if self.costs is None and self.fail is None:
            for n in self.NAMES:
                setattr(stock, n, None)
            return self
        costs, fail = self.costs or {}, self.fail

        def reads():
            if fail is not None:
                raise fail
            return {"price_book": [], "snapshot": [], "mrn": {}, "sheet": {}, "unread": []}

        def table(rows, price_book, snapshot):
            return {name: types.SimpleNamespace(cost=None) for name in {r["item"] for r in rows}}, None, []

        def apply(items, mrn, sheet):
            for name, it in items.items():
                it.cost = costs.get(name)

        stock.cost_reads, stock.accessory_table, stock.apply_costs = reads, table, apply
        return self

    def __exit__(self, *exc):
        for n in self.NAMES:
            if self.had[n]:
                setattr(stock, n, self.saved[n])
            elif hasattr(stock, n):
                delattr(stock, n)
        return False


COSTS = {L("A"): Decimal("0.4"), L("B"): Decimal("2.5"), L("F"): Decimal("1.4")}   # ITEM-N has none


def prep_run(w: World | None = None, *spec, day: date = PREP_DAY) -> dict:
    with standing_in(w or World(book=count_world())):
        return recon.run(Args("prep", *spec, date_=day.isoformat()))


def count_run(path, w: World | None = None, costs=COSTS, today_: date = date(2026, 3, 16), fail=None) -> dict:
    with standing_in(w or World(book=count_world())), on_day(today_), seam(costs, fail):
        return recon.run(Args("count", *([] if path is None else [str(path)])))


def folder(root: Path) -> Path:
    return root / "exports" / "reports" / "counts"


def the_file(res: dict) -> Path:
    return Path(res["flags"]["file"])


def all_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def head_row(ws) -> int:
    for row in ws.iter_rows(min_row=1, max_row=40):
        if [c.value for c in row[:4]] == list(count_book.HEADS):
            return row[0].row
    raise AssertionError("no header")


def fill(src: Path, dst: Path, sheet: str, *, day=date(2026, 3, 16), by=COUNTER, checked="Checker Person",
         got: dict | None = None, notes: dict | None = None, added=(), edit=None, reverse=False) -> Path:
    """A copy of the workbook with one sheet typed in: the Count date, the names, the counts by code, a note by code,
    added rows (code, count[, item]) in the empty rows, then edit(ws) for anything else. reverse writes the item
    rows back in the other order (a sort)."""
    wb = openpyxl.load_workbook(src)
    ws = wb[sheet]
    if day is not None:
        ws.cell(count_book.R_DATE + 1, 2).value = datetime(day.year, day.month, day.day) if isinstance(day, date) else day
    if by is not None:
        ws.cell(count_book.R_BY + 1, 2).value = by
    if checked is not None:
        ws.cell(count_book.R_CHECKED + 1, 2).value = checked
    h = head_row(ws)
    rows = []
    r = h + 1
    while ws.cell(r, 1).value:
        rows.append((ws.cell(r, 1).value, ws.cell(r, 2).value))
        r += 1
    first_empty = r
    if reverse:
        rows = rows[::-1]
    for i, (code, item) in enumerate(rows):
        ws.cell(h + 1 + i, 1).value, ws.cell(h + 1 + i, 2).value = code, item
        ws.cell(h + 1 + i, 3).value = (got or {}).get(code)
        ws.cell(h + 1 + i, 4).value = (notes or {}).get(code)
    for j, entry in enumerate(added):
        ws.cell(first_empty + j, 1).value = entry[0]
        ws.cell(first_empty + j, 3).value = entry[1]
        if len(entry) > 2:
            ws.cell(first_empty + j, 2).value = entry[2]
    if edit is not None:
        edit(ws)
    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)
    return dst


def picks(w: World | None = None) -> counts.StockPicks:
    with standing_in(w or World(book=count_world())):
        model = stock.build(stock.gather(), stock.settings())
        return recon.picks_of(model)


def plan_of(rows=None, kind="cycle", exceptions=None, note=None, p=None) -> counts.Prep:
    rows = rows if rows is not None else count_world().rows
    return counts.plan(rows, counts.store_rules(), p or picks(), kind, PREP_DAY, "2026-03-15-cycle-ghijkl",
                       exceptions=exceptions, exceptions_note=note)


def codes_of(prep: counts.Prep, store: str) -> list[str]:
    return [ln.code for s in prep.sheets if s.store == store for ln in s.lines]


# ── the words ─────────────────────────────────────────────────────────────────

@test("the words: prep, prep full, count with a file of several words, a month, nothing; anything else is one sentence")
def _():
    assert recon.parse_spec([]) == ("month", None)
    assert recon.parse_spec(["prep"]) == recon.parse_spec(["PREP", "cycle"]) == ("prep", "cycle")
    assert recon.parse_spec(["prep", "full"]) == recon.parse_spec(["prep full"]) == ("prep", "full")
    assert recon.parse_spec(["count"]) == ("count", None)
    assert recon.parse_spec(["count", r"C:\Counts\YQ Count Sheets 2026-03-15 cycle.xlsx"]) == \
        ("count", r"C:\Counts\YQ Count Sheets 2026-03-15 cycle.xlsx")
    assert recon.parse_spec(["count", "YQ", "Count", "Sheets.xlsx"]) == ("count", "YQ Count Sheets.xlsx")
    assert recon.parse_spec(["2026-10"]) == ("month", "2026-10")
    for bad in (["prep", "half"], ["2026-13"], ["recount"], ["2026-10", "x"]):
        exc = raises(counts.CountInput, lambda b=bad: recon.parse_spec(b))
        said = str(exc)
        assert said.endswith(".") and "\n" not in said, said
    with data_root():
        res = recon.run(Args("nonsense"))
        assert res["exit"] == 1 and res["text"] == recon.USAGE


@test("the month view: one line (exit 1) until scripts.ops.recon_blocks exists, then handed run(args, month)")
def _():
    with data_root():
        res = recon.run(Args("2026-10"))
        assert res["exit"] == 1 and res["text"] == recon.NO_BLOCKS and not res["ok"], res
        assert recon.run(Args())["text"] == recon.NO_BLOCKS
        seen = []
        fake = types.ModuleType(recon.BLOCKS_MODULE)
        fake.run = lambda args, month: seen.append(month) or {"ok": True, "text": "blocks", "exit": 0}
        sys.modules[recon.BLOCKS_MODULE] = fake
        try:
            assert recon.run(Args("2026-10"))["text"] == "blocks" and recon.run(Args())["text"] == "blocks"
        finally:
            del sys.modules[recon.BLOCKS_MODULE]
        assert seen == ["2026-10", None]
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["recon", "2026-10", "--data-root", str(paths.data_root())])
        assert code == 1 and buf.getvalue().strip() == recon.NO_BLOCKS


# ── what a sheet lists ────────────────────────────────────────────────────────

@test("prep lists: a van or outlet holds the item at the end of D or moved it in the 30 days; full a year; no sheet without")
def _():
    with data_root():
        p = plan_of()
        assert [(s.store, s.kind) for s in p.sheets] == [(VAN, "van"), (VAN_B, "van"), (OUTLET, "outlet"),
                                                          (MAIN, "main")]
        # Van A: ITEM-A held at minus ten (ten sent back), ITEM-B and ITEM-N moved this month; ITEM-F last moved in
        # September; Van B: ITEM-B held since 1 Feb (not moved in the 30 days), ITEM-C sent on 13 Mar
        assert codes_of(p, VAN) == ["CA1", "CB2", "SN4"]
        assert codes_of(p, VAN_B) == ["CB2", "HG3"]
        assert codes_of(p, OUTLET) == ["HG3"]
        assert p.data_date == D and p.lines == 3 + 2 + 1 + len(codes_of(p, MAIN))
        assert all(ln.item == NAMES[k] for s in p.sheets for ln in s.lines for k in NAMES if ln.ledger == L(k))
        full = plan_of(kind="full")
        assert codes_of(full, VAN) == ["CA1", "CB2", "FO5", "SN4"]           # ITEM-F moved in September: in a year
        assert codes_of(full, MAIN) == ["CA1", "CB2", "DS1", "HG3", "SN4"]   # every item held (ITEM-F stands at nought)
        assert full.main_parts == {counts.WHY_HELD: 5}
        # the SIM pack moves only in its own store: no accessory, no sheet, and the store is no van or outlet
        assert all(ln.ledger != "SIM-01 Starter Pack SP9" for s in p.sheets for ln in s.lines)
        # a van that holds nothing and moved nothing in the 30 days: no sheet, named
        w = count_world()
        w.move(L("C"), date(2025, 11, 1), 5, MAIN, "Van Q - Acc WH")
        w.move(L("C"), date(2025, 11, 20), 5, "Van Q - Acc WH", MAIN)
        q = plan_of(rows=w.rows)
        assert "Van Q - Acc WH" in q.skipped and all(s.store != "Van Q - Acc WH" for s in q.sheets)
        assert "Van Q - Acc WH" not in plan_of(rows=w.rows, kind="full").skipped     # it moved within the year
        # an item a van moved that never moved in the main store is on no sheet, and counted in a note
        w.move("ZZ-1 Handset Other ZZ1", date(2026, 3, 14), 2, "Head Office", VAN)
        r = plan_of(rows=w.rows)
        assert "ZZ1" not in " ".join(codes_of(r, VAN)) and r.not_accessories == 1
        assert any("never moved in the main store" in n for n in r.notes)


@test("the cycle list: the fast movers, the 20 highest-value items (ties by name), the exceptions given; a note when not")
def _():
    with data_root():
        pk = picks()
        assert pk.fast == [L("A"), L("F")] and pk.fast_n == 2                    # the stock suite's two fast movers
        assert set(pk.value) == {L(k) for k in ("A", "B", "C", "N", "DISP")}     # held and costed
        p = plan_of(note=recon.NO_MOVES)
        main = next(s for s in p.sheets if s.kind == "main")
        assert [ln.code for ln in main.lines] == ["CA1", "CB2", "DS1", "FO5", "HG3", "SN4"]
        why = {ln.code: ln.why for ln in main.lines}
        assert why["CA1"] == (counts.WHY_FAST, counts.WHY_VALUE) and why["FO5"] == (counts.WHY_FAST,)
        assert why["HG3"] == (counts.WHY_VALUE,)
        assert p.main_parts == {counts.WHY_FAST: 2, counts.WHY_VALUE: 5} and recon.NO_MOVES in p.notes
        # the exceptions when they are given: one more reason, and an item that is no accessory is named in a note
        q = plan_of(exceptions=[L("B"), "ZZ-9 Not An Item ZZ9"])
        mq = next(s for s in q.sheets if s.kind == "main")
        assert {ln.code: ln.why for ln in mq.lines}["CB2"] == (counts.WHY_VALUE, counts.WHY_EXCEPTION)
        assert q.main_parts[counts.WHY_EXCEPTION] == 1 and recon.NO_MOVES not in q.notes
        assert any("not accessories" in n for n in q.notes)
        # twenty-five held items with a value: the twenty highest, a tie by the item's name
        items_ = {f"ITM-{n:02d} Thing {n:02d} T{n:02d}": (f"T{n:02d}", f"ITM-{n:02d} Thing {n:02d}") for n in range(25)}
        value = {led: Decimal(100 - n) for n, led in enumerate(sorted(items_))}
        value[sorted(items_)[24]] = Decimal(81)                                 # ties with the 20th (81): by name
        rows = []
        mv = Moves()
        for led in items_:
            mv.receipt(led, date(2026, 3, 1), 3, 3, "MRN:T-26-03-9")
        rows = mv.rows
        big = counts.plan(rows, counts.store_rules(), counts.StockPicks(items=items_, value=value), "cycle", PREP_DAY,
                          "2026-03-15-cycle-ghijkl")
        top = [ln.code for ln in big.sheets[0].lines]
        assert len(top) == counts.TOP_VALUE == 20 and "T24" not in top and "T19" in top and "T20" not in top
        assert big.main_parts == {counts.WHY_FAST: 0, counts.WHY_VALUE: 20}


@test("one code per item: an item with no code, or a code another item shares, is listed by its ledger name")
def _():
    table = counts.code_table({"A Thing X1": ("X1", "A Thing"), "B Thing x1": ("x1", "B Thing"),
                               "C Thing": (None, "C Thing"), "D Thing D4": (" D4 ", "D Thing")})
    assert table == {"A Thing X1": ("A Thing X1", "A Thing"), "B Thing x1": ("B Thing x1", "B Thing"),
                     "C Thing": ("C Thing", "C Thing"), "D Thing D4": ("D4", "D Thing")}
    assert len({counts.fold(c) for c, _n in table.values()}) == len(table)
    # should a ledger name meet a code, the later one takes a number
    clash = counts.code_table({"Q1": (None, "Q1 one"), "R Two": ("q1", "R Two")})
    assert sorted(c for c, _n in clash.values()) == ["Q1", "q1 (2)"]


# ── the workbook ──────────────────────────────────────────────────────────────

@test("the workbook: the sheets in order, the top block and its yellow inputs, the hidden tag row, the rows, the print setup")
def _():
    with data_root() as root:
        res = prep_run()
        assert res["ok"] and res["exit"] if "exit" in res else res["ok"], res
        path = the_file(res)
        assert path.parent == folder(root) and path.name == f"YQ Count Sheets {PREP_DAY.isoformat()} cycle.xlsx"
        wb = openpyxl.load_workbook(path)
        assert wb.sheetnames == TABS, wb.sheetnames
        prep_id = res["flags"]["prep_id"]
        assert counts.PREP_ID.fullmatch(prep_id) and prep_id.startswith(f"{PREP_DAY.isoformat()}-cycle-")
        record = json.loads(counts.prep_file(prep_id).read_text(encoding="utf-8"))
        for title, entry in zip(TABS, record["stores"]):
            ws = wb[title]
            assert ws["A1"].value == f"{count_book.TITLE}: {title}"
            assert [ws.cell(r, 1).value for r in range(2, 7)] == list(counts.TOP_LABELS)
            assert ws["B2"].value == entry["store"]
            assert ws["B3"].value == datetime(PREP_DAY.year, PREP_DAY.month, PREP_DAY.day)
            for ref in ("B4", "B5", "B6"):
                assert ws[ref].fill.fgColor.rgb == YELLOW and ws[ref].value is None, ref
            assert ws.row_dimensions[7].hidden
            assert [ws.cell(7, c).value for c in (1, 2, 3)] == [counts.TAG, prep_id, entry["checksum"]]
            assert entry["checksum"] == counts.checksum(prep_id, entry["store"], [r["code"] for r in entry["rows"]])
            assert re.fullmatch(r"[a-p]{16}", entry["checksum"])                  # a hash in letters, never a number
            h = head_row(ws)
            assert h == count_book.R_HEAD + 1
            n = len(entry["rows"])
            assert [ws.cell(h + 1 + i, 1).value for i in range(n)] == [r["code"] for r in entry["rows"]]
            assert [ws.cell(h + 1 + i, 2).value for i in range(n)] == [r["item"] for r in entry["rows"]]
            for i in range(n + counts.ADD_ROWS):
                r = h + 1 + i
                assert ws.cell(r, 3).fill.fgColor.rgb == YELLOW and ws.cell(r, 4).fill.fgColor.rgb == YELLOW
                assert (ws.cell(r, 1).fill.fgColor.rgb == YELLOW) == (i >= n), (title, r)     # an add row's code
                assert ws.cell(r, 3).value is None
            assert ws.cell(h + 1 + n + counts.ADD_ROWS, 1).value is None
            signs = [ws.cell(r, 1).value for r in range(h + n + counts.ADD_ROWS + 1, ws.max_row + 1)]
            assert [s for s in signs if s] == [count_book.SIGN_COUNTER, count_book.SIGN_CHECKER]
            dv = {(d.type, str(d.sqref)): d for d in ws.data_validations.dataValidation}
            date_dv = dv[("date", "B4")]
            assert (date_dv.formula1, date_dv.formula2) == ("46096", "46461")      # 15 Mar 2026 to 15 Mar 2027
            whole = dv[("whole", f"C{h + 1}:C{h + n + counts.ADD_ROWS}")]
            assert whole.operator == "greaterThanOrEqual" and whole.formula1 == "0"
            assert ws.print_title_rows == f"${h}:${h}" and ws.freeze_panes == f"A{h + 1}"
            assert ws.page_setup.paperSize == 9 and ws.sheet_properties.pageSetUpPr.fitToPage
            assert ws.page_setup.fitToHeight == 0 and ws.oddFooter.left.text == entry["store"]
        notes = wb["Notes"]
        text = " ".join(str(c.value) for row in notes.iter_rows() for c in row if c.value is not None)
        assert counts.TAG not in text and "Blind" in text and "/yq-month count" in text
        assert recon.NO_MOVES in text and prep_id in text
        assert count_book.read_file(path).sheets[-1].tag is None                 # Notes is no count sheet


@test("BLIND: no number in any cell but the prep date, hidden cells included; no book figure in a text, the XML or prep.json")
def _():
    with data_root() as root:
        for spec in ((), ("full",)):
            res = prep_run(None, *spec)
            path = the_file(res)
            wb = openpyxl.load_workbook(path)
            serial = (PREP_DAY - date(1899, 12, 30)).days
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for c in row:
                        v = c.value
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            raise AssertionError(f"{ws.title}!{c.coordinate} holds the number {v}")
                        if isinstance(v, datetime):
                            assert v.date() == PREP_DAY and c.coordinate == "B3", (ws.title, c.coordinate)
                        if isinstance(v, str):
                            assert not re.fullmatch(r"\s*-?[0-9][0-9,.]*\s*", v), (ws.title, c.coordinate, v)
                            for figure in SECRET:
                                assert not re.search(rf"(?<![0-9]){figure}(?![0-9])", v.replace(",", "")), v
            with zipfile.ZipFile(path) as z:
                for part in z.namelist():
                    if part.endswith(".xml"):
                        xml = z.read(part).decode("utf-8")
                        for figure in SECRET:
                            assert not re.search(rf"(?<![0-9]){figure}(?![0-9])", xml), (part, figure)
                        # every number a cell holds (a cell typed t="s" holds the index of a text, not a number)
                        numbers = [v for attrs, v in re.findall(r"<c ([^>]*)><v>([^<]*)</v></c>", xml)
                                   if 't="' not in attrs or 't="n"' in attrs]
                        assert all(v == str(serial) for v in numbers), (part, numbers)
            record = counts.prep_file(res["flags"]["prep_id"]).read_text(encoding="utf-8")
            for figure in SECRET:
                assert not re.search(rf"(?<![0-9]){figure}(?![0-9])", record), figure
            doc = json.loads(record)
            assert {k for e in doc["stores"] for r in e["rows"] for k in r} <= {"code", "item", "ledger", "why"}
            for figure in SECRET:
                assert str(figure) not in res["text"]
        assert all(paths.allowed_output(p) for p in all_files(root))


@test("next free name: the first file, then (2); never over a file; each prep its own id and record")
def _():
    with data_root() as root:
        one, two = prep_run(), prep_run()
        assert the_file(one).name == f"YQ Count Sheets {PREP_DAY.isoformat()} cycle.xlsx"
        assert the_file(two).name == f"YQ Count Sheets {PREP_DAY.isoformat()} cycle (2).xlsx"
        assert one["flags"]["prep_id"] != two["flags"]["prep_id"]
        assert counts.prep_file(one["flags"]["prep_id"]).is_file() and counts.prep_file(two["flags"]["prep_id"]).is_file()
        full = prep_run(None, "full")
        assert the_file(full).name == f"YQ Count Sheets {PREP_DAY.isoformat()} full.xlsx"
        names = sorted(p.name for p in folder(root).iterdir())
        assert len(names) == 3 and not any(n.startswith(("~", ".")) for n in names)
        assert count_book.target_path(folder(root), PREP_DAY, "cycle").name.endswith("cycle (3).xlsx")


@test("the prep command: its screen, its files under exports/ and business_data/, the dates it refuses, a worktree")
def _():
    with data_root() as root:
        res = prep_run()
        text = res["text"]
        assert text.startswith("COUNT SHEETS  exports") and "Blind: no sheet shows a book quantity" in text
        assert "4 sheets, 12 lines to count (vans 5, outlets 1, the main store 6)" in text, text
        assert recon.NO_MOVES in text and "/yq-month count" in text and "the 2 fast movers" in text
        assert not NINE.search(text) and not LONG.search(text)
        assert res["data_date"] == D.isoformat() and res["flags"]["sheets"] == 4 and res["flags"]["lines"] == 12
        files = [Path(f) for f in res["files"]]
        assert all(paths.allowed_output(f) for f in files) and len(files) == 4
        assert (paths.exports_dir(D) / "recon-prep.txt").read_text(encoding="utf-8").strip() == text
        # a prep date before the ledger's last day, and one that is not a date
        with standing_in(World(book=count_world())):
            early = recon.run(Args("prep", date_="2026-03-14"))
            bad = recon.run(Args("prep", date_="15/03/2026"))
        assert early["exit"] == 1 and "before the stock ledger's last day" in early["text"]
        assert bad["exit"] == 1 and "YYYY-MM-DD" in bad["text"]
        before = sorted(map(str, all_files(root)))
        # a code worktree with no --data-root makes nothing (the files belong to the project folder)
        saved_wt, saved_root = paths.is_worktree, paths._override
        paths.is_worktree = lambda: True
        paths.set_data_root(None)
        try:
            for words in (("prep",), ("count", "x.xlsx")):
                res = recon.run(Args(*words))
                assert res["exit"] == 1 and "code worktree" in res["text"], res
        finally:
            paths.is_worktree, paths._override = saved_wt, saved_root
        assert sorted(map(str, all_files(root))) == before


# ── the read-back ─────────────────────────────────────────────────────────────

def made(_root: Path) -> tuple[Path, str]:
    """The cycle sheets of the count world, made in the data root: (the workbook, its prep id)."""
    res = prep_run()
    return the_file(res), res["flags"]["prep_id"]


@test("read-back: a sorted copy with counts, an added item, a blank; the book side, BHD, the results file, the screen")
def _():
    with data_root() as root:
        src, prep_id = made(root)
        typed = fill(src, root / "typed" / "a.xlsx", "Van A", got={"CA1": 0, "CB2": 2}, notes={"CB2": NOTE_WORDS},
                     added=[("fo5", 1)], reverse=True,
                     edit=lambda ws: setattr(ws.cell(40, 4), "value", "a note on its own " + NOTE_WORDS))
        fill(typed, typed, "Van B", got={"CB2": 30, "HG3": SECRET[1]})
        res = count_run(typed)
        assert res["ok"] and res["exit"] == 0 if "exit" in res else res["ok"], res
        text = res["text"]
        # Van A: ITEM-A counted 0 against minus ten (+10 at 0.400), ITEM-B 2 against nought (+2 at 2.500), ITEM-N not
        # counted, ITEM-F added, 1 against nought (+1 at 1.400): BHD over 4 + 5 + 1.4; Van B: ITEM-B 30 against 35
        # (-5 at 2.500), ITEM-C as booked
        a = json.loads(counts.result_file("2026-03", prep_id, "van-a-acc-wh").read_text(encoding="utf-8"))
        b = json.loads(counts.result_file("2026-03", prep_id, "van-b-acc-wh").read_text(encoding="utf-8"))
        assert a["sha256"] == hashlib.sha256(typed.read_bytes()).hexdigest() and a["book"] == "ready"
        assert a["count_date"] == "2026-03-16" and a["book_day"] == "2026-03-15" and a["month"] == "2026-03"
        rows = {r["code"]: r for r in a["rows"]}
        assert (rows["CA1"]["book"], rows["CA1"]["counted"], rows["CA1"]["diff"], rows["CA1"]["bhd"]) == \
            ("-10", 0, "10", "4.000")
        assert (rows["CB2"]["diff"], rows["CB2"]["bhd"], rows["CB2"]["note"]) == ("2", "5.000", NOTE_WORDS)
        assert rows["SN4"]["counted"] is None and rows["SN4"]["diff"] is None
        assert rows["FO5"]["added"] and rows["FO5"]["diff"] == "1" and rows["FO5"]["bhd"] == "1.400"
        assert a["totals"] == {"lines": 3, "counted": 2, "added": 1, "short_units": "0", "over_units": "13",
                               "short_bhd": "0.000", "over_bhd": "10.400", "uncosted_differences": 0,
                               "moved_on_count_day": 0}, a["totals"]
        assert a["counted_by"] == COUNTER and a["sheet_notes"] == ["a note on its own " + NOTE_WORDS]
        assert b["totals"]["short_units"] == "5" and b["totals"]["short_bhd"] == "12.500"
        assert b["totals"]["over_units"] == "0" and {r["code"]: r["diff"] for r in b["rows"]} == {"CB2": "-5", "HG3": "0"}
        # the screen: totals per store only; never a note, a name or an item line
        assert text.startswith(f"COUNTS READ  {typed.name}") and "2 sheets counted, 2 not counted yet" in text
        assert "2 of 3 +1 added" in text and "10.400" in text and "12.500" in text
        assert NOTE_WORDS not in text and COUNTER not in text and NAMES["A"] not in text and "FO5" not in text
        assert "Not counted yet (nothing typed): Causeway, Main store." in text
        # the item differences only in exports/ops/<D>/, without a note or a name
        diff = (paths.exports_dir(D) / "recon-count.txt").read_text(encoding="utf-8")
        facts = (paths.exports_dir(D) / "recon-count.json").read_text(encoding="utf-8")
        assert "FO5" in diff and "added" in diff and "+10" in diff and "-5" in diff
        for out in (diff, facts):
            assert NOTE_WORDS not in out and COUNTER not in out and "Checker Person" not in out
        assert all(paths.allowed_output(Path(f)) for f in res["files"])
        # read again: the newest read replaces the store's result
        again = count_run(typed)
        assert again["ok"]
        a2 = json.loads(counts.result_file("2026-03", prep_id, "van-a-acc-wh").read_text(encoding="utf-8"))
        assert a2["read_at"] >= a["read_at"] and a2["totals"] == a["totals"]


@test("read-back: columns found by header (a column inserted), a Count date that lost its format, the newest file")
def _():
    with data_root() as root:
        src, prep_id = made(root)
        serial = (date(2026, 3, 16) - date(1899, 12, 30)).days

        def shifted(ws):
            ws.insert_cols(1)
            ws.cell(count_book.R_DATE + 1, 3).value = serial                   # the date cell is now in column C
            ws.cell(count_book.R_DATE + 1, 3).number_format = "General"
        typed = fill(src, folder(root) / "YQ Count Sheets typed.xlsx", OUTLET, day=None, got={"HG3": SECRET[0] - 7},
                     edit=shifted)
        res = count_run(None)                                                  # no file named: the newest there
        assert res["ok"], res
        o = json.loads(counts.result_file("2026-03", prep_id, "causeway").read_text(encoding="utf-8"))
        assert o["file"] == typed.name and o["count_date"] == "2026-03-16"
        assert o["rows"][0]["diff"] == "-7" and o["totals"]["short_units"] == "7"


@test("refusals: each problem one sentence, nothing kept while any stands")
def _():
    with data_root() as root:
        src, prep_id = made(root)
        n = 0

        def refused(words: str, sheet: str = "Van A", today_=date(2026, 3, 16), **kw) -> str:
            nonlocal n
            n += 1
            typed = fill(src, root / "typed" / f"r{n}.xlsx", sheet, **kw)
            res = count_run(typed, today_=today_)
            assert res["exit"] == 2 and not res["ok"], (words, res)
            text = res["text"]
            assert text.startswith(f"COUNTS NOT READ  {typed.name}: nothing was kept."), text
            problems = [ln[4:] for ln in text.splitlines()[1:]]
            assert problems and all(p.endswith(".") for p in problems), problems
            assert any(words in p for p in problems), (words, problems)
            assert not (paths.ops_dir() / "counts" / "2026-03").exists(), "something was kept"
            return text

        good = {"CA1": 1, "CB2": 1, "SN4": 1}
        refused("carries the layout tag 'count-sheet v2'", got=good,
                edit=lambda ws: setattr(ws.cell(count_book.R_TAG + 1, 1), "value", "count-sheet v2"))
        refused("hidden layout row was changed", got=good,
                edit=lambda ws: setattr(ws.cell(count_book.R_TAG + 1, 2), "value", "../../elsewhere"))
        refused("checksum does not match", got=good,
                edit=lambda ws: setattr(ws.cell(count_book.R_TAG + 1, 3), "value", "g" * 16))
        refused("which is not a store of these count sheets", got=good,
                edit=lambda ws: setattr(ws.cell(count_book.R_STORE + 1, 2), "value", "Van Z - Acc WH"))
        said = refused("is missing (deleted?)", got=good, edit=lambda ws: ws.delete_rows(head_row(ws) + 1))
        assert f"CA1 ({NAMES['A']})" in said
        refused("the Count date is empty", day=None, got=good)
        refused("the Count date is not a date ('16th')", day="16th", got=good)
        refused("the Count date holds a formula", got=good,
                edit=lambda ws: setattr(ws.cell(count_book.R_DATE + 1, 2), "value", "=TODAY()"))
        refused("is in the future", day=date(2026, 3, 17), got=good)
        refused("is before the sheets were prepared", day=date(2026, 3, 14), got=good)
        refused("Counted by is empty", by=None, got=good)
        refused("Counted holds a formula", got=dict(good, CA1="=1+1"))
        refused("Counted is not a number ('two')", got=dict(good, CA1="two"))
        refused("Counted is below nought (-1)", got=dict(good, CA1=-1))
        refused("Counted is not a whole number (1.5)", got=dict(good, CA1=1.5))
        refused("the code FO5 has no count", got=good, added=[("FO5", None)])
        refused("has a count but no code", got=good, added=[(None, 3)])
        refused("the code ZZ9 is not an accessory's code", got=good, added=[("ZZ9", 3)])
        refused("names an item but has no code and no count", got=good, added=[(None, None, "a box of cables")])
        refused("the code CB2 is on rows", got=good, added=[("cb2", 4)])
        refused("has lost its hidden layout row", got=good, edit=lambda ws: ws.delete_rows(count_book.R_TAG + 1))
        refused("both hold the counts of Van A - Acc WH", got=good, edit=lambda ws: ws.parent.copy_worksheet(ws))
        # a prep this PC holds no record of
        counts.prep_file(prep_id).unlink()
        refused("this PC holds no record of them", got=good)


@test("the book side waits for a load when the ledger stops before the day before the count")
def _():
    with data_root() as root:
        src, prep_id = made(root)
        typed = fill(src, root / "typed" / "w.xlsx", "Van A", day=date(2026, 3, 17), got={"CA1": 0})
        res = count_run(typed, today_=date(2026, 3, 17))
        assert res["ok"] and res["flags"]["waits"] == 1, res
        a = json.loads(counts.result_file("2026-03", prep_id, "van-a-acc-wh").read_text(encoding="utf-8"))
        assert a["book"] == "waits" and a["waits_for"] == "2026-03-16" and a["rows"][0]["book"] is None
        assert a["waits_words"] == "the book side waits for a load with data to 16 March 2026"
        assert a["totals"]["short_units"] is None and a["rows"][0]["counted"] == 0
        assert "Van A - Acc WH: the book side waits for a load with data to 16 March 2026." in res["text"]
        assert "waits" in res["text"]


@test("vouchers in that store on the count day are named as a possible cause; a day not loaded says so")
def _():
    with data_root() as root:
        src, prep_id = made(root)
        typed = fill(src, root / "typed" / "v.xlsx", "Van A", day=date(2026, 3, 15), got={"CA1": 3, "CB2": 0})
        res = count_run(typed, today_=date(2026, 3, 15))
        assert res["ok"], res
        a = json.loads(counts.result_file("2026-03", prep_id, "van-a-acc-wh").read_text(encoding="utf-8"))
        assert a["book_day"] == "2026-03-14" and a["count_day_loaded"]
        moved = {r["code"]: r["count_day_vouchers"] for r in a["rows"]}
        # ITEM-A and ITEM-B moved in Van A on 15 Mar (a receipt and a sale each), ITEM-N did not
        assert len(moved["CA1"]) == 2 and len(moved["CB2"]) == 2 and moved["SN4"] == []
        assert all(v.startswith(("STRV:", "SI:")) for v in moved["CA1"])
        assert a["totals"]["moved_on_count_day"] == 2
        assert "Van A - Acc WH: 2 items moved in that store on the count day" in res["text"]
        diff = (paths.exports_dir(D) / "recon-count.txt").read_text(encoding="utf-8")
        assert moved["CA1"][0] in diff
        # counted on the day after the ledger's last day: the book side stands, that day's vouchers are not known
        late = fill(src, root / "typed" / "v2.xlsx", "Van B", day=date(2026, 3, 16), got={"CB2": 35})
        res = count_run(late)
        assert ("The count day is not in the loaded ledger yet, so its vouchers are not checked: Van B - Acc WH."
                in res["text"]), res["text"]


@test("BHD: through /yq-stock's cost seam; dropped with one note when the seam is missing or fails; no cost apart")
def _():
    with data_root() as root:
        src, prep_id = made(root)
        typed = fill(src, root / "typed" / "c.xlsx", "Van A", got={"CA1": 0, "CB2": 2, "SN4": 4})
        res = count_run(typed)
        assert res["ok"] and res["flags"]["costed"]
        assert "Van A - Acc WH: 1 difference with no cost: their BHD is left out." in res["text"]   # ITEM-N has none
        a = json.loads(counts.result_file("2026-03", prep_id, "van-a-acc-wh").read_text(encoding="utf-8"))
        assert {r["code"]: r["bhd"] for r in a["rows"]} == {"CA1": "4.000", "CB2": "5.000", "SN4": None}
        assert a["totals"]["uncosted_differences"] == 1 and a["costed"]
        for kw, note in (({"costs": None}, recon.NO_COSTS),
                         ({"fail": RuntimeError("down")}, "the unit costs could not be read (RuntimeError)")):
            res = count_run(typed, **kw)
            assert res["ok"] and not res["flags"]["costed"], res
            assert note in res["text"] and "BHD short" not in res["text"] and "Units over" in res["text"]
            a = json.loads(counts.result_file("2026-03", prep_id, "van-a-acc-wh").read_text(encoding="utf-8"))
            assert not a["costed"] and all(r["bhd"] is None for r in a["rows"])
            assert a["totals"]["over_bhd"] is None and a["totals"]["over_units"] == "16"     # 10 + 2 + 4
        # the real seam, once /yq-stock has it: the same costs as /yq-stock's own model
        if all(callable(getattr(stock, n, None)) for n in recon.COST_SEAM):
            with standing_in(World(book=count_world())):
                costs, note = recon.unit_costs(count_world().rows)
                model = stock.build(stock.gather(), stock.settings())
            assert costs is not None and note is None
            assert all(costs.get(it.ledger_name) == it.cost for it in model.items)
        else:
            print("    SKIP (part): /yq-stock's cost seam is not in this checkout yet")


@test("the file: held by another program (copied aside, read, removed), missing, a folder, no workbook, nothing typed")
def _():
    with data_root() as root:
        src, prep_id = made(root)
        typed = fill(src, root / "typed" / "h.xlsx", "Van A", got={"CA1": 0})
        real = count_book._read_bytes
        seen = []

        def held(path):
            seen.append(Path(path).name)
            if Path(path) == typed:
                raise PermissionError("held")
            return real(path)
        count_book._read_bytes = held
        try:
            res = count_run(typed)
        finally:
            count_book._read_bytes = real
        assert res["ok"] and seen[0] == typed.name and seen[1].startswith("~read-"), seen
        assert not [p for p in paths.exports_dir().iterdir() if p.name.startswith("~read-")]
        for name, words in (("absent.xlsx", "absent.xlsx is not there"), ("typed", "is a folder")):
            res = count_run(root / name)
            assert res["exit"] == 1 and words in res["text"], res
        junk = root / "typed" / "junk.xlsx"
        junk.write_bytes(b"not a workbook")
        res = count_run(junk)
        assert res["exit"] == 1 and "cannot be read as an Excel workbook" in res["text"]
        res = count_run(src)                                                    # as made: nothing typed
        assert res["exit"] == 2 and res["text"].startswith("Nothing is typed in"), res
        plain = root / "typed" / "plain.xlsx"
        wb = openpyxl.Workbook()
        wb.active["A1"] = "hello"
        wb.save(plain)
        res = count_run(plain)
        assert res["exit"] == 1 and "holds no count sheet" in res["text"]
        # a sheet with nothing typed is not counted, whatever else is wrong with it (a row deleted)
        quiet = fill(src, root / "typed" / "q.xlsx", "Van A", got={"CA1": 0},
                     edit=lambda ws: ws.parent["Van B"].delete_rows(head_row(ws.parent["Van B"]) + 1))
        res = count_run(quiet)
        assert res["ok"] and "Van B" in res["text"].split("Not counted yet (nothing typed): ")[1]


@test("month_results: the newest count of each store in a month, across preps")
def _():
    with data_root() as root:
        src1, prep1 = made(root)
        src2, prep2 = made(root)
        count_run(fill(src1, root / "typed" / "m1.xlsx", "Van A", day=date(2026, 3, 16), got={"CA1": 1}),
                  today_=date(2026, 3, 16))
        count_run(fill(src2, root / "typed" / "m2.xlsx", "Van A", day=date(2026, 3, 15), got={"CA1": 2}),
                  today_=date(2026, 3, 16))
        count_run(fill(src2, root / "typed" / "m3.xlsx", "Van B", day=date(2026, 3, 16), got={"CB2": 35}),
                  today_=date(2026, 3, 16))
        best = counts.month_results("2026-03")
        assert set(best) == {VAN, VAN_B}
        assert best[VAN]["prep_id"] == prep1 and best[VAN]["rows"][0]["counted"] == 1    # the later Count date
        assert best[VAN_B]["prep_id"] == prep2
        assert counts.month_results("2026-02") == {}
        raises(ValueError, lambda: counts.month_results("March"))


@test("the hook to /yq-moves: the items it gives join the list; a module without the hook, or one that fails, a note")
def _():
    saved = sys.modules.get(recon.MOVES_MODULE)
    fake = types.ModuleType(recon.MOVES_MODULE)
    try:
        sys.modules[recon.MOVES_MODULE] = fake
        assert recon.movement_exception_items(date(2026, 3, 1), D) == (None, recon.NO_MOVES)
        fake.exception_items = lambda first, last: [L("B")] if (first, last) == (date(2026, 3, 1), D) else []
        assert recon.movement_exception_items(date(2026, 3, 1), D) == ([L("B")], None)
        def broken(first, last):
            raise LookupError("not built")
        fake.exception_items = broken
        items_, note = recon.movement_exception_items(date(2026, 3, 1), D)
        assert items_ is None and "could not give them (LookupError)" in note
        fake.exception_items = lambda first, last: [L("B")]
        with data_root():
            res = prep_run()
            assert "the 1 items on a movement exception of the month" in res["text"], res["text"]
    finally:
        if saved is None:
            sys.modules.pop(recon.MOVES_MODULE, None)
        else:
            sys.modules[recon.MOVES_MODULE] = saved


@test("the module stays read-only: counts and count_book import no client, no writer; every write through paths")
def _():
    import ast
    for rel in ("scripts/ops/counts.py", "scripts/ops/recon.py", "scripts/ops/workbook/count_book.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not mods & {"psycopg", "supabase", "httpx", "subprocess", "shutil", "requests"}, (rel, mods)
        assert "open(" not in src.replace("_read_bytes", "").replace('open(bk._long(path), "rb")', ""), rel
        assert ".write_text(" not in src.replace("paths.write_text(", ""), rel


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
