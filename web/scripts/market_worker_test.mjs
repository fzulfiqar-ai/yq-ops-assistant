/**
 * The edge catalog Worker (web/workers/market.js), exercised in plain node — no wrangler, no network,
 * no new deps. Same shape as soldout_order_test.mjs: exit 1 on any failure; tests/test_r6_speed.py runs
 * it and reports the result (SKIP when node is missing).
 *
 *   node web/scripts/market_worker_test.mjs        (from the repo root or from web/)
 *
 * The Workers runtime globals the script uses (Request/Response/Headers, AbortSignal.timeout,
 * crypto.subtle) exist in Node 20+; `caches.default` and `fetch` are replaced by fakes here so the
 * cache states, the origin calls and the timeouts can be asserted exactly. The fake origin behaves
 * like app/shop.py for ?ref=: a listed rep gets `ref` filled, an unknown slug gets the no-ref payload.
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
  async delete(req) {
    return this.map.delete(req.url)
  }
  /** move a stored copy back in time by `seconds` (what the Worker reads as its age); a fresh
   *  set-up, so any grace marker a previous sub-case left is cleared with it */
  async age(url, seconds) {
    const res = this.map.get(url)
    const h = new Headers(res.headers)
    h.set('x-yq-stored-at', String(Date.now() - seconds * 1000))
    h.delete('x-yq-grace-until')
    this.map.set(url, new Response(await res.clone().arrayBuffer(), { status: 200, headers: h }))
  }
}

const ORIGIN_CC = 'public, max-age=60, stale-while-revalidate=600, stale-if-error=86400'
const REPS = ['furqan', 'harsh']
const refOf = (url) => (new URL(url).searchParams.get('ref') || '').toLowerCase()
/** the API's payload shape for the parts the Worker reads: items, salesmen[].referral_code, ref */
const catalogJson = (n, ref = '') =>
  JSON.stringify({
    items: [{ item_code: `X0${n}` }],
    n,
    salesmen: REPS.map((code, i) => ({ id: i + 1, name: code, referral_code: code })),
    ref: REPS.includes(ref) ? { referral_code: ref, salesman_id: REPS.indexOf(ref) + 1 } : null,
  })
const originOk = (n, extra = {}, ref = '') =>
  new Response(catalogJson(n, ref), {
    status: 200,
    headers: { 'content-type': 'application/json', 'cache-control': ORIGIN_CC, etag: `W/"v${n}${REPS.includes(ref) ? '-' + ref : ''}"`, ...extra },
  })
/** an origin that answers like the API: the ref decides the payload */
const originLike = (n, extra = {}) => async (url) => originOk(n, extra, refOf(url))

let originCalls = []
let originImpl = originLike(1)
globalThis.fetch = async (url, init) => {
  originCalls.push({ url: String(url), init: init || {} })
  return originImpl(String(url), init || {})
}
globalThis.caches = { default: new FakeCache() }
const reset = () => {
  globalThis.caches = { default: new FakeCache() }
  originCalls = []
  originImpl = originLike(1)
}
/** an origin that never answers: the Worker's own timeout is what ends the call. Node unrefs
 *  AbortSignal.timeout's timer (the Workers runtime does not), so `keep` holds the loop open. */
const hang = () => (url, init) =>
  new Promise((_, reject) => {
    init.signal.addEventListener('abort', () => reject(Object.assign(new Error('timeout'), { name: 'TimeoutError' })))
  })

