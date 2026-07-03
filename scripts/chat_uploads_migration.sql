-- Assistant document uploads — extracted text kept 24h, owner-scoped.
-- Written/read ONLY via the service client (no yq_readonly grant: private user docs
-- must never be reachable from the text-to-SQL surface). Idempotent.

create table if not exists chat_uploads (
  id            uuid primary key default gen_random_uuid(),
  user_email    text not null,
  filename      text,
  text_content  text,
  created_at    timestamptz default now(),
  expires_at    timestamptz not null default now() + interval '24 hours'
);
create index if not exists chat_uploads_user_idx on chat_uploads (user_email, expires_at);

alter table chat_uploads enable row level security;
