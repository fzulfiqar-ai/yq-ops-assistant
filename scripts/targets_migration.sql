-- Sales targets — company monthly target lives in app_settings
-- (monthly_sales_target_bhd); per-salesman monthly targets live here and are
-- edited from Settings → Sales targets. Idempotent.

create table if not exists salesman_targets (
  salesman    text primary key,
  target_bhd  numeric not null default 0,
  updated_by  text,
  updated_at  timestamptz default now()
);

alter table salesman_targets enable row level security;
drop policy if exists salesman_targets_read on salesman_targets;
create policy salesman_targets_read on salesman_targets for select to authenticated using (true);

grant select on salesman_targets to yq_readonly;
