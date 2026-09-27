"""Rep follow-ups (release R3a, 24-Sep-2026; plan §7 lever 3 / §9 Step 3.4): which of a rep's
shops are due a visit, which have lapsed, what they usually buy — and the two link cards, "Baskets
from your link not sent" (lever 2) and "My link this week".

Everything is a RULE over Focus history (no model, no paid call), computed on request and cached
for a few minutes per rep:

  * A shop belongs to the rep who sold to it most often in the last 365 days (ties: the most
    recent seller). That is the privacy line: a rep sees ONLY shops whose Focus sales are his
    (salesman_resolved match, the same `name` / `name - %` rule as app.shop.rep_month_sales); an
    admin may pass ?rep= to see any rep's list, or no rep to see every shop with its owner.
  * Cadence is the shop's OWN median gap between distinct purchase dates. `overdue_ratio` =
    days since the last purchase ÷ that gap; a shop with one visit has no gap yet, so the rep's
    median cadence (else 30 days) stands in and the row says so (gap_source).
  * Value = the shop's ex-VAT Accessories sales over 180 days ÷ 6 (BHD per month, Decimal).
  * Ranking: overdue_ratio × monthly value. "Due" = at least 80 % of the usual gap has passed
    (the same rule v_customer_regulars uses per SKU). Lapsed = quiet for more than
    max(90 days, 4 × gap) → the separate win-back list, ranked by how much history the shop has.
  * Top 5 due SKUs per shop with the median quantity come from v_customer_regulars (service role
    only — it carries names), limited to that rep's shops.
  * The copy never claims a date ("usually every ~23 days · last bought 31 days ago"), because
    the data cannot know when the shop will reorder.
  * 20 % holdout, recomputed and never stored: sha1(rep|shop) % 5 == 0 marks a shop as holdout
    and the rep never sees it, so Focus invoices within 14 days of a shown vs a held-back shop
    measure whether the list changes behaviour. An admin sees holdout shops flagged. Taps are
    logged by the route (audit_log 'followup.tap') for the same measurement.

Cash sales, giveaways and SIM never count. Nothing here writes to the database.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.db_read import exec_sql_params

log = logging.getLogger(__name__)

DUE_RATIO = 0.8            # v_customer_regulars.due: 80 % of the usual gap has passed
LAPSED_MIN_DAYS = 90
LAPSED_GAP_MULT = 4
DEFAULT_GAP_DAYS = 30.0
HOLDOUT_MOD = 5            # 1 in 5 shops (20 %) held back per rep
TOP_SKUS = 5
DUE_CARD = 5               # "Due this week" shows this many
BASKET_DAYS = 7
_TTL = 300.0

_Q3 = Decimal("0.001")


def d3(x) -> Decimal:
    if x is None or x == "":
        return Decimal("0.000")
    try:
        return Decimal(str(x)).quantize(_Q3, rounding=ROUND_HALF_UP)
    except Exception:  # noqa: BLE001
        return Decimal("0.000")


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _i(x, default: int = 0) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


# ── the one SQL: a rep's shops with cadence and value (v_sales, yq_readonly) ──

# $1 = the rep's Focus name; '' = every rep, which only load_shops(all_reps=True) ever sends (the
# admin route). Names in v_sales carry a warehouse suffix ("Ahmed Aradi - Acc WH"): the match is
# the whole name or the part before ' - ' (rep_base), the same rule as rep_month_sales' `name - %`
# but with no LIKE, so a '%' or '_' in an admin-typed Focus name cannot widen the match.
SHOPS_SQL = """
WITH mx AS (SELECT MAX(sale_date) AS d FROM v_sales),
base AS (
  SELECT DISTINCT v.customer_name, v.salesman_resolved AS rep, v.sale_date
  FROM v_sales v, mx
  WHERE v.division = 'Accessories' AND NOT v.is_giveaway AND NOT v.is_cash_customer
    AND v.customer_name IS NOT NULL AND COALESCE(v.quantity, 0) > 0
    AND v.sale_date > mx.d - 365),
owner AS (
  SELECT DISTINCT ON (customer_name) customer_name, rep, n AS rep_visits
  FROM (SELECT customer_name, rep, COUNT(*) AS n, MAX(sale_date) AS last_d FROM base GROUP BY 1, 2) x
  ORDER BY customer_name, n DESC, last_d DESC, rep),
dates AS (SELECT DISTINCT customer_name, sale_date FROM base),
gaps AS (
  SELECT customer_name, percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) AS median_gap
  FROM (SELECT customer_name, sale_date - lag(sale_date) OVER (PARTITION BY customer_name ORDER BY sale_date) AS gap
        FROM dates) g
  WHERE gap > 0 GROUP BY 1),
agg AS (SELECT customer_name, COUNT(*) AS visits, MAX(sale_date) AS last_date, MIN(sale_date) AS first_date
        FROM dates GROUP BY 1),
val AS (
  SELECT v.customer_name, SUM(v.net_bhd) AS net_180d, COUNT(DISTINCT v.invoice_no) AS invoices_180d
  FROM v_sales v, mx
  WHERE v.division = 'Accessories' AND NOT v.is_giveaway AND NOT v.is_cash_customer
    AND v.customer_name IS NOT NULL AND v.sale_date > mx.d - 180
  GROUP BY 1)
SELECT o.customer_name, o.rep, o.rep_visits, a.visits, a.last_date::text AS last_date, a.first_date::text AS first_date,
       (mx.d - a.last_date) AS days_since, ROUND(g.median_gap::numeric, 1) AS median_gap,
       ROUND(COALESCE(val.net_180d, 0)::numeric, 3) AS net_180d, COALESCE(val.invoices_180d, 0) AS invoices_180d,
       mx.d::text AS data_through
