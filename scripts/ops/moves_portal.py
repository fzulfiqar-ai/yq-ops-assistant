"""python -m scripts.ops moves portal          (the release check of phase 2b: no word of the skill)

READ-ONLY proof that the portal's Stock Movement page pairs the transfers exactly as /yq-moves does.

  the portal   its own input, the view v_transfer_lines (scripts/r9_stock_moves_migration.sql), read through the
               read-only session of scripts.ops.db, with the API's own columns (app/transfer_audit.py
               TRANSFER_LINES_SQL, every day at once: LINES_SQL) and the API's own data date (the stock ledger's last
               day over every voucher type: transfer_audit.DATA_DATE_SQL); paired by the API's own function,
               transfer_audit.pairs_from_rows (imported when the check runs, never at import)
  /yq-moves    its own pairing of the stock ledger: moves.audit, from the source /yq-moves reads (--ledger as there)

The two are compared for the last 7 days (the default window of /yq-moves) and for the whole ledger:
  every voucher of the window   an issue: what became of it, the receipt or receipts it was paired with (both receipts
                                of a transfer booked in under two), the step, the basis and the identical issues it
                                could not be told apart from; a receipt: the issue it was paired with, or none; a
                                voucher set aside: why, and its partner; and of every one its day, stores and lines
  the counts                    pairing.summarise of the window on both sides: the status counts, and the vouchers,
                                lines and units of each outcome and of every issue
One line holds the verdict: `moves portal: PASS ...` when all of it is the same, else `moves portal: FAIL ...` with the
vouchers that differ below it (the first SHOWN, by date and number) and the counts that differ. No other line holds
either word. Exit code 0 PASS, 1 FAIL (or an error), 2 not checked: before migration M1 the view is not in the database,
and the one line says "not checked yet: v_transfer_lines is not in the database"; nothing more is read then.
scripts/r9_release.ps1 -Part verify runs it once the release API is live: it needs a line with the word PASS and none
with the word FAIL.

The narration is read as stored (masked by M1) and reduced to its hash by the API's function: no word of it is kept or
printed. Nothing is written: no copy of the screen, no record.
"""
from __future__ import annotations

from datetime import date

from scripts.ops import db  # noqa: F401  (first: it blanks the side channels before any app import)
from scripts.ops import fmt, moves, pairing

WORD = moves.PORTAL_WORD                         # python -m scripts.ops moves portal
PRESENT_SQL = "SELECT to_regclass('public.v_transfer_lines')::text AS found"
# The API's own read (app/transfer_audit.py TRANSFER_LINES_SQL) without its date range: every day at once. The tests
# hold the two equal.
LINES_SQL = ('SELECT voucher, vtype, "date"::text AS "date", wh, to_wh, item, qty_in, qty_out, value_in, value_out, '
             'narration FROM v_transfer_lines')
WINDOWS = (moves.DEFAULT_WINDOW, None)          # the last 7 days, then the whole ledger
SHOWN = 20                                      # the differing vouchers listed under a FAIL
VERDICT = "moves portal:"
NOT_YET = "not checked yet: v_transfer_lines is not in the database"
PASSED = f"{VERDICT} PASS - the portal pairs every transfer as /yq-moves does (the last 7 days and the whole ledger)."
OUTCOME_WORDS = {
    pairing.RECEIVED: "received", pairing.LATE: "received after the deadline",
    pairing.ELSEWHERE: "booked in at another store", pairing.QTY_DIFFERS: "received with other quantities",
    pairing.IN_GRACE: "not received yet", pairing.NOT_RECEIVED: "not received after the deadline",
    pairing.TWINS: "one of identical issues with fewer receipts", pairing.ORPHAN: "a receipt with no issue",
}
COUNT_WORDS = {
    "received": "received", "late": "received late", "elsewhere": "at another store",
    "qty_differs": "other quantities", "in_grace": "not received yet", "not_received": "not received",
    "twin_items": "sets of identical issues", "twin_slots": "identical issues without a receipt",
    "orphans": "receipts with no issue", "aside": "set aside",
}
COUNT_OUTCOME = {"received": pairing.RECEIVED, "late": pairing.LATE, "elsewhere": pairing.ELSEWHERE,
                 "qty_differs": pairing.QTY_DIFFERS, "in_grace": pairing.IN_GRACE, "not_received": pairing.NOT_RECEIVED,
                 "twin_slots": pairing.TWINS, "orphans": pairing.ORPHAN}      # a status count -> its outcome's figures


def _leg(leg) -> tuple:
    """What a leg holds, as both sides must hold it: (day, store, destination, lines)."""
    return (leg.day, leg.wh, leg.to, leg.lines)


