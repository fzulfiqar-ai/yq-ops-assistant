"""python -m scripts.ops stock [reorder | ageing | damage | file | <item code>]     (the command behind /yq-stock)

    (nothing)   the headline: the main store's stock as the portal shows it (items, units, selling
                value), at cost on two bases side by side when they differ, cover against KPI 1 (the
                aggregate days, the share of value above the KPI band, the change on last month, the
                bands by count and value, new arrivals apart), the order list in short, fast-mover
                availability, the ageing and its review in short, the damage store in short; and the
                day's Stock & Order Plan workbook, made once per data date (scripts.ops.workbook.stock_book)
    reorder     the whole order list with each item's supplier, the lead time it rests on (the last ten
                supplier invoices matched to their receipt), and the fast movers with their sales ranking
    ageing      main-store units on their supplier receipts, newest first, in age bands, crossed with
                the days since the item last sold, each band with a suggested action; then the ageing
                review (scripts.ops.stock_review: pace, clear target, the price cuts' effect)
    damage      the damage store in full (scripts.ops.stock_damage)
    file        the headline, and a new workbook even when the day's one exists (the next free name)
    <code>      one item: its Focus item code (a model code several items share lists them)

THE BASIS. The main store is config 'warehouses.main' (the Focus "Accessories Warehouse"); vans and
outlets come later (vans_beside() is the hook, empty until /yq-moves pairs the transfers). An item
is an accessory when it has ever moved in the main store, less config 'items.not_accessories'
(scripts.ops.items.accessory_items): SIM packs never move there, so SIM is out by the rule, never by
a word in a name. Every figure is read from the stock ledger (scripts.ops.ledger.for_audit: the
loaded file when it is proven equal to what production holds, else the database), except the
headline's units and selling value, which are the portal's own read (PORTAL_WAREHOUSE_SQL, word for
word app.reports.stock_by_warehouse, the Inventory page), and the portal's figure at cost
(app.metrics.STOCK_SQL through stock_cost_summary, the Command Centre's Products tile). The ledger's
main-store balance is set beside the snapshot item by item, and a difference is printed to check.

  * D is the ledger's last day. Every day-end figure is as at the end of a day; D can be a part day
    (the export morning), which the windows below keep.
  * Units sold = Sales Invoice units in every warehouse (vans and outlets sell; the main store
    issues), customer returns not deducted (they are back in a stock already). On the real ledger
    these units equal the day book's accessories without giveaways.
  * Daily rate = units sold in the RATE_DAYS days to the day ÷ the days of that window on which the
    main store held the item at the end of the day, never fewer than RATE_MIN_DAYS. Cover = units on
    hand ÷ the daily rate (no rate: "no sale in the window"). A rate that rests on fewer days in stock
    than that is starred on every screen (Rate.floored, FLOOR_NOTE): it can understate the demand of
    an item that was out most of the window, and the screen says so rather than hide it.
  * At landed cost (the owner, 15-Oct-2026) = the real landed cost of the code's goods-received note
    (mrn_landed_costs, the MRN XML), the row the portal itself costs from (mrn_costs: v_product_economics'
    rule, the latest receipt date, then the highest id); only where a code has no MRN landed cost, the
    pricing sheet (purchase_costs, its newest row = MAX(id), read on the read-only session: the read RPC
    may not see that table); where neither has the code, the latest supplier receipt's rate in the
    ledger (its Material Receipt Note line). An item with none is counted apart, never valued at
    nought. Every figure at cost names its basis and how many items rest on each (cost_words). The
    portal's figure is on its own basis (Focus average cost, else the latest landed cost) and is
    printed beside it when the two differ.
  * Item code: the price book writes the code and the stock name of an item, and the ledger names an
    item "<stock name> <code>"; an item missing from the price book takes the stock-balance name
    (items.Bridge) and the rest of its ledger name as its code.
  * KPI 1 cover: aggregate days = stock at cost ÷ the daily cost of sales (the daily rates at cost)
    over the items with a cost, display items and new arrivals left out; the share of that value on
    items above the top of the KPI band (or with no sale in the window); the same on the last day of
    last month, with today's costs. New arrivals = items first received in the RATE_DAYS days to the
    day: their rate is too short to judge, so they are a band of their own.
  * Order list: an item that sold in the window is listed when (on hand + on order) ÷ its rate is
    below the lead time + the low end of the KPI band; the quantity brings it to the lead time + the
    top of the band, less what is on order, in whole units. Goods on order are promise lines of the
    KPI log (business_data/ops/kpi_log.jsonl, kind 'promise': one per item from a proforma or a
    packing list), each deducted until a receipt of that item is dated after it; while none is
    logged the list says "goods on order not deducted". No purchase order is drafted.
  * Lead time: a supplier invoice (supplier_prices: number, date, model, quantity) is matched to the
    main-store receipt, at most LEAD_MAX_DAYS after it, that holds the same quantity of the most of
    its models, and of at least half of them; the days between the two are its lead time. With at
    least LEAD_MIN matched, the lead time is the median of the newest LEAD_SHOWN (printed, each with
    its basis); otherwise config 'stock.lead_time_days', and the screen says which.
  * Fast movers: the config 'stock.fast_movers' best sellers by units over the RANK_MONTHS full
    months before the month (display items out). Availability = available SKU-days ÷ SKU-days, a day
    counting when the main store's day-end balance is above nought and covers one day of the item's
    rate (the rate as on the last day of the period); beside it the same for every item that sold
    in the window (the wider basis) and the fast movers that were out on every day of the period.
  * Ageing: the main store's units on hand are allocated to the item's supplier receipts into the
    main store (Material Receipt Notes), newest first; units no receipt explains are "no receipt on
    file". A transfer back from a van is no receipt, so it never makes stock younger. Bands by days
    since the receipt, crossed with the days since the item last sold anywhere.
  * Display items (config 'stock.display_items', codes or names) stay in the headline, which must
    equal the portal, and are left out of cover, the order list, the fast movers and the ageing (the
    ageing table names them on a row of their own, so that it still adds up to the main store).

Reads, all through scripts.ops.db: the ledger (above), the newest stock_balance snapshot, the
portal's warehouse read, the Command Centre's stock rows, the price book (selling_prices: code and
name only), supplier_prices, and purchase_costs on the read-only session. A read that fails costs
its own part and is named under the screen; the ledger and the snapshot are required.

Numbers are computed here and formatted by scripts.ops.fmt; the skill relays the text as it is.
The counts in this file are the plan's definitions (the windows, the age bands, the last ten lead
times); the owner's values (lead time, the KPI band, how many fast movers, the display items) are
read from config.json, and a key config.json does not hold yet (config.OPTIONAL) takes the plan's
default here and is named on the screen. Screens and facts go to exports/ops/<D>/ (git-ignored).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path

from scripts.ops import db  # first: it blanks the side channels before any app import
from scripts.ops import (config, fmt, items, ledger, paths, stock_damage, stock_orders, stock_plan, stock_review,
                         stock_season, stockflow)

from app import metrics  # noqa: E402

COMMAND = "stock"
VIEW_WORDS = {"reorder": "reorder", "ageing": "ageing", "aging": "ageing", "damage": "damage", "file": "file"}
CLEAR_DAYS_DEFAULT = 90        # the plan's default while config.json has no 'stock.clear_target_days' (his 20-Oct rule)
EVERY_DAYS_DEFAULT = 30        # ... and no 'stock.order_every_days': he orders about monthly (20-Oct-2026)
WINDOW_MAX_DAYS = 365          # a join or fill window never reaches past a year (the workbook caps it the same)
RAISE_PARTS = False            # tests set it: a part of the screen that fails raises instead of standing aside
RATE_DAYS = 90                 # the window of an item's daily rate
RATE_MIN_DAYS = 30             # ... divided by at least this many days in stock
RANK_MONTHS = 12               # fast movers: best sellers over the full months before the month
FAST_MOVERS_DEFAULT = 20       # the plan's default while config.json has no 'stock.fast_movers'
COVER_KPI_DEFAULT = (30, 45)   # the plan's default while config.json has no 'stock.cover_kpi_days' (KPI sheet)
LEAD_SHOWN = 10                # the last ten measured lead times are printed, and the median is theirs
LEAD_MIN = 3                   # fewer matched invoices than this: the config lead time stands
LEAD_MAX_DAYS = 200            # a receipt this long after an invoice is not its receipt
LEAD_SAME_SHARE = Decimal("0.5")   # the receipt holds the same quantity of at least half the invoice's models
AGE_LIMITS = (90, 180, 365)    # age bands: up to 90 days, 91-180, 181-365, over 365
SALE_LIMITS = (30, 90, 180)    # days since the last sale: up to 30, 31-90, 91-180, over 180 or never
COVER_LIMITS = (90, 180, 365)  # cover bands above the KPI band
HEADLINE_ROWS = 5              # order-list rows on the headline
OLDEST_ROWS = 10               # items listed under the ageing table
NAME_WIDTH = 44
ZERO = Decimal(0)
ONE = Decimal(1)

AGE_BANDS = ("up to 90 days", "91 to 180 days", "181 to 365 days", "over 365 days", "no receipt on file")
SALE_BANDS = ("Sold in 30 days", "31 to 90", "91 to 180", "Over 180 or never")
# The only lever on old stock is the sales team (the owner, 20-Oct-2026): no price cut, no bundle.
AGE_ACTIONS = (
    "new stock: nothing to do yet",
    "offer it on every visit; order no more while it lasts",
    "push it through the sales team: a weekly target in the ageing review",
    "push it hard through the sales team: a weekly target in the ageing review",
    "to check: no supplier receipt on file explains these units",
)

# The portal's Inventory page reads the stock per warehouse with exactly this statement
# (app.reports.stock_by_warehouse; tests/test_ops_stock.py holds the two to the same text).
PORTAL_WAREHOUSE_SQL = (
    "SELECT warehouse_name, COALESCE(SUM(total_value_bhd),0) AS value_bhd, "
    "COALESCE(SUM(net_qty),0) AS qty, COUNT(*) AS items FROM stock_balance "
    "WHERE as_of_date=(SELECT MAX(as_of_date) FROM stock_balance) "
    "GROUP BY warehouse_name ORDER BY value_bhd DESC"
)
# The same snapshot, one row per item and warehouse; numbers as text, so no float gets in between.
SNAPSHOT_SQL = (
    "SELECT item_name, warehouse_name, net_qty::text AS net_qty, total_value_bhd::text AS value_bhd, "
    "as_of_date::text AS as_of_date FROM stock_balance "
    "WHERE as_of_date = (SELECT MAX(as_of_date) FROM stock_balance)"
)
PRICE_BOOK_SQL = ("SELECT DISTINCT sku_code, item_name FROM selling_prices "
                  "WHERE sku_code IS NOT NULL AND item_name IS NOT NULL")
SUPPLIER_SQL = ("SELECT invoice_no, invoice_date::text AS invoice_date, upper(model) AS model, "
                "COALESCE(qty, 0)::text AS qty, vendor FROM supplier_prices "
                "WHERE invoice_no IS NOT NULL AND invoice_date IS NOT NULL AND model IS NOT NULL")
# The real landed cost of a receipt (the goods-received notes' MRN XML): every row, and mrn_costs() picks the one
# the portal costs from. The read RPC can see this table.
MRN_SQL = ("SELECT id, upper(sku_code) AS code, landed_cost_bhd::text AS cost, effective_date::text AS effective_date, "
           "doc_no FROM mrn_landed_costs WHERE sku_code IS NOT NULL")
# The pricing sheet, for an item with no MRN landed cost: every row, and sheet_costs() keeps the newest per code
# (MAX(id), docs/CLAUDE.md data rule 2). The read RPC may not see this table: it is read on the read-only session.
SHEET_SQL = ("select id, upper(sku_code) as code, landed_cost_bhd::text as cost from purchase_costs "
             "where sku_code is not null")
# Every item of the item master, active or not, with its brand (the supplier where nothing better says it).
CATALOG_SQL = ("SELECT upper(item_code) AS code, display_name, category, brand, division, is_active, hidden "
               "FROM catalog_items WHERE item_code IS NOT NULL")
# The vendor of each code's newest purchase order (the PO PDFs loaded on the Orders page).
PO_VENDOR_SQL = ("SELECT upper(item_code) AS code, vendor FROM v_po_price_history "
                 "WHERE recency = 1 AND item_code IS NOT NULL AND vendor IS NOT NULL")
# The price book's latest change of each code, the cuts only (the dealer book MA_base, as the price-cut review).
PRICE_CUT_SQL = ("SELECT upper(sku_code) AS code, item_name, changed_on::text AS changed_on, "
                 "prev_price_bhd::text AS was, current_price_bhd::text AS now FROM v_price_change "
                 "WHERE current_price_bhd < prev_price_bhd")

BASIS_LINES = (
    "Basis: accessories (items that ever moved in the main store); units sold = Sales Invoice units in every "
    "warehouse, customer returns not deducted.",
    "At landed cost = the code's latest goods-received note (MRN), else the pricing sheet (its newest row), else the "
    "latest receipt rate; the portal's figure is Focus average cost, else the latest landed cost.",
)


class StockInput(ValueError):
    """Something the command cannot answer: one plain sentence, printed as it is."""


# ── small helpers ─────────────────────────────────────────────────────────────

def fold(text) -> str:
    """A code or a name as it is compared: spaces collapsed, case ignored."""
    return items.norm(text)


def _whole(x: Decimal) -> int:
    return int(Decimal(x).quantize(ONE, rounding=ROUND_HALF_UP))


def amount(x) -> Decimal:
    """A figure the portal returns (a JSON number arrives as a float) held to three decimals and
    written the short way: 505.0 is 505, 12.50 is 12.5."""
    v = fmt.dec(x).quantize(fmt.Q3, rounding=ROUND_HALF_UP)
    whole = v.to_integral_value()
    return whole if whole == v else v.normalize()


def days_text(x) -> str:
    """Days of cover as text: whole days, or 'no sale' for none."""
    return "no sale" if x is None else fmt.units(_whole(x))


def rate_text(x: Decimal, floored: bool = False) -> str:
    """Units a day: two decimals under ten, one above; a star when the rate rests on fewer than
    RATE_MIN_DAYS days in stock (FLOOR_NOTE says what that means)."""
    x = Decimal(x)
    text = f"{x.quantize(Decimal('0.01') if x < 10 else Decimal('0.1'), rounding=ROUND_HALF_UP):,}"
    return text + ("*" if floored else "")


FLOOR_NOTE = (f"  * in stock on fewer than {RATE_MIN_DAYS} of the last {RATE_DAYS} days: the rate divides by "
              f"{RATE_MIN_DAYS}, so it may understate the demand (its sales over a year are on /yq-stock <code>)")


def short(name, width: int = NAME_WIDTH) -> str:
    text = " ".join(str(name or "").split())
    return text if len(text) <= width else text[: width - 3].rstrip() + "..."


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{fmt.units(n)} {one if n == 1 else (many or one + 's')}"


def _indent(block: str, by: str = "  ") -> list[str]:
    return [by + ln for ln in block.splitlines()]


def _guarded(line: str) -> str:
    """Every line shown passes the wording guard (fmt.careful); a word from the data that the guard
    refuses is blanked, never printed."""
    try:
        return fmt.careful(line)
    except ValueError:
        out = []
        for part in re.split(r"(\s+)", line):
            try:
                out.append(fmt.careful(part))
            except ValueError:
                out.append("-" * len(part))
        return "".join(out)


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, date):
        return x.isoformat()
    return x


def month_start(d: date, back: int = 0) -> date:
    """The first day of d's month, `back` months earlier."""
    n = d.year * 12 + (d.month - 1) - back
    return date(n // 12, n % 12 + 1, 1)


def month_name(d: date) -> str:
    return d.strftime("%b %Y")


# ── settings ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Settings:
    lead_default: int
    fast_n: int
    low: int
    high: int
    display: frozenset
    defaults_used: tuple = ()      # config.OPTIONAL keys config.json does not hold (the plan's default stood in)
    clear_days: int = CLEAR_DAYS_DEFAULT   # ageing: the days the stock left should sell in (the owner's clear target)
    damage: str = ""               # config 'warehouses.damage': the damage store ('' while it is not set up)
    not_counted: tuple = ()        # config 'warehouses.not_counted': the stores outside the sellable pool
    suppliers: dict = field(default_factory=dict)          # item code (upper) -> supplier, config 'stock.suppliers'
    supplier_aliases: dict = field(default_factory=dict)   # a supplier's spelling (folded) -> its one name
    every_days: int = EVERY_DAYS_DEFAULT                   # the order rhythm: days between two orders to a supplier
    supplier_every: dict = field(default_factory=dict)     # supplier -> its own rhythm in days
    supplier_lead: dict = field(default_factory=dict)      # supplier -> its lead time where none can be measured
    season_lift: dict = field(default_factory=dict)        # month 1-12 -> a lift that stands in for last year's
    pack_sizes: dict = field(default_factory=dict)         # item code (upper) -> units in a carton


def settings() -> Settings:
    used: list[str] = []

    def optional(key: str, default):
        try:
            return config.get(key)
        except config.ConfigMissing:
            if key in config.OPTIONAL and key not in config.load():
                used.append(key)
                return default
            raise

    lead = config.get("stock.lead_time_days")
    if lead <= 0:
        raise config.ConfigMissing("config: 'stock.lead_time_days' must be a number of days above nought")
    fast = optional("stock.fast_movers", FAST_MOVERS_DEFAULT)
    if fast <= 0:
        raise config.ConfigMissing("config: 'stock.fast_movers' must be a count above nought")
    band = optional("stock.cover_kpi_days", list(COVER_KPI_DEFAULT))
    if (not isinstance(band, (list, tuple)) or len(band) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in band) or not 0 < band[0] < band[1]):
        raise config.ConfigMissing("config: 'stock.cover_kpi_days' is [low, high], two whole numbers of days, "
                                   "low above nought and below high")
    display = frozenset(fold(x) for x in config.get("stock.display_items") if fold(x))
    clear = optional("stock.clear_target_days", CLEAR_DAYS_DEFAULT)
    if clear <= 0:
        raise config.ConfigMissing("config: 'stock.clear_target_days' must be a number of days above nought")
    suppliers = {code_key(k): " ".join(str(v).split()) for k, v in config.get("stock.suppliers").items()
                 if code_key(k) and str(v).strip()}
    aliases = {fold(k): " ".join(str(v).split()) for k, v in config.get("stock.supplier_aliases").items()
               if fold(k) and str(v).strip()}
    every = optional("stock.order_every_days", EVERY_DAYS_DEFAULT)
    if every < 0:
        raise config.ConfigMissing("config: 'stock.order_every_days' must be a number of days, nought or more")

    def days_by_supplier(key: str, least: int) -> dict:
        out = {}
        for k, v in config.get(key).items():
            name = " ".join(str(k).split())
            if not name or isinstance(v, bool) or not isinstance(v, int) or v < least:
                raise config.ConfigMissing(f"config: every entry of '{key}' is a supplier and a whole number of days "
                                           f"({least} or more)")
            out[aliases.get(fold(name), name)] = v
        return out

    packs = {}
    for k, v in config.get("stock.pack_sizes").items():
        if not code_key(k) or isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise config.ConfigMissing("config: every entry of 'stock.pack_sizes' is an item code and a whole number of "
                                       "units, 1 or more")
        packs[code_key(k)] = v
    return Settings(lead_default=int(lead), fast_n=int(fast), low=int(band[0]), high=int(band[1]),
                    display=display, defaults_used=tuple(used), clear_days=int(clear),
                    damage=str(config.get("warehouses.damage") or "").strip(),
                    not_counted=tuple(str(w) for w in config.get("warehouses.not_counted") if str(w or "").strip()),
                    suppliers=suppliers, supplier_aliases=aliases, every_days=int(every),
                    supplier_every=days_by_supplier("stock.supplier_every_days", 0),
                    supplier_lead=days_by_supplier("stock.supplier_lead_days", 1),
                    season_lift=dict(config.get("stock.season_lift")), pack_sizes=packs)


