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

## Phase 7 — Focus X → automated report ingest (waiting on vendor)
Removes the manual morning export-and-upload step. Furqan is asking Focus Softnet what the API
costs; **if it is too expensive, build the free route instead** — it needs no vendor cooperation
beyond a settings change:
- **Free route (no cost):** Focus X's built-in "Schedule Report" emails the Sales Day Book on a
  schedule → a GitHub Actions cron (~15 min, the same free mechanism as `keepalive.yml`) polls
  that mailbox and POSTs the file to a new narrow ingest endpoint. No manual upload, no new
  hosting spend. Open question that decides freshness: what minimum interval does the Focus
  scheduler allow, and can it deliver to email / SFTP / a watched folder?
- **Paid route (if affordable):** the Focus REST API, or a read-only SQL login (below).
- **Fallback if the scheduler is daily-only:** the Playwright export bot already planned as
  "Phase 2" in `docs/FOCUS_EXPORT.md` — logs in and downloads on a schedule like a human.
- Note: `POST /ingest` runs the full ~9-step refresh and is unsuitable for a live cadence — this
  needs a separate narrow path (per-invoice replace, because `line_no` is synthesized per file).
- Full research, vendor questions, and build design:
  `~/.claude/plans/focus-x-live-sprightly-flask.md` (24-Aug-2026).

## Cross-cutting roadmap notes
- **Real-time data:** read-only SQL connection to the Focus DB (SQL Server on the LAN) — the
  only true real-time path; needs IT to grant a read-only login. (The saved FocusX dashboard
  page reports `bOnCloud = false`, so this is a permissions ask, not a cloud impossibility.)
- **SSO:** corporate login via Microsoft Entra ID / Microsoft 365 (Supabase Azure OAuth).
- **Infra upgrade path:** if always-on uptime is later required, the cheapest reliable step is a
  ~$5–10/mo host for the API + n8n and/or a paid LLM key. Documented in README; no spend in v1.
