"""The month's facts: every figure the monthly report's ten comment lines and three notes may name.

The words of /yq-month (five Highlights, five Points to note, the notes under the top products, under the top
accounts and on the Customers sheet) are drafted with placeholders only, never a typed figure; scripts.ops.textcheck
(`python -m scripts.ops check`) holds the placeholder language and checks the draft. This module works out the facts
the placeholders name, and writes them beside the build's record with the template the draft is written in.

WHERE A FACT COMES FROM. From the EVALUATED workbook wherever it has a cell for the figure, so a line never says a
figure the file does not show: the Dashboard's tiles (row 9, and row 10's change and "from" figures), its customer
base block and its "Other products" / "Other accounts" lines, the Daily sheet's days and week blocks, and the rows of
the Salesmen, Categories, Products and Customers sheets. A figure the workbook has no cell for comes from the model's
day-book lines, accessories only as the tiles count them (a handset line is left out): the bulk invoices, each
salesman's largest order, the accounts' first invoices, the unit-price mix, gross and VAT (the Dashboard's VAT line
is text).

A change (kind signed) is the share a figure moved by against the month before (+0.25: up a quarter); it has no
value when the month before had nothing (a placeholder naming it is refused). A share (kind pct) is a part of the
month's whole. The digest is the sha256 of the facts alone: neither the data date nor the day the report is
prepared is a fact, so a later load that leaves the month's figures as they were leaves the digest as it was.

THE FACTS (M the report month, M-1 the month before; kinds in brackets)
  month, month_prev                          [month]
  net, units, invoices, avg_invoice, avg_price, accounts, reps_active
                                             the Dashboard's seven tiles [bhd or int], each with <key>_prev (the
                                             "from" figure) and <key>_change [signed]
  gross, vat                                 [bhd] M's gross (VAT included) and its VAT, from the lines
  accounts_both, accounts_cur_only, accounts_prev_only
                                             [int] the customer base block: accounts invoiced in both months, in M
                                             only, in M-1 only; each with _net, _net_prev [bhd], _change [signed],
                                             _share, _share_prev [pct of that month's net]
  cash_net, cash_net_prev, cash_change, cash_share, cash_share_prev     the block's cash customers line
  wk1 .. wk5   .label [text] .net .net_prev [bhd] .share .share_prev [pct] .change [signed]
               .first_accounts .first_accounts_by .first_accounts_prev .first_accounts_prev_by [int]
                                             the Daily sheet's week blocks (1 to 7 ... 29 to month end), and the
                                             accounts whose first invoice of M (of M-1) falls in the block, and by its
                                             end
  first_invoice_day, first_invoice_day_prev  [date] the day of the month's first invoice
  days_sold, days_sold_prev                  [int] days with net sales above nought (the Daily sheet)
  best_day, best_day_prev [date], best_day_net, best_day_net_prev [bhd]  the day with the most net sales (the first)
  bulk_threshold                             [bhd] an invoice of at least this net value is a bulk order
  bulk_count, bulk_count_prev [int], bulk_net, bulk_net_prev [bhd], bulk_share, bulk_share_prev [pct]
  net_ex_bulk, net_ex_bulk_prev [bhd], net_ex_bulk_change [signed]     the month's net without its bulk invoices
  bulk1 .. bulk10  .customer .rep .code .product [text] .net .product_net [bhd] .day [date]
                                             M's bulk invoices, largest first (then the earliest); the main product is
                                             the invoice's largest item
  reps                                       [int] the Salesmen sheet's rows
  rep1 .. repN  .name .type (salesman or outlet) [text] .net .net_prev .avg_invoice .largest [bhd] .change [signed]
                .share [pct] .units .units_prev .invoices .invoices_prev .accounts .accounts_prev [int]
                .largest_customer [text] .largest_day [date]
                                             the Salesmen sheet's rows in its order (M's net sales first); the largest
                                             order is the salesman's largest invoice in M
  categories, products_sold                  [int] the Categories sheet's rows and its products-sold total
  cat1 .. catN  .name [text] .net .net_prev .avg_price [bhd] .change [signed] .share [pct] .units .units_prev
                .products [int]              the Categories sheet's rows by M's net sales (Other among them)
  prod1 .. prod10  .code .name .category [text] .net .net_prev .avg_price .bulk_net .net_ex_bulk [bhd]
                   .change [signed] .share [pct] .units .units_prev .bulk_invoices [int]
                                             the Dashboard's top ten products (the Products sheet's rank), and how much
                                             of each came on M's bulk invoices
  products_other_net, products_other_net_prev   [bhd] the Dashboard's "Other products" line
  accounts_listed                            [int] the accounts on the Customers sheet (either month)
  acct1 .. acctN  .name [text]               every account by the Customers sheet's rank, and for the top ten also
                  .net .net_prev [bhd] .change [signed] .share [pct] .invoices .invoices_prev [int] .rep .status [text]
  accounts_other_net, accounts_other_net_prev   [bhd] the Dashboard's "Other accounts" line
  band1 .. band5  .label [text] .units .units_prev [int] .net .net_prev [bhd] .units_share .units_share_prev
                  .net_share .net_share_prev [pct]
                                             the unit-price mix: the lines by net price a unit (UNIT_PRICE_BANDS, the
                                             lower edge in, the upper out), of the lines with units above nought and
                                             a net of nought or more; the shares are of those lines
  stock.*, moves.*, recon.*, kpi.*           the summary facts a caller passes (see month_facts)

CONTRACT (the monthly build relies on it)
  month_facts(book, model, facts, *, bulk_invoice_bhd, data_date=None, stock=None, movements=None, recon=None,
              kpi=None) -> {"month", "data_date", "digest", "facts": {key: {"value", "kind", "shown"}}}
      book        the month's workbook, evaluated (sheets_core.build, then book.evaluate(); a cell not evaluated yet
                  is evaluated here); a cell that holds an error value gives a fact with no value
      model       its model (a month: a week is refused); facts its sheets_core.Facts (the outlets: a salesman's type)
      bulk_invoice_bhd   the bulk threshold in BHD, a number of nought or more (config report.bulk_invoice_bhd, or
                  the approved default: the caller's)
      data_date   the data date the build ran on (a date or 'YYYY-MM-DD'): written in the file, never in the digest
      stock, movements, recon, kpi   optional summaries, each {name: (value, kind)} or {name: {"value", "kind"}};
                  they become stock.<name>, moves.<name>, recon.<name>, kpi.<name>. Only figures that stay as they
                  are until the final build belong here: a moving figure changes the digest, and the approval with it
    Refused with ValueError: a week's model, a threshold that is no amount, a summary that is not one.
  write_files(out_dir, month, facts_doc) -> {"facts": Path, "commentary": Path, "template_written": bool}
      writes <out_dir>/report-month-<M>.facts.json every time (one fact a line) and, only when it does not exist yet,
      <out_dir>/report-month-<M>.commentary.json, the template:
        {"layout": "commentary v1", "month", "data_date", "facts": the facts file's name (beside it),
         "facts_digest", "slots": {highlight_1 .. highlight_5, point_1 .. point_5, products_note, accounts_note,
         customers_note: ""}}
      An existing template is never written over: his draft survives every rebuild. Writes go through
      scripts.ops.paths.write_text (under business_data/ or exports/ of the data root only). A facts_doc of another
      month, or whose digest does not match its facts: ValueError.
  facts_path(out_dir, month), commentary_path(out_dir, month) -> Path;  template(month, facts_doc, facts_name) -> dict;
  facts_text(facts_doc) -> str (the facts file's text, valid JSON)
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from scripts.ops import paths, textcheck
from scripts.ops.workbook import categories as cats
from scripts.ops.workbook import formula as fx
from scripts.ops.workbook import model as mdl
from scripts.ops.workbook import sheets_core as sc

TOP_N = sc.TOP_N                 # the Dashboard's top products and accounts
BULK_LISTED = 10                 # the bulk invoices listed one by one (the counts and totals take every one)
# the unit-price mix: (lower edge, upper edge or None, label); a net price a unit at the lower edge is in the band
UNIT_PRICE_BANDS = ((0, 1, "under BHD 1"), (1, 2, "BHD 1 to under 2"), (2, 5, "BHD 2 to under 5"),
                    (5, 10, "BHD 5 to under 10"), (10, None, "BHD 10 or more"))
# the Dashboard's tiles: (key, kind, value cell, "from" cell, change cell)
TILES = (("net", "bhd", "B9", "C10", "B10"), ("units", "int", "F9", "G10", "F10"),
         ("invoices", "int", "H9", "I10", "H10"), ("avg_invoice", "bhd", "K9", "L10", "K10"),
         ("avg_price", "bhd", "M9", "N10", "M10"), ("accounts", "int", "O9", "P10", "O10"),
         ("reps_active", "int", "Q9", "R10", "Q10"))
# the Dashboard's customer base block: (key, row); P = accounts, Q = M-1 net, R = M net; row 63 the cash customers
COHORTS = (("accounts_both", 60), ("accounts_cur_only", 61), ("accounts_prev_only", 62))
CASH_ROW = 63
SUMMARIES = (("stock", "stock"), ("movements", "moves"), ("recon", "recon"), ("kpi", "kpi"))
LAYOUT = textcheck.LAYOUT


# ── small helpers ──────────────────────────────────────────────────────────────────────────────

class _Out:
    def __init__(self):
        self.facts: dict[str, dict] = {}

    def add(self, key: str, value, kind: str) -> None:
        if key in self.facts:
            raise ValueError(f"month_facts: the fact {key} is worked out twice")
        self.facts[key] = textcheck.make_fact(key, value, kind)


class _Cells:
    """The evaluated workbook's cells, by sheet and A1 reference."""

    def __init__(self, book):
        self.book = book

    def get(self, sheet: str, ref: str):
        try:
            ws = self.book.get(sheet)
        except KeyError:
            raise ValueError(f"month_facts: the workbook has no {sheet} sheet") from None
        r, c = fx.parse_cell(ref)
        if (r, c) not in ws.cells:
            return None
        v = self.book.value(sheet, r, c)
        return None if isinstance(v, fx.Err) else v

    def num(self, sheet: str, ref: str) -> float | None:
        v = self.get(sheet, ref)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        return float(v)

    def text(self, sheet: str, ref: str) -> str | None:
        v = self.get(sheet, ref)
        return None if v is None else str(v)