# ── the reads ─────────────────────────────────────────────────────────────────

@dataclass
class Inputs:
    rows: list[dict]                       # the stock ledger (scripts.ops.ledger movements)
    ledger_note: str                       # where the ledger was read from, and why
    snapshot: list[dict]                   # stock_balance, the newest snapshot, one row per item and warehouse
    portal_warehouses: list[dict]          # the portal's own read per warehouse
    portal_cost: dict | None = None        # metrics.stock_cost_summary of the Command Centre's stock rows
    price_book: list[dict] | None = None   # {sku_code, item_name}
    mrn: dict | None = None                # code -> {cost, effective_date, doc_no, id}: mrn_costs(); None = not read
    sheet: dict | None = None              # code -> {cost, id}: sheet_costs(); None = not read
    invoices: list[dict] | None = None     # supplier_prices lines
    promises: list[dict] | None = None     # KPI-log promise lines; None when the log is not there
    catalog: list[dict] | None = None      # catalog_items: every item of the item master; None = not read
    po_vendor: dict | None = None          # code -> the vendor of its newest purchase order; None = not read
    price_cuts: list[dict] | None = None   # v_price_change, the cuts: {code, item_name, changed_on, was, now}
    unread: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def kpi_log_file():
    return paths.ops_dir() / "kpi_log.jsonl"


