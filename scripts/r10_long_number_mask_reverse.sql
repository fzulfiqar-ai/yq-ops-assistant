-- Reverse of scripts/r10_long_number_mask_migration.sql (release r10). Idempotent.
--   python -m scripts.apply_sql scripts/r10_long_number_mask_reverse.sql ; python -m scripts.audit_grants
-- From the MAIN project folder only.
--
-- What it puts back: v_sales as r7_narration_mask_migration.sql has it, that file's statement verbatim (the R7b mask:
-- a run of exactly 9 digits and a run of 15 digits or more keep their last 3) with that file's column comment.
-- CREATE OR REPLACE with the same 31 columns, so v_sales_agent, the other views on v_sales and every grant stay;
-- anon / authenticated are revoked again (plain statements).
--
-- What it CANNOT put back: the stored narrations. The migration replaced every run of 10 to 14 digits in
-- order_lines.narration, stock_movements.narration and the batch importer's copies of both with stars; reversing the
-- view does not bring the text back, and nothing here touches a row. The only way back to the text is the release
-- backup taken before the migration (yq_backup release r10, encrypted): restore the column from it by hand
-- (order_lines on invoice_no + line_no, stock_movements on voucher + item_name + row_hash), which is never wanted for
-- ID numbers. The loader masks with the r10 rule for as long as the main folder carries the r10 code: going back to
-- the R7b rule there is a code revert, not this file.

set lock_timeout = '2s';      -- a busy v_sales makes the file fail fast, never queue

-- r7_narration_mask_migration.sql's statement, verbatim
create or replace view v_sales as
select
    ol.id                                          as line_id,
    ol.invoice_no,
    ol.line_no,
    coalesce(ol.line_date, o.order_date)           as sale_date,
    o.order_date,
    o.customer_name,
    ol.customer_account,
    o.salesman,
    o.payment_mode,
    o.sales_account_name,
    ol.item_name,
    p.sku_code,
    p.item_name                                    as product_name,
    cat.name                                       as category_name,
    ol.quantity,
    ol.rate_bhd,
    ol.gross_bhd,
    ol.discount_bhd,
    ol.taxable_bhd,
    ol.vat_amount_bhd,
    coalesce(ol.total_amount_bhd, ol.gross_bhd)    as revenue_bhd,
    coalesce(ol.taxable_bhd, ol.gross_bhd / 1.1)   as net_bhd,
    coalesce(ol.total_amount_bhd, ol.gross_bhd)    as total_amount_bhd,
    coalesce(o.salesman, ol.warehouse_name)        as salesman_resolved,
    ol.warehouse_name                              as salesman_raw,
    coalesce(sc.channel,
             case when coalesce(o.salesman, ol.warehouse_name) in ('Causeway', 'YQ Roadshow')
                  then 'B2C' else 'B2B' end)        as channel,
    (coalesce(o.customer_name, ol.customer_account) ilike 'cash customer%') as is_cash_customer,
    -- R7b: personal ID numbers masked (9-digit runs, 15+-digit runs; the last 3 digits kept).
    -- The guard skips the two regexp passes on every narration without a 9-digit stretch.
    case when ol.narration ~ '[0-9]{9}' then
      regexp_replace(
        regexp_replace(ol.narration, '(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])', '************\1', 'g'),
        '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')
    else ol.narration end                          as narration,
    -- division: item-group mapping; SIM item names win even if the group is generic
    case
      when cat.division in ('SIM', 'Giveaway', 'Devices', 'Accessories') then cat.division
      when ol.item_name ilike '%sim%' or ol.item_name ilike '%batelco%'  then 'SIM'
      else 'Accessories'
    end                                            as division,
    -- cash vs credit: Focus header fields first, walk-in flag as backstop
    case
      when o.payment_mode ilike 'cash%'                    then 'cash'
      when o.payment_mode ilike 'credit%'                  then 'credit'
      when o.payment_mode ilike 'benefit%'                 then 'cash'
      when o.sales_account_name ilike '%credit%'           then 'credit'
      when o.sales_account_name ilike '%cash%'             then 'cash'
      when coalesce(o.customer_name, ol.customer_account) ilike 'cash customer%' then 'cash'
      else 'credit'
    end                                            as sale_type,
    -- free stock issued through sales (zero-priced lines) or the Giveaway division
    (coalesce(cat.division, '') = 'Giveaway'
     or (coalesce(ol.rate_bhd, 0) = 0 and coalesce(ol.gross_bhd, 0) = 0
         and coalesce(ol.quantity, 0) > 0))        as is_giveaway
from order_lines ol
left join orders          o   on o.invoice_no  = ol.invoice_no
left join product_aliases pa  on pa.alias_text = ol.item_name
left join products        p   on p.id          = pa.product_id
left join categories      cat on cat.id        = p.category_id
left join salesman_channels sc on sc.salesman  = coalesce(o.salesman, ol.warehouse_name);

comment on column v_sales.narration is
  'Focus Sales Day Book narration with personal ID numbers masked: every 9-digit run and every run of 15+ digits keeps only its last 3 digits (R7b, scripts/r7_narration_mask_migration.sql). Order numbers such as YQ-2609-0019 are untouched.';

-- grants (plain statements, never inside a DO block)
revoke all on table v_sales from anon, authenticated;
revoke all on table v_sales_agent from anon, authenticated;

-- self-check: the R7b mask back in v_sales, its 31 columns, the grants
do $$
declare
  def text;
begin
  def := pg_get_viewdef('public.v_sales'::regclass);
  if position('[0-9]{12,}([0-9]{3})' in def) = 0 or position('[0-9]{7,}' in def) > 0 then
    raise exception 'r10_long_number_mask reverse: v_sales.narration is not the R7b mask';
  end if;
  if (select count(*) from pg_attribute
       where attrelid = 'public.v_sales'::regclass and attnum > 0 and not attisdropped) <> 31 then
    raise exception 'r10_long_number_mask reverse: v_sales no longer has its 31 columns';
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name in ('v_sales', 'v_sales_agent')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r10_long_number_mask reverse: v_sales / v_sales_agent must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and not (has_table_privilege('yq_readonly', 'public.v_sales', 'SELECT')
              and has_table_privilege('yq_readonly', 'public.v_sales_agent', 'SELECT')) then
    raise exception 'r10_long_number_mask reverse: yq_readonly lost SELECT on v_sales / v_sales_agent';
  end if;
  raise notice 'r10_long_number_mask reverse: ok (v_sales as r7_narration_mask_migration.sql has it; the stored narrations stay masked)';
end $$;
