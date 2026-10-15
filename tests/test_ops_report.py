"""Ops engine: the workbook engine and the monthly report (python -m scripts.ops report month).

    python -m tests.test_ops_report

Same lightweight runner as the other suites (no pytest). No database, no network, no .env:
SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import and
scripts.ops.db is imported first. The report reads through a Source; here a stand-in answers from an
in-memory list of invented day-book lines (two vans, an outlet, a few shops, round amounts), so every
figure below is a sum a reader can redo by hand. Every folder is a temporary one.

Covered:
  * the formula evaluator: Excel's precedence, case-blind text, criteria with their operators and
    wildcards, numbers compared as a spreadsheet shows them, rounding half away from zero, dates, the
    array idioms the report uses (SUMPRODUCT ranks, LOOKUP(2, 1/(...))), errors as values, and a
    formula it does not know refused instead of given a made-up value;
  * the category rule: the longest alphabetic prefix (TB is its own row, GN a power bank), an unknown
    code through the config name table (a code key wins over a card key), a no-value item in the
    handset row, Other only when nothing else, the Notes sentence from the rule in force;
  * the data rules: the "SI : " prefix, every spelling of cash customer as one, a narration with a
    masked ID left blank, empty money as 0, the order by date, the invoice's trailing number and the
    line; the lists and their order; long labels cut at a whole word;
  * the eight sheets on the synthetic months: every formula evaluated (no error value), the figures
    against hand sums, the controls nil, the positions following the list lengths, the commentary
    DRAFT, the counts of rules, data bars, the colour scale, sparklines and charts;
  * the file: written through a temporary name, never over an existing file, the next free name, a
    valid workbook; the comparison with a reference catches a changed value and sets aside what it
    is told to; no 9-digit or 15-plus-digit run in what the owner reads;
  * the command: the whole-month gate (not ready: nothing written), the week and a bad month refused
    in one sentence, a month with no sales refused, the build record, the text;
  * the real September file, when it is on this machine AND YQ_OPS_GOLDEN=1 is set (SKIP otherwise):
    September 2026 rebuilt from production through the read-only engine in a child process and
    compared with the reference on every sheet but Notes.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
import zipfile
from datetime import date, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)

from scripts.ops import db  # noqa: E402,F401  (first, as every command does)
from scripts.ops import config, paths, status  # noqa: E402
from scripts.ops.workbook import book as bk  # noqa: E402
from scripts.ops.workbook import build, categories as cats, formula as fx, golden, model as mdl  # noqa: E402
from scripts.ops.workbook import sheets_core as sc  # noqa: E402

TESTS: list[tuple[str, object]] = []
MONTH = "2026-03"                      # the report month; February is the comparison month
REFERENCE = Path(os.environ.get("YQ_OPS_REFERENCE") or
                 (paths.data_root() / "YQ Bahrain - Mobile Accessories Sales Report - Sep 2026 (1).xlsx"))


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


# ── a tiny book to evaluate single formulas ────────────────────────────────────────────────────

def ev(formula: str, cells: dict | None = None, names: dict | None = None):
    """Evaluate one formula on a sheet 'S' holding `cells` ({'A1': value})."""
    b = bk.Book()
    s = b.sheet("S")
    for ref, value in (cells or {}).items():
        r, c = fx.parse_cell(ref)
        if isinstance(value, str) and value.startswith("="):
            s.formula(r, c, value)
        else:
            s.put(r, c, value)
    s.formula(99, 0, formula)
    for k, v in (names or {}).items():
        b.define(k, v)
    b.evaluate()
    return s.cells[(99, 0)].value


# ── the synthetic world ────────────────────────────────────────────────────────────────────────

VAN_A, VAN_B, OUTLET, SHOW = "Van A - Acc WH", "Van B - Acc WH", "Causeway", "YQ Roadshow"
ITEMS = {
    "T01": "T01 Demo Earbuds One (VFAN) T01",
    "X01": "X01 Demo Cable One (VFAN)",
    "TB": "TB-D1 Demo Cable Box (VFAN)",
    "GN": "GN-01 Demo Power Bank",
    "WS": "WS-01 Demo Wireless Earbuds",
    "WG": "WG-01 Demo Neckband",
    "ZZ": "ZZ9 Demo Gadget",
    "PHONE": "ZTE-X1 DEMO PHONE",
}
NAMES = {"WG-01": "Headphones & Neckbands", "Wireless Audio": "TWS Earbuds"}   # the config name table
UPCOMING = {"WS-01": "Wireless Audio", "WG-01": "Wireless Audio"}               # the Upcoming cards


def line(day: date, inv: int, no: int, cust: str, item: str, qty: int, rate: float, rep: str, narr: str | None = None):
    gross = round(qty * rate, 3)
    net = round(gross / 1.1, 2) if gross else None
    return {"line_id": inv * 10 + no, "invoice_no": f"SI : SI-YQ-26-{day.month:02d}-{inv}", "line_no": no,
            "sale_date": day.isoformat(), "customer_account": cust, "customer_name": cust, "item_name": item,
            "quantity": float(qty), "rate_bhd": rate if rate else None, "gross_bhd": gross if gross else None,
            "net_bhd": net, "salesman_raw": rep, "salesman_resolved": rep, "narration": narr,
            "category_name": None, "discount_bhd": None, "vat_amount_bhd": None}


def world_rows() -> list[dict]:
    feb, mar = date(2026, 2, 1), date(2026, 3, 1)
    d = lambda base, n: base + timedelta(days=n)  # noqa: E731
    rows = [
        # February (the comparison month)
        line(d(feb, 1), 1, 1, "Shop One", ITEMS["T01"], 10, 2.2, VAN_A),
        line(d(feb, 1), 1, 2, "Shop One", ITEMS["X01"], 20, 1.1, VAN_A),
        line(d(feb, 3), 2, 1, "Cash customer", ITEMS["X01"], 10, 1.1, OUTLET),
        line(d(feb, 9), 3, 1, "Shop Three", ITEMS["GN"], 5, 5.5, VAN_B, "credit note for return"),
        line(d(feb, 20), 4, 1, "Cash Customer", ITEMS["PHONE"], 4, 0, SHOW, "ID 123456789 given"),
        # March (the report month)
        line(d(mar, 0), 9, 1, "Shop One", ITEMS["T01"], 20, 2.2, VAN_A),
        line(d(mar, 0), 10, 1, "Shop Two", ITEMS["X01"], 30, 1.1, VAN_B),
        line(d(mar, 0), 10, 2, "Shop Two", ITEMS["TB"], 2, 11.0, VAN_B),
        line(d(mar, 4), 11, 1, "CASH CUSTOMER", ITEMS["WS"], 5, 4.4, OUTLET),
        line(d(mar, 9), 12, 1, "Shop Four", ITEMS["WG"], 3, 3.3, VAN_A),
        line(d(mar, 9), 12, 2, "Shop Four", ITEMS["ZZ"], 1, 1.1, VAN_A),
        line(d(mar, 30), 13, 1, "Cash Customer", ITEMS["PHONE"], 6, 0, SHOW, "ID 987654321 given"),
        line(d(mar, 30), 14, 1, "Shop One", ITEMS["GN"], 10, 5.5, VAN_A, "credit note"),
    ]
    # v_sales masks a 9-digit run before the engine ever sees it (the R7b mask): do the same here
    for r in rows:
        if r["narration"]:
            r["narration"] = re.sub(r"(?<!\d)\d{6}(\d{3})(?!\d)", r"******\1", r["narration"])
    return rows


class FakeSource(build.Source):
    """The report's reads answered from the world (and counted, so a test can say nothing else was read)."""

    def __init__(self, rows=None, to: date = date(2026, 4, 7), uploaded: str = "2026-04-08T09:00:00+03:00",
                 upcoming_fails: bool = False):
        self.rows = world_rows() if rows is None else rows
        self.to, self.uploaded, self.upcoming_fails = to, uploaded, upcoming_fails
        self.calls: list[str] = []

    def anchors(self) -> dict:
        self.calls.append("anchors")
        return {"focus_to": self.to.isoformat(), "uploaded_at": self.uploaded}

    def lines(self, first, last):
        self.calls.append("lines")
        return [r for r in self.rows if first.isoformat() <= r["sale_date"] <= last.isoformat()]

    def totals(self, first, cur_first, last):
        self.calls.append("totals")
        out: dict = {}
        for r in self.lines(first, last):
            k = "prev" if r["sale_date"] < cur_first.isoformat() else "cur"
            t = out.setdefault(k, {"lines": 0, "qty": 0, "gross": 0, "net": 0})
            t["lines"] += 1
            t["qty"] += r["quantity"] or 0
            t["gross"] = round(t["gross"] + (r["gross_bhd"] or 0), 3)
            t["net"] = round(t["net"] + (r["net_bhd"] or 0), 3)
        return out

    def upcoming(self) -> dict:
        self.calls.append("upcoming")
        if self.upcoming_fails:
            raise RuntimeError("no session")
        return dict(UPCOMING)


