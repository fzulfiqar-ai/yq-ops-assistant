-- Fix: a PO line is unique by (po_no, line_no), NOT (po_no, code). VFAN POs legitimately list the
-- same item code on several lines (volume tiers at different rates), so the old unique(po_no,code)
-- both blocked the load and made the cost views compare two lines of the SAME order as "now vs prev".
-- This re-keys to (po_no, line_no) and rebuilds the views on a per-order blended unit cost
-- (total paid / total qty per item per PO) — the true "what did we pay for X in this order".

alter table purchase_orders drop constraint if exists purchase_orders_po_no_code_key;
alter table purchase_orders drop constraint if exists purchase_orders_po_no_line_no_key;  -- idempotent
alter table purchase_orders add  constraint purchase_orders_po_no_line_no_key unique (po_no, line_no);

-- One blended row per (po_no, code): qty + gross summed, unit rate = gross / qty (falls back to the
-- line rate when qty is 0, e.g. free/sample lines). Shared by all three PO views below.
create or replace view v_po_item AS
select po_no, po_date, vendor, code,
       max(description)                                          as description,
       sum(qty)                                                  as qty,
       sum(gross_bhd)                                            as gross_bhd,
       case when sum(qty) > 0 then round((sum(gross_bhd) / sum(qty))::numeric, 4)
            else max(rate_bhd) end                               as rate_bhd
from purchase_orders
where code is not null
group by po_no, po_date, vendor, code;

-- Full ordering history per item (one blended row per order, newest first).
create or replace view v_po_price_history as
select code as item_code, description, vendor, po_no, po_date, qty, rate_bhd, gross_bhd,
       row_number() over (partition by code order by po_date desc, po_no desc) as recency
from v_po_item;

-- Latest vs previous PO unit cost per item = the cost change across orders.
create or replace view v_po_cost_change as
with r as (
  select code, description, vendor, po_date, po_no, rate_bhd,
         row_number() over (partition by code order by po_date desc, po_no desc) as rn
  from v_po_item where rate_bhd > 0
)
select cur.code                                                  as item_code,
       cur.description, cur.vendor,
       cur.po_date                                               as last_ordered,
       cur.rate_bhd                                              as current_rate_bhd,
       prev.po_date                                              as prev_ordered,
       prev.rate_bhd                                             as prev_rate_bhd,
       round((cur.rate_bhd - prev.rate_bhd)::numeric, 3)         as rate_delta_bhd,
       case when prev.rate_bhd > 0
            then round(100.0 * (cur.rate_bhd - prev.rate_bhd) / prev.rate_bhd, 1) end as rate_change_pct
from r cur
join r prev on prev.code = cur.code and prev.rn = 2
where cur.rn = 1;

-- Ordered -> received lifecycle: one row per (po_no, code), matched to the earliest MRN receipt.
create or replace view v_purchase_lifecycle as
select
  o.po_no, o.po_date, o.vendor, o.code, o.description, o.qty as qty_ordered, o.rate_bhd,
  (select min(s.received_date) from shipments s
     where s.received_qty > 0 and s.received_date >= o.po_date
       and s.item_name ilike '%' || o.code || '%')              as received_on,
  case when exists (select 1 from shipments s
     where s.received_qty > 0 and s.received_date >= o.po_date
       and s.item_name ilike '%' || o.code || '%')
       then 'received' else 'on_order' end                      as status
from v_po_item o;

do $$ begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on v_po_item, v_po_price_history, v_po_cost_change, v_purchase_lifecycle to yq_readonly;
  end if;
end $$;
