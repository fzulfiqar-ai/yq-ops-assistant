"""The metric dictionary behind the Management Command Centre (release R7b, plan §8, §14, §17).

One place says what every number on the Command Centre means. A metric is a key, a label, a basis
line ("Accessories · ex-VAT · 1–24 Sep · data to 24 Sep") and a function; the modules are lists of
metric keys in the plan's order (Needs attention first). A screen never recomputes a figure: it
shows the tile this module built, basis included.

Definitions (owner rules, 21-Sep and 24-Sep-2026):
  * Sales are Accessories only (v_sales division 'Accessories'), giveaways out, ex-VAT (net_bhd).
    Batelco SIM is a separate chip and never counts toward a target. B2C = the Causeway and
    Roadshow counters (v_sales.channel), everything else is B2B.
  * Business days are Sunday to Thursday (the Bahrain weekend is Friday + Saturday, as the reminder
    clock in app/shop_jobs.py counts it). Public holidays are not deducted.
  * Focus figures are anchored on the last loaded sale day ("data to 24 Sep"), never on the
    calendar: a Monday upload of Thursday's export compares Thursday with Thursday. Marketplace
    order figures are live and anchored on today in Bahrain.
  * Money is summed in SQL (ROUND(... , 3)) or as Decimals here, and leaves as 3-dp floats.

Reads: everything goes through the read-only RPC as yq_readonly (app.db_read exec_sql /
exec_sql_params) over views that role is already granted, plus v_command_orders
(scripts/r7b_command_views_migration.sql). Until that view exists the order figures fall back to
v_shop_orders_agent (test orders cannot be told apart, no accepted rate, the invoice match rate
empty) and the page says so. The rep names for the team table come from the salesmen table
exactly as app.reports.salesman_attainment_result reads them (names only, never a phone).

Caching: the Focus half of a period's payload is kept until the next data upload
(app.reports.invalidate_dashboard_cache calls invalidate(); a 30-minute ceiling is the safety net
for a load that bypasses the API). The marketplace half is live and kept for 60 seconds.
"""
from __future__ import annotations

import calendar
import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

from app.db_read import exec_sql, exec_sql_params
from app.shop_jobs import WEEKEND_DAYS, business_minutes

log = logging.getLogger(__name__)

# Who may open the Command Centre (the API checks it in the route; the SPA hides the page).
COMMAND_ROLES: frozenset[str] = frozenset({"admin", "management"})

PERIODS: dict[str, str] = {
    "today": "Today",
    "7d": "Last 7 days",
    "mtd": "Month to date",
    "last_month": "Last month",
    "quarter": "Quarter to date",
}
DEFAULT_PERIOD = "mtd"

B2C_NOTE = "B2C = Causeway + Roadshow"
SLA_BUSINESS_HOURS = 24          # an order Received for longer than this needs attention
MATCH_AFTER_DAYS = 3             # a delivered order has this long to get its Focus invoice
SILENT_AFTER_BUSINESS_DAYS = 7   # a rep with no Accessories invoice for longer is flagged
DORMANT_AFTER_DAYS = 45
STALE_AFTER_DAYS = 3             # app.reports.STALE_AFTER_DAYS: the same lenient rule
TOP_N = 5

_BAHRAIN = timezone(timedelta(hours=3))
_Q3 = Decimal("0.001")
_Q1 = Decimal("0.1")
OPEN_STATUSES = ("new", "confirmed", "packed", "out_for_delivery")
CONFIRMED_STATUSES = ("confirmed", "packed", "out_for_delivery", "delivered")


# ── numbers ────────────────────────────────────────────────────────────────────

def dec(x) -> Decimal:
    """An exact Decimal for any numeric input (a float goes through str()); None/junk = 0."""
    if x is None:
        return Decimal(0)
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x).strip())
    except Exception:  # noqa: BLE001
        return Decimal(0)


def money(x) -> float:
    """BHD to the fils, half-up, as a float at the JSON edge."""
    return float(dec(x).quantize(_Q3, rounding=ROUND_HALF_UP))


def share(part, whole) -> float | None:
    """part / whole as a percentage to one decimal; None when the whole is zero."""
    w = dec(whole)
    if w == 0:
        return None
    return float((dec(part) / w * 100).quantize(_Q1, rounding=ROUND_HALF_UP))


def change_pct(cur, prev) -> float | None:
    """(cur − prev) / prev as a percentage to one decimal; None when there is nothing to compare."""
    p = dec(prev)
    if p == 0:
        return None
    return float(((dec(cur) - p) / p * 100).quantize(_Q1, rounding=ROUND_HALF_UP))


def _int(x) -> int:
    try:
        return int(float(x or 0))
    except (TypeError, ValueError):
        return 0


# ── dates, business days and the period windows ────────────────────────────────

def parse_day(v) -> date | None:
    if not v:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def parse_ts(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    if len(s) > 3 and s[-3] in "+-" and s[-2:].isdigit():
        s += ":00"                      # the Postgres text form '… 07:24:43.2+00'
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def iso_ts(v) -> str | None:
    d = parse_ts(v)
    return d.isoformat() if d else None


def bahrain_day(ts) -> date | None:
    d = parse_ts(ts)
    return d.astimezone(_BAHRAIN).date() if d else None


def is_business_day(d: date) -> bool:
    return d.weekday() not in WEEKEND_DAYS


def business_days(start: date, end: date) -> int:
    """Sunday–Thursday days from start to end, both included (0 when end < start)."""
    if end < start:
        return 0
    return sum(1 for i in range((end - start).days + 1) if is_business_day(start + timedelta(days=i)))


def nth_business_day(start: date, n: int, cap: date) -> date:
    """The date of the n-th business day counted from `start` (inclusive), never after `cap`."""
    count, d = 0, start
    while d <= cap:
        if is_business_day(d):
            count += 1
            if count >= n:
                return d
        d += timedelta(days=1)
    return cap


def prev_business_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    """The 1st of the month `n` months from d's month."""
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def day_label(d: date | None) -> str:
    return f"{d.day} {d:%b}" if d else ""


def span_label(a: date, b: date) -> str:
    if a == b:
        return day_label(a)
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {b:%b}"
    if a.year == b.year:
        return f"{day_label(a)} – {day_label(b)}"
    return f"{day_label(a)} {a.year} – {day_label(b)} {b.year}"


@dataclass(frozen=True)
class Window:
    start: date
    end: date
    basis: str = ""        # how a comparison window was chosen ("same 18 business days last month")

    @property
    def label(self) -> str:
        return span_label(self.start, self.end)

    @property
    def business_days(self) -> int:
        return business_days(self.start, self.end)

    def contains(self, d: date | None) -> bool:
        return d is not None and self.start <= d <= self.end

    def as_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "label": self.label,
                "business_days": self.business_days, **({"basis": self.basis} if self.basis else {})}


def windows(period: str, anchor: date) -> tuple[Window, Window | None]:
    """(current window, comparison window) for a period ending on `anchor`.

    today      anchor · vs the previous business day
    7d         the 7 days to anchor · vs the 7 days before
    mtd        the 1st to anchor · vs the same number of business days from the 1st of last month
    last_month the whole previous month · vs the month before it
    quarter    the quarter's 1st day to anchor · vs the same business days of the previous quarter
    """
    if period == "today":
        p = prev_business_day(anchor)
        return Window(anchor, anchor), Window(p, p, "the previous business day")
    if period == "7d":
        return (Window(anchor - timedelta(days=6), anchor),
                Window(anchor - timedelta(days=13), anchor - timedelta(days=7), "the 7 days before"))
    if period == "last_month":
        m0 = add_months(anchor, -1)
        p0 = add_months(anchor, -2)
        return Window(m0, month_end(m0)), Window(p0, month_end(p0), "the month before")
    if period == "quarter":
        q0 = quarter_start(anchor)
        n = max(1, business_days(q0, anchor))
        p0 = add_months(q0, -3)
        p1 = nth_business_day(p0, n, q0 - timedelta(days=1))
        return Window(q0, anchor), Window(p0, p1, f"the same {n} business days last quarter")
    # mtd (the default)
    m0 = month_start(anchor)
    n = max(1, business_days(m0, anchor))
    p0 = add_months(anchor, -1)
    p1 = nth_business_day(p0, n, month_end(p0))
    return Window(m0, anchor), Window(p0, p1, f"the same {n} business days last month")


def month_pace(mtd, anchor: date, target) -> dict:
    """Month-to-date pace on business days: projected = MTD ÷ business days so far × business days in
    the month; against the company target when one is set."""
    m0, m1 = month_start(anchor), month_end(anchor)
    done, total = business_days(m0, anchor), business_days(m0, m1)
    mtd_d, tgt = dec(mtd), dec(target)
    projected = (mtd_d / done * total) if done else mtd_d
    left = max(0, total - done)
    need = ((tgt - mtd_d) / left) if (tgt > 0 and left and tgt > mtd_d) else Decimal(0)
    return {
        "month": anchor.strftime("%B %Y"), "mtd_bhd": money(mtd_d), "target_bhd": money(tgt) if tgt > 0 else None,
        "projected_bhd": money(projected), "business_days_done": done, "business_days_total": total,
        "business_days_left": left, "pct_of_target": share(mtd_d, tgt) if tgt > 0 else None,
        "projected_pct_of_target": share(projected, tgt) if tgt > 0 else None,
        "on_track": (projected >= tgt) if tgt > 0 else None,
        "needed_per_business_day_bhd": money(need) if tgt > 0 else None,
    }


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p in 0..100) of the values; None for an empty list."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = max(1, math.ceil(p / 100 * len(vals)))
    return vals[min(k, len(vals)) - 1]


