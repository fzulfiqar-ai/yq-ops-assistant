-- Release 2b (R9, 21-Oct-2026), migration M1: the stock moves views read from the stock ledger, and the
-- stock-ledger narration masked. Idempotent: CREATE OR REPLACE only, one data step that cannot be undone from SQL.
--   Rehearse: python -m scripts.apply_sql scripts/r9_stock_moves_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r9_stock_moves_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r9_stock_moves_reverse.sql (the two views only: the narration stays masked)
-- From the MAIN project folder only (a worktree finds the main folder's .env, which is production), BEFORE the API
-- deploy of release 2b: that API reads both views. scripts/r9_release.ps1 runs it in that order. The old API keeps
-- working after it: it reads the old columns of v_salesman_stock_recon, which keep their names, order and types.
--
-- 1. v_transfer_lines (new): every Stock Issue Voucher and Stock Receive Voucher row of the accessory items, the
--    input of the portal's pairing by contents (app/transfer_audit.py runs scripts/ops/pairing.py, the code /yq-moves
--    runs). Columns: voucher, vtype, date, wh, to_wh, item, qty_in, qty_out, value_in, value_out (received_* and
--    issued_* as stored) and narration as stored, which step 3 masks. An accessory is an item that has EVER moved in
--    the main store, as scripts/ops/items.accessory_items counts them (without the owner's never-an-accessory list,
--    which lives in his config). SELECT for yq_readonly (the API reads it through the read RPC); it is not on the
--    AI's allowlist (app/sql_validator.py) and never should be.
--
-- 2. v_salesman_stock_recon, rewritten from the stock ledger alone (accessory items only). The old body
--    (stock_transfer_migration.sql) took its rows from the people in v_sales, its sales from the Sales Day Book with
--    SIM included, its stock on hand from the stock_balance snapshot, and called every store whose name holds the
--    letters "sim" a SIM store, so a van of a rep whose name holds them dropped out of the vans. Now, per store:
--      rows               every store an accessory moved in, or was issued to; one row per name as the ledger spells it
--      on_hand_qty        the ledger balance: received minus issued, every voucher type (never stock_balance)
--      transferred_in     the issue legs aimed at the store (units, and BHD as the issue states it)
--      transferred_out    the issue legs that left it
--      received_qty       its own receive legs; pending_qty = issued to it minus received by it
--      sold_qty           its Sales Invoice units; customer_returns_qty its Sales Return units
--      shortage / excess  its Shortages in Stock / Excesses in Stocks units (the shortage value as before)
--      unexplained_qty    the old formula on these figures: transferred in - transferred out - sold - on hand
--      items_held         the items with a ledger balance above nought there
--      first / last_move  its first and last movement (its own rows and the issues aimed at it)
--    store_kind: the first rule that holds on the name folded the way scripts/ops/pairing.py StoreRules.canon folds
--    it (case and runs of spaces; a trailing ' <n>-<n>' code dropped; no aliases), in StoreRules.kind's order: main =
--    'Accessories Warehouse'; damage = 'Accessories Damage Warehouse'; sim = a name ending ' - SIM WH', or the SIM
--    Warehouse itself; van = a name ending ' - Acc WH'; outlet = Causeway or YQ Roadshow. Then the old view's own two
--    rules: modern_trade ('multi market') and facility (sample, giveaway, inventory, warehouse); else other. Never a
--    test for the letters "sim" anywhere in a name. salesman keeps its old meaning: the store name exactly as the
--    ledger writes it, ' - Acc WH' included, which is how v_sales.salesman_resolved spells a van (the old view took
--    it from there; the agents, the AI's schema and the portal read it, and some match it against v_sales);
--    warehouse_name, appended, holds the same name. warehouse_type keeps the old words (main -> hub; an outlet, or a
--    store no rule names, -> van, as the old view called every seller; the rest as store_kind) and is_van =
--    (warehouse_type = 'van'), as before.
--    The eleven old columns come first with their names, order and types (CREATE OR REPLACE VIEW refuses anything
--    else); nine are appended: warehouse_name, store_kind, received_qty, customer_returns_qty, excess_qty,
--    pending_qty, items_held, first_move, last_move. Readers: app/agents.py (the stock-recon and returns agents read
--    old columns), GET /stock/recon (SELECT *), the AI's allowlist (app/ai.py describes the old columns). Grants
--    restated: SELECT for yq_readonly, nothing for anon / authenticated. Nothing depends on the view.
--
-- 3. The narration data step. Staff type customers' CPR numbers (9 digits) and card / account numbers (15 digits and
--    more) into Focus narrations; the Stock_ledger export carries them on its Sales Invoice rows and stock_movements
--    kept them as typed (v_stock_transfers, on the AI's allowlist, shows the narration of the issue rows).
--    stock_movements.narration is masked with the SQL twin of scripts/ingest.py mask_personal_ids, the expression of
--    r7_narration_mask_data_migration.sql verbatim (a 9-digit run keeps its last 3 digits behind 6 stars, a run of 15+
--    behind 12; masking twice changes nothing), and so are the batch importer's copies of stock rows (ingest_stage /
--    ingest_replaced, target 'stock_movements'), so ingest_undo compares like with like and an undo cannot put a
--    number back. row_hash is NOT touched: from release 2b parse_stock masks the narration but still makes row_hash
--    from the raw cells, so every stored key (voucher + item + row_hash) stays what the next load computes and a
--    re-load of those dates reads unchanged. Order numbers such as YQ-2610-0019 survive (4- and 8-digit runs), so the
--    Focus matcher's tier 2 (r7_focus_links_migration.sql, the Stock Issue narrations) still finds them, and the
--    pairing's narration hash (scripts/ops/ledger.py masks before it hashes) is the same before and after.
--    BEFORE, the only way back: a backup taken before this file (the release's encrypted backup, or
--      python -m scripts.db_backup --tables stock_movements --out <a folder outside OneDrive and the repo>);
--    it holds the numbers in clear. Restore the column from it by hand on (voucher, item_name, row_hash) if it is ever
--    needed. Without it the TEXT cannot be put back: the Focus exports still carry the numbers, but the parser masks
--    them again on every load. Leaving this file out would not keep them either: from the 2b code on, every
--    Stock_ledger load replaces its date span with masked rows.
--    The mask covers the text, not the row: row_hash (the first 16 hex digits, 64 bits, of an md5 over the RAW cells,
--    the unmasked narration included) stays beside the masked text in stock_movements and in the ingest_stage /
--    ingest_replaced copies. Whoever can read those rows (the service role, a db_backup copy) can work a masked
--    number back from row_hash and the row's other stored fields: a 9-digit run shows 3 digits, which leaves a
--    million candidates. No view shows row_hash (v_transfer_lines and v_stock_transfers name their columns; the AI
--    and the portal never see it): treat it as sensitive. The real fix, a rehash and a reload, is a later release.
--
-- Never security_invoker: run_readonly_query runs as yq_readonly, which is granted views, not the tables beneath.

