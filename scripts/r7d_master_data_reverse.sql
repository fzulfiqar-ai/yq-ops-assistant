-- Reverse of scripts/r7d_master_data_migration.sql (release R7d). Idempotent.
--   python -m scripts.apply_sql scripts/r7d_master_data_reverse.sql
--
-- 1. Clears every customers.segment / customers.area the seed wrote — only while the value is still
--    the one it wrote (a value someone edited afterwards stays), then drops master_data_seed_log.
-- 2. Drops the salesman_targets trigger + function, its index, FK and the salesman_id column (the
--    kickback math never read it; `salesman` stays the key), and salesmen.territory /
--    salesmen.focus_aliases. Anything typed into those two columns since is lost — export first if
--    the owner has started using them.
-- No customer row is deleted; no order, line, event or statement is touched. No CASCADE: nothing
-- depends on these objects (a dependency would make the DROP fail loudly, which is intended).

do $$
begin
  if to_regclass('public.master_data_seed_log') is not null then
    update customers c
       set segment = null
      from master_data_seed_log l
     where l.entity = 'customers' and l.field = 'segment' and c.id = l.entity_id
       and c.segment is not distinct from l.new_value;
    update customers c
       set area = null
      from master_data_seed_log l
     where l.entity = 'customers' and l.field = 'area' and c.id = l.entity_id
       and c.area is not distinct from l.new_value;
  end if;
end $$;

drop table if exists master_data_seed_log;

drop trigger if exists salesman_targets_fill_salesman_id on salesman_targets;
drop function if exists salesman_targets_fill_salesman_id();
drop index if exists salesman_targets_salesman_id_period_idx;
alter table salesman_targets drop constraint if exists salesman_targets_salesman_id_fkey;
alter table salesman_targets drop column if exists salesman_id;

alter table salesmen drop column if exists territory;
alter table salesmen drop column if exists focus_aliases;

do $$
begin
  if to_regclass('public.master_data_seed_log') is not null then
    raise exception 'r7d_master_data reverse: master_data_seed_log still present';
  end if;
  if exists (select 1 from information_schema.columns where table_schema = 'public'
               and ((table_name = 'salesman_targets' and column_name = 'salesman_id')
                 or (table_name = 'salesmen' and column_name in ('territory', 'focus_aliases')))) then
    raise exception 'r7d_master_data reverse: a master-data column is still present';
  end if;
  raise notice 'r7d_master_data reverse: ok';
end $$;
