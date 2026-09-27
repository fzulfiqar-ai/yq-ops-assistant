-- Market Intelligence v1, release R7b (plan §18: Product Finds + Field Notes merged into one capture).
-- Additive and idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7b_market_intel_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7b_market_intel_migration.sql ; python -m scripts.audit_grants
--   Copy the old boards in (dry run first): python -m scripts.market_intel_import  then  --apply
--   Reverse:  scripts/r7b_market_intel_reverse.sql
--
-- Why: the Finds board holds 259 photos from one July import (none triaged) and Field Notes 2 owner
-- tests; reps held neither grant, Field Notes sat behind "AI Assistant", and neither linked a shop,
-- a SKU, a competitor or a price. From R7b one capture ("Spotted" in the salesman app) writes:
--
--   market_items          one thing seen in the market (a product, a competitor price, a promotion,
--                         a shop's ask...). The office moves it New -> Researching -> Opportunity ->
--                         Approved (with a management action) | Rejected | Merged (a duplicate).
--   market_observations   one sighting: who, when, which shop / area, kind, note, price, demand,
--                         barcode text. client_uuid is the capture's idempotency key (a retry from the
--                         rep's phone never makes a second row). item_id stays NULL for a photo-only
--                         sighting until the office identifies it. source = rep | import | system.
--   market_photos         1-4 photos per sighting: the object path in the PRIVATE storage bucket (the
--                         API hands out short-lived signed URLs), width, height, bytes, a 64-bit dHash
--                         (Pillow, server-side, for near-duplicate photos) and has_people_flag (the
--                         office flags a photo with people; flagged photos never reach a report).
--   market_item_decisions append-only history of every status change / identify / merge: who, why.
--
--   v_market_clusters       one row per item from the sightings themselves: DISTINCT shops, reps and
--                           observations, price range, first / last seen, a cover photo. API only.
--   v_market_signals_agent  the same clusters plus the un-identified sightings with NO personal data
--                           (no rep, shop, note, email or photo path): granted to yq_readonly for the
--                           weekly AI Head.
--
-- Copy, not move: the old product_finds / field_notes tables are not touched here. The copy is a
-- separate script (scripts/market_intel_import.py, dry run by default) that inserts the 259 finds + 2
-- notes as source='import' observations; the old rows and their photos stay exactly where they are.
--
-- Service role only: RLS on every table with no policy, everything revoked from anon / authenticated
-- (tables, views and identity sequences). Nothing is granted to yq_readonly except the agent view.
--
-- The API tolerates all of this being absent (app/market_intel.py probes market_observations once and
-- caches the answer: a hit for 10 minutes, a miss for 1). Until it exists the board answers "not set up
-- yet" and a capture is saved to the legacy Product Finds / Field Notes tables instead (the import
-- script copies those in later), so the API and the SPA may deploy before this runs.

-- ── 1. items ─────────────────────────────────────────────────────────────────
create table if not exists market_items (
  id              bigint generated always as identity primary key,
  kind            text        not null default 'other'
                  check (kind in ('new_product', 'competitor_price', 'promotion', 'shop_asked', 'complaint', 'other')),
  title           text,                          -- what the reps call it ("Brand X 20W charger")
  norm_key        text,                          -- normalised title tokens: the "seen before" key (app/market_intel.norm_name)
  brand           text,
  competitor      text,                          -- whose price / promotion it is (competitor_price, promotion)
  category        text,                          -- the catalog's categories (CABLE, CHARGER, ...) or free text
  barcode         text,                          -- digits only; an exact barcode is the same item
  yq_item_code    text,                          -- the YQ catalog SKU when it is already ours; NULL = a gap
  status          text        not null default 'new'
                  check (status in ('new', 'researching', 'opportunity', 'approved', 'rejected', 'merged')),
  action          text
                  check (action in ('source', 'price_response', 'promo', 'ignore')),   -- set with Approved
  first_seen      timestamptz,
  last_seen       timestamptz,
  obs_count       integer     not null default 0,   -- cached from the sightings (v_market_clusters recomputes)
  shop_count      integer     not null default 0,   -- DISTINCT shops
  rep_count       integer     not null default 0,   -- DISTINCT reps
  price_min       numeric(12,3),
  price_max       numeric(12,3),
  ai_suggestion   jsonb,                         -- the weekly AI Head's reading of the photos: Unverified
  ai_confidence   numeric(4,3) check (ai_confidence is null or (ai_confidence >= 0 and ai_confidence <= 1)),
  verified        boolean     not null default false,   -- a person confirmed the AI suggestion
  merged_into     bigint      references market_items(id) on delete set null,
  status_reason   text,
  status_changed_at timestamptz,
  status_changed_by text,
  created_at      timestamptz not null default now(),
  created_by      text,
  updated_at      timestamptz not null default now(),
  updated_by      text,
  constraint market_items_merged_not_self check (merged_into is null or merged_into <> id),
  constraint market_items_approved_has_action check (status <> 'approved' or action is not null)
);
create index if not exists market_items_status_idx   on market_items (status, last_seen desc);
create index if not exists market_items_key_idx      on market_items (kind, norm_key);
create index if not exists market_items_barcode_idx  on market_items (barcode) where barcode is not null;
create index if not exists market_items_yq_code_idx  on market_items (yq_item_code) where yq_item_code is not null;
alter table market_items enable row level security;      -- service role only; no policies
revoke all on table market_items from anon, authenticated;
revoke all on sequence market_items_id_seq from anon, authenticated;