def read_promises() -> list[dict] | None:
    """The promise lines of the KPI log (goods on order), the newest line of each ref; None when the
    log does not exist. A line that cannot be read is passed over."""
    f = kpi_log_file()
    if not f.is_file():
        return None
    newest: dict[str, dict] = {}
    for n, line in enumerate(f.read_text(encoding="utf-8").splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") == "promise":
            newest[str(row.get("ref") or row.get("id") or f"line {n}")] = row
    return list(newest.values())


def code_key(code) -> str:
    """An item code as the portal compares one: upper(sku_code) = upper(code), the ends trimmed."""
    return str(code or "").strip().upper()


def mrn_costs(rows) -> dict[str, dict]:
    """{code: {cost, effective_date, doc_no, id}}: per item code, the receipt the portal itself costs from
    (v_product_economics in scripts/r7d_margin_exvat_migration.sql: mrn_landed_costs first, a landed cost
    above nought, ordered by effective_date desc nulls last, then id desc). The effective date is the
    receipt's date in the ledger (scripts/mrn_dates_migration.sql), so the latest receipt wins; an undated
    row comes after every dated one; on one date, the higher id."""
    best: dict[str, tuple] = {}
    for r in rows or []:
        code, cost = code_key(r.get("code")), fmt.dec(r.get("cost"))
        if not code or cost <= 0:
            continue
        day = fmt.as_date(r.get("effective_date"))
        rid = int(fmt.dec(r.get("id")))
        rank = (day is not None, day or date.min, rid)
        if code not in best or rank > best[code][0]:
            best[code] = (rank, {"cost": cost, "effective_date": day, "doc_no": r.get("doc_no"), "id": rid})
    return {code: value for code, (_rank, value) in best.items()}


def sheet_costs(rows) -> dict[str, dict]:
    """{code: {cost, id}}: the pricing sheet's newest row per item code (MAX(id), docs/CLAUDE.md data rule 2)
    among those with a cost above nought. Used only where a code has no MRN landed cost."""
    best: dict[str, dict] = {}
    for r in rows or []:
        code, cost = code_key(r.get("code")), fmt.dec(r.get("cost"))
        if not code or cost <= 0:
            continue
        rid = int(fmt.dec(r.get("id")))
        if code not in best or rid > best[code]["id"]:
            best[code] = {"cost": cost, "id": rid}
    return best


def read_sheet() -> dict[str, dict]:
    with db.session() as s:
        return sheet_costs(s.q(SHEET_SQL))


def po_vendors(rows) -> dict[str, str]:
    """{code: vendor} of each code's newest purchase order (one row per code: recency 1)."""
    out: dict[str, str] = {}
    for r in rows or []:
        code, vendor = code_key(r.get("code")), " ".join(str(r.get("vendor") or "").split())
        if code and vendor:
            out.setdefault(code, vendor)
    return out


def gather(prefer: str = "auto") -> Inputs:
    """Every read the command makes. The ledger and the snapshot are required; any other read that
    fails is named in `unread` and costs its own part of the screen."""
    led, info = ledger.for_audit(prefer)
    warnings = [led.cache_note] if led.cache_note else []
    snapshot = db.rpc(SNAPSHOT_SQL)
    portal = db.rpc(PORTAL_WAREHOUSE_SQL)
    inp = Inputs(rows=led.rows, ledger_note=info["note"], snapshot=snapshot, portal_warehouses=portal,
                 warnings=warnings)
    parts = (
        ("the portal's figure at cost", "portal_cost", lambda: metrics.stock_cost_summary(db.rpc(metrics.STOCK_SQL))),
        ("the price book (item codes)", "price_book", lambda: db.rpc(PRICE_BOOK_SQL)),
        ("the supplier invoices (lead time)", "invoices", lambda: db.rpc(SUPPLIER_SQL)),
        ("the MRN landed costs (mrn_landed_costs)", "mrn", lambda: mrn_costs(db.rpc(MRN_SQL))),
        ("the pricing sheet (purchase_costs)", "sheet", read_sheet),
        ("the KPI log (goods on order)", "promises", read_promises),
        ("the catalog (catalog_items)", "catalog", lambda: db.rpc(CATALOG_SQL)),
        ("the purchase orders (an item's supplier)", "po_vendor", lambda: po_vendors(db.rpc(PO_VENDOR_SQL))),
        ("the price book's changes (price cuts)", "price_cuts", lambda: db.rpc(PRICE_CUT_SQL)),
    )
    for words, name, read in parts:
        try:
            setattr(inp, name, read())
        except Exception as exc:  # noqa: BLE001  (one part of the screen, never the screen)
            inp.unread.append(f"{words} ({type(exc).__name__})")
    return inp


# ── the item model ────────────────────────────────────────────────────────────

@dataclass
class Item:
    ledger_name: str
    name: str                      # as the stock balance and the price book name it
    code: str | None
    display: bool = False
    on_hand: Decimal = ZERO        # main store, end of D (ledger)
    sell_value: Decimal = ZERO     # the snapshot's selling value in the main store
    cost: Decimal | None = None
    cost_source: str | None = None     # 'mrn' | 'sheet' | 'receipt' | None (COST_SOURCES)
    cost_ref: dict | None = None       # the MRN row costed from: {cost, effective_date, doc_no, id}
    receipts: list = field(default_factory=list)   # [(date, voucher, units, value)] main-store receipts, oldest first
    sales: dict = field(default_factory=dict)      # {date: units sold that day, every warehouse}
    last_sale: date | None = None
    series: list = field(default_factory=list)     # main-store day-end units, from Model.series_start to D
    on_order: Decimal = ZERO
    rank: int | None = None                        # by units over the months before this month (fast movers)
    sold_rank: Decimal = ZERO

    @property
    def first_receipt(self) -> date | None:
        return self.receipts[0][0] if self.receipts else None

    @property
    def last_receipt(self) -> date | None:
        return self.receipts[-1][0] if self.receipts else None


@dataclass
class Rate:
    sold: Decimal
    days_in_stock: int
    rate: Decimal

    @property
    def floored(self) -> bool:
        """Sold, and in stock on fewer than RATE_MIN_DAYS days of the window: the rate divides by
        RATE_MIN_DAYS, so it can understate the demand of an item that was out most of the time."""
        return self.sold > 0 and self.days_in_stock < RATE_MIN_DAYS


def sold_between(it: Item, first: date, last: date) -> Decimal:
    return sum((q for d, q in it.sales.items() if first <= d <= last), ZERO)


def day_value(it: Item, start: date, day: date) -> Decimal:
    i = (day - start).days
    if i < 0:
        return ZERO
    return it.series[min(i, len(it.series) - 1)] if it.series else ZERO


def rate_at(it: Item, start: date, end: date) -> Rate:
    """Units sold in the RATE_DAYS days to `end` ÷ the days of that window the main store held the item
    at the day's end, never fewer than RATE_MIN_DAYS."""
    first = end - timedelta(days=RATE_DAYS - 1)
    sold = max(sold_between(it, first, end), ZERO)
    days = 0
    d = max(first, start)
    while d <= end:
        if day_value(it, start, d) > 0:
            days += 1
        d += timedelta(days=1)
    return Rate(sold=sold, days_in_stock=days, rate=sold / Decimal(max(days, RATE_MIN_DAYS)))


def is_new(it: Item, end: date) -> bool:
    """First received into the main store in the RATE_DAYS days to `end`."""
    first = it.first_receipt
    return first is not None and end - timedelta(days=RATE_DAYS - 1) <= first <= end


def cover_of(units: Decimal, rate: Decimal) -> Decimal | None:
    return units / rate if rate > 0 else None


def available_days(it: Item, start: date, first: date, last: date, rate: Decimal) -> int:
    n = 0
    d = first
    while d <= last:
        v = day_value(it, start, d)
        if v > 0 and v >= rate:
            n += 1
        d += timedelta(days=1)
    return n


def allocate(on_hand: Decimal, receipts: list) -> list[tuple]:
    """Units on hand on the receipts that brought them, newest first: [(receipt day, voucher, units)];
    what no receipt explains is one last chunk (None, None, units)."""
    left = max(on_hand, ZERO)
    out: list[tuple] = []
    for day, voucher, units, _value in sorted(receipts, key=lambda r: (r[0], r[1]), reverse=True):
        if left <= 0:
            break
        take = min(left, units)
        if take > 0:
            out.append((day, voucher, take))
            left -= take
    if left > 0:
        out.append((None, None, left))
    return out


def band_of(days: int | None, limits: tuple) -> int:
    """The band of a count of days: 0 up to limits[0], ..., len(limits) above the last; None is the last band."""
    if days is None:
        return len(limits)
    for i, limit in enumerate(limits):
        if days <= limit:
            return i
    return len(limits)


COST_SOURCES = ("mrn", "sheet", "receipt")


def basis_counts(group) -> dict[str, int]:
    """{mrn, sheet, receipt, none}: how many of these items are costed on each basis."""
    out = dict.fromkeys(COST_SOURCES + ("none",), 0)
    for it in group:
        out[it.cost_source if it.cost_source in COST_SOURCES else "none"] += 1
    return out


def cost_words(counts: dict, none_sell=None) -> tuple[str, str]:
    """(label, basis) for a figure at cost: 'at landed cost' when an MRN or a pricing-sheet cost is in it,
    else 'at cost'; the basis names each part, e.g. 'MRN; the pricing sheet where no MRN: 3 items'."""
    parts = []
    if counts.get("mrn"):
        parts.append("MRN")
    if counts.get("sheet"):
        parts.append(f"the pricing sheet where no MRN: {_plural(counts['sheet'], 'item')}")
    if counts.get("receipt"):
        parts.append(f"the latest receipt rate where neither: {_plural(counts['receipt'], 'item')}")
    if counts.get("none"):
        parts.append(f"{_plural(counts['none'], 'item')} without a cost"
                     + (f", BHD {fmt.bhd(none_sell)} at selling price" if none_sell is not None else ""))
    label = "at landed cost" if counts.get("mrn") or counts.get("sheet") else "at cost"
    return label, "; ".join(parts) or "nothing to value"


def receipt_rate(it: Item) -> Decimal | None:
    """The newest main-store supplier receipt that states a value: its BHD ÷ its units."""
    for _day, _voucher, units, value in reversed(it.receipts):
        if units > 0 and value > 0:
            return value / units
    return None


# ── the lead time ─────────────────────────────────────────────────────────────

def receipt_codes(rows) -> dict[str, dict]:
    """Main-store supplier receipts as {voucher: {date, codes {model code: units}}}: a receipt is set
    beside a supplier invoice by the model codes of its items (items.leading_code)."""
    out: dict[str, dict] = {}
    for number, v in ledger.vouchers(rows, ledger.SUPPLIER_RECEIPT).items():
        if not ledger.is_main(v["wh"]):
            continue
        codes: dict[str, Decimal] = {}
        for item, units in v["lines"].items():
            code = items.leading_code(item)
            codes[code] = codes.get(code, ZERO) + units
        out[number] = {"date": v["date"], "codes": codes}
    return out


def measure_lead_times(invoices: list[dict] | None, receipts: dict[str, dict]) -> list[dict]:
    """Each supplier invoice (number and date: one number can come in two documents) set beside the
    main-store receipt it most likely became: dated on or after it and at most LEAD_MAX_DAYS later,
    holding the same quantity of the most of its models (then the most models, then the earliest),
    and of at least half of them. Oldest receipt first."""
    docs: dict[tuple[str, date], dict[str, Decimal]] = {}
    for r in invoices or []:
        day = fmt.as_date(r.get("invoice_date"))
        number = str(r.get("invoice_no") or "").strip()
        model = str(r.get("model") or "").strip().upper()
        if day is None or not number or not model:
            continue
        doc = docs.setdefault((number, day), {})
        doc[model] = doc.get(model, ZERO) + fmt.dec(r.get("qty"))
    out: list[dict] = []
    for (number, day), models in docs.items():
        best = None
        for voucher, rec in receipts.items():
            lag = (rec["date"] - day).days
            if lag < 0 or lag > LEAD_MAX_DAYS:
                continue
            common = [m for m in models if m in rec["codes"]]
            same = sum(1 for m in common if rec["codes"][m] == models[m])
            key = (same, len(common), -lag, voucher)
            if best is None or key > best[0]:
                best = (key, voucher, rec, lag, same)
        if best is None:
            continue
        _key, voucher, rec, lag, same = best
        if same < 1 or Decimal(same) < LEAD_SAME_SHARE * len(models):
            continue
        out.append({"invoice": number, "invoice_date": day, "receipt": voucher, "receipt_date": rec["date"],
                    "days": lag, "same": same, "models": len(models)})
    return sorted(out, key=lambda x: (x["receipt_date"], x["invoice_date"], x["invoice"]))


def lead_time(measured: list[dict], default: int) -> dict:
    """{days, basis 'measured' | 'config', shown [the newest LEAD_SHOWN], default}."""
    shown = measured[-LEAD_SHOWN:]
    if len(shown) >= LEAD_MIN:
        values = sorted(x["days"] for x in shown)
        mid = len(values) // 2
        med = Decimal(values[mid]) if len(values) % 2 else Decimal(values[mid - 1] + values[mid]) / 2
        return {"days": _whole(med), "basis": "measured", "shown": shown, "default": default}
    return {"days": default, "basis": "config", "shown": shown, "default": default}


# ── goods on order ────────────────────────────────────────────────────────────

CLOSED_STATES = {"closed", "cancelled", "canceled", "done", "received"}


def apply_promises(model_items: list[Item], promises: list[dict] | None) -> dict:
    """Promise lines (goods on order) onto the items: each counts while no main-store receipt of its
    item is dated after it. {logged, counted, unmatched}; the items' on_order is set."""
    if promises is None:
        return {"logged": 0, "counted": 0, "unmatched": 0, "log": False}
    by_code: dict[str, list[Item]] = {}
    by_model: dict[str, list[Item]] = {}
    for it in model_items:
        if it.code:
            by_code.setdefault(fold(it.code), []).append(it)
        by_model.setdefault(items.leading_code(it.name).casefold(), []).append(it)
    logged = counted = unmatched = 0
    for row in promises:
        if str(row.get("state") or "").strip().lower() in CLOSED_STATES:
            continue
        logged += 1
        code = fold(row.get("item_code"))
        found = by_code.get(code) or (by_model.get(code) if len(by_model.get(code) or []) == 1 else None)
        if not code or not found or len(found) != 1:
            unmatched += 1
            continue
        it = found[0]
        since = fmt.as_date(row.get("event_date")) or fmt.as_date(row.get("logged_at"))
        if since is not None and it.last_receipt is not None and it.last_receipt > since:
            continue                       # a receipt dated after the promise: it has arrived
        qty = fmt.dec(row.get("qty"))
        if qty > 0:
            it.on_order += qty
            counted += 1
    return {"logged": logged, "counted": counted, "unmatched": unmatched, "log": True}


# ── vans and outlets (later) ──────────────────────────────────────────────────

def vans_beside(model) -> list[dict]:
    """The stock held beside the main store, one column each: vans and the two outlets. Empty until
    /yq-moves pairs the transfers (an issue and its receipt are two vouchers with independent numbers;
    only paired can the units between them be told apart from the vans' own). Each entry will be
    {name, items, units, cost_bhd}; the headline prints them on the line it now spends on saying so."""
    return []


# ── building it all ───────────────────────────────────────────────────────────

@dataclass
class Model:
    d: date
    stock_as_of: date | None
    cfg: Settings
    items: list[Item]
    series_start: date
    facts: dict
    notes: list[str]
    warnings: list[str]
    flows: stockflow.Flows | None = None     # the sellable pool, the damage store and the sellers of every item
    ledger_note: str = ""                    # where the stock ledger was read from, and why (the workbook's Notes)


PART_WORDS = {"damage": "the damage figures", "review": "the ageing review", "plan": "the order plan"}


def _part(model: Model, key: str, work) -> dict:
    """One part of the facts (damage, review, plan): a part that fails stands aside, named among the
    warnings, so the rest of the screen still prints (RAISE_PARTS: it raises instead)."""
    try:
        return work()
    except Exception as exc:  # noqa: BLE001
        if RAISE_PARTS:
            raise
        model.warnings.append(f"{PART_WORDS[key]} could not be worked out: {type(exc).__name__}: {exc}")
        return {"error": type(exc).__name__}


def _code_and_name(ledger_name: str, book: dict, stock_bridge: items.Bridge) -> tuple[str | None, str]:
    hit = book.get(fold(ledger_name))
    if hit is not None:
        return hit
    known = stock_bridge.get(ledger_name)
    if known is not None:
        rest = " ".join(ledger_name.split())[len(" ".join(str(known).split())):].strip()
        return (rest or None), known
    return None, ledger_name


def build(inp: Inputs, cfg: Settings) -> Model:
    rows = inp.rows
    d = ledger.last_date(rows)
    if d is None:
        raise StockInput("No stock ledger is loaded: there is no stock to plan.")
    notes: list[str] = []
    warnings = list(inp.warnings)

    # the price book: the ledger names an item "<stock name> <code>"
    book: dict[str, tuple[str, str]] = {}
    for r in inp.price_book or []:
        code, name = str(r.get("sku_code") or "").strip(), str(r.get("item_name") or "").strip()
        if code and name:
            book.setdefault(fold(f"{name} {code}"), (code, name))
    main_snap = [r for r in inp.snapshot if ledger.is_main(r.get("warehouse_name"))]
    stock_as_of = max((fmt.as_date(r.get("as_of_date")) for r in inp.snapshot if r.get("as_of_date")), default=None)
    stock_bridge = items.Bridge([r.get("item_name") for r in main_snap] + [name for _c, name in book.values()])
    acc = items.accessory_items(rows, bridge=stock_bridge)

    model_items: dict[str, Item] = {}
    for ledger_name in sorted(acc):
        code, name = _code_and_name(ledger_name, book, stock_bridge)
        it = Item(ledger_name=ledger_name, name=name, code=code)
        it.display = bool(cfg.display) and (fold(code) in cfg.display or fold(name) in cfg.display
                                            or fold(ledger_name) in cfg.display)
        model_items[ledger_name] = it

    # one pass: sales in every warehouse, receipts into the main store
    main_names: dict[str, bool] = {}
    receipts: dict[str, dict[tuple, list]] = {}
    for r in rows:
        it = model_items.get(r["item"])
        if it is None:
            continue
        if r["vtype"] == ledger.SALE:
            q = r["qty_out"] - r["qty_in"]
            it.sales[r["date"]] = it.sales.get(r["date"], ZERO) + q
            if r["qty_out"] > 0 and (it.last_sale is None or r["date"] > it.last_sale):
                it.last_sale = r["date"]
        elif r["vtype"] == ledger.SUPPLIER_RECEIPT and r["qty_in"] > 0:
            inside = main_names.get(r["wh"])
            if inside is None:
                inside = main_names[r["wh"]] = ledger.is_main(r["wh"])
            if inside:
                slot = receipts.setdefault(it.ledger_name, {}).setdefault((r["date"], r["voucher"]), [ZERO, ZERO])
                slot[0] += r["qty_in"]
                slot[1] += r["value_in"]
    for name, by_voucher in receipts.items():
        model_items[name].receipts = [(day, voucher, u, v) for (day, voucher), (u, v) in sorted(by_voucher.items())]

    # main-store day-end units from the series start (the RATE_DAYS before last month's end) to D
    m1 = month_start(d)
    e = m1 - timedelta(days=1)
    start = e - timedelta(days=RATE_DAYS - 1)
    series = ledger.day_end_balances(rows, list(model_items), None, start, d) if model_items else {}
    for name, it in model_items.items():
        it.series = series.get(name, [])
        it.on_hand = it.series[-1] if it.series else ZERO

    # the snapshot's selling value, and the ledger set beside it item by item
    by_name: dict[str, Item] = {}
    for it in model_items.values():
        by_name.setdefault(fold(it.name), it)
    snap_units: dict[str, Decimal] = {}
    unknown_snap = []
    for r in main_snap:
        key = fold(r.get("item_name"))
        snap_units[key] = snap_units.get(key, ZERO) + fmt.dec(r.get("net_qty"))
        it = by_name.get(key)
        if it is None:
            unknown_snap.append(str(r.get("item_name") or ""))
        else:
            it.sell_value += fmt.dec(r.get("value_bhd"))
    compare_day = min(d, stock_as_of) if stock_as_of else d
    agree, differ = 0, []
    for it in model_items.values():
        snap = snap_units.get(fold(it.name), ZERO)
        mine = day_value(it, start, compare_day)
        if snap == 0 and mine == 0:
            continue
        if snap == mine:
            agree += 1
        else:
            differ.append({"item": it.name, "snapshot": snap, "ledger": mine})
    negative = [it.name for it in model_items.values() if it.on_hand < 0]

    # costs: the code's MRN landed cost, else the pricing sheet, else the latest receipt rate (the owner, 15-Oct-2026)
    mrn, sheet = inp.mrn or {}, inp.sheet or {}
    for it in model_items.values():
        key = code_key(it.code)
        if key and key in mrn:
            it.cost, it.cost_source, it.cost_ref = mrn[key]["cost"], "mrn", mrn[key]
        elif key and key in sheet:
            it.cost, it.cost_source = sheet[key]["cost"], "sheet"
        else:
            rr = receipt_rate(it)
            if rr is not None:
                it.cost, it.cost_source = rr, "receipt"

    plannable = [it for it in model_items.values() if not it.display]
    # the stores beside the main store, the damage store and the sellers, read once: the order rule (a longer history
    # for an item out of stock most of the window) and the parts after the build read them
    fl = stockflow.flows(rows, model_items, cfg.damage, cfg.not_counted, d)
    # each item's supplier first: its lead time and its rhythm are the supplier's (the owner, 20-Oct-2026)
    supplier_of = stock_plan.suppliers_of(plannable, cfg, inp.catalog, inp.po_vendor)
    measured = measure_lead_times(inp.invoices, receipt_codes(rows))
    lead = lead_time(measured, cfg.lead_default)
    vendors = {str(r.get("invoice_no") or "").strip(): r.get("vendor") for r in inp.invoices or [] if r.get("vendor")}
    leads = stock_season.lead_by_supplier(measured, vendors, cfg.supplier_lead, lead, cfg.lead_default,
                                          cfg.supplier_aliases)

    def lead_of(supplier) -> int:
        return (leads.get(supplier) or leads[stock_season.OTHER])["days"]

    # goods on order: the KPI log's promise lines, each item's later supplier receipts paying the oldest line first
    on_order = stock_orders.fifo(plannable, inp.promises, d, lead_of)
    for it in plannable:
        it.on_order = on_order["on_order"].get(it.ledger_name, ZERO)
    if inp.mrn is None:
        notes.append("The MRN landed costs could not be read: the pricing sheet stands in where it has the item, else "
                     "the latest receipt rate.")
    if inp.sheet is None:
        notes.append("The pricing sheet could not be read: an item with no MRN landed cost takes the latest receipt "
                     "rate.")

    # fast movers: units over the RANK_MONTHS full months before the month
    def ranking(month_first: date) -> tuple[list[Item], date, date, bool]:
        first, last = month_start(month_first, RANK_MONTHS), month_first - timedelta(days=1)
        scored = []
        for it in plannable:
            units = sold_between(it, first, last)
            if units > 0:
                scored.append((units, it))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        partial = (ledger.first_date(rows) or first) > first
        return [it for _u, it in scored], first, last, partial

    ranked_now, rank_first, rank_last, rank_partial = ranking(m1)
    for n, it in enumerate(ranked_now, 1):
        it.rank, it.sold_rank = n, sold_between(it, rank_first, rank_last)
    fast_now = ranked_now[:cfg.fast_n]
    ranked_last, _f, _l, last_partial = ranking(month_start(d, 1))
    fast_last = ranked_last[:cfg.fast_n]

    rates_d = {it.ledger_name: rate_at(it, start, d) for it in plannable}
    rates_e = {it.ledger_name: rate_at(it, start, e) for it in plannable}

    def availability(fast: list[Item], first: date, last: date, rates: dict) -> dict:
        days = (last - first).days + 1

        def tally(group: list[Item]) -> tuple[int, int, list[Item]]:
            have = total = 0
            out_all: list[Item] = []
            for it in group:
                n = available_days(it, start, first, last, rates[it.ledger_name].rate)
                have += n
                total += days
                if n == 0:
                    out_all.append(it)
            return have, total, out_all

        have, total, out_all = tally(fast)
        wide = [it for it in plannable if rates[it.ledger_name].rate > 0]
        w_have, w_total, _ = tally(wide)
        return {"first": first, "last": last, "days": days, "fast": len(fast), "available": have, "sku_days": total,
                "out_all": out_all, "wide_items": len(wide), "wide_available": w_have, "wide_sku_days": w_total}

    avail_now = availability(fast_now, m1, d, rates_d)
    avail_last = availability(fast_last, month_start(d, 1), e, rates_e)

    # the season (the owner, 20-Oct-2026): each month's lift is last year's same month against today's pace,
    # company-wide; a month whose fast movers were out of stock a fifth of the time or more is named (held down)
    lifts = stock_season.measure_lifts(plannable, d, ledger.first_date(rows), cfg.season_lift)
    held_down = []
    for m, lf in sorted(lifts.items()):
        if lf["basis"] == "last year":
            first = date(lf["year"], m, 1)
            share = stock_season.availability(fast_now, fl.items, first, min(month_start(first, -1) - timedelta(days=1), d))
            if share is not None and share < stock_season.AVAILABILITY_WARN:
                held_down.append(m)
    season_on = any(lf["lift"] != stock_season.NEUTRAL for lf in lifts.values())

    # the order rule of each supplier: its lead time + its rhythm + the KPI band (low to join, high to top up)
    rules: dict[str, dict] = {}

    def rule_of(supplier: str) -> dict:
        if supplier not in rules:
            row = leads.get(supplier) or leads[stock_season.OTHER]
            every = int(cfg.supplier_every.get(supplier, cfg.every_days))
            # a window never reaches past a year (the workbook caps it the same way)
            rules[supplier] = {"lead": row["days"], "lead_basis": row["basis"], "every": every, "low": cfg.low,
                               "high": cfg.high, "trig_days": min(row["days"] + every + cfg.low, WINDOW_MAX_DAYS),
                               "fill_days": min(row["days"] + every + cfg.high, WINDOW_MAX_DAYS)}
        return rules[supplier]

    default_rule = rule_of(stock_season.OTHER)
    trigger, fill = default_rule["trig_days"], default_rule["fill_days"]
    for supplier, _basis in supplier_of.values():       # every supplier has its rule (an item added in the file too)
        rule_of(supplier)
    order: list[dict] = []
    for it in plannable:
        r = rates_d[it.ledger_name]
        if r.rate <= 0:
            continue
        used, basis = r.rate, f"{RATE_DAYS} days"
        if r.floored:                                  # out of stock most of the window: a longer history
            longer = stock_season.long_rate(it, fl.get(it.ledger_name), d)
            if longer is not None and longer["rate"] > 0:
                used, basis = longer["rate"], f"longer history from {longer['first'].isoformat()}"
        supplier = supplier_of.get(it.ledger_name, (stock_plan.OTHER, "none"))[0]
        rule = rule_of(supplier)
        have = max(it.on_hand, ZERO) + it.on_order
        # the workbook works the same rule out again from the rate to six decimals (stock_plan's one rounding rule),
        # so the screen and the file agree to the unit at a whole-number boundary
        rate6 = stock_plan.round6(used)
        if not stock_season.joins(have, rate6, d, rule["trig_days"], lifts):
            continue
        qty = stock_season.suggested(have, rate6, d, rule["fill_days"], lifts)
        pack = cfg.pack_sizes.get(code_key(it.code))
        if pack and pack > 1 and qty > 0:                # whole cartons
            qty = (qty / pack).to_integral_value(rounding=ROUND_CEILING) * pack
        order.append({"item": it, "rate": r, "cover": have / used, "qty": qty, "rate6": rate6, "rate_basis": basis,
                      "supplier": supplier, "position": have, "trig_days": rule["trig_days"],
                      "fill_days": rule["fill_days"], "pack": pack,
                      "cost_bhd": fmt.q3(qty * it.cost) if it.cost is not None else None})
    order.sort(key=lambda x: (x["cover"], -(x["cost_bhd"] or ZERO), x["item"].name))

    # KPI 1: cover, now and on the last day of last month (today's costs)
    def cover_kpi(end: date, rates: dict, at_end: bool) -> dict:
        num = den = above = ZERO
        new_items = uncosted = 0
        for it in plannable:
            units = max(day_value(it, start, end), ZERO) if at_end else max(it.on_hand, ZERO)
            if is_new(it, end):
                new_items += 1 if units > 0 else 0
                continue
            if it.cost is None:
                uncosted += 1 if units > 0 else 0
                continue
            r = rates[it.ledger_name].rate
            value = units * it.cost
            num += value
            den += r * it.cost
            c = cover_of(units, r)
            if units > 0 and (c is None or c > cfg.high):
                above += value
        return {"days": (num / den) if den > 0 else None, "value": num, "above_value": above,
                "new_items": new_items, "uncosted_items": uncosted}

    kpi_now = cover_kpi(d, rates_d, at_end=False)
    kpi_last = cover_kpi(e, rates_e, at_end=True)

    # cover bands, by count and value, new arrivals apart
    band_names = (f"under {cfg.low} days", f"{cfg.low} to {cfg.high} days", f"over {cfg.high} to {COVER_LIMITS[0]}",
                  f"over {COVER_LIMITS[0]} to {COVER_LIMITS[1]}", f"over {COVER_LIMITS[1]} to {COVER_LIMITS[2]}",
                  f"over {COVER_LIMITS[2]} days", f"no sale in {RATE_DAYS} days", "new arrivals (apart)")
    bands = [{"band": b, "items": 0, "units": ZERO, "value": ZERO, "uncosted": 0} for b in band_names]
    for it in plannable:
        if it.on_hand <= 0:
            continue
        r = rates_d[it.ledger_name].rate
        c = cover_of(it.on_hand, r)
        if is_new(it, d):
            i = 7
        elif c is None:
            i = 6
        elif c < cfg.low:
            i = 0
        elif c <= cfg.high:
            i = 1
        else:
            i = 2 + next((n for n, limit in enumerate(COVER_LIMITS) if c <= limit), len(COVER_LIMITS))
        b = bands[i]
        b["items"] += 1
        b["units"] += it.on_hand
        if it.cost is None:
            b["uncosted"] += 1
        else:
            b["value"] += it.on_hand * it.cost

    # the last supplier receipt, in months of sales of its items at today's rates
    last_mrn = None
    rec = receipt_codes(rows)
    if rec:
        voucher = max(rec, key=lambda v: (rec[v]["date"], v))
        got = [(it, units) for it in plannable for (day, vno, units, _v) in it.receipts if vno == voucher]
        daily = sum((rates_d[it.ledger_name].rate for it, _u in got), ZERO)
        units = sum((u for _it, u in got), ZERO)
        if got and daily > 0:
            last_mrn = {"voucher": voucher, "date": rec[voucher]["date"], "units": units,
                        "months": units / daily / Decimal(30)}

    # ageing
    held = [it for it in model_items.values() if it.on_hand > 0]
    display_held = [it for it in held if it.display]
    grid_cells = [[{"units": ZERO, "value": ZERO} for _ in SALE_BANDS] for _ in AGE_BANDS]
    age_rows = [{"band": AGE_BANDS[i], "items": set(), "units": ZERO, "value": ZERO, "uncosted_units": ZERO,
                 "action": AGE_ACTIONS[i]} for i in range(len(AGE_BANDS))]
    old_items: list[dict] = []
    for it in held:
        if it.display:
            continue
        since = (d - it.last_sale).days if it.last_sale else None
        s = band_of(since, SALE_LIMITS)
        over_180 = ZERO
        for day, _voucher, units in allocate(it.on_hand, it.receipts):
            a = band_of((d - day).days, AGE_LIMITS) if day is not None else 4
            row = age_rows[a]
            row["items"].add(it.ledger_name)
            row["units"] += units
            cell = grid_cells[a][s]
            cell["units"] += units
            if it.cost is None:
                row["uncosted_units"] += units
            else:
                row["value"] += units * it.cost
                cell["value"] += units * it.cost
            if a in (2, 3):
                over_180 += units
        if over_180 > 0:
            old_items.append({"item": it, "units": over_180,
                              "value": over_180 * it.cost if it.cost is not None else None,
                              "since": since})
    old_items.sort(key=lambda x: (-(x["value"] or ZERO), -x["units"], x["item"].name))
    age_total_units = sum((r["units"] for r in age_rows), ZERO)
    age_total_value = sum((r["value"] for r in age_rows), ZERO)

    # the headline: the portal's own read of the main store, and at cost on both bases
    portal_main = [r for r in inp.portal_warehouses if ledger.is_main(r.get("warehouse_name"))]
    other_wh = [r for r in inp.portal_warehouses if not ledger.is_main(r.get("warehouse_name"))]
    head = {"items": sum(int(fmt.dec(r.get("items"))) for r in portal_main),
            "units": amount(sum((fmt.dec(r.get("qty")) for r in portal_main), ZERO)),
            "sell_bhd": fmt.q3(sum((fmt.dec(r.get("value_bhd")) for r in portal_main), ZERO)),
            "found": bool(portal_main), "other_warehouses": len(other_wh)}
    mine_cost = sum((it.on_hand * it.cost for it in held if it.cost is not None), ZERO)
    mine_uncosted = [it for it in held if it.cost is None]
    head["cost_bhd"] = fmt.q3(mine_cost)
    held_basis = basis_counts(held)
    head["cost_mrn_items"], head["cost_sheet_items"] = held_basis["mrn"], held_basis["sheet"]
    head["cost_receipt_items"] = held_basis["receipt"]
    head["uncosted_items"] = len(mine_uncosted)
    head["uncosted_sell_bhd"] = fmt.q3(sum((it.sell_value for it in mine_uncosted), ZERO))
    pc = inp.portal_cost
    head["portal_cost_bhd"] = fmt.q3(pc.get("cost_value_bhd")) if pc else None
    head["portal_uncosted_items"] = int(pc.get("items_uncosted") or 0) if pc else None
    head["display_items"] = len(display_held)
    head["display_units"] = sum((it.on_hand for it in display_held), ZERO)
    head["vans"] = vans_beside(None)

    facts = {
        "data_date": d, "stock_as_of": stock_as_of, "headline": head,
        "ledger_check": {"agree": agree, "differ": differ, "compared_on": compare_day,
                         "unknown_snapshot_items": unknown_snap, "negative": negative},
        "kpi_cover": {"now": kpi_now, "last_month_end": e, "last": kpi_last, "low": cfg.low, "high": cfg.high},
        "bands": bands, "last_receipt": last_mrn,
        "lead": lead, "trigger_days": trigger, "fill_days": fill, "order": order, "on_order": on_order,
        "on_the_way": on_order, "leads": leads, "supplier_rules": rules,
        "season": {"lifts": lifts, "warn": held_down, "applied": season_on},
        "fast": {"n": cfg.fast_n, "rank_first": rank_first, "rank_last": rank_last, "partial": rank_partial,
                 "last_partial": last_partial, "now": avail_now, "last": avail_last, "list": fast_now,
                 "rates": rates_d},
        "ageing": {"rows": age_rows, "cells": grid_cells, "units": age_total_units, "value": age_total_value,
                   "display_items": len(display_held), "display_units": head["display_units"],
                   "main_units": sum((it.on_hand for it in held), ZERO), "oldest": old_items},
        "defaults_used": list(cfg.defaults_used), "unread": list(inp.unread),
        "cost_basis": basis_counts(model_items.values()),
    }
    model = Model(d=d, stock_as_of=stock_as_of, cfg=cfg, items=list(model_items.values()), series_start=start,
                  facts=facts, notes=notes, warnings=warnings, flows=fl, ledger_note=inp.ledger_note)
    facts["damage"] = _part(model, "damage", lambda: stock_damage.damage_facts(model, fl))
    facts["review"] = _part(model, "review", lambda: stock_review.review_facts(model, fl, inp.price_cuts))
    facts["plan"] = _part(model, "plan", lambda: stock_plan.plan_facts(model, fl, inp.catalog, inp.po_vendor))
    return model


# ── the screens ───────────────────────────────────────────────────────────────

def _cost_cell(x) -> str:
    return "n/a" if x is None else fmt.bhd(x)


def _date_short(x, ref: date) -> str:
    dd = fmt.as_date(x)
    if dd is None:
        return "never"
    return dd.strftime("%d %b") if dd.year == ref.year else dd.strftime("%d %b %Y")


def _defaults_line(model: Model) -> list[str]:
    used = model.facts["defaults_used"]
    if not used:
        return []
    names = " and ".join(f"'{k}'" for k in used)
    return [f"(config {names} not set in config.json: the plan's default is used)"]


def _footer(model: Model, inp_note: str) -> list[str]:
    lines = [""] + list(BASIS_LINES) + [inp_note]
    lines += _defaults_line(model)
    for part in model.facts["unread"]:
        lines.append(f"Not read this time: {part}.")
    lines += model.notes
    return lines


def _order_heading(model: Model) -> str:
    oo = model.facts["on_order"]
    if oo["counted"]:
        return f"goods on order deducted ({_plural(oo['counted'], 'promise line')} of the KPI log)"
    return "goods on order not deducted"


def _release2(model: Model) -> bool:
    """The rhythm or the season is in play (release 2): the screens then say how the rule reads."""
    f = model.facts
    return model.cfg.every_days > 0 or bool(model.cfg.supplier_every) or bool((f.get("season") or {}).get("applied"))


def _rule_line(model: Model) -> list[str]:
    """One line on how an item joins the order list, once the rhythm or the season is in play."""
    if not _release2(model):
        return []
    f = model.facts
    rule = f["supplier_rules"][stock_season.OTHER]
    season = ", with the season," if f["season"]["applied"] else ""
    return [f"  Rule: an item is listed when its demand{season} reaches its stock and goods on the way within the lead "
            f"time + {rule['every']} days to the next order + {rule['low']} = {rule['trig_days']} days, and ordered up "
            f"to {rule['fill_days']} days (each supplier's rule on /yq-stock reorder)"]


def _month_short(m: int) -> str:
    return date(2000, m, 1).strftime("%b")


def season_lines(model: Model) -> list[str]:
    """SEASON: each month's lift (last year's month against the last 90 days' pace), when the season is in play."""
    s = model.facts.get("season") or {}
    if not s.get("applied"):
        return []
    d = model.d
    months = [((d.month - 1 + k) % 12) + 1 for k in range(1, 13)]          # the twelve months after D's month
    cells = []
    for m in months:
        lf = s["lifts"][m]
        mark = "" if lf["basis"] == "last year" else (" (set)" if lf["basis"] == "config" else " (no data)")
        cells.append(f"{_month_short(m)} {lf['lift']:.2f}{mark}")
    lines = ["", f"SEASON  each month's lift: the same month a year back against the pace of the last {RATE_DAYS} days, "
                 "company-wide; the order list counts each day's demand at its month's lift",
             "  " + " · ".join(cells[:6]), "  " + " · ".join(cells[6:])]
    if s.get("warn"):
        names = ", ".join(_month_short(m) for m in s["warn"])
        lines.append(f"  Held down by stock-outs (the fast movers were out a fifth of the time or more): {names}; "
                     "their lifts may be low")
    lines.append("  (config 'stock.season_lift' sets a month's lift by hand)")
    return lines


def supplier_rule_lines(model: Model) -> list[str]:
    """SUPPLIERS: each supplier's lead time, rhythm and the days it joins and tops up at."""
    if not _release2(model):
        return []
    rules = model.facts.get("supplier_rules") or {}
    named = [(s, r) for s, r in sorted(rules.items()) if s != stock_season.OTHER]
    if not named:
        named = [("any supplier", rules[stock_season.OTHER])]
    words = {"measured": "measured", "config": "set in config", "overall": "every supplier's measured",
             "setting": "the setting"}
    rows = [[s, str(r["lead"]), words.get(r["lead_basis"], r["lead_basis"]), str(r["every"]), str(r["trig_days"]),
             str(r["fill_days"])] for s, r in named]
    return ["", "SUPPLIERS  each supplier's rule (lead time + the days to its next order + the KPI band)"] + \
        _indent(fmt.grid(["Supplier", "Lead time", "Basis", "Order every", "Join below", "Fill to"], rows, "lrlrrr"))


def on_the_way_lines(model: Model, only: str | None = None) -> list[str]:
    """ON THE WAY: the logged orders whose goods are not all received (one item when `only` names its ledger name)."""
    ow = model.facts.get("on_the_way") or {}
    lines_due = [x for x in ow.get("lines") or [] if x.get("due", ZERO) > 0
                 and (only is None or x.get("ledger_name") == only)]
    if not lines_due:
        return []
    rows = [[x.get("supplier") or "", _date_short(x["date"], model.d), x.get("code") or "", short(x.get("item_name") or "", 30),
             fmt.units(x["qty"]), fmt.units(x["received"]), fmt.units(x["due"]), _date_short(x["expected_by"], model.d),
             "late, to check" if x.get("late") else ("dated ahead" if x.get("future") else "")]
            for x in lines_due]
    head = ["", f"ON THE WAY  {_plural(len(rows), 'logged line')} with goods still due (deducted from the order list)"]
    return head + _indent(fmt.grid(["Supplier", "Ordered on", "Code", "Item", "Ordered", "Received", "Due", "Expected by",
                                    ""], rows, "llllrrrll"))


def _rate_cell(x: dict) -> str:
    """An order row's rate a day: the one its quantity rests on (a longer history marked ~, the 30-day floor *)."""
    if str(x.get("rate_basis") or "").startswith("longer"):
        return rate_text(x["rate6"]) + "~"
    return rate_text(x["rate"].rate, x["rate"].floored)


LONGER_NOTE = (f"  ~ out of stock most of the last {RATE_DAYS} days: the rate of a longer history (back to the first "
               f"window holding {RATE_MIN_DAYS} days in stock, up to a year)")


def _lead_words(model: Model) -> str:
    lead = model.facts["lead"]
    if lead["basis"] == "measured":
        return (f"lead time {lead['days']} days, measured on the last {_plural(len(lead['shown']), 'supplier invoice')} "
                f"(config {lead['default']})")
    return f"lead time {lead['days']} days (config: too few supplier invoices matched to a receipt to measure it)"


def headline_lines(model: Model, ledger_note: str) -> list[str]:
    f, d = model.facts, model.d
    h = f["headline"]
    lines = [f"STOCK  main store, {config.get('warehouses.main')}: stock as on {fmt.day(model.stock_as_of)}, "
             f"ledger to {fmt.day(d)}"]
    if h["found"]:
        lines.append(f"  {_plural(h['items'], 'item')}, {fmt.units(h['units'])} units, BHD {fmt.bhd(h['sell_bhd'])} "
                     "at selling price (the portal's figures)")
    else:
        lines.append("  The portal holds no stock of the main store in its newest snapshot.")
    label, basis = cost_words({"mrn": h["cost_mrn_items"], "sheet": h["cost_sheet_items"],
                               "receipt": h["cost_receipt_items"], "none": h["uncosted_items"]}, h["uncosted_sell_bhd"])
    lines.append(f"  {label[0].upper() + label[1:]} BHD {fmt.bhd(h['cost_bhd'])} ({basis})")
    if h["portal_cost_bhd"] is None:
        lines.append("  The portal's figure at cost was not read this time.")
    elif h["portal_cost_bhd"] == h["cost_bhd"]:
        lines.append("  The portal's figure at cost is the same.")
    else:
        extra = (f"; {_plural(h['portal_uncosted_items'], 'item')} without a cost there" if h["portal_uncosted_items"]
                 else "")
        lines.append(f"  The portal at cost: BHD {fmt.bhd(h['portal_cost_bhd'])} (Focus average cost, else the latest "
                     f"landed cost{extra})")
    if h["other_warehouses"]:
        lines.append(f"  The portal's snapshot holds {_plural(h['other_warehouses'], 'other warehouse')} too: its "
                     "figure at cost counts them, the line above it does not.")
    chk = f["ledger_check"]
    if chk["differ"]:
        names = ", ".join(short(x["item"], 28) for x in chk["differ"][:3])
        lines.append(f"  The ledger differs from the stock balance on {_plural(len(chk['differ']), 'item')}, to check: "
                     f"{names}" + (" ..." if len(chk["differ"]) > 3 else ""))
    else:
        lines.append(f"  The ledger agrees with the stock balance on all {_plural(chk['agree'], 'item')} "
                     f"({fmt.day(chk['compared_on'])}).")
    if chk["unknown_snapshot_items"]:
        lines.append(f"  {_plural(len(chk['unknown_snapshot_items']), 'item')} of the stock balance match no accessory "
                     "of the ledger, to check.")
    if chk["negative"]:
        lines.append(f"  {_plural(len(chk['negative']), 'item')} stand below nought in the ledger, to check.")
    if h["display_items"]:
        lines.append(f"  Display items: {_plural(h['display_items'], 'item')}, {fmt.units(h['display_units'])} units, "
                     "left out of cover, the order list and the ageing.")
    lines.append("  Vans and outlets: not counted here yet (they come with /yq-moves, once transfers are paired).")

    k = f["kpi_cover"]
    now, last = k["now"], k["last"]
    valued = [it for it in model.items if not it.display and not is_new(it, d)]
    label, basis = cost_words(basis_counts(valued))
    lines += ["", f"COVER  KPI 1 band {k['low']} to {k['high']} days, {label} ({basis}); new arrivals apart"]
    if now["days"] is None:
        lines.append("  No item with a cost sold in the window: cover cannot be worked out.")
    else:
        was = ""
        if last["days"] is not None:
            delta = _whole(now["days"]) - _whole(last["days"])
            was = (f" (on {fmt.day(k['last_month_end'])}: {days_text(last['days'])} days, "
                   f"{'+' if delta > 0 else ''}{delta})")
        lines.append(f"  Aggregate cover {days_text(now['days'])} days{was}; "
                     f"{fmt.pct(now['above_value'], now['value'])} of the value is above {k['high']} days")
    table = [[b["band"], fmt.units(b["items"]), fmt.units(b["units"]),
              fmt.bhd(b["value"]) + (f" (+{b['uncosted']} uncosted)" if b["uncosted"] else "")]
             for b in f["bands"] if b["items"]]
    if table:
        lines += _indent(fmt.grid(["Cover", "Items", "Units", "At cost"], table))
    lm = f["last_receipt"]
    if lm:
        lines.append(f"  The last supplier receipt ({lm['voucher']}, {fmt.day(lm['date'])}) holds "
                     f"{lm['months'].quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)} months of sales of its items "
                     "at today's rates.")

    order = f["order"]
    lines += ["", f"ORDER LIST  {_lead_words(model)}; {_order_heading(model)}"] + _rule_line(model)
    if not order:
        lines.append(f"  No item is below {f['trigger_days']} days of cover.")
    else:
        units = sum((x["qty"] for x in order), ZERO)
        value = sum((x["cost_bhd"] for x in order if x["cost_bhd"] is not None), ZERO)
        label, basis = cost_words(basis_counts(x["item"] for x in order))
        lines.append(f"  {_plural(len(order), 'item')} below {f['trigger_days']} days of cover; ordered up to "
                     f"{f['fill_days']} days: {fmt.units(units)} units, BHD {fmt.bhd(value)} {label} ({basis})")
        top = order[:HEADLINE_ROWS]
        rows = [[short(x["item"].name, 36), x["item"].code or "", fmt.units(x["item"].on_hand),
                 _rate_cell(x), days_text(x["cover"]), fmt.units(x["qty"]),
                 _cost_cell(x["cost_bhd"])] for x in top]
        lines += _indent(fmt.grid(["Item", "Code", "On hand", "A day", "Cover", "Order", "At cost"], rows, "llrrrrr"))
        if any(x["rate"].floored and _rate_cell(x).endswith("*") for x in top):
            lines.append(FLOOR_NOTE)
        if any(_rate_cell(x).endswith("~") for x in top):
            lines.append(LONGER_NOTE)
        if len(order) > HEADLINE_ROWS:
            lines.append(f"  (+{len(order) - HEADLINE_ROWS} more: /yq-stock reorder)")

    fa = f["fast"]
    lines += ["", f"FAST MOVERS  the {fa['n']} best sellers by units, {month_name(fa['rank_first'])} to "
                  f"{month_name(fa['rank_last'])}"]
    for label, a in (("This month so far", fa["now"]),
                     (f"{month_name(fa['last']['first'])} (that month's fast movers)", fa["last"])):
        lines.append(f"  {label}: {fmt.pct(a['available'], a['sku_days'])} of SKU-days available "
                     f"(every item that sold: {fmt.pct(a['wide_available'], a['wide_sku_days'])})")
    out_all = fa["last"]["out_all"]
    if out_all:
        lines.append(f"  Out all of {month_name(fa['last']['first'])}: " + ", ".join(short(it.name, 30) for it in out_all[:5])
                     + (f" (+{len(out_all) - 5} more)" if len(out_all) > 5 else ""))
    out_now = fa["now"]["out_all"]
    if out_now:
        lines.append("  Out every day this month: " + ", ".join(short(it.name, 30) for it in out_now[:5])
                     + (f" (+{len(out_now) - 5} more)" if len(out_now) > 5 else ""))
    if fa["partial"]:
        lines.append("  (the ledger starts inside that year: the ranking counts the months it holds)")

    ag = f["ageing"]
    over = ag["rows"][2]["value"] + ag["rows"][3]["value"]
    lines += ["", f"AGEING  {fmt.pct(over, ag['value'])} of the value at cost has been in stock over 180 days "
                  "(units on their newest supplier receipts). Full table: /yq-stock ageing"]
    lines += _part_lines(model, "review", stock_review.headline_lines)
    damage = _part_lines(model, "damage", stock_damage.headline_lines)
    if damage:
        lines += [""] + damage
    lines += _footer(model, ledger_note)
    return lines


def _part_lines(model: Model, key: str, words) -> list[str]:
    """A part's lines (words(facts)), or one line when that part could not be worked out this time."""
    part = model.facts.get(key) or {}
    if "error" in part:
        return [f"  ({PART_WORDS[key][0].upper() + PART_WORDS[key][1:]} could not be worked out this time: "
                f"{part['error']}.)"]
    return list(words(part))


def reorder_lines(model: Model, ledger_note: str) -> list[str]:
    f, d = model.facts, model.d
    lead = f["lead"]
    every = f["supplier_rules"][stock_season.OTHER]["every"] if f.get("supplier_rules") else 0
    rhythm = f" + {every} days to the next order" if every else ""
    season = " (each day's demand at its month's season lift)" if (f.get("season") or {}).get("applied") else ""
    lines = [f"ORDER LIST  main store, ledger to {fmt.day(d)}: {_lead_words(model)}; {_order_heading(model)}",
             f"  An item that sold in the last {RATE_DAYS} days is listed when its cover is below the lead time"
             f"{rhythm} + {model.cfg.low} = {f['trigger_days']} days; the order brings it to the lead time{rhythm} + "
             f"{model.cfg.high} = {f['fill_days']} days{season}. No purchase order is drafted."]
    oo = f["on_order"]
    if oo["unmatched"]:
        lines.append(f"  {_plural(oo['unmatched'], 'promise line')} of the KPI log "
                     f"{'matches' if oo['unmatched'] == 1 else 'match'} no item, to check.")
    order = f["order"]
    if not order:
        lines.append(f"  No item is below {f['trigger_days']} days of cover.")
    else:
        rows = []
        plan = f.get("plan") or {}
        for n, x in enumerate(order, 1):
            it, r = x["item"], x["rate"]
            rows.append([str(n), short(it.name, 34), it.code or "", stock_plan.supplier_of(plan, it.ledger_name),
                         fmt.units(it.on_hand), fmt.units(it.on_order) if it.on_order else "", fmt.units(r.sold),
                         str(r.days_in_stock), _rate_cell(x), days_text(x["cover"]),
                         fmt.units(x["qty"]), _cost_cell(x["cost_bhd"]),
                         _date_short(it.last_receipt, d) if it.last_receipt else "none",
                         str(it.rank) if it.rank and it.rank <= model.cfg.fast_n else ""])
        lines += _indent(fmt.grid(["#", "Item", "Code", "Supplier", "On hand", "On order", f"Sold {RATE_DAYS} d",
                                   "In stock", "A day", "Cover", "Order", "At cost", "Last receipt", "Fast"], rows,
                                  "rlllrrrrrrrrlr"))
        if any(_rate_cell(x).endswith("*") for x in order):
            lines.append(FLOOR_NOTE)
        if any(_rate_cell(x).endswith("~") for x in order):
            lines.append(LONGER_NOTE)
        units = sum((x["qty"] for x in order), ZERO)
        value = sum((x["cost_bhd"] for x in order if x["cost_bhd"] is not None), ZERO)
        label, basis = cost_words(basis_counts(x["item"] for x in order))
        lines.append(f"  Total: {_plural(len(order), 'item')}, {fmt.units(units)} units, BHD {fmt.bhd(value)} {label} "
                     f"({basis}).")
        lines.append(f"  (In stock = days of the last {RATE_DAYS} the main store held the item at the day's end; "
                     "Fast = its rank among the fast movers.)")
        longer = [x for x in order if str(x.get("rate_basis") or "").startswith("longer")]
        if longer:
            lines.append(f"  {_plural(len(longer), 'item')} out of stock most of the last {RATE_DAYS} days "
                         f"{'takes' if len(longer) == 1 else 'take'} the rate of a longer history (back to the first "
                         f"window holding {RATE_MIN_DAYS} days in stock, up to a year): "
                         + ", ".join(x["item"].code or short(x["item"].name, 20) for x in longer[:8])
                         + (" ..." if len(longer) > 8 else ""))
    lines += supplier_rule_lines(model) + season_lines(model) + on_the_way_lines(model)

    lines += ["", f"LEAD TIME  {lead['days']} days: "
              + (f"the median of the last {_plural(len(lead['shown']), 'supplier invoice')} to their receipt; config "
                 f"'stock.lead_time_days' is {lead['default']}" if lead["basis"] == "measured" else
                 f"config 'stock.lead_time_days'; only {_plural(len(lead['shown']), 'supplier invoice')} could be matched "
                 f"to a receipt (a measured lead time needs {LEAD_MIN})")]
    if lead["shown"]:
        rows = [[x["invoice"], _date_short(x["invoice_date"], d), x["receipt"], _date_short(x["receipt_date"], d),
                 str(x["days"]), f"{x['same']} of {x['models']} models same quantity"] for x in lead["shown"]]
        lines += _indent(fmt.grid(["Supplier invoice", "Dated", "Receipt", "Received", "Days", "Basis"], rows,
                                  "llllrl"))
        lines.append(f"  (an invoice is set beside the receipt within {LEAD_MAX_DAYS} days that holds the same quantity "
                     "of the most of its models, and of at least half of them)")

    fa = f["fast"]
    lines += ["", f"FAST MOVERS  the {fa['n']} best sellers by units, {month_name(fa['rank_first'])} to "
                  f"{month_name(fa['rank_last'])}, and their days in stock"]
    now, last = fa["now"], fa["last"]
    lines.append(f"  This month so far: {fmt.pct(now['available'], now['sku_days'])} of SKU-days available; "
                 f"{month_name(last['first'])}: {fmt.pct(last['available'], last['sku_days'])} (every item that sold: "
                 f"{fmt.pct(now['wide_available'], now['wide_sku_days'])} and "
                 f"{fmt.pct(last['wide_available'], last['wide_sku_days'])})")
    rows = []
    rates = fa["rates"]
    for it in fa["list"]:
        r = rates[it.ledger_name]
        here = available_days(it, model.series_start, now["first"], now["last"], r.rate)
        rows.append([str(it.rank), short(it.name, 34), it.code or "", fmt.units(it.sold_rank), fmt.units(it.on_hand),
                     rate_text(r.rate, r.floored), days_text(cover_of(max(it.on_hand, ZERO), r.rate)),
                     f"{here} of {now['days']}"])
    if rows:
        lines += _indent(fmt.grid(["Rank", "Item", "Code", "Units sold", "On hand", "A day", "Cover", "Days available"],
                                  rows, "rllrrrrr"))
        if any(rates[it.ledger_name].floored for it in fa["list"]):
            lines.append(FLOOR_NOTE)
    if last["out_all"]:
        lines.append(f"  Out all of {month_name(last['first'])} (that month's fast movers): "
                     + ", ".join(short(it.name, 30) for it in last["out_all"]))
    if fa["last_partial"]:
        lines.append(f"  ({month_name(last['first'])}'s fast movers are ranked on the months the ledger holds: it starts "
                     "inside their year)")
    lines += _footer(model, ledger_note)
    return lines


def ageing_lines(model: Model, ledger_note: str) -> list[str]:
    f, d = model.facts, model.d
    ag = f["ageing"]
    lines = [f"AGEING  main store, ledger to {fmt.day(d)}: units on hand allocated to supplier receipts, newest "
             "first; a return from a van never makes stock younger",
             "  BHD {} ({}), by the days since the item last sold (any warehouse)".format(*cost_words(basis_counts(
                 it for it in model.items if it.on_hand > 0 and not it.display)))]
    head = ["Age", "Items", "Units", "At cost"] + list(SALE_BANDS) + ["Suggested"]
    rows = []
    for i, r in enumerate(ag["rows"]):
        if not r["units"]:
            continue
        cells = [fmt.bhd(c["value"]) if c["units"] else "" for c in ag["cells"][i]]
        value = fmt.bhd(r["value"]) + (" *" if r["uncosted_units"] else "")
        rows.append([r["band"], fmt.units(len(r["items"])), fmt.units(r["units"]), value] + cells + [r["action"]])
    if ag["display_items"]:
        rows.append(["display items (left out)", fmt.units(ag["display_items"]), fmt.units(ag["display_units"]), "",
                     "", "", "", "", ""])
    total_units = ag["units"] + ag["display_units"]
    rows.append(["Total", "", fmt.units(total_units), fmt.bhd(ag["value"]), "", "", "", "", ""])
    lines += _indent(fmt.grid(head, rows, "lrrrrrrrl"))
    if any(r["uncosted_units"] for r in ag["rows"]):
        lines.append("  * units without a cost are counted, not valued")
    lines.append("  (an item whose units came on two receipts counts in both of their bands)")
    check = "the same as" if total_units == ag["main_units"] else "NOT the same as"
    lines.append(f"  The bands add up to {fmt.units(total_units)} units, {check} the main store's "
                 f"{fmt.units(ag['main_units'])} units.")
    oldest = ag["oldest"][:OLDEST_ROWS]
    if oldest:
        lines += ["", "OLDEST  the items with the most value in stock over 180 days"]
        rows = []
        for x in oldest:
            it = x["item"]
            since = x["since"]
            if since is None or since > SALE_LIMITS[1]:
                action = "not selling: push it through the sales team (its weekly target is below)"
            else:
                action = "still selling: order no more until it runs down"
            rows.append([short(it.name, 34), it.code or "", fmt.units(x["units"]), _cost_cell(x["value"]),
                         _date_short(it.last_sale, d), action])
        lines += _indent(fmt.grid(["Item", "Code", "Units over 180 d", "At cost", "Last sale", "Suggested"], rows,
                                  "llrrll"))
    review = _part_lines(model, "review", lambda part: stock_review.review_lines(part, model))
    if review:
        lines += [""] + review
    lines += _footer(model, ledger_note)
    return lines


def damage_view_lines(model: Model, ledger_note: str) -> list[str]:
    """/yq-stock damage: the damage store in full (scripts.ops.stock_damage words it)."""
    lines = _part_lines(model, "damage", lambda part: stock_damage.damage_lines(part, model))
    return (lines or ["DAMAGE  nothing to show"]) + _footer(model, ledger_note)


def item_cost_words(it: Item) -> str:
    """One item's value at cost and the basis of its unit cost."""
    if it.cost is None:
        return "no cost on file (neither an MRN landed cost, a pricing-sheet cost nor a receipt rate)"
    value = f"BHD {fmt.bhd(max(it.on_hand, ZERO) * it.cost)}"
    unit = f"{fmt.bhd(it.cost)} a unit"
    code = it.code or "this item"
    if it.cost_source == "mrn":
        ref = it.cost_ref or {}
        doc = f", receipt {ref['doc_no']}" if ref.get("doc_no") else ""
        when = f" of {fmt.day(ref['effective_date'])}" if doc and ref.get("effective_date") else ""
        return f"at landed cost {value} (MRN landed cost {unit}{doc}{when})"
    if it.cost_source == "sheet":
        return f"at landed cost {value} (the pricing sheet {unit}: no MRN landed cost for {code})"
    return f"at cost {value} (the latest receipt rate {unit}: neither an MRN landed cost nor a pricing-sheet cost for {code})"


def find_items(model: Model, query: str) -> list[Item]:
    """The item a code names: its Focus item code; else the items whose model code it is; else the
    items whose name holds it."""
    q = fold(query)
    if not q:
        return []
    every = model.items
    exact = [it for it in every if it.code and fold(it.code) == q]
    if exact:
        return exact
    by_model = [it for it in every if items.leading_code(it.name).casefold() == q]
    if by_model:
        return sorted(by_model, key=lambda it: it.name)
    return sorted((it for it in every if q in fold(it.name) or q in fold(it.ledger_name)), key=lambda it: it.name)


def item_lines(model: Model, query: str, ledger_note: str) -> list[str]:
    f, d = model.facts, model.d
    found = find_items(model, query)
    if not found:
        raise StockInput(f"No accessory item has the code {query!r} (nor a name that holds it). "
                         "Type its Focus item code as the price book writes it.")
    if len(found) > 1:
        lines = [f"{_plural(len(found), 'item')} answer to {query!r}: type one of these codes"]
        rows = [[it.code or "(no code)", short(it.name, 50), fmt.units(it.on_hand)] for it in found[:20]]
        lines += _indent(fmt.grid(["Code", "Item", "On hand"], rows, "llr"))
        if len(found) > 20:
            lines.append(f"  (+{len(found) - 20} more)")
        return lines
    it = found[0]
    rates = f["fast"]["rates"]
    r = rates.get(it.ledger_name) or rate_at(it, model.series_start, d)
    lines = [f"ITEM  {it.code or '(no code)'}: {it.name}" + ("  (a display item)" if it.display else "")]
    lines.append(f"  Main store, end of {fmt.day(d)}: {fmt.units(it.on_hand)} units; BHD {fmt.bhd(it.sell_value)} at "
                 f"selling price in the stock balance; {item_cost_words(it)}")
    lines.append(f"  Sold in the last {RATE_DAYS} days: {fmt.units(r.sold)} units, in stock at the end of "
                 f"{r.days_in_stock} of those days: {rate_text(r.rate, r.floored)} a day; cover "
                 f"{days_text(cover_of(max(it.on_hand, ZERO), r.rate))}" + (" days" if r.rate > 0 else ""))
    if r.floored:
        lines.append(FLOOR_NOTE.replace(" (its sales over a year are on /yq-stock <code>)", ""))
    if it.rank:
        fast = " (a fast mover)" if it.rank <= model.cfg.fast_n else ""
        lines.append(f"  {month_name(f['fast']['rank_first'])} to {month_name(f['fast']['rank_last'])}: "
                     f"{fmt.units(it.sold_rank)} units sold, rank {it.rank}{fast}")
    else:
        lines.append(f"  No sale from {month_name(f['fast']['rank_first'])} to {month_name(f['fast']['rank_last'])}.")
    since = (d - it.last_sale).days if it.last_sale else None
    lines.append(f"  Last sale: {fmt.day(it.last_sale) if it.last_sale else 'none in the ledger'}"
                 + (f" ({since} days before {fmt.day(d)})" if since else "")
                 + f". First received: {fmt.day_long(it.first_receipt) if it.first_receipt else 'no receipt on file'}.")
    listed = next((x for x in f["order"] if x["item"] is it), None)
    if it.display:
        lines.append("  Order list: display items are left out.")
    elif listed:
        lines.append(f"  Order list: order {fmt.units(listed['qty'])} units"
                     + (f" (BHD {fmt.bhd(listed['cost_bhd'])} at cost)" if listed["cost_bhd"] is not None else "")
                     + f", {_lead_words(model)}; {_order_heading(model)}")
    elif r.rate <= 0:
        lines.append(f"  Order list: not listed (no sale in the last {RATE_DAYS} days).")
    else:
        lines.append(f"  Order list: not listed (cover at or above {f['trigger_days']} days).")
    if it.on_order:
        lines.append(f"  On order (KPI log): {fmt.units(it.on_order)} units.")
    lines += on_the_way_lines(model, only=it.ledger_name)
    if listed and str(listed.get("rate_basis") or "").startswith("longer"):
        lines.append(f"  Its order uses the rate of a {listed['rate_basis']} ({rate_text(listed['rate6'])} a day): it was "
                     f"in stock on fewer than {RATE_MIN_DAYS} of the last {RATE_DAYS} days.")
    now = f["fast"]["now"]
    here = available_days(it, model.series_start, now["first"], now["last"], r.rate)
    lines.append(f"  In stock with a day of sales at the day's end: {here} of {now['days']} days this month.")
    chunks = allocate(it.on_hand, it.receipts) if it.on_hand > 0 else []
    on_receipt = {voucher: units for _day, voucher, units in chunks}
    if it.receipts:
        lines.append("  Supplier receipts into the main store, newest first, and the units of today's stock on each:")
        rows = [[voucher, _date_short(day, d), fmt.units(units), fmt.units(on_receipt.get(voucher, ZERO)),
                 f"{(d - day).days} days"] for day, voucher, units, _v in reversed(it.receipts)]
        lines += _indent(fmt.grid(["Receipt", "Received", "Units", "Still on hand", "Age"], rows, "llrrr"), "    ")
    if chunks and chunks[-1][0] is None:
        lines.append(f"  {fmt.units(chunks[-1][2])} units on hand are explained by no receipt on file, to check.")
    lines += _part_lines(model, "review", lambda part: stock_review.item_lines(part, it.ledger_name))
    lines += _part_lines(model, "damage", lambda part: stock_damage.item_lines(part, it.ledger_name))
    lines += _footer(model, ledger_note)
    return lines


# ── the command ───────────────────────────────────────────────────────────────

ACTION_WORDS = ("ordered", "cancel", "team")    # a word and what follows it: an order placed, cancelled, the team copy


def parse_spec(words) -> tuple[str, str | None]:
    """(view, the rest): headline, reorder, ageing, damage, file; ordered / cancel with the supplier typed after it;
    team; or item with the code typed."""
    tokens = [w for raw in (words or []) for w in str(raw).split()]
    if not tokens:
        return "headline", None
    if tokens[0].lower() in ACTION_WORDS:
        return tokens[0].lower(), " ".join(tokens[1:]) or None
    if len(tokens) == 1 and tokens[0].lower() in VIEW_WORDS:
        return VIEW_WORDS[tokens[0].lower()], None
    return "item", " ".join(tokens)


def add_arguments(parser) -> None:
    parser.add_argument("spec", nargs="*", metavar="word",
                        help="reorder, ageing, damage, file, ordered <supplier>, cancel <supplier>, team, or an item code")
    parser.add_argument("--ledger", choices=ledger.PREFER, default="auto",
                        help="where the stock ledger is read: auto (the file when it equals production), file, db")
    parser.add_argument("--line", action="append", default=[], metavar="CODE=QTY",
                        help="ordered: a line typed instead of the newest workbook's (may repeat)")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="ordered / cancel: the day the order was placed (default today, Bahrain)")
    parser.add_argument("--file", default=None, metavar="PATH",
                        help="ordered / team: the workbook to read instead of the newest under exports/reports/stock")
    parser.add_argument("--commit", default=None, metavar="TOKEN",
                        help="ordered / cancel: log the lines the preview showed (its token); only after his yes")


