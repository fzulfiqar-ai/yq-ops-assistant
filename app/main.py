"""FastAPI app — Phase 0.

Exposes ONLY /health (no auth). CORS is whitelisted to the Streamlit URL(s) and a slowapi
rate limit of 30/min is applied. Data endpoints (/ask, /action, webhooks) are added in later
phases and will all depend on app.auth.get_current_user.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])

app = FastAPI(title="YQ Bahrain Ops Assistant", version="0.1.0")
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
    """Liveness probe. No auth. Used by local checks and Railway."""
    return {"status": "ok", "service": "yq-ops-assistant", "version": app.version}
