"""The blind stock-count sheets: what `recon prep` lists, and what `recon count` makes of a copy typed in.

    python -m scripts.ops recon prep [full]          (scripts.ops.recon runs it; the workbook is
    python -m scripts.ops recon count <file>          scripts.ops.workbook.count_book)

Nothing here reads the database or prints: the caller hands in the ledger's rows, the stock facts and the unit
costs, and gets the lists, the checks and the results back. Files are written by the caller, through paths.

WHAT A SHEET LISTS (plan(); D is the stock ledger's last day)
  * One sheet per van and per outlet (pairing.StoreRules.kind, with config's main store, damage store, stores not
    counted and aliases): every accessory whose ledger balance there is not nought at the end of D, and every
    accessory that moved there (a ledger row of that store's own) in the HELD_DAYS days to D. Full: FULL_DAYS days.
    A van or an outlet with nothing to list gets no sheet; it is named in the notes.
  * The main store's cycle list: the fast movers of /yq-stock (its own facts: config 'stock.fast_movers' best
    sellers by units over the twelve full months before the month), the TOP_VALUE highest-value items (units on
    hand at the end of D x the unit cost /yq-stock values them at; an item with no cost is not ranked, and
    counted in a note), and every item on a movement exception of the month when the caller can give them (until
    /yq-moves gives them they are left out, and one note line says so). Full: every item whose main-store balance
    is not nought.
  * Accessories only: the items /yq-stock plans (ever moved in the main store, less config 'items.not_accessories').
    An item held or moved in a van or an outlet that never moved in the main store is on no sheet; a note counts
    them. Rows are in the order of their code.
  * BLIND: no book quantity is written anywhere, not on a sheet, not in a hidden cell, not in prep.json. The book
    side is worked out only when a typed copy is read back.
  * Every item has one code on the sheets: its Focus item code (the price book's, else the rest of its ledger name
    after the stock name), and where an item has none, or shares it with another item, its ledger name.

THE TAG AND THE CHECKSUM
  Each sheet carries, in a hidden row, the layout tag TAG ('count-sheet v1'), the prep id and its checksum:
  sha256 of 'tag|prep id|store|the codes listed, sorted', written in letters (the digits as g to p, as the engine
  writes every hash, so that it never reads as a number), the first 16. On read-back the checksum is
  recomputed over the listed codes found in the file, so a sorted sheet passes and a deleted row is named.

THE READ-BACK (check(), then compare())
  A sheet with nothing typed (no Count date, no name, no count, no added row) is not counted yet and passed over.
  A typed sheet is refused, one plain sentence per problem and nothing kept for the whole file, when: its tag is
  not TAG; its prep is not recorded here (business_data/ops/counts/<prep id>/prep.json); its Store is not one of
  that prep's stores; the checksum does not match (a listed row deleted: named); a code is on two rows; the Count
  date is empty, not a date, in the future or before the sheets were prepared; Counted by is empty; a Counted cell
  holds a formula, text, a number below nought or not whole; a row has a count and no code, a code and no count
  (an added row), an item name only, or a code that is not an accessory's. A blank Counted cell is not counted.
  A Count date typed as the day's number (a cell Excel left in General format) counts as a date when it falls in
  the range the cell accepts (the prep date to a year after).

THE BOOK SIDE (compare())
  ledger.balances(rows, as_of = count date - 1)[(store, item)]: the stock at the end of the day before the count.
  When the ledger stops before that day the result is kept with book None and "the book side waits for a load with
  data to <day>". Vouchers in that store on the count day are named as a possible cause of a difference (when the
  ledger holds that day). Differences are counted - book, in units and, with a unit cost, BHD to the fils.

THE FILES (under business_data/ops/counts/, git-ignored)
  <prep id>/prep.json                      what prep listed (prep_json): per store its rows (code, item, ledger
                                           name) and checksum, and the code table of every accessory
  <YYYY-MM>/<prep id>/<store slug>.json    one store's result (compare()), the month of the book side's day; a
                                           store read again replaces its file (the newest read wins), and across
                                           preps month_results() keeps the newest count of each store
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from scripts.ops import config, fmt, ledger, loadplan, pairing, paths, status

TAG = "count-sheet v1"                  # the layout this engine writes and reads back
TAG_PREFIX = "count-sheet"              # a cell starting so is a layout tag (another version is refused, not missed)
KINDS = ("cycle", "full")
HELD_DAYS = 30                          # a van or an outlet: what moved there in the 30 days to the ledger's last day
FULL_DAYS = 365                         # prep full: a year
TOP_VALUE = 20                          # the main store's cycle list: the highest-value items
ADD_ROWS = 10                           # empty rows under the list, for an item found that is not listed
DATE_RANGE_DAYS = 365                   # a Count date is accepted from the prep date to a year after
SHEET_KINDS = ("van", "outlet")         # the stores that get a sheet of their own (and the main store's list)
WHY_FAST, WHY_VALUE, WHY_EXCEPTION, WHY_HELD = "fast mover", "high value", "movement exception", "held"
# The labels of a sheet's top block, as the workbook writes them and the read-back finds them (count_book's own).
L_STORE, L_PREPARED, L_DATE, L_BY, L_CHECKED = TOP_LABELS = ("Store", "Prepared on", "Count date", "Counted by",
                                                             "Checked by")
PREP_VERSION = RESULT_VERSION = 1
SHOWN = 5                               # codes named in one sentence, then "and n more"
ZERO = Decimal(0)
PREP_ID = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-(?:cycle|full)-[a-p]{6}")     # hex in letters: a to p
MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")


class CountInput(ValueError):
    """Something the command cannot answer: one plain sentence, printed as it is."""


def today() -> date:
    """Today in Bahrain (a Count date after it is in the future). Tests replace it."""
    return status.today_bahrain()


def fold(text) -> str:
    """A code, a store or a header as it is compared: spaces collapsed, case ignored ('' for nothing)."""
    return "" if text is None else " ".join(str(text).split()).casefold()


def clean(text) -> str:
    """Text as it is kept and shown: spaces collapsed ('' for nothing)."""
    return "" if text is None else " ".join(str(text).split())


def jsonable(x):
    """Facts as JSON holds them: a Decimal as its text (no float ever), a date as YYYY-MM-DD."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    return x


