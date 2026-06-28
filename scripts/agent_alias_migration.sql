-- NOW#3 agent consolidation: repoint saved schedules from retired agent names to their
-- successors. risk_watch = anomaly + fraud;  purchase_insights = procurement + purchase_tracker.
-- Two old rows can map to one new name, so pick the most-active cadence (daily > weekly > off),
-- upsert the successor, then drop the old rows. Idempotent. (run_agent() also resolves these
-- aliases at runtime, so this is belt-and-suspenders for the agent_schedules PK.)
do $$
declare c text;
begin
  -- risk_watch ← anomaly, fraud
  select cadence into c from agent_schedules where agent in ('anomaly', 'fraud')
    order by case cadence when 'daily' then 2 when 'weekly' then 1 else 0 end desc limit 1;
  if c is not null then
    insert into agent_schedules (agent, cadence, updated_by) values ('risk_watch', c, 'alias_migration')
      on conflict (agent) do update set cadence = excluded.cadence, updated_at = now();
    delete from agent_schedules where agent in ('anomaly', 'fraud');
  end if;

  -- purchase_insights ← procurement, purchase_tracker
  select cadence into c from agent_schedules where agent in ('procurement', 'purchase_tracker')
    order by case cadence when 'daily' then 2 when 'weekly' then 1 else 0 end desc limit 1;
  if c is not null then
    insert into agent_schedules (agent, cadence, updated_by) values ('purchase_insights', c, 'alias_migration')
      on conflict (agent) do update set cadence = excluded.cadence, updated_at = now();
    delete from agent_schedules where agent in ('procurement', 'purchase_tracker');
  end if;
end $$;
