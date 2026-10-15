"""The period model and the report's data: the day-book lines of two periods, tidied, with their lists.

PERIOD. A month report covers the report month and the month before it (the comparison month). The
weekly report (a later phase) is the same model with a week, its own key column and labels; only the
month is built now (Period.week raises until then).

DATA RULES (the plan's, the Sep 2026 file's):
  * the lines are v_sales, division not SIM and not Devices; zero-value lines stay (they are the
    "not counted" lines: the handset row of categories.py);
  * ordered by sale date, then the number at the end of the invoice as an integer, then the line
    number (the invoice text breaks a tie between two series);
  * invoice without the "SI : " prefix; customer as in the day book, any spelling of cash customer
    written as CASH_ACCOUNT; rate, gross and net 0 when empty; narration blank when it holds a masked
    ID number (v_sales masks them as ******123);
  * salesman / outlet = the warehouse with WH_SUFFIX removed (the sheet does it with SUBSTITUTE).

The lists that set the size of each sheet (salesmen, categories, products, customers) and their order
are worked out here; every figure on a sheet is a formula over the Data sheet (book.py evaluates them).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from scripts.ops.workbook import categories as cats

CASH_ACCOUNT = "Cash Customer"
WH_SUFFIX = " - Acc WH"
INVOICE_PREFIX = re.compile(r"^\s*SI\s*:\s*", re.I)
CASH_SPELLING = re.compile(r"^\s*cash\s*customer\s*$", re.I)
MASKED_ID = re.compile(r"\*{6,}\d{3}")               # what the v_sales mask leaves of an ID number
TRAILING_NUMBER = re.compile(r"(\d+)\s*$")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


def month_bounds(ym: str) -> tuple[date, date]:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", str(ym).strip())
    if not m or not 1 <= int(m.group(2)) <= 12 or int(m.group(1)) < 2000:
        raise ValueError(f"a month is written YYYY-MM (for example 2026-09), not {ym!r}")
    y, mo = int(m.group(1)), int(m.group(2))
    first = date(y, mo, 1)
    nxt = date(y + (mo == 12), mo % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


@dataclass(frozen=True)
class Period:
    kind: str                # 'month' ('week' arrives with /yq-week)
    start: date
    end: date
    prev_start: date
    prev_end: date

    @staticmethod
    def month(ym: str) -> "Period":
        start, end = month_bounds(ym)
        prev_end = start - timedelta(days=1)
        return Period("month", start, end, prev_end.replace(day=1), prev_end)

    @staticmethod
    def week(_week_end: date) -> "Period":
        raise NotImplementedError("the weekly workbook (/yq-week) arrives in a later phase")

    @property
    def key(self) -> str:
        return f"{self.start:%Y-%m}"

    @property
    def short(self) -> str:
        return MONTHS[self.start.month - 1][:3]

    @property
    def prev_short(self) -> str:
        return MONTHS[self.prev_start.month - 1][:3]

    @property
    def label(self) -> str:
        return f"{self.short} {self.start.year}"

    @property
    def prev_label(self) -> str:
        return f"{self.prev_short} {self.prev_start.year}"

    @property
    def long(self) -> str:
        return f"{MONTHS[self.start.month - 1]} {self.start.year}"

    @property
    def prev_long(self) -> str:
        return f"{MONTHS[self.prev_start.month - 1]} {self.prev_start.year}"

    def span_text(self) -> str:
        """'1 August to 30 September 2026' (the years both written when they differ)."""
        a, b = self.prev_start, self.end
        first = f"{a.day} {MONTHS[a.month - 1]}" + (f" {a.year}" if a.year != b.year else "")
        return f"{first} to {b.day} {MONTHS[b.month - 1]} {b.year}"

    def in_prev(self, d: date) -> bool:
        return self.prev_start <= d <= self.prev_end

    def in_cur(self, d: date) -> bool:
        return self.start <= d <= self.end


@dataclass
class Line:
    sale_date: date
    invoice: str
    line_no: int
    customer: str
    item: str
    qty: float
    rate: float
    gross: float
    net: float
    warehouse: str
    narration: str
    card_category: str | None = None          # the catalog card's category (v_sales.category_name)
    raw_customer: str = ""
    narration_masked: bool = False
    narration_seen: str = ""                  # the narration as v_sales gives it (IDs masked): read, never written

    @property
    def month(self) -> date:
        return self.sale_date.replace(day=1)

    @property
    def rep(self) -> str:
        return self.warehouse.replace(WH_SUFFIX, "")

    @property
    def code(self) -> str:
        return cats.item_code(self.item)


def _num(x) -> float:
    if x is None or x == "":
        return 0
    v = float(x)
    return int(v) if v.is_integer() else v


def _invoice_key(invoice: str) -> int:
    m = TRAILING_NUMBER.search(invoice)
    return int(m.group(1)) if m else 0


def tidy(rows: list[dict]) -> list[Line]:
    """v_sales rows (dicts) -> the Data sheet's lines, in its order."""
    lines: list[Line] = []
    for r in rows:
        d = r["sale_date"]
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        raw_cust = str(r.get("customer_account") or r.get("customer_name") or "").strip()
        cust = CASH_ACCOUNT if CASH_SPELLING.match(raw_cust) else raw_cust
        narr = str(r.get("narration") or "")
        masked = bool(MASKED_ID.search(narr))
        lines.append(Line(
            sale_date=d,
            invoice=INVOICE_PREFIX.sub("", str(r.get("invoice_no") or "")).strip(),
            line_no=int(r.get("line_no") or 0),
            customer=cust,
            item=str(r.get("item_name") or "").strip(),
            qty=_num(r.get("quantity")),
            rate=_num(r.get("rate_bhd")),
            gross=_num(r.get("gross_bhd")),
            net=_num(r.get("net_bhd")),
            warehouse=str(r.get("salesman_raw") or r.get("salesman_resolved") or "").strip(),
            narration="" if masked else narr,
            card_category=(str(r["category_name"]) if r.get("category_name") else None),
            raw_customer=raw_cust,
            narration_masked=masked,
            narration_seen=narr,
        ))
    lines.sort(key=lambda ln: (ln.sale_date, _invoice_key(ln.invoice), ln.invoice, ln.line_no))
    return lines


