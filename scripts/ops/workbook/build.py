"""python -m scripts.ops report month [YYYY-MM] [--prepared-on YYYY-MM-DD] [--prepared-by NAME] [--compare FILE]
       python -m scripts.ops report month YYYY-MM --commentary FILE --token T        (the final step)

    month [YYYY-MM]   the monthly management sales report for a whole month (with no month, the last whole month
                      before the data date): the month-end pack, the eight sheets of the Sep 2026 workbook and five
                      more between Daily and Data, totals only: Stock (the main store at the end of the month's last
                      day), Movements (the vans and outlets over the month), Reconciliation, KPI and KPI log
    week [YYYY-MM-DD] the weekly report (/yq-week) for the week ending that Saturday, by default the last full
                      week; a day that is not a Saturday is refused. It is handed to scripts.ops.workbook.week
                      once that module exists; until then the command says it arrives in a later phase
    --prepared-on     the date on "Prepared ..." (default: today in Bahrain)
    --prepared-by     the name on "Prepared by ..." (default: config report.prepared_by, else none)
    --compare FILE    after the build, compare the new workbook with FILE, sheet by sheet (the golden test: the
                      reference's sheets but Notes, in the reference's order, each sheet with its own counts of
                      rules and charts; the sheets the pack adds are named; the commentary cells set aside)
    --commentary FILE --token T
                      the final step, run as the FINAL line of `python -m scripts.ops check` prints it. The month's
                      figures are worked out again: when a load has changed them, or the token is not this file's,
                      nothing is built (one sentence). Otherwise the final workbook with the approved lines, named
                      without DRAFT (the DRAFT stays), and its record report-month-<M>.final.json

It runs only on a WHOLE month, the plan's rule (scripts.ai_head.pack.monthly_ready): a load finished on or
after the first day of the next month, and Focus data reaching within two days of the month's last day.
The time of the last load is the one state.json records; with none on record, the newest successful
upload the database records stands in.

THE COMMENT LINES. Five Highlights, five Points to note and three notes beside the tables read DRAFT in the DRAFT
build, which writes beside its record the month's facts (report-month-<M>.facts.json, every build) and, only when
there is none yet, the commentary template (report-month-<M>.commentary.json): scripts.ops.workbook.facts. The lines
are written in the template with placeholders only, never a typed figure; `python -m scripts.ops check <file>` checks
them and prints the FINAL line. In the facts, a bulk order is an invoice of config report.bulk_invoice_bhd or more
(BHD, excluding VAT), else of the approved plan's default (BULK_DEFAULT_BHD); the Notes sheet says which. The facts'
digest covers the month's sales figures only (no stock, movement, reconciliation or KPI figure, which move with
every load and every logged line), so the final step is refused only when the sales figures moved.

THE PACK'S SHEETS are read through their own modules, each imported only when its sheet is drawn:
scripts.ops.workbook.sheets_extra (Stock, Movements), scripts.ops.recon_blocks with
scripts.ops.workbook.sheets_recon (Reconciliation), the KPI package (scripts.ops.kpi, scripts.ops.kpi_log,
scripts.ops.workbook.sheets_kpi; the KPI sheet's Status cells coloured by status). A module not there yet, or a part
that fails, leaves its sheet one line ("... not worked out this time (...)") and the workbook stands; the screen
names it, and the build record keeps each sheet's state and the ageing part KPI 1c looks for.

Reads, all read-only: the month's and the month before's lines from v_sales (division not SIM and not
Devices, every line, the zero-value ones too); the same months' totals in a separate statement (the
Notes sheet's control against the database); the Upcoming cards' categories (shop_upcoming_items, through
the read-only session: an item code the category rule does not know takes its card's category through the
config name table); the pack's parts, each through its module. Writes: the workbook under
exports/reports/monthly/ (never over an existing file: a second build of a month gets the next free name) and,
under exports/ops/<data date>/, the record of the build (report-month-<M>.json for a DRAFT; for a final
report-month-<M>.final.json, never written over: another final takes .final-2.json), the facts and the template.
Nothing else. From a code worktree nothing is read or written unless --data-root names the folder to work in (one
sentence, exit 1: worktree_refusal), so its code never puts a workbook or a record in the project folder.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path

from scripts.ops import db  # first: it blanks the side channels before any app import
from scripts.ops import config, fmt, paths, status, textcheck
from scripts.ops.workbook import book as bk, facts as fa, formula as fx, golden, model as mdl, sheets_core, style

COMMAND = "report"
REPORT_NAME = "YQ Bahrain - Mobile Accessories Sales Report - {label}"
# The weekly workbook's own module: `report week` is handed to its run(args, saturday) once it exists.
WEEK_MODULE = "scripts.ops.workbook.week"
ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
BASIS_SQL = "v.division NOT IN ('SIM', 'Devices')"
LINES_SQL = (
    "SELECT v.line_id, v.invoice_no, v.line_no, v.sale_date::text AS sale_date, v.customer_account, "
    "v.customer_name, v.item_name, v.quantity, v.rate_bhd, v.gross_bhd, v.net_bhd, v.salesman_raw, "
    "v.salesman_resolved, v.narration, v.category_name, v.discount_bhd, v.vat_amount_bhd FROM v_sales v "
    f"WHERE v.sale_date BETWEEN $1::date AND $2::date AND {BASIS_SQL} ORDER BY v.sale_date, v.invoice_no, v.line_no"
)
# the same months added up on their own: the Notes sheet's check that no line was lost on the way
TOTALS_SQL = (
    "SELECT CASE WHEN v.sale_date < $2::date THEN 'prev' ELSE 'cur' END AS m, COUNT(*) AS lines, "
    "COALESCE(SUM(v.quantity), 0) AS qty, ROUND(COALESCE(SUM(v.gross_bhd), 0)::numeric, 3) AS gross, "
    "ROUND(COALESCE(SUM(v.net_bhd), 0)::numeric, 3) AS net FROM v_sales v "
    f"WHERE v.sale_date BETWEEN $1::date AND $3::date AND {BASIS_SQL} GROUP BY 1"
)
ANCHOR_SQL = ("SELECT (SELECT MAX(sale_date) FROM v_sales)::text AS focus_to, "
              "(SELECT MAX(loaded_at) FROM v_agent_data_trust WHERE source = 'focus_upload')::text AS uploaded_at")
UPCOMING_SQL = "SELECT model_code, category FROM shop_upcoming_items WHERE category IS NOT NULL"

# The month-end pack: each part read through its own module, imported only when its sheet is drawn.
EXTRA_MODULE = "scripts.ops.workbook.sheets_extra"          # Stock and Movements (the weekly workbook's too)
RECON_MODULE = "scripts.ops.recon_blocks"                   # the month's reconciliations ...
RECON_SHEET_MODULE = "scripts.ops.workbook.sheets_recon"    # ... and their sheet
KPI_MODULE, KPI_LOG_MODULE = "scripts.ops.kpi", "scripts.ops.kpi_log"
KPI_SHEET_MODULE = "scripts.ops.workbook.sheets_kpi"
# the five sheets in their order, and what each one's line names when its part cannot be worked out
PACK = (("Stock", "The stock figures"), ("Movements", "The movements of the vans and outlets"),
        ("Reconciliation", "The reconciliations"), ("KPI", "The KPI scorecard"), ("KPI log", "The KPI log"))
NOT_BUILT = "not built yet"
# the KPI sheet's Status cells by status (scripts.ops.kpi.STATUSES), in the conditional styles of style.DXF_EXTRA
STATUS_STYLES = (("MET", "cf_green"), ("NOT MET", "cf_red"), ("NOT EVIDENCED", "cf_amber"), ("NOTHING DUE", "cf_grey"))
BULK_DEFAULT_BHD = 200        # the approved plan's bulk order: an invoice of this net value or more (BHD, ex-VAT)
TOKEN = re.compile(f"[{textcheck.TOKEN_LETTERS}]{{{textcheck.TOKEN_LEN}}}")
DRAFT_WORDS = (
    "DRAFT: the ten comment lines (five Highlights, five Points to note) and three notes beside the tables "
    "are placeholders until they are approved. They are written in the commentary file below with placeholders "
    "only and checked with python -m scripts.ops check; the FINAL line it prints builds the final workbook, and "
    "this DRAFT stays.")
FINAL_WORDS = ("FINAL: the ten comment lines (five Highlights, five Points to note) and three notes beside the tables "
               "are the approved ones, from {name}; the DRAFT stays as it was.")


class ReportInput(ValueError):
    """The words typed cannot be read: said in one sentence, before anything is read."""


class PartMissing(LookupError):
    """A part of the month-end pack whose module is not there yet: a later release brings it."""


def add_arguments(parser) -> None:
    parser.add_argument("spec", nargs="*", metavar="word", help="month [YYYY-MM], or week [YYYY-MM-DD] (a Saturday)")
    parser.add_argument("--prepared-on", help="the date on the report (YYYY-MM-DD; default today)")
    parser.add_argument("--prepared-by", help="the name on the report (default: config report.prepared_by)")
    parser.add_argument("--compare", help="a reference workbook to compare the new one with (the golden test)")
    parser.add_argument("--commentary", help="the final step: the commentary file the FINAL line of check names")
    parser.add_argument("--token", help="the final step: the token the FINAL line of check printed")


def parse_spec(words: list[str]) -> tuple[str, str | date | None]:
    """-> ('month', 'YYYY-MM' or None) or ('week', the Saturday as a date, or None for the default week)."""
    words = [w.strip().lower() for raw in (words or []) for w in str(raw).split() if w.strip()]
    if not words:
        return "month", None
    kind = words[0]
    if kind not in ("month", "week"):
        raise ReportInput("say which report: `report month` (or `report month 2026-09`); the weekly one comes later")
    rest = words[1:]
    if kind == "week":
        if len(rest) > 1:
            raise ReportInput("one week only, named by the Saturday it ends on, written YYYY-MM-DD (for example "
                              "2026-10-17)")
        if not rest:
            return "week", None
        try:
            day = date.fromisoformat(rest[0]) if ISO_DAY.fullmatch(rest[0]) else None
        except ValueError:
            day = None
        if day is None:
            raise ReportInput("a week is named by the Saturday it ends on, written YYYY-MM-DD (for example 2026-10-17)")
        mdl.Period.week(day)                # raises ValueError in one sentence for a day that is not a Saturday
        return "week", day
    if len(rest) > 1:
        raise ReportInput("one month only, written YYYY-MM (for example 2026-09)")
    if rest:
        mdl.month_bounds(rest[0])           # raises ValueError in one sentence
    return "month", rest[0] if rest else None


def worktree_refusal(what: str) -> str | None:
    """One sentence when this checkout is a code worktree and no --data-root names the folder to write in
    (YQ_MAIN_CHECKOUT alone does not): a worktree's code never writes the owner's workbooks, the files beside them
    or their build records into the project folder, the rule of /yq-stock's day file and the count sheets. None
    otherwise. `what` names what would have been written ('the final workbook and its record')."""
    if paths.is_worktree() and not paths.root_is_set():
        return (f"This is a code worktree: {what} belong to the project folder (to try it, name another folder with "
                "--data-root).")
    return None


def final_args(args, kind: str, month: str | None) -> tuple[Path, str] | None:
    """(the commentary file, the token) of the final step, or None for a DRAFT build; ReportInput in one sentence."""
    given, token = getattr(args, "commentary", None), getattr(args, "token", None)
    if not given and not token:
        return None
    if kind != "month":
        raise ReportInput("--commentary and --token belong to the monthly report's final step, the FINAL line that "
                          "python -m scripts.ops check prints")
    if not given or not token or month is None:
        raise ReportInput("the final step takes its month, --commentary and --token together: run the FINAL line that "
                          "python -m scripts.ops check printed, as it is")
    token = str(token).strip()
    if not TOKEN.fullmatch(token):
        raise ReportInput("a token is the twelve letters at the end of the FINAL line that python -m scripts.ops "
                          "check printed")
    return Path(str(given).strip()).expanduser(), token


def default_month(data_to: date) -> str:
    """The last whole month before the data date's own month."""
    first = data_to.replace(day=1)
    prev = date(first.year - (first.month == 1), (first.month - 2) % 12 + 1, 1)
    return f"{prev:%Y-%m}"


