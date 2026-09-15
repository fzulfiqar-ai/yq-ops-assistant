
-- ============================================================
-- YQ Bahrain ops assistant — Phase 0.5 semantic views
--
-- GENERATED FILE — do not hand-edit scripts/views.sql. Edit VIEWS_SQL in
-- scripts/migrate_views.py, then re-run `python -m scripts.migrate_views`.
--
-- BOOTSTRAP BASELINE ONLY — this file builds the views on an EMPTY database.
-- FIVE of its views are SUPERSEDED at runtime by scripts/*_migration.sql:
--   v_sales, v_receivables, v_low_stock  — regress LOUDLY (column lists differ, so a
--       CREATE OR REPLACE errors out); and
--   v_current_stock, v_product_margin    — regress SILENTLY (identical columns, only the
--       source/filter differs, so a re-run SUCCEEDS and quietly corrupts the numbers).
-- Applying this file to the LIVE database would regress all five. The guard below aborts
-- the whole file, but it CANNOT protect you if you copy a single view's block out of it.
-- Apply order and view ownership: docs/MIGRATIONS.md.
--
-- LLM queries ONLY these views — never raw tables.
-- ============================================================

-- ── Safety guard: refuse to run against an already-migrated database ──────────
-- No-op on a fresh DB (v_sales does not exist yet, so the column test is false).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'v_sales'
          AND column_name  = 'revenue_bhd'
    ) THEN
        RAISE EXCEPTION USING
            MESSAGE = 'REFUSING TO RUN: this database already has the enriched v_sales.',
            DETAIL  = 'scripts/views.sql is the Phase-0.5 bootstrap baseline for an EMPTY '
                      'database. Re-applying it here would strip revenue_bhd, net_bhd, '
                      'channel, division, sale_type and is_giveaway from v_sales and break '
                      'app/templates.py, app/ai.py and every rollup view that reads them.',
            HINT    = 'To rebuild views on an existing database, re-apply the migrations in '
                      'the order given in docs/MIGRATIONS.md — not this file. Do NOT work '
                      'around this by adding CASCADE to the DROP VIEW below: that deletes '
                      'the enriched views outright.';
    END IF;
END $$;

-- v_sales: enriched sales lines ---------------------------
-- SUPERSEDED at runtime: the canonical definition lives in
-- scripts/division_payment_migration.sql, which appends revenue_bhd, net_bhd, channel,
-- is_cash_customer, division, sale_type and is_giveaway. This simpler version exists only
-- so a fresh DB bootstraps before migrations run — apply the migrations right after.
-- Note the revenue basis differs deliberately: the canonical view uses
-- COALESCE(total_amount, gross) — gross, VAT-inclusive, the basis that reconciles against
-- the Focus reports — not the 3-way fallback below, which understates revenue whenever
-- taxable_bhd is present. Do not "reconcile" the two; fix the canonical file if it is wrong.
-- DROP first so the historic column rename (warehouse_name -> salesman_resolved) applies
-- cleanly on a part-built DB. Never add CASCADE here — see the guard above.
DROP VIEW IF EXISTS v_sales;
CREATE VIEW v_sales AS
SELECT
    ol.id                                          AS line_id,
    ol.invoice_no,
    ol.line_no,
    COALESCE(ol.line_date, o.order_date)           AS sale_date,
    o.order_date,
    o.customer_name,
    ol.customer_account,
    o.salesman,
    o.payment_mode,
    o.sales_account_name,
    ol.item_name,
    p.sku_code,
    p.item_name                                    AS product_name,
    cat.name                                       AS category_name,
    ol.quantity,
    ol.rate_bhd,
    ol.gross_bhd,
    ol.discount_bhd,
    ol.taxable_bhd,
    ol.vat_amount_bhd,
    -- Focus export leaves total_amount_bhd blank when VAT is zero;
    -- fall back to taxable then gross so revenue aggregations never return null.
    COALESCE(ol.total_amount_bhd, ol.taxable_bhd, ol.gross_bhd) AS total_amount_bhd,
    -- In YQ Bahrain's Focus Sales Day Book the "Warehouse Name" column holds
    -- the salesman name, not a warehouse. Use orders.salesman first; fall back
    -- to this field so salesman is always populated.
    COALESCE(o.salesman, ol.warehouse_name)                      AS salesman_resolved,
    ol.warehouse_name                                            AS salesman_raw,
    ol.narration
FROM order_lines ol
LEFT JOIN orders          o   ON o.invoice_no  = ol.invoice_no
LEFT JOIN product_aliases pa  ON pa.alias_text = ol.item_name
LEFT JOIN products        p   ON p.id          = pa.product_id
LEFT JOIN categories      cat ON cat.id        = p.category_id;

-- v_current_stock: latest balance per item+warehouse ------
-- ⚠️ SUPERSEDED at runtime by scripts/stock_migration.sql — AND THIS ONE REGRESSES
-- SILENTLY. The column list is identical to the canonical view, so CREATE OR REPLACE
-- SUCCEEDS with no error; only the SOURCE differs. This version reads the
-- stock_movements ledger; the canonical version reads the stock_balance snapshot at
-- MAX(as_of_date). The ledger basis was measured ~8.6x OVERSTATED (see the header of
-- stock_migration.sql). Never run this block against a live DB to "refresh" the view.
-- DISTINCT ON implements MAX(id) per group (data rule 6).
CREATE OR REPLACE VIEW v_current_stock AS
SELECT DISTINCT ON (sm.item_name, sm.warehouse_name)
    sm.item_name,
    sm.warehouse_name,
    sm.balance_qty,
    sm.balance_value_bhd,
    sm.avg_rate_bhd,
    sm.move_date                                   AS as_of_date,
    p.sku_code,
    p.item_name                                    AS product_name,
    cat.name                                       AS category_name,
    (sm.balance_qty IS NOT NULL AND sm.balance_qty <= 10) AS is_low_stock
FROM stock_movements sm
LEFT JOIN product_aliases pa  ON pa.alias_text = sm.item_name
LEFT JOIN products        p   ON p.id          = pa.product_id
LEFT JOIN categories      cat ON cat.id        = p.category_id
ORDER BY sm.item_name, sm.warehouse_name, sm.id DESC;

-- v_product_margin: Focus COGS basis (data rule 1) --------
-- ⚠️ SUPERSEDED at runtime by scripts/stock_migration.sql — AND THIS ONE REGRESSES
-- SILENTLY. Identical column list, so CREATE OR REPLACE SUCCEEDS with no error. The
-- canonical version restricts to the newest report only:
--     where pp.report_date = (select max(report_date) from product_profitability)
-- Without that filter this view sums EVERY loaded period, double-counting margin and
-- breaking the verified "below-cost items" figure. Never run this block on a live DB.
CREATE OR REPLACE VIEW v_product_margin AS
SELECT
    pp.item_name,
    pp.report_date,
    pp.gross_bhd,
    pp.discount_pct,
    pp.net_amount_bhd,
    pp.cogs_bhd,
    pp.gross_profit_bhd,
    pp.gp_margin_pct,
    pp.misc_charges_bhd,
    pp.net_profit_bhd,
    pp.np_margin_pct,
    p.sku_code,
    p.item_name                                    AS product_name,
    cat.name                                       AS category_name,
    sp.rate_bhd                                    AS list_price_bhd
FROM product_profitability pp
LEFT JOIN product_aliases pa  ON pa.alias_text = pp.item_name
LEFT JOIN products        p   ON p.id          = pa.product_id
LEFT JOIN categories      cat ON cat.id        = p.category_id
LEFT JOIN LATERAL (
    SELECT rate_bhd FROM selling_prices
    WHERE sku_code = p.sku_code
      AND price_book = 'MA_base'
      AND (customer_code IS NULL OR customer_code = '')
    ORDER BY id DESC
    LIMIT 1
) sp ON true;

-- v_receivables: latest outstanding balance per account ---
-- SUPERSEDED at runtime: the canonical, bucket-based definition lives in
-- receivables_consolidation_migration.sql (sourced from ar_ageing, adds
-- current_bhd/overdue_bhd/over_90_bhd). This ledger-based version exists only so a
-- fresh DB bootstraps before migrations run — apply the migration right after.
CREATE OR REPLACE VIEW v_receivables AS
WITH latest AS (
    SELECT DISTINCT ON (account)
        account,
        entry_date  AS last_entry_date,
        balance_bhd AS outstanding_bhd,
        salesman,
        narration   AS last_narration
    FROM ledger_entries
    WHERE balance_bhd IS NOT NULL
    ORDER BY account, id DESC
)
SELECT
    account,
    last_entry_date,
    outstanding_bhd,
    salesman,
    last_narration,
    (CURRENT_DATE - last_entry_date) AS days_outstanding
FROM latest
WHERE outstanding_bhd > 0
ORDER BY outstanding_bhd DESC;

-- v_top_customers: revenue ranking ------------------------
CREATE OR REPLACE VIEW v_top_customers AS
SELECT
    COALESCE(o.customer_name, ol.customer_account)                      AS customer_name,
    COUNT(DISTINCT ol.invoice_no)                                        AS order_count,
    SUM(ol.quantity)                                                     AS total_qty,
    SUM(ol.gross_bhd)                                                    AS gross_bhd,
    SUM(ol.discount_bhd)                                                 AS total_discount_bhd,
    SUM(COALESCE(ol.total_amount_bhd, ol.taxable_bhd, ol.gross_bhd))    AS total_revenue_bhd,
    MIN(COALESCE(ol.line_date, o.order_date))                           AS first_order_date,
    MAX(COALESCE(ol.line_date, o.order_date))                           AS last_order_date
FROM order_lines ol
LEFT JOIN orders o ON o.invoice_no = ol.invoice_no
WHERE COALESCE(o.customer_name, ol.customer_account) IS NOT NULL
GROUP BY COALESCE(o.customer_name, ol.customer_account)
ORDER BY total_revenue_bhd DESC NULLS LAST;

-- v_sales_by_period: monthly trend -----------------------
CREATE OR REPLACE VIEW v_sales_by_period AS
SELECT
    DATE_TRUNC('month', COALESCE(ol.line_date, o.order_date))::date AS period_month,
    COUNT(DISTINCT ol.invoice_no)    AS order_count,
    COUNT(*)                         AS line_count,
    SUM(ol.quantity)                 AS total_qty,
    SUM(ol.gross_bhd)                AS gross_bhd,
    SUM(ol.discount_bhd)             AS total_discount_bhd,
    SUM(COALESCE(ol.total_amount_bhd, ol.taxable_bhd, ol.gross_bhd)) AS net_revenue_bhd,
    SUM(ol.vat_amount_bhd)           AS total_vat_bhd
FROM order_lines ol
LEFT JOIN orders o ON o.invoice_no = ol.invoice_no
WHERE COALESCE(ol.line_date, o.order_date) IS NOT NULL
GROUP BY DATE_TRUNC('month', COALESCE(ol.line_date, o.order_date))
ORDER BY period_month;

-- v_low_stock: items at or below 10 units ----------------
-- SUPERSEDED at runtime: the canonical definition lives in
-- lowstock_unification_migration.sql, which adds sold_90d, days_cover,
-- suggested_reorder_qty and status (the flat <=10 rule below is only a bootstrap
-- placeholder). Exists so a fresh DB bootstraps before migrations run.
CREATE OR REPLACE VIEW v_low_stock AS
SELECT
    item_name,
    product_name,
    sku_code,
    category_name,
    warehouse_name,
    balance_qty,
    balance_value_bhd,
    as_of_date
FROM v_current_stock
WHERE balance_qty IS NOT NULL AND balance_qty <= 10
ORDER BY balance_qty ASC;

-- ============================================================
-- Read-only role for Phase 1 /ask query path
-- ============================================================
DO $$ BEGIN
    CREATE ROLE yq_readonly NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

GRANT USAGE ON SCHEMA public TO yq_readonly;
GRANT SELECT ON
    v_sales, v_current_stock, v_product_margin, v_receivables,
    v_top_customers, v_sales_by_period, v_low_stock, shipments
TO yq_readonly;

-- ============================================================
-- Safe SQL executor for the AI /ask query path (Phase 1)
-- Only callable via service_role key (backend only).
-- Input SQL must be pre-validated by app/sql_validator.py.
-- ============================================================
CREATE OR REPLACE FUNCTION run_readonly_query(sql_text text)
RETURNS json
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE result json;
BEGIN
    EXECUTE format(
        'SELECT COALESCE(json_agg(t), ''[]''::json) FROM (%s) t',
        sql_text
    ) INTO result;
    RETURN result;
END;
$$;

REVOKE EXECUTE ON FUNCTION run_readonly_query(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION run_readonly_query(text) FROM anon;
REVOKE EXECUTE ON FUNCTION run_readonly_query(text) FROM authenticated;
GRANT  EXECUTE ON FUNCTION run_readonly_query(text) TO service_role;
