/**
 * The sold-out rule, exercised on the real listing helpers — plain node, no test runner, no new deps.
 *
 *   node web/scripts/soldout_order_test.mjs        (from the repo root or from web/)
 *
 * The TypeScript sources are transpiled on load with the project's own `typescript` (a load hook
 * from module.registerHooks, Node 22.15+), and a resolve hook maps the extensionless relative
 * imports and the `@/` alias the Vite build resolves onto src/*.ts. Nothing is compiled to disk.
 * Exit 1 on any failure. tests/test_r1_soldout.py runs this script and reports its result; the CI
 * web job runs it after `npm ci`.
 *
 * What it proves: every listing helper keeps sold out last (partition, every sort, every shelf, the
 * home grid, search, the Codes group) and — the R1 blocker — Quick order's Enter NEVER substitutes:
 * a typed code that is a sold-out SKU locks nothing and has no Enter target, whatever in-stock
 * neighbour the fuzzy index finds one edit away (the live payload had 45 such substitutions).
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

const mod = (rel) => import(pathToFileURL(path.join(SRC, rel)).href)
const facets = await mod('market/lib/facets.ts')
const search = await mod('market/lib/search.ts')
const groups = await mod('market/lib/searchGroups.ts')
const quick = await mod('market/lib/quickParse.ts')
const { S } = await mod('market/strings.ts')
const home = await mod('market/lib/home.ts')

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
  // the review's substitutions in miniature: a sold-out code with an in-stock neighbour one edit away
  item('UK05', 'out_of_stock', { name: 'QC3.0 charger', price: 1.8, category: 'CHARGER' }),
  item('C10', 'out_of_stock', { name: 'Car charger dual USB', price: 0.7, category: 'CAR CHARGER' }),
  item('T10', 'in_stock', { name: 'Wireless headset', price: 2.5, category: 'BLUETOOTH HEADSET' }),
]
const codes = (arr) => arr.map((i) => i.item_code)
const out = (i) => i.stock_status === 'out_of_stock'
const firstOut = (arr) => arr.findIndex(out)
const noAvailableAfterSoldOut = (arr) => {
  const k = firstOut(arr)
  return k < 0 || arr.slice(k).every(out)
}
const HAVE = ['C02', 'C03', 'C05', 'UK15', 'T10']
const SOLD = ['C01', 'C04', 'UK16', 'UK05', 'C10']

/* ── the runner ── */
const TESTS = []
const test = (name, fn) => TESTS.push([name, fn])
const assert = (cond, msg) => {
  if (!cond) throw new Error(msg)
}
const eq = (a, b, msg) => assert(JSON.stringify(a) === JSON.stringify(b), `${msg}: got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`)

test('strings: zero stock reads "Sold out", never "Out of stock"; the Arabic sits beside it; the dated form', () => {
  eq(S.card.stockOut, 'Sold out', 'S.card.stockOut')
  eq(S.card.soldOut, 'Sold out', 'S.card.soldOut')
  eq(S.card.stockOutAr, 'نفدت الكمية', 'S.card.stockOutAr')
  assert(!/out of stock/i.test(S.card.backorderNote), 'backorderNote still says "Out of stock"')
  eq(S.card.soldOutAsOf('21 Sep'), 'Sold out · stock as of 21 Sep', 'the stale-snapshot label')
  eq(S.card.leftOut(1), '1 sold-out line left out', 'leftOut singular')
  eq(S.card.leftOut(3), '3 sold-out lines left out', 'leftOut plural')
})

test('partitionByAvailability: stable — each group keeps its incoming order', () => {
  eq(codes(facets.partitionByAvailability(CATALOG)), [...HAVE, ...SOLD], 'partition')
  eq(codes(facets.partitionByAvailability([])), [], 'empty')
})

