"""python -m scripts.ops report week [YYYY-MM-DD] [--prepared-on YYYY-MM-DD] [--prepared-by NAME]   (/yq-week)

    week              the weekly sales workbook for the brief's last full week: the Saturday that ends the last
                      Sunday-to-Saturday week lying wholly within the complete days (sales.full_week_end), the
                      same week /yq-brief last-week shows
    week YYYY-MM-DD   the week ending that Saturday. A day that is not a Saturday is refused (exit 1: build.py
                      says so before anything is read); a week that is not complete yet is not built (exit 2,
                      one sentence)
    --prepared-on / --prepared-by   as for the month (build.py's options)

scripts/ops/workbook/build.py hands `report week` to run(args, saturday) here.

THE WEEK runs Sunday to Saturday (model.Period.week). Every tile shows four windows: the week; the four weeks
before divided by four; the same seven days one month back; and the month to date against the KPI step in
force for the Saturday's month (config sales.kpi_steps), with the BHD a business day still needed over the
Sunday-to-Thursday days after the week to the month end. The sheets are sheets_week.py's.

COMPLETE. The brief's own rule (sales.export_day, sales.full_until): a day is complete when it is on or before
the data date, or before the day the reports were exported; when the data date is the export day it is a PART
DAY and the day before it is the last complete one. A week is complete when its Saturday is a complete day.

READS, all read-only, through a Source (tests hand run() a stand-in): the data dates; the loaded sale day before
a part day; the lines from the first day any window reaches to the Saturday, through the month report's own
statement (build.LINES_SQL: v_sales, division not SIM and not Devices, every line, the zero-value ones too) with
one column more, v_sales' is_giveaway (LINES_SQL here); each window's totals in a statement of their own (the
Notes sheet's controls against the database); the Upcoming cards (their categories); and for the parts beside
the sales, the stock model of /yq-stock (the stock ledger), /yq-moves, the purchases reconciliation and the KPI
log, each only when it is there.

GIVEAWAYS. A line v_sales marks is_giveaway (the Giveaway division, or a zero-priced line with units) stays on the
Data sheet with its value, every line counting in the controls against v_sales, but its line type is Giveaway, so
it counts in no units, invoices or accounts: /yq-brief leaves those lines out (sales.BASIS_SQL), and the week's
tiles are the brief's last full week.

THE PARTS beside the sales. Each one that cannot be worked out this time reads one line in the file, "Not worked
out this time (...)", is 0 in the Notes sheet's table of parts and false in the build record; the rest stands:
  * Stock and Movements: the shared sheets (scripts.ops.workbook.sheets_extra: stock_totals / moves_totals, then
    stock_sheet / movements_sheet(ws, totals, heading)). A function is called with the arguments it names out
    of what this report hands over (offered()); totals that carry an "error" (the shared functions never raise:
    they say why there) are a part not worked out; a sheet is built on a trial book and evaluated there first;
    a sheet that names an item or a voucher number is refused, since the file is forwarded and carries totals only.
  * Actions, counts only: the fast movers out of stock at the end of the Saturday, the order list per supplier
    and the late goods on the way (the stock model, as on its own day: stock.build takes the Saturday as as_of
    when it can); the receipts missing papers (scripts.ops.recon_blocks.purchases, its rows' status PAPERS
    MISSING); the transfers to check (scripts.ops.moves.audit, then exceptions, counted by moves' kinds, in the
    shared sheets' words); the counts due (the calendar: the third Sunday-to-Thursday day counted back from the
    month's last day, and the quarter-end full count); the receipts with no incoming check logged (the KPI log's
    receipt_check lines, once its writer, scripts.ops.kpi_log, is there).

WRITES, only from the project folder or a folder named with --data-root (a code worktree is refused before anything
is read: the week's file and its record belong to the project folder): the workbook under exports/reports/weekly/
(never over an existing file: the next free name, up to " (99)") and the build record for KPI seven, {kind,
week_ending, data_date, file, built_at, built_on, due_date (the first config kpi.weekly_due_weekday after the week,
Sunday while config has none), on_time, checks_ok, lines, parts}, at exports/ops/<data date>/report-week-<Saturday>
.json; a later build of the same week on the same data date never replaces it (KPI seven reads the first build)
but takes the next free report-week-<Saturday>.<n>.json, n from 2. Nothing else.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import re
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path

from scripts.ops import db  # first: it blanks the side channels before any app import
from scripts.ops import config, fmt, paths, sales, status, stock
from scripts.ops.workbook import book as bk, build, categories as cats, formula as fx, model as mdl
from scripts.ops.workbook import sheets_extra as sx, sheets_week as sw
from scripts.ops.workbook.sheets_week import Part

REPORT_NAME = "YQ Bahrain - Mobile Accessories Weekly Report - Week ending {day}"
RECORD_NAME = "report-week-{sat}.json"            # the first build's record; a later one: report-week-<sat>.<n>.json
WORKTREE_REFUSAL = ("This is a code worktree: the weekly workbook and its build record belong to the project folder "
                    "(to try it, name another folder with --data-root).")
EXTRA_MODULE = "scripts.ops.workbook.sheets_extra"
MOVES_MODULE = "scripts.ops.moves"
RECON_MODULE = "scripts.ops.recon_blocks"
KPI_LOG_MODULE = "scripts.ops.kpi_log"
DUE_KEY = "kpi.weekly_due_weekday"
DUE_DEFAULT = "Sunday"         # the plan's default (21-Oct-2026) while config.json has no 'kpi.weekly_due_weekday'
COUNT_DAY = 3                  # the month-end count sheets: the third business day counted back from the month's end
QUARTER_ENDS = (3, 6, 9, 12)
BUSINESS_DAYS = frozenset({6, 0, 1, 2, 3})       # Sunday to Thursday, as date.weekday() numbers them
STOCK_PARTS = ("fast", "order", "late")
# the shared sheets: sheet, totals function, sheet function, its name in a sentence
EXTRA = {"stock": ("Stock", "stock_totals", "stock_sheet", "the stock totals"),
         "movements": ("Movements", "moves_totals", "movements_sheet", "the movement totals")}
# what is to check on the transfers, by moves.exceptions' kind (moves.EXCEPTION_KINDS, in its order), in the words
# of the shared Movements sheet (sheets_extra.EXCEPTION_WORDS), which has no row for the damage store's kind
TRANSFER_KINDS = ("not_received", "damage_not_booked", "twins", "qty_differs", "elsewhere", "orphan")
TRANSFER_WORDS = {**sx.EXCEPTION_WORDS, "damage_not_booked": "Not booked in by the damage store by its deadline"}
# the week's lines: the month report's statement with v_sales' giveaway flag beside them (GIVEAWAYS)
LINES_SQL = build.LINES_SQL.replace(" FROM v_sales v ", ", v.is_giveaway FROM v_sales v ", 1)
# a Focus voucher or invoice number: letters, then -YY-MM-N (SI-YQ-26-10-123, YQ-26-09-2, SIO:X-26-01-1)
VOUCHER = re.compile(r"\b[A-Za-z]{1,6}(?:-[A-Za-z]{1,6})*-\d{2}-\d{2}-\d+\b")
ANCHOR_SQL = ("SELECT (SELECT MAX(sale_date) FROM v_sales)::text AS focus_to, "
              "(SELECT MAX(as_of_date) FROM stock_balance)::text AS stock_as_of, "
              "(SELECT MAX(loaded_at) FROM v_agent_data_trust WHERE source = 'focus_upload')::text AS uploaded_at")
_ONE_WINDOW = ("SELECT '{w}' AS w, v.quantity AS qty, v.gross_bhd AS gross, v.net_bhd AS net FROM v_sales v "
               "WHERE v.sale_date BETWEEN ${a}::date AND ${b}::date AND ")
# each window added up on its own (they overlap: the month to date holds days of the week): the Notes controls
WINDOW_TOTALS_SQL = (
    "SELECT x.w, COUNT(*) AS lines, COALESCE(SUM(x.qty), 0) AS qty, "
    "ROUND(COALESCE(SUM(x.gross), 0)::numeric, 3) AS gross, ROUND(COALESCE(SUM(x.net), 0)::numeric, 3) AS net FROM ("
    + " UNION ALL ".join(_ONE_WINDOW.format(w=w, a=a, b=b) + build.BASIS_SQL
                         for w, a, b in (("wk", 1, 2), ("prev", 3, 4), ("lm", 5, 6), ("mtd", 7, 2)))
    + ") x GROUP BY x.w")


class NotYet(RuntimeError):
    """A part this file cannot work out this time: the words go into the brackets of its one line."""


def _now() -> datetime:
    """The time of the build, in Bahrain (tests replace it)."""
    return datetime.now(status.BAHRAIN)


def _day(d: date | None) -> str:
    return sw.long_day(d) if d else "n/a"


def _num(x):
    v = fmt.dec(x)
    return int(v) if v == v.to_integral_value() else float(v)


# ── reads (all read-only) ──────────────────────────────────────────────────────────────────────

def _module(name: str, missing: str):
    """An optional module of the package, imported; NotYet(missing) when it is not there (not built yet)."""
    try:
        found = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        found = None
    if found is None:
        raise NotYet(missing)
    return importlib.import_module(name)


def _function(mod, name: str, where: str):
    fn = getattr(mod, name, None)
    if not callable(fn):
        raise NotYet(f"{where} has no {name}() yet")
    return fn


def call_with(fn, offered_: dict):
    """Call fn with the arguments it names out of `offered_`, by name (a positional-only one in its place); a
    parameter it requires that is not offered stops with NotYet, named. Its other parameters keep their defaults."""
    args, kwargs = [], {}
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.name in offered_:
            if p.kind is p.POSITIONAL_ONLY:
                args.append(offered_[p.name])
            else:
                kwargs[p.name] = offered_[p.name]
        elif p.default is p.empty:
            raise NotYet(f"{getattr(fn, '__name__', 'it')}() asks for '{p.name}', which the weekly report does not "
                         "hand over")
        elif p.kind is p.POSITIONAL_ONLY:
            break
    return fn(*args, **kwargs)


def offered(period: mdl.Period, d: date) -> dict:
    """What this report can hand a shared function: the week's days, the period and the data date."""
    return {"as_of": period.end, "saturday": period.end, "week_ending": period.end, "first": period.start,
            "last": period.end, "start": period.start, "end": period.end, "period": period, "data_date": d,
            "window": f"{period.start.isoformat()}..{period.end.isoformat()}", "rep": None, "prefer": "auto"}


