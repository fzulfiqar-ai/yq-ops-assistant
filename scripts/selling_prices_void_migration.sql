-- Void the day-first phantom price rows; voiding becomes a first-class state (24-Sep-2026, trust plan D1).
-- Additive, idempotent, NO deletes.
--   Rehearse: python -m scripts.apply_sql scripts/selling_prices_void_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/selling_prices_void_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/selling_prices_void_reverse.sql
--   After:    shop.invalidate() (or restart the API) so the market drops the two phantom "Was" pills.
--
-- Why: the 14-Sep-2026 upload of MASellingPriceBook (36) -- staged by the portal as
-- 'MASellingPriceBook _36_.xlsx' -- was parsed DAY-first, before norm_date_us() existed. Every one of
-- its 165 MA_base rows has a twin in the real, month-first book (today 'MASellingPriceBook (40).xlsx',
-- 21-Sep) with the same SKU / book / customer / warehouse / rate and the day and month of start_date
-- swapped. Those phantoms are the only reason the market shows "Was 2.000" on UK10 C and UK10 L today
-- (v_price_change reads a cut dated 2026-09-06 = the swap of the real 9-Jun row), and they would move
-- M20 on 5-Oct-2026 and eight SKUs on 1-Nov-2026 -- prices Focus never set.
--
-- Verified read-only on 24-Sep-2026 before this file was written: the twin rule below selects exactly
-- 165 rows (65 SKUs, 77 base-layer rows, 18 dated in the future), and 0 rows of the _36_ file fall
-- outside it (none has day > 12 or day = month, so every row has a swapped twin). Recomputing
-- v_price_change without them leaves no price drop in the last 30 days. The twin is looked for in ANY
-- live MASellingPriceBook% file other than _36_, so a price-book upload made before this file runs
-- (which moves the matching rows to the new file name) does not break the rule.
--
-- Section 3 runs the loader's snapshot rule ONCE (scripts/load_supabase.py applies it after every
-- Focus price-book load from now on): a Focus export is the whole book, so live rows of an OLDER
-- export of the same book whose key the newest export (by import time) does not carry are voided.
-- Today that is 1 MA_base row ('MASellingPriceBook (32).xlsx': F25 for warehouse Moideen KP, gone from
-- the book) and the 167 rows of 'ModernTradeSellerBook (3).xlsx', a column-shifted parse (the customer
-- name sits in warehouse_name) that 'ModernTradeSellerBook _7_.xlsx' re-read correctly -- both live
-- side by side, both never winning a current price. Without this step verify_numbers' new check
-- "live rows not in file (older exports)" could never pass, and the first modern_trade upload after
-- this migration would trip the loader's mass-void guard (167 of 334 rows = 50 %).
--
-- Nothing is deleted: the rows get voided_at / void_reason, and every view that reads selling_prices
-- skips voided rows (pg_depend, read live 24-Sep-2026: v_price_list_by_book, v_price_change,
-- v_price_list, v_price_history, v_product_margin; their dependents v_catalog, v_price_tracker and
-- v_product_economics are untouched). Every view keeps every column, type and position, so CREATE OR
-- REPLACE VIEW is legal. These definitions supersede the ones in price_list_migration.sql and the
-- earlier view files (see docs/MIGRATIONS.md).

alter table selling_prices add column if not exists voided_at   timestamptz;
alter table selling_prices add column if not exists void_reason text;

comment on column selling_prices.voided_at is
  'Set instead of deleting: every price view ignores the row. NULL = live. See void_reason.';
comment on column selling_prices.void_reason is
  'Why the row was voided: a mis-parsed upload, or superseded by a newer Focus price-book export.';

-- ── 1. views: identical column lists, plus "voided_at is null" ────────────────
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
    and voided_at is null
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
    and voided_at is null
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

-- the assistant's "current selling price" (app/ai.py, agents, enrich_catalog_specs); v_price_tracker
-- and v_product_economics build on it
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
    and voided_at is null
)
select sku_code, item_name, rate_bhd as price_bhd, unit_name, price_book
from cur
where rn = 1;

