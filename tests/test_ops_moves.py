"""Ops engine: the stock ledger and the item name bridge, and the movement audit built on them (/yq-moves).

    python -m tests.test_ops_moves

Same lightweight runner as the other suites (no pytest). No database, no network, no .env
(SUPABASE_URL / SUPABASE_KEY / DATABASE_URL are pinned to ci.invalid before any import). The stock
ledger is a workbook built here, in a temporary folder, in the layout Focus exports (title block,
band labels, header row, one group per item with its Sub Total, an Opening Balance row, a Grand
Total) and read by the REAL reader and parser of scripts/ingest.py. All names and numbers are
invented.

Covered:
  * ledger: every movement is read (no opening balance, no total row); quantities and values are
    Decimal, held to three decimals (a float's stray digits never reach a row, the cache or a
    total); a leg with units and no value; a changed column layout stops the read; a file the
    reader cannot open (an old-style .xls, a damaged one) ends in one plain sentence; find_file
    (the newest, never Excel's lock file) and loaded_folder;
  * ledger, privacy: narration is kept for the two legs of a transfer only, masked, as a hash in
    letters; the words and the ID-shaped numbers typed into invoices are in no row and no cache;
  * ledger cache: written, reused without opening the workbook, rebuilt when the file or the code
    that shapes the rows changes or the cache cannot be trusted; a cache that cannot be written is
    said, never swallowed; old caches are removed (this workbook's and the newest other one stay);
  * ledger: one name per warehouse (alias, case, spacing) and is_main; balances per warehouse and
    item (as on a date) and the day series of one warehouse in one pass; whole vouchers with the
    units that state no value; the database fallback shaped the same way; parity with production
    on one aggregated read, per voucher type and per warehouse, the dates first in the sentence;
    for_audit (the file when it is proven equal to production, else the database);
  * items: the name bridge (longest known name, whole words, exceptions that must point at a known
    name) and the accessory rule (ever moved in the main store, less the config list; it stops on a
    main store that matches no warehouse and on a list given no bridge);
  * the real 7-Oct ledger, when it is on this machine (SKIP otherwise); its golden figures stand
    for one export and are set aside when the folder is published again from another.

The movement audit (scripts.ops.moves), on an invented ledger of rows shaped as the ledger shapes
them, with every outcome of a transfer in one week (received, late, elsewhere, other quantities,
twins, not received yet, not received, the damage store's week, a receipt with no issue, a voucher
set aside), sales, returns, counts, a supplier receipt in an outlet and SIM stock; the reads behind
the cost (snapshot, price book, MRN landed costs, pricing sheet) and the ledger's source answered by
stand-ins:
  * the words (windows, rep:, again/top refused) and the van or outlet a typed name means;
  * every store's check is nought in units and in BHD, and a movement put in no column shows "to
    check"; the split by the store that actually booked a transfer in; issued_to beside received;
  * shares, behaviour counts (Friday vouchers, back-dated numbers, round trips, re-issues, month-end
    returns), the valuation at /yq-stock's basis (MRN, sheet, receipt rate, none counted apart);
  * the items to check: kinds, fields, BHD, order (no cost last), the store asked for;
  * per-store figures held back without parity, the closing lines, no 9- or 15-digit runs;
  * the API other commands build on: audit(None) with every exception, sha, memo reuse,
    period_table (by kind), store_balances, books_only, brief_facts, exception_items, the order
    position re-exported once it is there; the command through the entry point; the hidden golden
    build on a fixture.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

from scripts.ops import db  # noqa: E402  (first, as every command does)
from scripts.ops import __main__ as cli  # noqa: E402
from scripts.ops import config, fmt, items, ledger, moves, pairing, paths, status, stock  # noqa: E402

TESTS: list[tuple[str, object]] = []

# The project folder (the main checkout when this is a code worktree): only the last test reads it.
REAL_ROOT = paths.data_root()
REAL_FOLDER = REAL_ROOT / "business_data" / "Focus ERP 2026-10-07"

NINE = re.compile(r"(?<![0-9])[0-9]{9}(?![0-9])")            # shaped like a personal ID number
LONG = re.compile(r"(?<![0-9])[0-9]{15,}(?![0-9])")          # shaped like a card or account number

MAIN = "Main Store"
FIXTURE = {
    "warehouses.main": MAIN,
    "warehouses.aliases": {"Shop 1 S-1": "Shop 1"},
    "items.bridge_exceptions": {},
    "items.not_accessories": [],
}

# Ledger names are "<name as the other reports have it> <item code>"; a code can hold a space.
CABLE = "ITEM-01 Cable One C 01"
CHARGER = "ITEM-02 Charger Two CH2"
PACK = "ITEM-03 Starter Pack SP3"             # never in the main store; opens with a balance
HANDSET = "ITEM-04 Handset HS4"               # moves in the main store; config can take it out
EJECTOR = "ITEM-05 Sim Ejector Cable SE5"     # an accessory with "sim" in its name
REEL = "ITEM-06 Reel RL6"                     # only in the workbook of the stray-digits test
KNOWN = ["ITEM-01 Cable One", "ITEM-02 Charger Two", "ITEM-03 Starter Pack", "ITEM-04 Handset",
         "ITEM-05 Sim Ejector Cable"]
OLD_XLS = bytes.fromhex("d0cf11e0a1b11ae1") + bytes(504)      # how an old-style .xls begins: not a zip, so not an .xlsx
# What a float sum looks like written out: 0.1 + 0.2, and 1.1 times 3. Put together here so that this
# file holds no long digit run (the scan for card numbers reads one as such).
STRAY = "0.3" + "0" * 15 + "4"
STRAY_3 = "3.3" + "0" * 14 + "3"

# Invented digit runs, put together here so that this file holds none: two that look like personal
# ID numbers and share their last three digits, and one that looks like a card number.
ID_A = "12345" + "6789"
ID_B = "98765" + "4789"
CARD = "4" + "1" * 15
# Every narration carries a word found nowhere else, so a leak of the text is easy to see.
WORDS = ("zebra", "walrus", "quokka", "yak", "ibis", "lemur")

HEADER = ["Date", "Voucher", "Received Quantity", "Rate", "Issued Quantity", "Rate", "Balance Quantity",
          "Value", "Value", "Value", "Avg Rate", "Warehouse Name", "To Warehouse Name", "Narration", "Voucher name"]
NUMBERS = ("qty_in", "qty_out", "value_in", "value_out")


def day(month: int, dom: int) -> datetime:
    return datetime(2026, month, dom)


# item -> (day, voucher, voucher type, warehouse, to warehouse, units in, units out, rate, narration)
# A rate of None is a leg Focus exports with its units and no rate or value (receive legs, often).
MOVES: dict[str, list[tuple]] = {
    CABLE: [
        (day(1, 5), "MRN:T-26-01-1", ledger.SUPPLIER_RECEIPT, MAIN, None, 100, 0, 1, None),
        (day(1, 10), "STRV:T-26-01-1", ledger.RECEIVE, "Van A", None, 10, 0, 1, "zebra run"),
        (day(1, 10), "SIO:T-26-01-1", ledger.ISSUE, MAIN, "Van A", 0, 6, 1, "Zebra  Run"),
        (day(1, 10), "SIO:T-26-01-1", ledger.ISSUE, MAIN, "Van A", 0, 4, 1, "Zebra  Run"),
        (day(1, 12), "SIO:T-26-01-2", ledger.ISSUE, MAIN, "Van B", 0, 20, 1, None),
        (day(1, 13), "STRV:T-26-01-3", ledger.RECEIVE, "Van B", None, 20, 0, None, None),
        (day(1, 15), "SI:SI-T-26-01-1", ledger.SALE, "Van A", None, 0, 3, 1, f"walrus {ID_A} card {CARD}"),
        (day(1, 20), "SRV:T-26-01-1", ledger.SALES_RETURN, "Van A", None, 1, 0, 1, f"quokka {ID_A}"),
        (day(2, 2), "STRV:T-26-02-1", ledger.RECEIVE, MAIN, None, 2, 0, 1, f"LEMUR  {ID_B}"),
        (day(2, 2), "SIO:T-26-02-1", ledger.ISSUE, "Van A", MAIN, 0, 2, 1, f"lemur {ID_A}"),
    ],
    CHARGER: [
        (day(1, 5), "MRN:T-26-01-1", ledger.SUPPLIER_RECEIPT, MAIN, None, 50, 0, 2, None),
        (day(1, 10), "STRV:T-26-01-1", ledger.RECEIVE, "Van A", None, 5, 0, 2, "zebra run"),
        (day(1, 10), "SIO:T-26-01-1", ledger.ISSUE, MAIN, "Van A", 0, 5, 2, "Zebra  Run"),
        (day(1, 25), "ShoStk:T-26-01-1", ledger.COUNT_SHORT, MAIN, None, 0, 2, 2, "ibis count"),
        (day(1, 26), "STRV:T-26-01-4", ledger.RECEIVE, "Shop 1", None, 8, 0, 2, None),
        (day(1, 26), "SIO:T-26-01-4", ledger.ISSUE, MAIN, "SHOP 1  S-1", 0, 8, 2, None),
        (day(1, 28), "SI:SI-T-26-01-4", ledger.SALE, "SHOP 1  S-1", None, 0, 3, 2, None),
    ],
    PACK: [
        (day(1, 8), "STRV:T-26-01-9", ledger.RECEIVE, "Van A", None, 30, 0, 1, "yak packs"),
        (day(1, 18), "SI:SI-T-26-01-2", ledger.SALE, "Van A", None, 0, 12, 1, None),
    ],
    HANDSET: [
        (day(1, 6), "MRN:T-26-01-2", ledger.SUPPLIER_RECEIPT, MAIN, None, 5, 0, 100, None),
        (day(1, 16), "SI:SI-T-26-01-3", ledger.SALE, MAIN, None, 0, 1, 100, ID_B),
    ],
    EJECTOR: [
        (day(1, 6), "MRN:T-26-01-2", ledger.SUPPLIER_RECEIPT, MAIN, None, 30, 0, 1, None),
    ],
}
OPENING = {PACK: 40}
N_MOVES = sum(len(rows) for rows in MOVES.values())          # 22
N_TYPES = len({row[2] for rows in MOVES.values() for row in rows})                # 6 voucher types
N_PAIRS = len({(row[2], row[3]) for rows in MOVES.values() for row in rows})      # 12: a type in a warehouse


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


def write_book(path: Path, moves: dict[str, list[tuple]], opening: dict | None = None,
               header: list[str] | None = None) -> None:
    """A Stock ledger workbook as Focus exports it: title block in column H, the band labels, the
    header, then per item a group row, an optional Opening Balance row, its movements and a Sub
    Total; a Grand Total closes the sheet. 'Balance Quantity' runs per item, as in the export."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Stock_ledger"
    pad = [None] * 7
    ws.append(pad + ["Example Trading W.L.L"])
    ws.append(pad + ["Stock ledger"])
    ws.append([])
    ws.append(pad + ["[As on date 02/02/2026]"])
    ws.append([None, None, None, "Received", None, "Issued", "Balance", "Received", "Issued", "Balance"])
    ws.append(HEADER if header is None else header)
    grand = [0, 0, 0]
    for item, rows in moves.items():
        ws.append([item])
        balance = (opening or {}).get(item, 0)
        if balance:
            ws.append([None, "Opening Balance", None, None, None, None, balance, None, None, balance, 1])
        total_in = total_out = 0
        for when, voucher, vtype, wh, to_wh, qty_in, qty_out, rate, narration in rows:
            balance += qty_in - qty_out
            total_in += qty_in
            total_out += qty_out
            if rate is None:                  # units, and the rate and value cells left blank
                ws.append([when, voucher, qty_in or None, None, qty_out or None, None, balance, None, None, None,
                           None, wh, to_wh, narration, vtype])
                continue
            ws.append([when, voucher, qty_in or None, rate if qty_in else None, qty_out or None,
                       rate if qty_out else None, balance, qty_in * rate or None, qty_out * rate or None,
                       balance * rate, rate, wh, to_wh, narration, vtype])
        ws.append(["Sub Total", None, total_in, None, total_out, None, balance])
        grand = [grand[0] + total_in, grand[1] + total_out, grand[2] + balance]
    ws.append(["Grand Total", None, grand[0], None, grand[1], None, grand[2]])
    wb.save(path)
    wb.close()


def store_as_excel_does(path: Path, swaps: dict[str, str]) -> int:
    """Rewrite number cells of a saved workbook: every cell holding `short` gets `long` instead.

    Excel stores a float with all its 17 digits, so a real export can hold 0.1 + 0.2 as it is in
    memory (STRAY). openpyxl rounds to 16 when it WRITES (that cell is saved as 0.3), so a workbook
    built here can only hold such a number when it is put into the sheet's XML afterwards. Returns
    the cells changed."""
    with zipfile.ZipFile(path) as z:
        parts = [(info.filename, z.read(info.filename)) for info in z.infolist()]
    changed = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts:
            if name.startswith("xl/worksheets/"):
                text = data.decode("utf-8")
                for short, long in swaps.items():
                    changed += text.count(f"<v>{short}</v>")
                    text = text.replace(f"<v>{short}</v>", f"<v>{long}</v>")
                data = text.encode("utf-8")
            z.writestr(name, data)
    return changed