class WeekSource(build.Source):
    """Where the weekly report's data comes from: production, read-only. Tests hand run() a stand-in."""

    def anchors(self) -> dict:
        rows = db.rpc(ANCHOR_SQL)
        return rows[0] if rows else {}

    def lines(self, first: date, last: date) -> list[dict]:
        """The month report's lines with v_sales' is_giveaway beside them (LINES_SQL)."""
        if LINES_SQL == build.LINES_SQL:            # the month's statement changed shape: say so, never read without it
            raise RuntimeError("the month report's lines statement no longer reads FROM v_sales v: the weekly report "
                               "cannot add the giveaway flag to it")
        return db.rpc_params(LINES_SQL, [first.isoformat(), last.isoformat()])

    def prev_day(self, d: date) -> date | None:
        """The loaded sale day before D (the latest full sale day when D is a part day)."""
        rows = db.rpc_params(sales.PREV_DAY_SQL, [d.isoformat()])
        return fmt.as_date((rows or [{}])[0].get("prev_day"))

    def window_totals(self, period: mdl.Period) -> dict:
        rows = db.rpc_params(WINDOW_TOTALS_SQL, [x.isoformat() for x in (
            period.start, period.end, period.prev_start, period.prev_end, period.lm_start, period.lm_end,
            period.mtd_start)])
        return {r["w"]: {k: r.get(k) for k in ("lines", "qty", "gross", "net")} for r in rows}

    def stock_model(self, sat: date):
        """The /yq-stock model (the stock ledger and its reads): as at the Saturday when stock.build can cut
        there, else as on the ledger's own last day."""
        cfg = stock.settings()
        inp = stock.gather("auto")
        if "as_of" in inspect.signature(stock.build).parameters:      # the stock plan's cut at a date, once it is there
            return stock.build(inp, cfg, **{"as_of": sat})
        return stock.build(inp, cfg)

    def transfers(self, period: mdl.Period, d: date) -> list:
        """The movement exceptions of the week (/yq-moves)."""
        mod = _module(MOVES_MODULE, "the movement audit (/yq-moves) is not built yet")
        offer = offered(period, d)
        audit = call_with(_function(mod, "audit", "the movement audit"), offer)
        return list(call_with(_function(mod, "exceptions", "the movement audit"), dict(offer, audit=audit)) or [])

    def papers(self, first: date, last: date) -> list:
        """The purchases reconciliation's rows: one per supplier receipt, each with its status."""
        mod = _module(RECON_MODULE, "the purchases reconciliation is not built yet")
        rows = call_with(_function(mod, "purchases", "the purchases reconciliation"),
                         {"first": first, "last": last, "start": first, "end": last, "as_of": last,
                          "month": f"{last:%Y-%m}"})
        if isinstance(rows, dict):
            rows = rows.get("rows")
        return list(rows or [])

    def receipt_checks(self) -> list:
        """The KPI log's incoming-check lines (kind receipt_check), once the log's writer is there."""
        _module(KPI_LOG_MODULE, "the incoming-check log (/yq-kpi log) is not built yet")
        f = stock.kpi_log_file()
        if not f.is_file():
            return []
        out = []
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("kind") == "receipt_check":
                out.append(row)
        return out


