/**
 * The rep's app in the field (the salesman-app polish stream), exercised on the real modules — plain
 * node, no test runner, no new deps.
 *
 *   node web/scripts/rep_field_test.mjs        (from the repo root or from web/)
 *
 * Same loader as rep_ui_test.mjs (a module.registerHooks load hook, Node 22.15+, the project's own
 * `typescript`; nothing is compiled to disk). Each module is loaded on its own, so a missing rule
 * fails its own checks with its name instead of stopping the run. Exit 1 on any failure.
 *
 * What it proves (synthetic codes and shops only):
 *   • the shop being ordered for survives the app closing on a WhatsApp detour, for 12 hours;
 *   • signing out takes this phone's rep carts, the shop picked and the catalog's remembered search
 *     with it, and nothing else of the device;
 *   • the checkout never shows BHD 0.000 / Free or lets an order go before the server has priced
 *     this very cart; a failed check says so and offers Retry.
 */
import { registerHooks } from 'node:module'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'
import ts from 'typescript'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')
const isFile = (p) => existsSync(p) && statSync(p).isFile()

registerHooks({
  resolve(spec, ctx, next) {
    let s = spec
    if (s.startsWith('@/')) s = path.join(SRC, s.slice(2))
    else if (s.startsWith('./') || s.startsWith('../')) s = path.resolve(path.dirname(fileURLToPath(ctx.parentURL)), s)
    else return next(spec, ctx)
    for (const cand of [s, s + '.ts', s + '.tsx', path.join(s, 'index.ts')]) {
      if (isFile(cand)) return { url: pathToFileURL(cand).href, shortCircuit: true }
    }
    return next(spec, ctx)
  },
  load(url, ctx, next) {
    if (url.startsWith('file:') && /\.tsx?$/.test(url)) {
      const file = fileURLToPath(url)
      const { outputText } = ts.transpileModule(readFileSync(file, 'utf8'), {
        fileName: file,
        compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
      })
      return { format: 'module', source: outputText, shortCircuit: true }
    }
    return next(url, ctx)
  },
})

/** A module of the app, or why it did not load (its checks then fail with that reason). */
async function load(rel) {
  try {
    return await import(pathToFileURL(path.join(SRC, rel)).href)
  } catch (e) {
    return { __missing: `${rel} did not load: ${e && e.message ? e.message.split('\n')[0] : e}` }
  }
}
/** The named export of a loaded module, or a failure that says what is missing. */
function need(mod, name) {
  if (mod.__missing) throw new Error(mod.__missing)
  if (!(name in mod)) throw new Error(`${name} is not exported`)
  return mod[name]
}

const D = await load('pages/sales/repDevice.ts')
const C = await load('pages/shop/checkout.ts')

let passed = 0
let failed = 0
function check(name, fn) {
  try {
    fn()
    passed += 1
    console.log(`  PASS  ${name}`)
  } catch (e) {
    failed += 1
    console.log(`  FAIL  ${name}\n        ${e && e.message ? e.message : e}`)
  }
}
function eq(a, b, what = '') {
  const A = JSON.stringify(a)
  const B = JSON.stringify(b)
  if (A !== B) throw new Error(`${what} expected ${B}, got ${A}`)
}

const HOUR = 3_600_000
const NOW = Date.UTC(2026, 9, 28, 8, 0)
const shopA = { key: 'f:ALPHA MOBILE', name: 'Contact A', shop: 'Alpha Mobile', phone: '97333000001', area: 'Area One' }

/* ── 1. the shop being ordered for: localStorage, 12 hours ── */
check('the shop picked survives the app closing (a WhatsApp detour), for 12 hours from the pick', () => {
  const record = need(D, 'selectionRecord')
  const read = need(D, 'readSelection')
  const raw = record(shopA, NOW)
  eq(typeof raw, 'string', 'stored as text')
  eq(read(raw, NOW + 5 * 60_000), { c: shopA, at: NOW }, 'five minutes later')
  eq(read(raw, NOW + 12 * HOUR - 1), { c: shopA, at: NOW }, 'just inside 12 h')
  eq(read(raw, NOW + 12 * HOUR), null, '12 h after the pick: the next morning starts with no shop')
  eq(D.SELECTION_TTL_MS, 12 * HOUR, 'the window')
})

