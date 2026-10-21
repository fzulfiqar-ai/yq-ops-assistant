"""python -m scripts.ops watch [all | <id> | explain <id> <words> | done <id> [words] | replay]
(the command behind /yq-watch)

    (nothing)      the action list after a load: the movement items open on the register, ACT before CHECK,
                   then the larger BHD, then the older; under them, per rule, how many sit under the floor;
                   then, read-only, the open items of the load check and of the Command Centre, the BHD of a
                   changed invoice or stock voucher worked out now; on the first screen only, the BASELINE
                   and the THRESHOLDS; the line on the two rules still to come; how to explain an item
    all            every open item: the baseline and the items under the floor too
    <id>           one item in full (its id, A12, or its whole key)
    explain <id> <his words>   the item stays off the list while it is what he explained
    done <id> [his words]      the item rests until its facts change
    replay         the thresholds again, and how the 90-day replay worked them out

THE FIVE MOVEMENT RULES (MOVE_RULES). The movement audit (scripts.ops.moves) pairs every Stock Issue Voucher
with its Stock Receive Voucher by what they hold and hands over what is left to check, one exception per
issue (or per receipt), valued at the /yq-stock cost basis. Each kind is one rule of the register, keyed on
a Focus number, never on a name:
    not_received        xfer_open|<SIO>               not received after its deadline
    damage_not_booked   xfer_open|damage|<YYYY-MM>    sent to the damage store and not booked in there: one item
                                                     per month of issue, its SIO numbers the evidence
    twins               xfer_twins|<first SIO>        identical issues, not all received ("x of N ... SIO a, b,
                                                     c"): the receipts cannot say which, so none is named
    qty_differs         qty_diff|<SIO>                received with other quantities (the lines in the detail)
    elsewhere           xfer_elsewhere|<SIO>          received in another store than the one it was sent to
    orphan              rcpt_orphan|<STRV>            a receipt with no issue (possible matches in the detail,
                                                     never in the evidence: a new guess is no new voucher)
Every item is CHECK; it reads ACT at watch.act_bhd and above, and (the kinds that wait for a receipt: not
received, the damage store's month, twins) when it is more than watch.act_days working days past its
deadline. Such an issue is listed only once it is out of grace, as pairing.within_grace reads it: the day
after its deadline, or to the damage store the deadline day itself; one still inside it is not late,
whatever the audit hands over. A title holds no count and no age and a detail no age, so an item does not
change while it stays what it is (the register compares level, title, detail, BHD and evidence); 'who' and
'due' stay empty: the e-mail's playbook names the owner by the rule. A store is named in the detail only
(alerts.jsonl is a local file). Of twins the audit's `open` (how many of the N have no receipt) is the item's
count.

ONE RECORDER. record_moves is the one place that records these rules (tests/test_ops_watch.py walks the
package for another): it records EVERY rule on every run, an empty list when nothing is found, because the
register marks a rule's open items cleared when a run no longer finds them. A second recorder would clear
the first one's items. Each rule's first run is its baseline (counted once, not listed); an older item that
later names a voucher it did not name (a new issue in the damage store's month, a new member of a set of
identical issues) is listed from then on, like a new one (alerts.record). The load check's rules
(inv_changed, voucher_changed, price_scope, profit_held) and the Command Centre's (attn) are never recorded
here: this screen shows them read-only.

ONCE PER LEDGER. ensure_recorded() records the movement rules once per stock ledger: the sha256 of the
ledger file, or 'db:<last day>' when the audit read the portal's database, kept in state.json under
'moves_recorded'. The delivery step calls it before the brief, the full brief calls it, and /yq-watch does;
the second call is a no-op (when the loaded folder's file is the one recorded, it does not even run the
audit). It never raises: a failure is one sentence in its answer, so it cannot stop the e-mail. A ledger
that is not proven equal to production (the audit's per_store_ok) records nothing.

THE THRESHOLDS. watch.floor_bhd, watch.act_bhd and watch.act_days in config.json win, each by itself.
Where one is absent, the figure the 90-day replay worked out stands in: replay_defaults goes over the issues
of the REPLAY_DAYS days to the ledger's last day and takes
    act_days   the smallest N for which at most LATE_RECEIPTS_MAX receipts came more than N working days
               after their issue's deadline;
    floor_bhd  the BHD of the FLOOR_RANK-th largest item the register raised over those days (0 with fewer):
               the items of the audit's exceptions of those days, and the transfers that came after their
               deadline (each was open on the register until its receipt), valued at cost;
    act_bhd    the ACT_PERCENTILE-th percentile (nearest rank) of the same BHD figures.
They are worked out once, kept in state.json under 'watch_defaults' with their basis, and printed once (with
the BASELINE, on the first screen); `replay` prints them again. A movement item under the floor is counted
under it, not listed, here and on the brief's Do today (alerts.open_items(floor=..., floor_rules=MOVE_RULES));
an item with no BHD is never under it.

BHD AT DISPLAY TIME. A changed stock voucher (voucher_changed|<voucher>|<item>) is worth the ledger's units of
that voucher and item at cost; a changed invoice (inv_changed|<invoice>) its Sales Invoice lines in the ledger
at cost (the export writes 'XX : 12' where the ledger writes 'XX:12', so the number is compared without its
spaces). Worked out for the screen, never stored; 'gone' when the voucher or the invoice is no longer in the
data.

The screen and its facts go to exports/ops/<ledger day>/watch*.txt and .json (sales.save_screen). Every line
passes the wording guard (fmt.careful; a word from the data it refuses is blanked): what differs is 'to
check', never an accusation. The counts in this file are the plan's (90 days, 13 receipts, the 66th-largest,
the 90th percentile, three lines of a voucher in a detail); the owner's thresholds are config.json's.
"""
from __future__ import annotations

import dataclasses
import importlib
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from scripts.ops import alerts, config, fmt, ledger, loadplan, pairing, status

COMMAND = "watch"
MOVE_RULES = ("xfer_open", "xfer_twins", "qty_diff", "xfer_elsewhere", "rcpt_orphan")
KIND_RULE = {"not_received": "xfer_open", "damage_not_booked": "xfer_open", "twins": "xfer_twins",
             "qty_differs": "qty_diff", "elsewhere": "xfer_elsewhere", "orphan": "rcpt_orphan"}
WAITING = ("not_received", "damage_not_booked", "twins")     # the kinds that wait for a receipt: a deadline and an age
# shown read-only, recorded elsewhere: load_check.EVENT_RULES + STATE_RULES, and sales.ATTN_RULE
LOAD_CHECK_RULES = ("inv_changed", "voucher_changed", "price_scope", "profit_held")
COMMAND_CENTRE_RULES = ("attn",)
VALUED_NOW = ("voucher_changed", "inv_changed")      # their BHD is worked out at display time
LIMIT_KEYS = ("floor_bhd", "act_bhd", "act_days")
REPLAY_DAYS = 90              # the replay goes over the issues of these days to the ledger's last day
LATE_RECEIPTS_MAX = 13        # act_days: at most this many receipts came later than it
FLOOR_RANK = 66               # floor_bhd: the BHD of this item, counted from the largest
ACT_PERCENTILE = 90           # act_bhd: this percentile of the same figures
LINES_SHOWN = 3               # a voucher's lines named in a detail
COST_DP = Decimal("0.000001")  # a unit's cost held to six decimals, as the movement audit holds it (moves.COST_DP)
STATE_RECORDED = "moves_recorded"
STATE_DEFAULTS = "watch_defaults"
STATE_SHOWN = "watch_baseline_shown"
_ID = re.compile(r"^[Aa]\d+$")
_IN_FORCE = object()          # movement_items: the floor in force (thresholds())