# ── an order placed, an order cancelled, the team copy ───────────────────────

def _bahrain_now():
    from datetime import datetime
    from scripts.ops import status
    return datetime.now(status.BAHRAIN)


def _typed_lines(raw: list[str]) -> list[tuple[str, str]]:
    """--line CODE=QTY, as typed (stock_orders checks each code and quantity)."""
    out = []
    for text in raw or []:
        code, sep, qty = str(text).partition("=")
        if not sep or not code.strip() or not qty.strip():
            raise StockInput(f"A line is typed CODE=QTY, for example --line T17=300: {text!r} is not.")
        out.append((code.strip(), qty.strip()))
    return out


def _order_source(args, supplier: str) -> dict:
    """The lines of an order placed: typed (--line), or this supplier's final quantities in his newest workbook (or
    --file): {pairs, sha, source, notes, problems}."""
    import hashlib
    typed = _typed_lines(getattr(args, "line", None))
    if typed:
        canon = "\n".join(f"{c.upper()}={q}" for c, q in typed)
        return {"pairs": typed, "sha": hashlib.sha256(canon.encode("utf-8")).hexdigest(), "source": "typed",
                "notes": [], "problems": []}
    from scripts.ops.workbook import readback
    path = Path(args.file) if getattr(args, "file", None) else readback.newest_plan(paths.reports_dir("stock"))
    if path is None or not Path(path).is_file():
        raise StockInput("No Stock & Order Plan workbook to read: type /yq-stock first (or name the lines with "
                         "--line CODE=QTY).")
    try:
        plan = readback.read_plan(Path(path))
    except readback.PlanFileError as exc:
        raise StockInput(str(exc)) from None
    notes = [f"  Read from {Path(path).name}, last saved {plan.get('saved_at') or '?'}."]
    if plan.get("open_in_excel"):
        notes.append("  The file is open in Excel: this is its last save. Save it first if you changed it since.")
    pairs = [(c, str(q)) for c, q in readback.final_lines(plan, supplier)]
    return {"pairs": pairs, "sha": plan["sha256"], "source": Path(path).name, "notes": notes,
            "problems": list(plan.get("problems") or [])}


