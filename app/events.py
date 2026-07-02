"""Phase B — the event backbone (Postgres-native, no broker).

Agents don't call each other directly; they emit typed events into `agent_events`, and
an hourly n8n call to /events/dispatch fans them out to subscribed agents via a
rules-only SUBSCRIPTIONS map (never an LLM deciding what runs). This is how "Agent A
acts → Agent B reacts" without Kafka/Redis.

Design rules mirrored from the rest of the codebase:
  - emit() is best-effort and NEVER raises (same discipline as the memory hook).
  - run_agent() stays the single choke point: reactions call run_agent(), and the
    events extracted from a run are derived from the diffs memory.py already computes.
  - Cascade guard: reactions run with triggered_by="event"; any events they emit carry
    chain_depth+1; dispatch skips depth>=MAX_CHAIN_DEPTH and caps runs per invocation.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from app.database import get_client

log = logging.getLogger(__name__)

MAX_CHAIN_DEPTH = 2
MAX_RUNS_PER_DISPATCH = 6
_DEDUPE_HOURS = 24


def _fp(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _recent_fingerprints(hours: int = _DEDUPE_HOURS) -> set[str]:
    """Fingerprints emitted in the last `hours` — for 24h dedupe at emit time."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = (get_client().table("agent_events").select("fingerprint")
                .gte("ts", since).not_.is_("fingerprint", "null").execute().data or [])
        return {r["fingerprint"] for r in rows if r.get("fingerprint")}
    except Exception as e:  # noqa: BLE001
        log.warning("recent event fingerprints lookup failed: %s", e)
        return set()


def emit(emitter: str, event_type: str, *, entity_type: str | None = None,
         entity_key: str | None = None, severity: str = "info",
         payload: dict | None = None, fingerprint: str | None = None,
         dedupe: bool = True) -> bool:
    """Insert one event (best-effort; never raises). Returns True if written.

    If `dedupe` and an identical `fingerprint` was emitted within 24h, it's skipped."""
    payload = payload or {}
    payload.setdefault("chain_depth", 0)
    try:
        if dedupe and fingerprint and fingerprint in _recent_fingerprints():
            return False
        get_client().table("agent_events").insert({
            "emitter": emitter, "event_type": event_type,
            "entity_type": entity_type, "entity_key": entity_key,
            "severity": severity, "payload": payload, "fingerprint": fingerprint,
        }).execute()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("event emit failed (%s/%s): %s", emitter, event_type, e)
        return False


# ── Extract events from an agent run's diffs (memory.py new_items / metric_deltas) ──
# agent name -> (event_type, severity, builder(wrapped, changes) -> extra payload or None)

def _stock_low(w, ch):
    new = ch.get("new_items") or []
    return {"items": new[:10], "count": len(new)} if new else None


def _margin_negative(w, ch):
    neg = w.get("negative_margins") or w.get("priced_below_cost") or []
    return {"count": len(neg), "items": [str(r.get("item_name")) for r in neg[:10]]} if neg else None


def _ar_risk(w, ch):
    d = (ch.get("metric_deltas") or {}).get("total_overdue_bhd", 0)
    return {"overdue_delta_bhd": d} if d and d > 0 else None


def _catalog_changed(w, ch):
    new = ch.get("new_items") or []
    cost_changes = w.get("cost_changes") or w.get("cost_change_count") or 0
    n_cost = cost_changes if isinstance(cost_changes, int) else len(cost_changes)
    if new or n_cost:
        return {"new_items": new[:10], "cost_change_count": n_cost}
    return None


def _trend_rising(w, ch):
    new = ch.get("new_items") or []
    return {"rising": new[:10]} if new else None


def _returns_spike(w, ch):
    flagged = w.get("high_return_items") or w.get("items") or []
    return {"count": len(flagged)} if flagged else None


# event_type, severity, builder
EVENT_EXTRACTORS: dict[str, tuple[str, str, callable]] = {
    "demand_forecast": ("stock.low", "warn", _stock_low),
    "inventory": ("stock.low", "warn", _stock_low),
    "margin": ("margin.negative", "critical", _margin_negative),
    "risk_watch": ("margin.negative", "critical", _margin_negative),
    "collections": ("ar.risk", "warn", _ar_risk),
    "credit_exposure": ("ar.risk", "warn", _ar_risk),
    "catalog_watch": ("catalog.changed", "info", _catalog_changed),
    "trend_radar": ("trend.rising", "info", _trend_rising),
    "trend": ("trend.rising", "info", _trend_rising),
    "returns_investigator": ("returns.spike", "warn", _returns_spike),
}


def emit_from_run(name: str, wrapped: dict) -> None:
    """Called inside run_agent() after the memory diff. Emits a typed event if this
    agent's diff carries something worth reacting to. Best-effort; never raises."""
    spec = EVENT_EXTRACTORS.get(name)
    if not spec:
        return
    event_type, severity, builder = spec
    changes = wrapped.get("changes") or {}
    if changes.get("first_run"):
        return  # no baseline yet — suppress noise
    try:
        extra = builder(wrapped, changes)
    except Exception as e:  # noqa: BLE001
        log.warning("event extractor failed for %s: %s", name, e)
        return
    if not extra:
        return
    depth = int((wrapped.get("_event_chain_depth") or 0))
    payload = {**extra, "summary": wrapped.get("summary", ""), "chain_depth": depth}
    emit(f"agent:{name}", event_type, severity=severity, payload=payload,
         fingerprint=_fp(event_type, name, sorted(extra.get("items", []) or extra.get("new_items", []))
                         or round(float(extra.get("overdue_delta_bhd", 0)) / 100)))


