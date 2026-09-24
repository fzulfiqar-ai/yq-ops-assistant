"""Marketplace housekeeping, run through GET /scheduler/shop-jobs (X-Agent-Key) every 15 minutes by
the Cloudflare Worker cron (web/wrangler.keepwarm.jsonc, 24-Sep-2026). .github/workflows/shop-cron.yml
keeps only its manual trigger (its schedule is commented out: GitHub's free scheduler really fired
about every 3.4 h, and two callers would overlap). n8n is down. Everything here is idempotent and
safe to run often.

Lease (24-Sep-2026): run_shop_jobs() first takes a short lease — app_settings key 'shop_jobs_lock'
holding an ISO expiry LOCK_TTL_MIN ahead, taken with a conditional update (value < now) so two callers
cannot both win — and skips the whole run while another caller holds a live one; the lease is released
at the end. Without it a Worker tick and a manual GitHub run landing together would send every
reminder twice.

Jobs (in run order):
  * notify_retry — an order younger than 48 h whose new-order alert reached nobody (every internal
    channel in notify_result failed, the fan-out raised before any send, or no result was written
    within NOTIFY_GRACE_MIN) gets notify_new_order again, at most MAX_NOTIFY_ATTEMPTS times in all;
    channels that already delivered are kept. Not gated by the send window: it is the first alert.
  * unassigned_reminder — an order nobody owns after shop_assign_sla_min minutes is re-alerted
    to the owner channel (Telegram + owner email) at most every two hours until someone assigns it.
  * unconfirmed_reminder — an order a rep DOES own but has left in 'new' past shop_confirm_sla_min
    minutes from ASSIGNMENT: the rep is reminded (email + WhatsApp Cloud when configured), at most
    every shop_confirm_renotify_hours; past twice the SLA the owner channel is told as well. Every
    attempt is a shop_order_events row event='reminded' — detail.rep/owner say who was reached,
    detail.attempted who was tried — so a channel that did not deliver is tried again at most every
    REP_RETRY_MIN, not every run. Rows older than the order's assigned_at are ignored, so a
    reassigned order starts its cadence over. After ESCALATION_MAX_DAYS from assignment or
    ESCALATION_MAX_REMINDERS delivered nudges the per-order chasing stops and the order becomes one
    line in a single daily owner digest. sla_notified_at is stamped only when someone was reached.
  * cleanup — expired or revoked merchant sessions and stale access links are removed.
  * stale_data_alert — when the stock snapshot merchants see is older than shop_stale_days, the
    owner channel gets one Telegram (owner email when Telegram is not configured) a day asking for
    the Focus report. The "alerted today" marker is written only when a channel really delivered.

Nudges (unassigned, unconfirmed, digest) go out only between SEND_FROM_H and SEND_UNTIL_H Bahrain;
outside that window the jobs answer skipped and try again on the next tick. A reminder is stamped
only when at least one channel delivered: with every channel dead the job keeps trying (hourly)
instead of pretending someone was told (D-13). Cron drifts by minutes, so all of this is an internal
nudge, never a merchant-facing promise.
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
RETRY_PER_RUN = 10           # each fan-out is up to four sends; bound the run
STALE_RENOTIFY_HOURS = 24

# unconfirmed_reminder back-off (24-Sep-2026 review): a dead channel is not retried every 15 min
REP_RETRY_MIN = 60           # a rep/owner channel that did not deliver is tried again at most hourly
ESCALATION_MAX_DAYS = 7      # per-order chasing stops this long after assignment ...
ESCALATION_MAX_REMINDERS = 10   # ... or after this many delivered nudges; then one daily digest line
DIGEST_RENOTIFY_HOURS = 20   # the daily digest settles on the first tick after SEND_FROM_H each day
DIGEST_MARKER = "shop_unconfirmed_digest_at"

# nudges only in waking hours (Bahrain, UTC+3): 07:00 ≤ local time < 22:00
_BAHRAIN = timezone(timedelta(hours=3))
SEND_FROM_H, SEND_UNTIL_H = 7, 22

LOCK_KEY = "shop_jobs_lock"
LOCK_TTL_MIN = 10
_LOCK_RELEASED = "1970-01-01T00:00:00Z"   # a released lease sorts before any live timestamp


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


def _lock_ts(d: datetime) -> str:
    """Fixed-width UTC stamp (no microseconds, 'Z'), so PostgREST's text `lt` compares digit by digit
    whatever the column collation does with punctuation."""
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _base() -> str:
    return (os.getenv("APP_BASE_URL", "") or "").rstrip("/")


def _age_min(row: dict, now: datetime, since: str = "created_at") -> int:
    start = _parse(row.get(since)) or _parse(row.get("created_at"))
    return int((now - start).total_seconds() // 60) if start else 0


def _fmt_wait(minutes: int) -> str:
    if minutes >= 2 * 1440:
        return f"{minutes // 1440} days"
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _order_line(r: dict, now: datetime, rep: bool = False, since: str = "created_at") -> str:
    return (f"• {r['order_no']} · {r.get('customer_shop') or 'shop'} · {r.get('customer_area') or '-'}"
            f" · BHD {float(r.get('total_bhd') or 0):.3f} · waiting {_fmt_wait(_age_min(r, now, since))}"
            + (f" · {r.get('salesman_name') or 'rep'}" if rep else ""))


def _text_html(text: str) -> str:
    return "<p>" + _html.escape(text).replace("\n", "<br>") + "</p>"


def in_send_window(now: datetime | None = None) -> bool:
    """True between SEND_FROM_H:00 and SEND_UNTIL_H:00 Bahrain — the only hours a nudge goes out."""
    h = (now or _now()).astimezone(_BAHRAIN).hour
    return SEND_FROM_H <= h < SEND_UNTIL_H


def _outside_window(now: datetime) -> dict:
    return {"skipped": f"outside {SEND_FROM_H:02d}:00-{SEND_UNTIL_H:02d}:00 Bahrain "
                       f"(now {now.astimezone(_BAHRAIN):%H:%M})"}


# ── lease (one run at a time) ─────────────────────────────────────────────────

def acquire_lease(client, key: str = LOCK_KEY, ttl_min: int = LOCK_TTL_MIN, owner: str = "shop_jobs") -> dict:
    """Take app_settings[key] = expiry (ttl_min ahead) unless a live expiry is already there.
    The take-over of an expired lease is ONE conditional update (WHERE value < now), so two callers
    racing for it cannot both win; a first-ever lease is an insert, whose unique violation means
    the other caller was first. Returns {"acquired": bool, "expires_at"|"held_until"|"error"}.
    A read/update error answers acquired=True with the error (fail-open: a broken lock must not
    silently stop every job for good — the jobs themselves report their own failures)."""
    now = _now()
    now_s, expiry = _lock_ts(now), _lock_ts(now + timedelta(minutes=ttl_min))
    row = {"key": key, "value": expiry, "updated_by": owner, "updated_at": now.isoformat()}
    try:
        res = client.table("app_settings").update(row).eq("key", key).lt("value", now_s).execute()
        if res.data:
            return {"acquired": True, "expires_at": expiry}
        cur = client.table("app_settings").select("value").eq("key", key).limit(1).execute().data or []
        if cur:
            held = _parse(cur[0].get("value"))
            if held and held > now:
                return {"acquired": False, "held_until": cur[0].get("value")}
            # an unparseable value or the same-second edge: nobody live holds it — take it
            client.table("app_settings").update(row).eq("key", key).execute()
            return {"acquired": True, "expires_at": expiry}
    except Exception as e:  # noqa: BLE001
        log.warning("lease %s read/update failed (running anyway): %s", key, e)
        return {"acquired": True, "error": f"{type(e).__name__}: {e}"[:160]}
    try:
        client.table("app_settings").insert(row).execute()
        return {"acquired": True, "expires_at": expiry}
    except Exception as e:  # noqa: BLE001 — unique violation: the other caller inserted first
        return {"acquired": False, "error": f"{type(e).__name__}: {e}"[:160]}


def release_lease(client, key: str = LOCK_KEY, owner: str = "shop_jobs", expires_at: str | None = None) -> None:
    """Release only the lease this run still holds: when `expires_at` is known the update is pinned
    to it, so a run that outlived its TTL can never free a newer caller's live lease."""
    try:
        qry = client.table("app_settings").update({"value": _LOCK_RELEASED, "updated_by": owner,
                                                   "updated_at": _now().isoformat()}).eq("key", key)
        if expires_at:
            qry = qry.eq("value", expires_at)
        qry.execute()
    except Exception as e:  # noqa: BLE001 — it expires on its own after LOCK_TTL_MIN
        log.debug("lease %s release failed: %s", key, e)


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
    """One audit row per nudge or attempt. shop_order_events.event has no CHECK constraint (verified
    live, 24-Sep-2026), so 'reminded' needs no migration. The detail never carries a provider's
    error text (it names accounts); the notify panel on the order has that."""
    try:
        client.table("shop_order_events").insert({
            "order_id": order_id, "actor": "shop_jobs", "event": event, "detail": detail}).execute()
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("event %s for order %s failed: %s", event, order_id, e)
        return False


