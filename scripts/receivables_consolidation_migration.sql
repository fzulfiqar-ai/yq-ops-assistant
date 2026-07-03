-- Receivables consolidation — ONE canonical v_receivables.
-- Basis: the Focus "Customer summary ageing by due date" report (ar_ageing table,
-- created by dashboard_migration.sql — run that first on a fresh DB).
-- Supersedes the ledger-based definition in views.sql and the earlier rebuild in
-- dashboard_migration.sql. Adds current_bhd (the 0-30 bucket) so the dashboard can
-- show Total = Current + Overdue on exactly the same basis the collections agent uses.
-- Idempotent; safe to re-run.

drop view if exists v_receivables cascade;
create view v_receivables as
select
  account,
  account_code,
  group_name,
  balance_bhd                              as outstanding_bhd,
  -- not yet due / freshest bucket (0-30 days by due date)
  coalesce(bucket_0_30, 0)                 as current_bhd,
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

grant select on v_receivables to yq_readonly;