def rep_matches(focus_rep: str | None, name: str | None) -> bool:
    """Focus names a rep '<name> - Acc WH' (or ' - SIM WH'); the salesmen/targets rows say '<name>'.
    The same rule as ATTAINMENT_SQL and app.shop.rep_month_sales."""
    a, b = str(focus_rep or "").strip(), str(name or "").strip()
    return bool(a and b) and (a == b or a.startswith(b + " - "))


def rep_base(focus_rep: str | None) -> str:
    return str(focus_rep or "").split(" - ")[0].strip()


def short(text, n: int = 32) -> str:
    """A name cut to n characters on a word boundary, with an ellipsis when cut."""
    t = " ".join(str(text or "").split())
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0] if " " in t[:n] else t[:n]
    return cut.rstrip(" ,.-(+") + "…"


# ── SQL (every statement runs as yq_readonly through the read-only RPC) ─────────

ANCHOR_SQL = (
    "SELECT (SELECT MAX(sale_date) FROM v_sales)::text AS focus_to, "
    "(SELECT MAX(as_of_date) FROM stock_balance)::text AS stock_as_of, "
    "(SELECT MAX(as_of_date) FROM v_receivables)::text AS ar_as_of, "
    "(SELECT MAX(report_date) FROM v_product_margin)::text AS margin_report_date, "
    "(SELECT value FROM app_settings WHERE key = 'monthly_sales_target_bhd' LIMIT 1) AS target_bhd"
)

# $1 = the windows as JSON [{"k": "cur", "s": "2026-09-01", "e": "2026-09-24"}, ...]; a line counts in
# every window it falls in. Giveaways never count; divisions and channels stay apart.
SALES_SQL = (
    "SELECT w.k, v.channel, v.division, ROUND(COALESCE(SUM(v.net_bhd), 0)::numeric, 3) AS net_bhd, "
    "COUNT(DISTINCT v.invoice_no) AS invoices, "
    "COUNT(DISTINCT v.invoice_no) FILTER (WHERE v.is_cash_customer) AS cash_invoices, "
    "ROUND(COALESCE(SUM(v.net_bhd) FILTER (WHERE v.is_cash_customer), 0)::numeric, 3) AS cash_net_bhd, "
    "ROUND(COALESCE(SUM(v.net_bhd) FILTER (WHERE v.channel = 'B2B' AND NOT v.is_cash_customer), 0)::numeric, 3) "
    "AS named_net_bhd "
    "FROM jsonb_to_recordset($1::jsonb) AS w(k text, s date, e date) "
    "JOIN v_sales v ON v.sale_date BETWEEN w.s AND w.e "
    "WHERE NOT v.is_giveaway GROUP BY 1, 2, 3"
)

# 13 Bahrain weeks (Sunday–Saturday) to $1: this week so far and the 12 before it
TREND_SQL = (
    "SELECT (v.sale_date - EXTRACT(DOW FROM v.sale_date)::int)::text AS week_start, "
    "ROUND(COALESCE(SUM(v.net_bhd), 0)::numeric, 3) AS net_bhd "
    "FROM v_sales v WHERE v.division = 'Accessories' AND NOT v.is_giveaway AND v.sale_date <= $1::date "
    "AND v.sale_date >= ($1::date - EXTRACT(DOW FROM $1::date)::int) - 84 GROUP BY 1 ORDER BY 1"
)

# per Focus rep name: the last Accessories invoice ever, and this month's sales with the named-shop part
REPS_SQL = (
    "SELECT salesman_resolved AS rep, MAX(sale_date)::text AS last_invoice, "
    "ROUND(COALESCE(SUM(net_bhd) FILTER (WHERE sale_date >= $1::date), 0)::numeric, 3) AS month_net_bhd, "
    "ROUND(COALESCE(SUM(net_bhd) FILTER (WHERE sale_date >= $1::date AND NOT is_cash_customer), 0)::numeric, 3) "
    "AS month_named_bhd "
    "FROM v_sales WHERE division = 'Accessories' AND NOT is_giveaway AND sale_date <= $2::date "
    "AND salesman_resolved IS NOT NULL GROUP BY 1"
)

# named B2B accounts (not Cash Customer): first / last Accessories invoice and the 12 months to $1
CUSTOMERS_SQL = (
    "SELECT customer_name, MIN(sale_date)::text AS first_invoice, MAX(sale_date)::text AS last_invoice, "
    "ROUND(COALESCE(SUM(net_bhd) FILTER (WHERE sale_date > $1::date - 365), 0)::numeric, 3) AS net_12m "
    "FROM v_sales WHERE channel = 'B2B' AND NOT is_cash_customer AND NOT is_giveaway "
    "AND division = 'Accessories' AND customer_name IS NOT NULL AND sale_date <= $1::date GROUP BY 1"
)

# per item: the last 30 days to $1, the 30 before, and the whole 60
MOVERS_SQL = (
    "SELECT item_name, "
    "COALESCE(SUM(quantity) FILTER (WHERE sale_date > $1::date - 30), 0) AS qty_30, "
    "ROUND(COALESCE(SUM(net_bhd) FILTER (WHERE sale_date > $1::date - 30), 0)::numeric, 3) AS net_30, "
    "COALESCE(SUM(quantity) FILTER (WHERE sale_date <= $1::date - 30), 0) AS qty_prev, "
    "ROUND(COALESCE(SUM(net_bhd) FILTER (WHERE sale_date <= $1::date - 30), 0)::numeric, 3) AS net_prev, "
    "COALESCE(SUM(quantity), 0) AS qty_60, ROUND(COALESCE(SUM(net_bhd), 0)::numeric, 3) AS net_60 "
    "FROM v_sales WHERE division = 'Accessories' AND NOT is_giveaway AND item_name IS NOT NULL "
    "AND sale_date > $1::date - 60 AND sale_date <= $1::date GROUP BY 1"
)

# the latest stock snapshot per item with its unit cost (v_product_economics: the MRN receipt cost
# first, the last purchase cost otherwise — one row per item)
STOCK_SQL = (
    "SELECT h.item_name, h.current_stock, h.sold_90d, h.days_cover, e.cost_bhd, e.cost_source "
    "FROM v_stock_health h LEFT JOIN v_product_economics e ON e.item_name = h.item_name "
    "WHERE h.division = 'Accessories'"
)

# landed margin: ex-VAT sales against the MRN landed cost, 12 months to $1, with its coverage
LANDED_SQL = (
    "SELECT ROUND(COALESCE(SUM(s.net_bhd) FILTER (WHERE e.cost_bhd IS NOT NULL), 0)::numeric, 3) AS costed_net_bhd, "
    "ROUND(COALESCE(SUM(s.net_bhd - s.quantity * e.cost_bhd) FILTER (WHERE e.cost_bhd IS NOT NULL), 0)::numeric, 3) "
    "AS gp_bhd, ROUND(COALESCE(SUM(s.net_bhd), 0)::numeric, 3) AS acc_net_bhd "
    "FROM v_sales s LEFT JOIN v_product_economics e ON e.item_name = s.item_name AND e.cost_source = 'mrn' "
    "WHERE s.division = 'Accessories' AND NOT s.is_giveaway AND s.item_name IS NOT NULL "
    "AND s.sale_date > $1::date - 365 AND s.sale_date <= $1::date"
)

RECEIVABLES_SQL = (
    "SELECT account, outstanding_bhd, overdue_bhd, over_90_bhd, last_receipt_date::text AS last_receipt_date "
    "FROM v_receivables"
)
# Focus's own Grand Total for the snapshot on screen (economics_v2_migration.sql; absent before it)
AR_TOTALS_SQL = (
    "SELECT as_of_date::text AS as_of_date, focus_total_bhd, focus_over90_bhd, rows_total_bhd "
    "FROM ar_ageing_totals WHERE as_of_date = (SELECT MAX(as_of_date) FROM v_receivables) LIMIT 1"
)
# who last invoiced each account on the ageing: the account's owner in practice (Focus has no field)
AR_OWNER_SQL = (
    "SELECT DISTINCT ON (v.customer_name) v.customer_name AS account, v.salesman_resolved AS rep, "
    "v.channel, v.sale_date::text AS last_sale FROM v_sales v "
    "WHERE v.customer_name IN (SELECT account FROM v_receivables) "
    "ORDER BY v.customer_name, v.sale_date DESC, v.line_id DESC"
)

_ORDER_COLS = ("id, order_no, status, source, salesman_id, salesman_name, is_test, created_at::text AS created_at, "
               "confirmed_at::text AS confirmed_at, delivered_at::text AS delivered_at, "
               "cancelled_at::text AS cancelled_at, total_bhd, total_confirmed_bhd, units_ordered, units_confirmed, "
               "has_invoice_no, focus_links_n")
_ORDER_WHERE = ("WHERE created_at >= $1::timestamptz "
                "OR status IN ('new', 'confirmed', 'packed', 'out_for_delivery')")
