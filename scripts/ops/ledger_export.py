"""The Focus month-end Ledger export (all accounts), read for the month-end reconciliations. READ-ONLY.

THE FILE. Focus's Ledger report for every account, put in the dated drop folder on the first morning of the
new month (docs/FOCUS_EXPORT.md). It names one date, "[As on date dd/mm/yyyy]", and no from/to range, and it is
CUMULATIVE: it holds entries from months back up to the as-on date. So a month is never "the file as it
stands": every question here names its days, and the rows are filtered by date. The report has one section per
account, closed by a "Sub Total" row, and one "Grand Total". It is double entry: a voucher is written in two or
more account sections, once for each side of it, so a total across accounts counts a voucher twice; pick the
accounts first (customer_legs). The load never loads this file (scripts.ingest.classify calls it
ledger_entries; the load check only lists it as NOT LOADED).

READING. scripts.ingest does the work: read_grid, then parse_ledger. The parser checks the column layout first
and stops on a changed one (ingest.HeaderMismatch, which read() lets through). Nothing is loaded, nothing is
written and there is no cache: one read of a big export takes as long as the workbook takes to open, so the
caller reads it once and passes the Export on.

PRIVACY. The Narration column can hold customers' personal ID numbers, and parse_ledger does not mask it.
read() pops it from every row the moment the parser returns, before anything else looks at the rows, so it is
in no row of an Export, in no sentence a function here returns and in no file.

A ROW is one leg of one voucher, a small dict:
    account       the section the row stands in (the account whose ledger it is), as the header spells it
    date          datetime.date
    voucher       as Focus writes it, e.g. 'SI : 1234'
    prefix        'SI' (upper case); '' when the voucher does not begin 'PREFIX :'
    kind          kind_of(voucher): sale, receipt, return, credit_note, payment, journal or other
    counter       the report's own "Account" column: the other side of the voucher, as the report shows it
    debit, credit Decimal, BHD to the fils; a blank cell is 0
    balance       Decimal, the running balance after the row exactly as the report prints it: on the real export a
                  magnitude, never negative; None when the cell is blank (the report leaves it off some credit
                  legs). balance() gives the signed figure, with the blanks worked out
    currency, payment_mode, salesman
                  as written; a payment mode of '0' or 'Payment Mode' (noise in the export) is None
There is no narration and no row hash. Rows stay in file order. An account's rows stand together, and the helpers
take them to be in date order, as the report writes them (the real-file check prints how many pairs are).

WHAT THE HELPERS ASSUME (so a caller can say it). The Balance column is the account's running balance, which
moves by debit less credit, and balance() gives it signed: a debit balance positive (what a customer owes), a
credit balance negative. balance() and movements() read the same rows, so for one account
    balance(first - 1 day) + debits - credits (the window's movements, every kind) = balance(last)
and "opening + SI - RCTV - SRV - CN = closing" is that line with the kinds spelt out (any PV, JV or other
movement is the difference).
The real export prints the column in two ways a caller must not take at face value. (1) A blank cell on some
credit legs (the payment inside a cash sale, mostly the last row of its voucher): the balance is exactly nought
there, and balance() works it out as the last stated balance plus the debit less the credit of the rows after
it, in the file's own order. (2) No sign: the figure is a magnitude, so a customer in credit looks like one in
debit; balance() takes the sign from the rows' running total (running_balances says when). It says None only
for an account no row of which states a balance.
tests/test_ops_ledger_export.py, run with YQ_OPS_LEDGER_SAMPLE naming a real export, prints the counts that show
both: the blank rows, the accounts whose last row is blank, the runs of blank rows and how many the forward
balance reaches, the accounts printed as magnitudes. A report that stops before the days asked for cannot
answer: the helpers refuse a day after the as-on date instead of answering from a part of the month.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from scripts.ops import fmt, loadplan, paths
from scripts.ops.workbook import model as mdl

SEARCH_DAYS = 10                   # find() looks in drop folders dated from the month's last day to this many days on
ZERO = Decimal(0)
# BHD. How far a printed balance may sit from the account's opening plus the sum of its rows for running_balances to
# take its sign from that sum. Rounding in the report moves a balance by fils; a wrong opening moves it by a figure.
SIGN_TOLERANCE = Decimal("0.5")
# Bahrain's VAT rate. The portal's own ex-VAT fallback is the same: gross / 1.1 (glossary, scripts/seed_memory.py).
VAT_RATE = Decimal("0.10")

# The voucher prefixes Focus writes ("SI : 1234") and what each one is. Anything else is kind OTHER, and the
# Export lists it by prefix and count.
KIND_BY_PREFIX = {
    "SI": "sale", "RCTV": "receipt", "SRV": "return", "CN": "credit_note", "PV": "payment", "JV": "journal",
}
OTHER = "other"
KINDS = (*KIND_BY_PREFIX.values(), OTHER)

NOISE_PAYMENT_MODES = frozenset({"0", "payment mode"})      # what the export writes when a row has no mode (folded)
NO_SALESMAN = "(no salesman)"                              # the key of returns that name no salesman
_PREFIX = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]{0,11})\s*:")
_ZERO_TEXT = re.compile(r"0+(\.0*)?")
_VAT_WORD = re.compile(r"\bvat\b", re.I)


class LedgerExportError(ValueError):
    """The export cannot be read as a Ledger. The text is one plain sentence that names the file (a name,
    never a path), fit to print as it is. It carries the kind of a reader's error and never its text, which
    can quote what a cell holds."""


def _cannot_open(path: Path, exc: BaseException) -> LedgerExportError:
    return LedgerExportError(
        f"the Ledger export {path.name} could not be opened ({type(exc).__name__}); the reader takes the .xlsx "
        "export of Focus, not an old-style .xls, and not a file that is damaged, half copied or held by another "
        "program")


# -- the vouchers -------------------------------------------------------------

def prefix_of(voucher) -> str:
    """'SI : 1234' -> 'SI', 'rctv:7' -> 'RCTV' (upper case); '' for a voucher that does not begin 'PREFIX :'."""
    m = _PREFIX.match(str(voucher or ""))
    return m.group(1).upper() if m else ""


def kind_of(voucher) -> str:
    """What a voucher is: SI sale, RCTV receipt, SRV return, CN credit_note, PV payment, JV journal; any other
    prefix (and a voucher without one) is 'other'."""
    return KIND_BY_PREFIX.get(prefix_of(voucher), OTHER)


def fold(name) -> str:
    """An account name as it is compared: spaces collapsed, case ignored. Two reports can spell one account
    with other spacing or case, so a name is never compared with ==."""
    return " ".join(str(name or "").split()).casefold()


# -- the export ---------------------------------------------------------------

@dataclass
class Export:
    """A Ledger export as read: the rows (see the module note), what the title block says and what the
    read noticed. Treat it as read-only: the helpers answer from it and share its rows."""
    rows: list[dict] = field(repr=False)
    source: str                                                  # the file's name
    as_on: date                                                  # the title block's "As on date"
    first_date: date | None = None                               # the dates of the first and the last entry
    last_date: date | None = None
    sha: str | None = None                                       # 'sha256:<hex>' of the file
    unknown: list[str] = field(default_factory=list)             # 'type not known: ABC (3 rows)', one per prefix
    prefixes: dict[str, int] = field(default_factory=dict)       # rows per voucher prefix ('' = none)
    notes: list[str] = field(default_factory=list)               # anything else the read noticed, as sentences
    grand_total: tuple[Decimal, Decimal] | None = None           # Focus's own Grand Total row: (debit, credit)
    _index: dict | None = field(default=None, repr=False, compare=False)

    def by_account(self) -> dict[str, list[dict]]:
        """{folded account name: its rows in file order}, built on first use."""
        if self._index is None:
            index: dict[str, list[dict]] = {}
            for r in self.rows:
                index.setdefault(fold(r["account"]), []).append(r)
            self._index = index
        return self._index

    @property
    def accounts(self) -> list[str]:
        """Every account that has at least one row, as its section spells it, in file order."""
        return [rows[0]["account"] for rows in self.by_account().values()]

    def rows_total(self) -> tuple[Decimal, Decimal]:
        """(debit, credit) added up over every row read."""
        return (sum((r["debit"] for r in self.rows), ZERO), sum((r["credit"] for r in self.rows), ZERO))

    def grand_total_gap(self) -> tuple[Decimal, Decimal] | None:
        """The rows' totals less Focus's own Grand Total, debit and credit: (0, 0) when the read took every
        row and nothing else; None when the file has no Grand Total row to compare with."""
        if self.grand_total is None:
            return None
        debit, credit = self.rows_total()
        return debit - self.grand_total[0], credit - self.grand_total[1]


@dataclass(frozen=True)
class Movement:
    """What one account moved in a window, for one kind of voucher."""
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    count: int = 0                  # rows

    @property
    def net(self) -> Decimal:
        """debit less credit: what the balance moved by."""
        return self.debit - self.credit


@dataclass(frozen=True)
class ReturnVoucher:
    """One sales return (SRV voucher) with its value."""
    voucher: str
    date: date
    salesman: str | None            # the first salesman any leg of the voucher names
    gross: Decimal                  # VAT included: what the customer (or cash) side carries
    vat: Decimal
    ex_vat: Decimal                 # gross less vat
    legs: int                       # rows the voucher has in the export (a clean return has at least two)
    basis: str                      # 'legs': the VAT account's own legs; 'rate': none found, gross / (1 + rate)


# -- reading ------------------------------------------------------------------

def _money(x) -> Decimal:
    """A debit, credit or balance as BHD to the fils (three decimals, half up) from the float Excel gave."""
    q = fmt.q3(x)
    return q if q else ZERO


def _payment_mode(text) -> str | None:
    """The mode as written; None for a blank one and for the export's noise ('0', 'Payment Mode')."""
    t = " ".join(str(text or "").split())
    if not t or t.casefold() in NOISE_PAYMENT_MODES or _ZERO_TEXT.fullmatch(t):
        return None
    return t