def order_log_lines(model: Model, verb: str, supplier: str | None, args) -> list[str]:
    """ordered <supplier>: the lines an order placed would log, with a token; with --commit <token> (only after his
    yes) they are logged in the KPI log and count as on the way from the next screen. cancel <supplier>: the same for
    a cancelled order. Nothing is ever sent to a supplier."""
    supplier = " ".join(str(supplier or "").split())
    if not supplier:
        raise StockInput(f"Name the supplier: /yq-stock {verb} <supplier> (as the order list names it, for example "
                         "VFAN).")
    now = _bahrain_now()
    today = now.date()
    order_day = fmt.as_date(args.date) if getattr(args, "date", None) else today
    if order_day is None:
        raise StockInput(f"The date is written YYYY-MM-DD: {args.date!r} is not one.")
    stamp = now.replace(microsecond=0).isoformat()
    existing = read_promises() or []
    names = {code_key(it.code): it.name for it in model.items if it.code}
    if verb == "cancel":
        cancel = stock_orders.cancel_lines(existing, supplier, order_day, stamp)
        if not cancel:
            raise StockInput(f"No open order of {supplier} dated {order_day.isoformat()} is in the KPI log.")
        plan_w = {"write": [], "unchanged": [], "cancel": cancel}
        pairs = [(row["item_code"], "0") for row in cancel]
        source = {"sha": "cancel", "notes": [], "problems": []}
        head = f"ORDER CANCELLED  {supplier}, ordered on {fmt.day_long(order_day)}: {_plural(len(cancel), 'line')}"
    else:
        source = _order_source(args, supplier)
        pairs = source["pairs"]
        if source["problems"]:                          # the file's own words first: nothing is worked out from it
            return ([f"ORDER PLACED  {supplier}: the workbook needs fixing first (nothing can be logged while these "
                     "stand):"] + source["notes"] + [f"    {p}" for p in source["problems"]])
        if not pairs:
            raise StockInput(f"The workbook holds no quantity for {supplier}: type the quantities in its Order sheet "
                             "(or name the lines with --line CODE=QTY).")
        try:
            new = stock_orders.promise_lines(supplier, order_day, pairs, source["source"], stamp, today=today)
        except ValueError as exc:
            raise StockInput(str(exc)) from None
        plan_w = stock_orders.plan_write(existing, new)
        head = (f"ORDER PLACED  {supplier}, ordered on {fmt.day_long(order_day)}: {_plural(len(new), 'line')}, "
                f"{fmt.units(sum((Decimal(q) for _c, q in pairs), ZERO))} units")
    token = stock_orders.token(source["sha"], supplier, order_day, pairs)
    lines = [head] + source["notes"]
    shown = plan_w["write"] + plan_w["cancel"]
    if shown:
        rows = [[row["item_code"], short(names.get(code_key(row["item_code"]), ""), 40),
                 fmt.units(Decimal(row["qty"])) if row.get("state") != stock_orders.CANCELLED else "cancelled"]
                for row in shown]
        lines += _indent(fmt.grid(["Code", "Item", "Units"], rows, "llr"))
    if plan_w["unchanged"]:
        lines.append(f"  {_plural(len(plan_w['unchanged']), 'line')} already logged as it is: nothing to change there.")
    unknown = [c for c, _q in pairs if code_key(c) not in names]
    if unknown and verb != "cancel":
        lines.append("  Not an item of the main store, to check: " + ", ".join(unknown[:8]))
    if source["problems"]:
        lines.append("  Fix these in the file first (nothing can be logged while they stand):")
        lines += [f"    {p}" for p in source["problems"]]
        return lines
    if not shown:
        lines.append("  Nothing to log: the KPI log already holds this order as it is.")
        return lines
    commit = getattr(args, "commit", None)
    if commit:
        if commit != token:
            raise StockInput("The file or the lines changed since the preview: nothing was logged. Run the preview "
                             "again and log what it shows.")
        n = stock_orders.append(kpi_log_file(), shown)
        if verb == "cancel":
            lines.append(f"  Logged: {_plural(n, 'line')} in the KPI log. From the next screen and file this order no "
                         "longer counts as on the way.")
        else:
            lines.append(f"  Logged: {_plural(n, 'line')} in the KPI log. From the next screen and file they count as "
                         "on the way, until the supplier's receipts cover them.")
        return lines
    again = [f"python -m scripts.ops stock {verb} {supplier}"]
    if getattr(args, "date", None):
        again.append(f"--date {order_day.isoformat()}")
    if getattr(args, "file", None):
        again.append(f"--file \"{args.file}\"")
    for c, q in _typed_lines(getattr(args, "line", None)):
        again.append(f"--line {c}={q}")
    again.append(f"--commit {token}")
    lines += ["  Nothing is logged yet. Once he says yes, this logs exactly these lines:", "    " + " ".join(again)]
    return lines


