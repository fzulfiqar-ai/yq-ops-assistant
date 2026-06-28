# YQ Portal — One-Time Activation Checklist

Everything on the code side is **done and committed**. This is your one-time setup. Do the steps
**in order** — the order matters so the live Streamlit login never breaks.

---

## 1 · Database migration (2 min) — unlocks Team + per-user login

Supabase dashboard → **SQL Editor** → New query → paste **all of**
[`scripts/team_management.sql`](scripts/team_management.sql) → **Run**.

(Adds the `features` / `status` columns + the `app_invites` table, and grants your two admin
accounts full access. Safe to re-run.)

## 2 · Set your admin passwords (1 min) — locally, never in chat

```bash
python -m scripts.manage_users set fzulfiqar@pie-int.com  'YourPassword'      admin
python -m scripts.manage_users set furqanahmed223@gmail.com 'AnotherPassword' admin
```

(Already set `fzulfiqar@pie-int.com` to a temp value during the demo — rerun to make it yours.)

## 3 · Deploy the backend (Railway)

- **Add 1 env var** on the `yq-ops-assistant` service:
  `APP_BASE_URL = https://<your-vercel-app>.vercel.app`  (set after step 4; used for invite links)
- **Add the Vercel origin** to `ALLOWED_ORIGINS` (comma-append `https://<your-vercel-app>.vercel.app`)
- **Deploy:** `git push` (Railway auto-builds; `cryptography` installs from requirements for the new
  ES256 token validation). Already set earlier and still valid: `AGENT_API_KEY`, `RESEND_API_KEY`,
  `EMAIL_FROM`, `ALERT_EMAIL_TO`, `SUPABASE_*`.

## 4 · Deploy the frontend (Vercel — free)

1. vercel.com → **Add New Project** → import the GitHub repo.
2. **Root Directory:** `web` · Framework: **Vite** (auto) · Build: `npm run build` · Output: `dist`.
3. **Environment Variables:**
   - `VITE_SUPABASE_URL` = `https://vofwqcqmdwdidueqxtxy.supabase.co`
   - `VITE_SUPABASE_ANON_KEY` = `sb_publishable_…` (your publishable key)
   - `VITE_API_URL` = `https://yq-ops-assistant-production.up.railway.app`
4. **Deploy.** Then put that Vercel URL back into `APP_BASE_URL` + `ALLOWED_ORIGINS` (step 3) and redeploy Railway.

## 5 · Security hygiene

- **Rotate the `sb_secret_…` key** (Supabase → Settings → API) — it was pasted in chat.
- Change your temp password (step 2 covers it).

---

## Optional / later

- **Invites to outside emails:** verify a domain in **Resend** (3 DNS records), then set
  `EMAIL_FROM = YQ Bahrain <alerts@yourdomain.com>`. Until then, Resend only delivers to your own
  account email — temp-password onboarding works regardless.
- **Step 5 — real margins:** load landed costs (confirm which column is your cost basis — likely
  `LC`):
  ```bash
  python -m scripts.import_costs "roadmap_sources/raw/pricing/Mobile Accessories - Cost Pricing  RRP.xlsx" --cost-col LC --effective-date 2026-06-01 --commit
  ```
  Then switch `v_product_margin` to landed-cost basis (one view change in Supabase).
- **Step 4 — hands-off data refresh:** Power Automate Desktop exports Focus reports daily → emails
  them → an n8n email-to-ingest flow hits `POST /ingest`. (The manual **Data** upload page works now.)

---

## What's already live & verified

- React portal builds clean, code-split, PWA-installable; login → `/me` → shell verified (admin).
- Backend: ES256 + HS256 JWT, `/me`, `/report/{key}` (feature-gated), `/team`, `/ask` model,
  `ingest_runs` freshness, 8 agents emailing.
- Per-user Supabase Auth; shared `yq2024` password removed.
