# Proactive briefing — morning brief + alerts (roadmap N2)

The portal can come to you instead of you visiting it: a **morning brief** (every agent's headline +
recommended actions) and **hourly alerts** (only when something crosses a threshold), delivered by
**email** (works now) and **Telegram** (once a token is set).

## Endpoints (called by a scheduler with the agent key)
| Endpoint | What it does |
|---|---|
| `GET /escalation/brief` | Runs all agents → ONE combined briefing → email + Telegram. Use each morning. |
| `GET /escalation/check` | Evaluates the alert rules → fires only the freshly-triggered ones (deduped 24h). Use hourly. |

Both accept `?send=false` to **preview** (returns the JSON without sending). Auth = `X-Agent-Key`
header (for schedulers) **or** an admin login.

Alert rules (in `app/escalation.py`): overdue receivables > BHD 10k, overdue jumped > BHD 1k since
last run, new low/out-of-stock while still selling, products below cost, > BHD 5k over 90 days.

## Delivery setup
- **Email** — already working (the refresh + briefs send via `app/emailer.py`). Recipient is
  `ALERT_EMAIL_TO`.
- **Telegram** (free, optional) — set `TELEGRAM_BOT_TOKEN` (from @BotFather) + `TELEGRAM_CHAT_ID`
  in the API's env. With both set, the brief + alerts also push to Telegram. Without them, Telegram
  is silently skipped.

## Scheduling in n8n (free)
1. On the **Railway API** service set `AGENT_API_KEY` to a strong secret (this is what lets n8n call
   the protected endpoints).
2. In **n8n** set env vars: `RAILWAY_API_URL` = the API URL, `AGENT_API_KEY` = the same secret.
3. Generate + push the flows:
   ```
   # writes JSON to n8n_workflows/  (import manually), or:
   N8N_API_URL=https://<your-n8n> N8N_API_KEY=<n8n key> python -m scripts.n8n_agents --push
   ```
   This creates/activates:
   - **YQ — Morning Brief** → `GET /escalation/brief` daily at **08:00**
   - **YQ — Hourly Alerts** → `GET /escalation/check` **every hour**
   (plus the per-agent flows). Re-running `--push` cleanly replaces the old `YQ —` / `YQ Agent —` flows.

## Test it now (admin, local)
```
# preview without sending
curl "http://localhost:8001/escalation/brief?send=false" -H "Authorization: Bearer <admin JWT>"
# or trigger a real send (email + Telegram if configured)
curl "http://localhost:8001/escalation/brief"            -H "Authorization: Bearer <admin JWT>"
```

> Tip: the brief is only as good as the data — keep the daily upload current (see
> [FOCUS_EXPORT.md](FOCUS_EXPORT.md)) so the 08:00 brief reflects yesterday's numbers.
