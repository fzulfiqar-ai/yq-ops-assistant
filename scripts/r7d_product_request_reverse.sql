-- Reverse of r7d_product_request_migration.sql: the CHECK goes back to the 20 values it had before
-- R7d (the live definition read on 27-Sep-2026). No row is deleted or rewritten: the narrower CHECK
-- is added NOT VALID, so 'product_request' rows already written stay exactly as they are while
-- every NEW insert is held to the old list again. The web and API can stay deployed: a
-- 'product_request' insert then falls back to a 'search_zero' row (app/shop.py record_event).
set lock_timeout = '2s';

alter table shop_events drop constraint if exists shop_events_event_check;
alter table shop_events add constraint shop_events_event_check
  check (event in ('view', 'item', 'add', 'checkout', 'order',
                   'search', 'search_zero', 'remove', 'qty', 'cart', 'checkout_start',
                   'rail_click', 'reco_click', 'share', 'install', 'reorder', 'cancel', 'vitals',
                   'push_subscribe', 'error')) not valid;

do $$
begin
  if exists (select 1 from pg_constraint
             where conname = 'shop_events_event_check'
               and conrelid = 'public.shop_events'::regclass
               and pg_get_constraintdef(oid) like '%''product_request''%') then
    raise exception 'shop_events CHECK still allows product_request';
  end if;
  if not exists (select 1 from pg_constraint
                 where conname = 'shop_events_event_check'
                   and conrelid = 'public.shop_events'::regclass
                   and pg_get_constraintdef(oid) like '%''error''%') then
    raise exception 'shop_events CHECK lost error';
  end if;
end $$;
