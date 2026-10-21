"""The sheets of the weekly sales workbook (/yq-week): Dashboard, Salesmen, Categories, Products, Customers,
Daily, Stock, Movements, Actions, Data, Notes.

THE WINDOWS. The week runs Sunday to Saturday (model.Period.week). Every figure is set beside three other
windows: the four weeks before (their total divided by four: an average week), the same seven days one
month back, and the month to date (the first of the Saturday's month to the Saturday), against the KPI
step. A window is a date range on the Data sheet, SUMIFS(..., d_Date, ">="&start, d_Date, "<="&end), its
bounds named cells of the Notes sheet: WkStart is typed in, the rest are formulas over it. Column K of
the Data sheet is the week key, 7*ROUNDUP(A/7,0): the serial number of a Saturday is a multiple of 7, so
it is the Saturday that ends the line's week, and the Salesmen sheet's five weekly columns add up on it.
Accounts are counted with three flags on the Data sheet: once per account and week (the week, and the
four weekly counts of the average), once per account and month (the month to date), once per account in
the same days last month. Column V is v_sales' giveaway flag: such a line's type is Giveaway, so it counts in
no units, invoices or accounts (as /yq-brief leaves it out), while its value stays in every total.

THE IDIOM is the monthly report's (sheets_core.py): every position follows the list lengths, every figure
is a formula written with the value book.evaluate() works out from it, the formats are the Sep 2026 file's.
The three comment lines of the Dashboard are formulas too, over its figures, the KPI step and the Actions
sheet: no approval step. The Stock and Movements sheets are filled by the shared sheet module when
scripts/ops/workbook/week.py hands one over (Part.data['sheet']); otherwise each reads one line, "Not
worked out this time (...)". The Actions sheet carries counts only; the Stock and Movements sheets totals
only: no item name or voucher number is on any of the three (week.py refuses a shared sheet that has one).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from datetime import date, datetime

from scripts.ops.workbook import categories as cats
from scripts.ops.workbook import formula as fx
from scripts.ops.workbook import model as mdl
from scripts.ops.workbook import sheets_core as sc
from scripts.ops.workbook import style
from scripts.ops.workbook.book import Book, Sheet, aa, rng

COMPANY = sc.COMPANY
FOOTER_LEFT = "YQ Bahrain W.L.L - Mobile accessories weekly sales report - {sheet}"
MARGINS = {"left": 0.4, "right": 0.4, "top": 0.5, "bottom": 0.55}
SHEETS = ("Dashboard", "Salesmen", "Categories", "Products", "Customers", "Daily", "Stock", "Movements", "Actions",
          "Data", "Notes")
TOP_N = 10                    # the Dashboard's top products and accounts, and the salesmen on its chart
AVERAGE_OF = 4                # the four weeks before, divided by four: an average week
WEEK_COLUMNS = 5              # the Salesmen sheet's weekly columns: the four weeks before and the week
NOT_WORKED = "Not worked out this time ({why})."
PAPERS_DAYS = 60              # receipts missing papers, and receipts with no incoming check: the 60 days to the Saturday
DAY_NAMES = ("Sunday","Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
PLACE = "Actions!$C$1"        # a blank cell: what a comment formula names for a part that was not worked out
PLACE_RANGE = "Actions!$C$1:$C$1"

# the four windows: their bounds (named cells of the Notes sheet) and their names in a sentence
WINDOWS = ("wk", "prev", "lm", "mtd")
BOUNDS = {"wk": ("WkStart", "WkEnd"), "prev": ("PrevStart", "PrevEnd"), "lm": ("LmStart", "LmEnd"),
          "mtd": ("MtdStart", "WkEnd")}
WINDOW_NAMES = {"wk": "The week", "prev": "The four weeks before", "lm": "The same days last month",
                "mtd": "The month to date"}

# the Dashboard's measures: key, label, format in the windows table, format of the tile, decimals in the tile
MEASURES = (
    ("net", "Net sales (BHD, excluding VAT)", "money", "tile_money", 0),
    ("units", "Units sold", "money_1dp", "tile_int", 0),
    ("invoices", "Invoices", "money_1dp", "tile_int", 0),
    ("avg_invoice", "Average invoice (BHD)", "money", "tile_1dp", 1),
    ("avg_price", "Average unit price (BHD)", "money", "tile_2dp", 2),
    ("accounts", "Accounts invoiced", "money_1dp", "tile_int", 0),
    ("active", "Salesmen/outlets active", "money_1dp", "tile_int", 0),
)
TILE_COLS = (1, 5, 7, 10, 12, 14, 16)     # the first column of each tile (the net sales tile is four wide)
WIN_COLS = {"wk": 3, "prev": 4, "lm": 6, "mtd": 8}       # D, E, G and I of the windows table
TILE_WORDS = ("avg", "last mth", "to date")
TILE_WORDS_WIDE = ("4-week average", "same days last month", "month to date")
ACCOUNT_FLAG = {"wk": "d_WkAcct", "prev": "d_WkAcct", "lm": "d_LmAcct", "mtd": "d_MonAcct"}

MON3 = '"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"'
MONFULL = ('"January","February","March","April","May","June","July","August","September","October","November",'
           '"December"')
# Sunday-to-Thursday days after the week to the month end: n days after a Saturday start on a Sunday, so they hold
# 5 business days a whole week plus at most 5 of the rest (floor(n/7) is ROUNDUP((n+1)/7,0)-1 for a whole n)
BIZ_DAYS_LEFT = ("5*(ROUNDUP((MonthEnd-WkEnd+1)/7,0)-1)+MIN(MonthEnd-WkEnd-7*(ROUNDUP((MonthEnd-WkEnd+1)/7,0)-1),5)")


def mon3(x: str) -> str:
    return f"CHOOSE(MONTH({x}),{MON3})"


def monfull(x: str) -> str:
    return f"CHOOSE(MONTH({x}),{MONFULL})"


def crit(w: str) -> str:
    """The date criteria of a window, for SUMIFS and COUNTIFS."""
    a, b = BOUNDS[w]
    return f'd_Date,">="&{a},d_Date,"<="&{b}'


def sumifs(col: str, w: str, *pairs: str) -> str:
    """SUMIFS(col, the criteria pairs given, then the window's date range)."""
    lead = "".join(p + "," for p in pairs)
    return f"SUMIFS({col},{lead}{crit(w)})"


def per_week(w: str, formula: str) -> str:
    """The four weeks before as an average week: their total divided by four."""
    return f"{formula}/{AVERAGE_OF}" if w == "prev" else formula


def col(c: int) -> str:
    return fx.col_letters(c)


def ex(r: int) -> int:
    return r + 1


def _join(words: list[str]) -> str:
    words = [w for w in words if w]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def long_day(d: date) -> str:
    return f"{d.day} {mdl.MONTHS[d.month - 1]} {d.year}"


def span_text(a: date, b: date) -> str:
    """'27 August to 3 October 2026', '11 to 17 October 2026' (both years written when they differ)."""
    if (a.year, a.month) == (b.year, b.month):
        first = f"{a.day}"
    else:
        first = f"{a.day} {mdl.MONTHS[a.month - 1]}" + (f" {a.year}" if a.year != b.year else "")
    return f"{first} to {b.day} {mdl.MONTHS[b.month - 1]} {b.year}"


def month_year(d: date | None) -> str:
    return f"{mdl.MONTHS[d.month - 1]} {d.year}" if d else "n/a"


def footer(sheet: str | None) -> str:
    left = COMPANY if sheet is None else FOOTER_LEFT.format(sheet=sheet)
    return f'&L&8&"Arial,Regular"{left}&R&8&"Arial,Regular"Page &P of &N'


def _page(ws: Sheet, **kw) -> None:
    ws.page.update({"margins": dict(MARGINS), "footer": footer(ws.name), "footer_margin": 0.25})
    ws.page.update(kw)


def _para_height(text: str) -> float:
    lines = max(1, math.ceil(len(text) / 165))
    return 15.75 if lines == 1 else 4.5 + 11.25 * lines


# ── what the sheets are handed ─────────────────────────────────────────────────────────────────

@dataclass
class Part:
    """One part of the file beside the sales (a shared sheet, an Actions block): worked out or not, why
    not in a few words (they go in the brackets of its one line), and its figures."""
    ok: bool
    why: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class WeekFacts:
    totals: dict                  # v_sales per window, read on their own: {'wk'|'prev'|'lm'|'mtd': {lines, qty, gross, net}}
    prepared_on: date
    prepared_by: str = ""
    kpi_step: float = 0           # the KPI step in force for the Saturday's month; nought when none is set
    parts: dict = field(default_factory=dict)       # name -> Part (PARTS)
    outlets: tuple = ("Causeway", "YQ Roadshow")
    giveaways: frozenset = frozenset()              # line_key of each line v_sales marks is_giveaway


def line_key(ln: mdl.Line) -> tuple:
    """One day-book line, as the Data sheet holds it: its day, its invoice, its line number and its item."""
    return (ln.sale_date, ln.invoice, ln.line_no, ln.item)


# the parts of the file beside the sales, in the order of the Notes table
PARTS = (("stock", "Stock sheet"), ("movements", "Movements sheet"), ("fast", "Fast movers out of stock"),
         ("order", "Order list by supplier"), ("late", "Goods on the way, late"), ("papers", "Receipts missing papers"),
         ("transfers", "Transfers to check"), ("counts", "Counts due"),
         ("checks", "Receipts with no incoming check logged"))
PART_NAMES = {"fast": "PartFast", "order": "PartOrder", "papers": "PartPapers"}     # the flags a comment line reads


class Layout:
    """Where every block starts and ends (0-based rows), from the list lengths."""

    def __init__(self, model: mdl.Model):
        self.S = len(model.reps)
        self.K = len(model.categories)
        self.P = len(model.products)
        self.H = len(model.not_counted)
        self.C = len(model.customers)
        self.N = len(model.lines)
        self.sal_first, self.sal_last, self.sal_total = 5, 4 + self.S, 5 + self.S
        self.cat_first, self.cat_last, self.cat_total = 5, 4 + self.K, 5 + self.K
        self.prod_first, self.prod_last, self.prod_total = 5, 4 + self.P, 5 + self.P
        self.nc_head = self.prod_total + 1
        self.nc_first = self.nc_head + 1
        self.prod_end = self.nc_first + self.H - 1 if self.H else self.prod_total
        self.cust_first, self.cust_last, self.cust_total = 5, 4 + self.C, 5 + self.C
        self.day_first, self.day_total = 5, 12          # the Daily sheet: Sunday to Saturday, then the total
        self.data_first, self.data_last = 4, 4 + max(self.N, 1) - 1


class Dash:
    """The Dashboard's rows (0-based)."""
    LABEL, VALUE, VS_AVG, VS_LM, TO_DATE, NEED, RULE = 7, 8, 9, 10, 11, 12, 13   # NEED: the net tile's BHD a day
    SAY_HEAD, SAY = 14, 15                       # the three comment lines: 15 to 17
    WIN_HEAD, WIN_NOTE, WIN_TH, WIN = 19, 20, 21, 22      # the seven measures: 22 to 28; the KPI block beside them
    CHART_HEAD, CHART_NOTE, CHART = 30, 31, 32
    TOP_HEAD, TOP_NOTE, TOP_TH, TOP = 53, 54, 55, 56      # ten rows, then other, cash and the totals
    FOOT_RULE, FOOT = 70, 71
    LAST = 73


def window_cell(measure: str, w: str) -> tuple[int, int]:
    """(row, column) of one measure's value in one window, in the Dashboard's windows table."""
    i = [m[0] for m in MEASURES].index(measure)
    return Dash.WIN + i, WIN_COLS[w]


def kpi_cell(i: int) -> str:
    """The KPI block's value cells (column R): 0 step, 1 month to date, 2 share, 3 still to reach, 4 business
    days left, 5 BHD a business day still needed."""
    return f"R{ex(Dash.WIN_TH + i)}"


# ── the book ───────────────────────────────────────────────────────────────────────────────────

def build(model: mdl.Model, facts: WeekFacts) -> Book:
    lay = Layout(model)
    book = Book()
    p = model.period
    book.properties = {
        "title": f"Mobile Accessories Weekly Sales Report - {p.long}", "subject": "Weekly sales report",
        "author": facts.prepared_by or "", "company": COMPANY,
        "created": datetime(facts.prepared_on.year, facts.prepared_on.month, facts.prepared_on.day, 12, 0, 0),
    }
    tabs = {"Dashboard": style.BLUE, "Data": style.MUTED, "Notes": style.MUTED}
    ws = {name: book.sheet(name, tab_color=tabs.get(name)) for name in SHEETS}
    _names(book, lay)
    act = _actions(ws["Actions"], facts)
    _dashboard(ws["Dashboard"], model, lay, facts, act)
    _salesmen(ws["Salesmen"], model, lay)
    _categories(ws["Categories"], model, lay)
    _products(ws["Products"], model, lay)
    _customers(ws["Customers"], model, lay)
    _daily(ws["Daily"])
    _extra(ws["Stock"], facts.parts.get("stock"))
    _extra(ws["Movements"], facts.parts.get("movements"))
    _data(ws["Data"], model, lay, facts.giveaways)
    book.controls = _notes(ws["Notes"], book, model, lay, facts)
    book.commentary = {}                        # the comment lines are formulas: nothing waits for an approval
    return book


def fit_rows(book: Book) -> None:
    """After the evaluation: each comment line's row as high as its words need."""
    dash = book.get("Dashboard")
    for k in range(3):
        cell = dash.cells.get((Dash.SAY + k, 1))
        text = str(cell.value) if cell is not None and isinstance(cell.value, str) else ""
        dash.row(Dash.SAY + k, 6 + 11.25 * max(2, math.ceil(len(text) / 190)))


def _names(book: Book, lay: Layout) -> None:
    d1, d2 = lay.data_first, lay.data_last
    for name, c in (("d_Date", 0), ("d_Cust", 2), ("d_Item", 3), ("d_Qty", 4), ("d_Gross", 6), ("d_Net", 7),
                    ("d_Wk", 10), ("d_Month", 11), ("d_Rep", 12), ("d_Cat", 13), ("d_VAT", 14), ("d_Units", 16),
                    ("d_InvFlag", 17), ("d_WkAcct", 18), ("d_MonAcct", 19), ("d_LmAcct", 20)):
        book.define(name, sc.col_range("Data", c, d1, d2))
    book.define("p_Cat", sc.col_range("Products", 3, lay.prod_first, lay.prod_end))
    book.define("p_Item", sc.col_range("Products", 12, lay.prod_first, lay.prod_end))


# ── Dashboard ──────────────────────────────────────────────────────────────────────────────────

def measure_formula(m: str, w: str, lay: Layout, ref) -> str:
    """One measure in one window. ref(measure, window) names that cell of the windows table."""
    if m == "net":
        return per_week(w, sumifs("d_Net", w))
    if m == "units":
        return per_week(w, sumifs("d_Units", w))
    if m == "invoices":
        return per_week(w, sumifs("d_InvFlag", w))
    if m == "avg_invoice":
        return f"IF({ref('invoices', w)}=0,0,{ref('net', w)}/{ref('invoices', w)})"
    if m == "avg_price":
        return f"IF({ref('units', w)}=0,0,{ref('net', w)}/{ref('units', w)})"
    if m == "accounts":
        return per_week(w, sumifs(ACCOUNT_FLAG[w], w))
    if m == "active":
        if w == "prev":           # the four weekly columns before the week: an average of their counts
            counts = "+".join(f'COUNTIF(Salesmen!{rng(lay.sal_first, c, lay.sal_last, c)},">0")'
                              for c in range(12, 12 + AVERAGE_OF))
            return f"({counts})/{AVERAGE_OF}"
        c = {"wk": 2, "lm": 5, "mtd": 7}[w]
        return f'COUNTIF(Salesmen!{rng(lay.sal_first, c, lay.sal_last, c)},">0")'
    raise KeyError(m)


def comment_formulas(win: dict, act: dict) -> list[str]:
    """The three comment lines, as formulas: the week against the four-week average and the same days last
    month; the month to date against the KPI step; the fast movers, the order list and the receipts' papers."""
    n = ex(win["net"])
    wk, avg, lm, mtd = f"D{n}", f"E{n}", f"G{n}", f"I{n}"
    vs_avg = (f'IF({avg}=0,"with no sales in the four weeks before",'
              f'IF(ROUND({wk}/{avg}-1,2)=0,"level with",FIXED(ABS({wk}/{avg}-1)*100,0)&"% "&IF({wk}>{avg},"above",'
              f'"below"))&" the four-week average of BHD "&FIXED({avg},0))')
    vs_lm = (f'IF({lm}=0,"no sales on the same days last month",'
             f'IF(ROUND({wk}/{lm}-1,2)=0,"level with",FIXED(ABS({wk}/{lm}-1)*100,0)&"% "&IF({wk}>{lm},"above",'
             f'"below"))&" the same days last month (BHD "&FIXED({lm},0)&")")')
    first = f'"Net sales in "&WkLong&" were BHD "&FIXED({wk},0)&", "&{vs_avg}&", and "&{vs_lm}&"."'
    share, still, per_day = kpi_cell(2), kpi_cell(3), kpi_cell(5)
    second = (f'IF(KpiStep>0,"Month to date ("&MtdLabel&"): BHD "&FIXED({mtd},0)&", "&FIXED({share}*100,0)&"% of '
              f'the KPI step of BHD "&FIXED(KpiStep,0)&IF({still}=0,"; the step is reached.","; BHD "&FIXED({still},0)'
              f'&" to go"&IF(BizDaysLeft>0,", BHD "&FIXED({per_day},0)&" a business day over the "&BizDaysLeft&'
              f'" business day"&IF(BizDaysLeft=1,"","s")&" left.",", and no business day is left in "&MonthLong&".")),'
              f'"Month to date ("&MtdLabel&"): BHD "&FIXED({mtd},0)&"; no KPI step is set for "&MonthLong&".")')
    out, fast_n = act["fast_out"], act["fast_n"]
    fast = (f'IF(PartFast=1,IF({out}=0,"none of the "&{fast_n}&" fast movers was",{out}&" of the "&{fast_n}&'
            f'" fast movers "&IF({out}=1,"was","were"))&" out of stock at the end of the Saturday",'
            f'"the fast movers were not worked out this time")')
    items, units, sup = act["order_items"], act["order_units"], f'COUNTIF({act["order_range"]},">0")'
    order = (f'IF(PartOrder=1,IF({items}=0,"the order list is empty","the order list holds "&{items}&" item"&'
             f'IF({items}=1,"","s")&" ("&FIXED({units},0)&" units) from "&{sup}&" supplier"&IF({sup}=1,"","s")),'
             f'"the order list was not worked out this time")')
    miss, days = act["papers_missing"], act["papers_days"]
    papers = (f'IF(PartPapers=1,IF({miss}=0,"no receipt of the last {days} days misses a paper",{miss}&" receipt"&'
              f'IF({miss}=1," misses","s miss")&" a paper (last {days} days)"),'
              f'"the receipts\' papers were not checked this time")')
    third = f'"Stock and papers: "&{fast}&"; "&{order}&"; "&{papers}&"."'
    return [first, second, third]


def _dashboard(ws: Sheet, model: mdl.Model, lay: Layout, facts: WeekFacts, act: dict) -> None:
    ws.col(0, 0, 2.28515625)
    ws.col(1, 8, 10.28515625)
    ws.col(9, 9, 3.42578125)
    ws.col(10, 17, 10.28515625)
    ws.col(18, 18, 2.28515625)
    for r, h in ((0, 4.5), (1, 9), (2, 12), (3, 24), (4, 13.5), (5, 6), (6, 9), (Dash.LABEL, 13.5), (Dash.VALUE, 39),
                 (Dash.VS_AVG, 13.5), (Dash.VS_LM, 13.5), (Dash.TO_DATE, 13.5), (Dash.NEED, 13.5), (Dash.RULE, 6),
                 (Dash.SAY_HEAD, 18), (18, 12), (Dash.WIN_HEAD, 18), (Dash.WIN_NOTE, 13.5), (Dash.WIN_TH, 24),
                 (29, 12), (Dash.CHART_HEAD, 18), (Dash.CHART_NOTE, 13.5), (52, 12), (Dash.TOP_HEAD, 18),
                 (Dash.TOP_NOTE, 13.5), (Dash.TOP_TH, 22.5), (69, 10.5), (Dash.FOOT_RULE, 4.5), (Dash.FOOT, 11.25),
                 (Dash.FOOT + 1, 11.25), (Dash.FOOT + 2, 11.25)):
        ws.row(r, h)
    ws.blanks(0, 0, 18, "band")
    ws.merge(2, 1, 2, 8, "YQ BAHRAIN W.L.L", "company")
    ws.merge(2, 10, 2, 17, None, "meta_r",
             formula='IF(PreparedBy="","Prepared ","Prepared by "&PreparedBy&", ")&PreparedText')
    ws.merge(3, 1, 3, 17, "Mobile Accessories Weekly Sales Report", "report_title")
    ws.merge(4, 1, 4, 8, None, "report_sub",
             formula=f'"Week ending "&DAY(WkEnd)&" "&{monfull("WkEnd")}&" "&YEAR(WkEnd)&": Sunday "&DAY(WkStart)&" "&'
                     f'{mon3("WkStart")}&" to Saturday "&DAY(WkEnd)&" "&{mon3("WkEnd")}')
    ws.merge(4, 10, 4, 17, "Source: ERP sales day book", "meta_r")
    ws.blanks(5, 1, 17, "rule_medium")

    win = {m[0]: Dash.WIN + i for i, m in enumerate(MEASURES)}

    def ref(m: str, w: str) -> str:
        return f"{col(WIN_COLS[w])}{ex(win[m])}"

    # the tiles: the week, beside the four-week average, the same days last month and the month to date
    for i, (key, label, _tfmt, vfmt, dp) in enumerate(MEASURES):
        n = ex(win[key])
        c = TILE_COLS[i]
        wide = i == 0
        last = c + (3 if wide else 1)
        words = TILE_WORDS_WIDE if wide else TILE_WORDS
        ws.merge(Dash.LABEL, c, Dash.LABEL, last, label, "note" if wide else "tile_label")
        ws.merge(Dash.VALUE, c, Dash.VALUE, last, None, vfmt, formula=f"D{n}")
        for row, base, word in ((Dash.VS_AVG, "E", words[0]), (Dash.VS_LM, "G", words[1])):
            ws.formula(row, c, f"IF({base}{n}=0,0,D{n}/{base}{n}-1)", "tile_change_first" if wide else "tile_change")
            text = f'"{word} "&FIXED({base}{n},{dp})'
            if wide:
                ws.merge(row, c + 1, row, last, None, "tile_from_int", formula=text)
            else:
                ws.formula(row, c + 1, text, "tile_from_int")
        to_date = f'"{words[2]} "&FIXED(I{n},{dp})'
        if key == "net":
            to_date += f'&IF(KpiStep>0,", "&FIXED({kpi_cell(2)}*100,0)&"% of the KPI step","")'
        ws.merge(Dash.TO_DATE, c, Dash.TO_DATE, last, None, "note" if wide else "tile_foot", formula=to_date)
        if key == "net":          # the month to date against the step: the BHD a business day still needed
            still, per_day = kpi_cell(3), kpi_cell(5)
            ws.merge(Dash.NEED, c, Dash.NEED, last, None, "note",
                     formula=f'IF(KpiStep>0,IF({still}=0,"the KPI step is reached",IF(BizDaysLeft>0,"BHD "&'
                             f'FIXED({per_day},0)&" a business day needed ("&BizDaysLeft&" left)","BHD "&'
                             f'FIXED({still},0)&" short, no business day left")),"no KPI step for "&MonthLong)')
        else:
            ws.merge(Dash.NEED, c, Dash.NEED, last, None, "tile_foot")
    for r in range(Dash.LABEL, Dash.NEED + 1):
        ws.blank(r, 9, "gutter")
    ws.blanks(Dash.RULE, 1, 17, "rule_thin")

    # the three comment lines
    ws.merge(Dash.SAY_HEAD, 1, Dash.SAY_HEAD, 17, "The week in three lines", "section")
    for k, text in enumerate(comment_formulas(win, act)):
        ws.merge(Dash.SAY + k, 1, Dash.SAY + k, 17, None, "comment", formula=text)
        ws.row(Dash.SAY + k, 28.5)

    # the windows table, and the month to date against the KPI step beside it
    ws.merge(Dash.WIN_HEAD, 1, Dash.WIN_HEAD, 8, "The week against the other three windows", "section")
    ws.merge(Dash.WIN_NOTE, 1, Dash.WIN_NOTE, 8, "BHD, excluding VAT. The four-week average is the four weeks before "
                                                 "the week divided by four.", "note")
    th = Dash.WIN_TH
    ws.merge(th, 1, th, 2, "Measure", "th")
    ws.formula(th, 3, "WkLabel", "th_r")
    ws.text(th, 4, "4-week average", "th_r")
    ws.text(th, 5, "Change", "th_r")
    ws.formula(th, 6, "LmLabel", "th_r")
    ws.text(th, 7, "Change", "th_r")
    ws.formula(th, 8, "MtdLabel", "th_r")
    for i, (key, label, tfmt, _vfmt, _dp) in enumerate(MEASURES):
        r = Dash.WIN + i
        n = ex(r)
        ws.merge(r, 1, r, 2, label, "td")
        for w, c in WIN_COLS.items():
            ws.formula(r, c, measure_formula(key, w, lay, ref), tfmt)
        ws.formula(r, 5, f'IF(E{n}=0,IF(D{n}=0,0,"n/a"),D{n}/E{n}-1)', "change_pct")
        ws.formula(r, 7, f'IF(G{n}=0,IF(D{n}=0,0,"n/a"),D{n}/G{n}-1)', "change_pct")
    ws.merge(Dash.WIN_HEAD, 10, Dash.WIN_HEAD, 17, "Month to date against the KPI step", "section")
    ws.merge(Dash.WIN_NOTE, 10, Dash.WIN_NOTE, 17, None, "note",
             formula='"BHD, excluding VAT. The step in force for "&MonthLong&"; business days are Sunday to Thursday."')
    mtd = ref("net", "mtd")
    kpi = ((True, '"KPI step, "&MonthLong', "KpiStep", "money"),
           (True, '"Month to date, "&MtdLabel', mtd, "money"),
           (False, "Share of the step", f"IF(KpiStep>0,{kpi_cell(1)}/KpiStep,0)", "share"),
           (False, "Still to reach the step", f"IF(KpiStep>{kpi_cell(1)},KpiStep-{kpi_cell(1)},0)", "money"),
           (False, "Business days left after the week", "BizDaysLeft", "int"),
           (False, "BHD a business day still needed",
            f"IF(AND({kpi_cell(3)}>0,{kpi_cell(4)}>0),{kpi_cell(3)}/{kpi_cell(4)},0)", "money"))
    for i, (is_formula, label, formula, f) in enumerate(kpi):
        r = Dash.WIN_TH + i
        if is_formula:
            ws.merge(r, 10, r, 16, None, "td", formula=label)
        else:
            ws.merge(r, 10, r, 16, label, "td")
        ws.formula(r, 17, formula, f)
    ws.merge(Dash.WIN_TH + len(kpi), 10, Dash.WIN_TH + len(kpi), 17, None, "note",
             formula='IF(KpiStep>0,"","No KPI step is set for "&MonthLong&": the share and the BHD a day read nought.")')

    # the charts
    top_reps = min(TOP_N, lay.S)
    ws.merge(Dash.CHART_HEAD, 1, Dash.CHART_HEAD, 8, "Net sales by day of the week", "section")
    ws.merge(Dash.CHART_NOTE, 1, Dash.CHART_NOTE, 8, "BHD, excluding VAT. Each day of the week against the average of "
                                                     "the same weekday in the four weeks before.", "note")
    ws.merge(Dash.CHART_HEAD, 10, Dash.CHART_HEAD, 17, "Net sales by salesman", "section")
    ws.merge(Dash.CHART_NOTE, 10, Dash.CHART_NOTE, 17, None, "note",
             formula=f'"BHD, excluding VAT. Top {top_reps} of "&COUNTA(Salesmen!{rng(lay.sal_first, 1, lay.sal_last, 1)})'
                     f'&" by the week, against their four-week average; the rest are on the Salesmen sheet."')
    ws.charts.append((Dash.CHART, 1, daily_chart(), {"object_position": 2, "description": (
        "Column chart of net sales by day of the week, against the average of the same weekday in the four weeks "
        "before.")}))
    ws.charts.append((Dash.CHART, 10, sc.bar_chart("Salesmen", 1, 3, 2, lay.sal_first, lay.sal_first + top_reps - 1),
                      {"object_position": 2, "description": "Bar chart of net sales for the largest salesmen, the "
                                                            "week against their four-week average."}))

    # the top products and the top accounts of the week
    pr = lambda c: f"Products!{aa(lay.prod_first, c)}:{aa(lay.prod_last, c)}"  # noqa: E731
    cu = lambda c: f"Customers!{aa(lay.cust_first, c)}:{aa(lay.cust_last, c)}"  # noqa: E731
    ws.merge(Dash.TOP_HEAD, 1, Dash.TOP_HEAD, 8, f"Top {TOP_N} products", "section")
    ws.merge(Dash.TOP_NOTE, 1, Dash.TOP_NOTE, 8, None, "note", formula='"Net sales in BHD, ranked by "&WkLong&"."')
    ws.merge(Dash.TOP_HEAD, 10, Dash.TOP_HEAD, 17, f"Top {TOP_N} accounts", "section")
    ws.merge(Dash.TOP_NOTE, 10, Dash.TOP_NOTE, 17, None, "note",
             formula='"Net sales in BHD, ranked by "&WkLong&". Cash customers are shown separately."')
    th = Dash.TOP_TH
    ws.merge(th, 1, th, 5, "Product", "mini_th")
    ws.text(th, 6, "4-week average", "mini_th_r")
    ws.formula(th, 7, "WkLabel", "mini_th_r")
    ws.text(th, 8, "Change", "mini_th_r")
    ws.merge(th, 10, th, 14, "Customer", "mini_th")
    ws.text(th, 15, "4-week average", "mini_th_r")
    ws.formula(th, 16, "WkLabel", "mini_th_r")
    ws.text(th, 17, "Change", "mini_th_r")
    accounts = len([c for c in model.customers if c != mdl.CASH_ACCOUNT])
    for k in range(TOP_N):
        r = Dash.TOP + k
        n = ex(r)
        if k < lay.P:
            ws.merge(r, 1, r, 5, None, "mini_td", formula=f"INDEX({pr(14)},MATCH({k + 1},{pr(13)},0))")
            ws.formula(r, 6, f"INDEX({pr(7)},MATCH({k + 1},{pr(13)},0))", "mini_int")
            ws.formula(r, 7, f"INDEX({pr(6)},MATCH({k + 1},{pr(13)},0))", "mini_int")
            ws.formula(r, 8, f"H{n}-G{n}", "mini_change")
        else:
            ws.merge(r, 1, r, 5, None, "mini_td")
            ws.blank(r, 6, "mini_int")
            ws.blank(r, 7, "mini_int")
            ws.blank(r, 8, "mini_change")
        if k < accounts:
            ws.merge(r, 10, r, 14, None, "mini_td", formula=f"INDEX({cu(13)},MATCH({k + 1},{cu(12)},0))")
            ws.formula(r, 15, f"INDEX({cu(5)},MATCH({k + 1},{cu(12)},0))", "mini_int")
            ws.formula(r, 16, f"INDEX({cu(4)},MATCH({k + 1},{cu(12)},0))", "mini_int")
            ws.formula(r, 17, f"Q{n}-P{n}", "mini_change")
        else:
            ws.merge(r, 10, r, 14, None, "mini_td")
            ws.blank(r, 15, "mini_int")
            ws.blank(r, 16, "mini_int")
            ws.blank(r, 17, "mini_change")
    first, last = ex(Dash.TOP), ex(Dash.TOP + TOP_N - 1)
    other = Dash.TOP + TOP_N
    total_p, cash, total_a = other + 1, other + 1, other + 2
    ws.merge(other, 1, other, 5, "Other products", "mini_td")
    ws.formula(other, 6, f"G{ex(total_p)}-SUM(G{first}:G{last})", "mini_int")
    ws.formula(other, 7, f"H{ex(total_p)}-SUM(H{first}:H{last})", "mini_int")
    ws.formula(other, 8, f"H{ex(other)}-G{ex(other)}", "mini_change")
    ws.merge(total_p, 1, total_p, 5, "Total", "mini_total")
    ws.formula(total_p, 6, f"Products!H{ex(lay.prod_total)}", "mini_total_int")
    ws.formula(total_p, 7, f"Products!G{ex(lay.prod_total)}", "mini_total_int")
    ws.formula(total_p, 8, f"H{ex(total_p)}-G{ex(total_p)}", "mini_total_change")
    ws.merge(other, 10, other, 14, "Other accounts", "mini_td")
    ws.formula(other, 15, f"P{ex(total_a)}-SUM(P{first}:P{last})-P{ex(cash)}", "mini_int")
    ws.formula(other, 16, f"Q{ex(total_a)}-SUM(Q{first}:Q{last})-Q{ex(cash)}", "mini_int")
    ws.formula(other, 17, f"Q{ex(other)}-P{ex(other)}", "mini_change")
    ws.merge(cash, 10, cash, 14, "Cash customers", "mini_td")
    ws.formula(cash, 15, f'SUMIFS({cu(5)},{cu(2)},"Cash")', "mini_int")
    ws.formula(cash, 16, f'SUMIFS({cu(4)},{cu(2)},"Cash")', "mini_int")
    ws.formula(cash, 17, f"Q{ex(cash)}-P{ex(cash)}", "mini_change")
    ws.merge(total_a, 10, total_a, 14, "Total", "mini_total")
    ws.formula(total_a, 15, f"Customers!F{ex(lay.cust_total)}", "mini_total_int")
    ws.formula(total_a, 16, f"Customers!E{ex(lay.cust_total)}", "mini_total_int")
    ws.formula(total_a, 17, f"Q{ex(total_a)}-P{ex(total_a)}", "mini_total_change")

    # the footnotes
    ws.blanks(Dash.FOOT_RULE, 1, 17, "rule_thin")
    for k, text in enumerate(footnotes(model, facts)):
        ws.merge(Dash.FOOT + k, 1, Dash.FOOT + k, 17, text, "footnote")

    # rules: the order they are added is their priority (as in the monthly file)
    for rule in style.change_rules(with_na=False):
        ws.cond(" ".join(f"{col(c)}{ex(row)}" for row in (Dash.VS_AVG, Dash.VS_LM) for c in TILE_COLS), rule)
    w1, w2 = ex(Dash.WIN), ex(Dash.WIN + len(MEASURES) - 1)
    for rule in style.change_rules():
        ws.cond(f"F{w1}:F{w2} H{w1}:H{w2}", rule)
    for rule in style.change_rules():
        ws.cond(f"I{first}:I{ex(total_p)}", rule)
    ws.cond(f"H{first}:H{last}", dict(style.DATA_BAR))
    for rule in style.change_rules():
        ws.cond(f"R{first}:R{ex(total_a)}", rule)
    ws.cond(f"Q{first}:Q{last}", dict(style.DATA_BAR))
    ws.page.update({"landscape": True, "scale": 80, "center": True, "hide_headers": True,
                    "margins": {"left": 0.35, "right": 0.35, "top": 0.4, "bottom": 0.5},
                    "footer": footer(None), "footer_margin": 0.25, "breaks": [Dash.CHART_HEAD - 1],
                    "print_area": f"A1:S{ex(Dash.LAST)}", "selection": "A2"})


def footnotes(model: mdl.Model, facts: WeekFacts) -> list[str]:
    p = model.period
    first = (f"Net sales are the taxable value of sales invoices, excluding 10% VAT, from the ERP sales day book for "
             f"{span_text(p.data_first, p.end)}. Returns are not deducted. Figures are rounded, so columns may not add "
             f"exactly.")
    second = ("The week runs Sunday to Saturday. The four-week average is the four weeks before it divided by four; the "
              "same days last month are the same seven days one month back.")
    third = "Units, invoices and accounts cover accessories only."
    if model.not_counted:
        codes = _join([it.code for it in model.not_counted])
        said = sc.issuers_of(model, {it.item for it in model.not_counted}, facts.outlets)
        third += f" {codes} handsets issued through {said} carry no value in the day book and are excluded."
    third += f" Salesman is the issuing warehouse; an account is any customer other than {mdl.CASH_ACCOUNT}."
    return [first, second, third]


def daily_chart() -> dict:
    cats_ref = "=Daily!$B$6:$B$12"
    return {
        "type": "column",
        "series": [
            {"name": "=Daily!$E$5", "categories": cats_ref, "values": "=Daily!$E$6:$E$12",
             "fill": {"color": style.PALE}, "border": {"none": True}, "gap": 60, "overlap": -10},
            {"name": "=Daily!$D$5", "categories": cats_ref, "values": "=Daily!$D$6:$D$12",
             "fill": {"color": style.BLUE}, "border": {"none": True},
             "data_labels": {"value": True, "num_format": "#,##0", "font": style.font(8)}},
        ],
        "x_axis": {"num_font": style.font(8, style.MUTED), "line": {"color": style.LINE, "width": 0.75},
                   "major_tick_mark": "none"},
        "y_axis": {"num_format": "#,##0", "num_font": style.font(8, style.MUTED), "major_tick_mark": "none",
                   "line": {"none": True},
                   "major_gridlines": {"visible": True, "line": {"color": style.HAIR, "width": 0.75}}},
        "size": {"width": 576, "height": 300},
    }


# ── Salesmen ───────────────────────────────────────────────────────────────────────────────────

def _salesmen(ws: Sheet, model: mdl.Model, lay: Layout) -> None:
    ws.default_row = 20
    ws.col(0, 0, 2.28515625)
    ws.col(1, 1, 24.28515625)
    ws.col(2, 16, 11.7109375)
    ws.col(17, 17, 18.5703125)
    ws.row(4, 24)
    ws.text(0, 1, "Sales by salesman and outlet", "sheet_title")
    ws.formula(1, 1, 'WkLong&" ("&WkSpan&") against the four weeks before, the same days last month and the month '
                     'to date. Net sales in BHD, excluding VAT."', "sheet_sub")
    ws.merge(3, 2, 3, 8, "Net sales (BHD)", "group_head")
    ws.merge(3, 9, 3, 11, "The week", "group_head")
    ws.merge(3, 12, 3, 16, "Net sales by week (BHD)", "group_head")
    ws.text(3, 17, "Trend", "group_head")
    ws.text(4, 1, "Salesman / outlet", "th")
    for c, head in ((2, "=WkLabel"), (3, "4-week average"), (4, "Change %"), (5, "=LmLabel"), (6, "Change %"),
                    (7, "=MtdLabel"), (8, "Share of the week"), (9, "Units"), (10, "Invoices"), (11, "Avg invoice"),
                    (17, '="5 weeks to "&DAY(WkEnd)&" "&' + mon3("WkEnd"))):
        if head.startswith("="):
            ws.formula(4, c, head, "th_r")
        else:
            ws.text(4, c, head, "th_r")
    for j in range(WEEK_COLUMNS):         # each weekly column is headed by the Saturday its week ends on
        back = 7 * (WEEK_COLUMNS - 1 - j)
        ws.formula(4, 12 + j, f"WkEnd-{back}" if back else "WkEnd", "th_date")
    tot = ex(lay.sal_total)
    for i, rep in enumerate(model.reps):
        r = lay.sal_first + i
        n = ex(r)
        rep_ = f"d_Rep,$B{n}"
        ws.text(r, 1, rep, "td")
        ws.formula(r, 2, sumifs("d_Net", "wk", rep_), "money")
        ws.formula(r, 3, per_week("prev", sumifs("d_Net", "prev", rep_)), "money")
        ws.formula(r, 4, f'IF(D{n}=0,IF(C{n}=0,0,"n/a"),C{n}/D{n}-1)', "change_pct")
        ws.formula(r, 5, sumifs("d_Net", "lm", rep_), "money")
        ws.formula(r, 6, f'IF(F{n}=0,IF(C{n}=0,0,"n/a"),C{n}/F{n}-1)', "change_pct")
        ws.formula(r, 7, sumifs("d_Net", "mtd", rep_), "money")
        ws.formula(r, 8, f"IF(C${tot}=0,0,C{n}/C${tot})", "share")
        ws.formula(r, 9, sumifs("d_Units", "wk", rep_), "int")
        ws.formula(r, 10, sumifs("d_InvFlag", "wk", rep_), "int")
        ws.formula(r, 11, f"IF(K{n}=0,0,C{n}/K{n})", "money_1dp")
        for j in range(WEEK_COLUMNS):
            c = 12 + j
            ws.formula(r, c, f"SUMIFS(d_Net,d_Rep,$B{n},d_Wk,{col(c)}$5)", "money")
        ws.blank(r, 17, "td")
        ws.sparklines.append(dict(style.SPARKLINE, location=f"R{n}", range=f"Salesmen!M{n}:Q{n}"))
    f1, l1 = ex(lay.sal_first), ex(lay.sal_last)
    r = lay.sal_total
    ws.text(r, 1, "Total", "total_label")
    for c in (2, 3, 5, 7) + tuple(range(12, 12 + WEEK_COLUMNS)):
        ws.formula(r, c, f"SUM({col(c)}{f1}:{col(c)}{l1})", "total_money")
    ws.formula(r, 4, f"IF(D{tot}=0,0,C{tot}/D{tot}-1)", "total_change_pct")
    ws.formula(r, 6, f"IF(F{tot}=0,0,C{tot}/F{tot}-1)", "total_change_pct")
    ws.formula(r, 8, f"SUM(I{f1}:I{l1})", "total_share")
    ws.formula(r, 9, f"SUM(J{f1}:J{l1})", "total_int")
    ws.formula(r, 10, f"SUM(K{f1}:K{l1})", "total_int")
    ws.formula(r, 11, f"IF(K{tot}=0,0,C{tot}/K{tot})", "total_1dp")
    ws.blank(r, 17, "total_label")
    outlets = [x for x in model.reps if x in ("Causeway", "YQ Roadshow")]
    note = "Salesman is the warehouse the invoice was issued from."
    if outlets:
        note += f" {_join(outlets)} {'is an outlet' if len(outlets) == 1 else 'are outlets'}."
    ws.merge(r + 1, 1, r + 1, 17, note + " Units and invoices cover accessories only.", "note")
    ws.merge(r + 2, 1, r + 2, 17, "The four-week average is the four weeks before divided by four. The weekly columns "
                                  "are Sunday-to-Saturday weeks headed by the Saturday they end on; the Trend column "
                                  "charts them.", "note")
    for rule in style.change_rules():
        ws.cond(f"E{f1}:E{tot} G{f1}:G{tot}", rule)
    ws.cond(f"I{f1}:I{l1}", dict(style.DATA_BAR))
    _page(ws, landscape=True, fit=(1, 0), print_area=f"A1:R{ex(r + 2)}")


# ── Categories ─────────────────────────────────────────────────────────────────────────────────

def _categories(ws: Sheet, model: mdl.Model, lay: Layout) -> None:
    ws.default_row = 20
    ws.col(0, 0, 2.28515625)
    ws.col(1, 1, 27.140625)
    ws.col(2, 11, 12.5703125)
    ws.row(4, 24)
    ws.text(0, 1, "Sales by category", "sheet_title")
    ws.formula(1, 1, 'WkLong&" against the four weeks before, the same days last month and the month to date. Net '
                     'sales in BHD, excluding VAT. Accessories only."', "sheet_sub")
    ws.merge(3, 3, 3, 4, "Units", "group_head")
    ws.merge(3, 5, 3, 10, "Net sales (BHD)", "group_head")
    ws.text(3, 11, "Avg unit price", "group_head")
    ws.text(4, 1, "Category", "th")
    for c, head in ((2, '="Products sold, "&WkLabel'), (3, "=WkLabel"), (4, "4-week average"), (5, "=WkLabel"),
                    (6, "4-week average"), (7, "Change %"), (8, "Share of the week"), (9, "=LmLabel"),
                    (10, "=MtdLabel"), (11, "=WkLabel")):
        if head.startswith("="):
            ws.formula(4, c, head, "th_r")
        else:
            ws.text(4, c, head, "th_r")
    tot = ex(lay.cat_total)
    p_cat = f"Products!{aa(lay.prod_first, 3)}:{aa(lay.prod_last, 3)}"
    p_units = f"Products!{aa(lay.prod_first, 4)}:{aa(lay.prod_last, 4)}"
    for i, name in enumerate(model.categories):
        r = lay.cat_first + i
        n = ex(r)
        cat_ = f"d_Cat,$B{n}"
        ws.text(r, 1, name, "td")
        ws.formula(r, 2, f'COUNTIFS({p_cat},$B{n},{p_units},">0")', "int")
        ws.formula(r, 3, sumifs("d_Units", "wk", cat_), "int")
        ws.formula(r, 4, per_week("prev", sumifs("d_Units", "prev", cat_)), "money_1dp")
        ws.formula(r, 5, sumifs("d_Net", "wk", cat_), "money")
        ws.formula(r, 6, per_week("prev", sumifs("d_Net", "prev", cat_)), "money")
        ws.formula(r, 7, f'IF(G{n}=0,IF(F{n}=0,0,"n/a"),F{n}/G{n}-1)', "change_pct")
        ws.formula(r, 8, f"IF(F${tot}=0,0,F{n}/F${tot})", "share")
        ws.formula(r, 9, sumifs("d_Net", "lm", cat_), "money")
        ws.formula(r, 10, sumifs("d_Net", "mtd", cat_), "money")
        ws.formula(r, 11, f"IF(D{n}=0,0,F{n}/D{n})", "money")
    f1, l1 = ex(lay.cat_first), ex(lay.cat_last)
    r = lay.cat_total
    ws.text(r, 1, "Total", "total_label")
    for c, f in ((2, "total_int"), (3, "total_int"), (4, "total_1dp"), (5, "total_money"), (6, "total_money"),
                 (9, "total_money"), (10, "total_money")):
        ws.formula(r, c, f"SUM({col(c)}{f1}:{col(c)}{l1})", f)
    ws.formula(r, 7, f"IF(G{tot}=0,0,F{tot}/G{tot}-1)", "total_change_pct")
    ws.formula(r, 8, f"SUM(I{f1}:I{l1})", "total_share")
    ws.formula(r, 11, f"IF(D{tot}=0,0,F{tot}/D{tot})", "total_money")
    ws.merge(r + 1, 1, r + 1, 11, "Category comes from the item code (see Notes). Average unit price is net sales "
                                  "divided by units.", "note")
    for rule in style.change_rules():
        ws.cond(f"H{f1}:H{tot}", rule)
    ws.cond(f"I{f1}:I{l1}", dict(style.DATA_BAR))
    _page(ws, landscape=True, fit=(1, 1))


# ── Products ───────────────────────────────────────────────────────────────────────────────────

def _products(ws: Sheet, model: mdl.Model, lay: Layout) -> None:
    ws.default_row = 18
    for c1, c2, w in ((0, 0, 2.28515625), (1, 1, 9.140625), (2, 2, 60), (3, 3, 22.85546875), (4, 11, 12),
                      (12, 12, 85.7109375), (13, 13, 10), (14, 14, 52.85546875)):
        ws.col(c1, c2, w)
    ws.row(4, 24)
    ws.text(0, 1, "Sales by product", "sheet_title")
    ws.formula(1, 1, 'WkLong&" against the four weeks before, the same days last month and the month to date. Net '
                     'sales in BHD, excluding VAT. Accessories only, listed by the week\'s sales."', "sheet_sub")
    ws.merge(3, 4, 3, 5, "Units", "group_head")
    ws.merge(3, 6, 3, 11, "Net sales (BHD)", "group_head")
    ws.merge(3, 12, 3, 14, "Reference columns", "ref_group_head")
    for c, head in ((1, "Code"), (2, "Product"), (3, "Category")):
        ws.text(4, c, head, "th")
    for c, head in ((4, "=WkLabel"), (5, "4-week average"), (6, "=WkLabel"), (7, "4-week average"), (8, "Change %"),
                    (9, "Share of the week"), (10, "=LmLabel"), (11, "=MtdLabel")):
        if head.startswith("="):
            ws.formula(4, c, head, "th_r")
        else:
            ws.text(4, c, head, "th_r")
    ws.text(4, 12, "ERP item description", "ref_th")
    ws.formula(4, 13, '"Rank, "&WkLabel', "ref_th_r")
    ws.text(4, 14, "Dashboard label", "ref_th")
    tot = ex(lay.prod_total)
    f1, l1 = ex(lay.prod_first), ex(lay.prod_last)
    for i, it in enumerate(model.products):
        r = lay.prod_first + i
        n = ex(r)
        item_ = f"d_Item,$M{n}"
        ws.text(r, 1, it.code, "td")
        ws.text(r, 2, it.product, "td")
        ws.text(r, 3, it.category, "td")
        ws.formula(r, 4, sumifs("d_Units", "wk", item_), "int")
        ws.formula(r, 5, per_week("prev", sumifs("d_Units", "prev", item_)), "money_1dp")
        ws.formula(r, 6, sumifs("d_Net", "wk", item_), "money")
        ws.formula(r, 7, per_week("prev", sumifs("d_Net", "prev", item_)), "money")
        ws.formula(r, 8, f'IF(H{n}=0,IF(G{n}=0,0,"n/a"),G{n}/H{n}-1)', "change_pct")
        ws.formula(r, 9, f"IF(G${tot}=0,0,G{n}/G${tot})", "share")
        ws.formula(r, 10, sumifs("d_Net", "lm", item_), "money")
        ws.formula(r, 11, sumifs("d_Net", "mtd", item_), "money")
        ws.text(r, 12, it.item, "ref_td")
        ws.formula(r, 13, f"SUMPRODUCT(--($G${f1}:$G${l1}>G{n}))+SUMPRODUCT(--($G${f1}:G{n}=G{n}))", "ref_int")
        ws.text(r, 14, mdl.dashboard_label(it.code, it.product), "ref_td")
    r = lay.prod_total
    ws.text(r, 1, "Total", "total_label")
    ws.blank(r, 2, "total_label")
    ws.blank(r, 3, "total_label")
    for c, f in ((4, "total_int"), (5, "total_1dp"), (6, "total_money"), (7, "total_money"), (10, "total_money"),
                 (11, "total_money")):
        ws.formula(r, c, f"SUM({col(c)}{f1}:{col(c)}{l1})", f)
    ws.formula(r, 8, f"IF(H{tot}=0,0,G{tot}/H{tot}-1)", "total_change_pct")
    ws.formula(r, 9, f"SUM(J{f1}:J{l1})", "total_share")
    for c in (12, 13, 14):
        ws.blank(r, c, "total_label")
    if model.not_counted:
        ws.text(lay.nc_head, 1, "Not included above: handsets with no value in the day book (units)", "subhead_small")
        for i, it in enumerate(model.not_counted):
            r = lay.nc_first + i
            n = ex(r)
            item_ = f"d_Item,$M{n}"
            ws.text(r, 1, it.code, "td")
            ws.text(r, 2, it.product, "td")
            ws.text(r, 3, it.category, "td")
            ws.formula(r, 4, sumifs("d_Qty", "wk", item_), "int")
            ws.formula(r, 5, per_week("prev", sumifs("d_Qty", "prev", item_)), "money_1dp")
            ws.formula(r, 6, sumifs("d_Net", "wk", item_), "money")
            ws.formula(r, 7, per_week("prev", sumifs("d_Net", "prev", item_)), "money")
            for c in (8, 9, 10, 11):
                ws.blank(r, c, "td")
            ws.text(r, 12, it.item, "ref_td")
    for rule in style.change_rules():
        ws.cond(f"I{f1}:I{tot}", rule)
    ws.cond(f"J{f1}:J{l1}", dict(style.DATA_BAR))
    _page(ws, landscape=True, fit=(1, 0), freeze=(5, 0), autofilter=f"B5:O{l1}",
          print_area=f"A1:L{ex(lay.prod_end)}", repeat_rows=(3, 4))


# ── Customers ──────────────────────────────────────────────────────────────────────────────────

def _customers(ws: Sheet, model: mdl.Model, lay: Layout) -> None:
    ws.default_row = 18
    for c1, c2, w in ((0, 0, 2.28515625), (1, 1, 57.140625), (2, 2, 11.42578125), (3, 3, 21.42578125),
                      (4, 10, 12.5703125), (11, 11, 20), (12, 12, 10), (13, 13, 52.85546875)):
        ws.col(c1, c2, w)
    ws.row(4, 24)
    ws.text(0, 1, "Sales by customer", "sheet_title")
    ws.formula(1, 1, 'WkLong&" against the four weeks before, the same days last month and the month to date. Net '
                     'sales in BHD, excluding VAT. Accounts listed by the week\'s sales."', "sheet_sub")
    ws.merge(3, 4, 3, 8, "Net sales (BHD)", "group_head")
    ws.merge(3, 9, 3, 10, "Invoices", "group_head")
    ws.merge(3, 12, 3, 13, "Reference columns", "ref_group_head")
    for c, head in ((1, "Customer"), (2, "Type"), (3, "Salesman, latest invoice")):
        ws.text(4, c, head, "th")
    for c, head in ((4, "=WkLabel"), (5, "4-week average"), (6, "Change"), (7, "=LmLabel"), (8, "=MtdLabel"),
                    (9, "=WkLabel"), (10, "Four weeks before")):
        if head.startswith("="):
            ws.formula(4, c, head, "th_r")
        else:
            ws.text(4, c, head, "th_r")
    ws.text(4, 11, "Status", "th_indent3")
    ws.formula(4, 12, '"Rank, "&WkLabel', "ref_th_r")
    ws.text(4, 13, "Dashboard label", "ref_th")
    f1, l1, tot = ex(lay.cust_first), ex(lay.cust_last), ex(lay.cust_total)
    for i, name in enumerate(model.customers):
        r = lay.cust_first + i
        n = ex(r)
        cust_ = f"d_Cust,$B{n}"
        ws.text(r, 1, name, "td")
        ws.formula(r, 2, f'IF(B{n}=CashAcct,"Cash","Account")', "td")
        ws.formula(r, 3, f'IF(C{n}="Cash","Various",LOOKUP(2,1/(d_Cust=$B{n}),d_Rep))', "td")
        ws.formula(r, 4, sumifs("d_Net", "wk", cust_), "money")
        ws.formula(r, 5, per_week("prev", sumifs("d_Net", "prev", cust_)), "money")
        ws.formula(r, 6, f"E{n}-F{n}", "money_change")
        ws.formula(r, 7, sumifs("d_Net", "lm", cust_), "money")
        ws.formula(r, 8, sumifs("d_Net", "mtd", cust_), "money")
        ws.formula(r, 9, sumifs("d_InvFlag", "wk", cust_), "int")
        ws.formula(r, 10, sumifs("d_InvFlag", "prev", cust_), "int")
        ws.formula(r, 11, f'IF(C{n}="Cash","Cash",IF(AND(J{n}>0,K{n}>0),"Both",IF(J{n}>0,"This week only",IF(K{n}>0,'
                          f'"Not this week",IF(OR(H{n}>0,I{n}>0),"Other days only","No invoice counted")))))', "status")
        ws.formula(r, 12, f'IF(C{n}="Cash","",SUMPRODUCT(--($E${f1}:$E${l1}>E{n}),--($C${f1}:$C${l1}="Account"))'
                          f'+SUMPRODUCT(--($E${f1}:E{n}=E{n}),--($C${f1}:C{n}="Account")))', "rank")
        ws.text(r, 13, mdl.account_label(name), "ref_td")
    r = lay.cust_total
    ws.text(r, 1, "Total", "total_label")
    for c in (2, 3, 11, 12, 13):
        ws.blank(r, c, "total_label")
    for c, f in ((4, "total_money"), (5, "total_money"), (7, "total_money"), (8, "total_money"), (9, "total_int"),
                 (10, "total_int")):
        ws.formula(r, c, f"SUM({col(c)}{f1}:{col(c)}{l1})", f)
    ws.formula(r, 6, f"E{tot}-F{tot}", "total_money_change")
    ws.text(r + 1, 1, "Customer names are as recorded in the ERP. Status: Both = invoiced in the week and in the four "
                      "weeks before; Not this week = in the four weeks before only (filter on it to list them); Other "
                      "days only = invoiced on the same days last month alone; No invoice counted = giveaway or handset "
                      "lines only.", "note")
    for rule in style.change_rules():
        ws.cond(f"G{f1}:G{tot}", rule)
    _page(ws, landscape=True, fit=(1, 0), freeze=(5, 2), autofilter=f"B5:N{l1}", print_area=f"A1:L{ex(r + 1)}",
          repeat_rows=(3, 4))


# ── Daily ──────────────────────────────────────────────────────────────────────────────────────

def _daily(ws: Sheet) -> None:
    ws.default_row = 18
    for c1, c2, w in ((0, 0, 2.28515625), (1, 1, 14), (2, 2, 14), (3, 6, 17)):
        ws.col(c1, c2, w)
    ws.row(4, 24)
    ws.text(0, 1, "Daily net sales", "sheet_title")
    ws.formula(1, 1, '"Each day of "&WkLong&" beside the average of the same weekday in the four weeks before. Net '
                     'sales in BHD, excluding VAT, by invoice date."', "sheet_sub")
    ws.text(4, 1, "Day", "th")
    ws.text(4, 2, "Date", "th_r")
    ws.formula(4, 3, "WkLabel", "th_r")
    ws.text(4, 4, "Same weekday, 4-week average", "th_r")
    ws.text(4, 5, "Change %", "th_r")
    ws.text(4, 6, "Invoices", "th_r")
    for k, name in enumerate(DAY_NAMES):
        r = 5 + k
        n = ex(r)
        ws.text(r, 1, name, "td")
        ws.formula(r, 2, f"WkStart+{k}" if k else "WkStart", "calc_date")
        ws.formula(r, 3, f"SUMIFS(d_Net,d_Date,$C{n})", "money")
        before = "+".join(f"SUMIFS(d_Net,d_Date,$C{n}-{7 * j})" for j in range(1, AVERAGE_OF + 1))
        ws.formula(r, 4, f"({before})/{AVERAGE_OF}", "money")
        ws.formula(r, 5, f'IF(E{n}=0,IF(D{n}=0,0,"n/a"),D{n}/E{n}-1)', "change_pct")
        ws.formula(r, 6, f"SUMIFS(d_InvFlag,d_Date,$C{n})", "int")
    r = 12
    ws.text(r, 1, "Total", "total_label")
    ws.blank(r, 2, "total_label")
    ws.formula(r, 3, "SUM(D6:D12)", "total_money")
    ws.formula(r, 4, "SUM(E6:E12)", "total_money")
    ws.formula(r, 5, "IF(E13=0,0,D13/E13-1)", "total_change_pct")
    ws.formula(r, 6, "SUM(G6:G12)", "total_int")
    ws.merge(r + 1, 1, r + 1, 6, "A weekday's average is that weekday in each of the four weeks before, added up and "
                                 "divided by four.", "note")
    for rule in style.change_rules():
        ws.cond("F6:F13", rule)
    ws.cond("D6:D12", dict(style.DATA_BAR))
    _page(ws, landscape=False, fit=(1, 1), freeze=(5, 0))


# ── Stock and Movements (the shared sheets) ─────────────────────────────────────────────────────

def _extra(ws: Sheet, part: Part | None) -> None:
    """The shared sheet week.py built and checked (Part.data['sheet']), or one line saying why there is none."""
    src = part.data.get("sheet") if part is not None and part.ok else None
    if isinstance(src, Sheet):
        for f in fields(Sheet):
            if f.name not in ("name", "tab_color"):
                setattr(ws, f.name, getattr(src, f.name))
        if not ws.page.get("footer"):
            ws.page.update({"margins": dict(MARGINS), "footer": footer(ws.name), "footer_margin": 0.25})
        return
    ws.col(0, 0, 2.28515625)
    ws.col(1, 1, 100)
    ws.text(0, 1, NOT_WORKED.format(why=part.why if part is not None and part.why else "not asked for"), "sheet_sub")
    _page(ws, landscape=False, fit=(1, 1))


# ── Actions ────────────────────────────────────────────────────────────────────────────────────

def _act_fast(ws: Sheet, r: int, data: dict, refs: dict) -> int:
    ws.merge(r, 1, r, 5, f"Main store at the end of Saturday {long_day(data['as_of'])}. Fast movers are the "
                         f"{data['n']} best sellers by units, {month_year(data.get('rank_first'))} to "
                         f"{month_year(data.get('rank_last'))}.", "note")
    ws.text(r + 1, 1, "Main store", "th")
    ws.text(r + 1, 2, "Items", "th_r")
    ws.text(r + 2, 1, "Fast movers", "td")
    ws.put(r + 2, 2, int(data["n"]), "int")
    ws.text(r + 3, 1, "Out of stock at the end of the Saturday", "td")
    ws.put(r + 3, 2, int(data["out"]), "int")
    ws.text(r + 4, 1, "In stock", "total_label")
    ws.formula(r + 4, 2, f"C{ex(r + 2)}-C{ex(r + 3)}", "total_int")
    refs["fast_n"], refs["fast_out"] = f"Actions!C{ex(r + 2)}", f"Actions!C{ex(r + 3)}"
    return r + 5


def _supplier_table(ws: Sheet, r: int, heads: tuple, rows: list, empty: str) -> tuple[int, int, int]:
    """A Supplier | count | units table with its total; (first row, total row, next free row), 0-based."""
    ws.text(r, 1, heads[0], "th")
    ws.text(r, 2, heads[1], "th_r")
    ws.text(r, 3, heads[2], "th_r")
    body = rows or [(empty, 0, 0)]
    first = r + 1
    for k, (name, n, units) in enumerate(body):
        ws.text(first + k, 1, str(name), "td")
        ws.put(first + k, 2, n, "int")
        ws.put(first + k, 3, units, "int")
    last = first + len(body) - 1
    tot = last + 1
    ws.text(tot, 1, "Total", "total_label")
    ws.formula(tot, 2, f"SUM(C{ex(first)}:C{ex(last)})", "total_int")
    ws.formula(tot, 3, f"SUM(D{ex(first)}:D{ex(last)})", "total_int")
    return first, tot, tot + 1


def _act_order(ws: Sheet, r: int, data: dict, refs: dict) -> int:
    ws.merge(r, 1, r, 5, f"As on {long_day(data['as_of'])}: the /yq-stock order list, each item under its supplier. "
                         "No purchase order is drafted.", "note")
    first, tot, nxt = _supplier_table(ws, r + 1, ("Supplier", "Items", "Units"), list(data.get("suppliers") or []),
                                      "(no item to order)")
    refs["order_items"], refs["order_units"] = f"Actions!C{ex(tot)}", f"Actions!D{ex(tot)}"
    refs["order_range"] = f"Actions!C{ex(first)}:C{ex(tot - 1)}"
    return nxt


def _act_late(ws: Sheet, r: int, data: dict, refs: dict) -> int:
    said = (f"As on {long_day(data['as_of'])}: logged goods on order past their expected day, with goods still due."
            if data.get("logged") else
            f"As on {long_day(data['as_of'])}: no goods on order are logged in the KPI log, so none can be late.")
    ws.merge(r, 1, r, 5, said, "note")
    _first, _tot, nxt = _supplier_table(ws, r + 1, ("Supplier", "Lines late", "Units due"),
                                        list(data.get("suppliers") or []), "(none late)")
    return nxt


def _two_counts(ws: Sheet, r: int, note: str, rows: tuple) -> int:
    ws.merge(r, 1, r, 5, note, "note")
    ws.text(r + 1, 1, "Supplier receipts", "th")
    ws.text(r + 1, 2, "Count", "th_r")
    for k, (label, n) in enumerate(rows):
        ws.text(r + 2 + k, 1, label, "td")
        ws.put(r + 2 + k, 2, int(n), "int")
    return r + 2 + len(rows)


def _act_papers(ws: Sheet, r: int, data: dict, refs: dict) -> int:
    nxt = _two_counts(ws, r, f"Supplier receipts into the main store, {span_text(data['first'], data['last'])}. A paper "
                             "is the purchase order, the MRN XML or the supplier invoice.",
                      (("Supplier receipts", data["receipts"]),
                       ("Missing the purchase order, the MRN XML or the supplier invoice", data["missing"])))
    refs["papers_missing"] = f"Actions!C{ex(nxt - 1)}"
    return nxt


def _act_transfers(ws: Sheet, r: int, data: dict, refs: dict) -> int:
    ws.merge(r, 1, r, 5, f"Transfers of {span_text(data['first'], data['last'])}, as /yq-moves finds them: counts only.",
             "note")
    ws.text(r + 1, 1, "To check", "th")
    ws.text(r + 1, 2, "Count", "th_r")
    body = list(data.get("kinds") or []) or [("(none to check)", 0)]
    first = r + 2
    for k, (kind, n) in enumerate(body):
        ws.text(first + k, 1, str(kind), "td")
        ws.put(first + k, 2, int(n), "int")
    tot = first + len(body)
    ws.text(tot, 1, "Total", "total_label")
    ws.formula(tot, 2, f"SUM(C{ex(first)}:C{ex(tot - 1)})", "total_int")
    return tot + 1


def _act_counts(ws: Sheet, r: int, data: dict, refs: dict) -> int:
    ws.merge(r, 1, r, 5, "The count sheets come from /yq-month prep (prep full for the quarter-end count); the counts "
                         "are typed into them and read back with /yq-month count.", "note")
    ws.text(r + 1, 1, "Count", "th")
    ws.text(r + 1, 2, "Sheets by", "th_r")
    ws.text(r + 1, 3, "Counted by", "th_r")
    for k, (label, sheets_by, counted_by) in enumerate(data["rows"]):
        ws.text(r + 2 + k, 1, label, "td")
        ws.put(r + 2 + k, 2, sheets_by, "calc_date")
        ws.put(r + 2 + k, 3, counted_by, "calc_date")
    return r + 2 + len(data["rows"])


def _act_checks(ws: Sheet, r: int, data: dict, refs: dict) -> int:
    return _two_counts(ws, r, f"Supplier receipts into the main store, {span_text(data['first'], data['last'])}, "
                              "against the incoming checks logged in the KPI log.",
                       (("Supplier receipts", data["receipts"]), ("No incoming check logged", data["unchecked"])))


ACTION_BLOCKS = (("fast", "Fast movers out of stock", _act_fast),
                 ("order", "Order list by supplier", _act_order),
                 ("late", "Goods on the way, late", _act_late),
                 ("papers", "Receipts missing papers", _act_papers),
                 ("transfers", "Transfers to check", _act_transfers),
                 ("counts", "Counts due", _act_counts),
                 ("checks", "Receipts with no incoming check logged", _act_checks))


def _actions(ws: Sheet, facts: WeekFacts) -> dict:
    """Returns the cells a comment line reads (a blank cell for a part that was not worked out)."""
    ws.default_row = 18
    for c1, c2, w in ((0, 0, 2.28515625), (1, 1, 62), (2, 3, 15.7109375), (4, 5, 30)):
        ws.col(c1, c2, w)
    ws.text(0, 1, "Actions", "sheet_title")
    ws.formula(1, 1, '"Stock and paperwork to act on after "&WkLong&". Counts and totals only: no item names or '
                     'voucher numbers."', "sheet_sub")
    refs = {"fast_n": PLACE, "fast_out": PLACE, "order_items": PLACE, "order_units": PLACE,
            "order_range": PLACE_RANGE, "papers_missing": PLACE, "papers_days": PAPERS_DAYS}
    r = 3
    for key, title, writer in ACTION_BLOCKS:
        part = facts.parts.get(key) or Part(False, "not asked for")
        ws.text(r, 1, title, "subhead")
        if not part.ok:
            ws.merge(r + 1, 1, r + 1, 5, NOT_WORKED.format(why=part.why or "not asked for"), "note")
            r += 3
            continue
        r = writer(ws, r + 1, part.data, refs) + 1
    _page(ws, landscape=False, fit=(1, 0))
    return refs


# ── Data ───────────────────────────────────────────────────────────────────────────────────────

DATA_HEADERS = (("Date", "th"), ("Invoice", "th"), ("Customer", "th"), ("Item", "th"), ("Qty", "th_r"),
                ("Rate", "th_r"), ("Gross", "th_r"), ("Net", "th_r"), ("Warehouse", "data_th_wh"),
                ("Narration", "th"), ("Week ending", "data_th_calc"), ("Month", "data_th_calc"),
                ("Salesman / outlet", "data_th_calc"), ("Category", "data_th_calc"), ("VAT", "data_th_calc_r"),
                ("Line type", "data_th_calc_indent"), ("Units", "data_th_calc_r"), ("Invoice flag", "data_th_calc_r"),
                ("Week account flag", "data_th_calc_r"), ("Month account flag", "data_th_calc_r"),
                ("Same days last month account flag", "data_th_calc_r"), ("Giveaway (v_sales)", "th_r"))


def _data(ws: Sheet, model: mdl.Model, lay: Layout, giveaways: frozenset = frozenset()) -> None:
    ws.default_row = 16
    for c1, c2, w in ((0, 0, 11.7109375), (1, 1, 17.7109375), (2, 2, 44.7109375), (3, 3, 78.7109375),
                      (4, 4, 7.7109375), (5, 5, 8.7109375), (6, 7, 10.7109375), (8, 8, 29.7109375),
                      (9, 9, 30.7109375), (10, 10, 11.7109375), (11, 11, 10.7109375), (12, 12, 20.7109375),
                      (13, 13, 23.7109375), (14, 14, 9.7109375), (15, 15, 13.7109375), (16, 16, 8.7109375),
                      (17, 19, 9.7109375), (20, 21, 13.7109375)):
        ws.col(c1, c2, w)
    ws.row(3, 33.75)
    p = model.period
    ws.text(0, 0, "Sales day book lines", "sheet_title")
    ws.text(1, 0, f"ERP export for {COMPANY}, {span_text(p.data_first, p.end)}. Columns A to J are the export, tidied: "
                  f"invoice prefix removed, {mdl.CASH_ACCOUNT} spelt one way, the three empty columns dropped and "
                  f"customer ID numbers left out of the narration. Columns K to U are calculated; K is the Saturday "
                  f"that ends the line's week (7 x ROUNDUP(date / 7, 0)). Column V is v_sales' giveaway flag (1: the "
                  f"Giveaway division or a zero-priced line): such a line counts in no units, invoices or accounts.",
            "sheet_sub")
    for c, (head, f) in enumerate(DATA_HEADERS):
        ws.text(3, c, head, f)
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
        ws.formula(r, 10, f"7*ROUNDUP(A{n}/7,0)", "data_date")
        ws.formula(r, 11, f"DATE(YEAR(A{n}),MONTH(A{n}),1)", "data_month")
        ws.formula(r, 12, f'SUBSTITUTE(I{n},WhSuffix,"")', "data_text")
        ws.formula(r, 13, f'IFERROR(INDEX(p_Cat,MATCH(D{n},p_Item,0)),"Unmapped")', "data_text")
        ws.formula(r, 14, f"G{n}-H{n}", "data_money")
        ws.formula(r, 15, f'IF(N{n}=HandsetCat,"Handset",IF(V{n}=1,"Giveaway","Accessory"))', "data_indent")
        ws.formula(r, 16, f'IF(P{n}="Accessory",E{n},0)', "data_int")
        ws.formula(r, 17, f'IF(P{n}="Accessory",IF(COUNTIFS($B$5:B{n},B{n},$P$5:P{n},"Accessory")=1,1,0),0)', "data_int")
        acct = f'OR(C{n}=CashAcct,P{n}<>"Accessory")'
        ws.formula(r, 18, f'IF({acct},0,IF(COUNTIFS($C$5:C{n},C{n},$K$5:K{n},K{n},$P$5:P{n},"Accessory")=1,1,0))',
                   "data_int")
        ws.formula(r, 19, f'IF({acct},0,IF(COUNTIFS($C$5:C{n},C{n},$L$5:L{n},L{n},$P$5:P{n},"Accessory")=1,1,0))',
                   "data_int")
        ws.formula(r, 20, f'IF(OR(C{n}=CashAcct,P{n}<>"Accessory",A{n}<LmStart,A{n}>LmEnd),0,IF(COUNTIFS($C$5:C{n},'
                          f'C{n},$A$5:A{n},">="&LmStart,$A$5:A{n},"<="&LmEnd,$P$5:P{n},"Accessory")=1,1,0))', "data_int")
        ws.put(r, 21, 1 if line_key(ln) in giveaways else 0, "data_int")
    _page(ws, landscape=True, fit=(1, 0), freeze=(4, 2), autofilter=f"A4:V{ex(lay.data_last)}", repeat_rows=(3,))


# ── Notes ──────────────────────────────────────────────────────────────────────────────────────

LABELS = (
    ("WkLabel", "The week, short", '"w/e "&DAY(WkEnd)&" "&' + mon3("WkEnd") + '&" "&YEAR(WkEnd)'),
    ("WkLong", "The week, in full", '"the week ending "&DAY(WkEnd)&" "&' + monfull("WkEnd") + '&" "&YEAR(WkEnd)'),
    ("WkSpan", "The week, its days", 'DAY(WkStart)&" "&' + mon3("WkStart") + '&" to "&DAY(WkEnd)&" "&' + mon3("WkEnd")
     + '&" "&YEAR(WkEnd)'),
    ("LmLabel", "The same days last month", 'DAY(LmStart)&" "&' + mon3("LmStart") + '&" to "&DAY(LmEnd)&" "&'
     + mon3("LmEnd")),
    ("MtdLabel", "The month to date", '"1 to "&DAY(WkEnd)&" "&' + mon3("WkEnd")),
    ("MonthLong", "The month, in full", monfull("MtdStart") + '&" "&YEAR(MtdStart)'),
    ("PreparedText", "Date prepared", 'DAY(PreparedOn)&" "&' + monfull("PreparedOn") + '&" "&YEAR(PreparedOn)'),
)


def _settings(p: mdl.Period, facts: WeekFacts) -> tuple:
    return (
        ("WkStart", "Week starting (Sunday)", p.start, "input_date",
         "The week reported, Sunday to Saturday. Every window below is worked out from this day."),
        ("WkEnd", "Week ending (Saturday)", "=WkStart+6", "calc_date", None),
        ("PrevStart", "Four weeks before: first day", "=WkStart-28", "calc_date",
         "The four weeks before the week. The tiles show their total divided by four."),
        ("PrevEnd", "Four weeks before: last day", "=WkStart-1", "calc_date", None),
        ("LmStart", "Same days last month: first day", "=EDATE(WkStart,-1)", "calc_date",
         "The same seven days one month back."),
        ("LmEnd", "Same days last month: last day", "=LmStart+6", "calc_date", None),
        ("MtdStart", "Month to date: first day", "=DATE(YEAR(WkEnd),MONTH(WkEnd),1)", "calc_date",
         "The first day of the Saturday's month; the month to date runs to the Saturday."),
        ("MonthEnd", "Month end", "=EDATE(MtdStart,1)-1", "calc_date", None),
        ("DataFirst", "First day on the Data sheet", "=MIN(PrevStart,LmStart,MtdStart)", "calc_date",
         "The earliest day any window reaches."),
        ("KpiStep", "KPI step for the month (BHD)", facts.kpi_step or 0, "input_money",
         "The step in force for the Saturday's month (config sales.kpi_steps); nought when none is set."),
        ("BizDaysLeft", "Business days left after the week", "=" + BIZ_DAYS_LEFT, "int_plain",
         "Sunday to Thursday days from the day after the Saturday to the month end."),
        ("CashAcct", "Cash sales account", mdl.CASH_ACCOUNT, "input_text", "ERP account used for unnamed cash sales."),
        ("HandsetCat", "Handset category", cats.HANDSET, "input_text",
         "Items in this category are left out of accessory units and invoice counts."),
        ("WhSuffix", "Warehouse suffix", mdl.WH_SUFFIX, "input_text",
         "Removed from the ERP warehouse name to give the salesman name."),
        ("PreparedBy", "Prepared by", facts.prepared_by or "", "input_text", None),
        ("PreparedOn", "Prepared on", facts.prepared_on, "input_date", None),
    )


def basis_paragraphs(model: mdl.Model, facts: WeekFacts) -> list[str]:
    """The Notes sheet's basis of preparation, from the data and the rules in force (numbered by the sheet)."""
    p = model.period
    invoices = len({ln.invoice for ln in model.lines})
    vat = "VAT is taken as Gross less Taxable."
    if {"Discount", "VAT Amount"} <= set(model.empty_columns):
        vat = "The Discount and VAT Amount columns of the export were empty, so " + vat
    out = [f"Source is the ERP sales day book for {COMPANY}, {span_text(p.data_first, p.end)} ({len(model.lines):,} "
           f"lines, {invoices:,} invoices). The lines are on the Data sheet.",
           f"The week is Sunday {long_day(p.start)} to Saturday {long_day(p.end)}. It is set beside the four weeks "
           f"before it ({span_text(p.prev_start, p.prev_end)}), as their total divided by four; the same seven days "
           f"one month back ({span_text(p.lm_start, p.lm_end)}); and the month to date ({span_text(p.mtd_start, p.end)}).",
           f"Net sales is the Taxable column, which excludes 10% VAT. Gross is the invoiced value including VAT. {vat}",
           "The day book covers sales invoices only, so any sales returns are not deducted."]
    outlets = [r for r in model.reps if r in facts.outlets]
    out.append(f"Salesman or outlet is the warehouse the invoice was issued from, with \"{mdl.WH_SUFFIX}\" removed."
               + ((f" {_join(outlets)} " + ("is an outlet rather than a salesman." if len(outlets) == 1
                                            else "are outlets rather than salesmen.")) if outlets else ""))
    out.append(cats.rule_paragraph(model.category_names))
    if model.not_counted:
        items = {it.item for it in model.not_counted}
        lines_n = sum(1 for ln in model.lines if ln.item in items)
        out.append(f"{_join([it.code for it in model.not_counted])} handsets issued through "
                   f"{sc.issuers_of(model, items, facts.outlets)} carry no value in the day book ({lines_n:,} lines). "
                   "They are not accessories, so they are left out of units, invoice counts and product counts.")
    given = [ln for ln in model.lines if line_key(ln) in facts.giveaways
             and model.items[ln.item].category != cats.HANDSET]          # the handsets have their own paragraph
    if given:
        n = len(given)
        out.append(f"{sc.words(n, capital=True)} line{'s' if n != 1 else ''} v_sales marks as giveaways (the Giveaway "
                   f"division, or a zero-priced line) {'are' if n != 1 else 'is'} on the Data sheet, line type Giveaway: "
                   f"{'they count' if n != 1 else 'it counts'} in no units, invoices or accounts, as /yq-brief leaves "
                   f"{'them' if n != 1 else 'it'} out.")
    spellings = {ln.raw_customer for ln in model.lines if mdl.CASH_SPELLING.match(ln.raw_customer)}
    out.append(f"An account is any customer other than {mdl.CASH_ACCOUNT}, which covers unnamed cash sales."
               + (f" The {sc.words(len(spellings))} spellings of {mdl.CASH_ACCOUNT} in the ERP are treated as one."
                  if len(spellings) > 1 else ""))
    out.append("Accounts invoiced: in a week each account with an accessory invoice counts once, and the four-week "
               "average adds the four weekly counts and divides by four; the same days last month and the month to "
               "date count each account once.")
    month = f"{mdl.MONTHS[p.end.month - 1]} {p.end.year}"
    out.append(f"The month to date is set against the KPI step in force for {month} (config sales.kpi_steps). The BHD a "
               "business day still needed is what is left of the step divided by the business days, Sunday to "
               "Thursday, from the day after the week to the month end." if facts.kpi_step else
               f"No KPI step is set for {month} (config sales.kpi_steps): the month to date is shown on its own.")
    out.append("The Stock, Movements and Actions sheets carry totals and counts only: no item names and no voucher "
               "numbers. A part that could not be worked out reads one line saying so (see Parts of this file).")
    masked = [ln for ln in model.lines if ln.narration_masked]
    if masked:
        out.append(f"{sc.words(len(masked), capital=True)} narration{'s hold' if len(masked) != 1 else ' holds'} "
                   "customer ID numbers. These are not carried into the Data sheet.")
    return out


def _notes(ws: Sheet, book: Book, model: mdl.Model, lay: Layout, facts: WeekFacts) -> dict:
    """The settings (each a named cell), the labels, the basis, the parts and the control totals. Returns the
    cells that must read nil, by check (what week.py reads back after the evaluation)."""
    p = model.period
    ws.default_row = 18
    ws.col(0, 0, 2.28515625)
    ws.col(1, 1, 46)
    ws.col(2, 6, 15.7109375)
    ws.col(7, 7, 61.42578125)
    ws.text(0, 1, "Notes and settings", "sheet_title")
    ws.text(1, 1, "Settings, basis of preparation and control totals. Cells in blue are typed in.", "sheet_sub")
    ws.text(3, 1, "Report settings", "subhead")
    r = 4
    for name, label, value, f, help_text in _settings(p, facts):
        ws.text(r, 1, label, "td")
        if isinstance(value, str) and value.startswith("="):
            ws.formula(r, 2, value, f)
        elif isinstance(value, str):
            ws.text(r, 2, value, f)
        else:
            ws.put(r, 2, value, f)
        ws.merge(r, 3, r, 7, help_text, "help")
        book.define(name, f"Notes!{aa(r, 2)}")
        r += 1
    r += 1
    ws.text(r, 1, "Labels used in the headings", "subhead")
    r += 1
    for name, label, formula in LABELS:
        ws.text(r, 1, label, "td")
        ws.merge(r, 2, r, 7, None, "td", formula=formula)
        book.define(name, f"Notes!{aa(r, 2)}")
        r += 1

    r += 1
    defs_head = r
    for head, texts in (("Basis of preparation", basis_paragraphs(model, facts)),
                        ("About this workbook", (
                            "Figures on the sales sheets are worked out from the Data sheet with formulas. The Stock, "
                            "Movements and Actions sheets hold figures from the stock ledger and the logs, typed in.",
                            "The salesman, product and customer lists are fixed for this report. Products and Customers "
                            "can be sorted or filtered; the Dashboard picks its top ten by rank, so it is not affected.",
                            "The three lines at the top of the Dashboard are formulas over its figures, the KPI step and "
                            "the Actions sheet: they follow the figures.",
                            f"This file covers {span_text(p.data_first, p.end)} only. Next week's report will be a new "
                            "file."))):
        ws.text(r, 1, head, "subhead")
        r += 1
        for k, text in enumerate(texts):
            ws.merge(r, 1, r, 7, f"{k + 1}.  {text}", "para")
            ws.row(r, _para_height(text))
            r += 1
        r += 1
    ws.text(r, 1, "Definitions", "subhead")
    r += 1
    for term, meaning in (("Net sales", "Taxable value of sales invoice lines in BHD, excluding VAT."),
                          ("Units", "Quantity of accessory items invoiced, giveaway lines left out."),
                          ("Invoices", "Sales invoices with at least one accessory line that is not a giveaway."),
                          ("Average invoice", "Net sales divided by invoices."),
                          ("Average unit price", "Net sales divided by units."),
                          ("Accounts invoiced", "Accounts with an accessory invoice in the window; for the four-week "
                                                "average, the four weekly counts divided by four."),
                          ("Salesmen/outlets active", "Salesmen and outlets with net sales above nought in the window."),
                          ("Four-week average", "The four weeks before the week, added up and divided by four."),
                          ("Same days last month", "The same seven days one month back."),
                          ("Month to date", "The first of the Saturday's month to the Saturday."),
                          ("Change %", "The week against the window beside it; n/a where that window had no sales.")):
        ws.text(r, 1, term, "td")
        ws.merge(r, 2, r, 7, meaning, "td")
        r += 1

    # the parts beside the sales: worked out or not, and why not
    r += 1
    ws.text(r, 1, "Parts of this file", "subhead")
    r += 1
    ws.text(r, 1, "Part", "th")
    ws.text(r, 2, "Worked out (1 = yes)", "th_r")
    ws.merge(r, 3, r, 7, "When not, why", "th")
    r += 1
    for key, label in PARTS:
        part = facts.parts.get(key) or Part(False, "not asked for")
        ws.text(r, 1, label, "td")
        ws.put(r, 2, 1 if part.ok else 0, "int_plain")
        ws.merge(r, 3, r, 7, "" if part.ok else NOT_WORKED.format(why=part.why or "not asked for"), "help")
        if key in PART_NAMES:
            book.define(PART_NAMES[key], f"Notes!{aa(r, 2)}")
        r += 1

    r += 1
    found = _controls(ws, r, lay, facts, model)
    _page(ws, landscape=True, scale=72, breaks=[defs_head])
    return found


def _controls(ws: Sheet, top: int, lay: Layout, facts: WeekFacts, model: mdl.Model) -> dict:
    """The Data sheet against v_sales in each of the four windows; each sheet's total less the Data sheet in each
    window; the lines with no category; the narrations with an ID number. Every check must read nil."""
    found: dict = {"db": [], "sheets": [], "unmapped": [], "narration": None}
    r = top
    ws.text(r, 1, "Control totals", "subhead")
    r += 1
    ws.text(r, 1, "Data sheet against v_sales", "th")
    ws.text(r, 2, "Data sheet", "th_r")
    ws.text(r, 3, "v_sales", "th_r")
    ws.text(r, 4, "Difference", "th_r")
    for c in (5, 6):
        ws.blank(r, c, "th_r")
    ws.blank(r, 7, "th")
    r += 1
    rows = (("day book lines", "COUNTIFS({c})", "int", "input_int", "diff_int", "lines"),
            ("quantity, all lines", "SUMIFS(d_Qty,{c})", "int", "input_int", "diff_int", "qty"),
            ("gross, including VAT (BHD)", "SUMIFS(d_Gross,{c})", "money", "input_money", "diff_money", "gross"),
            ("net sales, excluding VAT (BHD)", "SUMIFS(d_Net,{c})", "money", "input_money", "diff_money", "net"))
    first_help = True
    for w in WINDOWS:
        t = facts.totals.get(w) or {}
        for label, f, fmt_, in_fmt, diff_fmt, key in rows:
            n = ex(r)
            words = f"{WINDOW_NAMES[w]}: {label}"
            ws.text(r, 1, words, "td")
            ws.formula(r, 2, f.format(c=crit(w)), fmt_)
            ws.put(r, 3, float(t.get(key) or 0), in_fmt)
            ws.formula(r, 4, f"ROUND(C{n}-D{n},2)", diff_fmt)
            ws.blank(r, 5, "td")
            ws.blank(r, 6, "td")
            if first_help:
                ws.text(r, 7, "v_sales, read for each window on its own: accessories, division not SIM or Devices, every "
                              "line.", "help")
                first_help = False
            else:
                ws.blank(r, 7, "help")
            found["db"].append((words, f"E{n}"))
            r += 1

    r += 1
    ws.text(r, 1, "Sheet totals less Data (net sales)", "th")
    for c, w in zip((2, 3, 4, 5), WINDOWS):
        ws.text(r, c, {"wk": "The week", "prev": "Four weeks before", "lm": "Same days last month",
                       "mtd": "Month to date"}[w], "th_r")
    ws.blank(r, 6, "th_r")
    ws.blank(r, 7, "th")
    r += 1
    s, c_, p_, u_ = ex(lay.sal_total), ex(lay.cat_total), ex(lay.prod_total), ex(lay.cust_total)
    net = ex(Dash.WIN)
    times = f"*{AVERAGE_OF}"
    checks = (("Salesmen", {"wk": f"Salesmen!C{s}", "prev": f"Salesmen!D{s}{times}", "lm": f"Salesmen!F{s}",
                            "mtd": f"Salesmen!H{s}"}),
              ("Salesmen, weekly columns", {"wk": f"Salesmen!Q{s}", "prev": f"SUM(Salesmen!M{s}:P{s})"}),
              ("Categories", {"wk": f"Categories!F{c_}", "prev": f"Categories!G{c_}{times}", "lm": f"Categories!J{c_}",
                              "mtd": f"Categories!K{c_}"}),
              ("Products", {"wk": f"Products!G{p_}", "prev": f"Products!H{p_}{times}", "lm": f"Products!K{p_}",
                            "mtd": f"Products!L{p_}"}),
              ("Customers", {"wk": f"Customers!E{u_}", "prev": f"Customers!F{u_}{times}", "lm": f"Customers!H{u_}",
                             "mtd": f"Customers!I{u_}"}),
              ("Daily", {"wk": "Daily!D13", "prev": f"Daily!E13{times}"}),
              ("Dashboard", {"wk": f"Dashboard!D{net}", "prev": f"Dashboard!E{net}{times}", "lm": f"Dashboard!G{net}",
                             "mtd": f"Dashboard!I{net}"}))
    for k, (label, refs) in enumerate(checks):
        ws.text(r, 1, label, "td")
        cells = []
        for c, w in zip((2, 3, 4, 5), WINDOWS):
            if w in refs:
                ws.formula(r, c, f"ROUND({refs[w]}-SUMIFS(d_Net,{crit(w)}),2)", "diff_money")
                cells.append(f"{col(c)}{ex(r)}")
            else:
                ws.blank(r, c, "td")
        ws.blank(r, 6, "td")
        if k == 0:
            ws.text(r, 7, "All differences should be nil.", "help")
        else:
            ws.blank(r, 7, "help")
        found["sheets"].append((label, cells))
        r += 1

    r += 1
    ws.text(r, 1, "Day book lines with no category", "td")
    ws.formula(r, 2, 'COUNTIFS(d_Cat,"Unmapped")', "int_plain")
    ws.text(r, 7, "A count here would mean an item is missing from the Products sheet.", "help")
    found["unmapped"].append(f"C{ex(r)}")
    r += 1
    ws.text(r, 1, "Narrations left out (they hold an ID number)", "td")
    ws.put(r, 2, sum(1 for ln in model.lines if ln.narration_masked), "input_int")
    ws.text(r, 7, "The narration is blank on the Data sheet for these lines.", "help")
    r += 1
    narr = f"Data!{aa(lay.data_first, 9)}:{aa(lay.data_last, 9)}"
    ws.text(r, 1, "Narrations on the Data sheet with an ID number", "td")
    ws.formula(r, 2, f'COUNTIF({narr},"*~*~*~*~*~*~*?")', "int_plain")
    ws.text(r, 7, "Should be 0: a masked ID number is six or more stars before its last three digits.", "help")
    found["narration"] = f"C{ex(r)}"
    return found
