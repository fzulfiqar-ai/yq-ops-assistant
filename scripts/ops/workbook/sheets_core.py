"""The eight sheets of the Sep 2026 monthly report, rebuilt for any whole month.

Layout, names, formulas and formats are the Sep 2026 file's (YQ Bahrain - Mobile Accessories Sales Report
- Sep 2026). Every position that depends on the data (the number of salesmen, categories, products,
customers and lines) is worked out from the list lengths in Layout; every figure is a formula over the
Data sheet, evaluated by book.py so the file carries its values.

Words: the Highlights, the Points to note and three notes beside the tables are commentary (Commentary):
until the approval step of a later phase writes them they read DRAFT. Every other sentence is generated
here from the data and the rule in force (the footnotes, the Notes sheet's basis of preparation).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime

from scripts.ops.workbook import categories as cats
from scripts.ops.workbook import model as mdl
from scripts.ops.workbook import style
from scripts.ops.workbook.book import Book, Sheet, a1, aa, rng

COMPANY = "YQ Bahrain W.L.L"
FOOTER_LEFT = "YQ Bahrain W.L.L - Mobile accessories sales report - {sheet}"
MARGINS = {"left": 0.4, "right": 0.4, "top": 0.5, "bottom": 0.55}
TOP_N = 10                                   # the Dashboard's top products / accounts / salesmen
WEEK_BLOCKS = (("1 to 7", 1, 7), ("8 to 14", 8, 14), ("15 to 21", 15, 21), ("22 to 28", 22, 28),
               ("29 to month end", 29, 31))
DAYS = 31                                    # rows of the Daily sheet (one of two months in a row has 31 days)
DRAFT = "DRAFT"


def footer(sheet: str | None) -> str:
    left = COMPANY if sheet is None else FOOTER_LEFT.format(sheet=sheet)
    return f'&L&8&"Arial,Regular"{left}&R&8&"Arial,Regular"Page &P of &N'


@dataclass
class Commentary:
    """The written words of the report: five Highlights, five Points to note, and the notes under the
    top products, under the top accounts and on the Customers sheet. A slot left empty reads DRAFT."""
    highlights: list[str] = field(default_factory=list)
    points: list[str] = field(default_factory=list)
    products_note: str = ""
    accounts_note: str = ""
    customers_note: str = ""

    def highlight(self, i: int) -> str:
        return (self.highlights[i] if i < len(self.highlights) and self.highlights[i] else
                f"{DRAFT}: highlight {i + 1} of 5, written from the month's facts once they are approved.")

    def point(self, i: int) -> str:
        return (self.points[i] if i < len(self.points) and self.points[i] else
                f"{DRAFT}: point to note {i + 1} of 5, written from the month's facts once they are approved.")

    def note(self, slot: str) -> str:
        text = getattr(self, slot)
        if text:
            return text
        what = {"products_note": "the bulk orders inside the top products",
                "accounts_note": "accounts that appear under more than one name",
                "customers_note": "similar account names"}[slot]
        return f"{DRAFT}: a note on {what}, written once it is approved."

    @property
    def is_draft(self) -> bool:
        return not (len([h for h in self.highlights if h]) == 5 and len([p for p in self.points if p]) == 5)


class Layout:
    """Where every block starts and ends (0-based rows), from the list lengths."""

    def __init__(self, model: mdl.Model):
        self.S = len(model.reps)
        self.K = len(model.categories)
        self.P = len(model.products)
        self.H = len(model.not_counted)
        self.C = len(model.customers)
        self.N = len(model.lines)
        # Salesmen
        self.sal_first, self.sal_last = 5, 5 + self.S - 1
        self.sal_total = 5 + self.S
        self.week_head = self.sal_total + 4
        self.week_first = self.week_head + 2
        self.week_last = self.week_first + self.S - 1
        self.week_total = self.week_last + 1
        # Categories
        self.cat_first, self.cat_last = 5, 5 + self.K - 1
        self.cat_total = 5 + self.K
        self.mat_head = self.cat_total + 4
        self.mat_first = self.mat_head + 2
        self.mat_last = self.mat_first + self.S - 1
        self.mat_total = self.mat_last + 1
        # Products
        self.prod_first, self.prod_last = 5, 5 + self.P - 1
        self.prod_total = 5 + self.P
        self.nc_head = self.prod_total + 1
        self.nc_first = self.nc_head + 1
        self.prod_end = self.nc_first + self.H - 1 if self.H else self.prod_total
        # Customers
        self.cust_first, self.cust_last = 5, 5 + self.C - 1
        self.cust_total = 5 + self.C
        # Data
        self.data_first, self.data_last = 4, 4 + max(self.N, 1) - 1


# ── small helpers ──────────────────────────────────────────────────────────────────────────────

def col_range(sheet: str, c: int, r1: int, r2: int) -> str:
    return f"{sheet}!{aa(r1, c)}:{aa(r2, c)}"


def ex(r: int) -> int:
    """0-based row -> the row number Excel shows."""
    return r + 1


def text_rows(text: str, per_line: int) -> int:
    return max(1, math.ceil(len(text) / per_line))


def comment_height(*texts: str) -> float:
    lines = max(2, max(text_rows(t, 96) for t in texts))
    return 6 + 11.25 * lines


def para_height(text: str) -> float:
    return 27 if len(text) > 170 else 15.75


def _join(words: list[str]) -> str:
    words = [w for w in words if w]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _units(n: float) -> str:
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,.2f}"


# ── the book ───────────────────────────────────────────────────────────────────────────────────

@dataclass
class Facts:
    """The few figures the generated sentences need, worked out from the lines (not from a formula)."""
    totals: dict                        # v_sales month totals: {'prev'|'cur': {'lines','qty','gross','net'}}
    prepared_on: date
    prepared_by: str = ""
    outlets: tuple = ("Causeway", "YQ Roadshow")


def build(model: mdl.Model, facts: Facts, commentary: Commentary | None = None) -> Book:
    commentary = commentary or Commentary()
    lay = Layout(model)
    book = Book()
    p = model.period
    book.properties = {
        "title": f"Mobile Accessories Sales Report - {p.long}", "subject": "Monthly sales report",
        "author": facts.prepared_by or "", "company": COMPANY,
        "created": datetime(facts.prepared_on.year, facts.prepared_on.month, facts.prepared_on.day, 12, 0, 0),
    }
    dash = book.sheet("Dashboard", tab_color=style.BLUE)
    sal = book.sheet("Salesmen")
    cat = book.sheet("Categories")
    prod = book.sheet("Products")
    cust = book.sheet("Customers")
    daily = book.sheet("Daily")
    data = book.sheet("Data", tab_color=style.MUTED)
    notes = book.sheet("Notes", tab_color=style.MUTED)

    _names(book, lay)
    _dashboard(dash, model, lay, facts, commentary)
    _salesmen(sal, model, lay)
    _categories(cat, model, lay)
    _products(prod, model, lay)
    _customers(cust, model, lay, commentary)
    _daily(daily, model)
    _data(data, model, lay)
    _notes(notes, model, lay, facts)
    book.commentary = {"Dashboard": [f"B{15 + i}" for i in range(5)] + [f"K{15 + i}" for i in range(5)] + ["K54", "B73"],
                       "Customers": [f"B{ex(lay.cust_total) + 2}"]}
    return book


def _names(book: Book, lay: Layout) -> None:
    d1, d2 = lay.data_first, lay.data_last
    for name, c in (("d_Date", 0), ("d_Cust", 2), ("d_Item", 3), ("d_Qty", 4), ("d_Gross", 6), ("d_Net", 7),
                    ("d_Month", 10), ("d_Rep", 11), ("d_Cat", 12), ("d_VAT", 13), ("d_Units", 15),
                    ("d_InvFlag", 16), ("d_AcctFlag", 17), ("d_RepAcctFlag", 18)):
        book.define(name, col_range("Data", c, d1, d2))
    book.define("p_Cat", col_range("Products", 3, lay.prod_first, lay.prod_end))
    book.define("p_Item", col_range("Products", 12, lay.prod_first, lay.prod_end))
    for name, r in (("CurMonth", 4), ("PrevMonth", 5), ("CashAcct", 6), ("HandsetCat", 7), ("WhSuffix", 8),
                    ("PreparedBy", 9), ("PreparedOn", 10), ("CurShort", 58), ("PrevShort", 59),
                    ("CurLabel", 60), ("PrevLabel", 61), ("CurLong", 62), ("PrevLong", 63), ("PreparedText", 64)):
        book.define(name, f"Notes!{aa(r, 2)}")


def _page(ws: Sheet, **kw) -> None:
    ws.page.update({"margins": dict(MARGINS), "footer": footer(ws.name), "footer_margin": 0.25})
    ws.page.update(kw)


# ── Dashboard ──────────────────────────────────────────────────────────────────────────────────

def _dashboard(ws: Sheet, model: mdl.Model, lay: Layout, facts: Facts, words: Commentary) -> None:
    p = model.period
    ws.col(0, 0, 2.28515625)
    ws.col(1, 8, 10.28515625)
    ws.col(9, 9, 3.42578125)
    ws.col(10, 17, 10.28515625)
    ws.col(18, 18, 2.28515625)
    for r, h in ((0, 4.5), (1, 9), (2, 12), (3, 24), (4, 13.5), (5, 6), (6, 9), (7, 13.5), (8, 39), (9, 13.5),
                 (10, 12), (11, 6), (12, 10.5), (13, 18), (19, 12), (20, 18), (21, 13.5), (37, 12), (38, 18),
                 (39, 13.5), (55, 12), (56, 18), (57, 13.5), (65, 18), (74, 10.5), (75, 4.5), (76, 11.25),
                 (77, 11.25), (78, 11.25)):
        ws.row(r, h)
    ws.blanks(0, 0, 18, "band")
    ws.merge(2, 1, 2, 8, "YQ BAHRAIN W.L.L", "company")
    ws.merge(2, 10, 2, 17, None, "meta_r",
             formula='IF(PreparedBy="","Prepared ","Prepared by "&PreparedBy&", ")&PreparedText')
    ws.merge(3, 1, 3, 17, "Mobile Accessories Sales Report", "report_title")
    ws.merge(4, 1, 4, 8, None, "report_sub", formula='CurLong&" compared with "&PrevLong')
    ws.merge(4, 10, 4, 17, "Source: ERP sales day book", "meta_r")
    ws.blanks(5, 1, 17, "rule_medium")

    # the KPI tiles (rows 8 to 11)
    sal_d = rng(lay.sal_first, 3, lay.sal_last, 3)
    sal_c = rng(lay.sal_first, 2, lay.sal_last, 2)
    ws.merge(7, 1, 7, 4, "Net sales (BHD, excluding VAT)", "note")
    ws.merge(8, 1, 8, 4, None, "tile_money", formula="SUMIFS(d_Net,d_Month,CurMonth)")
    ws.formula(9, 1, "IF(C10=0,0,B9/C10-1)", "tile_change_first")
    ws.merge(9, 2, 9, 4, None, "tile_from_int", formula="SUMIFS(d_Net,d_Month,PrevMonth)")
    ws.merge(10, 1, 10, 4, None, "note",
             formula='"BHD "&FIXED(SUMIFS(d_Gross,d_Month,CurMonth),0)&" including VAT of "&FIXED(SUMIFS(d_VAT,d_Month,CurMonth),0)')
    tiles = (  # (first col, label, value formula, value format, change formula, from formula, from format)
        (5, "Units sold", "SUMIFS(d_Units,d_Month,CurMonth)", "tile_int", "IF(G10=0,0,F9/G10-1)",
         "SUMIFS(d_Units,d_Month,PrevMonth)", "tile_from_int"),
        (7, "Invoices", "SUMIFS(d_InvFlag,d_Month,CurMonth)", "tile_int", "IF(I10=0,0,H9/I10-1)",
         "SUMIFS(d_InvFlag,d_Month,PrevMonth)", "tile_from_int"),
        (10, "Average invoice (BHD)", "IF(H9=0,0,B9/H9)", "tile_1dp", "IF(L10=0,0,K9/L10-1)",
         "IF(I10=0,0,C10/I10)", "tile_from_1dp"),
        (12, "Average unit price (BHD)", "IF(F9=0,0,B9/F9)", "tile_2dp", "IF(N10=0,0,M9/N10-1)",
         "IF(G10=0,0,C10/G10)", "tile_from_2dp"),
        (14, "Accounts invoiced", "SUMIFS(d_AcctFlag,d_Month,CurMonth)", "tile_int", "IF(P10=0,0,O9/P10-1)",
         "SUMIFS(d_AcctFlag,d_Month,PrevMonth)", "tile_from_int"),
        (16, "Salesmen/outlets active", f'COUNTIF(Salesmen!{sal_d},">0")', "tile_int", "IF(R10=0,0,Q9/R10-1)",
         f'COUNTIF(Salesmen!{sal_c},">0")', "tile_from_int"),
    )
    for c, label, value, vfmt, change, frm, ffmt in tiles:
        ws.merge(7, c, 7, c + 1, label, "tile_label")
        ws.merge(8, c, 8, c + 1, None, vfmt, formula=value)
        ws.merge(10, c, 10, c + 1, None, "tile_foot")
        ws.formula(9, c, change, "tile_change")
        ws.formula(9, c + 1, frm, ffmt)
    for r in range(7, 11):
        ws.blank(r, 9, "gutter")
    ws.blanks(11, 1, 17, "rule_thin")

    # Highlights and Points to note (rows 14 to 19)
    ws.merge(13, 1, 13, 8, "Highlights", "section")
    ws.merge(13, 10, 13, 17, "Points to note", "section")
    for i in range(5):
        h, pt = words.highlight(i), words.point(i)
        ws.merge(14 + i, 1, 14 + i, 8, h, "comment")
        ws.merge(14 + i, 10, 14 + i, 17, pt, "comment")
        ws.row(14 + i, comment_height(h, pt))

    # chart headings
    ws.merge(20, 1, 20, 8, "Cumulative net sales by day of month", "section")
    ws.merge(21, 1, 21, 8, "BHD, excluding VAT. Running total from the first day of each month.", "note")
    ws.merge(20, 10, 20, 17, "Net sales by salesman", "section")
    top_reps = min(TOP_N, lay.S)
    ws.merge(21, 10, 21, 17, None, "note",
             formula=f'"BHD, excluding VAT. Top {top_reps} of "&COUNTA(Salesmen!{rng(lay.sal_first, 1, lay.sal_last, 1)})'
                     f'&" by "&CurLong&" sales; the rest are on the Salesmen sheet."')
    ws.merge(38, 1, 38, 8, "Net sales by category", "section")
    ws.merge(39, 1, 39, 8, "BHD, excluding VAT.", "note")
    ws.merge(38, 10, 38, 17, f"Top {TOP_N} products", "section")
    ws.merge(39, 10, 39, 17, None, "note", formula='"Net sales in BHD, ranked by "&CurLong&"."')

    # top products (rows 41 to 55)
    ws.merge(40, 10, 40, 14, "Product", "mini_th")
    ws.formula(40, 15, "PrevLabel", "mini_th_r")
    ws.formula(40, 16, "CurLabel", "mini_th_r")
    ws.text(40, 17, "Change", "mini_th_r")
    pr = lambda c: f"Products!{aa(lay.prod_first, c)}:{aa(lay.prod_last, c)}"  # noqa: E731
    for k in range(TOP_N):
        r = 41 + k
        if k < lay.P:
            ws.merge(r, 10, r, 14, None, "mini_td", formula=f"INDEX({pr(14)},MATCH({k + 1},{pr(13)},0))")
            ws.formula(r, 15, f"INDEX({pr(6)},MATCH({k + 1},{pr(13)},0))", "mini_int")
            ws.formula(r, 16, f"INDEX({pr(7)},MATCH({k + 1},{pr(13)},0))", "mini_int")
            ws.formula(r, 17, f"Q{ex(r)}-P{ex(r)}", "mini_change")
        else:
            ws.merge(r, 10, r, 14, None, "mini_td")
            for c in (15, 16):
                ws.blank(r, c, "mini_int")
            ws.blank(r, 17, "mini_change")
    ws.merge(51, 10, 51, 14, "Other products", "mini_td")
    ws.formula(51, 15, "P53-SUM(P42:P51)", "mini_int")
    ws.formula(51, 16, "Q53-SUM(Q42:Q51)", "mini_int")
    ws.formula(51, 17, "Q52-P52", "mini_change")
    ws.merge(52, 10, 52, 14, "Total", "mini_total")
    ws.formula(52, 15, f"Products!G{ex(lay.prod_total)}", "mini_total_int")
    ws.formula(52, 16, f"Products!H{ex(lay.prod_total)}", "mini_total_int")
    ws.formula(52, 17, "Q53-P53", "mini_total_change")
    ws.merge(53, 10, 54, 17, words.note("products_note"), "mini_note")

    # top accounts (rows 57 to 74) and the customer base, week of month (columns K to R)
    ws.merge(56, 1, 56, 8, f"Top {TOP_N} accounts", "section")
    ws.merge(57, 1, 57, 8, None, "note",
             formula='"Net sales in BHD, ranked by "&CurLong&". Cash customers are shown separately."')
    ws.merge(56, 10, 56, 17, "Customer base", "section")
    ws.merge(57, 10, 57, 17, "Accounts by the month they were invoiced in. Net sales in BHD.", "note")
    ws.merge(58, 1, 58, 5, "Customer", "mini_th")
    ws.formula(58, 6, "PrevLabel", "mini_th_r")
    ws.formula(58, 7, "CurLabel", "mini_th_r")
    ws.text(58, 8, "Change", "mini_th_r")
    cu = lambda c: f"Customers!{aa(lay.cust_first, c)}:{aa(lay.cust_last, c)}"  # noqa: E731
    accounts = len([c for c in model.customers if c != mdl.CASH_ACCOUNT])
    for k in range(TOP_N):
        r = 59 + k
        if k < accounts:
            ws.merge(r, 1, r, 5, None, "mini_td", formula=f"INDEX({cu(11)},MATCH({k + 1},{cu(10)},0))")
            ws.formula(r, 6, f"INDEX({cu(4)},MATCH({k + 1},{cu(10)},0))", "mini_int")
            ws.formula(r, 7, f"INDEX({cu(5)},MATCH({k + 1},{cu(10)},0))", "mini_int")
            ws.formula(r, 8, f"H{ex(r)}-G{ex(r)}", "mini_change")
        else:
            ws.merge(r, 1, r, 5, None, "mini_td")
            for c in (6, 7):
                ws.blank(r, c, "mini_int")
            ws.blank(r, 8, "mini_change")
    ws.merge(69, 1, 69, 5, "Other accounts", "mini_td")
    ws.formula(69, 6, "G72-SUM(G60:G69)-G71", "mini_int")
    ws.formula(69, 7, "H72-SUM(H60:H69)-H71", "mini_int")
    ws.formula(69, 8, "H70-G70", "mini_change")
    ws.merge(70, 1, 70, 5, "Cash customers", "mini_td")
    ws.formula(70, 6, f'SUMIFS({cu(4)},{cu(2)},"Cash")', "mini_int")
    ws.formula(70, 7, f'SUMIFS({cu(5)},{cu(2)},"Cash")', "mini_int")
    ws.formula(70, 8, "H71-G71", "mini_change")
    ws.merge(71, 1, 71, 5, "Total", "mini_total")
    ws.formula(71, 6, f"Customers!E{ex(lay.cust_total)}", "mini_total_int")
    ws.formula(71, 7, f"Customers!F{ex(lay.cust_total)}", "mini_total_int")
    ws.formula(71, 8, "H72-G72", "mini_total_change")
    ws.merge(72, 1, 73, 8, words.note("accounts_note"), "mini_note")

    ws.merge(58, 10, 58, 14, None, "mini_th")
    ws.text(58, 15, "Accounts", "mini_th_r")
    ws.formula(58, 16, "PrevLabel", "mini_th_r")
    ws.formula(58, 17, "CurLabel", "mini_th_r")
    base = (("Invoiced in both months", None, 'PrevShort&" & "&CurShort'),
            (None, '"Invoiced in "&CurShort&" only"', 'CurShort&" only"'),
            (None, '"Invoiced in "&PrevShort&" only"', 'PrevShort&" only"'))
    for k, (label, label_f, crit) in enumerate(base):
        r = 59 + k
        ws.merge(r, 10, r, 14, label, "mini_td", formula=label_f)
        ws.formula(r, 15, f"COUNTIF({cu(9)},{crit})", "mini_int")
        ws.formula(r, 16, f"SUMIFS({cu(4)},{cu(9)},{crit})", "mini_int")
        ws.formula(r, 17, f"SUMIFS({cu(5)},{cu(9)},{crit})", "mini_int")
    ws.merge(62, 10, 62, 14, "Cash customers", "mini_td")
    ws.blank(62, 15, "mini_int")
    ws.formula(62, 16, f'SUMIFS({cu(4)},{cu(9)},"Cash")', "mini_int")
    ws.formula(62, 17, f'SUMIFS({cu(5)},{cu(9)},"Cash")', "mini_int")
    ws.merge(63, 10, 63, 14, "Total", "mini_total")
    for c, col in ((15, "P"), (16, "Q"), (17, "R")):
        ws.formula(63, c, f"SUM({col}60:{col}63)", "mini_total_int")
    ws.merge(65, 10, 65, 17, "Net sales by week of month", "section")
    ws.merge(66, 10, 66, 13, "Days of the month", "mini_th")
    ws.formula(66, 14, "PrevLabel", "mini_th_r")
    ws.text(66, 15, "% of month", "mini_th_r")
    ws.formula(66, 16, "CurLabel", "mini_th_r")
    ws.text(66, 17, "% of month", "mini_th_r")
    for k in range(5):
        r = 67 + k
        ws.merge(r, 10, r, 13, None, "mini_td", formula=f"Daily!H{6 + k}")
        ws.formula(r, 14, f"Daily!I{6 + k}", "mini_int")
        ws.formula(r, 15, f"Daily!J{6 + k}", "mini_pct")
        ws.formula(r, 16, f"Daily!K{6 + k}", "mini_int")
        ws.formula(r, 17, f"Daily!L{6 + k}", "mini_pct")
    ws.merge(72, 10, 72, 13, "Total", "mini_total")
    ws.formula(72, 14, "Daily!I11", "mini_total_int")
    ws.formula(72, 15, "Daily!J11", "mini_total_pct")
    ws.formula(72, 16, "Daily!K11", "mini_total_int")
    ws.formula(72, 17, "Daily!L11", "mini_total_pct")

    # footnotes
    ws.blanks(75, 1, 17, "rule_thin")
    for k, text in enumerate(footnotes(model, facts)):
        ws.merge(76 + k, 1, 76 + k, 17, text, "footnote")

    # rules: the order they are added is their priority (as in the Sep file)
    for rule in style.change_rules(with_na=False):
        ws.cond("B10 F10 H10 K10 M10 O10 Q10", rule)
    for rule in style.change_rules():
        ws.cond("R42:R53", rule)
    ws.cond("Q42:Q51", dict(style.DATA_BAR))
    for rule in style.change_rules():
        ws.cond("I60:I72", rule)
    ws.cond("H60:H69", dict(style.DATA_BAR))
    ws.cond("P68:P72 R68:R72", dict(style.DATA_BAR))

    # charts
    prev_days = (model.period.prev_end - model.period.prev_start).days + 1
    cur_days = (model.period.end - model.period.start).days + 1
    ws.charts.append((22, 1, cumulative_chart(prev_days, cur_days),
                      {"object_position": 2, "description": "Line chart of cumulative net sales by day of month, "
                                                            "report month against comparison month."}))
    ws.charts.append((22, 10, bar_chart("Salesmen", 1, 2, 3, lay.sal_first, lay.sal_first + top_reps - 1),
                      {"object_position": 2, "description": "Bar chart of net sales for the ten largest salesmen, "
                                                            "report month against comparison month."}))
    ws.charts.append((40, 1, bar_chart("Categories", 1, 5, 6, lay.cat_first, lay.cat_last),
                      {"object_position": 2, "description": "Bar chart of net sales by product category, report "
                                                            "month against comparison month."}))
    ws.page.update({"landscape": True, "scale": 80, "center": True, "hide_headers": True,
                    "margins": {"left": 0.35, "right": 0.35, "top": 0.4, "bottom": 0.5},
                    "footer": footer(None), "footer_margin": 0.25, "breaks": [38], "print_area": "A1:S80",
                    "selection": "A2"})


def footnotes(model: mdl.Model, facts: Facts) -> list[str]:
    p = model.period
    first = (f"Net sales are the taxable value of sales invoices, excluding 10% VAT, from the ERP sales day book "
             f"for {p.span_text()}. Returns are not deducted. Figures are rounded, so columns may not add exactly.")
    second = "Units, invoices and accounts cover accessories only."
    if model.not_counted:
        codes = _join([it.code for it in model.not_counted])
        issuers = []
        names = {it.item for it in model.not_counted}
        for ln in model.lines:
            if ln.item in names and ln.rep not in issuers:
                issuers.append(ln.rep)
        said = _join([("the " + r) if r in facts.outlets else r for r in issuers])
        prev_units = sum(it.prev_qty for it in model.not_counted)
        cur_units = sum(it.cur_qty for it in model.not_counted)
        parts = []
        if prev_units:
            parts.append(f"{_units(prev_units)} units in {mdl.MONTHS[p.prev_start.month - 1]}")
        if cur_units:
            parts.append((f"{_units(cur_units)} in " if parts else f"{_units(cur_units)} units in ")
                         + mdl.MONTHS[p.start.month - 1])
        second += (f" {codes} handsets issued through {said} carry no value in the day book "
                   f"({', '.join(parts)}) and are excluded.")
    third = (f"Salesman is taken from the issuing warehouse on each invoice. An account is any customer other than "
             f"{mdl.CASH_ACCOUNT}.")
    return [first, second, third]


def cumulative_chart(prev_days: int, cur_days: int) -> dict:
    label = {"value": True, "num_format": "#,##0", "font": style.font(8.5)}
    return {
        "type": "line",
        "series": [
            {"name": "=Daily!$E$5", "categories": "=Daily!$B$6:$B$36", "values": f"=Daily!$E$6:$E${5 + prev_days}",
             "line": {"color": style.PALE, "width": 2.25}, "marker": {"type": "none"},
             "data_labels": dict(label, custom=[{"delete": True}] * (prev_days - 1))},
            {"name": "=Daily!$F$5", "categories": "=Daily!$B$6:$B$36", "values": f"=Daily!$F$6:$F${5 + cur_days}",
             "line": {"color": style.BLUE, "width": 2.25}, "marker": {"type": "none"},
             "data_labels": dict(label, custom=[{"delete": True}] * (cur_days - 1))},
        ],
        "x_axis": {"name": "Day of month", "name_font": style.font(8, style.MUTED, bold=False),
                   "num_font": style.font(8, style.MUTED), "line": {"color": style.LINE, "width": 0.75},
                   "major_tick_mark": "none", "interval_unit": 2},
        "y_axis": {"min": 0, "num_format": "#,##0", "num_font": style.font(8, style.MUTED),
                   "major_gridlines": {"visible": True, "line": {"color": style.HAIR, "width": 0.75}},
                   "line": {"none": True}, "major_tick_mark": "none"},
        "plotarea": {"layout": {"x": 0.075, "y": 0.13, "width": 0.84, "height": 0.69},
                     "border": {"none": True}, "fill": {"none": True}},
        "size": {"width": 576, "height": 300},
    }


def bar_chart(sheet: str, cat_col: int, prev_col: int, cur_col: int, r1: int, r2: int) -> dict:
    cats_ref = f"={sheet}!{aa(r1, cat_col)}:{aa(r2, cat_col)}"
    return {
        "type": "bar",
        "series": [
            {"name": f"={sheet}!{aa(4, prev_col)}", "categories": cats_ref,
             "values": f"={sheet}!{aa(r1, prev_col)}:{aa(r2, prev_col)}",
             "fill": {"color": style.PALE}, "border": {"none": True}, "gap": 60, "overlap": -10},
            {"name": f"={sheet}!{aa(4, cur_col)}", "categories": cats_ref,
             "values": f"={sheet}!{aa(r1, cur_col)}:{aa(r2, cur_col)}",
             "fill": {"color": style.BLUE}, "border": {"none": True},
             "data_labels": {"value": True, "num_format": "#,##0", "font": style.font(8)}},
        ],
        "y_axis": {"reverse": True, "major_tick_mark": "none", "line": {"color": style.LINE, "width": 0.75},
                   "num_font": style.font(8.5), "interval_unit": 1, "crossing": "max"},
        "x_axis": {"num_format": "#,##0", "num_font": style.font(8, style.MUTED), "major_tick_mark": "none",
                   "line": {"none": True},
                   "major_gridlines": {"visible": True, "line": {"color": style.HAIR, "width": 0.75}}},
        "size": {"width": 576, "height": 300},
    }


def daily_chart() -> dict:
    return {
        "type": "column",
        "series": [
            {"name": "=Daily!$C$5", "categories": "=Daily!$B$6:$B$36", "values": "=Daily!$C$6:$C$36",
             "fill": {"color": style.PALE}, "border": {"none": True}, "gap": 60, "overlap": -10},
            {"name": "=Daily!$D$5", "categories": "=Daily!$B$6:$B$36", "values": "=Daily!$D$6:$D$36",
             "fill": {"color": style.BLUE}, "border": {"none": True}},
        ],
        "x_axis": {"name": "Day of month", "name_font": style.font(8, style.MUTED, bold=False),
                   "num_font": style.font(8, style.MUTED), "line": {"color": style.LINE, "width": 0.75},
                   "major_tick_mark": "none"},
        "y_axis": {"num_format": "#,##0", "num_font": style.font(8, style.MUTED), "major_tick_mark": "none",
                   "line": {"none": True},
                   "major_gridlines": {"visible": True, "line": {"color": style.HAIR, "width": 0.75}}},
        "size": {"width": 482, "height": 300},
    }


# ── Salesmen ───────────────────────────────────────────────────────────────────────────────────

def _salesmen(ws: Sheet, model: mdl.Model, lay: Layout) -> None:
    ws.default_row = 20
    ws.col(0, 0, 2.28515625)
    ws.col(1, 1, 24.28515625)
    ws.col(2, 13, 11.7109375)
    ws.col(14, 14, 18.5703125)
    ws.row(4, 16.5)
    ws.text(0, 1, "Sales by salesman and outlet", "sheet_title")
    ws.formula(1, 1, 'CurLong&" compared with "&PrevLong&". Net sales in BHD, excluding VAT."', "sheet_sub")
    ws.merge(3, 2, 3, 6, "Net sales (BHD)", "group_head")
    ws.merge(3, 7, 3, 8, "Units", "group_head")
    ws.merge(3, 9, 3, 10, "Invoices", "group_head")
    ws.text(3, 11, "Avg invoice", "group_head")
    ws.merge(3, 12, 3, 13, "Accounts invoiced", "group_head")
    ws.text(3, 14, "Trend", "group_head")
    ws.text(4, 1, "Salesman / outlet", "th")
    for c, head in ((2, "=PrevLabel"), (3, "=CurLabel"), (4, "Change"), (5, "Change %"), (6, '="Share of "&CurShort'),
                    (7, "=PrevLabel"), (8, "=CurLabel"), (9, "=PrevLabel"), (10, "=CurLabel"), (11, "=CurLabel"),
                    (12, "=PrevLabel"), (13, "=CurLabel"),
                    (14, '="1 "&PrevShort&" to "&DAY(EDATE(CurMonth,1)-1)&" "&CurShort')):
        if head.startswith("="):
            ws.formula(4, c, head, "th_r")
        else:
            ws.text(4, c, head, "th_r")
    tot = ex(lay.sal_total)
    for i, rep in enumerate(model.reps):
        r = lay.sal_first + i
        n = ex(r)
        ws.text(r, 1, rep, "td")
        ws.formula(r, 2, f"SUMIFS(d_Net,d_Rep,$B{n},d_Month,PrevMonth)", "money")
        ws.formula(r, 3, f"SUMIFS(d_Net,d_Rep,$B{n},d_Month,CurMonth)", "money")
        ws.formula(r, 4, f"D{n}-C{n}", "money_change")
        ws.formula(r, 5, f'IF(C{n}=0,IF(D{n}=0,0,"n/a"),D{n}/C{n}-1)', "change_pct")
        ws.formula(r, 6, f"IF(D${tot}=0,0,D{n}/D${tot})", "share")
        ws.formula(r, 7, f"SUMIFS(d_Units,d_Rep,$B{n},d_Month,PrevMonth)", "int")
        ws.formula(r, 8, f"SUMIFS(d_Units,d_Rep,$B{n},d_Month,CurMonth)", "int")
        ws.formula(r, 9, f"SUMIFS(d_InvFlag,d_Rep,$B{n},d_Month,PrevMonth)", "int")
        ws.formula(r, 10, f"SUMIFS(d_InvFlag,d_Rep,$B{n},d_Month,CurMonth)", "int")
        ws.formula(r, 11, f"IF(K{n}=0,0,D{n}/K{n})", "money_1dp")
        ws.formula(r, 12, f"SUMIFS(d_RepAcctFlag,d_Rep,$B{n},d_Month,PrevMonth)", "int")
        ws.formula(r, 13, f"SUMIFS(d_RepAcctFlag,d_Rep,$B{n},d_Month,CurMonth)", "int")
        ws.blank(r, 14, "td")
    f1, l1 = ex(lay.sal_first), ex(lay.sal_last)
    r = lay.sal_total
    ws.text(r, 1, "Total", "total_label")
    ws.formula(r, 2, f"SUM(C{f1}:C{l1})", "total_money")
    ws.formula(r, 3, f"SUM(D{f1}:D{l1})", "total_money")
    ws.formula(r, 4, f"D{tot}-C{tot}", "total_money_change")
    ws.formula(r, 5, f"IF(C{tot}=0,0,D{tot}/C{tot}-1)", "total_change_pct")
    ws.formula(r, 6, f"SUM(G{f1}:G{l1})", "total_share")
    for c, col in ((7, "H"), (8, "I"), (9, "J"), (10, "K")):
        ws.formula(r, c, f"SUM({col}{f1}:{col}{l1})", "total_int")
    ws.formula(r, 11, f"IF(K{tot}=0,0,D{tot}/K{tot})", "total_1dp")
    ws.formula(r, 12, "SUMIFS(d_AcctFlag,d_Month,PrevMonth)", "total_int")
    ws.formula(r, 13, "SUMIFS(d_AcctFlag,d_Month,CurMonth)", "total_int")
    ws.blank(r, 14, "total_label")
    outlets = [x for x in model.reps if x in ("Causeway", "YQ Roadshow")]
    first_note = "Salesman is the warehouse the invoice was issued from."
    if outlets:
        first_note += f" {_join(outlets)} {'is an outlet' if len(outlets) == 1 else 'are outlets'}."
    first_note += " Units and invoices cover accessories only."
    ws.merge(r + 1, 1, r + 1, 14, first_note, "note")
    ws.merge(r + 2, 1, r + 2, 14, "An account invoiced by more than one salesman is counted under each of them and "
                                  "once in the total.", "note")

    # seven-day periods from the first day of the comparison month
    p = model.period
    periods = []
    start = p.prev_start
    while start <= p.end:
        periods.append(start)
        start = start.fromordinal(start.toordinal() + 7)
    head = lay.week_head
    ws.text(head, 1, "Net sales by seven-day period (BHD, excluding VAT)", "subhead")
    ws.text(head + 1, 1, "Period starting", "th")
    hdr = ex(head + 1)
    for j in range(len(periods)):
        c = 2 + j
        ws.formula(head + 1, c, "PrevMonth" if j == 0 else f"{a1(head + 1, c - 1)}+7", "th_date")
    last_c = 2 + len(periods) - 1
    ws.text(head + 1, last_c + 1, "Total", "th_r")
    for i in range(lay.S):
        r = lay.week_first + i
        n = ex(r)
        ws.formula(r, 1, f"B{ex(lay.sal_first + i)}", "td")
        for j in range(len(periods)):
            cl = a1(0, 2 + j)[:-1]
            ws.formula(r, 2 + j, f'SUMIFS(d_Net,d_Rep,$B{n},d_Date,">="&{cl}${hdr},d_Date,"<"&({cl}${hdr}+7))', "money")
        ws.formula(r, last_c + 1, f"SUM(C{n}:{a1(r, last_c)})", "money")
        ws.sparklines.append(dict(style.SPARKLINE, location=a1(lay.sal_first + i, 14),
                                  range=f"Salesmen!C{n}:{a1(r, last_c)}"))
    wf, wl, wt = ex(lay.week_first), ex(lay.week_last), lay.week_total
    ws.text(wt, 1, "Total", "total_label")
    for c in range(2, last_c + 2):
        cl = a1(0, c)[:-1]
        ws.formula(wt, c, f"SUM({cl}{wf}:{cl}{wl})", "total_money")
    ws.merge(wt + 1, 1, wt + 1, 14, "Periods of seven days counted from the first day of the comparison month. The "
                                    "last period is shorter. The Trend column charts these figures.", "note")
    for rule in style.change_rules():
        ws.cond(f"E{f1}:F{tot}", rule)
    ws.cond(f"G{f1}:G{l1}", dict(style.DATA_BAR))
    _page(ws, landscape=True, scale=72, breaks=[head], print_area=f"A1:O{ex(wt + 1)}")


# ── Categories ─────────────────────────────────────────────────────────────────────────────────

def _categories(ws: Sheet, model: mdl.Model, lay: Layout) -> None:
    ws.default_row = 20
    ws.col(0, 0, 2.28515625)
    ws.col(1, 1, 27.140625)
    ws.col(2, max(11, 2 + lay.K), 12.5703125)
    ws.row(4, 22.5)
    ws.row(lay.mat_head + 1, 22.5)
    ws.text(0, 1, "Sales by category", "sheet_title")
    ws.formula(1, 1, 'CurLong&" compared with "&PrevLong&". Net sales in BHD, excluding VAT. Accessories only."',
               "sheet_sub")
    ws.merge(3, 3, 3, 4, "Units", "group_head")
    ws.merge(3, 5, 3, 9, "Net sales (BHD)", "group_head")
    ws.text(3, 10, "Avg unit price", "group_head")
    ws.text(4, 1, "Category", "th")
    for c, head in ((2, '="Products sold, "&CurShort'), (3, "=PrevLabel"), (4, "=CurLabel"), (5, "=PrevLabel"),
                    (6, "=CurLabel"), (7, "Change"), (8, "Change %"), (9, '="Share of "&CurShort'),
                    (10, "=CurLabel")):
        if head.startswith("="):
            ws.formula(4, c, head, "th_r")
        else:
            ws.text(4, c, head, "th_r")
    tot = ex(lay.cat_total)
    pd_ = f"Products!{aa(lay.prod_first, 3)}:{aa(lay.prod_last, 3)}"
    pf_ = f"Products!{aa(lay.prod_first, 5)}:{aa(lay.prod_last, 5)}"
    for i, name in enumerate(model.categories):
        r = lay.cat_first + i
        n = ex(r)
        ws.text(r, 1, name, "td")
        ws.formula(r, 2, f'COUNTIFS({pd_},$B{n},{pf_},">0")', "int")
        ws.formula(r, 3, f"SUMIFS(d_Units,d_Cat,$B{n},d_Month,PrevMonth)", "int")
        ws.formula(r, 4, f"SUMIFS(d_Units,d_Cat,$B{n},d_Month,CurMonth)", "int")
        ws.formula(r, 5, f"SUMIFS(d_Net,d_Cat,$B{n},d_Month,PrevMonth)", "money")
        ws.formula(r, 6, f"SUMIFS(d_Net,d_Cat,$B{n},d_Month,CurMonth)", "money")
        ws.formula(r, 7, f"G{n}-F{n}", "money_change")
        ws.formula(r, 8, f'IF(F{n}=0,IF(G{n}=0,0,"n/a"),G{n}/F{n}-1)', "change_pct")
        ws.formula(r, 9, f"IF(G${tot}=0,0,G{n}/G${tot})", "share")
        ws.formula(r, 10, f"IF(E{n}=0,0,G{n}/E{n})", "money")
    f1, l1 = ex(lay.cat_first), ex(lay.cat_last)
    r = lay.cat_total
    ws.text(r, 1, "Total", "total_label")
    for c, col, fmt in ((2, "C", "total_int"), (3, "D", "total_int"), (4, "E", "total_int"), (5, "F", "total_money"),
                        (6, "G", "total_money")):
        ws.formula(r, c, f"SUM({col}{f1}:{col}{l1})", fmt)
    ws.formula(r, 7, f"G{tot}-F{tot}", "total_money_change")
    ws.formula(r, 8, f"IF(F{tot}=0,0,G{tot}/F{tot}-1)", "total_change_pct")
    ws.formula(r, 9, f"SUM(J{f1}:J{l1})", "total_share")
    ws.formula(r, 10, f"IF(E{tot}=0,0,G{tot}/E{tot})", "total_money")
    ws.merge(r + 1, 1, r + 1, 11, "Category comes from the item code (see Notes). Average unit price is net sales "
                                  "divided by units.", "note")

    # the month by salesman and category
    head = lay.mat_head
    hdr = ex(head + 1)
    ws.formula(head, 1, 'CurLong&" net sales by salesman and category (BHD, excluding VAT)"', "subhead")
    ws.text(head + 1, 1, "Salesman / outlet", "th")
    for j in range(lay.K):
        ws.formula(head + 1, 2 + j, f"$B${ex(lay.cat_first + j)}", "th_r")
    tc = 2 + lay.K
    ws.text(head + 1, tc, "Total", "th_r")
    for i in range(lay.S):
        r = lay.mat_first + i
        n = ex(r)
        ws.formula(r, 1, f"Salesmen!B{ex(lay.sal_first + i)}", "td")
        for j in range(lay.K):
            cl = a1(0, 2 + j)[:-1]
            ws.formula(r, 2 + j, f"SUMIFS(d_Net,d_Rep,$B{n},d_Cat,{cl}${hdr},d_Month,CurMonth)", "money_1dp")
        ws.formula(r, tc, f"SUM(C{n}:{a1(r, tc - 1)})", "money_1dp_bold")
    mf, ml = ex(lay.mat_first), ex(lay.mat_last)
    ws.text(lay.mat_total, 1, "Total", "total_label")
    for c in range(2, tc + 1):
        cl = a1(0, c)[:-1]
        ws.formula(lay.mat_total, c, f"SUM({cl}{mf}:{cl}{ml})", "total_1dp")
    ws.merge(lay.mat_total + 1, 1, lay.mat_total + 1, max(11, tc), "The darker the cell, the higher the sales.", "note")
    for rule in style.change_rules():
        ws.cond(f"H{f1}:I{tot}", rule)
    ws.cond(f"J{f1}:J{l1}", dict(style.DATA_BAR))
    ws.cond(f"C{mf}:{a1(lay.mat_last, tc - 1)}", dict(style.COLOR_SCALE))
    _page(ws, landscape=True, fit=(1, 1))


# ── Products ───────────────────────────────────────────────────────────────────────────────────

def _products(ws: Sheet, model: mdl.Model, lay: Layout) -> None:
    ws.default_row = 18
    for c1, c2, w in ((0, 0, 2.28515625), (1, 1, 9.140625), (2, 2, 80), (3, 3, 22.85546875), (4, 11, 12),
                      (12, 12, 85.7109375), (13, 13, 10), (14, 14, 52.85546875)):
        ws.col(c1, c2, w)
    ws.row(4, 16.5)
    ws.text(0, 1, "Sales by product", "sheet_title")
    ws.formula(1, 1, 'CurLong&" compared with "&PrevLong&". Net sales in BHD, excluding VAT. Accessories only, '
                     'listed by "&CurLong&" sales."', "sheet_sub")
    ws.merge(3, 4, 3, 5, "Units", "group_head")
    ws.merge(3, 6, 3, 10, "Net sales (BHD)", "group_head")
    ws.text(3, 11, "Avg unit price", "group_head")
    ws.merge(3, 12, 3, 14, "Reference columns", "ref_group_head")
    for c, head in ((1, "Code"), (2, "Product"), (3, "Category")):
        ws.text(4, c, head, "th")
    for c, head in ((4, "=PrevLabel"), (5, "=CurLabel"), (6, "=PrevLabel"), (7, "=CurLabel"), (8, "Change"),
                    (9, "Change %"), (10, '="Share of "&CurShort'), (11, "=CurLabel")):
        if head.startswith("="):
            ws.formula(4, c, head, "th_r")
        else:
            ws.text(4, c, head, "th_r")
    ws.text(4, 12, "ERP item description", "ref_th")
    ws.formula(4, 13, '"Rank, "&CurShort', "ref_th_r")
    ws.text(4, 14, "Dashboard label", "ref_th")
    tot = ex(lay.prod_total)
    f1, l1 = ex(lay.prod_first), ex(lay.prod_last)
    for i, it in enumerate(model.products):
        r = lay.prod_first + i
        n = ex(r)
        ws.text(r, 1, it.code, "td")
        ws.text(r, 2, it.product, "td")
        ws.text(r, 3, it.category, "td")
        ws.formula(r, 4, f"SUMIFS(d_Units,d_Item,$M{n},d_Month,PrevMonth)", "int")
        ws.formula(r, 5, f"SUMIFS(d_Units,d_Item,$M{n},d_Month,CurMonth)", "int")
        ws.formula(r, 6, f"SUMIFS(d_Net,d_Item,$M{n},d_Month,PrevMonth)", "money")
        ws.formula(r, 7, f"SUMIFS(d_Net,d_Item,$M{n},d_Month,CurMonth)", "money")
        ws.formula(r, 8, f"H{n}-G{n}", "money_change")
        ws.formula(r, 9, f'IF(G{n}=0,IF(H{n}=0,0,"n/a"),H{n}/G{n}-1)', "change_pct")
        ws.formula(r, 10, f"IF(H${tot}=0,0,H{n}/H${tot})", "share")
        ws.formula(r, 11, f"IF(F{n}=0,0,H{n}/F{n})", "money")
        ws.text(r, 12, it.item, "ref_td")
        ws.formula(r, 13, f"SUMPRODUCT(--($H${f1}:$H${l1}>H{n}))+SUMPRODUCT(--($H${f1}:H{n}=H{n}))", "ref_int")
        ws.text(r, 14, mdl.dashboard_label(it.code, it.product), "ref_td")
    r = lay.prod_total
    ws.text(r, 1, "Total", "total_label")
    ws.blank(r, 2, "total_label")
    ws.blank(r, 3, "total_label")
    ws.formula(r, 4, f"SUM(E{f1}:E{l1})", "total_int")
    ws.formula(r, 5, f"SUM(F{f1}:F{l1})", "total_int")
    ws.formula(r, 6, f"SUM(G{f1}:G{l1})", "total_money")
    ws.formula(r, 7, f"SUM(H{f1}:H{l1})", "total_money")
    ws.formula(r, 8, f"H{tot}-G{tot}", "total_money_change")
    ws.formula(r, 9, f"IF(G{tot}=0,0,H{tot}/G{tot}-1)", "total_change_pct")
    ws.formula(r, 10, f"SUM(K{f1}:K{l1})", "total_share")
    ws.formula(r, 11, f"IF(F{tot}=0,0,H{tot}/F{tot})", "total_money")
    for c in (12, 13, 14):
        ws.blank(r, c, "total_label")
    if model.not_counted:
        ws.text(lay.nc_head, 1, "Not included above: handsets with no value in the day book (units)", "subhead_small")
        for i, it in enumerate(model.not_counted):
            r = lay.nc_first + i
            n = ex(r)
            ws.text(r, 1, it.code, "td")
            ws.text(r, 2, it.product, "td")
            ws.text(r, 3, it.category, "td")
            ws.formula(r, 4, f"SUMIFS(d_Qty,d_Item,$M{n},d_Month,PrevMonth)", "int")
            ws.formula(r, 5, f"SUMIFS(d_Qty,d_Item,$M{n},d_Month,CurMonth)", "int")
            ws.formula(r, 6, f"SUMIFS(d_Net,d_Item,$M{n},d_Month,PrevMonth)", "money")
            ws.formula(r, 7, f"SUMIFS(d_Net,d_Item,$M{n},d_Month,CurMonth)", "money")
            for c in (8, 9, 10, 11):
                ws.blank(r, c, "td")
            ws.text(r, 12, it.item, "ref_td")
    for rule in style.change_rules():
        ws.cond(f"I{f1}:J{tot}", rule)
    ws.cond(f"K{f1}:K{l1}", dict(style.DATA_BAR))
    _page(ws, landscape=True, fit=(1, 0), freeze=(5, 0), autofilter=f"B5:O{l1}",
          print_area=f"A1:L{ex(lay.prod_end)}", repeat_rows=(3, 4))


# ── Customers ──────────────────────────────────────────────────────────────────────────────────

def _customers(ws: Sheet, model: mdl.Model, lay: Layout, words: Commentary) -> None:
    ws.default_row = 18
    for c1, c2, w in ((0, 0, 2.28515625), (1, 1, 57.140625), (2, 2, 11.42578125), (3, 3, 21.42578125),
                      (4, 8, 12.5703125), (9, 9, 17.140625), (10, 10, 10), (11, 11, 52.85546875)):
        ws.col(c1, c2, w)
    ws.row(4, 16.5)
    ws.text(0, 1, "Sales by customer", "sheet_title")
    ws.formula(1, 1, 'CurLong&" compared with "&PrevLong&". Net sales in BHD, excluding VAT. Accounts listed by "'
                     '&CurLong&" sales."', "sheet_sub")
    ws.merge(3, 4, 3, 6, "Net sales (BHD)", "group_head")
    ws.merge(3, 7, 3, 8, "Invoices", "group_head")
    ws.merge(3, 10, 3, 11, "Reference columns", "ref_group_head")
    for c, head in ((1, "Customer"), (2, "Type"), (3, "Salesman, latest invoice")):
        ws.text(4, c, head, "th")
    for c, head in ((4, "=PrevLabel"), (5, "=CurLabel"), (6, "Change"), (7, "=PrevLabel"), (8, "=CurLabel")):
        if head.startswith("="):
            ws.formula(4, c, head, "th_r")
        else:
            ws.text(4, c, head, "th_r")
    ws.text(4, 9, "Status", "th_indent3")
    ws.formula(4, 10, '"Rank, "&CurShort', "ref_th_r")
    ws.text(4, 11, "Dashboard label", "ref_th")
    f1, l1, tot = ex(lay.cust_first), ex(lay.cust_last), ex(lay.cust_total)
    for i, name in enumerate(model.customers):
        r = lay.cust_first + i
        n = ex(r)
        ws.text(r, 1, name, "td")
        ws.formula(r, 2, f'IF(B{n}=CashAcct,"Cash","Account")', "td")
        ws.formula(r, 3, f'IF(C{n}="Cash","Various",LOOKUP(2,1/(d_Cust=$B{n}),d_Rep))', "td")
        ws.formula(r, 4, f"SUMIFS(d_Net,d_Cust,$B{n},d_Month,PrevMonth)", "money")
        ws.formula(r, 5, f"SUMIFS(d_Net,d_Cust,$B{n},d_Month,CurMonth)", "money")
        ws.formula(r, 6, f"F{n}-E{n}", "money_change")
        ws.formula(r, 7, f"SUMIFS(d_InvFlag,d_Cust,$B{n},d_Month,PrevMonth)", "int")
        ws.formula(r, 8, f"SUMIFS(d_InvFlag,d_Cust,$B{n},d_Month,CurMonth)", "int")
        ws.formula(r, 9, f'IF(C{n}="Cash","Cash",IF(AND(H{n}>0,I{n}>0),PrevShort&" & "&CurShort,IF(I{n}>0,CurShort'
                         f'&" only",IF(H{n}>0,PrevShort&" only","No invoice"))))', "status")
        ws.formula(r, 10, f'IF(C{n}="Cash","",SUMPRODUCT(--($F${f1}:$F${l1}>F{n}),--($C${f1}:$C${l1}="Account"))'
                          f'+SUMPRODUCT(--($F${f1}:F{n}=F{n}),--($C${f1}:C{n}="Account")))', "rank")
        ws.text(r, 11, mdl.account_label(name), "ref_td")
    r = lay.cust_total
    ws.text(r, 1, "Total", "total_label")
    for c in (2, 3, 9, 10, 11):
        ws.blank(r, c, "total_label")
    ws.formula(r, 4, f"SUM(E{f1}:E{l1})", "total_money")
    ws.formula(r, 5, f"SUM(F{f1}:F{l1})", "total_money")
    ws.formula(r, 6, f"F{tot}-E{tot}", "total_money_change")
    ws.formula(r, 7, f"SUM(H{f1}:H{l1})", "total_int")
    ws.formula(r, 8, f"SUM(I{f1}:I{l1})", "total_int")
    ws.formula(r + 1, 1, '"Customer names are as recorded in the ERP. Filter Status on """&PrevShort&" only"" to list '
                         'the accounts not invoiced in "&CurLong&"."', "note")
    ws.text(r + 2, 1, words.note("customers_note"), "note")
    for rule in style.change_rules():
        ws.cond(f"G{f1}:G{tot}", rule)
    _page(ws, landscape=True, fit=(1, 0), freeze=(5, 2), autofilter=f"B5:L{l1}", print_area=f"A1:J{ex(r + 2)}",
          repeat_rows=(3, 4))


