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

v_sales               Sales lines (one row per invoice line)
  sale_date(date), customer_name, salesman_resolved, channel('B2C'|'B2B'),
  is_cash_customer(bool), item_name, sku_code, category_name, quantity,
  revenue_bhd[GROSS, VAT-incl — USE THIS FOR REVENUE], net_bhd[ex-VAT], gross_bhd

v_sales_by_period     Monthly totals (period_month = 1st of month)
  period_month, order_count, total_qty, gross_bhd[revenue], net_revenue_bhd[ex-VAT]

v_sales_by_salesman   Per-salesman totals
  salesman, orders, qty, revenue_bhd[gross], net_bhd

v_sales_by_channel    B2C (Causeway/YQ Roadshow = retail) vs B2B (wholesale)
  channel, orders, qty, revenue_bhd[gross], net_bhd

v_top_customers       Customer revenue ranking (gross_bhd = gross revenue)
  customer_name, order_count, total_qty, gross_bhd, total_revenue_bhd, last_order_date

v_current_stock       Current on-hand stock per item+warehouse (warehouse = salesman/location)
  item_name, warehouse_name, balance_qty, balance_value_bhd, avg_rate_bhd,
  as_of_date, category_name

v_stock_health        Velocity-aware stock signal (one row per item)
  item_name, current_stock, sold_30d, sold_90d, avg_daily, days_cover,
  suggested_reorder_qty,
  status('urgent_out_of_stock'|'low_stock'|'dead_stock'|'overstock'|'healthy')

v_inventory_aging     On-hand stock by idleness
  item_name, current_stock, stock_value, last_sold, days_since_sale

v_product_margin      Profitability per item, latest period (Focus COGS basis)
  item_name, category_name, net_amount_bhd, cogs_bhd, gross_profit_bhd,
  gp_margin_pct[below cost if < 0], np_margin_pct

v_receivables         Trade-debtor balances + ageing (positive = owes YQ)
  account, group_name, outstanding_bhd, overdue_bhd[31+ days], over_90_bhd,
  b_0_30, b_31_60, b_61_90, b_91_120, b_121_150, b_151_180, b_181_210, b_over_210

RULES:
- Return ONLY the SQL SELECT — no markdown, no explanation, no code blocks.
- Use ILIKE '%value%' for text searches (case-insensitive).
- Always include LIMIT (max 200). Default LIMIT 50.
- Currency is BHD. Revenue = revenue_bhd (gross). Receivables total = SUM(outstanding_bhd).
- DATA IS HISTORICAL: "today"/"this month"/"latest" must anchor to the data's last day:
  use (SELECT MAX(sale_date) FROM v_sales), NOT CURRENT_DATE.
  This month = sale_date >= DATE_TRUNC('month',(SELECT MAX(sale_date) FROM v_sales)).
- For "customers" exclude the walk-in bucket: customer_name NOT ILIKE 'cash customer%'.
- Below cost = v_product_margin WHERE gp_margin_pct < 0. Low stock = v_stock_health
  WHERE status IN ('urgent_out_of_stock','low_stock').
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


def _llm_sql(question: str, model_name: str | None = None) -> str:
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
    raw = chat(msgs, tier=2, temperature=0.1, max_tokens=400, model_name=model_name)
    raw = re.sub(r"```\w*\s*", "", raw, flags=re.IGNORECASE)
    return raw.replace("```", "").strip().rstrip(";")