def _q3(x: Decimal) -> Decimal:
    return Decimal(x).quantize(fmt.Q3, rounding=ROUND_HALF_UP)


def plural(n: int, one: str, many: str | None = None) -> str:
    """'1 line', '2 lines' (fmt.units groups a large one)."""
    return f"{fmt.units(n)} {one if n == 1 else (many or one + 's')}"


def _short_list(words: list[str]) -> str:
    shown = ", ".join(words[:SHOWN])
    return shown + (f" and {len(words) - SHOWN} more" if len(words) > SHOWN else "")


# ── where things are ──────────────────────────────────────────────────────────

def counts_dir() -> Path:
    return paths.ops_dir() / "counts"


def prep_file(prep_id: str) -> Path:
    if not PREP_ID.fullmatch(str(prep_id or "")):
        raise ValueError(f"not a prep id: {prep_id!r}")
    return counts_dir() / prep_id / "prep.json"


def result_file(month: str, prep_id: str, slug: str) -> Path:
    if not MONTH.fullmatch(month) or not PREP_ID.fullmatch(prep_id) or not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        raise ValueError("result_file: a month YYYY-MM, a prep id and a store slug")
    return counts_dir() / month / prep_id / f"{slug}.json"


def store_rules() -> pairing.StoreRules:
    """The stores as config names them: the main store, the damage store, the stores never counted, the aliases."""
    return pairing.StoreRules(main=config.get("warehouses.main"), damage=config.get("warehouses.damage") or None,
                              not_counted=tuple(config.get("warehouses.not_counted")),
                              aliases=config.get("warehouses.aliases"))


def new_prep_id(prep_date: date, kind: str) -> str:
    """'<prep date>-<cycle|full>-<six letters>': the letters are random, so two preps of one day differ."""
    if kind not in KINDS:
        raise ValueError(f"the kind of count is one of {', '.join(KINDS)}")
    return f"{prep_date.isoformat()}-{kind}-{loadplan.letters_only(uuid.uuid4().hex)[:6]}"


