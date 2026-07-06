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

## Phase 5 — Marketing ✅ (see docs/MARKETING.md)
- ~~Trend discovery for mobile accessories.~~ (`trend_radar` agent)
- ~~Product image / video generation (free tools where possible).~~ (`content_engine` —
  Pillow ad cards + FFmpeg 9:16 videos from catalog photos; IG/FB via Graph API).
- **Outreach engine** (the 10k/month build): send queue + attribution + Marketing Studio.

## Phase 6 — Lead generation ✅ (see docs/MARKETING.md)
- ~~Lead scraping + enrichment, gated by a **compliance filter**.~~ (`lead_gen` Overpass +
  `contact_enrich`; PDPL opt-out enforced before draft and send; human approves every message).

## Cross-cutting roadmap notes
- **Real-time data:** read-only SQL connection to the Focus DB (SQL Server on the LAN) — the
  only true real-time path; needs IT to grant a read-only login.
- **SSO:** corporate login via Microsoft Entra ID / Microsoft 365 (Supabase Azure OAuth).
- **Infra upgrade path:** if always-on uptime is later required, the cheapest reliable step is a
  ~$5–10/mo host for the API + n8n and/or a paid LLM key. Documented in README; no spend in v1.
