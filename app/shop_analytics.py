"""Shop analytics done properly — release R7d (27-Sep-2026; plan §13, audit ANA-4/5, OFF-8, INT-13).

shop.analytics() is the one entry point (GET /shop/analytics, the rep's "My link this week" card);
this module holds what it is built from, plus the merchant profile (GET /shop/analytics/merchant/{id}).

Where the numbers come from
  * The storefront events are counted in SQL views (scripts/r7d_analytics_views_migration.sql) and read
    through the read-only RPC as yq_readonly, with the window and the rep as bound parameters. Before,
    every shop_events row of the window came through PostgREST, which answers 1,000 rows at most, so the
    page saw about a third of a week. Until the views exist (the API may deploy first) the raw events are
    read the old way — paged now — and counted here by Python twins of the same rules
    (funnel_from_events, search_rows_from_events, rail_rows_from_events). The page says which it used.
  * Orders and lines are read through PostgREST, paged, rep-scoped by the database (eq salesman_id).

The rules (one each, shared by the SQL and the Python twin)
  * Money: an order is worth total_confirmed_bhd ?? total_bhd (shop_heart.effective_money); a line its
    confirmed total, else its ordered total pro rata to the confirmed quantity (the price lock). Decimal,
    3 dp, ROUND_HALF_UP. Test orders never count; cancelled orders are counted apart.
  * The funnel: view -> item -> add -> cart -> checkout (checkout_start; the storefront never sends
    "checkout") -> order. A session (or a device) is counted once per Bahrain day, and all of one
    device's events of a day are credited to that day's first rep link.
  * Searches: only the final typed search counts. One followed within SEARCH_TYPING_GAP_S on the same
    device by a query that starts with it (longer, or the same again) is typing; chip / facet /
    quick-order pings (meta.rail) are not typed searches.
  * Merchant cadence and the dormant rule: see v_merchant_360 / merchant_facts. Insights are rules with
    their evidence, labelled as insights — never presented as fact.
"""
from __future__ import annotations

import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median

log = logging.getLogger(__name__)

SEARCH_TYPING_GAP_S = 30
VIEWS_MISS_TTL_S = 300            # a missing view is remembered 5 minutes (re-probed after)
PAGE = 1000                       # PostgREST's max-rows
EVENTS_MAX_PAGES = 100            # the fallback's ceiling: 100,000 events (said on the page if reached)
ORDERS_MAX_PAGES = 50
LINES_CHUNK = 100                 # order ids per lines read (each read is paged too)
STARTED_STATUSES = ("confirmed", "packed", "out_for_delivery", "delivered")
OPEN_STATUSES = ("new", "confirmed", "packed", "out_for_delivery")
LINE_OUT = ("removed", "unavailable", "substituted")
MONEY_BASIS = "confirmed_else_requested"
ZERO = Decimal("0.000")
_BAHRAIN = timezone(timedelta(hours=3))

# cadence / dormant rules (plan §21) — the same numbers as scripts/r7d_analytics_views_migration.sql
OWN_CADENCE_MIN_DAYS = 4          # order days before a merchant's own median gap is its rhythm
ALL_SHOPS_MIN_GAPS = 10           # gaps across all merchants before the all-shops median stands in
DUE_X, OVERDUE_X = 1.5, 2.0
VALUE_DROP_X, RANGE_DROP_X = 0.6, 0.7
TREND_MIN_HISTORY_DAYS = 60       # history before the last 30 days needed to compare with its own baseline

_views: dict = {"at": 0.0, "ok": None}
_first_event: dict = {"day": None}


def _shop():
    from app import shop
    return shop


# ── small helpers ─────────────────────────────────────────────────────────────

def d3(x) -> Decimal:
    return _shop().dmoney(x if x is not None else 0)


def f3(x: Decimal) -> float:
    """A Decimal at the JSON edge: the float of its 3-dp value (the payload's money has always been numbers)."""
    return float(d3(x))


def bahrain_day(ts) -> date | None:
    d = _shop()._parse_ts(ts)
    return d.astimezone(_BAHRAIN).date() if d else None


def window(days, today: date | None = None) -> tuple[int, date, date]:
    """(days, first day, last day) — Bahrain calendar days, today included. days is clamped to 1..365."""
    n = max(1, min(_shop()._i(days, 30), 365))
    end = today or _shop().bahrain_today()
    return n, end - timedelta(days=n - 1), end


def bahrain_midnight(day: date) -> str:
    """00:00 in Bahrain on `day`, as a UTC ISO timestamp (the orders / events lower bound)."""
    return (datetime(day.year, day.month, day.day, tzinfo=_BAHRAIN)).astimezone(timezone.utc).isoformat()


def pct(part, whole, dp: int = 1) -> float | None:
    return round(float(part) / float(whole) * 100, dp) if whole else None


