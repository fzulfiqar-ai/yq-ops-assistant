# ROADMAP.md — vision recorded, NOT built in v1

v1 is the operations Q&A assistant only (Phases 0 → 2.5). Build nothing below until
Furqan explicitly asks. These phases use the vendor / pricing / research folders that are
**not** ingested in v1.

## Phase 4 — Vendor / SKU sourcing analytics
Ingest and analyze vendor sourcing data: `roadmap_sources/vendors/` (MoXOM, Vfan, WK, XO),
`Vfan Item List And Pricing/`, `MT Pricing May 2026.xlsx`, `vendor_research/`.
- Populate `purchase_costs` (landed cost = cost source of truth, Rules 1 & 2).
- Compare vendor quotes vs current selling prices → margin & sourcing recommendations.
- Best-vendor-per-SKU, price-change tracking, reorder-cost modelling.

## Phase 5 — Marketing
- Trend discovery for mobile accessories.
- Product image / video generation (free tools where possible).

## Phase 6 — Lead generation
- Lead scraping + enrichment, gated by a **compliance filter** (consent / data-source rules).

## Cross-cutting roadmap notes
- **Real-time data:** read-only SQL connection to the Focus DB (SQL Server on the LAN) — the
  only true real-time path; needs IT to grant a read-only login.
- **SSO:** corporate login via Microsoft Entra ID / Microsoft 365 (Supabase Azure OAuth).
- **Infra upgrade path:** if always-on uptime is later required, the cheapest reliable step is a
  ~$5–10/mo host for the API + n8n and/or a paid LLM key. Documented in README; no spend in v1.
