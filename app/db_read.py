"""Read-only SQL execution primitives (extracted from app.ai so data access no longer
requires importing the LLM engine).

Both RPCs are SECURITY DEFINER functions owned by the SELECT-only `yq_readonly` role
(scripts/security_migration.sql), so nothing that reaches them can write, whatever the
SQL says. Use exec_sql_params for ANY value that originates outside the codebase
(user input, URL params, parsed documents) — never interpolate those into SQL strings.
"""
from __future__ import annotations

import json

from app.database import get_client


def exec_sql(sql: str) -> list[dict]:
    """Execute pre-validated, parameter-free SQL via Supabase RPC and return rows."""
    r = get_client().rpc("run_readonly_query", {"sql_text": sql}).execute()
    data = r.data
    if isinstance(data, str):
        data = json.loads(data)
    return data or []


def exec_sql_params(sql: str, params: list) -> list[dict]:
    """Execute SQL with bound parameters ($1..$8, all text — cast in SQL as needed).

    Bind an IN-list as ONE json-encoded array param:
        col IN (SELECT jsonb_array_elements_text($2::jsonb))   with params=[x, json.dumps(items)]
    """
    r = get_client().rpc(
        "run_readonly_query_params",
        {"sql_text": sql, "params": [str(p) for p in params]},
    ).execute()
    data = r.data
    if isinstance(data, str):
        data = json.loads(data)
    return data or []
