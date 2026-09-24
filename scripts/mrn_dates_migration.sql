-- Re-date mrn_landed_costs to the real receipt date (R2 review, 24-Sep-2026). Data-only, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/mrn_dates_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/mrn_dates_migration.sql
--   Reverse:  scripts/mrn_dates_reverse.sql
--
-- Why. Every row scripts/ingest_mrn.py wrote before R2 is dated to the 1st of its doc month
-- (production 24-Sep-2026: YQ-26-09-2 at 2026-09-01, YQ-26-02-2 at 2026-02-01, YQ-25-12-2 at
-- 2025-12-01) while the ledger books MRN:YQ-26-09-2 on 2026-09-20. R2 dates new loads by the real
-- receipt date, and the loader's "never older" guard would have compared the two conventions: an
-- OLDER receipt of the same month (YQ-26-09-1, ledger 2026-09-14 >= '2026-09-01'; YQ-26-02-1,
-- 02-05; YQ-25-12-1, 12-18) would pass it and overwrite the newer receipt's landed cost for every
-- shared SKU -- the cost app/shop.py _load_costs uses for the marketplace discount floor and
-- v_product_economics now prefers. The loader now resolves each stored doc's ledger date before
-- comparing (scripts/ingest_mrn.py current_cost_dates), so an un-migrated database is guarded too;
-- this migration makes the stored dates themselves true, BEFORE any new receipt is uploaded.
--
-- Rule: a row moves to MIN(move_date) of the ledger voucher 'MRN:<doc_no>' when that date is later
-- than the stored one and in the SAME month -- forward only, never across a month. A doc keyed by
-- a PO number whose MRN was raised under another number keeps its date, as does every row the
-- ledger has no voucher for (YQ-26-06-3 today: the loader's doc-sequence tiebreak covers it).
-- purchase_costs is not touched: effective_date is part of its unique key, the views prefer
-- mrn_landed_costs, and its "latest = MAX(id)" rule does not read the date.
-- No row is deleted or inserted.

update mrn_landed_costs m
   set effective_date = l.received_on
  from (select trim(substr(voucher, 5)) as doc_no, min(move_date) as received_on
          from stock_movements
         where voucher like 'MRN:%'
         group by 1) l
 where l.doc_no = m.doc_no
   and m.effective_date < l.received_on
   and date_trunc('month', m.effective_date) = date_trunc('month', l.received_on);

-- ── Self-check ─────────────────────────────────────────────────────────────────
do $$
declare
  n int;
begin
  select count(*) into n
    from mrn_landed_costs m
    join (select trim(substr(voucher, 5)) as doc_no, min(move_date) as received_on
            from stock_movements where voucher like 'MRN:%' group by 1) l on l.doc_no = m.doc_no
   where m.effective_date < l.received_on
     and date_trunc('month', m.effective_date) = date_trunc('month', l.received_on);
  if n <> 0 then
    raise exception 'mrn_landed_costs: % rows still dated before their ledger receipt', n;
  end if;
  -- the case the review named: YQ-26-09-2 carries the ledger date, when both are present
  if exists (select 1 from stock_movements where voucher = 'MRN:YQ-26-09-2')
     and exists (select 1 from mrn_landed_costs where doc_no = 'YQ-26-09-2'
                    and effective_date <> (select min(move_date) from stock_movements where voucher = 'MRN:YQ-26-09-2')) then
    raise exception 'YQ-26-09-2 is not dated by its ledger receipt';
  end if;
end $$;
