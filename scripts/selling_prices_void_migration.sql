-- Void the day-first phantom price rows; voiding becomes a first-class state (24-Sep-2026, trust plan D1).
-- Additive, idempotent, NO deletes.
--   Rehearse: python -m scripts.apply_sql scripts/selling_prices_void_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/selling_prices_void_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/selling_prices_void_reverse.sql
--   After:    shop.invalidate() (or restart the API) so the market drops the two phantom "Was" pills.
--
-- Why: the 14-Sep-2026 upload of MASellingPriceBook (36) -- staged by the portal as
-- 'MASellingPriceBook _36_.xlsx' -- was parsed DAY-first, before norm_date_us() existed. Every one of
-- its 165 MA_base rows has a twin from 'MASellingPriceBook (40).xlsx' (21-Sep, month-first, the real
-- book) with the same SKU / book / customer / warehouse / rate and the day and month of start_date
-- swapped. Those phantoms are the only reason the market shows "Was 2.000" on UK10 C and UK10 L today
-- (v_price_change reads a cut dated 2026-09-06 = the swap of the real 9-Jun row), and they would move
-- M20 on 5-Oct-2026 and eight SKUs on 1-Nov-2026 -- prices Focus never set.
--
-- Verified read-only on 24-Sep-2026 before this file was written: the twin rule below selects exactly
-- 165 rows (65 SKUs, 77 base-layer rows, 18 dated in the future), and 0 rows of the _36_ file fall
-- outside it (none has day > 12 or day = month, so every row has a swapped twin). Recomputing
-- v_price_change without them leaves no price drop in the last 30 days.
--
-- Nothing is deleted: the rows get voided_at / void_reason, and the two views that read selling_prices
-- skip voided rows. Both views keep every column, type and position (v_catalog depends on
-- v_price_list_by_book), so CREATE OR REPLACE VIEW is legal. These definitions supersede the ones in
-- price_list_migration.sql. scripts/load_supabase.py voids superseded Focus-book rows the same way
-- from now on (each export is a snapshot of the whole book).

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

-- the views run as their owner; the publishable keys must never see prices through them
revoke all on v_price_list_by_book, v_price_change from anon, authenticated;
grant select on v_price_list_by_book, v_price_change to yq_readonly;

-- ── 2. void the phantom twins (a re-run touches nothing: voided rows are excluded) ──
-- The twin rule: same sku_code / price_book / customer_code / warehouse_name / rate, and the (40)
-- row's start_date is the phantom's with day and month swapped. The CASE keeps make_date() away
-- from a day > 12 (it would raise), so the rule is safe on any row the source filter lets through.
update selling_prices p
   set voided_at   = now(),
       void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))'
 where p.voided_at is null
   and p.price_book  = 'MA_base'
   and p.source_file = 'MASellingPriceBook _36_.xlsx'
   and exists (
     select 1
       from selling_prices t
      where t.source_file = 'MASellingPriceBook (40).xlsx'
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

-- ── 3. audit trail (one row, however often the file runs) ─────────────────────
insert into audit_log (user_email, event, question, sql_used, detail)
select 'migration',
       'selling_prices.void',
       'selling_prices_void_migration.sql: void the 165 day-first MA_base rows of MASellingPriceBook _36_.xlsx',
       $q$update selling_prices set voided_at = now(), void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))' where source_file = 'MASellingPriceBook _36_.xlsx' and <same key + rate, start_date day/month swapped in MASellingPriceBook (40).xlsx>$q$,
       jsonb_build_object(
         'rows_voided', (select count(*) from selling_prices
                          where void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))'),
         'skus',        (select count(distinct sku_code) from selling_prices
                          where void_reason = 'dayfirst-parse 14-Sep (twin of MASellingPriceBook (40))'),
         'source_file', 'MASellingPriceBook _36_.xlsx',
         'twin_source', 'MASellingPriceBook (40).xlsx',
         'pills_removed', jsonb_build_array('UK10 C', 'UK10 L'))
 where not exists (select 1 from audit_log
                    where event = 'selling_prices.void'
                      and question like 'selling_prices_void_migration.sql%');

-- ── 4. self-check: raise (and roll the file back) unless every claim above holds ──
do $$
declare
  n_voided   int;
  n_other    int;
  n_left     int;
  n_twins    int;
  n_pills    int;
  n_grants   int;
  def1       text;
  def2       text;
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
   where source_file = 'MASellingPriceBook (40).xlsx' and void_reason = reason;
  if n_twins <> 0 then
    raise exception '% rows of the real (40) book were voided by the twin rule', n_twins;
  end if;

  select count(*) into n_pills from v_price_change
   where sku_code in ('UK10 C', 'UK10 L') and changed_on = date '2026-09-06';
  if n_pills <> 0 then
    raise exception 'v_price_change still reports the phantom 6-Sep cut on UK10 C / UK10 L';
  end if;

  select pg_get_viewdef('public.v_price_list_by_book'::regclass) into def1;
  select pg_get_viewdef('public.v_price_change'::regclass)       into def2;
  if def1 not ilike '%voided_at is null%' or def2 not ilike '%voided_at is null%' then
    raise exception 'a price view does not filter voided rows';
  end if;

  select count(*) into n_grants from information_schema.role_table_grants
   where table_schema = 'public'
     and table_name in ('selling_prices', 'v_price_list_by_book', 'v_price_change')
     and grantee in ('anon', 'authenticated');
  if n_grants <> 0 then
    raise exception 'selling_prices / price views must not be granted to anon or authenticated (% grants)', n_grants;
  end if;

  if not exists (select 1 from audit_log where event = 'selling_prices.void') then
    raise exception 'audit_log row for the void is missing';
  end if;

  raise notice 'selling_prices_void: % phantom rows voided (65 SKUs); views skip voided rows; grants clean', n_voided;
end $$;