# ── reads (all read-only) ──────────────────────────────────────────────────────────────────────

def part_module(name: str):
    """The module of a part of the pack, imported now; PartMissing when it is not there yet."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and (exc.name == name or name.startswith(exc.name + ".")):
            raise PartMissing(name) from None
        raise


class Source:
    """Where the report's data comes from: production, read-only. Tests hand build_month a stand-in. The pack's five
    parts are read through their own modules (each imported only now; PartMissing when one is not there yet)."""

    def anchors(self) -> dict:
        rows = db.rpc(ANCHOR_SQL)
        return rows[0] if rows else {}

    def lines(self, first: date, last: date) -> list[dict]:
        return db.rpc_params(LINES_SQL, [first.isoformat(), last.isoformat()])

    def totals(self, first: date, cur_first: date, last: date) -> dict:
        rows = db.rpc_params(TOTALS_SQL, [first.isoformat(), cur_first.isoformat(), last.isoformat()])
        return {r["m"]: {k: r.get(k) for k in ("lines", "qty", "gross", "net")} for r in rows}

    def upcoming(self) -> dict:
        with db.session() as s:
            return {str(r["model_code"]).strip(): str(r["category"]).strip() for r in s.q(UPCOMING_SQL)
                    if r.get("model_code") and r.get("category")}

    # -- the month-end pack's parts
    def stock_totals(self, as_of: date) -> dict:
        """The Stock sheet's totals: the main store at the end of `as_of` (the month's last day), costs today's."""
        return part_module(EXTRA_MODULE).stock_totals(as_of=as_of)

    def moves_totals(self, first: date, last: date) -> dict:
        """The Movements sheet's totals: the vans and the outlets from `first` to `last`."""
        return part_module(EXTRA_MODULE).moves_totals(first=first, last=last)

    def recon_blocks(self, month: str) -> dict:
        return part_module(RECON_MODULE).month_blocks(month)

    def kpi_card(self, month: str) -> dict:
        """The KPI scorecard of the month (it writes nothing)."""
        return part_module(KPI_MODULE).scorecard(month)

    def kpi_lines(self, month: str) -> list:
        """The KPI log's lines whose event day falls in the month."""
        return part_module(KPI_LOG_MODULE).month_lines(month)


