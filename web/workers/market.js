/**
 * yq-marketplace edge Worker (release R6, 24-Sep-2026). Config and deploy: ../wrangler.market.jsonc.
 *
 * What reaches this script, and what never does (there is NO assets.run_worker_first — see the
 * wrangler config for why an array of any shape defeats the missing-chunk 404):
 *   - a path that IS a file           served by the asset layer, free and unlimited, no invocation —
 *                                     every JS chunk, font, icon, sw.js and version.json
 *   - a NAVIGATION to an unknown path index.html from the asset layer (single-page-application), free,
 *     (Sec-Fetch-Mode: navigate)      no invocation: assets_navigation_prefers_asset_serving, default
 *                                     since compatibility date 2025-04-01. That includes a human typing
 *                                     /api/market or a chunk URL into the address bar: they get the app,
 *                                     which is right — the app's own requests are never navigations
 *   - any other unknown path          the Worker: /api/market[?ref=] (fetch(), the prefetch script and
 *                                     the service worker all send Sec-Fetch-Mode: cors) → catalog();
 *                                     a missing chunk or font (a script/font fetch is not a navigation)
 *                                     → staticFile() below; anything else → the asset binding's own SPA
 *                                     answer, unchanged
 *
 *  1. /api/market[?ref=slug]  — the catalog, same-origin, through the Cache API. The origin is the free
 *     Render container: warm it answers in ~0.3 s, cold in 11–12 s (the audit's worst LCP was 40.8 s),
 *     and a merchant pays that on every visit while the catalog changes once a day. Here:
 *       fresh   age < max-age (60 s)                         → cached copy, X-Yq-Cache: HIT
 *       stale   age < max-age + stale-while-revalidate (600) → cached copy at once, origin refreshed
 *                                                              in the background, X-Yq-Cache: STALE
 *       older   up to stale-if-error (86400)                 → ask the origin, but give it ORIGIN_SOFT_MS
 *                                                              (3 s); a slow or failing origin means the
 *                                                              last good copy is served, X-Yq-Cache: ERROR-STALE,
 *                                                              a background refresh is started and the copy
 *                                                              enters a GRACE window (stale-while-revalidate
 *                                                              long): the visitors behind that one are
 *                                                              answered at once instead of each waiting 3 s
 *       nothing cached                                       → wait for the origin (a cold start included)
 *     The three numbers come from the origin's own Cache-Control (app/shop_api.py MARKET_CACHE), with
 *     these defaults. Only a 200 application/json that parses is ever stored; a non-JSON 200 is passed
 *     through untouched (never relabelled as the catalog).
 *     What the last good copy covers, and what it must not: a 5xx, a 429 and the synthetic 504 (timeout,
 *     unreachable) are origin trouble → the copy is served (RFC 5861 stale-if-error). A 404 or 410 is the
 *     origin's ANSWER — the owner closed the marketplace (shop_market_enabled=0) — so it is authoritative:
 *     the copy is deleted and the 404 forwarded, in the background refresh as well, and the store is
 *     closed at the edge within max-age of the switch, as it was before the edge existed. Any other 4xx
 *     is forwarded, the copy kept. The stored copy keeps the origin's ETag: revalidation sends
 *     If-None-Match, and a 304 just re-stamps the copy (no body crosses the wire); a browser
 *     If-None-Match that matches gets a 304 from here without touching the origin.
 *     ?ref= is validated against the slug shape and lowercased; junk shares the no-ref copy. Whether a
 *     well-formed slug is a rep is the ORIGIN's call — the public payload carries no referral codes
 *     (R3: rep ids and names only), so the edge has no list to check against and must not guess. The
 *     payload's own `ref` decides: a rep's answer is stored under the slug's key; any other slug's
 *     answer is the no-ref catalog, stored under the no-ref key, and the slug's key gets a small
 *     NO-REP VERDICT (X-Yq-No-Rep, never served). While the verdict stands the slug is a HIT on the
 *     no-ref copy with no origin call; once it is NO_REP_TTL old the slug is still answered at once
 *     and the origin is asked again in the background, so a rep added today is recognised within
 *     NO_REP_TTL of their first link visit. Cost of an invented slug: one origin call per NO_REP_TTL
 *     (the origin serves it from its in-process no-ref entry; past its 60/min per-IP limit it answers
 *     429, which a real rep's first visitor gets passed through — the app then asks the API directly).
 *     (Before 25-Sep-2026 the edge folded every slug the no-ref copy's salesmen[].referral_code did not
 *     list; once R3 removed those codes it folded every rep link, and the app, seeing ref: null,
 *     dropped the rep and bounced /{slug} to /.)
 *
 *  2. /assets/* and /fonts/*  — reached only for a MISSING file (see above): it becomes a real 404 with
 *     Cache-Control: no-store. The asset layer's SPA answer for an unknown path is index.html + HTTP 200 +
 *     the immutable header of the path pattern, so after a deploy a tab holding the old index.html
 *     loaded HTML as a JS chunk — and Cloudflare's edge then cached that HTML under the .js URL for a
 *     year (audit D-10). A real 404 lets vite:preloadError reload once (main.market.tsx) and nothing
 *     wrong is cached.
 *
 *  3. everything else → the asset binding, unchanged (SPA fallback, public/_headers).
 *
 * Budget: Workers Free = 100,000 requests/day, and only Worker invocations count: one /api/market per
 * page load (the service worker's network-first fetch), a missing chunk after a deploy, a non-navigation
 * fetch of an unknown path. At the audit's ~150 visits/day that is < 1 % of the budget. THE CLIFF, so it
 * is written down: past the cap, Cloudflare answers every invocation with a 429 until the daily reset —
 * /api/market then 429s and the app falls back to the API's /public/market by itself (marketApi.ts,
 * catalog-prefetch.js), a missing chunk 429s instead of 404ing and vite:preloadError still reloads
 * once; the files, the fonts and every navigation are the asset layer's and stay up. (Before this
 * revision /api/*, /assets/* and /fonts/* were in run_worker_first and every chunk was an invocation:
 * ~5k first visits, or one loop over /assets/x.js, would have taken the whole store down until
 * midnight UTC.)
 * The Cache API is a no-op on workers.dev (wrangler 4.138's `wrangler dev` does keep entries, older ones
 * did not); it works on the custom domain, and the code never depends on a hit.
 *
 * Nothing here logs a body: the catalog carries prices and the rep card, not PII, but the rule from
 * keepwarm.js holds — status and cache state only. console.log fires only on an origin timeout/error;
 * Workers Logs (observability) additionally records one invocation event per request that reaches the
 * script, which is the same small set listed under Budget.
 */
