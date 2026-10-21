"""Ops engine: the month-end pack of /yq-month (python -m scripts.ops report month): five more sheets, the ten comment
lines with their approval step, and the eight sheets of the September golden kept as they were.

    python -m tests.test_ops_month_pack

Same lightweight runner as the other suites (no pytest). No database, no network, no .env: tests.test_ops_report is
imported first (it pins SUPABASE_URL, SUPABASE_KEY and DATABASE_URL to ci.invalid, imports scripts.ops.db first and
sets stdout to UTF-8), and its invented month is the one used here (two vans, an outlet, a roadshow handset, a few
shops, round amounts). The two packages still being built (scripts.ops.workbook.sheets_extra; scripts.ops.recon_blocks
with scripts.ops.workbook.sheets_recon) are stand-in modules put in sys.modules for each test, and the KPI scorecard
is a card of its contract's shape (the real one reads the database); the KPI sheets are drawn by the real
scripts.ops.workbook.sheets_kpi, the KPI log's lines read by the real scripts.ops.kpi_log. Every folder is a temporary
one; every name and figure is invented.

Covered:
  * the pack: thirteen sheets in order, the five between Daily and Data, each drawn from its part (the stock at the
    month's last day, the movements over its first to last day, the reconciliations and the KPI of its key); the
    eight sheets cell for cell as the eight-sheet build has them, each with its own rules, data bars, colour scale,
    sparklines and charts; the Notes sheet's About lines name the pack's sheets and the bulk threshold (the plan's
    default or config's); conditional styles above the three of every file only through the KPI status rules (none
    when the KPI part fails); the record's parts: the ageing part KPI 1c looks for, each sheet's state;
  * every pack sheet's "not worked out this time" path: its module not there yet, its part raising, its drawer
    raising half-way (what it drew goes), a formula the evaluator refuses, totals that carry their own error; the
    workbook stands each time and the screen names the sheet;
  * the golden compare: the reference's sheets as an ordered subsequence (the added ones named), a sheet out of
    order or missing, a count that differs on one sheet even when the totals agree, Notes left out unless named;
  * the comment lines end to end: the DRAFT writes the facts and the template once (a later DRAFT keeps his draft),
    check passes and prints FINAL, the FINAL line builds the final workbook (the approved lines, no DRAFT in its name,
    the rest as the DRAFT has it) and keeps the DRAFT; its record (kind, built_at, final, approved_at, facts_digest,
    commentary_sha, due_date, parts) as report-month-M.final.json, never written over by a later DRAFT build at the
    same data date; a second final takes the next free names; KPI's record reader sees all of them;
  * the final step refused in one sentence with nothing written: the month's figures changed by a load, a token
    that is not the file's (or a file changed after its check), lines that no longer pass, a commentary of another
    month, a file that cannot be read, the options without their month or with the week, a token of the wrong shape;
  * the due day: working day five of the next month, Sunday to Thursday, as KPI 7 counts it, and the keys moving it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import traceback
import types
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from tests.test_ops_report import (  # noqa: E402  (first: it pins the environment, imports scripts.ops.db first)
    ITEMS, LAST_LOAD, MONTH, VAN_A, FakeSource, data_root, line, raises, world_rows)
from scripts.ops import config, kpi, kpi_log, textcheck  # noqa: E402
from scripts.ops.workbook import book as bk  # noqa: E402
from scripts.ops.workbook import build, golden, sheets_core as sc, sheets_kpi  # noqa: E402
from scripts.ops.workbook import formula as fx  # noqa: E402

TESTS: list[tuple[str, object]] = []
PACK_NAMES = ["Stock", "Movements", "Reconciliation", "KPI", "KPI log"]
ALL_NAMES = ["Dashboard", "Salesmen", "Categories", "Products", "Customers", "Daily", *PACK_NAMES, "Data", "Notes"]
CORE_NAMES = ["Dashboard", "Salesmen", "Categories", "Products", "Customers", "Daily", "Data", "Notes"]
MISSING = object()


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── the KPI card and the stand-ins ─────────────────────────────────────────────────────────────

def _row(key, name, status_, value, unit):
    return {"key": key, "area": int(key[0]), "kpi": name, "target": "as the KPI sheet has it", "status": status_,
            "actual": "invented figures", "evidence": [f"evidence line {key}"], "source": "a test", "value": value,
            "unit": unit}


CARD = {"command": "kpi", "month": MONTH, "label": "March 2026", "first": "2026-03-01", "last": "2026-03-31",
        "data_date": "2026-04-07", "today": "2026-04-08", "whole": True, "partial": False,
        "log": {"started": None, "lines": 0, "marker": None},
        "counts": {"MET": 1, "NOT MET": 1, "NOT EVIDENCED": 1, "NOTHING DUE": 1},
        "rows": [_row("1a", "Stock cover", "MET", 33, "days"), _row("1b", "Fast-mover availability", "NOT MET", 0.9,
                                                                     "share"),
                 _row("3a", "Quotations", "NOT EVIDENCED", None, "count"), _row("5b", "SKU productivity", "NOTHING DUE",
                                                                                  0.5, "share")],
        "plan": {"month": MONTH, "set": True, "counts": {"MET": 1, "NOT MET": 0, "NOT EVIDENCED": 0, "NOTHING DUE": 0},
                 "note": None, "rows": [{"name": "Sales", "target": "100", "actual": "120", "status": "MET",
                                         "value": 120, "unit": "bhd", "source": "KPI 5a above"}]},
        "unread": [], "defaults_used": [], "notes": []}


def stock_totals_of(as_of, error=None) -> dict:
    if error:
        return {"error": error, "as_of": as_of, "store": "Main store"}
    return {"as_of": as_of, "ledger_to": as_of, "cut": True, "store": "Main store", "cost_label": "at today's cost",
            "main": {"items": 3, "units": 40, "value": 12.5, "uncosted_items": 0},
            "ageing": {"bands": [{"band": "0-90", "items": 2, "units": 30, "value": 10}], "units": 30, "value": 10},
            "review": {"counts": {"Not moving": 1, "Slow": 2, "On track": 3, "Cleared": 4}, "items": 10}}


def extra_module(seen: dict, fail: dict) -> types.ModuleType:
    """scripts.ops.workbook.sheets_extra as its contract has it: the totals, then the sheet drawn from them."""
    m = types.ModuleType(build.EXTRA_MODULE)

    def stock_totals(model=None, *, as_of=None, window=None):
        seen["stock"] = (model, as_of, window)
        if "stock_totals" in fail:
            raise fail["stock_totals"]
        return stock_totals_of(as_of, seen.get("stock_error"))

    def moves_totals(table=None, *, first=None, last=None):
        seen["moves"] = (table, first, last)
        if "moves_totals" in fail:
            raise fail["moves_totals"]
        return {"first": first, "last": last, "basis": "at cost", "stores": [{"store": "Van A", "kind": "van"}],
                "total": {}, "exceptions": None, "unbalanced": 0, "note": None, "error": None}

    def stock_sheet(ws, totals, heading):
        seen["stock_heading"] = heading
        ws.text(0, 0, heading, "sheet_title")
        if totals.get("error"):
            ws.text(3, 0, f"The stock figures: not worked out this time ({totals['error']}).", "note")
            return {}
        ws.text(4, 0, "Units", "g_text")
        ws.put(4, 1, totals["main"]["units"], "g_int")
        ws.merge(5, 0, 5, 2, "a merged note", "note")
        ws.formula(6, 1, "B5*2", "g_int")
        if "stock_sheet" in fail:                            # drawn half-way, then a failure
            ws.cond("B5:B5", {"type": "cell", "criteria": "less than", "value": 0, "format": "cf_red"})
            raise fail["stock_sheet"]
        return {"units": "Stock!$B$5"}

    def movements_sheet(ws, totals, heading):
        seen["moves_heading"] = heading
        ws.text(0, 0, heading, "sheet_title")
        ws.text(4, 0, "Van A", "g_text")
        for c, v in ((1, 10), (2, 4), (3, 6)):
            ws.put(4, c, v, "g_int")
        ws.formula(4, 4, "B5-C5-D5", "g_int")                 # the check: nought on a store that balances
        ws.cond("E5:E5", {"type": "cell", "criteria": "not equal to", "value": 0, "format": "down"})
        return {"checks": "Movements!$E$5"}

    m.stock_totals, m.moves_totals, m.stock_sheet, m.movements_sheet = stock_totals, moves_totals, stock_sheet, \
        movements_sheet
    return m


def recon_modules(seen: dict, fail: dict) -> tuple[types.ModuleType, types.ModuleType]:
    blocks = types.ModuleType(build.RECON_MODULE)
    sheet = types.ModuleType(build.RECON_SHEET_MODULE)

    def month_blocks(month):
        seen["recon"] = month
        if "month_blocks" in fail:
            raise fail["month_blocks"]
        return {"month": month, "blocks": [{"name": "Sales and receivables", "status": "PROVEN"}]}

    def recon_sheet(ws, got):
        ws.text(0, 0, "Reconciliations", "sheet_title")
        for i, b in enumerate(got["blocks"]):
            ws.text(3 + i, 0, b["name"], "g_text")
            ws.text(3 + i, 1, b["status"], "g_text")
        if "recon_formula" in fail:
            ws.formula(9, 0, "VLOOKUP(1,A4:A5,1,0)", "g_int")        # a function the evaluator does not know
        return {"first": "Reconciliation!$A$4"}

    blocks.month_blocks, sheet.recon_sheet = month_blocks, recon_sheet
    return blocks, sheet


class stand_ins:
    """The two packages still being built, as stand-in modules in sys.modules for the time of a test (what was there
    is put back after). `absent`: modules that are not there yet (None in sys.modules: importing one raises
    ModuleNotFoundError); `fail`: a stand-in function's name -> the exception it raises."""

    def __init__(self, absent=(), fail=None, stock_error=None):
        self.absent, self.fail = tuple(absent), dict(fail or {})
        self.seen: dict = {"stock_error": stock_error} if stock_error else {}

    def __enter__(self) -> dict:
        names = (build.EXTRA_MODULE, build.RECON_MODULE, build.RECON_SHEET_MODULE) + self.absent
        self.saved = {name: sys.modules.get(name, MISSING) for name in names}
        sys.modules[build.EXTRA_MODULE] = extra_module(self.seen, self.fail)
        sys.modules[build.RECON_MODULE], sys.modules[build.RECON_SHEET_MODULE] = recon_modules(self.seen, self.fail)
        for name in self.absent:
            sys.modules[name] = None
        return self.seen

    def __exit__(self, *exc):
        for name, mod in self.saved.items():
            if mod is MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        return False


