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
drop function if exists ingest_stage_analyze();
drop function if exists _ingest_apply_target(bigint, text, jsonb);
drop function if exists _ingest_scope_overlap(jsonb, jsonb);
drop function if exists _ingest_target_cfg(text);
drop function if exists _ingest_cast(text, text, text);
drop function if exists _ingest_eq(text, text, text);
drop function if exists _ingest_cols(text);

-- the read grants the preview needed (yq_readonly), back to what security_migration.sql set
do $$
declare t text;
begin
  foreach t in array array['orders', 'order_lines', 'stock_movements', 'ledger_entries', 'ar_ageing',
                           'product_profitability', 'product_aliases', 'purchase_costs'] loop
    if to_regclass('public.' || t) is null then
      continue;
    end if;
    execute format('drop policy if exists %I on %I', t || '_yq_readonly_read', t);
    execute format('revoke select on %I from yq_readonly', t);
  end loop;
end $$;

drop table if exists ingest_replaced;
drop table if exists ingest_stage;
drop table if exists ingest_batches;
