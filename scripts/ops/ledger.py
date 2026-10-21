"""The stock ledger as the ops engine reads it: every movement of every item. READ-ONLY.

Source, in this order:
  1. the Focus Stock_ledger export of the set that is loaded (business_data/Focus ERP <date>/),
     read with the loader's own reader and parser (scripts.ingest.read_grid + parse_stock) and kept
     as a small cache under exports/ops/cache/, so only the first read of a file takes the workbook's
     15 seconds. A cache belongs to one workbook AND to the code that shaped its rows (the parser
     and this module): when either changes it is rebuilt, never served. The folder keeps the cache
     of the workbook just read and the newest other one; older ones are removed;
  2. the database (from_db), when the file is missing, cannot be opened (NoLedgerFile says which, in
     one sentence) or parity() says it is not the file production holds. stock_movements is not
     open to the read RPC, so that path is the read-only session of scripts.ops.db.
for_audit() makes that choice in one call and says which source was read, and why.

Warehouse names. One report spells a warehouse with its code after the name (config
'warehouses.aliases' maps that spelling), and a name typed in config can be in another case than
the ledger writes it. So a raw spelling is never compared with `==`: balances() and vouchers() key
on one name per warehouse (warehouse_names), same_warehouse() says whether two spellings are one
warehouse, is_main() whether one is the main store, and day_end_balances() takes its warehouse
however it is spelt.

A movement is a small dict:
    item      the item as the ledger names it ("<name> <item code>": see scripts.ops.items.Bridge)
    date      datetime.date                     voucher   the Focus number, e.g. 'SIO:X-26-01-1'
    vtype     the voucher type name             wh        the warehouse whose stock moved
    to_wh     where an issue was sent (issue rows only; a receipt does not say where it came from)
    qty_in / qty_out      units received / issued (Decimal; exactly one of them on a row)
    value_in / value_out  the same in BHD at cost, as the export states it (Decimal). 0 can mean
                          "not stated": a receive leg often carries units and no value (see vouchers)
    narr      a short hash of the narration: issue and receive rows only, else None
The four numbers are held to three decimals (fils; units are whole). Excel keeps every number as a
float, so a figure can arrive with stray digits far behind the point (0.1 + 0.2 as 0.3, fifteen
zeros and a 4): they end at _dec(), and never reach a row, the cache, a total or a printed line,
where such a tail would also read as a long number to the scan for card numbers.

PRIVACY. The narration column of the ledger holds customers' personal ID numbers on Sales Invoice
rows. Narration is therefore read ONLY on Stock Issue Voucher and Stock Receive Voucher rows (the
two legs of a transfer, where it helps to pair them), passed through the loader's own mask
(scripts.text_mask.mask_personal_ids, which scripts.ingest re-exports; standard library only, so the
database path needs no pandas), folded (spaces collapsed, lower case) and reduced to a short
hash in letters. Two legs typed with the same words get the same hash, and that is all it tells:
the mask comes first, so even a number typed into a transfer narration is hashed as its masked
form. The words themselves are never stored in a row, never cached and never printed, and the
database path does not even fetch them for other voucher types.

Units on hand are received minus issued, so they are right only when the ledger starts at the
item's first movement: parity() checks the first date as well as the last. The export's "Opening
Balance" rows name no warehouse and the parser leaves them out, so an item that opens with a
balance (SIM packs do, accessories do not) has no per-warehouse on-hand here; that is one reason
the movement figures are for accessories (scripts.ops.items.accessory_items).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from scripts.ops import config, fmt, loadplan, paths, status

# scripts.ops.db is imported inside the functions that reach the database (from_db, parity and
# for_audit, which calls them), as the other modules of the package do: importing this module, or
# scripts.ops.items, touches nothing.

# Voucher type names as Focus writes them in the ledger's "Voucher name" column.
ISSUE = "Stock Issue Voucher"                # stock leaves `wh` for `to_wh`
RECEIVE = "Stock Receive Voucher"            # the receiving leg of an issue (no reference to it)
SALE = "Sales Invoice"
SALES_RETURN = "Sales Return"
SUPPLIER_RECEIPT = "Material Receipt Note"
COUNT_SHORT = "Shortages in Stock"
COUNT_OVER = "Excesses in Stocks"
NARRATED = frozenset({ISSUE, RECEIVE})       # the only rows whose narration is read at all

FILE_PREFIX = "stock_ledger"
CACHE_VERSION = 1
NARR_LEN = 12
HASH_LETTERS = frozenset(loadplan.letters_only("0123456789abcdef"))   # all a narration hash can hold
ZERO = Decimal(0)

# The code that turns a workbook into rows: the loader's reader and parser, its mask (its own module
# from release 2b) and this module. A cache is bound to their bytes (see _code_key), so a change in
# any of them can never serve old rows.
_HERE = Path(__file__).resolve()
CODE_SOURCES = (_HERE.parents[1] / "ingest.py", _HERE.parents[1] / "text_mask.py", _HERE)

# One statement, aggregated in SQL (never rows): one line per voucher type and warehouse, about a
# hundred of them. Sums and counts travel as text so no float gets between the database and the
# comparison. No column is aliased `t` (the read RPC wraps the query as `... t`).
PARITY_SQL = (
    "SELECT COALESCE(voucher_type, '') AS vtype, COALESCE(warehouse_name, '') AS wh, "
    "SUM(in_qty)::text AS units_in, SUM(out_qty)::text AS units_out, SUM(lines)::text AS line_count, "
    "COUNT(DISTINCT move_date)::text AS day_count, "
    "MIN(move_date)::text AS first_date, MAX(move_date)::text AS last_date "
    "FROM v_stock_daily_movement "
    "GROUP BY COALESCE(voucher_type, ''), COALESCE(warehouse_name, '')"
)
# Narration is fetched for the two transfer types only: the rest never leaves the database.
DB_SQL = (
    "select item_name, move_date, voucher, voucher_type, warehouse_name, to_warehouse_name, "
    "received_qty, issued_qty, received_value_bhd, issued_value_bhd, "
    "case when voucher_type = any(%s) then narration end as narration "
    "from stock_movements where move_date is not null order by item_name, move_date, id"
)


@dataclass
class Ledger:
    rows: list[dict]
    source: str                    # the file's name, or 'stock_movements' when read from the database
    first_date: date | None
    last_date: date | None
    sha: str | None = None         # 'sha256:<hex>' of the file; None for the database
    origin: str = "file"           # file (the workbook was parsed) | cache | database
    cache_note: str | None = None  # why this read left no cache behind (a warning to print), else None


class NoLedgerFile(FileNotFoundError):
    """There is no stock ledger file to read, or the one that is there cannot be opened. Its text
    is one plain sentence that names the file or the folder (a name, never a path), fit to print as
    it is. A FileNotFoundError, so code that catches that keeps working: read from_db() instead."""


def _cannot_open(path: Path, exc: BaseException) -> NoLedgerFile:
    """The sentence for a file that is there and cannot be read as a workbook. It carries the kind
    of the reader's error and nothing of its text: that text can quote what a cell holds."""
    return NoLedgerFile(
        f"the stock ledger file {path.name} could not be opened ({type(exc).__name__}); the reader takes the "
        ".xlsx export of Focus, not an old-style .xls, and not a file that is damaged, half copied or held by "
        "another program")


