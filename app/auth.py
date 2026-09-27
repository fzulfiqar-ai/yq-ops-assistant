"""Authentication dependency.

get_current_user decodes a Supabase JWT (HS256, audience "authenticated") using
SUPABASE_JWT_SECRET, then fetches the caller's role from user_roles.

- 401 if the token is missing/invalid/expired.
- 403 if the user has no role row (not provisioned for this tool), if the row's status is
  not 'active' (a disabled member keeps a valid Supabase session until it expires — the row
  is the gate), or if `must_reset` is set and the route is not one of the few needed to
  change the password (R1 security, 24-Sep-2026).
- 403 {"code": "read_only"} for a read-only role (management, release R7a) on any request that
  is not GET/HEAD/OPTIONS, except READ_ONLY_WRITE_ALLOWLIST, and on the AI surfaces
  (READ_ONLY_DENIED_PREFIXES) whatever the method. This is the one central gate: a route never
  has to remember it, and a new write route is refused to management the day it is added.

Release R7b "Safe access" (27-Sep-2026):
- must_reset never locks out an owner (settings.owner_emails): the break-glass login always gets in.
- 401 for a token of a session that a password change signed out (end_other_sessions): Supabase
  stops the refresh (POST /auth/password asks it to sign the other sessions out), this closes the
  window of the access tokens already issued, in this process (the API runs one worker).
- A login trail: the first request of every Supabase session (the `session_id` claim) seen by this
  process leaves one audit_log row 'auth.session_seen' (session id, role, sign-in method, hashed
  client address, browser) — never the token.

Every data endpoint except /health depends on get_current_user.
"""
from __future__ import annotations

import hmac
import logging
import threading
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.database import cached_user_row
from app.features import is_read_only, may_hold

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

# A read-only login (management) changes nothing: every request that is not a read is refused
# here, except the few a person needs to use the portal at all. There is no logout or profile
# route on the API (sign-out is Supabase's, preferences live in the browser), so the list is
# the login's own password change. Keys are (method, route path) exactly as the request names them.
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
# A path may be a route template: "{name}" matches one path segment. The Market Intel approval
# (R7b, plan §7 "read + approve actions") is the one decision management makes; the route itself
# only moves an Opportunity to Approved with an action (app/market_intel.approve_item).
READ_ONLY_WRITE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/auth/password"),
    ("POST", "/market-intel/items/{item_id}/approve"),
})
# The AI surfaces run agents, free-text SQL and uploads: never a read-only login's, GET included.
READ_ONLY_DENIED_PREFIXES: tuple[str, ...] = ("/agents", "/ask", "/orchestrate", "/assistant", "/field-notes",
                                              "/coaching")   # the brief recalls field notes from the AI knowledge base
READ_ONLY_CODE = "read_only"
READ_ONLY_DETAIL: dict[str, str] = {"code": READ_ONLY_CODE,
                                    "message": "Your access is read-only. Ask an admin to make this change."}


def _allowlisted(method: str, path: str) -> bool:
    for m, p in READ_ONLY_WRITE_ALLOWLIST:
        if m != method:
            continue
        if p == path:
            return True
        if "{" in p and re.fullmatch(re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(p)), path or ""):
            return True
    return False


def read_only_refuses(method: str, path: str) -> bool:
    """True when a read-only login may not make this request (see READ_ONLY_WRITE_ALLOWLIST)."""
    method = (method or "").upper()
    if any(path == p or path.startswith(p + "/") for p in READ_ONLY_DENIED_PREFIXES):
        return True
    return method not in SAFE_METHODS and not _allowlisted(method, path)

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
        # monotonic time of the last refresh=True fetch; -inf so the first refresh is never held
        # back by how long the host happens to have been up (monotonic counts from boot on Linux)
        self._last_refresh = float("-inf")

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
    # the Supabase session behind the token (`session_id` claim) and when the token was issued;
    # None for a machine caller or a token without the claims
    session_id: str | None = None
    issued_at: int | None = None


def _is_owner(email: str | None) -> bool:
    e = (email or "").strip().lower()
    return bool(e) and e in {o.strip().lower() for o in settings.owner_emails}


def _session_id(payload: dict) -> str:
    """Supabase names it `session_id`; `sid` is the OIDC spelling. Empty when the token has neither."""
    return str(payload.get("session_id") or payload.get("sid") or "")[:64]


# ── sessions a password change signed out (release R7b) ──────────────────────────
# POST /auth/password asks Supabase to sign every OTHER session of the login out (their refresh
# tokens die), and records here that tokens issued before the change, from any session but the one
# that changed the password, are no longer accepted. Access tokens live up to an hour, so an entry
# is kept for a day. In memory: one uvicorn worker (Dockerfile); after a restart Supabase's own
# sign-out still holds and the old access tokens simply expire.
SESSION_CUTOFF_KEEP_S = 24 * 3600
# Tokens issued this close to the change are let through: the API's clock and Supabase's may
# differ by a few seconds, and a fresh sign-in right after the change must never be refused.
SESSION_CUTOFF_SKEW_S = 30
SESSION_ENDED_DETAIL = "This session was signed out after a password change. Sign in again."
_ended_sessions: dict[str, tuple[float, str]] = {}     # user id -> (cut-off epoch, the session kept)
_ended_lock = threading.Lock()