FROM owner o JOIN agg a USING (customer_name)
LEFT JOIN gaps g USING (customer_name) LEFT JOIN val USING (customer_name) CROSS JOIN mx
WHERE ($1 = '' OR o.rep = $1 OR split_part(o.rep, ' - ', 1) = $1)
ORDER BY o.customer_name
"""


def load_shops(focus_name: str | None, run=exec_sql_params, *, all_reps: bool = False) -> list[dict]:
    """The SQL above for one rep, or for every rep when `all_reps` is set explicitly (the admin
    route only). A blank name without it is an error, never the whole company book: a salesman
    whose focus_name is empty or whitespace must see nothing. `run(sql, params)` is
    exec_sql_params in production and a psycopg adapter in the local-Postgres test."""
    name = str(focus_name or "").strip()
    if all_reps:
        return run(SHOPS_SQL, [""]) or []
    if not name:
        raise ValueError("a rep's Focus name is required (all_reps=True for the company book)")
    return run(SHOPS_SQL, [name]) or []


# ── pure rules ────────────────────────────────────────────────────────────────

def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def rep_base(name) -> str:
    """The Focus rep name without its warehouse suffix: 'Ahmed Aradi - Acc WH' → 'Ahmed Aradi'.
    This is what salesmen.focus_name and salesman_targets.salesman hold."""
    return str(name or "").split(" - ")[0].strip()


def is_holdout(rep_key: str, shop: str) -> bool:
    """Deterministic 20 % holdout per (rep, shop): the same pair always lands on the same side,
    nothing is stored. sha1 so the split is uniform whatever the name. The rep key is the base
    Focus name, so the rep's own list and the admin's view of it agree on every shop."""
    digest = hashlib.sha1(f"{_norm(rep_base(rep_key))}|{_norm(shop)}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % HOLDOUT_MOD == 0


def _median(values: list[float]) -> float | None:
    v = sorted(x for x in values if x is not None and x > 0)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def rank_shops(shops: list[dict], regulars: dict[str, list[dict]] | None = None,
               contacts: dict[str, dict] | None = None, *,
               include_holdout: bool = False, names: dict[str, str] | None = None) -> dict:
    """Pure ranking (unit-tested without a database). `shops` are SHOPS_SQL rows; `regulars`
    maps a shop to its v_customer_regulars rows; `contacts` maps a shop to {phone, ...};
    `names` maps an item code to its display name. The holdout is seeded by each row's own
    owner (its `rep`), so the rep's list and the admin's view of that rep agree."""
    regulars = regulars or {}
    contacts = contacts or {}
    names = names or {}
    gap_default = _median([_f(s.get("median_gap")) for s in shops]) or DEFAULT_GAP_DAYS
    due: list[dict] = []
    lapsed: list[dict] = []
    fresh = 0
    held: list[dict] = []
    data_through = None
    for s in shops:
        shop = str(s.get("customer_name") or "")
        if not shop:
            continue
        data_through = data_through or s.get("data_through")
        owner = str(s.get("rep") or "")
        holdout = is_holdout(owner, shop)
        days = _i(s.get("days_since"))
        own_gap = _f(s.get("median_gap")) if s.get("median_gap") is not None else 0.0
        gap = own_gap if own_gap > 0 else gap_default
        gap_source = "own" if own_gap > 0 else "default"
        ratio = round(days / gap, 2) if gap > 0 else 0.0
        monthly = (d3(s.get("net_180d")) / Decimal(6)).quantize(_Q3, rounding=ROUND_HALF_UP)
        visits = _i(s.get("visits"))
        is_lapsed = days > max(LAPSED_MIN_DAYS, LAPSED_GAP_MULT * gap)
        skus = _top_skus(regulars.get(shop) or [], names)
        contact = contacts.get(shop) or {}
        row = {
            "shop": shop, "rep": owner, "days_since": days, "gap_days": round(gap, 1), "gap_source": gap_source,
            "overdue_ratio": ratio, "monthly_value_bhd": format(monthly, "f"),
            "invoices_180d": _i(s.get("invoices_180d")), "visits": visits, "rep_visits": _i(s.get("rep_visits")),
            "last_date": s.get("last_date"), "first_date": s.get("first_date"),
            "top_skus": skus, "phone": contact.get("phone") or None,
            "why": _why(days, gap, gap_source, visits, is_lapsed),
            "holdout": holdout,
        }
        if holdout and not include_holdout:
            held.append(row)          # counted, never shown to the rep
            continue
        if is_lapsed:
            row["list"] = "lapsed"
            lapsed.append(row)
        elif ratio >= DUE_RATIO:
            row["list"] = "due"
            due.append(row)
        else:
            fresh += 1
    # score = overdue ratio × monthly value; ties → the older gap first, then the name (stable)
    due.sort(key=lambda r: (-(Decimal(str(r["overdue_ratio"])) * Decimal(r["monthly_value_bhd"])),
                            -r["days_since"], r["shop"]))
    lapsed.sort(key=lambda r: (-r["visits"], r["days_since"], r["shop"]))
    for lst in (due, lapsed):
        for n, r in enumerate(lst, 1):
            r["rank"] = n
    return {
        "data_through": data_through, "due": due, "lapsed": lapsed,
        "counts": {"shops": len(shops), "due": len(due), "lapsed": len(lapsed), "fresh": fresh,
                   "holdout": len(held) if not include_holdout else sum(1 for r in due + lapsed if r["holdout"])},
        "rules": {"due_ratio": DUE_RATIO, "lapsed_min_days": LAPSED_MIN_DAYS, "lapsed_gap_mult": LAPSED_GAP_MULT,
                  "default_gap_days": round(gap_default, 1), "holdout_share": 1.0 / HOLDOUT_MOD},
    }


def _why(days: int, gap: float, gap_source: str, visits: int, lapsed: bool) -> str:
    """The one line under the shop name. Facts only — a cadence and an age, never a date."""
    last = f"last bought {days} day{'' if days == 1 else 's'} ago"
    if lapsed:
        return f"Bought {visits} time{'' if visits == 1 else 's'} · {last}"
    if gap_source == "own":
        return f"Usually every ~{gap:.0f} days · {last}"
    return f"No pattern yet · {last}"


def _top_skus(rows: list[dict], names: dict[str, str]) -> list[dict]:
    """The due SKUs (v_customer_regulars.due), most-bought first, capped at TOP_SKUS."""
    due_rows = [r for r in rows if r.get("due") in (True, "true", "t", 1)]
    due_rows.sort(key=lambda r: (-_i(r.get("times_bought")), -_i(r.get("days_since")), str(r.get("item_code"))))
    out = []
    for r in due_rows[:TOP_SKUS]:
        code = str(r.get("item_code") or "")
        out.append({"item_code": code, "display_name": names.get(code) or names.get(code.upper()) or code,
                    "median_qty": max(1, _i(r.get("median_qty"), 1)), "times_bought": _i(r.get("times_bought")),
                    "cadence_days": (_f(r.get("cadence_days")) if r.get("cadence_days") is not None else None),
                    "days_since": _i(r.get("days_since"))})
    return out


def wa_text(row: dict, rep_name: str | None = None) -> str:
    """A WhatsApp opener the rep can edit: the usual items with the usual quantities, no dates."""
    items = ", ".join(f"{s['display_name']} ×{s['median_qty']}" for s in row.get("top_skus") or [])
    who = f"{rep_name} from YQ" if rep_name else "YQ"
    if items:
        return f"Hello, {who} here. Shall I bring your usual — {items} — on my next round?"
    return f"Hello, {who} here. Shall I bring your usual order on my next round?"


# ── the request path ──────────────────────────────────────────────────────────

_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()


def invalidate() -> None:
    with _lock:
        _cache.clear()


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


REGULARS_PAGE = 500        # under PostgREST's max-rows (1000 on Supabase), which truncates silently


def load_regulars(shop_names: list[str]) -> dict[str, list[dict]]:
    """v_customer_regulars rows for these shops only (service role; the view is never granted to
    the read-only RPC because it carries names). Only rows that are due are ever used, so they
    are the only rows fetched — filtered server-side, in a fixed order, and paged, because
    PostgREST caps a response at max-rows without saying so (one shop alone has had 143 rows).
    {} when the view is not there."""
    from app.database import get_client
    out: dict[str, list[dict]] = {}
    if not shop_names:
        return out
    try:
        for chunk in _chunks(shop_names, 80):
            start = 0
            while True:
                rows = (get_client().table("v_customer_regulars")
                        .select("customer_name,item_code,times_bought,median_qty,cadence_days,last_bought,days_since,due")
                        .in_("customer_name", chunk).eq("due", True)
                        .order("customer_name").order("item_code")
                        .range(start, start + REGULARS_PAGE - 1).execute().data or [])
                for r in rows:
                    out.setdefault(str(r.get("customer_name") or ""), []).append(r)
                if len(rows) < REGULARS_PAGE:
                    break
                start += REGULARS_PAGE
    except Exception as e:  # noqa: BLE001 — the list still works, just without the SKU hints
        log.warning("v_customer_regulars unavailable (no SKU hints): %s", e)
    return out


def load_contacts(shop_names: list[str]) -> dict[str, dict]:
    from app.customer_contacts import get_contacts
    out: dict[str, dict] = {}
    for chunk in _chunks(shop_names, 80):
        out.update(get_contacts(chunk))
    return out


def _display_names() -> dict[str, str]:
    try:
        from app import shop
        items = shop.context().get("items") or {}
        out = {}
        for code, it in items.items():
            name = it.get("display_name") or code
            out[code] = name
            out[str(code).upper()] = name
        return out
    except Exception as e:  # noqa: BLE001
        log.debug("catalog names unavailable for follow-ups: %s", e)
        return {}


def followups(focus_name: str | None, *, all_reps: bool = False, include_holdout: bool = False,
              force: bool = False) -> dict:
    """The rep's ranked lists. The company book (every shop with its owner) is only ever served
    when `all_reps` is passed explicitly — the admin route does; a blank name on its own raises,
    so no login can be handed every rep's shops by an empty focus_name. Cached per (rep, holdout
    view) for _TTL seconds."""
    name = str(focus_name or "").strip()
    if not all_reps and not name:
        raise ValueError("a rep's Focus name is required (all_reps=True for the company book)")
    key = ("*" if all_reps else name, bool(include_holdout))
    now = time.monotonic()
    if not force:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    shops = load_shops(None, all_reps=True) if all_reps else load_shops(name)
    shop_names = [str(s.get("customer_name")) for s in shops if s.get("customer_name")]
    regulars = load_regulars(shop_names)
    contacts = load_contacts(shop_names)
    out = rank_shops(shops, regulars, contacts, include_holdout=include_holdout, names=_display_names())
    out["rep"] = None if all_reps else name
    with _lock:
        _cache[key] = (now + _TTL, out)
    return out


def on_list(focus_name: str | None, shop: str) -> bool:
    """Is `shop` on this rep's CURRENT due / lapsed list (the cached one the screen shows)? For
    the tap log: a tap on a shop the rep was never shown is noise, not a measurement."""
    name = str(focus_name or "").strip()
    if not name or not shop:
        return False
    try:
        out = followups(name)
    except Exception as e:  # noqa: BLE001
        log.debug("follow-up list unavailable for a tap check (%s): %s", name, e)
        return False
    want = _norm(shop)
    return any(_norm(r.get("shop")) == want for r in (out.get("due") or []) + (out.get("lapsed") or []))


# ── "Baskets from your link not sent" (shop_events, PII-free) ─────────────────

def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def group_baskets(events: list[dict], ordered_devices: set[str], prices: dict[str, Decimal] | None = None,
                  names: dict[str, str] | None = None, *, limit: int = 10) -> list[dict]:
    """Pure. Replays add / qty / remove per device in time order, drops any device that placed an
    order (an 'order' event or a shop_orders row) inside the window, and returns the open baskets:
    item codes and quantities, a value at today's trade price (Decimal), the last activity — no
    device id, phone or name (the device is replaced by a short opaque label)."""
    prices = prices or {}
    names = names or {}
    by_dev: dict[str, dict] = {}
    for e in sorted(events, key=lambda x: str(x.get("ts") or "")):
        dev = str(e.get("device_id") or "")
        if not dev:
            continue
        kind = str(e.get("event") or "")
        b = by_dev.setdefault(dev, {"items": {}, "last": None, "ordered": False, "first": None})
        b["last"] = e.get("ts") or b["last"]
        b["first"] = b["first"] or e.get("ts")
        if kind == "order":
            b["ordered"] = True
            continue
        code = str(e.get("item_code") or "").strip().upper()
        meta = e.get("meta") if isinstance(e.get("meta"), dict) else {}
        if kind == "add" and code:
            b["items"][code] = max(1, _i(meta.get("count"), 1))
        elif kind == "qty" and code:
            q = _i(meta.get("count"), 0)
            if q > 0:
                b["items"][code] = q
            elif code in b["items"]:
                del b["items"][code]
        elif kind == "remove" and code:
            b["items"].pop(code, None)
    out = []
    for dev, b in by_dev.items():
        if b["ordered"] or dev in ordered_devices or not b["items"]:
            continue
        lines = []
        total = Decimal("0.000")
        for code, qty in b["items"].items():
            price = prices.get(code)
            line = (d3(price) * qty).quantize(_Q3, rounding=ROUND_HALF_UP) if price is not None else None
            if line is not None:
                total += line
            lines.append({"item_code": code, "display_name": names.get(code) or code, "qty": qty,
                          "line_bhd": (format(line, "f") if line is not None else None)})
        lines.sort(key=lambda ln: (-(Decimal(ln["line_bhd"]) if ln["line_bhd"] else Decimal(0)), ln["item_code"]))
        out.append({"basket": hashlib.sha1(dev.encode()).hexdigest()[:6], "items": lines, "items_count": len(lines),
                    "units": sum(b["items"].values()), "value_bhd": format(total, "f"),
                    "last_activity": b["last"], "first_activity": b["first"]})
    out.sort(key=lambda r: (-Decimal(r["value_bhd"]), str(r["last_activity"] or "")))
    return out[:limit]


def baskets_not_sent(sm: dict, days: int = BASKET_DAYS, now: datetime | None = None) -> dict:
    """The rep's open baskets: adds on his link (referral_code) with no order from that device
    inside the window. Empty (not an error) when he has no referral code or the tables are off."""
    from app.database import get_client
    from app import shop
    code = str((sm or {}).get("referral_code") or "").strip().lower()
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=max(1, min(int(days), 30)))).isoformat()
    if not code:
        return {"days": days, "since": since[:10], "baskets": [], "count": 0}
    client = get_client()
    try:
        ev = (client.table("shop_events").select("ts,event,device_id,item_code,meta")
              .eq("referral_code", code).gte("ts", since).in_("event", ["add", "qty", "remove", "order"])
              .order("ts").limit(5000).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("shop_events unavailable for baskets: %s", e)
        ev = []
    ordered: set[str] = set()
    try:
        rows = shop._select_optional("shop_orders", "device_id,status", "is_test",
                                     lambda q: q.gte("created_at", since).not_.is_("device_id", "null")
                                     .limit(5000).execute().data or [])
        ordered = {str(o.get("device_id")) for o in rows if o.get("device_id") and not shop._is_test(o)}
    except Exception as e:  # noqa: BLE001
        log.debug("shop_orders unavailable for baskets: %s", e)
    prices: dict[str, Decimal] = {}
    names: dict[str, str] = {}
    try:
        ctx = shop.context()
        for c, it in (ctx.get("items") or {}).items():
            if it.get("standard_rate") is not None:
                prices[str(c).upper()] = d3(it.get("standard_rate"))
            names[str(c).upper()] = it.get("display_name") or c
    except Exception as e:  # noqa: BLE001
        log.debug("catalog unavailable for basket values: %s", e)
    baskets = group_baskets(ev, ordered, prices, names)
    return {"days": days, "since": since[:10], "baskets": baskets, "count": len(baskets),
            "value_bhd": format(sum((Decimal(b["value_bhd"]) for b in baskets), Decimal("0.000")), "f")}


# ── "My link this week" (a rep-scoped analytics summary) ──────────────────────

LINK_TTL = 180.0           # shop.analytics reads every event of the window; once per rep per few minutes


def link_week(sm: dict, days: int = 7, *, force: bool = False) -> dict:
    """The small card: the funnel from the rep's link over `days`, orders and value. Built on
    shop.analytics(days, salesman=sm) — the same scoping the admin analytics page uses — and
    cached per rep for LINK_TTL seconds, because that call replays the whole company's events
    and the Today screen mounts it on every visit."""
    from app import shop
    key = ("link", str((sm or {}).get("id")), int(days))
    now = time.monotonic()
    if not force:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    a = shop.analytics(days, salesman=sm)
    out = _link_summary(a, days)
    with _lock:
        _cache[key] = (now + LINK_TTL, out)
    return out


def _link_summary(a: dict, days: int) -> dict:
    f = a.get("funnel") or {}
    return {"days": a.get("days", days), "since": a.get("since"),
            "sessions": _i(f.get("sessions")), "item_views": _i(f.get("item_views")), "adds": _i(f.get("adds")),
            "checkouts": _i(f.get("checkouts")), "orders": _i(a.get("orders")), "cancelled": _i(a.get("cancelled")),
            "value_bhd": format(d3(a.get("value_bhd")), "f"), "aov_bhd": format(d3(a.get("aov_bhd")), "f"),
            "customers": _i(a.get("customers")), "conversion_pct": f.get("conversion_pct"),
            "shares": _i((a.get("engagement") or {}).get("share")),
            "top_products": [{"item_code": p.get("item_code"), "display_name": p.get("display_name"),
                              "units": _i(p.get("units")), "value_bhd": format(d3(p.get("value_bhd")), "f")}
                             for p in (a.get("top_products") or [])[:3]]}


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 5 "rep speed" (27-Sep-2026; plan §10, §11, §23 items 2-3). A repeat order in a handful
# of taps: ONE book of shops (the rep's Focus regulars and the shops he has ordered for in the
# app, merged), the usual basket behind "Order again" and "Send restock link", and Today in one
# call. Reads only — the one write is a phone a rep fills in for one of his own Focus shops that
# has none on file (save_shop_phone), never an overwrite.
# ══════════════════════════════════════════════════════════════════════════════

BOOK_TTL = 120.0           # the book: per rep, dropped when he saves a phone or places an order
TODAY_TTL = 60.0           # Today's heavy half (me, due, baskets, link week, restock), per rep
PARTIAL_TTL = 15.0         # either of the two when part of it failed: retried soon, not per tap
BASKET_MAX = 12            # usual-basket lines listed on a shop
SUGGEST_MAX = 8            # lines "Order again" preloads from the Focus regulars
WAITING_MAX = 20           # orders "To confirm" listed on Today
MARKET_BOOK_MAX = 100      # app customers merged into a book (shop.recent_customers caps at 100)
_LEGAL_WORDS = frozenset({"wll", "spc", "bsc", "co", "est", "llc", "ltd", "company"})


def name_key(name) -> str:
    """A shop name reduced to what identifies it: lower case, no punctuation, no spaces, legal
    suffixes dropped — 'AL-NOOR MOBILE W.L.L.' and 'Al Noor Mobile' both become 'alnoormobile'.
    The dedup rule between a Focus customer and the shop a rep typed at checkout."""
    s = str(name or "").lower().replace("&", " and ")
    s = re.sub(r"[.'`’]", "", s)
    words = [w for w in re.split(r"[^0-9a-z؀-ۿ]+", s) if w and w not in _LEGAL_WORDS]
    return "".join(words)


def _phone(raw) -> str | None:
    from app.shop import phone_digits
    return phone_digits(raw)


def shop_key(focus_name: str | None = None, phone: str | None = None) -> str:
    """The book's id for a shop: 'f:<Focus customer name>' for a Focus shop (merged or not),
    'm:<phone digits>' for an app-only customer."""
    if focus_name:
        return f"f:{focus_name}"
    digits = _phone(phone)
    return f"m:{digits}" if digits else ""


def _day(v) -> str | None:
    """'2026-09-24T10:11:12+00:00' / '2026-09-24' → '2026-09-24'."""
    s = str(v or "").strip()
    return s[:10] if len(s) >= 10 else None


def merge_book(shops: list[dict], market: list[dict], contacts: dict[str, dict] | None = None,
               credit: dict[str, dict] | None = None, *, include_holdout: bool = False) -> list[dict]:
    """Pure. One row per shop: the rep's Focus shops (SHOPS_SQL rows) and his app customers
    (shop.recent_customers rows, latest first), an app customer folded into the Focus shop with
    the same phone or the same shop name (name_key). Holdout shops stay in the book — a rep must
    find every shop he serves — but are never marked due, so the book never nudges toward one;
    only an admin view (`include_holdout`) carries the flag. Sorted: due first (most overdue ×
    value), then the most recent purchase, then the name."""
    contacts = contacts or {}
    credit = credit or {}
    gap_default = _median([_f(s.get("median_gap")) for s in shops]) or DEFAULT_GAP_DAYS
    rows: list[dict] = []
    by_phone: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for s in shops:
        shop = str(s.get("customer_name") or "")
        if not shop:
            continue
        holdout = is_holdout(str(s.get("rep") or ""), shop)
        days = _i(s.get("days_since"))
        own_gap = _f(s.get("median_gap")) if s.get("median_gap") is not None else 0.0
        gap = own_gap if own_gap > 0 else gap_default
        gap_source = "own" if own_gap > 0 else "default"
        ratio = round(days / gap, 2) if gap > 0 else 0.0
        monthly = (d3(s.get("net_180d")) / Decimal(6)).quantize(_Q3, rounding=ROUND_HALF_UP)
        visits = _i(s.get("visits"))
        lapsed = days > max(LAPSED_MIN_DAYS, LAPSED_GAP_MULT * gap)
        status = "lapsed" if lapsed else "due" if ratio >= DUE_RATIO else "ok"
        if holdout and status == "due":
            status = "ok"                     # held back: in the book, never nudged
        contact = contacts.get(shop) or {}
        phone = _phone(contact.get("phone"))
        row = {
            "key": shop_key(shop), "name": shop, "focus_name": shop, "contact_name": None,
            "phone": phone, "area": None, "email": None, "sources": ["focus"],
            "last_focus_date": _day(s.get("last_date")), "last_app_order_at": None, "app_orders": 0,
            "last_order_date": _day(s.get("last_date")), "days_since": days,
            "status": status, "due": status == "due",
            "why": (f"Last bought {days} day{'' if days == 1 else 's'} ago" if holdout and not include_holdout
                    else _why(days, gap, gap_source, visits, lapsed)),
            "monthly_value_bhd": format(monthly, "f"), "visits": visits,
            "credit": credit.get(shop) or None,
            "_score": Decimal(str(ratio)) * monthly,
        }
        if include_holdout:
            row["holdout"] = holdout
        rows.append(row)
        if phone:
            by_phone.setdefault(phone, row)
        nk = name_key(shop)
        if nk:
            by_name.setdefault(nk, row)
    for c in market:
        phone = _phone(c.get("phone"))
        target = (by_phone.get(phone) if phone else None) or by_name.get(name_key(c.get("shop")))
        if target is None:
            if not phone:
                continue                     # an app order always carries a phone; nothing to key on
            target = {
                "key": shop_key(None, phone), "name": str(c.get("shop") or c.get("name") or ""),
                "focus_name": None, "contact_name": c.get("name") or None, "phone": phone,
                "area": c.get("area") or None, "email": c.get("email") or None, "sources": ["app"],
                "last_focus_date": None, "last_app_order_at": None, "app_orders": 0, "last_order_date": None,
                "days_since": None, "status": "ok", "due": False, "why": None, "monthly_value_bhd": None,
                "visits": 0, "credit": None, "_score": Decimal(0),
            }
            if include_holdout:
                target["holdout"] = False
            rows.append(target)
            by_phone[phone] = target
        else:
            if "app" not in target["sources"]:
                target["sources"].append("app")
            for k, v in (("phone", phone), ("contact_name", c.get("name")), ("area", c.get("area")),
                         ("email", c.get("email"))):
                if v and not target.get(k):
                    target[k] = v
            if phone:
                by_phone.setdefault(phone, target)
        target["app_orders"] += _i(c.get("orders"), 1) or 1
        at = c.get("last_order_at")
        if at and str(at) > str(target.get("last_app_order_at") or ""):
            target["last_app_order_at"] = at
            day = _day(at)
            if day and day > str(target.get("last_order_date") or ""):
                target["last_order_date"] = day
    due = sorted((r for r in rows if r["due"]), key=lambda r: (-r["_score"], -_i(r["days_since"]), r["name"]))
    for n, r in enumerate(due, 1):
        r["rank"] = n
    rest = [r for r in rows if not r["due"]]
    rest.sort(key=lambda r: r["name"].lower())
    rest.sort(key=lambda r: str(r.get("last_order_date") or ""), reverse=True)
    out = due + rest
    for r in out:
        r.pop("_score", None)
        r.setdefault("rank", None)
    return out


CREDIT_SQL = ("SELECT account, outstanding_bhd, over_90_bhd, as_of_date::text AS as_of FROM v_receivables "
              "WHERE over_90_bhd > 0 AND account IN (SELECT jsonb_array_elements_text($1::jsonb))")


def load_credit(names: list[str]) -> dict[str, dict]:
    """The over-90 receivable per Focus account, for the credit chip — only accounts that HAVE
    one (a clean account carries no chip). The ageing account name is the Focus customer name
    (82 of 84 accounts match exactly on 27-Sep-2026). {} when the view cannot be read."""
    import json
    out: dict[str, dict] = {}
    for chunk in _chunks([n for n in names if n], 200):
        try:
            rows = exec_sql_params(CREDIT_SQL, [json.dumps(chunk)]) or []
        except Exception as e:  # noqa: BLE001 — the book works without the chip
            log.warning("receivables unavailable for the credit chip: %s", e)
            return out
        for r in rows:
            over = d3(r.get("over_90_bhd"))
            if over > 0:
                out[str(r.get("account"))] = {"over_90_bhd": format(over, "f"),
                                              "outstanding_bhd": format(d3(r.get("outstanding_bhd")), "f"),
                                              "as_of": r.get("as_of")}
    return out


def _cache_get(key: tuple):
    hit = _cache.get(key)
    return hit[1] if hit and hit[0] > time.monotonic() else None


def _cache_put(key: tuple, ttl: float, value) -> None:
    with _lock:
        _cache[key] = (time.monotonic() + ttl, value)


def forget_rep(sm: dict | None) -> None:
    """Drop one rep's book and Today (his order or his saved phone changed them)."""
    sid = str((sm or {}).get("id") if sm else "*")
    with _lock:
        for k in [k for k in _cache if k[:1] in (("book",), ("today",)) and len(k) > 1 and k[1] == sid]:
            _cache.pop(k, None)


