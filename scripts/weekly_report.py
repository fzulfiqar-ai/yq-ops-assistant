"""YQ Marketplace weekly management report (HTML email + PDF), read-only.

    python -m scripts.weekly_report                          # last completed Sun-Sat week (Bahrain); files only
    python -m scripts.weekly_report --week-ending 2026-09-26
    python -m scripts.weekly_report --send --to you@x.com --preview    # owner preview copy
    python -m scripts.weekly_report --send --to a@x.com,b@x.com        # after the owner approves

What it reads (one read-only session, conn.read_only = True; nothing is ever written):
  shop_orders / shop_order_lines   marketplace orders placed in the week and their status now
  shop_events                      marketplace traffic (devices, product views, adds, checkouts, searches)
  v_sales                          Focus invoices (B2B Accessories, giveaways excluded) for the sales-impact
                                   section: which marketplace orders reached a Focus invoice, their share of
                                   the week's B2B accessory sales, and the same working days in earlier weeks

The order->invoice match is an ESTIMATE (same rep, invoice dated order day -1..+7, >= 60 % of the order's
SKUs on the invoice, or the order number typed in the invoice narration); only the matched SKUs count.
The report says so, and every edition carries the auto-generated disclaimer.

Output: exports/weekly/<week-end>/ (gitignored: it carries commercial data). Money is BHD, 3 dp.
"""
from __future__ import annotations

import argparse
import base64
import html
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

BH = timezone(timedelta(hours=3))          # Bahrain, no DST
MATCH_MIN_OVERLAP = 0.6
MATCH_DAYS_BEFORE, MATCH_DAYS_AFTER = 1, 7
CHIP_QUERIES = {"instock", "deals", "offer", "offers", "new", "clearance", "best", "bestsellers"}
SENDER = "YQ Marketplace Reports <reports@mail.yqmarketplace.com>"
REPLY_TO = "fzulfiqar@pie-int.com"
ORDER_NO = re.compile(r"YQ-\d{4}-\d{4}", re.I)

# marketplace look (web/src/market/market.css): plum + ink on white, email-safe hexes
PLUM, PLUM_DEEP, PLUM_SOFT = "#6D4091", "#58337A", "#F0EAF6"
INK, MUTED, RULE, PAPER = "#1B1522", "#6B6478", "#E6E0EC", "#F7F5FA"
OK, WARN, BAD = "#2A7954", "#904F0E", "#A8243A"
FONT = "Segoe UI, Arial, Helvetica, sans-serif"


# ── helpers ──────────────────────────────────────────────────────────────────

def D(x) -> Decimal:
    return Decimal(str(x or 0))


def bhd(x) -> str:
    return f"BHD {D(x).quantize(Decimal('0.001'), ROUND_HALF_UP):,}"


def pct(n, d, dp: int = 0) -> str:
    if not d:
        return "–"
    return f"{(Decimal(n) / Decimal(d) * 100).quantize(Decimal(10) ** -dp, ROUND_HALF_UP)}%"


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def day_bh(ts: datetime) -> date:
    return ts.astimezone(BH).date()


def hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


def fmt_age(h: float) -> str:
    return f"{h:.0f} h" if h < 48 else f"{h / 24:.1f} days"


def last_week_ending(today: date) -> date:
    """The most recent Saturday strictly before today (Bahrain week = Sun..Sat)."""
    back = (today.weekday() - 5) % 7 or 7          # Mon=0 .. Sat=5
    return today - timedelta(days=back)


def inv_label(no: str) -> str:
    return re.sub(r"^SI\s*:\s*", "", no or "")


def rep_key(focus_salesman: str | None) -> str:
    return (focus_salesman or "").split(" - ")[0].strip().lower()


# ── data (read-only) ─────────────────────────────────────────────────────────