# ── where the file is ─────────────────────────────────────────────────────────

def find_file(folder) -> Path | None:
    """The newest Stock_ledger*.xls* directly in `folder` (by modified time), or None. A '~$' file
    is Excel's lock on an open workbook, never the report."""
    if folder is None:
        return None
    folder = Path(folder)
    if not folder.is_dir():
        return None
    found = [p for p in folder.iterdir()
             if p.is_file() and not p.name.startswith("~$")
             and p.name.lower().startswith(FILE_PREFIX) and p.suffix.lower().startswith(".xls")]
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def loaded_folder() -> Path | None:
    """The folder of the set that is loaded: business_data/<state.last_load.published> when
    state.json names it (and it is there), else the newest 'Focus ERP 20*' folder."""
    base = paths.data_root() / "business_data"
    if not base.is_dir():
        return None
    last = status.read_state().get("last_load")
    named = str(last.get("published") or "").strip() if isinstance(last, dict) else ""
    if named:
        folder = base / named
        if folder.is_dir() and folder.resolve().parent == base.resolve():     # a folder of business_data, nothing else
            return folder
    folders = sorted((p for p in base.glob("Focus ERP 20*") if p.is_dir()), key=lambda p: p.name)
    return folders[-1] if folders else None


def cache_file(sha: str) -> Path:
    """Where the shaped rows of the file with this sha256 are kept. The name is the hash written
    in letters only, so it can never look like a number of any kind."""
    hex_text = sha.split(":", 1)[1] if ":" in sha else sha
    return paths.exports_dir() / "cache" / f"ledger_{loadplan.letters_only(hex_text)[:32]}.json"


def _code_key() -> str | None:
    """The code that shapes a row (CODE_SOURCES), as a hash in letters. A cache carries it: rows
    shaped by other code (a changed parser, voucher-type table or mask) are parsed again instead of
    served. Reading two small files, so a cache hit still never imports pandas. None when a source
    cannot be read: no cache is used then."""
    digest = hashlib.sha256()
    try:
        for source in CODE_SOURCES:
            digest.update(source.read_bytes())
            digest.update(b"\0")
    except OSError:
        return None
    return loadplan.letters_only(digest.hexdigest())[:32]


# ── shaping (the one place a narration is ever looked at) ─────────────────────

def _dec(x) -> Decimal:
    """A quantity or a value as a row holds it: a Decimal of at most three decimals, rounded half
    up and written the short way (100, 12.5, 0.3). Blank is 0.

    Excel keeps every number as a float, the parser hands it on as one, and the table can hold what
    such a float wrote. So a figure summed in Focus (0.1 + 0.2) can arrive as 0.3 with a tail of
    stray digits: here it becomes 0.3, whatever the source, and a row from the file equals the same
    row from the database. ValueError for anything that is not a number."""
    if x is None or x == "":
        return ZERO
    if isinstance(x, float) and math.isnan(x):
        return ZERO                           # a blank cell
    try:
        if isinstance(x, float):
            # as the float is written, not its binary tail; float() because a numpy float's repr names its type
            number = Decimal(repr(float(x)))
        elif isinstance(x, (Decimal, int)):
            number = Decimal(x)
        else:
            number = Decimal(str(x).replace(",", "").strip())
        if not number.is_finite():
            raise ArithmeticError
        number = number.quantize(fmt.Q3, rounding=ROUND_HALF_UP)
    except ArithmeticError:                   # words, infinity, or a figure too long to be one
        raise ValueError("stock ledger: a quantity or a value is not a number") from None
    if not number:
        return ZERO                           # -0.000 as well
    whole = number.to_integral_value()
    return whole if whole == number else number.normalize()


