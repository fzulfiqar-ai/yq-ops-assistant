"""Order pipeline states beyond the five merchant stages (trust plan §9 Step 3 item 5; audit
B-08, SEC-10, TXN-08).

  * Cancel reasons — a staff cancel needs a reason code from CANCEL_REASONS ('other' needs a
    note); the merchant's own cancel is recorded as customer_request. The code goes to
    shop_orders.cancel_reason_code (once the migration adds it), the text to cancel_reason,
    and both to the status event, so the reason exists even before the column does.
  * Payment — payment_status unpaid | partial | paid (+ method, amount, note) as a
    FACT recorded by the office, never a lifecycle state: an order is Delivered whether or not
    it is paid. Every change is an order event ('payment') the drawer shows.
  * Returns — a 'returned' EVENT with lines, quantities and a reason (never a status): the
    order stays Delivered, the value is on the event (Decimal, 3 dp) and, once the column
    exists, summed into shop_orders.returned_bhd for the list pill.
  * Focus invoice — the optional Focus invoice number recorded at Delivered (or later), and
    v_shop_focus_recon: delivered orders without an invoice, or whose invoice's salesman or
    amount in v_sales disagrees with the order.

Money is Decimal end to end here; floats appear only at the JSON edge (shop.money).
"""
from __future__ import annotations

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

PAYMENT_STATUSES = ("unpaid", "partial", "paid")
PAYMENT_METHODS = ("cash", "benefit", "bank_transfer", "cheque", "credit", "other")
PAYMENT_LABELS = {"unpaid": "Unpaid", "partial": "Partly paid", "paid": "Paid", "refunded": "Refunded"}

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


# ── payment ───────────────────────────────────────────────────────────────────

def set_payment(order_id: int, status: str, method: str | None, amount_bhd, note: str | None, actor: str) -> dict:
    """Record the payment fact on an order. Raises ShopError on a bad status/method, an unknown
    order or a cancelled one. Writes payment_status + payment_method (+ paid_at when the column
    exists) and one 'payment' event with from/to/method/amount/note."""
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
    upd: dict = {"payment_status": st, "payment_method": m, "updated_at": now}
    if shop.has_column("shop_orders", "paid_at"):
        upd["paid_at"] = now if st == "paid" else None
    client = database.get_client()
    done = client.table("shop_orders").update(upd).eq("id", order_id).execute().data or []
    if not done:
        raise shop.ShopError(shop.CAS_CONFLICT_MSG)
    client.table("shop_order_events").insert({
        "order_id": order_id, "actor": actor, "event": "payment",
        "detail": {"from": o.get("payment_status") or "unpaid", "to": st, "method": m,
                   "amount_bhd": (float(amt) if amt is not None else None),
                   "note": shop.clean(note, 300) or None}}).execute()
    out = shop.get_order(order_id) or {}
    out["payment_label"] = PAYMENT_LABELS.get(st, st)
    return out


# ── returns (an event, never a status) ────────────────────────────────────────

def record_return(order_id: int, lines, reason: str | None, actor: str) -> dict:
    """Admin: goods came back. `lines` = [{line_id, qty}]; qty ≤ the confirmed (else ordered)
    quantity of that line. Value = Σ qty × the line's confirmed unit price (else the ordered
    one), Decimal 3 dp. Writes one 'returned' event (lines, reason, value) and, when the
    column exists, adds the value to shop_orders.returned_bhd. The status is untouched."""
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
        cap = _i(ln.get("qty_confirmed")) if ln.get("qty_confirmed") is not None else _i(ln.get("qty"))
        if qty <= 0 or qty > cap:
            raise shop.ShopError(f"Return quantity for {ln['item_code']} must be between 1 and {cap}.")
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
        prev = q3(o.get("returned_bhd"))
        try:
            client.table("shop_orders").update({"returned_bhd": float(q3(prev + total)), "updated_at": _iso()}) \
                .eq("id", order_id).execute()
        except Exception as e:  # noqa: BLE001 — the event is the record; the column is a convenience
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


def focus_recon(limit: int = 300) -> dict:
    """Delivered orders against v_sales by Focus invoice: missing invoice, invoice not found,
    salesman mismatch, amount mismatch. Reads v_shop_focus_recon (service role only — it joins
    customer names, which never leave through here). Empty + hint before the migration."""
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
        flags = [k for k in ("missing_invoice", "invoice_not_found", "salesman_mismatch", "amount_mismatch") if r.get(k)]
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
    return {"rows": out, "count": len(out), "issues": issues}