TITLES = {
    "not_received": "Transfer not received after its deadline: to check",
    "damage": "Sent to the damage store and not booked in there: to check",
    "twins": "Identical transfers, not all received: to check",
    "qty_differs": "Transfer received with other quantities: to check",
    "elsewhere": "Transfer received in another store: to check",
    "orphan": "Receipt with no issue found: to check",
}
RULE_WORDS = {
    "xfer_open": "not received after the deadline",
    "xfer_twins": "identical transfers, not all received",
    "qty_diff": "received with other quantities",
    "xfer_elsewhere": "received in another store",
    "rcpt_orphan": "receipts with no issue found",
    "inv_changed": "invoices changed after they were loaded",
    "voucher_changed": "stock vouchers changed after they were loaded",
    "price_scope": "a van's or a customer's price below the general one",
    "profit_held": "the profitability report held back",
    "attn": "the Command Centre's items",
}
HOOKS = ("Not checked yet: sold below cost ({profit}) and sales returns against last month's kickback tier "
         "(with the month-end Ledger export); both come with the month-end release.")
HOW_TO = ("To explain an item: /yq-watch explain <id> <your words> (it then stays off the list while it is what "
          "you explained). When one is done: /yq-watch done <id>. One item in full: /yq-watch <id>.")
USAGE = ("Type /yq-watch, /yq-watch all, /yq-watch <id>, /yq-watch explain <id> <your words>, "
         "/yq-watch done <id> [words] or /yq-watch replay.")
NO_ITEM = "No item {ident} on the action list. /yq-watch lists the open items; /yq-watch all every one."


class WatchInput(ValueError):
    """What was typed cannot be read. The message is the plain sentence shown."""


class NotProven(ValueError):
    """The stock ledger the audit read is not proven equal to what production holds: nothing is recorded."""