def checksum(prep_id: str, store: str, codes) -> str:
    """sha256('tag|prep id|store|codes sorted') in letters, the first 16 (see THE TAG AND THE CHECKSUM)."""
    text = "|".join([TAG, str(prep_id), clean(store)] + sorted(clean(c) for c in codes))
    return loadplan.letters_only(hashlib.sha256(text.encode("utf-8")).hexdigest())[:16]


def slug(store: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(store)).strip("-") or "store"


# ── what the sheets list ──────────────────────────────────────────────────────

@dataclass
class StockPicks:
    """What /yq-stock knows that the cycle list needs (scripts.ops.recon takes it from stock's Model):
    items    {ledger name: (code or None, item name)}: the accessories, as /yq-stock names them
    fast     the fast movers' ledger names, in their rank order (stock's facts["fast"]["list"])
    fast_n   how many fast movers /yq-stock lists (config 'stock.fast_movers', or its default)
    value    {ledger name: units on hand in the main store x unit cost}: the held items with a cost
    uncosted_held   the held items with no cost (not ranked by value)"""
    items: dict
    fast: list = field(default_factory=list)
    fast_n: int = 0
    value: dict = field(default_factory=dict)
    uncosted_held: int = 0


@dataclass(frozen=True)
class Line:
    code: str
    item: str
    ledger: str
    why: tuple = ()                  # the main store's cycle list: why the item is on it


@dataclass
class Sheet:
    store: str                       # the store as the ledger names it (one name per store)
    kind: str                        # 'van', 'outlet' or 'main'
    lines: list
    slug: str = ""
    checksum: str = ""


@dataclass
class Prep:
    prep_id: str
    kind: str                        # 'cycle' or 'full'
    prep_date: date
    data_date: date                  # the stock ledger's last day
    sheets: list
    codes: dict                      # {code: {"item", "ledger"}}: every accessory, for an item added on a sheet
    notes: list = field(default_factory=list)
    skipped: list = field(default_factory=list)     # the vans and outlets with nothing to list
    main_parts: dict = field(default_factory=dict)  # the cycle list: how many items for each reason
    not_accessories: int = 0         # items held or moved in the vans and outlets that are not accessories

    @property
    def lines(self) -> int:
        return sum(len(s.lines) for s in self.sheets)


def code_table(items: dict) -> dict[str, tuple[str, str]]:
    """{ledger name: (code as printed, item name)}, every code once: an item with no code, or whose code another
    item shares (spaces and case aside), is listed by its ledger name instead; should two still meet, the later
    ones (in ledger-name order) take ' (2)', ' (3)'."""
    raw = {led: (clean(code), clean(name) or clean(led)) for led, (code, name) in items.items()}
    seen: dict[str, int] = {}
    for code, _name in raw.values():
        if code:
            seen[fold(code)] = seen.get(fold(code), 0) + 1
    out: dict[str, tuple[str, str]] = {}
    taken: set[str] = set()
    for led in sorted(raw):
        code, name = raw[led]
        if not code or seen[fold(code)] > 1:
            code = clean(led)
        shown, n = code, 2
        while fold(shown) in taken:
            shown, n = f"{code} ({n})", n + 1
        taken.add(fold(shown))
        out[led] = (shown, name)
    return out


def _order(lines) -> list[Line]:
    return sorted(lines, key=lambda ln: (fold(ln.code), ln.code, ln.ledger))