ORDERS_SQL = f"SELECT {_ORDER_COLS} FROM v_command_orders {_ORDER_WHERE}"
# before r7b_command_views_migration.sql: the PII-free agent view (no is_test, no confirmed units)
ORDERS_FALLBACK_SQL = (
    "SELECT id, order_no, status, source, NULL::bigint AS salesman_id, salesman_name, created_at::text AS created_at, "
    "confirmed_at::text AS confirmed_at, delivered_at::text AS delivered_at, cancelled_at::text AS cancelled_at, "
    f"total_bhd, units_count AS units_ordered FROM v_shop_orders_agent {_ORDER_WHERE}"
)
# $1 = now: delivered orders older than MATCH_AFTER_DAYS and how many carry a confirmed Focus link
MATCH_SQL = (
    "SELECT COUNT(*) FILTER (WHERE d) AS eligible, COUNT(*) FILTER (WHERE d AND focus_links_n > 0) AS matched, "
    "ROUND(COALESCE(SUM(COALESCE(total_confirmed_bhd, total_bhd)) FILTER (WHERE d AND focus_links_n = 0), 0)::numeric, 3) "
    "AS unmatched_bhd, COUNT(*) FILTER (WHERE status IN ('new', 'confirmed', 'packed', 'out_for_delivery') "
    "AND (focus_links_n > 0 OR has_invoice_no)) AS invoiced_open, MAX(created_at)::text AS last_order_at "
    "FROM (SELECT *, (status = 'delivered' AND COALESCE(delivered_at, created_at) < $1::timestamptz - interval "
    f"'{MATCH_AFTER_DAYS} days') AS d FROM v_command_orders WHERE NOT is_test) o"
)
LAST_ORDER_SQL = "SELECT MAX(created_at)::text AS last_order_at FROM v_shop_orders_agent"


# ── the reader: every source once per build, in parallel, failures kept apart ────

def _default_salesmen() -> list[dict]:
    """Rep names for the team table, read exactly as app.reports.salesman_attainment_result does."""
    from app.database import get_client
    return (get_client().table("salesmen").select("id,name,focus_name,is_active,referral_code")
            .limit(500).execute().data or [])


def _is_missing_relation(exc: BaseException) -> bool:
    s = str(exc).lower()
    return "does not exist" in s or "42p01" in s or "undefined_table" in s or "42703" in s


@dataclass
class Reader:
    """The sources one build reads. `q` / `qp` default to the read-only RPC; tests pass stand-ins."""
    q: Callable[[str], list[dict]] = None            # type: ignore[assignment]
    qp: Callable[[str, list], list[dict]] = None     # type: ignore[assignment]
    salesmen_fn: Callable[[], list[dict]] = None     # type: ignore[assignment]
    now: datetime = None                             # type: ignore[assignment]
    memo: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def __post_init__(self):
        self.q = self.q or exec_sql
        self.qp = self.qp or exec_sql_params
        self.salesmen_fn = self.salesmen_fn or _default_salesmen
        self.now = self.now or datetime.now(timezone.utc)
        self._lock = threading.Lock()

    @property
    def today(self) -> date:
        return self.now.astimezone(_BAHRAIN).date()

    def run(self, name: str, fn: Callable[[], object]):
        """Run one source (memoised); a failure is recorded and answers None."""
        with self._lock:
            if name in self.memo:
                return self.memo[name]
        try:
            val = fn()
        except Exception as e:  # noqa: BLE001 -- one source never costs the page
            log.warning("command centre: %s unavailable: %s", name, str(e)[:200])
            with self._lock:
                self.errors[name] = str(e)[:200]
            val = None
        with self._lock:
            self.memo[name] = val
        return val

    def many(self, jobs: dict[str, Callable[[], object]], workers: int = 6) -> None:
        todo = {k: f for k, f in jobs.items() if k not in self.memo}
        if not todo:
            return
        if len(todo) == 1:
            k, f = next(iter(todo.items()))
            self.run(k, f)
            return
        with ThreadPoolExecutor(max_workers=min(workers, len(todo)), thread_name_prefix="cmd") as ex:
            list(ex.map(lambda kv: self.run(kv[0], kv[1]), todo.items()))

    def get(self, name: str):
        return self.memo.get(name)


# ── the metric dictionary ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    module: str
    basis: str                       # a template over Ctx.fmt() (see Ctx.fields)
    fn: Callable[["Ctx"], dict | None]
    unit: str = "bhd"                # bhd | pct | count | hours | table | list
    drill: tuple[str, str] | None = None   # (route, label)


METRICS: dict[str, Metric] = {}


def metric(key: str, label: str, module: str, basis: str, unit: str = "bhd",
           drill: tuple[str, str] | None = None):
    def deco(fn):
        if key in METRICS:
            raise ValueError(f"metric {key} defined twice")
        METRICS[key] = Metric(key, label, module, basis, fn, unit, drill)
        return fn
    return deco


@dataclass
class Ctx:
    """What a metric function reads: the reader, the period and the anchors."""
    r: Reader
    period: str
    focus_to: date | None
    cur: Window | None
    cmp: Window | None
    live: Window
    anchors: dict

    def fields(self) -> dict:
        return {
            "focus_to": day_label(self.focus_to) or "no data",
            "window": self.cur.label if self.cur else "no data",
            "compare": (self.cmp.basis or self.cmp.label) if self.cmp else "",
            "compare_span": self.cmp.label if self.cmp else "",
            "month": self.focus_to.strftime("%B") if self.focus_to else "",
            "live": self.live.label,
            "stock_as_of": day_label(parse_day(self.anchors.get("stock_as_of"))) or "no snapshot",
            "ar_as_of": day_label(parse_day(self.anchors.get("ar_as_of"))) or "no snapshot",
            "margin_date": day_label(parse_day(self.anchors.get("margin_report_date"))) or "no report",
            "tests": ("test orders not yet told apart (r7b migration pending)"
                      if self.r.get("orders_source") == "v_shop_orders_agent" else "test orders left out"),
        }

    def fmt(self, template: str) -> str:
        try:
            return template.format(**self.fields())
        except (KeyError, IndexError):
            return template


# ── sources ────────────────────────────────────────────────────────────────────

def _sales_rows(ctx: Ctx) -> list[dict] | None:
    return ctx.r.get("sales")


def _sales_sum(ctx: Ctx, window: str, division: str = "Accessories", channel: str | None = None,
               col: str = "net_bhd") -> Decimal:
    rows = _sales_rows(ctx) or []
    return sum((dec(r.get(col)) for r in rows
                if r.get("k") == window and r.get("division") == division
                and (channel is None or r.get("channel") == channel)), Decimal(0))


def _orders(ctx: Ctx) -> list[dict]:
    rows = ctx.r.get("orders") or []
    return [o for o in rows if not o.get("is_test")]


def _orders_view(ctx: Ctx) -> bool:
    """True when the orders came from v_command_orders (the r7b migration is applied)."""
    return ctx.r.get("orders_source") == "v_command_orders"


def _live_orders(ctx: Ctx) -> list[dict]:
    return [o for o in _orders(ctx) if ctx.live.contains(bahrain_day(o.get("created_at")))]


def _order_value(o: dict) -> Decimal:
    v = o.get("total_confirmed_bhd")
    return dec(v if v is not None else o.get("total_bhd"))


def _order_age_business_h(o: dict, now: datetime, until_field: str | None = None) -> float | None:
    start = parse_ts(o.get("created_at"))
    end = parse_ts(o.get(until_field)) if until_field else now
    if not start or not end:
        return None
    return round(business_minutes(start, end) / 60, 1)


# ══ Sales ══════════════════════════════════════════════════════════════════════

@metric("sales.accessories", "Accessories sales", "sales",
        "Accessories · ex-VAT · {window} vs {compare} · data to {focus_to}", "bhd", ("/sales", "By rep, customer and item"))
def _sales_accessories(ctx: Ctx) -> dict | None:
    if _sales_rows(ctx) is None or not ctx.cur:
        return None
    cur = _sales_sum(ctx, "cur")
    prev = _sales_sum(ctx, "cmp") if ctx.cmp else None
    return {"value": money(cur), "compare": {"value": money(prev), "label": ctx.cmp.label} if ctx.cmp else None,
            "delta_pct": change_pct(cur, prev) if prev is not None else None,
            "invoices": _int(sum(_int(r.get("invoices")) for r in _sales_rows(ctx) or []
                                 if r.get("k") == "cur" and r.get("division") == "Accessories"))}


@metric("sales.pace", "Month pace", "sales",
        "Accessories · ex-VAT · {month} on business days (Fri, Sat off) · company target from settings · "
        "data to {focus_to}", "bhd")
def _sales_pace(ctx: Ctx) -> dict | None:
    if _sales_rows(ctx) is None or not ctx.focus_to:
        return None
    out = month_pace(_sales_sum(ctx, "month"), ctx.focus_to, ctx.anchors.get("target_bhd"))
    return {"value": out["projected_bhd"], **out}


@metric("sales.channels", "B2B and B2C", "sales",
        "Accessories · ex-VAT · {window} · " + B2C_NOTE, "bhd")
def _sales_channels(ctx: Ctx) -> dict | None:
    if _sales_rows(ctx) is None:
        return None
    b2b, b2c = _sales_sum(ctx, "cur", channel="B2B"), _sales_sum(ctx, "cur", channel="B2C")
    return {"value": money(b2b + b2c), "chips": [
        {"label": "B2B", "value": money(b2b), "unit": "bhd", "share_pct": share(b2b, b2b + b2c)},
        {"label": "B2C", "value": money(b2c), "unit": "bhd", "share_pct": share(b2c, b2b + b2c)},
    ]}


@metric("sales.sim", "Batelco SIM", "sales",
        "SIM · ex-VAT · {window} · shown apart, never counted toward a target", "bhd")
def _sales_sim(ctx: Ctx) -> dict | None:
    if _sales_rows(ctx) is None:
        return None
    cur = _sales_sum(ctx, "cur", division="SIM")
    prev = _sales_sum(ctx, "cmp", division="SIM") if ctx.cmp else None
    return {"value": money(cur), "delta_pct": change_pct(cur, prev) if prev is not None else None}


