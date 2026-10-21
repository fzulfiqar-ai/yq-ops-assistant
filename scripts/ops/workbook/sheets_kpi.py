"""The KPI and KPI log sheets of the month workbook: the month pack (scripts.ops.workbook.build) places them between
Daily and Data, from what scripts.ops.kpi.scorecard and scripts.ops.kpi_log.month_lines give.

Typed values and totals only. A row carries its KPI, target, figure, actual (one line of figures and totals),
status and source, never its evidence lines: those name refs, item codes and suppliers, and stay on the /yq-kpi
screen and in exports/ops/<D>/kpi-<M>.json. No person, shop or supplier is named on either sheet. The KPI sheet's
header counts each status with a COUNTIF over its own Status column (the 90-day plan's block the same over its
own), so a count can never drift from the rows under it; Book.evaluate() works the values out.

CONTRACT
  kpi_sheet(ws, card) -> dict
      ws    a workbook.book.Sheet of the month pack (the builder writes cells, widths and the page; the book saves)
      card  scripts.ops.kpi.scorecard(month), or its JSON (kpi.jsonable): figures may come as text
    {"first_row", "last_row", "status_col", "counts": {status: "D5"}, "plan": (first, last) | None,
     "plan_counts": {status: cell} | None}           0-based rows; the cells the header counts sit in
  kpi_log_sheet(ws, lines, title=None) -> dict
      lines  scripts.ops.kpi_log.month_lines(month): the log's lines of the month, every kind
    one row per kind of the log (KINDS, then any other kind found), with its lines, its refs, the states its refs
    stand in (their newest line) and its first and last event day; a total row of SUM formulas.
    {"first_row", "last_row", "total_row"}
"""
from __future__ import annotations

from scripts.ops import fmt
from scripts.ops.workbook import sheets_core
from scripts.ops.workbook.book import Sheet, a1, rng

STATUSES = ("MET", "NOT MET", "NOT EVIDENCED", "NOTHING DUE")      # scripts.ops.kpi.STATUSES (a test holds them equal)
UNIT_FORMAT = {"share": "g_pct", "bhd": "g_money3", "days": "g_int", "count": "g_int", "units": "g_int"}
KIND_WORDS = (("promise", "Orders placed (goods on the way)"), ("candidate", "Products assessed (candidates)"),
              ("quote", "Quotations"), ("terms", "Terms agreed"), ("receipt_check", "Incoming checks"),
              ("claim", "Claims"), ("report", "Reports sent"))
NOTE = ("Totals only: what each row rests on (the refs, codes and receipts) is on the /yq-kpi screen and in "
        "exports/ops, never on this sheet.")


def _number(x) -> float | None:
    if x is None or x == "":
        return None
    try:
        v = fmt.dec(x) if not isinstance(x, (int, float)) else x
        return float(v)
    except (ValueError, ArithmeticError):
        return None


def _page(ws: Sheet) -> None:
    ws.page.update({"margins": dict(sheets_core.MARGINS), "footer": sheets_core.footer(ws.name), "footer_margin": 0.25,
                    "landscape": True, "fit": (1, 0), "paper": 9})


def _counts(ws: Sheet, r: int, c: int, status_col: int, first: int, last: int) -> dict:
    """The four statuses side by side from column c, each with its COUNTIF over the status column under it."""
    out = {}
    span = rng(first, status_col, last, status_col, True)
    for i, s in enumerate(STATUSES):
        ws.text(r, c + i, s, "g_head")
        ws.formula(r + 1, c + i, f'COUNTIF({span},"{s}")', "g_int")
        out[s] = a1(r + 1, c + i)
    return out


