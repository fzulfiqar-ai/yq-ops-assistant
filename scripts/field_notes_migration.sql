-- Field intelligence: market observations captured by reps (competitor pricing, stock-outs seen,
-- demand signals, customer requests, complaints). Each note is also embedded into the RAG memory
-- (kb_chunks) so the chat recalls ground truth from the field, not just ERP data. Idempotent.
create table if not exists field_notes (
  id         bigint generated always as identity primary key,
  note       text not null,
  category   text,            -- competitor_price | stockout | demand | complaint | new_product | other
  created_by text,
  created_at timestamptz default now()
);
create index if not exists field_notes_time on field_notes (created_at desc);
alter table field_notes enable row level security;  -- backend (service role) manages it
