"""Shop order notifications — salesman alert, owner copy, customer confirmation, status updates.

Channels (all reuse existing modules; each one is inert until its keys are set on the host):
  * email      → app.emailer.send_html   (Resend → Brevo → SMTP)
  * telegram   → app.notify.send_telegram (owner channel)
  * whatsapp   → the CUSTOMER taps a wa.me link to the salesman (assist mode, always works);
                 app.whatsapp.send_text is tried too, but only succeeds inside an open 24 h
                 customer-initiated session window (Meta policy), so it never carries the routing.
Everything user-supplied is html.escape()d before it reaches an email body.
"""
from __future__ import annotations

import html
import logging
import os
import urllib.parse
from datetime import datetime, timezone

from app.config import settings as cfg
from app.customer_contacts import wa_digits

log = logging.getLogger(__name__)

PURPLE = "#6d28d9"
INK = "#1a1430"
MUTED = "#6b6480"


def _base() -> str:
    return (os.getenv("APP_BASE_URL", "") or "").rstrip("/")


def _money(x) -> str:
    try:
        return f"BHD {float(x):,.3f}"
    except (TypeError, ValueError):
        return "—"


def _status_url(o: dict) -> str:
    return f"{_base()}/o/{o.get('token')}" if _base() else f"/o/{o.get('token')}"


def _lines(o: dict) -> list[dict]:
    return o.get("lines") or []


# ── plain-text summaries (WhatsApp / Telegram) ────────────────────────────────

def order_text(o: dict, audience: str = "salesman") -> str:
    """Compact order summary. audience: 'salesman' (full), 'customer' (their copy)."""
    parts = []
    if audience == "salesman":
        parts.append(f"New order {o.get('order_no')} from {o.get('customer_name')}"
                     + (f" ({o.get('customer_shop')})" if o.get("customer_shop") else "")
                     + (f", {o.get('customer_area')}" if o.get("customer_area") else ""))
        parts.append(f"Phone: {o.get('customer_phone')}")
    else:
        parts.append(f"Hello YQ Bahrain, this is my order {o.get('order_no')}:")
    for ln in _lines(o):
        tag = " (backorder)" if ln.get("backorder") else ""
        parts.append(f"• {ln.get('qty')} × {ln.get('item_code')} @ {float(ln.get('unit_price_bhd') or 0):.3f}"
                     f" = {float(ln.get('line_total_bhd') or 0):.3f}{tag}")
    if float(o.get("discount_bhd") or 0) > 0:
        parts.append(f"Discount: -{float(o.get('discount_bhd')):.3f}")
    if float(o.get("delivery_bhd") or 0) > 0:
        parts.append(f"Delivery: {float(o.get('delivery_bhd')):.3f}")
    parts.append(f"Total: {_money(o.get('total_bhd'))} ({o.get('units_count')} units)")
    if o.get("note"):
        parts.append(f"Note: {o.get('note')}")
    parts.append(f"Status: {_status_url(o)}")
    return "\n".join(parts)


def wa_url(digits: str | None, text: str) -> str | None:
    d = wa_digits(digits) if digits else None
    if not d:
        return None
    return f"https://wa.me/{d}?text={urllib.parse.quote(text)}"


def customer_to_salesman_wa_url(o: dict) -> str | None:
    """The customer's one-tap WhatsApp to the salesman (falls back to the owner's number)."""
    sm = o.get("salesman") or {}
    number = sm.get("whatsapp") or sm.get("phone") or cfg.wa_human_number
    return wa_url(number, order_text(o, audience="customer"))


def salesman_to_customer_wa_url(o: dict, status: str | None = None) -> str | None:
    """Prefilled message the salesman taps to update the customer."""
    name = (o.get("customer_name") or "").split(" ")[0] or "there"
    st = status or o.get("status") or "received"
    msgs = {
        "new": f"Hello {name}, thank you for your order {o.get('order_no')} with YQ Bahrain. I'm checking availability and will confirm shortly.",
        "confirmed": f"Hello {name}, your order {o.get('order_no')} is confirmed. Total {_money(o.get('total_bhd'))}. I'll let you know when it's packed.",
        "packed": f"Hello {name}, your order {o.get('order_no')} is packed and on its way.",
        "delivered": f"Hello {name}, your order {o.get('order_no')} has been delivered. Thank you for choosing YQ Bahrain!",
        "cancelled": f"Hello {name}, your order {o.get('order_no')} has been cancelled. Please message me if this is unexpected.",
    }
    return wa_url(o.get("customer_phone"), msgs.get(st, msgs["new"]) + f"\n{_status_url(o)}")


