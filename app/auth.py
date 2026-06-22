"""Authentication dependency.

get_current_user decodes a Supabase JWT (HS256, audience "authenticated") using
SUPABASE_JWT_SECRET, then fetches the caller's role from user_roles.

- 401 if the token is missing/invalid/expired.
- 403 if the user has no role row (not provisioned for this tool).

Every data endpoint except /health depends on get_current_user.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.database import fetch_role

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user_id: str
    email: str
    role: str


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(
            creds.credentials,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    email = payload.get("email") or ""
    user_id = payload.get("sub") or ""

    role = fetch_role(email)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no assigned role for this tool.",
        )

    return CurrentUser(user_id=user_id, email=email, role=role)


def require_roles(*allowed: str):
    """Dependency factory: restrict an endpoint to specific roles (used from Phase 2)."""

    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role in {allowed}; you are '{user.role}'.",
            )
        return user

    return _dep


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Restrict an endpoint to admin users (mutating / governance actions)."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires admin role; you are '{user.role}'.",
        )
    return user


AGENT_EMAIL = "agent@yqbahrain.local"


def get_caller(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_agent_key: str | None = Header(default=None, alias="X-Agent-Key"),
) -> CurrentUser:
    """Accept EITHER a valid service key (X-Agent-Key) OR a Supabase user JWT.

    Read-only automation endpoints (digests, agent runs) use this so schedulers /
    n8n can call them with a machine key instead of a user login. If the key is
    absent or wrong, it falls back to normal user-JWT auth.
    """
    key = settings.agent_api_key
    if key and x_agent_key and hmac.compare_digest(x_agent_key, key):
        return CurrentUser(user_id="agent", email=AGENT_EMAIL, role="agent")
    return get_current_user(creds)
