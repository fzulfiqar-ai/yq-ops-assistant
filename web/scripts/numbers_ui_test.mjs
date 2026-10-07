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
 *     for the API's own scope; SIM is only ever named as left out;
 *   * the Command Centre's "Accessories sales" card (R8, web/src/lib/dailySales.ts): the daily chart's
 *     x labels (counted back from the latest day, the month where it changes, sparser on a phone), the
 *     target for one business day, the three facts and the spoken summary taken from the tile as sent,
 *     and the card itself — one heading, a Daily / Weekly control, Daily first.
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

// ── the Command Centre's "Accessories sales" card (R8): the daily chart's pure helpers ──────────────
const D = await load('lib/dailySales.ts')
// the page's source, line ends normalised (a Windows checkout has CRLF): the checks cut it at "\n}\n"
const PAGE = readFileSync(path.join(SRC, 'pages/CommandCentre.tsx'), 'utf8').replace(/\r\n/g, '\n')
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const bhd = (n) => `BHD ${n.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`
/** n calendar days ending on `end`, shaped as the API's sales.daily series (made-up amounts) */
function series(end, values = {}, n = 30) {
  const last = Date.parse(`${end}T00:00:00Z`)
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(last - (n - 1 - i) * 86_400_000)
    const day = d.toISOString().slice(0, 10)
    return {
      day, label: `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`, weekday: WEEKDAYS[d.getUTCDay()],
      value: values[day] ?? 0, invoices: values[day] ? 3 : 0,
      business_day: d.getUTCDay() !== 5 && d.getUTCDay() !== 6, latest: i === n - 1,
    }
  })
}
const DAYS = series('2026-10-06', { '2026-09-29': 900, '2026-10-02': 120, '2026-10-06': 110.5 })

check('daily chart: x labels count back from the latest day; the month on the first label and where it changes', () => {
  eq(Object.entries(D.dayTicks(DAYS, 2)).map(([d, t]) => `${d.slice(5)}=${t}`).join(' '),
    '09-08=8 Sep 09-10=10 09-12=12 09-14=14 09-16=16 09-18=18 09-20=20 09-22=22 09-24=24 09-26=26 09-28=28 09-30=30 '
    + '10-02=2 Oct 10-04=4 10-06=6', 'every 2nd day')
  eq(D.dayTicks(DAYS, 5), { '2026-09-11': '11 Sep', '2026-09-16': '16', '2026-09-21': '21', '2026-09-26': '26',
    '2026-10-01': '1 Oct', '2026-10-06': '6' }, 'every 5th day (a phone)')
  const all = D.dayTicks(DAYS, 1)
  eq([Object.keys(all).length, all['2026-09-07'], all['2026-09-30'], all['2026-10-01'], all['2026-10-06']],
    [30, '7 Sep', '30', '1 Oct', '6'], 'every day')
  // a window inside one month names the month once; a year end is a month change like any other
  eq(Object.values(D.dayTicks(series('2026-10-31'), 5)), ['6 Oct', '11', '16', '21', '26', '31'], 'one month')
  eq(Object.values(D.dayTicks(series('2026-01-05'), 5)), ['11 Dec', '16', '21', '26', '31', '5 Jan'], 'year end')
  // the latest day always carries a label, whatever the step; a junk step labels every day
  for (const step of [1, 2, 3, 4, 5, 6, 7, 9, 30, 45]) ok('2026-10-06' in D.dayTicks(DAYS, step), `latest at step ${step}`)
  eq([Object.keys(D.dayTicks(DAYS, 0)).length, Object.keys(D.dayTicks(DAYS, NaN)).length], [30, 30], 'junk step')
  eq(D.dayTicks([], 2), {}, 'no days')
})

check('daily chart: a label every 2nd day on a desktop card, every 3rd on a narrow one, every 5th on a 360 px phone', () => {
  // plot width = the chart's width less the y axis (44) and the right margin (16)
  eq([D.dayTickStep(593, 30), D.dayTickStep(642, 30)], [2, 2], '1366 and 1440 desktop')
  eq(D.dayTickStep(535, 30), 3, '1280 desktop (the narrower two-column card)')
  eq([D.dayTickStep(234, 30), D.dayTickStep(264, 30)], [5, 5], '360 and 390 phones')
  eq([D.dayTickStep(0, 30), D.dayTickStep(NaN, 30), D.dayTickStep(-5, 30), D.dayTickStep(600, 0)], [5, 5, 5, 5], 'unknown width: sparse')
  eq(D.dayTickStep(2000, 30), 1, 'never below every day')
  // whatever the width, two neighbouring labels get the room one label needs (38 px)
  for (let w = 150; w <= 1400; w += 7) ok(D.dayTickStep(w, 30) * (w / 30) >= 38 - 1e-9, `room at ${w}px`)
})

