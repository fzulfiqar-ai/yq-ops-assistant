"""python -m scripts.ops recon [YYYY-MM]     (behind /yq-month: the four month-end reconciliations)

scripts.ops.recon reads the words and hands the month here: run(args, month). With no month, the last whole month
before the data date. A month the data has not reached is refused in one sentence; a month the data has not
finished yet is worked out to the data date and says so.

THE FOUR BLOCKS, one status each. A difference is "to check" (fmt.careful guards every word), a missing paper is an
action and never a difference, and nothing reads as agreeing without its second side.
  1. MAIN STORE. The month's count of the main store (scripts.ops.counts.month_results: each store's newest count
     result of the month, the month of its book day) set beside the books it was read against (the stock ledger at
     the end of the day before the count): lines counted n of m, units short and over, BHD at landed cost.
     PROVEN when every counted line agrees with its book; DIFFERS (to check) when one does not; NOT COUNTED when no
     count of the month is on file, when its book side still waits for a load, or when no line was counted.
  2. VANS AND OUTLETS. The same for each van and outlet, from its SIGNED sheet (Counted by and Checked by filled
     in). A store without one is BOOKS ONLY: its stock at landed cost on the books and the items held there over
     HELD_DAYS days unsold, from /yq-moves (scripts.ops.moves.books_only(as_of, held_days=30), read lazily: "not
     worked out this time" while it cannot give them). The stores are every van and outlet that held an accessory
     at the end of the month or moved one in it, and every one with a count of the month. The block: DIFFERS when a
     store differs, else BOOKS ONLY when a store has no signed count (or there is none to count), else PROVEN; not
     worked out (None) when the stores on the books cannot be worked out, since a store nobody counted could then be
     missing from the list.
  3. SALES AND RECEIVABLES.
     invoicing   the load control: the day book = the register = v_sales for the month, invoice by invoice (lines
                 and BHD, VAT included: the day book's Gross, the register's Gross, v_sales.gross_bhd), from the
                 files of the loaded set (business_data/<the published folder>) when they reach the whole month;
                 when they do not, it is stated as the load's own verify (every load set its files beside the
                 database before they were published).
     statement   the kickback statement as app.statements.build_rows(month) drafts it: the reps with a target, their
                 sales on the statement's basis, the tier reached and the kickback; returns not deducted (the
                 owner's rule while no Sales Return register is loaded).
     collection  with the month-end Ledger export (scripts.ops.ledger_export.find(month) in the drop folders, or the
                 file `recon --ledger-file <path>` names: a Focus Ledger export as on the month's last day or later,
                 any other refused with the reason; then read): per customer account, opening (its balance at the
                 end of the month before) + sales invoices (SI) - receipts (RCTV) - returns (SRV) - credit notes (CN)
                 = closing, as the export's own rows and running balance give them; the closing set beside the
                 receivables ageing (ar_ageing) as on the month's last day, else the nearest one after it within
                 AR_DAYS days (the Ledger is then read to that day when it reaches it). The customer accounts are the
                 export's accounts that a receivables ageing has ever listed, and the ageing's own; accounts that
                 differ are listed to check, and so is every account another entry moved (a journal, a payment: the
                 identity has no term for it, shown as "other" in the totals). The ageing prints a credit balance
                 without its sign: a credit equal in amount agrees.
     returns     with the export: the month's sales returns ex-VAT per salesman (the export's own SRV legs), set
                 against each rep's statement, and the reps whose kickback tier they would change (app.shop's own
                 tier rule) listed to check. Without it, net of returns is an estimate from returned units: the
                 stock ledger's Sales Return units of the month at each item's average ex-VAT selling price of the
                 month, and no tier is judged on an estimate.
     The block: DIFFERS when the invoicing differs, an account is to check or a tier would change; COLLECTION NOT
     PROVEN without the export, or when it cannot be set beside an ageing; not worked out (None) when the
     collection is proven but the invoicing was not set beside the files here (it stands on the load's own verify)
     or a part could not be read; else PROVEN.
  4. PURCHASES. Every supplier receipt (Material Receipt Note) into the main store in the month, one row each: the
     purchase order on file (by the receipt's number, else the order whose model codes overlap the receipt's by at
     least PO_OVERLAP (Jaccard) and dated at most PO_DAYS days before it), the MRN XML on file (the Orders page's
     vault), the supplier invoice on file (the vault, or a supplier invoice /yq-stock's lead-time rule sets beside
     this receipt), the cost lines n of m (the receipt's items with a landed cost loaded under its number or its
     order's), and the order's flags (app.orders.detail(po, with_files=False): short, over, not received, not on
     the order, cost above or below the order are differences; a thin margin and an estimated cost are notes). A
     model code with several variants on the receipt reads "variant not provable from the PO". A row: PAPERS
     MISSING when a paper or a cost line is missing, or the MRN XML is filed where its order cannot read it (the
     order shows nothing received): the paper first, since until it is there the rest cannot be told (the order's
     flags then stand as a note: a cost line not loaded reads as "short" there); else DIFFERS (to check) when a flag
     says so, else PROVEN. Without the item codes the cost lines are not counted, never read as missing. The
     block: DIFFERS when a row reads DIFFERS, else PAPERS MISSING when a row misses a paper, else PROVEN (a month
     with no receipt: nothing is due).

A part that cannot be worked out says so in one line and the rest stands; a block whose own reading fails reads
"not worked out this time" (status None).

READS, all read-only, through a Source (tests hand month_blocks, purchases and run a stand-in): the stock ledger
(ledger.for_audit); the accessories and their item codes (/yq-stock's cost seam: stock.cost_reads and
stock.accessory_table); the count results (counts.month_results); /yq-moves' books_only; the loaded set's day book
and register (ledger.loaded_folder, read with the loader's own reader and parsers, the customer and narration
columns dropped at once); v_sales; state.json's last load; the kickback statement rows; the Ledger export; the
receivables ageing (ar_ageing, on the read-only session); purchase orders (v_po_item), the Orders page's vault
(order_files), the landed-cost lines (mrn_lines), the supplier invoices (supplier_prices) and app.orders.detail.

WRITES exports/ops/<data date>/recon-<YYYY-MM>.txt and .json (scripts.ops.sales.save_screen). Nothing else.

FOR THE WORKBOOKS: month_blocks(month) gives the four blocks as data (scripts.ops.workbook.sheets_recon writes the
month workbook's Reconciliation sheet from it, totals only); purchases(first, last) gives the receipt rows of any
window with their statuses (the weekly workbook's Actions sheet counts its PAPERS MISSING rows).
"""
from __future__ import annotations

import importlib
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from scripts.ops import db  # first: it blanks the side channels before any app import
from scripts.ops import counts, fmt, items, ledger, ledger_export, paths, sales, status
from scripts.ops.workbook import model as mdl

COMMAND = "recon"
PROVEN, DIFFERS, NOT_COUNTED, BOOKS_ONLY = "PROVEN", "DIFFERS", "NOT COUNTED", "BOOKS ONLY"
COLLECTION_NOT_PROVEN, PAPERS_MISSING = "COLLECTION NOT PROVEN", "PAPERS MISSING"
STATUSES = (PROVEN, DIFFERS, NOT_COUNTED, BOOKS_ONLY, COLLECTION_NOT_PROVEN, PAPERS_MISSING)
BLOCKS = (("main", "Main store"), ("vans", "Vans and outlets"), ("sales", "Sales and receivables"),
          ("purchases", "Purchases"))
NOT_WORKED = "not worked out this time"
HELD_DAYS = 30                    # books only: the items held in a store over this many days unsold (the plan's)
PO_DAYS = 200                     # an order matched by its codes is dated at most this many days before the receipt
PO_OVERLAP = Fraction(1, 2)       # ... and shares at least half of the model codes of the two (Jaccard, as pairing)
AR_DAYS = ledger_export.SEARCH_DAYS   # the ageing nearest after the month's end: at most this many days after it
FILS = Decimal("0.0005")          # two amounts agree to the fils
SHOWN = 6                         # names or numbers listed in one line, then "and n more"
MOVES_MODULE = "scripts.ops.moves"
ZERO = Decimal(0)
SIGN_STEP = "type the checker's name in Checked by and read the file again (/yq-month count)"

# the order's flags (app.orders.detail): the ones that are differences, the ones that are notes, and their words
DIFF_FLAGS = ("short", "over", "not_received", "not_ordered", "cost_overrun", "cost_drop")
NOTE_FLAGS = ("thin_margin", "cost_estimated")
FLAG_WORDS = {"short": "received short of the order", "over": "received over the order",
              "not_received": "ordered and not received", "not_ordered": "received and not on the order",
              "cost_overrun": "supplier price more than 5% above the order",
              "cost_drop": "supplier price more than 5% below the order",
              "thin_margin": "margin on arrival below 20%", "cost_estimated": "cost estimated from the supplier invoice"}
PAPER_ACTIONS = {"po": "upload its purchase order (the Focus PO PDF) on the Orders page",
                 "xml": "upload its MRN XML on the Orders page",
                 "invoice": "attach the supplier invoice to its order on the Orders page"}

SALES_SPAN_SQL = "SELECT MIN(sale_date)::text AS first_sale, MAX(sale_date)::text AS last_sale FROM v_sales"
# one row per invoice of the month: v_sales is the day book's lines joined to the register (all divisions)
PORTAL_INVOICES_SQL = (
    "SELECT invoice_no, COUNT(*)::text AS line_count, ROUND(COALESCE(SUM(gross_bhd), 0)::numeric, 3)::text AS gross "
    "FROM v_sales WHERE sale_date BETWEEN $1::date AND $2::date GROUP BY invoice_no")
# the month's accessories by item, ex-VAT and giveaways out (the kickback basis): the average selling price
PRICES_SQL = (
    "SELECT item_name, COALESCE(SUM(quantity), 0)::text AS qty, ROUND(COALESCE(SUM(net_bhd), 0)::numeric, 3)::text "
    "AS net FROM v_sales WHERE sale_date BETWEEN $1::date AND $2::date AND division = 'Accessories' "
    "AND NOT is_giveaway GROUP BY item_name")
