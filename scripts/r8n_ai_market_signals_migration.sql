-- Market Intel v2, release R8 (plan Part F1 / F8): the weekly AI Head reads Market Intel. Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r8n_ai_market_signals_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r8n_ai_market_signals_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r8n_ai_market_signals_reverse.sql
--   Needs:    r7b_market_intel_migration.sql (market_items, market_observations, market_photos) and
--             r7b_ai_head_migration.sql (v_agent_market_signals, ai_agent_mask, the ai_head_ro login).
--             The first block refuses and names the file to apply when one is missing.
--   Order with r8m_market_quality_migration.sql: either. This file reads no r8m table or column (cases,
--             issue types, suppliers, brands, photo roles), and r8m neither reads nor redefines this view.
--
-- Why: the weekly review (.claude/commands/weekly-review.md) reads v_agent_market_signals through
-- scripts/ai_head/pack.py (market_signals.csv every week, repeated_field_requests at month end), and
-- r7b_ai_head built that view on the old Product Finds / Field Notes boards. Since Market Intel v1 every
-- capture ("Spotted") is written to market_observations instead, so not one sighting reached the AI Head.
--
-- What changes: the body of v_agent_market_signals and its comment, nothing else. CREATE OR REPLACE keeps
-- the owner and the grants (re-issued below all the same). No table, row, role or other view is touched.
--   The twelve r7b columns stay first with their names, types and order (the pack selects day, signal,
--   item_code, label, n and sources by name):
--     signal, ref_id, day, item_code, label, category, n, sources, qty, price, currency, status
--   One row per:
--     restock_request      Bahrain day x SKU: marketplace "tell me when back" asks, as in r7b (n requests,
--                          sources = distinct devices, qty of interest; never the phone)
--     market_item          Bahrain day x Market Intel item (a merged item left out): n field sightings (by
--                          reps, or imported from the old boards), sources = distinct shops, qty asked, the
--                          lowest price seen; status = the item's status
--     market_report        Bahrain day x YQ SKU x kind: sightings of one of OUR products that no market item
--                          clusters (Market Intel v2 files quality reports on cases, not on items)
--     market_unidentified  one sighting with neither an item nor a YQ product: a photo still to be
--                          identified (ref_id = its observation id)
--   The product_find / field_note rows are gone: scripts/market_intel_import.py copies those boards into
--   Market Intel (source = 'import'), and they are read from there.
--   Appended (CREATE OR REPLACE VIEW can only append):
--     kind             new_product, competitor_price, promotion, shop_asked, complaint or other
--     brand            the brand as typed, masked
--     reps             distinct reps behind the row (counted, never named)
--     photos           photos of the row's sightings, never one flagged with people
--     system_signals   marketplace demand rolled onto the item that day (zero-result searches, "tell {rep}"
--                      requests, restock asks): the SAME demand as the restock_request rows and
--                      v_agent_search_demand, so the two are never added together
--     item_shops       the item's distinct shops and reps over all its field sightings: two of either
--     item_reps        corroborate a report (plan §18.9)
--     price_max        the highest price seen that day
--     ai_reading       'unverified' or 'verified' once the AI Head's reading of the photos is stored (an
--                      item's ai_suggestion, a sighting's meta.ai_suggestion); NULL = none yet
--     observation_ids  the field sightings behind the row, ascending: a finding cites them as [MI-<id>]
--
-- Privacy (counts and aggregates only): no shop or rep name, shop or rep id, login, note, area, competitor,
-- barcode, meta, photo path or device id is a column. A shop and a rep are COUNTED (distinct) through keys
-- the view never shows. The typed title and brand pass ai_agent_mask() (emails -> '[email]', runs of 8+
-- digits -> '[number]'). The closing block refuses to finish if any of this is off.
--
-- Re-running r7b_ai_head_migration.sql after this file FAILS LOUDLY at v_agent_market_signals ("cannot
-- drop columns from view"): that is the protection, because it would put the old boards back. To return
-- to r7b, run the reverse. Never force it with CASCADE.

-- ── 0. dependencies ──────────────────────────────────────────────────────────────
do $$
declare
  need text[][] := array[
    array['market_items', 'r7b_market_intel_migration.sql'],
    array['market_observations', 'r7b_market_intel_migration.sql'],
    array['market_photos', 'r7b_market_intel_migration.sql'],
    array['shop_restock_requests', 'marketplace_campaigns_migration.sql'],
    array['v_agent_market_signals', 'r7b_ai_head_migration.sql']];
  i int;
begin
  for i in 1 .. array_length(need, 1) loop
    if to_regclass('public.' || need[i][1]) is null then
      raise exception 'r8n_ai_market_signals: % is missing: apply scripts/% first', need[i][1], need[i][2];
    end if;
  end loop;
  if to_regprocedure('public.ai_agent_mask(text)') is null then
    raise exception 'r8n_ai_market_signals: ai_agent_mask(text) is missing: apply scripts/r7b_ai_head_migration.sql first';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'ai_head_ro') then
    raise exception 'r8n_ai_market_signals: role ai_head_ro is missing: apply scripts/r7b_ai_head_migration.sql first';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    raise exception 'r8n_ai_market_signals: role yq_readonly is missing: apply scripts/security_migration.sql first';
  end if;