# ── Daily ──────────────────────────────────────────────────────────────────────────────────────

def _daily(ws: Sheet, model: mdl.Model) -> None:
    p = model.period
    ws.default_row = 18
    for c1, c2, w in ((0, 0, 2.28515625), (1, 1, 8.5703125), (2, 5, 13.7109375), (6, 6, 4.28515625),
                      (7, 7, 18.5703125), (8, 11, 12.5703125)):
        ws.col(c1, c2, w)
    ws.text(0, 1, "Daily net sales", "sheet_title")
    ws.formula(1, 1, 'CurLong&" compared with "&PrevLong&". Net sales in BHD, excluding VAT, by invoice date."',
               "sheet_sub")
    ws.merge(3, 2, 3, 3, "Daily", "group_head")
    ws.merge(3, 4, 3, 5, "Cumulative", "group_head")
    ws.text(3, 7, "By week of month", "subhead")
    ws.text(4, 1, "Day", "th")
    for c, head in ((2, "PrevLabel"), (3, "CurLabel"), (4, "PrevLabel"), (5, "CurLabel")):
        ws.formula(4, c, head, "th_r")
    ws.text(4, 7, "Days of the month", "th")
    ws.formula(4, 8, "PrevLabel", "th_r")
    ws.text(4, 9, "% of month", "th_r")
    ws.formula(4, 10, "CurLabel", "th_r")
    ws.text(4, 11, "% of month", "th_r")
    prev_days = (p.prev_end - p.prev_start).days + 1
    cur_days = (p.end - p.start).days + 1
    for d in range(1, DAYS + 1):
        r = 4 + d
        n = ex(r)
        ws.put(r, 1, d, "day")
        if d <= prev_days:
            ws.formula(r, 2, f"SUMIFS(d_Net,d_Date,PrevMonth+$B{n}-1)", "money")
            ws.formula(r, 4, f"SUM(C$6:C{n})", "money_plain")
        else:
            ws.blank(r, 2, "td")
            ws.blank(r, 4, "td")
        if d <= cur_days:
            ws.formula(r, 3, f"SUMIFS(d_Net,d_Date,CurMonth+$B{n}-1)", "money")
            ws.formula(r, 5, f"SUM(D$6:D{n})", "money_plain")
        else:
            ws.blank(r, 3, "td")
            ws.blank(r, 5, "td")
    for k, (label, d1, d2) in enumerate(WEEK_BLOCKS):
        r = 5 + k
        ws.text(r, 7, label, "td")
        ws.formula(r, 8, f"SUM(C{5 + d1}:C{5 + d2})", "money")
        ws.formula(r, 9, f"IF(I$11=0,0,I{ex(r)}/I$11)", "share")
        ws.formula(r, 10, f"SUM(D{5 + d1}:D{5 + d2})", "money")
        ws.formula(r, 11, f"IF(K$11=0,0,K{ex(r)}/K$11)", "share")
    ws.text(10, 7, "Total", "total_label")
    ws.formula(10, 8, "SUM(I6:I10)", "total_money")
    ws.formula(10, 9, "SUM(J6:J10)", "total_share")
    ws.formula(10, 10, "SUM(K6:K10)", "total_money")
    ws.formula(10, 11, "SUM(L6:L10)", "total_share")
    ws.text(12, 7, "Net sales by day (BHD, excluding VAT)", "subhead")
    ws.text(36, 1, "Total", "total_label")
    ws.formula(36, 2, "SUM(C6:C36)", "total_money")
    ws.formula(36, 3, "SUM(D6:D36)", "total_money")
    ws.blank(36, 4, "total_label")
    ws.blank(36, 5, "total_label")
    ws.charts.append((13, 7, daily_chart(), {"object_position": 2, "y_offset": 4,
                                             "description": "Column chart of net sales by day of month, report "
                                                            "month against comparison month."}))
    _page(ws, landscape=False, fit=(1, 1), freeze=(5, 0))


