-- Reverse of ingest_batches_migration.sql.
-- Refuses while a committed batch exists: ingest_replaced holds the only copies of the rows those
-- batches removed or changed, and dropping it would make ingest_undo impossible. Undo (or accept)
-- every committed batch first, then take `python -m scripts.db_backup --tables
-- ingest_batches,ingest_stage,ingest_replaced` before running this.
do $$
begin
  if to_regclass('public.ingest_batches') is not null
     and exists (select 1 from ingest_batches where status = 'committed') then
    raise exception 'REFUSING: committed ingest batches exist; undo them first (their replaced rows live only in ingest_replaced)';
  end if;
end $$;

drop function if exists ingest_commit(bigint, jsonb);
drop function if exists ingest_undo(bigint, text);
drop function if exists ingest_prune(interval);
drop function if exists ingest_stage_analyze();
drop function if exists _ingest_apply_target(bigint, text, jsonb);
drop function if exists _ingest_scope_overlap(jsonb, jsonb);
drop function if exists _ingest_target_cfg(text);
drop function if exists _ingest_cast(text, text, text);
drop function if exists _ingest_eq(text, text, text);
drop function if exists _ingest_cols(text);

-- No role grants to undo: the migration grants nothing to yq_readonly, service_role, anon or
-- authenticated (the API runs the batch path over its own DATABASE_URL session).

drop table if exists ingest_replaced;
drop table if exists ingest_stage;
drop table if exists ingest_batches;
