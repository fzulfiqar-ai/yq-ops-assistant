/**
 * The office tables and lists (polish stream, 28-Oct-2026) — the pure rules, on the real modules, in plain node:
 * no test runner, no new deps.
 *
 *   node web/scripts/admin_tables_test.mjs        (from the repo root or from web/)
 *
 * The .ts sources are transpiled on load with the project's own `typescript` (a load hook from
 * module.registerHooks, the loader of order_heart_ui_test.mjs); nothing is compiled to disk. Exit 1 on any
 * failure. All inputs are synthetic.
 *
 * What it proves:
 *   * lib/tableWindow.ts — DataTable mounts its rows a page at a time (150 table rows, 60 phone cards) and starts
 *     again from the first page ONLY when its search, its sort or the caller's filter changes: re-reading the
 *     rows (opening or closing an order re-renders the page) keeps the window and the scroll where they were; a
 *     wide table shows a soft fade on the side that has more to scroll to;
 *   * lib/orderList.ts — Customer orders read 100 at a time with Load more: the next offset, no order twice when
 *     a new one arrives between two pages, the "Latest 100 of 342 orders" footer, and the rep's stage tab kept
 *     per browser tab (a link's ?bucket= wins, a blocked storage never breaks the page);
 *   * lib/searchRoute.ts — the command palette opens a salesman on Sales filtered to him, and Sales finds the
 *     rep the link names whatever its case;
 *   * the components and pages, read as text: DataTable's solid sticky header, its IntersectionObserver
 *     sentinel on the viewport (no onScroll handler, no scroll container of its own on a touch screen), and
 *     Customer orders' memoised rows, Load more and remembered stage.
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

const load = (rel) => import(pathToFileURL(path.join(SRC, rel)).href).catch((e) => ({ __missing: e.message }))
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

// ── lib/tableWindow.ts ───────────────────────────────────────────────────────────────────────────
const W = await load('lib/tableWindow.ts')

await check('a page is 150 table rows or 60 phone cards', () => {
  ok(!W.__missing, `lib/tableWindow.ts loads (${W.__missing})`)
  eq([W.TABLE_PAGE, W.CARD_PAGE], [150, 60])
})

await check('the window starts again only when the search, the sort or the filter changes — never on a re-read', () => {
  const k = W.windowKey('', null, 'all|')
  eq(W.windowKey('', null, 'all|'), k, 'the same filter is the same window (the rows array is not part of it)')
  ok(W.windowKey('cab', null, 'all|') !== k, 'a search')
  ok(W.windowKey('', { key: 'total', dir: 'desc' }, 'all|') !== k, 'a sort')
  ok(W.windowKey('', { key: 'total', dir: 'asc' }, 'all|') !== W.windowKey('', { key: 'total', dir: 'desc' }, 'all|'), 'its direction')
  ok(W.windowKey('', null, 'new|') !== k, "the caller's filter")
  eq(W.windowKey('', null), W.windowKey('', null, undefined), 'no filter given')
  eq(W.limitFor({ key: k, limit: 450 }, k, 150), 450, 'a re-read keeps the rows already mounted')
  eq(W.limitFor({ key: k, limit: 450 }, W.windowKey('x', null, 'all|'), 150), 150, 'a new search starts at one page')
})

await check('growing: one page at a time, never past the end', () => {
  eq(W.grownLimit(150, 1000, 150), 300)
  eq(W.grownLimit(900, 1000, 150), 1000)
  eq(W.grownLimit(1000, 1000, 150), 1000)
  eq(W.grownLimit(150, 40, 150), 150, 'nothing left to mount')
  eq([W.hasMore(150, 151), W.hasMore(150, 150), W.hasMore(150, 12)], [true, false, false])
})

await check('the sideways hint: a fade on the side that has more to scroll to, none when the table fits', () => {
  eq(W.edgeFade({ scrollLeft: 0, clientWidth: 300, scrollWidth: 300 }), { left: false, right: false }, 'fits')
  eq(W.edgeFade({ scrollLeft: 0, clientWidth: 300, scrollWidth: 640 }), { left: false, right: true }, 'at the start')
  eq(W.edgeFade({ scrollLeft: 120, clientWidth: 300, scrollWidth: 640 }), { left: true, right: true }, 'in the middle')
  eq(W.edgeFade({ scrollLeft: 340, clientWidth: 300, scrollWidth: 640 }), { left: true, right: false }, 'at the end')
  eq(W.edgeFade({ scrollLeft: 339.4, clientWidth: 300, scrollWidth: 640 }), { left: true, right: false }, "a zoomed screen's sub-pixel end")
  eq(W.edgeFade({ scrollLeft: 0.6, clientWidth: 300, scrollWidth: 640 }), { left: false, right: true }, 'a sub-pixel start')
  eq(W.edgeFade({ scrollLeft: 0, clientWidth: 300, scrollWidth: 301 }), { left: false, right: false }, 'one pixel over is a fit')
})

await check('DataTable: a solid sticky header, a viewport sentinel instead of onScroll, a reset only on search/sort/filter', () => {
  const dt = text('components/DataTable.tsx')
  ok(!dt.includes('backdrop-blur') && !dt.includes('bg-secondary/90'), 'no blur over a see-through header')
  ok(dt.includes('<thead className="sticky top-0 z-10 bg-secondary">'), 'a solid sticky header')
  ok(!dt.includes('onScroll={'), 'no scroll handler mounting 150 rows per event')
  ok(dt.includes('new IntersectionObserver(') && dt.includes('{ root: null }'), 'an IntersectionObserver on the viewport')
  ok(!dt.includes('[query, sort, rows]'), 'no reset on every new rows array')
  ok(dt.includes('windowKey(query, sort,') && dt.includes('resetKey?: string | number'), "the window's key: search, sort, the caller's filter")
  ok(dt.includes('style={mouse ? ({ maxHeight } as CSSProperties) : undefined}'), 'its own vertical scroller only with a mouse')
  ok(dt.includes('const wide = useMediaQuery(WIDE_QUERY, true)') && dt.includes(') : !wide ? ('), 'one layout mounted, never both')
  ok(dt.includes('pinLast') && dt.includes('sticky right-0'), 'the pinned last column stays (R7d)')
  ok(dt.includes('export function ScrollX(') && dt.includes('<EdgeFades'), 'ScrollX and the sideways hint')
})

// ── lib/orderList.ts ─────────────────────────────────────────────────────────────────────────────
const L = await load('lib/orderList.ts')
const page = (n, count, from = 0) => ({ orders: Array.from({ length: n }, (_, i) => ({ id: 10_000 - from - i })), count })

await check('Load more: the next offset is the rows read so far; none once all are read or a page came back short', () => {
  ok(!L.__missing, `lib/orderList.ts loads (${L.__missing})`)
  eq(L.ORDER_PAGE, 100)
  eq(L.nextOrderOffset([page(100, 342)]), 100)
  eq(L.nextOrderOffset([page(100, 342), page(100, 342, 100)]), 200)
  eq(L.nextOrderOffset([page(100, 342), page(100, 342, 100), page(100, 342, 200), page(42, 342, 300)]), undefined, 'all 342 read')
  eq(L.nextOrderOffset([page(100, 100)]), undefined, 'exactly one page')
  eq(L.nextOrderOffset([page(40, 342)]), undefined, 'a short page ends the list, whatever the count says')
  eq(L.nextOrderOffset([page(0, 0)]), undefined, 'no orders')
  eq(L.nextOrderOffset([{ orders: page(100, 0).orders }]), 100, 'an answer without a count: a full page may have more')
  eq(L.nextOrderOffset([]), undefined)
})

await check('an order that arrives between two pages never shows twice', () => {
  const p1 = { orders: [{ id: 9 }, { id: 8 }, { id: 7 }], count: 5 }
  const p2 = { orders: [{ id: 7 }, { id: 6 }], count: 5 }     // id 7 slid down a place when a new order came in
  eq(L.flattenOrders([p1, p2]).map((o) => o.id), [9, 8, 7, 6])
  eq(L.flattenOrders([]), [])
})

await check('the footer: "Latest 100 of 342 orders" and what the next tap loads; nothing once all are shown', () => {
  eq(L.listFooter(100, 342), { text: 'Latest 100 of 342 orders', more: 100 })
  eq(L.listFooter(300, 342), { text: 'Latest 300 of 342 orders', more: 42 })
  eq(L.listFooter(342, 342), null)
  eq(L.listFooter(100, 100), null)
  eq(L.listFooter(100, 1342), { text: 'Latest 100 of 1,342 orders', more: 100 }, 'thousands grouped')
  eq(L.listFooter(100, 130, 100, 'open orders'), { text: 'Latest 100 of 130 open orders', more: 30 })
})

await check("the rep's stage: a link's ?bucket wins, else this tab's last stage, else Received", () => {
  eq(L.initialStage('delivered', 'confirmed'), 'delivered', 'a deep link wins')
  eq(L.initialStage('progress', null), 'confirmed', 'an old link')
  eq(L.initialStage(null, 'cancelled'), 'cancelled', "the tab's last stage")
  eq(L.initialStage('', 'delivered'), 'delivered', 'an empty param is no param')
  eq(L.initialStage(null, '<script>'), 'new', 'junk in the storage')
  eq(L.initialStage(null, null), 'new')
})

await check("the stage lives in this tab's sessionStorage; a blocked storage never breaks the page", () => {
  const mem = new Map()
  const store = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => { mem.set(k, v) } }
  L.saveStage(store, 'confirmed')
  eq([L.readStage(store), [...mem.keys()]], ['confirmed', [L.STAGE_KEY]])
  const blocked = { getItem: () => { throw new Error('SecurityError') }, setItem: () => { throw new Error('QuotaExceededError') } }
  eq(L.readStage(blocked), null)
  L.saveStage(blocked, 'new')
  eq([L.readStage(null), L.readStage(undefined)], [null, null])
  L.saveStage(undefined, 'new')
})

// ── lib/searchRoute.ts ───────────────────────────────────────────────────────────────────────────
const Q = await load('lib/searchRoute.ts')

await check('the palette opens a salesman on Sales filtered to him; customers and items as before', () => {
  ok(!Q.__missing, `lib/searchRoute.ts loads (${Q.__missing})`)
  eq(Q.hitRoute({ type: 'salesman', label: 'Rep A' }), '/sales?rep=Rep%20A')
  eq(Q.hitRoute({ type: 'customer', label: 'Shop & Co' }), '/receivables?q=Shop%20%26%20Co')
  eq(Q.hitRoute({ type: 'item', label: 'TST-C18 USB-C' }), '/inventory?q=TST-C18%20USB-C')
})

await check('Sales picks the rep the link names, whatever its case or spaces; an unknown name picks nobody', () => {
  const names = ['Rep A', 'Rep B', 'Outlet C']
  eq([Q.matchSalesman(names, 'rep b'), Q.matchSalesman(names, '  Rep A ')], ['Rep B', 'Rep A'])
  eq([Q.matchSalesman(names, 'Rep Z'), Q.matchSalesman(names, null), Q.matchSalesman(names, ''), Q.matchSalesman([], 'Rep A')],
    [null, null, null, null])
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