class PackSource(FakeSource):
    """The invented month, the pack's parts read through build.Source's own methods (so through the stand-in modules
    and the real KPI log), and the KPI scorecard a card of the contract's shape (or an exception it raises)."""
    stock_totals = build.Source.stock_totals
    moves_totals = build.Source.moves_totals
    recon_blocks = build.Source.recon_blocks
    kpi_lines = build.Source.kpi_lines

    def __init__(self, rows=None, card=None, **kw):
        super().__init__(rows, **kw)
        self.card = CARD if card is None else card
        self.kpi_months: list[str] = []

    def kpi_card(self, month):
        self.kpi_months.append(month)
        if isinstance(self.card, BaseException):
            raise self.card
        return copy.deepcopy(self.card)


class Args:
    """The report command's options, as argparse hands them over."""

    def __init__(self, spec=("month", MONTH), prepared_on="2026-04-08", prepared_by=None, compare=None,
                 commentary=None, token=None):
        self.spec, self.prepared_on, self.prepared_by, self.compare = list(spec), prepared_on, prepared_by, compare
        self.commentary, self.token = commentary, token


def pack(source=None, **kw):
    """(book, model, facts) of the pack on the invented month."""
    return build.build_month_pack(MONTH, source or PackSource(), prepared_on=date(2026, 4, 8), **kw)


