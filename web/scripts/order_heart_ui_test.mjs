/**
 * The order heart's page-side rules (R7c, Sprint 3), exercised on the real module — plain node, no
 * test runner, no new deps.
 *
 *   node web/scripts/order_heart_ui_test.mjs        (from the repo root or from web/)
 *
 * web/src/pages/shop-ops/heart.ts is transpiled on load with the project's own `typescript` (a load
 * hook from module.registerHooks, Node 22.15+ — the same loader as soldout_order_test.mjs). Nothing
 * is compiled to disk. Exit 1 on any failure. tests/test_r7c_ui.py runs this script and reports its
 * result; the CI web job runs it after `npm ci`.
 *
 * What it proves, on hand-worked orders (synthetic codes, no real data): the three visible stages;
 * the confirmed-else-original money; every changed line needs a reason ('other' needs a note); the
 * "Shop agreed" tick is asked for exactly the adverse changes app/shop_heart.plan_edit refuses
 * without it (a substitute at another price, goods that come later, a cut that crosses the minimum —
 * not a plain stock cut, not an order already under the minimum); the pro-rata cart share to the
 * fils; deliver-with-changes; and what the drawer offers for each status.
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
function ok(v, what = '') {
  if (!v) throw new Error(`${what} expected truthy`)
}

/* ── a synthetic order: two requested lines, BHD 20 minimum ── */
const line = (id, code, qty, unit, extra = {}) => ({
  id, item_code: code, display_name: `Item ${code}`, qty, unit_price_bhd: unit,
  line_total_bhd: Math.round(unit * qty * 1000) / 1000, discount_bhd: 0, line_status: 'ok', ...extra,
})
const order = (extra = {}) => ({
  status: 'new', total_bhd: 23.0, total_confirmed_bhd: null, delivery_bhd: 0, discount_bhd: 0, min_order_bhd: 20,
  lines: [line(1, 'TST-A', 10, 1.5), line(2, 'TST-B', 4, 2.0)], ...extra,
})
const catalog = H.indexItems([
  { item_code: 'TST-A', category: 'CABLES', price_bhd: 1.5, stock_qty: 5 },
  { item_code: 'TST-B', category: 'CHARGERS', price_bhd: 2.0, stock_qty: 50 },
  { item_code: 'TST-C', category: 'CABLES', price_bhd: 1.6, stock_qty: 40 },
  { item_code: 'TST-D', category: 'CABLES', price_bhd: 1.5, stock_qty: 12 },
  { item_code: 'TST-E', category: 'CABLES', price_bhd: 1.45, stock_qty: 0 },
  { item_code: 'TST-F', category: 'CABLES', price_bhd: null, stock_qty: 9 },
  { item_code: 'TST-T', category: 'CASES', price_bhd: 3.0, stock_qty: 30, tiers: [{ min_qty: 10, unit_price_bhd: 2.7 }, { min_qty: 50, unit_price_bhd: 2.5 }] },
])
const draftWith = (o, patch) => {
  const d = H.initialDraft(o.lines)
  for (const [id, p] of Object.entries(patch)) d[id] = { ...d[id], ...p }
  return d
}

/* ── the three visible stages ── */
check('packed / out_for_delivery read as Confirmed; four visible stages', () => {
  eq(H.visibleStatus('packed'), 'confirmed')
  eq(H.visibleStatus('out_for_delivery'), 'confirmed')
  eq(H.visibleStatus('new'), 'new')
  eq(H.STATUS_LABEL.packed, 'Confirmed')
  eq(H.STATUS_LABEL.out_for_delivery, 'Confirmed')
  eq([...H.VISIBLE_STATUSES], ['new', 'confirmed', 'delivered', 'cancelled'])
  eq(H.VISIBLE_STATUSES.map((v) => H.VISIBLE_LABEL[v]), ['Received', 'Confirmed', 'Delivered', 'Cancelled'])
  eq(H.FILTER_PARAM.confirmed, 'confirmed,packed,out_for_delivery')
  eq(H.visibleCount({ confirmed: 2, packed: 1, out_for_delivery: 3, new: 9 }, 'confirmed'), 6)
})

