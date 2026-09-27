"""Order pipeline states beyond the five merchant stages (trust plan §9 Step 3 item 5; audit
B-08, SEC-10, TXN-08).

  * Cancel reasons — a staff cancel needs a reason code from CANCEL_REASONS ('other' needs a
    note); the merchant's own cancel is recorded as customer_request. The code goes to
    shop_orders.cancel_reason_code (once the migration adds it), the text to cancel_reason,
    and both to the status event, so the reason exists even before the column does. The note
    is the OFFICE's record: the merchant's email carries the reason's label only
    (customer_cancel_text) — "duplicate / fake number" never reaches a shop.
  * Payment — payment_status unpaid | partial | paid (+ method, amount, note) as a
    FACT recorded by the office, never a lifecycle state: an order is Delivered whether or not
    it is paid. Every change is an order event ('payment') the drawer shows. 'unpaid' is the
    row's default too, so it reads as "payment not recorded" (Focus holds the ledger), never
    as a claim that the shop owes.
  * Returns — a 'returned' EVENT with lines, quantities and a reason (never a status): the
    order stays Delivered, the value is on the event (Decimal, 3 dp) and, once the column
    exists, shop_orders.returned_bhd is recomputed from ALL the order's returned events for
    the list pill. A line can never be returned beyond what was delivered, across calls.
  * Focus invoice — the optional Focus invoice number recorded at Delivered (or later), and
    v_shop_focus_recon: delivered orders without an invoice, whose invoice is not in the
    uploaded ledger (v_sales), whose invoice's salesman or amount disagrees with the order, or
    whose invoice number sits on more than one delivered order.
  * Focus link (R7a, scripts/r7_focus_links_migration.sql) — v_shop_focus_candidates suggests
    which Focus invoice covers which open order (Narration naming the order, a Stock Issue
    Voucher naming it, or the same rep's invoice with the same lines); the office accepts or
    rejects each pair (shop_order_focus_links). An accept records the link, fills an empty
    focus_invoice_no and moves an order that is still open to Delivered through shop.set_status
    — never a money column, never a cancelled order. Invoice numbers are compared in ONE
    normalised form everywhere (clean_invoice_no = the views' SQL).

Money is Decimal end to end here; floats appear only at the JSON edge (shop.money).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from app import database

log = logging.getLogger(__name__)

# code → the label the chips show; order = how the chips are laid out
CANCEL_REASONS: dict[str, str] = {
    "out_of_stock": "Out of stock",
    "customer_request": "Customer asked to cancel",
    "duplicate": "Duplicate order",
    "test": "Test order",
    "price_issue": "Price issue",
    "below_minimum": "Below the minimum order",
    "other": "Other",
}
# codes the shop_orders.cancel_reason_code check accepts only after scripts/r7c_order_lines_qty_migration.sql
# (set_status writes 'other' to the column until then; the event keeps the real code)
R7C_CANCEL_REASONS = ("below_minimum",)
CANCEL_NOTE_MIN = 3
CANCEL_REASON_REQUIRED = ("Pick a cancel reason (out of stock, customer request, duplicate, test, price issue, "
                          "below the minimum or other).")
CANCEL_NOTE_REQUIRED = "Say why in the note when the reason is 'other'."
# What the MERCHANT is told when staff cancel: the reason's label from the fixed list, never the
# free-text note (that is the office's record — see customer_cancel_text). 'test' and 'other'
# add nothing beyond "cancelled".
CUSTOMER_CANCEL_TEXT: dict[str, str] = {
    "out_of_stock": "Out of stock",
    "customer_request": "As you asked",
    "duplicate": "Duplicate order",
    "price_issue": "Price issue",
    "below_minimum": "Below the minimum order value",
}

PAYMENT_STATUSES = ("unpaid", "partial", "paid")
PAYMENT_METHODS = ("cash", "benefit", "bank_transfer", "cheque", "credit", "other")
# 'unpaid' is also the row default (marketplace_migration.sql), so the label says what is true:
# nothing has been recorded here — payments live in Focus until the office records one.
PAYMENT_LABELS = {"unpaid": "Payment not recorded", "partial": "Partly paid", "paid": "Paid", "refunded": "Refunded"}

RETURN_REASON_MIN = 3
RETURN_MAX_LINES = 60

INVOICE_MAX = 40
RECON_HINT = "Focus reconciliation not available yet — apply scripts/r3_pipeline_migration.sql."

_Q = Decimal("0.001")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _d(x) -> Decimal:
    """Decimal from whatever the row carries (numeric arrives as a float/str from PostgREST)."""
    if x is None or x == "":
        return Decimal("0")
    return Decimal(str(x))


def q3(x) -> Decimal:
    return _d(x).quantize(_Q, rounding=ROUND_HALF_UP)


def _i(x, default: int = 0) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


# ── cancel reasons ────────────────────────────────────────────────────────────

def validate_cancel(reason_code: str | None, note: str | None, *, cancelled_by: str = "staff") -> tuple[str, str | None]:
    """(code, text) for a cancel. Staff must give a code; 'other' must come with a note. The
    merchant's own cancel (cancelled_by='customer') is customer_request with whatever they wrote."""
    from app.shop import ShopError, clean
    text = clean(note, 300) or None
    code = str(reason_code or "").strip().lower()
    if cancelled_by == "customer":
        return (code if code in CANCEL_REASONS else "customer_request"), text
    if code not in CANCEL_REASONS:
        raise ShopError(CANCEL_REASON_REQUIRED)
    if code == "other" and len(text or "") < CANCEL_NOTE_MIN:
        raise ShopError(CANCEL_NOTE_REQUIRED)
    return code, (text or CANCEL_REASONS[code])


