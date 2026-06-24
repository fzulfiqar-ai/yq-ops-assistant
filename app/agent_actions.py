"""Phase C — agents draft real actions for HUMAN APPROVAL (governance preserved).

Deterministic drafters turn an agent's top rows into EXISTING action types and submit
them as pending actions via `actions.submit_action` — the admin approve/reject/export
flow is untouched. A separate communication drafter writes bilingual EN/AR debtor
reminders (copy/send, not an ERP action).

Guardrails: opt-in (caller decides), cap ≤10, dedupe vs existing pending actions of the
same type+item, and a data-freshness guard (never draft off stale data).
"""
from __future__ import annotations

import datetime
import json
import logging

from app.actions import list_actions, submit_action
from app.agents import run_agent

log = logging.getLogger(__name__)

MAX_DRAFTS = 10
AGENT_EMAIL = "agent@yqbahrain.local"
SUPPORTED = {"inventory", "margin", "anomaly", "collections"}  # agents that can draft

# agent -> (action_type, result_list_key, item_field, payload_builder)
_DRAFTERS = {
    "inventory": ("reorder_stock", "items", "item_name",
                  lambda r: {"qty": r.get("suggested_reorder_qty"), "current_stock": r.get("current_stock"),
                             "sold_90d": r.get("sold_90d"), "reason": r.get("status")}),
    "margin": ("price_change", "negative_margins", "item_name",
               lambda r: {"gp_margin_pct": r.get("gp_margin_pct"), "reason": "selling below cost — review price"}),
    "anomaly": ("write_off", "dead_stock", "item_name",
                lambda r: {"current_stock": r.get("current_stock"), "stock_value": r.get("stock_value"),
                           "reason": "dead stock — consider write-off / clearance"}),
}


def _data_fresh(max_age_days: int = 10) -> tuple[bool, str]:
    from app.reports import data_as_of
    d = data_as_of()
    if not d:
        return False, "unknown"
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(str(d)[:10])).days
        return age <= max_age_days, str(d)[:10]
    except Exception:  # noqa: BLE001
        return True, str(d)[:10]


def _pending_items(action_type: str) -> set[str]:
    """Item identifiers already pending for this action type (for dedupe)."""
    keys: set[str] = set()
    for a in list_actions(status="pending"):
        if a.get("action_type") != action_type:
            continue
        p = a.get("payload")
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:  # noqa: BLE001
                p = {}
        k = (p or {}).get("item")
        if k:
            keys.add(str(k))
    return keys


def draft_for_agent(name: str, result: dict | None = None, requested_by: str = AGENT_EMAIL) -> dict:
    """Draft pending actions from an agent's result. Returns a status dict (never raises)."""
    if name not in _DRAFTERS:
        return {"drafted": 0, "skipped": 0, "action_ids": [], "reason": f"{name} does not draft actions"}
    fresh, as_of = _data_fresh()
    if not fresh:
        return {"drafted": 0, "skipped": 0, "action_ids": [], "reason": f"data is stale (as of {as_of}) — refresh before drafting"}

    if result is None:
        result = run_agent(name, triggered_by="user")
    action_type, list_key, item_field, build = _DRAFTERS[name]
    rows = result.get(list_key) or []
    existing = _pending_items(action_type)
    ids, skipped = [], 0
    for row in rows[:MAX_DRAFTS]:
        item = str(row.get(item_field, "")).strip()
        if not item or item in existing:
            skipped += 1
            continue
        payload = {**build(row), "item": item, "source": f"agent:{name}", "data_as_of": as_of}
        try:
            a = submit_action(action_type, payload, requested_by=requested_by)
            ids.append(a.get("id"))
            existing.add(item)
        except Exception as e:  # noqa: BLE001
            log.warning("draft action failed for %s/%s: %s", name, item, e)
            skipped += 1
    return {"drafted": len(ids), "skipped": skipped, "action_ids": ids, "action_type": action_type, "data_as_of": as_of}


def draft_reminders(result: dict | None = None) -> dict:
    """Bilingual EN/AR debtor reminder drafts (copy/send) — not an ERP action."""
    if result is None:
        result = run_agent("collections", triggered_by="user")
    out = []
    for r in (result.get("items") or [])[:MAX_DRAFTS]:
        acct = str(r.get("account", "")).strip()
        amt = float(r.get("outstanding_bhd") or 0)
        overdue = float(r.get("overdue_bhd") or 0)
        if not acct:
            continue
        out.append({
            "account": acct, "outstanding_bhd": amt, "overdue_bhd": overdue,
            "message_en": (f"Dear {acct}, our records show an outstanding balance of BHD {amt:,.3f}, "
                           f"of which BHD {overdue:,.3f} is overdue. We would appreciate settlement at "
                           f"your earliest convenience. Thank you — YQ Bahrain W.L.L."),
            "message_ar": (f"السادة {acct} المحترمين،\nتشير سجلاتنا إلى وجود رصيد مستحق بقيمة "
                           f"{amt:,.3f} د.ب، منها {overdue:,.3f} د.ب متأخرة السداد. نرجو التكرم بتسوية "
                           f"المبلغ في أقرب وقت ممكن. شكراً لتعاونكم — واي كيو البحرين."),
        })
    return {"count": len(out), "reminders": out}