def _marker(client, key: str) -> datetime | None:
    try:
        row = client.table("app_settings").select("value").eq("key", key).limit(1).execute().data or []
        return _parse(row[0].get("value")) if row else None
    except Exception:  # noqa: BLE001 — a missing marker just means "never"
        return None


def _set_marker(client, key: str, now: datetime) -> str | None:
    try:
        client.table("app_settings").upsert({"key": key, "value": now.isoformat(), "updated_by": "shop_jobs",
                                             "updated_at": now.isoformat()}, on_conflict="key").execute()
        return None
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"[:120]


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
        if not shop_notify.notify_failed(nr, r.get("created_at"), now):
            continue                                   # someone was told, or the background task may still be running
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
    if not in_send_window(now):
        return _outside_window(now)
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


def _reminder_history(client, rows: list[dict]) -> dict[int, dict] | None:
    """Per order, from the 'reminded' audit rows written since its CURRENT assignment (older rows
    belong to the previous rep and are ignored): "rep"/"owner" = last delivered nudge,
    "rep_try"/"owner_try" = last attempt whether or not it delivered, "count" = delivered nudges.
    None when the read failed (the caller then falls back to sla_notified_at)."""
    ids = [r["id"] for r in rows]
    since = {r["id"]: _parse(r.get("assigned_at")) for r in rows}
    out: dict[int, dict] = {i: {"count": 0} for i in ids}
    if not ids:
        return out
    # Only the window that can matter (since the oldest current assignment, capped at 8 days),
    # newest first: PostgREST caps a response at 1000 rows, so if a cap ever bites it drops the
    # OLDEST rows, never the newest ones the back-off and the cadence depend on.
    starts = [t for t in since.values() if t]
    floor = max(min(starts) if starts else _now() - timedelta(days=8), _now() - timedelta(days=8))
    try:
        evs = (client.table("shop_order_events").select("order_id,ts,detail")
               .eq("event", "reminded").in_("order_id", ids).gte("ts", floor.isoformat())
               .order("id", desc=True).limit(5000).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.debug("reminded events read failed: %s", e)
        return None
    for e in evs:
        ts = _parse(e.get("ts"))
        oid = int(e.get("order_id") or 0)
        if not ts or oid not in out:
            continue
        if since.get(oid) and ts < since[oid]:
            continue                                   # before this rep got the order
        d = e.get("detail") if isinstance(e.get("detail"), dict) else {}
        tried = d.get("attempted") if isinstance(d.get("attempted"), dict) else {}
        slot = out[oid]
        for who in ("rep", "owner"):            # keep the NEWEST stamp whatever the row order
            if d.get(who) and (slot.get(who) is None or ts > slot[who]):
                slot[who] = ts
            if (d.get(who) or tried.get(who)) and (slot.get(who + "_try") is None or ts > slot[who + "_try"]):
                slot[who + "_try"] = ts
        if d.get("rep") or d.get("owner"):
            slot["count"] += 1
    return out


def unconfirmed_reminder() -> dict:
    """Chase orders a rep owns but has left in 'new' past shop_confirm_sla_min (from assignment)."""
    from app import shop
    vals = shop.shop_settings()
    sla = shop._i(vals.get("shop_confirm_sla_min"), 120)
    if sla <= 0:
        return {"skipped": "shop_confirm_sla_min is 0"}
    renotify_h = max(1, shop._i(vals.get("shop_confirm_renotify_hours"), 12))
    now = _now()
    if not in_send_window(now):
        return _outside_window(now)
    cutoff = (now - timedelta(minutes=sla)).isoformat()
    renotify_before = now - timedelta(hours=renotify_h)
    retry_before = now - timedelta(minutes=REP_RETRY_MIN)
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
    hist = _reminder_history(client, rows)

    def slot(r: dict) -> dict:
        return (hist or {}).get(r["id"], {})

    def _due(r: dict, who: str) -> bool:
        # The audit rows say who was told (and tried) when; sla_notified_at (shared with the
        # unassigned nudge) is only the fallback when those rows could not be read.
        if hist is None:
            last = tried = _parse(r.get("sla_notified_at"))
        else:
            last, tried = slot(r).get(who), slot(r).get(who + "_try")
        if last and last >= renotify_before:
            return False                               # told recently
        if tried and tried >= retry_before:
            return False                               # tried and failed less than an hour ago
        return True

    def _capped(r: dict) -> bool:
        return (_age_min(r, now, since="assigned_at") >= ESCALATION_MAX_DAYS * 1440
                or slot(r).get("count", 0) >= ESCALATION_MAX_REMINDERS)

    stale = [r for r in rows if _capped(r)]
    active = [r for r in rows if not _capped(r)]
    rep_due: dict[int, list[dict]] = {}
    owner_due: list[dict] = []
    backoff: list[str] = []
    for r in active:
        rep_ok = _due(r, "rep")
        if rep_ok:
            rep_due.setdefault(int(r["salesman_id"]), []).append(r)
        esc = _age_min(r, now, since="assigned_at") >= 2 * sla
        owner_ok = esc and _due(r, "owner")
        if owner_ok:
            owner_due.append(r)
        if not rep_ok and hist is not None:
            s = slot(r)
            recent_try = bool(s.get("rep_try") and s["rep_try"] >= retry_before)
            told_recently = bool(s.get("rep") and s["rep"] >= renotify_before)
            if recent_try and not told_recently:
                backoff.append(r["order_no"])          # a dead rep channel, holding for the hour
    if backoff:
        out["backoff"] = backoff
    if stale:
        out["digest"] = _stale_digest(client, stale, now, sla)
    if not rep_due and not owner_due:
        return out

    tried: dict[int, dict] = {}           # order id → {"rep": delivered?, "owner": delivered?} (present = attempted)
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
            tried.setdefault(r["id"], {})["rep"] = bool(res["sent"])
    if owner_due:
        text = (f"{len(owner_due)} marketplace order(s) unconfirmed for over {_fmt_wait(2 * sla)} — the rep was reminded:\n"
                + "\n".join(_order_line(r, now, rep=True, since="assigned_at") for r in owner_due))
        if portal:
            text += f"\nOrders: {portal}/shop-orders"
        res = _owner_alert(f"YQ Marketplace · {len(owner_due)} order(s) unconfirmed past {_fmt_wait(2 * sla)}", text)
        out["owner"] = {"telegram": bool((res.get("telegram") or {}).get("sent")),
                        "email": bool((res.get("email") or {}).get("sent")), "sent": res["sent"]}
        for r in owner_due:
            tried.setdefault(r["id"], {})["owner"] = bool(res["sent"])

    stamped: list[int] = []
    for r in active:
        d = tried.get(r["id"])
        if not d:
            continue
        got_rep, got_owner = bool(d.get("rep")), bool(d.get("owner"))
        # Every attempt is recorded — a failed one too, so the next run backs off for an hour
        # instead of hammering a dead channel; only a delivered one stamps sla_notified_at.
        _event(client, r["id"], "reminded", {
            "rep": got_rep, "owner": got_owner,
            "attempted": {"rep": "rep" in d, "owner": "owner" in d}, "attempted_at": now.isoformat(),
            "level": "owner" if got_owner else ("rep" if got_rep else "attempt"),
            "age_min": _age_min(r, now, since="assigned_at"), "sla_min": sla})
        if got_rep:
            out["reminded"].append(r["order_no"])
        if got_owner:
            out["escalated"].append(r["order_no"])
        if got_rep or got_owner:
            stamped.append(r["id"])
    if stamped:
        _stamp(client, stamped, now)
    else:
        out["reason"] = f"no channel delivered — next try in {REP_RETRY_MIN} min"
    return out


def _stale_digest(client, stale: list[dict], now: datetime, sla: int) -> dict:
    """Orders past ESCALATION_MAX_DAYS or ESCALATION_MAX_REMINDERS: no more per-order nudges, one
    owner digest a day (marker DIGEST_MARKER in app_settings, written only when it delivered).
    Each order in a delivered digest gets a 'reminded' row with level 'digest' for its timeline."""
    out: dict = {"orders": [r["order_no"] for r in stale], "sent": False}
    last = _marker(client, DIGEST_MARKER)
    if last and last > now - timedelta(hours=DIGEST_RENOTIFY_HOURS):
        out["skipped"] = f"digest sent in the last {DIGEST_RENOTIFY_HOURS} h"
        return out
    text = (f"{len(stale)} marketplace order(s) still unconfirmed after {ESCALATION_MAX_DAYS} days or "
            f"{ESCALATION_MAX_REMINDERS} reminders — per-order reminders have stopped; this is the once-a-day "
            f"list until they are confirmed or cancelled:\n"
            + "\n".join(_order_line(r, now, rep=True, since="assigned_at") for r in stale))
    if _base():
        text += f"\nOrders: {_base()}/shop-orders?bucket=new"
    res = _owner_alert(f"YQ Marketplace · daily digest: {len(stale)} order(s) still unconfirmed", text)
    out["telegram"] = bool((res.get("telegram") or {}).get("sent"))
    out["email"] = bool((res.get("email") or {}).get("sent"))
    out["sent"] = res["sent"]
    if not res["sent"]:
        out["reason"] = "no channel delivered — will retry next run"
        return out
    err = _set_marker(client, DIGEST_MARKER, now)
    if err:
        out["marker"] = err
    for r in stale:
        _event(client, r["id"], "reminded", {"rep": False, "owner": True, "level": "digest",
                                              "attempted": {"rep": False, "owner": True}, "attempted_at": now.isoformat(),
                                              "age_min": _age_min(r, now, since="assigned_at"), "sla_min": sla})
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
    last = _marker(client, "shop_stale_alerted_at")
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
    err = _set_marker(client, "shop_stale_alerted_at", now)
    if err:
        out["marker"] = err
    else:
        out["alerted"] = True
    return out


def sweep_lineless() -> dict:
    """Cancel (never delete) Received headers left without lines by a failed create (app/shop.py)."""
    from app import shop
    return shop.sweep_lineless_orders(10)


JOBS = (("notify_retry", notify_retry), ("unassigned_reminder", unassigned_reminder),
        ("unconfirmed_reminder", unconfirmed_reminder), ("cleanup", cleanup),
        ("stale_data_alert", stale_data_alert), ("sweep_lineless", sweep_lineless))


def run_shop_jobs() -> dict:
    """Run every job under the lease; one failing never stops the others. `ok` is False when any
    job errored, so the cron caller can tell a quiet run from a broken one (the HTTP status stays
    200). While another caller holds a live lease the answer is {ok: true, skipped: ...}."""
    out: dict = {"at": _now().isoformat()}
    client = None
    try:
        client = get_client()
        lease = acquire_lease(client)
    except Exception as e:  # noqa: BLE001 — no client at all: the jobs will say so themselves
        lease = {"acquired": True, "error": f"{type(e).__name__}: {e}"[:160]}
    if not lease.get("acquired"):
        out.update(ok=True, skipped="another run holds the lease", lease=lease)
        return out
    if lease.get("error"):
        out["lease"] = lease
    errors: list[str] = []
    try:
        for name, fn in JOBS:
            try:
                out[name] = fn()
            except Exception as e:  # noqa: BLE001 — one job failing must not stop the others
                log.warning("shop job %s failed: %s", name, e)
                out[name] = {"error": f"{type(e).__name__}: {e}"[:200]}
            if isinstance(out[name], dict) and out[name].get("error"):
                errors.append(name)
    finally:
        if client is not None:
            release_lease(client, expires_at=lease.get("expires_at"))
    out["ok"] = not errors
    if errors:
        out["errors"] = errors
    return out
