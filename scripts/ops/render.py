"""The words and pages of a load's delivery (scripts/ops/deliver.py), made from the brief's facts.

  team_text(...)          the WhatsApp message for the team group: plain text, at most TEAM_MAX characters
  privacy_problems(...)   why a team text may not go to the group ([] = it may)
  email_html / email_text the owner's management e-mail (private: names, accounts, the rep table)
  whatsapp_page(text)     a local page with the team text in an editable box, Copy and Open in WhatsApp
  note_subject / note_*   the short e-mails for a load that needs a look or failed: no figure at all

Pure: nothing is read, written or sent here. Every figure comes from the facts (sales.full_brief,
enrich.build, actions.ranked); nothing is computed here but sums of those figures and percentages for
display. A missing figure prints NOT EVIDENCED and says why. The e-mail uses inline styles and tables
only (what Outlook and Gmail both show), no image and no script.
"""
from __future__ import annotations

import html
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from scripts.ops import fmt

TEAM_MAX = 1000             # characters of the team message (one phone screen and a little more)
PRAISE_MIN_REPS = 3         # reps are named for praise only when at least this many have sold this month
NOT_EVIDENCED = "NOT EVIDENCED"
# what is never in the reports, said once in the e-mail so nobody reads its absence as nought
NEVER_IN_THE_REPORTS = (
    ("Visits, calls, quotations and leads", "no system records them yet"),
    ("Stock in the vans", "the stock report is loaded for the main store only"),
    ("Value of returns", "only the month-end ledger carries it, and it is not loaded"),
)
# a team message must not carry these: money owed, the reps' pay, or a word that singles a rep out
TEAM_FORBIDDEN = ("receivables?", "overdue", "owes?", "owed", "owing", "debts?", "outstanding", "tiers?", "kickbacks?",
                  "commissions?", "incentives?", "bonus(?:es)?", "salar(?:y|ies)")      # word forms, matched whole
NEGATIVE = ("below", "behind", "silent", "no invoice", "weak", "poor", "worst", "lowest", "missed", "slow",
            "failed", "late")

PLUM, PLUM_SOFT, INK, MUTED, RULE, PAPER = "#6D4091", "#F0EAF6", "#1B1522", "#6B6478", "#E6E0EC", "#F7F5FA"
GOOD, WARN, BAD = "#2A7954", "#904F0E", "#A8243A"
FONT = "Segoe UI, Arial, Helvetica, sans-serif"


# ── small helpers ────────────────────────────────────────────────────────────────────────────────

def esc(x) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


def money(x) -> str:
    return f"BHD {fmt.bhd(x)}" if x not in (None, "") else NOT_EVIDENCED


def whole(x) -> str:
    """BHD to the whole dinar, for the team message (the e-mail keeps the fils)."""
    if x in (None, ""):
        return NOT_EVIDENCED
    return f"BHD {int(fmt.dec(x).quantize(Decimal(1), rounding=ROUND_HALF_UP)):,}"


def whole_pct(x) -> str:
    try:
        return f"{int(Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))}%"
    except (ArithmeticError, ValueError, TypeError):
        return "-"


def long_day(d: date) -> str:
    """'Wednesday 14 October 2026'."""
    return f"{d:%A} {d.day} {d:%B %Y}"


def _d(x) -> Decimal:
    return fmt.dec(x)


def _day(x) -> str:
    return fmt.day(x) if x else "-"


def _plural(n, word: str) -> str:
    try:
        k = int(n)
    except (TypeError, ValueError):
        return f"{n} {word}s"
    return f"{k} {word}" + ("" if k == 1 else "s")


def url_quote(text: str) -> str:
    """Percent-encoding for a wa.me link (UTF-8; only the unreserved characters stay as they are)."""
    keep = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    return "".join(chr(b) if b in keep else f"%{b:02X}" for b in str(text).encode("utf-8"))


def wa_link(text: str) -> str:
    """WhatsApp's own share link: it opens WhatsApp with the text typed in, he picks the group and may
    edit it before he sends it. Nothing is sent by this program."""
    return "https://wa.me/?text=" + url_quote(text)


def _words(text: str) -> str:
    return " ".join(str(text or "").split())


# ── the team message ─────────────────────────────────────────────────────────────────────────────

