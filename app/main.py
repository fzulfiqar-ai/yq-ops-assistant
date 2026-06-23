"""FastAPI app — Phase 2 / 2.5 / 3.

Endpoints:
  GET  /health                    — liveness (no auth)
  POST /ask                       — AI query (auth)
  GET  /llm/health                — LLM provider status (auth)
  GET  /digest/daily              — daily ops summary (auth)
  GET  /digest/alerts             — low-stock + overdue + margin alerts (auth)
  POST /action                    — submit pending action (auth)
  GET  /actions                   — list actions (auth)
  PATCH /actions/{id}/approve     — approve action (auth)
  PATCH /actions/{id}/reject      — reject action (auth)
  GET  /actions/export            — download approved CSV (auth)
  POST /ingest                    — upload Focus Excel → auto-ingest (auth)
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, File, Form, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.audit import log_event
from app.auth import CurrentUser, get_caller, get_current_user, require_admin
from app.config import settings

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])

app = FastAPI(title="YQ Bahrain Ops Assistant", version="0.3.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Local dev origins are always allowed; production origins come from ALLOWED_ORIGINS.
_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8501"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(set(settings.allowed_origins) | set(_DEV_ORIGINS)),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
@limiter.limit(settings.rate_limit)
async def health(request: Request) -> dict:
    return {"status": "ok", "service": "yq-ops-assistant", "version": app.version}


class AskRequest(BaseModel):
    question: str
    model: str | None = None


@app.post("/ask")
@limiter.limit(settings.rate_limit)
async def ask(request: Request, body: AskRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    from app.ai import ask as ai_ask
    return ai_ask(body.question, user_email=user.email, model_name=body.model)


@app.get("/search")
async def global_search(q: str = "", _user: CurrentUser = Depends(get_current_user)) -> list:
    """Global ⌘K search across customers, items and salesmen."""
    from app.reports import search
    return search(q)


@app.get("/report/{key}")
async def report(key: str, user: CurrentUser = Depends(get_current_user)):
    """Read-only data for a portal page, gated by the matching feature."""
    from fastapi import HTTPException
    from app.auth import has_feature
    from app.reports import REPORTS, REPORT_FEATURE
    if key not in REPORTS:
        raise HTTPException(status_code=404, detail=f"Unknown report '{key}'.")
    if not has_feature(user, REPORT_FEATURE[key]):
        raise HTTPException(status_code=403, detail=f"Requires access to '{REPORT_FEATURE[key]}'.")
    return REPORTS[key]()


@app.get("/llm/health")
async def llm_health(_user: CurrentUser = Depends(get_current_user)) -> dict:
    from app.llm_router import health
    return {"providers": health()}


@app.get("/digest/daily")
async def digest_daily(_caller: CurrentUser = Depends(get_caller)) -> dict:
    from app.digest import daily_summary
    return daily_summary()


@app.get("/digest/alerts")
async def digest_alerts(_caller: CurrentUser = Depends(get_caller)) -> dict:
    from app.digest import all_alerts
    return all_alerts()


@app.get("/agents")
async def agents_list(_caller: CurrentUser = Depends(get_caller)) -> list:
    from app.agents import list_agents
    return list_agents()


@app.get("/agents/{name}")
async def agents_run(name: str, email: bool = False, caller: CurrentUser = Depends(get_caller)) -> dict:
    from app.agents import run_agent
    try:
        result = run_agent(name)
    except KeyError:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown agent '{name}'.")
    if email:
        from app.emailer import send_agent
        result["email"] = send_agent(result)
    log_event(caller.email, "agent", detail={"agent": name, "summary": result.get("summary")})
    return result


@app.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Identity + access for the SPA: role + granted feature pages."""
    role, features, full_name = user.role, [], ""
    try:
        from app.user_auth import _user_row
        row = _user_row(user.email)
        if row:
            role = row.get("role", role)
            features = row.get("features") or []
            full_name = row.get("full_name") or ""
    except Exception:  # columns may predate the team migration
        pass
    return {"email": user.email, "role": role, "features": features, "full_name": full_name}


# ── Team & access management (admin) ─────────────────────────────────────────

class InviteRequest(BaseModel):
    email: str
    full_name: str = ""
    role: str = "member"
    features: list[str] = []
    method: str = "temp"  # "temp" (set a temp password) | "email" (send a link)


class UpdateAccessRequest(BaseModel):
    role: str | None = None
    features: list[str] | None = None
    status: str | None = None


class AcceptRequest(BaseModel):
    token: str
    password: str
    full_name: str | None = None


@app.get("/team")
async def team_list(_admin: CurrentUser = Depends(require_admin)) -> dict:
    from app.user_auth import list_members
    return list_members()


