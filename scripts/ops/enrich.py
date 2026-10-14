"""What the delivery adds to the full brief (scripts/ops/deliver.py): the private rep table, the items to
push, the movers, receivables, waiting orders, the shops due a re-order, the discounts and the shape of
the stock. The figures of the chat screen are worked out by sales.full_brief and are not repeated here;
this module only ADDS figures, from the rows the overview already read (sales.read_overview) and from
two small reads of its own, both through scripts.ops.db as the SELECT-only role:

  * the shops due a re-order: app.followups' own SQL and ranking (the salesman app's Today list),
    so the e-mail names the same shops the reps see; the app's holdout shops stay out;
  * the month's discounts: v_sales.discount_bhd. The Focus export carries no discount values (it was
    nought on every line when this was written), so the e-mail says NOT EVIDENCED until it does.

Every money figure is a three-decimal string (fmt.q3), as in brief.json. Rep names, customer names and
the shops are for the owner's e-mail and the files under exports/ops (git-ignored): views.team_text never
reads this module's names except the reps it praises (views.py checks that)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from scripts.ops import fmt

PUSH_MIN_UNITS = 3          # an item counts as selling faster with at least this many units in its last 30 days
PUSH_MIN_COVER_DAYS = 14    # ... and stock for at least this many days at its 90-day pace
TOP_ROWS = 5                # items and accounts listed per section of the e-mail
TOP_SHOPS = 10              # shops due a re-order listed by name

DISCOUNT_SQL = (
    "SELECT COUNT(*) FILTER (WHERE COALESCE(v.discount_bhd, 0) <> 0) AS lines, "
    "ROUND(COALESCE(SUM(v.discount_bhd), 0)::numeric, 3) AS bhd, "
    "ROUND(COALESCE(SUM(v.net_bhd), 0)::numeric, 3) AS net_bhd FROM v_sales v "
    "WHERE v.division NOT IN ('SIM', 'Devices') AND NOT v.is_giveaway "
    "AND v.sale_date BETWEEN $1::date AND $2::date"
)


def _q(x) -> str:
    return str(fmt.q3(x))


def _d(x) -> Decimal:
    return fmt.dec(x)


def _tile(payload: dict | None, key: str) -> dict | None:
    for mod in (payload or {}).get("modules") or []:
        for t in mod.get("tiles") or []:
            if t.get("key") == key:
                return t if t.get("available") else None
    return None


def tier_one(row: dict) -> Decimal | None:
    """The rep's first tier (his monthly target) from an attainment row, or None."""
    tier = row.get("tier") if isinstance(row.get("tier"), dict) else {}
    for t in tier.get("tiers") or []:
        if isinstance(t, dict) and int(t.get("n") or 0) == 1 and _d(t.get("bhd")) > 0:
            return _d(t.get("bhd"))
    return None


def team_rows(attainment: list | None, team_tile: dict | None, dim_rows: list | None, d: date) -> list[dict]:
    """One row per rep on the target sheet who is switched on: his month to date (the portal's own figure,
    the one the Command Centre shows), the same business days last month, invoices, shops, days since his
    last invoice, and his target (tier 1) with the share reached and what a business day must bring. The
    kickback figures of the attainment rows are left out on purpose."""
    from app import metrics
    silent = {str(r.get("salesman")): r.get("business_days_since_invoice")
              for r in (team_tile or {}).get("rows") or []}
    cmp_rows = [r for r in (dim_rows or []) if r.get("k") == "cmp" and r.get("dim") == "salesman"]
    out = []
    for a in attainment or []:
        rep = str(a.get("salesman") or "")
        if not rep or a.get("no_target") or a.get("is_active") is False:
            continue
        mtd = _d(a.get("net_bhd"))
        cmp_ = sum((_d(r.get("net_bhd")) for r in cmp_rows if metrics.rep_matches(r.get("name"), rep)), Decimal(0))
        target = tier_one(a)
        pace = metrics.month_pace(mtd, d, target) if target else None
        change = None
        if cmp_rows and cmp_ > 0:
            change = float(round((mtd - cmp_) / cmp_ * 100))
        out.append({
            "salesman": rep, "name": str(a.get("name") or rep), "mtd_bhd": _q(mtd),
            "same_days_last_month_bhd": _q(cmp_) if cmp_rows else None, "change_pct": change,
            "invoices": int(a.get("invoices") or 0), "shops": int(a.get("shops") or 0),
            "last_invoice": a.get("last_sale"), "business_days_since_invoice": silent.get(rep),
            "target_bhd": _q(target) if target else None,
            "achieved_pct": float(round(mtd / target * 100, 1)) if target else None,
            "needed_per_business_day_bhd": _q(pace["needed_per_business_day_bhd"]) if pace else None,
            "business_days_left": pace["business_days_left"] if pace else None,
        })
    out.sort(key=lambda r: (-_d(r["mtd_bhd"]), r["name"]))
    return out