def load(week_start: date, week_end: date) -> dict:
    import psycopg
    from psycopg.rows import dict_row

    t0 = datetime.combine(week_start, datetime.min.time(), BH)
    t1 = datetime.combine(week_end + timedelta(days=1), datetime.min.time(), BH)
    p0 = t0 - timedelta(days=7)
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as c:
        c.read_only = True
        q = lambda sql, *a: c.execute(sql, a).fetchall()  # noqa: E731
        orders = q("""
            select o.id, o.order_no, o.status, o.total_bhd, o.total_confirmed_bhd, o.order_kind,
                   o.customer_id, o.customer_shop, o.customer_name, o.customer_area, o.created_at,
                   o.confirmed_at, o.delivered_at, o.cancelled_at, o.source, o.salesman_id,
                   s.name as rep, s.focus_name
              from shop_orders o left join salesmen s on s.id = o.salesman_id
             where o.created_at >= %s and o.created_at < %s and not coalesce(o.is_test, false)
             order by o.created_at""", t0, t1)
        ids = [o["id"] for o in orders]
        lines = q("""
            select l.order_id, upper(l.item_code) as code, l.display_name, l.qty, l.line_total_bhd,
                   coalesce(ci.category, 'Other') as category
              from shop_order_lines l left join catalog_items ci on upper(ci.item_code) = upper(l.item_code)
             where l.order_id = any(%s)""", ids) if ids else []
        prev = q("""select count(*) as n, coalesce(sum(total_bhd), 0) as v from shop_orders
                     where created_at >= %s and created_at < %s and not coalesce(is_test, false)
                       and status <> 'cancelled'""", p0, t0)[0]
        open_now = q("""
            select o.order_no, o.status, o.total_bhd, o.created_at, o.confirmed_at, s.name as rep
              from shop_orders o left join salesmen s on s.id = o.salesman_id
             where o.status in ('new', 'confirmed', 'packed', 'out_for_delivery')
               and not coalesce(o.is_test, false)
             order by o.created_at""")
        events = q("""select ts, session_id, device_id, event, referral_code, meta from shop_events
                       where ts >= %s and ts < %s""", t0, t1)
        reps = {r["referral_code"]: r["name"] for r in q("select referral_code, name from salesmen where referral_code is not null")}
        focus_max = q("select max(sale_date) as d from v_sales")[0]["d"]
        base_from = week_start - timedelta(days=28 + 180)
        focus = q("""
            select invoice_no, sale_date, customer_name, is_cash_customer, salesman_raw,
                   upper(coalesce(sku_code, item_name)) as code, quantity, taxable_bhd, gross_bhd, narration
              from v_sales
             where channel = 'B2B' and division = 'Accessories' and not is_giveaway
               and sale_date >= %s and sale_date <= %s""", base_from, week_end + timedelta(days=MATCH_DAYS_AFTER))
    return {"orders": orders, "lines": lines, "prev": prev, "open_now": open_now, "events": events,
            "reps": reps, "focus_max": focus_max, "focus": focus, "t0": t0, "t1": t1}


# ── metrics ──────────────────────────────────────────────────────────────────

def _fold_prefixes(c: Counter) -> Counter:
    """A short stub ('mem') counts toward the full term ('memory') when both were searched; terms under 3 letters are dropped."""
    out = Counter()
    for t, n in c.items():
        if len(t) < 3:
            continue
        longer = [u for u in c if u != t and u.startswith(t)] if len(t) <= 4 else []
        out[max(longer, key=lambda u: c[u]) if longer else t] += n
    return out