def ready(month: str, data_to: date | None, loaded_at) -> dict:
    """The plan's whole-month gate, fed with the last load's time and MAX(sale_date)."""
    from scripts.ai_head.pack import monthly_ready
    sources = [{"source": "focus_upload", "loaded_at": loaded_at},
               {"source": "focus_sales", "as_of": data_to.isoformat() if data_to else None}]
    return monthly_ready(sources, month)


def last_load_time(state: dict, anchors: dict):
    """(time, where it comes from): the later of the last /yq-load on record (state.json) and the newest
    successful upload the database records (a Data-page upload is a load too)."""
    last = state.get("last_load") if isinstance(state.get("last_load"), dict) else {}
    found = []
    if last and last.get("finished_at"):
        found.append((str(last["finished_at"]), "as recorded by /yq-load"))
    if anchors.get("uploaded_at"):
        found.append((str(anchors["uploaded_at"]), "the newest successful upload in the database"))
    if not found:
        return None, "none on record"
    return max(found, key=lambda x: (fmt.as_date(x[0]) or date.min, x[0]))


def setting(key: str, default):
    """(config.get(key), False), or (the approved plan's default, True) while config.json does not hold the key. A key
    it holds with a wrong value stops the command in one sentence (config.ConfigMissing), as every key does."""
    try:
        return config.get(key), False
    except config.ConfigMissing:
        try:
            held = config.load()
        except config.ConfigMissing:
            held = {}
        if key in held:
            raise
        return default, True


