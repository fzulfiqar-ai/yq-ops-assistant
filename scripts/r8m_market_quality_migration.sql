-- Market Intelligence v2, release R8m: the quality track (plan Part F; build contract §2).
-- Additive and idempotent. Needs scripts/r7b_market_intel_migration.sql (the first block stops otherwise).
-- Run it FROM THE MAIN PROJECT FOLDER only: a worktree finds the project folder's .env, which is production.
--   Back up:  python -m scripts.db_backup --all --out business_data/backups/<date>_pre-r8m
--             python -m scripts.prod_gate snapshot --label pre-r8m
--   Rehearse: python -m scripts.apply_sql scripts/r8m_market_quality_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r8m_market_quality_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r8m_market_quality_reverse.sql (refuses while anything below holds data)
--   Local:    YQ_LOCAL_PG_R8M=postgresql://postgres@127.0.0.1:<port>/<empty db> python -m tests.test_r8m_market_quality_replay
--
-- Why: v1 (R7b) folds every complaint about a SKU into one open "complaint" item: no issue type,
-- quantity, batch, packaging version or "customer said / I saw it", no verification, no evidence
-- level, no safety flag, no supplier, and brands / categories hard-coded in two places. v2 keeps the
-- one capture and the one evidence store and adds a quality track next to the opportunity clusters
-- (market_items keeps demand, competitor and trend sightings):
--
--   suppliers              admin list: who makes or sells us the product. Deactivated or merged rather than
--                          deleted: a delete is refused while a case, sighting, action or report uses it.
--   brands                 admin list: brand -> supplier, aliases (a rename keeps the old name), merges.
--   market_issue_types     admin list: kind (quality / packaging / demand / competitor / other), a slug
--                          code, English + Arabic labels, a safety default and the extra capture fields
--                          it shows (qty, batch_ref, packaging_version, label_photo). Nothing is seeded:
--                          an admin adds them on the Lists page.
--   market_cases           one real-world problem (a product + an issue): the eight statuses, the
--                          evidence level 1-5, safety, severity, assignee, due date, how it was
--                          verified, cached counts, merges.
--   market_case_events     the case timeline, append-only (triggers refuse UPDATE, DELETE, TRUNCATE),
--                          like market_item_decisions.
--   supplier_actions       what YQ asked a supplier to do, the supplier's answer and YQ's check of the fix.
--   supplier_action_cases  which cases an action covers.
--   supplier_reports       the monthly supplier workbook: draft -> approved (an older approved one of
--                          the same supplier, month and edition -> superseded), the narrative with its
--                          [MI-<id>] references, the sightings included, counts, approver, the file's
--                          sha256 and every download.
--   v_market_cases_agent   every case not merged into another (any status) with codes, counts and dates only: no title, note,
--                          person, shop or photo path. Granted to yq_readonly for the weekly AI Head.
--
-- Existing tables only gain columns, each nullable or with a constant default (no row is rewritten):
--   market_observations  case_id, issue_type_id, report_basis, qty_affected, batch_ref, packaging_version,
--                        safety_flag, client_observed_at, brand_id, supplier_id, brand_text, supplier_text.
--                        The product picked at capture goes in R7b's own yq_item_code (already there).
--   market_photos        role, sha256, thumb_path, caption, blurred_path.
--   catalog_items        barcode (nullable, indexed where set).
--
-- The workflow lives in the API (app/market_quality.py), not here: the database checks the VALUES
-- (the eight statuses, evidence 1-5, severity 1-3, kinds, roles, editions) but not which status may
-- follow which, that evidence only moves up, or what Verified / Resolved need.
--
-- Service role only: RLS on every new table with no policy; everything revoked from anon /
-- authenticated (tables, identity sequences, the view, the trigger function). yq_readonly gets the agent
-- view only. v_market_clusters, v_market_signals_agent and R7b's one GRANT are not touched.
--
-- Order: after R7b; apply it before (or with) the API release that brings Market Intel v2. The v1
-- routes read none of these columns, so the API running today keeps working after the apply.

