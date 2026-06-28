-- Advance lead-gen: classify each lead by SEGMENT (modern trade vs reseller vs general) and detect
-- the BRAND (known chain) so the team can pursue strategic modern-trade accounts separately from
-- quick independent-reseller wins. Idempotent.
alter table leads add column if not exists segment text;   -- modern_trade | wholesale | electronics | mobile | general
alter table leads add column if not exists brand   text;   -- matched chain name (lulu, ansar, sharaf_dg, ...)
create index if not exists leads_segment_idx on leads (segment);

-- Segment mix view (for the Leads page header / BI).
create or replace view v_lead_segments as
select coalesce(segment, 'unknown') as segment, count(*) as leads,
       round(coalesce(avg(fit_score), 0)::numeric, 0) as avg_fit
from leads group by coalesce(segment, 'unknown') order by leads desc;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on v_lead_segments to yq_readonly;
  end if;
end $$;