def team_text(facts: dict, made_on: date) -> str:
    """The WhatsApp message for the team group. No sales total of the company (the month is a share of the
    target, plus what is still needed a day), no account, no money owed, nothing of one rep but praise.
    Lines are dropped from the end of the lists until it fits TEAM_MAX."""
    sales = facts.get("sales") or {}
    mtd = sales.get("mtd") or {}
    pace = facts.get("pace") or {}
    en = facts.get("enriched") or {}
    d = fmt.as_date(facts.get("data_date"))
    month = d.strftime("%B") if d else "This month"
    head = [f"*YQ Accessories - {fmt.day(made_on)}*"]
    step, p = pace.get("kpi_step"), pace.get("pace") or {}
    if step and p:
        if _d(mtd.get("bhd")) >= _d(step):
            head.append(f"{month} so far: our monthly target is reached. Well done, team!")
        else:
            head.append(f"{month} so far: {whole_pct(p.get('done_pct'))} of our monthly target; "
                        f"{_plural(p.get('business_days_left'), 'working day')} left, "
                        f"{whole(p.get('needed_per_business_day'))} a day needed.")
    elif mtd.get("change"):
        head.append(f"{month} so far: {mtd.get('change')} on the same days last month.")
    praise = _praise(facts)
    push = [x["item"] for x in (en.get("push") or [])][:3]
    sold_out = [x.get("item") for x in ((facts.get("stock") or {}).get("items") or [])][:3]
    due = (en.get("shops_due") or {}).get("due")
    priorities = _priorities(facts, push, bool(due))

    def build(n_push: int, n_out: int, with_praise: bool) -> str:
        lines = list(head)
        if with_praise and praise:
            lines.append(praise)
        if push[:n_push]:
            lines.append("Push today (in stock, selling faster): " + "; ".join(push[:n_push]) + ".")
        if sold_out[:n_out]:
            lines.append("Sold out in the main store, check with the office before promising: "
                         + "; ".join(str(x) for x in sold_out[:n_out]) + ".")
        if due:
            lines.append("Your shops due a re-order are on your Today page in the app.")
        if priorities:
            lines.append("Today's priorities:")
            lines += [f"{n}. {t}" for n, t in enumerate(priorities, 1)]
        lines.append("By 5 pm: reply here with your orders taken, shops visited and any product problem "
                     "(photo it with Spotted in the app).")
        return "\n".join(lines)

    for n_push, n_out, with_praise in ((3, 3, True), (3, 2, True), (3, 0, True), (2, 0, True), (2, 0, False),
                                       (1, 0, False), (0, 0, False)):
        text = build(n_push, n_out, with_praise)
        if len(text) <= TEAM_MAX:
            return text
    return text[:TEAM_MAX - 3].rstrip() + "..."


def _praise(facts: dict) -> str | None:
    """Real good news only: the reps who lead the month (named only when at least PRAISE_MIN_REPS have
    sold, never with a figure), a last 7 days above the normal range, a latest day above the 30-day
    average business day."""
    out = []
    team = [r for r in ((facts.get("enriched") or {}).get("team") or []) if _d(r.get("mtd_bhd")) > 0]
    if len(team) >= PRAISE_MIN_REPS:
        names = [str(r.get("name") or r.get("salesman")) for r in team[:3]]
        out.append(f"Well done {names[0]}, {names[1]} and {names[2]}, leading this month's sales")
    sales = facts.get("sales") or {}
    if str((sales.get("d7") or {}).get("reading") or "") == "above the normal range":
        out.append("the last seven days were above our normal range")       # no digit on a line that names a rep
    avg = _d((sales.get("average_business_day") or {}).get("bhd"))
    if avg > 0 and _d((sales.get("day") or {}).get("bhd")) > avg:
        out.append("the latest day beat our daily average of the last month")
    if not out:
        return None
    text = "; ".join(out)
    return text[0].upper() + text[1:] + "!"


def _priorities(facts: dict, push: list[str], due: bool) -> list[str]:
    out = []
    if push:
        out.append("Offer the items to push (above) at every visit: in stock and selling faster.")
    if due:
        out.append("Visit or call your shops due a re-order (your Today page).")
    if (facts.get("accounts_not_back") or {}).get("count"):
        out.append("Call your accounts that bought last month and not yet this month.")
    orders = ((facts.get("enriched") or {}).get("orders") or {}).get("orders")
    if orders:
        out.append("Confirm every waiting marketplace order today.")
    return out[:3]


def _norm(text) -> str:
    """A name or a text with case, punctuation and spacing set aside: 'AL-MANAR  Mobiles ' and 'al manar
    mobiles' are one name (letters of any script count, Arabic included)."""
    return " ".join(re.sub(r"[\W_]+", " ", str(text or "").casefold()).split())


def known_names() -> list[str]:
    """Every customer and salesman name of the loaded Focus files (business_data/ops/denylist.json, the
    commit gate's list), or [] when it is not there."""
    try:
        from scripts.ops import selftest
        return [str(n) for n in (selftest.load_denylist() or [])]
    except Exception:  # noqa: BLE001  (the names the facts carry are checked all the same)
        return []