class data_root:
    """A temporary data root with the test config."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        paths.set_data_root(self.root)
        config.use(FIXTURE)
        return self.root

    def __exit__(self, *exc):
        paths.set_data_root(None)
        config.use(None)
        for _ in range(20):
            try:
                self._tmp.cleanup()
                break
            except OSError:               # Windows: a scanner still holds a file written a moment ago
                time.sleep(0.1)
        return False


class loaded_set(data_root):
    """The same, with the ledger workbook in a loaded folder that state.json names."""

    def __enter__(self) -> tuple[Path, Path]:
        root = super().__enter__()
        folder = root / "business_data" / "Focus ERP 2026-02-02"
        folder.mkdir(parents=True)
        book = folder / "Stock_ledger1_Example.xlsx"
        write_book(book, MOVES, OPENING)
        status.write_state({"last_load": {"run": "020226_0900", "published": folder.name}})
        return root, book


def spec_rows(moves: dict | None = None) -> list[dict]:
    """The movements (MOVES, or `moves`) as the stock_movements table holds them: column names, date
    and numeric types. A quantity given as a float is held with every digit of that float, as a
    numeric column does for what a float wrote into it."""
    out = []
    for item, rows in (MOVES if moves is None else moves).items():
        for when, voucher, vtype, wh, to_wh, qty_in, qty_out, rate, text in rows:
            valued = rate is not None
            out.append({
                "item_name": item, "move_date": when.date(), "voucher": voucher, "voucher_type": vtype,
                "warehouse_name": wh, "to_warehouse_name": to_wh,
                "received_qty": Decimal(qty_in) if qty_in else None,
                "issued_qty": Decimal(qty_out) if qty_out else None,
                "received_value_bhd": Decimal(qty_in * rate).quantize(Decimal("0.001")) if qty_in and valued else None,
                "issued_value_bhd": Decimal(qty_out * rate).quantize(Decimal("0.001")) if qty_out and valued else None,
                "narration": text,
            })
    return out


def production_totals() -> list[dict]:
    """What the parity statement returns when production holds exactly these movements: one line
    per voucher type and warehouse, sums and counts as text."""
    acc: dict[tuple, dict] = {}
    for r in spec_rows():
        a = acc.setdefault((r["voucher_type"], r["warehouse_name"]),
                           {"in": Decimal(0), "out": Decimal(0), "n": 0, "days": set()})
        a["in"] += r["received_qty"] or 0
        a["out"] += r["issued_qty"] or 0
        a["n"] += 1
        a["days"].add(r["move_date"])
    return [{"vtype": vtype, "wh": wh, "units_in": str(a["in"]), "units_out": str(a["out"]),
             "line_count": str(a["n"]), "day_count": str(len(a["days"])),
             "first_date": min(a["days"]).isoformat(), "last_date": max(a["days"]).isoformat()}
            for (vtype, wh), a in acc.items()]


def order(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["item"], r["date"], r["voucher"], r["qty_in"], r["qty_out"]))


def narr_of(rows: list[dict], voucher: str):
    """The one narration hash (or None) every line of this voucher carries."""
    found = {r["narr"] for r in rows if r["voucher"] == voucher}
    assert len(found) == 1, (voucher, found)
    return found.pop()


def leaks(blob: str) -> list[str]:
    """What must never be in a row, a cache or a printed ledger: narration words, masked or not,
    and digit runs shaped like an ID or a card number. A file hash is told apart by its 'sha256:'
    prefix and left out of the scan (its digits are chance)."""
    blob = re.sub(r"sha256:[0-9a-f]{64}", "sha256:(hash)", blob)
    low = blob.lower()
    found = [w for w in WORDS + (ID_A, ID_B, CARD, "***") if w in low]
    if NINE.search(blob):
        found.append("a 9-digit run")
    if LONG.search(blob):
        found.append("a run of 15 digits or more")
    return found


# ── ledger: reading ───────────────────────────────────────────────────────────

@test("ledger: the real parser reads the Focus layout: every movement, no opening balance, no total row")
def _():
    with loaded_set() as (root, book):
        led = ledger.load(book, use_cache=False)
        assert len(led.rows) == N_MOVES == 22, len(led.rows)
        assert led.source == book.name and led.origin == "file" and led.sha.startswith("sha256:")
        assert led.first_date == date(2026, 1, 5) and led.last_date == date(2026, 2, 2)
        assert led.rows[0] == {
            "item": CABLE, "date": date(2026, 1, 5), "voucher": "MRN:T-26-01-1", "vtype": ledger.SUPPLIER_RECEIPT,
            "wh": MAIN, "to_wh": None, "qty_in": Decimal(100), "qty_out": Decimal(0), "value_in": Decimal(100),
            "value_out": Decimal(0), "narr": None}, led.rows[0]
        issue = [r for r in led.rows if r["voucher"] == "SIO:T-26-01-1" and r["item"] == CHARGER][0]
        assert (issue["wh"], issue["to_wh"], issue["qty_out"], issue["value_out"]) == (MAIN, "Van A", 5, 10)
        bare = [r for r in led.rows if r["voucher"] == "STRV:T-26-01-3"][0]
        assert (bare["qty_in"], bare["value_in"], bare["value_out"]) == (20, 0, 0), "units, and no value stated"
        sent = [r for r in led.rows if r["voucher"] == "SIO:T-26-01-4"][0]
        assert (sent["wh"], sent["to_wh"]) == (MAIN, "SHOP 1  S-1"), "a row keeps the spelling of the file"
        for r in led.rows:
            assert type(r["date"]) is date and all(isinstance(r[k], Decimal) for k in NUMBERS), r
            assert (r["qty_in"] == 0) != (r["qty_out"] == 0), "one of received / issued on every row"
        assert {r["item"] for r in led.rows} == set(MOVES)
        assert {r["vtype"] for r in led.rows} == {ledger.SUPPLIER_RECEIPT, ledger.ISSUE, ledger.RECEIVE,
                                                  ledger.SALE, ledger.SALES_RETURN, ledger.COUNT_SHORT}
        assert sum(r["qty_in"] for r in led.rows) == 261 and sum(r["qty_out"] for r in led.rows) == 66
        assert [(r["qty_in"], r["qty_out"]) for r in led.rows if r["item"] == PACK] == [(30, 0), (0, 12)], \
            "the Opening Balance row is not a movement"
        assert not (root / "exports").exists(), "use_cache=False leaves the cache alone"
        assert led.cache_note is None
        assert ledger.load(book.parent, use_cache=False).rows == led.rows, "a folder is searched for its ledger"
        raises(FileNotFoundError, lambda: ledger.load(root / "nothing.xlsx"))


@test("ledger: a changed column layout stops the read: nothing is guessed, nothing is cached")
def _():
    with loaded_set() as (root, book):
        moved = list(HEADER)
        moved[12], moved[13] = moved[13], moved[12]          # narration where a warehouse name is expected
        write_book(book, MOVES, OPENING, header=moved)
        exc = raises(ValueError, lambda: ledger.load())
        assert "stock_movements" in str(exc) and "header" in str(exc).lower()
        assert not leaks(str(exc)) and not list(root.rglob("ledger_*.json"))
        write_book(book, MOVES, OPENING)
        assert len(ledger.load().rows) == N_MOVES
        renamed = [dict(r) for r in spec_rows()]
        for r in renamed:
            r["qty_received"] = r.pop("received_qty")            # a parser that renames a column
        exc = raises(KeyError, lambda: ledger._shape(renamed, lambda text: text))
        assert "received_qty" in str(exc), "the read stops and names the column: no ledger of blanks"


@test("ledger: a file the reader cannot open (an old-style .xls, a damaged or half-copied one) ends in one plain sentence that names it")
def _():
    from scripts import ingest

    with loaded_set() as (root, book):
        folder, cache_folder = book.parent, root / "exports" / "ops" / "cache"
        good = ledger.load()
        ahead = time.time() + 100

        def put(name: str, data: bytes, rank: int) -> Path:
            """A file newer than the good workbook, so find_file takes it."""
            f = folder / name
            f.write_bytes(data)
            os.utime(f, (ahead + rank, ahead + rank))
            return f

        legacy = put("Stock_ledger2_Example.xls", OLD_XLS, 1)
        assert ledger.find_file(folder) == legacy, "named like the report, and the newest"
        exc = raises(FileNotFoundError, lambda: ledger.load())
        text = str(exc)
        assert isinstance(exc, ledger.NoLedgerFile), "a FileNotFoundError: the caller reads the database instead"
        assert text.startswith(f"the stock ledger file {legacy.name} could not be opened (BadZipFile); ")
        assert ".xlsx" in text and "old-style .xls" in text and "\n" not in text
        assert str(root) not in text and str(folder.name) not in text, "the file's name, never its path"
        assert not leaks(text) and fmt.careful(text) == text
        assert exc.__cause__ is None and exc.__suppress_context__, "the reader's own error is not shown behind it"
        shown = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert shown.rstrip().endswith(text), "what is printed ends in the sentence"
        assert "During handling" not in shown and "direct cause" not in shown and "not a zip file" not in shown
        assert sorted(p.name for p in cache_folder.iterdir()) == [ledger.cache_file(good.sha).name], "nothing is cached for it"
        for ask in (lambda: ledger.load(legacy), lambda: ledger.load(folder), lambda: ledger.load(legacy, use_cache=False)):
            assert str(raises(ledger.NoLedgerFile, ask)) == text, "the same sentence however the file is named"

        # other files that are no workbook: empty, plain text, half of a real one, a zip of something else
        packed = io.BytesIO()
        with zipfile.ZipFile(packed, "w") as z:
            z.writestr("notes.txt", "not a workbook")
        whole = book.read_bytes()
        for rank, (data, kind) in enumerate([(b"", "BadZipFile"), (b"plain text under the report's name", "BadZipFile"),
                                             (whole[: len(whole) // 2], "BadZipFile"), (packed.getvalue(), "")], start=2):
            bad = put("Stock_ledger3_Example.xlsx", data, rank)
            said = str(raises(ledger.NoLedgerFile, lambda: ledger.load()))
            assert said.startswith(f"the stock ledger file {bad.name} could not be opened ({kind}"), (rank, said)
            assert "\n" not in said and str(root) not in said
        assert len(list(cache_folder.iterdir())) == 1

        # a file that is there and Windows will not let be read
        real_sha = ledger.loadplan.sha256_file

        def held(path):
            raise PermissionError(13, "Permission denied", str(path))
        ledger.loadplan.sha256_file = held
        try:
            said = str(raises(ledger.NoLedgerFile, lambda: ledger.load(book)))
        finally:
            ledger.loadplan.sha256_file = real_sha
        assert said.startswith(f"the stock ledger file {book.name} could not be opened (PermissionError); ")
        assert str(root) not in said

        # the reader's own words can quote a cell: only the kind of its error is kept. A fault of this
        # PC (a missing library, no memory) is not the file's, and is said as it is.
        real_read = ingest.read_grid
        try:
            def quoting(path):
                raise RuntimeError(f"cell N7 holds zebra {ID_A}")
            ingest.read_grid = quoting
            said = str(raises(ledger.NoLedgerFile, lambda: ledger.load(book, use_cache=False)))
            assert "(RuntimeError)" in said and not leaks(said)
            for kind in (ImportError, MemoryError):
                def broken(path, kind=kind):
                    raise kind("not the file's doing")
                ingest.read_grid = broken
                assert not isinstance(raises(kind, lambda: ledger.load(book, use_cache=False)), FileNotFoundError)
        finally:
            ingest.read_grid = real_read

        legacy.unlink()
        bad.unlink()
        assert ledger.load().rows == good.rows, "with the unreadable files gone the good workbook is read again"


@test("ledger: find_file takes the newest Stock_ledger workbook, never Excel's lock file; loaded_folder follows state.json")
def _():
    with data_root() as root:
        assert ledger.loaded_folder() is None and ledger.find_file(None) is None
        base = root / "business_data"
        old, new = base / "Focus ERP 2026-01-10", base / "Focus ERP 2026-02-02"
        old.mkdir(parents=True)
        new.mkdir()
        (base / "ops").mkdir()
        stamp = time.time() - 1000
        for i, name in enumerate(["Stock_ledger1_X.xlsx", "stock_LEDGER2_X.xlsx", "~$Stock_ledger3_X.xlsx",
                                  "Stock_balance_by_warehouse4_X.xlsx", "Stock_ledger5_X.txt"]):
            (old / name).write_bytes(b"x")
            os.utime(old / name, (stamp + i * 10, stamp + i * 10))
        (old / "Stock_ledger9_X.xlsx").mkdir()                       # a folder is not a report
        assert ledger.find_file(old).name == "stock_LEDGER2_X.xlsx", "not the lock file, not another report"
        os.utime(old / "Stock_ledger1_X.xlsx", (stamp + 100, stamp + 100))
        assert ledger.find_file(old).name == "Stock_ledger1_X.xlsx", "the newest by its time, not the last by its name"
        assert ledger.find_file(new) is None and ledger.find_file(root / "nowhere") is None
        assert ledger.loaded_folder() == new, "with no state: the newest 'Focus ERP 20*' folder"
        status.write_state({"last_load": {"published": old.name}})
        assert ledger.loaded_folder() == old
        for named in ("Focus ERP 2026-03-01", "..", "ops/..", ""):  # not there, or not a folder of business_data
            status.write_state({"last_load": {"published": named}})
            assert ledger.loaded_folder() == new, named
        for odd in ({"last_load": old.name}, {"last_load": [old.name]}, {"last_load": {"published": 7}}):
            status.write_state(odd)                                 # a last_load that is not what the writer leaves
            assert ledger.loaded_folder() == new, odd
        for text in ("[1, 2]", '"Focus ERP 2026-01-10"', "{not json"):   # a state file that is no object at all
            paths.write_text(paths.state_file(), text)
            assert ledger.loaded_folder() == new, text
        exc = raises(FileNotFoundError, lambda: ledger.load())
        assert "Stock_ledger" in str(exc) and new.name in str(exc)


# ── ledger: privacy ───────────────────────────────────────────────────────────

@test("ledger: narration never survives: no words and no ID-shaped number in a row, in the cache or in a printed ledger")
def _():
    with loaded_set() as (root, book):
        led = ledger.load()
        cache = ledger.cache_file(led.sha)
        assert cache.is_file() and cache.parent == root / "exports" / "ops" / "cache"
        assert cache.stem.split("_", 1)[1].isalpha() and len(cache.stem) == len("ledger_") + 32
        assert leaks(" ".join([ID_A, "zebra"])) == ["zebra", ID_A, "a 9-digit run"], "the scan itself works"
        assert leaks(CARD) == [CARD, "a run of 15 digits or more"] and leaks("******789") == ["***"]
        for what, blob in (("cache", cache.read_text(encoding="utf-8")), ("rows", repr(led.rows)),
                           ("ledger", repr(led)), ("cache name", cache.name)):
            assert not leaks(blob), (what, leaks(blob))
        for r in led.rows:
            if r["vtype"] not in ledger.NARRATED:
                assert r["narr"] is None, r
            elif r["narr"] is not None:
                assert len(r["narr"]) == ledger.NARR_LEN and set(r["narr"]) <= ledger.HASH_LETTERS, r
        assert len(ledger.HASH_LETTERS) == 16 and "".join(sorted(ledger.HASH_LETTERS)).isalpha(), "letters, no digit"
        again = ledger.load()
        assert again.origin == "cache" and not leaks(repr(again))


@test("ledger: two legs typed with the same words get the same hash; a blank narration is None; the mask comes before the hash")
def _():
    with loaded_set() as (_root, _book):
        rows = ledger.load().rows
        out_leg, in_leg = narr_of(rows, "SIO:T-26-01-1"), narr_of(rows, "STRV:T-26-01-1")
        assert out_leg is not None and out_leg == in_leg, "same words, other spacing and capitals"
        assert narr_of(rows, "SIO:T-26-01-2") is None and narr_of(rows, "STRV:T-26-01-3") is None
        back_out, back_in = narr_of(rows, "SIO:T-26-02-1"), narr_of(rows, "STRV:T-26-02-1")
        assert back_out is not None and back_out == back_in, "two numbers alike in their last three digits"
        assert ID_A != ID_B and len({out_leg, back_out, narr_of(rows, "STRV:T-26-01-9")}) == 3
        for voucher in ("SI:SI-T-26-01-1", "SRV:T-26-01-1", "ShoStk:T-26-01-1", "SI:SI-T-26-01-3"):
            assert narr_of(rows, voucher) is None, voucher      # narrated in the file, never read


# ── ledger: the cache ─────────────────────────────────────────────────────────

@test("ledger cache: written on the first read, reused without opening the workbook, rebuilt when it cannot be trusted or the file changes")
def _():
    from scripts import ingest

    opened: list[str] = []
    real_read = ingest.read_grid

    def counting(path):
        opened.append(Path(path).name)
        return real_read(path)

    with loaded_set() as (_root, book):
        ingest.read_grid = counting
        try:
            first = ledger.load()
            cache = ledger.cache_file(first.sha)
            assert first.origin == "file" and opened == [book.name] and cache.is_file() and first.cache_note is None
            second = ledger.load()
            assert second.origin == "cache" and len(opened) == 1, "the workbook is not opened again"
            assert second.rows == first.rows and second.sha == first.sha and second.source == book.name
            assert (second.first_date, second.last_date) == (first.first_date, first.last_date)
            assert second.cache_note is None
            assert all(type(r["date"]) is date and all(type(r[k]) is Decimal for k in NUMBERS) for r in second.rows), \
                "a cached row is typed like a parsed one: dates, and Decimal for every number"

            paths.write_text(cache, "{not json")
            assert ledger.load().origin == "file" and len(opened) == 2, "an unreadable cache is rebuilt"
            good = json.loads(cache.read_text(encoding="utf-8"))
            assert good["version"] == ledger.CACHE_VERSION and len(good["rows"]) == N_MOVES
            assert good["code"] == ledger._code_key() and good["code"].isalpha()
            word = "confidential"                                    # as long as a hash, and not one
            assert len(word) == ledger.NARR_LEN and word.isalpha()
            for change in ({"version": ledger.CACHE_VERSION + 1}, {"sha": "another file"}, {"code": "other code"},
                           {"rows": [[0, 0]]}, {"narrs": ["words not a hash"] + good["narrs"][1:]},
                           {"narrs": [word] + good["narrs"][1:]}, {"dates": ["not a date"]}):
                paths.write_text(cache, json.dumps(dict(good, **change)))
                before = len(opened)
                assert ledger.load().rows == first.rows and len(opened) == before + 1, change
                assert ledger.load().origin == "cache" and len(opened) == before + 1, change
            unmarked = {k: v for k, v in good.items() if k != "code"}        # a cache from before the code was bound
            paths.write_text(cache, json.dumps(unmarked))
            assert ledger.load().origin == "file" and ledger.load().origin == "cache"

            more = dict(MOVES)
            more[EJECTOR] = MOVES[EJECTOR] + [(day(1, 30), "SI:SI-T-26-01-9", ledger.SALE, MAIN, None, 0, 7, 1, None)]
            write_book(book, more, OPENING)
            before = len(opened)
            changed = ledger.load()
            assert changed.origin == "file" and len(opened) == before + 1 and changed.sha != first.sha
            assert len(changed.rows) == N_MOVES + 1 and ledger.cache_file(changed.sha) != cache
            assert ledger.load().origin == "cache" and ledger.load().rows == changed.rows
            assert ledger.load(use_cache=False).origin == "file"
        finally:
            ingest.read_grid = real_read


@test("ledger cache: it belongs to the code that shaped its rows: a changed parser or ledger module is never served old rows")
def _():
    assert [p.name for p in ledger.CODE_SOURCES] == ["ingest.py", "ledger.py"]
    assert all(p.is_file() for p in ledger.CODE_SOURCES) and ledger.CODE_SOURCES[0].parent.name == "scripts"
    key = ledger._code_key()
    assert key == ledger._code_key() and len(key) == 32 and set(key) <= ledger.HASH_LETTERS

    real_sources, real_key = ledger.CODE_SOURCES, ledger._code_key
    with loaded_set() as (root, _book):
        parser, module = root / "parser.py", root / "module.py"
        parser.write_bytes(b"one")
        module.write_bytes(b"two")
        try:
            ledger.CODE_SOURCES = (parser, module)
            before = ledger._code_key()
            assert before != key and before == ledger._code_key()
            parser.write_bytes(b"one, changed")
            after_parser = ledger._code_key()
            module.write_bytes(b"two, changed")
            assert len({before, after_parser, ledger._code_key()}) == 3, "either file changes the key"
            module.unlink()
            assert ledger._code_key() is None, "a source that cannot be read: no key"
        finally:
            ledger.CODE_SOURCES = real_sources

        first = ledger.load()
        assert first.origin == "file" and ledger.load().origin == "cache"
        try:
            ledger._code_key = lambda: "shapedbyothercode"
            assert ledger.load().origin == "file", "rows shaped by other code are parsed again"
            assert ledger.load().origin == "cache" and ledger.load().rows == first.rows
            ledger._code_key = lambda: None
            blind = ledger.load()
            assert blind.origin == "file" and blind.rows == first.rows, "no key: the workbook is read, no cache is trusted"
            assert blind.cache_note and "cache is not used" in blind.cache_note and "slow" in blind.cache_note
        finally:
            ledger._code_key = real_key
        assert ledger.load().origin == "file", "and the cache the other code left is not served to this one"
        assert ledger.load().origin == "cache"


@test("ledger cache: one that cannot be written is said in cache_note, never swallowed; the rows still come back")
def _():
    with loaded_set() as (root, _book):
        real_write = paths.write_text
        try:
            for kind in (OSError, PermissionError):
                def refuse(path, text, kind=kind):
                    raise kind(28, "no room")
                paths.write_text = refuse
                first, second = ledger.load(), ledger.load()
                for led in (first, second):
                    assert led.origin == "file" and len(led.rows) == N_MOVES, "no cache: the workbook is read each time"
                    assert led.cache_note, "a cache that could not be written must be said"
                    assert f"could not be written ({kind.__name__})" in led.cache_note and "slow" in led.cache_note
                    assert not leaks(led.cache_note) and str(root) not in led.cache_note
        finally:
            paths.write_text = real_write
        assert not list(root.rglob("ledger_*.json"))
        healthy = ledger.load()
        assert healthy.origin == "file" and healthy.cache_note is None
        assert ledger.load().origin == "cache" and ledger.load().cache_note is None


@test("ledger cache: after a read the folder keeps this workbook's cache and the newest other one; older ones go, and nothing else is touched")
def _():
    with loaded_set() as (root, _book):
        folder = root / "exports" / "ops" / "cache"
        folder.mkdir(parents=True)
        stamp = time.time() - 50000

        def other(letter: str, rank: int) -> Path:
            """The cache of another export, named as cache_file() names one; a higher rank is newer."""
            f = folder / f"ledger_{letter * 32}.json"
            f.write_text("{}", encoding="utf-8")
            os.utime(f, (stamp + rank * 60, stamp + rank * 60))
            return f

        def names() -> set[str]:
            return {p.name for p in folder.iterdir()}

        a, b, c, d = (other(letter, rank) for rank, letter in enumerate("abcd"))
        # Not this module's to remove, though each is older than all of the above: other names (a
        # letter or a digit no hash holds, a key too short or too long), a temp file, another report's
        # cache, and a folder named like a cache.
        strangers = [folder / name for name in (
            "ledger_notes.json", f"ledger_{'q' * 32}.json", f"ledger_{'1' * 32}.json", f"ledger_{'a' * 31}.json",
            f"ledger_{'a' * 33}.json", f"ledger_{'a' * 32}.json.tmp", f"ledger_{'a' * 32}.txt", f"accounts_{'a' * 32}.json",
            "other.json")]
        for f in strangers:
            f.write_text("{}", encoding="utf-8")
            os.utime(f, (stamp - 9000, stamp - 9000))
        shelf = folder / f"ledger_{'e' * 32}.json"
        shelf.mkdir()
        spared = {f.name for f in strangers} | {shelf.name}
        assert all(ledger._is_cache_name(f.name) for f in (a, b, c, d, shelf))
        assert not any(ledger._is_cache_name(f.name) for f in strangers)

        first = ledger.load()
        mine = ledger.cache_file(first.sha)
        assert first.origin == "file" and first.cache_note is None and mine.parent == folder
        assert ledger._is_cache_name(mine.name)
        assert names() == spared | {mine.name, d.name}, "the one just written and the newest other one"

        # a read served from the cache clears up as well; the newest is by its time, not by its name
        a, b = other("a", 9), other("b", 1)
        assert ledger.load().origin == "cache" and names() == spared | {mine.name, a.name}

        # this workbook's cache stays however old it is
        os.utime(mine, (stamp - 99999, stamp - 99999))
        c = other("c", 20)
        assert ledger.load().origin == "cache" and names() == spared | {mine.name, c.name}

        # use_cache=False leaves the folder alone
        a, b = other("a", 1), other("b", 2)
        crowded = names()
        assert crowded == spared | {mine.name, a.name, b.name, c.name}
        assert ledger.load(use_cache=False).origin == "file" and names() == crowded

        # a cache that could not be written clears nothing: there is no "one just written"
        mine.unlink()
        real_write = paths.write_text

        def refuse(path, text):
            raise OSError(28, "no room")
        paths.write_text = refuse
        try:
            unsaved = ledger.load()
        finally:
            paths.write_text = real_write
        assert unsaved.origin == "file" and unsaved.cache_note and names() == crowded - {mine.name}

        # every removal goes through paths.remove_file (its guard); one that fails is left for the next read
        asked: list[str] = []
        real_remove = paths.remove_file

        def stubborn(path):
            asked.append(Path(path).name)
            if Path(path).name == a.name:
                raise PermissionError(13, "held by another program")
            return real_remove(path)
        paths.remove_file = stubborn
        try:
            led = ledger.load()
        finally:
            paths.remove_file = real_remove
        assert led.origin == "file" and len(led.rows) == N_MOVES and led.cache_note is None, "the rows are good either way"
        assert sorted(asked) == sorted([a.name, b.name]), "the two oldest, and no stranger"
        assert names() == spared | {mine.name, c.name, a.name}
        assert ledger.load().origin == "cache" and names() == spared | {mine.name, c.name}, "the next read removes it"
        assert ledger._prune_cache(mine) == 0 and names() == spared | {mine.name, c.name}, "nothing left to remove"
        assert ledger._prune_cache(root / "nowhere" / mine.name) == 0, "no folder: nothing to do, no error"


# ── ledger: warehouse names, balances and vouchers ────────────────────────────

@test("ledger: one name per warehouse (alias, case, spacing); is_main finds the main store however config spells it")
def _():
    with loaded_set() as (_root, _book):
        rows = ledger.load().rows
        bal = ledger.balances(rows)
        assert ledger.warehouse_names(rows) == {MAIN: MAIN, "Van A": "Van A", "Van B": "Van B", "Shop 1": "Shop 1",
                                                "SHOP 1  S-1": "Shop 1"}
        assert ledger.warehouse("shop 1   s-1") == "Shop 1" and ledger.warehouse("Van A") == "Van A"

        # one van typed two ways in the rows, with no alias for either: one key, the first spelling
        twin = [dict(r, wh="VAN  a") if r["wh"] == "Van A" and r["vtype"] == ledger.SALE else r for r in rows]
        assert {"Van A", "VAN  a"} <= {r["wh"] for r in twin}
        assert ledger.warehouse_names(twin)["VAN  a"] == "Van A" and ledger.balances(twin) == bal
        assert ledger.main_items(twin, main="van a") == {CABLE, CHARGER, PACK}

        # the name is the same for every as_of and for every voucher type asked of the same rows
        last = [r for r in rows if r["voucher"] == "SIO:T-26-02-1"][0]             # the van's last row, put first
        mixed = [dict(last, wh="VAN A")] + [r for r in rows if r is not last]
        whole, january = ledger.balances(mixed), ledger.balances(mixed, as_of=date(2026, 1, 31))
        assert whole[("VAN A", CABLE)] == 6 and january[("VAN A", CABLE)] == 8
        assert not [k for k in list(whole) + list(january) if k[0] == "Van A"]
        sent = ledger.vouchers(mixed, [ledger.ISSUE])["SIO:T-26-01-1"]
        got = ledger.vouchers(mixed, [ledger.RECEIVE])["STRV:T-26-01-1"]
        assert sent["to_wh"] == got["wh"] == "VAN A", "where it was sent equals where it was received, with =="

        # the name config writes wins over a spelling of the file that only differs in case
        shouted = [dict(r, wh="SHOP 1") if r["wh"] == "Shop 1" else r for r in rows]
        assert ledger.warehouse_names(shouted)["SHOP 1"] == "Shop 1" and ledger.balances(shouted) == bal

        assert ledger.is_main(MAIN) and ledger.is_main("  main   STORE ")
        assert not ledger.is_main("Van A") and not ledger.is_main("") and not ledger.is_main(None)
        assert ledger.is_main("SHOP 1  S-1", main="shop 1") and ledger.is_main("Shop 1", main="Shop 1 S-1")
        raises(ValueError, lambda: ledger.is_main(MAIN, main="  "))
        assert ledger.same_warehouse("Shop 1 S-1", "SHOP  1") and ledger.same_warehouse("van a", "Van A")
        assert not ledger.same_warehouse("Van A", "Van B") and not ledger.same_warehouse("Van A", "Van")
        assert not ledger.same_warehouse("", "") and not ledger.same_warehouse(None, "Van A"), "a blank name is no warehouse"

        config.use(dict(FIXTURE, **{"warehouses.main": "main  STORE"}))          # config in another case than the ledger
        assert ledger.main_items(rows) == items.accessory_items(rows) == {CABLE, CHARGER, HANDSET, EJECTOR}
        held = {k: v for k, v in ledger.balances(rows).items() if ledger.is_main(k[0])}
        assert held == {(MAIN, CABLE): 72, (MAIN, CHARGER): 35, (MAIN, HANDSET): 4, (MAIN, EJECTOR): 30}, held
        assert not [k for k in ledger.balances(rows) if k[0] == config.get("warehouses.main")], \
            "== on the config spelling finds nothing: that is what is_main is for"


@test("ledger: balances per warehouse and item are received minus issued, with an alias applied and as on a date")
def _():
    with loaded_set() as (_root, _book):
        rows = ledger.load().rows
        bal = ledger.balances(rows)
        assert bal == {
            (MAIN, CABLE): 72, ("Van A", CABLE): 6, ("Van B", CABLE): 20,
            (MAIN, CHARGER): 35, ("Van A", CHARGER): 5, ("Shop 1", CHARGER): 5,
            ("Van A", PACK): 18, (MAIN, HANDSET): 4, (MAIN, EJECTOR): 30}, bal
        assert all(isinstance(v, Decimal) for v in bal.values())
        assert ledger.balances(iter(rows)) == bal, "any iterable of rows"

        config.use(dict(FIXTURE, **{"warehouses.aliases": {}}))        # without the alias: two names
        plain = ledger.balances(rows)
        assert plain[("Shop 1", CHARGER)] == 8 and plain[("SHOP 1  S-1", CHARGER)] == -3
        config.use(FIXTURE)

        january = ledger.balances(rows, as_of=date(2026, 1, 31))
        assert january[(MAIN, CABLE)] == 70 and january[("Van A", CABLE)] == 8
        early = ledger.balances(rows, as_of="2026-01-09")
        assert early == {(MAIN, CABLE): 100, (MAIN, CHARGER): 50, ("Van A", PACK): 30, (MAIN, HANDSET): 5,
                         (MAIN, EJECTOR): 30}, early
        assert ledger.balances(rows, as_of=date(2026, 1, 10))[("Van A", CABLE)] == 10, "the day itself counts"
        assert ledger.balances(rows, as_of=date(2025, 12, 31)) == {}
        raises(ValueError, lambda: ledger.balances(rows, as_of="soon"))
        sold_out = [r for r in rows if r["item"] == PACK] + [dict(rows[-1], item=PACK, wh="Van A", qty_in=Decimal(0),
                                                                  qty_out=Decimal(18))]
        assert ledger.balances(sold_out) == {("Van A", PACK): 0}, "moved and now at nothing: kept, with 0"
        assert ledger.first_date(rows) == date(2026, 1, 5) and ledger.last_date(rows) == date(2026, 2, 2)
        assert ledger.first_date([]) is None and ledger.last_date([]) is None


@test("ledger: day_end_balances gives each item's units at the end of every day in one warehouse, from one pass over the rows")
def _():
    with loaded_set() as (_root, _book):
        rows = ledger.load().rows
        first, last = date(2026, 1, 3), date(2026, 2, 4)        # two days before the first movement, two after the last
        days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
        every_item = sorted(MOVES)

        main = ledger.day_end_balances(rows, [CABLE, CHARGER], MAIN, first, last)
        assert list(main) == [CABLE, CHARGER] and all(len(series) == len(days) == 33 for series in main.values())
        assert all(type(units) is Decimal for series in main.values() for units in series)
        cable = dict(zip(days, main[CABLE]))
        assert [cable[date(2026, 1, d)] for d in (3, 4, 5, 9, 10, 11, 12, 13, 31)] == [0, 0, 100, 100, 90, 90, 70, 70, 70]
        assert [cable[date(2026, 2, d)] for d in (1, 2, 3, 4)] == [70, 72, 72, 72], "a quiet day repeats the day before"

        # the figure balances() gives for that day, for every item, in every warehouse, on every day:
        # inside a long window, and as a window of that one day (a movement on the first and the last
        # day of a window counts in it)
        for wh in (MAIN, "Van A", "Van B", "Shop 1"):
            series = ledger.day_end_balances(rows, every_item, wh, first, last)
            assert list(series) == every_item
            for n, when in enumerate(days):
                as_on = ledger.balances(rows, as_of=when)
                expected = {item: as_on.get((wh, item), 0) for item in every_item}
                assert {item: units[n] for item, units in series.items()} == expected, (wh, when)
                alone = ledger.day_end_balances(rows, every_item, wh, when, when)
                assert {item: units[0] for item, units in alone.items()} == expected, (wh, when, "alone")
        assert ledger.day_end_balances(rows, [CABLE], MAIN, date(2026, 1, 10), date(2026, 1, 12)) == {CABLE: [90, 90, 70]}, \
            "10 units out on the first day, 20 on the last: both are in"

        # one pass: the rows are walked once, and read no more for 400 days than for one
        class Walked:
            def __init__(self, inner):
                self.inner, self.walks = inner, 0

            def __iter__(self):
                self.walks += 1
                return iter(self.inner)

        class Row(dict):
            reads = 0

            def __getitem__(self, key):
                Row.reads += 1
                return dict.__getitem__(self, key)

        walked = Walked(rows)
        assert ledger.day_end_balances(walked, every_item, MAIN, first, last) == \
            ledger.day_end_balances(rows, every_item, MAIN, first, last)
        assert walked.walks == 1, "33 days, the rows walked once"
        assert ledger.day_end_balances((r for r in rows), [CABLE], MAIN, first, last) == {CABLE: main[CABLE]}, \
            "rows that can be walked only once are enough"
        watched = [Row(r) for r in rows]

        def reads_for(n_days: int) -> int:
            Row.reads = 0
            got = ledger.day_end_balances(watched, every_item, MAIN, last - timedelta(days=n_days - 1), last)
            assert all(len(series) == n_days for series in got.values())
            return Row.reads
        assert reads_for(1) == reads_for(400) <= len(rows) * 5, "the work on the rows does not grow with the days"

        # one warehouse, however a row or the caller spells it (alias, case, spacing)
        shop = ledger.day_end_balances(rows, [CHARGER], "shop 1  s-1", date(2026, 1, 25), date(2026, 1, 29))
        assert shop == {CHARGER: [0, 8, 8, 5, 5]}, shop
        assert ledger.day_end_balances(rows, [CHARGER], "SHOP 1", "2026-01-25", "2026-01-29") == shop, "dates as text too"

        # what moved before the first day is carried; an item that never moved there is 0 on every day
        late = ledger.day_end_balances(rows, [CABLE, PACK, "ITEM-09 Unknown U9"], MAIN, date(2026, 1, 11), date(2026, 1, 13))
        assert late == {CABLE: [90, 70, 70], PACK: [0, 0, 0], "ITEM-09 Unknown U9": [0, 0, 0]}, late

        # None for the warehouse is the main store; None for the items is every item that moved there by the last day
        whole = ledger.day_end_balances(rows, None, None, last, last)
        assert whole == {CABLE: [72], CHARGER: [35], HANDSET: [4], EJECTOR: [30]} and list(whole) == sorted(whole), whole
        assert ledger.day_end_balances(rows, None, "Van A", date(2026, 1, 9), date(2026, 1, 9)) == {PACK: [30]}
        assert ledger.day_end_balances(rows, None, "Van A", date(2026, 1, 1), date(2026, 1, 7)) == {}
        config.use(dict(FIXTURE, **{"warehouses.main": "main  STORE"}))             # config in another case than the ledger
        assert ledger.day_end_balances(rows, [CABLE], None, last, last) == {CABLE: [72]}
        config.use(FIXTURE)

        assert ledger.day_end_balances(rows, CABLE, MAIN, last, last) == {CABLE: [72]}, "one item given as text"
        assert list(ledger.day_end_balances(rows, [CHARGER, CABLE, CHARGER], MAIN, last, last)) == [CHARGER, CABLE]
        assert ledger.day_end_balances([], [CABLE], MAIN, first, first) == {CABLE: [0]}
        assert ledger.day_end_balances([], None, MAIN, first, first) == {}
        assert "before the first" in str(raises(ValueError, lambda: ledger.day_end_balances(rows, [CABLE], MAIN, last, first)))
        assert "not a date" in str(raises(ValueError, lambda: ledger.day_end_balances(rows, [CABLE], MAIN, "soon", last)))
        assert "not a date" in str(raises(ValueError, lambda: ledger.day_end_balances(rows, [CABLE], MAIN, first, None)))
        raises(ValueError, lambda: ledger.day_end_balances(rows, [CABLE], "  ", first, last))

        # a warehouse no row moved in stops it: 0 on every day would read as "out of stock all month"
        exc = raises(ValueError, lambda: ledger.day_end_balances(rows, [CABLE], "Van C", first, last))
        assert "no movement was found in the warehouse 'Van C'" in str(exc) and "spelt" in str(exc)
        raises(ValueError, lambda: ledger.day_end_balances(rows, None, "Van C", first, last))
        config.use(dict(FIXTURE, **{"warehouses.main": "Main Stor"}))               # a letter short in config
        exc = raises(ValueError, lambda: ledger.day_end_balances(rows, [CABLE], None, first, last))
        assert "the main store 'Main Stor' (config 'warehouses.main')" in str(exc)
        config.use(FIXTURE)
        away = [dict(r, to_wh="Damage Bay") if r["voucher"] == "SIO:T-26-01-2" else r for r in rows]
        exc = raises(ValueError, lambda: ledger.day_end_balances(away, None, "Damage Bay", first, last))
        assert "only ever sent to" in str(exc), "a store with no row of its own has no stock in the ledger"
        early = ledger.day_end_balances(rows, [CABLE], "Van B", date(2026, 1, 3), date(2026, 1, 11))
        assert early == {CABLE: [0] * 9}, "a store that moves later is known: before its first movement it holds 0"


@test("ledger: vouchers() gives whole vouchers: units per item, the value the file states, the units it leaves unvalued, in date order")
def _():
    with loaded_set() as (_root, _book):
        rows = ledger.load().rows
        issues = ledger.vouchers(rows, [ledger.ISSUE])
        assert list(issues) == ["SIO:T-26-01-1", "SIO:T-26-01-2", "SIO:T-26-01-4", "SIO:T-26-02-1"]
        first = issues["SIO:T-26-01-1"]
        assert first == {"vtype": ledger.ISSUE, "date": date(2026, 1, 10), "wh": MAIN, "to_wh": "Van A",
                         "lines": {CABLE: 10, CHARGER: 5}, "value": 20, "unvalued": 0,
                         "narr": narr_of(rows, "SIO:T-26-01-1"), "mixed": []}, first
        assert all(isinstance(first[k], Decimal) for k in ("value", "unvalued")) and isinstance(first["lines"][CABLE], Decimal)
        assert issues["SIO:T-26-01-2"]["narr"] is None and issues["SIO:T-26-01-2"]["lines"] == {CABLE: 20}
        assert (issues["SIO:T-26-02-1"]["wh"], issues["SIO:T-26-02-1"]["to_wh"]) == ("Van A", MAIN)

        receipts = ledger.vouchers(rows, ledger.RECEIVE)             # one type can be given as text
        assert list(receipts) == ["STRV:T-26-01-9", "STRV:T-26-01-1", "STRV:T-26-01-3", "STRV:T-26-01-4",
                                  "STRV:T-26-02-1"]
        got = receipts["STRV:T-26-01-1"]
        assert (got["wh"], got["to_wh"], got["lines"], got["value"]) == ("Van A", None, {CABLE: 10, CHARGER: 5}, 20)
        assert got["narr"] == first["narr"] and sorted(got["lines"].items()) == sorted(first["lines"].items())
        assert receipts["STRV:T-26-02-1"]["narr"] == issues["SIO:T-26-02-1"]["narr"]

        # where an issue was sent goes through the aliases like any warehouse name
        assert {r["to_wh"] for r in rows if r["voucher"] == "SIO:T-26-01-4"} == {"SHOP 1  S-1"}
        assert issues["SIO:T-26-01-4"]["to_wh"] == "Shop 1" == receipts["STRV:T-26-01-4"]["wh"]
        away = [dict(r, to_wh="Damage Bay") if r["voucher"] == "SIO:T-26-01-2" else r for r in rows]
        assert ledger.vouchers(away, ledger.ISSUE)["SIO:T-26-01-2"]["to_wh"] == "Damage Bay", \
            "a store that is only ever sent to, with no row of its own"
        assert ledger.warehouse_names(away)["Damage Bay"] == "Damage Bay" and len(ledger.balances(away)) == 9

        # a receive leg that states its units and no value: the value is the issue leg's to give
        bare, its_issue = receipts["STRV:T-26-01-3"], issues["SIO:T-26-01-2"]
        assert (bare["lines"], bare["value"], bare["unvalued"]) == ({CABLE: 20}, 0, 20)
        assert (its_issue["lines"], its_issue["value"], its_issue["unvalued"]) == ({CABLE: 20}, 20, 0)
        both = ledger.vouchers(rows, {ledger.ISSUE, ledger.RECEIVE})
        assert len(both) == 9 and [n for n, v in both.items() if v["unvalued"] != 0] == ["STRV:T-26-01-3"]
        part = [dict(r) for r in rows if r["voucher"] == "STRV:T-26-01-1"]
        part[1]["value_in"] = Decimal(0)                             # one of its two lines states no value
        one = ledger.vouchers(part, ledger.RECEIVE)["STRV:T-26-01-1"]
        assert (one["value"], one["unvalued"]) == (10, 5), "a value beside units it does not cover is a part"

        assert ledger.vouchers(rows, [ledger.SALES_RETURN])["SRV:T-26-01-1"]["lines"] == {CABLE: 1}
        assert ledger.vouchers(rows, []) == {} and ledger.vouchers(iter(rows), ledger.RECEIVE) == receipts
        sales = ledger.vouchers(rows, [ledger.SALE])
        assert sales["SI:SI-T-26-01-4"]["wh"] == "Shop 1", "warehouse names go through the aliases"

        odd = [dict(r) for r in rows if r["voucher"] == "SIO:T-26-01-1"]
        odd[1]["to_wh"], odd[2]["date"] = "Van B", date(2026, 1, 9)
        one = ledger.vouchers(odd, [ledger.ISSUE])["SIO:T-26-01-1"]
        assert sorted(one["mixed"]) == ["date", "to_wh"] and one["date"] == date(2026, 1, 9) and one["to_wh"] == "Van A"
        cased = [dict(r) for r in rows if r["voucher"] == "SIO:T-26-01-1"]
        cased[1]["to_wh"] = "VAN A"                                  # the same van, typed in capitals
        assert ledger.vouchers(cased, [ledger.ISSUE])["SIO:T-26-01-1"]["mixed"] == []


# ── ledger: the database fallback and parity with production ──────────────────

class FakeSession:
    """Stands in for scripts.ops.db.Session: answers like the database would."""

    def __init__(self, rows: list[dict], honour_case: bool = True):
        self.rows, self.honour_case = rows, honour_case
        self.asked: list[tuple] = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def q(self, sql, params=None):
        db.check_sql(sql)
        self.asked.append((sql, params))
        out = []
        for r in self.rows:
            r = dict(r)
            if self.honour_case and r["voucher_type"] not in params[0]:
                r["narration"] = None
            out.append(r)
        return out


@test("ledger: from_db reads stock_movements read-only, asks for narration on transfers only, and shapes rows like the file")
def _():
    with loaded_set() as (_root, _book):
        file_rows = ledger.load().rows
        real_session = db.session
        try:
            fake = FakeSession(spec_rows())
            db.session = lambda *a, **kw: fake
            led = ledger.from_db()
            assert led.origin == "database" and led.source == "stock_movements" and led.sha is None
            assert led.cache_note is None and fake.closed and len(fake.asked) == 1
            sql, params = fake.asked[0]
            assert "from stock_movements" in sql and "v_stock_daily_movement" not in sql
            assert "case when voucher_type = any(%s) then narration end" in sql and sql.count("narration") == 2
            assert params == (sorted([ledger.ISSUE, ledger.RECEIVE]),)
            assert order(led.rows) == order(file_rows), "same movements, same hashes, whatever the source"
            assert (led.first_date, led.last_date) == (date(2026, 1, 5), date(2026, 2, 2))
            assert not leaks(repr(led))

            careless = FakeSession(spec_rows(), honour_case=False)     # a database that hands back every narration
            db.session = lambda *a, **kw: careless
            again = ledger.from_db()
            assert order(again.rows) == order(file_rows) and not leaks(repr(again))
        finally:
            db.session = real_session


@test("ledger: a float's stray digits never survive: quantities and values are held to three decimals in a row, in the cache and in a total")
def _():
    from scripts import ingest

    stray = 0.1 + 0.2
    assert repr(stray) == STRAY and repr(1.1 * 3) == STRAY_3 and len(STRAY) == 19, "the stray digits themselves"
    assert LONG.search(STRAY) and not LONG.search(str(ledger._dec(stray))), "written out, the tail reads as a card number"
    for given, held in ((stray, "0.3"), (1.1 * 3, "3.3"), (100.0, "100"), (12.5, "12.5"), (2.675, "2.675"), (0.0005, "0.001"),
                        (0.0004, "0"), (-0.0004, "0"), (-1.0005, "-1.001"), (7, "7"), (None, "0"), ("", "0"),
                        (float("nan"), "0"), (Decimal(STRAY), "0.3"), (Decimal(stray), "0.3"), (STRAY, "0.3"),
                        (Decimal("7.000"), "7"), (Decimal("1E+2"), "100"), ("1,234.50", "1234.5"),
                        (" 45.00000000000001 ", "45")):
        got = ledger._dec(given)
        assert type(got) is Decimal and str(got) == held, (given, got)
    for bad in (float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity"), "nine", Decimal("1E+40")):
        assert "not a number" in str(raises(ValueError, lambda: ledger._dec(bad))), bad

    moves = {REEL: [
        (day(3, 1), "MRN:T-26-03-1", ledger.SUPPLIER_RECEIPT, MAIN, None, stray, 0, 1, None),     # units and value: the stray sum
        (day(3, 2), "SIO:T-26-03-1", ledger.ISSUE, MAIN, "Van A", 0, 0.1, 1, None),
        (day(3, 2), "SIO:T-26-03-1", ledger.ISSUE, MAIN, "Van A", 0, 0.2, 1, None),
        (day(3, 3), "MRN:T-26-03-2", ledger.SUPPLIER_RECEIPT, MAIN, None, 3, 0, 1.1, None),       # value: 3 times 1.1
    ]}
    with data_root() as root:
        folder = root / "business_data" / "Focus ERP 2026-03-03"
        folder.mkdir(parents=True)
        book = folder / "Stock_ledger1_Example.xlsx"
        write_book(book, moves)
        assert store_as_excel_does(book, {"0.3": STRAY, "3.3": STRAY_3}) >= 3
        raw = ingest.parse_stock(ingest.read_grid(book), book.name)
        assert [repr(raw[0][k]) for k in ("received_qty", "received_value_bhd")] == [STRAY] * 2, \
            "the workbook and the parser hand the float on as it is"
        assert repr(raw[3]["received_value_bhd"]) == STRAY_3

        led = ledger.load(book)
        assert not leaks(repr(led.rows)), "and no tail of digits for the scan to take for a card number"
        shown = [tuple(str(r[k]) for k in NUMBERS) for r in led.rows]
        assert shown == [("0.3", "0", "0.3", "0"), ("0", "0.1", "0", "0.1"), ("0", "0.2", "0", "0.2"),
                         ("3", "0", "3.3", "0")], shown
        assert all(r[k].as_tuple().exponent >= -3 for r in led.rows for k in NUMBERS), "three decimals at most"
        assert ledger.balances(led.rows) == {(MAIN, REEL): 3}, "0.3 in, 0.1 and 0.2 out, 3 in: exactly 3"
        sent = ledger.vouchers(led.rows, ledger.ISSUE)["SIO:T-26-03-1"]
        assert sent["lines"] == {REEL: Decimal("0.3")} and sent["value"] == Decimal("0.3") and sent["unvalued"] == 0
        assert ledger.totals(led.rows)[(ledger.SUPPLIER_RECEIPT, MAIN)]["units_in"] == Decimal("3.3")
        cache_text = ledger.cache_file(led.sha).read_text(encoding="utf-8")
        assert not re.search(r"[0-9]\.[0-9]{4,}", cache_text + repr(led.rows)), "no figure with more than three decimals"
        warm = ledger.load(book)
        assert warm.origin == "cache" and warm.rows == led.rows and repr(warm.rows) == repr(led.rows)

        # the table holds what a float wrote into it: the same row comes back from the database
        real_session = db.session
        try:
            db.session = lambda *a, **kw: FakeSession(spec_rows(moves))
            assert len(str(spec_rows(moves)[0]["received_qty"])) > 20, "the stand-in really holds the stray digits"
            held = ledger.from_db()
        finally:
            db.session = real_session
        assert order(held.rows) == order(led.rows) and repr(order(held.rows)) == repr(order(led.rows))


@test("ledger parity: one aggregated read of production; ok only when every voucher type agrees in every warehouse, and both dates")
def _():
    with loaded_set() as (_root, _book):
        led = ledger.load()
        seen: list[str] = []
        answer: list[dict] = []
        real_rpc = db.rpc

        def stand_in(sql):
            seen.append(sql)
            return [dict(r) for r in answer]

        def differs(rows: list[dict], of: ledger.Ledger | None = None) -> list[str]:
            answer[:] = rows
            out = ledger.parity(led if of is None else of)
            assert out["ok"] is False and "differs" in out["summary"] and "to check" in out["summary"], out
            return [c["what"] for c in out["checks"] if not c["ok"]]

        def changed(vtype: str, wh: str, **fields) -> list[dict]:
            rows = production_totals()
            hit = [r for r in rows if (r["vtype"], r["wh"]) == (vtype, wh)]
            assert len(hit) == 1, (vtype, wh)
            hit[0].update(fields)
            return rows

        def other(rows: list[dict]) -> ledger.Ledger:
            return ledger.Ledger(rows=rows, source="other", first_date=led.first_date, last_date=led.last_date)

        issue, receive, sale = ledger.ISSUE, ledger.RECEIVE, ledger.SALE
        every = ("units in", "units out", "lines", "days with movement", "first day", "last day")
        db.rpc = stand_in
        try:
            answer[:] = production_totals()
            assert len(answer) == N_PAIRS == 12 and N_TYPES == 6
            out = ledger.parity(led)
            assert out["ok"] is True and all(c["ok"] for c in out["checks"]), out
            assert len(seen) == 1, "one read"
            levels = [c["level"] for c in out["checks"]]
            assert levels == ["dates"] * 2 + ["type"] * (N_TYPES * 3) + ["warehouse"] * (N_PAIRS * 6), levels
            assert out["summary"] == (
                "The stock ledger file agrees with what the portal holds: 22 lines in 6 voucher types, the same units, "
                "lines and days in every warehouse, 5 January 2026 to 2 February 2026.")
            sql = seen[0]
            db.check_sql(sql)
            assert sql is ledger.PARITY_SQL and "FROM v_stock_daily_movement" in sql and "stock_movements" not in sql
            grouped = sql.split("GROUP BY")
            assert len(grouped) == 2 and "voucher_type" in grouped[1] and "warehouse_name" in grouped[1]
            assert sql.count("SUM(") == 3 and "COUNT(DISTINCT move_date)" in sql and ";" not in sql
            assert not re.search(r"\bas\s+t\b", sql, re.I), "the read RPC breaks on a column aliased t"

            # one figure of one voucher type in one warehouse: named at both levels, told at the plain one
            assert differs(changed(issue, MAIN, units_out="42")) == [f"{issue}: units out", f"{issue} in {MAIN}: units out"]
            assert ledger.parity(led)["summary"] == (
                "The stock ledger file differs from what the portal holds, to check: "
                f"{issue}: units out 45 in the file, 44 in the portal.")
            assert differs(changed(sale, "Van A", line_count="5")) == [f"{sale}: lines", f"{sale} in Van A: lines"]
            assert differs(changed(receive, "Van A", units_in="46.5")) == [f"{receive}: units in",
                                                                           f"{receive} in Van A: units in"]
            assert differs(changed(issue, MAIN, day_count="2")) == [f"{issue} in {MAIN}: days with movement"]
            assert ledger.parity(led)["summary"].endswith(
                f"to check: {issue} in {MAIN}: days with movement 3 in the file, 2 in the portal.")

            # units are compared to three decimals, as a row holds them: a float's stray digits in the
            # table are no difference, a thousandth is
            answer[:] = changed(receive, "Van A", units_in="45.00000000000001")
            assert ledger.parity(led)["ok"] is True, "the file's 45 and the table's 45.00000000000001 agree"
            assert differs(changed(receive, "Van A", units_in="45.0006")) == [f"{receive}: units in",
                                                                              f"{receive} in Van A: units in"]

            # the dates lead the sentence: the portal simply holding another load is the plain fact
            assert differs(changed(issue, "Van A", last_date="2026-02-03")) == ["last movement date",
                                                                                 f"{issue} in Van A: last day"]
            assert ledger.parity(led)["summary"].startswith(
                "The stock ledger file differs from what the portal holds, to check: "
                "last movement date 2 February 2026 in the file, 3 February 2026 in the portal; ")
            assert differs(changed(ledger.SUPPLIER_RECEIPT, MAIN, first_date="2025-12-01")) == [
                "first movement date", f"{ledger.SUPPLIER_RECEIPT} in {MAIN}: first day"]
            newer = [dict(r, units_in=str(Decimal(r["units_in"]) + 5), line_count=str(int(r["line_count"]) + 2),
                          last_date="2026-02-09") for r in production_totals()]
            assert len(differs(newer)) > 3
            told = ledger.parity(led)["summary"]
            assert told.startswith("The stock ledger file differs from what the portal holds, to check: "
                                   "last movement date 2 February 2026 in the file, 9 February 2026 in the portal; "), told
            assert "first movement date" not in told and told.endswith(" more).")

            over, back = ledger.COUNT_OVER, ledger.SALES_RETURN
            extra = production_totals() + [{"vtype": over, "wh": MAIN, "units_in": "3", "units_out": "0",
                                            "line_count": "1", "day_count": "1", "first_date": "2026-01-07",
                                            "last_date": "2026-01-07"}]
            absent = [w for w in every if w != "units out"]              # nothing went out: 0 on both sides
            assert differs(extra) == [f"{over}: units in", f"{over}: lines"] + [f"{over} in {MAIN}: {w}" for w in absent]
            fewer = [r for r in production_totals() if r["vtype"] != back]
            assert differs(fewer) == [f"{back}: units in", f"{back}: lines"] + [f"{back} in Van A: {w}" for w in absent]
            by_type_only = [{k: v for k, v in r.items() if k not in ("wh", "day_count")} for r in production_totals()]
            assert differs(by_type_only), "an answer in another shape is never taken for agreement"
            assert len(differs([])) == 2 + N_TYPES * 2 + N_PAIRS * 5, "an empty production agrees with nothing"
            assert ledger.parity(led)["summary"].endswith("(+11 more)."), \
                "three are spelt out; the rest of the dates and voucher types are counted, the warehouses are not"

            # every voucher type still adds up, in other warehouses: all the receipts of one van sit in another
            moved = other([dict(r, wh="Van B") if r["vtype"] == receive and r["wh"] == "Van A" else r for r in led.rows])
            what = differs(production_totals(), of=moved)
            assert {w.split(":")[0] for w in what} == {f"{receive} in Van A", f"{receive} in Van B"}, what
            out = ledger.parity(moved)
            assert {c["level"] for c in out["checks"] if not c["ok"]} == {"warehouse"}
            assert f"{receive} in Van A: units in 0 in the file, 45 in the portal" in out["summary"], out["summary"]

            # the same units and lines in every warehouse, on other days
            redated = other([dict(r, date=date(2026, 1, 6)) if led.first_date < r["date"] < led.last_date else r
                             for r in led.rows])
            what = differs(production_totals(), of=redated)
            assert what and all(w.endswith(("days with movement", "first day", "last day")) for w in what), what

            # counts are compared, not lines: the limit parity()'s own words state
            answer[:] = production_totals()
            repointed = other([dict(r, item=EJECTOR) if r["vtype"] == sale else r for r in led.rows])
            assert ledger.parity(repointed)["ok"] is True

            # a warehouse is a name from the data: the wording guard is for the engine's own words
            named = other([dict(r, wh="Leak Test Store") if r["wh"] == "Van B" else r for r in led.rows])
            told = ledger.parity(named)["summary"]
            assert "to check" in told and f"{receive} in Leak Test Store: units in 20 in the file, 0 in the portal" in told

            answer[:] = []
            empty = ledger.parity(ledger.Ledger(rows=[], source="none", first_date=None, last_date=None))
            assert empty["ok"] is False and "could not be compared" in empty["summary"]

            def down(sql):
                raise RuntimeError("no network")
            db.rpc = down
            raises(RuntimeError, lambda: ledger.parity(led))          # an error is never an answer
        finally:
            db.rpc = real_rpc


@test("ledger for_audit: the file when it is proven equal to production, else the database; 'file' never touches the database, 'db' always reads it")
def _():
    from scripts import ingest

    with loaded_set() as (root, book):
        folder = book.parent
        file_rows = ledger.load(use_cache=False).rows
        opened: list[str] = []          # workbooks the reader was given
        reached: list[str] = []         # what reached the database stand-ins: 'rpc' (parity), 'session' (the table)
        answer: list[dict] = []         # what the parity read returns
        failing: list[Exception] = []   # set: the parity read raises it
        real_read, real_rpc, real_session, real_load = ingest.read_grid, db.rpc, db.session, ledger.load

        def reading(path):
            opened.append(Path(path).name)
            return real_read(path)

        def rpc(sql):
            reached.append("rpc")
            if failing:
                raise failing[0]
            return [dict(r) for r in answer]

        def session(*a, **kw):
            reached.append("session")
            return FakeSession(spec_rows())

        def never(*a, **kw):
            raise AssertionError("not to be called here")

        notes: list[str] = []

        def ask(*prefer):
            del opened[:], reached[:]
            led, info = ledger.for_audit(*prefer)
            assert info["source"] in ("file", "database") and (info["source"] == "database") == (led.origin == "database")
            notes.append(info["note"])
            return led, info

        portal = "The stock ledger is read from the portal's database"
        ingest.read_grid, db.rpc, db.session = reading, rpc, session
        try:
            # auto, and the file is what production holds: the file, on one comparison
            answer[:] = production_totals()
            led, info = ask()
            assert sorted(info) == ["note", "parity", "source"]
            assert info["source"] == "file" and led.source == book.name and led.rows == file_rows
            assert info["parity"]["ok"] is True and reached == ["rpc"], "one comparison; the table itself is not read"
            assert info["note"] == f"The stock ledger is read from the file {book.name}: it agrees with what the portal holds."
            again, same = ask("auto")
            assert same == info and again.origin == "cache" and opened == [], "auto is the default; the cache serves it"

            # auto, and production holds another load: the database, with the differences kept
            answer[:] = [dict(r, last_date="2026-02-09") for r in production_totals()]
            led, info = ask()
            assert info["source"] == "database" and led.origin == "database" and led.source == "stock_movements"
            assert reached == ["rpc", "session"] and info["parity"]["ok"] is False
            assert info["note"] == f"{portal}: the file {book.name} differs from what the portal holds."
            assert "last movement date" in info["parity"]["summary"] and order(led.rows) == order(file_rows)

            # auto, and the comparison cannot be made: no proof, so the database
            failing.append(RuntimeError(f"no network, zebra {ID_A}"))
            led, info = ask()
            assert info["source"] == "database" and info["parity"] is None and reached == ["rpc", "session"]
            assert info["note"] == f"{portal}: the file {book.name} could not be compared with the portal (RuntimeError)."
            del failing[:]

            # auto, and the database itself fails: raised, there is nothing left to read
            def down(*a, **kw):
                reached.append("session")
                raise RuntimeError("no database")
            db.session = down
            assert "no database" in str(raises(RuntimeError, lambda: ask()))
            assert reached == ["rpc", "session"]
            db.session = session

            # auto, with no file, and with a file the reader cannot open: the database, production is not compared
            answer[:] = production_totals()
            aside = book.rename(book.with_name("Report_put_aside.xlsx"))
            led, info = ask()
            assert info == {"source": "database", "parity": None,
                            "note": f"{portal}: no Stock_ledger file in {folder.name}."}, info
            assert reached == ["session"] and opened == [] and order(led.rows) == order(file_rows)
            legacy = folder / "Stock_ledger2_Example.xls"
            legacy.write_bytes(OLD_XLS)
            led, info = ask()
            assert info["source"] == "database" and info["parity"] is None and reached == ["session"]
            assert info["note"].startswith(f"{portal}: the stock ledger file {legacy.name} could not be opened (BadZipFile); ")
            assert info["note"].endswith("another program.") and "\n" not in info["note"]
            legacy.unlink()

            # auto, and a workbook that opens in a layout the parser refuses: the database, and the note says why
            swapped = list(HEADER)
            swapped[12], swapped[13] = swapped[13], swapped[12]
            write_book(book, MOVES, OPENING, header=swapped)
            led, info = ask()
            assert info == {"source": "database", "parity": None, "note": (
                f"{portal}: the stock ledger file of the loaded folder could not be read (HeaderMismatch).")}, info
            assert reached == ["session"] and opened == [book.name]

            # 'file': the file as it is, and the database is never touched, whatever the file is like
            db.rpc = db.session = never
            raises(ValueError, lambda: ask("file"))                    # the refused layout stops it; no fallback
            book.unlink()
            said = str(raises(ledger.NoLedgerFile, lambda: ask("file")))
            assert said == f"no Stock_ledger file in {folder.name}"
            aside.rename(book)
            led, info = ask("file")
            assert info == {"source": "file", "parity": None, "note": (
                f"The stock ledger is read from the file {book.name}, as asked: it was not compared with the portal.")}, info
            assert led.rows == file_rows and reached == []

            # 'db': the table, always; the file is not looked for, even when it is there and equal
            db.session, ledger.load = session, never
            led, info = ask("db")
            assert info == {"source": "database", "parity": None,
                            "note": f"{portal}, as asked: the file was not opened."}, info
            assert reached == ["session"] and opened == [] and order(led.rows) == order(file_rows)
            ledger.load = real_load

            exc = raises(ValueError, lambda: ask("production"))
            assert "auto, file, db" in str(exc) and reached == [] and opened == []
        finally:
            ingest.read_grid, db.rpc, db.session, ledger.load = real_read, real_rpc, real_session, real_load

        assert len(notes) == 9 and len(set(notes)) == 8, "one sentence for each way it can go"
        for note in notes:                                              # plain, careful, and never a path or a cell's words
            assert fmt.careful(note) == note and note.endswith(".") and "\n" not in note, note
            assert str(root) not in note and not leaks(note), note


# ── items: the name bridge and the accessory rule ─────────────────────────────

@test("items: the bridge picks the longest known name, on whole words only; unknown is None; names compare without case or extra spaces")
def _():
    config.use(FIXTURE)
    try:
        known = ["ITEM-01 Cable", "ITEM-01 Cable One", "ITEM-02 Charger Two", "ITEM-1", "ITEM-10 Long Lead",
                 "Item-02  CHARGER two", None, "", float("nan")]
        bridge = items.Bridge(known)
        assert len(bridge) == 5
        assert bridge.get("ITEM-01 Cable One C 01") == "ITEM-01 Cable One", "the longest, not 'ITEM-01 Cable'"
        assert bridge.get("ITEM-01 Cable Two C 02") == "ITEM-01 Cable"
        assert bridge.get("ITEM-01 Cable One") == "ITEM-01 Cable One", "a name can equal the ledger name"
        assert bridge.get("  item-02   CHARGER two  ch2 ") == "ITEM-02 Charger Two", "as it was given, first spelling"
        assert bridge.get("ITEM-10 Long Lead LL 10") == "ITEM-10 Long Lead"
        assert bridge.get("ITEM-1 Short S1") == "ITEM-1"
        assert bridge.get("ITEM-100 Other X") is None, "'ITEM-1' is only the start of a longer word"
        assert bridge.get("ITEM-01 Cab") is None and bridge.get("ITEM-01 CableOne C 01") is None
        assert bridge.get("ITEM-09 Unknown U9") is None
        assert bridge.get("") is None and bridge.get(None) is None and bridge.get("   ") is None
        assert [bridge.get(n) for n in (CABLE, CHARGER)] == ["ITEM-01 Cable One", "ITEM-02 Charger Two"]

        full = items.Bridge(KNOWN)
        assert {name: full.get(name) for name in MOVES} == dict(zip(MOVES, KNOWN))
        names = [CABLE, "ITEM-09 Unknown U9", CABLE, "ITEM-01 Cable One C 99", "ITEM-08 Other O8", "ITEM-09 Unknown U9"]
        assert full.unmapped(names) == ["ITEM-09 Unknown U9", "ITEM-08 Other O8"]
        assert full.collisions(names) == {"ITEM-01 Cable One": [CABLE, "ITEM-01 Cable One C 99"]}
        assert full.collisions(list(MOVES)) == {} and full.unmapped(list(MOVES)) == []

        assert items.leading_code("item-01 Cable One") == "ITEM-01" and items.leading_code("  x12  UC cable") == "X12"
        assert items.leading_code(CABLE) == items.leading_code(KNOWN[0]) == "ITEM-01"
        assert items.leading_code("ITEM-01 Cable One") == items.leading_code("ITEM-01 Cable Two"), \
            "variants share the model code: it is never a key for one item"
        assert items.leading_code("") == "" and items.leading_code(None) == "" and items.leading_code(float("nan")) == ""
        assert items.norm("  ITEM-01   Cable\tOne ") == "item-01 cable one" and items.norm(None) == ""
    finally:
        config.use(None)


@test("items: a config exception wins over the rule; one that points at no known name leaves the item unplaced, in sight")
def _():
    known = ["ITEM-01 Cable One", "ITEM-02 Charger Two"]
    try:
        config.use(dict(FIXTURE, **{"items.bridge_exceptions": {"item-01 cable  one c 01": "item-02  charger TWO"}}))
        bridge = items.Bridge(known)
        assert bridge.get(CABLE) == "ITEM-02 Charger Two", "config wins; the known name comes back as it was given"
        assert bridge.get(CHARGER) == "ITEM-02 Charger Two" and bridge.collisions([CABLE, CHARGER]) == {
            "ITEM-02 Charger Two": [CABLE, CHARGER]}

        renamed = "ITEM-09 Renamed R9"
        passed = items.Bridge(known, exceptions={renamed: "ITEM-09 Old Name", CHARGER: "", CABLE: None})
        assert passed.get(renamed) is None, "a name this bridge was not given is never handed out"
        assert passed.get(CHARGER) is None and passed.get(CABLE) is None
        assert passed.unmapped([CABLE, CHARGER, renamed]) == [CABLE, CHARGER, renamed]

        slip = items.Bridge(known, exceptions={CABLE: "ITEM-01 Cable Onee"})          # one letter too many
        assert items.Bridge(known, exceptions={}).get(CABLE) == "ITEM-01 Cable One", "the rule alone places it"
        assert slip.get(CABLE) is None, "config wins even when it is wrong: the rule is not tried behind it"
        assert slip.unmapped([CABLE, CHARGER]) == [CABLE] and slip.collisions([CABLE, CHARGER]) == {}
        assert slip.get(CHARGER) == "ITEM-02 Charger Two"
        for bridge in (passed, slip):
            for name in (CABLE, CHARGER, renamed, "ITEM-08 Other O8"):
                assert bridge.get(name) is None or bridge.get(name) in known, "a known name or None, nothing else"

        config.use({"warehouses.main": MAIN})                              # no exceptions key at all
        raises(config.ConfigMissing, lambda: items.Bridge(known))
        assert items.Bridge(known, exceptions={}).get(CABLE) == "ITEM-01 Cable One"
    finally:
        config.use(None)


@test("items: an accessory is an item that ever moved in the main store, less the config list; never by warehouse name or a word in the item")
def _():
    with loaded_set() as (_root, _book):
        rows = ledger.load().rows
        assert ledger.main_items(rows) == {CABLE, CHARGER, HANDSET, EJECTOR}
        assert items.accessory_items(rows) == {CABLE, CHARGER, HANDSET, EJECTOR}
        assert items.accessory_items(iter(rows)) == {CABLE, CHARGER, HANDSET, EJECTOR}, "any iterable of rows"
        assert PACK not in items.accessory_items(rows), "it sits in a van that also holds accessories"
        assert EJECTOR in items.accessory_items(rows), "'sim' in the name decides nothing"
        assert ledger.main_items(rows, main="van a") == {CABLE, CHARGER, PACK}
        assert items.accessory_items(rows, main="Shop 1 S-1") == {CHARGER}, "the main store's name goes through the aliases"
        assert ledger.main_items(rows, main="Nowhere") == set()
        raises(ValueError, lambda: ledger.main_items(rows, main="  "))
        sent_only = [r for r in rows if r["wh"] != MAIN]                    # issued TO the main store, never moved in it
        assert any(r["to_wh"] == MAIN for r in sent_only) and ledger.main_items(sent_only) == set()

        bridge = items.Bridge(KNOWN)
        config.use(dict(FIXTURE, **{"items.not_accessories": ["item-04  HANDSET"]}))     # as the other reports name it
        assert items.accessory_items(rows, bridge=bridge) == {CABLE, CHARGER, EJECTOR}
        config.use(dict(FIXTURE, **{"items.not_accessories": ["ITEM-04 handset   hs4", "", "ITEM-03 Starter Pack"]}))
        assert items.accessory_items(rows, bridge=bridge) == {CABLE, CHARGER, EJECTOR}, "the ledger name works too"

        # a blank entry names no item: it never takes out the ones a bridge cannot place
        partial = items.Bridge([KNOWN[0], KNOWN[3]])                         # the cable and the handset only
        assert partial.get(CHARGER) is None and partial.get(EJECTOR) is None
        config.use(dict(FIXTURE, **{"items.not_accessories": ["", "ITEM-04 Handset"]}))
        assert items.accessory_items(rows, bridge=partial) == {CABLE, CHARGER, EJECTOR}

        acc = items.accessory_items(rows, bridge=bridge)
        held = {k: v for k, v in ledger.balances(rows).items() if ledger.is_main(k[0]) and k[1] in acc}
        assert held == {(MAIN, CABLE): 72, (MAIN, CHARGER): 35, (MAIN, EJECTOR): 30}


@test("items: the accessory rule stops on a main store that matches no warehouse, and on a never-list given no bridge")
def _():
    with loaded_set() as (_root, _book):
        rows = ledger.load().rows
        exc = raises(ValueError, lambda: items.accessory_items(rows, main="Nowhere"))
        assert "no movement was found in the main store 'Nowhere'" in str(exc) and "`main` argument" in str(exc)
        config.use(dict(FIXTURE, **{"warehouses.main": "Main Stor"}))               # a letter short in config
        exc = raises(ValueError, lambda: items.accessory_items(rows))
        assert "'Main Stor'" in str(exc) and "config 'warehouses.main'" in str(exc) and "whole ledger" in str(exc)
        config.use(FIXTURE)
        window = [r for r in rows if r["wh"] != MAIN]                               # a window of the ledger, not the ledger
        raises(ValueError, lambda: items.accessory_items(window))
        assert items.accessory_items([]) == set(), "no rows: nothing to judge"

        config.use(dict(FIXTURE, **{"items.not_accessories": ["ITEM-04 Handset"]}))  # as the other reports name it
        exc = raises(TypeError, lambda: items.accessory_items(rows))
        assert "Bridge" in str(exc) and "items.not_accessories" in str(exc)
        assert items.accessory_items(rows, bridge=items.Bridge(KNOWN)) == {CABLE, CHARGER, EJECTOR}
        config.use(dict(FIXTURE, **{"items.not_accessories": ["ITEM-04 handset   hs4"]}))
        raises(TypeError, lambda: items.accessory_items(rows))                      # even written the ledger's way
        config.use(dict(FIXTURE, **{"items.not_accessories": ["", "   "]}))
        assert items.accessory_items(rows) == {CABLE, CHARGER, HANDSET, EJECTOR}, "blank entries are no list"


# ── the real ledger (read-only; SKIP when it is not on this machine) ──────────

def golden_for(sha: str, golden: dict) -> tuple[dict, str]:
    """The golden figures that hold for the workbook with this sha256, and the word for the test's
    line. A golden file is written for ONE export. When the folder is published again from another
    export, the figures describe the old file: they are set aside, and only the rules that hold for
    any export are checked. A golden with no 'sha256' is taken as it is."""
    if not golden:
        return {}, "golden absent"
    if golden.get("sha256") not in (None, sha):
        return {}, "golden set aside"
    return golden, "golden present"


@test("golden figures stand for one export: the golden of another export of the same folder is set aside")
def _():
    this, that = "sha256:" + "ab" * 32, "sha256:" + "cd" * 32
    figures = {"sha256": this, "rows": 22, "items": 5}
    assert golden_for(this, figures) == (figures, "golden present")
    assert golden_for(that, figures) == ({}, "golden set aside"), "the folder was published again: nothing is compared"
    assert golden_for(that, {"rows": 22}) == ({"rows": 22}, "golden present"), "an older golden names no export"
    assert golden_for(this, {}) == ({}, "golden absent")


@test("real ledger (SKIP without the 7-Oct folder): parses to the golden row count; no ID-shaped number in rows or cache; a cached read is fast")
def _():
    book = ledger.find_file(REAL_FOLDER)
    if book is None:
        print(f"  SKIP: no Stock_ledger file in {REAL_FOLDER.name} on this machine")
        return
    golden: dict = {}
    golden_file = REAL_ROOT / "business_data" / "ops" / "golden" / "ledger.json"
    if golden_file.is_file():
        golden = json.loads(golden_file.read_text(encoding="utf-8"))
    real_config = None
    config_file = REAL_ROOT / "business_data" / "ops" / "config.json"
    if config_file.is_file():
        real_config = json.loads(config_file.read_text(encoding="utf-8"))

    with data_root() as root:                       # this test's cache goes to a temporary folder
        t0 = time.perf_counter()
        cold = ledger.load(book)
        t1 = time.perf_counter()
        warm = ledger.load(book)
        t2 = time.perf_counter()
        assert cold.origin == "file" and warm.origin == "cache" and warm.rows == cold.rows
        assert cold.cache_note is None and warm.cache_note is None
        golden, basis = golden_for(cold.sha, golden)
        if basis == "golden set aside":
            print("    golden describes another export of this folder: only the 30,000-row rule applies")
        if "rows" in golden:
            assert len(cold.rows) == golden["rows"], (len(cold.rows), golden["rows"])
        else:
            assert len(cold.rows) > 30000, len(cold.rows)
        cache = ledger.cache_file(cold.sha)
        assert cache.is_file() and root in cache.parents
        for what, blob in (("cache", cache.read_text(encoding="utf-8")), ("rows", repr(cold.rows))):
            assert not NINE.search(blob) and not LONG.search(blob), f"a digit run in the {what}"
        assert all(r["narr"] is None or (r["vtype"] in ledger.NARRATED and set(r["narr"]) <= ledger.HASH_LETTERS)
                   for r in cold.rows)
        assert all(r[k].as_tuple().exponent >= -3 for r in cold.rows for k in NUMBERS), "a figure with more than three decimals"
        assert sorted(p.name for p in cache.parent.iterdir()) == [cache.name], "one cache for one workbook"
        assert (t2 - t1) * 4 < (t1 - t0), f"cached read {t2 - t1:.2f}s against {t1 - t0:.1f}s"

        for key, value in (("first_date", cold.first_date.isoformat()), ("last_date", cold.last_date.isoformat()),
                           ("items", len({r["item"] for r in cold.rows})),
                           ("warehouses", len({r["wh"] for r in cold.rows}))):
            assert key not in golden or golden[key] == value, (key, value)
        checked = "row count, dates, items, warehouses" if golden else "row count"
        if real_config is not None and "main_units" in golden:
            config.use(real_config)                 # the main store's name and the aliases, as the owner set them
            try:
                held = [units for (wh, _item), units in ledger.balances(cold.rows).items()
                        if ledger.is_main(wh) and units != 0]
                listed_out = config.get("items.not_accessories")
                # A filled list needs a bridge. Its own names serve for the check below (a subset, never a
                # figure): a real figure passes the bridge of the stock and day book names.
                bridge = items.Bridge(listed_out, exceptions={}) if listed_out else None
                accessories = items.accessory_items(cold.rows, bridge=bridge)
                # the main store's day series over the last 30 days, against balances() at both ends
                start = cold.last_date - timedelta(days=29)
                t3 = time.perf_counter()
                month = ledger.day_end_balances(cold.rows, None, None, start, cold.last_date)
                t4 = time.perf_counter()
                opened_at = {item: units for (wh, item), units in ledger.balances(cold.rows, as_of=start).items()
                             if ledger.is_main(wh)}
                closed_at = {item: units for (wh, item), units in ledger.balances(cold.rows).items() if ledger.is_main(wh)}
            except config.ConfigMissing:
                checked += " (main store left out: the config on this machine lacks a key)"
            else:
                assert sum(held) == Decimal(str(golden["main_units"])), "main store units"
                assert len(held) == golden["main_items"], "main store items"
                assert accessories <= ledger.main_items(cold.rows)
                if not listed_out and "accessory_items" in golden:
                    assert len(accessories) == golden["accessory_items"], "accessory items"
                assert all(len(series) == 30 for series in month.values()) and set(month) == set(closed_at)
                assert {item: series[-1] for item, series in month.items()} == closed_at, "day series, last day"
                assert {item: series[0] for item, series in month.items()} == \
                    {item: opened_at.get(item, 0) for item in month}, "day series, first day"
                assert sum(series[-1] for series in month.values()) == Decimal(str(golden["main_units"]))
                checked += f" and the main store, its 30-day series in {t4 - t3:.2f}s"
        print(f"    {basis} ({checked}); workbook {t1 - t0:.1f}s, cached {t2 - t1:.2f}s")


# ── the movement audit (scripts.ops.moves) ────────────────────────────────────
# An invented ledger, as scripts.ops.ledger shapes its rows: three vans, an outlet, the main store, the damage store
# and a van's SIM store. The week of D (Wed 11 Mar to Tue 17 Mar 2026) holds every outcome of a transfer.

VA, VB, VC = "Van A - Acc WH", "Van B - Acc WH", "Van C - Acc WH"
SHOP, DMG, SMP, SIMWH, SIMWH_B = "Causeway", "Damage Store", "Samples Store", "Van A - SIM WH", "Van B - SIM WH"
MV_FIXTURE = {"warehouses.main": MAIN, "warehouses.aliases": {}, "warehouses.not_counted": [DMG, SMP],
              "warehouses.damage": DMG, "items.bridge_exceptions": {}, "items.not_accessories": []}
STOCK_NAMES = {"cable": "ITEM-11 Cable Eleven", "charger": "ITEM-12 Charger Twelve", "bank": "ITEM-13 Power Bank",
               "nocost": "ITEM-14 Holder Fourteen"}
ITEM_CODES = {"cable": "C11", "charger": "C12", "bank": "C13", "nocost": "C14"}
M_CABLE, M_CHARGER, M_BANK, M_NOCOST = (f"{STOCK_NAMES[k]} {ITEM_CODES[k]}" for k in ("cable", "charger", "bank", "nocost"))
M_SIM = "SIM-01 Starter Pack SP1"
M_ODD = "ZZ-99 Odd Thing ZZ9"                   # moves in the main store; neither the price book nor the stock knows it
D_MV = date(2026, 3, 17)                        # the ledger's last day, a Tuesday
SHA_MV = "sha256:" + "ab" * 32


def mday(month: int, dom: int) -> date:
    return date(2026, month, dom)


def mrow(day, voucher, vtype, wh, item, q_in=0, q_out=0, value=0, to=None) -> dict:
    v = Decimal(str(value))
    return {"item": item, "date": day, "voucher": voucher, "vtype": vtype, "wh": wh, "to_wh": to,
            "qty_in": Decimal(q_in), "qty_out": Decimal(q_out), "value_in": v if q_in else Decimal(0),
            "value_out": v if q_out else Decimal(0), "narr": None}


def sio(n: int, day: date, frm: str, to: str, lines) -> list[dict]:
    return [mrow(day, f"SIO:T-26-{day.month:02d}-{n}", ledger.ISSUE, frm, item, q_out=u, value=u, to=to)
            for item, u in lines]


def strv(n: int, day: date, wh: str, lines) -> list[dict]:
    return [mrow(day, f"STRV:T-26-{day.month:02d}-{n}", ledger.RECEIVE, wh, item, q_in=u) for item, u in lines]


def mv_rows(charger_value=400, extra=()) -> list[dict]:
    rows = []
    rows += [mrow(mday(2, 1), "MRN:T-26-02-1", ledger.SUPPLIER_RECEIPT, MAIN, M_CABLE, q_in=500, value=250),
             mrow(mday(2, 1), "MRN:T-26-02-1", ledger.SUPPLIER_RECEIPT, MAIN, M_CHARGER, q_in=200, value=charger_value),
             mrow(mday(2, 1), "MRN:T-26-02-1", ledger.SUPPLIER_RECEIPT, MAIN, M_BANK, q_in=100, value=300),
             mrow(mday(2, 2), "ExcStk:T-26-02-1", ledger.COUNT_OVER, MAIN, M_NOCOST, q_in=20),
             mrow(mday(2, 2), "MRN:T-26-02-2", ledger.SUPPLIER_RECEIPT, SIMWH, M_SIM, q_in=100, value=100)]
    rows += sio(1, mday(2, 3), MAIN, VA, [(M_CABLE, 50), (M_CHARGER, 10)])
    rows += strv(1, mday(2, 3), VA, [(M_CABLE, 50), (M_CHARGER, 10)])
    rows += sio(2, mday(2, 3), MAIN, VB, [(M_CABLE, 30)]) + strv(2, mday(2, 3), VB, [(M_CABLE, 30)])
    rows += sio(3, mday(2, 4), MAIN, SHOP, [(M_BANK, 10), (M_NOCOST, 5)])
    rows += strv(3, mday(2, 4), SHOP, [(M_BANK, 10), (M_NOCOST, 5)])
    rows += [mrow(mday(2, 10), "SI:T-26-02-1", ledger.SALE, VA, M_CABLE, q_out=5, value=5)]
    rows += sio(4, mday(2, 26), VA, MAIN, [(M_CABLE, 4)]) + strv(4, mday(2, 26), MAIN, [(M_CABLE, 4)])
    rows += sio(5, mday(2, 27), SIMWH, SIMWH_B, [(M_SIM, 20)]) + strv(5, mday(2, 27), SIMWH_B, [(M_SIM, 20)])
    rows += sio(9, mday(3, 5), MAIN, DMG, [(M_CABLE, 2)])                         # never booked in by the damage store
    rows += sio(10, mday(3, 11), MAIN, VA, [(M_CABLE, 20)]) + sio(11, mday(3, 11), MAIN, VB, [(M_CHARGER, 6)])
    rows += strv(10, mday(3, 12), VA, [(M_CABLE, 20)])                             # SIO 10, by its deadline
    rows += sio(12, mday(3, 12), VA, MAIN, [(M_CABLE, 3)]) + strv(11, mday(3, 12), VB, [(M_CABLE, 3)])    # at Van B
    rows += sio(13, mday(3, 12), VA, MAIN, [(M_CHARGER, 2)]) + strv(12, mday(3, 12), MAIN, [(M_CHARGER, 2)])
    rows += sio(14, mday(3, 13), VB, DMG, [(M_CABLE, 1)]) + strv(13, mday(3, 13), DMG, [(M_CABLE, 1)])    # a Friday
    rows += sio(15, mday(3, 14), MAIN, VC, [(M_BANK, 4)])                          # never received
    rows += strv(14, mday(3, 14), VB, [(M_CHARGER, 6)])                            # SIO 11, after its deadline
    rows += sio(22, mday(3, 14), VB, MAIN, [(M_CABLE, 2)]) + strv(15, mday(3, 14), MAIN, [(M_CABLE, 2)])  # back-dated
    rows += sio(16, mday(3, 15), MAIN, VA, [(M_CABLE, 5)]) + sio(17, mday(3, 15), MAIN, VA, [(M_CABLE, 5)])
    rows += strv(16, mday(3, 15), VA, [(M_CABLE, 5)])                              # one receipt for the two
    rows += sio(18, mday(3, 15), MAIN, DMG, [(M_NOCOST, 2)])                       # inside the damage store's week
    rows += sio(19, mday(3, 16), MAIN, VC, [(M_CHARGER, 3)])                       # inside its deadline
    rows += sio(20, mday(3, 16), MAIN, SHOP, [(M_BANK, 6), (M_CABLE, 2)])
    rows += strv(17, mday(3, 16), SHOP, [(M_BANK, 5), (M_CABLE, 2)])               # other quantities
    rows += sio(21, mday(3, 16), VA, VA, [(M_CHARGER, 1)]) + strv(18, mday(3, 16), VA, [(M_CHARGER, 1)])   # own store
    rows += strv(19, mday(3, 17), VB, [(M_CHARGER, 7)])                            # no issue
    rows += sio(23, mday(3, 16), SIMWH, SIMWH_B, [(M_SIM, 5)])                     # SIM: out of scope
    rows += [mrow(mday(3, 13), "SI:T-26-03-1", ledger.SALE, VA, M_CABLE, q_out=6, value=6),
             mrow(mday(3, 14), "SI:T-26-03-2", ledger.SALE, SHOP, M_BANK, q_out=2, value=2),
             mrow(mday(3, 16), "SI:T-26-03-3", ledger.SALE, VB, M_CABLE, q_out=4, value=4),
             mrow(mday(3, 16), "SRV:T-26-03-1", ledger.SALES_RETURN, VB, M_CABLE, q_in=1, value=1),
             mrow(mday(3, 15), "ShoStk:T-26-03-1", ledger.COUNT_SHORT, VA, M_CHARGER, q_out=1),
             mrow(mday(3, 15), "ExcStk:T-26-03-1", ledger.COUNT_OVER, VB, M_CABLE, q_in=2),
             mrow(mday(3, 16), "MRN:T-26-03-5", ledger.SUPPLIER_RECEIPT, SHOP, M_BANK, q_in=3, value=9),
             mrow(mday(3, 16), "SI:T-26-03-4", ledger.SALE, SIMWH, M_SIM, q_out=9, value=9)]
    return rows + list(extra)


class MoveWorld:
    """The reads of the movement audit, answered from the invented world: the ledger and its source, the stock
    snapshot, the price book, the MRN landed costs and the pricing sheet. Every statement must be a read."""

    def __init__(self, rows=None, parity_ok=True, source="file", sha=SHA_MV, sheet=None, snapshot_fails=False):
        self.rows = mv_rows() if rows is None else rows
        self.parity_ok, self.source, self.sha = parity_ok, source, sha
        self.sheet = [(5, "C12", "1.75")] if sheet is None else sheet
        self.snapshot_fails = snapshot_fails
        self.sql: list[str] = []
        self.asked: list[str] = []          # the prefer of each for_audit

    def rpc(self, sql):
        db.check_sql(sql)
        self.sql.append(sql)
        if sql == stock.SNAPSHOT_SQL:
            if self.snapshot_fails:
                raise RuntimeError("no network")
            return [{"item_name": STOCK_NAMES[k], "warehouse_name": MAIN, "net_qty": "1", "value_bhd": "1",
                     "as_of_date": D_MV.isoformat()} for k in STOCK_NAMES]
        if sql == stock.PRICE_BOOK_SQL:
            return [{"sku_code": ITEM_CODES[k], "item_name": STOCK_NAMES[k]} for k in STOCK_NAMES]
        if sql == stock.MRN_SQL:
            return [{"id": 1, "code": "C11", "cost": "0.4", "effective_date": "2026-02-01", "doc_no": "T-26-02-1"}]
        raise AssertionError(f"a statement the stand-in does not know: {sql[:70]}")

    def session(self, **_kw):
        world_ = self

        class Session:
            def q(self, sql, params=None):
                db.check_sql(sql)
                world_.sql.append(sql)
                assert sql == stock.SHEET_SQL, sql[:70]
                return [{"id": i, "code": c, "cost": v} for i, c, v in world_.sheet]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Session()

    def ledger_(self) -> ledger.Ledger:
        rows = self.rows
        return ledger.Ledger(rows=rows, source="Stock_ledger_test.xlsx", first_date=ledger.first_date(rows),
                             last_date=ledger.last_date(rows), sha=None if self.source == "database" else self.sha,
                             origin="database" if self.source == "database" else "cache")

    def for_audit(self, prefer="auto"):
        self.asked.append(prefer)
        led = self.ledger_()
        if self.source == "database":
            return led, {"source": "database", "parity": None, "note": "The stock ledger is read from the portal's "
                                                                       "database, as asked: the file was not opened."}
        if prefer == "file" or not self.parity_ok:
            return led, {"source": "file", "parity": None, "note": "The stock ledger is read from the file "
                         "Stock_ledger_test.xlsx, as asked: it was not compared with the portal."}
        return led, {"source": "file", "parity": {"ok": True}, "note": "The stock ledger is read from the file "
                     "Stock_ledger_test.xlsx: it agrees with what the portal holds."}


class moves_world:
    """The stand-ins, the movement fixture's config and a temporary data root, for a with-block; the audit's memo is
    emptied going in and coming out."""

    def __init__(self, world: MoveWorld | None = None, cfg: dict | None = None):
        self.world = world or MoveWorld()
        self.cfg = dict(MV_FIXTURE, **(cfg or {}))

    def __enter__(self) -> MoveWorld:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name).resolve()
        paths.set_data_root(self.root)
        config.use(self.cfg)
        moves.forget()
        self.saved = (db.rpc, db.session, ledger.for_audit)
        db.rpc, db.session, ledger.for_audit = self.world.rpc, self.world.session, self.world.for_audit
        return self.world

    def __exit__(self, *exc):
        db.rpc, db.session, ledger.for_audit = self.saved
        moves.forget()
        paths.set_data_root(None)
        config.use(None)
        self._tmp.cleanup()
        return False


def by_store(a) -> dict:
    return {r.store: r for r in a.stores}


class MvArgs:
    def __init__(self, *spec, ledger_="auto", golden=False):
        self.spec, self.ledger, self.golden_build, self.json, self.data_root = list(spec), ledger_, golden, False, None


@test("moves words: a window and rep:<name> in any order, the default 7 days; 'again' and 'top:' refused in one sentence")
def _():
    assert moves.parse_words([]) == (None, None)
    assert moves.parse_words(["mtd"]) == ("mtd", None) and moves.parse_words(["2026-03"]) == ("2026-03", None)
    assert moves.parse_words(["2026-03-01..2026-03-10"]) == ("2026-03-01..2026-03-10", None)
    assert moves.parse_words(["rep:Van", "A", "mtd"]) == ("mtd", "Van A") == moves.parse_words(["mtd", "rep:Van A"])
    for words in (["again"], ["top:3"], ["7d", "again"], ["rep:Van A", "top:5"]):
        exc = raises(moves.MovesInput, lambda w=words: moves.parse_words(w))
        assert str(exc) == moves.REFUSED_WORDS and "\n" not in str(exc), words
    assert "window" in str(raises(moves.MovesInput, lambda: moves.parse_words(["sideways"])))
    assert moves.DEFAULT_WINDOW == "7d"
    start = mday(2, 1)
    for window, (first, last, title) in {"7d": (mday(3, 11), D_MV, "Last 7 days"), "3d": (mday(3, 15), D_MV, "Last 3 days"),
                                         "1d": (D_MV, D_MV, "Latest day"), "mtd": (mday(3, 1), D_MV, "Month to date"),
                                         "2026-02": (start, mday(2, 28), "February 2026"),
                                         None: (start, D_MV, "The whole ledger")}.items():
        w = moves.resolve(window, D_MV, start)
        assert (w["first"], w["last"], w["title"]) == (first, last, title), (window, w)
    clipped = moves.resolve(("2026-01-20", date(2026, 3, 31)), D_MV, start)
    assert (clipped["first"], clipped["last"]) == (start, D_MV) and len(clipped["notes"]) == 2, clipped
    assert any("starts on" in n for n in clipped["notes"]) and any("stops there" in n for n in clipped["notes"])
    for window, said in (("2026-04", "No stock movement is loaded for April 2026 yet"),
                         ("2026-01", "the window ends before it"), ("0d", "one day or more"),
                         (("2026-03-01",), "two days")):
        exc = raises(moves.MovesInput, lambda w=window: moves.resolve(w, D_MV, start))
        assert said in str(exc) and "sales" not in str(exc).lower(), (window, str(exc))


@test("moves rep: a salesman's name (any case, its start) means his van; an outlet by its name; SIM and the main store are no answer")
def _():
    with moves_world():
        base = moves.audit("7d").base
        for typed, store in (("van a", VA), ("Van B", VB), ("VAN C - ACC WH", VC), ("causeway", SHOP), ("caus", SHOP)):
            assert base.name_of(moves.resolve_store(typed, base)) == store, typed
        exc = raises(moves.MovesInput, lambda: moves.resolve_store("Van", base))
        assert str(exc).startswith("More than one van or outlet fits 'Van'") and "salesman" not in str(exc)
        exc = raises(moves.MovesInput, lambda: moves.resolve_store("Main", base))
        assert str(exc).startswith("No van or outlet fits 'Main'. Vans and outlets in the stock ledger: ")
        assert SIMWH not in str(exc) and "SIM" not in str(exc) and MAIN not in str(exc)
        a = moves.audit("7d", rep="van b")
        assert a.rep == VB and [r.store for r in a.stores] == [VB] and a.totals.store == VB
        assert moves.audit("7d", rep="Van B") is a, "the memo knows the store, however it was typed"


@test("moves: every van's and outlet's check is nought in units and in BHD, the main store's too; SIM is out")
def _():
    with moves_world():
        a = moves.audit("7d")
        rows = by_store(a)
        assert list(rows) == [VA, VB, VC, SHOP], list(rows)            # vans by name, then the outlets
        assert all(r.balanced for r in a.stores + [a.main]) and not a.unbalanced
        want = {   # opening, received, issued_to, back_to_main, to_damage, to_other, not_received_yet, not_received,
                   # sold, customer_returns, counts, other, closing
            VA: (51, 25, 30, 2, 0, 3, 0, 0, 6, 0, -1, 0, 64),     # SIO 21 and its mirror STRV 18 move nothing
            VB: (30, 16, 6, 2, 1, 0, 0, 0, 4, 1, 2, 0, 42),
            VC: (0, 0, 7, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
            SHOP: (15, 7, 8, 0, 0, 0, 0, 0, 2, 0, 0, 3, 23),
            MAIN: (717, 4, 7, 0, 0, 39, 5, 9, 0, 0, 0, 0, 668)}
        keys = [k for k in moves.MOVE_KEYS if k != "check"]
        for store, figures in want.items():
            row = a.main if store == MAIN else rows[store]
            got = tuple(int(row.units[k]) for k in keys)
            assert got == figures, (store, dict(zip(keys, got)))
            assert row.units["check"] == 0 and row.bhd["check"] == 0 and row.uncosted["check"] == 0, store
        assert all(M_SIM not in r.items for r in a.stores + [a.main]), "SIM never enters a figure"
        legs = a.base.legs
        assert SIMWH not in rows and SIMWH_B not in rows and legs.out_of_scope == 0, "the rows are cut to accessories first"
        assert all(M_SIM not in leg.items for leg in legs.issues + legs.receipts)
        assert "SIO:T-26-02-5" not in a.result.by_issue and "SIO:T-26-03-23" not in a.result.by_issue
        # issued_to is what other stores sent to it; received is its own receive legs
        assert rows[VA].units["issued_to"] == 20 + 5 + 5 and rows[VA].units["received"] == 20 + 5   # STRV 18: a mirror
        # every row balances on the whole ledger and on any window as well
        for window in (None, "mtd", "2026-02", "3d", "2026-03-12..2026-03-15"):
            w = moves.audit(window)
            assert all(r.balanced for r in w.stores + [w.main]), window


@test("moves: a store's issues leave by the kind of store that actually booked them in; open and set-aside issues are not received")
def _():
    with moves_world():
        a = moves.audit("7d")
        rows = by_store(a)
        va = rows[VA]
        assert va.units["back_to_main"] == 2, "SIO 13 was booked in by the main store"
        assert va.units["to_other"] == 3, "SIO 12 was sent to the main store and booked in by Van B: not back to main"
        assert (va.units["not_received"], va.units["received"]) == (0, 25), \
            "SIO 21, sent to Van A itself, and STRV 18 that booked it back in there, both in the window: nothing moved"
        mirror = a.result.by_receipt["STRV:T-26-03-18"]
        assert isinstance(mirror, pairing.Aside) and mirror.partner == "SIO:T-26-03-21"
        assert mirror.reason == pairing.MIRRORS_OWN and mirror.kind == "receipt"
        assert rows[VB].units["to_damage"] == 1 and rows[VB].units["back_to_main"] == 2
        main = a.main
        assert (main.units["to_other"], main.units["not_received"], main.units["not_received_yet"]) == (39, 9, 5)
        bucket = a.base.bucket
        assert bucket["SIO:T-26-03-12"] == "to_other" and bucket["SIO:T-26-03-13"] == "back_to_main"
        assert bucket["SIO:T-26-03-14"] == "to_damage" and bucket["SIO:T-26-03-15"] == "not_received"
        assert bucket["SIO:T-26-03-17"] == "not_received" and bucket["SIO:T-26-03-16"] == "to_other"     # the twins
        assert bucket["SIO:T-26-03-18"] == bucket["SIO:T-26-03-19"] == "not_received_yet"
        assert bucket["SIO:T-26-03-21"] == "not_received_yet" and bucket["SIO:T-26-03-9"] == "not_received"
        assert a.base.mirror_day == {"SIO:T-26-03-21": mday(3, 16), "STRV:T-26-03-18": mday(3, 16)}
        # a movement put in no column shows: the check is not nought, and the screen says it is to check
        real = moves._row

        def dropped(base, store, buckets):
            if base.name_of(store) == VA:
                buckets = {k: v for k, v in buckets.items() if k != "sold"}
            return real(base, store, buckets)
        moves.forget()
        moves._row = dropped
        try:
            broken = moves.audit("7d")
        finally:
            moves._row = real
            moves.forget()
        assert by_store(broken)[VA].units["check"] == -6 and broken.unbalanced == [VA]
        text = "\n".join(moves.screen_lines(broken))
        assert "-6 to check" in text and "The check is not 0 for Van A: a movement fell in no column or in two" in text


@test("moves: an own-store issue nothing booked back in is not received yet, then an item to check (xfer_open); its mirror's are never")
def _():
    from scripts.ops import checks
    lone = (sio(27, mday(3, 16), VB, VB, [(M_CABLE, 1)])                  # Monday: due Tuesday, the ledger's last day
            + sio(29, mday(3, 12), VB, VB, [(M_CABLE, 2)]))               # Thursday: due Saturday, past by Tuesday
    with moves_world(MoveWorld(rows=mv_rows(extra=lone))) as w:
        a = moves.audit("7d")
        for no in ("SIO:T-26-03-27", "SIO:T-26-03-29"):
            x = next(x for x in a.result.aside if x.voucher == no)
            assert (x.reason, x.partner) == (pairing.OWN_STORE, None), no
        assert a.base.bucket["SIO:T-26-03-27"] == "not_received_yet" and a.base.bucket["SIO:T-26-03-29"] == "not_received"
        vb = by_store(a)[VB]
        assert (vb.units["not_received_yet"], vb.units["not_received"], vb.units["closing"]) == (1, 2, 39) and vb.balanced
        mine = [e for e in a.exceptions if e["issues"] == ["SIO:T-26-03-29"]]
        assert len(mine) == 1, a.exceptions
        e = mine[0]
        assert (e["kind"], e["from"], e["to"], e["to_kind"], e["set_aside"]) == ("not_received", VB, VB, "van",
                                                                                pairing.OWN_STORE)
        assert (e["bhd"], e["units"], e["deadline"], e["receipt"]) == (Decimal("0.800"), 2, mday(3, 14), None)
        numbers = {n for e in a.exceptions for n in e["issues"] + [e["receipt"]]}
        assert not numbers & {"SIO:T-26-03-27", "SIO:T-26-03-21", "STRV:T-26-03-18"}, "inside its deadline; a mirror pair"
        # the watch records it under its own rule, keyed on the issue, with the issue as its evidence
        found, notes = checks.found_items(a.exceptions, a.d, {}, a.rules)
        item = next(i for i in found["xfer_open"] if i["key"] == "xfer_open|SIO:T-26-03-29")
        assert item["evidence"] == ["SIO:T-26-03-29"] and item["bhd"] == Decimal("0.800") and not notes
        assert moves.brief_facts(7)["not_received_units"] == 9 + 2 and moves.brief_facts(7)["to_check_count"] == 6
        text = " ".join("\n".join(moves.screen_lines(a)).split())
        assert ("SIO:T-26-03-29 Thu 12 Mar, Van B to Van B (sent to its own store): not received after its deadline "
                "(Sat 14 Mar)") in text
        assert "SET ASIDE 4 vouchers of the window outside the ordinary pairing" in text and "cannot be paired" not in text
        assert "4 vouchers set aside (each named under SET ASIDE)" in text
        assert "SIO:T-26-03-27 Mon 16 Mar (sent to its own store)" in text
        assert "SIO:T-26-03-21 Mon 16 Mar (sent to its own store; booked back in there under STRV:T-26-03-18)" in text
        assert "STRV:T-26-03-18 Mon 16 Mar (books in SIO:T-26-03-21, sent to its own store)" in text
        mine = moves.audit("7d", rep="van a")
        own = [m for m in mine.store_moves if m["number"] in ("SIO:T-26-03-21", "STRV:T-26-03-18")]
        assert [(m["number"], m["receipt"]) for m in own] == [("SIO:T-26-03-21", "STRV:T-26-03-18")], own
        assert own[0]["outcome"] == "set aside: sent to its own store; booked back in there"
        # Van B's list says what became of each in an issue's words, as TO CHECK reads it (From and To: Van B)
        theirs = moves.audit("7d", rep="van b")
        words = {m["number"]: (m["from"], m["to"], m["outcome"]) for m in theirs.store_moves
                 if m["number"] in ("SIO:T-26-03-27", "SIO:T-26-03-29")}
        assert words == {"SIO:T-26-03-27": (VB, VB, "not received yet (due Tue 17 Mar)"),
                         "SIO:T-26-03-29": (VB, VB, "not received after its deadline (Sat 14 Mar), to check")}, words
        lines = moves.screen_lines(theirs)
        assert all(len(ln) <= 150 for ln in lines) and all(fmt.careful(ln) == ln for ln in lines)
        # the golden list keeps the issue as the case's evidence, and the review text names it
        root = paths.data_root()
        folder = root / "business_data" / "Focus ERP 2026-03-17"
        folder.mkdir(parents=True)
        real_load = ledger.load
        ledger.load = lambda path=None, use_cache=True: w.ledger_()
        try:
            res = moves.run(MvArgs(golden=True, ledger_=str(folder)))
        finally:
            ledger.load = real_load
        assert res["ok"], res
        doc = json.loads((paths.golden_dir() / "pairs_accessories.json").read_text(encoding="utf-8"))
        (case,) = [c for c in doc["exceptions"] if c["issues"] == ["SIO:T-26-03-29"]]
        (leg,) = case["issue_legs"]
        assert (leg["number"], leg["store"], leg["to"], case["receipt_leg"]) == ("SIO:T-26-03-29", VB, VB, None), case
        assert (case["to_kind"], case["set_aside"]) == ("van", pairing.OWN_STORE)
        review = (root / "exports" / "ops" / "2026-03-17" / "moves-golden-review.txt").read_text(encoding="utf-8")
        assert f"      issue    SIO:T-26-03-29 Thu 12 Mar, {VB} to {VB}: " in review


@test("moves: a mirror pair moves nothing: neither leg in a figure when both are in the window, each by itself when one is")
def _():
    pairs = (sio(30, mday(3, 14), VB, VB, [(M_CABLE, 1)]) + strv(25, mday(3, 15), VB, [(M_CABLE, 1)])   # a day apart
             + sio(31, mday(3, 13), MAIN, MAIN, [(M_CABLE, 2)]) + strv(26, mday(3, 13), MAIN, [(M_CABLE, 2)]))
    with moves_world(MoveWorld(rows=mv_rows(extra=pairs))):
        a = moves.audit("7d")
        assert {x.voucher: x.partner for x in a.result.aside if x.voucher in ("STRV:T-26-03-25", "STRV:T-26-03-26")} \
            == {"STRV:T-26-03-25": "SIO:T-26-03-30", "STRV:T-26-03-26": "SIO:T-26-03-31"}
        vb = by_store(a)[VB]
        assert (vb.units["received"], vb.units["not_received_yet"], vb.units["to_other"]) == (16, 0, 0) and vb.balanced
        assert (a.main.units["received"], a.main.units["back_to_main"]) == (4, 0) and a.main.balanced, "no To main"
        assert vb.shares() == {"sold": Decimal(4) / Decimal(46), "back": Decimal(2) / Decimal(46)}, "as without the pair"
        alone = by_store(moves.audit("2026-03-14..2026-03-14"))[VB]        # the issue inside, its mirror after
        assert alone.units["not_received_yet"] == 1 and alone.balanced
        after = by_store(moves.audit("2026-03-15..2026-03-17"))[VB]        # the mirror inside, its issue before
        assert after.units["received"] == 3 + 6 + 7 - 9 + 1 and after.balanced, after.units
        assert all(r.balanced for w in (None, "mtd", "7d", "3d") for r in moves.audit(w).stores + [moves.audit(w).main])
        assert not [e for e in a.exceptions if set(e["issues"]) & {"SIO:T-26-03-30", "SIO:T-26-03-31"}]


@test("moves: every item to check names the kind of its destination (to_kind): a set of twins sent to the damage store reads damage")
def _():
    twins_to_damage = (sio(7, mday(3, 3), MAIN, DMG, [(M_BANK, 1)]) + sio(8, mday(3, 3), MAIN, DMG, [(M_BANK, 1)])
                       + strv(9, mday(3, 4), DMG, [(M_BANK, 1)]))
    with moves_world(MoveWorld(rows=mv_rows(extra=twins_to_damage))):
        a = moves.audit("2026-03")
        assert all("to_kind" in e for e in a.exceptions)
        kinds = {(e["kind"], e["issues"][0] if e["issues"] else e["receipt"]): e["to_kind"] for e in a.exceptions}
        assert kinds[("twins", "SIO:T-26-03-7")] == "damage" and kinds[("twins", "SIO:T-26-03-16")] == "van"
        assert kinds[("damage_not_booked", "SIO:T-26-03-9")] == "damage" and kinds[("not_received", "SIO:T-26-03-15")] == "van"
        assert kinds[("elsewhere", "SIO:T-26-03-12")] == "main" and kinds[("qty_differs", "SIO:T-26-03-20")] == "outlet"
        assert kinds[("orphan", "STRV:T-26-03-19")] == "van"
        damage = next(e for e in a.exceptions if e["kind"] == "twins" and e["to_kind"] == "damage")
        assert (damage["missing"], damage["of"], damage["to"], damage["deadline"]) == (1, 2, DMG, mday(3, 10))


@test("moves: an issue booked in under two receipts (pairing step S) is one pair: both on its line, no item to check, checks 0")
def _():
    # SIO 28 sends a bank and a holder to Van B; Van B books them in under two receipts the same day, numbered next
    # to each other, the second holding exactly what the first lacks (the gate holds on the numbers: 7 and 8 apart)
    split = (sio(28, mday(3, 16), MAIN, VB, [(M_BANK, 2), (M_NOCOST, 1)])
             + strv(20, mday(3, 16), VB, [(M_BANK, 2)]) + strv(21, mday(3, 16), VB, [(M_NOCOST, 1)]))
    with moves_world(MoveWorld(rows=mv_rows(extra=split))) as w:
        a = moves.audit("7d")
        p = a.result.by_issue["SIO:T-26-03-28"]
        assert isinstance(p, pairing.Pair) and (p.step, p.outcome, len(p.also)) == ("S", "received", 1), p
        both = {p.receipt.no, p.also[0].no}
        assert both == {"STRV:T-26-03-20", "STRV:T-26-03-21"}
        assert a.result.by_receipt["STRV:T-26-03-20"] is p is a.result.by_receipt["STRV:T-26-03-21"]
        numbers = {n for e in a.exceptions for n in e["issues"] + [e["receipt"]]}
        assert not numbers & (both | {"SIO:T-26-03-28"}), "one pair, received: never an item to check"
        assert len(a.exceptions) == 5, "the week's five items stay as they were"
        vb = by_store(a)[VB]
        assert (vb.units["received"], vb.units["issued_to"]) == (16 + 3, 6 + 3) and vb.balanced
        assert a.base.bucket["SIO:T-26-03-28"] == "to_other" and a.main.units["to_other"] == 39 + 3
        assert all(r.balanced for r in a.stores + [a.main])
        # Van B's list: the issue's line names both receipts, and neither stands on a line of its own
        mine = moves.audit("7d", rep="van b")
        lines = [m for m in mine.store_moves if m["number"] in both | {"SIO:T-26-03-28"}]
        assert [m["number"] for m in lines] == ["SIO:T-26-03-28"], lines
        assert lines[0]["outcome"] == "received, under 2 receipts" and {lines[0]["receipt"], *lines[0]["also"]} == both
        assert f"{p.receipt.no} + {p.also[0].no}" in "\n".join(moves.screen_lines(mine))
        moved = next(m for m in sales_json(mine.facts())["store_moves"] if m["number"] == "SIO:T-26-03-28")
        assert (moved["receipt"], moved["also"]) == (p.receipt.no, [p.also[0].no])
        # the golden list keeps it with all three legs; the review text names both receipts, and the sample takes it
        root = paths.data_root()
        folder = root / "business_data" / "Focus ERP 2026-03-17"
        folder.mkdir(parents=True)
        real_load = ledger.load
        ledger.load = lambda path=None, use_cache=True: w.ledger_()
        try:
            res = moves.run(MvArgs(golden=True, ledger_=str(folder)))
        finally:
            ledger.load = real_load
        assert res["ok"] and res["flags"]["split"] == 1 and res["flags"]["sample"].count("split") == 1, res["flags"]
        doc = json.loads((paths.golden_dir() / "pairs_accessories.json").read_text(encoding="utf-8"))
        (s,) = doc["split"]
        assert (s["issue"], s["receipt"], s["also"], s["outcome"], s["step"]) == ("SIO:T-26-03-28", p.receipt.no,
                                                                                [p.also[0].no], "received", "S")
        assert [leg["number"] for leg in s["also_legs"]] == [p.also[0].no] and all(c["also"] == [] for c in doc["clean"])
        review = (root / "exports" / "ops" / "2026-03-17" / "moves-golden-review.txt").read_text(encoding="utf-8")
        assert "split (received)" in review and f"{p.receipt.no} + {p.also[0].no}" in review
        assert review.count("      and      ") == 1


@test("moves shares: sold and sent back over opening plus received; n/a with nothing to divide by")
def _():
    with moves_world():
        rows = by_store(moves.audit("7d"))
        s = rows[VA].shares()                          # 51 opening + 25 received (the own-store pair moves nothing)
        assert s == {"sold": Decimal(6) / Decimal(76), "back": Decimal(2) / Decimal(76)}, s
        assert rows[VC].shares() == {"sold": None, "back": None}
        assert rows[SHOP].shares()["back"] == 0
        table = "\n".join(moves.behaviour_table([rows[VA], rows[VC]]))
        assert "7.9%" in table and "2.6%" in table and "n/a" in table


def behaviour_rows() -> list[dict]:
    """A small February: a round trip and a re-issue within a week, one more return two weeks on (no trip), a
    Friday return at the month end, a voucher numbered after a later-dated one."""
    rows = [mrow(mday(2, 1), "MRN:T-26-02-1", ledger.SUPPLIER_RECEIPT, MAIN, M_CABLE, q_in=100, value=40)]
    rows += sio(1, mday(2, 2), MAIN, VA, [(M_CABLE, 10)]) + strv(1, mday(2, 2), VA, [(M_CABLE, 10)])     # a Monday
    rows += sio(2, mday(2, 4), VA, MAIN, [(M_CABLE, 10)]) + strv(2, mday(2, 4), MAIN, [(M_CABLE, 10)])   # sent back
    rows += sio(3, mday(2, 5), MAIN, VA, [(M_CABLE, 10)]) + strv(3, mday(2, 5), VA, [(M_CABLE, 10)])     # and again
    rows += sio(4, mday(2, 19), VA, MAIN, [(M_CABLE, 10)]) + strv(4, mday(2, 19), MAIN, [(M_CABLE, 10)])  # 14 days on
    rows += sio(5, mday(2, 27), VA, MAIN, [(M_CABLE, 2)]) + strv(5, mday(2, 28), MAIN, [(M_CABLE, 2)])    # a Friday
    rows += sio(6, mday(2, 18), MAIN, VA, [(M_CABLE, 1)]) + strv(6, mday(2, 18), VA, [(M_CABLE, 1)])      # back-dated
    assert [r["date"].weekday() for r in rows if r["vtype"] == ledger.ISSUE].count(4) == 1, "one Friday voucher"
    return rows


@test("moves behaviour: Friday vouchers, back-dated numbers, round trips and re-issues within 7 days, month-end returns")
def _():
    with moves_world(MoveWorld(rows=behaviour_rows())):
        a = moves.audit("2026-02")
        va, main = by_store(a)[VA], a.main
        assert va.behaviour == {"friday": 1, "backdated": 1, "round_trips": 1, "reissued": 1, "month_end_back": 1}, \
            va.behaviour
        assert main.behaviour["backdated"] == 1 and main.behaviour["friday"] == 0, main.behaviour
        assert main.behaviour["round_trips"] == 1 and main.behaviour["reissued"] == 1      # the same pairs, its side
        assert a.base.backdated == {"SIO:T-26-02-6", "STRV:T-26-02-6"}
        assert all(r.balanced for r in a.stores + [a.main])
        early = by_store(moves.audit("2026-02-01..2026-02-10"))[VA]
        assert (early.behaviour["round_trips"], early.behaviour["month_end_back"], early.behaviour["friday"]) == (1, 0, 0)
        late = by_store(moves.audit("2026-02-15..2026-02-28"))[VA]
        assert late.behaviour["round_trips"] == late.behaviour["reissued"] == 0, "two weeks apart is no round trip"
        text = "\n".join(moves.screen_lines(a))
        assert "Round trips" in text and "never an item to check" in " ".join(text.split())
        assert a.exceptions == [] or all(e["kind"] in moves.EXCEPTION_KINDS for e in a.exceptions)


@test("moves valuation: /yq-stock's basis (MRN, then the sheet, then the receipt rate), held to six decimals; no cost is counted apart")
def _():
    with moves_world():
        a = moves.audit("7d")
        assert a.costs == {M_CABLE: Decimal("0.4"), M_CHARGER: Decimal("1.75"), M_BANK: Decimal(3), M_NOCOST: None}
        sources = {name: it.cost_source for name, it in a.base.table.items()}
        assert sources == {M_CABLE: "mrn", M_CHARGER: "sheet", M_BANK: "receipt", M_NOCOST: None}, sources
        assert a.cost_label == "at landed cost"
        assert a.cost_basis == ("MRN; the pricing sheet where no MRN: 1 item; the latest receipt rate where neither: "
                                "1 item; 1 item without a cost"), a.cost_basis
        assert all(c is None or c.as_tuple().exponent >= -6 for c in a.costs.values())
        assert moves._cost6(Decimal(1) / Decimal(3)) == Decimal("0.333333")
        rows = by_store(a)
        vb = rows[VB].bhd
        assert vb["opening"] == Decimal("12.0") and vb["received"] == Decimal("1.2") + Decimal("10.5") + Decimal("12.25")
        shop = rows[SHOP]
        assert shop.uncosted["opening"] == 5 and shop.uncosted_units == 5 and shop.bhd["opening"] == 30
        assert a.main.uncosted["opening"] == 15 and a.main.uncosted["not_received_yet"] == 2
        assert a.main.bhd["opening"] == Decimal("771.3") and a.main.bhd["check"] == 0
        assert "never in BHD" in "\n".join(moves.screen_lines(a))
    with moves_world(MoveWorld(snapshot_fails=True)):                         # no snapshot: the costs that can be had
        a = moves.audit("7d")
        assert any("snapshot" in u for u in a.unread) and a.unmapped == []
        assert a.costs[M_BANK] == 3 and a.costs[M_CABLE] == Decimal("0.5"), "no codes: the receipt rates stand in"
        assert all(r.balanced for r in a.stores + [a.main])
        assert "Not read this time: the stock balance snapshot" in "\n".join(moves.screen_lines(a))


@test("moves to check: every kind with its fields; BHD per kind; largest first, then date and number; no cost last")
def _():
    with moves_world():
        a = moves.audit("2026-03")
        got = [(e["kind"], e["issues"][0] if e["issues"] else e["receipt"], e["bhd"]) for e in a.exceptions]
        # STRV 18 books in SIO 21, sent to its own store: set aside with it (pairing.MIRRORS_OWN), never an item here
        assert got == [("orphan", "STRV:T-26-03-19", Decimal("12.250")), ("not_received", "SIO:T-26-03-15", Decimal("12.000")),
                       ("qty_differs", "SIO:T-26-03-20", Decimal("3.000")), ("twins", "SIO:T-26-03-16", Decimal("2.000")),
                       ("elsewhere", "SIO:T-26-03-12", Decimal("1.200")),
                       ("damage_not_booked", "SIO:T-26-03-9", Decimal("0.800"))], got
        ex = {e["kind"] + "|" + (e["issues"][0] if e["issues"] else e["receipt"]): e for e in a.exceptions}
        keys = {"kind", "issues", "receipt", "from", "to", "received_at", "date", "deadline", "lag", "lines", "units",
                "bhd", "uncosted", "basis", "possible"}
        assert all(keys <= set(e) for e in a.exceptions)
        twins = ex["twins|SIO:T-26-03-16"]
        assert (twins["missing"], twins["of"], twins["open"], twins["issues"]) == (1, 2, 1, ["SIO:T-26-03-16", "SIO:T-26-03-17"])
        assert (twins["units"], twins["from"], twins["to"], twins["deadline"]) == (5, MAIN, VA, mday(3, 16))
        qty = ex["qty_differs|SIO:T-26-03-20"]
        assert qty["lines"] == [{"item": M_BANK, "units": 1, "issued": 6, "received": 5}] and qty["units"] == 1
        assert (qty["receipt"], qty["received_at"], qty["lag"]) == ("STRV:T-26-03-17", SHOP, 0)
        assert qty["basis"] == ["number", "date"]
        away = ex["elsewhere|SIO:T-26-03-12"]
        assert (away["from"], away["to"], away["received_at"], away["receipt"]) == (VA, MAIN, VB, "STRV:T-26-03-11")
        assert away["lines"] == [{"item": M_CABLE, "units": 3}] and away["deadline"] == mday(3, 14)
        gone = ex["not_received|SIO:T-26-03-15"]
        assert (gone["deadline"], gone["receipt"], gone["possible"], gone["to"]) == (mday(3, 15), None, [], VC)
        damage = ex["damage_not_booked|SIO:T-26-03-9"]
        assert (damage["to"], damage["deadline"], damage["date"]) == (DMG, mday(3, 12), mday(3, 5))
        lone = ex["orphan|STRV:T-26-03-19"]
        assert (lone["issues"], lone["from"], lone["received_at"], lone["before_ledger"]) == ([], None, VB, False)
        assert all(e["date"] >= mday(3, 1) for e in a.exceptions)
        assert moves.exceptions(a) == a.exceptions, "one builder: the same list every time"
        week = moves.audit("7d")
        assert [e["kind"] for e in week.exceptions].count("damage_not_booked") == 0, "dated 5 Mar: outside the week"
        assert len(week.exceptions) == 5
        mine = moves.audit("2026-03", rep="van b").exceptions
        assert [(e["kind"], e["receipt"]) for e in mine] == [("orphan", "STRV:T-26-03-19"),
                                                            ("elsewhere", "STRV:T-26-03-11")], mine
    # an item with no cost: its exceptions carry no BHD and come last, by date and number (STRV 22, a receipt of the
    # item that never has a cost that nothing explains, the day before STRV 19 of the charger, here with no cost)
    stray = strv(22, mday(3, 16), VC, [(M_NOCOST, 1)])
    with moves_world(MoveWorld(rows=mv_rows(charger_value=0, extra=stray), sheet=[])):
        a = moves.audit("2026-03")
        assert [e["receipt"] for e in a.exceptions[-2:]] == ["STRV:T-26-03-22", "STRV:T-26-03-19"]
        assert a.exceptions[-3]["bhd"] is not None
        assert all(e["bhd"] is None and e["uncosted"] for e in a.exceptions[-2:])
        lines = moves.to_check_lines(a)
        assert "2 with no cost on file, last" in lines[0] and any(ln.strip().startswith("no cost") for ln in lines)
        assert [x["kind"] for x in moves.exceptions_by_kind(a.exceptions)] == [
            "not_received", "damage_not_booked", "twins", "qty_differs", "elsewhere", "orphan"]
        orphans = moves.exceptions_by_kind(a.exceptions)[-1]
        assert orphans == {"kind": "orphan", "count": 2, "units": 8, "bhd": None, "uncosted": 8}, orphans


@test("moves: the figures per store are printed only from the database or a file that passed parity; the transfers still are")
def _():
    for world, ok in ((MoveWorld(), True), (MoveWorld(source="database"), True), (MoveWorld(parity_ok=False), False)):
        with moves_world(world):
            a = moves.audit("7d")
            assert a.source["per_store_ok"] is ok and (a.source["why"] is None) == ok
            text = "\n".join(moves._guarded(ln) for ln in moves.screen_lines(a))
            assert ("VANS AND OUTLETS" in text) == ok and ("MAIN STORE" in text) == ok
            assert ("Per-store figures not printed: the stock ledger file Stock_ledger_test.xlsx was read as it is and "
                    "not compared with the portal" in " ".join(text.split())) == (not ok)
            assert "TRANSFERS" in text and "TO CHECK" in text and a.exceptions, "the transfers and the items still stand"
            facts = a.facts()
            assert (facts["stores"] == []) == (not ok) and (facts["main"] is None) == (not ok)
            assert moves.brief_facts(7)["per_store_ok"] is ok
            if ok:
                assert moves.period_table(mday(3, 11), D_MV)["per_store_ok"] is True
            else:
                exc = raises(moves.MovesInput, lambda: moves.period_table(mday(3, 11), D_MV))
                assert "not compared with the portal" in str(exc)
                raises(moves.MovesInput, lambda: moves.store_balances(D_MV))
                raises(moves.MovesInput, lambda: moves.books_only(D_MV))
                assert "not compared" in moves.brief_facts(7)["note"]
    with moves_world() as w:                                                  # 'file' asked: not compared, held back
        assert moves.audit("7d", prefer="file").source["per_store_ok"] is False and w.asked[-1] == "file"


@test("moves screen: the heading names the window, the ledger's day and its source; the closing lines; careful words; no long digit runs")
def _():
    with moves_world():
        for window, rep in (("7d", None), ("mtd", None), (None, None), ("7d", "van a"), ("2026-03", "causeway")):
            a = moves.audit(window, rep=rep)
            lines = [moves._guarded(ln) for ln in moves.screen_lines(a)]
            text = "\n".join(lines)
            assert lines[0].startswith("MOVES  ") and "the stock ledger runs to Tue 17 Mar" in " ".join(text.split())
            assert "it agrees with what the portal holds" in text
            assert lines[-2:] == [moves.PORTAL_LINE, moves.NOT_MEASURED], lines[-2:]
            assert lines[-2] == ("The portal's Stock Movement page pairs the transfers with this same code since release 2b: "
                                 "the same pairs for the same window.")
            assert lines[-1] == "Not measured: the time from a Stock Request to its issue (Focus exports no request vouchers)."
            assert "never by the voucher number" in " ".join(text.split())
            assert all(fmt.careful(ln) == ln for ln in lines)
            blob = text + json.dumps(sales_json(a.facts()))
            assert not NINE.search(blob) and not LONG.search(blob), window
            assert all(len(ln) <= 150 for ln in lines), max(len(ln) for ln in lines)
            if rep:
                assert "ITS TRANSFERS" in text and "VANS AND OUTLETS" not in text and "THE STORE" in text
        week = "\n".join(moves.screen_lines(moves.audit("7d")))
        assert "TO CHECK  5 items, BHD 30.450 at landed cost, largest first" in week
        assert "Sent to it" in week and "SET ASIDE  2 vouchers" in week                 # SIO 21 and its mirror
        assert "From the main store to the vans and outlets 51 units (BHD 58.550 at landed cost); back to it from them " \
               "7; to the damage store 3." in " ".join(week.split())
        mine = "\n".join(moves.screen_lines(moves.audit("7d", rep="van a")))
        assert "SIO:T-26-03-10" in mine and "received" in mine and "TRANSFERS  " in mine


def sales_json(facts: dict) -> dict:
    """The facts as the saved copy holds them (sales.save_screen's own JSON rule)."""
    from scripts.ops import sales
    return sales._jsonable(facts)


@test("moves API: audit(None) holds every exception of the whole ledger; sha is the file's or db:<day>; exception_items by date")
def _():
    with moves_world() as w:
        whole, week = moves.audit(None), moves.audit("7d")
        assert whole.window["first"] == mday(2, 1) and whole.window["last"] == D_MV
        assert len(whole.exceptions) == 6 and len(week.exceptions) == 5
        assert {e["issues"][0] for e in whole.exceptions if e["issues"]} >= {"SIO:T-26-03-9"}
        assert whole.sha == SHA_MV == week.sha
        assert moves.exception_items("2026-03-01", "2026-03-17") == {M_CABLE, M_CHARGER, M_BANK}
        assert moves.exception_items(D_MV, D_MV) == {M_CHARGER} and moves.exception_items("2026-02-01", "2026-02-28") == set()
        assert whole.exception_items(mday(3, 5), mday(3, 5)) == {M_CABLE}
        assert isinstance(whole.result, pairing.Result) and whole.rows is w.rows and isinstance(whole.rules, pairing.Rules)
        assert whole.store_rules.kind(VA) == "van" and whole.store(VB.upper()).store == VB
    with moves_world(MoveWorld(source="database")):
        a = moves.audit(None)
        assert a.sha == "db:2026-03-17" and a.source["source"] == "database" and a.source["per_store_ok"] is True


@test("moves memo: one pairing and one set of cost reads per ledger; the same audit for the same window and store; a new ledger anew")
def _():
    calls = {"pair": 0, "costs": 0}
    real_pair, real_costs = pairing.pair, stock.cost_reads

    def counted_pair(*a, **kw):
        calls["pair"] += 1
        return real_pair(*a, **kw)

    def counted_costs():
        calls["costs"] += 1
        return real_costs()
    pairing.pair, stock.cost_reads = counted_pair, counted_costs
    try:
        with moves_world() as w:
            first = moves.audit("7d")
            assert moves.audit("7d") is first and moves.audit("7d", prefer="auto") is first
            assert calls == {"pair": 1, "costs": 1}
            month = moves.audit("mtd")
            assert month is not first and calls == {"pair": 1, "costs": 1}, "another window, the same ledger"
            assert moves.audit(None) is moves.audit(None) and moves.brief_facts(7)["to_check_count"] == 5
            assert calls == {"pair": 1, "costs": 1}
            w.sha = "sha256:" + "cd" * 32                                     # another export of the folder
            again = moves.audit("7d")
            assert again is not first and calls == {"pair": 2, "costs": 2} and again.sha == w.sha
            moves.forget()
            moves.audit("7d")
            assert calls == {"pair": 3, "costs": 3}
    finally:
        pairing.pair, stock.cost_reads = real_pair, real_costs


@test("moves period_table: the Movements sheet's table (every key, BHD exact, checks nought), the exceptions by kind; balances and books")
def _():
    with moves_world():
        t = moves.period_table("2026-03-11", "2026-03-17")
        assert (t["first"], t["last"]) == (mday(3, 11), D_MV) and t["basis"].startswith("at landed cost (MRN")
        assert [s["store"] for s in t["stores"]] == [VA, VB, VC, SHOP] and [s["kind"] for s in t["stores"]] == [
            "van", "van", "van", "outlet"]
        for s in t["stores"] + [t["main"], t["totals"]]:
            assert set(s["units"]) == set(s["bhd"]) == set(moves.MOVE_KEYS)
            plus = sum((s["bhd"][k] for k in moves.CHECK_PLUS), Decimal(0))
            minus = sum((s["bhd"][k] for k in moves.CHECK_MINUS), Decimal(0)) + s["bhd"]["closing"]
            assert plus - minus == 0, s["store"]                              # the sheet's own check, exactly nought
        assert t["totals"]["units"]["closing"] == 64 + 42 + 0 + 23 and t["totals"]["uncosted_units"] == 5
        assert t["counts"]["counts"]["received"] == 5 and t["counts"]["issues"]["vouchers"] == 12
        assert [(x["kind"], x["count"], x["units"], x["bhd"]) for x in t["exceptions"]] == [
            ("not_received", 1, 4, Decimal("12.000")), ("twins", 1, 5, Decimal("2.000")),
            ("qty_differs", 1, 1, Decimal("3.000")), ("elsewhere", 1, 3, Decimal("1.200")),
            ("orphan", 1, 7, Decimal("12.250"))], t["exceptions"]
        held = moves.store_balances(D_MV)
        assert held == {SHOP: {M_BANK: 16, M_CABLE: 2, M_NOCOST: 5}, VA: {M_CABLE: 57, M_CHARGER: 7},
                        VB: {M_CABLE: 29, M_CHARGER: 13}}, held
        assert moves.store_balances(D_MV, kinds=("main",)) == {MAIN: {M_BANK: 80, M_CABLE: 392, M_CHARGER: 183,
                                                                      M_NOCOST: 13}}
        assert moves.store_balances("2026-03-10")[VA] == {M_CABLE: 41, M_CHARGER: 10}
        books = moves.books_only(D_MV, held_days=30)
        assert [b["store"] for b in books] == [VA, VB, SHOP], books              # vans, then the outlets
        assert all(set(b) == {"store", "units", "bhd", "uncosted", "held_over"} for b in books)
        va, vb, shop = books
        assert (va["units"], va["bhd"], va["uncosted"], va["held_over"]) == (64, Decimal("35.050"), 0, []), va
        assert (vb["units"], vb["bhd"], vb["held_over"]) == (42, Decimal("34.350"), []), vb   # sold or received lately
        assert (shop["units"], shop["bhd"], shop["uncosted"]) == (23, Decimal("48.800"), 5), shop
        # held there, never sold there, last received on 4 Feb: 41 days to D
        assert shop["held_over"] == [{"item": STOCK_NAMES["nocost"], "code": "C14", "units": 5, "last_sale": None,
                                      "days": 41}], shop["held_over"]
        assert all(set(x) == {"item", "code", "units", "last_sale", "days"} for b in books for x in b["held_over"])
        assert moves.books_only(D_MV, held_days=60)[2]["held_over"] == [], "received inside the 60 days"
        early = moves.books_only("2026-02-03", held_days=1)
        assert [(b["store"], b["units"], b["bhd"], b["held_over"]) for b in early] == [
            (VA, 60, Decimal("37.500"), []), (VB, 30, Decimal("12.000"), [])], early
        for bad in ("2026-03-18", "2026-01-31"):
            raises(moves.MovesInput, lambda b=bad: moves.store_balances(b))
            raises(moves.MovesInput, lambda b=bad: moves.books_only(b))
    # a store whose stock has no cost at all is not valued at nought: bhd None, its units counted apart
    with moves_world(MoveWorld(rows=[r for r in mv_rows() if r["item"] == M_NOCOST])):
        (shop,) = [b for b in moves.books_only("2026-03-15") if b["store"] == SHOP]
        assert (shop["units"], shop["bhd"], shop["uncosted"]) == (5, None, 5), shop
        assert [(x["code"], x["days"]) for x in shop["held_over"]] == [("C14", 39)]


@test("moves brief_facts: the brief's MOVEMENTS figures of the last 7 days, as plain values")
def _():
    with moves_world():
        f = moves.brief_facts(7)
        assert f == {"first": "2026-03-11", "last": "2026-03-17", "ledger_to": "2026-03-17", "to_stores_units": 51,
                     "to_stores_bhd": "58.550", "back_units": 7, "damage_units": 3, "not_received_units": 9,
                     "not_received_bhd": "14.000", "to_check_count": 5, "to_check_bhd": "30.450", "per_store_ok": True,
                     "note": None}, f
        assert json.loads(json.dumps(f)) == f
        assert moves.brief_facts(3)["first"] == "2026-03-15"
        raises(ValueError, lambda: moves.brief_facts(0))


@test("moves: the order position is re-exported once scripts.ops.position is there, and only then")
def _():
    import importlib.util
    there = importlib.util.find_spec("scripts.ops.position") is not None
    assert hasattr(moves, "position_beside_main") == there
    if there:
        from scripts.ops import position
        assert moves.position_beside_main is position.position_beside_main


@test("moves command: the screen through the entry point, its copies by window and store, a word refused in one sentence")
def _():
    from contextlib import redirect_stdout
    with moves_world():
        root = paths.data_root()
        for argv, stem in ((["moves"], "moves"), (["moves", "mtd", "rep:van", "a"], "moves-mtd-rep-van-a"),
                           (["moves", "2026-03-01..2026-03-17"], "moves-2026-03-01-to-2026-03-17")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli.main(argv + ["--data-root", str(root)])
            out = buf.getvalue()
            assert code == 0 and out.startswith("MOVES  "), (argv, out[:200])
            folder = root / "exports" / "ops" / "2026-03-17"
            assert (folder / f"{stem}.txt").is_file() and (folder / f"{stem}.json").is_file(), sorted(folder.iterdir())
            facts = json.loads((folder / f"{stem}.json").read_text(encoding="utf-8"))
            assert facts["command"] == "moves" and facts["data_date"] == "2026-03-17"
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["moves", "again", "--data-root", str(root)])
        assert code == 1 and buf.getvalue().strip() == moves.REFUSED_WORDS
        res = moves.run(MvArgs())
        assert res["ok"] and res["flags"]["to_check"] == 5 and res["flags"]["per_store_ok"] is True
        assert res["flags"]["unbalanced"] == 0 and res["data_date"] == "2026-03-17"
        assert moves.run(MvArgs("rep:Nobody"))["exit"] == 1
        help_text = io.StringIO()
        with redirect_stdout(help_text):
            try:
                cli.main(["moves", "--help"])
            except SystemExit:
                pass
        assert "--golden-build" not in help_text.getvalue() and "--ledger" in help_text.getvalue()


@test("moves golden build (hidden): every exception and the first 50 clean pairs by hash, the review text with both legs; never from a worktree without a root")
def _():
    import hashlib
    rows = [mrow(mday(1, 2), "MRN:T-26-01-1", ledger.SUPPLIER_RECEIPT, MAIN, M_BANK, q_in=100, value=300)]
    for n in range(1, 61):                                                    # sixty clean January transfers
        day = mday(1, 2) + timedelta(days=n // 3)
        rows += sio(n, day, MAIN, VB, [(M_BANK, 1)]) + strv(n, day, VB, [(M_BANK, 1)])
    with moves_world(MoveWorld(rows=rows + mv_rows())) as w:
        root = paths.data_root()
        folder = root / "business_data" / "Focus ERP 2026-03-17"
        folder.mkdir(parents=True)
        paths.write_text(paths.golden_dir() / "pairing.json", json.dumps({"folder": folder.name}))
        real_load = ledger.load
        loaded: list[str] = []

        def load(path=None, use_cache=True):
            loaded.append(Path(path).name)
            return w.ledger_()
        ledger.load = load
        try:
            res = moves.run(MvArgs(golden=True))
            assert loaded == [folder.name] and w.asked == [], "the folder golden/pairing.json names, read as it is"
            pairs = moves.audit(None, prefer=str(folder)).result.pairs
        finally:
            ledger.load = real_load
        assert res["ok"], res
        doc = json.loads((paths.golden_dir() / "pairs_accessories.json").read_text(encoding="utf-8"))
        assert doc["folder"] == folder.name and doc["sha256"] == SHA_MV and doc["data_date"] == "2026-03-17"
        assert len(doc["exceptions"]) == 6 and doc["exceptions_by_kind"]["orphan"] == 1
        assert {"voucher": "STRV:T-26-03-18", "kind": "receipt", "reason": pairing.MIRRORS_OWN,
                "partner": "SIO:T-26-03-21"} in doc["aside"], doc["aside"]
        assert res["flags"]["exceptions"] == doc["exceptions_by_kind"] and res["flags"]["clean"] == moves.GOLDEN_CLEAN
        clean = doc["clean"]
        chosen = sorted(hashlib.sha256(p.issue.no.encode("utf-8")).hexdigest() for p in pairs
                        if p.step == "A" and not p.ambiguous)[:moves.GOLDEN_CLEAN]
        assert len(clean) == moves.GOLDEN_CLEAN and [c["sha256_of_issue"] for c in clean] == ["sha256:" + h for h in chosen]
        assert all(c["issue_leg"]["lines"] and c["receipt_leg"]["lines"] for c in clean)
        assert all(c["issue_legs"] for c in doc["exceptions"] if c["kind"] != "orphan")
        review = (root / "exports" / "ops" / "2026-03-17" / "moves-golden-review.txt").read_text(encoding="utf-8")
        assert review.startswith("GOLDEN REVIEW  ") and "SAMPLE  20 cases" in review and "CASES  " in review
        legs = sum(len(c["issue_legs"]) for c in doc["exceptions"])
        assert review.count("      issue    ") == legs + len(clean), (review.count("      issue    "), legs)
        assert review.count("      receipt  ") == len(doc["exceptions"]) + len(clean)
        assert not NINE.search(review) and not LONG.search(review)
        assert res["text"].startswith("GOLDEN  " + folder.name)
        # never from a code worktree that names no data root: the list belongs to the project folder
        saved = (paths.is_worktree, paths._override)
        try:
            paths.is_worktree, paths._override = (lambda: True), None
            refused = moves.golden_build(MvArgs(golden=True))
        finally:
            paths.is_worktree, paths._override = saved
        assert refused["exit"] == 1 and "code worktree" in refused["text"]


@test("moves: a transfer that also holds SIM is paired on its accessory lines, as the portal pairs them (never set aside)")
def _():
    mixed = (sio(24, mday(3, 16), VA, VB, [(M_CABLE, 2), (M_SIM, 3)])
             + strv(20, mday(3, 17), VB, [(M_CABLE, 2), (M_SIM, 3)]))
    with moves_world(MoveWorld(rows=mv_rows(extra=mixed))):
        a = moves.audit("7d")
        got = a.result.by_issue["SIO:T-26-03-24"]
        assert isinstance(got, pairing.Pair) and got.receipt.no == "STRV:T-26-03-20" and got.outcome == "received"
        assert got.issue.lines == ((M_CABLE, Decimal(2)),) and got.receipt.lines == ((M_CABLE, Decimal(2)),)
        assert not [x for x in a.result.aside if x.voucher in ("SIO:T-26-03-24", "STRV:T-26-03-20")]
        assert a.base.bucket["SIO:T-26-03-24"] == "to_other"
        rows = by_store(a)
        assert rows[VA].units["to_other"] == 3 + 2 and rows[VB].units["received"] == 16 + 2
        assert all(r.balanced for r in a.stores + [a.main])
        # the portal's way, by hand: the accessory lines of the rows, every voucher kept (scope None)
        cut = [r for r in a.rows if r["item"] in a.base.scope]
        portal = pairing.pair(pairing.legs_from_rows(cut, stores=moves.store_rules(), scope=None), data_date=a.d)
        assert portal.counts() == a.result.counts() and portal.by_issue.keys() == a.result.by_issue.keys()


@test("moves: identical issues from two senders are one item that names both; each sender's view holds it")
def _():
    twins = (sio(25, mday(3, 12), MAIN, VA, [(M_BANK, 2)]) + sio(26, mday(3, 12), VB, VA, [(M_BANK, 2)])
             + strv(21, mday(3, 17), VA, [(M_BANK, 2)]))
    with moves_world(MoveWorld(rows=mv_rows(extra=twins))):
        found = [e for e in moves.audit("7d").exceptions if e["kind"] == "twins" and "SIO:T-26-03-25" in e["issues"]]
        assert len(found) == 1 and found[0]["from"] == f"{MAIN} and {VB}" and found[0]["to"] == VA, found
        assert (found[0]["missing"], found[0]["of"]) == (1, 2)
        for rep in ("van b", "van a"):
            assert any("SIO:T-26-03-25" in e["issues"] for e in moves.audit("7d", rep=rep).exceptions), rep
        assert not any("SIO:T-26-03-25" in e["issues"] for e in moves.audit("7d", rep="van c").exceptions)
        assert all(r.balanced for r in moves.audit("7d").stores)


@test("moves: an accessory the bridge cannot place stays in every figure and is listed apart (UNMAPPED)")
def _():
    odd = [mrow(mday(2, 2), "ExcStk:T-26-02-2", ledger.COUNT_OVER, MAIN, M_ODD, q_in=3)]
    with moves_world(MoveWorld(rows=mv_rows(extra=odd))):
        a = moves.audit("7d")
        assert a.unmapped == [M_ODD] and M_ODD in a.base.scope and a.costs[M_ODD] is None
        assert a.main.units["opening"] == 717 + 3 and a.main.balanced
        text = " ".join("\n".join(moves.screen_lines(a)).split())
        assert "UNMAPPED 1 accessory the item bridge cannot place" in text and M_ODD in text


# ── moves portal: the portal's pairing against /yq-moves' own (the release check of phase 2b) ──────────────────────
# The fixture again, the main and damage stores under the public names the portal's store rules know
# (app/transfer_audit.py MAIN_STORE, DAMAGE_STORE); the view v_transfer_lines is made from the same rows as the view
# makes it (the two transfer types of the items that ever moved in the main store), its narration as typed. Two
# narrations differ only in an ID-shaped number, so they are one narration once masked.

P_MAIN, P_DMG = "Accessories Warehouse", "Accessories Damage Warehouse"
P_FIXTURE = {"warehouses.main": P_MAIN, "warehouses.damage": P_DMG, "warehouses.not_counted": [P_DMG, SMP]}
P_NARRATED = {"SIO:T-26-03-12": "back to the store, slip " + ID_A, "STRV:T-26-03-11": "back to the store, slip " + ID_B}


def public(rows) -> list[dict]:
    """The movement fixture with the main and the damage store under their public names; the narrations of
    P_NARRATED hashed as the ledger hashes them (the loader's mask first)."""
    from scripts import ingest
    rename = {MAIN: P_MAIN, DMG: P_DMG}
    out = []
    for r in rows:
        r = dict(r, wh=rename.get(r["wh"], r["wh"]), to_wh=rename.get(r["to_wh"], r["to_wh"]))
        if r["voucher"] in P_NARRATED and r["vtype"] in (ledger.ISSUE, ledger.RECEIVE):
            r["narr"] = pairing.narr_key(P_NARRATED[r["voucher"]], ingest.mask_personal_ids)
        out.append(r)
    return out


def view_rows(rows) -> list[dict]:
    """v_transfer_lines of these ledger rows, as the view holds them and the session returns them: the two transfer
    types of the items that ever moved in the main store, the date as text (the API casts it), the narration typed."""
    canon = pairing.StoreRules(main=P_MAIN).canon
    held = {r["item"] for r in rows if canon(r["wh"]) == canon(P_MAIN)}
    return [{"voucher": r["voucher"], "vtype": r["vtype"], "date": r["date"].isoformat(), "wh": r["wh"],
             "to_wh": r["to_wh"], "item": r["item"], "qty_in": r["qty_in"], "qty_out": r["qty_out"],
             "value_in": r["value_in"], "value_out": r["value_out"], "narration": P_NARRATED.get(r["voucher"])}
            for r in rows if r["vtype"] in (ledger.ISSUE, ledger.RECEIVE) and r["item"] in held]


class PortalWorld(MoveWorld):
    """MoveWorld with the portal's view beside it: the read-only session also answers the view's presence, the API's
    data date and the view's rows (view, else view_rows of the ledger). Every statement must be a read."""

    def __init__(self, rows=None, view=None, present=True, portal_day=D_MV, **kw):
        super().__init__(rows=public(mv_rows()) if rows is None else rows, **kw)
        self.view = view_rows(self.rows) if view is None else view
        self.present, self.portal_day = present, portal_day

    def rpc(self, sql):
        got = super().rpc(sql)
        if sql == stock.SNAPSHOT_SQL:                # the snapshot's main store under its public name too
            got = [dict(r, warehouse_name=P_MAIN) for r in got]
        return got

    def session(self, **_kw):
        world_ = self
        from app import transfer_audit as ta
        from scripts.ops import moves_portal

        class Session:
            def q(self, sql, params=None):
                db.check_sql(sql)
                world_.sql.append(sql)
                if sql == moves_portal.PRESENT_SQL:
                    return [{"found": "v_transfer_lines" if world_.present else None}]
                if sql == ta.DATA_DATE_SQL:
                    return [{"data_date": world_.portal_day.isoformat() if world_.portal_day else None}]
                if sql == moves_portal.LINES_SQL:
                    return [dict(r) for r in world_.view]
                assert sql == stock.SHEET_SQL, sql[:70]
                return [{"id": i, "code": c, "cost": v} for i, c, v in world_.sheet]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Session()


def verdicts(text: str) -> tuple[list[str], list[str]]:
    """(the lines with the word PASS, the lines with the word FAIL), read as scripts/r9_release.ps1 reads them."""
    lines = text.splitlines()
    return ([ln for ln in lines if re.search(r"\bPASS\b", ln)], [ln for ln in lines if re.search(r"\bFAIL\b", ln)])


SPLIT = (sio(28, mday(3, 16), P_MAIN, VB, [(M_BANK, 2), (M_NOCOST, 1)])
         + strv(20, mday(3, 16), VB, [(M_BANK, 2)]) + strv(21, mday(3, 16), VB, [(M_NOCOST, 1)]))


@test("moves portal: the portal's pairing of v_transfer_lines is /yq-moves' own: one PASS line, no FAIL, exit 0, both windows")
def _():
    from contextlib import redirect_stdout
    from app import transfer_audit as ta
    from scripts.ops import moves_portal
    with moves_world(PortalWorld(rows=public(mv_rows(extra=SPLIT))), cfg=P_FIXTURE) as w:
        root = paths.data_root()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["moves", "portal", "--data-root", str(root)])
        out = buf.getvalue()
        passed, failed = verdicts(out)
        assert code == 0 and passed == [moves_portal.PASSED] and not failed, out
        flat = " ".join(out.split())
        assert out.startswith("MOVES PORTAL  ") and flat.count("every voucher and every count the same") == 2, out
        assert "Last 7 days (Wed 11 Mar to Tue 17 Mar)" in flat and "The whole ledger (Sun 01 Feb to Tue 17 Mar)" in flat
        # the cases that ride on the rule, the same on both sides: a split pair, an own-store issue and its mirror, the
        # damage store's week, identical issues, a receipt with no issue, a pair at another store held by a narration
        # that only the mask makes one
        portal, mine = ta.pairs_from_rows(w.view, D_MV), moves.audit(None).result
        split = portal.by_issue["SIO:T-26-03-28"]
        assert split.step == "S" and {split.receipt.no, *(x.no for x in split.also)} == {"STRV:T-26-03-20",
                                                                                      "STRV:T-26-03-21"}
        assert portal.by_issue["SIO:T-26-03-12"].basis == ("narration", "number", "date") == \
            mine.by_issue["SIO:T-26-03-12"].basis
        assert portal.by_issue["SIO:T-26-03-18"].outcome == pairing.IN_GRACE, "the damage store's week"
        assert portal.counts() == mine.counts()
        # what was read: the view's presence, the API's data date, the rows (each a read); nothing was written
        mine_sql = [s for s in w.sql if s in (moves_portal.PRESENT_SQL, ta.DATA_DATE_SQL, moves_portal.LINES_SQL)]
        assert mine_sql == [moves_portal.PRESENT_SQL, ta.DATA_DATE_SQL, moves_portal.LINES_SQL], mine_sql
        assert w.asked and set(w.asked) == {"auto"}, "the ledger as /yq-moves reads it"
        assert not (root / "exports").exists(), "no copy of the screen, no record"
        res = moves.run(MvArgs("portal"))
        assert res["exit"] == 0 and res["ok"] and res["data_date"] == "2026-03-17", res
        assert res["flags"]["verdict"] == "PASS" and res["flags"]["vouchers_differ"] == 0 and res["flags"]["lines"] > 0
        assert all(fmt.careful(ln) == ln for ln in res["text"].splitlines())
        # --ledger is /yq-moves' own option
        moves_portal.run(MvArgs("portal", ledger_="db"))
        assert w.asked[-1] == "db"


@test("moves portal: before migration M1 the view is not there: not checked yet, exit 2, neither PASS nor FAIL, nothing more read")
def _():
    from contextlib import redirect_stdout
    from scripts.ops import moves_portal
    with moves_world(PortalWorld(present=False), cfg=P_FIXTURE) as w:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["moves", "portal", "--data-root", str(paths.data_root())])
        out = buf.getvalue()
        passed, failed = verdicts(out)
        assert code == 2 and moves_portal.NOT_YET in out and not passed and not failed, out
        assert "not checked yet: v_transfer_lines is not in the database" in out
        assert w.sql == [moves_portal.PRESENT_SQL] and w.asked == [], (w.sql, w.asked)
        res = moves.run(MvArgs("PORTAL"))
        assert res["exit"] == 2 and not res["ok"] and res["flags"]["verdict"] == "not checked"


