"""Weekly AI Head pack (release R7b, plan §19 / §20): a dated, read-only data pack for the review.

    python -m scripts.ai_head.pack                          # the last completed Sun-Sat week (Bahrain)
    python -m scripts.ai_head.pack --week-ending 2026-09-26
    python -m scripts.ai_head.pack --verify                 # also run scripts/verify_numbers (read-only) into the pack
    python -m scripts.ai_head.pack --monthly 2026-09        # the month-close pack (§20) instead

Connection (never writes; every statement runs in a read-only transaction):
  AI_HEAD_DATABASE_URL  the ai_head_ro login (scripts/r7b_ai_head_migration.sql; SELECT on the v_agent_*
                        views only, a member of no role). Preferred.
  DATABASE_URL          fallback, with a warning: the owner login in a read-only transaction. Until the
                        migration runs the views are missing; the pack then builds the weekly metrics
                        from the base tables through scripts/weekly_report.load() and Focus context from
                        v_sales, and names every file it could not build.

Output: exports/ai_head/<week-ending>/ (gitignored: commercial data), or exports/ai_head/month-YYYY-MM/.
Every text cell passes app.ai_insights.mask_text (8+ digit runs and email addresses masked) on its way
into a CSV or JSON file; the views already mask names, narration and search terms, and carry no phone,
email, token, IP or raw device id. Money is BHD, Decimal, 3 dp. Weeks are Sunday-Saturday, Bahrain time.
Focus windows anchor to the last Focus sale date, never today.

What the review reads first: manifest.json (what was built, from where) and trust.json (the data trust
gate: as-on dates, staleness, totals, the Focus match rate, verify_numbers). Then weekly_metrics.json
(scripts/weekly_report.compute(): the numbers the management email carries) and the rest.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.ai_insights import mask_text  # noqa: E402
from scripts import weekly_report as wr  # noqa: E402

BH = wr.BH
Q3 = Decimal("0.001")
VAT = Decimal("0.10")                 # price-book rates include 10 % VAT (app_settings.shop_vat_rate)
STALE_AFTER_DAYS = 3                  # = app.reports.STALE_AFTER_DAYS (a missed upload)
MARKET_STALE_DAYS = 2                 # no merchant event for this long = tracking may be down
JOIN_MATCH_MIN_PCT = Decimal("80")    # the importer's voucher<->invoice floor (data rule 3)
OPEN_STATUSES = ("new", "confirmed", "packed", "out_for_delivery")
WEEKS_HISTORY = 13
AGENT_VIEWS = ("v_agent_shop_orders", "v_agent_shop_lines", "v_agent_shop_events", "v_agent_funnel_daily",
               "v_agent_search_demand", "v_agent_rep_governance", "v_agent_statements", "v_agent_focus_links",
               "v_agent_customer_regulars", "v_agent_focus_sales", "v_agent_items", "v_agent_market_signals",
               "v_agent_data_trust", "v_agent_insights")
READ_ONLY_SQL = re.compile(r"^\s*(select|with|show|set\s+(local\s+)?statement_timeout)\b", re.I)
# a second statement or a data-changing CTE never passes either (the session is read-only anyway)
WRITE_WORDS = re.compile(r"\b(insert|update|delete|merge|alter|drop|create|grant|revoke|truncate|copy|call|vacuum|lock)\b",
                         re.I)


# ── helpers ──────────────────────────────────────────────────────────────────

def D(x) -> Decimal:
    return Decimal(str(x if x is not None and x != "" else 0))


def q3(x) -> Decimal:
    return D(x).quantize(Q3, ROUND_HALF_UP)


def pct(n, d, dp: int = 1) -> Decimal | None:
    if not d:
        return None
    return (D(n) / D(d) * 100).quantize(Decimal(10) ** -dp, ROUND_HALF_UP)


def strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or ""))


def jsonable(x):
    """Decimal -> str (exact), date/datetime -> ISO, sets -> sorted lists, tuples -> lists, text masked."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(jsonable(v) for v in x)
    if isinstance(x, Decimal):
        exp = x.as_tuple().exponent
        return str(x) if not isinstance(exp, int) or exp >= -3 else str(x.quantize(Q3, ROUND_HALF_UP))
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, str):
        return mask_text(x)
    return x


def week_bounds(week_end: date) -> tuple[date, date]:
    return week_end - timedelta(days=6), week_end


def month_bounds(ym: str) -> tuple[date, date]:
    y, m = (int(p) for p in ym.split("-"))
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def rep_base(name: str | None) -> str:
    """'Name - Acc WH' -> 'name' (the rule app/followups.rep_base and weekly_report.rep_key share)."""
    return wr.rep_key(name)


# ── connection (read-only, always) ───────────────────────────────────────────

class ReadOnly:
    """A read-only session: conn.read_only = True, and every statement must be a SELECT / WITH (or
    the session's own statement_timeout) — anything else raises before it reaches the database."""

    def __init__(self, conn, mode: str, role: str | None, warnings: list[str]):
        self.conn, self.mode, self.role, self.warnings = conn, mode, role, warnings

    def q(self, sql: str, *params) -> list[dict]:
        if not READ_ONLY_SQL.match(sql) or ";" in sql.strip().rstrip(";") or WRITE_WORDS.search(sql):
            raise RuntimeError("pack: refusing a statement that is not a read: " + sql.strip()[:60])
        return self.conn.execute(sql, params or None).fetchall()

    def available(self) -> set[str]:
        rows = self.q("""select table_name from information_schema.tables
                          where table_schema = 'public' and table_name = any(%s)
                            and has_table_privilege(current_user, format('public.%%I', table_name), 'SELECT')""",
                      list(AGENT_VIEWS))
        return {r["table_name"] for r in rows}


def choose_dsn(env: dict | None = None) -> tuple[str | None, str, list[str]]:
    """(dsn, mode, warnings): the ai_head_ro login when AI_HEAD_DATABASE_URL is set, else DATABASE_URL
    with a warning, else nothing."""
    env = os.environ if env is None else env
    if env.get("AI_HEAD_DATABASE_URL"):
        return env["AI_HEAD_DATABASE_URL"], "ai_head_ro", []
    if env.get("DATABASE_URL"):
        return env["DATABASE_URL"], "owner_fallback", [
            "AI_HEAD_DATABASE_URL is not set: the pack used DATABASE_URL (the owner login) in a READ-ONLY "
            "transaction. Set up the ai_head_ro login (scripts/ai_head/README.md) so the pack cannot see "
            "anything but the v_agent_* views."]
    return None, "none", ["Neither AI_HEAD_DATABASE_URL nor DATABASE_URL is set."]


def connect(env: dict | None = None) -> ReadOnly:
    import psycopg
    from psycopg.rows import dict_row

    dsn, mode, warnings = choose_dsn(env)
    if not dsn:
        raise SystemExit("pack: set AI_HEAD_DATABASE_URL (the ai_head_ro login) in .env -- see scripts/ai_head/README.md")
    conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=15)
    conn.read_only = True
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ     # every file from one snapshot
    conn.execute("set statement_timeout = '15s'")
    role = conn.execute("select current_user as u").fetchone()["u"]
    return ReadOnly(conn, mode, role, warnings)


# ── the weekly metrics: weekly_report.compute() on data read through the views ─