def _grand_total(grid, ing) -> tuple[Decimal, Decimal] | None:
    """(debit, credit) of the 'Grand Total' row that closes the sheet, or None when there is none."""
    if grid.shape[1] < 5:
        return None
    for i in range(len(grid) - 1, max(len(grid) - 60, -1), -1):
        first = grid.iat[i, 0]
        if isinstance(first, str) and " ".join(first.split()).casefold() == "grand total":
            debit, credit = ing.norm_num(grid.iat[i, 3]), ing.norm_num(grid.iat[i, 4])
            return None if debit is None and credit is None else (_money(debit), _money(credit))
    return None


def read(path) -> Export:
    """The Ledger export at `path`, shaped (see the module note). The narration is dropped here.

    LedgerExportError (one plain sentence) for a file that is not there, cannot be opened as a workbook or
    has no "As on date" in its title block; ingest.HeaderMismatch, with the parser's own words, when the
    columns are not those the parser was written for."""
    from scripts import ingest as ing        # pandas: imported only when a workbook is really read

    path = Path(path)
    if not path.is_file():
        raise LedgerExportError(f"no such Ledger export: {path.name}")
    try:
        sha = loadplan.sha256_file(path)
        grid = ing.read_grid(path)
    except (ImportError, MemoryError):       # this PC, not the file: said as it is
        raise
    except Exception as exc:                 # noqa: BLE001  whatever the reader makes of a file it cannot open
        raise _cannot_open(path, exc) from None
    as_on = fmt.as_date(ing.report_date_from_title(grid))
    if as_on is None:
        raise LedgerExportError(
            f"the Ledger export {path.name} has no \"As on date\" in its title block, so it cannot be dated; "
            "export it again from Focus (the Ledger, as on the last day of the month)")
    grand = _grand_total(grid, ing)
    parsed = ing.parse_ledger(grid, path.name)
    del grid
    for r in parsed:
        r.pop("narration", None)             # personal ID numbers live here: gone before anything else looks

    rows: list[dict] = []
    no_account = 0
    for r in parsed:
        if r["account"] is None:             # a detail row above the first account's header
            no_account += 1
            continue
        voucher = r["voucher"] or ""
        prefix = prefix_of(voucher)
        rows.append({
            "account": r["account"], "date": date.fromisoformat(r["entry_date"]), "voucher": voucher,
            "prefix": prefix, "kind": KIND_BY_PREFIX.get(prefix, OTHER), "counter": r["counter_account"],
            "debit": _money(r["debit_bhd"]), "credit": _money(r["credit_bhd"]),
            "balance": None if r["balance_bhd"] is None else _money(r["balance_bhd"]),
            "currency": r["currency"], "payment_mode": _payment_mode(r["payment_mode"]), "salesman": r["salesman"],
        })
    del parsed

    prefixes = Counter(r["prefix"] for r in rows)
    unknown = [f"type not known: {p} ({n} rows)" for p, n in sorted(prefixes.items())
               if p and p not in KIND_BY_PREFIX]
    notes: list[str] = []
    if prefixes.get(""):
        notes.append(f"{prefixes['']} rows carry a voucher that does not begin 'PREFIX :' (read as kind {OTHER})")
    if no_account:
        notes.append(f"{no_account} rows stand above the first account's header and were left out")
    days = [r["date"] for r in rows]
    return Export(rows=rows, source=path.name, as_on=as_on, first_date=min(days, default=None),
                  last_date=max(days, default=None), sha=sha, unknown=unknown, prefixes=dict(prefixes), notes=notes,
                  grand_total=grand)


