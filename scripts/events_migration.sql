-- Phase B — Postgres-native event backbone (the platform "nervous system").
-- Idempotent; apply with:  python -m scripts.apply_migration scripts/events_migration.sql
--
-- Agents emit typed events (from the diffs run_agent already computes); an hourly
-- n8n call to /events/dispatch fans them out to subscribed agents via a rules-only
-- map in app/events.py. No broker (Kafka/Redis) — just this table + polling.

create table if not exists agent_events (
  id            bigint generated always as identity primary key,
  ts            timestamptz not null default now(),
  emitter       text not null,               -- 'agent:margin' | 'ingest' | 'procurement' | 'actions' | 'escalation'
  event_type    text not null,               -- 'stock.low' | 'margin.negative' | 'ingest.completed' | ...
  entity_type   text,                        -- 'item' | 'account' | 'vendor' | 'po' | null
  entity_key    text,
  severity      text not null default 'info' check (severity in ('info','warn','critical')),
  payload       jsonb not null default '{}'::jsonb,   -- includes chain_depth for cascade guarding
  fingerprint   text,                        -- 24h dedupe key (computed in Python at emit time)
  processed_at  timestamptz,                 -- set by dispatch()
  consumed_by   jsonb not null default '[]'::jsonb    -- list of reactions that fired
);

create index if not exists agent_events_unprocessed_idx
  on agent_events (ts) where processed_at is null;
create index if not exists agent_events_type_ts_idx
  on agent_events (event_type, ts desc);

-- RLS: same posture as agent_runs — service-role only (the backend reads/writes via
-- PostgREST as service_role, which bypasses RLS; no direct client access).
alter table agent_events enable row level security;
