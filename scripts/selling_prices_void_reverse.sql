-- Reverse of selling_prices_void_migration.sql. Only run if the void must be undone.
--   python -m scripts.apply_sql scripts/selling_prices_void_reverse.sql ; python -m scripts.audit_grants
--
-- What it does: puts the two price views back to their price_list_migration.sql definitions (no
-- voided_at filter) and un-voids the 165 phantom rows. The two columns STAY: they are additive,
-- nothing reads them once the views are restored, and scripts/load_supabase.py may have voided
-- other rows (void_reason 'superseded by …') that a column drop would silently un-void as well.
-- Dropping them is a deliberate, separate step:
--   alter table selling_prices drop column if exists voided_at, drop column if exists void_reason;
-- (only after the views below no longer reference them, i.e. after this file has run).

create or replace view v_price_list_by_book as
with cur as (
  select
    sku_code, item_name, price_book, rate_bhd, unit_name,
    row_number() over (
      partition by sku_code, price_book
      order by (warehouse_name is null) desc, start_date desc, id desc
    ) as rn
  from selling_prices
  where status = 'Authorized'
    and rate_bhd is not null and rate_bhd > 0
    and start_date <= current_date
    and (end_date is null or end_date >= current_date)
)
select sku_code, item_name, price_book, rate_bhd as price_bhd, unit_name
from cur
where rn = 1;

create or replace view v_price_change as
with distinct_prices as (
  select distinct sku_code, item_name, start_date, rate_bhd
  from selling_prices
  where status = 'Authorized' and price_book = 'MA_base'
    and warehouse_name is null
    and rate_bhd is not null and rate_bhd > 0
    and start_date <= current_date
),
ranked as (
  select sku_code, item_name, start_date, rate_bhd,
         row_number() over (partition by sku_code order by start_date desc, rate_bhd desc) as rn
  from distinct_prices
)
select
  cur.sku_code, cur.item_name,
  cur.start_date  as changed_on,
  cur.rate_bhd    as current_price_bhd,
  prev.rate_bhd   as prev_price_bhd,
  round((cur.rate_bhd - prev.rate_bhd)::numeric, 3) as price_delta_bhd,
  case when prev.rate_bhd > 0
       then round(100.0 * (cur.rate_bhd - prev.rate_bhd) / prev.rate_bhd, 1) end as price_change_pct
from ranked cur
join ranked prev on prev.sku_code = cur.sku_code and prev.rn = 2
where cur.rn = 1 and cur.rate_bhd <> prev.rate_bhd;

revoke all on v_price_list_by_book, v_price_change from anon, authenticated;
grant select on v_price_list_by_book, v_price_change to yq_readonly;

update selling_prices
   set voided_at = null, void_reason = null
 where void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))';

insert into audit_log (user_email, event, question, detail)
values ('migration', 'selling_prices.unvoid',
        'selling_prices_void_reverse.sql: views restored, phantom rows un-voided',
        jsonb_build_object('live_again', (select count(*) from selling_prices
                                           where source_file = 'MASellingPriceBook _36_.xlsx' and voided_at is null)));

do $$
declare
  n_left   int;
  n_grants int;
begin
  select count(*) into n_left from selling_prices
   where void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))';
  if n_left <> 0 then
    raise exception '% phantom rows still voided', n_left;
  end if;
  if pg_get_viewdef('public.v_price_list_by_book'::regclass) ilike '%voided_at%'
     or pg_get_viewdef('public.v_price_change'::regclass) ilike '%voided_at%' then
    raise exception 'a price view still filters on voided_at';
  end if;
  select count(*) into n_grants from information_schema.role_table_grants
   where table_schema = 'public'
     and table_name in ('selling_prices', 'v_price_list_by_book', 'v_price_change')
     and grantee in ('anon', 'authenticated');
  if n_grants <> 0 then
    raise exception 'selling_prices / price views must not be granted to anon or authenticated (% grants)', n_grants;
  end if;
end $$;
