/**
 * The portal's layers (28-Oct-2026 polish): what sits above what. No browser: the rules are read
 * from the sources they live in.
 *
 *   node web/scripts/portal_layers_test.mjs        (from the repo root or from web/)
 *
 * Why source rules: the bug was structural. The intro (components/ui/arc-preloader-hero.tsx) wraps
 * the whole portal in `relative isolate`, so every layer rendered INSIDE the app — the toasts'
 * z-[100] included — is trapped below anything portalled to <body>, where the sheets live (z-50).
 * "Order confirmed", "Delivered" and cancel errors were invisible under an open sheet (plan S11).
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')
const read = (p) => readFileSync(path.join(SRC, p), 'utf8')

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

check('toasts render at the body level, above sheets — never inside the intro\'s isolated app (S11)', () => {
  const hero = read('components/ui/arc-preloader-hero.tsx')
  ok(/relative isolate/.test(hero), 'premise: the intro isolates the app (if this changes, re-check the layers)')
  const toast = read('components/Toast.tsx')
  ok(/createPortal\(/.test(toast) && /document\.body/.test(toast), 'components/Toast.tsx portals its layer to document.body')
  const sheet = read('components/ui/sheet.tsx')
  const zSheet = Number((sheet.match(/fixed inset-0 z-(\d+)/) || [])[1])
  const zToast = Number((toast.match(/z-\[(\d+)\]/) || [])[1])
  ok(zSheet > 0 && zToast > zSheet, `the toast layer (z ${zToast}) is above the sheet layer (z ${zSheet})`)
  const undo = read('pages/shop/UndoToast.tsx')
  ok(/createPortal\(/.test(undo) && /z-\[100\]/.test(undo), 'the Undo toast stays portalled at the same height')
})

/* ── ship 2, items 17 and 18 (30-Oct-2026): sheets on lib/sheet, the seven modals, toasts, photos ── */

const SHEET = () => read('components/ui/sheet.tsx')
// the component's own stylesheet (the MOTION template) and its class strings, without comments
const code = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

check('the portal sheet runs on the shared lib/sheet (layers, scroll lock, drag, keyboard, focus)', () => {
  const s = SHEET()
  const imp = (s.match(/import\s*\{([^}]*)\}\s*from\s*'@\/lib\/sheet'/) || [])[1] || ''
  for (const name of ['lockScroll', 'pushLayer', 'useSheetDrag', 'useKeyboardInset', 'initialFocus']) {
    ok(new RegExp(`\\b${name}\\b`).test(imp), `ui/sheet.tsx imports ${name} from @/lib/sheet`)
  }
  ok(!/body\.style\.overflow\s*=/.test(s), 'no hand-rolled body overflow lock (lockScroll trades the Windows scrollbar gutter for padding)')
  ok(/layer\.isTop\(\)/.test(s), 'Escape closes only the top layer')
})

check('a sheet settles without a bounce, under a plain dim, and the drag owns the transform (S6)', () => {
  const s = code(SHEET())
  ok(!/backdrop-blur/.test(s), 'no blur under the dim')
  ok(!/will-change/.test(s), 'no permanent will-change')
  for (const m of s.matchAll(/cubic-bezier\(\s*([\d.]+)\s*,\s*([\d.-]+)\s*,\s*([\d.]+)\s*,\s*([\d.-]+)\s*\)/g)) {
    ok(Number(m[2]) <= 1 && Number(m[4]) <= 1, `no overshooting curve: ${m[0]}`)
  }
  // an entrance that fills `both` keeps its last keyframe over the inline transform the drag writes
  let entrances = 0
  for (const m of s.matchAll(/([^{}]+)\{\s*animation:([^}]*)\}/g)) {
    const sel = m[1].trim()
    if (!/\.yq-panel/.test(sel) || /\.yq-ghost/.test(sel)) continue // the exits (.yq-ghost) fill both
    entrances += 1
    ok(!/\bboth\b/.test(m[2]), `the entrance fills backwards only: ${sel} {${m[2]}}`)
  }
  ok(entrances >= 2, `the panel's entrances were found (${entrances})`)
  ok((s.match(/onPointerDown=\{onPointerDown\}/g) || []).length >= 2, 'the handle and the header both start the drag')
  ok(/attachScroller/.test(s) && /attachPanel/.test(s) && /attachBackdrop/.test(s), 'the panel, the dim and the scroller are handed to the drag')
})

