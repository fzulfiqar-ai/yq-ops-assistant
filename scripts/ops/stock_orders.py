"""Goods on order: the "order placed" log and its clearing by the main store's receipts, first in, first out.

    the words behind it (wired in scripts.ops.stock):  /yq-stock ordered <supplier> [<order date>]
                                                        /yq-stock cancel <supplier> <order date>

THE OWNER'S RULE (20-Oct-2026). When he has placed an order he says so. The command reads that supplier's final
quantities from his edited workbook (or lines he types), previews them, and only after his yes logs them: one
promise line per item in the KPI log (business_data/ops/kpi_log.jsonl, kind 'promise'). From then on they are
deducted from the order list as goods on the way, until the main store's receipts cover them. Nothing is ever
ordered or sent by the system: a line records an order he placed himself, and it holds an item code and a number
of units, never a cost or a price.

  * One order line of one item is one ref: '<supplier slug>:<order date>:<CODE>' ('vfan:2026-10-20:CB2'). The log
    is only appended to; the newest line of a ref is its state (stock.read_promises keeps it). A corrected quantity
    is a new line of the same ref; a code he takes off the order, or a whole order he cancels, is a line in state
    'cancelled'; logging the same order again writes nothing.
  * The preview carries a token, 12 letters worked out from the workbook's sha and the lines; his yes gives it
    back, so a file or a line that changed between the preview and the yes is never logged.
  * The write holds a lock file beside the log (<log>.lock, made only where none exists). A lock younger than
    LOCK_STALE_SECONDS means another command is writing, and the write is refused in one sentence; an older one
    was left by a command that stopped, and is taken over, once.

THE CLEARING (fifo), item by item:
  * A line is matched to an item by its code (the item's code and the line's compared as stock.code_key compares
    them, inner spaces collapsed too); where no item has the code, by the model code an item's name starts with
    (items.leading_code), when exactly one item has it; else it is unmatched, and so is a code two items share.
    A line whose quantity or order date cannot be read is unmatched too.
  * The item's open lines go oldest first (order date, then ref), and so do its main-store supplier receipts
    (Item.receipts: a transfer back from a van is no receipt). Each receipt pays the oldest line still due that is
    dated strictly before the receipt's day, then the next one, until its units run out. A receipt on an order's
    own day, or before it, pays nothing of that order; a newer order is paid only once every older one is. A part
    arrival leaves the rest due; units beyond every line due are ignored (stock bought on another order, or
    elsewhere). Every supplier receipt of the item counts, whoever sent it: a receipt names no order.
  * Expected by = the order date + lead_of(supplier) days. Late = units still due more than late_days days after
    that, on D (the ledger's last day). Future = an order dated after D: the ledger cannot hold its receipt yet.

CONTRACT (scripts.ops.stock and the /yq-stock words rely on it)
  slug(supplier) -> str                   letters and digits in lower case, accents dropped, each run joined to
                                          the next by '-' ('VFAN Co., Ltd.' -> 'vfan-co-ltd'); '' when it has none
  ref_of(supplier, order_date, code) -> str          'vfan:2026-10-20:CB2'
  whole(x) -> Decimal | None              a quantity as a whole number of units above nought, else None ('30',
                                          30.0 and '1,000' are whole; 2.5, 0, 'ten', None and True are not)
  promise_lines(supplier, order_date, lines, source, logged_at, today=None) -> list[dict]
      supplier    the supplier's name as he gives it (spaces collapsed; it must hold a letter or a digit)
      order_date  the day the order was placed: a date or 'YYYY-MM-DD', never after today
      lines       [(item code, units)]: his final quantities, each code once
      source      the workbook's file name, or "typed"
      logged_at   now: a datetime or its ISO text, kept to the second
      today       the day the order date may not pass (default: logged_at's day)
    One line per item, in his order, with exactly the keys LINE_KEYS and every value text:
      {"kind": "promise", "ref", "supplier", "item_code" (upper case, trimmed), "qty" (a whole number above
       nought), "event_date" (the order date, ISO), "logged_at" (ISO, seconds), "state": "open", "source"}
    Refused in one plain sentence (stock.StockInput, a ValueError the command prints as it is): no line; a line
    that is not a code and a quantity; a blank code; a code given twice; a quantity that is not a whole number
    above nought; no supplier, or one with no letter or digit; no order date, one that is no date, or one after
    today; a blank source; a logging time that is no date and time.
  token(file_sha, supplier, order_date, lines) -> str
      12 letters a to p: the first 12 hex digits of the sha256 of the inputs written one canonical way (JSON of
      the file's sha or '', the supplier, the order date and the lines as [CODE, units], sorted), each digit a
      letter (0 -> a ... f -> p). The same order in another line order gives the same token; another file sha,
      supplier or date, a changed quantity, a line added or dropped gives another.
  plan_write(existing, new_lines) -> {"write": [line], "unchanged": [ref], "cancel": [line]}
      existing    the newest line of each ref (stock.read_promises(); None = no log yet)
      new_lines   promise_lines(...) of an order (lines of two orders are planned order by order)
      write       the new lines to append: a ref with no line yet, a corrected quantity, a ref closed before
      unchanged   the refs whose newest line is open with the same quantity: the same order again writes nothing
      cancel      a line in state "cancelled" (with the new lines' logging time and source) for every ref of the
                  same supplier and order date that is not closed and that the new lines no longer hold
      A ref given twice in new_lines is a ValueError.
  cancel_lines(existing, supplier, order_date, logged_at, source="typed") -> list[dict]
      a line in state "cancelled" for every ref of that supplier and order date that is not closed ([] when none
      is); no supplier or one with no letter or digit, no order date or one that is no date: refused as
      promise_lines refuses them
  lock_of(path) -> Path                   <path>.lock, beside the log
  locked_append(path, texts) -> int
      the one write of the KPI log, shared by every writer of it (this module's promise lines and
      scripts.ops.kpi_log's evidence lines): the texts (each one line, already JSON) appended to path through
      paths.append_lines, while lock_of(path) is held. Checked first, nothing made or written when one fails:
      the place (business_data/ or exports/ of the data root, else PermissionError) and every text (a string
      with no line break, else ValueError). A lock younger than LOCK_STALE_SECONDS raises RuntimeError(LOCK_BUSY);
      an older one is removed and the lock tried once more. The lock is removed when the write ends, whatever
      happened. Returns the number of texts written; no text: 0, and no lock is taken.
  append(path, lines) -> int
      the promise lines appended to path (stock.kpi_log_file()), one json.dumps per line (ensure_ascii=False,
      sort_keys=True), through locked_append. Checked first, nothing made or written when one fails: the place
      (as locked_append) and every line (exactly LINE_KEYS, every value text, kind 'promise', a ref, state 'open'
      or a closed state, an open line's qty whole: else ValueError). Returns the number of lines written; no
      line: 0, and no lock is taken.
  fifo(items, promises, d, lead_of, late_days=LATE_DAYS) -> dict
      items       the stock Items to match (stock.build's plannable items: display items left out)
      promises    stock.read_promises() (None = no log); a closed line is passed over
      d           the ledger's last day
      lead_of     supplier name ('' when a line names none) -> days: the lead time it is expected in (None: no
                  expected day, never late)
    {"lines": [{"ref", "supplier", "code", "item_name" (None: unmatched), "ledger_name" (None: unmatched),
                "date", "qty", "received", "due", "expected_by" (date | None), "late", "future"}],
         every open line whose quantity and date can be read, oldest first (date, ref); unmatched lines are due
         in full, and deducted from nothing
     "on_order": {ledger name: units due},   the matched items with units due: what the order list deducts
     "logged": int,       the open lines (not closed), readable or not
     "counted": int,      the matched lines with units due: the lines deducted from the order list
     "unmatched": int,    the open lines that match no item, or whose quantity or date cannot be read
     "log": bool}         False when there is no log
    Units are Decimals. Nothing is changed: the caller sets each item's on_order from "on_order".

Nothing here reads the database or the network. The log and its lock are the only files written, and only by
locked_append().
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.ops import fmt, paths
from scripts.ops.items import leading_code

KIND = "promise"
OPEN = "open"
CANCELLED = "cancelled"
TYPED = "typed"
# The keys of a promise line, and no other: no cost, no price, ever.
LINE_KEYS = ("kind", "ref", "supplier", "item_code", "qty", "event_date", "logged_at", "state", "source")
LATE_DAYS = 30                 # late: units still due this many days after the expected day
LOCK_STALE_SECONDS = 60        # a lock older than this was left by a command that stopped
LOCK_BUSY = "the KPI log is being written by another command: try again in a minute"
TOKEN_LEN = 12
TOKEN_LETTERS = "abcdefghijklmnop"     # hex digit 0 -> a ... f -> p
ZERO = Decimal(0)
_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+")   # '1,000': thousands written with commas


# ── small helpers ─────────────────────────────────────────────────────────────

def _stock():
    """scripts.ops.stock, imported when used: it imports the command's modules at its own top."""
    from scripts.ops import stock
    return stock