const ORIGIN = 'https://yq-ops-assistant.onrender.com'
const ORIGIN_PATH = '/public/market'
const EDGE_PATH = '/api/market'
const DEFAULTS = { fresh: 60, swr: 600, sie: 86400 }
const ORIGIN_SOFT_MS = 3000   // how long a merchant waits for the origin while a last good copy exists
const ORIGIN_HARD_MS = 60000  // with no copy at all: long enough for a Render cold start (11–12 s measured)
const ORIGIN_BG_MS = 25000    // a background refresh: waitUntil work is cut ~30 s after the response
const SLUG = /^[a-z0-9][a-z0-9-]{1,31}$/
const STORED_AT = 'x-yq-stored-at'
const STORED_CC = 'x-yq-origin-cache-control'
const NO_REP = 'x-yq-no-rep'             // on a slug's key: the origin answered this slug with ref: null
const NO_REP_TTL = 600                   // seconds a no-rep verdict stands before the origin is asked again
const GRACE_UNTIL = 'x-yq-grace-until'   // set after a soft failure: serve at once until then
const SYNTHETIC = 'x-yq-origin'          // on the Worker's own 504: 'timeout' | 'error'

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
  // a real file under /assets or /fonts is js/css/woff2/svg/png/webp — never HTML; and whatever the
  // asset layer says for a file it does not have (index.html under SPA handling, or its own 404 that
  // the /assets/* header rule may have stamped immutable) becomes this one, uncacheable answer
  if (res.status !== 200 || /text\/html/i.test(type)) {
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
  const cache = caches.default
  let ref = normalizeRef(url.searchParams.get('ref'))
  let cached = null
  if (ref) {
    const own = await match(cache, keyFor(url.origin, ref))
    if (own && own.headers.get(NO_REP)) {
      // the origin said this slug is nobody: the no-ref copy answers it; an old verdict is re-asked
      // in the background, never on the visitor's time
      if (ageOf(own) >= NO_REP_TTL) ctx.waitUntil(recheckSlug(cache, url.origin, ref))
      ref = ''
    } else {
      cached = own
    }
  }
  const key = keyFor(url.origin, ref)
  const originUrl = originFor(ref)
  if (!ref) cached = await match(cache, key)

  if (cached) {
    const rules = parseCacheControl(cached.headers.get(STORED_CC))
    const age = ageOf(cached)
    const grace = Number(cached.headers.get(GRACE_UNTIL) || 0) > Date.now()
    if (age < rules.fresh) return reply(request, cached, 'HIT', age)
    if (age < rules.fresh + rules.swr) {
      // a clone: the reply below streams `cached`'s body while the refresh may re-stamp it
      ctx.waitUntil(revalidate(cache, url.origin, ref, cached.clone(), ORIGIN_BG_MS))
      return reply(request, cached, 'STALE', age)
    }
    if (grace && age < rules.sie) {
      // a visitor just paid the soft timeout and a refresh is under way: nobody else waits for it
      ctx.waitUntil(revalidate(cache, url.origin, ref, cached.clone(), ORIGIN_BG_MS))
      return reply(request, cached, 'ERROR-STALE', age)
    }
    // past the revalidation window: give the origin a short chance, then fall back to the copy
    const fresh = await fromOrigin(originUrl, cached.headers.get('etag'), ORIGIN_SOFT_MS)
    if (fresh.status === 200) {
      const stored = await store(cache, url.origin, ref, fresh)
      return stored ? reply(request, stored, 'MISS', 0) : passThrough(fresh)
    }
    if (fresh.status === 304) {
      const stored = await restamp(cache, key, cached)
      return reply(request, stored, 'REVALIDATED', 0)
    }
    if (isGone(fresh.status)) {
      // the origin's answer, not its trouble: the marketplace is closed — the copy goes with it
      await remove(cache, key)
      return passThrough(fresh)
    }
    if (isOriginTrouble(fresh) && age < rules.sie) {
      if (fresh.status !== 429) {
        // the origin is cold or failing, not rate-limiting us: refresh in the background and open the
        // grace window first, so the next visitor finds it whatever the refresh does
        const marked = await mark(cache, key, cached.clone(), rules.swr)
        ctx.waitUntil(revalidate(cache, url.origin, ref, marked, ORIGIN_BG_MS))
      }
      return reply(request, cached, 'ERROR-STALE', age)
    }
    return passThrough(fresh)
  }

  const fresh = await fromOrigin(originUrl, null, ORIGIN_HARD_MS)
  if (fresh.status !== 200) return passThrough(fresh)
  const stored = await store(cache, url.origin, ref, fresh)
  return stored ? reply(request, stored, 'MISS', 0) : passThrough(fresh)
}

