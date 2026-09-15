# SCHEMA.md — Focus ERP exports → Supabase tables

YQ Bahrain W.L.L internal ops assistant. This maps each file in `Focus ERP Data/` to
Supabase/Postgres tables using the **actual Focus column names** found in the exports.
Money columns are BHD `numeric`. Every table has RLS enabled and stores `source_file`
+ `imported_at`. Loads are **idempotent** via the natural keys noted per table — re-exporting
and re-loading never double-counts.

> Source-file reality (verified): Focus reports have a 5-row title block; the real header is
> **row 6**. The two PriceBook files have their header in **row 1**. The Ledger / Ledger_detail
> / Stock_ledger reports are **grouped** — column A holds either a section header
> (account/item name) or a transaction date; ingestion forward-fills the section into its own
> column and drops `Sub Total` / `Grand Total` rows.

## File → table summary

| Focus file | Header row | Layout | → Table(s) | ~rows |
|---|---|---|---|---|
| Summary_sales_register*.xlsx | 6 | flat | `orders` | 2,028 |
| Sales_day_book*.xlsx | 6 | flat | `order_lines` | 9,092 |
| Stock_ledger*.xlsx | 6 | grouped by item | `stock_movements` (+ `shipments` view) | 26,683 |
| Ledger*.xlsx | 6 | grouped by account | `ledger_entries` | 49,226 |
| Ledger_detail*.xlsx | 6 | grouped by account | *(skipped — subset of Ledger)* | — |
| Product_Profitability_Report*.xlsx | 6 | flat | `product_profitability` | 144 |
| MASellingPriceBook*.xlsx | 1 | flat | `selling_prices` (price_book=`MA_base`) | 647 |
| ModernTradeSellerBook.xlsx | 1 | flat | `selling_prices` (price_book=`modern_trade`) | 167 |
| MT Pricing May 2026.xlsx *(reference)* | 1 | flat | `selling_prices` (price_book=`mt_pricing`) | 167 |

---

## Dimension / spine

### categories
User-managed; categories are **data, not code** (product-agnostic guarantee).

| column | type | source |
|---|---|---|
| id | bigint PK | — |
| name | text UNIQUE NOT NULL | (user-entered) |
| created_at | timestamptz default now() | — |

### products
Built primarily from the price books (clean Item Code + Item Name).

| column | type | Focus source |
|---|---|---|
| id | bigint PK | — |
| sku_code | text UNIQUE NOT NULL | Item Code (e.g. `X02-M`) |
| item_name | text | Item Name |
| category_id | bigint FK→categories NULL | (you assign) |
| unit_name | text | Unit Name |
| status | text | Status |
| created_at / updated_at | timestamptz | — |

**Natural key:** `sku_code`.

### product_aliases
Maps the long Focus item strings used in sales/stock/profitability to a SKU, so facts link
reliably instead of fuzzy-guessing at query time. Populated by `scripts/reconcile_products.py`.

| column | type | notes |
|---|---|---|
| id | bigint PK | — |
| product_id | bigint FK→products | — |
| alias_text | text UNIQUE NOT NULL | normalized item string |

### customers
| column | type | Focus source |
|---|---|---|
| id | bigint PK | — |
| name | text UNIQUE NOT NULL | Customer / Customer Account |
| created_at | timestamptz | — |

---

## Sales

### orders  *(= Summary_sales_register)*
| column | type | Focus column |
|---|---|---|
| id | bigint PK | — |
| invoice_no | text UNIQUE NOT NULL | **Invoice** (`'SI : 1'`) |
| order_date | date | Date |
| customer_id | bigint FK→customers NULL | (resolved from Customer) |
| customer_name | text | Customer |
| gross_bhd | numeric | Gross |
| salesman | text | Salesman |
| payment_mode | text | Payment Mode |
| sales_account_name | text | Sales Account Name |
| source_file / imported_at | text / timestamptz | — |

**Natural key:** `invoice_no`.

### order_lines  *(= Sales_day_book)*
| column | type | Focus column |
|---|---|---|
| id | bigint PK | — |
| invoice_no | text NOT NULL | **Voucher** (join key) |
| order_id | bigint FK→orders NULL | resolved via invoice_no |
| line_no | int | per-invoice sequence |
| line_date | date | Date |
| customer_account | text | Customer Account |
| item_name | text | Item |
| product_id | bigint FK→products NULL | resolved via product_aliases |
| quantity | numeric | Quantity |
| rate_bhd | numeric | Rate |
| gross_bhd | numeric | Gross |
| discount_bhd | numeric | Discount |
| taxable_bhd | numeric | Taxable |
| vat_amount_bhd | numeric | VAT Amount |
| total_amount_bhd | numeric | Total Amount |
| warehouse_name | text | Warehouse Name |
| narration | text | Narration |
| source_file / imported_at | — | — |