def _ratio(cur, prev) -> float | None:
    """cur against prev as a change; None with nothing to compare with."""
    if cur is None or not prev:
        return None
    return cur / prev - 1


def _share(part, whole) -> float | None:
    if part is None or not whole:
        return None
    return part / whole


def _round_key(x) -> float:
    return round(x or 0, 6)


def _iso(x) -> str | None:
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x.date().isoformat()
    if isinstance(x, date):
        return x.isoformat()
    try:
        return date.fromisoformat(str(x)[:10]).isoformat()
    except ValueError:
        raise ValueError(f"month_facts: the data date is a date, not {x!r}") from None


@dataclass
class _Invoice:
    order: int                        # the first appearance in the model's lines (their order)
    day: date
    customer: str
    rep: str
    net: float = 0.0
    items: dict = field(default_factory=dict)     # item -> its net on the invoice, in line order


def _counted(model: mdl.Model, ln) -> bool:
    return model.items[ln.item].category != cats.HANDSET


def _invoices(model: mdl.Model) -> list[_Invoice]:
    """The invoices of both months, accessory lines only, in the order the Data sheet first lists them."""
    by: dict[tuple, _Invoice] = {}
    for ln in model.lines:
        if not _counted(model, ln):
            continue
        key = (ln.invoice, ln.sale_date)
        inv = by.get(key)
        if inv is None:
            inv = by[key] = _Invoice(len(by), ln.sale_date, ln.customer, ln.rep)
        inv.net += ln.net
        inv.items[ln.item] = inv.items.get(ln.item, 0.0) + ln.net
    return list(by.values())