function normalizeRef(raw) {
  const s = String(raw || '').trim().toLowerCase()
  return SLUG.test(s) ? s : ''
}

function keyFor(origin, ref) {
  return new Request(`${origin}${EDGE_PATH}${ref ? `?ref=${ref}` : ''}`, { method: 'GET' })
}

function originFor(ref) {
  return `${ORIGIN}${ORIGIN_PATH}${ref ? `?ref=${encodeURIComponent(ref)}` : ''}`
}

/** Seconds since a stored copy (or verdict) was written. */
function ageOf(stored) {
  return Math.max(0, Math.floor((Date.now() - Number(stored.headers.get(STORED_AT) || 0)) / 1000))
}

/** The small marker on a slug's key that says "the origin answered ref: null" — never served. */
async function putNoRep(cache, origin, ref) {
  const marker = new Response('', {
    status: 200,
    headers: { [NO_REP]: '1', [STORED_AT]: String(Date.now()), 'cache-control': `public, s-maxage=${DEFAULTS.sie}` },
  })
  try {
    await cache.put(keyFor(origin, ref), marker)
  } catch {
    /* no cache: the slug is simply asked again next time */
  }
}

/**
 * An old no-rep verdict: re-stamp it first (the visitors behind this one do not start their own
 * check), then ask the origin. store() files the answer — under the slug's own key if the slug is a
 * rep now, else as the no-ref copy with a fresh verdict. Anything but a 200 leaves the verdict as is.
 */
