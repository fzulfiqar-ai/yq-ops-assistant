-- Release R7b "Safe access" (27-Sep-2026, plan §25 P1 / §28 "Privacy"): personal ID numbers in the
-- Focus Sales Day Book narration never leave v_sales. Additive, idempotent, CREATE OR REPLACE only.
--   Rehearse: python -m scripts.apply_sql scripts/r7_narration_mask_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7_narration_mask_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7_narration_mask_reverse.sql
--
-- Why: staff type customers' CPR numbers (Bahrain personal ID, 9 digits) and card / account numbers
-- (15 digits and more) into the invoice Narration. 217 of the 1,796 narrated order lines carried one
-- (read-only count on production, 27-Sep-2026) and v_sales hands them to everything that reads it —
-- v_sales_agent and the AI's free-text SQL (yq_readonly) among them.
--
-- What: v_sales.narration becomes the MASKED narration — every 9-digit run and every run of 15+
-- digits keeps its last 3 digits:
--     850512345        -> ******345         (the length of a 9-digit run is kept)
--     4111111111111111 -> ************111   (15+ digits: always 12 stars + 3)
-- A run is digits with no digit on either side, so the order number the storekeeper types for the
-- Focus matcher (YQ-2609-0019, YQ 2609 0019, YQ26090019 — 4- and 8-digit runs) and every invoice /
-- voucher number pass untouched: v_shop_focus_recon and v_shop_focus_candidates read v_sales.narration
-- and still find their orders. Masked text never matches again (masking twice changes nothing).
-- The same two patterns mask the narration at parse time (scripts/ingest.py mask_personal_ids);
-- tests/test_r7b_security.py holds the two spellings equal. Proven read-only on production
-- 27-Sep-2026: the SQL mask and the parser agree on all 1,796 narrations, none is left with a 9- or
-- a 15+-digit run, and the expression costs ~8 ms over the whole of order_lines.
--
-- v_sales_agent is `select * from v_sales` (daily_ops2_migration.sql): its column list was fixed
-- when it was created, and it reads v_sales at query time, so it is masked by this file without
-- being touched. So are the 20 other views built on v_sales (v_sales_by_*, v_customer_ltv,
-- v_shop_focus_*, ...): CREATE OR REPLACE keeps every dependent view and every grant.
--
-- The body below is division_payment_migration.sql's v_sales (identical to the live definition,
-- read with pg_get_viewdef on 27-Sep-2026) with ONE change: `ol.narration` -> the masked
-- expression, still named narration, still text. Every column keeps its name, position and type,
-- which is what CREATE OR REPLACE VIEW requires. From this release THIS file is the canonical owner
-- of v_sales (docs/MIGRATIONS.md); re-running division_payment_migration.sql would unmask it again.
-- The stored order_lines.narration is not changed here — that is the separate, OPTIONAL
-- scripts/r7_narration_mask_data_migration.sql (the owner decides; it cannot be undone from SQL).
--
-- Order: any time; the code does not depend on it (the parser masks new loads either way).
-- Never security_invoker: run_readonly_query runs as yq_readonly, which is granted views only.