@app.post("/team/invite")
async def team_invite(body: InviteRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
    from app.user_auth import FEATURES, create_email_invite, create_member, generate_temp_password
    grant = body.features if body.role == "member" else list(FEATURES)
    if body.method == "email":
        res = create_email_invite(body.email, body.full_name, body.role, grant, invited_by=admin.email)
        log_event(admin.email, "team.invite", detail={"email": body.email, "mode": "email"})
        return {"mode": "email", **res}
    tmp = generate_temp_password()
    create_member(body.email, body.full_name, body.role, grant, tmp, invited_by=admin.email, must_reset=True)
    log_event(admin.email, "team.invite", detail={"email": body.email, "mode": "temp"})
    return {"mode": "temp", "email": body.email, "temp_password": tmp}


@app.patch("/team/{email}")
async def team_update(email: str, body: UpdateAccessRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
    from app.user_auth import update_access
    update_access(email, role=body.role, features=body.features, status=body.status)
    log_event(admin.email, "team.update", detail={"email": email})
    return {"ok": True}


@app.delete("/team/{email}")
async def team_remove(email: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    from fastapi import HTTPException
    from app.user_auth import remove_user
    if email.strip().lower() == admin.email:
        raise HTTPException(status_code=400, detail="You cannot remove your own account.")
    remove_user(email)
    log_event(admin.email, "team.remove", detail={"email": email})
    return {"ok": True}


@app.get("/team/invite/{token}")
async def team_invite_info(token: str) -> dict:
    from fastapi import HTTPException
    from app.user_auth import get_invite
    inv = get_invite(token)
    if not inv:
        raise HTTPException(status_code=404, detail="Invalid or expired invite.")
    return {"email": inv["email"], "role": inv["role"], "features": inv.get("features") or []}


@app.post("/team/accept")
async def team_accept(body: AcceptRequest) -> dict:
    from fastapi import HTTPException
    from app.user_auth import accept_invite
    res = accept_invite(body.token, body.password, body.full_name)
    if not res:
        raise HTTPException(status_code=400, detail="Invalid or expired invite.")
    return res


class ActionRequest(BaseModel):
    action_type: str
    payload: dict
    notes: str = ""


@app.post("/action")
async def submit_action(body: ActionRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    from app.actions import submit_action as _submit
    result = _submit(body.action_type, {**body.payload, "notes": body.notes}, requested_by=user.email)
    log_event(user.email, "action.submit", detail={"action_type": body.action_type})
    return result


@app.get("/actions")
async def list_actions(status: str | None = None, _user: CurrentUser = Depends(get_current_user)) -> list:
    from app.actions import list_actions as _list
    return _list(status=status)


@app.patch("/actions/{action_id}/approve")
async def approve_action(action_id: int, user: CurrentUser = Depends(require_admin)) -> dict:
    from app.actions import approve_action as _approve
    result = _approve(action_id, approved_by=user.email)
    log_event(user.email, "action.approve", detail={"action_id": action_id})
    return result


@app.patch("/actions/{action_id}/reject")
async def reject_action(action_id: int, reason: str = "", user: CurrentUser = Depends(require_admin)) -> dict:
    from app.actions import reject_action as _reject
    result = _reject(action_id, approved_by=user.email, reason=reason)
    log_event(user.email, "action.reject", detail={"action_id": action_id, "reason": reason})
    return result


@app.get("/actions/export")
async def export_actions(_user: CurrentUser = Depends(require_admin)) -> Response:
    from app.actions import export_approved_csv
    return Response(content=export_approved_csv(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=approved_actions.csv"})


@app.post("/ingest")
async def ingest_file(
    file: UploadFile = File(...),
    report_type: str = Form(default="auto"),
    user: CurrentUser = Depends(require_admin),
) -> dict:
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[1]
    contents = await file.read()
    suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
    dest_dir = ROOT / "Focus ERP Data"
    dest_dir.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=dest_dir) as tmp:
        tmp.write(contents)
        saved_to = tmp.name
    r1 = subprocess.run([sys.executable, "-m", "scripts.ingest"], cwd=ROOT, capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, "-m", "scripts.load_supabase"], cwd=ROOT, capture_output=True, text=True)
    log_event(user.email, "ingest", detail={
        "filename": file.filename, "ingest_ok": r1.returncode == 0, "load_ok": r2.returncode == 0,
    })
    # Record an ingest run for the "Data as of" freshness banner (best-effort).
    try:
        from datetime import datetime, timezone
        from app.database import get_client
        ok = r1.returncode == 0 and r2.returncode == 0
        get_client().table("ingest_runs").insert({
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok" if ok else "error",
            "file": file.filename,
            "errors": None if ok else ((r1.stderr or "") + (r2.stderr or ""))[-500:],
        }).execute()
    except Exception:
        pass
    return {
        "filename": file.filename,
        "report_type": report_type,
        "saved_to": saved_to,
        "ingest_ok": r1.returncode == 0,
        "load_ok": r2.returncode == 0,
        "ingest_log": (r1.stdout or r1.stderr)[-400:],
        "load_log": (r2.stdout or r2.stderr)[-400:],
        "uploaded_by": user.email,
    }