async function recheckSlug(cache, origin, ref) {
  await putNoRep(cache, origin, ref)
  const fresh = await fromOrigin(originFor(ref), null, ORIGIN_BG_MS)
  if (fresh.status === 200) await store(cache, origin, ref, fresh)
}

async function match(cache, key) {
  try {
    return (await cache.match(key)) || null
  } catch {
    return null
  }
}

async function remove(cache, key) {
  try {
    await cache.delete(key)
  } catch {
    /* no cache here: nothing to remove */
  }
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

/** 404 / 410: the origin says there is no catalog to serve — the kill switch, not an outage. */
function isGone(status) {
  return status === 404 || status === 410
}

/** What the last good copy is for: a 5xx, a 429, or the Worker's own 504 (timeout / unreachable). */
function isOriginTrouble(res) {
  return res.status >= 500 || res.status === 429 || Boolean(res.headers.get(SYNTHETIC))
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
      headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store', [SYNTHETIC]: timeout ? 'timeout' : 'error' },
    })
  }
}

/**
 * Background refresh of a copy: 200 → replace, 304 → re-stamp, 404/410 → the copy is deleted (the
 * marketplace closed while the copy was in its stale window), anything else → keep the copy.
 */
async function revalidate(cache, origin, ref, cached, timeoutMs) {
  const key = keyFor(origin, ref)
  const fresh = await fromOrigin(originFor(ref), cached.headers.get('etag'), timeoutMs)
  if (fresh.status === 200) await store(cache, origin, ref, fresh)
  else if (fresh.status === 304) await restamp(cache, key, cached)
  else if (isGone(fresh.status)) await remove(cache, key)
}

/**
 * Store a 200 origin answer. Returns the stored Response (a fresh clone for the caller to serve), or
 * null when the answer is not the catalog (not JSON, or JSON that does not parse): the caller then
 * passes the origin's answer through as it is, and nothing is kept. The payload's own `ref` decides
 * the key: an unknown slug's answer IS the no-ref catalog and is stored as that, and the slug's own
 * key gets the no-rep verdict (which also retires the copy of a rep who was deactivated).
 */
async function store(cache, origin, ref, res) {
  const type = res.headers.get('content-type') || ''
  if (!/application\/json/i.test(type)) return null
  const body = await res.clone().arrayBuffer()
  let data
  try {
    data = JSON.parse(new TextDecoder().decode(body))
  } catch {
    return null
  }
  if (!data || typeof data !== 'object') return null
  const payloadRef = data.ref && typeof data.ref === 'object' ? normalizeRef(data.ref.referral_code) : ''
  const isRep = Boolean(ref) && payloadRef === ref
  const key = keyFor(origin, isRep ? ref : '')
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
  if (ref && !isRep) await putNoRep(cache, origin, ref)
  return stored
}

/** The origin said 304: the copy is current again. Same body, new timestamp, grace over. */
async function restamp(cache, key, cached) {
  const headers = new Headers(cached.headers)
  headers.set(STORED_AT, String(Date.now()))
  headers.delete(GRACE_UNTIL)
  const stored = new Response(await cached.arrayBuffer(), { status: 200, headers })
  try {
    await cache.put(key, stored.clone())
  } catch {
    /* as above */
  }
  return stored
}

/** After a soft failure: the same copy, same age, with a grace window of `seconds` written on it. */
async function mark(cache, key, cached, seconds) {
  const headers = new Headers(cached.headers)
  headers.set(GRACE_UNTIL, String(Date.now() + seconds * 1000))
  const marked = new Response(await cached.arrayBuffer(), { status: 200, headers })
  try {
    await cache.put(key, marked.clone())
  } catch {
    /* as above */
  }
  return marked
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

/** An origin answer that is not stored (a non-200, a non-JSON 200, the synthetic 504): forwarded with its status and type. */
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