def texts(ws) -> list[str]:
    return [str(c.value) for c in ws.cells.values() if c.kind == "s"]


def one_line(ws) -> str:
    """The text of a sheet that holds one line and nothing else (no rule, merge, chart, sparkline or validation)."""
    assert len(ws.cells) == 1 and not (ws.conds or ws.merges or ws.charts or ws.sparklines or ws.validations), \
        (ws.name, ws.cells, ws.conds, ws.merges)
    (cell,) = ws.cells.values()
    assert cell.kind == "s", cell
    return cell.value


def sheet_parts(path) -> tuple[dict, str]:
    """({sheet name: its XML}, the styles XML), read through the workbook's relationships as golden.read_book reads."""
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        out = {}
        for s in wb.find(golden.M + "sheets"):
            target = rels[s.get(golden.R_ID)].lstrip("/").removeprefix("xl/")
            out[s.get("name")] = z.read("xl/" + target).decode("utf-8")
        return out, z.read("xl/styles.xml").decode("utf-8")


def dxf_ids(xml: str) -> list[int]:
    return [int(x) for x in re.findall(r'<cfRule [^>]*dxfId="(\d+)"', xml)]


def monthly(root: Path) -> list[str]:
    folder = root / "exports" / "reports" / "monthly"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def ops_files(root: Path) -> list[str]:
    folder = root / "exports" / "ops"
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []


# ── the pack ───────────────────────────────────────────────────────────────────────────────────

@test("the pack: thirteen sheets in order, the five between Daily and Data, each drawn from its own part")
def _():
    with data_root(last_load=LAST_LOAD), stand_ins() as seen:
        src = PackSource()
        b, m, _f = pack(src)
    assert [s.name for s in b.sheets] == ALL_NAMES, [s.name for s in b.sheets]
    assert seen["stock"] == (None, date(2026, 3, 31), None)                 # as of the month's last day
    assert seen["moves"] == (None, date(2026, 3, 1), date(2026, 3, 31))     # the month's first to last day
    assert seen["recon"] == MONTH and src.kpi_months == [MONTH]
    assert seen["stock_heading"] == "Stock at the end of March 2026" and seen["moves_heading"] == "Stock movements, March 2026"
    extra = b.controls["extra"]
    assert list(extra) == PACK_NAMES and all(r["state"] == "ok" for r in extra.values()), extra
    assert extra["Stock"]["ageing"] == {"sheet": "Stock", "as_of": "2026-03-31", "items_with_action": 3}
    assert extra["KPI"]["refs"]["status_col"] == 5 and extra["KPI log"]["refs"]["total_row"] > 0
    assert not m.warnings, m.warnings
    for ws in b.sheets:                                            # every formula worked out, none an error value
        for cell in ws.cells.values():
            if cell.kind == "f":
                assert not isinstance(cell.value, fx.Err), (ws.name, cell.formula, cell.value)
    assert b.get("Stock").cells[(6, 1)].value == 80                 # the pack sheets' formulas carry their values
    kpi_sheet = b.get("KPI")
    assert kpi_sheet.cells[(4, 2)].value == 1 and kpi_sheet.cells[(4, 3)].value == 1      # the header's COUNTIFs
    assert texts(b.get("KPI log"))[0].startswith("KPI log - March 2026")
    assert build.pack_parts(b) == {"ageing": {"sheet": "Stock", "as_of": "2026-03-31", "items_with_action": 3},
                                   "sheets": dict.fromkeys(PACK_NAMES, "ok")}
    assert build.pack_line(build.pack_parts(b)) == ("Month-end sheets: Stock, Movements, Reconciliation, KPI and KPI log "
                                                    "worked out.")


