import { Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Stepper } from '@/components/ui/stepper'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { ProductImage } from './ProductImage'
import { badgeMeta, bhd, minQtyOf, money, RING, RING_INSET, stepOf, stockPill } from './shared'

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

/**
 * One product, two columns wide on a phone.
 *
 * The anatomy is fixed so a screen of them scans as a list, not a collage:
 * photo (square, inset on white) → code → two lines of spec → one stock chip →
 * price → one full-width action. The action sits at the bottom of every card in
 * a row because the price block is pushed down by `mt-auto`.
 */
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
  const out = item.stock_status === 'out_of_stock' || (typeof item.stock_qty === 'number' && item.stock_qty <= 0)
  const stock = stockPill(item)
  const step = stepOf(item)
  const min = minQtyOf(item)
  const name = item.display_name || item.item_code

  const compare =
    showCompare && item.compare_at_bhd != null && item.price_bhd != null && item.compare_at_bhd > item.price_bhd
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
  const canOrder = !out || allowBackorder
  const selected = qty > 0

  return (
    <article
      className={cn(
        'group flex flex-col overflow-hidden rounded-[20px] border bg-white transition duration-200 ease-out',
        selected
          ? 'border-[#6d28d9]/40 shadow-[0_0_0_1px_rgba(109,40,217,.10),0_8px_24px_-16px_rgba(24,16,48,.30)]'
          : 'border-[#ece9f3] shadow-[0_1px_2px_rgba(24,16,48,.04)] hover:border-[#e2ddef] hover:shadow-[0_12px_28px_-18px_rgba(24,16,48,.32)]',
        className,
      )}
    >
      <button
        type="button"
        onClick={onOpen}
        aria-label={`View details for ${name}`}
        className={cn('relative block w-full', RING_INSET)}
      >
        <ProductImage
          srcs={[item.thumb_url, item.product_image_url, item.package_image_url]}
          alt={name}
          eager={eagerImage}
          className="aspect-square w-full"
          imgClassName={cn(
            'p-3 transition-transform duration-500 ease-out group-hover:scale-[1.035]',
            out && 'opacity-45 saturate-50',
          )}
        />
        {savePct != null && savePct > 0 && (
          <span className="absolute left-2.5 top-2.5 rounded-full bg-[#1a1430] px-2 py-[3px] text-[10.5px] font-semibold leading-[14px] text-white">
            Save {savePct}%
          </span>
        )}
      </button>

      <div className="flex flex-1 flex-col border-t border-[#f4f2f9] p-3">
        <button type="button" onClick={onOpen} className={cn('rounded-lg text-left', RING)}>
          <h3 className="font-display text-[13.5px] font-bold leading-[1.15] tracking-[-0.01em] text-[#1a1430]">
            {item.item_code}
          </h3>
          {item.spec ? (
            <p className="mt-1 line-clamp-2 whitespace-pre-line text-[11.5px] leading-[1.35] text-[#6b6480]">
              {item.spec}
            </p>
          ) : null}
        </button>

        <div className="mt-2 flex flex-wrap items-center gap-1">
          <Badge tone={stock.tone} dot>
            {stock.label}
          </Badge>
          {badges.map((b) => {
            const meta = badgeMeta(b)
            return (
              <Badge key={b} tone={meta.tone}>
                {meta.label}
              </Badge>
            )
          })}
        </div>

        <div className="mt-auto pt-3">
          <div className="font-display text-[15px] font-extrabold leading-none tracking-[-0.01em] tabular-nums text-[#6d28d9]">
            {item.price_bhd != null ? bhd(item.price_bhd) : 'Price on request'}
          </div>
          <div className="mt-1 space-y-0.5 text-[10.5px] leading-[1.35] text-[#6b6480]">
            {compare != null && <div className="tabular-nums line-through">Retail BHD {money(compare)}</div>}
            {tier && (
              <div className="tabular-nums">
                {tier.min_qty}+ pcs · <span className="font-semibold text-[#1a1430]">{bhd(tier.unit_price_bhd)}</span>
              </div>
            )}
            {min > 1 && <div className="tabular-nums">Minimum {min} pcs</div>}
            {out && allowBackorder && <div>Backorder available</div>}
            {item.social_proof && <div className="line-clamp-1">{item.social_proof}</div>}
          </div>

          <div className="mt-2.5">
            {selected ? (
              <Stepper
                value={qty}
                step={step}
                min={min}
                size="md"
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
                  'flex h-11 w-full items-center justify-center gap-1.5 rounded-xl text-[13px] font-semibold transition duration-150 ease-out active:scale-[.99]',
                  RING,
                  canOrder
                    ? out
                      ? 'border border-[#e4e0ee] bg-white text-[#1a1430] hover:border-[#d9d2ee] hover:bg-[#f7f5fb]'
                      : 'bg-[#6d28d9] text-white hover:bg-[#5b21b6]'
                    : 'cursor-not-allowed border border-[#f0eef6] bg-[#f9f8fc] text-[#a8a2bb]',
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
      </div>
    </article>
  )
}
