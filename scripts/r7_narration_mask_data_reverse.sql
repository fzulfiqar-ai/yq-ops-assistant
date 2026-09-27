-- "Reverse" of r7_narration_mask_data_migration.sql: THERE IS NONE IN SQL, by design.
--
-- The data step replaced the digits in order_lines.narration with stars; the database no longer
-- holds them anywhere (keeping a copy would defeat the purpose). The only ways back:
--   1. the backup taken BEFORE the data step
--      (python -m scripts.db_backup --tables order_lines --out business_data/backups/<date>_pre-narration-mask):
--      restore the narration column from it by hand, row by row on (invoice_no, line_no) — never
--      `db_backup --restore` of the whole table, which refuses anyway when newer lines exist;
--   2. nothing else: the Focus exports still carry the numbers, but scripts/ingest.py masks them on
--      every load, so a re-upload brings back the masked text.
-- Reverting the VIEW (scripts/r7_narration_mask_reverse.sql) does not bring the digits back either.
--
-- This file changes nothing and stops here so that running it can never be mistaken for a restore.
do $$
begin
  raise exception 'r7_narration_mask_data_migration.sql cannot be reversed from SQL: restore order_lines.narration from the pre-apply db_backup (see this file''s header).';
end $$;
