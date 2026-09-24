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

# $1 = the rep's Focus name ('' = every rep, admin only). Names in v_sales carry a warehouse
# suffix ("Ahmed Aradi - Acc WH"), hence the `name - %` pattern shared with rep_month_sales.
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
WHERE ($1 = '' OR o.rep = $1 OR o.rep LIKE $1 || ' - %')
ORDER BY o.customer_name
"""


def load_shops(focus_name: str | None, run=exec_sql_params) -> list[dict]:
    """The SQL above for one rep ('' = all). `run(sql, params)` is exec_sql_params in production
    and a psycopg adapter in the local-Postgres test."""
    return run(SHOPS_SQL, [str(focus_name or "").strip()]) or []


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


def load_regulars(shop_names: list[str]) -> dict[str, list[dict]]:
    """v_customer_regulars rows for these shops only (service role; the view is never granted to
    the read-only RPC because it carries names). {} when the view is not there."""
    from app.database import get_client
    out: dict[str, list[dict]] = {}
    if not shop_names:
        return out
    try:
        for chunk in _chunks(shop_names, 80):
            rows = (get_client().table("v_customer_regulars")
                    .select("customer_name,item_code,times_bought,median_qty,cadence_days,last_bought,days_since,due")
                    .in_("customer_name", chunk).limit(5000).execute().data or [])
            for r in rows:
                out.setdefault(str(r.get("customer_name") or ""), []).append(r)
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


def followups(focus_name: str | None, *, include_holdout: bool = False, force: bool = False) -> dict:
    """The rep's ranked lists. `focus_name` '' / None = every rep (admin only; the route enforces
    it). Cached per (rep, holdout view) for _TTL seconds."""
    name = str(focus_name or "").strip()
    key = (name, bool(include_holdout))
    now = time.monotonic()
    if not force:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    shops = load_shops(name)
    shop_names = [str(s.get("customer_name")) for s in shops if s.get("customer_name")]
    regulars = load_regulars(shop_names)
    contacts = load_contacts(shop_names)
    out = rank_shops(shops, regulars, contacts, include_holdout=include_holdout, names=_display_names())
    out["rep"] = name or None
    with _lock:
        _cache[key] = (now + _TTL, out)
    return out


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

def link_week(sm: dict, days: int = 7) -> dict:
    """The small card: the funnel from the rep's link over `days`, orders and value. Built on
    shop.analytics(days, salesman=sm) — the same scoping the admin analytics page uses."""
    from app import shop
    a = shop.analytics(days, salesman=sm)
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