@test("moves portal: a voucher the portal pairs otherwise is a FAIL that names it, with the counts that differ; the first 20, then how many more")
def _():
    from scripts.ops import moves_portal
    rows = public(mv_rows())
    # the portal's rows lack STRV 10 (SIO 10's receipt): /yq-moves has the pair, the portal an issue past its deadline
    with moves_world(PortalWorld(rows=rows, view=[r for r in view_rows(rows) if r["voucher"] != "STRV:T-26-03-10"]),
                     cfg=P_FIXTURE):
        res = moves.run(MvArgs("portal"))
        text = res["text"]
        passed, failed = verdicts(text)
        assert res["exit"] == 1 and not res["ok"] and not passed and len(failed) == 1, text
        assert failed[0].startswith("moves portal: FAIL - 2 vouchers and ") and "(the vouchers below)" in failed[0]
        assert ("  SIO:T-26-03-10: the portal: not received after the deadline; /yq-moves: received (STRV:T-26-03-10)"
                in text.splitlines()), text
        assert "  STRV:T-26-03-10: the portal: no such voucher; /yq-moves: booked in for SIO:T-26-03-10" in text.splitlines()
        flat = " ".join(text.split())
        assert "the counts differ (the portal's against /yq-moves'): received 4 against 5, lines 4 against 5" in flat, flat
        assert res["flags"]["vouchers_differ"] == 2 and res["flags"]["counts_differ"] > 0
    # the same issue, its lines otherwise on the portal's side: named for what differs
    other = [dict(r, qty_out=r["qty_out"] + 1) if r["voucher"] == "SIO:T-26-03-15" else r for r in view_rows(rows)]
    with moves_world(PortalWorld(rows=rows, view=other), cfg=P_FIXTURE):
        text = moves.run(MvArgs("portal"))["text"]
        assert "  SIO:T-26-03-15: not received after the deadline on both sides; what differs: its lines" in text.splitlines()
    # twenty-five issues the ledger does not hold: the first twenty listed by date and number, then how many more
    extra = [{"voucher": f"SIO:T-26-03-{n}", "vtype": ledger.ISSUE, "date": "2026-03-17", "wh": P_MAIN, "to_wh": VA,
              "item": M_CABLE, "qty_in": 0, "qty_out": n, "value_in": 0, "value_out": n, "narration": None}
             for n in range(100, 125)]
    with moves_world(PortalWorld(rows=rows, view=view_rows(rows) + extra), cfg=P_FIXTURE):
        text = moves.run(MvArgs("portal"))["text"]
        listed = [ln for ln in text.splitlines() if ln.startswith("  SIO:T-26-03-1")]
        assert [ln.split(":")[1] for ln in listed[:1]] == ["T-26-03-100"] and len(listed) == moves_portal.SHOWN, listed
        assert text.splitlines()[-1] == "  and 5 more." and "25 vouchers" in text