# ── the week ───────────────────────────────────────────────────────────────────────────────────

def complete_to(d: date, anchors: dict, state: dict, source) -> tuple[date, bool]:
    """(the last complete day, is D a part day): the brief's rule (sales.latest_full_day, sales.full_until), the
    loaded sale day before a part day read through the source."""
    exported = sales.export_day(d, anchors, state)
    part = exported == d
    latest = d
    if part:
        prev = source.prev_day(d)
        if prev is None:
            part = False
        else:
            latest = prev
    return sales.full_until(d, latest, exported), part


def not_complete(period: mdl.Period, d: date, full_to: date, part: bool, last_full: date) -> str:
    why = f" ({_day(d)} is a part day: the reports were exported on it)" if part else ""
    return (f"The week ending {_day(period.end)} is not complete yet: the last complete day of the data is "
            f"{_day(full_to)}{why}, and the last complete week ends on {_day(last_full)}.")


def due_date(sat: date) -> tuple[date, str]:
    """(the day the week's report is due, its weekday): the first config 'kpi.weekly_due_weekday' after the week,
    Sunday while config.json has none."""
    try:
        name = config.get(DUE_KEY)
    except config.ConfigMissing:
        try:
            absent = DUE_KEY not in config.load()
        except config.ConfigMissing:
            absent = True
        if not absent:
            raise                                   # there, and not a weekday: the command stops and names the key
        name = DUE_DEFAULT
    wd = config.weekday(name)
    due = sat + timedelta(days=(wd - sat.weekday()) % 7 or 7)
    return due, mdl.DAYS[due.weekday()]