check('old deep links (?bucket=new|progress|done) land on a visible stage', () => {
  eq(H.bucketFromParam('progress'), 'confirmed')
  eq(H.bucketFromParam('done'), 'delivered')
  eq(H.bucketFromParam('new'), 'new')
  eq(H.bucketFromParam('received'), 'new')
  eq(H.bucketFromParam('cancelled'), 'cancelled')
  eq(H.bucketFromParam('<script>'), 'new')
  eq(H.bucketFromParam(null), 'new')
})

check('stages: the server steps win; an older five-step answer is recomputed as three', () => {
  const s = H.stagesOf({ status: 'packed', created_at: '2026-09-27T06:00:00Z', confirmed_at: '2026-09-27T07:00:00Z' })
  eq(s.map((x) => x.label), ['Received', 'Confirmed', 'Delivered'])
  eq(s.map((x) => x.current), [false, true, false])
  eq(s.map((x) => x.done), [true, false, false])
  const d = H.stagesOf({ status: 'delivered' })
  eq(d.map((x) => x.done), [true, true, true])
})

/* ── money ── */
check('effective money = confirmed ?? original; the as-ordered figure only when different', () => {
  eq(H.effectiveTotal({ total_bhd: 23, total_confirmed_bhd: null }), 23)
  eq(H.effectiveTotal({ total_bhd: 23, total_confirmed_bhd: 15.5 }), 15.5)
  eq(H.effectiveTotal({ total_bhd: 23, total_confirmed_bhd: 15.5, total_effective_bhd: 15.5 }), 15.5)
  eq(H.totalChanged({ total_bhd: 23, total_confirmed_bhd: 23.0 }), false)
  eq(H.totalChanged({ total_bhd: 23, total_confirmed_bhd: null }), false)
  eq(H.totalChanged({ total_bhd: 23, total_confirmed_bhd: 22.999 }), true)
})

check('a line: confirmed-else-original money, delivered once delivered, never struck for an added line', () => {
  const l = line(1, 'TST-A', 10, 1.5, { qty_confirmed: 5, unit_price_confirmed: 1.5, line_total_confirmed: 7.5 })
  eq(H.lineMoney(l, 'confirmed'), { now: 7.5, was: 15 })
  eq(H.lineMoney({ ...l, line_total_delivered: 4.5, qty_delivered: 3 }, 'delivered'), { now: 4.5, was: 15 })
  eq(H.lineMoney(line(3, 'TST-C', 5, 1.6, { line_status: 'added', added_at_stage: 'confirm', qty_confirmed: 5, line_total_confirmed: 8 }), 'confirmed'), { now: 8, was: null })
  // an older payload without line_total_confirmed: the locked unit × confirmed quantity, in fils
  eq(H.lineMoney(line(4, 'TST-X', 3, 0.333, { qty_confirmed: 2 }), 'confirmed'), { now: 0.666, was: 0.999 })
})

check('dispositions mirror shop_heart.disposition on an older payload', () => {
  eq(H.dispositionOf(line(1, 'A', 10, 1, { qty_confirmed: 5 })), 'reduced')
  eq(H.dispositionOf(line(1, 'A', 10, 1, { qty_confirmed: 0, line_status: 'removed' })), 'unavailable')
  eq(H.dispositionOf(line(1, 'A', 10, 1, { qty_confirmed: 0, line_status: 'substituted', substitute_item_code: 'C' })), 'substituted')
  eq(H.dispositionOf(line(1, 'A', 5, 1, { line_status: 'added', added_at_stage: 'amend' })), 'added')
  eq(H.dispositionOf(line(1, 'A', 5, 1, { line_status: 'backorder' })), 'backorder')
  eq(H.dispositionOf(line(1, 'A', 5, 1)), 'as_ordered')
  eq(H.dispositionOf({ ...line(1, 'A', 5, 1), disposition: 'reduced' }), 'reduced', 'server wins')
  eq(H.dispositionChip(line(1, 'A', 10, 1, { qty_confirmed: 0, line_status: 'substituted', substitute_item_code: 'TST-C' })).label, 'Substituted → TST-C')
  eq(H.dispositionChip(line(1, 'A', 5, 1)), null)
})

