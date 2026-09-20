import { useState } from 'react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { bhd, minQtyOf, stepOf, unitAt, productName } from '../lib/format'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Sheet } from '../ui/Sheet'

/** Type a quantity: a big numeric field, quick picks (pack multiples or 6/12/24/48/100), the tier ladder. */
export function QtySheet({ item, value, onApply, onRemove, onClose }: { item: ShopItem; value: number; onApply: (n: number) => void; onRemove: () => void; onClose: () => void }) {
  const step = stepOf(item)
  const min = minQtyOf(item)
  const [draft, setDraft] = useState(String(value || min))
  const [valueSeen, setValueSeen] = useState(value)
  if (valueSeen !== value) {
    setValueSeen(value)
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
  return (
    <Sheet
      open
      onClose={onClose}
      variant="dialog"
      title={S.qty.title}
      subtitle={`${item.item_code} · ${productName(item)}${min > 1 ? ` · ${S.card.min(min)}` : ''}${step > 1 ? ` · ${S.card.packs(step)}` : ''}`}
      footer={
        <div className="flex gap-2">
          {value > 0 && (
            <Button
              variant="danger"
              size="lg"
              onClick={() => {
                onRemove()
                onClose()
              }}
            >
              {S.qty.remove}
            </Button>
          )}
          <Button size="lg" className="flex-1" disabled={!valid} onClick={apply}>
            {S.qty.apply}
            {valid && unit != null ? <span className="tnum opacity-90">· {bhd(unit * n)}</span> : null}
          </Button>
        </div>
      }
    >
      <div className="px-4 py-4 md:px-5">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value.replace(/\D/g, '').slice(0, 4))}
          onKeyDown={(e) => e.key === 'Enter' && apply()}
          inputMode="numeric"
          pattern="[0-9]*"
          autoFocus
          aria-label={S.qty.title}
          className="h-16 w-full rounded-md border border-line bg-surface text-center font-display text-3xl font-extrabold tnum text-ink outline-none focus:border-plum focus:ring-2 focus:ring-plum/15"
        />
        {!valid && draft !== '' && <p className="mt-1.5 text-center text-xs font-medium text-bad">{n < min ? S.card.min(min) : S.qty.tooMany}</p>}
        <div className="mt-4 text-xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.qty.presets}</div>
        <div className="mt-2 grid grid-cols-5 gap-2">
          {presets.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setDraft(String(p))}
              aria-pressed={n === p}
              className={cn(
                'h-11 rounded-sm border text-sm font-semibold tnum transition duration-1 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
                n === p ? 'border-plum bg-plum-soft text-plum-ink' : 'border-line bg-surface text-ink hover:bg-plum-wash',
              )}
            >
              {p}
            </button>
          ))}
        </div>
        {(item.tiers || []).length > 0 && (
          <ul className="mt-4 space-y-1 text-xs text-ink-2">
            {(item.tiers || []).map((t) => (
              <li key={t.min_qty} className={cn('flex justify-between rounded-xs px-2 py-1 tnum', n >= t.min_qty && 'bg-plum-soft font-semibold text-plum-ink')}>
                <span>{S.card.pcsPlus(t.min_qty)}</span>
                <span>
                  {bhd(t.unit_price_bhd)} {S.cart.each}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Sheet>
  )
}
