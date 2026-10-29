"""The e-mail's "what to do" list: at most five actions, each with why, the expected effect, the next
step, who and by when (scripts/ops/deliver.py). Two sources, one ranking:

  * the action register (scripts.ops.alerts, what DO TODAY lists on the chat screen): each open item
    gets its owner, next step and deadline from PLAYBOOK, by its key;
  * opportunities read from the brief's own figures (sales.full_brief and enrich.build): items to
    push, shops due a re-order, accounts that have not come back, the month's pace.

Rank = expected effect in BHD x confidence x urgency / effort. An item with no money figure counts as
NO_FIGURE_BHD, so a count-only item still ranks. Every action is tagged:
  ACTION    the figures say it (facts from the reports)
  TO CHECK  something differs and the reason is not known: a question, never a conclusion
Nothing here accuses anyone (fmt.careful guards the words). The register itself is never written.

An item's Why is its detail only for the rules whose detail names no store, salesman or customer
(DETAIL_SAFE): the Command Centre's items keep their definition (sales.attention_found), a changed invoice its
number, a fixed word and a day, a held-back profitability report its figures. Every other rule's Why is built
from numbers, codes and dates only (plain_why), because its detail can name a van (a salesman), a customer or a
store, and the e-mail leaves this PC while the register (alerts.jsonl) stays on it:
  the five move rules (MOVE_RULES)   the recorder's own plain fallback (move_why): vouchers, count, the month of
                                     its key, the days of its detail
  price_scope                        the item code, the scoped and the general price, the start day and the units
                                     sold at it; its scope ("for <a van or a customer>") never
  voucher_changed                    the voucher, and the quantities and days of its lines as loaded and as in the
                                     file; their stores and the item's name never
  a rule this module does not know   its first voucher or number only
No word of a detail besides those numbers, codes and days is ever taken, and the e-mail's customer_names
setting (delivery.json) changes none of this: a price_scope scope can be a van as well as a customer, and the
register cannot tell which."""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from scripts.ops import fmt
from scripts.ops.enrich import nth_business_day

TOP = 5
NO_FIGURE_BHD = Decimal(50)
# the five move rules of /yq-watch (scripts.ops.checks.MOVE_RULES; tests/test_ops_deliver.py holds the two equal)
MOVE_RULES = ("xfer_open", "xfer_twins", "qty_diff", "xfer_elsewhere", "rcpt_orphan")
# the rules whose detail names no store, salesman or customer (the module text): their Why is their detail
DETAIL_SAFE = ("attn", "inv_changed", "profit_held")
# a day as the recorders write it in a detail (fmt.day), an amount (fmt.bhd) and a quantity (fmt.units): with the
# codes of an item's key, the only parts of a detail a plain Why takes
_DAY = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_MONEY = r"\d{1,3}(?:,\d{3})*\.\d{3}"
_QTY = r"\d[\d,]*(?:\.\d+)?"
_MONTH = re.compile(r"(\d{4})-(\d{2})")
_LINE = re.compile(rf"\b(received|issued) ({_QTY}) on ({_DAY})|\bno quantity on ({_DAY})")
_MORE_LINES = re.compile(r"\band (\d+) more lines\b")

# key prefix -> (owner, next step, business days to do it in, effort 1 low .. 3 high). The longest
# matching prefix wins; a key no entry names gets the DEFAULT words and is still listed.
PLAYBOOK = {
    "attn|orders_waiting": ("Sales team", "Confirm or call each waiting marketplace order", 0, 1),
    "attn|delivered_no_invoice": ("Operations", "Invoice the delivered orders in Focus", 1, 1),
    "attn|invoiced_open": ("Operations", "Close the invoiced orders that are still open in the portal", 2, 1),
    "attn|invoice_awaiting_acceptance": ("Operations", "Get the waiting invoices accepted", 2, 1),
    "attn|unowned_ar": ("You, with accounts", "Give each account with no active rep to a rep and agree a "
                                              "collection date", 3, 2),
    "attn|below_cost": ("You", "Check the price and the cost of the items sold below cost before the next "
                               "order", 2, 2),
    "attn|sold_out": ("You (purchasing)", "Reorder, or name the item to offer instead, for each item sold "
                                          "out with demand", 3, 2),
    "attn|rep_silence": ("You", "Ask the rep what is holding his invoices back; check his stock and his route "
                                "first", 1, 1),
    "attn|stale_data": ("You", "Load the newest Focus reports", 0, 1),
    "attn|chat_tickets": ("The ticket's rep, then you", "Open each chat ticket, call the shop and mark it in progress "
                                                        "or resolved (portal: YQ Assist, Tickets)", 1, 1),
    "profit_held": ("Accounts", "Post cost of sales in Focus for the invoices it is missing, then export the "
                                "profitability report again", 3, 1),
    "inv_changed": ("Accounts", "Check why these invoices changed after they were exported", 3, 2),
    "voucher_changed": ("Accounts", "Check why these stock vouchers changed or were entered with an earlier date",
                        3, 2),
    "price_scope": ("You", "Decide on the price book's prices for one van or one customer", 5, 2),
    # the five move rules of /yq-watch (scripts.ops.checks.MOVE_RULES): what the stock ledger's transfers show
    "xfer_open": ("Storekeeper, with the receiving store", "Post the Stock Receive Voucher or reverse the issue", 1, 1),
    "xfer_twins": ("Storekeeper", "Find which identical transfer was received", 2, 1),
    "qty_diff": ("Storekeeper, with the van", "Count the transfer's items and correct the wrong voucher", 2, 2),
    "xfer_elsewhere": ("Storekeeper", "Correct the receipt or the issue to the store the goods are in", 3, 1),
    "rcpt_orphan": ("Storekeeper", "Find the issue for each receipt or reverse it", 3, 1),
}
DEFAULT = ("You", "Look into it", 3, 2)


