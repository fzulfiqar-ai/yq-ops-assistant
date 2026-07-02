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
import os

from app.actions import list_actions, submit_action
from app.agents import run_agent

log = logging.getLogger(__name__)

MAX_DRAFTS = 10
AGENT_EMAIL = "agent@yqbahrain.local"
SUPPORTED = {"inventory", "margin", "anomaly", "collections", "reorder_proposal"}  # agents that can draft

# ── Circuit breaker (the "governor") ─────────────────────────────────────────
# Bounds what an agent draft can even PROPOSE. The human approval step is the real breaker;
# these caps stop an agent from flooding the queue or drafting a reckless value. A violation is
# logged + emitted as platform.alert and simply NOT drafted (never silently executed).
POLICIES: dict[str, dict] = {
    "create_po":     {"max_value_bhd": 3000.0, "max_per_day": 3},
    "price_change":  {"max_change_pct": 20.0,  "max_per_day": 10},
    "reorder_stock": {"max_per_day": 10},
    "write_off":     {"max_value_bhd": 500.0,  "max_per_day": 5},
}

# Global kill switch — set AGENT_DRAFTS_ENABLED=0 to stop ALL agent drafting instantly.
def _drafts_enabled() -> bool:
    return os.getenv("AGENT_DRAFTS_ENABLED", "1").lower() not in ("0", "false", "no", "off")


def _drafted_today(action_type: str) -> int:
    """How many of this action type the agent has already drafted today (per-day cap)."""
    today = datetime.date.today().isoformat()
    n = 0
    for a in list_actions():
        if a.get("action_type") != action_type or a.get("requested_by") != AGENT_EMAIL:
            continue
        if str(a.get("requested_at", ""))[:10] == today:
            n += 1
    return n


def policy_check(action_type: str, payload: dict) -> tuple[bool, str]:
    """Return (ok, reason). Enforces per-value and per-day caps from POLICIES."""
    pol = POLICIES.get(action_type)
    if not pol:
        return True, ""
    if "max_value_bhd" in pol:
        val = float(payload.get("est_value_bhd") or payload.get("stock_value") or 0)
        if val > pol["max_value_bhd"]:
            return False, f"value BHD {val:,.0f} exceeds cap BHD {pol['max_value_bhd']:,.0f}"
    if "max_change_pct" in pol and payload.get("change_pct") is not None:
        # Only caps an EXPLICIT proposed price-change magnitude. The margin drafter carries
        # gp_margin_pct (a diagnosis, not a proposed change), so it isn't gated here.
        chg = abs(float(payload.get("change_pct") or 0))
        if chg > pol["max_change_pct"]:
            return False, f"proposed change {chg:.0f}% exceeds cap {pol['max_change_pct']:.0f}%"
    if "max_per_day" in pol and _drafted_today(action_type) >= pol["max_per_day"]:
        return False, f"daily cap of {pol['max_per_day']} {action_type} drafts reached"
    return True, ""


def _alert(msg: str) -> None:
    try:
        from app import events
        events.emit("agent_actions", "platform.alert", severity="warn",
                    payload={"summary": msg}, dedupe=False)
    except Exception:  # noqa: BLE001
        pass

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
    if not _drafts_enabled():
        return {"drafted": 0, "skipped": 0, "action_ids": [], "reason": "agent drafting is disabled (AGENT_DRAFTS_ENABLED=0)"}
    fresh, as_of = _data_fresh()
    if not fresh:
        return {"drafted": 0, "skipped": 0, "action_ids": [], "reason": f"data is stale (as of {as_of}) — refresh before drafting"}

    # reorder_proposal drafts ONE create_po per vendor (not per item), so admins approve a whole PO.
    if name == "reorder_proposal":
        return _draft_create_po(result, as_of, requested_by)

    if name not in _DRAFTERS:
        return {"drafted": 0, "skipped": 0, "action_ids": [], "reason": f"{name} does not draft actions"}

    if result is None:
        result = run_agent(name, triggered_by="user")
    action_type, list_key, item_field, build = _DRAFTERS[name]
    rows = result.get(list_key) or []
    existing = _pending_items(action_type)
    ids, skipped, blocked = [], 0, 0
    for row in rows[:MAX_DRAFTS]:
        item = str(row.get(item_field, "")).strip()
        if not item or item in existing:
            skipped += 1
            continue
        payload = {**build(row), "item": item, "source": f"agent:{name}", "data_as_of": as_of}
        ok, why = policy_check(action_type, payload)
        if not ok:
            _alert(f"blocked {action_type} draft for {item}: {why}")
            blocked += 1
            continue
        try:
            a = submit_action(action_type, payload, requested_by=requested_by)
            ids.append(a.get("id"))
            existing.add(item)
        except Exception as e:  # noqa: BLE001
            log.warning("draft action failed for %s/%s: %s", name, item, e)
            skipped += 1
    return {"drafted": len(ids), "skipped": skipped, "blocked": blocked,
            "action_ids": ids, "action_type": action_type, "data_as_of": as_of}


def _draft_create_po(result: dict | None, as_of: str, requested_by: str) -> dict:
    """One create_po pending action per vendor group from reorder_proposal (human approves a PO)."""
    if result is None:
        result = run_agent("reorder_proposal", triggered_by="user")
    vendors = result.get("by_vendor") or []
    existing = _pending_items("create_po")   # dedupe key = vendor
    ids, skipped, blocked = [], 0, 0
    for g in vendors[:MAX_DRAFTS]:
        vendor = str(g.get("vendor") or "").strip()
        if not vendor or vendor.startswith("(vendor") or vendor in existing:
            skipped += 1
            continue
        est = float(g.get("est_total_bhd") or 0)
        payload = {
            "item": vendor, "vendor": vendor, "est_value_bhd": est,
            "line_count": g.get("lines"),
            "lines": [{"item_name": i.get("item_name"), "qty": i.get("suggested_qty"),
                       "cost_bhd": i.get("cost_bhd"), "est_cost_bhd": i.get("est_cost_bhd")}
                      for i in (g.get("items") or [])],
            "draft_message": g.get("draft_message"),
            "source": "agent:reorder_proposal", "data_as_of": as_of,
        }
        ok, why = policy_check("create_po", payload)
        if not ok:
            _alert(f"blocked create_po for {vendor}: {why}")
            blocked += 1
            continue
        try:
            a = submit_action("create_po", payload, requested_by=requested_by)
            ids.append(a.get("id"))
            existing.add(vendor)
        except Exception as e:  # noqa: BLE001
            log.warning("draft create_po failed for %s: %s", vendor, e)
            skipped += 1
    return {"drafted": len(ids), "skipped": skipped, "blocked": blocked,
            "action_ids": ids, "action_type": "create_po", "data_as_of": as_of}


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