def built(rows=None, names=NAMES, prepared_by: str = ""):
    return build.build_month(MONTH, FakeSource(rows), prepared_on=date(2026, 4, 8), prepared_by=prepared_by,
                             category_names=names)


def value(b, sheet: str, ref: str):
    r, c = fx.parse_cell(ref)
    cell = b.get(sheet).cells.get((r, c))
    return None if cell is None else cell.value


def formula_of(b, sheet: str, ref: str):
    r, c = fx.parse_cell(ref)
    cell = b.get(sheet).cells.get((r, c))
    return None if cell is None else cell.formula


def find_row(b, sheet: str, col: str, text: str) -> int:
    """The Excel row of the first cell in `col` holding `text`."""
    c = fx.col_index(col)
    for (r, cc), cell in sorted(b.get(sheet).cells.items()):
        if cc == c and cell.value == text:
            return r + 1
    raise AssertionError(f"{text!r} not found in {sheet}!{col}")


class data_root:
    """A temporary data root (exports/, business_data/), optionally with a last load on record."""

    def __init__(self, last_load: dict | None = None, cfg: dict | None = None):
        self.last_load, self.cfg = last_load, cfg

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name).resolve()
        paths.set_data_root(self.root)
        config.use(self.cfg)
        if self.cfg is None:
            config.use(None)                    # no config.json: the report keys read their defaults
        if self.last_load:
            status.write_state({"last_load": self.last_load})
        return self.root

    def __exit__(self, *exc):
        paths.set_data_root(None)
        config.use(None)
        self._tmp.cleanup()
        return False


