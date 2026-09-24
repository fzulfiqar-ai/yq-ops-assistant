-- Salesman kickback statements (24-Sep-2026, trust plan M8). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/kickback_statements_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/kickback_statements_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/kickback_statements_reverse.sql
--
-- Why: a rep's month used to live only as a live query, so any Focus reload, target edit or
-- basis change silently rewrote the month. A statement freezes a month (or a moment) on a stated
-- basis, so the money a rep was shown is on record and a change of rule is documented, not silent.
--
-- Owner decision 24-Sep-2026: kickback base = EX-VAT (taxable) Accessories sales, NET of Focus
-- Sales Returns (valued only from a Sales Return register, never estimated), whole month at the
-- reached tier. basis values:
--   'vat_incl_display'  what the Today card showed before the switch (revenue_bhd, VAT-inclusive)
--   'net_ex_vat'        the owner's basis (net_bhd = taxable)
-- status: 'snapshot' (a documented moment, never paid) | 'draft' | 'approved' | 'paid' | 'superseded'.

create table if not exists salesman_kickback_statements (
  id              bigint generated always as identity primary key,
  salesman        text        not null,          -- = salesman_targets.salesman (Focus "Warehouse Name")
  salesman_id     bigint      references salesmen(id) on delete restrict,
  period          text        not null,          -- 'YYYY-MM'
  basis           text        not null check (basis in ('vat_incl_display', 'net_ex_vat')),
  status          text        not null default 'draft'
                  check (status in ('snapshot', 'draft', 'approved', 'paid', 'superseded')),
  data_through    date,                          -- last Focus sale date the figures include
  sales_bhd       numeric(12,3) not null,        -- on `basis`, Accessories only, giveaways excluded
  returns_bhd     numeric(12,3),                 -- NULL = returns not valued (no Sales Return register)
  tier_reached    int         not null,
  rate            numeric(6,4) not null,         -- fraction applied to the whole month
  kickback_bhd    numeric(12,3) not null,
  target_snapshot jsonb       not null,          -- the salesman_targets row used, verbatim
  note            text,
  created_by      text        not null,
  created_at      timestamptz not null default now(),
  approved_by     text,
  approved_at     timestamptz,
  paid_at         timestamptz,
  -- replaced by two partial unique indexes in scripts/r3_statements_migration.sql (R3a): superseded
  -- rows of one data date collided here once superseding became routine. Kept for a fresh
  -- database so the file stays what production ran; R3a drops it by its definition.
  unique (salesman, period, basis, status, data_through)
);
alter table salesman_kickback_statements enable row level security;   -- service role only; no policies
create index if not exists salesman_kickback_statements_period_idx on salesman_kickback_statements (period, salesman);
revoke all on salesman_kickback_statements from anon, authenticated;

comment on table salesman_kickback_statements is
  'Frozen kickback figures per rep and month on a stated basis (24-Sep-2026). Never rewritten: a new basis or a corrected month is a new row; older rows move to superseded.';

do $$
begin
  if to_regclass('public.salesman_kickback_statements') is null then
    raise exception 'salesman_kickback_statements missing';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_name = 'salesman_kickback_statements' and grantee in ('anon', 'authenticated')) then
    raise exception 'salesman_kickback_statements must not be granted to anon/authenticated';
  end if;
end $$;
