-- Salesman targets: tiered kickback scheme (21-Sep-2026, from Harsh Bhatia's "Accessories Target"
-- email). Idempotent.  Apply:  python -m scripts.apply_sql scripts/targets_tiers_migration.sql
--
-- The scheme is not one number per rep: monthly sales thresholds open three kickback tiers.
--   Normal sales team            Tier 1 BD 100 (5%)  · Tier 2 BD 200 (7%)  · Tier 3 BD 300 (8%)
--   Mobile Accessories Team      Tier 1 BD 500 (5%)  · Tier 2 BD 1200 (7%) · Tier 3 BD 2000 (8%)
--   (Moideen KP, Karrar Mohamed, Ahmed Aradi)
-- target_bhd stays the Tier-1 threshold (the "minimum sales per month"); the extra columns carry
-- the rest so any future attainment view can compute the tier reached and the kickback due.

alter table salesman_targets add column if not exists team          text;
alter table salesman_targets add column if not exists tier2_bhd     numeric;
alter table salesman_targets add column if not exists tier3_bhd     numeric;
alter table salesman_targets add column if not exists kickback_t1   numeric;   -- fraction, 0.05 = 5%
alter table salesman_targets add column if not exists kickback_t2   numeric;
alter table salesman_targets add column if not exists kickback_t3   numeric;

comment on column salesman_targets.target_bhd is 'Tier 1 threshold = minimum monthly sales (BHD).';
comment on column salesman_targets.team is 'normal | mobile_accessories (the 3-member team with higher tiers).';