def _block(day: int) -> int:
    """The week block (0 to 4) a day of the month falls in (sheets_core.WEEK_BLOCKS)."""
    for k, (_label, d1, d2) in enumerate(sc.WEEK_BLOCKS):
        if d1 <= day <= d2:
            return k
    return len(sc.WEEK_BLOCKS) - 1


# ── the facts ──────────────────────────────────────────────────────────────────────────────────

def _totals(out: _Out, cells: _Cells, model: mdl.Model) -> tuple[float | None, float | None]:
    for key, kind, cur_ref, prev_ref, change_ref in TILES:
        prev = cells.num("Dashboard", prev_ref)
        out.add(key, cells.num("Dashboard", cur_ref), kind)
        out.add(f"{key}_prev", prev, kind)
        out.add(f"{key}_change", cells.num("Dashboard", change_ref) if prev else None, "signed")
    cur = [ln for ln in model.lines if model.period.in_cur(ln.sale_date)]
    out.add("gross", math.fsum(ln.gross for ln in cur), "bhd")
    out.add("vat", math.fsum(ln.gross - ln.net for ln in cur), "bhd")
    return cells.num("Dashboard", "B9"), cells.num("Dashboard", "C10")


def _cohorts(out: _Out, cells: _Cells, net, net_prev) -> None:
    for key, row in COHORTS:
        cur, prev = cells.num("Dashboard", f"R{row}"), cells.num("Dashboard", f"Q{row}")
        out.add(key, cells.num("Dashboard", f"P{row}"), "int")
        out.add(f"{key}_net", cur, "bhd")
        out.add(f"{key}_net_prev", prev, "bhd")
        out.add(f"{key}_change", _ratio(cur, prev), "signed")
        out.add(f"{key}_share", _share(cur, net), "pct")
        out.add(f"{key}_share_prev", _share(prev, net_prev), "pct")
    cur, prev = cells.num("Dashboard", f"R{CASH_ROW}"), cells.num("Dashboard", f"Q{CASH_ROW}")
    out.add("cash_net", cur, "bhd")
    out.add("cash_net_prev", prev, "bhd")
    out.add("cash_change", _ratio(cur, prev), "signed")
    out.add("cash_share", _share(cur, net), "pct")
    out.add("cash_share_prev", _share(prev, net_prev), "pct")


