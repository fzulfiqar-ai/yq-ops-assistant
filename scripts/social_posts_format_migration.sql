-- Premium Ad Studio: formats (reel/feed/square/carousel) + caption/voice language.
-- Idempotent.
alter table social_posts add column if not exists format text default 'reel';
alter table social_posts add column if not exists lang text default 'en';
-- carousels store extra image URLs beyond media_url
alter table social_posts add column if not exists extra_media jsonb default '[]'::jsonb;

-- keep the status set (rendering already added by agnes_migration.sql; re-assert safe)
alter table social_posts drop constraint if exists social_posts_status_check;
alter table social_posts add constraint social_posts_status_check
  check (status in ('draft','approved','posted','failed','dismissed','rendering'));
