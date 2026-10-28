import { Loader2, ShoppingBag, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Cart } from '@/lib/cart'
import type { Quote, ShopItem, StaffCustomer } from '@/lib/shopApi'
import type { BookShop } from '@/pages/sales/lib'
import { ProductImage } from './ProductImage'
import { ShopPill } from './ShopPicker'
import { bhd, HIT, money, RING } from './shared'
import { minimumOf } from './staffOrder'

/**
 * The wholesale minimum as a ruler: how far this cart is from it (the server's quote.minimum when
 * there is one, the list-price estimate until the first quote lands; staffOrder.minimumOf), and
 * "reached" once it is. `compact` is the phone's order bar: only a 3 px line along its top edge (the
 * words share the bar's one line). The fill grows by scaling, not by its width: transform only.
 */
export function MinimumRuler({ total, minBhd, quote, className, compact = false }: { total: number; minBhd: number; quote: Quote | null; className?: string; compact?: boolean }) {
  const m = minimumOf(total, minBhd, quote)
  if (!m) return null
  const bar = (
    <div
      className={cn('w-full overflow-hidden bg-[#E9E4EF]', compact ? 'h-[3px]' : 'mt-1.5 h-1.5 rounded-full')}
      role="progressbar"
      aria-valuenow={Math.round(m.pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={m.met ? `Minimum ${bhd(m.value)} reached` : `${bhd(m.remaining)} to the ${bhd(m.value)} minimum`}
    >
      <div
        className={cn('h-full w-full origin-left transition-transform duration-200 ease-[cubic-bezier(.22,1,.36,1)] motion-reduce:transition-none', !compact && 'rounded-full', m.met ? 'bg-[#137a48]' : 'bg-[#6D4091]')}
        style={{ transform: `scaleX(${m.pct / 100})` }}
      />
    </div>
  )
  if (compact) return <div className={className}>{bar}</div>
  return (
    <div className={className}>
      <div className="flex items-baseline justify-between gap-2 text-[11.5px]">
        <span className={cn('font-medium', m.met ? 'text-[#137a48]' : 'text-[#1A1428]')}>
          {m.met ? `Minimum ${bhd(m.value)} reached` : `${bhd(m.remaining)} to the ${bhd(m.value)} minimum`}
        </span>
      </div>
      {bar}
    </div>
  )
}

/**
 * The docked order slip (Sprint 5, 1280 px and up): the shop, the lines as they are typed, the
 * minimum ruler, the total and Place order — always in view while the table on the left is used.
 * Place order opens the checkout, which already knows the shop.
 */
export function OrderSlip({
  selected,
  row,
  cart,
  itemsByCode,
  quote,
  quoting,
  total,
  minBhd,
  onPickShop,
  onPlace,
  onRemove,
}: {
  selected: StaffCustomer | null
  row: BookShop | null
  cart: Cart
  itemsByCode: Map<string, ShopItem>
  quote: Quote | null
  quoting: boolean
  total: number
  minBhd: number
  onPickShop: () => void
  onPlace: () => void
  /** take a line out (the catalog offers Undo) */
  onRemove: (code: string) => void
}) {
  const quoteLines = new Map((quote?.lines || []).map((l) => [l.item_code, l]))
  return (
    <aside aria-label="Order slip" className="sticky top-6 flex max-h-[calc(100vh-3rem)] flex-col overflow-hidden rounded-[20px] border border-[#E9E4EF] bg-white shadow-[0_12px_40px_-28px_rgba(24,16,48,.45)]">
      <div className="border-b border-[#F3F0F6] p-3">
        <ShopPill selected={selected} row={row} onOpen={onPickShop} />
      </div>
      <div className="flex items-baseline justify-between px-4 pb-1 pt-3">
        <h2 className="font-display text-[14px] font-bold text-[#1A1428]">Order slip</h2>
        <span className="text-[11.5px] tabular-nums text-[#6b6480]">
          {cart.items} {cart.items === 1 ? 'line' : 'lines'} · {cart.units} pcs
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2">
        {cart.lines.length === 0 ? (
          <p className="px-2 py-8 text-center text-[12.5px] leading-relaxed text-[#6b6480]">
            Type a code in the search and press Enter,
            <br />
            or add a list: <b className="font-semibold text-[#1A1428]">C18 3, UK15 6</b>
          </p>
        ) : (
          <ul className="divide-y divide-[#F3F0F6]">
            {cart.lines.map((l) => {
              const it = itemsByCode.get(l.item_code)
              const q = quoteLines.get(l.item_code)
              return (
                <li key={l.item_code} className="flex items-center gap-2.5 px-2 py-2">
                  <span className="h-9 w-9 shrink-0 overflow-hidden rounded-lg border border-[#E9E4EF]">
                    <ProductImage srcs={[it?.thumb_url, it?.product_image_url]} alt="" width={36} height={36} className="h-full w-full" imgClassName="p-0.5" iconSize={14} showCaption={false} />
                  </span>
                  <span className="min-w-0 flex-1 leading-tight">
                    <span className="block truncate font-display text-[12.5px] font-bold text-[#1A1428]">{l.item_code}</span>
                    <span className={cn('block text-[11px] tabular-nums', q?.unavailable ? 'font-medium text-[#9f1239]' : 'text-[#6b6480]')}>
                      {q?.unavailable ? q.blocked_reason || 'Cannot be ordered' : `${l.qty} × ${money(q?.unit_price_bhd ?? it?.price_bhd)}`}
                    </span>
                  </span>
                  <span className="shrink-0 font-display text-[12.5px] font-bold tabular-nums text-[#1A1428]">{q?.unavailable ? '—' : money(q?.line_total_bhd ?? (Number(it?.price_bhd) || 0) * l.qty)}</span>
                  <button type="button" onClick={() => onRemove(l.item_code)} aria-label={`Remove ${l.item_code}`} className={cn('relative grid h-8 w-8 shrink-0 place-items-center rounded-lg text-[#a8a2bb] hover:bg-[#fdecef] hover:text-[#9f1239]', HIT, RING)}>
                    <X size={14} aria-hidden="true" />
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
      <div className="border-t border-[#F3F0F6] p-4">
        <MinimumRuler total={total} minBhd={minBhd} quote={quote} />
        <div className="mt-3 flex items-baseline justify-between">
          <span className="flex items-center gap-1.5 text-[12px] font-medium text-[#6b6480]">
            Total {quoting && <Loader2 size={11} className="animate-spin" aria-label="Updating" />}
          </span>
          <span className="font-display text-[20px] font-extrabold tracking-[-0.015em] tabular-nums text-[#1A1428]">{bhd(total)}</span>
        </div>
        <button
          type="button"
          onClick={onPlace}
          disabled={!cart.lines.length}
          className={cn(
            'mt-3 flex h-12 w-full items-center justify-center gap-2 rounded-xl text-[14px] font-semibold transition duration-150 ease-out',
            RING,
            cart.lines.length ? 'bg-[#6D4091] text-white hover:bg-[#5A3478]' : 'cursor-not-allowed bg-[#f0eef6] text-[#a8a2bb]',
          )}
        >
          <ShoppingBag size={16} aria-hidden="true" /> Place order
        </button>
        {!selected && cart.lines.length > 0 && <p className="mt-2 text-center text-[11px] text-[#6b6480]">Pick the shop first, or type it at checkout.</p>}
      </div>
    </aside>
  )
}
