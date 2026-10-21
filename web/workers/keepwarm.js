/**
 * yq-keepwarm — Cloudflare Worker cron (24-Sep-2026). Config and deploy notes: ../wrangler.keepwarm.jsonc.
 *
 * Two triggers, told apart by the cron string the runtime hands us:
 *   HEALTH_CRON     (every 10 min) two legs, one after the other:
 *                   1. GET {API_URL}/health — keeps the free Render container inside its 15-minute idle
 *                      window around the clock (a cold start costs 11-12 s per merchant; 24/7 since 28-Sep-2026).
 *                   2. GET {SUPABASE_URL}/rest/v1/keepalive_ping?select=id&limit=1 with the publishable (anon)
 *                      key as both apikey and Bearer — a real PostgREST read, which is what Supabase counts as
 *                      activity: a Free project with none for ~7 days is PAUSED and its DNS record withdrawn
 *                      (the 14-Sep-2026 outage). The same read .github/workflows/keepalive.yml makes (same URL,
 *                      same headers, 30 s timeout); 200 or 206 = awake. It runs even when the API leg throws and
 *                      never throws itself. The key is the PUBLISHABLE one, never the service key.
 *   SCHEDULER_CRON  GET each SCHEDULER_PATHS entry with X-Agent-Key — the shop jobs (order alert
 *                   retries, unassigned + unconfirmed reminders, cleanup, stale-stock alert), the
 *                   daily agents and the agent reactions. Same method, path and header as
 *                   .github/workflows/shop-cron.yml, which keeps only its manual trigger now (its
 *                   schedule is commented out; the API's run_shop_jobs also holds a 10-minute lease,
 *                   so a stray second caller is skipped rather than doubled).
 *
 * A failed Supabase read is logged (console.error) on EVERY tick and also sent to Telegram, at most once per
 * 6 hours, with NO stored state: the alert leaves only from the tick that falls in the first 10 minutes of UTC
 * hour 0, 6, 12 or 18 (event.scheduledTime; with the every-10-minutes cron that is the :00 tick, i.e. 03:00,
 * 09:00, 15:00 and 21:00 Bahrain). So the first alert of a new outage can be up to 6 hours late, and an outage that heals
 * before the next such tick never alerts. Needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID; without them the alert
 * is skipped with one log line. There is deliberately no third cron trigger (tests/test_r1_alerts.py pins two).
 *
 * Logging rule (same as shop-cron.yml): never the response body. It carries order numbers and
 * merchant/shop names; the status and the top-level keys say enough to tell "ran" from "did nothing".
 * A scheduler response with ok:false is logged as a warning with the failed job names only. The Supabase
 * leg logs only "keepalive_ping -> HTTP <status>" and never reads its body. The Telegram leg logs only its
 * HTTP status (or the error's name): its URL carries the bot token, so it does not go through ping(), which
 * logs the path. Nothing here caches: the API answers JSON without Cache-Control headers, which
 * Cloudflare's fetch() does not store.
 */
const HEALTH_CRON = '*/10 * * * *'
const SCHEDULER_CRON = '*/15 * * * *'
const DEFAULT_API = 'https://api.yqmarketplace.com'
const HEALTH_TIMEOUT_MS = 90_000      // tolerates a genuine cold start (keepalive.yml: --max-time 90)
const SCHEDULER_TIMEOUT_MS = 120_000  // shop-cron.yml: --max-time 120
const SUPABASE_TIMEOUT_MS = 30_000    // keepalive.yml: --max-time 30
const TELEGRAM_TIMEOUT_MS = 10_000    // app/notify.py: timeout=10
const ALERT_EVERY_HOURS = 6           // an alert may leave only in UTC hour 0, 6, 12 or 18 ...
const ALERT_WINDOW_MINUTES = 10       // ... and only from the tick in that hour's first 10 minutes

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(event.cron, env, event.scheduledTime))
  },
  // No public surface (workers_dev off, no routes); this only answers a dashboard "Send request".
  async fetch() {
    return new Response('yq-keepwarm: cron only', { status: 404 })
  },
}

async function run(cron, env, scheduledTime) {
  const api = String(env.API_URL || DEFAULT_API).replace(/\/+$/, '')
  if (cron === HEALTH_CRON) {
    try {
      const r = await ping(`${api}/health`, {}, HEALTH_TIMEOUT_MS)
      if (r.status !== 200) console.error(`/health -> HTTP ${r.status} (${r.note})`)
    } finally {
      // The database leg guards against the slow, catastrophic failure (a paused project), so a broken API
      // leg must not skip it; keepalive.yml likewise lets neither of its two legs stop the other.
      await keepalive(env, scheduledTime)
    }
    return
  }
  if (cron === SCHEDULER_CRON) {
    await scheduler(api, env)
    return
  }
  console.warn(`unknown cron trigger "${cron}" — nothing to do`)
}

async function scheduler(api, env) {
  if (!env.AGENT_API_KEY) {
    console.error('AGENT_API_KEY secret is not set — npx wrangler@4 secret put AGENT_API_KEY -c wrangler.keepwarm.jsonc')
    return
  }
  const paths = String(env.SCHEDULER_PATHS || '/scheduler/shop-jobs')
    .split(',').map((s) => s.trim()).filter(Boolean)
  // One at a time: the API is a single worker, so parallel calls would only queue there.
  for (const path of paths) {
    const r = await ping(`${api}${path}`, { 'X-Agent-Key': env.AGENT_API_KEY }, SCHEDULER_TIMEOUT_MS)
    if (r.status !== 200) {
      console.warn(`${path} -> HTTP ${r.status} (${r.note})`)
    } else if (r.body && r.body.ok === false) {
      const failed = Array.isArray(r.body.errors) ? r.body.errors.join(',') : '?'
      console.warn(`${path} -> HTTP 200 but ok:false [${failed}]`)
    }
  }
}