def plan(rows, rules: pairing.StoreRules, picks: StockPicks, kind: str, prep_date: date, prep_id: str,
         exceptions: list | None = None, exceptions_note: str | None = None) -> Prep:
    """The sheets of one prep (see WHAT A SHEET LISTS). `rows` are the WHOLE stock ledger; `exceptions` the ledger
    names of the items on a movement exception of the month (None: /yq-moves could not give them, and
    `exceptions_note` says so). Nothing is written."""
    if kind not in KINDS:
        raise ValueError(f"the kind of count is one of {', '.join(KINDS)}")
    rows = rows if isinstance(rows, (list, tuple)) else list(rows)
    d = ledger.last_date(rows)
    if d is None:
        raise CountInput("No stock ledger is loaded: there is nothing to count.")
    table = code_table(picks.items)
    accessories = set(table)
    names = ledger.warehouse_names(rows)
    since = d - timedelta(days=(FULL_DAYS if kind == "full" else HELD_DAYS) - 1)
    held: dict[str, set] = {}
    for (store, item), units in ledger.balances(rows).items():
        if units != 0:
            held.setdefault(store, set()).add(item)
    moved: dict[str, set] = {}
    for r in rows:
        if r["date"] >= since:
            moved.setdefault(names[r["wh"]], set()).add(r["item"])

    def line(led: str, why: tuple = ()) -> Line:
        code, name = table[led]
        return Line(code=code, item=name, ledger=led, why=why)

    sheets: list[Sheet] = []
    skipped: list[str] = []
    others: set[str] = set()
    stores = sorted(set(names.values()), key=lambda s: (fold(s), s))
    by_kind = {k: [s for s in stores if rules.kind(s) == k] for k in SHEET_KINDS + ("main",)}
    for k in SHEET_KINDS:
        for store in by_kind[k]:
            listed = held.get(store, set()) | moved.get(store, set())
            others |= listed - accessories
            items_ = sorted(listed & accessories)
            if items_:
                sheets.append(Sheet(store=clean(store), kind=k, lines=_order(line(led) for led in items_)))
            else:
                skipped.append(clean(store))

    main_parts: dict[str, int] = {}
    notes: list[str] = []
    mains = by_kind["main"]
    if mains:
        store = mains[0]
        if kind == "full":
            chosen = {led: (WHY_HELD,) for led in sorted(held.get(store, set()) & accessories)}
            main_parts[WHY_HELD] = len(chosen)
        else:
            why: dict[str, list] = {}
            fast = [led for led in picks.fast if led in accessories]
            for led in fast:
                why.setdefault(led, []).append(WHY_FAST)
            top = sorted(((v, led) for led, v in picks.value.items() if led in accessories and v > 0),
                         key=lambda x: (-x[0], fold(table[x[1]][1]), x[1]))[:TOP_VALUE]
            for _v, led in top:
                why.setdefault(led, []).append(WHY_VALUE)
            main_parts = {WHY_FAST: len(fast), WHY_VALUE: len(top)}
            if exceptions is not None:
                found = [led for led in dict.fromkeys(exceptions) if led in accessories]
                for led in found:
                    why.setdefault(led, []).append(WHY_EXCEPTION)
                main_parts[WHY_EXCEPTION] = len(found)
                if len(found) < len(set(exceptions)):
                    notes.append(f"{len(set(exceptions)) - len(found)} item(s) on a movement exception are not "
                                 "accessories: they are not on the main store's list.")
            elif exceptions_note:
                notes.append(exceptions_note)
            if picks.uncosted_held:
                notes.append(f"{picks.uncosted_held} item(s) held in the main store have no cost: they are not ranked "
                             "by value.")
            chosen = {led: tuple(w) for led, w in why.items()}
        if chosen:
            sheets.append(Sheet(store=clean(store), kind="main",
                                lines=_order(line(led, w) for led, w in chosen.items())))
    if others:
        notes.append(f"{len(others)} item(s) held or moved in the vans and outlets never moved in the main store "
                     "(not accessories): they are on no sheet.")

    taken: dict[str, int] = {}
    for s in sheets:
        base = slug(s.store)
        taken[base] = taken.get(base, 0) + 1
        s.slug = base if taken[base] == 1 else f"{base}-{taken[base]}"
        s.checksum = checksum(prep_id, s.store, [ln.code for ln in s.lines])
    codes = {code: {"item": name, "ledger": led} for led, (code, name) in sorted(table.items(), key=lambda t: t[1][0])}
    return Prep(prep_id=prep_id, kind=kind, prep_date=prep_date, data_date=d, sheets=sheets, codes=codes,
                notes=notes, skipped=skipped, main_parts=main_parts, not_accessories=len(others))


def prep_json(prep: Prep, file_name: str | None = None, ledger_note: str = "") -> dict:
    """What prep.json holds: no quantity of any kind (the sheets are blind)."""
    return jsonable({
        "version": PREP_VERSION, "tag": TAG, "prep_id": prep.prep_id, "kind": prep.kind, "prep_date": prep.prep_date,
        "data_date": prep.data_date, "file": file_name, "ledger_note": ledger_note,
        "stores": [{"store": s.store, "kind": s.kind, "slug": s.slug, "checksum": s.checksum,
                    "rows": [{"code": ln.code, "item": ln.item, "ledger": ln.ledger, **({"why": list(ln.why)}
                                                                                       if ln.why else {})}
                             for ln in s.lines]} for s in prep.sheets],
        "codes": prep.codes, "skipped": prep.skipped, "main_parts": prep.main_parts, "notes": prep.notes,
    })


