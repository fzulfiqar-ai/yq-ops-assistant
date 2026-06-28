-- Real landed costs from Material Receipt Notes (MRN) — the source of truth.
-- Keyed on the FULL Focus ProdCode (e.g. 'X01 UC', 'X05 UL-1Mtr'), so cable variants
-- (UC vs UL vs 3-in-1) never get conflated. landed_cost_bhd = StockValue ÷ Quantity from
-- the receipt, i.e. supplier price + vendor freight + 3rd-party freight + customs + misc, all in.
-- Populated by scripts/ingest_mrn.py from the Transactions_*.xml exports. Run once.

CREATE TABLE IF NOT EXISTS mrn_landed_costs (
  id              serial PRIMARY KEY,
  sku_code        text UNIQUE NOT NULL,
  landed_cost_bhd numeric,            -- StockValue ÷ Qty (supplier price + ALL freight/charges)
  product_cost_bhd numeric,           -- Gross ÷ Qty (supplier price only, freight excluded)
  last_qty        numeric,
  doc_no          text,
  effective_date  date,
  created_at      timestamptz DEFAULT now()
);
ALTER TABLE mrn_landed_costs ADD COLUMN IF NOT EXISTS product_cost_bhd numeric;
