import { Link, useLocation } from 'react-router-dom'
import { ArrowRight, Check } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMarket, useOrder } from '../MarketContext'
import { bhd } from '../lib/format'
import { NAV_ICONS } from '../lib/icons'
import { useCartCounts, useCartLines } from '../store/cart'
import { S } from '../strings'

/**
 * The running restock in the thumb zone while browsing (phone + tablet): "3 products · 36 pcs ·
 * BHD 12.400 → Review restock". When the office has set a wholesale minimum, the priced quote's
 * `minimum` adds a caption strip ("BHD 7.000 away from your wholesale order" / "Wholesale order
 * ready") and a 3px progress line along the dock's top edge — lilac (the plum rail's colour on the dark
 * dock) while under, mint once met; amber stays reserved for deals. Hidden on the restock, checkout and
 * tracking pages, which carry their own bar.
 */
export function CartDock() {
  const { pathname } = useLocation()
  const { itemsByCode } = useMarket()
  const { quote } = useOrder()
  const lines = useCartLines()
  const { items, units } = useCartCounts()
  if (!lines.length) return null
  if (/^\/(cart|checkout|o\/)/.test(pathname)) return null
  const estimate = lines.reduce((s, l) => s + (Number(itemsByCode.get(l.item_code)?.price_bhd) || 0) * l.qty, 0)
  const total = quote?.total_bhd != null ? Number(quote.total_bhd) : estimate
  const min = quote?.minimum && Number(quote.minimum.value_bhd) > 0 ? quote.minimum : null
  const remaining = min ? Math.max(0, Number(min.remaining_bhd) || 0) : 0
  const met = Boolean(min && (min.met || remaining <= 0))
  const pct = min ? Math.min(100, Math.max(0, ((Number(min.value_bhd) - remaining) / Number(min.value_bhd)) * 100)) : 0
  const RestockIcon = NAV_ICONS.restock

  return (
    <div className="mx-3 mb-2">
      <Link
        to="/cart"
        className="relative block overflow-hidden rounded-lg bg-ink text-white shadow-3 transition duration-1 ease-m active:scale-[.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
      >
        {min && (
          <>
            <span aria-hidden="true" className="absolute inset-x-0 top-0 h-[3px] bg-white/10">
              <span className={cn('block h-full rounded-e-full transition-[width] duration-3 ease-m', met ? 'bg-fresh' : 'bg-tile-lilac')} style={{ width: `${pct}%` }} />
            </span>
            <span className="flex h-8 items-center gap-1.5 border-b border-white/10 px-4 pt-[3px] text-xs">
              {met ? (
                <>
                  <Check size={14} strokeWidth={2.4} className="shrink-0 text-fresh" aria-hidden="true" />
                  <span key="met" className="truncate font-semibold anim-fade-in">
                    {S.wholesale.ready}
                  </span>
                </>
              ) : (
                <span key={remaining} className="truncate text-white/75 anim-fade-in">
                  {S.wholesale.away(bhd(remaining))}
                </span>
              )}
            </span>
          </>
        )}
        <span className="flex h-14 items-center gap-3 pe-2 ps-4">
          <span className="relative grid h-9 w-9 shrink-0 place-items-center rounded-sm bg-white/10">
            <RestockIcon size={18} aria-hidden="true" />
            <span className="absolute -end-1.5 -top-1.5 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-plum px-1 text-[10.5px] font-bold tnum text-white">{items}</span>
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-xs text-white/70">{S.cart.summary(items, units)}</span>
            <span key={total} className="block font-display text-md font-bold leading-tight tnum anim-fade-in">
              {bhd(total)}
            </span>
          </span>
          <span className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-sm bg-white px-3.5 text-sm font-semibold text-ink">
            {S.cart.review} <ArrowRight size={15} aria-hidden="true" />
          </span>
        </span>
      </Link>
    </div>
  )
}