@test("the pack: the eight sheets cell for cell and rule for rule as the eight-sheet build has them (golden compare)")
def _():
    with data_root(last_load=LAST_LOAD) as root, stand_ins():
        core, m, _f = build.build_month(MONTH, PackSource(), prepared_on=date(2026, 4, 8))
        b, _m, _f2 = pack()
        core_path = core.save(root / "exports" / "core.xlsx")
        pack_path = b.save(root / "exports" / "pack.xlsx")
        res = golden.compare(pack_path, core_path)
        assert res.ok and res.names_ok and res.added == PACK_NAMES, golden.summary(res)
        assert [s.name for s in res.sheets] == CORE_NAMES[:-1]               # Notes is left out unless it is named
        assert res.names_note == ("the reference's 8 sheets in the same order, and 5 added: Stock, Movements, "
                                  "Reconciliation, KPI, KPI log"), res.names_note
        lines = golden.summary(res)
        assert all(ln.endswith(": identical") for ln in lines[1:-1]), lines
        assert lines[-1].startswith("Counts, sheet by sheet on the compared sheets: cell rules 20 (reference 20)"), lines
        notes = golden.compare(pack_path, core_path, sheets=["Notes"])     # named, Notes is compared: its About differs
        assert not notes.ok and notes.sheets[0].value, golden.summary(notes)
        a, c = golden.read_book(pack_path), golden.read_book(core_path)
        for name in CORE_NAMES:
            sa, sb = a.sheet(name), c.sheet(name)
            got = [(getattr(sa, w), getattr(sb, w)) for w in golden.COUNTS]
            assert all(x == y for x, y in got), (name, got)
        dash = a.sheet("Dashboard")
        assert (dash.charts, a.sheet("Daily").charts, a.sheet("Categories").color_scales) == (3, 1, 1)
        assert a.sheet("Salesmen").sparklines == len(m.reps) and dash.cell_rules == c.sheet("Dashboard").cell_rules
        assert a.print_areas["Dashboard"] == c.print_areas["Dashboard"] and a.names == c.names
        assert a.sheet("Movements").cell_rules == 1 and a.sheet("KPI").cell_rules == 4      # the pack's own rules


@test("the pack: the Notes sheet's About lines name the pack's sheets and the bulk threshold, the plan's or config's")
def _():
    def about(b) -> list[str]:
        notes = b.get("Notes")
        head = next(r for (r, c), cell in notes.cells.items() if cell.value == "About this workbook")
        out = []
        r = head + 1
        while (r, 1) in notes.cells and str(notes.cells[(r, 1)].value)[:1].isdigit():
            out.append(notes.cells[(r, 1)].value)
            r += 1
        return out

    def control_row(b) -> int:
        return next(r for (r, c), cell in b.get("Notes").cells.items() if cell.value == "Control totals")

    with data_root(last_load=LAST_LOAD), stand_ins():
        pack_book = pack(bulk_invoice_bhd=200.0, bulk_default=True)[0]
        core_book = build.build_month(MONTH, PackSource(), prepared_on=date(2026, 4, 8))[0]
        lines, core = about(pack_book), about(core_book)
        set_ = about(pack(bulk_invoice_bhd=40.0)[0])
    assert control_row(pack_book) == control_row(core_book) and pack_book.controls["db"] == core_book.controls["db"]
    assert len(core) == 4 and core[0] == "1.  Figures on every sheet are worked out from the Data sheet with formulas."
    assert len(lines) == len(set_) == 4 and lines[1] == core[1] and lines[3] == core[3], lines   # the Sep file's rows
    assert lines[0] == ("1.  Figures on the sales sheets (Dashboard to Daily) are worked out from the Data sheet with "
                        "formulas. The Stock, Movements, Reconciliation, KPI and KPI log sheets hold totals worked out "
                        "when the file was built, from the sources each one names; they do not change if the Data sheet "
                        "changes."), lines[0]
    assert lines[2] == core[2] + (" In the facts the comment lines are written from, a bulk order is an invoice of BHD "
                                  "200 or more, excluding VAT: the approved plan's default, as config "
                                  "report.bulk_invoice_bhd is not set."), lines[2]
    assert set_[2].endswith("an invoice of BHD 40 or more, excluding VAT (config report.bulk_invoice_bhd)."), set_[2]


@test("the pack: conditional styles above the three of every file only through the KPI status rules")
def _():
    with data_root(last_load=LAST_LOAD) as root, stand_ins():
        b, _m, _f = pack()
        parts, styles = sheet_parts(b.save(root / "exports" / "pack.xlsx"))
        failed, _m2, _f2 = pack(PackSource(card=RuntimeError("no card")))
        plain_styles = sheet_parts(failed.save(root / "exports" / "no-kpi.xlsx"))[1]
    assert list(b.dxf_styles) == ["cf_green", "cf_red", "cf_amber", "cf_grey"], list(b.dxf_styles)
    assert '<dxfs count="7">' in styles and list(parts) == ALL_NAMES
    above = {name for name, xml in parts.items() if any(i >= 3 for i in dxf_ids(xml))}
    assert above == {"KPI"}, above
    kpi_xml = parts["KPI"]
    assert sorted(dxf_ids(kpi_xml)) == [3, 4, 5, 6]
    for status_ in ("MET", "NOT MET", "NOT EVIDENCED", "NOTHING DUE"):
        assert f"<formula>\"{status_}\"</formula>" in kpi_xml, status_
    where = b.controls["extra"]["KPI"]["refs"]                  # the scorecard's rows, then the 90-day plan's
    assert (where["first_row"], where["last_row"], tuple(where["plan"])) == (7, 10, (16, 16)), where
    assert 'sqref="F8:F11 F17:F17"' in kpi_xml, re.findall(r'sqref="[^"]+"', kpi_xml)
    assert dxf_ids(parts["Movements"]) == [2]                                             # the fall style every file has
    assert one_line(failed.get("KPI")) == "The KPI scorecard: not worked out this time (RuntimeError)."
    assert failed.dxf_styles == {} and '<dxfs count="3">' in plain_styles
    assert sheets_kpi.STATUSES == tuple(s for s, _style in build.STATUS_STYLES) == kpi.STATUSES