def read_prep(prep_id: str) -> dict | None:
    """prep.json of that prep, or None when there is none (or it cannot be read as one)."""
    try:
        f = prep_file(prep_id)
    except ValueError:
        return None
    if not f.is_file():
        return None
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(doc, dict) or doc.get("tag") != TAG or doc.get("prep_id") != prep_id
            or fmt.as_date(doc.get("prep_date")) is None or not isinstance(doc.get("stores"), list)
            or not isinstance(doc.get("codes"), dict)):
        return None
    return doc


# ── the read-back: the checks ─────────────────────────────────────────────────

def _shown(v) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (datetime, date)):
        return f"a date, {fmt.as_date(v).isoformat()}"
    return repr(v) if isinstance(v, str) else str(v)


def qty_problem(v, formula: bool = False) -> str | None:
    """Why a typed Counted value is not a whole number of units, nought or more (None when it is one)."""
    if formula or (isinstance(v, str) and v.startswith("=")):
        return "holds a formula: type the number found"
    if isinstance(v, bool) or isinstance(v, (datetime, date)) or not isinstance(v, (int, float, Decimal)):
        return f"is not a number ({_shown(v)}): type the number found, or clear the cell"
    if isinstance(v, float) and not (v == v and abs(v) != float("inf")):
        return f"is not a number ({v}): type the number found"
    if v != int(v):
        return f"is not a whole number ({_shown(v)}): type a whole number of units"
    if v < 0:
        return f"is below nought ({_shown(v)}): type nought or more"
    return None


def count_date_of(v, formula: bool, prepared: date) -> tuple[date | None, str | None]:
    """(the Count date, None) or (None, why it is not one). A whole number in the cell's own range (the prep date
    to a year after, as Excel's serial numbers count) is a date that lost its format."""
    if formula or (isinstance(v, str) and v.startswith("=")):
        return None, "holds a formula: type the date of the count"
    if isinstance(v, datetime):
        return v.date(), None
    if isinstance(v, date):
        return v, None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and float(v).is_integer():
        try:
            day = date(1899, 12, 30) + timedelta(days=int(v))
        except OverflowError:
            day = None
        if day is not None and prepared <= day <= prepared + timedelta(days=DATE_RANGE_DAYS):
            return day, None
    return None, f"is not a date ({_shown(v)}): type the day of the count as a date"


@dataclass
class Checked:
    """What check() made of a typed file: the sheets to compare, the problems (each one sentence; while any stands
    nothing is kept), the sheets with nothing typed (not counted yet) and the sheets that are no count sheet."""
    sheets: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    untouched: list = field(default_factory=list)
    other_sheets: list = field(default_factory=list)


def _typed(s) -> bool:
    """Typed for sure: a Count date, a name, a count. (A code typed in an empty row is told only against the prep.)"""
    return (any(clean(s.top.get(label)) for label in (L_DATE, L_BY, L_CHECKED))
            or any(not r.sign and r.counted is not None for r in s.rows))


def _extra(r, listed: dict) -> bool:
    """A row the sheet did not print: a code it does not list, or a name typed with no code."""
    if r.sign:
        return False
    return bool(fold(r.code) not in listed) if r.code else bool(clean(r.item))


def _locate(s, preps: dict, prep_of) -> tuple[tuple | None, str | None]:
    """((prep, its entry for the sheet's store), None), or (None, the sentence that says why not)."""
    said = f"Sheet {s.sheet!r}"
    tag = clean(s.tag)
    if tag and tag != TAG:
        return None, (f"{said} carries the layout tag {tag!r}, not {TAG!r}: this engine reads only the count sheets it "
                      "made (recon prep).")
    if s.layout or not tag:
        return None, f"{said} {s.layout or 'has no layout tag: use the file as it was made'}."
    prep_id = clean(s.prep_id)
    if not PREP_ID.fullmatch(prep_id):
        return None, (f"{said}: its hidden layout row was changed (it holds no prep id): use the file as it was made, "
                      "with the counts typed in.")
    if prep_id not in preps:
        preps[prep_id] = prep_of(prep_id)
    prep = preps[prep_id]
    if prep is None:
        return None, (f"{said} belongs to the count sheets {prep_id}, and this PC holds no record of them "
                      "(business_data/ops/counts): read it on the PC that made them.")
    store = clean(s.top.get(L_STORE))
    entry = next((e for e in prep["stores"] if clean(e.get("store")) == store), None)
    if entry is None:
        return None, (f"{said}: its Store cell reads {store!r}, which is not a store of these count sheets: put back "
                      "the name it was printed with.")
    return (prep, entry), None