test('sortItems: every sort applies within the availability groups', () => {
  const shelf = facets.sortItems(CATALOG, 'shelf')
  eq(codes(shelf), [...HAVE, ...SOLD], 'shelf')
  const asc = facets.sortItems(CATALOG, 'price_asc')
  assert(noAvailableAfterSoldOut(asc), 'price_asc: an available line after a sold-out one')
  eq(codes(asc), ['C02', 'C03', 'C05', 'UK15', 'T10', 'C01', 'C10', 'C04', 'UK05', 'UK16'], 'price_asc within groups')
  const desc = facets.sortItems(CATALOG, 'price_desc')
  assert(noAvailableAfterSoldOut(desc), 'price_desc: an available line after a sold-out one')
  eq(codes(desc), ['T10', 'C05', 'UK15', 'C03', 'C02', 'UK16', 'UK05', 'C04', 'C10', 'C01'], 'price_desc within groups')
  const pop = facets.sortItems(CATALOG, 'popular')
  assert(noAvailableAfterSoldOut(pop), 'popular: an available line after a sold-out one')
  // C01 is a best seller but sold out: it must not lead the popular sort
  eq(pop[0].item_code, 'C05', 'popular leads with the available best seller')
  eq(pop[firstOut(pop)].item_code, 'C01', 'the sold-out best seller leads only the sold-out group')
})

test('shelfOrder: the plain shelf and every destination shelf keep sold out last', () => {
  eq(codes(facets.shelfOrder(CATALOG, new Set())), [...HAVE, ...SOLD], 'plain shelf')
  for (const f of ['best', 'moving', 'new', 'deals', 'clearance', 'drops', 'offers']) {
    assert(noAvailableAfterSoldOut(facets.shelfOrder(CATALOG, new Set([f]))), `destination ${f}`)
  }
})

test('homeGridOrder: unseen available → available on a rail → sold out in shelf order', () => {
  // C01 is sold out AND in the rail set (a last-order code): it keeps its shelf place in the sold-out band
  const rails = new Set(['C02', 'C01'])
  eq(codes(facets.homeGridOrder(CATALOG, rails)), ['C03', 'C05', 'UK15', 'T10', 'C02', ...SOLD], 'home grid')
  eq(codes(facets.homeGridOrder(CATALOG, new Set())), [...HAVE, ...SOLD], 'no rails')
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
  eq(codes(uk.codes), ['UK15', 'UK16', 'UK05'], 'codes: available first, sold out kept')
  eq(uk.exact, null, 'no exact code')
})

test('resolveQuery: an exact sold-out code is never resolved — it is offered after in-stock siblings, and named as `exact`', () => {
  const index = search.buildIndex(CATALOG)
  const r = quick.resolveQuery('c01', CATALOG, index)
  eq(r.item, null, 'sold-out exact code resolved')
  eq(r.exact && r.exact.item_code, 'C01', 'the exact match is named')
  assert(r.candidates.some((c) => c.item_code === 'C01'), 'the sold-out line is offered')
  assert(noAvailableAfterSoldOut(r.candidates), `candidates: ${codes(r.candidates)}`)
  assert(r.candidates[0].item_code !== 'C01', `in-stock alternatives first: ${codes(r.candidates)}`)
  const ok = quick.resolveQuery('uk-15', CATALOG, index)
  eq(ok.item && ok.item.item_code, 'UK15', 'an available exact code resolves')
  eq(ok.exact && ok.exact.item_code, 'UK15', 'and is the exact match')
  const noIndex = quick.resolveQuery('C04', CATALOG, null)
  eq(noIndex.item, null, 'sold out without an index')
  eq(codes(noIndex.candidates), ['C04'], 'offered alone without an index')
  eq(quick.resolveQuery('braided', CATALOG, index).exact, null, 'a name is not an exact code')
})