def _days(out: _Out, cells: _Cells, model: mdl.Model) -> None:
    p = model.period
    # the accounts' first invoice of each month (accessory lines; the cash customer is no account)
    first: dict[tuple[str, str], date] = {}
    first_day: dict[str, date] = {}
    for ln in model.lines:
        if not _counted(model, ln):
            continue
        m = "cur" if p.in_cur(ln.sale_date) else "prev" if p.in_prev(ln.sale_date) else None
        if m is None:
            continue
        first_day.setdefault(m, ln.sale_date)
        if ln.customer != mdl.CASH_ACCOUNT:
            first.setdefault((m, ln.customer), ln.sale_date)
    counts = {m: [0] * len(sc.WEEK_BLOCKS) for m in ("cur", "prev")}
    for (m, _cust), d in first.items():
        counts[m][_block(d.day)] += 1
    for k in range(len(sc.WEEK_BLOCKS)):
        row, key = 6 + k, f"wk{k + 1}"
        cur, prev = cells.num("Daily", f"K{row}"), cells.num("Daily", f"I{row}")
        out.add(f"{key}.label", cells.text("Daily", f"H{row}"), "text")
        out.add(f"{key}.net", cur, "bhd")
        out.add(f"{key}.net_prev", prev, "bhd")
        out.add(f"{key}.share", cells.num("Daily", f"L{row}"), "pct")
        out.add(f"{key}.share_prev", cells.num("Daily", f"J{row}"), "pct")
        out.add(f"{key}.change", _ratio(cur, prev), "signed")
        out.add(f"{key}.first_accounts", counts["cur"][k], "int")
        out.add(f"{key}.first_accounts_by", sum(counts["cur"][:k + 1]), "int")
        out.add(f"{key}.first_accounts_prev", counts["prev"][k], "int")
        out.add(f"{key}.first_accounts_prev_by", sum(counts["prev"][:k + 1]), "int")
    out.add("first_invoice_day", first_day.get("cur"), "date")
    out.add("first_invoice_day_prev", first_day.get("prev"), "date")
    for suffix, col, start, end in (("", "D", p.start, p.end), ("_prev", "C", p.prev_start, p.prev_end)):
        days = []
        for d in range(1, (end - start).days + 2):
            v = cells.num("Daily", f"{col}{5 + d}")
            days.append((start + timedelta(days=d - 1), fx.r15(v) if v else 0.0))
        sold = [x for x in days if x[1] > 0]
        best = max(sold, key=lambda x: (x[1], -x[0].toordinal())) if sold else None
        out.add(f"days_sold{suffix}", len(sold), "int")
        out.add(f"best_day{suffix}", best[0] if best else None, "date")
        out.add(f"best_day_net{suffix}", best[1] if best else None, "bhd")


