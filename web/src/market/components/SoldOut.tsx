import type { ShopItem } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { productName } from '../lib/format'
import { S } from '../strings'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { TellBackButton } from './RestockAsk'

/**
 * The sold-out rule, drawn: sold-out lines stay on every listing, after the lines a shop can order
 * today (lib/facets keeps that order), and a quiet ruled heading — "Not in stock now · N lines" —
 * marks where they begin. It is a heading, never role=separator: a separator's words are
 * presentational, so a screen reader would lose the count and the group boundary. The rules are
 * the page's own hairline (.shelf-divider in market.css); the words are the secondary ink.
 */
export function SoldOutDivider({ count, as: Tag = 'h3', className }: { count: number; as?: 'h2' | 'h3' | 'h4'; className?: string }) {
  return <Tag className={cn('shelf-divider text-xs font-semibold text-ink-2', className)}>{S.shop.notInStock(count)}</Tag>
}

/**
 * The lines of a reorder that cannot be ordered today (sold out while the shop takes no backorder):
 * shown greyed after the divider, each with "Tell me when back" — never dropped without a word.
 * Order again on Home and on the Restock page, and the order's own page, share it.
 */
export function SoldOutRows({ items, className }: { items: ShopItem[]; className?: string }) {
  const { soldOutLabel } = useMarket()
  if (!items.length) return null
  return (
    <div className={className}>
      <SoldOutDivider count={items.length} />
      <ul className="mt-1 divide-y divide-line-2">
        {items.map((it) => (
          <li key={it.item_code} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2.5">
            <span className="h-10 w-10 shrink-0 overflow-hidden rounded-sm border border-line-2 bg-white">
              <ProductImage item={it} alt="" sizes={SIZES_THUMB} size={40} imgClassName="p-0.5 opacity-45 saturate-50" iconSize={14} showCaption={false} />
            </span>
            <span className="min-w-[8rem] flex-1">
              <span className="block truncate text-sm font-semibold text-ink-2">{productName(it)}</span>
              <span className="block text-xs tnum text-ink-2">
                {it.item_code} · {soldOutLabel}
              </span>
            </span>
            <TellBackButton item={it} />
          </li>
        ))}
      </ul>
    </div>
  )
}
