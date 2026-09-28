import { memo, useState, type KeyboardEvent } from 'react'
import { Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { ProductImage } from './ProductImage'
import { priceDropOf, wasText } from './priceDrops'
import { bhd, isSoldOut, minQtyOf, RING, stepOf, stockBand } from './shared'

export interface ProductRowProps {
  item: ShopItem
  qty: number
  allowBackorder: boolean
  highlighted?: boolean
  eagerImage?: boolean
  onOpen: () => void
  /** set the line's quantity (0 removes it) */
  onSetQty: (qty: number) => void
  /** the keypad sheet (phone / tablet) */
  onQtyClick: () => void
}

/** The desktop quantity cell: type a number, Enter (or leaving the field) sets it; empty or 0 removes. */
function QtyInput({ item, qty, disabled, onSetQty }: { item: ShopItem; qty: number; disabled: boolean; onSetQty: (n: number) => void }) {
  const [draft, setDraft] = useState(qty ? String(qty) : '')
  const [seen, setSeen] = useState(qty)
  if (seen !== qty) {
    setSeen(qty)
    setDraft(qty ? String(qty) : '')
  }
  const min = minQtyOf(item)
  const commit = () => {
    const n = Math.floor(Number(draft) || 0)
    if (n === qty) return
    if (n <= 0) onSetQty(0)
    else onSetQty(Math.max(min, Math.min(9999, n)))
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      commit()
    } else if (e.key === 'Escape') {
      setDraft(qty ? String(qty) : '')
    }
  }
  return (
    <input
      value={draft}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value.replace(/\D/g, '').slice(0, 4))}
      onBlur={commit}
      onKeyDown={onKey}
      onFocus={(e) => e.currentTarget.select()}
      inputMode="numeric"
      placeholder="0"
      aria-label={`Quantity of ${item.item_code}${min > 1 ? `, minimum ${min}` : ''}`}
      className={cn(
        'h-10 w-[4.5rem] rounded-xl border bg-white text-center text-[14px] font-semibold tabular-nums text-[#1A1428] outline-none transition duration-150 ease-out placeholder:text-[#c9c3d6] focus:border-[#6D4091] focus:ring-2 focus:ring-[#6D4091]/15',
        qty > 0 ? 'border-[#6D4091]/45 bg-[#F6F2FA]' : 'border-[#E2DCEA]',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    />
  )
}

/**
 * One product as a list row (Sprint 5: list mode is the salesman's default — the grid is for
 * showing a customer). Phone: a 72 px row — photo, code, stock band, price, then the quantity (a
 * tap opens the keypad) and "+". From 1280 px the same row is a line of the dense order-entry
 * table: code and name · stock band · price · a typed quantity · "+". The exact free-to-sell number
 * is only in the product sheet; the row carries the band. A genuine price drop (priceDrops) adds
 * "Was 1.500 · ↓20%" under the price and a "Price drop" chip in the desk's stock cell; on a phone the
 * price line says "↓20%" (the full words in its title, the sheet and the grid card) — a second chip on
 * the code line, or the whole "Was …" beside the price, has no room at 390 px.
 */
