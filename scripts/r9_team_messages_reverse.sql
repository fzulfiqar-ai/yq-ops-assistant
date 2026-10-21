-- Reverse of scripts/r9_team_messages_migration.sql (release 2b, X1). Idempotent.
--   python -m scripts.apply_sql scripts/r9_team_messages_reverse.sql ; python -m scripts.audit_grants
-- From the MAIN project folder only.
--
-- Drops the team-message table. Its rows go with it (each is the text /yq-brief team prints for that load, so it
-- can be made again from the loaded data); take
--   python -m scripts.db_backup --tables team_messages
-- first if the stored copies are wanted. Nothing depends on the table and nothing else is touched. The Command
-- Centre's Copy button then has nothing to show, and a load's post step can no longer store the message (the plan
-- makes that one journal line, never a failed load).

set lock_timeout = '2s';      -- fail fast, never queue

drop table if exists team_messages;

do $$
begin
  if to_regclass('public.team_messages') is not null then
    raise exception 'r9_team_messages reverse: team_messages still present';
  end if;
  raise notice 'r9_team_messages reverse: ok';
end $$;