def bulk_threshold() -> tuple[float, bool]:
    """(the bulk order's least net value in BHD, whether the approved plan's default stands in)."""
    value, default = setting("report.bulk_invoice_bhd", BULK_DEFAULT_BHD)
    return float(value), default


def due_date(month: str) -> date:
    """The day the month's final report is due, as KPI 7 counts it: working day 'kpi.monthly_due_working_day' (the
    plan's 5) of the month after, the working days 'kpi.working_days' (the plan's Sunday to Thursday)."""
    kpi = part_module(KPI_MODULE)
    nth, _default = setting("kpi.monthly_due_working_day", kpi.MONTHLY_DUE_DEFAULT)
    names, _default = setting("kpi.working_days", list(kpi.WORKING_DAYS_DEFAULT))
    working = {config.weekday(w) for w in names}
    if not working or int(nth) < 1:
        raise ValueError("no working day to count the due day by")
    d, seen = mdl.Period.month(month).end + timedelta(days=1), 0
    while True:
        if d.weekday() in working:
            seen += 1
            if seen == int(nth):
                return d
        d += timedelta(days=1)


# ── the build ──────────────────────────────────────────────────────────────────────────────────

def target_path(period: mdl.Period, draft: bool):
    base = REPORT_NAME.format(label=period.label) + (" - DRAFT" if draft else "")
    folder = paths.reports_dir("monthly")
    for k in range(1, 100):
        name = base + (f" ({k})" if k > 1 else "") + ".xlsx"
        p = folder / name
        if not bk.exists(p):
            return p
    raise FileExistsError(f"ninety-nine copies of {base} already exist in {folder}")


def final_record_path(data_to: date, month: str) -> Path:
    """report-month-<M>.final.json under exports/ops/<data date>/, or the next free .final-2.json ...: the record of a
    final build is never written over (a later DRAFT writes report-month-<M>.json)."""
    folder = paths.exports_dir(data_to)
    for k in range(1, 100):
        p = folder / f"report-month-{month}.final{'' if k == 1 else f'-{k}'}.json"
        if not p.exists():
            return p
    raise FileExistsError(f"ninety-nine final records of {month} already exist in {folder}")


def control_values(book) -> dict:
    """The Notes sheet's checks as evaluated (every difference must be nil), read where the sheet put them."""
    notes, where = book.get("Notes"), book.controls

    def at(ref: str):
        r, c = fx.parse_cell(ref)
        cell = notes.cells.get((r, c))
        return None if cell is None else cell.value

    return {"export": [(label, at(ref)) for label, ref in where.get("export", [])],
            "sheets": [(label, at(a), at(b)) for label, a, b in where.get("sheets", [])],
            "unmapped": [at(ref) for ref in where.get("unmapped", [])],
            "db": [at(ref) for ref in where.get("db", [])],
            "narration": at(where["narration"]) if where.get("narration") else None}


def read_month(month: str, source: Source, category_names: dict | None = None) -> tuple[mdl.Model, dict]:
    """(the model, the v_sales month totals) of the month and the month before; nothing is written. ReportInput when
    neither month has an accessory sale."""
    period = mdl.Period.month(month)
    rows = source.lines(period.prev_start, period.end)
    totals = source.totals(period.prev_start, period.start, period.end)
    try:
        upcoming = source.upcoming()
        upcoming_note = None
    except Exception as exc:  # noqa: BLE001  (without the cards an unknown code reads Other; the screen says so)
        upcoming, upcoming_note = {}, f"the Upcoming cards could not be read ({type(exc).__name__})"
    model = mdl.build(period, rows, category_names=category_names, upcoming=upcoming)
    if not model.products or not model.reps:
        raise ReportInput(f"No accessory sales are loaded for {period.prev_long} or {period.long}: there is nothing "
                          "to report.")
    if upcoming_note:
        model.warnings.append(upcoming_note)
    return model, totals


def _evaluated(book, model: mdl.Model):
    errors = book.evaluate()
    if errors:
        note = f"{len(errors)} formula(s) came out as an error value: " + "; ".join(errors[:3])
        if note not in model.warnings:
            model.warnings.append(note)
    return book


def build_month(month: str, source: Source, *, prepared_on: date, prepared_by: str = "",
                category_names: dict | None = None, commentary: sheets_core.Commentary | None = None, read=None):
    """-> (book, model, facts): the eight sheets of the Sep 2026 workbook. Reads through `source` (or takes
    `read`, read_month's answer); writes nothing."""
    model, totals = read or read_month(month, source, category_names)
    facts = sheets_core.Facts(totals=totals, prepared_on=prepared_on, prepared_by=prepared_by)
    return _evaluated(sheets_core.build(model, facts, commentary), model), model, facts


