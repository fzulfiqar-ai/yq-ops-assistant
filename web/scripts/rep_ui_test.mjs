/**
 * The rep's screens (R7b/R7c review fixes, stream F3), exercised on the real modules — plain node,
 * no test runner, no new deps.
 *
 *   node web/scripts/rep_ui_test.mjs        (from the repo root or from web/)
 *
 * Same loader as order_heart_ui_test.mjs (a module.registerHooks load hook, Node 22.15+, the
 * project's own `typescript`; nothing is compiled to disk). Exit 1 on any failure. tests/test_r7c_ui.py
 * runs it and reports; the CI web job runs it after `npm ci`.
 *
 * What it proves (synthetic codes and shops only):
 *   • quick-add turns the search field into a list only when the rep says so — a separator, an
 *     explicit x / ×, or one "C18 3" whose word IS a code; "iphone 15" and "tws 4" stay searches;
 *   • the staff checkout's client_order_id names ONE order: the same order again keeps its id (the
 *     safe retry), a different basket or shop after a failed attempt gets a new one;
 *   • the checkout's "Change" carries its lines into the new shop's cart only when that cart is empty;
 *   • the order sheet's "Shop not told yet" / "Tell the shop first" rule: never on a rep-placed order
 *     (born Confirmed in the shop) until something changes; the pick-list stamps are not steps.
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

const Q = await import(pathToFileURL(path.join(SRC, 'pages/shop/quickAdd.ts')).href)
const O = await import(pathToFileURL(path.join(SRC, 'pages/shop/staffOrder.ts')).href)
const H = await import(pathToFileURL(path.join(SRC, 'pages/shop-ops/heart.ts')).href)

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

/* ── quick-add: a list only when the rep says so ── */
// the caller's rule: codeKey (upper case, no spaces / dashes / dots / underscores) is an item code
const CODES = new Set(['C18', 'UK15', 'X05UC1MTR', 'PD20'])
const key = (w) => String(w).toUpperCase().replace(/[\s\-_.]/g, '')
const isCode = (w) => CODES.has(key(w))
const quick = (t) => Q.isQuickList(t, isCode)

check('separators make a list: comma, semicolon, new line, the Arabic comma', () => {
  eq(['C18 3, UK15 6', 'C18; UK15', 'C18 3\nUK15 6', 'C18 3، UK15 6', 'tws, iphone'].map(quick), [true, true, true, true, true])
})

check('an explicit x / × / * between a quantity and a word makes a list', () => {
  eq(['24 x C18', '3x C18', '2 × C18', '2 * C18', 'C18 x 3', 'C18 x3', 'C18×3', 'tws x 4'].map(quick), [true, true, true, true, true, true, true, true])
  // a glued x is part of a code as often as not ("USBX3", "3xC18"): no list without the space
  eq(['3xC18', 'C18x3'].map(quick), [false, false])
})

check('one "code quantity" (or "quantity code") is a list only when the word IS a code', () => {
  eq(['C18 3', 'c18 12', '3 C18', 'X05 UC-1Mtr 2', 'uk-15 6'].map(quick), [true, true, true, true, true])
})

check('"iphone 15", "tws 4", "20w 2 port", "15 pro" stay searches on the whole text (the review finding)', () => {
  eq(['iphone 15', 'tws 4', '20w 2 port', '15 pro', 'box 3', 'USBX3', 'C18', 'type c 60w', ''].map(quick),
    [false, false, false, false, false, false, false, false, false])
})

check('a text that is itself a code (one ending in a number) is a search that finds it first', () => {
  eq(quick('PD 20'), false, 'PD 20 is the code PD20, not PD × 20')
})

/* ── the checkout's idempotency key ── */
const lines = [{ item_code: 'C18', qty: 4 }, { item_code: 'UK15', qty: 6 }]
const shopA = { name: 'Contact A', phone: '33000001', shop: 'Alpha Mobile' }
const shopB = { name: 'Contact B', phone: '33000002', shop: 'Beta Phones' }
let n = 0
const mint = () => `id-${++n}`

check('attemptKey: the same order in any line order is the same key; another shop, phone or basket is not', () => {
  const k = O.attemptKey(lines, shopA)
  eq(O.attemptKey([...lines].reverse(), { ...shopA, phone: '3300 0001', shop: ' alpha  mobile ' }), k, 'order-free, spacing / case folded')
  eq(O.attemptKey(lines, shopB) === k, false, 'another shop')
  eq(O.attemptKey(lines, { ...shopA, phone: '33000009' }) === k, false, 'another phone')
  eq(O.attemptKey([{ item_code: 'C18', qty: 5 }, lines[1]], shopA) === k, false, 'another quantity')
  eq(O.attemptKey([lines[0]], shopA) === k, false, 'a line removed')
})

check('client_order_id: the first attempt uses the checkout id; the same order after a failure keeps its id', () => {
  n = 0
  const kA = O.attemptKey(lines, shopA)
  eq(O.clientIdFor('open-1', {}, kA, mint), 'open-1', 'first attempt')
  const tried = { [kA]: 'open-1' }                       // it failed: the response was lost
  eq(O.clientIdFor('open-1', tried, kA, mint), 'open-1', 'the safe retry: a lost response comes back as that order')
  eq(O.clientIdFor('', {}, kA, mint), 'id-1', 'no id yet: a fresh one')
})

