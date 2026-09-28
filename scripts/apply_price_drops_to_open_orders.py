"""Give open orders the price book's cuts (R7e, price book 41 effective 28-Sep-2026) -- dry run by default.

    python -m scripts.apply_price_drops_to_open_orders                                  # dry run: the table, nothing written
    python -m scripts.apply_price_drops_to_open_orders --effective 2026-09-28 --expect-cuts 34
    python -m scripts.apply_price_drops_to_open_orders --expect-cuts 34 --commit --by owner@example.com
    python -m scripts.apply_price_drops_to_open_orders --expect-cuts 34 --commit --by owner@example.com --include YQ-2609-0012
    python -m scripts.apply_price_drops_to_open_orders --reverse business_data/backups/<DAY>_pre-price-drop-orders
    python -m scripts.apply_price_drops_to_open_orders --reverse <dir> --commit --by owner@example.com
    python -m scripts.apply_price_drops_to_open_orders --verify-load --expect-cuts 34   # read-only load checks

Exit: 0 done (or a dry run); 1 an order not lowered / put back (changed since read, gone, a line
moved, a failed write) or a --verify-load FAIL; 2 no cuts on that date (load the price book first)
or refused (the Focus links unreadable); 3 the cut count is not --expect-cuts (nothing written).

The owner's rule: an order still open when the book cuts a price gets the lower price; nothing is
ever raised. Only the CONFIRMED layer moves -- lines' unit_price_confirmed / line_total_confirmed and
the order's total_confirmed_bhd / subtotal_confirmed_bhd (+ updated_at). What the shop placed
(unit_price_bhd, line_total_bhd, list_price_bhd, discounts, total_bhd, delivery, order_kind,
minimum_gap_bhd) is never touched, so prod_gate's fixed fields stay fixed. That is enough because
the order heart reads every total as confirmed ?? placed (shop_heart.effective_money) and prices a
line at shop_heart.lock_unit, which prefers the confirmed unit: a later Confirm, Amend or Deliver
keeps the lowered price. The order heart itself is not changed -- this only calls it.

Which lines: open orders (new / confirmed / packed / out_for_delivery), not test orders, not invoiced
or paid ones (those are listed: adjust in Focus by credit note). Invoiced = a Focus invoice number on
the order, a payment, or a confirmed shop_order_focus_links row; an order v_shop_focus_candidates
suggests an invoice for (nobody accepted it yet) is listed as possibly invoiced and left alone unless
--include names it. A line qualifies when its code is cut in v_price_change on --effective, its
confirmed quantity is above 0, it was placed on the old book (list price above today's
standard_rate), and today's engine price for it (shop_heart.price_new_line: the order's rep, tiers,
offers, margin floor) is under its locked unit. New unit = min(locked, today). The cart discount is
re-shared pro rata and never grows (the heart's compute_totals); delivery stays; no small-order fee
comes or goes. Only the cut moves a total: an order whose total on file is not what its lines add
up to, or with fewer lines than it was placed with, is listed (check by hand). An order the cut
takes below the minimum is reported, never cancelled -- except a Received one: the rep's Confirm
compares with the placed total and would ask for 'Shop agreed' for a change nobody made, so it is
listed and left until the rep confirms (a re-run then lowers it).

Writes, per order, the heart's own pattern (PostgREST has no multi-statement transaction): the
header compare-and-swap on status + updated_at, then the lines, then one 'amended' event (reason
'price', stage 'price_book' -- the shop sees its note on the tracking page); any failure after the
swap puts the lines and the header back. Then one shop_admin_audit row, and the offer ledger's
confirmed amounts re-measured (offers.refresh_confirmed: a lowered line's discount is measured
against its list moved with it, so the book's cut never reads as a rule's spend -- run this after
the API carrying that measure is deployed, or a rep's Confirm meanwhile re-measures the old way).
Before the first write the open orders and their lines are saved under
business_data/backups/<DAY>_pre-price-drop-orders/, and applied.json there grows after each order --
--reverse reads it. A re-run is a no-op.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

EFFECTIVE_DEFAULT = "2026-09-28"
OPEN = ("new", "confirmed", "packed", "out_for_delivery")
ORDER_MONEY = ("total_confirmed_bhd", "subtotal_confirmed_bhd")
LINE_MONEY = ("unit_price_confirmed", "line_total_confirmed")
BACKUPS = ROOT / "business_data" / "backups"
STAGE = "price_book"
CREDIT_NOTE = "invoiced / paid: adjust in Focus by credit note"
POSSIBLY_INVOICED = "possibly invoiced in Focus"
BELOW_MIN_NEW = ("Received, and the cut takes it under the minimum: the rep confirms it first "
                 "(else the Confirm asks for 'Shop agreed'), then re-run")
NOT_IN_CATALOG = "not in today's catalog"


def _shop():
    from app import shop
    return shop


def _heart():
    from app import shop_heart
    return shop_heart


def _sql(sql: str, params: list) -> list[dict]:
    """Read-only SQL (the yq_readonly RPCs). One seam, so the tests serve their own rows."""
    from app.db_read import exec_sql, exec_sql_params
    return (exec_sql_params(sql, params) if params else exec_sql(sql)) or []


def _f3(x) -> str:
    return "-" if x is None else f"{Decimal(str(x)):.3f}"


def _book_label(effective: str) -> str:
    d = date.fromisoformat(effective)
    return f"{d.day}-{d.strftime('%b')}"


# ── reads ─────────────────────────────────────────────────────────────────────

def load_cuts(effective: str) -> dict[str, dict]:
    """{UPPER code: {"sku_code", "was", "now"}} -- the MA_base cuts dated `effective`."""
    rows = _sql("SELECT sku_code, prev_price_bhd, current_price_bhd FROM v_price_change "
                "WHERE changed_on=$1::date AND current_price_bhd<prev_price_bhd", [effective])
    shop = _shop()
    out: dict[str, dict] = {}
    for r in rows:
        code = str(r.get("sku_code") or "").strip()
        if code:
            out[code.upper()] = {"sku_code": code, "was": shop.dmoney(r.get("prev_price_bhd")),
                                 "now": shop.dmoney(r.get("current_price_bhd"))}
    return out


def load_open_orders() -> list[dict]:
    """Every open order (full rows, paged) with its lines (full rows, by id) under "lines". Lines
    are read order by order: a batched read stops silently at PostgREST's 1,000 rows, and an order
    missing lines would be priced on the ones that came back."""
    from app import shop_analytics as sa
    shop = _shop()
    orders = sa.paged(lambda a: (shop.get_client().table("shop_orders").select("*").in_("status", list(OPEN))
                                 .order("id").range(a, a + sa.PAGE - 1).execute().data or []),
                      sa.ORDERS_MAX_PAGES)[0]
    for o in orders:
        o["lines"] = shop._order_lines(o["id"])
    return orders


def load_focus_marks(order_ids: list) -> dict[int, dict]:
    """{order id: {"state", "invoice_key", "method"}} for the orders Focus may already have billed:
    a confirmed shop_order_focus_links row (state 'confirmed'), else the best suggestion in
    v_shop_focus_candidates (state 'suggested': the invoice note / stock issue names the order, or
    the same rep sold the same items -- nobody accepted it yet). Service role. Raises when either
    cannot be read: an order that may be invoiced is never lowered blind."""
    from app import shop_pipeline as sp
    client = _shop().get_client()
    out: dict[int, dict] = {}
    for oid in order_ids:
        got = (client.table(sp.LINKS_TABLE).select("invoice_key,method").eq("order_id", oid)
               .eq("state", "confirmed").limit(1).execute().data or [])
        if got:
            out[oid] = {"state": "confirmed", **got[0]}
            continue
        got = (client.table(sp.CANDIDATES_VIEW).select("invoice_key,method,rank").eq("order_id", oid)
               .order("rank").limit(1).execute().data or [])
        if got:
            out[oid] = {"state": "suggested", **got[0]}
    return out


# ── the plan (pure: nothing is written) ───────────────────────────────────────

def plan_order(o: dict, ctx: dict, cuts: dict, focus: dict | None = None, *, include: bool = False) -> dict:
    """What the cut does to one order: the qualifying lines (old -> new unit), the confirmed-layer
    writes, the totals before / after and the minimum. `skip` says why nothing is written. `focus` =
    the order's load_focus_marks entry; `include` = the operator named it (--include): a suggestion
    no longer holds it back, a confirmed link still does."""
    from app import shop_pipeline as sp
    shop, heart = _shop(), _heart()
    D0 = shop.D0
    st = o.get("status")
    plan: dict = {"order_id": o["id"], "order_no": o.get("order_no"), "status": st,
                  "updated_at": o.get("updated_at"), "skip": None, "lines": [], "line_updates": {},
                  "upd_o": {}, "before": None, "after": None, "min": None, "drift": None}
    lines = o.get("lines") or []
    new_unit: dict[int, Decimal] = {}
    for ln in lines:
        code = str(ln.get("item_code") or "")
        if code.upper() not in cuts:
            continue
        row = {"line_id": ln["id"], "item_code": code, "qty": heart.qty_confirmed_eff(ln),
               "requested": shop._i(ln.get("qty")), "old": None, "new": None, "why": None}
        plan["lines"].append(row)
        if row["qty"] <= 0:
            row["why"] = "confirmed 0"
            continue
        try:
            row["old"] = heart.lock_unit(ln)
        except shop.ShopError:
            row["why"] = "no price on this order"
            continue
        cat = shop.resolve_code(ctx, code)
        it = (ctx.get("items") or {}).get(cat) if cat else None
        if not it or it.get("standard_rate") is None:
            row["why"] = NOT_IN_CATALOG
            continue
        lp = ln.get("list_price_bhd")
        if lp is None or shop.dmoney(lp) <= shop.dmoney(it["standard_rate"]):
            row["why"] = "placed on the new book"
            continue
        try:
            today = shop.dmoney(heart.price_new_line(o, cat, row["qty"], ctx)["unit_price_bhd"])
        except shop.ShopError as e:        # e.g. confirmed under the item's MOQ: the engine prices nothing
            row["why"] = f"{NOT_IN_CATALOG} ({e})"
            continue
        new = min(row["old"], today)
        if new >= row["old"]:              # never raised: the shop keeps the lower price it has
            row["why"] = "already at or under today's price"
            continue
        row["new"] = new
        new_unit[int(ln["id"])] = new
    placed, want = sum(1 for ln in lines if not heart.is_added(ln)), shop._i(o.get("items_count"))
    if shop._is_test(o):
        plan["skip"] = "test order"
    elif o.get("focus_invoice_no") or (o.get("payment_status") or "unpaid") != "unpaid":
        plan["skip"] = CREDIT_NOTE
    elif focus and focus.get("state") == "confirmed":
        plan["skip"] = f"{CREDIT_NOTE} (linked to {focus.get('invoice_key')})"
    elif focus and not include:
        # the recon has not been accepted, but Focus may already bill the old price: the owner decides
        label = sp.LINK_METHOD_LABELS.get(focus.get("method"), focus.get("method"))
        plan["skip"] = (f"{POSSIBLY_INVOICED} ({focus.get('invoice_key')}, {label}): credit note, "
                        f"or --include {o.get('order_no')}")
    elif st not in OPEN:
        plan["skip"] = f"order is {st}"
    elif not new_unit:
        plan["skip"] = "nothing to lower"
    elif placed < want:
        plan["skip"] = f"{placed} of the {want} lines it was placed with read: check by hand"
    if not new_unit or placed < want:
        return plan
    after_lines = [({**ln, "unit_price_confirmed": float(new_unit[int(ln["id"])])}
                    if int(ln["id"]) in new_unit else ln) for ln in lines]
    try:
        t0 = heart.compute_totals(o, lines, heart.qty_confirmed_eff)
        t = heart.compute_totals(o, after_lines, heart.qty_confirmed_eff)
    except shop.ShopError as e:
        plan["skip"] = plan["skip"] or f"check by hand: {e}"
        return plan
    before, after = shop.dmoney(heart.order_total(o)), t["total_bhd"]
    plan["before"], plan["after"] = before, after
    if t0["total_bhd"] != before:
        # a total on file its own lines do not add up to: writing the lines' total would take the
        # gap off (or put it on) with the cut -- only the cut may move a total
        plan["drift"] = t0["total_bhd"]
        plan["skip"] = plan["skip"] or (f"the total on file {_f3(before)} is not what its lines add up to "
                                        f"({_f3(t0['total_bhd'])}): check by hand")
    for x in t["lines"]:
        lid = int(x["line"]["id"])
        if lid in new_unit:
            plan["line_updates"][lid] = {"unit_price_confirmed": float(x["unit"]),
                                         "line_total_confirmed": float(x["total"])}
            row = next(r for r in plan["lines"] if int(r["line_id"]) == lid)
            row["d_line"] = x["total"] - shop.dmoney(row["old"] * row["qty"])
    plan["upd_o"] = {"total_confirmed_bhd": float(after), "subtotal_confirmed_bhd": float(t["subtotal_bhd"])}
    min_order = shop.dmoney((ctx.get("settings") or {}).get("shop_min_order_bhd"))
    delivery = t["delivery_bhd"]
    if min_order <= D0 or after - delivery >= min_order:
        plan["min"] = "ok"
    elif before - delivery < min_order:
        plan["min"] = "already small"
    else:
        plan["min"] = "BELOW MIN"       # reported only: the order stays, status and kind untouched
        if st == "new":
            # the heart's Confirm measures the minimum against the PLACED total (plan_edit, stage
            # confirm): it would ask the rep for 'Shop agreed' and record a below-minimum he never made
            plan["skip"] = plan["skip"] or BELOW_MIN_NEW
    if after >= before and not plan["skip"]:
        # a total the heart would re-price upwards is not this script's business: never raise an order
        plan["skip"] = "total would not drop: check by hand"
    return plan


def event_note(plan: dict, effective: str, *, undo: bool = False) -> str:
    """The shop-facing note on the 'amended' event (the tracking page shows it)."""
    moved = [r for r in plan["lines"] if r.get("new") is not None]
    if undo:
        parts = ", ".join(f"{r['item_code']} {_f3(r['new'])} → {_f3(r['old'])}" for r in moved)
        return (f"Price change undone: {parts}. Total BHD {_f3(plan['before'])} "
                f"(was {_f3(plan['after'])}).")
    parts = ", ".join(f"{r['item_code']} {_f3(r['old'])} → {_f3(r['new'])}" for r in moved)
    return (f"Price lowered to the {_book_label(effective)} price book: {parts}. "
            f"Total BHD {_f3(plan['after'])} (was {_f3(plan['before'])}).")


def print_table(plans: list[dict]) -> None:
    """order_no | status | item | qty | old unit | new unit | d unit | d line | total old -> new | min | skip"""
    head = (f"{'order_no':14} {'status':16} {'item':12} {'qty':>5} {'old unit':>9} {'new unit':>9} "
            f"{'d unit':>9} {'d line':>9}  {'total old -> new':21} {'min':13} skip")
    print(head)
    print("-" * len(head))
    for p in plans:
        tot = f"{_f3(p['before'])} -> {_f3(p['after'])}" if p["before"] is not None else "-"
        for i, r in enumerate(p["lines"]):
            first, moved = i == 0, r.get("new") is not None
            du = r["new"] - r["old"] if moved else None
            dl = r.get("d_line") if moved else None
            why = "; ".join(x for x in ((p["skip"] if first else None), r["why"]) if x)
            order_no, status = (p["order_no"] or str(p["order_id"]), p["status"]) if first else ("", "")
            print(f"{order_no:14} {status:16} {r['item_code'][:12]:12} {r['qty']:>5} {_f3(r['old']):>9} "
                  f"{_f3(r['new']):>9} {_f3(du):>9} {_f3(dl):>9}  {tot if first else '':21} "
                  f"{(p['min'] or '-') if first else '':13} {why}")


def footer(plans: list[dict], *, commit: bool) -> None:
    todo = [p for p in plans if not p["skip"]]
    n_lines = sum(len(p["line_updates"]) for p in todo)
    cut = sum((p["before"] - p["after"] for p in todo), Decimal("0"))
    skipped = [p for p in plans if p["skip"] and p["skip"] != "nothing to lower"]
    print(f"\nOrders to lower: {len(todo)}   lines: {n_lines}   BHD reduction: {cut:.3f}")
    for p in skipped:
        print(f"  skipped {p['order_no']}: {p['skip']}")
    below = [p for p in todo if p["min"] == "BELOW MIN"]
    if below:
        print(f"  BELOW MIN (reported, never cancelled): {', '.join(p['order_no'] for p in below)}")
    if not commit:
        print("\nDry run. Nothing written. Add --commit --by <owner email> to lower these orders.")


# ── writes ────────────────────────────────────────────────────────────────────

def _money_snapshot(o: dict, line_ids) -> dict:
    by_id = {int(ln["id"]): ln for ln in o.get("lines") or []}
    return {**{k: o.get(k) for k in ORDER_MONEY},
            "lines": [{"line_id": lid, "item_code": by_id[lid].get("item_code"),
                       **{k: by_id[lid].get(k) for k in LINE_MONEY}} for lid in line_ids]}


def _jsonable(x):
    if isinstance(x, Decimal):
        return f"{x:.3f}"
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(_jsonable(data), indent=1, default=str, ensure_ascii=False), encoding="utf-8")


def write_backup(orders: list[dict], plans: list[dict], root: Path | None = None) -> Path:
    """<DAY>_pre-price-drop-orders (a -2, -3 ... twin when the day already has one): every open order
    and its lines as read (select *), and the plan -- written before the first write."""
    day = _shop().bahrain_today().isoformat()
    base = (root or BACKUPS) / f"{day}_pre-price-drop-orders"
    out, n = base, 1
    while out.exists():
        n += 1
        out = base.with_name(f"{base.name}-{n}")
    out.mkdir(parents=True)
    _dump(out / "orders.json", [{k: v for k, v in o.items() if k != "lines"} for o in orders])
    _dump(out / "lines.json", [ln for o in orders for ln in o.get("lines") or []])
    _dump(out / "plan.json", plans)
    _dump(out / "applied.json", [])
    return out


def _write_order(o: dict, upd_o: dict, line_updates: dict, detail: dict, *, by: str,
                 expected_updated_at, audit_before: dict, audit_after: dict) -> tuple[str, str | None]:
    """The heart's write (shop_heart._edit): header CAS, lines, the event -- all put back on a
    failure after the swap -- then the audit row and the offer ledger re-measured, as the heart's
    callers do (app/shop.py). ('applied', new updated_at) | ('changed', None) | ('failed: ...', None)."""
    shop, heart = _shop(), _heart()
    from app import offers, shop_audit
    oid, st = o["id"], o["status"]
    now = shop._iso()
    upd = {**upd_o, "updated_at": now}
    client = shop.get_client()
    swapped = shop._update_optional(
        "shop_orders", upd, heart.ORDER_OPTIONAL,
        lambda q: heart.pin_updated(q.eq("id", oid).eq("status", st), expected_updated_at).execute().data or [])
    if not swapped:
        return "changed since read — re-run", None
    before_lines = {int(ln["id"]): ln for ln in o.get("lines") or []}
    undo = None
    try:
        undo, _ = heart.write_lines(client, before_lines, line_updates, [])
        client.table("shop_order_events").insert({"order_id": oid, "actor": by, "event": "amended",
                                                  "detail": detail}).execute()
    except Exception as e:  # noqa: BLE001 — whatever it was, lines and header go back first
        if undo:
            undo()
        heart._revert_header(oid, {k: o.get(k) for k in upd}, status=st, updated_at=now,
                             order_no=o.get("order_no"), err=e)
        return f"failed: {e}", None
    shop_audit.record(by, "order", oid, "update", audit_before, audit_after)
    offers.refresh_confirmed(oid)       # best effort, never raises; the ledger's updated_at, not the order's
    return "applied", str((swapped[0] or {}).get("updated_at") or now)


def apply_order(o: dict, plan: dict, *, by: str, effective: str) -> dict:
    """One order lowered. Returns {result, entry}; `entry` (for applied.json) only when applied."""
    note = event_note(plan, effective)
    moved = [r for r in plan["lines"] if r.get("new") is not None]
    detail = {"stage": STAGE, "from": o["status"], "note": note,
              "lines": [{"line_id": r["line_id"], "item_code": r["item_code"], "reason": "price",
                         "requested": r["requested"], "before": {"unit_price_bhd": float(r["old"])},
                         "after": {"unit_price_bhd": float(r["new"])}, "note": None} for r in moved],
              "changed": [], "removed": [], "added": [], "adverse": [], "shop_agreed": None,
              "total_before": float(plan["before"]), "total_after": float(plan["after"]),
              "price_book_date": effective}
    before = _money_snapshot(o, list(plan["line_updates"]))
    after = {**plan["upd_o"], "lines": [{"line_id": int(r["line_id"]), "item_code": r["item_code"],
                                         **plan["line_updates"][int(r["line_id"])]} for r in moved],
             "reason": f"price book {effective}"}
    result, new_ts = _write_order(o, plan["upd_o"], plan["line_updates"], detail, by=by,
                                  expected_updated_at=o.get("updated_at"), audit_before=before, audit_after=after)
    entry = None
    if result == "applied":
        entry = {"order_id": o["id"], "order_no": o.get("order_no"), "status": o["status"],
                 "updated_at": new_ts, "effective": effective, "before": before,
                 "after": {k: v for k, v in after.items() if k != "reason"},
                 "total_before": float(plan["before"]), "total_after": float(plan["after"])}
    return {"order_no": o.get("order_no"), "result": result, "entry": entry}


# ── reverse ───────────────────────────────────────────────────────────────────

def reverse(folder: str | Path, *, commit: bool, by: str | None) -> int:
    """Put back what applied.json says was lowered: the old confirmed layer (NULL on a Received
    order), an 'amended' event "Price change undone", an audit row. Compare-and-swap on the
    updated_at the apply wrote: an order touched since is skipped (adjust it by hand). With
    --commit, exit 1 unless every order in applied.json went back."""
    shop, heart = _shop(), _heart()
    path = Path(folder) / "applied.json"
    if not path.exists():
        print(f"REFUSED: no applied.json in {folder}")
        return 2
    entries = json.loads(path.read_text(encoding="utf-8"))
    print(f"{len(entries)} order(s) in {path}")
    back, left = 0, []                  # left: every order not put back, whatever the reason
    for e in entries:
        o = shop.get_order(e["order_id"])
        label = str(e.get("order_no") or e["order_id"])
        if not o:
            print(f"  {label}: gone, skipped")
            left.append(label)
            continue
        if heart.is_stale(o, e["updated_at"]) or o.get("status") != e.get("status"):
            print(f"  {label}: touched since the price change, skipped (adjust by hand)")
            left.append(label)
            continue
        by_id = {int(ln["id"]): ln for ln in o.get("lines") or []}
        lowered = {int(x["line_id"]): x for x in e["after"]["lines"]}
        if any(lid not in by_id or shop.money(by_id[lid].get("unit_price_confirmed"))
               != shop.money(x.get("unit_price_confirmed")) for lid, x in lowered.items()):
            print(f"  {label}: a line moved since the price change, skipped (adjust by hand)")
            left.append(label)
            continue
        back_lines = {int(x["line_id"]): {k: x.get(k) for k in LINE_MONEY} for x in e["before"]["lines"]}
        upd_o = {k: e["before"].get(k) for k in ORDER_MONEY}
        effective = e.get("effective") or EFFECTIVE_DEFAULT
        # the unit the shop had before (a Received order had no confirmed one: its placed unit)
        moved = [{"line_id": lid, "item_code": by_id[lid].get("item_code"), "requested": shop._i(by_id[lid].get("qty")),
                  "new": lowered[lid]["unit_price_confirmed"],
                  "old": back["unit_price_confirmed"] if back["unit_price_confirmed"] is not None
                  else by_id[lid].get("unit_price_bhd")} for lid, back in back_lines.items()]
        note = event_note({"lines": moved, "before": e["total_before"], "after": e["total_after"]}, effective, undo=True)
        print(f"  {label}: {note}")
        if not commit:
            continue
        detail = {"stage": STAGE, "from": o["status"], "note": note,
                  "lines": [{"line_id": r["line_id"], "item_code": r["item_code"], "reason": "price",
                             "requested": r["requested"], "before": {"unit_price_bhd": shop.money(r["new"])},
                             "after": {"unit_price_bhd": shop.money(r["old"])}, "note": None} for r in moved],
                  "changed": [], "removed": [], "added": [], "adverse": [], "shop_agreed": None,
                  "total_before": float(e["total_after"]), "total_after": float(e["total_before"]),
                  "price_book_date": effective, "undo": True}
        audit_after = {**upd_o, "lines": e["before"]["lines"], "reason": f"price book {effective} undone"}
        result, _ = _write_order(o, upd_o, back_lines, detail, by=by or "", expected_updated_at=o.get("updated_at"),
                                 audit_before=_money_snapshot(o, list(back_lines)), audit_after=audit_after)
        print(f"    -> {result}")
        if result == "applied":
            back += 1
        else:
            left.append(label)
    listed = f" (adjust by hand): {', '.join(left)}" if left else ""
    if not commit:
        if left:
            print(f"\n{len(left)} would be left as it is{listed}")
        print("\nDry run. Nothing written. Add --commit --by <owner email> to put these back.")
        return 0
    print(f"\n{back} put back, {len(left)} not{listed}.")
    return 1 if left else 0


# ── the load, read-only (release step 3) ──────────────────────────────────────

def verify_load(effective: str, expect_cuts: int | None) -> int:
    """Checks a, b, c and e of the R7e release: the cuts are loaded and in force, the sales data is
    fresh, and the catalog shows Was/Now on every cut line it sells. PASS / FAIL each."""
    shop = _shop()
    fails = 0

    def check(name: str, ok: bool, text: str) -> None:
        nonlocal fails
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {text}")
    r = (_sql("SELECT count(*) FILTER (WHERE current_price_bhd<prev_price_bhd) AS cuts, "
              "count(*) FILTER (WHERE current_price_bhd>prev_price_bhd) AS rises "
              "FROM v_price_change WHERE changed_on=$1::date", [effective]) or [{}])[0]
    cuts, rises = shop._i(r.get("cuts")), shop._i(r.get("rises"))
    want = f"{expect_cuts} / 0" if expect_cuts is not None else "> 0 / 0"
    check("a. cuts loaded", cuts > 0 and rises == 0 and (expect_cuts is None or cuts == expect_cuts),
          f"{cuts} cuts / {rises} rises dated {effective} (expected {want})")
    r = (_sql("SELECT count(*) AS n FROM v_price_change c JOIN v_price_list_by_book b ON b.sku_code=c.sku_code "
              "AND b.price_book='MA_base' WHERE c.changed_on=$1::date AND b.price_bhd=c.current_price_bhd",
              [effective]) or [{}])[0]
    n = shop._i(r.get("n"))
    check("b. new prices in force", n == cuts + rises and n > 0, f"{n} of {cuts + rises} at the new price")
    r = (_sql("SELECT MAX(sale_date)::text AS d FROM v_sales", []) or [{}])[0]
    got = str(r.get("d") or "")[:10]
    due = (date.fromisoformat(effective) - timedelta(days=1)).isoformat()
    check("c. freshness", bool(got) and got >= due, f"sales through {got or 'none'} (expected {due})")
    try:
        cut_codes = load_cuts(effective)
        ctx = shop.context(force=True)
        pay = shop.catalog_payload(ctx.get("share_token")) or {}
        items = pay.get("items") or []
        sold = {str(c).upper() for c in ctx.get("order") or []}
        in_cat = sorted(c for c in cut_codes if c in sold)
        gone = sorted(c for c in cut_codes if c not in sold)
        was = {str(i["item_code"]).upper() for i in items if i.get("was_bhd") is not None}
        shown = [c for c in in_cat if c in was]
        both = [i["item_code"] for i in items if {"price_drop", "clearance"} <= set(i.get("badges") or [])]
        print(f"      cut codes not in the catalog ({len(gone)}): {', '.join(gone) or '-'}")
        check("e. catalog Was/Now", bool(items) and len(shown) == len(in_cat),
              f"{len(shown)} of {len(in_cat)} cut catalog items show was_bhd ({len(was)} in all)")
        check("e. price drop wins (this checkout's code; prod after the deploy)", not both,
              f"{len(both)} item(s) carry both price_drop and clearance{': ' + ', '.join(both) if both else ''}")
    except Exception as e:  # noqa: BLE001 — a check that cannot run is a FAIL, never a crash
        check("e. catalog Was/Now", False, f"could not build the catalog: {e}")
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAIL'}")
    return 1 if fails else 0


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--effective", default=EFFECTIVE_DEFAULT, help="the price book's cut date (YYYY-MM-DD)")
    ap.add_argument("--expect-cuts", type=int, default=None, help="abort before any write unless exactly N cuts")
    ap.add_argument("--commit", action="store_true", help="write (default: dry run)")
    ap.add_argument("--by", default=None, help="the owner's email: the event's actor and the audit row's")
    ap.add_argument("--include", action="append", default=[], metavar="ORDER_NO",
                    help="lower this order although Focus suggests an invoice for it (the owner checked the "
                         "invoice is not this order's); repeatable. A confirmed Focus link is never overridden")
    ap.add_argument("--reverse", default=None, metavar="DIR", help="put back what DIR/applied.json lowered")
    ap.add_argument("--verify-load", action="store_true", help="read-only checks of the price-book load")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        try:          # the notes carry arrows; a cp1252 console must not crash the run
            sys.stdout.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    try:
        date.fromisoformat(a.effective)
    except ValueError:
        ap.error("--effective must be YYYY-MM-DD")
    if a.commit and not (a.by or "").strip():
        ap.error("--commit needs --by <owner email>")
    if a.verify_load:
        return verify_load(a.effective, a.expect_cuts)
    if a.reverse:
        return reverse(a.reverse, commit=a.commit, by=a.by)

    shop = _shop()
    cuts = load_cuts(a.effective)
    if not cuts:
        print(f"No price cuts dated {a.effective} in v_price_change: load the price book first.")
        return 2
    if a.expect_cuts is not None and len(cuts) != a.expect_cuts:
        print(f"REFUSED: {len(cuts)} cuts dated {a.effective}, expected {a.expect_cuts}. Nothing written.")
        return 3
    missing = [c for c in LINE_MONEY if not shop.has_column("shop_order_lines", c)]
    if missing:
        print(f"REFUSED: shop_order_lines has no {', '.join(missing)} (scripts/r7c_order_lines_qty_migration.sql). "
              f"Nothing written.")
        return 2
    ctx = shop.context(force=True)
    orders = load_open_orders()
    by_id = {o["id"]: o for o in orders}
    touched = [o["id"] for o in orders if any(str(ln.get("item_code") or "").upper() in cuts for ln in o["lines"])]
    try:
        focus = load_focus_marks(touched)
    except Exception as e:  # noqa: BLE001 — unread = unknown, and an invoiced order is never lowered blind
        print(f"REFUSED: could not read the Focus links / suggestions ({e}). Nothing written.")
        return 2
    include = {x.strip().upper() for x in a.include if x.strip()}
    plans = [p for p in (plan_order(o, ctx, cuts, focus.get(o["id"]),
                                    include=str(o.get("order_no") or "").upper() in include) for o in orders)
             if p["lines"]]
    unknown = sorted(include - {str(p["order_no"] or "").upper() for p in plans})
    if unknown:
        print(f"--include: {', '.join(unknown)} is not an open order with a cut line (ignored)")
    print(f"{len(cuts)} cuts dated {a.effective}; {len(orders)} open orders, {len(plans)} with a cut line.\n")
    print_table(plans)
    footer(plans, commit=a.commit)
    if not a.commit:
        return 0
    todo = [p for p in plans if not p["skip"]]
    if not todo:
        print("\nNothing to lower. Nothing written.")
        return 0
    folder = write_backup(orders, plans)
    print(f"\nBackup: {folder}")
    applied: list[dict] = []
    for p in todo:
        res = apply_order(by_id[p["order_id"]], p, by=a.by.strip(), effective=a.effective)
        print(f"  {res['order_no']}: {res['result']}")
        if res["entry"]:
            applied.append(res["entry"])
            _dump(folder / "applied.json", applied)
    print(f"\n{len(applied)} order(s) lowered, {len(todo) - len(applied)} not"
          f"{' (re-run: an applied order is a no-op)' if len(applied) < len(todo) else ''}. Undo: "
          f"python -m scripts.apply_price_drops_to_open_orders --reverse \"{folder}\" --commit --by {a.by.strip()}")
    return 0 if len(applied) == len(todo) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