comment on table market_items is
  'Market Intelligence (R7b, plan §18): one thing seen in the market. Counts are cached from market_observations; v_market_clusters recomputes them (distinct shops and reps). Status: new -> researching -> opportunity -> approved (with action) | rejected | merged.';
comment on column market_items.ai_suggestion is
  'The weekly AI Head''s reading of the photos (brand, model, spec, visible text). Unverified until verified = true; never shown as fact.';

-- ── 2. observations (sightings) ──────────────────────────────────────────────
create table if not exists market_observations (
  id                bigint generated always as identity primary key,
  item_id           bigint      references market_items(id) on delete set null,   -- NULL until the office identifies a photo-only sighting
  client_uuid       uuid        not null,          -- the capture's idempotency key (a retry is the same row)
  source            text        not null default 'rep'
                    check (source in ('rep', 'import', 'system')),
  signal            text
                    check (signal in ('search_zero', 'restock_ask')),   -- system rows: which demand signal
  kind              text        not null default 'other'
                    check (kind in ('new_product', 'competitor_price', 'promotion', 'shop_asked', 'complaint', 'other')),
  salesman_id       bigint      references salesmen(id) on delete set null,
  created_by        text,                          -- the login that captured it (API only; never in the agent view)
  shop_customer_id  bigint      references shop_customers(id) on delete set null,
  shop_name         text,                          -- the shop as the rep named it (Focus customer name when known)
  area              text,
  title             text,
  brand             text,
  competitor        text,
  category          text,
  note              text,
  price_bhd         numeric(12,3) check (price_bhd is null or (price_bhd >= 0 and price_bhd < 100000)),
  demand_level      text        check (demand_level in ('low', 'medium', 'high')),
  demand_qty        integer     check (demand_qty is null or demand_qty > 0),
  barcode_raw       text,
  yq_item_code      text,                          -- the catalog SKU the typed code / name resolved to
  result            text        check (result in ('already_in_yq', 'seen_before', 'new_find', 'saved_for_review')),
  near_duplicate_of bigint      references market_observations(id) on delete set null,   -- a photo within Hamming 8
  legacy_ref        text,                          -- product_finds:<id> / field_notes:<id> for imported rows
  meta              jsonb,
  observed_at       timestamptz not null default now(),
  created_at        timestamptz not null default now(),
  constraint market_observations_client_uuid_key unique (client_uuid)
);
create index if not exists market_observations_item_idx    on market_observations (item_id, observed_at desc);
create index if not exists market_observations_by_idx      on market_observations (created_by, created_at desc);
create index if not exists market_observations_rep_idx     on market_observations (salesman_id) where salesman_id is not null;
create index if not exists market_observations_unassigned  on market_observations (created_at desc) where item_id is null;
create index if not exists market_observations_source_idx  on market_observations (source, signal);
alter table market_observations enable row level security;
revoke all on table market_observations from anon, authenticated;
revoke all on sequence market_observations_id_seq from anon, authenticated;

