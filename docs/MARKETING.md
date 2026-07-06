# Marketing & Outreach Engine — the BHD 10,000/month build

**Goal:** lift monthly sales from ~BHD 3,400 (last-90-day average) to **BHD 10,000+**.
March 2026 already hit 9,664, so the target is reachable with the *existing* book — the
gap is 94% a **B2B reactivation** problem, not an audience problem. This engine turns the
platform's draft-producing agents into an approved-send machine with revenue attribution,
plus a free content studio (picture ads + 9:16 videos from catalog photos).

Everything is **free / open-source**, and **a human approves every outbound message**
(owner decision). No paid AI, no paid tools.

---

## What got built (and where)

| Piece | File | What it does |
|---|---|---|
| Outreach core | `app/outreach.py` | Send queue, contact enrichment, opt-out, attribution, scorecard |
| Content engine | `app/video_gen.py` | Picture ads (Pillow) + AI videos from catalog photos |
| Agnes AI client | `app/agnes.py` | Free OpenAI-compatible AI image/video backend (image-to-video) |
| WhatsApp Cloud API | `app/whatsapp.py` | Phase 2 concierge bot (config-gated; inert until keys set) |
| Social publishing | `app/social_publish.py` | IG/FB via Graph API, TikTok via Telegram hand-off, inbound webhook |
| API endpoints | `app/main.py` | `/outreach/*`, `/contacts/*`, `/social/*`, `/wa/*`, `/public/*` webhooks |
| DB tables/views | `scripts/outreach_migration.sql` | `outreach_queue`, `outreach_touches`, `marketing_opt_outs`, `catalog_visits`, `social_posts`, `wa_sessions`, `wa_messages`, + `v_outreach_attribution`, `v_marketing_kpis`, `v_month_pace` |
| Marketing Studio | `web/src/pages/Marketing.tsx` | Send Center · Contacts · Campaigns · Content · Results |
| Opt-out page | `web/src/pages/OptOut.tsx` | Public PDPL unsubscribe landing |
| Catalog CTA | `web/src/pages/PublicCatalog.tsx` | "Order on WhatsApp" button + visit attribution ping |
| Schedules | `scripts/n8n_agents.py` → `n8n_workflows/agents/*.json` | 5 new scheduled agents |

### New agents (registered in `app/agents.py`, callable at `GET /agents/{name}`)

| Agent | Schedule | Purpose |
|---|---|---|
| `contact_enrich` | nightly 02:00 | Find business phones/emails for top-value customers (Tavily public listings) |
| `outreach_builder` | Sun 07:00 | Build the week's send queue from `sales_outreach` / `winback` / `sales_push` / `lead_gen` |
| `outreach_digest` | daily 08:30 | Telegram digest of messages waiting for a one-tap send |
| `growth_scorecard` | Fri 17:00 | Month pace vs 10,000 target + outreach funnel + contact coverage |
| `content_engine` | Tue + Thu 10:00 | Render picture ads + kick off an AI video → `social_posts` drafts for approval |
| `content_poll` | hourly | Finish Agnes AI videos still rendering (async ~90s) and make them approvable |

---

## How the money is made (the loop)

1. **Fill contacts.** `contact_enrich` (auto, nightly) + bulk-paste import in the Contacts
   tab. This was the #1 blocker — the DB started with **zero** phone numbers.
2. **Build the queue.** `outreach_builder` drains the growth agents into `outreach_queue`
   as drafts: reorder nudges (customers past their cycle), win-backs (lapsed high-value),
   clearance offers (slow stock → the customers who buy that category), and lead openers.
   Top win-backs get an LLM tone-pass (free rotation, PII-redacted).
3. **Approve & send.** In **Marketing Studio → Send Center**: WhatsApp = one-tap `wa.me`
   deep link (you press Send in WhatsApp — zero ToS risk, uses your own number); Email =
   sent server-side via Resend/Brevo with an opt-out footer.
4. **Every real send logs a touch** (`outreach_touches`).
5. **Attribution.** `v_outreach_attribution` credits any order the customer places within
   14 days of a touch. **Results tab** shows month pace vs 10,000, weekly funnel, and
   reactivated-customer count.

---

## Legal & compliance (Bahrain PDPL, WhatsApp/Meta ToS)

- **B2B only.** Outreach targets business contacts obtained legitimately (sales history,
  public business listings, salesman knowledge). No personal-PII scraping.
- **Opt-out everywhere.** Every email carries an unsubscribe link (`/optout/{token}`,
  HMAC-signed). Replying "STOP" (or Arabic قف/إلغاء) on WhatsApp records an opt-out.
  Opt-outs are checked **before drafting AND before sending**.
