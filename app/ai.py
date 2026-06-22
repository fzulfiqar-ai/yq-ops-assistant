"""Phase 1 AI query engine.

Flow: cache → template → LLM text-to-SQL → validate → RPC execute → LLM answer → cache
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import get_client
from app.llm_router import Redactor, chat
from app.sql_validator import SQLValidationError, validate
from app.templates import match as template_match

log = logging.getLogger(__name__)

_VIEW_SCHEMA = """
DATABASE VIEWS — query ONLY these, never raw tables:

v_sales               All sales lines with product + customer info
  sale_date(date), customer_name, salesman_resolved, item_name, sku_code,
  category_name, quantity, gross_bhd, total_amount_bhd[use for revenue],
  narration, warehouse_name

v_current_stock       Latest stock balance per item+warehouse (MAX-id rule)
  item_name, warehouse_name, balance_qty, avg_rate_bhd, as_of_date,
  sku_code, product_name, category_name, is_low_stock(bool)

v_product_margin      Profitability snapshot (Focus COGS basis — never use stock valuation)
  item_name, report_date, cogs_bhd, gross_profit_bhd, gp_margin_pct,
  net_profit_bhd, np_margin_pct, list_price_bhd, sku_code, category_name

v_receivables         Outstanding debtor balances (positive = customer owes YQ)
  account, last_entry_date, outstanding_bhd, days_outstanding, salesman

v_top_customers       Customer revenue ranking
  customer_name, order_count, total_revenue_bhd, total_qty, last_order_date

v_sales_by_period     Monthly aggregates (period_month = 1st day of month)
  period_month, order_count, net_revenue_bhd, total_qty, gross_bhd

v_low_stock           Items with balance_qty <= 10
  item_name, warehouse_name, balance_qty, as_of_date, sku_code, category_name

shipments             Goods received (Material Receipt Notes only)
  received_date, mrn_no, item_name, received_qty, received_rate_bhd, warehouse_name