-- the portal's per-SKU price history (app/main.py) -- the phantom 6-Sep / 1-Nov entries leave it here
create or replace view v_price_history as
select distinct
  sku_code, item_name, price_book,
  start_date as effective_from,
  end_date   as effective_to,
  rate_bhd   as price_bhd,
  unit_name
from selling_prices
where status = 'Authorized'
  and rate_bhd is not null and rate_bhd > 0
  and voided_at is null;

-- product margin vs the latest MA_base dealer row by id: a voided row must never be "the latest"
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
    and voided_at is null
  order by id desc
  limit 1
) sp on true
where pp.report_date = (select max(report_date) from product_profitability);

-- the views run as their owner; the publishable keys must never see prices through them
revoke all on v_price_list_by_book, v_price_change, v_price_list, v_price_history, v_product_margin
  from anon, authenticated;
grant select on v_price_list_by_book, v_price_change, v_price_list, v_price_history, v_product_margin
  to yq_readonly;

-- ── 2. void the phantom twins (a re-run touches nothing: voided rows are excluded) ──
-- The twin rule: same sku_code / price_book / customer_code / warehouse_name / rate, and a row of
-- any OTHER live MA Focus book has the phantom's start_date with day and month swapped. The CASE
-- keeps make_date() away from a day > 12 (it would raise), so the rule is safe on any row the
-- source filter lets through.
update selling_prices p
   set voided_at   = now(),
       void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))'
 where p.voided_at is null
   and p.price_book  = 'MA_base'
   and p.source_file = 'MASellingPriceBook _36_.xlsx'
   and exists (
     select 1
       from selling_prices t
      where t.source_file like 'MASellingPriceBook%'
        and t.source_file <> 'MASellingPriceBook _36_.xlsx'
        and t.voided_at is null
        and t.price_book  = p.price_book
        and t.sku_code    = p.sku_code
        and t.customer_code  is not distinct from p.customer_code
        and t.warehouse_name is not distinct from p.warehouse_name
        and t.rate_bhd    = p.rate_bhd
        and t.start_date <> p.start_date
        and t.start_date  = case when extract(day from p.start_date) <= 12
                                 then make_date(extract(year  from p.start_date)::int,
                                                extract(day   from p.start_date)::int,
                                                extract(month from p.start_date)::int)
                            end
   );

-- ── 3. the loader's snapshot rule, once: older Focus exports of each book ─────
-- newest export per book = the Focus file imported last (workbook rows YQ_MRN% are not exports and
-- never take part); a live row of another Focus export of that book whose key the newest export
-- does not carry is superseded. The reason carries this file's name so the reverse can tell these
-- voids from the ones scripts/load_supabase.py writes later.
with newest as (
  select price_book, source_file
    from (
      select price_book, source_file,
             row_number() over (partition by price_book
                                order by max(imported_at) desc nulls last, max(id) desc) as rn
        from selling_prices
       where voided_at is null
         and ((price_book = 'MA_base'      and lower(source_file) like 'masellingpricebook%')
           or (price_book = 'modern_trade' and lower(source_file) like 'moderntradesellerbook%'))
       group by price_book, source_file
    ) s
   where rn = 1
)
update selling_prices p
   set voided_at   = now(),
       void_reason = 'superseded by ' || n.source_file
                     || ' (Focus book snapshot: key not in the newest export; selling_prices_void_migration.sql)'
  from newest n
 where p.voided_at is null
   and p.price_book  = n.price_book
   and p.source_file <> n.source_file
   and ((p.price_book = 'MA_base'      and lower(p.source_file) like 'masellingpricebook%')
     or (p.price_book = 'modern_trade' and lower(p.source_file) like 'moderntradesellerbook%'))
   and not exists (
     select 1
       from selling_prices f
      where f.source_file = n.source_file
        and f.price_book  = p.price_book
        and f.sku_code    = p.sku_code
        and f.customer_code  is not distinct from p.customer_code
        and f.warehouse_name is not distinct from p.warehouse_name
        and f.start_date  = p.start_date
   );

