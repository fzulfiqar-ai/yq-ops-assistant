/**
 * The portal's shells (ship 2, plan items 12, 13, 23 and 24) — the rules on the real modules, and the shells'
 * structure read from their sources, in plain node: no browser, no test runner, no new deps.
 *
 *   node web/scripts/portal_shell_test.mjs        (from the repo root or from web/)
 *
 * The .ts modules are transpiled on load with the project's own `typescript` (the module.registerHooks
 * loader of rep_field_test.mjs); lib/api.ts is replaced by a stub (it would build a Supabase client), and no
 * page module is ever loaded (lib/pageCode only names them). Exit 1 on any failure. The browser half — the
 * window scrolling, Back landing where it was, the next page painting within 150 ms — is
 * web/scripts/shell_render_check.py.
 *
 * What it proves:
 *   * the scroll keeper's plan: a new page at the top; Back and Forward where the entry was; a tab tapped in
 *     the salesman tab bar where that tab was left, the open tab tapped again back to its top; a filter in the
 *     query or a drawer's ?open= leaves the page where it is; a reload restores, a typed address does not;
 *   * the page glide is the motion system's: 200 ms and 8 px on the enter curve, 120 ms of opacity alone when
 *     calm — transform and opacity only;
 *   * the warm-ups: every route App.tsx renders inside the shells has its page's code listed, the same page
 *     object App.tsx renders; each page read fetched ahead uses that page's own queryKey and URL; the idle
 *     order follows the menu without the page on screen; Save-Data and 2G warm nothing at idle;
 *   * the office shell, read as text: the window scrolls (no h-screen box, no scrolling <main>), the sidebar
 *     sticks at h-dvh and swaps its width without animating it, the top bar is solid (no blur), nothing waits
 *     for the old page (no AnimatePresence mode="wait"), one page-shaped skeleton, an error boundary per
 *     route, menu links warm their page; the salesman shell keeps its tabs' places and warms them; a salesman's
 *     landing is warmed; App.css and Stub.tsx are gone.
 */
import { registerHooks } from 'node:module'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'
import ts from 'typescript'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')
const isFile = (p) => existsSync(p) && statSync(p).isFile()
const API = path.join(SRC, 'lib', 'api.ts')
const API_STUB = 'data:text/javascript,' + encodeURIComponent('export const apiGet = async () => ({}); export const API_BASE = "";')