def load_from_views(db: ReadOnly, week_start: date, week_end: date) -> dict:
    """The dict scripts/weekly_report.load() returns, read through the v_agent_* views: same keys, same
    row shapes, so compute() runs unchanged. The views carry no person's name (customer_name is None)
    and hashed device / session keys (the counts are the same)."""
    t0 = datetime.combine(week_start, datetime.min.time(), BH)
    t1 = datetime.combine(week_end + timedelta(days=1), datetime.min.time(), BH)
    p0 = t0 - timedelta(days=7)
    orders = db.q("""
        select order_id as id, order_no, status, total_requested_bhd as total_bhd, total_confirmed_bhd, order_kind,
               customer_id, customer_shop, null::text as customer_name, customer_area, created_at, confirmed_at,
               delivered_at, cancelled_at, source, salesman_id, rep, rep_focus_name as focus_name
          from v_agent_shop_orders
         where created_at >= %s and created_at < %s and not is_test
         order by created_at""", t0, t1)
    ids = [o["id"] for o in orders]
    lines = db.q("""
        select order_id, upper(item_code) as code, display_name, qty, line_total_bhd,
               coalesce(category, 'Other') as category
          from v_agent_shop_lines where order_id = any(%s)""", ids) if ids else []
    prev = db.q("""select count(*) as n, coalesce(sum(total_requested_bhd), 0) as v from v_agent_shop_orders
                    where created_at >= %s and created_at < %s and not is_test and status <> 'cancelled'""", p0, t0)[0]
    open_now = db.q("""
        select order_no, status, total_requested_bhd as total_bhd, created_at, confirmed_at, rep
          from v_agent_shop_orders
         where status = any(%s) and not is_test
         order by created_at""", list(OPEN_STATUSES))
    events = db.q("""
        select ts, session_key as session_id, device_key as device_id, event, referral_code,
               case when search_term is not null then jsonb_build_object('q', search_term) end as meta
          from v_agent_shop_events where ts >= %s and ts < %s""", t0, t1)
    reps = {r["referral_code"]: r["rep"] for r in
            db.q("select referral_code, rep from v_agent_rep_governance where referral_code is not null")}
    focus_max = db.q("select max(sale_date) as d from v_agent_focus_sales")[0]["d"]
    base_from = week_start - timedelta(days=28 + 180)
    focus = db.q("""
        select invoice_no, sale_date, customer_name, is_cash_customer, salesman_raw,
               upper(coalesce(sku_code, item_name)) as code, quantity, taxable_bhd, gross_bhd, narration
          from v_agent_focus_sales
         where channel = 'B2B' and division = 'Accessories' and not is_giveaway
           and sale_date >= %s and sale_date <= %s""", base_from, week_end + timedelta(days=wr.MATCH_DAYS_AFTER))
    return {"orders": orders, "lines": lines, "prev": prev, "open_now": open_now, "events": events,
            "reps": reps, "focus_max": focus_max, "focus": focus, "t0": t0, "t1": t1}


def load_weekly(db: ReadOnly, have: set[str], week_start: date, week_end: date) -> tuple[dict | None, str]:
    need = {"v_agent_shop_orders", "v_agent_shop_lines", "v_agent_shop_events", "v_agent_rep_governance",
            "v_agent_focus_sales"}
    if need <= have:
        return load_from_views(db, week_start, week_end), "views"
    if db.mode == "owner_fallback":
        # before the migration: weekly_report's own read-only loader on the base tables, then the same
        # redaction the views apply (no person's name; shop names, search terms, Focus names and
        # narration masked), so both paths give compute() the same data
        d = wr.load(week_start, week_end)
        for o in d["orders"]:
            o["customer_name"] = None
            o["customer_shop"] = mask_text(o.get("customer_shop"))
            o["customer_area"] = mask_text(o.get("customer_area"))
        for e in d["events"]:
            q = (e.get("meta") or {}).get("q") if e["event"] in ("search", "search_zero") else None
            e["meta"] = {"q": mask_text(q.strip().lower())} if isinstance(q, str) else None
        for r in d["focus"]:
            r["customer_name"] = mask_text(r.get("customer_name"))
            r["narration"] = mask_text(r.get("narration"))
        return d, "base_tables"
    return None, "missing: " + ", ".join(sorted(need - have))


def weekly_metrics(d: dict, week_start: date, week_end: date, now: datetime) -> dict:
    m = wr.compute(d, week_start, week_end, now)
    out = dict(m)
    out["highlights_text"] = [strip_html(h) for h in wr.highlights(m)]
    out["rule_actions_text"] = [strip_html(a) for a in wr.actions(m)]
    out["subject_line"] = wr.subject_line(m, preview=False)
    imp = m["impact"]
    out["impact"] = {k: v for k, v in imp.items() if k != "matches"}
    out["impact"]["matches"] = [{"order_no": mt["order"]["order_no"], "invoice": wr.inv_label(mt["invoice"]),
                                 "how": mt["how"], "overlap": mt["overlap"], "codes": sorted(mt["codes"])}
                                for mt in imp["matches"].values()]
    out["status"] = [{"label": label, "orders": len(lst),
                      "value_bhd": sum((D(o["total_bhd"]) for o in lst), Decimal(0)),
                      "order_nos": [o["order_no"] for o in lst]} for label, lst in m["status"]]
    out["small"] = [o["order_no"] for o in m["small"]]
    out["test_like"] = [o["order_no"] for o in m["test_like"]]
    out["waiting"] = [{"order_no": w["order_no"], "rep": w["rep"], "total_bhd": w["total_bhd"],
                       "age_hours": round(w["age_h"], 1), "focus_invoice": w.get("focus")} for w in m["waiting"]]
    out["reps"] = [{"rep": r[0], "orders": r[1], "value_bhd": r[2], "delivered": r[3], "waiting": r[4],
                    "link_visitors": r[5]} for r in m["reps"]]
    out["days"] = [{"day": dd, "visitors": v, "orders": n, "value_bhd": val} for dd, v, n, val in m["days"]]
    out["products"] = [{"code": c, "name": n, "units": u, "orders": o, "value_bhd": v} for c, n, u, o, v in m["products"]]
    out["shops"] = [{"shop": s, "area": a, "orders": o, "value_bhd": v} for s, a, o, v in m["shops"]]
    return out


# ── the data trust gate (pure) ────────────────────────────────────────────────

