-- Landed-cost margin (the real margin) — now sourced from MRN receipts (mrn_landed_costs),
-- the source of truth. Each sold line is matched to its cost by NORMALISED ProdCode prefix
-- (spaces/hyphens/dots stripped), longest code wins, so 'X01 UC' / 'X01 UL' / 'X16' never collide.
-- Gross profit is ex-VAT (net_bhd) minus landed cost. Items where avg selling price < landed cost
-- surface as gross_profit_bhd < 0 (the real below-cost / discount-leakage list).
--
-- Refresh costs anytime with: python -m scripts.ingest_mrn. Run this view migration once.

CREATE OR REPLACE VIEW v_landed_margin AS
WITH cost AS (
  SELECT landed_cost_bhd,
         REPLACE(REPLACE(REPLACE(UPPER(sku_code), ' ', ''), '-', ''), '.', '') AS nkey
  FROM mrn_landed_costs
  WHERE landed_cost_bhd IS NOT NULL
),
line_cost AS (
  SELECT DISTINCT ON (s.line_id)
         s.item_name, s.category_name, s.quantity, s.net_bhd, c.landed_cost_bhd
  FROM v_sales s
  JOIN cost c
    ON REPLACE(REPLACE(REPLACE(UPPER(s.item_name), ' ', ''), '-', ''), '.', '') LIKE c.nkey || '%'
  WHERE s.item_name IS NOT NULL
  ORDER BY s.line_id, LENGTH(c.nkey) DESC          -- most specific (longest) code wins
)
SELECT
  item_name,
  MAX(category_name)                                                   AS category_name,
  MAX(landed_cost_bhd)                                                 AS unit_cost_bhd,
  SUM(quantity)                                                        AS qty,
  ROUND(SUM(net_bhd)::numeric, 3)                                      AS net_revenue_bhd,
  ROUND(SUM(quantity * landed_cost_bhd)::numeric, 3)                   AS landed_cogs_bhd,
  ROUND((SUM(net_bhd) - SUM(quantity * landed_cost_bhd))::numeric, 3)  AS gross_profit_bhd,
  CASE WHEN SUM(net_bhd) > 0
       THEN ROUND(((SUM(net_bhd) - SUM(quantity * landed_cost_bhd)) / SUM(net_bhd) * 100)::numeric, 2)
       ELSE NULL END                                                   AS gp_margin_pct
FROM line_cost
GROUP BY item_name;
