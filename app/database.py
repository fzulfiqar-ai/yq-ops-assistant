"""Supabase client + small query helpers.

The client is created lazily so importing this module (e.g. for /health or tests) never
requires live Supabase credentials.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

import httpx
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from app.config import settings

log = logging.getLogger(__name__)

# ── Supabase transport (perf-2609, 29-Sep-2026) ──────────────────────────────────────────────
# supabase-py / postgrest 2.31 default to ONE httpx.Client(http2=True), and this process shares
# one SDK client across the whole anyio threadpool. httpcore 1.0.9 allocates HTTP/2 stream ids
# and HPACK-encodes headers outside its lock, so concurrent threads corrupt the shared
# connection: StreamIDTooLowError / KeyError locally, and in prod PROTOCOL_ERROR GOAWAYs,
# Cloudflare HTML "400 Bad Request" pages and 40 s stalls, which the portal showed as
# "Waking the server". An HTTP/1.1 pool gives each in-flight request its own connection.
# Measured, 40 threads x 25 reads against prod (read-only): HTTP/2 389/1000 failed, p95 11 s;
# HTTP/1.1 0/1000 failed.
_POOL_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=20)
# 60 s, not the SDK's 120 s: batch upserts from /ingest share this client, so it stays generous;
# the read RPCs stop themselves at 8 s (statement_timeout).
_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
_READ_ONLY_RPCS = ("/rpc/run_readonly_query", "/rpc/run_readonly_query_params")
_READ_RETRY_ERRORS = (httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.ReadError,
                      httpx.WriteError, httpx.ConnectError)


def _is_read(request: httpx.Request) -> bool:
    """GET/HEAD, or a POST to one of the SELECT-only RPCs (owned by yq_readonly, so running one
    twice cannot change anything)."""
    if request.method in ("GET", "HEAD"):
        return True
    return request.method == "POST" and request.url.path.rstrip("/").endswith(_READ_ONLY_RPCS)


def _edge_error(resp: httpx.Response) -> bool:
    """An HTML error page from Supabase's Cloudflare edge. PostgREST's own errors are JSON, so an
    HTML 4xx/5xx never came from the database."""
    return resp.status_code >= 400 and "text/html" in resp.headers.get("content-type", "")


class RetryReadsTransport(httpx.BaseTransport):
    """Re-send a READ when the connection under it broke or the edge answered with an HTML error
    page. Writes go through untouched: one that failed on the wire may still have landed. (The
    inner transport's own `retries` re-sends only when no connection was made, which is safe for
    writes too.)"""

    def __init__(self, inner: httpx.BaseTransport, attempts: int = 3) -> None:
        self._inner = inner
        self._attempts = attempts

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if not _is_read(request):
            return self._inner.handle_request(request)
        for attempt in range(1, self._attempts + 1):
            last = attempt == self._attempts
            try:
                resp = self._inner.handle_request(request)
            except _READ_RETRY_ERRORS as exc:
                if last:
                    raise
                log.warning("supabase read %s %s: %s, retry %d", request.method, request.url.path,
                            type(exc).__name__, attempt)
            else:
                if last or not _edge_error(resp):
                    return resp
                resp.close()
                log.warning("supabase read %s %s: edge HTML %d, retry %d", request.method,
                            request.url.path, resp.status_code, attempt)
            time.sleep(0.05 * 2 ** attempt + random.uniform(0, 0.1))
        raise AssertionError("unreachable")

    def close(self) -> None:
        self._inner.close()


def _http_client() -> httpx.Client:
    inner = httpx.HTTPTransport(http2=False, limits=_POOL_LIMITS, retries=1)
    return httpx.Client(transport=RetryReadsTransport(inner), timeout=_TIMEOUT, follow_redirects=True)


_client: Client | None = None
_client_lock = threading.Lock()


def get_client() -> Client:
    """Return the process-wide Supabase client (built once, under a lock so concurrent first calls
    cannot build several). Raises a clear error if config is missing."""
    global _client
    client = _client
    if client is None:
        with _client_lock:
            if _client is None:
                settings.require_supabase()
                _client = create_client(settings.supabase_url, settings.supabase_key,
                                        options=SyncClientOptions(httpx_client=_http_client()))
            client = _client
    return client


def reset_client() -> None:
    """Forget the cached client so the next get_client() builds a new one. The old one is not
    closed: another thread may still be mid-request on it; it is garbage once nobody holds it.
    Rarely needed now: the HTTP/1.1 pool drops a broken connection by itself."""
    global _client
    with _client_lock:
        _client = None


# user_roles is read on EVERY authenticated request — once by get_current_user (fetch_role)
# and again by has_feature (_user_row) for non-admins. That was 1-2 uncached PostgREST
# round-trips before any business logic ran, which is expensive on a 0.1-CPU container.
# Cache the row briefly; every write path flushes it via invalidate_user_cache(), so an
# admin changing someone's access still takes effect immediately.
_USER_TTL_S = 60
_user_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}

# `must_reset` is server-owned (R1 security, 24-Sep-2026) and arrives with
# scripts/user_roles_must_reset_migration.sql. The API must deploy BEFORE the migration, so
# a select that names the column is retried without it and the legacy shape is remembered
# for a while (re-probed every _LEGACY_RETRY_S) instead of failing every login.
_USER_COLUMNS = "email,role,features,status,full_name,must_reset"
_USER_COLUMNS_LEGACY = "email,role,features,status,full_name"
_LEGACY_RETRY_S = 60
_legacy_until = 0.0


def invalidate_user_cache(email: str | None = None) -> None:
    """Drop cached user_roles rows (one email, or all when None)."""
    if email is None:
        _user_cache.clear()
        return
    for key in {email, (email or "").strip().lower()}:
        _user_cache.pop(key, None)


def missing_column_error(exc: Exception, column: str) -> bool:
    """True when PostgREST says `column` is not there, whatever the wrapper or the verb:
      - a SELECT naming it: Postgres 42703 'column user_roles.must_reset does not exist'
      - an INSERT / UPDATE payload naming it: PostgREST PGRST204 "Could not find the
        'must_reset' column of 'user_roles' in the schema cache" (PostgREST 14.x)."""
    text = f"{getattr(exc, 'code', '')} {getattr(exc, 'message', '')} {getattr(exc, 'details', '')} {exc}"
    if column not in text:
        return False
    return ("42703" in text or "does not exist" in text or "PGRST204" in text
            or "schema cache" in text or f"Could not find the '{column}' column" in text)