def _refuse(sentence: str) -> ValueError:
    """One plain sentence the command prints as it is (stock.StockInput, a ValueError)."""
    return _stock().StockInput(sentence)


def _text(x) -> str:
    """A name or a value as text, its spaces collapsed ('' for nothing)."""
    return "" if x is None else " ".join(str(x).split())


def _shown(x) -> str:
    """A value he gave, as a refusal quotes it."""
    text = _text(x)
    return f"'{text[:40]}'" if text else "nothing"


def _code(code) -> str:
    """An item code as it is logged and matched: stock.code_key's (the ends trimmed, upper case), inner spaces
    collapsed too."""
    return " ".join(str(code or "").split()).upper()


def _day(x) -> date | None:
    return fmt.as_date(x)


def _stamp(logged_at) -> str:
    """The time a line is logged, as ISO text to the second."""
    if isinstance(logged_at, datetime):
        moment = logged_at
    else:
        try:
            moment = datetime.fromisoformat(_text(logged_at))
        except ValueError:
            raise _refuse("The time of logging is not a date and time.") from None
    return moment.replace(microsecond=0).isoformat()


def _state(row: dict) -> str:
    """A line's state, folded; a line that names none is open."""
    return _text(row.get("state")).lower() or OPEN


def _closed(row: dict) -> bool:
    return _state(row) in _stock().CLOSED_STATES


