
-- ============================================================
-- YQ Bahrain ops assistant — Phase 0.5 semantic views (idempotent)
-- LLM queries ONLY these views — never raw tables.
-- ============================================================

-- v_sales: enriched sales lines ---------------------------
CREATE OR REPLACE VIEW v_sales AS
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