def end_other_sessions(user_id: str, keep_session_id: str, at: float | None = None) -> None:
    """From now on refuse the login's tokens issued before `at` by any session but `keep_session_id`."""
    if not user_id or not keep_session_id:
        return
    now = time.time() if at is None else at
    with _ended_lock:
        for uid in [u for u, (cut, _k) in _ended_sessions.items() if now - cut > SESSION_CUTOFF_KEEP_S]:
            _ended_sessions.pop(uid, None)
        _ended_sessions[user_id] = (now, keep_session_id)


def session_ended(user_id: str, session_id: str, issued_at) -> bool:
    hit = _ended_sessions.get(user_id or "")
    if not hit:
        return False
    cut, keep = hit
    if time.time() - cut > SESSION_CUTOFF_KEEP_S or (session_id and session_id == keep):
        return False
    try:
        iat = float(issued_at)
    except (TypeError, ValueError):
        return True                    # a token that does not say when it was issued predates nothing
    return iat < cut - SESSION_CUTOFF_SKEW_S


# ── the login trail (release R7b) ────────────────────────────────────────────────
SESSION_SEEN_EVENT = "auth.session_seen"
SESSION_SEEN_MAX = 4096                # session ids remembered per process (oldest forgotten first)
_seen_sessions: OrderedDict[str, None] = OrderedDict()
_seen_lock = threading.Lock()


def _first_sight(session_id: str) -> bool:
    with _seen_lock:
        if session_id in _seen_sessions:
            return False
        _seen_sessions[session_id] = None
        while len(_seen_sessions) > SESSION_SEEN_MAX:
            _seen_sessions.popitem(last=False)
        return True


def note_session(request: Request, payload: dict, email: str, role: str, user_status: str) -> None:
    """One audit_log row the first time this process sees a session: who, which session, how they
    signed in, from which (hashed) address and browser. Never the token, never a raw IP. A token
    without a session id is not recorded (there is nothing to tell its requests apart by)."""
    sid = _session_id(payload)
    if not sid or not _first_sight(sid):
        return
    try:
        from app.ratelimit import client_ip
        from app.shop import _ip_hash
        ip_hash = _ip_hash(client_ip(request))
    except Exception:  # noqa: BLE001 — the trail never breaks a request
        ip_hash = None
    amr = payload.get("amr") if isinstance(payload.get("amr"), list) else []
    detail = {
        "session_id": sid, "role": role, "status": user_status, "aal": payload.get("aal"),
        "methods": [str(m.get("method"))[:20] for m in amr if isinstance(m, dict) and m.get("method")][:4],
        "issued_at": payload.get("iat"), "method": request.method, "path": (request.scope.get("path") or "")[:120],
        "ip_hash": ip_hash, "ua": (request.headers.get("user-agent") or "")[:160],
    }
    from app.audit import log_event
    log_event(email, SESSION_SEEN_EVENT, detail=detail)


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

    email = payload.get("email") or ""
    user_id = payload.get("sub") or ""
    session_id = _session_id(payload)
    if session_ended(user_id, session_id, payload.get("iat")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=SESSION_ENDED_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        )
    # The ONLY place a token earns a per-user rate-limit bucket: here, in the threadpool, after
    # it verified. app.ratelimit.rate_limit_key never decodes anything itself.
    from app.ratelimit import remember_verified
    remember_verified(creds.credentials)

    row = cached_user_row(email)
    role = row.get("role") if row else None
    if not role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no assigned role for this tool.",
        )
    user_status = str(row.get("status") or "active")
    # provisioned logins only: a token from a stray sign-up must not be able to fill audit_log
    note_session(request, payload, email, role, user_status)
    if user_status != "active":
        # verify_login already refuses a disabled member; this closes the window for a token
        # minted before the switch (and update_access also bans the auth user).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled.",
        )
    # The column may not exist until user_roles_must_reset_migration.sql runs: absent = False.
    # An owner is never held at the password screen (the break-glass login, plan §32).
    must_reset = bool(row.get("must_reset")) and not _is_owner(email)
    if must_reset and request.scope.get("path") not in MUST_RESET_EXEMPT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=MUST_RESET_DETAIL)
    if is_read_only(role) and read_only_refuses(request.method, request.scope.get("path") or ""):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=READ_ONLY_DETAIL)

    iat = payload.get("iat")
    return CurrentUser(user_id=user_id, email=email, role=role, status=user_status, must_reset=must_reset,
                       session_id=session_id or None, issued_at=int(iat) if isinstance(iat, (int, float)) else None)


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
    """True if the user may access a feature page (admins always may). A page outside the role's
    limit (features.ROLE_FEATURE_LIMITS — management: its six read pages) is refused even when
    the row lists it."""
    if user.role == "admin":
        return True
    if not may_hold(user.role, feature):
        return False
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
        feats = set((_user_row(getattr(user, "email", "")) or {}).get("features") or [])
    except Exception:  # noqa: BLE001
        return set()
    return {f for f in feats if may_hold(role, f)}


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
