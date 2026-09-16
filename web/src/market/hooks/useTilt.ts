import type { PointerEvent as ReactPointerEvent } from 'react'

/**
 * Pointer tilt for ONE surface (the home hero): the element leans up to `max` degrees toward
 * the pointer via CSS custom properties consumed by `.tilt` (market.css). Fine pointers only;
 * touch and reduced-motion get nothing (the class's transform is neutralised by media queries).
 * No refs: the handlers read `e.currentTarget`.
 */
export function tiltHandlers(max = 3) {
  return {
    onPointerMove(e: ReactPointerEvent<HTMLElement>) {
      if (e.pointerType !== 'mouse') return
      const el = e.currentTarget
      const r = el.getBoundingClientRect()
      const px = (e.clientX - r.left) / r.width - 0.5
      const py = (e.clientY - r.top) / r.height - 0.5
      el.style.setProperty('--ry', `${(px * max * 2).toFixed(2)}deg`)
      el.style.setProperty('--rx', `${(-py * max * 2).toFixed(2)}deg`)
    },
    onPointerLeave(e: ReactPointerEvent<HTMLElement>) {
      e.currentTarget.style.setProperty('--rx', '0deg')
      e.currentTarget.style.setProperty('--ry', '0deg')
    },
  }
}