def customer_cancel_text(reason_code) -> str | None:
    """The line the merchant's cancel email may carry: the reason's public label, or nothing.
    The staff note NEVER goes through here — it is internal (cancel_reason + the event)."""
    return CUSTOMER_CANCEL_TEXT.get(str(reason_code or "").strip().lower())


# ── payment ───────────────────────────────────────────────────────────────────

def set_payment(order_id: int, status: str, method: str | None, amount_bhd, note: str | None, actor: str) -> dict:
    """Record the payment fact on an order. Raises ShopError on a bad status/method, an unknown
    order or a cancelled one. Writes payment_status + payment_method (+ paid_at when the column
    exists) and one 'payment' event with from/to/method/amount/note. A request that names no
    method keeps the one on the row ('partial, cash' then 'paid' stays cash); paid_at survives
    the reverse script under a cached probe (shop._update_optional retries without it)."""
    from app import shop
    st = str(status or "").strip().lower()
    if st not in PAYMENT_STATUSES:
        raise shop.ShopError("Payment status must be unpaid, partial or paid.")
    m = str(method or "").strip().lower() or None
    if m and m not in PAYMENT_METHODS:
        raise shop.ShopError(f"Payment method must be one of {', '.join(PAYMENT_METHODS)}.")
    amt = None
    if amount_bhd not in (None, ""):
        try:
            amt = q3(amount_bhd)
        except Exception as e:  # noqa: BLE001
            raise shop.ShopError("Amount must be a number in BHD.") from e
        if amt < 0:
            raise shop.ShopError("Amount cannot be negative.")
    o = shop.get_order(order_id)
    if not o:
        raise shop.ShopError("Order not found.")
    if o.get("status") == "cancelled":
        raise shop.ShopError("A cancelled order has no payment to record.")
    if st == "paid" and amt is None:
        amt = q3(shop.shop_heart.order_total(o))
    now = _iso()
    method = m or (str(o.get("payment_method") or "").strip().lower() or None)     # omitted = unchanged
    upd: dict = {"payment_status": st, "payment_method": method, "updated_at": now}
    if shop.has_column("shop_orders", "paid_at"):
        upd["paid_at"] = now if st == "paid" else None
    client = database.get_client()
    done = shop._update_optional("shop_orders", upd, ("paid_at",),
                                 lambda q: q.eq("id", order_id).execute().data or [])
    if not done:
        raise shop.ShopError(shop.CAS_CONFLICT_MSG)
    client.table("shop_order_events").insert({
        "order_id": order_id, "actor": actor, "event": "payment",
        "detail": {"from": o.get("payment_status") or "unpaid", "to": st, "method": method,
                   "amount_bhd": (float(amt) if amt is not None else None),
                   "note": shop.clean(note, 300) or None}}).execute()
    out = shop.get_order(order_id) or {}
    out["payment_label"] = PAYMENT_LABELS.get(st, st)
    return out


# ── returns (an event, never a status) ────────────────────────────────────────

