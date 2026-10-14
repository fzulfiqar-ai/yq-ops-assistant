-- Reverse of scripts/r8n_ai_market_signals_migration.sql (Market Intel v2, release R8). Idempotent.
--   python -m scripts.apply_sql scripts/r8n_ai_market_signals_reverse.sql ; python -m scripts.audit_grants
--
-- Puts v_agent_market_signals back EXACTLY as scripts/r7b_ai_head_migration.sql (3l) defines it: restock
-- requests, product finds and field notes, twelve columns. CREATE OR REPLACE cannot drop the ten columns
-- r8n appended, so the view is dropped and created again, then given the grants r7b_ai_head gives it
-- (SELECT for ai_head_ro and yq_readonly; nothing for anon / authenticated). No CASCADE: nothing depends on
-- this view (the pack reads it by name); should a dependent view ever exist, the DROP refuses and names it,
-- which is the protection. No table or row is touched; ai_agent_mask and the ai_head_ro login stay (they
-- belong to r7b_ai_head).
--
-- Needs the old boards' tables (product_finds, field_notes), which the r7b body reads: the first block
-- refuses, and changes nothing, if one of them is gone.
--
-- Afterwards the weekly pack reads the old boards again and Market Intel sightings stop reaching
-- market_signals.csv. scripts/market_intel_ai_head.py (the photo readings) reads v_market_signals_agent and
-- is not affected.

do $$
declare
  t text;
begin
  foreach t in array array['product_finds', 'field_notes', 'shop_restock_requests'] loop
    if to_regclass('public.' || t) is null then
      raise exception 'r8n_ai_market_signals reverse: % is missing, and the r7b body of v_agent_market_signals reads it', t;
    end if;
  end loop;
  if to_regprocedure('public.ai_agent_mask(text)') is null
     or not exists (select 1 from pg_roles where rolname = 'ai_head_ro')
     or not exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    raise exception 'r8n_ai_market_signals reverse: ai_agent_mask(text), ai_head_ro or yq_readonly is missing (r7b_ai_head_migration.sql is not in place)';
  end if;
end $$;

drop view if exists v_agent_market_signals;

-- r7b_ai_head_migration.sql 3l, verbatim
create or replace view v_agent_market_signals as
select 'restock_request'::text                                          as signal,
       null::bigint                                                     as ref_id,
       (r.created_at at time zone 'Asia/Bahrain')::date                 as day,
       r.item_code,
       null::text                                                       as label,
       null::text                                                       as category,
       count(*)::int                                                    as n,
       count(distinct r.device_id)::int                                 as sources,
       sum(coalesce(r.qty_interest, 0))::int                            as qty,
       null::numeric(12,3)                                              as price,
       null::text                                                       as currency,
       null::text                                                       as status
from shop_restock_requests r
group by 3, 4
union all
select 'product_find', f.id, (f.posted_at at time zone 'Asia/Bahrain')::date, f.promoted_item_code,
       ai_agent_mask(left(f.name, 200)), f.category, 1, 1, null,
       f.price_bhd, f.currency, f.status
from product_finds f
union all
select 'field_note', n.id, (n.created_at at time zone 'Asia/Bahrain')::date, null,
       ai_agent_mask(left(n.note, 500)), n.category, 1, 1, null,
       null, null, null
from field_notes n;

comment on view v_agent_market_signals is
  'AI Head (R7b): market signals, each a REPORT until two shops or reps corroborate it (plan §18.9): restock_request (per day x item: requests, distinct devices, quantity of interest; never the phone), product_find (name, category, price as posted, status), field_note (the note, masked, first 500 chars). No poster / creator. ai_head_ro + yq_readonly.';

-- the grants r7b_ai_head gives this view (plain statements)
revoke all on table v_agent_market_signals from anon, authenticated;
grant select on v_agent_market_signals to ai_head_ro;
grant select on v_agent_market_signals to yq_readonly;

do $$
declare
  want text[] := array['signal', 'ref_id', 'day', 'item_code', 'label', 'category', 'n', 'sources', 'qty',
                       'price', 'currency', 'status'];
  got text[];
begin
  select array_agg(a.attname::text order by a.attnum) into got
  from pg_attribute a
  where a.attrelid = 'public.v_agent_market_signals'::regclass and a.attnum > 0 and not a.attisdropped;
  if got is distinct from want then
    raise exception 'r8n_ai_market_signals reverse: v_agent_market_signals has %, expected the r7b columns %', got, want;
  end if;
  if (select pg_get_userbyid(c.relowner) from pg_class c
       where c.oid = 'public.v_agent_market_signals'::regclass) <> 'postgres' then
    raise exception 'r8n_ai_market_signals reverse: v_agent_market_signals must be owned by postgres (apply as postgres)';
  end if;
  if exists (select 1 from pg_class c where c.oid = 'public.v_agent_market_signals'::regclass
             and coalesce(array_to_string(c.reloptions, ','), '') ilike '%security_invoker%') then
    raise exception 'r8n_ai_market_signals reverse: v_agent_market_signals must not be security_invoker';
  end if;
  if (select count(distinct c.relname) from pg_depend d join pg_rewrite r on r.oid = d.objid
        join pg_class c on c.oid = d.refobjid
       where r.ev_class = 'public.v_agent_market_signals'::regclass
         and c.relname in ('product_finds', 'field_notes')) <> 2
     or exists (select 1 from pg_depend d join pg_rewrite r on r.oid = d.objid
                  join pg_class c on c.oid = d.refobjid
                 where r.ev_class = 'public.v_agent_market_signals'::regclass
                   and c.relname in ('market_items', 'market_observations', 'market_photos')) then
    raise exception 'r8n_ai_market_signals reverse: v_agent_market_signals is not the r7b body (product_finds + field_notes)';
  end if;
  if not has_table_privilege('ai_head_ro', 'public.v_agent_market_signals', 'SELECT')
     or not has_table_privilege('yq_readonly', 'public.v_agent_market_signals', 'SELECT') then
    raise exception 'r8n_ai_market_signals reverse: ai_head_ro and yq_readonly must read v_agent_market_signals';
  end if;
  if has_table_privilege('ai_head_ro', 'public.v_agent_market_signals', 'INSERT')
     or has_table_privilege('ai_head_ro', 'public.v_agent_market_signals', 'UPDATE')
     or has_table_privilege('ai_head_ro', 'public.v_agent_market_signals', 'DELETE') then
    raise exception 'r8n_ai_market_signals reverse: ai_head_ro may write to v_agent_market_signals';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'v_agent_market_signals'
               and grantee in ('anon', 'authenticated')) then
    raise exception 'r8n_ai_market_signals reverse: v_agent_market_signals is granted to anon/authenticated';
  end if;
  raise notice 'r8n_ai_market_signals reverse: ok (v_agent_market_signals is the r7b view again: restock requests, product finds, field notes)';
end $$;
