# YQ Bahrain — Internal AI Operations Assistant

Internal tool for YQ Bahrain W.L.L (mobile accessories) that answers operational questions
(sales, stock, margins, customers, receivables) over Focus ERP data, and proactively pushes
alerts. **Free stack, automated, product-agnostic.** No Zulfi anything.

> Source of truth for design: [docs/CLAUDE.md](docs/CLAUDE.md) (standing orders + 7 data
> rules), [docs/SCHEMA.md](docs/SCHEMA.md) (file→table mapping), [docs/ROADMAP.md](docs/ROADMAP.md).

## Stack
Supabase (Postgres + Auth + RLS) · FastAPI on Railway (Docker) · Streamlit Cloud dashboard ·
n8n self-hosted (orchestration/webhooks/scheduling). AI = `app/llm_router.py`, a rotation of
~10 free providers, with redaction before any external call.

## Repo layout
```
Focus ERP Data/      # the 8 Focus xlsx exports — SINGLE source of truth (gitignored)
data/clean/          # ingest.py output (gitignored)
roadmap_sources/     # vendor/pricing/sourcing docs — Phase 4+ only (gitignored)
docs/                # CLAUDE.md, SCHEMA.md, ROADMAP.md
app/                 # config, database, auth, llm_router, main (FastAPI)
scripts/             # ingest, migrate_supabase, load_supabase, reconcile_products
n8n_workflows/       # automation (added from Phase 2)
```

## Phase 0 — what is built now
Security spine + ingestion only. The API exposes **only** `/health`. Data endpoints arrive in
Phase 1.

### 1. Install
```bash
pip install -r requirements.txt
cp .env.example .env     # then fill in real values
```

### 2. Ingest Focus exports (no DB needed)
Drop the Focus xlsx into `Focus ERP Data/`, then:
```bash
python scripts/ingest.py
```
Writes cleaned CSVs to `data/clean/` and prints a data-quality report (row counts, the
voucher↔invoice join match %, dropped totals). Ingestion **hard-fails if the sales join is
below 80%** (data rule 3).

### 3. Create the database
In Supabase, **first** create your admin row by hand (the migration never overwrites it):
```sql
insert into user_roles (email, role) values ('furqanahmed223@gmail.com', 'admin');
```
Then:
```bash
python scripts/migrate_supabase.py      # idempotent; creates tables, RLS, views
python scripts/reconcile_products.py    # builds product_aliases + match-rate report
python scripts/load_supabase.py         # idempotent upserts of data/clean/ → Supabase
```

### 4. Run the API locally
```bash
uvicorn app.main:app --reload
# GET http://127.0.0.1:8000/health  -> {"status":"ok"}
```

## Deploy to Railway
1. Push this repo to GitHub (`.gitignore` keeps data + secrets out).
2. Railway → New Project → Deploy from repo. It uses the `Dockerfile`.
3. Add env vars from `.env.example` in Railway → Variables.
4. Railway sets `$PORT`; the container binds it automatically.
5. Test: `GET https://<your-app>.up.railway.app/health`.

> Free-tier note: Railway/Streamlit free tiers sleep and free LLMs have daily caps — fine for
> build + demo. If always-on uptime is later needed, the cheapest reliable step is a ~$5–10/mo
> host for the API + n8n. No spend in v1.

## Adding a NEW product category (zero code changes)
The system is **product-agnostic**. Categories live in the `categories` table and every product
links to one via `products.category_id`. No query or code hardcodes a category name. To add an
entirely new product line (e.g. phones, not just accessories):
```sql
insert into categories (name) values ('Mobile Phones');
```
Then load the new products and their sales/stock/pricing exactly as before
(`ingest.py` → `load_supabase.py`) and assign `category_id`. **No code changes are required** —
ingestion maps by Focus column names, the schema has no category enum, and all reporting groups
by whatever categories exist in the table.
