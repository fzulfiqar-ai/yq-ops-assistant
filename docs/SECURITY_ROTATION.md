# Security Rotation Runbook

When to use: on any suspected leak, on staff departure, or on the schedule below.
All secrets live ONLY in `.env` (local), Railway variables (API), Vercel env (portal),
and the n8n "YQ API Key" credential. None are committed to git (verified through history).

## Priority 1 — rotate NOW (already flagged in ACTIVATION.md)

| Secret | Where to rotate | Then update |
|---|---|---|
| `SUPABASE_KEY` (sb_secret service-role) | Supabase → Settings → API keys → rotate secret key. **This key was pasted into a chat once — treat as exposed.** | `.env`, Railway `SUPABASE_KEY` |
| `DATABASE_URL` password | Supabase → Settings → Database → reset password | `.env` `DATABASE_URL` (+ `DASHBOARD_SECRET` if still mirrored) |

## Priority 2 — schedule (invalidates active sessions)

| Secret | Where | Notes |
|---|---|---|
| `SUPABASE_JWT_SECRET` | Supabase → Settings → API → JWT | Rotating logs every user out — do it end-of-day; update `.env` + Railway together |
| `AGENT_API_KEY` | Generate 32+ random chars (`python -c "import secrets;print(secrets.token_urlsafe(32))"`) | Update Railway env AND the n8n "YQ API Key" credential in the same window, else hourly crons 401 |

## Priority 3 — LLM/provider keys (quarterly or on anomaly)

Groq, Cerebras, Gemini, OpenRouter, SambaNova, NVIDIA, Mistral, Moonshot, Z.AI, Tavily,
YouTube — rotate at each provider's console; update `.env` (local) + Railway. The router
picks up whatever keys exist; a missing key just removes that provider from rotation.

## Standing rules

- `.env` and `web/.env` stay gitignored — check with `git check-ignore .env web/.env` after any .gitignore edit.
- Never paste the sb_secret / DATABASE_URL into chats, tickets, or docs. Use "rotated on DATE" notes instead.
- The frontend ships ONLY `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (publishable), `VITE_API_URL`.
- Supabase auth tokens persist in browser localStorage (supabase-js default). Accepted risk,
  mitigated by the CSP + sanitizer in `web/vercel.json` / `Assistant.tsx`. Revisit if the
  portal ever embeds third-party scripts.

## Backlog (not yet implemented)

- **MFA**: Supabase supports TOTP enrollment; needs a frontend enrollment + challenge flow
  (Settings page) and `aal2` enforcement for the admin role in `app/auth.py`.
- SSO via Microsoft Entra (ROADMAP.md Phase 6 cross-cutting).
