"""Per-agent scheduling (NEXT bucket).

The admin picks a cadence per agent in the portal (Off / Daily / Weekly); a single hourly call
to GET /scheduler/run-due (from n8n) runs + emails the agents that are due. All scheduled runs are
at 08:00 Asia/Bahrain; weekly = Monday. A per-day `last_ran` guard makes it idempotent, so calling
run-due more than once in the 8 o'clock hour never double-sends.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.database import get_client

log = logging.getLogger(__name__)

CADENCES = ("off", "daily", "weekly")
RUN_HOUR = 8                       # 08:00
_BAHRAIN = timezone(timedelta(hours=3))


def get_schedules() -> dict[str, str]:
    """{agent: cadence} for every agent that has a non-default schedule."""
    try:
        rows = get_client().table("agent_schedules").select("agent,cadence").execute().data or []
        return {r["agent"]: r["cadence"] for r in rows}
    except Exception as e:  # noqa: BLE001
        log.warning("get_schedules failed: %s", e)
        return {}


def set_schedule(agent: str, cadence: str, by: str = "") -> dict:
    cadence = cadence if cadence in CADENCES else "off"
    get_client().table("agent_schedules").upsert(
        {"agent": agent, "cadence": cadence, "updated_by": by,
         "updated_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="agent").execute()
    return {"agent": agent, "cadence": cadence}


def run_due(send: bool = True) -> dict:
    """Run + email the agents due right now. n8n calls this hourly; it acts only in the 08:00
    Bahrain hour and never runs an agent twice in the same day."""
    from app.agents import AGENTS, run_agent
    from app.emailer import send_agent

    now = datetime.now(timezone.utc).astimezone(_BAHRAIN)
    if now.hour != RUN_HOUR:
        return {"ran": [], "count": 0, "skipped": f"not the run hour (Bahrain {now.hour:02d}:00)"}
    today = now.date().isoformat()
    client = get_client()
    rows = client.table("agent_schedules").select("agent,cadence,last_ran").execute().data or []

    ran = []
    for r in rows:
        cad = r.get("cadence")
        if cad == "off" or cad not in CADENCES:
            continue
        if cad == "weekly" and now.weekday() != 0:   # Monday only
            continue
        if r.get("last_ran") == today:               # already ran today
            continue
        name = r["agent"]
        if name not in AGENTS:
            continue
        try:
            res = run_agent(name, triggered_by="schedule")
            emailed = bool(send_agent(res).get("emailed")) if send else False
            client.table("agent_schedules").update({"last_ran": today}).eq("agent", name).execute()
            ran.append({"agent": name, "emailed": emailed})
        except Exception as e:  # noqa: BLE001
            log.warning("scheduled run failed for %s: %s", name, e)
    return {"ran": ran, "count": len(ran)}