set lock_timeout = '2s';      -- a busy catalog_items / market_observations makes the file fail fast, never queue

do $$
begin
  if to_regclass('public.market_items') is null or to_regclass('public.market_observations') is null
     or to_regclass('public.market_photos') is null then
    raise exception 'r8m_market_quality: Market Intel v1 is missing: apply scripts/r7b_market_intel_migration.sql first';
  end if;
  if to_regclass('public.catalog_items') is null then
    raise exception 'r8m_market_quality: catalog_items is missing (scripts/catalog_migration.sql)';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    raise exception 'r8m_market_quality: the role yq_readonly is missing (scripts/views.sql creates it); this file grants it the agent view';
  end if;
  -- A first apply (no market_cases yet) adopts nothing: IF NOT EXISTS would silently keep a table or a
  -- column of the same name made by hand (the dashboard, an experiment), and the reverse would later drop it.
  if to_regclass('public.market_cases') is null then
    if to_regclass('public.suppliers') is not null or to_regclass('public.brands') is not null
       or to_regclass('public.market_issue_types') is not null or to_regclass('public.market_case_events') is not null
       or to_regclass('public.supplier_actions') is not null or to_regclass('public.supplier_action_cases') is not null
       or to_regclass('public.supplier_reports') is not null or to_regclass('public.v_market_cases_agent') is not null then
      raise exception 'r8m_market_quality: a table or view of this release exists although market_cases does not (made by hand?): look at it before applying';
    end if;
    if exists (select 1 from information_schema.columns where table_schema = 'public' and (
         (table_name = 'market_observations' and column_name in ('case_id', 'issue_type_id', 'report_basis', 'qty_affected', 'batch_ref', 'packaging_version', 'safety_flag', 'client_observed_at', 'brand_id', 'supplier_id', 'brand_text', 'supplier_text'))
         or (table_name = 'market_photos' and column_name in ('role', 'sha256', 'thumb_path', 'caption', 'blurred_path'))
         or (table_name = 'catalog_items' and column_name = 'barcode'))) then
      raise exception 'r8m_market_quality: a column of this release exists although market_cases does not (made by hand?): look at it before applying';
    end if;
  end if;
end $$;

-- ── 1. suppliers ─────────────────────────────────────────────────────────────
create table if not exists suppliers (
  id           bigint      generated always as identity primary key,
  name         text        not null,
  code         text,                              -- optional short code (the supplier's or Focus's)
  active       boolean     not null default true,
  merged_into  bigint      references suppliers(id) on delete set null,   -- a duplicate points at the one kept
  created_at   timestamptz not null default now(),
  created_by   text,
  updated_at   timestamptz not null default now(),
  updated_by   text,
  constraint suppliers_name_not_blank check (btrim(name) <> ''),
  constraint suppliers_merged_not_self check (merged_into is null or merged_into <> id)
);
create unique index if not exists suppliers_active_name_key on suppliers (lower(btrim(name))) where active;
create index if not exists suppliers_merged_idx on suppliers (merged_into) where merged_into is not null;
alter table suppliers enable row level security;      -- service role only; no policies
revoke all on table suppliers from anon, authenticated;
revoke all on sequence suppliers_id_seq from anon, authenticated;

comment on table suppliers is
  'Market Intel v2 (R8m): admin-managed supplier list. One active row per name (any case); a duplicate is merged (merged_into) and deactivated rather than deleted: a delete is refused while a case, sighting, action or report uses it, and a brand pointing at it loses that link.';

-- ── 2. brands ────────────────────────────────────────────────────────────────
create table if not exists brands (
  id            bigint      generated always as identity primary key,
  name          text        not null,
  supplier_id   bigint      references suppliers(id) on delete set null,
  aliases       text[]      default '{}',        -- other spellings and every earlier name (a rename adds the old one)
  renamed_from  text,                            -- the name before the last rename
  active        boolean     not null default true,
  merged_into   bigint      references brands(id) on delete set null,
  created_at    timestamptz not null default now(),
  created_by    text,
  updated_at    timestamptz not null default now(),
  updated_by    text,
  constraint brands_name_not_blank check (btrim(name) <> ''),
  constraint brands_merged_not_self check (merged_into is null or merged_into <> id)
);
create index if not exists brands_supplier_idx on brands (supplier_id) where supplier_id is not null;
create index if not exists brands_merged_idx   on brands (merged_into) where merged_into is not null;
create index if not exists brands_name_idx     on brands (lower(name));
alter table brands enable row level security;
revoke all on table brands from anon, authenticated;
revoke all on sequence brands_id_seq from anon, authenticated;

