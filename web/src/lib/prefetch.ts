import { useEffect, useRef } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { apiGet } from './api'
import { codeFor, type Warmable } from './pageCode'

/**
 * Fetching the next page before it is asked for (ship 2, plan item 13; acceptance S8: a click paints the
 * next page within 150 ms, at most one skeleton).
 *
 *  - hover or touch of a menu item or a tab: that page's code, and its main read when the page has one
 *    listed below (a hover must rest 60 ms, so a mouse crossing the menu does not ask for ten reports;
 *    a press or a keyboard focus asks at once);
 *  - at idle after the shell has painted: the code of every page this login can open, one at a time,
 *    once the page in front of the user has its own answers (code only — the reports' data is warmed
 *    by the shell's own short list, it costs the 0.1-CPU server);
 *  - never on Save-Data or a 2G connection, except for the one page being pointed at.
 *
 * A warm-up that meets a missing chunk (a deploy since this tab opened) does not reload the tab: the
 * reload rule (lib/preloadReload) is for a page the person opens, not for a hover or an idle moment
 * in the middle of their typing. The next real visit to that page reloads as before.
 */

/** a page's main read, fetched with its code on hover or touch: the page's own queryKey and URL, as
 *  its useQuery has them (web/scripts/portal_shell_test.mjs reads each page to hold them equal) */
export const PAGE_DATA: Readonly<Record<string, { key: readonly unknown[]; url: string }>> = {
  '/': { key: ['report', 'dashboard'], url: '/report/dashboard' },
  '/command': { key: ['management', 'overview', 'mtd'], url: '/management/overview?period=mtd' },
  '/command/team': { key: ['management', 'overview', 'mtd'], url: '/management/overview?period=mtd' },
  '/command/customers': { key: ['management', 'overview', 'mtd'], url: '/management/overview?period=mtd' },
  '/inventory': { key: ['report', 'inventory'], url: '/report/inventory' },
  '/sales': { key: ['report', 'sales'], url: '/report/sales' },
  '/margins': { key: ['report', 'margins'], url: '/report/margins' },
  '/receivables': { key: ['report', 'receivables'], url: '/report/receivables' },
  '/catalog': { key: ['catalog'], url: '/catalog' },
  '/orders': { key: ['purchase-orders'], url: '/purchase-orders' },
  '/today': { key: ['shop-today'], url: '/shop/me/today' },
}

/** how long a hover must rest on a link before its page is fetched */
export const HOVER_INTENT_MS = 60
/** the idle warm-up starts this long after the shell mounts */
export const WARM_START_MS = 1500

// ── the reload rule stays out of warm-ups ───────────────────────────────────────────────────────

let warming = 0
if (typeof window !== 'undefined') {
  // A capture listener on the window runs before the reload rule's own (registered at boot, not
  // capturing): while a warm-up is in flight, a missing chunk is that warm-up's failure only.
  window.addEventListener('vite:preloadError', (event) => {
    if (warming > 0) event.stopImmediatePropagation()
  }, { capture: true })
}

async function warm(code: readonly Warmable[]): Promise<void> {
  if (!code.length) return
  warming += 1
  try {
    await Promise.all(code.map((c) => c.preload()))
  } finally {
    warming -= 1
  }
}

/** Fetch the code of the page `to` opens (no-op for a path without code, or when it is already here). */
export function preloadPage(to: string): Promise<void> {
  return warm(codeFor(to))
}