def team_names(facts: dict) -> tuple[set[str], set[str]]:
    """(customer and shop names, rep names) for the privacy check: the names the facts carry, and every
    other name of the loaded Focus files that is not a rep's (a shop's name inside an item name counts)."""
    customers: set[str] = set()
    for a in (facts.get("accounts_not_back") or {}).get("accounts") or []:
        customers.add(str(a.get("account") or ""))
    en = facts.get("enriched") or {}
    for a in ((en.get("receivables") or {}).get("top_overdue")) or []:
        customers.add(str(a.get("account") or ""))
    for s in ((en.get("shops_due") or {}).get("shops")) or []:
        customers.add(str(s.get("shop") or ""))
    for inv in ((facts.get("sales") or {}).get("largest_invoice") or {}).values():
        if isinstance(inv, dict):
            customers.add(str(inv.get("account") or ""))
    reps: set[str] = set()
    for r in en.get("team") or []:
        reps.update({str(r.get("name") or ""), str(r.get("salesman") or "")})
    for r in ((facts.get("reps") or {}).get("salesmen")) or []:
        reps.add(str(r.get("salesman") or ""))
    reps = {r for r in reps if len(_norm(r)) >= 3}
    rep_keys = {_norm(r) for r in reps} | {_norm(r.split(" - ")[0]) for r in reps}
    customers.update(n for n in known_names()          # a rep's Focus name is '<name>' or '<name> - ...'
                     if _norm(n) not in rep_keys and _norm(n.split(" - ")[0]) not in rep_keys)
    clean = {c for c in customers if len(_norm(c)) >= 4 and _norm(c) not in ("cash customer",)}
    return clean, reps


def _has(word: str, text: str) -> bool:
    """Is `word` in `text` as whole words, case, punctuation and spacing set aside?"""
    w = _norm(word)
    return bool(w) and f" {w} " in f" {_norm(text)} "


def privacy_problems(text: str, facts: dict) -> list[str]:
    """Why this text may not go to the team group ([] = it may): a customer or shop name, a word about
    money owed or the reps' pay, a rep named on a line that holds any figure (a digit or a percent), or a
    rep named beside a word that singles him out. The check reads the finished text, so nothing added later
    can slip past it. A rep may be named on a line of praise alone, and that line holds no figure."""
    customers, reps = team_names(facts)
    problems = []
    for name in sorted(customers):
        if _has(name, text):
            problems.append(f"a customer or shop is named ({name.strip()[:2]}...)")
    for form in TEAM_FORBIDDEN:
        hit = re.search(r"(?<![\w])(?:" + form + r")(?![\w])", text, re.IGNORECASE)
        if hit:
            problems.append(f"the word '{hit.group(0).lower()}' is not for the group")
    for line in text.splitlines():
        named = [r for r in reps if _has(r, line) or _has(r.split(" - ")[0], line)]
        if not named:
            continue
        if re.search(r"\d|%", line):
            problems.append("a rep is named beside an amount or a figure")
        if any(_has(w, line) for w in NEGATIVE):
            problems.append("a rep is named beside a word that singles him out")
    return problems


# ── the management e-mail ────────────────────────────────────────────────────────────────────────

def email_subject(facts: dict, made_on: date, test: bool = False) -> str:
    return (("[TEST] " if test else "") + f"YQ Daily Sales & Operations Brief - {made_on:%a %d %b %Y} "
            f"(data to {_day(facts.get('data_date'))})")


def _summary(facts: dict, load: dict) -> list[str]:
    sales = facts.get("sales") or {}
    day, d7, mtd = sales.get("day") or {}, sales.get("d7") or {}, sales.get("mtd") or {}
    pace = facts.get("pace") or {}
    out = []
    step, p = pace.get("kpi_step"), pace.get("pace") or {}
    if step and p:
        out.append(f"Month so far {money(mtd.get('bhd'))}: {p.get('done_pct')}% of the {money(step)} target; "
                   f"{money(p.get('needed_per_business_day'))} needed per business day for the "
                   f"{p.get('business_days_left')} days left.")
    else:
        out.append(f"Month so far {money(mtd.get('bhd'))} ({mtd.get('change') or 'not compared'} on the same "
                   f"business days last month). Target: {NOT_EVIDENCED} (no KPI step set).")
    avg = (sales.get("average_business_day") or {}).get("bhd")
    out.append(f"Latest day ({_day(day.get('start'))}): {money(day.get('bhd'))} in {day.get('invoices') or 0} "
               f"invoices; the 30-day average business day is {money(avg)}.")
    if d7:
        out.append(f"Last 7 days: {money(d7.get('bhd'))}, {d7.get('reading') or 'not judged'}.")
    gone = facts.get("accounts_not_back") or {}
    if gone.get("count"):
        out.append(f"{gone['count']} accounts that bought last month ({money(gone.get('bhd_last_month'))}) have not "
                   "bought this month.")
    held = (load.get("profitability") or {})
    if held.get("held"):
        out.append(f"The profitability report is held back (Focus cost of sales is missing for recent invoices): "
                   f"margins stay as on {_day(held.get('production_as_of'))}.")
    return out[:5]


