/**
 * The edge catalog Worker (web/workers/market.js), exercised in plain node — no wrangler, no network,
 * no new deps. Same shape as soldout_order_test.mjs: exit 1 on any failure; tests/test_r6_speed.py runs
 * it and reports the result (SKIP when node is missing).
 *
 *   node web/scripts/market_worker_test.mjs        (from the repo root or from web/)
 *
 * The Workers runtime globals the script uses (Request/Response/Headers, AbortSignal.timeout,
 * crypto.subtle) exist in Node 20+; `caches.default` and `fetch` are replaced by fakes here so the
 * cache states, the origin calls and the timeouts can be asserted exactly.
 */
import worker from '../workers/market.js'

let failures = 0
const check = (cond, msg) => {
  if (cond) console.log(`  ok    ${msg}`)
  else {
    failures += 1
    console.log(`  FAIL  ${msg}`)
  }
}

/* ── fakes ─────────────────────────────────────────────────────────────────────────────────────── */

class FakeCache {
  constructor() {
    this.map = new Map()
  }
  async match(req) {
    const r = this.map.get(req.url)
    return r ? r.clone() : undefined
  }
  async put(req, res) {
    this.map.set(req.url, res)
  }
  /** move a stored copy back in time by `seconds` (what the Worker reads as its age) */
  async age(url, seconds) {
    const res = this.map.get(url)
    const h = new Headers(res.headers)
    h.set('x-yq-stored-at', String(Date.now() - seconds * 1000))
    this.map.set(url, new Response(await res.clone().arrayBuffer(), { status: 200, headers: h }))
  }
}

const ORIGIN_CC = 'public, max-age=60, stale-while-revalidate=600, stale-if-error=86400'
const catalogJson = (n) => JSON.stringify({ items: [{ item_code: `X0${n}` }], n })
const originOk = (n, extra = {}) =>
  new Response(catalogJson(n), {
    status: 200,
    headers: { 'content-type': 'application/json', 'cache-control': ORIGIN_CC, etag: `W/"v${n}"`, ...extra },
  })

let originCalls = []
let originImpl = async () => originOk(1)
globalThis.fetch = async (url, init) => {
  originCalls.push({ url: String(url), init: init || {} })
  return originImpl(String(url), init || {})
}
globalThis.caches = { default: new FakeCache() }
const reset = () => {
  globalThis.caches = { default: new FakeCache() }
  originCalls = []
}

const assets = {
  fetch: async (req) => {
    const u = new URL(req.url)
    if (u.pathname === '/assets/ok.js') return new Response('ok()', { status: 200, headers: { 'content-type': 'text/javascript', 'cache-control': 'public, max-age=31536000, immutable' } })
    if (u.pathname === '/fonts/ok.woff2') return new Response('wOF2', { status: 200, headers: { 'content-type': 'font/woff2', 'cache-control': 'public, max-age=31536000, immutable' } })
    // the static layer's SPA fallback: index.html, 200, and the immutable header the path pattern gives it
    return new Response('<!doctype html><title>YQ</title>', { status: 200, headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'public, max-age=31536000, immutable' } })
  },
}
const env = { ASSETS: assets }
let waits = []
const ctx = { waitUntil: (p) => waits.push(p) }
const call = (path, headers = {}, method = 'GET') => worker.fetch(new Request('https://yqmarketplace.com' + path, { method, headers }), env, ctx)
const KEY = 'https://yqmarketplace.com/api/market'