def _day(x) -> date:
    d = fmt.as_date(x)
    if d is None:
        raise ValueError("stock ledger: a movement has no readable date")
    return d


def _narr_key(text, mask) -> str | None:
    """A transfer narration as a short hash in letters; None when blank. `mask` is the loader's
    mask_personal_ids. Nothing but the hash leaves this function."""
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return None
    folded = " ".join(str(mask(str(text)) or "").split()).lower()
    if not folded:
        return None
    return loadplan.letters_only(hashlib.sha256(folded.encode("utf-8")).hexdigest())[:NARR_LEN]


def _shape(raw_rows, mask) -> list[dict]:
    """parse_stock rows, or stock_movements rows (same column names), as movements. Every column
    is asked for by name with no default: if the parser ever renames one, this stops with the
    name instead of filling a ledger with blanks."""
    out: list[dict] = []
    for r in raw_rows:
        vtype = str(r["voucher_type"] or "")
        to_wh = r["to_warehouse_name"]
        out.append({
            "item": str(r["item_name"] or ""),
            "date": _day(r["move_date"]),
            "voucher": str(r["voucher"] or ""),
            "vtype": vtype,
            "wh": str(r["warehouse_name"] or ""),
            "to_wh": str(to_wh) if to_wh else None,
            "qty_in": _dec(r["received_qty"]),
            "qty_out": _dec(r["issued_qty"]),
            "value_in": _dec(r["received_value_bhd"]),
            "value_out": _dec(r["issued_value_bhd"]),
            "narr": _narr_key(r["narration"], mask) if vtype in NARRATED else None,
        })
    return out


def _parse(path: Path) -> list[dict]:
    """The workbook through the loader's reader and parser (the parser checks the column layout
    first and refuses a changed one, so a narration can never be read as another column). The
    sheet and the parser's rows hold every narration; neither outlives this call.

    A file the reader cannot open at all (an old-style .xls named like the report, a damaged or
    half-copied file) ends in NoLedgerFile: one sentence naming the file, not the reader's own
    error. A workbook that opens and is laid out differently still stops at the parser, with its
    own words on the header."""
    from scripts import ingest               # pandas: imported only when a workbook is really read
    from scripts.text_mask import mask_personal_ids

    try:
        grid = ingest.read_grid(path)
    except (ImportError, MemoryError):       # this PC, not the file: said as it is
        raise
    except Exception as exc:                 # noqa: BLE001  whatever the reader makes of a file it cannot open
        raise _cannot_open(path, exc) from None
    raw = ingest.parse_stock(grid, path.name)
    del grid
    return _shape(raw, mask_personal_ids)


# ── the cache: shaped rows only, never a narration ────────────────────────────

_TABLES = (("items", "item"), ("dates", "date"), ("vouchers", "voucher"), ("vtypes", "vtype"),
           ("warehouses", "wh"), ("narrs", "narr"))


def _pack(rows: list[dict], sha_letters: str, code: str) -> dict:
    """Rows as arrays of table positions: the names are written once, so the file stays small and
    reads back in a fraction of a second. `sha_letters` ties it to the workbook, `code` to the
    code that shaped the rows."""
    tables: dict[str, dict] = {name: {} for name, _field in _TABLES}

    def pos(name: str, value):
        if value is None:
            return None
        table = tables[name]
        if value not in table:
            table[value] = len(table)
        return table[value]

    packed = []
    for r in rows:
        packed.append([
            pos("items", r["item"]), pos("dates", r["date"].isoformat()), pos("vouchers", r["voucher"]),
            pos("vtypes", r["vtype"]), pos("warehouses", r["wh"]), pos("warehouses", r["to_wh"]),
            str(r["qty_in"]), str(r["qty_out"]), str(r["value_in"]), str(r["value_out"]),
            pos("narrs", r["narr"]),
        ])
    doc: dict = {"version": CACHE_VERSION, "sha": sha_letters, "code": code}
    for name, _field in _TABLES:
        doc[name] = list(tables[name])
    doc["rows"] = packed
    return doc


def _unpack(doc: dict) -> list[dict]:
    """The rows of a cache document. Raises on anything unexpected (the caller then rebuilds)."""
    def texts(name: str) -> list[str]:
        values = doc[name]
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise TypeError(name)
        return values

    items, vouchers_, vtypes, whs = texts("items"), texts("vouchers"), texts("vtypes"), texts("warehouses")
    dates = [date.fromisoformat(v) for v in texts("dates")]
    narrs = texts("narrs")
    if not all(len(v) == NARR_LEN and set(v) <= HASH_LETTERS for v in narrs):
        raise ValueError("narration that is not a hash")      # words never come back from a cache
    numbers: dict[str, Decimal] = {}

    def num(text) -> Decimal:
        if not isinstance(text, str):
            raise TypeError("number")
        if text not in numbers:
            numbers[text] = Decimal(text)
        return numbers[text]

    out: list[dict] = []
    for item, day, voucher, vtype, wh, to_wh, q_in, q_out, v_in, v_out, narr in doc["rows"]:
        out.append({
            "item": items[item], "date": dates[day], "voucher": vouchers_[voucher], "vtype": vtypes[vtype],
            "wh": whs[wh], "to_wh": None if to_wh is None else whs[to_wh],
            "qty_in": num(q_in), "qty_out": num(q_out), "value_in": num(v_in), "value_out": num(v_out),
            "narr": None if narr is None else narrs[narr],
        })
    return out