def _newest(rows) -> dict[str, dict]:
    """{ref: its newest line} of the promise lines, as stock.read_promises keeps them (a later line wins)."""
    out: dict[str, dict] = {}
    for n, row in enumerate(rows or []):
        if isinstance(row, dict) and row.get("kind") == KIND:
            out[str(row.get("ref") or row.get("id") or f"line {n}")] = row
    return out


def _order_of(ref) -> str | None:
    """'vfan:2026-10-20:' for the ref 'vfan:2026-10-20:CB2': the order a ref belongs to (None for another shape).
    A slug holds no ':' and a date none, so the code may."""
    parts = str(ref or "").split(":", 2)
    return f"{parts[0]}:{parts[1]}:" if len(parts) == 3 and parts[0] and parts[1] else None


def _pair(x) -> tuple:
    if not isinstance(x, (tuple, list)) or len(x) != 2:
        raise _refuse("Each line is an item code and its number of units, two values and no more.")
    return x[0], x[1]


def _supplier(supplier) -> str:
    name = _text(supplier)
    if not name:
        raise _refuse("The order names no supplier.")
    if not slug(name):
        raise _refuse(f"The supplier {_shown(name)} has no letter or digit to log the order under.")
    return name


def _order_day(order_date) -> date:
    day = _day(order_date)
    if day is None and not _text(order_date):
        raise _refuse("The order has no date: write it as YYYY-MM-DD.")
    if day is None:
        raise _refuse(f"The order date {_shown(order_date)} is not a date: write it as YYYY-MM-DD.")
    return day


def _cancelled(row: dict, stamp: str, source: str) -> dict:
    """A line closing `row` (state 'cancelled'), the newest line of its ref from now on."""
    units = whole(row.get("qty"))
    return {"kind": KIND, "ref": str(row.get("ref")), "supplier": _text(row.get("supplier")),
            "item_code": _code(row.get("item_code")),
            "qty": str(int(units)) if units is not None else _text(row.get("qty")),
            "event_date": _text(row.get("event_date")), "logged_at": stamp, "state": CANCELLED, "source": source}


