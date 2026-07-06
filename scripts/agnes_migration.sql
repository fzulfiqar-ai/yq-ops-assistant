-- Agnes AI async video: social_posts needs a 'rendering' status while the MP4 generates.
-- Idempotent — drop + re-add the status CHECK with the extra value.
alter table social_posts drop constraint if exists social_posts_status_check;
alter table social_posts add constraint social_posts_status_check
  check (status in ('draft','approved','posted','failed','dismissed','rendering'));
