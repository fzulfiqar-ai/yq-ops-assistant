# CLAUDE.md — standing orders (read at the start of every session)

**Project:** YQ Bahrain W.L.L internal AI operations assistant. Mobile accessories (and any
future product line — the system is product-agnostic). Internal tool to make ops work faster
and demonstrate capability to management.

**NO Zulfi branding, code, or infrastructure anywhere. This is YQ Bahrain only.**

---

## Stack (FINAL — no substitutes)
- **Supabase** — Postgres + Auth + RLS (the database + auth).
- **FastAPI** backend on **Railway** via **Docker**.
- **Streamlit** dashboard on **Streamlit Cloud**.
- **n8n** self-hosted for orchestration / webhooks / scheduling.

## AI (NO Anthropic, NO paid APIs)
All model calls go through `app/llm_router.py` — a rotation of ~10 **FREE** providers:
GLM-5.2 via OpenRouter, Groq, Cerebras, more OpenRouter free models, Scaleway, Together.
- On **403 / quota**: skip that provider immediately.
- On **429**: wait `2^n` seconds and retry the **same** provider.
- **Tier 1** (parsing / classification / intent): use the full rotation.
- **Tier 2** (final answers): use the strongest free models first.
- **Redaction is mandatory:** customer / account names are tokenized before any external call
  and mapped back locally. Only redacted text leaves the network.
- Reliability aids: deterministic templates for the top ~20 questions (no LLM, no quota burn),
  7-day `query_cache`, and a provider health / circuit-breaker log.

## BANNED
- No Claude CLI subprocess in production. The deployed app calls APIs over HTTPS; the Claude
  CLI is only a build tool on the developer machine.
- No hardcoded product categories — categories come from the `categories` table.
- No real secrets in code — only `.env.example` with placeholders.
- No Zulfi anything.

## Governance
Customer names, prices, and margins are YQ Bahrain commercial data. Keep prompts to the free
routers factual; redact PII you don't need to send. Every data endpoint except `/health`
requires the auth dependency (`app/auth.py`). Generated SQL is read-only, SELECT-only,
restricted to the curated view allowlist, with a forced `LIMIT`. Every ask / action / alert is
recorded in `audit_log`.

## How to build
Build in **phases**. Do one phase, then **STOP** and tell the user exactly how to test it.
- Phase 0: security spine + ingestion (this).  • Phase 0.5: semantic views + guardrails + eval.
- Phase 1: `/ask` + dashboard.  • Phase 2: `/action` + approvals.  • Phase 2.5: proactive
  agent (digests/alerts).  • Phase 3: webhooks + automated ingest + n8n MCP/skill.
- Roadmap Phases 4–6: see ROADMAP.md — build only when explicitly asked.

## The 7 data rules (carried over — enforce in every script and query)
1. **Cost source of truth** = `purchase_costs.landed_cost_bhd` (from the pricing sheet). Never
   use `stock_movements.received_rate_bhd` / `avg_rate_bhd` for pricing (ERP valuation drifts).
2. `purchase_costs` is **versioned** by `effective_date`; same SKU+date = `ON CONFLICT DO
   NOTHING`; a new date = a new row. "Latest cost" = `MAX(id)` per SKU.
3. Sales import **hard-fails** if voucher↔invoice match rate < **80%** (`ValueError`).
4. Verified join: `order_lines.invoice_no = orders.invoice_no` on exact string equality
   (`'SI : N'`, spaces around colon). No stripping needed.
5. Purchase history filter: `voucher_type = 'Material Receipt Note'` only.
6. Current stock = `MAX(id)` per SKU, **not** `MAX(date)` (same-date ties are non-deterministic).
7. Reorganize by **copy**, never move. Originals are deleted by the user after verifying a
   clean import.

## Data freshness (no Focus API)
Focus ERP (FocusX) has no API. Data comes from Excel exports dropped into `Focus ERP Data/`.
v1 = manual export + `scripts/ingest.py`. Phase 3 = n8n email-to-ingest or watched-folder.
True real-time would need a read-only SQL link to the Focus DB (needs IT) — roadmap only.