set lock_timeout = '2s';      -- a busy stock_movements makes the file fail fast, never queue

-- ── 1. v_transfer_lines ────────────────────────────────────────────────────────
create or replace view v_transfer_lines as
with main_names as (      -- the main store, however the ledger spells it (StoreRules.canon, no aliases)
  select n.wh
  from (select distinct warehouse_name as wh from stock_movements where warehouse_name is not null) n
  where regexp_replace(lower(btrim(regexp_replace(n.wh, '\s+', ' ', 'g'))), ' [0-9]+-[0-9]+$', '')
        = 'accessories warehouse'
),
acc as (                  -- the accessory items: every item that has EVER moved in the main store
  select distinct sm.item_name
  from stock_movements sm
  join main_names m on m.wh = sm.warehouse_name
)
select sm.voucher,
       sm.voucher_type        as vtype,
       sm.move_date           as date,
       sm.warehouse_name      as wh,
       sm.to_warehouse_name   as to_wh,
       sm.item_name           as item,
       sm.received_qty        as qty_in,
       sm.issued_qty          as qty_out,
       sm.received_value_bhd  as value_in,
       sm.issued_value_bhd    as value_out,
       sm.narration
from stock_movements sm
join acc on acc.item_name = sm.item_name
where sm.voucher_type in ('Stock Issue Voucher', 'Stock Receive Voucher')
  and sm.move_date is not null;

comment on view v_transfer_lines is
  'Stock Issue and Stock Receive Voucher rows of the accessory items (ever moved in the main store), for the pairing by contents (app/transfer_audit.py, scripts/ops/pairing.py). Narration masked as stored (r9_stock_moves_migration.sql). yq_readonly only; not on the AI allowlist.';