# -- finding the file ---------------------------------------------------------

def _title_date(path: Path) -> tuple[date | None, str | None]:
    """(the title block's "As on date", None), or (None, why it has none). Reads the first rows only."""
    import pandas as pd
    from scripts import ingest as ing

    try:
        top = pd.read_excel(path, header=None, dtype=object, engine="openpyxl", nrows=8)
    except Exception as exc:  # noqa: BLE001
        return None, f"could not be opened ({type(exc).__name__})"
    day = fmt.as_date(ing.report_date_from_title(top))
    return (day, None) if day else (None, "has no \"As on date\" in its title block")


def _listed(items: list[str], limit: int = 4) -> str:
    return ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")


def find(month: str, *, drops=None, path=None) -> tuple[Path | None, str]:
    """(the Ledger export that covers `month` 'YYYY-MM', a sentence that says why this one, or why none).

    Where it looks: files directly in the drop folders dated from the month's last day to SEARCH_DAYS days
    after it (a sub-folder is not entered), whose name ingest.classify calls the Ledger (a Stock ledger and
    ledger_detail are not). `drops` is the list of (date, folder) to look in; the default is every dated drop
    folder under the data root (scripts.ops.paths.drop_folders).

    Which: the one as on the month's last day, else the smallest as-on after it (the report is cumulative, so
    a later one still holds the whole month). A Ledger as on an earlier day stops before the month ends and
    is refused, with the reason. Among files with the same as-on the newest by modified time wins.

    `path` names the file: it replaces the search, not the as-on rule (any name will do, but an earlier as-on
    is still refused). ValueError, in one sentence, for a month that is not written YYYY-MM."""
    _first, last_day = mdl.month_bounds(month)
    if path is not None:
        return _named(Path(path), last_day)
    from scripts import ingest as ing        # pandas: imported only when a search is made

    end = last_day + timedelta(days=SEARCH_DAYS)
    folders = sorted(((d, Path(p)) for d, p in (paths.drop_folders() if drops is None else drops)
                      if last_day <= d <= end), key=lambda t: (t[0], str(t[1])))
    where = f"dated {last_day.isoformat()} to {end.isoformat()}"
    if not folders:
        return None, f"no drop folder is {where}, where the Ledger of {month} is looked for"

    found: list[tuple[date, int, date, str, Path]] = []       # (as-on, modified ns, folder date, label, file)
    unusable: list[str] = []
    for folder_day, folder in folders:
        try:
            files = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name.lower())
        except OSError as exc:
            unusable.append(f"{folder.name} (could not be read: {type(exc).__name__})")
            continue
        for p in files:
            if p.name.startswith("~$") or not p.suffix.lower().startswith(".xls"):
                continue                      # Excel's lock on an open workbook; anything that is not a workbook
            if ing.classify(p.name) != "ledger_entries":
                continue
            label = f"{folder.name}/{p.name}"
            as_on, problem = _title_date(p)
            if as_on is None:
                unusable.append(f"{label} {problem}")
                continue
            try:
                modified = p.stat().st_mtime_ns
            except OSError:
                modified = 0
            found.append((as_on, modified, folder_day, label, p))

    usable = [c for c in found if c[0] >= last_day]
    early = [c for c in found if c[0] < last_day]
    if not usable:
        if early:
            as_on, _m, _d, label, _p = max(early, key=lambda c: c[0])
            why = (f"the Ledger found is as on {as_on.isoformat()} ({label}), before the month ends "
                   f"({last_day.isoformat()}): it cannot cover {month}. Export the Ledger again as on "
                   f"{last_day.isoformat()}")
        elif unusable:
            why = f"no usable Ledger file in the drop folders {where}"
        else:
            why = (f"no Ledger file lies directly in the drop folders {where} ("
                   f"{_listed([f.name for _d, f in folders])})")
        return None, why + (f"; not usable: {_listed(unusable)}" if unusable else "")

    best_day = last_day if any(c[0] == last_day for c in usable) else min(c[0] for c in usable)
    pool = [c for c in usable if c[0] == best_day]
    chosen = max(pool, key=lambda c: (c[1], c[2], c[3]))
    as_on, _m, _d, label, file = chosen
    if as_on == last_day:
        why = f"the Ledger as on {as_on.isoformat()}, in {label}"
    else:
        why = (f"no Ledger is as on {last_day.isoformat()}; the earliest after it is as on {as_on.isoformat()}, in "
               f"{label}, and it is read up to {last_day.isoformat()}")
    skipped = [f"{c[3]} (as on {c[0].isoformat()})" for c in usable if c is not chosen]
    skipped += [f"{c[3]} (as on {c[0].isoformat()}, before the month ends)" for c in early]
    skipped += unusable
    return file, why + (f"; not used: {_listed(skipped)}" if skipped else "")


