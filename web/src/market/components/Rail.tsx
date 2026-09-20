import { Children, isValidElement, useEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useReveal } from '../hooks/useReveal'
import { useShell } from '../shell/ShellContext'
import { S } from '../strings'
import { SectionHeader } from '../ui/SectionHeader'

/**
 * A discovery rail. Phones/tablets: a full-bleed, scroll-snapped strip (never taller than ~60% of
 * the viewport, so it does not hijack vertical scrolling). Desktop: a static row of up to six
 * cards with "See all" — no horizontal scrolling on a mouse — whose cards fade and rise in with a
 * short stagger as the row scrolls into view (hooks/useReveal; CSS "v3: reveal").
 *
 * Reveal rules: `.reveal` sits on a plain wrapper around each desktop card (the card keeps its own
 * hover transition and its `.cv-*` class); the wrapper's className never changes; the grid is the
 * element passed to useReveal. Scroll rails get no reveal — their cards are off-screen sideways.
 */
export function Rail({ id, title, subtitle, action, seeAllTo, children, max = 6 }: { id: string; title: string; subtitle?: string; action?: ReactNode; seeAllTo?: string; children: ReactNode; max?: number }) {
  const { viewport } = useShell()
  const desktop = viewport === 'desktop' || viewport === 'wide'
  const all = Children.toArray(children)
  const shown = desktop ? all.slice(0, viewport === 'wide' ? max : Math.min(max, 5)) : all
  // one id per shown card: the wrapper keys, and the reveal deps — a card swapped into the row is
  // then rescanned, without the scan (and its forced layout) running after every render
  const keys = shown.map((child, i) => (isValidElement(child) && child.key != null ? String(child.key) : String(i)))
  const scroller = useRef<HTMLDivElement>(null)
  const grid = useRef<HTMLDivElement>(null)
  useReveal(grid, [desktop, keys.join('|')])
  // the arrows are a tablet affordance: on a phone nothing reads this, so nothing listens either
  const tablet = viewport === 'tablet'
  const [canScroll, setCanScroll] = useState<{ l: boolean; r: boolean }>({ l: false, r: false })
  useEffect(() => {
    const el = scroller.current
    if (!el || !tablet) return
    let frame = 0
    const read = () => {
      frame = 0
      // scrollLeft runs negative in RTL: the distance from the start edge is its magnitude
      const x = Math.abs(el.scrollLeft)
      const l = x > 4
      const r = x + el.clientWidth < el.scrollWidth - 4
      // same value → same object, so a swipe does not re-render the rail on every scroll event
      setCanScroll((p) => (p.l === l && p.r === r ? p : { l, r }))
    }
    const update = () => {
      if (!frame) frame = window.requestAnimationFrame(read)
    }
    read()
    el.addEventListener('scroll', update, { passive: true })
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => {
      if (frame) window.cancelAnimationFrame(frame)
      el.removeEventListener('scroll', update)
      ro.disconnect()
    }
  }, [tablet, all.length])
  const by = (dir: 1 | -1) => scroller.current?.scrollBy({ left: dir * scroller.current.clientWidth * 0.8, behavior: 'smooth' })

  const arrows = tablet ? (
    <>
      <button type="button" onClick={() => by(-1)} disabled={!canScroll.l} aria-label={S.card.scrollBack} className="relative grid h-9 w-9 place-items-center rounded-sm border border-line bg-surface text-ink-2 transition duration-1 ease-m after:absolute after:-inset-y-1 after:inset-x-0 hover:bg-plum-wash disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
        <ChevronLeft size={16} aria-hidden="true" className="rtl:-scale-x-100" />
      </button>
      <button type="button" onClick={() => by(1)} disabled={!canScroll.r} aria-label={S.card.scrollForward} className="relative grid h-9 w-9 place-items-center rounded-sm border border-line bg-surface text-ink-2 transition duration-1 ease-m after:absolute after:-inset-y-1 after:inset-x-0 hover:bg-plum-wash disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
        <ChevronRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />
      </button>
    </>
  ) : null

  return (
    <section className="mt-7 first:mt-4" aria-labelledby={`rail-${id}`}>
      <SectionHeader
        id={`rail-${id}`}
        title={title}
        line={subtitle}
        seeAllTo={seeAllTo}
        action={
          action || arrows ? (
            <>
              {action}
              {arrows}
            </>
          ) : undefined
        }
      />
      {desktop ? (
        <div ref={grid} className={cn('mt-3 grid gap-3', viewport === 'wide' ? 'grid-cols-4 2xl:grid-cols-5 3xl:grid-cols-6' : 'grid-cols-4 xl:grid-cols-5')}>
          {shown.map((child, i) => (
            // grid-cols-1 (minmax(0,1fr)) so a skipped .cv-* card's intrinsic size can never widen the cell
            <div key={keys[i]} className="reveal grid grid-cols-1" style={{ ['--i' as string]: i }}>
              {child}
            </div>
          ))}
        </div>
      ) : (
        <div ref={scroller} className="rail bleed mt-3 pb-1.5">
          {shown}
          <span aria-hidden="true" className="w-px shrink-0" />
        </div>
      )}
    </section>
  )
}