def check(read, today_: date | None = None, prep_of=read_prep) -> Checked:
    """The checks of THE READ-BACK on a file count_book.read_file() read. A sheet with no tag is no count sheet and is
    passed over; a sheet with nothing typed is not counted yet; a typed sheet is checked whole, every problem one
    sentence naming its sheet (and its row)."""
    today_ = today_ or today()
    out = Checked()
    preps: dict[str, dict | None] = {}
    for s in read.sheets:
        if s.tag is None:
            out.other_sheets.append(s.sheet)
            continue
        found_at, why = _locate(s, preps, prep_of)
        if why:
            if _typed(s):
                out.problems.append(why)
            else:                                # nothing typed on it: not counted, whatever else is wrong with it
                out.untouched.append(s.sheet)
            continue
        prep, entry = found_at
        listed = {fold(r["code"]): r for r in entry.get("rows") or []}
        if not _typed(s) and not any(_extra(r, listed) for r in s.rows):
            out.untouched.append(s.sheet)
            continue
        problems = _sheet_problems(s, prep, entry, listed, today_)
        if isinstance(problems, list):
            out.problems += problems
        else:
            out.sheets.append(problems)
    # one store typed on two sheets (a sheet copied to type again): its result would be the one read last
    seen: dict[tuple, str] = {}
    for sheet in list(out.sheets):
        key = (sheet["prep_id"], fold(sheet["store"]))
        if key in seen:
            out.problems.append(f"Sheets {seen[key]!r} and {sheet['sheet']!r} both hold the counts of {sheet['store']}: "
                                "keep them on one sheet.")
        seen.setdefault(key, sheet["sheet"])
    return out