-- ── 2. v_salesman_stock_recon ──────────────────────────────────────────────────
create or replace view v_salesman_stock_recon as
with ledger_stores as (   -- every store the ledger names
  select warehouse_name as wh from stock_movements where warehouse_name is not null
  union
  select to_warehouse_name from stock_movements where to_warehouse_name is not null
),
folded as (               -- each name as StoreRules.canon compares it: case and spaces folded, a trailing code dropped
  select wh,
         regexp_replace(lower(btrim(regexp_replace(wh, '\s+', ' ', 'g'))), ' [0-9]+-[0-9]+$', '') as k
  from ledger_stores
),
kinds as (                -- the first rule that holds: StoreRules.kind's five, then the old view's two
  select wh,
         case
           when k = 'accessories warehouse'                    then 'main'
           when k = 'accessories damage warehouse'             then 'damage'
           when k like '% - sim wh' or k = 'sim warehouse'     then 'sim'
           when k like '% - acc wh'                            then 'van'
           when k in ('causeway', 'yq roadshow')               then 'outlet'
           when k like '%multi market%'                        then 'modern_trade'
           when k like '%sample%' or k like '%giveaway%'
             or k like '%inventory%' or k like '%warehouse%'   then 'facility'
           else 'other'
         end as store_kind
  from folded
),
acc as (                  -- the accessory items: every item that has EVER moved in the main store
  select distinct sm.item_name
  from stock_movements sm
  join kinds m on m.wh = sm.warehouse_name
  where m.store_kind = 'main'
),
mv as (                   -- the accessory ledger; a blank quantity is nought
  select sm.warehouse_name, sm.to_warehouse_name, sm.voucher_type, sm.item_name, sm.move_date,
         coalesce(sm.received_qty, 0) as q_in,
         coalesce(sm.issued_qty, 0)   as q_out,
         sm.received_qty, sm.issued_qty, sm.received_value_bhd, sm.issued_value_bhd
  from stock_movements sm
  join acc on acc.item_name = sm.item_name
),
stores as (               -- one row per store an accessory moved in, or was issued to
  select warehouse_name as wh from mv where warehouse_name is not null
  union
  select to_warehouse_name from mv where voucher_type = 'Stock Issue Voucher' and to_warehouse_name is not null
),
own as (                  -- what moved in the store itself
  select warehouse_name                                                         as wh,
         sum(q_in) - sum(q_out)                                                 as on_hand,
         sum(q_out) filter (where voucher_type = 'Stock Issue Voucher'
                              and to_warehouse_name is not null)                as tout_qty,
         sum(q_in) filter (where voucher_type = 'Stock Receive Voucher')        as rcv_qty,
         sum(q_out - q_in) filter (where voucher_type = 'Sales Invoice')        as sold_qty,
         sum(q_in - q_out) filter (where voucher_type = 'Sales Return')         as ret_qty,
         sum(coalesce(issued_qty, received_qty, 0))
           filter (where voucher_type = 'Shortages in Stock')                   as short_qty,
         sum(coalesce(issued_value_bhd, received_value_bhd, 0))
           filter (where voucher_type = 'Shortages in Stock')                   as short_val,
         sum(coalesce(received_qty, issued_qty, 0))
           filter (where voucher_type = 'Excesses in Stocks')                   as excess_qty,
         min(move_date)                                                         as first_move,
         max(move_date)                                                         as last_move
  from mv
  where warehouse_name is not null
  group by warehouse_name
),
tin as (                  -- the issue legs aimed at the store
  select to_warehouse_name                  as wh,
         sum(q_out)                         as qty,
         sum(coalesce(issued_value_bhd, 0)) as val,
         min(move_date)                     as first_move,
         max(move_date)                     as last_move
  from mv
  where voucher_type = 'Stock Issue Voucher' and to_warehouse_name is not null
  group by to_warehouse_name
),
held as (                 -- the items the store holds: a ledger balance above nought
  select b.wh, count(*) as items
  from (select warehouse_name as wh, item_name
        from mv
        where warehouse_name is not null
        group by warehouse_name, item_name
        having sum(q_in) - sum(q_out) > 0) b
  group by b.wh
)
select
  k.wh                                                                 as salesman,
  case k.store_kind
    when 'main'   then 'hub'
    when 'outlet' then 'van'
    when 'other'  then 'van'
    else k.store_kind
  end                                                                  as warehouse_type,
  round(coalesce(tin.qty, 0))::numeric                                 as transferred_in_qty,
  round(coalesce(tin.val, 0)::numeric, 3)                              as transferred_in_value_bhd,
  round(coalesce(o.tout_qty, 0))::numeric                              as transferred_out_qty,
  round(coalesce(o.sold_qty, 0))::numeric                              as sold_qty,
  round(coalesce(o.on_hand, 0))::numeric                               as on_hand_qty,
  round(coalesce(o.short_qty, 0))::numeric                             as shortage_qty,
  round(coalesce(o.short_val, 0)::numeric, 3)                          as shortage_value_bhd,
  round((coalesce(tin.qty, 0) - coalesce(o.tout_qty, 0))
        - coalesce(o.sold_qty, 0) - coalesce(o.on_hand, 0))::numeric   as unexplained_qty,
  (k.store_kind in ('van', 'outlet', 'other'))                         as is_van,
  k.wh                                                                 as warehouse_name,
  k.store_kind,
  round(coalesce(o.rcv_qty, 0))::numeric                               as received_qty,
  round(coalesce(o.ret_qty, 0))::numeric                               as customer_returns_qty,
  round(coalesce(o.excess_qty, 0))::numeric                            as excess_qty,
  round(coalesce(tin.qty, 0) - coalesce(o.rcv_qty, 0))::numeric        as pending_qty,
  coalesce(h.items, 0)::int                                            as items_held,
  least(o.first_move, tin.first_move)                                  as first_move,
  greatest(o.last_move, tin.last_move)                                 as last_move