def p75(values: list) -> float | None:
    """Nearest rank — the same value as SQL's percentile_disc(0.75)."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    return round(float(vals[max(0, math.ceil(len(vals) * 0.75) - 1)]), 3)


# ── the money rule ────────────────────────────────────────────────────────────

def order_value(o: dict) -> Decimal:
    """confirmed ?? requested, to the fils (shop_heart.effective_money, as a Decimal)."""
    v = o.get("total_confirmed_bhd")
    return d3(v if v is not None else o.get("total_bhd"))


def started(o: dict) -> bool:
    """The order has confirmed figures (shop_heart.line_view's rule)."""
    return o.get("status") in STARTED_STATUSES or bool(o.get("confirmed_at"))


def line_effective(ln: dict, o: dict) -> tuple[int, Decimal]:
    """(units, value) of one line on the effective rule. A Received order: as ordered. A confirmed one:
    the confirmed quantity (a line out = 0) and the confirmed total, else the ordered total pro rata
    (the price lock: the ordered unit price, never today's book)."""
    s = _shop()
    qty = s._i(ln.get("qty"))
    if not started(o):
        return qty, d3(ln.get("line_total_bhd"))
    qc = ln.get("qty_confirmed")
    if qc is None:
        units = 0 if (ln.get("line_status") or "ok") in LINE_OUT else qty
    else:
        units = max(0, s._i(qc))
    ltc = ln.get("line_total_confirmed")
    if ltc is not None:
        return units, d3(ltc)
    total = d3(ln.get("line_total_bhd"))
    if units == qty:
        return units, total
    return units, (d3(total * units / qty) if qty > 0 else ZERO)


# ── the Python twins of the view rules (the fallback, and what the tests pin) ──

def _meta(e: dict) -> dict:
    m = e.get("meta")
    return m if isinstance(m, dict) else {}


def _who(e: dict) -> str:
    return str(e.get("device_id") or e.get("session_id") or f"event:{e.get('id')}")


def _ref(e: dict) -> str | None:
    r = str(e.get("referral_code") or "").strip().lower()
    return r or None


def _ts_key(e: dict):
    d = _shop()._parse_ts(e.get("ts"))
    return (d or datetime.min.replace(tzinfo=timezone.utc), _shop()._i(e.get("id")))


def final_searches(events: list[dict]) -> list[dict]:
    """The typed searches that count — v_shop_search_daily's rule. Typed = a search / search_zero ping
    with a query and no meta.rail (chips, facets and the quick order send one). Per device, in time
    order: a search followed within SEARCH_TYPING_GAP_S by one whose query starts with it is typing."""
    typed: dict[str, list[tuple]] = {}
    for e in events:
        if e.get("event") not in ("search", "search_zero"):
            continue
        m = _meta(e)
        term = str(m.get("q") or "").strip(" ").lower()
        if not term or "rail" in m:
            continue
        key = _ts_key(e)
        if key[0].year == 1:
            continue
        typed.setdefault(_who(e), []).append((key, term, e))
    out: list[dict] = []
    for rows in typed.values():
        rows.sort(key=lambda r: r[0])
        for i, (key, term, e) in enumerate(rows):
            if i + 1 < len(rows):
                nkey, nterm, _ = rows[i + 1]
                if (nkey[0] - key[0]).total_seconds() <= SEARCH_TYPING_GAP_S and nterm.startswith(term):
                    continue
            out.append({**e, "_term": term})
    return out


def search_rows_from_events(events: list[dict]) -> list[dict]:
    """Rows shaped like SEARCH_SQL's answer (per term: searches, zero, sessions, devices, last_seen)."""
    terms: dict[str, dict] = {}
    for e in final_searches(events):
        t = terms.setdefault(e["_term"], {"term": e["_term"], "searches": 0, "zero": 0, "_s": set(), "_d": set(),
                                          "last_seen": None})
        t["searches"] += 1
        t["zero"] += 1 if e.get("event") == "search_zero" else 0
        day = bahrain_day(e.get("ts"))
        if e.get("session_id"):
            t["_s"].add((day, e["session_id"]))
        if e.get("device_id"):
            t["_d"].add((day, e["device_id"]))
        ts = str(e.get("ts") or "")
        t["last_seen"] = max(t["last_seen"] or "", ts) or None
    return [{"term": t["term"], "searches": t["searches"], "zero": t["zero"], "sessions": len(t["_s"]),
             "devices": len(t["_d"]), "last_seen": t["last_seen"]} for t in terms.values()]


def rail_rows_from_events(events: list[dict]) -> list[dict]:
    """v_shop_rail_perf's rule: rail taps (rail_click, reco_click, reorder) and adds made from a rail."""
    rails: dict[str, dict] = {}
    for e in events:
        ev = e.get("event")
        m = _meta(e)
        if ev not in ("rail_click", "reco_click", "add", "reorder") or not ("rail" in m or ev == "reorder"):
            continue
        rail = str(m.get("rail") or "reorder")[:40]
        r = rails.setdefault(rail, {"rail": rail, "clicks": 0, "adds": 0, "_s": set()})
        if ev == "add":
            r["adds"] += 1
        else:
            r["clicks"] += 1
        if e.get("session_id"):
            r["_s"].add((bahrain_day(e.get("ts")), e["session_id"]))
    return [{"rail": r["rail"], "clicks": r["clicks"], "adds": r["adds"], "sessions": len(r["_s"])}
            for r in rails.values()]


FUNNEL_KEYS = ("devices", "sessions", "view_sessions", "item_sessions", "add_sessions", "cart_sessions",
               "checkout_sessions", "order_sessions", "view_devices", "item_devices", "add_devices", "cart_devices",
               "checkout_devices", "order_devices", "search_sessions", "share_sessions", "install_sessions",
               "reorder_sessions", "cancel_sessions", "new_devices")
_STEP_EVENTS = {"view": ("view",), "item": ("item",), "add": ("add",), "cart": ("cart",),
                "checkout": ("checkout_start", "checkout"), "order": ("order",), "search": ("search", "search_zero"),
                "share": ("share",), "install": ("install",), "reorder": ("reorder",), "cancel": ("cancel",)}


def funnel_from_events(events: list[dict], first_seen: dict[str, datetime] | None = None,
                       scope: tuple[int | None, str] | None = None) -> dict:
    """v_shop_funnel_daily's rule over raw events. Every event of one device (else session) on one
    Bahrain day is credited to that day's first rep link; then, per (day, link, salesman, src) row,
    sessions and devices are counted distinct — exactly the view's GROUP BY. Returns {"total": {key: n},
    "by_day": {iso: {...}}, "by_ref": {ref|None: {...}}}. `first_seen` maps a device to its first event
    ever (new_devices); `scope` = (salesman_id, referral_code) keeps one rep's rows (the view's WHERE)."""
    s = _shop()
    parts: dict[tuple, list[dict]] = {}
    for e in events:
        if e.get("event") == "error":
            continue
        day = bahrain_day(e.get("ts"))
        if day is None:
            continue
        parts.setdefault((day, _who(e)), []).append(e)
    rows: dict[tuple, dict[str, set]] = {}
    for (day, _key), evs in parts.items():
        evs.sort(key=lambda e: (_ref(e) is None, _ts_key(e)))
        head = evs[0]
        acc = rows.setdefault((day, _ref(head), head.get("salesman_id"), head.get("src")), {})
        for e in evs:
            sess, dev, kind = e.get("session_id"), e.get("device_id"), e.get("event")
            if sess:
                acc.setdefault("sessions", set()).add(sess)
            if dev:
                acc.setdefault("devices", set()).add(dev)
                if first_seen is not None and first_seen.get(dev) == _ts_key(e)[0]:
                    acc.setdefault("new_devices", set()).add(dev)
            for step, kinds in _STEP_EVENTS.items():
                if kind not in kinds:
                    continue
                who = (sess or dev) if step == "order" else sess
                if who:
                    acc.setdefault(f"{step}_sessions", set()).add(who)
                if dev:
                    acc.setdefault(f"{step}_devices", set()).add(dev)
    total = {k: 0 for k in FUNNEL_KEYS}
    by_day: dict[str, dict] = {}
    by_ref: dict = {}
    for (day, g_ref, g_sid, _src), acc in rows.items():
        if scope is not None:
            sid, code = scope
            if not ((sid is not None and g_sid is not None and s._i(g_sid, -1) == s._i(sid))
                    or (code and g_ref == code)):
                continue
        dacc = by_day.setdefault(day.isoformat(), {k: 0 for k in FUNNEL_KEYS})
        racc = by_ref.setdefault(g_ref, {k: 0 for k in FUNNEL_KEYS})
        for k in FUNNEL_KEYS:
            n = len(acc.get(k, ()))
            total[k] += n
            dacc[k] += n
            racc[k] += n
    return {"total": total, "by_day": by_day, "by_ref": by_ref}


def first_seen_map(events: list[dict]) -> dict[str, datetime]:
    """device -> its first event's time, within the events given (the fallback reads the window only,
    so a device first seen before the window counts as new in it — the view knows better)."""
    out: dict[str, datetime] = {}
    for e in events:
        dev = e.get("device_id")
        if not dev or e.get("event") == "error":
            continue
        t = _ts_key(e)[0]
        if dev not in out or t < out[dev]:
            out[dev] = t
    return out


# ── the SQL (bound parameters only; read as yq_readonly through the RPC) ───────

# $1 first day, $2 last day (Bahrain dates), $3 salesman id or '', $4 referral code (lower case) or ''
_SCOPE = "($3 = '' OR salesman_id = NULLIF($3, '')::bigint OR referral_code = NULLIF($4, ''))"
_WINDOW = "day >= $1::date AND day <= $2::date"

FUNNEL_SQL = (
    "SELECT CASE WHEN GROUPING(day) = 0 THEN 'day' WHEN GROUPING(referral_code) = 0 THEN 'ref' ELSE 'all' END AS g, "
    "day::text AS day, referral_code AS ref, "
    + ", ".join(f"COALESCE(SUM({k}), 0)::bigint AS {k}" for k in FUNNEL_KEYS)
    + f" FROM v_shop_funnel_daily WHERE {_WINDOW} AND {_SCOPE} "
    "GROUP BY GROUPING SETS ((day), (referral_code), ())")

SEARCH_SQL = (
    "WITH t AS (SELECT term, SUM(searches)::bigint AS searches, SUM(zero_results)::bigint AS zero, "
    "SUM(sessions)::bigint AS sessions, SUM(devices)::bigint AS devices, MAX(last_seen) AS last_seen "
    f"FROM v_shop_search_daily WHERE {_WINDOW} AND {_SCOPE} GROUP BY term), "
    "tot AS (SELECT COALESCE(SUM(searches), 0)::bigint AS n, COALESCE(SUM(zero), 0)::bigint AS z, "
    "COUNT(*)::bigint AS terms FROM t) "
    "SELECT t.term, t.searches, t.zero, t.sessions, t.devices, t.last_seen, "
    "tot.n AS total_searches, tot.z AS total_zero, tot.terms AS total_terms "
    "FROM tot LEFT JOIN t ON t.term IN ("
    "(SELECT term FROM t ORDER BY searches DESC, zero DESC, term LIMIT 20) UNION "
    "(SELECT term FROM t WHERE zero > 0 ORDER BY zero DESC, searches DESC, term LIMIT 20))")

RAILS_SQL = (
    "SELECT rail, SUM(clicks + reco_clicks + reorders)::bigint AS clicks, SUM(adds)::bigint AS adds, "
    "SUM(sessions)::bigint AS sessions "
    f"FROM v_shop_rail_perf WHERE {_WINDOW} AND {_SCOPE} "
    "GROUP BY rail ORDER BY 2 DESC, 3 DESC, rail LIMIT 40")

_VITAL_COLS = (("lcp_ms_p75", "lcp_ms"), ("inp_ms_p75", "inp_ms"), ("cls_p75", "cls"),
               ("lcp_ttfb_ms_p75", "lcp_ttfb_ms"), ("lcp_delay_ms_p75", "lcp_delay_ms"),
               ("lcp_load_ms_p75", "lcp_load_ms"), ("lcp_render_ms_p75", "lcp_render_ms"),
               ("catalog_ms_p75", "catalog_ms"))
VITALS_SQL = (
    "SELECT COUNT(*)::bigint AS visits, (COUNT(*) FILTER (WHERE has_metric))::bigint AS samples, "
    + ", ".join(f"percentile_disc(0.75) WITHIN GROUP (ORDER BY {col}) AS {key}" for key, col in _VITAL_COLS)
    + ", (SELECT COALESCE(json_agg(json_build_object('src', s.catalog_src, 'visits', s.n) "
    "ORDER BY s.n DESC, s.catalog_src), '[]'::json) FROM (SELECT catalog_src, COUNT(*) AS n FROM v_shop_vitals "
    f"WHERE {_WINDOW} AND {_SCOPE} GROUP BY catalog_src) s) AS catalog_src "
    f"FROM v_shop_vitals WHERE {_WINDOW} AND {_SCOPE}")

MERCHANT_COLS = ("customer_id, shop, area, rep_id, rep_name, orders, open_orders, value_bhd, value_90d_bhd, "
                 "value_30d_bhd, aov_bhd, first_order_at, last_order_at, days_since_last, order_days, cadence_days, "
                 "cadence_basis, due_status, dormant, dormant_signals, is_new")
MERCHANTS_SQL = (f"SELECT {MERCHANT_COLS} FROM v_merchant_360 WHERE orders > 0 "
                 "ORDER BY value_90d_bhd DESC, value_bhd DESC, customer_id LIMIT 500")
MERCHANT_COUNTS_SQL = (
    "SELECT (COUNT(*) FILTER (WHERE orders > 0))::bigint AS merchants, "
    "(COUNT(*) FILTER (WHERE is_new))::bigint AS new, "
    "(COUNT(*) FILTER (WHERE order_days >= 2))::bigint AS returning, "
    "(COUNT(*) FILTER (WHERE due_status = 'due'))::bigint AS due, "
    "(COUNT(*) FILTER (WHERE due_status = 'overdue'))::bigint AS overdue, "
    "(COUNT(*) FILTER (WHERE dormant))::bigint AS dormant, "
    "(COUNT(*) FILTER (WHERE cadence_basis IS NOT NULL))::bigint AS with_cadence "
    "FROM v_merchant_360")
MERCHANT_SQL = "SELECT * FROM v_merchant_360 WHERE customer_id = $1::bigint"


# ── the read path ─────────────────────────────────────────────────────────────

def view_sql(sql: str, params: list) -> list[dict]:
    """db_read.exec_sql_params through app.shop's client: the same read-only RPC (a SECURITY DEFINER
    function owned by yq_readonly), so nothing here can write. Bound to shop's client so a test's
    stand-in answers it too — no test reaches a database through analytics()."""
    from app.db_read import retry_read
    args = {"sql_text": sql, "params": [str(p) for p in params]}
    r = retry_read(lambda: _shop().get_client().rpc("run_readonly_query_params", args).execute(),
                   what="shop analytics views")
    data = r.data
    if isinstance(data, str):
        data = json.loads(data)
    return data if isinstance(data, list) else []


def missing_relation(exc: BaseException) -> bool:
    s = str(exc).lower()
    return ("does not exist" in s or "42p01" in s or "42703" in s or "undefined_table" in s
            or "undefined_column" in s)


def views_known_missing() -> bool:
    return _views["ok"] is False and time.time() - _views["at"] < VIEWS_MISS_TTL_S


def remember_views(ok: bool) -> None:
    _views.update(at=time.time(), ok=ok)


def forget_views() -> None:
    _views.update(at=0.0, ok=None)


def _scope_params(start: date, end: date, salesman: dict | None) -> list[str]:
    sid = (salesman or {}).get("id")
    code = str((salesman or {}).get("referral_code") or "").strip().lower()
    return [start.isoformat(), end.isoformat(), "" if salesman is None else str(_shop()._i(sid, -1)), code]


def _num(r: dict | None, k: str) -> int:
    return _shop()._i((r or {}).get(k))


def digest_from_views(start: date, end: date, salesman: dict | None, notes: list[str]) -> dict | None:
    """The event half of the analytics from the views, or None when they are not there (before the
    migration, or the first read failed) — the caller then counts the raw events."""
    if views_known_missing():
        return None
    p = _scope_params(start, end, salesman)
    try:
        rows = view_sql(FUNNEL_SQL, p)
    except Exception as e:  # noqa: BLE001 — any failure: the raw events answer this time
        if missing_relation(e):
            remember_views(False)
        else:
            log.warning("shop analytics: funnel view unavailable (%s) — counting raw events", str(e)[:160])
        return None
    total = next((r for r in rows if r.get("g") == "all"), None)
    if total is None:          # a grand-total row always comes back from real SQL; anything else is not the view
        return None
    remember_views(True)
    funnel = {"total": {k: _num(total, k) for k in FUNNEL_KEYS},
              "by_day": {str(r.get("day")): {k: _num(r, k) for k in FUNNEL_KEYS} for r in rows if r.get("g") == "day"},
              "by_ref": {(r.get("ref") or None): {k: _num(r, k) for k in FUNNEL_KEYS} for r in rows if r.get("g") == "ref"}}
    jobs = {"search": (SEARCH_SQL, p), "rails": (RAILS_SQL, p), "vitals": (VITALS_SQL, p)}
    if salesman is None:
        jobs.update(merchants=(MERCHANTS_SQL, []), merchant_counts=(MERCHANT_COUNTS_SQL, []))
    got: dict[str, list | None] = {}

    def run(item):
        name, (sql, params) = item
        try:
            return name, view_sql(sql, params)
        except Exception as e:  # noqa: BLE001 — one section never costs the page
            log.warning("shop analytics: %s unavailable: %s", name, str(e)[:160])
            return name, None
    with ThreadPoolExecutor(max_workers=min(4, len(jobs)), thread_name_prefix="ana") as ex:
        for name, val in ex.map(run, jobs.items()):
            got[name] = val
    out: dict = {"funnel": funnel}
    srows = got.get("search")
    if srows is None:
        notes.append("Search terms could not be read just now.")
        out["search"] = None
    else:
        head = srows[0] if srows else {}
        out["search"] = {"rows": [r for r in srows if r.get("term")], "total": _num(head, "total_searches"),
                         "zero": _num(head, "total_zero")}
    rrows = got.get("rails")
    if rrows is None:
        notes.append("Rail taps could not be read just now.")
    out["rails"] = rrows or []
    v = got.get("vitals")
    if v is None:
        notes.append("Site speed could not be read just now.")
        out["vitals"] = None
    else:
        v = v[0] if v else {}
        src = v.get("catalog_src")
        if isinstance(src, str):
            src = json.loads(src)
        out["vitals"] = {"samples": _num(v, "samples"), "visits": _num(v, "visits"),
                         **{key: (round(float(v[key]), 3) if v.get(key) is not None else None) for key, _ in _VITAL_COLS},
                         "catalog_src": [{"src": str(x.get("src")), "visits": _shop()._i(x.get("visits"))}
                                         for x in (src or [])]}
    if salesman is None:
        m, c = got.get("merchants"), got.get("merchant_counts")
        if m is None or c is None:
            notes.append("The merchant list could not be read just now.")
        out["merchants"] = m
        out["merchant_counts"] = (c or [None])[0] if c is not None else None
    return out


def events_since(client) -> str | None:
    """The first storefront event ever (a Bahrain date): the history the page can speak for. Remembered
    once known — it never moves."""
    if _first_event["day"]:
        return _first_event["day"]
    try:
        r = (client.table("shop_events").select("ts").order("ts").limit(1).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.debug("first event read failed: %s", e)
        return None
    day = bahrain_day((r[0] if r else {}).get("ts"))
    if day:
        _first_event["day"] = day.isoformat()
    return _first_event["day"]


def paged(fetch, max_pages: int) -> tuple[list[dict], bool]:
    """fetch(offset) -> one page; stops on a short page. Returns (rows, capped)."""
    out: list[dict] = []
    for i in range(max_pages):
        rows = fetch(i * PAGE) or []
        out += rows
        if len(rows) < PAGE:
            return out, False
    return out, True


# ── the order half (both paths) ───────────────────────────────────────────────

def order_sections(orders: list[dict], lines: list[dict], *, sla_min: int, now: datetime) -> dict:
    """Everything the payload says about orders, on the effective money rule. `orders` are this window's
    orders (test orders already left out), cancelled ones included; `lines` those of the live ones."""
    s = _shop()
    live = [o for o in orders if o.get("status") != "cancelled"]
    cancelled = [o for o in orders if o.get("status") == "cancelled"]
    by_id = {o.get("id"): o for o in live}
    val = {o.get("id"): order_value(o) for o in live}
    n = len(live)
    value = sum(val.values(), ZERO)

    units_by_order: dict = {}
    prod: dict[str, dict] = {}
    for ln in lines:
        o = by_id.get(ln.get("order_id"))
        if o is None:
            continue
        u, v = line_effective(ln, o)
        units_by_order[o.get("id")] = units_by_order.get(o.get("id"), 0) + u
        if u <= 0:
            continue
        code = ln.get("item_code")
        p = prod.setdefault(code, {"item_code": code, "display_name": ln.get("display_name"), "units": 0,
                                   "value": ZERO, "orders": 0})
        p["units"] += u
        p["value"] += v
        p["orders"] += 1
    top_products = [{"item_code": p["item_code"], "display_name": p["display_name"], "units": p["units"],
                     "value_bhd": f3(p["value"]), "orders": p["orders"]}
                    for p in sorted(prod.values(), key=lambda p: (-p["value"], -p["units"], str(p["item_code"])))[:15]]
    units = sum(units_by_order.get(o.get("id"), s._i(o.get("units_count"))) for o in live)

    reps: dict[str, dict] = {}
    for o in live:
        name = o.get("salesman_name") or "Unassigned"
        r = reps.setdefault(name, {"salesman": name, "orders": 0, "value": ZERO, "customers": set()})
        r["orders"] += 1
        r["value"] += val[o.get("id")]
        r["customers"].add(o.get("customer_phone"))
    leaderboard = [{"salesman": r["salesman"], "orders": r["orders"], "value_bhd": f3(r["value"]),
                    "customers": len(r["customers"]),
                    "aov_bhd": f3(r["value"] / r["orders"]) if r["orders"] else 0.0}
                   for r in sorted(reps.values(), key=lambda r: -r["value"])]

    def attribution(keyfn, only=None) -> list[dict]:
        acc: dict[str, dict] = {}
        for o in live:
            if only is not None and not only(o):
                continue
            k = keyfn(o)
            a = acc.setdefault(k, {"key": k, "orders": 0, "value": ZERO})
            a["orders"] += 1
            a["value"] += val[o.get("id")]
        return [{"key": a["key"], "orders": a["orders"], "value_bhd": f3(a["value"])}
                for a in sorted(acc.values(), key=lambda a: -a["value"])]

    by_ref = attribution(lambda o: (f"salesman:{o.get('salesman_name') or 'staff'}" if o.get("source") == "salesman"
                                    else o.get("referral_code") or ("dropdown" if o.get("source") == "dropdown" else "direct")))
    by_src = attribution(lambda o: o.get("src") or "direct")
    by_coupon = attribution(lambda o: o.get("coupon_code"), only=lambda o: bool(o.get("coupon_code")))
    by_attr = attribution(lambda o: o.get("attribution_source") or ("staff" if o.get("source") == "salesman" else "legacy"))

    daily: dict[str, dict] = {}
    for o in live:
        day = bahrain_day(o.get("created_at"))
        k = day.isoformat() if day else str(o.get("created_at") or "")[:10]
        x = daily.setdefault(k, {"date": k, "orders": 0, "value": ZERO})
        x["orders"] += 1
        x["value"] += val[o.get("id")]

    breaches = 0
    for o in live:
        created = s._parse_ts(o.get("created_at"))
        if not created:
            continue
        assigned = s._parse_ts(o.get("assigned_at"))
        if o.get("salesman_id") and not assigned:
            continue  # attributed at creation: nobody waited
        if ((assigned or now) - created).total_seconds() / 60 > sla_min:
            breaches += 1
    # Received -> Confirmed only: an order a rep placed himself (source 'salesman') is born Confirmed
    # (R7c) and never waited, so it would pull the median to zero
    confirm_mins = sorted((s._parse_ts(o["confirmed_at"]) - s._parse_ts(o["created_at"])).total_seconds() / 60
                          for o in live if s._parse_ts(o.get("confirmed_at")) and s._parse_ts(o.get("created_at"))
                          and o.get("source") != "salesman")
    ops = {"by_attribution": by_attr,
           "unassigned_now": sum(1 for o in live if not o.get("salesman_id") and o.get("status") in ("new", "confirmed")),
           "conflicts": sum(1 for o in live if o.get("attribution_conflict")),
           "sla_min": sla_min, "sla_breaches": breaches,
           "median_time_to_confirm_min": round(confirm_mins[len(confirm_mins) // 2], 1) if confirm_mins else None,
           "cancelled_by_customer": sum(1 for o in cancelled if o.get("cancelled_by") == "customer"),
           "cancelled_by_staff": sum(1 for o in cancelled if o.get("cancelled_by") != "customer")}

    per_customer: dict = {}
    for o in live:
        k = o.get("customer_id") or o.get("customer_phone")
        per_customer[k] = per_customer.get(k, 0) + 1
    repeat = sum(1 for c in per_customer.values() if c >= 2)
    identity = {"customers": len(per_customer), "repeat_customers": repeat,
                "repeat_rate_pct": pct(repeat, len(per_customer)),
                "market_orders": sum(1 for o in live if o.get("source") in ("market", "slug")),
                "staff_orders": sum(1 for o in live if o.get("source") == "salesman"),
                "legacy_orders": sum(1 for o in live if o.get("source") not in ("market", "slug", "salesman"))}
    return {
        "orders": n, "cancelled": len(cancelled), "value_bhd": f3(value),
        "aov_bhd": f3(value / n) if n else 0.0, "units": units,
        "customers": len({o.get("customer_phone") for o in live}),
        "backorder_rate_pct": round(sum(1 for o in live if o.get("has_backorder")) / n * 100, 1) if n else 0.0,
        "top_products": top_products, "leaderboard": leaderboard,
        "attribution": {"by_referral": by_ref, "by_src": by_src, "by_coupon": by_coupon},
        "_daily": daily, "ops": ops, "identity": identity, "_live": live, "_val": val,
    }


def daily_rows(daily: dict[str, dict], by_day: dict[str, dict]) -> list[dict]:
    """Orders and value per Bahrain day, with the day's sessions (visits) beside them."""
    keys = sorted(set(daily) | set(by_day))
    return [{"date": k, "orders": (daily.get(k) or {}).get("orders", 0),
             "value_bhd": f3((daily.get(k) or {}).get("value", ZERO)),
             "sessions": (by_day.get(k) or {}).get("view_sessions", 0)} for k in keys]


def link_rows(by_ref: dict, live: list[dict], val: dict, names: dict[str, str]) -> list[dict]:
    """Visits and orders per rep link (the storefront's own orders — a rep placing one is not a link
    conversion), with the rep's name."""
    acc: dict = {}
    for ref, f in by_ref.items():
        k = ref or "direct"
        a = acc.setdefault(k, {"key": k, "sessions": 0, "devices": 0, "orders": 0, "value": ZERO})
        a["sessions"] += f.get("view_sessions", 0)
        a["devices"] += f.get("devices", 0)
    for o in live:
        if o.get("source") == "salesman":
            continue
        k = str(o.get("referral_code") or "").strip().lower() or "direct"
        a = acc.setdefault(k, {"key": k, "sessions": 0, "devices": 0, "orders": 0, "value": ZERO})
        a["orders"] += 1
        a["value"] += val.get(o.get("id"), ZERO)
    rows = [{"key": a["key"], "rep": names.get(a["key"]), "sessions": a["sessions"], "devices": a["devices"],
             "orders": a["orders"], "value_bhd": f3(a["value"]),
             "conversion_pct": pct(a["orders"], a["sessions"]) if a["sessions"] else None}
            for a in acc.values()]
    return sorted(rows, key=lambda r: (-r["value_bhd"], -r["sessions"], r["key"]))


def search_payload(rows: list[dict], total: int | None = None, zero: int | None = None) -> dict:
    n = sum(r["searches"] for r in rows) if total is None else total
    z = sum(r["zero"] for r in rows) if zero is None else zero
    terms = sorted(rows, key=lambda t: (-_shop()._i(t.get("searches")), -_shop()._i(t.get("zero")), str(t.get("term"))))
    zt = sorted((t for t in rows if _shop()._i(t.get("zero"))),
                key=lambda t: (-_shop()._i(t.get("zero")), -_shop()._i(t.get("searches")), str(t.get("term"))))
    keep = ("term", "searches", "zero", "sessions", "devices", "last_seen")
    return {"searches": n, "zero_results": z, "zero_rate_pct": pct(z, n),
            "terms": [{k: t.get(k) for k in keep} for t in terms[:20]],
            "zero_terms": [{k: t.get(k) for k in keep} for t in zt[:20]],
            "rule": "final typed searches: typing within 30 s on one device and chip taps are not counted"}


def rails_payload(rows: list[dict]) -> list[dict]:
    s = _shop()
    out = [{"rail": str(r.get("rail")), "clicks": s._i(r.get("clicks")), "adds": s._i(r.get("adds")),
            "sessions": s._i(r.get("sessions"))} for r in rows]
    return sorted(out, key=lambda r: (-r["clicks"], -r["adds"], r["rail"]))


def merchants_payload(live: list[dict], val: dict, m360: list[dict] | None, counts: dict | None) -> dict:
    """Merchants active in the window (from its orders), with the 360 flags when the view is there, and
    the ones worth a call (due, overdue or dormant — from the view only)."""
    s = _shop()
    flags = {s._i(r.get("customer_id")): r for r in (m360 or [])}
    act: dict = {}
    for o in sorted(live, key=lambda o: str(o.get("created_at") or "")):
        cid = o.get("customer_id")
        if not cid:
            continue
        a = act.setdefault(cid, {"customer_id": cid, "orders": 0, "value": ZERO})
        a["orders"] += 1
        a["value"] += val.get(o.get("id"), ZERO)
        a.update(shop=o.get("customer_shop"), area=o.get("customer_area"), rep=o.get("salesman_name"),
                 last_order_at=o.get("created_at"))
    active = []
    for a in sorted(act.values(), key=lambda a: -a["value"])[:25]:
        f = flags.get(s._i(a["customer_id"])) or {}
        active.append({"customer_id": a["customer_id"], "shop": a.get("shop"), "area": a.get("area"),
                       "rep": a.get("rep"), "orders": a["orders"], "value_bhd": f3(a["value"]),
                       "last_order_at": a.get("last_order_at"), "due_status": f.get("due_status"),
                       "dormant": f.get("dormant"), "is_new": f.get("is_new")})
    watch = [{k: r.get(k) for k in ("customer_id", "shop", "area", "rep_name", "orders", "value_bhd", "value_90d_bhd",
                                    "last_order_at", "days_since_last", "cadence_days", "cadence_basis", "due_status",
                                    "dormant", "dormant_signals")}
             for r in (m360 or []) if r.get("due_status") in ("due", "overdue") or r.get("dormant")][:15]
    return {"available": m360 is not None, "counts": counts, "active": active, "watch": watch}


# ── the merchant profile ──────────────────────────────────────────────────────

def merchant_facts(customer: dict, orders: list[dict], lines: list[dict], *, categories: dict[str, str],
                   rep_names: dict, today: date, all_gaps: list[int] | None = None,
                   cancelled_orders: int | None = None) -> dict:
    """The Python twin of one v_merchant_360 row (the profile's fallback before the migration, and what
    the tests pin). `orders` = the merchant's orders (any status; test orders are dropped here);
    `lines` = their lines; `all_gaps` = every merchant's gaps (None: the all-shops cadence is unknown)."""
    s = _shop()
    real = [o for o in orders if not o.get("is_test")]
    live = [o for o in real if o.get("status") != "cancelled"]
    ncancel = cancelled_orders if cancelled_orders is not None else sum(1 for o in real if o.get("status") == "cancelled")
    by_id = {o.get("id"): o for o in live}
    val = {o.get("id"): order_value(o) for o in live}
    days = {o.get("id"): bahrain_day(o.get("created_at")) for o in live}

    def within(day, lo, hi=0) -> bool:
        return day is not None and today - timedelta(days=lo) <= day <= today - timedelta(days=hi)

    def vsum(lo, hi=0) -> Decimal:
        return sum((val[i] for i, d in days.items() if within(d, lo, hi)), ZERO)

    order_days = sorted({d for d in days.values() if d})
    gaps = [(b - a).days for a, b in zip(order_days, order_days[1:])]
    own_median = round(float(median(gaps)), 1) if gaps else None
    if len(order_days) >= OWN_CADENCE_MIN_DAYS:
        cadence, basis = own_median, "own"
    elif all_gaps is not None and len(all_gaps) >= ALL_SHOPS_MIN_GAPS:
        cadence, basis = round(float(median(all_gaps)), 1), "all_shops"
    else:
        cadence, basis = None, None
    first = min((s._parse_ts(o.get("created_at")) for o in live if s._parse_ts(o.get("created_at"))), default=None)
    last = max((s._parse_ts(o.get("created_at")) for o in live if s._parse_ts(o.get("created_at"))), default=None)
    last_day = last.astimezone(_BAHRAIN).date() if last else None
    days_since = (today - last_day).days if last_day else None

    cats: dict[str, dict] = {}
    skus: dict[str, dict] = {}
    units = 0
    sku_60, sku_prev = set(), set()
    for ln in lines:
        o = by_id.get(ln.get("order_id"))
        if o is None:
            continue
        u, v = line_effective(ln, o)
        if u <= 0:
            continue
        code = str(ln.get("item_code") or "").upper()
        units += u
        d = days.get(o.get("id"))
        if within(d, 59):
            sku_60.add(code)
        if within(d, 119, 60):
            sku_prev.add(code)
        cat = categories.get(code) or "OTHER"
        c = cats.setdefault(cat, {"category": cat, "value": ZERO, "units": 0})
        c["value"] += v
        c["units"] += u
        k = skus.setdefault(code, {"item_code": code, "display_name": ln.get("display_name"), "units": 0,
                                   "value": ZERO, "orders": set()})
        k["units"] += u
        k["value"] += v
        k["orders"].add(o.get("id"))
        if ln.get("display_name") and str(ln.get("display_name")) > str(k["display_name"] or ""):
            k["display_name"] = ln.get("display_name")
    top_categories = [{"category": c["category"], "value_bhd": f3(c["value"]), "units": c["units"]}
                      for c in sorted(cats.values(), key=lambda c: (-c["value"], c["category"]))[:3]]
    top_skus = [{"item_code": k["item_code"], "display_name": k["display_name"], "units": k["units"],
                 "value_bhd": f3(k["value"]), "orders": len(k["orders"])}
                for k in sorted(skus.values(), key=lambda k: (-k["value"], k["item_code"]))[:5]]

    value = sum(val.values(), ZERO)
    v60, vp60 = vsum(59), vsum(119, 60)
    sig_overdue = bool(cadence is not None and days_since is not None and days_since > OVERDUE_X * cadence)
    sig_value = bool(vp60 > 0 and v60 < Decimal(str(VALUE_DROP_X)) * vp60)
    sig_range = bool(len(sku_prev) > 0 and len(sku_60) < RANGE_DROP_X * len(sku_prev))
    last_rep = next((o.get("salesman_id") for o in sorted(live, key=lambda o: (str(o.get("created_at") or ""),
                                                                              s._i(o.get("id"))), reverse=True)
                     if o.get("salesman_id")), None)
    rep_id = customer.get("salesman_id") or customer.get("sticky_salesman_id") or last_rep
    rep_source = ("assigned" if customer.get("salesman_id") else "sticky" if customer.get("sticky_salesman_id")
                  else "last_order" if last_rep else None)
    n = len(live)
    signals = int(sig_overdue) + int(sig_value) + int(sig_range)
    if n == 0 or cadence is None:
        due = "unknown"
    elif days_since is not None and days_since > OVERDUE_X * cadence:
        due = "overdue"
    elif days_since is not None and days_since > DUE_X * cadence:
        due = "due"
    else:
        due = "ok"
    return {
        "customer_id": customer.get("id"), "shop": (customer.get("shop") or "").strip() or None,
        "area": (customer.get("area") or "").strip() or None, "rep_id": rep_id,
        "rep_name": rep_names.get(rep_id) if rep_id is not None else None, "rep_source": rep_source,
        "focus_customer_id": customer.get("focus_customer_id"), "focus_customer_name": customer.get("focus_customer_name"),
        "orders": n, "open_orders": sum(1 for o in live if o.get("status") in OPEN_STATUSES),
        "cancelled_orders": ncancel, "value_bhd": f3(value),
        "confirmed_value_bhd": f3(sum((val[o.get("id")] for o in live if o.get("status") in STARTED_STATUSES), ZERO)),
        "delivered_value_bhd": f3(sum((val[o.get("id")] for o in live if o.get("status") == "delivered"), ZERO)),
        "aov_bhd": f3(value / n) if n else None,
        "first_order_at": first.isoformat() if first else None, "last_order_at": last.isoformat() if last else None,
        "days_since_last": days_since, "order_days": len(order_days),
        "orders_30d": sum(1 for d in days.values() if within(d, 29)), "value_30d_bhd": f3(vsum(29)),
        "orders_90d": sum(1 for d in days.values() if within(d, 89)), "value_90d_bhd": f3(vsum(89)),
        "value_prev_90d_bhd": f3(vsum(179, 90)), "value_60d_bhd": f3(v60), "value_prev_60d_bhd": f3(vp60),
        "units": units, "skus_n": len(skus), "skus_60d": len(sku_60), "skus_prev_60d": len(sku_prev),
        "median_gap_days": own_median, "cadence_days": cadence, "cadence_basis": basis, "due_status": due,
        "sig_overdue": sig_overdue, "sig_value_drop": sig_value, "sig_range_drop": sig_range,
        "dormant_signals": signals, "dormant": signals >= 2,
        "is_new": bool(first and first.astimezone(_BAHRAIN).date() >= today - timedelta(days=29)),
        "top_categories": top_categories, "top_skus": top_skus,
    }


def _fmt_day(ts) -> str:
    d = bahrain_day(ts)
    return f"{d.day} {d.strftime('%b')}" if d else "—"


def _bhd(x) -> str:
    return f"BHD {d3(x):,.3f}"


def trend(m: dict, today: date) -> dict:
    """Its last 30 days against its own usual 30 days (the value before, spread over the time before) —
    only once there are TREND_MIN_HISTORY_DAYS of history before the last 30 days."""
    first = bahrain_day(m.get("first_order_at"))
    v30 = d3(m.get("value_30d_bhd"))
    out = {"value_30d_bhd": f3(v30), "baseline_30d_bhd": None, "change_pct": None, "enough_history": False,
           "history_days": (today - first).days + 1 if first else 0}
    if not first:
        out["text"] = "No orders yet."
        return out
    before_days = (today - timedelta(days=30) - first).days + 1
    if before_days < TREND_MIN_HISTORY_DAYS:
        out["text"] = f"Too early to compare with its own usual — first order {_fmt_day(m.get('first_order_at'))}."
        return out
    base = d3((d3(m.get("value_bhd")) - v30) / Decimal(before_days) * 30)
    out.update(baseline_30d_bhd=f3(base), enough_history=True,
               change_pct=(round(float((v30 - base) / base * 100), 1) if base > 0 else None))
    out["text"] = (f"Last 30 days {_bhd(v30)} against its usual {_bhd(base)} per 30 days"
                   + (f" ({out['change_pct']:+.0f} %)." if out["change_pct"] is not None else "."))
    return out


def insights(m: dict) -> list[dict]:
    """Rules only (plan §21), each with its evidence and the rule that fired — shown as INSIGHTS, never as
    fact. Levels: act (worth a call now), watch, info."""
    s = _shop()
    out: list[dict] = []
    n, days_since, cad = s._i(m.get("orders")), m.get("days_since_last"), m.get("cadence_days")
    basis, last = m.get("cadence_basis"), m.get("last_order_at")
    if n == 0:
        return out

    def add(kind, level, title, evidence, rule):
        out.append({"kind": kind, "level": level, "title": title, "evidence": evidence, "rule": rule})
    if cad is not None and days_since is not None:
        cad_f = float(cad)
        if basis == "own":
            ev = f"{s._i(m.get('order_days'))} order days; its median gap is {cad_f:g} days; last order {_fmt_day(last)}."
            if days_since > OVERDUE_X * cad_f:
                add("cadence", "act", f"Usually orders every ~{cad_f:g} days — now {days_since} days.", ev,
                    "Overdue: more than 2 x its usual gap since the last order.")
            elif days_since > DUE_X * cad_f:
                add("cadence", "watch", f"Usually orders every ~{cad_f:g} days — now {days_since} days.", ev,
                    "Due: more than 1.5 x its usual gap since the last order.")
            else:
                add("cadence", "info", f"On its usual rhythm: every ~{cad_f:g} days, last order {days_since} days ago.",
                    ev, "Its own median gap between order days (4 or more order days).")
        else:
            level = "watch" if days_since > DUE_X * cad_f else "info"
            add("cadence", level, f"Shops reorder about every ~{cad_f:g} days — this one: {days_since} days since its last order.",
                f"All shops' median gap is {cad_f:g} days; this shop has {s._i(m.get('order_days'))} order day(s).",
                "Not enough of its own history (fewer than 4 order days): the all-shops median stands in.")
    elif s._i(m.get("order_days")) == 1 and days_since is not None and days_since >= 30:
        add("one_order", "watch", f"Ordered once, {days_since} days ago — no second order yet.",
            f"First and only order day: {_fmt_day(last)}.", "One order day and 30 days or more since.")
    elif s._i(m.get("order_days")) < OWN_CADENCE_MIN_DAYS:
        add("rhythm", "info", "Not enough orders yet to know its rhythm.",
            f"{s._i(m.get('order_days'))} order day(s); a rhythm needs {OWN_CADENCE_MIN_DAYS}.",
            "A cadence is read from 4 or more distinct order days.")
    if m.get("sig_value_drop"):
        v60, p60 = d3(m.get("value_60d_bhd")), d3(m.get("value_prev_60d_bhd"))
        change = round(float((v60 - p60) / p60 * 100)) if p60 > 0 else None
        add("value_drop", "watch", "Ordering less than before.",
            f"{_bhd(v60)} in the last 60 days against {_bhd(p60)} in the 60 days before"
            + (f" ({change:+d} %)." if change is not None else "."),
            "The last 60 days below 60 % of the 60 days before.")
    if m.get("sig_range_drop"):
        add("range_drop", "watch", "Buying a narrower range.",
            f"{s._i(m.get('skus_60d'))} different items in the last 60 days against {s._i(m.get('skus_prev_60d'))} before.",
            "The last 60 days' item count below 70 % of the 60 days before.")
    if m.get("dormant"):
        fired = [lbl for key, lbl in (("sig_overdue", "overdue"), ("sig_value_drop", "ordering less"),
                                      ("sig_range_drop", "narrower range")) if m.get(key)]
        add("dormant", "act", "Looks dormant — worth a visit or a call.",
            f"{s._i(m.get('dormant_signals'))} of 3 warning signs: {', '.join(fired)}.",
            "Dormant = 2 or more of: overdue by 2 x its gap, value down 40 %, range down 30 %.")
    if m.get("is_new"):
        add("new", "info", f"New merchant — first order {_fmt_day(m.get('first_order_at'))}.",
            f"{n} order(s) so far, {_bhd(m.get('value_bhd'))}.", "First order within the last 30 days.")
    cats = m.get("top_categories") or []
    total = d3(m.get("value_bhd"))
    if cats and total >= Decimal("20"):
        top = cats[0]
        share = float(d3(top.get("value_bhd")) / total * 100) if total > 0 else 0.0
        if share >= 60:
            add("mix", "info", f"Mostly buys {str(top.get('category')).title()}: {share:.0f} % of its value.",
                f"{_bhd(top.get('value_bhd'))} of {_bhd(total)}.", "One category at 60 % or more of its value (BHD 20 or more).")
    return out


def monthly(orders: list[dict], months: int = 6, today: date | None = None) -> list[dict]:
    """Orders and value per Bahrain month, the last `months` months (empty months included)."""
    today = today or _shop().bahrain_today()
    keys = []
    y, mth = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{mth:02d}")
        mth -= 1
        if mth == 0:
            y, mth = y - 1, 12
    acc = {k: {"month": k, "orders": 0, "value": ZERO} for k in keys}
    for o in orders:
        if o.get("is_test") or o.get("status") == "cancelled":
            continue
        d = bahrain_day(o.get("created_at"))
        k = f"{d.year:04d}-{d.month:02d}" if d else None
        if k in acc:
            acc[k]["orders"] += 1
            acc[k]["value"] += order_value(o)
    return [{"month": a["month"], "orders": a["orders"], "value_bhd": f3(a["value"])} for a in
            (acc[k] for k in reversed(keys))]


PROFILE_ORDER_COLS = ("id,order_no,status,created_at,confirmed_at,delivered_at,total_bhd,total_confirmed_bhd,"
                      "items_count,units_count,salesman_id,salesman_name,source,customer_id")


def _merchant_orders(client, customer_id: int) -> list[dict]:
    s = _shop()

    def read(cols: str) -> list[dict]:
        rows, _ = paged(lambda a: (client.table("shop_orders").select(cols).eq("customer_id", customer_id)
                                   .order("id").range(a, a + PAGE - 1).execute().data or []), ORDERS_MAX_PAGES)
        return rows
    with_test = s.has_column("shop_orders", "is_test")
    try:
        rows = read(PROFILE_ORDER_COLS + (",is_test" if with_test else ""))
    except Exception as e:  # noqa: BLE001 — only the missing-column case is retried
        if with_test and s._missing_column(e):
            s._forget_column("shop_orders", "is_test")
            rows = read(PROFILE_ORDER_COLS)
        else:
            raise
    return [o for o in rows if not s._is_test(o)]


def merchant_profile(customer_id: int, today: date | None = None) -> dict | None:
    """GET /shop/analytics/merchant/{id}: one merchant's facts (v_merchant_360, or its Python twin before
    the migration), its orders, a monthly series, the trend against its own usual and the insights.
    No phone, email or person's name. None = no such merchant."""
    s = _shop()
    client = s.get_client()
    today = today or s.bahrain_today()
    notes: list[str] = []
    cid = s._i(customer_id, -1)
    row = None
    source = "views"
    if not views_known_missing():
        try:
            got = view_sql(MERCHANT_SQL, [cid])
            remember_views(True)
            row = got[0] if got else None
            if row is None:
                # the view answers for every merchant: none = no such merchant
                return None
        except Exception as e:  # noqa: BLE001
            if missing_relation(e):
                remember_views(False)
            else:
                log.warning("merchant profile: view unavailable (%s) — computing from the orders", str(e)[:160])
    orders = _merchant_orders(client, cid)
    if row is None:
        source = "orders"
        cust = (client.table("shop_customers").select("id,shop,area,salesman_id,sticky_salesman_id,focus_customer_id")
                .eq("id", cid).limit(1).execute().data or [])
        if not cust:
            return None
        customer = dict(cust[0])
        if customer.get("focus_customer_id"):
            try:
                fc = (client.table("customers").select("name").eq("id", customer["focus_customer_id"])
                      .limit(1).execute().data or [])
                customer["focus_customer_name"] = (fc[0] if fc else {}).get("name")
            except Exception as e:  # noqa: BLE001
                log.debug("focus customer read failed: %s", e)
        live_ids = [o["id"] for o in orders if o.get("status") != "cancelled"]
        lines: list[dict] = []
        for i in range(0, len(live_ids), LINES_CHUNK):
            chunk = live_ids[i:i + LINES_CHUNK]
            got, _ = paged(lambda a, chunk=chunk: (client.table("shop_order_lines").select("*").in_("order_id", chunk)
                                                   .order("id").range(a, a + PAGE - 1).execute().data or []), 50)
            lines += got
        try:
            ctx = s.context()
            categories = {str(c).upper(): (it.get("category") or None) for c, it in (ctx.get("items") or {}).items()}
        except Exception as e:  # noqa: BLE001
            log.debug("catalog unavailable for categories: %s", e)
            categories = {}
        names = {r.get("id"): r.get("name") for r in (client.table("salesmen").select("id,name").execute().data or [])}
        row = merchant_facts(customer, orders, lines, categories=categories, rep_names=names, today=today)
        notes.append("Computed from this merchant's orders: the all-shops cadence arrives with the analytics views "
                     "(scripts/r7d_analytics_views_migration.sql).")
    for k in ("top_categories", "top_skus"):
        if isinstance(row.get(k), str):
            row[k] = json.loads(row[k])
    recent = sorted(orders, key=lambda o: (str(o.get("created_at") or ""), s._i(o.get("id"))), reverse=True)[:20]
    return {
        "merchant": row, "source": source, "notes": notes, "money_basis": MONEY_BASIS, "as_of": today.isoformat(),
        "trend": trend(row, today), "insights": insights(row), "monthly": monthly(orders, 6, today),
        "orders": [{"id": o.get("id"), "order_no": o.get("order_no"), "status": o.get("status"),
                    "status_label": s.shop_heart.status_label(o.get("status")), "created_at": o.get("created_at"),
                    "value_bhd": f3(order_value(o)), "requested_bhd": f3(d3(o.get("total_bhd"))),
                    "units": s._i(o.get("units_count")), "items": s._i(o.get("items_count")),
                    "rep": o.get("salesman_name")} for o in recent],
    }
