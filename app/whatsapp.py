"""WhatsApp Cloud API — Mode B (Marketing Phase 2). Config-gated: everything here is
inert until WA_ACCESS_TOKEN + WA_PHONE_NUMBER_ID are set (a SECOND SIM registered on
the Cloud API — never the owner's daily WhatsApp number, that would kill the app).

Cost discipline = 100% free:
  • We ONLY send inside an open 24h service window (customer messaged first).
  • Paid marketing templates are NEVER sent by code — policy, not just config.
  • Entry points (catalog CTA, wa.me links, QR on invoices) make customers open
    the window; the concierge replies free.

Concierge: keyword triage first, LLM (free rotation) with catalog context second,
Telegram escalation on order intent, max 5 auto-replies/session/hour, kill switch
WA_AUTOREPLY_ENABLED. STOP/قف → marketing_opt_outs. Unknown numbers → leads.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from app.database import get_client

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"
WINDOW_HOURS = 24
MAX_AUTO_REPLIES_PER_HOUR = 5
_STOP_WORDS = {"stop", "unsubscribe", "قف", "توقف", "الغاء", "إلغاء"}


def configured() -> bool:
    return bool(os.getenv("WA_ACCESS_TOKEN") and os.getenv("WA_PHONE_NUMBER_ID"))


def autoreply_enabled() -> bool:
    return os.getenv("WA_AUTOREPLY_ENABLED", "1").lower() not in ("0", "false", "no", "off")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Sending (window-gated) ────────────────────────────────────────────────────

def window_open(wa_id: str) -> bool:
    rows = (get_client().table("wa_sessions").select("last_inbound_at")
            .eq("wa_id", wa_id).execute().data or [])
    if not rows or not rows[0].get("last_inbound_at"):
        return False
    last = datetime.fromisoformat(rows[0]["last_inbound_at"].replace("Z", "+00:00"))
    return _now() - last < timedelta(hours=WINDOW_HOURS)


def send_text(wa_id: str, body: str) -> dict:
    """Free-form text — ONLY inside an open service window (free). Refuses otherwise."""
    if not configured():
        return {"ok": False, "reason": "wa_not_configured"}
    if not window_open(wa_id):
        return {"ok": False, "reason": "window_closed — customer must message first (keeps it free)"}
    r = requests.post(
        f"{GRAPH}/{os.getenv('WA_PHONE_NUMBER_ID')}/messages",
        headers={"Authorization": f"Bearer {os.getenv('WA_ACCESS_TOKEN')}",
                 "Content-Type": "application/json"},
        json={"messaging_product": "whatsapp", "to": wa_id,
              "type": "text", "text": {"body": body[:4000]}},
        timeout=20)
    ok = r.status_code in (200, 201)
    if ok:
        get_client().table("wa_messages").insert(
            {"wa_id": wa_id, "direction": "out", "body": body[:4000]}).execute()
        get_client().table("wa_sessions").update(
            {"last_outbound_at": _now().isoformat(), "updated_at": _now().isoformat()}
        ).eq("wa_id", wa_id).execute()
    else:
        log.warning("wa send failed %s: %s", r.status_code, r.text[:200])
    return {"ok": ok, "status": r.status_code}


# ── Inbound webhook processing ────────────────────────────────────────────────

def _match_customer(wa_id: str) -> str | None:
    """wa_id (digits) → customer_name via customer_contacts.phone."""
    try:
        from app.customer_contacts import wa_digits
        rows = (get_client().table("customer_contacts")
                .select("customer_name,phone").neq("phone", "").execute().data or [])
        for r in rows:
            if wa_digits(r.get("phone")) == wa_id:
                return r["customer_name"]
    except Exception:  # noqa: BLE001
        pass
    return None


def _upsert_session(wa_id: str, profile_name: str) -> dict:
    cli = get_client()
    rows = cli.table("wa_sessions").select("*").eq("wa_id", wa_id).execute().data or []
    now = _now().isoformat()
    if rows:
        sess = rows[0]
        cli.table("wa_sessions").update(
            {"last_inbound_at": now, "updated_at": now,
             "profile_name": profile_name or sess.get("profile_name")}
        ).eq("wa_id", wa_id).execute()
        return sess
    customer = _match_customer(wa_id)
    sess = {"wa_id": wa_id, "customer_name": customer, "profile_name": profile_name,
            "last_inbound_at": now, "opt_in": True}  # messaging us first = service opt-in
    cli.table("wa_sessions").insert(sess).execute()
    if not customer:  # unknown number → a lead we can follow up
        try:
            cli.table("leads").upsert(
                {"name": profile_name or f"WhatsApp {wa_id[-4:]}", "phone": wa_id,
                 "source": "manual", "source_ref": f"wa:{wa_id}", "status": "new",
                 "notes": "Inbound WhatsApp enquiry"},
                on_conflict="source,source_ref").execute()
        except Exception:  # noqa: BLE001
            pass
    return sess


def _concierge_reply(wa_id: str, sess: dict, text: str) -> str | None:
    """Draft the reply: deterministic triage first, LLM with catalog context second.
    Returns None when a human should take over (order intent → Telegram escalation)."""
    t = text.strip().lower()
    from app.agents import _catalog_link
    link = _catalog_link()

    if any(w in t for w in ("catalog", "price list", "كتالوج", "الاسعار", "الأسعار")):
        return (f"Here is our live catalog with trade prices: {link}\n"
                f"Reply with any item code for details — or tell us what you need!")

    # order intent → human takes over (escalate, don't auto-negotiate)
    if any(w in t for w in ("order", "buy", "deliver", "quantity", "اطلب", "طلب", "توصيل")):
        try:
            from app.notify import send_telegram
            who = sess.get("customer_name") or sess.get("profile_name") or wa_id
            send_telegram(f"🛒 <b>WhatsApp order intent</b> — {who}\n“{text[:200]}”\n"
                          f"Reply from your phone or the portal Inbox.")
        except Exception:  # noqa: BLE001
            pass
        return ("Thank you! A member of our team is on it and will confirm your order "
                "shortly. 🙏 / شكراً! سيؤكد فريقنا طلبكم قريباً.")

    # general question → LLM with light catalog/price context (PII-free)
    try:
        from app.db_read import exec_sql
        from app.llm_router import chat
        sample = exec_sql(
            "SELECT item_code, display_name, standard_rate FROM v_catalog "
            "WHERE is_active ORDER BY updated_at DESC LIMIT 40") or []
        ctx = "\n".join(f"{r['item_code']}: {str(r.get('display_name') or '')[:60]} — "
                        f"BHD {r.get('standard_rate')}" for r in sample)
        out = chat([
            {"role": "system", "content":
                "You are the WhatsApp assistant for YQ Bahrain, a mobile-accessories wholesaler "
                "(VFAN brand) in Bahrain. Answer briefly (max 3 sentences), friendly, EN or AR "
                "matching the customer. Use ONLY the price list below; if unsure say a colleague "
                f"will confirm. Catalog link: {link}\n\nPRICE LIST:\n" + ctx},
            {"role": "user", "content": text[:500]},
        ], tier=2, task="multilingual", max_tokens=220)
        return (out or "").strip() or None
    except Exception as e:  # noqa: BLE001
        log.warning("concierge llm failed: %s", str(e)[:120])
        return (f"Thanks for reaching out to YQ Bahrain! Our catalog: {link}\n"
                f"A team member will reply shortly.")


def handle_inbound(payload: dict) -> dict:
    """Process one Meta webhook delivery (messages field). Always succeeds — Meta
    retries on non-200 and we never want duplicate processing storms."""
    handled = 0
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                val = change.get("value", {})
                contacts = {c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                            for c in val.get("contacts", [])}
                for msg in val.get("messages", []):
                    wa_id = msg.get("from", "")
                    body = (msg.get("text") or {}).get("body", "") if msg.get("type") == "text" \
                        else f"[{msg.get('type')}]"
                    if not wa_id:
                        continue
                    get_client().table("wa_messages").insert(
                        {"wa_id": wa_id, "direction": "in", "body": body[:4000],
                         "msg_type": msg.get("type", "text")}).execute()
                    sess = _upsert_session(wa_id, contacts.get(wa_id, ""))
                    handled += 1

                    if body.strip().lower() in _STOP_WORDS:
                        from app.outreach import record_optout
                        record_optout(sess.get("customer_name") or wa_id,
                                      channel="whatsapp", reason="wa_stop")
                        send_text(wa_id, "You won't receive further messages from us. "
                                         "Reply anytime to reach the team. ✅")
                        continue

                    if not autoreply_enabled() or not configured():
                        continue
                    # circuit breaker: max N auto-replies per session per hour
                    hour_ago = (_now() - timedelta(hours=1)).isoformat()
                    n = (get_client().table("wa_messages").select("id", count="exact")
                         .eq("wa_id", wa_id).eq("direction", "out")
                         .gte("ts", hour_ago).execute().count or 0)
                    if n >= MAX_AUTO_REPLIES_PER_HOUR:
                        continue
                    reply = _concierge_reply(wa_id, sess, body)
                    if reply:
                        send_text(wa_id, reply)
    except Exception as e:  # noqa: BLE001
        log.warning("wa inbound processing error: %s", e)
    return {"handled": handled}


# ── Inbox (portal Marketing Studio) ───────────────────────────────────────────

def threads(limit: int = 50) -> list[dict]:
    return (get_client().table("wa_sessions").select("*")
            .order("last_inbound_at", desc=True).limit(limit).execute().data or [])


def thread_messages(wa_id: str, limit: int = 50) -> list[dict]:
    rows = (get_client().table("wa_messages").select("*").eq("wa_id", wa_id)
            .order("ts", desc=True).limit(limit).execute().data or [])
    return list(reversed(rows))
