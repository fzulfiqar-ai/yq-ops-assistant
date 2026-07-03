-- Business settings — single source for the numbers that were hardcoded in code
-- (order costing FX chain, landing+VAT uplift, target markup) plus sales targets.
-- Owner's costing method (VFAN New Order Pricing sheet):
--   RMB list ÷ (1+dealer_discount... actually ÷1.18) → ÷ fx_rmb_usd → × fx_usd_bhd = base BHD
--   base × (1 + landing_vat_pct) = landed cost → landed × (1 + target_markup) = sell.
-- Idempotent; safe to re-run.

create table if not exists app_settings (
  key         text primary key,
  value       text not null,
  description text,
  updated_by  text,
  updated_at  timestamptz default now()
);

alter table app_settings enable row level security;
drop policy if exists app_settings_read on app_settings;
create policy app_settings_read on app_settings for select to authenticated using (true);

insert into app_settings (key, value, description) values
  ('fx_rmb_usd',              '6.8',     'RMB per USD (order costing)'),
  ('fx_usd_bhd',              '0.37744', 'BHD per USD'),
  ('dealer_discount',         '0.18',    'VFAN dealer discount off list (net = list / 1.18)'),
  ('landing_vat_pct',         '0.30',    'Landing + VAT uplift on base BHD cost (0.30 = 20% + 10%)'),
  ('target_markup',           '0.70',    'Target markup on landed cost'),
  ('monthly_sales_target_bhd','0',       'Company monthly gross sales target in BHD (0 = unset)')
on conflict (key) do nothing;

grant select on app_settings to yq_readonly;
