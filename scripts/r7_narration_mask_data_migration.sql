-- OPTIONAL, THE OWNER DECIDES (release R7b, 27-Sep-2026): mask the personal ID numbers in the
-- STORED Sales Day Book narration too. One-off data step; idempotent; cannot be undone from SQL.
--
-- r7_narration_mask_migration.sql already masks what anyone can READ (v_sales, v_sales_agent and
-- every view on them), and scripts/ingest.py masks every narration it loads from now on. What is
-- left is the raw text already stored in order_lines.narration: 217 of 1,796 narrated lines on
-- production (read-only count, 27-Sep-2026). It is reachable only with the service key or the
-- database password — neither yq_readonly (the AI) nor the browser can read order_lines — so this
-- file is about not KEEPING the numbers at all, not about who can see them today.
--
-- BEFORE (the only way back):
--   python -m scripts.db_backup --tables order_lines --out business_data/backups/<date>_pre-narration-mask
--   That backup holds the numbers in clear: keep it out of OneDrive and the repo, and delete it once
--   the owner is satisfied. Without it the digits are gone for good — the Focus exports still carry
--   them, but scripts/ingest.py masks them again on every load.
-- Rehearse: python -m scripts.apply_sql scripts/r7_narration_mask_data_migration.sql --rehearse
-- Apply:    python -m scripts.apply_sql scripts/r7_narration_mask_data_migration.sql
-- Reverse:  scripts/r7_narration_mask_data_reverse.sql explains why there is none in SQL.
--
-- What it writes: order_lines.narration only — the same mask as v_sales and the parser (a 9-digit
-- run keeps its last 3 digits behind 6 stars, a run of 15+ digits behind 12). No other column, no
-- row inserted or deleted. The batch importer's own copies (ingest_stage / ingest_replaced rows of
-- target order_lines, empty on production today) get the same mask, so ingest_undo keeps comparing
-- like with like and a later undo cannot put a number back.
-- Effect on the importer: a Sales Day Book re-upload of those dates now reads "unchanged" for the
-- masked lines (the parser masks identically) instead of "updated".
--
-- Run it AFTER r7_narration_mask_migration.sql (the view is the part that protects readers).

update order_lines
   set narration = regexp_replace(
         regexp_replace(narration, '(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])', '************\1', 'g'),
         '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')
 where narration ~ '[0-9]{9}'
   and narration is distinct from regexp_replace(
         regexp_replace(narration, '(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])', '************\1', 'g'),
         '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g');

do $$
begin
  if to_regclass('public.ingest_stage') is not null then
    update ingest_stage
       set row = jsonb_set(row, '{narration}', to_jsonb(regexp_replace(
             regexp_replace(row ->> 'narration', '(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])', '************\1', 'g'),
             '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')))
     where target = 'order_lines' and row ->> 'narration' ~ '[0-9]{9}';
  end if;
  if to_regclass('public.ingest_replaced') is not null then
    update ingest_replaced
       set row = jsonb_set(row, '{narration}', to_jsonb(regexp_replace(
             regexp_replace(row ->> 'narration', '(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])', '************\1', 'g'),
             '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')))
     where target = 'order_lines' and row ->> 'narration' ~ '[0-9]{9}';
  end if;
end $$;

-- Self-check: no stored narration still carries a 9-digit or a 15+-digit run.
do $$
begin
  if exists (select 1 from order_lines
              where narration ~ '(^|[^0-9])[0-9]{9}([^0-9]|$)' or narration ~ '[0-9]{15,}') then
    raise exception 'order_lines.narration still carries a 9-digit or a 15+-digit number';
  end if;
  if to_regclass('public.ingest_stage') is not null and exists (
       select 1 from ingest_stage where target = 'order_lines'
          and (row ->> 'narration' ~ '(^|[^0-9])[0-9]{9}([^0-9]|$)' or row ->> 'narration' ~ '[0-9]{15,}')) then
    raise exception 'ingest_stage still carries a 9-digit or a 15+-digit narration';
  end if;
  if to_regclass('public.ingest_replaced') is not null and exists (
       select 1 from ingest_replaced where target = 'order_lines'
          and (row ->> 'narration' ~ '(^|[^0-9])[0-9]{9}([^0-9]|$)' or row ->> 'narration' ~ '[0-9]{15,}')) then
    raise exception 'ingest_replaced still carries a 9-digit or a 15+-digit narration';
  end if;
end $$;
