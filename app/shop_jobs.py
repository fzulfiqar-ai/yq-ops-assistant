"""Marketplace housekeeping, run through GET /scheduler/shop-jobs (X-Agent-Key) every 15 minutes by
the Cloudflare Worker cron (web/wrangler.keepwarm.jsonc, 24-Sep-2026); .github/workflows/shop-cron.yml
still calls it too, but GitHub's free scheduler really fires about every 3.4 h, so it is only the
backstop. n8n is down. Everything here is idempotent and safe to run often.

Jobs (in run order):
  * notify_retry — an order younger than 48 h whose new-order alert reached nobody (every internal
    channel in notify_result failed, or the fan-out never wrote a result) gets notify_new_order
    again, at most MAX_NOTIFY_ATTEMPTS times in total; channels that already delivered are kept.
  * unassigned_reminder — an order nobody owns after shop_assign_sla_min minutes is re-alerted
    to the owner channel (Telegram + owner email) at most every two hours until someone assigns it.
  * unconfirmed_reminder — an order a rep DOES own but has left in 'new' past shop_confirm_sla_min
    minutes: the rep is reminded (email + WhatsApp Cloud when configured), at most every
    shop_confirm_renotify_hours; past twice the SLA the owner channel is told as well. Each nudge
    is a shop_order_events row event='reminded' and stamps sla_notified_at.
  * cleanup — expired or revoked merchant sessions and stale access links are removed.
  * stale_data_alert — when the stock snapshot merchants see is older than shop_stale_days, the
    owner channel gets one Telegram (owner email when Telegram is not configured) a day asking for
    the Focus report. The "alerted today" marker is written only when a channel really delivered.

A reminder is stamped only when at least one channel delivered: with every channel dead the job
keeps trying every run instead of pretending someone was told (D-13, 24-Sep-2026). Cron drifts by
minutes, so all of this is an internal nudge, never a merchant-facing promise.
"""
from __future__ import annotations

import html as _html
import logging
import os
from datetime import datetime, timedelta, timezone

from app.database import get_client

log = logging.getLogger(__name__)

RENOTIFY_HOURS = 2           # unassigned orders: owner nudge cadence
KEEP_DAYS = 30
RETRY_WINDOW_HOURS = 48      # notify_retry looks this far back
RETRY_GRACE_MIN = 15         # a null notify_result younger than this may still be the background task
RETRY_PER_RUN = 10           # each fan-out is up to four sends; bound the run
STALE_RENOTIFY_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _base() -> str:
    return (os.getenv("APP_BASE_URL", "") or "").rstrip("/")


