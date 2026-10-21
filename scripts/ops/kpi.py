"""python -m scripts.ops kpi [YYYY-MM]        (the command behind /yq-kpi: the monthly KPI scorecard)
           python -m scripts.ops kpi log ...        (the evidence log: scripts.ops.kpi_log)

THE SCORECARD of one month (default: the last whole month), one row per KPI of his KPI sheet (areas 1 to 5 and 7;
the sheet has no area 6), each with one status:
    MET            the target is met, and the evidence it rests on is named under it
    NOT MET        the evidence on file shows the target is not met
    NOT EVIDENCED  nothing on file shows either way: a source could not be read, or nothing was logged
    NOTHING DUE    nothing fell due in the month, or the KPI has no target (its figures are shown)
The header counts each status. The month the KPI log started is marked "log started <day>". Then the 90-day plan's
rows for the month (config 'kpi.plan_targets'), each target set beside its actual where a source holds one. A status
is never MET without named evidence: _row() turns a MET with nothing named into NOT EVIDENCED. A row that rests on the
KPI log (2b, 3b, 4b's claims) reads NOTHING DUE only when the log was read and holds lines: with no log yet, or one
that could not be read, it reads NOT EVIDENCED (_log_gap), as the header says.

THE ROWS (the approved plan's table; M = the month; D = the data date: the last loaded sale day)
  1a Stock cover          the aggregate cover of /yq-stock's KPI 1 (the main store's stock at landed cost over the
                          daily cost of sales, new arrivals apart) at the end of M, MET inside 'stock.cover_kpi_days'
                          (the plan's 30 to 45); the share of value above the band; the months of sales the last
                          supplier receipt holds. The stock engine works cover out at D and at the end of the month
                          before D's: an older month reads NOT EVIDENCED
  1b Fast-mover availability   available SKU-days over SKU-days of M's fast movers (/yq-stock), MET at
                          'kpi.fast_mover_availability_min' or above (the plan's 95%); the same two months as 1a
  1c Ageing identified    MET when a build record of M's workbook names its ageing part (record "parts": "ageing");
                          NOTHING DUE while M is not whole
  2a New items            M's new items from four sources: Market Intel items (new products) first moved to
                          researching, opportunity or approved in M; product finds reviewed or promoted (by their last
                          change); Upcoming cards created; candidates first logged. An item two sources name by one
                          code counts once. MET inside 'kpi.new_skus_band' (the plan's 5 to 10), NOT MET outside it;
                          NOT EVIDENCED while a source that could change that is unread (the KPI log among them)
  2b Checked before ordering   each new item ordered in M (a candidate logged 'ordered', or an order placed for a
                          code the main store had not received before that day, a candidate's or another) with all
                          four checks (price, quality, demand, competitor) logged by the order day, judged on the day
                          each check was LOGGED. A candidate short of one: NOT MET; a new code with no candidate line,
                          or an order before the log started: NOT EVIDENCED; nothing new ordered: NOTHING DUE (the
                          orders are the log's own lines: with no log, or one unread, NOT EVIDENCED)
  2c Launches             the items first received in the 90 days to M's end: units received, units sold in their first
                          30 and 60 days (counts until a successful launch is defined: NOTHING DUE)
  3a Quotations           each main-store supplier receipt of M valued above 'kpi.major_purchase_bhd' (its value as the
                          ledger states it) with quotations from two or more suppliers logged for its MRN, dated on or
                          before it (one supplier: NOT MET; none: NOT EVIDENCED; a receipt with units on lines that state
                          no value cannot be told major: NOT EVIDENCED); NOT EVIDENCED while that key is not set; the
                          supplier register is named, or reads "register not found" ('kpi.supplier_register' empty, or no
                          such file)
  3b Cost, terms, warranty  every supplier ordered from in M (orders placed, purchase orders on the Orders page) with a
                          terms line on file by M's end (never NOT MET: a missing line is NOT EVIDENCED); each code's
                          price against its last order beside it (v_po_cost_change). No supplier ordered from: NOTHING
                          DUE only when both the purchase orders and the log were read (and the log holds lines)
  3c Timely delivery      each receipt of M against the delivery day promised for it (a quote or terms line for its MRN
                          with a due date); its lead time from the supplier invoice beside it
  4a Incoming check       each receipt of M: the units counted in its check line against the units received, with
                          quantity, quality and packaging each recorded
  4b Damage and claims    the units sent to the damage store in M; each claim due in M (or raised in M with no due date)
                          settled by its due day. Units sent to damage and no claim logged: NOT EVIDENCED; so are units
                          not known (no ledger, the damage store not set up, its figures not worked out or not reaching
                          M) and a log that is missing or unread; NOTHING DUE only when none went and none was due
  5a Monthly sales        M's accessories sales (v_sales: SIM and devices out, giveaways out, ex-VAT, returns not
                          deducted) against the KPI step in force ('sales.kpi_steps'); the three months to M against the
                          three before; the same month last year when the data has it. A month not over is NOTHING DUE
                          below the step
  5b SKU productivity     the 20 best sellers of M (by sales) against the rest: share of sales, margin (sales less the
                          units at /yq-stock's landed cost); the company's margin beside it (no target: NOTHING DUE)
  7  Reports on time      the reports due in M: each weekly workbook, due on the first 'kpi.weekly_due_weekday' (the
                          plan's Sunday) after its Saturday; the monthly workbook of the month before M, due by working
                          day 'kpi.monthly_due_working_day' (the plan's 5) of M, the working days 'kpi.working_days'
                          (the plan's Sunday to Thursday). A monthly report counts only as a final file (a DRAFT never
                          does). The build records under exports/ops/ are the evidence (built_at, else built_on): a
                          report due before the first record of its kind reads NOT EVIDENCED, one due after it with no
                          record NOT MET. "Sent" is counted where it is logged

READS, each made once and only when a row asks for it; a read that fails costs only its own rows (NOT EVIDENCED, the
reason named under the screen): the KPI log and the build records (local files); the stock model
(scripts.ops.stock: the ledger, the snapshot, the costs, the supplier invoices); v_sales and v_po_cost_change through
the read RPC; Market Intel, Product Finds and the Upcoming cards through the read-only session (the read RPC cannot
see them). Nothing is written but the screen and its facts (exports/ops/<D>/kpi-<M>.txt and .json, through
sales.save_screen); `kpi log` writes the log (scripts.ops.kpi_log).

CONTRACT (the month workbook relies on it)
  scorecard(month=None, prefer="auto", sources=None) -> dict      the card; nothing is written
      {"month", "label", "first", "last", "data_date", "today", "whole", "partial",
       "log": {"started", "lines", "marker"}, "counts": {status: n}, "rows": [row],
       "plan": {"month", "set", "rows": [plan row], "counts", "note"}, "unread": [words], "defaults_used": [key],
       "notes": [words]}
      row = {"key", "area", "kpi", "target", "status", "actual" (one line: figures and totals, no name),
             "evidence": [lines: what it rests on, refs and codes named], "source", "value", "unit"}
      plan row = {"name", "target", "actual", "status", "value", "unit", "source"}
  render(card) -> list[str]           the screen
  jsonable(card) -> dict              the card as JSON (Decimals as text, dates ISO)
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from scripts.ops import db  # first: it blanks the side channels before any app import
from scripts.ops import config, fmt, items, kpi_log, ledger, paths, status, stock_orders

COMMAND = "kpi"
MET, NOT_MET, NOT_EVIDENCED, NOTHING_DUE = "MET", "NOT MET", "NOT EVIDENCED", "NOTHING DUE"
STATUSES = (MET, NOT_MET, NOT_EVIDENCED, NOTHING_DUE)
AREAS = {1: "Stock planning", 2: "New products", 3: "Suppliers", 4: "Stock quality", 5: "Sales", 7: "Reporting"}
# The plan's defaults while config.json does not hold the key (config.OPTIONAL): named under the screen when used.
AVAILABILITY_DEFAULT = Decimal("0.95")
NEW_BAND_DEFAULT = (5, 10)
WEEKLY_DUE_DEFAULT = "Sunday"
MONTHLY_DUE_DEFAULT = 5
WORKING_DAYS_DEFAULT = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday")
LAUNCH_DAYS = 90                  # 2c: first received in the 90 days to the month's end
LAUNCH_WINDOWS = (30, 60)         # ... and sold in their first 30 and 60 days
BEST_SELLERS = 20                 # 5b: the 20 best sellers against the rest
GROWTH_MONTHS = 3                 # 5a: the three months to M against the three before
MOVED_ON = ("researching", "opportunity", "approved")      # a Market Intel item taken forward
FIND_MOVED = ("reviewing", "promoted")                      # a product find taken forward
TAKEN_BACK = frozenset({"cancelled", "canceled"})           # an order taken back was never placed
LISTED = 8                        # codes and refs named on one evidence line
ZERO = Decimal(0)
Q3 = Decimal("0.001")
SRC_STOCK = "/yq-stock (the stock ledger)"
SRC_LOG = "the KPI log (/yq-kpi log)"
MONTH_WORD = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")

BASIS_SQL = "v.division NOT IN ('SIM', 'Devices') AND NOT v.is_giveaway"      # sales.BASIS_SQL (a test holds them equal)
ANCHOR_SQL = ("SELECT (SELECT MAX(sale_date) FROM v_sales)::text AS focus_to, "
              "(SELECT MIN(sale_date) FROM v_sales)::text AS focus_from, "
              "(SELECT MAX(loaded_at) FROM v_agent_data_trust WHERE source = 'focus_upload')::text AS uploaded_at")
MONTHS_SQL = ("SELECT to_char(v.sale_date, 'YYYY-MM') AS m, ROUND(COALESCE(SUM(v.net_bhd), 0)::numeric, 3) AS net_bhd, "
              "COUNT(DISTINCT v.invoice_no) AS invoices FROM v_sales v "
              f"WHERE {BASIS_SQL} AND v.sale_date BETWEEN $1::date AND $2::date GROUP BY 1 ORDER BY 1")
SKU_SQL = ("SELECT COALESCE(NULLIF(v.sku_code, ''), v.item_name) AS code, MIN(v.item_name) AS item, "
           "ROUND(COALESCE(SUM(v.net_bhd), 0)::numeric, 3) AS net_bhd, COALESCE(SUM(v.quantity), 0) AS units "
           f"FROM v_sales v WHERE {BASIS_SQL} AND v.sale_date BETWEEN $1::date AND $2::date GROUP BY 1")
# $1..$2 the three months to M, $3 the least a shop buys to count, $4 M as 'YYYY-MM': two counts, never a name
SHOPS_SQL = ("SELECT COALESCE(SUM(CASE WHEN b.m = $4 THEN 1 ELSE 0 END), 0) AS shops, "
             "COUNT(DISTINCT CASE WHEN b.months = 3 THEN b.shop END) AS regular FROM ("
             "SELECT s.m, s.shop, COUNT(*) OVER (PARTITION BY s.shop) AS months FROM ("
             "SELECT to_char(v.sale_date, 'YYYY-MM') AS m, COALESCE(v.customer_name, v.customer_account) AS shop, "
             "SUM(v.net_bhd) AS net FROM v_sales v "
             f"WHERE {BASIS_SQL} AND NOT v.is_cash_customer "
             "AND COALESCE(v.customer_name, v.customer_account) IS NOT NULL "
             "AND v.sale_date BETWEEN $1::date AND $2::date GROUP BY 1, 2) s "
             "WHERE s.net > 0 AND s.net >= $3::numeric) b")
REP_DAYS_SQL = ("SELECT COUNT(*) AS rep_days FROM (SELECT DISTINCT v.salesman_resolved, v.sale_date FROM v_sales v "
                f"WHERE {BASIS_SQL} AND v.salesman_resolved IS NOT NULL AND COALESCE(v.channel, '') <> 'B2C' "
                "AND v.sale_date BETWEEN $1::date AND $2::date) x")
PO_SQL = ("SELECT item_code, vendor, last_ordered::text AS last_ordered, current_rate_bhd::text AS rate_now, "
          "prev_rate_bhd::text AS rate_prev FROM v_po_cost_change WHERE last_ordered BETWEEN $1::date AND $2::date")
# the read-only session (the read RPC cannot see these tables)
MARKET_SQL = ("select d.item_id, min((d.created_at at time zone 'Asia/Bahrain')::date)::text as first_move, "
              "max(i.yq_item_code) as code from market_item_decisions d join market_items i on i.id = d.item_id "
              "where i.kind = 'new_product' and (d.to_status in ('researching', 'opportunity', 'approved') "
              "or d.event = 'approve') group by d.item_id")
FINDS_SQL = ("select id, status, promoted_item_code as code, "
             "(updated_at at time zone 'Asia/Bahrain')::date::text as moved_on from product_finds "
             "where status in ('reviewing', 'promoted')")
UPCOMING_SQL = ("select model_code, catalog_item_code as code, status, "
                "(created_at at time zone 'Asia/Bahrain')::date::text as created_on from shop_upcoming_items")


class KpiInput(ValueError):
    """What was typed cannot be answered: one plain sentence, printed as it is."""


# ── small helpers ─────────────────────────────────────────────────────────────

def month_start(d: date, back: int = 0) -> date:
    """The first day of d's month, `back` months earlier (later, when negative)."""
    n = d.year * 12 + (d.month - 1) - back
    return date(n // 12, n % 12 + 1, 1)


def month_end(any_day: date) -> date:
    return month_start(any_day, -1) - timedelta(days=1)


def month_key(d: date) -> str:
    return f"{d:%Y-%m}"


def month_label(first: date) -> str:
    return first.strftime("%B %Y")


def _plural(n, one: str, many: str | None = None) -> str:
    return f"{fmt.units(n)} {one if n == 1 else (many or one + 's')}"


def _whole_days(x) -> int:
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _signed(n: int) -> str:
    return f"+{n}" if n > 0 else str(n)


def _code(code) -> str:
    """An item code as it is compared: spaces collapsed, capitals ('' for none)."""
    return " ".join(str(code or "").split()).upper()


def _listed(names) -> str:
    names = [str(n) for n in names if n]
    return ", ".join(names[:LISTED]) + (f" (+{len(names) - LISTED} more)" if len(names) > LISTED else "")


def _guarded(line: str) -> str:
    """Every line shown passes the wording guard (fmt.careful); a word of the data it refuses is blanked."""
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


def jsonable(x):
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        items_ = sorted(x) if isinstance(x, (set, frozenset)) else x
        return [jsonable(v) for v in items_]
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, date):
        return x.isoformat()
    return x