def returned_so_far(events) -> dict[int, int]:
    """{line_id: units already returned} from an order's earlier 'returned' events."""
    out: dict[int, int] = {}
    for ev in events or []:
        if (ev or {}).get("event") != "returned":
            continue
        for x in ((ev.get("detail") or {}).get("lines") or []):
            lid = _i((x or {}).get("line_id"))
            out[lid] = out.get(lid, 0) + max(0, _i((x or {}).get("qty")))
    return out


def returned_value(events) -> Decimal:
    """Σ value_bhd over an order's 'returned' events, Decimal 3 dp — what returned_bhd holds."""
    total = Decimal("0")
    for ev in events or []:
        if (ev or {}).get("event") == "returned":
            total += q3((ev.get("detail") or {}).get("value_bhd"))
    return q3(total)


def record_return(order_id: int, lines, reason: str | None, actor: str) -> dict:
    """Admin: goods came back. `lines` = [{line_id, qty}]; qty ≤ the confirmed (else ordered)
    quantity of that line MINUS what earlier returns already took back, so two returns (or a
    retry after a timeout) can never record more units than were delivered. Value = Σ qty × the
    line's confirmed unit price (else the ordered one), Decimal 3 dp — the line price as
    confirmed; an order-level coupon is not apportioned (the event carries the lines, the office
    decides the credit). Writes one 'returned' event (lines, reason, value) and, when the column
    exists, sets shop_orders.returned_bhd to the sum over ALL the order's returned events (read
    back after the insert, never prev + this). The status is untouched."""
    from app import shop
    from app.audit import log_event
    why = shop.clean(reason, 300)
    if len(why) < RETURN_REASON_MIN:
        raise shop.ShopError("A reason is required to record a return.")
    o = shop.get_order(order_id)
    if not o:
        raise shop.ShopError("Order not found.")
    if o.get("status") != "delivered":
        raise shop.ShopError("Only a delivered order can have a return recorded.")
    by_id = {int(ln["id"]): ln for ln in o.get("lines") or []}
    already = returned_so_far(o.get("events"))
    items = list(lines or [])[:RETURN_MAX_LINES]
    if not items:
        raise shop.ShopError("Pick at least one line to return.")
    out_lines: list[dict] = []
    total = Decimal("0")
    seen: set[int] = set()
    for it in items:
        lid = _i((it or {}).get("line_id"))
        ln = by_id.get(lid)
        if not ln or lid in seen:
            raise shop.ShopError("Unknown or repeated order line.")
        seen.add(lid)
        qty = _i((it or {}).get("qty"))
        # R7c: what was handed over (qty_delivered; plain Delivered = the confirmed quantity)
        delivered = shop.shop_heart.qty_delivered_eff(ln, o.get("status")) or 0
        if delivered <= 0:
            raise shop.ShopError(f"{ln['item_code']} was not delivered — nothing to return.")
        back = already.get(lid, 0)
        cap = max(0, delivered - back)
        if cap <= 0:
            raise shop.ShopError(f"{ln['item_code']}: all {delivered} delivered units are already returned.")
        if qty <= 0 or qty > cap:
            tail = f" ({back} of {delivered} already returned)." if back else "."
            raise shop.ShopError(f"Return quantity for {ln['item_code']} must be between 1 and {cap}{tail}")
        unit = q3(ln.get("unit_price_confirmed") if ln.get("unit_price_confirmed") is not None else ln.get("unit_price_bhd"))
        value = q3(unit * qty)
        total += value
        out_lines.append({"line_id": lid, "item_code": ln["item_code"], "qty": qty,
                          "unit_price_bhd": float(unit), "value_bhd": float(value)})
    total = q3(total)
    client = database.get_client()
    detail = {"lines": out_lines, "reason": why, "value_bhd": float(total), "units": sum(x["qty"] for x in out_lines)}
    client.table("shop_order_events").insert({"order_id": order_id, "actor": actor, "event": "returned",
                                              "detail": detail}).execute()
    if shop.has_column("shop_orders", "returned_bhd"):
        try:
            # the sum of every returned event as it stands now (this one included), not the row
            # value read earlier plus this call — two office users recording returns at once each
            # write the full sum, so neither increment is lost
            evs = (client.table("shop_order_events").select("event,detail").eq("order_id", order_id)
                   .eq("event", "returned").execute().data or [])
            client.table("shop_orders").update({"returned_bhd": float(returned_value(evs)), "updated_at": _iso()}) \
                .eq("id", order_id).execute()
        except Exception as e:  # noqa: BLE001 — the event is the record; the column is a convenience
            if shop._missing_column(e):
                shop._forget_column("shop_orders", "returned_bhd")
            log.warning("returned_bhd update failed for %s: %s", o.get("order_no"), e)
    log_event(actor, "shop.order_return", detail={"order_id": order_id, "order_no": o.get("order_no"), **detail})
    return {"ok": True, "order_id": order_id, "order_no": o.get("order_no"), "value_bhd": float(total),
            "lines": out_lines, "reason": why}


