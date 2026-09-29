"""Read-only SQL execution primitives (extracted from app.ai so data access no longer
requires importing the LLM engine).

Both RPCs are SECURITY DEFINER functions owned by the SELECT-only `yq_readonly` role
(scripts/security_migration.sql), so nothing that reaches them can write, whatever the
SQL says. Use exec_sql_params for ANY value that originates outside the codebase
(user input, URL params, parsed documents) — never interpolate those into SQL strings.

Both retry ONCE on a dropped connection (retry_read): they only read, so running them
again cannot change anything.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, TypeVar

from app.database import get_client

log = logging.getLogger(__name__)
T = TypeVar("T")


def _transient_errors() -> tuple[type[BaseException], ...]:
    """The connection-level failures worth one more try (R7a). Supabase closes idle HTTP/2
    connections (GOAWAY → httpcore ConnectionTerminated → RemoteProtocolError), and the next
    request on the pooled connection fails before the server saw it; a reset or refused socket
    is a ReadError / WriteError / ConnectError. Timeouts are NOT here: a slow query retried is
    twice as slow. httpx wraps the httpcore classes; both are listed in case one leaks through."""
    out: list[type[BaseException]] = []
    try:
        import httpx
        out += [httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.ReadError,
                httpx.WriteError, httpx.ConnectError]
    except ImportError:  # pragma: no cover — httpx ships with supabase
        pass
    try:
        import httpcore
        out += [httpcore.RemoteProtocolError, httpcore.LocalProtocolError, httpcore.ReadError,
                httpcore.WriteError, httpcore.ConnectError]
    except ImportError:  # pragma: no cover
        pass
    return tuple(out)


TRANSIENT_ERRORS = _transient_errors()


def retry_read(run: Callable[[], T], what: str = "read") -> T:
    """Run an idempotent READ; if the connection dropped under it, run it once more.

    A second line of defence: app.database's RetryReadsTransport already re-sends every read on a
    broken connection. The client is NOT reset any more: the HTTP/1.1 pool discards the broken
    connection itself, and every failing thread resetting at once (the old behaviour) threw away
    the healthy connections too and sent the retries into the same storm.

    For reads only — a write that failed on the wire may still have landed, and running it
    again could apply it twice."""
    try:
        return run()
    except TRANSIENT_ERRORS as exc:
        log.warning("%s: connection dropped (%s: %s) — retrying once",
                    what, type(exc).__name__, str(exc)[:160])
        return run()


def _rows(data) -> list[dict]:
    if isinstance(data, str):
        data = json.loads(data)
    return data or []


def exec_sql(sql: str) -> list[dict]:
    """Execute pre-validated, parameter-free SQL via Supabase RPC and return rows."""
    r = retry_read(lambda: get_client().rpc("run_readonly_query", {"sql_text": sql}).execute(),
                   what="exec_sql")
    return _rows(r.data)


def exec_sql_params(sql: str, params: list) -> list[dict]:
    """Execute SQL with bound parameters ($1..$8, all text — cast in SQL as needed).

    Bind an IN-list as ONE json-encoded array param:
        col IN (SELECT jsonb_array_elements_text($2::jsonb))   with params=[x, json.dumps(items)]
    """
    args = {"sql_text": sql, "params": [str(p) for p in params]}
    r = retry_read(lambda: get_client().rpc("run_readonly_query_params", args).execute(),
                   what="exec_sql_params")
    return _rows(r.data)