def _stock_module():
    """scripts.ops.stock, imported when a row needs it (it reads the portal's own modules)."""
    from scripts.ops import stock
    return stock


def _reason(exc: BaseException) -> str:
    """Why a read failed, in words: the sentence of a refusal the engine words itself, else the kind of error only
    (an error's own text can quote what a cell holds)."""
    if type(exc).__name__ in ("ConfigMissing", "StockInput", "NoLedgerFile", "LogInput", "KpiInput"):
        text = " ".join(str(exc).replace(str(paths.config_file()), "config.json").split())
        return text[:200]
    return type(exc).__name__


# ── the reads ─────────────────────────────────────────────────────────────────

class Sources:
    """The reads of the scorecard: each one made once, on the first row that asks for it. A read that fails is kept
    with its reason (why) and costs only the rows that need it. Tests hand in `given` {part: value}; an exception
    given stands for a read that failed. `today` (Bahrain) is the day the scorecard is made."""

    WORDS = {"anchors": "the data dates (v_sales)", "state": "the last load on record (state.json)",
             "log": "the KPI log", "stock": "the stock ledger and the stock figures (/yq-stock)",
             "months": "the monthly sales (v_sales)", "skus": "the sales by item (v_sales)",
             "shops": "the shops buying (v_sales)", "rep_days": "the selling rep-days (v_sales)",
             "market": "Market Intel (the portal)", "finds": "Product Finds (the portal)",
             "upcoming": "the Upcoming cards (the portal)", "po": "the purchase orders' prices (v_po_cost_change)",
             "whole": "whether the month is whole (the /yq-month rule)"}

    def __init__(self, prefer: str = "auto", given: dict | None = None, today: date | None = None):
        self.prefer = prefer
        self.given = dict(given or {})
        self.today = today or status.today_bahrain()
        self.got: dict = {}
        self.why: dict[str, str] = {}
        self.first: date | None = None
        self.last: date | None = None
        self.shop_min = ZERO

    def get(self, part: str):
        if part in self.got:
            return self.got[part]
        try:
            if part in self.given:
                value = self.given[part]
                if isinstance(value, BaseException):
                    raise value
            else:
                value = getattr(self, "_" + part)()
        except Exception as exc:  # noqa: BLE001  (one source costs its own rows, never the scorecard)
            self.why[part] = _reason(exc)
            value = None
        self.got[part] = value
        return value

    def unread(self, part: str) -> bool:
        return part in self.why

    def words(self, part: str) -> str:
        """'<the source> could not be read (<why>)', for a row whose source is unread."""
        return f"{self.WORDS.get(part, part)} could not be read ({self.why.get(part, 'no answer')})"

    def unread_lines(self) -> list[str]:
        return [f"{self.WORDS.get(p, p)} ({w})" for p, w in self.why.items()]

    # each read, by its part's name
    def _anchors(self):
        rows = db.rpc(ANCHOR_SQL)
        return rows[0] if rows else {}

    def _state(self):
        return status.read_state()

    def _log(self):
        return kpi_log.read_lines()

    def _stock(self):
        stock = _stock_module()
        cfg = stock.settings()
        inp = stock.gather(self.prefer)
        return {"model": stock.build(inp, cfg), "inp": inp}

    def _months(self):
        first = month_start(self.first, 17)                 # 5a: the year before the month, and the five before it
        rows = db.rpc_params(MONTHS_SQL, [first.isoformat(), self.last.isoformat()])
        return {str(r.get("m")): {"net": fmt.dec(r.get("net_bhd")), "invoices": int(fmt.dec(r.get("invoices")))}
                for r in rows}

    def _skus(self):
        return db.rpc_params(SKU_SQL, [self.first.isoformat(), self.last.isoformat()])

    def _shops(self):
        rows = db.rpc_params(SHOPS_SQL, [month_start(self.first, 2).isoformat(), self.last.isoformat(),
                                         str(self.shop_min), month_key(self.first)])
        row = rows[0] if rows else {}
        return {"shops": int(fmt.dec(row.get("shops"))), "regular": int(fmt.dec(row.get("regular")))}

    def _rep_days(self):
        rows = db.rpc_params(REP_DAYS_SQL, [self.first.isoformat(), self.last.isoformat()])
        return int(fmt.dec((rows[0] if rows else {}).get("rep_days")))

    def _po(self):
        return db.rpc_params(PO_SQL, [self.first.isoformat(), self.last.isoformat()])

    def _session_rows(self, sql: str):
        with db.session() as s:
            return s.q(sql)

    def _market(self):
        return self._session_rows(MARKET_SQL)

    def _finds(self):
        return self._session_rows(FINDS_SQL)

    def _upcoming(self):
        return self._session_rows(UPCOMING_SQL)


@dataclass
class Ctx:
    month: str
    first: date
    last: date
    d: date                       # the data date (the last loaded sale day; today when none could be read)
    today: date
    whole: bool | None            # the month is whole (the /yq-month rule); None: it could not be told
    partial: bool                 # the month is not over in the data
    log_started: date | None
    used: list = field(default_factory=list)       # config keys whose plan default stood in
    notes: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return month_label(self.first)

    def day(self, x) -> str:
        """'Tue 06 Oct 2026': every day on this screen carries its year (a month is often looked back on)."""
        d = fmt.as_date(x)
        return f"{fmt.day(d)} {d.year}" if d else "n/a"


