"""Ops engine: the weekly sales workbook (python -m scripts.ops report week [YYYY-MM-DD]).

    python -m tests.test_ops_week

Same lightweight runner as the other suites (no pytest). No database, no network, no .env: SUPABASE_URL,
SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import and scripts.ops.db is imported first.
The report reads through a Source; here a stand-in answers from an in-memory list of invented day-book lines
(two vans, an outlet, a few shops, round amounts) spread over the four windows of the week ending Saturday
17 October 2026, and from an invented stock model, transfer list, purchases list and KPI log. Every figure
below is a sum a reader can redo by hand. Every folder is a temporary one; all names and numbers are invented.

Covered:
  * the windows: the Notes sheet's formulas over the typed Sunday give model.Period.week's days for a week in a
    month, across two months, starting on the 31st, ending on the 31st and across the year's end; the business
    days left after the week against app.metrics.business_days for every Saturday of a year;
  * the week key 7*ROUNDUP(A/7,0): the Saturday that ends the week of every day across month and year ends, and
    on the Data sheet;
  * the sheets: every formula evaluated, none an error value; the tiles and the windows table against hand sums
    in each window, the account counts (once per account and week, month, same days) among them; the Salesmen
    sheet's five weekly columns and sparklines; the Daily sheet's same-weekday averages; the Customers statuses;
  * the Notes controls: the Data sheet against v_sales in all four windows and every sheet's total less the Data
    sheet are nil; a line lost on the way is caught;
  * the three comment lines: their text, the KPI step reached, no step set, a part not worked out;
  * the command: the default week (the brief's last full week), a part-day Saturday, a day that is not a Saturday,
    an incomplete week, a week with no sales; the hand-over from `report week`;
  * the parts: each one failing on its own leaves the others standing; the Stock and Movements sheets from a
    stand-in shared module, refused when they name an item or a voucher; no item name or voucher on Stock,
    Movements or Actions; the counts due; the optional modules not built yet;
  * the file: the next free name, never over an existing file, a valid workbook (charts, sparklines, cached
    values); no 9-digit run in it; the build record's shape, its due day and on-time flag.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import traceback
import types
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
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
from scripts.ops import config, paths, status, stock  # noqa: E402
from scripts.ops.workbook import book as bk  # noqa: E402
from scripts.ops.workbook import build, formula as fx, golden, model as mdl  # noqa: E402
from scripts.ops.workbook import sheets_week as sw, week  # noqa: E402
from app import metrics  # noqa: E402

TESTS: list[tuple[str, object]] = []
SAT = date(2026, 10, 17)                     # the week: Sunday 11 to Saturday 17 October 2026
TO = date(2026, 10, 20)                      # the data date
FIXTURE = {"sales.kpi_steps": [{"from": "2026-01", "bhd": 100}], "report.category_names": {},
           "report.prepared_by": ""}
ABSENT = "scripts.ops.workbook._no_such_module_here"
BAHRAIN = status.BAHRAIN


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


# ── the synthetic world ────────────────────────────────────────────────────────────────────────

VAN_A, VAN_B, OUTLET = "Van A - Acc WH", "Van B - Acc WH", "Causeway"
ITEMS = {"T01": "T01 Demo Earbuds One (VFAN) T01", "X01": "X01 Demo Cable One (VFAN)", "GN": "GN-01 Demo Power Bank",
         "ZZ": "ZZ9 Demo Gadget", "PHONE": "ZTE-X1 DEMO PHONE"}


def line(day: date, inv: int, no: int, cust: str, item: str, qty: int, rate: float, rep: str, narr: str | None = None):
    gross = round(qty * rate, 3)
    net = round(gross / 1.1, 2) if gross else None
    return {"line_id": inv * 10 + no, "invoice_no": f"SI : SI-YQ-26-{day.month:02d}-{inv}", "line_no": no,
            "sale_date": day.isoformat(), "customer_account": cust, "customer_name": cust, "item_name": item,
            "quantity": float(qty), "rate_bhd": rate if rate else None, "gross_bhd": gross if gross else None,
            "net_bhd": net, "salesman_raw": rep, "salesman_resolved": rep, "narration": narr,
            "category_name": None, "discount_bhd": None, "vat_amount_bhd": None}


def world_rows() -> list[dict]:
    d = date.fromisoformat
    t01, x01, gn, zz, phone = (ITEMS[k] for k in ("T01", "X01", "GN", "ZZ", "PHONE"))
    rows = [
        line(d("2026-09-10"), 1, 1, "Shop One", t01, 7, 2.2, VAN_A),              # before every window: never read
        line(d("2026-09-11"), 2, 1, "Shop One", t01, 2, 2.2, VAN_A),              # the same days last month only
        line(d("2026-09-12"), 3, 1, "Cash customer", x01, 3, 1.1, OUTLET),
        line(d("2026-09-13"), 4, 1, "Shop Two", x01, 10, 1.1, VAN_B),             # last month and the weeks before
        line(d("2026-09-15"), 5, 1, "Shop One", gn, 1, 5.5, VAN_A),
        line(d("2026-09-15"), 5, 2, "Shop One", t01, 1, 2.2, VAN_A),              # one invoice, two lines
        line(d("2026-09-20"), 6, 1, "Shop Three", t01, 4, 2.2, VAN_B),            # the four weeks before
        line(d("2026-09-24"), 7, 1, "Shop One", x01, 5, 1.1, VAN_A),
        line(d("2026-09-27"), 8, 1, "Shop Two", gn, 2, 5.5, VAN_B),
        line(d("2026-09-30"), 9, 1, "Cash Customer", phone, 2, 0, OUTLET, "ID 123456789 given"),   # no value: a handset
        line(d("2026-10-01"), 10, 1, "Shop One", t01, 3, 2.2, VAN_A),             # the month to date, before the week
        line(d("2026-10-06"), 11, 1, "Shop Four", zz, 1, 1.1, VAN_A),
        line(d("2026-10-08"), 12, 1, "CASH CUSTOMER", x01, 2, 1.1, OUTLET),
        line(d("2026-10-11"), 13, 1, "Shop One", t01, 5, 2.2, VAN_A),             # the week
        line(d("2026-10-11"), 13, 2, "Shop One", x01, 10, 1.1, VAN_A),
        line(d("2026-10-12"), 14, 1, "Shop Two", gn, 3, 5.5, VAN_B),
        line(d("2026-10-14"), 15, 1, "Shop One", gn, 1, 5.5, VAN_A),              # Shop One twice: one account
        line(d("2026-10-15"), 16, 1, "Cash Customer", t01, 2, 2.2, OUTLET),
        line(d("2026-10-16"), 17, 1, "Shop Five", x01, 4, 1.1, VAN_B),
        line(d("2026-10-17"), 18, 1, "Cash Customer", phone, 1, 0, OUTLET, "ID 987654321 given"),
        line(d("2026-10-18"), 19, 1, "Shop One", t01, 9, 2.2, VAN_A),             # after the week: never read
    ]
    for r in rows:                     # v_sales masks a 9-digit run before the engine sees it: do the same here
        if r["narration"]:
            r["narration"] = re.sub(r"(?<!\d)\d{6}(\d{3})(?!\d)", r"******\1", r["narration"])
    return rows


def in_window(r: dict, a: date, b: date) -> bool:
    return a.isoformat() <= r["sale_date"] <= b.isoformat()


def window_days(period: mdl.Period, w: str) -> tuple[date, date]:
    return {"wk": (period.start, period.end), "prev": (period.prev_start, period.prev_end),
            "lm": (period.lm_start, period.lm_end), "mtd": (period.mtd_start, period.end)}[w]


def fake_stock_model(d: date = TO, sat: date = SAT):
    """A /yq-stock model with what the Actions sheet reads: three fast movers (one out at the Saturday), an order
    list for two suppliers, one late line on the way, and the main store's supplier receipts."""
    start = date(2026, 7, 3)
    span = (d - start).days + 1

    def item(name: str, code: str, out_on_sat: bool, receipts: list) -> stock.Item:
        it = stock.Item(ledger_name=name, name=name, code=code)
        it.series = [Decimal(5)] * span
        if out_on_sat and sat <= d:
            it.series[(sat - start).days] = Decimal(0)
        it.receipts = receipts
        return it

    new, old, older = (date(2026, 10, 5), "MRN : YQ-26-10-1"), (date(2026, 9, 2), "MRN : YQ-26-09-1"), \
        (date(2026, 7, 10), "MRN : YQ-26-07-1")
    fast = [item("Fast One", "F1", False, [(*new, Decimal(10), Decimal(20))]),
            item("Fast Two", "F2", True, [(*new, Decimal(4), Decimal(8)), (*old, Decimal(6), Decimal(12))]),
            item("Fast Three", "F3", False, [(*older, Decimal(2), Decimal(4))])]
    facts = {"fast": {"list": fast, "rank_first": date(2025, 10, 1), "rank_last": date(2026, 9, 30)},
             "order": [{"supplier": "Supplier One", "qty": Decimal(10)}, {"supplier": "Supplier One", "qty": Decimal(5)},
                       {"supplier": "Supplier Two", "qty": Decimal(7)}],
             "on_the_way": {"log": True, "lines": [{"supplier": "Supplier Two", "late": True, "due": Decimal(4)},
                                                   {"supplier": "Supplier Two", "late": False, "due": Decimal(3)},
                                                   {"supplier": "Supplier One", "late": True, "due": Decimal(0)}]}}
    return types.SimpleNamespace(d=d, series_start=start, facts=facts, items=fast)