def compute(d: dict, week_start: date, week_end: date, now: datetime) -> dict:
    orders, lines, events = d["orders"], d["lines"], d["events"]
    live = [o for o in orders if o["status"] != "cancelled"]
    by_order = defaultdict(list)
    for ln in lines:
        by_order[ln["order_id"]].append(ln)

    m: dict = {"week_start": week_start, "week_end": week_end, "now": now}
    m["n_orders"] = len(orders)
    m["n_live"] = len(live)
    m["value"] = sum((D(o["total_bhd"]) for o in live), Decimal(0))
    m["merchants"] = len({o["customer_id"] or o["customer_shop"] for o in live})
    m["aov"] = (m["value"] / len(live)) if live else Decimal(0)
    m["prev_n"], m["prev_v"] = int(d["prev"]["n"]), D(d["prev"]["v"])
    counts = Counter(o["customer_id"] or o["customer_shop"] for o in live)
    m["repeat_merchants"] = sum(1 for v in counts.values() if v > 1)

    groups = {"Delivered": ("delivered",), "Confirmed / on the way": ("confirmed", "packed", "out_for_delivery"),
              "Waiting for confirmation": ("new",), "Cancelled": ("cancelled",)}
    m["status"] = [(k, [o for o in orders if o["status"] in v]) for k, v in groups.items()]
    conf = [hours(o["created_at"], o["confirmed_at"]) for o in orders if o["confirmed_at"]]
    m["confirm_median_h"] = statistics.median(conf) if conf else None
    m["confirmed_n"] = len(conf)
    m["small"] = [o for o in live if o["order_kind"] == "small"]
    m["test_like"] = [o for o in live if re.search(r"\btest\b", f"{o['customer_shop']} {o['customer_name']}", re.I)]

    # waiting orders as of now (any week) — the operational attention list
    waiting = []
    for o in d["open_now"]:
        if o["status"] == "new":
            age = hours(o["created_at"], now)
            if age >= 24:
                waiting.append({**o, "age_h": age})
    m["waiting"] = waiting

    # reps
    rep_rows = defaultdict(lambda: {"orders": 0, "value": Decimal(0), "delivered": 0, "waiting": 0, "visitors": set()})
    for o in orders:
        r = rep_rows[o["rep"] or "Unassigned"]
        if o["status"] != "cancelled":
            r["orders"] += 1
            r["value"] += D(o["total_bhd"])
        r["delivered"] += o["status"] == "delivered"
        r["waiting"] += o["status"] == "new"

    # traffic (merchant marketplace events)
    ev = events
    dev = lambda kind: {e["device_id"] for e in ev if e["event"] == kind and e["device_id"]}  # noqa: E731
    visitors = dev("view")
    m["visitors"] = len(visitors)
    m["sessions"] = len({e["session_id"] for e in ev if e["event"] == "view" and e["session_id"]})
    order_devices = {o_dev for o_dev in (e["device_id"] for e in ev if e["event"] == "order") if o_dev}
    m["funnel"] = [("Visited the marketplace", len(visitors)), ("Opened a product", len(dev("item"))),
                   ("Added to cart", len(dev("add"))), ("Started checkout", len(dev("checkout_start"))),
                   ("Placed an order", len(order_devices) or m["n_orders"])]
    first_ref: dict = {}
    for e in sorted(ev, key=lambda e: e["ts"]):
        if e["device_id"] and e["device_id"] not in first_ref and e["event"] == "view":
            first_ref[e["device_id"]] = e["referral_code"]
    via_link = {k for k, v in first_ref.items() if v}
    m["via_link"], m["direct"] = len(via_link), len(first_ref) - len(via_link)
    m["orders_via_link"] = sum(1 for o in orders if o["salesman_id"])
    for devc, ref in first_ref.items():
        if ref:
            rep_rows[d["reps"].get(ref, ref)]["visitors"].add(devc)
    m["reps"] = sorted(((k, v["orders"], v["value"], v["delivered"], v["waiting"], len(v["visitors"]))
                        for k, v in rep_rows.items()), key=lambda r: (-r[2], -r[5], r[0]))

    days = []
    for i in range((week_end - week_start).days + 1):
        dd = week_start + timedelta(days=i)
        v = {e["device_id"] for e in ev if e["event"] == "view" and e["device_id"] and day_bh(e["ts"]) == dd}
        od = [o for o in live if day_bh(o["created_at"]) == dd]
        days.append((dd, len(v), len(od), sum((D(o["total_bhd"]) for o in od), Decimal(0))))
    m["days"] = days
    active = [x for x in days if x[1]]
    m["traffic_first"], m["traffic_last"] = (active[0][1], active[-1][1]) if active else (0, 0)

    # demand: search is logged per keystroke, so a query followed within 30 s on the same device by a
    # longer one that starts with it ("san" -> "sandisk") is typing in progress, not a search
    typed = defaultdict(list)
    for e in ev:
        if e["event"] in ("search", "search_zero") and e["device_id"]:
            qv = re.sub(r"[^a-z0-9 ]+", "", ((e["meta"] or {}).get("q") or "").lower()).strip()
            if len(qv) >= 2 and qv not in CHIP_QUERIES:
                typed[e["device_id"]].append((e["ts"], qv, e["event"]))
    searches, zero = Counter(), Counter()
    for seq in typed.values():
        seq.sort()
        for k, (ts, qv, kind) in enumerate(seq):
            nxt = seq[k + 1] if k + 1 < len(seq) else None
            if nxt and (nxt[0] - ts).total_seconds() <= 30 and nxt[1] != qv and nxt[1].startswith(qv):
                continue
            (zero if kind == "search_zero" else searches)[qv] += 1
    m["top_searches"] = _fold_prefixes(searches).most_common(6)
    m["zero_searches"] = _fold_prefixes(zero).most_common(6)

    prod = defaultdict(lambda: {"name": "", "units": 0, "orders": set(), "value": Decimal(0)})
    cat = defaultdict(lambda: Decimal(0))
    live_ids = {o["id"] for o in live}
    for ln in lines:
        if ln["order_id"] not in live_ids:
            continue
        p = prod[ln["code"]]
        p["name"] = p["name"] or ln["display_name"]
        p["units"] += int(ln["qty"] or 0)
        p["orders"].add(ln["order_id"])
        p["value"] += D(ln["line_total_bhd"])
        cat[(ln["category"] or "Other").title()] += D(ln["line_total_bhd"])
    m["products"] = sorted(((k, v["name"], v["units"], len(v["orders"]), v["value"]) for k, v in prod.items()),
                           key=lambda r: (-r[4], -r[2]))[:8]
    m["categories"] = sorted(cat.items(), key=lambda kv: -kv[1])[:6]
    shops = defaultdict(lambda: {"area": "", "orders": 0, "value": Decimal(0)})
    for o in live:
        s = shops[o["customer_shop"] or o["customer_name"] or "—"]
        s["area"] = s["area"] or (o["customer_area"] or "")
        s["orders"] += 1
        s["value"] += D(o["total_bhd"])
    m["shops"] = sorted(((k, v["area"], v["orders"], v["value"]) for k, v in shops.items()), key=lambda r: -r[3])[:5]

    m["impact"] = sales_impact(d, live, by_order, week_start, week_end)
    invoiced = {mt["order"]["order_no"]: inv_label(mt["invoice"]) for mt in m["impact"]["matches"].values()}
    for w in m["waiting"]:
        w["focus"] = invoiced.get(w["order_no"])
    return m