def _setting(ctx: Ctx, key: str, default):
    """config.get(key), or the plan's default when config.json does not hold the key (named under the screen). A key
    that is there with a wrong value stops the row that reads it (ConfigMissing, its sentence on the row)."""
    try:
        return config.get(key)
    except config.ConfigMissing:
        try:
            held = config.load()
        except config.ConfigMissing:
            held = {}
        if key in held:
            raise
        if key not in ctx.used:
            ctx.used.append(key)
        return default


def _row(key: str, kpi: str, target: str, status_: str, actual: str, evidence=(), source: str = "", value=None,
         unit: str | None = None) -> dict:
    """One row of the scorecard. A MET with no evidence named is not a MET: it reads NOT EVIDENCED."""
    evidence = [e for e in (evidence or []) if e]
    if status_ == MET and not evidence:
        status_, evidence = NOT_EVIDENCED, ["no evidence could be named for it"]
    return {"key": key, "area": int(key[0]), "kpi": kpi, "target": target, "status": status_, "actual": actual,
            "evidence": evidence, "source": source, "value": value, "unit": unit}


def _worst(states: list[str]) -> str | None:
    """The status of a row from its items: any NOT MET, else any NOT EVIDENCED, else MET when one is met; None when
    there is nothing but items not due."""
    if NOT_MET in states:
        return NOT_MET
    if NOT_EVIDENCED in states:
        return NOT_EVIDENCED
    if MET in states:
        return MET
    return None


def _stock_parts(src: Sources):
    got = src.get("stock")
    return got if isinstance(got, dict) and got.get("model") is not None else None


NO_LOG_YET = "the KPI log has no line yet: nothing on file can show that nothing fell due"


def _log_gap(src: Sources) -> str | None:
    """Why a row that rests on the KPI log cannot say NOTHING DUE: the log could not be read, or it holds no line yet
    (the header says so too). None when the log was read and holds lines."""
    lines = src.get("log")
    if src.unread("log"):
        return src.words("log")
    return NO_LOG_YET if not lines else None


def _which_month(model, ctx: Ctx) -> str | None:
    """'now' when the month holds the ledger's last day, 'last' when it is the month before that one, else None: the
    two months the stock engine works its KPI 1 figures out for."""
    d = model.d
    if ctx.first <= d <= ctx.last:
        return "now"
    if ctx.last == month_start(d) - timedelta(days=1):
        return "last"
    return None


def _not_this_month(model, ctx: Ctx) -> str:
    e = month_start(model.d) - timedelta(days=1)
    return (f"the stock engine works it out for the month of the ledger's last day ({ctx.day(model.d)}) and for the "
            f"month that ended on {ctx.day(e)}: {ctx.label} is not one of them")


def receipts_in(model, first: date, last: date) -> list[dict]:
    """The main store's supplier receipts (Material Receipt Notes) dated first..last, one per voucher: its units, its
    value as the ledger states it (units on lines with no value are counted in 'unvalued'), its items; oldest first."""
    found: dict[str, dict] = {}
    for it in model.items:
        for day, voucher, units, value in it.receipts:
            if not first <= day <= last:
                continue
            r = found.setdefault(voucher, {"voucher": voucher, "date": day, "units": ZERO, "value": ZERO,
                                           "unvalued": ZERO, "items": [], "key": kpi_log.purchase_key(voucher)})
            r["date"] = min(r["date"], day)
            r["units"] += units
            r["value"] += value
            if units and not value:
                r["unvalued"] += units
            r["items"].append(it)
    return sorted(found.values(), key=lambda r: (r["date"], r["voucher"]))


# ── area 1: stock planning ────────────────────────────────────────────────────

def row_cover(ctx: Ctx, src: Sources) -> dict:
    key, name = "1a", "Stock cover"
    got = _stock_parts(src)
    if got is None:
        return _row(key, name, "the KPI band in days", NOT_EVIDENCED, "not worked out", [src.words("stock")], SRC_STOCK)
    model, stock = got["model"], _stock_module()
    low, high = model.cfg.low, model.cfg.high
    target = f"{low} to {high} days"
    which = _which_month(model, ctx)
    if which is None:
        return _row(key, name, target, NOT_EVIDENCED, "not worked out for this month", [_not_this_month(model, ctx)],
                    SRC_STOCK)
    kc = model.facts["kpi_cover"]
    at = model.d if which == "now" else kc["last_month_end"]
    fig = kc[which]
    if fig["days"] is None:
        return _row(key, name, target, NOT_EVIDENCED, "not worked out",
                    ["no item with a cost sold in the rate window: cover cannot be worked out"], SRC_STOCK)
    days = _whole_days(fig["days"])
    when = f"on {ctx.day(at)}" if which == "now" else f"at the end of {ctx.label}"
    actual = f"{days} days {when}; {fmt.pct(fig['above_value'], fig['value'])} of the value above {high} days"
    if which == "now" and kc["last"]["days"] is not None:
        actual += f" ({_signed(days - _whole_days(kc['last']['days']))} days against the end of the month before)"
    valued = [it for it in model.items if not it.display and not stock.is_new(it, at)]
    label, basis = stock.cost_words(stock.basis_counts(valued))
    evidence = [f"the stock ledger to {ctx.day(model.d)}: the main store's stock {label} ({basis}) over the daily "
                "cost of sales, new arrivals apart" + ("; at today's costs" if at != model.d else "")]
    lr = model.facts.get("last_receipt")
    if lr:
        months = Decimal(lr["months"]).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        evidence.append(f"one shipment is months of sales: the last supplier receipt ({lr['voucher']}, "
                        f"{ctx.day(lr['date'])}) holds {months} months of sales of its items at today's rates")
    status_ = MET if low <= days <= high else NOT_MET
    return _row(key, name, target, status_, actual, evidence, SRC_STOCK, value=Decimal(days), unit="days")


def row_availability(ctx: Ctx, src: Sources) -> dict:
    key, name = "1b", "Fast-mover availability"
    least = Decimal(str(_setting(ctx, "kpi.fast_mover_availability_min", AVAILABILITY_DEFAULT)))
    target = f"{fmt.pct(least, 1, 0)} of the fast movers' days in stock"
    got = _stock_parts(src)
    if got is None:
        return _row(key, name, target, NOT_EVIDENCED, "not worked out", [src.words("stock")], SRC_STOCK)
    model = got["model"]
    which = _which_month(model, ctx)
    if which is None:
        return _row(key, name, target, NOT_EVIDENCED, "not worked out for this month", [_not_this_month(model, ctx)],
                    SRC_STOCK)
    fa = model.facts["fast"]
    a = fa[which]
    if not a["sku_days"]:
        return _row(key, name, target, NOT_EVIDENCED, "not worked out",
                    ["no fast mover sold in the months they are ranked on"], SRC_STOCK)
    share = (Decimal(a["available"]) / Decimal(a["sku_days"])).quantize(Q3, rounding=ROUND_HALF_UP)
    actual = (f"{fmt.pct(a['available'], a['sku_days'])} of SKU-days available ({fmt.units(a['available'])} of "
              f"{fmt.units(a['sku_days'])}; the {a['fast']} best sellers by units)")
    evidence = [f"the main store's day-end stock, {ctx.day(a['first'])} to {ctx.day(a['last'])}: a day counts when it "
                "covers a day of the item's sales",
                f"every item that sold: {fmt.pct(a['wide_available'], a['wide_sku_days'])} of its days available"]
    if a["out_all"]:
        evidence.append("out on every one of those days: " + _listed(it.code or it.name for it in a["out_all"]))
    status_ = MET if share >= least else NOT_MET
    return _row(key, name, target, status_, actual, evidence, SRC_STOCK, value=share, unit="share")


def _names_ageing(record: dict) -> bool:
    parts = record.get("parts")
    if isinstance(parts, dict):
        return bool(parts.get("ageing"))
    if isinstance(parts, (list, tuple)):
        return any(str(p).strip().lower() == "ageing" for p in parts)
    return False


def row_ageing(ctx: Ctx, src: Sources) -> dict:
    key, name, target = "1c", "Ageing identified", "every month, in the month's workbook"
    source = "the build records of /yq-month (exports/ops)"
    if ctx.partial:
        return _row(key, name, target, NOTHING_DUE, "the month is not over",
                    [f"the workbook of {ctx.label} is built once the month is whole"], source)
    recs = kpi_log.records("month", ctx.month)
    with_age = [r for r in recs if _names_ageing(r)]
    if with_age:
        r = with_age[0]
        kind = "final" if r.get("draft") is False else "DRAFT"
        part = (r.get("parts") or {}).get("ageing") if isinstance(r.get("parts"), dict) else None
        extra = ""
        if isinstance(part, dict) and part.get("items_with_action") is not None:
            extra = f"; {_plural(int(fmt.dec(part['items_with_action'])), 'item')} with an action"
        return _row(key, name, target, MET, f"identified in the {ctx.label} workbook",
                    [f"the {kind} workbook of {ctx.label} built on {ctx.day(r['_built'])} holds the ageing part "
                     f"({r['_file']}){extra}"], source, value=Decimal(1), unit="count")
    if recs:
        return _row(key, name, target, NOT_EVIDENCED, "no ageing part on file",
                    [f"the workbook of {ctx.label} built on {ctx.day(recs[-1]['_built'])} has no ageing part (the "
                     "month-end pack adds it)"], source)
    return _row(key, name, target, NOT_EVIDENCED, "no workbook on file",
                [f"no workbook of {ctx.label} is on record (/yq-month builds it)"], source)


# ── area 2: new products ──────────────────────────────────────────────────────

