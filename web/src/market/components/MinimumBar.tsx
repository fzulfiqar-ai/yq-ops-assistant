import { Plus, Check } from 'lucide-react'
import type { GapSuggestion, QuoteMinimum } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { track } from '../lib/events'
import { bhd, productName } from '../lib/format'
import { S } from '../strings'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'

/**
 * The wholesale minimum as a sales engine. Under the minimum: a plum progress bar with "Only
 * BHD 7.000 more to place your order" and the server's gap fillers — one tap adds the suggested
 * quantity and the bar rolls. At the minimum: one quiet line. Never a wall: in `request` mode the
 * order can still be sent as a small order for the rep to confirm.
 */
export function MinimumBar({ minimum, suggestions, compact }: { minimum: QuoteMinimum; suggestions: GapSuggestion[]; compact?: boolean }) {
  const m = useMarket()
  const value = Number(minimum.value_bhd || 0)
  const remaining = Number(minimum.remaining_bhd || 0)
  const pct = value > 0 ? Math.max(0, Math.min(100, ((value - remaining) / value) * 100)) : 100
  if (minimum.met) {
    return (
      <div className={cn('flex items-center gap-1.5 text-ok', compact ? 'text-xs' : 'text-sm')}>
        <Check size={14} aria-hidden="true" /> {S.minimum.met}
      </div>
    )
  }
  return (
    <div>
      <div className={cn('flex items-baseline justify-between gap-2', compact ? 'text-xs' : 'text-sm')}>
        <span className="font-semibold text-ink">{S.minimum.more(bhd(remaining))}</span>
        <span className="shrink-0 tnum text-ink-3">{S.minimum.of(bhd(value))}</span>
      </div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-pill bg-plum-wash" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={S.minimum.title}>
        <div className="h-full rounded-pill bg-plum transition-[width] duration-3 ease-m" style={{ width: `${pct}%` }} />
      </div>
      {suggestions.length > 0 && (
        <ul className={cn('mt-2 divide-y divide-line-2', compact && 'text-sm')}>
          {suggestions.slice(0, compact ? 3 : 6).map((g) => {
            const item = m.itemsByCode.get(g.item_code)
            if (!item) return null
            return (
              <li key={g.item_code} className="flex items-center gap-2.5 py-2">
                <span className="h-10 w-10 shrink-0 overflow-hidden rounded-sm border border-line-2 bg-white">
                  <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={40} imgClassName="p-0.5" iconSize={14} showCaption={false} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-ink">{productName(item)}</span>
                  <span className="block text-xs tnum text-ink-2">
                    {g.qty} × {bhd(g.unit_price_bhd)} · {bhd(g.value_bhd)}
                    {g.closes_gap && <span className="ms-1 font-semibold text-ok">{S.minimum.closes}</span>}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => {
                    m.add(item, g.qty, 'gap_filler')
                    track('rail_click', { item_code: g.item_code, meta: { rail: 'gap', code: g.why } })
                  }}
                  aria-label={`${S.card.add} ${g.qty} — ${productName(item)}`}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-line text-plum transition duration-1 ease-m hover:border-plum hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
                >
                  <Plus size={16} aria-hidden="true" />
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {minimum.mode === 'request' && (
        <p className={cn('mt-2 text-ink-2', compact ? 'text-2xs' : 'text-xs')}>
          {Number(minimum.fee_bhd) > 0 ? S.minimum.requestFee(bhd(minimum.fee_bhd)) : S.minimum.request}
        </p>
      )}
    </div>
  )
}