comment on table market_observations is
  'Market Intelligence (R7b): one sighting from a rep (source rep), a copied Product Find / Field Note (import) or a demand signal rolled up from zero-result searches and restock asks (system). client_uuid makes a retried capture idempotent.';

-- ── 3. photos ────────────────────────────────────────────────────────────────
create table if not exists market_photos (
  id               bigint generated always as identity primary key,
  observation_id   bigint      not null references market_observations(id) on delete cascade,
  bucket           text        not null default 'finds'
                   check (bucket in ('finds', 'field-notes')),   -- both PRIVATE; new captures go to finds/market/...
  path             text        not null,           -- object path; the API signs a short-lived URL at read time
  position         smallint    not null default 0,
  width            integer,
  height           integer,
  bytes            integer,
  phash            bigint,                         -- 64-bit dHash (two's complement), app/market_intel.dhash64
  has_people_flag  boolean     not null default false,
  created_at       timestamptz not null default now(),
  constraint market_photos_path_key unique (bucket, path)
);
create index if not exists market_photos_obs_idx   on market_photos (observation_id, position);
create index if not exists market_photos_phash_idx on market_photos (id desc) where phash is not null;
alter table market_photos enable row level security;
revoke all on table market_photos from anon, authenticated;
revoke all on sequence market_photos_id_seq from anon, authenticated;

comment on column market_photos.has_people_flag is
  'The office flagged people in this photo: it stays on the item for the office but never reaches a report or the agent view.';

-- ── 4. decision history (append-only) ────────────────────────────────────────
create table if not exists market_item_decisions (
  id           bigint generated always as identity primary key,
  item_id      bigint      not null references market_items(id),
  event        text        not null
               check (event in ('status', 'edit', 'identify', 'merge', 'approve')),
  from_status  text,
  to_status    text,
  action       text,
  reason       text,
  actor        text        not null,
  actor_role   text,
  detail       jsonb,
  created_at   timestamptz not null default now()
);
create index if not exists market_item_decisions_item_idx on market_item_decisions (item_id, created_at desc);
alter table market_item_decisions enable row level security;
revoke all on table market_item_decisions from anon, authenticated;
revoke all on sequence market_item_decisions_id_seq from anon, authenticated;

comment on table market_item_decisions is
  'Append-only (trigger): every Market Intel status change, identify, merge and approval with who and why.';

create or replace function market_item_decisions_no_rewrite() returns trigger
language plpgsql as $$
begin
  if tg_op = 'TRUNCATE' then
    raise exception 'market_item_decisions is append-only (it cannot be truncated)'
      using errcode = 'restrict_violation';
  end if;
  raise exception 'market_item_decisions is append-only (row % cannot be % )', old.id, tg_op
    using errcode = 'restrict_violation';
end $$;
revoke all on function market_item_decisions_no_rewrite() from public, anon, authenticated;

drop trigger if exists market_item_decisions_append_only on market_item_decisions;
create trigger market_item_decisions_append_only
  before update or delete on market_item_decisions
  for each row execute function market_item_decisions_no_rewrite();

drop trigger if exists market_item_decisions_no_truncate on market_item_decisions;
create trigger market_item_decisions_no_truncate
  before truncate on market_item_decisions
  for each statement execute function market_item_decisions_no_rewrite();

-- ── 5. clusters: counts from the sightings themselves (distinct shops and reps) ──
create or replace view v_market_clusters as
with obs as (
  select o.item_id,
         count(*)                                                                         as observations,
         count(distinct coalesce('c' || o.shop_customer_id::text,
                                 'n' || lower(nullif(btrim(o.shop_name), ''))))           as shops,
         count(distinct coalesce('s' || o.salesman_id::text,
                                 'e' || lower(nullif(btrim(o.created_by), ''))))
           filter (where o.source = 'rep')                                                as reps,
         min(o.price_bhd)                                                                 as price_min,
         max(o.price_bhd)                                                                 as price_max,
         min(o.observed_at)                                                               as first_seen,
         max(o.observed_at)                                                               as last_seen,
         count(*) filter (where o.source = 'system')                                      as system_signals,
         count(*) filter (where o.near_duplicate_of is not null)                          as near_duplicates,
         sum(o.demand_qty)                                                                as demand_qty
  from market_observations o
  where o.item_id is not null
  group by o.item_id
), ph as (
  select o.item_id,
         count(*)                                     as photos,
         count(*) filter (where p.has_people_flag)    as photos_people
  from market_photos p join market_observations o on o.id = p.observation_id
  where o.item_id is not null
  group by o.item_id
)
select i.id                                   as item_id,
       i.kind, i.title, i.brand, i.competitor, i.category, i.barcode, i.yq_item_code,
       i.status, i.action, i.merged_into,
       coalesce(obs.observations, 0)          as observations,
       coalesce(obs.shops, 0)                 as shops,
       coalesce(obs.reps, 0)                  as reps,
       obs.price_min, obs.price_max,
       coalesce(obs.first_seen, i.created_at) as first_seen,
       coalesce(obs.last_seen, i.created_at)  as last_seen,
       coalesce(obs.system_signals, 0)        as system_signals,
       coalesce(obs.near_duplicates, 0)       as near_duplicates,
       obs.demand_qty,
       coalesce(ph.photos, 0)                 as photos,
       coalesce(ph.photos_people, 0)          as photos_people,
       cover.bucket                           as cover_bucket,
       cover.path                             as cover_path,
       (i.ai_suggestion is not null)          as has_ai_suggestion,
       i.ai_confidence, i.verified,
       i.status_changed_at, i.created_at, i.updated_at,
       fn.note                                as first_note   -- the office's name for an item nobody named (never shown to reps)
from market_items i
left join obs on obs.item_id = i.id
left join ph  on ph.item_id = i.id
left join lateral (
  select p.bucket, p.path
  from market_photos p join market_observations o on o.id = p.observation_id
  where o.item_id = i.id and not p.has_people_flag
  order by o.observed_at desc, p.position, p.id
  limit 1
) cover on true
left join lateral (
  select o.note
  from market_observations o
  where o.item_id = i.id and nullif(btrim(o.note), '') is not null
  order by o.observed_at, o.id
  limit 1
) fn on true;
revoke all on v_market_clusters from anon, authenticated;

comment on view v_market_clusters is
  'Market Intel board (R7b): one row per item, counted from its sightings (distinct shops / reps, so duplicates never inflate evidence). API (service role) only: it names photo paths.';

-- ── 6. the AI Head's view: clusters + un-identified sightings, no personal data ──
create or replace view v_market_signals_agent as
select 'item'::text                    as row_type,
       c.item_id,
       null::bigint                    as observation_id,
       c.kind, c.title, c.brand, c.competitor, c.category, c.barcode, c.yq_item_code,
       c.status, c.action,
       c.observations, c.shops, c.reps,
       c.price_min, c.price_max, c.first_seen, c.last_seen,
       c.system_signals, c.demand_qty,
       (c.photos - c.photos_people)    as usable_photos,
       c.has_ai_suggestion, c.ai_confidence, c.verified
from v_market_clusters c
where c.status <> 'merged'
union all
select 'unassigned'::text,
       null::bigint,
       o.id,
       o.kind, o.title, o.brand, o.competitor, o.category, o.barcode_raw, o.yq_item_code,
       'new'::text, null::text,
       1::bigint,
       (case when o.shop_customer_id is not null or nullif(btrim(o.shop_name), '') is not null then 1 else 0 end)::bigint,
       (case when o.source = 'rep' then 1 else 0 end)::bigint,
       o.price_bhd, o.price_bhd, o.observed_at, o.observed_at,
       (case when o.source = 'system' then 1 else 0 end)::bigint,
       o.demand_qty::bigint,
       (select count(*) from market_photos p where p.observation_id = o.id and not p.has_people_flag),
       false, null::numeric, false
from market_observations o
where o.item_id is null;
revoke all on v_market_signals_agent from anon, authenticated;

comment on view v_market_signals_agent is
  'Market Intel for the weekly AI Head (R7b): clusters + un-identified sightings with counts, prices, kinds and dates only: no rep, shop, note, login or photo path. Granted to yq_readonly.';

-- ── 7. read-only role: the agent view only (plain statement: the role exists in production) ──
grant select on v_market_signals_agent to yq_readonly;

-- ── self-check ─────────────────────────────────────────────────────────────────
do $$
declare
  t text;
  c text;
  n int;
begin
  foreach t in array array['market_items', 'market_observations', 'market_photos', 'market_item_decisions'] loop
    if to_regclass('public.' || t) is null then
      raise exception 'r7b_market_intel: % missing', t;
    end if;
    if not exists (select 1 from pg_tables where schemaname = 'public' and tablename = t and rowsecurity) then
      raise exception 'r7b_market_intel: % must have RLS enabled', t;
    end if;
    if has_sequence_privilege('anon', 'public.' || t || '_id_seq', 'USAGE,SELECT,UPDATE')
       or has_sequence_privilege('authenticated', 'public.' || t || '_id_seq', 'USAGE,SELECT,UPDATE') then
      raise exception 'r7b_market_intel: %_id_seq must not be granted to anon/authenticated', t;
    end if;
    if exists (select 1 from pg_roles where rolname = 'yq_readonly')
       and has_table_privilege('yq_readonly', 'public.' || t, 'SELECT') then
      raise exception 'r7b_market_intel: % names reps and shops and must not be readable by yq_readonly', t;
    end if;
  end loop;
  foreach c in array array['item_id', 'client_uuid', 'source', 'signal', 'kind', 'salesman_id', 'created_by',
                           'shop_customer_id', 'shop_name', 'area', 'title', 'brand', 'competitor', 'category',
                           'note', 'price_bhd', 'demand_level', 'demand_qty', 'barcode_raw', 'yq_item_code',
                           'result', 'near_duplicate_of', 'legacy_ref', 'meta', 'observed_at', 'created_at'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'market_observations' and column_name = c) then
      raise exception 'r7b_market_intel: market_observations.% missing', c;
    end if;
  end loop;
  foreach c in array array['observation_id', 'bucket', 'path', 'width', 'height', 'bytes', 'phash', 'has_people_flag'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'market_photos' and column_name = c) then
      raise exception 'r7b_market_intel: market_photos.% missing', c;
    end if;
  end loop;
  if not exists (select 1 from pg_constraint
                 where conrelid = 'public.market_observations'::regclass and contype = 'u'
                   and pg_get_constraintdef(oid) like '%client_uuid%') then
    raise exception 'r7b_market_intel: market_observations.client_uuid must be unique (capture idempotency)';
  end if;
  select count(*) into n from pg_constraint
  where conrelid = 'public.market_items'::regclass and contype = 'c'
    and (pg_get_constraintdef(oid) like '%researching%' or pg_get_constraintdef(oid) like '%price_response%');
  if n < 2 then
    raise exception 'r7b_market_intel: market_items status / action CHECKs missing (%/2)', n;
  end if;
  if not exists (select 1 from pg_trigger where tgname = 'market_item_decisions_append_only'
                   and tgrelid = 'public.market_item_decisions'::regclass)
     or not exists (select 1 from pg_trigger where tgname = 'market_item_decisions_no_truncate'
                   and tgrelid = 'public.market_item_decisions'::regclass) then
    raise exception 'r7b_market_intel: the decision history must be append-only (triggers missing)';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public'
               and table_name in ('market_items', 'market_observations', 'market_photos', 'market_item_decisions',
                                  'v_market_clusters', 'v_market_signals_agent')
               and grantee in ('anon', 'authenticated')) then
    raise exception 'r7b_market_intel: nothing here may be granted to anon/authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    if has_table_privilege('yq_readonly', 'public.v_market_clusters', 'SELECT') then
      raise exception 'r7b_market_intel: v_market_clusters names photo paths and must not be readable by yq_readonly';
    end if;
    if not has_table_privilege('yq_readonly', 'public.v_market_signals_agent', 'SELECT') then
      raise exception 'r7b_market_intel: v_market_signals_agent must be readable by yq_readonly';
    end if;
  end if;
  select count(*) into n from information_schema.columns
  where table_schema = 'public' and table_name = 'v_market_signals_agent'
    and column_name in ('created_by', 'salesman_id', 'shop_name', 'shop_customer_id', 'note', 'path', 'cover_path', 'email');
  if n > 0 then
    raise exception 'r7b_market_intel: v_market_signals_agent carries personal data (% columns)', n;
  end if;
  raise notice 'r7b_market_intel: ok (4 tables with RLS, unique client_uuid, append-only decisions, 2 views, agent view for yq_readonly only)';
end $$;
