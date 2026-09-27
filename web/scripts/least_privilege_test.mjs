/**
 * Least privilege in the portal's routing (R7d release review, stream SECURITY) — plain node, no test
 * runner, no new deps.
 *
 *   node web/scripts/least_privilege_test.mjs        (from the repo root or from web/)
 *
 * Same loader as rep_ui_test.mjs (a module.registerHooks load hook, Node 22.15+, the project's own
 * `typescript`; nothing is compiled to disk). lib/nav.ts's './auth' import is served a stub (the real
 * one builds the Supabase client), lucide-react loads from node_modules. Exit 1 on any failure;
 * tests/test_r7d_least_privilege.py runs it and reports; the CI web job runs it after `npm ci`.
 *
 * What it proves: management opens a Command Centre drill page only when the login holds that
 * page's feature — no Profitability / Price tracker link without 'Margins', no Receivables without
 * 'Receivables', no Sales without 'Sales' — while its own feature-free pages always open.
 */
import { registerHooks } from 'node:module'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'
import ts from 'typescript'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')
const isFile = (p) => existsSync(p) && statSync(p).isFile()
const AUTH_STUB = 'data:text/javascript,' + encodeURIComponent(
  "export const isManagement = (me) => !!me && me.role === 'management'\n",
)

registerHooks({
  resolve(spec, ctx, next) {
    let s = spec
    if (s.startsWith('@/')) s = path.join(SRC, s.slice(2))
    else if (s.startsWith('./') || s.startsWith('../')) s = path.resolve(path.dirname(fileURLToPath(ctx.parentURL)), s)
    else return next(spec, ctx)
    if (s === path.join(SRC, 'lib', 'auth')) return { url: AUTH_STUB, shortCircuit: true }
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

const N = await import(pathToFileURL(path.join(SRC, 'lib/nav.ts')).href)

let passed = 0
let failed = 0
function check(name, fn) {
  try {
    fn()
    passed += 1
    console.log(`PASS  ${name}`)
  } catch (e) {
    failed += 1
    console.log(`FAIL  ${name}\n      ${e && e.message ? e.message : e}`)
  }
}
function eq(got, want, what) {
  if (got !== want) throw new Error(`${what}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`)
}

const ALL = ['Dashboard', 'Sales', 'Margins', 'Receivables', 'Inventory', 'Shop Orders']
const NO_MARGINS = ['Dashboard', 'Sales', 'Receivables', 'Inventory', 'Shop Orders']
const BARE = ['Dashboard', 'Shop Orders']

check('without Margins: no Profitability, no Price tracker (the tiles\' drill links are not offered)', () => {
  eq(N.managementMayOpen('/margins', NO_MARGINS), false, '/margins')
  eq(N.managementMayOpen('/prices', NO_MARGINS), false, '/prices')
  eq(N.managementMayOpen('/margins', ALL), true, '/margins with Margins')
  eq(N.managementMayOpen('/prices', ALL), true, '/prices with Margins')
})

check('Receivables, Sales, Inventory and Customer orders each need their own grant', () => {
  eq(N.managementMayOpen('/receivables', BARE), false, '/receivables')
  eq(N.managementMayOpen('/sales', BARE), false, '/sales')
  eq(N.managementMayOpen('/inventory', BARE), false, '/inventory')
  eq(N.managementMayOpen('/inventory', ALL), true, '/inventory with Inventory')
  eq(N.managementMayOpen('/shop-orders', BARE), true, '/shop-orders with Shop Orders')
  eq(N.managementMayOpen('/shop-orders', []), false, '/shop-orders with nothing')
  eq(N.managementMayOpen('/receivables', ALL), true, '/receivables with Receivables')
  eq(N.managementMayOpen('/sales', ALL), true, '/sales with Sales')
})

check('the role\'s own pages open with no grant; a page outside its list never does; no features = most closed', () => {
  for (const p of ['/command', '/command/team', '/command/customers', '/settings', '/']) {
    eq(N.managementMayOpen(p, []), true, p)
  }
  eq(N.managementMayOpen('/team', ALL), false, '/team')
  eq(N.managementMayOpen('/shop-rules', ALL), false, '/shop-rules')
  eq(N.managementMayOpen('/margins'), false, '/margins with no feature list')
  eq(N.managementMayOpen('/shop-orders?open=4'.split('?')[0], ['Shop Orders']), true, 'a drill with a query')
})

check('the menu and the route agree: every MANAGEMENT_NAV page the menu shows also opens', () => {
  const me = { role: 'management', features: NO_MARGINS }
  for (const item of N.navFor(me)) eq(N.managementMayOpen(item.to, me.features), true, item.to)
  eq(N.navFor(me).some((i) => i.to === '/margins'), false, 'no Profitability in the menu')
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
