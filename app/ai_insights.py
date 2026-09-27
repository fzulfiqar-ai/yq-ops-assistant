"""What the weekly AI Head flagged (release R7b, plan §19): the read side of ai_insights.

    GET /management/insights?week_ending=YYYY-MM-DD&kind=weekly|monthly     admin + management

The owner runs the weekly review in Claude Code on his own PC (.claude/commands/weekly-review.md):
scripts/ai_head/pack.py builds a read-only pack, Claude writes weekly.html + insights.json, the
owner approves the Top 5 actions, and only then scripts/ai_head/load_insights.py --commit loads the
statements into ai_insights. This module only READS that table (service role, PostgREST).

Who sees what:
  * admin      every status (proposed / approved / rejected / done), ?status= filters, decided_by;
  * management approved and done statements only (what the owner signed off), no decided_by.
  * anyone else 403. The route is a GET, so the central read-only gate (app.auth) lets
    management through; nothing here writes.

Before scripts/r7b_ai_head_migration.sql runs the table does not exist: the route answers
{"available": false, "hint": ...} with empty lists instead of failing, so the API may deploy first.

mask_text() is the one phone / email mask shared with the pack and the loader: every email address
becomes '[email]' and every run of 8+ digits (spaces allowed between them) '[number]'. Order numbers
(YQ-2609-0019) and Focus invoice keys (SI-YQ-26-09-110) survive it. The SQL views' ai_agent_mask()
applies the same two patterns in the same order.
"""
# NOTE: no `from __future__ import annotations` — the route is defined inside register() and
# FastAPI must see real annotation objects to bind its query parameters.
import logging
import re
from datetime import date

log = logging.getLogger(__name__)

# the same pattern as the regexp_replace in scripts/r7b_ai_head_migration.sql (PG ARE and Python re agree)
PHONE_PATTERN = r"\+?\d(?: ?\d){7,}"
PHONE_RE = re.compile(PHONE_PATTERN)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
NUMBER_MASK = "[number]"
EMAIL_MASK = "[email]"

INSIGHT_ROLES: frozenset[str] = frozenset({"admin", "management"})
MANAGEMENT_STATUSES: tuple[str, ...] = ("approved", "done")
STATUSES: tuple[str, ...] = ("proposed", "approved", "rejected", "done")
KINDS: tuple[str, ...] = ("weekly", "monthly")
TAG_LABELS: dict[str, str] = {
    "fact": "FACT",
    "analysis": "ANALYSIS",
    "recommendation": "RECOMMENDATION",
    "hypothesis": "LOW-CONFIDENCE HYPOTHESIS",
}
# The §19 report format, identical every week; the UI groups statements in this order.
SECTIONS: tuple[str, ...] = (
    "Executive summary", "Top actions", "Sales", "Order health", "Merchant movement", "Product movement",
    "Team", "Offers", "Profitability", "Lost demand", "Market intelligence", "Risks and anomalies", "Data quality",
)
MAX_LIMIT = 200
MISSING_HINT = ("The AI Head insights table is not set up yet (scripts/r7b_ai_head_migration.sql). "
                "Nothing has been loaded.")
COLUMNS = ("id,kind,week_ending,section,tag,text,evidence,confidence,action,rank,status,data_as_of,"
           "decided_by,decided_at,created_at")


def mask_text(value):
    """Emails -> '[email]', then runs of 8+ digits (spaces allowed) -> '[number]': the same result as
    the SQL ai_agent_mask() the views use. Anything that is not a string comes back unchanged."""
    if not isinstance(value, str) or not value:
        return value
    return PHONE_RE.sub(NUMBER_MASK, EMAIL_RE.sub(EMAIL_MASK, value))


def has_contact(value) -> bool:
    """True when a string still carries something that looks like a phone number or an email."""
    return isinstance(value, str) and bool(PHONE_RE.search(value) or EMAIL_RE.search(value))


def _missing_table(e: Exception) -> bool:
    """PostgREST's answer for a table the migration has not created yet (app/shop_pipeline.py's rule)."""
    code = str(getattr(e, "code", "") or "")
    msg = str(e)
    return code in ("42P01", "PGRST205") or "42P01" in msg or "PGRST205" in msg or \
        ("relation" in msg and "does not exist" in msg) or "Could not find the table" in msg


