"""python -m scripts.ops recon [prep [full] | count [<file>] | YYYY-MM]     (behind /yq-month prep and /yq-month count)

    prep          the blind count sheets (scripts.ops.counts lists them; scripts.ops.workbook.count_book lays them
                  out): one sheet per van and per outlet, the main store's cycle list and a Notes sheet, in ONE
                  workbook under exports/reports/counts/ ("YQ Count Sheets <prep date> cycle.xlsx", the next free
                  " (2)" ... when it exists: never over a file), and what it lists in
                  business_data/ops/counts/<prep id>/prep.json. --date YYYY-MM-DD: the prep date (default today,
                  Bahrain); it may not be before the stock ledger's last day.
    prep full     the same with every item: every store lists what it holds or moved there in a year, at nought on
                  the books too (once a quarter)
    count [<file>]  a copy with the counts typed in, read back (the file as saved, or its name in
                  exports/reports/counts; with none, the newest there): checked, set beside the books (the ledger at
                  the end of the day before the Count date, once that day is complete in the ledger read: a set's
                  export day is a part day, see export_day), valued at landed cost, and kept per store in
                  business_data/ops/counts/<YYYY-MM>/<prep id>/<store>.json. The screen gives each store's totals
                  (lines counted, units short and over, BHD); the item differences go to exports/ops/<data date>/
                  only (recon-count-<the typed file's name>.txt and .json, one pair per file read);
                  the Notes typed on the sheets are never printed. A file with a problem keeps nothing: each problem
                  is one sentence, to fix in the file before reading it again.
    [YYYY-MM]     the four month-end reconciliations: they come with the month-end release (scripts.ops.recon_blocks,
                  handed run(args, month) once it exists; until then one line, exit 1). --ledger-file PATH names the
                  month's Focus Ledger export (args.ledger_file; absent, None: the drop folders are searched); it is
                  not --ledger, which says where the STOCK ledger is read

Reads: the stock ledger (ledger.for_audit: the loaded file when it equals what production holds, else the database;
--ledger file | db to choose); for prep, /yq-stock's own model (scripts.ops.stock gather and build: the accessories,
their codes, the fast movers, the units on hand and their unit costs); for count, the unit costs through /yq-stock's
cost seam (unit_costs: without it the BHD columns are left out, with one line). All read-only.

A code worktree makes no count sheets and keeps no counts unless --data-root names another folder: they belong to
the project folder (as /yq-stock's day file does).
"""
from __future__ import annotations

import importlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from scripts.ops import db  # noqa: F401  (first: it blanks the side channels before any app import)
from scripts.ops import counts, fmt, ledger, paths, status
from scripts.ops.workbook import count_book

COMMAND = "recon"
BLOCKS_MODULE = "scripts.ops.recon_blocks"      # the month-end reconciliations (the month-end release)
MOVES_MODULE = "scripts.ops.moves"              # /yq-moves: the items on a movement exception of a month
COST_SEAM = ("cost_reads", "accessory_table", "apply_costs")     # scripts.ops.stock: a unit's cost on its basis
ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
NO_BLOCKS = "The four month-end reconciliations come with the month-end release."
NO_MOVES = ("The items on a movement exception of the month are left out of the main store's list: they join it once "
            "/yq-moves can give them.")
NO_COSTS = "BHD: not worked out this time: /yq-stock's cost seam (cost_reads, accessory_table, apply_costs) is not there."
USAGE = "Say what to do: recon prep (or prep full), recon count <file>, or recon <YYYY-MM> for the reconciliations."
WIDTH = 34                                      # a store's name on the screen


def add_arguments(parser) -> None:
    parser.add_argument("spec", nargs="*", metavar="word", help="prep [full], count [<file>], or a month YYYY-MM")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="prep: the prep date (default today, Bahrain)")
    parser.add_argument("--ledger", choices=ledger.PREFER, default="auto",
                        help="where the stock ledger is read: auto (the file when it equals production), file, db")
    parser.add_argument("--ledger-file", default=None, metavar="PATH",
                        help="recon YYYY-MM: the month-end Focus Ledger export to read (absent: the reconciliations "
                             "look for it in the drop folders); args.ledger_file to scripts.ops.recon_blocks")


