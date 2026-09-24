"""Admin audit (trust plan M12, release R3): every admin write to shop settings, discount rules,
campaigns, salesmen, target imports, upcoming items and merchant attribution leaves one
append-only row in `shop_admin_audit` — who, when, which entity, what it looked like before
and after. Nothing here ever blocks the write it describes: a missing table (the migration
lands after the API) or a failed insert is logged and swallowed, the way app.audit.log_event
behaves. The table itself refuses UPDATE/DELETE (trigger in scripts/r3_pipeline_migration.sql),
so a row can be added but never rewritten.

Call sites (app/shop_api.py) do three things around the existing function:

    before = shop_audit.snapshot("discount_rules", rule_id)
    row = shop.upsert_rule(...)
    shop_audit.record(user.email, "discount_rule", rule_id, "update", before, row)

`before`/`after` are stored verbatim (jsonb) minus the keys in STRIP; `changes` is derived on
read so the Audit view shows only what moved.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app import database

log = logging.getLogger(__name__)

TABLE = "shop_admin_audit"
ENTITIES = ("settings", "discount_rule", "campaign", "salesman", "target", "upcoming", "shop_customer", "order")
ACTIONS = ("create", "update", "delete", "import", "assign")
# never worth a second copy (volatile, or already elsewhere): timestamps the row itself moves,
# derived fields the API decorates rows with, and browser fingerprints
STRIP = frozenset({"updated_at", "link", "orders_30d", "references", "impact", "summary", "status_label",
                   "ua", "ip_hash", "device_ids", "interest_phones", "interest_ids", "by"})
MAX_JSON = 16_000          # per side; a campaign row with a long image URL is ~1 KB
MISSING_HINT = "Admin audit not available yet — apply scripts/r3_pipeline_migration.sql."


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(v):
    """jsonb-safe copy: dicts/lists recursively, everything else through str() when json can't."""
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        return json.loads(json.dumps(v, default=str))


def trim(row) -> dict | None:
    """A row as it is stored on the audit: STRIP keys removed, jsonb-safe, capped in size."""
    if row is None:
        return None
    if not isinstance(row, dict):
        return {"value": _jsonable(row)}
    out = {k: _jsonable(v) for k, v in row.items() if k not in STRIP}
    if len(json.dumps(out, default=str)) > MAX_JSON:
        # keep the small scalar fields, drop the long ones — the row is still identifiable
        out = {k: v for k, v in out.items() if not isinstance(v, (str, list, dict)) or len(json.dumps(v, default=str)) <= 400}
        out["_truncated"] = True
    return out


def diff(before: dict | None, after: dict | None) -> dict:
    """{key: {"from": x, "to": y}} for every key whose value moved (None on a missing side)."""
    b, a = before or {}, after or {}
    out: dict = {}
    for k in sorted(set(b) | set(a)):
        if k.startswith("_"):
            continue
        if b.get(k) != a.get(k):
            out[k] = {"from": b.get(k), "to": a.get(k)}
    return out


def snapshot(table: str, row_id) -> dict | None:
    """The row as it is now (before the write), or None when it does not exist / cannot be read.
    Never raises — a failed snapshot must not stop an admin edit."""
    if row_id in (None, ""):
        return None
    try:
        got = database.get_client().table(table).select("*").eq("id", row_id).limit(1).execute().data
        return got[0] if got else None
    except Exception as e:  # noqa: BLE001
        log.debug("audit snapshot %s/%s failed: %s", table, row_id, e)
        return None


def record(actor: str, entity: str, entity_id, action: str, before=None, after=None) -> bool:
    """Append one audit row. Returns False (and logs) instead of raising — see the module doc."""
    if entity not in ENTITIES:
        log.warning("audit: unknown entity %r (recorded anyway)", entity)
    row = {
        "at": _iso(), "actor": str(actor or "")[:160] or "unknown", "entity": str(entity)[:40],
        "entity_id": (str(entity_id)[:80] if entity_id not in (None, "") else None),
        "action": str(action)[:20], "before": trim(before), "after": trim(after),
    }
    try:
        database.get_client().table(TABLE).insert(row).execute()
        return True
    except Exception as e:  # noqa: BLE001 — the write it describes has already happened
        log.warning("audit row not written (%s %s %s): %s", entity, action, entity_id, e)
        return False


def settings_change(actor: str, current: dict, changes: dict, saved: dict) -> bool:
    """One row per PUT /settings/shop: only the keys the request named, old → new."""
    keys = [k for k in (changes or {}) if k in (saved or {})]
    if not keys:
        return False
    before = {k: (current or {}).get(k) for k in keys}
    after = {k: saved.get(k) for k in keys}
    if before == after:
        return False
    return record(actor, "settings", "shop", "update", before, after)


def record_target_import(client, rows: list[dict], actor: str) -> int:
    """scripts/import_targets.py: before the upsert, one audit row per (salesman, period) with
    the target row as it was (None for a new one) and as it will be. Returns rows written."""
    n = 0
    for r in rows or []:
        before = None
        try:
            got = (client.table("salesman_targets").select("*").eq("salesman", r.get("salesman"))
                   .eq("period", r.get("period", "")).limit(1).execute().data)
            before = got[0] if got else None
        except Exception as e:  # noqa: BLE001
            log.debug("target snapshot failed: %s", e)
        key = f"{r.get('salesman')}|{r.get('period') or 'standing'}"
        if record(actor, "target", key, "import", before, r):
            n += 1
    return n


def list_audit(entity: str | None = None, limit: int = 100, offset: int = 0) -> dict:
    """Newest first, with `changes` derived per row. Before the migration: an empty list plus a
    hint, never a 500 (the Settings page loads this on every visit by an admin)."""
    lim = max(1, min(int(limit or 100), 500))
    off = max(0, int(offset or 0))
    try:
        q = database.get_client().table(TABLE).select("*", count="exact")
        if entity:
            q = q.eq("entity", entity)
        res = q.order("id", desc=True).range(off, off + lim - 1).execute()
    except Exception as e:  # noqa: BLE001 — the table arrives with the migration
        log.info("audit list unavailable: %s", e)
        return {"rows": [], "count": 0, "hint": MISSING_HINT}
    rows = res.data or []
    for r in rows:
        r["changes"] = diff(r.get("before"), r.get("after"))
    return {"rows": rows, "count": res.count if res.count is not None else len(rows), "entities": list(ENTITIES)}