# ── small helpers ─────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get(obj, name, default=None):
    """An attribute of the audit (or of a part of it), whether it is an object or a mapping."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _source(audit) -> dict:
    s = _get(audit, "source")
    return dict(s) if isinstance(s, Mapping) else {}


def _number_of(x) -> str | None:
    """A voucher number: the text itself, or the `number` (else `no`, the pairing's own leg, else `voucher`) of a
    leg."""
    if x is None:
        return None
    if isinstance(x, str):
        return x.strip() or None
    for name in ("number", "no", "voucher"):
        v = _get(x, name)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _ref(number) -> str:
    """A Focus number as it can stand in a register key (load_check's own rule: '|' becomes '/')."""
    return str(number).replace("|", "/")


def _store(name) -> str:
    return " ".join(str(name or "").split()) or "(no store named)"


def _day(x) -> str:
    return fmt.day(x) if fmt.as_date(x) is not None else "n/a"


def _bhd(x) -> Decimal | None:
    if x is None or x == "":
        return None
    try:
        v = Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return fmt.q3(v) if v.is_finite() else None


def _listed(x) -> list:
    """A list the audit gives (voucher numbers, possible matches), also when it gives one thing alone."""
    if x is None:
        return []
    if isinstance(x, (str, Mapping)):
        return [x]
    return list(x)


def _pairs(lines) -> list[tuple[str, Decimal]]:
    """A voucher's lines as [(item, units)], from a list of pairs (or of {item, units}) or an {item: units}
    mapping."""
    if isinstance(lines, Mapping):
        lines = list(lines.items())
    out = []
    for ln in lines or []:
        if isinstance(ln, Mapping):
            ln = (ln.get("item"), ln.get("units"))
        if isinstance(ln, (tuple, list)) and len(ln) >= 2 and ln[0] is not None:
            out.append((str(ln[0]), fmt.dec(ln[1])))
    return out


def _as_dict(raw) -> dict:
    """One exception of the audit as a dict (the contract's form; an object's fields are read the same way)."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if dataclasses.is_dataclass(raw) and not isinstance(raw, type):
        return {f.name: getattr(raw, f.name) for f in dataclasses.fields(raw)}
    return dict(vars(raw)) if hasattr(raw, "__dict__") else {}


def _lines_text(lines) -> str:
    rows = _pairs(lines)
    if not rows:
        return "no lines"
    shown = [f"{' '.join(item.split())} ({fmt.units(units)})" for item, units in rows[:LINES_SHOWN]]
    more = len(rows) - LINES_SHOWN
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def _careful(text: str, fallback: str) -> str:
    """A detail carries store and item names from the data; one that holds a word the wording guard refuses
    is replaced by the plain fallback (numbers and dates only), so one odd name never stops the recording."""
    try:
        return fmt.careful(text)
    except ValueError:
        return fmt.careful(fallback)


def _guarded(line: str) -> str:
    """Every line shown passes the wording guard (fmt.careful). A word from the data (or of his) that it
    refuses is no accusation, but the guard cannot know: the word is blanked, never printed (sales._guarded's
    rule)."""
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


def _cut(text, width: int = alerts.WHAT_WIDTH) -> str:
    t = " ".join(str(text or "").split())
    if len(t) <= width:
        return t
    cut = t[:width - 3].rsplit(" ", 1)[0] if " " in t[:width - 3] else t[:width - 3]
    return cut.rstrip(" ,.:;-") + "..."


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{fmt.units(n)} {one if n == 1 else (many or one + 's')}"


def working_days_after(start, end, rules: pairing.Rules | None = None) -> int:
    """The working days after `start` up to and including `end` (the rules' weekend and holidays skipped);
    0 when `end` is not after `start` or either is no date."""
    rules = rules or pairing.Rules()
    a, b = fmt.as_date(start), fmt.as_date(end)
    if a is None or b is None or b <= a:
        return 0
    off = frozenset(rules.weekend)
    n, day = 0, a
    while day < b:
        day += timedelta(days=1)
        if day.weekday() not in off and day not in rules.holidays:
            n += 1
    return n


def _level(bhd: Decimal | None, late: int | None, limits: Mapping) -> str:
    """CHECK, or ACT at act_bhd and above, or when more than act_days working days past the deadline."""
    act_bhd, act_days = limits.get("act_bhd"), limits.get("act_days")
    if bhd is not None and act_bhd is not None and bhd >= Decimal(str(act_bhd)):
        return "ACT"
    if late is not None and act_days is not None and late > int(act_days):
        return "ACT"
    return "CHECK"


def _cost(costs, item: str) -> Decimal | None:
    """One ledger item's cost per unit, held to six decimals as the movement audit holds it (moves._cost6): only a
    total is rounded to the fils, never a unit's cost, so a figure here is the one /yq-moves shows. The audit
    gives a number; an Item (.cost_bhd, else stock's .cost) or a mapping is read the same way."""
    c = (costs or {}).get(item)
    if c is None:
        return None
    if isinstance(c, (int, float, Decimal, str)):
        value = c
    else:
        value = _get(c, "cost_bhd")
        if value is None:
            value = _get(c, "cost")
    if value is None:
        return None
    try:
        unit = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return unit.quantize(COST_DP, rounding=ROUND_HALF_UP) if unit.is_finite() else None


def _value(lines, costs) -> Decimal | None:
    """Units at cost over the lines that have one; None when none has."""
    total, priced = Decimal(0), False
    for item, units in _pairs(lines):
        c = _cost(costs, item)
        if c is not None:
            total += units * c
            priced = True
    return fmt.q3(total) if priced else None


# ── the exceptions as register items ──────────────────────────────────────────

def _item(rule: str, ref: tuple, level: str, title: str, detail: str, fallback: str, bhd, evidence,
          count=None) -> dict:
    return {"key": alerts.make_key(rule, *ref), "level": level, "title": fmt.careful(title),
            "detail": _careful(detail, fallback), "who": None, "due": None, "bhd": bhd, "count": count,
            "evidence": [e for e in dict.fromkeys(evidence) if e]}


def _issues(exc: Mapping) -> list[str]:
    nums = [_number_of(x) for x in _listed(exc.get("issues"))]
    return sorted({n for n in nums if n}, key=pairing.parse_number)


def _in_grace(exc: Mapping, d) -> bool:
    """An issue still inside its deadline is not late yet: never 'not received after its deadline', whatever the
    audit hands over. Read as pairing.within_grace reads it: to the damage store while the ledger's last day is
    before the deadline (/yq-stock damage lists it on that day), to any other store while it is on or before it.
    The damage store is the audit's to_kind 'damage' (a set of identical issues sent there too), or, where the
    audit names no to_kind, a damage_not_booked issue."""
    due, day = fmt.as_date(exc.get("deadline")), fmt.as_date(d)
    if due is None or day is None:
        return False
    damage = exc.get("to_kind") == "damage" or exc.get("kind") == "damage_not_booked"
    return day < due if damage else day <= due


def _late(exc: Mapping, d, rules) -> int | None:
    return working_days_after(exc.get("deadline"), d, rules) if exc.get("deadline") else None


def _receipt_on(exc: Mapping) -> str:
    """' on <day>': the day a paired receipt was booked, the issue day plus the lag, when the audit gives both."""
    day, lag = fmt.as_date(exc.get("date")), exc.get("lag")
    try:
        return f" on {fmt.day(day + timedelta(days=int(lag)))}" if day is not None and lag is not None else ""
    except (TypeError, ValueError, OverflowError):
        return ""


def _route(exc: Mapping) -> str:
    return f"from {_store(exc.get('from'))} to {_store(exc.get('to'))}"


def _not_received(exc, d, limits, rules) -> dict | None:
    issues = _issues(exc)
    if not issues:
        return None
    sio, bhd = issues[0], _bhd(exc.get("bhd"))
    when = f"issued {_day(exc.get('date'))}, due {_day(exc.get('deadline'))}; no receipt found"
    detail = f"{sio}: {_route(exc)}, {when}. Items: {_lines_text(exc.get('lines'))}"
    return _item("xfer_open", (_ref(sio),), _level(bhd, _late(exc, d, rules), limits), TITLES["not_received"],
                 detail, f"{sio}: {when}", bhd, [sio])


def _damage_month(month: str, excs: list[dict], d, limits, rules) -> dict:
    excs = sorted(excs, key=lambda e: (fmt.as_date(e.get("date")) or date.min, pairing.parse_number(_issues(e)[0])))
    sios = [_issues(e)[0] for e in excs]
    values = [v for v in (_bhd(e.get("bhd")) for e in excs) if v is not None]
    bhd = fmt.q3(sum(values, Decimal(0))) if values else None
    lates = [x for x in (_late(e, d, rules) for e in excs) if x is not None]
    y, m = (int(p) for p in month.split("-"))
    words = date(y, m, 1).strftime("%B %Y")
    parts = [f"{s} on {_day(e.get('date'))} from {_store(e.get('from'))}" for s, e in zip(sios, excs)]
    detail = (f"Issued to {_store(excs[0].get('to'))} in {words} and not booked in there by the deadline: "
              + "; ".join(parts))
    fallback = f"Issued in {words} and not booked in by the deadline: " + ", ".join(sios)
    return _item("xfer_open", ("damage", month), _level(bhd, max(lates) if lates else None, limits),
                 TITLES["damage"], detail, fallback, bhd, sios)


def _twins(exc, d, limits, rules) -> dict | None:
    issues = _issues(exc)
    if not issues:
        return None
    n, bhd = len(issues), _bhd(exc.get("bhd"))
    x = exc.get("open")
    x = x if isinstance(x, int) and not isinstance(x, bool) and 0 < x <= n else None
    said = (f"{x} of {n} identical issues {_route(exc)} have no receipt" if x else
            f"Not every one of {n} identical issues {_route(exc)} has a receipt")
    detail = f"{said}: {', '.join(issues)}; the receipts cannot say which. Items: {_lines_text(exc.get('lines'))}"
    fallback = (f"{x} of {n}" if x else f"Not every one of {n}") + " identical issues: " + ", ".join(issues)
    return _item("xfer_twins", (_ref(issues[0]),), _level(bhd, _late(exc, d, rules), limits), TITLES["twins"],
                 detail, fallback, bhd, issues, count=x)


def _qty_differs(exc, d, limits, rules) -> dict | None:
    issues = _issues(exc)
    if not issues:
        return None
    sio, strv, bhd = issues[0], _number_of(exc.get("receipt")), _bhd(exc.get("bhd"))
    under = f", received under {strv}" if strv else ""
    detail = (f"{sio} {_route(exc)}{under}{_receipt_on(exc)}: the lines that differ: "
              f"{_lines_text(exc.get('lines'))}")
    return _item("qty_diff", (_ref(sio),), _level(bhd, None, limits), TITLES["qty_differs"], detail,
                 f"{sio}{under}: other quantities", bhd, [sio, strv])


def _elsewhere(exc, d, limits, rules) -> dict | None:
    issues = _issues(exc)
    if not issues:
        return None
    sio, strv, bhd = issues[0], _number_of(exc.get("receipt")), _bhd(exc.get("bhd"))
    under = f" under {strv}" if strv else ""
    detail = f"{sio} {_route(exc)}: received at {_store(exc.get('received_at'))}{under}{_receipt_on(exc)}"
    return _item("xfer_elsewhere", (_ref(sio),), _level(bhd, None, limits), TITLES["elsewhere"], detail,
                 f"{sio}: received in another store{under}", bhd, [sio, strv])


def _orphan(exc, d, limits, rules) -> dict | None:
    strv = _number_of(exc.get("receipt"))
    if not strv:
        return None
    bhd = _bhd(exc.get("bhd"))
    possible = [p for p in (_number_of(x) for x in _listed(exc.get("possible"))) if p]
    head = f"{strv}, received {_day(exc.get('date'))}: no issue found"
    detail = (f"{strv} at {_store(exc.get('received_at') or exc.get('to'))}, received {_day(exc.get('date'))}: "
              "no issue found" + (f"; it may belong to {', '.join(possible)}" if possible else "")
              + f". Items: {_lines_text(exc.get('lines'))}")
    return _item("rcpt_orphan", (_ref(strv),), _level(bhd, None, limits), TITLES["orphan"], detail, head, bhd,
                 [strv])


BUILDERS = {"not_received": _not_received, "twins": _twins, "qty_differs": _qty_differs,
            "elsewhere": _elsewhere, "orphan": _orphan}


def found_items(exceptions, d, limits: Mapping | None = None,
                rules: pairing.Rules | None = None) -> tuple[dict[str, list[dict]], list[str]]:
    """The audit's exceptions as register items: ({rule: [items]} with every rule of MOVE_RULES, notes). The
    damage store's are folded into one item per month of issue. An issue still inside its deadline (_in_grace)
    is left out without a word: it is not late. A kind no rule knows, an exception that names no voucher and a
    key found twice (the first, the larger, is kept) are each counted in a note."""
    d = fmt.as_date(d)
    limits = limits or {}
    found: dict[str, list[dict]] = {rule: [] for rule in MOVE_RULES}
    months: dict[str, list[dict]] = {}
    unknown: dict[str, int] = {}
    unread = 0
    for raw in exceptions or []:
        exc = _as_dict(raw)
        kind = exc.get("kind")
        if kind in WAITING and _in_grace(exc, d):
            continue
        if kind == "damage_not_booked":
            day = fmt.as_date(exc.get("date"))
            if day is None or not _issues(exc):
                unread += 1
            else:
                months.setdefault(day.strftime("%Y-%m"), []).append(exc)
            continue
        build = BUILDERS.get(kind)
        if build is None:
            unknown[str(kind)] = unknown.get(str(kind), 0) + 1
            continue
        item = build(exc, d, limits, rules)
        if item is None:
            unread += 1
        else:
            found[KIND_RULE[kind]].append(item)
    for month in sorted(months):
        found["xfer_open"].append(_damage_month(month, months[month], d, limits, rules))
    twice = 0
    for rule in MOVE_RULES:
        seen: set[str] = set()
        kept = []
        for item in found[rule]:
            if item["key"] in seen:
                twice += 1
                continue
            seen.add(item["key"])
            kept.append(item)
        found[rule] = kept
    notes: list[str] = []
    if unknown:
        notes.append("the movement audit gave a kind the action list does not know: "
                     + ", ".join(f"{k} ({v})" for k, v in sorted(unknown.items())))
    if unread:
        notes.append(f"{_plural(unread, 'exception')} of the movement audit named no voucher: not recorded")
    if twice:
        notes.append(f"{_plural(twice, 'exception')} of the movement audit repeated a key: the first was kept")
    return found, notes


# ── the thresholds and the 90-day replay ──────────────────────────────────────

def _stores(audit) -> pairing.StoreRules:
    """The stores as the audit read them (Audit.store_rules; Audit.stores is its table of vans and outlets), else
    as config.json names them."""
    s = _get(audit, "store_rules")
    if isinstance(s, pairing.StoreRules):
        return s
    return pairing.StoreRules(main=config.get("warehouses.main"), damage=config.get("warehouses.damage") or None,
                              not_counted=tuple(config.get("warehouses.not_counted")),
                              aliases=config.get("warehouses.aliases"))


def _rules(audit) -> pairing.Rules:
    r = _get(audit, "rules")
    return r if isinstance(r, pairing.Rules) else pairing.Rules()


def _pair_numbers(result) -> tuple[list[tuple[str, tuple[str, ...]]], int]:
    """(issue number, (receipt number, ...)) of every pair of the pairing result, and how many pairs named no
    number. A pair names its issue and its receipt (`issue`, `receipt`: a leg with `number` or `no`, or the
    number itself) and the further receipts it was booked in under (`also`: the pairing's step S), or is the
    two of them in that order."""
    pairs = _get(result, "pairs")
    if isinstance(pairs, Mapping):
        pairs = list(pairs.values())
    out: set[tuple[str, tuple[str, ...]]] = set()
    unread = 0
    for p in pairs or []:
        more = ()
        if isinstance(p, (tuple, list)) and len(p) >= 2:
            a, b = p[0], p[1]
        else:
            a, b, more = _get(p, "issue"), _get(p, "receipt"), _get(p, "also") or ()
        i, r = _number_of(a), _number_of(b)
        if i and r:
            out.add((i, (r,) + tuple(n for n in (_number_of(x) for x in more) if n)))
        else:
            unread += 1
    return sorted(out, key=lambda t: (pairing.parse_number(t[0]), pairing.parse_number(t[1][0]))), unread


def _act_days(late: list[int]) -> int:
    """The smallest N >= 0 for which at most LATE_RECEIPTS_MAX of the receipts came more than N working days
    late: the (LATE_RECEIPTS_MAX + 1)-th largest lateness, or 0 when there are no more receipts than that."""
    ordered = sorted(late, reverse=True)
    return max(0, ordered[LATE_RECEIPTS_MAX]) if len(ordered) > LATE_RECEIPTS_MAX else 0


def _percentile(values: list[Decimal], p: int) -> Decimal:
    """The p-th percentile by nearest rank: the value at rank ceil(p * n / 100), counted from the smallest."""
    ordered = sorted(values)
    rank = -(-p * len(ordered) // 100)
    return ordered[max(rank, 1) - 1]


def replay_defaults(result, costs, d, *, rows=(), exceptions=(), stores: pairing.StoreRules | None = None,
                    rules: pairing.Rules | None = None) -> dict:
    """The thresholds the 90-day replay works out (the module text). PURE: the pairing result (its pairs), the
    costs per ledger item, the ledger's last day, its rows (the dates, destinations and lines of the vouchers,
    through ledger.vouchers) and the audit's exceptions in, figures out; the same arguments in any order give
    the same answer. A figure with nothing to rest on is None: act_days with no receipt in the days, act_bhd
    with no item valued.
        -> {act_days, floor_bhd, act_bhd, basis{first, last, receipts, later, items, unvalued, pairs_unread}}"""
    rules = rules or pairing.Rules()
    d = fmt.as_date(d)
    first = d - timedelta(days=REPLAY_DAYS - 1)
    rows = rows if isinstance(rows, (list, tuple)) else list(rows or [])
    issues = ledger.vouchers(rows, pairing.ISSUE) if rows else {}
    receipts = ledger.vouchers(rows, pairing.RECEIVE) if rows else {}
    numbers, unread = _pair_numbers(result)
    late: list[int] = []
    late_items: list[tuple[str, Decimal | None]] = []
    for issue_no, receipt_nos in numbers:
        i = issues.get(issue_no)
        got = [receipts[n] for n in receipt_nos if n in receipts]
        if i is None or not got or not first <= i["date"] <= d:
            continue
        kind = stores.kind(i["to_wh"]) if stores is not None else "other"
        booked = max(r["date"] for r in got)     # booked in under two receipts (step S): in when the later one is
        days = working_days_after(pairing.deadline(i["date"], kind, rules), booked, rules)
        late.append(days)
        if days > 0:                          # open on the register from its deadline to its receipt
            late_items.append((issue_no, _value(i["lines"], costs)))
    window = [e for e in exceptions or [] if isinstance(e, Mapping) and fmt.as_date(e.get("date")) is not None
              and first <= fmt.as_date(e.get("date")) <= d]
    found, _notes = found_items(window, d, {}, rules)
    items = [it for rule in MOVE_RULES for it in found[rule]]
    named = {n for it in items for n in it["evidence"]}
    values = [it["bhd"] for it in items] + [v for n, v in late_items if n not in named]
    priced = sorted((Decimal(str(v)) for v in values if v is not None), reverse=True)
    act_days = _act_days(late) if late else None
    floor = priced[FLOOR_RANK - 1] if len(priced) >= FLOOR_RANK else Decimal(0)
    act_bhd = _percentile(priced, ACT_PERCENTILE) if priced else None
    basis = {"first": first.isoformat(), "last": d.isoformat(), "receipts": len(late),
             "later": sum(1 for x in late if act_days is not None and x > act_days),
             "items": len(priced), "unvalued": len(values) - len(priced), "pairs_unread": unread}
    return {"act_days": act_days, "floor_bhd": fmt.q3(floor),
            "act_bhd": fmt.q3(act_bhd) if act_bhd is not None else None, "basis": basis}


def defaults_from_audit(audit, store: bool = True) -> dict:
    """replay_defaults over the audit's parts, as state.json keeps them ({act_days, floor_bhd, act_bhd, basis,
    at}; amounts as text). Kept (store=True) only when every figure could be worked out: a short ledger is
    tried again on the next one."""
    res = replay_defaults(_get(audit, "result"), _get(audit, "costs") or {}, _get(audit, "d"),
                          rows=_get(audit, "rows") or [], exceptions=_get(audit, "exceptions") or [],
                          stores=_stores(audit), rules=_rules(audit))
    doc = {"act_days": res["act_days"], "basis": res["basis"], "at": _now(),
           "floor_bhd": str(res["floor_bhd"]) if res["floor_bhd"] is not None else None,
           "act_bhd": str(res["act_bhd"]) if res["act_bhd"] is not None else None}
    if store and all(doc[k] is not None for k in LIMIT_KEYS):
        status.write_state({STATE_DEFAULTS: doc})
    return doc


def stored_defaults() -> dict | None:
    kept = status.read_state().get(STATE_DEFAULTS)
    return kept if isinstance(kept, dict) and all(k in kept for k in LIMIT_KEYS) else None


def _configured(name: str):
    """watch.<name> from config.json: None when it is not there (the replay stands in). A value of the wrong
    kind stops the command in the config reader's own sentence (config.ConfigMissing)."""
    key = "watch." + name
    try:
        cfg = config.load()
    except config.ConfigMissing:
        return None
    if key not in cfg:
        return None
    return int(config.get(key)) if name == "act_days" else fmt.q3(config.number(key))


def _as_limit(name: str, value):
    if value is None:
        return None
    return int(value) if name == "act_days" else _bhd(value)


def thresholds(audit=None) -> dict:
    """The floor and the two ACT thresholds in force: {floor_bhd, act_bhd, act_days, from{name: 'config' |
    'replay'}, basis, at}. config.json wins, each key by itself; the others come from the replay kept in
    state.json, worked out now from `audit` (and kept) when none is kept yet. A figure that cannot be had
    is None: no floor, or no ACT by that test."""
    out: dict = {"floor_bhd": None, "act_bhd": None, "act_days": None, "from": {}, "basis": None, "at": None}
    for name in LIMIT_KEYS:
        value = _configured(name)
        if value is not None:
            out[name], out["from"][name] = value, "config"
    missing = [n for n in LIMIT_KEYS if out[n] is None]
    if missing:
        kept = stored_defaults()
        if kept is None and audit is not None:
            kept = defaults_from_audit(audit)
        if kept:
            out["basis"], out["at"] = kept.get("basis"), kept.get("at")
            for name in missing:
                value = _as_limit(name, kept.get(name))
                if value is not None:
                    out[name], out["from"][name] = value, "replay"
    return out


def _limits_json(limits: Mapping) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in limits.items()}


# ── the one recorder ──────────────────────────────────────────────────────────

def record_moves(audit, data_date: str) -> dict:
    """THE ONE PLACE that records the movement rules. Every rule of MOVE_RULES is recorded on every run, an
    empty list when nothing is found, so items that are no longer found are cleared; each rule's first run is
    its baseline. NotProven (nothing recorded) when the audit's ledger is not proven equal to production.
        -> {counts{rule: alerts.record's counts}, limits, notes}"""
    source = _source(audit)
    if not source.get("per_store_ok"):
        raise NotProven(_unproven_words(source))
    limits = thresholds(audit)
    found, notes = found_items(_get(audit, "exceptions") or [], _get(audit, "d"), limits, _rules(audit))
    counts = {}
    for rule in MOVE_RULES:
        counts[rule] = alerts.record(rule, found[rule], data_date)
    return {"counts": counts, "limits": _limits_json(limits), "notes": notes}


def _unproven_words(source: Mapping) -> str:
    why = " ".join(str(source.get("why") or "").split()).rstrip(".")
    return why or "the stock ledger is not proven equal to what the portal holds"


def _moves():
    """scripts.ops.moves (the movement audit), imported when it is first needed: the action list still shows
    the load check's and the Command Centre's items without it."""
    return importlib.import_module("scripts.ops.moves")


def _audit(prefer: str = "auto"):
    return _moves().audit(prefer=prefer)


def _file_key() -> str | None:
    """The sha256 of the loaded folder's Stock_ledger file (the key of a ledger the audit read from the file);
    None when there is none or it cannot be read."""
    try:
        f = ledger.find_file(ledger.loaded_folder())
        return loadplan.sha256_file(f) if f is not None else None
    except OSError:
        return None


def ledger_key(audit) -> str:
    """Which ledger the audit read: its file's sha256, or 'db:<last day>' when it read the database."""
    sha = _get(audit, "sha") or _source(audit).get("sha")
    if not sha and _source(audit).get("source") == "file":
        sha = _file_key()
    if sha:
        return str(sha)
    d = fmt.as_date(_get(audit, "d"))
    return f"db:{d.isoformat() if d else '?'}"


def _failure_words(exc: BaseException) -> str:
    if isinstance(exc, ModuleNotFoundError) and str(exc.name or "").startswith("scripts.ops.moves"):
        return "the movement audit is not built yet"
    if isinstance(exc, (NotProven, ledger.NoLedgerFile, config.ConfigMissing)):
        return str(exc).rstrip(".")
    return f"the movement audit could not run ({type(exc).__name__})"


def ensure_recorded(record: bool = True, *, prefer: str = "auto") -> dict:
    """Record the movement rules once per stock ledger (the module text). NEVER RAISES; the answer is
        {ok, recorded, already?, why?, key?, data_date?, counts?, notes?, note?}
    record=False (a delivery preview) records nothing. A ledger not proven equal to production records
    nothing and answers ok False with the reason, one sentence the caller prints as a warning."""
    if not record:
        return {"ok": True, "recorded": False, "why": "a preview records nothing"}
    try:
        done = status.read_state().get(STATE_RECORDED)
        done = done if isinstance(done, dict) else {}
        quick = _file_key()
        if quick and done.get("key") == quick:
            return {"ok": True, "recorded": False, "already": True, "key": quick, "data_date": done.get("data_date"),
                    "at": done.get("at")}
        audit = _audit(prefer)
        source = _source(audit)
        if not source.get("per_store_ok"):
            return {"ok": False, "recorded": False, "why": _unproven_words(source), "note": source.get("note")}
        d = fmt.as_date(_get(audit, "d"))
        if d is None:
            return {"ok": False, "recorded": False, "why": "the movement audit gave no last day of the ledger"}
        key = ledger_key(audit)
        if done.get("key") == key:
            return {"ok": True, "recorded": False, "already": True, "key": key, "data_date": done.get("data_date"),
                    "at": done.get("at"), "note": source.get("note")}
        out = record_moves(audit, d.isoformat())
        status.write_state({STATE_RECORDED: {"key": key, "data_date": d.isoformat(), "at": _now(),
                                             "counts": out["counts"]}})
        return {"ok": True, "recorded": True, "key": key, "data_date": d.isoformat(), "counts": out["counts"],
                "notes": out["notes"], "note": source.get("note")}
    except Exception as exc:  # noqa: BLE001  (never raises: a failure is one sentence, never the e-mail's end)
        return {"ok": False, "recorded": False, "why": _failure_words(exc)}


# ── for the brief and the e-mail ──────────────────────────────────────────────

def movement_items(include_baseline: bool = False, floor=_IN_FORCE) -> list[dict]:
    """The open movement items, most pressing first, those under the floor left out: the floor in force by
    default, a figure of the caller's, or None (none left out)."""
    if floor is _IN_FORCE:
        floor = thresholds().get("floor_bhd")
    return [r for r in alerts.open_items(include_baseline, floor=floor, floor_rules=MOVE_RULES)
            if r.get("rule") in MOVE_RULES]


def summary() -> dict:
    """The movement items on the register, in figures (no audit is run): {open, act, check, bhd, under_floor
    {rule: n}, floor_bhd, recorded (state.json's moves_recorded)}."""
    floor = thresholds().get("floor_bhd")
    items = movement_items(floor=floor)
    return {"open": len(items), "act": sum(1 for r in items if r.get("level") == "ACT"),
            "check": sum(1 for r in items if r.get("level") == "CHECK"),
            "bhd": str(fmt.q3(sum((Decimal(str(r.get("bhd") or 0)) for r in items), Decimal(0)))),
            "under_floor": alerts.under_floor(floor, MOVE_RULES) if floor is not None else {},
            "floor_bhd": str(floor) if floor is not None else None,
            "recorded": status.read_state().get(STATE_RECORDED)}


# ── BHD at display time ───────────────────────────────────────────────────────

def _norm_invoice(text) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


class LedgerIndex:
    """The ledger rows indexed once for the BHD worked out at display time: units per (voucher, item), and
    the Sales Invoice lines per invoice."""

    def __init__(self, rows, costs):
        self.costs = costs or {}
        self.by_voucher: dict[tuple[str, str], Decimal] = {}
        self.by_invoice: dict[str, dict[str, Decimal]] = {}
        self.names: dict[str, str] = {}
        for r in rows or []:
            units = fmt.dec(r.get("qty_in")) + fmt.dec(r.get("qty_out"))
            item = str(r.get("item") or "")
            self.names[_ref(item)] = item
            key = (_ref(r.get("voucher") or "(no number)"), _ref(item))
            self.by_voucher[key] = self.by_voucher.get(key, Decimal(0)) + units
            if r.get("vtype") == ledger.SALE:
                lines = self.by_invoice.setdefault(_norm_invoice(r.get("voucher")), {})
                lines[item] = lines.get(item, Decimal(0)) + units

    def valued(self, lines: Mapping) -> tuple[str, Decimal | None, bool]:
        """(the BHD column's text, the value, some lines have no cost)."""
        if not lines:
            return "gone", None, False
        total, priced, unpriced = Decimal(0), False, False
        for item, units in sorted(lines.items()):
            c = _cost(self.costs, item)
            if c is None:
                unpriced = True
            else:
                total += units * c
                priced = True
        if not priced:
            return "no cost", None, True
        value = fmt.q3(total)
        return fmt.bhd(value) + ("*" if unpriced else ""), value, unpriced

    def shown(self, row: Mapping) -> tuple[str, Decimal | None, bool]:
        rule, key = row.get("rule"), str(row.get("key") or "")
        if rule == "voucher_changed":
            voucher, item = (key.split("|", 2) + ["", ""])[1:3]
            units = self.by_voucher.get((voucher, item))
            return self.valued({self.names.get(item, item): units} if units is not None else {})
        if rule == "inv_changed":
            return self.valued(self.by_invoice.get(_norm_invoice(key.split("|", 1)[-1])) or {})
        return _stored_text(row), _bhd(row.get("bhd")), False


def _stored_text(row: Mapping) -> str:
    return fmt.bhd(row.get("bhd")) if row.get("bhd") not in (None, "") else ""


# ── the screen ────────────────────────────────────────────────────────────────

def parse_spec(spec) -> tuple[str, str | None, str | None]:
    """(view, id, words): view is open | all | replay | item | explain | done."""
    tokens = [w for raw in (spec or []) for w in str(raw).split()]
    if not tokens:
        return "open", None, None
    head = tokens[0].lower()
    if head in ("all", "replay") and len(tokens) == 1:
        return head, None, None
    if head in ("explain", "done"):
        if len(tokens) < 2 or not (_ID.match(tokens[1]) or "|" in tokens[1]):
            tail = " <your words>." if head == "explain" else " [your words]."
            raise WatchInput(f"Name the item: /yq-watch {head} <id>{tail}")
        words = " ".join(tokens[2:]).strip() or None
        if head == "explain" and not words:
            raise WatchInput(f"Say why in your own words: /yq-watch explain {tokens[1]} <your words>.")
        return head, tokens[1], words
    if len(tokens) == 1 and (_ID.match(tokens[0]) or "|" in tokens[0]):
        return "item", tokens[0], None
    raise WatchInput(f"'{' '.join(tokens)[:60]}' is not a word of this command. {USAGE}")


def add_arguments(parser) -> None:
    parser.add_argument("spec", nargs="*", metavar="word",
                        help="all, an item's id (A12), explain <id> <words>, done <id> [words], or replay")
    parser.add_argument("--ledger", choices=ledger.PREFER, default="auto",
                        help="where the movement audit reads the stock ledger: auto (the file when it equals "
                             "production), file, db")


def _recording_line(rec: Mapping, d: date | None) -> str:
    on = f"the stock ledger to {fmt.day(d)}" if d else "this stock ledger"
    if rec.get("recorded"):
        totals = {k: 0 for k in ("new", "same", "cleared", "baseline", "explained")}
        for counts in (rec.get("counts") or {}).values():
            for k in totals:
                totals[k] += int(counts.get(k) or 0)
        line = (f"MOVEMENTS  recorded for {on}: {totals['new']} new, {totals['same']} as before, "
                f"{totals['cleared']} cleared")
        if totals["baseline"]:
            line += f", {totals['baseline']} counted once as already there (the first run of a rule)"
        if totals["explained"]:
            line += f"; {totals['explained']} you explained stay off the list"
        return line + "."
    if rec.get("already"):
        return f"MOVEMENTS  already recorded for {on}."
    return f"MOVEMENTS  not recorded this time: {rec.get('why') or 'no reason given'}. The list below is as it stood."


def _profit_words() -> str:
    last = status.read_state().get("last_load")
    held = fmt.as_date(last.get("profit_held_since")) if isinstance(last, dict) else None
    if held is not None:
        return f"the profitability report is held back since {fmt.day(held)}"
    return "it needs the profitability report"


def hooks_line() -> str:
    """The two rules that come with the month-end release, named so that nobody takes their silence for a
    clean bill."""
    return fmt.careful(HOOKS.format(profit=_profit_words()))


def _blocks(include_baseline: bool, floor) -> dict[str, list[dict]]:
    """The open items by block: the movements (and any rule not shown read-only), the load check's, the
    Command Centre's."""
    out: dict[str, list[dict]] = {"moves": [], "load_check": [], "command_centre": []}
    for r in alerts.open_items(include_baseline, floor=floor, floor_rules=MOVE_RULES):
        rule = r.get("rule")
        block = ("load_check" if rule in LOAD_CHECK_RULES else
                 "command_centre" if rule in COMMAND_CENTRE_RULES else "moves")
        out[block].append(r)
    return out


def _marked(rows: list[dict]) -> list[dict]:
    """Rows as a list shows them: an older item (the baseline) says so in its title."""
    return [dict(r, title="(older) " + str(r.get("title") or "")) if r.get("baseline") else r for r in rows]


def _floor_lines(floor) -> list[str]:
    """UNDER THE FLOOR: per rule, the open movement items valued below the floor, and their BHD."""
    counts = alerts.under_floor(floor, MOVE_RULES) if floor is not None else {}
    if not counts:
        return []
    sums: dict[str, Decimal] = {}
    for r in alerts.open_items():
        if r.get("rule") in counts and r.get("bhd") not in (None, "") and Decimal(str(r["bhd"])) < floor:
            sums[r["rule"]] = sums.get(r["rule"], Decimal(0)) + Decimal(str(r["bhd"]))
    lines = [f"UNDER THE FLOOR (below BHD {fmt.bhd(floor)} each: counted here, kept off this list and the brief's "
             "Do today; /yq-watch all lists them)"]
    return lines + [f"  {RULE_WORDS[rule]}: {counts[rule]} (BHD {fmt.bhd(sums.get(rule, 0))})"
                    for rule in MOVE_RULES if counts.get(rule)]


def _readonly_block(title: str, rows: list[dict], index: LedgerIndex | None, why: str | None) -> tuple[list, list]:
    """A block of items this command shows and never records: (lines, facts)."""
    if not rows:
        return [f"{title}  nothing open"], []
    table, facts, starred, valued_now = [], [], False, False
    for r in _marked(rows):
        if r.get("rule") in VALUED_NOW:
            valued_now = True
            text, value, part = index.shown(r) if index is not None else ("n/a", None, False)
            starred = starred or (part and value is not None)
        else:
            text, value = _stored_text(r), _bhd(r.get("bhd"))
        table.append([r.get("id", ""), r.get("level", ""), _cut(r.get("title")),
                      ", ".join((r.get("evidence") or [])[:3]), text])
        fact = {k: r.get(k) for k in ("id", "key", "rule", "level", "title", "evidence", "first_seen")}
        fact.update(bhd_now=str(value) if value is not None else None, bhd_text=text)
        facts.append(fact)
    lines = [title] + fmt.grid(["ID", "Level", "What", "Evidence", "BHD"], table, "llllr").splitlines()
    if starred:
        lines.append("* some lines have no cost on file: the BHD covers the others")
    if valued_now and index is None:
        lines.append(f"(the BHD of a changed invoice or voucher is not worked out this time: {why or 'no ledger'})")
    return lines, facts


def _baseline_lines() -> list[str]:
    rows = alerts.baseline_summary()
    if not rows:
        return ["BASELINE  nothing was already in the data when the rules first ran."]
    table = [[RULE_WORDS.get(b["rule"], b["rule"]), fmt.units(b["count"]), fmt.bhd(b["bhd"])] for b in rows]
    return (["BASELINE (shown once: what each rule found already in the data on its first run; counted, not "
             "listed; /yq-watch all lists them)"]
            + ["  " + ln for ln in fmt.grid(["Rule", "Items", "BHD"], table).splitlines()])


def threshold_lines(limits: Mapping, *, again: bool = False) -> list[str]:
    """The floor and the two ACT thresholds, each with where it comes from."""
    basis = limits.get("basis") or {}
    src = limits.get("from") or {}
    floor, act_bhd, act_days = limits.get("floor_bhd"), limits.get("act_bhd"), limits.get("act_days")

    def whence(name: str, words: str) -> str:
        if src.get(name) == "config":
            return "set in config.json"
        if src.get(name) == "replay":
            return "replay: " + words
        if basis:
            return "too little history to work it out: not applied"
        return "not worked out yet (the replay runs with the first movement audit): not applied"

    items = int(basis.get("items") or 0)
    receipts = int(basis.get("receipts") or 0)
    floor_words = (f"the {FLOOR_RANK}th-largest of {_plural(items, 'item')}" if items >= FLOOR_RANK else
                   f"fewer than {FLOOR_RANK} items, so no floor")
    act_words = f"the {ACT_PERCENTILE}th percentile of the same items"
    days_words = f"{basis.get('later', 0)} of {_plural(receipts, 'receipt')} came later than that"
    lines = ["THRESHOLDS (" + ("in force" if again else "shown once; /yq-watch replay shows them again")
             + "; a value in config.json wins: watch.floor_bhd, watch.act_bhd, watch.act_days)",
             "  Floor      " + (f"BHD {fmt.bhd(floor)}" if floor is not None else "none")
             + ": a movement item below it is counted under the floor, not listed"
             + f" ({whence('floor_bhd', floor_words)})",
             "  ACT from   " + (f"BHD {fmt.bhd(act_bhd)}" if act_bhd is not None else "no figure")
             + f" ({whence('act_bhd', act_words)})",
             "  ACT after  " + (f"{_plural(int(act_days), 'working day')} past the deadline" if act_days is not None
                                else "no figure") + f" ({whence('act_days', days_words)})"]
    if basis.get("first") and basis.get("last") and "replay" in src.values():
        when = f", worked out on {_day(str(limits.get('at') or '')[:10])}" if limits.get("at") else ""
        unvalued = int(basis.get("unvalued") or 0)
        lines.append(f"  The replay: the issues of {fmt.day(basis['first'])} to {fmt.day(basis['last'])}{when}"
                     + (f"; {_plural(unvalued, 'item')} with no cost left out" if unvalued else "") + ".")
    return lines


def _item_lines(row: Mapping, index: LedgerIndex | None) -> list[str]:
    rule = row.get("rule")
    state = row.get("state") or "open"
    lines = [f"{row.get('id', '?')}  {row.get('level', '')}  {state}"
             + ("  (older: counted once as the baseline)" if row.get("baseline") and state == "open" else ""),
             f"Rule:      {RULE_WORDS.get(rule, rule)} ({rule})",
             f"What:      {row.get('title') or ''}",
             f"Detail:    {row.get('detail') or ''}",
             f"Evidence:  {', '.join(row.get('evidence') or []) or 'none'}"]
    if rule in VALUED_NOW:
        text = index.shown(row)[0] if index is not None else "not worked out this time"
        lines.append(f"BHD:       {text} (worked out now, at cost)")
    elif row.get("bhd") not in (None, ""):
        kept = " (at cost, as recorded)" if rule in MOVE_RULES else ""
        lines.append(f"BHD:       {fmt.bhd(row.get('bhd'))}{kept}")
    lines.append(f"Seen:      first {_day(str(row.get('first_seen') or '')[:10])}; on the data of "
                 f"{_day(row.get('data_date'))}")
    if row.get("reason"):
        lines.append(f"His words: {row['reason']}")
    return lines


def _index(prefer: str) -> tuple[LedgerIndex | None, str | None, object]:
    """The ledger rows and costs for the BHD worked out at display time, from the movement audit (memoised
    there per ledger): (index, why not, audit)."""
    try:
        audit = _audit(prefer)
    except Exception as exc:  # noqa: BLE001  (the screen still prints; the BHD says it was not worked out)
        return None, _failure_words(exc), None
    source = _source(audit)
    if not source.get("per_store_ok"):
        return None, _unproven_words(source), audit
    return LedgerIndex(_get(audit, "rows") or [], _get(audit, "costs") or {}), None, audit


def set_item_state(view: str, ident: str, words: str | None) -> tuple[list[str], int]:
    """explain / done: the register's own set_state, with the item's title said back so that a wrong id shows."""
    row = alerts.find(ident)
    if row is None:
        return [NO_ITEM.format(ident=ident)], 1
    state = "explained" if view == "explain" else "done"
    new = alerts.set_state(row["key"], state, words)
    lines = [f"{new.get('id') or ident} {'explained' if state == 'explained' else 'marked done'}: "
             f"{new.get('title') or new.get('key')}",
             f"  Evidence: {', '.join(new.get('evidence') or []) or 'none'}"]
    if words:
        lines.append(f"  His words: {words}")
    if row.get("state") != "open":
        lines.append(f"  (it was {row.get('state')} before)")
    if state == "explained":
        lines.append("  It stays off the list while it is what you explained; it comes back by itself when it grows by "
                     "more than a quarter, becomes more pressing, or names a voucher that was not there.")
    else:
        lines.append("  It rests until its facts change: another level, another BHD or a new voucher brings it back.")
    return lines, 0


def _save(d: date | None, stem: str, text: str, facts: dict) -> list[str]:
    if d is None:
        return []
    from scripts.ops import sales              # the brief's own writer (it imports the portal: only here)
    return [str(p) for p in sales.save_screen(d, stem, text, facts)]


def _list_lines(view: str, rec: Mapping, d: date | None, floor, index, why_not, limits, facts: dict) -> list[str]:
    """The list itself (no word, or `all`)."""
    every = view == "all"
    lines = [_recording_line(rec, d)] + ([str(rec["note"])] if rec.get("note") else []) + [""]
    blocks = _blocks(every, None if every else floor)
    lines.append("MOVEMENTS TO CHECK (ACT before CHECK, then the larger BHD, then the older; BHD at cost)")
    lines += alerts.render(_marked(blocks["moves"])).splitlines()
    facts["movements"] = [{k: r.get(k) for k in ("id", "key", "rule", "level", "title", "bhd", "evidence",
                                                 "first_seen", "baseline")} for r in blocks["moves"]]
    if not every:
        lines += _floor_lines(floor)
        facts["under_floor"] = alerts.under_floor(floor, MOVE_RULES) if floor is not None else {}
    lines.append("")
    part, facts["load_check"] = _readonly_block(
        "FROM THE LOAD CHECK (read-only: the load check records these; the BHD of a changed invoice or voucher is "
        "worked out now, at cost)", blocks["load_check"], index, why_not)
    lines += part
    part, facts["command_centre"] = _readonly_block("FROM THE COMMAND CENTRE (read-only: the brief records these)",
                                                    blocks["command_centre"], index, why_not)
    lines += part
    state = status.read_state()
    if not state.get(STATE_SHOWN):
        lines += [""] + _baseline_lines() + [""] + threshold_lines(limits)
        facts["baseline"] = [dict(b, bhd=str(b["bhd"])) for b in alerts.baseline_summary()]
        if state.get(STATE_RECORDED):          # the movement rules have had their first run: shown for good
            status.write_state({STATE_SHOWN: {"at": _now(), "data_date": d.isoformat() if d else None}})
    return lines + ["", hooks_line(), HOW_TO]


def run(args) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    try:
        view, ident, words = parse_spec(getattr(args, "spec", None))
    except WatchInput as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    if view in ("explain", "done"):
        lines, code = set_item_state(view, ident, words)
        result = dict(out, ok=code == 0, text="\n".join(_guarded(ln) for ln in lines),
                      flags={"view": view, "open": status.register_counts()})
        return dict(result, exit=code) if code else result
    if view == "item" and alerts.find(ident) is None:
        return dict(out, ok=False, exit=1, text=NO_ITEM.format(ident=ident))

    prefer = getattr(args, "ledger", "auto") or "auto"
    rec = ensure_recorded(prefer=prefer)
    warnings = list(rec.get("notes") or [])
    if not rec.get("ok"):
        warnings.append(f"the movement rules were not recorded: {rec.get('why')}")
    index, why_not, audit = _index(prefer)
    d = fmt.as_date(_get(audit, "d")) if audit is not None else fmt.as_date(rec.get("data_date"))
    limits = thresholds(audit if index is not None else None)
    floor = limits.get("floor_bhd")
    facts: dict = {"view": view, "recorded": rec, "thresholds": _limits_json(limits)}
    lines = ["YQ ACTION LIST - " + (f"stock ledger to {fmt.day(d)}" if d else "the stock ledger was not read")]
    if view == "item":
        row = alerts.find(ident)
        lines += [""] + _item_lines(row, index)
        facts["item"] = {k: row.get(k) for k in ("id", "key", "rule", "state", "level", "title", "detail", "bhd",
                                                 "evidence", "first_seen", "data_date", "reason")}
        stem = f"watch-{str(row.get('id') or 'item').lower()}"
    elif view == "replay":
        lines += [""] + threshold_lines(limits, again=True)
        stem = "watch-replay"
    else:
        lines += _list_lines(view, rec, d, floor, index, why_not, limits, facts)
        stem = "watch-all" if view == "all" else "watch"

    text = "\n".join(_guarded(ln).rstrip() for ln in lines)
    try:
        files = _save(d, stem, text, facts)
    except (OSError, ImportError) as exc:
        files = []
        warnings.append(f"the screen was not saved ({type(exc).__name__})")
    flags = {"view": view, "open": status.register_counts(), "recorded": bool(rec.get("recorded")),
             "floor_bhd": str(floor) if floor is not None else None}
    return dict(out, data_date=d.isoformat() if d else None, text=text, flags=flags, files=files, warnings=warnings)
