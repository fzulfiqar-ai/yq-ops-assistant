import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, MessageCircle, TrendingDown } from 'lucide-react'
import { getStaffCatalog, type ShopItem } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { dropShareText, dropsOf, productShareUrl } from '@/pages/shop/priceDrops'
import { bhd, isSoldOut } from '@/pages/shop/shared'

/**
 * Today's "N products got a lower price" (R7e): the price book's genuine cuts (priceDrops — the
 * marketplace's Was → Now rule), read from the rep's own catalog through the SAME ['staff-catalog']
 * cache as the Catalog tab, so either screen paints the other at once. The deepest five cuts with
 * was → now, each with a WhatsApp share of the product on his storefront link (the order is his),
 * and "See all" opens the catalog filtered to them. What can be sold today is listed first; a
 * sold-out line is still named (its price did go down) but not pushed. No drops, or the catalog did
 * not load: no card. No countdown, no nudge — a price that went down is the whole message.
 */

const TOP = 5
const ICON_BTN = 'grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-border hover:bg-muted'

/** The row's second word: the name when it is not just the code, else the first spec line (as ProductRow). */
function nameOf(i: ShopItem): string {
  return i.display_name && i.display_name !== i.item_code ? i.display_name : (i.spec || '').split('\n')[0]
}

export function PriceDrops({ link }: { link: string }) {
  const catalogQ = useQuery({ queryKey: ['staff-catalog'], queryFn: getStaffCatalog, staleTime: 5 * 60_000, retry: 1 })
  const drops = useMemo(() => dropsOf(catalogQ.data?.items || []), [catalogQ.data])
  if ((catalogQ.isError && !catalogQ.data) || !drops.length) return null
  const top = [...drops.filter((d) => !isSoldOut(d.item)), ...drops.filter((d) => isSoldOut(d.item))].slice(0, TOP)
  const n = drops.length

  return (
    <section aria-label="Price drops" className="overflow-hidden rounded-2xl border border-border bg-card">
      <div className="flex items-center gap-2 px-4 py-3">
        <TrendingDown size={16} className="shrink-0 text-primary" aria-hidden="true" />
        <h2 className="min-w-0 font-display text-[15px] font-bold leading-tight">
          {n} {n === 1 ? 'product' : 'products'} got a lower price
        </h2>
        <Link to="/shop?f=drops" className="-mr-2 ml-auto inline-flex h-10 shrink-0 items-center gap-0.5 rounded-lg px-2 text-[12.5px] font-semibold text-primary hover:bg-muted">
          See all <ChevronRight size={14} aria-hidden="true" />
        </Link>
      </div>
      <ul className="divide-y divide-border border-t border-border">
        {top.map(({ item, drop }) => {
          const url = productShareUrl(link, item.item_code)
          const name = nameOf(item)
          return (
            <li key={item.item_code} className="flex items-center gap-3 px-4 py-2.5">
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="shrink-0 font-display text-[13.5px] font-bold">{item.item_code}</span>
                  {name && <span className="truncate text-[12.5px] text-muted-foreground">{name}</span>}
                </span>
                <span className="block truncate text-[12px] tabular-nums text-muted-foreground">
                  Was {bhd(drop.was)} <span aria-hidden="true">→</span>
                  <span className="sr-only">, </span> <b className="font-semibold text-foreground">now {bhd(drop.now)}</b>
                </span>
              </span>
              <Badge tone="rose" className="shrink-0 tabular-nums">
                ↓{drop.pct}%
              </Badge>
              {isSoldOut(item) ? (
                <Badge tone="grey" className="shrink-0">
                  Sold Out
                </Badge>
              ) : (
                <a
                  href={`https://wa.me/?text=${encodeURIComponent(dropShareText(item, url))}`}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={`Share ${item.item_code} on WhatsApp`}
                  title="Share on WhatsApp"
                  className={cn(ICON_BTN, 'text-[#1d9e50]')}
                >
                  <MessageCircle size={16} aria-hidden="true" />
                </a>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