def team_lines(model: Model, args) -> list[str]:
    """team: the team copy of his newest workbook (or --file): his quantities and comments, no cost or value."""
    from scripts.ops.workbook import readback, stock_team
    path = Path(args.file) if getattr(args, "file", None) else readback.newest_plan(paths.reports_dir("stock"))
    if path is None or not Path(path).is_file():
        raise StockInput("No Stock & Order Plan workbook to copy: type /yq-stock first.")
    res = stock_team.team_file(Path(path))
    if res.get("error"):
        return [f"TEAM COPY  not made this time: {res['error']}."]
    made = Path(res["path"])
    try:
        shown = str(made.resolve().relative_to(paths.data_root().resolve()))
    except ValueError:
        shown = str(made)
    return [f"TEAM COPY  {shown} (made now from {Path(path).name})",
            "  Your quantities and comments, with a Team comment column; no cost, value, budget or salesman's name.",
            "  Share this one with the team; keep your own file for costs."]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"


def facts_out(model: Model, view: str, query: str | None) -> dict:
    """The facts saved beside the screen: figures only, items by name and code."""
    f = model.facts

    def item_row(it: Item) -> dict:
        return {"item": it.name, "code": it.code, "on_hand": it.on_hand, "cost": it.cost, "cost_source": it.cost_source}

    out = {k: v for k, v in f.items() if k not in ("order", "fast", "ageing", "damage", "review", "plan")}
    for key, as_json in (("damage", stock_damage.facts_json), ("review", stock_review.facts_json),
                         ("plan", stock_plan.facts_json)):
        part = f.get(key) or {}
        out[key] = part if "error" in part else as_json(part)
    out["order"] = [dict(item_row(x["item"]), rate=x["rate"].rate, sold=x["rate"].sold,
                         days_in_stock=x["rate"].days_in_stock, cover=x["cover"], qty=x["qty"], cost_bhd=x["cost_bhd"])
                    for x in f["order"]]
    fa = f["fast"]
    out["fast"] = {"n": fa["n"], "rank_first": fa["rank_first"], "rank_last": fa["rank_last"],
                   "items": [dict(item_row(it), rank=it.rank, sold=it.sold_rank) for it in fa["list"]]}
    for key in ("now", "last"):
        a = dict(fa[key])
        a["out_all"] = [it.name for it in a["out_all"]]
        out["fast"][key] = a
    ag = f["ageing"]
    out["ageing"] = {"rows": [dict(r, items=len(r["items"])) for r in ag["rows"]], "cells": ag["cells"],
                     "units": ag["units"], "value": ag["value"], "main_units": ag["main_units"],
                     "display_units": ag["display_units"],
                     "oldest": [dict(item_row(x["item"]), units=x["units"], value=x["value"]) for x in ag["oldest"]]}
    out["view"], out["query"] = view, query
    return _jsonable(out)


