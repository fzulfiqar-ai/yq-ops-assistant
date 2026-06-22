"""Generate (and optionally push) one n8n workflow per AI agent.

Each flow: Schedule trigger -> HTTP GET {RAILWAY_API_URL}/agents/{name} with the
`X-Agent-Key` header (read from n8n env, no credential object needed) -> Email the
agent's summary.

The header + URL come from n8n environment variables so there is nothing secret in
these files and nothing to wire in the UI:
  - RAILWAY_API_URL  = https://yq-ops-assistant-production.up.railway.app
  - AGENT_API_KEY    = (same strong secret set on the Railway FastAPI service)
  - SMTP_USER, ALERT_EMAIL_TO = email from/to (already used by daily_ops_digest)

Usage:
  python -m scripts.n8n_agents --write          # write JSON files to n8n_workflows/agents/
  python -m scripts.n8n_agents --push           # create them in n8n via the public API
        (needs env N8N_API_URL + N8N_API_KEY; --push implies --write)
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "n8n_workflows" / "agents"

# name -> (Title, schedule). schedule: {"field": "hours"|"weeks", "hour": H, "day": 1..7 (Mon=1, weekly only)}
AGENTS: dict[str, tuple[str, dict]] = {
    "collections":     ("Collections",          {"field": "hours", "hour": 9}),
    "inventory":       ("Inventory & Reorder",   {"field": "hours", "hour": 8}),
    "anomaly":         ("Anomaly Watch",         {"field": "hours", "hour": 7}),
    "cashflow":        ("Cash-flow Forecast",    {"field": "weeks", "hour": 8, "day": 1}),
    "margin":          ("Margin Guardian",       {"field": "weeks", "hour": 8, "day": 1}),
    "sales_insights":  ("Sales Insights",        {"field": "weeks", "hour": 8, "day": 1}),
    "customer_health": ("Customer Health",       {"field": "weeks", "hour": 9, "day": 1}),
    "sales_push":      ("Sales Push",            {"field": "weeks", "hour": 18, "day": 7}),
}


def _schedule_node(sched: dict) -> dict:
    interval: dict = {"field": sched["field"]}
    if sched["field"] == "hours":
        interval["hoursInterval"] = 24
    else:
        interval["weeksInterval"] = 1
        interval["triggerAtDay"] = [sched["day"]]
    return {
        "id": "trigger",
        "name": "Schedule",
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1.2,
        "position": [260, 300],
        "parameters": {"rule": {"interval": [interval]}, "triggerAtHour": sched["hour"], "triggerAtMinute": 0},
    }


def _http_node(name: str) -> dict:
    """Single call: run the agent AND have the backend email the briefing (?email=1).

    The backend owns SMTP (app/emailer.py), so n8n needs no email node and no SMTP
    credential — just the X-Agent-Key header (read from n8n env)."""
    return {
        "id": "http",
        "name": f"Run {name} + email",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [520, 300],
        "parameters": {
            "method": "GET",
            "url": f"={{{{$env.RAILWAY_API_URL}}}}/agents/{name}?email=1",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "X-Agent-Key", "value": "={{$env.AGENT_API_KEY}}"}]},
            "options": {"timeout": 60000},
        },
    }


def build_workflow(name: str) -> dict:
    title, sched = AGENTS[name]
    http_name = f"Run {name} + email"
    return {
        "name": f"YQ Agent — {title}",
        "nodes": [_schedule_node(sched), _http_node(name)],
        "connections": {
            "Schedule": {"main": [[{"node": http_name, "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def write_all() -> list[Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in AGENTS:
        wf = build_workflow(name)
        p = OUT_DIR / f"{name}.json"
        p.write_text(json.dumps(wf, indent=2), encoding="utf-8")
        paths.append(p)
    return paths


def push_all(activate: bool = True) -> None:
    """Clean re-sync: delete any existing 'YQ Agent — …' flows, recreate, activate."""
    import requests
    base = os.getenv("N8N_API_URL", "").rstrip("/")
    key = os.getenv("N8N_API_KEY", "")
    if not base or not key:
        raise SystemExit("Set N8N_API_URL and N8N_API_KEY to push (e.g. https://n8n-production-5fc2.up.railway.app).")
    headers = {"X-N8N-API-KEY": key, "Content-Type": "application/json"}

    # 1 — remove old YQ Agent flows so we don't pile up duplicates
    existing = requests.get(f"{base}/api/v1/workflows?limit=200", headers=headers, timeout=30).json().get("data", [])
    for wf in existing:
        if str(wf.get("name", "")).startswith("YQ Agent — "):
            requests.delete(f"{base}/api/v1/workflows/{wf['id']}", headers=headers, timeout=30)
            print(f"  deleted old: {wf['name']}")

    # 2 — create + activate fresh
    for name in AGENTS:
        wf = build_workflow(name)
        payload = {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"], "settings": wf["settings"]}
        r = requests.post(f"{base}/api/v1/workflows", headers=headers, json=payload, timeout=30)
        if r.status_code not in (200, 201):
            print(f"  {wf['name']:<32} -> FAIL {r.status_code}: {r.text[:160]}")
            continue
        wid = r.json().get("id")
        act = ""
        if activate and wid:
            ar = requests.post(f"{base}/api/v1/workflows/{wid}/activate", headers=headers, timeout=30)
            act = "active" if ar.status_code in (200, 201) else f"created (activate {ar.status_code})"
        print(f"  {wf['name']:<32} -> {act or 'created'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write JSON files to n8n_workflows/agents/")
    ap.add_argument("--push", action="store_true", help="create workflows in n8n via API (implies --write)")
    args = ap.parse_args()
    if not (args.write or args.push):
        ap.error("pass --write and/or --push")
    paths = write_all()
    print(f"Wrote {len(paths)} workflow JSON files to {OUT_DIR.relative_to(ROOT)}/")
    for p in paths:
        print(f"  - {p.name}")
    if args.push:
        print("\nPushing to n8n…")
        push_all()


if __name__ == "__main__":
    main()
