-- Shop analytics views — release R7d "Shop analytics done properly" (plan §13; audit ANA-4/5, OFF-8, INT-13).
-- Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7d_analytics_views_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7d_analytics_views_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7d_analytics_views_reverse.sql
--   Order:    any time (it reads shop_events, shop_orders, shop_order_lines, shop_customers, salesmen,
--             customers and catalog_items, which all exist since the marketplace migration). The API
--             may deploy first: app/shop.py probes these views and, until they exist, reads the raw
--             events the old way (paged now, so it no longer stops at PostgREST's 1,000 rows).
--
-- Why: GET /shop/analytics pulled every shop_events row of the window through PostgREST, which
-- answers 1,000 rows at most, so the page saw about a third of the week (3,315 events on
-- 27-Sep-2026). It also counted a "checkout" event the storefront never sends, counted every
-- keystroke of a search as a search, and read the order totals as requested, not as confirmed.
-- These views do the counting in SQL; the API reads them through the read-only RPC as yq_readonly
-- with the window as parameters.
--
--   v_shop_funnel_daily  (new)      one row per Bahrain day x rep link (referral_code, salesman_id) x src.
--                                    devices and sessions, and the sessions / devices that reached each
--                                    step: view, item, add, cart, checkout (checkout_start), order; the
--                                    sessions that searched / shared / installed / reordered / cancelled;
--                                    new_devices (the device's first event ever). A session or a device is
--                                    counted once per Bahrain day, and all of one device's events of a
--                                    day land on ONE row: the day's first rep link (else no link). So the
--                                    columns add up across rows without counting a visit twice.
--   v_shop_search_daily  (new)      final typed searches per Bahrain day x rep link x term. A search followed
--                                    within 30 s on the same device by a query that starts with it (a
--                                    longer one, or the same one again) is typing, not a search, and is
--                                    dropped; chip, facet and quick-order pings (meta.rail) are not typed
--                                    searches and are left out.
--   v_shop_search_terms  (FIXED)    same columns as before (term, searches, zero_results, sessions,
--                                    last_seen), now over the final typed searches only.
--   v_shop_rail_perf     (FIXED)    same six columns first, then referral_code, salesman_id, reorders,
--                                    devices APPENDED; `day` is now the Bahrain day (was UTC); only rail
--                                    taps / reco taps / adds / reorders count (a search or view event that
--                                    carries meta.rail is not a rail tap).
--   v_shop_vitals        (new)      one row per speed beacon (the storefront sends one per visit): the Bahrain
--                                    day, rep link, route template, viewport class, catalog source and the
--                                    numbers. No session, device, IP or user agent.
--   v_merchant_360       (new)      one row per marketplace merchant (shop_customers): orders, value on the
--                                    effective money rule (confirmed ?? requested), confirmed / delivered
--                                    value, first / last order, AOV, 30 / 60 / 90-day windows, cadence from
--                                    the distinct order DAYS (own median gap with >= 4 order days, else the
--                                    all-shops median once there are >= 10 gaps), due status, the dormant
--                                    rule (>= 2 of: overdue > 2x cadence, 60-day value < 60 % of the 60 days
--                                    before, 60-day SKU range < 70 % of the 60 days before), top categories
--                                    and SKUs, the rep, the area and the Focus customer when
--                                    shop_customers.focus_customer_id is set. Test orders never count.
--                                    Shop name and area only: no phone, email, person name or device.
--
-- Every view is owned by postgres (the role that applies this file), is NOT security_invoker
-- (yq_readonly reads the view, never the tables beneath), is revoked from anon and authenticated
-- and granted SELECT to yq_readonly only. Nothing here writes a row.

-- ── 1. the funnel, per Bahrain day x rep link x src ─────────────────────────────
create or replace view v_shop_funnel_daily as
with ev as (
  select e.id,
         e.ts,
         e.event,
         e.session_id,
         e.device_id,
         e.salesman_id,
         e.src,
         (e.ts at time zone 'Asia/Bahrain')::date                         as day,
         lower(nullif(btrim(e.referral_code), ''))                         as ref,
         coalesce(e.device_id, e.session_id, 'event:' || e.id::text)       as who
  from shop_events e
  where e.event <> 'error'
), tagged as (
  -- every event of one device on one day carries that day's first rep link (else none)
  select ev.*,
         first_value(ev.ref)         over w as g_ref,
         first_value(ev.salesman_id) over w as g_sid,
         first_value(ev.src)         over w as g_src
  from ev
  window w as (partition by ev.day, ev.who order by (ev.ref is null), ev.ts, ev.id
               rows between unbounded preceding and unbounded following)
), firsts as (
  select device_id, min(ts) as first_ts
  from shop_events
  where device_id is not null and event <> 'error'
  group by device_id
)
select t.day                                                                              as day,
       t.g_ref                                                                            as referral_code,
       t.g_sid                                                                            as salesman_id,
       t.g_src                                                                            as src,
       count(distinct t.device_id)                                                        as devices,
       count(distinct t.session_id)                                                       as sessions,
       count(distinct t.session_id) filter (where t.event = 'view')                       as view_sessions,
       count(distinct t.session_id) filter (where t.event = 'item')                       as item_sessions,
       count(distinct t.session_id) filter (where t.event = 'add')                        as add_sessions,
       count(distinct t.session_id) filter (where t.event = 'cart')                       as cart_sessions,
       count(distinct t.session_id) filter (where t.event in ('checkout_start', 'checkout')) as checkout_sessions,
       -- the order event is written by the API and carries no session yet: the device stands in
       count(distinct coalesce(t.session_id, t.device_id)) filter (where t.event = 'order') as order_sessions,
       count(distinct t.device_id) filter (where t.event = 'view')                        as view_devices,
       count(distinct t.device_id) filter (where t.event = 'item')                        as item_devices,
       count(distinct t.device_id) filter (where t.event = 'add')                         as add_devices,
       count(distinct t.device_id) filter (where t.event = 'cart')                        as cart_devices,
       count(distinct t.device_id) filter (where t.event in ('checkout_start', 'checkout')) as checkout_devices,
       count(distinct t.device_id) filter (where t.event = 'order')                       as order_devices,
       count(distinct t.session_id) filter (where t.event in ('search', 'search_zero'))   as search_sessions,
       count(distinct t.session_id) filter (where t.event = 'share')                      as share_sessions,
       count(distinct t.session_id) filter (where t.event = 'install')                    as install_sessions,
       count(distinct t.session_id) filter (where t.event = 'reorder')                    as reorder_sessions,
       count(distinct t.session_id) filter (where t.event = 'cancel')                     as cancel_sessions,
       count(distinct t.device_id) filter (where t.ts = f.first_ts)                       as new_devices
from tagged t
left join firsts f on f.device_id = t.device_id
group by t.day, t.g_ref, t.g_sid, t.g_src;

revoke all on v_shop_funnel_daily from anon, authenticated;
grant select on v_shop_funnel_daily to yq_readonly;

comment on view v_shop_funnel_daily is
  'Shop funnel (R7d): one row per Bahrain day x rep link x src. Sessions and devices are counted once per day, and all of one device''s events of a day land on one row (the day''s first rep link), so every column adds up across rows. checkout = checkout_start. No session, device or IP value is exposed. yq_readonly only.';

-- ── 2. final typed searches, per Bahrain day x rep link x term ─────────────────
create or replace view v_shop_search_daily as
with typed as (
  select e.id,
         e.ts,
         e.event,
         e.session_id,
         e.device_id,
         e.salesman_id,
         lower(nullif(btrim(e.referral_code), ''))                         as referral_code,
         lower(btrim(e.meta->>'q'))                                         as term,
         coalesce(e.device_id, e.session_id, 'event:' || e.id::text)       as who
  from shop_events e
  where e.event in ('search', 'search_zero')
    and nullif(btrim(e.meta->>'q'), '') is not null
    and not coalesce(e.meta ? 'rail', false)        -- chip / facet / quick-order pings are not typed searches
), seq as (
  select t.*,
         lead(t.term) over w as next_term,
         lead(t.ts)   over w as next_ts
  from typed t
  window w as (partition by t.who order by t.ts, t.id)
), final as (
  -- typing: the next search on this device, within 30 s, starts with this one (longer, or the same again)
  select s.*
  from seq s
  where not (s.next_ts is not null
             and s.next_ts - s.ts <= interval '30 seconds'
             and left(s.next_term, length(s.term)) = s.term)
)
select (f.ts at time zone 'Asia/Bahrain')::date                 as day,
       f.referral_code                                          as referral_code,
       f.salesman_id                                            as salesman_id,
       f.term                                                   as term,
       count(*)                                                 as searches,
       count(*) filter (where f.event = 'search_zero')          as zero_results,
       count(distinct f.session_id)                             as sessions,
       count(distinct f.device_id)                              as devices,
       max(f.ts)                                                as last_seen
from final f
group by 1, 2, 3, 4;

revoke all on v_shop_search_daily from anon, authenticated;
grant select on v_shop_search_daily to yq_readonly;

comment on view v_shop_search_daily is
  'Final typed searches (R7d) per Bahrain day x rep link x term. A search followed within 30 s on the same device by a query that starts with it is typing and is dropped; chip / facet / quick-order pings (meta.rail) are left out. zero_results = the final search found nothing. yq_readonly only.';

-- ── 3. v_shop_search_terms: the same five columns, over final typed searches only ─
create or replace view v_shop_search_terms as
select d.term                                                   as term,
       sum(d.searches)::bigint                                  as searches,
       sum(d.zero_results)::bigint                              as zero_results,
       sum(d.sessions)::bigint                                  as sessions,
       max(d.last_seen)                                         as last_seen
from v_shop_search_daily d
group by d.term;

comment on view v_shop_search_terms is
  'What merchants search for (R7d fix): final typed searches only, all time. sessions = sessions per day, added up.';

-- ── 4. v_shop_rail_perf: the six columns as before, Bahrain day, rep link APPENDED ─
create or replace view v_shop_rail_perf as
select coalesce(e.meta->>'rail', 'reorder')                              as rail,
       (e.ts at time zone 'Asia/Bahrain')::date                          as day,
       count(*) filter (where e.event = 'rail_click')                    as clicks,
       count(*) filter (where e.event = 'reco_click')                    as reco_clicks,
       count(*) filter (where e.event = 'add')                           as adds,
       count(distinct e.session_id)                                      as sessions,
       lower(nullif(btrim(e.referral_code), ''))                         as referral_code,
       e.salesman_id                                                     as salesman_id,
       count(*) filter (where e.event = 'reorder')                       as reorders,
       count(distinct e.device_id)                                       as devices
from shop_events e
where e.event in ('rail_click', 'reco_click', 'add', 'reorder')
  and (coalesce(e.meta ? 'rail', false) or e.event = 'reorder')
group by 1, 2, 7, 8;

comment on view v_shop_rail_perf is
  'Rail performance (R7d fix): per rail x Bahrain day x rep link — taps (clicks + reco_clicks + reorders), adds from the rail, sessions, devices.';

-- ── 5. the speed beacons (one per visit), numbers only ───────────────────────────
create or replace view v_shop_vitals as
with b as (
  select e.ts, e.referral_code, e.salesman_id, e.meta
  from shop_events e
  where e.event = 'vitals'
)
select (b.ts at time zone 'Asia/Bahrain')::date                                         as day,
       lower(nullif(btrim(b.referral_code), ''))                                         as referral_code,
       b.salesman_id                                                                    as salesman_id,
       left(b.meta->>'route', 40)                                                       as route,
       left(b.meta->>'vp', 12)                                                          as vp,
       left(coalesce(nullif(b.meta->>'catalog_src', ''), 'none'), 24)                   as catalog_src,
       case when b.meta->>'lcp' ~ '^-?[0-9]+(\.[0-9]+)?$' then (b.meta->>'lcp')::numeric end               as lcp_ms,
       case when b.meta->>'inp' ~ '^-?[0-9]+(\.[0-9]+)?$' then (b.meta->>'inp')::numeric end               as inp_ms,
       case when b.meta->>'cls' ~ '^-?[0-9]+(\.[0-9]+)?$' then (b.meta->>'cls')::numeric end               as cls,
       case when b.meta->>'lcp_ttfb' ~ '^-?[0-9]+(\.[0-9]+)?$' then (b.meta->>'lcp_ttfb')::numeric end     as lcp_ttfb_ms,
       case when b.meta->>'lcp_delay' ~ '^-?[0-9]+(\.[0-9]+)?$' then (b.meta->>'lcp_delay')::numeric end   as lcp_delay_ms,
       case when b.meta->>'lcp_load' ~ '^-?[0-9]+(\.[0-9]+)?$' then (b.meta->>'lcp_load')::numeric end     as lcp_load_ms,
       case when b.meta->>'lcp_render' ~ '^-?[0-9]+(\.[0-9]+)?$' then (b.meta->>'lcp_render')::numeric end as lcp_render_ms,
       case when b.meta->>'catalog_ms' ~ '^-?[0-9]+(\.[0-9]+)?$' then (b.meta->>'catalog_ms')::numeric end as catalog_ms,
       (b.meta ?| array['lcp', 'inp', 'cls'])                                           as has_metric
from b;

revoke all on v_shop_vitals from anon, authenticated;
grant select on v_shop_vitals to yq_readonly;

comment on view v_shop_vitals is
  'Storefront speed beacons (R7d): one row per visit beacon with the Bahrain day, rep link, route template, viewport class, catalog source and the numbers (ms; cls unitless). No session, device, IP or user agent. yq_readonly only.';

-- ── 6. one row per marketplace merchant ─────────────────────────────────────────
create or replace view v_merchant_360 as
with today as (
  select (now() at time zone 'Asia/Bahrain')::date as d
), o as (
  -- live orders of a known merchant: not cancelled, not a test; the effective money rule
  select o.id,
         o.customer_id,
         o.salesman_id,
         o.status,
         o.created_at,
         (o.created_at at time zone 'Asia/Bahrain')::date                                as day,
         coalesce(o.total_confirmed_bhd, o.total_bhd)                                   as value_bhd,
         (o.status in ('confirmed', 'packed', 'out_for_delivery', 'delivered')
          or o.confirmed_at is not null)                                                as started
  from shop_orders o
  where o.customer_id is not null
    and not coalesce(o.is_test, false)
    and o.status <> 'cancelled'
), cancelled as (
  select customer_id, count(*) as n
  from shop_orders
  where customer_id is not null and not coalesce(is_test, false) and status = 'cancelled'
  group by customer_id
), agg as (
  select o.customer_id,
         count(*)                                                                        as orders,
         count(*) filter (where o.status in ('new', 'confirmed', 'packed', 'out_for_delivery')) as open_orders,
         sum(o.value_bhd)                                                                as value_bhd,
         coalesce(sum(o.value_bhd) filter (where o.status in ('confirmed', 'packed', 'out_for_delivery', 'delivered')), 0) as confirmed_value_bhd,
         coalesce(sum(o.value_bhd) filter (where o.status = 'delivered'), 0)             as delivered_value_bhd,
         min(o.created_at)                                                               as first_order_at,
         max(o.created_at)                                                               as last_order_at,
         count(distinct o.day)                                                           as order_days,
         count(*) filter (where o.day >= t.d - 29)                                       as orders_30d,
         coalesce(sum(o.value_bhd) filter (where o.day >= t.d - 29), 0)                  as value_30d_bhd,
         count(*) filter (where o.day >= t.d - 89)                                       as orders_90d,
         coalesce(sum(o.value_bhd) filter (where o.day >= t.d - 89), 0)                  as value_90d_bhd,
         coalesce(sum(o.value_bhd) filter (where o.day between t.d - 179 and t.d - 90), 0) as value_prev_90d_bhd,
         coalesce(sum(o.value_bhd) filter (where o.day >= t.d - 59), 0)                  as value_60d_bhd,
         coalesce(sum(o.value_bhd) filter (where o.day between t.d - 119 and t.d - 60), 0) as value_prev_60d_bhd
  from o
  cross join today t
  group by o.customer_id
), last_rep as (
  select distinct on (o.customer_id) o.customer_id, o.salesman_id
  from o
  where o.salesman_id is not null
  order by o.customer_id, o.created_at desc, o.id desc
), odays as (
  select distinct customer_id, day from o
), gaps as (
  select customer_id, day - lag(day) over (partition by customer_id order by day) as gap
  from odays
), own as (
  select customer_id, count(*) as gaps_n,
         percentile_cont(0.5) within group (order by gap) as median_gap
  from gaps
  where gap is not null
  group by customer_id
), everyone as (
  select count(*) as gaps_n,
         percentile_cont(0.5) within group (order by gap) as median_gap
  from gaps
  where gap is not null
), cats as (
  select distinct on (upper(item_code)) upper(item_code) as code, nullif(btrim(category), '') as category
  from catalog_items
  order by upper(item_code), id
), l0 as (
  select o.customer_id, o.id as order_id, o.day, o.started,
         upper(ln.item_code) as item_code, ln.display_name, ln.qty, ln.line_total_bhd, ln.line_total_confirmed,
         case when o.started
              then coalesce(ln.qty_confirmed,
                            case when ln.line_status in ('removed', 'unavailable', 'substituted') then 0 else ln.qty end)
              else ln.qty end                                                            as units
  from o
  join shop_order_lines ln on ln.order_id = o.id
), l as (
  -- the price lock: a line confirmed without a stored confirmed total is its ordered total, pro rata
  select l0.*,
         case when not l0.started then l0.line_total_bhd
              when l0.line_total_confirmed is not null then l0.line_total_confirmed
              when l0.units = l0.qty then l0.line_total_bhd
              when l0.qty > 0 then round(l0.line_total_bhd * l0.units / l0.qty, 3)
              else 0 end                                                                 as value_bhd,
         coalesce(c.category, 'OTHER')                                                   as category
  from l0
  left join cats c on c.code = l0.item_code
  where l0.units > 0
), lagg as (
  select l.customer_id,
         sum(l.units)                                                                    as units,
         count(distinct l.item_code)                                                     as skus_n,
         count(distinct l.item_code) filter (where l.day >= t.d - 59)                   as skus_60d,
         count(distinct l.item_code) filter (where l.day between t.d - 119 and t.d - 60) as skus_prev_60d
  from l
  cross join today t
  group by l.customer_id
), cat_rank as (
  select customer_id, category, sum(value_bhd) as value_bhd, sum(units) as units,
         row_number() over (partition by customer_id order by sum(value_bhd) desc, category) as rn
  from l
  group by customer_id, category
), cat_j as (
  select customer_id,
         jsonb_agg(jsonb_build_object('category', category, 'value_bhd', round(value_bhd, 3), 'units', units)
                   order by rn) as top_categories
  from cat_rank
  where rn <= 3
  group by customer_id
), sku_rank as (
  select customer_id, item_code, max(display_name) as display_name, sum(units) as units,
         sum(value_bhd) as value_bhd, count(distinct order_id) as orders,
         row_number() over (partition by customer_id order by sum(value_bhd) desc, item_code) as rn
  from l
  group by customer_id, item_code
), sku_j as (
  select customer_id,
         jsonb_agg(jsonb_build_object('item_code', item_code, 'display_name', display_name, 'units', units,
                                      'value_bhd', round(value_bhd, 3), 'orders', orders)
                   order by rn) as top_skus
  from sku_rank
  where rn <= 5
  group by customer_id
), base as (
  select c.id                                                                           as customer_id,
         nullif(btrim(c.shop), '')                                                      as shop,
         nullif(btrim(c.area), '')                                                      as area,
         coalesce(c.salesman_id, c.sticky_salesman_id, lr.salesman_id)                  as rep_id,
         case when c.salesman_id is not null then 'assigned'
              when c.sticky_salesman_id is not null then 'sticky'
              when lr.salesman_id is not null then 'last_order' end                    as rep_source,
         c.focus_customer_id                                                            as focus_customer_id,
         fc.name                                                                        as focus_customer_name,
         coalesce(a.orders, 0)                                                          as orders,
         coalesce(a.open_orders, 0)                                                     as open_orders,
         coalesce(x.n, 0)                                                               as cancelled_orders,
         round(coalesce(a.value_bhd, 0), 3)                                             as value_bhd,
         round(coalesce(a.confirmed_value_bhd, 0), 3)                                   as confirmed_value_bhd,
         round(coalesce(a.delivered_value_bhd, 0), 3)                                   as delivered_value_bhd,
         case when coalesce(a.orders, 0) > 0 then round(a.value_bhd / a.orders, 3) end  as aov_bhd,
         a.first_order_at                                                               as first_order_at,
         a.last_order_at                                                                as last_order_at,
         (t.d - (a.last_order_at at time zone 'Asia/Bahrain')::date)                    as days_since_last,
         coalesce(a.order_days, 0)                                                      as order_days,
         coalesce(a.orders_30d, 0)                                                      as orders_30d,
         round(coalesce(a.value_30d_bhd, 0), 3)                                         as value_30d_bhd,
         coalesce(a.orders_90d, 0)                                                      as orders_90d,
         round(coalesce(a.value_90d_bhd, 0), 3)                                         as value_90d_bhd,
         round(coalesce(a.value_prev_90d_bhd, 0), 3)                                    as value_prev_90d_bhd,
         round(coalesce(a.value_60d_bhd, 0), 3)                                         as value_60d_bhd,
         round(coalesce(a.value_prev_60d_bhd, 0), 3)                                    as value_prev_60d_bhd,
         coalesce(g.units, 0)                                                           as units,
         coalesce(g.skus_n, 0)                                                          as skus_n,
         coalesce(g.skus_60d, 0)                                                        as skus_60d,
         coalesce(g.skus_prev_60d, 0)                                                   as skus_prev_60d,
         round(ow.median_gap::numeric, 1)                                               as median_gap_days,
         case when coalesce(a.order_days, 0) >= 4 then round(ow.median_gap::numeric, 1)
              when ev.gaps_n >= 10 then round(ev.median_gap::numeric, 1) end            as cadence_days,
         case when coalesce(a.order_days, 0) >= 4 then 'own'
              when ev.gaps_n >= 10 then 'all_shops' end                                 as cadence_basis,
         (a.first_order_at is not null
          and (a.first_order_at at time zone 'Asia/Bahrain')::date >= t.d - 29)        as is_new,
         cj.top_categories                                                              as top_categories,
         sj.top_skus                                                                    as top_skus
  from shop_customers c
  cross join today t
  cross join everyone ev
  left join agg a       on a.customer_id = c.id
  left join cancelled x on x.customer_id = c.id
  left join last_rep lr on lr.customer_id = c.id
  left join own ow      on ow.customer_id = c.id
  left join lagg g      on g.customer_id = c.id
  left join cat_j cj    on cj.customer_id = c.id
  left join sku_j sj    on sj.customer_id = c.id
  left join customers fc on fc.id = c.focus_customer_id
), flagged as (
  select b.*,
         coalesce(b.cadence_days is not null and b.days_since_last > 2 * b.cadence_days, false)     as sig_overdue,
         coalesce(b.value_prev_60d_bhd > 0 and b.value_60d_bhd < 0.6 * b.value_prev_60d_bhd, false) as sig_value_drop,
         coalesce(b.skus_prev_60d > 0 and b.skus_60d < 0.7 * b.skus_prev_60d, false)                as sig_range_drop
  from base b
)
select f.customer_id,
       f.shop,
       f.area,
       f.rep_id,
       s.name                                                                           as rep_name,
       f.rep_source,
       f.focus_customer_id,
       f.focus_customer_name,
       f.orders,
       f.open_orders,
       f.cancelled_orders,
       f.value_bhd,
       f.confirmed_value_bhd,
       f.delivered_value_bhd,
       f.aov_bhd,
       f.first_order_at,
       f.last_order_at,
       f.days_since_last,
       f.order_days,
       f.orders_30d,
       f.value_30d_bhd,
       f.orders_90d,
       f.value_90d_bhd,
       f.value_prev_90d_bhd,
       f.value_60d_bhd,
       f.value_prev_60d_bhd,
       f.units,
       f.skus_n,
       f.skus_60d,
       f.skus_prev_60d,
       f.median_gap_days,
       f.cadence_days,
       f.cadence_basis,
       case when f.orders = 0 or f.cadence_days is null then 'unknown'
            when f.days_since_last > 2 * f.cadence_days then 'overdue'
            when f.days_since_last > 1.5 * f.cadence_days then 'due'
            else 'ok' end                                                              as due_status,
       f.sig_overdue,
       f.sig_value_drop,
       f.sig_range_drop,
       (f.sig_overdue::int + f.sig_value_drop::int + f.sig_range_drop::int)            as dormant_signals,
       (f.sig_overdue::int + f.sig_value_drop::int + f.sig_range_drop::int) >= 2       as dormant,
       f.is_new,
       coalesce(f.top_categories, '[]'::jsonb)                                          as top_categories,
       coalesce(f.top_skus, '[]'::jsonb)                                                as top_skus
from flagged f
left join salesmen s on s.id = f.rep_id;

revoke all on v_merchant_360 from anon, authenticated;
grant select on v_merchant_360 to yq_readonly;

comment on view v_merchant_360 is
  'Merchant 360 (R7d): one row per shop_customers row. Value = confirmed ?? requested (effective money rule), test and cancelled orders left out. Cadence from distinct Bahrain order days: own median gap with >= 4 order days, else the all-shops median once >= 10 gaps exist. Dormant = >= 2 of: overdue > 2x cadence, 60-day value < 60 % of the 60 days before, 60-day SKU range < 70 % of before. Shop name and area only: no phone, email, person name or device. yq_readonly only.';
comment on column v_merchant_360.due_status is
  'unknown (no cadence yet) | ok | due (> 1.5x cadence since the last order day) | overdue (> 2x).';

-- ── 7. self-check: fails the transaction if anything is off ────────────────────
do $$
declare
  v text;
  c text;
  views constant text[] := array['v_shop_funnel_daily', 'v_shop_search_daily', 'v_shop_search_terms',
                                 'v_shop_rail_perf', 'v_shop_vitals', 'v_merchant_360'];
  n1 bigint;
  n2 bigint;
begin
  foreach v in array views loop
    if to_regclass('public.' || v) is null then
      raise exception 'r7d_analytics_views: % missing', v;
    end if;
    if exists (select 1 from pg_class where oid = ('public.' || v)::regclass
               and coalesce(reloptions::text, '') like '%security_invoker%') then
      raise exception 'r7d_analytics_views: % must not be security_invoker (yq_readonly reads the view, not the tables)', v;
    end if;
    if (select pg_get_userbyid(relowner) from pg_class where oid = ('public.' || v)::regclass) <> 'postgres' then
      raise exception 'r7d_analytics_views: % must be owned by postgres', v;
    end if;
    if exists (select 1 from information_schema.role_table_grants
               where table_schema = 'public' and table_name = v and grantee in ('anon', 'authenticated')) then
      raise exception 'r7d_analytics_views: % must not be granted to anon/authenticated', v;
    end if;
    if exists (select 1 from pg_roles where rolname = 'yq_readonly')
       and not has_table_privilege('yq_readonly', 'public.' || v, 'SELECT') then
      raise exception 'r7d_analytics_views: yq_readonly cannot read %', v;
    end if;
    -- nothing personal or identifying travels in these views
    foreach c in array array['session_id', 'device_id', 'ip_hash', 'ua', 'customer_phone', 'customer_name',
                             'customer_email', 'phone', 'email', 'name', 'token', 'note', 'meta', 'device_ids'] loop
      if exists (select 1 from information_schema.columns
                 where table_schema = 'public' and table_name = v and column_name = c) then
        raise exception 'r7d_analytics_views: % must not carry %', v, c;
      end if;
    end loop;
  end loop;

  -- the live column names, types and order of the two views this file REPLACES are unchanged
  if (select string_agg(a.attname || ':' || format_type(a.atttypid, a.atttypmod), ',' order by a.attnum)
      from pg_attribute a where a.attrelid = 'public.v_shop_search_terms'::regclass and a.attnum > 0 and not a.attisdropped)
     <> 'term:text,searches:bigint,zero_results:bigint,sessions:bigint,last_seen:timestamp with time zone' then
    raise exception 'r7d_analytics_views: v_shop_search_terms changed shape';
  end if;
  if (select string_agg(a.attname || ':' || format_type(a.atttypid, a.atttypmod), ',' order by a.attnum)
      from pg_attribute a where a.attrelid = 'public.v_shop_rail_perf'::regclass and a.attnum > 0 and not a.attisdropped)
     not like 'rail:text,day:date,clicks:bigint,reco_clicks:bigint,adds:bigint,sessions:bigint,%' then
    raise exception 'r7d_analytics_views: v_shop_rail_perf lost its first six columns';
  end if;

  -- the funnel counts each device once per day, on one row, and each device's first event once
  select coalesce(sum(devices), 0), coalesce(sum(new_devices), 0) into n1, n2 from v_shop_funnel_daily;
  if n1 <> (select count(distinct ((ts at time zone 'Asia/Bahrain')::date, device_id))
            from shop_events where event <> 'error' and device_id is not null) then
    raise exception 'r7d_analytics_views: v_shop_funnel_daily counts a device twice on one day';
  end if;
  if n2 <> (select count(distinct device_id) from shop_events where event <> 'error' and device_id is not null) then
    raise exception 'r7d_analytics_views: v_shop_funnel_daily new_devices does not add up to the devices ever seen';
  end if;
  -- one row per merchant
  if (select count(*) from v_merchant_360) <> (select count(*) from shop_customers) then
    raise exception 'r7d_analytics_views: v_merchant_360 does not have one row per merchant';
  end if;
  -- the final searches never exceed the raw typed searches
  if (select coalesce(sum(searches), 0) from v_shop_search_daily)
     > (select count(*) from shop_events where event in ('search', 'search_zero')) then
    raise exception 'r7d_analytics_views: more final searches than search events';
  end if;
  raise notice 'r7d_analytics_views: ok (6 views, owned by postgres, yq_readonly only, nothing personal)';
end $$;
