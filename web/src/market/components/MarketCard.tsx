import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { Check, Eye, MessageCircle, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { useMarket } from '../MarketContext'
import { track } from '../lib/events'
import { badgeMeta, bhd, cardBadges, isOut, minQtyOf, money, nextTier, stepOf, stockMeta, unitAt, priceAnchor, productDetail, productName } from '../lib/format'
import { useShell } from '../shell/ShellContext'
import { useCartQty } from '../store/cart'
import { S } from '../strings'
import { Chip } from '../ui/Chip'
import { ProductImage, SIZES_GRID, SIZES_RAIL, SIZES_THUMB } from '../ui/ProductImage'
import { Stepper } from '../ui/Stepper'
import { useToast } from '../ui/Toast'
import { QtySheet } from './QtySheet'

/**
 * The product card, three densities:
 *   grid    — the shelf: photo tile, name, code · stock, price, quantity rules, first tier, Add
 *   compact — rails and the mega-nav: photo, name, price, Add
 *   list    — mission mode (Shop/Search density toggle, search rows): one line per SKU
 *
 * One action. `Add · 12` (the remembered quantity, rounded to the pack) → "✓ Added" for 600 ms →
 * the stepper, with a plum border on the card. Tapping the number opens the keypad. Below the
 * minimum the stepper removes the line — with an Undo toast. Every add also bumps the cart badge
 * and rolls the cart total (those subscribe to the store themselves). No fly-to-cart.
 *
 * Memoised on the item: an add re-renders this card (its own qty subscription) and the cart
 * surfaces, not the whole grid.
 */

export type CardVariant = 'grid' | 'compact' | 'list'

export interface MarketCardProps {
  item: ShopItem
  variant?: CardVariant
  /** analytics: which rail / surface the card sits in */
  from?: string
  /** first-screen photos: eager + high priority */
  priority?: boolean
  /** override the remembered quantity (Order again rows) */
  presetQty?: number
  className?: string
}

const ADDED_MS = 600

export const MarketCard = memo(function MarketCard({ item, variant = 'grid', from, priority, presetQty, className }: MarketCardProps) {
  const m = useMarket()
  const { openProduct, viewport } = useShell()
  const toast = useToast()
  const qty = useCartQty(item.item_code)
  const [added, setAdded] = useState(false)
  const [keypad, setKeypad] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const imgWrap = useRef<HTMLDivElement>(null)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const out = isOut(item)
  const stock = stockMeta(item.stock_status)
  const step = stepOf(item)
  const min = minQtyOf(item)
  const name = productName(item)
  const showCode = name.toUpperCase() !== item.item_code.toUpperCase()
  const defaultQty = presetQty && presetQty > 0 ? presetQty : m.defaultQty(item)
  const anchor = priceAnchor(item, m.showCompare)
  const compare = anchor?.was ?? null
  const savePct = anchor?.pct ?? null
  const badges = cardBadges(item, variant === 'compact' ? 1 : 2)
  const tiers = m.publicTiers ? item.tiers || [] : []
  const firstTier = tiers[0]
  const canOrder = !out || m.allowBackorder
  const selected = qty > 0
  const nudgeTier = selected ? nextTier(item, qty) : null
  const tellRep = out && !m.allowBackorder && m.rep?.whatsapp_url
  const tellUrl = tellRep ? `${m.rep!.whatsapp_url!.split('?text=')[0]}?text=${encodeURIComponent(`Hello ${m.rep!.first_name || ''}, please tell me when ${item.item_code} (${name}) is back in stock.`)}` : null
  const spec = productDetail(item)

  const open = useCallback(() => {
    if (from) track('rail_click', { item_code: item.item_code, meta: { rail: from } })
    const phone = viewport === 'phone'
    const img = imgWrap.current?.querySelector('img')
    const vt = phone && typeof document !== 'undefined' && 'startViewTransition' in document && Boolean(img)
    if (vt && img) {
      img.style.viewTransitionName = 'product-photo'
      window.setTimeout(() => {
        img.style.viewTransitionName = ''
      }, 900)
    }
    openProduct(item.item_code, from, vt)
  }, [from, item.item_code, openProduct, viewport])

  const add = useCallback(() => {
    m.add(item, defaultQty, from)
    setAdded(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setAdded(false), ADDED_MS)
    try {
      navigator.vibrate?.(8)
    } catch {
      /* not supported */
    }
  }, [m, item, defaultQty, from])

  const removeWithUndo = useCallback(() => {
    const last = qty
    m.remove(item.item_code)
    toast(S.card.removed(name), { kind: 'info', action: { label: S.card.undo, onClick: () => m.setQty(item, last) } })
  }, [m, item, qty, name, toast])

  const setQty = useCallback((n: number) => m.setQty(item, n), [m, item])

  /* ── the one action ── */
  const action = (size: 'sm' | 'md') => {
    if (added) {
      return (
        <div aria-live="polite" className={cn('flex w-full items-center justify-center gap-1.5 rounded-sm bg-plum text-sm font-semibold text-white', size === 'sm' ? 'h-10' : 'h-11')}>
          <Check size={16} aria-hidden="true" /> {S.card.added}
        </div>
      )
    }
    if (selected) {
      return <Stepper value={qty} step={step} min={min} size={size} label={name} onChange={setQty} onRemove={removeWithUndo} onValueClick={() => setKeypad(true)} className="w-full" />
    }
    if (tellUrl) {
      return (
        <a href={tellUrl} target="_blank" rel="noreferrer" className={cn('flex w-full items-center justify-center gap-1.5 rounded-sm border border-line bg-surface text-sm font-semibold text-ink transition duration-1 ease-m hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', size === 'sm' ? 'h-10' : 'h-11')}>
          <MessageCircle size={15} aria-hidden="true" /> {S.card.tellRep(m.rep?.first_name || 'us')}
        </a>
      )
    }
    return (
      <button
        type="button"
        onClick={add}
        disabled={!canOrder}
        aria-label={canOrder ? `${out ? S.card.backorder : S.card.add}${!out && defaultQty > 1 ? ` · ${defaultQty}` : ''} — ${name}` : `${S.card.soldOut} — ${name}`}
        className={cn(
          'flex w-full items-center justify-center gap-1.5 rounded-sm text-sm font-semibold transition duration-1 ease-m active:scale-[.985] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2',
          size === 'sm' ? 'h-10' : 'h-11',
          canOrder ? (out ? 'border border-line bg-surface text-ink hover:border-ink/25 hover:bg-plum-wash' : 'bg-plum text-white shadow-1 hover:bg-plum-deep') : 'cursor-not-allowed border border-bad/20 bg-bad-soft text-bad',
        )}
      >
        {canOrder ? (
          <>
            <Plus size={15} aria-hidden="true" /> {out ? S.card.backorder : S.card.add}
            {!out && defaultQty > 1 && <span className="tnum opacity-80">· {defaultQty}</span>}
          </>
        ) : (
          S.card.soldOut
        )}
      </button>
    )
  }

  const keypadEl = keypad && <QtySheet item={item} value={qty} onApply={setQty} onRemove={removeWithUndo} onClose={() => setKeypad(false)} />

  /* ── list row ── */
  if (variant === 'list') {
    return (
      <article className={cn('flex items-center gap-3 border-b border-line-2 py-2.5', selected && 'bg-plum-wash/60 -mx-2 px-2 rounded-sm', className)}>
        <button type="button" onClick={open} className="h-14 w-14 shrink-0 overflow-hidden rounded-sm border border-line-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70" aria-label={name}>
          <div ref={imgWrap}>
            <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={56} imgClassName={cn('p-1', out && 'opacity-45 saturate-50')} iconSize={18} showCaption={false} />
          </div>
        </button>
        <button type="button" onClick={open} className="min-w-0 flex-1 text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 rounded-xs">
          <div className="line-clamp-1 text-sm font-semibold text-ink">{name}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-ink-2">
            {showCode && <span className="tnum">{item.item_code}</span>}
            <span className={cn('inline-flex items-center gap-1', stock.tone === 'ok' ? 'text-ok' : stock.tone === 'warn' ? 'text-warn' : 'text-bad')}>
              <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
              {stock.label}
            </span>
            {firstTier && <span className="tnum">{S.card.tier(firstTier.min_qty, money(firstTier.unit_price_bhd))}</span>}
          </div>
        </button>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <span className="font-display text-sm font-bold tnum text-ink">{item.price_bhd != null ? bhd(selected ? (unitAt(item, qty) ?? Number(item.price_bhd)) * qty : item.price_bhd) : S.card.priceOnRequest}</span>
          <div className="w-[7.5rem]">{action('sm')}</div>
        </div>
        {keypadEl}
      </article>
    )
  }

  const compact = variant === 'compact'

  return (
    <article
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-lg border bg-surface transition duration-2 ease-m',
        compact ? 'cv-compact w-[clamp(9.5rem,44vw,12.5rem)] shrink-0 lg:w-auto' : priority ? '' : 'cv-card',
        selected ? 'border-plum/50 shadow-[0_0_0_1px_hsl(var(--m-plum)/.18),var(--m-shadow-2)]' : 'border-line shadow-1 hover:-translate-y-0.5 hover:border-ink/15 hover:shadow-2',
        className,
      )}
    >
      <div className="relative">
      <button type="button" onClick={open} className="block w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70" aria-label={`${S.card.quickView}: ${name}`}>
        <div ref={imgWrap}>
          <ProductImage
            item={item}
            alt={name}
            sizes={compact ? SIZES_RAIL : SIZES_GRID}
            size={compact ? 200 : 320}
            priority={priority}
            eager={priority}
            className="w-full"
            imgClassName={cn('p-3 transition-transform duration-3 ease-m group-hover:scale-[1.03]', out && 'opacity-45 saturate-50')}
            iconSize={compact ? 28 : 40}
            showCaption={!compact}
          />
        </div>
      </button>
      <div className="pointer-events-none absolute inset-0" aria-hidden="true">
        {badges[0] && (
          <span className="absolute start-2.5 top-2.5">
            <Chip tone={badgeMeta(badges[0]).tone}>{badgeMeta(badges[0]).label}</Chip>
          </span>
        )}
        {savePct != null && savePct > 0 && (
          <span className="absolute end-2.5 top-2.5">
            <Chip tone="ink">{S.card.save(savePct)}</Chip>
          </span>
        )}
        {out && compact && (
          <span className="absolute inset-x-2.5 bottom-2.5">
            <Chip tone="bad" className="w-full justify-center">
              {S.card.soldOut}
            </Chip>
          </span>
        )}
        {/* desktop hover reveal */}
        <span className="pointer-events-none absolute inset-x-0 bottom-2.5 hidden justify-center opacity-0 transition duration-2 ease-m group-hover:opacity-100 lg:flex">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-ink/85 px-3 py-1.5 text-xs font-semibold text-white shadow-2 backdrop-blur">
            <Eye size={13} aria-hidden="true" /> {S.card.quickView}
          </span>
        </span>
      </div>
      </div>

      <div className={cn('flex flex-1 flex-col border-t border-line-2', compact ? 'p-2.5' : 'p-3')}>
        <button type="button" onClick={open} className="-my-1 min-h-[2.5rem] rounded-xs py-1 text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
          <h3 className={cn('line-clamp-2 font-display font-bold leading-[1.2] text-ink', compact ? 'text-xs' : 'text-sm')}>{name}</h3>
        </button>
        {!compact && (
          <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-ink-2">
            {showCode && <span className="tnum">{item.item_code}</span>}
            {showCode && <span aria-hidden="true">·</span>}
            <span className={cn('inline-flex items-center gap-1 font-medium', stock.tone === 'ok' ? 'text-ok' : stock.tone === 'warn' ? 'text-warn' : 'text-bad')}>
              <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
              {stock.label}
            </span>
            {badges[1] && <Chip tone={badgeMeta(badges[1]).tone}>{badgeMeta(badges[1]).label}</Chip>}
          </div>
        )}
        {!compact && spec && <p className="mt-0.5 line-clamp-1 text-xs text-ink-3">{spec}</p>}

        <div className={cn('mt-auto', compact ? 'pt-1.5' : 'pt-2.5')}>
          <div className="flex items-baseline gap-1.5">
            <span className={cn('font-display font-extrabold leading-none tnum text-plum-ink', compact ? 'text-sm' : 'text-md')}>{item.price_bhd != null ? bhd(item.price_bhd) : S.card.priceOnRequest}</span>
            {item.price_bhd != null && !compact && <span className="text-2xs text-ink-3">{S.card.perPiece}</span>}
          </div>
          {!compact && (
            <div className="mt-1 min-h-[1rem] text-xs leading-[1.35] text-ink-2">
              {compare != null && (
                <span className="tnum">
                  {anchor?.kind === 'was' ? S.card.was : S.card.retail} <s>BHD {money(compare)}</s>
                </span>
              )}
              {(min > 1 || step > 1) && <div className="tnum">{[min > 1 ? S.card.min(min) : null, step > 1 ? S.card.packs(step) : null].filter(Boolean).join(' · ')}</div>}
              {firstTier ? (
                <div className="tnum">
                  {firstTier.min_qty}+ pcs → <span className="font-semibold text-ink">{bhd(firstTier.unit_price_bhd)}</span>
                  {tiers.length > 1 && (
                    <button type="button" onClick={open} className="ms-1.5 font-semibold text-plum hover:underline">
                      {S.card.breaks(tiers.length)}
                    </button>
                  )}
                </div>
              ) : item.has_tiers && !m.publicTiers ? (
                <div>{S.card.volumePrices}</div>
              ) : null}
            </div>
          )}
          <div className={cn(compact ? 'mt-2' : 'mt-2.5')}>{action(compact ? 'sm' : 'md')}</div>
          {!compact && nudgeTier && <p className="mt-1.5 text-xs font-medium text-plum">{S.card.nudge(nudgeTier.min_qty - qty, money(nudgeTier.unit_price_bhd))}</p>}
        </div>
      </div>
      {keypadEl}
    </article>
  )
})