class Args:
    def __init__(self, spec=("month", MONTH), prepared_on="2026-04-08", prepared_by=None, compare=None):
        self.spec, self.prepared_on, self.prepared_by, self.compare = list(spec), prepared_on, prepared_by, compare


LONG_RUN = re.compile(r"(?<!\d)\d{9}(?!\d)|\d{15,}")


# ── the formula evaluator ──────────────────────────────────────────────────────────────────────

@test("formulas: Excel's precedence, text and numbers as a spreadsheet compares them")
def _():
    assert ev("1+2*3") == 7 and ev("-2^2") == 4 and ev("(1+2)*3") == 9 and ev("2^3^2") == 64
    assert ev("10/4") == 2.5 and ev("7-2-1") == 4 and ev('"a"&"b"&1&TRUE') == "ab1TRUE"
    assert ev('"Shop"="shop"') is True and ev('"a"<"B"') is True and ev("0.1+0.2=0.3") is True
    assert ev('1<"1"') is True                         # a number sorts before any text
    assert ev("A1+1", {"A1": None}) == 1 and ev('A1&"x"', {"A1": None}) == "x"
    assert isinstance(ev("1/0"), fx.Err) and ev("IFERROR(1/0,7)") == 7
    assert ev("IF(1/0,1,2)").code == "#DIV/0!"         # an error flows, it is not a crash


@test("formulas: rounding half away from zero, FIXED, dates and month names")
def _():
    assert ev("ROUND(2.675,2)") == 2.68 and ev("ROUND(-2.5,0)") == -3 and ev("ROUND(1234.5678,-2)") == 1200
    assert ev("FIXED(5961.5,0)") == "5,962" and ev("FIXED(1234.56,1)") == "1,234.6" and ev("FIXED(0.4,0)") == "0"
    jan31 = fx.serial(date(2026, 1, 31))
    assert ev(f"EDATE({jan31},1)") == fx.serial(date(2026, 2, 28)) and ev(f"EDATE({jan31},-1)") == fx.serial(date(2025, 12, 31))
    assert ev("DATE(2026,13,1)") == fx.serial(date(2027, 1, 1)) and ev(f"DAY(EDATE({jan31},1)-1)") == 27
    assert ev(f'CHOOSE(MONTH({jan31}),"Jan","Feb")&" "&YEAR({jan31})') == "Jan 2026"
    assert ev('SUBSTITUTE("Van A - Acc WH"," - Acc WH","")') == "Van A"


@test("formulas: criteria carry their operator, whole-cell wildcards and case-blind text")
def _():
    cells = {"A1": "Cash Customer", "A2": "cash customer", "A3": "Shop *1", "A4": None, "A5": "Shop 2",
             "B1": 1, "B2": 2, "B3": 3, "B4": 4, "B5": 5}
    assert ev('SUMIFS(B1:B5,A1:A5,"CASH CUSTOMER")', cells) == 3
    assert ev('COUNTIFS(A1:A5,"shop*")', cells) == 2 and ev('COUNTIFS(A1:A5,"Shop ~*1")', cells) == 1
    assert ev('COUNTIFS(A1:A5,"")', cells) == 1 and ev('COUNTIFS(A1:A5,"<>")', cells) == 4
    assert ev('COUNTIFS(B1:B5,">=3")', cells) == 3 and ev('SUMIFS(B1:B5,B1:B5,"<>2")', cells) == 13
    assert ev('COUNTIFS(A1:A5,"cash*",B1:B5,">1")', cells) == 1
    assert ev("SUMIFS(B1:B5,B1:B5,3)", cells) == 3 and ev('COUNTIF(B1:B5,">0")', cells) == 5
    assert ev('COUNTIFS(A1:A5,"Shop")', cells) == 0                     # a whole cell must match


@test("formulas: MATCH, INDEX, LOOKUP(2,1/(...)) and the SUMPRODUCT rank with ties")
def _():
    cells = {"A1": "x", "A2": "Shop", "A3": "y", "A4": "shop", "B1": 10, "B2": 30, "B3": 30, "B4": 20}
    assert ev('MATCH("SHOP",A1:A4,0)', cells) == 2 and ev("INDEX(B1:B4,3)", cells) == 30
    assert ev('MATCH("none",A1:A4,0)', cells).code == "#N/A"
    assert ev('LOOKUP(2,1/(A1:A4="shop"),B1:B4)', cells) == 20          # the last row the condition holds for
    ranks = [ev(f"SUMPRODUCT(--($B$1:$B$4>B{i}))+SUMPRODUCT(--($B$1:B{i}=B{i}))", cells) for i in range(1, 5)]
    assert ranks == [4, 1, 2, 3], ranks                                 # ties ranked by their order
    assert ev("COUNTA(A1:A4)", cells) == 4 and ev("SUM(B1:B4)", cells) == 90


