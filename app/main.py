"""FastAPI app — Phase 1.

Endpoints:
  GET  /health        — liveness probe (no auth)
  POST /ask           — AI query endpoint (auth required)
  GET  /llm/health    — LLM provider status (auth required)
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.auth import CurrentUser, get_current_user
from app.config import settings

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])

app = FastAPI(title="YQ Bahrain Ops Assistant", version="0.2.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
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


@app.post("/ask")
@limiter.limit(settings.rate_limit)
async def ask(
    request: Request,
    body: AskRequest,
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """AI query endpoint. Auth required. Returns reply + sql_used."""
    from app.ai import ask as ai_ask
    result = ai_ask(body.question, user_email=user.email)
    return result


@app.get("/llm/health")
async def llm_health(_user: CurrentUser = Depends(get_current_user)) -> dict:
    """LLM provider health — no secrets exposed."""
    from app.llm_router import health
    return {"providers": health()}
