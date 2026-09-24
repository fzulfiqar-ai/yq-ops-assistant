-- Reverse of user_roles_must_reset_migration.sql. The API treats a missing column as
-- must_reset = false, so this can run with the R1 API still deployed; members on a temporary
-- password simply stop being forced to change it. Take
-- `python -m scripts.db_backup --tables user_roles` first if the flags must be recoverable.
alter table user_roles drop column if exists must_reset;

do $$
begin
  if exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'user_roles' and column_name = 'must_reset') then
    raise exception 'user_roles.must_reset still present';
  end if;
end $$;
