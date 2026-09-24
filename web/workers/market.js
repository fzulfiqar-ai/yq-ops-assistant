/**
 * yq-marketplace edge Worker (release R6, 24-Sep-2026). Config and deploy: ../wrangler.market.jsonc.
 *
 * Three jobs, and nothing else — every other path is still a plain static-asset request that never
 * invokes this script (assets.run_worker_first lists only /api/*, /assets/* and /fonts/*):
 *
 *  1. /api/market[?ref=slug]  — the catalog, same-origin, through the Cache API. The origin is the free
 *     Render container: warm it answers in ~0.3 s, cold in 11–12 s (the audit's worst LCP was 40.8 s),
 *     and a merchant pays that on every visit while the catalog changes once a day. Here:
 *       fresh   age < max-age (60 s)                         → cached copy, X-Yq-Cache: HIT
 *       stale   age < max-age + stale-while-revalidate (600) → cached copy at once, origin refreshed
 *                                                              in the background, X-Yq-Cache: STALE
 *       older   up to stale-if-error (86400)                 → ask the origin, but give it ORIGIN_SOFT_MS
 *                                                              (3 s); a slow or failing origin means the
 *                                                              last good copy is served, X-Yq-Cache: ERROR-STALE
 *       nothing cached                                       → wait for the origin (a cold start included)
 *     The three numbers come from the origin's own Cache-Control (app/shop_api.py MARKET_CACHE), with
 *     these defaults. Only a 200 application/json is ever stored; a 404 (marketplace closed), a 429 or
 *     a 5xx is passed through and never cached. The stored copy keeps the origin's ETag: revalidation
 *     sends If-None-Match, and a 304 just re-stamps the copy (no body crosses the wire); a browser
 *     If-None-Match that matches gets a 304 from here without touching the origin.
 *     ?ref= is validated against the slug shape and lowercased, so a junk value shares the no-ref
 *     copy (as it does on the API) and cannot grow the cache or burn origin calls.
 *
 *  2. /assets/* and /fonts/*  — a MISSING file becomes a real 404 with Cache-Control: no-store. The
 *     static layer answers every unknown path with index.html + HTTP 200 + the immutable cache header
 *     (single-page-application), so after a deploy a tab holding the old index.html loaded HTML as a
 *     JS chunk — and Cloudflare's edge then cached that HTML under the .js URL for a year (audit D-10).
 *     A real 404 lets vite:preloadError reload once (main.market.tsx) and nothing wrong is cached.
 *
 *  3. everything else → the asset binding, unchanged (SPA fallback, public/_headers).
 *
 * Budget: Workers Free = 100,000 requests/day. Only the three prefixes count; a page load is one
 * /api/market plus the chunks the browser does not already hold (immutable + SW precache), so a
 * first visit ≈ 15–20 requests and a repeat visit ≈ 1. At the audit's ~150 visits/day that is < 3 %
 * of the budget. The Cache API is a no-op on workers.dev and in `wrangler dev`; it works on the
 * custom domain, and the code never depends on a hit.
 *
 * Nothing here logs a body: the catalog carries prices and the rep card, not PII, but the rule from
 * keepwarm.js holds — status and cache state only.
 */
const ORIGIN = 'https://yq-ops-assistant.onrender.com'
const ORIGIN_PATH = '/public/market'
const EDGE_PATH = '/api/market'
const DEFAULTS = { fresh: 60, swr: 600, sie: 86400 }
const ORIGIN_SOFT_MS = 3000   // how long a merchant waits for the origin while a last good copy exists
const ORIGIN_HARD_MS = 60000  // with no copy at all: long enough for a Render cold start (11–12 s measured)
const SLUG = /^[a-z0-9][a-z0-9-]{1,31}$/
const STORED_AT = 'x-yq-stored-at'
const STORED_CC = 'x-yq-origin-cache-control'

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url)
    const path = url.pathname
    if (path === EDGE_PATH || path === EDGE_PATH + '/') return catalog(request, url, env, ctx)
    if (path.startsWith('/assets/') || path.startsWith('/fonts/')) return staticFile(request, env)
    return env.ASSETS.fetch(request)
  },
}