/* ── live stock ── */
check('stock: In stock N / Only N / Sold out against the quantity wanted; the order-time word without the catalog', () => {
  eq(H.stockView(H.itemOf(catalog, 'TST-B'), 4).label, 'In stock 50')
  eq(H.stockView(H.itemOf(catalog, 'tst-a'), 10), { label: 'Only 5', tone: 'amber', available: 5 })
  eq(H.stockView(H.itemOf(catalog, 'TST-E'), 1), { label: 'Sold out', tone: 'rose', available: 0 })
  eq(H.stockView(undefined, 3, 'low_stock').label, 'Only a few left')
  eq(H.stockView(undefined, 3, null).label, '')
})

check('today’s book price reaches the best tier; substitutes = same category, in stock, priced, closest price first', () => {
  eq(H.unitAt(H.itemOf(catalog, 'TST-T'), 9), 3)
  eq(H.unitAt(H.itemOf(catalog, 'TST-T'), 10), 2.7)
  eq(H.unitAt(H.itemOf(catalog, 'TST-T'), 60), 2.5)
  eq(H.unitAt(H.itemOf(catalog, 'TST-F'), 1), null)
  const subs = H.substituteOptions(catalog, 'TST-A', 1.5, 10)
  eq(subs.map((o) => o.item.item_code), ['TST-D', 'TST-C'], 'E is sold out, F unpriced, B/T another category')
  eq(subs.map((o) => o.diff), [0, 0.1])
  eq(H.searchItems(catalog, 'tst-', 1, null, new Set(['TST-A'])).map((o) => o.item.item_code)[0] !== 'TST-A', true)
  eq(H.searchItems(catalog, 't', 1, null, new Set()), [], 'two letters at least')
})

/* ── the editor: reasons ── */
check('a changed line needs a reason; ‘other’ needs a 3-letter note; untouched lines are not sent', () => {
  const o = order()
  let p = H.planEdit(o, draftWith(o, { 1: { qty: 9 } }), [], catalog, 'confirm')
  eq(p.needReason, ['TST-A'])
  eq(p.lines, [{ line_id: 1, qty_confirmed: 9 }])
  p = H.planEdit(o, draftWith(o, { 1: { qty: 9, reason: 'other', note: 'ok' } }), [], catalog, 'confirm')
  eq(p.needReason, ['TST-A'])
  p = H.planEdit(o, draftWith(o, { 1: { qty: 9, reason: 'other', note: 'shop asked twice' } }), [], catalog, 'confirm')
  eq(p.needReason, [])
  eq(p.lines, [{ line_id: 1, reason: 'other', note: 'shop asked twice', qty_confirmed: 9 }])
  p = H.planEdit(o, H.initialDraft(o.lines), [], catalog, 'confirm')
  eq([p.lines, p.changed, p.estTotal, p.adverse], [[], 0, 23, []], 'as ordered')
})

check('0 = unavailable (‘removed’ to an API from before R7c); every line out = cancel instead', () => {
  const o = order()
  let p = H.planEdit(o, draftWith(o, { 2: { qty: 0, reason: 'out_of_stock' } }), [], catalog, 'confirm')
  eq(p.lines, [{ line_id: 2, reason: 'out_of_stock', qty_confirmed: 0, line_status: 'unavailable' }])
  p = H.planEdit(o, draftWith(o, { 2: { qty: 0, reason: 'out_of_stock' } }), [], catalog, 'confirm', true)
  eq(p.lines[0].line_status, 'removed')
  p = H.planEdit(o, draftWith(o, { 1: { qty: 0, reason: 'out_of_stock' }, 2: { qty: 0, reason: 'out_of_stock' } }), [], catalog, 'confirm')
  eq(p.allOut, true)
})

/* ── the editor: the adverse tick ── */
check('a plain stock cut that stays above the minimum needs no tick', () => {
  const o = order()
  const p = H.planEdit(o, draftWith(o, { 1: { qty: 9, reason: 'out_of_stock' } }), [], catalog, 'confirm')
  eq(p.adverse, [])
  eq(p.estTotal, 21.5)
})