def _identity(code, fallback: str) -> str:
    c = _code(code)
    return f"code:{c}" if c else fallback


def _in_month(ctx: Ctx, x) -> bool:
    d = fmt.as_date(x)
    return d is not None and ctx.first <= d <= ctx.last


def row_new_items(ctx: Ctx, src: Sources) -> dict:
    key, name = "2a", "New items"
    band = _setting(ctx, "kpi.new_skus_band", list(NEW_BAND_DEFAULT))
    low, high = int(band[0]), int(band[1])
    target = f"{low} to {high} a month"
    source = "the portal (Market Intel, Product Finds, Upcoming) and " + SRC_LOG
    seen: dict[str, set] = {}
    per: dict[str, set] = {"market": set(), "finds": set(), "upcoming": set(), "candidates": set()}

    def add(part: str, identity: str) -> None:
        seen.setdefault(identity, set()).add(part)
        per[part].add(identity)

    unread = []
    for part in ("market", "finds", "upcoming"):
        rows = src.get(part)
        if rows is None:
            unread.append(part)
            continue
        for r in rows:
            if part == "market" and _in_month(ctx, r.get("first_move")):
                add(part, _identity(r.get("code"), f"mi:{r.get('item_id')}"))
            elif part == "finds" and str(r.get("status") or "") in FIND_MOVED and _in_month(ctx, r.get("moved_on")):
                add(part, _identity(r.get("code"), f"find:{r.get('id')}"))
            elif part == "upcoming" and str(r.get("status") or "") != "withdrawn" and _in_month(ctx, r.get("created_on")):
                add(part, _identity(r.get("code") or r.get("model_code"), f"up:{_code(r.get('model_code'))}"))
    if src.get("log") is None and src.unread("log"):
        unread.append("log")                              # the candidates could not be read: a source unread
    for ref, rows in kpi_log.of_ref(src.get("log"), "candidate").items():
        days = [fmt.as_date(r.get("event_date")) for r in rows]
        days = [x for x in days if x is not None]
        if days and ctx.first <= min(days) <= ctx.last:
            code = next((r.get("item_code") for r in reversed(rows) if r.get("item_code")), None)
            add("candidates", _identity(code, ref))
    n = len(seen)
    twice = sum(1 for parts in seen.values() if len(parts) > 1)
    actual = (f"{n} new items: {len(per['market'])} from Market Intel, {len(per['finds'])} finds, "
              f"{len(per['upcoming'])} Upcoming cards, {len(per['candidates'])} candidates logged")
    evidence = [f"{n} items in {ctx.label}: {len(per['market'])} Market Intel items moved on (new products), "
                f"{len(per['finds'])} product finds reviewed or promoted, {len(per['upcoming'])} Upcoming cards "
                f"created, {len(per['candidates'])} candidates first logged"
                + (f"; {_plural(twice, 'item')} named by two sources, counted once" if twice else "")]
    if seen:
        evidence.append("named: " + _listed(sorted(i.split(":", 1)[1] if i.startswith("code:") else i for i in seen)))
    for part in unread:
        evidence.append(src.words(part))
    if n > high:
        status_ = NOT_MET
    elif unread:
        status_ = NOT_EVIDENCED
    else:
        status_ = MET if low <= n <= high else NOT_MET
    return _row(key, name, target, status_, actual, evidence, source, value=Decimal(n), unit="count")


def _orders_in(ctx: Ctx, lines) -> dict[str, date]:
    """{CODE: the first day it was ordered in the month}: the orders placed (promise lines, the newest line of each
    ref), an order taken back aside."""
    out: dict[str, date] = {}
    for row in kpi_log.newest(lines, kpi_log.PROMISE).values():
        if str(row.get("state") or "").strip().lower() in TAKEN_BACK:
            continue
        day, code = fmt.as_date(row.get("event_date")), _code(row.get("item_code"))
        if code and day is not None and ctx.first <= day <= ctx.last:
            out[code] = min(out.get(code, day), day)
    return out


def _received_before(model, code: str, day: date) -> bool | None:
    """Did the main store receive this code (its item, or the items of the model code) before the day? None when no
    item of the stock model has the code."""
    hits = [it for it in model.items if it.code and _code(it.code) == code] or \
           [it for it in model.items if items.leading_code(it.name) == code]
    if not hits:
        return None
    return any(r[0] < day for it in hits for r in it.receipts)


def row_checked(ctx: Ctx, src: Sources) -> dict:
    key, name = "2b", "Checked before ordering"
    checks = kpi_log.KINDS["candidate"].checks
    target = "all four checks (" + ", ".join(checks) + ") logged before the order"
    lines = src.get("log") or []
    started = kpi_log.started(lines)
    cands = kpi_log.of_ref(lines, "candidate")
    orders = _orders_in(ctx, lines)
    code_of = {ref: _code(next((r.get("item_code") for r in reversed(rows) if r.get("item_code")), None))
               for ref, rows in cands.items()}
    got = _stock_parts(src)
    ordered: dict[str, date] = {}
    for ref, rows in cands.items():
        days = [fmt.as_date(r.get("event_date")) for r in rows if str(r.get("state")) == "ordered"]
        days = [x for x in days if x is not None and ctx.first <= x <= ctx.last]
        code = code_of[ref]
        # an order placed for the candidate's code counts while it is the code's first: one the main store received
        # before that day is a repeat order of an item it holds already
        if code and code in orders and not (got is not None and _received_before(got["model"], code, orders[code])):
            days.append(orders[code])
        if days:
            ordered[ref] = min(days)
    covered = {c for c in code_of.values() if c}
    no_line, untold = [], []
    for code, day in sorted(orders.items()):
        if code in covered:
            continue
        if got is None:
            untold.append(code)
            continue
        before = _received_before(got["model"], code, day)
        if not before:                                    # never received before the order: a new item
            no_line.append((code, day))
    states, evidence = [], []
    for ref, day in sorted(ordered.items(), key=lambda x: (x[1], x[0])):
        if started is None or day < started:
            states.append(NOT_EVIDENCED)
            evidence.append(f"{ref}: ordered on {ctx.day(day)}, before the KPI log started")
            continue
        done = {c for r in cands[ref] if (fmt.as_date(r.get("logged_at")) or date.max) <= day
                for c in (r.get("check_types") or [])}
        missing = [c for c in checks if c not in done]
        if missing:
            states.append(NOT_MET)
            evidence.append(f"{ref}: ordered on {ctx.day(day)}; not logged by then: {', '.join(missing)}")
        else:
            states.append(MET)
            evidence.append(f"{ref}: ordered on {ctx.day(day)}, all four checks logged by then")
    for code, day in no_line:
        states.append(NOT_EVIDENCED)
        evidence.append(f"{code}: a new item ordered on {ctx.day(day)} with no candidate line")
    if untold:
        states.append(NOT_EVIDENCED)
        evidence.append(f"{_plural(len(untold), 'code')} ordered ({_listed(untold)}) cannot be told new or repeat: "
                        + src.words("stock"))
    status_ = _worst(states)
    n = len(ordered) + len(no_line)
    if status_ is None:
        gap = _log_gap(src)                               # the orders and the candidates are the log's own lines
        if gap:
            return _row(key, name, target, NOT_EVIDENCED, "not judged", [gap], SRC_LOG)
        return _row(key, name, target, NOTHING_DUE, "no new item was ordered",
                    [f"no candidate was ordered and no new code was ordered in {ctx.label}"], SRC_LOG)
    met = states.count(MET)
    actual = f"{met} of {_plural(n, 'new item')} ordered had all four checks logged by the order day"
    return _row(key, name, target, status_, actual, evidence, SRC_LOG + ", the orders placed and the stock ledger",
                value=Decimal(met), unit="count")


def row_launches(ctx: Ctx, src: Sources) -> dict:
    key, name, target = "2c", "Launches", "counts (no target until a successful launch is defined)"
    got = _stock_parts(src)
    if got is None:
        return _row(key, name, target, NOT_EVIDENCED, "not worked out", [src.words("stock")], SRC_STOCK)
    model, stock = got["model"], _stock_module()
    end = min(ctx.last, model.d)
    start = end - timedelta(days=LAUNCH_DAYS - 1)
    new = [it for it in model.items if not it.display and it.first_receipt and start <= it.first_receipt <= end]
    received = ZERO
    sold = {n: ZERO for n in LAUNCH_WINDOWS}
    behind = {n: 0 for n in LAUNCH_WINDOWS}
    shown = []
    for it in sorted(new, key=lambda x: (x.first_receipt, x.name)):
        first = it.first_receipt
        got_units = sum((u for day, _v, u, _value in it.receipts if day == first), ZERO)
        received += got_units
        bits = [f"{it.code or stock.short(it.name, 30)}: {fmt.units(got_units)} received {ctx.day(first)}"]
        for n in LAUNCH_WINDOWS:
            if first + timedelta(days=n - 1) <= model.d:
                units = max(stock.sold_between(it, first, first + timedelta(days=n - 1)), ZERO)
                sold[n] += units
                behind[n] += 1
                bits.append(f"{fmt.units(units)} sold in {n} days")
        shown.append(", ".join(bits))
    actual = (f"{_plural(len(new), 'item')} first received in the {LAUNCH_DAYS} days to {ctx.day(end)}: "
              f"{fmt.units(received)} units received; sold in their first {LAUNCH_WINDOWS[0]} days "
              f"{fmt.units(sold[LAUNCH_WINDOWS[0]])} units ({behind[LAUNCH_WINDOWS[0]]} with that many days behind them), "
              f"in their first {LAUNCH_WINDOWS[1]} days {fmt.units(sold[LAUNCH_WINDOWS[1]])} "
              f"({behind[LAUNCH_WINDOWS[1]]})")
    evidence = shown[:LISTED] + ([f"(+{len(shown) - LISTED} more)"] if len(shown) > LISTED else [])
    return _row(key, name, target, NOTHING_DUE, actual, evidence or ["no item was first received in those days"],
                SRC_STOCK, value=Decimal(len(new)), unit="count")


