-- Optional rollout step for Market Intelligence (release R7b): give every salesman login the new
-- 'Market Intel' page, i.e. the "Spotted" camera button and "My signals". Idempotent; data only.
--   Run AFTER scripts/r7b_market_intel_migration.sql:
--     python -m scripts.apply_sql scripts/r7b_market_intel_grants.sql
--   Reverse: scripts/r7b_market_intel_grants_reverse.sql
--
-- New salesman invites get the page anyway (app/features.ROLE_DEFAULT_FEATURES['salesman']); this
-- only reaches the logins that existed before R7b. It appends one string to user_roles.features and to
-- pending salesman invites, nothing else: no other page is added or removed, no role, status or
-- password changes. Granting 'Market Intel' never opens the AI Assistant (/ask): the two are separate.
-- The API caches a login's row for a short while, so a rep sees the button within a few minutes.

update user_roles
   set features = features || '["Market Intel"]'::jsonb
 where role = 'salesman'
   and jsonb_typeof(features) = 'array'
   and not features ? 'Market Intel';

update app_invites
   set features = features || '["Market Intel"]'::jsonb
 where role = 'salesman'
   and status = 'pending'
   and jsonb_typeof(features) = 'array'
   and not features ? 'Market Intel';

do $$
declare
  n int;
begin
  select count(*) into n from user_roles
   where role = 'salesman' and jsonb_typeof(features) = 'array' and not features ? 'Market Intel';
  if n > 0 then
    raise exception 'r7b_market_intel_grants: % salesman logins still lack Market Intel', n;
  end if;
  raise notice 'r7b_market_intel_grants: ok (every salesman login holds Market Intel)';
end $$;