def day_file(model: Model, force: bool = False) -> dict:
    """The day's Stock & Order Plan workbook (scripts.ops.workbook.stock_book), made at most once per data date
    unless forced. Never raises: a workbook that cannot be made is one sentence, and the screen still prints."""
    if paths.is_worktree() and not paths.root_is_set():
        # a code worktree reads the main folder's data, but its unfinished code never makes the owner's day file:
        # build-once would hand that file to his next /yq-load (--data-root names another folder to try it in)
        return {"path": None, "made": False, "made_at": None, "controls": [],
                "error": "this is a code worktree, and the day's file belongs to the project folder (to try it, "
                         "name another folder with --data-root)"}
    try:
        from scripts.ops.workbook import stock_book
    except ImportError as exc:
        return {"path": None, "made": False, "made_at": None, "controls": [],
                "error": f"the workbook engine could not be loaded ({type(exc).__name__})"}
    try:
        return stock_book.day_file(model, force=force)
    except Exception as exc:  # noqa: BLE001  (the file is one part of the answer, never the answer)
        if RAISE_PARTS:
            raise
        model.warnings.append(f"the workbook could not be made: {type(exc).__name__}: {exc}")
        return {"path": None, "made": False, "made_at": None, "controls": [], "error": type(exc).__name__}


