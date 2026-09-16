"""Marketplace housekeeping, run by the GitHub Actions cron (.github/workflows/shop-cron.yml)
every 15 minutes through GET /scheduler/shop-jobs (X-Agent-Key). n8n is down; GitHub's free
scheduler is the replacement for everything the shop needs to happen without a human.
Everything here is idempotent and safe to run often.

Jobs:
  * unassigned_reminder — an order nobody owns after shop_assign_sla_min minutes is re-alerted
    to the owner channel (Telegram + owner email) at most every two hours until someone assigns
    it. GitHub cron drifts by minutes, so this is an internal nudge, never a merchant-facing promise.
  * cleanup — expired or revoked merchant sessions and stale access links are removed.
  * stale_data_alert — when the stock snapshot merchants see is older than shop_stale_days, the
    owner channel gets one Telegram a day asking for the Focus report (stale availability costs
    trust and confirmations).
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


STALE_RENOTIFY_HOURS = 24


def stale_data_alert() -> dict:
    """One Telegram a day while the stock snapshot on the marketplace is older than shop_stale_days."""
    from app import shop
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
    try:
        from app.notify import send_telegram
        out["telegram"] = bool(send_telegram(text))
    except Exception as e:  # noqa: BLE001
        out["telegram"] = f"{type(e).__name__}: {e}"[:120]
    try:
        client.table("app_settings").upsert({"key": "shop_stale_alerted_at", "value": now.isoformat(),
                                             "updated_by": "shop_jobs", "updated_at": now.isoformat()},
                                            on_conflict="key").execute()
        out["alerted"] = True
    except Exception as e:  # noqa: BLE001
        out["marker"] = f"{type(e).__name__}: {e}"[:120]
    return out


def run_shop_jobs() -> dict:
    out: dict = {"at": _now().isoformat()}
    for name, fn in (("unassigned_reminder", unassigned_reminder), ("cleanup", cleanup),
                     ("stale_data_alert", stale_data_alert)):
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — one job failing must not stop the others
            log.warning("shop job %s failed: %s", name, e)
            out[name] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return out
