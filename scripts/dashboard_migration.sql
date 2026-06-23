-- Dashboard / correctness migration.
-- Fixes receivables (was summing the whole trial balance) by sourcing from the
-- authoritative Customer ageing report, and adds the AR ageing table.
-- Idempotent; safe to re-run.

-- 1) Authoritative trade-debtor receivables (one row per customer, from Focus AR).
create table if not exists ar_ageing (
  id                bigint generated always as identity primary key,
  account           text not null,
  account_code      text,
  group_name        text,
  balance_bhd       numeric,
  bucket_0_30       numeric,
  bucket_31_60      numeric,
  bucket_61_90      numeric,
  bucket_91_120     numeric,
  bucket_121_150    numeric,
  bucket_151_180    numeric,
  bucket_181_210    numeric,
  bucket_over_210   numeric,
  total_bhd         numeric,
  last_receipt_date date,
  as_of_date        date,
  source_file       text,
  imported_at       timestamptz default now(),
  unique (account, as_of_date)
);
create index if not exists ar_ageing_asof_idx on ar_ageing (as_of_date);

alter table ar_ageing enable row level security;
drop policy if exists ar_ageing_read on ar_ageing;
create policy ar_ageing_read on ar_ageing for select to authenticated using (true);

-- 2) Rebuild v_receivables from the AR report (column shape changes → drop+create).
drop view if exists v_receivables cascade;
create view v_receivables as
select
  account,
  account_code,
  group_name,
  balance_bhd                              as outstanding_bhd,
  coalesce(bucket_0_30, 0)                 as b_0_30,
  coalesce(bucket_31_60, 0)                as b_31_60,
  coalesce(bucket_61_90, 0)                as b_61_90,
  coalesce(bucket_91_120, 0)               as b_91_120,
  coalesce(bucket_121_150, 0)              as b_121_150,
  coalesce(bucket_151_180, 0)              as b_151_180,
  coalesce(bucket_181_210, 0)              as b_181_210,
  coalesce(bucket_over_210, 0)             as b_over_210,
  -- amount past 30 days (every bucket except the freshest)
  (coalesce(bucket_31_60,0)+coalesce(bucket_61_90,0)+coalesce(bucket_91_120,0)
   +coalesce(bucket_121_150,0)+coalesce(bucket_151_180,0)+coalesce(bucket_181_210,0)
   +coalesce(bucket_over_210,0))           as overdue_bhd,
  -- amount aged beyond 90 days (collection risk)
  (coalesce(bucket_91_120,0)+coalesce(bucket_121_150,0)+coalesce(bucket_151_180,0)
   +coalesce(bucket_181_210,0)+coalesce(bucket_over_210,0)) as over_90_bhd,
  last_receipt_date,
  as_of_date
from ar_ageing
where as_of_date = (select max(as_of_date) from ar_ageing)
  and balance_bhd is not null
  and balance_bhd <> 0
order by balance_bhd desc;

-- 3) Re-grant to the optional read-only role if it exists.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on v_receivables to yq_readonly;
  end if;
end $$;
