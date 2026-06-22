"""Supabase client + small query helpers.

The client is created lazily so importing this module (e.g. for /health or tests) never
requires live Supabase credentials.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from app.config import settings


@lru_cache
def get_client() -> Client:
    """Return a cached Supabase client. Raises a clear error if config is missing."""
    settings.require_supabase()
    return create_client(settings.supabase_url, settings.supabase_key)


def fetch_role(email: str) -> str | None:
    """Return the role for a user email from user_roles, or None if not present."""
    client = get_client()
    resp = (
        client.table("user_roles")
        .select("role")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    rows: list[dict[str, Any]] = resp.data or []
    return rows[0]["role"] if rows else None
