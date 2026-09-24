-- Reverse of r3_statements_migration.sql. Drops only the R3a metadata columns and indexes and
-- puts the M8 unique constraint back; no statement row is deleted or changed. Take
-- `python -m scripts.db_backup --tables salesman_kickback_statements` first if any row has been
-- superseded or paid through the API, because the who/when/why of those moves lives in these
-- columns (the audit_log rows remain). The API keeps working without them (it retries writes
-- without the optional columns).
--
-- The M8 constraint UNIQUE (salesman, period, basis, status, data_through) cannot hold two
-- superseded rows of one rep, month and data date. If the R3a code has produced such rows the
-- constraint is NOT recreated (a NOTICE says so) — the rows stay, and app/statements.py still
-- refuses a second approval in code.
drop index if exists salesman_kickback_statements_one_per_data_date_idx;
drop index if exists salesman_kickback_statements_one_closed_idx;
drop index if exists salesman_kickback_statements_rep_status_idx;
do $$
begin
  if not exists (select 1 from pg_constraint
                 where conrelid = 'public.salesman_kickback_statements'::regclass and contype = 'u'
                   and pg_get_constraintdef(oid) = 'UNIQUE (salesman, period, basis, status, data_through)') then
    begin
      alter table salesman_kickback_statements
        add constraint salesman_kickback_statements_salesman_period_basis_status_d_key
        unique (salesman, period, basis, status, data_through);
    exception when unique_violation then
      raise notice 'r3_statements reverse: superseded rows share a data date; the M8 constraint was not recreated';
    end;
  end if;
end $$;
alter table salesman_kickback_statements drop column if exists superseded_by_id;
alter table salesman_kickback_statements drop column if exists superseded_reason;
alter table salesman_kickback_statements drop column if exists superseded_by;
alter table salesman_kickback_statements drop column if exists superseded_at;
alter table salesman_kickback_statements drop column if exists paid_by;
