import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'

/**
 * Hover glide (29-Oct-2026, motion K2): one soft plum pill that slides under whichever link or
 * button of its parent the mouse is on — the header's shortcuts, the category strip — instead of
 * each item lighting up on its own. Render it as the parent's FIRST child (the items paint over it)
 * and give the parent `relative`. A mouse only: a touch screen never moves it.
 *
 * It listens on its own parent and writes its own style (no React render per move). Positions are
 * the items' offsetLeft/offsetTop, so it rides along when the parent scrolls sideways (the strip).
 * The first hover places it without a slide; it fades when the mouse leaves the group.
 */
export function HoverPill({ className }: { className?: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    const pill = ref.current
    const box = pill?.parentElement
    if (!pill || !box) return
    const over = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse') return
      const item = (e.target as Element | null)?.closest?.<HTMLElement>('a,button')
      if (!item || !box.contains(item)) return
      const first = pill.dataset.on !== '1'
      if (first) pill.style.transition = 'none'
      pill.style.width = `${item.offsetWidth}px`
      pill.style.height = `${item.offsetHeight}px`
      pill.style.transform = `translate3d(${item.offsetLeft}px, ${item.offsetTop}px, 0)`
      if (first) {
        void pill.offsetWidth // commit the jump before the fade-in, so it never slides in from 0,0
        pill.style.transition = ''
      }
      pill.dataset.on = '1'
    }
    const leave = (e: PointerEvent) => {
      if (e.pointerType === 'mouse') pill.dataset.on = '0'
    }
    box.addEventListener('pointerover', over)
    box.addEventListener('pointerleave', leave)
    return () => {
      box.removeEventListener('pointerover', over)
      box.removeEventListener('pointerleave', leave)
    }
  }, [])
  return (
    <span
      ref={ref}
      aria-hidden="true"
      data-hover-pill=""
      // physical left: offsetLeft is measured from the left edge in Arabic too
      className={cn('pointer-events-none absolute left-0 top-0 bg-plum-wash opacity-0 transition-[transform,width,opacity] duration-[220ms] [transition-timing-function:var(--m-ease-out)] data-[on="1"]:opacity-100', className)}
    />
  )
}
