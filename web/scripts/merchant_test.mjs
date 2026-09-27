/**
 * Merchant ordering (R7d) — the marketplace's pure rules, exercised on the real modules in plain
 * node: no test runner, no new deps.
 *
 *   node web/scripts/merchant_test.mjs        (from the repo root or from web/)
 *
 * The .ts sources are transpiled on load with the project's own `typescript` (the same load hook as
 * order_heart_ui_test.mjs / soldout_order_test.mjs). Nothing is compiled to disk. Exit 1 on any
 * failure. tests/test_r7d_merchant.py runs this script and reports its result.
 *
 * What it proves, on hand-made payloads (synthetic codes, no real data):
 *   * lib/orderChanges.ts — "What changed": requested → confirmed (→ delivered) rows, a line the rep
 *     could not supply reads "not available" (never "10 → 0"), a substitute folds its replacement into
 *     one row — the LIVE replacement down a re-substitute / restore / chain, never a dead "× 0" one, and
 *     no "requested" row for a rep line taken off again — a rep-added line, a delivery that differs, the total before → after to the fils, no
 *     tile for an order as ordered, a backorder, or a cancelled order; older payloads without a
 *     disposition still read right;
 *   * the words: English and Arabic for each row, the reason keys ("Sold Out" for out_of_stock);
 *   * lib/serverWords.ts — the quote's sentences (a sold-out line, the block, the warnings, the coupon,
 *     the progress line, the order's refusals) in Arabic from the stable keys, the English page
 *     unchanged, an older API's English sentences still worded;
 *   * lib/format.ts fmtDay — the ETA date read as a local calendar day;
 *   * lib/device.ts — opening a tracking link adopts the order (newest placed stays first, a placed
 *     order is never re-marked adopted) and "Same as last time" only ever uses an order placed here.
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

// a phone's storage, for lib/device.ts (read and write are wrapped there; this makes them work)
const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
}

const mod = (p) => import(pathToFileURL(path.join(SRC, p)).href)
const C = await mod('market/lib/orderChanges.ts')
const F = await mod('market/lib/format.ts')
const D = await mod('market/lib/device.ts')
const AR = await mod('market/lib/areaRep.ts')
const SW = await mod('market/lib/serverWords.ts')
const { en } = await mod('market/i18n/en.ts')
const { ar } = await mod('market/i18n/ar.ts')

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
function ok(v, what = '') {
  if (!v) throw new Error(`${what} expected truthy`)
}

/* ── synthetic public payload lines (the R7c line_view(public=True) shape + R7d reason_code) ── */
const line = (code, qty, extra = {}) => ({
  item_code: code, display_name: `Item ${code}`, qty, qty_confirmed: qty, qty_delivered: null,
  unit_price_bhd: 2, line_total_bhd: 2 * qty, line_status: 'ok', disposition: 'as_ordered',
  reason_code: null, reason_label: null, substitute_item_code: null, substitute_for: null, added_at_stage: null, ...extra,
})
const order = (lines, extra = {}) => ({ order_no: 'YQ-TEST-0001', status: 'confirmed', cancelled: false, lines, total_bhd: 30, total_confirmed_bhd: 30, total_effective_bhd: 30, ...extra })

/** a row in words — the very function components/WhatChanged.tsx renders with */
const words = (S, r) => C.changeText(S.track, r)

check('as ordered: no tile, no total change', () => {
  const ch = C.orderChanges(order([line('A1', 10), line('B2', 5)]))
  eq(ch.rows, [], 'rows')
  eq(ch.totalChanged, false, 'totalChanged')
})