@dataclass
class Item:
    item: str                 # the Focus item name (the Data sheet's key)
    code: str
    product: str              # the name without the code and the supplier tag
    category: str
    how: str                  # how the category was found (categories.resolve)
    cur_net: float = 0
    prev_net: float = 0
    cur_units: float = 0
    prev_units: float = 0
    cur_qty: float = 0
    prev_qty: float = 0


@dataclass
class Model:
    period: Period
    lines: list[Line]
    items: dict[str, Item]
    reps: list[str]
    categories: list[str]
    products: list[Item]           # counted items, in the Products sheet's order
    not_counted: list[Item]        # the handset row's items
    customers: list[str]           # Cash Customer first, then accounts
    category_names: dict = field(default_factory=dict)
    upcoming: dict = field(default_factory=dict)       # code -> Upcoming card category
    warnings: list[str] = field(default_factory=list)
    empty_columns: list[str] = field(default_factory=list)   # day-book columns empty on every line


SUPPLIER_TAG = re.compile(r"\s*\((?:VFAN|WEKOME|WK)\).*$", re.I)


def product_name(item: str, code: str) -> str:
    """'T17 Open Ear Clip On - Type-C Port (VFAN) T17' -> 'Open Ear Clip On - Type-C Port'."""
    text = item[len(code):] if item.startswith(code) else item
    text = SUPPLIER_TAG.sub("", text)
    return " ".join(text.split())


# How wide a label is on the Dashboard: Arial's advance widths, in thousandths of an em, for the printable
# ASCII characters (any other character counts as a digit). The top-ten tables give a label five columns.
_ARIAL = (278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556,
          556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278,
          500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469,
          556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500,
          278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584)
LABEL_FITS = 29000             # wider than this a label is shortened, at a whole word, with " ..."
LABEL_ROOM_PRODUCT = 28920     # the room the shortened label (" ..." included) keeps: products
LABEL_ROOM_ACCOUNT = 29200     # ... and account names (the Sep 2026 file's own cut, every label reproduced)
_DANGLING = ("+", "&", "-", "/")


def label_width(text: str) -> int:
    return sum(_ARIAL[ord(ch) - 32] if 32 <= ord(ch) <= 126 else 556 for ch in text)


def shorten(full: str, room: int) -> str:
    if label_width(full) <= LABEL_FITS:
        return full
    words = full.split(" ")
    while words and label_width(" ".join(words) + " ...") > room:
        words.pop()
    while words and words[-1] in _DANGLING:
        words.pop()
    return " ".join(words) + " ..."


def dashboard_label(code: str, product: str) -> str:
    """A product as the Dashboard's top ten shows it: the code and the name, shortened when too wide."""
    return shorten(f"{code} {product}".strip(), LABEL_ROOM_PRODUCT)


def account_label(name: str) -> str:
    """An account as the Dashboard's top ten shows it."""
    return shorten(name.strip(), LABEL_ROOM_ACCOUNT)


