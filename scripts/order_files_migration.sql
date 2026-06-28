-- Order file vault — the actual uploaded documents per order (PO PDF, MRN XML/PDF, shelf photos),
-- so an order shows everything in one place. Bytes live in a PRIVATE Supabase Storage bucket
-- ('orders'); this table stores only the object path. The API hands out short-lived signed URLs.
-- Populated on PO/MRN upload, the photo endpoint, and the one-off backfill. Run once.

CREATE TABLE IF NOT EXISTS order_files (
  id          serial PRIMARY KEY,
  po_no       text NOT NULL,                 -- the order number (PO = MRN number)
  kind        text NOT NULL,                 -- 'po' | 'mrn' | 'photo'
  path        text NOT NULL,                 -- object path in the private 'orders' bucket
  filename    text,
  uploaded_by text,
  created_at  timestamptz DEFAULT now(),
  UNIQUE (path)
);
CREATE INDEX IF NOT EXISTS order_files_po ON order_files (po_no);