def _as_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.astimezone(BH).date() if v.tzinfo else v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def trust_gate(sources: list[dict], today: date, *, orders: list[dict] | None = None,
               lines: list[dict] | None = None, verify: dict | None = None, weekly_match: dict | None = None,
               week_end: date | None = None) -> dict:
    """Step 1 of every review: is the data fresh and whole enough to trust? Returns the per-source
    as-on dates, the checks (ok / warn / stale / fail) and a verdict the report must lead with when
    it is not 'ok'."""
    by = {s["source"]: s for s in sources}
    checks: list[dict] = []

    def add(name: str, status: str, detail: str, value=None):
        checks.append({"check": name, "status": status, "detail": detail, "value": value})

    fs = by.get("focus_sales") or {}
    f_as_of = _as_date(fs.get("as_of"))
    if f_as_of is None:
        add("focus_sales_fresh", "fail", "No Focus sales loaded.")
    else:
        behind = (today - f_as_of).days
        add("focus_sales_fresh", "stale" if behind > STALE_AFTER_DAYS else "ok",
            f"Focus sales run to {f_as_of:%a %d %b %Y}, {behind} day(s) before {today:%d %b}.", behind)
        if week_end and f_as_of < week_end:
            add("focus_covers_week", "warn",
                f"Focus data stops {(week_end - f_as_of).days} day(s) before the week ends: Focus figures for "
                f"the week are partial and are compared on the same weekdays only.", (week_end - f_as_of).days)
    sb = by.get("stock_balance") or {}
    s_as_of = _as_date(sb.get("as_of"))
    if s_as_of is None:
        add("stock_fresh", "fail", "No stock snapshot loaded.")
    else:
        behind = (today - s_as_of).days
        add("stock_fresh", "stale" if behind > STALE_AFTER_DAYS else "ok",
            f"Stock snapshot as on {s_as_of:%d %b %Y} ({behind} day(s) old).", behind)
    ar = by.get("ar_ageing_totals") or {}
    if not ar.get("as_of"):
        add("ar_loaded", "warn", "No receivables ageing totals loaded: AR risk cannot be judged this week.")
    else:
        add("ar_loaded", "ok", f"Receivables ageing as on {_as_date(ar['as_of']):%d %b %Y}.")
    up = by.get("focus_upload") or {}
    if up:
        last = _as_date(up.get("loaded_at"))
        add("last_upload", "ok" if (up.get("status") or "ok") == "ok" else "warn",
            f"Last good Focus upload {last:%d %b %Y}" + (f"; the latest run says {up.get('status')}." if
                                                           (up.get("status") or "ok") != "ok" else ".")
            if last else "No successful Focus upload recorded.", up.get("status"))
        jm = up.get("metric")
        if jm is not None:
            add("join_match", "ok" if D(jm) >= JOIN_MATCH_MIN_PCT else "fail",
                f"Voucher<->invoice join match on the last upload: {D(jm)} % (floor {JOIN_MATCH_MIN_PCT} %).", jm)
    sku = by.get("focus_sku_match_90d") or {}
    if sku.get("metric") is not None:
        v = D(sku["metric"])
        add("sku_match", "ok" if v >= 95 else "warn",
            f"{v} % of the last 90 days of Focus lines map to a catalog SKU.", v)
    me = by.get("marketplace_events") or {}
    m_last = _as_date(me.get("as_of"))
    if m_last is not None:
        behind = (today - m_last).days
        add("marketplace_tracking", "warn" if behind > MARKET_STALE_DAYS else "ok",
            f"Last merchant event {m_last:%d %b} ({behind} day(s) ago).", behind)

    if orders is not None:
        by_order = defaultdict(lambda: [Decimal(0), Decimal(0), 0, 0])     # requested, confirmed, lines, confirmed lines
        for ln in lines or []:
            b = by_order[ln["order_id"]]
            b[0] += D(ln.get("line_total_bhd"))
            if ln.get("line_total_confirmed") is not None:
                b[1] += D(ln["line_total_confirmed"])
                b[3] += 1
            b[2] += 1
        bad_total, bad_sub, bad_conf = [], [], []
        for o in orders:
            expect = (D(o.get("subtotal_bhd")) - D(o.get("discount_bhd")) + D(o.get("delivery_bhd"))
                      + D(o.get("small_order_fee_bhd")))
            if q3(o.get("total_requested_bhd")) != q3(expect):
                bad_total.append(o["order_no"])
            b = by_order.get(o["order_id"])
            if b and q3(o.get("subtotal_bhd")) != q3(b[0]):
                bad_sub.append(o["order_no"])
            if (b and b[2] and b[3] == b[2] and o.get("subtotal_confirmed_bhd") is not None
                    and q3(o["subtotal_confirmed_bhd"]) != q3(b[1])):
                bad_conf.append(o["order_no"])
        add("order_totals", "ok" if not bad_total else "fail",
            "Every order total = subtotal - discount + delivery + small-order fee." if not bad_total else
            f"{len(bad_total)} order total(s) do not add up: {', '.join(bad_total[:8])}.", len(bad_total))
        add("order_lines_sum", "ok" if not bad_sub else "fail",
            "Every order subtotal = the sum of its lines." if not bad_sub else
            f"{len(bad_sub)} subtotal(s) differ from their lines: {', '.join(bad_sub[:8])}.", len(bad_sub))
        add("confirmed_lines_sum", "ok" if not bad_conf else "warn",
            "Confirmed subtotals match their confirmed lines." if not bad_conf else
            f"{len(bad_conf)} confirmed subtotal(s) differ from the confirmed lines: {', '.join(bad_conf[:8])}.",
            len(bad_conf))
        since = datetime.combine(today - timedelta(days=30), datetime.min.time(), BH)
        delivered = [o for o in orders if not o.get("is_test") and o.get("status") == "delivered"
                     and o.get("delivered_at") and o["delivered_at"] >= since]
        linked = sum(1 for o in delivered if o.get("focus_link_state") == "linked")
        typed = sum(1 for o in delivered if o.get("focus_link_state") == "typed")
        add("focus_match_rate", "ok" if not delivered or linked == len(delivered) else "warn",
            f"{linked} of {len(delivered)} orders delivered in 30 days have a confirmed Focus invoice link"
            f" ({typed} more carry only a typed invoice number).", str(pct(linked, len(delivered))) if delivered else None)
    if weekly_match:
        n, of = weekly_match.get("matched", 0), weekly_match.get("orders", 0)
        add("weekly_match_estimate", "ok",
            f"The weekly report's automatic match found {n} of {of} live orders on a Focus invoice (an estimate).",
            str(pct(n, of)) if of else None)
    if verify is not None:
        add("verify_numbers", {"pass": "ok", "fail": "fail"}.get(verify.get("result"), "warn"),
            verify.get("detail") or "", verify.get("result"))
    else:
        add("verify_numbers", "warn", "verify_numbers was not run with this pack (use --verify).")

    worst = "ok"
    for c in checks:
        if c["status"] in ("fail", "stale"):
            worst = "stale" if c["status"] == "stale" and worst != "fail" else "fail"
        elif c["status"] == "warn" and worst == "ok":
            worst = "check"
    lead = {"ok": "Data is fresh and consistent.",
            "check": "Data is usable with the caveats listed.",
            "stale": "DATA IS STALE: say so first; the Focus figures describe an older week.",
            "fail": "DATA FAILED A CHECK: say so first and treat the affected figures as unreliable."}[worst]
    return {"today": today, "verdict": worst, "lead": lead,
            "as_of": {s["source"]: {"as_of": _as_date(s.get("as_of")), "loaded_at": s.get("loaded_at"),
                                    "rows": s.get("row_count"), "metric": s.get("metric"), "status": s.get("status")}
                      for s in sources},
            "checks": checks}


def monthly_ready(sources: list[dict], month: str) -> dict:
    """§20: the month-close review runs only on an export dated on or after the 1st of the next month.
    The upload time stands in for the export date; the Focus data must also reach within 2 days of
    the month's last day (a month ending on a Friday or a holiday has no sales that day)."""
    first, last = month_bounds(month)
    by = {s["source"]: s for s in sources}
    upload = _as_date((by.get("focus_upload") or {}).get("loaded_at"))
    as_of = _as_date((by.get("focus_sales") or {}).get("as_of"))
    nxt = last + timedelta(days=1)
    ok = bool(upload and upload >= nxt and as_of and as_of >= last - timedelta(days=2))
    why = ("ready" if ok else
           f"not ready: needs an upload on or after {nxt:%d %b %Y} (last: {upload or 'none'}) and Focus data to "
           f"{last - timedelta(days=2):%d %b} or later (now: {as_of or 'none'})")
    return {"month": month, "first": first, "last": last, "ready": ok, "why": why,
            "last_upload": upload, "focus_as_of": as_of}


# ── anomalies (pure) ──────────────────────────────────────────────────────────

