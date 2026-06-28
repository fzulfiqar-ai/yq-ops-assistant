"""Phase 3 — procurement workflow: track an order from the AI proposal to goods received.

The lifecycle mirrors YQ's real flow: propose (from sales) -> review -> raise with the vendor ->
pay advance -> vendor invoices -> pay balance -> raise the PO in Focus -> receive (MRN). Each order
is a `procurement_orders` row; every move is logged to `procurement_events` (timeline + audit). The
board view flags orders 'stuck' past a per-stage SLA so the status agent can nudge them.

Reads use the read-only RPC (exec_sql); writes use the service client (get_client). Advisory by
design — moving a stage is a human click; nothing here touches Focus or posts to a vendor.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.ai import exec_sql
from app.database import get_client

# Ordered lifecycle (simplified to 100% payment). Each stage: label + the SLA (days) after which an
# order is 'stuck' here. We pay the vendor in full, so the old advance/balance split is one 'Paid'.
STAGES: list[dict] = [
    {"key": "proposed", "label": "Proposed", "sla": 3},
    {"key": "reviewed", "label": "Reviewed", "sla": 3},
    {"key": "raised", "label": "Raised & confirmed", "sla": 5},
    {"key": "paid", "label": "Paid (100%)", "sla": 7},
    {"key": "received", "label": "Received (MRN)", "sla": 21},
    {"key": "closed", "label": "Closed", "sla": None},
]
# Legacy stage keys → the simplified stage they now resolve to (so old rows / saved schedules / n8n
# flows keep working). Applied on every read and before every write.
STAGE_ALIASES: dict[str, str] = {
    "advance_paid": "paid",
    "invoiced": "raised",
    "po_raised": "paid",
}
_ORDER = [s["key"] for s in STAGES]
_LABEL = {s["key"]: s["label"] for s in STAGES}
_LABEL["cancelled"] = "Cancelled"
VALID_STAGES = set(_ORDER) | set(STAGE_ALIASES) | {"cancelled"}


def normalize_stage(stage: str | None) -> str | None:
    """Resolve a (possibly legacy) stage key to its current canonical key."""
    return STAGE_ALIASES.get(stage or "", stage)


def stages() -> list[dict]:
    """The stage model, for the UI to render the pipeline columns."""
    return STAGES


def next_stage(current: str) -> str | None:
    """The stage that naturally follows `current` (None at the end / for terminal stages)."""
    current = normalize_stage(current)
    if current in _ORDER:
        i = _ORDER.index(current)
        return _ORDER[i + 1] if i + 1 < len(_ORDER) else None
    return None


def _client():
    return get_client()


def create_order(title: str, vendor: str | None = None, est_value_bhd: float | None = None,
                 lines: list[dict] | None = None, note: str | None = None,
                 actor: str = "user", stage: str | None = None) -> dict:
    """Open a new procurement order. Defaults to 'proposed'; a verified order can open straight at
    'reviewed' (pass stage). Returns the created row."""
    stage = normalize_stage(stage) if stage else "proposed"
    if stage not in VALID_STAGES:
        stage = "proposed"
    c = _client()
    row = {
        "title": (title or "Untitled order").strip()[:200],
        "vendor": (vendor or None),
        "stage": stage,
        "est_value_bhd": est_value_bhd,
        "lines": lines or [],
        "note": note,
    }
    res = c.table("procurement_orders").insert(row).execute()
    created = (res.data or [{}])[0]
    oid = created.get("id")
    ref = f"PRO-{datetime.now(timezone.utc):%Y}-{int(oid):04d}" if oid else None
    if oid and ref:
        c.table("procurement_orders").update({"ref": ref}).eq("id", oid).execute()
        created["ref"] = ref
    _log(oid, stage, note or "Order created", actor)
    return created


def _log(order_id: Any, stage: str, note: str | None, actor: str) -> None:
    if not order_id:
        return
    try:
        _client().table("procurement_events").insert(
            {"order_id": order_id, "stage": stage, "note": note, "actor": actor}).execute()
    except Exception:  # noqa: BLE001 — the event log is best-effort, never block the move
        pass


def advance(order_id: int, to_stage: str, note: str | None = None,
            po_no: str | None = None, actor: str = "user") -> dict:
    """Move an order to `to_stage` (any valid stage — usually the next one, but a jump/back is
    allowed). Stamps stage_changed_at, logs the event, and links a Focus PO when one is given."""
    if to_stage not in VALID_STAGES:
        raise ValueError(f"Unknown stage '{to_stage}'.")
    to_stage = normalize_stage(to_stage)
    now = datetime.now(timezone.utc).isoformat()
    patch: dict = {"stage": to_stage, "stage_changed_at": now, "updated_at": now}
    if po_no:
        patch["po_no"] = po_no.strip()
    if note:
        patch["note"] = note
    res = _client().table("procurement_orders").update(patch).eq("id", order_id).execute()
    _log(order_id, to_stage, note, actor)
    return (res.data or [{}])[0]


def _board_rows() -> list[dict]:
    try:
        rows = exec_sql(
            "SELECT id, ref, title, vendor, stage, est_value_bhd, po_no, note, "
            "created_at, updated_at, stage_changed_at, days_in_stage, sla_days, is_stuck "
            "FROM v_procurement_board ORDER BY is_stuck DESC, stage_changed_at ASC"
        ) or []
    except Exception:  # noqa: BLE001
        return []
    for r in rows:  # resolve any legacy stage key so the UI columns line up
        r["stage"] = normalize_stage(r.get("stage"))
    return rows


def board() -> dict:
    """The full pipeline: every open order with its stage, days-in-stage, and stuck flag, plus the
    stage model so the UI can lay out columns. Terminal stages (received/cancelled) are included."""
    rows = _board_rows()
    open_rows = [r for r in rows if r.get("stage") not in ("received", "closed", "cancelled")]
    stuck = [r for r in rows if r.get("is_stuck")]
    pipeline_value = sum(float(r.get("est_value_bhd") or 0) for r in open_rows)
    return {
        "stages": [{"key": s["key"], "label": s["label"]} for s in STAGES] + [{"key": "cancelled", "label": "Cancelled"}],
        "orders": rows,
        "open_count": len(open_rows),
        "stuck_count": len(stuck),
        "pipeline_value_bhd": round(pipeline_value, 3),
        "summary": (f"{len(open_rows)} open order(s) worth ~BHD {pipeline_value:,.0f} in the pipeline; "
                    f"{len(stuck)} need attention." if open_rows else "No open procurement orders."),
    }


def get_order(order_id: int) -> dict:
    """One order + its full event timeline (for the detail/timeline view)."""
    rows = _board_rows()
    order = next((r for r in rows if int(r.get("id", 0)) == int(order_id)), None)
    try:
        events = exec_sql(
            f"SELECT stage, note, actor, created_at FROM procurement_events "
            f"WHERE order_id = {int(order_id)} ORDER BY created_at ASC") or []
    except Exception:  # noqa: BLE001
        events = []
    return {"order": order, "events": events}


def stuck_orders() -> list[dict]:
    """Open orders sitting past their stage SLA — the nudge list for the status agent."""
    return [r for r in _board_rows() if r.get("is_stuck")]