comment on table brands is
  'Market Intel v2 (R8m): admin-managed brand list with its supplier. A rename keeps the old name in aliases and renamed_from; a duplicate is merged (merged_into). Sightings keep the brand as linked AND as typed (market_observations.brand_text), so history reads the same after a rename.';

-- ── 3. issue types ───────────────────────────────────────────────────────────
create table if not exists market_issue_types (
  id              bigint      generated always as identity primary key,
  kind            text        not null
                  check (kind in ('quality', 'packaging', 'demand', 'competitor', 'other')),
  code            text        not null,           -- lowercase slug, stable: reports and tests key on it
  label_en        text        not null,
  label_ar        text,
  safety_default  boolean     not null default false,   -- choosing it flags the sighting as a safety risk
  fields          text[]      default '{}',        -- the extra capture fields this type shows
  active          boolean     not null default true,
  sort            integer     default 100,
  created_at      timestamptz not null default now(),
  created_by      text,
  updated_at      timestamptz not null default now(),
  updated_by      text,
  constraint market_issue_types_code_key unique (code),
  constraint market_issue_types_code_slug check (code ~ '^[a-z0-9][a-z0-9_-]{0,59}$'),
  constraint market_issue_types_label_not_blank check (btrim(label_en) <> ''),
  constraint market_issue_types_fields_known
    check (fields <@ array['qty', 'batch_ref', 'packaging_version', 'label_photo']::text[])
);
create index if not exists market_issue_types_kind_idx on market_issue_types (kind, sort);
alter table market_issue_types enable row level security;
revoke all on table market_issue_types from anon, authenticated;
revoke all on sequence market_issue_types_id_seq from anon, authenticated;

comment on table market_issue_types is
  'Market Intel v2 (R8m): admin-managed issue chips per kind (quality / packaging -> cases; demand / competitor / other -> v1 items). fields = extra capture fields (qty, batch_ref, packaging_version, label_photo). Nothing is seeded by the migration; deactivate, never delete, a type in use.';

-- ── 4. cases: one real-world problem per product and issue ───────────────────
create table if not exists market_cases (
  id                   bigint      generated always as identity primary key,
  yq_item_code         text,                     -- the catalog SKU; NULL = not identified / not ours (title = the typed text)
  brand_id             bigint      references brands(id),
  supplier_id          bigint      references suppliers(id),
  issue_type_id        bigint      not null references market_issue_types(id),
  batch_ref            text,
  packaging_version    text,
  status               text        not null default 'submitted'
                       check (status in ('submitted', 'under_review', 'more_info_required', 'verified',
                                         'not_substantiated', 'action_requested', 'resolved', 'closed')),
  evidence_level       smallint    not null default 1
                       check (evidence_level between 1 and 5),   -- 1 reported .. 5 resolution verified; the API only raises it
  safety               boolean     not null default false,
  severity             smallint    check (severity between 1 and 3),
  assignee             text,                     -- the reviewer's login
  due_date             date,
  verification_method  text        check (verification_method in ('sample_inspected', 'photos_sufficient', 'several_shops')),
  verification_reason  text,
  title                text        not null,
  first_seen           timestamptz,
  last_seen            timestamptz,
  obs_count            integer     not null default 0,   -- cached from the sightings (DISTINCT shops in shop_count)
  shop_count           integer     not null default 0,
  merged_into          bigint      references market_cases(id) on delete set null,
  status_reason        text,
  changed_at           timestamptz,
  changed_by           text,
  created_at           timestamptz not null default now(),
  created_by           text,
  updated_at           timestamptz not null default now(),
  updated_by           text,
  constraint market_cases_title_not_blank check (btrim(title) <> ''),
  constraint market_cases_merged_not_self check (merged_into is null or merged_into <> id),
  constraint market_cases_counts_not_negative check (obs_count >= 0 and shop_count >= 0)
);
create index if not exists market_cases_status_idx     on market_cases (status, last_seen desc);
create index if not exists market_cases_safety_idx     on market_cases (last_seen desc) where safety;
create index if not exists market_cases_item_issue_idx on market_cases (yq_item_code, issue_type_id, last_seen desc);
create index if not exists market_cases_issue_idx      on market_cases (issue_type_id, last_seen desc);
create index if not exists market_cases_supplier_idx   on market_cases (supplier_id, last_seen desc) where supplier_id is not null;
create index if not exists market_cases_brand_idx      on market_cases (brand_id) where brand_id is not null;
create index if not exists market_cases_last_seen_idx  on market_cases (last_seen desc);
create index if not exists market_cases_merged_idx     on market_cases (merged_into) where merged_into is not null;
alter table market_cases enable row level security;
revoke all on table market_cases from anon, authenticated;
revoke all on sequence market_cases_id_seq from anon, authenticated;

