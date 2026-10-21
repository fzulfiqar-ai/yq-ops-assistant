"""The month workbook's Reconciliation sheet: the four month-end reconciliations, totals only.

    recon_sheet(ws, blocks)     ws: a book.Sheet the month pack made; blocks: scripts.ops.recon_blocks.month_blocks(month)

What it writes, top to bottom: the four statuses with what each reconciliation proves and what it reads without
its second side; the main store's count (lines, units and BHD short and over); one row per van and outlet (its
status, its signed count, its books-only figures) with a total; the sales and receivables (the load control's
three sources, the kickback statement, the receivables' roll-forward with a check formula that reads nought, the
returns); the purchases (receipts and their papers, as counts).

FORWARDED, SO TOTALS ONLY: no customer, no item, no voucher or invoice number is written anywhere on it. A van or
an outlet is named the way the month workbook already names a salesman or an outlet (the store without
' - Acc WH'); the reps whose kickback tier the returns would change are a count. Words follow the screen's: a
difference is "to check", a missing paper is an action. Numbers are typed values (BHD to three decimals, as the
other new sheets), the totals and the roll-forward's check are formulas the book evaluates. A block that was not
worked out reads one line saying so; the rest of the sheet stands.
"""
from __future__ import annotations

from scripts.ops import fmt
from scripts.ops.workbook.book import Sheet, a1

SHEET_NAME = "Reconciliation"
TAB_COLOR = "#184F95"
LABEL_COL, FIRST = 1, 2                 # column B holds the labels, C on the figures
WIDTHS = ((0, 0, 2.28515625), (1, 1, 38.7109375), (2, 12, 13.7109375))
NOT_WORKED = "Not worked out this time."
STATUS_WORDS = {"DIFFERS": "DIFFERS (to check)"}
TITLES = (("main", "Main store"), ("vans", "Vans and outlets"), ("sales", "Sales and receivables"),
          ("purchases", "Purchases"))
PROVES = {"main": "the main store's physical stock",
          "vans": "what is on each van and in each outlet",
          "sales": "invoicing, collection and the kickback statement",
          "purchases": "quantities received against the order; the landed cost"}
WITHOUT = {"main": "not counted: it never reads as agreeing",
           "vans": "books only: the stock at cost and the items held over 30 days unsold",
           "sales": "collection not proven; net of returns an estimate from returned units",
           "purchases": "a missing paper is an action, never a difference"}
VAN_NOTES = {"no signed count of the month": "no count", "not signed": "not signed", "waits": "waits for a load",
             "no line counted": "no line counted"}


def _status(s) -> str:
    return "NOT WORKED OUT" if s is None else STATUS_WORDS.get(s, s)


def _num(x):
    """A figure as the sheet types it: an int when whole, else a float (a Decimal never reaches a cell)."""
    if x is None:
        return None
    d = fmt.dec(x)
    return int(d) if d == d.to_integral_value() else float(d)


def _bhd(x):
    return None if x is None else float(fmt.q3(x))


def _label(store: str) -> str:
    return str(store or "").replace(" - Acc WH", "").strip()


def _day(x) -> str:
    d = fmt.as_date(x)
    return d.strftime("%d %b %Y").lstrip("0") if d else "-"


class _Writer:
    def __init__(self, ws: Sheet):
        self.ws = ws
        self.r = 0

    def text(self, c: int, value, fmt_: str = "g_text") -> None:
        self.ws.text(self.r, c, "" if value is None else str(value), fmt_)

    def num(self, c: int, value, fmt_: str = "g_int") -> None:
        self.ws.put(self.r, c, value, fmt_)

    def line(self, text: str, fmt_: str = "note") -> None:
        self.ws.merge(self.r, LABEL_COL, self.r, 12, text, fmt_)
        self.r += 1

    def section(self, text: str) -> None:
        self.r += 1
        self.ws.text(self.r, LABEL_COL, text, "section")
        self.ws.blanks(self.r, FIRST, 12, "section")
        self.r += 1

    def head(self, labels: list[str], first_label: str = "") -> None:
        self.ws.text(self.r, LABEL_COL, first_label, "g_head")
        for k, label in enumerate(labels):
            self.ws.text(self.r, FIRST + k, label, "g_head_r")
        self.r += 1

    def pairs(self, rows: list[tuple]) -> None:
        """Label | value rows: (label, value, format)."""
        for label, value, fmt_ in rows:
            self.text(LABEL_COL, label)
            if fmt_ == "g_date":
                d = fmt.as_date(value)
                if d:
                    self.ws.put(self.r, FIRST, d, "g_date")
                else:
                    self.text(FIRST, "-")
            elif fmt_ == "text":
                self.text(FIRST, value)
            else:
                self.num(FIRST, value, fmt_)
            self.r += 1