def sales_impact(d: dict, live: list, by_order: dict, week_start: date, week_end: date) -> dict:
    """Which marketplace orders reached a Focus invoice, and what that is worth against the week."""
    focus, fmax = d["focus"], d["focus_max"]
    inv = defaultdict(lambda: {"date": None, "rep": "", "customer": "", "cash": False, "narr": "", "lines": []})
    for r in focus:
        i = inv[r["invoice_no"]]
        i["date"], i["rep"], i["customer"], i["cash"] = r["sale_date"], rep_key(r["salesman_raw"]), r["customer_name"], bool(r["is_cash_customer"])
        i["narr"] = i["narr"] or (r["narration"] or "")
        i["lines"].append(r)

    # an invoice line's units can be claimed once: two orders asking for the same 6 units cannot both
    # be "on" an invoice that carries them once (earliest order first; the note-typed order number wins)
    left = {no: Counter() for no in inv}
    for no, i in inv.items():
        for ln in i["lines"]:
            left[no][ln["code"]] += int(ln["quantity"] or 0)
    matches = {}
    for o in sorted(live, key=lambda o: o["created_at"]):
        want = Counter()
        for ln in by_order.get(o["id"], []):
            want[ln["code"]] += int(ln["qty"] or 0)
        if not want:
            continue
        od = day_bh(o["created_at"])
        best = None
        for no, i in inv.items():
            if not (od - timedelta(days=MATCH_DAYS_BEFORE) <= i["date"] <= od + timedelta(days=MATCH_DAYS_AFTER)):
                continue
            if o["order_no"] and o["order_no"].upper() in {x.upper() for x in ORDER_NO.findall(i["narr"])}:
                best = (9.0, no, "order number in invoice note", 1.0)
                break
            if rep_key(o["focus_name"] or o["rep"]) != i["rep"]:
                continue
            hit = [c for c in want if left[no][c] > 0]
            ov = len(hit) / len(want)
            exact = sum(1 for c in hit if left[no][c] == want[c]) / len(want)
            # a 1-2 product order proves little by overlap alone: its quantities must match exactly
            if ov < MATCH_MIN_OVERLAP or (len(want) <= 2 and exact < 1):
                continue
            days = (i["date"] - od).days
            score = ov + 0.5 * exact - 0.03 * abs(days) - (0.1 if days < 0 else 0)
            if best is None or score > best[0]:
                best = (score, no, "same rep, date and products", ov)
        if best:
            no = best[1]
            codes = {c for c in want if left[no][c] > 0}
            for c in codes:
                left[no][c] -= min(want[c], left[no][c])
            matches[o["id"]] = {"order": o, "invoice": no, "overlap": best[3], "how": best[2], "codes": codes}

    # matched value = the invoice lines whose SKU the linked orders asked for (conservative)
    per_inv_codes = defaultdict(set)
    for mt in matches.values():
        per_inv_codes[mt["invoice"]] |= mt["codes"]
    matched_taxable = Decimal(0)
    matched_gross = Decimal(0)
    customers = {}
    for no, codes in per_inv_codes.items():
        i = inv[no]
        for ln in i["lines"]:
            if ln["code"] in codes:
                matched_taxable += D(ln["taxable_bhd"])
                matched_gross += D(ln["gross_bhd"])
        customers[no] = (i["customer"], i["cash"], i["date"])

    # the week's B2B accessory sales on the days Focus covers, and the same weekdays in the 4 weeks before
    cover_end = min(week_end, fmax) if fmax else None
    week_taxable = Decimal(0)
    week_invoices = set()
    base = []
    if cover_end and cover_end >= week_start:
        for r in focus:
            if week_start <= r["sale_date"] <= cover_end:
                week_taxable += D(r["taxable_bhd"])
                week_invoices.add(r["invoice_no"])
        for k in range(1, 5):
            a, b = week_start - timedelta(weeks=k), cover_end - timedelta(weeks=k)
            base.append(sum((D(r["taxable_bhd"]) for r in focus if a <= r["sale_date"] <= b), Decimal(0)))
    base_avg = (sum(base, Decimal(0)) / len(base)) if base else None

    # were the matched shops already buying? (named Focus account with a B2B accessory invoice in the 180 days before)
    seen_before = {r["customer_name"] for r in focus if r["sale_date"] < week_start and not r["is_cash_customer"]}
    existing = sum(1 for (cust, cash, _) in customers.values() if not cash and cust in seen_before)
    new_named = sum(1 for (cust, cash, _) in customers.values() if not cash and cust not in seen_before)
    walk_in = sum(1 for (_, cash, _) in customers.values() if cash)
    orders_after_cover = sum(1 for o in live if fmax and day_bh(o["created_at"]) > fmax)
    return {"focus_max": fmax, "cover_end": cover_end, "matches": matches, "invoices": len(per_inv_codes),
            "matched_taxable": matched_taxable, "matched_gross": matched_gross, "week_taxable": week_taxable,
            "week_invoices": len(week_invoices), "base": base, "base_avg": base_avg, "existing": existing,
            "new_named": new_named, "walk_in": walk_in, "orders_after_cover": orders_after_cover,
            "n_live": len(live)}


# ── words ────────────────────────────────────────────────────────────────────

def highlights(m: dict) -> list[str]:
    out = []
    launch = m["prev_n"] == 0
    head = f"<b>{m['n_live']} orders</b> worth <b>{bhd(m['value'])}</b> from <b>{m['merchants']} shops</b>"
    if launch:
        out.append(f"Launch week: {head}. There is no earlier week to compare with yet.")
    else:
        out.append(f"{head} ({pct(m['n_live'] - m['prev_n'], m['prev_n'])} orders vs last week).")
    delivered = dict(m["status"])["Delivered"]
    out.append(f"{len(delivered)} of {m['n_orders']} orders are already delivered "
               f"({bhd(sum((D(o['total_bhd']) for o in delivered), Decimal(0)))}).")
    imp = m["impact"]
    if imp["matched_taxable"] and imp["week_taxable"]:
        out.append(f"About <b>{pct(imp['matched_taxable'], imp['week_taxable'])}</b> of B2B accessory sales invoiced in Focus "
                   f"on the days Focus data covers came through the marketplace (estimate, see Sales impact).")
    if m["visitors"]:
        out.append(f"{m['visitors']} phones or computers visited; {pct(m['funnel'][-1][1], m['visitors'], 1)} of them placed an order. "
                   f"{pct(m['via_link'], m['via_link'] + m['direct'])} arrived through a salesman's link.")
    if m["traffic_first"] and m["traffic_last"] < m["traffic_first"] * 0.5:
        out.append(f"Visits fell from {m['traffic_first']} on the first active day to {m['traffic_last']} on the last one: "
                   f"launch interest faded and needs re-sharing.")
    repeated = [t for t, n in m["zero_searches"] if n >= 2]
    if repeated:
        terms = ", ".join(f"“{esc(t)}”" for t in repeated[:4])
        out.append(f"Shops searched for products we do not list: {terms}.")
    return out