@test("formulas: an unknown function or a circular reference is refused, never given a value")
def _():
    raises(fx.FormulaError, lambda: ev("VLOOKUP(1,A1:A2,1,0)"))
    raises(fx.FormulaError, lambda: ev("NOTANAME+1"))
    exc = raises(fx.FormulaError, lambda: ev("1", {"A1": "=A2", "A2": "=A1"}))
    assert "circular" in str(exc)
    assert ev("Total", names={"Total": "S!$A$1"}) is None
    raises(fx.FormulaError, lambda: ev("1+"))


# ── the category rule ──────────────────────────────────────────────────────────────────────────

@test("categories: the longest alphabetic prefix; TB is its own row and never TWS")
def _():
    assert cats.code_prefix("GN-08C") == "GN" and cats.code_prefix("UK12") == "UK" and cats.code_prefix("TB-D3") == "TB"
    assert cats.by_code("T17") == "TWS Earbuds" and cats.by_code("TB-D3") == cats.BUNDLES
    assert cats.by_code("GN-08C") == "Power Banks" and cats.by_code("G176-1") == "Power Banks"
    assert cats.by_code("BE02") == "Headphones & Neckbands" and cats.by_code("BS06") == "Bluetooth Speakers"
    assert cats.by_code("ZTE-A35e") is None and cats.by_code("WDC-C37") is None and cats.by_code("9X") is None


@test("categories: an unknown code through the name table (code beats card), no value to the handset row, else Other")
def _():
    assert cats.resolve("WG-01", ["Wireless Audio"], NAMES) == ("Headphones & Neckbands", "name table")
    assert cats.resolve("WS-01", ["Wireless Audio"], NAMES) == ("TWS Earbuds", "name table")
    assert cats.resolve("ws-01", ["wireless audio"], {}) == (cats.OTHER, "none")
    assert cats.resolve("ZTE-A35e", [], {}, no_value=True) == (cats.HANDSET, "no value")
    assert cats.resolve("T17", [], {"T17": "Cables"}) == ("TWS Earbuds", "code")       # the rule comes first
    text = cats.rule_paragraph({})
    for part in ("T = TWS earbuds", "X, P and L = cables", "F, G, GN and K = power banks", "TB = bundle sets"):
        assert part in text, part
    assert "WG-01 = Headphones & Neckbands" in cats.rule_paragraph(NAMES)


@test("config: the two report keys read their defaults when config.json has none, and are type-checked")
def _():
    with data_root():
        assert config.get("report.category_names") == {} and config.get("report.prepared_by") == ""
        assert "report.category_names" not in config.missing_keys()
        paths.write_text(paths.config_file(), json.dumps({"report.category_names": ["not", "a", "table"]}))
        config.use(None)
        raises(config.ConfigMissing, lambda: config.get("report.category_names"))
        paths.write_text(paths.config_file(), json.dumps({"report.category_names": {"WG-01": "Headphones & Neckbands"}}))
        config.use(None)
        assert config.get("report.category_names") == {"WG-01": "Headphones & Neckbands"}


# ── the data rules ─────────────────────────────────────────────────────────────────────────────

@test("data: SI prefix off, one cash customer, a masked ID left out, empty money 0, ordered by date and invoice number")
def _():
    lines = mdl.tidy(world_rows())
    assert [ln.invoice for ln in lines][:3] == ["SI-YQ-26-02-1", "SI-YQ-26-02-1", "SI-YQ-26-02-2"]
    march = [ln.invoice for ln in lines if ln.sale_date == date(2026, 3, 1)]
    assert march == ["SI-YQ-26-03-9", "SI-YQ-26-03-10", "SI-YQ-26-03-10"], march   # 9 before 10, by the number
    cash = {ln.customer for ln in lines if "ash" in ln.customer.lower()}
    assert cash == {mdl.CASH_ACCOUNT}, cash
    phone = [ln for ln in lines if ln.item == ITEMS["PHONE"]]
    assert all(ln.narration == "" and ln.narration_masked for ln in phone)
    assert all(ln.rate == 0 and ln.gross == 0 and ln.net == 0 for ln in phone)
    credit = [ln for ln in lines if "credit note" in ln.narration]
    assert len(credit) == 2 and not any(ln.narration_masked for ln in credit)
    assert mdl.product_name(ITEMS["T01"], "T01") == "Demo Earbuds One" and lines[0].rep == "Van A"