def _llm_answer(question: str, rows: list[dict], redactor: Redactor, model_name: str | None = None) -> str:
    """Format SQL result rows into a concise business answer via LLM."""
    if not rows:
        return (
            "I checked the records but didn't find anything matching that. The data runs "
            "to **22 Jun 2026** — try rephrasing, or ask about sales, a salesman, stock "
            "health, margins, or who owes us money."
        )
    row_text = json.dumps(rows[:50], indent=2, default=str)
    redacted_rows = redactor.redact(row_text, [])
    msgs = [
        {
            "role": "system",
            "content": (
                "You are a sharp, confident business analyst for YQ Bahrain Mobile Accessories. "
                "Answer the question directly and concisely from the data, then add ONE short "
                "insight line ('Insight: …') only if it's genuinely useful. "
                "Format BHD as 'BHD X,XXX.XX'. Use bullet points for lists. Bold key numbers. "
                "Never invent data not present in the result. Be crisp — no filler."
            ),
        },
        {
            "role": "user",
            "content": f"Question: {question}\n\nData:\n{redacted_rows}",
        },
    ]
    answer = chat(msgs, tier=2, temperature=0.3, max_tokens=600, model_name=model_name)
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


_GREETING = re.compile(r"^\s*(hi|hey+|hello|h?ello|yo|salam|salaam|hola|good\s*(morning|afternoon|evening)|howdy)\b", re.I)
_THANKS = re.compile(r"\b(thanks|thank you|thx|shukran|appreciate|great|awesome|perfect|nice work)\b", re.I)
_IDENTITY = re.compile(r"\b(who are you|what are you|your name|what can you do|help me|what do you do|capabilities|how do you work)\b", re.I)


def _smalltalk(question: str) -> str | None:
    """Respond to greetings / identity / thanks conversationally — no SQL.

    Keeps the assistant feeling intelligent and human instead of returning
    'no data' to a simple 'hi'."""
    q = question.strip()
    if len(q) <= 60 and _GREETING.search(q) and not re.search(r"\d|sale|stock|revenue|customer|margin|owe|debtor", q, re.I):
        return (
            "Hello — I'm your YQ Bahrain operations analyst. I have your live "
            "Mobile Accessories data (as of **22 Jun 2026**): sales, salesmen, stock, "
            "margins and receivables.\n\n"
            "Try asking me:\n"
            "- **Who's our top salesman this month?**\n"
            "- **Which fast movers are out of stock?**\n"
            "- **Who owes us the most, and how overdue?**\n"
            "- **What's our gross margin?**\n\n"
            "What would you like to know?"
        )
    if _IDENTITY.search(q):
        return (
            "I'm the YQ Bahrain AI analyst. I read your live Focus ERP data and answer "
            "in plain English — sales & revenue (gross and ex-VAT), salesman and B2C/B2B "
            "performance, stock health and reorder needs, product margins, and customer "
            "receivables with ageing. Ask me anything operational and I'll pull the numbers "
            "and the insight. What shall we look at?"
        )
    if _THANKS.search(q) and len(q) <= 40:
        return "Anytime. Ask me anything else about sales, stock, margins or receivables."
    return None


def ask(question: str, user_email: str = "system", model_name: str | None = None) -> dict[str, Any]:
    """Answer a natural-language question about YQ Bahrain operations.

    Returns dict with: reply, sql_used, cached, row_count
    """
    client = get_client()
    key = _cache_key(question)

    # 0 — conversational (greetings / identity / thanks) — never run SQL on these
    chat_reply = _smalltalk(question)
    if chat_reply:
        return {"reply": chat_reply, "sql_used": "", "cached": False, "row_count": 0}

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
            raw_sql = _llm_sql(redacted_q, model_name)
            try:
                sql_used = validate(raw_sql)
            except SQLValidationError as e:
                return {"reply": f"I couldn't generate a valid query. ({e})", "sql_used": raw_sql, "cached": False, "row_count": 0}
            rows = exec_sql(sql_used)
            reply = _llm_answer(question, rows, redactor, model_name)

    except Exception as e:
        log.exception("ask() error: %s", e)
        return {"reply": "Something went wrong. Please try rephrasing your question.", "sql_used": sql_used, "cached": False, "row_count": 0}

    _store_cache(client, key, question, reply, sql_used, rows)
    return {"reply": reply, "sql_used": sql_used, "cached": False, "row_count": len(rows)}