# ── area 3: suppliers ─────────────────────────────────────────────────────────

def _register() -> str:
    """The supplier register named, or 'register not found' (config 'kpi.supplier_register' empty, or no file)."""
    try:
        where = str(config.get("kpi.supplier_register") or "").strip()
    except config.ConfigMissing as exc:
        return f"register not read ({_reason(exc)})"
    if not where or not Path(where).is_file():
        return "register not found"
    return f"the supplier register {Path(where).name}"


def _purchase_lines(lines, kinds: tuple) -> dict[str, list[dict]]:
    """{PURCHASE: lines} of the quote and terms lines, by the purchase their ref names (kind:PURCHASE:supplier)."""
    out: dict[str, list[dict]] = {}
    for row in lines or []:
        if isinstance(row, dict) and row.get("kind") in kinds:
            parts = str(row.get("ref") or "").split(":")
            if len(parts) == 3 and parts[1]:
                out.setdefault(parts[1], []).append(row)
    return out


def _receipts_or_row(ctx: Ctx, src: Sources, key: str, name: str, target: str, source: str):
    got = _stock_parts(src)
    if got is None:
        return None, _row(key, name, target, NOT_EVIDENCED, "not worked out", [src.words("stock")], source)
    return receipts_in(got["model"], ctx.first, ctx.last), None


def row_quotes(ctx: Ctx, src: Sources) -> dict:
    key, name = "3a", "Quotations for major purchases"
    register = _register()
    source = f"the stock ledger (receipts), {SRC_LOG}; {register}"
    try:
        floor = config.number("kpi.major_purchase_bhd")
    except config.ConfigMissing as exc:
        try:
            held = config.load()
        except config.ConfigMissing:
            held = {}
        why = ("config 'kpi.major_purchase_bhd' is not set: no receipt can be called a major purchase yet"
               if "kpi.major_purchase_bhd" not in held else _reason(exc))
        return _row(key, name, "two or more for each major purchase", NOT_EVIDENCED, "not judged", [why], source)
    target = f"two or more for each receipt above BHD {fmt.bhd(floor)}"
    receipts, refused = _receipts_or_row(ctx, src, key, name, target, source)
    if refused:
        return refused
    major = [r for r in receipts if r["value"] > floor]
    # a receipt whose lines state no value on some units cannot be told below the floor: it may be a major purchase
    untold = [r for r in receipts if r["value"] <= floor and r["unvalued"] > 0]
    if not major and not untold:
        return _row(key, name, target, NOTHING_DUE, "no major purchase",
                    [f"{_plural(len(receipts), 'supplier receipt')} in {ctx.label}, none above BHD {fmt.bhd(floor)}"],
                    source)
    by_purchase = _purchase_lines(src.get("log"), ("quote",))
    states, evidence = [], []
    for r in untold:
        states.append(NOT_EVIDENCED)
        evidence.append(f"{r['voucher']} ({ctx.day(r['date'])}): {fmt.units(r['unvalued'])} units on lines that state "
                        "no value: it cannot be told whether it is a major purchase")
    for r in major:
        suppliers = sorted({str(q.get("ref")).split(":")[2] for q in by_purchase.get(r["key"], [])
                            if (fmt.as_date(q.get("event_date")) or date.max) <= r["date"]})
        if len(suppliers) >= 2:
            states.append(MET)
        else:
            states.append(NOT_MET if suppliers else NOT_EVIDENCED)
        evidence.append(f"{r['voucher']} ({ctx.day(r['date'])}, BHD {fmt.bhd(r['value'])}): "
                        + (f"quotations from {len(suppliers)} suppliers ({', '.join(suppliers)})" if suppliers
                           else "no quotation logged for it"))
    met = states.count(MET)
    actual = f"{met} of {_plural(len(major), 'major purchase')} with two or more quotations" + (
        f"; {_plural(len(untold), 'receipt')} with no stated value" if untold else "")
    return _row(key, name, target, _worst(states), actual, evidence, source, value=Decimal(met), unit="count")


def _supplier_slug(name, aliases: dict) -> str:
    text = " ".join(str(name or "").split())
    return stock_orders.slug(aliases.get(items.norm(text), text))


def row_terms(ctx: Ctx, src: Sources) -> dict:
    key, name = "3b", "Cost, terms and warranty"
    target = "terms on file for every supplier ordered from"
    source = "the orders placed and the purchase orders (v_po_cost_change), " + SRC_LOG
    try:
        aliases = {items.norm(k): " ".join(str(v).split()) for k, v in config.get("stock.supplier_aliases").items()}
    except config.ConfigMissing:
        aliases = {}
    lines = src.get("log") or []
    suppliers: dict[str, str] = {}                        # slug -> the name as first seen
    for row in kpi_log.newest(lines, kpi_log.PROMISE).values():
        if str(row.get("state") or "").strip().lower() not in TAKEN_BACK and _in_month(ctx, row.get("event_date")):
            slug = _supplier_slug(row.get("supplier"), aliases)
            if slug:
                suppliers.setdefault(slug, " ".join(str(row.get("supplier")).split()))
    po = src.get("po")
    dearer = cheaper = same = 0
    for r in po or []:
        slug = _supplier_slug(r.get("vendor"), aliases)
        if slug:
            suppliers.setdefault(slug, " ".join(str(r.get("vendor")).split()))
        now, before = fmt.dec(r.get("rate_now")), fmt.dec(r.get("rate_prev"))
        if before > 0:
            dearer += now > before
            cheaper += now < before
            same += now == before
    terms: dict[str, dict] = {}                           # supplier slug -> its newest terms line by the month's end
    for ref_lines in kpi_log.of_ref(lines, "terms").values():
        for t in ref_lines:
            day = fmt.as_date(t.get("event_date"))
            slug = _supplier_slug(t.get("supplier"), aliases)
            if day is not None and day <= ctx.last and slug:
                terms[slug] = t
    gap = _log_gap(src)                                   # the orders placed and the terms are the log's own lines
    if not suppliers:
        if po is None or gap:
            return _row(key, name, target, NOT_EVIDENCED, "not judged",
                        [w for w in (src.words("po") if po is None else None, gap) if w], source)
        return _row(key, name, target, NOTHING_DUE, "no supplier ordered from",
                    [f"no order placed and no purchase order dated in {ctx.label}"], source)
    have = sorted(s for s in suppliers if s in terms)
    evidence = [f"{suppliers[s]}: terms logged on {ctx.day(terms[s]['event_date'])} ({terms[s]['ref']})" for s in have]
    evidence += [f"{suppliers[s]}: no terms line on file" for s in sorted(suppliers) if s not in terms]
    if src.unread("log"):
        evidence.append(src.words("log"))
    if po is None:
        evidence.append(src.words("po"))
    elif po:
        evidence.append(f"price against the last order: {dearer} dearer, {cheaper} cheaper, {same} the same "
                        f"({_plural(len(po), 'code')} ordered in {ctx.label})")
    status_ = MET if len(have) == len(suppliers) and po is not None else NOT_EVIDENCED
    actual = f"terms on file for {len(have)} of {_plural(len(suppliers), 'supplier')} ordered from"
    if po:
        actual += f"; {_plural(len(po), 'code')} ordered: {dearer} dearer than the last order, {cheaper} cheaper"
    return _row(key, name, target, status_, actual, evidence, source, value=Decimal(len(have)), unit="count")


def row_delivery(ctx: Ctx, src: Sources) -> dict:
    key, name, target = "3c", "Timely delivery", "received by the day promised"
    source = "the stock ledger and the supplier invoices (lead times), " + SRC_LOG + " (promised days)"
    receipts, refused = _receipts_or_row(ctx, src, key, name, target, source)
    if refused:
        return refused
    if not receipts:
        return _row(key, name, target, NOTHING_DUE, "no receipt", [f"no supplier receipt in {ctx.label}"], source)
    stock, got = _stock_module(), _stock_parts(src)
    inp = got["inp"]
    vendors = {str(r.get("invoice_no") or "").strip(): r.get("vendor") for r in inp.invoices or [] if r.get("vendor")}
    lead = {x["receipt"]: x for x in stock.measure_lead_times(inp.invoices, stock.receipt_codes(inp.rows))}
    promised: dict[str, date] = {}
    for purchase, rows in _purchase_lines(src.get("log"), ("quote", "terms")).items():
        dues = [fmt.as_date(r.get("due_date")) for r in rows]
        dues = [x for x in dues if x is not None]
        if dues:
            promised[purchase] = dues[-1]                 # the newest line that names one
    states, evidence = [], []
    for r in receipts:
        due = promised.get(r["key"])
        x = lead.get(r["voucher"])
        lead_words = (f"; {x['days']} days from supplier invoice {x['invoice']}"
                      + (f" ({vendors[x['invoice']]})" if vendors.get(x["invoice"]) else "")) if x else ""
        if due is None:
            states.append(NOT_EVIDENCED)
            evidence.append(f"{r['voucher']} received {ctx.day(r['date'])}: no promised day logged{lead_words}")
        elif r["date"] <= due:
            states.append(MET)
            evidence.append(f"{r['voucher']} received {ctx.day(r['date'])}, by the day promised ({ctx.day(due)})"
                            f"{lead_words}")
        else:
            states.append(NOT_MET)
            evidence.append(f"{r['voucher']} received {ctx.day(r['date'])}, {_plural((r['date'] - due).days, 'day')} "
                            f"after the day promised ({ctx.day(due)}){lead_words}")
    met = states.count(MET)
    actual = (f"{met} of {_plural(len(receipts), 'receipt')} by the day promised; "
              f"{len(receipts) - states.count(NOT_EVIDENCED)} with a promised day on file")
    return _row(key, name, target, _worst(states), actual, evidence, source, value=Decimal(met), unit="count")


