"""python -m scripts.ops brief [window] [rep:<name>] [top:N] [again] [team]     (the command behind /yq-brief)

    (nothing)     the full brief, one screen: latest day, last 3 days, last 7 days, last full week,
                  month to date and pace, accounts, reps, stock, and what to do today
    day | 3d | 7d | week | last-week | mtd | last-month | YYYY-MM | YYYY-MM-DD..YYYY-MM-DD
                  that window in detail: totals, by salesman, top accounts, top products
    rep:<name>    every figure for one salesman (his Focus names are '<name>' and '<name> - ...',
                  the rule of app.metrics.rep_matches)
    again         the full brief even when no new data has been loaded since the last one
                  (without it the answer is then two lines: the data date and what is waiting)
    top:N         rows in the account and product tables of a window (the same as --top N; when
                  both are given the word wins)
    team          on its own: the team message the last load made (exports/ops/<D>/delivery/<run>/
                  whatsapp.txt), as it was made, to copy into the salesmen's group; nothing is worked out
    The words and the options may come in any order: `brief mtd rep:Van A`, `brief rep:Van A mtd`
    and `brief --top 20 mtd rep:Van A` are one request.

ONE BASIS, everywhere: v_sales, division not SIM and not Devices, giveaway lines out, net_bhd
(ex-VAT), anchored on D = the last loaded sale day and never on the calendar. Weeks run Sunday to
Saturday; business days are Sunday to Thursday (app.metrics' own helpers).

COMPLETE DAYS. A day is complete when it is on or before D, or before the day the reports were
exported: the days before an export are over, whether or not they sold anything. The export day
is the stock as-on date (status.anchors) or, when no stock snapshot says, the date the folder of
the last load is named for, when that load is the one that brought D (of two, the earlier counts;
a date before D says nothing about this data). The export day itself is never complete: when D is
that day it is a PART DAY, the latest day is the loaded sale day before it, and the month to date
keeps it. The 3-day, 7-day and full-week lines end on the last complete day, so the last full week
is the last Sunday-Saturday that is complete: a Sunday-morning export with no sale on Friday and
Saturday still shows the week that ended on that Saturday, and a week whose Saturday is the export
day is not over yet. Whenever those lines do not end on D the screen says where they end and why.

What each line is set beside, and why (the review of the plan measured it on a year of sales):
  * latest day and last 3 days: BHD, invoices and the largest invoice, beside the average business
    day of the 30 days to D. A selling day is a handful of invoices and one of them is often half
    the day, so these lines carry NO percentage.
  * last 7 days and the last full week: beside the average of the four full weeks before, and read
    as inside / above / below the normal range by scripts.ai_head.pack.xmr on the 12 full weeks
    before plus the week itself. That range cannot go under nought: when its low end is nought a
    low week cannot be told from an ordinary one, so the line reads "not above the normal range"
    and one line under the table says why. A window that holds one of the first five or the last
    five business days of a month is tagged "month-end week" and is not judged: sales pile onto
    the month end every month.
  * month to date: against the same business days last month (app.metrics.windows), in percent
    (no percentage while the month has had no business day yet: a weekend's counter sales beside a
    working day would only mislead). Pace is app.metrics.month_pace against the KPI step in force
    (config sales.kpi_steps), with the portal's own target on the same line when it differs.

Reads, all through scripts.ops.db as the SELECT-only role: one app.metrics.overview('mtd') built on
db.rpc / db.rpc_params (the attention list, the team table, sold out with demand, the daily tile
once the portal has it), one SALES_SQL with every window, one DIM_SQL (salesman, account, SKU),
the weekly totals (TREND_SQL, anchored on the Saturday that ends the last full week, so all 13
weeks are full), and the small reads: the largest invoices, the accounts that have not come back,
the target figures, the loaded sale day before a part day, and the salesman names when rep: is
typed. Beside them, the stock ledger's transfers of its last seven days (scripts.ops.moves.brief_facts,
memoised per ledger) for the MOVEMENTS line, BHD at landed cost, the /yq-stock basis and not the sales
one, as the line says. A read that fails costs its own line, never the screen; only the sales read
is required. moves and checks are imported when they are first asked for, so that a module that
cannot be imported, or one that fails, costs one line ("not read this time") and never the brief.

DO TODAY is the action register (scripts.ops.alerts). The Command Centre's attention list is put on
it first, under 'attn|<its key>'. Those keys are a fixed handful of standing conditions, not old
vouchers, so the rule is recorded with baseline=False: what it finds the first time is listed, not
counted once and hidden. Each item carries the Command Centre's own count and money, and the
waiting orders every order number it lists, so the register's rule for an item he has explained
works on them: it stays off while it is what he explained, and comes back when it is more
pressing, more than a quarter larger in BHD or in count, or names an order that was not there. An
item he marks done rests until its facts change. When a source the attention list reads did not
answer, an item may be missing only because it could not be worked out: the register is then left
as it stood, the screen says so, and that brief does not count as read (the next one prints the
whole screen again instead of two lines). The row that reads the profitability report (items
selling below cost) says so in its own words: Focus cost of sales, the report's date, and either
"held back since" the day a load first left a newer report out, where the load has written that
day down (state.json last_load.profit_held_since: only the load knows it), or, with no such day
on record and a report older than the data date, "older than the data to <D>". The register keeps
those words at the end of the item's title. Its own list cuts a long title short, so a screen that
shows the item takes them off with basis_apart and prints them by themselves, as this one does: a
star on the row and one line under the table.
The transfers to check are on the register too: before DO TODAY is listed, the five move rules of
/yq-watch are recorded for this ledger (scripts.ops.checks.ensure_recorded: once per ledger, never
raising; a preview records nothing). A recording that did not happen is one line, and the transfer
items are then listed as they stood. In the delivery both the recording and the MOVEMENTS read are
done before the brief, in a process of their own with its own time budget (deliver.moves_step); the
brief takes that answer over and redoes neither (handed_moves). A transfer item valued below the
floor (config watch.floor_bhd when it is set, else the figure /yq-watch's replay left in state.json,
watch_defaults.floor_bhd) is not listed: one line counts those; a floor in config.json that is no
amount is one warning, and no floor. The full list, and marking an item done or explained, are
/yq-watch, and the line that counts the older items points there.
sales.record_attention is the one place that records rule 'attn' (tests/test_ops_sales.py walks
the package for another): a rule's items that are not found again are marked cleared, so a second
recorder with a list of its own would clear these on every run.

Numbers are computed here and formatted by scripts.ops.fmt; the skill relays the text as it is.
Customer and salesman names appear on the screen and under exports/ops/<data date>/ (git-ignored),
nowhere else: the register keeps an item's definition and, for waiting orders alone, their order
numbers (taken by their shape, never by guess), never a name, in a key or anywhere else. The counts
in this file are the plan's definitions and the room on one screen (30 days, four and twelve weeks,
five business days, three names, five items, two order numbers on a row, the seven days of the
MOVEMENTS line); the owner's thresholds are read from config.json.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from scripts.ops import db  # first: it blanks the side channels before any app import
from scripts.ops import alerts, config, fmt, paths, status

from app import metrics  # noqa: E402
from app.ai_insights import mask_text  # noqa: E402
from scripts.ai_head.pack import xmr  # noqa: E402
from scripts.weekly_report import last_week_ending  # noqa: E402

COMMAND = "brief"
BASIS_LINE = "Accessories only, ex-VAT, BHD. Returns not deducted."
NOT_ACCESSORIES = ("SIM", "Devices")
ATTN_RULE = "attn"
AVERAGE_DAYS = 30           # the Command Centre's daily tile: the 30 days to the data date
BASELINE_WEEKS = 12         # full weeks before the week judged
AVERAGE_WEEKS = 4           # "the four full weeks before"
MONTH_EDGE_DAYS = 5         # first / last business days of a month that make a week a month-end week
TOP_NAMES = 3               # accounts and sold-out items named on the full brief
MAX_NAMES = 5               # reps named on one line
DO_TODAY = 5
TOP_DEFAULT = 10            # rows of the account and product tables of a window
KEYWORDS = ("day", "3d", "7d", "week", "last-week", "mtd", "last-month")
WINDOW_HELP = ("day, 3d, 7d, week, last-week, mtd, last-month, a month as 2026-09, "
               "or two days as 2026-09-01..2026-09-07")
READINGS = {"above": "above the normal range", "below": "below the normal range", None: "inside the normal range"}
# the range cannot go under nought: with its low end at nought "inside" would be a claim the method cannot test
NOT_ABOVE = "not above the normal range"
NOT_ABOVE_NOTE = ("The normal range starts at 0.000 here, so a low week cannot be judged: compare it with the "
                  "4-week average.")
MONTH_END_TAG = "month-end week: not judged"
NO_BUSINESS_DAY = "no business day yet this month: not compared with last month"
PROFIT_KEY = "below_cost"             # the attention item that reads the profitability report
PROFIT_BASIS = "Focus cost of sales, profitability report"
EVIDENCE_KEYS = ("orders_waiting",)   # the only attention items whose rows are document numbers
EVIDENCE_SHOWN = 2                    # order numbers printed on a DO TODAY row (the register keeps them all)
# What the Command Centre's attention list is worked out from (app.metrics.attention_live and
# attention_focus, by the reader's own names). When one of these did not answer, an item can be missing
# only because it could not be worked out, so the register is not touched.
ATTENTION_SOURCES = ("anchor", "orders", "match", "salesmen", "reps", "attainment", "receivables", "ar_owner",
                     "stock", "movers", "margin_totals", "below_cost", "chat_tickets")
MOVES_DAYS = 7                        # the MOVEMENTS line: the stock ledger's last seven days (/yq-moves' own default)
MOVES_BASIS = "at landed cost"        # what its BHD rests on: the /yq-stock basis, not the sales one
FLOOR_KEY = "watch.floor_bhd"         # a transfer item valued below this stays off DO TODAY (/yq-watch lists it)
FLOOR_UNREAD = ("config.json watch.floor_bhd is not an amount in BHD, nought or more: no floor this time, every open "
                "item is listed")
LABEL = " " * 10                      # a section's lines after its first, under the text after its label

_MONTH = re.compile(r"^(\d{4})-(\d{1,2})$")
_RANGE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})\.\.(\d{4})-(\d{1,2})-(\d{1,2})$")
# a marketplace order number, as the database makes it: <prefix>-<yymm>-<counter> (shop_next_order_no)
_ORDER_NO = re.compile(r"^[A-Za-z]{1,6}-\d{4}-\d{4,6}$")

# ── SQL (every statement runs as the SELECT-only role through the read RPC: one JSON result, 8 s,
#    $1..$8 as text, no trailing semicolon, no column aliased t) ────────────────────────────────────

BASIS_SQL = "v.division NOT IN ('SIM', 'Devices') AND NOT v.is_giveaway"


def _rep_sql(n: int) -> str:
    """The salesman filter, as app.metrics.rep_matches reads a name: '<name>' or '<name> - ...'.
    An empty parameter means everyone."""
    return f"(${n} = '' OR v.salesman_resolved = ${n} OR starts_with(v.salesman_resolved, ${n} || ' - '))"


# $1 = the windows as JSON [{"k": "mtd", "s": "2026-01-01", "e": "2026-01-20"}, ...], $2 = a salesman or ''
_WINDOWS_SQL = ("FROM jsonb_to_recordset($1::jsonb) AS w(k text, s date, e date) "
                "JOIN v_sales v ON v.sale_date BETWEEN w.s AND w.e "
                f"WHERE {BASIS_SQL} AND {_rep_sql(2)}")

# per window: one row per salesman, per account and per item (a line counts in every window it falls in)
DIM_SQL = (
    "SELECT x.k, CASE GROUPING(x.rep, x.account, x.item) WHEN 3 THEN 'salesman' WHEN 5 THEN 'account' "
    "ELSE 'sku' END AS dim, COALESCE(x.rep, x.account, x.item) AS name, "
    "ROUND(COALESCE(SUM(x.net_bhd), 0)::numeric, 3) AS net_bhd, COALESCE(SUM(x.quantity), 0) AS units, "
    "COUNT(DISTINCT x.invoice_no) AS invoices, MIN(x.channel) AS channel, BOOL_OR(x.is_cash) AS is_cash, "
    "MAX(x.code) AS code "
    "FROM (SELECT w.k, v.invoice_no, v.net_bhd, v.quantity, v.channel, v.sku_code AS code, "
    "v.is_cash_customer AS is_cash, COALESCE(v.salesman_resolved, '(no salesman)') AS rep, "
    "COALESCE(v.customer_name, v.customer_account, '(no name)') AS account, "
    "COALESCE(v.item_name, '(no item)') AS item " + _WINDOWS_SQL + ") x "
    "GROUP BY x.k, GROUPING SETS ((x.rep), (x.account), (x.item))"
)

# per window: its largest invoice
TOP_INVOICE_SQL = (
    "SELECT DISTINCT ON (i.k) i.k, i.invoice_no, i.net_bhd, i.rep, i.account, i.sale_day "
    "FROM (SELECT w.k, v.invoice_no, ROUND(COALESCE(SUM(v.net_bhd), 0)::numeric, 3) AS net_bhd, "
    "MIN(v.salesman_resolved) AS rep, MIN(COALESCE(v.customer_name, v.customer_account)) AS account, "
    "MIN(v.sale_date)::text AS sale_day " + _WINDOWS_SQL + " GROUP BY w.k, v.invoice_no) i "
    "ORDER BY i.k, i.net_bhd DESC, i.invoice_no"
)

# named accounts (never Cash Customer) that bought between $1 and $2 and have not bought between $3 and $4;
# $5 = a salesman or ''. The salesman shown is the one on the account's latest line of the first span.
LAPSED_SQL = (
    "SELECT a.account, a.net_bhd, a.rep, a.last_day FROM ("
    "SELECT COALESCE(v.customer_name, v.customer_account) AS account, "
    "ROUND(COALESCE(SUM(v.net_bhd), 0)::numeric, 3) AS net_bhd, "
    "(ARRAY_AGG(v.salesman_resolved ORDER BY v.sale_date DESC, v.line_id DESC))[1] AS rep, "
    "MAX(v.sale_date)::text AS last_day FROM v_sales v "
    f"WHERE {BASIS_SQL} AND NOT v.is_cash_customer AND COALESCE(v.customer_name, v.customer_account) IS NOT NULL "
    f"AND v.sale_date BETWEEN $1::date AND $2::date AND {_rep_sql(5)} GROUP BY 1) a "
    "WHERE a.net_bhd > 0 AND NOT EXISTS (SELECT 1 FROM v_sales c "
    "WHERE COALESCE(c.customer_name, c.customer_account) = a.account AND c.division NOT IN ('SIM', 'Devices') "
    "AND NOT c.is_giveaway AND c.sale_date BETWEEN $3::date AND $4::date)"
)

# the figures a monthly target can be read from: the portal's own target, the salesman tiers in force
# for the month $1 (a month row beats the standing '' row, as reports.ATTAINMENT_SQL reads them), and the
# sales of the months from $2 up to (not including) $3
TARGETS_SQL = (
    "SELECT (SELECT s.value FROM app_settings s WHERE s.key = 'monthly_sales_target_bhd' LIMIT 1) AS portal_target, "
    "g.reps, g.tier1_bhd, g.tier2_bhd, g.tier3_bhd, y.net_bhd AS net_months, y.months "
    "FROM (SELECT COUNT(*) AS reps, ROUND(COALESCE(SUM(e.target_bhd), 0)::numeric, 3) AS tier1_bhd, "
    "ROUND(COALESCE(SUM(e.tier2_bhd), 0)::numeric, 3) AS tier2_bhd, "
    "ROUND(COALESCE(SUM(e.tier3_bhd), 0)::numeric, 3) AS tier3_bhd "
    "FROM (SELECT DISTINCT ON (st.salesman) st.salesman, st.target_bhd, st.tier2_bhd, st.tier3_bhd "
    "FROM salesman_targets st WHERE st.period IN ('', $1) ORDER BY st.salesman, st.period DESC) e) g "
    "CROSS JOIN (SELECT ROUND(COALESCE(SUM(v.net_bhd), 0)::numeric, 3) AS net_bhd, "
    "COUNT(DISTINCT date_trunc('month', v.sale_date)) AS months FROM v_sales v "
    f"WHERE {BASIS_SQL} AND v.sale_date >= $2::date AND v.sale_date < $3::date) y"
)

# the loaded sale day before $1 (the latest full day when $1 is a part day)
PREV_DAY_SQL = "SELECT MAX(sale_date)::text AS prev_day FROM v_sales WHERE sale_date < $1::date"
# every salesman name Focus has used (to read what was typed after rep:)
REP_NAMES_SQL = "SELECT DISTINCT salesman_resolved AS rep FROM v_sales WHERE salesman_resolved IS NOT NULL"


class BriefInput(ValueError):
    """What was typed cannot be read. The message is the plain sentence shown."""


@dataclass(frozen=True)
class Win:
    key: str
    start: date
    end: date

    def param(self) -> dict:
        return {"k": self.key, "s": self.start.isoformat(), "e": self.end.isoformat()}

    def facts(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


# ── small helpers ──────────────────────────────────────────────────────────────────────────────

def _int(x) -> int:
    try:
        return int(fmt.dec(x))
    except (ValueError, ArithmeticError):
        return 0


def _number(x) -> Decimal | None:
    """A figure another program gave, as a Decimal; None when it gave none or gave no number
    (unlike fmt.dec, which reads nothing as nought: here 'no figure' must stay 'no figure')."""
    if x is None:
        return None
    try:
        v = Decimal(str(x).replace(",", "").strip())
    except ArithmeticError:
        return None
    return v if v.is_finite() else None


def _count(x) -> int | None:
    """A count of things (orders, items, reps) as a whole number; None when there is none."""
    v = _number(x)
    return int(v) if v is not None and v >= 0 and v == v.to_integral_value() else None


def _last_load(state: dict) -> dict:
    """The last load on record (state.json last_load); {} when there is none, or when what is
    kept under that name is not an object."""
    last = state.get("last_load")
    return last if isinstance(last, dict) else {}


def _zero() -> dict:
    return {"net": Decimal(0), "invoices": 0, "b2b": Decimal(0), "b2c": Decimal(0)}


def _clean(x) -> str:
    """A name from the data, on one line, with anything that looks like a phone number masked."""
    return mask_text(" ".join(str(x or "").split())) or ""


def _name(x, limit: int = 40) -> str:
    s = _clean(x) or "(no name)"
    return s if len(s) <= limit else s[: limit - 3].rstrip() + "..."


def _rep(x, limit: int = 24) -> str:
    return _name(metrics.rep_base(str(x or "")) or x, limit)


def _day(x, ref: date | None = None) -> str:
    """'Tue 06 Oct', with the year when it is not the data year."""
    d = fmt.as_date(x)
    if d is None:
        return "n/a"
    return fmt.day(d) + (f" {d.year}" if ref is not None and d.year != ref.year else "")


def _span(a: date, b: date, ref: date | None = None) -> str:
    return _day(a, ref) if a == b else f"{_day(a, ref)} - {_day(b, ref)}"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{fmt.units(n)} {one if n == 1 else (many or one + 's')}"


def _basis_words(basis: str | None) -> str:
    """app.metrics' words for the comparison window, with its one slip of grammar put right
    ('the same 1 business days')."""
    return (basis or "the same business days last month").replace(" 1 business days", " 1 business day")


def kpi_step_for(month: str) -> Decimal | None:
    """The KPI step in force for 'YYYY-MM', None while none is set. config.kpi_step reads it: the
    newest entry that has started; an entry of nought is no entry, so the step before it stays in
    force; a figure typed with its comma is read. An entry it cannot read, or a figure that is no
    number at all (NaN, infinity), stops the command with config.ConfigMissing, one plain sentence
    that names the key."""
    return config.kpi_step(month)


def _guarded(line: str) -> str:
    """Every line shown passes the wording guard (fmt.careful). A name from the data that holds
    such a word is no accusation, but the guard cannot know: the word is blanked, never printed."""
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


def _indent(block: str, by: str = "  ") -> list[str]:
    return [by + ln for ln in block.splitlines()]


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    return x


def _gather(jobs: dict) -> dict:
    """Run the reads side by side. A read that fails is kept as its exception: one source never
    costs the screen (the caller decides which ones the brief cannot do without)."""
    def one(item):
        name, fn = item
        try:
            return name, fn()
        except Exception as exc:  # noqa: BLE001
            return name, exc
    if not jobs:
        return {}
    with ThreadPoolExecutor(max_workers=min(6, len(jobs)), thread_name_prefix="brief") as pool:
        return dict(pool.map(one, list(jobs.items())))


def _rows(got: dict, name: str, warnings: list[str], what: str):
    """What one read returned (rows, or the Command Centre's payload), or None with a warning when
    it failed; None without one when it was not asked for."""
    val = got.get(name)
    if isinstance(val, Exception):
        warnings.append(f"{what} could not be read: {type(val).__name__}: {str(val)[:120]}")
        return None
    return val


# ── what was typed ─────────────────────────────────────────────────────────────────────────────

def _is_window(low: str) -> bool:
    return low in KEYWORDS or bool(_MONTH.match(low)) or bool(_RANGE.match(low))


def parse_spec(tokens) -> tuple[str | None, str | None, bool, int | None]:
    """(window, salesman, again, top) from the words after `brief`, in whatever order they were
    typed (the entry point has already taken the options out from among them). A name may be
    several words (rep:Van A); top:N is the rows of a window's tables, as a word; a word that is
    neither a window nor part of a name is refused in a plain sentence."""
    window: str | None = None
    rep_parts: list[str] | None = None
    again = False
    top: int | None = None
    words = [tok for raw in tokens or [] for tok in str(raw).split()]
    i = 0
    while i < len(words):
        tok = words[i]
        low = tok.lower().replace("_", "-")
        if low == "last" and i + 1 < len(words) and words[i + 1].lower() in ("week", "month"):
            low, i = f"last-{words[i + 1].lower()}", i + 1        # 'last week' typed as two words
        i += 1
        if low.startswith("rep:"):
            if rep_parts is not None:
                raise BriefInput("Give one salesman only, for example rep:Van A.")
            rep_parts = [tok[4:]] if tok[4:] else []
        elif low.startswith("top:"):
            if not tok[4:].isdecimal() or int(tok[4:]) < 1:
                raise BriefInput("top: needs a whole number above 0, for example top:20.")
            top = int(tok[4:])
        elif low == "again":
            again = True
        elif _is_window(low):
            if window is not None:
                raise BriefInput(f"Two windows were given ('{window}' and '{low}'). Give one.")
            window = low
        elif rep_parts is not None:
            rep_parts.append(tok)
        else:
            raise BriefInput(f"I do not know the window '{tok}'. Use one of: {WINDOW_HELP}.")
    rep = " ".join(rep_parts).strip() if rep_parts is not None else None
    if rep_parts is not None and not rep:
        raise BriefInput("rep: needs a name, for example rep:Van A.")
    return window, rep, again, top


def _a_date(y: str, m: str, d: str, text: str) -> date:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        raise BriefInput(f"'{text}' is not a day on the calendar. Write days as 2026-09-01.") from None


def export_day(d: date, anchors: dict, state: dict) -> date | None:
    """The day the reports that brought D were exported, as far as anything on record says: the
    stock as-on date, and the date the folder of the last load is named for. A load on record
    that brought another day says nothing about D (the data then came another way: the portal's
    Data page, a load by hand), and neither does a date before D (an older stock snapshot). Of
    two days the earlier one counts, so that fewer days are called complete. None when nothing
    says. When the answer is D itself, D is a PART DAY: the reports were exported while that day
    was still being invoiced."""
    last = _last_load(state)
    noted = fmt.as_date(last.get("data_date"))
    folder = paths.drop_date(str(last.get("run") or "")[:6]) if noted in (None, d) else None
    known =[x for x in (fmt.as_date(anchors.get("stock_as_of")), folder) if x is not None and x >= d]
    return min(known) if known else None


def full_until(d: date, latest: date, exported: date | None = None) -> date:
    """The last COMPLETE calendar day. A day is complete when it is on or before D, or before the
    day the reports were exported (`exported`, from export_day): the days before an export are
    over, whether or not they sold anything (a Friday and a Saturday before a Sunday-morning
    export). The export day itself is never complete: when D is that day (a part day: `latest`,
    its latest full sale day, is then earlier than D) the last complete day is the day before it.
    The 3-day, 7-day and full-week windows end here."""
    own = d if latest >= d else d - timedelta(days=1)
    return own if exported is None else max(own, exported - timedelta(days=1))


def full_week_end(full_to: date) -> date:
    """The Saturday that ends the last Sunday-Saturday week lying wholly on or before `full_to`."""
    return last_week_ending(full_to + timedelta(days=1))


def resolve_window(window: str, d: date, latest: date, full_to: date | None = None) -> tuple[Win, str, list[str]]:
    """(the window, its title, notes) for one window word, on data to `d` whose latest full sale
    day is `latest` (earlier than `d` on a part day) and whose last complete day is `full_to`
    (full_until; worked out from the two days alone when it is not given). Raises BriefInput for
    a month or a day that cannot be."""
    notes: list[str] = []
    if full_to is None:
        full_to = full_until(d, latest)
    if window == "day":
        return Win("win", latest, latest), "Latest day", notes
    if window == "3d":
        return Win("win", full_to - timedelta(days=2), full_to), "Last 3 days", notes
    if window == "7d":
        return Win("win", full_to - timedelta(days=6), full_to), "Last 7 days", notes
    if window == "week":
        return Win("win", d - timedelta(days=(d.weekday() + 1) % 7), d), "This week so far", notes
    if window == "last-week":
        sat = full_week_end(full_to)
        return Win("win", sat - timedelta(days=6), sat), "Last full week", notes
    if window == "mtd":
        return Win("win", metrics.month_start(d), d), "Month to date", notes
    if window == "last-month":
        m0 = metrics.add_months(d, -1)
        return Win("win", m0, metrics.month_end(m0)), f"Last month ({m0:%B %Y})", notes
    m = _MONTH.match(window)
    if m:
        try:
            m0 = date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:                              # month 13, month 0, the year 0000
            raise BriefInput(f"'{window}' is not a month. Write a month as 2026-09.") from None
        if m0 > d:
            raise BriefInput(f"No sales are loaded for {m0:%B %Y} yet: the data runs to {_day(d)} {d.year}.")
        end = min(metrics.month_end(m0), d)
        if end < metrics.month_end(m0):
            notes.append(f"{m0:%B} is not over: the data runs to {_day(d)}.")
        return Win("win", m0, end), f"{m0:%B %Y}", notes
    m = _RANGE.match(window)
    if m:
        a = _a_date(m.group(1), m.group(2), m.group(3), window.split("..")[0])
        b = _a_date(m.group(4), m.group(5), m.group(6), window.split("..")[1])
        if a > b:
            raise BriefInput("The first day is after the last day. Write the earlier day first.")
        if a > d:
            raise BriefInput(f"No sales are loaded after {_day(d)} {d.year}.")
        if b > d:
            notes.append(f"The data runs to {_day(d)}: the window stops there.")
            b = d
        return Win("win", a, b), "Chosen days", notes
    raise BriefInput(f"I do not know the window '{window}'. Use one of: {WINDOW_HELP}.")


def resolve_rep(typed: str, names: list[str]) -> str:
    """The salesman a typed name means: a Focus name, or the part of it before ' - ' (so the name
    then matches exactly as app.metrics.rep_matches does). Case and a part of the name are
    forgiven when only one salesman fits; otherwise the names are listed."""
    want = " ".join(typed.split())
    full = sorted({str(n) for n in names if n})                 # as the database spells them
    bases = sorted({metrics.rep_base(n) for n in full if metrics.rep_base(n)})
    low = want.lower()

    def norm(n: str) -> str:
        return " ".join(n.split()).lower()
    for pool in (bases, full):
        if want in pool:
            return want
    for pool in (bases, full):
        same = [n for n in pool if norm(n) == low]
        if len(same) == 1:
            return same[0]
    for test in (lambda n: norm(n).startswith(low), lambda n: low in norm(n)):
        some = [n for n in bases if test(n)]
        if len(some) == 1:
            return some[0]
        if len(some) > 1:
            raise BriefInput(f"More than one salesman fits '{want}': {', '.join(_clean(n) for n in some)}. "
                             "Type more of the name.")
    listed = ", ".join(_clean(n) for n in bases) or "none loaded"
    raise BriefInput(f"No salesman fits '{want}'. Names in the sales data: {listed}.")


# ── calendar rules ─────────────────────────────────────────────────────────────────────────────

def month_edge_days(any_day: date) -> set[date]:
    """The first five and the last five business days of the month `any_day` is in."""
    m0, m1 = metrics.month_start(any_day), metrics.month_end(any_day)
    work = [m0 + timedelta(days=i) for i in range((m1 - m0).days + 1)
            if metrics.is_business_day(m0 + timedelta(days=i))]
    return set(work[:MONTH_EDGE_DAYS]) | set(work[-MONTH_EDGE_DAYS:])


def touches_month_edge(start: date, end: date) -> bool:
    """True when the window holds one of the first five or the last five business days of a month."""
    edges: dict[tuple[int, int], set[date]] = {}
    d = start
    while d <= end:
        key = (d.year, d.month)
        if key not in edges:
            edges[key] = month_edge_days(d)
        if d in edges[key]:
            return True
        d += timedelta(days=1)
    return False


def reading(win: Win, before: list[Decimal], value: Decimal) -> tuple[str, dict]:
    """How a week reads against the weeks before it: (words, the xmr result).

    The range xmr gives cannot go under nought. When its low end is nought no week can read
    "below" and a week with no sale at all would read "inside": the method cannot tell a low week
    from an ordinary one there, so the words are "not above the normal range" (NOT_ABOVE) and the
    screen adds one line saying why."""
    if touches_month_edge(win.start, win.end):
        return MONTH_END_TAG, {"judged": False, "why": "month-end week"}
    res = xmr(list(before) + [value], win.key)
    if not res.get("judged"):
        return "not judged: too few earlier weeks", res
    signal = res.get("signal")
    if signal is None and fmt.dec(res.get("lower")) <= 0:
        return NOT_ABOVE, res
    return READINGS.get(signal, READINGS[None]), res


def loaded_text(state: dict, d: date) -> str | None:
    """'Wed 07 Oct 12:46' from the last load on record, when that load is the one that brought D
    (None when no load is on record for this data date: it came another way)."""
    last = _last_load(state)
    when = last.get("finished_at")
    noted = fmt.as_date(last.get("data_date"))
    if not when or (noted is not None and noted != d):
        return None
    try:
        ts = datetime.fromisoformat(str(when))
    except ValueError:
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(status.BAHRAIN)
    return f"{_day(ts.date(), d)} {ts:%H:%M}"


# ── totals ─────────────────────────────────────────────────────────────────────────────────────

def totals_from_sales(rows: list[dict] | None) -> dict[str, dict]:
    """SALES_SQL rows -> per window {net, invoices, b2b, b2c} on the basis (SIM and Devices out;
    the SQL already leaves giveaways out)."""
    out: dict[str, dict] = {}
    for r in rows or []:
        if str(r.get("division")) in NOT_ACCESSORIES:
            continue
        acc = out.setdefault(str(r.get("k")), _zero())
        v = fmt.dec(r.get("net_bhd"))
        acc["net"] += v
        acc["invoices"] += _int(r.get("invoices"))
        acc["b2c" if r.get("channel") == "B2C" else "b2b"] += v
    return out


def totals_from_dim(rows: list[dict] | None) -> dict[str, dict]:
    """The same totals from the salesman rows of DIM_SQL (used when one salesman is asked for)."""
    out: dict[str, dict] = {}
    for r in rows or []:
        if r.get("dim") != "salesman":
            continue
        acc = out.setdefault(str(r.get("k")), _zero())
        v = fmt.dec(r.get("net_bhd"))
        acc["net"] += v
        acc["invoices"] += _int(r.get("invoices"))
        acc["b2c" if r.get("channel") == "B2C" else "b2b"] += v
    return out


def _dim(rows: list[dict] | None, key: str, dim: str) -> list[dict]:
    return [r for r in rows or [] if r.get("k") == key and r.get("dim") == dim]


def _by_salesman(rows: list[dict]) -> list[dict]:
    """Salesman rows folded onto the name before ' - ' (a van's Acc and SIM warehouses are one man)."""
    acc: dict[str, dict] = {}
    for r in rows:
        base = metrics.rep_base(str(r.get("name") or "")) or str(r.get("name") or "")
        a = acc.setdefault(base, {"name": base, "net": Decimal(0), "invoices": 0, "units": Decimal(0)})
        a["net"] += fmt.dec(r.get("net_bhd"))
        a["invoices"] += _int(r.get("invoices"))
        a["units"] += fmt.dec(r.get("units"))
    return sorted(acc.values(), key=lambda a: (-a["net"], a["name"]))


# ── the Command Centre's own payload ───────────────────────────────────────────────────────────

def read_overview() -> dict:
    """One app.metrics.overview('mtd', use_cache=False) on a reader built on db.rpc / db.rpc_params,
    so every statement goes through the guard (the rep names it reads through the client are a GET
    on the same guarded client). Returns the payload, per salesman of the target sheet whether the
    portal has him switched on, and the date of the profitability report its margin rows read."""
    db.client()
    reader = metrics.Reader(q=db.rpc, qp=db.rpc_params)
    payload = metrics.overview("mtd", reader=reader, use_cache=False)
    active = {str(r.get("salesman")): r.get("is_active") for r in reader.get("attainment") or []}
    # the rows the overview already read (no further read): the delivery's rep table and push list use them
    return {"payload": payload, "active": active,
            "margin_report_date": (reader.get("anchor") or {}).get("margin_report_date"),
            "rows": {"attainment": reader.get("attainment"), "stock": reader.get("stock"),
                     "movers": reader.get("movers")}}


def _tile(payload: dict | None, key: str) -> dict | None:
    for mod in (payload or {}).get("modules") or []:
        for t in mod.get("tiles") or []:
            if t.get("key") == key:
                return t if t.get("available") else None
    return None


def _attention(payload: dict | None) -> list[dict] | None:
    for mod in (payload or {}).get("modules") or []:
        if mod.get("key") == "attention":
            return list(mod.get("items") or [])
    return None


# ── the action register ────────────────────────────────────────────────────────────────────────

def profit_basis(report_day: date | None, d: date, state: dict) -> str:
    """What a row that reads the profitability report says of it (the plan's One basis): the cost
    is Focus cost of sales, not the 'at cost' of every other line; the report's date; and, when a
    load left a newer report out, since when. That day is the load's to know: it is read from
    state.json (last_load.profit_held_since) and counts only while the report on file is older.
    With no such day on record a report older than the data date is called just that."""
    if report_day is None:
        return f"{PROFIT_BASIS} date not read"
    text = f"{PROFIT_BASIS} of {_day(report_day, d)}"
    held = fmt.as_date(_last_load(state).get("profit_held_since"))
    if held is not None and held > report_day:
        return f"{text}, held back since {_day(held, d)}"
    if report_day < d:
        return f"{text}, older than the data to {_day(d)}"
    return text


def basis_apart(title: str) -> tuple[str, str | None]:
    """A register title with its profitability words taken off its end: ('1 item selling below
    cost', 'Focus cost of sales, profitability report of ...'), or (the title, None) when it has
    none. The register keeps the whole sentence in the title; a table cannot (the register's own
    list cuts a long title short, and one title of that length would push every row of DO TODAY
    past the width of the window), so a screen sets the words on a line of their own."""
    head, mark, tail = title.partition(f" ({PROFIT_BASIS}")
    if not mark or not head or not tail.endswith(")"):
        return title, None
    return head, PROFIT_BASIS + tail[:-1]


def attention_found(items: list[dict], profit: str | None = None) -> list[dict]:
    """The Command Centre's attention items as register items: key attn|<its key> (a fixed word,
    never a name), ACT for an alert and CHECK for the rest, with its money and its count when it
    has them (what the register measures an explained item against). The detail kept is the
    item's definition (its basis line), not the Command Centre's own detail, which names reps and
    accounts: the names stay on the screen and in the exports folder.
    Evidence is kept for waiting orders alone, and only labels shaped like an order number: the
    rows of every other item are reps, accounts or items, whatever their names look like. Every
    order number the Command Centre lists is kept, not the two a screen has room for: the
    register brings an explained item back when it names an order that was not there, and a list
    cut short would call an old order new as soon as an older one was confirmed.
    `profit` (profit_basis) is added to the title of the row that reads the profitability report."""
    found: list[dict] = []
    seen: set[str] = set()
    for it in items or []:
        key = str(it.get("key") or "").strip()
        if not key or "|" in key or key in seen:
            continue
        seen.add(key)
        title = _guarded(_clean(it.get("title"))) or f"Command Centre: {key}"
        if key == PROFIT_KEY and profit:
            title = f"{title} ({profit})"
        labels = [str(r.get("label") or "").strip() for r in it.get("rows") or []] if key in EVIDENCE_KEYS else []
        money = _number(it.get("bhd"))
        found.append({
            "key": alerts.make_key(ATTN_RULE, key),
            "level": "ACT" if it.get("tone") == "alert" else "CHECK",
            "title": title,
            "detail": _guarded(_clean(it.get("basis"))),
            "who": None, "due": None,
            "bhd": fmt.q3(money) if money is not None else None,
            "count": _count(it.get("count")),
            "evidence": list(dict.fromkeys(lb for lb in labels if _ORDER_NO.match(lb))),
        })
    return found


def record_attention(items: list[dict], data_date: str, profit: str | None = None) -> dict:
    """Put the attention list on the register. Its keys are a handful of standing conditions, each
    a fixed word, so the rule is never a baseline (baseline=False): what the first brief finds is
    listed, not counted once and hidden.

    THE ONE PLACE that records rule 'attn' (tests/test_ops_sales.py walks the package for any
    other). The register marks a rule's items cleared when a run no longer finds them, so a second
    recorder with a list of its own would clear these on every run, and one that left the baseline
    on would hide them. A command that wants the attention list on the register calls this."""
    return alerts.record(ATTN_RULE, attention_found(items, profit), data_date, baseline=False)


# ── sections of the full brief ─────────────────────────────────────────────────────────────────

def _week_values(trend_rows: list[dict] | None, week_start: date) -> list[Decimal]:
    """The totals of the 12 full weeks before the week that starts on `week_start`, oldest first
    (a week with no sale is a 0)."""
    by = {str(r.get("week_start"))[:10]: fmt.dec(r.get("net_bhd")) for r in trend_rows or []}
    return [by.get((week_start - timedelta(days=7 * k)).isoformat(), Decimal(0))
            for k in range(BASELINE_WEEKS, 0, -1)]


def _largest_text(row: dict | None, d: date, with_day: bool, bulk_from: Decimal) -> tuple[str, dict | None]:
    if not row or fmt.dec(row.get("net_bhd")) <= 0:
        return "none", None
    net = fmt.q3(row.get("net_bhd"))
    bulk = net >= bulk_from
    fact = {"bhd": net, "salesman": _clean(row.get("rep")), "account": _clean(row.get("account")),
            "invoice": _clean(row.get("invoice_no")), "day": str(row.get("sale_day") or "")[:10], "bulk_order": bulk}
    text = f"{fmt.bhd(net)}, {_rep(row.get('rep'))}, {_name(row.get('account'))}"
    if with_day and fmt.as_date(row.get("sale_day")):
        text += f", on {_day(row.get('sale_day'), d)}"
    return text + (" (a bulk order)" if bulk else ""), fact


def no_sale_note(d: date, exported: date, runs_to: date, what: str) -> str:
    """The line a screen carries when a window ends after D: the days between are complete because
    the reports were exported after them, and they sold nothing."""
    return (f"The reports were exported on {_day(exported, d)} and hold no sale after {_day(d)}: {what} "
            f"{_day(runs_to, d)}.")


def sales_section(d: date, latest: date, full_to: date, part: bool, wins: dict[str, Win], tot,
                  weeks_before: list[Decimal], not_judged: str | None, avg_day: Decimal | None, avg_note: str,
                  top_rows: list[dict] | None, cmp_basis: str, bulk_from: Decimal,
                  exported: date | None = None) -> tuple[dict, list[str]]:
    """The SALES block: five rows and the two largest invoices. `not_judged` says why the two
    week rows carry no reading (one salesman, or the weekly totals were not read); None = judge.
    `latest` is the day of the first row; `full_to` the last complete day, where the 3-day, 7-day
    and full-week rows end. Both are D on an ordinary day; on a part day both are earlier, and
    when the reports were exported (`exported`) more than a day after D, `full_to` is later."""
    facts: dict = {}
    lines: list[str] = []
    if part:
        p = tot("part")
        said = (f"Part day: {_day(d)} is the day the reports were exported, so it may not be complete "
                f"({fmt.bhd(p['net'])} in {_plural(p['invoices'], 'invoice')} so far). ")
        if latest == full_to:
            said += f"The latest full day is {_day(latest, d)}; "
        else:                                         # the days between sold nothing (a weekend before the export)
            said += (f"The latest day with sales before it is {_day(latest, d)}; the 3-day, 7-day and full-week "
                     f"lines stop at {_day(full_to, d)}; ")
        lines += [said + "the month to date includes the part day.", ""]
        facts["part_day"] = {"day": d.isoformat(), "bhd": fmt.q3(p["net"]), "invoices": p["invoices"],
                             "latest_day": latest.isoformat(), "full_to": full_to.isoformat()}
    elif full_to > d and exported is not None:        # exported after days that sold nothing: they are over
        lines += [no_sale_note(d, exported, full_to, "the 3-day, 7-day and full-week lines run to"), ""]
        facts["no_sale_after"] = {"day": d.isoformat(), "exported": exported.isoformat(),
                                  "full_to": full_to.isoformat()}

    beside_day = f"30-day average business day {fmt.bhd(avg_day)}" if avg_day is not None else "no 30-day average"
    facts["average_business_day"] = {"bhd": fmt.q3(avg_day) if avg_day is not None else None, "days": AVERAGE_DAYS,
                                     "to": d.isoformat(), "note": avg_note}
    recent = weeks_before[-AVERAGE_WEEKS:]
    avg_week = (sum(recent, Decimal(0)) / len(recent)) if recent else None
    facts["four_week_average"] = {"bhd": fmt.q3(avg_week) if avg_week is not None else None,
                                  "weeks": [fmt.q3(v) for v in recent]}
    facts["weeks_before"] = [fmt.q3(v) for v in weeks_before]

    grid: list[list[str]] = []
    for key, label in (("day", "Latest day"), ("d3", "Last 3 days")):
        w, t = wins[key], tot(key)
        weekend = key == "day" and not metrics.is_business_day(w.start)
        grid.append([label, _span(w.start, w.end, d) + (" (weekend)" if weekend else ""), fmt.bhd(t["net"]),
                     fmt.units(t["invoices"]), beside_day])
        facts[key] = dict(w.facts(), bhd=fmt.q3(t["net"]), invoices=t["invoices"])
    low_end_note = False
    for key, label in (("d7", "Last 7 days"), ("lfw", "Last full week")):
        w, t = wins[key], tot(key)
        if not_judged is None:
            words, res = reading(w, weeks_before, t["net"])
        else:
            words, res = not_judged, {"judged": False, "why": not_judged}
        low_end_note = low_end_note or words == NOT_ABOVE
        beside = (f"4-week average {fmt.bhd(avg_week)}; " if avg_week is not None else "") + words
        grid.append([label, _span(w.start, w.end, d), fmt.bhd(t["net"]), fmt.units(t["invoices"]), beside])
        facts[key] = dict(w.facts(), bhd=fmt.q3(t["net"]), invoices=t["invoices"], reading=words, xmr=res,
                          month_end_week=touches_month_edge(w.start, w.end))
    w, t, c = wins["mtd"], tot("mtd"), tot("cmp")
    # a month that has had no business day yet (it opened on a weekend) has nothing like for like to stand beside
    compared = metrics.business_days(w.start, w.end) > 0
    change = fmt.change(t["net"], c["net"]) if compared else None
    grid.append(["Month to date", _span(w.start, w.end, d), fmt.bhd(t["net"]), fmt.units(t["invoices"]),
                 f"{cmp_basis} {fmt.bhd(c['net'])}: {change}" if compared else NO_BUSINESS_DAY])
    facts["mtd"] = dict(w.facts(), bhd=fmt.q3(t["net"]), invoices=t["invoices"], b2b=fmt.q3(t["b2b"]),
                        b2c=fmt.q3(t["b2c"]),
                        compare=dict(wins["cmp"].facts(), bhd=fmt.q3(c["net"]), basis=cmp_basis, compared=compared),
                        change=change)
    lines += fmt.grid(["SALES", "Days", "BHD", "Invoices", "Beside"], grid, "llrrl").splitlines()
    if low_end_note:
        lines.append(NOT_ABOVE_NOTE)

    by_k = {str(r.get("k")): r for r in top_rows or []}
    facts["largest_invoice"] = {}
    for key, label, with_day in (("day", "latest day", False), ("d3", "last 3 days", True)):
        if top_rows is None:
            lines.append(f"Largest invoice, {label}: not read this time")
            continue
        text, fact = _largest_text(by_k.get(key), d, with_day, bulk_from)
        lines.append(f"Largest invoice, {label}: {text}")
        facts["largest_invoice"][key] = fact
    return facts, lines


def pace_section(d: date, mtd: Decimal, step: Decimal | None, targets: dict | None, portal_tile: dict | None,
                 show_figures: bool) -> tuple[dict, list[str], bool]:
    """The pace line. Returns (facts, lines, the four figures were printed)."""
    portal = fmt.dec((targets or {}).get("portal_target")) if targets else fmt.dec((portal_tile or {}).get("target_bhd"))
    facts: dict = {"kpi_step": fmt.q3(step) if step else None, "portal_target": fmt.q3(portal) if portal > 0 else None}

    def need_text(p: dict, target: Decimal) -> str:
        left = int(p["business_days_left"])
        if mtd >= target:
            return "reached"
        if left == 0:
            return f"{fmt.bhd(target - mtd)} short, no business day left"
        return f"{fmt.bhd(p['needed_per_business_day_bhd'])} a day still needed"

    def days_left(p: dict) -> str:
        """', N business days left'; nothing on the month's last business day (the words before say it)."""
        left = int(p["business_days_left"])
        return f", {_plural(left, 'business day')} left" if left else ""

    portal_pace = metrics.month_pace(mtd, d, portal) if portal > 0 else None
    if step:
        p = metrics.month_pace(mtd, d, step)
        left = int(p["business_days_left"])
        text = (f"Pace: KPI step {fmt.bhd(step)}: {fmt.pct(fmt.dec(p['pct_of_target']), 100)} done, "
                f"{need_text(p, step)}{days_left(p)}")
        facts["pace"] = {"done_pct": p["pct_of_target"], "needed_per_business_day": fmt.q3(p["needed_per_business_day_bhd"]),
                         "business_days_left": left, "business_days_done": p["business_days_done"]}
        if portal_pace and fmt.q3(portal) != fmt.q3(step):
            text += f"; portal target {fmt.bhd(portal)}: {need_text(portal_pace, portal)}"
    else:
        text = "Pace: KPI step not set"
        if portal_pace:
            text += f"; portal target {fmt.bhd(portal)}: {need_text(portal_pace, portal)}{days_left(portal_pace)}"
    if portal_pace:
        facts["portal_pace"] = {"done_pct": portal_pace["pct_of_target"],
                                "needed_per_business_day": fmt.q3(portal_pace["needed_per_business_day_bhd"]),
                                "business_days_left": int(portal_pace["business_days_left"])}
    lines = [text]
    printed = False
    if step is None and show_figures and targets:
        months = _int(targets.get("months"))
        avg = (fmt.dec(targets.get("net_months")) / months) if months else None
        rows = [["your KPI sheet's step", "(yours to say)"],
                ["the portal's monthly target", fmt.bhd(portal) if portal > 0 else "not set"],
                [f"the tiers of {_plural(_int(targets.get('reps')), 'salesman', 'salesmen')} added up",
                 " / ".join(fmt.bhd(targets.get(k)) for k in ("tier1_bhd", "tier2_bhd", "tier3_bhd"))],
                [f"the average of the last {_plural(months, 'month')}" if months else "the average of the last months",
                 fmt.bhd(avg) if avg is not None else "no sales loaded"]]
        lines.append("  Four monthly figures exist. Say which one management measures you on (shown once):")
        lines += _indent(fmt.grid(["Figure", "BHD"], rows, "ll"), "    ")[1:]
        facts["target_figures"] = {"portal": fmt.q3(portal) if portal > 0 else None,
                                   "tiers": [fmt.q3(targets.get(k)) for k in ("tier1_bhd", "tier2_bhd", "tier3_bhd")],
                                   "salesmen": _int(targets.get("reps")), "months": months,
                                   "monthly_average": fmt.q3(avg) if avg is not None else None}
        printed = True
    return facts, lines, printed


def accounts_section(rows: list[dict] | None, mine: bool) -> tuple[dict, list[str]]:
    """Accounts that bought last month and have not bought this month (never Cash Customer)."""
    if rows is None:
        return {}, ["ACCOUNTS  not read this time"]
    ranked = sorted(rows, key=lambda r: (-fmt.dec(r.get("net_bhd")), str(r.get("account") or "")))
    total = sum((fmt.dec(r.get("net_bhd")) for r in ranked), Decimal(0))
    facts = {"count": len(ranked), "bhd_last_month": fmt.q3(total),
             "accounts": [{"account": _clean(r.get("account")), "bhd_last_month": fmt.q3(r.get("net_bhd")),
                           "salesman": _clean(r.get("rep")), "last_invoice": str(r.get("last_day") or "")[:10]}
                          for r in ranked]}
    if not ranked:
        return facts, ["ACCOUNTS  every account " + ("of his " if mine else "")
                       + "that bought last month has bought again this month"]
    n = len(ranked)
    who = f"{n} of his accounts" if mine else _plural(n, "account")
    lines = [f"ACCOUNTS  {who} that bought last month {'has' if n == 1 else 'have'} not bought this month "
             f"(BHD {fmt.bhd(total)} last month)"]
    top = [[_name(r.get("account"), 44), fmt.bhd(r.get("net_bhd")), _rep(r.get("rep"))] for r in ranked[:TOP_NAMES]]
    lines += _indent(fmt.grid(["Largest of them", "Last month", "Salesman"], top, "lrl"))
    return facts, lines


def reps_section(team: dict | None, active: dict, dim_rows: list[dict] | None, d: date, share_min: Decimal,
                 silent_days: int, cmp_basis: str, compared: bool = True) -> tuple[dict, list[str]]:
    """Reps of the target sheet (the Command Centre's team table): month to date below the
    configured share of the same business days last month, and no invoice for MORE THAN the
    configured number of business days (config brief.silent_business_days). That is the Command
    Centre's own rule for a silent rep (over N, not from N), so this line and its item on the
    action list count the same men when the setting is the portal's figure; should they ever
    not, silent_count_differs puts a line on the screen. A counter or a SIM-only name has no
    target row and is never listed; neither is a rep the portal has switched off. `compared` is
    False while the month has had no business day: nobody is "below" a working day of last month
    on a weekend's sales."""
    if team is None:
        return {}, ["REPS      not read this time (the Command Centre's team table did not answer)"]
    share_text = fmt.pct(share_min, 1, 0)
    now, then = _dim(dim_rows, "mtd", "salesman"), _dim(dim_rows, "cmp", "salesman")
    listed: list[dict] = []
    for row in team.get("rows") or []:
        key = str(row.get("salesman") or "")
        if not key or row.get("no_target") or active.get(key) is False:
            continue
        mtd = cmp_ = None
        if dim_rows is not None:
            mtd = sum((fmt.dec(r.get("net_bhd")) for r in now if metrics.rep_matches(r.get("name"), key)), Decimal(0))
            cmp_ = sum((fmt.dec(r.get("net_bhd")) for r in then if metrics.rep_matches(r.get("name"), key)), Decimal(0))
        last = fmt.as_date(row.get("last_invoice"))
        days = row.get("business_days_since_invoice")
        below = compared and cmp_ is not None and cmp_ > 0 and mtd < share_min * cmp_
        silent = last is None or (days is not None and int(days) > silent_days)
        if below or silent:
            listed.append({"salesman": _clean(row.get("name") or key), "below": below, "silent": silent,
                           "mtd": fmt.q3(mtd) if mtd is not None else None,
                           "same_days_last_month": fmt.q3(cmp_) if cmp_ is not None else None,
                           "last_invoice": last.isoformat() if last else None,
                           "business_days_since": int(days) if days is not None else None})
    listed.sort(key=lambda x: (0 if x["below"] else 1, -((x["same_days_last_month"] or 0) - (x["mtd"] or 0)),
                               -(x["business_days_since"] or 0), x["salesman"]))
    n_below, n_silent = sum(1 for x in listed if x["below"]), sum(1 for x in listed if x["silent"])
    judged = compared and dim_rows is not None
    facts = {"share_min": str(share_min), "silent_business_days": silent_days, "below": n_below if judged else None,
             "silent": n_silent, "salesmen": listed}
    first = (f"{n_below} below {share_text} of {cmp_basis}" if judged
             else "month to date by salesman not read this time" if compared
             else "month to date not compared (no business day yet this month)")
    lines = [f"REPS      {first}; {n_silent} with no invoice for over {_plural(silent_days, 'business day')}"]
    if listed:
        table = [[_name(x["salesman"], 24), fmt.bhd(x["mtd"]) if x["mtd"] is not None else "",
                  fmt.bhd(x["same_days_last_month"]) if x["same_days_last_month"] is not None else "",
                  _day(x["last_invoice"], d) if x["last_invoice"] else "none on record",
                  fmt.units(x["business_days_since"]) if x["business_days_since"] is not None else "",
                  ", ".join(w for w, on in (("below", x["below"]), ("no invoice", x["silent"])) if on)]
                 for x in listed[:MAX_NAMES]]
        lines += _indent(fmt.grid(["Salesman", "Month to date", "Same days last month", "Last invoice",
                                   "Business days since", "To check"], table, "lrrlrl"))
        if len(listed) > MAX_NAMES:
            lines.append(f"  (+{len(listed) - MAX_NAMES} more)")
    return facts, lines


def stock_section(tile: dict | None, have_overview: bool) -> tuple[dict, list[str]]:
    """Until the stock skill is built: the Command Centre's sold-out-with-demand list."""
    if tile is None:
        return {}, ["STOCK     " + ("nothing to show (the Command Centre's list is not available)" if have_overview
                                   else "not read this time")]
    items = list(tile.get("items") or [])
    count = _int(tile.get("value"))
    facts = {"sold_out_with_demand": count, "bhd_60_days": fmt.q3(tile.get("bhd")),
             "items": [{"item": _clean(x.get("item")), "sold_60_days": _int(x.get("qty_60")),
                        "bhd_60_days": fmt.q3(x.get("net_60_bhd"))} for x in items]}
    if count <= 0:
        return facts, ["STOCK     Nothing is sold out that was still asked for in the last 60 days"]
    lines = [f"STOCK     {_plural(count, 'item')} sold out, still asked for (BHD {fmt.bhd(tile.get('bhd'))} sold in "
             "the last 60 days)"]
    top = [[_name(x.get("item"), 56), fmt.units(x.get("qty_60")), fmt.bhd(x.get("net_60_bhd"))] for x in items[:TOP_NAMES]]
    lines += _indent(fmt.grid(["Largest of them", "Sold in 60 days", "BHD"], top))
    return facts, lines


# ── the stock ledger's transfers, and the move rules of the action list ───────────────────────────

MOVE_UNITS = ("to_stores_units", "back_units", "damage_units", "not_received_units", "to_check_count")
MOVE_BHD = ("to_stores_bhd", "not_received_bhd", "to_check_bhd")


def read_moves(days: int = MOVES_DAYS) -> dict:
    """The stock ledger's transfers of its last `days` days, as scripts.ops.moves.brief_facts gives them
    (memoised per ledger): {first, last, ledger_to, to_stores_units, to_stores_bhd, back_units,
    damage_units, not_received_units, not_received_bhd, to_check_count, to_check_bhd, per_store_ok, note}.
    The module is imported here, when it is first asked for: one that cannot be imported raises as one
    that fails does, and either costs the MOVEMENTS line alone."""
    from scripts.ops import moves
    got = moves.brief_facts(days=days)
    if not isinstance(got, dict):
        raise TypeError(f"moves.brief_facts gave {type(got).__name__}, not its facts")
    return got


def _moved(x) -> str:
    """Units (or a count) of the MOVEMENTS line; 'n/a' when moves gave none (never a nought for a blank)."""
    v = _number(x)
    return fmt.units(v) if v is not None else "n/a"


def _moved_bhd(x, basis: str = "") -> str:
    v = _number(x)
    return f"BHD {fmt.bhd(v)}{basis}" if v is not None else "cost not worked out"


def movements_section(got: dict | None, d: date) -> tuple[dict, list[str]]:
    """MOVEMENTS: the stock ledger's transfers of its last seven days (read_moves), BHD at landed cost (the
    /yq-stock basis, which the line names): what the main store sent to the vans and the outlets, what came
    back to it, what went to the damage store; the units not received after their deadline; and what is to
    check, which /yq-moves lists. One sentence on three lines, each inside the window. Under it, moves' own
    note when it gives one, and a line when the figures store by store need a look. `got` None: the facts
    were not read (the warning says why) and the line says so."""
    if got is None:
        return {"read": False}, ["MOVEMENTS not read this time"]
    first, last, ledger_to = (fmt.as_date(got.get(k)) for k in ("first", "last", "ledger_to"))
    upto = last or ledger_to
    days = (last - first).days + 1 if first and last and last >= first else MOVES_DAYS
    head = f"MOVEMENTS last {_plural(days, 'day')}" + (f" to {_day(upto, d)}" if upto else "")
    lines = [f"{head}: to the vans and outlets {_moved(got.get('to_stores_units'))} units "
             f"({_moved_bhd(got.get('to_stores_bhd'), ' ' + MOVES_BASIS)}),",
             f"{LABEL}back to the main store {_moved(got.get('back_units'))}, "
             f"to damage {_moved(got.get('damage_units'))}; "
             f"not received after the deadline {_moved(got.get('not_received_units'))} "
             f"({_moved_bhd(got.get('not_received_bhd'))});",
             f"{LABEL}to check {_moved(got.get('to_check_count'))} ({_moved_bhd(got.get('to_check_bhd'))}): /yq-moves"]
    note = _guarded(_clean(got.get("note")))
    if note:
        lines.append(LABEL + note)
    per_store = got.get("per_store_ok")
    if per_store is False:
        lines.append(LABEL + "the figures store by store need a look too")
    facts: dict = {"read": True, "days": days, "first": first.isoformat() if first else None,
                   "last": last.isoformat() if last else None,
                   "ledger_to": ledger_to.isoformat() if ledger_to else None, "basis": MOVES_BASIS,
                   "per_store_ok": per_store if isinstance(per_store, bool) else None, "note": note or None}
    for key in MOVE_UNITS:
        facts[key] = _number(got.get(key))
    for key in MOVE_BHD:
        v = _number(got.get(key))
        facts[key] = fmt.q3(v) if v is not None else None
    return facts, lines


def record_moves(record: bool = True) -> tuple[dict | None, str | None]:
    """The five move rules of /yq-watch recorded for this ledger before DO TODAY is listed
    (scripts.ops.checks.ensure_recorded: once per ledger, and it never raises; record=False, a preview,
    records nothing). Returns (its answer, None when it gave none; why it did not record, None when it
    did). The module is imported here, when it is first asked for: one that cannot be imported, or that
    raises after all, is a reason like any other, never a failed brief."""
    try:
        from scripts.ops import checks
        got = checks.ensure_recorded(record=record)
    except Exception as exc:  # noqa: BLE001  (one line on the screen, never the brief)
        return None, f"{type(exc).__name__}: {str(exc)[:120]}"
    if not isinstance(got, dict):
        return None, f"it gave {type(got).__name__}, not its answer"
    return got, (None if got.get("ok") else _clean(got.get("why")) or "no reason given")


def move_rules() -> tuple[str, ...]:
    """The names of the five move rules (scripts.ops.checks.MOVE_RULES), the rules the floor applies to;
    () when the module cannot be imported, and the floor then leaves nothing out."""
    try:
        from scripts.ops import checks
        return tuple(str(r) for r in checks.MOVE_RULES)
    except Exception:  # noqa: BLE001  (no rules: DO TODAY lists every open item)
        return ()


def watch_floor(state: dict) -> tuple[Decimal | None, str | None]:
    """(the floor below which a transfer item stays off DO TODAY, why there is none this time). The floor is
    config watch.floor_bhd when it is set, else the figure /yq-watch's replay of the last 90 days left in
    state.json (watch_defaults.floor_bhd), else none. A value in config.json that the config reader refuses
    (no amount, or below nought) is no floor this time, and the reason is one warning line: the brief and
    the delivery go on, every open item listed. A figure in state.json that is no amount is no floor."""
    try:
        cfg = config.load()
    except config.ConfigMissing:
        cfg = {}
    if FLOOR_KEY in cfg:
        try:
            return config.number(FLOOR_KEY), None
        except (config.ConfigMissing, ArithmeticError, ValueError):
            return None, FLOOR_UNREAD
    kept = state.get("watch_defaults")
    v = _number(kept.get("floor_bhd")) if isinstance(kept, dict) else None
    return (v if v is not None and v >= 0 else None), None


def handed_moves(moves, warnings: list[str]) -> tuple[dict | None, dict | None, str | None]:
    """The delivery's own movement step (deliver.moves_step: a process of its own, with its own time budget,
    that recorded the move rules and read the MOVEMENTS facts), as the brief takes it over: (the facts, or
    None; the recording's answer, or None; why the rules were not recorded, None when they were). Nothing of
    it is redone here: a part the step did not give is the warning and the line its own failure would be."""
    step = moves if isinstance(moves, dict) else {}
    facts = step.get("facts")
    if not isinstance(facts, dict):
        warnings.append(f"the movements could not be read: {_clean(step.get('facts_why')) or 'no answer'}")
        facts = None
    answer = step.get("recorded") if isinstance(step.get("recorded"), dict) else None
    if answer is not None and answer.get("ok"):
        return facts, answer, None
    return facts, answer, (_clean(step.get("recorded_why")) or _clean((answer or {}).get("why")) or "no reason given")


def today_section(attention: list[dict] | None, unavailable: list[str], data_date: str, warnings: list[str],
                  profit: str | None = None, record: bool = True, *, floor: Decimal | None = None,
                  floor_rules: tuple = (), moves_problem: str | None = None) -> tuple[dict, list[str]]:
    """DO TODAY: the attention list goes on the register, then at most five open items.

    The register is updated only when the list is whole. When a source the list is worked out from
    did not answer (ATTENTION_SOURCES), an item may be missing only because it could not be worked
    out, and recording would mark it cleared: the register is left as it stood and facts['recorded']
    stays None (the brief then does not count as read). A source the list never reads, the customer
    table for one, costs its own tile and nothing here.

    The move rules were recorded just before (record_moves); `moves_problem` is why they were not, said
    in one line: their items are then listed as they stood. `floor` leaves the items of `floor_rules`
    (the move rules) valued below it off the list (alerts.open_items), and one line counts them
    (alerts.under_floor); with no floor, or no rules, nothing is left out."""
    recorded: dict | None = None
    lines = ["DO TODAY"]
    needed = [name for name in unavailable if name in ATTENTION_SOURCES]
    other = [name for name in unavailable if name not in ATTENTION_SOURCES]
    if attention is None:
        lines.append("(the Command Centre's attention list was not read this time: the list below is as it stood)")
    elif needed:
        warnings.append("the Command Centre could not read: " + ", ".join(needed)
                        + "; the action list was left as it stood")
        lines.append("(part of the Command Centre did not answer: the list below is as it stood)")
    elif record:
        recorded = record_attention(attention, data_date, profit)
    if other:
        warnings.append("the Command Centre could not read: " + ", ".join(other) + " (the action list does not use it)")
    if moves_problem is not None:
        warnings.append(f"the transfer checks were not recorded this time: {moves_problem}")
        lines.append("(the transfer checks were not recorded this time: their items below are as they stood)")
    floored = floor is not None and bool(floor_rules)
    items = alerts.open_items(floor=floor, floor_rules=tuple(floor_rules)) if floored else alerts.open_items()
    # the table has room for two order numbers a row (the register keeps every one); the row that reads the
    # profitability report gets a star in the table, its basis and dates on a line under it
    table: list[dict] = []
    under: list[str] = []
    for n, row in enumerate(items):
        head, basis = basis_apart(str(row.get("title") or ""))
        shown = dict(row, evidence=list(row.get("evidence") or [])[:EVIDENCE_SHOWN])
        table.append(shown if basis is None else dict(shown, title=f"{head} *"))
        if basis is not None and n < DO_TODAY:
            under.append(f"* {row.get('id')}: {basis}")
    rendered = alerts.render(table, limit=DO_TODAY).splitlines()
    more = [rendered.pop()] if under and rendered[-1].startswith("(+") else []     # "(+2 more open...)" stays last
    lines += rendered + under + more
    small: dict[str, int] = {}
    if floored:      # the transfer items under the floor: counted here, listed by /yq-watch
        for rule, n in (alerts.under_floor(floor, tuple(floor_rules)) or {}).items():
            if _count(n):
                small[str(rule)] = _count(n)
        if small:
            lines.append(f"{_plural(sum(small.values()), 'small transfer item')} under BHD {fmt.bhd(floor)} "
                         "counted, not listed")
    older = sum(int(b["count"]) for b in alerts.baseline_summary())
    if older:       # another rule's first finds: counted once here, every one of them on /yq-watch all
        lines.append(f"{_plural(older, 'older item')} counted once: /yq-watch all lists them")
    facts = {"recorded": recorded, "open": [{k: r.get(k) for k in ("id", "key", "level", "title", "detail", "bhd",
                                                                 "count", "evidence", "first_seen")} for r in items],
             "older_items": older, "not_answered": needed + other,
             "floor": ({"bhd": fmt.q3(floor), "rules": list(floor_rules), "under": small} if floored else None)}
    return facts, lines


def silent_count_differs(reps_facts: dict, attention: list[dict] | None, silent_days: int) -> str | None:
    """With the portal's own figure as the setting, the REPS line and the Command Centre's item on
    the action list apply one rule (no invoice for over N business days), so they count the same
    men. Should the two ever differ (a salesman with a target and no active record in the portal
    is on one list and not the other), the screen says so, as something to check. None when they
    agree, when the setting is another figure (each line then names its own), and when either
    side was not read."""
    if not reps_facts or attention is None or silent_days != metrics.SILENT_AFTER_BUSINESS_DAYS:
        return None
    item = next((it for it in attention if it.get("key") == "rep_silence"), None)
    theirs = 0 if item is None else _count(item.get("count"))
    ours = _int(reps_facts.get("silent"))
    if theirs is None or theirs == ours:
        return None
    return (f"REPS counts {_plural(ours, 'salesman', 'salesmen')} with no invoice for over "
            f"{_plural(silent_days, 'business day')} and the Command Centre's list counts {theirs}: to check")


# ── the two screens ────────────────────────────────────────────────────────────────────────────

def _header(d: date, state: dict, rep: str | None, middle: str | None = None) -> list[str]:
    first = "YQ ACCESSORIES BRIEF - "
    if middle:
        first += f"{middle} - "
    loaded = loaded_text(state, d)
    first += f"data to {_day(d)} - " + (f"loaded {loaded}" if loaded else "load time not recorded")
    if rep:
        first += f" - salesman: {_name(rep, 30)}"
    return [first, BASIS_LINE, ""]


def full_brief(d: date, latest: date, part: bool, state: dict, rep: str | None,
               exported: date | None = None, *, record: bool = True, enrich: bool = False,
               moves: dict | None = None) -> tuple[list[str], dict, list[str], dict]:
    """The one-screen brief. Returns (lines, facts, warnings, state patch). `latest` is the latest
    full sale day: D, or on a part day the loaded sale day before it. `exported` is the day the
    reports were exported, when anything says (export_day). record=False (a delivery preview) leaves
    the action register as it stands, the move rules included; enrich=True (the delivery) adds
    facts['enriched'] for the e-mail (scripts/ops/enrich.py), and changes no line of the screen.
    The company brief reads the stock ledger's transfers beside the sales (facts['movements'], the
    MOVEMENTS line) and records the move rules before DO TODAY (facts['do_today']['moves_recorded']).
    `moves` is the delivery's own movement step (deliver.moves_step), which did both already in a process
    of its own: the brief then takes its answer over (handed_moves) and redoes neither."""
    warnings: list[str] = []
    full_to = full_until(d, latest, exported)            # the last complete calendar day
    sat = full_week_end(full_to)                         # ... and the Saturday that ends the last complete week
    cur, cmp_ = metrics.windows("mtd", d)
    wins = {
        "day": Win("day", latest, latest),
        "d3": Win("d3", full_to - timedelta(days=2), full_to),
        "d7": Win("d7", full_to - timedelta(days=6), full_to),
        "lfw": Win("lfw", sat - timedelta(days=6), sat),
        "d30": Win("d30", d - timedelta(days=AVERAGE_DAYS - 1), d),
        "mtd": Win("mtd", cur.start, cur.end),
        "cmp": Win("cmp", cmp_.start, cmp_.end),
    }
    if part:
        wins["part"] = Win("part", d, d)
    week_keys = [f"w{k}" for k in range(AVERAGE_WEEKS, 0, -1)]
    if rep:
        for i, k in enumerate(range(AVERAGE_WEEKS, 0, -1)):      # one salesman: his own four weeks before
            start = wins["lfw"].start - timedelta(days=7 * k)
            wins[week_keys[i]] = Win(week_keys[i], start, start + timedelta(days=6))
    all_wins = json.dumps([w.param() for w in wins.values()])
    top_wins = json.dumps([wins["day"].param(), wins["d3"].param()])
    prev0 = metrics.add_months(d, -1)
    step = None if rep else kpi_step_for(d.strftime("%Y-%m"))
    share_min = config.number("brief.rep_share_min")
    silent_days = int(config.get("brief.silent_business_days"))
    bulk_from = config.number("brief.bulk_invoice_bhd")
    floor, floor_unread = (None, None) if rep else watch_floor(state)    # DO TODAY is a company line
    if floor_unread:
        warnings.append(floor_unread)

    jobs = {
        "top": lambda: db.rpc_params(TOP_INVOICE_SQL, [top_wins, rep or ""]),
        "lapsed": lambda: db.rpc_params(LAPSED_SQL, [prev0.isoformat(), metrics.month_end(prev0).isoformat(),
                                                     cur.start.isoformat(), d.isoformat(), rep or ""]),
    }
    if rep:
        jobs["dim"] = lambda: db.rpc_params(DIM_SQL, [all_wins, rep])
    else:
        month_wins = json.dumps([wins["mtd"].param(), wins["cmp"].param()])
        jobs.update({
            "sales": lambda: db.rpc_params(metrics.SALES_SQL, [all_wins]),
            "dim": lambda: db.rpc_params(DIM_SQL, [month_wins, ""]),
            "trend": lambda: db.rpc_params(metrics.TREND_SQL, [sat.isoformat()]),
            "targets": lambda: db.rpc_params(TARGETS_SQL, [d.strftime("%Y-%m"), metrics.add_months(d, -12).isoformat(),
                                                           cur.start.isoformat()]),
            "overview": read_overview,
        })
        if moves is None:                                # the stock ledger, beside the database reads
            jobs["moves"] = read_moves
    got = _gather(jobs)
    core = "dim" if rep else "sales"
    if isinstance(got.get(core), Exception):
        raise RuntimeError(f"the sales figures could not be read: {got[core]}") from got[core]

    dim_rows = _rows(got, "dim", warnings, "the salesman and account figures")
    totals = totals_from_dim(dim_rows) if rep else totals_from_sales(got["sales"])

    def tot(key: str) -> dict:
        return totals.get(key) or _zero()

    overview = None if rep else _rows(got, "overview", warnings, "the Command Centre")
    payload = (overview or {}).get("payload") if overview else None
    targets_rows = None if rep else _rows(got, "targets", warnings, "the target figures")
    targets = (targets_rows or [None])[0] if targets_rows is not None else None

    if rep:
        weeks_before = [tot(k)["net"] for k in week_keys]
    else:
        trend = _rows(got, "trend", warnings, "the weekly totals")
        weeks_before = _week_values(trend, wins["lfw"].start) if trend is not None else []

    daily = _tile(payload, "sales.daily") if payload else None
    work = metrics.business_days(wins["d30"].start, d)
    if daily and daily.get("avg_business_day") is not None:
        avg_day, avg_note = fmt.dec(daily.get("avg_business_day")), "the Command Centre's daily tile"
    elif work:
        avg_day = tot("d30")["net"] / work
        avg_note = (f"{fmt.bhd(tot('d30')['net'])} in the {AVERAGE_DAYS} days to the data date over their "
                    f"{work} business days")
    else:
        avg_day, avg_note = None, "no business day in the window"

    facts: dict = {"command": COMMAND, "kind": "full", "data_date": d.isoformat(), "latest_day": latest.isoformat(),
                   "full_to": full_to.isoformat(), "exported": exported.isoformat() if exported else None,
                   "part_day": part, "salesman": _clean(rep) if rep else None,
                   "basis": BASIS_LINE, "loaded": loaded_text(state, d)}
    lines = _header(d, state, rep)
    cmp_basis = _basis_words(cmp_.basis)
    compared = metrics.business_days(cur.start, cur.end) > 0     # False while the month has had no business day
    not_judged = "not judged for one salesman" if rep else (None if weeks_before else "weekly totals not read")
    part_f, part_l = sales_section(d, latest, full_to, part, wins, tot, weeks_before, not_judged, avg_day, avg_note,
                                   _rows(got, "top", warnings, "the largest invoices"), cmp_basis, bulk_from, exported)
    facts["sales"] = part_f
    lines += part_l

    patch: dict = {}
    if not rep:
        pace_f, pace_l, printed = pace_section(d, tot("mtd")["net"], step, targets, _tile(payload, "sales.pace"),
                                               show_figures=not state.get("brief_targets_shown"))
        facts["pace"] = pace_f
        lines += pace_l
        if printed:
            patch["brief_targets_shown"] = d.isoformat()

    month = tot("mtd")
    accounts = {str(r.get("name")) for r in _dim(dim_rows, "mtd", "account") if not r.get("is_cash")}
    seen = _plural(len(accounts), "account") + " invoiced" if dim_rows is not None else "accounts invoiced: not read"
    lines.append(f"Month so far: B2B {fmt.bhd(month['b2b'])}, B2C {fmt.bhd(month['b2c'])}; "
                 f"{_plural(month['invoices'], 'invoice')}; {seen}")
    facts["month"] = {"b2b": fmt.q3(month["b2b"]), "b2c": fmt.q3(month["b2c"]), "invoices": month["invoices"],
                      "accounts_invoiced": len(accounts) if dim_rows is not None else None}

    acc_f, acc_l = accounts_section(_rows(got, "lapsed", warnings, "the accounts that have not come back"), bool(rep))
    facts["accounts_not_back"] = acc_f
    lines += [""] + acc_l

    if rep:
        lines += ["", "Pace, reps, stock, movements and DO TODAY are company lines: /yq-brief without rep: shows them."]
        return lines, facts, warnings, patch

    reps_f, reps_l = reps_section(_tile(payload, "team.reps") if payload else None, (overview or {}).get("active") or {},
                                  dim_rows, d, share_min, silent_days, cmp_basis, compared)
    facts["reps"] = reps_f
    lines += [""] + reps_l

    stock_f, stock_l = stock_section(_tile(payload, "products.sold_out") if payload else None, payload is not None)
    facts["stock"] = stock_f
    lines += [""] + stock_l

    if moves is None:
        # the ledger was read beside the sales; the move rules go on the register for this ledger before it is
        # listed (a preview records nothing, and says nothing)
        moved = _rows(got, "moves", warnings, "the movements")
        answer, not_recorded = record_moves(record)
    else:                        # the delivery's movement step did both, in its own process and time budget
        moved, answer, not_recorded = handed_moves(moves, warnings)
    moves_f, moves_l = movements_section(moved, d)
    facts["movements"] = moves_f
    lines += [""] + moves_l

    profit = profit_basis(fmt.as_date((overview or {}).get("margin_report_date")), d, state)
    attention = _attention(payload) if payload else None
    today_f, today_l = today_section(attention, list((payload or {}).get("unavailable") or []), d.isoformat(),
                                     warnings, profit, record=record, floor=floor,
                                     floor_rules=move_rules() if floor is not None else (),
                                     moves_problem=not_recorded if record else None)
    facts["do_today"] = dict(today_f, profit_basis=profit,
                             moves_recorded={"ok": not_recorded is None,
                                             "why": not_recorded or _clean((answer or {}).get("why")) or None})
    lines += [""] + today_l
    if enrich:
        from scripts.ops import enrich as enrich_mod
        facts["enriched"] = enrich_mod.build(d, overview, dim_rows)
    # the brief counts as read only when the whole screen was there: the Command Centre answered and its
    # attention list went on the register. Otherwise the next /yq-brief prints the screen again, not two lines.
    if today_f.get("recorded") is not None:
        patch.update(last_brief_data_date=d.isoformat(), last_brief_load_run=_load_run(state))
        differs = silent_count_differs(reps_f, attention, silent_days)      # only a whole list can be compared
        if differs:
            warnings.append(differs)
    return lines, facts, warnings, patch


def window_brief(window: str, d: date, latest: date, part: bool, state: dict, rep: str | None,
                 top_n: int, exported: date | None = None) -> tuple[list[str], dict, list[str]]:
    """One window in detail: totals, by salesman, top accounts, top products."""
    warnings: list[str] = []
    win, title, notes = resolve_window(window, d, latest, full_until(d, latest, exported))
    wins = [win]
    cmp_ = None
    if window == "mtd":
        _cur, cmp_ = metrics.windows("mtd", d)
        wins.append(Win("cmp", cmp_.start, cmp_.end))
    as_json = json.dumps([w.param() for w in wins])
    jobs = {"dim": lambda: db.rpc_params(DIM_SQL, [as_json, rep or ""])}
    if not rep:
        jobs["sales"] = lambda: db.rpc_params(metrics.SALES_SQL, [as_json])
    got = _gather(jobs)
    for name in jobs:
        if isinstance(got.get(name), Exception):
            raise RuntimeError(f"the sales figures could not be read: {got[name]}") from got[name]
    dim_rows = got["dim"]
    totals = totals_from_dim(dim_rows) if rep else totals_from_sales(got["sales"])
    t = totals.get("win") or _zero()

    lines = _header(d, state, rep, f"{title}: {_span(win.start, win.end, d)}")
    if part and win.end >= d:
        notes.append(f"{_day(d)} is a part day (the reports were exported that day): it is in this window.")
    elif part and window in ("day", "3d", "7d", "last-week"):       # the windows the part day moves back
        notes.append(f"{_day(d)} is a part day (the reports were exported that day): this window stops before it.")
    elif win.end > d and exported is not None:                      # 3d, 7d, last-week: complete days after D
        notes.append(no_sale_note(d, exported, win.end, "this window runs to"))
    lines += notes + ([""] if notes else [])

    salesmen = _by_salesman(_dim(dim_rows, "win", "salesman"))
    accounts = sorted(_dim(dim_rows, "win", "account"), key=lambda r: (-fmt.dec(r.get("net_bhd")), str(r.get("name"))))
    named = [r for r in accounts if not r.get("is_cash")]
    cash = [r for r in accounts if r.get("is_cash")]
    cash_net = sum((fmt.dec(r.get("net_bhd")) for r in cash), Decimal(0))
    cash_inv = sum(_int(r.get("invoices")) for r in cash)
    named_net = sum((fmt.dec(r.get("net_bhd")) for r in named), Decimal(0))
    skus = sorted(_dim(dim_rows, "win", "sku"), key=lambda r: (-fmt.dec(r.get("net_bhd")), str(r.get("name"))))
    units = sum((fmt.dec(r.get("units")) for r in skus), Decimal(0))

    by_rep = sum((s["net"] for s in salesmen), Decimal(0))
    # each row is rounded to the fils by the database, so the rows may differ from the total by half a fils each
    if abs(by_rep - t["net"]) > Decimal("0.0005") * (len(_dim(dim_rows, "win", "salesman")) + 1):
        warnings.append(f"the salesman table adds up to {fmt.bhd(by_rep)} and the total is {fmt.bhd(t['net'])}: to check")

    lines.append(f"TOTAL     {fmt.bhd(t['net'])} in {_plural(t['invoices'], 'invoice')}; {fmt.units(units)} units; "
                 f"B2B {fmt.bhd(t['b2b'])}, B2C {fmt.bhd(t['b2c'])}")
    lines.append(f"          {_plural(len(named), 'account')} invoiced {fmt.bhd(named_net)}; cash customers "
                 f"{fmt.bhd(cash_net)} in {_plural(cash_inv, 'invoice')}")
    facts: dict = {"command": COMMAND, "kind": "window", "window": window, "title": title, "data_date": d.isoformat(),
                   "exported": exported.isoformat() if exported else None,
                   "salesman": _clean(rep) if rep else None, "basis": BASIS_LINE, **win.facts(),
                   "total": {"bhd": fmt.q3(t["net"]), "invoices": t["invoices"], "units": units, "b2b": fmt.q3(t["b2b"]),
                             "b2c": fmt.q3(t["b2c"]), "accounts_invoiced": len(named), "accounts_bhd": fmt.q3(named_net),
                             "cash_customers_bhd": fmt.q3(cash_net), "cash_customer_invoices": cash_inv}}
    if cmp_ is not None:
        c = totals.get("cmp") or _zero()
        basis = _basis_words(cmp_.basis)
        compared = metrics.business_days(win.start, win.end) > 0   # as on the full brief: no percentage on a weekend
        change = fmt.change(t["net"], c["net"]) if compared else None
        lines.append(f"          against {basis} ({_span(cmp_.start, cmp_.end, d)}) {fmt.bhd(c['net'])}: {change}"
                     if compared else f"          {NO_BUSINESS_DAY}")
        facts["compare"] = {"start": cmp_.start.isoformat(), "end": cmp_.end.isoformat(), "bhd": fmt.q3(c["net"]),
                            "basis": basis, "compared": compared, "change": change}
    if t["net"] == 0 and not salesmen:
        lines += ["", "No sales are loaded in this window."]
        return lines, facts, warnings

    lines.append("")
    lines += fmt.grid(["BY SALESMAN", "BHD", "Share", "Invoices", "Units"],
                      [[_name(s["name"], 30), fmt.bhd(s["net"]), fmt.pct(s["net"], t["net"]), fmt.units(s["invoices"]),
                        fmt.units(s["units"])] for s in salesmen]).splitlines()
    facts["by_salesman"] = [{"salesman": _clean(s["name"]), "bhd": fmt.q3(s["net"]), "invoices": s["invoices"],
                             "units": s["units"]} for s in salesmen]

    lines.append("")
    head = f"TOP ACCOUNTS ({min(top_n, len(named))} of {len(named)})"
    lines += fmt.grid([head, "BHD", "Share", "Invoices"],
                      [[_name(r.get("name"), 48), fmt.bhd(r.get("net_bhd")), fmt.pct(r.get("net_bhd"), t["net"]),
                        fmt.units(_int(r.get("invoices")))] for r in named[:top_n]]).splitlines()
    facts["accounts"] = [{"account": _clean(r.get("name")), "bhd": fmt.q3(r.get("net_bhd")),
                          "invoices": _int(r.get("invoices"))} for r in named]

    lines.append("")
    head = f"TOP PRODUCTS ({min(top_n, len(skus))} of {len(skus)})"
    lines += fmt.grid([head, "BHD", "Share", "Units"],
                      [[_name(r.get("name"), 60), fmt.bhd(r.get("net_bhd")), fmt.pct(r.get("net_bhd"), t["net"]),
                        fmt.units(r.get("units"))] for r in skus[:top_n]]).splitlines()
    facts["products"] = [{"item": _clean(r.get("name")), "code": _clean(r.get("code")) or None,
                          "bhd": fmt.q3(r.get("net_bhd")), "units": fmt.dec(r.get("units"))} for r in skus]
    return lines, facts, warnings


def _load_run(state: dict) -> str:
    return str(_last_load(state).get("run") or "")


def is_nothing_new(d: date, state: dict) -> bool:
    """No new data since the last full brief: the same data date, and no other load on record
    (a second export of the same day, a part day completed, is new data on the same date)."""
    return (state.get("last_brief_data_date") == d.isoformat()
            and state.get("last_brief_load_run", _load_run(state)) == _load_run(state))


def nothing_new(d: date, state: dict) -> list[str]:
    """Two lines when no new data has been loaded since the last brief."""
    waiting = status.unloaded_drops(_load_run(state) or None)
    loaded = loaded_text(state, d)
    first = (f"Data to {_day(d)}" + (f" (loaded {loaded})" if loaded else "") + ": nothing new since the last brief. "
             "For the same brief again: /yq-brief again")
    second = ("Waiting: new reports in " + ", ".join(waiting[-3:]) + ": run /yq-load." if waiting
              else "Waiting: no drop folder newer than the last load.")
    return [first, second]


# ── the command ────────────────────────────────────────────────────────────────────────────────

def add_arguments(parser) -> None:
    # a plain metavar: argparse on Python 3.11 cannot wrap a usage line whose metavar holds spaces
    parser.add_argument("spec", nargs="*", metavar="word",
                        help=f"a window ({WINDOW_HELP}); rep:NAME for one salesman; top:N for the rows of a window's "
                             "tables; 'again' for the same brief again. Words and options (--top, --again, --json, "
                             "--data-root) may come in any order")
    parser.add_argument("--again", action="store_true", help="the full brief even when the data has not changed")
    parser.add_argument("--top", type=int, default=TOP_DEFAULT,
                        help="rows in the account and product tables of a window (the same as the word top:N)")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("..", "-to-")).strip("-")


def file_stem(window: str | None, rep: str | None) -> str:
    """The name the screen and the facts are saved under: brief, brief-<window>, brief-rep-<name>.
    A name with no Latin letter is stood for by its hash."""
    stem = "brief" + (f"-{_slug(window)}" if window else "")
    return stem + (f"-rep-{_slug(rep) or alerts.ref_hash(rep)}" if rep else "")


def latest_full_day(d: date, anchors: dict, state: dict) -> tuple[date | None, bool, date]:
    """(the export day, is D a part day, the latest full sale day) for the brief of data date D."""
    exported = export_day(d, anchors, state)
    part = exported == d                    # the reports were exported on D itself: D may not be complete
    latest = d
    if part:
        prev = fmt.as_date((db.rpc_params(PREV_DAY_SQL, [d.isoformat()]) or [{}])[0].get("prev_day"))
        if prev is None:
            part = False
        else:
            latest = prev
    return exported, part, latest


def save_screen(d: date, stem: str, text: str, facts: dict) -> list:
    """exports/ops/<D>/<stem>.txt and .json: what the screen said, and its facts."""
    folder = paths.exports_dir(d)
    return [paths.write_text(folder / f"{stem}.txt", text + "\n"),
            paths.write_text(folder / f"{stem}.json", json.dumps(_jsonable(facts), indent=1, ensure_ascii=False) + "\n")]


def full_screen(*, record: bool = True, enrich: bool = False, moves: dict | None = None) -> dict:
    """The full brief exactly as /yq-brief works it out, for the delivery (scripts/ops/deliver.py):
    {d, text, facts, warnings, patch}. Nothing is written here; the caller saves the screen and the
    state patch (record=False, a preview, records nothing and stamps nothing). `moves`: the delivery's
    own movement step (deliver.moves_step), taken over as it is (full_brief)."""
    anchors = status.anchors()
    d = fmt.as_date(anchors.get("focus_to"))
    if d is None:
        raise BriefInput("No sales are loaded yet: there is nothing to brief.")
    state = status.read_state()
    exported, part, latest = latest_full_day(d, anchors, state)
    lines, facts, warnings, patch = full_brief(d, latest, part, state, None, exported, record=record, enrich=enrich,
                                               moves=moves)
    facts["warnings"] = warnings
    return {"d": d, "text": "\n".join(_guarded(ln).rstrip() for ln in lines), "facts": facts,
            "warnings": warnings, "patch": patch}


def unshown_brief(d: date, state: dict) -> str | None:
    """The brief a load's delivery made right after the load (state brief_unshown), while it has not
    been shown in the chat: the first plain /yq-brief after a load shows it as it was made, with one
    line on top, instead of the two 'nothing new' lines. None when there is none to show."""
    kept = state.get("brief_unshown")
    if not isinstance(kept, dict) or kept.get("data_date") != d.isoformat() or kept.get("run") != _load_run(state):
        return None
    try:
        text = paths.exports_dir(d).joinpath("brief.txt").read_text(encoding="utf-8").rstrip("\n")
    except OSError:
        return None
    head = (f"(Made at {kept.get('at') or '?'} right after load {kept.get('run')}; the e-mail carries the same "
            "figures. For a brief worked out anew: /yq-brief again)")
    return head + "\n" + text


TEAM_WORD = "team"


def team_message() -> str:
    """The team message the last load's delivery made (exports/ops/<D>/delivery/<run>/whatsapp.txt), as it
    was made; one line on top says where it came from. Nothing is worked out here."""
    run = _load_run(status.read_state())
    base = paths.exports_dir()
    found = sorted(base.glob(f"*/delivery/{run}/whatsapp.txt")) if (run and base.is_dir()) else []
    if not found:
        return ("No team message yet: it is made right after each load (the e-mail carries it too). After the "
                "next /yq-load, type /yq-brief team.")
    text = found[-1].read_text(encoding="utf-8").rstrip("\n")
    return (f"Team message of load {run}: copy the block below into the team group (or open whatsapp.html in "
            f"{found[-1].parent} to change it first).\n" + text)


def run(args) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    words = [w.lower() for raw in (getattr(args, "spec", None) or []) for w in str(raw).split()]
    if words == [TEAM_WORD]:
        return dict(out, text=team_message(), flags={"team": True})
    try:
        window, typed_rep, again_word, top_word = parse_spec(getattr(args, "spec", None))
    except BriefInput as exc:
        return dict(out, ok=False, text=str(exc), exit=1)
    again = bool(getattr(args, "again", False)) or again_word
    top_n = max(1, int(top_word or getattr(args, "top", None) or TOP_DEFAULT))      # the word wins over --top

    anchors = status.anchors()
    d = fmt.as_date(anchors.get("focus_to"))
    if d is None:
        return dict(out, ok=False, text="No sales are loaded yet: there is nothing to brief.", exit=2)
    out["data_date"] = d.isoformat()
    state = status.read_state()
    is_full = window is None and typed_rep is None
    if is_full and not again and is_nothing_new(d, state):
        stored = unshown_brief(d, state)
        if stored is not None:              # the brief the load's delivery made, shown once in the chat
            status.write_state({"brief_unshown": None})
            return dict(out, text=stored, flags={"open": status.register_counts(), "stored": True})
        return dict(out, text="\n".join(_guarded(ln) for ln in nothing_new(d, state)),
                    flags={"open": status.register_counts()})

    exported, part, latest = latest_full_day(d, anchors, state)

    try:
        rep = resolve_rep(typed_rep, [r.get("rep") for r in db.rpc(REP_NAMES_SQL)]) if typed_rep else None
        if window is None:
            lines, facts, warnings, patch = full_brief(d, latest, part, state, rep, exported)
        else:
            lines, facts, warnings = window_brief(window, d, latest, part, state, rep, top_n, exported)
            patch = {}
    except BriefInput as exc:
        return dict(out, ok=False, text=str(exc), exit=1)

    text = "\n".join(_guarded(ln).rstrip() for ln in lines)
    facts["warnings"] = warnings
    files = save_screen(d, file_stem(window, rep), text, facts)
    if patch:
        status.write_state(patch)
    flags = {"open": status.register_counts()}
    recorded = ((facts.get("do_today") or {}).get("recorded")) if isinstance(facts.get("do_today"), dict) else None
    if recorded:
        flags["recorded"] = recorded
    return dict(out, text=text, flags=flags, files=[str(f) for f in files], warnings=warnings)