/* ── 1. miss → hit → 304 ───────────────────────────────────────────────────────────────────────── */
console.log('miss, hit, conditional')
{
  reset()
  const r1 = await call('/api/market')
  check(r1.status === 200 && r1.headers.get('x-yq-cache') === 'MISS', 'first request is a MISS answered by the origin')
  check((await r1.json()).n === 1, 'the origin body is passed through')
  check(originCalls.length === 1 && originCalls[0].url === 'https://yq-ops-assistant.onrender.com/public/market', 'the origin URL is /public/market with no ref')
  check(originCalls[0].init.cache === 'no-store', "the subrequest bypasses Cloudflare's own fetch cache")
  check(r1.headers.get('cache-control') === ORIGIN_CC, "the merchant sees the origin's cache policy")
  check(r1.headers.get('etag') === 'W/"v1"', "the origin's ETag is forwarded")
  check(r1.headers.get('x-robots-tag') === 'noindex, nofollow', 'never indexed')
  check(globalThis.caches.default.map.has(KEY), 'the 200 is stored under the same-origin key')
  const stored = globalThis.caches.default.map.get(KEY)
  check(/s-maxage=86400/.test(stored.headers.get('cache-control')), 'the stored copy lives for the stale-if-error window (Cache API TTL = s-maxage)')

  const r2 = await call('/api/market')
  check(r2.status === 200 && r2.headers.get('x-yq-cache') === 'HIT' && originCalls.length === 1, 'second request is a HIT with no origin call')
  check(Number(r2.headers.get('age')) >= 0, 'Age is reported')

  const r3 = await call('/api/market', { 'if-none-match': 'W/"v1"' })
  check(r3.status === 304 && (await r3.text()) === '', 'If-None-Match with the ETag is a bodiless 304')
  const r4 = await call('/api/market', { 'if-none-match': '"v1"' })
  check(r4.status === 304, 'weak comparison: a strong tag with the same core matches')
  const r5 = await call('/api/market', { 'if-none-match': 'W/"other"' })
  check(r5.status === 200 && originCalls.length === 1, 'a different tag gets the body — still from the cache')

  const h = await call('/api/market', {}, 'HEAD')
  check(h.status === 200 && (await h.text()) === '' && h.headers.get('etag') === 'W/"v1"', 'HEAD answers headers only')
  const p = await call('/api/market', {}, 'POST')
  check(p.status === 405, 'POST is 405 (the quote and order routes never go through the edge)')
}

/* ── 2. ref handling ───────────────────────────────────────────────────────────────────────────── */
console.log('ref')
{
  reset()
  await call('/api/market?ref=Furqan')
  check(originCalls[0].url.endsWith('/public/market?ref=furqan'), 'a slug is lowercased before it reaches the origin')
  check(globalThis.caches.default.map.has(KEY + '?ref=furqan'), 'each slug has its own copy')
  const junk = await call('/api/market?ref=%3Cscript%3E')
  check(originCalls.length === 2 && originCalls[1].url.endsWith('/public/market') && junk.status === 200, 'a junk ref shares the no-ref copy (one origin call, no new key)')
  const again = await call('/api/market?ref=%21%21')
  check(again.headers.get('x-yq-cache') === 'HIT' && originCalls.length === 2, 'junk refs cannot grow the cache or burn origin calls')
}

/* ── 3. stale-while-revalidate ─────────────────────────────────────────────────────────────────── */
console.log('stale-while-revalidate')
{
  reset()
  waits = []
  await call('/api/market')
  await globalThis.caches.default.age(KEY, 120)
  originImpl = async (url, init) => (init.headers['if-none-match'] === 'W/"v1"' ? new Response(null, { status: 304 }) : originOk(2))
  const r = await call('/api/market')
  check(r.status === 200 && r.headers.get('x-yq-cache') === 'STALE' && (await r.json()).n === 1, 'past 60 s the stale copy is served at once')
  check(Number(r.headers.get('age')) >= 120, 'with its real age')
  check(waits.length === 1, 'and a background revalidation is scheduled')
  await Promise.all(waits)
  check(originCalls.length === 2 && originCalls[1].init.headers['if-none-match'] === 'W/"v1"', 'the revalidation sends If-None-Match with the stored ETag')
  const r2 = await call('/api/market')
  check(r2.headers.get('x-yq-cache') === 'HIT' && (await r2.json()).n === 1, 'a 304 re-stamps the copy: fresh again, same body')

  await globalThis.caches.default.age(KEY, 120)
  originImpl = async () => originOk(2)
  waits = []
  await call('/api/market')
  await Promise.all(waits)
  const r3 = await call('/api/market')
  check(r3.headers.get('x-yq-cache') === 'HIT' && (await r3.json()).n === 2 && r3.headers.get('etag') === 'W/"v2"', 'a 200 replaces the copy')
}