check('a cut that takes the order from >= BHD 20 to < BHD 20 needs the tick; an order already under it does not', () => {
  const o = order()
  let p = H.planEdit(o, draftWith(o, { 1: { qty: 5, reason: 'out_of_stock' } }), [], catalog, 'confirm')
  eq(p.adverse.map((a) => a.kind), ['below_minimum'])
  eq([p.beforeTotal, p.estTotal], [23, 15.5])
  const small = order({ total_bhd: 15.5, lines: [line(1, 'TST-A', 5, 1.5), line(2, 'TST-B', 4, 2.0)] })
  p = H.planEdit(small, draftWith(small, { 1: { qty: 2, reason: 'out_of_stock' } }), [], catalog, 'confirm')
  eq(p.adverse, [])
  // exactly the minimum is not below it
  const at = order({ total_bhd: 24, lines: [line(1, 'TST-A', 8, 1.5), line(2, 'TST-B', 6, 2.0)] })
  p = H.planEdit(at, draftWith(at, { 1: { qty: 0, reason: 'out_of_stock' }, 2: { qty: 10, reason: 'customer_changed' } }), [], catalog, 'confirm')
  eq([p.estTotal, p.adverse], [20, []])
})

check('the minimum is measured without delivery; amend measures from the confirmed total', () => {
  const o = order({ total_bhd: 24, delivery_bhd: 1, lines: [line(1, 'TST-A', 10, 1.5), line(2, 'TST-B', 4, 2.0)] })
  let p = H.planEdit(o, draftWith(o, { 2: { qty: 1, reason: 'out_of_stock' } }), [], catalog, 'confirm')
  eq(p.estTotal, 18, '15 + 2 + 1 delivery')
  eq(p.adverse.map((a) => a.kind), ['below_minimum'])
  const conf = order({ status: 'confirmed', total_confirmed_bhd: 18.5,
    lines: [line(1, 'TST-A', 10, 1.5, { qty_confirmed: 7 }), line(2, 'TST-B', 4, 2.0, { qty_confirmed: 4 })] })
  p = H.planEdit(conf, draftWith(conf, { 1: { qty: 6, reason: 'damaged' } }), [], catalog, 'amend')
  eq([p.beforeTotal, p.estTotal, p.adverse], [18.5, 17, []], 'already under the minimum after the confirm')
})

check('a substitute at another price needs the tick; at the same price it does not; a sold-out one comes later', () => {
  const o = order()
  let p = H.planEdit(o, draftWith(o, { 1: { sub: { code: 'TST-C', qty: 10 } } }), [], catalog, 'confirm')
  eq(p.lines, [{ line_id: 1, substitute_item_code: 'TST-C', substitute_qty: 10, reason: 'substituted' }])
  eq(p.adverse, [{ kind: 'substitute_price', item_code: 'TST-A', substitute: 'TST-C' }])
  eq(p.estTotal, 24)
  p = H.planEdit(o, draftWith(o, { 1: { sub: { code: 'TST-D', qty: 10 } } }), [], catalog, 'confirm')
  eq(p.adverse, [])
  p = H.planEdit(o, draftWith(o, { 1: { sub: { code: 'TST-E', qty: 10 } } }), [], catalog, 'confirm')
  eq(p.adverse.map((a) => a.kind), ['substitute_price', 'backorder'])
})

check('marking a line "comes later" is a backorder (adverse); a line already on backorder is not newly marked', () => {
  const o = order()
  let p = H.planEdit(o, draftWith(o, { 1: { backorder: true, reason: 'out_of_stock' } }), [], catalog, 'confirm')
  eq(p.lines, [{ line_id: 1, reason: 'out_of_stock', qty_confirmed: 10, line_status: 'backorder', backorder: true }])
  eq(p.adverse.map((a) => a.kind), ['backorder'])
  const bo = order({ lines: [line(1, 'TST-A', 10, 1.5, { backorder: true, line_status: 'backorder' }), line(2, 'TST-B', 4, 2)] })
  p = H.planEdit(bo, draftWith(bo, { 1: { backorder: true } }), [], catalog, 'confirm')
  eq([p.lines, p.adverse], [[], []])
})