# ── Data ───────────────────────────────────────────────────────────────────────────────────────

DATA_HEADERS = (("Date", "th"), ("Invoice", "th"), ("Customer", "th"), ("Item", "th"), ("Qty", "th_r"),
                ("Rate", "th_r"), ("Gross", "th_r"), ("Net", "th_r"), ("Warehouse", "data_th_wh"),
                ("Narration", "th"), ("Month", "data_th_calc"), ("Salesman / outlet", "data_th_calc"),
                ("Category", "data_th_calc"), ("VAT", "data_th_calc_r"), ("Line type", "data_th_calc_indent"),
                ("Units", "data_th_calc_r"), ("Invoice flag", "data_th_calc_r"), ("Account flag", "data_th_calc_r"),
                ("Salesman account flag", "data_th_calc_r"))


def _data(ws: Sheet, model: mdl.Model, lay: Layout) -> None:
    ws.default_row = 16
    for c1, c2, w in ((0, 0, 11.7109375), (1, 1, 17.7109375), (2, 2, 44.7109375), (3, 3, 78.7109375),
                      (4, 4, 7.7109375), (5, 5, 8.7109375), (6, 7, 10.7109375), (8, 8, 29.7109375),
                      (9, 9, 30.7109375), (10, 10, 10.7109375), (11, 11, 20.7109375), (12, 12, 23.7109375),
                      (13, 13, 9.7109375), (14, 14, 13.7109375), (15, 15, 8.7109375), (16, 17, 9.7109375),
                      (18, 18, 11.7109375)):
        ws.col(c1, c2, w)
    ws.row(3, 22.5)
    p = model.period
    ws.text(0, 0, "Sales day book lines", "sheet_title")
    ws.text(1, 0, f"ERP export for {COMPANY}, {p.span_text()}. Columns A to J are the export, tidied: invoice prefix "
                  f"removed, {mdl.CASH_ACCOUNT} spelt one way, the three empty columns dropped and customer ID numbers "
                  f"left out of the narration. Columns K to S are calculated.", "sheet_sub")
    for c, (head, fmt) in enumerate(DATA_HEADERS):
        ws.text(3, c, head, fmt)
    for i, ln in enumerate(model.lines):
        r = lay.data_first + i
        n = ex(r)
        ws.put(r, 0, ln.sale_date, "data_date")
        ws.text(r, 1, ln.invoice, "data_text")
        ws.text(r, 2, ln.customer, "data_text")
        ws.text(r, 3, ln.item, "data_text")
        ws.put(r, 4, ln.qty, "data_int")
        ws.put(r, 5, ln.rate, "data_money")
        ws.put(r, 6, ln.gross, "data_money")
        ws.put(r, 7, ln.net, "data_money")
        ws.text(r, 8, ln.warehouse, "data_indent")
        ws.text(r, 9, ln.narration, "data_text")
        ws.formula(r, 10, f"DATE(YEAR(A{n}),MONTH(A{n}),1)", "data_month")
        ws.formula(r, 11, f'SUBSTITUTE(I{n},WhSuffix,"")', "data_text")
        ws.formula(r, 12, f'IFERROR(INDEX(p_Cat,MATCH(D{n},p_Item,0)),"Unmapped")', "data_text")
        ws.formula(r, 13, f"G{n}-H{n}", "data_money")
        ws.formula(r, 14, f'IF(M{n}=HandsetCat,"Handset","Accessory")', "data_indent")
        ws.formula(r, 15, f'IF(O{n}="Accessory",E{n},0)', "data_int")
        ws.formula(r, 16, f'IF(O{n}="Accessory",IF(COUNTIFS($B$5:B{n},B{n},$O$5:O{n},"Accessory")=1,1,0),0)', "data_int")
        ws.formula(r, 17, f'IF(OR(C{n}=CashAcct,O{n}<>"Accessory"),0,IF(COUNTIFS($C$5:C{n},C{n},$K$5:K{n},K{n},'
                          f'$O$5:O{n},"Accessory")=1,1,0))', "data_int")
        ws.formula(r, 18, f'IF(OR(C{n}=CashAcct,O{n}<>"Accessory"),0,IF(COUNTIFS($C$5:C{n},C{n},$K$5:K{n},K{n},'
                          f'$L$5:L{n},L{n},$O$5:O{n},"Accessory")=1,1,0))', "data_int")
    _page(ws, landscape=True, fit=(1, 0), freeze=(4, 2), autofilter=f"A4:S{ex(lay.data_last)}", repeat_rows=(3,))


