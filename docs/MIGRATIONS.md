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
| `v_shop_orders_agent` | `shop_salesman_mode_migration.sql` (15-Sep-2026, appends `placed_by`) | not in the baseline |
| `v_catalog_stock_rows`, `v_catalog_stock`, `v_shop_unpriced_stock`, `v_catalog_velocity`, `v_catalog_pairs`, `v_catalog_cost`, `v_shop_order_lines_agent` | `shop_migration.sql` (15-Sep-2026) | not in the baseline — YQ Shop views (stock status per catalog code, velocity, co-purchases, landed cost, PII-free order views) |

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

Also applied 15-Sep-2026: `shop_catalog_tidy_migration.sql` — `catalog_items.hidden` (owner's "not a product" switch; kept separate from `is_active`, which the price-book auto-sync mirrors and would switch back on), the three display stands hidden, and 45 SKUs re-categorised so no `OTHER` / `FOR CAR` / `WIRELESS HFS` remains (verified: 179 shop SKUs in 8 categories). Categories come from `app.catalog.classify_category()`, which the auto-sync now also uses for every new SKU.

Also applied 15-Sep-2026: `shop_salesman_mode_migration.sql` — `shop_orders.source` gains `salesman`, `shop_orders.placed_by`, `v_shop_orders_agent.placed_by` (appended last: CREATE OR REPLACE VIEW cannot reorder columns — the first attempt failed on exactly that), and `user_roles_role_check` widened to include `salesman` (the portal offered the role since July but the DB rejected it — no salesman login had ever been created).

## Applied 16-Sep-2026: `hotfix_view_grants_migration.sql` — the public schema is closed to the publishable key

**What was wrong.** Supabase's default ACL (`pg_default_acl` for role `postgres`) grants every new table,
view, sequence and function in `public` to `anon` and `authenticated`. Tables were shielded by RLS, but a
view executes with its owner's rights, so all 65 views — `v_sales` with customer names, `v_catalog_cost`
(landed cost), `v_current_stock`, `v_product_economics`, `v_customer_ltv` — were readable with the anon
key that ships in the browser bundle (verified with a plain PostgREST GET). Three tables
(`customer_aliases`, `division_rules`, `salesman_channels`) had no RLS at all and were writable.

**What the migration does** (generated by `python -m scripts.audit_grants --emit`, one plain statement per
object): enables RLS on every table; re-creates `*_yq_readonly_read` policies wherever `yq_readonly` holds a
SELECT grant, so the assistant's read path is unchanged; revokes all privileges on every view, table and
sequence from `anon`/`authenticated`; revokes EXECUTE on `match_kb` from PUBLIC and grants it to
`service_role` (the API calls it with the service key); drops the seven stale blanket `*_read` policies for
`authenticated`; alters `postgres`'s default privileges so future objects are not auto-granted; creates
`keepalive_ping` (RLS on, no policy, SELECT for anon) as the one thing the keepalive workflow may read; and
ends with a `DO` block that raises if anything is still exposed.

**Rules from now on.**
- Never grant `anon`/`authenticated` anything in `public`. The SPA uses Supabase for auth only; all data goes
  through the FastAPI API with the service key.
- Do **not** set `security_invoker` on views: `run_readonly_query` runs as `yq_readonly`, which is granted
  on views, not on the tables beneath them.
- Objects created by `supabase_admin` (extensions) still inherit its default ACL, so run
  `python -m scripts.audit_grants` after every migration; exit 0 means nothing is exposed.
- Re-applying the file is safe (idempotent). Rollback would be re-granting; do not.

## Applied 16-Sep-2026: `marketplace_migration.sql` — merchant identity, attribution, lifecycle depth

Adds `shop_customers`, `shop_customer_phones`, `shop_customer_sessions`, `shop_access_links`,
`shop_push_subscriptions`, `shop_reserved_slugs`; widens `shop_orders.status` (`out_for_delivery`),
`shop_orders.source` (`market`), `shop_events.event` (v2 vocabulary) and `user_roles.role` (`storekeeper`);
adds the lifecycle / attribution / payment-ready columns on `shop_orders`, `qty_confirmed` + `line_status` on
`shop_order_lines`, `device_id` / `customer_id` / `meta` on `shop_events`, storefront fields on `salesmen`, seven
`shop_*` settings, and the views `v_customer_regulars` (service role only — customer names),
`v_shop_assignment_queue`, `v_shop_search_terms`, `v_shop_rail_perf`. `v_shop_orders_agent` gained APPENDED
columns (`attribution_source … cancelled_at`). Ends with a `DO` block that raises if anything is missing or
granted to `anon`/`authenticated`. Verified afterwards with `python -m scripts.audit_grants` (clean apart from the
temporary `user_roles` keepalive grant) and `python -m tests.test_shop` (31/31). Contract: `docs/SHOP.md`
§ Marketplace.

## Applied 17-Sep-2026: `marketplace_campaign_creative_migration.sql` — campaign creatives that are never cropped

Marketplace v3 (D4). Four **additive** columns on `shop_campaigns`, every one nullable or defaulted, so the
code already in production kept working before and after the apply: `image_url_600` (the 600 w rendition the
upload route already returns, for a 600/1200 srcset), `image_fit` (`contain` default | `cover` — how an
uploaded photo is framed), `product_codes text[]` (a creative *composed* from up to 3 catalog codes; the ≤3
limit is enforced by `app.shop.validate_campaign`, not by the DB), and `canvas` (`lilac` default | `apricot` |
`mint` | `plum` | `night`). The two `check` constraints are dropped by name and re-added, so a re-run never
stacks a second auto-named constraint — **the whole file is idempotent and re-run safe**. **No GRANT**:
`shop_campaigns` is service-role only (RLS on, no policies — `scripts/marketplace_campaigns_migration.sql`).
Applied with `python -m scripts.apply_sql scripts/marketplace_campaign_creative_migration.sql`. The file's own
closing `DO` block raises if a column or a check is missing, or if anything is granted to
`anon`/`authenticated`; `python -m scripts.audit_grants` afterwards was **clean (exit 0)**, and
`python -m tests.test_shop` passed (51/51, the new cases cover the enums and the 3-code cap). Rendering and
admin contract: `docs/MARKETPLACE.md` § Campaign creative.


## Backup, restore and the preservation gate (24-Sep-2026)

Before **every** production change: `python -m scripts.db_backup --all --out business_data/backups/<date>_<label>`
(every public table inside ONE read-only REPEATABLE READ transaction, UTC timestamps, manifest with columns and
max id / created_at, re-verified on write) and `python -m scripts.prod_gate snapshot --label <label>`. After the
change: a second gate snapshot and `python -m scripts.prod_gate compare A.json B.json`, which fails if any
pre-existing order, line, event, merchant, salesman link, target or audit row disappeared or changed without an
explaining event. Rehearse every migration first as `BEGIN; SET LOCAL lock_timeout='2s'; <file>; <checks>; ROLLBACK;`.

`db_backup --restore` now refuses (dry run and `--yes` alike) when:
- a table outside `--tables` references a restored table with ON DELETE CASCADE / SET NULL and holds rows
  (restoring `shop_orders` alone would have deleted every line and event; `salesmen` alone would have blanked
  the salesman on every order and merchant);
- the live table holds rows newer than the backup (max id, or created_at compared as timestamptz);
- the backup's columns do not fit the live table.
It deletes children first, loads parents first, asserts every count and writes an audit_log row.

**Drill, 24-Sep-2026 (local scratch Postgres 17.6, never production):** schema of the 19 marketplace/people/audit
tables copied with a read-only `pg_dump --schema-only`; backup `2026-09-24_prerollout` restored in about 1 s
(salesmen 18, targets 15, user_roles 19, audit_log 607, shop_customers 15, shop_orders 17, lines 122, events 41,
shop_events 2,557 ...). md5 over every business field of shop_orders, shop_order_lines, shop_order_events,
shop_customers and salesman_targets: **identical** to production. Guards proven on the scratch copy: restoring
shop_orders alone REFUSED (122 lines + 41 events would cascade), salesmen alone REFUSED (17 orders + 15 merchants
would lose their rep), and a restore over a newer order REFUSED. The drill also caught two restore bugs before they
could matter (a sequence re-seed on a table without an id; timestamps compared as text across time zones).
Tools: portable PostgreSQL binaries in `%LOCALAPPDATA%\yq-tools\pgsql` (not in the repo), cluster on port 55432.