check('an added item goes as added_lines (reason: shop changed it); a sold-out one needs the tick', () => {
  const o = order()
  let p = H.planEdit(o, H.initialDraft(o.lines), [{ key: 'k1', code: 'TST-T', qty: 10 }], catalog, 'confirm')
  eq(p.added_lines, [{ item_code: 'TST-T', qty: 10, reason: 'customer_changed' }])
  eq([p.changed, p.estTotal, p.adverse], [1, 50, []], '23 + 10 × 2.700')
  p = H.planEdit(o, H.initialDraft(o.lines), [{ key: 'k2', code: 'TST-E', qty: 2 }], catalog, 'confirm')
  eq(p.adverse.map((a) => a.kind), ['backorder'])
})

check('the order’s own cart discount is shared pro rata over the requested lines, to the fils', () => {
  // as placed: items 23.000, cart discount 2.300, delivery 1.000 → 21.700
  const o = order({ total_bhd: 21.7, discount_bhd: 2.3, delivery_bhd: 1 })
  const p = H.planEdit(o, draftWith(o, { 1: { qty: 5, reason: 'out_of_stock' } }), [], catalog, 'confirm')
  // items 15.500 → share 2.300 × 15.5 / 23 = 1.550 → 15.500 − 1.550 + 1.000
  eq(p.estTotal, 14.95)
  // an added line carries no share
  const q = H.planEdit(o, H.initialDraft(o.lines), [{ key: 'k', code: 'TST-B', qty: 1 }], catalog, 'confirm')
  eq(q.estTotal, 23.7)
})

check('a substitute inherits the replaced line’s share; the share never outgrows the discount as placed (shop_heart.compute_totals)', () => {
  // as placed: items 23.000, cart discount 2.300, delivery 1.000 → 21.700
  const o = order({ total_bhd: 21.7, discount_bhd: 2.3, delivery_bhd: 1 })
  // TST-B (8.000) → TST-C × 4 (6.400): the share follows it — 2.300 × 21.4 / 23 = 2.140 (not 1.500 on TST-A alone)
  let p = H.planEdit(o, draftWith(o, { 2: { sub: { code: 'TST-C', qty: 4 } } }), [], catalog, 'confirm')
  eq(p.estTotal, 20.26, '21.400 − 2.140 + 1.000')
  // TST-A 10 → 20: 2.300 × 38 / 23 = 3.800 would outgrow the 2.300 placed — capped
  p = H.planEdit(o, draftWith(o, { 1: { qty: 20, reason: 'customer_changed' } }), [], catalog, 'confirm')
  eq(p.estTotal, 36.7, '38.000 − 2.300 + 1.000')
  // a saved substitute (substitute_for_line) of a requested line carries its share; one of an added line does not
  const saved = order({ status: 'confirmed', total_bhd: 21.7, total_confirmed_bhd: 20.26, discount_bhd: 2.3, delivery_bhd: 1,
    lines: [line(1, 'TST-A', 10, 1.5, { qty_confirmed: 10 }),
      line(2, 'TST-B', 4, 2.0, { qty_confirmed: 0, line_status: 'substituted', substitute_item_code: 'TST-C' }),
      line(3, 'TST-C', 4, 1.6, { qty_confirmed: 4, line_status: 'added', added_at_stage: 'confirm', substitute_for_line: 2 }),
      line(4, 'TST-D', 2, 1.5, { qty_confirmed: 2, line_status: 'added', added_at_stage: 'confirm' }),
      line(5, 'TST-C', 1, 1.6, { qty_confirmed: 1, line_status: 'added', added_at_stage: 'amend', substitute_for_line: 4 })] })
  p = H.planEdit(saved, H.initialDraft(saved.lines), [], catalog, 'amend')
  // items 15 + 6.4 + 3 + 1.6 = 26.000; share over 15 + 6.4 = 21.4 → 2.140; 26 − 2.14 + 1 = 24.86
  eq(p.estTotal, 24.86)
})