@metric("sales.trend", "13-week trend", "sales",
        "Accessories · ex-VAT · weeks Sunday to Saturday · this week so far · data to {focus_to}", "series")
def _sales_trend(ctx: Ctx) -> dict | None:
    rows = ctx.r.get("trend")
    if rows is None or not ctx.focus_to:
        return None
    by = {str(r.get("week_start"))[:10]: dec(r.get("net_bhd")) for r in rows}
    wk0 = ctx.focus_to - timedelta(days=(ctx.focus_to.weekday() + 1) % 7)     # this Sunday
    series = []
    for i in range(12, -1, -1):
        ws = wk0 - timedelta(days=7 * i)
        series.append({"week_start": ws.isoformat(), "label": day_label(ws), "value": money(by.get(ws.isoformat(), 0)),
                       "partial": i == 0})
    return {"value": series[-1]["value"], "series": series}


# ══ Order health (live) ════════════════════════════════════════════════════════

_ORDERS_BASIS = "Marketplace orders placed {live} · {tests}"


@metric("orders.funnel", "Placed → Confirmed → Delivered", "orders", _ORDERS_BASIS, "funnel")
def _orders_funnel(ctx: Ctx) -> dict | None:
    if ctx.r.get("orders") is None:
        return None
    rows = _live_orders(ctx)

    def stage(sel: Callable[[dict], bool], units_field: str, value: Callable[[dict], Decimal]) -> dict:
        got = [o for o in rows if sel(o)]
        units = sum(_int(o.get(units_field) if o.get(units_field) is not None else o.get("units_ordered")) for o in got)
        return {"orders": len(got), "units": units, "bhd": money(sum((value(o) for o in got), Decimal(0)))}

    submitted = stage(lambda o: True, "units_ordered", lambda o: dec(o.get("total_bhd")))
    confirmed = stage(lambda o: o.get("status") in CONFIRMED_STATUSES, "units_confirmed", _order_value)
    delivered = stage(lambda o: o.get("status") == "delivered", "units_confirmed", _order_value)
    return {"value": submitted["orders"], "stages": [
        {"key": "submitted", "label": "Placed", **submitted},
        {"key": "confirmed", "label": "Confirmed", **confirmed},
        {"key": "delivered", "label": "Delivered", **delivered},
    ], "cancelled": sum(1 for o in rows if o.get("status") == "cancelled"),
        "open": sum(1 for o in rows if o.get("status") == "new")}


@metric("orders.accepted", "Accepted rate", "orders",
        "Units accepted at confirmation ÷ units ordered · orders placed {live} and confirmed", "pct")
def _orders_accepted(ctx: Ctx) -> dict | None:
    if ctx.r.get("orders") is None:
        return None
    if not _orders_view(ctx):
        return {"available": False, "note": "Needs the Command Centre view (r7b migration) to read confirmed quantities."}
    got = [o for o in _live_orders(ctx) if o.get("status") in CONFIRMED_STATUSES and o.get("units_confirmed") is not None]
    ordered = sum(_int(o.get("units_ordered")) for o in got)
    accepted = sum(_int(o.get("units_confirmed")) for o in got)
    return {"value": share(accepted, ordered), "units_ordered": ordered, "units_accepted": accepted, "orders": len(got)}


@metric("orders.confirm_time", "Time to confirm", "orders",
        "Received → Confirmed · business hours (Fri, Sat off) · orders placed {live} · still-open orders at their age now",
        "hours")
def _orders_confirm_time(ctx: Ctx) -> dict | None:
    if ctx.r.get("orders") is None:
        return None
    hours, open_n = [], 0
    for o in _live_orders(ctx):
        if o.get("confirmed_at"):
            h = _order_age_business_h(o, ctx.r.now, "confirmed_at")
        elif o.get("status") == "new":
            h = _order_age_business_h(o, ctx.r.now)
            open_n += 1
        else:
            continue          # cancelled before anyone confirmed it: no confirm time
        if h is not None:
            hours.append(h)
    return {"value": percentile(hours, 50), "p50_hours": percentile(hours, 50), "p90_hours": percentile(hours, 90),
            "orders": len(hours), "open_included": open_n}


def waiting_orders(ctx: Ctx) -> list[dict]:
    """Every Received order older than SLA_BUSINESS_HOURS business hours (any date), oldest first."""
    out = []
    for o in _orders(ctx):
        if o.get("status") != "new":
            continue
        h = _order_age_business_h(o, ctx.r.now)
        if h is not None and h > SLA_BUSINESS_HOURS:
            out.append({"id": o.get("id"), "order_no": o.get("order_no"), "rep": o.get("salesman_name") or "Unassigned",
                        "hours": h, "bhd": money(o.get("total_bhd"))})
    return sorted(out, key=lambda x: -x["hours"])


@metric("orders.waiting", "Waiting over 24 business hours", "orders",
        "Received and not yet confirmed for over 24 business hours · as of now", "count",
        ("/shop-orders", "Open the order desk"))
def _orders_waiting(ctx: Ctx) -> dict | None:
    if ctx.r.get("orders") is None:
        return None
    w = waiting_orders(ctx)
    by_rep: dict[str, dict] = {}
    for x in w:
        g = by_rep.setdefault(x["rep"], {"rep": x["rep"], "orders": 0, "bhd": Decimal(0)})
        g["orders"] += 1
        g["bhd"] += dec(x["bhd"])
    reps = sorted(({"rep": g["rep"], "orders": g["orders"], "bhd": money(g["bhd"])} for g in by_rep.values()),
                  key=lambda g: (-g["orders"], g["rep"]))
    return {"value": len(w), "bhd": money(sum((dec(x["bhd"]) for x in w), Decimal(0))),
            "oldest_hours": w[0]["hours"] if w else None, "by_rep": reps, "waiting": w[:10]}


@metric("orders.match_rate", "Invoice match", "orders",
        "Delivered orders over 3 days old with a confirmed Focus invoice link · all time", "pct",
        ("/shop-orders", "Focus links"))
def _orders_match(ctx: Ctx) -> dict | None:
    m = match_summary(ctx.r)
    if not m["available"]:
        return {"available": False, "value": None, "note": m["note"]}
    return {"value": m["pct"], "matched": m["matched"], "eligible": m["eligible"], "unmatched_bhd": m["unmatched_bhd"]}


@metric("orders.self_order", "Ordered by the shop itself", "orders",
        "Share of marketplace orders placed by the shop, not by a rep · {live}", "pct")
def _orders_self(ctx: Ctx) -> dict | None:
    if ctx.r.get("orders") is None:
        return None
    rows = _live_orders(ctx)
    own = sum(1 for o in rows if (o.get("source") or "") != "salesman")
    return {"value": share(own, len(rows)), "orders": len(rows), "by_shop": own}


def match_summary(r: Reader) -> dict:
    """The invoice match rate (freshness chip + order health): 0 / empty before the r7b view."""
    row = r.get("match")
    if r.get("orders_source") != "v_command_orders" or not row:
        return {"available": False, "pct": None, "matched": 0, "eligible": 0, "unmatched_bhd": 0.0,
                "invoiced_open": 0, "note": "Invoice matching shows once the Command Centre view (r7b migration) is applied."}
    eligible, matched = _int(row.get("eligible")), _int(row.get("matched"))
    return {"available": True, "pct": share(matched, eligible), "matched": matched, "eligible": eligible,
            "unmatched_bhd": money(row.get("unmatched_bhd")), "invoiced_open": _int(row.get("invoiced_open")),
            "note": None if eligible else "No delivered order is old enough to need an invoice yet."}


# ══ Team ═══════════════════════════════════════════════════════════════════════

def _rep_last_invoice(ctx: Ctx, name: str) -> tuple[date | None, Decimal, Decimal]:
    """(last Accessories invoice ever, this month's net, this month's named-shop net) over every
    Focus name that belongs to `name` ('<name>' or '<name> - …')."""
    last, net, named = None, Decimal(0), Decimal(0)
    for r in ctx.r.get("reps") or []:
        if rep_matches(r.get("rep"), name):
            d = parse_day(r.get("last_invoice"))
            if d and (last is None or d > last):
                last = d
            net += dec(r.get("month_net_bhd"))
            named += dec(r.get("month_named_bhd"))
    return last, net, named


def _silence_days(ctx: Ctx, last: date | None) -> int | None:
    if not ctx.focus_to or not last:
        return None
    return business_days(last + timedelta(days=1), ctx.focus_to)


@metric("team.reps", "Rep table", "team",
        "Accessories · ex-VAT · {month} to {focus_to} · tiers from the target sheet · marketplace orders placed in {month}",
        "table", ("/command/team", "Every rep"))