# ── Notes ──────────────────────────────────────────────────────────────────────────────────────

def _notes(ws: Sheet, model: mdl.Model, lay: Layout, facts: Facts) -> None:
    p = model.period
    ws.default_row = 18
    ws.col(0, 0, 2.28515625)
    ws.col(1, 1, 42.85546875)
    ws.col(2, 6, 15.7109375)
    ws.col(7, 7, 61.42578125)
    ws.text(0, 1, "Notes and settings", "sheet_title")
    ws.text(1, 1, "Settings, basis of preparation and control totals. Cells in blue are typed in.", "sheet_sub")
    ws.text(3, 1, "Report settings", "subhead")
    settings = (
        ("Report month (first day)", p.start, "input_date", "The month reported. It is compared with the month before."),
        ("Comparison month", "=EDATE(CurMonth,-1)", "calc_date", None),
        ("Cash sales account", mdl.CASH_ACCOUNT, "input_text", "ERP account used for unnamed cash sales."),
        ("Handset category", cats.HANDSET, "input_text",
         "Items in this category are left out of accessory units and invoice counts."),
        ("Warehouse suffix", mdl.WH_SUFFIX, "input_text", "Removed from the ERP warehouse name to give the salesman name."),
        ("Prepared by", facts.prepared_by or "", "input_text", None),
        ("Prepared on", facts.prepared_on, "input_date", None),
    )
    for k, (label, value, fmt, help_text) in enumerate(settings):
        r = 4 + k
        ws.text(r, 1, label, "td")
        if isinstance(value, str) and value.startswith("="):
            ws.formula(r, 2, value, fmt)
        elif isinstance(value, str):
            ws.text(r, 2, value, fmt)
        else:
            ws.put(r, 2, value, fmt)
        ws.merge(r, 3, r, 7, help_text, "help")

    ws.text(12, 1, "Basis of preparation", "subhead")
    basis = basis_paragraphs(model, facts)
    for k, text in enumerate(basis):
        r = 13 + k
        ws.merge(r, 1, r, 7, f"{k + 1}.  {text}", "para")
        ws.row(r, para_height(text))
    about_head = 13 + len(basis) + 1
    ws.text(about_head, 1, "About this workbook", "subhead")
    about = ("Figures on every sheet are worked out from the Data sheet with formulas.",
             "The salesman, product and customer lists are fixed for this report. Products and Customers can be "
             "sorted or filtered; the Dashboard picks its top ten by rank, so it is not affected.",
             "The commentary and footnotes on the Dashboard are written text. They do not change if the data changes.",
             f"This file covers {mdl.MONTHS[p.prev_start.month - 1]} and {p.long} only. Next month's report will be "
             f"a new file.")
    for k, text in enumerate(about):
        r = about_head + 1 + k
        ws.merge(r, 1, r, 7, f"{k + 1}.  {text}", "para")
        ws.row(r, para_height(text))
    defs_head = about_head + 1 + len(about) + 1
    ws.text(defs_head, 1, "Definitions", "subhead")
    defs = (("Net sales", "Taxable value of sales invoice lines in BHD, excluding VAT."),
            ("Units", "Quantity of accessory items invoiced."),
            ("Invoices", "Sales invoices with at least one accessory line."),
            ("Average invoice", "Net sales divided by invoices."),
            ("Average unit price", "Net sales divided by units."),
            ("Accounts invoiced", "Accounts with at least one invoice in the month."),
            ("Change %", "Report month against comparison month. Shown as n/a where there were no sales in the "
                         "comparison month."))
    for k, (term, meaning) in enumerate(defs):
        r = defs_head + 1 + k
        ws.text(r, 1, term, "td")
        ws.merge(r, 2, r, 7, meaning, "td")
    # the control totals start where the Sep file has them when the basis has its eleven paragraphs
    ctl = max(defs_head + 1 + len(defs) + 1, 40)
    ws.text(ctl, 1, "Control totals", "subhead")
    _controls(ws, ctl + 1, lay, facts)
    ws.text(57, 1, "Month names used in the headings", "subhead")
    months = ("CHOOSE(MONTH(CurMonth),\"Jan\",\"Feb\",\"Mar\",\"Apr\",\"May\",\"Jun\",\"Jul\",\"Aug\",\"Sep\",\"Oct\","
              "\"Nov\",\"Dec\")")
    full = ("\"January\",\"February\",\"March\",\"April\",\"May\",\"June\",\"July\",\"August\",\"September\","
            "\"October\",\"November\",\"December\"")
    rows = (("Report month, short", months, "Worked out from the report month."),
            ("Comparison month, short", months.replace("CurMonth", "PrevMonth"), None),
            ("Report month", 'CurShort&" "&YEAR(CurMonth)', None),
            ("Comparison month", 'PrevShort&" "&YEAR(PrevMonth)', None),
            ("Report month, in full", f'CHOOSE(MONTH(CurMonth),{full})&" "&YEAR(CurMonth)', None),
            ("Comparison month, in full", f'CHOOSE(MONTH(PrevMonth),{full})&" "&YEAR(PrevMonth)', None),
            ("Date prepared", f'DAY(PreparedOn)&" "&CHOOSE(MONTH(PreparedOn),{full})&" "&YEAR(PreparedOn)', None))
    for k, (label, formula, help_text) in enumerate(rows):
        r = 58 + k
        ws.text(r, 1, label, "td")
        ws.formula(r, 2, formula, "calc_text")
        ws.merge(r, 3, r, 7, help_text, "help")
    _engine_controls(ws, 66, model, lay, facts)
    _page(ws, landscape=True, scale=72, breaks=[defs_head])