registerHooks({
  resolve(spec, ctx, next) {
    let s = spec
    if (s.startsWith('@/')) s = path.join(SRC, s.slice(2))
    else if (s.startsWith('./') || s.startsWith('../')) s = path.resolve(path.dirname(fileURLToPath(ctx.parentURL)), s)
    else return next(spec, ctx)
    for (const cand of [s, s + '.ts', s + '.tsx', path.join(s, 'index.ts')]) {
      if (isFile(cand)) {
        if (path.resolve(cand) === API) return { url: API_STUB, shortCircuit: true }
        return { url: pathToFileURL(cand).href, shortCircuit: true }
      }
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

async function load(rel) {
  try {
    return await import(pathToFileURL(path.join(SRC, rel)).href)
  } catch (e) {
    return { __missing: `${rel} did not load: ${e && e.message ? e.message.split('\n')[0] : e}` }
  }
}
function need(mod, name) {
  if (mod.__missing) throw new Error(mod.__missing)
  if (!(name in mod)) throw new Error(`${name} is not exported`)
  return mod[name]
}
// a Windows checkout has CRLF, CI has LF: the text checks read one form
const text = (rel) => (isFile(path.join(SRC, rel)) ? readFileSync(path.join(SRC, rel), 'utf8').replace(/\r\n/g, '\n') : '')

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

const S = await load('lib/shellScroll.ts')
const G = await load('lib/pageGlide.ts')
const P = await load('lib/prefetch.ts')
const C = await load('lib/pageCode.ts')
const M = await load('lib/motionOnce.ts')

/* ── 1. the scroll keeper's plan ─────────────────────────────────────────────────────────────── */

await check('a new page opens at the top; Back and Forward land where the entry was (S4)', () => {
  const plan = need(S, 'scrollPlan')
  const base = { first: false, docNav: 'navigate', samePath: false, tapped: false }
  eq(plan({ ...base, navType: 'PUSH', entryY: 900, tabY: 700 }), { kind: 'top', smooth: false }, 'a click to a new page')
  eq(plan({ ...base, navType: 'REPLACE' }), { kind: 'top', smooth: false }, 'a redirect to a new page')
  eq(plan({ ...base, navType: 'POP', entryY: 1200 }), { kind: 'restore', y: 1200 }, 'Back')
  eq(plan({ ...base, navType: 'POP', entryY: 0 }), { kind: 'restore', y: 0 }, 'Back to a page left at its top')
  eq(plan({ ...base, navType: 'POP' }), { kind: 'top', smooth: false }, 'Back to an entry it never saw')
})

await check('the same page in a new entry (a filter in the query, a drawer\'s ?open=) stays where it is', () => {
  const plan = need(S, 'scrollPlan')
  const base = { first: false, docNav: 'navigate', samePath: true, tapped: false }
  eq(plan({ ...base, navType: 'PUSH' }), { kind: 'keep' })
  eq(plan({ ...base, navType: 'REPLACE' }), { kind: 'keep' })
  eq(plan({ ...base, navType: 'POP' }), { kind: 'keep' }, 'Back over a filter it never saw scrolled')
  eq(plan({ ...base, navType: 'POP', entryY: 300 }), { kind: 'restore', y: 300 }, 'Back over a filter that was')
})

await check('salesman tabs: a tab tapped lands where it was left; the open tab tapped again goes to its top', () => {
  const plan = need(S, 'scrollPlan')
  const base = { first: false, docNav: 'navigate', navType: 'PUSH', tapped: true }
  eq(plan({ ...base, samePath: false, tabY: 900, entryY: 50 }), { kind: 'restore', y: 900 }, 'Orders → Catalog from the tab bar')
  eq(plan({ ...base, samePath: false }), { kind: 'top', smooth: false }, 'a tab never scrolled')
  eq(plan({ ...base, navType: 'REPLACE', samePath: true, tabY: 900 }), { kind: 'top', smooth: true }, 'the open tab again')
  // a link to a tab from inside a page (OrderSuccess "My orders", Today's "Orders") is not a tab tap
  eq(plan({ ...base, tapped: false, samePath: false, tabY: 900 }), { kind: 'top', smooth: false }, 'a link to a tab')
})

await check('the first page: a reload or a return to the tab restores; a typed address or a link from elsewhere does not', () => {
  const plan = need(S, 'scrollPlan')
  const base = { first: true, navType: 'POP', samePath: false, tapped: false, entryY: 640 }
  eq(plan({ ...base, docNav: 'reload' }), { kind: 'restore', y: 640 })
  eq(plan({ ...base, docNav: 'back_forward' }), { kind: 'restore', y: 640 })
  eq(plan({ ...base, docNav: 'navigate' }), { kind: 'keep' })
  eq(plan({ ...base, docNav: 'reload', entryY: undefined }), { kind: 'keep' })
})

/* ── 2. the page glide ──────────────────────────────────────────────────────────────────────── */

await check('the page glide: 200 ms and an 8 px rise on the enter curve; calm = 120 ms of opacity alone', () => {
  const frames = need(G, 'glideFrames')
  const DURATION = need(M, 'DURATION')
  const ENTER = need(M, 'ENTER_EASE')
  const live = frames(false)
  eq(live.options.duration, DURATION.fade * 1000, 'the motion system\'s fade')
  eq(live.options.easing, `cubic-bezier(${ENTER.join(',')})`, 'the enter curve')
  eq(live.keyframes, [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }])
  const calm = frames(true)
  eq(calm.keyframes, [{ opacity: 0 }, { opacity: 1 }], 'nothing rises under reduced motion')
  ok(calm.options.duration <= 120, 'a calm fade of 120 ms or less')
  for (const k of [...live.keyframes, ...calm.keyframes]) {
    for (const prop of Object.keys(k)) ok(prop === 'opacity' || prop === 'transform', `only transform and opacity (${prop})`)
  }
})

/* ── 3. the warm-ups ────────────────────────────────────────────────────────────────────────── */

/** each Route inside <ProtectedRoute> of App.tsx: its path and the page components its element renders */
function appRoutes() {
  const app = text('App.tsx')
  const block = app.split('<Route element={<ProtectedRoute />}>')[1]?.split('</Route>')[0] || ''
  const out = []
  for (const m of block.matchAll(/<Route\s+(?:path="([^"]*)"|index)\s+element=\{(.*?)\}\s*\/>/g)) {
    const comps = [...m[2].matchAll(/<([A-Z]\w*)[\s/>]/g)].map((x) => x[1]).filter((c) => !['Gate', 'ShopAnalyticsGate'].includes(c))
    out.push({ path: m[1] === undefined ? '/' : `/${m[1]}`, comps })
  }
  return out
}

await check('App.tsx renders every page from lib/pageCode (portalPage), never React.lazy', () => {
  const app = text('App.tsx')
  ok(!/\blazy\(/.test(app) && !/import \{[^}]*\blazy\b[^}]*\} from 'react'/.test(app), 'no React.lazy left in App.tsx')
  ok(app.includes("} from '@/lib/pageCode'"), 'the pages come from lib/pageCode')
  const code = text('lib/pageCode.ts')
  const pages = [...code.matchAll(/^export const (\w+) = portalPage\(\(\) => import\('@\/pages\/[^']+'\)\)$/gm)].map((m) => m[1])
  ok(pages.length >= 38, `every page is one portalPage line (${pages.length})`)
  const imported = app.split("} from '@/lib/pageCode'", 1)[0].split('import {').pop().split(',').map((s) => s.trim()).filter(Boolean)
  for (const name of imported) ok(pages.includes(name), `${name} is a portalPage in lib/pageCode`)
})

await check('every route inside the shells has its page\'s code listed — the same page object App.tsx renders', () => {
  const codeFor = need(C, 'codeFor')
  const routes = appRoutes()
  ok(routes.length >= 35, `routes read from App.tsx (${routes.length})`)
  for (const r of routes) {
    if (r.path.includes(':')) continue // a detail page (orders/:poNo…) has no menu item to warm it from
    const comps = r.path === '/' ? ['Dashboard'] : r.comps // Home renders the Dashboard for the office
    ok(comps.length > 0, `${r.path} renders a page`)
    const warm = codeFor(r.path)
    for (const c of comps) ok(warm.includes(need(C, c)), `${r.path} warms ${c}`)
  }
  ok(codeFor('/shop').length === 2, 'Catalog warms ShopPage and the rep\'s StaffCatalog chunk it loads')
  eq(codeFor('/sales?rep=x#top').length, 1, 'a query or a hash does not change the page')
  eq(codeFor('/nowhere').length, 0, 'an unknown path warms nothing')
})

await check('every page menu and tab link has code to warm (lib/nav NAV, MANAGEMENT_NAV, the salesman tabs)', () => {
  const codeFor = need(C, 'codeFor')
  const nav = text('lib/nav.ts')
  const links = [...nav.matchAll(/to: '([^']+)'/g)].map((m) => m[1])
  ok(links.length >= 30, `links read from lib/nav.ts (${links.length})`)
  for (const to of links) ok(codeFor(to).length > 0, `${to} has code`)
  for (const to of ['/today', '/shop', '/shop-orders', '/customers', '/account', '/settings']) ok(codeFor(to).length > 0, `${to} has code`)
})

await check("each page read fetched ahead is the page's own queryKey and URL (a wrong key would fetch twice)", () => {
  const DATA = need(P, 'PAGE_DATA')
  const file = { '/': 'pages/Dashboard.tsx', '/inventory': 'pages/Inventory.tsx', '/sales': 'pages/Sales.tsx', '/margins': 'pages/Margins.tsx',
    '/receivables': 'pages/Receivables.tsx', '/catalog': 'pages/Catalog.tsx', '/orders': 'pages/Orders.tsx', '/today': 'pages/sales/lib.ts' }
  const lit = (key) => `[${key.map((k) => `'${k}'`).join(', ')}]`
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')
  for (const [to, d] of Object.entries(DATA)) {
    if (to.startsWith('/command')) {
      // the Command Centre's read is per period, 'mtd' unless the address says otherwise
      const cc = text('pages/CommandCentre.tsx')
      ok(cc.includes("queryKey: ['management', 'overview', period]") && cc.includes('apiGet<Overview>(`/management/overview?period=${period}`)'), 'the Command Centre read')
      ok(/\? \(raw as PeriodKey\) : 'mtd'/.test(cc), "its default period is 'mtd'")
      eq(d, { key: ['management', 'overview', 'mtd'], url: '/management/overview?period=mtd' }, to)
      continue
    }
    const src = text(file[to])
    ok(src, `${to}: its page source is known`)
    const re = new RegExp(`queryKey: ${esc(lit(d.key))},\\s*queryFn: \\(\\) => apiGet(?:<\\w+>)?\\('${esc(d.url)}'\\)`)
    ok(re.test(src), `${to}: ${file[to]} reads ${lit(d.key)} from ${d.url}`)
  }
})

await check('the idle warm-up follows the menu, skips the page on screen and repeats, and stays off on Save-Data / 2G', () => {
  const order = need(P, 'warmOrder')
  const light = need(P, 'lightConnection')
  eq(order(['/command', '/shop-orders', '/shop', '/command', '/nowhere', '/sales?rep=a'], '/shop-orders'), ['/command', '/shop', '/sales'])
  eq(order(['/', '/inventory'], '/'), ['/inventory'], 'the Dashboard on screen')
  ok(light({ saveData: true }) && light({ effectiveType: '2g' }) && light({ effectiveType: 'slow-2g' }), 'light connections')
  ok(!light({ effectiveType: '4g' }) && !light({ effectiveType: '3g' }) && !light(null) && !light(undefined), 'ordinary connections')
  ok(need(P, 'HOVER_INTENT_MS') >= 40 && need(P, 'HOVER_INTENT_MS') <= 120, 'a hover rests briefly before it fetches')
  ok(need(P, 'WARM_START_MS') >= 1000 && need(P, 'WARM_START_MS') <= 3000, 'the idle warm-up waits for the first page to draw')
})

await check('a warm-up that meets a missing chunk never reloads the tab (the reload rule is for a page the person opens)', () => {
  // release review, 30-Oct-2026: a capturing listener here ran AFTER the rule (a window runs its own listeners in
  // the order they were added), so the warm-ups count themselves in lib/warmState and the rule asks it
  const src = text('lib/prefetch.ts')
  ok(!/stopImmediatePropagation/.test(src), 'no listener racing the reload rule')
  ok(/warmStarted\(\)[\s\S]*finally \{\s*warmEnded\(\)/.test(src), 'the count drops whatever the warm-up\'s outcome')
  ok(text('main.tsx').includes('installPreloadReload(isWarming)'), 'the reload rule asks whether a warm-up is running')
})

/* ── 4. the shells, read as text ────────────────────────────────────────────────────────────── */

await check('the office: the window scrolls (S9) — no h-screen box, no scrolling <main>, sticky works inside pages', () => {
  const shell = text('components/AppShell.tsx')
  ok(!shell.includes('h-screen w-full overflow-hidden'), 'the root is no longer a fixed-height clipping box')
  ok(shell.includes('<div className="flex min-h-screen w-full">'), 'the root grows with its page')
  const main = shell.split('<main ', 2)[1]?.split('>', 1)[0] || ''
  ok(main.includes('className="relative z-10 flex-1"') && !/overflow-y-(auto|scroll)/.test(main), `<main> does not scroll (${main})`)
  ok(!/overflow-x-hidden/.test(shell), 'no overflow-x-hidden (it made a scroll container: nothing inside a page stuck)')
  ok(shell.includes('overflow-x-clip px-4 py-6 pb-[calc(1.5rem+var(--yq-capture-room,0px))]'), 'the page box clips sideways only')
  ok(shell.includes("root.style.setProperty('--yq-topbar', `${TOPBAR_PX}px`)") && shell.includes('const TOPBAR_PX = 64'), 'a page\'s sticky bar hangs under the 64 px top bar')
  ok(shell.includes("root.style.scrollbarGutter = 'stable'"), 'a stable gutter: short and long pages line up')
  ok(/fixed inset-0 z-0" aria-hidden>\s*<div className="luxe-canvas" \/>/.test(shell), 'the canvas is pinned to the window, not stretched down the page')
  ok(/useShellScroll\(\)/.test(shell), 'a new page at the top, Back where it was')
})

await check('the office: the sidebar sticks at h-dvh and swaps its width at once; the top bar is solid (item 23, no blur)', () => {
  const shell = text('components/AppShell.tsx')
  const aside = shell.split('<aside', 2)[1]?.split('>', 1)[0] || ''
  ok(/lg:sticky lg:top-0/.test(aside) && /lg:h-dvh/.test(aside), 'the sidebar sticks to the window from 1024 px')
  ok(!/transition-\[width\]|transition-all/.test(aside), 'no width animation on collapse')
  ok(/collapsed \? 'lg:w-\[76px\]' : 'lg:w-\[260px\]'/.test(aside), 'an instant swap of 76 / 260 px')
  const link = shell.split('<NavLink', 2)[1]?.split('</NavLink>', 1)[0] || ''
  ok(link.includes('transition-colors') && !link.includes('transition-all'), 'menu items animate colour only (their padding changes on collapse)')
  const header = shell.split('<header ', 2)[1]?.split('>', 1)[0] || ''
  ok(header.includes('sticky top-0') && !/backdrop-blur/.test(header) && /\bbg-background\b(?!\/)/.test(header), `a sticky, solid top bar (${header})`)
  ok(!/backdrop-blur/.test(shell), 'no blur anywhere in the office shell')
  // the rep's phone: the floating tab bar and the header sat at 90 % with a blur over the scrolling catalog
  const rep = text('components/SalesmanShell.tsx')
  ok(!/backdrop-blur/.test(rep), 'no blur in the salesman shell (its tab bar and header are solid)')
  // StaffCatalog's fixed order bar reads these: in the office it floated 64 px up and over the sidebar at 1024-1279
  ok(/--yq-tabbar/.test(shell) && /--yq-sidebar/.test(shell), 'the office publishes --yq-tabbar and --yq-sidebar for the pages that pin bars')
})

await check('the office: nothing waits for the old page; one page-shaped skeleton; an error boundary per route (S5, S8, item 24)', () => {
  const shell = text('components/AppShell.tsx')
  ok(!/<AnimatePresence\s+mode="wait"/.test(shell), 'no <AnimatePresence mode="wait"> (a 280 ms exit before every page)')
  ok(/<PageGlide\s+key=\{loc\.pathname\}\s+glide=\{navigated\}/.test(shell), 'a keyed page that glides in on mount, never on the first page')
  ok(/<PageErrorBoundary resetKey=\{loc\.pathname\}>\s*<Suspense fallback=\{<PageSkeleton \/>\}>\{outlet\}<\/Suspense>\s*<\/PageErrorBoundary>/.test(shell),
    'the page\'s own error boundary around one skeleton fallback')
  ok(!/Loader2/.test(shell), 'the spinner is gone')
  ok(/\{\.\.\.warmOn\(n\.to\)\}/.test(shell) && /useCodeWarm\(\[\.\.\.items\.map\(\(n\) => n\.to\), '\/settings'\], loc\.pathname/.test(shell),
    'menu links warm their page; the pages this login can open warm at idle')
  ok(/onClick=\{markPending\(n\.to\)\}/.test(shell) && /\(pendingTo \? pendingTo === n\.to : isActive\)/.test(shell),
    'the clicked menu item is lit at once, while its page renders')
  ok(/e\.button !== 0 \|\| e\.metaKey \|\| e\.ctrlKey \|\| e\.shiftKey \|\| e\.altKey/.test(shell), 'not for a Ctrl/⌘, shift or middle click (a new tab)')
  const frame = text('lib/pageFrame.tsx')
  ok(/getDerivedStateFromProps[\s\S]*resetKey !== state\.key/.test(frame), 'a new page starts without the last page\'s error')
  ok(/reportError\('portal', error\)/.test(frame) && /window\.location\.reload\(\)/.test(frame), 'reported like the app-wide one, with Reload')
  ok(/data-page-skeleton/.test(frame) && /aria-busy="true"/.test(frame), 'the skeleton says it is loading')
  ok(text('PortalRoot.tsx').includes('<ErrorBoundary>'), 'the app-wide screen stays, for a broken shell only')
})

await check('the salesman shell: each tab keeps its place, tab links warm their page and say they are tab taps; per-route errors', () => {
  const shell = text('components/SalesmanShell.tsx')
  ok(/useShellScroll\(tabPaths\)/.test(shell), 'the keeper knows the tabs')
  ok(/useCodeWarm\(tabPaths, loc\.pathname/.test(shell), 'the tabs\' code at idle')
  ok((shell.match(/\{\.\.\.tabLink\(t\.to\)\}/g) || []).length === 3, 'the floating tabs, the top tabs and the sidebar tabs')
  ok(/onClick: \(\) => noteTabTap\(to\)/.test(shell), 'a tab link tells the keeper it is a tab tap')
  ok(/<PageErrorBoundary resetKey=\{loc\.pathname\}>/.test(shell), 'a page that breaks keeps the tabs')
  // the hooks run before the temporary-password redirect (the rules of hooks)
  ok(shell.indexOf('useShellScroll(tabPaths)') < shell.indexOf('if (mustReset && loc.pathname !== passwordScreen'), 'hooks first')
})

await check("a salesman's landing is warmed during /me (it used to be skipped: his first tap froze)", () => {
  const auth = text('lib/auth.tsx')
  ok(/if \(role === 'salesman'\) \{\s*warmSalesmanLanding\(queryClient\)\s*return\s*\}/.test(auth), 'Today\'s code and read')
  ok(!/if \(role === 'salesman'\) return/.test(auth), 'no longer skipped')
  const pf = text('lib/prefetch.ts')
  ok(/export function warmSalesmanLanding\(qc: QueryClient\): void \{\s*void preloadPage\('\/today'\)\s*prefetchPageData\(qc, '\/today'\)/.test(pf), 'the landing is Today')
})

await check('dead files are gone: App.css and Stub.tsx', () => {
  ok(!isFile(path.join(SRC, 'App.css')), 'web/src/App.css')
  ok(!isFile(path.join(SRC, 'components', 'Stub.tsx')), 'web/src/components/Stub.tsx')
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