def _top(w: _Writer, m: dict) -> dict:
    ws = w.ws
    ws.text(0, LABEL_COL, SHEET_NAME, "sheet_title")
    words = m.get("words") or ""
    if m.get("whole"):
        sub = (f"{words}: the four month-end reconciliations. Data to {_day(m.get('data_date'))}; the books as at the "
               f"end of {_day(m.get('books_as_of'))}.")
    else:
        sub = (f"{words}: the four month-end reconciliations, not whole yet: the data runs to "
               f"{_day(m.get('data_date'))}.")
    ws.text(1, LABEL_COL, sub, "sheet_sub")
    ws.text(2, LABEL_COL, "A difference is to check; a missing paper is an action; nothing reads as agreeing without its "
                          "second side. Totals only: the items, the accounts and the vouchers are on the screen.", "note")
    w.r = 3
    w.section("The four reconciliations")
    w.ws.text(w.r, LABEL_COL, "Reconciliation", "g_head")
    w.ws.text(w.r, FIRST, "Status", "g_head")
    w.ws.merge(w.r, FIRST + 1, w.r, FIRST + 4, "It proves", "g_head")
    w.ws.merge(w.r, FIRST + 5, w.r, 12, "Without its second side", "g_head")
    w.r += 1
    first = w.r
    statuses = m.get("statuses") or {}
    for k, title in TITLES:
        w.text(LABEL_COL, title)
        w.text(FIRST, _status(statuses.get(k)))
        w.ws.merge(w.r, FIRST + 1, w.r, FIRST + 4, PROVES[k], "g_text")
        w.ws.merge(w.r, FIRST + 5, w.r, 12, WITHOUT[k], "g_text")
        w.r += 1
    return {"statuses": f"{a1(first, FIRST)}:{a1(w.r - 1, FIRST)}"}


def _main(w: _Writer, b: dict) -> None:
    w.section("1. Main store")
    v = b.get("count")
    if b.get("status") is None:
        w.line(NOT_WORKED)
        return
    if v is None:
        w.line("No count of the main store for the month is on file: the books alone prove nothing physical.")
        return
    if v.get("waits"):
        w.line(f"A count of {_day(v.get('count_date'))} is on file; its book side waits for a load with data to "
               f"{_day(v.get('book_day'))}.")
        return
    if (v.get("counted", 0) + v.get("added", 0)) == 0:
        w.line(f"The count of {_day(v.get('count_date'))} has no line counted.")
        return
    costed = v.get("costed") and v.get("short_bhd") is not None
    rows = [("Counted on", v.get("count_date"), "g_date"), ("The books at the end of", v.get("book_day"), "g_date"),
            ("Lines on the sheet", _num(v.get("lines")), "g_int"), ("Lines counted", _num(v.get("counted")), "g_int"),
            ("Lines added (found, not listed)", _num(v.get("added")), "g_int"),
            ("Lines that differ from the book", _num(v.get("differ")), "g_int"),
            ("Units short", _num(v.get("short_units") or 0), "g_int"),
            ("Units over", _num(v.get("over_units") or 0), "g_int")]
    if costed:
        rows += [("BHD short, at landed cost", _bhd(v.get("short_bhd")), "g_money3"),
                 ("BHD over, at landed cost", _bhd(v.get("over_bhd")), "g_money3")]
    w.pairs(rows)
    if not costed:
        w.line("BHD: not worked out this time (no unit cost was read with the count).")


def _van_note(row: dict) -> str:
    why = row.get("why") or ""
    if not why:
        return ""
    for key, word in VAN_NOTES.items():
        if key in why:
            return word
    return ""


