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


def invalidate_user_cache(email: str | None = None) -> None:
    """Drop cached user_roles rows (one email, or all when None)."""
    if email is None:
        _user_cache.clear()
        return
    for key in {email, (email or "").strip().lower()}:
        _user_cache.pop(key, None)


def cached_user_row(email: str) -> dict[str, Any] | None:
    """The user_roles row for `email`, cached for _USER_TTL_S.

    Keyed on the exact string passed so callers that normalise the address and callers
    that don't each keep their existing semantics; in the normal all-lowercase case both
    share one entry, collapsing the two per-request lookups into a single query.
    """
    hit = _user_cache.get(email)
    if hit and time.time() - hit[0] < _USER_TTL_S:
        return hit[1]
    resp = (
        get_client().table("user_roles")
        .select("email,role,features,status,full_name")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    row = (resp.data or [None])[0]
    _user_cache[email] = (time.time(), row)
    return row


def fetch_role(email: str) -> str | None:
    """Return the role for a user email from user_roles, or None if not present."""
    row = cached_user_row(email)
    return row.get("role") if row else None