def basis_paragraphs(model: mdl.Model, facts: Facts) -> list[str]:
    p = model.period
    invoices = len({ln.invoice for ln in model.lines})
    out = [f"Source is the ERP sales day book for {COMPANY}, {p.span_text()} ({len(model.lines):,} lines, "
           f"{invoices:,} invoices). The lines are on the Data sheet.",
           "Net sales is the Taxable column, which excludes 10% VAT. Gross is the invoiced value including VAT. "
           "VAT is taken as Gross less Taxable.",
           "The day book covers sales invoices only, so any sales returns are not deducted."]
    credit = {}
    for ln in model.lines:
        if "credit note" in ln.narration.lower():
            credit.setdefault(ln.invoice, [ln.sale_date, 0.0])
            credit[ln.invoice][1] += ln.net
    if credit:
        on_last = sum(1 for d, _n in credit.values() if d == p.end)
        net = sum(n for _d, n in credit.values())
        tail = f", {on_last} of them dated {p.end.day} {mdl.MONTHS[p.end.month - 1]}" if on_last else ""
        out.append(f"{len(credit)} invoice{'s carry' if len(credit) != 1 else ' carries'} \"credit note\" in the "
                   f"narration (BHD {net:,.0f} net{tail}). They are sales invoices in the day book and are included in "
                   f"sales.")
    outlets = [r for r in model.reps if r in facts.outlets]
    out.append(f"Salesman or outlet is the warehouse the invoice was issued from, with \"{mdl.WH_SUFFIX}\" removed."
               + (f" {_join(outlets)} {'is an outlet rather than a salesman' if len(outlets) == 1 else 'are outlets rather than salesmen'}."
                  if outlets else ""))
    out.append(cats.rule_paragraph(model.category_names))
    if model.not_counted:
        lines_n = sum(1 for ln in model.lines if ln.item in {it.item for it in model.not_counted})
        units = sum(it.prev_qty + it.cur_qty for it in model.not_counted)
        codes = _join([it.code for it in model.not_counted])
        out.append(f"{codes} items carry no value in the day book ({lines_n:,} lines, {_units(units)} units). They are "
                   "not accessory sales, so they are left out of units, invoice counts and product counts.")
    spellings = {ln.raw_customer for ln in model.lines if mdl.CASH_SPELLING.match(ln.raw_customer)}
    out.append(f"An account is any customer other than {mdl.CASH_ACCOUNT}, which covers unnamed cash sales."
               + (f" The {len(spellings)} spellings of {mdl.CASH_ACCOUNT} in the ERP are treated as one."
                  if len(spellings) > 1 else ""))
    out.append("Customer names are as recorded in the ERP and similar names are not merged.")
    out.append(f"\"{p.short} only\" on the Customers sheet means no invoice in {mdl.MONTHS[p.prev_start.month - 1]}. "
               "Earlier months are outside this extract, so some of these accounts may have bought before "
               f"{mdl.MONTHS[p.prev_start.month - 1]}.")
    masked = sum(1 for ln in model.lines if ln.narration_masked)
    if masked:
        out.append(f"{masked:,} narration{'s hold' if masked != 1 else ' holds'} customer ID numbers. These are not "
                   "carried into the Data sheet.")
    return out