def _named(path: Path, last_day: date) -> tuple[Path | None, str]:
    """find() for a file the caller named."""
    if not path.is_file():
        return None, f"no such file: {path.name}"
    as_on, problem = _title_date(path)
    if as_on is None:
        return None, f"{path.name} {problem}"
    if as_on < last_day:
        return None, (f"the Ledger {path.name} is as on {as_on.isoformat()}, before the month ends "
                      f"({last_day.isoformat()}): it cannot cover the month. Export the Ledger again as on "
                      f"{last_day.isoformat()}")
    if as_on == last_day:
        return path, f"the file named, the Ledger as on {as_on.isoformat()}"
    return path, (f"the file named, the Ledger as on {as_on.isoformat()}; it is read up to the month's last day, "
                  f"{last_day.isoformat()}")


# -- questions on the rows ----------------------------------------------------

def _as_day(x, what: str) -> date:
    d = fmt.as_date(x)
    if d is None:
        raise ValueError(f"{what}: not a date: {x!r}")
    return d


def _check_day(export: Export, day: date) -> None:
    if day > export.as_on:
        raise ValueError(f"the Ledger {export.source} is as on {export.as_on.isoformat()}: it cannot speak of "
                         f"{day.isoformat()}")


def _window(export: Export, first, last) -> tuple[date, date]:
    lo, hi = _as_day(first, "first day"), _as_day(last, "last day")
    if hi < lo:
        raise ValueError(f"the last day ({hi.isoformat()}) is before the first ({lo.isoformat()})")
    _check_day(export, hi)
    return lo, hi