UNLINKED_FOCUS_HINT = "Your login has no Focus name yet — only the shops you have ordered for in the app are listed."


def book(sm: dict | None, *, admin: bool = False, force: bool = False) -> dict:
    """The merged book for a rep (his Focus shops + his app customers), cached BOOK_TTL per rep.
    `sm` None = an admin with no salesman row: every app customer, no Focus shops (the company
    Focus book is the follow-ups admin view). A failed Focus read still returns the app
    customers, says so, and is not cached."""
    from app import shop as _shop
    sid = str(sm.get("id")) if sm else "*"
    key = ("book", sid, bool(admin))
    if not force:
        hit = _cache_get(key)
        if hit is not None:
            return hit
    focus = str((sm or {}).get("focus_name") or "").strip()
    shops: list[dict] = []
    hint = None
    error = None
    if sm and focus:
        try:
            shops = load_shops(focus)
        except Exception as e:  # noqa: BLE001
            log.warning("Focus book unavailable for %s: %s", focus, e)
            error = "Your Focus shops could not be loaded just now — the app customers are listed. Try again in a minute."
    elif sm:
        hint = UNLINKED_FOCUS_HINT
    names = [str(s.get("customer_name")) for s in shops if s.get("customer_name")]
    contacts = load_contacts(names) if names else {}
    credit = load_credit(names) if names else {}
    try:
        market = _shop.recent_customers(sm.get("id") if sm else None, limit=MARKET_BOOK_MAX)
    except Exception as e:  # noqa: BLE001
        log.warning("app customers unavailable for the book: %s", e)
        market = []
        error = error or "Your app customers could not be loaded just now. Try again in a minute."
    rows = merge_book(shops, market, contacts, credit, include_holdout=admin)
    out = {"shops": rows, "count": len(rows),
           "counts": {"due": sum(1 for r in rows if r["due"]), "focus": sum(1 for r in rows if r["focus_name"]),
                      "app": sum(1 for r in rows if "app" in r["sources"]),
                      "credit": sum(1 for r in rows if r.get("credit"))},
           "data_through": next((s.get("data_through") for s in shops if s.get("data_through")), None),
           "hint": hint, "error": error}
    _cache_put(key, BOOK_TTL if not error else PARTIAL_TTL, out)
    return out


