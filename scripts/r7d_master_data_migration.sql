-- Customer + salesman master data — release R7d "Profitability + master data" (plan §24 r7_master_data,
-- §28 "Identity", 27-Sep-2026). Additive, idempotent, reviewed data migration.
--   Dry run:  the two SELECTs under "Dry run" below (read-only)
--   Rehearse: python -m scripts.apply_sql scripts/r7d_master_data_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7d_master_data_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7d_master_data_reverse.sql
--
-- What it does:
--   1. customers.segment — seeded from the Focus AR ageing "Group Name" (Retail / Key Account /
--      Cash Customer Group / MT), taken from each account's latest snapshot. The account is matched
--      to a customer by exact name (case and outer spaces ignored), else the customer_id an earlier
--      ageing load linked, else customer_aliases. Only a NULL segment is filled — a value someone set
--      is never overwritten.
--   2. customers.area — from the marketplace merchants linked to that customer
--      (shop_customers.focus_customer_id), where all of them name the same area. Only a NULL area
--      is filled.
--   3. salesmen.territory (text, nullable) and salesmen.focus_aliases (text[], nullable): room for
--      the rep's area and the other names Focus uses for him ("Husain Ali - Acc WH", …). Empty.
--   4. salesman_targets.salesman_id — the monthly target rows keyed by the rep's id as well as his
--      name: a nullable column + FK (on delete set null) + index on (salesman_id, period), backfilled
--      by name (salesmen.focus_name first, then salesmen.name), and a trigger that fills it the same
--      way on every insert / rename, so scripts/import_targets.py and the Salesmen page need no change.
--      The kickback math is untouched: ATTAINMENT_SQL and app/shop.py still read `salesman`.
--   Every value 1 and 2 write is logged in master_data_seed_log first; the reverse restores from it.
--
-- Preview (read-only, 27-Sep-2026): customers 291, area 0 / segment 0 filled today.
--   segment: 103 AR accounts in the latest group data, 101 matched (all by exact name), 2 unmatched
--            → 101 customers get a segment: Retail 89, Key Account 9, Cash Customer Group 2, MT 1.
--            No account has had two different groups; no customer matches two groups.
--   area:    0 — no marketplace merchant is linked to a Focus customer yet (shop_customers
--            .focus_customer_id is NULL on all 19). The statement runs and fills nothing; re-running
--            this file after merchants are linked fills their areas.
--   salesman_targets: 15 rows, all 15 resolve by focus_name (ids 3-17, 22). salesmen 18 rows.
--
-- Dry run (read-only):
--   with ar as (
--     select distinct on (lower(trim(a.account))) a.account, trim(a.group_name) as group_name, a.as_of_date
--     from ar_ageing a
--     where nullif(trim(a.group_name), '') is not null and nullif(trim(a.account), '') is not null
--     order by lower(trim(a.account)), a.as_of_date desc, a.id desc
--   ), matched as (
--     select a.account, a.group_name, a.as_of_date, coalesce(c.id, h.customer_id, ca.customer_id) as customer_id
--     from ar a
--     left join customers c on lower(trim(c.name)) = lower(trim(a.account))
--     left join lateral (select max(x.customer_id) as customer_id from ar_ageing x
--                        where lower(trim(x.account)) = lower(trim(a.account)) and x.customer_id is not null) h on true
--     left join lateral (select y.customer_id from customer_aliases y where lower(trim(y.alias)) = lower(trim(a.account))
--                        order by y.confidence desc nulls last, y.customer_id limit 1) ca on true
--   )
--   select m.group_name, count(distinct m.customer_id) as customers,
--          count(distinct m.customer_id) filter (where c.segment is null) as would_fill
--   from matched m join customers c on c.id = m.customer_id group by 1 order by 2 desc;
--
--   select t.salesman, t.period, s.id as salesman_id
--   from salesman_targets t
--   left join lateral (select s2.id from salesmen s2 where s2.focus_name = t.salesman or s2.name = t.salesman
--                      order by (s2.focus_name = t.salesman) desc, s2.id limit 1) s on true
--   order by 1, 2;
--
-- Nothing is deleted. No order, line, event or statement row is written; customers only gains the two
-- values above where they were empty.

do $$
begin
  if to_regclass('public.customers') is null or to_regclass('public.ar_ageing') is null
     or to_regclass('public.customer_aliases') is null or to_regclass('public.shop_customers') is null
     or to_regclass('public.salesmen') is null or to_regclass('public.salesman_targets') is null then
    raise exception 'r7d_master_data: customers / ar_ageing / customer_aliases / shop_customers / salesmen / salesman_targets missing';
  end if;
end $$;

-- ── 0. the seed log ────────────────────────────────────────────────────────────────────────────
create table if not exists master_data_seed_log (
  id          bigint      generated always as identity primary key,
  entity      text        not null,                 -- 'customers'
  entity_id   bigint      not null,
  field       text        not null,                 -- 'segment' | 'area'
  old_value   text,
  new_value   text        not null,
  source      text        not null,                 -- 'ar_ageing.group_name' | 'shop_customers.area'
  applied_at  timestamptz not null default now()
);
create index if not exists master_data_seed_log_entity_idx on master_data_seed_log (entity, entity_id, field);
alter table master_data_seed_log enable row level security;
revoke all on master_data_seed_log from anon, authenticated;
comment on table master_data_seed_log is
  'R7d: every master-data value the seed migration wrote (customers.segment from the AR group name, customers.area from linked merchants). The reverse clears a value only while it still equals new_value.';