# ── a part that cannot be worked out ───────────────────────────────────────────────────────────

@test("not worked out: a module not there yet, a part raising, a drawer failing half-way; the workbook stands")
def _():
    with data_root(last_load=LAST_LOAD) as root:
        cases = (
            (dict(absent=[build.EXTRA_MODULE]), {"Stock": "The stock figures: not worked out this time (not built yet).",
                                                 "Movements": "The movements of the vans and outlets: not worked out "
                                                              "this time (not built yet)."}),
            (dict(absent=[build.RECON_MODULE]), {"Reconciliation": "The reconciliations: not worked out this time (not "
                                                                   "built yet)."}),
            (dict(absent=[build.RECON_SHEET_MODULE]), {"Reconciliation": "The reconciliations: not worked out this time "
                                                                         "(not built yet)."}),
            (dict(absent=[build.KPI_SHEET_MODULE]), {"KPI": "The KPI scorecard: not worked out this time (not built "
                                                            "yet).",
                                                     "KPI log": "The KPI log: not worked out this time (not built yet)."}),
            (dict(fail={"stock_totals": RuntimeError("down")}), {"Stock": "The stock figures: not worked out this time "
                                                                          "(RuntimeError)."}),
            (dict(fail={"moves_totals": KeyError("x")}), {"Movements": "The movements of the vans and outlets: not "
                                                                       "worked out this time (KeyError)."}),
            (dict(fail={"month_blocks": OSError("x")}), {"Reconciliation": "The reconciliations: not worked out this "
                                                                           "time (OSError)."}),
            (dict(fail={"stock_sheet": ValueError("half-way")}), {"Stock": "The stock figures: not worked out this time "
                                                                           "(ValueError)."}),
            (dict(fail={"recon_formula": True}), {"Reconciliation": "The Reconciliation sheet: not worked out this time "
                                                                    "(FormulaError)."}),
        )
        for k, (given, said) in enumerate(cases):
            with stand_ins(**given):
                b, m, _f = pack()
                for name, line_ in said.items():
                    assert one_line(b.get(name)) == line_, (given, name, b.get(name).cells)
                    assert b.controls["extra"][name]["state"] == line_.split(": ", 1)[1].rstrip("."), b.controls
                for name in set(PACK_NAMES) - set(said):
                    assert b.controls["extra"][name]["state"] == "ok", (given, name, b.controls["extra"][name])
                assert [s.name for s in b.sheets] == ALL_NAMES and not m.warnings, m.warnings
                assert b.get("Dashboard").cells[(8, 1)].value == 170                 # the eight sheets as they were
                b.save(root / "exports" / f"case-{k}.xlsx")                          # and the file is written
        with stand_ins(fail={"stock_sheet": ValueError("half-way")}):          # its rule named cf_red: gone with it
            b, _m, _f = pack(PackSource(card=RuntimeError("no card")))
        assert b.dxf_styles == {} and build.pack_parts(b)["ageing"] is None
        with stand_ins(), data_root(last_load=LAST_LOAD):
            out = build.run(Args(), source=PackSource(card=RuntimeError("no card")))
        assert out["exit"] == 0 and ("Month-end sheets: Stock, Movements, Reconciliation and KPI log worked out; KPI: "
                                     "not worked out this time (RuntimeError).") in out["text"], out["text"]


@test("not worked out: totals carrying their own error are drawn by their sheet, and the state and record say so")
def _():
    with data_root(last_load=LAST_LOAD), stand_ins(stock_error="the ledger stops before that day") as seen:
        b, _m, _f = pack()
    assert seen["stock"][1] == date(2026, 3, 31)
    stock = b.controls["extra"]["Stock"]
    assert stock["state"] == "not worked out this time (the ledger stops before that day)" and stock["ageing"] is None
    assert "The stock figures: not worked out this time (the ledger stops before that day)." in texts(b.get("Stock"))
    parts = build.pack_parts(b)
    assert parts["ageing"] is None and parts["sheets"]["Stock"] == stock["state"] and not kpi._names_ageing(
        {"parts": parts})
    assert build.part_state({"error": "not worked out this time (x)"}) == "not worked out this time (x)"
    assert build.part_state({"error": None}) == build.part_state(None) == "ok"


@test("extra sheets: a name Excel refuses, one of the eight, or a builder that is no function is refused at once")
def _():
    m, totals = build.read_month(MONTH, PackSource())
    facts = sc.Facts(totals=totals, prepared_on=date(2026, 4, 8))
    for bad in ([("Data", None, lambda ws: None)], [("notes", None, lambda ws: None)], [("A:B", None, lambda ws: None)],
                [("x" * 32, None, lambda ws: None)], [("Two", None, lambda ws: None), ("two", None, lambda ws: None)],
                [("Fine", None, "not a function")], [("Fine", None)]):
        raises(ValueError, lambda bad=bad: sc.build(m, facts, extra=bad))
    b = sc.build(m, facts, extra=[("Fine", None, lambda ws: ws.text(0, 0, "one cell") or {"state": "ok"})])
    assert [s.name for s in b.sheets][6:8] == ["Fine", "Data"] and b.controls["extra"] == {"Fine": {"state": "ok"}}
    assert sc.build(m, facts).controls.get("extra") is None                              # the eight sheets: no extra


# ── the golden compare ─────────────────────────────────────────────────────────────────────────

