"""Shop order notifications — salesman alert, owner copy, customer confirmation, status updates.

Channels (all reuse existing modules; each one is inert until its keys are set on the host):
  * email      → app.emailer.send_html   (Resend → Brevo → SMTP, per recipient)
  * telegram   → app.notify.send_telegram (owner channel)
  * whatsapp   → the CUSTOMER taps a wa.me link to the salesman (assist mode, always works);
                 app.whatsapp.send_text is tried too, but only succeeds inside an open 24 h
                 customer-initiated session window (Meta policy), so it never carries the routing.
Everything user-supplied is html.escape()d before it reaches an email body.

notify_result (stored on shop_orders, read by the portal's Notifications panel) is a flat map of
channel → {"sent": bool, "reason"?: str, ...} plus a little metadata:
  email_rep · email_owner · customer_email · telegram · whatsapp   (24-Sep-2026: the rep and the
  owner get SEPARATE sends, so Resend's testing-mode 403 on one address no longer loses the other)
  at · attempts[] (ISO timestamps, one per fan-out — shop_jobs.notify_retry stops at 3) · recipients[]
Rows written before 24-Sep carry a single `email` key; all_channels_failed() reads both shapes, and
notify_failed() is the one "nobody was told" rule shared by notify_retry, shop.list_orders and the badge.
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
    from app.shop import market_base   # lazy: app.shop imports this module for the wa.me helpers
    base = market_base() or _base()
    return f"{base}/o/{o.get('token')}" if base else f"/o/{o.get('token')}"


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


def customer_to_salesman_email_url(o: dict) -> str | None:
    """A mailto: link the CUSTOMER taps — opens their own mail app with the salesman's
    address, subject and the full order already filled in. Costs nothing and needs no
    provider key (the same 'the human's tap IS the send' rule as the wa.me link).
    None when the salesman has no email on file — we never expose any other address here."""
    sm = o.get("salesman") or {}
    to = (sm.get("email") or "").strip()
    if not to or "@" not in to:
        return None
    shop = o.get("customer_shop") or o.get("customer_name") or ""
    subject = f"Order {o.get('order_no')}" + (f" - {shop}" if shop else "")
    body = (order_text(o, audience="customer")
            .replace("•", "-").replace("×", "x").replace("→", "->"))
    q = urllib.parse.quote
    return f"mailto:{q(to, safe='@')}?subject={q(subject)}&body={q(body)}"


def _changes_text(o: dict) -> str:
    """One line per changed or removed line after confirm-with-changes; empty when none."""
    out = []
    for ln in _lines(o):
        st = ln.get("line_status") or "ok"
        if st == "removed":
            out.append(f"- {ln.get('item_code')}: not available this time")
        elif st in ("changed", "backorder") and ln.get("qty_confirmed") is not None \
                and int(ln.get("qty_confirmed") or 0) != int(ln.get("qty") or 0):
            out.append(f"- {ln.get('item_code')}: {ln.get('qty')} -> {ln.get('qty_confirmed')}")
    return "\n".join(out)


def salesman_to_customer_wa_url(o: dict, status: str | None = None) -> str | None:
    """Prefilled message the salesman taps to update the customer at each stage. The merchant's
    tracking page uses the same words (Received / Confirmed / Preparing / On the way / Delivered)."""
    name = (o.get("customer_name") or "").split(" ")[0] or "there"
    st = status or o.get("status") or "new"
    no = o.get("order_no")
    total = o.get("total_confirmed_bhd") if o.get("total_confirmed_bhd") is not None else o.get("total_bhd")
    eta = f" Expected delivery: {o['expected_delivery']}." if o.get("expected_delivery") else ""
    changes = _changes_text(o)
    confirmed = (f"Hello {name}, your order {no} is confirmed. Total {_money(total)}.{eta}"
                 + (f"\nChanges to your order:\n{changes}" if changes else ""))
    msgs = {
        "new": f"Hello {name}, thank you for your order {no} with YQ Bahrain. I'm checking availability and will confirm shortly.",
        "confirmed": confirmed,
        "packed": f"Hello {name}, your order {no} is being prepared at our warehouse.{eta}",
        "out_for_delivery": f"Hello {name}, your order {no} is on its way to you.",
        "delivered": f"Hello {name}, your order {no} has been delivered. Thank you for choosing YQ Bahrain!",
        "cancelled": f"Hello {name}, your order {no} has been cancelled. Please message me if this is unexpected.",
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


# The channels that tell YQ's own people about an order (the customer's confirmation copy is not
# one of them: a merchant who got their receipt while nobody at YQ was told is still "not notified").
INTERNAL_CHANNELS = ("email_rep", "email_owner", "email", "telegram", "whatsapp")
MAX_NOTIFY_ATTEMPTS = 3      # first fan-out + up to two shop_jobs.notify_retry re-runs
_ATTEMPTS_KEPT = 5           # timestamps kept in notify_result.attempts (the count is what matters)
NOTIFY_GRACE_MIN = 15        # a null notify_result younger than this may still be the background task
RETRY_TAG = "Re-sent (first alert did not reach you)"


def channel_results(result) -> dict[str, bool]:
    """{channel: delivered} for every internal channel present in a notify_result (either shape)."""
    if not isinstance(result, dict):
        return {}
    return {k: bool(v.get("sent")) for k, v in result.items()
            if k in INTERNAL_CHANNELS and isinstance(v, dict)}


def all_channels_failed(result) -> bool:
    """True when no internal channel delivered: a missing result, an error before any send, or a
    result whose every rep/owner channel says sent=false. Drives the portal's "Not notified" badge
    and shop_jobs.notify_retry."""
    flags = channel_results(result)
    return not any(flags.values())


def attempt_count(result) -> int:
    """Fan-outs so far. Rows written before attempts[] existed count as one."""
    if not isinstance(result, dict):
        return 0
    n = len(result.get("attempts") or [])
    return n if n else 1


def _age_minutes(created_at, now: datetime | None = None) -> int | None:
    if not created_at:
        return None
    try:
        d = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return int(((now or datetime.now(timezone.utc)) - d).total_seconds() // 60)


def notify_failed(result, created_at=None, now: datetime | None = None) -> bool:
    """The one definition of "nobody at YQ was told" shared by the portal's list rows
    (shop.list_orders → notify_failed), the badge and shop_jobs.notify_retry: every internal
    channel failed, or the fan-out raised before any send (error only), or there is no result at
    all and the order is older than NOTIFY_GRACE_MIN (a younger null may still be the background
    task). The order's status is the caller's business — the badge shows it for 'new' only."""
    if result is None:
        age = _age_minutes(created_at, now)
        return age is None or age >= NOTIFY_GRACE_MIN
    return all_channels_failed(result)


def _placed_ago(o: dict, now: datetime) -> str:
    """'placed N h ago' / 'placed N min ago' for a re-sent alert (never claims a cadence)."""
    age = _age_minutes(o.get("created_at"), now) or 0
    return f"placed {age // 60} h ago" if age >= 60 else f"placed {age} min ago"


def _previous_result(client, order_id: int) -> dict:
    """The stored notify_result alone (a light read for the except path, so a broken get_order()
    never wipes the attempts history and notify_retry still stops at MAX_NOTIFY_ATTEMPTS)."""
    try:
        rows = client.table("shop_orders").select("notify_result").eq("id", order_id).limit(1).execute().data or []
        prev = rows[0].get("notify_result") if rows else None
        return prev if isinstance(prev, dict) else {}
    except Exception as e:  # noqa: BLE001
        log.debug("notify_new_order(%s): previous result unreadable: %s", order_id, e)
        return {}


def _with_attempts(result: dict, prev: dict, now: str) -> None:
    attempts = list(prev.get("attempts") or ([prev["at"]] if prev.get("at") else []))
    attempts.append(now)
    result["attempts"] = attempts[-_ATTEMPTS_KEPT:]
    result["attempt"] = len(attempts)


def owner_addresses(exclude: str | None = None) -> list[str]:
    """ALERT_EMAIL_TO as a list, minus the rep's own address so nobody gets the same order twice."""
    out: list[str] = []
    for a in (os.getenv("ALERT_EMAIL_TO", "") or "").split(","):
        a = a.strip()
        if a and a.lower() != (exclude or "").strip().lower() and a.lower() not in [x.lower() for x in out]:
            out.append(a)
    return out


def notify_new_order(order_id: int, retry: bool = False) -> dict:
    """Fan out a new order: a copy to the rep, a separate copy to the owner addresses, the
    customer's confirmation, Telegram and (when Cloud API is set) WhatsApp. Every channel is
    recorded on its own in notify_result. With retry=True (shop_jobs.notify_retry) channels that
    already delivered on an earlier attempt are kept, not re-sent — the merchant never gets a
    second "order received" mail. Safe to run in a background task; never raises."""
    from app.database import get_client
    from app.shop import get_order
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    # The attempt is counted before anything can fail, so a broken order read still moves the
    # retry counter and notify_retry stops at MAX_NOTIFY_ATTEMPTS.
    result: dict = {"at": now, "attempts": [now], "attempt": 1}
    prev: dict | None = None
    try:
        o = get_order(order_id)
        if not o:
            return {"error": "order not found"}
        prev = o.get("notify_result") if isinstance(o.get("notify_result"), dict) else {}
        _with_attempts(result, prev, now)

        def kept(channel: str) -> dict | None:
            """On a retry, a channel that delivered before is carried over untouched."""
            r = prev.get(channel) if retry else None
            return dict(r, kept=True) if isinstance(r, dict) and r.get("sent") else None

        sm = o.get("salesman") or {}
        placed_by_staff = o.get("source") == "salesman"   # the salesman placed it himself — do not alert him
        # A re-send days later must not read like a fresh order: say so in the subject and the intro.
        resent = f"{RETRY_TAG} · {_placed_ago(o, now_dt)}" if retry else ""
        subject = (f"YQ Shop · {resent} · New order {o['order_no']}" if resent else f"YQ Shop · New order {o['order_no']}") \
            + f" — {o.get('customer_name')} · {_money(o.get('total_bhd'))}"
        text = (f"{resent}\n" if resent else "") + order_text(o, audience="salesman")
        # rep copy — his order to confirm
        rep_to = sm.get("email") if (sm.get("email") and sm.get("notify_email", True) and not placed_by_staff) else None
        owners = owner_addresses(exclude=rep_to)
        result["recipients"] = ([rep_to] if rep_to else []) + owners
        if rep_to:
            body = order_html(o, f"New order {o['order_no']}",
                              (f"{resent}. " if resent else "")
                              + "A customer ordered from the marketplace and this order is yours. "
                              "Please confirm availability and delivery.")
            result["email_rep"] = kept("email_rep") or _email(subject, body, rep_to)
        # owner copy — what came in and who has it
        if owners:
            body = order_html(o, f"New order {o['order_no']}",
                              (f"{resent}. " if resent else "")
                              + (f"Placed by {sm.get('name') or o.get('placed_by')} for the shop." if placed_by_staff else
                                 f"A customer ordered from the shared catalog. {'Assigned to ' + sm['name'] + '.' if sm else 'No salesman assigned — please pick it up.'}"))
            result["email_owner"] = kept("email_owner") or _email(subject, body, ",".join(owners))
        if not rep_to and not owners:
            result["email"] = {"sent": False, "emailed": False, "reason": "no_recipient (rep has no email; ALERT_EMAIL_TO unset)"}
        # customer confirmation
        if o.get("customer_email"):
            cbody = order_html(o, f"Thank you — order {o['order_no']} received",
                               "We have your order. Your salesman will confirm availability and delivery shortly.",
                               show_contact=False)
            result["customer_email"] = kept("customer_email") or \
                _email(f"YQ Bahrain · Order {o['order_no']} received", cbody, o["customer_email"])
        if not sm:
            # Nobody owns this order yet: the admins must assign it (portal → Shop Orders → queue).
            text = "UNASSIGNED marketplace order — assign it in the portal.\n" + text
            if _base():
                text += f"\nQueue: {_base()}/shop-orders?queue=1"
        elif not placed_by_staff:
            text = f"Assigned to {sm.get('name')}.\n" + text
        result["telegram"] = kept("telegram") or _telegram(text)
        if sm and sm.get("notify_whatsapp", True) and not placed_by_staff:
            # the rep's own copy: the plain order, without the owner-channel "Assigned to" prefix
            result["whatsapp"] = kept("whatsapp") or \
                _whatsapp_cloud(sm.get("whatsapp") or sm.get("phone"),
                                (f"{resent}\n" if resent else "") + order_text(o, audience="salesman"))
    except Exception as e:  # noqa: BLE001
        log.warning("notify_new_order(%s) failed: %s", order_id, e)
        result["error"] = f"{type(e).__name__}: {e}"[:200]
        if prev is None:
            # get_order() itself raised: the stored history was never read, so read it the light
            # way — otherwise every failed run would start the attempts list over at 1.
            _with_attempts(result, _previous_result(get_client(), order_id), now)
    # Persist even a failed attempt: the attempt count is what stops notify_retry after 3 runs.
    try:
        get_client().table("shop_orders").update({
            "notify_result": result, "notified_at": datetime.now(timezone.utc).isoformat()}).eq("id", order_id).execute()
    except Exception as e:  # noqa: BLE001
        log.warning("notify_new_order(%s): could not store notify_result: %s", order_id, e)
        result.setdefault("error", f"store: {type(e).__name__}: {e}"[:200])
    return result


def notify_assigned(order_id: int) -> dict:
    """Tell the newly assigned salesman (email if on file) and the owner channel. Never raises."""
    from app.shop import get_order
    result: dict = {}
    try:
        o = get_order(order_id)
        if not o:
            return {"error": "order not found"}
        sm = o.get("salesman") or {}
        text = f"Order {o['order_no']} assigned to {sm.get('name') or o.get('salesman_name')}.\n" + order_text(o, audience="salesman")
        if sm.get("email") and sm.get("notify_email", True):
            body = order_html(o, f"Order {o['order_no']} is yours",
                              "This marketplace order has been assigned to you. Please confirm availability and delivery.")
            result["email"] = _email(f"YQ Shop · Order {o['order_no']} assigned to you — {_money(o.get('total_bhd'))}", body, sm["email"])
        result["telegram"] = _telegram(text)
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"[:200]
    return result


def notify_customer_cancel(order_id: int) -> dict:
    """The merchant cancelled from the status page: tell the salesman and the owner. Never raises."""
    from app.shop import get_order
    result: dict = {}
    try:
        o = get_order(order_id)
        if not o:
            return {"error": "order not found"}
        sm = o.get("salesman") or {}
        reason = o.get("cancel_reason") or "no reason given"
        text = f"Order {o['order_no']} was CANCELLED by the customer ({reason}).\n" + order_text(o, audience="salesman")
        if sm.get("email") and sm.get("notify_email", True):
            body = order_html(o, f"Order {o['order_no']} cancelled by the customer", f"Reason: {reason}")
            result["email"] = _email(f"YQ Shop · Order {o['order_no']} cancelled by the customer", body, sm["email"])
        result["telegram"] = _telegram(text)
    except Exception as e:  # noqa: BLE001
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
        titles = {"confirmed": "Your order is confirmed", "packed": "Your order is being prepared",
                  "out_for_delivery": "Your order is on its way",
                  "delivered": "Your order has been delivered", "cancelled": "Your order was cancelled"}
        intro = titles.get(status, f"Order status: {status}") + (f" — {note}" if note else "")
        body = order_html(o, f"{titles.get(status, status.title())} · {o['order_no']}", intro, show_contact=False)
        result["customer_email"] = _email(f"YQ Bahrain · Order {o['order_no']} {status}", body, o["customer_email"])
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"[:200]
    return result