check('client_order_id: a changed basket or another shop after a failed attempt never reuses the failed id', () => {
  n = 0
  const kA = O.attemptKey(lines, shopA)
  const kB = O.attemptKey(lines, shopB)
  const kA2 = O.attemptKey([...lines, { item_code: 'C18X', qty: 1 }], shopA)
  const tried = { [kA]: 'open-1' }
  const idB = O.clientIdFor('open-1', tried, kB, mint)
  eq(idB, 'id-1', "shop B's order is not shop A's duplicate")
  eq(O.clientIdFor('open-1', tried, kA2, mint), 'id-2', 'lines changed after the failure')
  const tried2 = { ...tried, [kB]: idB }                  // B failed too
  eq(O.clientIdFor('open-1', tried2, kA, mint), 'open-1', 'back to A exactly: A keeps its own id')
  eq(O.clientIdFor('open-1', tried2, kB, mint), 'id-1', 'B keeps its own id')
  eq(O.clientIdFor('open-9', tried2, kA2, mint), 'id-3', 'a third order: a third id, even with a fresh checkout id')
})

check('newClientOrderId: a unique string every time', () => {
  const a = O.newClientOrderId()
  const b = O.newClientOrderId()
  eq(typeof a === 'string' && a.length >= 10 && a.length <= 64 && a !== b, true)
})

/* ── the cart carry when the shop is picked ── */
check('carryCart: from "no shop yet" into an empty cart; from the checkout "Change" into an EMPTY cart only', () => {
  eq(O.carryCart('staff', 'staff:f:ALPHA', 3, 0, false), true, 'a cart started before the pick')
  eq(O.carryCart('staff', 'staff:f:ALPHA', 3, 2, false), false, 'the shop already has a cart: never merged')
  eq(O.carryCart('staff:f:ALPHA', 'staff:f:BETA', 3, 0, true), true, "the checkout's Change: the order goes to the new shop")
  eq(O.carryCart('staff:f:ALPHA', 'staff:f:BETA', 3, 1, true), false, "Change to a shop with its own cart: B's cart is kept, A's stays with A")
  eq(O.carryCart('staff:f:ALPHA', 'staff:f:BETA', 3, 0, false), false, 'switching shops outside the checkout keeps each cart')
  eq(O.carryCart('staff:f:ALPHA', 'staff:f:ALPHA', 3, 0, true), false, 'the same shop again')
  eq(O.carryCart('staff:f:ALPHA', 'staff', 3, 0, true), false, 'never into "no shop"')
  eq(O.carryCart('staff:f:ALPHA', 'staff:f:BETA', 0, 0, true), false, 'nothing to carry')
})

/* ── the order sheet: when "Tell the shop" is the first thing asked ── */
const ev = (event, detail = null) => ({ event, detail })
const born = [ev('created', { source: 'salesman' }), ev('status:confirmed', { from: 'new', born_confirmed: true })]

check('a rep-placed order (born Confirmed in the shop) is already told: no chip, no "Tell the shop" first', () => {
  eq(H.shopToldFrom(born), true)
  eq(H.shopNotTold({ status: 'confirmed', shop_told: false, events: born }), false, 'even when an older API says not told')
})

check('after a real step the shop has to be told again; a logged tap clears it', () => {
  const amended = [...born, ev('amended', { lines: [] })]
  eq(H.shopNotTold({ status: 'confirmed', events: amended }), true)
  eq(H.shopNotTold({ status: 'confirmed', events: [...amended, ev('customer_notified', { channel: 'phone' })] }), false)
  const delivered = [...born, ev('status:delivered', {})]
  eq(H.shopNotTold({ status: 'delivered', events: delivered }), true)
})

check('a marketplace order confirmed by the rep needs telling; the pick-list stamps are not steps', () => {
  const market = [ev('created', { source: 'market' }), ev('status:confirmed', { from: 'new' })]
  eq(H.shopNotTold({ status: 'confirmed', events: market }), true)
  const told = [...market, ev('customer_notified', { channel: 'whatsapp' })]
  eq(H.shopNotTold({ status: 'packed', events: [...told, ev('status:packed'), ev('status:out_for_delivery')] }), false)
  eq(H.shopNotTold({ status: 'out_for_delivery', events: [...born, ev('status:out_for_delivery')] }), false)
  eq(H.isStampEvent('status:packed') && H.isStampEvent('status:out_for_delivery') && !H.isStampEvent('status:confirmed'), true)
})

check('never on a Received order; with no step on record the server flag decides; reminders are not steps', () => {
  eq(H.shopNotTold({ status: 'new', events: [ev('created')] }), false)
  eq(H.shopToldFrom([ev('reminded'), ev('assigned')]), null)
  eq(H.shopNotTold({ status: 'confirmed', shop_told: false, events: [] }), true, 'server flag')
  eq(H.shopNotTold({ status: 'confirmed', shop_told: true }), false, 'server flag, no events')
  eq(H.shopNotTold({ status: 'confirmed' }), false, 'an older API says nothing')
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
