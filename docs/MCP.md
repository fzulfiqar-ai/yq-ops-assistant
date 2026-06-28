# Connecting your local Claude to the YQ brain (MCP)

MCP lets a powerful local model (Claude Desktop / Cursor) drive YQ's own read-only data layer +
agents — your "heavyweight analyst" — at **zero paid API cost in the deployed product** (the
production app still uses only the free LLM rotation). This is a **local, owner-only** setup: the
servers run on your PC and talk to Supabase; nothing is exposed publicly.

There are two MCP servers worth adding:

| Server | What it gives Claude | Source |
|---|---|---|
| **YQ Ops** (`app/mcp_server.py`) | `ask_business`, `run_business_agent`, `search_data` (validated read-only SQL on the semantic views), `recall_memory`, `list_business_agents` | this repo |
| **Supabase MCP** (official) | direct, read-only SQL exploration of the database beyond the curated views | `@supabase/mcp-server-supabase` |

## 1. YQ Ops server (this repo)
Already built + hardened (customer names are tokenised in tool results — `MCP_REDACT=1` default).

```bash
pip install mcp        # one-time
```

Claude Desktop → Settings → Developer → Edit Config (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "yq-ops": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\Users\\fahmed\\OneDrive - YqBahrain\\Desktop\\YQ Bahrain Mobile Accessories",
      "env": { "MCP_REDACT": "1" }
    }
  }
}
```
Restart Claude Desktop. You'll see the YQ tools — ask e.g. *"run the salesman_stock_recon agent"* or
*"search_data: SELECT category_name, SUM(revenue_bhd) FROM v_sales_by_category GROUP BY 1"*.

- **Redaction:** `MCP_REDACT=1` (default) tokenises customer names before they leave to Claude. Set
  `MCP_REDACT=0` only if you accept the data egress on your own machine.
- **Read-only:** `search_data` runs through the same `sql_validator` (SELECT-only, view allowlist) and
  the `run_readonly_query` RPC (service-role only). It cannot write.

## 2. Supabase MCP (official, optional)
For free-form SQL exploration. **Use a read-only key/role.**

```json
{
  "mcpServers": {
    "supabase": {
      "command": "npx",
      "args": ["-y", "@supabase/mcp-server-supabase@latest", "--read-only",
               "--project-ref", "<your-project-ref>"],
      "env": { "SUPABASE_ACCESS_TOKEN": "<a personal access token>" }
    }
  }
}
```
- Pass `--read-only` so Claude can only SELECT.
- Prefer scoping to the project; rotate the token if exposed.
- This bypasses the curated views — handy for ad-hoc analysis, but the YQ Ops server is safer for
  day-to-day (it's redacted + view-scoped).

## Why this is the "more powerful brain" — for free
`docs/CLAUDE.md` allows the Claude CLI/Desktop **as a build/owner tool** (never embedded in prod).
So the owner gets a top-tier model over the live business data via MCP, while the shipped product
stays on the free rotation. No paid API in production, no new PII surface beyond your own machine.