from stores s
join kinds k      on k.wh   = s.wh
left join own o   on o.wh   = s.wh
left join tin     on tin.wh = s.wh
left join held h  on h.wh   = s.wh
order by shortage_value_bhd desc, unexplained_qty desc, warehouse_name;

comment on view v_salesman_stock_recon is
  'Per store, from the stock ledger alone (accessory items): issued to it, received, back out, sold, returns, counts, on hand (the ledger balance), pending, the old unexplained gap. Old 11 columns first; store kinds pinned to scripts/ops/pairing.py StoreRules.kind (r9_stock_moves_migration.sql).';
comment on column v_salesman_stock_recon.salesman is
  'The store name exactly as the ledger writes it (vans with their " - Acc WH"), the way v_sales.salesman_resolved spells it: the old meaning, kept. Equal to warehouse_name.';
comment on column v_salesman_stock_recon.on_hand_qty is
  'The ledger balance at the store: received minus issued over every voucher type, accessory items only. Never the stock_balance snapshot.';
comment on column v_salesman_stock_recon.store_kind is
  'main | damage | sim | van | outlet (StoreRules.kind, on the folded name) | modern_trade | facility (the old view''s rules) | other.';

-- ── grants (plain statements, never inside a DO block) ─────────────────────────
revoke all on table v_transfer_lines from anon, authenticated;
revoke all on table v_salesman_stock_recon from anon, authenticated;
grant select on v_transfer_lines to yq_readonly;
grant select on v_salesman_stock_recon to yq_readonly;

