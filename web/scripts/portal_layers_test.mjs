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

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
