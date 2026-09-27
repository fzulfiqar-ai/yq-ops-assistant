-- Reverse of scripts/r7b_market_intel_grants.sql: takes the 'Market Intel' page off every salesman
-- login and pending salesman invite. Idempotent; data only. It removes that one string and keeps
-- every other page in the list in its order. (An admin who granted the page to a rep by hand on the
-- Team page loses it too: re-grant there if needed.)
--   python -m scripts.apply_sql scripts/r7b_market_intel_grants_reverse.sql

update user_roles
   set features = features - 'Market Intel'
 where role = 'salesman'
   and jsonb_typeof(features) = 'array'
   and features ? 'Market Intel';

update app_invites
   set features = features - 'Market Intel'
 where role = 'salesman'
   and status = 'pending'
   and jsonb_typeof(features) = 'array'
   and features ? 'Market Intel';

do $$
begin
  if exists (select 1 from user_roles where role = 'salesman' and jsonb_typeof(features) = 'array'
             and features ? 'Market Intel') then
    raise exception 'r7b_market_intel_grants reverse: a salesman login still holds Market Intel';
  end if;
  raise notice 'r7b_market_intel_grants reverse: ok';
end $$;