@test("data: the lists and their order; categories by the rule; long labels cut at a whole word")
def _():
    m = mdl.build(mdl.Period.month(MONTH), world_rows(), category_names=NAMES, upcoming=UPCOMING)
    assert m.period.prev_start == date(2026, 2, 1) and m.period.end == date(2026, 3, 31)
    assert m.reps == ["Van A", "Van B", "Causeway"], m.reps            # by March, then February, then name
    assert m.customers[0] == mdl.CASH_ACCOUNT and m.customers[1:] == ["Shop One", "Shop Two", "Shop Four", "Shop Three"]
    cat_of = {it.code: it.category for it in m.items.values()}
    assert cat_of["TB-D1"] == cats.BUNDLES and cat_of["WS-01"] == "TWS Earbuds"
    assert cat_of["WG-01"] == "Headphones & Neckbands" and cat_of["ZZ9"] == cats.OTHER
    assert cat_of["ZTE-X1"] == cats.HANDSET and [it.code for it in m.not_counted] == ["ZTE-X1"]
    assert m.categories[-1] == cats.OTHER and cats.HANDSET not in m.categories
    assert m.categories == ["TWS Earbuds", "Power Banks", "Cables", cats.BUNDLES, "Headphones & Neckbands",
                            cats.OTHER], m.categories
    long = "X99 " + "Demo Very Long Cable Name With Many Words + Extras " * 3
    label = mdl.dashboard_label("X99", long[4:])
    assert label.endswith(" ...") and mdl.label_width(label) <= mdl.LABEL_ROOM_PRODUCT
    assert not label[:-4].rstrip().endswith("+") and long.startswith(label[:-4])
    assert mdl.dashboard_label("X01", "Short") == "X01 Short"
    raises(ValueError, lambda: mdl.month_bounds("2026-13"))
    raises(ValueError, lambda: mdl.month_bounds("Sep 2026"))
    raises(NotImplementedError, lambda: mdl.Period.week(date(2026, 3, 7)))


# ── the eight sheets ───────────────────────────────────────────────────────────────────────────

@test("sheets: the eight sheets in the Sep 2026 order, every formula with a value and none an error")
def _():
    b, m, _f = built()
    assert [s.name for s in b.sheets] == ["Dashboard", "Salesmen", "Categories", "Products", "Customers", "Daily",
                                          "Data", "Notes"]
    assert not m.warnings, m.warnings
    n = 0
    for ws in b.sheets:
        for (_r, _c), cell in ws.cells.items():
            if cell.kind == "f":
                n += 1
                assert not isinstance(cell.value, fx.Err), (ws.name, cell.formula, cell.value)
    assert n > 300, n


@test("sheets: the Dashboard tiles are the hand sums (the handset left out of units, invoices and accounts)")
def _():
    b, m, _f = built()
    rows = world_rows()
    mar = [r for r in rows if r["sale_date"] >= "2026-03-01"]
    feb = [r for r in rows if r["sale_date"] < "2026-03-01"]
    net = lambda rs: sum(r["net_bhd"] or 0 for r in rs)  # noqa: E731
    acc = lambda rs: [r for r in rs if r["item_name"] != ITEMS["PHONE"]]  # noqa: E731
    assert abs(value(b, "Dashboard", "B9") - net(mar)) < 1e-9 and abs(value(b, "Dashboard", "C10") - net(feb)) < 1e-9
    assert value(b, "Dashboard", "F9") == sum(r["quantity"] for r in acc(mar)) == 71
    assert value(b, "Dashboard", "H9") == len({r["invoice_no"] for r in acc(mar)}) == 5
    assert value(b, "Dashboard", "O9") == 3                     # Shop One, Shop Two, Shop Four (cash never counts)
    assert value(b, "Dashboard", "Q9") == 3 and value(b, "Dashboard", "R10") == 3
    assert value(b, "Dashboard", "K3") == "Prepared 8 April 2026"
    assert value(b, "Dashboard", "B5") == "March 2026 compared with February 2026"
    foot = value(b, "Dashboard", "B78")
    assert foot == ("Units, invoices and accounts cover accessories only. ZTE-X1 handsets issued through the YQ "
                    "Roadshow carry no value in the day book (4 units in February, 6 in March) and are excluded."), foot
    assert "1 February to 31 March 2026" in value(b, "Dashboard", "B77")


@test("sheets: every sheet's total less the Data sheet is nil, and the Data sheet equals v_sales")
def _():
    b, _m, _f = built()
    top = find_row(b, "Notes", "B", "Sheet totals less Data (net sales)")
    for k in range(1, 6):
        assert value(b, "Notes", f"C{top + k}") == 0 and value(b, "Notes", f"D{top + k}") == 0
    exp = find_row(b, "Notes", "B", "Data sheet against the export file")
    for k in range(1, 5):
        assert value(b, "Notes", f"G{exp + k}") == 0, (k, value(b, "Notes", f"G{exp + k}"))
    assert value(b, "Notes", f"C{exp + 1}") == 5 and value(b, "Notes", f"D{exp + 1}") == 8   # every line, the zero-value ones too
    db_row = find_row(b, "Notes", "B", "Day book (Data sheet) less v_sales")
    assert value(b, "Notes", f"C{db_row}") == 0 and value(b, "Notes", f"D{db_row}") == 0
    left = find_row(b, "Notes", "B", "Narrations left out (they hold an ID number)")
    assert value(b, "Notes", f"C{left}") == 1 and value(b, "Notes", f"D{left}") == 1
    assert value(b, "Notes", f"E{left + 1}") == 0
    unmapped = find_row(b, "Notes", "B", "Day book lines with no category")
    assert value(b, "Notes", f"C{unmapped}") == 0 and value(b, "Notes", f"D{unmapped}") == 0