def parse_spec(words) -> tuple[str, str | None]:
    """('prep', 'cycle' | 'full'), ('count', the file as typed or None) or ('month', 'YYYY-MM' or None).
    CountInput (one sentence) for anything else."""
    tokens = [str(w).strip() for w in (words or []) if str(w).strip()]
    if not tokens:
        return "month", None
    first = tokens[0].split()
    head, rest = first[0].lower(), first[1:] + tokens[1:]
    if head == "prep":
        more = [w.lower() for t in rest for w in t.split()]
        if more in ([], ["cycle"]):
            return "prep", "cycle"
        if more == ["full"]:
            return "prep", "full"
        raise counts.CountInput("prep takes one word at most: full (every item), for example recon prep full.")
    if head == "count":
        return "count", " ".join(rest) or None
    if len(tokens) == 1 and counts.MONTH.fullmatch(tokens[0]):
        return "month", tokens[0]
    raise counts.CountInput(USAGE)


def _guarded(line: str) -> str:
    """Every line shown passes the wording guard (fmt.careful); a word from the data that it refuses is blanked."""
    try:
        return fmt.careful(line)
    except ValueError:
        out = []
        for part in re.split(r"(\s+)", line):
            try:
                out.append(fmt.careful(part))
            except ValueError:
                out.append("-" * len(part))
        return "".join(out)


def _short(text, width: int = WIDTH) -> str:
    text = counts.clean(text)
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def _shown(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(paths.data_root().resolve()))
    except ValueError:
        return str(path)


def _worktree_refusal() -> str | None:
    if paths.is_worktree() and not paths.root_is_set():
        return ("This is a code worktree: the count sheets and the counts belong to the project folder (to try them, "
                "name another folder with --data-root).")
    return None


def _save(d: date, stem: str, text: str, facts: dict) -> list[str]:
    folder = paths.exports_dir(d)
    return [str(paths.write_text(folder / f"{stem}.txt", text + "\n")),
            str(paths.write_text(folder / f"{stem}.json", json.dumps(counts.jsonable(facts), indent=1,
                                                                      ensure_ascii=False) + "\n"))]


# ── the hooks: /yq-moves and the month-end reconciliations ────────────────────

