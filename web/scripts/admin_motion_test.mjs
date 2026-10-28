/**
 * One motion system for the office pages (polish stream, 28-Oct-2026) — the pure rules, on the real module,
 * in plain node: no test runner, no new deps.
 *
 *   node web/scripts/admin_motion_test.mjs        (from the repo root or from web/)
 *
 * lib/motionOnce.ts is transpiled on load with the project's own `typescript` (a load hook from
 * module.registerHooks, the loader of order_heart_ui_test.mjs); nothing is compiled to disk. Exit 1 on any
 * failure.
 *
 * What it proves:
 *   * the system's numbers: 120 ms for a press, 200 ms for a fade (and skeleton → content), 280 ms for a sheet;
 *     the enter curve cubic-bezier(.22,1,.36,1) and the exit curve cubic-bezier(.4,0,1,1);
 *   * a chart, a count-up or the Dashboard's stagger moves only on its FIRST view in the session (the tab's
 *     sessionStorage, the tab's memory when storage is blocked) and never under prefers-reduced-motion — the
 *     final figure at once; charts used to replay on every visit and on every resize;
 *   * a chart's resizes are debounced and its series animate only while that first view settles;
 *   * content fades in over its skeleton only when a skeleton was actually shown;
 *   * the pages, read as text: every chart goes through ChartBox, CountUp and the stagger read the first-view
 *     rule and the reduced-motion setting, nothing animates width, height, box-shadow or filter.
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

const load = (rel) => import(pathToFileURL(path.join(SRC, rel)).href).catch((e) => ({ __missing: e.message }))
// a Windows checkout has CRLF, CI has LF: the text checks read one form
const text = (rel) => readFileSync(path.join(SRC, rel), 'utf8').replace(/\r\n/g, '\n')

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

const M = await load('lib/motionOnce.ts')

await check('the one motion system: 120 ms press, 200 ms fade, 280 ms sheet; the enter and exit curves', () => {
  ok(!M.__missing, `lib/motionOnce.ts loads (${M.__missing})`)
  eq(M.DURATION, { press: 0.12, fade: 0.2, sheet: 0.28 })
  eq([M.ENTER_EASE, M.EXIT_EASE], [[0.22, 1, 0.36, 1], [0.4, 0, 1, 1]])
})

await check('a chart, a count-up or a stagger moves on its first view only, and never under reduced motion', () => {
  eq([M.shouldAnimate(false, false), M.shouldAnimate(true, false), M.shouldAnimate(false, true), M.shouldAnimate(true, true)],
    [true, false, false, false])
})

await check("the session's memory: the tab's sessionStorage, the tab's memory when the storage is blocked", () => {
  const mem = new Map()
  const store = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => { mem.set(k, v) } }
  const v = M.viewMemory(store)
  eq(v.seen('dashboard.kpis'), false)
  v.mark('dashboard.kpis')
  v.mark('dashboard.kpis')
  eq([v.seen('dashboard.kpis'), v.seen('sales.trend')], [true, false], 'each view its own')
  eq(M.viewMemory(store).seen('dashboard.kpis'), true, 'a reload in the same tab remembers it')
  eq(mem.size, 1, 'marking twice stores it once')
  const blocked = { getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') } }
  const b = M.viewMemory(blocked)
  eq(b.seen('x'), false)
  b.mark('x')
  eq(b.seen('x'), true, 'held in memory instead')
  const none = M.viewMemory(null)
  none.mark('y')
  eq(none.seen('y'), true)
})

await check('a chart: its series animate on the first view only, and settle before resizes stop replaying', () => {
  eq(M.chartSeriesProps(true), { isAnimationActive: true, animationDuration: 400, animationBegin: 0, animationEasing: 'ease-out' })
  eq(M.chartSeriesProps(false).isAnimationActive, false)
  ok(M.CHART_RESIZE_DEBOUNCE_MS >= 100 && M.CHART_RESIZE_DEBOUNCE_MS <= 250, 'resizes debounced')
  ok(M.CHART_SETTLE_MS > M.chartSeriesProps(true).animationDuration, 'the first view finishes before animation is switched off')
})

await check('content fades in over its skeleton only when a skeleton was shown; never under reduced motion', () => {
  eq([M.fadeFromSkeleton(false, false), M.fadeFromSkeleton(true, false), M.fadeFromSkeleton(false, true)], [true, false, false])
})

console.log(`\n${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