check('daily chart: the target for one business day is the monthly target over the month’s business days; none without a target', () => {
  // October 2026 has 21 business days (Sun–Thu): BHD 10,000 a month is BHD 476 a business day
  eq(Math.round(D.targetPerBusinessDay(10000, 21)), 476, 'October 2026')
  eq(D.targetPerBusinessDay(10000, 21), 10000 / 21, 'the Dashboard’s rule, unrounded')
  eq([D.targetPerBusinessDay(null, 21), D.targetPerBusinessDay(0, 21), D.targetPerBusinessDay(undefined, 21),
    D.targetPerBusinessDay(10000, 0), D.targetPerBusinessDay(10000, null), D.targetPerBusinessDay(10000)],
  [null, null, null, null, null, null], 'no target or no business days: no line')
})

check('the three facts: the latest day with its weekday, the average business day, the best day with its date', () => {
  const tile = { avg_business_day: 104.926, best: { day: '2026-09-29', label: '29 Sep', value: 900 } }
  eq(D.dailyFacts(DAYS, tile, bhd), [
    { key: 'latest', label: 'Latest day', when: 'Tue 6 Oct', value: 'BHD 110.500' },
    { key: 'average', label: 'Average business day', value: 'BHD 104.926' },
    { key: 'best', label: 'Best day', when: '29 Sep', value: 'BHD 900.000' },
  ], 'facts')
  // the figures are the tile's own, never worked out here: a different average or best is shown as sent
  const sent = D.dailyFacts(DAYS, { avg_business_day: 1.5, best: { day: '2026-10-02', label: '2 Oct', value: 7 } }, bhd)
  eq(sent.map((f) => `${f.when ?? ''}|${f.value}`), ['Tue 6 Oct|BHD 110.500', '|BHD 1.500', '2 Oct|BHD 7.000'], 'as sent')
  // nothing sold in the window: no best day; an average the API did not send is left out, a 0 is shown
  eq(D.dailyFacts(DAYS, { avg_business_day: 0, best: null }, bhd).map((f) => f.key), ['latest', 'average'], 'no best')
  eq(D.dailyFacts(DAYS, {}, bhd).map((f) => f.key), ['latest'], 'no average, no best')
  eq(D.dailyFacts([], tile, bhd).map((f) => f.key), ['average', 'best'], 'no days')
  // the latest day is the one the API flags; an unflagged payload falls back to the last point
  const friday = series('2026-10-02', { '2026-10-02': 120 })
  eq(D.dailyFacts(friday, {}, bhd)[0], { key: 'latest', label: 'Latest day', when: 'Fri 2 Oct', value: 'BHD 120.000' }, 'a weekend day')
  eq(D.latestDay(DAYS.map((p) => ({ ...p, latest: false }))).day, '2026-10-06', 'no flag: the last point')
  eq(D.latestDay([]), undefined, 'no days')
})

check('the chart’s spoken summary reads the latest day, the average and the best day', () => {
  const facts = D.dailyFacts(DAYS, { avg_business_day: 104.926, best: { day: '2026-09-29', label: '29 Sep', value: 900 } }, bhd)
  eq(D.dailySummary(facts, 30), 'Accessories sales by day, ex-VAT, the last 30 days. Latest day, Tue 6 Oct: BHD 110.500. '
    + 'Average business day: BHD 104.926. Best day, 29 Sep: BHD 900.000', 'summary')
  eq(D.dailySummary([], 30), 'Accessories sales by day, ex-VAT, the last 30 days', 'no facts')
})

check('invoice counts in words: "7 invoices", "1 invoice", "no invoices"', () => {
  eq([D.invoicesText(7), D.invoicesText(1), D.invoicesText(0), D.invoicesText(null), D.invoicesText(undefined), D.invoicesText(1200)],
    ['7 invoices', '1 invoice', 'no invoices', 'no invoices', 'no invoices', '1,200 invoices'], 'words')
})

check('the card: one "Accessories sales" heading with a Daily / Weekly control, Daily first; the weekly-only heading only as the fallback', () => {
  const card = PAGE.split('function SalesChartCard(')[1].split('\n}\n')[0]
  ok(card.includes("{both ? 'Accessories sales' : view === 'daily' ? 'Daily Accessories sales' : 'Weekly Accessories sales'}"), 'heading')
  ok(!PAGE.includes('>Weekly Accessories sales</h2>'), 'the old one-chart heading is gone')
  ok(card.includes('role="group"') && card.includes('aria-pressed={view === v.key}'), 'a group of pressed buttons')
  const views = PAGE.split('const CHART_VIEWS')[1].split('\n]\n')[0]
  ok(views.indexOf("label: 'Daily'") > 0 && views.indexOf("label: 'Daily'") < views.indexOf("label: 'Weekly'"), 'Daily first')
  ok(card.includes('ex-VAT · last {span} days · lighter bars are Friday and Saturday'), 'the daily caption')
  ok(PAGE.includes("const WEEKLY_CAPTION = 'ex-VAT · Sunday–Saturday weeks · the lighter bar is this week so far'"), 'the weekly caption, as it was')
  // every figure on the card is read off the tile through the helpers above
  ok(card.includes('dailyFacts(dayPoints(daily), daily, fmtBhd)') && card.includes('dailySummary(facts, span)'), 'facts from the tile')
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
