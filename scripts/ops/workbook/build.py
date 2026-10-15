"""python -m scripts.ops report month [YYYY-MM] [--prepared-on YYYY-MM-DD] [--prepared-by NAME] [--compare FILE]

    month [YYYY-MM]   the monthly management sales report (the eight sheets of the Sep 2026 workbook) for a
                      whole month; with no month, the last whole month before the data date
    week              the weekly report arrives in a later phase (/yq-week)
    --prepared-on     the date on "Prepared ..." (default: today in Bahrain)
    --prepared-by     the name on "Prepared by ..." (default: config report.prepared_by, else none)
    --compare FILE    after the build, compare the new workbook with FILE, sheet by sheet (the golden test:
                      every sheet but Notes; the commentary cells, still DRAFT, set aside)

It runs only on a WHOLE month, the plan's rule (scripts.ai_head.pack.monthly_ready): a load finished on or
after the first day of the next month, and Focus data reaching within two days of the month's last day.
The time of the last load is the one state.json records; with none on record, the newest successful
upload the database records stands in.

Reads, all read-only: the month's and the month before's lines from v_sales (division not SIM and not
Devices, every line, the zero-value ones too); the same months' totals in a separate statement (the
Notes sheet's control against the database); the Upcoming cards' categories (shop_upcoming_items, through
the read-only session: an item code the category rule does not know takes its card's category through the
config name table). Writes: the workbook under exports/reports/monthly/ (never over an existing file:
a second build of a month gets the next free name) and a small record of the build under
exports/ops/<data date>/. Nothing else.

The ten comment lines (five Highlights, five Points to note) and three notes beside the tables are DRAFT
placeholders until the approval step of a later phase writes them; the file name says DRAFT meanwhile.
"""
from __future__ import annotations

import json
import re
from datetime import date

from scripts.ops import db  # first: it blanks the side channels before any app import
from scripts.ops import config, fmt, paths, status
from scripts.ops.workbook import golden, model as mdl, sheets_core
from scripts.ops.workbook.book import a1

