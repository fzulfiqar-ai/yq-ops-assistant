-- Customer contact book — phones/emails for outreach (wa.me one-tap sends).
-- Filled by the sales_outreach agent's enrichment (public business listings via
-- Tavily — PDPL-conscious) and editable by hand later. Idempotent.

create table if not exists customer_contacts (
  customer_name text primary key,
  phone         text,
  email         text,
  website       text,
  source        text,            -- 'tavily' | 'manual'
  enriched_at   timestamptz,
  updated_by    text,
  updated_at    timestamptz default now()
);

alter table customer_contacts enable row level security;
drop policy if exists customer_contacts_read on customer_contacts;
create policy customer_contacts_read on customer_contacts for select to authenticated using (true);

grant select on customer_contacts to yq_readonly;
