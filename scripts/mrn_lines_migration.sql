-- Per-receipt MRN lines — one row per (MRN doc, SKU), so an order's RECEIVED side is self-contained
-- from the MRN XML upload (qty + real landed/product cost), independent of the daily Stock-ledger.
-- Cross-checked against the `shipments` view. `mrn_landed_costs` stays the deduped 'current cost'
-- lookup; this keeps the full per-order detail. Populated by scripts/ingest_mrn.py. Run once.

CREATE TABLE IF NOT EXISTS mrn_lines (
  id               serial PRIMARY KEY,
  doc_no           text NOT NULL,            -- = the order number (PO number)
  sku_code         text NOT NULL,            -- full Focus ProdCode (e.g. 'X01 UC')
  qty              numeric,
  landed_unit_bhd  numeric,                  -- StockValue ÷ Qty (all freight in)
  product_unit_bhd numeric,                  -- Gross ÷ Qty (supplier price only)
  created_at       timestamptz DEFAULT now(),
  UNIQUE (doc_no, sku_code)
);
CREATE INDEX IF NOT EXISTS mrn_lines_doc ON mrn_lines (doc_no);