# ── Focus invoice ─────────────────────────────────────────────────────────────

# Focus writes 'SI : SI-YQ-26-09-119' (the voucher prefix), the stock ledger 'SI:SI-YQ-26-09-119',
# staff type 'SI-YQ-26-09-119' or 'si-yq-26-09-119 '. The views compare
# upper(regexp_replace(trim(x), '^SI\s*:\s*', '', 'i')) — this is the same rule in Python.
_SI_PREFIX = re.compile(r"^SI\s*:\s*", re.I)


def clean_invoice_no(raw) -> str | None:
    """The invoice number in the one form every comparison uses: trimmed, the 'SI :' voucher
    prefix removed, upper case — SI-YQ-26-09-119. None when nothing is left."""
    from app.shop import clean
    s = _SI_PREFIX.sub("", clean(raw, INVOICE_MAX + 10).strip()).upper()
    return s[:INVOICE_MAX] or None


def set_invoice(order_id: int, focus_invoice_no, actor: str) -> dict:
    """Record (or correct) the Focus invoice number on a delivered order; one 'invoice' event."""
    from app import shop
    inv = clean_invoice_no(focus_invoice_no)
    if not inv:
        raise shop.ShopError("Enter the Focus invoice number.")
    o = shop.get_order(order_id)
    if not o:
        raise shop.ShopError("Order not found.")
    if o.get("status") != "delivered":
        raise shop.ShopError("The Focus invoice is recorded on a delivered order.")
    if (o.get("focus_invoice_no") or "") == inv:
        return o
    client = database.get_client()
    done = (client.table("shop_orders").update({"focus_invoice_no": inv, "updated_at": _iso()})
            .eq("id", order_id).execute().data or [])
    if not done:
        raise shop.ShopError(shop.CAS_CONFLICT_MSG)
    client.table("shop_order_events").insert({"order_id": order_id, "actor": actor, "event": "invoice",
                                              "detail": {"from": o.get("focus_invoice_no"), "to": inv}}).execute()
    return shop.get_order(order_id) or {}


RECON_FLAGS = ("missing_invoice", "invoice_not_found", "salesman_mismatch", "amount_mismatch", "invoice_reused")


LEDGER_AS_OF_SQL = "SELECT MAX(sale_date)::text AS d FROM v_sales"


def ledger_as_of() -> str | None:
    """The last sale date in the uploaded Focus ledger (v_sales) — 'not in the ledger' means
    'not in the ledger uploaded up to this date', which the UI says. Read through the read-only
    RPC (app.db_read's run_readonly_query, yq_readonly-owned) via app.database.get_client at
    call time. None when unavailable — never a failed reconciliation list."""
    try:
        r = database.get_client().rpc("run_readonly_query", {"sql_text": LEDGER_AS_OF_SQL}).execute()
        data = r.data
        if isinstance(data, str):
            data = json.loads(data)
        d = ((data or [{}])[0] or {}).get("d")
        return str(d)[:10] if d else None
    except Exception as e:  # noqa: BLE001 — a missing RPC must not cost the reconciliation list
        log.debug("ledger_as_of unavailable: %s", e)
        return None


