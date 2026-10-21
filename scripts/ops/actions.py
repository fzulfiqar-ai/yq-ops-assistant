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

An item's Why is its detail, except for the five move rules (MOVE_RULES): their detail names the stores of a
transfer, and a van's store is its salesman's name. The recorder keeps that detail in alerts.jsonl on this PC
(scripts/ops/checks.py); the e-mail leaves it, so a move item's Why is the recorder's own plain fallback, numbers
and dates only (move_why): its vouchers, its count, the month of its key and the days the detail holds, never a
word of the detail besides those days."""
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
# a day as the recorder writes it in a detail (fmt.day): the only words of a detail a move item's Why takes
_DAY = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_MONTH = re.compile(r"(\d{4})-(\d{2})")

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


def from_register(items: list[dict], d: date) -> list[dict]:
    """The open items of the action register (facts['do_today']['open']). A move item's Why is move_why's,
    never its detail (the module text)."""
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
        why = move_why(it) if is_move(key) else str(it.get("detail") or "")
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
