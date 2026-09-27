-- Reverse of scripts/r7_notifications_migration.sql (release R7a). Idempotent.
--   python -m scripts.apply_sql scripts/r7_notifications_reverse.sql
--
-- Drops the notifications log. Its rows go with it, so take
--   python -m scripts.db_backup --tables shop_notifications
-- FIRST if the delivery history is wanted. Nothing else is touched: no order, line, event,
-- customer or statement row, and the legacy 'reminded' events stay where they are. Nothing
-- depends on the table, so no CASCADE.
--
-- The API keeps working through it: app/shop_notify.py forgets its cached probe the first time an
-- insert or a read meets the missing table, and app/shop_jobs.py then writes one 'reminded' event
-- per attempt again and reads its back-off state from those events alone (the pre-R7a behaviour).
-- Orders reminded while the table existed without reaching anyone have no event for those tries,
-- so right after a reverse their rep may be tried once more within the hour — never more.

drop table if exists shop_notifications;

do $$
begin
  if to_regclass('public.shop_notifications') is not null then
    raise exception 'r7_notifications reverse: shop_notifications still present';
  end if;
  raise notice 'r7_notifications reverse: ok';
end $$;
