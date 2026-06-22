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

ACTION_TYPES = frozenset({"reorder_stock", "price_change", "write_off", "credit_note"})


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
    return r.data[0] if r.data else {}


def reject_action(action_id: int, approved_by: str, reason: str = "") -> dict:
    client = get_client()
    r = client.table("pending_actions").update({
        "status": "rejected",
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "result": json.dumps({"reason": reason}),
    }).eq("id", action_id).execute()
    return r.data[0] if r.data else {}


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
