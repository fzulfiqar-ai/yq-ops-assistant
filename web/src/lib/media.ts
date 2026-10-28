import { useCallback, useSyncExternalStore } from 'react'

/**
 * A CSS media query as a live boolean, read synchronously from matchMedia (so the first paint is already the
 * right layout) and kept current as the window changes. `fallback` answers where there is no matchMedia.
 */
export function useMediaQuery(query: string, fallback = false): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    if (typeof window === 'undefined' || !window.matchMedia) return () => {}
    const m = window.matchMedia(query)
    m.addEventListener('change', onChange)
    return () => m.removeEventListener('change', onChange)
  }, [query])
  return useSyncExternalStore(
    subscribe,
    () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : fallback),
    () => fallback,
  )
}

/** md: and wider — DataTable draws its table; narrower, its cards */
export const WIDE_QUERY = '(min-width: 768px)'
/** a mouse (or trackpad) — only there may a long table be its own vertical scroller; on touch it is one page */
export const FINE_POINTER_QUERY = '(hover: hover) and (pointer: fine)'