# ── HTML email ────────────────────────────────────────────────────────────────

def order_html(o: dict, heading: str, intro: str, show_contact: bool = True) -> str:
    e = html.escape
    rows = "".join(
        f"<tr><td style='padding:6px 8px;border-bottom:1px solid #eee'>{e(str(ln.get('item_code') or ''))}"
        f"<div style='color:{MUTED};font-size:12px'>{e(str(ln.get('display_name') or ''))}"
        f"{' · <b>backorder</b>' if ln.get('backorder') else ''}</div></td>"
        f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{int(ln.get('qty') or 0)}</td>"
        f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{float(ln.get('unit_price_bhd') or 0):.3f}</td>"
        f"<td style='padding:6px 8px;border-bottom:1px solid #eee;text-align:right'>{float(ln.get('line_total_bhd') or 0):.3f}</td></tr>"
        for ln in _lines(o))
    contact = ""
    if show_contact:
        contact = (f"<p style='margin:0 0 14px'><b>{e(str(o.get('customer_name') or ''))}</b>"
                   f"{' · ' + e(str(o.get('customer_shop'))) if o.get('customer_shop') else ''}"
                   f"{' · ' + e(str(o.get('customer_area'))) if o.get('customer_area') else ''}<br>"
                   f"<a href='tel:{e(str(o.get('customer_phone') or ''))}'>{e(str(o.get('customer_phone') or ''))}</a>"
                   f"{' · ' + e(str(o.get('customer_email'))) if o.get('customer_email') else ''}</p>")
    note = f"<p style='margin:0 0 14px;color:{MUTED}'>Note: {e(str(o.get('note')))}</p>" if o.get("note") else ""
    totals = (f"<table style='margin-top:10px;margin-left:auto;font-size:14px'>"
              f"<tr><td style='padding:2px 8px;color:{MUTED}'>Subtotal</td><td style='text-align:right'>{_money(o.get('subtotal_bhd'))}</td></tr>"
              + (f"<tr><td style='padding:2px 8px;color:{MUTED}'>Discount</td><td style='text-align:right'>-{_money(o.get('discount_bhd'))}</td></tr>" if float(o.get('discount_bhd') or 0) > 0 else "")
              + (f"<tr><td style='padding:2px 8px;color:{MUTED}'>Delivery</td><td style='text-align:right'>{_money(o.get('delivery_bhd'))}</td></tr>" if float(o.get('delivery_bhd') or 0) > 0 else "")
              + f"<tr><td style='padding:6px 8px;font-weight:700'>Total</td><td style='text-align:right;font-weight:700;color:{PURPLE}'>{_money(o.get('total_bhd'))}</td></tr></table>")
    sm = o.get("salesman") or {}
    sm_line = f"<p style='margin:0 0 14px;color:{MUTED}'>Salesman: <b style='color:{INK}'>{e(str(sm.get('name') or o.get('salesman_name') or 'YQ Bahrain'))}</b></p>" if (sm or o.get("salesman_name")) else ""
    return f"""<!doctype html><html><body style="margin:0;background:#faf9fc;font-family:Inter,Segoe UI,Arial,sans-serif;color:{INK}">
<div style="max-width:640px;margin:0 auto;padding:24px 16px">
  <div style="background:{PURPLE};color:#fff;border-radius:14px 14px 0 0;padding:16px 20px">
    <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.85">YQ Bahrain · Shop</div>
    <div style="font-size:20px;font-weight:700;margin-top:4px">{e(heading)}</div>
  </div>
  <div style="background:#fff;border:1px solid #e9e5f3;border-top:0;border-radius:0 0 14px 14px;padding:20px">
    <p style="margin:0 0 14px">{e(intro)}</p>
    {sm_line}{contact}{note}
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <thead><tr style="color:{MUTED};font-size:12px;text-transform:uppercase;letter-spacing:.04em">
        <th style="text-align:left;padding:6px 8px">Item</th><th style="text-align:right;padding:6px 8px">Qty</th>
        <th style="text-align:right;padding:6px 8px">Unit</th><th style="text-align:right;padding:6px 8px">Total</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    {totals}
    <p style="margin:18px 0 0;font-size:13px"><a href="{e(_status_url(o))}" style="color:{PURPLE}">Order status page</a></p>
  </div>
  <p style="color:{MUTED};font-size:11px;text-align:center;margin-top:14px">YQ Bahrain W.L.L · Orders are confirmed by your salesman; prices as per the current trade price list.</p>
</div></body></html>"""