def _bulk(out: _Out, model: mdl.Model, invoices: list[_Invoice], threshold: float, net, net_prev) -> list[_Invoice]:
    p = model.period
    big = [inv for inv in invoices if fx.r15(inv.net) >= threshold]
    cur = sorted((inv for inv in big if p.in_cur(inv.day)), key=lambda v: (-_round_key(v.net), v.order))
    prev = [inv for inv in big if p.in_prev(inv.day)]
    cur_net, prev_net = math.fsum(v.net for v in cur), math.fsum(v.net for v in prev)
    out.add("bulk_threshold", threshold, "bhd")
    out.add("bulk_count", len(cur), "int")
    out.add("bulk_count_prev", len(prev), "int")
    out.add("bulk_net", cur_net, "bhd")
    out.add("bulk_net_prev", prev_net, "bhd")
    out.add("bulk_share", _share(cur_net, net), "pct")
    out.add("bulk_share_prev", _share(prev_net, net_prev), "pct")
    ex_cur = None if net is None else net - cur_net
    ex_prev = None if net_prev is None else net_prev - prev_net
    out.add("net_ex_bulk", ex_cur, "bhd")
    out.add("net_ex_bulk_prev", ex_prev, "bhd")
    out.add("net_ex_bulk_change", _ratio(ex_cur, ex_prev), "signed")
    for n, inv in enumerate(cur[:BULK_LISTED], 1):
        item, item_net = max(inv.items.items(), key=lambda kv: _round_key(kv[1]))     # the first largest line
        it = model.items[item]
        out.add(f"bulk{n}.customer", inv.customer, "text")
        out.add(f"bulk{n}.rep", inv.rep, "text")
        out.add(f"bulk{n}.net", inv.net, "bhd")
        out.add(f"bulk{n}.day", inv.day, "date")
        out.add(f"bulk{n}.code", it.code, "text")
        out.add(f"bulk{n}.product", it.product, "text")
        out.add(f"bulk{n}.product_net", item_net, "bhd")
    return cur


def _reps(out: _Out, cells: _Cells, model: mdl.Model, lay: sc.Layout, outlets, invoices: list[_Invoice]) -> None:
    p = model.period
    out.add("reps", len(model.reps), "int")
    for i, rep in enumerate(model.reps):
        n, key = sc.ex(lay.sal_first + i), f"rep{i + 1}"
        prev = cells.num("Salesmen", f"C{n}")
        out.add(f"{key}.name", cells.text("Salesmen", f"B{n}"), "text")
        out.add(f"{key}.type", "outlet" if rep in outlets else "salesman", "text")
        out.add(f"{key}.net", cells.num("Salesmen", f"D{n}"), "bhd")
        out.add(f"{key}.net_prev", prev, "bhd")
        out.add(f"{key}.change", cells.num("Salesmen", f"F{n}") if prev else None, "signed")     # 'n/a' reads None
        out.add(f"{key}.share", cells.num("Salesmen", f"G{n}"), "pct")
        out.add(f"{key}.units", cells.num("Salesmen", f"I{n}"), "int")
        out.add(f"{key}.units_prev", cells.num("Salesmen", f"H{n}"), "int")
        out.add(f"{key}.invoices", cells.num("Salesmen", f"K{n}"), "int")
        out.add(f"{key}.invoices_prev", cells.num("Salesmen", f"J{n}"), "int")
        out.add(f"{key}.avg_invoice", cells.num("Salesmen", f"L{n}"), "bhd")
        out.add(f"{key}.accounts", cells.num("Salesmen", f"N{n}"), "int")
        out.add(f"{key}.accounts_prev", cells.num("Salesmen", f"M{n}"), "int")
        mine = [v for v in invoices if v.rep == rep and p.in_cur(v.day)]
        big = max(mine, key=lambda v: (_round_key(v.net), -v.order)) if mine else None
        out.add(f"{key}.largest", big.net if big else None, "bhd")
        out.add(f"{key}.largest_customer", big.customer if big else None, "text")
        out.add(f"{key}.largest_day", big.day if big else None, "date")