/** The Supabase leg. Never throws: a failure is logged on every tick and sent to Telegram on the 6-hour rule. */
async function keepalive(env, scheduledTime) {
  try {
    const base = String(env.SUPABASE_URL || '').trim().replace(/\/+$/, '')
    const key = String(env.SUPABASE_ANON_KEY || '').trim()
    if (!base || !key) {
      console.error('SUPABASE_URL / SUPABASE_ANON_KEY secrets are not set — Supabase is NOT being kept awake and pauses after ~7 idle days: ' +
        'npx wrangler@4 secret put SUPABASE_URL -c wrangler.keepwarm.jsonc ; ' +
        'npx wrangler@4 secret put SUPABASE_ANON_KEY -c wrangler.keepwarm.jsonc')
      return
    }
    const r = await readKeepalive(base, key)
    const code = String(r.status).padStart(3, '0')   // 000 = no answer at all, as keepalive.yml prints it
    if (r.status === 200 || r.status === 206) {
      console.log(`keepalive_ping -> HTTP ${code}`)
      return
    }
    console.error(`keepalive_ping -> HTTP ${code} (${r.note})`)
    if (inAlertWindow(scheduledTime)) await alertTelegram(env, code, r.note)
  } catch (e) {
    console.error(`keepalive leg failed: ${errName(e)}`)
  }
}

/** One PostgREST read under the anon role. Returns {status, note}; status 0 = no answer. Never reads the body. */
async function readKeepalive(base, key) {
  let res
  try {
    res = await fetch(`${base}/rest/v1/keepalive_ping?select=id&limit=1`, {
      method: 'GET',
      headers: { apikey: key, Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(SUPABASE_TIMEOUT_MS),
    })
  } catch (e) {
    const note = e && e.name === 'TimeoutError'
      ? `no answer in ${SUPABASE_TIMEOUT_MS / 1000}s`
      : 'could not connect: DNS or network, which is how a paused project looks'
    return { status: 0, note }
  }
  try { await res.body?.cancel() } catch { /* only the status is read */ }
  return { status: res.status, note: noteFor(res.status) }
}

function noteFor(status) {
  if (status === 401 || status === 403) return 'the anon key was rejected: check the SUPABASE_ANON_KEY secret'
  if (status === 404) return 'table keepalive_ping not found: apply scripts/hotfix_view_grants_migration.sql'
  return 'unexpected answer from Supabase'
}

/** True on the one tick per 6 hours that may alert: first 10 minutes of UTC hour 0, 6, 12 or 18. No state. */
function inAlertWindow(scheduledTime) {
  const t = Number.isFinite(scheduledTime) ? scheduledTime : Date.now()
  const d = new Date(t)
  return d.getUTCHours() % ALERT_EVERY_HOURS === 0 && d.getUTCMinutes() < ALERT_WINDOW_MINUTES
}

/** Plain-text Telegram message: what failed, the HTTP status, what to do. Logs the status only (see the header). */
async function alertTelegram(env, code, note) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) {
    console.warn('TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets are not set — no Telegram alert sent: ' +
      'npx wrangler@4 secret put TELEGRAM_BOT_TOKEN -c wrangler.keepwarm.jsonc ; ' +
      'npx wrangler@4 secret put TELEGRAM_CHAT_ID -c wrangler.keepwarm.jsonc')
    return
  }
  const text = `yq-keepwarm: the Supabase keep-alive read failed (keepalive_ping -> HTTP ${code}: ${note}). ` +
    'Open the Supabase dashboard and click Resume project if it is paused. ' +
    'This alert repeats every 6 hours while the read keeps failing.'
  try {
    const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, disable_web_page_preview: true }),
      signal: AbortSignal.timeout(TELEGRAM_TIMEOUT_MS),
    })
    if (res.ok) console.log(`telegram alert -> HTTP ${res.status}`)
    else console.error(`telegram alert -> HTTP ${res.status}`)
    try { await res.body?.cancel() } catch { /* only the status is read */ }
  } catch (e) {
    console.error(`telegram alert failed: ${errName(e)}`)   // the error's name only: never its text or the URL
  }
}

function errName(e) {
  return e && e.name ? String(e.name) : 'Error'
}

/** GET with a hard timeout. Logs "<path> -> HTTP <status> [<keys>]"; returns {status, note, body}. */
async function ping(url, headers, timeoutMs) {
  const path = new URL(url).pathname
  let res
  try {
    res = await fetch(url, { method: 'GET', headers, signal: AbortSignal.timeout(timeoutMs) })
  } catch (e) {
    const note = e && e.name === 'TimeoutError' ? `timeout after ${timeoutMs / 1000}s` : String(e && e.message || e)
    console.log(`${path} -> HTTP 000 (${note})`)
    return { status: 0, note, body: null }
  }
  let body = null
  let keys = '(non-json)'
  try {
    body = await res.json()
    keys = body && typeof body === 'object' && !Array.isArray(body) ? Object.keys(body).join(',') : typeof body
  } catch {
    body = null
  }
  console.log(`${path} -> HTTP ${res.status} [${keys}]`)
  return { status: res.status, note: res.statusText || '', body }
}