def month_end(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


def count_day(month_first: date) -> date:
    """The day the month-end count sheets are due: the third Sunday-to-Thursday day counted back from the month's
    last day (the last day itself counting when it is one)."""
    d, n = month_end(month_first), 0
    while True:
        if d.weekday() in BUSINESS_DAYS:
            n += 1
            if n == COUNT_DAY:
                return d
        d -= timedelta(days=1)


# ── the parts beside the sales ─────────────────────────────────────────────────────────────────

def guarded(work, what: str) -> Part:
    try:
        return work()
    except NotYet as exc:
        return Part(False, str(exc))
    except Exception as exc:  # noqa: BLE001  (one part of the file, never the file)
        return Part(False, f"{what} could not be worked out: {type(exc).__name__}")


def forbidden_words(model: mdl.Model) -> dict:
    """What a forwarded sheet may never name: every item of the report by its Focus name, its product name and
    its code (a code with a digit, three characters or more, as a whole word). A product name that is part of a
    category's name ("Power Bank" of the Power Banks row) is no item to a reader, so it is left out."""
    rows = {c.casefold() for c in (*model.categories, *cats.CODE_RULE.values(), cats.HANDSET, cats.OTHER, cats.BUNDLES)}
    names: set[str] = set()
    codes: set[str] = set()
    for it in model.items.values():
        for text in (it.item, it.product):
            t = " ".join(str(text or "").split()).casefold()
            if len(t) >= 8 and not any(t in row for row in rows):
                names.add(t)
        if len(it.code) >= 3 and any(ch.isdigit() for ch in it.code):
            codes.add(it.code)
    rx = (re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(c) for c in sorted(codes, key=len, reverse=True))
                     + r")(?![A-Za-z0-9])", re.I) if codes else None)
    return {"names": sorted(names), "codes": rx}


def leak(ws: bk.Sheet, forbidden: dict) -> str | None:
    """'a voucher number' or 'an item' when a text on the sheet names one; None when it names neither."""
    for cell in ws.cells.values():
        text = cell.value if isinstance(cell.value, str) else None
        if not text:
            continue
        if VOUCHER.search(text):
            return "a voucher number"
        low = " ".join(text.split()).casefold()
        if any(name in low for name in forbidden["names"]):
            return "an item"
        if forbidden["codes"] is not None and forbidden["codes"].search(text):
            return "an item"
    return None