test('resolveQuery: a clear fuzzy winner resolves only when available; candidates are partitioned', () => {
  const index = search.buildIndex(CATALOG)
  const win = quick.resolveQuery('braided', CATALOG, index)
  eq(win.item && win.item.item_code, 'C03', 'the clear available winner resolves')
  const dual = quick.resolveQuery('20w charger dual', CATALOG, index)
  eq(dual.item, null, 'a sold-out clear winner must not resolve')
  assert(dual.candidates.some((c) => c.item_code === 'UK16'), 'the sold-out winner is offered')
  assert(noAvailableAfterSoldOut(dual.candidates), `candidates: ${codes(dual.candidates)}`)
  const list = quick.parseList('24 x C01\n12 UK15\n20w charger dual 6', CATALOG, index)
  eq(list.map((r) => (r.item ? r.item.item_code : null)), [null, 'UK15', null], 'a pasted list never resolves a sold-out line')
  eq(list[0].qty, 24, 'the quantity survives')
  assert(list[0].candidates.some((c) => c.item_code === 'C01'), 'the sold-out code is offered on its row')
  eq(list[0].exact && list[0].exact.item_code, 'C01', 'the pasted row knows its code is a sold-out SKU')
})

test('resolveQuick: a typed SOLD-OUT code adds NOTHING — no lock, no Enter target — whatever in-stock neighbour is one edit away', () => {
  const index = search.buildIndex(CATALOG)
  for (const it of CATALOG.filter(out)) {
    for (const q of [it.item_code, it.item_code.toLowerCase(), ` ${it.item_code} `, it.item_code.replace(/(\D)(\d)/, '$1-$2')]) {
      const r = quick.resolveQuick(q, CATALOG, index)
      eq(r.lock, null, `${JSON.stringify(q)}: locked a line`)
      eq(r.best, null, `${JSON.stringify(q)}: Enter would lock ${r.best && r.best.item_code}`)
      eq(r.exactOut && r.exactOut.item_code, it.item_code, `${JSON.stringify(q)}: the sold-out SKU itself is what is shown`)
      assert(!r.suggestions.some((s) => s.item_code === it.item_code), `${JSON.stringify(q)}: the sold-out SKU is also in the suggestions`)
      assert(noAvailableAfterSoldOut(r.suggestions), `${JSON.stringify(q)}: suggestions ${codes(r.suggestions)}`)
    }
  }
  // the review's cases in miniature: UK05 → UK15, C10 → T10 are one edit apart, so the index offers the
  // neighbour — as a suggestion the merchant picks by hand, never as what Enter takes
  const uk05 = quick.resolveQuick('UK05', CATALOG, index)
  assert(uk05.suggestions.some((s) => s.item_code === 'UK15'), `UK15 is offered as an alternative: ${codes(uk05.suggestions)}`)
  eq(uk05.best, null, 'Enter must not take UK15')
  const c10 = quick.resolveQuick('C10', CATALOG, index)
  assert(c10.suggestions.some((s) => s.item_code === 'T10'), `T10 is offered as an alternative: ${codes(c10.suggestions)}`)
  eq(c10.best, null, 'Enter must not put a headset on a car-charger row')
  // without an index the sold-out SKU is still shown, with nothing to substitute
  const bare = quick.resolveQuick('C04', CATALOG, null)
  eq(bare.exactOut && bare.exactOut.item_code, 'C04', 'no index: still the sold-out SKU')
  eq(bare.best, null, 'no index: no Enter target')
  eq(codes(bare.suggestions), [], 'no index: nothing else offered')
})

test('resolveQuick: an available code locks at once; a name gives Enter its best match only when that match is available', () => {
  const index = search.buildIndex(CATALOG)
  for (const it of CATALOG.filter((i) => !out(i))) {
    const r = quick.resolveQuick(it.item_code.toLowerCase(), CATALOG, index)
    eq(r.lock && r.lock.item_code, it.item_code, `${it.item_code}: lock`)
    eq(r.exactOut, null, `${it.item_code}: not sold out`)
  }
  const braided = quick.resolveQuick('braided', CATALOG, index)
  eq(braided.lock, null, 'a name never locks by itself')
  eq(braided.best && braided.best.item_code, 'C03', 'Enter takes the available best match')
  eq(braided.exactOut, null, 'a name is not a sold-out code')
  assert(noAvailableAfterSoldOut(braided.suggestions), `suggestions ${codes(braided.suggestions)}`)
  const dual = quick.resolveQuick('20w charger dual', CATALOG, index)
  eq(dual.best, null, `the best match is sold out: Enter takes nothing (got ${dual.best && dual.best.item_code})`)
  assert(dual.suggestions.some((s) => s.item_code === 'UK16'), 'the sold-out match is still offered, greyed')
  const none = quick.resolveQuick('zzzzzz', CATALOG, index)
  eq(none.best, null, 'no hit, no target')
  eq(codes(none.suggestions), [], 'no hit, nothing offered')
})