def small_book(names: list[str], rules: dict | None = None, notes_text: str = "settings") -> bk.Book:
    b = bk.Book()
    for name in names:
        ws = b.sheet(name)
        ws.text(0, 0, f"{name} title")
        ws.put(1, 0, 5)
        if name == "Notes":
            ws.text(2, 0, notes_text)
        for _k in range((rules or {}).get(name, 0)):
            ws.cond("A2", {"type": "cell", "criteria": "greater than", "value": 0, "format": "up"})
    b.evaluate()
    return b


@test("golden: the reference's sheets as an ordered subsequence with the added ones named; counts sheet by sheet")
def _():
    with data_root() as root:
        def saved(name, *a, **kw):
            return small_book(*a, **kw).save(root / "exports" / f"{name}.xlsx")

        ref = saved("ref", ["A", "B", "Notes"], {"A": 1})
        res = golden.compare(saved("added", ["A", "X", "B", "Y", "Notes"], {"A": 1, "X": 2}), ref)
        assert res.ok and res.names_ok and res.added == ["X", "Y"], golden.summary(res)
        assert res.names_note == "the reference's 3 sheets in the same order, and 2 added: X, Y"
        assert [s.name for s in res.sheets] == ["A", "B"] and res.counts["cell_rules"] == (1, 1)  # X's rules never meet
        res = golden.compare(saved("order", ["B", "A", "Notes"], {"A": 1}), ref)
        assert not res.ok and not res.names_ok and "against" in res.names_note
        res = golden.compare(saved("missing", ["A", "Notes"], {"A": 1}), ref)
        assert not res.ok and next(s for s in res.sheets if s.name == "B").parts == ["sheet missing in the built file"]
        moved = golden.compare(saved("moved", ["A", "B", "Notes"], {"B": 1}), ref)       # same total, other sheet
        assert moved.counts["cell_rules"] == (1, 1) and not moved.ok, golden.summary(moved)
        lines = golden.summary(moved)
        assert lines[1] == "A: 2 cells compared: cell rules 0 (reference 1)" and lines[2].startswith("B: ") \
            and "cell rules 1 (reference 0)" in lines[2], lines
        other_notes = saved("notes", ["A", "B", "Notes"], {"A": 1}, notes_text="another day")
        assert golden.compare(other_notes, ref).ok                                        # Notes left out ...
        assert not golden.compare(other_notes, ref, sheets=["A", "Notes"]).ok             # ... unless it is named
        assert golden.sheet_order(["A", "B"], ["A", "B"]) == (True, "same sheets in the same order", [])


# ── the comment lines, end to end ──────────────────────────────────────────────────────────────

LINES = {
    "highlight_1": "Net sales were {net_change|updown} {net_change|abs_pct0} at BHD {net|bhd0}, against BHD "
                   "{net_prev|bhd0} in {month_prev|month}.",
    "highlight_2": "{invoices|Words} invoices against {invoices_prev|words}; the average invoice was BHD "
                   "{avg_invoice|bhd1}.",
    "highlight_3": "{rep1.name} sold BHD {rep1.net|bhd0}, {rep1.share|pct0} of the month.",
    "highlight_4": "{cat1.name} led the categories with BHD {cat1.net|bhd0}.",
    "highlight_5": "{accounts|Words} accounts were invoiced in {month|month}.",
    "point_1": "{accounts_prev_only|Words} account bought in {month_prev|month} and not in {month|month}.",
    "point_2": "Units sold were {units_change|updown} {units_change|abs_pct0} at {units|int}.",
    "point_3": "The best day was {best_day|day}, with BHD {best_day_net|bhd0}.",
    "point_4": "{days_sold|Words} days had sales in {month|month}.",
    "point_5": "{bulk_count|Words} invoices reached the bulk order line of BHD {bulk_threshold|bhd0}.",
    "products_note": "The top products are listed by net sales.",
    "accounts_note": "Each account appears under its own name this month.",
    "customers_note": "Account names are as recorded in the ERP.",
}
READ = {"highlight_1": "Net sales were up 127% at BHD 170, against BHD 75 in February 2026.",
        "point_5": "No invoices reached the bulk order line of BHD 200."}
FINAL_LINE = re.compile(r'^FINAL: python -m scripts\.ops report month (\S+) --commentary "([^"]+)" --token ([a-p]{12})$')