def _read_cache(file: Path, sha_letters: str, code: str) -> list[dict] | None:
    """The cached rows, or None when there is no cache for this file or it cannot be trusted
    (unreadable, another layout version, not for this file, shaped by other code)."""
    if not file.is_file():
        return None
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
        if (not isinstance(doc, dict) or doc.get("version") != CACHE_VERSION or doc.get("sha") != sha_letters
                or doc.get("code") != code):
            return None
        return _unpack(doc)
    except (OSError, ValueError, KeyError, IndexError, TypeError, ArithmeticError):
        return None


def _is_cache_name(name: str) -> bool:
    """Is this a name cache_file() gives: 'ledger_' + 32 hash letters + '.json'? Nothing else in
    the cache folder is this module's to remove."""
    stem, dot, kind = name.rpartition(".")
    head, _, key = stem.partition("_")
    return dot == "." and kind == "json" and head == "ledger" and len(key) == 32 and set(key) <= HASH_LETTERS


def _prune_cache(keep: Path) -> int:
    """Remove old caches: beside `keep` (the cache of the workbook just read) the folder keeps the
    newest other one, by its time, and no more. Every distinct export leaves a file of about 2 MB
    and nothing else would ever remove one. Only names cache_file() gives are touched, each through
    paths.remove_file (its guard: under exports/ or business_data/ of the data root). Returns how
    many went. A file that cannot be removed now (a sync program holds it) is left for the next
    read: the rows are good either way, so this never raises."""
    try:
        others = [(p.stat().st_mtime, p.name, p) for p in keep.parent.iterdir()
                  if p.name != keep.name and _is_cache_name(p.name) and p.is_file()]
    except OSError:
        return 0
    removed = 0
    for _time, _name, old in sorted(others, reverse=True)[1:]:
        try:
            removed += bool(paths.remove_file(old))
        except OSError:
            continue
    return removed


# ── reading ───────────────────────────────────────────────────────────────────

def load(path=None, use_cache: bool = True) -> Ledger:
    """The ledger of `path` (a Stock_ledger workbook, or a folder holding one); with no path, the
    one in the loaded folder. use_cache=False reads the workbook and leaves the cache folder alone.
    NoLedgerFile (a FileNotFoundError) when there is no such file, or the file cannot be opened as
    a workbook: one plain sentence, and the caller then reads from_db().
    A read that could not leave a cache behind still answers, and says so in `cache_note` (the
    caller prints it as a warning): without a cache every read takes the workbook's full time.
    A read that used the cache also clears old caches away (_prune_cache)."""
    if path is None:
        folder = loaded_folder()
        file = find_file(folder)
        if file is None:
            where = folder.name if folder is not None else "business_data (no 'Focus ERP 20*' folder)"
            raise NoLedgerFile(f"no Stock_ledger file in {where}")
    else:
        file = Path(path)
        if file.is_dir():
            found = find_file(file)
            if found is None:
                raise NoLedgerFile(f"no Stock_ledger file in {file.name}")
            file = found
        elif not file.is_file():
            raise NoLedgerFile(f"no such file: {file.name}")

    try:
        sha = loadplan.sha256_file(file)
    except OSError as exc:                   # there, and Windows will not let it be read
        raise _cannot_open(file, exc) from None
    letters = loadplan.letters_only(sha.split(":", 1)[1])
    cache = cache_file(sha)
    slow = "every read opens the workbook again, which is slow"
    note = None
    code = _code_key() if use_cache else None
    if use_cache and code is None:
        use_cache = False
        note = f"the stock ledger cache is not used (a program file could not be read): {slow}"
    rows = _read_cache(cache, letters, code) if use_cache else None
    origin = "cache"
    cached = rows is not None
    if rows is None:
        rows = _parse(file)
        origin = "file"
        if use_cache:
            try:
                paths.write_text(cache, json.dumps(_pack(rows, letters, code), separators=(",", ":")))
                cached = True
            except OSError as exc:           # the rows are good; only the next read is slow again
                note = f"the stock ledger cache could not be written ({type(exc).__name__}): {slow}"
    if cached:                               # this workbook's cache is in place: older ones can go
        _prune_cache(cache)
    return Ledger(rows=rows, source=file.name, first_date=first_date(rows), last_date=last_date(rows),
                  sha=sha, origin=origin, cache_note=note)