def _team_reps(ctx: Ctx) -> dict | None:
    att = ctx.r.get("attainment")
    if att is None:
        return None
    month = Window(month_start(ctx.focus_to), month_end(ctx.focus_to)) if ctx.focus_to else None
    orders = [o for o in _orders(ctx) if o.get("status") != "cancelled"
              and month is not None and month.contains(bahrain_day(o.get("created_at")))]
    rows = []
    for a in att:
        last, _net, named = _rep_last_invoice(ctx, a.get("salesman"))
        mine = [o for o in orders
                if (a.get("salesman_id") is not None and o.get("salesman_id") == a.get("salesman_id"))
                or (o.get("salesman_id") is None and (o.get("salesman_name") or "") in (a.get("name"), a.get("salesman")))]
        nt = a.get("next_tier") or None
        rows.append({
            "salesman": a.get("salesman"), "name": a.get("name") or a.get("salesman"),
            "salesman_id": a.get("salesman_id"), "net_bhd": money(a.get("net_bhd")),
            "no_target": bool(a.get("no_target")), "tier_reached": a.get("tier_reached"),
            "next_tier": nt.get("n") if isinstance(nt, dict) else None,
            "gap_to_next_bhd": money(nt.get("gap_bhd")) if isinstance(nt, dict) else None,
            "progress_pct": a.get("progress_pct"), "invoices": _int(a.get("invoices")), "shops": _int(a.get("shops")),
            "named_share_pct": share(named, a.get("net_bhd")),
            "last_invoice": last.isoformat() if last else None,
            "business_days_since_invoice": _silence_days(ctx, last),
            "marketplace_orders": len(mine),
            "marketplace_bhd": money(sum((_order_value(o) for o in mine), Decimal(0))),
        })
    rows.sort(key=lambda x: (-x["net_bhd"], str(x["salesman"])))
    return {"value": len(rows), "rows": rows,
            "totals": {"net_bhd": money(sum((dec(x["net_bhd"]) for x in rows), Decimal(0))),
                       "marketplace_orders": sum(x["marketplace_orders"] for x in rows)}}


def silent_reps(ctx: Ctx) -> list[dict]:
    """Active reps whose last Accessories invoice is more than SILENT_AFTER_BUSINESS_DAYS business
    days before the data date (or who have none on record)."""
    out = []
    for s in ctx.r.get("salesmen") or []:
        fn = str(s.get("focus_name") or "").strip()
        if not s.get("is_active") or not fn:
            continue
        last, _n, _m = _rep_last_invoice(ctx, fn)
        days = _silence_days(ctx, last)
        if last is None or (days is not None and days > SILENT_AFTER_BUSINESS_DAYS):
            out.append({"rep": s.get("name") or fn, "last_invoice": last.isoformat() if last else None,
                        "business_days": days})
    return sorted(out, key=lambda x: (x["last_invoice"] or "", x["rep"]))


# ══ Customers ═════════════════════════════════════════════════════════════════

def _customers(ctx: Ctx) -> list[dict] | None:
    return ctx.r.get("customers")


@metric("customers.active", "Active accounts", "customers",
        "Named B2B accounts with an Accessories invoice in the last 30 / 60 / 90 days to {focus_to}", "count")
def _customers_active(ctx: Ctx) -> dict | None:
    rows = _customers(ctx)
    if rows is None or not ctx.focus_to:
        return None
    ages = [(ctx.focus_to - d).days for d in (parse_day(r.get("last_invoice")) for r in rows) if d]
    n = {k: sum(1 for a in ages if a < k) for k in (30, 60, 90)}
    new = sum(1 for r in rows if (d := parse_day(r.get("first_invoice"))) and (ctx.focus_to - d).days < 30)
    return {"value": n[30], "chips": [{"label": "30 days", "value": n[30], "unit": "count"},
                                      {"label": "60 days", "value": n[60], "unit": "count"},
                                      {"label": "90 days", "value": n[90], "unit": "count"}],
            "new_30d": new, "accounts_on_record": len(rows)}


@metric("customers.dormant", "Dormant high-value accounts", "customers",
        "Named B2B accounts with no Accessories invoice for over 45 days, by their last 12 months' ex-VAT sales · "
        "data to {focus_to}", "list", ("/command/customers", "Every dormant account"))
def _customers_dormant(ctx: Ctx) -> dict | None:
    rows = _customers(ctx)
    if rows is None or not ctx.focus_to:
        return None
    dormant = []
    for r in rows:
        d = parse_day(r.get("last_invoice"))
        if d and (ctx.focus_to - d).days > DORMANT_AFTER_DAYS and dec(r.get("net_12m")) > 0:
            dormant.append({"account": r.get("customer_name"), "last_invoice": d.isoformat(),
                            "days": (ctx.focus_to - d).days, "net_12m_bhd": money(r.get("net_12m"))})
    dormant.sort(key=lambda x: (-x["net_12m_bhd"], x["account"] or ""))
    return {"value": len(dormant), "bhd": money(sum((dec(x["net_12m_bhd"]) for x in dormant), Decimal(0))),
            "items": dormant[:TOP_N], "all": dormant[:50]}


@metric("customers.concentration", "Top-10 concentration", "customers",
        "Top 10 named accounts' share of named B2B Accessories sales · ex-VAT · 12 months to {focus_to}", "pct")
def _customers_concentration(ctx: Ctx) -> dict | None:
    rows = _customers(ctx)
    if rows is None:
        return None
    vals = sorted((dec(r.get("net_12m")) for r in rows), reverse=True)
    total = sum(vals, Decimal(0))
    top = sum(vals[:10], Decimal(0))
    return {"value": share(top, total), "top10_bhd": money(top), "named_bhd": money(total)}


@metric("customers.cash", "Cash Customer share", "customers",
        "Walk-in 'Cash Customer' share of Accessories invoices and ex-VAT sales · {window}", "pct")
def _customers_cash(ctx: Ctx) -> dict | None:
    if _sales_rows(ctx) is None:
        return None
    inv = sum(_int(r.get("invoices")) for r in _sales_rows(ctx) or [] if r.get("k") == "cur" and r.get("division") == "Accessories")
    cash_inv = sum(_int(r.get("cash_invoices")) for r in _sales_rows(ctx) or []
                   if r.get("k") == "cur" and r.get("division") == "Accessories")
    net, cash = _sales_sum(ctx, "cur"), _sales_sum(ctx, "cur", col="cash_net_bhd")
    return {"value": share(cash_inv, inv), "invoices_pct": share(cash_inv, inv), "sales_pct": share(cash, net),
            "cash_invoices": cash_inv, "invoices": inv}


# ══ Products & stock ══════════════════════════════════════════════════════════

@metric("products.movers", "Top movers", "products",
        "Accessories · ex-VAT · last 30 days vs the 30 before · data to {focus_to}", "list",
        ("/inventory", "Inventory"))
def _products_movers(ctx: Ctx) -> dict | None:
    rows = ctx.r.get("movers")
    if rows is None:
        return None
    moves = [{"item": r.get("item_name"), "net_30_bhd": money(r.get("net_30")), "net_prev_bhd": money(r.get("net_prev")),
              "delta_bhd": money(dec(r.get("net_30")) - dec(r.get("net_prev"))),
              "qty_30": _int(r.get("qty_30")), "qty_prev": _int(r.get("qty_prev"))} for r in rows]
    rising = sorted((m for m in moves if m["delta_bhd"] > 0), key=lambda m: (-m["delta_bhd"], m["item"] or ""))[:TOP_N]
    falling = sorted((m for m in moves if m["delta_bhd"] < 0), key=lambda m: (m["delta_bhd"], m["item"] or ""))[:TOP_N]
    return {"value": len(rising), "rising": rising, "falling": falling}


def sold_out_with_demand(ctx: Ctx) -> list[dict] | None:
    stock, movers = ctx.r.get("stock"), ctx.r.get("movers")
    if stock is None or movers is None:
        return None
    sold = {r.get("item_name"): r for r in movers if dec(r.get("qty_60")) > 0}
    out = []
    for s in stock:
        m = sold.get(s.get("item_name"))
        if m and dec(s.get("current_stock")) <= 0:
            out.append({"item": s.get("item_name"), "qty_60": _int(m.get("qty_60")), "net_60_bhd": money(m.get("net_60"))})
    return sorted(out, key=lambda x: (-x["net_60_bhd"], x["item"] or ""))


@metric("products.sold_out", "Sold out with demand", "products",
        "Out of stock in the {stock_as_of} snapshot and sold in the 60 days to {focus_to} · Accessories · ex-VAT",
        "count", ("/inventory", "Reorder from Inventory"))
def _products_sold_out(ctx: Ctx) -> dict | None:
    rows = sold_out_with_demand(ctx)
    if rows is None:
        return None
    return {"value": len(rows), "bhd": money(sum((dec(x["net_60_bhd"]) for x in rows), Decimal(0))),
            "items": rows[:TOP_N]}


@metric("products.stock_shape", "Stock shape", "products",
        "Accessories stock at cost (latest MRN landed cost, else last purchase) · snapshot {stock_as_of} · "
        "cover = stock ÷ the last 90 days' daily sales", "bhd", ("/inventory", "Inventory"))
def _products_stock_shape(ctx: Ctx) -> dict | None:
    rows = ctx.r.get("stock")
    if rows is None:
        return None
    total = slow = dead = Decimal(0)
    dead_n = slow_n = uncosted = held = 0
    for s in rows:
        qty = dec(s.get("current_stock"))
        if qty <= 0:
            continue
        held += 1
        if s.get("cost_bhd") is None:
            uncosted += 1
            continue
        v = qty * dec(s.get("cost_bhd"))
        total += v
        if dec(s.get("sold_90d")) <= 0:
            dead += v
            dead_n += 1
        elif s.get("days_cover") is not None and dec(s.get("days_cover")) > 365:
            slow += v
            slow_n += 1
    return {"value": money(total), "chips": [
        {"label": "Over a year of cover", "value": money(slow), "unit": "bhd", "share_pct": share(slow, total), "items": slow_n},
        {"label": "No sale in 90 days", "value": money(dead), "unit": "bhd", "share_pct": share(dead, total), "items": dead_n},
    ], "items_held": held, "items_uncosted": uncosted}


# ══ Profitability ═════════════════════════════════════════════════════════════

