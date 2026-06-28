-- Per-agent scheduling. The admin sets a cadence per agent in the portal; a single hourly n8n
-- call (GET /scheduler/run-due) runs + emails the agents that are due. Idempotent.
create table if not exists agent_schedules (
  agent      text primary key,
  cadence    text not null default 'off' check (cadence in ('off','daily','weekly')),
  last_ran   date,                       -- guard: never run the same agent twice in one day
  updated_by text,
  updated_at timestamptz default now()
);

alter table agent_schedules enable row level security;  -- service-role only (backend manages it)
