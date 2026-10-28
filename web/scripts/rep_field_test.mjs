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
 *     this very cart; a failed check says so and offers Retry;
 *   • after an order the Orders list, the stock and the shop's usual basket are read again;
 *   • "Order again" keeps every quantity the rep typed and adds only the usual lines missing; Undo
 *     restores the cart before it, and a removed line comes back where it was.
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
const E = await load('pages/shop/cartEdits.ts')

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
async function checkAsync(name, fn) {
  try {
    await fn()
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

/* ── 3. after an order: what the screens show again ── */
const O = await load('pages/shop/staffOrder.ts')

check('after an order: the Orders list and its badge, Today, the book, the catalog stock and that shop\'s usual basket', () => {
  const keys = need(O, 'afterOrderKeys')
  const k = keys('f:ALPHA MOBILE').map((x) => JSON.stringify(x))
  for (const want of [['shop-orders'], ['shop-orders-new-count'], ['shop-today'], ['shop-book'], ['staff-catalog'], ['shop-basket', 'f:ALPHA MOBILE']]) {
    if (!k.includes(JSON.stringify(want))) throw new Error(`missing ${JSON.stringify(want)} in ${k.join(' ')}`)
  }
  const fresh = keys(null).map((x) => JSON.stringify(x))
  eq(fresh.some((x) => x.startsWith('["shop-basket"')), false, 'a new shop has no usual basket to refresh')
})

check('after an order: every key it drops is one a screen reads (no key that quietly stopped matching)', () => {
  const keys = need(O, 'afterOrderKeys')
  // the screens' own queryKey literals, read from the sources
  const files = ['pages/ShopOrders.tsx', 'pages/sales/lib.ts', 'pages/shop/StaffCatalog.tsx', 'pages/sales/PriceDrops.tsx']
  const text = files.map((f) => readFileSync(path.join(SRC, f), 'utf8')).join('\n')
  for (const key of keys('f:ALPHA MOBILE')) {
    const head = `queryKey: ['${key[0]}'`
    if (!text.includes(head)) throw new Error(`no screen reads ${head}…`)
  }
})

/* ── 4. Order again merges; 5. Undo puts things back ── */
const ITEMS = new Map([
  ['C18', { item_code: 'C18', stock_qty: 40 }],
  ['UK15', { item_code: 'UK15', stock_qty: 12, moq: 6, pack_size: 6 }],
  ['PD20', { item_code: 'PD20', stock_qty: 0 }],              // sold out
  ['X05', { item_code: 'X05', stock_qty: 9 }],
])
const itemOf = (code) => ITEMS.get(code)
const L = (item_code, qty) => ({ item_code, qty })

check('Order again keeps every quantity already typed and adds only the usual lines that are missing', () => {
  const merge = need(E, 'mergeUsual')
  const typed = [L('C18', 7), L('ZZ9', 2)]
  const usual = [L('C18', 3), L('UK15', 4), L('PD20', 2), L('X05', 5), L('GONE', 1)]
  const m = merge(typed, usual, itemOf)
  eq(m.lines, [L('C18', 7), L('ZZ9', 2), L('UK15', 6), L('X05', 5)], 'typed lines first and as typed; the missing usual lines after, at least their minimum')
  eq([m.added, m.kept, m.soldOut], [['UK15', 'X05'], ['C18'], ['PD20']])
  eq(typed, [L('C18', 7), L('ZZ9', 2)], 'the cart it was given is not changed (Undo restores it)')
})

check('Order again into an empty cart fills every usual line that can be sold today', () => {
  const merge = need(E, 'mergeUsual')
  const m = merge([], [L('UK15', 12), L('PD20', 2), L('C18', 1)], itemOf)
  eq([m.lines, m.added, m.kept, m.soldOut], [[L('UK15', 12), L('C18', 1)], ['UK15', 'C18'], [], ['PD20']])
  eq(merge([], [], itemOf).lines, [])
})

check('the Order again toast says what happened: "Added N usual lines · kept your quantities"', () => {
  const text = need(E, 'usualText')
  eq(text({ added: ['A', 'B', 'C'], kept: ['D'] }, 'regulars'), 'Added 3 usual lines · kept your quantities')
  eq(text({ added: ['A'], kept: [] }, 'due_regulars'), 'Added 1 usual line')
  eq(text({ added: ['A', 'B'], kept: [] }, 'last_order'), 'Added 2 lines from their last order')
  eq(text({ added: [], kept: ['D'] }, 'regulars'), '', 'nothing added: nothing to undo')
})

check('Undo puts a removed line back where it was, with its quantity', () => {
  const restore = need(E, 'restoreLine')
  const now = [L('A', 1), L('C', 3)]
  eq(restore(now, L('B', 2), 1), [L('A', 1), L('B', 2), L('C', 3)])
  eq(restore(now, L('B', 2), 9), [L('A', 1), L('C', 3), L('B', 2)], 'past the end: last')
  eq(restore([L('A', 1), L('B', 1)], L('B', 5), 0), [L('B', 5), L('A', 1)], 'added again since: the removed quantity comes back, once')
  eq(restore([], L('B', 2), 3), [L('B', 2)])
  eq(now, [L('A', 1), L('C', 3)], 'the lines it was given are not changed')
})

check('replaceLines makes the cart exactly these lines, in this order, through clear + set', () => {
  const replace = need(E, 'replaceLines')
  let lines = [L('X', 9)]
  const cart = { clear: () => { lines = [] }, set: (c, q) => { lines = [...lines.filter((l) => l.item_code !== c), L(c, q)] } }
  replace(cart, [L('B', 2), L('A', 1)])
  eq(lines, [L('B', 2), L('A', 1)])
  replace(cart, [])
  eq(lines, [])
})

/* ── 10. the shell's tabs: the page's own tab lit, and only tabs the rep may open ── */
const T = await load('pages/sales/tabs.ts')
const TABS = [{ to: '/today', end: true }, { to: '/shop' }, { to: '/shop-orders' }, { to: '/customers' }, { to: '/account' }]

check('the tab bar lights the tab a page is under — and no tab on any other page (it used to light Today)', () => {
  const active = need(T, 'activeTab')
  eq(['/today', '/shop', '/shop-orders', '/customers', '/account'].map((p) => active(p, TABS)), [0, 1, 2, 3, 4])
  eq(active('/shop/anything', TABS), 1, 'a page under Catalog')
  eq(active('/shop-orders', TABS), 2, '"/shop-orders" is not a page under "/shop"')
  eq(['/market-intel', '/market-intel/cases/7', '/finds', '/leads', '/settings', '/today/old', '/'].map((p) => active(p, TABS)),
    [-1, -1, -1, -1, -1, -1, -1])
})

check('a tab shows only when the rep may open its page (the route\'s Gate); Me always', () => {
  const allowed = need(T, 'tabAllowed')
  const shown = (me) => TABS.filter((t) => allowed(me, t.to)).map((t) => t.to)
  const rep = (features) => ({ role: 'salesman', features })
  eq(shown(rep(['Catalog', 'Shop Orders'])), ['/today', '/shop', '/shop-orders', '/customers', '/account'])
  eq(shown(rep(['Catalog'])), ['/shop', '/account'])
  eq(shown(rep(['Shop Orders', 'Market Intel'])), ['/today', '/shop-orders', '/customers', '/account'])
  eq(shown(rep([])), ['/account'])
  eq(shown({ role: 'salesman' }), ['/account'], 'no features listed')
  eq(shown({ role: 'admin', features: [] }), ['/today', '/shop', '/shop-orders', '/customers', '/account'], 'an admin passes every Gate')
  eq(shown(null), [], 'no login, no tabs')
})

check("the tabs' features are the routes' own (App.tsx <Gate feature>): a renamed feature cannot drift", () => {
  const map = need(T, 'TAB_FEATURE')
  const app = readFileSync(path.join(SRC, 'App.tsx'), 'utf8')
  for (const [to, feature] of Object.entries(map)) {
    const route = `path="${to.slice(1)}" element={<Gate feature="${feature}">`
    if (!app.includes(route)) throw new Error(`App.tsx has no ${route}`)
  }
  if (!app.includes('path="account" element={<SalesAccount />}')) throw new Error('Me (/account) is no longer open to every login')
  eq(Object.keys(map).sort(), ['/customers', '/shop', '/shop-orders', '/today'])
})

/* ── 14. the compact order bar: the minimum as a line, one line of words under the total ── */
check('the wholesale minimum: the server\'s when it has priced the cart, the list-price estimate until then', () => {
  const min = need(O, 'minimumOf')
  eq(min(15, 20, null), { value: 20, remaining: 5, met: false, pct: 75 }, 'the estimate, short of it')
  eq(min(25, 20, null), { value: 20, remaining: 0, met: true, pct: 100 }, 'the estimate, past it')
  eq(min(15, 20, { minimum: { value_bhd: 30, remaining_bhd: 12, met: false } }), { value: 30, remaining: 12, met: false, pct: 60 }, "the server's own")
  eq(min(15, 20, { minimum: { value_bhd: 20, remaining_bhd: 0, met: true } }).met, true)
  eq([min(15, 0, null), min(15, 0, { minimum: null })], [null, null], 'no minimum: nothing to draw')
})

check('the order bar\'s one line: how far from the minimum while short of it, else the shop and the order\'s size', () => {
  const line = need(O, 'orderBarLine')
  const short = { value: 20, remaining: 4.5, met: false, pct: 77.5 }
  const met = { value: 20, remaining: 0, met: true, pct: 100 }
  eq(line({ shop: 'Alpha Mobile', lines: 3, units: 24, minimum: short }), 'BHD 4.500 to the minimum · 3 lines')
  eq(line({ shop: 'Alpha Mobile', lines: 3, units: 24, minimum: met }), 'Alpha Mobile · 3 lines · 24 pcs')
  eq(line({ shop: '', lines: 1, units: 6, minimum: null }), 'No shop picked yet · 1 line · 6 pcs')
})

/* ── 11–13. the catalog keeps up with typing: the index at idle, a plain filter until then ── */
const B = await load('pages/shop/staffBrowse.ts')
const CAT = [
  { item_code: 'C18', display_name: 'Car charger 18W', spec: 'Dual USB', category: 'CAR CHARGER' },
  { item_code: 'X05 UC-1Mtr', display_name: 'Type-C cable 1 m', spec: 'Braided', category: 'CABLE' },
  { item_code: 'UK15', display_name: 'Wall charger', spec: '15W', category: 'CHARGER' },
]

check('before the search index is built, a search is a plain filter — never the whole catalog', () => {
  const plain = need(B, 'plainSearch')
  const codes = (t) => plain(CAT, t).map((i) => i.item_code)
  eq(codes('x05uc'), ['X05 UC-1Mtr'], 'a code typed without its spaces and dash')
  eq(codes('X05 uc'), ['X05 UC-1Mtr'], 'or with them, in any case')
  eq(codes('charger'), ['C18', 'UK15'], 'a word of the name or the category')
  eq(codes('car 18w'), ['C18'], 'every word must hit')
  eq(codes('braided cable'), ['X05 UC-1Mtr'], 'the spec counts')
  eq(codes('nothing like it'), [])
  eq(codes('   '), ['C18', 'X05 UC-1Mtr', 'UK15'], 'no words: the whole list')
})

await checkAsync('the index is built when the phone is idle; a timer where there is no requestIdleCallback; cancel stops it', async () => {
  const idle = need(B, 'whenIdle')
  const tick = () => new Promise((r) => setTimeout(r, 25))
  let ran = 0
  idle(() => { ran += 1 })
  await tick()
  eq(ran, 1, 'no requestIdleCallback (Safari, node): it runs on a timer')
  idle(() => { ran += 10 })()
  await tick()
  eq(ran, 1, 'cancelled before it ran')
  const asked = []
  let cancelled = null
  globalThis.requestIdleCallback = (cb, opts) => { asked.push(opts); setTimeout(cb, 0); return 7 }
  globalThis.cancelIdleCallback = (id) => { cancelled = id }
  try {
    idle(() => { ran += 100 }, 900)
    await tick()
    eq([ran, asked], [101, [{ timeout: 900 }]], 'requestIdleCallback with a deadline')
    idle(() => {})()
    eq(cancelled, 7, 'its cancel')
  } finally {
    delete globalThis.requestIdleCallback
    delete globalThis.cancelIdleCallback
  }
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