check('reduced: "10 requested → 5 confirmed", the public reason key, the total before → after', () => {
  const ch = C.orderChanges(order([line('A1', 10, { qty_confirmed: 5, disposition: 'reduced', reason_code: 'out_of_stock' }), line('B2', 5)], { total_confirmed_bhd: 20, total_effective_bhd: 20 }))
  eq(ch.rows.length, 1, 'one row')
  const r = ch.rows[0]
  eq([r.kind, r.code, r.requested, r.confirmed, r.reason], ['reduced', 'A1', 10, 5, 'out_of_stock'], 'row')
  eq(words(en, r), '10 requested → 5 confirmed', 'English')
  eq(en.track.reasons[r.reason], 'Sold Out', 'out_of_stock reads Sold Out (never "Out of stock")')
  eq(words(ar, r), 'طُلب 10 ← أُكّد 5', 'Arabic')
  eq(ar.track.reasons[r.reason], 'نفدت الكمية', 'Arabic reason')
  eq([ch.before, ch.after, ch.totalChanged], [30, 20, true], 'totals')
})

check('unavailable: "6 requested · not available this time" — never "6 → 0"', () => {
  const ch = C.orderChanges(order([line('A1', 6, { qty_confirmed: 0, line_status: 'unavailable', disposition: 'unavailable', reason_code: 'discontinued' })], { total_effective_bhd: 0, total_confirmed_bhd: 0 }))
  const r = ch.rows[0]
  eq(r.kind, 'unavailable', 'kind')
  const text = words(en, r)
  eq(text, '6 requested · not available this time', 'English')
  ok(!/→\s*0\b/.test(text) && !/\b0 confirmed/.test(text), 'no "→ 0"')
  eq(en.track.reasons[r.reason], 'No longer available', 'reason')
})

check('substituted: one row "Replaced with UK20N × 4"; the replacement line is not listed again', () => {
  const lines = [
    line('UK20', 4, { qty_confirmed: 0, line_status: 'substituted', disposition: 'substituted', substitute_item_code: 'UK20N', reason_code: 'substituted' }),
    line('UK20N', 4, { qty_confirmed: 4, line_status: 'added', disposition: 'added', substitute_for: 'UK20', added_at_stage: 'confirm' }),
  ]
  const ch = C.orderChanges(order(lines))
  eq(ch.rows.length, 1, 'one row for the pair')
  eq(ch.rows[0].kind, 'substituted', 'kind')
  eq(ch.rows[0].replacement, { code: 'UK20N', name: 'Item UK20N', qty: 4 }, 'replacement')
  eq(words(en, ch.rows[0]), 'Replaced with UK20N × 4', 'English')
  ok(words(ar, ch.rows[0]).includes('⁦UK20N × 4⁩'), 'Arabic keeps the code and qty in an LTR island')
  // the replacement line missing from the payload: only what is certain
  const lone = C.orderChanges(order([lines[0]]))
  eq(lone.rows[0].kind, 'unavailable', 'no replacement on the payload → not available')
})

check('re-substitute: the row names the item really coming ("Replaced with C18 × 4"), never the dead substitute "× 0"', () => {
  const ch = C.orderChanges(order([
    line('UK20', 4, { qty_confirmed: 0, line_status: 'substituted', disposition: 'substituted', substitute_item_code: 'C18', reason_code: 'substituted' }),
    line('UK20N', 4, { qty_confirmed: 0, line_status: 'unavailable', disposition: 'unavailable', substitute_for: 'UK20', added_at_stage: 'confirm' }),
    line('C18', 4, { qty_confirmed: 4, line_status: 'added', disposition: 'added', substitute_for: 'UK20', added_at_stage: 'amend' }),
  ]))
  eq(ch.rows.map((r) => [r.code, r.kind]), [['UK20', 'substituted']], 'one row, and none for the dead UK20N')
  eq(ch.rows[0].replacement, { code: 'C18', name: 'Item C18', qty: 4 }, 'the live replacement')
  eq(words(en, ch.rows[0]), 'Replaced with C18 × 4', 'English')
  ok(ch.rows.every((r) => !words(en, r).includes('× 0') && !(r.replacement && r.replacement.qty === 0)), 'no zero-quantity replacement')
  // the same with the older payload shape (no disposition on the lines)
  const old = C.orderChanges(order([
    { item_code: 'UK20', qty: 4, qty_confirmed: 0, line_status: 'substituted', substitute_item_code: 'C18' },
    { item_code: 'UK20N', qty: 4, qty_confirmed: 0, line_status: 'unavailable', substitute_for: 'UK20', added_at_stage: 'confirm' },
    { item_code: 'C18', qty: 4, qty_confirmed: 4, line_status: 'added', substitute_for: 'UK20', added_at_stage: 'amend' },
  ]))
  eq(old.rows.map((r) => [r.code, r.kind, r.replacement && r.replacement.code]), [['UK20', 'substituted', 'C18']], 'older payload')
})