comment on table market_cases is
  'Market Intel v2 (R8m): one real-world quality / packaging problem (product + issue type). Status submitted -> under_review <-> more_info_required -> verified | not_substantiated -> action_requested -> resolved -> closed: the transitions, what each needs and "evidence only moves up" are enforced by the API (app/market_quality.py), the database checks the values only.';
comment on column market_cases.evidence_level is
  '1 reported, 2 evidence submitted, 3 inspected and verified by YQ, 4 supplier confirmed, 5 resolution verified. Only ever raised (API).';

-- ── 5. the case timeline (append-only) ───────────────────────────────────────
create table if not exists market_case_events (
  id           bigint      generated always as identity primary key,
  case_id      bigint      not null references market_cases(id),   -- no cascade: a case with a timeline is never deleted
  at           timestamptz not null default now(),
  actor        text        not null,
  event        text        not null
               check (event in ('created', 'status', 'evidence', 'question', 'answer', 'link', 'move', 'merge',
                                'edit', 'action', 'safety_alert')),
  from_status  text,
  to_status    text,
  detail       jsonb       default '{}'
);
create index if not exists market_case_events_case_idx on market_case_events (case_id, at desc);
alter table market_case_events enable row level security;
revoke all on table market_case_events from anon, authenticated;
revoke all on sequence market_case_events_id_seq from anon, authenticated;

comment on table market_case_events is
  'Append-only (trigger): every Market Intel case change, question, answer, link, move, merge, supplier action and safety alert with who and when.';

create or replace function market_case_events_no_rewrite() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'TRUNCATE' then
    raise exception 'market_case_events is append-only (it cannot be truncated)'
      using errcode = 'restrict_violation';
  end if;
  raise exception 'market_case_events is append-only (row % cannot be changed: %)', old.id, tg_op
    using errcode = 'restrict_violation';
end $$;
revoke all on function market_case_events_no_rewrite() from public, anon, authenticated;

-- one safety-alert CLAIM per case: two safety sightings (or a sighting and a reviewer) racing can never send two
-- e-mails; the API claims by inserting the 'queued' event and reads a unique violation as "already claimed". The
-- outcome events ('sent' / 'not_sent') are not limited.
create unique index if not exists market_case_events_one_alert_claim_idx on market_case_events (case_id)
  where event = 'safety_alert' and detail->>'email' = 'queued';

drop trigger if exists market_case_events_append_only on market_case_events;
create trigger market_case_events_append_only
  before update or delete on market_case_events
  for each row execute function market_case_events_no_rewrite();

drop trigger if exists market_case_events_no_truncate on market_case_events;
create trigger market_case_events_no_truncate
  before truncate on market_case_events
  for each statement execute function market_case_events_no_rewrite();