-- ── 4. audit trail (one row per batch, however often the file runs) ───────────
insert into audit_log (user_email, event, question, sql_used, detail)
select 'migration',
       'selling_prices.void',
       'selling_prices_void_migration.sql: void the 165 day-first MA_base rows of MASellingPriceBook _36_.xlsx',
       $q$update selling_prices set voided_at = now(), void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))' where source_file = 'MASellingPriceBook _36_.xlsx' and <same key + rate, start_date day/month swapped in another live MASellingPriceBook% file>$q$,
       jsonb_build_object(
         'rows_voided', (select count(*) from selling_prices
                          where void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))'),
         'skus',        (select count(distinct sku_code) from selling_prices
                          where void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))'),
         'source_file', 'MASellingPriceBook _36_.xlsx',
         'pills_removed', jsonb_build_array('UK10 C', 'UK10 L'))
 where not exists (select 1 from audit_log
                    where event = 'selling_prices.void'
                      and question like 'selling_prices_void_migration.sql: void the 165%');

insert into audit_log (user_email, event, question, sql_used, detail)
select 'migration',
       'selling_prices.void',
       'selling_prices_void_migration.sql: ' || price_book || ' rows of older Focus exports whose key is not in the newest export (the loader snapshot rule, run once)',
       $q$update selling_prices set voided_at = now(), void_reason = 'superseded by <newest export> (Focus book snapshot: key not in the newest export; selling_prices_void_migration.sql)' where <older Focus export of the same book> and <key not in the newest export>$q$,
       jsonb_build_object(
         'book',    price_book,
         'count',   count(*),
         'newest',  min(substring(void_reason from 'superseded by (.+) \(Focus book snapshot')),
         'sources', (select jsonb_agg(distinct x.source_file) from selling_prices x
                      where x.price_book = s.price_book and x.void_reason like '%selling_prices_void_migration.sql%'),
         'skus',    (select jsonb_agg(distinct x.sku_code) from selling_prices x
                      where x.price_book = s.price_book and x.void_reason like '%selling_prices_void_migration.sql%'))
  from selling_prices s
 where void_reason like '%selling_prices_void_migration.sql%'
 group by price_book
having not exists (select 1 from audit_log
                    where event = 'selling_prices.void'
                      and question like 'selling_prices_void_migration.sql: ' || s.price_book || ' rows of older%');

-- ── 5. self-check: raise (and roll the file back) unless every claim above holds ──
do $$
declare
  n_voided   int;
  n_other    int;
  n_left     int;
  n_twins    int;
  n_pills    int;
  n_grants   int;
  n_stale    int;
  n_super    int;
  n_newest   int;
  v          text;
  def        text;
  reason     constant text := 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))';
