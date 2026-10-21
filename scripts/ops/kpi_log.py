"""python -m scripts.ops kpi log <kind> --field value ...     (the evidence lines behind KPIs 2, 3, 4 and 7)

He says one sentence whenever he assesses a product, gets a quotation, agrees terms with a supplier, checks a
delivery, raises or settles a claim, or sends a report. Claude turns it into one line of the KPI log
(business_data/ops/kpi_log.jsonl), shows it, and logs it only after his yes. Without such a line the scorecard
(/yq-kpi, scripts.ops.kpi) reads NOT EVIDENCED, never MET.

    kpi log                                                  the kinds and the fields each one takes
    kpi log <kind> --field value ...                         the preview: the line, and the line that logs it
    kpi log <kind> --field value ... --commit --token <t>    logs it, only while it is still the line previewed
    kpi log sent weekly <saturday>  |  kpi log sent monthly <YYYY-MM>       a report he has sent (preview, commit)

THE LOG. One file, only ever appended to, shared with scripts.ops.stock_orders: its promise lines (goods on order,
kind 'promise') are its own, never written here, and stock.read_promises keeps only those. Both write through
stock_orders.locked_append, under the one lock beside the log (<log>.lock): a write while another command holds
it is refused in one sentence. Every line of the kinds here holds exactly KEYS:
    kind, ref, id, logged_at, event_date, state, source, note,
    check_types (a list), supplier, item_code, qty, amount_bhd, due_date, closed_date
A field the kind does not use is null (check_types: an empty list); every other value is text; dates are ISO.
id is 12 letters worked out from the line; logged_at is the moment of the yes (Bahrain time, to the second).

THE KINDS (the ref is made from the fields, so the same thing logged again has the same ref)
  candidate      a product assessed before it is ordered          cand:<NAME or CODE>
                 --ref <a short name> or --item-code <code>; --check-types, any of price, quality, demand,
                 competitor; --state open (assessing) | approved | ordered | rejected; --supplier, --qty, --note
  quote          a quotation from a supplier for a purchase       quote:<PURCHASE>:<supplier>
                 --ref <the receipt's MRN number, or the order's name> --supplier <name>; --amount-bhd, --qty,
                 --item-code, --due-date (the delivery day it promises), --note; --state received | chosen |
                 declined
  terms          cost, payment terms or warranty agreed           terms:<PURCHASE or GENERAL>:<supplier>
                 --supplier <name> --note <the terms, in words>; --ref <the purchase>, --due-date (the delivery
                 day promised), --amount-bhd, --item-code; --state agreed | asked | refused
  receipt_check  the incoming check of a supplier receipt         check:<MRN>
                 --ref <the MRN number> --qty <units counted> --check-types, any of quantity, quality, packaging;
                 --state done | issues; --supplier, --item-code, --note
  claim          a claim to a supplier                            claim:<supplier>:<WHAT>
                 --supplier <name> --ref <what it is for: the MRN, the item>; --item-code, --qty, --amount-bhd,
                 --due-date (the day it is to be settled), --closed-date, --note; --state raised | closed |
                 rejected (a closed or rejected claim without a closed date is closed on its event date)
  report         a report sent: only through `sent`              report:weekly:<saturday> | report:monthly:<YYYY-MM>
  Every kind takes --event-date (the day it happened, default today) and --source (default 'typed').
A name in a ref is written in capitals, its words joined by '-' ('Eid order' -> 'EID-ORDER'); an MRN number loses
the voucher prefix the ledger writes before it ('MRN:T-26-01-1' -> 'T-26-01-1'), so a receipt of the ledger and
the line about it carry one key (purchase_key); a supplier is written as stock_orders.slug writes it.

THE RULES
  * A later line with the same ref is appended, never written over: the scorecard counts distinct refs and reads
    every line of a ref (a candidate's checks add up; the newest line holds the ref's state).
  * The same line twice writes nothing: a line equal to the newest line of its ref but for its id, its logging time
    and its source is not logged again.
  * Refused in one plain sentence (LogInput, a ValueError the command prints as it is): a kind that is not one of
    KINDS; a field the kind does not take, or one it needs left out; a state or a check type the kind does not have;
    a date that is not written YYYY-MM-DD; an event or closing date after today; a quantity that is not a whole
    number, an amount that is not a number of BHD with at most three decimals; in any text, a run of eight or more
    digits with single spaces or hyphens between them (a phone or an ID number; an ISO date, an order number and an
    MRN number pass: id_like), an e-mail address, a character the command line cannot carry safely (" ` $ and the
    backslash), a word the wording guard (fmt.careful) refuses, more than its length allows; a report logged as sent
    that no build record shows was built (a monthly report: its final file, never a DRAFT), or on a day before it was
    built; a --commit from a code worktree (.claude/worktrees) without --data-root, before anything is written: the
    log belongs to the project folder (the rule of stock.day_file).
  * The token: 12 letters a to p from the sha256 of the line as it would be written, its id and its logging time
    aside. The preview prints the commit line with every field written out (the event date too), so a yes on a
    later day logs the same line; a field that changed between the preview and the yes gives another token, and
    the commit is refused.

CONTRACT (scripts.ops.kpi and the month workbook rely on it)
  log_file() -> Path                  business_data/ops/kpi_log.jsonl (the file stock.kpi_log_file() names)
  read_lines() -> list[dict] | None   every line of the log that reads as an object, of every kind, in file order;
                                      None when there is no log
  newest(lines, kind) -> {ref: line}       the newest line of each ref of that kind
  of_ref(lines, kind) -> {ref: [line]}     every line of each ref of that kind, in file order
  started(lines) -> date | None       the day of the first line logged (by its logging time)
  month_lines(month, lines=None)      the lines whose event date falls in the month 'YYYY-MM' (every kind)
  purchase_key(text) -> str           an MRN number or an order's name as a ref holds it
  id_like(text) -> bool               the text holds what could be a phone or an ID number (refused in every field)
  read_fields(kind, given, today) -> dict  the fields of a line, checked and written one way (refused as above)
  make_line(kind, fields, logged_at, ref=None) -> dict      the line itself (KEYS); ref only for a report
  token(line) -> str; plan(lines, line) -> "write" | "unchanged"
  records(kind, key) -> list[dict]    the build records of the weekly ('week', its Saturday) or the monthly
                                      ('month', 'YYYY-MM') workbook under exports/ops/*/, oldest build first; each
                                      with "_built" (the day it was built: built_at, else built_on) and "_file"
  built_day(record) -> date | None
  add_arguments(parser); given_fields(args) -> dict; run(args, words) -> dict      the command
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.ops import fmt, paths, status, stock_orders

COMMAND = "kpi"
KEYS = ("kind", "ref", "id", "logged_at", "event_date", "state", "source", "note", "check_types", "supplier",
        "item_code", "qty", "amount_bhd", "due_date", "closed_date")
# What he can give, each as --<field> <value> (the field's name with '-' for '_'), in the order the commit line
# writes them.
FIELDS = ("ref", "event_date", "state", "check_types", "supplier", "item_code", "qty", "amount_bhd", "due_date",
          "closed_date", "note", "source")
ALWAYS = ("event_date", "state", "source")           # every kind takes these
PROMISE = stock_orders.KIND                          # goods on order: stock_orders' own lines, never written here
TYPED = "typed"
GENERAL = "GENERAL"                                  # terms that are for no one purchase
WEEKLY, MONTHLY = "weekly", "monthly"
NOTE_MAX = 200                                       # a note is one short sentence
TEXT_MAX = 60                                        # a name, a code, a source
TOKEN_LEN = stock_orders.TOKEN_LEN
TOKEN_LETTERS = stock_orders.TOKEN_LETTERS
# a run of eight or more digits, single spaces allowed between them (app.ai_insights' phone pattern): a phone number,
# or an ID number; an order number (YQ-2609-0019) or a date (2026-10-21) survives it
ID_LIKE = re.compile(r"\+?\d(?: ?\d){7,}")
# ... and the same run with single hyphens between its digits too (3344-5566, 880-512-345), unless it is the tail of a
# number that a hyphen joins to what is before it (the order number YQ-2609-0019, the MRN number T-26-02-1); a date
# written YYYY-MM-DD is set aside first (id_like)
ID_JOINED = re.compile(r"(?<![0-9-])\+?\d(?:[ -]?\d){7,}")
DATE_IN_TEXT = re.compile(r"(?<![0-9-])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9-])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
UNSAFE = frozenset('"`$\\')                          # what a double-quoted value cannot carry in Bash and PowerShell
BARE = re.compile(r"[A-Za-z0-9_./:-]+")              # a value the commit line may write without quotes
ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
PREFIX = re.compile(r"^\s*[A-Za-z]+\s*:\s*")         # the voucher type the ledger writes before a number ('MRN:')
SATURDAY = 5
CLOCK = None            # tests: a function that returns the moment of logging (an aware datetime)


class LogInput(ValueError):
    """What was typed cannot be logged: one plain sentence, printed as it is."""


@dataclass(frozen=True)
class Kind:
    name: str
    what: str              # what a line of it records, as the preview says it
    prefix: str            # the first part of its ref
    states: tuple          # the first one is the default
    checks: tuple = ()     # the check types a line of it may carry
    needs: tuple = ()      # the fields it must be given
    takes: tuple = ()      # the other fields it may be given (ALWAYS aside)


KINDS: dict[str, Kind] = {
    "candidate": Kind("candidate", "a product assessed before it is ordered", "cand",
                      ("open", "approved", "ordered", "rejected"), ("price", "quality", "demand", "competitor"),
                      (), ("ref", "item_code", "check_types", "supplier", "qty", "note")),
    "quote": Kind("quote", "a quotation from a supplier, for a purchase", "quote", ("received", "chosen", "declined"),
                  (), ("ref", "supplier"), ("amount_bhd", "qty", "item_code", "due_date", "note")),
    "terms": Kind("terms", "cost, payment terms or warranty agreed with a supplier", "terms",
                  ("agreed", "asked", "refused"), (), ("supplier", "note"), ("ref", "due_date", "amount_bhd", "item_code")),
    "receipt_check": Kind("receipt_check", "the incoming check of a supplier receipt", "check", ("done", "issues"),
                          ("quantity", "quality", "packaging"), ("ref", "qty", "check_types"),
                          ("supplier", "item_code", "note")),
    "claim": Kind("claim", "a claim to a supplier", "claim", ("raised", "closed", "rejected"), (),
                  ("ref", "supplier"), ("item_code", "qty", "amount_bhd", "due_date", "closed_date", "note")),
    "report": Kind("report", "a report sent", "report", ("sent",), (), (), ("note",)),
}
CLOSED_CLAIM = ("closed", "rejected")
WORDS = {"ref": "ref", "event_date": "event date", "state": "state", "check_types": "check types",
         "supplier": "supplier", "item_code": "item code", "qty": "quantity", "amount_bhd": "amount",
         "due_date": "due date", "closed_date": "closed date", "note": "note", "source": "source"}


# ── small helpers ─────────────────────────────────────────────────────────────

def _option(field: str) -> str:
    return "--" + field.replace("_", "-")


def _now() -> datetime:
    return CLOCK() if CLOCK is not None else datetime.now(status.BAHRAIN)


def _stamp(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


def _letters(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return "".join(TOKEN_LETTERS[int(c, 16)] for c in digest[:TOKEN_LEN])


def _canon(line: dict, drop: tuple) -> str:
    return json.dumps({k: v for k, v in line.items() if k not in drop}, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def log_file() -> Path:
    """The KPI log: the same file stock.kpi_log_file() names (a test holds the two equal)."""
    return paths.ops_dir() / "kpi_log.jsonl"


def purchase_key(text) -> str:
    """An MRN number or an order's name as a ref holds it: the voucher prefix the ledger writes ('MRN:') dropped,
    letters and digits in capitals, each run joined to the next by '-' ('MRN:T-26-01-1' -> 'T-26-01-1')."""
    return stock_orders.slug(PREFIX.sub("", " ".join(str(text or "").split()))).upper()


def id_like(text) -> bool:
    """True when the text holds what could be a phone or an ID number: eight digits or more in one run, single spaces
    or hyphens between them (ID_LIKE, ID_JOINED). An ISO date and an order or MRN number pass."""
    plain = DATE_IN_TEXT.sub(" ", str(text or ""))
    return bool(ID_LIKE.search(plain) or ID_JOINED.search(plain))


def _kind(name) -> Kind:
    kind = KINDS.get(str(name or "").strip().lower())
    if kind is None or kind.name == "report":
        raise LogInput(f"'{str(name)[:30]}' is not a kind of the KPI log: say one of "
                       + ", ".join(k for k in KINDS if k != "report") + " (or `sent` for a report sent).")
    return kind


# ── the fields ────────────────────────────────────────────────────────────────

def _text(value, field: str, limit: int) -> str:
    """A text field as it is logged: its spaces collapsed, and refused when it could carry a person's number or
    address, would not survive the command line, or reads as an accusation."""
    text = " ".join(str(value).split())
    what = f"The {WORDS[field]}"
    if not text:
        raise LogInput(f"{what} was given empty: leave {_option(field)} out, or give it a value.")
    if text.startswith("-"):
        raise LogInput(f"{what} cannot start with '-': write it without the dash.")
    if UNSAFE & set(text):
        raise LogInput(f"{what} holds a character the command line cannot carry safely (\" ` $ or a backslash): "
                       "write it without.")
    if id_like(text):
        raise LogInput(f"{what} holds a run of eight or more digits (a phone or an ID number?): the log keeps none.")
    if EMAIL.search(text):
        raise LogInput(f"{what} holds an e-mail address: the log keeps none.")
    if len(text) > limit:
        raise LogInput(f"{what} is longer than {limit} characters: keep it short.")
    try:
        fmt.careful(text)
    except ValueError:
        raise LogInput(f"{what} uses a word the log never uses about a voucher or a person: say it another way "
                       "(what differs, 'to check').") from None
    return text


def _day(value, field: str) -> date:
    text = str(value).strip()
    try:
        day = date.fromisoformat(text) if ISO_DAY.fullmatch(text) else None
    except ValueError:
        day = None
    if day is None:
        raise LogInput(f"The {WORDS[field]} is written YYYY-MM-DD: '{text[:20]}' is not one.")
    return day


def _whole(value) -> str:
    text = str(value).strip()
    plain = text.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", text) else text
    try:
        v = Decimal(plain)
    except InvalidOperation:
        v = None
    if v is None or not v.is_finite() or v < 0 or v != v.to_integral_value():
        raise LogInput(f"The quantity '{text[:20]}' is not a whole number of units, nought or more.")
    return str(int(v))


def _amount(value) -> str:
    text = str(value).strip()
    plain = text.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text) else text
    try:
        v = Decimal(plain)
    except InvalidOperation:
        v = None
    if v is None or not v.is_finite() or v < 0 or -v.as_tuple().exponent > 3:
        raise LogInput(f"The amount '{text[:20]}' is not a number of BHD (at most three decimals, nought or more).")
    return format(v.normalize(), "f") if v != v.to_integral_value() else str(int(v))


def _checks(value, kind: Kind) -> list[str]:
    words = [w for w in re.split(r"[\s,;/]+", str(value).strip().lower()) if w]
    if not words:
        raise LogInput("Check types were given empty: name them, for example price,quality.")
    bad = [w for w in words if w not in kind.checks]
    if bad:
        raise LogInput(f"A {kind.name} has no check type '{bad[0][:20]}': it takes " + ", ".join(kind.checks) + ".")
    return [c for c in kind.checks if c in words]           # each once, in the kind's own order


def given_fields(args) -> dict:
    """The fields given on the command line ({field: the text as typed}), those not given left out."""
    return {f: getattr(args, f) for f in FIELDS if getattr(args, f, None) is not None}


def read_fields(kind_name, given: dict, today: date) -> dict:
    """The fields of one line, each checked and written one way: text collapsed, dates ISO, a state folded, check
    types in the kind's order, a quantity and an amount in their shortest form; the event date today when it is not
    given. Refused in one plain sentence (see the module docstring)."""
    kind = KINDS["report"] if kind_name == "report" else _kind(kind_name)
    given = {k: v for k, v in (given or {}).items() if v is not None}
    allowed = set(ALWAYS) | set(kind.needs) | set(kind.takes)
    for field in given:
        if field not in allowed:
            raise LogInput(f"A {kind.name} takes no {WORDS.get(field, field)} ({_option(field)}): leave it out.")
    for field in kind.needs:
        if field not in given:
            raise LogInput(f"A {kind.name} needs its {WORDS[field]}: give {_option(field)}.")
    if kind.name == "candidate" and "ref" not in given and "item_code" not in given:
        raise LogInput("A candidate is named by --ref (a short name) or --item-code: give one of them.")
    out: dict = {}
    for field, value in given.items():
        if field in ("event_date", "due_date", "closed_date"):
            out[field] = _day(value, field).isoformat()
        elif field == "state":
            state = str(value).strip().lower()
            if state not in kind.states:
                raise LogInput(f"A {kind.name} has no state '{state[:20]}': it is " + " or ".join(kind.states) + ".")
            out[field] = state
        elif field == "check_types":
            out[field] = _checks(value, kind)
        elif field == "qty":
            out[field] = _whole(value)
        elif field == "amount_bhd":
            out[field] = _amount(value)
        elif field == "item_code":
            out[field] = _text(value, field, TEXT_MAX).upper()
        elif field == "note":
            out[field] = _text(value, field, NOTE_MAX)
        else:
            out[field] = _text(value, field, TEXT_MAX)
    out.setdefault("event_date", today.isoformat())
    out.setdefault("state", kind.states[0])
    out.setdefault("source", TYPED)
    for field in ("event_date", "closed_date"):
        if field in out and date.fromisoformat(out[field]) > today:
            raise LogInput(f"The {WORDS[field]} {out[field]} is after today ({today.isoformat()}): a line is "
                           "logged once it has happened.")
    if "supplier" in out and not stock_orders.slug(out["supplier"]):
        raise LogInput(f"The supplier '{out['supplier'][:30]}' has no letter or digit to log it under.")
    if "ref" in out and not purchase_key(out["ref"]):
        raise LogInput(f"The ref '{out['ref'][:30]}' has no letter or digit to log it under.")
    if kind.name == "receipt_check" and not out.get("check_types"):
        raise LogInput("A receipt check names what was checked: --check-types, any of " + ", ".join(kind.checks) + ".")
    if kind.name == "claim":
        if out["state"] in CLOSED_CLAIM:
            out.setdefault("closed_date", out["event_date"])
        elif "closed_date" in out:
            raise LogInput("A claim still raised has no closed date: give --state closed (or rejected) with it.")
    return out


def _ref(kind: Kind, fields: dict) -> str:
    if kind.name == "candidate":
        return f"cand:{purchase_key(fields['ref']) if fields.get('ref') else purchase_key(fields['item_code'])}"
    if kind.name == "quote":
        return f"quote:{purchase_key(fields['ref'])}:{stock_orders.slug(fields['supplier'])}"
    if kind.name == "terms":
        purchase = purchase_key(fields["ref"]) if fields.get("ref") else GENERAL
        return f"terms:{purchase}:{stock_orders.slug(fields['supplier'])}"
    if kind.name == "receipt_check":
        return f"check:{purchase_key(fields['ref'])}"
    return f"claim:{stock_orders.slug(fields['supplier'])}:{purchase_key(fields['ref'])}"


def make_line(kind_name, fields: dict, logged_at, ref: str | None = None) -> dict:
    """One line of the log, with exactly KEYS, from fields read_fields() gave (a report's ref is handed in)."""
    kind = KINDS[kind_name] if kind_name in KINDS else _kind(kind_name)
    moment = logged_at if isinstance(logged_at, datetime) else datetime.fromisoformat(str(logged_at))
    line = {k: None for k in KEYS}
    line.update({k: v for k, v in fields.items() if k in KEYS})
    line["check_types"] = list(fields.get("check_types") or [])
    line["kind"] = kind.name
    line["ref"] = ref if kind.name == "report" else _ref(kind, fields)
    line["logged_at"] = _stamp(moment)
    line["id"] = None
    line["id"] = _letters(_canon(line, ("id",)))
    return line


def token(line: dict) -> str:
    """12 letters a to p from the line as it would be written, its id and its logging time aside."""
    return _letters(_canon(line, ("id", "logged_at")))


# ── the log ───────────────────────────────────────────────────────────────────

def read_lines() -> list[dict] | None:
    """Every line of the log that reads as an object, of every kind, in file order; None when there is no log."""
    f = log_file()
    if not f.is_file():
        return None
    out: list[dict] = []
    for text in f.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(text)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def of_ref(lines, kind: str) -> dict[str, list[dict]]:
    """{ref: every line of that ref, in file order} of one kind."""
    out: dict[str, list[dict]] = {}
    for row in lines or []:
        if isinstance(row, dict) and row.get("kind") == kind and row.get("ref"):
            out.setdefault(str(row["ref"]), []).append(row)
    return out


def newest(lines, kind: str) -> dict[str, dict]:
    """{ref: its newest line} of one kind (a later line of a ref is its state)."""
    return {ref: rows[-1] for ref, rows in of_ref(lines, kind).items()}


def started(lines) -> date | None:
    """The day the log started: the earliest logging time of any line, of any kind."""
    days = [fmt.as_date(row.get("logged_at")) for row in lines or [] if isinstance(row, dict)]
    days = [d for d in days if d is not None]
    return min(days) if days else None


def month_lines(month: str, lines=None) -> list[dict]:
    """The lines whose event date falls in the month 'YYYY-MM', every kind, in file order."""
    if not MONTH.fullmatch(str(month or "")):
        raise ValueError(f"a month is written YYYY-MM, not {month!r}")
    rows = read_lines() if lines is None else lines
    return [r for r in rows or [] if isinstance(r, dict) and str(r.get("event_date") or "")[:7] == month]


def plan(lines, line: dict) -> str:
    """'unchanged' when the newest line of the same ref (and kind) is this line but for its id, logging time and
    source; else 'write'."""
    old = newest(lines, line["kind"]).get(line["ref"])
    if old is not None and set(old) == set(line) and \
            _canon(old, ("id", "logged_at", "source")) == _canon(line, ("id", "logged_at", "source")):
        return "unchanged"
    return "write"


# ── the build records (the evidence that a report was prepared) ───────────────

def built_day(record: dict) -> date | None:
    """The day a workbook was built: its record's built_at (the time of the build), else its built_on."""
    for key in ("built_at", "built_on"):
        day = fmt.as_date(record.get(key)) if isinstance(record, dict) else None
        if day is not None:
            return day
    return None


def records(kind: str, key: str) -> list[dict]:
    """The build records of one workbook (see the module docstring), oldest build first. A record whose own month or
    week names another one is passed over; so is a commentary or facts file beside it."""
    if kind not in ("week", "month"):
        raise ValueError("records: kind is 'week' or 'month'")
    base = paths.exports_dir()
    if not base.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(base.glob(f"*/report-{kind}-{key}*.json")):
        if f.name.endswith((".facts.json", ".commentary.json")):
            continue
        stem = f.name[len(f"report-{kind}-"):]
        if not (stem == f"{key}.json" or stem.startswith(f"{key}.")):
            continue                                          # report-month-2026-1 must not take 2026-10
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        own = rec.get("month") if kind == "month" else (rec.get("week_ending") or rec.get("saturday"))
        if isinstance(own, str) and (MONTH.fullmatch(own) or ISO_DAY.fullmatch(own)) and own != key:
            continue                                          # a record that names another period
        out.append(dict(rec, _built=built_day(rec), _file=f"{f.parent.name}/{f.name}"))
    return sorted(out, key=lambda r: (r["_built"] or date.min, r["_file"]))


def all_records(kind: str) -> list[dict]:
    """Every build record of the weekly ('week') or the monthly ('month') workbooks, oldest build first."""
    base = paths.exports_dir()
    if kind not in ("week", "month") or not base.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(base.glob(f"*/report-{kind}-*.json")):
        if f.name.endswith((".facts.json", ".commentary.json")):
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append(dict(rec, _built=built_day(rec), _file=f"{f.parent.name}/{f.name}"))
    return sorted(out, key=lambda r: (r["_built"] or date.min, r["_file"]))


# ── the command ───────────────────────────────────────────────────────────────

def add_arguments(parser) -> None:
    """The fields of a line, --commit and --token (the kpi command's parser carries them)."""
    for field in FIELDS:
        parser.add_argument(_option(field), dest=field, default=None, metavar="VALUE",
                            help=f"kpi log: {WORDS[field]}")
    parser.add_argument("--commit", action="store_true", help="kpi log: log the line the preview showed (with --token)")
    parser.add_argument("--token", default=None, metavar="TOKEN", help="kpi log: the token the preview printed")


def _arg(value: str) -> str:
    return value if BARE.fullmatch(value) else f'"{value}"'


def _commit_line(words: list[str], fields: dict, tok: str) -> str:
    parts = ["python -m scripts.ops kpi log"] + [_arg(w) for w in words]
    for field in FIELDS:
        value = fields.get(field)
        if value is None or (field == "source" and value == TYPED):
            continue
        text = ",".join(value) if isinstance(value, list) else str(value)
        parts.append(f"{_option(field)} {_arg(text)}")
    return " ".join(parts + ["--commit", "--token", tok])


def _describe(line: dict) -> str:
    """The line in words, field by field (the preview's second line)."""
    bits = [f"event {fmt.day_long(line['event_date'])}", f"state {line['state']}"]
    if line["check_types"]:
        bits.append("checks " + ", ".join(line["check_types"]))
    for field, words in (("supplier", "supplier"), ("item_code", "item"), ("qty", "units"),
                         ("amount_bhd", "BHD"), ("due_date", "due"), ("closed_date", "closed")):
        if line.get(field) is not None:
            value = line[field]
            if field in ("due_date", "closed_date"):
                value = fmt.day_long(value)
            bits.append(f"{words} {value}")
    bits.append(f"source {line['source']}")
    return "; ".join(bits)


def _check_summary(lines, line: dict) -> list[str]:
    """For a candidate or a receipt check: what the ref has on file once this line is in, and what is still to log."""
    kind = KINDS[line["kind"]]
    if not kind.checks:
        return []
    done = {c for row in of_ref(lines, kind.name).get(line["ref"], []) for c in (row.get("check_types") or [])}
    done |= set(line["check_types"])
    have = [c for c in kind.checks if c in done]
    left = [c for c in kind.checks if c not in done]
    text = f"  On file for {line['ref']} with this line: " + (", ".join(have) if have else "no check yet")
    return [text + (f" (still to log: {', '.join(left)})." if left else " (all of them).")]


def _help() -> list[str]:
    lines = ["KPI LOG  one line for each thing done, logged only after his yes: python -m scripts.ops kpi log "
             "<kind> --field value ..."]
    for kind in KINDS.values():
        if kind.name == "report":
            continue
        needs = " ".join(f"{_option(f)}" for f in kind.needs)
        takes = ", ".join(_option(f) for f in kind.takes)
        extra = f"; check types {', '.join(kind.checks)}" if kind.checks else ""
        lines.append(f"  {kind.name:14} {kind.what}: needs {needs or '--ref or --item-code'}; takes {takes}; "
                     f"states {' | '.join(kind.states)}{extra}")
    lines.append("  sent weekly <saturday> | sent monthly <YYYY-MM>   a report sent (it must have been built)")
    lines.append("  Every kind takes --event-date (the day it happened, default today) and --source (default typed).")
    return lines


def _sent(words: list[str], given: dict, today: date) -> tuple[str, dict]:
    """(the report's ref, its fields) for `sent weekly <saturday>` / `sent monthly <YYYY-MM>`, refused when no build
    record shows the report was built (a monthly report: its final file, never a DRAFT, the rule KPI 7 counts by), or
    when it is said to have gone out before that."""
    if len(words) != 2 or words[0].lower() not in (WEEKLY, MONTHLY):
        raise LogInput("Say which report was sent: kpi log sent weekly <saturday> (YYYY-MM-DD) or kpi log sent "
                       "monthly <YYYY-MM>.")
    which, key = words[0].lower(), words[1].strip()
    if which == WEEKLY:
        try:
            sat = date.fromisoformat(key) if ISO_DAY.fullmatch(key) else None
        except ValueError:
            sat = None
        if sat is None or sat.weekday() != SATURDAY:
            raise LogInput(f"A week is named by the Saturday it ends on, written YYYY-MM-DD: '{key[:20]}' is not one.")
        key, recs = sat.isoformat(), records("week", sat.isoformat())
        what = f"the weekly report for the week ending {fmt.day_long(sat)}"
        builder = "/yq-week builds it"
    else:
        if not MONTH.fullmatch(key):
            raise LogInput(f"A month is written YYYY-MM: '{key[:20]}' is not one.")
        recs = records("month", key)
        what = f"the monthly report of {key}"
        builder = "/yq-month builds it"
    if not recs:
        raise LogInput(f"No build record shows that {what} was built: {builder}, and only a built report is logged as "
                       "sent.")
    if which == MONTHLY:
        recs = [r for r in recs if r.get("draft") is False]
        if not recs:
            raise LogInput(f"Only a DRAFT of {what} was built: a monthly report is logged as sent once its final file is "
                           "made (on the approval of its comment lines), never a DRAFT.")
    fields = read_fields("report", given, today)
    built = min((r["_built"] for r in recs if r["_built"] is not None), default=None)
    sent_on = date.fromisoformat(fields["event_date"])
    if built is not None and sent_on < built:
        raise LogInput(f"{what[0].upper() + what[1:]} was built on {built.isoformat()}: it cannot have been sent on "
                       f"{sent_on.isoformat()}, before that.")
    return f"report:{which}:{key}", fields


def run(args, words: list[str]) -> dict:
    """kpi log ...: the preview of one line, or (with --commit and its token) the line logged."""
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    try:
        lines = _run(args, words, out)
    except LogInput as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    except RuntimeError as exc:
        if str(exc) != stock_orders.LOCK_BUSY:
            raise
        return dict(out, ok=False, text=f"Nothing was logged: {exc}.", exit=1)
    return dict(out, text="\n".join(_guarded(ln).rstrip() for ln in lines))


def _refused(word: str) -> bool:
    try:
        fmt.careful(word)
    except ValueError:
        return True
    return False


def _guarded(line: str) -> str:
    """Every line shown passes the wording guard (fmt.careful). Every text he gives is checked against it before it
    is logged, so a word it refuses can come only from an older line: it is blanked, never printed."""
    try:
        return fmt.careful(line)
    except ValueError:
        return " ".join("-" * len(w) if _refused(w) else w for w in line.split(" "))


def _run(args, words: list[str], out: dict) -> list[str]:
    words = [str(w) for w in words or []]
    commit, tok = bool(getattr(args, "commit", False)), getattr(args, "token", None)
    given = given_fields(args)
    if not words:
        if given or commit or tok:
            raise LogInput("Say what to log first: kpi log <kind> --field value ... (kpi log alone lists the kinds).")
        return _help()
    if tok is not None and not commit:
        raise LogInput("--token goes with --commit: run the line the preview printed, as it was printed.")
    if commit and not tok:
        raise LogInput("A line is logged with the token its preview printed: run the preview first, without --commit.")
    if commit and paths.is_worktree() and not paths.root_is_set():
        # a code worktree reads the main folder's data; its unfinished code never writes the owner's log (the rule of
        # stock.day_file): a line would land in the project folder's KPI log
        raise LogInput("Nothing was logged: this is a code worktree, and the KPI log belongs to the project folder (to "
                       "try it, name another folder with --data-root).")
    moment = _now()
    today = moment.date()
    if words[0].lower() == "sent":
        ref, fields = _sent(words[1:], given, today)
        kind_name, shown_words = "report", ["sent"] + [w.lower() for w in words[1:2]] + [ref.rsplit(":", 1)[1]]
    else:
        if len(words) > 1:
            raise LogInput(f"One kind at a time, with its fields as options: '{' '.join(words[1:])[:40]}' is not a "
                           "field (write --ref, --note ...).")
        kind_name, ref, shown_words = _kind(words[0]).name, None, [words[0].lower()]
        fields = read_fields(kind_name, given, today)
    line = make_line(kind_name, fields, moment, ref=ref)
    tok_now = token(line)
    existing = read_lines() or []
    kind = KINDS[kind_name]
    head = [f"KPI LOG  {kind.what}: {line['ref']}", "  " + _describe(line)]
    if line.get("note"):
        head.append(f"  Note: {line['note']}")
    head += _check_summary(existing, line)
    out["flags"] = {"log": kind_name, "ref": line["ref"], "token": tok_now, "logged": 0}
    if plan(existing, line) == "unchanged":
        return head + ["  Nothing to log: the KPI log already holds this line as it is."]
    if commit:
        if tok != tok_now:
            raise LogInput("The line changed since the preview: nothing was logged. Run the preview again and log what "
                           "it shows.")
        n = stock_orders.locked_append(log_file(), [json.dumps(line, ensure_ascii=False, sort_keys=True)])
        out["flags"]["logged"] = n
        return head + [f"  Logged: {n} line in the KPI log. The scorecard counts it from now on (/yq-kpi)."]
    return head + ["  Nothing is logged yet. Once he says yes, this logs exactly this line:",
                   "    " + _commit_line(shown_words, fields, tok_now)]
