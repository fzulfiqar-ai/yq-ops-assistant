import { onScrollLock, scrollLocked } from '@/lib/sheet'
import { setScrollWriter } from '@/lib/scrollKeeper'

/**
 * Glide scrolling (29-Oct-2026, motion F1): on a PC with a mouse the wheel moves the page in one
 * smooth glide instead of 100 px steps. Lenis (MIT, ~5 KB gzip, pinned in package.json) drives the
 * window's own scroll, so the sticky header, the reveal observer and scroll-driven CSS all keep
 * working as they are.
 *
 * Who gets it: a fine pointer that can hover, never under reduced motion, never on a lite device. A
 * phone, a tablet and a touch PC keep their native scrolling, which is already smooth there. It is
 * fetched with a dynamic import after the first paint, so it is never in the first-load scripts.
 *
 * - A layer (a sheet, the palette, the photo viewer) locks the page through lib/sheet's lockScroll,
 *   and the glide pauses with it. Lenis's own stylesheet is not used: its paused rule
 *   (`overflow: clip`) would bring back the Windows scrollbar strip lockScroll covers.
 * - Scrollers inside the page (the mini-cart column, a sheet's body, the palette list) keep their
 *   native scroll while they can scroll (allowNestedScroll); at their end the wheel stops there, as
 *   before (they are overscroll-behavior: contain).
 * - The scroll keeper's jumps (a new page to the top, Back to where it was) are immediate moves
 *   through Lenis, so a glide still running from a click made mid-scroll never carries on.
 */

type Glide = InstanceType<typeof import('lenis').default>

let glide: Glide | null = null
let starting = false

export function glideWanted(): boolean {
  try {
    return (
      window.matchMedia('(hover: hover) and (pointer: fine)').matches &&
      !window.matchMedia('(prefers-reduced-motion: reduce)').matches &&
      !('lite' in document.documentElement.dataset)
    )
  } catch {
    return false
  }
}

/** Once, after the first paint (main.market.tsx). A failed download leaves native scrolling. */
export function startGlide(): void {
  if (glide || starting || !glideWanted()) return
  starting = true
  import('lenis')
    .then(({ default: Lenis }) => {
      const g = new Lenis({ autoRaf: true, lerp: 0.1, allowNestedScroll: true, stopInertiaOnNavigate: true })
      glide = g
      if (scrollLocked()) g.stop()
      onScrollLock((locked) => (locked ? g.stop() : g.start()))
      // resize first: Lenis learns a new page height through a 250 ms-debounced observer, and the Back
      // restore must not be clamped to the old one
      setScrollWriter((y) => {
        g.resize()
        g.scrollTo(y, { immediate: true, force: true })
      })
    })
    .catch(() => {
      /* the chunk did not arrive: the page keeps native scrolling */
    })
    .finally(() => {
      starting = false
    })
}
