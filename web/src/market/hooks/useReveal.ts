import { useLayoutEffect, type DependencyList, type RefObject } from 'react'

/**
 * Scroll reveal for `.reveal` nodes inside `ref` (CSS: the "v3: reveal" block in market.css).
 *
 * Content is never hidden unless it is safe to show it again:
 * - `html[data-reveal]` (the CSS gate) is set only when IntersectionObserver exists, and the CSS
 *   hides nothing under prefers-reduced-motion. Under reduced motion the hook also marks every
 *   node `.is-in` at once and observes nothing (double gate).
 * - In a layout effect — before the browser paints — nodes already in (or above) the viewport are
 *   marked `.is-in` synchronously, so they paint visible with no fade (no phase-2 flash, no LCP
 *   delay). Only the rest are observed, by ONE module-level observer, and revealed on entry.
 *
 * Rules for consumers: every `.reveal` must live inside an element passed to this hook; keep the
 * className of a `.reveal` node stable (React rewriting it drops `.is-in`); pass `deps` that change
 * when new `.reveal` nodes render (omit them to rescan after every render). Stagger with
 * `style={{ ['--i' as string]: index }}`.
 */

const PENDING = '.reveal:not(.is-in)'

let observer: IntersectionObserver | null = null

function sharedObserver(): IntersectionObserver {
  if (!observer) {
    observer = new IntersectionObserver(
      (entries, io) => {
        for (const entry of entries) {
          // entering, or already scrolled past (a jump) — either way the reader should see it
          if (entry.isIntersecting || entry.boundingClientRect.bottom <= 0) {
            entry.target.classList.add('is-in')
            io.unobserve(entry.target)
          }
        }
      },
      { rootMargin: '0px 0px -8% 0px', threshold: 0 },
    )
  }
  return observer
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

export function useReveal(ref: RefObject<HTMLElement | null>, deps?: DependencyList): void {
  useLayoutEffect(() => {
    const root = ref.current
    if (!root) return

    const supported = typeof IntersectionObserver === 'function'
    // first use: arm the CSS gate (only when the observer that un-hides content exists)
    if (supported && !('reveal' in document.documentElement.dataset)) document.documentElement.dataset.reveal = ''

    const nodes = Array.from(root.querySelectorAll<HTMLElement>(PENDING))
    if (root.matches(PENDING)) nodes.unshift(root)
    if (!nodes.length) return

    // Show everything first, then measure: the forced style flush sees these nodes visible, so the
    // ones that stay visible never start a fade.
    for (const node of nodes) node.classList.add('is-in')
    if (!supported || prefersReducedMotion()) return

    const fold = window.innerHeight || document.documentElement.clientHeight
    const below = nodes.filter((node) => node.getBoundingClientRect().top >= fold)
    if (!below.length) return

    // Hide the below-the-fold nodes again without a (hidden, wasted) fade-out: transitions off,
    // flush the style once, restore. They then fade in when the shared observer sees them.
    const saved = below.map((node) => node.style.transition)
    for (const node of below) {
      node.style.transition = 'none'
      node.classList.remove('is-in')
    }
    void getComputedStyle(below[0]).opacity
    below.forEach((node, i) => {
      node.style.transition = saved[i]
    })

    const io = sharedObserver()
    for (const node of below) io.observe(node)
    return () => {
      for (const node of below) io.unobserve(node)
    }
    // `deps` is the caller's list, forwarded as-is (the hook's contract); `ref` is a stable ref object
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
