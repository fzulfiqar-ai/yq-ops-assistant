import { Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Stepper } from '@/components/ui/stepper'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { ProductImage } from './ProductImage'
import { badgeMeta, bhd, minQtyOf, money, stepOf, stockMeta } from './shared'

export interface ProductCardProps {
  item: ShopItem
  qty: number
  allowBackorder: boolean
  showCompare: boolean
  onOpen: () => void
  onAdd: () => void
  onSetQty: (qty: number) => void
  onRemove: () => void
  eagerImage?: boolean
  className?: string
}

/** Order the chips so the strongest signal wins the limited space on a phone. */
const BADGE_ORDER = ['best_seller', 'price_drop', 'on_offer', 'new', 'trending']

export function ProductCard({
  item,
  qty,
  allowBackorder,
  showCompare,
  onOpen,
  onAdd,
  onSetQty,
  onRemove,
  eagerImage,
  className,
}: ProductCardProps) {
  const out = item.stock_status === 'out_of_stock'
  const stock = stockMeta(item.stock_status)
  const step = stepOf(item)
  const min = minQtyOf(item)
  const name = item.display_name || item.item_code

  const compare = showCompare && item.compare_at_bhd != null && item.price_bhd != null && item.compare_at_bhd > item.price_bhd
    ? Number(item.compare_at_bhd)
    : null
  const savePct =
    compare != null
      ? Number(item.save_pct) || Math.round(((compare - Number(item.price_bhd)) / compare) * 100)
      : null

  const badges = (item.badges || [])
    .slice()
    .sort((a, b) => BADGE_ORDER.indexOf(a) - BADGE_ORDER.indexOf(b))
    .slice(0, 2)

  const tier = (item.tiers || [])[0]
  const tierHint = tier ? tier.label || `${tier.min_qty}+ → ${bhd(tier.unit_price_bhd)}` : null

  const canOrder = !out || allowBackorder

  return (
    <article
      className={cn(
        'group flex flex-col overflow-hidden rounded-2xl border border-[#ece9f3] bg-white shadow-[0_1px_2px_rgba(24,16,48,.04)] transition-shadow duration-300 hover:shadow-[0_10px_30px_-14px_rgba(24,16,48,.28)]',
        className,
      )}
    >
      <button
        type="button"
        onClick={onOpen}
        aria-label={`View details for ${name}`}
        className="relative block w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#6d28d9]"
      >
        <ProductImage
          srcs={[item.thumb_url, item.product_image_url, item.package_image_url]}
          alt={name}
          eager={eagerImage}
          className="aspect-square w-full"
          imgClassName={cn('p-3 transition-transform duration-500 group-hover:scale-[1.03]', out && 'opacity-55 saturate-50')}
        />
        {savePct != null && savePct > 0 && (
          <span className="absolute left-2.5 top-2.5 rounded-full bg-[#1a1430] px-2 py-0.5 text-[10.5px] font-semibold text-white">
            Save {savePct}%
          </span>
        )}
      </button>

      <div className="flex flex-1 flex-col border-t border-[#f2f0f7] p-3">
        <button
          type="button"
          onClick={onOpen}
          className="text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]"
        >
          <h3 className="font-display text-[13.5px] font-bold leading-tight text-[#1a1430]">{item.item_code}</h3>
          {item.spec ? (
            <p className="mt-1 line-clamp-2 whitespace-pre-line text-[11.5px] leading-snug text-[#6b6480]">{item.spec}</p>
          ) : null}
        </button>

        {badges.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {badges.map((b) => {
              const meta = badgeMeta(b)
              return (
                <Badge key={b} tone={meta.tone}>
                  {meta.label}
                </Badge>
              )
            })}
          </div>
        )}

        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Badge tone={stock.tone}>{stock.label}</Badge>
          {out && allowBackorder && <span className="text-[10.5px] text-[#6b6480]">Backorder available</span>}
        </div>

        <div className="mt-2.5 border-t border-[#f2f0f7] pt-2.5">
          <div className="flex items-baseline gap-2">
            <span className="font-display text-[17px] font-extrabold leading-none tabular-nums text-[#6d28d9]">
              {item.price_bhd != null ? bhd(item.price_bhd) : 'Price on request'}
            </span>
          </div>
          {compare != null && (
            <div className="mt-1 flex items-center gap-1.5 text-[11px] text-[#6b6480]">
              <span className="tabular-nums line-through">Retail BHD {money(compare)}</span>
            </div>
          )}
          {tierHint && <div className="mt-1 text-[11px] font-medium tabular-nums text-[#6d28d9]">{tierHint}</div>}
          {min > 1 && <div className="mt-1 text-[10.5px] text-[#6b6480]">Min {min}</div>}
          {item.social_proof && <div className="mt-1 text-[10.5px] leading-snug text-[#6b6480]">{item.social_proof}</div>}
        </div>

        <div className="mt-3 pt-0">
          {qty > 0 ? (
            <Stepper
              value={qty}
              step={step}
              min={min}
              label={item.item_code}
              onChange={onSetQty}
              onRemove={onRemove}
              className="w-full"
            />
          ) : (
            <button
              type="button"
              onClick={onAdd}
              disabled={!canOrder}
              className={cn(
                'flex h-11 w-full items-center justify-center gap-1.5 rounded-xl text-[13px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9] focus-visible:ring-offset-2',
                canOrder
                  ? out
                    ? 'border border-[#e4e0ee] bg-white text-[#1a1430] hover:bg-[#f7f5fb]'
                    : 'bg-[#6d28d9] text-white hover:bg-[#5b21b6]'
                  : 'cursor-not-allowed border border-[#ece9f3] bg-[#f7f6fa] text-[#a8a2bb]',
              )}
            >
              {canOrder ? (
                <>
                  <Plus size={15} aria-hidden="true" /> {out ? 'Backorder' : 'Add'}
                </>
              ) : (
                'Out of stock'
              )}
            </button>
          )}
        </div>
      </div>
    </article>
  )
}