/** The page's main read, once, if it has one listed and nothing fresh is cached (a failure is not retried). */
export function prefetchPageData(qc: QueryClient, to: string): void {
  const path = to.split(/[?#]/, 1)[0] || '/'
  const d = PAGE_DATA[path]
  if (!d) return
  void qc.prefetchQuery({ queryKey: [...d.key], queryFn: () => apiGet<unknown>(d.url), retry: false })
}

/** Save-Data, or a 2G connection: warm only what is pointed at. */
export function lightConnection(conn: { saveData?: boolean; effectiveType?: string } | null | undefined): boolean {
  return Boolean(conn && (conn.saveData || conn.effectiveType === '2g' || conn.effectiveType === 'slow-2g'))
}

function connection(): { saveData?: boolean; effectiveType?: string } | null {
  return (typeof navigator !== 'undefined' && (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection) || null
}

/**
 * The pages to warm at idle, in the order given (the menu's order: its first items are the ones used
 * most), without the page already on screen and without repeats.
 */
export function warmOrder(paths: readonly string[], current: string): string[] {
  const here = current.split(/[?#]/, 1)[0] || '/'
  const seen = new Set<string>([here])
  const out: string[] = []
  for (const p of paths) {
    const path = p.split(/[?#]/, 1)[0] || '/'
    if (seen.has(path) || !codeFor(path).length) continue
    seen.add(path)
    out.push(path)
  }
  return out
}

/** A salesman's landing (lib/auth warmLanding, during /me): Today's code and its read. */
export function warmSalesmanLanding(qc: QueryClient): void {
  void preloadPage('/today')
  prefetchPageData(qc, '/today')
}

type Idle = (cb: () => void) => () => void
const onIdle: Idle = (cb) => {
  const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number; cancelIdleCallback?: (id: number) => void }
  if (w.requestIdleCallback && w.cancelIdleCallback) {
    const id = w.requestIdleCallback(cb, { timeout: 2000 })
    return () => w.cancelIdleCallback!(id)
  }
  const t = setTimeout(cb, 200)
  return () => clearTimeout(t)
}

/**
 * At idle after the shell has painted, the code of `paths` (the pages this login can open), one page at
 * a time, once nothing else is loading (the page in front of the user first, up to ~10 s). Code only.
 */
export function useCodeWarm(paths: readonly string[], current: string, enabled: boolean): void {
  const qc = useQueryClient()
  const list = paths.join('|')
  // the page on screen when the warm-up starts (a later navigation does not restart it)
  const here = useRef(current)
  useEffect(() => {
    if (!enabled || lightConnection(connection())) return
    let stop = false
    let cancel: () => void = () => {}
    let waited = 0
    const queue = warmOrder(list ? list.split('|') : [], here.current)
    const next = () => {
      if (stop) return
      if (qc.isFetching() > 0 && waited < 10_000) {
        waited += 500
        const t = setTimeout(next, 500)
        cancel = () => clearTimeout(t)
        return
      }
      const path = queue.shift()
      if (!path) return
      cancel = onIdle(() => {
        if (stop) return
        void preloadPage(path).then(next)
      })
    }
    // well after the shell's first paint (the page in front is still drawing its first answers then, and
    // his laptop's CPU is busy), then one idle moment per page; a hover warms its page at once meanwhile
    const start = setTimeout(next, WARM_START_MS)
    return () => {
      stop = true
      clearTimeout(start)
      cancel()
    }
  }, [enabled, list, qc])
}

/** The event handlers that warm a link's page: a resting hover, a press, a keyboard focus. */
export interface WarmHandlers {
  onPointerEnter: () => void
  onPointerLeave: () => void
  onPointerDown: () => void
  onFocus: () => void
}

/** Hover, touch or focus on a link warms its page's code and main read (see the top of this file). */
export function useWarmOnIntent(): (to: string) => WarmHandlers {
  const qc = useQueryClient()
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])
  return (to: string) => {
    const now = () => {
      if (timer.current) clearTimeout(timer.current)
      timer.current = null
      void preloadPage(to)
      prefetchPageData(qc, to)
    }
    return {
      onPointerEnter: () => {
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(now, HOVER_INTENT_MS)
      },
      onPointerLeave: () => {
        if (timer.current) clearTimeout(timer.current)
        timer.current = null
      },
      onPointerDown: now,
      onFocus: now,
    }
  }
}
