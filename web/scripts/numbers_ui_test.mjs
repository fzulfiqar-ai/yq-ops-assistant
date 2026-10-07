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
 *   * the VAT basis words Shop analytics and the merchant page put beside marketplace money;
 *   * the Dashboard is Mobile Accessories only (R8, owner 7-Oct-2026) — read as source text, nothing is
 *     rendered: the page never reads or prints the every-division split again (by_payment / by_division,
 *     the "every division" caption, the all-division day total); its chips are Accessories ex-VAT with the
 *     invoices on hover and a free-units chip only above 0; the scope pill and the Accessories labels wait
 *     for the API's own scope; SIM is only ever named as left out.
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

// ── the Dashboard page, as text (a Windows checkout has CRLF, CI has LF: one form here) ──────────────
const DASH_FILE = 'pages/Dashboard.tsx'
const DASH = readFileSync(path.join(SRC, DASH_FILE), 'utf8').replace(/\r\n/g, '\n')
const CHIPS_CAPTION = 'Month to date · Accessories · ex-VAT'

check('dashboard: Mobile Accessories only — the every-division split is never read or rendered again', () => {
  for (const gone of ['by_payment', 'by_division', 'PaymentRow', 'DivisionRow']) {
    ok(!DASH.includes(gone), `${DASH_FILE} without "${gone}"`)
  }
  ok(!/every[\s-]+division/i.test(DASH), `${DASH_FILE} without the words "every division"`)
  ok(!DASH.includes('gross, VAT-incl'), 'no VAT-inclusive chips caption')
  // the daily bars and the running total never fall back to the all-division day total
  ok(!DASH.includes('gross_bhd'), `${DASH_FILE} without the all-division "gross_bhd"`)
  // no JSX prints a division name out of the payload (the old chips did: {d.division})
  ok(!/\{\s*[\w.?]+\.division\s*\}/.test(DASH), 'no {x.division} rendered')
})

check('dashboard: the chips are Accessories ex-VAT, invoices on hover; the free-units chip only above 0', () => {
  eq(DASH.split(CHIPS_CAPTION).length - 1, 1, 'the chips caption, once:')
  const row = DASH.split(CHIPS_CAPTION)[1].split('</Card>')[0]
  ok(DASH.includes('const split = data?.payment_split_acc ?? null'), 'the chips read payment_split_acc')
  ok(row.includes('split.rows.map((p) => ('), 'one chip per sale type')
  ok(row.includes('{bhd(p.net_bhd, 0)}'), 'the ex-VAT amount')
  ok(row.includes("title={`${num(p.invoices)} invoice${p.invoices === 1 ? '' : 's'}"), 'the invoices on hover')
  ok(row.includes('{split.giveaway_qty > 0 && ('), 'free units only when above 0')
  ok(row.includes("free unit{split.giveaway_qty === 1 ? '' : 's'}"), '"N free units"')
  ok(!/division|revenue_bhd|SIM/.test(row), 'no division chip, no VAT-inclusive amount, no SIM in the chips row')
  // the row is drawn only from the Accessories split: a failed read or an older API shows no chips at all
  const guard = DASH.slice(DASH.indexOf(CHIPS_CAPTION) - 600, DASH.indexOf(CHIPS_CAPTION))
  ok(guard.includes('{split && (split.rows.length > 0 || split.giveaway_qty > 0) && ('), 'the row needs the split')
})

check('dashboard: the scope pill and the Accessories labels wait for the API to state the scope', () => {
  ok(DASH.includes("const accOnly = data?.scope?.division === 'Accessories'"), 'accOnly comes from the API scope')
  const header = DASH.split('<PageHeader title={greeting}')[1].split('/>\n\n')[0]
  ok(header.includes('actions={accOnly ? ('), 'the pill only when the API says Accessories')
  ok(header.includes('title={data?.scope?.text}'), "the API's sentence on hover")
  ok(header.includes('Mobile Accessories only'), 'the pill text')
  ok(DASH.includes("label={accOnly ? 'Low-stock items · Accessories' : 'Low-stock items'}"), 'the low-stock tile label')
  ok(DASH.includes("{accOnly ? 'Accessories items' : 'items'} under 30 days cover"), 'the alerts strip')
  // receivables are the one figure that is not Accessories only, and its hover says so
  ok(DASH.includes("'every customer balance in Focus, not split by product'"), 'the receivables hover')
})

check('dashboard: SIM is only ever named as left out, never shown as a figure', () => {
  for (const m of DASH.matchAll(/\bSIM\b/g)) {
    const near = DASH.slice(Math.max(0, m.index - 60), m.index + 60).replace(/\s+/g, ' ')
    ok(/excluded|never counts/.test(near), `SIM named as left out near "…${near.slice(30, 90)}…"`)
  }
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
