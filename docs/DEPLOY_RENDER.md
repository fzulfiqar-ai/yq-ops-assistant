# Moving the API off Railway → Render (free)

## Why

Railway's trial ended. The workspace subscription went `INACTIVE` with no payment
method on file, so Railway **removed** both deployments:

| Service | Last deploy | Status |
| --- | --- | --- |
| `yq-ops-assistant` (API) | 13-Jul-2026, `0a2d8ad` | REMOVED |
| `n8n` (automations) | 22-Jun-2026 | REMOVED |

The domain record survived but points at nothing, so the edge answers
`404 {"status":"error","message":"Application not found"}`. That is why the portal
sat on "Waking the server…" forever — nothing was ever going to wake up.

Supabase (all data, photos, the `finds` bucket) and the Vercel frontend were never
affected.

## What changed in the repo

| File | Change |
| --- | --- |
| `requirements.txt` | Dropped `streamlit`, `plotly`, `python-docx`; pinned `numpy` explicitly |
| `requirements-dev.txt` | **New** — holds `streamlit`/`plotly`/`watchdog` for the local dashboard |
| `Dockerfile` | Dropped `build-essential`; single uvicorn worker |
| `.dockerignore` | **New** — keeps `web/`, `dashboard/`, data files out of the image |
| `render.yaml` | **New** — Render blueprint, free plan, Frankfurt, `/health` check |
| `web/src/lib/auth.tsx` | New `offline` state — a 404 is no longer mistaken for a cold start |
| `web/src/components/guards.tsx` | New "Backend is offline" screen; waking screen stops lying after ~2 min |

`streamlit` and `plotly` were only ever used by `dashboard/ui.py`, which the
Dockerfile never copied into the image — roughly 250 MB of wheels (`pyarrow`,
`altair`, `pydeck`, `protobuf`, `gitpython`…) shipped to production for nothing.
That slack is what makes the 512 MB free tier viable.

Verified: `app.main` imports cleanly with `streamlit`/`plotly` forced to `None`,
registering all 122 routes.

## Deploy

### 1. Push

```bash
git add -A && git commit -m "deploy: move API to Render free tier" && git push
```

### 2. Create the service

Render Dashboard → **New** → **Blueprint** → pick the `yq-ops-assistant` repo.
It reads `render.yaml` and creates the service. No credit card required.

### 3. Set the environment variables

All 24 are marked `sync: false`, so Render will not read them from git. Dump them
from Railway (still readable while the project exists — **do this before deleting it**):

```bash
railway variables --kv | grep -v '^RAILWAY_' > .env.render
```

`.env.render` is gitignored. In Render → **Environment** → **Add from .env**,
paste the contents, then delete the local file.

The 24 to migrate:

```
AGENT_API_KEY        AGNES_API_KEY       ALERT_EMAIL_TO      ALLOWED_ORIGINS
APIFY_TOKEN          APP_BASE_URL        CEREBRAS_API_KEY    DASHBOARD_SECRET
EMAIL_FROM           GROQ_API_KEY        OPENROUTER_API_KEY  RESEND_API_KEY
SCALEWAY_API_KEY     SMTP_HOST           SMTP_PASS           SMTP_PORT
SMTP_USER            SUPABASE_JWT_SECRET SUPABASE_KEY        SUPABASE_URL
TAVILY_API_KEY       TOGETHER_API_KEY    WA_HUMAN_NUMBER     YOUTUBE_API_KEY
```

The twelve `RAILWAY_*` variables are platform-injected and must **not** be copied.

`ALLOWED_ORIGINS` and `APP_BASE_URL` both point at the Vercel frontend, so they
carry over unchanged.

### 4. Repoint the frontend

Render gives the service a new URL, e.g. `https://yq-ops-assistant.onrender.com`.

Vercel → project → Settings → Environment Variables → set:

```
VITE_API_URL = https://<your-service>.onrender.com
```

Then redeploy the frontend (Vercel is **not** git-integrated here):

```bash
cd web && npx vercel deploy --prod
```

`VITE_*` values are baked in at build time — changing the variable without
rebuilding does nothing.

### 5. Verify

```bash
curl -s https://<your-service>.onrender.com/health
# {"status":"ok","service":"yq-ops-assistant","version":"..."}
```

Then sign in to the portal.

## Free-tier trade-offs

- **Sleeps after 15 min idle**, ~50 s cold start. The "Waking the server…" screen
  is built for this and now escalates honestly if the wait is real downtime.
- **0.1 CPU.** Reports and chat are fine. ffmpeg reel rendering will be slow —
  treat marketing video as a batch job, not something to wait on.
- **512 MB RAM.** Do not reinstate `streamlit`/`plotly` in `requirements.txt`; use
  `pip install -r requirements-dev.txt` locally instead.

## n8n

Still down and **not** covered by this blueprint. Render's free tier is a poor fit
(it needs a persistent database and no sleep). Options: run it locally, self-host
on a VPS, or use n8n Cloud. Nothing in the portal breaks without it — only the
scheduled automations stop firing.

## Before deleting the Railway project

Export the env vars first (step 3). Once the project is gone the values are
unrecoverable, and several are provider keys that would need reissuing.