create or replace view v_sales as
select
    ol.id                                          as line_id,
    ol.invoice_no,
    ol.line_no,
    coalesce(ol.line_date, o.order_date)           as sale_date,
    o.order_date,
    o.customer_name,
    ol.customer_account,
    o.salesman,
    o.payment_mode,
    o.sales_account_name,
    ol.item_name,
    p.sku_code,
    p.item_name                                    as product_name,
    cat.name                                       as category_name,
    ol.quantity,
    ol.rate_bhd,
    ol.gross_bhd,
    ol.discount_bhd,
    ol.taxable_bhd,
    ol.vat_amount_bhd,
    coalesce(ol.total_amount_bhd, ol.gross_bhd)    as revenue_bhd,
    coalesce(ol.taxable_bhd, ol.gross_bhd / 1.1)   as net_bhd,
    coalesce(ol.total_amount_bhd, ol.gross_bhd)    as total_amount_bhd,
    coalesce(o.salesman, ol.warehouse_name)        as salesman_resolved,
    ol.warehouse_name                              as salesman_raw,
    coalesce(sc.channel,
             case when coalesce(o.salesman, ol.warehouse_name) in ('Causeway', 'YQ Roadshow')
                  then 'B2C' else 'B2B' end)        as channel,
    (coalesce(o.customer_name, ol.customer_account) ilike 'cash customer%') as is_cash_customer,
    -- R7b: personal ID numbers masked (9-digit runs, 15+-digit runs; the last 3 digits kept).
    -- The guard skips the two regexp passes on every narration without a 9-digit stretch.
    case when ol.narration ~ '[0-9]{9}' then
      regexp_replace(
        regexp_replace(ol.narration, '(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])', '************\1', 'g'),
        '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')
    else ol.narration end                          as narration,
    -- division: item-group mapping; SIM item names win even if the group is generic
    case
      when cat.division in ('SIM', 'Giveaway', 'Devices', 'Accessories') then cat.division
      when ol.item_name ilike '%sim%' or ol.item_name ilike '%batelco%'  then 'SIM'
      else 'Accessories'
    end                                            as division,
    -- cash vs credit: Focus header fields first, walk-in flag as backstop
    case
      when o.payment_mode ilike 'cash%'                    then 'cash'
      when o.payment_mode ilike 'credit%'                  then 'credit'
      when o.payment_mode ilike 'benefit%'                 then 'cash'
      when o.sales_account_name ilike '%credit%'           then 'credit'
      when o.sales_account_name ilike '%cash%'             then 'cash'
      when coalesce(o.customer_name, ol.customer_account) ilike 'cash customer%' then 'cash'
      else 'credit'
    end                                            as sale_type,
    -- free stock issued through sales (zero-priced lines) or the Giveaway division
    (coalesce(cat.division, '') = 'Giveaway'
     or (coalesce(ol.rate_bhd, 0) = 0 and coalesce(ol.gross_bhd, 0) = 0
         and coalesce(ol.quantity, 0) > 0))        as is_giveaway
from order_lines ol
left join orders          o   on o.invoice_no  = ol.invoice_no
left join product_aliases pa  on pa.alias_text = ol.item_name
left join products        p   on p.id          = pa.product_id
left join categories      cat on cat.id        = p.category_id
left join salesman_channels sc on sc.salesman  = coalesce(o.salesman, ol.warehouse_name);

comment on column v_sales.narration is
  'Focus Sales Day Book narration with personal ID numbers masked: every 9-digit run and every run of 15+ digits keeps only its last 3 digits (R7b, scripts/r7_narration_mask_migration.sql). Order numbers such as YQ-2609-0019 are untouched.';

-- Unchanged grants, restated so a re-run can never widen them (plain statements, never in a DO block).
revoke all on table v_sales from anon, authenticated;
revoke all on table v_sales_agent from anon, authenticated;

-- Self-check: the column is the masked one and still text, the view still has its 31 columns,
-- nothing leaks to the browser roles, the AI's read-only role still reads both views, and no
-- narration visible through v_sales or v_sales_agent still carries a 9-digit or a 15+-digit run.
do $$
declare
  n_cols int;
  typ text;
begin
  select count(*) into n_cols from pg_attribute
   where attrelid = 'public.v_sales'::regclass and attnum > 0 and not attisdropped;
  if n_cols <> 31 then
    raise exception 'v_sales has % columns, expected 31', n_cols;
  end if;
  select format_type(atttypid, atttypmod) into typ from pg_attribute
   where attrelid = 'public.v_sales'::regclass and attname = 'narration';
  if typ is distinct from 'text' then
    raise exception 'v_sales.narration is %, expected text', typ;
  end if;
  if position('regexp_replace' in pg_get_viewdef('public.v_sales'::regclass)) = 0 then
    raise exception 'v_sales.narration is not the masked expression';
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name in ('v_sales', 'v_sales_agent')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'v_sales / v_sales_agent must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and not (has_table_privilege('yq_readonly', 'public.v_sales', 'SELECT')
              and has_table_privilege('yq_readonly', 'public.v_sales_agent', 'SELECT')) then
    raise exception 'yq_readonly lost SELECT on v_sales / v_sales_agent';
  end if;
  if exists (select 1 from v_sales
              where narration ~ '(^|[^0-9])[0-9]{9}([^0-9]|$)' or narration ~ '[0-9]{15,}') then
    raise exception 'a narration in v_sales still carries a 9-digit or a 15+-digit number';
  end if;
  if exists (select 1 from v_sales_agent
              where narration ~ '(^|[^0-9])[0-9]{9}([^0-9]|$)' or narration ~ '[0-9]{15,}') then
    raise exception 'a narration in v_sales_agent still carries a 9-digit or a 15+-digit number';
  end if;
end $$;
