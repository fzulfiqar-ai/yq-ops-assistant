import { useSyncExternalStore } from 'react'

/**
 * Which shell to render. Read synchronously from matchMedia so there is no flash of the wrong
 * chrome on first paint.
 *   phone   < 768   floating bottom nav, bottom sheets
 *   tablet  768–1023 top bar, no bottom nav, dialogs
 *   desktop 1024–1279 sticky header + mega-nav, cart as a drawer
 *   wide    ≥ 1280  + persistent mini-cart aside
 */
export type Viewport = 'phone' | 'tablet' | 'desktop' | 'wide'

const QUERIES: [Viewport, string][] = [
  ['wide', '(min-width: 1280px)'],
  ['desktop', '(min-width: 1024px)'],
  ['tablet', '(min-width: 768px)'],
]

function current(): Viewport {
  if (typeof window === 'undefined' || !window.matchMedia) return 'phone'
  for (const [v, q] of QUERIES) if (window.matchMedia(q).matches) return v
  return 'phone'
}

function subscribe(fn: () => void) {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const mqls = QUERIES.map(([, q]) => window.matchMedia(q))
  mqls.forEach((m) => m.addEventListener('change', fn))
  return () => mqls.forEach((m) => m.removeEventListener('change', fn))
}

export function useViewport(): Viewport {
  return useSyncExternalStore(subscribe, current, () => 'phone')
}

export const isPhoneLike = (v: Viewport) => v === 'phone' || v === 'tablet'
export const isDesktopLike = (v: Viewport) => v === 'desktop' || v === 'wide'

/** Hover-capable, fine pointer — the only place pointer tilt and hover reveals are enabled. */
export function useFinePointer(): boolean {
  return useSyncExternalStore(
    (fn) => {
      const m = window.matchMedia('(hover: hover) and (pointer: fine)')
      m.addEventListener('change', fn)
      return () => m.removeEventListener('change', fn)
    },
    () => window.matchMedia('(hover: hover) and (pointer: fine)').matches,
    () => false,
  )
}

export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    (fn) => {
      const m = window.matchMedia('(prefers-reduced-motion: reduce)')
      m.addEventListener('change', fn)
      return () => m.removeEventListener('change', fn)
    },
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    () => false,
  )
}

/**
 * The behavior for a one-off programmatic scroll (a rail arrow, a jump to a field): 'smooth' only
 * when the viewer has not asked for reduced motion. Read at the moment of the scroll, so a
 * component needs no subscription for it; the CSS kill switch (market.css) cannot reach a
 * scrollBy / scrollTo / scrollIntoView call that names its own behavior.
 */
export function scrollMotion(): ScrollBehavior {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
  } catch {
    return 'auto'
  }
}