def push_items(movers: list | None, stock: list | None) -> list[dict]:
    """Items selling faster (more units in the last 30 days than in the 30 before, at least
    PUSH_MIN_UNITS) that the main store holds for at least PUSH_MIN_COVER_DAYS at their pace: what a rep
    can offer at every visit today without promising stock that is not there."""
    held = {}
    for s in stock or []:
        if str(s.get("division") or "Accessories") != "Accessories":
            continue
        held[str(s.get("item_name") or "")] = s
    out = []
    for m in movers or []:
        name = str(m.get("item_name") or "")
        q30, qprev = _d(m.get("qty_30")), _d(m.get("qty_prev"))
        s = held.get(name)
        if not name or s is None or q30 < PUSH_MIN_UNITS or q30 <= qprev:
            continue
        on_hand = _d(s.get("current_stock"))
        cover = s.get("days_cover")
        enough = (_d(cover) >= PUSH_MIN_COVER_DAYS) if cover is not None else on_hand >= q30 / 2
        if on_hand <= 0 or not enough:
            continue
        out.append({"item": name, "units_30_days": int(q30), "units_30_before": int(qprev),
                    "bhd_30_days": _q(m.get("net_30")), "rise_bhd": _q(_d(m.get("net_30")) - _d(m.get("net_prev"))),
                    "in_stock": int(on_hand), "days_cover": int(_d(cover)) if cover is not None else None})
    out.sort(key=lambda r: (-_d(r["rise_bhd"]), r["item"]))
    return out[:TOP_ROWS]


def receivables(payload: dict | None) -> dict | None:
    total, over, top = _tile(payload, "ar.total"), _tile(payload, "ar.over90"), _tile(payload, "ar.top_overdue")
    if total is None:
        return None
    return {"total_bhd": _q(total.get("value")), "accounts": total.get("accounts"),
            "over_90_pct": over.get("value") if over else None,
            "over_90_bhd": _q(over.get("over_90_bhd")) if over else None,
            "top_overdue": [{"account": str(x.get("account") or ""), "overdue_bhd": _q(x.get("overdue_bhd")),
                             "over_90_bhd": _q(x.get("over_90_bhd")), "last_receipt": x.get("last_receipt")}
                            for x in (top or {}).get("items") or []][:TOP_ROWS]}


def waiting_orders(payload: dict | None) -> dict | None:
    t = _tile(payload, "orders.waiting")
    if t is None:
        return None
    return {"orders": int(t.get("value") or 0), "bhd": _q(t.get("bhd")), "oldest_hours": t.get("oldest_hours")}


def movers(payload: dict | None) -> dict | None:
    t = _tile(payload, "products.movers")
    if t is None:
        return None

    def rows(key: str) -> list[dict]:
        return [{"item": str(x.get("item") or ""), "bhd_30_days": _q(x.get("net_30_bhd")),
                 "bhd_30_before": _q(x.get("net_prev_bhd")), "change_bhd": _q(x.get("delta_bhd"))}
                for x in (t.get(key) or [])][:TOP_ROWS]
    return {"rising": rows("rising"), "falling": rows("falling")}


def stock_shape(payload: dict | None) -> dict | None:
    t = _tile(payload, "products.stock_shape")
    if t is None:
        return None
    chips = {c.get("label"): c for c in t.get("chips") or []}
    slow, dead = chips.get("Over a year of cover") or {}, chips.get("No sale in 90 days") or {}
    return {"at_cost_bhd": _q(t.get("value")), "over_a_year_bhd": _q(slow.get("value")),
            "over_a_year_items": slow.get("items"), "no_sale_90_days_bhd": _q(dead.get("value")),
            "no_sale_90_days_items": dead.get("items")}