def extra_part(kind: str, period: mdl.Period, d: date, forbidden: dict) -> Part:
    """A shared sheet (Stock or Movements), built on a trial book and evaluated there; Part.data['sheet'] when it
    stands, else why not."""
    sheet_name, totals_name, writer_name, what = EXTRA[kind]
    heading = (f"Stock at the end of Saturday {_day(period.end)}" if kind == "stock" else
               f"Movements in the week ending {_day(period.end)}")
    try:
        mod = _module(EXTRA_MODULE, "the shared stock and movement sheets are not built yet")
        totals = call_with(_function(mod, totals_name, "the shared sheet module"), offered(period, d))
        if isinstance(totals, Mapping) and totals.get("error"):    # the shared functions never raise: they say why
            raise NotYet(f"{what}: {' '.join(str(totals['error']).split()).rstrip('.')}")
        trial = bk.Book()
        ws = trial.sheet(sheet_name)
        _function(mod, writer_name, "the shared sheet module")(ws, totals, heading)
        errors = trial.evaluate()
        if errors:
            raise NotYet(f"{what}: {len(errors)} formula(s) came out as an error value")
        hit = leak(ws, forbidden)
        if hit:
            raise NotYet(f"{what} named {hit}, and a forwarded sheet carries totals only")
        return Part(True, data={"sheet": ws})
    except NotYet as exc:
        return Part(False, str(exc))
    except Exception as exc:  # noqa: BLE001
        return Part(False, f"{what} could not be worked out: {type(exc).__name__}")


def stock_parts(model, period: mdl.Period) -> tuple[dict, list | None]:
    """The fast movers out at the end of the Saturday, the order list per supplier and the late goods on the way,
    from a /yq-stock model; with the main store's supplier receipts of the PAPERS_DAYS days to the Saturday
    (None when the ledger does not reach the Saturday)."""
    sat = period.end
    if model.d < sat:
        why = f"the stock ledger runs to {_day(model.d)}, before the Saturday"
        return {k: Part(False, why) for k in STOCK_PARTS}, None
    parts: dict = {}
    fast = model.facts.get("fast") or {}
    movers = list(fast.get("list") or [])
    if sat < model.series_start:
        parts["fast"] = Part(False, f"the stock ledger's day-end figures start on {_day(model.series_start)}, after "
                                    "the Saturday")
    else:
        out = sum(1 for it in movers if stock.day_value(it, model.series_start, sat) <= 0)
        parts["fast"] = Part(True, data={"n": len(movers), "out": out, "as_of": sat,
                                         "rank_first": fast.get("rank_first"), "rank_last": fast.get("rank_last")})
    by_supplier: dict[str, list] = {}
    for x in model.facts.get("order") or []:
        row = by_supplier.setdefault(str(x.get("supplier") or "Other"), [0, 0])
        row[0] += 1
        row[1] += _num(x.get("qty"))
    parts["order"] = Part(True, data={"as_of": model.d, "suppliers": sorted(
        ((s, n, _num(u)) for s, (n, u) in by_supplier.items()), key=lambda t: (-t[2], -t[1], t[0]))})
    ow = model.facts.get("on_the_way") or {}
    late: dict[str, list] = {}
    for x in ow.get("lines") or []:
        due = fmt.dec(x.get("due"))
        if x.get("late") and due > 0:
            row = late.setdefault(str(x.get("supplier") or "(no supplier logged)"), [0, 0])
            row[0] += 1
            row[1] += _num(due)
    parts["late"] = Part(True, data={"as_of": model.d, "logged": bool(ow.get("log")), "suppliers": sorted(
        ((s, n, _num(u)) for s, (n, u) in late.items()), key=lambda t: (-t[2], -t[1], t[0]))})
    first = sat - timedelta(days=sw.PAPERS_DAYS - 1)
    receipts = sorted({(day, str(voucher)) for it in model.items for (day, voucher, _u, _v) in it.receipts
                       if first <= day <= sat})
    return parts, receipts


def papers_part(rows: list, first: date, last: date) -> Part:
    def status_of(r) -> str:
        return str((r.get("status") if isinstance(r, dict) else getattr(r, "status", "")) or "")
    missing = sum(1 for r in rows if "PAPERS MISSING" in status_of(r).upper())
    return Part(True, data={"first": first, "last": last, "days": sw.PAPERS_DAYS, "receipts": len(rows),
                            "missing": missing})