check('a stored pick that cannot be trusted is no pick: unreadable, no time, no shop, far in the future', () => {
  const read = need(D, 'readSelection')
  eq(read(null, NOW), null, 'nothing stored')
  eq(read('', NOW), null, 'empty')
  eq(read('{not json', NOW), null, 'not JSON')
  eq(read(JSON.stringify({ c: shopA }), NOW), null, 'no time')
  eq(read(JSON.stringify({ c: shopA, at: 'yesterday' }), NOW), null, 'a time that is not a number')
  eq(read(JSON.stringify({ c: { name: '' }, at: NOW }), NOW), null, 'no name, no shop, no key: nothing to order for')
  eq(read(JSON.stringify({ c: 'Alpha', at: NOW }), NOW), null, 'not a shop')
  eq(read(JSON.stringify({ c: shopA, at: NOW + 13 * HOUR }), NOW), null, 'a clock that was far ahead')
  eq(read(JSON.stringify({ c: shopA, at: NOW + 2 * 60_000 }), NOW), { c: shopA, at: NOW + 2 * 60_000 }, 'a clock a little ahead')
})

check('a pick made before this release (sessionStorage, the bare shop) is still read once', () => {
  const legacy = need(D, 'legacySelection')
  eq(legacy(JSON.stringify(shopA)), shopA)
  eq(legacy(JSON.stringify({ name: 'Walk-in', shop: '' })), { name: 'Walk-in', shop: '' }, 'a new shop typed by name')
  eq([legacy(null), legacy('nope'), legacy(JSON.stringify({ phone: '' })), legacy(JSON.stringify([shopA]))], [null, null, null, null])
})

/* ── 6. sign-out on a shared phone ── */
check("signing out clears every rep cart (each shop's and 'no shop yet'), the pick and the remembered search — nothing else", () => {
  const keysOf = need(D, 'repDeviceKeys')
  const stored = [
    'yq-shop-cart:staff',
    'yq-shop-cart:staff:f:ALPHA MOBILE',
    'yq-shop-cart:staff:m:97333000002',
    'yq-shop-cart:staffer',            // a customer's share-link token that happens to start with "staff"
    'yq-shop-cart:tok123',             // a customer's cart from a share link: not the rep's
    'yq-shop-cart:anon',
    'yq-staff-customer',
    D.BROWSE_KEY,
    'yq-staff-view',                   // list or grid: a choice of the device, kept
    'yq-sales-rail',
    'yq-shop-session',
    'yq-mi-lang',
    'sb-xyz-auth-token',               // the session: the sign-out itself handles it
  ]
  eq(keysOf(stored), ['yq-shop-cart:staff', 'yq-shop-cart:staff:f:ALPHA MOBILE', 'yq-shop-cart:staff:m:97333000002', 'yq-staff-customer', D.BROWSE_KEY])
  eq(keysOf([]), [])
  eq(keysOf([null, undefined, '']), [], 'a storage that hands back holes')
})

/** A Storage like the browser's: key(i) follows the live order, so removing while walking skips one. */
function fakeStorage(init) {
  const data = { ...init }
  return {
    data,
    get length() { return Object.keys(data).length },
    key: (i) => Object.keys(data)[i] ?? null,
    getItem: (k) => (k in data ? data[k] : null),
    removeItem: (k) => { delete data[k] },
  }
}

check('the sign-out sweep removes exactly those keys from a Storage (collected first: none skipped)', () => {
  const sweep = need(D, 'clearRepStorage')
  const store = fakeStorage({
    'yq-shop-cart:staff:f:ALPHA MOBILE': '[{"item_code":"C18","qty":3}]',
    'yq-shop-cart:staff:f:BETA': '[{"item_code":"UK15","qty":6}]',
    'yq-shop-cart:staff': '[{"item_code":"PD20","qty":1}]',
    'yq-shop-cart:tok123': '[{"item_code":"C18","qty":9}]',
    'yq-staff-customer': '{}',
    'yq-staff-view': 'grid',
  })
  eq(sweep(store), 4, 'removed')
  eq(Object.keys(store.data).sort(), ['yq-shop-cart:tok123', 'yq-staff-view'], 'left')
  const broken = { get length() { throw new Error('SecurityError') }, key: () => null, removeItem: () => {} }
  eq(sweep(broken), 0, 'storage switched off: nothing to do, no throw')
})

check('the sign-out question reads the same Storage shape', () => {
  const entries = need(D, 'storageEntries')
  const store = fakeStorage({ 'yq-shop-cart:staff:f:ALPHA MOBILE': '[{"item_code":"C18","qty":3}]', 'yq-staff-view': 'list' })
  eq(entries(store), [['yq-shop-cart:staff:f:ALPHA MOBILE', '[{"item_code":"C18","qty":3}]'], ['yq-staff-view', 'list']])
  eq(entries({ get length() { throw new Error('SecurityError') } }), [])
})

check('the sign-out question counts the orders in progress on this phone (non-empty rep carts)', () => {
  const open = need(D, 'openRepOrders')
  const cart = (lines) => JSON.stringify(lines)
  eq(open([
    ['yq-shop-cart:staff:f:ALPHA MOBILE', cart([{ item_code: 'C18', qty: 3 }, { item_code: 'UK15', qty: 6 }])],
    ['yq-shop-cart:staff', cart([{ item_code: 'PD20', qty: 1 }])],
    ['yq-shop-cart:staff:m:97333000002', cart([])],
    ['yq-shop-cart:tok123', cart([{ item_code: 'C18', qty: 9 }])],
    ['yq-shop-cart:staff:f:BETA', '{broken'],
  ]), { orders: 2, lines: 3 })
  eq(open([]), { orders: 0, lines: 0 })
})