def fates(result, first: date, last: date) -> dict[str, tuple]:
    """{voucher number: (kind, what became of it, what it holds)} for every voucher of `result` dated `first` to `last`
    (both included): its issues (outcome, receipts, step, basis, ambiguous or missing, twins), its receipts ('paired'
    and the issue, an orphan, or 'aside' with the issue sent to its own store) and the vouchers legs_from_rows set
    aside (their reason and partner). pairing.Result's own fields, nothing worked out anew."""
    out: dict[str, tuple] = {}
    for i in result.legs.issues:
        if first <= i.day <= last:
            got = result.by_issue[i.no]
            if isinstance(got, pairing.Pair):
                what = (got.outcome, (got.receipt.no, *(x.no for x in got.also)), got.step, got.basis, got.ambiguous,
                        got.twins)
            else:
                what = (got.outcome, (), None, (), got.missing, got.twins)
            out[i.no] = ("issue", what, _leg(i))
    for r in result.legs.receipts:
        if first <= r.day <= last:
            got = result.by_receipt[r.no]
            if isinstance(got, pairing.Pair):
                what = ("paired", (got.issue.no,), got.outcome)
            elif isinstance(got, pairing.Orphan):
                what = (pairing.ORPHAN, (), None)
            else:                               # set aside with the issue it books back in (pairing.MIRRORS_OWN)
                what = ("aside", (got.partner,) if got.partner else (), got.reason)
            out[r.no] = ("receipt", what, _leg(r))
    for a in result.aside:
        if a.voucher in out or a.day is None or not first <= a.day <= last:
            continue
        out[a.voucher] = (a.kind, ("aside", (a.partner,) if a.partner else (), a.reason), (a.day, a.wh, a.to, a.lines))
    return out


def _said(fate) -> str:
    """What became of a voucher, in a few words."""
    if fate is None:
        return "no such voucher"
    kind, what, _held = fate
    head, partners = what[0], what[1]
    if head == "paired":
        return f"booked in for {partners[0]}"
    if head == "aside":
        return f"set it aside ({what[2]}" + (f", with {partners[0]}" if partners else "") + ")"
    words = OUTCOME_WORDS.get(head, str(head))
    return words + (f" ({' + '.join(partners)})" if partners else "")


def _how(p: tuple, m: tuple) -> str:
    """Of two fates with the same outcome and partners: which of the rest differs."""
    parts = []
    if p[0] != m[0]:
        parts.append("its kind")
    if p[1][2:] != m[1][2:]:
        parts.append("how it was paired (step, basis, identical issues)")
    for n, words in ((0, "its date"), (1, "its store"), (2, "its destination"), (3, "its lines")):
        if p[2][n] != m[2][n]:
            parts.append(words)
    return ", ".join(parts) or "a detail"


def _order(day, no) -> tuple:
    """A voucher's place in a list: by its day, then by its number (pairing.parse_number)."""
    return (day is None, day or date.min, pairing.parse_number(no))


def differences(portal: dict, mine: dict) -> list[tuple]:
    """[(day, number, line)] of every voucher whose fate differs between the portal's fates and /yq-moves' (fates()),
    by date and number; the line says what each side made of it."""
    out = []
    for no in set(portal) | set(mine):
        p, m = portal.get(no), mine.get(no)
        if p == m:
            continue
        held = (p or m)[2]
        if p is None or m is None or p[1][:2] != m[1][:2]:
            line = f"{no}: the portal: {_said(p)}; /yq-moves: {_said(m)}"
        else:
            line = f"{no}: {_said(p)} on both sides; what differs: {_how(p, m)}"
        out.append((held[0], no, line))
    out.sort(key=lambda t: _order(t[0], t[1]))
    return out


