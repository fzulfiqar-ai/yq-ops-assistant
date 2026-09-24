/**
 * The sold-out rule, exercised on the real listing helpers — plain node, no test runner, no deps.
 *
 *   node web/scripts/soldout_order_test.mjs        (from the repo root or from web/)
 *
 * Node 22.15+/24 strips the TypeScript types on import; the one thing it cannot do is resolve the
 * extensionless relative imports and the `@/` alias the Vite build resolves, so a resolve hook
 * (module.registerHooks) maps those onto src/*.ts. Nothing is compiled or written. Exit 1 on any
 * failure. tests/test_r1_soldout.py runs this script and reports its result.
 */
import { registerHooks } from 'node:module'
import { existsSync, statSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

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
})

const mod = (rel) => import(pathToFileURL(path.join(SRC, rel)).href)
const facets = await mod('market/lib/facets.ts')
const search = await mod('market/lib/search.ts')
const groups = await mod('market/lib/searchGroups.ts')
const quick = await mod('market/lib/quickParse.ts')
const { S } = await mod('market/strings.ts')

/* ── a small catalog: shelf order is the array order, as the payload's is ── */
const item = (code, stock, extra = {}) => ({
  item_code: code,
  display_name: extra.name || code,
  spec: extra.spec || `${code} spec`,
  category: extra.category || 'CABLE',
  brand: 'VFAN',
  price_bhd: extra.price ?? 1,
  stock_status: stock,
  badges: extra.badges || [],
  social_proof: extra.proof || null,
  tiers: [],
  moq: 1,
  pack_size: null,
})
const CATALOG = [
  item('C01', 'out_of_stock', { name: 'Type-C cable 1m', price: 0.5, badges: ['best_seller'] }),
  item('C02', 'in_stock', { name: 'Type-C cable 2m', price: 0.9 }),
  item('C03', 'low_stock', { name: 'Type-C braided cable 1m', price: 1.2, badges: ['trending'] }),
  item('C04', 'out_of_stock', { name: 'Lightning cable 1m', price: 0.8 }),
  item('C05', 'in_stock', { name: 'Lightning cable 2m', price: 1.5, badges: ['best_seller'], proof: 'Ordered by 12 shops this month' }),
  item('UK15', 'in_stock', { name: '20W charger', price: 1.5, category: 'CHARGER' }),
  item('UK16', 'out_of_stock', { name: '20W charger dual', price: 2.0, category: 'CHARGER' }),
]
const codes = (arr) => arr.map((i) => i.item_code)
const out = (i) => i.stock_status === 'out_of_stock'
const firstOut = (arr) => arr.findIndex(out)
const noAvailableAfterSoldOut = (arr) => {
  const k = firstOut(arr)
  return k < 0 || arr.slice(k).every(out)
}

/* ── the runner ── */
const TESTS = []
const test = (name, fn) => TESTS.push([name, fn])
const assert = (cond, msg) => {
  if (!cond) throw new Error(msg)
}
const eq = (a, b, msg) => assert(JSON.stringify(a) === JSON.stringify(b), `${msg}: got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`)

test('strings: zero stock reads "Sold out", never "Out of stock"; the Arabic sits beside it', () => {
  eq(S.card.stockOut, 'Sold out', 'S.card.stockOut')
  eq(S.card.soldOut, 'Sold out', 'S.card.soldOut')
  eq(S.card.stockOutAr, 'نفدت الكمية', 'S.card.stockOutAr')
  assert(!/out of stock/i.test(S.card.backorderNote), 'backorderNote still says "Out of stock"')
})

test('partitionByAvailability: stable — each group keeps its incoming order', () => {
  eq(codes(facets.partitionByAvailability(CATALOG)), ['C02', 'C03', 'C05', 'UK15', 'C01', 'C04', 'UK16'], 'partition')
  eq(codes(facets.partitionByAvailability([])), [], 'empty')
})

test('sortItems: every sort applies within the availability groups', () => {
  const shelf = facets.sortItems(CATALOG, 'shelf')
  eq(codes(shelf), ['C02', 'C03', 'C05', 'UK15', 'C01', 'C04', 'UK16'], 'shelf')
  const asc = facets.sortItems(CATALOG, 'price_asc')
  assert(noAvailableAfterSoldOut(asc), 'price_asc: an available line after a sold-out one')
  eq(codes(asc), ['C02', 'C03', 'C05', 'UK15', 'C01', 'C04', 'UK16'], 'price_asc within groups')
  const desc = facets.sortItems(CATALOG, 'price_desc')
  assert(noAvailableAfterSoldOut(desc), 'price_desc: an available line after a sold-out one')
  eq(codes(desc), ['C05', 'UK15', 'C03', 'C02', 'UK16', 'C04', 'C01'], 'price_desc within groups')
  const pop = facets.sortItems(CATALOG, 'popular')
  assert(noAvailableAfterSoldOut(pop), 'popular: an available line after a sold-out one')
  // C01 is a best seller but sold out: it must not lead the popular sort
  eq(pop[0].item_code, 'C05', 'popular leads with the available best seller')
  eq(pop[pop.length - 3].item_code, 'C01', 'the sold-out best seller leads only the sold-out group')
})

