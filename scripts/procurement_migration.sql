-- Procurement view: dated purchase (goods-received) history per item, with the VENDOR
-- promoted from the "(VFAN)" tag embedded in the item name + the shipments/<VENDOR>/ folders.
-- Answers "when did we last buy X and at what cost", "did the cost go up/down", "which
-- vendor", "what did we order/receive". Future vendors = same tag, zero code change.

CREATE OR REPLACE VIEW v_purchase_history AS
SELECT
  -- a tidy display name: drop the trailing duplicated SKU tokens Focus appends
  trim(regexp_replace(item_name, '\s*\(([A-Za-z0-9]+)\)\s*.*$', '')) AS item_name,
  item_name                              AS raw_item_name,
  received_date                          AS purchased_on,
  received_qty                           AS qty,
  received_rate_bhd                      AS cost_bhd,     -- what we PAID per unit (BHD)
  received_value_bhd                     AS value_bhd,
  COALESCE(NULLIF(substring(item_name from '\(([A-Za-z]{2,8})\)'), ''), 'Other') AS vendor,
  mrn_no,
  warehouse_name
FROM shipments
WHERE received_rate_bhd IS NOT NULL
  AND received_rate_bhd > 0;

-- Latest vs previous purchase cost per item = cost change signal (↑/↓ on every new shipment).
CREATE OR REPLACE VIEW v_cost_change AS
WITH ranked AS (
  SELECT
    trim(regexp_replace(item_name, '\s*\(([A-Za-z0-9]+)\)\s*.*$', '')) AS item_name,
    COALESCE(NULLIF(substring(item_name from '\(([A-Za-z]{2,8})\)'), ''), 'Other') AS vendor,
    received_date,
    received_rate_bhd,
    ROW_NUMBER() OVER (PARTITION BY trim(regexp_replace(item_name, '\s*\(([A-Za-z0-9]+)\)\s*.*$', ''))
                       ORDER BY received_date DESC) AS rn
  FROM shipments
  -- exclude nominal/free lines (display stands, FOC samples at ~0.01) so cost trends are real
  WHERE received_rate_bhd IS NOT NULL AND received_rate_bhd >= 0.05
)
SELECT
  cur.item_name,
  cur.vendor,
  cur.received_date            AS last_bought_on,
  cur.received_rate_bhd        AS current_cost_bhd,
  prev.received_date           AS prev_bought_on,
  prev.received_rate_bhd       AS prev_cost_bhd,
  ROUND((cur.received_rate_bhd - prev.received_rate_bhd)::numeric, 3) AS cost_delta_bhd,
  CASE WHEN prev.received_rate_bhd > 0
       THEN ROUND(100.0 * (cur.received_rate_bhd - prev.received_rate_bhd) / prev.received_rate_bhd, 1)
  END                          AS cost_change_pct
FROM ranked cur
JOIN ranked prev ON prev.item_name = cur.item_name AND prev.rn = 2
WHERE cur.rn = 1;   -- only items bought 2+ times → an actual cost change exists