# ── area 4: stock quality ─────────────────────────────────────────────────────

def row_incoming(ctx: Ctx, src: Sources) -> dict:
    key, name = "4a", "Incoming check"
    want = kpi_log.KINDS["receipt_check"].checks
    target = "every receipt counted in full, with " + ", ".join(want) + " recorded"
    source = "the stock ledger (receipts), " + SRC_LOG
    receipts, refused = _receipts_or_row(ctx, src, key, name, target, source)
    if refused:
        return refused
    if not receipts:
        return _row(key, name, target, NOTHING_DUE, "no receipt", [f"no supplier receipt in {ctx.label}"], source)
    checks = {ref.split(":", 1)[1]: rows for ref, rows in kpi_log.of_ref(src.get("log"), "receipt_check").items()
              if ":" in ref}
    states, evidence = [], []
    counted_all = units_all = ZERO
    for r in receipts:
        units_all += r["units"]
        rows = checks.get(r["key"])
        if not rows:
            states.append(NOT_EVIDENCED)
            evidence.append(f"{r['voucher']} ({fmt.units(r['units'])} units, {ctx.day(r['date'])}): no check logged")
            continue
        counted = fmt.dec(rows[-1].get("qty"))
        counted_all += min(counted, r["units"])
        done = {c for row in rows for c in (row.get("check_types") or [])}
        missing = [c for c in want if c not in done]
        short = counted < r["units"]
        if missing or short:
            states.append(NOT_MET)
            gaps = ([f"{fmt.units(counted)} of {fmt.units(r['units'])} units counted"] if short else []) + \
                   ([f"not recorded: {', '.join(missing)}"] if missing else [])
            evidence.append(f"{r['voucher']} ({ctx.day(r['date'])}): " + "; ".join(gaps))
        else:
            states.append(MET)
            evidence.append(f"{r['voucher']} ({ctx.day(r['date'])}): {fmt.units(counted)} of {fmt.units(r['units'])} "
                            "units counted; " + ", ".join(want) + " recorded")
    met = states.count(MET)
    actual = (f"{fmt.units(counted_all)} of {fmt.units(units_all)} units counted ({fmt.pct(counted_all, units_all)}); "
              f"{met} of {_plural(len(receipts), 'receipt')} checked in full")
    return _row(key, name, target, _worst(states), actual, evidence, source,
                value=(counted_all / units_all).quantize(Q3, rounding=ROUND_HALF_UP) if units_all else None,
                unit="share")


def row_damage(ctx: Ctx, src: Sources) -> dict:
    key, name, target = "4b", "Damage and claims", "every claim settled by its due day"
    source = "the stock ledger (the damage store), " + SRC_LOG + " (claims)"
    got = _stock_parts(src)
    sent, damage_words = None, src.words("stock") if got is None else None
    if got is not None:
        dm = got["model"].facts.get("damage") or {}
        if "error" in dm:
            damage_words = "the damage figures could not be worked out"
        elif not dm.get("set_up"):
            damage_words = "the damage store is not set up (config 'warehouses.damage')"
        else:
            month = next((m for m in dm.get("months") or [] if m["month"] == ctx.first), None)
            if month is None:
                damage_words = f"the damage figures do not reach {ctx.label}"
            else:
                sent = month["sent"]
    states, evidence = [], []
    pending = 0
    for ref, rows in kpi_log.of_ref(src.get("log"), "claim").items():
        raised = min((d for d in (fmt.as_date(r.get("event_date")) for r in rows) if d), default=None)
        last = rows[-1]
        due = next((fmt.as_date(r.get("due_date")) for r in reversed(rows) if r.get("due_date")), None)
        if not (due is not None and ctx.first <= due <= ctx.last) and \
                not (due is None and raised is not None and ctx.first <= raised <= ctx.last):
            continue
        state = str(last.get("state") or "")
        closed_on = fmt.as_date(last.get("closed_date")) if state in kpi_log.CLOSED_CLAIM else None
        if due is None:
            states.append(NOT_EVIDENCED)
            evidence.append(f"{ref}: raised {ctx.day(raised)}, no due day logged")
        elif closed_on is not None:
            states.append(MET if closed_on <= due else NOT_MET)
            evidence.append(f"{ref}: {state} on {ctx.day(closed_on)}, due {ctx.day(due)}"
                            + ("" if closed_on <= due else f" ({_plural((closed_on - due).days, 'day')} late)"))
        elif due < ctx.today:
            states.append(NOT_MET)
            evidence.append(f"{ref}: still open, due {ctx.day(due)}")
        else:
            pending += 1
            evidence.append(f"{ref}: open, due {ctx.day(due)} (not due yet)")
    damage_text = (f"{fmt.units(sent)} units sent to damage" if sent is not None else "damage units not worked out")
    if damage_words:
        evidence.append(damage_words)
    status_ = _worst(states)
    if status_ is None:
        gap = _log_gap(src)                               # the claims are the log's own lines
        if sent is not None and sent > 0:
            status_ = NOT_EVIDENCED
            evidence.append(f"{fmt.units(sent)} units went to the damage store in {ctx.label} and no claim is logged")
            if gap and src.unread("log"):
                evidence.append(gap)
        elif sent is None:                                # the damage units are not known: their words are above
            status_ = NOT_EVIDENCED
        elif gap:
            status_ = NOT_EVIDENCED
            evidence.append(gap)
        else:
            status_ = NOTHING_DUE
    met = states.count(MET)
    claims = f"{met} of {_plural(len(states), 'claim')} due settled in time" if states else "no claim due"
    actual = f"{damage_text}; {claims}" + (f"; {pending} not due yet" if pending else "")
    return _row(key, name, target, status_, actual, evidence, source,
                value=Decimal(sent) if sent is not None else None, unit="units")


# ── area 5: sales ─────────────────────────────────────────────────────────────

def row_sales(ctx: Ctx, src: Sources) -> dict:
    key, name = "5a", "Monthly sales"
    source = "v_sales (accessories, ex-VAT, giveaways out; returns not deducted)"
    try:
        step = config.kpi_step(ctx.month)
    except config.ConfigMissing as exc:
        return _row(key, name, "the KPI step", NOT_EVIDENCED, "not judged", [_reason(exc)], source)
    target = f"BHD {fmt.bhd(step)} (the KPI step)" if step is not None else "the KPI step (not set)"
    months = src.get("months")
    if months is None:
        return _row(key, name, target, NOT_EVIDENCED, "not worked out", [src.words("months")], source)
    cur = months.get(ctx.month, {"net": ZERO, "invoices": 0})
    net = cur["net"]
    window = [month_key(month_start(ctx.first, k)) for k in range(GROWTH_MONTHS)]
    before = [month_key(month_start(ctx.first, k)) for k in range(GROWTH_MONTHS, 2 * GROWTH_MONTHS)]
    three = sum((months.get(m, {}).get("net", ZERO) for m in window), ZERO)
    three_before = sum((months.get(m, {}).get("net", ZERO) for m in before), ZERO)
    evidence = [f"{ctx.label}: BHD {fmt.bhd(net)}, {_plural(cur['invoices'], 'invoice')}"
                + (" (the month is not over)" if ctx.partial else ""),
                f"the {GROWTH_MONTHS} months to {ctx.label}: BHD {fmt.bhd(three)}, {fmt.change(three, three_before)} on "
                f"the {GROWTH_MONTHS} before (BHD {fmt.bhd(three_before)})"]
    anchors = src.get("anchors") or {}
    data_from = fmt.as_date(anchors.get("focus_from"))
    year_ago = month_start(ctx.first, 12)
    if data_from is not None and data_from <= year_ago:
        ly = months.get(month_key(year_ago), {}).get("net", ZERO)
        evidence.append(f"{month_label(year_ago)}: BHD {fmt.bhd(ly)} ({fmt.change(net, ly)} a year on)")
    else:
        evidence.append("the same month last year: the sales data does not reach it"
                        + (f" (it starts on {ctx.day(data_from)})" if data_from else ""))
    if step is None:
        return _row(key, name, target, NOT_EVIDENCED, f"BHD {fmt.bhd(net)}; the KPI step is not set",
                    evidence + ["the KPI step is not set (config 'sales.kpi_steps')"], source, value=net, unit="bhd")
    actual = f"BHD {fmt.bhd(net)} against the step of BHD {fmt.bhd(step)} ({fmt.pct(net, step)})"
    if net >= step:
        status_ = MET
    else:
        status_ = NOTHING_DUE if ctx.partial else NOT_MET
    return _row(key, name, target, status_, actual, evidence, source, value=net, unit="bhd")


def row_productivity(ctx: Ctx, src: Sources) -> dict:
    key, name, target = "5b", "SKU productivity", "no target (its figures)"
    source = "v_sales, and the landed cost of /yq-stock"
    rows = src.get("skus")
    if rows is None:
        return _row(key, name, target, NOT_EVIDENCED, "not worked out", [src.words("skus")], source)
    got = _stock_parts(src)
    cost = {}
    if got is not None:
        cost = {_code(it.code): it.cost for it in got["model"].items if it.code and it.cost is not None}
    sales = sorted(({"code": _code(r.get("code")), "net": fmt.dec(r.get("net_bhd")), "units": fmt.dec(r.get("units"))}
                    for r in rows), key=lambda r: (-r["net"], r["code"]))
    top, rest = sales[:BEST_SELLERS], sales[BEST_SELLERS:]
    total = sum((r["net"] for r in sales), ZERO)

    def margin(group) -> tuple[str, int]:
        costed = [r for r in group if r["code"] in cost]
        net = sum((r["net"] for r in costed), ZERO)
        spent = sum((r["units"] * cost[r["code"]] for r in costed), ZERO)
        return (fmt.pct(net - spent, net) if net else "n/a"), len(group) - len(costed)

    m_top, u_top = margin(top)
    m_rest, u_rest = margin(rest)
    m_all, u_all = margin(sales)
    actual = (f"the {len(top)} best sellers: {fmt.pct(sum((r['net'] for r in top), ZERO), total)} of sales, margin "
              f"{m_top}; the other {len(rest)}: margin {m_rest}; the company: margin {m_all}")
    evidence = [f"v_sales of {ctx.label}: {_plural(len(sales), 'item')}, BHD {fmt.bhd(total)}"]
    if got is None:
        evidence.append(src.words("stock") + ": no margin is worked out")
    elif u_all:
        evidence.append(f"{_plural(u_all, 'item')} without a landed cost left out of the margins")
    return _row(key, name, target, NOTHING_DUE, actual, evidence, source,
                value=(sum((r["net"] for r in top), ZERO) / total).quantize(Q3, rounding=ROUND_HALF_UP) if total else None,
                unit="share")