class FakeSource(week.WeekSource):
    """The weekly report's reads answered from the world; `fail` names the reads that raise."""

    def __init__(self, rows=None, to: date = TO, stock_as_of: date | None = None, prev: date | None = None,
                 uploaded: str = "2026-10-20T09:00:00+03:00", fail=(), lose_line: str | None = None, model=None):
        self.rows = world_rows() if rows is None else rows
        self.to, self.stock_as_of, self.prev, self.uploaded = to, stock_as_of, prev, uploaded
        self.fail, self.lose_line, self.model = set(fail), lose_line, model
        self.calls: list[str] = []

    def _maybe(self, name: str):
        self.calls.append(name)
        if name in self.fail:
            raise TimeoutError(f"{name} did not answer")

    def anchors(self) -> dict:
        self._maybe("anchors")
        return {"focus_to": self.to.isoformat(), "stock_as_of": self.stock_as_of.isoformat() if self.stock_as_of
                else None, "uploaded_at": self.uploaded}

    def prev_day(self, d):
        self._maybe("prev_day")
        if self.prev is not None:
            return self.prev
        before = [r["sale_date"] for r in self.rows if r["sale_date"] < d.isoformat()]
        return date.fromisoformat(max(before)) if before else None

    def lines(self, first, last):
        self._maybe("lines")
        out = [r for r in self.rows if in_window(r, first, last)]
        return [r for r in out if r["sale_date"] != self.lose_line]          # a line lost on the way (the controls)

    def window_totals(self, period):
        self._maybe("window_totals")
        out: dict = {}
        for w in sw.WINDOWS:
            a, b = window_days(period, w)
            rs = [r for r in self.rows if in_window(r, a, b)]
            if rs:
                out[w] = {"lines": len(rs), "qty": sum(r["quantity"] for r in rs),
                          "gross": round(sum(r["gross_bhd"] or 0 for r in rs), 3),
                          "net": round(sum(r["net_bhd"] or 0 for r in rs), 3)}
        return out

    def upcoming(self) -> dict:
        self._maybe("upcoming")
        return {}

    def stock_model(self, sat):
        self._maybe("stock_model")
        return self.model if self.model is not None else fake_stock_model(self.to, sat)

    def transfers(self, period, d):
        self._maybe("transfers")
        return [{"kind": "xfer_open"}, {"kind": "qty_diff"}, {"kind": "xfer_open"}]

    def papers(self, first, last):
        self._maybe("papers")
        return [{"status": "PROVEN"}, {"status": "PAPERS MISSING"}, {"status": "papers missing"}]

    def receipt_checks(self):
        self._maybe("receipt_checks")
        return [{"kind": "receipt_check", "ref": "yq-26-10-1"}]


class data_root:
    """A temporary data root (exports/, business_data/) with the config fixture, optionally a last load."""

    def __init__(self, last_load: dict | None = None, cfg: dict | None = FIXTURE):
        self.last_load, self.cfg = last_load, cfg

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name).resolve()
        paths.set_data_root(self.root)
        config.use(self.cfg)
        if self.last_load:
            status.write_state({"last_load": self.last_load})
        return self.root

    def __exit__(self, *exc):
        paths.set_data_root(None)
        config.use(None)
        self._tmp.cleanup()
        return False


class Args:
    def __init__(self, prepared_on="2026-10-18", prepared_by=None, compare=None, spec=("week",)):
        self.prepared_on, self.prepared_by, self.compare, self.spec = prepared_on, prepared_by, compare, list(spec)