def find_shop(bk: dict, key: str) -> dict | None:
    """A row of this book by its key; an 'm:' key also finds the merged Focus row carrying that
    phone (a shop picked from an older app-only row keeps working after the merge)."""
    key = str(key or "").strip()
    if not key:
        return None
    rows = bk.get("shops") or []
    hit = next((r for r in rows if r.get("key") == key), None)
    if hit is None and key.startswith("m:"):
        digits = _phone(key[2:])
        hit = next((r for r in rows if digits and r.get("phone") == digits), None)
    return hit


# ── the usual basket ──────────────────────────────────────────────────────────

def load_shop_regulars(focus_name: str) -> list[dict]:
    """Every v_customer_regulars row for one Focus shop (bought on two or more dates — the view's
    own rule), paged like load_regulars. Service role: the view carries names and is not granted
    to the read-only RPC. [] when it cannot be read."""
    from app.database import get_client
    out: list[dict] = []
    if not focus_name:
        return out
    try:
        start = 0
        while True:
            rows = (get_client().table("v_customer_regulars")
                    .select("customer_name,item_code,times_bought,median_qty,cadence_days,last_bought,days_since,due")
                    .eq("customer_name", focus_name).order("item_code")
                    .range(start, start + REGULARS_PAGE - 1).execute().data or [])
            out.extend(rows)
            if len(rows) < REGULARS_PAGE:
                break
            start += REGULARS_PAGE
    except Exception as e:  # noqa: BLE001
        log.warning("v_customer_regulars unavailable for a basket: %s", e)
    return out