def _age_min(row: dict, now: datetime, since: str = "created_at") -> int:
    start = _parse(row.get(since)) or _parse(row.get("created_at"))
    return int((now - start).total_seconds() // 60) if start else 0


def _fmt_wait(minutes: int) -> str:
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _order_line(r: dict, now: datetime, rep: bool = False, since: str = "created_at") -> str:
    return (f"• {r['order_no']} · {r.get('customer_shop') or 'shop'} · {r.get('customer_area') or '-'}"
            f" · BHD {float(r.get('total_bhd') or 0):.3f} · waiting {_fmt_wait(_age_min(r, now, since))}"
            + (f" · {r.get('salesman_name') or 'rep'}" if rep else ""))


def _text_html(text: str) -> str:
    return "<p>" + _html.escape(text).replace("\n", "<br>") + "</p>"


# ── channel helpers (each returns {"sent": bool, ...}; nothing here raises) ────

def _owner_alert(subject: str, text: str) -> dict:
    """Telegram + ALERT_EMAIL_TO. `sent` is True when at least one of them delivered."""
    from app import shop_notify
    out: dict = {"telegram": shop_notify._telegram(text)}
    owner = os.getenv("ALERT_EMAIL_TO", "")
    if owner:
        out["email"] = shop_notify._email(subject, _text_html(text), owner)
    out["sent"] = any(isinstance(v, dict) and v.get("sent") for k, v in out.items() if k != "sent")
    return out


def _rep_alert(sm: dict, subject: str, text: str) -> dict:
    """Email (when the rep has one and did not opt out) + WhatsApp Cloud (only inside an open
    session window — Meta policy — so it rarely carries the message on its own)."""
    from app import shop_notify
    out: dict = {}
    if sm.get("email") and sm.get("notify_email", True):
        out["email"] = shop_notify._email(subject, _text_html(text), sm["email"])
    if sm.get("notify_whatsapp", True) and (sm.get("whatsapp") or sm.get("phone")):
        out["whatsapp"] = shop_notify._whatsapp_cloud(sm.get("whatsapp") or sm.get("phone"), text)
    if not out:
        out["reason"] = "rep has no email or phone on file"
    out["sent"] = any(isinstance(v, dict) and v.get("sent") for k, v in out.items() if k != "sent")
    return out


def _stamp(client, order_ids: list[int], when: datetime) -> None:
    for oid in order_ids:
        try:
            client.table("shop_orders").update({"sla_notified_at": when.isoformat()}).eq("id", oid).execute()
        except Exception as e:  # noqa: BLE001
            log.debug("sla stamp failed for order %s: %s", oid, e)


def _event(client, order_id: int, event: str, detail: dict) -> bool:
    """One audit row per nudge. shop_order_events.event has no CHECK constraint (verified live,
    24-Sep-2026), so 'reminded' needs no migration."""
    try:
        client.table("shop_order_events").insert({
            "order_id": order_id, "actor": "shop_jobs", "event": event, "detail": detail}).execute()
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("event %s for order %s failed: %s", event, order_id, e)
        return False


# ── jobs ──────────────────────────────────────────────────────────────────────

def notify_retry() -> dict:
    """Re-run the new-order fan-out for young orders nobody at YQ was told about."""
    from app import shop_notify
    now = _now()
    since = (now - timedelta(hours=RETRY_WINDOW_HOURS)).isoformat()
    rows = (get_client().table("shop_orders")
            .select("id,order_no,status,created_at,notify_result,notified_at")
            .eq("status", "new").gte("created_at", since)
            .order("created_at").limit(200).execute().data or [])
    out: dict = {"checked": len(rows), "retried": [], "recovered": [], "exhausted": []}
    for r in rows:
        nr = r.get("notify_result")
        if nr is None and _age_min(r, now) < RETRY_GRACE_MIN:
            continue                                   # the create_order background task may still be running
        if not shop_notify.all_channels_failed(nr):
            continue
        if shop_notify.attempt_count(nr) >= shop_notify.MAX_NOTIFY_ATTEMPTS:
            out["exhausted"].append(r["order_no"])
            continue
        if len(out["retried"]) >= RETRY_PER_RUN:
            break
        res = shop_notify.notify_new_order(r["id"], retry=True)
        out["retried"].append(r["order_no"])
        if not shop_notify.all_channels_failed(res):
            out["recovered"].append(r["order_no"])
    return out


def unassigned_reminder() -> dict:
    """Re-alert admins about orders still without a salesman past the SLA."""
    from app import shop
    sla = shop._i(shop.shop_settings().get("shop_assign_sla_min"), 30)
    if sla <= 0:
        return {"skipped": "shop_assign_sla_min is 0"}
    now = _now()
    cutoff = (now - timedelta(minutes=sla)).isoformat()
    renotify_before = now - timedelta(hours=RENOTIFY_HOURS)
    client = get_client()
    rows = (client.table("shop_orders")
            .select("id,order_no,customer_shop,customer_area,total_bhd,created_at,sla_notified_at")
            .is_("salesman_id", "null").in_("status", ["new", "confirmed"]).lt("created_at", cutoff)
            .order("created_at").limit(50).execute().data or [])
    due = [r for r in rows if not r.get("sla_notified_at") or (_parse(r["sla_notified_at"]) or now) < renotify_before]
    if not due:
        return {"checked": len(rows), "notified": 0}
    text = (f"{len(due)} marketplace order(s) still UNASSIGNED after {sla} minutes:\n"
            + "\n".join(_order_line(r, now) for r in due))
    if _base():
        text += f"\nAssign: {_base()}/shop-orders?queue=1"
    result: dict = {"checked": len(rows), "notified": len(due), "orders": [r["order_no"] for r in due]}
    sent = _owner_alert(f"YQ Marketplace · {len(due)} unassigned order(s)", text)
    result["telegram"] = bool((sent.get("telegram") or {}).get("sent"))
    result["email"] = bool((sent.get("email") or {}).get("sent"))
    result["sent"] = sent["sent"]
    if sent["sent"]:
        _stamp(client, [r["id"] for r in due], now)
    else:
        result["notified"] = 0
        result["reason"] = "no channel delivered — will retry next run"
    return result


def _last_reminders(client, order_ids: list[int]) -> dict[int, dict[str, datetime]] | None:
    """{order_id: {"rep": last rep nudge, "owner": last owner escalation}} from the audit rows;
    None when the read failed (the caller then falls back to sla_notified_at)."""
    out: dict[int, dict[str, datetime]] = {}
    if not order_ids:
        return out
    try:
        rows = (client.table("shop_order_events").select("order_id,ts,detail")
                .eq("event", "reminded").in_("order_id", order_ids).order("id").execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.debug("reminded events read failed: %s", e)
        return None
    for e in rows:
        ts = _parse(e.get("ts"))
        d = e.get("detail") if isinstance(e.get("detail"), dict) else {}
        if not ts:
            continue
        slot = out.setdefault(int(e["order_id"]), {})
        if d.get("rep"):
            slot["rep"] = ts
        if d.get("owner"):
            slot["owner"] = ts
    return out


def unconfirmed_reminder() -> dict:
    """Chase orders a rep owns but has left in 'new' past shop_confirm_sla_min."""
    from app import shop
    vals = shop.shop_settings()
    sla = shop._i(vals.get("shop_confirm_sla_min"), 120)
    if sla <= 0:
        return {"skipped": "shop_confirm_sla_min is 0"}
    renotify_h = max(1, shop._i(vals.get("shop_confirm_renotify_hours"), 12))
    now = _now()
    cutoff = (now - timedelta(minutes=sla)).isoformat()
    renotify_before = now - timedelta(hours=renotify_h)
    client = get_client()
    rows = (client.table("shop_orders")
            .select("id,order_no,customer_shop,customer_area,total_bhd,created_at,assigned_at,sla_notified_at,"
                    "salesman_id,salesman_name,source")
            .eq("status", "new").not_.is_("salesman_id", "null").lt("created_at", cutoff)
            .order("created_at").limit(100).execute().data or [])
    # The clock starts when the rep GOT the order: one assigned from the admin queue ten minutes
    # ago is not late, however old the order itself is.
    rows = [r for r in rows if _age_min(r, now, since="assigned_at") >= sla]
    out: dict = {"checked": len(rows), "reminded": [], "escalated": [], "reps": {}, "sla_min": sla}
    if not rows:
        return out
    last = _last_reminders(client, [r["id"] for r in rows])

    def _due(r: dict, who: str) -> bool:
        # The audit rows say who was told when; sla_notified_at (shared with the unassigned nudge)
        # is only the fallback when those rows could not be read.
        ts = (last.get(r["id"], {}).get(who) if last is not None else _parse(r.get("sla_notified_at")))
        return ts is None or ts < renotify_before

    rep_due: dict[int, list[dict]] = {}
    owner_due: list[dict] = []
    for r in rows:
        if _due(r, "rep"):
            rep_due.setdefault(int(r["salesman_id"]), []).append(r)
        if _age_min(r, now, since="assigned_at") >= 2 * sla and _due(r, "owner"):
            owner_due.append(r)
    if not rep_due and not owner_due:
        return out

    delivered: dict[int, dict] = {}       # order id → {"rep": bool, "owner": bool}
    portal = _base()
    for sid, orders in rep_due.items():
        sm = shop._salesman_by_id(sid) or {}
        lines = "\n".join(_order_line(r, now, since="assigned_at") for r in orders)
        text = (f"{len(orders)} order(s) are waiting for your confirmation (the merchant sees 'Received' until you confirm):\n"
                f"{lines}")
        if portal:
            text += f"\nOpen: {portal}/shop-orders" + (f"?open={orders[0]['id']}" if len(orders) == 1 else "?bucket=new")
        res = _rep_alert(sm, f"YQ Shop · {len(orders)} order(s) waiting for your confirmation", text)
        out["reps"][sm.get("name") or str(sid)] = res
        for r in orders:
            delivered.setdefault(r["id"], {})["rep"] = bool(res["sent"])
    if owner_due:
        text = (f"{len(owner_due)} marketplace order(s) unconfirmed for over {_fmt_wait(2 * sla)} — the rep was reminded:\n"
                + "\n".join(_order_line(r, now, rep=True, since="assigned_at") for r in owner_due))
        if portal:
            text += f"\nOrders: {portal}/shop-orders"
        res = _owner_alert(f"YQ Marketplace · {len(owner_due)} order(s) unconfirmed past {_fmt_wait(2 * sla)}", text)
        out["owner"] = {"telegram": bool((res.get("telegram") or {}).get("sent")),
                        "email": bool((res.get("email") or {}).get("sent")), "sent": res["sent"]}
        for r in owner_due:
            delivered.setdefault(r["id"], {})["owner"] = bool(res["sent"])

    stamped: list[int] = []
    for r in rows:
        d = delivered.get(r["id"])
        if not d or not any(d.values()):
            continue                      # nothing reached anyone: no stamp, no event, retry next run
        _event(client, r["id"], "reminded", {"rep": bool(d.get("rep")), "owner": bool(d.get("owner")),
                                              "level": "owner" if d.get("owner") else "rep",
                                              "age_min": _age_min(r, now), "sla_min": sla})
        stamped.append(r["id"])
        if d.get("rep"):
            out["reminded"].append(r["order_no"])
        if d.get("owner"):
            out["escalated"].append(r["order_no"])
    if stamped:
        _stamp(client, stamped, now)
    elif rep_due or owner_due:
        out["reason"] = "no channel delivered — will retry next run"
    return out


def cleanup() -> dict:
    """Drop expired / revoked sessions and used or expired access links older than KEEP_DAYS."""
    client = get_client()
    now = _now().isoformat()
    old = (_now() - timedelta(days=KEEP_DAYS)).isoformat()
    out: dict = {}
    try:
        client.table("shop_customer_sessions").delete().lt("expires_at", now).execute()
        client.table("shop_customer_sessions").delete().not_.is_("revoked_at", "null").lt("revoked_at", old).execute()
        client.table("shop_access_links").delete().lt("expires_at", old).execute()
        client.table("shop_access_links").delete().not_.is_("used_at", "null").lt("created_at", old).execute()
        out["ok"] = True
    except Exception as e:  # noqa: BLE001 — the tables arrive with marketplace_migration.sql
        out["error"] = f"{type(e).__name__}: {e}"[:160]
    return out


def stale_data_alert() -> dict:
    """One owner alert a day while the stock snapshot on the marketplace is older than shop_stale_days.
    Telegram first; the owner email only when Telegram did not deliver. Marked as alerted ONLY when
    a channel really delivered, so a dead channel never silences the alert for 24 h."""
    from app import shop, shop_notify
    days = shop._i(shop.shop_settings().get("shop_stale_days"), 3)
    if days <= 0:
        return {"skipped": "shop_stale_days is 0"}
    rows = shop.exec_sql("SELECT max(as_of_date)::text AS as_of FROM v_catalog_stock") or []
    as_of = _parse((rows[0] or {}).get("as_of")) if rows else None
    now = _now()
    age = (now - as_of).days if as_of else None
    out: dict = {"as_of": as_of.date().isoformat() if as_of else None, "age_days": age, "alerted": False}
    if age is None or age < days:
        return out
    client = get_client()
    last = None
    try:
        row = client.table("app_settings").select("value").eq("key", "shop_stale_alerted_at").limit(1).execute().data or []
        last = _parse(row[0].get("value")) if row else None
    except Exception:  # noqa: BLE001 — a missing marker just means "never alerted"
        last = None
    if last and last > now - timedelta(hours=STALE_RENOTIFY_HOURS):
        out["skipped"] = "alerted in the last 24 h"
        return out
    text = (f"Stock data on the marketplace is {age} days old (as of {as_of:%d %b}). "
            f"Merchants see stale availability.\nUpload the Focus Stock Balance report"
            + (f": {_base()}/data" if _base() else "."))
    tg = shop_notify._telegram(text)
    out["telegram"] = bool(tg.get("sent"))
    if not tg.get("sent"):
        owner = os.getenv("ALERT_EMAIL_TO", "")
        em = (shop_notify._email(f"YQ Marketplace · stock data is {age} days old", _text_html(text), owner)
              if owner else {"sent": False, "reason": "ALERT_EMAIL_TO unset"})
        out["email"] = bool(em.get("sent"))
        if not em.get("sent"):
            out["reason"] = f"telegram: {tg.get('reason') or 'not delivered'}; email: {em.get('reason') or 'not delivered'}"[:240]
    if not (out.get("telegram") or out.get("email")):
        return out                        # nobody heard it: leave the marker alone and try next run
    try:
        client.table("app_settings").upsert({"key": "shop_stale_alerted_at", "value": now.isoformat(),
                                             "updated_by": "shop_jobs", "updated_at": now.isoformat()},
                                            on_conflict="key").execute()
        out["alerted"] = True
    except Exception as e:  # noqa: BLE001
        out["marker"] = f"{type(e).__name__}: {e}"[:120]
    return out


JOBS = (("notify_retry", notify_retry), ("unassigned_reminder", unassigned_reminder),
        ("unconfirmed_reminder", unconfirmed_reminder), ("cleanup", cleanup),
        ("stale_data_alert", stale_data_alert))


def run_shop_jobs() -> dict:
    """Run every job; one failing never stops the others. `ok` is False when any job errored, so
    the cron caller can tell a quiet run from a broken one (the HTTP status stays 200)."""
    out: dict = {"at": _now().isoformat()}
    errors: list[str] = []
    for name, fn in JOBS:
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — one job failing must not stop the others
            log.warning("shop job %s failed: %s", name, e)
            out[name] = {"error": f"{type(e).__name__}: {e}"[:200]}
        if isinstance(out[name], dict) and out[name].get("error"):
            errors.append(name)
    out["ok"] = not errors
    if errors:
        out["errors"] = errors
    return out
