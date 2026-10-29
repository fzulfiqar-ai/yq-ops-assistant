"""YQ Assist v2 support (29-Oct-2026): the tickets the chat raises, their timeline, alerts, SLA and retention.

A merchant reports a damaged, wrong or missing item, something not working, a delivery problem or a return, or
asks for a call back or an account. The ticket card runs its steps in the page; this module checks and stores
the one request it ends with. The owner's rules (29-Oct-2026):
  * the ticket belongs to the salesman on that order (else the shop's rep, else the storefront rep, else the
    office); the owner watches every ticket and can reassign;
  * the merchant is promised the next step only: "Report T-... is with {rep}, who will contact you" - no time
    window, no replacement, refund or credit (there is no approved policy for those);
  * chats are kept 180 days, tickets and their photos 730 days after closing (shop_ai_retention_days,
    shop_ticket_keep_days);
  * the owner hears of each new ticket on Telegram, and sees the open ones in the Command Centre.

Security: an order is named by its TOKEN, the capability the device already holds (the status page's own);
a line by its position in that order plus its code; a quantity never above what was delivered, less what was
returned or is already reported. A report without an order (bought on a salesman's visit) names catalogue
items only. Photos are re-encoded (no location or camera data survives) into a PRIVATE bucket; staff see them
through one-hour signed links. Nothing here reaches a model.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from app import shop

log = logging.getLogger(__name__)

KINDS = ("damaged", "wrong_item", "missing", "not_working", "delivery", "return", "other", "callback", "account")
STATUSES = ("new", "in_progress", "waiting_shop", "resolved", "rejected")
OPEN = ("new", "in_progress", "waiting_shop")
RESOLUTIONS = ("replaced", "credit_note", "returned", "answered", "not_valid", "other")
_AFTER_DELIVERY = ("damaged", "wrong_item", "missing", "not_working", "return")
# what an order's status allows (a cancelled order can still ask for a call back)
_KINDS_FOR = {"new": ("delivery", "other", "callback"),
              "confirmed": ("delivery", "other", "callback"), "packed": ("delivery", "other", "callback"),
              "out_for_delivery": ("delivery", "other", "callback"),
              "delivered": _AFTER_DELIVERY + ("other", "callback"),
              "cancelled": ("other", "callback")}
BUCKET = "assist-tickets"          # PRIVATE: paths are stored, staff get one-hour signed links
MAX_PHOTOS = 3
PHOTO_MAX_BYTES = 1_600_000
MAX_LINES = 10
_SIGNED_TTL = 3600
_DATA_URL = re.compile(r"^data:image/(jpeg|jpg|png|webp);base64,([A-Za-z0-9+/=\s]+)$")


class TicketError(shop.ShopError):
    """Something the merchant can fix (HTTP 400)."""


def _vals() -> dict:
    return shop.context().get("settings") or {}


def enabled() -> bool:
    return str(_vals().get("shop_ai_support_enabled", "")).strip() in ("1", "true", "yes")


def _i(v, default: int = 0) -> int:
    return shop._i(v, default)


# ── the order and its lines, as the device holds them ──────────────────────────────────────────

def _reported_open(order_id: int, client) -> dict[int, int]:
    """{line_id: units already on an open ticket of this order}: a line cannot be reported twice over."""
    out: dict[int, int] = {}
    rows = (client.table("shop_tickets").select("lines,status").eq("order_id", order_id).execute().data or [])
    for r in rows:
        if r.get("status") not in OPEN:
            continue
        for x in r.get("lines") or []:
            lid = _i((x or {}).get("line_id"))
            if lid:
                out[lid] = out.get(lid, 0) + max(0, _i((x or {}).get("qty")))
    return out


def check_lines(order: dict | None, raw_lines: list, kind: str, cat: dict, client=None) -> list[dict]:
    """The reported lines, checked. With an order: each {index, item_code, qty} must be that order's line at
    that position with that code, and qty at most what was delivered (or confirmed, before delivery), less what
    was returned or is already reported. Without: catalogue codes, 1 to 999 each. A kind with no line
    (a call back, an account, a delivery problem) needs none."""
    from app.shop_heart import qty_confirmed_eff, qty_delivered_eff
    from app.shop_pipeline import returned_so_far
    raw_lines = [x for x in (raw_lines or []) if isinstance(x, dict)][:MAX_LINES]
    if not raw_lines:
        if kind in _AFTER_DELIVERY:
            raise TicketError("Pick the item and how many pieces.")
        return []
    out: list[dict] = []
    if order is None:
        for x in raw_lines:
            code = str(x.get("item_code") or "").strip()
            it = (cat.get("by_code") or {}).get(code.upper())
            qty = _i(x.get("qty"))
            if not it:
                raise TicketError(f"{code or 'That item'} is not in the catalogue.")
            if not 1 <= qty <= 999:
                raise TicketError(f"Check the number of pieces for {it['item_code']}.")
            out.append({"line_id": None, "item_code": it["item_code"], "qty": qty})
        return out
    lines = order.get("lines") or []
    done = returned_so_far(order.get("events"))
    taken = _reported_open(order["id"], client) if client is not None else {}
    for x in raw_lines:
        idx = _i(x.get("index"), -1)
        code = str(x.get("item_code") or "").strip()
        if not 0 <= idx < len(lines) or str(lines[idx].get("item_code") or "").upper() != code.upper():
            raise TicketError("This order changed a moment ago. Open it again and pick the item.")
        ln = lines[idx]
        got = qty_delivered_eff(ln, order.get("status"))
        cap = (got if got is not None else qty_confirmed_eff(ln)) - done.get(ln["id"], 0) - taken.get(ln["id"], 0)
        qty = _i(x.get("qty"))
        if qty < 1 or qty > max(cap, 0):
            raise TicketError(f"{ln.get('item_code')}: at most {max(cap, 0)} pieces can be reported on this order.")
        out.append({"line_id": ln["id"], "item_code": ln.get("item_code"), "qty": qty})
    return out


# ── who owns it ────────────────────────────────────────────────────────────────────────────

def _customer_for_device(device_id: str | None, client) -> dict | None:
    if not device_id:
        return None
    try:
        rows = (client.table("shop_customers").select("id,salesman_id,sticky_salesman_id")
                .contains("device_ids", json.dumps([device_id])).limit(1).execute().data or [])   # jsonb @> '["dev"]'
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        log.debug("support: device lookup failed: %s", e)
        return None


def owner_of(order: dict | None, device_id: str | None, referral_code: str | None, client) -> tuple[int | None, int | None]:
    """(salesman_id, customer_id): the order's rep and shop; else the shop this device belongs to and its rep;
    else the storefront rep of the link the merchant came by; else nobody (the office)."""
    if order:
        return order.get("salesman_id"), order.get("customer_id")
    cust = _customer_for_device(device_id, client)
    if cust and (cust.get("salesman_id") or cust.get("sticky_salesman_id")):
        return cust.get("salesman_id") or cust.get("sticky_salesman_id"), cust.get("id")
    ref = (referral_code or "").strip().lower()
    if ref:
        for s in shop.context().get("salesmen") or []:
            if str(s.get("referral_code") or "").lower() == ref:
                return s.get("id"), (cust or {}).get("id")
    return None, (cust or {}).get("id")


# ── photos: re-encoded, private ───────────────────────────────────────────────────────────

def reencode(data_url: str) -> bytes:
    """One photo as a fresh JPEG (long side at most 1600 px): nothing of the original file survives, so no
    location, camera or other metadata reaches the bucket. Raises TicketError for anything that is not a photo."""
    m = _DATA_URL.match(str(data_url or "").strip())
    if not m or len(m.group(2)) * 3 // 4 > PHOTO_MAX_BYTES:
        raise TicketError("Send JPEG, PNG or WebP photos under 1.5 MB each.")
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(m.group(2))))
        img = img.convert("RGB")
        img.thumbnail((1600, 1600))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()
    except TicketError:
        raise
    except Exception as e:  # noqa: BLE001
        raise TicketError("That photo could not be read. Try another one.") from e


def _store_photos(ticket_id: int, photos: list[bytes]) -> list[str]:
    paths: list[str] = []
    if not photos:
        return paths
    client = shop.get_client()
    try:
        client.storage.create_bucket(BUCKET, options={"public": False})
    except Exception:  # noqa: BLE001 — it exists already
        pass
    for n, data in enumerate(photos, 1):
        path = f"{ticket_id}/{n}.jpg"
        try:
            client.storage.from_(BUCKET).upload(path, data, {"content-type": "image/jpeg", "upsert": "true"})
            paths.append(path)
        except Exception as e:  # noqa: BLE001 — the ticket stands without the photo
            log.warning("support: photo upload failed for ticket %s: %s", ticket_id, e)
    return paths


def signed_photos(paths: list[str]) -> list[str]:
    if not paths:
        return []
    try:
        rows = shop.get_client().storage.from_(BUCKET).create_signed_urls(list(paths), _SIGNED_TTL) or []
        return [r.get("signedURL") or r.get("signedUrl") for r in rows if r.get("signedURL") or r.get("signedUrl")]
    except Exception as e:  # noqa: BLE001
        log.debug("support: signing failed: %s", e)
        return []


# ── create ───────────────────────────────────────────────────────────────────────────────

def _event(client, ticket_id: int, actor: str, event: str, detail: dict | None = None,
           from_status: str | None = None, to_status: str | None = None) -> None:
    try:
        client.table("shop_ticket_events").insert({"ticket_id": ticket_id, "actor": actor, "event": event,
                                                   "from_status": from_status, "to_status": to_status,
                                                   "detail": detail or {}}).execute()
    except Exception as e:  # noqa: BLE001 — the timeline is best effort; the ticket row is the record
        log.debug("support: event %s for %s not stored: %s", event, ticket_id, e)


def _existing(client, device_id: str, client_ticket_id: str) -> dict | None:
    rows = (client.table("shop_tickets").select("id,ref,kind,status,salesman_id")
            .eq("device_id", device_id).eq("client_ticket_id", client_ticket_id).limit(1).execute().data or [])
    return rows[0] if rows else None


def _rep_view(salesman_id: int | None, order: dict | None) -> dict | None:
    """The rep the merchant is told about: a first name and the WhatsApp link the status page already shows."""
    sm = None
    if order and order.get("salesman"):
        sm = order["salesman"]
    elif salesman_id:
        sm = shop._salesman_by_id(salesman_id)
    if not sm:
        return None
    from app.shop_notify import wa_url
    from app.customer_contacts import wa_digits
    digits = wa_digits(sm.get("whatsapp") or sm.get("phone")) if (sm.get("whatsapp") or sm.get("phone")) else None
    return {"name": str(sm.get("name") or "").split(" ")[0] or None,
            "whatsapp_url": wa_url(digits, "Hello, about my report on the YQ Marketplace.") if digits else None}


def create_ticket(body: dict, ip: str | None = None) -> dict:
    """The ticket card's one request: checked, stored, the rep and the owner told. Returns what the merchant's
    receipt shows: {ok, ref, kind, status, rep}. A retry of the same card (client_ticket_id) is the same ticket."""
    from app import market_assist as ma
    if not enabled():
        raise TicketError("Problem reports are not open yet. Please tell your representative.")
    kind = str(body.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise TicketError("Pick what went wrong.")
    device_id = ma._clean_text(body.get("device_id"), 64)
    if not device_id:
        raise TicketError("Open this page on your phone to report a problem.")
    client_ticket_id = ma._clean_text(body.get("client_ticket_id"), 64) or None
    client = shop.get_client()
    if client_ticket_id:
        prior = _existing(client, device_id, client_ticket_id)
        if prior:
            return {"ok": True, "ref": prior["ref"], "kind": prior["kind"], "status": prior["status"],
                    "rep": _rep_view(prior.get("salesman_id"), None), "repeat": True}
    token = ma._clean_text(body.get("order_token"), 64)
    order = shop.get_order_by_token(token) if token else None
    if token and not order:
        raise TicketError("That order is not on this phone any more.")
    if order and kind not in _KINDS_FOR.get(str(order.get("status") or ""), ("other", "callback")):
        raise TicketError("That can be reported once the order is delivered." if kind in _AFTER_DELIVERY
                          else "That does not fit this order. Pick another problem or ask for a call back.")
    lines = check_lines(order, body.get("lines"), kind, ma.public_catalog(), client)
    photos = [reencode(p) for p in (body.get("photos") or [])[:MAX_PHOTOS]]
    salesman_id, customer_id = owner_of(order, device_id, body.get("referral_code"), client)
    row = {"kind": kind, "order_id": order["id"] if order else None, "lines": lines,
           "note": ma.mask(ma._clean_text(body.get("note"), 500)) or None, "photos": [],
           "customer_id": customer_id, "salesman_id": salesman_id, "device_id": device_id,
           "session_id": ma._clean_text(body.get("session_id"), 64) or None,
           "lang": "ar" if body.get("lang") == "ar" else "en", "client_ticket_id": client_ticket_id,
           "ip_hash": shop._ip_hash(ip)}
    try:
        t = client.table("shop_tickets").insert(row).execute().data[0]
    except Exception as e:  # noqa: BLE001 — a double tap racing itself: the first one is the ticket
        prior = _existing(client, device_id, client_ticket_id) if client_ticket_id else None
        if not prior:
            raise
        log.info("support: duplicate ticket %s folded: %s", prior["ref"], e)
        return {"ok": True, "ref": prior["ref"], "kind": prior["kind"], "status": prior["status"],
                "rep": _rep_view(prior.get("salesman_id"), order), "repeat": True}
    paths = _store_photos(t["id"], photos)
    if paths:
        client.table("shop_tickets").update({"photos": paths}).eq("id", t["id"]).execute()
    _event(client, t["id"], "merchant", "created", {"lines": len(lines), "photos": len(paths)}, None, "new")
    turn_id = _i(body.get("turn_id")) or None
    if turn_id:
        try:
            client.table("shop_ask_turns").update({"ticket_id": t["id"], "outcome": "ticket"}).eq("id", turn_id) \
                .eq("device_id", device_id).execute()
        except Exception as e:  # noqa: BLE001
            log.debug("support: turn %s not linked: %s", turn_id, e)
    notify_new(t, order, len(paths))
    return {"ok": True, "ref": t["ref"], "kind": kind, "status": t.get("status") or "new",
            "rep": _rep_view(salesman_id, order)}


# ── alerts ───────────────────────────────────────────────────────────────────────────────

KIND_LABEL = {"damaged": "Damaged", "wrong_item": "Wrong item", "missing": "Missing pieces",
              "not_working": "Not working", "delivery": "Delivery problem", "return": "Return",
              "other": "Other problem", "callback": "Call back", "account": "Account / credit"}


def ticket_line(t: dict, order: dict | None = None) -> str:
    """One line about a ticket for the owner and the rep: its reference, kind, items and order; never a phone."""
    items = ", ".join(f"{x.get('item_code')} x{x.get('qty')}" for x in (t.get("lines") or [])[:4])
    bits = [f"{t.get('ref')}: {KIND_LABEL.get(t.get('kind'), t.get('kind'))}"]
    if items:
        bits.append(items)
    if order and order.get("order_no"):
        bits.append(f"order {order['order_no']}")
    return " · ".join(bits)


def notify_new(t: dict, order: dict | None, photos: int = 0) -> None:
    """The owner on Telegram (owner 29-Oct-2026), the rep by e-mail when his notify_email is on. Logged in
    shop_notifications (kind 'ticket' / 'callback'); never raises."""
    from app import shop_notify as sn
    kind = "callback" if t.get("kind") in ("callback", "account") else "ticket"
    sm = shop._salesman_by_id(t.get("salesman_id")) if t.get("salesman_id") else None
    who = (sm or {}).get("name") or "the office (no rep)"
    text = f"YQ Assist · new {('call-back request' if kind == 'callback' else 'ticket')} {ticket_line(t, order)}" \
           f"{f' · {photos} photo(s)' if photos else ''} · with {who}"
    rows = []
    try:
        rows.append(sn.notification_row(kind, "telegram", "owner", sn._telegram(text), order_id=t.get("order_id"),
                                        detail={"ticket": t.get("ref")}))
        if sm and sm.get("notify_email") and sm.get("email"):
            body = f"<p>{text}</p><p>Open the YQ portal → Today → From the chat.</p>"
            rows.append(sn.notification_row(kind, "email", "rep", sn._email(f"YQ Assist {t.get('ref')}", body, sm["email"]),
                                            order_id=t.get("order_id"), to=sm["email"], detail={"ticket": t.get("ref")}))
        sn.log_notifications(rows)
    except Exception as e:  # noqa: BLE001
        log.warning("support: alert for %s failed: %s", t.get("ref"), e)


# ── the rep's Today card and the staff reads ──────────────────────────────────────────────────

def rep_tickets(salesman_id: int | None, limit: int = 20) -> list[dict]:
    """The open tickets a rep owns (None: every open ticket, for the office), oldest first, with the item lines
    and the WhatsApp link to the shop when the order carries its number."""
    q = (shop.get_client().table("shop_tickets")
         .select("id,ref,kind,status,order_id,lines,note,created_at,first_seen_at,salesman_id")
         .in_("status", list(OPEN)).order("created_at").limit(limit))
    if salesman_id is not None:
        q = q.eq("salesman_id", salesman_id)
    return q.execute().data or []


def list_tickets(status: str | None = None, salesman_id: int | None = None, kind: str | None = None,
                 limit: int = 50, offset: int = 0) -> dict:
    q = (shop.get_client().table("shop_tickets")
         .select("id,ref,kind,status,order_id,lines,customer_id,salesman_id,lang,resolution,case_id,"
                 "created_at,updated_at,first_seen_at,resolved_at", count="exact"))
    if status == "open":
        q = q.in_("status", list(OPEN))
    elif status in STATUSES:
        q = q.eq("status", status)
    if salesman_id is not None:
        q = q.eq("salesman_id", salesman_id)
    if kind in KINDS:
        q = q.eq("kind", kind)
    r = q.order("created_at", desc=True).range(offset, offset + max(1, min(limit, 200)) - 1).execute()
    return {"rows": r.data or [], "total": getattr(r, "count", None)}


def get_ticket(ticket_id: int, by: str, mark_seen: bool = True) -> dict | None:
    """One ticket for staff: the row, its timeline, signed photo links, the order (number, status, the shop's
    name and the lines reported). Opening a new ticket the first time records who saw it."""
    client = shop.get_client()
    rows = client.table("shop_tickets").select("*").eq("id", ticket_id).limit(1).execute().data or []
    if not rows:
        return None
    t = rows[0]
    if mark_seen and not t.get("first_seen_at"):
        now = datetime.now(timezone.utc).isoformat()
        client.table("shop_tickets").update({"first_seen_at": now}).eq("id", ticket_id).execute()
        _event(client, ticket_id, by, "seen")
        t["first_seen_at"] = now
    events = (client.table("shop_ticket_events").select("at,actor,event,from_status,to_status,detail")
              .eq("ticket_id", ticket_id).order("at").execute().data or [])
    order = shop.get_order(t["order_id"]) if t.get("order_id") else None
    t.pop("ip_hash", None)
    return {**t, "events": events, "photo_urls": signed_photos(t.get("photos") or []),
            "order": ({"id": order["id"], "order_no": order.get("order_no"), "status": order.get("status"),
                       "customer_shop": order.get("customer_shop"), "customer_name": order.get("customer_name"),
                       "delivered_at": order.get("delivered_at")} if order else None)}


def update_ticket(ticket_id: int, changes: dict, by: str) -> dict:
    """Status, owner, resolution and a note, each change on the timeline. Resolving needs a resolution."""
    client = shop.get_client()
    rows = client.table("shop_tickets").select("id,status,salesman_id,resolution").eq("id", ticket_id).execute().data or []
    if not rows:
        raise TicketError("No such ticket.")
    t, upd = rows[0], {}
    status = changes.get("status")
    if status is not None:
        if status not in STATUSES:
            raise TicketError("Unknown status.")
        if status in ("resolved", "rejected") and not (changes.get("resolution") or t.get("resolution")):
            raise TicketError("Say how it was resolved.")
        if status != t["status"]:
            upd["status"] = status
            upd["resolved_at"] = datetime.now(timezone.utc).isoformat() if status in ("resolved", "rejected") else None
    if "resolution" in changes and changes["resolution"] is not None:
        if changes["resolution"] not in RESOLUTIONS:
            raise TicketError("Unknown resolution.")
        upd["resolution"] = changes["resolution"]
    if "salesman_id" in changes and changes["salesman_id"] != t.get("salesman_id"):
        sid = changes["salesman_id"]
        if sid is not None and not shop._salesman_by_id(sid):
            raise TicketError("No such salesman.")
        upd["salesman_id"] = sid
    if upd:
        upd["updated_at"] = datetime.now(timezone.utc).isoformat()
        client.table("shop_tickets").update(upd).eq("id", ticket_id).execute()
        if "status" in upd:
            _event(client, ticket_id, by, "resolve" if upd["status"] in ("resolved", "rejected") else "status",
                   {"resolution": upd.get("resolution") or t.get("resolution")}, t["status"], upd["status"])
        if "salesman_id" in upd:
            _event(client, ticket_id, by, "assign", {"from": t.get("salesman_id"), "to": upd["salesman_id"]})
    note = shop_note(changes.get("note"))
    if note:
        _event(client, ticket_id, by, "note", {"text": note})
    return get_ticket(ticket_id, by, mark_seen=False) or {}


def shop_note(v) -> str | None:
    from app.market_assist import _clean_text
    return _clean_text(v, 1000) or None


# ── jobs: the SLA reminder and the retention purge (app/shop_jobs.py, every 15 minutes) ─────────────

def _working_hours_between(start: datetime, end: datetime) -> float:
    """Hours of 07:00-22:00 Bahrain time, Sunday to Thursday, between two moments (the scheduler's own window)."""
    bh = timezone(timedelta(hours=3))
    t, hours = start.astimezone(bh), 0.0
    end = end.astimezone(bh)
    while t < end:
        step = min(end, (t + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0))
        if t.weekday() not in (4, 5) and 7 <= t.hour < 22:      # Friday, Saturday off
            hours += (step - t).total_seconds() / 3600
        t = step
    return hours


def sla_sweep(now: datetime | None = None) -> dict:
    """An open ticket nobody opened within shop_ticket_sla_hours working hours: one reminder to its rep (by
    e-mail when on, else noted for the owner), then, a second SLA later, one to the owner on Telegram. Each
    claimed once per ticket and recipient (the unique reminder index), so racing runs never send twice."""
    from app import shop_notify as sn
    now = now or datetime.now(timezone.utc)
    sla = max(_i(_vals().get("shop_ticket_sla_hours"), 24), 1)
    client = shop.get_client()
    out = {"rep": 0, "owner": 0}
    rows = (client.table("shop_tickets").select("id,ref,kind,lines,order_id,salesman_id,created_at,first_seen_at")
            .eq("status", "new").order("created_at").limit(200).execute().data or [])
    for t in rows:
        age = _working_hours_between(shop._parse_ts(t["created_at"]) or now, now)
        for to, after in (("rep", sla), ("owner", 2 * sla)):
            if age < after:
                continue
            try:
                client.table("shop_ticket_events").insert({"ticket_id": t["id"], "actor": "system", "event": "reminder",
                                                           "detail": {"to": to, "hours": round(age, 1)}}).execute()
            except Exception:  # noqa: BLE001 — claimed by an earlier run
                continue
            line = f"YQ Assist · not opened after {round(age)} working hours: {ticket_line(t)}"
            sm = shop._salesman_by_id(t.get("salesman_id")) if t.get("salesman_id") else None
            if to == "rep" and sm and sm.get("notify_email") and sm.get("email"):
                res = sn._email(f"YQ Assist reminder {t['ref']}", f"<p>{line}</p>", sm["email"])
                sn.log_notifications([sn.notification_row("ticket_sla", "email", "rep", res, order_id=t.get("order_id"),
                                                          to=sm["email"], detail={"ticket": t["ref"]})])
            elif to == "owner" or not sm:
                res = sn._telegram(line + (f" · with {sm['name']}" if sm else " · no rep"))
                sn.log_notifications([sn.notification_row("ticket_sla", "telegram", "owner", res,
                                                          order_id=t.get("order_id"), detail={"ticket": t["ref"]})])
            out[to] += 1
    return out


def retention_purge(now: datetime | None = None) -> dict:
    """Bahrain PDPL (owner 29-Oct-2026): chat turns older than shop_ai_retention_days (180) go; a closed ticket,
    its timeline and its photos go shop_ticket_keep_days (730) after it closed. Feedback rows keep their text but
    lose the turn they pointed at (ON DELETE SET NULL)."""
    now = now or datetime.now(timezone.utc)
    vals = _vals()
    keep_turns = max(_i(vals.get("shop_ai_retention_days"), 180), 30)
    keep_tickets = max(_i(vals.get("shop_ticket_keep_days"), 730), 90)
    client = shop.get_client()
    out = {"turns": 0, "tickets": 0, "photos": 0}
    cut = (now - timedelta(days=keep_turns)).isoformat()
    r = client.table("shop_ask_turns").delete().lt("ts", cut).execute()
    out["turns"] = len(r.data or [])
    cut_t = (now - timedelta(days=keep_tickets)).isoformat()
    old = (client.table("shop_tickets").select("id,photos").in_("status", ["resolved", "rejected"])
           .lt("resolved_at", cut_t).limit(500).execute().data or [])
    for t in old:
        if t.get("photos"):
            try:
                client.storage.from_(BUCKET).remove(list(t["photos"]))
                out["photos"] += len(t["photos"])
            except Exception as e:  # noqa: BLE001
                log.warning("support: photos of ticket %s not removed: %s", t["id"], e)
                continue                       # keep the row so the next run retries the photos
        client.table("shop_tickets").delete().eq("id", t["id"]).execute()
        out["tickets"] += 1
    return out