def _vans(w: _Writer, b: dict) -> dict:
    w.section("2. Vans and outlets")
    if b.get("status") is None:
        w.line(NOT_WORKED)
        return {}
    rows = b.get("rows") or []
    if not rows:
        w.line("No van or outlet held an accessory at the end of the month or moved one in it: nothing to count.")
        return {}
    heads = ["Status", "Lines", "Lines counted", "Units short", "Units over", "BHD short", "BHD over",
             "Units on the books", "BHD at cost", "Held over 30 days unsold", "Note"]
    w.head(heads, "Store (signed count, else the books only)")
    first = w.r
    for row in rows:
        w.text(LABEL_COL, _label(row.get("store")))
        w.text(FIRST, _status(row.get("status")))
        only = row.get("status") == "BOOKS ONLY"
        v = None if only else row.get("count")
        bk = (row.get("books") or {}) if only else {}
        w.num(FIRST + 1, _num(v["lines"]) if v else None)
        w.num(FIRST + 2, _num(v["counted"] + v["added"]) if v else None)
        w.num(FIRST + 3, _num(v["short_units"] or 0) if v else None)
        w.num(FIRST + 4, _num(v["over_units"] or 0) if v else None)
        w.num(FIRST + 5, _bhd(v["short_bhd"]) if v and v.get("costed") else None, "g_money3")
        w.num(FIRST + 6, _bhd(v["over_bhd"]) if v and v.get("costed") else None, "g_money3")
        w.num(FIRST + 7, _num(bk.get("units")) if bk else None)
        w.num(FIRST + 8, _bhd(bk.get("bhd")) if bk else None, "g_money3")
        w.num(FIRST + 9, _num(bk.get("held_items")) if bk else None)
        w.text(FIRST + 10, _van_note(row))
        w.r += 1
    last = w.r - 1
    w.text(LABEL_COL, "Total", "g_head")
    w.text(FIRST, "", "g_head")
    for k in range(1, 10):
        c = FIRST + k
        money = k in (5, 6, 8)
        w.ws.formula(w.r, c, f"SUM({a1(first, c)}:{a1(last, c)})", "g_total_money3" if money else "g_total_int")
    w.text(FIRST + 10, "", "g_head")
    w.r += 1
    if b.get("books_note") and not any(r.get("books") for r in rows):
        w.line(f"The books-only figures: {b['books_note']}.")
    return {"vans_total": a1(w.r - 1, FIRST + 3)}


def _sales(w: _Writer, b: dict) -> dict:
    w.section("3. Sales and receivables")
    refs: dict = {}
    if b.get("status") is None and not any(b.get(k) for k in ("invoicing", "statement", "collection", "returns")):
        w.line(NOT_WORKED)
        return refs
    inv = b.get("invoicing")
    w.line("Invoicing, the load control: the day book = the register = v_sales, invoice by invoice (BHD, VAT included).",
           "subhead_small")
    if inv is None:
        w.line(NOT_WORKED)
    else:
        w.head(["Invoices", "Lines", "BHD"], "Source")
        sources = ([("Day book (the Focus file)", inv.get("daybook")), ("Register (the Focus file)", inv.get("register"))]
                   if inv.get("basis") == "files" else []) + [("v_sales (the portal)", inv.get("portal"))]
        for label, side in sources:
            side = side or {}
            w.text(LABEL_COL, label)
            w.num(FIRST, _num(side.get("invoices")))
            w.num(FIRST + 1, _num(side.get("lines")))
            w.num(FIRST + 2, _bhd(side.get("gross")), "g_money3")
            w.r += 1
        if inv.get("basis") == "files":
            w.pairs([("Invoices to check", len(inv.get("differences") or []), "g_int")])
        else:
            w.line("Not set beside the files this time: it stands on each load's own verify.")
    st = b.get("statement")
    w.line("The kickback statement, as drafted (returns not deducted).", "subhead_small")
    if st is None:
        w.line(NOT_WORKED)
    else:
        w.pairs([("Reps with a target", st.get("count"), "g_int"), ("Their sales, BHD ex-VAT", _bhd(st.get("sales")),
                                                                     "g_money3"),
                 ("Kickback at the tiers reached, BHD", _bhd(st.get("kickback")), "g_money3")])
    col = b.get("collection") or {}
    w.line("Collection: customer accounts, the Ledger export set beside the receivables ageing.", "subhead_small")
    totals = col.get("totals")
    if totals:
        w.head(["Opening", "Sales invoices", "Receipts", "Returns", "Credit notes", "Other", "Closing", "Check"], "BHD")
        w.text(LABEL_COL, "Customer accounts")
        for k, key in enumerate(("opening", "si", "rctv", "srv", "cn", "other", "closing")):
            w.num(FIRST + k, _bhd(totals.get(key)), "g_money3")
        c = [a1(w.r, FIRST + k) for k in range(7)]
        w.ws.formula(w.r, FIRST + 7, f"ROUND({c[0]}+{c[1]}-{c[2]}-{c[3]}-{c[4]}+{c[5]}-{c[6]},3)", "g_money3")
        refs["roll_check"] = a1(w.r, FIRST + 7)
        w.r += 1
        compared = col.get("ar_day") is not None and col.get("status") != "COLLECTION NOT PROVEN"
        rows = [("Customer accounts in the export", col.get("accounts"), "g_int")]
        if compared:
            rows += [("Set beside the ageing as on", col.get("ar_day"), "g_date"),
                     ("Agree with the ageing", col.get("agree"), "g_int"),
                     ("To check", len(col.get("to_check") or []), "g_int")]
        w.pairs(rows)
        if not compared and col.get("why"):
            w.line(f"Not set beside the ageing: {col['why']}.")
    else:
        w.line(f"Collection not proven: {col.get('why') or NOT_WORKED}.")
    ret = b.get("returns")
    w.line("Returns of the month.", "subhead_small")
    if ret is None:
        w.line(NOT_WORKED)
    elif ret.get("basis") == "export":
        w.pairs([("Returns, BHD ex-VAT (the Ledger export)", _bhd(ret.get("total")), "g_money3"),
                 ("Reps whose kickback tier they would change", len(ret.get("changed") or []), "g_int")])
    else:
        rows = [("Units returned (the stock ledger)", _num(ret.get("units")), "g_int")]
        if ret.get("value") is not None:
            rows.append(("An estimate of their value, BHD ex-VAT", _bhd(ret.get("value")), "g_money3"))
        w.pairs(rows)
        w.line("Net of returns is an estimate from returned units: the Ledger export holds their values.")
    return refs