def movements(export: Export, first, last) -> dict[str, dict[str, Movement]]:
    """{account: {kind: Movement}} for the rows dated `first` to `last` (dates or ISO text, both days included).

    The accounts are as their sections spell them, in file order; a kind with no row in the window is not
    there. Every kind is kept, 'other' included, so an account's kinds add up to everything it moved."""
    lo, hi = _window(export, first, last)
    sums: dict[str, dict[str, list]] = {}
    for r in export.rows:
        if lo <= r["date"] <= hi:
            slot = sums.setdefault(r["account"], {}).setdefault(r["kind"], [ZERO, ZERO, 0])
            slot[0] += r["debit"]
            slot[1] += r["credit"]
            slot[2] += 1
    return {account: {kind: Movement(*v) for kind, v in kinds.items()} for account, kinds in sums.items()}


def running_balances(legs: list[dict]) -> list[Decimal | None]:
    """The signed balance after each of one account's rows, in the file's order (balance() reads its answer
    here). A debit balance is positive, a credit balance negative. It works out what the report's Balance cells
    leave open, in two ways.

    A blank cell. The report leaves the Balance off some credit legs (the payment inside a cash sale is the
    usual one, often the last row of its voucher); on the real export every such balance is exactly nought. A
    blank row takes the balance after the row before it, plus its debit, less its credit: the last stated
    balance worked forward through every row since. A blank row with no stated balance before it (the first
    rows of an account) is worked back from the first stated balance after it. An account no row of which
    states a balance gives None throughout.

    No sign. The real export prints a balance as a magnitude, never negative: a customer in credit shows the
    figure of one in debit. When no stated balance of the account is negative, the sign of each comes from the
    rows: the balance after a row is the opening balance plus the running total of debit less credit since the
    account's first row. The real export has no opening balance (its first row opens the account), so that is
    nought; a report that does write one above the first row (which the reader leaves out) is found the same
    way, as whichever of the two openings the first stated figure allows makes the magnitudes equal the rows'
    totals. The sign is used when that explains most of the stated balances (to within SIGN_TOLERANCE); when
    nothing does, the stated balances stay as they are.

    The size of a stated balance is never replaced: only a blank is filled, and a magnitude gets its sign."""
    out: list[Decimal | None] = [None] * len(legs)
    stated = [i for i, r in enumerate(legs) if r["balance"] is not None]
    if not stated:
        return out
    totals: list[Decimal] = []
    run = ZERO
    for r in legs:
        run += r["debit"] - r["credit"]
        totals.append(run)
    opening = ZERO
    unsigned = not any(legs[i]["balance"] < 0 for i in stated)
    if unsigned:
        first = stated[0]
        best = -1
        for candidate in (ZERO, legs[first]["balance"] - totals[first], -legs[first]["balance"] - totals[first]):
            near = sum(1 for i in stated if abs(abs(candidate + totals[i]) - legs[i]["balance"]) <= SIGN_TOLERANCE)
            if near > best:                      # nought wins a tie: it is listed first
                best, opening = near, candidate
        unsigned = best * 2 > len(stated)
    for i in stated:
        out[i] = -legs[i]["balance"] if unsigned and opening + totals[i] < 0 else legs[i]["balance"]
    for i in range(stated[0] + 1, len(legs)):
        if out[i] is None:
            out[i] = out[i - 1] + legs[i]["debit"] - legs[i]["credit"]
    for i in range(stated[0] - 1, -1, -1):
        out[i] = out[i + 1] - (legs[i + 1]["debit"] - legs[i + 1]["credit"])
    return out


