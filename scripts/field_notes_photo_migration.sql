-- Field Notes photo capture — let reps attach a picture (competitor price tag,
-- empty shelf, a new product on a shelf) for richer ground-truth feedback.
--
-- Image bytes live in a PRIVATE Supabase Storage bucket ('field-notes'); this column
-- stores only the object PATH. The API hands out short-lived SIGNED URLs at read time,
-- so photos stay access-controlled (Bahrain PDPL: shop-fronts/people may appear).
--
-- The bucket itself is created by the API on first upload (app/field_notes.ensure_bucket),
-- so nothing to do in the Supabase dashboard. Run this once in the SQL editor.

ALTER TABLE field_notes ADD COLUMN IF NOT EXISTS image_path text;