check('restore: the line back as ordered and its old substitutes taken off → no rows at all', () => {
  const ch = C.orderChanges(order([
    line('UK20', 4, { qty_confirmed: 4 }),
    line('UK20N', 4, { qty_confirmed: 0, line_status: 'unavailable', disposition: 'unavailable', substitute_for: 'UK20', added_at_stage: 'confirm' }),
    line('C18', 4, { qty_confirmed: 0, line_status: 'unavailable', disposition: 'unavailable', substitute_for: 'UK20', added_at_stage: 'amend' }),
  ]))
  eq(ch.rows, [], 'the shop never requested UK20N or C18')
  const less = C.orderChanges(order([
    line('UK20', 4, { qty_confirmed: 3, disposition: 'reduced' }),
    line('UK20N', 4, { qty_confirmed: 0, line_status: 'unavailable', disposition: 'unavailable', substitute_for: 'UK20', added_at_stage: 'confirm' }),
  ]))
  eq(less.rows.map((r) => [r.code, r.kind]), [['UK20', 'reduced']], 'restored lower: only its own numbers')
})

check('a chain A → B → C (the substitute itself substituted): "Replaced with C", B has no row', () => {
  const ch = C.orderChanges(order([
    line('A1', 6, { qty_confirmed: 0, line_status: 'substituted', disposition: 'substituted', substitute_item_code: 'B2' }),
    line('B2', 6, { qty_confirmed: 0, line_status: 'substituted', disposition: 'substituted', substitute_item_code: 'C3', substitute_for: 'A1', added_at_stage: 'confirm' }),
    line('C3', 5, { qty_confirmed: 5, line_status: 'added', disposition: 'added', substitute_for: 'B2', added_at_stage: 'amend' }),
  ]))
  eq(ch.rows.map((r) => [r.code, r.kind]), [['A1', 'substituted']], 'one row')
  eq(words(en, ch.rows[0]), 'Replaced with C3 × 5', 'the end of the chain')
  // a rep-added line (not a substitute) substituted: its live replacement is still listed as added
  const add = C.orderChanges(order([
    line('A1', 2),
    line('X9', 3, { qty_confirmed: 0, line_status: 'substituted', disposition: 'substituted', substitute_item_code: 'Y8', added_at_stage: 'confirm' }),
    line('Y8', 3, { qty_confirmed: 3, line_status: 'added', disposition: 'added', substitute_for: 'X9', added_at_stage: 'amend' }),
  ]))
  eq(add.rows.map((r) => [r.code, r.kind]), [['Y8', 'added']], 'the item coming is named; X9 was never requested')
})

check('reopen: a door-added line taken off again has no "requested · not available" row', () => {
  const ch = C.orderChanges(order([
    line('A1', 10, { qty_delivered: 10 }),
    line('D4', 2, { qty_confirmed: 0, qty_delivered: 0, line_status: 'unavailable', disposition: 'unavailable', added_at_stage: 'delivery' }),
  ], { status: 'confirmed' }))
  eq(ch.rows, [], 'no row for D4')
  // older payload: an added line at confirmed 0 is out, not "added · 0 pcs"
  eq(C.dispositionOf({ item_code: 'D4', qty: 2, qty_confirmed: 0, line_status: 'added', added_at_stage: 'delivery' }), 'unavailable', 'derived')
})

