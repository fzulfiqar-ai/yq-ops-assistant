import { Link, useLocation } from 'react-router-dom'
import { ArrowRight, ShoppingBag } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMarket, useOrder } from '../MarketContext'
import { bhd } from '../lib/format'
import { useCartCounts, useCartLines } from '../store/cart'
import { S } from '../strings'

/**
 * The running order in the thumb zone while browsing (phone + tablet): "3 products · 36 pcs ·
 * BHD 12.400 → Review". Hidden on the cart/checkout/tracking pages, which carry their own bar.
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
  return (
    <div className="mx-3 mb-2">
      <Link
        to="/cart"
        className={cn(
          'flex h-14 items-center gap-3 rounded-lg bg-ink ps-4 pe-2 text-white shadow-3 transition duration-1 ease-m active:scale-[.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
        )}
      >
        <span className="relative grid h-9 w-9 place-items-center rounded-sm bg-white/10">
          <ShoppingBag size={18} aria-hidden="true" />
          <span className="absolute -end-1.5 -top-1.5 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-plum px-1 text-[10.5px] font-bold tnum text-white">{items}</span>
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs text-white/70">{S.cart.summary(items, units)}</span>
          <span key={total} className="block font-display text-md font-bold leading-tight tnum anim-fade-in">
            {bhd(total)}
          </span>
        </span>
        <span className="inline-flex h-10 items-center gap-1.5 rounded-sm bg-white px-3.5 text-sm font-semibold text-ink">
          {S.cart.review} <ArrowRight size={15} aria-hidden="true" />
        </span>
      </Link>
    </div>
  )
}
