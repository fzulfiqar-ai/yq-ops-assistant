-- Phase C.1 — unified customer dimension.
-- Idempotent; apply with:  python -m scripts.apply_migration scripts/customer_dimension_migration.sql
-- Then run:  python -m scripts.customer_alias_backfill   (seeds aliases + resolves FKs)
--
-- Today customers live in three disconnected name spaces: orders.customer_name (free text),
-- order_lines.customer_account (free text), ar_ageing.account (text) — orders.customer_id is
-- unpopulated. We add a resolver column + an alias table (the proven product_aliases pattern),
-- NOT hard FKs on ERP-loaded tables: ingest replaces those tables and new name variants appear,
-- so a constraint would break loads. area/segment support the geo/segmentation BI (Phase C.6).

alter table customers add column if not exists area    text;
alter table customers add column if not exists segment text;

create table if not exists customer_aliases (
  alias        text primary key,               -- a raw name as seen in ERP data (or its normalized form)
  customer_id  bigint not null references customers(id) on delete cascade,
  source       text default 'backfill',        -- 'identity' | 'normalized' | 'fuzzy' | 'manual'
  confidence   real default 1.0,
  created_at   timestamptz default now()
);
create index if not exists customer_aliases_cust_idx on customer_aliases(customer_id);

-- Nullable resolver columns (NO fk constraint — see header). Populated by the backfill + refresh.
alter table ar_ageing add column if not exists customer_id bigint;
-- orders.customer_id already exists in the base schema; ensure it's there for older DBs.
alter table orders    add column if not exists customer_id bigint;

-- Grant the read-only role SELECT on the new alias table (used by v_customer_ltv).
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on customer_aliases to yq_readonly;
  end if;
end $$;