@test("moves portal: the portal's ledger on another day is a FAIL that says so; an empty view differs in every voucher")
def _():
    from scripts.ops import moves_portal
    with moves_world(PortalWorld(portal_day=D_MV - timedelta(days=1)), cfg=P_FIXTURE):
        res = moves.run(MvArgs("portal"))
        passed, failed = verdicts(res["text"])
        assert res["exit"] == 1 and not passed and failed[0].startswith("moves portal: FAIL - the ledger's last day")
        assert "The portal's stock ledger runs to Mon 16 Mar, /yq-moves' to Tue 17 Mar" in " ".join(res["text"].split())
    with moves_world(PortalWorld(view=[]), cfg=P_FIXTURE):
        res = moves.run(MvArgs("portal"))
        assert res["exit"] == 1 and res["flags"]["lines"] == 0 and res["flags"]["vouchers_differ"] > moves_portal.SHOWN
        assert "the portal: no such voucher" in res["text"]


@test("moves portal: the API's read and the API's function; every statement a read; app imported only when it runs; 'portal' alone")
def _():
    import ast
    from app import transfer_audit as ta
    from scripts.ops import moves_portal
    head, sep, _tail = ta.TRANSFER_LINES_SQL.partition(" WHERE ")
    assert sep and moves_portal.LINES_SQL == head, "the API's own columns, every day at once"
    for sql in (moves_portal.PRESENT_SQL, moves_portal.LINES_SQL, ta.DATA_DATE_SQL):
        db.check_sql(sql)
    tree = ast.parse((ROOT / "scripts" / "ops" / "moves_portal.py").read_text(encoding="utf-8"))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not [n for n in top if (getattr(n, "module", None) or "").startswith("app")
                or any(a.name.startswith("app") for a in getattr(n, "names", []))], "app is imported only when it runs"
    assert moves_portal.WORD == moves.PORTAL_WORD == "portal"
    with moves_world(PortalWorld(), cfg=P_FIXTURE) as w:
        res = moves.run(MvArgs("portal", "7d"))
        assert res["exit"] == 1 and moves_portal.PRESENT_SQL not in w.sql, "with another word it is no portal check"
    # fates: every voucher once (issues, receipts, the vouchers set aside); one side against itself differs in nothing
    with moves_world(PortalWorld(rows=public(mv_rows(extra=SPLIT))), cfg=P_FIXTURE):
        a = moves.audit(None)
        f = moves_portal.fates(a.result, a.window["first"], a.window["last"])
        legs = a.result.legs
        assert set(f) == {x.no for x in legs.issues} | {x.no for x in legs.receipts} | {x.voucher for x in a.result.aside}
        split = a.result.by_issue["SIO:T-26-03-28"]
        assert f["SIO:T-26-03-28"][1][1] == (split.receipt.no, *(x.no for x in split.also))
        assert set(f["SIO:T-26-03-28"][1][1]) == {"STRV:T-26-03-20", "STRV:T-26-03-21"}
        assert f["STRV:T-26-03-21"][1][:2] == ("paired", ("SIO:T-26-03-28",))
        assert moves_portal.differences(f, f) == []
        told = pairing.summarise(a.result, first=a.window["first"], last=a.window["last"])
        assert moves_portal.count_differences(told, told) == []


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
