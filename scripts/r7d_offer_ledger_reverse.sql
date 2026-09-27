-- Reverse of scripts/r7d_offer_ledger_migration.sql (release R7d, offer ledger + follow-up exposures).
-- Idempotent.
--   python -m scripts.apply_sql scripts/r7d_offer_ledger_reverse.sql
--
-- Drops the two views, the two coupon functions, and — ONLY when they are empty or you say so — the
-- three measurement tables and the archive columns. No order, line, event, customer, rep or statement
-- row is touched. Nothing depends on the two views, so no CASCADE anywhere.
--
-- shop_order_discounts / shop_badge_log / followup_exposures hold the only record of which offer each
-- order got, which badges shops saw and who was served or held back; an archived rule or campaign
-- carries archived_at. While any of them holds data this file REFUSES, so a routine reverse can never
-- lose it. To drop them anyway, back them up first and say so in the same session:
--   python -m scripts.db_backup --tables shop_order_discounts,shop_badge_log,followup_exposures,discount_rules,shop_campaigns
--   set yq.offer_ledger_drop = 'yes';   -- then run this file in that same session
-- Dropping archived_at does NOT bring an archived row back to life: archiving also switched it off
-- (is_active = false), and that stays.
--
-- The API keeps working through a reverse: the ledger, the badge log and the exposures are probed and
-- skipped, the coupon counter falls back to the old read-then-write, the Offers & Rules readout and the
-- follow-up card say "not switched on yet". RESTART THE API AFTER REVERSING: a table probe is
-- remembered for 10 minutes.

do $$
declare
  n bigint := 0;
  t text;
  m bigint;
begin
  foreach t in array array['shop_order_discounts', 'shop_badge_log', 'followup_exposures'] loop
    if to_regclass('public.' || t) is not null then        -- dynamic: the table may be gone already
      execute format('select count(*) from public.%I', t) into m;
      n := n + m;
    end if;
  end loop;
  if exists (select 1 from information_schema.columns where table_schema = 'public'
             and table_name = 'discount_rules' and column_name = 'archived_at') then
    execute 'select count(*) from public.discount_rules where archived_at is not null' into m;
    n := n + m;
  end if;
  if exists (select 1 from information_schema.columns where table_schema = 'public'
             and table_name = 'shop_campaigns' and column_name = 'archived_at') then
    execute 'select count(*) from public.shop_campaigns where archived_at is not null' into m;
    n := n + m;
  end if;
  if n > 0 and coalesce(current_setting('yq.offer_ledger_drop', true), '') <> 'yes' then
    raise exception 'r7d_offer_ledger reverse: % ledger / badge / exposure row(s) or archived rule(s) / campaign(s) would be lost. Back them up (python -m scripts.db_backup --tables shop_order_discounts,shop_badge_log,followup_exposures,discount_rules,shop_campaigns) and run SET yq.offer_ledger_drop = ''yes'' in this session to drop them.', n;
  end if;
end $$;

drop view if exists v_followup_lift;
drop view if exists v_offer_performance;

drop function if exists shop_coupon_reserve(bigint, boolean);
drop function if exists shop_coupon_release(bigint);

drop table if exists followup_exposures;
drop table if exists shop_badge_log;
drop table if exists shop_order_discounts;

alter table shop_campaigns drop column if exists archived_by;
alter table shop_campaigns drop column if exists archived_at;
alter table discount_rules drop column if exists archived_by;
alter table discount_rules drop column if exists archived_at;

do $$
declare
  v text;
begin
  foreach v in array array['v_followup_lift', 'v_offer_performance', 'followup_exposures', 'shop_badge_log',
                           'shop_order_discounts'] loop
    if to_regclass('public.' || v) is not null then
      raise exception 'r7d_offer_ledger reverse: % still present', v;
    end if;
  end loop;
  if exists (select 1 from pg_proc where proname in ('shop_coupon_reserve', 'shop_coupon_release')
             and pronamespace = 'public'::regnamespace) then
    raise exception 'r7d_offer_ledger reverse: a coupon function is still present';
  end if;
  if exists (select 1 from information_schema.columns where table_schema = 'public'
             and table_name in ('discount_rules', 'shop_campaigns') and column_name in ('archived_at', 'archived_by')) then
    raise exception 'r7d_offer_ledger reverse: an archive column is still present';
  end if;
  if to_regclass('public.shop_orders') is null or to_regclass('public.shop_order_lines') is null
     or to_regclass('public.discount_rules') is null or to_regclass('public.shop_campaigns') is null then
    raise exception 'r7d_offer_ledger reverse: a base table is missing -- this file must never touch one';
  end if;
  raise notice 'r7d_offer_ledger reverse: ok (views, coupon functions, measurement tables and archive columns gone; base tables untouched)';
end $$;
