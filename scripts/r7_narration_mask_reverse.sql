-- Reverse of r7_narration_mask_migration.sql: v_sales.narration is the raw Focus narration again
-- (division_payment_migration.sql's v_sales, verbatim). CREATE OR REPLACE with the same 31 columns,
-- so every dependent view (v_sales_agent, v_sales_by_*, v_shop_focus_*, ...) and every grant stays.
-- Nothing else changes: order_lines is not touched here, and lines loaded since R7b keep the
-- narration the parser masked (scripts/ingest.py) — this reverse cannot bring those digits back.
-- If scripts/r7_narration_mask_data_migration.sql also ran, the stored narrations stay masked too.
-- Idempotent.

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
    ol.narration,
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

comment on column v_sales.narration is null;

revoke all on table v_sales from anon, authenticated;
revoke all on table v_sales_agent from anon, authenticated;

do $$
begin
  if position('regexp_replace' in pg_get_viewdef('public.v_sales'::regclass)) > 0 then
    raise exception 'v_sales.narration is still the masked expression';
  end if;
  if (select count(*) from pg_attribute
       where attrelid = 'public.v_sales'::regclass and attnum > 0 and not attisdropped) <> 31 then
    raise exception 'v_sales no longer has its 31 columns';
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name in ('v_sales', 'v_sales_agent')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'v_sales / v_sales_agent must not be granted to anon/authenticated';
  end if;
end $$;
