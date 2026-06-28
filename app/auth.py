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
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.database import fetch_role

_bearer = HTTPBearer(auto_error=False)


@lru_cache
def _jwks_client() -> PyJWKClient:
    """Cached JWKS client for the project's asymmetric (ES256/RS256) signing keys."""
    return PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")


def _decode_token(token: str) -> dict:
    """Validate a Supabase access token.

    Supports BOTH the legacy HS256 (shared `SUPABASE_JWT_SECRET`) and the newer
    asymmetric ES256/RS256 tokens issued under the publishable/secret key system
    (validated against the project JWKS).
    """
    alg = jwt.get_unverified_header(token).get("alg", "HS256")
    if alg == "HS256":
        return jwt.decode(
            token, settings.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated"
        )
    signing_key = _jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token, signing_key.key, algorithms=["ES256", "RS256", "EdDSA"], audience="authenticated"
    )


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
        payload = _decode_token(creds.credentials)
    except Exception as exc:
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


def has_feature(user: CurrentUser, feature: str) -> bool:
    """True if the user may access a feature page (admins always may)."""
    if user.role == "admin":
        return True
    try:
        from app.user_auth import _user_row
        feats = (_user_row(user.email) or {}).get("features") or []
    except Exception:
        feats = []
    return feature in feats


def feature_set(user) -> set[str] | None:
    """The caller's granted feature pages as a set, or None = unrestricted (admin / trusted
    agent-key caller). Used to feature-scope free-text data queries (app/sql_validator.validate)
    so a member can't pull data outside their pages."""
    role = getattr(user, "role", "")
    if role in ("admin", "agent"):
        return None
    try:
        from app.user_auth import _user_row
        return set((_user_row(getattr(user, "email", "")) or {}).get("features") or [])
    except Exception:  # noqa: BLE001
        return set()


def require_feature(feature: str):
    """Dependency factory: gate an endpoint behind a granted feature page.

    The API is the trust boundary — hiding nav in the SPA is UX only.
    """
    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not has_feature(user, feature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires access to '{feature}'.",
            )
        return user

    return _dep


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