def from_db() -> Ledger:
    """The same ledger read from stock_movements through the read-only session: the fallback when
    the file is missing, cannot be opened or is not what production holds (for_audit makes that
    choice). Shaped and hashed the same way, its numbers held to the same three decimals."""
    from scripts.ops import db               # first: before anything that could import app.*
    from scripts.text_mask import mask_personal_ids    # the loader's mask, without pandas

    with db.session() as s:
        raw = s.q(DB_SQL, (sorted(NARRATED),))
    rows = _shape(raw, mask_personal_ids)
    return Ledger(rows=rows, source="stock_movements", first_date=first_date(rows),
                  last_date=last_date(rows), sha=None, origin="database")


# ── questions on the rows ─────────────────────────────────────────────────────

def first_date(rows) -> date | None:
    return min((r["date"] for r in rows), default=None)


def last_date(rows) -> date | None:
    return max((r["date"] for r in rows), default=None)


def _fold(name) -> str:
    return " ".join(str(name or "").split()).casefold()


def warehouse_aliases() -> dict[str, str]:
    """config 'warehouses.aliases' ready for lookups: {a spelling, folded: the name used everywhere
    else}. One report writes a warehouse with its code after the name; the ledger does not."""
    return {_fold(k): str(v) for k, v in config.get("warehouses.aliases").items() if _fold(k) and _fold(v)}


def warehouse(name, aliases: dict | None = None) -> str:
    """One spelling through config 'warehouses.aliases': the alias when config has one for it
    (spaces and case ignored), else the name as given. This looks at one name alone: the one name
    a warehouse is keyed on across a set of rows is warehouse_names(), and whether two spellings
    are one warehouse is same_warehouse()."""
    table = warehouse_aliases() if aliases is None else aliases
    text = str(name or "")
    return table.get(_fold(text), text)


def same_warehouse(a, b, aliases: dict | None = None) -> bool:
    """Do two spellings name one warehouse? Through the aliases, spaces and case ignored. Ask this
    (or is_main) wherever a name typed in config meets a name from a report, never `==`: a name
    typed in another case would find nothing, without a word. A blank name is no warehouse."""
    table = warehouse_aliases() if aliases is None else aliases
    left = _fold(warehouse(a, table))
    return bool(left) and left == _fold(warehouse(b, table))


def warehouse_names(rows, aliases: dict | None = None) -> dict[str, str]:
    """{each warehouse spelling in these rows ('wh' and 'to_wh'): the one name it is keyed on}.

    The alias when config has one for the spelling. Spellings that differ only in case or spacing
    are one warehouse and share one name: the name config writes when it is the target of an alias,
    else the first spelling in the rows. balances() and vouchers() key on these names, so one
    warehouse is one key however a row spells it; code that walks the rows itself names a row's
    warehouse through this, and gets the same keys."""
    table = warehouse_aliases() if aliases is None else aliases
    canon: dict[str, str] = {}
    for target in table.values():
        canon.setdefault(_fold(target), target)
    names: dict[str, str] = {}
    for r in rows:
        for spelt in (r["wh"], r["to_wh"]):
            if spelt is not None and spelt not in names:
                name = warehouse(spelt, table)
                names[spelt] = canon.setdefault(_fold(name), name)
    return names


def _wh_key(name, aliases: dict) -> str:
    """One spelling as it is compared: through the aliases, spaces and case ignored."""
    return _fold(warehouse(name, aliases))


def _main_key(main, aliases: dict) -> str:
    key = _wh_key(config.get("warehouses.main") if main is None else main, aliases)
    if not key:
        raise ValueError("the main store has no name (config 'warehouses.main')")
    return key


def is_main(name, main: str | None = None, aliases: dict | None = None) -> bool:
    """Is this warehouse the main store (config 'warehouses.main', or `main`)? Compared the way
    same_warehouse() compares: through the aliases, spaces and case ignored. Ask this, never `==`:
    config can spell the store in another case than the ledger does, and `==` then finds nothing.
    ValueError when the main store itself has no name."""
    table = warehouse_aliases() if aliases is None else aliases
    return _fold(warehouse(name, table)) == _main_key(main, table)


def balances(rows, as_of=None) -> dict[tuple[str, str], Decimal]:
    """{(warehouse, item): units} = received minus issued up to and including `as_of` (every date
    when None). The warehouse is its one name (warehouse_names: config 'warehouses.aliases' applied,
    spellings that differ only in case or spacing merged), the same for every `as_of` on the same
    rows. The main store's pairs are the ones is_main() says yes to. A pair that has moved and
    stands at nothing is kept, with 0; a pair that had not moved yet is absent."""
    limit = None
    if as_of is not None:
        limit = fmt.as_date(as_of)
        if limit is None:
            raise ValueError(f"balances: not a date: {as_of!r}")
    rows = rows if isinstance(rows, (list, tuple)) else list(rows)
    names = warehouse_names(rows)
    out: dict[tuple[str, str], Decimal] = {}
    for r in rows:
        if limit is not None and r["date"] > limit:
            continue
        key = (names[r["wh"]], r["item"])
        out[key] = out.get(key, ZERO) + r["qty_in"] - r["qty_out"]
    return out


