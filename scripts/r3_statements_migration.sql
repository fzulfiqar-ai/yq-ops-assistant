-- Kickback statements, release R3a (24-Sep-2026): who paid, who superseded, and why — and the
-- two rules the database enforces on its own. Additive, idempotent, no grants to anon/authenticated.
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
--
-- Uniqueness (re-review, 24-Sep-2026). M8 shipped UNIQUE (salesman, period, basis, status,
-- data_through). Superseding is a routine move, so two retired rows of one rep, month and data
-- date collided on it (23505 half-way through "Create draft"). It is replaced by two partial
-- unique indexes that say what the business rule actually is:
--   * one_closed: a rep, month and basis are approved or paid ONCE. The code refuses a second
--     approval and names the row to supersede; this index catches the race it cannot see.
--   * one_per_data_date: at most one draft / approved / paid row per rep, month, basis and
--     data date — a double-click or a double CLI run cannot write the same day twice. Superseded
--     rows are outside it (as many as the month's history needs) and so are snapshots (documented
--     moments outside the payable chain; close_kickback_month.py refuses a duplicate snapshot
--     itself before inserting).

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

-- the M8 constraint, found by its definition (Postgres truncated the auto-generated name)
do $$
declare
  c text;
begin
  for c in
    select conname from pg_constraint
    where conrelid = 'public.salesman_kickback_statements'::regclass and contype = 'u'
      and pg_get_constraintdef(oid) = 'UNIQUE (salesman, period, basis, status, data_through)'
  loop
    execute format('alter table salesman_kickback_statements drop constraint %I', c);
    raise notice 'r3_statements: dropped M8 constraint %', c;
  end loop;
end $$;

create unique index if not exists salesman_kickback_statements_one_closed_idx
  on salesman_kickback_statements (salesman, period, basis)
  where status in ('approved', 'paid');

create unique index if not exists salesman_kickback_statements_one_per_data_date_idx
  on salesman_kickback_statements (salesman, period, basis, data_through)
  where status in ('draft', 'approved', 'paid');

comment on index salesman_kickback_statements_one_closed_idx is
  'A rep, month and basis are approved or paid once (R3a). Supersede the old row before approving a new draft.';
comment on index salesman_kickback_statements_one_per_data_date_idx is
  'One draft/approved/paid row per rep, month, basis and data date (R3a); superseded rows and snapshots are free.';

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
  select count(*) into n from pg_indexes where schemaname = 'public'
    and indexname in ('salesman_kickback_statements_rep_status_idx', 'salesman_kickback_statements_one_closed_idx',
                      'salesman_kickback_statements_one_per_data_date_idx');
  if n < 3 then
    raise exception 'r3_statements: only %/3 indexes present', n;
  end if;
  if exists (select 1 from pg_constraint
             where conrelid = 'public.salesman_kickback_statements'::regclass and contype = 'u'
               and pg_get_constraintdef(oid) = 'UNIQUE (salesman, period, basis, status, data_through)') then
    raise exception 'r3_statements: the M8 unique constraint is still there';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_name = 'salesman_kickback_statements' and grantee in ('anon', 'authenticated')) then
    raise exception 'r3_statements: salesman_kickback_statements must not be granted to anon/authenticated';
  end if;
end $$;