-- ── 6. supplier actions and the cases they cover ─────────────────────────────
create table if not exists supplier_actions (
  id                 bigint      generated always as identity primary key,
  supplier_id        bigint      references suppliers(id),
  request            text        not null,       -- what YQ asks the supplier to do
  owner              text,                       -- who at YQ follows it up (a login)
  due_date           date,
  status             text        not null default 'open'
                     check (status in ('open', 'responded', 'resolved', 'cancelled')),
  supplier_response  text,
  resolution_check   text,                       -- how YQ checked the fix (a case is Resolved only with one)
  created_at         timestamptz not null default now(),
  created_by         text,
  updated_at         timestamptz not null default now(),
  updated_by         text,
  constraint supplier_actions_request_not_blank check (btrim(request) <> '')
);
create index if not exists supplier_actions_supplier_idx on supplier_actions (supplier_id, status) where supplier_id is not null;
create index if not exists supplier_actions_status_idx   on supplier_actions (status, due_date);
alter table supplier_actions enable row level security;
revoke all on table supplier_actions from anon, authenticated;
revoke all on sequence supplier_actions_id_seq from anon, authenticated;

create table if not exists supplier_action_cases (
  action_id   bigint      not null references supplier_actions(id) on delete cascade,
  case_id     bigint      not null references market_cases(id) on delete cascade,
  created_at  timestamptz not null default now(),
  created_by  text,
  constraint supplier_action_cases_pkey primary key (action_id, case_id)
);
create index if not exists supplier_action_cases_case_idx on supplier_action_cases (case_id);
alter table supplier_action_cases enable row level security;
revoke all on table supplier_action_cases from anon, authenticated;

comment on table supplier_actions is
  'Market Intel v2 (R8m): a corrective action asked of a supplier (CA-<id> in the workbook): request, YQ owner, due date, the supplier''s response and YQ''s resolution check. supplier_action_cases links it to the cases it covers.';

-- ── 7. the monthly supplier reports ──────────────────────────────────────────
create table if not exists supplier_reports (
  id                        bigint      generated always as identity primary key,
  supplier_id               bigint      not null references suppliers(id),
  month                     date        not null,          -- the first day of the month reported
  edition                   text        not null default 'supplier'
                            check (edition in ('supplier', 'internal')),
  status                    text        not null default 'draft'
                            check (status in ('draft', 'approved', 'superseded')),
  narrative                 text,                          -- every finding cites [MI-<observation id>], every action [CA-<id>]
  included_observation_ids  bigint[]    default '{}',
  counts                    jsonb       default '{}',
  approved_by               text,
  approved_at               timestamptz,
  file_sha256               text,
  downloads                 jsonb       default '[]',      -- [{at, by, sha256}], one per download
  created_at                timestamptz not null default now(),
  created_by                text,
  updated_at                timestamptz not null default now(),
  updated_by                text,
  constraint supplier_reports_month_first_day check (extract(day from month) = 1)
);
create index if not exists supplier_reports_supplier_month_idx on supplier_reports (supplier_id, month desc, edition, status);
-- one approved report per supplier, month and edition: two approvals racing can never both stand (the API
-- supersedes the older one first, then approves; the loser of a race gets a unique violation)
create unique index if not exists supplier_reports_one_approved_idx on supplier_reports (supplier_id, month, edition)
  where status = 'approved';
alter table supplier_reports enable row level security;
revoke all on table supplier_reports from anon, authenticated;
revoke all on sequence supplier_reports_id_seq from anon, authenticated;

comment on table supplier_reports is
  'Market Intel v2 (R8m): the monthly supplier workbook (supplier or internal edition). draft -> approved; approving supersedes the earlier approved report of the same supplier, month and edition (API). Never e-mailed automatically; every download is logged with its sha256.';