def kpi_sheet(ws: Sheet, card: dict) -> dict:
    """The KPI sheet (see the module docstring)."""
    rows = list(card.get("rows") or [])
    label = card.get("label") or str(card.get("month") or "")
    for c1, c2, width in ((0, 0, 5.7), (1, 1, 28), (2, 2, 30), (3, 3, 11.5), (4, 4, 58), (5, 5, 16), (6, 6, 40)):
        ws.col(c1, c2, width)
    ws.text(0, 0, f"KPI scorecard - {label}", "sheet_title")
    data_to = fmt.as_date(card.get("data_date"))
    sub = (f"Data to {fmt.day_long(data_to)}" if data_to else "Data date not known") + \
        ("; the month is not over" if card.get("partial") else "")
    marker = (card.get("log") or {}).get("marker")
    if marker:
        words = f"the KPI log {marker}" if str(marker).startswith("log started") else str(marker).rstrip(".")
        sub += "; " + words[0].lower() + words[1:]
    ws.text(1, 0, sub, "sheet_sub")
    head, first = 6, 7
    last = first + max(len(rows), 1) - 1
    status_col = 5
    ws.text(3, 1, "Rows by status", "subhead_small")
    counts = _counts(ws, 3, 2, status_col, first, last)
    for c, (text, style_) in enumerate((("#", "g_head"), ("KPI", "g_head"), ("Target", "g_head"), ("Figure", "g_head_r"),
                                        ("Actual", "g_head"), ("Status", "g_head"), ("Source", "g_head"))):
        ws.text(head, c, text, style_)
    for i, r in enumerate(rows):
        row = first + i
        ws.text(row, 0, str(r.get("key") or ""), "g_text")
        ws.text(row, 1, str(r.get("kpi") or ""), "g_text_wrap")
        ws.text(row, 2, str(r.get("target") or ""), "g_text_wrap")
        value = _number(r.get("value"))
        ws.put(row, 3, value, UNIT_FORMAT.get(r.get("unit") or "", "g_dec2"))
        ws.text(row, 4, str(r.get("actual") or ""), "g_text_wrap")
        ws.text(row, 5, str(r.get("status") or ""), "g_text")
        ws.text(row, 6, str(r.get("source") or ""), "g_text_wrap")
    if not rows:
        for c in range(7):
            ws.blank(first, c, "g_text")
    out = {"first_row": first, "last_row": last, "status_col": status_col, "counts": counts, "plan": None,
           "plan_counts": None}
    plan = card.get("plan") or {}
    prow = last + 2
    ws.text(prow, 0, "90-day plan", "subhead")
    plan_rows = list(plan.get("rows") or [])
    if not plan_rows:
        ws.text(prow + 1, 0, str(plan.get("note") or "No targets are set for this month.").rstrip(".") + ".", "note")
        ws.text(prow + 3, 0, NOTE, "footnote")
        _page(ws)
        return out
    p_head, p_first = prow + 3, prow + 4
    p_last = p_first + len(plan_rows) - 1
    out["plan"] = (p_first, p_last)
    out["plan_counts"] = _counts(ws, prow + 1, 2, 5, p_first, p_last)
    for c, (text, style_) in enumerate((("", "g_head"), ("Target", "g_head"), ("Planned", "g_head"),
                                        ("Figure", "g_head_r"), ("Actual", "g_head"), ("Status", "g_head"),
                                        ("Source", "g_head"))):
        ws.text(p_head, c, text, style_)
    for i, p in enumerate(plan_rows):
        row = p_first + i
        ws.blank(row, 0, "g_text")
        ws.text(row, 1, str(p.get("name") or ""), "g_text_wrap")
        ws.text(row, 2, str(p.get("target") or ""), "g_text")
        ws.put(row, 3, _number(p.get("value")), UNIT_FORMAT.get(p.get("unit") or "", "g_dec2"))
        ws.text(row, 4, str(p.get("actual") or ""), "g_text")
        ws.text(row, 5, str(p.get("status") or ""), "g_text")
        ws.text(row, 6, str(p.get("source") or ""), "g_text_wrap")
    ws.text(p_last + 2, 0, NOTE, "footnote")
    _page(ws)
    return out


def kpi_log_sheet(ws: Sheet, lines, title: str | None = None) -> dict:
    """The KPI log sheet (see the module docstring): totals per kind, no ref, note or name."""
    lines = [r for r in lines or [] if isinstance(r, dict)]
    known = [k for k, _w in KIND_WORDS]
    others = sorted({str(r.get("kind")) for r in lines if r.get("kind") and r.get("kind") not in known})
    kinds = [(k, w) for k, w in KIND_WORDS] + [(k, k) for k in others]
    for c1, c2, width in ((0, 0, 34), (1, 2, 10), (3, 3, 44), (4, 5, 14)):
        ws.col(c1, c2, width)
    ws.text(0, 0, title or "KPI log", "sheet_title")
    ws.text(1, 0, "The lines of the KPI log whose event day falls in the month, by kind: what was logged, never who "
                  "or what it names.", "sheet_sub")
    head, first = 3, 4
    for c, (text, style_) in enumerate((("Kind", "g_head"), ("Lines", "g_head_r"), ("Refs", "g_head_r"),
                                        ("States (each ref's newest line)", "g_head"), ("First event", "g_head_r"),
                                        ("Last event", "g_head_r"))):
        ws.text(head, c, text, style_)
    for i, (kind, words) in enumerate(kinds):
        row = first + i
        mine = [r for r in lines if r.get("kind") == kind]
        newest: dict[str, dict] = {}
        for r in mine:
            newest[str(r.get("ref") or r.get("id") or id(r))] = r
        states: dict[str, int] = {}
        for r in newest.values():
            s = str(r.get("state") or "open")
            states[s] = states.get(s, 0) + 1
        days = [d for d in (fmt.as_date(r.get("event_date")) for r in mine) if d is not None]
        ws.text(row, 0, words, "g_text")
        ws.put(row, 1, len(mine), "g_int")
        ws.put(row, 2, len(newest), "g_int")
        ws.text(row, 3, ", ".join(f"{s} {n}" for s, n in sorted(states.items())), "g_text_wrap")
        ws.put(row, 4, min(days) if days else None, "g_date")
        ws.put(row, 5, max(days) if days else None, "g_date")
    last = first + len(kinds) - 1
    total = last + 1
    ws.text(total, 0, "Total", "g_total_int")
    ws.formula(total, 1, f"SUM({rng(first, 1, last, 1)})", "g_total_int")
    ws.formula(total, 2, f"SUM({rng(first, 2, last, 2)})", "g_total_int")
    for c in (3, 4, 5):
        ws.blank(total, c, "g_total_int")
    _page(ws)
    return {"first_row": first, "last_row": last, "total_row": total}