def _table(head: list[str], rows: list[list], align: str = "") -> str:
    cell = f"padding:6px 8px;border-bottom:1px solid {RULE};font-size:13px;color:{INK};"
    th = (f"padding:6px 8px;border-bottom:2px solid {PLUM};font-size:12px;color:{MUTED};text-align:left;"
          "font-weight:600;")
    out = [f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;'
           f'margin:6px 0 14px 0;font-family:{FONT};">']
    if any(head):                               # a list of label and value has no head row
        out.append("<tr>")
        for i, h in enumerate(head):
            right = "text-align:right;" if (align[i:i + 1] == "r") else ""
            out.append(f'<th style="{th}{right}">{esc(h)}</th>')
        out.append("</tr>")
    for r in rows:
        out.append("<tr>")
        for i, c in enumerate(r):
            right = "text-align:right;white-space:nowrap;" if (align[i:i + 1] == "r") else ""
            out.append(f'<td style="{cell}{right}">{esc(c)}</td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _h2(text: str) -> str:
    return (f'<h2 style="font-family:{FONT};font-size:15px;color:{PLUM};margin:22px 0 6px 0;'
            f'padding-bottom:4px;border-bottom:1px solid {RULE};">{esc(text)}</h2>')


def _p(text: str, muted: bool = False, raw: bool = False) -> str:
    color = MUTED if muted else INK
    body = text if raw else esc(text)
    return f'<p style="font-family:{FONT};font-size:13px;line-height:1.5;color:{color};margin:4px 0 8px 0;">{body}</p>'


def _ul(items: list[str]) -> str:
    li = "".join(f'<li style="margin:0 0 6px 0;">{esc(i)}</li>' for i in items)
    return (f'<ul style="font-family:{FONT};font-size:13px;line-height:1.5;color:{INK};margin:6px 0 10px 18px;'
            f'padding:0;">{li}</ul>')


def _sales_rows(facts: dict) -> list[list]:
    s = facts.get("sales") or {}
    avg = (s.get("average_business_day") or {}).get("bhd")
    rows = []
    for key, label in (("day", "Latest day"), ("d3", "Last 3 days"), ("d7", "Last 7 days"), ("lfw", "Last full week"),
                       ("mtd", "Month to date")):
        w = s.get(key) or {}
        if not w:
            continue
        span = _day(w.get("start")) if w.get("start") == w.get("end") else f"{_day(w.get('start'))} - {_day(w.get('end'))}"
        if key == "day":
            note = f"the 30-day average business day: {money(avg)}"
        elif key == "d3":
            note = f"three average business days: {money(_d(avg) * 3) if avg else NOT_EVIDENCED}"
        elif key == "mtd":
            note = f"{w.get('change') or 'not compared'} on the same business days last month"
        else:
            note = str(w.get("reading") or "")
        rows.append([label, span, fmt.bhd(w.get("bhd")), str(w.get("invoices") or 0), note])
    return rows


def _rep_next(r: dict, silent_after: int, pace_share: float | None) -> str:
    """One next step per rep, from his own figures: silent first (ask, and look at stock and route
    first), then far behind the month's share of the target, then his shops due a re-order."""
    silent = r.get("business_days_since_invoice")
    if isinstance(silent, int) and silent > silent_after:
        return "Ask what is holding his invoices back; check his stock and route first"
    ach = r.get("achieved_pct")
    if ach is not None and pace_share is not None and ach + 15 < pace_share:
        return f"Agree how to reach the target: {money(r.get('needed_per_business_day_bhd'))} a business day"
    shops = r.get("shops_due") or 0
    if shops:
        return f"Work his {shops} shops due a re-order"
    return "Keep the pace"


def _team_section(facts: dict) -> tuple[list[list], list[str]]:
    en = facts.get("enriched") or {}
    team = en.get("team")
    if team is None:
        return [], [f"The rep table: {NOT_EVIDENCED} (the Command Centre's rep figures were not read)."]
    p = ((facts.get("pace") or {}).get("pace")) or {}
    done, left = p.get("business_days_done"), p.get("business_days_left")
    pace_share = (100.0 * done / (done + left)) if isinstance(done, int) and isinstance(left, int) and done + left else None
    silent_after = int(((facts.get("reps") or {}).get("silent_business_days")) or 7)
    rows = []
    for r in team:
        change = r.get("change_pct")
        ago = r.get("business_days_since_invoice")
        rows.append([r.get("name"), fmt.bhd(r.get("mtd_bhd")),
                     (f"{change:+.0f}% on the same days last month" if isinstance(change, (int, float))
                      else "nothing sold on the same days last month"),
                     _plural(r.get("invoices"), "invoice"), _plural(r.get("shops"), "shop"),
                     (f"{_day(r.get('last_invoice'))}" + (f" ({_plural(ago, 'business day')} ago)" if isinstance(ago, int)
                                                          else "")) if r.get("last_invoice") else "-",
                     (f"{r['achieved_pct']:.0f}%" if r.get("achieved_pct") is not None else "no target"),
                     fmt.bhd(r.get("needed_per_business_day_bhd")) if r.get("needed_per_business_day_bhd") else "-",
                     _rep_next(r, silent_after, pace_share)])
    sold_out = (facts.get("stock") or {}).get("sold_out_with_demand") or 0
    notes = [f"Before judging a decline: {sold_out} items that sold in the last 60 days are sold out in the main store, "
             f"and the stock in the vans is {NOT_EVIDENCED}. Ask about stock before anything else.",
             "Change = month to date against the same business days last month. Target = his first tier "
             "(kickback figures are left out of this e-mail)."]
    return rows, notes


def _rep_cards(rows: list[list]) -> list[str]:
    """One short block per rep (a nine-column table does not fit a phone); the reps with no invoice this
    month share one line."""
    out, idle = [], []
    for r in rows:
        name, mtd, change, inv, shops, last, target, needed, nxt = r
        if fmt.dec(mtd) <= 0:
            idle.append(str(name))
            continue
        figures = (f"BHD {mtd} ({change}) · {inv} · {shops} · target {target}"
                   + (f" · BHD {needed} a business day needed" if needed not in ("-", "0.000") else "")
                   + f" · last invoice {last}")
        out.append(f'<div style="border-bottom:1px solid {RULE};padding:7px 0;font-family:{FONT};font-size:13px;'
                   f'line-height:1.45;color:{INK};"><b>{esc(name)}</b><br>{esc(figures)}<br>'
                   f'<span style="color:{PLUM};">Next: {esc(nxt)}</span></div>')
    if idle:
        out.append(_p(f"No invoice yet this month: {', '.join(idle)}. Next: ask what is holding their invoices back; "
                      "check their stock and route first."))
    return out


def email_html(facts: dict, actions: list[dict], more: int, load: dict, team: str | None, made_on: date,
               test: bool = False, names: bool = True) -> str:
    """The management e-mail. `load` = what deliver read about the load (run, checks, timings, dates,
    the profitability evidence). `names` False leaves account and shop names out (delivery.json)."""
    en = facts.get("enriched") or {}
    parts = [f'<div style="background:{PAPER};padding:16px 8px;">',
             f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:640px;'
             f'margin:0 auto;background:#ffffff;border:1px solid {RULE};border-radius:8px;">'
             f'<tr><td style="padding:16px 14px 8px 14px;font-family:{FONT};">']
    if test:
        parts.append(_p("TEST: this copy went to the test address only.", muted=True))
    parts.append(f'<div style="font-family:{FONT};font-size:12px;color:{MUTED};letter-spacing:.04em;">YQ BAHRAIN · '
                 f'MOBILE ACCESSORIES</div>'
                 f'<h1 style="font-family:{FONT};font-size:20px;color:{INK};margin:4px 0 2px 0;">Daily Sales &amp; '
                 f'Operations Brief</h1>'
                 + _p(f"{long_day(made_on)} · data to {_day(facts.get('data_date'))} · loaded "
                      f"{facts.get('loaded') or '-'} · load {load.get('run') or '-'}", muted=True))
    parts += [_h2("In short"), _ul(_summary(facts, load))]

    parts.append(_h2("Sales (Accessories, ex-VAT)"))
    parts.append(_table(["Window", "Days", "BHD", "Invoices", "Reading"], _sales_rows(facts), "llrrl"))
    parts.append(_p(str(facts.get("basis") or ""), muted=True))

    pace = facts.get("pace") or {}
    parts.append(_h2("Target"))
    if pace.get("kpi_step") and pace.get("pace"):
        p = pace["pace"]
        mtd = ((facts.get("sales") or {}).get("mtd") or {}).get("bhd")
        parts.append(_table(["", ""], [["Monthly target", money(pace.get("kpi_step"))], ["Month so far", money(mtd)],
                                       ["Reached", f"{p.get('done_pct')}%"],
                                       ["Business days left", str(p.get("business_days_left"))],
                                       ["Needed per business day", money(p.get("needed_per_business_day"))]], "lr"))
        if pace.get("portal_target") and fmt.q3(pace.get("portal_target")) != fmt.q3(pace.get("kpi_step")):
            parts.append(_p(f"The portal's own target is {money(pace.get('portal_target'))}.", muted=True))
    else:
        parts.append(_p(f"Target: {NOT_EVIDENCED}: no KPI step is set for this month."))

    parts.append(_h2("Products and stock"))
    push = en.get("push")
    if push:
        parts.append(_p("To push today: in stock for at least two weeks and selling faster than the 30 days before."))
        parts.append(_table(["Item", "Units, 30 days", "30 days before", "In stock", "Days of cover"],
                            [[x["item"], x["units_30_days"], x["units_30_before"], x["in_stock"],
                              x["days_cover"] if x.get("days_cover") is not None else "-"] for x in push], "lrrrr"))
    elif push is None:
        parts.append(_p(f"Items to push: {NOT_EVIDENCED} (the stock or the movers were not read)."))
    else:
        parts.append(_p("No in-stock item is selling faster than in the 30 days before."))
    falling = ((en.get("movers") or {}).get("falling")) or []
    if falling:
        parts.append(_p("Falling most (last 30 days against the 30 before):"))
        parts.append(_table(["Item", "BHD, 30 days", "30 days before", "Change"],
                            [[x["item"], fmt.bhd(x["bhd_30_days"]), fmt.bhd(x["bhd_30_before"]), fmt.bhd(x["change_bhd"])]
                             for x in falling[:3]], "lrrr"))
    stock = facts.get("stock") or {}
    if stock.get("items"):
        parts.append(_p(f"Sold out in the main store and still asked for: {stock.get('sold_out_with_demand')} items "
                        f"({money(stock.get('bhd_60_days'))} sold in the last 60 days)."))
        parts.append(_table(["Item", "Sold in 60 days", "BHD"],
                            [[x["item"], x["sold_60_days"], fmt.bhd(x["bhd_60_days"])] for x in stock["items"]], "lrr"))
    shape = en.get("stock_shape")
    if shape:
        parts.append(_p(f"Stock at cost {money(shape.get('at_cost_bhd'))}; over a year of cover "
                        f"{money(shape.get('over_a_year_bhd'))} ({shape.get('over_a_year_items')} items); no sale in "
                        f"90 days {money(shape.get('no_sale_90_days_bhd'))} ({shape.get('no_sale_90_days_items')} items).",
                        muted=True))

    parts.append(_h2("Salesmen (private)"))
    rows, notes = _team_section(facts)
    parts += _rep_cards(rows)
    for n in notes:
        parts.append(_p(n, muted=True))

    parts.append(_h2("Customers"))
    due = en.get("shops_due")
    if due and due.get("shops") and names:
        parts.append(_p(f"{due['due']} shops are due a re-order (about {money(due.get('monthly_value_bhd'))} a month "
                        "together); the largest:"))
        parts.append(_table(["Shop", "Rep", "Days since", "Usual gap", "Monthly BHD"],
                            [[s["shop"], s["rep"], s["days_since"], s["usual_gap_days"], fmt.bhd(s["monthly_value_bhd"])]
                             for s in due["shops"]], "llrrr"))
    elif due:
        parts.append(_p(f"{due.get('due') or 0} shops are due a re-order (names are switched off for this e-mail)."))
    else:
        parts.append(_p(f"Shops due a re-order: {NOT_EVIDENCED} (not read this time)."))
    gone = facts.get("accounts_not_back") or {}
    if gone.get("count"):
        parts.append(_p(f"{gone['count']} accounts bought last month ({money(gone.get('bhd_last_month'))}) and not yet "
                        "this month" + (": the largest:" if names else ".")))
        if names:
            parts.append(_table(["Account", "Last month BHD", "Rep", "Last invoice"],
                                [[a["account"], fmt.bhd(a["bhd_last_month"]), a.get("salesman") or "-",
                                  _day(a.get("last_invoice"))] for a in (gone.get("accounts") or [])[:5]], "lrll"))

    parts.append(_h2("Exceptions"))
    parts += _exceptions(facts, load, names)

    parts.append(_h2("What to do"))
    if actions:
        for n, a in enumerate(actions, 1):
            color = PLUM if a["tag"] == "ACTION" else WARN
            parts.append(
                f'<div style="border-left:3px solid {color};padding:6px 10px;margin:8px 0;background:{PLUM_SOFT};'
                f'font-family:{FONT};font-size:13px;line-height:1.45;color:{INK};">'
                f'<b>{n}. [{esc(a["tag"])}] {esc(a["title"])}</b><br>'
                f'Why: {esc(a["why"] or "-")}<br>Expected effect: {esc(a["impact"])}<br>'
                f'Next step: {esc(a["next_step"])} · Who: {esc(a["owner"])} · By: {esc(a["due_text"])}</div>')
        if more > len(actions):
            parts.append(_p(f"{more - len(actions)} more on the action list (/yq-brief in Claude Code).", muted=True))
        parts.append(_p("ACTION = what the figures say. TO CHECK = something differs and the reason is not known: "
                        "a question, never a conclusion.", muted=True))
    else:
        parts.append(_p("Nothing is open on the action list."))

    parts.append(_h2("Data and load"))
    parts.append(_table(["What", "As on / result"], _load_rows(facts, load), "ll"))
    parts.append(_p("Not in the reports (" + NOT_EVIDENCED + "): " + "; ".join(f"{a}: {b}" for a, b in NEVER_IN_THE_REPORTS)
                    + ".", muted=True))

    if team:
        parts.append(_h2("Team message (WhatsApp)"))
        parts.append(_p("Ready to post: it names no customer, no money owed and nothing of one rep but praise. "
                        "Open it in WhatsApp, pick the team group, change what you like, then send."))
        parts.append(f'<pre style="font-family:Consolas,Menlo,monospace;font-size:12px;line-height:1.45;'
                     f'white-space:pre-wrap;background:{PAPER};border:1px solid {RULE};border-radius:6px;padding:10px;'
                     f'color:{INK};">{esc(team)}</pre>')
        parts.append(f'<p style="margin:8px 0 4px 0;"><a href="{esc(wa_link(team))}" style="background:{PLUM};color:#ffffff;'
                     f'text-decoration:none;padding:9px 14px;border-radius:6px;font-family:{FONT};font-size:13px;'
                     f'font-weight:600;">Open in WhatsApp</a></p>')

    parts.append(_p(f"Made by /yq-load right after load {load.get('run') or '-'}. The chat brief in Claude Code "
                    "shows the same figures. To have this e-mail sent again, ask Claude.", muted=True))
    parts.append("</td></tr></table></div>")
    return "".join(parts)


def _exceptions(facts: dict, load: dict, names: bool) -> list[str]:
    en = facts.get("enriched") or {}
    out = []
    pr = load.get("profitability") or {}
    if pr.get("held"):
        since = pr.get("production_as_of")
        moved = (f" {pr.get('with_no_cost_movement')} of the {pr.get('items_sold_since')} items sold since "
                 f"{_day(since)} show no cost in Focus." if pr.get("items_sold_since") else "")
        out.append(_p(f"Profitability report held back: its cost of sales is {pr.get('gap_pct')}% short of the stock "
                      f"ledger's (limit {pr.get('limit_pct')}%).{moved} Margins stay as on {_day(since)}. Accounts: "
                      "post cost of sales in Focus for the invoices after that day, then export the report again."))
    ar = en.get("receivables")
    if ar:
        top = ar.get("top_overdue") or []
        text = (f"Receivables {money(ar.get('total_bhd'))} on {ar.get('accounts')} accounts; over 90 days "
                f"{money(ar.get('over_90_bhd'))} ({ar.get('over_90_pct')}%).")
        if names and top:
            text += " Most overdue: " + "; ".join(f"{t['account']} {fmt.bhd(t['overdue_bhd'])}" for t in top[:3]) + "."
        out.append(_p(text))
    else:
        out.append(_p(f"Receivables: {NOT_EVIDENCED} (not read this time)."))
    orders = en.get("orders")
    if orders and orders.get("orders"):
        out.append(_p(f"Marketplace orders waiting: {orders['orders']} ({money(orders.get('bhd'))}), the oldest "
                      f"{orders.get('oldest_hours')} hours old."))
    disc = en.get("discounts") or {}
    if disc.get("evidenced"):
        out.append(_p(f"Discounts this month: {money(disc.get('bhd'))} on {disc.get('lines')} lines "
                      f"({disc.get('share_pct')}% of sales)."))
    else:
        out.append(_p(f"Discounts: {NOT_EVIDENCED} ({disc.get('why') or 'not read'}).", muted=True))
    for w in facts.get("warnings") or []:
        out.append(_p(f"Warning: {w}", muted=True))
    for w in en.get("not_read") or []:
        out.append(_p(f"Not read this time: {w}.", muted=True))
    return out


def _load_rows(facts: dict, load: dict) -> list[list]:
    rows = [["Sales to", _day(facts.get("data_date"))]]
    for key, label in (("stock_balance", "Stock (main store) as on"), ("receivables", "Receivables as on")):
        if (load.get("as_on") or {}).get(key):
            rows.append([label, _day(load["as_on"][key])])
    pr = load.get("profitability") or {}
    if pr:
        rows.append(["Profitability as on", _day(pr.get("production_as_of"))
                     + (" (the newer report held back)" if pr.get("held") else "")])
    rows.append(["Load", f"run {load.get('run') or '-'}: {load.get('result') or '-'}"])
    for key in ("verify", "reconcile", "gate"):
        if load.get(key):
            rows.append([key.capitalize(), load[key]])
    t = load.get("timings") or {}
    if t:
        total = load.get("total_seconds")
        waited = t.get("wait_tick")
        rows.append(["Load time", (f"{round(total / 60, 1)} min" if isinstance(total, (int, float)) else "-")
                     + (f" (of which waiting for a quiet moment {round(waited / 60, 1)} min)"
                        if isinstance(waited, (int, float)) and waited >= 30 else "")])
    return rows


def email_text(facts: dict, actions: list[dict], more: int, load: dict, team: str | None, made_on: date,
               test: bool = False, names: bool = True) -> str:
    """The plain-text part of the same e-mail (mail programs that show no HTML, and the search index)."""
    out = []
    if test:
        out.append("TEST: this copy went to the test address only.\n")
    out += [f"YQ Daily Sales & Operations Brief - {long_day(made_on)}",
            f"Data to {_day(facts.get('data_date'))} - loaded {facts.get('loaded') or '-'} - load {load.get('run') or '-'}",
            "", "IN SHORT"] + [f"- {s}" for s in _summary(facts, load)]
    out += ["", "SALES (Accessories, ex-VAT)"]
    out += [f"- {r[0]} ({r[1]}): BHD {r[2]} in {r[3]} invoices; {r[4]}" for r in _sales_rows(facts)]
    out += ["", "WHAT TO DO"]
    for n, a in enumerate(actions, 1):
        out += [f"{n}. [{a['tag']}] {a['title']}", f"   Why: {a['why'] or '-'}", f"   Expected effect: {a['impact']}",
                f"   Next step: {a['next_step']} - Who: {a['owner']} - By: {a['due_text']}"]
    if not actions:
        out.append("Nothing is open on the action list.")
    elif more > len(actions):
        out.append(f"({more - len(actions)} more on the action list: /yq-brief in Claude Code)")
    due = (facts.get("enriched") or {}).get("shops_due") or {}
    if due.get("due"):
        out += ["", f"SHOPS DUE A RE-ORDER: {due['due']}"]
        if names:
            out += [f"- {s['shop']} ({s['rep']}): {s['days_since']} days since, usually {s['usual_gap_days']}"
                    for s in due.get("shops") or []]
    rows, notes = _team_section(facts)
    if rows:
        out += ["", "SALESMEN (private)"]
        out += [f"- {r[0]}: BHD {r[1]} ({r[2]}), {r[3]}, {r[4]}, target {r[6]}, needed BHD {r[7]} a day; "
                f"next: {r[8]}" for r in rows]
        out += notes
    out += ["", "DATA AND LOAD"] + [f"- {a}: {b}" for a, b in _load_rows(facts, load)]
    if team:
        out += ["", "TEAM MESSAGE (WhatsApp, ready to post)", team, "", "Open in WhatsApp: " + wa_link(team)]
    out += ["", f"Made by /yq-load right after load {load.get('run') or '-'}. To have it sent again, ask Claude."]
    return "\n".join(out) + "\n"


# ── the local preview page of the team message ──────────────────────────────────────────────────

def whatsapp_page(text: str, made_on: date) -> str:
    """A page to open on this PC: the team message in a box he can edit, Copy, and Open in WhatsApp
    (built from what the box holds at that moment). Self-contained: no script or font from anywhere."""
    body = esc(text)
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>Team message {esc(fmt.day(made_on))}</title>"
        f"<style>body{{font-family:{FONT};background:{PAPER};color:{INK};margin:0;padding:16px}}"
        f"main{{max-width:640px;margin:0 auto;background:#fff;border:1px solid {RULE};border-radius:8px;padding:16px}}"
        f"textarea{{width:100%;box-sizing:border-box;min-height:22em;font:13px/1.45 Consolas,Menlo,monospace;"
        f"border:1px solid {RULE};border-radius:6px;padding:10px}}"
        f"button,a.btn{{background:{PLUM};color:#fff;border:0;border-radius:6px;padding:9px 14px;font-size:14px;"
        f"cursor:pointer;text-decoration:none;display:inline-block;margin:8px 8px 0 0}}"
        f"small{{color:{MUTED}}}</style></head><body><main>"
        f"<h1 style=\"font-size:18px;margin:0 0 6px\">Team message - {esc(fmt.day(made_on))}</h1>"
        "<small>Change anything you like, then Copy, or Open in WhatsApp and pick the team group.</small>"
        f"<textarea id=\"t\">{body}</textarea><div><small id=\"n\"></small></div>"
        "<button id=\"c\" type=\"button\">Copy</button><a class=\"btn\" id=\"w\" href=\"#\">Open in WhatsApp</a>"
        "<script>var t=document.getElementById('t'),n=document.getElementById('n');"
        "function upd(){n.textContent=t.value.length+' characters';"
        "document.getElementById('w').href='https://wa.me/?text='+encodeURIComponent(t.value);}"
        "t.addEventListener('input',upd);upd();"
        "document.getElementById('c').onclick=function(){t.select();"
        "if(navigator.clipboard){navigator.clipboard.writeText(t.value).then(function(){n.textContent='Copied.';});}"
        "else{document.execCommand('copy');n.textContent='Copied.';}};</script>"
        "</main></body></html>")


