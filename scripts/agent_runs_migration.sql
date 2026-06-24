-- Phase B: agent memory — what the headline numbers were on each run, for diffing.
-- Idempotent. Service-role only (RLS enabled, no authenticated read policy) like audit_log.
create table if not exists agent_runs (
  id           bigint generated always as identity primary key,
  agent        text not null,
  ran_at       timestamptz default now(),
  summary      text,
  metrics      jsonb,        -- flat dict of headline numbers
  item_keys    jsonb,        -- sorted stable identifiers seen this run
  triggered_by text default 'schedule'   -- 'schedule' | 'user' | 'escalation'
);
create index if not exists agent_runs_agent_time on agent_runs (agent, ran_at desc);

alter table agent_runs enable row level security;
-- no policy → only the backend service-role key can read/write (bypasses RLS)