@test("sheets: the Data sheet's calculated columns: month, salesman, category, line type, units and the flags")
def _():
    b, m, _f = built()
    assert value(b, "Data", "A5") == fx.serial(date(2026, 2, 2)) and value(b, "Data", "K5") == fx.serial(date(2026, 2, 1))
    assert value(b, "Data", "B5") == "SI-YQ-26-02-1" and value(b, "Data", "L5") == "Van A"
    for i, ln in enumerate(m.lines):
        r = 5 + i
        handset = ln.item == ITEMS["PHONE"]
        assert value(b, "Data", f"O{r}") == ("Handset" if handset else "Accessory")
        assert value(b, "Data", f"P{r}") == (0 if handset else ln.qty)
        assert value(b, "Data", f"M{r}") == m.items[ln.item].category
        assert value(b, "Data", f"J{r}") == ("" if handset else (ln.narration or ""))
    assert formula_of(b, "Data", "Q6") == 'IF(O6="Accessory",IF(COUNTIFS($B$5:B6,B6,$O$5:O6,"Accessory")=1,1,0),0)'
    flags = [value(b, "Data", f"Q{5 + i}") for i in range(len(m.lines))]
    assert sum(flags) == len({ln.invoice for ln in m.lines if ln.item != ITEMS["PHONE"]})
    assert b.names["d_Net"] == f"Data!$H$5:$H${4 + len(m.lines)}"


@test("sheets: Salesmen, Categories, Products and Customers follow the lists, totals at the end")
def _():
    b, m, _f = built()
    S, P, C = len(m.reps), len(m.products), len(m.customers)
    tot = 6 + S
    assert value(b, "Salesmen", f"B{tot}") == "Total" and formula_of(b, "Salesmen", f"C{tot}") == f"SUM(C6:C{5 + S})"
    assert value(b, "Salesmen", "B6") == "Van A" and formula_of(b, "Salesmen", f"G6") == f"IF(D${tot}=0,0,D6/D${tot})"
    head = tot + 4
    assert value(b, "Salesmen", f"B{head}").startswith("Net sales by seven-day period")
    assert value(b, "Salesmen", f"C{head + 1}") == fx.serial(date(2026, 2, 1))
    assert len(b.get("Salesmen").sparklines) == S
    week_sum = sum(value(b, "Salesmen", f"{fx.col_letters(c)}{head + 2 + S}") for c in range(2, 2 + 9))
    assert abs(week_sum - value(b, "Salesmen", f"C{tot}") - value(b, "Salesmen", f"D{tot}")) < 1e-9
    cat_tot = 6 + len(m.categories)
    assert value(b, "Categories", f"B{cat_tot}") == "Total"
    assert value(b, "Categories", f"B{cat_tot - 1}") == cats.OTHER
    assert abs(value(b, "Categories", f"G{cat_tot}") - value(b, "Dashboard", "B9")) < 1e-9
    ptot = 6 + P
    assert value(b, "Products", f"B{ptot}") == "Total"
    assert value(b, "Products", f"B{ptot + 1}").startswith("Not included above: handsets")
    assert value(b, "Products", f"B{ptot + 2}") == "ZTE-X1" and value(b, "Products", f"F{ptot + 2}") == 6
    assert b.names["p_Cat"] == f"Products!$D$6:$D${ptot + 2}"
    assert sorted(value(b, "Products", f"N{6 + i}") for i in range(P)) == list(range(1, P + 1))
    ctot = 6 + C
    assert value(b, "Customers", "B6") == mdl.CASH_ACCOUNT and value(b, "Customers", "J6") == "Cash"
    assert value(b, "Customers", "K6") == "" and value(b, "Customers", "D6") == "Various"
    status_of = {value(b, "Customers", f"B{6 + i}"): value(b, "Customers", f"J{6 + i}") for i in range(C)}
    assert status_of == {mdl.CASH_ACCOUNT: "Cash", "Shop One": "Feb & Mar", "Shop Two": "Mar only",
                         "Shop Four": "Mar only", "Shop Three": "Feb only"}, status_of
    assert value(b, "Customers", "D7") == "Van A"                      # the salesman of the latest invoice
    assert value(b, "Customers", f"B{ctot}") == "Total"


@test("sheets: the Daily sheet has 31 days, the week blocks add up to each month, the days a month lacks are blank")
def _():
    b, _m, _f = built()
    assert value(b, "Daily", "B36") == 31 and formula_of(b, "Daily", "D36") == "SUMIFS(d_Net,d_Date,CurMonth+$B36-1)"
    assert formula_of(b, "Daily", "C34") is None and formula_of(b, "Daily", "C36") is None   # February has 28 days
    assert abs(value(b, "Daily", "I11") - value(b, "Daily", "C37")) < 1e-9
    assert abs(value(b, "Daily", "K11") - value(b, "Daily", "D37")) < 1e-9
    assert value(b, "Daily", "J11") == 1 and value(b, "Daily", "L11") == 1