def drafted() -> tuple[dict, Path]:
    """The DRAFT build, then the template filled in with LINES: (the DRAFT's output, the commentary file)."""
    out = build.run(Args(), source=PackSource())
    assert out["exit"] == 0 and out["flags"]["template_written"], out["text"]
    path = Path(out["flags"]["commentary"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["slots"].update(LINES)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out, path


def checked(path: Path) -> tuple[str, str, str]:
    """python -m scripts.ops check on the file: (month, file, token) as its FINAL line prints them."""
    chk = textcheck.run(types.SimpleNamespace(file=str(path)))
    assert chk["exit"] == 0, chk["text"]
    m = FINAL_LINE.match(chk["text"].splitlines()[-1])
    assert m, chk["text"].splitlines()[-1]
    return m.group(1), m.group(2), m.group(3)


def final(month, given, token, source=None, **kw):
    return build.run(Args(spec=("month", month), commentary=given, token=token, **kw), source=source or PackSource())


@test("comment lines: the DRAFT writes the facts and the template once; check prints FINAL; FINAL builds the final file")
def _():
    with data_root(last_load=LAST_LOAD) as root, stand_ins():
        out, path = drafted()
        draft_path, record_path, facts_path = (Path(x) for x in out["files"][:3])
        assert draft_path.name == "YQ Bahrain - Mobile Accessories Sales Report - Mar 2026 - DRAFT.xlsx"
        assert out["files"][3] == str(path) and path.name == f"report-month-{MONTH}.commentary.json"
        assert facts_path.name == f"report-month-{MONTH}.facts.json" and path.parent == root / "exports" / "ops" / "2026-04-07"
        draft_record = json.loads(record_path.read_text(encoding="utf-8"))
        assert draft_record["draft"] is True and draft_record["kind"] == "month" and draft_record["built_at"]
        assert draft_record["parts"]["ageing"]["items_with_action"] == 3 and kpi._names_ageing(draft_record)
        text = out["text"]
        assert build.DRAFT_WORDS in text and f"Facts: {facts_path}" in text
        assert f"Commentary: {path} (the template, written now)" in text
        assert "Month-end sheets: Stock, Movements, Reconciliation, KPI and KPI log worked out." in text
        assert "Note: a bulk order, in the facts, is an invoice of BHD 200 or more excluding VAT" in text
        mine = path.read_bytes()
        again = build.run(Args(), source=PackSource())                  # a later DRAFT: his draft is kept as it is
        assert again["exit"] == 0 and again["flags"]["template_written"] is False and path.read_bytes() == mine
        assert f"Commentary: {path} (kept as it was: a draft is never written over)" in again["text"]
        month, given, token = checked(path)
        assert (month, given) == (MONTH, str(path))
        before = monthly(root)
        done = final(month, given, token)
        assert done["ok"] and done["exit"] == 0, done["text"]
        final_path = Path(done["files"][0])
        assert final_path.name == "YQ Bahrain - Mobile Accessories Sales Report - Mar 2026.xlsx"
        assert monthly(root) == sorted(before + [final_path.name])            # the DRAFTs stay as they were
        assert build.FINAL_WORDS.format(name=path.name) in done["text"] and "DRAFT:" not in done["text"]
        assert done["text"].splitlines()[1] == f"File: {final_path}"
        # the final file: the approved lines where the DRAFT has placeholders, and nothing else differs
        book = golden.read_book(final_path)
        dash = book.sheet("Dashboard")
        assert dash.cells["B15"][2] == READ["highlight_1"] and dash.cells["K19"][2] == READ["point_5"], dash.cells["B15"]
        b, _m, _f = pack()
        for sheet, refs in b.commentary.items():
            for ref in refs:
                assert not str(book.sheet(sheet).cells[ref][2]).startswith("DRAFT"), (sheet, ref)
                assert str(golden.read_book(draft_path).sheet(sheet).cells[ref][2]).startswith("DRAFT"), (sheet, ref)
        res = golden.compare(final_path, draft_path, set_aside=b.commentary)
        assert res.ok and sum(s.set_aside for s in res.sheets) == 13, golden.summary(res)


@test("comment lines: the final record, never written over by a later DRAFT; a second final takes the next free names")
def _():
    with data_root(last_load=LAST_LOAD) as root, stand_ins():
        out, path = drafted()
        month, given, token = checked(path)
        done = final(month, given, token)
        assert done["exit"] == 0, done["text"]
        rec_path = Path(done["files"][1])
        assert rec_path.name == f"report-month-{MONTH}.final.json" and rec_path.parent.name == "2026-04-07"
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        facts_doc = json.loads(Path(out["files"][2]).read_text(encoding="utf-8"))
        assert rec["kind"] == "month" and rec["final"] is True and rec["draft"] is False and rec["month"] == MONTH
        assert rec["facts_digest"] == facts_doc["digest"] and rec["checks_ok"] is True
        assert rec["commentary_sha"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert rec["due_date"] == "2026-04-07" and rec["file"] == done["files"][0]
        assert rec["approved_at"] <= rec["built_at"] and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+03:00", rec["built_at"])
        assert rec["parts"] == {"ageing": {"sheet": "Stock", "as_of": "2026-03-31", "items_with_action": 3},
                                "sheets": dict.fromkeys(PACK_NAMES, "ok")}
        kept = rec_path.read_bytes()
        later = build.run(Args(), source=PackSource())                  # a later DRAFT at the same data date
        assert later["exit"] == 0 and Path(later["files"][1]).name == f"report-month-{MONTH}.json"
        assert rec_path.read_bytes() == kept
        # approved twice: nothing written over; a reference that cannot be read is said after the save, the record kept
        again = final(month, given, token, compare=str(root / "no-reference.xlsx"))
        assert again["exit"] == 0 and again["flags"]["golden_ok"] is False, again["text"]
        assert "  the reference workbook could not be read (FileNotFoundError)" in again["text"].splitlines()
        assert Path(again["files"][0]).name == "YQ Bahrain - Mobile Accessories Sales Report - Mar 2026 (2).xlsx"
        assert Path(again["files"][1]).name == f"report-month-{MONTH}.final-2.json" and rec_path.read_bytes() == kept
        assert json.loads(Path(again["files"][1]).read_text(encoding="utf-8"))["golden_ok"] is False
        recs = kpi_log.records("month", MONTH)                          # what KPI 1c and 7 read
        assert sorted(r["_file"].split("/")[1] for r in recs if r.get("final")) == [
            f"report-month-{MONTH}.final-2.json", f"report-month-{MONTH}.final.json"]
        assert sorted(r["draft"] for r in recs) == [False, False, True] and all(kpi._names_ageing(r) for r in recs)
        assert not [r for r in recs if r["_file"].endswith((".facts.json", ".commentary.json"))]


@test("the final step refused in one sentence, nothing written: figures moved, a wrong token, lines refused, bad words")
def _():
    with data_root(last_load=LAST_LOAD) as root, stand_ins():
        _out, path = drafted()
        month, given, token = checked(path)
        files_before, ops_before = monthly(root), ops_files(root)

        def refused(out, exit_code: int, said: str) -> None:
            assert out["exit"] == exit_code and not out["ok"] and said in out["text"], out
            assert "\n" not in out["text"], out["text"]                                    # one sentence
            assert monthly(root) == files_before and ops_files(root) == ops_before        # nothing written

        moved = world_rows() + [line(date(2026, 3, 20), 50, 1, "Shop Five", ITEMS["X01"], 1, 1.1, VAN_A)]
        refused(final(month, given, token, PackSource(rows=moved)), 2,
                "a load changed the month's figures: run /yq-month again")
        refused(final(month, given, "a" * 12), 2, "the token is not this commentary file's")
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["slots"]["accounts_note"] = "Each account appears under its own name."           # changed after the check
        path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        refused(final(month, given, token), 2, "the token is not this commentary file's")
        month2, given2, token2 = checked(path)                                               # checked again: it passes

        def refuse_every_line(doc, facts_doc):           # the check's own rules changed since: the lines fail now
            raise textcheck.CommentaryRefused(textcheck.Checked(MONTH, "March 2026", refused={"point_1": ["a reason"]}))

        real = build.textcheck.render_slots
        build.textcheck.render_slots = refuse_every_line
        try:
            refused(final(month2, given2, token2), 2, "the comment lines no longer pass the check")
        finally:
            build.textcheck.render_slots = real
        other = json.loads(path.read_text(encoding="utf-8"))
        other["month"] = "2026-02"
        elsewhere = path.with_name("other.commentary.json")
        elsewhere.write_text(json.dumps(other), encoding="utf-8")
        ops_before = ops_files(root)
        refused(final(MONTH, str(elsewhere), token2), 1, "The commentary file is for 2026-02, not 2026-03")
        refused(final(MONTH, str(path.with_name("none.json")), token2), 1, "No commentary file at")
        for args, said in ((Args(commentary=given2), "takes its month, --commentary and --token together"),
                           (Args(token=token2), "takes its month, --commentary and --token together"),
                           (Args(spec=("month",), commentary=given2, token=token2), "takes its month"),
                           (Args(spec=("week", "2026-03-28"), commentary=given2, token=token2),
                            "belong to the monthly report's final step"),
                           (Args(commentary=given2, token="ABCDEFGHIJKL"), "a token is the twelve letters"),
                           (Args(commentary=given2, token=token2 + "a"), "a token is the twelve letters")):
            refused(build.run(args, source=PackSource()), 1, said)
        assert final(month2, given2, token2)["exit"] == 0                    # and with the right words it is built


# ── the due day and the bulk threshold ─────────────────────────────────────────────────────────

@test("the due day: working day five of the next month (Sunday to Thursday), as KPI 7 counts it; config moves it")
def _():
    with data_root():
        assert build.due_date("2026-03") == date(2026, 4, 7) == kpi._working_day(date(2026, 4, 1), 5, {6, 0, 1, 2, 3})
        assert build.due_date("2026-09") == date(2026, 10, 7) and build.due_date("2026-12") == date(2027, 1, 7)
    with data_root(cfg={"kpi.monthly_due_working_day": 1}):
        assert build.due_date("2026-03") == date(2026, 4, 1)
    with data_root(cfg={"kpi.monthly_due_working_day": 2, "kpi.working_days": ["Monday"]}):
        assert build.due_date("2026-03") == date(2026, 4, 13)
    with data_root(cfg={"kpi.monthly_due_working_day": 0}):
        raises(config.ConfigMissing, lambda: build.due_date("2026-03"))     # a wrong value is said, never a default


@test("the bulk threshold: config's when set (the facts and the Notes say it), the plan's default else, a wrong one stops")
def _():
    with data_root(last_load=LAST_LOAD, cfg={"report.bulk_invoice_bhd": 40}), stand_ins():
        assert build.bulk_threshold() == (40.0, False)
        out = build.run(Args(), source=PackSource())
        assert out["exit"] == 0 and "Note: a bulk order" not in out["text"], out["text"]
        facts_doc = json.loads(Path(out["flags"]["facts"]).read_text(encoding="utf-8"))
        # March's invoices of 40, 50 and 50 net (the others 20 and 10) are bulk orders at 40
        assert facts_doc["facts"]["bulk_threshold"]["value"] == 40 and facts_doc["facts"]["bulk_count"]["value"] == 3
        record = json.loads(Path(out["files"][1]).read_text(encoding="utf-8"))
        assert record["bulk_invoice_bhd"] == 40 and record["bulk_default"] is False
    with data_root():
        assert build.bulk_threshold() == (float(build.BULK_DEFAULT_BHD), True)
    for wrong in (-1, "200", True):
        with data_root(last_load=LAST_LOAD, cfg={"report.bulk_invoice_bhd": wrong}):
            raises(config.ConfigMissing, build.bulk_threshold)


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
    from scripts.ops import paths
    paths.set_data_root(None)
    config.use(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
