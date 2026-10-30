/**
 * The portal's first seconds (ship 2, items 14 and 15), on the real modules — plain node, no browser,
 * no new deps.
 *
 *   node web/scripts/portal_boot_test.mjs        (from the repo root or from web/)
 *
 * Same loader as rep_field_test.mjs (a module.registerHooks load hook, Node 22.15+, the project's own
 * `typescript`; nothing is compiled to disk). The browser half — the frames on screen, the 4 s rule on a
 * real sign-in, the intro's holds and skips — is web/scripts/portal_boot_check.py.
 *
 * What it proves:
 *   • S7: the served first script (src/boot/portal-boot.js, run here as the browser runs it) and the
 *     app (boot/handoff.ts bootKindFor) pick the same frame for every page and storage state — the night
 *     for a signed-out visit, the office or the field shell for a returning user, nothing for the public
 *     links — and the saved theme lands on <html> before the first paint;
 *   • the frame itself: every part it draws has its rule, nothing in it blurs, it only ever animates
 *     transform and opacity, it takes no taps and it cannot outlive a dead app;
 *   • the build: the portal's HTML gets the frame, the first script, viewport-fit=cover and the plum
 *     bar from a portal-only plugin; the market's HTML path is not touched;
 *   • the 4 s rule: a signed-in page waiting for /me shows the shell's outline, the connecting screen
 *     only after 4 s, and the sign-in waits briefly for /me instead of flashing a screen in between;
 *   • the intro: at most once a week per device, never twice in a tab, only onto the night (never a
 *     signed-in shell, an invite or a public link), the old intro's 'done' counts as a week old, blocked
 *     storage means no intro; it skips on a tap, the wheel or a key, and the login has no blur left.
 */