/* ── deliver with changes ── */
check('deliver: equal to confirmed = the one tap (nothing sent); a difference needs a reason; below the minimum needs the tick', () => {
  const o = order({ status: 'confirmed', total_confirmed_bhd: 23,
    lines: [line(1, 'TST-A', 10, 1.5, { qty_confirmed: 10 }), line(2, 'TST-B', 4, 2, { qty_confirmed: 4 }),
      line(3, 'TST-Z', 2, 1, { qty_confirmed: 0, line_status: 'unavailable' })] })
  eq(H.deliverableLines(o.lines).map((l) => l.id), [1, 2])
  let p = H.planDeliver(o, H.initialDeliverDraft(o.lines), [], catalog)
  eq([p.withChanges, p.lines, p.adverse, p.estTotal], [false, [], [], 23])
  const d = H.initialDeliverDraft(o.lines)
  d[1] = { qty: 8, reason: '', note: '' }
  p = H.planDeliver(o, d, [], catalog)
  eq(p.needReason, ['TST-A'])
  d[1] = { qty: 8, reason: 'damaged', note: '' }
  p = H.planDeliver(o, d, [], catalog)
  eq([p.lines, p.estTotal, p.adverse], [[{ line_id: 1, qty_delivered: 8, reason: 'damaged' }], 20, []])
  d[1] = { qty: 5, reason: 'damaged', note: '' }
  p = H.planDeliver(o, d, [], catalog)
  eq(p.adverse.map((a) => a.kind), ['below_minimum'])
  p = H.planDeliver(o, d, [{ key: 'k', code: 'TST-B', qty: 3 }], catalog)
  eq([p.added, p.estTotal, p.adverse], [[{ item_code: 'TST-B', qty: 3, reason: 'customer_changed' }], 21.5, []])
  const none = { 1: { qty: 0, reason: 'damaged', note: '' }, 2: { qty: 0, reason: 'damaged', note: '' }, 3: { qty: 0, reason: '', note: '' } }
  eq(H.planDeliver(o, none, [], catalog).nothing, true)
})

/* ── what the drawer offers ── */
check('actions: the server list wins (filtered to known keys); management gets none; an older API gets the forward steps only', () => {
  eq(H.actionsOf({ status: 'confirmed', actions: ['deliver', 'amend', 'cancel', 'tell_shop', 'packed'] }, false),
    { list: ['deliver', 'amend', 'cancel', 'tell_shop'], legacy: false })
  eq(H.actionsOf({ status: 'confirmed', actions: ['deliver'] }, true), { list: [], legacy: false })
  eq(H.actionsOf({ status: 'new' }, false), { list: ['confirm', 'cancel'], legacy: true })
  eq(H.actionsOf({ status: 'out_for_delivery', whatsapp_url: 'https://wa.me/x' }, false), { list: ['deliver', 'cancel', 'tell_shop'], legacy: true })
  eq(H.actionsOf({ status: 'delivered' }, false), { list: [], legacy: true })
  eq(H.actionsOf(null, false), { list: [], legacy: false })
})

check('reopen goes back one stage; "Shop not told yet" only after a step', () => {
  eq(H.reopenTarget('delivered'), 'confirmed')
  eq(H.reopenTarget('cancelled'), 'new')
  eq(H.reopenTarget('confirmed'), null)
  eq(H.shopNotTold({ status: 'confirmed', shop_told: false }), true)
  eq(H.shopNotTold({ status: 'new', shop_told: false }), false)
  eq(H.shopNotTold({ status: 'delivered', shop_told: true }), false)
  eq(H.shopNotTold({ status: 'delivered' }), false, 'an older API says nothing')
})

check('the server’s exact messages and vocabulary', () => {
  eq(H.ADVERSE_MSG, "Tick 'Shop agreed' — this change costs the shop more or goes below the minimum")
  eq(H.CAS_MSG, 'This order changed a moment ago — refresh and try again')
  eq(H.CHANGE_REASONS.map((r) => r.code), ['out_of_stock', 'discontinued', 'price', 'customer_changed', 'substituted', 'damaged', 'other'])
  eq(H.AGREED_VIA.map((v) => v.code), ['whatsapp', 'phone', 'visit'])
  eq(H.reasonOptions({ out_of_stock: 'Out of stock', other: 'Other' }).map((r) => r.code), ['out_of_stock', 'other'])
  eq(H.reasonOptions(null).length, 7)
  ok(H.reasonReady('price', ''))
  ok(!H.reasonReady('', 'x'))
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