def playbook(key: str) -> tuple[str, str, int, int]:
    best = ""
    for prefix in PLAYBOOK:
        if str(key).startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return PLAYBOOK[best] if best else DEFAULT


def urgency(days: int) -> int:
    return 3 if days <= 0 else 2 if days <= 2 else 1


def _bhd(x) -> Decimal | None:
    if x in (None, ""):
        return None
    try:
        v = fmt.dec(x)
    except (ArithmeticError, ValueError, TypeError):
        return None
    return v if v > 0 else None


def _safe(text: str) -> str:
    """The words as they are, unless they hold a word the brief never uses (fmt.careful): then a neutral
    line, so one oddly worded item never stops the e-mail."""
    try:
        return fmt.careful(text)
    except ValueError:
        return "(this item's wording is withheld here: see the chat brief)"


def _action(*, tag: str, title: str, why: str, impact: str, impact_bhd: Decimal | None, step: str, owner: str,
            days: int, effort: int, confidence: float, source: str, d: date, ref: str | None = None) -> dict:
    due = nth_business_day(d + timedelta(days=1), days)
    weight = (impact_bhd or NO_FIGURE_BHD) * Decimal(str(confidence)) * urgency(days) / max(1, effort)
    return {"tag": tag, "title": _safe(title), "why": _safe(why), "impact": impact,
            "impact_bhd": str(fmt.q3(impact_bhd)) if impact_bhd is not None else None, "next_step": step,
            "owner": owner, "due": due.isoformat(), "due_text": fmt.day(due), "source": source, "ref": ref,
            "score": float(round(weight, 3))}


def is_move(key: str) -> bool:
    """True for an item of one of the five move rules (its key's first part)."""
    return str(key or "").split("|", 1)[0] in MOVE_RULES


def _day_after(word: str, text: str) -> str | None:
    m = re.search(rf"\b{word} ({_DAY})\b", text)
    return m.group(1) if m else None


def move_why(item: dict) -> str:
    """A move item's Why in numbers and dates only, in the words of the recorder's own plain fallback
    (scripts/ops/checks.py): built from its key, its vouchers (Focus numbers), its count and the days its
    detail holds. No other word of the detail is ever taken: the detail names the stores, and a van's store
    is its salesman's name."""
    parts = str(item.get("key") or "").split("|")
    rule = parts[0]
    vouchers = [str(v).strip() for v in item.get("evidence") or [] if str(v or "").strip()]
    first = vouchers[0] if vouchers else (parts[1] if len(parts) > 1 and parts[1] != "damage" else "(no number)")
    other = vouchers[1] if len(vouchers) > 1 else None
    detail = str(item.get("detail") or "")
    if rule == "xfer_open" and len(parts) > 2 and parts[1] == "damage":
        m = _MONTH.fullmatch(parts[2])
        month = date(int(m.group(1)), int(m.group(2)), 1).strftime("%B %Y") if m and 1 <= int(m.group(2)) <= 12 else ""
        return (f"Issued{' in ' + month if month else ''} and not booked in by the deadline: "
                + (", ".join(vouchers) or "no number on record"))
    if rule == "xfer_open":
        issued, due = _day_after("issued", detail), _day_after("due", detail)
        when = ", ".join(w for w in (f"issued {issued}" if issued else "", f"due {due}" if due else "") if w)
        return f"{first}: {when}; no receipt found" if when else f"{first}: no receipt found by its deadline"
    if rule == "xfer_twins":
        n = len(vouchers)
        x = item.get("count")
        x = x if isinstance(x, int) and not isinstance(x, bool) and 0 < x <= n else None
        return (f"{x} of {n}" if x else f"Not every one of {n}") + " identical issues: " + ", ".join(vouchers)
    if rule == "qty_diff":
        return first + (f", received under {other}" if other else "") + ": other quantities"
    if rule == "xfer_elsewhere":
        return f"{first}: received in another store" + (f" under {other}" if other else "")
    if rule == "rcpt_orphan":
        received = _day_after("received", detail)
        return first + (f", received {received}" if received else "") + ": no issue found"
    return f"{first}: to check"


