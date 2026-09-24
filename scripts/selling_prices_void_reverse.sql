-- Reverse of selling_prices_void_migration.sql. Only run if the void must be undone.
--   python -m scripts.apply_sql scripts/selling_prices_void_reverse.sql ; python -m scripts.audit_grants
--
-- What it does: puts the five price views back to their pre-migration definitions (read live on
-- 24-Sep-2026, no voided_at filter) and un-voids the rows THIS migration voided: the 165 phantom
-- rows (twin reason) and the superseded rows whose reason names selling_prices_void_migration.sql.
-- Rows scripts/load_supabase.py voided later (reason 'superseded by ... export)' without the file
-- name) are left as they are. The two columns STAY: they are additive, nothing reads them once the
-- views are restored, and a column drop would silently un-void every loader void as well. Dropping
-- them is a deliberate, separate step:
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

create or replace view v_price_list as
with cur as (
  select
    sku_code, item_name, price_book, rate_bhd, unit_name, start_date, end_date,
    row_number() over (
      partition by sku_code
      order by (price_book = 'MA_base') desc, (warehouse_name is null) desc, start_date desc, id desc
    ) as rn
  from selling_prices
  where status = 'Authorized'
    and rate_bhd is not null and rate_bhd > 0
    and start_date <= current_date
    and (end_date is null or end_date >= current_date)
)
select sku_code, item_name, rate_bhd as price_bhd, unit_name, price_book
from cur
where rn = 1;

create or replace view v_price_history as
select distinct
  sku_code, item_name, price_book,
  start_date as effective_from,
  end_date   as effective_to,
  rate_bhd   as price_bhd,
  unit_name
from selling_prices
where status = 'Authorized'
  and rate_bhd is not null and rate_bhd > 0;

create or replace view v_product_margin as
select
  pp.item_name, pp.report_date, pp.gross_bhd, pp.discount_pct, pp.net_amount_bhd, pp.cogs_bhd,
  pp.gross_profit_bhd, pp.gp_margin_pct, pp.misc_charges_bhd, pp.net_profit_bhd, pp.np_margin_pct,
  p.sku_code,
  p.item_name as product_name,
  cat.name    as category_name,
  sp.rate_bhd as list_price_bhd
from product_profitability pp
left join product_aliases pa on pa.alias_text = pp.item_name
left join products p on p.id = pa.product_id
left join categories cat on cat.id = p.category_id
left join lateral (
  select rate_bhd
  from selling_prices
  where sku_code = p.sku_code
    and price_book = 'MA_base'
    and (customer_code is null or customer_code = '')
  order by id desc
  limit 1
) sp on true
where pp.report_date = (select max(report_date) from product_profitability);

revoke all on v_price_list_by_book, v_price_change, v_price_list, v_price_history, v_product_margin
  from anon, authenticated;
grant select on v_price_list_by_book, v_price_change, v_price_list, v_price_history, v_product_margin
  to yq_readonly;

update selling_prices
   set voided_at = null, void_reason = null
 where void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))'
    or void_reason like '%selling_prices_void_migration.sql%';

insert into audit_log (user_email, event, question, detail)
values ('migration', 'selling_prices.unvoid',
        'selling_prices_void_reverse.sql: views restored, phantom and migration-superseded rows un-voided',
        jsonb_build_object('live_again_36', (select count(*) from selling_prices
                                              where source_file = 'MASellingPriceBook _36_.xlsx' and voided_at is null)));

do $$
declare
  n_left   int;
  n_grants int;
  v        text;
begin
  select count(*) into n_left from selling_prices
   where void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))'
      or void_reason like '%selling_prices_void_migration.sql%';
  if n_left <> 0 then
    raise exception '% migration-voided rows still voided', n_left;
  end if;
  foreach v in array array['v_price_list_by_book', 'v_price_change', 'v_price_list', 'v_price_history', 'v_product_margin'] loop
    if pg_get_viewdef(('public.' || v)::regclass) ilike '%voided_at%' then
      raise exception 'view % still filters on voided_at', v;
    end if;
  end loop;
  select count(*) into n_grants from information_schema.role_table_grants
   where table_schema = 'public'
     and table_name in ('selling_prices', 'v_price_list_by_book', 'v_price_change',
                        'v_price_list', 'v_price_history', 'v_product_margin')
     and grantee in ('anon', 'authenticated');
  if n_grants <> 0 then
    raise exception 'selling_prices / price views must not be granted to anon or authenticated (% grants)', n_grants;
  end if;
end $$;