def balance(export: Export, account, day) -> Decimal | None:
    """The account's signed balance at the end of `day` (a debit balance positive, what a customer owes; a
    credit balance negative), from the report's running Balance column. It is the balance after the last row
    dated `day` or earlier, the later row of the file for two on one day. A blank Balance cell is worked out
    from the stated ones and a balance printed without its sign gets it from the rows: see running_balances.
    Before the account's first row it is what the first row opened with (the balance after it, less its debit,
    plus its credit), which carries an opening balance the report wrote above it.

    None only when the export cannot give it: no such account (name matched with fold), or no row of the
    account states a balance. ValueError for a day after the export's as-on date."""
    d = _as_day(day, "balance")
    _check_day(export, d)
    legs = export.by_account().get(fold(account))
    if not legs:
        return None
    latest = None
    for i, r in enumerate(legs):
        if r["date"] <= d and (latest is None or r["date"] >= legs[latest]["date"]):
            latest = i
    after = running_balances(legs)
    if latest is not None:
        return after[latest]
    earliest = 0
    for i, r in enumerate(legs):
        if r["date"] < legs[earliest]["date"]:
            earliest = i
    if after[earliest] is None:
        return None
    return after[earliest] - legs[earliest]["debit"] + legs[earliest]["credit"]


def customer_legs(export: Export, accounts, first=None, last=None) -> dict[str, list[dict]]:
    """{each account as the caller wrote it: its rows in file order} for the accounts passed (customers, say,
    from the receivables report), matched with fold; [] for a name the export does not hold. `first` and
    `last` (dates or ISO text) keep the rows dated within them; none keeps them all. The rows are the
    export's own: do not change them."""
    if isinstance(accounts, str):
        accounts = [accounts]
    lo = None if first is None else _as_day(first, "first day")
    hi = None if last is None else _as_day(last, "last day")
    if hi is not None:
        _check_day(export, hi)
        if lo is not None and hi < lo:
            raise ValueError(f"the last day ({hi.isoformat()}) is before the first ({lo.isoformat()})")
    index = export.by_account()

    def within(r: dict) -> bool:
        return (lo is None or r["date"] >= lo) and (hi is None or r["date"] <= hi)

    return {a: [r for r in index.get(fold(a), ()) if within(r)] for a in accounts}