def _controls(ws: Sheet, top: int, lay: Layout, facts: Facts) -> None:
    """Rows top.. : the Data sheet against v_sales, then each sheet's total less the Data sheet."""
    ws.text(top, 1, "Data sheet against the export file", "th")
    ws.formula(top, 2, "PrevLabel", "th_r")
    ws.formula(top, 3, "CurLabel", "th_r")
    ws.text(top, 4, "Both months", "th_r")
    ws.text(top, 5, "Export file", "th_r")
    ws.text(top, 6, "Difference", "th_r")
    ws.blank(top, 7, "th")
    t = facts.totals
    both = {k: (t.get("prev", {}).get(k) or 0) + (t.get("cur", {}).get(k) or 0) for k in ("lines", "qty", "gross", "net")}
    rows = (("Day book lines", "COUNTIFS(d_Month,{m})", "int", "input_int", "diff_int", both["lines"],
             "Export file totals were added up before loading; they confirm no lines were lost."),
            ("Quantity, all lines", "SUMIFS(d_Qty,d_Month,{m})", "int", "input_int", "diff_int", both["qty"], None),
            ("Gross, including VAT (BHD)", "SUMIFS(d_Gross,d_Month,{m})", "money", "input_money", "diff_money",
             both["gross"], None),
            ("Net sales, excluding VAT (BHD)", "SUMIFS(d_Net,d_Month,{m})", "money", "input_money", "diff_money",
             both["net"], None))
    for k, (label, f, fmt, in_fmt, diff_fmt, typed, help_text) in enumerate(rows):
        r = top + 1 + k
        n = ex(r)
        ws.text(r, 1, label, "td")
        ws.formula(r, 2, f.format(m="PrevMonth"), fmt)
        ws.formula(r, 3, f.format(m="CurMonth"), fmt)
        ws.formula(r, 4, f"C{n}+D{n}", fmt)
        ws.put(r, 5, typed, in_fmt)
        ws.formula(r, 6, f"ROUND(E{n}-F{n},2)", diff_fmt)
        if help_text:
            ws.text(r, 7, help_text, "help")
        else:
            ws.blank(r, 7, "help")
    r = top + 5
    n = ex(r)
    ws.text(r, 1, "VAT (BHD)", "td")
    ws.formula(r, 2, "SUMIFS(d_VAT,d_Month,PrevMonth)", "money")
    ws.formula(r, 3, "SUMIFS(d_VAT,d_Month,CurMonth)", "money")
    ws.formula(r, 4, f"C{n}+D{n}", "money")
    ws.blank(r, 5, "td")
    ws.blank(r, 6, "td")
    ws.text(r, 7, "Gross less net.", "help")
    r = top + 6
    vat, net = ex(top + 5), ex(top + 4)
    ws.text(r, 1, "VAT as % of net sales", "td")
    for c, col in ((2, "C"), (3, "D"), (4, "E")):
        ws.formula(r, c, f"IF({col}{net}=0,0,{col}{vat}/{col}{net})", "pct_plain")
    ws.blank(r, 5, "td")
    ws.blank(r, 6, "td")
    ws.text(r, 7, "VAT rate is 10%.", "help")

    s = top + 8
    ws.text(s, 1, "Sheet totals less Data (net sales)", "th")
    ws.formula(s, 2, "PrevLabel", "th_r")
    ws.formula(s, 3, "CurLabel", "th_r")
    for c in (4, 5, 6):
        ws.blank(s, c, "th_r")
    ws.blank(s, 7, "th")
    checks = (("Salesmen", f"Salesmen!C{ex(lay.sal_total)}", f"Salesmen!D{ex(lay.sal_total)}", "All differences should be nil."),
              ("Categories", f"Categories!F{ex(lay.cat_total)}", f"Categories!G{ex(lay.cat_total)}", None),
              ("Products", f"Products!G{ex(lay.prod_total)}", f"Products!H{ex(lay.prod_total)}", None),
              ("Customers", f"Customers!E{ex(lay.cust_total)}", f"Customers!F{ex(lay.cust_total)}", None),
              ("Daily", "Daily!C37", "Daily!D37", None))
    for k, (label, prev_ref, cur_ref, help_text) in enumerate(checks):
        r = s + 1 + k
        ws.text(r, 1, label, "td")
        ws.formula(r, 2, f"ROUND({prev_ref}-SUMIFS(d_Net,d_Month,PrevMonth),2)", "diff_money")
        ws.formula(r, 3, f"ROUND({cur_ref}-SUMIFS(d_Net,d_Month,CurMonth),2)", "diff_money")
        for c in (4, 5, 6):
            ws.blank(r, c, "td")
        if help_text:
            ws.text(r, 7, help_text, "help")
        else:
            ws.blank(r, 7, "help")
    r = s + 6
    ws.text(r, 1, "Day book lines with no category", "td")
    ws.formula(r, 2, 'COUNTIFS(d_Cat,"Unmapped",d_Month,PrevMonth)', "int_plain")
    ws.formula(r, 3, 'COUNTIFS(d_Cat,"Unmapped",d_Month,CurMonth)', "int_plain")
    for c in (4, 5, 6):
        ws.blank(r, c, "td")
    ws.text(r, 7, "A count here would mean an item is missing from the Products sheet.", "help")