-- ── 8. sightings: the quality fields ─────────────────────────────────────────
alter table market_observations
  add column if not exists case_id             bigint      references market_cases(id) on delete set null,
  add column if not exists issue_type_id       bigint      references market_issue_types(id),
  add column if not exists report_basis        text        check (report_basis in ('customer_said', 'rep_saw')),
  add column if not exists qty_affected        integer     check (qty_affected > 0),
  add column if not exists batch_ref           text,
  add column if not exists packaging_version   text,
  add column if not exists safety_flag         boolean     not null default false,
  add column if not exists client_observed_at  timestamptz,
  add column if not exists brand_id            bigint      references brands(id),
  add column if not exists supplier_id         bigint      references suppliers(id),
  add column if not exists brand_text          text,
  add column if not exists supplier_text       text;
create index if not exists market_observations_case_idx     on market_observations (case_id, observed_at desc) where case_id is not null;
create index if not exists market_observations_issue_idx    on market_observations (issue_type_id, observed_at desc) where issue_type_id is not null;
create index if not exists market_observations_supplier_idx on market_observations (supplier_id, observed_at desc) where supplier_id is not null;
create index if not exists market_observations_brand_idx    on market_observations (brand_id) where brand_id is not null;
create index if not exists market_observations_yq_code_idx  on market_observations (yq_item_code, observed_at desc) where yq_item_code is not null;
create index if not exists market_observations_safety_idx   on market_observations (observed_at desc) where safety_flag;

comment on column market_observations.client_observed_at is
  'When the phone took the photo (Market Intel v2). The API stores it only within the last 7 days and at most 10 minutes ahead, and then uses it for observed_at.';
comment on column market_observations.brand_text is
  'The brand as linked (or typed) at capture time, kept when the brand list is renamed or merged later. supplier_text likewise.';

-- ── 9. photos: role, fingerprint and derived files ───────────────────────────
alter table market_photos
  add column if not exists role          text        default 'product'
                                         check (role in ('product', 'packaging', 'label_batch', 'receipt', 'other')),
  add column if not exists sha256        text,       -- of the stored master, recorded at capture
  add column if not exists thumb_path    text,       -- the 320 px WebP next to the master (<stem>-t320.webp)
  add column if not exists caption       text,
  add column if not exists blurred_path  text;       -- the server-blurred copy of a people-flagged photo (<stem>-blur.webp)
create index if not exists market_photos_sha256_idx on market_photos (sha256) where sha256 is not null;

comment on column market_photos.blurred_path is
  'Market Intel v2: the server-blurred derivative of a photo flagged has_people_flag. While flagged, every response signs this path only (an admin may ask for the master). The master is never overwritten.';

-- ── 10. catalog: a barcode per SKU ───────────────────────────────────────────
alter table catalog_items add column if not exists barcode text;
create index if not exists catalog_items_barcode_idx on catalog_items (barcode) where barcode is not null;

-- ── 11. the AI Head's view of the cases: codes, counts and dates only ────────
create or replace view v_market_cases_agent as
select c.id                          as case_id,
       c.status, c.evidence_level, c.safety, c.severity,
       c.issue_type_id,
       t.kind                        as issue_kind,
       t.code                        as issue_code,
       t.label_en                    as issue_label,
       c.yq_item_code,
       c.brand_id,
       b.name                        as brand,
       c.supplier_id,
       s.name                        as supplier,
       c.verification_method,           -- (no batch / packaging text: free text a rep typed)
       c.first_seen, c.last_seen, c.obs_count, c.shop_count,
       (select count(*)
          from market_photos p join market_observations o on o.id = p.observation_id
         where o.case_id = c.id and not p.has_people_flag)                     as usable_photos,
       (select count(*)
          from supplier_action_cases ac join supplier_actions a on a.id = ac.action_id
         where ac.case_id = c.id and a.status in ('open', 'responded'))        as open_actions,
       c.due_date, c.created_at, c.changed_at
from market_cases c
join market_issue_types t on t.id = c.issue_type_id
left join brands b on b.id = c.brand_id
left join suppliers s on s.id = c.supplier_id
where c.merged_into is null;
revoke all on v_market_cases_agent from anon, authenticated;

