"""Marketplace housekeeping, run by the GitHub Actions cron (.github/workflows/shop-cron.yml)
every 15 minutes through GET /scheduler/shop-jobs (X-Agent-Key). n8n is down; GitHub's free
scheduler is the replacement for everything the shop needs to happen without a human.
Everything here is idempotent and safe to run often.

Jobs:
  * unassigned_reminder — an order nobody owns after shop_assign_sla_min minutes is re-alerted
    to the owner channel (Telegram + owner email) at most every two hours until someone assigns
    it. GitHub cron drifts by minutes, so this is an internal nudge, never a merchant-facing promise.
  * cleanup — expired or revoked merchant sessions and stale access links are removed.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from app.database import get_client

log = logging.getLogger(__name__)

RENOTIFY_HOURS = 2
KEEP_DAYS = 30


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
    lines = []
    for r in due:
        created = _parse(r.get("created_at"))
        age = int((now - created).total_seconds() // 60) if created else 0
        lines.append(f"• {r['order_no']} · {r.get('customer_shop') or 'shop'} · {r.get('customer_area') or '-'}"
                     f" · BHD {float(r.get('total_bhd') or 0):.3f} · waiting {age} min")
    text = (f"{len(due)} marketplace order(s) still UNASSIGNED after {sla} minutes:\n" + "\n".join(lines))
    if _base():
        text += f"\nAssign: {_base()}/shop-orders?queue=1"
    result: dict = {"checked": len(rows), "notified": len(due), "orders": [r["order_no"] for r in due]}
    try:
        from app.notify import send_telegram
        result["telegram"] = bool(send_telegram(text))
    except Exception as e:  # noqa: BLE001
        result["telegram"] = f"{type(e).__name__}: {e}"[:120]
    owner = os.getenv("ALERT_EMAIL_TO", "")
    if owner:
        try:
            from app.emailer import send_html
            import html as _html
            body = "<p>" + _html.escape(text).replace("\n", "<br>") + "</p>"
            r = send_html(f"YQ Marketplace · {len(due)} unassigned order(s)", body, to=owner)
            result["email"] = bool(r.get("emailed"))
        except Exception as e:  # noqa: BLE001
            result["email"] = f"{type(e).__name__}: {e}"[:120]
    stamp = now.isoformat()
    for r in due:
        try:
            client.table("shop_orders").update({"sla_notified_at": stamp}).eq("id", r["id"]).execute()
        except Exception as e:  # noqa: BLE001
            log.debug("sla stamp failed for %s: %s", r.get("order_no"), e)
    return result


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


def run_shop_jobs() -> dict:
    out: dict = {"at": _now().isoformat()}
    for name, fn in (("unassigned_reminder", unassigned_reminder), ("cleanup", cleanup)):
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — one job failing must not stop the others
            log.warning("shop job %s failed: %s", name, e)
            out[name] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return out