def _categories(out: _Out, cells: _Cells, model: mdl.Model, lay: sc.Layout) -> None:
    rows = []
    for i in range(len(model.categories)):
        n = sc.ex(lay.cat_first + i)
        rows.append((n, cells.text("Categories", f"B{n}") or "", cells.num("Categories", f"G{n}"),
                     cells.num("Categories", f"F{n}")))
    rows.sort(key=lambda x: (-_round_key(x[2]), -_round_key(x[3]), x[1].casefold(), x[1]))
    out.add("categories", len(rows), "int")
    out.add("products_sold", cells.num("Categories", f"C{sc.ex(lay.cat_total)}"), "int")
    for k, (n, name, cur, prev) in enumerate(rows, 1):
        key = f"cat{k}"
        out.add(f"{key}.name", name, "text")
        out.add(f"{key}.net", cur, "bhd")
        out.add(f"{key}.net_prev", prev, "bhd")
        out.add(f"{key}.change", cells.num("Categories", f"I{n}") if prev else None, "signed")
        out.add(f"{key}.share", cells.num("Categories", f"J{n}"), "pct")
        out.add(f"{key}.units", cells.num("Categories", f"E{n}"), "int")
        out.add(f"{key}.units_prev", cells.num("Categories", f"D{n}"), "int")
        out.add(f"{key}.products", cells.num("Categories", f"C{n}"), "int")
        out.add(f"{key}.avg_price", cells.num("Categories", f"K{n}"), "bhd")


def _ranked_rows(cells: _Cells, sheet: str, rank_col: str, first: int, count: int, keep=None) -> dict[int, int]:
    """rank -> the Excel row holding it, read from the evaluated rank column."""
    out: dict[int, int] = {}
    for i in range(count):
        n = sc.ex(first + i)
        if keep is not None and not keep(n):
            continue
        rank = cells.num(sheet, f"{rank_col}{n}")
        if rank is not None:
            out.setdefault(int(round(rank)), n)
    return out


def _products(out: _Out, cells: _Cells, lay: sc.Layout, bulk_cur: list[_Invoice]) -> None:
    by_rank = _ranked_rows(cells, "Products", "N", lay.prod_first, lay.P)
    for k in range(1, min(TOP_N, lay.P) + 1):
        n = by_rank.get(k)
        if n is None:
            continue
        key, item = f"prod{k}", cells.text("Products", f"M{n}")
        cur, prev = cells.num("Products", f"H{n}"), cells.num("Products", f"G{n}")
        on_bulk = [v.items[item] for v in bulk_cur if item in v.items]
        bulk_net = math.fsum(on_bulk)
        out.add(f"{key}.code", cells.text("Products", f"B{n}"), "text")
        out.add(f"{key}.name", cells.text("Products", f"C{n}"), "text")
        out.add(f"{key}.category", cells.text("Products", f"D{n}"), "text")
        out.add(f"{key}.net", cur, "bhd")
        out.add(f"{key}.net_prev", prev, "bhd")
        out.add(f"{key}.change", cells.num("Products", f"J{n}") if prev else None, "signed")
        out.add(f"{key}.share", cells.num("Products", f"K{n}"), "pct")
        out.add(f"{key}.units", cells.num("Products", f"F{n}"), "int")
        out.add(f"{key}.units_prev", cells.num("Products", f"E{n}"), "int")
        out.add(f"{key}.avg_price", cells.num("Products", f"L{n}"), "bhd")
        out.add(f"{key}.bulk_net", bulk_net, "bhd")
        out.add(f"{key}.bulk_invoices", len(on_bulk), "int")
        out.add(f"{key}.net_ex_bulk", None if cur is None else cur - bulk_net, "bhd")
    out.add("products_other_net", cells.num("Dashboard", "Q52"), "bhd")
    out.add("products_other_net_prev", cells.num("Dashboard", "P52"), "bhd")


