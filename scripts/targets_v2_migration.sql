-- Salesman targets v2 (21-Sep-2026). Idempotent.
-- Apply:  python -m scripts.apply_sql scripts/targets_v2_migration.sql
--
-- Before launch the only rows in salesman_targets were 15 placeholders seeded on 05-Jul-2026
-- ('seed 10k'), three of them for names that are not salesmen. The portal editor and every
-- target read (Today, Me, Dashboard leaderboard, Sales, /shop/me) were retired, so those rows
-- had no reader left and are removed here (backup: business_data/backups/2026-09-21_0659).
--
-- The table is kept and gains a `period` (YYYY-MM) so the owner's forthcoming target file
-- can carry one row per salesman per month: python -m scripts.import_targets <file>.
-- An empty period means "standing monthly target" (what the old single-row shape meant).

delete from salesman_targets where updated_by = 'seed 10k';

alter table salesman_targets add column if not exists period text not null default '';

comment on column salesman_targets.period is
  'YYYY-MM the target applies to; empty = standing monthly target. Loaded by scripts/import_targets.py.';

do $$
begin
  if exists (select 1 from pg_constraint where conname = 'salesman_targets_pkey'
             and conrelid = 'salesman_targets'::regclass
             and array_length(conkey, 1) = 1) then
    alter table salesman_targets drop constraint salesman_targets_pkey;
    alter table salesman_targets add constraint salesman_targets_pkey primary key (salesman, period);
  end if;
end $$;
