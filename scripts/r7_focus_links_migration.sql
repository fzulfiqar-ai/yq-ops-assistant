-- Focus link v1 — release R7a, Sprint 1 "Make it true" (plan §24 r7_focus_links, audit D6).
-- Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7_focus_links_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7_focus_links_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7_focus_links_reverse.sql
--
-- Why: the R3 reconciliation (scripts/r3_pipeline_migration.sql) could never match. Focus stores
-- the invoice as 'SI : SI-YQ-26-09-119' while staff type 'SI-YQ-26-09-119'; Focus names the rep
-- '<name> - Acc WH' (or ' - SIM WH') while salesmen.focus_name is '<name>'; and it assumed one
-- order per invoice, although a rep often invoices two orders of the same shop together.
--
-- What this adds:
--   1. shop_order_focus_links: which marketplace order a Focus invoice covers — many to many.
--      A row is a suggestion someone decided on: confirmed (accepted) or rejected (never suggested
--      again). invoice_key is the bare normalised key (SI-YQ-26-09-110), the same form
--      app/shop_pipeline.py clean_invoice_no produces. Rows are never deleted by the app; an
--      undo is a state change. Service role only (RLS on, no policies).
--   2. v_shop_focus_recon, re-created with its 23 columns unchanged (the portal reads them) plus
--      four appended: invoice keys normalised on both sides; the rep compared suffix-aware; the
--      confirmed links counted as the order's invoices (they win over a typed number, and a typed
--      number whose pair was rejected stops counting — the order row itself is never rewritten); and
--      many orders per invoice — the invoice total is compared with the SUM of the orders on it.
--      focus_invoice_no now reports the key actually compared (normalised), so it is null exactly
--      when missing_invoice is true.
--      invoice_reused now means "several orders claim this invoice and not all of those claims
--      were confirmed by a person", so an accepted two-order invoice is not flagged forever.
--   3. v_shop_focus_candidates: for every live order (new … delivered, not test, not cancelled)
--      without a confirmed link, the Focus invoices that may cover it, scored in three tiers:
--        narration_ref (1.000) — the invoice's Narration names the order number (YQ-2609-0019;
--                                 several may be comma-separated);
--        sio_ref       (0.950) — a Stock Issue Voucher's Narration names the order, and the same
--                                 rep raised an SI with those lines within 0-2 days;
--        auto_items    (≤ 0.900) — same rep, invoice dated order day -1 … +7, and at least 60 %
--                                 of the order's SKUs (alias-resolved) on the invoice;
--                                 a 1-2 line order only on exact lines; confidence = that share x
--                                 (0.5 + 0.5 x exact-line share), capped at 0.9.
--      Each row carries line-level diff counts (matched, qty / price differences, missing on the
--      invoice, extra on the invoice) and a rank per order. A rejected pair is never suggested
--      again; an invoice already fully covered by confirmed links is not auto-suggested again.
--
-- Nothing here changes a row: no UPDATE, no DELETE, no backfill. The views carry customer
-- names, so they are service-role only (never yq_readonly, never anon/authenticated). The API
-- probes and falls back (empty list + hint) until this runs, so it may deploy first.
-- v_shop_focus_recon's canonical definition moves here from r3_pipeline_migration.sql
-- (re-running that older file after this one fails loudly: it would drop the appended columns).

-- ── 1. shop_order_focus_links ──────────────────────────────────────────────────
create table if not exists shop_order_focus_links (
  id             bigint generated always as identity primary key,
  order_id       bigint        not null references shop_orders(id),     -- no cascade: a link outlives nothing silently
  invoice_key    text          not null,     -- normalised bare key, e.g. SI-YQ-26-09-110
  sio_key        text,                       -- the Stock Issue Voucher behind an sio_ref match, e.g. SIO:YQ-26-09-137
  method         text          not null,     -- narration_ref | sio_ref | auto_items | manual
  confidence     numeric(4,3),
  allocated_bhd  numeric(12,3),              -- the order's total when the link was accepted (its share of the invoice)
  state          text          not null default 'suggested',   -- suggested | confirmed | rejected
  note           text,
  created_by     text,
  created_at     timestamptz   not null default now(),
  decided_by     text,
  decided_at     timestamptz,
  constraint shop_order_focus_links_order_invoice_key unique (order_id, invoice_key)
);