end $$;

-- ── 1. the view ──────────────────────────────────────────────────────────────────
create or replace view v_agent_market_signals as
with ph as (           -- photos a report may count: never one flagged with people
  select p.observation_id, count(*) filter (where not p.has_people_flag) as usable
  from market_photos p
  group by p.observation_id
),
obs as (               -- every sighting with its Bahrain day; a merged item's left out (a merge moves them on)
  select o.id, o.item_id, o.source, o.kind, o.title, o.brand, o.category, o.price_bhd, o.demand_qty, o.yq_item_code,
         (o.observed_at at time zone 'Asia/Bahrain')::date                                         as day,
         -- a shop and a rep are COUNTED through these two keys and never shown (v_market_clusters' rule)
         coalesce('c' || o.shop_customer_id::text, 'n' || lower(nullif(btrim(o.shop_name), '')))   as shop_key,
         case when o.source = 'rep'
              then coalesce('s' || o.salesman_id::text, 'e' || lower(nullif(btrim(o.created_by), ''))) end as rep_key,
         coalesce(jsonb_typeof(o.meta -> 'ai_suggestion') = 'object', false)                       as has_ai,
         coalesce(o.meta -> 'ai_suggestion' ->> 'verified', '') = 'true'                          as ai_verified,
         coalesce(ph.usable, 0)                                                                   as usable
  from market_observations o
  left join market_items i on i.id = o.item_id
  left join ph on ph.observation_id = o.id
  where o.item_id is null or i.status <> 'merged'
),
tot as (               -- corroboration: an item's distinct shops and reps over all its field sightings
  select b.item_id, count(distinct b.shop_key) as shops, count(distinct b.rep_key) as reps
  from obs b
  where b.item_id is not null and b.source <> 'system'
  group by b.item_id
)
select 'restock_request'::text                                          as signal,
       null::bigint                                                     as ref_id,
       (r.created_at at time zone 'Asia/Bahrain')::date                 as day,
       r.item_code,
       null::text                                                       as label,
       null::text                                                       as category,
       count(*)::int                                                    as n,
       count(distinct r.device_id)::int                                 as sources,
       sum(coalesce(r.qty_interest, 0))::int                            as qty,
       null::numeric                                                    as price,
       null::text                                                       as currency,
       null::text                                                       as status,
       null::text                                                       as kind,
       null::text                                                       as brand,
       null::int                                                        as reps,
       null::int                                                        as photos,
       null::int                                                        as system_signals,
       null::int                                                        as item_shops,
       null::int                                                        as item_reps,
       null::numeric                                                    as price_max,
       null::text                                                       as ai_reading,
       null::bigint[]                                                   as observation_ids
from shop_restock_requests r
group by 3, 4
union all
select 'market_item', i.id, b.day, i.yq_item_code,
       ai_agent_mask(left(i.title, 200)), i.category,
       (count(*) filter (where b.source <> 'system'))::int,
       (count(distinct b.shop_key) filter (where b.source <> 'system'))::int,
       coalesce(sum(b.demand_qty) filter (where b.source <> 'system'), 0)::int,
       min(b.price_bhd) filter (where b.source <> 'system'),
       case when count(b.price_bhd) filter (where b.source <> 'system') > 0 then 'BHD' end,
       i.status,
       i.kind,
       ai_agent_mask(left(i.brand, 80)),
       (count(distinct b.rep_key))::int,
       coalesce(sum(b.usable) filter (where b.source <> 'system'), 0)::int,
       (count(*) filter (where b.source = 'system'))::int,
       coalesce(t.shops, 0)::int,
       coalesce(t.reps, 0)::int,
       max(b.price_bhd) filter (where b.source <> 'system'),
       case when i.ai_suggestion is null then null when i.verified then 'verified' else 'unverified' end,
       coalesce(array_agg(b.id order by b.id) filter (where b.source <> 'system'), '{}')
from obs b
join market_items i on i.id = b.item_id
left join tot t on t.item_id = b.item_id
group by i.id, b.day, t.shops, t.reps
union all
select 'market_report', null::bigint, b.day, b.yq_item_code,
       null::text, null::text,
       count(*)::int,
       count(distinct b.shop_key)::int,
       coalesce(sum(b.demand_qty), 0)::int,
       min(b.price_bhd),
       case when count(b.price_bhd) > 0 then 'BHD' end,
       null::text,
       b.kind,
       null::text,
       count(distinct b.rep_key)::int,
       sum(b.usable)::int,
       0,
       null::int,
       null::int,
       max(b.price_bhd),
       null::text,
       array_agg(b.id order by b.id)
from obs b
where b.item_id is null and b.yq_item_code is not null and b.source <> 'system'
group by b.day, b.yq_item_code, b.kind
union all
select 'market_unidentified', b.id, b.day, null::text,
       ai_agent_mask(left(b.title, 200)), b.category,
       1,
       (b.shop_key is not null)::int,
       coalesce(b.demand_qty, 0),
       b.price_bhd::numeric,
       case when b.price_bhd is not null then 'BHD' end,
       null::text,
       b.kind,
       ai_agent_mask(left(b.brand, 80)),
       (b.rep_key is not null)::int,
       b.usable::int,
       0,
       null::int,
       null::int,
       b.price_bhd::numeric,
       case when b.has_ai then case when b.ai_verified then 'verified' else 'unverified' end end,
       array[b.id]
from obs b
where b.item_id is null and b.yq_item_code is null and b.source <> 'system';

comment on view v_agent_market_signals is
  'AI Head (R7b; rebuilt on Market Intel in R8 by r8n_ai_market_signals_migration.sql): market signals, each a REPORT until two shops or reps corroborate it (plan §18.9) or a person verifies it. restock_request (per Bahrain day x SKU: requests, distinct devices, quantity of interest; never the phone); market_item (per day x Market Intel item: field sightings, distinct shops and reps, price range, usable photos, the item''s status, its whole-life distinct shops / reps, marketplace demand apart in system_signals); market_report (per day x YQ SKU x kind: sightings of our products that no item clusters); market_unidentified (one sighting to identify). observation_ids = the sightings behind the row, cited as [MI-<id>]. Counts and aggregates only: no shop, rep, login, note, area, competitor, barcode or photo path; title and brand masked. ai_head_ro + yq_readonly.';

-- ── 2. grants: exactly those r7b_ai_head gives this view (plain statements) ───────
revoke all on table v_agent_market_signals from anon, authenticated;
grant select on v_agent_market_signals to ai_head_ro;
grant select on v_agent_market_signals to yq_readonly;

-- ── 3. self-check: fails the whole transaction if anything is off ─────────────────
do $$
declare
  want text[] := array['signal text', 'ref_id bigint', 'day date', 'item_code text', 'label text',
                       'category text', 'n integer', 'sources integer', 'qty integer', 'price numeric',
                       'currency text', 'status text', 'kind text', 'brand text', 'reps integer',
                       'photos integer', 'system_signals integer', 'item_shops integer', 'item_reps integer',
                       'price_max numeric', 'ai_reading text', 'observation_ids bigint[]'];
  got text[];
  t text;
  n bigint;
  m bigint;
begin
  select array_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod) order by a.attnum) into got
  from pg_attribute a
  where a.attrelid = 'public.v_agent_market_signals'::regclass and a.attnum > 0 and not a.attisdropped;
  if got is distinct from want then
    raise exception 'r8n_ai_market_signals: v_agent_market_signals has %, expected %', got, want;
  end if;
  if (select pg_get_userbyid(c.relowner) from pg_class c
       where c.oid = 'public.v_agent_market_signals'::regclass) <> 'postgres' then
    raise exception 'r8n_ai_market_signals: v_agent_market_signals must be owned by postgres (apply as postgres)';
  end if;
  if exists (select 1 from pg_class c where c.oid = 'public.v_agent_market_signals'::regclass
             and coalesce(array_to_string(c.reloptions, ','), '') ilike '%security_invoker%') then
    raise exception 'r8n_ai_market_signals: v_agent_market_signals must not be security_invoker';
  end if;
  -- it reads the Market Intel tables, never the old boards
  foreach t in array array['market_items', 'market_observations', 'market_photos'] loop
    if not exists (select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
                   where r.ev_class = 'public.v_agent_market_signals'::regclass
                     and d.refobjid = ('public.' || t)::regclass) then
      raise exception 'r8n_ai_market_signals: v_agent_market_signals does not read %', t;
    end if;
  end loop;
  if exists (select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
             join pg_class c on c.oid = d.refobjid
             where r.ev_class = 'public.v_agent_market_signals'::regclass
               and c.relname in ('product_finds', 'field_notes')) then
    raise exception 'r8n_ai_market_signals: v_agent_market_signals still reads product_finds / field_notes';
  end if;
  -- the grants r7b_ai_head gives it, and nothing more
  if not has_table_privilege('ai_head_ro', 'public.v_agent_market_signals', 'SELECT')
     or not has_table_privilege('yq_readonly', 'public.v_agent_market_signals', 'SELECT') then
    raise exception 'r8n_ai_market_signals: ai_head_ro and yq_readonly must read v_agent_market_signals';
  end if;
  if has_table_privilege('ai_head_ro', 'public.v_agent_market_signals', 'INSERT')
     or has_table_privilege('ai_head_ro', 'public.v_agent_market_signals', 'UPDATE')
     or has_table_privilege('ai_head_ro', 'public.v_agent_market_signals', 'DELETE') then
    raise exception 'r8n_ai_market_signals: ai_head_ro may write to v_agent_market_signals';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'v_agent_market_signals'
               and grantee in ('anon', 'authenticated')) then
    raise exception 'r8n_ai_market_signals: v_agent_market_signals is granted to anon/authenticated';
  end if;
  -- no column names a person, a contact or a raw identifier (r7b_ai_head's list plus Market Intel's own)
  select count(*) into n from information_schema.columns
  where table_schema = 'public' and table_name = 'v_agent_market_signals'
    and (column_name ilike '%phone%' or column_name ilike '%email%' or column_name ilike '%whatsapp%'
         or column_name ilike '%shop_name%' or column_name ilike '%salesman%' or column_name ilike '%customer%'
         or column_name in ('token', 'ip_hash', 'ua', 'device_id', 'session_id', 'note', 'created_by',
                            'posted_by', 'user_email', 'address', 'contact_name', 'shop_customer_id', 'area',
                            'competitor', 'barcode', 'barcode_raw', 'meta', 'path', 'bucket', 'legacy_ref',
                            'client_uuid', 'rep', 'shop'));
  if n > 0 then
    raise exception 'r8n_ai_market_signals: % column(s) of v_agent_market_signals carry a person, a contact or a raw identifier', n;
  end if;
  -- every field sighting reaches the AI Head (a merged item's aside): the market rows add up to them
  select coalesce(sum(v.n), 0) into n from public.v_agent_market_signals v where v.signal like 'market\_%';
  select count(*) into m
  from public.market_observations o left join public.market_items i on i.id = o.item_id
  where o.source <> 'system' and (o.item_id is null or i.status <> 'merged');
  if n <> m then
    raise exception 'r8n_ai_market_signals: the market rows count % sighting(s), market_observations holds %', n, m;
  end if;
  -- the typed text is masked: no run of 8+ digits survives in a label or a brand
  if exists (select 1 from public.v_agent_market_signals
             where coalesce(label, '') || ' ' || coalesce(brand, '') ~ '\d(?: ?\d){7,}') then
    raise exception 'r8n_ai_market_signals: a label or a brand carries an unmasked number';
  end if;
  raise notice 'r8n_ai_market_signals: ok (v_agent_market_signals reads Market Intel: % field sighting(s); no personal column; ai_head_ro + yq_readonly only)', m;
end $$;