@metric("profit.official", "Gross margin (official)", "profitability",
        "Ex-VAT sales vs Focus COGS · profitability report of {margin_date} · every item costed", "pct",
        ("/margins", "Margins by item"))
def _profit_official(ctx: Ctx) -> dict | None:
    tot = ctx.r.get("margin_totals")
    if tot is None:
        return None
    return {"value": share(tot.get("gp_ex"), tot.get("net_ex")), "gp_bhd": money(tot.get("gp_ex")),
            "net_ex_vat_bhd": money(tot.get("net_ex")), "items": _int(tot.get("n")), "below_cost": _int(tot.get("below"))}


@metric("profit.landed", "Landed margin", "profitability",
        "Ex-VAT sales vs landed cost (latest MRN receipt per item) · 12 months to {focus_to} · items with a receipt only",
        "pct", ("/prices", "Price tracker"))
def _profit_landed(ctx: Ctx) -> dict | None:
    row = ctx.r.get("landed")
    if not row:
        return None
    net, gp, all_net = dec(row.get("costed_net_bhd")), dec(row.get("gp_bhd")), dec(row.get("acc_net_bhd"))
    return {"value": share(gp, net), "gp_bhd": money(gp), "costed_net_bhd": money(net),
            "coverage_pct": share(net, all_net)}


@metric("profit.below_cost", "Selling below cost", "profitability",
        "Items whose ex-VAT sales are under their Focus COGS · profitability report of {margin_date}", "list",
        ("/margins", "Margins by item"))
def _profit_below(ctx: Ctx) -> dict | None:
    rows = ctx.r.get("below_cost")
    tot = ctx.r.get("margin_totals")
    if rows is None:
        return None
    items = [{"item": r.get("item_name"), "gp_bhd": money(r.get("gp_ex_vat_bhd")),
              "margin_pct": float(r["margin_ex_vat_pct"]) if r.get("margin_ex_vat_pct") is not None else None}
             for r in rows[:TOP_N]]
    return {"value": _int((tot or {}).get("below")) if tot else len(rows), "items": items}


# ══ Receivables ═══════════════════════════════════════════════════════════════

def _ar_rows(ctx: Ctx) -> list[dict] | None:
    return ctx.r.get("receivables")


@metric("ar.total", "Receivables", "receivables",
        "Focus customer ageing as on {ar_as_of}", "bhd", ("/receivables", "Every account"))
def _ar_total(ctx: Ctx) -> dict | None:
    rows = _ar_rows(ctx)
    if rows is None:
        return None
    row_sum = sum((dec(r.get("outstanding_bhd")) for r in rows), Decimal(0))
    tot = ctx.r.get("ar_totals")
    if tot and tot.get("focus_total_bhd") is not None:
        gap = row_sum - dec(tot.get("focus_total_bhd"))
        return {"value": money(tot.get("focus_total_bhd")), "source": "focus_total", "rows_bhd": money(row_sum),
                "gap_bhd": money(gap),
                "note": (f"The account rows add up to BHD {money(row_sum):,.3f}: Focus prints credits as positive "
                         "balances." if abs(gap) > Decimal("0.005") else None),
                "accounts": len(rows)}
    return {"value": money(row_sum), "source": "rows", "rows_bhd": money(row_sum), "accounts": len(rows),
            "note": "Sum of the account rows: Focus's own Grand Total is not stored for this snapshot, and the "
                    "export prints credits as positive, so this can read higher than Focus."}


@metric("ar.over90", "Over 90 days", "receivables",
        "Share of receivables more than 90 days past the invoice · Focus ageing as on {ar_as_of}", "pct")
def _ar_over90(ctx: Ctx) -> dict | None:
    rows = _ar_rows(ctx)
    if rows is None:
        return None
    tot = ctx.r.get("ar_totals")
    if tot and tot.get("focus_total_bhd") is not None and tot.get("focus_over90_bhd") is not None:
        over, total = dec(tot.get("focus_over90_bhd")), dec(tot.get("focus_total_bhd"))
    else:
        over = sum((dec(r.get("over_90_bhd")) for r in rows), Decimal(0))
        total = sum((dec(r.get("outstanding_bhd")) for r in rows), Decimal(0))
    return {"value": share(over, total), "over_90_bhd": money(over)}


@metric("ar.top_overdue", "Most overdue accounts", "receivables",
        "Overdue = more than 30 days · Focus ageing as on {ar_as_of}", "list", ("/receivables", "Every account"))
def _ar_top(ctx: Ctx) -> dict | None:
    rows = _ar_rows(ctx)
    if rows is None:
        return None
    od = sorted((r for r in rows if dec(r.get("overdue_bhd")) > 0),
                key=lambda r: (-dec(r.get("overdue_bhd")), str(r.get("account") or "")))
    return {"value": len(od), "bhd": money(sum((dec(r.get("overdue_bhd")) for r in od), Decimal(0))),
            "items": [{"account": r.get("account"), "overdue_bhd": money(r.get("overdue_bhd")),
                       "over_90_bhd": money(r.get("over_90_bhd")), "last_receipt": r.get("last_receipt_date")}
                      for r in od[:TOP_N]]}


@metric("ar.no_receipt", "No receipt on record", "receivables",
        "Accounts owing money with no receipt date in the Focus ageing as on {ar_as_of}", "count",
        ("/receivables", "Every account"))
def _ar_no_receipt(ctx: Ctx) -> dict | None:
    rows = _ar_rows(ctx)
    if rows is None:
        return None
    none = [r for r in rows if not r.get("last_receipt_date") and dec(r.get("outstanding_bhd")) > 0]
    return {"value": len(none), "bhd": money(sum((dec(r.get("outstanding_bhd")) for r in none), Decimal(0)))}


def unowned_over90(ctx: Ctx) -> dict | None:
    """Over-90 receivables on accounts whose last invoicing rep is not an active rep (a departed rep,
    or no invoice on record), grouped by that last rep."""
    rows, owners, reps = _ar_rows(ctx), ctx.r.get("ar_owner"), ctx.r.get("salesmen")
    if rows is None or owners is None or reps is None:
        return None
    active = {str(s.get("focus_name") or "").strip() for s in reps if s.get("is_active") and s.get("focus_name")}
    owner = {o.get("account"): o for o in owners}
    total = Decimal(0)
    groups: dict[str, dict] = {}
    for r in rows:
        o90 = dec(r.get("over_90_bhd"))
        if o90 <= 0:
            continue
        total += o90
        own = owner.get(r.get("account")) or {}
        rep = own.get("rep")
        base = rep_base(rep)
        if rep and base in active:
            continue
        if rep and own.get("channel") == "B2C":
            continue      # the B2C counters (Causeway, Roadshow) are channels, not people: owned
        key = base or "No invoice on record"
        g = groups.setdefault(key, {"last_rep": key, "accounts": 0, "over_90_bhd": Decimal(0)})
        g["accounts"] += 1
        g["over_90_bhd"] += o90
    unowned = sum((g["over_90_bhd"] for g in groups.values()), Decimal(0))
    out = sorted(({"last_rep": g["last_rep"], "accounts": g["accounts"], "over_90_bhd": money(g["over_90_bhd"])}
                  for g in groups.values()), key=lambda g: -g["over_90_bhd"])
    return {"bhd": money(unowned), "share_pct": share(unowned, total), "accounts": sum(g["accounts"] for g in out),
            "groups": out}


# ══ Needs attention ═══════════════════════════════════════════════════════════

def _item(key, tone, title, detail, basis, drill=None, count=None, bhd=None, rows=None) -> dict:
    return {"key": key, "tone": tone, "title": title, "detail": detail, "basis": basis,
            "count": count, "bhd": bhd, "drill": ({"to": drill[0], "label": drill[1]} if drill else None),
            "rows": rows or []}


def _hours_text(h: float | None) -> str:
    if h is None:
        return ""
    return f"{h:.0f} business hours" if h < 48 else f"{h / 24:.1f} business days"


def attention_live(ctx: Ctx) -> list[dict]:
    """The marketplace exceptions (live)."""
    out: list[dict] = []
    if ctx.r.get("orders") is not None:
        w = waiting_orders(ctx)
        if w:
            reps: dict[str, int] = {}
            for x in w:
                reps[x["rep"]] = reps.get(x["rep"], 0) + 1
            who = ", ".join(f"{k} {v}" for k, v in sorted(reps.items(), key=lambda kv: (-kv[1], kv[0]))[:4])
            out.append(_item("orders_waiting", "alert", f"{len(w)} order{'s' if len(w) != 1 else ''} waiting to be confirmed",
                             f"Oldest {_hours_text(w[0]['hours'])} · {who}",
                             "Received and not confirmed for over 24 business hours (Fri, Sat off) · now",
                             ("/shop-orders", "Open the order desk"), len(w),
                             money(sum((dec(x["bhd"]) for x in w), Decimal(0))),
                             [{"label": x["order_no"], "sub": f"{x['rep']} · {_hours_text(x['hours'])}", "bhd": x["bhd"],
                               "to": f"/shop-orders?open={x['id']}"} for x in w[:6]]))
    m = match_summary(ctx.r)
    if m["available"]:
        miss = m["eligible"] - m["matched"]
        if miss > 0:
            out.append(_item("delivered_no_invoice", "warn",
                             f"{miss} delivered order{'s' if miss != 1 else ''} without a Focus invoice",
                             f"BHD {m['unmatched_bhd']:,.3f} delivered over {MATCH_AFTER_DAYS} days ago with no confirmed invoice link",
                             "Delivered more than 3 days ago · no confirmed Focus link", ("/shop-orders", "Match invoices"),
                             miss, m["unmatched_bhd"]))
        if m["invoiced_open"] > 0:
            n = m["invoiced_open"]
            out.append(_item("invoiced_open", "warn", f"{n} invoiced order{'s' if n != 1 else ''} still open",
                             "Focus has invoiced them but the order is not marked Delivered",
                             "Open orders with a confirmed Focus link or a typed invoice number",
                             ("/shop-orders", "Open the order desk"), n))
    return out