def xmr(values: list, label: str) -> dict:
    """XmR (individuals) chart on a series, the last point judged against the ones before it:
    natural process limits = mean +/- 2.66 x the average moving range of the baseline."""
    vals = [D(v) for v in values]
    if len(vals) < 6:
        return {"series": label, "judged": False, "why": f"only {len(vals)} points (needs 6)"}
    base, last = vals[:-1], vals[-1]
    centre = sum(base, Decimal(0)) / len(base)
    mr = [abs(b - a) for a, b in zip(base, base[1:])]
    mr_bar = sum(mr, Decimal(0)) / len(mr)
    unpl = centre + Decimal("2.66") * mr_bar
    lnpl = max(Decimal(0), centre - Decimal("2.66") * mr_bar)
    signal = "above" if last > unpl else "below" if last < lnpl else None
    return {"series": label, "judged": True, "last": q3(last), "centre": q3(centre), "upper": q3(unpl),
            "lower": q3(lnpl), "signal": signal, "points": len(vals)}


def modified_z(values: list) -> list:
    """Iglewicz-Hoaglin modified z-scores (0.6745 x (x - median) / MAD); with MAD = 0 the mean absolute
    deviation stands in (x 1.253314). All zeros when the series has no spread."""
    xs = [float(v) for v in values]
    if len(xs) < 2:
        return [0.0 for _ in xs]
    med = statistics.median(xs)
    mad = statistics.median([abs(x - med) for x in xs])
    if mad:
        return [0.6745 * (x - med) / mad for x in xs]
    mean_ad = sum(abs(x - med) for x in xs) / len(xs)
    if not mean_ad:
        return [0.0 for _ in xs]
    return [(x - med) / (1.253314 * mean_ad) for x in xs]


def qty_outliers(lines: list[dict], min_lines: int = 5, cut: float = 3.5) -> list[dict]:
    """Marketplace order lines whose quantity is far off that item's usual (typo guard, plan §21)."""
    by = defaultdict(list)
    for ln in lines:
        if ln.get("is_test"):
            continue
        by[str(ln.get("item_code") or "").upper()].append(ln)
    out = []
    for code, lst in by.items():
        if len(lst) < min_lines:
            continue
        zs = modified_z([int(x.get("qty") or 0) for x in lst])
        for ln, z in zip(lst, zs):
            if abs(z) > cut:
                out.append({"item_code": code, "order_no": ln.get("order_no"), "qty": ln.get("qty"),
                            "usual_qty": statistics.median(int(x.get("qty") or 0) for x in lst), "z": round(z, 2)})
    return sorted(out, key=lambda r: -abs(r["z"]))


# ── Focus context (pure) ──────────────────────────────────────────────────────

def _acc_b2b(r: dict) -> bool:
    return r.get("channel") == "B2B" and r.get("division") == "Accessories" and not r.get("is_giveaway")


def focus_context(rows: list[dict], week_start: date, week_end: date) -> dict:
    """Focus sales around the review week (ex-VAT, net_bhd = taxable): the last 13 weeks by channel x
    division (a partial last week is compared on the same weekdays), reps and SKUs this week against
    the 4 weeks before, the category mix, and customer concentration over 30 days."""
    if not rows:
        return {"available": False}
    fmax = max(r["sale_date"] for r in rows)
    cover_end = min(week_end, fmax)
    span = (cover_end - week_start).days          # days covered inside the week, minus one
    weeks = []
    for k in range(WEEKS_HISTORY - 1, -1, -1):
        a = week_start - timedelta(weeks=k)
        b = a + timedelta(days=span) if span >= 0 else a - timedelta(days=1)
        agg = defaultdict(lambda: Decimal(0))
        inv, cust = set(), set()
        for r in rows:
            if a <= r["sale_date"] <= b:
                agg[f"{r.get('channel')}|{r.get('division')}"] += D(r.get("net_bhd"))
                if _acc_b2b(r):
                    inv.add(r["invoice_no"])
                    if not r.get("is_cash_customer"):
                        cust.add(r.get("customer_name"))
        weeks.append({"week_start": a, "through": b, "b2b_accessories_ex_vat": q3(agg["B2B|Accessories"]),
                      "b2c_accessories_ex_vat": q3(agg["B2C|Accessories"]),
                      "sim_ex_vat": q3(agg["B2B|SIM"] + agg["B2C|SIM"]),
                      "b2b_invoices": len(inv), "b2b_named_shops": len(cust)})

    def window(k0: int, k1: int):
        """rows of weeks k0..k1 back (0 = the review week), same weekdays."""
        out = []
        for k in range(k0, k1 + 1):
            a = week_start - timedelta(weeks=k)
            b = a + timedelta(days=span)
            out += [r for r in rows if a <= r["sale_date"] <= b and _acc_b2b(r)]
        return out

    this, before = window(0, 0), window(1, 4)

    def by_key(rs, key, val="net_bhd", div=1):
        acc = defaultdict(lambda: Decimal(0))
        for r in rs:
            acc[key(r)] += D(r.get(val))
        return {k: v / div for k, v in acc.items()}

    rep_now = by_key(this, lambda r: rep_base(r.get("salesman_resolved")) or "unknown")
    rep_before = by_key(before, lambda r: rep_base(r.get("salesman_resolved")) or "unknown", div=4)
    reps = sorted(({"rep": k, "this_week": q3(rep_now.get(k, 0)), "avg_prior_4": q3(rep_before.get(k, 0)),
                    "change_pct": pct(rep_now.get(k, 0) - rep_before.get(k, 0), rep_before.get(k, 0))}
                   for k in set(rep_now) | set(rep_before)), key=lambda r: -r["this_week"])
    sku_now = by_key(this, lambda r: r.get("sku_code") or "(no SKU)", "quantity")
    sku_before = by_key(before, lambda r: r.get("sku_code") or "(no SKU)", "quantity", div=4)
    movers = []
    for k in set(sku_now) | set(sku_before):
        n, b = sku_now.get(k, Decimal(0)), sku_before.get(k, Decimal(0))
        movers.append({"sku": k, "units_this_week": q3(n), "units_avg_prior_4": q3(b), "change_units": q3(n - b)})
    movers.sort(key=lambda r: -abs(r["change_units"]))
    cat_now = by_key(this, lambda r: r.get("category_name") or "Other")
    cat_before = by_key(before, lambda r: r.get("category_name") or "Other", div=4)
    tot_now, tot_before = sum(cat_now.values(), Decimal(0)), sum(cat_before.values(), Decimal(0))
    cats = sorted(({"category": k, "this_week": q3(cat_now.get(k, 0)), "share_pct": pct(cat_now.get(k, 0), tot_now),
                    "avg_prior_4": q3(cat_before.get(k, 0)), "prior_share_pct": pct(cat_before.get(k, 0), tot_before)}
                   for k in set(cat_now) | set(cat_before)), key=lambda r: -r["this_week"])
    return {"available": True, "focus_max": fmax, "cover_end": cover_end, "partial_week": cover_end < week_end,
            "weeks": weeks, "reps": reps, "movers": movers[:40], "categories": cats,
            "concentration_30d": concentration([r for r in rows if _acc_b2b(r) and not r.get("is_cash_customer")
                                                and r["sale_date"] > fmax - timedelta(days=30)])}


def concentration(rows: list[dict]) -> dict:
    """Top-10 customers' share and the Herfindahl-Hirschman index (0-10,000) of ex-VAT sales."""
    acc = defaultdict(lambda: Decimal(0))
    for r in rows:
        acc[r.get("customer_name")] += D(r.get("net_bhd"))
    total = sum(acc.values(), Decimal(0))
    if not total:
        return {"customers": 0, "top10_share_pct": None, "hhi": None}
    shares = sorted((v / total for v in acc.values()), reverse=True)
    hhi = sum((s * 100) ** 2 for s in shares)
    return {"customers": len(acc), "total_ex_vat": q3(total), "top10_share_pct": pct(sum(shares[:10], Decimal(0)), 1),
            "hhi": int(hhi.to_integral_value(ROUND_HALF_UP))}