def _engine_controls(ws: Sheet, top: int, model: mdl.Model, lay: Layout, facts: Facts) -> None:
    """The two controls the ops engine adds: the day book equals v_sales month by month, and no narration
    on the Data sheet holds an ID number."""
    t = facts.totals
    ws.text(top, 1, "Checks against the database", "subhead")
    ws.text(top + 1, 1, "Net sales, excluding VAT (BHD)", "th")
    ws.formula(top + 1, 2, "PrevLabel", "th_r")
    ws.formula(top + 1, 3, "CurLabel", "th_r")
    for c in (4, 5, 6):
        ws.blank(top + 1, c, "th_r")
    ws.blank(top + 1, 7, "th")
    r = top + 2
    ws.text(r, 1, "v_sales, month total (read separately)", "td")
    ws.put(r, 2, t.get("prev", {}).get("net") or 0, "input_money")
    ws.put(r, 3, t.get("cur", {}).get("net") or 0, "input_money")
    ws.text(r, 7, "Accessories: division not SIM or Devices, every line.", "help")
    r = top + 3
    ws.text(r, 1, "Day book (Data sheet) less v_sales", "td")
    ws.formula(r, 2, f"ROUND(SUMIFS(d_Net,d_Month,PrevMonth)-C{ex(top + 2)},2)", "diff_money")
    ws.formula(r, 3, f"ROUND(SUMIFS(d_Net,d_Month,CurMonth)-D{ex(top + 2)},2)", "diff_money")
    ws.text(r, 7, "Should be nil.", "help")
    masked_prev = sum(1 for ln in model.lines if ln.narration_masked and model.period.in_prev(ln.sale_date))
    masked_cur = sum(1 for ln in model.lines if ln.narration_masked and model.period.in_cur(ln.sale_date))
    r = top + 4
    ws.text(r, 1, "Narrations left out (they hold an ID number)", "td")
    ws.put(r, 2, masked_prev, "input_int")
    ws.put(r, 3, masked_cur, "input_int")
    ws.text(r, 7, "The narration is blank on the Data sheet for these lines.", "help")
    r = top + 5
    narr = f"Data!{aa(lay.data_first, 9)}:{aa(lay.data_last, 9)}"
    ws.text(r, 1, "Narrations on the Data sheet with an ID number", "td")
    ws.formula(r, 4, f'COUNTIF({narr},"*~*~*~*~*~*~*?")', "int_plain")
    ws.text(r, 7, "Should be 0: a masked ID number is six or more stars before its last three digits.", "help")