comment on view v_market_cases_agent is
  'Market Intel v2 for the weekly AI Head (R8m): unmerged cases with status, evidence, safety, issue, SKU, brand, supplier, counts and dates: no title, note, batch or packaging text, person, shop or photo path. Granted to yq_readonly.';

-- ── 12. read-only role: the agent view only (plain statement: the role exists in production) ──
grant select on v_market_cases_agent to yq_readonly;

-- ── self-check ─────────────────────────────────────────────────────────────────
do $$
declare
  t text;
  c text;
  n int;
begin
  foreach t in array array['suppliers', 'brands', 'market_issue_types', 'market_cases', 'market_case_events',
                           'supplier_actions', 'supplier_action_cases', 'supplier_reports'] loop
    if to_regclass('public.' || t) is null then
      raise exception 'r8m_market_quality: % missing', t;
    end if;
    if not exists (select 1 from pg_tables where schemaname = 'public' and tablename = t and rowsecurity) then
      raise exception 'r8m_market_quality: % must have RLS enabled', t;
    end if;
    if exists (select 1 from pg_policies where schemaname = 'public' and tablename = t) then
      raise exception 'r8m_market_quality: % must have no RLS policy (service role only)', t;
    end if;
    if has_table_privilege('anon', 'public.' || t, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       or has_table_privilege('authenticated', 'public.' || t, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') then
      raise exception 'r8m_market_quality: % must not be granted to anon/authenticated', t;
    end if;
    if to_regclass('public.' || t || '_id_seq') is not null
       and (has_sequence_privilege('anon', 'public.' || t || '_id_seq', 'USAGE,SELECT,UPDATE')
            or has_sequence_privilege('authenticated', 'public.' || t || '_id_seq', 'USAGE,SELECT,UPDATE')) then
      raise exception 'r8m_market_quality: %_id_seq must not be granted to anon/authenticated', t;
    end if;
    if has_table_privilege('yq_readonly', 'public.' || t, 'SELECT') then
      raise exception 'r8m_market_quality: % must not be readable by yq_readonly (the agent view is)', t;
    end if;
    -- the API runs as service_role: it must be able to use what it writes (Supabase's default grant).
    -- One privilege per call: a comma list answers "any of them".
    if exists (select 1 from pg_roles where rolname = 'service_role')
       and not (has_table_privilege('service_role', 'public.' || t, 'SELECT')
                and has_table_privilege('service_role', 'public.' || t, 'INSERT')
                and has_table_privilege('service_role', 'public.' || t, 'UPDATE')) then
      raise exception 'r8m_market_quality: service_role (the API) cannot read and write % (check the default privileges)', t;
    end if;
  end loop;
  foreach c in array array['case_id', 'issue_type_id', 'report_basis', 'qty_affected', 'batch_ref', 'packaging_version',
                           'safety_flag', 'client_observed_at', 'brand_id', 'supplier_id', 'brand_text', 'supplier_text',
                           'yq_item_code'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'market_observations' and column_name = c) then
      raise exception 'r8m_market_quality: market_observations.% missing', c;
    end if;
  end loop;
  foreach c in array array['role', 'sha256', 'thumb_path', 'caption', 'blurred_path'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'market_photos' and column_name = c) then
      raise exception 'r8m_market_quality: market_photos.% missing', c;
    end if;
  end loop;
  if not exists (select 1 from information_schema.columns
                 where table_schema = 'public' and table_name = 'catalog_items' and column_name = 'barcode') then
    raise exception 'r8m_market_quality: catalog_items.barcode missing';
  end if;
  select count(*) into n from pg_constraint
  where conrelid = 'public.market_cases'::regclass and contype = 'c'
    and ((pg_get_constraintdef(oid) like '%more_info_required%' and pg_get_constraintdef(oid) like '%not_substantiated%'
          and pg_get_constraintdef(oid) like '%action_requested%')
         or pg_get_constraintdef(oid) like '%evidence_level%');
  if n < 2 then
    raise exception 'r8m_market_quality: market_cases status / evidence_level CHECKs missing (%/2)', n;
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.market_case_events'::regclass and contype = 'c'
                 and pg_get_constraintdef(oid) like '%safety_alert%') then
    raise exception 'r8m_market_quality: market_case_events event CHECK missing';
  end if;
  if not exists (select 1 from pg_trigger where tgname = 'market_case_events_append_only'
                   and tgrelid = 'public.market_case_events'::regclass)
     or not exists (select 1 from pg_trigger where tgname = 'market_case_events_no_truncate'
                   and tgrelid = 'public.market_case_events'::regclass) then
    raise exception 'r8m_market_quality: the case timeline must be append-only (triggers missing)';
  end if;
  if has_function_privilege('anon', 'public.market_case_events_no_rewrite()', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.market_case_events_no_rewrite()', 'EXECUTE') then
    raise exception 'r8m_market_quality: the timeline trigger function must not be executable by anon/authenticated';
  end if;
  if not exists (select 1 from pg_constraint
                 where conrelid = 'public.market_observations'::regclass and contype = 'f'
                   and confrelid = 'public.market_cases'::regclass and confdeltype = 'n') then
    raise exception 'r8m_market_quality: market_observations.case_id must reference market_cases ON DELETE SET NULL';
  end if;
  if not exists (select 1 from pg_indexes where schemaname = 'public' and tablename = 'suppliers'
                 and indexname = 'suppliers_active_name_key') then
    raise exception 'r8m_market_quality: one active supplier per name (suppliers_active_name_key) missing';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public'
               and table_name in ('suppliers', 'brands', 'market_issue_types', 'market_cases', 'market_case_events',
                                  'supplier_actions', 'supplier_action_cases', 'supplier_reports', 'v_market_cases_agent')
               and grantee in ('anon', 'authenticated', 'PUBLIC')) then
    raise exception 'r8m_market_quality: nothing here may be granted to anon/authenticated/PUBLIC';
  end if;
  -- the agent view: yq_readonly only, no personal column, never security_invoker
  if not has_table_privilege('yq_readonly', 'public.v_market_cases_agent', 'SELECT') then
    raise exception 'r8m_market_quality: v_market_cases_agent must be readable by yq_readonly';
  end if;
  if has_table_privilege('anon', 'public.v_market_cases_agent', 'SELECT')
     or has_table_privilege('authenticated', 'public.v_market_cases_agent', 'SELECT') then
    raise exception 'r8m_market_quality: v_market_cases_agent must not be readable by anon/authenticated';
  end if;
  select count(*) into n from information_schema.columns
  where table_schema = 'public' and table_name = 'v_market_cases_agent'
    and column_name in ('title', 'assignee', 'created_by', 'updated_by', 'changed_by', 'actor', 'status_reason',
                        'verification_reason', 'note', 'shop_name', 'shop_customer_id', 'salesman_id', 'path',
                        'thumb_path', 'blurred_path', 'caption', 'email', 'phone');
  if n > 0 then
    raise exception 'r8m_market_quality: v_market_cases_agent carries personal or free-text data (% columns)', n;
  end if;
  if exists (select 1 from pg_class where oid = 'public.v_market_cases_agent'::regclass
             and array_to_string(coalesce(reloptions, '{}'::text[]), ',') like '%security_invoker%') then
    raise exception 'r8m_market_quality: v_market_cases_agent must not be security_invoker (yq_readonly reads views, not tables)';
  end if;
  -- R7b untouched: its agent view is still yq_readonly's, its clusters view (photo paths) still is not
  if not has_table_privilege('yq_readonly', 'public.v_market_signals_agent', 'SELECT') then
    raise exception 'r8m_market_quality: R7b''s grant on v_market_signals_agent to yq_readonly is gone';
  end if;
  if has_table_privilege('yq_readonly', 'public.v_market_clusters', 'SELECT') then
    raise exception 'r8m_market_quality: v_market_clusters names photo paths and must not be readable by yq_readonly';
  end if;
  raise notice 'r8m_market_quality: ok (8 tables with RLS and no policy, append-only case timeline, 12 sighting + 5 photo columns, catalog barcode, agent view for yq_readonly only)';
end $$;
