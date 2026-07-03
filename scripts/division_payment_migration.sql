-- Business divisions + cash/credit sale type.
-- Divisions (the owner's 4 lines of business): Accessories (VFAN), SIM (Batelco),
-- Giveaway (free Batelco stock — must NOT distort revenue/margin), Devices (future).
-- Seeded by rules over Focus item-groups; admin-editable later via the categories table.
-- v_sales gains THREE APPENDED columns (division, sale_type, is_giveaway) — CREATE OR
-- REPLACE keeps every existing column identical so nothing downstream breaks.
-- Idempotent; safe to re-run.

-- 0) The assistant hit "permission denied for table categories" — the read-only role
--    needs the reference dimensions (read-only, non-sensitive).
grant select on categories to yq_readonly;
grant select on products to yq_readonly;

-- 1) Normalize category divisions to the four business divisions.
alter table categories add column if not exists division text;
update categories set division = case
  when name ilike '%sim%'                                        then 'SIM'
  when name ilike '%giveaway%' or name ilike '%give away%'
    or name ilike '%gift%' or name ilike '%free%'                then 'Giveaway'
  when (name ilike '%device%' or name ilike '%handset%'
    or name ilike '%smart phone%' or name ilike '%smartphone%')  then 'Devices'
  else 'Accessories'
end
where division is null
   or division not in ('SIM', 'Giveaway', 'Devices', 'Accessories')
   or (name ilike '%sim%' and division <> 'SIM');

-- 2) Mirror onto catalog items (best-effort name join; default stays Accessories).
update catalog_items ci
set division = c.division
from categories c
where upper(c.name) = ci.category and c.division is not null;

-- 3) v_sales + division / sale_type / is_giveaway (appended columns only).
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

-- 4) Rollup views.
create or replace view v_sales_by_payment as
select
  sale_type,
  count(distinct invoice_no) as orders,
  sum(quantity)              as qty,
  sum(revenue_bhd)           as revenue_bhd,
  sum(net_bhd)               as net_bhd
from v_sales
group by sale_type;

create or replace view v_sales_by_division as
select
  division,
  count(distinct invoice_no) as orders,
  sum(quantity)              as qty,
  sum(revenue_bhd)           as revenue_bhd,
  sum(net_bhd)               as net_bhd,
  sum(case when is_giveaway then quantity else 0 end) as giveaway_qty
from v_sales
group by division;

grant select on v_sales_by_payment to yq_readonly;
grant select on v_sales_by_division to yq_readonly;