PO_SQL = ("SELECT po_no, po_date::text AS po_date, upper(code) AS code FROM v_po_item "
          "WHERE po_no IS NOT NULL AND code IS NOT NULL")
FILES_SQL = "SELECT po_no, kind FROM order_files WHERE po_no IS NOT NULL"
MRN_LINES_SQL = "SELECT doc_no, upper(sku_code) AS code FROM mrn_lines WHERE doc_no IS NOT NULL AND sku_code IS NOT NULL"
# the receivables ageing is not open to the read RPC: these three go through the read-only session (psycopg %s)
AR_DATES_SQL = ("select distinct as_of_date::text as as_of from ar_ageing where as_of_date between %s and %s "
                "order by 1")
AR_ROWS_SQL = "select account, balance_bhd::text as balance from ar_ageing where as_of_date = %s"
AR_NAMES_SQL = "select distinct account from ar_ageing"


class ReconInput(ValueError):
    """Something the command cannot answer: one plain sentence, printed as it is."""


class NotYet(RuntimeError):
    """A part another package has not built yet: the words go into its one line."""


# ── small helpers ─────────────────────────────────────────────────────────────

def _dec(x) -> Decimal | None:
    return None if x is None or x == "" else fmt.dec(x)


def _q(x) -> Decimal:
    return fmt.q3(x)


