/**
 * The office admin pages' honest states (polish stream, 28-Oct-2026) — the pure rules, on the real modules,
 * in plain node: no test runner, no new deps.
 *
 *   node web/scripts/admin_states_test.mjs        (from the repo root or from web/)
 *
 * The .ts sources are transpiled on load with the project's own `typescript` (a load hook from
 * module.registerHooks, the loader of order_heart_ui_test.mjs); nothing is compiled to disk. Exit 1 on any
 * failure. All inputs are synthetic.
 *
 * What it proves:
 *   * lib/dataState.ts — which face a panel shows: a page still loading never reads as "nothing there", a failed
 *     read with nothing on screen is an error (with Retry), a failed RE-read keeps the figures already shown, and
 *     "empty" only when the answer really is empty;
 *   * lib/dataRefresh.ts — the one list of queries a data load, commit, undo or remove makes stale: every report
 *     page, the header's freshness chip, the Command Centre, Stock moves, the price tracker, the Data page itself —
 *     and never the live marketplace orders;
 *   * lib/actionGate.ts — Start order, Approve and Advance run once however fast they are clicked: a second click
 *     while the first is saving is ignored, a failed save frees the button again;
 *   * the pages, read as text: each title is its menu label (lib/nav.ts), lucide icons instead of emoji and text
 *     arrows, the honest states wired in (no 60vh spinner block, no "Loading…" text, no card that vanishes while
 *     it loads), every data change refreshes through the one helper, a pipeline cancel asks first and Delete is
 *     reachable on a touch screen.
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

const load = (rel) => import(pathToFileURL(path.join(SRC, rel)).href)
// a Windows checkout has CRLF, CI has LF: the text checks read one form
const text = (rel) => readFileSync(path.join(SRC, rel), 'utf8').replace(/\r\n/g, '\n')

let passed = 0
let failed = 0
async function check(name, fn) {
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
function ok(v, what = '') {
  if (!v) throw new Error(`${what} expected truthy`)
}

// ── lib/dataState.ts ─────────────────────────────────────────────────────────────────────────────
const S = await load('lib/dataState.ts').catch((e) => ({ __missing: e.message }))

await check('a panel still loading never reads as empty; a failed read with nothing shown is an error', () => {
  ok(!S.__missing, `lib/dataState.ts loads (${S.__missing})`)
  eq(S.panelState({ data: undefined, isError: false }), 'loading', 'first read under way')
  eq(S.panelState({}), 'loading', 'no fields yet')
  eq(S.panelState({ data: undefined, isError: true }), 'error', 'the read failed')
})

await check('empty only when the answer really is empty; anything else is ready', () => {
  eq(S.panelState({ data: [] }), 'empty', '[]')
  eq(S.panelState({ data: null }), 'empty', 'null')
  eq(S.panelState({ data: [{ id: 1 }] }), 'ready', 'one row')
  eq(S.panelState({ data: { rows: [] } }), 'ready', 'an object is ready unless the page says what empty is')
  eq(S.panelState({ data: { rows: [] } }, (d) => d.rows.length === 0), 'empty', "the page's own rule")
  eq(S.panelState({ data: { rows: [1] } }, (d) => d.rows.length === 0), 'ready')
})

await check('a failed RE-read keeps the figures already on screen (and says the refresh failed)', () => {
  eq(S.panelState({ data: [{ id: 1 }], isError: true }), 'ready', 'stale figures stay')
  eq(S.panelState({ data: [], isError: true }), 'empty')
  eq([S.refreshFailed({ data: [1], isError: true }), S.refreshFailed({ data: undefined, isError: true }),
    S.refreshFailed({ data: [1], isError: false })], [true, false, false])
})

// ── lib/dataRefresh.ts ───────────────────────────────────────────────────────────────────────────
const R = await load('lib/dataRefresh.ts').catch((e) => ({ __missing: e.message }))

await check('one list: every report page, the freshness chip, the Command Centre, Stock moves, prices, the Data page', () => {
  ok(!R.__missing, `lib/dataRefresh.ts loads (${R.__missing})`)
  const covered = [
    ['report', 'dashboard'], ['report', 'sales'], ['report', 'inventory'], ['report', 'margins'], ['report', 'receivables'],
    ['freshness'], ['management', 'overview', 'mtd'], ['command', 'team-message'],
    ['stock-daily', '', ''], ['stock-flow', 'out', 30], ['stock-transfers', 30], ['stock-recon'],
    ['price-tracker', '', '', '', false], ['price-history', 'SKU-1'], ['catalog'],
    ['shop-attainment'], ['shop-statements'], ['shop-margins'], ['shop-unpriced-stock'],
    ['data', 'coverage'], ['ingest', 'batches'],
  ]
  for (const k of covered) ok(R.coversKey(k), `covers ${JSON.stringify(k)}`)
})

await check('the live marketplace is never re-read by a Focus load', () => {
  for (const k of [['shop-orders', '', ''], ['shop-order', 4], ['shop-me'], ['settings-shop'], ['purchase-orders'], ['team']]) {
    ok(!R.coversKey(k), `leaves ${JSON.stringify(k)} alone`)
  }
})

await check('refreshAfterDataChange invalidates each key of the list once, by prefix', async () => {
  const seen = []
  await R.refreshAfterDataChange({ invalidateQueries: (f) => { seen.push(f.queryKey); return Promise.resolve() } })
  eq(seen, R.DATA_QUERY_KEYS.map((k) => [...k]), 'one call per key, in order')
  eq(new Set(seen.map((k) => JSON.stringify(k))).size, seen.length, 'no key twice')
})

// ── lib/actionGate.ts ────────────────────────────────────────────────────────────────────────────
const G = await load('lib/actionGate.ts').catch((e) => ({ __missing: e.message }))

await check('the gate: a key runs once at a time; another key is independent', () => {
  ok(!G.__missing, `lib/actionGate.ts loads (${G.__missing})`)
  const g = new G.ActionGate()
  eq([g.tryStart('start:VFAN'), g.tryStart('start:VFAN'), g.tryStart('advance:7')], [true, false, true])
  eq([g.isRunning('start:VFAN'), g.isRunning('approve')], [true, false])
  g.finish('start:VFAN')
  eq([g.isRunning('start:VFAN'), g.tryStart('start:VFAN')], [false, true])
  eq([...g.snapshot()].sort(), ['advance:7', 'start:VFAN'])
})

await check('a double click saves once; the button is free again after the save, or after a failure', async () => {
  const g = new G.ActionGate()
  let calls = 0
  let release
  const slow = () => { calls += 1; return new Promise((r) => { release = r }) }
  const changes = []
  const first = G.runGuarded(g, 'approve', slow, () => changes.push(g.isRunning('approve')))
  const second = G.runGuarded(g, 'approve', slow, () => changes.push('second'))
  eq(await second, undefined, 'the second click is ignored')
  eq(calls, 1, 'one POST')
  release('done')
  eq(await first, 'done')
  eq(changes, [true, false], 'busy while saving, free after')
  await G.runGuarded(g, 'approve', async () => { throw new Error('500') }).catch(() => {})
  eq(g.isRunning('approve'), false, 'a failed save frees the key')
  eq(await G.runGuarded(g, 'approve', async () => 'again'), 'again')
})

// ── the pages, as text ───────────────────────────────────────────────────────────────────────────
await check('Dashboard: an error with Retry; every card holds its place while the figures load', () => {
  const d = text('pages/Dashboard.tsx')
  ok(d.includes("import { LoadError } from '@/components/LoadError'"), 'LoadError')
  ok(d.includes('const state = panelState({ data, isError })'), 'the shared state rule')
  ok(d.includes("state === 'error' ? (") && d.includes("state === 'loading' ? ("), 'an error face and a loading face')
  for (const s of ['ActionsSkeleton', 'DailySkeleton', 'HealthSkeleton', 'MomentumSkeleton']) ok(d.includes(`function ${s}(`), s)
  ok(!d.includes("subtitle={isLoading ? 'Mobile Accessories Intelligence' : focus}"), 'no "All clear" under a failed read')
})

await check('Purchase orders: a skeleton, an error with Retry, "everything received" only once the list is read', () => {
  const o = text('pages/Orders.tsx')
  ok(o.includes('<PageHeader title="Purchase orders"'), 'the menu label')
  ok(o.includes("import { LoadError } from '@/components/LoadError'"), 'LoadError')
  ok(o.includes('const poState = panelState({ data, isError: poQuery.isError })'), 'the shared state rule')
  const at = o.lastIndexOf('Everything ordered has been received.')   // the rendered line, not a comment naming it
  ok(at > 0, 'the message is still there')
  const guard = o.indexOf("{(poState === 'ready' || poState === 'empty') && (() => {")
  ok(guard > 0 && guard < at && o.indexOf('})()}', guard) > at, 'shown only inside the block drawn once the list is read')
  ok(o.includes("poState === 'loading'") && o.includes("poState === 'error'"), 'a loading face and an error face')
  ok(/boardQuery\.isError/.test(o) && /spQuery\.isError/.test(o), 'the pipeline and the supplier prices say when they did not load')
})

await check('Purchase orders: Start order, Approve and Advance save once; a pipeline cancel asks first; Delete on touch', () => {
  const o = text('pages/Orders.tsx')
  ok(o.includes("import { useActionGate } from '@/lib/actionGate'"), 'the gate')
  for (const key of ['`start:', "'approve'", '`advance:']) ok(o.includes(`run(${key}`), `run(${key}…)`)
  ok((o.match(/disabled=\{pending\(/g) || []).length >= 4, 'start, approve, advance and cancel disable while saving')
  ok(o.includes('confirmCancel'), 'cancel asks first')
  ok(!/className="[^"]*\bhidden\b[^"]*group-hover:grid/.test(o), 'Delete is not hover-only')
  ok(o.includes('[@media(hover:none)]:opacity-100'), 'Delete shows on a touch screen')
})

await check('Purchase orders: wide tables scroll sideways in their card; icons, not glyphs; an MRN refreshes every report', () => {
  const o = text('pages/Orders.tsx')
  ok((o.match(/<ScrollX[\s>]/g) || []).length >= 3, 'the proposal, the verify and the cost-change tables')
  ok(!/[▲▼✓]/.test(o) && !o.includes('Approve → pipeline'), 'no text arrows or ticks')
  ok(o.includes("import { refreshAfterDataChange } from '@/lib/dataRefresh'") && o.includes('if (mrns) void refreshAfterDataChange(qc)'),
    'a goods receipt moves the landed costs: every report and the freshness chip are read again')
})

await check('Customer orders: a failed read shows the error with Retry, never "No orders yet"; the drawer and the sheet too', () => {
  const s = text('pages/ShopOrders.tsx')
  ok(s.includes("import { LoadError } from '@/components/LoadError'"), 'LoadError')
  ok((s.match(/<LoadError /g) || []).length >= 3, 'the desk list, the order drawer and the field sheet')
  ok(s.includes('const listState = panelState({ data, isError })'), 'the desk list reads the shared state rule')
  const drawer = s.split('function OrderDrawer(')[1].split('\nfunction ')[0]
  ok(drawer.includes('flow.isError'), "the drawer's skeleton ends when its read fails")
})

await check('Stock moves: the daily chart and the transfers say when they did not load; the menu label; icons for direction', () => {
  const sm = text('pages/StockMovement.tsx')
  ok(sm.includes('<PageHeader title="Stock moves"'), 'the menu label')
  ok(!sm.includes('if (!isLoading && (!data?.months?.length)) return null'), 'the daily chart does not vanish on a failure')
  ok(sm.includes("dailyState === 'error'") && sm.includes('fallback="Could not load the daily movement."'), 'the daily chart')
  ok(sm.includes("transfersState === 'error'") && sm.includes('fallback="Could not load the transfers."'), 'the transfers table')
  ok(!sm.includes("'From main →'") && !sm.includes("'→ Into main'") && !/\? '→' : '←'/.test(sm), 'direction controls draw icons')
})

await check('Data upload: a load, commit, undo or remove refreshes every report and the freshness chip; skeletons, not "Loading…"', () => {
  const dp = text('pages/DataPage.tsx')
  ok(dp.includes('<PageHeader title="Data upload"'), 'the menu label')
  ok(dp.includes("import { refreshAfterDataChange } from '@/lib/dataRefresh'"), 'the one helper')
  ok((dp.match(/refreshAfterDataChange\(qc\)/g) || []).length >= 4, 'upload, commit, undo and remove')
  ok(!/invalidateQueries\(\{ queryKey: \['data', 'coverage'\] \}\)/.test(dp), 'no coverage-only refresh left')
  ok(!dp.includes('Loading…'), 'skeleton rows instead of "Loading…"')
  ok(dp.includes("coverageState === 'error'") && dp.includes("batchState === 'error'"), 'the sources and the history say when they did not load')
})

await check('Inventory and Sales: a page-shaped skeleton, never a 60vh block', () => {
  for (const [f, name] of [['pages/Inventory.tsx', 'InventorySkeleton'], ['pages/Sales.tsx', 'SalesSkeleton']]) {
    const src = text(f)
    ok(!src.includes('h-[60vh]'), `${f}: no 60vh block`)
    ok(src.includes(`function ${name}(`) && src.includes(`<${name} />`), `${f}: ${name}`)
  }
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