- **WhatsApp stays free.** Phase 1 "assist mode" uses `wa.me` links you send from your own
  phone — normal person-to-person messaging. Phase 2 Cloud API only ever replies **inside
  the 24-hour service window** (the customer messaged first) — those conversations are free.
  **Paid marketing templates are never sent by code** — this is enforced in `app/whatsapp.py`,
  not just config.
- **Human in the loop.** Nothing sends autonomously in Phase 1–2. Kill switches:
  `OUTREACH_ENABLED=0`, `WA_AUTOREPLY_ENABLED=0`. Per-day caps live in `outreach.POLICIES`.

---

## Phase 1 is live now (assist mode). Phases 2–4 need keys/accounts.

### To turn on WhatsApp "Order" buttons + one-tap sends (assist mode — free, no Meta)
Set on Railway:
```
WA_HUMAN_NUMBER=97333XXXXXX     # your WhatsApp number, digits only
```
That's it. The public catalog gains an "Order on WhatsApp" button and the Send Center's
WhatsApp buttons deep-link to your number with the message pre-filled.

### Phase 2 — WhatsApp Cloud API bot (auto-reply concierge)
1. **Use a SECOND SIM** — registering a number on the Cloud API removes it from the
   WhatsApp app. Never migrate your daily number.
2. developers.facebook.com → create app → add **WhatsApp** → API Setup. Get the
   **Phone Number ID** and a **permanent System-User access token**.
3. Set on Railway: `WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`, `WA_VERIFY_TOKEN` (any secret
   string you choose), `META_APP_SECRET` (App Settings → Basic).
4. In Meta's WhatsApp config, set the **Callback URL** to
   `https://<railway-api>/public/wa/webhook` and the **Verify Token** to your
   `WA_VERIFY_TOKEN`; subscribe to `messages`.
5. Test: message the number "price iPhone cable" → the bot replies in-window; order intent
   escalates to your Telegram. Confirm **zero paid conversations** in Meta → Billing.

### Phase 3 — Content posting to Instagram + Facebook
1. Create a **Facebook Page** and an **Instagram Business/Creator** account; link the IG
   account to the Page.
2. Same Meta app → add the Page. Get a **Page access token** (`FB_PAGE_TOKEN`), the
   **Page ID** (`FB_PAGE_ID`), and the **IG Business account ID** (`IG_BUSINESS_ID`).
   In dev mode the app can publish to *your own* Page/IG without app review.
3. Set those three on Railway. In Marketing Studio → **Content**, tap *Generate*, then
   *Publish* on any card.
4. **TikTok** posts as a Telegram hand-off (download + 30-second manual upload) — its
   Content Posting API needs an audited app for public posts.

### Phase 4 — Inbound social → WhatsApp funnel
Point the Meta app's webhook `https://<railway-api>/public/meta/webhook` at your Page/IG
(fields: `feed`, `comments`, `messages`). Comments/DMs mentioning price arrive on your
Telegram with a drafted reply for one-tap posting, and interested users become `leads`.

---

## Free-tier notes

- **Email:** Resend (100/day, 3k/mo) or Brevo (300/day). Outreach caps at 25 emails/day —
  well under the free limit.
- **LLM:** the existing free-provider rotation (`app/llm_router.py`). No Anthropic/paid.
- **Video:** Agnes AI (free, OpenAI-compatible) generates a real image-to-video reel from
  the clean product photo (~90s, async — kicked off by `content_engine`, finished by
  `content_poll`). The finished MP4 is copied into our own `marketing` Supabase bucket
  (stable URL + CSP-friendly). If `AGNES_API_KEY` is absent, the engine falls back to the
  local FFmpeg ken-burns renderer; picture ads (Pillow) are always produced regardless.
  Note: Agnes animates the *raw* product photo (the text-heavy branded card trips its
  content moderation) — the caption carries the price/branding.
- **Contact discovery:** Tavily free tier + OpenStreetMap/Overpass (already wired).

## Verify locally
```
python -m scripts.n8n_agents --write        # regenerate workflow JSONs
# then, with the API running:
GET /agents/contact_enrich                   # coverage grows
GET /agents/outreach_builder                 # queue fills
GET /outreach/kpis                           # month pace vs 10,000
GET /agents/content_engine                   # ads land in the 'marketing' bucket
```
Push new schedules to n8n with `python -m scripts.n8n_agents --push` (needs `N8N_API_URL`
+ `N8N_API_KEY`).
