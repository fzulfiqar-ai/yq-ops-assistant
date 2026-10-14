-- Reverse of scripts/r8m_market_quality_migration.sql (Market Intel v2, release R8m). Idempotent.
-- Run it FROM THE MAIN PROJECT FOLDER only (a worktree finds the project folder's .env = production):
--   python -m scripts.apply_sql scripts/r8m_market_quality_reverse.sql ; python -m scripts.audit_grants
--
-- Drops what R8m added and nothing else: the agent view, the eight tables (suppliers, brands, issue
-- types, cases, the case timeline, supplier actions and their case links, supplier reports), the
-- timeline's trigger function, the twelve sighting columns, the five photo columns, the catalog barcode
-- and R8m's indexes. R7b's tables, views, rows and its one GRANT stay exactly as they are
-- (market_observations.yq_item_code is R7b's and stays). Storage is not touched: thumbnails and blurred
-- copies stay in the private bucket, unreferenced.
--
-- The lists, cases, timeline, actions and reports are the quality history and the evidence shown to
-- suppliers. While ANY of them holds a row, or any sighting / photo / catalog row holds an R8m value (a
-- case or issue link, a basis, a quantity, a batch, a packaging version, a phone capture time, a brand
-- or supplier link or snapshot, a safety flag that is true, a photo role other than 'product', a sha256,
-- a thumbnail, a caption, a blurred copy, a barcode), this file REFUSES, so a routine reverse can never
-- lose them. The check and every drop are ONE statement (a DO block): whatever the client, a refusal drops
-- nothing and a failure half-way (a lock that does not come within 2 s) drops nothing either.
-- To drop them anyway, back them up first and say so in the same session (one psql session
-- or the Supabase SQL editor: apply_sql opens a session of its own, so the switch cannot reach it):
--   python -m scripts.db_backup --tables suppliers,brands,market_issue_types,market_cases,market_case_events,supplier_actions,supplier_action_cases,supplier_reports,market_observations,market_photos,catalog_items
--   set yq.r8m_drop = 'yes';   -- then run this file in that same session
-- When R8m holds nothing (R7b's own sightings and photos do not count) it runs without the switch.
--
-- Order: reverse R8m BEFORE r7b_market_intel_reverse.sql. v_market_cases_agent reads R7b's sightings
-- and photos, so the R7b reverse stops (without CASCADE) until this one ran. Here, no CASCADE either:
-- the view goes first, then the sighting columns that point at the lists and cases, then each table
-- after everything that references it. If an R7b table is already gone, its columns are skipped.
-- A view created later with SELECT * over market_observations / market_photos / catalog_items would pin
-- the new columns: the DROP COLUMN then fails loudly. Drop or recreate that view first, never CASCADE.
--
-- RESTART THE API AFTER REVERSING if it probes these tables (a probe may cache its answer).

set lock_timeout = '2s';      -- a busy catalog_items / market_observations makes the file fail fast, never queue

do $$
declare
  n bigint := 0;
  m bigint;
  t text;
  c text;
begin
  foreach t in array array['suppliers', 'brands', 'market_issue_types', 'market_cases', 'market_case_events',
                           'supplier_actions', 'supplier_action_cases', 'supplier_reports'] loop
    if to_regclass('public.' || t) is not null then          -- dynamic: a table may be gone already
      execute format('select count(*) from public.%I', t) into m;
      n := n + m;
    end if;
  end loop;
  foreach c in array array['case_id', 'issue_type_id', 'report_basis', 'qty_affected', 'batch_ref', 'packaging_version',
                           'client_observed_at', 'brand_id', 'supplier_id', 'brand_text', 'supplier_text'] loop
    if exists (select 1 from information_schema.columns where table_schema = 'public'
               and table_name = 'market_observations' and column_name = c) then
      execute format('select count(*) from public.market_observations where %I is not null', c) into m;
      n := n + m;
    end if;
  end loop;
  if exists (select 1 from information_schema.columns where table_schema = 'public'
             and table_name = 'market_observations' and column_name = 'safety_flag') then
    execute 'select count(*) from public.market_observations where safety_flag' into m;   -- the default (false) is not data
    n := n + m;
  end if;
  foreach c in array array['sha256', 'thumb_path', 'caption', 'blurred_path'] loop
    if exists (select 1 from information_schema.columns where table_schema = 'public'
               and table_name = 'market_photos' and column_name = c) then
      execute format('select count(*) from public.market_photos where %I is not null', c) into m;
      n := n + m;
    end if;
  end loop;
  if exists (select 1 from information_schema.columns where table_schema = 'public'
             and table_name = 'market_photos' and column_name = 'role') then
    execute 'select count(*) from public.market_photos where role <> ''product''' into m;    -- the default is not data
    n := n + m;
  end if;
  if exists (select 1 from information_schema.columns where table_schema = 'public'
             and table_name = 'catalog_items' and column_name = 'barcode') then
    execute 'select count(*) from public.catalog_items where barcode is not null' into m;
    n := n + m;
  end if;
  if n > 0 and coalesce(current_setting('yq.r8m_drop', true), '') <> 'yes' then
    raise exception 'r8m_market_quality reverse: % R8m row(s) / value(s) would be lost (suppliers, brands, issue types, cases and their timeline, supplier actions and reports, or the quality fields on sightings, photos and the catalog barcode). Back them up (python -m scripts.db_backup --tables suppliers,brands,market_issue_types,market_cases,market_case_events,supplier_actions,supplier_action_cases,supplier_reports,market_observations,market_photos,catalog_items) and run SET yq.r8m_drop = ''yes'' in this session to drop them.', n;
  end if;

drop view if exists v_market_cases_agent;

drop index if exists market_observations_safety_idx;
drop index if exists market_observations_yq_code_idx;      -- R8m's index on R7b's column (the column stays)
drop index if exists market_observations_brand_idx;
drop index if exists market_observations_supplier_idx;
drop index if exists market_observations_issue_idx;
drop index if exists market_observations_case_idx;
alter table if exists market_observations
  drop column if exists supplier_text,
  drop column if exists brand_text,
  drop column if exists supplier_id,
  drop column if exists brand_id,
  drop column if exists client_observed_at,
  drop column if exists safety_flag,
  drop column if exists packaging_version,
  drop column if exists batch_ref,
  drop column if exists qty_affected,
  drop column if exists report_basis,
  drop column if exists issue_type_id,
  drop column if exists case_id;

drop index if exists market_photos_sha256_idx;
alter table if exists market_photos
  drop column if exists blurred_path,
  drop column if exists caption,
  drop column if exists thumb_path,
  drop column if exists sha256,
  drop column if exists role;

drop index if exists catalog_items_barcode_idx;
alter table if exists catalog_items drop column if exists barcode;

drop table if exists supplier_reports;
drop table if exists supplier_action_cases;
drop table if exists supplier_actions;
drop table if exists market_case_events;
drop function if exists market_case_events_no_rewrite();
drop table if exists market_cases;
drop table if exists market_issue_types;
drop table if exists brands;
drop table if exists suppliers;
end $$;

do $$
declare
  t text;
begin
  foreach t in array array['v_market_cases_agent', 'supplier_reports', 'supplier_action_cases', 'supplier_actions',
                           'market_case_events', 'market_cases', 'market_issue_types', 'brands', 'suppliers'] loop
    if to_regclass('public.' || t) is not null then
      raise exception 'r8m_market_quality reverse: % still present', t;
    end if;
  end loop;
  if exists (select 1 from pg_proc where proname = 'market_case_events_no_rewrite'
             and pronamespace = 'public'::regnamespace) then
    raise exception 'r8m_market_quality reverse: the timeline trigger function is still present';
  end if;
  if exists (select 1 from information_schema.columns
             where table_schema = 'public'
               and ((table_name = 'market_observations' and column_name in
                     ('case_id', 'issue_type_id', 'report_basis', 'qty_affected', 'batch_ref', 'packaging_version',
                      'safety_flag', 'client_observed_at', 'brand_id', 'supplier_id', 'brand_text', 'supplier_text'))
                 or (table_name = 'market_photos' and column_name in
                     ('role', 'sha256', 'thumb_path', 'caption', 'blurred_path'))
                 or (table_name = 'catalog_items' and column_name = 'barcode'))) then
    raise exception 'r8m_market_quality reverse: a column is still present';
  end if;
  if exists (select 1 from pg_indexes where schemaname = 'public'
             and indexname in ('market_observations_case_idx', 'market_observations_issue_idx',
                               'market_observations_supplier_idx', 'market_observations_brand_idx',
                               'market_observations_yq_code_idx', 'market_observations_safety_idx',
                               'market_photos_sha256_idx', 'catalog_items_barcode_idx')) then
    raise exception 'r8m_market_quality reverse: an R8m index is still present';
  end if;
  if to_regclass('public.catalog_items') is null then
    raise exception 'r8m_market_quality reverse: catalog_items is missing: this file must never drop a base table';
  end if;
  if to_regclass('public.market_observations') is not null
     and not exists (select 1 from information_schema.columns where table_schema = 'public'
                     and table_name = 'market_observations' and column_name = 'yq_item_code') then
    raise exception 'r8m_market_quality reverse: market_observations.yq_item_code (R7b''s) must stay';
  end if;
  raise notice 'r8m_market_quality reverse: ok (agent view, 8 tables, the trigger function, 12 sighting + 5 photo columns, the barcode and their indexes gone; R7b untouched)';
end $$;