def _purchases(w: _Writer, b: dict) -> None:
    w.section("4. Purchases")
    if b.get("status") is None:
        w.line(NOT_WORKED)
        return
    t = b.get("totals") or {}
    if not t.get("receipts"):
        w.line("No supplier receipt into the main store in the month: no paper is due.")
    w.head(["Receipts", "PO on file", "MRN XML on file", "Supplier invoice on file", "Cost lines", "Lines", "To check",
            "Papers missing", "Into another store"], "Supplier receipts into the main store")
    w.text(LABEL_COL, "Count")
    for k, key in enumerate(("receipts", "po", "xml", "invoice", "cost_lines", "lines", "differs", "papers_missing",
                             "elsewhere")):
        value = t.get(key, 0)
        w.num(FIRST + k, None if value is None else _num(value))      # None: not worked out this time (a blank)
    w.r += 1
    if t.get("receipts") and t.get("cost_lines") is None:
        w.line("Cost lines: not worked out this time (the item codes could not be read).")


def recon_sheet(ws: Sheet, blocks: dict) -> dict:
    """Write the Reconciliation sheet onto `ws` from month_blocks' answer. Returns where its figures stand:
    {statuses: the four status cells, roll_check: the roll-forward's check cell (it reads nought) when there is one}."""
    for c1, c2, width in WIDTHS:
        ws.col(c1, c2, width)
    if "blocks" not in blocks and any(k in blocks for k, _t in TITLES):     # handed the four blocks alone
        blocks = {"blocks": blocks, "statuses": {k: (blocks.get(k) or {}).get("status") for k, _t in TITLES}}
    w = _Writer(ws)
    refs = _top(w, blocks)
    parts = blocks.get("blocks") or {}
    _main(w, parts.get("main") or {"status": None})
    refs.update(_vans(w, parts.get("vans") or {"status": None}))
    refs.update(_sales(w, parts.get("sales") or {"status": None}))
    _purchases(w, parts.get("purchases") or {"status": None})
    ws.page.update({"landscape": True, "fit": (1, 0), "margins": {"left": 0.4, "right": 0.4, "top": 0.5, "bottom": 0.55},
                    "hide_gridlines": True})
    return refs


def numbers_of(ws: Sheet) -> list:
    """Every typed number on the sheet (tests and the month pack's own checks read it)."""
    return [cell.value for cell in ws.cells.values() if cell.kind == "n" and isinstance(cell.value, (int, float))
            and not isinstance(cell.value, bool)]


def texts_of(ws: Sheet) -> list[str]:
    return [cell.value for cell in ws.cells.values() if cell.kind == "s" and cell.value]
