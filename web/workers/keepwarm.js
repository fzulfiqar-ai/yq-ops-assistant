/**
 * yq-keepwarm — Cloudflare Worker cron (24-Sep-2026). Config and deploy notes: ../wrangler.keepwarm.jsonc.
 *
 * Two triggers, told apart by the cron string the runtime hands us:
 *   HEALTH_CRON     GET {API_URL}/health — keeps the free Render container inside its 15-minute idle
 *                   window during Bahrain business hours (a cold start costs 11-12 s per merchant).
 *   SCHEDULER_CRON  GET each SCHEDULER_PATHS entry with X-Agent-Key — the shop jobs (order alert
 *                   retries, unassigned + unconfirmed reminders, cleanup, stale-stock alert), the
 *                   daily agents and the agent reactions. Same method, path and header as
 *                   .github/workflows/shop-cron.yml, which keeps only its manual trigger now (its
 *                   schedule is commented out; the API's run_shop_jobs also holds a 10-minute lease,
 *                   so a stray second caller is skipped rather than doubled).
 *
 * Logging rule (same as shop-cron.yml): never the response body. It carries order numbers and
 * merchant/shop names; the status and the top-level keys say enough to tell "ran" from "did nothing".
 * A scheduler response with ok:false is logged as a warning with the failed job names only.
 * Nothing here caches: the API answers JSON without Cache-Control headers, which Cloudflare's
 * fetch() does not store.
 */
const HEALTH_CRON = '*/10 3-19 * * *'
const SCHEDULER_CRON = '*/15 * * * *'
const DEFAULT_API = 'https://yq-ops-assistant.onrender.com'
const HEALTH_TIMEOUT_MS = 90_000      // tolerates a genuine cold start (keepalive.yml: --max-time 90)
const SCHEDULER_TIMEOUT_MS = 120_000  // shop-cron.yml: --max-time 120

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(event.cron, env))
  },
  // No public surface (workers_dev off, no routes); this only answers a dashboard "Send request".
  async fetch() {
    return new Response('yq-keepwarm: cron only', { status: 404 })
  },
}

async function run(cron, env) {
  const api = String(env.API_URL || DEFAULT_API).replace(/\/+$/, '')
  if (cron === HEALTH_CRON) {
    const r = await ping(`${api}/health`, {}, HEALTH_TIMEOUT_MS)
    if (r.status !== 200) console.error(`/health -> HTTP ${r.status} (${r.note})`)
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