# ── the notes for a load that did not end well (no figure, on purpose) ──────────────────────────

def note_subject(kind: str, run: str, test: bool = False) -> str:
    word = "needs a look" if kind == "look" else "FAILED"
    return ("[TEST] " if test else "") + f"YQ load {run[:6]} {word}: no brief sent"


def note_text(kind: str, run: str, steps: list[str], test: bool = False) -> str:
    if kind == "look":
        said = (f"The load of folder {run[:6]} (run {run}) finished, but these checks need a look: "
                + (", ".join(steps) or "see the load's journal") + ". The figures are not sent until that is settled.")
        nxt = "Open Claude Code in the project folder and ask Claude to look into the lines marked 'needs a look'."
    else:
        said = (f"The load of folder {run[:6]} (run {run}) stopped after it had begun to write: the database may hold "
                "part of it. No figures are sent.")
        nxt = "Open Claude Code in the project folder and type /yq-load: it starts the load that replaces this one."
    head = "TEST: this copy went to the test address only.\n\n" if test else ""
    return head + said + "\n\n" + nxt + "\n"


def note_html(kind: str, run: str, steps: list[str], test: bool = False) -> str:
    paras = [p for p in note_text(kind, run, steps, test).split("\n\n") if p.strip()]
    return (f'<div style="background:{PAPER};padding:16px 8px;"><div style="max-width:640px;margin:0 auto;'
            f'background:#fff;border:1px solid {RULE};border-left:4px solid {BAD};border-radius:8px;padding:16px 20px;">'
            + "".join(_p(p.strip()) for p in paras) + "</div></div>")