/* ── 4. past the window: origin first, last good copy on error or timeout ──────────────────────── */
console.log('stale-if-error')
{
  reset()
  originImpl = async () => originOk(1)
  await call('/api/market')
  await globalThis.caches.default.age(KEY, 700) // past 60 + 600
  originImpl = async () => {
    throw new TypeError('fetch failed')
  }
  const r = await call('/api/market')
  check(r.status === 200 && r.headers.get('x-yq-cache') === 'ERROR-STALE' && (await r.json()).n === 1, 'origin unreachable → the last good copy, marked ERROR-STALE')

  originImpl = async () => new Response('boom', { status: 503, headers: { 'content-type': 'text/plain' } })
  const r5 = await call('/api/market')
  check(r5.status === 200 && r5.headers.get('x-yq-cache') === 'ERROR-STALE', 'origin 5xx → the last good copy')

  // the 3 s soft timeout: an origin that never answers must not hold the merchant longer than that.
  // Node unrefs AbortSignal.timeout's timer (the Workers runtime does not), so a ref'd timer keeps
  // the event loop alive until the Worker's own timeout fires.
  originImpl = (url, init) =>
    new Promise((_, reject) => {
      init.signal.addEventListener('abort', () => reject(Object.assign(new Error('timeout'), { name: 'TimeoutError' })))
    })
  const keep = setTimeout(() => {}, 10000)
  const t0 = Date.now()
  const r2 = await call('/api/market')
  const took = Date.now() - t0
  clearTimeout(keep)
  check(r2.status === 200 && r2.headers.get('x-yq-cache') === 'ERROR-STALE', 'origin slower than 3 s → the last good copy')
  check(took >= 2500 && took < 6000, `after the soft timeout (${took} ms), not the hard one`)

  originImpl = async () => originOk(3)
  const r3 = await call('/api/market')
  check(r3.headers.get('x-yq-cache') === 'MISS' && (await r3.json()).n === 3, 'origin back → fresh copy, MISS')

  await globalThis.caches.default.age(KEY, 90000) // past stale-if-error
  originImpl = async () => {
    throw new TypeError('fetch failed')
  }
  const r4 = await call('/api/market')
  check(r4.status === 504 && r4.headers.get('cache-control') === 'no-store', 'beyond stale-if-error the failure is passed through, uncached')
}

/* ── 5. never cache a non-200 ──────────────────────────────────────────────────────────────────── */
console.log('non-200')
{
  reset()
  originImpl = async () => new Response(JSON.stringify({ detail: 'The marketplace is not open.' }), { status: 404, headers: { 'content-type': 'application/json' } })
  const r = await call('/api/market')
  check(r.status === 404 && r.headers.get('x-yq-cache') === 'BYPASS' && r.headers.get('cache-control') === 'no-store', 'a 404 (marketplace closed) is forwarded with no-store')
  check(globalThis.caches.default.map.size === 0, 'and never stored')
  originImpl = async () => new Response('slow down', { status: 429, headers: { 'retry-after': '30' } })
  const r2 = await call('/api/market')
  check(r2.status === 429 && r2.headers.get('retry-after') === '30' && globalThis.caches.default.map.size === 0, 'a 429 keeps Retry-After and is never stored')
  originImpl = async () => originOk(1, { etag: '' })
  const r3 = await call('/api/market')
  check(/^W\/"[0-9a-f]{24}"$/.test(r3.headers.get('etag') || ''), 'an origin answer without an ETag gets a weak one computed at the edge')
}

/* ── 6. origin numbers drive the states ────────────────────────────────────────────────────────── */
console.log("the origin's Cache-Control")
{
  reset()
  const cc = 'public, max-age=10, stale-while-revalidate=20, stale-if-error=100'
  originImpl = async () => originOk(1, { 'cache-control': cc })
  const r = await call('/api/market')
  check(r.headers.get('cache-control') === cc, 'forwarded as given')
  await globalThis.caches.default.age(KEY, 15)
  waits = []
  check((await call('/api/market')).headers.get('x-yq-cache') === 'STALE', 'max-age=10: stale at 15 s')
  await Promise.all(waits)
  await globalThis.caches.default.age(KEY, 50)
  originImpl = async () => {
    throw new TypeError('down')
  }
  check((await call('/api/market')).headers.get('x-yq-cache') === 'ERROR-STALE', 'past 10 + 20 s the copy still covers an origin failure')
  await globalThis.caches.default.age(KEY, 200)
  check((await call('/api/market')).status === 504, 'past stale-if-error=100 it does not')
}

/* ── 7. missing chunks are a real 404 ──────────────────────────────────────────────────────────── */
console.log('static files')
{
  const ok = await call('/assets/ok.js')
  check(ok.status === 200 && ok.headers.get('cache-control').includes('immutable'), 'an existing chunk passes through untouched')
  const miss = await call('/assets/index-OLDHASH.js')
  check(miss.status === 404 && miss.headers.get('cache-control') === 'no-store' && !/html/.test(miss.headers.get('content-type')), 'a missing chunk is 404 + no-store, never index.html')
  const font = await call('/fonts/gone.woff2')
  check(font.status === 404 && font.headers.get('cache-control') === 'no-store', 'a missing font too')
  const fontOk = await call('/fonts/ok.woff2')
  check(fontOk.status === 200, 'an existing font passes through')
  const page = await call('/some/deep/route')
  check(page.status === 200 && /html/.test(page.headers.get('content-type')), 'every other path is the SPA fallback, unchanged')
}

console.log(failures ? `\n${failures} failure(s)` : '\nall passed')
process.exit(failures ? 1 : 0)
