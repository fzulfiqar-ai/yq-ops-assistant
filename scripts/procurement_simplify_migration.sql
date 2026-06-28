-- Phase 3: simplify the procurement pipeline to 6 stages / 100% payment.
-- Old:  proposed -> reviewed -> raised -> advance_paid -> invoiced -> paid -> po_raised -> received
-- New:  proposed -> reviewed -> raised (& confirmed) -> paid (100%) -> received (MRN) -> closed
-- We pay the vendor in full, so advance/balance collapse into one 'paid'. Map legacy rows forward
-- and add a terminal 'closed'. Run once.

update procurement_orders set stage = 'paid'   where stage in ('advance_paid', 'po_raised');
update procurement_orders set stage = 'raised' where stage = 'invoiced';

-- Board view rebuilt with the new per-stage SLAs (days before an order counts as 'stuck').
create or replace view v_procurement_board as
with sla(stage, days) as (values
  ('proposed', 3), ('reviewed', 3), ('raised', 5), ('paid', 7), ('received', 21))
select
  o.id, o.ref, o.title, o.vendor, o.stage, o.est_value_bhd, o.po_no, o.lines, o.note,
  o.created_at, o.updated_at, o.stage_changed_at,
  extract(day from (now() - o.stage_changed_at))::int as days_in_stage,
  s.days                                              as sla_days,
  (o.stage not in ('received', 'closed', 'cancelled')
   and s.days is not null
   and extract(day from (now() - o.stage_changed_at)) > s.days) as is_stuck
from procurement_orders o
left join sla s on s.stage = o.stage;