def _accounts(out: _Out, cells: _Cells, lay: sc.Layout, net) -> None:
    by_rank = _ranked_rows(cells, "Customers", "K", lay.cust_first, lay.C,
                           keep=lambda n: cells.text("Customers", f"C{n}") == "Account")
    out.add("accounts_listed", len(by_rank), "int")
    for k in sorted(by_rank):
        n, key = by_rank[k], f"acct{k}"
        out.add(f"{key}.name", cells.text("Customers", f"B{n}"), "text")
        if k > TOP_N:
            continue
        cur, prev = cells.num("Customers", f"F{n}"), cells.num("Customers", f"E{n}")
        out.add(f"{key}.net", cur, "bhd")
        out.add(f"{key}.net_prev", prev, "bhd")
        out.add(f"{key}.change", _ratio(cur, prev), "signed")
        out.add(f"{key}.share", _share(cur, net), "pct")
        out.add(f"{key}.invoices", cells.num("Customers", f"I{n}"), "int")
        out.add(f"{key}.invoices_prev", cells.num("Customers", f"H{n}"), "int")
        out.add(f"{key}.rep", cells.text("Customers", f"D{n}"), "text")
        out.add(f"{key}.status", cells.text("Customers", f"J{n}"), "text")
    out.add("accounts_other_net", cells.num("Dashboard", "H70"), "bhd")
    out.add("accounts_other_net_prev", cells.num("Dashboard", "G70"), "bhd")


def _band(price: float) -> int:
    for k, (lo, hi, _label) in enumerate(UNIT_PRICE_BANDS):
        if price >= lo and (hi is None or price < hi):
            return k
    return 0


def _bands(out: _Out, model: mdl.Model) -> None:
    p = model.period
    tally = {m: [[0.0, 0.0] for _ in UNIT_PRICE_BANDS] for m in ("cur", "prev")}
    for ln in model.lines:
        if not _counted(model, ln) or ln.qty <= 0 or ln.net < 0:
            continue
        m = "cur" if p.in_cur(ln.sale_date) else "prev" if p.in_prev(ln.sale_date) else None
        if m is None:
            continue
        units_net = tally[m][_band(fx.r15(ln.net / ln.qty))]
        units_net[0] += ln.qty
        units_net[1] += ln.net
    whole = {m: (math.fsum(u for u, _n in tally[m]), math.fsum(x for _u, x in tally[m])) for m in tally}
    for k, (_lo, _hi, label) in enumerate(UNIT_PRICE_BANDS):
        key = f"band{k + 1}"
        out.add(f"{key}.label", label, "text")
        for m, suffix in (("cur", ""), ("prev", "_prev")):
            units, net = tally[m][k]
            out.add(f"{key}.units{suffix}", units, "int")
            out.add(f"{key}.net{suffix}", net, "bhd")
            out.add(f"{key}.units_share{suffix}", _share(units, whole[m][0]), "pct")
            out.add(f"{key}.net_share{suffix}", _share(net, whole[m][1]), "pct")


def _summary(out: _Out, prefix: str, part) -> None:
    if part is None:
        return
    if not isinstance(part, dict):
        raise ValueError(f"month_facts: the {prefix} summary is {{name: (value, kind)}}, not {type(part).__name__}")
    for name, spec in part.items():
        if isinstance(spec, dict) and set(spec) <= {"value", "kind"} and "kind" in spec:
            value, kind = spec.get("value"), spec["kind"]
        elif isinstance(spec, (tuple, list)) and len(spec) == 2:
            value, kind = spec
        else:
            raise ValueError(f"month_facts: the {prefix} summary's {name!r} is (value, kind) or "
                             "{'value': ..., 'kind': ...}")
        out.add(f"{prefix}.{name}", value, kind)


