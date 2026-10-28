import { useEffect, useState } from 'react'
import { useReducedMotion } from 'motion/react'

/**
 * One motion system for the office pages (polish stream, 28-Oct-2026).
 *
 *   * only transform and opacity animate — never width, height, box-shadow or filter;
 *   * 120 ms for a press, 200 ms for a fade (a skeleton giving way to its content), 280 ms for a sheet;
 *   * things enter on cubic-bezier(.22,1,.36,1) and leave on cubic-bezier(.4,0,1,1);
 *   * a chart, a count-up or a stagger moves on its FIRST view in the session only: the charts replayed their
 *     animation on every visit and on every resize, the KPI figures counted up from 0 every time;
 *   * prefers-reduced-motion gets the still version: the final figure, the drawn chart, at once.
 *
 * "The session" is the browser tab: its sessionStorage remembers what was already seen, across a reload; when
 * the storage is blocked (a private window) the tab's memory does the same until the page is reloaded.
 */
export const DURATION = { press: 0.12, fade: 0.2, sheet: 0.28 } as const
export const ENTER_EASE = [0.22, 1, 0.36, 1] as const
export const EXIT_EASE = [0.4, 0, 1, 1] as const

/** Moves at all: only a first view, and only for someone who has not asked the system for less motion. */
export function shouldAnimate(seen: boolean, reduced: boolean): boolean {
  return !seen && !reduced
}

/** Skeleton → content: a fade only when a skeleton was actually on screen (a cached revisit draws at once). */
export function fadeFromSkeleton(hadDataAtMount: boolean, reduced: boolean): boolean {
  return !hadDataAtMount && !reduced
}

type KeyStore = Pick<Storage, 'getItem' | 'setItem'>
const PREFIX = 'yq-seen:'

/** What this tab has already seen: the storage when it answers, the memory always. */
export function viewMemory(store: KeyStore | null | undefined) {
  const local = new Set<string>()
  return {
    seen(key: string): boolean {
      if (local.has(key)) return true
      try {
        return store?.getItem(PREFIX + key) === '1'
      } catch {
        return false
      }
    },
    mark(key: string): void {
      if (local.has(key)) return
      local.add(key)
      try {
        store?.setItem(PREFIX + key, '1')
      } catch {
        /* a blocked storage: the memory above still holds it for this page's life */
      }
    },
  }
}

function sessionStore(): KeyStore | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

const SESSION = viewMemory(sessionStore())

/**
 * true for the whole life of the component that first shows `key` in this tab (and not under reduced motion).
 * Decided once, when the component mounts — call it in the component that draws the moving thing, so the
 * first view is the one the person actually sees, not the page's empty first render.
 */
export function useFirstView(key: string): boolean {
  const reduced = useReducedMotion() ?? false
  const [first] = useState(() => !SESSION.seen(key))
  useEffect(() => {
    SESSION.mark(key)
  }, [key])
  return shouldAnimate(!first, reduced)
}

// ── charts (recharts) ────────────────────────────────────────────────────────────────────────────────

/** a chart redraws once the window has stopped resizing for this long */
export const CHART_RESIZE_DEBOUNCE_MS = 150
/** after its first draw, a chart's series stop animating, so a resize or a new answer redraws at once */
export const CHART_SETTLE_MS = 700

/** The animation props of every series (Bar, Area, Line, Pie) of a chart. */
export function chartSeriesProps(animate: boolean) {
  return {
    isAnimationActive: animate,
    animationDuration: 400,
    animationBegin: 0,
    animationEasing: 'ease-out' as const,
  }
}
export type ChartSeriesMotion = ReturnType<typeof chartSeriesProps>

/** A chart's first view in the session draws in, once; every later draw (a resize, a revisit) is still. */
export function useChartMotion(key: string): { series: ChartSeriesMotion; debounce: number } {
  const first = useFirstView(key)
  const [live, setLive] = useState(first)
  useEffect(() => {
    if (!live) return
    const t = setTimeout(() => setLive(false), CHART_SETTLE_MS)
    return () => clearTimeout(t)
  }, [live])
  return { series: chartSeriesProps(first && live), debounce: CHART_RESIZE_DEBOUNCE_MS }
}

/**
 * Whether a page's content fades in over its skeleton: decided at the page's mount — a page that mounted with
 * its answer already cached draws at once.
 */
export function useSkeletonFade(hasData: boolean): boolean {
  const reduced = useReducedMotion() ?? false
  const [hadData] = useState(hasData)
  return fadeFromSkeleton(hadData, reduced)
}
