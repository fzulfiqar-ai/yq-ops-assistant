import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Sheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { bhd, minQtyOf, RING, stepOf } from './shared'

/** The unit price at a quantity: the deepest volume tier it reaches, else the trade price. */
function unitAt(item: ShopItem, qty: number): number | null {
  const tiers = (item.tiers || []).filter((t) => qty >= t.min_qty).sort((a, b) => b.min_qty - a.min_qty)
  if (tiers.length) return Number(tiers[0].unit_price_bhd)
  return item.price_bhd != null ? Number(item.price_bhd) : null
}

/**
 * The keypad quantity sheet (ported from the marketplace's QtySheet, in the salesman app's own
 * style). A rep types "48" instead of tapping "+" forty-eight times: a big numeric field that opens
 * focused and selected, so the first digit replaces the number it was seeded with; quick picks (pack
 * multiples, or 6 / 12 / 24 / 48 / 100); the tier ladder with the one this quantity reaches lit.
 * Anything under the minimum is refused with the reason, never rounded up silently (the server
 * enforces the same minimum).
 */
export function QtySheet({
  item,
  value,
  onApply,
  onRemove,
  onClose,
}: {
  item: ShopItem
  value: number
  onApply: (n: number) => void
  onRemove: () => void
  onClose: () => void
}) {
  const step = stepOf(item)
  const min = minQtyOf(item)
  const [draft, setDraft] = useState(String(value || min))
  const [seen, setSeen] = useState(value)
  if (seen !== value) {
    setSeen(value)
    setDraft(String(value || min))
  }
  const presets = Array.from(new Set((step > 1 ? [1, 2, 4, 6, 10] : [6, 12, 24, 48, 100]).map((n) => (step > 1 ? n * step : n)).filter((n) => n >= min)))
  const n = Math.max(0, Math.floor(Number(draft) || 0))
  const valid = n >= min && n <= 9999
  const unit = valid ? unitAt(item, n) : null
  const apply = () => {
    if (!valid) return
    onApply(n)
    onClose()
  }
  const why = n < min ? `Minimum ${min} pcs` : n > 9999 ? 'That is more than one order can take' : ''
  return (
    <Sheet
      open
      onClose={onClose}
      variant="dialog"
      title="How many?"
      subtitle={`${item.item_code}${min > 1 ? ` · minimum ${min}` : ''}${step > 1 ? ` · packs of ${step}` : ''}`}
      footer={
        <div className="flex gap-2">
          {value > 0 && (
            <button
              type="button"
              onClick={() => {
                onRemove()
                onClose()
              }}
              className={cn('inline-flex h-12 items-center gap-1.5 rounded-xl border border-[#f3c9d2] bg-[#fdecef] px-4 text-[13px] font-semibold text-[#9f1239]', RING)}
            >
              <Trash2 size={15} aria-hidden="true" /> Remove
            </button>
          )}
          <button
            type="button"
            disabled={!valid}
            onClick={apply}
            className={cn(
              'flex h-12 flex-1 items-center justify-center gap-2 rounded-xl text-[14px] font-semibold transition duration-150 ease-out',
              RING,
              valid ? 'bg-[#6D4091] text-white hover:bg-[#5A3478]' : 'cursor-not-allowed bg-[#f0eef6] text-[#a8a2bb]',
            )}
          >
            Set quantity
            {valid && unit != null ? <span className="tabular-nums opacity-90">· {bhd(unit * n)}</span> : null}
          </button>
        </div>
      }
    >
      <div className="px-4 py-4 sm:px-5">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value.replace(/\D/g, '').slice(0, 4))}
          onKeyDown={(e) => e.key === 'Enter' && apply()}
          onFocus={(e) => e.currentTarget.select()}
          inputMode="numeric"
          pattern="[0-9]*"
          autoFocus
          aria-label={`Quantity of ${item.item_code}`}
          className="h-16 w-full rounded-2xl border border-[#E2DCEA] bg-white text-center font-display text-[30px] font-extrabold tabular-nums text-[#1A1428] outline-none focus:border-[#6D4091] focus:ring-2 focus:ring-[#6D4091]/15"
        />
        {!valid && draft !== '' && <p className="mt-1.5 text-center text-[12px] font-medium text-[#9f1239]">{why}</p>}
        <div className="mt-4 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[#6b6480]">Quick picks</div>
        <div className="mt-2 grid grid-cols-5 gap-2">
          {presets.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setDraft(String(p))}
              aria-pressed={n === p}
              className={cn(
                'h-11 rounded-xl border text-[14px] font-semibold tabular-nums transition duration-150 ease-out',
                RING,
                n === p ? 'border-[#6D4091] bg-[#EEE8F4] text-[#5A3478]' : 'border-[#E2DCEA] bg-white text-[#1A1428] hover:bg-[#f7f5fb]',
              )}
            >
              {p}
            </button>
          ))}
        </div>
        {(item.tiers || []).length > 0 && (
          <ul className="mt-4 space-y-1 text-[12px] text-[#6b6480]">
            {(item.tiers || []).map((t) => (
              <li key={t.min_qty} className={cn('flex justify-between rounded-lg px-2 py-1 tabular-nums', n >= t.min_qty && 'bg-[#EEE8F4] font-semibold text-[#5A3478]')}>
                <span>{t.min_qty}+ pcs</span>
                <span>{bhd(t.unit_price_bhd)} each</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Sheet>
  )
}