def last_app_order(salesman_id, phone: str | None, focus_name: str | None) -> dict | None:
    """The shop's most recent app order (not cancelled, not a test), with its lines — found by
    phone, else by the Focus name typed as the shop. A rep's own orders only (salesman_id);
    None = any rep's (an admin)."""
    from app import shop as _shop
    if not phone and not focus_name:
        return None

    def _run(q):
        q = q.eq("customer_phone", phone) if phone else q.eq("customer_shop", focus_name)
        if salesman_id is not None:
            q = q.eq("salesman_id", salesman_id)
        return q.order("created_at", desc=True).limit(10).execute().data or []
    try:
        rows = _shop._select_optional("shop_orders", "id,order_no,status,created_at,customer_phone,customer_shop",
                                      "is_test", _run)
    except Exception as e:  # noqa: BLE001
        log.warning("last app order unavailable: %s", e)
        return None
    rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    o = next((r for r in rows if r.get("status") != "cancelled" and not _shop._is_test(r)), None)
    if not o:
        return None
    try:
        lines = _shop._order_lines(o["id"])
    except Exception as e:  # noqa: BLE001
        log.warning("last app order lines unavailable: %s", e)
        lines = []
    return {"order_no": o.get("order_no"), "created_at": o.get("created_at"), "status": o.get("status"),
            "lines": lines}


