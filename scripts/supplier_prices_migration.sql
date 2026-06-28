-- Supplier price history — the RMB unit prices from the VFAN proforma invoices (PDF + .xls),
-- so we can see when a supplier price changed (e.g. "F15 was ¥68 in Jul-25, ¥X in Feb-26").
-- model = the leading product code (F15, F30). net_price_rmb = price after the 18% discount.
-- Populated by scripts/ingest_invoices.py + POST /invoices/upload. Run once.

CREATE TABLE IF NOT EXISTS supplier_prices (
  id              serial PRIMARY KEY,
  model           text NOT NULL,
  spec            text,
  qty             numeric,
  unit_price_rmb  numeric,           -- list price (before discount)
  net_price_rmb   numeric,           -- after 18% discount (the real cost basis)
  invoice_no      text,
  invoice_date    date,
  vendor          text DEFAULT 'VFAN',
  source_file     text,
  created_at      timestamptz DEFAULT now(),
  UNIQUE (invoice_no, model, spec)
);
CREATE INDEX IF NOT EXISTS supplier_prices_model ON supplier_prices (model, invoice_date);

-- Latest vs previous supplier price per model = the price change (like v_po_cost_change but in RMB).
CREATE OR REPLACE VIEW v_supplier_price_history AS
WITH per_inv AS (
  SELECT model, invoice_no, MAX(invoice_date) AS invoice_date,
         ROUND(AVG(net_price_rmb)::numeric, 2)  AS net_rmb,
         ROUND(AVG(unit_price_rmb)::numeric, 2) AS list_rmb
  FROM supplier_prices
  WHERE model IS NOT NULL AND net_price_rmb > 0
  GROUP BY model, invoice_no
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY model ORDER BY invoice_date DESC, invoice_no DESC) AS rn
  FROM per_inv
)
SELECT cur.model,
       cur.invoice_no     AS latest_invoice,
       cur.invoice_date   AS latest_date,
       cur.net_rmb        AS latest_rmb,
       cur.list_rmb       AS latest_list_rmb,
       prev.net_rmb       AS prev_rmb,
       prev.invoice_date  AS prev_date,
       CASE WHEN prev.net_rmb > 0
            THEN ROUND(100.0 * (cur.net_rmb - prev.net_rmb) / prev.net_rmb, 1) END AS change_pct,
       (SELECT COUNT(DISTINCT invoice_no) FROM supplier_prices sp WHERE sp.model = cur.model) AS invoice_count
FROM ranked cur
LEFT JOIN ranked prev ON prev.model = cur.model AND prev.rn = 2
WHERE cur.rn = 1;