def _section_pos(section: str | None) -> int:
    try:
        return SECTIONS.index(section or "")
    except ValueError:
        return len(SECTIONS)


def _out(row: dict, admin: bool) -> dict:
    item = {
        "id": row.get("id"),
        "kind": row.get("kind"),
        "week_ending": row.get("week_ending"),
        "section": row.get("section"),
        "tag": row.get("tag"),
        "tag_label": TAG_LABELS.get(str(row.get("tag") or ""), str(row.get("tag") or "").upper()),
        "text": mask_text(row.get("text")),
        "evidence": row.get("evidence"),
        "confidence": row.get("confidence"),
        "action": mask_text(row.get("action")),
        "rank": row.get("rank"),
        "status": row.get("status"),
        "data_as_of": row.get("data_as_of"),
        "decided_at": row.get("decided_at"),
    }
    if admin:
        item["decided_by"] = row.get("decided_by")
    return item


def list_insights(client, *, kind: str = "weekly", week_ending: str | None = None,
                  statuses: tuple[str, ...] = MANAGEMENT_STATUSES, limit: int = 100, admin: bool = False) -> dict:
    """The statements of one review (the latest by default) in the §19 order, plus the recent
    review dates for a picker. {"available": False, ...} before the migration."""
    limit = max(1, min(int(limit or 100), MAX_LIMIT))
    base = {"available": True, "kind": kind, "sections": list(SECTIONS), "tag_labels": TAG_LABELS,
            "statuses": list(statuses), "weeks": [], "week_ending": None, "insights": [], "top_actions": []}
    try:
        rows = (client.table("ai_insights").select("week_ending").eq("kind", kind)
                .in_("status", list(statuses)).order("week_ending", desc=True).limit(500).execute().data or [])
    except Exception as e:  # noqa: BLE001
        if _missing_table(e):
            return {**base, "available": False, "hint": MISSING_HINT}
        raise
    weeks: list[str] = []
    for r in rows:
        w = str(r.get("week_ending") or "")[:10]
        if w and w not in weeks:
            weeks.append(w)
    target = week_ending or (weeks[0] if weeks else None)
    base["weeks"] = weeks[:12]
    base["week_ending"] = target
    if not target:
        return base
    got = (client.table("ai_insights").select(COLUMNS).eq("kind", kind).eq("week_ending", target)
           .in_("status", list(statuses)).limit(MAX_LIMIT).execute().data or [])
    got.sort(key=lambda r: (_section_pos(r.get("section")), r.get("rank") is None, r.get("rank") or 0,
                            int(r.get("id") or 0)))
    items = [_out(r, admin) for r in got[:limit]]
    base["insights"] = items
    base["top_actions"] = sorted((i for i in items if i["tag"] == "recommendation" and i["rank"]),
                                 key=lambda i: i["rank"])
    return base


def register(app, limiter) -> None:
    from fastapi import Depends, HTTPException

    from app import database
    from app.auth import CurrentUser, get_current_user

    @app.get("/management/insights")
    def management_insights(week_ending: str | None = None, kind: str = "weekly", status: str | None = None,
                            limit: int = 100, user: CurrentUser = Depends(get_current_user)) -> dict:
        """What the weekly AI Head flagged: admin (every status) and management (approved / done) only."""
        if user.role not in INSIGHT_ROLES:
            raise HTTPException(status_code=403, detail="Requires the admin or management role.")
        if kind not in KINDS:
            raise HTTPException(status_code=400, detail="kind must be weekly or monthly.")
        if week_ending:
            try:
                week_ending = date.fromisoformat(week_ending[:10]).isoformat()
            except ValueError:
                raise HTTPException(status_code=400, detail="week_ending must be a date (YYYY-MM-DD).") from None
        admin = user.role == "admin"
        if admin:
            if status and status not in STATUSES:
                raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(STATUSES)}.")
            statuses = (status,) if status else STATUSES
        else:
            statuses = MANAGEMENT_STATUSES          # management sees what the owner signed off, nothing else
        return list_insights(database.get_client(), kind=kind, week_ending=week_ending, statuses=statuses,
                             limit=limit, admin=admin)
