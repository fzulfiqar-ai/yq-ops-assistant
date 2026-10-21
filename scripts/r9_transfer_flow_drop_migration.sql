-- Release 2b (R9, 21-Oct-2026), migration M2: drop v_transfer_flow. Idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r9_transfer_flow_drop_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r9_transfer_flow_drop_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r9_transfer_flow_drop_reverse.sql (re-creates it exactly as daily_ops2_migration.sql has it)
-- From the MAIN project folder only, and only AFTER the 2b API is live and `python -m scripts.ops moves portal`
-- reads PASS (scripts/r9_release.ps1 part 6).
--
-- Why: v_transfer_flow paired an issue with a receipt by the voucher NUMBER (SIO:x <-> STRV:x). Focus numbers the two
-- legs on their own, so on the 20-Oct ledger the numbers matched destination and lines for fewer than half of the
-- issues, and the Stock Movement page's Pending / Partial were not reliable. From 2b, GET /stock/flow pairs by the
-- vouchers' contents over v_transfer_lines (r9_stock_moves_migration.sql) with the code /yq-moves runs, and
-- v_transfer_flow had no other reader (only the old /stock/flow route read it; nothing depends on it in the
-- database, so a plain DROP; should a view ever depend on it, the DROP refuses and names it, which is the protection).
--
-- The first block refuses, and changes nothing, while v_transfer_lines is missing: then M1 has not run, the 2b API
-- cannot be live, and the API that is running still reads this view.
-- Re-running daily_ops2_migration.sql afterwards would create the view again (harmless, but this file then needs
-- to run again).

set lock_timeout = '2s';      -- a busy view makes the file fail fast, never queue

do $$
begin
  if to_regclass('public.v_transfer_lines') is null then
    raise exception 'r9_transfer_flow_drop: v_transfer_lines is missing: apply scripts/r9_stock_moves_migration.sql (M1) and deploy the 2b API first; the running API still reads v_transfer_flow';
  end if;
end $$;

drop view if exists v_transfer_flow;

do $$
begin
  if to_regclass('public.v_transfer_flow') is not null then
    raise exception 'r9_transfer_flow_drop: v_transfer_flow is still there';
  end if;
  raise notice 'r9_transfer_flow_drop: ok (v_transfer_flow dropped; GET /stock/flow pairs by contents over v_transfer_lines)';
end $$;
