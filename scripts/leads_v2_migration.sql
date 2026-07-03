-- Leads v2 — contactability fields + follow-up tracking.
-- email/address are filled by hand or by the OPTIONAL Tavily enrichment (public
-- business info only — PDPL-conscious; nothing is scraped from private sources).
-- Idempotent; safe to re-run.

alter table leads
  add column if not exists email          text,
  add column if not exists address        text,
  add column if not exists contact_name   text,
  add column if not exists last_contacted date,
  add column if not exists next_action    text,
  add column if not exists enriched_at    timestamptz;

grant select on leads to yq_readonly;