# ── the lines ─────────────────────────────────────────────────────────────────

def slug(supplier) -> str:
    """The supplier as a ref spells it: letters and digits in lower case (accents dropped), each run joined to the
    next by '-' ('VFAN Co., Ltd.' -> 'vfan-co-ltd'); '' when it has none."""
    plain = unicodedata.normalize("NFKD", _text(supplier))
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return "-".join(re.findall(r"[a-z0-9]+", plain.lower()))


def ref_of(supplier, order_date, code) -> str:
    """One order line of one item: '<supplier slug>:<order date>:<CODE>'."""
    day = _day(order_date)
    return f"{slug(supplier)}:{day.isoformat() if day else _text(order_date)}:{_code(code)}"


def whole(x) -> Decimal | None:
    """A quantity as a whole number of units above nought, else None. 30, '30', '30.0', 30.0 and '1,000' are whole
    numbers; 2.5, 0, -3, '', 'ten', None and True are not."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, float):
        if not x.is_integer():                        # 2.5, and nan and inf too
            return None
        v = Decimal(int(x))
    elif isinstance(x, (int, Decimal)):
        v = Decimal(x)
    else:
        text = str(x).strip()
        if _GROUPED.fullmatch(text):
            text = text.replace(",", "")
        try:
            v = Decimal(text)
        except InvalidOperation:
            return None
    if not v.is_finite() or v <= 0 or v != v.to_integral_value():
        return None
    return Decimal(int(v))


def promise_lines(supplier, order_date, lines, source, logged_at, today=None) -> list[dict]:
    """One promise line per (code, units) of an order he has placed (see the module docstring)."""
    name = _supplier(supplier)
    day = _order_day(order_date)
    stamp = _stamp(logged_at)
    limit = _day(today) if today is not None else datetime.fromisoformat(stamp).date()
    if limit is None:
        raise _refuse(f"Today {_shown(today)} is not a date.")
    if day > limit:
        raise _refuse(f"The order date {day.isoformat()} is after today ({limit.isoformat()}): an order is logged "
                      "once it is placed.")
    origin = _text(source)
    if not origin:
        raise _refuse("A promise line names its source: the workbook's file name, or 'typed'.")
    pairs = [_pair(x) for x in (lines or [])]
    if not pairs:
        raise _refuse("There is no line to log: give each item code with its number of units.")
    out: list[dict] = []
    seen: set[str] = set()
    for code, qty in pairs:
        key = _code(code)
        if not key:
            raise _refuse("A line has no item code: give each item code with its number of units.")
        if key in seen:
            raise _refuse(f"{key} is on two lines: give it once, with its whole quantity.")
        seen.add(key)
        units = whole(qty)
        if units is None:
            raise _refuse(f"The quantity of {key} ({_shown(qty)}) is not a whole number of units above nought.")
        out.append({"kind": KIND, "ref": ref_of(name, day, key), "supplier": name, "item_code": key,
                    "qty": str(int(units)), "event_date": day.isoformat(), "logged_at": stamp, "state": OPEN,
                    "source": origin})
    return out


def token(file_sha, supplier, order_date, lines) -> str:
    """12 letters a to p from the sha256 of the inputs written one canonical way (see the module docstring)."""
    rows = []
    for x in lines or []:
        code, qty = _pair(x)
        units = whole(qty)
        rows.append([_code(code), str(int(units)) if units is not None else _text(qty)])
    day = _day(order_date)
    canon = json.dumps({"file": _text(file_sha), "supplier": _text(supplier),
                        "date": day.isoformat() if day else _text(order_date), "lines": sorted(rows)},
                       ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return "".join(TOKEN_LETTERS[int(c, 16)] for c in digest[:TOKEN_LEN])


def plan_write(existing, new_lines) -> dict:
    """What logging these lines writes, against the newest line of each ref (see the module docstring)."""
    newest = _newest(existing)
    write: list[dict] = []
    unchanged: list[str] = []
    refs: set[str] = set()
    orders: dict[str, dict] = {}                      # 'slug:date:' -> the first new line of that order
    for line in new_lines or []:
        ref = str(line.get("ref") or "")
        if not ref or ref in refs:
            raise ValueError(f"plan_write: a new line without a ref, or a ref given twice ({ref or 'none'})")
        refs.add(ref)
        old = newest.get(ref)
        if old is not None and _state(old) == OPEN and whole(old.get("qty")) == whole(line.get("qty")):
            unchanged.append(ref)
        else:
            write.append(dict(line))
        order = _order_of(ref)
        if order is not None:
            orders.setdefault(order, line)
    cancel: list[dict] = []
    for order, first in orders.items():
        for ref in sorted(newest):
            row = newest[ref]
            if ref.startswith(order) and ref not in refs and not _closed(row):
                cancel.append(_cancelled(row, first["logged_at"], first["source"]))
    return {"write": write, "unchanged": unchanged, "cancel": cancel}


def cancel_lines(existing, supplier, order_date, logged_at, source=TYPED) -> list[dict]:
    """A 'cancelled' line for every open ref of that supplier and order date; [] when none is open."""
    order = f"{slug(_supplier(supplier))}:{_order_day(order_date).isoformat()}:"
    stamp = _stamp(logged_at)
    origin = _text(source) or TYPED
    newest = _newest(existing)
    return [_cancelled(newest[ref], stamp, origin) for ref in sorted(newest)
            if ref.startswith(order) and not _closed(newest[ref])]


# ── the write ─────────────────────────────────────────────────────────────────

def lock_of(path) -> Path:
    """The lock file of a log: <path>.lock, beside it."""
    path = Path(path)
    return path.with_name(path.name + ".lock")


def _checked(line) -> str:
    """A promise line as the log holds it, one JSON text; ValueError for anything else."""
    if not isinstance(line, dict) or set(line) != set(LINE_KEYS):
        raise ValueError("append: a promise line holds exactly the keys " + ", ".join(LINE_KEYS))
    if not all(isinstance(v, str) for v in line.values()):
        raise ValueError("append: every value of a promise line is text")
    state = line["state"]
    if line["kind"] != KIND or not line["ref"] or (state != OPEN and state not in _stock().CLOSED_STATES):
        raise ValueError(f"append: not a promise line of this log ({line['ref'] or 'no ref'}, state {state!r})")
    if state == OPEN and whole(line["qty"]) is None:
        raise ValueError(f"append: an open line's quantity is a whole number above nought ({line['ref']})")
    return json.dumps(line, ensure_ascii=False, sort_keys=True)


def _take(lock: Path) -> None:
    """Make the lock file, only where none exists; a stale one is taken over, once."""
    for attempt in range(2):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return
        except FileExistsError:
            pass
        try:
            age = time.time() - lock.stat().st_mtime
        except FileNotFoundError:
            continue                                  # let go while it was looked at: try again
        if age < LOCK_STALE_SECONDS or attempt:
            raise RuntimeError(LOCK_BUSY)
        paths.remove_file(lock)                       # left by a command that stopped
    raise RuntimeError(LOCK_BUSY)


def _let_go(lock: Path) -> None:
    try:
        paths.remove_file(lock)
    except OSError:
        pass                                          # still there: stale after LOCK_STALE_SECONDS, then taken over


def _place(path: Path) -> None:
    """The log and its lock go under business_data/ or exports/ of the data root, nowhere else."""
    if not (paths.allowed_output(path) and paths.allowed_output(lock_of(path))):
        raise PermissionError(f"scripts/ops writes only under business_data/ and exports/: {path}")


def _not_from_a_worktree() -> None:
    """A code worktree finds the project folder's data by itself: its unfinished code never writes the owner's
    KPI log (the rule of stock.day_file); --data-root names another folder to try it in."""
    if paths.is_worktree() and not paths.root_is_set():
        raise PermissionError("Nothing was logged: this is a code worktree, and the KPI log belongs to the project "
                              "folder (to try it, name another folder with --data-root).")


def locked_append(path, texts) -> int:
    """Append lines of text (each one JSON line) to the KPI log while its lock is held: the one write every
    writer of the log shares (see the module docstring). Returns the texts written."""
    texts = list(texts or [])
    if not texts:
        return 0
    _not_from_a_worktree()
    path = Path(path)
    _place(path)
    if not all(isinstance(t, str) and "\n" not in t and "\r" not in t for t in texts):
        raise ValueError("locked_append: every line is one text with no line break")
    lock = lock_of(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _take(lock)
    try:
        paths.append_lines(path, texts)
    finally:
        _let_go(lock)
    return len(texts)


def append(path, lines) -> int:
    """Append promise lines to the log under its lock (see the module docstring). Returns the lines written."""
    lines = list(lines or [])
    if not lines:
        return 0
    _not_from_a_worktree()
    path = Path(path)
    _place(path)
    return locked_append(path, [_checked(line) for line in lines])


# ── the clearing ──────────────────────────────────────────────────────────────

def _matcher(items):
    """match(code) -> the one Item the code names, or None (see THE CLEARING)."""
    by_code: dict[str, list] = {}
    by_model: dict[str, list] = {}
    for it in items or []:
        key = _code(it.code)
        if key:
            by_code.setdefault(key, []).append(it)
        model = leading_code(it.name)
        if model:
            by_model.setdefault(model, []).append(it)

    def match(code: str):
        if not code:
            return None
        hits = by_code.get(code)
        if hits:
            return hits[0] if len(hits) == 1 else None    # a code two items share: unmatched
        hits = by_model.get(code) or []
        return hits[0] if len(hits) == 1 else None

    return match


def fifo(items, promises, d, lead_of, late_days=LATE_DAYS) -> dict:
    """The open promise lines cleared by the main store's supplier receipts, first in, first out (see THE
    CLEARING in the module docstring)."""
    match = _matcher(items)
    lines: list[dict] = []
    per_item: dict[str, tuple] = {}                   # ledger name -> (Item, its lines)
    logged = unmatched = 0
    for ref, row in _newest(promises).items():
        if _closed(row):
            continue
        logged += 1
        qty, day = whole(row.get("qty")), _day(row.get("event_date"))
        if qty is None or day is None:
            unmatched += 1
            continue
        code, supplier = _code(row.get("item_code")), _text(row.get("supplier"))
        it = match(code)
        lead = lead_of(supplier)
        line = {"ref": ref, "supplier": supplier, "code": code,
                "item_name": it.name if it is not None else None,
                "ledger_name": it.ledger_name if it is not None else None,
                "date": day, "qty": qty, "received": ZERO, "due": qty,
                "expected_by": day + timedelta(days=int(lead)) if lead is not None else None,
                "late": False, "future": day > d}
        lines.append(line)
        if it is None:
            unmatched += 1
        else:
            per_item.setdefault(it.ledger_name, (it, []))[1].append(line)

    for it, group in per_item.values():
        group.sort(key=lambda x: (x["date"], x["ref"]))
        for r_day, _voucher, units, _value in sorted(it.receipts, key=lambda r: (r[0], str(r[1]))):
            left = fmt.dec(units)
            for line in group:                        # oldest first; only lines dated strictly before the receipt
                if left <= 0 or line["date"] >= r_day:
                    break
                take = min(left, line["due"])
                if take > 0:
                    line["received"] += take
                    line["due"] -= take
                    left -= take
            # what is left of the receipt pays no line: it is ignored

    on_order: dict[str, Decimal] = {}
    counted = 0
    for line in lines:
        if line["expected_by"] is not None:
            line["late"] = line["due"] > 0 and d > line["expected_by"] + timedelta(days=late_days)
        if line["ledger_name"] is not None and line["due"] > 0:
            counted += 1
            on_order[line["ledger_name"]] = on_order.get(line["ledger_name"], ZERO) + line["due"]
    lines.sort(key=lambda x: (x["date"], x["ref"]))
    return {"lines": lines, "on_order": on_order, "logged": logged, "counted": counted, "unmatched": unmatched,
            "log": promises is not None}