def build_month_pack(month: str, source: Source, *, prepared_on: date, prepared_by: str = "",
                     category_names: dict | None = None, commentary: sheets_core.Commentary | None = None,
                     bulk_invoice_bhd: float | None = None, bulk_default: bool = False, read=None):
    """-> (book, model, facts): the month-end pack, the eight sheets with the five of PACK between Daily and Data
    (each sheet's state and what it found in book.controls["extra"]). Reads through `source` (or takes `read`,
    read_month's answer); writes nothing."""
    model, totals = read or read_month(month, source, category_names)
    facts = sheets_core.Facts(totals=totals, prepared_on=prepared_on, prepared_by=prepared_by,
                              bulk_invoice_bhd=bulk_invoice_bhd, bulk_default=bulk_default)
    book = sheets_core.build(model, facts, commentary, extra=pack_sheets(source, model.period))
    return _evaluated(book, model), model, facts


# ── the pack's five sheets ─────────────────────────────────────────────────────────────────────

def guarded(what: str, draw):
    """A builder for sheets_core.build: draw(ws), or the sheet's one line when its module is not there yet or its
    part fails (said by the error's type alone: another module's words may name a voucher or a shop)."""
    def builder(ws):
        try:
            return draw(ws)
        except PartMissing:
            why = NOT_BUILT
        except Exception as exc:  # noqa: BLE001  (one part never stops the workbook: its sheet says so)
            why = type(exc).__name__
        return sheets_core.not_worked(ws, what, why)
    return builder


def part_state(totals) -> str:
    """'ok', or 'not worked out this time (why)' for totals that carry an error of their own (the part's sheet
    then reads its own one line)."""
    err = totals.get("error") if isinstance(totals, Mapping) else None
    if not err:
        return "ok"
    why = " ".join(str(err).split()).rstrip(".")
    return why if why.startswith(sheets_core.NOT_WORKED) else f"{sheets_core.NOT_WORKED} ({why})"


def ageing_part(totals) -> dict | None:
    """The build record's ageing part, which KPI 1c looks for: the Stock sheet's ageing at the month's end, with the
    items the ageing review has the sales team push (Not moving and Slow); None when the Stock sheet has none."""
    if not isinstance(totals, Mapping) or totals.get("error") or not isinstance(totals.get("ageing"), Mapping):
        return None
    as_of = fmt.as_date(totals.get("as_of"))
    part = {"sheet": "Stock", "as_of": as_of.isoformat() if as_of else None}
    review = totals.get("review")
    counts = review.get("counts") if isinstance(review, Mapping) else None
    if isinstance(counts, Mapping):
        from scripts.ops.stock_review import NOT_MOVING, SLOW
        part["items_with_action"] = sum(int(fmt.dec(counts.get(s))) for s in (NOT_MOVING, SLOW))
    return part


def status_rules(ws, where: Mapping) -> None:
    """The KPI sheet's Status cells coloured by status: the scorecard's rows and the 90-day plan's (the rows and the
    status column sheets_kpi.kpi_sheet returns)."""
    col = where["status_col"]
    spans = [(where["first_row"], where["last_row"])] + ([tuple(where["plan"])] if where.get("plan") else [])
    ref = " ".join(f"{bk.a1(r1, col)}:{bk.a1(r2, col)}" for r1, r2 in spans)
    for status_, style_name in STATUS_STYLES:
        ws.cond(ref, {"type": "cell", "criteria": "equal to", "value": f'"{status_}"', "format": style_name})


def pack_sheets(source: Source, period: mdl.Period) -> list[tuple]:
    """[(name, tab colour, builder)] for sheets_core.build: the five sheets of PACK, each drawn from its own part."""
    month = period.key

    def stock(ws):
        totals = source.stock_totals(period.end)
        refs = part_module(EXTRA_MODULE).stock_sheet(ws, totals, f"Stock at the end of {period.long}")
        return {"state": part_state(totals), "refs": refs, "ageing": ageing_part(totals)}

    def movements(ws):
        totals = source.moves_totals(period.start, period.end)
        refs = part_module(EXTRA_MODULE).movements_sheet(ws, totals, f"Stock movements, {period.long}")
        return {"state": part_state(totals), "refs": refs}

    def reconciliation(ws):
        blocks = source.recon_blocks(month)
        refs = part_module(RECON_SHEET_MODULE).recon_sheet(ws, blocks)
        return {"state": part_state(blocks), "refs": refs}

    def kpi(ws):
        card = source.kpi_card(month)
        where = part_module(KPI_SHEET_MODULE).kpi_sheet(ws, card)
        status_rules(ws, where)
        return {"state": "ok", "refs": where}

    def kpi_log(ws):
        lines = source.kpi_lines(month)
        where = part_module(KPI_SHEET_MODULE).kpi_log_sheet(ws, lines, title=f"KPI log - {period.long}")
        return {"state": "ok", "refs": where}

    draws = {"Stock": stock, "Movements": movements, "Reconciliation": reconciliation, "KPI": kpi, "KPI log": kpi_log}
    return [(name, style.PALE, guarded(what, draws[name])) for name, what in PACK]


