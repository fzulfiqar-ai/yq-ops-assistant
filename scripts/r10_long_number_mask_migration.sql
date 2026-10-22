-- Release r10 "the long-number mask" (22-Oct-2026): every run of 10 or more digits in a Focus narration is masked,
-- not only a run of 9 or a run of 15 and more. Idempotent: CREATE OR REPLACE, and a data step that cannot be undone
-- from SQL.
--   Rehearse: python -m scripts.apply_sql scripts/r10_long_number_mask_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r10_long_number_mask_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r10_long_number_mask_reverse.sql (v_sales only: the stored narrations stay masked)
-- From the MAIN project folder only (a worktree finds the main folder's .env, which is production), after the
-- release backup (python -m scripts.yq_backup release r10), before the API deploy of the same release, never during
-- a Focus load (docs/RELEASE.md, "Release r10"). Neither needs the other: the API reads nothing this file changes.
--
-- Why. R7b (r7_narration_mask_migration.sql and r7_narration_mask_data_migration.sql) and release 2b
-- (r9_stock_moves_migration.sql) mask a run of exactly 9 digits (a CPR) and a run of 15 digits or more (a card),
-- keeping the last 3. A run of 10 to 14 digits stayed in clear: a phone number with its country code, a bank account
-- number, a 12- or 14-digit stretch of one. The review of release 2b counted 15 order lines with such a run on the
-- masked copy of production, readable through v_sales and so by the AI's read role.
--
-- What. A run of exactly 9 digits keeps its last 3 behind 6 stars, as before (its length is kept); a run of 10
-- digits or MORE keeps its last 3 behind 12 stars (the length is not kept):
--     900000123             -> ******123
--     12345678901           -> ************901
--     12345678901234567890  -> ************890
-- The long pattern is (?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9]) where R7b had [0-9]{12,}; nothing else changes. A run is
-- digits with no digit on either side, so the 8-digit runs of the order numbers (YQ26100019) and every shorter run
-- stay as typed, and the Focus matcher (r7_focus_links_migration.sql) still finds YQ-2610-0019, YQ 2610 0019 and
-- YQ26100019. Masked text holds no run of 9 digits, so masking twice changes nothing. The parser masks with the same
-- two patterns (scripts/text_mask.py, re-exported by scripts/ingest.py: the loader and the API's transfer pairing);
-- tests/test_r10_long_number_mask.py holds every spelling below equal to it, and the self-check at the end proves on
-- the database that the SQL engine gives what the Python mask gives on a fixed list of invented samples.
-- Known limits, as scripts/text_mask.py says: grouped digits ("4111 1111 1111 1111": runs of 4), Arabic-Indic digits
-- ([0-9] is ASCII only) and runs of 8 digits or fewer (a local phone number typed without its country code) are not
-- masked.
--
-- 1. v_sales: r7_narration_mask_migration.sql's statement verbatim but the long pattern. From this release THIS file
--    owns v_sales (docs/MIGRATIONS.md): re-running r7_narration_mask_migration.sql would put the narrower mask back,
--    division_payment_migration.sql would unmask the narration altogether. CREATE OR REPLACE keeps the 31 columns
--    (names, order, types), v_sales_agent (select * from v_sales), the other views on v_sales and every grant;
--    anon / authenticated are revoked again (plain statements).
-- 2. The data step, where the r7 and r9 data steps ran: order_lines.narration, stock_movements.narration and the batch
--    importer's copies of both (ingest_stage / ingest_replaced rows of target order_lines or stock_movements), so
--    ingest_undo compares like with like and an undo cannot put a number back. Each statement is the r7 or the r9 one
--    verbatim but the long pattern. row_hash is NOT touched: it stays the hash of the raw cells, so every stored key
--    is what the next load computes. (As r9_stock_moves_migration.sql says, row_hash is made from the raw narration,
--    so a masked number can be worked back from it by whoever reads those rows: treat it as sensitive.)
--    BEFORE, the only way back: the release backup (yq_backup release r10, encrypted), which holds the numbers in
--    clear. The text cannot be put back from SQL; the Focus exports still carry the numbers, but the parser masks them
--    again on every load once the release code is in the main folder.
-- 3. The self-check: the samples, v_sales (31 columns, the r10 pattern and not R7b's), the grants, and no narration
--    left with a run of 9 digits or more in the tables and views above.
--
-- Never security_invoker: run_readonly_query runs as yq_readonly, which is granted views, not the tables beneath.

set lock_timeout = '2s';      -- a busy v_sales, order_lines or stock_movements makes the file fail fast, never queue

-- 1. v_sales -------------------------------------------------------------------------------------------------------
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
    -- r10: personal ID numbers masked (a 9-digit run, and every run of 10 digits or more; the last 3 digits kept).
    -- The guard skips the two regexp passes on every narration without a 9-digit stretch.
    case when ol.narration ~ '[0-9]{9}' then
      regexp_replace(
        regexp_replace(ol.narration, '(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])', '************\1', 'g'),
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
  'Focus Sales Day Book narration with personal ID numbers masked: a 9-digit run and every run of 10 or more digits keep only their last 3 digits (r10, scripts/r10_long_number_mask_migration.sql; R7b masked 9 and 15+). Order numbers such as YQ-2609-0019 are untouched.';

-- Unchanged grants, restated so a re-run can never widen them (plain statements, never in a DO block).
revoke all on table v_sales from anon, authenticated;
revoke all on table v_sales_agent from anon, authenticated;

-- 2. the data step -------------------------------------------------------------------------------------------------
-- order_lines: r7_narration_mask_data_migration.sql's statement, the long pattern widened
update order_lines
   set narration = regexp_replace(
         regexp_replace(narration, '(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])', '************\1', 'g'),
         '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')
 where narration ~ '[0-9]{9}'
   and narration is distinct from regexp_replace(
         regexp_replace(narration, '(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])', '************\1', 'g'),
         '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g');

-- stock_movements: r9_stock_moves_migration.sql's statement, the long pattern widened (row_hash never written)
update stock_movements
   set narration = regexp_replace(
         regexp_replace(narration, '(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])', '************\1', 'g'),
         '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')
 where narration ~ '[0-9]{9}'
   and narration is distinct from regexp_replace(
         regexp_replace(narration, '(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])', '************\1', 'g'),
         '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g');

-- the batch importer's copies of order lines (r7's block)
do $$
begin
  if to_regclass('public.ingest_stage') is not null then
    update ingest_stage
       set row = jsonb_set(row, '{narration}', to_jsonb(regexp_replace(
             regexp_replace(row ->> 'narration', '(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])', '************\1', 'g'),
             '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')))
     where target = 'order_lines' and row ->> 'narration' ~ '[0-9]{9}';
  end if;
  if to_regclass('public.ingest_replaced') is not null then
    update ingest_replaced
       set row = jsonb_set(row, '{narration}', to_jsonb(regexp_replace(
             regexp_replace(row ->> 'narration', '(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])', '************\1', 'g'),
             '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')))
     where target = 'order_lines' and row ->> 'narration' ~ '[0-9]{9}';
  end if;
end $$;

-- the batch importer's copies of stock rows (r9's block)
do $$
begin
  if to_regclass('public.ingest_stage') is not null then
    update ingest_stage
       set row = jsonb_set(row, '{narration}', to_jsonb(regexp_replace(
             regexp_replace(row ->> 'narration', '(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])', '************\1', 'g'),
             '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')))
     where target = 'stock_movements' and row ->> 'narration' ~ '[0-9]{9}';
  end if;
  if to_regclass('public.ingest_replaced') is not null then
    update ingest_replaced
       set row = jsonb_set(row, '{narration}', to_jsonb(regexp_replace(
             regexp_replace(row ->> 'narration', '(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])', '************\1', 'g'),
             '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')))
     where target = 'stock_movements' and row ->> 'narration' ~ '[0-9]{9}';
  end if;
end $$;

-- 3. self-check ----------------------------------------------------------------------------------------------------
-- The SQL mask (v_sales's expression, on each sample) gives what scripts/text_mask.py gives: every expected value
-- below is the Python mask's (tests/test_r10_long_number_mask.py checks each one). Every sample is invented. Then
-- v_sales, the grants, and no narration with a run of 9 digits or more, stored or shown.
do $$
declare
  s record;
  got text;
  n_cols int;
  typ text;
  def text;
begin
  for s in
    select * from (values
      (1,  'order YQ-2610-0019, YQ 2610 0019, YQ26100019', 'order YQ-2610-0019, YQ 2610 0019, YQ26100019'),
      (2,  'inv 12345678 ok', 'inv 12345678 ok'),
      (3,  'CPR 900000123', 'CPR ******123'),
      (4,  'id900000123x', 'id******123x'),
      (5,  'ph 1234567890', 'ph ************890'),
      (6,  'tel:12345678901/', 'tel:************901/'),
      (7,  'acct12345678901234end', 'acct************234end'),
      (8,  'card 123456789012345', 'card ************345'),
      (9,  'ref 12345678901234567890', 'ref ************890'),
      (10, 'a 900000123 b 12345678901 c 1234567 d YQ-2610-0019', 'a ******123 b ************901 c 1234567 d YQ-2610-0019'),
      (11, 'ph ************901 / ******123', 'ph ************901 / ******123'),
      (12, '4111 1111 1111 1111', '4111 1111 1111 1111'),
      (13, U&'cpr \0669\0660\0660\0660\0660\0660\0661\0662\0663', U&'cpr \0669\0660\0660\0660\0660\0660\0661\0662\0663'),
      (14, '', '')
    ) as v(i, raw, want)
  loop
    got := case when s.raw ~ '[0-9]{9}' then
             regexp_replace(
               regexp_replace(s.raw, '(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])', '************\1', 'g'),
               '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')
           else s.raw end;
    if got is distinct from s.want then
      raise exception 'r10_long_number_mask: sample % masks to %, expected % (the SQL mask is not the Python one)',
        s.i, got, s.want;
    end if;
  end loop;

  select count(*) into n_cols from pg_attribute
   where attrelid = 'public.v_sales'::regclass and attnum > 0 and not attisdropped;
  if n_cols <> 31 then
    raise exception 'r10_long_number_mask: v_sales has % columns, expected 31', n_cols;
  end if;
  select format_type(atttypid, atttypmod) into typ from pg_attribute
   where attrelid = 'public.v_sales'::regclass and attname = 'narration';
  if typ is distinct from 'text' then
    raise exception 'r10_long_number_mask: v_sales.narration is %, expected text', typ;
  end if;
  def := pg_get_viewdef('public.v_sales'::regclass);
  if position('[0-9]{7,}([0-9]{3})' in def) = 0 or position('[0-9]{12,}' in def) > 0 then
    raise exception 'r10_long_number_mask: v_sales.narration is not the r10 mask';
  end if;

  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name in ('v_sales', 'v_sales_agent')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r10_long_number_mask: v_sales / v_sales_agent must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and not (has_table_privilege('yq_readonly', 'public.v_sales', 'SELECT')
              and has_table_privilege('yq_readonly', 'public.v_sales_agent', 'SELECT')) then
    raise exception 'r10_long_number_mask: yq_readonly lost SELECT on v_sales / v_sales_agent';
  end if;

  if exists (select 1 from order_lines where narration ~ '[0-9]{9}') then
    raise exception 'r10_long_number_mask: order_lines.narration still carries a run of 9 digits or more';
  end if;
  if exists (select 1 from stock_movements where narration ~ '[0-9]{9}') then
    raise exception 'r10_long_number_mask: stock_movements.narration still carries a run of 9 digits or more';
  end if;
  if to_regclass('public.ingest_stage') is not null then
    if exists (select 1 from ingest_stage where target in ('order_lines', 'stock_movements')
                  and row ->> 'narration' ~ '[0-9]{9}') then
      raise exception 'r10_long_number_mask: ingest_stage still carries a narration with a run of 9 digits or more';
    end if;
  end if;
  if to_regclass('public.ingest_replaced') is not null then
    if exists (select 1 from ingest_replaced where target in ('order_lines', 'stock_movements')
                  and row ->> 'narration' ~ '[0-9]{9}') then
      raise exception 'r10_long_number_mask: ingest_replaced still carries a narration with a run of 9 digits or more';
    end if;
  end if;
  if exists (select 1 from v_sales where narration ~ '[0-9]{9}') then
    raise exception 'r10_long_number_mask: a narration in v_sales still carries a run of 9 digits or more';
  end if;
  if exists (select 1 from v_sales_agent where narration ~ '[0-9]{9}') then
    raise exception 'r10_long_number_mask: a narration in v_sales_agent still carries a run of 9 digits or more';
  end if;
  raise notice 'r10_long_number_mask: ok (the samples, v_sales with the r10 mask and 31 columns, the grants, no narration with a run of 9 digits or more)';
end $$;