**Natural key:** `(invoice_no, line_no)`.
**Join (Rule 3 & 4):** `order_lines.invoice_no = orders.invoice_no` on exact string equality
(`'SI : N'`, spaces around the colon). Ingest **hard-fails if the match rate < 80%**.

---

## Stock

### stock_movements  *(= Stock_ledger, all voucher types)*
| column | type | Focus column |
|---|---|---|
| id | bigint PK | — |
| item_name | text NOT NULL | (forward-filled section header) |
| product_id | bigint FK→products NULL | via product_aliases |
| move_date | date | Date |
| voucher | text | Voucher (e.g. `MRN:YQ-25-12-2`) |
| voucher_type | text | Voucher name / parsed prefix |
| received_qty | numeric | Received Quantity |
| received_rate_bhd | numeric | Rate (received) — **ERP valuation only, never for pricing (Rule 1)** |
| issued_qty | numeric | Issued Quantity |
| issued_rate_bhd | numeric | Rate (issued) |
| balance_qty | numeric | Balance Quantity |
| received_value_bhd | numeric | Value (received) |
| issued_value_bhd | numeric | Value (issued) |
| balance_value_bhd | numeric | Value (balance) |
| avg_rate_bhd | numeric | Avg Rate — **valuation only** |
| warehouse_name | text | Warehouse Name |
| to_warehouse_name | text | To Warehouse Name |
| narration | text | Narration |
| row_hash | text | md5 of the raw row (dedupe) |

**Natural key:** `(voucher, item_name, row_hash)`.
**Current stock (Rule 6):** `MAX(id)` per product (not `MAX(date)`).
**Purchases (Rule 5):** `voucher_type = 'Material Receipt Note'` only.

### shipments  *(VIEW — inbound goods receipts)*
```sql
CREATE VIEW shipments AS
SELECT move_date AS received_date, voucher AS mrn_no, item_name, product_id,
       received_qty, received_rate_bhd, received_value_bhd, warehouse_name
FROM stock_movements
WHERE voucher_type = 'Material Receipt Note';
```
A view keeps one source of truth and stays correct on every re-import.

---

## Finance & profitability

### ledger_entries  *(= Ledger — the richer of the two ledger files)*
`Ledger_detail` is a strict subset of `Ledger`, so it is **skipped** on ingest.

| column | type | Focus column |
|---|---|---|
| id | bigint PK | — |
| account | text NOT NULL | (forward-filled section header, e.g. `Bank 121-001`) |
| entry_date | date | Date |
| voucher | text | Voucher |
| counter_account | text | Account (on detail rows) |
| debit_bhd | numeric | Debit |
| credit_bhd | numeric | Credit |
| balance_bhd | numeric | Balance |
| currency | text | Currency |
| payment_mode | text | Payment Mode |
| salesman | text | Salesman |
| narration | text | Narration |
| row_hash | text | md5 of raw row |

**Natural key:** `(account, voucher, row_hash)`.

### product_profitability  *(= Product_Profitability_Report — a point-in-time snapshot)*
| column | type | Focus column |
|---|---|---|
| id | bigint PK | — |
| item_name | text NOT NULL | Particulars |
| product_id | bigint FK→products NULL | via product_aliases |
| report_date | date | from `[As on date …]` title block |
| gross_bhd | numeric | Gross |
| discount_pct | numeric | Discount % |
| net_amount_bhd | numeric | Net amount |
| cogs_bhd | numeric | COGS — **v1 cost basis for margin** |
| gross_profit_bhd | numeric | Gross Profit |
| gp_margin_pct | numeric | GP Margin % |
| misc_charges_bhd | numeric | Misc Charges |
| net_profit_bhd | numeric | Net Profit |
| np_margin_pct | numeric | NP Margin % |

**Natural key:** `(item_name, report_date)` — re-import = a new snapshot, never an overwrite.

---

## Pricing

### selling_prices  *(= MASellingPriceBook + ModernTradeSellerBook + MT Pricing reference)*
28-column Focus PriceBook schema. `customer_name` blank = base/list price.

