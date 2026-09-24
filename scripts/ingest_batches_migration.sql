-- Transactional batch importer (24-Sep-2026, trust plan M7 / release R2a). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/ingest_batches_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/ingest_batches_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/ingest_batches_reverse.sql
--
-- Why: the old Focus refresh (scripts/refresh.py) upserts straight into the live tables, one
-- PostgREST call at a time, and verifies AFTERWARDS. A bad export is live before anyone sees it,
-- a deleted or re-lined Focus invoice persists (audit D8), and there is no way back except a
-- backup restore. Here the parsed rows are staged (ingest_stage), previewed, and then applied by
-- ONE plpgsql transaction (ingest_commit) that
--   * takes an advisory lock, so two commits can never interleave;
--   * per target, replaces the file's own scope -- sales by invoice-date span, the stock ledger by
--     date span, stock_balance per (as_of_date, warehouse), ar_ageing per as_of_date,
--     product_profitability per report_date, selling_prices by the loader's Focus-book snapshot
--     rule (voids stale rows, un-voids keys the file carries again);
--   * copies every row it deletes, voids or changes into ingest_replaced BEFORE touching it, and
--     records which live row each staged row became (ingest_stage.action / row_id);
--   * asserts the totals the preview promised -- per-action counts, and the row count and money
--     sums of the scope after the load -- and RAISES on any mismatch, so everything rolls back.
-- ingest_undo reverses a committed batch (deletes what it inserted, restores what it changed,
-- re-inserts what it removed) and refuses when a later committed batch touched the same scope.
--
-- Unchanged rows are never rewritten (same id, same imported_at). Backfilled columns the
-- importer does not own (product_id, order_id, customer_id, voided_at/void_reason except through
-- the snapshot rule) are never overwritten. Nothing here grants anything to anon/authenticated;
-- EXECUTE on the two RPCs goes to service_role only (the API's key).

-- ── 1. tables ─────────────────────────────────────────────────────────────────
create table if not exists ingest_batches (
  id            bigint generated always as identity primary key,
  status        text        not null default 'parsing'
                check (status in ('parsing', 'previewed', 'committed', 'rejected', 'undone', 'failed')),
  files         jsonb       not null default '[]'::jsonb,   -- [{file, target, rows}]
  summary       jsonb       not null default '{}'::jsonb,   -- the preview (per target totals, actions, scope) + commit result
  exceptions    jsonb       not null default '[]'::jsonb,   -- [{code, severity, target, message, count, items}]
  created_by    text,
  created_at    timestamptz not null default now(),
  committed_by  text,
  committed_at  timestamptz,
  undone_by     text,
  undone_at     timestamptz
);

create table if not exists ingest_stage (
  id        bigint generated always as identity primary key,
  batch_id  bigint not null references ingest_batches(id) on delete restrict,
  target    text   not null,
  row       jsonb  not null,
  action    text,            -- set by ingest_commit: inserted | updated | revived | unchanged
  row_id    bigint           -- the live row this staged row became or matched
);
create index if not exists ingest_stage_batch_idx on ingest_stage (batch_id, target);

create table if not exists ingest_replaced (
  id        bigint generated always as identity primary key,
  batch_id  bigint not null references ingest_batches(id) on delete restrict,
  target    text   not null,
  action    text   not null check (action in ('deleted', 'updated', 'revived', 'voided')),
  row       jsonb  not null   -- the whole live row (incl. id) as it was before the batch touched it
);
create index if not exists ingest_replaced_batch_idx on ingest_replaced (batch_id, target);

alter table ingest_batches  enable row level security;   -- service role only; no policies for API roles
alter table ingest_stage    enable row level security;
alter table ingest_replaced enable row level security;
revoke all on ingest_batches, ingest_stage, ingest_replaced from anon, authenticated;

comment on table ingest_batches  is 'One Focus drop: parsed -> previewed -> committed (ingest_commit) or rejected; undone by ingest_undo. Never deleted.';
comment on table ingest_stage    is 'Parsed rows of a batch, one jsonb row per target row. action/row_id say what ingest_commit did with each.';
comment on table ingest_replaced is 'Live rows a batch deleted, voided or changed, copied verbatim BEFORE the change, so ingest_undo can put them back.';

-- ── 2. read path for the preview (the API diff runs through the yq_readonly RPC) ──
-- The preview joins ingest_stage against the live tables inside run_readonly_query_params, which
-- executes as yq_readonly. That role could read stock_balance and selling_prices already; the
-- other Focus tables (and the three tables above) need the same SELECT grant + read policy.
-- Read-only role, additive; audit_grants only polices anon/authenticated.
do $$
declare t text;
begin
  foreach t in array array[
    'ingest_batches', 'ingest_stage', 'ingest_replaced',
    'orders', 'order_lines', 'stock_movements', 'ledger_entries', 'ar_ageing', 'product_profitability',
    'product_aliases', 'purchase_costs'
  ] loop
    if to_regclass('public.' || t) is null then
      continue;
    end if;
    execute format('grant select on %I to yq_readonly', t);
    execute format('drop policy if exists %I on %I', t || '_yq_readonly_read', t);
    execute format('create policy %I on %I for select to yq_readonly using (true)', t || '_yq_readonly_read', t);
  end loop;
end $$;

-- ── 3. helpers (internal; EXECUTE revoked from the API roles) ──────────────────
-- Column list of a public table, in attribute order, with the SQL type name to cast to.
create or replace function _ingest_cols(p_table text)
returns table(col text, typ text, attnum smallint)
language sql stable set search_path = public as $$
  select a.attname::text, format_type(a.atttypid, a.atttypmod), a.attnum
    from pg_attribute a
   where a.attrelid = ('public.' || quote_ident(p_table))::regclass
     and a.attnum > 0 and not a.attisdropped
   order by a.attnum
$$;

-- SQL text that pulls column p_col out of the jsonb expression p_src as type p_typ.
create or replace function _ingest_cast(p_src text, p_col text, p_typ text)
returns text language sql immutable as $$
  select case when p_typ = 'jsonb' then format('(%s->%L)', p_src, p_col)
              when p_typ = 'json'  then format('(%s->%L)::json', p_src, p_col)
              else format('(%s->>%L)::%s', p_src, p_col, p_typ) end
$$;

-- NULL-safe key equality the planner can hash/merge-join. `is not distinct from` is not an
-- operator, so the 36k-row stock ledger diff ran as a nested loop for minutes; coalescing both
-- sides to a sentinel of the column's type keeps NULL = NULL (the price-book key has NULLs) and
-- gets a merge join in seconds. Mirrored by _eq() in app/ingest_batch.py.
create or replace function _ingest_eq(a text, b text, typ text)
returns text language sql immutable as $$
  select case
    when typ = 'date' then format('coalesce(%s, ''0001-01-01''::date) = coalesce(%s, ''0001-01-01''::date)', a, b)
    when typ like 'timestamp%%' then format('coalesce(%s, ''0001-01-01''::timestamptz) = coalesce(%s, ''0001-01-01''::timestamptz)', a, b)
    when typ in ('integer', 'bigint', 'smallint') then format('coalesce(%s, -9223372036854775807) = coalesce(%s, -9223372036854775807)', a, b)
    when typ in ('numeric', 'double precision', 'real') then format('coalesce(%s, ''-1e30''::numeric) = coalesce(%s, ''-1e30''::numeric)', a, b)
    else format('coalesce(%s::text, '''') = coalesce(%s::text, '''')', a, b) end
$$;

-- The one place the per-target rules live (mirrored by app/ingest_batch.py TARGETS; the R2
-- tests assert both sides agree). keys = natural key (the upsert key of the old loader);
-- scope = what a file is the whole truth for; money = the sums asserted after the load.
create or replace function _ingest_target_cfg(p_target text)
returns jsonb language sql immutable as $$
  select case p_target
    when 'orders' then
      '{"keys":["invoice_no"],"scope":"span","span_col":"order_date","money":["gross_bhd"]}'::jsonb
    when 'order_lines' then
      '{"keys":["invoice_no","line_no"],"scope":"span","span_col":"line_date","money":["gross_bhd","taxable_bhd"]}'::jsonb
    when 'stock_movements' then
      '{"keys":["voucher","item_name","row_hash"],"scope":"span","span_col":"move_date","money":["received_qty","issued_qty"]}'::jsonb
    when 'ledger_entries' then
      '{"keys":["account","voucher","row_hash"],"scope":"span","span_col":"entry_date","money":["debit_bhd","credit_bhd"]}'::jsonb
    when 'stock_balance' then
      '{"keys":["item_name","warehouse_name","as_of_date"],"scope":"partition","partition":["as_of_date","warehouse_name"],"money":["net_qty","total_value_bhd"]}'::jsonb
    when 'ar_ageing' then
      '{"keys":["account","as_of_date"],"scope":"partition","partition":["as_of_date"],"money":["balance_bhd"]}'::jsonb
    when 'product_profitability' then
      '{"keys":["item_name","report_date"],"scope":"partition","partition":["report_date"],"money":["gross_bhd","net_amount_bhd"]}'::jsonb
    when 'selling_prices' then
      '{"keys":["sku_code","price_book","customer_code","warehouse_name","start_date"],"scope":"book","partition":["price_book"],"money":["rate_bhd"]}'::jsonb
    else null end
$$;

-- Two commit scopes overlap? (used by ingest_undo to refuse when a later batch touched the span)
create or replace function _ingest_scope_overlap(a jsonb, b jsonb)
returns boolean language sql immutable as $$
  select case
    when a is null or b is null or a->>'kind' is distinct from b->>'kind' then false
    when a->>'kind' = 'span' then
      not ((a->>'to')::date < (b->>'from')::date or (b->>'to')::date < (a->>'from')::date)
    when a->>'kind' = 'partition' then
      exists (select 1 from jsonb_array_elements(a->'partitions') x join jsonb_array_elements(b->'partitions') y on x = y)
    when a->>'kind' = 'book' then
      exists (select 1 from jsonb_array_elements(a->'books') x join jsonb_array_elements(b->'books') y on x = y)
    else true end
$$;

-- Apply one target of a batch. Returns {actions, scope, rows, sums}. Raises on any mismatch
-- with p_exp = {rows, sums:{col: "n.nnn"}, actions:{inserted, updated, unchanged, deleted|voided[, revived]}}.
create or replace function _ingest_apply_target(p_batch_id bigint, p_target text, p_exp jsonb)
returns jsonb language plpgsql set search_path = public as $$
declare
  cfg        jsonb := _ingest_target_cfg(p_target);
  keys       text[];
  protected  text[] := array['id', 'imported_at', 'created_at', 'updated_at', 'product_id', 'order_id',
                             'customer_id', 'voided_at', 'void_reason'];
  first_row  jsonb;
  cols       text[] := '{}';   -- payload columns (file keys that are real columns, minus protected)
  sets       text[] := '{}';   -- c = cast(s.row->>c)
  vals       text[] := '{}';   -- cast(s.row->>c)
  diffs      text[] := '{}';   -- L.c is distinct from cast(...)
  keym_l_s   text[] := '{}';   -- L.k is not distinct from cast(s.row->>k)
  partm      text[] := '{}';   -- L.p is not distinct from cast(sp.row->>p), partition columns only
  keym_i_st  text[] := '{}';   -- ins.k is not distinct from cast(st.row->>k)
  parts      text[] := array(select jsonb_array_elements_text(cfg->'partition'));
  c          record;
  v_scope    text;             -- predicate on alias L: the rows this file is the whole truth for
  v_after    text;             -- predicate on alias L for the post-load assertion
  v_from     date; v_to date;
  v_parts    jsonb;
  v_books    text[]; v_files text[];
  v_scope_json jsonb;
  is_book    boolean := (cfg->>'scope') = 'book';
  n_dup      bigint;
  n_removed  bigint := 0; n_updated bigint := 0; n_revived bigint := 0; n_unch bigint := 0; n_ins bigint := 0;
  n_stale    bigint := 0; n_null bigint;
  v_rows     bigint; v_sums jsonb := '{}'::jsonb; v_sum numeric; m text;
  v_actions  jsonb;
  k          text; want text; got text;
  sql        text;
  t0         timestamptz := clock_timestamp();
  timings    jsonb := '{}'::jsonb;
begin
  if cfg is null then
    raise exception 'ingest: target % is not importable by batch', p_target;
  end if;
  if to_regclass('public.' || p_target) is null then
    raise exception 'ingest: table % does not exist', p_target;
  end if;
  keys := array(select jsonb_array_elements_text(cfg->'keys'));

  select row into first_row from ingest_stage where batch_id = p_batch_id and target = p_target limit 1;
  if first_row is null then
    raise exception 'ingest: batch % has no staged rows for %', p_batch_id, p_target;
  end if;

  -- payload / key column expressions from the real column types
  for c in select col, typ from _ingest_cols(p_target) loop
    if c.col = any(keys) then
      keym_l_s  := keym_l_s  || _ingest_eq(format('L.%I', c.col), _ingest_cast('s.row', c.col, c.typ), c.typ);
      keym_i_st := keym_i_st || _ingest_eq(format('ins.%I', c.col), _ingest_cast('st.row', c.col, c.typ), c.typ);
    end if;
    if c.col = any(parts) then
      partm := partm || _ingest_eq(format('L.%I', c.col), _ingest_cast('sp.row', c.col, c.typ), c.typ);
    end if;
    if first_row ? c.col and not (c.col = any(protected)) then
      cols  := cols  || c.col;
      sets  := sets  || format('%I = %s', c.col, _ingest_cast('s.row', c.col, c.typ));
      vals  := vals  || _ingest_cast('s.row', c.col, c.typ);
      -- source_file changes on every re-export and says nothing about the row, so it never
      -- makes a row "updated" -- except in a price book, where the snapshot rule reads it
      if c.col <> 'source_file' or is_book then
        diffs := diffs || format('L.%I is distinct from %s', c.col, _ingest_cast('s.row', c.col, c.typ));
      end if;
    end if;
  end loop;
  if array_length(keym_l_s, 1) is distinct from array_length(keys, 1) then
    raise exception 'ingest: % key columns % not all present on the table', p_target, keys;
  end if;
  if array_length(cols, 1) is null then
    raise exception 'ingest: % staged rows carry no importable columns', p_target;
  end if;

  -- staged keys must be unique (the loader''s dedupe rules run before staging; this is the guard)
  execute format(
    'select count(*) - count(distinct jsonb_build_array(%s)) from ingest_stage where batch_id = %s and target = %L',
    (select string_agg(format('row->%L', kk), ', ') from unnest(keys) kk), p_batch_id, p_target) into n_dup;
  if n_dup > 0 then
    raise exception 'ingest: % staged rows repeat % natural key(s); dedupe before staging', p_target, n_dup;
  end if;

  -- the scope this file is the whole truth for
  if cfg->>'scope' = 'span' then
    execute format('select min((row->>%L)::date), max((row->>%L)::date) from ingest_stage where batch_id = %s and target = %L',
                   cfg->>'span_col', cfg->>'span_col', p_batch_id, p_target) into v_from, v_to;
    if v_from is null then
      raise exception 'ingest: % staged rows carry no % dates', p_target, cfg->>'span_col';
    end if;
    v_scope := format('L.%I between %L::date and %L::date', cfg->>'span_col', v_from, v_to);
    v_after := v_scope;
    v_scope_json := jsonb_build_object('kind', 'span', 'col', cfg->>'span_col', 'from', v_from, 'to', v_to);
  elsif cfg->>'scope' = 'partition' then
    if array_length(partm, 1) is distinct from array_length(parts, 1) then
      raise exception 'ingest: % partition columns % not all present on the table', p_target, parts;
    end if;
    v_scope := format('exists (select 1 from ingest_stage sp where sp.batch_id = %s and sp.target = %L and %s)',
                      p_batch_id, p_target, array_to_string(partm, ' and '));
    v_after := v_scope;
    execute format('select jsonb_agg(p order by p) from (select distinct jsonb_build_array(%s) p from ingest_stage where batch_id = %s and target = %L) q',
                   (select string_agg(format('row->>%L', pc), ', ') from jsonb_array_elements_text(cfg->'partition') pc),
                   p_batch_id, p_target) into v_parts;
    v_scope_json := jsonb_build_object('kind', 'partition', 'cols', cfg->'partition', 'partitions', v_parts);
  else  -- book: live Focus rows of the same book that came from a DIFFERENT Focus export
    execute format('select array_agg(distinct row->>''price_book''), array_agg(distinct row->>''source_file'') from ingest_stage where batch_id = %s and target = %L',
                   p_batch_id, p_target) into v_books, v_files;
    if v_books is null or array_position(v_books, null) is not null then
      raise exception 'ingest: selling_prices staged rows without a price_book';
    end if;
    v_scope := format($f$L.price_book = any(%L::text[]) and L.voided_at is null and L.source_file <> all(%L::text[])
                        and lower(coalesce(L.source_file, '')) like (case L.price_book when 'MA_base' then 'masellingpricebook%%'
                                                                                       when 'modern_trade' then 'moderntradesellerbook%%' end)$f$,
                      v_books, v_files);
    v_after := format('L.price_book = any(%L::text[]) and L.voided_at is null and L.source_file = any(%L::text[])', v_books, v_files);
    v_scope_json := jsonb_build_object('kind', 'book', 'books', to_jsonb(v_books), 'files', to_jsonb(v_files));
  end if;

  -- 1. rows in scope the file no longer carries: copy to ingest_replaced, then delete (or void)
  if is_book then
    sql := format($f$
      with pre as (select L.id, to_jsonb(L) as row from %I L where %s
                    and not exists (select 1 from ingest_stage s where s.batch_id = %s and s.target = %L and %s) for update),
           ins as (insert into ingest_replaced (batch_id, target, action, row) select %s, %L, 'voided', row from pre)
      update %I L set voided_at = now(), void_reason = %L where L.id in (select id from pre)$f$,
      p_target, v_scope, p_batch_id, p_target, array_to_string(keym_l_s, ' and '),
      p_batch_id, p_target, p_target,
      format('superseded by %s (Focus book snapshot: key not in the newest export; batch %s)', array_to_string(v_files, ', '), p_batch_id));
  else
    sql := format($f$
      with pre as (select L.id, to_jsonb(L) as row from %I L where %s
                    and not exists (select 1 from ingest_stage s where s.batch_id = %s and s.target = %L and %s) for update),
           ins as (insert into ingest_replaced (batch_id, target, action, row) select %s, %L, 'deleted', row from pre)
      delete from %I L where L.id in (select id from pre)$f$,
      p_target, v_scope, p_batch_id, p_target, array_to_string(keym_l_s, ' and '),
      p_batch_id, p_target, p_target);
  end if;
  execute sql;
  get diagnostics n_removed = row_count;
  timings := timings || jsonb_build_object('remove_s', round(extract(epoch from clock_timestamp() - t0)::numeric, 2));
  t0 := clock_timestamp();

  -- 2. matched rows whose payload differs (or, for a price book, that were voided): save, then update
  sql := format($f$
    with pre as (select L.id, to_jsonb(L) as row, %s as was_voided
                   from %I L join ingest_stage s on s.batch_id = %s and s.target = %L and %s
                  where (%s) %s for update of L),
         ins as (insert into ingest_replaced (batch_id, target, action, row)
                 select %s, %L, case when was_voided then 'revived' else 'updated' end, row from pre),
         upd as (update %I L set %s %s
                   from ingest_stage s
                  where s.batch_id = %s and s.target = %L and %s and L.id in (select id from pre)
                 returning L.id, s.id as stage_id)
    update ingest_stage st set action = case when p.was_voided then 'revived' else 'updated' end, row_id = upd.id
      from upd join pre p on p.id = upd.id where st.id = upd.stage_id$f$,
    case when is_book then 'L.voided_at is not null' else 'false' end,
    p_target, p_batch_id, p_target, array_to_string(keym_l_s, ' and '),
    array_to_string(diffs, ' or '), case when is_book then 'or L.voided_at is not null' else '' end,
    p_batch_id, p_target,
    p_target, array_to_string(sets, ', '), case when is_book then ', voided_at = null, void_reason = null' else '' end,
    p_batch_id, p_target, array_to_string(keym_l_s, ' and '));
  execute sql;
  execute format('select count(*) filter (where action = ''updated''), count(*) filter (where action = ''revived'') from ingest_stage where batch_id = %s and target = %L',
                 p_batch_id, p_target) into n_updated, n_revived;
  timings := timings || jsonb_build_object('update_s', round(extract(epoch from clock_timestamp() - t0)::numeric, 2));
  t0 := clock_timestamp();

  -- 3. matched rows that are identical: untouched, only noted
  sql := format($f$
    update ingest_stage st set action = 'unchanged', row_id = L.id
      from %I L
     where st.batch_id = %s and st.target = %L and st.action is null
       and %s$f$,
    p_target, p_batch_id, p_target, replace(array_to_string(keym_l_s, ' and '), 's.row', 'st.row'));
  execute sql;
  get diagnostics n_unch = row_count;
  timings := timings || jsonb_build_object('unchanged_s', round(extract(epoch from clock_timestamp() - t0)::numeric, 2));
  t0 := clock_timestamp();

  -- 4. everything else is new
  sql := format($f$
    with ins as (insert into %I (%s)
                 select %s from ingest_stage s where s.batch_id = %s and s.target = %L and s.action is null
                 returning %s)
    update ingest_stage st set action = 'inserted', row_id = ins.id
      from ins where st.batch_id = %s and st.target = %L and st.action is null and %s$f$,
    p_target, (select string_agg(quote_ident(x), ', ') from unnest(cols) x),
    array_to_string(vals, ', '), p_batch_id, p_target,
    'id, ' || (select string_agg(quote_ident(x), ', ') from unnest(keys) x),
    p_batch_id, p_target, array_to_string(keym_i_st, ' and '));
  execute sql;
  get diagnostics n_ins = row_count;
  timings := timings || jsonb_build_object('insert_s', round(extract(epoch from clock_timestamp() - t0)::numeric, 2));
  t0 := clock_timestamp();

  execute format('select count(*) from ingest_stage where batch_id = %s and target = %L and action is null', p_batch_id, p_target) into n_null;
  if n_null > 0 then
    raise exception 'ingest: % staged rows of % were neither matched nor inserted', n_null, p_target;
  end if;

  -- 5. assert what the preview promised
  v_actions := jsonb_build_object('inserted', n_ins, 'updated', n_updated, 'unchanged', n_unch,
                                  case when is_book then 'voided' else 'deleted' end, n_removed)
               || case when is_book then jsonb_build_object('revived', n_revived) else '{}'::jsonb end;
  for k in select jsonb_object_keys(p_exp->'actions') loop
    want := p_exp->'actions'->>k;
    got  := v_actions->>k;
    if got is null then
      raise exception 'ingest: % expected an action count for %, which this target does not produce', p_target, k;
    end if;
    if want is distinct from got then
      raise exception 'ingest: % % mismatch -- preview said %, the load produced % (rolled back)', p_target, k, want, got;
    end if;
  end loop;
  if not (p_exp->'actions' ? 'inserted' and p_exp->'actions' ? 'updated' and p_exp->'actions' ? 'unchanged'
          and p_exp->'actions' ? (case when is_book then 'voided' else 'deleted' end)) then
    raise exception 'ingest: % expected actions incomplete: %', p_target, p_exp->'actions';
  end if;

  execute format('select count(*) from %I L where %s', p_target, v_after) into v_rows;
  if v_rows is distinct from (p_exp->>'rows')::bigint then
    raise exception 'ingest: % rows in scope after load = %, the file has % (rolled back)', p_target, v_rows, p_exp->>'rows';
  end if;
  for m in select jsonb_array_elements_text(cfg->'money') loop
    execute format('select round(coalesce(sum(L.%I), 0)::numeric, 3) from %I L where %s', m, p_target, v_after) into v_sum;
    v_sums := v_sums || jsonb_build_object(m, v_sum::text);
    if p_exp->'sums'->>m is null then
      raise exception 'ingest: % expected sum for % missing', p_target, m;
    end if;
    if v_sum <> (p_exp->'sums'->>m)::numeric then
      raise exception 'ingest: % sum(%) after load = %, the file says % (rolled back)', p_target, m, v_sum, p_exp->'sums'->>m;
    end if;
  end loop;
  if is_book then
    execute format('select count(*) from %I L where %s', p_target, v_scope) into n_stale;
    if n_stale <> 0 then
      raise exception 'ingest: % live Focus rows of % are not in the newest export after the void', n_stale, v_books;
    end if;
  end if;

  timings := timings || jsonb_build_object('assert_s', round(extract(epoch from clock_timestamp() - t0)::numeric, 2));
  return jsonb_build_object('actions', v_actions, 'scope', v_scope_json, 'rows', v_rows, 'sums', v_sums,
                            'columns', to_jsonb(cols), 'timings', timings);
end $$;

-- ── 4. the two RPCs ────────────────────────────────────────────────────────────
-- p_expected = {"actor": email, "targets": {target: {rows, sums, actions}}}
create or replace function ingest_commit(p_batch_id bigint, p_expected jsonb)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  b        ingest_batches%rowtype;
  t        text;
  res      jsonb := '{}'::jsonb;
  missing  text[];
begin
  perform pg_advisory_xact_lock(hashtext('yq_ingest_commit'));
  select * into b from ingest_batches where id = p_batch_id for update;
  if not found then
    raise exception 'ingest_commit: batch % not found', p_batch_id;
  end if;
  if b.status <> 'previewed' then
    raise exception 'ingest_commit: batch % is %, only a previewed batch can be committed', p_batch_id, b.status;
  end if;
  if p_expected is null or jsonb_typeof(p_expected->'targets') <> 'object' then
    raise exception 'ingest_commit: expected totals missing';
  end if;
  select array_agg(x) into missing
    from jsonb_object_keys(p_expected->'targets') x
   where not exists (select 1 from ingest_stage s where s.batch_id = p_batch_id and s.target = x);
  if missing is not null then
    raise exception 'ingest_commit: expected totals name targets with no staged rows: %', missing;
  end if;
  -- Planner hygiene: the batch's rows were just inserted, so ingest_stage has no statistics for
  -- them and the planner would nest-loop the 36k-row ledger join for minutes (measured 466 s).
  -- Fresh statistics plus no nested loops for this transaction keep every step set-based.
  analyze ingest_stage;
  perform set_config('enable_nestloop', 'off', true);
  for t in select distinct target from ingest_stage where batch_id = p_batch_id order by 1 loop
    if p_expected->'targets'->t is null then
      raise exception 'ingest_commit: no expected totals for %', t;
    end if;
    res := res || jsonb_build_object(t, _ingest_apply_target(p_batch_id, t, p_expected->'targets'->t));
  end loop;
  update ingest_batches
     set status = 'committed', committed_by = p_expected->>'actor', committed_at = now(),
         summary = summary || jsonb_build_object('commit', res)
   where id = p_batch_id;
  if to_regclass('public.audit_log') is not null then
    insert into audit_log (user_email, event, question, detail)
    values (coalesce(p_expected->>'actor', 'ingest_commit'), 'ingest.batch_commit', 'batch ' || p_batch_id,
            jsonb_build_object('batch_id', p_batch_id, 'result', res));
  end if;
  return jsonb_build_object('batch_id', p_batch_id, 'status', 'committed', 'targets', res);
end $$;

create or replace function ingest_undo(p_batch_id bigint, p_actor text default null)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  b        ingest_batches%rowtype;
  later    record;
  t        text;
  c        record;
  sets     text[]; cols text[]; vals text[];
  n_del    bigint; n_res bigint; n_back bigint; want bigint;
  res      jsonb := '{}'::jsonb;
begin
  perform pg_advisory_xact_lock(hashtext('yq_ingest_commit'));
  select * into b from ingest_batches where id = p_batch_id for update;
  if not found then
    raise exception 'ingest_undo: batch % not found', p_batch_id;
  end if;
  if b.status <> 'committed' then
    raise exception 'ingest_undo: batch % is %, only a committed batch can be undone', p_batch_id, b.status;
  end if;
  perform set_config('enable_nestloop', 'off', true);
  -- refuse when a later committed batch touched the same scope of any target
  for later in select id, summary from ingest_batches where id > p_batch_id and status = 'committed' order by id loop
    for t in select jsonb_object_keys(b.summary->'commit') loop
      if _ingest_scope_overlap(b.summary->'commit'->t->'scope', later.summary->'commit'->t->'scope') then
        raise exception 'ingest_undo: batch % touched the same % scope after batch %; undo that batch first', later.id, t, p_batch_id;
      end if;
    end loop;
  end loop;

  for t in select distinct target from ingest_stage where batch_id = p_batch_id order by 1 loop
    if to_regclass('public.' || t) is null then
      raise exception 'ingest_undo: table % does not exist', t;
    end if;
    sets := '{}'; cols := '{}'; vals := '{}';
    for c in select col, typ from _ingest_cols(t) loop
      cols := cols || quote_ident(c.col);
      vals := vals || _ingest_cast('r.row', c.col, c.typ);
      if c.col <> 'id' then
        sets := sets || format('%I = %s', c.col, _ingest_cast('r.row', c.col, c.typ));
      end if;
    end loop;

    -- a. rows the batch inserted go away
    select count(*) into want from ingest_stage where batch_id = p_batch_id and target = t and action = 'inserted';
    execute format('delete from %I L where L.id in (select row_id from ingest_stage where batch_id = %s and target = %L and action = ''inserted'')',
                   t, p_batch_id, t);
    get diagnostics n_del = row_count;
    if n_del <> want then
      raise exception 'ingest_undo: % inserted % rows into %, only % are still there; something else changed them', p_batch_id, want, t, n_del;
    end if;
    -- b. rows it changed, un-voided or voided come back exactly as they were
    select count(*) into want from ingest_replaced where batch_id = p_batch_id and target = t and action in ('updated', 'revived', 'voided');
    execute format($f$update %I L set %s from ingest_replaced r
                     where r.batch_id = %s and r.target = %L and r.action in ('updated', 'revived', 'voided')
                       and L.id = (r.row->>'id')::bigint$f$, t, array_to_string(sets, ', '), p_batch_id, t);
    get diagnostics n_res = row_count;
    if n_res <> want then
      raise exception 'ingest_undo: % changed % rows of %, only % are still there; something else changed them', p_batch_id, want, t, n_res;
    end if;
    -- c. rows it deleted are re-inserted with their original ids
    select count(*) into want from ingest_replaced where batch_id = p_batch_id and target = t and action = 'deleted';
    execute format($f$insert into %I (%s) overriding system value
                     select %s from ingest_replaced r where r.batch_id = %s and r.target = %L and r.action = 'deleted'$f$,
                   t, array_to_string(cols, ', '), array_to_string(vals, ', '), p_batch_id, t);
    get diagnostics n_back = row_count;
    if n_back <> want then
      raise exception 'ingest_undo: could not put back all % deleted rows of %', want, t;
    end if;
    res := res || jsonb_build_object(t, jsonb_build_object('removed_inserts', n_del, 'restored', n_res, 'reinserted', n_back));
  end loop;

  update ingest_batches
     set status = 'undone', undone_by = p_actor, undone_at = now(),
         summary = summary || jsonb_build_object('undo', res)
   where id = p_batch_id;
  if to_regclass('public.audit_log') is not null then
    insert into audit_log (user_email, event, question, detail)
    values (coalesce(p_actor, 'ingest_undo'), 'ingest.batch_undo', 'batch ' || p_batch_id,
            jsonb_build_object('batch_id', p_batch_id, 'result', res));
  end if;
  return jsonb_build_object('batch_id', p_batch_id, 'status', 'undone', 'targets', res);
end $$;

-- The preview's diff runs through the yq_readonly RPC right after the rows were staged, before
-- autoanalyze has seen them; the API calls this first so that diff is planned on real statistics.
create or replace function ingest_stage_analyze()
returns void language plpgsql security definer set search_path = public as $$
begin
  analyze ingest_stage;
end $$;

-- ── 5. grants: the RPCs to service_role only; helpers to nobody but the owner ──
revoke execute on function ingest_commit(bigint, jsonb) from public, anon, authenticated;
grant  execute on function ingest_commit(bigint, jsonb) to service_role;
revoke execute on function ingest_stage_analyze() from public, anon, authenticated;
grant  execute on function ingest_stage_analyze() to service_role;
revoke execute on function ingest_undo(bigint, text) from public, anon, authenticated;
grant  execute on function ingest_undo(bigint, text) to service_role;
revoke execute on function _ingest_apply_target(bigint, text, jsonb) from public, anon, authenticated, service_role;
revoke execute on function _ingest_cols(text) from public, anon, authenticated, service_role;
revoke execute on function _ingest_cast(text, text, text) from public, anon, authenticated, service_role;
revoke execute on function _ingest_eq(text, text, text) from public, anon, authenticated, service_role;
revoke execute on function _ingest_target_cfg(text) from public, anon, authenticated, service_role;
revoke execute on function _ingest_scope_overlap(jsonb, jsonb) from public, anon, authenticated, service_role;

-- ── 6. self-check ──────────────────────────────────────────────────────────────
do $$
begin
  if to_regclass('public.ingest_batches') is null or to_regclass('public.ingest_stage') is null
     or to_regclass('public.ingest_replaced') is null then
    raise exception 'ingest tables missing';
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_name in ('ingest_batches', 'ingest_stage', 'ingest_replaced')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'ingest tables must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from information_schema.role_routine_grants
              where routine_name in ('ingest_commit', 'ingest_undo', 'ingest_stage_analyze', '_ingest_apply_target')
                and grantee in ('anon', 'authenticated', 'PUBLIC')) then
    raise exception 'ingest RPCs must not be executable by anon/authenticated/public';
  end if;
  if not exists (select 1 from information_schema.role_routine_grants
                  where routine_name = 'ingest_commit' and grantee = 'service_role') then
    raise exception 'service_role must be able to execute ingest_commit';
  end if;
  if _ingest_target_cfg('orders')->>'span_col' <> 'order_date' then
    raise exception 'target config broken';
  end if;
  if _ingest_scope_overlap('{"kind":"span","from":"2026-09-01","to":"2026-09-24"}',
                           '{"kind":"span","from":"2026-09-24","to":"2026-09-30"}') is not true
     or _ingest_scope_overlap('{"kind":"span","from":"2026-09-01","to":"2026-09-23"}',
                              '{"kind":"span","from":"2026-09-24","to":"2026-09-30"}') is true then
    raise exception 'scope overlap rule broken';
  end if;
end $$;
