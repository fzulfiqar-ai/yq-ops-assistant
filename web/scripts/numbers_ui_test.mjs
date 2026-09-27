/**
 * Portal number wording (release-candidate review, stream NUMBERS) — the pure helpers in
 * web/src/lib/basisText.ts, exercised in plain node: no test runner, no new deps.
 *
 *   node web/scripts/numbers_ui_test.mjs        (from the repo root or from web/)
 *
 * The .ts source is transpiled on load with the project's own `typescript` (nothing compiled to disk).
 * Exit 1 on any failure. All inputs are synthetic.
 *
 * What it proves:
 *   * deadUncostedNote — the dead-stock tile's item count and its at-cost value never describe
 *     different items silently: whenever some dead items have no usable cost the note says how many
 *     of how many are left out of the value; nothing when every item is costed;
 *   * the VAT basis words Shop analytics and the merchant page put beside marketplace money.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import ts from 'typescript'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')
const load = async (rel) => {
  const file = path.join(SRC, rel)
  const { outputText } = ts.transpileModule(readFileSync(file, 'utf8'), {
    fileName: file,
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  })
  return import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'))
}
const B = await load('lib/basisText.ts')

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
function eq(a, b, what = '') {
  const A = JSON.stringify(a)
  const B2 = JSON.stringify(b)
  if (A !== B2) throw new Error(`${what} expected ${B2}, got ${A}`)
}
function ok(v, what = '') {
  if (!v) throw new Error(`${what} expected truthy`)
}

check('dead stock: 5 of 12 dead items uncosted — the note says so beside the at-cost value', () => {
  eq(B.deadUncostedNote(12, 5), ' · 5 of 12 without a usable cost, left out of the value', 'note')
})

check('dead stock: every dead item costed (or none dead) — no note at all', () => {
  eq(B.deadUncostedNote(12, 0), '', 'all costed')
  eq(B.deadUncostedNote(0, 0), '', 'none dead')
  eq(B.deadUncostedNote(7, null), '', 'older API without the field')
  eq(B.deadUncostedNote(7, undefined), '', 'undefined')
})

check('dead stock: the "of N" never reads fewer than the uncosted count; thousands grouped', () => {
  eq(B.deadUncostedNote(null, 2), ' · 2 of 2 without a usable cost, left out of the value', 'count missing')
  eq(B.deadUncostedNote(1200, 1100), ' · 1,100 of 1,200 without a usable cost, left out of the value', 'grouped')
})

check('shop analytics money: the VAT basis is named, and the Command Centre difference explained', () => {
  eq(B.VAT_LABEL, 'incl. VAT', 'label')
  ok(B.MONEY_NOTE.includes('incl. VAT') && B.MONEY_NOTE.includes('ex-VAT'), 'note names both bases')
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
