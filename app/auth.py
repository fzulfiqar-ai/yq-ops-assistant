"""Authentication dependency.

get_current_user decodes a Supabase JWT (HS256, audience "authenticated") using
SUPABASE_JWT_SECRET, then fetches the caller's role from user_roles.

- 401 if the token is missing/invalid/expired.
- 403 if the user has no role row (not provisioned for this tool).

Every data endpoint except /health depends on get_current_user.
"""
from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.database import fetch_role

_bearer = HTTPBearer(auto_error=False)
log = logging.getLogger(__name__)


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
        log.info("token rejected: %s: %s", type(exc).__name__, exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
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


# Data pages the machine (X-Agent-Key) caller may touch — explicitly NOT unrestricted:
# scheduled agents legitimately compute over business data, but the key must never
# unlock governance surfaces (Team, Data ingest, action approval) if it leaks.
AGENT_FEATURES: frozenset[str] = frozenset(
    {"Sales", "Inventory", "Margins", "Receivables", "Orders", "Stock Movement", "Catalog"})


def feature_set(user) -> set[str] | None:
    """The caller's granted feature pages as a set, or None = unrestricted (admin only).
    Used to feature-scope free-text data queries (app/sql_validator.validate)
    so a member can't pull data outside their pages."""
    role = getattr(user, "role", "")
    if role == "admin":
        return None
    if role == "agent":
        return set(AGENT_FEATURES)
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

# The ONLY paths the machine key may authenticate. Belt-and-braces: get_caller is only
# wired to these endpoint families today, but if it is ever attached to something else,
# the key must not silently start working there.
AGENT_PATH_PREFIXES: tuple[str, ...] = (
    "/agents", "/scheduler", "/escalation", "/digest", "/events",
)


def get_caller(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_agent_key: str | None = Header(default=None, alias="X-Agent-Key"),
) -> CurrentUser:
    """Accept EITHER a valid service key (X-Agent-Key) OR a Supabase user JWT.

    Read-only automation endpoints (digests, agent runs, event dispatch) use this so
    schedulers / n8n can call them with a machine key instead of a user login. The key
    only works on the allowlisted automation paths; anywhere else (and when the key is
    absent or wrong) it falls back to normal user-JWT auth.
    """
    key = settings.agent_api_key
    if key and x_agent_key and hmac.compare_digest(x_agent_key, key):
        path = request.url.path
        if any(path == p or path.startswith(p + "/") or path.startswith(p + "?")
               for p in AGENT_PATH_PREFIXES):
            return CurrentUser(user_id="agent", email=AGENT_EMAIL, role="agent")
        log.warning("agent key presented on non-automation path %s — ignored", path)
    return get_current_user(creds)