test('soldOutSplit: the divider sits before the first sold-out line on the page, and counts the whole listing', () => {
  const list = facets.partitionByAvailability(CATALOG)
  eq(facets.soldOutSplit(list, list), { firstOut: HAVE.length, soldTotal: SOLD.length }, 'whole listing on screen')
  eq(facets.soldOutSplit(list, list.slice(0, 3)), { firstOut: -1, soldTotal: SOLD.length }, 'a first page that has not reached the sold-out lines: no divider yet')
  eq(facets.soldOutSplit(list, list.slice(0, HAVE.length + 1)), { firstOut: HAVE.length, soldTotal: SOLD.length }, 'the page that reaches them')
  const have = list.filter((i) => !out(i))
  eq(facets.soldOutSplit(have, have), { firstOut: -1, soldTotal: 0 }, 'nothing sold out: no divider')
  eq(S.shop.notInStock(1), 'Not in stock now · 1 line', 'divider singular')
  eq(S.shop.notInStock(54), 'Not in stock now · 54 lines', 'divider plural')
})

test('splitReorder: Order again adds what can be ordered today — backorder respected — and keeps the sold-out rest to show, in order', () => {
  const lines = CATALOG.map((it, i) => ({ item: it, qty: i + 1 }))
  const off = home.splitReorder(lines, false)
  eq(off.add.map((l) => l.item.item_code), HAVE, 'backorder off: in stock (a few left included) only')
  eq(off.sold.map((l) => l.item.item_code), SOLD, 'backorder off: every sold-out line kept, never dropped')
  eq(off.add.map((l) => l.qty), [2, 3, 5, 6, 10], 'quantities travel with their lines')
  const on = home.splitReorder(lines, true)
  eq(on.add.map((l) => l.item.item_code), codes(CATALOG), 'backorder on: every line can be ordered, in order')
  eq(on.sold, [], 'backorder on: nothing left out')
})

test('"Deals" only for a live offer or a real price-book drop; clearance alone is "Last chance"', () => {
  const clearing = [item('L1', 'in_stock', { badges: ['clearance'] }), item('L2', 'in_stock', { badges: ['clearance'] })]
  eq(home.dealSets(clearing, []).hasRealDeals, false, 'clearance only')
  eq(home.dealSets([...clearing, { ...item('D1', 'in_stock', { price: 1.5 }), was_bhd: 2 }], []).hasRealDeals, true, 'a real was_bhd drop')
  eq(home.dealSets([...clearing, { ...item('D2', 'in_stock', { price: 1.5 }), was_bhd: 1.5 }], []).hasRealDeals, false, 'was_bhd not above the price is no drop')
  eq(home.dealSets([...clearing, item('O1', 'in_stock', { badges: ['on_offer'] })], []).hasRealDeals, true, 'a live offer line')
  eq(home.dealSets([...clearing, { ...item('D3', 'out_of_stock', { price: 1.5 }), was_bhd: 2 }], []).hasRealDeals, false, 'a sold-out drop is no deal on the shelf')
  eq(S.shop.clearance, 'Last chance', 'the clearance wording the entry falls back to')
  eq(S.nav.deals, 'Deals', 'the deals wording')
})

test('strings: the VAT note and the wholesale minimum promise (the amount comes from the payload, formatted)', () => {
  eq(S.vat.note, 'Prices include 10% VAT', 'VAT note')
  eq(S.vat.noteAr, 'الأسعار شاملة ضريبة القيمة المضافة 10%', 'VAT note, Arabic')
  eq(S.promise.minimum('BHD 20.000'), 'Wholesale orders from BHD 20.000', 'minimum promise')
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
