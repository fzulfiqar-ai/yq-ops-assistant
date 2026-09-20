import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, ClipboardList, RotateCcw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { track } from '../lib/events'
import { useReducedMotion } from '../shell/useViewport'
import { S } from '../strings'
import { PasteBubble } from './ContinueRestock'

/**
 * "Ready to restock?" — the last thing on the home page before the footer, on the night palette
 * of the opening (the portal's glow horizon, ported to CSS in market.css). The arcs are mounted
 * the first time the band scrolls into view, so their rise plays where the merchant can see it;
 * without IntersectionObserver or with reduced motion they are simply there, at rest.
 * Two actions: paste a list, and Order again (when this phone has a last order) or Browse
 * essentials. Desktop adds the example list as a chat bubble — the thing the merchant pastes.
 */
export function CtaBand({ canReorder, className }: { canReorder: boolean; className?: string }) {
  const ref = useRef<HTMLElement>(null)
  const reduced = useReducedMotion()
  const [seen, setSeen] = useState(() => typeof IntersectionObserver !== 'function')
  // nothing to play under reduced motion: the arcs are simply there
  const arcs = seen || reduced
  useEffect(() => {
    const el = ref.current
    if (!el || arcs) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true)
          io.disconnect()
        }
      },
      { rootMargin: '0px 0px -12% 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [arcs])

  const second = canReorder ? { to: '/quick?load=last', label: S.band.again, icon: RotateCcw, code: 'again' } : { to: '/shop?f=best', label: S.band.browse, icon: ArrowRight, code: 'browse' }
  const SecondIcon = second.icon

  return (
    <section ref={ref} aria-labelledby="home-band" className={cn('relative isolate overflow-hidden rounded-xl bg-night text-white', className)}>
      {/* the rim crests at ~84% of the band's height (--horizon-y sinks the arcs), under the actions */}
      <div className={cn('horizon is-small', seen && !reduced && 'is-rising')} style={{ ['--horizon-y' as string]: '100%' }} aria-hidden="true">
        {arcs && (
          <>
            <i />
            <i />
            <i />
            <i />
          </>
        )}
      </div>
      <div className="relative grid items-center gap-8 px-5 pb-20 pt-8 md:px-10 lg:grid-cols-[minmax(0,1fr)_auto] lg:px-12 lg:pb-24 lg:pt-12 2xl:px-16">
        <div className="min-w-0">
          <p className="text-2xs font-semibold uppercase tracking-[0.16em] text-white/60">{S.kicker}</p>
          <h2 id="home-band" className="mt-2 text-balance font-display text-2xl font-bold leading-tight lg:text-3xl">
            {S.band.title}
          </h2>
          <p className="mt-2 max-w-md text-sm leading-relaxed text-white/75 lg:text-base">{S.band.line}</p>
          <div className="mt-6 flex flex-wrap gap-2.5">
            <Link
              to="/quick"
              onClick={() => track('rail_click', { meta: { rail: 'band', code: 'paste' } })}
              className="inline-flex h-12 items-center gap-2 rounded-full bg-white px-5 text-base font-semibold text-night shadow-2 transition duration-1 ease-m hover:bg-white/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80 focus-visible:ring-offset-2 focus-visible:ring-offset-night active:scale-[.98]"
            >
              <ClipboardList size={17} aria-hidden="true" />
              {S.band.paste}
            </Link>
            <Link
              to={second.to}
              onClick={() => track('rail_click', { meta: { rail: 'band', code: second.code } })}
              className="inline-flex h-12 items-center gap-2 rounded-full px-5 text-base font-semibold text-white ring-1 ring-inset ring-white/35 transition duration-1 ease-m hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80 active:scale-[.98]"
            >
              {second.label}
              <SecondIcon size={16} aria-hidden="true" className="rtl:-scale-x-100" />
            </Link>
          </div>
        </div>
        <PasteBubble className="hidden lg:block" />
      </div>
    </section>
  )
}