/* ── 2. the checkout's money: no Place order before the server has priced this cart ── */
const quote = (extra = {}) => ({ subtotal_bhd: 12, delivery_bhd: 0, total_bhd: 12, can_submit: true, ...extra })
const pick = (s) => [s.state, s.canPlace, s.showTotals]

check('checkout: no price yet → "checking": placeholders, never BHD 0.000 / Free, and Place order off', () => {
  const pc = need(C, 'priceCheck')
  eq(pick(pc({ lines: 2, quote: null, fresh: false, quoting: true, error: '' })), ['checking', false, false], 'first quote in flight')
  eq(pick(pc({ lines: 2, quote: null, fresh: false, quoting: false, error: '' })), ['checking', false, false], 'no answer and none asked: still no price')
})

check('checkout: a failed price check → the error, Retry, Place order off — even with an older price on screen', () => {
  const pc = need(C, 'priceCheck')
  const down = 'Could not reach the price server. Your cart is safe — please try again.'
  const s1 = pc({ lines: 2, quote: null, fresh: false, quoting: false, error: down })
  eq([...pick(s1), s1.error], ['failed', false, false, down])
  // the old rule (`quote?.can_submit !== false`) let an older cart's price place this one
  const s2 = pc({ lines: 2, quote: quote(), fresh: false, quoting: false, error: down })
  eq(pick(s2), ['failed', false, false], 'an older cart\'s price is not this cart\'s')
})

check('checkout: the server says no → blocked with its reason, or the fallback words', () => {
  const pc = need(C, 'priceCheck')
  const s = pc({ lines: 1, quote: quote({ can_submit: false, block_reason: 'Remove X05 UL-1Mtr: sold out.' }), fresh: true, quoting: false, error: '' })
  eq([...pick(s), s.reason], ['blocked', false, true, 'Remove X05 UL-1Mtr: sold out.'])
  eq(pc({ lines: 1, quote: quote({ can_submit: false }), fresh: true, quoting: false, error: '' }).reason, 'This order cannot be submitted yet.')
})

check('checkout: re-pricing keeps the last price on screen (no flash to zero) and Place order waits for it', () => {
  const pc = need(C, 'priceCheck')
  eq(pick(pc({ lines: 3, quote: quote(), fresh: false, quoting: true, error: '' })), ['updating', false, true])
  eq(pick(pc({ lines: 3, quote: quote(), fresh: true, quoting: true, error: '' })), ['updating', false, true], 'a Retry of the same cart')
  const s = pc({ lines: 3, quote: quote({ can_submit: false, block_reason: 'Minimum order is BHD 20.000.' }), fresh: false, quoting: true, error: '' })
  eq([s.state, s.reason], ['updating', 'Minimum order is BHD 20.000.'], 'the price on screen keeps its reason while it is checked again')
})

check('checkout: this cart priced and allowed → ready; an empty cart is nothing to price', () => {
  const pc = need(C, 'priceCheck')
  eq(pick(pc({ lines: 2, quote: quote(), fresh: true, quoting: false, error: '' })), ['ready', true, true])
  eq(pick(pc({ lines: 2, quote: quote({ can_submit: undefined }), fresh: true, quoting: false, error: '' })), ['ready', true, true], 'an older API without the flag')
  eq(pick(pc({ lines: 0, quote: null, fresh: false, quoting: false, error: '' })), ['empty', false, false])
})

check('useQuote: an error belongs to the request it answered; a new cart or a Retry is "checking" again', () => {
  const view = need(C, 'quoteView')
  const qA = quote()
  const answeredA = { req: '0|A', cart: 'A', quote: qA, error: '' }
  eq(view({ req: '', cart: '', quote: null, error: '' }, 'A', '0|A'), { quote: null, quoting: true, error: '', fresh: false }, 'first open')
  eq(view(answeredA, 'A', '0|A'), { quote: qA, quoting: false, error: '', fresh: true }, 'A priced')
  eq(view(answeredA, 'B', '0|B'), { quote: qA, quoting: true, error: '', fresh: false }, 'the cart became B: A stays on screen')
  const failedB = { req: '0|B', cart: 'A', quote: qA, error: 'down' }
  eq(view(failedB, 'B', '0|B'), { quote: qA, quoting: false, error: 'down', fresh: false }, "B's check failed")
  eq(view(failedB, 'B', '1|B'), { quote: qA, quoting: true, error: '', fresh: false }, 'Retry: checking again, the old error gone')
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
