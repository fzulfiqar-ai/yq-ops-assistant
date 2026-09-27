/**
 * R5 Arabic, exercised on the real modules — plain node, no test runner, no new deps.
 *
 *   node web/scripts/arabic_test.mjs        (from the repo root or from web/)
 *
 * Same loader as soldout_order_test.mjs: the TypeScript sources are transpiled on load with the
 * project's own `typescript` (module.registerHooks, Node 22.15+). Exit 1 on any failure.
 * tests/test_r7b_arabic.py runs this script and reports its result (it SKIPS when node or
 * web/node_modules is missing); the CI web job should run it after `npm ci`, beside the sold-out one.
 *
 * What it proves: every English key has an Arabic twin of the same shape that renders Arabic with
 * Western digits (tsc proves the keys; this proves the values); the owner's wording rules hold in
 * Arabic; the language rule (?lang → saved → the phone); money in both languages; quick paste reads
 * Arabic-Indic digits, «،» «؛» and «حبة / قطعة / درزن»; Arabic search words reach the English
 * catalog; English output is unchanged by all of it.
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

const mod = (rel) => import(pathToFileURL(path.join(SRC, rel)).href)
const { en } = await mod('market/i18n/en.ts')
const { ar, pa } = await mod('market/i18n/ar.ts')
const i18n = await mod('market/i18n/index.ts')
const { S, locale } = await mod('market/strings.ts')
const format = await mod('market/lib/format.ts')
const quick = await mod('market/lib/quickParse.ts')
const search = await mod('market/lib/search.ts')

/* ── the runner ── */
const TESTS = []
const test = (name, fn) => TESTS.push([name, fn])
const assert = (cond, msg) => {
  if (!cond) throw new Error(msg)
}
const eq = (a, b, msg) => assert(JSON.stringify(a) === JSON.stringify(b), `${msg}: got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`)

const ARABIC = /[\u0600-\u06ff]/
const ARABIC_DIGITS = /[\u0660-\u0669\u06f0-\u06f9]/
/** keys whose Arabic is deliberately the English: names and marks, not copy */
const SAME_OK = new Set(['brand', 'company', 'search.shortcut', 'footer.company', 'lang.english', 'lang.arabic', 'home.pasteExample', 'search.keys'])
/** an array's items share their key's rule */
const same = (where) => SAME_OK.has(where.replace(/\[\d+\]$/, ''))
/** sample arguments by parameter name (the transpiled source keeps the names) */
const SAMPLE = { n: 12, i: 2, u: 60, qty: 12, more: 3, pct: 41, step: 6, value: 12, d: 3, h: 4, m: 25, total: '12.000 د.ب' }

function argsFor(fn) {
  const head = String(fn).split('=>')[0]
  const names = (head.match(/\(([^)]*)\)/)?.[1] || head).split(',').map((s) => s.replace(/[=].*$/, '').trim()).filter(Boolean)
  return names.map((nm) => (nm in SAMPLE ? SAMPLE[nm] : nm === 'date' ? '12 سبتمبر' : 'UK15'))
}