def pack_parts(book) -> dict:
    """The build record's parts: the ageing part (KPI 1c) and each pack sheet's state, by name."""
    extra = book.controls.get("extra") or {}
    stock = extra.get("Stock") if isinstance(extra.get("Stock"), Mapping) else {}
    return {"ageing": stock.get("ageing"),
            "sheets": {name: (r.get("state") or "ok") if isinstance(r, Mapping) else "ok" for name, r in extra.items()}}


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def pack_line(parts: dict) -> str:
    sheets = parts.get("sheets") or {}
    done = [name for name, state in sheets.items() if state == "ok"]
    rest = [f"{name}: {state}" for name, state in sheets.items() if state != "ok"]
    return ("Month-end sheets: " + (f"{_and(done)} worked out" if done else "none worked out")
            + ("; " + "; ".join(rest) if rest else "") + ".")


# ── the screen and the files ───────────────────────────────────────────────────────────────────

def report_text(model: mdl.Model, book, path, checks: dict, data_to: date | None, loaded: str,
                compare_lines: list[str] | None, said: list[str] | None = None,
                notes: list[str] | None = None) -> tuple[list[str], bool]:
    p = model.period
    lines: list[str] = []
    ok = True
    prev_n = sum(1 for ln in model.lines if p.in_prev(ln.sale_date))
    cur_n = len(model.lines) - prev_n
    prev_net = sum(ln.net for ln in model.lines if p.in_prev(ln.sale_date))
    cur_net = sum(ln.net for ln in model.lines if p.in_cur(ln.sale_date))
    lines.append(f"Monthly sales report, {p.long}, against {p.prev_long} (data to {fmt.day(data_to)}; {loaded}).")
    lines.append(f"File: {path}")
    lines.append("Sheets: " + ", ".join(ws.name for ws in book.sheets) + ".")
    lines.append(f"Lines {len(model.lines):,} ({p.prev_short} {prev_n:,}, {p.short} {cur_n:,}); net sales ex-VAT "
                 f"{p.prev_short} {fmt.bhd(prev_net)}, {p.short} {fmt.bhd(cur_net)} BHD.")
    bad = [label for label, diff in checks["export"] if diff not in (0, 0.0)]
    bad += [f"{name} sheet" for name, d1, d2 in checks["sheets"] if d1 not in (0, 0.0) or d2 not in (0, 0.0)]
    bad += ["Data sheet against v_sales"] if any(d not in (0, 0.0) for d in checks["db"]) else []
    unmapped = sum(int(x or 0) for x in checks["unmapped"])
    narr = int(checks["narration"] or 0)
    if bad or unmapped or narr:
        ok = False
        lines.append("CHECKS TO LOOK AT: " + "; ".join(bad + ([f"{unmapped} lines with no category"] if unmapped else [])
                                                      + ([f"{narr} narrations with an ID number"] if narr else [])) + ".")
    else:
        lines.append("Checks: the Data sheet equals v_sales in both months; every sheet's total less the Data sheet is "
                     "nil; no line without a category; no narration with an ID number.")
    other = [it for it in model.products if it.category == "Other"]
    lines.append(f"Categories: {len(model.categories)} rows" + (f", {len(other)} item(s) under Other (no rule and no card)"
                                                               if other else "")
                 + (f"; {len(model.not_counted)} item(s) with no value in the day book left out of the counts"
                    if model.not_counted else "") + ".")
    lines.extend(said if said is not None else [DRAFT_WORDS])
    for w in list(model.warnings) + list(notes or []):
        lines.append(f"Note: {w}.")
    if compare_lines is not None:
        lines.append("")
        lines.append("Compared with the reference workbook:")
        lines.extend("  " + ln for ln in compare_lines)
    return lines, ok


def _now() -> str:
    return datetime.now(status.BAHRAIN).isoformat(timespec="seconds")


def _save(book, path) -> None:
    if not paths.allowed_output(path):                # the workbook goes under exports/ of the data root, nowhere else
        raise PermissionError(f"scripts/ops writes only under business_data/ and exports/: {path}")
    book.save(path)


def _compare(args, path, book, flags: dict) -> list[str] | None:
    """The golden compare after the save; a reference that cannot be read is said, never a stop (the record of the
    build is written after it)."""
    if not getattr(args, "compare", None):
        return None
    try:
        res = golden.compare(path, args.compare, set_aside=book.commentary)     # the reference's sheets but Notes
    except Exception as exc:  # noqa: BLE001  (a file that is no workbook, or none at that path)
        flags["golden_ok"] = False
        return [f"the reference workbook could not be read ({type(exc).__name__})"]
    flags["golden_ok"] = res.ok
    return golden.summary(res)


def _bulk_notes(bulk: float, bulk_default: bool) -> list[str]:
    if not bulk_default:
        return []
    amount = f"{bulk:,.0f}" if float(bulk).is_integer() else f"{bulk:,.3f}"
    return [f"a bulk order, in the facts, is an invoice of BHD {amount} or more excluding VAT: the approved plan's "
            "default, as config report.bulk_invoice_bhd is not set"]