def _joined(parts: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def count_differences(portal: dict, mine: dict) -> list[str]:
    """What differs between two pairing.summarise answers, one entry per status: its count, and the lines and units of
    the outcome that goes with it (an outcome's vouchers are its count); then every issue's vouchers, lines and units.
    Each figure '<the portal's> against </yq-moves'>'."""
    out = []
    for key in pairing.COUNT_KEYS:
        parts = []
        a, b = portal["counts"][key], mine["counts"][key]
        if a != b:
            parts.append(f"{fmt.units(a)} against {fmt.units(b)}")
        outcome = COUNT_OUTCOME.get(key)
        if outcome is not None:
            ta_, tb = portal["outcomes"][outcome], mine["outcomes"][outcome]
            parts += [f"{part} {fmt.units(ta_[part])} against {fmt.units(tb[part])}" for part in ("lines", "units")
                      if ta_[part] != tb[part]]
        if parts:
            out.append(f"{COUNT_WORDS.get(key, key)} {', '.join(parts)}")
    a, b = portal["issues"], mine["issues"]
    parts = [f"{part} {fmt.units(a[part])} against {fmt.units(b[part])}" for part in ("vouchers", "lines", "units")
             if a[part] != b[part]]
    if parts:
        out.append("every issue: " + ", ".join(parts))
    return out


def _sizes(f: dict) -> str:
    kinds = [fate[0] if fate[1][0] != "aside" else "aside" for fate in f.values()]
    n_i, n_r, n_a = kinds.count("issue"), kinds.count("receipt"), kinds.count("aside")
    return (f"{moves._plural(n_i, 'issue')}, {moves._plural(n_r, 'receipt')}"
            + (f", {fmt.units(n_a)} set aside" if n_a else ""))


def _empty_result(data_date: date):
    """The pairing of no rows at all (an empty view): every voucher of /yq-moves then differs."""
    from app import transfer_audit as ta
    legs = pairing.legs_from_rows([], stores=ta.portal_stores(), scope=None)
    return pairing.pair(legs, data_date=data_date)


def read_portal() -> tuple[bool, list[dict], date | None]:
    """(the view is there, its rows, the API's data date), in one read-only session: the view's presence first, and
    nothing more when it is not there."""
    from app import transfer_audit as ta     # scripts.ops.db is imported first (above): the side channels are blank
    with db.session() as s:
        found = s.q(PRESENT_SQL)
        if not found or not found[0].get("found"):
            return False, [], None
        day = s.q(ta.DATA_DATE_SQL)
        rows = s.q(LINES_SQL)
    return True, rows, fmt.as_date(day[0].get("data_date")) if day else None


def run(args) -> dict:
    out = {"ok": True, "command": moves.COMMAND, "data_date": None, "text": "", "flags": {"portal": True},
           "files": [], "warnings": []}
    present, rows, portal_d = read_portal()
    if not present:
        return dict(out, ok=False, exit=2, text=f"{VERDICT} {NOT_YET} (it comes with r9_stock_moves_migration.sql).",
                    flags={"portal": True, "verdict": "not checked"})
    from app import transfer_audit as ta
    prefer = getattr(args, "ledger", None) or "auto"
    try:
        audits = [moves.audit(w, prefer=prefer) for w in WINDOWS]
    except moves.MovesInput as exc:
        return dict(out, ok=False, exit=1, text=str(exc), flags={"portal": True, "verdict": "not checked"})
    d = audits[0].d
    portal = ta.pairs_from_rows(rows, portal_d) if rows else _empty_result(portal_d or d)
    lines = moves._para(f"MOVES PORTAL  the portal's pairing (v_transfer_lines, {moves._plural(len(rows), 'line')}, "
                        f"paired by app/transfer_audit.py) against /yq-moves'; the stock ledger runs to "
                        f"{moves._day(d)}", rest="  ")
    lines += moves._para(f"/yq-moves: {audits[0].source['note']}", "  ")
    same_day = portal_d == d
    if not same_day:
        lines += moves._para(f"The portal's stock ledger runs to {moves._day(portal_d, d) if portal_d else 'no day'}, "
                             f"/yq-moves' to {moves._day(d)}: the two do not stand on the same data.", "  ")
    listed: dict[str, tuple] = {}             # number -> (day, line): every window's, each voucher once
    counted = 0
    for a in audits:
        win = a.window
        lo, hi = win["first"], win["last"]
        theirs, ours = fates(portal, lo, hi), fates(a.result, lo, hi)
        found = differences(theirs, ours)
        for day, no, line in found:
            listed.setdefault(no, (day, line))
        figures = count_differences(pairing.summarise(portal, first=lo, last=hi),
                                    pairing.summarise(a.result, first=lo, last=hi))
        counted += len(figures)
        head = f"{win['title']} ({moves._day(lo, d)} to {moves._day(hi, d)}): /yq-moves {_sizes(ours)}"
        if not found and not figures:
            lines += moves._para(f"{head}; every voucher and every count the same.", "  ")
            continue
        said = []
        if found:
            said.append(f"{moves._plural(len(found), 'voucher')} {'differs' if len(found) == 1 else 'differ'}")
        if figures:
            said.append("the counts differ (the portal's against /yq-moves'): " + "; ".join(figures))
        lines += moves._para(f"{head}; the portal {_sizes(theirs)}: " + "; ".join(said) + ".", "  ")
    ok = same_day and not listed and not counted
    flags = {"portal": True, "verdict": "PASS" if ok else "FAIL", "vouchers_differ": len(listed),
             "counts_differ": counted, "lines": len(rows)}
    if ok:
        lines.append(PASSED)
    else:
        what = []
        if not same_day:
            what.append("the ledger's last day")
        if listed:
            what.append(moves._plural(len(listed), "voucher"))
        if counted:
            what.append(moves._plural(counted, "count"))
        lines.append(f"{VERDICT} FAIL - {_joined(what)} differ between the portal and /yq-moves"
                     + (" (the vouchers below)." if listed else "."))
        order = sorted(listed.items(), key=lambda kv: _order(kv[1][0], kv[0]))
        for _no, (_day, line) in order[:SHOWN]:
            lines += moves._para(line, "  ", "      ")
        if len(listed) > SHOWN:
            lines.append(f"  and {fmt.units(len(listed) - SHOWN)} more.")
    text = "\n".join(moves._guarded(ln).rstrip() for ln in lines)
    return dict(out, ok=ok, exit=0 if ok else 1, data_date=d.isoformat(), text=text, flags=flags)
