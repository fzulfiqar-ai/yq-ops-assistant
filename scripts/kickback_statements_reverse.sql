-- Reverse of kickback_statements_migration.sql. Only run if the statements must be removed:
-- take `python -m scripts.db_backup --tables salesman_kickback_statements` FIRST (it holds the
-- documented record of what reps were shown).
drop table if exists salesman_kickback_statements;