def actions(m: dict) -> list[str]:
    out = []
    if m["waiting"]:
        by = Counter(w["rep"] or "Unassigned" for w in m["waiting"])
        who = ", ".join(f"{k} ({v})" for k, v in by.most_common())
        done = [w["order_no"] for w in m["waiting"] if w.get("focus")]
        extra = (f" {len(done)} of them ({', '.join(done)}) already look invoiced in Focus, so they only need closing in the marketplace."
                 if done else "")
        out.append(f"<b>Clear the {len(m['waiting'])} orders waiting over 24 hours</b> ({who}): confirm, adjust or cancel each.{extra} "
                   f"Order alerts now reach salesmen by email (enabled 27-Sep).")
    if m["traffic_first"] and m["traffic_last"] < m["traffic_first"] * 0.5:
        out.append("<b>Ask every salesman to re-share his marketplace link</b> with his shops this week (WhatsApp status and groups).")
    if m["small"] and len(m["small"]) / max(m["n_live"], 1) >= 0.3:
        out.append(f"<b>Decide the small-order rule:</b> {len(m['small'])} of {m['n_live']} orders were below the BHD 20 minimum "
                   f"({bhd(sum((D(o['total_bhd']) for o in m['small']), Decimal(0)))} in total).")
    if m["zero_searches"]:
        out.append("<b>Review unmet demand</b> (searches with no result) with the sourcing team.")
    silent = [r[0] for r in m["reps"] if r[5] >= 10 and r[1] == 0]
    if silent:
        out.append(f"<b>Links with visits but no orders:</b> {', '.join(esc(s) for s in silent[:5])}: a quick follow-up call with those shops.")
    return out[:5]


# ── HTML (Outlook-safe: tables + inline styles, no images) ──────────────────

def _tile(label: str, value: str, sub: str = "") -> str:
    return (f'<td width="33%" valign="top" style="padding:6px;"><table width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{PAPER};border:1px solid {RULE};border-radius:8px;"><tr><td style="padding:12px 14px;">'
            f'<div style="font:600 11px {FONT};letter-spacing:.4px;color:{MUTED};text-transform:uppercase;">{esc(label)}</div>'
            f'<div style="font:700 21px {FONT};color:{INK};padding-top:4px;">{value}</div>'
            f'<div style="font:400 12px {FONT};color:{MUTED};padding-top:2px;">{sub}</div></td></tr></table></td>')


def _bar(frac: float, color: str = PLUM) -> str:
    w = max(0, min(100, round(frac * 100)))
    if w == 0:
        return f'<table width="100%" cellpadding="0" cellspacing="0"><tr><td style="height:10px;background:{RULE};font-size:0;">&nbsp;</td></tr></table>'
    rest = f'<td width="{100 - w}%" style="height:10px;background:{RULE};font-size:0;">&nbsp;</td>' if w < 100 else ""
    return (f'<table width="100%" cellpadding="0" cellspacing="0"><tr><td width="{w}%" style="height:10px;background:{color};'
            f'font-size:0;">&nbsp;</td>{rest}</tr></table>')


def _h2(t: str, sub: str = "") -> str:
    s = f'<div style="font:400 12px {FONT};color:{MUTED};padding-top:2px;">{sub}</div>' if sub else ""
    return (f'<tr><td style="padding:22px 24px 8px;"><div style="font:700 15px {FONT};color:{PLUM_DEEP};">{esc(t)}</div>{s}</td></tr>')


def _table(head: list[str], rows: list[list[str]], align: list[str]) -> str:
    th = "".join(f'<td align="{a}"{' width="150"' if not h else ""} style="padding:6px 8px;font:600 11px {FONT};color:{MUTED};text-transform:uppercase;'
                 f'border-bottom:1px solid {RULE};">{esc(h)}</td>' for h, a in zip(head, align))
    tr = "".join("<tr>" + "".join(f'<td align="{a}" valign="middle" style="padding:7px 8px;font:400 13px {FONT};color:{INK};'
                                  f'border-bottom:1px solid {RULE};">{c}</td>' for c, a in zip(r, align)) + "</tr>" for r in rows)
    return (f'<tr><td style="padding:0 24px;"><table width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;">{th and "<tr>" + th + "</tr>"}{tr}</table></td></tr>')


