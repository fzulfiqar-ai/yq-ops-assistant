-- Clean current selling-price list per SKU, so the AI can answer "price of X" reliably.
-- selling_prices has price HISTORY (many dated rows per SKU) across two price books
-- (MA_base = standard, modern_trade = retail/MT). We surface ONE current price per SKU:
-- the latest-dated, still-valid row, preferring the MA_base (standard) book.
--
-- ⚠️ WAREHOUSE LAYERS (fixed 06-Sep-2026 — this was wrong and it was visible to customers).
-- The MA_base book is NOT one price per SKU. It holds THREE layers in one book:
--     warehouse_name IS NULL  -> the DEALER / list price   (the B2B price)   293 rows
--     warehouse_name='Causeway'    -> outlet RETAIL price                    181 rows
--     warehouse_name='YQ Roadshow' -> outlet RETAIL price                    180 rows
-- These share a start_date, so the old tie-break `rate_bhd DESC` deterministically picked
-- the RETAIL price as the B2B price on 89 of 170 SKUs — median +77%, max +233%
-- (BE01 dealer 3.10 shown as 5.50; L11 0.30 shown as 1.00). Causeway is 4.6% of revenue,
-- so 95% of the business was being priced off the 5% channel's list.
-- Ground truth: Focus's own Stock-balance "Selling Rate" matches the blank-warehouse base
-- row on 69/69 SKUs and an outlet row on 0. NEVER tie-break a price by rate — order by
-- (warehouse_name IS NULL) first, then start_date, then id. modern_trade has warehouse_name
-- set on ALL 167 rows, so the predicate is uniformly false there and it falls through
-- correctly to latest-dated.
-- See also norm_date_us() in scripts/ingest.py — PriceBook dates are M/D/YYYY, not D/M/YYYY.

CREATE OR REPLACE VIEW v_price_list AS
WITH cur AS (
  SELECT
    sku_code,
    item_name,
    price_book,
    rate_bhd,
    unit_name,
    start_date,
    end_date,
    ROW_NUMBER() OVER (
      PARTITION BY sku_code
      ORDER BY (price_book = 'MA_base') DESC, (warehouse_name IS NULL) DESC,
               start_date DESC, id DESC
    ) AS rn
  FROM selling_prices
  WHERE status = 'Authorized'
    AND rate_bhd IS NOT NULL
    AND rate_bhd > 0
    AND start_date <= CURRENT_DATE
    AND (end_date IS NULL OR end_date >= CURRENT_DATE)
)
SELECT
  sku_code,
  item_name,
  rate_bhd      AS price_bhd,     -- current standard selling price (BHD)
  unit_name,
  price_book
FROM cur
WHERE rn = 1;

-- Both books per SKU (base vs modern-trade) for "what's the modern trade price" questions.
CREATE OR REPLACE VIEW v_price_list_by_book AS
WITH cur AS (
  SELECT
    sku_code, item_name, price_book, rate_bhd, unit_name,
    ROW_NUMBER() OVER (
      PARTITION BY sku_code, price_book
      ORDER BY (warehouse_name IS NULL) DESC, start_date DESC, id DESC
    ) AS rn
  FROM selling_prices
  WHERE status = 'Authorized'
    AND rate_bhd IS NOT NULL AND rate_bhd > 0
    AND start_date <= CURRENT_DATE
    AND (end_date IS NULL OR end_date >= CURRENT_DATE)
)
SELECT sku_code, item_name, price_book, rate_bhd AS price_bhd, unit_name
FROM cur
WHERE rn = 1;

-- Per-SKU economics: current selling price vs latest landed cost = unit margin.
-- The one place to answer "what's my margin on X", "which SKUs are thin/loss-making at
-- current price", "price vs cost". Cost is null where no purchase cost is on file.
CREATE OR REPLACE VIEW v_product_economics AS
SELECT
  pl.sku_code,
  pl.item_name,
  pl.price_bhd,
  pc.landed_cost_bhd                                   AS cost_bhd,
  ROUND((pl.price_bhd - pc.landed_cost_bhd)::numeric, 3) AS margin_bhd,
  CASE WHEN pl.price_bhd > 0 AND pc.landed_cost_bhd IS NOT NULL
       THEN ROUND(100.0 * (pl.price_bhd - pc.landed_cost_bhd) / pl.price_bhd, 1)
  END                                                  AS margin_pct
FROM v_price_list pl
LEFT JOIN LATERAL (
  SELECT landed_cost_bhd
  FROM purchase_costs pc
  WHERE pc.sku_code = pl.sku_code
  ORDER BY effective_date DESC NULLS LAST
  LIMIT 1
) pc ON TRUE;

-- Price HISTORY: prices change over time — one row per distinct dated price per SKU/book,
-- so the AI can answer "did X's price change", "what was the old price", "price trend".
-- Newest first; current price = the top row per (sku, price_book).
CREATE OR REPLACE VIEW v_price_history AS
SELECT DISTINCT
  sku_code,
  item_name,
  price_book,
  start_date     AS effective_from,
  end_date       AS effective_to,
  rate_bhd       AS price_bhd,
  unit_name
FROM selling_prices
WHERE status = 'Authorized'
  AND rate_bhd IS NOT NULL
  AND rate_bhd > 0;

-- Latest vs previous DEALER selling price per SKU (MA_base, base/list rows) = price change signal.
-- `warehouse_name IS NULL` is load-bearing: without it the DISTINCT keeps the Causeway and
-- YQ Roadshow retail rows, which share a start_date with the dealer row, so rn=1 was the retail
-- price and rn=2 the dealer price for the SAME day — reporting a price RISE that never happened.
-- Measured on the 23-Jun-2026 book: 166 SKUs flagged as "changed", 71 of them phantom
-- (BE01 3.10->5.50 +77%, BS07 6.90->12.00 +74%). Filtered: 95 real changes, 0 same-day artifacts.
-- This view feeds the Price Tracker page, catalog_watch and price_drift — all were wrong.
CREATE OR REPLACE VIEW v_price_change AS
WITH distinct_prices AS (
  SELECT DISTINCT sku_code, item_name, start_date, rate_bhd
  FROM selling_prices
  WHERE status='Authorized' AND price_book='MA_base'
    AND warehouse_name IS NULL
    AND rate_bhd IS NOT NULL AND rate_bhd > 0
    AND start_date <= CURRENT_DATE
),
ranked AS (
  SELECT sku_code, item_name, start_date, rate_bhd,
         ROW_NUMBER() OVER (PARTITION BY sku_code ORDER BY start_date DESC, rate_bhd DESC) AS rn
  FROM distinct_prices
)
SELECT
  cur.sku_code, cur.item_name,
  cur.start_date  AS changed_on,
  cur.rate_bhd    AS current_price_bhd,
  prev.rate_bhd   AS prev_price_bhd,
  ROUND((cur.rate_bhd - prev.rate_bhd)::numeric, 3) AS price_delta_bhd,
  CASE WHEN prev.rate_bhd > 0
       THEN ROUND(100.0*(cur.rate_bhd - prev.rate_bhd)/prev.rate_bhd, 1) END AS price_change_pct
FROM ranked cur
JOIN ranked prev ON prev.sku_code = cur.sku_code AND prev.rn = 2
WHERE cur.rn = 1 AND cur.rate_bhd <> prev.rate_bhd;