COMMAND = "report"
REPORT_NAME = "YQ Bahrain - Mobile Accessories Sales Report - {label}"
BASIS_SQL = "v.division NOT IN ('SIM', 'Devices')"
LINES_SQL = (
    "SELECT v.line_id, v.invoice_no, v.line_no, v.sale_date::text AS sale_date, v.customer_account, "
    "v.customer_name, v.item_name, v.quantity, v.rate_bhd, v.gross_bhd, v.net_bhd, v.salesman_raw, "
    "v.salesman_resolved, v.narration, v.category_name FROM v_sales v "
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
CONTROL_LABELS = ("Day book lines", "Quantity, all lines", "Gross, including VAT (BHD)", "Net sales, excluding VAT (BHD)")


class ReportInput(ValueError):
    """The words typed cannot be read: said in one sentence, before anything is read."""


def add_arguments(parser) -> None:
    parser.add_argument("spec", nargs="*", metavar="word", help="month [YYYY-MM]; week arrives in a later phase")
    parser.add_argument("--prepared-on", help="the date on the report (YYYY-MM-DD; default today)")
    parser.add_argument("--prepared-by", help="the name on the report (default: config report.prepared_by)")
    parser.add_argument("--compare", help="a reference workbook to compare the new one with (the golden test)")


def parse_spec(words: list[str]) -> tuple[str, str | None]:
    words = [w.strip().lower() for raw in (words or []) for w in str(raw).split() if w.strip()]
    if not words:
        return "month", None
    kind = words[0]
    if kind not in ("month", "week"):
        raise ReportInput("say which report: `report month` (or `report month 2026-09`); the weekly one comes later")
    rest = words[1:]
    if kind == "week":
        return "week", rest[0] if rest else None
    if len(rest) > 1:
        raise ReportInput("one month only, written YYYY-MM (for example 2026-09)")
    if rest:
        mdl.month_bounds(rest[0])           # raises ValueError in one sentence
    return "month", rest[0] if rest else None


def default_month(data_to: date) -> str:
    """The last whole month before the data date's own month."""
    first = data_to.replace(day=1)
    prev = date(first.year - (first.month == 1), (first.month - 2) % 12 + 1, 1)
    return f"{prev:%Y-%m}"


# ── reads (all read-only) ──────────────────────────────────────────────────────────────────────

class Source:
    """Where the report's data comes from: production, read-only. Tests hand build_month a stand-in."""

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


def ready(month: str, data_to: date | None, loaded_at) -> dict:
    """The plan's whole-month gate, fed with the last load's time and MAX(sale_date)."""
    from scripts.ai_head.pack import monthly_ready
    sources = [{"source": "focus_upload", "loaded_at": loaded_at},
               {"source": "focus_sales", "as_of": data_to.isoformat() if data_to else None}]
    return monthly_ready(sources, month)


def last_load_time(state: dict, anchors: dict):
    last = state.get("last_load") if isinstance(state.get("last_load"), dict) else {}
    when = last.get("finished_at") if last else None
    if when:
        return when, "the last load on record"
    if anchors.get("uploaded_at"):
        return anchors["uploaded_at"], "the newest successful upload in the database"
    return None, "no load on record"


# ── the build ──────────────────────────────────────────────────────────────────────────────────

def target_path(period: mdl.Period, draft: bool):
    base = REPORT_NAME.format(label=period.label) + (" - DRAFT" if draft else "")
    folder = paths.reports_dir("monthly")
    for k in range(1, 100):
        name = base + (f" ({k})" if k > 1 else "") + ".xlsx"
        p = folder / name
        if not p.exists():
            return p
    raise FileExistsError(f"ninety-nine copies of {base} already exist in {folder}")


def control_values(book) -> dict:
    """The Notes sheet's checks, as evaluated: every difference must be nil."""
    notes = book.get("Notes")
    out: dict[str, list] = {"export": [], "sheets": [], "unmapped": [], "db": [], "narration": None}
    for (r, c), cell in notes.cells.items():
        if cell.kind == "s" and cell.value in CONTROL_LABELS:
            out["export"].append((cell.value, notes.cells[(r, 6)].value))
        if cell.kind == "s" and cell.value in ("Salesmen", "Categories", "Products", "Customers", "Daily") and c == 1:
            out["sheets"].append((cell.value, notes.cells[(r, 2)].value, notes.cells[(r, 3)].value))
        if cell.kind == "s" and cell.value == "Day book lines with no category":
            out["unmapped"] = [notes.cells[(r, 2)].value, notes.cells[(r, 3)].value]
        if cell.kind == "s" and cell.value == "Day book (Data sheet) less v_sales":
            out["db"] = [notes.cells[(r, 2)].value, notes.cells[(r, 3)].value]
        if cell.kind == "s" and cell.value == "Narrations on the Data sheet with an ID number":
            out["narration"] = notes.cells[(r, 4)].value
    return out


def build_month(month: str, source: Source, *, prepared_on: date, prepared_by: str = "",
                category_names: dict | None = None, commentary: sheets_core.Commentary | None = None):
    """-> (book, model, facts). Reads through `source`; writes nothing."""
    period = mdl.Period.month(month)
    rows = source.lines(period.prev_start, period.end)
    totals = source.totals(period.prev_start, period.start, period.end)
    try:
        upcoming = source.upcoming()
        upcoming_note = None
    except Exception as exc:  # noqa: BLE001  (without the cards an unknown code reads Other; the screen says so)
        upcoming, upcoming_note = {}, f"the Upcoming cards could not be read ({type(exc).__name__})"
    model = mdl.build(period, rows, category_names=category_names, upcoming=upcoming)
    if upcoming_note:
        model.warnings.append(upcoming_note)
    facts = sheets_core.Facts(totals=totals, prepared_on=prepared_on, prepared_by=prepared_by)
    book = sheets_core.build(model, facts, commentary)
    errors = book.evaluate()
    if errors:
        model.warnings.append(f"{len(errors)} formula(s) came out as an error value: " + "; ".join(errors[:3]))
    return book, model, facts


def _money(x) -> str:
    return fmt.bhd(x, 2)


def report_text(model: mdl.Model, book, path, checks: dict, ready_why: str, data_to: date | None,
                loaded: str, compare_lines: list[str] | None) -> tuple[list[str], bool]:
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
                 f"{p.prev_short} {_money(prev_net)}, {p.short} {_money(cur_net)} BHD.")
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
    lines.append("DRAFT: the ten comment lines (five Highlights, five Points to note) and three notes beside the tables "
                 "are placeholders; the approval step arrives in a later phase.")
    for w in model.warnings:
        lines.append(f"Note: {w}.")
    if compare_lines is not None:
        lines.append("")
        lines.append("Compared with the reference workbook:")
        lines.extend("  " + ln for ln in compare_lines)
    return lines, ok


