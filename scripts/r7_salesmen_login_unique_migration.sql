-- Release R7b "Safe access" (27-Sep-2026, plan §25 P2 "unique lower(user_email)"). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7_salesmen_login_unique_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7_salesmen_login_unique_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7_salesmen_login_unique_reverse.sql
--
-- Why: a login finds its salesmen row by user_email (app/shop.salesman_for_user — the rep's scope,
-- KPIs, link and kickback all hang on it). The lookup used ILIKE, which reads `_` and `%` in an
-- address as wildcards, and nothing stopped two reps from carrying the same login. The code now
-- matches the lower-cased address exactly (upsert_salesman already stores it lower-cased and
-- trimmed) and links NEITHER row if two share it; this index makes the second one impossible.
--
-- Checked read-only on production 27-Sep-2026: 18 salesmen, 17 with a login, 17 distinct
-- lower(btrim(user_email)), none mixed-case or padded — the index builds without touching a row.
-- If that ever stops being true, the guard below stops the file and names the problem instead of
-- failing half way. salesmen has 18 rows: a plain (not CONCURRENTLY) build takes milliseconds.
-- No new table, view or sequence: nothing to grant or revoke (salesmen stays service-role only).
-- Order: any time; the code does not depend on it.

do $$
declare
  dup int;
begin
  select count(*) into dup from (
    select lower(user_email) from salesmen where user_email is not null
     group by 1 having count(*) > 1) d;
  if dup > 0 then
    raise exception '% login(s) are linked to more than one salesman: give each rep his own login on the Salesmen page, then re-run', dup;
  end if;
end $$;

create unique index if not exists salesmen_user_email_lower_key
  on salesmen (lower(user_email)) where user_email is not null;

comment on index salesmen_user_email_lower_key is
  'One salesman per portal login (R7b): app/shop.salesman_for_user matches the lower-cased address exactly.';

do $$
begin
  if not exists (select 1 from pg_indexes
                  where schemaname = 'public' and tablename = 'salesmen'
                    and indexname = 'salesmen_user_email_lower_key'
                    and indexdef ilike '%unique%lower(user_email)%') then
    raise exception 'salesmen_user_email_lower_key missing or not unique on lower(user_email)';
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name = 'salesmen'
                and grantee in ('anon', 'authenticated')) then
    raise exception 'salesmen must not be granted to anon/authenticated';
  end if;
end $$;
