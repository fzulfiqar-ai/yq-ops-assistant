"""Tools beyond read-only SQL. Each is best-effort and gated on config. Reused by the
vendor-sourcing agent and (optionally) the MCP server. Currently: web_search (Tavily free).
"""
from __future__ import annotations

import logging

import requests

from app.config import settings

log = logging.getLogger(__name__)


def web_search_enabled() -> bool:
    return bool(settings.tavily_api_key)


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Free web search via Tavily. Returns [{title, url, content}]; [] if not configured."""
    if not settings.tavily_api_key:
        return []
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": settings.tavily_api_key, "query": query,
                  "max_results": max_results, "search_depth": "basic"},
            timeout=15,
        )
        if r.ok:
            return [{"title": x.get("title"), "url": x.get("url"),
                     "content": (x.get("content") or "")[:500]}
                    for x in r.json().get("results", [])]
        log.warning("tavily %s: %s", r.status_code, r.text[:120])
    except Exception as e:  # noqa: BLE001
        log.warning("web_search failed: %s", e)
    return []


# ── Function-calling tool layer (bounded tool-calling for capable models) ──────
# Every tool RESULT is redacted before it goes back to the model (names tokenised CUST_n);
# recall_memory is intentionally NOT exposed here (its briefings can carry un-mappable names).

TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "search_data",
        "description": ("Run ONE read-only SQL SELECT against YQ's semantic views and get rows as "
                        "JSON. Views: v_sales, v_sales_by_period, v_sales_by_salesman, v_sales_by_channel, "
                        "v_sales_by_category, v_top_customers, v_current_stock, v_stock_health, "
                        "v_inventory_aging, v_product_margin, v_product_economics, v_price_list, "
                        "v_purchase_history, v_cost_change, v_receivables, v_stock_transfers, "
                        "v_salesman_stock_recon. Anchor 'today/this month' to (SELECT MAX(sale_date) FROM v_sales)."),
        "parameters": {"type": "object", "properties": {
            "sql": {"type": "string", "description": "A single SELECT statement against the allowed views."}},
            "required": ["sql"]}}},
    {"type": "function", "function": {
        "name": "run_agent",
        "description": ("Run a YQ specialist agent and get its briefing summary + key rows. Names: "
                        "collections, cashflow, credit_exposure, working_capital, inventory, inventory_aging, "
                        "purchase_insights, demand_forecast, deadstock_liquidation, abc_xyz, sales_insights, "
                        "sales_push, customer_health, salesman_performance, trend, marketing, winback, "
                        "pricing_optimization, risk_watch, salesman_stock_recon."),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "The agent name."}}, "required": ["name"]}}},
]


def dispatch(name: str, args: dict, redactor) -> str:
    """Execute a tool and return its result as a redacted string for the model. Never raises."""
    import json
    try:
        if name == "search_data":
            from app.ai import _entity_names, exec_sql
            from app.sql_validator import validate
            rows = exec_sql(validate(str(args.get("sql", ""))))
            return redactor.redact(json.dumps(rows[:50], default=str), _entity_names(rows)) if rows else "[] (no rows)"
        if name == "run_agent":
            from app.agents import AGENTS, run_agent
            from app.ai import _entity_names
            n = str(args.get("name", ""))
            if n not in AGENTS:
                return f"Unknown agent '{n}'."
            res = run_agent(n)
            lists = [v for v in res.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
            compact = {"summary": res.get("summary"), "rows": (lists[0][:10] if lists else [])}
            return redactor.redact(json.dumps(compact, default=str), _entity_names(compact["rows"]))
        return f"Unknown tool '{name}'."
    except Exception as e:  # noqa: BLE001
        return f"Tool error: {type(e).__name__}: {e}"