def focus_recon(limit: int = 300) -> dict:
    """Delivered orders against v_sales by Focus invoice: missing invoice, invoice not in the
    uploaded ledger, salesman mismatch, amount mismatch, one invoice number on several delivered
    orders. Reads v_shop_focus_recon (service role only — it joins customer names, which never
    leave through here). Empty + hint before the migration. `ledger_as_of` = the last sale date
    the ledger holds, so "not found" is read against the upload, not against Focus itself."""
    from app.shop import money
    try:
        rows = (database.get_client().table("v_shop_focus_recon").select("*")
                .order("delivered_at", desc=True).limit(max(1, min(int(limit or 300), 1000))).execute().data or [])
    except Exception as e:  # noqa: BLE001 — the view arrives with the migration
        log.info("focus recon unavailable: %s", e)
        return {"rows": [], "count": 0, "issues": 0, "hint": RECON_HINT}
    out = []
    issues = 0
    for r in rows:
        flags = [k for k in RECON_FLAGS if r.get(k)]
        if flags:
            issues += 1
        out.append({
            "order_id": r.get("order_id"), "order_no": r.get("order_no"), "delivered_at": r.get("delivered_at"),
            "customer_shop": r.get("customer_shop"), "salesman_name": r.get("salesman_name"),
            "salesman_focus_name": r.get("salesman_focus_name"), "order_total_bhd": money(r.get("order_total_bhd")),
            "payment_status": r.get("payment_status"), "focus_invoice_no": r.get("focus_invoice_no"),
            "invoice_total_bhd": money(r["invoice_total_bhd"]) if r.get("invoice_total_bhd") is not None else None,
            "invoice_date": r.get("invoice_date"), "focus_salesman": r.get("focus_salesman"),
            "amount_diff_bhd": money(r["amount_diff_bhd"]) if r.get("amount_diff_bhd") is not None else None,
            "flags": flags, "is_test": bool(r.get("is_test")),
            # R7a (absent before scripts/r7_focus_links_migration.sql): every invoice the order is
            # compared with, how it got them (confirmed link / typed number) and the orders sharing them
            "invoice_keys": r.get("invoice_keys"), "link_state": r.get("link_state"),
            "linked_orders_n": _i(r.get("linked_orders_n")),
            "linked_orders_total_bhd": (money(r["linked_orders_total_bhd"])
                                        if r.get("linked_orders_total_bhd") is not None else None),
        })
    return {"rows": out, "count": len(out), "issues": issues, "ledger_as_of": ledger_as_of()}


# ── Focus link (R7a): suggestions, accept / reject ────────────────────────────

LINKS_TABLE = "shop_order_focus_links"
CANDIDATES_VIEW = "v_shop_focus_candidates"
LINKS_HINT = "Focus suggestions not available yet — apply scripts/r7_focus_links_migration.sql."
LINK_ACTIONS = ("accept", "reject")
LINK_METHODS = ("narration_ref", "sio_ref", "auto_items", "manual")
# plain words for the drawer / list (what the office reads next to a suggestion)
LINK_METHOD_LABELS = {"narration_ref": "Order number in the invoice note",
                      "sio_ref": "Order number on the stock issue",
                      "auto_items": "Same rep, same items", "manual": "Entered by hand"}
# an accept moves an order that is still open to Delivered; delivered stays, cancelled is refused
ADVANCE_FROM = ("new", "confirmed", "packed", "out_for_delivery")
CANDIDATES_PER_ORDER = 5
FOCUS_ACTOR = "focus-recon:"
CANCELLED_LINK_MSG = "This order was cancelled — it can't be linked to a Focus invoice."
NOT_IN_LEDGER_MSG = ("{key} is not in the uploaded Focus sales yet — check the number, or upload the "
                     "latest day book and try again.")
LEDGER_CHECK_MSG = "Couldn't check the Focus sales just now — try again in a minute."


def _missing_table(e: Exception) -> bool:
    """PostgREST's answer for a table / view the migration has not created yet."""
    code = str(getattr(e, "code", "") or "")
    msg = str(e)
    return code in ("42P01", "PGRST205") or "42P01" in msg or "PGRST205" in msg or \
        ("relation" in msg and "does not exist" in msg) or "Could not find the table" in msg


def _unique_violation(e: Exception) -> bool:
    code = str(getattr(e, "code", "") or "")
    return code == "23505" or "23505" in str(e) or "duplicate key" in str(e)


def _f3(x) -> float | None:
    return None if x is None or x == "" else float(q3(x))