check('every close plays an exit, and reduced motion fades instead of sliding', () => {
  const s = code(SHEET())
  ok(/@keyframes\s+yq-sheet-down/.test(s) && /@keyframes\s+yq-pop-out/.test(s) && /@keyframes\s+yq-drawer-out/.test(s), 'exit keyframes for the phone sheet, the dialog and the drawer')
  ok(/yq-ghost/.test(s) && /cloneNode\(true\)/.test(s), 'the exit runs on a snapshot of the sheet, so a parent that unmounts it still sees it leave')
  // a stack (the keypad over the product sheet): the top one's exit plays above the sheet beneath it,
  // while a sheet opened as it closed rises over the leaving copy
  ok(/Number\(el\.dataset\.sheetSeq\) > seq/.test(s) && /newer \? '49' : '50'/.test(s), 'the leaving copy sits above older sheets and under a newer one')
  ok(/root\.isConnected/.test(s), "StrictMode's rehearsal unmount (development) plays no exit")
  const calm = (s.match(/@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*?)\n\}/) || [])[1] || ''
  ok(/yq-fade-in[^;]*!important/.test(calm) && /yq-fade-out[^;]*!important/.test(calm), 'under reduced motion the panel and its dim fade in and out (opacity only)')
})

const SEVEN = ['ShopOrders', 'Catalog', 'Leads', 'Finds', 'Salesmen', 'PriceTracker', 'ShopRules']
check('the seven hand-made modals are portal sheets now (Esc, focus trap, motion)', () => {
  for (const page of SEVEN) {
    const s = read(`pages/${page}.tsx`)
    ok(/from '@\/components\/ui\/sheet'/.test(s), `${page}.tsx uses components/ui/sheet`)
    ok(!/fixed inset-0/.test(code(s)), `${page}.tsx has no hand-made fixed overlay left`)
    ok(!/BodyPortal/.test(code(s)), `${page}.tsx no longer needs BodyPortal`)
  }
})

check('toasts sit on the bottom stack (tab bar, order bar, Spotted) and step aside from an open sheet', () => {
  const t = read('components/Toast.tsx')
  for (const v of ['--yq-tabbar', '--yq-dock-bottom', '--yq-capture-room', '--yq-dock-right']) ok(t.includes(v), `Toast.tsx reads ${v}`)
  ok(/data-portal-sheet/.test(t), 'Toast.tsx moves its stack while a sheet is open (html[data-portal-sheet])')
  ok(/html\.dataset\.portalSheet = ''/.test(code(SHEET())), 'ui/sheet.tsx marks <html data-portal-sheet> while a sheet is open')
})

check('photo fade-in: the tile wears the state, flipped by the image itself (no React render per photo)', () => {
  const s = read('pages/shop/ProductImage.tsx')
  ok(/data-state="wait"/.test(s), 'the tile starts in data-state="wait"')
  ok(/dataset\.state\s*=\s*'in'/.test(s), 'the load event flips dataset.state (the DOM, not React state)')
  ok(/\.complete\b/.test(s) && /naturalWidth/.test(s), 'a photo already in the cache shows at once')
  ok(/dataset\.instant/.test(s), 'a photo decoded mid-scroll shows at once')
})

check('every Orders table scrolls sideways inside its card on a phone, with the edge hint', () => {
  const s = code(read('pages/Orders.tsx'))
  const tables = (s.match(/<table\b/g) || []).length
  const scrolls = (s.match(/<ScrollX\b/g) || []).length
  ok(tables > 0 && scrolls >= tables, `${scrolls} ScrollX for ${tables} tables`)
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