def _is_vat(row: dict, names: set | None) -> bool:
    """Is this row a VAT leg? A name the caller gave: that account. Otherwise an account whose name holds the
    word VAT and whose row is not a credit (a customer called 'Vat Experts' is credited by its return; the VAT
    of a return is debited)."""
    if names is not None:
        return fold(row["account"]) in names
    return bool(_VAT_WORD.search(row["account"])) and row["debit"] >= row["credit"]


def returns_by_voucher(export: Export, first, last, *, vat_accounts=None,
                       vat_rate=VAT_RATE) -> list[ReturnVoucher]:
    """Every sales return (SRV voucher) dated `first` to `last`, with its value, in file order.

    A voucher is the rows that share its text and day, in any account. Its gross is what the legs other than
    VAT credit (the customer or cash side); its VAT is the net debit of its VAT legs. A VAT account is one
    whose name holds the word VAT and which the voucher debits, or one of `vat_accounts` (exact names,
    fold-matched) when the caller names them. A return with no VAT leg is ex-VAT'd by rate,
    gross / (1 + vat_rate), and says so in `basis`.
    This stands on the usual posting (the customer credited, the returns account and VAT debited, no stock
    or cost legs): a voucher with fewer than two legs, or posted another way, is for the caller to look at."""
    lo, hi = _window(export, first, last)
    rate = Decimal(str(vat_rate))
    names = None if vat_accounts is None else {fold(a) for a in vat_accounts}
    groups: dict[tuple[str, date], list[dict]] = {}
    for r in export.rows:
        if r["kind"] == "return" and lo <= r["date"] <= hi:
            groups.setdefault((" ".join(r["voucher"].split()).upper(), r["date"]), []).append(r)
    out: list[ReturnVoucher] = []
    for (_key, day), legs in groups.items():
        vat_legs = [r for r in legs if _is_vat(r, names)]
        gross = sum((r["credit"] for r in legs if not _is_vat(r, names)), ZERO)
        if vat_legs:
            vat, basis = sum((r["debit"] - r["credit"] for r in vat_legs), ZERO), "legs"
        else:
            vat, basis = gross - _money(gross / (1 + rate)), "rate"
        salesman = next((r["salesman"] for r in legs if r["salesman"]), None)
        out.append(ReturnVoucher(voucher=legs[0]["voucher"], date=day, salesman=salesman, gross=gross, vat=vat,
                                 ex_vat=gross - vat, legs=len(legs), basis=basis))
    return out


def returns_ex_vat_by_salesman(export: Export, first, last, *, vat_accounts=None,
                               vat_rate=VAT_RATE) -> dict[str, Decimal]:
    """{salesman: the value of the sales returns dated `first` to `last`, VAT left out}, as returns_by_voucher
    works it out. The salesman is as the export writes it; returns that name none are under NO_SALESMAN, last.
    Positive: value returned."""
    totals: dict[str, Decimal] = {}
    for v in returns_by_voucher(export, first, last, vat_accounts=vat_accounts, vat_rate=vat_rate):
        key = v.salesman or NO_SALESMAN
        totals[key] = totals.get(key, ZERO) + v.ex_vat
    return dict(sorted(totals.items(), key=lambda kv: (kv[0] == NO_SALESMAN, kv[0].casefold())))