# ── senders ───────────────────────────────────────────────────────────────────

def _email(subject: str, body_html: str, to: str | None) -> dict:
    if not to:
        return {"sent": False, "emailed": False, "reason": "no_recipient"}
    try:
        from app.emailer import send_html
        r = send_html(subject, body_html, to=to)
        r["sent"] = bool(r.get("emailed"))
        return r
    except Exception as e:  # noqa: BLE001
        return {"sent": False, "emailed": False, "reason": f"{type(e).__name__}: {e}"[:200]}


def _telegram(text: str) -> dict:
    try:
        from app.notify import send_telegram
        return {"sent": bool(send_telegram(text))}
    except Exception as e:  # noqa: BLE001
        return {"sent": False, "reason": f"{type(e).__name__}: {e}"[:200]}


def _whatsapp_cloud(number: str | None, text: str) -> dict:
    if not number or not (cfg.wa_access_token and cfg.wa_phone_number_id):
        return {"sent": False, "reason": "cloud_api_not_configured"}
    try:
        from app.whatsapp import send_text
        r = send_text(wa_digits(number) or number, text)
        ok = bool(r) if not isinstance(r, dict) else bool(r.get("ok", r))
        return {"sent": ok, "detail": str(r)[:160]}
    except Exception as e:  # noqa: BLE001
        return {"sent": False, "reason": f"{type(e).__name__}: {e}"[:200]}


def notify_new_order(order_id: int) -> dict:
    """Fan out a new order. Safe to run in a background task; never raises."""
    from app.database import get_client
    from app.shop import get_order
    result: dict = {"at": datetime.now(timezone.utc).isoformat()}
    try:
        o = get_order(order_id)
        if not o:
            return {"error": "order not found"}
        sm = o.get("salesman") or {}
        subject = f"YQ Shop · New order {o['order_no']} — {o.get('customer_name')} · {_money(o.get('total_bhd'))}"
        text = order_text(o, audience="salesman")
        # salesman + owner copy (one send; both see the same order)
        recipients = []
        if sm.get("email") and sm.get("notify_email", True):
            recipients.append(sm["email"])
        owner = os.getenv("ALERT_EMAIL_TO", "")
        for a in owner.split(","):
            a = a.strip()
            if a and a.lower() not in [r.lower() for r in recipients]:
                recipients.append(a)
        body = order_html(o, f"New order {o['order_no']}",
                          f"A customer ordered from the shared catalog. {'Assigned to ' + sm['name'] + '.' if sm else 'No salesman assigned — please pick it up.'}")
        result["email"] = _email(subject, body, ",".join(recipients))
        # customer confirmation
        if o.get("customer_email"):
            cbody = order_html(o, f"Thank you — order {o['order_no']} received",
                               "We have your order. Your salesman will confirm availability and delivery shortly.",
                               show_contact=False)
            result["customer_email"] = _email(f"YQ Bahrain · Order {o['order_no']} received", cbody, o["customer_email"])
        result["telegram"] = _telegram(text)
        if sm and sm.get("notify_whatsapp", True):
            result["whatsapp"] = _whatsapp_cloud(sm.get("whatsapp") or sm.get("phone"), text)
        get_client().table("shop_orders").update({
            "notify_result": result, "notified_at": datetime.now(timezone.utc).isoformat()}).eq("id", order_id).execute()
    except Exception as e:  # noqa: BLE001
        log.warning("notify_new_order(%s) failed: %s", order_id, e)
        result["error"] = f"{type(e).__name__}: {e}"[:200]
    return result


def notify_status(order_id: int, status: str, note: str | None = None) -> dict:
    """Customer email on a status change (when they left an email). Never raises."""
    from app.shop import get_order
    result: dict = {}
    try:
        o = get_order(order_id)
        if not o or not o.get("customer_email"):
            return {"customer_email": {"emailed": False, "reason": "no_customer_email"}}
        titles = {"confirmed": "Your order is confirmed", "packed": "Your order is packed",
                  "delivered": "Your order has been delivered", "cancelled": "Your order was cancelled"}
        intro = titles.get(status, f"Order status: {status}") + (f" — {note}" if note else "")
        body = order_html(o, f"{titles.get(status, status.title())} · {o['order_no']}", intro, show_contact=False)
        result["customer_email"] = _email(f"YQ Bahrain · Order {o['order_no']} {status}", body, o["customer_email"])
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"[:200]
    return result