def transfers_part(found: list, period: mdl.Period) -> Part:
    """moves.exceptions' items counted by kind, in moves' order of kinds and the shared sheets' words (a register
    key of /yq-watch read as the kind it stands for; a kind neither knows, in its own words, after them)."""
    kinds: Counter = Counter()
    for x in found:
        kind = str((x.get("kind") if isinstance(x, Mapping) else getattr(x, "kind", None)) or "other").strip().lower()
        kinds[sx.EXCEPTION_ALIASES.get(kind, kind)] += 1
    order = {k: i for i, k in enumerate(TRANSFER_KINDS)}
    rows = [(TRANSFER_WORDS.get(k, k.replace("_", " ").capitalize()), n)
            for k, n in sorted(kinds.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0]))]
    return Part(True, data={"first": period.start, "last": period.end, "kinds": rows})


def _number(text) -> str:
    """A voucher number as the KPI log's ref may write it: the part after its series, spaces out, any case."""
    return "".join(str(text or "").rsplit(":", 1)[-1].split()).casefold()


def checks_part(receipts: list, logged: list, first: date, last: date) -> Part:
    refs = {_number(x.get("ref")) for x in logged if x.get("ref")}
    unchecked = sum(1 for _day_, voucher in receipts if _number(voucher) not in refs)
    return Part(True, data={"first": first, "last": last, "receipts": len(receipts), "unchecked": unchecked})


def counts_part(period: mdl.Period) -> Part:
    """The month-end count and the quarter-end full count still to come after the week: the sheets' day and the
    month end, for the month of the day after the Saturday and for the next quarter's last month."""
    after = period.end + timedelta(days=1)
    m = after.replace(day=1)
    q = m
    while q.month not in QUARTER_ENDS:
        q = month_end(q) + timedelta(days=1)
    rows = [("Month-end count: vans, outlets and the main-store cycle list", count_day(m), month_end(m)),
            ("Quarter-end full count: every item", count_day(q), month_end(q))]
    return Part(True, data={"rows": rows})


def gather_parts(period: mdl.Period, d: date, source, model: mdl.Model) -> dict:
    """Every part beside the sales; each one that fails stands aside on its own."""
    sat = period.end
    first = sat - timedelta(days=sw.PAPERS_DAYS - 1)
    forbidden = forbidden_words(model)
    parts = {"stock": extra_part("stock", period, d, forbidden),
             "movements": extra_part("movements", period, d, forbidden)}
    receipts, stock_why = None, "the stock ledger could not be read"
    try:
        got, receipts = stock_parts(source.stock_model(sat), period)
        parts.update(got)
        if receipts is None:
            stock_why = got["order"].why
    except NotYet as exc:
        stock_why = str(exc)
        parts.update({k: Part(False, stock_why) for k in STOCK_PARTS})
    except Exception as exc:  # noqa: BLE001
        stock_why = f"the stock figures could not be worked out: {type(exc).__name__}"
        model.warnings.append(f"{stock_why}: {str(exc)[:160]}")
        parts.update({k: Part(False, stock_why) for k in STOCK_PARTS})
    parts["papers"] = guarded(lambda: papers_part(source.papers(first, sat), first, sat), "the receipts' papers")
    parts["transfers"] = guarded(lambda: transfers_part(source.transfers(period, d), period), "the transfers")
    parts["counts"] = counts_part(period)

    def checks() -> Part:
        logged = source.receipt_checks()            # NotYet first while the log's writer is not built
        if receipts is None:
            raise NotYet(stock_why)
        return checks_part(receipts, logged, first, sat)

    parts["checks"] = guarded(checks, "the incoming checks")
    return parts


# ── the build ──────────────────────────────────────────────────────────────────────────────────