def shops_due(rows: list | None) -> dict | None:
    """The shops due a re-order, as the salesman app ranks them (app.followups.rank_shops)."""
    if rows is None:
        return None
    from app.followups import rank_shops
    ranked = rank_shops(rows)
    due = ranked.get("due") or []
    by_rep: dict[str, int] = {}
    for r in due:
        by_rep[str(r.get("rep") or "")] = by_rep.get(str(r.get("rep") or ""), 0) + 1
    return {"due": len(due), "lapsed": len(ranked.get("lapsed") or []),
            "monthly_value_bhd": _q(sum((_d(r.get("monthly_value_bhd")) for r in due), Decimal(0))),
            "by_rep": dict(sorted(by_rep.items())),
            "shops": [{"shop": r.get("shop"), "rep": r.get("rep"), "days_since": r.get("days_since"),
                       "usual_gap_days": r.get("gap_days"), "monthly_value_bhd": r.get("monthly_value_bhd")}
                      for r in due[:TOP_SHOPS]]}


def discounts(row: dict | None) -> dict:
    if not row:
        return {"evidenced": False, "why": "the discount figures could not be read"}
    lines = int(row.get("lines") or 0)
    if lines == 0:
        return {"evidenced": False, "why": "the Focus sales export carries no discount values"}
    net = _d(row.get("net_bhd"))
    return {"evidenced": True, "lines": lines, "bhd": _q(row.get("bhd")),
            "share_pct": float(round(_d(row.get("bhd")) / net * 100, 1)) if net > 0 else None}


def read_shops() -> list | None:
    from app.followups import load_shops
    from scripts.ops import db
    return load_shops(None, run=db.rpc_params, all_reps=True)


def read_discounts(start: date, end: date) -> dict | None:
    from scripts.ops import db
    rows = db.rpc_params(DISCOUNT_SQL, [start.isoformat(), end.isoformat()])
    return rows[0] if rows else None


def build(d: date, overview: dict | None, dim_rows: list | None, *, reads: bool = True) -> dict:
    """{team, push, movers, receivables, orders, shops_due, discounts, stock_shape, not_read}. A part that
    could not be read is None and named in not_read; nothing here ever stops the brief."""
    payload = (overview or {}).get("payload")
    rows = (overview or {}).get("rows") or {}
    not_read: list[str] = []
    out: dict = {
        "team": team_rows(rows.get("attainment"), _tile(payload, "team.reps"), dim_rows, d) if overview else None,
        "push": push_items(rows.get("movers"), rows.get("stock")) if overview else None,
        "movers": movers(payload), "receivables": receivables(payload), "orders": waiting_orders(payload),
        "stock_shape": stock_shape(payload),
    }
    shops = disc = None
    if reads:
        try:
            shops = read_shops()
        except Exception:  # noqa: BLE001  (one part of the e-mail, never the brief)
            not_read.append("the shops due a re-order")
        try:
            disc = read_discounts(d.replace(day=1), d)
        except Exception:  # noqa: BLE001
            not_read.append("the discounts")
    out["shops_due"] = shops_due(shops)
    if out["team"] and out["shops_due"]:
        from app import metrics             # the one rule that reads a rep's Focus names (rep_matches)
        for row in out["team"]:
            row["shops_due"] = sum(n for rep, n in out["shops_due"]["by_rep"].items()
                                   if metrics.rep_matches(rep, row["salesman"]))
    if not reads:
        out["discounts"] = {"evidenced": False, "why": "not read this time"}
    elif "the discounts" in not_read:
        out["discounts"] = {"evidenced": False, "why": "the discount figures could not be read"}
    else:
        out["discounts"] = discounts(disc)
    for key in ("team", "push", "movers", "receivables", "orders", "stock_shape"):
        if out[key] is None:
            not_read.append(key.replace("_", " "))
    out["not_read"] = not_read
    return out


def nth_business_day(start: date, n: int) -> date:
    """The n-th business day (Sunday to Thursday) after `start`; n = 0 is `start` itself, or the next
    business day when `start` is a Friday or a Saturday."""
    day = start
    while day.weekday() in (4, 5):           # Friday, Saturday
        day += timedelta(days=1)
    left = max(0, int(n))
    while left:
        day += timedelta(days=1)
        if day.weekday() not in (4, 5):
            left -= 1
    return day