# ── area 7: reporting ─────────────────────────────────────────────────────────

def _working_day(first: date, n: int, working: set[int]) -> date:
    """The n-th working day of the month that starts on `first` (the first working day is one)."""
    d, seen = first, 0
    while True:
        if d.weekday() in working:
            seen += 1
            if seen == n:
                return d
        d += timedelta(days=1)


def row_reports(ctx: Ctx, src: Sources) -> dict:
    key, name = "7", "Reports on time"
    due_day = config.weekday(_setting(ctx, "kpi.weekly_due_weekday", WEEKLY_DUE_DEFAULT))
    nth = int(_setting(ctx, "kpi.monthly_due_working_day", MONTHLY_DUE_DEFAULT))
    working = {config.weekday(w) for w in _setting(ctx, "kpi.working_days", list(WORKING_DAYS_DEFAULT))}
    day_name = config.WEEKDAYS[due_day].capitalize()
    target = f"weekly by the {day_name} after its week; monthly (final) by working day {nth}"
    source = "the build records of /yq-week and /yq-month (exports/ops), " + SRC_LOG + " (sent)"
    sent = {ref for ref, row in kpi_log.newest(src.get("log"), "report").items() if row.get("state") == "sent"}
    first_week = min((r["_built"] for r in kpi_log.all_records("week") if r["_built"]), default=None)
    first_final = min((r["_built"] for r in kpi_log.all_records("month") if r["_built"] and r.get("draft") is False),
                      default=None)
    dues: list[tuple] = []                                # (kind, key, words, due day)
    sat = ctx.first - timedelta(days=(ctx.first.weekday() - 5) % 7 + 7)
    while sat <= ctx.last:
        due = sat + timedelta(days=(due_day - sat.weekday() - 1) % 7 + 1)      # the first such weekday after it
        if ctx.first <= due <= ctx.last:
            dues.append(("week", sat.isoformat(), f"the week ending {ctx.day(sat)}", due))
        sat += timedelta(days=7)
    prev = month_start(ctx.first, 1)
    dues.append(("month", month_key(prev), f"the {month_label(prev)} report", _working_day(ctx.first, nth, working)))
    states, evidence = [], []
    waiting = 0
    for kind, ref_key, words, due in dues:
        if due > ctx.today:
            waiting += 1
            continue
        recs = kpi_log.records(kind, ref_key)
        if kind == "month":
            finals = [r for r in recs if r.get("draft") is False and r["_built"]]
            built, first_ever = (finals[0]["_built"] if finals else None), first_final
            missing = "no final file" + (" (a DRAFT only)" if recs else "")
        else:
            built = next((r["_built"] for r in recs if r["_built"]), None)
            first_ever, missing = first_week, "no build record"
        was_sent = f"report:{'weekly' if kind == 'week' else 'monthly'}:{ref_key}" in sent
        tail = "; sent (logged)" if was_sent else ""
        if built is not None:
            states.append(MET if built <= due else NOT_MET)
            evidence.append(f"{words}: due {ctx.day(due)}, built {ctx.day(built)}"
                            + ("" if built <= due else f" ({_plural((built - due).days, 'day')} late)") + tail)
        elif first_ever is None or due < first_ever:
            states.append(NOT_EVIDENCED)
            evidence.append(f"{words}: due {ctx.day(due)}, {missing}; due before the first "
                            + ("weekly workbook" if kind == "week" else "final monthly file") + " was built")
        else:
            states.append(NOT_MET)
            evidence.append(f"{words}: due {ctx.day(due)}, {missing} by then{tail}")
    status_ = _worst(states)
    due_n = len(states)
    if status_ is None:
        return _row(key, name, target, NOTHING_DUE, "nothing due yet",
                    [f"{_plural(waiting, 'report')} due later than today"] if waiting else ["no report fell due"], source)
    met = states.count(MET)
    actual = (f"{met} of {_plural(due_n, 'report')} due were prepared on time"
              + (f"; {waiting} due later" if waiting else ""))
    return _row(key, name, target, status_, actual, evidence, source, value=Decimal(met), unit="count")


ROWS = (row_cover, row_availability, row_ageing, row_new_items, row_checked, row_launches, row_quotes, row_terms,
        row_delivery, row_incoming, row_damage, row_sales, row_productivity, row_reports)
ROW_NAMES = {"1a": "Stock cover", "1b": "Fast-mover availability", "1c": "Ageing identified", "2a": "New items",
             "2b": "Checked before ordering", "2c": "Launches", "3a": "Quotations for major purchases",
             "3b": "Cost, terms and warranty", "3c": "Timely delivery", "4a": "Incoming check",
             "4b": "Damage and claims", "5a": "Monthly sales", "5b": "SKU productivity", "7": "Reports on time"}


def _safe(fn, key: str, ctx: Ctx, src: Sources) -> dict:
    """A row, or NOT EVIDENCED with the reason when it could not be worked out: one row never costs the screen."""
    try:
        return fn(ctx, src)
    except Exception as exc:  # noqa: BLE001
        return _row(key, ROW_NAMES[key], "", NOT_EVIDENCED, "not worked out",
                    [f"it could not be worked out this time ({_reason(exc)})"], "")


# ── the 90-day plan ───────────────────────────────────────────────────────────

# a target's name (folded) -> (its words, its unit, the row or read that holds its actual, which way is better)
PLAN_NAMES = {
    "sales": ("Sales", "bhd", "5a", "up"), "sales_bhd": ("Sales", "bhd", "5a", "up"),
    "monthly_sales": ("Sales", "bhd", "5a", "up"),
    "invoices": ("Invoices", "count", "invoices", "up"),
    "shops": ("Shops buying", "count", "shops", "up"), "shops_buying": ("Shops buying", "count", "shops", "up"),
    "buying_shops": ("Shops buying", "count", "shops", "up"),
    "regular_shops": ("Regular shops (each of the three months)", "count", "regular", "up"),
    "rep_days": ("Selling rep-days", "count", "rep_days", "up"),
    "selling_rep_days": ("Selling rep-days", "count", "rep_days", "up"),
    "availability": ("Fast-mover availability", "share", "1b", "up"),
    "fast_mover_availability": ("Fast-mover availability", "share", "1b", "up"),
    "best_seller_availability": ("Fast-mover availability", "share", "1b", "up"),
    "new_items": ("New items", "count", "2a", "up"), "new_skus": ("New items", "count", "2a", "up"),
    "cover": ("Stock cover (days)", "days", "1a", "down"), "cover_days": ("Stock cover (days)", "days", "1a", "down"),
    "stock_cover_days": ("Stock cover (days)", "days", "1a", "down"),
}


def _fold(name) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")


def _figure(x, unit: str | None) -> str:
    if x is None:
        return "n/a"
    if unit == "bhd":
        return f"BHD {fmt.bhd(x)}"
    if unit == "share":
        return fmt.pct(x, 1)
    return fmt.units(x)


def _plan_targets(ctx: Ctx) -> tuple[list[tuple], str | None]:
    """[(name as config writes it, target)] of the month, or why there are none."""
    try:
        every = config.get("kpi.plan_targets")
    except config.ConfigMissing as exc:
        return [], _reason(exc)
    got = every.get(ctx.month)
    if got is None:
        return [], f"no 90-day plan targets are set for {ctx.label} (config 'kpi.plan_targets')"
    if isinstance(got, dict):
        return list(got.items()), None
    if isinstance(got, list) and all(isinstance(x, dict) and "name" in x for x in got):
        return [(x["name"], x.get("target")) for x in got], None
    return [], f"the targets of {ctx.label} cannot be read (config 'kpi.plan_targets')"


def _target_of(raw, unit: str):
    """(low, high) from a target: a number is a floor (a ceiling for cover, by its row), [low, high] a band; a share
    may be written as a percentage (95 is 0.95). None when it cannot be read."""
    def num(v):
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            return None
        n = fmt.dec(v) if not isinstance(v, str) or re.fullmatch(r"\s*[0-9.,]+\s*", v) else None
        if n is None:
            return None
        return n / 100 if unit == "share" and n > 1 else n
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        low, high = num(raw[0]), num(raw[1])
        return (low, high) if low is not None and high is not None and low <= high else None
    n = num(raw)
    return (n, None) if n is not None else None


