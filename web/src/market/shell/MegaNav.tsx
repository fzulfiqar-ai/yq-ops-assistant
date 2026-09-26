import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { MarketCard } from '../components/MarketCard'
import { useMarket } from '../MarketContext'
import { facetsFor } from '../lib/facets'
import { categorySlug, hasBadge, niceCategory } from '../lib/format'
import { S } from '../strings'

/**
 * Desktop "Shop" panel: the eight categories on the left (hover to preview), and on the right
 * that category's facet chips plus four best sellers with Add — a merchant can start an order
 * from the menu without landing on a page.
 *
 * Hover is a MOUSE thing here: the enter / leave handlers read the pointer type, so the
 * compatibility mouse events a touch screen fires around a tap never open, close or preview
 * anything (StickyHeader opens the panel on the first tap). Keyboard focus still previews.
 */
export function MegaNav({ onClose, onEnter, onLeave }: { onClose: () => void; onEnter: () => void; onLeave: () => void }) {
  const { categories, items } = useMarket()
  const [hover, setHover] = useState<string>(categories[0] || '')
  const cat = hover || categories[0] || ''
  const inCat = useMemo(() => items.filter((i) => (i.category || 'OTHER') === cat), [items, cat])
  const facets = useMemo(() => facetsFor(cat, inCat), [cat, inCat])
  const picks = useMemo(() => {
    const best = inCat.filter((i) => hasBadge(i, 'best_seller') && i.stock_status !== 'out_of_stock')
    const rest = inCat.filter((i) => !best.includes(i) && i.stock_status !== 'out_of_stock')
    return [...best, ...rest].slice(0, 4)
  }, [inCat])

  return (
    <div
      role="menu"
      onPointerEnter={(e) => e.pointerType === 'mouse' && onEnter()}
      onPointerLeave={(e) => e.pointerType === 'mouse' && onLeave()}
      className="absolute start-0 top-full z-header mt-2 flex w-[min(960px,calc(100vw-2*var(--m-gutter)))] overflow-hidden rounded-xl border border-line bg-surface shadow-3 [animation:m-pop_180ms_var(--m-ease-spring)_both]"
    >
      <ul className="w-[240px] shrink-0 border-e border-line-2 bg-canvas p-2">
        {categories.map((c) => {
          const n = items.filter((i) => (i.category || 'OTHER') === c).length
          const active = c === cat
          return (
            <li key={c}>
              <Link
                to={`/t/${categorySlug(c)}`}
                role="menuitem"
                onPointerEnter={(e) => e.pointerType === 'mouse' && setHover(c)}
                onFocus={() => setHover(c)}
                onClick={onClose}
                className={cn(
                  'flex h-11 items-center justify-between gap-2 rounded-sm px-3 text-sm font-semibold transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
                  active ? 'bg-surface text-ink shadow-1' : 'text-ink-2 hover:text-ink',
                )}
              >
                <span>{niceCategory(c)}</span>
                <span className="text-xs tnum text-ink-3">{n}</span>
              </Link>
            </li>
          )
        })}
        <li className="mt-1 border-t border-line-2 pt-1">
          <Link to="/shop" role="menuitem" onClick={onClose} className="flex h-11 items-center gap-1.5 rounded-sm px-3 text-sm font-semibold text-plum hover:bg-plum-wash">
            {S.categories.browse} <ArrowRight size={14} aria-hidden="true" />
          </Link>
        </li>
      </ul>
      <div className="min-w-0 flex-1 p-4">
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
                  {v.label} <span className="text-ink-3 tnum">{v.count}</span>
                </Link>
              ))}
          </div>
        )}
        <div className="mt-4 grid grid-cols-4 gap-3">
          {picks.map((it) => (
            <MarketCard key={it.item_code} item={it} variant="compact" from="mega" />
          ))}
        </div>
      </div>
    </div>
  )
}