const assets = {
  fetch: async (req) => {
    const u = new URL(req.url)
    if (u.pathname === '/assets/ok.js') return new Response('ok()', { status: 200, headers: { 'content-type': 'text/javascript', 'cache-control': 'public, max-age=31536000, immutable' } })
    if (u.pathname === '/fonts/ok.woff2') return new Response('wOF2', { status: 200, headers: { 'content-type': 'font/woff2', 'cache-control': 'public, max-age=31536000, immutable' } })
    // an asset layer with not_found_handling none: its own 404, stamped by the /assets/* header rule
    if (u.pathname === '/assets/plain-404.js') return new Response('', { status: 404, headers: { 'cache-control': 'public, max-age=31536000, immutable' } })
    // the static layer's SPA fallback: index.html, 200, and the immutable header the path pattern gives it
    return new Response('<!doctype html><title>YQ</title>', { status: 200, headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'public, max-age=31536000, immutable' } })
  },
}
const env = { ASSETS: assets }
let waits = []
const ctx = { waitUntil: (p) => waits.push(p) }
const call = (path, headers = {}, method = 'GET') => worker.fetch(new Request('https://yqmarketplace.com' + path, { method, headers }), env, ctx)
const KEY = 'https://yqmarketplace.com/api/market'
const keys = () => [...globalThis.caches.default.map.keys()]

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
  check(stored.headers.get('x-yq-reps') === 'furqan,harsh', 'the no-ref copy carries the reps the catalog lists')
  check(!r1.headers.has('x-yq-reps') && !r1.headers.has('x-yq-stored-at'), 'the bookkeeping headers never leave the edge')

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

/* ── 2. ref handling: known reps have a key, everything else is the no-ref copy ────────────────── */
console.log('ref')
{
  reset()
  const f = await call('/api/market?ref=Furqan')
  check(originCalls[0].url.endsWith('/public/market?ref=furqan'), 'a slug is lowercased before it reaches the origin')
  check(globalThis.caches.default.map.has(KEY + '?ref=furqan') && (await f.json()).ref.referral_code === 'furqan', 'a listed rep has its own copy, with the rep filled')
  const junk = await call('/api/market?ref=%3Cscript%3E')
  check(originCalls.length === 2 && originCalls[1].url.endsWith('/public/market') && junk.status === 200, 'a junk ref shares the no-ref copy (one origin call, no new key)')
  const again = await call('/api/market?ref=%21%21')
  check(again.headers.get('x-yq-cache') === 'HIT' && originCalls.length === 2, 'junk refs cannot grow the cache or burn origin calls')

  // a well-formed slug nobody has: canonicalized against the no-ref copy's rep list
  const unknown = await call('/api/market?ref=zzzz-not-a-rep')
  check(unknown.status === 200 && unknown.headers.get('x-yq-cache') === 'HIT' && originCalls.length === 2, 'an unknown slug is a HIT on the no-ref copy: no origin call')
  check((await unknown.json()).ref === null, 'and gets the no-ref catalog, as the API itself would answer')
  for (const s of ['aaaa', 'bbbb-1', 'cccc-2', 'dddd-3']) await call(`/api/market?ref=${s}`)
  check(originCalls.length === 2 && keys().length === 2, 'four more unknown slugs: still two keys, still two origin calls')
  const harsh = await call('/api/market?ref=harsh')
  check(originCalls.length === 3 && originCalls[2].url.endsWith('?ref=harsh') && harsh.headers.get('x-yq-cache') === 'MISS' && keys().includes(KEY + '?ref=harsh'), 'the other listed rep gets a key and an origin call of its own')
  check((await harsh.json()).ref.referral_code === 'harsh', 'rep isolation: the harsh copy is the harsh payload')
  check((await (await call('/api/market?ref=furqan')).json()).ref.referral_code === 'furqan', 'and the furqan copy still the furqan payload')

  // a cold edge (no no-ref copy yet): the origin decides, and its answer for an unknown slug is
  // stored as the no-ref copy so every later unknown slug is a HIT
  reset()
  const cold = await call('/api/market?ref=zzzz-not-a-rep')
  check(originCalls.length === 1 && originCalls[0].url.endsWith('?ref=zzzz-not-a-rep') && cold.headers.get('x-yq-cache') === 'MISS', 'cold edge + unknown slug: one origin call')
  check(keys().length === 1 && keys()[0] === KEY, 'stored under the no-ref key (the payload said ref: null)')
  const next = await call('/api/market?ref=yyyy-not-a-rep')
  check(next.headers.get('x-yq-cache') === 'HIT' && originCalls.length === 1, 'the next unknown slug is a HIT')

  // a rep the no-ref copy does not list yet (added today) becomes known when that copy refreshes
  reset()
  await call('/api/market')
  originImpl = async (url) => {
    const ref = refOf(url)
    const body = JSON.parse(catalogJson(2, ref))
    body.salesmen.push({ id: 3, name: 'new', referral_code: 'newrep' })
    if (ref === 'newrep') body.ref = { referral_code: 'newrep', salesman_id: 3 }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json', 'cache-control': ORIGIN_CC, etag: `W/"v2-${ref}"` } })
  }
  check((await call('/api/market?ref=newrep')).headers.get('x-yq-cache') === 'HIT' && originCalls.length === 1, 'until then the new slug is the no-ref copy')
  await globalThis.caches.default.age(KEY, 120)
  waits = []
  await call('/api/market')
  await Promise.all(waits)
  const known = await call('/api/market?ref=newrep')
  check(known.headers.get('x-yq-cache') === 'MISS' && (await known.json()).ref.referral_code === 'newrep' && keys().includes(KEY + '?ref=newrep'), 'after the no-ref copy refreshed the new rep has a key of their own')
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
  originImpl = originLike(2)
  waits = []
  await call('/api/market')
  await Promise.all(waits)
  const r3 = await call('/api/market')
  check(r3.headers.get('x-yq-cache') === 'HIT' && (await r3.json()).n === 2 && r3.headers.get('etag') === 'W/"v2"', 'a 200 replaces the copy')
}

