import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { track } from '../lib/events'
import { brandTiles } from '../lib/home'
import { S } from '../strings'
import { ProductImage } from '../ui/ProductImage'
import { SectionHeader } from '../ui/SectionHeader'

/**
 * Shop by brand — silent on a single-brand shelf (today: VFAN only), a row of brand tiles the day
 * a second brand carries three or more lines. Each tile: the brand's best photo on a white disc,
 * the brand as a wordmark, its line count; it opens Browse filtered to that brand.
 * Phone: a swipe rail; desktop: up to four tiles in a row (six on the widest screens).
 */
export function BrandTiles({ items, className }: { items: ShopItem[]; className?: string }) {
  const tiles = useMemo(() => brandTiles(items), [items])
  if (!tiles.length) return null
  return (
    <section aria-labelledby="home-brands" className={className}>
      <SectionHeader id="home-brands" title={S.brands.title} />
      <ul className="rail bleed mt-3 pb-1.5 lg:mx-0 lg:grid lg:grid-cols-4 lg:gap-3 lg:overflow-visible lg:px-0 lg:pb-0 3xl:grid-cols-6" style={{ ['--m-rail-gap' as string]: '10px' }}>
        {tiles.map((t, i) => (
          <li key={t.brand} className={cn('w-[11.5rem] lg:w-auto', i >= 4 && 'lg:hidden 3xl:block')}>
            <Link
              to={`/shop?brand=${encodeURIComponent(t.brand)}`}
              onClick={() => track('rail_click', { meta: { rail: 'brand', pos: i } })}
              className="group flex h-full items-center gap-3 rounded-lg bg-surface p-2.5 pe-3 shadow-1 ring-1 ring-line transition duration-2 ease-m hover:-translate-y-0.5 hover:shadow-2 hover:ring-ink/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 active:scale-[.985]"
            >
              <span className="grid h-14 w-14 shrink-0 place-items-center rounded-full bg-surface-2">
                <span className="block h-[70%] w-[70%]">
                  <ProductImage item={t.image} alt="" sizes="56px" size={56} className="h-full w-full bg-transparent" imgClassName="mix-blend-multiply" iconSize={18} showCaption={false} />
                </span>
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-display text-base font-extrabold uppercase tracking-[0.02em] text-ink">{t.brand}</span>
                <span className="block text-xs tnum text-ink-2">{S.brands.lines(t.count)}</span>
              </span>
              <ChevronRight size={16} className="shrink-0 text-ink-3 transition-transform duration-2 ease-m group-hover:translate-x-0.5 rtl:-scale-x-100" aria-hidden="true" />
            </Link>
          </li>
        ))}
        <li aria-hidden="true" className="w-px shrink-0 lg:hidden" />
      </ul>
    </section>
  )
}