def _step_min(it: dict) -> tuple[int, int]:
    """(pack step, smallest orderable quantity) — the storefront's stepOf / minQtyOf."""
    step = max(1, _i(it.get("pack_size"), 1) or 1)
    moq = max(1, _i(it.get("moq"), 1) or 1)
    return step, max(step, -(-moq // step) * step)


def orderable_qty(it: dict | None, qty) -> int:
    """A wanted quantity made orderable: at least the minimum, a whole number of packs."""
    q = max(1, _i(qty, 1))
    if not it:
        return q
    step, low = _step_min(it)
    return max(low, -(-q // step) * step)


def _basket_line(code: str, qty, items: dict, extra: dict | None = None) -> dict:
    it = items.get(code) or items.get(code.upper()) or None
    stock = _i((it or {}).get("stock_qty"), 0)
    price = (it or {}).get("standard_rate")
    q = orderable_qty(it, qty)
    line = {"item_code": (it or {}).get("item_code") or code,
            "display_name": (it or {}).get("display_name") or code, "qty": q,
            "in_catalog": it is not None, "sold_out": it is not None and stock <= 0,
            "price_bhd": (format(d3(price), "f") if price is not None else None)}
    line.update(extra or {})
    return line


def restock_link(link: str | None, lines: list[dict]) -> str | None:
    """The rep's storefront link carrying a ready cart: /{slug}?order=CODE:QTY,… (the market
    fills the cart from it — web/src/MarketApp.tsx EntryParams). Codes are URL-encoded the way the
    checkout's "Send as a ready order" does it."""
    from urllib.parse import quote
    if not link or not lines:
        return None
    joined = ",".join(f"{quote(str(ln['item_code']), safe='')}:{int(ln['qty'])}" for ln in lines)
    return f"{link}{'&' if '?' in link else '?'}order={joined}"


def basket_payload(row: dict, regulars: list[dict], last: dict | None, items: dict,
                   link: str | None = None) -> dict:
    """Pure. The shop's usual basket (its v_customer_regulars rows, due first, most-bought next,
    at the median quantity made orderable), its last app order, and the SUGGESTED repeat — the
    last app order when it is newer than the last Focus purchase (or there are no regulars),
    else the due regulars (else the most-bought ones), capped at SUGGEST_MAX. Lines that are not
    in the catalog any more are dropped from the suggestion; sold-out lines are listed apart and
    never go into the restock link."""
    usual_rows = sorted(regulars, key=lambda r: (not (r.get("due") in (True, "true", "t", 1)),
                                                 -_i(r.get("times_bought")), -_i(r.get("days_since")),
                                                 str(r.get("item_code") or "")))
    # only what the app sells: a SIM, a giveaway line or a product off the shelf is Focus history,
    # not something the rep can put in a cart
    usual = [ln for ln in (_basket_line(str(r.get("item_code") or ""), r.get("median_qty"), items,
                                        {"times_bought": _i(r.get("times_bought")),
                                         "due": r.get("due") in (True, "true", "t", 1),
                                         "cadence_days": (_f(r.get("cadence_days"))
                                                          if r.get("cadence_days") is not None else None),
                                         "days_since": _i(r.get("days_since"))})
                           for r in usual_rows if r.get("item_code")) if ln["in_catalog"]][:BASKET_MAX]
    last_out = None
    if last:
        from app.shop_heart import LINE_OUT
        lines = []
        for ln in last.get("lines") or []:
            # a line out of the order (removed / unavailable / substituted — its substitute is a line
            # of its own) is not part of the repeat
            if str(ln.get("line_status") or "") in LINE_OUT:
                continue
            # what the shop really got: delivered, else confirmed, else what it asked for
            q = next((ln.get(k) for k in ("qty_delivered", "qty_confirmed", "qty") if ln.get(k) is not None), None)
            if _i(q) <= 0 or not ln.get("item_code"):
                continue
            lines.append(_basket_line(str(ln["item_code"]), q, items))
        last_out = {"order_no": last.get("order_no"), "created_at": last.get("created_at"),
                    "status": last.get("status"), "lines": lines}
    focus_day = str(row.get("last_focus_date") or "")
    app_day = _day((last or {}).get("created_at")) or ""
    use_last = bool(last_out and last_out["lines"]) and (not usual or app_day >= focus_day)
    if use_last:
        pool, source = last_out["lines"], "last_order"
    else:
        due_lines = [ln for ln in usual if ln.get("due")]
        pool, source = (due_lines or usual), ("due_regulars" if due_lines else "regulars")
    suggested = [ln for ln in pool if ln["in_catalog"]][:SUGGEST_MAX]
    available = [ln for ln in suggested if not ln["sold_out"]]
    value = sum((d3(ln["price_bhd"]) * ln["qty"] for ln in available if ln["price_bhd"] is not None), Decimal("0.000"))
    return {
        "shop": {k: row.get(k) for k in ("key", "name", "focus_name", "contact_name", "phone", "area", "email",
                                         "last_order_date", "status", "due", "why", "credit")},
        "usual": usual, "last_order": last_out,
        "suggested": suggested, "suggested_from": source if suggested else None,
        "available": available, "sold_out": [ln for ln in suggested if ln["sold_out"]],
        "value_bhd": format(value.quantize(_Q3, rounding=ROUND_HALF_UP), "f"),
        "restock_link": restock_link(link, available),
    }


def shop_basket(sm: dict | None, key: str, *, admin: bool = False) -> dict | None:
    """The basket for one shop of the caller's book; None = not in his book (the route's 404).
    An admin may ask for any Focus shop or app phone by key."""
    from app import shop as _shop
    bk = book(sm, admin=admin)
    row = find_shop(bk, key)
    if row is None:
        if not admin:
            return None
        key = str(key or "").strip()
        if key.startswith("f:") and key[2:].strip():
            row = {"key": key, "name": key[2:].strip(), "focus_name": key[2:].strip()}
        elif key.startswith("m:") and _phone(key[2:]):
            row = {"key": key, "name": "", "focus_name": None, "phone": _phone(key[2:])}
        else:
            return None
    regulars = load_shop_regulars(row["focus_name"]) if row.get("focus_name") else []
    last = last_app_order((sm or {}).get("id") if sm and not admin else None, row.get("phone"), row.get("focus_name"))
    ctx = _shop.context()
    items = {}
    for code, it in (ctx.get("items") or {}).items():
        items[code] = {**it, "item_code": code}
        items.setdefault(str(code).upper(), items[code])
    link = _shop.salesman_link(sm) if sm else None
    return basket_payload(row, regulars, last, items, link)


# ── a phone for a Focus shop that has none (the one write) ──────────────────────

class BookError(ValueError):
    """A refused book write; the route answers 400 (404 when `missing`)."""

    def __init__(self, msg: str, missing: bool = False):
        super().__init__(msg)
        self.missing = missing


def _stored_phone(digits: str) -> str:
    """customer_contacts keeps the readable shape most rows already have: '+973 3312 3456'."""
    if digits.startswith("973") and len(digits) == 11:
        return f"+973 {digits[3:7]} {digits[7:]}"
    return f"+{digits}"


def save_shop_phone(sm: dict | None, shop_name: str, raw_phone: str, by: str, *, admin: bool = False) -> dict:
    """Fill a BLANK contact phone for a Focus shop (customer_contacts, the table the follow-ups
    read their WhatsApp numbers from). A rep may do it only for a Focus shop of his own book;
    a number already on file is never replaced (saved: false, and the book keeps using it).
    Audited by the route (no digits in the audit detail)."""
    from app.database import get_client
    from app.shop import clean
    digits = _phone(raw_phone)
    if not digits:
        raise BookError("Please enter a valid phone number (8-digit Bahrain or international).")
    name = clean(shop_name, 160)
    if not name:
        raise BookError("Which shop is this number for?")
    if not admin:
        bk = book(sm)
        if not any(r.get("focus_name") == name for r in bk.get("shops") or []):
            raise BookError("That shop is not in your book.", missing=True)
    client = get_client()
    cur = (client.table("customer_contacts").select("customer_name,phone").eq("customer_name", name)
           .limit(1).execute().data or [])
    if cur and str(cur[0].get("phone") or "").strip():
        return {"ok": True, "saved": False, "reason": "A number is already on file for this shop — it was kept."}
    now = datetime.now(timezone.utc).isoformat()
    stored = _stored_phone(digits)
    if cur:
        client.table("customer_contacts").update({"phone": stored, "updated_by": by, "updated_at": now}) \
            .eq("customer_name", name).execute()
    else:
        client.table("customer_contacts").insert({"customer_name": name, "phone": stored, "source": "rep",
                                                  "updated_by": by, "updated_at": now}).execute()
    forget_rep(sm)
    invalidate_followups()
    return {"ok": True, "saved": True}


def invalidate_followups() -> None:
    """Drop the ranked follow-up lists (they carry the contact phones) — not the other caches."""
    with _lock:
        for k in [k for k in _cache if k and k[0] not in ("book", "today", "link")]:
            _cache.pop(k, None)


# ── Today in one call ─────────────────────────────────────────────────────────

def money_strip(target: dict | None) -> dict | None:
    """The top of Today: this month's figure, the tier, and what the next tier is worth —
    "BHD 75.000 more → +28.000 kickback" = the next tier's threshold × its rate (it pays on the
    whole month) minus the kickback already earned. Decimal to the fils; None without a target."""
    if not target:
        return None
    mtd = d3(target.get("mtd_bhd"))
    kick = d3(target.get("kickback_bhd"))
    nxt = target.get("next_tier") or None
    gain = gap = None
    if nxt:
        at_next = (d3(nxt.get("bhd")) * Decimal(str(nxt.get("pct") or 0))).quantize(_Q3, rounding=ROUND_HALF_UP)
        gain = max(Decimal("0.000"), at_next - kick)
        gap = d3(nxt.get("gap_bhd"))
    age = target.get("data_age_days")
    return {"month": target.get("month"), "mtd_bhd": format(mtd, "f"), "tier": _i(target.get("tier_reached")),
            "rate": _f(target.get("kickback_pct")), "kickback_bhd": format(kick, "f"),
            "next_tier": (_i(nxt.get("n")) if nxt else None),
            "next_gap_bhd": (format(gap, "f") if gap is not None else None),
            "next_gain_bhd": (format(gain, "f") if gain is not None else None),
            "days_left": target.get("days_left"), "data_through": target.get("data_through"),
            "data_age_days": age, "stale": bool(age is not None and _i(age) > 1),
            "basis": target.get("basis"), "is_estimate": True,
            "returns_deducted": bool(target.get("returns_deducted"))}


def _waiting(sid, now: datetime, sla_min: int) -> tuple[list[dict], int, int | None]:
    """(orders in 'new' for the scope, OLDEST first, each with its age and an overdue flag;
    how many there are; how many are in progress). Read live, never cached: a confirm must leave
    this list at once."""
    from app import shop as _shop
    from app.shop_jobs import business_minutes

    def _run(q):
        q = q.eq("status", "new")
        if sid is not None:
            q = q.eq("salesman_id", sid)
        return q.order("created_at").limit(WAITING_MAX).execute().data or []
    rows = _shop._select_optional(
        "shop_orders", "id,order_no,status,created_at,assigned_at,customer_name,customer_shop,customer_area,"
                       "total_bhd,items_count,units_count,order_kind", "is_test", _run)
    rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""))
    out = []
    for r in rows:
        created = _ts(r.get("created_at"))
        started = _ts(r.get("assigned_at")) or created
        age = int(max(0.0, (now - created).total_seconds()) // 60) if created else None
        biz = business_minutes(started, now) if started else 0
        out.append({"id": r.get("id"), "order_no": r.get("order_no"), "created_at": r.get("created_at"),
                    "customer_name": r.get("customer_name"), "customer_shop": r.get("customer_shop"),
                    "customer_area": r.get("customer_area"), "total_bhd": format(d3(r.get("total_bhd")), "f"),
                    "items": _i(r.get("items_count")), "units": _i(r.get("units_count")),
                    "order_kind": r.get("order_kind") or "standard", "is_test": bool(r.get("is_test")),
                    "age_min": age, "overdue": bool(sla_min > 0 and biz >= sla_min)})
    try:
        from app.database import get_client

        def _count(statuses: list[str]) -> int:
            q = get_client().table("shop_orders").select("id", count="exact").in_("status", statuses)
            if sid is not None:
                q = q.eq("salesman_id", sid)
            res = q.limit(1).execute()
            return int(res.count if res.count is not None else len(res.data or []))
        waiting_n = _count(["new"])
        in_progress = _count(["confirmed", "packed", "out_for_delivery"])
    except Exception as e:  # noqa: BLE001
        log.debug("order counts unavailable for Today: %s", e)
        waiting_n, in_progress = len(out), None
    return out, max(waiting_n, len(out)), in_progress


def _part(errors: dict, name: str, fn, default=None):
    """One card of Today: its failure is named in `errors` and never takes the others down."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.warning("Today: %s unavailable: %s", name, e)
        errors[name] = "Could not load this just now."
        return default


def today(sm: dict | None, email: str, *, is_admin: bool = False, force: bool = False,
          now: datetime | None = None) -> dict:
    """GET /shop/me/today. The heavy half (the rep card and money strip, due top 5, baskets not
    sent, link week, restock) is cached TODAY_TTL per rep; the "To confirm" queue and the
    in-progress count are read on every call. `sm` None + not admin = an unlinked login: the hint
    and nothing else (the same line /shop/me draws)."""
    from app import shop as _shop
    from app import statements
    now = now or datetime.now(timezone.utc)
    if not sm and not is_admin:
        return {"hint": _shop.UNLINKED_HINT, "me": None, "money": None, "waiting": [], "waiting_count": 0,
                "in_progress": 0, "due": [], "due_count": 0, "due_counts": {}, "baskets": None, "link_week": None,
                "restock": [],
                "errors": {}, "sla_min": None}
    sid = sm.get("id") if sm else None
    key = ("today", str(sid) if sm else "*")
    heavy = None if force else _cache_get(key)
    if heavy is None:
        errors: dict = {}
        me = _part(errors, "me", lambda: _shop.me_payload(email, is_admin=is_admin), {}) or {}
        if me:
            me.update(_part(errors, "statements", lambda: statements.rep_summary(me.get("salesman")), {}) or {})
        focus = str((sm or {}).get("focus_name") or "").strip()
        due_hint = None
        due: list[dict] = []
        due_count = 0
        due_counts: dict = {}
        due_through = None
        if sm and focus:
            fu = _part(errors, "due", lambda: followups(focus), None)
            if fu:
                first = str(sm.get("name") or "").split(" ")[0] or None
                due = [{**r, "key": shop_key(r["shop"]), "wa_text": wa_text(r, first)} for r in fu.get("due", [])[:DUE_CARD]]
                due_counts = dict(fu.get("counts") or {})
                due_count = _i(due_counts.get("due"))
                due_through = fu.get("data_through")
        elif sm:
            due_hint = "Your login has no Focus name yet — ask the office to set it on the Salesmen page."
        heavy = {
            "me": me or None,
            "money": money_strip(((me or {}).get("focus") or {}).get("target")),
            "due": due, "due_count": due_count, "due_counts": due_counts, "due_data_through": due_through,
            "due_hint": due_hint,
            "baskets": (_part(errors, "baskets", lambda: baskets_not_sent(sm), None) if sm else None),
            "link_week": (_part(errors, "link_week", lambda: link_week(sm, 7), None) if sm else None),
            "restock": _part(errors, "restock", lambda: _shop.list_restock(
                None if not sm else ((sm.get("referral_code") or "-") if not is_admin else None)), []) or [],
            "errors": errors,
        }
        # a card that failed is retried soon, not after a minute — but not on every tap either
        _cache_put(key, TODAY_TTL if not errors else PARTIAL_TTL, heavy)
    live_errors: dict = {}
    sla = _i(_part(live_errors, "settings", lambda: _shop.shop_settings().get("shop_confirm_sla_min"), 120), 120)
    waiting, waiting_n, in_progress = _part(live_errors, "waiting", lambda: _waiting(sid, now, sla), ([], 0, None))
    return {**heavy, "errors": {**heavy.get("errors", {}), **live_errors},
            "waiting": waiting, "waiting_count": waiting_n, "in_progress": in_progress, "sla_min": sla,
            "generated_at": now.isoformat(), "hint": None}
