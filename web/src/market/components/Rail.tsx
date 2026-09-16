import { Children, useRef, useState, useEffect, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useShell } from '../shell/ShellContext'
import { S } from '../strings'

/**
 * A discovery rail. Phones/tablets: a full-bleed, scroll-snapped strip (never taller than ~60% of
 * the viewport, so it does not hijack vertical scrolling). Desktop: a static row of up to six
 * cards with "See all" — no horizontal scrolling on a mouse.
 */
export function Rail({ id, title, subtitle, action, seeAllTo, children, max = 6 }: { id: string; title: string; subtitle?: string; action?: ReactNode; seeAllTo?: string; children: ReactNode; max?: number }) {
  const { viewport } = useShell()
  const desktop = viewport === 'desktop' || viewport === 'wide'
  const all = Children.toArray(children)
  const shown = desktop ? all.slice(0, viewport === 'wide' ? max : Math.min(max, 5)) : all
  const scroller = useRef<HTMLDivElement>(null)
  const [canScroll, setCanScroll] = useState<{ l: boolean; r: boolean }>({ l: false, r: false })
  useEffect(() => {
    const el = scroller.current
    if (!el || desktop) return
    const update = () => setCanScroll({ l: el.scrollLeft > 4, r: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 })
    update()
    el.addEventListener('scroll', update, { passive: true })
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => {
      el.removeEventListener('scroll', update)
      ro.disconnect()
    }
  }, [desktop, all.length])
  const by = (dir: 1 | -1) => scroller.current?.scrollBy({ left: dir * scroller.current.clientWidth * 0.8, behavior: 'smooth' })

  return (
    <section className="mt-7 first:mt-4" aria-labelledby={`rail-${id}`}>
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 id={`rail-${id}`} className="font-display text-lg font-bold text-ink lg:text-xl">
            {title}
          </h2>
          {subtitle && <p className="mt-0.5 text-xs text-ink-2">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {action}
          {seeAllTo && (
            <Link to={seeAllTo} className="inline-flex h-9 items-center gap-1 rounded-sm px-2.5 text-sm font-semibold text-plum transition duration-1 ease-m hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              {S.home.seeAll} <ArrowRight size={14} aria-hidden="true" />
            </Link>
          )}
          {!desktop && viewport === 'tablet' && (
            <>
              <button type="button" onClick={() => by(-1)} disabled={!canScroll.l} aria-label="Scroll back" className="grid h-9 w-9 place-items-center rounded-sm border border-line bg-surface text-ink-2 disabled:opacity-30">
                <ChevronLeft size={16} />
              </button>
              <button type="button" onClick={() => by(1)} disabled={!canScroll.r} aria-label="Scroll forward" className="grid h-9 w-9 place-items-center rounded-sm border border-line bg-surface text-ink-2 disabled:opacity-30">
                <ChevronRight size={16} />
              </button>
            </>
          )}
        </div>
      </div>
      {desktop ? (
        <div className={cn('mt-3 grid gap-3', viewport === 'wide' ? 'grid-cols-4 2xl:grid-cols-5 3xl:grid-cols-6' : 'grid-cols-4 xl:grid-cols-5')}>{shown}</div>
      ) : (
        <div ref={scroller} className="rail bleed mt-3 pb-1.5">
          {shown}
          <span aria-hidden="true" className="w-px shrink-0" />
        </div>
      )}
    </section>
  )
}