def _write_record(target: Path, record: dict, out: dict) -> Path | None:
    try:
        return paths.write_text(target, json.dumps(record, indent=1, default=str) + "\n")
    except OSError as exc:
        out["warnings"].append(f"the build record was not written ({type(exc).__name__})")
        return None


def run(args, source: Source | None = None) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    try:
        kind, month = parse_spec(getattr(args, "spec", None))
        prepared_on = date.fromisoformat(args.prepared_on) if getattr(args, "prepared_on", None) else status.today_bahrain()
        final = final_args(args, kind, month)
    except (ReportInput, ValueError) as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    if kind == "week":
        # the weekly workbook's module takes it from here: run(args, saturday), where the Saturday (parse_spec's
        # second part for a week) is a date already checked to be one, or None for the default week
        if importlib.util.find_spec(WEEK_MODULE) is None:
            return dict(out, ok=False, text="The weekly report (/yq-week) arrives in a later phase.", exit=1)
        return importlib.import_module(WEEK_MODULE).run(args, month)
    # a code worktree never writes the owner's monthly files (the workbook, the facts and the template beside it,
    # the final record KPI 7 reads) into the project folder: refused before anything is read
    refusal = worktree_refusal("the monthly workbook, the files beside it and its build record" if final is None
                               else "the final workbook and its record")
    if refusal:
        return dict(out, ok=False, text=refusal, exit=1)
    source = source or Source()
    anchors = source.anchors()
    data_to = fmt.as_date(anchors.get("focus_to"))
    if data_to is None:
        return dict(out, ok=False, text="No sales are loaded yet: there is no month to report.", exit=2)
    out["data_date"] = data_to.isoformat()
    month = month or default_month(data_to)
    state = status.read_state()
    loaded_at, loaded_from = last_load_time(state, anchors)
    gate = ready(month, data_to, loaded_at)
    if not gate["ready"]:
        nxt = gate["last"] + timedelta(days=1)
        reach = gate["last"] - timedelta(days=2)
        text = (f"The {mdl.Period.month(month).long} report is not built: the month is not whole yet. It needs a load "
                f"on or after {fmt.day_long(nxt)} and Focus data to {fmt.day_long(reach)} or later; the last load "
                f"({loaded_from}) was on {fmt.day_long(gate['last_upload']) if gate['last_upload'] else 'no day on record'}"
                f" and the data reaches {fmt.day_long(gate['focus_as_of'])}.")
        return dict(out, ok=False, text=text, flags={"ready": False, "month": month}, exit=2)
    prepared_by = args.prepared_by if getattr(args, "prepared_by", None) is not None else config.get("report.prepared_by")
    bulk, bulk_default = bulk_threshold()
    ctx = {"month": month, "data_to": data_to, "prepared_on": prepared_on, "prepared_by": prepared_by,
           "names": config.get("report.category_names"), "bulk": bulk, "bulk_default": bulk_default,
           "loaded": f"loaded {str(loaded_at)[:16].replace('T', ' ')}" if loaded_at else "no load time on record"}
    if final is not None:
        return _final(out, args, source, final, **ctx)
    return _draft(out, args, source, **ctx)


def _draft(out: dict, args, source: Source, *, month, data_to, prepared_on, prepared_by, names, bulk, bulk_default,
           loaded) -> dict:
    """The DRAFT build: the pack with DRAFT comment lines, the facts and (once) the template, the record."""
    try:
        book, model, facts = build_month_pack(month, source, prepared_on=prepared_on, prepared_by=prepared_by,
                                              category_names=names, bulk_invoice_bhd=bulk, bulk_default=bulk_default)
    except ReportInput as exc:
        return dict(out, ok=False, text=str(exc), flags={"ready": True, "month": month}, exit=2)
    path = target_path(model.period, draft=True)
    _save(book, path)
    built_at = _now()
    checks = control_values(book)
    flags = {"ready": True, "month": month, "draft": True, "lines": len(model.lines)}
    compare_lines = _compare(args, path, book, flags)
    doc = files = None
    try:
        doc = fa.month_facts(book, model, facts, bulk_invoice_bhd=bulk, data_date=data_to)
        files = fa.write_files(paths.exports_dir(data_to), month, doc)
    except (OSError, ValueError) as exc:
        out["warnings"].append(f"the month's facts were not written ({type(exc).__name__}): the comment lines cannot "
                               "be drafted this time")
    parts = pack_parts(book)
    said = [pack_line(parts), DRAFT_WORDS]
    if files:
        said.append(f"Facts: {files['facts']}")
        said.append(f"Commentary: {files['commentary']} ("
                    + ("the template, written now" if files["template_written"]
                       else "kept as it was: a draft is never written over") + ")")
    text_lines, ok = report_text(model, book, path, checks, data_to, loaded, compare_lines, said,
                                 _bulk_notes(bulk, bulk_default))
    record = {"kind": "month", "month": month, "data_date": data_to.isoformat(), "file": str(path),
              "built_on": prepared_on.isoformat(), "built_at": built_at, "draft": True, "final": False,
              "checks_ok": ok, "lines": len(model.lines), "golden_ok": flags.get("golden_ok"),
              "facts_digest": doc["digest"] if files else None, "bulk_invoice_bhd": bulk,
              "bulk_default": bulk_default, "parts": parts}
    rec = _write_record(paths.exports_dir(data_to) / f"report-month-{month}.json", record, out)
    out["files"] = [str(path)] + ([str(rec)] if rec else []) + (
        [str(files["facts"]), str(files["commentary"])] if files else [])
    flags.update(parts=parts["sheets"], facts=str(files["facts"]) if files else None,
                 commentary=str(files["commentary"]) if files else None,
                 template_written=files["template_written"] if files else None)
    return dict(out, ok=ok, text="\n".join(text_lines), flags=flags, exit=0 if ok else 2)