@test("sheets: the commentary is DRAFT; the Notes sheet's sentences come from the data and the rule in force")
def _():
    b, _m, _f = built()
    for ref in b.commentary["Dashboard"]:
        assert str(value(b, "Dashboard", ref)).startswith("DRAFT"), ref
    assert str(value(b, "Customers", b.commentary["Customers"][0])).startswith("DRAFT")
    text = " ".join(str(c.value) for c in b.get("Notes").cells.values() if c.kind == "s")
    assert "Two invoices carry \"credit note\" in the narration" in text, text[:2000]
    assert "ZTE-X1 handsets issued through the YQ Roadshow carry no value in the day book (2 lines, 10 units)" in text
    assert "The three spellings of Cash Customer in the ERP are treated as one" in text
    assert "TB = bundle sets (a display group)" in text and "WG-01 = Headphones & Neckbands" in text
    assert "The narration on the handset invoices holds customer ID numbers" in text
    words = sc.Commentary(highlights=["h"] * 5, points=["p"] * 5)
    assert not words.is_draft and sc.Commentary().is_draft


@test("sheets: positions follow the list lengths (one more salesman moves every reference to the totals)")
def _():
    rows = world_rows() + [line(date(2026, 3, 20), 30, 1, "Shop Five", ITEMS["X01"], 1, 1.1, "Van C - Acc WH")]
    b, m, _f = built(rows)
    S = len(m.reps)
    assert S == 4 and value(b, "Salesmen", f"B{6 + S}") == "Total"
    top = find_row(b, "Notes", "B", "Sheet totals less Data (net sales)")
    assert formula_of(b, "Notes", f"C{top + 1}") == f"ROUND(Salesmen!C{6 + S}-SUMIFS(d_Net,d_Month,PrevMonth),2)"
    assert formula_of(b, "Dashboard", "Q9") == f'COUNTIF(Salesmen!D6:D{5 + S},">0")'
    assert value(b, "Categories", f"B{6 + len(m.categories) + 4 + 2 + S}") == "Total"


# ── the file ───────────────────────────────────────────────────────────────────────────────────

@test("file: written through a temporary name, never over an existing file; the next free name; a valid workbook")
def _():
    b, m, _f = built()
    with data_root() as root:
        first = build.target_path(m.period, draft=True)
        assert first.name == "YQ Bahrain - Mobile Accessories Sales Report - Mar 2026 - DRAFT.xlsx"
        assert first.parent == root / "exports" / "reports" / "monthly"
        b.save(first)
        assert first.is_file() and not [p for p in first.parent.iterdir() if p.name.startswith("~build-")]
        raises(FileExistsError, lambda: b.save(first))
        second = build.target_path(m.period, draft=True)
        assert second.name.endswith("DRAFT (2).xlsx")
        with zipfile.ZipFile(first) as z:
            assert "xl/worksheets/sheet8.xml" in z.namelist() and len([n for n in z.namelist() if n.startswith("xl/charts/")]) == 4
            xml = z.read("xl/workbook.xml").decode("utf-8")
            assert 'calcPr' in xml and "fullCalcOnLoad" in xml       # the file is recalculated when it opens
        part = golden.read_book(first)
        assert part.sheet("Dashboard").pane is None and part.sheet("Data").pane["topLeftCell"] == "C5"
        assert part.print_areas["Dashboard"] == "Dashboard!$A$1:$S$80" and part.sheet("Data").autofilter.startswith("A4:S")


@test("file: the counts of rules, data bars, the colour scale, sparklines and charts")
def _():
    b, m, _f = built()
    with data_root():
        path = b.save(build.target_path(m.period, draft=True))
        part = golden.read_book(path)
    total = lambda what: sum(getattr(s, what) for s in part.sheets)  # noqa: E731
    assert (total("cell_rules"), total("data_bars"), total("color_scales"), total("charts")) == (20, 6, 1, 4)
    assert total("sparklines") == len(m.reps)