def _sheet_problems(s, prep: dict, entry: dict, listed: dict, today_: date):
    """A typed sheet: its problems (a list of sentences), or, with none, the sheet as compare() takes it (a dict)."""
    said = f"Sheet {s.sheet!r}"
    prep_id, store = clean(s.prep_id), clean(s.top.get(L_STORE))
    known = {fold(code): dict(v, code=code) for code, v in (prep.get("codes") or {}).items()}
    problems: list[str] = []
    found: dict[str, list] = {}
    added: dict[str, list] = {}
    notes: list[str] = []
    for r in s.rows:
        if r.sign:
            continue
        where = f"{said}, row {r.row}"
        k = fold(r.code)
        if k and k in listed:
            found.setdefault(k, []).append(r)
        elif k and k in known:
            added.setdefault(k, []).append(r)
            if r.counted is None:
                problems.append(f"{where}: the code {clean(r.code)} has no count: type the number found (0 when there "
                                "is none), or clear the row.")
        elif k:
            problems.append(f"{where}: the code {clean(r.code)} is not an accessory's code: check it (anything else "
                            "found goes in a Note).")
        elif r.counted is not None:
            problems.append(f"{where} has a count but no code: type the item's code, or clear the count.")
        elif clean(r.item):
            problems.append(f"{where} names an item but has no code and no count: type its code and the number found, "
                            "or clear the row.")
        elif clean(r.note):
            notes.append(clean(r.note))
        if k and (k in listed or k in known) and r.counted is not None:
            why = qty_problem(r.counted, r.counted_formula)
            if why:
                problems.append(f"{where}: Counted {why}.")
    # the checksum, worked out again over the listed codes the file holds: a sorted sheet passes, a deleted row not
    missing = [listed[k]["code"] for k in listed if k not in found]
    again = checksum(prep_id, store, [listed[k]["code"] for k in found])
    if again != clean(s.checksum):
        if missing:
            named = [f"{c} ({listed[fold(c)]['item']})" for c in missing]
            many = len(missing) > 1
            problems.append(f"{said}: the row{'s' if many else ''} of {_short_list(named)} {'are' if many else 'is'} "
                            "missing (deleted?): type the code and the number found again in an empty row.")
        else:
            problems.append(f"{said}: its checksum does not match (its hidden layout row was changed, or the sheet comes "
                            "from other count sheets): use the file as it was made.")
    elif again != clean(entry.get("checksum")):
        problems.append(f"{said}: its checksum is not the one these count sheets recorded: use the file as it was made.")
    for group in (found, added):
        for rs in group.values():
            if len(rs) > 1:
                at = [str(r.row) for r in rs]
                problems.append(f"{said}: the code {clean(rs[0].code)} is on rows {', '.join(at[:-1])} and {at[-1]}: "
                                "keep one row per code (add the numbers together).")
    prepared = fmt.as_date(prep.get("prep_date"))
    raw = s.top.get(L_DATE)
    count_day = None
    if clean(raw) == "":
        problems.append(f"{said}: the Count date is empty: type the day of the count.")
    else:
        count_day, why = count_date_of(raw, L_DATE in s.top_formulas, prepared)
        if why:
            problems.append(f"{said}: the Count date {why}.")
        elif count_day > today_:
            problems.append(f"{said}: the Count date ({fmt.day_long(count_day)}) is in the future: type the day the "
                            "count was done.")
        elif count_day < prepared:
            problems.append(f"{said}: the Count date ({fmt.day_long(count_day)}) is before the sheets were prepared "
                            f"({fmt.day_long(prepared)}): type the day the count was done.")
    counted_by = "" if L_BY in s.top_formulas else clean(s.top.get(L_BY))
    if not counted_by:
        problems.append(f"{said}: Counted by is empty: type who counted.")
    if problems:
        return problems
    checked_by = "" if L_CHECKED in s.top_formulas else clean(s.top.get(L_CHECKED))

    def as_line(r, code_row: dict, was_added: bool) -> dict:
        return {"code": code_row["code"], "item": code_row["item"], "ledger": code_row["ledger"],
                "counted": None if r.counted is None else int(r.counted), "note": clean(r.note) or None,
                "row": r.row, "added": was_added}

    return {"sheet": s.sheet, "store": store, "kind": entry.get("kind"), "slug": entry.get("slug"),
            "prep_id": prep_id, "prep_kind": prep.get("kind"), "prep_date": prepared, "count_date": count_day,
            "counted_by": counted_by, "checked_by": checked_by or None,
            "lines": [as_line(found[fold(row["code"])][0], row, False) for row in entry.get("rows") or []],
            "added": [as_line(rs[0], known[k], True) for k, rs in added.items()], "sheet_notes": notes}


# ── the book side ─────────────────────────────────────────────────────────────

class Books:
    """The ledger's balances on the days asked for, each worked out once, and each store's movements on a day."""

    def __init__(self, rows, rules: pairing.StoreRules):
        self.rows = rows if isinstance(rows, (list, tuple)) else list(rows)
        self.rules = rules
        self.last = ledger.last_date(self.rows)
        self._at: dict[date, dict] = {}
        self._keys: dict[str, str] | None = None

    def store_key(self, store: str) -> str | None:
        """The name balances() keys the store on (spellings compared through the store rules)."""
        if self._keys is None:
            self._keys = {}
            for name in ledger.warehouse_names(self.rows).values():
                self._keys.setdefault(self.rules.canon(name), name)
        return self._keys.get(self.rules.canon(store))

    def at(self, day: date) -> dict:
        if day not in self._at:
            self._at[day] = ledger.balances(self.rows, day)
        return self._at[day]

    def moves_on(self, store: str, day: date) -> dict[str, list]:
        """{ledger item: [voucher numbers]} of that store on that day."""
        want = self.rules.canon(store)
        out: dict[str, set] = {}
        canon: dict[str, str] = {}
        for r in self.rows:
            if r["date"] != day:
                continue
            wh = r["wh"]
            if wh not in canon:
                canon[wh] = self.rules.canon(wh)
            if canon[wh] == want:
                out.setdefault(r["item"], set()).add(r["voucher"])
        return {item: sorted(v, key=pairing.parse_number) for item, v in out.items()}


