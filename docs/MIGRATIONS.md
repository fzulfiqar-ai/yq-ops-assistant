# MIGRATIONS.md — which SQL file owns which view, and what order to apply them

## The one rule

**`scripts/views.sql` is a bootstrap baseline for an EMPTY database. Never apply it to the live
database.** Five of its views are superseded at runtime by `scripts/*_migration.sql` files.
Re-applying the baseline rolls them back — and for two of them it does so *without any error*.

`scripts/views.sql` now carries a guard that raises `REFUSING TO RUN: this database already has
the enriched v_sales.` if you try. That guard is a safety net, not a suggestion — if you hit it,
you are about to do the wrong thing.

**Never add `CASCADE` to a `DROP VIEW` in order to clear a dependency error.** That error is
Postgres protecting views that depend on the one you are dropping; `CASCADE` deletes them
instead, silently and permanently.

`CASCADE` is fine where a migration deliberately rebuilds a view and its dependents — e.g.
`lowstock_unification_migration.sql` drops the leaf view `v_low_stock` that way. The rule is
about reaching for it *reactively*, to silence an error you did not expect.

---

## Who owns which view

Most views are created once and never redefined. These are the ones that ARE redefined, and
where the live definition actually lives:

Five baseline views are superseded. **Three fail loudly if you re-apply them; two corrupt the
numbers silently.** The silent pair matters most — their column lists are identical to the
canonical versions, so `CREATE OR REPLACE VIEW` succeeds with no error at all.

| View | Canonical (live) definition | Baseline in views.sql |
|---|---|---|
| `v_current_stock` | `stock_migration.sql` | **SILENTLY WRONG** — identical columns, different source. Baseline reads the `stock_movements` ledger; canonical reads the `stock_balance` snapshot at `MAX(as_of_date)`. The ledger basis measured **~8.6× overstated** |
| `v_product_margin` | `stock_migration.sql` | **SILENTLY WRONG** — identical columns. Canonical filters to `report_date = MAX(report_date)`; without it the view sums **every loaded period**, double-counting margin and breaking the verified below-cost figure |
| `v_sales` | `division_payment_migration.sql` | **STALE** (24 cols → 31) — lacks `revenue_bhd`, `net_bhd`, `channel`, `is_cash_customer`, `division`, `sale_type`, `is_giveaway` |
| `v_receivables` | `receivables_consolidation_migration.sql` | **STALE, different source table** — baseline is ledger-based, canonical reads `ar_ageing`. Lacks the 15 ageing columns; also carries 4 the canonical does *not* have (`last_entry_date`, `salesman`, `last_narration`, `days_outstanding`). Only `account` overlaps cleanly — `outstanding_bhd` changes meaning |
| `v_low_stock` | `lowstock_unification_migration.sql` | **STALE, diverges both ways** — lacks `sold_90d`, `days_cover`, `suggested_reorder_qty`, `status`; carries `product_name`, `sku_code`, `category_name`, `warehouse_name`, `balance_value_bhd`, `as_of_date` which the canonical drops |
| `v_sales_by_payment`, `v_sales_by_division` | `division_payment_migration.sql` | not present in the baseline at all |
| `v_top_customers`, `v_sales_by_period` | `scripts/views.sql` | baseline **is** canonical — never redefined |
| `v_catalog_stock_rows`, `v_catalog_stock`, `v_shop_unpriced_stock`, `v_catalog_velocity`, `v_catalog_pairs`, `v_catalog_cost`, `v_shop_orders_agent`, `v_shop_order_lines_agent` | `shop_migration.sql` (15-Sep-2026) | not in the baseline — YQ Shop views (stock status per catalog code, velocity, co-purchases, landed cost, PII-free order views) |

### The guard protects the file, not the statements

`views.sql` aborts as a whole if pointed at a migrated database. It **cannot** help you if you
copy one view's block out of the file and run just that. For `v_sales`, `v_receivables` and
`v_low_stock` Postgres would still stop you (the column lists differ). For `v_current_stock` and
`v_product_margin` **nothing stops you** — the statement succeeds and the numbers quietly go
wrong. If you need one view refreshed, take it from its canonical migration file, never from here.