export const ProductRow = memo(function ProductRow({ item, qty, allowBackorder, highlighted, eagerImage, onOpen, onSetQty, onQtyClick }: ProductRowProps) {
  const out = isSoldOut(item)
  const canOrder = !out || allowBackorder
  const band = stockBand(item)
  const step = stepOf(item)
  const min = minQtyOf(item)
  const name = item.display_name && item.display_name !== item.item_code ? item.display_name : (item.spec || '').split('\n')[0]
  const drop = priceDropOf(item)
  const plus = () => onSetQty(qty > 0 ? Math.min(9999, qty + step) : min)
  return (
    <div
      data-code={item.item_code}
      className={cn(
        'flex h-[72px] items-center gap-3 px-3 transition-colors duration-150 xl:grid xl:h-14 xl:grid-cols-[40px_minmax(0,1fr)_104px_112px_80px_44px] xl:gap-4 xl:px-4',
        qty > 0 && 'bg-[#FBF9FD]',
        // the keyboard highlight is a desk thing (Enter adds it from 1280 px); a phone never shows it
        highlighted && 'xl:bg-[#EEE8F4] xl:shadow-[inset_3px_0_0_#6D4091]',
      )}
    >
      <button type="button" onClick={onOpen} tabIndex={-1} aria-hidden="true" className="h-14 w-14 shrink-0 overflow-hidden rounded-xl border border-[#E9E4EF] bg-white xl:h-10 xl:w-10 xl:rounded-lg">
        <ProductImage
          srcs={[item.thumb_urls?.['160'], item.thumb_url, item.product_image_url]}
          alt=""
          width={56}
          height={56}
          eager={eagerImage}
          className="h-full w-full"
          imgClassName={cn('p-1', out && 'opacity-45 saturate-50')}
          iconSize={18}
          showCaption={false}
        />
      </button>

      <button type="button" onClick={onOpen} className={cn('min-w-0 flex-1 rounded-lg text-left', RING)} aria-label={`${item.item_code}${drop ? `, price drop ${drop.pct}%` : ''} — details`}>
        <span className="flex items-center gap-1.5">
          <span className={cn('truncate font-display text-[13.5px] font-bold leading-tight tracking-[-0.01em]', out ? 'text-[#8d86a0]' : 'text-[#1A1428]')}>{item.item_code}</span>
          <Badge tone={band.tone} dot className="shrink-0 xl:hidden">
            {band.label}
          </Badge>
        </span>
        {name && <span className="mt-0.5 block truncate text-[11.5px] leading-tight text-[#6b6480]">{name}</span>}
        <span className="mt-0.5 flex min-w-0 items-center font-display text-[12.5px] font-bold tabular-nums text-[#6D4091] xl:hidden">
          <span className="shrink-0">{item.price_bhd != null ? bhd(item.price_bhd) : 'Price on request'}</span>
          {/* the cut only: at 390 px "Was 1.500" is cut to "Was …" (measured) — the sheet and the grid card carry it */}
          {drop && (
            <span title={wasText(drop.was, drop.pct)} className="ml-1.5 min-w-0 truncate font-sans text-[10.5px] font-semibold text-[#9f1239]">
              ↓{drop.pct}%
            </span>
          )}
          {min > 1 && <span className="ml-1.5 shrink-0 font-sans text-[10.5px] font-medium text-[#6b6480]">min {min}</span>}
        </span>
      </button>

      <span className="hidden xl:flex xl:flex-col xl:items-start xl:gap-0.5">
        <Badge tone={band.tone} dot>
          {band.label}
        </Badge>
        {drop && <Badge tone="rose">Price drop</Badge>}
      </span>
      <span className="hidden text-right font-display text-[13.5px] font-bold tabular-nums text-[#6D4091] xl:block">
        {item.price_bhd != null ? bhd(item.price_bhd) : '—'}
        {drop && <span className="block truncate font-sans text-[10.5px] font-semibold text-[#9f1239]">{wasText(drop.was, drop.pct)}</span>}
        {min > 1 && <span className="block font-sans text-[10.5px] font-medium text-[#6b6480]">min {min}</span>}
      </span>

      {/* phone / tablet: the quantity is a keypad button */}
      <button
        type="button"
        onClick={onQtyClick}
        disabled={!canOrder && qty === 0}
        aria-label={qty > 0 ? `${qty} of ${item.item_code} — change quantity` : `Type a quantity of ${item.item_code}`}
        className={cn(
          'h-11 min-w-[3rem] shrink-0 rounded-xl border px-2 text-[14px] font-semibold tabular-nums transition duration-150 ease-out xl:hidden',
          RING,
          qty > 0 ? 'border-[#6D4091]/45 bg-[#EEE8F4] text-[#5A3478]' : 'border-[#E2DCEA] bg-white text-[#a8a2bb]',
          !canOrder && qty === 0 && 'cursor-not-allowed opacity-50',
        )}
      >
        {qty > 0 ? qty : 'Qty'}
      </button>
      {/* desktop: a typed quantity */}
      <span className="hidden xl:block">
        <QtyInput item={item} qty={qty} disabled={!canOrder && qty === 0} onSetQty={onSetQty} />
      </span>

      <button
        type="button"
        onClick={plus}
        disabled={!canOrder}
        aria-label={qty > 0 ? `Add ${step} more ${item.item_code}` : `Add ${item.item_code}`}
        title={out && canOrder ? 'Backorder — the rep confirms the ETA' : undefined}
        className={cn(
          'grid h-11 w-11 shrink-0 place-items-center rounded-xl transition duration-150 ease-out active:scale-[.97] xl:h-10 xl:w-10',
          RING,
          !canOrder
            ? 'cursor-not-allowed bg-[#f4f3f8] text-[#c9c3d6]'
            : out
              ? 'border border-[#E2DCEA] bg-white text-[#1A1428] hover:bg-[#f7f5fb]'
              : 'bg-[#6D4091] text-white hover:bg-[#5A3478]',
        )}
      >
        <Plus size={18} aria-hidden="true" />
      </button>
    </div>
  )
})
