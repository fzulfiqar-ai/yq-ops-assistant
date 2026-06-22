"""Best-effort audit logging to the audit_log table.

Never raises — auditing must never break a request. Writes via the Supabase
service client (bypasses RLS). Columns match scripts/schema.sql:
  audit_log(ts, user_email, event, question, sql_used, detail jsonb)
"""
from __future__ import annotations

from typing import Any


def log_event(
    user_email: str,
    event: str,
    *,
    question: str | None = None,
    sql_used: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        from app.database import get_client

        get_client().table("audit_log").insert(
            {
                "user_email": user_email,
                "event": event,
                "question": question,
                "sql_used": sql_used,
                "detail": detail,
            }
        ).execute()
    except Exception:
        # Auditing is best-effort; swallow all errors (missing creds, table, etc.).
        pass
