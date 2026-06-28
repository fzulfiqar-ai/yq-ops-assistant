# Focus → YQ Portal — daily data upload

How to keep the AI portal's data fresh from Focus ERP. **Phase 1 (now): upload via the dashboard.**
No DB access and no Focus API, so data comes in as **report exports you upload**. It's safe to
re-upload — the loader is idempotent, so the same days never create duplicates.

## What to export

### Daily (6 reports — export these every day, date range **up to yesterday**)
| Focus report | Powers |
|---|---|
| **Sales_day_book** | Sales (line items) — revenue, salesman, items sold |
| **Summary_sales_register** | Sales header — salesman / payment mode (the salesman join) |
| **Stock_balance_by_warehouse** | Current stock per warehouse — stock value, low-stock |
| **Stock_ledger** | Stock movements + inter-warehouse transfers — the fraud/recon model |
| **Customer_summary_ageing_by_due_date** | Receivables / ageing — collections, cashflow |
| **Product_Profitability_Report** | Margins — profitability, below-cost |

### Weekly / occasional (3 reports — change rarely)
| Focus report | Powers | Export when |
|---|---|---|
| **MASellingPriceBook** | Standard selling price list + product/SKU master | prices change |
| **ModernTradeSellerBook** | Modern-trade price list | prices change |
| **Multi_level_stock_movement** | Product **categories** (Cable, Charger, Power Bank, Sim…) → by-category analysis + Accessories vs Telecom split | categories/SKUs change |

### Do NOT export (the portal ignores these — they add nothing)
- **Stock_movement** (plain) — item summary; **Stock_ledger** already has the full per-voucher
  transfer detail.
- The other 3 **Customer_ageing** variants (detail_analysis, detail_by_due_date, summary_analysis) —
  duplicates; only `Customer_summary_ageing_by_due_date` is used.
- **Ledger / Ledger_detail** — the full trial balance; receivables come from the ageing report.
- The **Mobile Accessories Monthly** workbooks — your manual workbook, not ingested.

## How to export (in Focus)
- Set the **date range up to yesterday**. Focus filters by **date, not time** — so to get the freshest
  numbers during the day, just re-export today's range later and re-upload (no duplicates are created).
- Make the range **wide enough to cover any gap** — e.g. financial-year-to-date, or at least the last
  ~100 days (the AI needs 30/90-day windows). If a day was missed, a wider range fills it in.
- **Keep Focus's default file names** (they contain the report name, e.g. `Sales_day_book…xlsx`) — the
  portal recognises the report type from the file name. (`Stock_balance_by_warehouse` are "as on
  date" snapshots — exporting just gets the latest.)

## How to upload (in the YQ dashboard)
1. Open **Data** in the portal.
2. The **Data sources** panel shows each report, its cadence, and "data until <date>" with a status dot
   (green = current, amber = behind, grey = not loaded) — so you can see what needs uploading.
3. **Drag all the exported reports in at once** (or click to browse). Each file is auto-matched to its
   report; non-Focus or unrecognised files are flagged and **ignored** (never loaded).
4. Click **Upload & refresh**. The portal:
   - validates each file is a genuine Focus export,
   - de-dups (if you accidentally include two of the same report, only the newest loads),
   - **verifies** the loaded totals against the reports (Sales / Receivables / Stock),
   - flushes the cached answers and reports **what changed**,
   - updates every dashboard and AI agent.
5. The result shows: loaded reports, anything ignored, **Verify PASS/FAIL**, and what changed.

**Re-uploading is always safe** — same invoice/line/snapshot = no duplicate. Upload the full daily set
together for a complete refresh, or a single report to refresh just that one.

## Phase 2 (later — after ~1 month of Phase 1)
Automate the export so even the clicking is hands-off. Preferred: a **headless browser bot
(Playwright)** that logs into FocusX in the background and downloads the reports on a schedule — it
runs invisibly so it won't take over your screen while you work. (Power Automate Desktop is a no-code
alternative but grabs the mouse/keyboard while it runs.) We'll set this up against your FocusX then.