def attention_focus(ctx: Ctx) -> list[dict]:
    """The Focus-side exceptions (cached with the upload)."""
    out: list[dict] = []
    days = (ctx.r.today - ctx.focus_to).days if ctx.focus_to else None
    if ctx.focus_to is None or (days is not None and days > STALE_AFTER_DAYS):
        out.append(_item("stale_data", "warn",
                         "Focus data is out of date" if ctx.focus_to else "No Focus sales loaded",
                         f"The last loaded sale is {day_label(ctx.focus_to)}, {days} days ago: every Focus figure here "
                         "stops there." if ctx.focus_to else "Upload the daily Focus reports.",
                         "Latest sale date in the loaded Focus day book", ("/data", "Upload reports")))
    silent = silent_reps(ctx) if ctx.r.get("salesmen") is not None and ctx.r.get("reps") is not None else []
    if silent:
        names = ", ".join(f"{s['rep']} (since {day_label(parse_day(s['last_invoice']))})" if s["last_invoice"]
                          else f"{s['rep']} (none on record)" for s in silent[:4])
        out.append(_item("rep_silence", "warn", f"{len(silent)} rep{'s' if len(silent) != 1 else ''} with no invoice for "
                         f"over {SILENT_AFTER_BUSINESS_DAYS} business days", names,
                         f"Active reps · last Accessories invoice in Focus · business days to {day_label(ctx.focus_to)}",
                         ("/command/team", "Rep table"), len(silent),
                         rows=[{"label": s["rep"], "sub": (f"last invoice {day_label(parse_day(s['last_invoice']))}"
                                                           if s["last_invoice"] else "no invoice on record")}
                               for s in silent[:8]]))
    un = unowned_over90(ctx)
    if un and un["bhd"] > 0:
        top = un["groups"][0]
        out.append(_item("unowned_ar", "alert",
                         f"BHD {un['bhd']:,.3f} over 90 days sits with no active rep",
                         f"{un['share_pct'] or 0:.1f} % of the over-90 book · {top['last_rep']} holds "
                         f"BHD {top['over_90_bhd']:,.3f} on {top['accounts']} account{'s' if top['accounts'] != 1 else ''}",
                         "Over-90 receivables whose last invoicing rep is not an active rep · Focus ageing",
                         ("/receivables", "Receivables"), un["accounts"], un["bhd"],
                         [{"label": g["last_rep"], "sub": f"{g['accounts']} account{'s' if g['accounts'] != 1 else ''}",
                           "bhd": g["over_90_bhd"]} for g in un["groups"][:5]]))
    so = sold_out_with_demand(ctx)
    if so:
        out.append(_item("sold_out", "warn", f"{len(so)} item{'s' if len(so) != 1 else ''} sold out with demand",
                         f"BHD {money(sum((dec(x['net_60_bhd']) for x in so), Decimal(0))):,.3f} sold in the last 60 days · "
                         + ", ".join(short(x["item"]) for x in so[:3]),
                         "Out of stock in the latest snapshot and sold in the last 60 days · Accessories · ex-VAT",
                         ("/inventory", "Reorder from Inventory"), len(so),
                         money(sum((dec(x["net_60_bhd"]) for x in so), Decimal(0))),
                         [{"label": x["item"], "sub": f"{x['qty_60']} sold in 60 days", "bhd": x["net_60_bhd"],
                           "to": "/inventory?q=" + quote(str(x["item"] or ""))} for x in so[:6]]))
    tot = ctx.r.get("margin_totals")
    below = ctx.r.get("below_cost") or []
    if tot and _int(tot.get("below")) > 0:
        n = _int(tot.get("below"))
        out.append(_item("below_cost", "alert", f"{n} item{'s' if n != 1 else ''} selling below cost",
                         ", ".join(short(r.get("item_name")) for r in below[:3]),
                         "Ex-VAT sales under Focus COGS on the latest profitability report",
                         ("/margins", "Margins"), n,
                         rows=[{"label": r.get("item_name"), "sub": (f"{float(r['margin_ex_vat_pct']):.1f} % margin"
                                                                     if r.get("margin_ex_vat_pct") is not None else ""),
                                "bhd": money(r.get("gp_ex_vat_bhd"))} for r in below[:5]]))
    return out


_TONE_ORDER = {"alert": 0, "warn": 1, "info": 2}


def rank_attention(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: (_TONE_ORDER.get(i["tone"], 9), -(i.get("bhd") or 0), -(i.get("count") or 0)))


# ── modules in the plan's order ────────────────────────────────────────────────

MODULES: list[tuple[str, str, list[str]]] = [
    ("sales", "Sales", ["sales.accessories", "sales.pace", "sales.channels", "sales.sim", "sales.trend"]),
    ("orders", "Order health", ["orders.funnel", "orders.accepted", "orders.confirm_time", "orders.waiting",
                                "orders.match_rate", "orders.self_order"]),
    ("team", "Team", ["team.reps"]),
    ("customers", "Customers", ["customers.active", "customers.dormant", "customers.concentration", "customers.cash"]),
    ("products", "Products & stock", ["products.movers", "products.sold_out", "products.stock_shape"]),
    ("profitability", "Profitability", ["profit.official", "profit.landed", "profit.below_cost"]),
    ("receivables", "Receivables", ["ar.total", "ar.over90", "ar.top_overdue", "ar.no_receipt"]),
]
LIVE_MODULES = frozenset({"orders"})
MODULE_DRILL: dict[str, tuple[str, str]] = {
    "sales": ("/sales", "Sales"), "orders": ("/shop-orders", "Customer orders"), "team": ("/command/team", "Rep table"),
    "customers": ("/command/customers", "Customers"), "products": ("/inventory", "Inventory"),
    "profitability": ("/margins", "Profitability"), "receivables": ("/receivables", "Receivables"),
}


def tile(ctx: Ctx, key: str) -> dict:
    m = METRICS[key]
    base = {"key": m.key, "label": m.label, "unit": m.unit, "basis": ctx.fmt(m.basis),
            "drill": {"to": m.drill[0], "label": m.drill[1]} if m.drill else None}
    try:
        res = m.fn(ctx)
    except Exception as e:  # noqa: BLE001 -- one tile never costs the module
        log.warning("command centre: metric %s failed: %s", key, e)
        res = None
    if res is None:
        return {**base, "available": False, "value": None, "note": "Could not be computed from the loaded data."}
    return {**base, "available": True, **res}


def build_module(ctx: Ctx, key: str) -> dict:
    title, keys = next((t, k) for mk, t, k in MODULES if mk == key)
    d = MODULE_DRILL.get(key)
    return {"key": key, "title": title, "tiles": [tile(ctx, k) for k in keys],
            "drill": {"to": d[0], "label": d[1]} if d else None, "live": key in LIVE_MODULES}


# ── sources per half ───────────────────────────────────────────────────────────

def _load_anchor(r: Reader) -> dict:
    return r.run("anchor", lambda: (r.q(ANCHOR_SQL) or [{}])[0]) or {}


# Whether v_command_orders exists (r7b_command_views_migration.sql): a miss is remembered for 5 minutes so
# every live refresh does not pay for a failing query first; a hit is re-checked by the query itself.
_VIEW_PROBE_TTL_S = 300
_view_probe: dict = {"at": 0.0, "ok": None}


def _view_known_missing() -> bool:
    return _view_probe["ok"] is False and time.time() - _view_probe["at"] < _VIEW_PROBE_TTL_S


def _remember_view(ok: bool) -> None:
    _view_probe.update(at=time.time(), ok=ok)


def _load_orders(r: Reader, since: date) -> None:
    """v_command_orders when it exists, else the agent view; remembers which answered."""
    since_ts = datetime(since.year, since.month, since.day, tzinfo=_BAHRAIN).isoformat()

    def run():
        if not _view_known_missing():
            try:
                rows = r.qp(ORDERS_SQL, [since_ts]) or []
                _remember_view(True)
                r.memo["orders_source"] = "v_command_orders"
                return rows
            except Exception as e:  # noqa: BLE001
                if not _is_missing_relation(e):
                    raise
                _remember_view(False)
        rows = r.qp(ORDERS_FALLBACK_SQL, [since_ts]) or []
        r.memo["orders_source"] = "v_shop_orders_agent"
        r.notes.append("Order health reads the older order view until the Command Centre view (r7b migration) is "
                       "applied: test orders are not left out and the accepted rate and invoice match are not shown.")
        return rows

    r.run("orders", run)
    if r.memo.get("orders_source") == "v_command_orders":
        r.run("match", lambda: (r.qp(MATCH_SQL, [r.now.isoformat()]) or [{}])[0])


def _attainment(r: Reader) -> list[dict]:
    from app import reports
    return reports.attainment_rows(r.q(reports.ATTAINMENT_SQL) or [], r.get("salesmen") or [])