def merchant_movement(rows: list[dict], week_start: date, week_end: date) -> list[dict]:
    """Named B2B Accessories shops (cash accounts and giveaways excluded), as of the last Focus date:
    new (first purchase in the data falls in the week), reactivated (bought this week after a gap
    over max(60 days, 2 x its cadence)), due (quiet > 1.5 x its cadence with >= 4 purchase days; the
    segment median cadence stands in below 4), at_risk (>= 2 of: overdue > 2 x cadence, 60-day value
    < 60 % of the 60 days before, SKU count down 30 %), dormant (quiet > max(90, 4 x cadence))."""
    rs = [r for r in rows if _acc_b2b(r) and not r.get("is_cash_customer") and r.get("customer_name")]
    if not rs:
        return []
    fmax = max(r["sale_date"] for r in rs)
    cover_end = min(week_end, fmax)
    per = defaultdict(lambda: {"dates": set(), "v60": Decimal(0), "vp60": Decimal(0), "s60": set(), "sp60": set(),
                               "owner": Counter()})
    for r in rs:
        p = per[r["customer_name"]]
        p["dates"].add(r["sale_date"])
        age = (fmax - r["sale_date"]).days
        if age < 60:
            p["v60"] += D(r.get("net_bhd"))
            p["s60"].add(r.get("sku_code"))
        elif age < 120:
            p["vp60"] += D(r.get("net_bhd"))
            p["sp60"].add(r.get("sku_code"))
        if age < 365:
            p["owner"][rep_base(r.get("salesman_resolved"))] += 1
    cad = {}
    for name, p in per.items():
        ds = sorted(p["dates"])
        gaps = [(b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days > 0]
        cad[name] = statistics.median(gaps) if gaps else None
    seg = [c for n, c in cad.items() if c and len(per[n]["dates"]) >= 4]
    seg_median = statistics.median(seg) if seg else 30.0
    out = []
    for name, p in per.items():
        ds = sorted(p["dates"])
        last = ds[-1]
        since = (fmax - last).days
        visits = len(ds)
        own = cad[name] if (cad[name] and visits >= 4) else seg_median
        in_week = [x for x in ds if week_start <= x <= cover_end]
        before_week = [x for x in ds if x < week_start]
        flags, reasons = [], []
        if in_week and not before_week:
            flags.append("new")
        if in_week and before_week and (in_week[0] - before_week[-1]).days > max(60, 2 * own):
            flags.append("reactivated")
        if since > max(90, 4 * own):
            flags.append("dormant")
        elif since > 1.5 * own:
            flags.append("due")
        risk = 0
        if since > 2 * own:
            risk += 1
            reasons.append("overdue > 2x cadence")
        if p["vp60"] > 0 and p["v60"] < p["vp60"] * Decimal("0.6"):
            risk += 1
            reasons.append("60-day value < 60 % of the 60 days before")
        if len(p["sp60"]) >= 3 and len(p["s60"]) <= len(p["sp60"]) * 0.7:
            risk += 1
            reasons.append("SKUs down 30 %+")
        if risk >= 2 and "dormant" not in flags:
            flags.append("at_risk")
        if not flags:
            continue
        out.append({"customer_name": name, "owner_rep": p["owner"].most_common(1)[0][0] if p["owner"] else None,
                    "flags": "+".join(flags), "visits": visits, "cadence_days": round(own, 1),
                    "cadence_source": "own" if (cad[name] and visits >= 4) else "segment median",
                    "last_bought": last, "days_since": since, "value_60d_ex_vat": q3(p["v60"]),
                    "value_prev_60d_ex_vat": q3(p["vp60"]), "skus_60d": len(p["s60"]), "skus_prev_60d": len(p["sp60"]),
                    "reasons": "; ".join(reasons), "ar_rising": "unknown (no AR history in the pack)"})
    order = {"new": 0, "reactivated": 1, "at_risk": 2, "due": 3, "dormant": 4}
    out.sort(key=lambda r: (order.get(r["flags"].split("+")[0], 9), -r["value_prev_60d_ex_vat"] - r["value_60d_ex_vat"]))
    return out


def holdout_readout(rows: list[dict], window_days: int = 14) -> list[dict]:
    """Follow-ups vs holdout (app/followups.py): per rep, of the shops he owns, how many bought in the
    last `window_days` among the shops shown on his list vs the 1-in-5 held back. An early readout:
    small samples, and the list changes daily."""
    try:
        from app.followups import is_holdout
    except Exception:  # noqa: BLE001 — the app stack is optional for the pack
        return []
    rs = [r for r in rows if _acc_b2b(r) and not r.get("is_cash_customer") and r.get("customer_name")]
    if not rs:
        return []
    fmax = max(r["sale_date"] for r in rs)
    days = defaultdict(lambda: defaultdict(set))        # shop -> rep -> distinct sale dates (followups' owner rule)
    last = {}
    for r in rs:
        if (fmax - r["sale_date"]).days < 365:
            days[r["customer_name"]][r.get("salesman_resolved") or ""].add(r["sale_date"])
        last[r["customer_name"]] = max(last.get(r["customer_name"], r["sale_date"]), r["sale_date"])
    per = defaultdict(lambda: {"shown": 0, "shown_bought": 0, "held": 0, "held_bought": 0})
    for shop, by_rep in days.items():
        rep = sorted(by_rep, key=lambda k: (-len(by_rep[k]), -max(by_rep[k]).toordinal(), k))[0]
        bought = (fmax - last[shop]).days < window_days
        g = per[rep_base(rep) or "unknown"]
        if is_holdout(rep, shop):
            g["held"] += 1
            g["held_bought"] += bought
        else:
            g["shown"] += 1
            g["shown_bought"] += bought
    return [{"rep": k, **v, "shown_rate_pct": pct(v["shown_bought"], v["shown"]),
             "holdout_rate_pct": pct(v["held_bought"], v["held"]), "window_days": window_days}
            for k, v in sorted(per.items())]


def item_signals(items: list[dict], lines: list[dict], searches: list[dict], vat: Decimal = VAT) -> dict:
    """Sold out with demand, low cover, margins on the trade price (ex-VAT) vs the latest landed cost,
    and cost moves (the latest receipt vs the one before). An item with no stock row counts as 0 in
    stock (Focus leaves zero-stock lines out of the snapshot) and says so (stock_row: false)."""
    wanted = Counter()
    for ln in lines:
        if not ln.get("is_test") and ln.get("order_status") != "cancelled":
            wanted[str(ln.get("item_code") or "").upper()] += int(ln.get("qty") or 0)
    sold_out, low_cover, margins, drift = [], [], [], []
    with_cost = active = 0
    for it in items:
        if not it.get("is_active") or it.get("hidden"):
            continue
        active += 1
        code = str(it.get("item_code") or "").upper()
        stock = D(it.get("stock_qty"))
        rate = D(it.get("sold_90d")) / 90
        restock = int(it.get("restock_requests_30d") or 0)
        if stock <= 0 and (rate > 0 or restock or wanted.get(code)):
            sold_out.append({"item_code": it["item_code"], "name": it.get("display_name"), "stock": q3(stock),
                             "stock_row": it.get("stock_qty") is not None,     # False: no stock row, counted as 0
                             "units_per_day_90d": q3(rate), "est_units_lost_per_week": q3(rate * 7),
                             "restock_requests_30d": restock, "marketplace_units_asked_in_pack": wanted.get(code, 0),
                             "last_sold": it.get("last_sold"), "estimate": True})
        elif rate > 0 and stock / rate < 14:
            low_cover.append({"item_code": it["item_code"], "name": it.get("display_name"), "stock": q3(stock),
                              "days_cover": q3(stock / rate)})
        price, cost = it.get("trade_price_bhd"), it.get("landed_cost_bhd")
        if cost is not None:
            with_cost += 1
        if price is not None and cost is not None and D(price) > 0:
            ex = D(price) / (1 + vat)
            m = (ex - D(cost)) / ex * 100
            margins.append({"item_code": it["item_code"], "name": it.get("display_name"), "trade_ex_vat": q3(ex),
                            "landed_cost": q3(cost), "margin_pct": m.quantize(Decimal("0.1"), ROUND_HALF_UP),
                            "below_cost": ex < D(cost), "cost_date": it.get("cost_date")})
        prev = it.get("prev_landed_cost_bhd")
        if cost is not None and prev is not None and D(prev) > 0 and D(cost) != D(prev):
            drift.append({"item_code": it["item_code"], "name": it.get("display_name"), "landed_cost": q3(cost),
                          "previous_landed_cost": q3(prev), "change_pct": pct(D(cost) - D(prev), prev),
                          "cost_date": it.get("cost_date"), "previous_cost_date": it.get("prev_cost_date")})
    zero = Counter()
    for s in searches:
        if s.get("zero_result"):
            zero[s.get("term")] += int(s.get("searches") or 0)
    return {"sold_out_with_demand": sorted(sold_out, key=lambda r: -r["est_units_lost_per_week"]),
            "low_cover": sorted(low_cover, key=lambda r: r["days_cover"]),
            "margins": sorted(margins, key=lambda r: r["margin_pct"]),
            "below_cost": [m for m in margins if m["below_cost"]],
            "cost_moves": sorted(drift, key=lambda r: -abs(r["change_pct"] or 0)),
            "cost_coverage": {"active_items": active, "with_landed_cost": with_cost,
                              "pct": pct(with_cost, active)},
            "zero_result_terms": zero.most_common(20)}


# ── files ─────────────────────────────────────────────────────────────────────

def write_csv(path: Path, rows: list[dict]) -> int:
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([_cell(r.get(c)) for c in cols])
    return len(rows)


def _cell(v):
    j = jsonable(v)
    if isinstance(j, (dict, list)):
        return json.dumps(j, ensure_ascii=False)
    return "" if j is None else j


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(jsonable(obj), ensure_ascii=False, indent=1), encoding="utf-8")


