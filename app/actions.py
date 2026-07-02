"""Phase 2 — Pending actions CRUD.

Action types:
  reorder_stock   → request stock replenishment
  price_change    → request price update
  write_off       → request stock write-off
  credit_note     → request customer credit note

Flow: manager submits → admin approves/rejects → export CSV for Focus import
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from app.database import get_client

ACTION_TYPES = frozenset({"reorder_stock", "price_change", "write_off", "credit_note",
                          "create_po", "kb_article"})

# Apply-hooks: action_type -> callable(action_row) run WHEN an admin approves. Most actions
# are export-only (Focus import), so they have no hook. kb_article (Phase F) is the first
# approval with a real side effect — it publishes the drafted article into kb_chunks.
_APPLY_HOOKS: dict[str, "callable"] = {}


def register_apply_hook(action_type: str, fn) -> None:
    _APPLY_HOOKS[action_type] = fn


def _apply_kb_article(row: dict) -> None:
    """On approval, publish a drafted KB article into kb_chunks so the assistant can recall it.
    This is the ONE approval with a real side effect — LLM output never auto-publishes; a human
    approved it here."""
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:  # noqa: BLE001
            payload = {}
    payload = payload or {}
    title = str(payload.get("title") or "Knowledge base article")
    body = str(payload.get("markdown") or "")
    if not body.strip():
        return
    from app.knowledge import remember
    remember(f"{title}\n\n{body}", kind="knowledge",
             meta={"source": "ops_sentinel", "approved": True})


register_apply_hook("kb_article", _apply_kb_article)


def submit_action(action_type: str, payload: dict, requested_by: str) -> dict:
    if action_type not in ACTION_TYPES:
        raise ValueError(f"Unknown action type: {action_type}. Must be one of {sorted(ACTION_TYPES)}")
    client = get_client()
    row = {
        "action_type": action_type,
        "payload": json.dumps(payload),
        "status": "pending",
        "requested_by": requested_by,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    r = client.table("pending_actions").insert(row).execute()
    return r.data[0] if r.data else row


def list_actions(status: str | None = None) -> list[dict]:
    client = get_client()
    q = client.table("pending_actions").select("*").order("requested_at", desc=True)
    if status:
        q = q.eq("status", status)
    r = q.limit(100).execute()
    return r.data or []


def approve_action(action_id: int, approved_by: str) -> dict:
    client = get_client()
    r = client.table("pending_actions").update({
        "status": "approved",
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", action_id).execute()
    row = r.data[0] if r.data else {}
    # Run the action-type apply-hook (e.g. kb_article publishes to kb_chunks). Best-effort.
    hook = _APPLY_HOOKS.get(row.get("action_type"))
    if hook:
        try:
            hook(row)
        except Exception:  # noqa: BLE001
            pass
    _emit_decided(row, "approved", approved_by)
    return row


def reject_action(action_id: int, approved_by: str, reason: str = "") -> dict:
    client = get_client()
    r = client.table("pending_actions").update({
        "status": "rejected",
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "result": json.dumps({"reason": reason}),
    }).eq("id", action_id).execute()
    row = r.data[0] if r.data else {}
    _emit_decided(row, "rejected", approved_by)
    return row


def _emit_decided(row: dict, decision: str, by: str) -> None:
    try:
        from app import events
        events.emit("actions", "action.decided",
                    entity_type="action", entity_key=str(row.get("id") or ""),
                    severity="info",
                    payload={"decision": decision, "action_type": row.get("action_type"),
                             "by": by, "summary": f"{row.get('action_type')} {decision} by {by}"},
                    dedupe=False)
    except Exception:  # noqa: BLE001
        pass


def export_approved_csv() -> str:
    """Export all approved actions as CSV for Focus ERP import."""
    actions = list_actions(status="approved")
    if not actions:
        return ""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "action_type", "requested_by", "approved_by", "approved_at", "payload"])
    for a in actions:
        payload = a.get("payload", "{}")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                pass
        writer.writerow([
            a.get("id", ""),
            a.get("action_type", ""),
            a.get("requested_by", ""),
            a.get("approved_by", ""),
            a.get("approved_at", ""),
            json.dumps(payload) if isinstance(payload, dict) else payload,
        ])
    return buf.getvalue()
