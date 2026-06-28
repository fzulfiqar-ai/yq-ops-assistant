"""MCP server — exposes YQ Bahrain's ops brain to any MCP client (Claude Desktop, Cursor).

Run locally:  python -m app.mcp_server   (needs `pip install mcp`)
It reuses the SAME read-only data layer + agents as the portal. Tool results can contain
commercial data, so connect this only as the data owner (it's a local stdio server you run
yourself; it is NOT exposed publicly).

Claude Desktop config (claude_desktop_config.json):
  { "mcpServers": { "yq-ops": { "command": "python", "args": ["-m", "app.mcp_server"],
    "cwd": "<this project path>" } } }
"""
from __future__ import annotations

import os
from functools import lru_cache

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    raise SystemExit("MCP SDK not installed. Run:  pip install mcp")

mcp = FastMCP("YQ Bahrain Ops")

# MCP tool results leave to an external client (Claude Desktop / Anthropic), so by default we
# tokenize customer/account names (CUST_n) before returning them — same posture as the portal's
# external LLM calls. Set MCP_REDACT=0 to disable (you accept the data egress on your own machine).
_REDACT = os.getenv("MCP_REDACT", "1").strip().lower() not in ("0", "false", "no", "")


@lru_cache(maxsize=1)
def _customer_names() -> tuple[str, ...]:
    from app.ai import exec_sql
    names: set[str] = set()
    for sql in ("SELECT DISTINCT customer_name AS n FROM v_sales WHERE customer_name IS NOT NULL LIMIT 3000",
                "SELECT DISTINCT account AS n FROM v_receivables LIMIT 3000"):
        try:
            for r in exec_sql(sql):
                v = str(r.get("n") or "").strip()
                if v and "cash customer" not in v.lower():
                    names.add(v)
        except Exception:  # noqa: BLE001
            pass
    return tuple(sorted(names, key=len, reverse=True))  # longest first → replace full names first


def _redact(obj):
    """Tokenize known customer/account names anywhere in a tool result (string/list/dict)."""
    if not _REDACT:
        return obj
    if isinstance(obj, str):
        for i, name in enumerate(_customer_names()):
            if name and name in obj:
                obj = obj.replace(name, f"CUST_{i}")
        return obj
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    return obj


@mcp.tool()
def ask_business(question: str) -> str:
    """Answer a question from YQ Bahrain's live data — sales, salesmen, stock, margins,
    product prices & cost, vendors, receivables. Plain English in, answer out."""
    from app.ai import ask
    return _redact(ask(question).get("reply", ""))


@mcp.tool()
def run_business_agent(name: str) -> dict:
    """Run one YQ AI agent and return its briefing. Names: collections, inventory, margin,
    sales_insights, sales_push, customer_health, cashflow, anomaly, inventory_aging,
    salesman_performance, procurement, fraud, trend, marketing, catalog_watch."""
    from app.agents import run_agent
    return _redact(run_agent(name))


@mcp.tool()
def list_business_agents() -> list:
    """List the available YQ AI agents (name + description)."""
    from app.agents import list_agents
    return list_agents()


@mcp.tool()
def search_data(sql: str) -> list:
    """Run a validated read-only SELECT against the YQ semantic views (e.g. v_price_list,
    v_product_economics, v_purchase_history, v_sales_by_channel)."""
    from app.ai import exec_sql
    from app.sql_validator import validate
    return _redact(exec_sql(validate(sql)))


@mcp.tool()
def recall_memory(query: str) -> str:
    """Recall relevant past briefings, decisions, and business glossary from memory."""
    from app.knowledge import recall_text
    return _redact(recall_text(query, k=5))


if __name__ == "__main__":
    mcp.run()