-- ── 3. the narration data step (r7_narration_mask_data_migration.sql's mask, on the stock ledger) ──
update stock_movements
   set narration = regexp_replace(
         regexp_replace(narration, '(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])', '************\1', 'g'),
         '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')
 where narration ~ '[0-9]{9}'
   and narration is distinct from regexp_replace(
         regexp_replace(narration, '(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])', '************\1', 'g'),
         '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g');

do $$
begin
  if to_regclass('public.ingest_stage') is not null then
    update ingest_stage
       set row = jsonb_set(row, '{narration}', to_jsonb(regexp_replace(
             regexp_replace(row ->> 'narration', '(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])', '************\1', 'g'),
             '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')))
     where target = 'stock_movements' and row ->> 'narration' ~ '[0-9]{9}';
  end if;
  if to_regclass('public.ingest_replaced') is not null then
    update ingest_replaced
       set row = jsonb_set(row, '{narration}', to_jsonb(regexp_replace(
             regexp_replace(row ->> 'narration', '(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])', '************\1', 'g'),
             '(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])', '******\1', 'g')))
     where target = 'stock_movements' and row ->> 'narration' ~ '[0-9]{9}';
  end if;
end $$;

-- ── self-check ─────────────────────────────────────────────────────────────────
-- The columns (the old eleven first, unchanged), the grants, the words, one row per store, the main store found,
-- and no stored stock narration left with a 9-digit or a 15+-digit run.
do $$
declare
  got text;
  want_recon constant text :=
    'salesman text, warehouse_type text, transferred_in_qty numeric, transferred_in_value_bhd numeric, '
    || 'transferred_out_qty numeric, sold_qty numeric, on_hand_qty numeric, shortage_qty numeric, '
    || 'shortage_value_bhd numeric, unexplained_qty numeric, is_van boolean, warehouse_name text, store_kind text, '
    || 'received_qty numeric, customer_returns_qty numeric, excess_qty numeric, pending_qty numeric, '
    || 'items_held integer, first_move date, last_move date';
  want_lines constant text :=
    'voucher text, vtype text, date date, wh text, to_wh text, item text, qty_in numeric, qty_out numeric, '
    || 'value_in numeric, value_out numeric, narration text';
begin
  select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum) into got
    from pg_attribute a
   where a.attrelid = 'public.v_salesman_stock_recon'::regclass and a.attnum > 0 and not a.attisdropped;
  if got is distinct from want_recon then
    raise exception 'r9_stock_moves: v_salesman_stock_recon is (%), expected (%)', got, want_recon;
  end if;
  select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum) into got
    from pg_attribute a
   where a.attrelid = 'public.v_transfer_lines'::regclass and a.attnum > 0 and not a.attisdropped;
  if got is distinct from want_lines then
    raise exception 'r9_stock_moves: v_transfer_lines is (%), expected (%)', got, want_lines;
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name in ('v_transfer_lines', 'v_salesman_stock_recon')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r9_stock_moves: v_transfer_lines / v_salesman_stock_recon must not be granted to anon/authenticated';
  end if;
  if not (has_table_privilege('yq_readonly', 'public.v_transfer_lines', 'SELECT')
          and has_table_privilege('yq_readonly', 'public.v_salesman_stock_recon', 'SELECT')) then
    raise exception 'r9_stock_moves: yq_readonly cannot read v_transfer_lines / v_salesman_stock_recon';
  end if;
  if exists (select 1 from stock_movements)
     and not exists (select 1 from v_salesman_stock_recon where store_kind = 'main') then
    raise exception 'r9_stock_moves: no movement in the main store (Accessories Warehouse): if Focus renamed it, both views and scripts/ops/pairing.py need the new name';
  end if;
  if exists (select 1 from v_salesman_stock_recon
              where warehouse_type not in ('hub', 'damage', 'sim', 'modern_trade', 'facility', 'van')
                 or store_kind not in ('main', 'damage', 'sim', 'van', 'outlet', 'modern_trade', 'facility', 'other')
                 or is_van is distinct from (warehouse_type = 'van')
                 or warehouse_name is null
                 or salesman is distinct from warehouse_name) then
    raise exception 'r9_stock_moves: a store with a kind or a warehouse_type outside the agreed words, or a salesman that is not its store name';
  end if;
  if (select count(*) <> count(distinct warehouse_name) from v_salesman_stock_recon) then
    raise exception 'r9_stock_moves: v_salesman_stock_recon has two rows for one store';
  end if;
  if exists (select 1 from stock_movements
              where narration ~ '(^|[^0-9])[0-9]{9}([^0-9]|$)' or narration ~ '[0-9]{15,}') then
    raise exception 'r9_stock_moves: stock_movements.narration still carries a 9-digit or a 15+-digit number';
  end if;
  if to_regclass('public.ingest_stage') is not null then
    if exists (select 1 from ingest_stage where target = 'stock_movements'
                  and (row ->> 'narration' ~ '(^|[^0-9])[0-9]{9}([^0-9]|$)' or row ->> 'narration' ~ '[0-9]{15,}')) then
      raise exception 'r9_stock_moves: ingest_stage still carries a 9-digit or a 15+-digit stock narration';
    end if;
  end if;
  if to_regclass('public.ingest_replaced') is not null then
    if exists (select 1 from ingest_replaced where target = 'stock_movements'
                  and (row ->> 'narration' ~ '(^|[^0-9])[0-9]{9}([^0-9]|$)' or row ->> 'narration' ~ '[0-9]{15,}')) then
      raise exception 'r9_stock_moves: ingest_replaced still carries a 9-digit or a 15+-digit stock narration';
    end if;
  end if;
  raise notice 'r9_stock_moves: ok (v_transfer_lines, v_salesman_stock_recon with the old 11 columns first, grants, stock narration masked)';
end $$;