def day_end_balances(rows, items, warehouse, first, last) -> dict[str, list[Decimal]]:
    """{item: [units at the end of each day from `first` to `last`, both days included]} for one
    warehouse: the day series stock planning reads (was an item in stock on that day?).

    A day's figure is received minus issued up to and including that day, as balances(as_of=day)
    gives it, so the first one already carries everything before `first`: pass the WHOLE ledger,
    never a window of it. The rows are walked once, however many days are asked for (balances()
    for each day would walk them once per day); any iterable will do.

    items      the ledger item names wanted, in this order in the answer. Each gets its list, all 0
               for an item that never moved there. None: every item that moved in the warehouse up
               to `last`, by name.
    warehouse  a name as config or any report spells it (compared like same_warehouse: aliases,
               spaces and case ignored); None is the main store (config 'warehouses.main').
    first, last  dates, or ISO text.

    Right for an item whose ledger starts at its first movement (see the note on opening balances
    at the top: accessories do, SIM packs do not). ValueError for a blank warehouse, a date that
    cannot be read, or `last` before `first`; and for a warehouse that no row of the ledger moved
    in (a name misspelt, or a store that is only ever sent to): the answer would be 0 on every day,
    and would read as "out of stock all month"."""
    start, end = fmt.as_date(first), fmt.as_date(last)
    if start is None or end is None:
        raise ValueError(f"day_end_balances: not a date: {(first if start is None else last)!r}")
    if end < start:
        raise ValueError(f"day_end_balances: the last day ({end.isoformat()}) is before the first ({start.isoformat()})")
    aliases = warehouse_aliases()
    target = _main_key(None, aliases) if warehouse is None else _wh_key(warehouse, aliases)
    if not target:
        raise ValueError("day_end_balances: the warehouse has no name")
    if isinstance(items, str):
        items = [items]
    wanted = None if items is None else dict.fromkeys(items)

    before: dict[str, Decimal] = {}                  # item -> units moved before `first`
    on_day: dict[str, dict[int, Decimal]] = {}       # item -> {day number from `first`: units moved that day}
    here: dict[str, bool] = {}                       # a spelling of the rows -> is it this warehouse
    for r in rows:
        spelt = r["wh"]
        inside = here.get(spelt)
        if inside is None:
            inside = here[spelt] = _wh_key(spelt, aliases) == target
        if not inside:
            continue
        day = r["date"]
        if day > end:
            continue
        item = r["item"]
        if wanted is not None and item not in wanted:
            continue
        moved = r["qty_in"] - r["qty_out"]
        if day < start:
            before[item] = before.get(item, ZERO) + moved
            on_day.setdefault(item, {})
        else:
            days = on_day.setdefault(item, {})
            n = (day - start).days
            days[n] = days.get(n, ZERO) + moved
    if here and True not in here.values():           # rows, and not one of them in this warehouse
        named = (f"the main store {config.get('warehouses.main')!r} (config 'warehouses.main')" if warehouse is None
                 else f"the warehouse {str(warehouse)!r}")
        raise ValueError(f"day_end_balances: no movement was found in {named}: check how the name is spelt, and pass "
                         "the whole ledger (a store that is only ever sent to has no stock of its own in it)")

    length = (end - start).days + 1
    out: dict[str, list[Decimal]] = {}
    for item in (sorted(on_day) if wanted is None else wanted):
        level = before.get(item, ZERO)
        days = on_day.get(item, {})
        series: list[Decimal] = []
        for n in range(length):
            if n in days:
                level += days[n]
            series.append(level)
        out[item] = series
    return out


def main_items(rows, main: str | None = None) -> set[str]:
    """Every item that has EVER moved in the main store (config 'warehouses.main', or `main`): the
    ledger's own answer to "which items are ours to count". A row counts by the warehouse whose
    stock moved, never by where an issue was sent. An empty answer on rows that are not empty means
    the name matches no warehouse (scripts.ops.items.accessory_items stops on it)."""
    aliases = warehouse_aliases()
    target = _main_key(main, aliases)
    in_main: dict[str, bool] = {}
    out: set[str] = set()
    for r in rows:
        wh = r["wh"]
        if wh not in in_main:
            in_main[wh] = _fold(warehouse(wh, aliases)) == target
        if in_main[wh]:
            out.add(r["item"])
    return out


def vouchers(rows, vtypes) -> dict[str, dict]:
    """Whole vouchers of the given type(s), which is what a transfer is paired on:

        {voucher: {vtype, date, wh, to_wh, lines {item: units}, value, unvalued, narr, mixed}}

    lines: units summed per item (an item can sit on several lines of one voucher).
    value: BHD as the export states it on the lines, and no more than that. Focus often leaves the
    rate and the value of a receive leg blank (of an issue leg, now and then): `unvalued` is the
    units on lines that state no value. So a transfer is valued from its issue leg, or from the cost
    basis, never from a receive leg alone; a `value` beside an `unvalued` above 0 is a part.
    wh / to_wh: the one name of each warehouse (warehouse_names), the same for any `vtypes` on the
    same rows. narr: the narration hash.
    A voucher is one document, so its type, date, warehouses and narration are the same on every
    line; where a file ever says otherwise the field is named in `mixed` (the earliest date and the
    first value are kept) and the voucher is one to set aside, not to pair. Ordered by date, then
    voucher number."""
    want = {vtypes} if isinstance(vtypes, str) else set(vtypes)
    rows = rows if isinstance(rows, (list, tuple)) else list(rows)
    names = warehouse_names(rows)
    found: dict[str, dict] = {}
    for r in rows:
        if r["vtype"] not in want:
            continue
        head = {"vtype": r["vtype"], "date": r["date"], "wh": names[r["wh"]],
                "to_wh": None if r["to_wh"] is None else names[r["to_wh"]], "narr": r["narr"]}
        v = found.get(r["voucher"])
        if v is None:
            v = found[r["voucher"]] = dict(head, lines={}, value=ZERO, unvalued=ZERO, mixed=[])
        else:
            for name, value in head.items():
                if v[name] != value and name not in v["mixed"]:
                    v["mixed"].append(name)
            if r["date"] < v["date"]:
                v["date"] = r["date"]
            if v["narr"] is None:
                v["narr"] = r["narr"]
        units = r["qty_in"] + r["qty_out"]
        stated = r["value_in"] + r["value_out"]
        v["lines"][r["item"]] = v["lines"].get(r["item"], ZERO) + units
        v["value"] += stated
        if units != 0 and stated == 0:
            v["unvalued"] += units
    out: dict[str, dict] = {}
    for number in sorted(found, key=lambda n: (found[n]["date"], n)):
        v = found[number]
        v["lines"] = dict(sorted(v["lines"].items()))
        out[number] = v
    return out