def plan_block(ctx: Ctx, src: Sources, rows: list[dict]) -> dict:
    targets, why = _plan_targets(ctx)
    out = {"month": ctx.month, "set": bool(targets), "rows": [], "counts": dict.fromkeys(STATUSES, 0), "note": why}
    by_key = {r["key"]: r for r in rows}
    for raw_name, raw_target in targets:
        known = PLAN_NAMES.get(_fold(raw_name))
        if known is None:
            out["rows"].append({"name": str(raw_name), "target": str(raw_target), "actual": "n/a", "status": NOT_EVIDENCED,
                                "value": None, "unit": None, "source": "no source holds this target's actual yet"})
            continue
        words, unit, where, better = known
        target = _target_of(raw_target, unit)
        if target is None:
            out["rows"].append({"name": words, "target": str(raw_target), "actual": "n/a", "status": NOT_EVIDENCED,
                                "value": None, "unit": unit, "source": "the target cannot be read"})
            continue
        low, high = target
        if where in by_key:
            value, source = by_key[where]["value"], f"KPI {where} above"
            if by_key[where]["status"] == NOT_EVIDENCED and where != "5a":
                value = None
        elif where == "invoices":
            months = src.get("months")
            value = None if months is None else Decimal(months.get(ctx.month, {}).get("invoices", 0))
            source = "v_sales"
        elif where in ("shops", "regular"):
            try:
                src.shop_min = config.number("kpi.shop_min_bhd")
            except config.ConfigMissing:
                src.shop_min = ZERO
                if "kpi.shop_min_bhd" not in ctx.used:
                    ctx.used.append("kpi.shop_min_bhd")
            shops = src.get("shops")
            value = None if shops is None else Decimal(shops[where])
            source = (f"v_sales: named shops buying BHD {fmt.bhd(src.shop_min)} or more in a month" if src.shop_min
                      else "v_sales: named shops with a purchase in the month")
        else:
            got = src.get("rep_days")
            value, source = (None if got is None else Decimal(got)), "v_sales: a salesman's days with an invoice"
        if high is not None:
            shown = f"{_figure(low, unit)} to {_figure(high, unit)}"
        else:
            shown = ("at most " if better == "down" else "") + _figure(low, unit)
        if value is None:
            status_ = NOT_EVIDENCED
        elif high is not None:
            status_ = MET if low <= value <= high else NOT_MET
        elif better == "down":
            status_ = MET if value <= low else NOT_MET
        else:
            status_ = MET if value >= low else (NOTHING_DUE if ctx.partial else NOT_MET)
        out["rows"].append({"name": words, "target": shown, "actual": _figure(value, unit), "status": status_,
                            "value": value, "unit": unit, "source": source})
    for r in out["rows"]:
        out["counts"][r["status"]] += 1
    return out


# ── the scorecard ─────────────────────────────────────────────────────────────

def _is_whole(month: str, d: date, src: Sources) -> bool | None:
    """The /yq-month rule (scripts.ops.workbook.build.ready): a load on or after the next month's first day, and the
    data within two days of the month's end. None when it cannot be told."""
    try:
        from scripts.ops.workbook import build
        loaded_at, _where = build.last_load_time(src.get("state") or {}, src.get("anchors") or {})
        return bool(build.ready(month, d, loaded_at)["ready"])
    except Exception as exc:  # noqa: BLE001
        src.why["whole"] = _reason(exc)
        return None


def scorecard(month: str | None = None, prefer: str = "auto", sources: Sources | None = None) -> dict:
    """The card of one month (see the module docstring). Nothing is written."""
    src = sources or Sources(prefer)
    today = src.today
    anchors = src.get("anchors") or {}
    d = fmt.as_date(anchors.get("focus_to"))
    notes: list[str] = []
    if d is None:
        got = _stock_parts(src)
        d = got["model"].d if got is not None else None
        notes.append("The data date could not be read from the sales: " + (
            f"the stock ledger's last day ({fmt.day_long(d)}) stands in." if d else "today stands in."))
        d = d or today
    current = month_key(d)
    if month is None:
        month = current if _is_whole(current, d, src) else month_key(month_start(d, 1))
    elif not MONTH_WORD.fullmatch(str(month)):
        raise KpiInput(f"A month is written YYYY-MM (for example 2026-09): '{str(month)[:20]}' is not one.")
    elif month > current:
        raise KpiInput(f"No data is loaded for {month} yet: the data runs to {fmt.day_long(d)}.")
    first = date(int(month[:4]), int(month[5:7]), 1)
    last = month_end(first)
    whole = True if last < d else _is_whole(month, d, src)
    lines = src.get("log")
    started = kpi_log.started(lines)
    ctx = Ctx(month=month, first=first, last=last, d=d, today=today, whole=whole, partial=not whole,
              log_started=started, notes=notes)
    src.first, src.last = first, last
    if src.unread("log"):
        marker = (f"The KPI log could not be read ({src.why['log']}): the rows that rest on it read NOT EVIDENCED this "
                  "time.")
    elif not lines or started is None:
        marker = "The KPI log has no line yet: the rows that rest on it read NOT EVIDENCED until a sentence is logged."
    elif first <= started <= last:
        marker = f"log started {ctx.day(started)}"
    elif started > last:
        marker = (f"The KPI log started on {ctx.day(started)}, after {ctx.label}: the rows that rest on it read NOT "
                  "EVIDENCED for this month.")
    else:
        marker = None
    rows = [_safe(fn, key, ctx, src) for fn, key in zip(ROWS, ROW_NAMES)]
    plan = plan_block(ctx, src, rows)
    got = _stock_parts(src)
    used = list(ctx.used) + [k for k in (got["model"].facts.get("defaults_used") or [] if got else []) if k not in ctx.used]
    counts = Counter(r["status"] for r in rows)
    return {"command": COMMAND, "month": month, "label": ctx.label, "first": first, "last": last, "data_date": d,
            "today": today, "whole": whole, "partial": ctx.partial,
            "log": {"started": started, "lines": len(lines or []), "marker": marker},
            "counts": {s: counts.get(s, 0) for s in STATUSES}, "rows": rows, "plan": plan,
            "unread": src.unread_lines(), "defaults_used": used, "notes": notes}


# ── the screen ────────────────────────────────────────────────────────────────

def _counts_text(counts: dict) -> str:
    return ", ".join(f"{counts.get(s, 0)} {s}" for s in STATUSES)


def render(card: dict) -> list[str]:
    d = card["data_date"]
    when = f"month to date, data to {fmt.day_long(d)}" if card["partial"] else f"data to {fmt.day_long(d)}"
    lines = [f"KPI SCORECARD  {card['label']} ({when})", "  " + _counts_text(card["counts"])]
    marker = card["log"]["marker"]
    if marker:
        lines.append("  " + (f"The KPI log: {marker}." if marker.startswith("log started") else marker))
    if card["whole"] is None:
        lines.append("  Whether the month is whole could not be told: its figures may still move.")
    lines.append("")
    lines += ["  " + ln for ln in fmt.grid(["KPI", "Target", "Status"],
                                           [[f"{r['key']} {r['kpi']}", r["target"], r["status"]] for r in card["rows"]],
                                           "lll").splitlines()]
    area = None
    for r in card["rows"]:
        if r["area"] != area:
            area = r["area"]
            lines += ["", f"{area}  {AREAS[area].upper()}"]
        lines.append(f"  {r['key']} {r['kpi']}: {r['status']}")
        lines.append(f"     {r['actual']}")
        lines += [f"     - {e}" for e in r["evidence"]]
        if r["source"]:
            lines.append(f"     Source: {r['source']}")
    plan = card["plan"]
    lines += ["", f"90-DAY PLAN  {card['label']}" + (f": {_counts_text(plan['counts'])}" if plan["rows"] else "")]
    if plan["rows"]:
        lines += ["  " + ln for ln in fmt.grid(["Target", "Planned", "Actual", "Status"],
                                               [[p["name"], p["target"], p["actual"], p["status"]] for p in plan["rows"]],
                                               "lrrl").splitlines()]
        for p in plan["rows"]:
            lines.append(f"  - {p['name']}: {p['source']}")
    else:
        lines.append(f"  {plan['note'] or 'no targets are set for this month'}.")
    lines += ["", "Basis: accessories only (SIM and devices out), ex-VAT, BHD. A row is MET only with the evidence named "
                  "under it; a source that could not be read makes its rows NOT EVIDENCED."]
    for part in card["unread"]:
        lines.append(f"Not read this time: {part}.")
    used = [f"'{k}'" for k in card["defaults_used"]]
    if used:
        names = used[0] if len(used) == 1 else ", ".join(used[:-1]) + " and " + used[-1]
        lines.append(f"(config {names} not set in config.json: the plan's default is used)")
    lines += card["notes"]
    return lines


# ── the command ───────────────────────────────────────────────────────────────

def add_arguments(parser) -> None:
    parser.add_argument("spec", nargs="*", metavar="word",
                        help="a month as YYYY-MM (default: the last whole month); or log <kind> --field value ...")
    parser.add_argument("--ledger", choices=ledger.PREFER, default="auto",
                        help="where the stock ledger is read: auto (the file when it equals production), file, db")
    kpi_log.add_arguments(parser)


def _save(d: date, stem: str, text: str, facts: dict) -> list:
    from scripts.ops import sales
    return sales.save_screen(d, stem, text, facts)


def run(args) -> dict:
    words = [w for raw in (getattr(args, "spec", None) or []) for w in str(raw).split()]
    if words and words[0].lower() == "log":
        return kpi_log.run(args, words[1:])
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    if kpi_log.given_fields(args) or getattr(args, "commit", False) or getattr(args, "token", None):
        return dict(out, ok=False, exit=1, text="The fields, --commit and --token belong to `kpi log`: the scorecard "
                                                "takes one month (YYYY-MM), or nothing for the last whole month.")
    if len(words) > 1:
        return dict(out, ok=False, exit=1, text="Say one month, written YYYY-MM, or nothing for the last whole month.")
    try:
        card = scorecard(words[0] if words else None, prefer=getattr(args, "ledger", "auto") or "auto")
    except KpiInput as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    text = "\n".join(_guarded(ln).rstrip() for ln in render(card))
    files = _save(card["data_date"], f"kpi-{card['month']}", text, jsonable(card))
    return dict(out, data_date=card["data_date"].isoformat(), text=text, files=[str(f) for f in files],
                flags={"month": card["month"], "counts": card["counts"], "plan": card["plan"]["counts"]},
                warnings=[])
