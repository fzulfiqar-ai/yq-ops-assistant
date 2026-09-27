-- Reverse of r7_salesmen_login_unique_migration.sql: drop the unique index. No row changes.
-- The API keeps working (salesman_for_user links neither row when two share a login, and
-- upsert_salesman's duplicate message simply stops firing). Idempotent.
drop index if exists salesmen_user_email_lower_key;

do $$
begin
  if exists (select 1 from pg_indexes
              where schemaname = 'public' and tablename = 'salesmen' and indexname = 'salesmen_user_email_lower_key') then
    raise exception 'salesmen_user_email_lower_key still present';
  end if;
end $$;
