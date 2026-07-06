"""Outreach engine — the BHD 10k/month lever (Marketing Phase 1).

Turns the draft-producing growth agents (sales_outreach, winback, sales_push,
lead_gen) into ONE approved send queue with attribution. A HUMAN approves every
send (owner decision, 2026-07-06):
  • whatsapp — assist mode: one-tap wa.me deep links, the human's own number sends
  • email    — server-side via app/emailer (Resend/Brevo free tier) + opt-out footer

Guardrails follow app/agent_actions.py: per-day POLICIES caps + OUTREACH_ENABLED
kill switch. PDPL: business contacts only, opt-outs checked before draft AND send.
Attribution: outreach_touches → v_outreach_attribution (order within 14 days).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from app.database import get_client
from app.db_read import exec_sql

log = logging.getLogger(__name__)

POLICIES: dict[str, dict] = {
    "outreach_email": {"max_per_day": 25},   # well under the Resend/Brevo free tiers
    "outreach_wa":    {"max_per_day": 40},   # human taps each one — cap protects the brand
}
DEDUPE_DAYS = 14          # don't re-draft the same target+agent inside this window
LLM_PERSONALIZE_TOP = 10  # only the highest-value win-backs get an LLM-polished message


def enabled() -> bool:
    return os.getenv("OUTREACH_ENABLED", "1").lower() not in ("0", "false", "no", "off")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _q(sql: str) -> list[dict]:
    try:
        return exec_sql(sql) or []
    except Exception as e:  # noqa: BLE001
        log.warning("outreach query failed: %s", str(e)[:200])
        return []


# ── Opt-out (PDPL) ────────────────────────────────────────────────────────────

def _optout_secret() -> bytes:
    from app.config import settings
    return (os.getenv("OPTOUT_SECRET") or settings.supabase_jwt_secret or "yq-optout").encode()


def optout_token(name: str) -> str:
    """URL-safe reversible token: b64(name).hmac16 — verified server-side, no table lookup."""
    raw = base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")
    sig = hmac.new(_optout_secret(), name.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{raw}.{sig}"


def verify_optout_token(token: str) -> str | None:
    """Return the target name if the token is genuine, else None."""
    try:
        raw, sig = token.rsplit(".", 1)
        name = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        good = hmac.new(_optout_secret(), name.encode(), hashlib.sha256).hexdigest()[:16]
        return name if hmac.compare_digest(sig, good) else None
    except Exception:  # noqa: BLE001
        return None


def record_optout(name: str, channel: str = "all", reason: str = "link") -> None:
    get_client().table("marketing_opt_outs").insert(
        {"target_name": name, "channel": channel, "reason": reason}).execute()


def _opted_out() -> set[str]:
    try:
        rows = get_client().table("marketing_opt_outs").select("target_name").execute().data or []
        return {(r.get("target_name") or "").strip().lower() for r in rows}
    except Exception:  # noqa: BLE001
        return set()


# ── Queue building (agent: outreach_builder) ──────────────────────────────────

def _recent_fingerprints() -> set[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUPE_DAYS)).isoformat()
    try:
        rows = (get_client().table("outreach_queue").select("fingerprint")
                .gte("created_at", cutoff).execute().data or [])
        return {r["fingerprint"] for r in rows}
    except Exception:  # noqa: BLE001
        return set()


def _catalog_link() -> str:
    from app.agents import _catalog_link as _cl
    return _cl()


def _wa_link(phone: str | None, text: str) -> str | None:
    from app.customer_contacts import wa_digits
    d = wa_digits(phone)
    return f"https://wa.me/{d}?text={quote(text)}" if d else None


def _llm_polish(name: str, base_msg: str, context: str) -> str:
    """Optional tone polish for top win-backs. PII-safe: the customer name is replaced
    with a placeholder before the external call and substituted back after. Falls back
    to the template on any provider hiccup."""
    try:
        from app.llm_router import chat
        out = chat([
            {"role": "system", "content":
                "You write short, warm B2B WhatsApp messages for YQ Bahrain, a mobile-accessories "
                "wholesaler in Bahrain. Max 3 sentences. No emojis overload (1 max). Keep any URL "
                "and the placeholder {NAME} exactly as-is. Reply with the message only."},
            {"role": "user", "content":
                f"Rewrite this reorder/win-back message, keeping meaning and URL. Context: {context}\n\n"
                + base_msg.replace(name, "{NAME}")},
        ], tier=1, task="write", max_tokens=180)
        out = (out or "").strip()
        if 20 < len(out) < 500 and "{NAME}" in out:
            return out.replace("{NAME}", name)
    except Exception as e:  # noqa: BLE001
        log.info("llm polish skipped: %s", str(e)[:120])
    return base_msg


def build_queue() -> dict:
    """Agent outreach_builder — drain the growth agents into outreach_queue drafts.
    Skips: opt-outs, targets drafted in the last 14 days, targets already pending."""
    if not enabled():
        return {"count": 0, "summary": "Outreach is disabled (OUTREACH_ENABLED=0)."}
    from app.agents import sales_outreach, sales_push, winback
    from app.customer_contacts import get_contacts

    skip_fp = _recent_fingerprints()
    optout = _opted_out()
    link = _catalog_link()
    rows: list[dict] = []
    no_contact: list[str] = []

    def _add(target: str, agent: str, msg_en: str, msg_ar: str, reason: str,
             impact: float, phone: str | None, email: str | None,
             target_type: str = "customer", lead_id: int | None = None,
             subject: str | None = None) -> None:
        target = (target or "").strip()
        if not target or target.lower() in optout:
            return
        fp = f"{target}|{agent}"
        if fp in skip_fp:
            return
        channel = "whatsapp" if phone else ("email" if email else None)
        if channel is None:
            no_contact.append(target)
            return
        skip_fp.add(fp)  # also dedupes across agents within this run
        rows.append({
            "target_type": target_type, "target_name": target, "lead_id": lead_id,
            "source_agent": agent, "channel": channel, "phone": phone, "email": email,
            "wa_link": _wa_link(phone, msg_en), "subject": subject,
            "message_en": msg_en, "message_ar": msg_ar, "reason": reason,
            "impact_bhd": round(float(impact or 0), 1), "fingerprint": fp,
        })

    # 1 — reorder nudges (sales_outreach already builds the message + enriches contacts)
    so = sales_outreach()
    for d in so.get("drafts", []):
        _add(d.get("customer"), "sales_outreach", d.get("message_en") or "",
             d.get("message_ar") or "", f"{d.get('days_since_order')} days past usual cycle",
             d.get("lifetime_bhd") or 0, d.get("phone"), d.get("email"),
             subject="Your usual YQ Bahrain order — fresh stock in")

    # 2 — win-back (lapsed / at-risk high value); top N get an LLM tone pass
    wb = winback()
    lapsed = wb.get("lapsed_customers", [])
    names = [str(r.get("customer_name") or "").strip() for r in lapsed]
    contacts = get_contacts([n for n in names if n])
    tail_en = f"\nOur latest catalog with prices: {link}" if link else ""
    tail_ar = f"\nأحدث كتالوج بالأسعار: {link}" if link else ""
    for i, r in enumerate(lapsed):
        name = str(r.get("customer_name") or "").strip()
        days = int(float(r.get("days_since") or 0))
        msg_en = (f"Hello {name}, it's YQ Bahrain. We haven't seen an order in {days} days and "
                  f"we miss working with you — new VFAN stock just landed and we're holding "
                  f"trade pricing for you. Anything we can prepare?{tail_en}")
        msg_ar = (f"مرحباً {name}، معكم YQ البحرين. مضى {days} يوماً على آخر طلبية — وصلتنا "
                  f"بضاعة VFAN جديدة وأسعار الجملة محفوظة لكم. هل نجهز لكم طلبية؟{tail_ar}")
        if i < LLM_PERSONALIZE_TOP:
            msg_en = _llm_polish(name, msg_en, f"lapsed {days} days, segment {r.get('segment')}")
        c = contacts.get(name) or {}
        _add(name, "winback", msg_en, msg_ar,
             f"lapsed {days}d ({r.get('segment')})", r.get("lifetime_bhd") or 0,
             c.get("phone"), c.get("email"),
             subject="We miss you at YQ Bahrain — new VFAN stock + your trade price")

    # 3 — clearance push targets (slow stock matched to the customers who buy that category)
    sp = sales_push()
    for it in sp.get("push_list", []):
        item = str(it.get("item_name") or "").split(" (")[0]
        md = it.get("suggested_markdown_pct") or 30
        for t in (it.get("target_customers") or [])[:2]:
            name = str(t.get("customer") or "").strip()
            c = contacts.get(name) or get_contacts([name]).get(name) or {}
            msg_en = (f"Hello {name}, YQ Bahrain here — we're clearing {item} at {md}% off "
                      f"trade price this week. You've stocked this category before; want us to "
                      f"reserve quantity?{tail_en}")
            msg_ar = (f"مرحباً {name}، عرض تصفية من YQ البحرين: خصم {md}٪ على {item} هذا "
                      f"الأسبوع. هل نحجز لكم كمية؟{tail_ar}")
            _add(name, "sales_push", msg_en, msg_ar, f"clearance {item} -{md}%",
                 t.get("spent_bhd") or 0, c.get("phone"), c.get("email"),
                 subject=f"Clearance offer: {item} at {md}% off — YQ Bahrain")

    # 4 — new leads with a phone (Overpass discovery) — first-contact opener
    try:
        leads = (get_client().table("leads").select("id,name,phone,area,fit_score")
                 .eq("status", "new").neq("phone", "").order("fit_score", desc=True)
                 .limit(15).execute().data or [])
    except Exception:  # noqa: BLE001
        leads = []
    for ld in leads:
        name = str(ld.get("name") or "").strip()
        msg_en = (f"Hello! This is YQ Bahrain — we wholesale VFAN mobile accessories "
                  f"(cables, chargers, audio) to shops across Bahrain with same-week delivery. "
                  f"May we share our trade price list?{tail_en}")
        msg_ar = (f"مرحباً! معكم YQ البحرين — نوفر إكسسوارات VFAN بأسعار الجملة لمحلات "
                  f"البحرين مع توصيل سريع. نرسل لكم قائمة الأسعار؟{tail_ar}")
        _add(name, "lead_gen", msg_en, msg_ar, f"new lead ({ld.get('area') or 'Bahrain'})",
             0, ld.get("phone"), None, target_type="lead", lead_id=ld.get("id"))

    if rows:
        get_client().table("outreach_queue").insert(rows).execute()
        try:
            from app import events
            events.emit("agent:outreach_builder", "outreach.queue_ready", severity="info",
                        payload={"summary": f"{len(rows)} outreach drafts queued for approval"})
        except Exception:  # noqa: BLE001
            pass

    by_agent: dict[str, int] = {}
    for r in rows:
        by_agent[r["source_agent"]] = by_agent.get(r["source_agent"], 0) + 1
    return {
        "count": len(rows),
        "summary": (f"{len(rows)} outreach drafts queued for approval "
                    f"({', '.join(f'{k} {v}' for k, v in by_agent.items())}). "
                    f"{len(no_contact)} targets skipped — no phone/email on file yet."
                    if rows else
                    f"No new drafts (deduped/opted-out). {len(no_contact)} targets still "
                    f"have no contact info — run contact_enrich or import numbers."),
        "by_agent": [{"agent": k, "drafts": v} for k, v in by_agent.items()],
        "no_contact": sorted(set(no_contact))[:30],
        "queued": rows[:20],
    }


# ── Contact coverage + enrichment (agent: contact_enrich) ─────────────────────

def coverage() -> dict:
    """Phone/email coverage across the named customer book, weighted by value.
    customer_contacts is RLS-protected (service-role only) — the SQL RPC would
    silently see 0 rows, so contact counts go through the service client."""
    rows = _q(
        "SELECT customer_name, SUM(revenue_bhd) AS lifetime_bhd FROM v_sales "
        "WHERE customer_name IS NOT NULL AND customer_name NOT ILIKE 'cash customer%' "
        "GROUP BY 1 ORDER BY 2 DESC")
    top50 = {str(r.get("customer_name") or "") for r in rows[:50]}
    try:
        contacts = (get_client().table("customer_contacts")
                    .select("customer_name,phone,email").execute().data or [])
    except Exception:  # noqa: BLE001
        contacts = []
    with_phone = [c for c in contacts if (c.get("phone") or "").strip()]
    with_email = [c for c in contacts if (c.get("email") or "").strip()]
    covered = {c["customer_name"] for c in contacts
               if (c.get("phone") or "").strip() or (c.get("email") or "").strip()}
    return {"customers": len(rows),
            "with_phone": len(with_phone),
            "with_email": len(with_email),
            "top50_covered": len(top50 & covered)}


def contact_enrich_run() -> dict:
    """Agent contact_enrich — nightly: find business phones/emails for the highest-value
    customers that have no contact row yet (Tavily public listings, capped per run)."""
    from app.customer_contacts import enrich_missing
    rows = _q(
        "SELECT customer_name FROM v_sales WHERE customer_name IS NOT NULL "
        "AND customer_name NOT ILIKE 'cash customer%' "
        "GROUP BY 1 ORDER BY SUM(revenue_bhd) DESC LIMIT 120")
    names = [str(r.get("customer_name") or "").strip() for r in rows]
    found = enrich_missing(names, limit=25)
    cov = coverage()
    return {
        "count": found,
        "summary": (f"Contact enrichment: {found} customers looked up tonight. Coverage now "
                    f"{cov['with_phone']} phones / {cov['with_email']} emails across "
                    f"{cov['customers']} named customers; {cov['top50_covered']}/50 of the "
                    f"top-value book covered."),
        "coverage": cov,
    }


def import_contacts(rows: list[dict], commit: bool = False, by: str = "") -> dict:
    """Bulk paste/CSV import (salesmen know the numbers). Fuzzy-matches names against
    the canonical customer book; preview first, then commit. Manual source wins —
    enrichment never overwrites it (rule lives in customer_contacts)."""
    import difflib
    known = [str(r.get("customer_name") or "") for r in _q(
        "SELECT DISTINCT customer_name FROM v_sales WHERE customer_name IS NOT NULL")]
    out, to_upsert = [], []
    for row in rows[:500]:
        raw = str(row.get("customer_name") or row.get("name") or "").strip()
        phone = str(row.get("phone") or "").strip()
        email = str(row.get("email") or "").strip()
        if not raw or not (phone or email):
            continue
        match = difflib.get_close_matches(raw, known, n=1, cutoff=0.75)
        matched = match[0] if match else None
        out.append({"input": raw, "matched": matched, "phone": phone, "email": email,
                    "status": "matched" if matched else "no_match"})
        if matched and commit:
            rec = {"customer_name": matched, "source": "manual", "updated_by": by,
                   "updated_at": _now()}
            if phone:
                rec["phone"] = phone
            if email:
                rec["email"] = email
            to_upsert.append(rec)
    if to_upsert:
        get_client().table("customer_contacts").upsert(
            to_upsert, on_conflict="customer_name").execute()
    matched_n = sum(1 for o in out if o["status"] == "matched")
    return {"rows": out, "matched": matched_n, "unmatched": len(out) - matched_n,
            "committed": len(to_upsert) if commit else 0}


# ── Queue operations (Send Center) ────────────────────────────────────────────

def list_queue(status: str | None = "draft", limit: int = 100) -> list[dict]:
    q = get_client().table("outreach_queue").select("*").order("impact_bhd", desc=True)
    if status:
        q = q.eq("status", status)
    return q.limit(limit).execute().data or []


def _sent_today(channel: str) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        rows = (get_client().table("outreach_touches").select("id", count="exact")
                .eq("channel", channel).gte("sent_at", today).execute())
        return rows.count or 0
    except Exception:  # noqa: BLE001
        return 0


def set_status(row_id: int, status: str, by: str = "") -> dict:
    patch = {"status": status, "updated_at": _now()}
    if status == "sent":
        patch["sent_at"] = _now()
        patch["sent_by"] = by
    res = (get_client().table("outreach_queue").update(patch)
           .eq("id", row_id).execute().data or [])
    return res[0] if res else {}


def _email_html(msg: str, target: str) -> str:
    """Plain, personal-looking B2B email (not a flashy template) + PDPL opt-out footer."""
    base = os.getenv("APP_BASE_URL", "").rstrip("/")
    opt = f"{base}/optout/{optout_token(target)}" if base else ""
    body = msg.replace("\n", "<br>")
    foot = (f'<p style="margin-top:28px;font-size:12px;color:#8b8698">YQ Bahrain W.L.L · '
            f'Mobile accessories wholesale · Bahrain<br>'
            + (f'Prefer not to receive these? <a href="{opt}" style="color:#6d28d9">'
               f'Unsubscribe</a>.' if opt else "") + "</p>")
    return (f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;'
            f'color:#1a1430;line-height:1.6;max-width:560px">{body}{foot}</div>')


def send_row(row_id: int, by: str = "") -> dict:
    """Execute one approved/draft row. email → server-side send; whatsapp → the caller
    opened the wa.me link, we log the touch. Both respect kill switch + daily caps."""
    if not enabled():
        return {"ok": False, "reason": "outreach_disabled"}
    rows = get_client().table("outreach_queue").select("*").eq("id", row_id).execute().data or []
    if not rows:
        return {"ok": False, "reason": "not_found"}
    row = rows[0]
    if row["status"] in ("sent", "dismissed"):
        return {"ok": False, "reason": f"already_{row['status']}"}
    if (row.get("target_name") or "").strip().lower() in _opted_out():
        set_status(row_id, "dismissed", by)
        return {"ok": False, "reason": "opted_out"}

    pol = "outreach_email" if row["channel"] == "email" else "outreach_wa"
    if _sent_today(row["channel"]) >= POLICIES[pol]["max_per_day"]:
        return {"ok": False, "reason": f"daily cap {POLICIES[pol]['max_per_day']} reached"}

    result: dict = {"ok": True}
    if row["channel"] == "email":
        from app.emailer import send_html
        subject = row.get("subject") or "YQ Bahrain — VFAN accessories, trade prices"
        r = send_html(subject, _email_html(row.get("message_en") or "", row["target_name"]),
                      to=row["email"])
        if not r.get("emailed"):
            return {"ok": False, "reason": r.get("reason") or "email_failed"}
        result["via"] = r.get("via")
    # whatsapp: nothing to send server-side in assist mode — the human's tap IS the send.

    get_client().table("outreach_touches").insert({
        "queue_id": row_id, "target_name": row["target_name"], "channel": row["channel"],
        "message": row.get("message_en"), "sent_by": by}).execute()
    set_status(row_id, "sent", by)
    if row.get("target_type") == "lead" and row.get("lead_id"):
        try:
            get_client().table("leads").update({"status": "contacted"}).eq(
                "id", row["lead_id"]).eq("status", "new").execute()
        except Exception:  # noqa: BLE001
            pass
    return result


# ── Daily digest (agent: outreach_digest) ─────────────────────────────────────

def digest() -> dict:
    """Morning Telegram digest: what's waiting for a tap, with one-tap wa.me links."""
    drafts = list_queue("draft", limit=200)
    approved = list_queue("approved", limit=200)
    pending = approved + drafts
    if not pending:
        return {"count": 0, "summary": "Outreach queue is empty — nothing waiting for approval."}
    base = os.getenv("APP_BASE_URL", "").rstrip("/")
    lines = [f"📤 <b>{len(pending)} outreach messages waiting</b>"]
    for r in pending[:10]:
        tag = "📧" if r["channel"] == "email" else "💬"
        link = r.get("wa_link")
        nm = r["target_name"]
        lines.append(f"{tag} <a href=\"{link}\">{nm}</a> — {r.get('reason') or ''}" if link
                     else f"{tag} {nm} — {r.get('reason') or ''}")
    if base:
        lines.append(f'\nApprove & send: {base}/marketing')
    sent = False
    try:
        from app.notify import send_telegram
        sent = send_telegram("\n".join(lines))
    except Exception:  # noqa: BLE001
        pass
    return {"count": len(pending),
            "summary": f"{len(pending)} messages waiting for approval "
                       f"({len(approved)} approved, {len(drafts)} drafts)."
                       + (" Telegram digest sent." if sent else ""),
            "pending": pending[:15]}