check('added by the rep and a delivery that differs: the "→ 8 delivered" tail', () => {
  const ch = C.orderChanges(order([
    line('A1', 10, { qty_delivered: 8 }),
    line('C3', 3, { line_status: 'added', disposition: 'added', added_at_stage: 'amend', reason_code: 'customer_changed' }),
    line('B2', 10, { qty_confirmed: 6, qty_delivered: 5, disposition: 'reduced', reason_code: 'damaged' }),
  ], { status: 'delivered' }))
  eq(ch.rows.map((r) => r.kind), ['delivered', 'added', 'reduced'], 'kinds in line order')
  eq(words(en, ch.rows[0]), '10 requested → 10 confirmed → 8 delivered', 'delivered differs')
  eq(words(en, ch.rows[1]), 'Added by your representative · 3 pcs', 'added')
  eq(words(en, ch.rows[2]), '10 requested → 6 confirmed → 5 delivered', 'reduced then delivered less')
  eq(en.track.reasons.customer_changed, 'As you asked', 'reason')
})

check('no tile: a backorder line, a cancelled order, and totals equal to the fils', () => {
  eq(C.orderChanges(order([line('A1', 4, { line_status: 'backorder', disposition: 'backorder' })])).rows, [], 'backorder is not a change')
  eq(C.orderChanges(order([line('A1', 10, { qty_confirmed: 5, disposition: 'reduced' })], { status: 'cancelled', cancelled: true })).rows, [], 'cancelled')
  const same = C.orderChanges(order([line('A1', 10, { qty_confirmed: 12, disposition: 'increased' })], { total_bhd: 20.0004, total_effective_bhd: 20.0001 }))
  eq(same.rows.length, 1, 'the change is listed')
  eq(same.totalChanged, false, 'the same total to the fils is not a total change')
})

check('older payloads (no disposition): removed reads not available; a lower qty_confirmed reads reduced', () => {
  const ch = C.orderChanges(order([
    { item_code: 'A1', qty: 5, qty_confirmed: 0, line_status: 'removed' },
    { item_code: 'B2', qty: 8, qty_confirmed: 6, line_status: 'changed' },
    { item_code: 'D4', qty: 2, qty_confirmed: null, line_status: 'ok' },
  ]))
  eq(ch.rows.map((r) => [r.code, r.kind]), [['A1', 'unavailable'], ['B2', 'reduced']], 'derived')
  ok(C.isOut({ item_code: 'A1', qty: 5, qty_confirmed: 0, line_status: 'removed' }), 'removed is out')
  eq(C.qtyNow({ item_code: 'X', qty: 5, qty_confirmed: 4, qty_delivered: 3 }), 3, 'qtyNow: delivered first')
  eq(C.lineMoneyNow({ item_code: 'X', qty: 5, line_total_bhd: 10, line_total_confirmed: 8 }), 8, 'money: confirmed over ordered')
})

check('fmtDay: the ETA date is a local calendar day ("Wed 30 Sep"), never shifted; junk is null', () => {
  const t = F.fmtDay('2026-09-30')
  ok(t && t.includes('30') && /Sep/.test(t) && /Wed/.test(t), `got ${t}`)
  eq(F.fmtDay(null), null, 'null')
  eq(F.fmtDay('next visit'), null, 'words are not a date')
})

