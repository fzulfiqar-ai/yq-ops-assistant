-- Reverse of shop_events_error_migration.sql: the CHECK goes back to the 19 values it had before R6
-- (the live definition read on 24-Sep-2026). A narrower CHECK cannot be added while 'error' rows
-- exist, so they are removed first — they are client error telemetry (build, error class, scrubbed
-- message, route template), never a merchant action, an order or an attribution; nothing else is
-- touched. The web can stay deployed: an 'error' insert simply fails again, as it did before R6.
set lock_timeout = '2s';

delete from shop_events where event = 'error';

alter table shop_events drop constraint if exists shop_events_event_check;
alter table shop_events add constraint shop_events_event_check
  check (event in ('view', 'item', 'add', 'checkout', 'order',
                   'search', 'search_zero', 'remove', 'qty', 'cart', 'checkout_start',
                   'rail_click', 'reco_click', 'share', 'install', 'reorder', 'cancel', 'vitals',
                   'push_subscribe'));

do $$
begin
  if exists (select 1 from pg_constraint
             where conname = 'shop_events_event_check'
               and conrelid = 'public.shop_events'::regclass
               and pg_get_constraintdef(oid) like '%''error''%') then
    raise exception 'shop_events CHECK still allows error';
  end if;
end $$;
