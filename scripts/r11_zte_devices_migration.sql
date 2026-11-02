-- Release r11 (02-Nov-2026): the ZTE handsets are Devices, never Accessories -- the same as SIM for Batelco.
--
-- WHY. The ZTE handsets (the day book's "ZTE-A35e MOBILE" and "ZTE-A36 MOBILE") are held in the accessories store
-- and handled by the accessories team, but they carry no product category, so v_sales gave their lines the residual
-- division, Accessories. Issued through the roadshow at no value (March to September 2026) they were giveaway lines,
-- and no sales figure counted them. The first invoice at a price (29-Oct-2026) was counted by every accessory figure:
-- the monthly and weekly workbooks, the brief, the KPI pace. The owner (02-Nov-2026): no ZTE sale is ever counted,
-- the same as SIM for Batelco.
--
-- WHAT. Data only: no view, no column and no grant changes.
--   1. categories: 'Mobile Handsets', division Devices (the name of the monthly workbook's handset row).
--   2. products ZTE-A35e and ZTE-A36 in it, and product_aliases for the day book's two item names. v_sales takes the
--      category's division first, so their lines are Devices, and every basis that leaves SIM and Devices out
--      (scripts/ops/sales.py and kpi.py, the weekly and monthly workbooks; the reconciliation counts Accessories
--      only) leaves them out.
--   3. division_rules: a ZTE row (Devices, ahead of the generic device row), so v_item_division resolves every ZTE
--      spelling the same way (the stock balance's, the ledger's "ZTE-A35e MOBILE ZTE-A35e", a later model's).
-- KEPT SO. An alias is inserted only where none exists, and this file refuses to run if either name already points
-- at another product (a curated alias always wins). The one thing that could move them back is
-- scripts/category_backfill.py, which re-points products by their Focus item group when a load carries a
-- Multi_level_stock_movement report (none of the six standard reports is one). Its guard (never out of a SIM /
-- Devices / Giveaway category, never a curated division re-seeded) is an engine file and ships with the next API
-- deploy (branch zte-backfill-guard). Until then, if such a report is ever loaded, this file run again puts them back.
-- KNOWN LIMIT. A later ZTE model arrives under a new item name with no alias: v_sales calls it Accessories until it
-- has its own product and alias (the monthly workbook names such an item under Other, "no rule and no card").
--
-- Idempotent: a second run changes nothing. From the main project folder:
--   python -m scripts.apply_sql scripts/r11_zte_devices_migration.sql --rehearse     (runs it, then rolls back)
--   python -m scripts.apply_sql scripts/r11_zte_devices_migration.sql
-- Reverse: scripts/r11_zte_devices_reverse.sql

set lock_timeout = '2s';

-- 0. a curated alias always wins: stop if either name already points at a product that is not a ZTE one
do $$
declare
  clash text;
begin
  select string_agg(pa.alias_text || ' -> ' || coalesce(p.sku_code, 'no product'), ', ') into clash
  from product_aliases pa
  left join products p on p.id = pa.product_id
  where pa.alias_text in ('ZTE-A35e MOBILE', 'ZTE-A36 MOBILE')
    and coalesce(p.sku_code, '') not in ('ZTE-A35e', 'ZTE-A36');
  if clash is not null then
    raise exception 'r11 refused: an alias already points at another product (%)', clash;
  end if;
end $$;

-- 1. the category (one of that name already there takes the Devices division)
insert into categories (name, division) values ('Mobile Handsets', 'Devices')
on conflict (name) do update set division = excluded.division
  where categories.division is distinct from excluded.division;

-- 2. the two products in it (a product of the same code already there moves into it)
insert into products (sku_code, item_name, category_id, unit_name, status)
select v.sku_code, v.item_name, c.id, 'Nos', 'Authorized'
from (values ('ZTE-A35e', 'ZTE-A35e MOBILE'), ('ZTE-A36', 'ZTE-A36 MOBILE')) as v (sku_code, item_name)
join categories c on c.name = 'Mobile Handsets'
on conflict (sku_code) do update set category_id = excluded.category_id, updated_at = now()
  where products.category_id is distinct from excluded.category_id;

-- 3. the day book's two names point at them
insert into product_aliases (product_id, alias_text)
select p.id, v.alias_text
from (values ('ZTE-A35e', 'ZTE-A35e MOBILE'), ('ZTE-A36', 'ZTE-A36 MOBILE')) as v (sku_code, alias_text)
join products p on p.sku_code = v.sku_code
on conflict (alias_text) do nothing;

-- 4. the name rule (v_item_division: the stock balance's and the ledger's spellings, a later model)
insert into division_rules (pattern, division, priority, note) values
  ('(^|[^a-z])zte([^a-z]|$)', 'Devices', 25,
   'ZTE handsets (owner, 02-Nov-2026): held and sold by the accessories team, never counted as accessory sales, as SIM.')
on conflict (pattern, division) do nothing;

-- 5. the self-check: both names resolve to Devices, and no ZTE line or name is anything else
do $$
declare
  resolved int;
  outside int;
  devices int;
begin
  select count(*) into resolved
  from product_aliases pa
  join products p on p.id = pa.product_id
  join categories c on c.id = p.category_id
  where pa.alias_text in ('ZTE-A35e MOBILE', 'ZTE-A36 MOBILE') and c.division = 'Devices';
  if resolved <> 2 then
    raise exception 'r11 self-check: % of the 2 ZTE names resolve to a Devices category', resolved;
  end if;
  select count(*) filter (where v.division is distinct from 'Devices'), count(*) filter (where v.division = 'Devices')
    into outside, devices
  from v_sales v
  where v.item_name ~* '(^|[^a-z])zte([^a-z]|$)';
  if outside <> 0 then
    raise exception 'r11 self-check: % ZTE line(s) in v_sales are still outside Devices', outside;
  end if;
  if exists (select 1 from v_item_division d
             where d.item_name ~* '(^|[^a-z])zte([^a-z]|$)' and d.division is distinct from 'Devices') then
    raise exception 'r11 self-check: a ZTE name in v_item_division is still outside Devices';
  end if;
  raise notice 'r11: % ZTE line(s) in v_sales, every one Devices', devices;
end $$;
