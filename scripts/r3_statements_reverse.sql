-- Reverse of r3_statements_migration.sql. Drops only the R3a metadata columns and index; no
-- statement row is deleted or changed. Take `python -m scripts.db_backup --tables
-- salesman_kickback_statements` first if any row has been superseded or paid through the API,
-- because the who/when/why of those moves lives in these columns (the audit_log rows remain).
-- The API keeps working without them (it retries writes without the optional columns).
drop index if exists salesman_kickback_statements_rep_status_idx;
alter table salesman_kickback_statements drop column if exists superseded_by_id;
alter table salesman_kickback_statements drop column if exists superseded_reason;
alter table salesman_kickback_statements drop column if exists superseded_by;
alter table salesman_kickback_statements drop column if exists superseded_at;
alter table salesman_kickback_statements drop column if exists paid_by;