/* ── 2. missing chunks are a 404, never index.html ─────────────────────────────────────────────── */

async function staticFile(request, env) {
  const res = await env.ASSETS.fetch(request)
  const type = res.headers.get('content-type') || ''
  // a real file under /assets or /fonts is js/css/woff2/svg/png/webp — never HTML
  if (res.status === 200 && /text\/html/i.test(type)) {
    return new Response('Not found', {
      status: 404,
      headers: {
        'content-type': 'text/plain; charset=utf-8',
        'cache-control': 'no-store',
        'x-content-type-options': 'nosniff',
      },
    })
  }
  return res
}

/* ── 1. the catalog through the Cache API ──────────────────────────────────────────────────────── */

async function catalog(request, url, env, ctx) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return new Response('Method not allowed', { status: 405, headers: { allow: 'GET, HEAD', 'cache-control': 'no-store' } })
  }
  const ref = normalizeRef(url.searchParams.get('ref'))
  const key = new Request(`${url.origin}${EDGE_PATH}${ref ? `?ref=${ref}` : ''}`, { method: 'GET' })
  const originUrl = `${ORIGIN}${ORIGIN_PATH}${ref ? `?ref=${encodeURIComponent(ref)}` : ''}`
  const cache = caches.default
  let cached = null
  try {
    cached = (await cache.match(key)) || null
  } catch {
    cached = null
  }

  if (cached) {
    const rules = parseCacheControl(cached.headers.get(STORED_CC))
    const age = Math.max(0, Math.floor((Date.now() - Number(cached.headers.get(STORED_AT) || 0)) / 1000))
    if (age < rules.fresh) return reply(request, cached, 'HIT', age)
    if (age < rules.fresh + rules.swr) {
      // a clone: the reply below streams `cached`'s body while the refresh may re-stamp it
      ctx.waitUntil(revalidate(cache, key, originUrl, cached.clone()))
      return reply(request, cached, 'STALE', age)
    }
    // past the revalidation window: give the origin a short chance, then fall back to the copy
    const fresh = await fromOrigin(originUrl, cached.headers.get('etag'), ORIGIN_SOFT_MS)
    if (fresh.status === 200) {
      const stored = await store(cache, key, fresh)
      return reply(request, stored, 'MISS', 0)
    }
    if (fresh.status === 304) {
      const stored = await restamp(cache, key, cached)
      return reply(request, stored, 'REVALIDATED', 0)
    }
    if (age < rules.sie) return reply(request, cached, 'ERROR-STALE', age)
    return passThrough(fresh)
  }

  const fresh = await fromOrigin(originUrl, null, ORIGIN_HARD_MS)
  if (fresh.status !== 200) return passThrough(fresh)
  const stored = await store(cache, key, fresh)
  return reply(request, stored, 'MISS', 0)
}

function normalizeRef(raw) {
  const s = String(raw || '').trim().toLowerCase()
  return SLUG.test(s) ? s : ''
}

/** {fresh, swr, sie} in seconds from a Cache-Control value; the API's numbers when it says nothing. */
function parseCacheControl(value) {
  const out = { ...DEFAULTS }
  for (const part of String(value || '').split(',')) {
    const [k, v] = part.trim().toLowerCase().split('=')
    const n = Number(v)
    if (!Number.isFinite(n) || n < 0) continue
    if (k === 'max-age' || k === 's-maxage') out.fresh = n
    else if (k === 'stale-while-revalidate') out.swr = n
    else if (k === 'stale-if-error') out.sie = n
  }
  return out
}

/**
 * GET the origin with a hard timeout. Resolves to a Response in every case: the origin's own
 * (200 / 304 / 404 / 429 / 5xx) or a synthetic 504 for a timeout / network error. `cache: 'no-store'`
 * keeps Cloudflare's transparent fetch cache out of it — this Worker is the cache.
 */
async function fromOrigin(originUrl, etag, timeoutMs) {
  const headers = { accept: 'application/json', 'user-agent': 'yq-edge/1 (+https://yqmarketplace.com)' }
  if (etag) headers['if-none-match'] = etag
  try {
    return await fetch(originUrl, { method: 'GET', headers, cache: 'no-store', signal: AbortSignal.timeout(timeoutMs) })
  } catch (e) {
    const timeout = e && e.name === 'TimeoutError'
    console.log(`origin ${timeout ? 'timeout' : 'error'} after ${timeoutMs} ms`)
    return new Response(timeout ? 'Origin timeout' : 'Origin unreachable', {
      status: 504,
      headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' },
    })
  }
}