check('device: a tracking link adopts the order; a placed order stays placed; "Same as last time" uses a placed order only', () => {
  store.clear()
  eq(D.lastPlacedOrder(), null, 'nothing yet')
  eq(D.adoptOrder({ token: 'adopted-token-000001', order_no: 'YQ-1', ts: 1000 }), true, 'adopted')
  eq(D.lastPlacedOrder(), null, 'an adopted order is never "Same as last time"')
  D.rememberOrder({ token: 'placed-token-0000002', order_no: 'YQ-2', ts: 2000 })
  eq(D.adoptOrder({ token: 'placed-token-0000002', order_no: 'YQ-2', ts: 2000 }), false, 'already held: left as it is')
  eq(D.adoptOrder({ token: 'short', order_no: 'YQ-X', ts: 1 }), false, 'not a token')
  eq(D.adoptOrder({ token: 'adopted-token-000003', order_no: 'YQ-3', ts: 3000 }), true, 'a newer one')
  eq(D.rememberedOrders().map((o) => [o.order_no, Boolean(o.adopted)]), [['YQ-3', true], ['YQ-2', false], ['YQ-1', true]], 'newest placed first')
  eq(D.lastPlacedOrder().order_no, 'YQ-2', 'the newest order PLACED here')
  ok(D.isRemembered('adopted-token-000001') && !D.isRemembered('nope-nope-nope-nope'), 'isRemembered')
})

check('area line: only when the area routes the order — never with a rep link (even a hidden-profile one), a pick or a known merchant', () => {
  const base = { hasRepCard: false, ref: null, pick: '', recognized: false, known: false, reuse: false, area: ' Riffa ', areaReps: { riffa: 'Bob' } }
  eq(AR.areaRepName(base), 'Bob', 'a new visitor, no link, area mapped')
  eq(AR.areaRepName({ ...base, ref: 'ali' }), null, '?ref=ali with no public profile (no card): the server routes to Ali — say nothing')
  eq(AR.areaRepName({ ...base, hasRepCard: true }), null, 'a rep card')
  eq(AR.areaRepName({ ...base, pick: 7 }), null, 'a pick')
  eq(AR.areaRepName({ ...base, recognized: true }), null, 'ordered from this device before')
  eq(AR.areaRepName({ ...base, known: true }), null, 'a recognised phone: the shop may have its own rep')
  eq(AR.areaRepName({ ...base, reuse: true }), null, 'Same as last time')
  eq(AR.areaRepName({ ...base, area: 'Sitra' }), null, 'an unmapped area')
  eq(AR.areaRepName({ ...base, areaReps: null }), null, 'no map')
})

/* ── the server's sentences in the page's language (lib/serverWords.ts) ── */
const money3 = (n) => `BHD ${Number(n).toFixed(3)}`
const W = (t, lang, asOf = null) => ({ t, lang, bhd: money3, asOf })
const hasArabic = (s) => /[؀-ۿ]/.test(String(s))
const SOLD = "Sold Out — can't be ordered right now. Remove it to send your order."
const soldQuote = (extra = {}) => ({
  lines: [
    { item_code: 'UK15', qty: 2, unavailable: true, blocked_reason: SOLD, blocked_code: 'sold_out', moq: 1 },
    { item_code: 'T02', qty: 1, unavailable: false, blocked_reason: null },
  ],
  can_submit: false, block_reason: 'Remove UK15 to send this order — Sold Out.', block_code: 'dead_lines', block_items: ['UK15'],
  warnings: ['X05 is Sold Out — it will be backordered and confirmed by your salesman.'], warning_codes: [{ code: 'backorder', item_code: 'X05' }],
  coupon: { code: 'NOPE', valid: false, message: 'This code is not valid or has expired.', reason: 'invalid' },
  ...extra,
})

check('Arabic cart: a sold-out line, the block, the backorder warning and the coupon answer read Arabic («نفدت الكمية»), never the English', () => {
  const q = soldQuote()
  const w = W(ar, 'ar')
  const line = SW.lineBlockedText(w, q.lines[0])
  ok(line.includes('نفدت الكمية') && !/Sold Out|Remove it/.test(line), `line: ${line}`)
  const block = SW.blockText(w, q)
  ok(hasArabic(block) && block.includes('⁦UK15⁩') && block.includes('نفدت الكمية') && !/Remove|send this order/.test(block), `block: ${block}`)
  const warn = SW.warningTexts(w, q)
  eq(warn.length, 1, 'one warning per server warning')
  ok(warn[0].includes('⁦X05⁩') && warn[0].includes('نفدت الكمية') && !/backordered/.test(warn[0]), `warning: ${warn[0]}`)
  const coupon = SW.couponText(w, q.coupon)
  ok(hasArabic(coupon) && !/not valid/.test(coupon), `coupon: ${coupon}`)
  // a stale snapshot puts its date on the line
  ok(SW.lineBlockedText(W(ar, 'ar', '21 سبتمبر'), q.lines[0]).includes('21 سبتمبر'), 'as-of date')
})

