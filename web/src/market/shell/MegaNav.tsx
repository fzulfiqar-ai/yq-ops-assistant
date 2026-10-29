import { useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { MarketCard } from '../components/MarketCard'
import { useMarket } from '../MarketContext'
import { facetsFor } from '../lib/facets'
import { categorySlug, hasBadge, niceCategory } from '../lib/format'
import { createTrail, headingForPanel, type Pt } from '../lib/menuAim'
import { S } from '../strings'

/** how long a row the hand crosses on its way to the panel waits before it takes over (menu aim) */
const AIM_MS = 300
/** the height of one category row (h-11): the highlight slides by this much per row */
const ROW_PX = 44

/**
 * Desktop "Shop" panel: the eight categories on the left (hover to preview), and on the right
 * that category's facet chips plus four best sellers with Add — a merchant can start an order
 * from the menu without landing on a page.
 *
 * Hover is a MOUSE thing here: the enter / leave handlers read the pointer type, so the
 * compatibility mouse events a touch screen fires around a tap never open, close or preview
 * anything (StickyHeader opens the panel on the first tap). Keyboard focus still previews.
 *
 * Motion (K1, 29-Oct-2026): the panel drops from Browse; one highlight slides between the
 * categories instead of each row lighting up on its own; the right side slides in for a new
 * category and its four cards rise in a wave. A hand heading diagonally for the panel crosses the
 * rows below its category without switching to them (lib/menuAim); a row it rests on takes over.
 */
/** `leaving`: the menu plays its exit, then `onGone` lets the header unmount it (it used to vanish). */
export function MegaNav({ onClose, onEnter, onLeave, leaving = false, onGone }: { onClose: () => void; onEnter: () => void; onLeave: () => void; leaving?: boolean; onGone?: () => void }) {
  const { categories, items } = useMarket()
  const [hover, setHover] = useState<string>(categories[0] || '')
  // the first category comes in with the panel's own drop; only a change of category slides
  const [swapped, setSwapped] = useState(false)
  const [trail] = useState(createTrail)
  const aimTimer = useRef<number | undefined>(undefined)
  const panelRef = useRef<HTMLDivElement>(null)
  const cat = hover || categories[0] || ''
  const row = Math.max(0, categories.indexOf(cat))
  const inCat = useMemo(() => items.filter((i) => (i.category || 'OTHER') === cat), [items, cat])
  const facets = useMemo(() => facetsFor(cat, inCat), [cat, inCat])
  const picks = useMemo(() => {
    const best = inCat.filter((i) => hasBadge(i, 'best_seller') && i.stock_status !== 'out_of_stock')
    const rest = inCat.filter((i) => !best.includes(i) && i.stock_status !== 'out_of_stock')
    return [...best, ...rest].slice(0, 4)
  }, [inCat])

  const show = (c: string) => {
    window.clearTimeout(aimTimer.current)
    if (c === cat) return
    setHover(c)
    setSwapped(true)
  }
  // the hand's last few positions: the oldest is where the heading is measured from
  const track = (e: ReactPointerEvent) => {
    if (e.pointerType === 'mouse') trail.move(e.clientX, e.clientY)
  }
  const heading = (to: Pt) => {
    const from = trail.oldest()
    const box = panelRef.current?.getBoundingClientRect()
    return Boolean(from && box && headingForPanel(from, to, box, document.documentElement.dir === 'rtl'))
  }
  // a row crossed on the way to the panel waits; once the hand has rested AIM_MS (or turned away
  // from the panel) the row under it takes over
  const settle = (c: string) => {
    const last = trail.latest()
    if (last && trail.restedMs() < AIM_MS && heading(last)) {
      aimTimer.current = window.setTimeout(() => settle(c), AIM_MS)
    } else show(c)
  }
  const preview = (c: string, e: ReactPointerEvent) => {
    window.clearTimeout(aimTimer.current)
    if (heading({ x: e.clientX, y: e.clientY })) aimTimer.current = window.setTimeout(() => settle(c), AIM_MS)
    else show(c)
  }

  return (
    <div
      role="menu"
      onPointerEnter={(e) => e.pointerType === 'mouse' && onEnter()}
      onPointerLeave={(e) => {
        if (e.pointerType !== 'mouse') return
        window.clearTimeout(aimTimer.current)
        onLeave()
      }}
      onPointerMove={track}
      onAnimationEnd={(e) => {
        if (leaving && e.target === e.currentTarget) onGone?.()
      }}
      className={cn(
        // top-full of Browse's wrapper, whose 8 px bottom padding is the hover bridge (StickyHeader)
        'absolute start-0 top-full z-header flex w-[min(960px,calc(100vw-2*var(--m-gutter)))] origin-top-left overflow-hidden rounded-xl border border-line bg-surface shadow-3 rtl:origin-top-right',
        leaving ? 'pointer-events-none [animation:m-drop-out_140ms_var(--m-ease-in)_both]' : '[animation:m-drop_240ms_var(--m-ease-out)_both]',
      )}
    >
      <ul className="relative w-[240px] shrink-0 border-e border-line-2 bg-canvas p-2">
        {/* one highlight that slides to the row the hand is on */}
        <li
          role="presentation"
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-2 top-2 h-11 rounded-sm bg-surface shadow-1 transition-transform duration-2 ease-m"
          style={{ transform: `translate3d(0, ${row * ROW_PX}px, 0)` }}
        />
        {categories.map((c) => {
          const n = items.filter((i) => (i.category || 'OTHER') === c).length
          const active = c === cat
          return (
            <li key={c} className="relative">
              <Link
                to={`/t/${categorySlug(c)}`}
                role="menuitem"
                onPointerEnter={(e) => e.pointerType === 'mouse' && preview(c, e)}
                onFocus={() => show(c)}
                onClick={onClose}
                className={cn(
                  'flex h-11 items-center justify-between gap-2 rounded-sm px-3 text-sm font-semibold transition-colors duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
                  active ? 'text-ink' : 'text-ink-2 hover:text-ink',
                )}
              >
                <span>{niceCategory(c)}</span>
                <span className="text-xs tnum text-ink-3">{n}</span>
              </Link>
            </li>
          )
        })}
        <li className="relative mt-1 border-t border-line-2 pt-1">
          <Link to="/shop" role="menuitem" onClick={onClose} className="flex h-11 items-center gap-1.5 rounded-sm px-3 text-sm font-semibold text-plum hover:bg-plum-wash">
            {S.categories.browse} <ArrowRight size={14} aria-hidden="true" className="rtl:-scale-x-100" />
          </Link>
        </li>
      </ul>
      <div ref={panelRef} onPointerEnter={() => window.clearTimeout(aimTimer.current)} className="min-w-0 flex-1 p-4">
        <div key={cat} className={cn(swapped && '[animation:m-swap-in_220ms_var(--m-ease-out)_both]')}>
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="font-display text-lg font-bold text-ink">{niceCategory(cat)}</h3>
            <Link to={`/t/${categorySlug(cat)}`} onClick={onClose} className="text-sm font-semibold text-plum hover:underline">
              {S.home.seeAll} ({inCat.length})
            </Link>
          </div>
          {facets.length > 0 && (
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {facets
                .flatMap((f) => f.values.map((v) => ({ f, v })))
                .slice(0, 12)
                .map(({ f, v }) => (
                  <Link
                    key={`${f.key}:${v.key}`}
                    to={`/t/${categorySlug(cat)}?${f.key}=${v.key}`}
                    onClick={onClose}
                    className="rounded-full border border-line bg-surface px-3 py-1.5 text-xs font-medium text-ink-2 transition duration-1 ease-m hover:border-ink/25 hover:text-ink"
                  >
                    <span className="[unicode-bidi:isolate]">{v.label}</span> <span className="text-ink-3 tnum">{v.count}</span>
                  </Link>
                ))}
            </div>
          )}
          <div className="mt-4 grid grid-cols-4 gap-3">
            {picks.map((it, i) => (
              <div key={it.item_code} className="[animation:m-rise_320ms_var(--m-ease-out)_both]" style={{ animationDelay: `${60 + i * 45}ms` }}>
                <MarketCard item={it} variant="compact" from="mega" />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