-- ── 1. customers.segment from the AR "Group Name" ─────────────────────────────────────────────
with ar as (
  select distinct on (lower(trim(a.account))) a.account, trim(a.group_name) as group_name, a.as_of_date
  from ar_ageing a
  where nullif(trim(a.group_name), '') is not null and nullif(trim(a.account), '') is not null
  order by lower(trim(a.account)), a.as_of_date desc, a.id desc
),
matched as (
  select a.account, a.group_name, a.as_of_date, coalesce(c.id, h.customer_id, ca.customer_id) as customer_id
  from ar a
  left join customers c on lower(trim(c.name)) = lower(trim(a.account))
  left join lateral (select max(x.customer_id) as customer_id from ar_ageing x
                      where lower(trim(x.account)) = lower(trim(a.account)) and x.customer_id is not null) h on true
  left join lateral (select y.customer_id from customer_aliases y where lower(trim(y.alias)) = lower(trim(a.account))
                      order by y.confidence desc nulls last, y.customer_id limit 1) ca on true
),
pick as (
  select distinct on (m.customer_id) m.customer_id, m.group_name
  from matched m
  join customers c on c.id = m.customer_id
  where c.segment is null
  order by m.customer_id, m.as_of_date desc, m.account
),
logged as (
  insert into master_data_seed_log (entity, entity_id, field, old_value, new_value, source)
  select 'customers', customer_id, 'segment', null, group_name, 'ar_ageing.group_name' from pick
  returning entity_id, new_value
)
update customers c
   set segment = l.new_value
  from logged l
 where c.id = l.entity_id and c.segment is null;

-- ── 2. customers.area from the linked marketplace merchants (one area only) ───────────────────
with areas as (
  select sc.focus_customer_id as customer_id, min(trim(sc.area)) as area
  from shop_customers sc
  where sc.focus_customer_id is not null and nullif(trim(sc.area), '') is not null
  group by sc.focus_customer_id
  having count(distinct lower(trim(sc.area))) = 1
),
pick as (
  select a.customer_id, a.area
  from areas a join customers c on c.id = a.customer_id
  where c.area is null
),
logged as (
  insert into master_data_seed_log (entity, entity_id, field, old_value, new_value, source)
  select 'customers', customer_id, 'area', null, area, 'shop_customers.area' from pick
  returning entity_id, new_value
)
update customers c
   set area = l.new_value
  from logged l
 where c.id = l.entity_id and c.area is null;

-- ── 3. salesmen: territory and the other names Focus uses ─────────────────────────────────────
alter table salesmen add column if not exists territory     text;
alter table salesmen add column if not exists focus_aliases text[];
comment on column salesmen.territory is
  'R7d: the rep''s area / territory (free text, optional).';
comment on column salesmen.focus_aliases is
  'R7d: other names Focus books this rep under (a departed rep''s old warehouse name, a spelling variant). focus_name stays the primary match.';

-- ── 4. salesman_targets keyed by salesman_id (additive; the name stays the primary key) ───────
alter table salesman_targets add column if not exists salesman_id bigint;
alter table salesman_targets drop constraint if exists salesman_targets_salesman_id_fkey;
alter table salesman_targets add constraint salesman_targets_salesman_id_fkey
  foreign key (salesman_id) references salesmen(id) on delete set null;
create index if not exists salesman_targets_salesman_id_period_idx on salesman_targets (salesman_id, period);
comment on column salesman_targets.salesman_id is
  'R7d: the rep this target row belongs to (salesmen.id), resolved from `salesman` by focus_name, then name — kept filled by trigger salesman_targets_fill_salesman_id. The kickback math still keys on `salesman`.';

update salesman_targets t
   set salesman_id = s.id
  from (select t2.salesman, t2.period,
               (select s2.id from salesmen s2 where s2.focus_name = t2.salesman or s2.name = t2.salesman
                 order by (s2.focus_name = t2.salesman) desc, s2.id limit 1) as id
          from salesman_targets t2 where t2.salesman_id is null) s
 where t.salesman = s.salesman and t.period = s.period and t.salesman_id is null and s.id is not null;

create or replace function salesman_targets_fill_salesman_id() returns trigger
language plpgsql
set search_path = public
as $$
begin
  if (tg_op = 'INSERT' and new.salesman_id is null)
     or (tg_op = 'UPDATE' and new.salesman is distinct from old.salesman
         and new.salesman_id is not distinct from old.salesman_id) then
    new.salesman_id := (select s.id from salesmen s
                         where s.focus_name = new.salesman or s.name = new.salesman
                         order by (s.focus_name = new.salesman) desc, s.id limit 1);
  end if;
  return new;
end $$;
revoke all on function salesman_targets_fill_salesman_id() from public, anon, authenticated;

drop trigger if exists salesman_targets_fill_salesman_id on salesman_targets;
create trigger salesman_targets_fill_salesman_id
  before insert or update on salesman_targets
  for each row execute function salesman_targets_fill_salesman_id();

-- ── 5. checks ─────────────────────────────────────────────────────────────────────────────────
do $$
declare
  n_unkeyed int;
begin
  select count(*) into n_unkeyed
  from salesman_targets t
  where t.salesman_id is null
    and exists (select 1 from salesmen s where s.focus_name = t.salesman or s.name = t.salesman);
  if n_unkeyed > 0 then
    raise exception 'r7d_master_data: % target rows name a known rep but carry no salesman_id', n_unkeyed;
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name = 'master_data_seed_log'
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r7d_master_data: master_data_seed_log is granted to anon/authenticated';
  end if;
  raise notice 'r7d_master_data: ok (% segments, % areas seeded so far)',
    (select count(*) from master_data_seed_log where field = 'segment'),
    (select count(*) from master_data_seed_log where field = 'area');
end $$;