def _final(out: dict, args, source: Source, final: tuple[Path, str], *, month, data_to, prepared_on, prepared_by,
           names, bulk, bulk_default, loaded) -> dict:
    """The final step: the month's facts worked out again, the commentary checked against them through its token,
    then the pack with the approved lines, saved without DRAFT in its name, and its own record."""
    given, token = final
    flags = {"ready": True, "month": month, "draft": False, "final": True}
    try:
        doc, raw = textcheck.read_commentary(given)
    except textcheck.CommentaryUnreadable as exc:
        return dict(out, ok=False, text=str(exc), flags=flags, exit=1)
    if doc.get("month") != month:
        return dict(out, ok=False, flags=flags, exit=1,
                    text=f"The commentary file is for {str(doc.get('month'))[:10]}, not {month}: run the FINAL line that "
                         "python -m scripts.ops check printed, as it is.")
    approved_at = _now()
    try:
        read = read_month(month, source, names)
        core, model, core_facts = build_month(month, source, prepared_on=prepared_on, prepared_by=prepared_by,
                                              read=read)
    except ReportInput as exc:
        return dict(out, ok=False, text=str(exc), flags=flags, exit=2)
    try:
        fresh = fa.month_facts(core, model, core_facts, bulk_invoice_bhd=bulk, data_date=data_to)
    except ValueError as exc:
        return dict(out, ok=False, flags=dict(flags, refused="facts"), exit=2,
                    text=f"The final workbook is not built, as the month's facts could not be worked out again "
                         f"({type(exc).__name__}).")
    if doc.get("facts_digest") != fresh["digest"]:
        return dict(out, ok=False, flags=dict(flags, refused="figures"), exit=2,
                    text="The final workbook is not built, as a load changed the month's figures: run /yq-month again.")
    if textcheck.token_for(raw, fresh["digest"]) != token:
        return dict(out, ok=False, flags=dict(flags, refused="token"), exit=2,
                    text="The final workbook is not built, as the token is not this commentary file's: run "
                         "python -m scripts.ops check on it again and use the FINAL line it prints.")
    try:
        rendered = textcheck.render_slots(doc, fresh)
    except textcheck.CommentaryRefused:
        return dict(out, ok=False, flags=dict(flags, refused="lines"), exit=2,
                    text="The final workbook is not built, as the comment lines no longer pass the check: run "
                         "python -m scripts.ops check on the file again.")
    commentary = sheets_core.Commentary(**textcheck.commentary_fields(rendered))
    try:
        due = due_date(month).isoformat()
    except Exception as exc:  # noqa: BLE001  (the record says when it was due; KPI 7 works it out on its own too)
        due = None
        out["warnings"].append(f"the due day was not worked out ({type(exc).__name__})")
    book, model, _facts = build_month_pack(month, source, prepared_on=prepared_on, prepared_by=prepared_by,
                                           commentary=commentary, bulk_invoice_bhd=bulk, bulk_default=bulk_default,
                                           read=read)
    path = target_path(model.period, draft=False)
    _save(book, path)
    built_at = _now()
    checks = control_values(book)
    compare_lines = _compare(args, path, book, flags)
    parts = pack_parts(book)
    said = [pack_line(parts), FINAL_WORDS.format(name=Path(given).name)]
    text_lines, ok = report_text(model, book, path, checks, data_to, loaded, compare_lines, said,
                                 _bulk_notes(bulk, bulk_default))
    record = {"kind": "month", "month": month, "data_date": data_to.isoformat(), "file": str(path),
              "built_on": prepared_on.isoformat(), "built_at": built_at, "draft": False, "final": True,
              "approved_at": approved_at, "facts_digest": fresh["digest"], "commentary": str(given),
              "commentary_sha": "sha256:" + hashlib.sha256(raw).hexdigest(), "due_date": due, "checks_ok": ok,
              "lines": len(model.lines), "golden_ok": flags.get("golden_ok"), "bulk_invoice_bhd": bulk,
              "bulk_default": bulk_default, "parts": parts}
    rec = _write_record(final_record_path(data_to, month), record, out)
    out["files"] = [str(path)] + ([str(rec)] if rec else [])
    flags.update(lines=len(model.lines), parts=parts["sheets"], due_date=due)
    return dict(out, ok=ok, text="\n".join(text_lines), flags=flags, exit=0 if ok else 2)