/* ── 4. past the window: origin first, last good copy on origin trouble ────────────────────────── */
console.log('stale-if-error')
{
  reset()
  await call('/api/market')
  await globalThis.caches.default.age(KEY, 700) // past 60 + 600
  originImpl = async () => {
    throw new TypeError('fetch failed')
  }
  waits = []
  const r = await call('/api/market')
  check(r.status === 200 && r.headers.get('x-yq-cache') === 'ERROR-STALE' && (await r.json()).n === 1, 'origin unreachable → the last good copy, marked ERROR-STALE')
  check(waits.length === 1, 'and a background refresh is started')
  await Promise.allSettled(waits)

  await globalThis.caches.default.age(KEY, 700)
  originImpl = async () => new Response('boom', { status: 503, headers: { 'content-type': 'text/plain' } })
  waits = []
  const r5 = await call('/api/market')
  check(r5.status === 200 && r5.headers.get('x-yq-cache') === 'ERROR-STALE' && waits.length === 1, 'origin 5xx → the last good copy, refresh in the background')
  await Promise.allSettled(waits)

  await globalThis.caches.default.age(KEY, 700)
  originImpl = async () => new Response('slow down', { status: 429, headers: { 'retry-after': '30' } })
  waits = []
  const r6 = await call('/api/market')
  check(r6.status === 200 && r6.headers.get('x-yq-cache') === 'ERROR-STALE' && waits.length === 0, 'origin 429 → the last good copy, and no background retry to make it worse')

  await globalThis.caches.default.age(KEY, 700)
  originImpl = async () => new Response('forbidden', { status: 403, headers: { 'content-type': 'text/plain' } })
  const r7 = await call('/api/market')
  check(r7.status === 403 && r7.headers.get('x-yq-cache') === 'BYPASS' && globalThis.caches.default.map.has(KEY), 'any other 4xx is forwarded and the copy kept for later')

  // the 3 s soft timeout: an origin that never answers must not hold the merchant longer than that.
  await globalThis.caches.default.age(KEY, 700)
  originImpl = hang()
  waits = []
  const keep = setTimeout(() => {}, 10000)
  const t0 = Date.now()
  const r2 = await call('/api/market')
  const took = Date.now() - t0
  clearTimeout(keep)
  check(r2.status === 200 && r2.headers.get('x-yq-cache') === 'ERROR-STALE', 'origin slower than 3 s → the last good copy')
  check(took >= 2500 && took < 6000, `after the soft timeout (${took} ms), not the hard one`)
  check(waits.length === 1, 'and a background refresh is started for the visitors behind this one')
  const bg = waits[0]
  check(Number(globalThis.caches.default.map.get(KEY).headers.get('x-yq-grace-until')) > Date.now(), 'the copy is marked with a grace window')

  // grace: the next visitor, origin still cold, is answered at once
  const t1 = Date.now()
  waits = []
  const r8 = await call('/api/market')
  const took2 = Date.now() - t1
  check(r8.status === 200 && r8.headers.get('x-yq-cache') === 'ERROR-STALE' && took2 < 500, `the next visitor gets the copy at once (${took2} ms), not another 3 s wait`)
  check(waits.length === 1 && Number(r8.headers.get('age')) >= 700, 'with its real age, and another background refresh')
  // the refreshes were started against a hanging origin; let them time out (25 s would hold the
  // script) by aborting through a fresh origin answer: the copy is replaced and the grace ends
  originImpl = originLike(3)
  waits = []
  const r9 = await call('/api/market')
  check(r9.headers.get('x-yq-cache') === 'ERROR-STALE' && waits.length === 1, 'still in grace: served at once, refresh scheduled')
  await Promise.all(waits)
  const r10 = await call('/api/market')
  check(r10.headers.get('x-yq-cache') === 'HIT' && (await r10.json()).n === 3, 'the background refresh landed: the next visitor is a HIT on the new copy')
  check(!globalThis.caches.default.map.get(KEY).headers.has('x-yq-grace-until'), 'and the grace marker is gone with it')
  void bg

  originImpl = originLike(4)
  await globalThis.caches.default.age(KEY, 700)
  const r3 = await call('/api/market')
  check(r3.headers.get('x-yq-cache') === 'MISS' && (await r3.json()).n === 4, 'origin back past the window → fresh copy, MISS')

  await globalThis.caches.default.age(KEY, 90000) // past stale-if-error
  originImpl = async () => {
    throw new TypeError('fetch failed')
  }
  const r4 = await call('/api/market')
  check(r4.status === 504 && r4.headers.get('cache-control') === 'no-store', 'beyond stale-if-error the failure is passed through, uncached')
}