def build(period: Period, rows: list[dict], *, category_names: dict | None = None,
          upcoming: dict | None = None) -> Model:
    names = dict(category_names or {})
    upcoming = {str(k).strip().casefold(): v for k, v in (upcoming or {}).items()}
    lines = [ln for ln in tidy(rows) if period.prev_start <= ln.sale_date <= period.end]
    empty = [label for label, key in (("Discount", "discount_bhd"), ("VAT Amount", "vat_amount_bhd"))
             if rows and all(r.get(key) in (None, "") for r in rows)]
    warnings: list[str] = []

    # items and their categories
    items: dict[str, Item] = {}
    valued: dict[str, bool] = {}
    cards: dict[str, list[str]] = {}
    for ln in lines:
        valued[ln.item] = valued.get(ln.item, False) or bool(ln.net or ln.gross or ln.rate)
        cards.setdefault(ln.item, [])
        if ln.card_category and ln.card_category not in cards[ln.item]:
            cards[ln.item].append(ln.card_category)
    for name in dict.fromkeys(ln.item for ln in lines):
        code = cats.item_code(name)
        card = ([upcoming[code.casefold()]] if code.casefold() in upcoming else []) + cards.get(name, [])
        category, how = cats.resolve(code, card, names, no_value=not valued[name])
        items[name] = Item(name, code, product_name(name, code), category, how)
    for ln in lines:
        it = items[ln.item]
        counted = it.category != cats.HANDSET
        if period.in_cur(ln.sale_date):
            it.cur_net += ln.net
            it.cur_qty += ln.qty
            it.cur_units += ln.qty if counted else 0
        else:
            it.prev_net += ln.net
            it.prev_qty += ln.qty
            it.prev_units += ln.qty if counted else 0

    def order(rows_, key_cur, key_prev, name):
        return sorted(rows_, key=lambda x: (-round(key_cur(x), 6), -round(key_prev(x), 6), name(x).casefold(), name(x)))

    counted_items = [it for it in items.values() if it.category != cats.HANDSET]
    products = order(counted_items, lambda i: i.cur_net, lambda i: i.prev_net, lambda i: i.item)
    not_counted = sorted((it for it in items.values() if it.category == cats.HANDSET),
                         key=lambda i: (-(i.cur_qty + i.prev_qty), i.item.casefold()))

    # categories: by the month's sales; Other last
    cat_net: dict[str, list[float]] = {}
    for it in counted_items:
        cur, prev = cat_net.setdefault(it.category, [0, 0])
        cat_net[it.category] = [cur + it.cur_net, prev + it.prev_net]
    categories = sorted(cat_net, key=lambda c: (c == cats.OTHER, -round(cat_net[c][0], 6), -round(cat_net[c][1], 6),
                                                c.casefold()))

    # salesmen and outlets: every one with a counted line in either month
    rep_net: dict[str, list[float]] = {}
    for ln in lines:
        if items[ln.item].category == cats.HANDSET:
            continue
        cur, prev = rep_net.setdefault(ln.rep, [0, 0])
        rep_net[ln.rep] = [cur + (ln.net if period.in_cur(ln.sale_date) else 0),
                           prev + (ln.net if period.in_prev(ln.sale_date) else 0)]
    reps = order(list(rep_net), lambda r: rep_net[r][0], lambda r: rep_net[r][1], lambda r: r)

    # customers: Cash Customer first, then every account with a line in either month
    cust_net: dict[str, list[float]] = {}
    for ln in lines:
        cur, prev = cust_net.setdefault(ln.customer, [0, 0])
        cust_net[ln.customer] = [cur + (ln.net if period.in_cur(ln.sale_date) else 0),
                                 prev + (ln.net if period.in_prev(ln.sale_date) else 0)]
    accounts = order([c for c in cust_net if c != CASH_ACCOUNT], lambda c: cust_net[c][0],
                     lambda c: cust_net[c][1], lambda c: c)
    customers = ([CASH_ACCOUNT] if CASH_ACCOUNT in cust_net else []) + accounts

    # names a spreadsheet would read as one, or as a pattern: the totals would then not tie up
    for label, names_ in (("customer", list(cust_net)), ("item", list(items)), ("salesman", list(rep_net))):
        seen: dict[str, str] = {}
        for n in names_:
            k = n.casefold()
            if k in seen and seen[k] != n:
                warnings.append(f"two {label} names differ only in capitals; the sheet adds them together")
            seen[k] = n
            if any(ch in n for ch in "*?~"):
                warnings.append(f"a {label} name holds * ? or ~, which a spreadsheet reads as a pattern")
    return Model(period, lines, items, reps, categories, products, not_counted, customers, names,
                 dict(upcoming), warnings, empty)