def _price_why(item: dict) -> str:
    """A price_scope item's Why: load_check's own words for it with its scope left out. The code and the start
    day come from its key (price_scope|<code>|<the scope's hash>|<start>), the two prices and the units from its
    detail, each read only where its shape is a number's; the scope is never read."""
    parts = str(item.get("key") or "").split("|")
    code = parts[1] if len(parts) > 1 and parts[1] else "(no code)"
    start = fmt.as_date(parts[3]) if len(parts) > 3 else None
    since = f" from {fmt.day(start)}" if start else ""
    detail = str(item.get("detail") or "")
    rate = re.match(rf"[^:]+: ({_MONEY}) from ", detail)
    general = re.search(rf"\bthe general price is ({_MONEY})", detail)
    if not (rate and general):
        return f"{code}: a scoped price is below the general price{since}"
    sold = re.search(rf"; ({_QTY}) units already sold there at this price", detail)
    tail = (f"; {sold.group(1)} units already sold at this price" if sold else
            "; what was already sold at this price was not looked up" if "was not looked up" in detail else "")
    return f"{code}: {rate.group(1)}{since} for one van or customer, the general price is {general.group(1)}{tail}"


def _lines_plain(text: str) -> str:
    """One side of a changed voucher's detail ('received 4 on Mon 12 Oct at <store> to <store> x2; ...') as its
    quantities, days and repeats only: 'received 4 on Mon 12 Oct x2; ...', 'nothing' when it says so, '' when
    none reads."""
    if text.strip().rstrip(".") == "nothing":
        return "nothing"
    out = []
    for part in text.split("; "):
        m = _LINE.search(part)
        if m is None:
            continue
        times = re.search(r" x(\d+)$", part.strip())
        out.append((f"{m.group(1)} {m.group(2)} on {m.group(3)}" if m.group(1) else f"no quantity on {m.group(4)}")
                   + (f" x{times.group(1)}" if times else ""))
    more = _MORE_LINES.search(text)
    if out and more:
        out.append(f"and {more.group(1)} more lines")
    return "; ".join(out)


def _voucher_why(item: dict) -> str:
    """A voucher_changed item's Why: the voucher, and its lines as loaded and as in the file in quantities and
    days only (load_check's own words, its stores and the item's name left out); load_check's plain fallback
    when the detail does not read that way."""
    parts = str(item.get("key") or "").split("|")
    vouchers = [str(v).strip() for v in item.get("evidence") or [] if str(v or "").strip()]
    voucher = vouchers[0] if vouchers else (parts[1] if len(parts) > 1 and parts[1] else "(no number)")
    loaded, mark, now = str(item.get("detail") or "").partition("; the file has ")
    if mark and ": loaded as " in loaded:
        was, has = _lines_plain(loaded.split(": loaded as ", 1)[1]), _lines_plain(now)
        if was and has:
            return f"{voucher}: loaded as {was}; the file has {has}"
    return f"{voucher}: differs from what was loaded"


def plain_why(item: dict) -> str:
    """The Why of an item whose rule is not DETAIL_SAFE, in numbers, codes and dates only (the module text)."""
    rule = str(item.get("key") or "").split("|", 1)[0]
    if rule in MOVE_RULES:
        return move_why(item)
    if rule == "price_scope":
        return _price_why(item)
    if rule == "voucher_changed":
        return _voucher_why(item)
    vouchers = [str(v).strip() for v in item.get("evidence") or [] if str(v or "").strip()]
    return f"{vouchers[0] if vouchers else 'This item'}: to check, in full on /yq-watch"