test('shelfOrder: the plain shelf and every destination shelf keep sold out last', () => {
  eq(codes(facets.shelfOrder(CATALOG, new Set())), ['C02', 'C03', 'C05', 'UK15', 'C01', 'C04', 'UK16'], 'plain shelf')
  for (const f of ['best', 'moving', 'new', 'deals', 'clearance', 'drops', 'offers']) {
    assert(noAvailableAfterSoldOut(facets.shelfOrder(CATALOG, new Set([f]))), `destination ${f}`)
  }
})

test('homeGridOrder: unseen available → available on a rail → sold out in shelf order', () => {
  // C01 is sold out AND in the rail set (a last-order code): it keeps its shelf place in the sold-out band
  const rails = new Set(['C02', 'C01'])
  eq(codes(facets.homeGridOrder(CATALOG, rails)), ['C03', 'C05', 'UK15', 'C02', 'C01', 'C04', 'UK16'], 'home grid')
  eq(codes(facets.homeGridOrder(CATALOG, new Set())), ['C02', 'C03', 'C05', 'UK15', 'C01', 'C04', 'UK16'], 'no rails')
})

test('searchItems: availability is the primary key, then the score band', () => {
  const index = search.buildIndex(CATALOG)
  const hits = search.searchItems(index, CATALOG, 'type c cable')
  for (const c of ['C01', 'C02', 'C03']) assert(hits.some((h) => h.item_code === c), `expected ${c} among the cable hits, got ${codes(hits)}`)
  assert(noAvailableAfterSoldOut(hits), `search: an available line after a sold-out one — ${codes(hits)}`)
  // C01 is a best seller and the shortest name match, so it wins on score; sold out, it trails
  assert(hits[0].item_code !== 'C01', `the sold-out best match leads the results: ${codes(hits)}`)
  const lit = search.searchItems(index, CATALOG, 'lightning')
  eq(codes(lit).slice(0, 1), ['C05'], 'lightning: the available line first')
  assert(lit.some((h) => h.item_code === 'C04'), 'the sold-out lightning cable still shows')
})

test('groupResults: the Codes group is partitioned before the cut to three', () => {
  const index = search.buildIndex(CATALOG)
  const g = groups.groupResults('c0', CATALOG, index, search.searchItems)
  eq(codes(g.codes), ['C02', 'C03', 'C05'], 'codes: the three available C0* lines lead')
  assert(noAvailableAfterSoldOut(g.products), 'products group')
  const uk = groups.groupResults('uk', CATALOG, index, search.searchItems)
  eq(codes(uk.codes), ['UK15', 'UK16'], 'codes: available first, sold out kept')
  eq(uk.exact, null, 'no exact code')
})

test('resolveQuery: an exact sold-out code is never resolved — it is offered after in-stock siblings', () => {
  const index = search.buildIndex(CATALOG)
  const r = quick.resolveQuery('c01', CATALOG, index)
  eq(r.item, null, 'sold-out exact code resolved')
  assert(r.candidates.some((c) => c.item_code === 'C01'), 'the sold-out line is offered')
  assert(noAvailableAfterSoldOut(r.candidates), `candidates: ${codes(r.candidates)}`)
  assert(r.candidates[0].item_code !== 'C01', `in-stock alternatives first: ${codes(r.candidates)}`)
  const ok = quick.resolveQuery('uk-15', CATALOG, index)
  eq(ok.item && ok.item.item_code, 'UK15', 'an available exact code resolves')
  const noIndex = quick.resolveQuery('C04', CATALOG, null)
  eq(noIndex.item, null, 'sold out without an index')
  eq(codes(noIndex.candidates), ['C04'], 'offered alone without an index')
})

test('resolveQuery: a clear fuzzy winner resolves only when available; candidates are partitioned', () => {
  const index = search.buildIndex(CATALOG)
  const win = quick.resolveQuery('braided', CATALOG, index)
  eq(win.item && win.item.item_code, 'C03', 'the clear available winner resolves')
  const dual = quick.resolveQuery('charger dual', CATALOG, index)
  eq(dual.item, null, 'a sold-out clear winner must not resolve')
  assert(dual.candidates.some((c) => c.item_code === 'UK16'), 'the sold-out winner is offered')
  assert(noAvailableAfterSoldOut(dual.candidates), `candidates: ${codes(dual.candidates)}`)
  const list = quick.parseList('24 x C01\n12 UK15\ncharger dual 6', CATALOG, index)
  eq(list.map((r) => (r.item ? r.item.item_code : null)), [null, 'UK15', null], 'a pasted list never resolves a sold-out line')
  eq(list[0].qty, 24, 'the quantity survives')
  assert(list[0].candidates.some((c) => c.item_code === 'C01'), 'the sold-out code is offered on its row')
})

let failed = 0
for (const [name, fn] of TESTS) {
  try {
    await fn()
    console.log('  PASS  ' + name)
  } catch (e) {
    failed += 1
    console.log('  FAIL  ' + name + '\n        ' + (e && e.message ? e.message : e))
  }
}
console.log(`\n${TESTS.length - failed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