def _candidate_out(r: dict) -> dict:
    """One suggestion as the API emits it: the invoice, how it was found, and the line diff."""
    from app.shop import money
    lines = {"order": _i(r.get("lines_order")), "invoice": _i(r.get("lines_invoice")),
             "matched": _i(r.get("lines_matched")), "qty_diff": _i(r.get("lines_qty_diff")),
             "price_diff": _i(r.get("lines_price_diff")),
             "missing_on_invoice": _i(r.get("lines_missing_on_invoice")),
             "extra_on_invoice": _i(r.get("lines_extra_on_invoice"))}
    amount_diff = money(r.get("amount_diff_bhd"))
    method = r.get("method")
    return {
        "invoice_key": r.get("invoice_key"), "invoice_date": (str(r["invoice_date"])[:10] if r.get("invoice_date") else None),
        "focus_salesman": r.get("focus_salesman"), "focus_customer": r.get("focus_customer"),
        "invoice_total_bhd": money(r.get("invoice_total_bhd")),
        "invoice_open_bhd": money(r.get("invoice_open_bhd")),
        "invoice_linked_n": _i(r.get("invoice_linked_n")),
        "invoice_orders_n": _i(r.get("invoice_orders_n"), 1),
        "amount_diff_bhd": amount_diff,
        "method": method, "method_label": LINK_METHOD_LABELS.get(method, method),
        "confidence": _f3(r.get("confidence")), "overlap_share": _f3(r.get("overlap_share")),
        "sio_key": r.get("sio_key"), "rank": _i(r.get("rank"), 1),
        "lines": lines,
        # every line on both sides at the same quantity and price, and the same money
        "exact": (lines["order"] > 0 and lines["matched"] == lines["order"] == lines["invoice"]
                  and abs(amount_diff) < 0.0005),
    }


def list_focus_candidates(limit: int = 300, per_order: int = CANDIDATES_PER_ORDER) -> dict:
    """Open orders without a confirmed Focus link, each with its suggested invoices (best first,
    at most `per_order`). Reads v_shop_focus_candidates (service role only — it carries shop and
    Focus customer names, which leave only through this admin route). Newest order first; `limit`
    caps the view rows read. Empty + hint before the migration."""
    from app.shop import STATUS_LABELS, money
    lim = max(1, min(int(limit or 300), 1000))
    keep = max(1, min(int(per_order or CANDIDATES_PER_ORDER), 20))
    try:
        rows = (database.get_client().table(CANDIDATES_VIEW).select("*")
                .order("order_created_at", desc=True).limit(lim).execute().data or [])
    except Exception as e:  # noqa: BLE001 — the view arrives with the migration
        log.info("focus candidates unavailable: %s", e)
        return {"orders": [], "count": 0, "candidates": 0, "hint": LINKS_HINT}
    by_order: dict[int, dict] = {}
    for r in rows:
        oid = _i(r.get("order_id"))
        o = by_order.get(oid)
        if o is None:
            status = r.get("order_status")
            o = by_order[oid] = {
                "order_id": oid, "order_no": r.get("order_no"), "status": status,
                "status_label": STATUS_LABELS.get(status, status), "created_at": r.get("order_created_at"),
                "customer_shop": r.get("customer_shop"), "salesman_id": r.get("salesman_id"),
                "salesman_name": r.get("salesman_name"), "order_total_bhd": money(r.get("order_total_bhd")),
                "typed_invoice_key": r.get("typed_invoice_key"), "candidates": [],
            }
        o["candidates"].append(_candidate_out(r))
    out = []
    shown = 0
    for o in by_order.values():               # insertion order = newest order first
        o["candidates"].sort(key=lambda c: (c["rank"], c["invoice_key"] or ""))
        o["candidates"] = o["candidates"][:keep]
        shown += len(o["candidates"])
        out.append(o)
    return {"orders": out, "count": len(out), "candidates": shown, "ledger_as_of": ledger_as_of()}


def _link_row(order_id: int, key: str) -> dict | None:
    """The decision already on file for this pair, or None. ShopError(LINKS_HINT) before the
    migration (the table is missing); any other failure propagates."""
    from app.shop import ShopError
    try:
        got = (database.get_client().table(LINKS_TABLE).select("*").eq("order_id", order_id)
               .eq("invoice_key", key).limit(1).execute().data or [])
    except Exception as e:  # noqa: BLE001
        if _missing_table(e):
            raise ShopError(LINKS_HINT) from e
        raise
    return got[0] if got else None