check('English page: the server sentence itself, unchanged', () => {
  const q = soldQuote()
  const w = W(en, 'en')
  eq(SW.lineBlockedText(w, q.lines[0]), SOLD, 'line')
  eq(SW.blockText(w, q), 'Remove UK15 to send this order — Sold Out.', 'block')
  eq(SW.warningTexts(w, q), q.warnings, 'warnings')
  eq(SW.couponText(w, q.coupon), 'This code is not valid or has expired.', 'coupon')
  eq(SW.errorText(w, 'Anything the server said'), 'Anything the server said', 'error')
})

check('an older API (no keys): the Arabic page still words the known English sentences', () => {
  const q = soldQuote({ block_code: undefined, block_items: undefined, warning_codes: undefined })
  q.lines[0] = { item_code: 'UK15', qty: 2, unavailable: true, blocked_reason: SOLD }
  const w = W(ar, 'ar')
  ok(SW.lineBlockedText(w, q.lines[0]).includes('نفدت الكمية'), 'line from prose')
  ok(SW.blockText(w, q).includes('⁦UK15⁩'), 'block from the dead lines')
  ok(SW.warningTexts(w, q)[0].includes('⁦X05⁩'), 'warning from prose')
  eq(SW.lineBlockedText(w, { item_code: 'M06', qty: 2, unavailable: true, blocked_reason: 'Minimum order is 6.' }), 'الحد الأدنى للطلب 6 قطع.', 'moq from prose')
})

check('Arabic: the other blocks, coupon answers, progress and the order refusals', () => {
  const w = W(ar, 'ar')
  const many = SW.blockText(w, { lines: ['A1', 'B2', 'C3', 'D4', 'E5'].map((c) => ({ item_code: c, unavailable: true, blocked_code: 'sold_out' })), block_code: 'dead_lines', block_items: ['A1', 'B2', 'C3', 'D4', 'E5'], block_reason: 'Remove A1, B2, C3 and 2 more to send this order.' })
  ok(many.includes('⁦A1, B2, C3⁩') && hasArabic(many) && !/Remove/.test(many), `many: ${many}`)
  eq(SW.blockText(w, { lines: [], block_code: 'empty', block_reason: 'Your order is empty.' }), ar.cart.orderEmpty, 'empty')
  const min = SW.blockText(w, { lines: [], block_code: 'minimum', block_reason: 'Minimum order is BHD 20.000 — add BHD 5.050 more.', min_order_bhd: 20, minimum: { value_bhd: 20, remaining_bhd: 5.05, met: false, mode: 'block', kind: 'standard' } })
  ok(min.includes('BHD 20.000') && min.includes('BHD 5.050') && hasArabic(min), `minimum: ${min}`)
  eq(SW.blockText(w, { lines: [], block_reason: 'Something new.' }), ar.cart.blocked, 'an unknown block: the Arabic generic, not English')
  for (const [c, want] of [
    [{ message: 'x', reason: 'add_more', amount_bhd: 3 }, 'BHD 3.000'],
    [{ message: 'x', reason: 'better_offer', rule_name: 'Bulk 5%' }, '⁦Bulk 5%⁩'],
    [{ message: 'x', reason: 'capped', amount_bhd: 1.5, valid: true }, 'BHD 1.500'],
    [{ message: 'x', reason: 'applied', code: 'SAVE5', valid: true }, '⁦SAVE5⁩'],
  ]) {
    const text = SW.couponText(w, c)
    ok(text.includes(want) && hasArabic(text), `${c.reason}: ${text}`)
  }
  ok(hasArabic(SW.progressText(w, { kind: 'free_delivery', remaining_bhd: 2, unlocked: false, label: 'Add BHD 2.000 more for free delivery' })), 'free delivery gap')
  ok(SW.progressText(w, { kind: 'cart_value', remaining_bhd: 4, unlocked: false, label: 'Add BHD 4.000 more to unlock Big Box' }).includes('⁦Big Box⁩'), 'cart offer from an older label')
  eq(SW.progressText(W(en, 'en'), { kind: 'free_delivery', remaining_bhd: 2, unlocked: false, label: 'Add BHD 2.000 more for free delivery' }), 'Add BHD 2.000 more for free delivery', 'English progress = the server label')
  eq(SW.errorText(w, "Please enter your phone number — the one from your last order can't be used on this phone."), ar.checkout.reuseFailed, 'reuse failed')
  eq(SW.errorText(w, 'This code is no longer available — it has run out or ended. Remove it to place your order.'), ar.checkout.couponGone, 'coupon gone')
  const q = soldQuote()
  eq(SW.errorText(w, q.block_reason, q), SW.blockText(w, q), 'a refusal that repeats the block is worded like the block')
  ok(SW.FIXED_SENTENCES.every((k) => typeof k === 'string' && k.length > 5), 'fixed sentences listed')
  // every fixed refusal has an Arabic answer
  for (const k of SW.FIXED_SENTENCES) ok(hasArabic(SW.errorText(w, k)), `Arabic for ${k}`)
})

