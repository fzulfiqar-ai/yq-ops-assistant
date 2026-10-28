# CLAUDE.md — standing orders (read at the start of every session)

**Project:** YQ Bahrain W.L.L internal AI operations assistant. Mobile accessories (and any
future product line — the system is product-agnostic). Internal tool to make ops work faster
and demonstrate capability to management.

**NO Zulfi branding, code, or infrastructure anywhere. This is YQ Bahrain only.**

---

## Stack (FINAL — no substitutes)
- **Supabase** — Postgres + Auth + RLS (the database + auth).
- **FastAPI** backend on **Render** via **Docker** (`render.yaml` and the `Dockerfile`: the free plan, one uvicorn
  worker, the Singapore region next to the database; served as `https://api.yqmarketplace.com`). Deploys are
  deliberate, never on push: `python -m scripts.render_deploy` (docs/RELEASE.md, docs/DEPLOY_RENDER.md).
- **Web** — the React/Vite portal (`ops.yqmarketplace.com`) and the YQ Marketplace (`yqmarketplace.com`) are served by
  **Cloudflare Workers** static assets (`web/wrangler.portal.jsonc`, `web/wrangler.market.jsonc`), deployed by
  `.github/workflows/cf-deploy.yml` after CI passes or by `python -m scripts.deploy_cf`; Vercel only redirects the old
  addresses. The **Streamlit** dashboard (`dashboard/ui.py`) is the local dashboard; its libraries are in
  `requirements-dev.txt`, not in the API image.
- **Scheduling** — the `yq-keepwarm` **Cloudflare Worker** (cron triggers) keeps the API warm, calls its scheduler
  endpoints and, every 10 minutes, reads Supabase's `keepalive_ping` with the publishable key so the free project never
  pauses (a failed read alerts by Telegram, at most once every 6 hours). `.github/workflows/keepalive.yml` stays as a
  backstop until the repository is made private. **n8n** (workflows in `n8n_workflows/`) is not hosted at present
  (docs/DEPLOY_RENDER.md).

## AI (NO Anthropic, NO paid APIs)
All model calls go through `app/llm_router.py` — a rotation of **FREE** providers, each joining when its key is set.
Re-measured 28-Oct-2026: Groq (qwen3.8-27b, gpt-oss-120b/20b; Whisper for speech) is the backbone, then Mistral and
Gemini; Cerebras, OpenRouter, NVIDIA and SambaNova only when their keys answer. Free model IDs move: run
`python -m scripts.probe_models` (read-only) after any "All AI providers are busy" and before a release.
- On **401 / 402 / 403 / quota**: bench that provider (until UTC midnight for quota, else 5 minutes) and move on.
- On **429**: `chat()` waits `2^n` seconds and retries the **same** provider (callers cap it); `chat_json()` and
  `chat_tools()` move on at once. The SDK's own retries are off (`max_retries=0`).
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

**The `public` schema is closed to the publishable key (16-Sep-2026).** `anon`/`authenticated`
hold no grants on any table, view, sequence or function except SELECT on the empty
`keepalive_ping`. Supabase auto-grants new objects to those roles by default and views bypass
RLS, so after any migration run `python -m scripts.audit_grants` (exit 0 = clean). Never set
`security_invoker` on views (it would break the `yq_readonly` RPC path). See docs/MIGRATIONS.md.

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

## Database migrations — read docs/MIGRATIONS.md before touching any view
`scripts/views.sql` is a GENERATED, bootstrap-only baseline for an empty database. Never apply it
to the live DB — five of its views are superseded by `scripts/*_migration.sql`. Three
(`v_sales`, `v_receivables`, `v_low_stock`) fail loudly if re-applied; **`v_current_stock` and
`v_product_margin` regress SILENTLY** (identical columns, different source/filter — the old stock
basis was ~8.6× overstated). It carries a guard that refuses to run against a migrated DB, but
the guard cannot protect a copy-pasted fragment. To change a view, edit its canonical migration file
(see the ownership table in `docs/MIGRATIONS.md`). Never add `CASCADE` to a `DROP VIEW` to clear
a dependency error — that deletes the enriched views. Edit `VIEWS_SQL` in
`scripts/migrate_views.py`, never `scripts/views.sql` directly.

## Data freshness (no Focus API)
Focus ERP (FocusX) has no API. Data comes from Excel exports the owner drops into a dated folder
(`Uplaods/<ddmmyy>/`) and loads with `/yq-load` (checked, confirmed at a Windows box, then loaded by
`scripts/yq_load_go.py`; see docs/OPS_SKILLS.md) or through the portal's Data page. An automated feed
(scheduled export, e-mail-to-ingest) is roadmap only.
True real-time would need a read-only SQL link to the Focus DB (needs IT) — roadmap only.

## YQ Shop (15-Sep-2026)
The public catalog link `/c/{token}` is an ordering system: `app/shop.py` (payload, ONE pricing engine for quote+order,
orders, salesmen, discount rules, margins, analytics), `app/shop_notify.py`, `app/shop_api.py` (routes registered from
main.py), `scripts/shop_migration.sql`, `tests/test_shop.py`, `web/src/pages/shop/*`. Contract + setup: `docs/SHOP.md`.
Rules: never expose stock quantities or costs (status only); no business numbers in code (`app_settings` `shop_*` keys);
the shop never mutates stock (Focus is the system of record); salesman contact details are never committed (public repo).
Owner prices come from the price book only — to load the owner's workbook prices use `scripts/import_workbook_prices.py`
(adds dated dealer rows; never overwrites).

## YQ Marketplace (16-Sep-2026)
The shop, enhanced, as the merchant front door: `web/src/MarketApp.tsx` + `web/src/market/**` built with
`VITE_APP=market` as a second Cloudflare Workers site; token-less API `/public/market*`, `/public/rep/{slug}`;
merchant identity (`shop_customers`), attribution (`shop.resolve_salesman`), lifecycle
Received→Confirmed→Preparing→On the way→Delivered, assignment queue, storekeeper pick list. Read
`docs/MARKETPLACE.md` and `docs/SHOP.md § Marketplace` before touching any of it. After every migration
run `python -m scripts.audit_grants` (the public schema must stay closed to the anon key).
