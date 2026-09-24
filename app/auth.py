"""Authentication dependency.

get_current_user decodes a Supabase JWT (HS256, audience "authenticated") using
SUPABASE_JWT_SECRET, then fetches the caller's role from user_roles.

- 401 if the token is missing/invalid/expired.
- 403 if the user has no role row (not provisioned for this tool), if the row's status is
  not 'active' (a disabled member keeps a valid Supabase session until it expires — the row
  is the gate), or if `must_reset` is set and the route is not one of the few needed to
  change the password (R1 security, 24-Sep-2026).

Every data endpoint except /health depends on get_current_user.
"""
from __future__ import annotations

import hmac
import logging
import time
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.database import cached_user_row

_bearer = HTTPBearer(auto_error=False)
log = logging.getLogger(__name__)

# A member on a temporary password may only find out who they are, load the feature list and
# set a new password. `must_reset` lives in user_roles (server-owned; the auth user_metadata
# copy is writable by the user, so it is never consulted for enforcement).
MUST_RESET_EXEMPT: frozenset[str] = frozenset({"/me", "/auth/features", "/auth/password"})
# The 403 body is {"detail": {"code": ..., "message": ...}} so the SPA's api layer can recognise
# it and send the member to their password screen instead of showing a raw error.
MUST_RESET_CODE = "password_change_required"
MUST_RESET_DETAIL: dict[str, str] = {"code": MUST_RESET_CODE,
                                     "message": "Set your own password to continue."}

# An unknown `kid` makes PyJWT refresh the JWK set over the network. Supabase rotates keys
# rarely, so one refresh per minute per process is plenty; anything more is a token flood
# (each made-up ES256 token used to cost a 150 ms-1.4 s fetch in the threadpool).
JWKS_REFRESH_MIN_INTERVAL_S = 60.0


class _ThrottledJWKClient(PyJWKClient):
    """PyJWKClient whose unknown-kid refresh happens at most once per
    JWKS_REFRESH_MIN_INTERVAL_S process-wide. Inside the window an unknown kid fails at once
    from the cached set (no network), exactly as a bad signature would."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._last_refresh = 0.0     # monotonic time of the last refresh=True fetch

    def get_signing_keys(self, refresh: bool = False):
        if refresh:
            now = time.monotonic()
            if now - self._last_refresh < JWKS_REFRESH_MIN_INTERVAL_S:
                if self.jwk_set_cache is not None and self.jwk_set_cache.get() is not None:
                    return super().get_signing_keys(refresh=False)
                raise PyJWKClientError("JWKS refresh throttled: unknown signing key")
            self._last_refresh = now
        return super().get_signing_keys(refresh=refresh)


@lru_cache
def _jwks_client() -> PyJWKClient:
    """Cached JWKS client for the project's asymmetric (ES256/RS256) signing keys.

    cache_keys=True matters a lot on a 0.1-CPU container: without it PyJWT rebuilds the
    signing key through `cryptography` on EVERY request (an asymmetric key parse per API
    call). With it, the parsed key object is reused and only the JWK *set* refreshes.
    """
    return _ThrottledJWKClient(
        f"{settings.supabase_url}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
    )


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
    status: str = "active"
    must_reset: bool = False


def get_current_user(
    request: Request,
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
    # The ONLY place a token earns a per-user rate-limit bucket: here, in the threadpool, after
    # it verified. app.ratelimit.rate_limit_key never decodes anything itself.
    from app.ratelimit import remember_verified
    remember_verified(creds.credentials)

    email = payload.get("email") or ""
    user_id = payload.get("sub") or ""

    row = cached_user_row(email)
    role = row.get("role") if row else None
    if not role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no assigned role for this tool.",
        )
    user_status = str(row.get("status") or "active")
    if user_status != "active":
        # verify_login already refuses a disabled member; this closes the window for a token
        # minted before the switch (and update_access also bans the auth user).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled.",
        )
    # The column may not exist until user_roles_must_reset_migration.sql runs: absent = False.
    must_reset = bool(row.get("must_reset"))
    if must_reset and request.scope.get("path") not in MUST_RESET_EXEMPT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=MUST_RESET_DETAIL)

    return CurrentUser(user_id=user_id, email=email, role=role, status=user_status, must_reset=must_reset)


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

    _dep.feature = feature       # read by tests/test_r1_security.py's route → gate table
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
    return get_current_user(request, creds)


def require_agent_or_admin(caller: CurrentUser = Depends(get_caller)) -> CurrentUser:
    """The machine key (schedulers, crons) OR an admin login — nobody else.

    Digests, briefs, event dispatch and agent runs read COGS, margins and receivables and can
    send owner alerts; before R1 any login's JWT could call them (audit S2). Built on get_caller
    so a dependency override of get_caller (scripts/smoke_check.py) still flows through.
    """
    if caller.role not in ("agent", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires the automation key or an admin login.",
        )
    return caller