# ── is this file what production holds ────────────────────────────────────────

# field -> the words for it. Per voucher type: the first three. Per voucher type in one warehouse: all.
PARITY_FIGURES = (("units_in", "units in"), ("units_out", "units out"), ("lines", "lines"),
                  ("days", "days with movement"), ("first", "first day"), ("last", "last day"))
_SUMMED = PARITY_FIGURES[:3]


def totals(rows) -> dict[tuple[str, str], dict]:
    """{(voucher type, warehouse): {units_in, units_out, lines, days, first, last}}: what parity()
    compares with production. `days` is the number of days the pair moved on. The warehouse is the
    spelling of the row here, as production holds it: two copies of one export are compared, no
    figure is shown from this."""
    out: dict[tuple[str, str], dict] = {}
    moved_on: dict[tuple[str, str], set] = {}
    for r in rows:
        key = (r["vtype"], r["wh"])
        t = out.get(key)
        if t is None:
            t = out[key] = {"units_in": ZERO, "units_out": ZERO, "lines": 0, "days": 0,
                            "first": r["date"], "last": r["date"]}
            moved_on[key] = set()
        t["units_in"] += r["qty_in"]
        t["units_out"] += r["qty_out"]
        t["lines"] += 1
        moved_on[key].add(r["date"])
        if r["date"] < t["first"]:
            t["first"] = r["date"]
        if r["date"] > t["last"]:
            t["last"] = r["date"]
    for key, days in moved_on.items():
        out[key]["days"] = len(days)
    return out


def _by_type(pairs: dict) -> dict[str, dict]:
    """The summed figures of totals(), per voucher type."""
    out: dict[str, dict] = {}
    for (vtype, _wh), t in pairs.items():
        s = out.setdefault(vtype, {"units_in": ZERO, "units_out": ZERO, "lines": 0})
        for field, _words in _SUMMED:
            s[field] += t[field]
    return out


def _show(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, date):
        return fmt.day_long(value)
    return fmt.units(value)


