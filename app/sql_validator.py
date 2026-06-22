"""SQL guardrails for the /ask endpoint.

Validates LLM-generated SQL before execution. Rules:
  1. Single SELECT statement only.
  2. No DML / DDL / dangerous keywords.
  3. Must reference only the approved view allowlist.
  4. LIMIT injected at MAX_ROWS if missing.
"""
from __future__ import annotations

import re

# LLM may only query these views — never raw tables.
VIEW_ALLOWLIST: frozenset[str] = frozenset({
    "v_sales",
    "v_current_stock",
    "v_product_margin",
    "v_receivables",
    "v_top_customers",
    "v_sales_by_period",
    "v_low_stock",
    "shipments",
})

MAX_ROWS = 200

_BANNED = re.compile(
    r"\b(insert|update|delete|truncate|drop|create|alter|grant|revoke"
    r"|copy|pg_read_file|dblink|pg_exec|execute|perform)\b",
    re.IGNORECASE,
)
_LIMIT = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_SEMICOLON_MID = re.compile(r";(?!\s*$)")
_TABLE_REF = re.compile(r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)", re.IGNORECASE)


class SQLValidationError(ValueError):
    pass


def validate(sql: str) -> str:
    """Validate and return cleaned SQL (LIMIT injected if missing).

    Raises SQLValidationError with a safe message on any violation.
    Never raises on valid SELECT-only queries against the view allowlist.
    """
    sql = sql.strip().rstrip(";")

    if not sql.upper().lstrip().startswith("SELECT"):
        raise SQLValidationError("Only SELECT statements are allowed.")

    if _BANNED.search(sql):
        raise SQLValidationError("SQL contains a disallowed keyword.")

    if _SEMICOLON_MID.search(sql):
        raise SQLValidationError("Only a single SQL statement is allowed.")

    refs = {m.group(1) or m.group(2) for m in _TABLE_REF.finditer(sql)}
    bad = {r for r in refs if r and r.lower() not in VIEW_ALLOWLIST}
    if bad:
        raise SQLValidationError(
            f"Query references tables outside the allowed views: {', '.join(sorted(bad))}. "
            f"Allowed: {', '.join(sorted(VIEW_ALLOWLIST))}."
        )

    if not _LIMIT.search(sql):
        sql = f"{sql} LIMIT {MAX_ROWS}"

    return sql