def movement_exception_items(first: date, last: date) -> tuple[list | None, str | None]:
    """(the ledger names of the items on a movement exception from first to last, None) from /yq-moves
    (scripts.ops.moves.exception_items(first, last)), or (None, the note line) while it cannot give them."""
    try:
        moves = importlib.import_module(MOVES_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == MOVES_MODULE:
            return None, NO_MOVES
        return None, f"{NO_MOVES[:-1]} (it could not be loaded: {type(exc).__name__})."
    except Exception as exc:  # noqa: BLE001  (an unfinished /yq-moves never stops the count sheets)
        return None, f"{NO_MOVES[:-1]} (it could not be loaded: {type(exc).__name__})."
    give = getattr(moves, "exception_items", None)
    if not callable(give):
        return None, NO_MOVES
    try:
        return [str(x) for x in give(first, last)], None
    except Exception as exc:  # noqa: BLE001
        return None, ("The items on a movement exception of the month are left out of the main store's list: /yq-moves "
                      f"could not give them ({type(exc).__name__}).")


def month_view(args, month: str | None) -> dict:
    """recon [YYYY-MM]: handed to scripts.ops.recon_blocks.run(args, month) once that module exists."""
    try:
        blocks = importlib.import_module(BLOCKS_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != BLOCKS_MODULE:
            raise
        return {"ok": False, "command": COMMAND, "data_date": None, "text": NO_BLOCKS, "flags": {"month": month},
                "files": [], "warnings": [], "exit": 1}
    return blocks.run(args, month)


# ── prep ──────────────────────────────────────────────────────────────────────

def picks_of(model) -> counts.StockPicks:
    """What the cycle list takes from /yq-stock's Model: the accessories with their codes and names, the fast movers
    (facts["fast"]["list"]), each held item's units on hand x unit cost."""
    items = {it.ledger_name: (it.code, it.name) for it in model.items}
    fast = [it.ledger_name for it in model.facts["fast"]["list"]]
    value = {it.ledger_name: it.on_hand * it.cost for it in model.items if it.on_hand > 0 and it.cost is not None}
    uncosted = sum(1 for it in model.items if it.on_hand > 0 and it.cost is None)
    return counts.StockPicks(items=items, fast=fast, fast_n=int(model.cfg.fast_n), value=value, uncosted_held=uncosted)


def _prep_date(args) -> date:
    text = getattr(args, "date", None)
    if not text:
        return status.today_bahrain()
    day = None
    if ISO_DAY.fullmatch(str(text).strip()):
        try:
            day = date.fromisoformat(str(text).strip())
        except ValueError:
            day = None
    if day is None:
        raise counts.CountInput(f"The prep date is written YYYY-MM-DD: {text!r} is not one.")
    return day


def prep_lines(prep: counts.Prep, titles: list[str], target: Path, ledger_note: str) -> list[str]:
    window = counts.FULL_DAYS if prep.kind == "full" else counts.HELD_DAYS
    by_kind = {k: [s for s in prep.sheets if s.kind == k] for k in ("van", "outlet", "main")}
    n = {k: sum(len(s.lines) for s in v) for k, v in by_kind.items()}
    kinds = []
    for k, words in (("van", "vans"), ("outlet", "outlets"), ("main", "the main store")):
        if by_kind[k]:
            kinds.append(f"{words} {fmt.units(n[k])}")
    lines = [f"COUNT SHEETS  {_shown(target)} (made now)",
             f"  {'A full count' if prep.kind == 'full' else 'A cycle count'}, prepared on {fmt.day_long(prep.prep_date)} "
             f"from the stock ledger to {fmt.day_long(prep.data_date)}. Blind: no sheet shows a book quantity.",
             f"  {len(prep.sheets)} sheets, {fmt.units(prep.lines)} lines to count ({', '.join(kinds)}):"]
    grid = [[title, _short(s.store), fmt.units(len(s.lines))] for title, s in zip(titles, prep.sheets)]
    lines += ["    " + ln for ln in fmt.grid(["Sheet", "Store", "Lines"], grid, "llr").splitlines()]
    lines.append(f"  Vans and outlets: every accessory held there at the end of {fmt.day_long(prep.data_date)}, or moved "
                 f"there in the {window} days to it.")
    if by_kind["main"]:
        parts = prep.main_parts
        if prep.kind == "full":
            lines.append(f"  Main store: every accessory it holds at the end of {fmt.day_long(prep.data_date)}, or moved "
                         f"there in the {window} days to it: {counts.plural(n['main'], 'line')}.")
        else:
            bits = [f"the {parts.get(counts.WHY_FAST, 0)} fast movers",
                    f"the {parts.get(counts.WHY_VALUE, 0)} highest-value items (units on hand at landed cost)"]
            if counts.WHY_EXCEPTION in parts:
                k = parts[counts.WHY_EXCEPTION]
                bits.append(f"the {counts.plural(k, 'item')} on a movement exception of the month" if k
                            else "no items on a movement exception of the month")
            both = sum(1 for s in by_kind["main"] for ln in s.lines if len(ln.why) > 1)
            lines.append(f"  Main store: {', '.join(bits[:-1])} and {bits[-1]}, each once: {fmt.units(n['main'])} lines"
                         + (f" ({both} of them for more than one reason)." if both else "."))
    for note in prep.notes:
        lines.append(f"  Note: {note}")
    if prep.skipped:
        lines.append(f"  No sheet: {counts.plural(len(prep.skipped), 'store')} held nothing and moved nothing in the "
                     f"{window} days: " + ", ".join(_short(s) for s in prep.skipped) + ".")
    lines.append("  Next: print the sheets, count, type the counts into this file and save it; then /yq-month count "
                 "with the file's name.")
    lines.append(f"  {ledger_note}")
    return lines


def prep_view(args, kind: str) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    refusal = _worktree_refusal()
    if refusal:
        return dict(out, ok=False, text=refusal, exit=1)
    from scripts.ops import stock
    prep_date = _prep_date(args)
    cfg = stock.settings()
    inp = stock.gather(getattr(args, "ledger", "auto") or "auto")
    try:
        model = stock.build(inp, cfg)
    except stock.StockInput as exc:
        raise counts.CountInput(str(exc)) from None
    d = model.d
    if prep_date < d:
        raise counts.CountInput(f"The prep date ({fmt.day_long(prep_date)}) is before the stock ledger's last day "
                                f"({fmt.day_long(d)}): the sheets list the stock as on that day.")
    exceptions, note = (None, None)
    if kind == "cycle":
        exceptions, note = movement_exception_items(d.replace(day=1), d)
    prep = counts.plan(inp.rows, counts.store_rules(), picks_of(model), kind, prep_date,
                       counts.new_prep_id(prep_date, kind), exceptions=exceptions, exceptions_note=note)
    if not prep.sheets:
        raise counts.CountInput("Nothing to count: no van, outlet or main-store item to list.")
    book = count_book.build(prep)
    target = count_book.target_path(paths.reports_dir("counts"), prep_date, kind)
    if not paths.allowed_output(target):             # the workbook goes under exports/ of the data root, nowhere else
        raise PermissionError(f"scripts/ops writes only under business_data/ and exports/: {target}")
    record = counts.prep_json(prep, target.name, inp.ledger_note)
    files = [str(paths.write_text(counts.prep_file(prep.prep_id), json.dumps(record, indent=1, ensure_ascii=False)
                                  + "\n"))]
    book.save(target)
    files.insert(0, str(target))
    titles = count_book.sheet_titles(prep)
    text = "\n".join(_guarded(ln).rstrip() for ln in prep_lines(prep, titles, target, inp.ledger_note))
    facts = {"prep_id": prep.prep_id, "kind": prep.kind, "prep_date": prep.prep_date, "data_date": d,
             "file": target.name, "sheets": [{"sheet": t, "store": s.store, "kind": s.kind, "lines": len(s.lines)}
                                             for t, s in zip(titles, prep.sheets)],
             "skipped": prep.skipped, "main_parts": prep.main_parts, "notes": prep.notes}
    files += _save(d, "recon-prep", text, facts)
    warnings = list(inp.warnings) + [f"{u} could not be read" for u in inp.unread]
    return dict(out, data_date=d.isoformat(), text=text, files=files, warnings=warnings,
                flags={"view": "prep", "kind": kind, "prep_id": prep.prep_id, "sheets": len(prep.sheets),
                       "lines": prep.lines, "file": str(target)})


# ── count ─────────────────────────────────────────────────────────────────────

def unit_costs(rows) -> tuple[dict | None, str | None]:
    """({ledger name: unit cost, None for an item with none}, a note or None) on /yq-stock's basis, through its cost
    seam: stock.cost_reads() for the reads, stock.accessory_table(rows, price_book, snapshot) for the items and
    stock.apply_costs(items, mrn, sheet) for their costs. (None, the note) when the seam is not there or fails: the
    BHD figures are then left out, never valued at nought."""
    try:
        from scripts.ops import stock
    except Exception as exc:  # noqa: BLE001
        return None, f"BHD: not worked out this time: /yq-stock could not be loaded ({type(exc).__name__})."
    seam = [getattr(stock, name, None) for name in COST_SEAM]
    if not all(callable(fn) for fn in seam):
        return None, NO_COSTS
    reads, table, apply = seam
    try:
        got = reads()
        made = table(rows, got.get("price_book"), got.get("snapshot") or [])
        items_ = made[0] if isinstance(made, tuple) else made
        apply(items_, got.get("mrn"), got.get("sheet"))
        costs = {name: it.cost for name, it in items_.items()}
    except Exception as exc:  # noqa: BLE001  (the BHD figures are one part of the answer, never the answer)
        return None, f"BHD: not worked out this time: the unit costs could not be read ({type(exc).__name__})."
    unread = [str(u) for u in got.get("unread") or []]
    note = (f"Costs: {'; '.join(unread)} could not be read: the items it would cost take the next basis."
            if unread else None)
    return costs, note


FOLDER_DAY = re.compile(r"Focus ERP ([0-9]{4}-[0-9]{2}-[0-9]{2})")


def _folder_day(name) -> date | None:
    m = FOLDER_DAY.fullmatch(counts.clean(name))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def export_day(led) -> date | None:
    """The day the stock ledger read was exported, as far as anything on record says (as sales.export_day): for the
    file, the 'Focus ERP <date>' folder it was read from (a set is published under its reports' own as-on date, the
    day they were exported); for the database, the folder the last load on record published, else the day its drop
    folder is named for. None when nothing says, or when what it says is before the ledger's last day (the data then
    came another way): the book side then takes the ledger's last day as a part day."""
    day = None
    if led.origin in ("file", "cache"):
        folder = ledger.loaded_folder()
        day = _folder_day(folder.name) if folder is not None else None
    else:
        last = status.read_state().get("last_load")
        if isinstance(last, dict):
            day = _folder_day(last.get("published")) or paths.drop_date(str(last.get("run") or "")[:6])
    if day is None or led.last_date is None or day < led.last_date:
        return None
    return day


def _file_stem(name: str) -> str:
    """The typed file's own name as a part of a file name: 'YQ Count Sheets 2026-10-27 cycle (2).xlsx' ->
    'yq-count-sheets-2026-10-27-cycle-2'."""
    return re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")[:60].strip("-") or "file"


def resolve_file(text: str | None) -> Path:
    folder = paths.reports_dir("counts")
    if not text:
        found = count_book.newest(folder)
        if found is None:
            raise counts.CountInput("No count-sheet workbook is in exports/reports/counts: make them with recon prep, or "
                                    "name the file.")
        return found
    raw = text.strip().strip('"').strip("'")
    for cand in (Path(raw), folder / raw, folder / f"{raw}.xlsx"):
        if cand.exists():                          # a folder too: the reader says it is one
            return cand
    raise counts.CountInput(f"{Path(raw).name} is not there: name the file as it was saved (with its folder when it "
                            "is not exports/reports/counts).")


def _n(x) -> str:
    return "-" if x is None else fmt.units(x)


def count_lines(read, checked: counts.Checked, results: list[dict], ledger_note: str, cost_note: str | None,
                diff_file: str | None, kept: str) -> list[str]:
    saved = (read.saved_at or "?")[:16].replace("T", " ")
    lines = [f"COUNTS READ  {read.name} (last saved {saved}): {counts.plural(len(results), 'sheet')} counted"
             + (f", {len(checked.untouched)} not counted yet" if checked.untouched else "") + "."]
    if read.open_in_excel:
        lines.append("  The file is open in Excel: this is its last save. Save it first if you typed more since.")
    costed = all(r["costed"] for r in results)
    lines.append("  Book side: the stock ledger at the end of the day before each Count date."
                 + (" BHD at landed cost, as /yq-stock values stock." if costed else ""))
    heads = ["Store", "Counted on", "Lines counted", "Units short", "Units over"]
    if costed:
        heads += ["BHD short", "BHD over"]
    grid = []
    for r in results:
        t = r["totals"]
        lines_ = f"{t['counted']} of {t['lines']}" + (f" +{t['added']} added" if t["added"] else "")
        row = [_short(r["store"], 28), fmt.day(r["count_date"]), lines_]
        if r["book"] == "waits":
            row += ["waits", "waits"] + (["waits", "waits"] if costed else [])
        else:
            row += [_n(Decimal(t["short_units"])), _n(Decimal(t["over_units"]))]
            if costed:
                row += [fmt.bhd(t["short_bhd"]), fmt.bhd(t["over_bhd"])]
        grid.append(row)
    lines += ["    " + ln for ln in fmt.grid(heads, grid, "llr" + "r" * (len(heads) - 3)).splitlines()]
    not_loaded = []
    for r in results:
        t = r["totals"]
        if r["book"] == "waits":
            lines.append(f"  {_short(r['store'])}: {r['waits_words']}.")
            continue
        if t["moved_on_count_day"]:
            lines.append(f"  {_short(r['store'])}: {counts.plural(t['moved_on_count_day'], 'item')} moved in that store "
                         "on the count day: a possible cause of a difference (the vouchers are in the file below).")
        if not r["count_day_loaded"]:
            not_loaded.append(_short(r["store"]))
        if costed and t["uncosted_differences"]:
            lines.append(f"  {_short(r['store'])}: {counts.plural(t['uncosted_differences'], 'difference')} with no "
                         "cost: their BHD is left out.")
    if not_loaded:
        lines.append("  The count day is not complete in the loaded ledger yet, so its vouchers are not all checked: "
                     + ", ".join(not_loaded) + ".")
    if cost_note:
        lines.append(f"  {cost_note}")
    if checked.untouched:
        lines.append("  Not counted yet (nothing typed): " + ", ".join(checked.untouched) + ".")
    if diff_file:
        lines.append(f"  Item differences: {diff_file}")
    lines.append(f"  Kept: {kept}")
    lines.append(f"  {ledger_note}")
    return lines


def differences_text(read, results: list[dict]) -> str:
    """The item differences (exports/ops/<data date>/ only): every counted line whose count differs from the book,
    with the vouchers of its store on the count day. No Note typed on a sheet, no name."""
    out = [f"COUNT DIFFERENCES  {read.name}"]
    for r in results:
        out.append("")
        head = f"{r['store']} (sheet {r['sheet']}), counted on {fmt.day_long(r['count_date'])}"
        if r["book"] == "waits":
            out.append(f"{head}: {r['waits_words']}.")
            continue
        out.append(f"{head}; the book side at the end of {fmt.day_long(r['book_day'])}:")
        rows = []
        same = not_counted = 0
        for x in r["rows"]:
            if x["counted"] is None:
                not_counted += 1
                continue
            diff = Decimal(x["diff"])
            if diff == 0:
                same += 1
                continue
            rows.append([x["code"], _short(x["item"], 44), _n(Decimal(x["book"])), _n(x["counted"]),
                         f"{'+' if diff > 0 else ''}{fmt.units(diff)}",
                         "-" if x["bhd"] is None else fmt.bhd(x["bhd"]),
                         ", ".join(x["count_day_vouchers"][:3]) + (" ..." if len(x["count_day_vouchers"]) > 3 else ""),
                         "added" if x["added"] else ""])
        if rows:
            out += ["  " + ln for ln in fmt.grid(["Code", "Item", "Book", "Counted", "Difference", "BHD",
                                                  "Moved on the count day", ""], rows, "llrrrrll").splitlines()]
        agree = counts.plural(same, "line") + (" agrees" if same == 1 else " agree")
        out.append(f"  {agree} with the book" + (f"; {not_counted} not counted" if not_counted else "")
                   + ("." if rows else "; no difference."))
    return "\n".join(_guarded(ln).rstrip() for ln in out)


def count_view(args, file_text: str | None) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    refusal = _worktree_refusal()
    if refusal:
        return dict(out, ok=False, text=refusal, exit=1)
    path = resolve_file(file_text)
    try:
        read = count_book.read_file(path)
    except count_book.CountFileError as exc:
        raise counts.CountInput(str(exc)) from None
    checked = counts.check(read)
    if checked.problems:
        lines = [f"COUNTS NOT READ  {read.name}: nothing was kept. Fix these in the file, save it, and read it again:"]
        lines += [f"  - {p}" for p in checked.problems]
        return dict(out, ok=False, text="\n".join(_guarded(ln).rstrip() for ln in lines), exit=2,
                    flags={"view": "count", "problems": len(checked.problems)})
    if not checked.sheets:
        if not checked.untouched:
            raise counts.CountInput(f"{read.name} holds no count sheet this engine reads (no sheet carries the layout tag "
                                    f"{counts.TAG}): make them with recon prep.")
        return dict(out, ok=False, exit=2, flags={"view": "count", "sheets": 0},
                    text=f"Nothing is typed in {read.name} yet: no sheet has a Count date, a name or a count.")
    led, info = ledger.for_audit(getattr(args, "ledger", "auto") or "auto")
    if led.cache_note:
        out["warnings"].append(led.cache_note)
    books = counts.Books(led.rows, counts.store_rules(), export_day(led))
    costs, cost_note = unit_costs(led.rows)
    read_at = datetime.now(status.BAHRAIN).replace(microsecond=0).isoformat()
    meta = {"file": read.name, "sha256": read.sha256, "saved_at": read.saved_at, "read_at": read_at,
            "ledger_source": led.source, "ledger_last_date": led.last_date, "ledger_note": info["note"]}
    results = [counts.compare(s, books, costs, meta) for s in checked.sheets]
    d = led.last_date or status.today_bahrain()
    kept_dirs, files = set(), []
    for r in results:
        f = counts.result_file(r["month"], r["prep_id"], r["slug"])
        files.append(str(paths.write_text(f, json.dumps(r, indent=1, ensure_ascii=False) + "\n")))
        kept_dirs.add(_shown(f.parent))
    diff_text = differences_text(read, results)
    facts = {"file": read.name, "sha256": read.sha256, "data_date": d, "stores": [
        {k: v for k, v in r.items() if k not in ("counted_by", "checked_by", "sheet_notes", "rows")}
        | {"rows": [{k: v for k, v in x.items() if k != "note"} for x in r["rows"]]} for r in results],
        "untouched": checked.untouched}
    # one pair of files per typed file read (the next file read on the same data date never replaces it)
    saved = _save(d, f"recon-count-{_file_stem(read.name)}", diff_text, facts)
    kept = ", ".join(sorted(kept_dirs)) + f" ({len(results)} {'store' if len(results) == 1 else 'stores'})"
    lines = count_lines(read, checked, results, info["note"], cost_note, _shown(Path(saved[0])), kept)
    text = "\n".join(_guarded(ln).rstrip() for ln in lines)
    return dict(out, data_date=d.isoformat(), text=text, files=files + saved,
                flags={"view": "count", "sheets": len(results), "untouched": len(checked.untouched),
                       "waits": sum(1 for r in results if r["book"] == "waits"), "costed": costs is not None})


def run(args) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    try:
        view, rest = parse_spec(getattr(args, "spec", None))
        if view == "month":
            return month_view(args, rest)
        if view == "prep":
            return prep_view(args, rest)
        return count_view(args, rest)
    except counts.CountInput as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
