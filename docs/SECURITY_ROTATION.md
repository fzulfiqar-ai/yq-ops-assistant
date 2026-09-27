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

## Sessions, forced password change and the login trail (release R7b, 27-Sep-2026)

- **Forced password change.** `user_roles.must_reset` (server-owned) holds a login at the password screen: every
  API route but `GET /me`, `GET /auth/features` and `POST /auth/password` answers 403
  `password_change_required` (`app/auth.py`; `tests/test_r7b_security.py` sweeps every route). Every role is
  covered, the owner (`OWNER_EMAILS`) never — the break-glass login always gets in. The flag switches on when the
  owner applies `scripts/user_roles_must_reset_migration.sql` at an announced quiet hour.
- **Session revocation.** A password change made through the API signs every OTHER session of that login out:
  Supabase Auth's `POST /logout?scope=others` with the member's own access token
  (`app/user_auth.revoke_other_sessions`), so no other device can refresh; and the access tokens those sessions
  already hold are refused (401) from then on by this process (`app/auth.end_other_sessions`; tokens issued up to
  30 s before the change pass, for clock skew). The session that made the change stays signed in. Why not a
  SECURITY DEFINER function deleting `auth.sessions` rows: the sign-out is the documented API, needs no migration
  and no grant on the auth schema. Its limit: it needs the member's own token, so an ADMIN resetting someone's
  password (re-invite with a temporary password) does not end that person's other sessions — a must_reset session
  can then set a new password without the old one. To cut a suspected-compromised account off completely: Remove it
  on the Team page (deletes the Supabase auth user, and every session and refresh token with it; its tokens are
  refused at once because the user_roles row is gone), then invite the person again. A service_role-only function
  that deletes one user's `auth.sessions` would close this gap without the remove; not built in R7b.
- **Login trail.** The first request of every Supabase session seen by the API process writes one `audit_log`
  row `auth.session_seen` (session id, role, sign-in method, hashed client address, browser; never the token).
  `select ts, user_email, detail from audit_log where event = 'auth.session_seen' order by ts desc;`
- **API docs.** `/docs`, `/redoc` and `/openapi.json` are not served in production (`RENDER` or `ENV=production`
  set); they stay on a laptop.
- **Owner actions (Supabase / hosting, not code):** announce the password reset to the reps and the office before
  the migration runs; Supabase → Authentication → Sign In / Providers → **turn off "Allow new users to sign up"**
  (accounts are created by the Team page only); delete `.env.render` (stale since 18-Aug; `scripts/deploy_render.py`
  would push it over the live env); move the hosting keys (Render API key, Cloudflare write token, R2 keys,
  `DATABASE_URL`) out of the OneDrive-synced folder into a local-only secrets location; set the Render service's
  Auto-Deploy to **No** (render.yaml now says `autoDeploy: false`).

## Backlog (not yet implemented)

- **MFA**: Supabase supports TOTP enrollment; needs a frontend enrollment + challenge flow
  (Settings page) and `aal2` enforcement for the admin role in `app/auth.py`.
- SSO via Microsoft Entra (ROADMAP.md Phase 6 cross-cutting).
