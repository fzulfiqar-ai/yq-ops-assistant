-- Kickback statements, release R3a (24-Sep-2026): who paid, who superseded, and why. Additive,
-- idempotent, no grants to anon/authenticated.
--   Rehearse: python -m scripts.apply_sql scripts/r3_statements_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r3_statements_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r3_statements_reverse.sql
--
-- Needs salesman_kickback_statements (scripts/kickback_statements_migration.sql, M8). The API
-- (app/statements.py) writes these columns when they exist and retries without them when
-- PostgREST says they are not there yet, so the code may deploy before this file runs.
--
-- Why: a statement is never rewritten — a correction is a NEW draft and the old row moves to
-- 'superseded'. That move must say who did it, when, why and which row replaced it, or the chain
-- of a rep's month cannot be audited. 'paid' likewise records who marked it.

alter table salesman_kickback_statements add column if not exists paid_by            text;
alter table salesman_kickback_statements add column if not exists superseded_at      timestamptz;
alter table salesman_kickback_statements add column if not exists superseded_by      text;
alter table salesman_kickback_statements add column if not exists superseded_reason  text;
alter table salesman_kickback_statements add column if not exists superseded_by_id   bigint
  references salesman_kickback_statements(id) on delete restrict;

comment on column salesman_kickback_statements.paid_by is 'Login that marked the statement paid (R3a).';
comment on column salesman_kickback_statements.superseded_by_id is
  'The newer statement that replaced this one (R3a). NULL unless status = superseded.';

-- the rep card reads "my latest approved/paid" and "my open draft" by (salesman, status, period)
create index if not exists salesman_kickback_statements_rep_status_idx
  on salesman_kickback_statements (salesman, status, period desc);

revoke all on salesman_kickback_statements from anon, authenticated;

do $$
declare
  n int;
begin
  if to_regclass('public.salesman_kickback_statements') is null then
    raise exception 'r3_statements: salesman_kickback_statements missing (apply kickback_statements_migration.sql first)';
  end if;
  select count(*) into n from information_schema.columns
  where table_schema = 'public' and table_name = 'salesman_kickback_statements'
    and column_name in ('paid_by', 'superseded_at', 'superseded_by', 'superseded_reason', 'superseded_by_id');
  if n < 5 then
    raise exception 'r3_statements: only %/5 new columns present', n;
  end if;
  if not exists (select 1 from pg_indexes where schemaname = 'public'
                 and indexname = 'salesman_kickback_statements_rep_status_idx') then
    raise exception 'r3_statements: index missing';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_name = 'salesman_kickback_statements' and grantee in ('anon', 'authenticated')) then
    raise exception 'r3_statements: salesman_kickback_statements must not be granted to anon/authenticated';
  end if;
end $$;