| column | type | Focus column |
|---|---|---|
| id | bigint PK | — |
| item_name | text | Item Name |
| sku_code | text | Item Code |
| product_id | bigint FK→products NULL | — |
| customer_name | text | Customer Name (blank = base/list) |
| customer_code | text | Customer Code |
| warehouse_name | text | Warehouse Name |
| warehouse_code | text | Warehouse Code |
| price_book | text | `MA_base` \| `modern_trade` \| `mt_pricing` (from file) |
| currency | text | Currency |
| start_date | date | Start date |
| end_date | date | End date |
| min_qty | numeric | MinQty |
| max_qty | numeric | MaxQty |
| unit_name | text | Unit Name |
| rate_bhd | numeric | Rate (= selling price) |
| price_tiers | jsonb | Val 1 … Val 13 |
| status | text | Status |
| narration | text | Narration |

**Natural key:** `(sku_code, price_book, customer_code, warehouse_name, start_date)` —
repaired 06-Sep-2026 by `scripts/pricebook_key_migration.sql`. The original key omitted
`warehouse_name` and relied on `customer_code`, which is **NULL on 100% of rows in both books**;
under Postgres' default `NULLS DISTINCT` that index never matched, so `ON CONFLICT` never fired
and every re-ingest appended a duplicate copy. The new index is `NULLS NOT DISTINCT`.

> **THREE PRICE LAYERS LIVE IN THE MA_base BOOK — do not treat it as one price per SKU:**
> | `warehouse_name` | meaning | rows |
> |---|---|---:|
> | `NULL` (blank) | **dealer / list price — this is the B2B price** | 293 |
> | `Causeway` | outlet retail price | 181 |
> | `YQ Roadshow` | outlet retail price | 180 |
>
> All three share a `start_date`, so any "latest price" query that tie-breaks on `rate_bhd`
> picks the **retail** price. That defect showed retail as the B2B price on 89 of 170 SKUs
> (median +77%, max +233%) and fabricated 71 phantom price changes. Ground truth: Focus's own
> Stock-balance **"Selling Rate"** matches the blank-warehouse row on 69/69 SKUs and an outlet
> row on 0. Order by `(warehouse_name IS NULL) DESC, start_date DESC, id DESC`.
> `modern_trade` has `warehouse_name` set on all 167 rows, so it falls through to latest-dated.

> **⚠️ PRICE-BOOK DATES ARE `M/D/YYYY`, NOT `D/M/YYYY`.** The two PriceBook files are the only
> Focus exports that emit dates as **text**; every other report emits real Excel datetimes.
> Measured: 494 of 655 MA_base start dates and 167 of 167 modern_trade start dates are
> *unambiguously* month-first; **zero** are day-first. Parsing them day-first (as
> `norm_date` does, correctly, for the transaction reports) mis-dated **533 values** and pushed
> **13 start dates into the future**, silently excluding those prices from "current". Use
> `norm_date_us()` in `scripts/ingest.py` for price books only.

### purchase_costs  *(created now, EMPTY in v1; filled in Phase 4 from vendor/pricing sheets)*
The **cost source of truth** (Rules 1 & 2). NOT populated by any of the 8 Focus files — vendor
landed cost lives in the roadmap pricing folders.

| column | type | notes |
|---|---|---|
| id | bigint PK | — |
| sku_code | text NOT NULL | — |
| product_id | bigint FK→products NULL | — |
| landed_cost_bhd | numeric NOT NULL | the only value used for pricing |
| currency | text | — |
| effective_date | date NOT NULL | versioning |
| source_file | text | — |
| created_at | timestamptz | — |

`UNIQUE(sku_code, effective_date)` → `INSERT … ON CONFLICT DO NOTHING`. Latest cost =
`MAX(id)` per `sku_code`.

---

## App / governance

### user_roles
You insert the **admin** row by hand (your email). Migration uses `ON CONFLICT DO NOTHING`
and **never truncates**.

| column | type | notes |
|---|---|---|
| id | bigint PK | — |
| email | text UNIQUE NOT NULL | — |
| role | text NOT NULL | `admin` \| `manager` \| `viewer` |
| created_at | timestamptz | — |

### pending_actions  *(Phase 2)*
`id`, `action_type`, `payload jsonb`, `status` (`pending`/`approved`/`rejected`/`done`),
`requested_by`, `requested_at`, `approved_by`, `approved_at`, `result`.