def _candidate(order_id: int, key: str) -> dict:
    """This pair's row in v_shop_focus_candidates ({} when the view does not suggest it)."""
    try:
        got = (database.get_client().table(CANDIDATES_VIEW).select("*").eq("order_id", order_id)
               .eq("invoice_key", key).limit(1).execute().data or [])
    except Exception as e:  # noqa: BLE001 — a missing suggestion is a manual decision, not an error
        log.info("focus candidate lookup failed for %s/%s: %s", order_id, key, e)
        return {}
    return got[0] if got else {}


def _in_ledger(key: str) -> bool:
    """Is this invoice in the uploaded Focus sales (v_sales)? Focus stores 'SI : <key>', so the
    lookup is a suffix match narrowed to the exact normalised key in Python (an '_' in the key
    is a LIKE wildcard; the Python comparison removes anything it lets through)."""
    from app.shop import ShopError
    try:
        got = (database.get_client().table("v_sales").select("invoice_no").ilike("invoice_no", f"%{key}")
               .limit(50).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("ledger lookup for %s failed: %s", key, e)
        raise ShopError(LEDGER_CHECK_MSG) from e
    return any(clean_invoice_no(r.get("invoice_no")) == key for r in got)


def _save_link(existing: dict | None, order_id: int, key: str, *, state: str, method: str,
               confidence, sio_key, allocated, note: str | None, actor: str) -> dict:
    """Insert or update the pair's row. A concurrent first decision (unique violation on
    (order_id, invoice_key)) is re-read and updated, so the last decision stands and nothing
    is lost from the audit (both calls write their own audit rows)."""
    client = database.get_client()
    row = {"state": state, "method": method, "confidence": _f3(confidence), "sio_key": sio_key or None,
           "allocated_bhd": _f3(allocated), "note": note or None, "decided_by": actor, "decided_at": _iso()}
    if existing is None:
        try:
            got = client.table(LINKS_TABLE).insert({"order_id": order_id, "invoice_key": key,
                                                    "created_by": actor, **row}).execute().data or []
            return got[0] if got else {"order_id": order_id, "invoice_key": key, **row}
        except Exception as e:  # noqa: BLE001 — only the lost race is retried as an update
            if not _unique_violation(e):
                raise
            existing = _link_row(order_id, key) or {}
    got = (client.table(LINKS_TABLE).update(row).eq("order_id", order_id).eq("invoice_key", key)
           .execute().data or [])
    return got[0] if got else {**(existing or {}), **row}


def _fill_invoice_if_empty(o: dict, key: str, actor: str, extra: dict) -> bool:
    """An order already Delivered: put the invoice number on it only while the field is still
    empty (compare-and-set on what was read), with the same 'invoice' event set_invoice writes."""
    prev = o.get("focus_invoice_no")
    q = database.get_client().table("shop_orders").update({"focus_invoice_no": key, "updated_at": _iso()}) \
        .eq("id", o["id"])
    q = q.is_("focus_invoice_no", "null") if prev is None else q.eq("focus_invoice_no", prev)
    if not (q.execute().data or []):
        return False                     # someone recorded a number meanwhile: theirs stays
    database.get_client().table("shop_order_events").insert({
        "order_id": o["id"], "actor": actor, "event": "invoice",
        "detail": {"from": prev, "to": key, **extra}}).execute()
    return True


def decide_focus_link(order_id: int, invoice_key, action: str, actor: str, note: str | None = None) -> dict:
    """The office's answer to a suggestion (or a pair typed by hand): accept | reject.

    accept — stores a confirmed link (method and confidence from the suggestion, else 'manual';
    a manual pair must be in the uploaded Focus sales), puts the invoice number on the order when
    its field is empty, and moves an order that is still Confirmed / Preparing / On the way — or
    Received when the invoice matches it exactly — to Delivered through shop.set_status — a compare-and-swap on the status read here, actor
    'focus-recon:<email>', the status event carrying {invoice_key, method, confidence}. A Received
    order passes Confirmed on the way (the lifecycle has no direct step) without the confirmed
    totals being written. No money column is ever written, the merchant is not notified (the goods
    left long ago) and a cancelled order is refused, never reopened.

    reject — stores the pair as rejected, so v_shop_focus_candidates never suggests it again and a
    typed number equal to it stops counting in v_shop_focus_recon. The order row is not touched.

    Every decision leaves an audit_log row ('shop.focus_link') and a shop_admin_audit row (entity
    'focus_link', before/after = the link row). ShopError on bad input, an unknown order (the
    route's 404), a lost race (CAS_CONFLICT_MSG, the route's 409) or before the migration."""
    from app import shop, shop_audit
    from app.audit import log_event
    act = str(action or "").strip().lower()
    if act not in LINK_ACTIONS:
        raise shop.ShopError("Action must be accept or reject.")
    key = clean_invoice_no(invoice_key)
    if not key:
        raise shop.ShopError("Enter the Focus invoice number.")
    o = shop.get_order(order_id)
    if not o:
        raise shop.ShopError("Order not found.")
    existing = _link_row(order_id, key)                # LINKS_HINT before the migration
    cand = _candidate(order_id, key)
    method = cand.get("method") or (existing or {}).get("method") or "manual"
    if method not in LINK_METHODS:
        method = "manual"
    confidence = cand.get("confidence") if cand else (existing or {}).get("confidence")
    sio_key = cand.get("sio_key") or (existing or {}).get("sio_key")
    tag = f"{FOCUS_ACTOR}{actor}"
    extra = {"invoice_key": key, "method": method, "confidence": _f3(confidence)}
    status_from = o.get("status")
    status_to = status_from
    why = shop.clean(note, 300) or None
    was = (existing or {}).get("state")
    empty = not str(o.get("focus_invoice_no") or "").strip()

    if act == "accept" and status_from == "cancelled":
        raise shop.ShopError(CANCELLED_LINK_MSG)
    # the same decision again (a double tap, a retry after a timeout) writes nothing
    if (act == "reject" and was == "rejected") or \
            (act == "accept" and was == "confirmed" and status_from not in ADVANCE_FROM and not empty):
        return _decision_out(o, o, key, act, existing or {}, method, confidence, sio_key)

    if act == "accept":
        if not cand and was != "confirmed" and not _in_ledger(key):
            raise shop.ShopError(NOT_IN_LEDGER_MSG.format(key=key))
        # a Received order skips the rep's confirmation only when the invoice IS the order (every line
        # the same SKU, qty and price, same money); a partial match links it and leaves the status to
        # the rep, who confirms what was really supplied (review R7a: 0005 had 13 lines, 6 invoiced)
        exact = bool(cand) and _candidate_out(cand)["exact"]
        if status_from in ADVANCE_FROM and (status_from != "new" or exact):
            cur = status_from
            if cur == "new":
                shop.set_status(order_id, "confirmed", None, actor=tag, expected_status="new",
                                keep_totals=True, detail_extra=extra)
                cur = "confirmed"
            done = shop.set_status(order_id, "delivered", None, actor=tag, expected_status=cur,
                                   focus_invoice_no=(key if empty else None), detail_extra=extra)
            status_to = done.get("status") or "delivered"
        elif empty:
            _fill_invoice_if_empty(o, key, tag, extra)
        total = shop.shop_heart.order_total(o)
        link = _save_link(existing, order_id, key, state="confirmed", method=method, confidence=confidence,
                          sio_key=sio_key, allocated=total, note=why, actor=actor)
    else:
        link = _save_link(existing, order_id, key, state="rejected", method=method, confidence=confidence,
                          sio_key=sio_key, allocated=None, note=why, actor=actor)

    detail = {"order_id": order_id, "order_no": o.get("order_no"), "action": act, **extra,
              "state": link.get("state"), "sio_key": sio_key, "from_status": status_from, "to_status": status_to}
    log_event(actor, "shop.focus_link", detail=detail)
    shop_audit.record(actor, "focus_link", f"{order_id}:{key}", act, existing, link)
    return _decision_out(o, shop.get_order(order_id) or o, key, act, link, method, confidence, sio_key)


def _decision_out(before: dict, after: dict, key: str, act: str, link: dict, method: str, confidence, sio_key) -> dict:
    from app.shop import STATUS_LABELS
    st = after.get("status")
    return {"ok": True, "order_id": before.get("id"), "order_no": before.get("order_no"), "invoice_key": key,
            "action": act, "state": link.get("state"), "method": method,
            "method_label": LINK_METHOD_LABELS.get(method, method), "confidence": _f3(confidence),
            "sio_key": sio_key or None, "status": st, "status_label": STATUS_LABELS.get(st, st),
            "advanced": st != before.get("status"), "focus_invoice_no": after.get("focus_invoice_no")}