/** Background refresh of a stale copy: 200 → replace, 304 → re-stamp, anything else → keep the copy. */
async function revalidate(cache, key, originUrl, cached) {
  const fresh = await fromOrigin(originUrl, cached.headers.get('etag'), ORIGIN_HARD_MS)
  if (fresh.status === 200) await store(cache, key, fresh)
  else if (fresh.status === 304) await restamp(cache, key, cached)
}

/** Store a 200 origin answer. Returns the stored Response (a fresh clone for the caller to serve). */
async function store(cache, key, res) {
  const type = res.headers.get('content-type') || ''
  if (!/application\/json/i.test(type)) return res // not the catalog — serve, never keep
  const body = await res.arrayBuffer()
  const etag = res.headers.get('etag') || (await weakEtag(body))
  const headers = new Headers({
    'content-type': 'application/json',
    etag,
    [STORED_AT]: String(Date.now()),
    [STORED_CC]: res.headers.get('cache-control') || '',
    // the Cache API evicts at s-maxage: keep the copy for the whole stale-if-error window and let
    // the age rules above decide what "fresh" means
    'cache-control': `public, s-maxage=${parseCacheControl(res.headers.get('cache-control')).sie}`,
  })
  const stored = new Response(body, { status: 200, headers })
  try {
    await cache.put(key, stored.clone())
  } catch {
    /* workers.dev / local preview: no cache, still served */
  }
  return stored
}

/** The origin said 304: the copy is current again. Same body, new timestamp. */
async function restamp(cache, key, cached) {
  const headers = new Headers(cached.headers)
  headers.set(STORED_AT, String(Date.now()))
  const stored = new Response(await cached.arrayBuffer(), { status: 200, headers })
  try {
    await cache.put(key, stored.clone())
  } catch {
    /* as above */
  }
  return stored
}

async function weakEtag(body) {
  const digest = await crypto.subtle.digest('SHA-1', body)
  const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
  return `W/"${hex.slice(0, 24)}"`
}

/** The answer a merchant sees: the origin's cache policy, the ETag, an honest Age, and the cache state. */
function reply(request, stored, state, age) {
  const etag = stored.headers.get('etag') || ''
  const headers = new Headers({
    'content-type': 'application/json',
    'cache-control': stored.headers.get(STORED_CC) || `public, max-age=${DEFAULTS.fresh}, stale-while-revalidate=${DEFAULTS.swr}, stale-if-error=${DEFAULTS.sie}`,
    age: String(age),
    'x-yq-cache': state,
    'x-robots-tag': 'noindex, nofollow',
    'x-content-type-options': 'nosniff',
  })
  if (etag) headers.set('etag', etag)
  if (etag && etagMatches(request.headers.get('if-none-match'), etag)) {
    return new Response(null, { status: 304, headers })
  }
  if (request.method === 'HEAD') return new Response(null, { status: 200, headers })
  return new Response(stored.body, { status: 200, headers })
}

/** RFC 7232 weak comparison, the same rule as the API's _etag_matches. */
function etagMatches(header, etag) {
  if (!header) return false
  const core = (t) => (t.startsWith('W/') ? t.slice(2) : t)
  const tags = header.split(',').map((t) => t.trim()).filter(Boolean)
  return tags.includes('*') || tags.some((t) => core(t) === core(etag))
}

/** A non-200 origin answer (or the synthetic 504): forwarded with its status, never stored. */
function passThrough(res) {
  const headers = new Headers({
    'content-type': res.headers.get('content-type') || 'text/plain; charset=utf-8',
    'cache-control': 'no-store',
    'x-yq-cache': 'BYPASS',
    'x-robots-tag': 'noindex, nofollow',
  })
  const retry = res.headers.get('retry-after')
  if (retry) headers.set('retry-after', retry)
  return new Response(res.body, { status: res.status, headers })
}