def from_register(items: list[dict], d: date) -> list[dict]:
    """The open items of the action register (facts['do_today']['open']). The Why is the item's detail for a
    DETAIL_SAFE rule, plain_why's for every other (the module text)."""
    out = []
    for it in items or []:
        key = str(it.get("key") or "")
        owner, step, days, effort = playbook(key)
        if it.get("who"):
            owner = str(it["who"])
        level = str(it.get("level") or "CHECK").upper()
        bhd = _bhd(it.get("bhd"))
        count = it.get("count")
        impact = (f"BHD {fmt.bhd(bhd)} involved" if bhd is not None else
                  f"{count} involved" if count not in (None, "") else "no figure on record")
        why = str(it.get("detail") or "") if key.split("|", 1)[0] in DETAIL_SAFE else plain_why(it)
        out.append(_action(tag="ACTION" if level == "ACT" else "TO CHECK", title=str(it.get("title") or key),
                           why=why, impact=impact, impact_bhd=bhd, step=step, owner=owner,
                           days=days, effort=effort, confidence=1.0 if level == "ACT" else 0.6, source="register",
                           d=d, ref=str(it.get("id") or "") or None))
    return out


def opportunities(facts: dict, d: date) -> list[dict]:
    """What the figures show can be won this week, each with its own figure."""
    out = []
    en = facts.get("enriched") or {}
    push = en.get("push") or []
    if push:
        rise = sum((fmt.dec(p.get("rise_bhd")) for p in push), Decimal(0))
        names = ", ".join(p["item"] for p in push[:3])
        out.append(_action(
            tag="ACTION", title=f"Push the {len(push)} in-stock items that are selling faster",
            why=f"They sold BHD {fmt.bhd(rise)} more in the last 30 days than in the 30 before, and the main store "
                f"holds at least two weeks of each ({names}).",
            impact=f"BHD {fmt.bhd(rise)} more in the last 30 days", impact_bhd=_bhd(rise),
            step="Offer them at every visit today; the team message names them", owner="Sales team", days=0,
            effort=1, confidence=0.7, source="opportunity", d=d))
    due = en.get("shops_due") or {}
    if due.get("due"):
        value = _bhd(due.get("monthly_value_bhd"))
        out.append(_action(
            tag="ACTION", title=f"Visit or call the {due['due']} shops due a re-order",
            why=("Each has gone longer than its own usual gap between orders (the salesman app's Today list). "
                 f"Together they buy about BHD {fmt.bhd(due.get('monthly_value_bhd'))} a month."),
            impact=f"about BHD {fmt.bhd(due.get('monthly_value_bhd'))} a month of regular orders", impact_bhd=value,
            step="Each rep works his own list in the app today; the e-mail names the largest", owner="Each rep",
            days=2, effort=2, confidence=0.7, source="opportunity", d=d))
    gone = facts.get("accounts_not_back") or {}
    if gone.get("count"):
        out.append(_action(
            tag="ACTION", title=f"Call the {gone['count']} accounts that bought last month and not yet this month",
            why=f"They bought BHD {fmt.bhd(gone.get('bhd_last_month'))} last month.",
            impact=f"BHD {fmt.bhd(gone.get('bhd_last_month'))} bought last month", impact_bhd=_bhd(gone.get("bhd_last_month")),
            step="Each rep calls his own accounts; the largest are listed in the e-mail", owner="Each rep", days=5,
            effort=2, confidence=0.5, source="opportunity", d=d))
    pace = (facts.get("pace") or {}).get("pace") or {}
    step_bhd = (facts.get("pace") or {}).get("kpi_step")
    needed = _bhd(pace.get("needed_per_business_day"))
    if step_bhd and needed:
        left = pace.get("business_days_left")
        out.append(_action(
            tag="ACTION", title=f"Bring BHD {fmt.bhd(needed)} of sales each business day to reach the target",
            why=f"The month so far is {pace.get('done_pct')}% of the BHD {fmt.bhd(step_bhd)} target with {left} "
                "business days left.",
            impact=f"BHD {fmt.bhd(needed * Decimal(left or 0))} still to sell this month",
            impact_bhd=_bhd(needed * Decimal(left or 0)), step="Agree each rep's share of it this morning",
            owner="You and the team", days=0, effort=3, confidence=0.5, source="opportunity", d=d))
    return out


def ranked(facts: dict, d: date, top: int = TOP) -> tuple[list[dict], int]:
    """(the top actions, how many there were in all)."""
    every = from_register(((facts.get("do_today") or {}).get("open")) or [], d) + opportunities(facts, d)
    every.sort(key=lambda a: (-a["score"], a["tag"] != "ACTION", a["title"]))
    return every[:top], len(every)