def load_focus(r: Reader, period: str, anchor: date | None) -> None:
    from app import margin_truth
    jobs: dict[str, Callable[[], object]] = {"salesmen": r.salesmen_fn}
    if anchor:
        cur, cmp = windows(period, anchor)
        wins = [{"k": "cur", "s": cur.start.isoformat(), "e": cur.end.isoformat()},
                {"k": "month", "s": month_start(anchor).isoformat(), "e": anchor.isoformat()}]
        if cmp:
            wins.append({"k": "cmp", "s": cmp.start.isoformat(), "e": cmp.end.isoformat()})
        a = anchor.isoformat()
        jobs.update({
            "sales": lambda: r.qp(SALES_SQL, [json.dumps(wins)]) or [],
            "trend": lambda: r.qp(TREND_SQL, [a]) or [],
            "reps": lambda: r.qp(REPS_SQL, [month_start(anchor).isoformat(), a]) or [],
            "customers": lambda: r.qp(CUSTOMERS_SQL, [a]) or [],
            "movers": lambda: r.qp(MOVERS_SQL, [a]) or [],
            "landed": lambda: (r.qp(LANDED_SQL, [a]) or [{}])[0],
        })
    jobs.update({
        "stock": lambda: r.q(STOCK_SQL) or [],
        "margin_totals": lambda: margin_truth.margin_totals(q=r.q)[0],
        "below_cost": lambda: margin_truth.margin_rows("is_below_cost", "gp_ex_vat_bhd ASC", TOP_N, q=r.q)[0],
        "receivables": lambda: r.q(RECEIVABLES_SQL) or [],
        "ar_totals": lambda: _ar_totals(r),
        "ar_owner": lambda: r.q(AR_OWNER_SQL) or [],
    })
    r.many(jobs)
    # the attainment rows need the salesmen names read above
    r.run("attainment", lambda: _attainment(r))


def _ar_totals(r: Reader) -> dict | None:
    try:
        return (r.q(AR_TOTALS_SQL) or [None])[0]
    except Exception as e:  # noqa: BLE001 -- the totals table predates economics_v2
        if _is_missing_relation(e):
            return None
        raise


def _make_ctx(r: Reader, period: str) -> Ctx:
    a = r.get("anchor") or {}
    focus_to = parse_day(a.get("focus_to"))
    cur, cmp = windows(period, focus_to) if focus_to else (None, None)
    live, _ = windows(period, r.today)
    return Ctx(r=r, period=period, focus_to=focus_to, cur=cur, cmp=cmp, live=live, anchors=a)


def _live_since(period: str, r: Reader, focus_to: date | None) -> date:
    live, _ = windows(period, r.today)
    since = live.start
    if focus_to:
        since = min(since, month_start(focus_to))
    return since


# ── the payloads, cached ───────────────────────────────────────────────────────

FOCUS_TTL_S = 1800
LIVE_TTL_S = 60
_focus_cache: dict[str, tuple[float, dict]] = {}
_live_cache: dict[str, tuple[float, dict]] = {}
_fresh_cache: dict[str, tuple[float, dict]] = {}


def invalidate() -> None:
    """Drop every cached payload (called on every data upload via reports.invalidate_dashboard_cache)."""
    _focus_cache.clear()
    _live_cache.clear()
    _fresh_cache.clear()
    _view_probe.update(at=0.0, ok=None)


def _cached(cache: dict, key: str, ttl: float, build: Callable[[], dict]) -> dict:
    hit = cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    out = build()
    cache[key] = (time.time(), out)
    return out


def build_focus(period: str, reader: Reader | None = None) -> dict:
    """The Focus half of a period: anchors, reader state and the non-live modules (pure after the reads)."""
    r = reader or Reader()
    _load_anchor(r)
    ctx = _make_ctx(r, period)
    load_focus(r, period, ctx.focus_to)
    modules = {k: build_module(ctx, k) for k, _t, _m in MODULES if k not in LIVE_MODULES and k != "team"}
    return {"anchors": ctx.anchors, "focus_to": ctx.focus_to.isoformat() if ctx.focus_to else None,
            "period": {"key": period, "label": PERIODS[period],
                       "focus": ctx.cur.as_dict() if ctx.cur else None,
                       "compare": ctx.cmp.as_dict() if ctx.cmp else None},
            "modules": modules, "attention": attention_focus(ctx), "errors": dict(r.errors), "notes": list(r.notes),
            "reader": r}


def overview(period: str = DEFAULT_PERIOD, reader: Reader | None = None, use_cache: bool = True) -> dict:
    """GET /management/overview: freshness + the modules in the plan's order (Needs attention first)."""
    if period not in PERIODS:
        raise ValueError(f"Unknown period '{period}'.")
    focus = (_cached(_focus_cache, period, FOCUS_TTL_S, lambda: build_focus(period, reader)) if use_cache
             else build_focus(period, reader))
    return _cached(_live_cache, period, LIVE_TTL_S, lambda: _assemble(period, focus, reader)) if use_cache \
        else _assemble(period, focus, reader)


def _assemble(period: str, focus: dict, reader: Reader | None) -> dict:
    """Merge the cached Focus half with a fresh live read (orders, match rate, the team's orders)."""
    fr: Reader = focus["reader"]
    live_r = reader or Reader(q=fr.q, qp=fr.qp, salesmen_fn=fr.salesmen_fn)
    # the live reader sees the Focus sources as already read; only the orders are read again
    for k, v in fr.memo.items():
        if k not in ("orders", "match", "orders_source"):
            live_r.memo.setdefault(k, v)
    if reader is None:
        live_r.errors.update({k: v for k, v in fr.errors.items() if k not in ("orders", "match")})
    ctx = _make_ctx(live_r, period)
    _load_orders(live_r, _live_since(period, live_r, ctx.focus_to))
    team = build_module(ctx, "team")
    orders = build_module(ctx, "orders")
    attention = rank_attention(attention_live(ctx) + focus["attention"])
    mods = focus["modules"]
    ordered = []
    for k, _t, _m in MODULES:
        ordered.append(orders if k == "orders" else team if k == "team" else mods[k])
    return {
        "period": {**focus["period"], "live": ctx.live.as_dict(), "options": [{"key": k, "label": v} for k, v in PERIODS.items()]},
        "freshness": _freshness(ctx),
        "modules": [{"key": "attention", "title": "Needs attention", "items": attention,
                     "all_clear": not attention, "tiles": [], "live": True}] + ordered,
        "notes": list(dict.fromkeys(focus["notes"] + live_r.notes)),
        "unavailable": sorted(set(focus["errors"]) | set(live_r.errors)),
        "generated_at": live_r.now.isoformat(),
    }


def _freshness(ctx: Ctx) -> dict:
    m = match_summary(ctx.r)
    days = (ctx.r.today - ctx.focus_to).days if ctx.focus_to else None
    orders_ok = ctx.r.get("orders") is not None
    last = (ctx.r.get("match") or {}).get("last_order_at") if orders_ok else None
    if orders_ok and not last:
        last = max((str(o.get("created_at")) for o in ctx.r.get("orders") or [] if o.get("created_at")), default=None)
    return {
        "focus_to": ctx.focus_to.isoformat() if ctx.focus_to else None, "focus_label": day_label(ctx.focus_to),
        "focus_days_behind": days, "stale": days is None or days > STALE_AFTER_DAYS,
        "stock_as_of": ctx.anchors.get("stock_as_of"), "ar_as_of": ctx.anchors.get("ar_as_of"),
        "marketplace_live": orders_ok, "last_order_at": iso_ts(last),
        "match_rate": {k: m[k] for k in ("available", "pct", "matched", "eligible")},
    }


def attention(reader: Reader | None = None, use_cache: bool = True) -> dict:
    """GET /management/attention: the exceptions list alone (current state, not period-bound)."""
    ov = overview(DEFAULT_PERIOD, reader=reader, use_cache=use_cache)
    return {"items": ov["modules"][0]["items"], "freshness": ov["freshness"], "generated_at": ov["generated_at"]}


def freshness(show_match: bool, reader: Reader | None = None, use_cache: bool = True) -> dict:
    """GET /freshness: the header chip for every portal login — Focus data date and whether the
    marketplace answered; the invoice match rate only for the Command Centre roles."""
    def build() -> dict:
        r = reader or Reader()
        a = r.run("fresh_anchor", lambda: (r.q("SELECT (SELECT MAX(sale_date) FROM v_sales)::text AS focus_to") or [{}])[0]) or {}
        last = r.run("last_order", lambda: (r.q(LAST_ORDER_SQL) or [{}])[0])
        focus_to = parse_day(a.get("focus_to"))
        days = (r.today - focus_to).days if focus_to else None
        out = {"focus_to": focus_to.isoformat() if focus_to else None, "focus_label": day_label(focus_to),
               "focus_days_behind": days, "stale": days is None or days > STALE_AFTER_DAYS,
               "marketplace_live": last is not None, "last_order_at": iso_ts((last or {}).get("last_order_at")),
               "match_rate": None}
        if show_match and not _view_known_missing():
            try:
                row = (r.qp(MATCH_SQL, [r.now.isoformat()]) or [{}])[0]
                r.memo["match"], r.memo["orders_source"] = row, "v_command_orders"
                _remember_view(True)
            except Exception as e:  # noqa: BLE001 -- the view is not there yet: no match rate
                if _is_missing_relation(e):
                    _remember_view(False)
                else:
                    log.warning("freshness: match rate unavailable: %s", e)
        if show_match:
            m = match_summary(r)
            out["match_rate"] = {k: m[k] for k in ("available", "pct", "matched", "eligible")}
        return out

    key = "match" if show_match else "plain"
    return _cached(_fresh_cache, key, LIVE_TTL_S, build) if use_cache else build()
