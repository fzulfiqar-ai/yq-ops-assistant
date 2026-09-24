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

Money is Decimal end to end here; floats appear only at the JSON edge (shop.money).
"""
from __future__ import annotations

import json
import logging
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
    "other": "Other",
}
CANCEL_NOTE_MIN = 3
CANCEL_REASON_REQUIRED = "Pick a cancel reason (out of stock, customer request, duplicate, test, price issue or other)."
CANCEL_NOTE_REQUIRED = "Say why in the note when the reason is 'other'."
# What the MERCHANT is told when staff cancel: the reason's label from the fixed list, never the
# free-text note (that is the office's record — see customer_cancel_text). 'test' and 'other'
# add nothing beyond "cancelled".
CUSTOMER_CANCEL_TEXT: dict[str, str] = {
    "out_of_stock": "Out of stock",
    "customer_request": "As you asked",
    "duplicate": "Duplicate order",
    "price_issue": "Price issue",
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
        amt = q3(o.get("total_confirmed_bhd") if o.get("total_confirmed_bhd") is not None else o.get("total_bhd"))
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
        if (ln.get("line_status") or "ok") == "removed":
            raise shop.ShopError(f"{ln['item_code']} was removed at confirmation — nothing to return.")
        qty = _i((it or {}).get("qty"))
        delivered = _i(ln.get("qty_confirmed")) if ln.get("qty_confirmed") is not None else _i(ln.get("qty"))
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

def clean_invoice_no(raw) -> str | None:
    from app.shop import clean
    return clean(raw, INVOICE_MAX).strip() or None


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
        })
    return {"rows": out, "count": len(out), "issues": issues, "ledger_as_of": ledger_as_of()}