def run(args) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    try:
        kind, month = parse_spec(getattr(args, "spec", None))
        prepared_on = date.fromisoformat(args.prepared_on) if getattr(args, "prepared_on", None) else status.today_bahrain()
    except (ReportInput, ValueError) as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    if kind == "week":
        return dict(out, ok=False, text="The weekly report (/yq-week) arrives in a later phase.", exit=1)
    source = Source()
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
        text = (f"The {mdl.Period.month(month).long} report is not built: the month is not whole yet. "
                f"{gate['why'][0].upper() + gate['why'][1:]} (load time from {loaded_from}).")
        return dict(out, ok=False, text=text, flags={"ready": False, "month": month}, exit=2)
    prepared_by = args.prepared_by if getattr(args, "prepared_by", None) is not None else config.get("report.prepared_by")
    names = config.get("report.category_names")
    book, model, _facts = build_month(month, source, prepared_on=prepared_on, prepared_by=prepared_by,
                                      category_names=names)
    path = target_path(model.period, draft=True)
    book.save(path)
    checks = control_values(book)
    compare_lines = None
    flags = {"ready": True, "month": month, "draft": True, "lines": len(model.lines)}
    if getattr(args, "compare", None):
        res = golden.compare(path, args.compare, sheets=[s.name for s in book.sheets if s.name != "Notes"],
                             set_aside=book.commentary)
        compare_lines = golden.summary(res)
        flags["golden_ok"] = res.ok
    loaded = f"loaded {str(loaded_at)[:16].replace('T', ' ')}" if loaded_at else "no load time on record"
    text_lines, ok = report_text(model, book, path, checks, gate["why"], data_to, loaded, compare_lines)
    record = {"month": month, "data_date": data_to.isoformat(), "file": str(path), "built_on": prepared_on.isoformat(),
              "draft": True, "checks_ok": ok, "lines": len(model.lines),
              "golden_ok": flags.get("golden_ok")}
    try:
        rec = paths.write_text(paths.exports_dir(data_to) / f"report-month-{month}.json",
                               json.dumps(record, indent=1) + "\n")
        out["files"].append(str(rec))
    except OSError as exc:
        out["warnings"].append(f"the build record was not written ({type(exc).__name__})")
    out["files"].insert(0, str(path))
    return dict(out, ok=ok, text="\n".join(text_lines), flags=flags, exit=0 if ok else 2)


def safe_cell(ref: str) -> str:
    """Normalise a typed cell reference (for messages)."""
    m = re.fullmatch(r"\$?([A-Za-z]{1,3})\$?(\d+)", ref.strip())
    return f"{m.group(1).upper()}{m.group(2)}" if m else ref


__all__ = ["add_arguments", "run", "build_month", "Source", "default_month", "parse_spec", "a1"]