def _para(items: list[str], color: str = INK, bullet: str = "•") -> str:
    lis = "".join(f'<tr><td valign="top" width="16" style="font:400 14px {FONT};color:{PLUM};padding:3px 0;">{bullet}</td>'
                  f'<td style="font:400 14px/1.5 {FONT};color:{color};padding:3px 0;">{t}</td></tr>' for t in items)
    return f'<tr><td style="padding:0 24px;"><table width="100%" cellpadding="0" cellspacing="0">{lis}</table></td></tr>'


def render(m: dict, preview: bool) -> str:
    ws, we = m["week_start"], m["week_end"]
    period = f"{ws:%a %d %b} – {we:%a %d %b %Y}"
    gen = m["now"].astimezone(BH)
    imp = m["impact"]
    rows = []

    banner = ""
    if preview:
        banner = (f'<tr><td style="background:#FFF4E5;padding:10px 24px;font:600 12px {FONT};color:{WARN};">'
                  f'PREVIEW for approval — not yet sent to management.</td></tr>')
    rows.append(banner)
    rows.append(f'<tr><td style="background:{PLUM};padding:22px 24px;">'
                f'<div style="font:600 12px {FONT};letter-spacing:1px;color:#E9DDF6;text-transform:uppercase;">YQ Bahrain · YQ Marketplace</div>'
                f'<div style="font:700 22px {FONT};color:#FFFFFF;padding-top:6px;">Weekly report</div>'
                f'<div style="font:400 14px {FONT};color:#F3ECFA;padding-top:4px;">{esc(period)}'
                f'{" · launch week" if m["prev_n"] == 0 else ""}</div></td></tr>')
    rows.append(f'<tr><td style="background:{PLUM_SOFT};padding:9px 24px;font:400 12px {FONT};color:{PLUM_DEEP};">'
                f'Auto-generated from the YQ Marketplace system and Focus ERP exports. Please cross-check key figures before acting on '
                f'them or sharing them. Values include VAT (10%) unless marked ex-VAT.</td></tr>')

    # KPI tiles
    conv = pct(m["funnel"][-1][1], m["visitors"], 1)
    wow = "" if m["prev_n"] == 0 else f"{pct(m['n_live'] - m['prev_n'], m['prev_n'])} vs last week"
    rows.append('<tr><td style="padding:16px 18px 4px;"><table width="100%" cellpadding="0" cellspacing="0"><tr>'
                + _tile("Orders", str(m["n_live"]), wow or f"{m['n_orders'] - m['n_live']} cancelled")
                + _tile("Order value", bhd(m["value"]), f"avg {bhd(m['aov'])}")
                + _tile("Shops ordering", str(m["merchants"]), f"{m['repeat_merchants']} ordered twice or more")
                + '</tr><tr>'
                + _tile("Visitors", str(m["visitors"]), f"{m['sessions']} visits")
                + _tile("Visit → order", conv, "share of visitors who ordered")
                + _tile("Median time to confirm", fmt_age(m["confirm_median_h"]) if m["confirm_median_h"] is not None else "–",
                        f"{m['confirmed_n']} orders confirmed")
                + '</tr></table></td></tr>')

    rows.append(_h2("Highlights"))
    rows.append(_para(highlights(m)))

    acts = actions(m)
    if acts:
        rows.append(_h2("Needs attention and suggested actions", "Suggestions for management review — not automatic decisions."))
        rows.append(_para(acts, bullet="→"))

    # sales impact
    rows.append(_h2("Sales impact: did the marketplace bring in sales?",
                    f"Focus ERP data runs to {imp['focus_max']:%a %d %b}." if imp["focus_max"] else "No Focus data loaded."))
    if imp["cover_end"] and imp["week_taxable"]:
        base_txt = "no earlier weeks to compare with"
        if imp["base"]:
            lo, hi = min(imp["base"]), max(imp["base"])
            rank = 1 + sum(1 for b in imp["base"] if b > imp["week_taxable"])
            place = "the highest" if rank == 1 else f"number {rank}"
            base_txt = (f"{bhd(imp['week_taxable'])} ex-VAT, {place} of the last {len(imp['base']) + 1} weeks for the same days "
                        f"(previous 4 weeks: {bhd(lo)} to {bhd(hi)}, average {bhd(imp['base_avg'])}). Weekly sales swing a lot, "
                        f"so treat this as context, not proof")
        mix = []
        if imp["existing"]:
            mix.append(f"{imp['existing']} existing Focus customer invoice{'s' if imp['existing'] != 1 else ''}")
        if imp["new_named"]:
            mix.append(f"{imp['new_named']} named customer{'s' if imp['new_named'] != 1 else ''} with no B2B accessory invoice in the previous 6 months")
        if imp["walk_in"]:
            mix.append(f"{imp['walk_in']} invoiced to Cash Customer (the shop is not named in Focus)")
        facts = [
            f"<b>{len(imp['matches'])} of {imp['n_live']} marketplace orders</b> were found on <b>{imp['invoices']} Focus invoices</b>, worth "
            f"<b>{bhd(imp['matched_taxable'])} ex-VAT</b> ({bhd(imp['matched_gross'])} incl. VAT, matched products only).",
            f"That is about <b>{pct(imp['matched_taxable'], imp['week_taxable'])}</b> of all B2B accessory sales invoiced "
            f"{m['week_start']:%d}–{imp['cover_end']:%d %b} ({bhd(imp['week_taxable'])} ex-VAT, {imp['week_invoices']} invoices).",
            f"B2B accessory sales on those days: {base_txt}.",
        ]
        if mix:
            facts.append("Who the matched invoices went to: " + "; ".join(mix) + ".")
        if imp["orders_after_cover"]:
            n = imp["orders_after_cover"]
            facts.append(f"{n} order{'s were' if n != 1 else ' was'} placed after the last Focus data and cannot be checked yet.")
        rows.append(_para(facts))
        rows.append(f'<tr><td style="padding:6px 24px 0;font:400 12px/1.5 {FONT};color:{MUTED};">How to read this: orders are matched to '
                    f'invoices automatically (same salesman, invoice dated within {MATCH_DAYS_BEFORE} day before to {MATCH_DAYS_AFTER} days after the order, '
                    f'and at least {int(MATCH_MIN_OVERLAP * 100)}% of the ordered products on the invoice). It is an estimate. Some of this business '
                    f'would probably have come through the salesmen anyway; the clearest gain is new shops and orders placed without a visit. '
                    f'A fair verdict needs 4–6 weeks of data.</td></tr>')
    else:
        rows.append(_para(["Focus data for this week is not loaded yet, so the sales impact cannot be measured this time."]))

    # order status
    rows.append(_h2("Where this week's orders stand now"))
    tot = max(m["n_orders"], 1)
    st_rows = []
    for label, lst in m["status"]:
        color = {"Delivered": OK, "Waiting for confirmation": WARN, "Cancelled": MUTED}.get(label, PLUM)
        st_rows.append([esc(label), str(len(lst)), bhd(sum((D(o["total_bhd"]) for o in lst), Decimal(0))), _bar(len(lst) / tot, color)])
    rows.append(_table(["Status", "Orders", "Value", ""], st_rows, ["left", "right", "right", "left"]))
    if m["waiting"]:
        rows.append(_h2("Orders waiting over 24 hours (as of this report)"))
        rows.append(_table(["Order", "Salesman", "Value", "Waiting", "In Focus?"],
                           [[esc(w["order_no"]), esc(w["rep"] or "Unassigned"), bhd(w["total_bhd"]), fmt_age(w["age_h"]),
                             f'<span style="color:{OK};">Looks invoiced ({esc(w["focus"])})</span>' if w.get("focus") else "Not found"]
                            for w in m["waiting"]],
                           ["left", "left", "right", "right", "left"]))

    # traffic
    rows.append(_h2("Traffic", "Unique phones or computers per day (visitors) and orders placed."))
    peak = max((x[1] for x in m["days"]), default=0) or 1
    rows.append(_table(["Day", "Visitors", "", "Orders", "Value"],
                       [[f"{dd:%a %d %b}", str(v), _bar(v / peak), str(n), bhd(val) if n else "–"] for dd, v, n, val in m["days"]],
                       ["left", "right", "left", "right", "right"]))
    top = m["funnel"][0][1] or 1
    rows.append(_h2("From visit to order", "Unique visitors reaching each step."))
    rows.append(_table(["Step", "Visitors", "", "Share"],
                       [[esc(k), str(v), _bar(v / top), pct(v, top)] for k, v in m["funnel"]], ["left", "right", "left", "right"]))

    # salesmen
    rep_rows = [[esc(r[0]), str(r[5]), str(r[1]), bhd(r[2]) if r[1] else "–", str(r[3]), str(r[4]) if r[4] else "–"]
                for r in m["reps"] if r[1] or r[5] or r[4]]
    if rep_rows:
        rows.append(_h2("Salesmen", f"{pct(m['via_link'], m['via_link'] + m['direct'])} of visitors came through a salesman's link; "
                                    f"{m['orders_via_link']} of {m['n_orders']} orders are credited to a salesman."))
        rows.append(_table(["Salesman", "Link visitors", "Orders", "Value", "Delivered", "Waiting"], rep_rows,
                           ["left", "right", "right", "right", "right", "right"]))

    # products + categories
    if m["products"]:
        rows.append(_h2("Most ordered products"))
        rows.append(_table(["Code", "Product", "Units", "Orders", "Value"],
                           [[f"<b>{esc(c)}</b>", esc((n or "")[:48]), str(u), str(o), bhd(v)] for c, n, u, o, v in m["products"]],
                           ["left", "left", "right", "right", "right"]))
    if m["categories"]:
        cmax = m["categories"][0][1] or 1
        rows.append(_h2("Order value by category"))
        rows.append(_table(["Category", "", "Value"], [[esc(k), _bar(float(v / cmax)), bhd(v)] for k, v in m["categories"]],
                           ["left", "left", "right"]))

    # merchants + demand
    rows.append(_h2("Shops"))
    shop_notes = [f"{m['merchants']} shops ordered; {m['repeat_merchants']} of them more than once.",
                  f"{len(m['small'])} orders ({pct(len(m['small']), m['n_live'])}) were below the BHD 20 minimum order."]
    if m["test_like"]:
        shop_notes.append(f"{len(m['test_like'])} order(s) worth {bhd(sum((D(o['total_bhd']) for o in m['test_like']), Decimal(0)))} "
                          f"have 'test' in the shop name and are included until the salesmen confirm.")
    rows.append(_para(shop_notes))
    if m["shops"]:
        rows.append(_table(["Top shops by value", "Area", "Orders", "Value"],
                           [[esc(s), esc(a), str(o), bhd(v)] for s, a, o, v in m["shops"]], ["left", "left", "right", "right"]))
    if m["top_searches"] or m["zero_searches"]:
        rows.append(_h2("What shops searched for"))
        dem = []
        if m["top_searches"]:
            dem.append("Most searched: " + ", ".join(f"{esc(t)} ({n})" for t, n in m["top_searches"]))
        if m["zero_searches"]:
            dem.append("<b>No result found</b> (possible products to source): " + ", ".join(f"{esc(t)} ({n})" for t, n in m["zero_searches"]))
        rows.append(_para(dem))

    rows.append(f'<tr><td style="padding:24px 24px 8px;"><table width="100%" cellpadding="0" cellspacing="0" '
                f'style="border-top:1px solid {RULE};"><tr><td style="padding-top:12px;font:400 11px/1.6 {FONT};color:{MUTED};">'
                f'<b>Disclaimer:</b> this report is generated automatically from the YQ Marketplace system and Focus ERP exports. '
                f'Figures can change as orders are confirmed, adjusted, cancelled or invoiced, and the sales-impact figures are estimates '
                f'from automatic matching. Please cross-check key figures with the portal and Focus before making decisions or sharing '
                f'outside YQ. Marketplace order values are as requested by the shop (incl. VAT); Focus figures are ex-VAT unless stated.'
                f'<br>Generated {gen:%a %d %b %Y, %H:%M} (Bahrain). Internal to YQ Bahrain W.L.L.</td></tr></table></td></tr>')

    body = "".join(rows)
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<title>YQ Marketplace weekly report {esc(period)}</title></head>'
            f'<body style="margin:0;padding:0;background:#EFEBF3;">'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="background:#EFEBF3;"><tr><td align="center" style="padding:20px 8px;">'
            f'<table width="640" cellpadding="0" cellspacing="0" style="width:640px;max-width:640px;background:#FFFFFF;'
            f'border:1px solid {RULE};">{body}</table></td></tr></table></body></html>')