def run_verify(out: Path) -> dict:
    """scripts/verify_numbers (read-only: it sums the source files and the views) into verify_numbers.txt."""
    try:
        r = subprocess.run([sys.executable, "-m", "scripts.verify_numbers"], cwd=str(ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600)
    except Exception as e:  # noqa: BLE001
        return {"result": "error", "detail": f"verify_numbers could not run: {type(e).__name__}: {e}"}
    text = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    (out / "verify_numbers.txt").write_text(mask_text(text), encoding="utf-8")
    if "ALL CHECKS PASS" in text:
        return {"result": "pass", "detail": "verify_numbers: ALL CHECKS PASS (verify_numbers.txt)."}
    if "SOME CHECKS FAILED" in text:
        fails = [ln.strip() for ln in text.splitlines() if ln.rstrip().endswith("%)") and " FAIL " in ln]
        return {"result": "fail", "detail": "verify_numbers FAILED: " + "; ".join(fails[:5])}
    return {"result": "error", "detail": f"verify_numbers exited {r.returncode} without a verdict (verify_numbers.txt)."}


FILE_NOTES = {
    "trust.json": "the data trust gate: read first; lead the report with it when the verdict is not ok",
    "weekly_metrics.json": "scripts/weekly_report.compute() for the week: the numbers the management email carries",
    "focus_context.json": "Focus sales: 13 weeks, reps / SKUs / categories vs the 4 weeks before, concentration",
    "anomalies.json": "XmR on the weekly Focus series, modified-z quantity outliers on marketplace lines",
    "items.json": "sold out with demand, low cover, margins, below cost, cost moves, zero-result terms",
    "shop_orders.csv": "marketplace orders of the last 5 weeks + every open order (no contact details)",
    "shop_lines.csv": "their lines, requested vs confirmed",
    "funnel_daily.csv": "per day x referral code: visitors, item viewers, adders, checkouts, orders",
    "search_demand.csv": "what merchants searched, zero-result flag",
    "rep_governance.csv": "per rep: waiting orders, confirm times, last order action, link visitors, taps",
    "statements.csv": "kickback statements (draft / approved / paid / superseded / snapshot)",
    "focus_links.csv": "order <-> Focus invoice decisions",
    "regulars_due.csv": "shop x SKU pairs strictly due (>= 4 purchases, quiet > 1.5 x cadence), with the owning rep",
    "merchants.csv": "merchant movement: new / reactivated / at_risk / due / dormant with reason codes",
    "followups_holdout.csv": "follow-ups vs holdout: early readout per rep",
    "items.csv": "every catalog item: stock, velocity, landed cost, restock asks",
    "market_signals.csv": "restock requests, product finds, field notes of the last 5 weeks (reports, not facts)",
    "focus_lines_week.csv": "Focus sales lines of the review week (drill-down)",
    "insights_history.csv": "what earlier reviews proposed and what the owner decided",
    "verify_numbers.txt": "python -m scripts.verify_numbers output (only with --verify)",
    "monthly.json": "the month-close figures (§20; only with --monthly)",
}


def build(db: ReadOnly, week_start: date, week_end: date, out: Path, *, verify_result: dict | None = None,
          now: datetime | None = None, month: str | None = None) -> dict:
    """Read everything through `db` (one read-only snapshot), write the pack into `out`, return the
    manifest. `verify_result` is scripts/verify_numbers' verdict, run BEFORE the session opens (it
    takes minutes and the ai_head_ro login drops a transaction idle for 60 s)."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(BH).date()
    out.mkdir(parents=True, exist_ok=True)
    have = db.available()
    files: dict[str, int | None] = {}
    notes: list[str] = []
    missing = [v for v in AGENT_VIEWS if v not in have]
    if missing:
        notes.append("Views not readable (apply scripts/r7b_ai_head_migration.sql, or grant them to this login): "
                     + ", ".join(missing))

    def view(name: str, sql: str, *params) -> list[dict] | None:
        if name not in have:
            return None
        return db.q(sql, *params)

    t0 = datetime.combine(week_start, datetime.min.time(), BH)
    t1 = datetime.combine(week_end + timedelta(days=1), datetime.min.time(), BH)
    ctx_from = week_start - timedelta(days=28)

    # data trust
    sources = view("v_agent_data_trust", "select * from v_agent_data_trust")
    if sources is None and db.mode == "owner_fallback":
        sources = db.q("""
            select 'focus_sales' as source, max(sale_date) as as_of, null::timestamptz as loaded_at,
                   count(*) as row_count, null::numeric as metric, null::text as status from v_sales
            union all
            select 'stock_balance', max(as_of_date), max(imported_at), count(*), null, null from stock_balance
            union all
            select 'marketplace_events', (max(ts) at time zone 'Asia/Bahrain')::date, max(ts), count(*), null, null
              from shop_events""")
    orders = view("v_agent_shop_orders", """select * from v_agent_shop_orders
                                             where (created_at >= %s and created_at < %s) or status = any(%s)
                                             order by created_at""", t0 - timedelta(days=28), t1, list(OPEN_STATUSES))
    lines = None
    if orders is not None:
        ids = [o["order_id"] for o in orders]
        lines = view("v_agent_shop_lines", "select * from v_agent_shop_lines where order_id = any(%s) order by line_id",
                     ids) or []

    # weekly metrics through weekly_report.compute()
    d, how = load_weekly(db, have, week_start, week_end)
    metrics = None
    if d is not None:
        metrics = weekly_metrics(d, week_start, week_end, now)
        metrics["loaded_from"] = how
        write_json(out / "weekly_metrics.json", metrics)
        files["weekly_metrics.json"] = 1
    else:
        notes.append("weekly_metrics.json not built: " + how)

    # Focus sales (400 days: the 13-week series, merchant cadence, the monthly 12-month baseline)
    focus_from = week_end - timedelta(days=400)
    fcols = ("line_id, invoice_no, sale_date, customer_name, is_cash_customer, salesman_raw, salesman_resolved, "
             "channel, division, sale_type, is_giveaway, sku_code, item_name, category_name, quantity, rate_bhd, "
             "gross_bhd, discount_bhd, taxable_bhd, vat_amount_bhd, revenue_bhd, net_bhd, narration")
    focus = view("v_agent_focus_sales", f"select {fcols} from v_agent_focus_sales where sale_date >= %s", focus_from)
    if focus is None and db.mode == "owner_fallback":
        focus = db.q(f"select {fcols} from v_sales where sale_date >= %s", focus_from)
        notes.append("Focus sales read from v_sales (fallback); names and narration masked on the way out.")

    weekly_match = None
    if metrics:
        weekly_match = {"matched": len(metrics["impact"]["matches"]), "orders": metrics["n_live"]}
    if verify_result is not None:
        files["verify_numbers.txt"] = 1
    trust = trust_gate(sources or [], today, orders=orders, lines=lines, verify=verify_result,
                       weekly_match=weekly_match, week_end=week_end)
    if month:
        trust["monthly"] = monthly_ready(sources or [], month)
    write_json(out / "trust.json", trust)
    files["trust.json"] = len(trust["checks"])

    if orders is not None:
        recent = [o for o in orders if o["created_at"] >= t0 - timedelta(days=28) or o["status"] in OPEN_STATUSES]
        files["shop_orders.csv"] = write_csv(out / "shop_orders.csv", recent)
        files["shop_lines.csv"] = write_csv(out / "shop_lines.csv", lines or [])

    got: dict[str, list[dict]] = {}
    for name, fname, sql, params in (
        ("v_agent_funnel_daily", "funnel_daily.csv",
         "select * from v_agent_funnel_daily where day between %s and %s order by day, referral_code nulls first",
         (ctx_from, week_end)),
        ("v_agent_search_demand", "search_demand.csv",
         "select * from v_agent_search_demand where day between %s and %s order by day, searches desc",
         (ctx_from, week_end)),
        ("v_agent_rep_governance", "rep_governance.csv",
         "select * from v_agent_rep_governance order by is_active desc, rep", ()),
        ("v_agent_statements", "statements.csv",
         "select * from v_agent_statements order by period desc, rep, statement_id", ()),
        ("v_agent_focus_links", "focus_links.csv",
         "select * from v_agent_focus_links order by created_at desc", ()),
        ("v_agent_customer_regulars", "regulars_due.csv",
         "select * from v_agent_customer_regulars where due_strict order by owner_rep nulls last, times_bought desc", ()),
        ("v_agent_market_signals", "market_signals.csv",
         "select * from v_agent_market_signals where day >= %s order by day desc, signal", (ctx_from,)),
        ("v_agent_insights", "insights_history.csv",
         "select * from v_agent_insights where week_ending >= %s order by week_ending desc, rank nulls last",
         (week_end - timedelta(days=70),)),
    ):
        rows = view(name, sql, *params)
        if rows is None:
            files[fname] = None
            continue
        files[fname] = write_csv(out / fname, rows)
        got[fname] = rows

    items = view("v_agent_items", "select * from v_agent_items order by category, item_code")
    if items is not None:
        files["items.csv"] = write_csv(out / "items.csv", items)
        week_searches = [x for x in got.get("search_demand.csv", []) if week_start <= x["day"] <= week_end]
        sig = item_signals(items, lines or [], week_searches)
        write_json(out / "items.json", sig)
        files["items.json"] = len(sig["sold_out_with_demand"])

    anomalies: dict = {"xmr": [], "qty_outliers": []}
    if month:
        notes.append("Monthly pack: weekly_metrics.json and merchants.csv cover the whole month (its 'prev' figures "
                     "are the 7 days before the 1st); focus_context.json and the XmR series use the month's last "
                     "complete Sunday-Saturday week.")
    if focus:
        last_sat = week_end - timedelta(days=(week_end.weekday() - 5) % 7)
        ctx_ws, ctx_we = week_bounds(last_sat) if month else (week_start, week_end)
        ctx = focus_context(focus, ctx_ws, ctx_we)
        write_json(out / "focus_context.json", ctx)
        files["focus_context.json"] = len(ctx.get("weeks") or [])
        anomalies["xmr"] = [xmr([w["b2b_accessories_ex_vat"] for w in ctx["weeks"]], "B2B accessories ex-VAT per week"),
                            xmr([w["b2b_invoices"] for w in ctx["weeks"]], "B2B accessory invoices per week"),
                            xmr([w["b2b_named_shops"] for w in ctx["weeks"]], "named B2B shops buying per week")]
        mv = merchant_movement(focus, week_start, week_end)
        files["merchants.csv"] = write_csv(out / "merchants.csv", mv)
        ho = holdout_readout(focus)
        files["followups_holdout.csv"] = write_csv(out / "followups_holdout.csv", ho)
        wk = [r for r in focus if week_start <= r["sale_date"] <= week_end]
        files["focus_lines_week.csv"] = write_csv(out / "focus_lines_week.csv", wk)
        if month:
            mon = monthly_context(focus, month, items or [], view)
            write_json(out / "monthly.json", mon)
            files["monthly.json"] = 1
    else:
        notes.append("No Focus sales available to the pack: focus_context / merchants / anomalies skipped.")
    if lines:
        since = t1 - timedelta(days=90)
        anomalies["qty_outliers"] = qty_outliers([ln for ln in lines if ln["order_created_at"] >= since])
    write_json(out / "anomalies.json", anomalies)
    files["anomalies.json"] = len(anomalies["xmr"]) + len(anomalies["qty_outliers"])

    manifest = {
        "kind": "monthly" if month else "weekly",
        "week_start": week_start, "week_end": week_end, "month": month,
        "generated_at": now, "generated_on_bahrain": today,
        "connection": {"mode": db.mode, "role": db.role, "warnings": db.warnings},
        "views_readable": sorted(have), "views_missing": missing,
        "files": {k: {"rows": v, "about": FILE_NOTES.get(k, "")} for k, v in files.items()},
        "notes": notes,
        "trust_verdict": trust["verdict"], "trust_lead": trust["lead"],
        "rules": ["Read only: nothing here was written to any database.",
                  "Phones and emails are masked in every file; do not try to recover them.",
                  "Market signals and field notes are reports, not facts, until 2 shops or reps corroborate them.",
                  "Money is BHD, 3 decimals; Focus figures are ex-VAT (net_bhd), marketplace order values incl. VAT."],
    }
    write_json(out / "manifest.json", manifest)
    return manifest


def monthly_context(rows: list[dict], month: str, items: list[dict], view) -> dict:
    """§20: the month against the previous month, the trailing 3 and 12 months (monthly averages), by
    rep, SKU, merchant and category; concentration; repeated unavailable demand and field requests;
    a lost-sales ESTIMATE; margin drift; the month's statements."""
    first, last = month_bounds(month)

    def month_of(dd: date) -> str:
        return f"{dd.year:04d}-{dd.month:02d}"

    def shift(ym: str, k: int) -> str:
        y, m = (int(p) for p in ym.split("-"))
        t = y * 12 + (m - 1) - k
        return f"{t // 12:04d}-{t % 12 + 1:02d}"

    acc = [r for r in rows if _acc_b2b(r)]
    by_month = defaultdict(lambda: Decimal(0))
    for r in acc:
        by_month[month_of(r["sale_date"])] += D(r.get("net_bhd"))
    prev = shift(month, 1)
    t3 = [shift(month, k) for k in (1, 2, 3)]
    t12 = [shift(month, k) for k in range(1, 13)]
    have12 = [m for m in t12 if m in by_month]

    def avg(ms):
        ms = [m for m in ms if m in by_month]
        return q3(sum((by_month[m] for m in ms), Decimal(0)) / len(ms)) if ms else None

    def grouped(key):
        cur, p, tr = defaultdict(lambda: Decimal(0)), defaultdict(lambda: Decimal(0)), defaultdict(lambda: Decimal(0))
        for r in acc:
            mo = month_of(r["sale_date"])
            k = key(r)
            v = D(r.get("net_bhd"))
            if mo == month:
                cur[k] += v
            if mo == prev:
                p[k] += v
            if mo in t3:
                tr[k] += v / 3
        out = [{"key": k, "month": q3(cur.get(k, 0)), "previous": q3(p.get(k, 0)), "trailing_3_avg": q3(tr.get(k, 0)),
                "vs_trailing_pct": pct(cur.get(k, 0) - tr.get(k, 0), tr.get(k, 0))}
               for k in set(cur) | set(tr)]
        return sorted(out, key=lambda r: -(r["month"] - r["trailing_3_avg"]))

    skus = grouped(lambda r: r.get("sku_code") or "(no SKU)")
    shops = grouped(lambda r: r.get("customer_name") if not r.get("is_cash_customer") else "(cash)")
    reps = grouped(lambda r: rep_base(r.get("salesman_resolved")) or "unknown")
    cats = grouped(lambda r: r.get("category_name") or "Other")
    lost = []
    for it in items:
        if it.get("is_active") and not it.get("hidden") and D(it.get("stock_qty")) <= 0 and D(it.get("sold_90d")) > 0:
            rate = D(it["sold_90d"]) / 90
            ls = _as_date(it.get("last_sold"))
            days_out = max(0, (last - ls).days) if ls and ls <= last else 0
            lost.append({"item_code": it["item_code"], "stock_row": it.get("stock_qty") is not None,
                         "units_per_day_90d": q3(rate), "days_since_last_sale": days_out,
                         "est_units_lost": q3(rate * min(days_out, (last - first).days + 1)),
                         "est_value_lost_trade_ex_vat": q3(rate * min(days_out, (last - first).days + 1)
                                                            * D(it.get("trade_price_bhd")) / (1 + VAT)),
                         "estimate": True})
    # weeks start on Sunday here: day - dow = that week's Sunday
    zero = view("v_agent_search_demand",
                "select term, count(distinct day - extract(dow from day)::int) as weeks, sum(searches) as searches "
                "from v_agent_search_demand where zero_result and day between %s and %s "
                "group by term having count(distinct day - extract(dow from day)::int) >= 2 order by searches desc",
                first, last) or []
    asks = view("v_agent_market_signals",
                "select signal, coalesce(item_code, label) as what, sum(n) as reports, sum(sources) as sources "
                "from v_agent_market_signals where day between %s and %s "
                "group by 1, 2 having sum(n) >= 2 order by reports desc", first, last) or []
    stm = view("v_agent_statements", "select * from v_agent_statements where period = %s order by rep", month) or []
    return {"month": month, "first": first, "last": last,
            "b2b_accessories_ex_vat": {"month": q3(by_month.get(month, 0)), "previous": q3(by_month.get(prev, 0)),
                                       "trailing_3_avg": avg(t3), "trailing_12_avg": avg(t12),
                                       "months_in_12m_baseline": len(have12)},
            "skus_accelerating": skus[:15], "skus_declining": sorted(skus, key=lambda r: r["month"] - r["trailing_3_avg"])[:15],
            "merchants_accelerating": shops[:15],
            "merchants_declining": sorted(shops, key=lambda r: r["month"] - r["trailing_3_avg"])[:15],
            "reps": reps, "categories": cats,
            "concentration": concentration([r for r in acc if month_of(r["sale_date"]) == month
                                            and not r.get("is_cash_customer")]),
            "repeated_zero_result_terms": zero, "repeated_field_requests": asks,
            "lost_sales_estimate": sorted(lost, key=lambda r: -r["est_value_lost_trade_ex_vat"]),
            "margin_drift": [i for i in item_signals(items, [], [])["cost_moves"]
                             if _as_date(i.get("cost_date")) and first <= _as_date(i["cost_date"]) <= last],
            "statements": stm,
            "promotion_incrementality": "not measurable yet: needs offers with a holdout and the offer ledger (plan §16)"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week-ending", help="the Saturday that ends the week (YYYY-MM-DD); default: the last completed week")
    ap.add_argument("--monthly", metavar="YYYY-MM", help="build the month-close pack for this month instead")
    ap.add_argument("--verify", action="store_true", help="also run python -m scripts.verify_numbers into the pack")
    ap.add_argument("--out", help="output folder (default exports/ai_head/<week-ending> or month-YYYY-MM)")
    a = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    if a.monthly and not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", a.monthly):
        print(f"pack: --monthly takes YYYY-MM, not {a.monthly!r}", file=sys.stderr)
        return 2
    if a.monthly:
        first, last = month_bounds(a.monthly)
        we = last
        ws = first
        out = Path(a.out) if a.out else ROOT / "exports" / "ai_head" / f"month-{a.monthly}"
    else:
        try:
            we = date.fromisoformat(a.week_ending) if a.week_ending else wr.last_week_ending(now.astimezone(BH).date())
        except ValueError:
            print(f"pack: --week-ending takes YYYY-MM-DD, not {a.week_ending!r}", file=sys.stderr)
            return 2
        if we.weekday() != 5:
            print(f"pack: {we} is not a Saturday (weeks run Sunday-Saturday)", file=sys.stderr)
            return 2
        ws, we = week_bounds(we)
        out = Path(a.out) if a.out else ROOT / "exports" / "ai_head" / we.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    verify_result = run_verify(out) if a.verify else None     # before the session: it can take minutes
    db = connect()
    try:
        for w in db.warnings:
            print("WARNING:", w, file=sys.stderr)
        man = build(db, ws, we, out, verify_result=verify_result, now=now, month=a.monthly)
    finally:
        db.conn.rollback()
        db.conn.close()
    print(f"pack written to {out}")
    print(f"connection: {man['connection']['mode']} as {man['connection']['role']}")
    print(f"trust: {man['trust_verdict']} - {man['trust_lead']}")
    for k, v in man["files"].items():
        print(f"  {k:26} {'-' if v['rows'] is None else v['rows']}")
    for n in man["notes"]:
        print("note:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