def build_week(period: mdl.Period, source, *, d: date, prepared_on: date, prepared_by: str = "",
               category_names: dict | None = None, kpi_step=None):
    """-> (book, model, facts). Reads through `source`; writes nothing."""
    rows = source.lines(period.data_first, period.end)
    totals = source.window_totals(period)
    try:
        upcoming = source.upcoming()
        upcoming_note = None
    except Exception as exc:  # noqa: BLE001  (without the cards an unknown code reads Other; the screen says so)
        upcoming, upcoming_note = {}, f"the Upcoming cards could not be read ({type(exc).__name__})"
    model = mdl.build(period, rows, category_names=category_names, upcoming=upcoming)
    if not model.products or not model.reps:
        raise build.ReportInput(f"No accessory sales are loaded for {period.long} or the weeks before it: there is "
                                "nothing to report.")
    if upcoming_note:
        model.warnings.append(upcoming_note)
    parts = gather_parts(period, d, source, model)
    giveaways = frozenset(sw.line_key(ln) for ln in mdl.tidy([r for r in rows if r.get("is_giveaway") is True]))
    facts = sw.WeekFacts(totals=totals, prepared_on=prepared_on, prepared_by=prepared_by,
                         kpi_step=float(kpi_step or 0), parts=parts, giveaways=giveaways)
    book = sw.build(model, facts)
    errors = book.evaluate()
    if errors:
        model.warnings.append(f"{len(errors)} formula(s) came out as an error value: " + "; ".join(errors[:3]))
    sw.fit_rows(book)
    return book, model, facts


def target_path(period: mdl.Period) -> Path:
    sat = period.end
    base = REPORT_NAME.format(day=f"{sat.day} {mdl.MONTHS[sat.month - 1][:3]} {sat.year}")
    folder = paths.reports_dir("weekly")
    for k in range(1, 100):
        p = folder / (base + (f" ({k})" if k > 1 else "") + ".xlsx")
        if not bk.exists(p):
            return p
    raise FileExistsError(f"ninety-nine copies of {base} already exist in {folder}")


def record_path(d: date, sat: date) -> Path:
    """Where this build's record goes: report-week-<sat>.json for the first build of the week on this data date,
    else the next free report-week-<sat>.<n>.json (n from 2): a record is never replaced, so KPI seven reads the
    week's first build (scripts.ops.kpi_log.records, oldest build first)."""
    folder = paths.exports_dir(d)
    first = folder / RECORD_NAME.format(sat=sat.isoformat())
    if not bk.exists(first):
        return first
    for n in range(2, 1000):
        p = folder / f"report-week-{sat.isoformat()}.{n}.json"
        if not bk.exists(p):
            return p
    raise FileExistsError(f"a thousand build records of the week ending {sat.isoformat()} are in {folder}")


def control_values(book) -> dict:
    """The Notes sheet's checks as evaluated (every one must be nil), read where the sheet put them."""
    notes, where = book.get("Notes"), book.controls

    def at(ref: str):
        r, c = fx.parse_cell(ref)
        cell = notes.cells.get((r, c))
        return None if cell is None else cell.value

    return {"db": [(label, at(ref)) for label, ref in where.get("db", [])],
            "sheets": [(label, [at(ref) for ref in refs]) for label, refs in where.get("sheets", [])],
            "unmapped": [at(ref) for ref in where.get("unmapped", [])],
            "narration": at(where["narration"]) if where.get("narration") else None}


def checks_ok(checks: dict) -> tuple[bool, list[str]]:
    nil = (0, 0.0)
    bad = [label for label, diff in checks["db"] if diff not in nil]
    bad += [f"{name} sheet" for name, diffs in checks["sheets"] if any(x not in nil for x in diffs)]
    unmapped = sum(int(x or 0) for x in checks["unmapped"])
    if unmapped:
        bad.append(f"{unmapped} lines with no category")
    narr = int(checks["narration"] or 0)
    if narr:
        bad.append(f"{narr} narrations with an ID number")
    return not bad, bad


def window_value(book, measure: str, w: str):
    r, c = sw.window_cell(measure, w)
    cell = book.get("Dashboard").cells.get((r, c))
    return None if cell is None else cell.value


def report_text(model: mdl.Model, book, path, bad: list[str], parts: dict, d: date, loaded: str, due: date,
                due_day: str, built_on: date) -> list[str]:
    p = model.period
    v = lambda w: fmt.bhd(window_value(book, "net", w))  # noqa: E731
    lines = [f"Weekly sales report, {p.long} ({sw.span_text(p.start, p.end)}): data to {fmt.day(d)}; {loaded}.",
             f"File: {path}",
             "Sheets: " + ", ".join(ws.name for ws in book.sheets) + ".",
             f"Lines {len(model.lines):,} from {_day(p.data_first)}; net sales ex-VAT, BHD: the week {v('wk')}, the four "
             f"weeks before {v('prev')} a week, the same days last month {v('lm')}, the month to date {v('mtd')}."]
    if bad:
        lines.append("CHECKS TO LOOK AT: " + "; ".join(bad) + ".")
    else:
        lines.append("Checks: the Data sheet equals v_sales in all four windows; every sheet's total less the Data "
                     "sheet is nil; no line without a category; no narration with an ID number.")
    missing = [f"{label} ({parts[key].why})" for key, label in sw.PARTS if key in parts and not parts[key].ok]
    lines.append("Every part beside the sales was worked out." if not missing else
                 "Not worked out this time: " + "; ".join(missing) + ".")
    lines.append(f"Due by {due_day} {_day(due)}; built {_day(built_on)}: "
                 + ("on time." if built_on <= due else "after the due day."))
    for w in model.warnings:
        lines.append(f"Note: {w}.")
    return lines