def _fold(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{fmt.units(n)} {one if n == 1 else (many or one + 's')}"


def _listed(words, limit: int = SHOWN) -> str:
    words = [str(w) for w in words]
    shown = ", ".join(words[:limit])
    return shown + (f" and {len(words) - limit} more" if len(words) > limit else "")


def _guarded(line: str) -> str:
    """Every line shown passes the wording guard (fmt.careful); a word from the data that the guard refuses is
    blanked, never printed."""
    import re
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


def _safe(name) -> str:
    """A name from the data as it may be printed: spaces collapsed, an ID-shaped run of digits masked (the loader's
    own mask: nine digits, or ten and more; scripts/text_mask.py, standard library only)."""
    from scripts.text_mask import mask_personal_ids
    return mask_personal_ids(" ".join(str(name or "").split())) or ""


def shown_status(status_: str | None) -> str:
    if status_ is None:
        return "NOT WORKED OUT"
    return "DIFFERS (to check)" if status_ == DIFFERS else status_


def month_words(month: str) -> str:
    first, _last = mdl.month_bounds(month)
    return first.strftime("%B %Y")


def _bhd_or_dash(x) -> str:
    return "-" if x is None else fmt.bhd(x)


def _units_or_dash(x) -> str:
    return "-" if x is None else fmt.units(x)


def _store_label(store: str) -> str:
    """A store as the month workbook names a salesman or an outlet: the warehouse without ' - Acc WH'."""
    return str(store or "").replace(mdl.WH_SUFFIX, "").strip()


def named_file(text) -> Path:
    """The file `recon --ledger-file` names: as typed (quotes taken off); a relative name that is not there is looked
    for under the data root."""
    raw = str(text).strip().strip('"').strip("'")
    given = Path(raw)
    if given.is_absolute() or given.exists():
        return given
    under = paths.data_root() / raw
    return under if under.exists() else given


def not_a_ledger(path: Path) -> str | None:
    """None when the file reads as a Focus Ledger export (its header row is the Ledger's, as the parser checks it);
    else why not, one sentence naming the file. A file that is not there is ledger_export.find's to say."""
    if not Path(path).is_file():
        return None
    import pandas as pd
    from scripts import ingest
    try:
        top = pd.read_excel(path, header=None, dtype=object, engine="openpyxl", nrows=ingest.HEADER_SCAN_ROWS)
    except Exception as exc:  # noqa: BLE001  (its kind, never its text: that can quote a cell)
        return (f"the file named, {Path(path).name}, could not be opened as a workbook ({type(exc).__name__}): name the "
                ".xlsx export of Focus's Ledger report")
    if ingest.header_row_index(top, "ledger_entries") is None:
        return (f"the file named, {Path(path).name}, is not a Focus Ledger export (its header row is not the Ledger's: "
                "Date, Voucher, Account, Debit, Credit, Balance ...): name the Ledger report of all accounts")
    return None


# ── the reads ─────────────────────────────────────────────────────────────────

class Source:
    """Where the reconciliations' data comes from: production and the project folder's files, read-only. The stock
    ledger and the accessory table are read once per Source. Tests hand month_blocks, purchases and run a stand-in
    with the same methods."""

    def __init__(self, prefer: str = "auto", ledger_file=None):
        self.prefer = prefer or "auto"
        self.ledger_file = ledger_file          # recon --ledger-file <path>: the month-end Ledger export, named
        self._ledger = None
        self._table = None

    # the stock ledger, its accessories and the counts
    def stock_ledger(self) -> tuple:
        """(the Ledger, info) of ledger.for_audit: the loaded file when it equals production, else the database."""
        if self._ledger is None:
            self._ledger = ledger.for_audit(self.prefer)
        return self._ledger

    def accessories(self, rows) -> tuple[dict, list]:
        """({ledger name: item code or None} of the accessories as /yq-stock counts them, the reads that failed),
        through its cost seam: stock.cost_reads() and stock.accessory_table(rows, price book, snapshot)."""
        if self._table is None:
            from scripts.ops import stock
            got = stock.cost_reads()
            made = stock.accessory_table(rows, got.get("price_book"), got.get("snapshot") or [])
            table = made[0] if isinstance(made, tuple) else made
            self._table = ({name: getattr(it, "code", None) for name, it in table.items()},
                           [str(u) for u in got.get("unread") or []])
        return self._table

    def count_results(self, month: str) -> dict:
        return counts.month_results(month)

    def store_rules(self):
        return counts.store_rules()

    def books_only(self, as_of: date):
        """/yq-moves' books-only figures (its stock at cost and the items held over HELD_DAYS days unsold, per van
        and outlet), read lazily: NotYet while the module, or its books_only, is not there."""
        try:
            moves = importlib.import_module(MOVES_MODULE)
        except ModuleNotFoundError as exc:
            if exc.name == MOVES_MODULE:
                raise NotYet("/yq-moves cannot give them yet") from None
            raise
        give = getattr(moves, "books_only", None)
        if not callable(give):
            raise NotYet("/yq-moves has no books_only yet")
        said = getattr(moves, "MovesInput", None)        # its own one-sentence refusal (per-store figures not proven)
        try:
            return give(as_of, held_days=HELD_DAYS)
        except Exception as exc:
            if isinstance(said, type) and isinstance(exc, said):
                raise NotYet(str(exc).rstrip(".")) from None
            raise

    # sales and receivables
    def sales_files(self) -> tuple[str | None, Path | None, Path | None]:
        """(the loaded set's folder name, its day book, its register): ledger.loaded_folder(), the files directly in
        it that scripts.ingest.classify calls order_lines and orders (the first of each by name; None when absent)."""
        folder = ledger.loaded_folder()
        if folder is None:
            return None, None, None
        from scripts import ingest
        found: dict[str, Path] = {}
        for p in sorted(Path(folder).iterdir(), key=lambda p: p.name.lower()):
            if p.is_file() and not p.name.startswith("~$") and p.suffix.lower().startswith(".xls"):
                kind = ingest.classify(p.name)
                if kind in ("order_lines", "orders"):
                    found.setdefault(kind, p)
        return Path(folder).name, found.get("order_lines"), found.get("orders")

    def read_sales_file(self, path: Path, kind: str) -> tuple[date | None, list[dict]]:
        """(the title block's As-on date, [{invoice, day, gross}]) of a day book (kind 'order_lines') or a register
        ('orders'), through the loader's own reader and parser. The customer and narration columns are left behind
        the moment the parser returns."""
        from scripts import ingest
        grid = ingest.read_grid(path)
        as_on = fmt.as_date(ingest.report_date_from_title(grid))
        if kind == "order_lines":
            rows = [{"invoice": r["invoice_no"], "day": fmt.as_date(r["line_date"]), "gross": r["gross_bhd"]}
                    for r in ingest.parse_order_lines(grid, path.name)]
        else:
            rows = [{"invoice": r["invoice_no"], "day": fmt.as_date(r["order_date"]), "gross": r["gross_bhd"]}
                    for r in ingest.parse_orders(grid, path.name)]
        return as_on, rows

    def sales_span(self) -> tuple[date | None, date | None]:
        rows = db.rpc(SALES_SPAN_SQL)
        r = rows[0] if rows else {}
        return fmt.as_date(r.get("first_sale")), fmt.as_date(r.get("last_sale"))

    def portal_invoices(self, first: date, last: date) -> dict:
        """{invoice: (lines, BHD gross)} of v_sales for the days."""
        return {str(r["invoice_no"]): (int(fmt.dec(r.get("line_count"))), _q(r.get("gross")))
                for r in db.rpc_params(PORTAL_INVOICES_SQL, [first.isoformat(), last.isoformat()])}

    def last_load(self) -> dict:
        last = status.read_state().get("last_load")
        return last if isinstance(last, dict) else {}

    def statement_rows(self, month: str) -> list[dict]:
        from app import statements
        return statements.build_rows(month)

    def tier(self, target, amount: Decimal, data_through) -> tuple[int, Decimal] | None:
        """(the tier reached, the kickback at it) for `amount` on a rep's target, by app.shop's own rule."""
        from app import shop
        tp = shop.tier_progress(target, amount, data_through)
        return None if tp is None else (int(tp["tier_reached"]), _q(tp["kickback_bhd"]))

    def ledger_export(self, month: str):
        """(the Export, the sentence that says which file) or (None, why there is none). The drop folders are
        searched (ledger_export.find), unless a file is named (recon --ledger-file <path>): then that file, wherever
        it lies and whatever its name, when it is a Focus Ledger export as on the month's last day or later; any
        other is refused, with the reason. A file that is there and cannot be read raises its own one-sentence
        error."""
        if self.ledger_file:
            path = named_file(self.ledger_file)
            refused = not_a_ledger(path)
            if refused:
                return None, refused
            path, why = ledger_export.find(month, path=path)
        else:
            path, why = ledger_export.find(month)
        if path is None:
            return None, why
        return ledger_export.read(path), why

    def ar_dates(self, lo: date, hi: date) -> list[date]:
        with db.session() as s:
            return [d for d in (fmt.as_date(r.get("as_of")) for r in s.q(AR_DATES_SQL, (lo, hi))) if d]

    def ar_rows(self, as_of: date) -> dict:
        """{account: balance} of the receivables ageing as on that day."""
        with db.session() as s:
            return {str(r["account"]): _q(r.get("balance")) for r in s.q(AR_ROWS_SQL, (as_of,)) if r.get("account")}

    def ar_names(self) -> set:
        """Every account any receivables ageing has listed, folded."""
        with db.session() as s:
            return {_fold(r["account"]) for r in s.q(AR_NAMES_SQL) if r.get("account")}

    def month_prices(self, first: date, last: date) -> dict:
        """{day-book item name: (units, BHD ex-VAT)} of the month's accessories, giveaways out."""
        return {str(r["item_name"]): (fmt.dec(r.get("qty")), _q(r.get("net")))
                for r in db.rpc_params(PRICES_SQL, [first.isoformat(), last.isoformat()]) if r.get("item_name")}

    # purchases
    def po_lines(self) -> list[dict]:
        return db.rpc(PO_SQL)

    def order_files(self) -> list[dict]:
        return db.rpc(FILES_SQL)

    def mrn_lines(self) -> list[dict]:
        return db.rpc(MRN_LINES_SQL)

    def supplier_invoices(self) -> list[dict]:
        from scripts.ops import stock
        return db.rpc(stock.SUPPLIER_SQL)

    def order_detail(self, po_no: str) -> dict:
        from app import orders
        return orders.detail(po_no, with_files=False)


def _attempt(work, what: str, warnings: list):
    """(the answer, None), or (None, 'not worked out this time (<why>)') when it fails: one part, never the screen."""
    try:
        return work(), None
    except NotYet as exc:
        return None, f"{NOT_WORKED} ({exc})"
    except Exception as exc:  # noqa: BLE001  (one part of the answer, never the answer)
        warnings.append(f"{what} could not be worked out: {type(exc).__name__}: {str(exc)[:160]}")
        return None, f"{NOT_WORKED} ({what} could not be read: {type(exc).__name__})"


# ── 1 and 2: the counts ───────────────────────────────────────────────────────

def count_view(result: dict) -> dict:
    """What one store's count result says (counts.compare's file), for a block. No name typed on the sheet leaves
    here: `signed` only says whether Counted by and Checked by were both filled in."""
    t = result.get("totals") or {}
    rows = result.get("rows") or []
    differ = sum(1 for x in rows if x.get("counted") is not None and (_dec(x.get("diff")) or ZERO) != 0)
    return {"store": " ".join(str(result.get("store") or "").split()), "kind": result.get("kind"),
            "count_date": fmt.as_date(result.get("count_date")), "book_day": fmt.as_date(result.get("book_day")),
            "waits": result.get("book") != "ready", "prep_kind": result.get("prep_kind"),
            "lines": int(t.get("lines") or 0), "counted": int(t.get("counted") or 0), "added": int(t.get("added") or 0),
            "differ": differ, "short_units": _dec(t.get("short_units")), "over_units": _dec(t.get("over_units")),
            "short_bhd": _dec(t.get("short_bhd")), "over_bhd": _dec(t.get("over_bhd")), "costed": bool(result.get("costed")),
            "uncosted": int(t.get("uncosted_differences") or 0), "moved_on_count_day": int(t.get("moved_on_count_day") or 0),
            "signed": bool(" ".join(str(result.get("counted_by") or "").split())
                           and " ".join(str(result.get("checked_by") or "").split()))}


def count_agrees(v: dict) -> bool:
    return (v["short_units"] or ZERO) == 0 and (v["over_units"] or ZERO) == 0


def counted_words(v: dict) -> str:
    """'58 of 60 lines counted (+1 added)'."""
    return f"{fmt.units(v['counted'])} of {_plural(v['lines'], 'line')} counted" + (
        f" (+{fmt.units(v['added'])} added)" if v["added"] else "")


def counted_cell(v: dict) -> str:
    """'58 of 60', '58 of 60 +1': a table's Lines counted cell."""
    return f"{fmt.units(v['counted'])} of {fmt.units(v['lines'])}" + (f" +{fmt.units(v['added'])}" if v["added"] else "")


def difference_words(v: dict) -> str:
    """The units and BHD short and over of a count that differs."""
    said = (f"{_plural(v['differ'], 'line')} {'differs' if v['differ'] == 1 else 'differ'} from the book, to check: "
            f"{fmt.units(v['short_units'] or 0)} units short and {fmt.units(v['over_units'] or 0)} over")
    if v["costed"] and v["short_bhd"] is not None:
        said += f", BHD {fmt.bhd(v['short_bhd'])} short and BHD {fmt.bhd(v['over_bhd'] or 0)} over at landed cost"
        if v["uncosted"]:
            said += f" ({_plural(v['uncosted'], 'difference')} with no cost left out)"
    return said


def _newest(results) -> dict:
    return max(results, key=lambda r: (str(r.get("count_date") or ""), str(r.get("read_at") or "")))


def main_block(results: dict, rules, month: str) -> dict:
    """1. The main store's count of the month set beside its books (see the module note)."""
    words = month_words(month)
    found = [r for r in results.values() if r.get("kind") == "main" or rules.kind(r.get("store")) == "main"]
    if not found:
        return {"status": NOT_COUNTED, "count": None,
                "lines": [f"No count of the main store for {words} is on file: the books alone prove nothing physical. "
                          "The cycle list: /yq-month prep; the counts typed in: /yq-month count <file>."]}
    v = count_view(_newest(found))
    what = "a full count" if v["prep_kind"] == "full" else "the cycle list"
    if v["waits"]:
        return {"status": NOT_COUNTED, "count": v, "lines": [
            f"A count of {fmt.day(v['count_date'])} ({what}) is on file; its book side waits for a load with data to "
            f"{fmt.day_long(v['book_day'])}, so it is not set beside the books yet: once that load is in, read the "
            "file again (/yq-month count <file>)."]}
    if v["counted"] + v["added"] == 0:
        return {"status": NOT_COUNTED, "count": v, "lines": [
            f"The count of {fmt.day(v['count_date'])} ({what}) has no line counted: nothing is set beside the books."]}
    head = (f"Counted on {fmt.day(v['count_date'])} ({what}), set beside the books at the end of {fmt.day(v['book_day'])}:"
            f" {counted_words(v)}.")
    if count_agrees(v):
        lines = [head, "Every counted line agrees with its book."]
        st = PROVEN
    else:
        said = difference_words(v)
        lines = [head, said[0].upper() + said[1:] + ". The items: /yq-month count."]
        st = DIFFERS
    if v["moved_on_count_day"]:
        lines.append(f"{_plural(v['moved_on_count_day'], 'item')} moved in the store on the count day: a possible cause "
                     "of a difference.")
    return {"status": st, "count": v, "lines": lines}


def stores_on_books(rows, rules, accessory: set | None, first: date, as_of: date) -> list[str]:
    """The vans and outlets that held an accessory at the end of `as_of` or moved one from `first` to it (the ledger's
    one name each), in the order of their names."""
    names = ledger.warehouse_names(rows)
    kinds: dict[str, str] = {}
    out: set[str] = set()
    for (store, item), units in ledger.balances(rows, as_of).items():
        if units != 0 and (accessory is None or item in accessory):
            kind = kinds.setdefault(store, rules.kind(store))
            if kind in ("van", "outlet"):
                out.add(store)
    for r in rows:
        if first <= r["date"] <= as_of and (accessory is None or r["item"] in accessory):
            store = names[r["wh"]]
            if kinds.setdefault(store, rules.kind(store)) in ("van", "outlet"):
                out.add(store)
    return sorted(out, key=lambda s: (_fold(s), s))


_UNITS_KEYS, _BHD_KEYS = ("units", "on_hand", "stock_units"), ("bhd", "cost_bhd", "value_bhd", "stock_bhd")
_HELD_KEYS, _UNCOSTED_KEYS = ("held", "held_over", "items_held", "stale"), ("uncosted", "uncosted_units", "uncosted_items")


def _field(entry, names):
    for n in names:
        v = entry.get(n) if isinstance(entry, dict) else getattr(entry, n, None)
        if v is not None:
            return v
    return None


def books_view(answer, rules) -> dict:
    """/yq-moves' books_only answer as {store (canon): {units, bhd, uncosted, held_items, held_units, held_bhd}}.

    Read as {store: entry} or a list of entries that name their store ('store', 'warehouse' or 'name'). An entry
    (a dict or an object) gives its stock as `units` and `bhd` (BHD at cost), or a `stock` part holding them, and
    the items held over the days unsold as `held`: a count, a list of them (their `units` and `bhd` added up), or a
    part with `items` (or `count`), `units` and `bhd`. ValueError when an entry gives neither units nor BHD."""
    pairs = []
    if isinstance(answer, dict):
        pairs = list(answer.items())
    else:
        for entry in answer or []:
            pairs.append((_field(entry, ("store", "warehouse", "name")), entry))
    out: dict = {}
    for store, entry in pairs:
        stock_part = _field(entry, ("stock",))
        base = stock_part if stock_part is not None and not isinstance(stock_part, (int, float, Decimal)) else entry
        units, bhd = _dec(_field(base, _UNITS_KEYS)), _dec(_field(base, _BHD_KEYS))
        if not store or (units is None and bhd is None):
            raise ValueError("books_only gave an entry this command does not read")
        held = _field(entry, _HELD_KEYS)
        held_items = held_units = held_bhd = None
        if isinstance(held, (int, Decimal)) and not isinstance(held, bool):
            held_items = int(held)
        elif isinstance(held, (list, tuple)):
            held_items = len(held)
            held_units = sum((_dec(_field(x, _UNITS_KEYS)) or ZERO for x in held), ZERO)
            got = [_dec(_field(x, _BHD_KEYS)) for x in held]
            held_bhd = sum((x for x in got if x is not None), ZERO) if any(x is not None for x in got) else None
        elif held is not None:
            n = _field(held, ("items", "count", "n"))
            held_items = len(n) if isinstance(n, (list, tuple)) else (None if n is None else int(fmt.dec(n)))
            held_units, held_bhd = _dec(_field(held, _UNITS_KEYS)), _dec(_field(held, _BHD_KEYS))
        uncosted = _field(base, _UNCOSTED_KEYS)
        out[rules.canon(store)] = {"units": units, "bhd": None if bhd is None else _q(bhd),
                                   "uncosted": None if uncosted is None else fmt.dec(uncosted),
                                   "held_items": held_items, "held_units": held_units,
                                   "held_bhd": None if held_bhd is None else _q(held_bhd)}
    return out


def vans_block(results: dict, stores: list[str] | None, books: dict | None, books_note: str | None, rules, month: str,
               as_of: date) -> dict:
    """2. Each van and outlet: its signed count set beside its books, else its books only (see the module note).
    `stores` None: the stores on the books could not be worked out, so only the counted ones are listed and the
    block reads as not worked out (status None): a store nobody counted may be missing from it, so it is never
    PROVEN."""
    known = stores is not None
    stores = stores or []
    by_canon: dict[str, str] = {}
    for name in stores:
        by_canon.setdefault(rules.canon(name), " ".join(str(name).split()))
    counted: dict[str, list] = {}
    for r in results.values():
        if (r.get("kind") or rules.kind(r.get("store"))) in ("van", "outlet") and rules.kind(r.get("store")) != "main":
            c = rules.canon(r.get("store"))
            counted.setdefault(c, []).append(r)
            by_canon.setdefault(c, " ".join(str(r.get("store") or "").split()))
    rows = []
    for c, name in sorted(by_canon.items(), key=lambda kv: (rules.kind(kv[1]) != "van", _fold(kv[1]), kv[1])):
        res = counted.get(c)
        v = count_view(_newest(res)) if res else None
        row = {"store": name, "label": _store_label(name), "kind": rules.kind(name), "count": v,
               "books": None if books is None else books.get(c), "why": None}
        if v is not None and v["signed"] and not v["waits"] and v["counted"] + v["added"] > 0:
            row["status"] = PROVEN if count_agrees(v) else DIFFERS
        else:
            row["status"] = BOOKS_ONLY
            if v is None:
                row["why"] = "no signed count of the month"
            elif v["waits"]:
                row["why"] = (f"its count of {fmt.day(v['count_date'])} waits for a load with data to "
                              f"{fmt.day_long(v['book_day'])}: read the file again after it")
            elif not v["signed"]:
                row["why"] = f"its count of {fmt.day(v['count_date'])} is not signed (Checked by is empty): {SIGN_STEP}"
            else:
                row["why"] = f"its count of {fmt.day(v['count_date'])} has no line counted"
        rows.append(row)
    if not known:
        st = None
    elif any(r["status"] == DIFFERS for r in rows):
        st = DIFFERS
    elif not rows or any(r["status"] == BOOKS_ONLY for r in rows):
        st = BOOKS_ONLY
    else:
        st = PROVEN
    lines = _vans_lines(rows, books, books_note, month, as_of, known)
    totals = {k: sum((r["count"][k] or ZERO for r in rows if r["count"] and r["status"] != BOOKS_ONLY), ZERO)
              for k in ("short_units", "over_units", "short_bhd", "over_bhd")}
    totals["stores"] = len(rows)
    totals["signed"] = sum(1 for r in rows if r["status"] != BOOKS_ONLY)
    totals["books_only"] = len(rows) - totals["signed"]
    return {"status": st, "rows": rows, "books_note": books_note, "as_of": as_of, "totals": totals, "lines": lines}


def _vans_lines(rows: list[dict], books: dict | None, books_note: str | None, month: str, as_of: date,
                known: bool = True) -> list[str]:
    words = month_words(month)
    if not known and not rows:
        return [f"No van or outlet has a count of {words}, and the stores on the books could not be worked out."]
    if not rows:
        return [f"No van or outlet held an accessory at the end of {fmt.day_long(as_of)} or moved one in {words}: "
                "nothing to count, and nothing set beside a count."]
    lines: list[str] = []
    signed = [r for r in rows if r["status"] != BOOKS_ONLY]
    only = [r for r in rows if r["status"] == BOOKS_ONLY]
    if signed:
        lines.append("Signed counts (Counted by and Checked by filled in), each set beside the books at the end of the "
                     "day before it:")
        grid = []
        for r in signed:
            v = r["count"]
            grid.append([r["store"], shown_status(r["status"]), fmt.day(v["count_date"]), counted_cell(v),
                         fmt.units(v["short_units"] or 0), fmt.units(v["over_units"] or 0),
                         _bhd_or_dash(v["short_bhd"]) if v["costed"] else "-",
                         _bhd_or_dash(v["over_bhd"]) if v["costed"] else "-"])
        lines += ["    " + ln for ln in fmt.grid(["Store", "Status", "Counted on", "Lines counted", "Units short",
                                                   "Units over", "BHD short", "BHD over"], grid, "lllrrrrr").splitlines()]
    if only:
        lines.append(f"Books only ({_plural(len(only), 'store')} with no signed count of {words}): the stock on the books "
                     f"at the end of {fmt.day_long(as_of)}, at landed cost, and the items held there over {HELD_DAYS} days "
                     "unsold:")
        if books is None:
            lines.append(f"    {books_note or NOT_WORKED}.")
            lines.append("    Stores: " + _listed([r["store"] for r in only], 40) + ".")
        else:
            grid = []
            for r in only:
                b = r["books"]
                if b is None:
                    grid.append([r["store"], "-", "-", "-"])
                    continue
                held = "-" if b["held_items"] is None else _plural(b["held_items"], "item")
                if b["held_bhd"] is not None and b["held_items"]:
                    held += f", BHD {fmt.bhd(b['held_bhd'])}"
                grid.append([r["store"], _units_or_dash(b["units"]), _bhd_or_dash(b["bhd"]), held])
            lines += ["    " + ln for ln in fmt.grid(["Store", "Units", "BHD at cost",
                                                       f"Held over {HELD_DAYS} days unsold"], grid, "lrrl").splitlines()]
        for r in only:
            if r["why"] and r["count"] is not None:
                lines.append(f"  {r['store']}: {r['why']}.")
    return lines


# ── 3: sales and receivables ──────────────────────────────────────────────────

def _reaches(as_on: date | None, rows: list[dict], first: date, reach: date, first_sale: date | None) -> str | None:
    """None when a file reaches the whole window (its As-on date at `reach` or later, and its first day on the
    window's first day or before it, or on the first sale the portal holds); else why not, in words."""
    days = [r["day"] for r in rows if r["day"] is not None]
    if as_on is None:
        return "it has no As-on date in its title block"
    if not days:
        return "it holds no dated line"
    start = min(days)
    if as_on < reach:
        return f"it is as on {fmt.day_long(as_on)}"
    if start > first and (first_sale is None or start > first_sale):
        return f"it starts on {fmt.day_long(start)}"
    return None


def _by_invoice(rows: list[dict], first: date, last: date, with_lines: bool) -> dict:
    out: dict = {}
    for r in rows:
        if r["day"] is None or not (first <= r["day"] <= last) or not r["invoice"]:
            continue
        lines, gross = out.get(r["invoice"], (0, ZERO))
        out[r["invoice"]] = (lines + 1, gross + _q(r["gross"] or 0))
    return out if with_lines else {k: v[1] for k, v in out.items()}


def _side(invoices: dict, with_lines: bool) -> dict:
    if with_lines:
        return {"invoices": len(invoices), "lines": sum(v[0] for v in invoices.values()),
                "gross": sum((v[1] for v in invoices.values()), ZERO)}
    return {"invoices": len(invoices), "lines": None, "gross": sum(invoices.values(), ZERO)}


def invoicing_part(first: date, reach: date, folder: str | None, daybook, register, portal: dict,
                   first_sale: date | None, last_load: dict, files_note: str | None = None) -> dict:
    """The load control from `first` to `reach` (the month's last day, or the data date in a month not whole yet):
    the day book = the register = v_sales, invoice by invoice. `daybook` and `register` are (As-on date, rows) from
    read_sales_file, or None when the set has no such file; `portal` v_sales' invoices of the days. Compared only
    when both files reach the whole window; otherwise stated as the load's own verify."""
    portal_side = _side(portal, True)
    why = files_note
    if why is None:
        missing = [name for name, got in (("day book", daybook), ("register", register)) if got is None]
        if folder is None:
            why = "no loaded set of Focus files is on this PC"
        elif missing:
            why = f"the loaded set ({folder}) has no {' and no '.join(missing)}"
        else:
            reasons = [f"its {name} {r}" for name, r in (("day book", _reaches(*daybook, first, reach, first_sale)),
                                                         ("register", _reaches(*register, first, reach, first_sale))) if r]
            if reasons:
                why = f"the loaded set ({folder}) does not reach the whole month: " + "; ".join(reasons)
    if why is not None:
        run = last_load.get("run")
        verify = (f"the last load on record, {run} (finished {str(last_load.get('finished_at') or '?')[:16].replace('T', ' ')}"
                  f", {last_load.get('result') or 'no result on record'})" if run else "no load is on record here")
        return {"basis": "load_verify", "status": None, "folder": folder, "why": why, "portal": portal_side,
                "daybook": None, "register": None, "differences": [], "lines": [
                    f"Invoicing (the load control): not set beside the files this time: {why}. It stands on each load's "
                    f"own verify, which set the day book and the register beside the database before the set was "
                    f"published; {verify}. The portal holds {_plural(portal_side['invoices'], 'invoice')}, "
                    f"{_plural(portal_side['lines'], 'line')}, BHD {fmt.bhd(portal_side['gross'])} VAT included."]}
    book = _by_invoice(daybook[1], first, reach, True)
    reg = _by_invoice(register[1], first, reach, False)
    diffs: list[dict] = []
    for inv in sorted(set(book) | set(reg) | set(portal)):
        a, g, p = book.get(inv), reg.get(inv), portal.get(inv)
        said = []
        if a is None and p is not None:
            said.append("in the portal, not in the day book")
        elif p is None and a is not None:
            said.append("in the day book, not in the portal")
        elif a is not None and p is not None and (a[0] != p[0] or abs(a[1] - p[1]) > FILS):
            said.append(f"the day book has {_plural(a[0], 'line')} for BHD {fmt.bhd(a[1])}, the portal "
                        f"{_plural(p[0], 'line')} for BHD {fmt.bhd(p[1])}")
        if g is None:
            said.append("not in the register")
        elif a is None and p is None:
            said.append("in the register only")
        elif a is not None and abs(g - a[1]) > FILS:
            said.append(f"the register says BHD {fmt.bhd(g)}, its day-book lines BHD {fmt.bhd(a[1])}")
        if said:
            diffs.append({"invoice": inv, "said": "; ".join(said)})
    sides = {"daybook": _side(book, True), "register": _side(reg, False), "portal": portal_side}
    st = PROVEN if not diffs else DIFFERS
    if st == PROVEN:
        lines = [f"Invoicing (the load control): the day book = the register = v_sales, invoice by invoice: "
                 f"{_plural(portal_side['invoices'], 'invoice')}, {_plural(portal_side['lines'], 'line')}, BHD "
                 f"{fmt.bhd(portal_side['gross'])} VAT included (the files of {folder}, and the portal)."]
    else:
        lines = [f"Invoicing (the load control): {_plural(len(diffs), 'invoice')} to check: the day book "
                 f"{_plural(sides['daybook']['invoices'], 'invoice')} BHD {fmt.bhd(sides['daybook']['gross'])}, the "
                 f"register {_plural(sides['register']['invoices'], 'invoice')} BHD {fmt.bhd(sides['register']['gross'])}"
                 f", v_sales {_plural(portal_side['invoices'], 'invoice')} BHD {fmt.bhd(portal_side['gross'])} "
                 f"(the files of {folder})."]
        lines += [f"    {_safe(d['invoice'])}: {d['said']}." for d in diffs[:SHOWN]]
        if len(diffs) > SHOWN:
            lines.append(f"    ... and {len(diffs) - SHOWN} more (all in the file below).")
    return {"basis": "files", "status": st, "folder": folder, "why": None, "differences": diffs, "lines": lines, **sides}


def statement_part(rows: list[dict]) -> dict:
    """The kickback statement as build_rows drafts it: per rep its sales, tier and kickback."""
    reps = [{"rep": str(r.get("salesman") or ""), "sales": _q(r.get("sales_bhd")), "tier": int(r.get("tier_reached") or 0),
             "kickback": _q(r.get("kickback_bhd")), "target": r.get("target_snapshot"),
             "data_through": r.get("data_through")} for r in rows]
    total_sales = sum((r["sales"] for r in reps), ZERO)
    total_kick = sum((r["kickback"] for r in reps), ZERO)
    line = (f"Statement (app.statements.build_rows, as drafted): {_plural(len(reps), 'rep')} with a target, their sales "
            f"BHD {fmt.bhd(total_sales)} ex-VAT, kickback BHD {fmt.bhd(total_kick)} at the tiers reached; returns not "
            "deducted (the owner's rule while no Sales Return register is loaded).")
    return {"reps": reps, "count": len(reps), "sales": total_sales, "kickback": total_kick, "lines": [line]}


def _rep_of(salesman: str, reps: list[str]) -> str | None:
    """The rep (a statement's salesman) a return's salesman is: the same name, or one of the two followed by ' - '."""
    s = _fold(salesman)
    if not s:
        return None
    for rep in reps:
        r = _fold(rep)
        if r and (s == r or s.startswith(r + " - ") or r.startswith(s + " - ")):
            return rep
    return None


def returns_from_export(export, first: date, last: date, statement: dict | None, tier) -> dict:
    """The month's sales returns ex-VAT per salesman (the export's own SRV legs), each set against its rep's
    statement: the tier the rep's sales less them would reach (`tier`, app.shop's rule), listed when it changes."""
    by_salesman = ledger_export.returns_ex_vat_by_salesman(export, first, last)
    total = sum(by_salesman.values(), ZERO)
    reps = statement["reps"] if statement else []
    per_rep: dict[str, Decimal] = {}
    unmatched = ZERO
    for salesman, value in by_salesman.items():
        rep = _rep_of(salesman, [r["rep"] for r in reps]) if salesman != ledger_export.NO_SALESMAN else None
        if rep is None:
            unmatched += value
        else:
            per_rep[rep] = per_rep.get(rep, ZERO) + value
    changed = []
    for r in reps:
        back = per_rep.get(r["rep"], ZERO)
        if not back:
            continue
        got = tier(r["target"], r["sales"] - back, r["data_through"])
        if got is not None and got[0] != r["tier"]:
            changed.append({"rep": r["rep"], "returns": back, "net": r["sales"] - back, "tier": r["tier"],
                            "tier_net": got[0], "kickback": r["kickback"], "kickback_net": got[1]})
    lines = [f"Returns (the Ledger export's own Sales Return vouchers, ex-VAT, of every division): BHD {fmt.bhd(total)} "
             "in the month" + (f", BHD {fmt.bhd(unmatched)} of it naming no rep with a target" if unmatched else "")
             + "."]
    if statement is None:
        lines.append("The kickback tiers they would change: not worked out (the statement is not there).")
    elif changed:
        lines.append(f"They would change the kickback tier of {_plural(len(changed), 'rep')}, to check:")
        lines += [f"    {_safe(c['rep'])}: tier {c['tier']} -> {c['tier_net']} (returns BHD {fmt.bhd(c['returns'])}; "
                  f"kickback BHD {fmt.bhd(c['kickback'])} -> {fmt.bhd(c['kickback_net'])})." for c in changed]
    else:
        lines.append("They change no rep's kickback tier.")
    return {"basis": "export", "total": total, "unmatched": unmatched, "by_rep": per_rep, "changed": changed,
            "lines": lines}


def returns_estimate(rows, accessory: set | None, first: date, last: date, prices: dict | None) -> dict:
    """Without the export: the stock ledger's Sales Return units of the month (accessories), valued at each item's
    average ex-VAT selling price of the month (`prices`, day-book names: the bridge places a ledger name). No tier
    is judged on it."""
    units: dict[str, Decimal] = {}
    vouchers: set[str] = set()
    for r in rows:
        if r["vtype"] == ledger.SALES_RETURN and first <= r["date"] <= last and r["qty_in"] > 0 and (
                accessory is None or r["item"] in accessory):
            units[r["item"]] = units.get(r["item"], ZERO) + r["qty_in"]
            vouchers.add(r["voucher"])
    total_units = sum(units.values(), ZERO)
    value = None
    unpriced = ZERO
    if prices is not None:
        bridge = items.Bridge(list(prices))            # config 'items.bridge_exceptions' wins over the rule
        value = ZERO
        for item, n in units.items():
            known = bridge.get(item)
            qty, net = prices.get(known, (ZERO, ZERO)) if known is not None else (ZERO, ZERO)
            if qty and qty > 0:
                value += _q(n * net / qty)
            else:
                unpriced += n
    said = (f"Returns: net of returns is an estimate from returned units: {fmt.units(total_units)} units came back on "
            f"{_plural(len(vouchers), 'Sales Return voucher')} in the month (the stock ledger)")
    if value is not None and total_units:
        said += f", about BHD {fmt.bhd(value)} ex-VAT at the month's average selling price of each item"
        if unpriced:
            said += f" ({fmt.units(unpriced)} units of items not sold in the month not priced)"
    elif prices is None and total_units:
        said += f" (their price: {NOT_WORKED})"
    said += ". The Ledger export holds their values; no kickback tier is judged on an estimate."
    return {"basis": "estimate", "units": total_units, "vouchers": len(vouchers), "value": value, "unpriced": unpriced,
            "lines": [said]}


def _customers(export, ar_names: set, ar_now: dict, first: date, last: date) -> tuple[list[str], int]:
    """(the export's accounts that a receivables ageing lists, as the export spells them; how many accounts are
    debited by a sales invoice of the month and are in no ageing)."""
    listed = ar_names | {_fold(a) for a in ar_now}
    out, outside = [], set()
    for account in export.accounts:
        if _fold(account) in listed:
            out.append(account)
    for r in export.rows:
        if (r["kind"] == "sale" and r["debit"] > 0 and first <= r["date"] <= last
                and _fold(r["account"]) not in listed):
            outside.add(_fold(r["account"]))
    return out, len(outside)


def collection_part(export, why_file: str, first: date, last: date, ar_dates: list[date], ar_rows_of,
                    ar_names: set) -> dict:
    """Per customer account: opening + SI - RCTV - SRV - CN +/- other = closing (the export), and the closing set
    beside the receivables ageing (see the module note). `ar_rows_of(day)` reads the ageing of one day."""
    eve = first - timedelta(days=1)
    ar_day = next((d for d in sorted(ar_dates) if last <= d <= last + timedelta(days=AR_DAYS)), None)
    ar_now = ar_rows_of(ar_day) if ar_day is not None else {}
    accounts, outside = _customers(export, ar_names, ar_now, first, last)
    moves = ledger_export.movements(export, first, last)
    at = None if ar_day is None else min(ar_day, export.as_on)
    rows: list[dict] = []
    totals = dict.fromkeys(("opening", "si", "rctv", "srv", "cn", "other", "closing"), ZERO)
    breaks = []
    for account in accounts:
        kinds = moves.get(account) or {}

        def net(kind, sign=1):
            m = kinds.get(kind)
            return ZERO if m is None else sign * m.net

        si, rctv, srv, cn = net("sale"), net("receipt", -1), net("return", -1), net("credit_note", -1)
        other = sum((m.net for k, m in kinds.items() if k not in ("sale", "receipt", "return", "credit_note")), ZERO)
        opening, closing = ledger_export.balance(export, account, eve), ledger_export.balance(export, account, last)
        rolled = None if opening is None or closing is None else opening + si - rctv - srv - cn + other - closing
        if rolled is not None and abs(rolled) > FILS:
            breaks.append(account)
        if opening is not None and closing is not None:
            for k, v in (("opening", opening), ("si", si), ("rctv", rctv), ("srv", srv), ("cn", cn), ("other", other),
                         ("closing", closing)):
                totals[k] += v
        ledger_at = None if at is None else (closing if at == last else ledger_export.balance(export, account, at))
        rows.append({"account": account, "opening": opening, "si": si, "rctv": rctv, "srv": srv, "cn": cn,
                     "other": other, "closing": closing, "rolled": rolled, "ledger_at": ledger_at})
    out = {"file": export.source, "as_on": export.as_on, "why_file": why_file, "accounts": len(rows), "rows": rows,
           "totals": totals, "breaks": breaks, "outside": outside, "ar_day": ar_day, "compared_at": at,
           "agree": 0, "to_check": [], "credits": 0}
    roll = (f"Collection (the Ledger export {export.source}, as on {fmt.day_long(export.as_on)}): "
            f"{_plural(len(rows), 'customer account')}: opening BHD {fmt.bhd(totals['opening'])} + sales invoices BHD "
            f"{fmt.bhd(totals['si'])} - receipts BHD {fmt.bhd(totals['rctv'])} - returns BHD {fmt.bhd(totals['srv'])} - "
            f"credit notes BHD {fmt.bhd(totals['cn'])} {'+' if totals['other'] >= 0 else '-'} other BHD "
            f"{fmt.bhd(abs(totals['other']))} = closing BHD {fmt.bhd(totals['closing'])}.")
    lines = [roll]
    if breaks:
        lines.append(f"The export's running balance does not follow its own rows for {_plural(len(breaks), 'account')}, "
                     f"to check: {_listed([_safe(a) for a in breaks])}.")
    if outside:
        lines.append(f"{_plural(outside, 'account')} carrying a sales invoice {'is' if outside == 1 else 'are'} in no "
                     "receivables ageing: left out.")

    def finish(**kw) -> dict:
        """The part as it is kept: every account name masked the way it is printed."""
        out.update(kw, lines=lines)
        for r in out["rows"] + out["to_check"]:
            r["account"] = _safe(r["account"])
        out["breaks"] = [_safe(a) for a in out["breaks"]]
        return out

    if ar_day is None:
        why = f"no receivables ageing as on {fmt.day_long(last)}, nor in the {AR_DAYS} days after it, is loaded"
        lines.append(f"Not set beside an ageing: {why}.")
        return finish(status=COLLECTION_NOT_PROVEN, why=why)
    in_export = {_fold(x) for x in export.accounts}
    owing = [a for a, v in ar_now.items() if v]
    found = sum(1 for a in owing if _fold(a) in in_export)
    if owing and found * 2 < len(owing):
        why = (f"the account names of the ageing as on {fmt.day_long(ar_day)} and of the Ledger export do not meet "
               f"({found} of the {len(owing)} accounts with a balance found)")
        lines.append(f"Not set beside the ageing: {why}.")
        return finish(status=COLLECTION_NOT_PROVEN, why=why)
    ar = {_fold(a): v for a, v in ar_now.items()}
    seen = set()
    to_check = []
    credits = agree = 0
    for r in rows:
        k = _fold(r["account"])
        seen.add(k)
        mine, theirs = r["ledger_at"], ar.get(k)
        credit = False
        if mine is None:
            said = "the export states no balance"
        elif theirs is None:
            said = None if abs(mine) <= FILS else "not in the ageing"
        elif abs(mine - theirs) <= FILS:
            said = None
        elif mine < 0 and abs(-mine - theirs) <= FILS:
            said, credit = None, True
        else:
            said = "differs"
        # the identity has no "other" term: an account a journal, a payment or any other entry moved is to check,
        # whatever the ageing says (a write-off reads as agreeing once it is posted on both sides)
        if abs(r["other"]) > FILS:
            moved = (f"BHD {fmt.bhd(abs(r['other']))} moved {'into' if r['other'] > 0 else 'out of'} it by other "
                     "entries (no sales invoice, receipt, return or credit note)")
            said = f"{said}; {moved}" if said else f"agrees with the ageing, but {moved}"
        if said is None:
            agree += 1
            credits += credit
            continue
        to_check.append({"account": r["account"], "ledger": mine, "ageing": theirs, "said": said})
    for a, v in ar_now.items():
        if _fold(a) not in seen and abs(v) > FILS:
            to_check.append({"account": a, "ledger": None, "ageing": v, "said": "not in the Ledger export"})
    when = (f"as on {fmt.day_long(ar_day)}" if ar_day == last else
            f"as on {fmt.day_long(ar_day)} (none is as on {fmt.day_long(last)})")
    if at != ar_day:
        read_to = f"; the Ledger stops at {fmt.day_long(at)}, so what was entered after it shows as a difference"
    else:
        read_to = "" if ar_day == last else f"; the Ledger is read to {fmt.day_long(at)}"
    compared = agree + len(to_check)
    head = (f"The closing set beside the receivables ageing {when}{read_to}: {fmt.units(agree)} of the "
            f"{_plural(compared, 'account')} {'agrees' if agree == 1 else 'agree'}")
    head += (f" ({credits} of them a credit balance, which the ageing prints without its sign)" if credits else "")
    head += f"; {fmt.units(len(to_check))} to check:" if to_check else "."
    lines.append(head)
    for x in to_check[:SHOWN]:
        lines.append(f"    {_safe(x['account'])}: the Ledger {_bhd_or_dash(x['ledger'])}, the ageing "
                     f"{_bhd_or_dash(x['ageing'])} ({x['said']}).")
    if len(to_check) > SHOWN:
        lines.append(f"    ... and {len(to_check) - SHOWN} more (all in the file below).")
    return finish(status=DIFFERS if to_check or breaks else PROVEN, agree=agree, to_check=to_check, credits=credits)


def sales_block(invoicing: dict | None, invoicing_note: str | None, statement: dict | None, statement_note: str | None,
                collection: dict | None, collection_note: str | None, returns: dict | None,
                returns_note: str | None) -> dict:
    """3. The four parts put together, and the block's status (see the module note)."""
    lines: list[str] = []
    lines += invoicing["lines"] if invoicing else [f"Invoicing (the load control): {invoicing_note}."]
    lines += statement["lines"] if statement else [f"Statement: {statement_note}."]
    lines += collection["lines"] if collection else [f"Collection: {collection_note}."]
    lines += returns["lines"] if returns else [f"Returns: {returns_note}."]
    differs = ((invoicing or {}).get("status") == DIFFERS or (collection or {}).get("status") == DIFFERS
               or bool((returns or {}).get("changed")))
    if differs:
        st = DIFFERS
    elif collection is None or collection.get("status") != PROVEN:
        st = COLLECTION_NOT_PROVEN
    elif invoicing is None or invoicing.get("status") is None or statement is None or returns is None:
        # the collection is proven, and a part beside it was not worked out, or the invoicing was not set beside
        # the files here (it stands on the load's own verify): never PROVEN without its second side
        st = None
    else:
        st = PROVEN
    return {"status": st, "invoicing": invoicing, "statement": statement, "collection": collection, "returns": returns,
            "notes": {"invoicing": invoicing_note, "statement": statement_note, "collection": collection_note,
                      "returns": returns_note}, "lines": lines}


def no_export_part(month: str, why: str, named: bool = False) -> dict:
    """The collection without an export: why there is none, and what to do (`named`: a file was named and refused)."""
    last = mdl.month_bounds(month)[1]
    todo = (f"Name Focus's Ledger report (all accounts) as on {fmt.day_long(last)} or later" if named else
            f"Put Focus's Ledger report (all accounts, as on {fmt.day_long(last)}) in the next drop folder, or name the "
            "file with --ledger-file")
    return {"status": COLLECTION_NOT_PROVEN, "file": None, "why": why, "named": named, "lines": [
        f"Collection: COLLECTION NOT PROVEN: {why}. {todo}."]}


# ── 4: purchases ──────────────────────────────────────────────────────────────

def receipt_number(voucher: str) -> str:
    """'MRN:AB-26-01-7' -> 'AB-26-01-7' (the number a purchase order and the vault are filed under)."""
    text = str(voucher or "")
    return " ".join(text.split(":", 1)[1].split()) if ":" in text else " ".join(text.split())


def _key(text) -> str:
    return " ".join(str(text or "").split()).upper()


def _lead(name: str, code: str | None) -> str:
    """The model code an order is written by: the first word of the item code, else of the ledger name."""
    return (str(code).split()[0].upper() if code and str(code).split() else items.leading_code(name))


def match_po(number: str, day: date, leads: set, pos: dict) -> tuple[str | None, str | None, Fraction | None]:
    """(the order, how it was matched: 'number' or 'codes', the overlap) for a receipt: the order filed under the
    receipt's own number, else the order dated at most PO_DAYS days before it whose model codes overlap the
    receipt's by at least PO_OVERLAP (Jaccard), the best overlap first, then the latest, then its number."""
    if _key(number) in pos:
        return _key(number), "number", None
    best = None
    for po, entry in pos.items():
        when = entry["date"]
        if when is None or when > day or (day - when).days > PO_DAYS or not leads or not entry["codes"]:
            continue
        overlap = Fraction(len(leads & entry["codes"]), len(leads | entry["codes"]))
        if overlap >= PO_OVERLAP:
            rank = (overlap, when, po)
            if best is None or rank > best[0]:
                best = (rank, po, overlap)
    return (best[1], "codes", best[2]) if best else (None, None, None)


def _po_table(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows or []:
        po = _key(r.get("po_no"))
        if not po:
            continue
        e = out.setdefault(po, {"date": fmt.as_date(r.get("po_date")), "codes": set()})
        code = _key(r.get("code"))
        if code:
            e["codes"].add(code)
        when = fmt.as_date(r.get("po_date"))
        if when and (e["date"] is None or when > e["date"]):
            e["date"] = when
    return out


def detail_flags(detail: dict | None) -> dict:
    """{flag: [codes]} of app.orders.detail's lines (the flags it puts on each code)."""
    out: dict = {}
    for line in (detail or {}).get("lines") or []:
        for f in line.get("flags") or []:
            out.setdefault(str(f), []).append(str(line.get("code") or ""))
    return {f: sorted(set(codes)) for f, codes in out.items()}


def receipt_row(voucher: str, v: dict, codes: dict | None, pos: dict, files: dict, mrn: dict, matched_invoices: list,
                detail_of) -> dict:
    """One supplier receipt into the main store, its papers, cost lines and flags, and its status."""
    number = receipt_number(voucher)
    item_codes = {name: (codes or {}).get(name) for name in v["lines"]}
    leads: dict[str, set] = {}
    for name, code in item_codes.items():
        leads.setdefault(_lead(name, code), set()).add(name)
    po, basis, overlap = match_po(number, v["date"], set(leads), pos)
    keys = {_key(number)} | ({po} if po else set())
    kinds = set().union(*(files.get(k, set()) for k in keys))
    loaded = set().union(*(mrn.get(k, set()) for k in keys))
    no_code = sorted(n for n, c in item_codes.items() if not c) if codes is not None else []
    # the cost lines are counted only when the item codes could be read (None: not counted this time)
    costed = None if codes is None else sum(1 for _n, c in item_codes.items() if c and _key(c) in loaded)
    invoice_vault = "invoice" in kinds
    invoices = sorted({m["invoice"] for m in matched_invoices})
    row = {"receipt": voucher, "number": number, "date": v["date"], "lines": len(v["lines"]),
           "units": sum(v["lines"].values(), ZERO), "value": v["value"], "po": po, "po_basis": basis,
           "po_date": pos[po]["date"] if po else None, "overlap": None if overlap is None else str(overlap),
           "po_file_only": po is None and "po" in kinds, "xml": "mrn" in kinds, "cost_lines": costed,
           "no_code": no_code, "invoice": invoice_vault or bool(invoices),
           "invoice_basis": "vault" if invoice_vault else ("matched" if invoices else None), "invoices": invoices,
           "variants": sorted(lead for lead, names in leads.items() if len(names) > 1 and lead),
           "flags": {}, "detail": None, "actions": [], "notes": []}
    if po:
        detail = detail_of(po)
        row["detail"] = {"found": bool(detail.get("found")), "status": detail.get("status"),
                         "summary": (detail.get("reconciliation") or {}).get("summary")}
        row["flags"] = detail_flags(detail) if detail.get("found") else {}
    missing = [k for k, ok in (("po", po is not None), ("xml", row["xml"]), ("invoice", row["invoice"])) if not ok]
    row["actions"] = [PAPER_ACTIONS[k] for k in missing]
    if row["po_file_only"]:
        row["actions"][0] = ("its purchase order is in the vault but its lines are not loaded: upload it again on the "
                             "Orders page")
    if po and row["xml"] and row["detail"] and row["detail"]["found"] and row["detail"]["status"] == "pending":
        # the XML is on file under the receipt's own number, not under its order's: the order cannot set what was
        # received beside what was ordered, so nothing there can be told
        row["actions"].append(f"its order {po} shows nothing received on the Orders page: attach the MRN XML to that "
                              "order")
    if costed is None:
        pass
    elif row["xml"] and costed < row["lines"]:
        row["actions"].append(f"landed costs are loaded for {costed} of its {row['lines']} lines: load the rest from "
                              "its MRN XML")
    elif not row["xml"] and costed:
        row["notes"].append("its landed costs are loaded without the XML on file (from a workbook)" if costed == row["lines"]
                            else f"landed costs are loaded for {costed} of its {row['lines']} lines, without the XML "
                                 "on file")
    if no_code:
        row["notes"].append(f"{_plural(len(no_code), 'item')} with no item code to match a cost line")
    if row["variants"]:
        row["notes"].append(f"{_plural(len(row['variants']), 'model code')} with several variants "
                            f"({_listed(row['variants'])}): variant not provable from the PO")
    for f in NOTE_FLAGS:
        if row["flags"].get(f):
            row["notes"].append(f"{FLAG_WORDS[f]}: {_listed(row['flags'][f])}")
    if basis == "codes":
        shared, both = len(set(leads) & pos[po]["codes"]), len(set(leads) | pos[po]["codes"])
        row["notes"].append(f"its order {po} is matched by its model codes, not by its number: the two share {shared} of "
                            f"their {both} codes")
    # the order's flags are a difference only once every paper is there: while one is missing (a cost line not
    # loaded reads as "short" on the order, for one) they are a note, and the paper is the thing to do
    row["flagged"] = any(row["flags"].get(f) for f in DIFF_FLAGS)
    if row["actions"]:
        row["status"] = PAPERS_MISSING
        if row["flagged"]:
            said = [f"{FLAG_WORDS[f]}: {_listed(row['flags'][f])}" for f in DIFF_FLAGS if row["flags"].get(f)]
            row["notes"].append(f"the Orders page reads {'; '.join(said)}: not judged while a paper is missing")
    elif row["flagged"]:
        row["status"] = DIFFERS
    else:
        row["status"] = PROVEN
    row["differs"] = row["status"] == DIFFERS
    return row


def purchases_rows(rows, codes: dict | None, first: date, last: date, po_rows, file_rows, mrn_rows, invoice_rows,
                   detail_of) -> tuple[list[dict], int]:
    """(one row per supplier receipt into the main store dated first to last, oldest first; how many supplier
    receipts went into another store in the days)."""
    from scripts.ops import stock
    pos = _po_table(po_rows)
    files: dict = {}
    for r in file_rows or []:
        files.setdefault(_key(r.get("po_no")), set()).add(str(r.get("kind") or "").lower())
    mrn: dict = {}
    for r in mrn_rows or []:
        mrn.setdefault(_key(r.get("doc_no")), set()).add(_key(r.get("code")))
    receipts = ledger.vouchers(rows, ledger.SUPPLIER_RECEIPT)
    main = {n: v for n, v in receipts.items() if ledger.is_main(v["wh"])}
    elsewhere = sum(1 for n, v in receipts.items() if not ledger.is_main(v["wh"]) and first <= v["date"] <= last)
    matched = stock.measure_lead_times(invoice_rows, stock.receipt_codes(rows)) if invoice_rows is not None else []
    out = []
    for voucher, v in main.items():
        if first <= v["date"] <= last:
            mine = [m for m in matched if m["receipt"] == voucher]
            out.append(receipt_row(voucher, v, codes, pos, files, mrn, mine, detail_of))
    return out, elsewhere


def purchases_block(rows: list[dict], elsewhere: int, first: date, last: date, notes: list[str] | None = None) -> dict:
    """4. The receipts put together (see the module note): DIFFERS only from a row that reads DIFFERS (every paper
    there, a flag up); a row missing a paper is PAPERS MISSING whatever its order reads."""
    if any(r["status"] == DIFFERS for r in rows):
        st = DIFFERS
    elif any(r["status"] == PAPERS_MISSING for r in rows):
        st = PAPERS_MISSING
    else:
        st = PROVEN
    known = all(r["cost_lines"] is not None for r in rows)
    totals = {"receipts": len(rows), "po": sum(1 for r in rows if r["po"]), "xml": sum(1 for r in rows if r["xml"]),
              "invoice": sum(1 for r in rows if r["invoice"]),
              "cost_lines": sum(r["cost_lines"] for r in rows) if known else None,
              "lines": sum(r["lines"] for r in rows), "papers_missing": sum(1 for r in rows if r["status"] == PAPERS_MISSING),
              "differs": sum(1 for r in rows if r["status"] == DIFFERS), "elsewhere": elsewhere}
    span = f"{fmt.day_long(first)} to {fmt.day_long(last)}"
    lines: list[str] = []
    if not rows:
        lines.append(f"No supplier receipt into the main store from {span}: no paper is due.")
    else:
        lines.append(f"Supplier receipts into the main store, {span}: one row each.")
        grid = [[r["receipt"], fmt.day(r["date"]),
                 (r["po"] if r["po_basis"] == "number" else f"{r['po']} (by its codes)") if r["po"] else "no",
                 "yes" if r["xml"] else "no",
                 ("yes" if r["invoice_basis"] == "vault" else f"yes ({_listed(r['invoices'], 2)})") if r["invoice"] else "no",
                 "-" if r["cost_lines"] is None else f"{r['cost_lines']} of {r['lines']}", shown_status(r["status"])]
                for r in rows]
        lines += ["    " + ln for ln in fmt.grid(["Receipt", "Date", "PO on file", "MRN XML", "Supplier invoice",
                                                   "Cost lines", "Status"], grid, "lllllrl").splitlines()]
        for r in rows:
            if r["actions"]:
                lines.append(f"  To do, {r['receipt']}: " + "; ".join(r["actions"]) + ".")
            if r["status"] == DIFFERS:
                said = [f"{FLAG_WORDS[f]}: {_listed(r['flags'][f])}" for f in DIFF_FLAGS if r["flags"].get(f)]
                lines.append(f"  To check, {r['receipt']} against its order {r['po']}: " + "; ".join(said) + ".")
            for n in r["notes"]:
                lines.append(f"  {r['receipt']}: {n}.")
    if elsewhere:
        lines.append(f"{_plural(elsewhere, 'supplier receipt')} went into another store from {span}: not reconciled "
                     "here.")
    lines += [f"Note: {n}." for n in notes or []]
    return {"status": st, "rows": rows, "totals": totals, "first": first, "last": last, "lines": lines}


def purchases(first, last, source: Source | None = None) -> dict:
    """The purchases reconciliation of any window: {status, rows (one per supplier receipt into the main store, each
    with its status), totals, lines}. The weekly workbook's Actions sheet reads its rows' statuses."""
    first, last = fmt.as_date(first), fmt.as_date(last)
    if first is None or last is None or last < first:
        raise ReconInput("the purchases reconciliation takes two days, the first not after the last")
    source = source or Source()
    led, _info = source.stock_ledger()
    warnings: list[str] = []
    return _purchases_from(source, led.rows, first, last, warnings)


def _purchases_from(source, rows, first: date, last: date, warnings: list) -> dict:
    notes = []
    table, _why = _attempt(lambda: source.accessories(rows), "the item codes", warnings)
    codes = None if table is None else table[0]
    if codes is None:
        notes.append(f"the item codes are {NOT_WORKED}: the cost lines are not counted")
    invoices, _why = _attempt(source.supplier_invoices, "the supplier invoices", warnings)
    if invoices is None:
        notes.append(f"the supplier invoices are {NOT_WORKED}: only the vault says whether one is on file")
    po_rows = source.po_lines()
    file_rows = source.order_files()
    mrn_rows = source.mrn_lines()

    def detail_of(po: str) -> dict:
        got, why_ = _attempt(lambda: source.order_detail(po), f"the order {po}", warnings)
        if got is None:
            notes.append(f"the order {po}'s flags are {why_}")
            return {"found": False}
        return got

    found, elsewhere = purchases_rows(rows, codes, first, last, po_rows, file_rows, mrn_rows, invoices, detail_of)
    return purchases_block(found, elsewhere, first, last, notes)


# ── the month ─────────────────────────────────────────────────────────────────

def _default_month(data_to: date) -> str:
    first = data_to.replace(day=1)
    prev = first - timedelta(days=1)
    return f"{prev:%Y-%m}"


def month_blocks(month: str, source: Source | None = None) -> dict:
    """The four reconciliations of a month as data: {month, first, last, data_date, books_as_of, whole, ledger_note,
    blocks {main, vans, sales, purchases}: each {status, lines, ...}, statuses, warnings}. Reads through `source`
    (production when None), writes nothing. ReconInput (one sentence) for a month the data has not reached."""
    if not counts.MONTH.fullmatch(str(month or "")):
        raise ReconInput(f"A month is written YYYY-MM (for example 2026-09), not {month!r}.")
    source = source or Source()
    first, last = mdl.month_bounds(month)
    words = month_words(month)
    warnings: list[str] = []
    first_sale, last_sale = source.sales_span()
    led, info = source.stock_ledger()
    if led.cache_note:
        warnings.append(led.cache_note)
    rows = led.rows
    data_date = max((d for d in (last_sale, led.last_date) if d is not None), default=None)
    if data_date is None or first > data_date:
        raise ReconInput(f"No data reaches {words} yet: the data runs to {fmt.day_long(data_date)}.")
    if led.first_date is not None and last < led.first_date:
        raise ReconInput(f"The stock ledger starts on {fmt.day_long(led.first_date)}: it cannot speak of {words}.")
    as_of = min(last, led.last_date) if led.last_date else last
    reach = min(last, last_sale) if last_sale else last
    rules = source.store_rules()
    table, _why = _attempt(lambda: source.accessories(rows), "the accessories", warnings)
    accessory = None if table is None else set(table[0])
    if table is not None and table[1]:
        warnings += [f"{u} could not be read" for u in table[1]]
    blocks: dict = {}

    # 1 and 2: the counts
    results, why = _attempt(lambda: source.count_results(month), "the count results", warnings)
    if results is None:
        blocks["main"] = {"status": None, "lines": [f"The counts: {why}."], "count": None}
        blocks["vans"] = {"status": None, "lines": [f"The counts: {why}."], "rows": [], "totals": {}}
    else:
        blocks["main"] = main_block(results, rules, month)
        stores, why_s = _attempt(lambda: stores_on_books(rows, rules, accessory, first, as_of), "the stores", warnings)
        books_answer, books_note = _attempt(lambda: source.books_only(as_of), "the books-only figures", warnings)
        books = None
        if books_answer is not None:
            books, view_why = _attempt(lambda: books_view(books_answer, rules), "the books-only figures", warnings)
            books_note = books_note if books is not None else view_why
        blocks["vans"] = vans_block(results, stores, books, books_note, rules, month, as_of)
        if stores is None:                 # the block reads not worked out: a store nobody counted may be missing
            blocks["vans"]["lines"].insert(0, f"The stores on the books: {why_s}; only the counted ones are listed, so "
                                              "the block is not worked out.")

    # 3: sales and receivables
    blocks["sales"] = _sales(source, month, first, last, reach, rows, accessory, first_sale, warnings)

    # 4: purchases
    got, why = _attempt(lambda: _purchases_from(source, rows, first, as_of, warnings), "the purchases", warnings)
    blocks["purchases"] = got if got is not None else {"status": None, "rows": [], "totals": {},
                                                       "lines": [f"The purchases: {why}."]}
    whole = led.last_date is not None and led.last_date >= last and (last_sale is not None and last_sale >= last)
    return {"month": month, "words": words, "first": first, "last": last, "data_date": data_date,
            "books_as_of": as_of, "sales_to": reach, "whole": whole, "ledger_note": info.get("note") or "",
            "blocks": blocks, "statuses": {k: blocks[k]["status"] for k, _t in BLOCKS}, "warnings": warnings}


def _sales(source, month: str, first: date, last: date, reach: date, rows, accessory, first_sale, warnings) -> dict:
    portal, portal_why = _attempt(lambda: source.portal_invoices(first, reach), "v_sales", warnings)
    invoicing, invoicing_note = None, portal_why
    if portal is not None:
        def files():
            folder, book_file, register_file = source.sales_files()
            daybook = register = note = None
            try:
                daybook = source.read_sales_file(book_file, "order_lines") if book_file is not None else None
                register = source.read_sales_file(register_file, "orders") if register_file is not None else None
            except Exception as exc:  # noqa: BLE001  (a file that cannot be read: the load's own verify stands)
                note = f"a file of the loaded set ({folder}) could not be read ({type(exc).__name__})"
            return invoicing_part(first, reach, folder, daybook, register, portal, first_sale, source.last_load(),
                                  files_note=note)
        invoicing, invoicing_note = _attempt(files, "the invoicing control", warnings)
    statement_rows, statement_note = _attempt(lambda: source.statement_rows(month), "the kickback statement", warnings)
    statement = None if statement_rows is None else statement_part(statement_rows)
    collection = returns = None
    collection_note = returns_note = None
    try:
        export, why_file = source.ledger_export(month)
    except ValueError as exc:       # LedgerExportError and the parser's HeaderMismatch: one sentence of their own
        export, why_file = None, f"the Ledger export could not be read: {exc}"
    except Exception as exc:  # noqa: BLE001  (a file there that cannot be read: its kind, never its text)
        export, why_file = None, f"the Ledger export could not be read ({type(exc).__name__})"
    if export is None:
        collection = no_export_part(month, why_file, named=bool(getattr(source, "ledger_file", None)))

        def estimate():
            prices, _why = _attempt(lambda: source.month_prices(first, reach), "the month's prices", warnings)
            return returns_estimate(rows, accessory, first, reach, prices)
        returns, returns_note = _attempt(estimate, "the returns", warnings)
    else:
        def collect():
            names = source.ar_names()
            dates = source.ar_dates(last, last + timedelta(days=AR_DAYS))
            return collection_part(export, why_file, first, last, dates, source.ar_rows, names)
        collection, collection_note = _attempt(collect, "the collection", warnings)
        if collection is None:
            collection = {"status": COLLECTION_NOT_PROVEN, "why": collection_note,
                          "lines": [f"Collection: COLLECTION NOT PROVEN: {collection_note}."]}
        returns, returns_note = _attempt(lambda: returns_from_export(export, first, last, statement, source.tier),
                                         "the returns", warnings)
    return sales_block(invoicing, invoicing_note, statement, statement_note, collection, collection_note, returns,
                       returns_note)


# ── the screen ────────────────────────────────────────────────────────────────

def screen_lines(m: dict) -> list[str]:
    words = m["words"]
    head = f"RECONCILIATIONS  {words}"
    if m["whole"]:
        head += f" (data to {fmt.day(m['data_date'])}; the books as at the end of {fmt.day_long(m['books_as_of'])})"
    else:
        head += (f": not whole yet, the data runs to {fmt.day_long(m['data_date'])}; worked out to "
                 f"{fmt.day_long(m['books_as_of'])}")
    lines = [head]
    width = max(len(t) for _k, t in BLOCKS)
    for k, title in BLOCKS:
        lines.append(f"  {title.upper().ljust(width + 2)}{shown_status(m['statuses'][k])}")
    for n, (k, title) in enumerate(BLOCKS, start=1):
        b = m["blocks"][k]
        lines.append("")
        lines.append(f"{n}. {title.upper()}: {shown_status(b['status'])}")
        lines += ["  " + ln if not ln.startswith("    ") else ln for ln in b["lines"]]
    lines.append("")
    lines.append(f"{m['ledger_note']}")
    return lines


def facts_of(m: dict) -> dict:
    """What the json file keeps: the blocks as worked out (dates and Decimals as text by save_screen)."""
    return {k: v for k, v in m.items() if k != "blocks"} | {"blocks": m["blocks"]}


def run(args, month: str | None, source: Source | None = None) -> dict:
    """recon [YYYY-MM]: the four blocks on the screen, a copy in exports/ops/<data date>/recon-<YYYY-MM>.txt/.json.
    Exit 0; 2 when a block could not be worked out; 1 (one sentence) for a month that cannot be answered."""
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {"month": month}, "files": [],
           "warnings": []}
    # --ledger chooses where the STOCK ledger is read; --ledger-file names the month-end Ledger export (accounts)
    source = source or Source(getattr(args, "ledger", "auto") or "auto", getattr(args, "ledger_file", None))
    try:
        if month is None:
            _first_sale, last_sale = source.sales_span()
            if last_sale is None:
                raise ReconInput("No sales are loaded yet: there is no month to reconcile.")
            month = _default_month(last_sale)
        m = month_blocks(month, source)
    except ReconInput as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    text = "\n".join(_guarded(ln).rstrip() for ln in screen_lines(m))
    files = sales.save_screen(m["data_date"], f"recon-{month}", text, facts_of(m))
    unworked = [k for k, _t in BLOCKS if m["statuses"][k] is None]
    flags = {"month": month, "statuses": m["statuses"], "whole": m["whole"]}
    return dict(out, ok=not unworked, data_date=m["data_date"].isoformat(), text=text,
                files=[str(f) for f in files], warnings=m["warnings"], flags=flags, exit=2 if unworked else 0)
