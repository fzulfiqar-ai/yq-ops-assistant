-- Procurement workflow (Phase 3): tracks an order through its full lifecycle, from the AI reorder
-- proposal to goods received. This is the human/agent WORKFLOW record — distinct from
-- purchase_orders (the Focus PO line items). Stages follow the real YQ flow:
--   proposed -> reviewed -> raised (with vendor) -> advance_paid -> invoiced -> paid
--   -> po_raised (in Focus) -> received (MRN).   Plus a terminal 'cancelled'.

create table if not exists procurement_orders (
  id               bigint generated always as identity primary key,
  ref              text unique,                       -- human ref e.g. PRO-2026-001
  title            text not null,
  vendor           text,
  stage            text not null default 'proposed',
  est_value_bhd    numeric,
  po_no            text,                              -- links to the Focus PO once raised
  lines            jsonb,                             -- snapshot of proposed lines (item, qty, rate)
  note             text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  stage_changed_at timestamptz not null default now()
);

-- Audit trail of every stage transition (powers the per-order timeline + "who moved it when").
create table if not exists procurement_events (
  id         bigint generated always as identity primary key,
  order_id   bigint not null references procurement_orders(id) on delete cascade,
  stage      text not null,
  note       text,
  actor      text,
  created_at timestamptz not null default now()
);

create index if not exists idx_proc_events_order on procurement_events(order_id, created_at);
create index if not exists idx_proc_orders_stage on procurement_orders(stage);

-- Board view: open orders + days in the current stage + a per-stage SLA 'stuck' flag, so the
-- status agent can nudge orders that have sat too long (e.g. raised but no advance paid in 5 days).
create or replace view v_procurement_board as
with sla(stage, days) as (values
  ('proposed', 3), ('reviewed', 3), ('raised', 5), ('advance_paid', 7),
  ('invoiced', 3), ('paid', 5), ('po_raised', 21))
select
  o.id, o.ref, o.title, o.vendor, o.stage, o.est_value_bhd, o.po_no, o.lines, o.note,
  o.created_at, o.updated_at, o.stage_changed_at,
  extract(day from (now() - o.stage_changed_at))::int as days_in_stage,
  s.days                                              as sla_days,
  (o.stage not in ('received', 'cancelled')
   and s.days is not null
   and extract(day from (now() - o.stage_changed_at)) > s.days) as is_stuck
from procurement_orders o
left join sla s on s.stage = o.stage;
