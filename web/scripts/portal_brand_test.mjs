/**
 * The portal's brand (ship 2, 30-Oct-2026, plan item 16 and the look parts of 24): the office admin moves onto
 * the marketplace's identity — the logo plum, Instrument Sans for text and Sora for display, the night family for
 * the sidebar — with one Card, one KPI tile, one page-title style and tokens instead of hand-set hex. No browser:
 * the rules are read from the sources they live in.
 *
 *   node web/scripts/portal_brand_test.mjs        (from the repo root or from web/)
 *
 * Why source rules: every one of these drifted page by page (three purples — the token #6d28d9, the Dashboard's
 * #7c3aed, the Command Centre's plum; four KPI tile designs; three page-title sizes; two greetings that changed
 * at different hours). A rule read from the source stops the next page from drifting again.
 */
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SRC = path.join(WEB, 'src')
const read = (p) => readFileSync(path.join(SRC, p), 'utf8')
const readWeb = (p) => readFileSync(path.join(WEB, p), 'utf8')
/** the source without its comments, so a rule is never satisfied (or broken) by a sentence about it */
const code = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/[^\n]*/g, '$1').replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')

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

const css = read('index.css')
const cssCode = code(css)
const tw = readWeb('tailwind.config.js')
const root = (cssCode.match(/:root[^{]*\{([\s\S]*?)\}/) || [])[1] || ''
const dark = (cssCode.match(/\[data-theme='dark'\]\s*\{([\s\S]*?)\}/) || [])[1] || ''
const token = (block, name) => ((block.match(new RegExp(`--${name}:\\s*([^;]+);`)) || [])[1] || '').trim()

check('the primary and the focus ring are the logo plum (#6D4091), not the electric violet', () => {
  ok(token(root, 'primary') === '273 39% 41%', `:root --primary is ${token(root, 'primary') || 'missing'}`)
  ok(/^273 /.test(token(root, 'ring')), `:root --ring is on the plum hue (${token(root, 'ring')})`)
  ok(/^27[23] /.test(token(dark, 'primary')) && /^27[23] /.test(token(dark, 'ring')), 'dark mode keeps the plum hue')
  ok(!/262 73% 50%|262 83% 67%/.test(cssCode), 'the old violet tokens are gone')
})

check('Instrument Sans for text and Sora for display, self-hosted with font-display: swap (as the marketplace)', () => {
  for (const [fam, file] of [['Instrument Sans', 'instrument-sans-v1.woff2'], ['Sora', 'sora-v1.woff2']]) {
    const face = (cssCode.match(new RegExp(`@font-face\\s*\\{[^}]*font-family:\\s*'${fam}';[^}]*\\}`)) || [])[0] || ''
    ok(face, `an @font-face for ${fam}`)
    ok(face.includes(`url('/fonts/${file}')`) && /font-display:\s*swap/.test(face), `${fam}: /fonts/${file}, swap`)
    ok(existsSync(path.join(WEB, 'public/fonts', file)), `public/fonts/${file} exists`)
  }
  ok(!/font-family:\s*'(Inter|Space Grotesk)'/.test(cssCode), 'no Inter or Space Grotesk face is declared any more')
  ok(/fontFamily:\s*\{[\s\S]*?sans:\s*\[\s*'Instrument Sans'/.test(tw), 'tailwind font-sans starts with Instrument Sans')
  ok(/display:\s*\[\s*'Sora'/.test(tw), 'tailwind font-display starts with Sora')
  ok(!/'Inter'|Space Grotesk/.test(code(tw)), 'tailwind names neither old family')
})

check('the radius scale climbs: 2xl is never smaller than xl', () => {
  const rem = Number((root.match(/--radius:\s*([\d.]+)rem/) || [])[1])
  ok(rem > 0, '--radius in rem')
  const px = (name) => {
    // the key alone: 'xl' must not match inside '2xl'
    const v = (tw.match(new RegExp(`(?<![\\w'"])['"]?${name}['"]?:\\s*'([^']+)'`)) || [])[1]
    if (!v) return null
    if (v === 'var(--radius)') return rem * 16
    const m = v.match(/^calc\(var\(--radius\)\s*([+-])\s*(\d+)px\)$/)
    return m ? rem * 16 + (m[1] === '+' ? 1 : -1) * Number(m[2]) : Number.NaN
  }
  const scale = ['sm', 'md', 'lg', 'xl', '2xl'].map((k) => [k, px(k)])
  for (const [k, v] of scale) ok(Number.isFinite(v), `borderRadius.${k} is set on --radius (${v})`)
  for (let i = 1; i < scale.length; i++) ok(scale[i][1] > scale[i - 1][1], `${scale[i][0]} (${scale[i][1]}px) > ${scale[i - 1][0]} (${scale[i - 1][1]}px)`)
})

check('shadows and glows carry the plum or the ink, never the violet', () => {
  ok(!/rgba\(109,\s*40,\s*217|rgba\(124,\s*58,\s*237|hsl\(26[02] /.test(code(tw) + cssCode), 'no #6d28d9 / #7c3aed rgba and no 262-hue glow left')
})

check('the canvas behind the top bar is still: no animation runs on it forever (a GPU cost on integrated graphics)', () => {
  ok(!/@keyframes luxe-drift/.test(cssCode), 'the drift keyframes are gone')
  ok(!/\.luxe-canvas\s*\{[^}]*animation/.test(cssCode), '.luxe-canvas declares no animation')
})

check('the focus ring follows the control\'s own shape (a round button keeps a round ring)', () => {
  const rule = (cssCode.match(/(^|\n)\s*:focus-visible\s*\{([^}]*)\}/) || [])[2] || ''
  ok(/outline:/.test(rule), 'a :focus-visible outline exists')
  ok(!/border-radius/.test(rule), 'the :focus-visible rule sets no border-radius')
})

check('dead CSS is gone: App.css, the unused glass and gradient-ink classes, Stub.tsx', () => {
  ok(!existsSync(path.join(SRC, 'App.css')), 'src/App.css is deleted (nothing imports it)')
  ok(!existsSync(path.join(SRC, 'components/Stub.tsx')), 'components/Stub.tsx is deleted (nothing renders it)')
  ok(!/\.glass\b|\.text-gradient\b/.test(cssCode), 'no .glass and no .text-gradient')
})

// the files this ship moves onto tokens: no hand-set hex, no Tailwind violet/fuchsia/indigo, no off-brand gradient
const TOKEN_FILES = ['pages/Dashboard.tsx', 'pages/CommandCentre.tsx', 'pages/MarketIntel.tsx', 'components/MarketCapture.tsx',
  'components/PageHeader.tsx', 'components/ui/badge.tsx', 'components/ui/card.tsx', 'components/ui/kpi-tile.tsx']
check('the newest pages and the shared surfaces use tokens, not hex: MarketCapture, Command Centre, Market Intel, Dashboard', () => {
  for (const f of TOKEN_FILES) {
    const src = code(read(f))
    const hex = src.match(/#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![\w-])/g) || []
    ok(hex.length === 0, `${f}: ${hex.length} hex literal(s) left (${[...new Set(hex)].slice(0, 6).join(' ')})`)
    ok(!/\b(violet|fuchsia|indigo|purple)-\d{2,3}\b/.test(src), `${f}: a Tailwind violet/fuchsia/indigo/purple class`)
  }
})

check('one page-title style: the page header and the Command Centre band use the same class', () => {
  const header = code(read('components/PageHeader.tsx'))
  const cc = code(read('pages/CommandCentre.tsx'))
  const h1 = (s) => (s.match(/<h1\b[^>]*className="([^"]+)"/) || [])[1] || ''
  ok(/\bpage-title\b/.test(h1(header)), `PageHeader's h1 uses .page-title (${h1(header)})`)
  ok(/\bpage-title\b/.test(h1(cc)), `the Command Centre's h1 uses .page-title (${h1(cc)})`)
  for (const [who, cls] of [['PageHeader', h1(header)], ['CommandCentre', h1(cc)]]) {
    ok(!/\btext-\[|\btext-(xs|sm|base|lg|xl|[2-9]xl)\b/.test(cls), `${who}'s h1 sets no size of its own (${cls})`)
  }
  ok(/\.page-title\s*\{/.test(cssCode), 'index.css defines .page-title')
  ok(!/bg-gradient-to-b from-violet-500/.test(header), 'no violet-fuchsia-indigo accent rule beside the title')
})

check('one KPI tile: the Dashboard, the Command Centre and every Stat render components/ui/kpi-tile', () => {
  ok(existsSync(path.join(SRC, 'components/ui/kpi-tile.tsx')), 'components/ui/kpi-tile.tsx exists')
  for (const f of ['pages/Dashboard.tsx', 'pages/CommandCentre.tsx', 'components/DataTable.tsx']) {
    const src = code(read(f))
    ok(/from '@\/components\/ui\/kpi-tile'/.test(src), `${f} imports the one tile`)
    ok(!/function (KpiCard|HealthStat)\b/.test(src), `${f} defines no tile of its own`)
  }
})

check('one Card surface: the Command Centre\'s cards are the Card component\'s surface, not a second recipe', () => {
  const card = code(read('components/ui/card.tsx'))
  const cc = code(read('pages/CommandCentre.tsx'))
  ok(/card-luxe/.test(card), 'Card paints .card-luxe')
  ok(!/rounded-2xl border bg-card p-4 shadow-sm/.test(cc), 'no hand-made "rounded-2xl border bg-card shadow-sm" card left in the Command Centre')
})

check('one greeting helper: the top bar and the Dashboard read lib/greeting', () => {
  ok(existsSync(path.join(SRC, 'lib/greeting.ts')), 'lib/greeting.ts exists')
  for (const f of ['components/AppShell.tsx', 'pages/Dashboard.tsx']) {
    const src = code(read(f))
    ok(/from '@\/lib\/greeting'/.test(src), `${f} imports lib/greeting`)
    ok(!/Good (morning|afternoon|evening)/.test(src), `${f} spells no greeting of its own`)
  }
})

check('the shortcut hint says Ctrl K on Windows and ⌘K only on a Mac', () => {
  const shell = code(read('components/AppShell.tsx'))
  ok(!/>⌘K</.test(shell), 'no hard-coded ⌘K in the top bar')
  ok(/from '@\/lib\/shortcut'/.test(shell) && /shortcutHint\(\)/.test(shell), 'the top bar prints shortcutHint()')
  const helper = code(read('lib/shortcut.ts'))
  ok(/Mac|iPhone|iPad/.test(helper) && /Ctrl/.test(helper), 'the hint is chosen by the platform')
})

check('the sidebar is the night family (#2A1259 → #140F24) on tokens, and its active item is plum, not a violet glow', () => {
  const shell = code(read('components/AppShell.tsx'))
  ok(!/#2a1259|#190a3a/i.test(shell), 'no hand-set sidebar gradient in AppShell')
  ok(/sidebar-night/.test(shell), 'the aside paints .sidebar-night')
  ok(/\.sidebar-night\s*\{[^}]*var\(--night-2\)[^}]*var\(--night\)/.test(cssCode), '.sidebar-night runs --night-2 → --night')
  ok(token(root, 'night') === '254.3 41.2% 10%' && token(root, 'night-2') === '260.3 66.4% 21%', 'the night tokens are the marketplace opening\'s')
  ok(!/rgba\(124,58,237/.test(shell), 'no violet glow on the active item')
})

check('page titles match the menu labels', () => {
  const nav = read('lib/nav.ts')
  const label = (to) => (nav.match(new RegExp(`label: '([^']+)', to: '${to.replace(/[/]/g, '\\/')}'`)) || [])[1]
  const PAGES = [['/salesmen', 'pages/Salesmen.tsx'], ['/prices', 'pages/PriceTracker.tsx'], ['/shop-rules', 'pages/ShopRules.tsx'],
    ['/team', 'pages/Team.tsx'], ['/shop-analytics', 'pages/ShopAnalytics.tsx'], ['/agents', 'pages/Agents.tsx'],
    ['/marketing', 'pages/Marketing.tsx'], ['/coaching', 'pages/Coaching.tsx'], ['/inventory', 'pages/Inventory.tsx'],
    ['/orders', 'pages/Orders.tsx'], ['/margins', 'pages/Margins.tsx'], ['/receivables', 'pages/Receivables.tsx'],
    ['/stock', 'pages/StockMovement.tsx'], ['/market-intel', 'pages/MarketIntel.tsx'], ['/shop-orders', 'pages/ShopOrders.tsx'],
    ['/data', 'pages/DataPage.tsx'], ['/upcoming', 'pages/Upcoming.tsx'], ['/picklist', 'pages/PickList.tsx'], ['/feed', 'pages/Feed.tsx']]
  for (const [to, f] of PAGES) {
    const want = label(to)
    ok(want, `nav.ts has a label for ${to}`)
    const titles = [...code(read(f)).matchAll(/<PageHeader[\s\S]*?title=(?:"([^"]+)"|\{'([^']+)'\})/g)].map((m) => m[1] || m[2])
    ok(titles.length, `${f} has a PageHeader title`)
    for (const t of titles) ok(t === want, `${f}: the title "${t}" is not the menu's "${want}"`)
  }
})

check('no emoji or ▲▼ in the office pages this ship touched: lucide line icons instead', () => {
  const EMOJI = /[\u{1F300}-\u{1FAFF}☀-➿▲▼⏰-⏿]/u
  for (const f of ['pages/OrderDetail.tsx', 'pages/OptOut.tsx', 'pages/Marketing.tsx', 'pages/Agents.tsx', ...TOKEN_FILES, 'components/AppShell.tsx']) {
    const lines = code(read(f)).split('\n')
    // a message body sent to WhatsApp keeps its words as written (an icon cannot travel in it)
    const bad = lines.map((l, i) => [i + 1, l]).filter(([, l]) => EMOJI.test(l) && !/lines\.push\(/.test(l))
    ok(!bad.length, `${f}:${bad.map(([n]) => n).join(',')} ${bad.length ? bad[0][1].trim().slice(0, 80) : ''}`)
  }
})

check('badges have a dark-mode reading: tokens, not light-only hex tints', () => {
  const badge = code(read('components/ui/badge.tsx'))
  ok(!/#[0-9a-fA-F]{3,6}\b/.test(badge), 'badge.tsx holds no hex')
  ok(/dark:|var\(--|bg-(accent|success|warning|destructive|muted|foreground)/.test(badge), 'each tone reads a token or a dark: variant')
})

// cn() itself, run: lib/utils.ts with its two type annotations erased, its packages resolved from web/ (re-rate,
// 30-Oct-2026: the default tailwind-merge took the custom size text-kpi for a colour and dropped it beside the
// tone's colour, so every coloured KPI figure fell back to the body size)
let cn = null
let cnError = ''
try {
  const src = read('lib/utils.ts')
    .replace(/,\s*type ClassValue\b/, '')
    .replace(/:\s*ClassValue\[\]/g, '')
    .replace(/from '([^'.][^']*)'/g, (_, pkg) => `from '${import.meta.resolve(pkg)}'`)
  ;({ cn } = await import(`data:text/javascript,${encodeURIComponent(src)}`))
} catch (e) {
  cnError = e && e.message ? e.message : String(e)
}

check('cn() keeps the custom sizes (text-kpi, text-2xs) beside a colour, and still lets a later size win', () => {
  ok(typeof cn === 'function', `lib/utils.ts loads (${cnError})`)
  const kpi = cn('font-display text-kpi font-bold tabular-nums', 'text-primary').split(' ')
  ok(kpi.includes('text-kpi') && kpi.includes('text-primary'), `a coloured KPI figure keeps its size: "${kpi.join(' ')}"`)
  const tiny = cn('text-2xs', 'text-muted-foreground').split(' ')
  ok(tiny.includes('text-2xs') && tiny.includes('text-muted-foreground'), `a muted caption keeps its size: "${tiny.join(' ')}"`)
  ok(cn('text-sm', 'text-kpi') === 'text-kpi' && cn('text-kpi', 'text-2xs') === 'text-2xs', 'two sizes still merge to the later one')
})

check('the Ctrl K palette is on the brand: no blur under it, no violet, and a search reply that is not a list shows no rows', () => {
  const s = code(read('components/CommandPalette.tsx'))
  ok(!/backdrop-blur/.test(s), 'no backdrop blur under the palette (a per-frame GPU cost on integrated graphics)')
  ok(!/(violet|fuchsia|indigo)-\d/.test(s) && !/rgba\((124,\s*58,\s*237|76,\s*29,\s*149)/.test(s), 'no violet, fuchsia or indigo left in the palette')
  ok(/Array\.isArray\(r\)/.test(s), 'the hits are taken only when /search answers a list')
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