def run(args, saturday: date | None = None, source=None) -> dict:
    out = {"ok": True, "command": build.COMMAND, "data_date": None, "text": "", "flags": {}, "files": [],
           "warnings": []}
    if paths.is_worktree() and not paths.root_is_set():
        # a code worktree finds the project folder's data by itself: its file and record would land among his
        return dict(out, ok=False, text=WORKTREE_REFUSAL, exit=1)
    try:
        prepared_on = (date.fromisoformat(args.prepared_on) if getattr(args, "prepared_on", None)
                       else status.today_bahrain())
        if saturday is not None:
            mdl.Period.week(saturday)          # not a Saturday: one sentence (build.py has said it before any read)
    except ValueError as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    source = source or WeekSource()
    anchors = source.anchors()
    d = fmt.as_date(anchors.get("focus_to"))
    if d is None:
        return dict(out, ok=False, text="No sales are loaded yet: there is no week to report.", exit=2)
    out["data_date"] = d.isoformat()
    state = status.read_state()
    full_to, part = complete_to(d, anchors, state, source)
    last_full = sales.full_week_end(full_to)
    period = mdl.Period.week(saturday or last_full)
    flags = {"week_ending": period.end.isoformat(), "complete": period.end <= full_to}
    if not flags["complete"]:
        return dict(out, ok=False, text=not_complete(period, d, full_to, part, last_full), flags=flags, exit=2)
    if getattr(args, "compare", None):
        out["warnings"].append("--compare is for the monthly report: the weekly one has no reference file")
    prepared_by = args.prepared_by if getattr(args, "prepared_by", None) is not None else config.get("report.prepared_by")
    step = sales.kpi_step_for(f"{period.end:%Y-%m}")
    due, due_day = due_date(period.end)              # a config value it cannot read stops the command before a write
    try:
        book, model, facts = build_week(period, source, d=d, prepared_on=prepared_on, prepared_by=prepared_by,
                                        category_names=config.get("report.category_names"), kpi_step=step)
    except build.ReportInput as exc:
        return dict(out, ok=False, text=str(exc), flags=flags, exit=2)
    path = target_path(period)
    if not paths.allowed_output(path):              # the workbook goes under exports/ of the data root, nowhere else
        raise PermissionError(f"scripts/ops writes only under business_data/ and exports/: {path}")
    book.save(path)
    ok, bad = checks_ok(control_values(book))
    now = _now()
    loaded_at, _from = build.last_load_time(state, anchors)
    loaded = f"loaded {str(loaded_at)[:16].replace('T', ' ')}" if loaded_at else "no load time on record"
    text = report_text(model, book, path, bad, facts.parts, d, loaded, due, due_day, now.date())
    record = {"kind": "weekly", "week_ending": period.end.isoformat(), "data_date": d.isoformat(), "file": str(path),
              "built_at": now.isoformat(timespec="seconds"), "built_on": now.date().isoformat(),
              "due_date": due.isoformat(), "on_time": now.date() <= due, "checks_ok": ok, "lines": len(model.lines),
              "parts": {key: {"ok": part_.ok, "why": part_.why} for key, part_ in facts.parts.items()}}
    out["files"].append(str(path))
    try:
        rec = paths.write_text(record_path(d, period.end), json.dumps(record, indent=1) + "\n")
        out["files"].append(str(rec))
    except OSError as exc:
        out["warnings"].append(f"the build record was not written ({type(exc).__name__})")
    flags.update(lines=len(model.lines), checks_ok=ok, on_time=record["on_time"],
                 parts={k: v["ok"] for k, v in record["parts"].items()})
    return dict(out, ok=ok, text="\n".join(text), flags=flags, exit=0 if ok else 2)