def parity(ledger: Ledger) -> dict:
    """Is this ledger what production holds? {ok, checks[], summary}.

    One read of v_stock_daily_movement, aggregated in SQL to one line per voucher type and
    warehouse. Compared, in this order:
      dates      the first and the last movement date;
      type       units in, units out and lines of each voucher type;
      warehouse  the same three for each voucher type in each warehouse, with the number of days it
                 moved on and its first and last day.
    ok only when every one agrees (and there is something to compare). Each check:
    {level, what, file, production, ok}.

    It compares counts, not lines: two copies that differ only in which item a line names, or in a
    value, would pass. Units are compared to three decimals on both sides, as a row holds them: a
    float's stray digits in the table are no difference. A file that fails is not wrong, it is just
    not the loaded one: read from_db() instead (for_audit() does). An error reaching production is
    raised, not turned into an answer."""
    from scripts.ops import db

    mine = totals(ledger.rows)
    theirs: dict[tuple[str, str], dict] = {}
    their_first: date | None = None
    their_last: date | None = None
    for row in db.rpc(PARITY_SQL):
        start, end = fmt.as_date(row.get("first_date")), fmt.as_date(row.get("last_date"))
        theirs[(str(row.get("vtype") or ""), str(row.get("wh") or ""))] = {
            "units_in": _dec(fmt.dec(row.get("units_in"))), "units_out": _dec(fmt.dec(row.get("units_out"))),
            "lines": int(fmt.dec(row.get("line_count"))), "days": int(fmt.dec(row.get("day_count"))),
            "first": start, "last": end,
        }
        if start is not None and (their_first is None or start < their_first):
            their_first = start
        if end is not None and (their_last is None or end > their_last):
            their_last = end

    def check(level: str, what: str, mine_value, their_value) -> dict:
        return {"level": level, "what": what, "file": mine_value, "production": their_value,
                "ok": mine_value == their_value}

    checks: list[dict] = [check("dates", "first movement date", ledger.first_date, their_first),
                          check("dates", "last movement date", ledger.last_date, their_last)]
    my_types, their_types = _by_type(mine), _by_type(theirs)
    no_type = {"units_in": ZERO, "units_out": ZERO, "lines": 0}
    for vtype in sorted(set(my_types) | set(their_types)):
        a, b = my_types.get(vtype, no_type), their_types.get(vtype, no_type)
        for field, words in _SUMMED:
            checks.append(check("type", f"{vtype or '(no voucher type)'}: {words}", a[field], b[field]))
    no_pair = {"units_in": ZERO, "units_out": ZERO, "lines": 0, "days": 0, "first": None, "last": None}
    for vtype, wh in sorted(set(mine) | set(theirs)):
        a, b = mine.get((vtype, wh), no_pair), theirs.get((vtype, wh), no_pair)
        label = f"{vtype or '(no voucher type)'} in {wh or '(no warehouse)'}"
        for field, words in PARITY_FIGURES:
            checks.append(check("warehouse", f"{label}: {words}", a[field], b[field]))
    compared = bool(mine or theirs)
    differ = [c for c in checks if not c["ok"]]
    ok = compared and not differ

    # The one sentence for the owner ("the portal" is his word for what production holds). The
    # wording guard reads the engine's own words; a voucher type or a warehouse is a name from the
    # data, put in after it.
    if ok:
        lines = sum(t["lines"] for t in mine.values())
        summary = fmt.careful(
            f"The stock ledger file agrees with what the portal holds: {fmt.units(lines)} lines in "
            f"{len(my_types)} voucher types, the same units, lines and days in every warehouse, "
            f"{fmt.day_long(ledger.first_date)} to {fmt.day_long(ledger.last_date)}.")
    elif not compared:
        summary = fmt.careful("The stock ledger could not be compared with the portal: neither side has a movement.")
    else:
        # The plain fact first: the dates (the portal simply holds a newer or an older load). Then
        # the voucher types. A warehouse is named only when every type agrees and one differs inside.
        at = {level: [c for c in differ if c["level"] == level] for level in ("dates", "type", "warehouse")}
        told = at["dates"] + (at["type"] or at["warehouse"])
        shown = "; ".join(f"{c['what']} {_show(c['file'])} in the file, {_show(c['production'])} in the portal"
                          for c in told[:3])
        more = f" (+{len(told) - 3} more)" if len(told) > 3 else ""
        lead = fmt.careful("The stock ledger file differs from what the portal holds, to check: ")
        summary = f"{lead}{shown}{more}."
    return {"ok": ok, "checks": checks, "summary": summary}


# ── which source an audit reads ───────────────────────────────────────────────

PREFER = ("auto", "file", "db")


def for_audit(prefer: str = "auto") -> tuple[Ledger, dict]:
    """The ledger an audit reads, and where it came from: (Ledger, info).

        info = {"source": "file" | "database",
                "parity": what parity() answered, or None when it was not asked or gave no answer,
                "note":   one plain sentence: the source that was read, and why}

    prefer="auto"  the file of the loaded folder when it is there AND parity() says it is what
                   production holds. In every other case the database (from_db): no file, a file
                   that cannot be read, a file that differs (`parity` then holds the differences),
                   or a comparison that could not be made (`parity` None; the note says so). An
                   error from the database itself is raised: nothing is left to read.
    prefer="file"  the file, not compared: the database is never touched. NoLedgerFile when there
                   is no file to read; any other error of the read is raised as it is (no fallback).
    prefer="db"    the database, always: the file is not opened.

    The note is the engine's own words around a file or folder name ("the portal" is the owner's
    word for what production holds): a command prints it under its figures, with Ledger.cache_note
    as a warning when that is set."""
    if prefer not in PREFER:
        raise ValueError(f"for_audit: prefer is one of {', '.join(PREFER)}, not {prefer!r}")
    from_file = "The stock ledger is read from the file"
    from_portal = "The stock ledger is read from the portal's database"
    if prefer == "file":
        ledger = load()
        return ledger, {"source": "file", "parity": None,
                        "note": f"{from_file} {ledger.source}, as asked: it was not compared with the portal."}
    from scripts.ops import db  # noqa: F401  (first, as every command does: before a workbook or an app module loads)
    if prefer == "db":
        return from_db(), {"source": "database", "parity": None,
                           "note": f"{from_portal}, as asked: the file was not opened."}

    ledger = answer = None
    why = ""
    try:
        ledger = load()
    except NoLedgerFile as exc:              # no file, or one that cannot be opened: its text is the sentence
        why = str(exc)
    except (OSError, ValueError, KeyError, ArithmeticError) as exc:     # it opens, and gives no ledger
        why = f"the stock ledger file of the loaded folder could not be read ({type(exc).__name__})"
    if ledger is not None:
        try:
            answer = parity(ledger)
        except Exception as exc:             # noqa: BLE001  no comparison is no proof: the database is read
            why = f"the file {ledger.source} could not be compared with the portal ({type(exc).__name__})"
        else:
            if answer["ok"]:
                return ledger, {"source": "file", "parity": answer,
                                "note": f"{from_file} {ledger.source}: it agrees with what the portal holds."}
            why = f"the file {ledger.source} differs from what the portal holds"
    return from_db(), {"source": "database", "parity": answer, "note": f"{from_portal}: {why}."}