alter table shop_order_focus_links drop constraint if exists shop_order_focus_links_method_check;
alter table shop_order_focus_links add constraint shop_order_focus_links_method_check
  check (method in ('narration_ref', 'sio_ref', 'auto_items', 'manual'));
alter table shop_order_focus_links drop constraint if exists shop_order_focus_links_state_check;
alter table shop_order_focus_links add constraint shop_order_focus_links_state_check
  check (state in ('suggested', 'confirmed', 'rejected'));
alter table shop_order_focus_links drop constraint if exists shop_order_focus_links_confidence_check;
alter table shop_order_focus_links add constraint shop_order_focus_links_confidence_check
  check (confidence is null or (confidence >= 0 and confidence <= 1));
-- the key is stored in exactly the form the views and clean_invoice_no compare on
alter table shop_order_focus_links drop constraint if exists shop_order_focus_links_invoice_key_check;
alter table shop_order_focus_links add constraint shop_order_focus_links_invoice_key_check
  check (invoice_key <> '' and invoice_key = upper(regexp_replace(trim(invoice_key), '^SI\s*:\s*', '', 'i')));

create index if not exists shop_order_focus_links_invoice_idx on shop_order_focus_links (invoice_key) where state = 'confirmed';

alter table shop_order_focus_links enable row level security;      -- service role only; no policies
revoke all on shop_order_focus_links from anon, authenticated;
revoke all on sequence shop_order_focus_links_id_seq from anon, authenticated;

comment on table shop_order_focus_links is
  'Which Focus sales invoice covers which marketplace order (R7a, many to many). Suggested by v_shop_focus_candidates, decided in the portal (POST /shop/orders/{id}/focus-link): confirmed or rejected. invoice_key is the bare normalised key (SI : prefix stripped, upper case). Never deleted by the app; service role only.';