def compare(sheet: dict, books: Books, costs: dict | None, meta: dict) -> dict:
    """One checked sheet set beside the books: the result kept for its store (see THE BOOK SIDE). `costs` is
    {ledger name: unit cost or None}, None when no cost could be read (the BHD figures are then None). `meta` is
    what to keep about the file and the ledger read: {file, sha256, saved_at, read_at, ledger_source, ledger_note}."""
    count_day: date = sheet["count_date"]
    book_day = count_day - timedelta(days=1)
    ready = books.last is not None and books.last >= book_day
    day_loaded = books.last is not None and books.last >= count_day
    key = books.store_key(sheet["store"])
    bal = books.at(book_day) if ready else {}
    moves = books.moves_on(sheet["store"], count_day) if day_loaded else {}
    rows_out: list[dict] = []
    short = over = short_bhd = over_bhd = ZERO
    uncosted = 0
    for ln in sheet["lines"] + sheet["added"]:
        counted = ln["counted"]
        book = (bal.get((key, ln["ledger"]), ZERO) if key is not None else ZERO) if ready else None
        diff = Decimal(counted) - book if counted is not None and book is not None else None
        cost = costs.get(ln["ledger"]) if costs is not None else None
        bhd = _q3(diff * cost) if diff is not None and cost is not None else None
        if diff is not None and diff < 0:
            short -= diff
            short_bhd -= bhd if bhd is not None else ZERO
        elif diff is not None and diff > 0:
            over += diff
            over_bhd += bhd if bhd is not None else ZERO
        if diff and cost is None and costs is not None:
            uncosted += 1
        rows_out.append({"code": ln["code"], "item": ln["item"], "ledger": ln["ledger"], "added": ln["added"],
                         "row": ln["row"], "counted": counted, "book": book, "diff": diff, "cost": cost, "bhd": bhd,
                         "count_day_vouchers": moves.get(ln["ledger"], []), "note": ln["note"]})
    listed = [r for r in rows_out if not r["added"]]
    waits = None if ready else book_day
    totals = {"lines": len(listed), "counted": sum(1 for r in listed if r["counted"] is not None),
              "added": len(rows_out) - len(listed),
              "short_units": short if ready else None, "over_units": over if ready else None,
              "short_bhd": _q3(short_bhd) if ready and costs is not None else None,
              "over_bhd": _q3(over_bhd) if ready and costs is not None else None,
              "uncosted_differences": uncosted if ready and costs is not None else None,
              "moved_on_count_day": sum(1 for r in rows_out if r["count_day_vouchers"])}
    return jsonable({
        "version": RESULT_VERSION, "tag": TAG, "prep_id": sheet["prep_id"], "prep_kind": sheet["prep_kind"],
        "prep_date": sheet["prep_date"], "store": sheet["store"], "kind": sheet["kind"], "slug": sheet["slug"],
        "sheet": sheet["sheet"], "count_date": count_day, "book_day": book_day, "month": book_day.strftime("%Y-%m"),
        "counted_by": sheet["counted_by"], "checked_by": sheet["checked_by"],
        "book": "ready" if ready else "waits", "waits_for": waits,
        "waits_words": (f"the book side waits for a load with data to {fmt.day_long(book_day)}" if waits else None),
        "count_day_loaded": day_loaded, "costed": costs is not None,
        "totals": totals, "rows": rows_out, "sheet_notes": sheet["sheet_notes"], **meta,
    })


def month_results(month: str) -> dict[str, dict]:
    """{store: its newest result of that month} (the latest Count date, then the latest read), across every prep.
    A file that cannot be read is passed over."""
    if not MONTH.fullmatch(str(month or "")):
        raise ValueError("month_results: a month written YYYY-MM")
    base = counts_dir() / month
    best: dict[str, dict] = {}
    if not base.is_dir():
        return best
    for f in sorted(base.glob("*/*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("tag") != TAG or not doc.get("store"):
            continue
        rank = (str(doc.get("count_date") or ""), str(doc.get("read_at") or ""))
        have = best.get(doc["store"])
        if have is None or rank > (str(have.get("count_date") or ""), str(have.get("read_at") or "")):
            best[doc["store"]] = doc
    return best