RULES:
- Return ONLY the SQL SELECT — no markdown, no explanation, no code blocks.
- Use ILIKE '%value%' for all text searches (case-insensitive).
- Always include LIMIT (max 200). Default LIMIT 50.
- Currency is BHD (numeric).
- Current month: DATE_TRUNC('month', CURRENT_DATE)
- Today: CURRENT_DATE
- This week: DATE_TRUNC('week', CURRENT_DATE)
"""


def _cache_key(q: str) -> str:
    return hashlib.md5(q.strip().lower().encode()).hexdigest()


def _check_cache(client, key: str) -> dict | None:
    try:
        r = (
            client.table("query_cache")
            .select("reply,sql_used")
            .eq("query_hash", key)
            .gt("expires_at", datetime.now(timezone.utc).isoformat())
            .limit(1)
            .execute()
        )
        return r.data[0] if r.data else None
    except Exception:
        return None


def _store_cache(client, key: str, question: str, reply: str, sql: str, rows: list) -> None:
    try:
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        client.table("query_cache").upsert(
            {
                "query_hash": key,
                "question": question,
                "reply": reply,
                "sql_used": sql,
                "raw_data": json.dumps(rows[:100], default=str),
                "expires_at": expires,
            },
            on_conflict="query_hash",
        ).execute()
    except Exception as e:
        log.warning("cache write failed: %s", e)


def exec_sql(sql: str) -> list[dict]:
    """Execute pre-validated SQL via Supabase RPC and return rows."""
    client = get_client()
    r = client.rpc("run_readonly_query", {"sql_text": sql}).execute()
    data = r.data
    if isinstance(data, str):
        data = json.loads(data)
    return data or []


def _llm_sql(question: str) -> str:
    """Call LLM to generate SQL for the question. Returns raw SQL."""
    msgs = [
        {
            "role": "system",
            "content": (
                "You are a PostgreSQL query generator for YQ Bahrain Mobile Accessories. "
                "Generate ONE SELECT query using ONLY the views listed. "
                "Return ONLY the SQL — no markdown fences, no explanation.\n\n"
                + _VIEW_SCHEMA
            ),
        },
        {"role": "user", "content": question},
    ]
    raw = chat(msgs, tier=2, temperature=0.1, max_tokens=400)
    raw = re.sub(r"```\w*\s*", "", raw, flags=re.IGNORECASE)
    return raw.replace("```", "").strip().rstrip(";")


def _llm_answer(question: str, rows: list[dict], redactor: Redactor) -> str:
    """Format SQL result rows into a concise business answer via LLM."""
    if not rows:
        return "No data found for your question based on current records."
    row_text = json.dumps(rows[:50], indent=2, default=str)
    redacted_rows = redactor.redact(row_text, [])
    msgs = [
        {
            "role": "system",
            "content": (
                "You are a concise business analyst for YQ Bahrain Mobile Accessories. "
                "Answer the question directly using the data provided. "
                "Format BHD values as 'BHD X,XXX.XX'. Use bullet points for lists. "
                "Never invent data not present in the result."
            ),
        },
        {
            "role": "user",
            "content": f"Question: {question}\n\nData:\n{redacted_rows}",
        },
    ]
    answer = chat(msgs, tier=2, temperature=0.3, max_tokens=600)
    return redactor.restore(answer)


def _fmt_template(label: str, rows: list[dict]) -> str:
    """Format template SQL result as clean markdown — no LLM needed."""
    if not rows:
        return f"**{label}** — no data found."
    if len(rows) == 1:
        r = rows[0]
        parts = [f"**{label}**", ""]
        for k, v in r.items():
            if v is None:
                continue
            lbl = k.replace("_", " ").title()
            val = f"BHD {float(v):,.2f}" if isinstance(v, (int, float)) and "bhd" in k.lower() else str(v)
            parts.append(f"- **{lbl}:** {val}")
        return "\n".join(parts)
    lines = [f"**{label}** — {len(rows)} results", ""]
    key0 = list(rows[0].keys())[0]
    key1 = list(rows[0].keys())[1] if len(rows[0]) > 1 else None
    for i, r in enumerate(rows[:25], 1):
        name = str(r.get(key0, ""))[:60]
        suffix = ""
        if key1 and r.get(key1) is not None:
            v = r[key1]
            suffix = f" — **BHD {float(v):,.2f}**" if isinstance(v, (int, float)) and "bhd" in key1.lower() else f" — {v}"
        lines.append(f"{i}. {name}{suffix}")
    if len(rows) > 25:
        lines.append(f"_… and {len(rows) - 25} more items_")
    return "\n".join(lines)


def ask(question: str, user_email: str = "system") -> dict[str, Any]:
    """Answer a natural-language question about YQ Bahrain operations.

    Returns dict with: reply, sql_used, cached, row_count
    """
    client = get_client()
    key = _cache_key(question)

    # 1 — cache hit
    hit = _check_cache(client, key)
    if hit:
        return {"reply": hit["reply"], "sql_used": hit.get("sql_used", ""), "cached": True, "row_count": 0}

    redactor = Redactor()
    sql_used = ""
    rows: list[dict] = []

    try:
        # 2 — deterministic template
        tmpl = template_match(question)
        if tmpl:
            label, raw_sql = tmpl
            sql_used = validate(raw_sql)
            rows = exec_sql(sql_used)
            reply = _fmt_template(label, rows)

        else:
            # 3 — LLM text-to-SQL
            redacted_q = redactor.redact(question, [])
            raw_sql = _llm_sql(redacted_q)
            try:
                sql_used = validate(raw_sql)
            except SQLValidationError as e:
                return {"reply": f"I couldn't generate a valid query. ({e})", "sql_used": raw_sql, "cached": False, "row_count": 0}
            rows = exec_sql(sql_used)
            reply = _llm_answer(question, rows, redactor)

    except Exception as e:
        log.exception("ask() error: %s", e)
        return {"reply": "Something went wrong. Please try rephrasing your question.", "sql_used": sql_used, "cached": False, "row_count": 0}

    _store_cache(client, key, question, reply, sql_used, rows)
    return {"reply": reply, "sql_used": sql_used, "cached": False, "row_count": len(rows)}