@contextmanager
def modules(**names):
    """week.py's optional module names pointed elsewhere for one test (a stand-in, or nowhere)."""
    saved = {k: getattr(week, k) for k in names}
    try:
        for k, v in names.items():
            setattr(week, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(week, k, v)


@contextmanager
def stand_in(name: str, mod: types.ModuleType):
    mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
    saved = sys.modules.get(name)
    sys.modules[name] = mod
    try:
        yield mod
    finally:
        if saved is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved


EXTRA_NAME = "scripts.ops.workbook._week_test_extra"


def extra_module(stock_text: str = "Main store", moves_text: str = "Van A", stock_fails: bool = False,
                 bad_formula: bool = False) -> types.ModuleType:
    """A stand-in for the shared sheet module: totals only, a formula check column on Movements that reads 0."""
    mod = types.ModuleType(EXTRA_NAME)

    def stock_totals(as_of):
        if stock_fails:
            raise RuntimeError("no stock")
        return {"as_of": as_of, "rows": [("Main store", 120, 345.5), ("Vans", 30, 80.25)]}

    def stock_sheet(ws, totals, heading):
        ws.text(0, 1, heading, "sheet_title")
        ws.text(3, 1, "Store", "th")
        ws.text(3, 2, "Units", "th_r")
        ws.text(3, 3, "BHD at cost", "th_r")
        for k, (store, units, bhd) in enumerate(totals["rows"]):
            ws.text(4 + k, 1, stock_text if k == 0 else store, "td")
            ws.put(4 + k, 2, units, "int")
            ws.put(4 + k, 3, bhd, "g_money3")
        ws.formula(6, 2, "SUM(C5:C6)", "total_int")
        ws.formula(6, 3, "SUM(D5:D6)" if not bad_formula else "1/0", "g_total_money3")

    def moves_totals(first, last):
        return {"first": first, "last": last, "rows": [(moves_text, 10, 7, 3)]}

    def movements_sheet(ws, totals, heading):
        ws.text(0, 1, heading, "sheet_title")
        for k, (store, opening, out, closing) in enumerate(totals["rows"]):
            ws.text(4 + k, 1, store, "td")
            ws.put(4 + k, 2, opening, "int")
            ws.put(4 + k, 3, out, "int")
            ws.put(4 + k, 4, closing, "int")
            ws.formula(4 + k, 5, f"C{5 + k}-D{5 + k}-E{5 + k}", "int")      # the check column: 0

    mod.stock_totals, mod.stock_sheet = stock_totals, stock_sheet
    mod.moves_totals, mod.movements_sheet = moves_totals, movements_sheet
    return mod


def built(source=None, sat: date = SAT, kpi=100, extra: str = ABSENT):
    """(book, model, facts) for one week, from the world, the shared sheets pointed at `extra`."""
    with modules(EXTRA_MODULE=extra):
        return week.build_week(mdl.Period.week(sat), source or FakeSource(), d=TO, prepared_on=date(2026, 10, 18),
                               prepared_by="", category_names={}, kpi_step=kpi)


def value(b, sheet: str, ref: str):
    r, c = fx.parse_cell(ref)
    cell = b.get(sheet).cells.get((r, c))
    return None if cell is None else cell.value


def formula_of(b, sheet: str, ref: str):
    r, c = fx.parse_cell(ref)
    cell = b.get(sheet).cells.get((r, c))
    return None if cell is None else cell.formula


def named(b, name: str):
    rng_ = fx.parse_range(b.names[name], "")
    return b.get(rng_.sheet).cells[(rng_.r1, rng_.c1)].value


def find_row(b, sheet: str, col: str, text: str) -> int:
    c = fx.col_index(col)
    for (r, cc), cell in sorted(b.get(sheet).cells.items()):
        if cc == c and cell.value == text:
            return r + 1
    raise AssertionError(f"{text!r} not found in {sheet}!{col}")


def table(b, measure: str, w: str):
    return week.window_value(b, measure, w)


def fixed(x, dp: int = 0) -> str:
    """FIXED as a spreadsheet writes it: half away from zero, thousands grouped."""
    q = Decimal(1).scaleb(-dp)
    return f"{Decimal(repr(float(x))).quantize(q, rounding=ROUND_HALF_UP):,.{dp}f}"


def texts(ws) -> list[str]:
    return [c.value for c in ws.cells.values() if isinstance(c.value, str) and c.value]


LONG_RUN = re.compile(r"(?<!\d)\d{9}(?!\d)|\d{15,}")
VOUCHERS = re.compile(r"\b[A-Za-z]{1,6}(?:-[A-Za-z]{1,6})*-\d{2}-\d{2}-\d+\b")


# ── hand sums ──────────────────────────────────────────────────────────────────────────────────

def hand(period: mdl.Period, w: str, rows=None) -> dict:
    """The windows table's figures for one window, worked out from the rows alone."""
    rows = world_rows() if rows is None else rows
    a, b = window_days(period, w)
    rs = [r for r in rows if in_window(r, a, b)]
    acc = [r for r in rs if r["item_name"] != ITEMS["PHONE"]]
    accounts = lambda group: {r["customer_account"] for r in group if r["customer_account"].lower() != "cash customer"}  # noqa: E731
    reps = lambda group: {r["salesman_raw"] for r in group if (r["net_bhd"] or 0) > 0}  # noqa: E731
    if w == "prev":                    # the four weeks before, as an average week
        weeks = [[r for r in acc if in_window(r, a + timedelta(days=7 * k), a + timedelta(days=7 * k + 6))]
                 for k in range(4)]
        n_accounts = sum(len(accounts(g)) for g in weeks) / 4
        n_reps = sum(len(reps(g)) for g in weeks) / 4
        div = 4
    else:
        n_accounts, n_reps, div = len(accounts(acc)), len(reps(acc)), 1
    net = sum(r["net_bhd"] or 0 for r in rs) / div
    units = sum(r["quantity"] for r in acc) / div
    invoices = len({r["invoice_no"] for r in acc}) / div
    return {"net": net, "units": units, "invoices": invoices, "avg_invoice": net / invoices if invoices else 0,
            "avg_price": net / units if units else 0, "accounts": n_accounts, "active": n_reps}


# ── the windows ────────────────────────────────────────────────────────────────────────────────

def one_line_rows(sat: date) -> list[dict]:
    return [line(sat, 1, 1, "Shop One", ITEMS["X01"], 1, 1.1, VAN_A)]


@test("windows: the Notes formulas over the typed Sunday give the period's days (in a month, across two, the 31st, the year's end)")
def _():
    for sat in (date(2026, 10, 17), date(2026, 10, 3), date(2026, 6, 6), date(2026, 10, 31), date(2027, 1, 2),
                date(2026, 4, 4)):
        p = mdl.Period.week(sat)
        b, _m, _f = built(FakeSource(rows=one_line_rows(sat), to=sat + timedelta(days=3)), sat=sat)
        got = {name: named(b, name) for name in ("WkStart", "WkEnd", "PrevStart", "PrevEnd", "LmStart", "LmEnd",
                                                 "MtdStart", "MonthEnd", "DataFirst")}
        want = {"WkStart": p.start, "WkEnd": p.end, "PrevStart": p.prev_start, "PrevEnd": p.prev_end,
                "LmStart": p.lm_start, "LmEnd": p.lm_end, "MtdStart": p.mtd_start, "MonthEnd": week.month_end(sat),
                "DataFirst": p.data_first}
        assert got == {k: fx.serial(v) for k, v in want.items()}, (sat, got)
        assert named(b, "BizDaysLeft") == metrics.business_days(sat + timedelta(days=1), week.month_end(sat)), sat
        assert named(b, "WkLabel") == p.label and named(b, "WkLong") == p.long, (named(b, "WkLabel"), p.label)
    b, _m, _f = built(FakeSource(rows=one_line_rows(date(2027, 1, 2)), to=date(2027, 1, 5)), sat=date(2027, 1, 2))
    assert named(b, "LmLabel") == "27 Nov to 3 Dec" and named(b, "MtdLabel") == "1 to 2 Jan"
    assert named(b, "WkSpan") == "27 Dec to 2 Jan 2027" and named(b, "MonthLong") == "January 2027"


@test("windows: the business days left after the week, by formula, for every Saturday of a year")
def _():
    sat = date(2026, 1, 3)
    while sat.year == 2026:
        b = bk.Book()
        s = b.sheet("S")
        s.put(0, 0, sat - timedelta(days=6))
        b.define("WkStart", "S!$A$1")
        for k, (name, f) in enumerate((("WkEnd", "WkStart+6"), ("MtdStart", "DATE(YEAR(WkEnd),MONTH(WkEnd),1)"),
                                       ("MonthEnd", "EDATE(MtdStart,1)-1"), ("Left", sw.BIZ_DAYS_LEFT))):
            s.formula(1 + k, 0, f)
            b.define(name, f"S!$A${2 + k}")
        b.evaluate()
        want = metrics.business_days(sat + timedelta(days=1), week.month_end(sat))
        assert s.cells[(4, 0)].value == want, (sat, s.cells[(4, 0)].value, want)
        sat += timedelta(days=7)


@test("week key: 7*ROUNDUP(A/7,0) is the Saturday that ends the week of every day, across month and year ends")
def _():
    for first, last in ((date(2026, 2, 22), date(2026, 3, 14)), (date(2026, 12, 20), date(2027, 1, 16)),
                        (date(2028, 2, 20), date(2028, 3, 11))):
        d = first
        while d <= last:
            b = bk.Book()
            s = b.sheet("S")
            s.put(0, 0, d)
            s.formula(1, 0, "7*ROUNDUP(A1/7,0)")
            b.evaluate()
            saturday = d + timedelta(days=(5 - d.weekday()) % 7)
            assert s.cells[(1, 0)].value == fx.serial(saturday), (d, fx.from_serial(s.cells[(1, 0)].value))
            d += timedelta(days=1)
    b, m, _f = built()
    for i, ln in enumerate(m.lines):
        saturday = ln.sale_date + timedelta(days=(5 - ln.sale_date.weekday()) % 7)
        assert value(b, "Data", f"K{5 + i}") == fx.serial(saturday), (ln.sale_date, value(b, "Data", f"K{5 + i}"))


# ── the sheets ─────────────────────────────────────────────────────────────────────────────────

@test("sheets: the eleven sheets in order, every formula with a value and none an error")
def _():
    b, m, _f = built()
    assert [s.name for s in b.sheets] == ["Dashboard", "Salesmen", "Categories", "Products", "Customers", "Daily",
                                          "Stock", "Movements", "Actions", "Data", "Notes"]
    assert not m.warnings, m.warnings
    n = 0
    for ws in b.sheets:
        for (_r, _c), cell in ws.cells.items():
            if cell.kind == "f":
                n += 1
                assert not isinstance(cell.value, fx.Err), (ws.name, cell.formula, cell.value)
    assert n > 400, n
    assert len(m.lines) == 19 and m.lines[0].sale_date == date(2026, 9, 11) and m.lines[-1].sale_date == SAT
    assert b.commentary == {}                     # the comment lines are formulas: nothing is DRAFT


@test("sheets: the windows table and the tiles are the hand sums in each window, the account counts among them")
def _():
    b, _m, _f = built()
    p = mdl.Period.week(SAT)
    for w in sw.WINDOWS:
        want = hand(p, w)
        for key, _label, *_ in sw.MEASURES:
            got = table(b, key, w)
            assert abs(got - want[key]) < 1e-9, (w, key, got, want[key])
    # by hand, the world's own figures: the week, the four weeks before as a week, last month's days, the month
    assert [table(b, "net", w) for w in sw.WINDOWS] == [48, 12.25, 24, 57]
    assert [table(b, "accounts", w) for w in sw.WINDOWS] == [3, 1.75, 2, 4]      # once per account and window
    assert [table(b, "active", w) for w in sw.WINDOWS] == [3, 2, 3, 3]
    assert [table(b, "invoices", w) for w in sw.WINDOWS] == [5, 2, 4, 8]         # the handset invoice not counted
    assert [table(b, "units", w) for w in sw.WINDOWS] == [25, 7.25, 17, 31]
    n = sw.Dash.WIN + 1                                          # the net sales row, as Excel numbers it
    assert value(b, "Dashboard", "B9") == 48 and formula_of(b, "Dashboard", "B9") == f"D{n}"
    assert value(b, "Dashboard", "B10") == 48 / 12.25 - 1 and value(b, "Dashboard", "C10") == "4-week average 12"
    assert value(b, "Dashboard", "B11") == 1 and value(b, "Dashboard", "C11") == "same days last month 24"
    assert value(b, "Dashboard", "B12") == "month to date 57, 57% of the KPI step"
    assert value(b, "Dashboard", "B13") == "BHD 4 a business day needed (10 left)"      # 43 over 10 business days
    assert value(b, "Dashboard", "O9") == 3 and value(b, "Dashboard", "P10") == "avg 2"     # accounts (1.75 shown 2)
    assert value(b, "Dashboard", "K9") == 9.6 and value(b, "Dashboard", "L10") == "avg 6.1"  # average invoice
    kpi = [value(b, "Dashboard", sw.kpi_cell(i)) for i in range(6)]
    assert kpi[:2] == [100, 57] and abs(kpi[2] - 0.57) < 1e-12 and kpi[3:5] == [43, 10] and abs(kpi[5] - 4.3) < 1e-12
    assert value(b, "Dashboard", "B5") == "Week ending 17 October 2026: Sunday 11 Oct to Saturday 17 Oct"


@test("sheets: Salesmen has five weekly columns on the week key, with a sparkline each; the windows add up")
def _():
    b, m, _f = built()
    S = len(m.reps)
    tot = 6 + S
    assert m.reps == ["Van A", "Van B", "Causeway"], m.reps
    assert [value(b, "Salesmen", f"{c}5") for c in "MNOPQ"] == [fx.serial(SAT - timedelta(days=7 * k))
                                                               for k in (4, 3, 2, 1, 0)]
    assert formula_of(b, "Salesmen", "M6") == "SUMIFS(d_Net,d_Rep,$B6,d_Wk,M$5)"
    weekly = [value(b, "Salesmen", f"{c}{tot}") for c in "MNOPQ"]
    assert weekly == [17, 13, 16, 3, 48], weekly
    assert value(b, "Salesmen", f"C{tot}") == 48 and value(b, "Salesmen", f"D{tot}") == 12.25
    assert value(b, "Salesmen", f"F{tot}") == 24 and value(b, "Salesmen", f"H{tot}") == 57
    assert [value(b, "Salesmen", f"C{6 + i}") for i in range(S)] == [25, 19, 4]
    assert value(b, "Salesmen", "D8") == 0.5 and value(b, "Salesmen", "E8") == 7      # Causeway: 2 BHD before, / 4
    sparks = b.get("Salesmen").sparklines
    assert len(sparks) == S and sparks[0]["range"] == "Salesmen!M6:Q6" and sparks[0]["location"] == "R6"


@test("sheets: Daily sets each weekday beside the same weekday's four-week average; Customers' statuses")
def _():
    b, m, _f = built()
    rows = world_rows()
    for k in range(7):
        day = SAT - timedelta(days=6 - k)
        assert value(b, "Daily", f"C{6 + k}") == fx.serial(day) and value(b, "Daily", f"B{6 + k}") == sw.DAY_NAMES[k]
        same = [day - timedelta(days=7 * j) for j in range(1, 5)]
        avg = sum(r["net_bhd"] or 0 for r in rows if date.fromisoformat(r["sale_date"]) in same) / 4
        assert abs(value(b, "Daily", f"E{6 + k}") - avg) < 1e-9, (day, value(b, "Daily", f"E{6 + k}"), avg)
    assert value(b, "Daily", "D13") == 48 and value(b, "Daily", "E13") == 12.25
    status_of = {value(b, "Customers", f"B{6 + i}"): value(b, "Customers", f"L{6 + i}") for i in range(len(m.customers))}
    assert status_of == {mdl.CASH_ACCOUNT: "Cash", "Shop One": "Both", "Shop Two": "Both",
                         "Shop Five": "This week only", "Shop Three": "Not this week", "Shop Four": "Not this week"}, \
        status_of
    assert value(b, "Customers", "M7") == 1 and value(b, "Customers", "B7") == "Shop One"   # ranked by the week


@test("controls: the Data sheet equals v_sales in all four windows and every sheet's total less Data is nil")
def _():
    b, _m, _f = built()
    checks = week.control_values(b)
    assert len(checks["db"]) == 16 and all(diff == 0 for _l, diff in checks["db"]), checks["db"]
    labels = [label for label, _cells in checks["sheets"]]
    assert labels == ["Salesmen", "Salesmen, weekly columns", "Categories", "Products", "Customers", "Daily",
                      "Dashboard"], labels
    assert all(x == 0 for _l, cells in checks["sheets"] for x in cells), checks["sheets"]
    assert sum(len(cells) for _l, cells in checks["sheets"]) == 4 + 2 + 4 + 4 + 4 + 2 + 4
    assert checks["unmapped"] == [0] and checks["narration"] == 0
    assert week.checks_ok(checks) == (True, [])
    row = find_row(b, "Notes", "B", "The week: day book lines")
    assert value(b, "Notes", f"C{row}") == 7 and value(b, "Notes", f"D{row}") == 7 and value(b, "Notes", f"E{row}") == 0
    left = find_row(b, "Notes", "B", "Narrations left out (they hold an ID number)")
    assert value(b, "Notes", f"C{left}") == 2
    # a line lost between the database and the sheet: the windows that hold it say so
    b, _m, _f = built(FakeSource(lose_line="2026-10-01"))
    ok, bad = week.checks_ok(week.control_values(b))
    assert not ok and "The month to date: day book lines" in bad and "The four weeks before: day book lines" in bad
    assert not [x for x in bad if x.startswith("The week:") or x.startswith("The same days")], bad


# ── the comment lines ──────────────────────────────────────────────────────────────────────────

@test("comment lines: formulas over the tiles, the KPI step and the Actions sheet, with the right words")
def _():
    b, _m, _f = built()
    say = [value(b, "Dashboard", f"B{sw.Dash.SAY + 1 + k}") for k in range(3)]
    assert say[0] == (f"Net sales in the week ending 17 October 2026 were BHD 48, {fixed((48 / 12.25 - 1) * 100)}% above "
                      "the four-week average of BHD 12, and 100% above the same days last month (BHD 24)."), say[0]
    assert say[1] == ("Month to date (1 to 17 Oct): BHD 57, 57% of the KPI step of BHD 100; BHD 43 to go, BHD 4 a "
                      "business day over the 10 business days left."), say[1]
    assert say[2] == ("Stock and papers: 1 of the 3 fast movers was out of stock at the end of the Saturday; the order "
                      "list holds 3 items (22 units) from 2 suppliers; 2 receipts miss a paper (last 60 days)."), say[2]
    for k in range(3):
        f = formula_of(b, "Dashboard", f"B{sw.Dash.SAY + 1 + k}")
        assert f and f.startswith(("\"", "IF(")), f                  # a formula, never typed words
    # the step reached, and no step at all
    b, _m, _f = built(kpi=50)
    assert value(b, "Dashboard", f"B{sw.Dash.SAY + 2}") == ("Month to date (1 to 17 Oct): BHD 57, 114% of the KPI step "
                                                           "of BHD 50; the step is reached.")
    assert value(b, "Dashboard", "B13") == "the KPI step is reached"
    b, _m, _f = built(kpi=None)
    assert value(b, "Dashboard", f"B{sw.Dash.SAY + 2}") == ("Month to date (1 to 17 Oct): BHD 57; no KPI step is set "
                                                           "for October 2026.")
    assert value(b, "Dashboard", "B12") == "month to date 57" and value(b, "Dashboard", "B13") == \
        "no KPI step for October 2026"
    # the Saturday on the month's last day: no business day is left
    b, _m, _f = built(FakeSource(rows=world_rows() + [line(date(2026, 10, 31), 40, 1, "Shop One", ITEMS["X01"], 1, 1.1,
                                                          VAN_A)], to=date(2026, 11, 2)), sat=date(2026, 10, 31))
    # October's lines add up to 76 by then (the 18th's 18 and the 31st's 1 among them): 24 short of the step of 100
    assert value(b, "Dashboard", "B13") == "BHD 24 short, no business day left", value(b, "Dashboard", "B13")
    assert value(b, "Dashboard", f"B{sw.Dash.SAY + 2}").endswith("BHD 24 to go, and no business day is left in "
                                                                 "October 2026."), value(b, "Dashboard", "B17")
    # parts not worked out, none out of stock, an empty order list
    model = fake_stock_model()
    for it in model.facts["fast"]["list"]:
        it.series = [Decimal(5)] * len(it.series)
    model.facts["order"] = []
    b, _m, _f = built(FakeSource(model=model, fail={"papers"}))
    assert value(b, "Dashboard", f"B{sw.Dash.SAY + 3}") == (
        "Stock and papers: none of the 3 fast movers was out of stock at the end of the Saturday; the order list is "
        "empty; the receipts' papers were not checked this time."), value(b, "Dashboard", f"B{sw.Dash.SAY + 3}")
    b, _m, _f = built(FakeSource(fail={"stock_model"}))
    assert value(b, "Dashboard", f"B{sw.Dash.SAY + 3}").startswith(
        "Stock and papers: the fast movers were not worked out this time; the order list was not worked out this time")


# ── the parts ──────────────────────────────────────────────────────────────────────────────────

PART_KEYS = ("stock", "movements", "fast", "order", "late", "papers", "transfers", "counts", "checks")


def parts_of(b) -> dict:
    """{part: 1 or 0} as the Notes table writes it."""
    top = find_row(b, "Notes", "B", "Part")
    return {key: value(b, "Notes", f"C{top + 1 + k}") for k, (key, _label) in enumerate(sw.PARTS)}


@test("parts: each part that fails stands aside on its own; the Actions sheet holds the counts of the rest")
def _():
    b, _m, f = built()
    assert {k for k, p in f.parts.items() if p.ok} == set(PART_KEYS) - {"stock", "movements"}
    assert f.parts["stock"].why == f.parts["movements"].why == "the shared stock and movement sheets are not built yet"
    assert parts_of(b) == {k: 0 if k in ("stock", "movements") else 1 for k in PART_KEYS}
    act = texts(b.get("Actions"))
    assert "Supplier One" in act and "Not received after the deadline" in act and "Received in other quantities" in act
    for fail, off in (({"transfers"}, {"transfers"}), ({"papers"}, {"papers"}), ({"receipt_checks"}, {"checks"}),
                      ({"stock_model"}, {"fast", "order", "late", "checks"})):
        _b, _m, f = built(FakeSource(fail=fail))
        down = {k for k, p in f.parts.items() if not p.ok} - {"stock", "movements"}
        assert down == off, (fail, down)
        for k in off:
            assert "could not be worked out: TimeoutError" in f.parts[k].why, (k, f.parts[k].why)
    b, _m, f = built(FakeSource(fail={"stock_model"}))
    line_ = find_row(b, "Actions", "B", "Fast movers out of stock")
    assert value(b, "Actions", f"B{line_ + 1}") == ("Not worked out this time (the stock figures could not be worked "
                                                    "out: TimeoutError).")
    # a stock ledger that stops before the Saturday
    _b, _m, f = built(FakeSource(model=fake_stock_model(d=date(2026, 10, 16))))
    assert f.parts["fast"].why == "the stock ledger runs to 16 October 2026, before the Saturday"
    assert f.parts["checks"].why == f.parts["fast"].why and f.parts["papers"].ok


@test("parts: the Actions figures are the stand-ins' counts: fast movers, suppliers, late lines, papers, checks")
def _():
    _b, _m, f = built()
    assert f.parts["fast"].data == {"n": 3, "out": 1, "as_of": SAT, "rank_first": date(2025, 10, 1),
                                    "rank_last": date(2026, 9, 30)}
    assert f.parts["order"].data["suppliers"] == [("Supplier One", 2, 15), ("Supplier Two", 1, 7)]
    assert f.parts["late"].data["suppliers"] == [("Supplier Two", 1, 4)]            # due 0 is not late goods
    assert (f.parts["papers"].data["receipts"], f.parts["papers"].data["missing"]) == (3, 2)
    assert f.parts["transfers"].data["kinds"] == [("Not received after the deadline", 2),
                                                  ("Received in other quantities", 1)]
    # two receipts in the 60 days to the Saturday (one voucher shared by two items), one with its check logged
    assert (f.parts["checks"].data["receipts"], f.parts["checks"].data["unchecked"]) == (2, 1)


@test("parts: counts due: the third Sunday-to-Thursday day back from the month end, and the quarter-end count")
def _():
    assert week.count_day(date(2026, 10, 1)) == date(2026, 10, 27)          # 31 Oct a Saturday, 30 a Friday
    assert week.count_day(date(2026, 11, 1)) == date(2026, 11, 26)          # 30 Nov a Monday: it counts
    assert week.count_day(date(2026, 12, 1)) == date(2026, 12, 29)
    rows = week.counts_part(mdl.Period.week(date(2026, 10, 3))).data["rows"]
    assert [(r[1], r[2]) for r in rows] == [(date(2026, 10, 27), date(2026, 10, 31)),
                                            (date(2026, 12, 29), date(2026, 12, 31))]
    rows = week.counts_part(mdl.Period.week(date(2026, 10, 31))).data["rows"]    # the next month's, after the 31st
    assert rows[0][1:] == (date(2026, 11, 26), date(2026, 11, 30)) and rows[1][1] == date(2026, 12, 29)
    rows = week.counts_part(mdl.Period.week(date(2026, 12, 12))).data["rows"]    # a quarter's last month: the same
    assert rows[0][1:] == rows[1][1:] == (date(2026, 12, 29), date(2026, 12, 31))


@test("parts: the optional modules not built yet read so; a function is called with the arguments it names")
def _():
    src = week.WeekSource()
    p = mdl.Period.week(SAT)
    with modules(MOVES_MODULE=ABSENT, RECON_MODULE=ABSENT, KPI_LOG_MODULE=ABSENT):
        for call, said in ((lambda: src.transfers(p, TO), "the movement audit (/yq-moves) is not built yet"),
                           (lambda: src.papers(p.start, p.end), "the purchases reconciliation is not built yet"),
                           (lambda: src.receipt_checks(), "the incoming-check log (/yq-kpi log) is not built yet")):
            assert str(raises(week.NotYet, call)) == said
    seen = []

    def wants(as_of, first, *, last, heading="kept"):
        seen.append((as_of, first, last, heading))
        return "ok"

    assert week.call_with(wants, week.offered(p, TO)) == "ok" and seen == [(SAT, p.start, SAT, "kept")]

    def needs(stock_model):
        return stock_model

    said = str(raises(week.NotYet, lambda: week.call_with(needs, week.offered(p, TO))))
    assert said == "needs() asks for 'stock_model', which the weekly report does not hand over", said


@test("parts: the Stock and Movements sheets from the shared module; refused when they name an item or a voucher")
def _():
    with stand_in(EXTRA_NAME, extra_module()):
        b, _m, f = built(extra=EXTRA_NAME)
    assert f.parts["stock"].ok and f.parts["movements"].ok
    assert value(b, "Stock", "B1") == "Stock at the end of Saturday 17 October 2026"
    assert value(b, "Stock", "C7") == 150 and value(b, "Movements", "F5") == 0       # the check column reads 0
    assert value(b, "Movements", "B1") == "Movements in the week ending 17 October 2026"
    assert b.get("Stock").page.get("footer")                                       # the file's footer is added
    for leak_text, part in ((ITEMS["T01"], "stock"), ("Demo Cable One", "stock"), ("GN-01 out", "stock"),
                            ("SI-YQ-26-10-13", "movements"), ("STRV : YQ-26-10-7", "movements")):
        mod = extra_module(stock_text=leak_text if part == "stock" else "Main store",
                           moves_text=leak_text if part == "movements" else "Van A")
        with stand_in(EXTRA_NAME, mod):
            b, _m, f = built(extra=EXTRA_NAME)
        other = "movements" if part == "stock" else "stock"
        assert not f.parts[part].ok and f.parts[other].ok, (leak_text, f.parts[part])
        assert "a forwarded sheet carries totals only" in f.parts[part].why, f.parts[part].why
        sheet = "Stock" if part == "stock" else "Movements"
        assert texts(b.get(sheet)) == [sw.NOT_WORKED.format(why=f.parts[part].why)], texts(b.get(sheet))
    with stand_in(EXTRA_NAME, extra_module(stock_fails=True)):
        _b, _m, f = built(extra=EXTRA_NAME)
    assert f.parts["stock"].why == "the stock totals could not be worked out: RuntimeError" and f.parts["movements"].ok
    with stand_in(EXTRA_NAME, extra_module(bad_formula=True)):
        _b, m, f = built(extra=EXTRA_NAME)
    assert f.parts["stock"].why == "the stock totals: 1 formula(s) came out as an error value" and not m.warnings


@test("parts: no item name, item code or voucher number on the Stock, Movements or Actions sheets")
def _():
    with stand_in(EXTRA_NAME, extra_module()):
        b, m, _f = built(extra=EXTRA_NAME)
    words = week.forbidden_words(m)
    for sheet in ("Stock", "Movements", "Actions"):
        for text in texts(b.get(sheet)):
            assert not VOUCHERS.search(text), (sheet, text)
            assert not any(n in text.casefold() for n in words["names"]), (sheet, text)
            assert not words["codes"].search(text), (sheet, text)
    assert words["codes"].search("ZTE-X1") and not words["codes"].search("ZZ9X")
    # a product named like a category row is no item to a reader; its full Focus name still is
    rows = world_rows() + [line(date(2026, 10, 13), 30, 1, "Shop One", "GN-09 Power Bank", 1, 5.5, VAN_A)]
    m = mdl.build(mdl.Period.week(SAT), rows)
    words = week.forbidden_words(m)
    assert "gn-09 power bank" in words["names"] and "power bank" not in words["names"]
    ws = bk.Sheet("Stock")
    ws.text(0, 1, "Power Banks", "td")
    assert week.leak(ws, words) is None
    ws.text(1, 1, "GN-09 Power Bank", "td")
    assert week.leak(ws, words) == "an item"
    ws = bk.Sheet("Movements")
    ws.text(0, 1, "Van A: 12 units", "td")
    ws.text(1, 1, "SIO:X-26-01-1", "td")
    assert week.leak(ws, words) == "a voucher number"


# ── the command ────────────────────────────────────────────────────────────────────────────────

def freeze(when: datetime):
    saved = week._now
    week._now = lambda: when
    return saved


@test("command: the default week is the brief's last full week; the file, the record and the text")
def _():
    saved = freeze(datetime(2026, 10, 18, 9, 30, tzinfo=BAHRAIN))
    try:
        with data_root() as root, modules(EXTRA_MODULE=ABSENT):
            src = FakeSource()
            out = week.run(Args(), None, src)
            assert out["ok"] and out["exit"] == 0, out["text"]
            path = Path(out["files"][0])
            assert path.name == "YQ Bahrain - Mobile Accessories Weekly Report - Week ending 17 Oct 2026.xlsx"
            assert path.parent == root / "exports" / "reports" / "weekly" and path.is_file()
            rec_file = Path(out["files"][1])
            assert rec_file == root / "exports" / "ops" / "2026-10-20" / "report-week-2026-10-17.json"
            record = json.loads(rec_file.read_text(encoding="utf-8"))
            assert list(record) == ["kind", "week_ending", "data_date", "file", "built_at", "built_on", "due_date",
                                    "on_time", "checks_ok", "lines", "parts"], list(record)
            assert record["kind"] == "weekly" and record["week_ending"] == "2026-10-17"
            assert record["data_date"] == "2026-10-20" and record["file"] == str(path)
            assert record["built_at"] == "2026-10-18T09:30:00+03:00" and record["built_on"] == "2026-10-18"
            assert record["due_date"] == "2026-10-18" and record["on_time"] is True
            assert record["checks_ok"] is True and record["lines"] == 19
            assert set(record["parts"]) == set(PART_KEYS) and record["parts"]["stock"]["ok"] is False
            assert record["parts"]["order"] == {"ok": True, "why": ""}
            text = out["text"]
            assert text.startswith("Weekly sales report, the week ending 17 October 2026 (11 to 17 October 2026)")
            assert "Checks: the Data sheet equals v_sales in all four windows" in text
            assert "Not worked out this time: Stock sheet (the shared stock and movement sheets are not built yet)" in text
            assert "Due by Sunday 18 October 2026; built 18 October 2026: on time." in text
            assert not LONG_RUN.search(text)
            assert {"anchors", "lines", "window_totals", "upcoming", "stock_model"} <= set(src.calls)
            again = week.run(Args(), None, FakeSource())
            assert Path(again["files"][0]).name.endswith("17 Oct 2026 (2).xlsx")      # the first file is kept
            assert path.is_file()
    finally:
        week._now = saved


@test("command: a part-day Saturday, a day that is not a Saturday, an incomplete week, a week with no sales")
def _():
    with data_root(), modules(EXTRA_MODULE=ABSENT):
        # the reports exported on Saturday the 17th: that day is a part day, its week is not over
        src = FakeSource(to=SAT, stock_as_of=SAT)
        out = week.run(Args(), SAT, src)
        assert out["exit"] == 2 and not out["ok"] and "\n" not in out["text"], out
        assert out["text"] == ("The week ending 17 October 2026 is not complete yet: the last complete day of the data "
                               "is 16 October 2026 (17 October 2026 is a part day: the reports were exported on it), "
                               "and the last complete week ends on 10 October 2026."), out["text"]
        assert "lines" not in src.calls                                   # nothing read beyond the dates
        out = week.run(Args(), None, FakeSource(to=SAT, stock_as_of=SAT))
        assert out["exit"] == 0 and out["flags"]["week_ending"] == "2026-10-10", out["text"]
        # a day that is not a Saturday
        out = week.run(Args(), date(2026, 10, 16), FakeSource())
        assert out["exit"] == 1 and out["text"].endswith("2026-10-16 is a Friday, and the week that holds it ends "
                                                         "on 2026-10-17"), out["text"]
        # the data stops on the Thursday
        out = week.run(Args(), SAT, FakeSource(to=date(2026, 10, 15)))
        assert out["exit"] == 2 and "is not complete yet: the last complete day of the data is 15 October 2026" in \
            out["text"] and "ends on 10 October 2026" in out["text"]
        # a week long before the data
        out = week.run(Args(), date(2026, 3, 7), FakeSource())
        assert out["exit"] == 2 and "nothing to report" in out["text"], out["text"]
        out = week.run(Args(), None, FakeSource(rows=[]))
        assert out["exit"] == 2 and "nothing to report" in out["text"]
        out = week.run(Args(prepared_on="18-10-2026"), None, FakeSource())
        assert out["exit"] == 1


@test("command: `report week` is handed to this module; the due day from config; built late; a check that fails")
def _():
    saved_source, saved_now = week.WeekSource, freeze(datetime(2026, 10, 20, 8, 0, tzinfo=BAHRAIN))
    try:
        week.WeekSource = lambda: FakeSource()
        cfg = dict(FIXTURE, **{"kpi.weekly_due_weekday": "Monday"})
        with data_root(cfg=cfg), modules(EXTRA_MODULE=ABSENT):
            out = build.run(Args(spec=("week", "2026-10-17")))
            assert out["exit"] == 0 and out["flags"]["on_time"] is False, out["text"]
            record = json.loads(Path(out["files"][1]).read_text(encoding="utf-8"))
            assert record["due_date"] == "2026-10-19" and record["on_time"] is False     # Monday, built Tuesday
            assert "Due by Monday 19 October 2026; built 20 October 2026: after the due day." in out["text"]
        with data_root(cfg=dict(FIXTURE, **{"kpi.weekly_due_weekday": "someday"})) as root, \
                modules(EXTRA_MODULE=ABSENT):
            said = str(raises(config.ConfigMissing, lambda: build.run(Args(spec=("week",)))))
            assert "kpi.weekly_due_weekday" in said and not (root / "exports").exists()     # stopped before a write
        with data_root(), modules(EXTRA_MODULE=ABSENT):
            week.WeekSource = lambda: FakeSource(lose_line="2026-10-12")
            out = build.run(Args(spec=("week",)))
            assert out["exit"] == 2 and not out["ok"] and "CHECKS TO LOOK AT: The week: day book lines" in out["text"]
            record = json.loads(Path(out["files"][1]).read_text(encoding="utf-8"))
            assert record["checks_ok"] is False
    finally:
        week.WeekSource, week._now = saved_source, saved_now
    with data_root():                                   # no key in config: Sunday, the first one after the week
        assert week.due_date(SAT) == (date(2026, 10, 18), "Sunday")
    with data_root(cfg=dict(FIXTURE, **{"kpi.weekly_due_weekday": " saturday "})):
        assert week.due_date(SAT) == (date(2026, 10, 24), "Saturday")       # the first one AFTER the week


@test("file: a valid workbook: eleven sheets, two charts, a sparkline per salesman, cached values; no long digit runs")
def _():
    import warnings

    import openpyxl
    b, m, _f = built()
    with data_root(), warnings.catch_warnings():
        warnings.simplefilter("ignore")                 # openpyxl does not read sparklines and says so
        p = week.target_path(m.period)
        b.save(p)
        raises(FileExistsError, lambda: b.save(p))                         # never over an existing file
        assert week.target_path(m.period).name.endswith("(2).xlsx")
        part = golden.read_book(p)
        with zipfile.ZipFile(p) as z:
            strings = z.read("xl/sharedStrings.xml").decode("utf-8")
            assert len([n for n in z.namelist() if n.startswith("xl/charts/")]) == 2
        shown = openpyxl.load_workbook(p, data_only=True)
    assert [s.name for s in part.sheets] == list(sw.SHEETS)
    assert sum(s.charts for s in part.sheets) == 2 and sum(s.sparklines for s in part.sheets) == len(m.reps)
    assert shown["Dashboard"][f"D{sw.Dash.WIN + 1}"].value == 48 and shown["Salesmen"]["Q9"].value == 48
    assert part.sheet("Data").autofilter.startswith("A4:U") and part.print_areas["Dashboard"].endswith("$S$74")
    assert not LONG_RUN.search(strings) and "******" not in strings


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