### query_cache  *(Phase 1)*
`id`, `query_hash` UNIQUE (md5 of normalized question), `question`, `reply`, `sql_used`,
`raw_data jsonb`, `created_at`, `expires_at` (= created_at + 7 days). Exact-match lookup.

### audit_log
`id`, `ts`, `user_email`, `event` (`ask`/`action`/`ingest`/`alert`), `question`, `sql_used`,
`detail jsonb`. Corporate audit + debugging unattended automation.

### ingest_runs
`id`, `started_at`, `finished_at`, `status`, `file`, `rows_in`, `rows_loaded`,
`join_match_pct`, `errors`. Powers run-history + failure alerts.

---

## Semantic views (Phase 0.5 — the assistant queries ONLY these)
- `v_sales` — order_lines ⨝ orders (salesman/payment via `'SI : N'`) + product + category.
- `v_current_stock` — `MAX(id)` balance per product (+ warehouse), reorder flag.
- `v_product_margin` — product_profitability + selling_prices (Focus COGS basis, Rule 1).
- `v_receivables` — debtor balances / ageing from ledger_entries.
- `v_top_customers`, `v_sales_by_period`, `v_low_stock` — common rollups.

## Ingestion normalization (all files)
Skip rows 1–5 (price books: header row 1); header = row 6; forward-fill grouped section
headers; keep real detail rows only; drop `Sub Total` / `Grand Total`; parse mixed date
formats (datetime + `m/d/Y` + DD/MM/YYYY) — **but price books are month-first, see above**; strip thousands separators; trim whitespace; drop
empty rows. **If columns don't match this file, stop and ask.**

## YQ Shop (15-Sep-2026, `scripts/shop_migration.sql`, contract in `docs/SHOP.md`)
Service-role-only tables (RLS enabled, no anon/authenticated policy):
- `salesmen` — id, name UNIQUE, phone, email, whatsapp, user_email (→ user_roles.email), focus_name, referral_code UNIQUE,
  is_active, sort_order, notify_email, notify_whatsapp. Contact details are never seeded from the repo.
- `shop_orders` — order_no UNIQUE (PREFIX-YYMM-NNNN via `shop_next_order_no()` + `shop_counters`), token UNIQUE (status
  page), status new|confirmed|packed|delivered|cancelled, customer_* (name, phone, shop, area, email), note, salesman_id FK,
  salesman_name, source referral|dropdown|default|salesman, placed_by (staff login when source=salesman), referral_code, src, coupon_code, subtotal/discount/delivery/total_bhd,
  items_count, units_count, has_backorder, ip_hash, ua, notify_result jsonb, notified_at.
- `shop_order_lines` — order_id FK (cascade), item_code, display_name, spec, image_url, qty, list_price_bhd,
  unit_price_bhd, discount_bhd, line_total_bhd, stock_status, backorder, rule_ids jsonb.
- `shop_order_events` — order_id FK, ts, actor, event (`created`, `status:<s>`), detail jsonb.
- `discount_rules` — kind qty_tier|cart_value|coupon|bundle_price|salesman_offer, scope jsonb
  {item_codes[], categories[], referral_codes[]}, min_qty, min_value_bhd, exactly one of pct_off / amount_off_bhd /
  fixed_price_bhd, coupon_code UNIQUE, stackable, starts_at, ends_at, max_uses, uses, priority, is_active.
- `shop_events` — funnel pings (view|item|add|checkout|order) with session_id, item_code, referral_code, src, hashed IP.
- `catalog_stock_map` — manual `item_code → stock_item_name` override for the stock matcher.
- `catalog_items` gained `moq` (default 1) and `pack_size`.
`user_roles.role` check now includes `salesman`.
Views: `v_catalog_stock_rows` (each latest-snapshot stock row → catalog code: manual map → product_aliases → longest
normalised-prefix match), `v_catalog_stock` (qty per code — never exposed publicly), `v_shop_unpriced_stock`,
`v_catalog_velocity` (30/90-day units, customers, from `v_sales.sku_code`), `v_catalog_pairs` (co-purchases, 180 d),
`v_catalog_cost` (latest `mrn_landed_costs` per code; service role only), `v_shop_orders_agent` /
`v_shop_order_lines_agent` (PII-free, granted to `yq_readonly`). Settings live in `app_settings` as `shop_*` keys.
