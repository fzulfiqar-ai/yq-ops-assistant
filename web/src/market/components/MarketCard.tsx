import { MessageCircle, Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Stepper } from '@/components/ui/stepper'
import { cn } from '@/lib/utils'
import type { RepCard, ShopItem } from '@/lib/shopApi'
import { ProductImage } from '@/pages/shop/ProductImage'
import { badgeMeta, bhd, minQtyOf, money, RING, RING_INSET, stepOf, stockMeta } from '@/pages/shop/shared'
import { S } from '../strings'

/**
 * The marketplace product card (plan §H). Product NAME as the title, the code underneath,
 * one stock pill, up to two badges, the trade price, the first volume tier, and ONE action:
 * Add (which becomes a stepper preset to the merchant's remembered quantity) — or Backorder,
 * or "Tell Ahmed" when the item is sold out and the rep takes WhatsApp.
 */

const BADGE_ORDER = ['on_offer', 'new', 'best_seller', 'trending', 'selling_fast', 'price_drop']

export interface MarketCardProps {
  item: ShopItem
  qty: number
  defaultQty: number
  allowBackorder: boolean
  showCompare: boolean
  publicTiers: boolean
  rep?: RepCard | null
  compact?: boolean
  eagerImage?: boolean
  onOpen: () => void
  onAdd: () => void
  onSetQty: (qty: number) => void
  onRemove: () => void
  onKeypad: () => void
}

export function tierNudge(item: ShopItem, qty: number): string | null {
  const next = (item.tiers || []).find((t) => t.min_qty > qty)
  if (!next || qty <= 0) return null
  return S.card.nudge(next.min_qty - qty, next.min_qty)
}

