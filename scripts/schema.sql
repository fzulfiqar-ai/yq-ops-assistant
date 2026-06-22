
-- ============================================================
-- YQ Bahrain ops assistant — Phase 0 schema (idempotent)
-- ============================================================

-- Dimension / spine -----------------------------------------
create table if not exists categories (
    id bigint generated always as identity primary key,
    name text unique not null,
    created_at timestamptz default now()
);

create table if not exists products (
    id bigint generated always as identity primary key,
    sku_code text unique not null,
    item_name text,
    category_id bigint references categories(id),
    unit_name text,
    status text,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists product_aliases (
    id bigint generated always as identity primary key,
    product_id bigint references products(id),
    alias_text text unique not null
);

create table if not exists customers (
    id bigint generated always as identity primary key,
    name text unique not null,
    created_at timestamptz default now()
);

-- Sales ------------------------------------------------------
create table if not exists orders (
    id bigint generated always as identity primary key,
    invoice_no text unique not null,
    order_date date,
    customer_id bigint references customers(id),
    customer_name text,
    gross_bhd numeric,
    salesman text,
    payment_mode text,
    sales_account_name text,
    source_file text,
    imported_at timestamptz default now()
);

create table if not exists order_lines (
    id bigint generated always as identity primary key,
    invoice_no text not null,
    order_id bigint references orders(id),
    line_no int,
    line_date date,
    customer_account text,
    item_name text,
    product_id bigint references products(id),
    quantity numeric,
    rate_bhd numeric,
    gross_bhd numeric,
    discount_bhd numeric,
    taxable_bhd numeric,
    vat_amount_bhd numeric,
    total_amount_bhd numeric,
    warehouse_name text,
    narration text,
    source_file text,
    imported_at timestamptz default now(),
    unique (invoice_no, line_no)
);

-- Stock ------------------------------------------------------
create table if not exists stock_movements (
    id bigint generated always as identity primary key,
    item_name text not null,
    product_id bigint references products(id),
    move_date date,
    voucher text,
    voucher_type text,
    received_qty numeric,
    received_rate_bhd numeric,
    issued_qty numeric,
    issued_rate_bhd numeric,
    balance_qty numeric,
    received_value_bhd numeric,
    issued_value_bhd numeric,
    balance_value_bhd numeric,
    avg_rate_bhd numeric,
    warehouse_name text,
    to_warehouse_name text,
    narration text,
    row_hash text,
    source_file text,
    imported_at timestamptz default now(),
    unique (voucher, item_name, row_hash)
);

create or replace view shipments as
    select move_date as received_date, voucher as mrn_no, item_name, product_id,
           received_qty, received_rate_bhd, received_value_bhd, warehouse_name
    from stock_movements
    where voucher_type = 'Material Receipt Note';

-- Finance & profitability -----------------------------------
create table if not exists ledger_entries (
    id bigint generated always as identity primary key,
    account text not null,
    entry_date date,
    voucher text,
    counter_account text,
    debit_bhd numeric,
    credit_bhd numeric,
    balance_bhd numeric,
    currency text,
    payment_mode text,
    salesman text,
    narration text,
    row_hash text,
    source_file text,
    imported_at timestamptz default now(),
    unique (account, voucher, row_hash)
);

create table if not exists product_profitability (
    id bigint generated always as identity primary key,
    item_name text not null,
    product_id bigint references products(id),
    report_date date,
    gross_bhd numeric,
    discount_pct numeric,
    net_amount_bhd numeric,
    cogs_bhd numeric,
    gross_profit_bhd numeric,
    gp_margin_pct numeric,
    misc_charges_bhd numeric,
    net_profit_bhd numeric,
    np_margin_pct numeric,
    source_file text,
    imported_at timestamptz default now(),
    unique (item_name, report_date)
);

-- Pricing ----------------------------------------------------
create table if not exists selling_prices (
    id bigint generated always as identity primary key,
    item_name text,
    sku_code text,
    product_id bigint references products(id),
    customer_name text,
    customer_code text,
    warehouse_name text,
    warehouse_code text,
    price_book text,
    currency text,
    start_date date,
    end_date date,
    min_qty numeric,
    max_qty numeric,
    unit_name text,
    rate_bhd numeric,
    price_tiers jsonb,
    status text,
    narration text,
    source_file text,
    imported_at timestamptz default now(),
    unique (sku_code, price_book, customer_code, start_date)
);

create table if not exists purchase_costs (
    id bigint generated always as identity primary key,
    sku_code text not null,
    product_id bigint references products(id),
    landed_cost_bhd numeric not null,
    currency text,
    effective_date date not null,
    source_file text,
    created_at timestamptz default now(),
    unique (sku_code, effective_date)
);

-- App / governance ------------------------------------------
create table if not exists user_roles (
    id bigint generated always as identity primary key,
    email text unique not null,
    role text not null check (role in ('admin','manager','viewer')),
    created_at timestamptz default now()
);

create table if not exists pending_actions (
    id bigint generated always as identity primary key,
    action_type text,
    payload jsonb,
    status text default 'pending',
    requested_by text,
    requested_at timestamptz default now(),
    approved_by text,
    approved_at timestamptz,
    result text
);

create table if not exists query_cache (
    id bigint generated always as identity primary key,
    query_hash text unique,
    question text,
    reply text,
    sql_used text,
    raw_data jsonb,
    created_at timestamptz default now(),
    expires_at timestamptz default now() + interval '7 days'
);

create table if not exists audit_log (
    id bigint generated always as identity primary key,
    ts timestamptz default now(),
    user_email text,
    event text,
    question text,
    sql_used text,
    detail jsonb
);

create table if not exists ingest_runs (
    id bigint generated always as identity primary key,
    started_at timestamptz default now(),
    finished_at timestamptz,
    status text,
    file text,
    rows_in int,
    rows_loaded int,
    join_match_pct numeric,
    errors text
);

-- Row Level Security ----------------------------------------
-- Enable RLS everywhere; the backend uses the service role (bypasses RLS) for writes,
-- while authenticated read access is granted via simple select policies.
do $$
declare t text;
begin
  foreach t in array array[
    'categories','products','product_aliases','customers','orders','order_lines',
    'stock_movements','ledger_entries','product_profitability','selling_prices',
    'purchase_costs','user_roles','pending_actions','query_cache','audit_log','ingest_runs'
  ]
  loop
    execute format('alter table %I enable row level security;', t);
    execute format('drop policy if exists %I on %I;', t||'_read', t);
  end loop;
end $$;

-- authenticated users may read business data (not the governance tables)
do $$
declare t text;
begin
  foreach t in array array[
    'categories','products','product_aliases','customers','orders','order_lines',
    'stock_movements','ledger_entries','product_profitability','selling_prices','purchase_costs'
  ]
  loop
    execute format(
      'create policy %I on %I for select to authenticated using (true);', t||'_read', t);
  end loop;
end $$;

-- a user can read only their own role row
create policy user_roles_read on user_roles
    for select to authenticated using (email = auth.jwt() ->> 'email');
