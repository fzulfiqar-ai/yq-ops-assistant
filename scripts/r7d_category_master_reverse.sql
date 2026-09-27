-- Reverse of scripts/r7d_category_master_migration.sql (release R7d). Idempotent.
--   python -m scripts.apply_sql scripts/r7d_category_master_reverse.sql
--
-- 1. Puts every product the category master moved back in the category it had before the FIRST
--    logged move (old_category_id may be NULL: the 40 that had none go back to none) — but only
--    where the product still holds the category the LAST logged move gave it, so a category someone
--    set by hand afterwards (or a newer Focus item-group load) is left alone.
-- 2. Drops category_master and category_master_log (no view or function depends on them; no CASCADE).
--    scripts/category_backfill.py probes category_master and simply stops applying it.
-- No order, line, event, customer or statement row is touched; products keeps every row.

do $$
begin
  if to_regclass('public.category_master_log') is not null then
    update products p
       set category_id = f.old_category_id, updated_at = now()
      from (select distinct on (product_id) product_id, old_category_id
              from category_master_log order by product_id, id asc) f
      join (select distinct on (product_id) product_id, new_category_id
              from category_master_log order by product_id, id desc) l on l.product_id = f.product_id
     where p.id = f.product_id
       and p.category_id is not distinct from l.new_category_id;
  end if;
end $$;

drop table if exists category_master_log;
drop table if exists category_master;

do $$
begin
  if to_regclass('public.category_master') is not null or to_regclass('public.category_master_log') is not null then
    raise exception 'r7d_category_master reverse: a master table is still present';
  end if;
  raise notice 'r7d_category_master reverse: ok';
end $$;
