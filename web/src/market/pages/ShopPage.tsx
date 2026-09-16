import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Percent, Sparkles, Tag, TrendingDown, Zap } from 'lucide-react'
import { useMarket } from '../MarketContext'
import { categoryTiles } from '../lib/home'
import { categorySlug, hasBadge, niceCategory } from '../lib/format'
import { usePageTitle } from '../shell/ShellContext'
import { S } from '../strings'
import { Chip } from '../ui/Chip'
import { ProductImage } from '../ui/ProductImage'
import { GridPage } from './GridPage'

/**
 * /shop — the categories as a visible, top-level destination (Baymard: 40% of apps bury them),
 * plus New / Offers entry points. With ?f= or ?sort= it becomes the "See all" shelf over the
 * whole catalog (e.g. /shop?sort=popular from the Best sellers rail).
 */
export default function ShopPage() {
  const { items, categories } = useMarket()
  const [params] = useSearchParams()
  const filtered = params.has('f') || params.has('sort')
  const f = params.get('f') || ''
  const title = f.includes('clearance') ? S.rails.clearance : f.includes('drops') ? S.rails.drops : f.includes('new') ? S.rails.arrived : f.includes('offers') ? S.rails.offers : params.get('sort') === 'popular' ? S.rails.best : S.home.all
  usePageTitle(filtered ? title : S.shop.title, filtered, `${S.shop.title} · ${S.brand}`)
  const tiles = useMemo(() => categoryTiles(items, categories), [items, categories])
  const newCount = items.filter((i) => hasBadge(i, 'new')).length
  const offerCount = items.filter((i) => hasBadge(i, 'on_offer') || i.compare_at_bhd != null).length
  const clearanceCount = items.filter((i) => hasBadge(i, 'clearance')).length
  const dropCount = items.filter((i) => hasBadge(i, 'price_drop') || i.was_bhd != null).length

  if (filtered) return <GridPage title={title} items={items} breadcrumb={{ label: S.shop.title, to: '/shop' }} />

  return (
    <div className="px-gutter lg:px-0">
      <h1 className="hidden font-display text-2xl font-bold text-ink lg:block">{S.shop.title}</h1>
      <div className="mt-1 flex gap-2 lg:mt-3">
        {newCount > 0 && (
          <Link to="/shop?f=new" className="inline-flex h-10 items-center gap-1.5 rounded-full border border-line bg-surface px-3.5 text-sm font-semibold text-ink hover:bg-plum-wash">
            <Sparkles size={15} className="text-plum" aria-hidden="true" /> {S.rails.arrived} <span className="text-xs font-normal tnum text-ink-3">{newCount}</span>
          </Link>
        )}
        {offerCount > 0 && (
          <Link to="/shop?f=offers" className="inline-flex h-10 items-center gap-1.5 rounded-full border border-line bg-surface px-3.5 text-sm font-semibold text-ink hover:bg-plum-wash">
            <Tag size={15} className="text-plum" aria-hidden="true" /> {S.rails.offers} <span className="text-xs font-normal tnum text-ink-3">{offerCount}</span>
          </Link>
        )}
        {dropCount > 0 && (
          <Link to="/shop?f=drops" className="inline-flex h-10 items-center gap-1.5 rounded-full border border-line bg-surface px-3.5 text-sm font-semibold text-ink hover:bg-plum-wash">
            <TrendingDown size={15} className="text-plum" aria-hidden="true" /> {S.rails.drops} <span className="text-xs font-normal tnum text-ink-3">{dropCount}</span>
          </Link>
        )}
        {clearanceCount > 0 && (
          <Link to="/shop?f=clearance" className="inline-flex h-10 items-center gap-1.5 rounded-full border border-warn/30 bg-warn-soft px-3.5 text-sm font-semibold text-warn hover:bg-warn/15">
            <Percent size={15} aria-hidden="true" /> {S.rails.clearance} <span className="text-xs font-normal tnum">{clearanceCount}</span>
          </Link>
        )}
        <Link to="/quick" className="inline-flex h-10 items-center gap-1.5 rounded-full border border-line bg-surface px-3.5 text-sm font-semibold text-ink hover:bg-plum-wash">
          <Zap size={15} className="text-plum" aria-hidden="true" /> {S.nav.quick}
        </Link>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        {tiles.map((t) => (
          <Link key={t.category} to={`/t/${categorySlug(t.category)}`} className="group overflow-hidden rounded-lg border border-line bg-surface transition duration-2 ease-m hover:-translate-y-0.5 hover:border-ink/15 hover:shadow-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
            <div className="relative">
              <ProductImage item={t.image} alt="" sizes="(min-width: 768px) 24vw, 46vw" size={320} className="w-full" imgClassName="p-5 transition-transform duration-3 ease-m group-hover:scale-[1.04]" iconSize={32} showCaption={false} />
              {t.newCount > 0 && (
                <span className="absolute end-2.5 top-2.5">
                  <Chip tone="ok">{S.home.newCount(t.newCount)}</Chip>
                </span>
              )}
            </div>
            <div className="border-t border-line-2 p-3">
              <div className="font-display text-base font-bold text-ink">{niceCategory(t.category)}</div>
              <div className="text-xs tnum text-ink-2">{S.shop.products(t.count)}</div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
