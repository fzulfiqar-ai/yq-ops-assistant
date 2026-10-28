import { useLayoutEffect, useState, type DependencyList, type RefObject } from 'react'

/**
 * A horizontal scroller that says so. Returns the `mask-image` for `ref` — a soft fade on whichever
 * edge still has content behind it, nothing at all when everything fits (or when `enabled` is false,
 * e.g. the same row wrapping on desktop).
 *
 * Why: the browse chips row and the About "On this page" row slice their last chip mid-word at the
 * viewport edge, so a filtered shelf can look exactly like the whole shelf. The fade is paint only —
 * it never clips a tap target or a focus ring — and it is re-measured on scroll, on resize and
 * whenever `deps` change (a new chip set).
 */
export function useEdgeFade(ref: RefObject<HTMLElement | null>, enabled = true, deps?: DependencyList): string | undefined {
  const [fade, setFade] = useState({ start: false, end: false, rtl: false })
  useLayoutEffect(() => {
    const el = ref.current
    const off = () => setFade((f) => (f.start || f.end ? { ...f, start: false, end: false } : f))
    if (!enabled || !el) {
      off()
      return
    }
    let rtl = getComputedStyle(el).direction === 'rtl'
    const measure = () => {
      const max = el.scrollWidth - el.clientWidth
      // scrollLeft runs negative in RTL (as components/Rail.tsx already reads it): the distance
      // from the INLINE-START edge is its magnitude, so start/end below are logical, not left/right
      const x = Math.abs(el.scrollLeft)
      setFade((f) => {
        const next = { start: x > 8, end: max > 8 && x < max - 8, rtl }
        return f.start === next.start && f.end === next.end && f.rtl === next.rtl ? f : next
      })
    }
    // a swipe fires many scroll events per frame: measure once per frame, and read the direction
    // (a style read) only when the size changes, never mid-swipe
    let frame = 0
    const onScroll = () => {
      if (!frame)
        frame = requestAnimationFrame(() => {
          frame = 0
          measure()
        })
    }
    const onResize = () => {
      rtl = getComputedStyle(el).direction === 'rtl'
      measure()
    }
    measure()
    el.addEventListener('scroll', onScroll, { passive: true })
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(onResize)
    ro?.observe(el)
    return () => {
      el.removeEventListener('scroll', onScroll)
      if (frame) cancelAnimationFrame(frame)
      ro?.disconnect()
    }
    // `deps` is the caller's list, forwarded as-is (the hook's contract); `ref` is a stable ref object
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...(deps || [])])

  if (!fade.start && !fade.end) return undefined
  // `to inline-end` is not shipping in any engine yet, so the logical direction is resolved here
  // instead: the stops are always written start → end and the keyword follows the reading order.
  return `linear-gradient(to ${fade.rtl ? 'left' : 'right'}, ${fade.start ? 'transparent 0px, #000 28px' : '#000 0px'}, ${fade.end ? '#000 calc(100% - 28px), transparent 100%' : '#000 100%'})`
}

/** Scrolls the first pressed/current chip inside `el` into view — the scroller only, never the page. */
export function revealActiveChip(el: HTMLElement | null, selector = '[aria-pressed="true"]') {
  if (!el) return
  const chip = el.querySelector<HTMLElement>(selector)
  if (!chip) return
  const box = el.getBoundingClientRect()
  const r = chip.getBoundingClientRect()
  if (r.left >= box.left + 8 && r.right <= box.right - 8) return
  // the 16px it lands behind sits on the INLINE start, so the chip reads the same way round in both
  const rtl = getComputedStyle(el).direction === 'rtl'
  el.scrollLeft += rtl ? r.right - box.right + 16 : r.left - box.left - 16
}