export function MarketCard({
  item,
  qty,
  defaultQty,
  allowBackorder,
  showCompare,
  publicTiers,
  rep,
  compact,
  eagerImage,
  onOpen,
  onAdd,
  onSetQty,
  onRemove,
  onKeypad,
}: MarketCardProps) {
  const out = item.stock_status === 'out_of_stock'
  const stock = stockMeta(item.stock_status)
  const step = stepOf(item)
  const min = minQtyOf(item)
  const name = item.display_name || item.item_code
  const showCode = Boolean(item.display_name && item.display_name !== item.item_code)
  const compare =
    showCompare && item.compare_at_bhd != null && item.price_bhd != null && item.compare_at_bhd > item.price_bhd
      ? Number(item.compare_at_bhd)
      : null
  const savePct = compare != null ? Number(item.save_pct) || Math.round(((compare - Number(item.price_bhd)) / compare) * 100) : null
  const badges = (item.badges || [])
    .filter((b) => b !== 'selling_fast' || item.stock_status !== 'out_of_stock')
    .slice()
    .sort((a, b) => BADGE_ORDER.indexOf(a) - BADGE_ORDER.indexOf(b))
    .slice(0, 2)
  const tier = publicTiers ? (item.tiers || [])[0] : undefined
  const canOrder = !out || allowBackorder
  const selected = qty > 0
  const nudge = selected ? tierNudge(item, qty) : null
  const tellRep = out && !allowBackorder && rep?.whatsapp_url
  const tellUrl = tellRep
    ? `${rep!.whatsapp_url!.split('?text=')[0]}?text=${encodeURIComponent(`Hello ${rep!.first_name || ''}, please tell me when ${item.item_code} (${name}) is back in stock.`)}`
    : null

  return (
    <article
      className={cn(
        'group flex flex-col overflow-hidden rounded-[20px] border bg-white transition duration-200 ease-out',
        compact && 'w-[10.5rem] shrink-0 sm:w-[12rem]',
        selected
          ? 'border-[#6d28d9]/40 shadow-[0_0_0_1px_rgba(109,40,217,.10),0_8px_24px_-16px_rgba(24,16,48,.30)]'
          : 'border-[#ece9f3] shadow-[0_1px_2px_rgba(24,16,48,.04)] hover:border-[#e2ddef] hover:shadow-[0_12px_28px_-18px_rgba(24,16,48,.32)]',
      )}
    >
      <button type="button" onClick={onOpen} className={cn('relative block w-full', RING_INSET)}>
        <ProductImage
          srcs={[item.thumb_url, item.product_image_url, item.package_image_url]}
          alt={name}
          eager={eagerImage}
          width={compact ? 192 : 320}
          height={compact ? 192 : 320}
          className="aspect-square w-full"
          imgClassName={cn('p-3 transition-transform duration-500 ease-out group-hover:scale-[1.035]', out && 'opacity-45 saturate-50')}
          iconSize={compact ? 28 : 40}
          showCaption={!compact}
        />
        {badges[0] && (
          <span className="absolute left-2.5 top-2.5">
            <Badge tone={badgeMeta(badges[0]).tone}>{badgeMeta(badges[0]).label}</Badge>
          </span>
        )}
        {savePct != null && savePct > 0 && (
          <span className="absolute right-2.5 top-2.5 rounded-full bg-[#1a1430] px-2 py-[3px] text-[11px] font-semibold leading-[14px] text-white">
            Save {savePct}%
          </span>
        )}
      </button>

      <div className={cn('flex flex-1 flex-col border-t border-[#f4f2f9]', compact ? 'p-2.5' : 'p-3')}>
        <button type="button" onClick={onOpen} className={cn('rounded-lg text-left', RING)}>
          <h3 className={cn('line-clamp-2 font-display font-bold leading-[1.15] tracking-[-0.01em] text-[#1a1430]', compact ? 'text-[12.5px]' : 'text-[13.5px]')}>
            {name}
          </h3>
          {!compact && (
            <p className="mt-1 line-clamp-1 text-[12px] leading-[1.35] text-[#6b6480]">
              {[showCode ? item.item_code : null, (item.spec || '').split('\n')[0]].filter(Boolean).join(' · ')}
            </p>
          )}
        </button>

        <div className="mt-2 flex flex-wrap items-center gap-1">
          <Badge tone={stock.tone} dot>
            {stock.label}
          </Badge>
          {!compact && badges[1] && <Badge tone={badgeMeta(badges[1]).tone}>{badgeMeta(badges[1]).label}</Badge>}
        </div>

        <div className="mt-auto pt-2.5">
          <div className="flex items-baseline gap-1.5">
            <span className={cn('font-display font-extrabold leading-none tracking-[-0.01em] tabular-nums text-[#6d28d9]', compact ? 'text-[14px]' : 'text-[15px]')}>
              {item.price_bhd != null ? bhd(item.price_bhd) : 'Price on request'}
            </span>
            {item.price_bhd != null && !compact && <span className="text-[11px] text-[#6b6480]">{S.card.perPiece}</span>}
          </div>
          <div className="mt-1 min-h-[1rem] text-[11px] leading-[1.35] text-[#6b6480]">
            {compare != null && !compact && <span className="tabular-nums line-through">Retail BHD {money(compare)}</span>}
            {tier && (
              <div className="tabular-nums">
                {tier.min_qty}+ pcs · <span className="font-semibold text-[#1a1430]">{bhd(tier.unit_price_bhd)}</span>
              </div>
            )}
            {!tier && item.has_tiers && !publicTiers && <div>{S.card.volumePrices}</div>}
            {min > 1 && !compact && <div className="tabular-nums">{S.card.min(min)}</div>}
          </div>

          <div className="mt-2.5">
            {selected ? (
              <div>
                <Stepper
                  value={qty}
                  step={step}
                  min={min}
                  size={compact ? 'sm' : 'md'}
                  label={name}
                  onChange={onSetQty}
                  onRemove={onRemove}
                  onValueClick={onKeypad}
                  className="w-full"
                />
                {nudge && !compact && <p className="mt-1 text-[11px] font-medium text-[#6d28d9]">{nudge}</p>}
              </div>
            ) : tellUrl ? (
              <a
                href={tellUrl}
                target="_blank"
                rel="noreferrer"
                className={cn(
                  'flex w-full items-center justify-center gap-1.5 rounded-xl border border-[#e4e0ee] bg-white text-[13px] font-semibold text-[#1a1430] transition hover:bg-[#f7f5fb]',
                  compact ? 'h-10' : 'h-11',
                  RING,
                )}
              >
                <MessageCircle size={15} aria-hidden="true" /> {S.card.tellRep(rep?.first_name || 'us')}
              </a>
            ) : (
              <button
                type="button"
                onClick={onAdd}
                disabled={!canOrder}
                aria-label={canOrder ? `${out ? S.card.backorder : S.card.add} ${defaultQty} × ${name}` : `${name} is sold out`}
                className={cn(
                  'flex w-full items-center justify-center gap-1.5 rounded-xl text-[13px] font-semibold transition duration-150 ease-out active:scale-[.99]',
                  compact ? 'h-10' : 'h-11',
                  RING,
                  canOrder
                    ? out
                      ? 'border border-[#e4e0ee] bg-white text-[#1a1430] hover:border-[#d9d2ee] hover:bg-[#f7f5fb]'
                      : 'bg-[#6d28d9] text-white hover:bg-[#5b21b6]'
                    : 'cursor-not-allowed border border-[#f3c9d2] bg-[#fdecef] text-[#9f1239]',
                )}
              >
                {canOrder ? (
                  <>
                    <Plus size={15} aria-hidden="true" /> {out ? S.card.backorder : S.card.add}
                    {!out && defaultQty > 1 && <span className="tabular-nums opacity-80">· {defaultQty}</span>}
                  </>
                ) : (
                  S.card.soldOut
                )}
              </button>
            )}
          </div>
        </div>
      </div>
    </article>
  )
}