import { registerHooks } from 'node:module'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'
import vm from 'node:vm'
import ts from 'typescript'

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SRC = path.join(WEB, 'src')
const isFile = (p) => existsSync(p) && statSync(p).isFile()
const read = (rel) => (isFile(path.join(WEB, rel)) ? readFileSync(path.join(WEB, rel), 'utf8') : '')

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
function ok(v, what = '') {
  if (!v) throw new Error(`${what} expected truthy`)
}
function eq(a, b, what = '') {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${what} expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`)
}

// ── a browser's storage, enough for these modules ──────────────────────────────────────────────────
class Store {
  constructor(kv = {}, blocked = false) {
    this.m = new Map(Object.entries(kv))
    this.blocked = blocked
  }
  guard() {
    if (this.blocked) throw new Error('SecurityError: storage is blocked')
  }
  get length() { this.guard(); return this.m.size }
  key(i) { this.guard(); return [...this.m.keys()][i] ?? null }
  getItem(k) { this.guard(); return this.m.has(k) ? this.m.get(k) : null }
  setItem(k, v) { this.guard(); this.m.set(k, String(v)) }
  removeItem(k) { this.guard(); this.m.delete(k) }
}
const SESSION = JSON.stringify({ access_token: 'a.b.c', refresh_token: 'qa-not-a-token', user: { id: 'qa' } })
const SB = 'sb-qaref-auth-token'

/** run the served first script exactly as the browser does: a classic script, before the first paint */
function runBootScript(pathname, kv, blocked = false) {
  const attrs = {}
  const ls = new Store(kv, blocked)
  const ctx = {
    document: { documentElement: { setAttribute: (k, v) => { attrs[k] = String(v) }, getAttribute: (k) => attrs[k] ?? null } },
    location: { pathname },
    JSON,
  }
  ctx.window = { localStorage: blocked ? undefined : ls }
  if (blocked) Object.defineProperty(ctx.window, 'localStorage', { get() { throw new Error('SecurityError') } })
  vm.runInNewContext(read('src/boot/portal-boot.js'), ctx, { filename: 'portal-boot.js' })
  return attrs
}
/** the app's globals for one page load */
function setPage(pathname, kv = {}, blocked = false, session = {}) {
  globalThis.localStorage = new Store(kv, blocked)
  globalThis.sessionStorage = new Store(session, blocked)
  globalThis.window = globalThis
  globalThis.location = { pathname }
}

const H = await load('boot/handoff.ts')
const F = await load('boot/frames.ts')
const G = await load('boot/introGate.ts')

const CASES = [
  // path, storage, the frame
  ['/login', {}, 'night'],
  ['/login', { [SB]: SESSION }, 'night'],
  ['/', {}, 'night'],
  ['/shop-orders', {}, 'night'],
  ['/', { [SB]: SESSION }, 'shell'],
  ['/shop-orders', { [SB]: SESSION, 'yq-role': 'admin' }, 'shell'],
  ['/command', { [SB]: SESSION, 'yq-role': 'management' }, 'shell'],
  ['/today', { [SB]: SESSION, 'yq-role': 'salesman' }, 'sales'],
  ['/today', { 'yq-role': 'salesman' }, 'night'],
  ['/', { [SB]: JSON.stringify({ currentSession: { access_token: 'x' } }) }, 'shell'],
  ['/', { [SB]: 'not json' }, 'night'],
  ['/', { [SB]: JSON.stringify({ refresh_token: 'only' }) }, 'night'],
  ['/', { 'sb-qaref-auth-token-code-verifier': SESSION }, 'night'],
  ['/invite', {}, 'plain'],
  ['/invite', { [SB]: SESSION }, 'plain'],
  ['/c/abc123', { [SB]: SESSION }, 'off'],
  ['/o/tok', {}, 'off'],
  ['/f/tok', {}, 'off'],
  ['/optout/tok', {}, 'off'],
  ['/catalog', { [SB]: SESSION }, 'shell'],
  ['/customers', { [SB]: SESSION, 'yq-role': 'salesman' }, 'sales'],
]

check('S7: the served first script picks the frame for every page and storage state (night / shell / sales / plain / off)', () => {
  for (const [p, kv, want] of CASES) eq(runBootScript(p, kv)['data-boot'], want, `${p} ${Object.keys(kv).join(',') || '(empty)'}`)
  // blocked storage: no session can be known, so the night; never an exception before the first paint
  eq(runBootScript('/', {}, true)['data-boot'], 'night', 'blocked storage')
})

check('S7: the app (boot/handoff.ts bootKindFor) agrees with the served script on every case — the two never hand over different pictures', () => {
  const bootKindFor = need(H, 'bootKindFor')
  for (const [p, kv, want] of CASES) {
    setPage(p, kv)
    eq(bootKindFor(p), want, `${p} ${Object.keys(kv).join(',') || '(empty)'}`)
  }
  setPage('/', {}, true)
  eq(bootKindFor('/'), 'night', 'blocked storage')
})

check('S7: the saved theme is on <html> before the first paint (no light flash for a dark user); the public pages stay as they were', () => {
  eq(runBootScript('/', { 'yq-theme': 'dark', [SB]: SESSION })['data-theme'], 'dark', 'dark shell')
  eq(runBootScript('/login', { 'yq-theme': 'dark' })['data-theme'], 'dark', 'dark login')
  eq(runBootScript('/', {})['data-theme'], 'light', 'default')
  eq(runBootScript('/', { 'yq-theme': 'purple' })['data-theme'], 'light', 'an unknown value')
  eq(runBootScript('/c/abc', { 'yq-theme': 'dark' })['data-theme'], undefined, 'a public catalog keeps its light page (it has no theme)')
  const theme = read('src/lib/theme.tsx')
  ok(/useLayoutEffect\(\(\) => \{\s*document\.documentElement\.setAttribute\('data-theme', theme\)/.test(theme), 'lib/theme.tsx applies it before paint (a layout effect)')
  ok(/getItem\('yq-theme'\) === 'dark'/.test(theme) && /catch/.test(theme), 'lib/theme.tsx reads the same rule, and survives blocked storage')
})

check('the collapsed rails travel with the frame (AppShell yq-collapsed, SalesmanShell yq-sales-rail)', () => {
  eq(runBootScript('/', { [SB]: SESSION, 'yq-collapsed': '1' })['data-boot-rail'], 'narrow', 'office rail')
  eq(runBootScript('/', { [SB]: SESSION, 'yq-collapsed': '0' })['data-boot-rail'], undefined, 'office rail open')
  eq(runBootScript('/today', { [SB]: SESSION, 'yq-role': 'salesman', 'yq-sales-rail': '1' })['data-boot-rail'], 'narrow', 'field rail')
  eq(runBootScript('/today', { [SB]: SESSION, 'yq-role': 'salesman', 'yq-collapsed': '1' })['data-boot-rail'], undefined, "the office key is not the field app's")
})

check('the frame: every part has its rule, nothing blurs, only transform and opacity move, no taps, a 10 s safety net', () => {
  const css = need(F, 'PORTAL_BOOT_CSS')
  const parts = need(F, 'BOOT_PARTS')
  const html = need(F, 'bootHtml')()
  for (const kind of ['night', 'plain', 'shell', 'sales']) ok(html.includes(`class="yqb yqb-${kind}"`), `the served node carries the ${kind} frame`)
  ok(html.startsWith('<div id="yq-boot" aria-hidden="true">'), 'one aria-hidden node')
  ok(!/<img|src=|url\(|https?:/.test(html + css), 'no image or request on the critical path')
  const walk = (list) => list.flatMap(([cls, kids]) => [cls, ...(kids ? walk(kids) : [])])
  for (const cls of new Set(Object.values(parts).flatMap(walk).filter(Boolean))) ok(css.includes(`.${cls}`), `a rule for .${cls}`)
  ok(!/(^|[;{])\s*(-webkit-)?(backdrop-)?filter\s*:/.test(css), 'no filter or backdrop-filter anywhere in the frame')
  for (const [, body] of css.matchAll(/@keyframes [\w-]+\{((?:[^{}]*\{[^{}]*\})*)\}/g)) {
    const props = [...body.matchAll(/([a-z-]+)\s*:/g)].map((m) => m[1])
    for (const p of props) ok(['opacity', 'transform', 'visibility'].includes(p), `keyframes animate ${p}`)
  }
  ok(/#yq-boot\{[^}]*pointer-events:none/.test(css) && /\.yqb\{[^}]*pointer-events:none/.test(css), 'never a tap target')
  ok(/#yq-boot\{[^}]*animation:yqb-out [^}]* 10s both/.test(css), 'hides itself if no screen takes over within 10 s')
  ok(/html\[data-boot=plain\] #yq-boot\{z-index:-1/.test(css), "/invite's flat night sits behind its page, which nobody hands over")
  ok(/html:not\(\[data-boot\]\) #yq-boot,html\[data-boot=off\] #yq-boot\{display:none\}/.test(css), 'no frame when the script did not run, or on a public page')
  ok(/\.yqb-night \.yqb-glow\{[^}]*opacity:0/.test(css) && /\.yqb-glow\[data-in\]\{animation:yqb-bloom/.test(css), 'the one glow is off in the served frame and blooms once on the login')
  ok(/safe-area-inset-bottom/.test(css), "the field app's tab bar sits above the home bar, like the app's")
  // a frame painted mid-parse (after <body>, before the node) still shows the frame, not the bare canvas
  ok(/html\[data-boot=night\] body,html\[data-boot=plain\] body\{background:#140F24\}/.test(css), 'the night is on <body> from the first style pass')
  ok(/html\[data-boot=shell\] body\{background:/.test(css) && /html\[data-boot=shell\] body\{background:linear-gradient\(180deg,#2A1259/.test(css), "the office shell's bars, and its night sidebar from 1024 px, are on <body> too")
  ok(/html\[data-boot=sales\] body\{background:/.test(css), "and the field app's header")
  const handoff = read('src/boot/handoff.ts')
  ok(/getElementById\(BOOT_ID\)\?\.remove\(\)\s*\n[^\n]*\n\s*document\.documentElement\.removeAttribute\('data-boot'\)/.test(handoff), 'the hand-over takes the node and the <body> base away together')
})

check('the build: a portal-only plugin writes the frame, the first script, viewport-fit=cover and the plum bar; the market path is untouched', () => {
  const cfg = read('vite.config.ts')
  ok(/function portalHtml\(\): Plugin/.test(cfg), 'vite.config.ts portalHtml()')
  const plugins = cfg.slice(cfg.indexOf('plugins: ['))
  const marketBranch = plugins.slice(0, plugins.indexOf(': ['))
  ok(!marketBranch.includes('portalHtml()') && /:\s*\[\s*portalHtml\(\),\s*VitePWA\(/.test(plugins), 'registered in the portal branch only')
  ok(cfg.includes('viewport-fit=cover') && cfg.includes("theme: '#6D4091'"), 'viewport-fit=cover and the logo plum')
  ok(/theme_color: PORTAL\.theme/.test(cfg) && !/theme_color: '#6d28d9'/.test(cfg), "the manifest's colour is the plum too")
  ok(cfg.includes('`</title>\\n    <script src="${src}"></script>`') && cfg.includes('the title no longer comes before catalog-prefetch.js'),
    'the first script: right after the title, so ahead of catalog-prefetch.js and the app')
  ok(/throw new Error\(`portalHtml\(\): index\.html no longer contains/.test(cfg), 'a rewrite that misses fails the build')
  ok(/import \{ BOOT_CSS_ID, PORTAL_BOOT_CSS, bootHtml \} from '\.\/src\/boot\/frames'/.test(cfg), 'the CSS and the node come from src/boot/frames.ts, the source React draws from')
  // the market's own rewrite still finds the same index.html lines it always matched
  const html = read('index.html')
  for (const line of ['<meta name="theme-color" content="#6d28d9" />', '<meta name="viewport" content="width=device-width, initial-scale=1.0" />', '<script src="/catalog-prefetch.js"', '<div id="root"></div>'])
    ok(html.includes(line) && cfg.includes(line), `index.html and the rewrites agree on ${line}`)
})