begin
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'selling_prices' and column_name = 'voided_at')
     or not exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'selling_prices' and column_name = 'void_reason') then
    raise exception 'selling_prices.voided_at / void_reason missing';
  end if;

  select count(*) into n_voided from selling_prices
   where void_reason = reason and voided_at is not null
     and source_file = 'MASellingPriceBook _36_.xlsx' and price_book = 'MA_base';
  if n_voided <> 165 then
    raise exception 'expected exactly 165 phantom rows voided, found %', n_voided;
  end if;

  select count(*) into n_other from selling_prices
   where void_reason = reason and source_file is distinct from 'MASellingPriceBook _36_.xlsx';
  if n_other <> 0 then
    raise exception '% rows from another source carry the phantom void reason', n_other;
  end if;

  select count(*) into n_left from selling_prices
   where source_file = 'MASellingPriceBook _36_.xlsx' and voided_at is null;
  if n_left <> 0 then
    raise exception '% rows of the day-first file are still live (the twin rule missed them)', n_left;
  end if;

  select count(*) into n_twins from selling_prices
   where source_file like 'MASellingPriceBook%' and source_file <> 'MASellingPriceBook _36_.xlsx'
     and void_reason = reason;
  if n_twins <> 0 then
    raise exception '% rows of a real MA book were voided by the twin rule', n_twins;
  end if;

  -- the loader rule left no live row of an older Focus export whose key the newest export lacks
  select count(*) into n_stale
    from selling_prices p
    join (select price_book, source_file
            from (select price_book, source_file,
                         row_number() over (partition by price_book
                                            order by max(imported_at) desc nulls last, max(id) desc) as rn
                    from selling_prices
                   where voided_at is null
                     and ((price_book = 'MA_base'      and lower(source_file) like 'masellingpricebook%')
                       or (price_book = 'modern_trade' and lower(source_file) like 'moderntradesellerbook%'))
                   group by price_book, source_file) s
           where rn = 1) n on n.price_book = p.price_book
   where p.voided_at is null
     and p.source_file <> n.source_file
     and ((p.price_book = 'MA_base'      and lower(p.source_file) like 'masellingpricebook%')
       or (p.price_book = 'modern_trade' and lower(p.source_file) like 'moderntradesellerbook%'))
     and not exists (select 1 from selling_prices f
                      where f.source_file = n.source_file and f.price_book = p.price_book
                        and f.sku_code = p.sku_code
                        and f.customer_code  is not distinct from p.customer_code
                        and f.warehouse_name is not distinct from p.warehouse_name
                        and f.start_date = p.start_date);
  if n_stale <> 0 then
    raise exception '% live rows of older Focus exports are still not covered by the newest export', n_stale;
  end if;
  select count(*) into n_super from selling_prices where void_reason like '%selling_prices_void_migration.sql%';
  -- a superseded void must never touch the newest export of its book (it is the reference)
  select count(*) into n_newest from selling_prices
   where void_reason like '%selling_prices_void_migration.sql%'
     and void_reason like 'superseded by ' || source_file || ' (%';
  if n_newest <> 0 then
    raise exception '% rows of a newest export were voided as superseded by themselves', n_newest;
  end if;

  select count(*) into n_pills from v_price_change
   where sku_code in ('UK10 C', 'UK10 L') and changed_on = date '2026-09-06';
  if n_pills <> 0 then
    raise exception 'v_price_change still reports the phantom 6-Sep cut on UK10 C / UK10 L';
  end if;

  foreach v in array array['v_price_list_by_book', 'v_price_change', 'v_price_list', 'v_price_history', 'v_product_margin'] loop
    select pg_get_viewdef(('public.' || v)::regclass) into def;
    if def not ilike '%voided_at is null%' then
      raise exception 'view % does not filter voided rows', v;
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
  foreach v in array array['v_price_list_by_book', 'v_price_change', 'v_price_list', 'v_price_history', 'v_product_margin'] loop
    if not exists (select 1 from information_schema.role_table_grants
                    where table_schema = 'public' and table_name = v
                      and grantee = 'yq_readonly' and privilege_type = 'SELECT') then
      raise exception 'yq_readonly lost SELECT on %', v;
    end if;
  end loop;

  if not exists (select 1 from audit_log where event = 'selling_prices.void'
                    and question like 'selling_prices_void_migration.sql: void the 165%') then
    raise exception 'audit_log row for the phantom void is missing';
  end if;
  if n_super > 0 and not exists (select 1 from audit_log where event = 'selling_prices.void'
                    and question like 'selling_prices_void_migration.sql: % rows of older Focus exports%') then
    raise exception 'audit_log row for the superseded rows is missing';
  end if;

  raise notice 'selling_prices_void: % phantom rows voided (65 SKUs); % rows of older Focus exports voided as superseded (expected 168 = 1 MA_base + 167 modern_trade on 24-Sep-2026); 5 views skip voided rows; grants clean',
    n_voided, n_super;
end $$;