-- ── 2. v_shop_focus_recon: delivered orders vs Focus, fixed ──────────────────
create or replace view v_shop_focus_recon as
with inv as (             -- one row per Focus invoice, keyed the way staff type it
  select upper(regexp_replace(trim(invoice_no), '^SI\s*:\s*', '', 'i')) as invoice_key,
         sum(revenue_bhd)                as invoice_total_bhd,     -- VAT-inclusive, like the order total
         sum(net_bhd)                    as invoice_net_bhd,
         max(salesman_resolved)          as focus_salesman,
         max(customer_name)              as focus_customer,
         min(sale_date)                  as invoice_date
  from v_sales
  where invoice_no is not null and trim(invoice_no) <> ''
  group by 1
),
confirmed as (
  select l.order_id, l.invoice_key
  from shop_order_focus_links l
  join shop_orders o on o.id = l.order_id
  where l.state = 'confirmed' and o.status <> 'cancelled'
),
typed as (                -- the number typed at Delivered, in the same normalised form
  select o.id as order_id, upper(regexp_replace(trim(o.focus_invoice_no), '^SI\s*:\s*', '', 'i')) as invoice_key
  from shop_orders o
  where o.status = 'delivered' and nullif(trim(o.focus_invoice_no), '') is not null
),
claim as (                -- the invoices each order claims: confirmed links win; else the typed number,
                          -- unless a person rejected exactly that pair (the order row is never rewritten)
  select c.order_id, c.invoice_key, true as confirmed
  from confirmed c
  union
  select t.order_id, t.invoice_key, false
  from typed t
  where not exists (select 1 from confirmed c where c.order_id = t.order_id)
    and not exists (select 1 from shop_order_focus_links r
                    where r.order_id = t.order_id and r.invoice_key = t.invoice_key and r.state = 'rejected')
),
per_inv as (              -- how many orders claim each invoice, and how many of those claims a person confirmed
  select invoice_key, count(*) as orders_n, count(*) filter (where confirmed) as confirmed_n
  from claim
  group by 1
),
per_ord as (              -- per order: its invoices, which of them the uploaded ledger holds, and their totals
  select cl.order_id,
         string_agg(cl.invoice_key, ', ' order by cl.invoice_key)   as invoice_keys,
         bool_or(cl.confirmed)                                       as confirmed,
         count(*)                                                    as keys_n,
         count(i.invoice_key)                                        as found_n,
         sum(i.invoice_total_bhd)                                    as invoice_total_bhd,
         sum(i.invoice_net_bhd)                                      as invoice_net_bhd,
         max(i.focus_salesman)                                       as focus_salesman,
         max(i.focus_customer)                                       as focus_customer,
         min(i.invoice_date)                                         as invoice_date,
         -- Focus writes '<rep> - Acc WH': the rep matches when the name is equal or is that
         -- name followed by ' - ' (LIKE focus_name || ' - %', without LIKE's wildcards)
         bool_or(i.invoice_key is not null
                 and not (lower(coalesce(i.focus_salesman, '')) = lower(coalesce(s.focus_name, o.salesman_name, ''))
                          or starts_with(lower(coalesce(i.focus_salesman, '')),
                                         lower(coalesce(s.focus_name, o.salesman_name, '')) || ' - ')))
                                                                     as salesman_off,
         bool_or(pi.orders_n > 1 and pi.confirmed_n < pi.orders_n)   as reused
  from claim cl
  join shop_orders o   on o.id = cl.order_id
  left join salesmen s on s.id = o.salesman_id
  left join inv i      on i.invoice_key = cl.invoice_key
  left join per_inv pi on pi.invoice_key = cl.invoice_key
  group by 1
),
grp as (                  -- the orders sharing any of this order's invoices (itself included) and their value together
  select x.order_id, count(*) as orders_n,
         sum(coalesce(o.total_confirmed_bhd, o.total_bhd, 0)) as orders_total_bhd
  from (select distinct a.order_id, b.order_id as other_id
        from claim a join claim b on b.invoice_key = a.invoice_key) x
  join shop_orders o on o.id = x.other_id
  group by 1
)
select o.id                                              as order_id,
       o.order_no,
       o.status,
       o.delivered_at,
       o.customer_shop,
       o.salesman_id,
       o.salesman_name,
       s.focus_name                                      as salesman_focus_name,
       coalesce(o.total_confirmed_bhd, o.total_bhd)      as order_total_bhd,
       o.payment_status,
       -- the key actually compared (normalised; the first when there are several — see invoice_keys),
       -- so focus_invoice_no is null exactly when missing_invoice is true
       nullif(split_part(p.invoice_keys, ', ', 1), '')   as focus_invoice_no,
       p.invoice_total_bhd,
       p.invoice_net_bhd,
       p.focus_salesman,
       p.focus_customer,
       p.invoice_date,
       (p.order_id is null)                                                         as missing_invoice,
       (p.order_id is not null and p.found_n < p.keys_n)                            as invoice_not_found,
       coalesce(p.salesman_off, false)                                              as salesman_mismatch,
       (p.order_id is not null and p.found_n = p.keys_n
        and abs(coalesce(p.invoice_total_bhd, 0) - coalesce(g.orders_total_bhd, 0)) > 0.005)
                                                                                    as amount_mismatch,
       case when p.order_id is not null and p.found_n = p.keys_n
            then round(coalesce(p.invoice_total_bhd, 0) - coalesce(g.orders_total_bhd, 0), 3) end
                                                                                    as amount_diff_bhd,
       o.is_test,
       coalesce(p.reused, false)                                                    as invoice_reused,
       -- appended in R7a: CREATE OR REPLACE VIEW may only add columns at the end
       coalesce(g.orders_n, 0)::integer                                             as linked_orders_n,
       g.orders_total_bhd                                                           as linked_orders_total_bhd,
       p.invoice_keys,
       case when p.confirmed then 'confirmed' when p.order_id is not null then 'typed' end
                                                                                    as link_state
from shop_orders o
left join salesmen s on s.id = o.salesman_id
left join per_ord p  on p.order_id = o.id
left join grp g      on g.order_id = o.id
where o.status = 'delivered';

revoke all on v_shop_focus_recon from anon, authenticated;

comment on view v_shop_focus_recon is
  'Delivered marketplace orders against the Focus sales ledger (v_sales), R7a. The order''s invoices are its confirmed shop_order_focus_links, else the focus_invoice_no typed at Delivered (unless that pair was rejected); focus_invoice_no reports the key compared; keys are normalised on both sides (SI : prefix, case, spaces). Flags: missing invoice, invoice not found in the uploaded ledger, salesman mismatch (suffix-aware: ''<rep> - Acc WH'' matches <rep>), amount mismatch (> 0.005 BHD: the invoice total against the SUM of every order on it), invoice_reused (several orders on one invoice, not all confirmed). Carries customer names: service role only.';

-- ── 3. v_shop_focus_candidates: which Focus invoice may cover which open order ─
create or replace view v_shop_focus_candidates as
with ord as (             -- live orders still without a confirmed Focus link
  select o.id                                                      as order_id,
         o.order_no,
         o.status                                                  as order_status,
         o.created_at                                              as order_created_at,
         o.customer_shop,
         o.salesman_id,
         o.salesman_name,
         lower(coalesce(nullif(trim(s.focus_name), ''), o.salesman_name, ''))  as rep,
         coalesce(o.total_confirmed_bhd, o.total_bhd)              as order_total_bhd,
         nullif(upper(regexp_replace(trim(coalesce(o.focus_invoice_no, '')), '^SI\s*:\s*', '', 'i')), '')
                                                                   as typed_invoice_key,
         (o.created_at at time zone 'Asia/Bahrain')::date          as created_day
  from shop_orders o
  left join salesmen s on s.id = o.salesman_id
  where o.status in ('new', 'confirmed', 'packed', 'out_for_delivery', 'delivered')
    and not coalesce(o.is_test, false)
    and not exists (select 1 from shop_order_focus_links l where l.order_id = o.id and l.state = 'confirmed')
),
since as (select min(created_day) - 1 as d0 from ord),
cmap as (                 -- the manual stock-name → catalog code overrides (one code per stock name)
  select distinct on (stock_item_name) stock_item_name, item_code
  from catalog_stock_map
  order by stock_item_name, item_code
),
oline as (                -- the order by SKU (removed and zero lines left out; confirmed quantities and prices first)
  select l.order_id,
         upper(trim(l.item_code))                                  as sku,
         sum(coalesce(l.qty_confirmed, l.qty))                     as qty,
         round(sum(coalesce(l.unit_price_confirmed, l.unit_price_bhd, 0) * coalesce(l.qty_confirmed, l.qty))
               / nullif(sum(coalesce(l.qty_confirmed, l.qty)), 0), 3)  as unit_price,
         bool_and(coalesce(l.backorder, false) or coalesce(l.line_status, '') = 'backorder')  as backorder
  from shop_order_lines l
  join ord on ord.order_id = l.order_id
  where coalesce(l.line_status, 'ok') <> 'removed' and coalesce(l.qty_confirmed, l.qty) > 0
  group by 1, 2
),
fl as (                   -- Focus invoice lines since the oldest open order, keyed and SKU-resolved
  select upper(regexp_replace(trim(v.invoice_no), '^SI\s*:\s*', '', 'i'))  as invoice_key,
         v.sale_date, v.salesman_resolved, v.customer_name, v.narration,
         upper(trim(coalesce(m.item_code, v.sku_code, v.item_name)))      as sku,
         coalesce(v.quantity, 0)                                          as qty,
         coalesce(v.revenue_bhd, 0)                                       as revenue_bhd
  from v_sales v
  left join cmap m on m.stock_item_name = v.item_name
  where v.invoice_no is not null and trim(v.invoice_no) <> ''
    and v.sale_date >= (select d0 from since)
),
inv as (
  select invoice_key,
         min(sale_date)                        as invoice_date,
         max(salesman_resolved)                as focus_salesman,
         max(customer_name)                    as focus_customer,
         sum(revenue_bhd)                      as invoice_total_bhd,
         string_agg(distinct narration, ' | ') as narration
  from fl
  group by 1
),
iline as (                -- the invoice by SKU; the unit price is what the line was sold at (revenue / qty)
  select invoice_key, sku, sum(qty) as qty,
         round(sum(revenue_bhd) / nullif(sum(qty), 0), 3) as unit_price
  from fl
  group by 1, 2
),
-- tier 1: the invoice's Narration names the order (storekeeper types YQ-2609-0019; several allowed)
nref as (
  select distinct i.invoice_key, upper(m[1]) || '-' || m[2] || '-' || m[3] as order_no
  from inv i
  cross join lateral regexp_matches(i.narration, '([A-Za-z]{2,4})[ -]?([0-9]{4})[ -]?([0-9]{4})', 'g') as m
  where i.narration ~ '[0-9]{4}[ -]?[0-9]{4}'
),
ref_n as (
  select o.order_id, n.invoice_key
  from nref n
  join ord o on o.order_no = n.order_no
  join inv i on i.invoice_key = n.invoice_key
  where i.invoice_date >= o.created_day - 1
),
-- tier 2: a Stock Issue Voucher's Narration names the order; the same rep's SI with those lines follows within 2 days
sio_rows as (
  select upper(trim(sm.voucher))                                      as sio_key,
         sm.move_date, sm.to_warehouse_name, sm.narration,
         upper(trim(coalesce(m.item_code, p.sku_code, sm.item_name)))   as sku
  from stock_movements sm
  left join product_aliases pa on pa.alias_text = sm.item_name
  left join products p         on p.id = pa.product_id
  left join cmap m             on m.stock_item_name = sm.item_name
  where sm.voucher_type = 'Stock Issue Voucher'
    and sm.to_warehouse_name is not null
    and sm.move_date >= (select d0 from since)
),
sref as (
  select distinct s.sio_key, upper(m[1]) || '-' || m[2] || '-' || m[3] as order_no
  from (select sio_key, string_agg(distinct narration, ' | ') as narration
        from sio_rows where narration ~ '[0-9]{4}[ -]?[0-9]{4}' group by 1) s
  cross join lateral regexp_matches(s.narration, '([A-Za-z]{2,4})[ -]?([0-9]{4})[ -]?([0-9]{4})', 'g') as m
),
sio as (
  select sio_key, min(move_date) as sio_date, max(to_warehouse_name) as to_rep, count(distinct sku) as skus
  from sio_rows
  where sio_key in (select sio_key from sref)
  group by 1
),
ref_s as (
  select o.order_id, i.invoice_key, min(s.sio_key) as sio_key
  from sref r
  join ord o on o.order_no = r.order_no
  join sio s on s.sio_key = r.sio_key and s.sio_date >= o.created_day - 1
  join inv i on lower(trim(split_part(i.focus_salesman, ' - ', 1))) = lower(trim(split_part(s.to_rep, ' - ', 1)))
            and i.invoice_date between s.sio_date and s.sio_date + 2
  where (select count(distinct sr.sku)
         from sio_rows sr join iline il on il.invoice_key = i.invoice_key and il.sku = sr.sku
         where sr.sio_key = s.sio_key) >= 0.6 * s.skus
  group by 1, 2
),
-- tier 3: same rep (Focus adds ' - Acc WH'), invoice dated order day -1 … +7; the SKU share is checked below
rep_inv as (
  select o.order_id, i.invoice_key
  from ord o
  join inv i on (lower(i.focus_salesman) = o.rep or starts_with(lower(i.focus_salesman), o.rep || ' - '))
            and i.invoice_date between o.created_day - 1 and o.created_day + 7
  where o.rep <> ''
),
pairs as (
  select order_id, invoice_key from rep_inv
  union select order_id, invoice_key from ref_n
  union select order_id, invoice_key from ref_s
),
ov as (select p.order_id, p.invoice_key, a.sku, a.qty, a.unit_price, a.backorder
       from pairs p join oline a on a.order_id = p.order_id),
iv as (select p.order_id, p.invoice_key, b.sku, b.qty, b.unit_price
       from pairs p join iline b on b.invoice_key = p.invoice_key),
cmp as (                  -- line by line: the order's SKUs against the invoice's
  select coalesce(ov.order_id, iv.order_id)          as order_id,
         coalesce(ov.invoice_key, iv.invoice_key)    as invoice_key,
         count(ov.sku)                               as lines_order,
         count(iv.sku)                               as lines_invoice,
         count(*) filter (where ov.sku is not null and iv.sku is not null and ov.qty = iv.qty
                          and abs(coalesce(ov.unit_price, 0) - coalesce(iv.unit_price, 0)) < 0.0005)   as lines_matched,
         count(*) filter (where ov.sku is not null and iv.sku is not null and ov.qty <> iv.qty
                          and abs(coalesce(ov.unit_price, 0) - coalesce(iv.unit_price, 0)) < 0.0005)   as lines_qty_diff,
         count(*) filter (where ov.sku is not null and iv.sku is not null
                          and abs(coalesce(ov.unit_price, 0) - coalesce(iv.unit_price, 0)) >= 0.0005)  as lines_price_diff,
         count(*) filter (where iv.sku is null)      as lines_missing_on_invoice,
         count(*) filter (where ov.sku is null)      as lines_extra_on_invoice,
         -- the share is taken over the lines expected to ship: a backorder line counts only
         -- when the order has nothing else
         count(*) filter (where ov.sku is not null and not ov.backorder)                         as ready_n,
         count(*) filter (where ov.sku is not null and not ov.backorder and iv.sku is not null)  as ready_hit,
         count(*) filter (where ov.sku is not null and iv.sku is not null)                       as hit
  from ov
  full join iv on iv.order_id = ov.order_id and iv.invoice_key = ov.invoice_key and iv.sku = ov.sku
  group by 1, 2
),
alloc as (                -- what confirmed links already hold of each invoice
  select l.invoice_key, count(*) as linked_n, sum(coalesce(o.total_confirmed_bhd, o.total_bhd, 0)) as linked_bhd
  from shop_order_focus_links l
  join shop_orders o on o.id = l.order_id
  where l.state = 'confirmed' and o.status <> 'cancelled'
  group by 1
),
scored as (
  select p.order_id, p.invoice_key, rs.sio_key,
         (rn.order_id is not null)                                     as by_narration,
         (ri.order_id is not null)                                     as same_rep_window,
         case when c.ready_n > 0 then round(c.ready_hit::numeric / c.ready_n, 3)
              when c.lines_order > 0 then round(c.hit::numeric / c.lines_order, 3) end  as overlap_share,
         coalesce(c.lines_order, 0)               as lines_order,
         coalesce(c.lines_invoice, 0)             as lines_invoice,
         coalesce(c.lines_matched, 0)             as lines_matched,
         coalesce(c.lines_qty_diff, 0)            as lines_qty_diff,
         coalesce(c.lines_price_diff, 0)          as lines_price_diff,
         coalesce(c.lines_missing_on_invoice, 0)  as lines_missing_on_invoice,
         coalesce(c.lines_extra_on_invoice, 0)    as lines_extra_on_invoice
  from pairs p
  left join ref_n rn   on rn.order_id = p.order_id and rn.invoice_key = p.invoice_key
  left join ref_s rs   on rs.order_id = p.order_id and rs.invoice_key = p.invoice_key
  left join rep_inv ri on ri.order_id = p.order_id and ri.invoice_key = p.invoice_key
  left join cmp c      on c.order_id = p.order_id and c.invoice_key = p.invoice_key
),
tiered as (
  select sc.*,
         case when sc.by_narration then 'narration_ref'
              when sc.sio_key is not null then 'sio_ref'
              -- a 1-2 product order proves little by SKU presence: its lines must match exactly
              when sc.same_rep_window and sc.overlap_share >= 0.6
                   and (sc.lines_order > 2 or sc.lines_matched = sc.lines_order) then 'auto_items' end  as method
  from scored sc
),
kept as (
  select t.*,
         case t.method when 'narration_ref' then 1.000 when 'sio_ref' then 0.950
                       -- SKU presence, discounted by how many lines match exactly (qty and price)
                       else least(0.900, t.overlap_share * (0.5 + 0.5 * t.lines_matched::numeric
                                                             / greatest(t.lines_order, 1))) end::numeric(4,3)  as confidence,
         o.order_no, o.order_status, o.order_created_at, o.customer_shop, o.salesman_id, o.salesman_name,
         o.order_total_bhd, o.typed_invoice_key, o.created_day,
         i.invoice_date, i.focus_salesman, i.focus_customer, i.invoice_total_bhd,
         coalesce(a.linked_n, 0)                                                   as invoice_linked_n,
         coalesce(a.linked_bhd, 0)                                                 as invoice_linked_bhd
  from tiered t
  join ord o      on o.order_id = t.order_id
  join inv i      on i.invoice_key = t.invoice_key
  left join alloc a on a.invoice_key = t.invoice_key
  where t.method is not null
    -- a pair someone rejected is never suggested again
    and not exists (select 1 from shop_order_focus_links r
                    where r.order_id = t.order_id and r.invoice_key = t.invoice_key and r.state = 'rejected')
    -- an invoice the confirmed links already cover in full is not auto-suggested to another order
    and not (t.method = 'auto_items' and coalesce(a.linked_n, 0) > 0
             and i.invoice_total_bhd - coalesce(a.linked_bhd, 0) <= 0.005)
)
select k.order_id,
       k.order_no,
       k.order_status,
       k.order_created_at,
       k.customer_shop,
       k.salesman_id,
       k.salesman_name,
       k.order_total_bhd,
       k.typed_invoice_key,
       k.invoice_key,
       k.invoice_date,
       k.focus_salesman,
       k.focus_customer,
       k.invoice_total_bhd,
       k.invoice_linked_n,
       k.invoice_linked_bhd,
       round(k.invoice_total_bhd - k.invoice_linked_bhd, 3)           as invoice_open_bhd,
       round(k.invoice_total_bhd - k.order_total_bhd, 3)              as amount_diff_bhd,
       k.method,
       k.confidence,
       k.sio_key,
       k.overlap_share,
       k.lines_order,
       k.lines_invoice,
       k.lines_matched,
       k.lines_qty_diff,
       k.lines_price_diff,
       k.lines_missing_on_invoice,
       k.lines_extra_on_invoice,
       count(*) over (partition by k.invoice_key)::integer            as invoice_orders_n,
       row_number() over (partition by k.order_id
                          order by coalesce(k.invoice_key = k.typed_invoice_key, false) desc,
                                   (k.method <> 'auto_items') desc, k.lines_matched desc,
                                   abs(k.invoice_total_bhd - k.order_total_bhd), k.confidence desc,
                                   abs(k.invoice_date - k.created_day), k.invoice_key)::integer  as rank
from kept k;

revoke all on v_shop_focus_candidates from anon, authenticated;

comment on view v_shop_focus_candidates is
  'Suggested Focus invoices for live marketplace orders without a confirmed shop_order_focus_links row (R7a). One row per (order, invoice): method narration_ref (1.000, the invoice Narration names the order number) | sio_ref (0.950, a Stock Issue Voucher Narration names it and the same rep''s SI with those lines follows within 2 days) | auto_items (same rep, invoice dated order day -1..+7, >= 60 % of the order''s SKUs on the invoice, a 1-2 line order only on exact lines; confidence = that share x (0.5 + 0.5 x exact-line share), capped 0.9). Line diff counts, rank per order (1 = best), invoice_orders_n = orders this invoice is suggested for. Rejected pairs never return; an invoice fully covered by confirmed links is not auto-suggested again. Carries customer names: service role only.';

-- ── self-check ─────────────────────────────────────────────────────────────────
do $$
declare
  c text;
begin
  if to_regclass('public.shop_order_focus_links') is null then
    raise exception 'shop_order_focus_links missing';
  end if;
  foreach c in array array['id', 'order_id', 'invoice_key', 'sio_key', 'method', 'confidence', 'allocated_bhd',
                           'state', 'note', 'created_by', 'created_at', 'decided_by', 'decided_at'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'shop_order_focus_links' and column_name = c) then
      raise exception 'shop_order_focus_links.% missing', c;
    end if;
  end loop;
  if not exists (select 1 from pg_constraint where conname = 'shop_order_focus_links_order_invoice_key') then
    raise exception 'shop_order_focus_links unique (order_id, invoice_key) missing';
  end if;
  if not exists (select 1 from pg_constraint where conname = 'shop_order_focus_links_state_check'
                 and pg_get_constraintdef(oid) like '%rejected%') then
    raise exception 'shop_order_focus_links state CHECK missing';
  end if;
  if not exists (select 1 from pg_constraint where conname = 'shop_order_focus_links_method_check'
                 and pg_get_constraintdef(oid) like '%narration_ref%') then
    raise exception 'shop_order_focus_links method CHECK missing';
  end if;
  if not exists (select 1 from pg_tables where schemaname = 'public' and tablename = 'shop_order_focus_links' and rowsecurity) then
    raise exception 'shop_order_focus_links must have RLS enabled';
  end if;
  if to_regclass('public.v_shop_focus_recon') is null or to_regclass('public.v_shop_focus_candidates') is null then
    raise exception 'v_shop_focus_recon / v_shop_focus_candidates missing';
  end if;
  foreach c in array array['invoice_reused', 'linked_orders_n', 'linked_orders_total_bhd', 'invoice_keys', 'link_state'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'v_shop_focus_recon' and column_name = c) then
      raise exception 'v_shop_focus_recon.% missing', c;
    end if;
  end loop;
  -- both views must answer on live data; the R3 invariants still hold
  if exists (select 1 from v_shop_focus_recon where focus_invoice_no is null and not missing_invoice) then
    raise exception 'v_shop_focus_recon: a delivered order without an invoice must read missing_invoice';
  end if;
  if exists (select 1 from v_shop_focus_recon where missing_invoice and (salesman_mismatch or amount_mismatch or invoice_reused)) then
    raise exception 'v_shop_focus_recon: no invoice means no mismatch or reuse flags';
  end if;
  if exists (select 1 from v_shop_focus_candidates
             where method not in ('narration_ref', 'sio_ref', 'auto_items')
                or confidence is null or confidence < 0 or confidence > 1 or rank < 1) then
    raise exception 'v_shop_focus_candidates: method / confidence / rank out of range';
  end if;
  if exists (select 1 from v_shop_focus_candidates k
             join shop_order_focus_links l on l.order_id = k.order_id and l.state = 'confirmed') then
    raise exception 'v_shop_focus_candidates: an order with a confirmed link must not be suggested again';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public'
               and table_name in ('shop_order_focus_links', 'v_shop_focus_recon', 'v_shop_focus_candidates')
               and grantee in ('anon', 'authenticated')) then
    raise exception 'shop_order_focus_links / v_shop_focus_recon / v_shop_focus_candidates must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and (has_table_privilege('yq_readonly', 'public.v_shop_focus_candidates', 'SELECT')
          or has_table_privilege('yq_readonly', 'public.v_shop_focus_recon', 'SELECT')
          or has_table_privilege('yq_readonly', 'public.shop_order_focus_links', 'SELECT')) then
    raise exception 'the Focus link objects carry customer names and must not be readable by yq_readonly';
  end if;
  raise notice 'r7_focus_links: ok (shop_order_focus_links with RLS, v_shop_focus_recon normalised + many-to-many, v_shop_focus_candidates answers, no anon/authenticated/yq_readonly grants)';
end $$;