# ── Subscriptions: event_type -> reaction(event) ─────────────────────────────
# Reactions are plain Python (rules), NOT an LLM. Each returns a short dict for the log.

def _react_ingest_ok(ev, budget):
    from app.agents import run_agent
    ran = []
    payload = ev.get("payload") or {}
    if budget[0] > 0:
        _run_chained("demand_forecast", ev); ran.append("demand_forecast"); budget[0] -= 1
    if (payload.get("cost_change_count") or 0) > 0 and budget[0] > 0:
        _run_chained("margin", ev); ran.append("margin"); budget[0] -= 1
    return {"ran": ran}


def _react_stock_low(ev, budget):
    """Draft one PO per vendor from the reorder proposal — into pending_actions (human approves)."""
    if budget[0] <= 0:
        return {"skipped": "run budget exhausted"}
    budget[0] -= 1
    try:
        from app.agent_actions import draft_for_agent
        res = draft_for_agent("reorder_proposal")
        return {"drafted": res.get("drafted"), "skipped": res.get("skipped")}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:80]}


def _react_margin_negative(ev, budget):
    try:
        from app.agent_actions import draft_for_agent
        res = draft_for_agent("margin")
        return {"drafted": res.get("drafted"), "skipped": res.get("skipped")}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:80]}


def _react_returns_spike(ev, budget):
    _notify(f"⚠ Returns spike flagged: {(ev.get('payload') or {}).get('summary', '')}")
    return {"notified": True}


def _react_catalog_changed(ev, budget):
    """If landed costs moved, run Price Sentry to catch cost/price drift (margin erosion)."""
    if (ev.get("payload") or {}).get("cost_change_count", 0) <= 0 or budget[0] <= 0:
        return {}
    budget[0] -= 1
    r = _run_chained("price_drift", ev)
    return {"ran": "price_drift", "drifting": r.get("count")}


def _react_notify_warn(ev, budget):
    sev = ev.get("severity")
    if sev in ("warn", "critical"):
        _notify(f"[{sev.upper()}] {ev.get('event_type')}: {(ev.get('payload') or {}).get('summary', '')}")
        return {"notified": True}
    return {}


SUBSCRIPTIONS: dict[str, list] = {
    "ingest.completed": [_react_ingest_ok, _react_notify_warn],
    "stock.low": [_react_stock_low, _react_notify_warn],
    "margin.negative": [_react_margin_negative, _react_notify_warn],
    "ar.risk": [_react_notify_warn],
    "returns.spike": [_react_returns_spike],
    "catalog.changed": [_react_catalog_changed],   # cost moves → Price Sentry
    "trend.rising": [],      # feed-only
    "procurement.stage": [], # feed-only (procurement_status owns SLA nudges)
    "action.decided": [_react_notify_warn],
    "platform.alert": [_react_notify_warn],
}


def _notify(text: str) -> None:
    try:
        from app.notify import send_telegram
        send_telegram(text)
    except Exception:  # noqa: BLE001
        pass


def _run_chained(name: str, parent_ev: dict) -> dict:
    """Run an agent as a reaction, carrying chain_depth+1 so its emitted events can't loop."""
    from app.agents import run_agent
    depth = int((parent_ev.get("payload") or {}).get("chain_depth", 0)) + 1
    return run_agent(name, triggered_by="event", _event_chain_depth=depth)


def dispatch(limit: int = 50) -> dict:
    """Process unprocessed events: apply SUBSCRIPTIONS, stamp processed_at + consumed_by.
    Called hourly by n8n (GET /events/dispatch). Rules-only; capped and depth-guarded."""
    c = get_client()
    try:
        rows = (c.table("agent_events").select("*")
                .is_("processed_at", "null").order("ts").limit(limit).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("dispatch fetch failed: %s", e)
        return {"processed": 0, "reactions": 0, "error": str(e)[:80]}

    budget = [MAX_RUNS_PER_DISPATCH]   # shared agent-run budget across this dispatch
    processed = reactions = 0
    now = datetime.now(timezone.utc).isoformat()
    for ev in rows:
        depth = int((ev.get("payload") or {}).get("chain_depth", 0))
        consumed = []
        if depth < MAX_CHAIN_DEPTH:
            for react in SUBSCRIPTIONS.get(ev.get("event_type"), []):
                try:
                    out = react(ev, budget)
                    if out:
                        consumed.append({"reaction": react.__name__, "result": out})
                        reactions += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("reaction %s failed: %s", getattr(react, "__name__", "?"), e)
        else:
            consumed.append({"reaction": "skipped", "result": {"reason": "max chain depth"}})
        try:
            c.table("agent_events").update(
                {"processed_at": now, "consumed_by": consumed}).eq("id", ev["id"]).execute()
            processed += 1
        except Exception as e:  # noqa: BLE001
            log.warning("dispatch mark-processed failed for event %s: %s", ev.get("id"), e)
    return {"processed": processed, "reactions": reactions}


def feed(limit: int = 50, event_type: str | None = None, severity: str | None = None) -> list[dict]:
    """Merged, most-recent-first timeline of events for the Feed page (Phase E)."""
    try:
        q = get_client().table("agent_events").select(
            "id,ts,emitter,event_type,entity_type,entity_key,severity,payload,consumed_by,processed_at")
        if event_type:
            q = q.eq("event_type", event_type)
        if severity:
            q = q.eq("severity", severity)
        return q.order("ts", desc=True).limit(min(limit, 200)).execute().data or []
    except Exception as e:  # noqa: BLE001
        log.warning("feed fetch failed: %s", e)
        return []