/* ── 5. the kill switch: a 404 / 410 is the origin's answer, never covered by the copy ─────────── */
console.log('404 is authoritative')
{
  const closed = async () => new Response(JSON.stringify({ detail: 'The marketplace is not open.' }), { status: 404, headers: { 'content-type': 'application/json' } })
  // inside the stale-while-revalidate window: the copy is served once, the refresh removes it
  reset()
  await call('/api/market')
  await globalThis.caches.default.age(KEY, 120)
  originImpl = closed
  waits = []
  const r1 = await call('/api/market')
  check(r1.status === 200 && r1.headers.get('x-yq-cache') === 'STALE', 'at 120 s the stale copy is still served once (the refresh runs behind it)')
  await Promise.all(waits)
  check(!globalThis.caches.default.map.has(KEY), 'the background refresh saw the 404 and removed the copy')
  const r2 = await call('/api/market')
  check(r2.status === 404 && r2.headers.get('x-yq-cache') === 'BYPASS' && r2.headers.get('cache-control') === 'no-store', 'the next request is the 404, forwarded with no-store')
  check(/application\/json/.test(r2.headers.get('content-type')) && /not open/.test(await r2.text()), "with the origin's own body and type")
  check(globalThis.caches.default.map.size === 0, 'and nothing stored')

  // past the window: the soft-timeout branch, at 1 h and at 22 h
  for (const age of [3600, 22 * 3600]) {
    reset()
    await call('/api/market')
    await globalThis.caches.default.age(KEY, age)
    originImpl = closed
    const r = await call('/api/market')
    check(r.status === 404 && r.headers.get('x-yq-cache') === 'BYPASS', `at ${age} s a 404 is forwarded, not covered by the copy`)
    check(!globalThis.caches.default.map.has(KEY), `and the copy is removed at ${age} s`)
  }
  // 410 the same; a rep copy the same
  reset()
  await call('/api/market?ref=furqan')
  await globalThis.caches.default.age(KEY + '?ref=furqan', 3600)
  originImpl = async () => new Response('gone', { status: 410, headers: { 'content-type': 'text/plain' } })
  const r3 = await call('/api/market?ref=furqan')
  check(r3.status === 410 && !globalThis.caches.default.map.has(KEY + '?ref=furqan'), 'a 410 is authoritative too, for a rep copy as well')

  // an empty cache: the 404 is forwarded, as before
  reset()
  originImpl = closed
  const r4 = await call('/api/market')
  check(r4.status === 404 && r4.headers.get('x-yq-cache') === 'BYPASS' && globalThis.caches.default.map.size === 0, 'with nothing cached a 404 (marketplace closed) is forwarded and never stored')
}

