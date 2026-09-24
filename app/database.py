"""Supabase client + small query helpers.

The client is created lazily so importing this module (e.g. for /health or tests) never
requires live Supabase credentials.
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from app.config import settings


@lru_cache
def get_client() -> Client:
    """Return a cached Supabase client. Raises a clear error if config is missing."""
    settings.require_supabase()
    return create_client(settings.supabase_url, settings.supabase_key)


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
_LEGACY_RETRY_S = 600
_legacy_until = 0.0


def invalidate_user_cache(email: str | None = None) -> None:
    """Drop cached user_roles rows (one email, or all when None)."""
    if email is None:
        _user_cache.clear()
        return
    for key in {email, (email or "").strip().lower()}:
        _user_cache.pop(key, None)


def _missing_must_reset(exc: Exception) -> bool:
    """PostgREST's 'column user_roles.must_reset does not exist' (42703), whatever the wrapper."""
    text = f"{getattr(exc, 'code', '')} {getattr(exc, 'message', '')} {exc}"
    return "must_reset" in text and ("42703" in text or "does not exist" in text)


def _select_user_row(email: str) -> dict[str, Any] | None:
    global _legacy_until
    cols = _USER_COLUMNS_LEGACY if time.time() < _legacy_until else _USER_COLUMNS
    try:
        resp = get_client().table("user_roles").select(cols).eq("email", email).limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        if cols == _USER_COLUMNS_LEGACY or not _missing_must_reset(exc):
            raise
        _legacy_until = time.time() + _LEGACY_RETRY_S
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