check('the 4 s rule: a waiting page shows the shell\'s outline, the connecting screen only after 4 s; the sign-in waits briefly for /me', () => {
  const guards = read('src/components/guards.tsx')
  ok(/const CONNECTING_AFTER_MS = 4000/.test(guards), 'CONNECTING_AFTER_MS = 4000')
  ok(/setTimeout\(\(\) => setLate\(true\), CONNECTING_AFTER_MS\)/.test(guards), 'a timer of that length')
  ok(/return late \? <Connecting \/> : <BootScreen kind=\{shellKind\(\)\} \/>/.test(guards), "the outline until then (the field app's for a salesman)")
  const route = guards.slice(guards.indexOf('export function ProtectedRoute'))
  ok(route && !/return <Connecting \/>/.test(route), 'ProtectedRoute never shows the connecting screen at once')
  ok(/if \(loading\) return hasStoredSession\(\) \? <Waiting \/> : <BootScreen kind="night" \/>/.test(route), 'booting: the outline for a stored session, the night for none — never the old light spinner')
  ok(/useReleaseBoot\(\)/.test(route), 'it takes over from the served frame')
  ok(!/function Splash\(/.test(guards), 'the logo-and-spinner splash is gone')
  const auth = read('src/lib/auth.tsx')
  const signIn = auth.slice(auth.indexOf('const signIn = async'), auth.indexOf('const signOut = async'))
  ok(/await Promise\.race\(\[loadMe\(\), sleep\(1200\)\]\)/.test(signIn), 'signIn waits up to 1.2 s for the first /me (joins the load in flight)')
})

check('the intro: at most weekly per device, never twice in a tab, only onto the night; old stamps and blocked storage', () => {
  const introDue = need(G, 'introDue')
  const mark = need(G, 'markIntroShown')
  const K = 'yq-intro-v2'
  const DAY = 864e5
  const now = Date.UTC(2026, 9, 30, 6)
  setPage('/login')
  ok(introDue(K, now), 'a first visit')
  mark(K, now)
  ok(!introDue(K, now + 1000), 'not twice in a tab')
  eq(globalThis.localStorage.getItem(K), String(now), 'the device stamp is the time')
  setPage('/login', { [K]: String(now - 2 * DAY) })
  ok(!introDue(K, now), 'a new tab two days later: not yet')
  setPage('/login', { [K]: String(now - 7 * DAY - 1) })
  ok(introDue(K, now), 'a week later: again')
  setPage('/login', { [K]: 'done' })
  ok(introDue(K, now), "the old intro's 'done' counts as a week old")
  setPage('/login', { [K]: String(now + 3 * DAY) })
  ok(introDue(K, now), 'a stamp from the future (a clock put back) never silences it')
  setPage('/', { [SB]: SESSION })
  ok(!introDue(K, now), 'never over a signed-in shell')
  setPage('/', {})
  ok(introDue(K, now), 'a protected page with no session (on its way to the login)')
  for (const p of ['/invite', '/c/tok', '/o/tok', '/f/tok', '/optout/tok']) {
    setPage(p)
    ok(!introDue(K, now), `never on ${p}`)
  }
  setPage('/login', {}, true)
  ok(!introDue(K, now), 'blocked storage: no intro (no way to keep it weekly)')
})

check('the intro component: weekly gate, the salesman skip and reduced motion decided at mount; skip on tap, wheel or key; full holds; no SVG redraw', () => {
  const hero = read('src/components/ui/arc-preloader-hero.tsx')
  const init = hero.split('React.useState<Phase>(() => {')[1]?.split('})')[0] || ''
  ok(/!introDue\(storageKey\)/.test(init) && /prefersReducedMotion \? 'still' : 'intro'/.test(init), 'the gate and the still frame, in the initializer')
  ok(/markIntroShown\(storageKey\)/.test(hero) && /releaseBoot\(\)/.test(hero), 'stamps as it starts, takes over from the served night')
  ok(/onPointerDown=\{skip\}/.test(hero) && /onWheel=\{skip\}/.test(hero) && /addEventListener\('keydown', onKey, true\)/.test(hero), 'a tap or swipe, the wheel, any key')
  ok(/if \(e\.key === 'Enter' \|\| e\.key === ' '\) e\.preventDefault\(\)/.test(hero), 'the key that skips presses nothing under the night')
  ok(/if \(phase !== 'intro' \|\| entered !== index\) return/.test(hero) && /\}, holdFor\(word\.text, greetingHold\)\)/.test(hero), 'each hold is counted from the moment its word is fully in')
  ok(/onAnimationComplete=\{\(def\) => \{/.test(hero) && /\.opacity === 1\) setEntered\(index\)/.test(hero), 'the word itself reports that it is in (the entrance, not the exit)')
  ok(/return Math\.max\(hold, 120 \+ 19 \* text\.length\)/.test(hero), 'a long word gets the time it takes to read, whatever hold is asked for')
  ok(/<AnimatePresence mode="wait">/.test(hero), 'one word at a time')
  ok(!/useMotionValue|useTransform|motion\.path|<svg/.test(hero), 'no full-screen SVG path redrawn every frame')
  ok(/<IntroContext\.Provider value=\{playing\}>/.test(hero), 'the login knows when the night clears')
})

check('the login: a static night with one transform-only glow; no blur, no backdrop blur, plum button, 16 px fields', () => {
  const login = read('src/pages/Login.tsx')
  const glow = read('src/components/ui/glow-horizon.tsx')
  const quote = read('src/components/Quote.tsx')
  // the code, not the comments that say what it replaced
  const code = (src) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  for (const [name, src] of [['Login.tsx', login], ['glow-horizon.tsx', glow], ['Quote.tsx', quote]]) {
    ok(src, `${name} exists`)
    ok(!/blur\(|backdrop-blur|filter:/.test(code(src)), `${name}: no blur, animated or not`)
  }
  ok(/<NightScene glow=\{!intro\} \/>/.test(login) && /useReleaseBoot\(\)/.test(login), 'the served night as its background, the glow on arrival')
  ok(/'--primary': '273 39% 41%'/.test(login), 'the card is the logo plum #6D4091 in either theme')
  ok((login.match(/className="[^"]*text-base/g) || []).length >= 2, 'the email and password fields are 16 px (no iPhone zoom)')
  ok(/animate=\{intro \? undefined : shown\}/.test(login), 'the card waits for the intro, then rises')
  ok(/<Logo float/.test(login) && /font-display/.test(login), 'the logo float and the display heading kept')
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