/** walk en and ar together: same shape, and every Arabic leaf renders Arabic, Western digits only */
function walk(e, a, where, out) {
  if (typeof e === 'function') {
    assert(typeof a === 'function', `${where}: English is a function, Arabic is ${typeof a}`)
    const args = argsFor(e)
    const ea = String(e(...args))
    const aa = String(a(...args))
    out.push([where, aa])
    assert(aa.trim() && !/undefined|NaN|\[object/.test(aa), `${where}(${args}) renders ${JSON.stringify(aa)}`)
    assert(same(where) || ARABIC.test(aa) || !/[a-z]{3,}/i.test(ea), `${where}: the Arabic is not Arabic: ${JSON.stringify(aa)}`)
    return
  }
  if (Array.isArray(e)) {
    assert(Array.isArray(a) && a.length === e.length, `${where}: arrays differ (${e.length} vs ${a && a.length})`)
    e.forEach((x, k) => walk(x, a[k], `${where}[${k}]`, out))
    return
  }
  if (where === 'upcoming') {
    // the WEKOME block is bilingual already (upcoming.en / upcoming.ar) and shared by both files
    assert(a === e, 'upcoming: ar.ts must share en.upcoming')
    return
  }
  if (e && typeof e === 'object') {
    assert(a && typeof a === 'object', `${where}: missing in Arabic`)
    // an empty English map (categoryNames, areaNames) is a map the Arabic fills
    for (const k of Object.keys(e)) walk(e[k], a[k], where ? `${where}.${k}` : k, out)
    return
  }
  assert(typeof a === typeof e, `${where}: ${typeof e} in English, ${typeof a} in Arabic`)
  out.push([where, String(a)])
  if (typeof e === 'string' && !same(where) && /[a-z]{3,}/i.test(e.replace(/YQ|VFAN|WhatsApp|Safari|Esc|Enter|Type-C|Lightning|Micro USB|USB-A|USB|AUX|FM|TWS|mAh|W\b/g, ''))) {
    assert(ARABIC.test(a), `${where}: not translated: ${JSON.stringify(a)}`)
  }
}

test('ar mirrors en: every key, the same shape, Arabic text, Western digits only', () => {
  const out = []
  walk(en, ar, '', out)
  assert(out.length > 400, 'walked only ' + out.length + ' leaves')
  for (const [where, text] of out) assert(!ARABIC_DIGITS.test(text), `${where}: Arabic-Indic digits in ${JSON.stringify(text)}`)
})

test('tsc: an English key the Arabic lacks is a type error (ar is declared `Strings`)', () => {
  const arPath = path.join(SRC, 'market', 'i18n', 'ar.ts')
  const original = readFileSync(arPath, 'utf8')
  const broken = original.replace(/^ {2}searchLabel: '[^']*',\r?\n/m, '')   // CRLF on a Windows checkout
  assert(broken !== original, 'could not take searchLabel out of ar.ts')
  const opts = { noEmit: true, skipLibCheck: true, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler, lib: ['lib.es2023.d.ts', 'lib.dom.d.ts'], types: [] }
  const check = (text) => {
    const host = ts.createCompilerHost(opts)
    const getSource = host.getSourceFile
    host.getSourceFile = (f, lang, ...rest) => (path.resolve(f) === path.resolve(arPath) ? ts.createSourceFile(f, text, lang) : getSource(f, lang, ...rest))
    const program = ts.createProgram([arPath], opts, host)
    return ts.getPreEmitDiagnostics(program).map((d) => ts.flattenDiagnosticMessageText(d.messageText, '\n'))
  }
  const clean = check(original)
  assert(clean.length === 0, 'ar.ts does not type-check on its own: ' + clean.slice(0, 2).join(' | '))
  const errs = check(broken)
  assert(errs.some((d) => d.includes('searchLabel')), 'a missing key compiled: ' + errs.slice(0, 2).join(' | '))
})

test('owner wording: «نفدت الكمية» for sold out, no "out of stock" in either language, no tier word', () => {
  eq(ar.card.soldOut, 'نفدت الكمية', 'ar.card.soldOut')
  eq(ar.card.stockOut, 'نفدت الكمية', 'ar.card.stockOut')
  eq(ar.card.stockOutAr, 'نفدت الكمية', 'ar.card.stockOutAr')
  const all = JSON.stringify(ar, (k, v) => (typeof v === 'function' ? String(v) : v))
  for (const bad of ['غير متوفر في المخزون', 'نفد من المخزون', 'بريميوم', 'أرقى', 'فاخر', 'sub-premium', 'Sub-Premium']) assert(!all.includes(bad), `ar.ts carries ${bad}`)
  assert(ar.upcoming === en.upcoming, 'the WEKOME block is one bilingual block, shared')
})

test('pa(): counted nouns with Western digits — one / few (3–10) / many (11–99)', () => {
  eq(pa(1, 'منتج', 'منتجات', 'منتجًا'), '1 منتج', 'one')
  eq(pa(2, 'منتج', 'منتجات', 'منتجًا'), '2 منتج', 'two')
  eq(pa(5, 'منتج', 'منتجات', 'منتجًا'), '5 منتجات', 'few')
  eq(pa(12, 'منتج', 'منتجات', 'منتجًا'), '12 منتجًا', 'many')
  eq(pa(100, 'منتج', 'منتجات', 'منتجًا'), '100 منتج', 'hundred')
  eq(pa(11, 'قطعة', 'قطع'), '11 قطعة', 'many falls back to the singular')
})

test('language rule: ?lang= → the saved choice → the phone\'s FIRST language; English by default', () => {
  eq(i18n.pickLang('?lang=ar', null, ['en-US']), 'ar', '?lang=ar')
  eq(i18n.pickLang('?lang=en', 'ar', ['ar-BH']), 'en', '?lang=en beats the saved choice and the phone')
  eq(i18n.pickLang('', 'ar', ['en-US']), 'ar', 'saved ar')
  eq(i18n.pickLang('', null, ['ar-BH', 'en']), 'ar', 'an Arabic phone')
  eq(i18n.pickLang('', null, ['en-US', 'ar']), 'en', 'Arabic second is not Arabic first')
  eq(i18n.pickLang('?lang=fr', 'xx', []), 'en', 'nonsense falls back to English')
  eq(i18n.LANG_KEY, 'yq-lang', 'the storage key')
  // the boot script mirrors the same rule and key (public/market-lang.js)
  const boot = readFileSync(path.join(SRC, '..', 'public', 'market-lang.js'), 'utf8')
  assert(boot.includes("var KEY = 'yq-lang'") && boot.includes("root.setAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')"), 'market-lang.js drifted from i18n/index.ts')
  // node has no document: English, and strings.ts re-exports the same object
  eq(locale, { lang: 'en', dir: 'ltr' }, 'locale in node')
  assert(S === en, 'strings.ts S is the English object')
})

test('money: 3 decimals, Western digits, never grouped; «د.ب» after the amount in Arabic', () => {
  eq(i18n.moneyText(1234.5, 'ar'), '1234.500', 'ar 1234.5')
  eq(i18n.moneyText(0.1 + 0.2, 'ar'), '0.300', 'ar float noise')
  eq(i18n.moneyText(1234.5, 'en'), '1234.500', 'en 1234.5')
  eq(i18n.withCurrency('1.000', 'ar'), '1.000 د.ب', 'ar currency')
  eq(i18n.withCurrency('1.000', 'en'), 'BHD 1.000', 'en currency')
  // English output is exactly what it was before R5
  for (const v of [0, 1, 0.4, 1.9, 12.345, 20, 1234.5, 0.0005, 2.675, 99999.9999]) eq(format.money(v), Number(v).toFixed(3), 'money(' + v + ')')
  eq(format.bhd(1.9), 'BHD 1.900', 'bhd')
  eq(format.bhdRound(20), 'BHD 20', 'bhdRound integer')
  eq(format.bhdRound(20.5), 'BHD 20.500', 'bhdRound decimal')
  eq(format.statusLabel('packed', 'Preparing'), 'Preparing', 'English keeps the API label')
  eq(format.proofText('Ordered by 12 shops in the last 30 days'), 'Ordered by 12 shops in the last 30 days', 'English keeps the API sentence')
  eq(format.niceCategory('BLUETOOTH HEADSET'), 'Bluetooth headset', 'English category')
  eq(format.countdownLabel(new Date(Date.now() + (2 * 1440 + 185) * 60000).toISOString()), '2d 3h', 'English countdown')
  // the Arabic forms of the same helpers
  eq(ar.status.packed, 'قيد التجهيز', 'ar status')
  eq(ar.proof.shops(12), 'طلبه 12 محلًا خلال آخر 30 يومًا', 'ar social proof')
  eq(ar.categoryNames['CAR CHARGER'], 'شواحن سيارة', 'ar category')
  eq(ar.areaNames.Manama, 'المنامة', 'ar area')
  eq(ar.countdown.daysHours(2, 3), '2 يوم و3 ساعات', 'ar countdown')
})

test('isolation: English runs inside Arabic sentences are LTR islands (LRI … PDI)', () => {
  const LRI = '\u2066'
  const PDI = '\u2069'
  eq(ar.card.lineTotal(5, '1.000', '5.000 د.ب'), `${LRI}5 × 1.000${PDI} · 5.000 د.ب`, 'qty × price')
  eq(ar.track.order('YQ-2609-0012'), `الطلب ${LRI}YQ-2609-0012${PDI}`, 'order number')
  eq(ar.card.tier(12, '0.400 د.ب'), `${LRI}12+${PDI} ← 0.400 د.ب`, 'a price break, its arrow pointing along the line')
  assert(ar.card.removed('20W Charger (VFAN)').includes(`${LRI}20W Charger (VFAN)${PDI}`), 'a product name in a toast')
  // what the merchant sends the representative: Arabic, then the English line, codes intact
  const msg = ar.card.tellBackText('Sam', 'UK15', '20W Charger')
  assert(msg.includes('\n' + en.card.tellBackText('Sam', 'UK15', '20W Charger')) && msg.includes('UK15'), 'the rep message keeps its English line')
  // English never carries an isolate
  eq(i18n.ltr('UK15'), 'UK15', 'ltr() is a no-op in English')
})

test('quick paste: Arabic-Indic digits, «،» «؛», direction marks, حبة / قطعة / درزن', () => {
  eq(quick.splitList('24 x C18، 12 UK15؛ tws 6'), ['24 x C18', '12 UK15', 'tws 6'], 'Arabic comma and semicolon split lines')
  eq(quick.splitList('\u200f٢٤ x C18\n١٢ UK15'), ['24 x C18', '12 UK15'], 'digits and marks')
  eq(quick.parseToken('٢٤ x C18'), { query: 'C18', qty: 24 }, 'Arabic-Indic qty first')
  eq(quick.parseToken('12 حبة UK15'), { query: 'UK15', qty: 12 }, 'حبة before the code')
  eq(quick.parseToken('12 حبه UK15'), { query: 'UK15', qty: 12 }, 'حبه spelling')
  eq(quick.parseToken('UK15 ٣ قطع'), { query: 'UK15', qty: 3 }, 'قطع after the code')
  eq(quick.parseToken('UK15 5 قطعة'), { query: 'UK15', qty: 5 }, 'قطعة after the code')
  eq(quick.parseToken('2 درزن C18'), { query: 'C18', qty: 24 }, 'a dozen is 12')
  eq(quick.parseToken('C18 x ۲ درزن'), { query: 'C18', qty: 24 }, 'Extended Arabic-Indic digits, dozen after')
  // English unchanged
  eq(quick.parseToken('24 x C18'), { query: 'C18', qty: 24 }, 'en qty first')
  eq(quick.parseToken('C18 24'), { query: 'C18', qty: 24 }, 'en qty last')
  eq(quick.parseToken('X26-C'), { query: 'X26-C', qty: null }, 'a hyphenated code is not a quantity')
  eq(quick.parseToken('UK15'), { query: 'UK15', qty: null }, 'a bare code')
  eq(quick.parseToken('tws 6 pcs'), { query: 'tws', qty: 6 }, 'pcs after')
  eq(format.codeKey('uk١٥'), 'UK15', 'codeKey reads Arabic digits')
  eq(format.codeKey('\u200fUK-15'), 'UK15', 'codeKey drops a direction mark')
})

test('search: Arabic words reach the English catalog; English queries pass through untouched', () => {
  eq(search.queryText('شاحن سيارة'), 'car charger', 'car charger')
  eq(search.queryText('كيبل ايفون'), 'cable iphone', 'cable iphone')
  eq(search.queryText('كيبل آيفون'), 'cable iphone', 'آ folds to ا')
  eq(search.queryText('باور بانك'), 'power bank', 'power bank')
  eq(search.queryText('شاحن ٢٠ واط'), 'charger 20w', 'digits and watts')
  eq(search.queryText('الشاحن'), 'charger', 'the article')
  eq(search.queryText('كيبل Type-C'), 'cable type-c', 'mixed')
  eq(search.queryText('كلمة غريبة'), '', 'unknown Arabic finds nothing')
  eq(search.queryText('Type-C cable'), 'Type-C cable', 'English untouched')
  // every Arabic search hint the band rotates finds something in this small catalog's words
  const item = (code, name, category) => ({ item_code: code, display_name: name, spec: name, category, brand: 'VFAN', price_bhd: 1, stock_status: 'in_stock', badges: [], tiers: [] })
  const items = [
    item('C01', 'Type-C cable 1m', 'CABLE'),
    item('L01', 'Lightning cable iPhone 1m', 'CABLE'),
    item('UK15', '20W charger', 'CHARGER'),
    item('CC1', 'Car charger dual USB', 'CAR CHARGER'),
    item('PB1', '10000mAh power bank', 'POWER BANK'),
    item('T10', 'TWS earbuds', 'BLUETOOTH HEADSET'),
  ]
  const index = search.buildIndex(items)
  const hit = (q) => search.searchItems(index, items, q).map((i) => i.item_code)
  assert(hit('شاحن سيارة')[0] === 'CC1', 'شاحن سيارة → ' + hit('شاحن سيارة'))
  assert(hit('باور بانك').includes('PB1'), 'باور بانك → ' + hit('باور بانك'))
  assert(hit('كيبل آيفون')[0] === 'L01', 'كيبل آيفون → ' + hit('كيبل آيفون'))
  for (const h of ar.search.hints) assert(hit(h).length > 0, `the Arabic hint «${h}» finds nothing`)
  eq(hit('كلمة غريبة'), [], 'unknown Arabic finds nothing')
})

test('facets: every label comes from the locale (none hard-coded in lib/facets.ts)', () => {
  const src = readFileSync(path.join(SRC, 'market', 'lib', 'facets.ts'), 'utf8')
  assert(!/label: '/.test(src), 'a literal facet label is back in facets.ts')
  eq(Object.keys(ar.facets).sort(), Object.keys(en.facets).sort(), 'facet keys')
})

let passed = 0
let failed = 0
for (const [name, fn] of TESTS) {
  try {
    await fn()
    console.log(`  PASS  ${name}`)
    passed++
  } catch (err) {
    console.log(`  FAIL  ${name}\n        ${err && err.message}`)
    failed++
  }
}
console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