`v_sales` has been redefined three times: `channel_migration.sql` → `revenue_channel_migration.sql`
→ `division_payment_migration.sql`. Only the last one matters; the earlier two are history.

### Revenue basis — do not "reconcile" these

The baseline uses `COALESCE(total_amount_bhd, taxable_bhd, gross_bhd)`. The canonical view uses
`COALESCE(total_amount_bhd, gross_bhd) AS revenue_bhd` — gross, VAT-inclusive, the basis that
reconciles to the verified sales total. They differ **deliberately**. If revenue ever looks
wrong, fix `revenue_channel_migration.sql` / `division_payment_migration.sql`, never the baseline.

---

## To change a view on the live database

1. Find its owning file in the table above (or grep `create or replace view <name>` and take the
   most recent file).
2. Edit **that** file.
3. Re-run **only that file** in the Supabase SQL editor. They are written to be idempotent.
4. If you must rename, reorder, or remove a column, `CREATE OR REPLACE VIEW` cannot do it. Write
   a **new** migration that drops the view *and re-creates every dependent view in order*. Do it
   deliberately; do not reach for `CASCADE`.

## To rebuild from an empty database

Apply in this order:

1. `schema.sql` — tables and constraints.
2. `views.sql` — baseline views, the `yq_readonly` role, and the `run_readonly_query` RPC.
3. Every `*_migration.sql`, **oldest first**, in file-timestamp order — starting
   `team_management` → `dashboard_migration` → `revenue_channel_migration` → … and ending
   `product_finds_migration`.

Known ordering dependencies (each is stated in the file's own header comment):
- `receivables_consolidation_migration.sql` needs the `ar_ageing` table from `dashboard_migration.sql`.
- `division_payment_migration.sql` needs `salesman_channels` (from `channel_migration.sql`) and
  `categories.division`, which it adds itself.
- `lowstock_unification_migration.sql` builds on `stock_migration.sql`.

> ⚠️ This order was reconstructed from file timestamps and file headers, not from a recorded
> migration log — there has never been one. Treat it as best-effort and verify on a scratch
> database before trusting it for a real rebuild. Note also that `views.sql`'s timestamp is
> **not** meaningful: it is regenerated whenever `scripts/migrate_views.py` runs, so it can look
> like the newest file while belonging at position 2.

---

## Generated files — don't hand-edit

| Generated file | Source of truth |
|---|---|
| `scripts/views.sql` | `VIEWS_SQL` in `scripts/migrate_views.py` |
| `scripts/schema.sql` | `SCHEMA_SQL` in `scripts/migrate_supabase.py` |

Both generators rewrite their output on **every** run, including without `--apply` — `write_sql()`
is called before the `--apply` check. A hand-edit to `views.sql` was silently lost this way once
already. `migrate_views.py` now prints a warning when it overwrites a file that had diverged;
`migrate_supabase.py` does not yet (its output has not drifted, so it was left alone).

Two quirks worth knowing:
- **Line endings.** `views.sql` is written through `write_text`, so on Windows it lands as CRLF
  while the `VIEWS_SQL` string is LF. The two are character-identical but **not byte-identical**;
  any hash- or byte-level check will report permanent drift. Compare with `read_text`, not bytes.
- **`migrate_views.py --apply` does not read `.env`,** unlike the other `apply_*` scripts. That is
  deliberate: this is the empty-DB bootstrap, so pointing it at the live database has to be a
  conscious act. If it aborts, it now prints the guard's own message rather than a traceback.


## Applied 15-Sep-2026 (live Supabase, via `python -m scripts.apply_sql`)
`price_list_migration.sql` → `pricebook_key_migration.sql` → `rls_lockdown_migration.sql` → `division_split_migration.sql` → `shop_migration.sql`, in that order. Verified afterwards: `v_price_list_by_book` orders `warehouse_name IS NULL` first (98/182 trade prices corrected on the live link), `selling_prices` deduplicated (1,219 rows = 1,219 natural keys), `division_rules` present, `v_catalog_stock` maps 122/122 stock rows. `DATABASE_URL` lives in `.env` (never commit it).
