-- Reverse of mrn_dates_migration.sql: every mrn_landed_costs row whose doc has a ledger voucher
-- goes back to the 1st of its month (the pre-R2 convention). Rows the migration did not touch
-- (no voucher, or a receipt in another month) are already there. Data-only; nothing deleted.
--   python -m scripts.apply_sql scripts/mrn_dates_reverse.sql

update mrn_landed_costs m
   set effective_date = date_trunc('month', m.effective_date)::date
 where m.effective_date <> date_trunc('month', m.effective_date)::date
   and exists (select 1 from stock_movements s where s.voucher = 'MRN:' || m.doc_no);

do $$
declare
  n int;
begin
  select count(*) into n
    from mrn_landed_costs m
   where m.effective_date <> date_trunc('month', m.effective_date)::date
     and exists (select 1 from stock_movements s where s.voucher = 'MRN:' || m.doc_no);
  if n <> 0 then
    raise exception 'mrn_landed_costs: % ledger-dated rows remain', n;
  end if;
end $$;
