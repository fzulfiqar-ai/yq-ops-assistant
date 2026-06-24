"""Agent memory — "what changed since last time", computed in pure Python.

Each run snapshots its headline metrics + stable item keys into `agent_runs`; the next
run diffs against the last **scheduled** baseline (so ad-hoc "Run" clicks don't reset the
daily comparison), falling back to the last run of any kind before any schedule exists.
"""
from __future__ import annotations

import logging

from app.database import get_client

log = logging.getLogger(__name__)

_META = {"agent", "description", "generated_at", "summary", "email", "changes"}
_KEY_FIELDS = ("account", "item_name", "customer_name", "salesman")


def _generic_extract(result: dict) -> dict:
    """Headline numbers (flat) + stable item keys from the first list-of-rows."""
    metrics = {k: v for k, v in result.items()
               if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in _META}
    keys: list[str] = []
    for k, v in result.items():
        if k in _META:
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict):
            for row in v[:60]:
                for kf in _KEY_FIELDS:
                    if row.get(kf):
                        keys.append(str(row[kf]).strip())
                        break
            break
    return {"metrics": metrics, "item_keys": sorted(set(keys))}


def snapshot(spec, result: dict) -> dict:
    """Extract {metrics, item_keys} using the agent's extractor (or the generic one)."""
    extractor = getattr(spec, "extractor", None) or _generic_extract
    try:
        snap = extractor(result)
    except Exception as e:  # noqa: BLE001
        log.warning("extractor failed for %s: %s", getattr(spec, "name", "?"), e)
        snap = _generic_extract(result)
    snap.setdefault("metrics", {})
    snap.setdefault("item_keys", [])
    return snap


def last_snapshot(name: str) -> dict | None:
    """The diff baseline: the last 'schedule' run if one exists, else the last run of any kind."""
    c = get_client()
    for sched_only in (True, False):
        q = c.table("agent_runs").select("metrics,item_keys,ran_at,summary,triggered_by").eq("agent", name)
        if sched_only:
            q = q.eq("triggered_by", "schedule")
        rows = q.order("ran_at", desc=True).limit(1).execute().data
        if rows:
            return rows[0]
    return None


def diff(prev: dict | None, curr: dict) -> dict:
    """Numeric deltas + new/resolved item sets. first_run suppresses noise on the very first."""
    if not prev:
        return {"first_run": True}
    pm, cm = (prev.get("metrics") or {}), (curr.get("metrics") or {})
    deltas: dict[str, float] = {}
    for k, v in cm.items():
        try:
            d = float(v or 0) - float(pm.get(k, 0) or 0)
            if abs(d) > 1e-9:
                deltas[k] = round(d, 2)
        except (TypeError, ValueError):
            continue
    pk, ck = set(prev.get("item_keys") or []), set(curr.get("item_keys") or [])
    out: dict = {"metric_deltas": deltas}
    new, resolved = sorted(ck - pk), sorted(pk - ck)
    if new:
        out["new_items"] = new[:10]
    if resolved:
        out["resolved_items"] = resolved[:10]
    return out


def record(name: str, summary: str | None, snap: dict, triggered_by: str) -> None:
    """Persist this run's snapshot (best-effort)."""
    try:
        get_client().table("agent_runs").insert({
            "agent": name,
            "summary": summary,
            "metrics": snap.get("metrics") or {},
            "item_keys": snap.get("item_keys") or [],
            "triggered_by": triggered_by,
        }).execute()
    except Exception as e:  # noqa: BLE001
        log.warning("agent_runs record failed for %s: %s", name, e)