def _missing_must_reset(exc: Exception) -> bool:
    return missing_column_error(exc, "must_reset")


def must_reset_column_absent() -> bool:
    """True while the DB is known to predate the must_reset column (writers then leave it out
    of their payloads instead of paying a failing round trip)."""
    return time.time() < _legacy_until


def note_must_reset_absent() -> None:
    """A read or a write just learned the column is missing: remember it for _LEGACY_RETRY_S."""
    global _legacy_until
    _legacy_until = time.time() + _LEGACY_RETRY_S


def _select_user_row(email: str) -> dict[str, Any] | None:
    cols = _USER_COLUMNS_LEGACY if must_reset_column_absent() else _USER_COLUMNS
    try:
        resp = get_client().table("user_roles").select(cols).eq("email", email).limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        if cols == _USER_COLUMNS_LEGACY or not _missing_must_reset(exc):
            raise
        note_must_reset_absent()
        resp = get_client().table("user_roles").select(_USER_COLUMNS_LEGACY).eq("email", email).limit(1).execute()
    return (resp.data or [None])[0]


def cached_user_row(email: str) -> dict[str, Any] | None:
    """The user_roles row for `email`, cached for _USER_TTL_S.

    Keyed on the exact string passed so callers that normalise the address and callers
    that don't each keep their existing semantics; in the normal all-lowercase case both
    share one entry, collapsing the two per-request lookups into a single query.
    The row carries `must_reset` once the column exists; callers treat a missing key as False.
    """
    hit = _user_cache.get(email)
    if hit and time.time() - hit[0] < _USER_TTL_S:
        return hit[1]
    row = _select_user_row(email)
    _user_cache[email] = (time.time(), row)
    return row


def fetch_role(email: str) -> str | None:
    """Return the role for a user email from user_roles, or None if not present."""
    row = cached_user_row(email)
    return row.get("role") if row else None