# ── Weekly scorecard (agent: growth_scorecard) ────────────────────────────────

def scorecard() -> dict:
    """The numbers loop: month pace vs BHD 10,000 + the outreach funnel + coverage."""
    pace = _q("SELECT * FROM v_month_pace")
    kpis = _q("SELECT * FROM v_marketing_kpis ORDER BY week DESC LIMIT 6")
    cov = coverage()
    # v_outreach_attribution is a definer view (owner bypasses RLS) — safe via the RPC
    reactivated = _q(
        "SELECT COUNT(DISTINCT target_name) AS n FROM v_outreach_attribution "
        "WHERE touch_date > current_date - 30 AND first_order IS NOT NULL")
    p = pace[0] if pace else {}
    week = kpis[0] if kpis else {}
    target = 10000
    proj = float(p.get("projected_bhd") or 0)
    return {
        "month": p.get("month"),
        "revenue_bhd": float(p.get("revenue_bhd") or 0),
        "projected_bhd": proj,
        "target_bhd": target,
        "on_track": proj >= target,
        "weekly_kpis": kpis,
        "coverage": cov,
        "reactivated_30d": int((reactivated[0] or {}).get("n") or 0) if reactivated else 0,
        "summary": (f"Month {p.get('month')}: BHD {float(p.get('revenue_bhd') or 0):,.0f} booked, "
                    f"projecting BHD {proj:,.0f} vs the 10,000 target "
                    f"({'ON TRACK ✅' if proj >= target else f'gap BHD {target - proj:,.0f} ⚠️'}). "
                    f"This week: {int(week.get('touches') or 0)} touches → "
                    f"BHD {float(week.get('attributed_bhd') or 0):,.0f} attributed. "
                    f"Contacts: {cov['with_phone']} phones ({cov['top50_covered']}/50 top book)."),
    }