check('words: the placed line, the checkout card and the search card exist in both languages', () => {
  eq(en.placed.sentTo('Harsh'), 'Sent to Harsh', 'sentTo')
  eq(en.placed.receivedBy('Harsh'), 'Received by YQ — Harsh will confirm', 'receivedBy')
  eq(en.checkout.sameAsLast, 'Same as last time', 'sameAsLast')
  eq(en.checkout.areaRep('Harsh'), 'New shops here are looked after by Harsh', 'areaRep (new shops, never "your" rep)')
  eq(en.shop.notOnShelf('memory card'), 'We don’t stock “memory card” yet', 'notOnShelf')
  ok(ar.placed.receivedBy('Harsh').includes('Harsh') && /[؀-ۿ]/.test(ar.placed.receivedBy('Harsh')), 'Arabic receivedBy')
  ok(/[؀-ۿ]/.test(ar.checkout.areaRep('Harsh')), 'Arabic areaRep')
})

check('clearing, not "last chance": the wording in both languages', () => {
  eq(en.deals.badge, 'Clearing line', 'badge')
  eq(en.deals.lastChance, 'Clearing lines · trade price', 'section')
  eq(en.shop.clearance, 'Clearing lines', 'chip')
  const all = JSON.stringify(en) + JSON.stringify(ar) + [1, 12].map((n) => en.campaign.categoryLast(n) + en.spot.clearance(n) + en.slides.lastLine(n) + ar.campaign.categoryLast(n) + ar.spot.clearance(n)).join('')
  ok(!/last.chance/i.test(all), 'no "last chance" in English')
  ok(!/فرصة أخيرة|الفرصة الأخيرة/.test(all), 'no «فرصة أخيرة» in Arabic')
  ok(ar.deals.badge.includes('تصفية') && ar.deals.lastChance.includes('تصفية'), 'Arabic says تصفية')
  ok(en.slides.lastLine(24).length <= 34 && ar.slides.lastLine(24).length <= 34, 'a slide line stays one clause of ≤34 characters')
  eq([en.card.soldOut, en.card.stockOut, ar.card.soldOut], ['Sold Out', 'Sold Out', 'نفدت الكمية'], 'Sold Out casing')
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