def month_facts(book, model: mdl.Model, facts: sc.Facts, *, bulk_invoice_bhd, data_date=None, stock=None,
                movements=None, recon=None, kpi=None) -> dict:
    p = model.period
    if p.kind != "month":
        raise ValueError("month_facts: the facts are a month's (the weekly report writes its own lines)")
    if (isinstance(bulk_invoice_bhd, bool) or not isinstance(bulk_invoice_bhd, (int, float))
            or not math.isfinite(bulk_invoice_bhd) or bulk_invoice_bhd < 0):
        raise ValueError(f"month_facts: the bulk threshold is an amount in BHD of nought or more, not "
                         f"{bulk_invoice_bhd!r}")
    lay = sc.Layout(model)
    cells = _Cells(book)
    outlets = set(facts.outlets)
    invoices = _invoices(model)
    out = _Out()
    out.add("month", p.key, "month")
    out.add("month_prev", f"{p.prev_start:%Y-%m}", "month")
    net, net_prev = _totals(out, cells, model)
    _cohorts(out, cells, net, net_prev)
    _days(out, cells, model)
    bulk_cur = _bulk(out, model, invoices, float(bulk_invoice_bhd), net, net_prev)
    _reps(out, cells, model, lay, outlets, invoices)
    _categories(out, cells, model, lay)
    _products(out, cells, lay, bulk_cur)
    _accounts(out, cells, lay, net)
    _bands(out, model)
    for arg, prefix in SUMMARIES:
        _summary(out, prefix, {"stock": stock, "movements": movements, "recon": recon, "kpi": kpi}[arg])
    return {"month": p.key, "data_date": _iso(data_date), "digest": textcheck.digest_of(out.facts),
            "facts": out.facts}


# ── the two files ──────────────────────────────────────────────────────────────────────────────

def facts_path(out_dir, month: str) -> Path:
    return Path(out_dir) / f"report-month-{month}.facts.json"


def commentary_path(out_dir, month: str) -> Path:
    return Path(out_dir) / f"report-month-{month}.commentary.json"


def facts_text(doc: dict) -> str:
    """The facts file: valid JSON, one fact a line in the order they were worked out (easy to read and to search)."""
    head = ", ".join(f"{json.dumps(k)}: {json.dumps(doc.get(k), ensure_ascii=False)}"
                     for k in ("month", "data_date", "digest"))
    items = list(doc["facts"].items())
    lines = ["{" + head + ', "facts": {']
    for i, (key, fact) in enumerate(items):
        lines.append(f" {json.dumps(key, ensure_ascii=False)}: {json.dumps(fact, ensure_ascii=False, allow_nan=False)}"
                     + ("," if i < len(items) - 1 else ""))
    lines.append("}}")
    return "\n".join(lines) + "\n"


def template(month: str, facts_doc: dict, facts_name: str) -> dict:
    return {"layout": LAYOUT, "month": month, "data_date": facts_doc.get("data_date"), "facts": str(facts_name),
            "facts_digest": facts_doc["digest"], "slots": {slot: "" for slot in textcheck.SLOTS}}


def write_files(out_dir, month: str, facts_doc: dict) -> dict:
    if facts_doc.get("month") != month:
        raise ValueError(f"write_files: the facts are for {facts_doc.get('month')}, not {month}")
    if textcheck.digest_of(facts_doc.get("facts")) != facts_doc.get("digest"):
        raise ValueError("write_files: the facts do not match their digest")
    fp = paths.write_text(facts_path(out_dir, month), facts_text(facts_doc))
    cp = commentary_path(out_dir, month)
    made = False
    if not cp.exists():                       # his draft is never written over
        paths.write_text(cp, json.dumps(template(month, facts_doc, fp.name), indent=1, ensure_ascii=False) + "\n")
        made = True
    return {"facts": fp, "commentary": cp, "template_written": made}