/* ── 6. never store what is not the catalog ────────────────────────────────────────────────────── */
console.log('non-catalog answers')
{
  reset()
  originImpl = async () => new Response('slow down', { status: 429, headers: { 'retry-after': '30' } })
  const r2 = await call('/api/market')
  check(r2.status === 429 && r2.headers.get('retry-after') === '30' && globalThis.caches.default.map.size === 0, 'a 429 keeps Retry-After and is never stored')
  originImpl = originLike(1, { etag: '' })
  const r3 = await call('/api/market')
  check(/^W\/"[0-9a-f]{24}"$/.test(r3.headers.get('etag') || ''), 'an origin answer without an ETag gets a weak one computed at the edge')

  reset()
  originImpl = async () => new Response('<!doctype html><title>maintenance</title>', { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } })
  const html = await call('/api/market')
  check(html.status === 200 && html.headers.get('x-yq-cache') === 'BYPASS' && /text\/html/.test(html.headers.get('content-type')) && html.headers.get('cache-control') === 'no-store', 'a non-JSON 200 is passed through with its own type — never relabelled as the catalog')
  check(globalThis.caches.default.map.size === 0, 'and not stored')
  originImpl = async () => new Response('{"items": [', { status: 200, headers: { 'content-type': 'application/json' } })
  const broken = await call('/api/market')
  check(broken.status === 200 && broken.headers.get('x-yq-cache') === 'BYPASS' && (await broken.text()) === '{"items": [' && globalThis.caches.default.map.size === 0, 'a 200 that says JSON but does not parse is passed through, not stored')

  // the same past the window, with a copy in hand: the copy is not replaced by a non-catalog 200
  reset()
  originImpl = originLike(1)
  await call('/api/market')
  await globalThis.caches.default.age(KEY, 700)
  originImpl = async () => new Response('<!doctype html>', { status: 200, headers: { 'content-type': 'text/html' } })
  const r5 = await call('/api/market')
  check(r5.status === 200 && r5.headers.get('x-yq-cache') === 'BYPASS' && /html/.test(r5.headers.get('content-type')), 'past the window a non-JSON 200 is passed through as well')
  check((await (await globalThis.caches.default.match(new Request(KEY))).json()).n === 1, 'and the good copy is untouched')
}

/* ── 7. origin numbers drive the states ────────────────────────────────────────────────────────── */
console.log("the origin's Cache-Control")
{
  reset()
  const cc = 'public, max-age=10, stale-while-revalidate=20, stale-if-error=100'
  originImpl = originLike(1, { 'cache-control': cc })
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
  waits = []
  check((await call('/api/market')).headers.get('x-yq-cache') === 'ERROR-STALE', 'past 10 + 20 s the copy still covers an origin failure')
  await Promise.allSettled(waits)
  await globalThis.caches.default.age(KEY, 200)
  check((await call('/api/market')).status === 504, 'past stale-if-error=100 it does not')
}

/* ── 8. missing chunks are a real 404 ──────────────────────────────────────────────────────────── */
console.log('static files')
{
  const ok = await call('/assets/ok.js')
  check(ok.status === 200 && ok.headers.get('cache-control').includes('immutable'), 'an existing chunk passes through untouched')
  const miss = await call('/assets/index-OLDHASH.js')
  check(miss.status === 404 && miss.headers.get('cache-control') === 'no-store' && !/html/.test(miss.headers.get('content-type')), 'a missing chunk is 404 + no-store, never index.html')
  const plain = await call('/assets/plain-404.js')
  check(plain.status === 404 && plain.headers.get('cache-control') === 'no-store', "the asset layer's own 404 (stamped immutable by the header rule) becomes the same no-store 404")
  const font = await call('/fonts/gone.woff2')
  check(font.status === 404 && font.headers.get('cache-control') === 'no-store', 'a missing font too')
  const fontOk = await call('/fonts/ok.woff2')
  check(fontOk.status === 200, 'an existing font passes through')
  const page = await call('/some/deep/route')
  check(page.status === 200 && /html/.test(page.headers.get('content-type')), 'every other path is the SPA fallback, unchanged')
}

console.log(failures ? `\n${failures} failure(s)` : '\nall passed')
process.exit(failures ? 1 : 0)