@test("file: the comparison with a reference sees a changed value and sets aside the cells it is told to")
def _():
    b, m, _f = built()
    with data_root() as root:
        path = b.save(build.target_path(m.period, draft=True))
        same = golden.compare(path, path)
        assert same.ok and all(s.ok for s in same.sheets)
        changed = root / "exports" / "changed.xlsx"
        with zipfile.ZipFile(path) as zin, zipfile.ZipFile(changed, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":          # the Dashboard: one cached value moved
                    text = data.decode("utf-8")
                    text = re.sub(r'(<c r="F9"[^>]*><f>[^<]*</f><v>)([^<]+)(</v>)', r"\g<1>12345\3", text, count=1)
                    data = text.encode("utf-8")
                zout.writestr(item, data)
        diff = golden.compare(changed, path)
        dash = next(s for s in diff.sheets if s.name == "Dashboard")
        assert not diff.ok and [v[0] for v in dash.value] == ["F9"], dash.value
        aside = golden.compare(changed, path, set_aside={"Dashboard": ["F9"]})
        assert aside.ok and next(s for s in aside.sheets if s.name == "Dashboard").set_aside == 1
        assert any("F9" in ln for ln in golden.summary(diff))


@test("file: no 9-digit or 15-plus-digit run in what the owner reads (the masked IDs are left out)")
def _():
    b, m, _f = built()
    with data_root():
        path = b.save(build.target_path(m.period, draft=True))
        with zipfile.ZipFile(path) as z:
            strings = z.read("xl/sharedStrings.xml").decode("utf-8")
    assert not LONG_RUN.search(strings), LONG_RUN.search(strings).group(0)
    assert "******" not in strings                                   # not even the masked form


# ── the command ────────────────────────────────────────────────────────────────────────────────

LAST_LOAD = {"run": "080426_0900", "finished_at": "2026-04-08T09:00:00+03:00", "result": "ok", "data_date": "2026-04-07"}


@test("command: a whole month builds the DRAFT workbook and a record, and says what it checked")
def _():
    with data_root(last_load=LAST_LOAD) as root:
        src = FakeSource()
        out = build.run(Args(), source=src)
        assert out["ok"] and out.get("exit") == 0, out["text"]
        assert out["data_date"] == "2026-04-07" and out["flags"]["month"] == MONTH and out["flags"]["draft"]
        path = Path(out["files"][0])
        assert path.is_file() and path.parent == root / "exports" / "reports" / "monthly"
        record = json.loads(Path(out["files"][1]).read_text(encoding="utf-8"))
        assert record["month"] == MONTH and record["checks_ok"] and record["draft"]
        text = out["text"]
        assert text.startswith("Monthly sales report, March 2026, against February 2026")
        assert "Checks: the Data sheet equals v_sales in both months" in text and "DRAFT: the ten comment lines" in text
        assert not LONG_RUN.search(text)
        assert sorted(set(src.calls)) == ["anchors", "lines", "totals", "upcoming"]
        again = build.run(Args(), source=FakeSource())
        assert Path(again["files"][0]).name.endswith("DRAFT (2).xlsx")      # the first file is kept


@test("command: not a whole month yet (no load after the month, or data short of its end): nothing is written")
def _():
    early = {"run": "250326_0900", "finished_at": "2026-03-25T09:00:00+03:00", "result": "ok"}
    with data_root(last_load=early) as root:
        out = build.run(Args(), source=FakeSource(to=date(2026, 3, 24)))
        assert not out["ok"] and out["exit"] == 2 and "not whole yet" in out["text"], out["text"]
        assert not (root / "exports" / "reports").exists()
    with data_root() as root:                 # no load on record: the database's own upload time stands in
        out = build.run(Args(), source=FakeSource(uploaded="2026-03-30T10:00:00+03:00"))
        assert not out["ok"] and "newest successful upload" in out["text"]
        out = build.run(Args(), source=FakeSource())
        assert out["ok"], out["text"]


@test("command: the week, a bad month and a month with no sales are refused in one sentence")
def _():
    with data_root(last_load=LAST_LOAD):
        out = build.run(Args(spec=("week",)), source=FakeSource())
        assert out["exit"] == 1 and "later phase" in out["text"]
        out = build.run(Args(spec=("month", "2026-13")), source=FakeSource())
        assert out["exit"] == 1 and "YYYY-MM" in out["text"]
        out = build.run(Args(spec=("monthly",)), source=FakeSource())
        assert out["exit"] == 1 and "report month" in out["text"]
        out = build.run(Args(spec=("month", "2026-01")), source=FakeSource())
        assert out["exit"] == 2 and "nothing to report" in out["text"], out["text"]
    assert build.parse_spec([]) == ("month", None) and build.parse_spec(["month 2026-03"]) == ("month", "2026-03")
    assert build.default_month(date(2026, 4, 7)) == "2026-03" and build.default_month(date(2026, 1, 3)) == "2025-12"


@test("command: without the Upcoming cards the build goes on and says so")
def _():
    b, m, _f = build.build_month(MONTH, FakeSource(upcoming_fails=True), prepared_on=date(2026, 4, 8),
                                 category_names=NAMES)
    assert any("Upcoming cards could not be read" in w for w in m.warnings)
    assert m.items[ITEMS["WS"]].category == cats.OTHER                  # no card: the name table has no key for it
    assert m.items[ITEMS["WG"]].category == "Headphones & Neckbands"     # the code key still holds


# ── the real September file (local only) ───────────────────────────────────────────────────────

@test("golden: September 2026 from production against the reference (SKIP without the file or YQ_OPS_GOLDEN=1)")
def _():
    if not REFERENCE.is_file():
        print(f"      SKIP: the reference workbook is not on this machine ({REFERENCE.name})")
        return
    if os.environ.get("YQ_OPS_GOLDEN") != "1":
        print("      SKIP: set YQ_OPS_GOLDEN=1 to rebuild September 2026 from production (read-only) and compare")
        return
    env = {k: v for k, v in os.environ.items() if k not in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL")}
    env["PYTHONUTF8"] = "1"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        cmd = [sys.executable, "-m", "scripts.ops", "report", "month", "2026-09", "--data-root", tmp,
               "--prepared-on", "2026-10-04", "--compare", str(REFERENCE), "--json"]
        if os.environ.get("YQ_OPS_GOLDEN_BY"):
            cmd += ["--prepared-by", os.environ["YQ_OPS_GOLDEN_BY"]]
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8",
                              timeout=900)
    last = (proc.stdout.strip().splitlines() or [""])[-1]
    try:
        out = json.loads(last)
    except ValueError:
        print("      SKIP: the database could not be read: " + (proc.stdout + proc.stderr).strip()[-200:])
        return
    print("\n".join("      " + ln for ln in out.get("text", "").splitlines()[-12:]))
    assert out.get("flags", {}).get("golden_ok") is True, out.get("text")


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
    config.use(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