# ── output + send ────────────────────────────────────────────────────────────

def to_pdf(html_text: str, path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content(html_text, wait_until="load")
        pg.pdf(path=str(path), format="A4", print_background=True, margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"})
        b.close()


def resend_key() -> str:
    k = os.getenv("RESEND_API_KEY", "")
    if k or not os.getenv("RENDER_API_KEY"):
        return k
    import httpx  # owner PC: read the key from the Render service (GET only)

    r = httpx.get("https://api.render.com/v1/services/srv-da20eavlk1mc73agsbk0/env-vars/RESEND_API_KEY",
                  headers={"Authorization": "Bearer " + os.environ["RENDER_API_KEY"], "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    return r.json()["value"]


def send(html_text: str, pdf: Path, subject: str, to: list[str]) -> dict:
    import httpx

    body = {"from": SENDER, "to": to, "reply_to": REPLY_TO, "subject": subject, "html": html_text,
            "attachments": [{"filename": pdf.name, "content": base64.b64encode(pdf.read_bytes()).decode()}]}
    r = httpx.post("https://api.resend.com/emails", headers={"Authorization": "Bearer " + resend_key()}, json=body, timeout=60)
    return {"status": r.status_code, "body": r.json() if r.content else {}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week-ending", help="Saturday that ends the week (YYYY-MM-DD); default: the last completed week")
    ap.add_argument("--send", action="store_true", help="email the report (otherwise files only)")
    ap.add_argument("--to", default="", help="comma-separated recipients (required with --send)")
    ap.add_argument("--preview", action="store_true", help="mark the edition as a preview for approval")
    a = ap.parse_args()

    now = datetime.now(timezone.utc)
    we = date.fromisoformat(a.week_ending) if a.week_ending else last_week_ending(now.astimezone(BH).date())
    ws = we - timedelta(days=6)
    m = compute(load(ws, we), ws, we, now)
    html_text = render(m, a.preview)

    out = ROOT / "exports" / "weekly" / we.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.html").write_text(html_text, encoding="utf-8")
    pdf = out / f"YQ_Marketplace_Weekly_{ws:%Y-%m-%d}_to_{we:%Y-%m-%d}.pdf"
    to_pdf(html_text, pdf)
    print(f"wrote {out / 'report.html'} and {pdf.name}")
    print(f"orders {m['n_live']} (+{m['n_orders'] - m['n_live']} cancelled)  value {bhd(m['value'])}  shops {m['merchants']}  "
          f"visitors {m['visitors']}  matched {len(m['impact']['matches'])} orders / {m['impact']['invoices']} invoices "
          f"{bhd(m['impact']['matched_taxable'])} ex-VAT of {bhd(m['impact']['week_taxable'])}")

    if a.send:
        to = [x.strip() for x in a.to.split(",") if x.strip()]
        if not to:
            print("--send needs --to", file=sys.stderr)
            return 2
        subject = f"{'[Preview] ' if a.preview else ''}YQ Marketplace weekly report · {ws:%d %b} – {we:%d %b %Y}"
        res = send(html_text, pdf, subject, to)
        print("send:", res)
        return 0 if res["status"] in (200, 201) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