def file_lines(book: dict) -> list[str]:
    """The FILE line at the top of the screen: where the day's workbook is, or why there is none."""
    if not book or book.get("built") is False:
        return []
    if book.get("error"):
        return [f"FILE  not made this time: {book['error']}."]
    path = Path(book["path"])
    try:
        shown = str(path.resolve().relative_to(paths.data_root().resolve()))
    except ValueError:
        shown = str(path)
    when = "made now" if book.get("made") else f"made at {book.get('made_at') or '?'}; /yq-stock file makes a new one"
    lines = [f"FILE  {shown} ({when})"]
    lines += [f"  To check in the file: {c}" for c in book.get("controls") or []]
    return lines + [""]


def run(args) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    view, query = parse_spec(getattr(args, "spec", None))
    cfg = settings()
    inp = gather(getattr(args, "ledger", "auto") or "auto")
    book = None
    try:
        model = build(inp, cfg)
        if view == "reorder":
            lines = reorder_lines(model, inp.ledger_note)
        elif view == "ageing":
            lines = ageing_lines(model, inp.ledger_note)
        elif view == "damage":
            lines = damage_view_lines(model, inp.ledger_note)
        elif view in ("ordered", "cancel"):
            lines = order_log_lines(model, view, query, args)
        elif view == "team":
            lines = team_lines(model, args)
        elif view == "item":
            lines = item_lines(model, query or "", inp.ledger_note)
        else:
            lines = headline_lines(model, inp.ledger_note)
            book = day_file(model, force=view == "file")
            lines = file_lines(book) + lines
    except StockInput as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    text = "\n".join(_guarded(ln).rstrip() for ln in lines)
    stem = {"headline": "stock", "file": "stock", "reorder": "stock-reorder", "ageing": "stock-ageing",
            "damage": "stock-damage", "ordered": "stock-ordered", "cancel": "stock-cancel",
            "team": "stock-team"}.get(view) or f"stock-item-{_slug(query or '')}"
    folder = paths.exports_dir(model.d)
    facts = facts_out(model, view, query)
    facts["file"] = {k: v for k, v in (book or {}).items() if k in ("path", "made", "made_at", "error")}
    files = [paths.write_text(folder / f"{stem}.txt", text + "\n"),
             paths.write_text(folder / f"{stem}.json", json.dumps(facts, indent=1, ensure_ascii=False) + "\n")]
    flags = {"view": view, "order_items": len(model.facts["order"]), "lead_basis": model.facts["lead"]["basis"],
             "goods_on_order": bool(model.facts["on_order"]["counted"])}
    if book and book.get("path"):
        flags["file"] = book["path"]
    return dict(out, data_date=model.d.isoformat(), text=text, flags=flags, files=[str(p) for p in files],
                warnings=model.warnings)
