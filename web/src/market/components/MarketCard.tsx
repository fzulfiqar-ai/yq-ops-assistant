import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, Eye, Heart, MessageCircle, Plus, Store } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ShopItem } from '@/lib/shopApi'
import { useMarket } from '../MarketContext'
import { deviceId, readCustomer } from '../lib/device'
import { track } from '../lib/events'
import { postRestock } from '../lib/marketApi'
import { badgeMeta, bhd, cardBadges, isOut, marginOf, minQtyOf, money, nextTier, priceAnchor, productName, stepOf, unitAt, variantOf } from '../lib/format'
import { useShell } from '../shell/ShellContext'
import { useCartQty } from '../store/cart'
import { savedStore, useIsSaved } from '../store/saved'
import { S } from '../strings'
import { Chip } from '../ui/Chip'
import { ProductImage, SIZES_GRID, SIZES_RAIL, SIZES_THUMB } from '../ui/ProductImage'
import { Stepper } from '../ui/Stepper'
import { useToast } from '../ui/Toast'
import { QtySheet } from './QtySheet'

/**
 * The product card (v3), three densities, one reading order so a merchant scans the same way
 * everywhere:
 *
 *   photo (white 1:1 frame, never cropped; badges top, stock/margin facts on the bottom edge)
 *   → kicker  "VFAN · UK04-C"          brand · code — ALWAYS shown: many SKUs differ only here
 *   → name    family name, 2 lines     productName(): no code, no "(VFAN)"
 *   → chips   "+ Type-C cable" "20W"   variantOf(), the chips that tell it from its siblings first
 *   → price   "BHD 1.000 /pc"          + the real previous price struck (price-book cuts only)
 *   → margin  "Retail 2.200 · 41% margin"   marginOf(): only real payload numbers, never struck
 *   → stock   "Only a few left"        only when it matters (out of stock sits on the photo)
 *   → action  Add · 12 → ✓ Added → stepper
 *
 *   grid    — the shelf; two badges, tiers, "Ordered by N shops"
 *   compact — rails and the mega-nav; fixed width clamp(10rem, 46vw, 13rem), one badge
 *   list    — mission mode (Shop/Search list toggle, search rows): the same facts in one row
 *
 * On grid and compact the margin strip is one short line on narrow cards and the full sentence
 * once the card body is ≥ 12rem wide (a CSS container query — no JS measuring).
 *
 * The kicker, name and chip rows have fixed heights, so prices line up across a rail or a grid
 * row; the action is pushed to the bottom. One action: `Add · 12` (the remembered quantity,
 * rounded to the pack) → "✓ Added" for 600 ms → the stepper, with a plum border on the card.
 * Tapping the number opens the keypad. Below the minimum the stepper removes the line — with an
 * Undo toast. No fly-to-cart.
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
/** A margin this share of retail (or more) earns the "High margin" tag. */
const HIGH_MARGIN_PCT = 50

/* ───────────────────────── variant chips, siblings first ─────────────────────────
   "P05 1Mtr" and "P05 2Mtr" both read [Type-C → Type-C] [60W] [1 m]; on a 160 px card only the
   first two chips fit. So the chips that differ from the other SKUs of the same model come first
   ("1 m"), the shared ones after. Nothing is added or reworded — only reordered. */

const chipCache = new WeakMap<ShopItem, string[]>()
function chipsOf(item: ShopItem): string[] {
  let chips = chipCache.get(item)
  if (!chips) {
    chips = variantOf(item)
    chipCache.set(item, chips)
  }
  return chips
}

/** The model a code belongs to: "P05 1Mtr" → P05, "UK04-C" → UK04, "F29A" → F29, "TB-D9" → TB. */
function modelOf(code: string): string {
  const t = code.trim().toUpperCase()
  return t.match(/^[A-Z]+\d+/)?.[0] || t.split(/[\s-]+/)[0] || t
}

const familyCache = new WeakMap<readonly ShopItem[], Map<string, ShopItem[]>>()
function familyOf(item: ShopItem, items: readonly ShopItem[]): ShopItem[] {
  let byModel = familyCache.get(items)
  if (!byModel) {
    byModel = new Map()
    for (const it of items) {
      const key = modelOf(it.item_code)
      const list = byModel.get(key)
      if (list) list.push(it)
      else byModel.set(key, [it])
    }
    familyCache.set(items, byModel)
  }
  return byModel.get(modelOf(item.item_code)) || []
}

function orderedChips(item: ShopItem, items: readonly ShopItem[]): string[] {
  const own = chipsOf(item)
  if (own.length < 2) return own
  const others = familyOf(item, items).filter((s) => s.item_code !== item.item_code)
  if (!others.length) return own
  const tells = own.filter((c) => others.some((s) => !chipsOf(s).includes(c)))
  if (!tells.length || tells.length === own.length) return own
  return [...tells, ...own.filter((c) => !tells.includes(c))]
}

/* ───────────────────────── shared pieces (also used by ProductPanel) ───────────────────────── */

/** "VFAN · UK04-C": the maker's mark, then the code — the one thing that always tells two look-alikes apart. */
export function CardKicker({ item, className }: { item: ShopItem; className?: string }) {
  const brand = (item.brand || '').trim()
  return (
    <span className={cn('flex min-w-0 items-baseline gap-1 text-2xs leading-[14px] text-ink-3', className)}>
      {brand && (
        <>
          <span className="shrink-0 font-semibold uppercase tracking-[0.06em]">{brand}</span>
          <span aria-hidden="true">·</span>
        </>
      )}
      <span className="min-w-0 truncate font-semibold tnum text-ink-2">{item.item_code}</span>
    </span>
  )
}

/**
 * The variant facts as chips. `line` (cards): one 20 px row that never wraps — a chip that does not
 * fit drops to a clipped second line, so only whole chips show. `wrap` (product panel): all of them.
 */
export function VariantChips({ item, layout = 'line', reserve = true, className }: { item: ShopItem; layout?: 'line' | 'wrap'; /** line: keep the empty row so prices align */ reserve?: boolean; className?: string }) {
  const { items } = useMarket()
  const chips = useMemo(() => orderedChips(item, items), [item, items])
  if (!chips.length && (layout === 'wrap' || !reserve)) return null
  return (
    <span className={cn('flex flex-wrap', layout === 'line' ? 'h-5 gap-1 overflow-hidden' : 'gap-1.5', className)}>
      {chips.map((c) => (
        <Chip key={c} tone="spec" size={layout === 'wrap' ? 'md' : 'sm'} className={layout === 'line' ? 'px-1.5' : undefined}>
          {c}
        </Chip>
      ))}
    </span>
  )
}

/**
 * The merchant's maths, from the price book only (marginOf): retail and the margin the shop keeps.
 * Retail is never struck through. `short` — one line ("Retail 2.200 · 41% margin") for narrow
 * cards; `full` — "Retail BHD 2.200" over "Your margin BHD 0.900/pc · 41%"; `fit` — short, turning
 * full once the enclosing inline-size container (the card body) is ≥ 12rem wide.
 */
export function MarginStrip({ item, form, className }: { item: ShopItem; form: 'short' | 'full' | 'fit'; className?: string }) {
  const mg = marginOf(item)
  if (!mg) return null
  const short = (
    <span className={cn('flex h-[22px] min-w-0 items-center justify-between gap-2 rounded-xs bg-deal-soft px-2 text-2xs leading-[14px] tnum text-deal-ink', form === 'fit' && '[@container(min-width:12rem)]:hidden', className)}>
      <span className="min-w-0 truncate">{S.deals.retail(money(mg.retail))}</span>
      <span className="shrink-0 font-bold">{S.card.marginPct(mg.pct)}</span>
    </span>
  )
  const full = (
    <span className={cn('block rounded-sm bg-deal-soft px-2 py-1.5 text-2xs leading-[15px] tnum text-deal-ink', form === 'fit' && 'hidden [@container(min-width:12rem)]:block', className)}>
      <span className="block">{S.deals.retail(bhd(mg.retail))}</span>
      <span className="block font-bold">{S.deals.margin(bhd(mg.margin), mg.pct)}</span>
    </span>
  )
  if (form === 'short') return short
  if (form === 'full') return full
  return (
    <>
      {short}
      {full}
    </>
  )
}

/** true when the real margin earns the "High margin" tag */
function isHighMargin(item: ShopItem): boolean {
  return (marginOf(item)?.pct ?? 0) >= HIGH_MARGIN_PCT
}

/** The solid amber "High margin" tag — only when the real price-book margin is ≥ 50 % of retail. */
export function HighMarginTag({ item, size = 'sm', className }: { item: ShopItem; size?: 'sm' | 'md'; className?: string }) {
  if (!isHighMargin(item)) return null
  return (
    <Chip tone="deal-strong" size={size} className={className}>
      {S.deals.high}
    </Chip>
  )
}

/**
 * The first price break, "12+ → BHD 0.400", with the price picked out. The whole sentence — arrow
 * included — comes from S.card.tier, so a translation carries its own arrow instead of an LTR glyph
 * hard-coded in JSX; here we only bold the price we were given.
 */
function TierLine({ qty, price }: { qty: number; price: string }) {
  const text = S.card.tier(qty, price)
  const i = text.lastIndexOf(price)
  if (i < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, i)}
      <span className="font-semibold text-ink">{price}</span>
      {text.slice(i + price.length)}
    </>
  )
}

export const MarketCard = memo(function MarketCard({ item, variant = 'grid', from, priority, presetQty, className }: MarketCardProps) {
  const m = useMarket()
  const { openProduct, viewport } = useShell()
  const toast = useToast()
  const qty = useCartQty(item.item_code)
  const saved = useIsSaved(item.item_code)
  const [added, setAdded] = useState(false)
  const [keypad, setKeypad] = useState(false)
  const [asked, setAsked] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const imgWrap = useRef<HTMLDivElement>(null)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const out = isOut(item)
  const low = item.stock_status === 'low_stock'
  const step = stepOf(item)
  const min = minQtyOf(item)
  const name = productName(item)
  const defaultQty = presetQty && presetQty > 0 ? presetQty : m.defaultQty(item)
  const was = priceAnchor(item)?.was ?? null
  const high = !out && isHighMargin(item)
  const badges = cardBadges(item, variant === 'compact' ? 1 : 2)
  const tiers = m.publicTiers ? item.tiers || [] : []
  const firstTier = tiers[0]
  const canOrder = !out || m.allowBackorder
  const selected = qty > 0
  const nudgeTier = selected ? nextTier(item, qty) : null
  const tellRep = out && !m.allowBackorder && m.rep?.whatsapp_url
  // productName() again rather than `name`: handing `name` to a function would make the React Compiler drop the memoised callbacks below
  const tellUrl = tellRep ? `${m.rep!.whatsapp_url!.split('?text=')[0]}?text=${encodeURIComponent(S.card.tellBackText(m.rep!.first_name || '', item.item_code, productName(item)))}` : null
  const askBack = () => {
    setAsked(true)
    toast(S.card.tellBackDone, 'success')
    postRestock({ item_code: item.item_code, phone: readCustomer().phone || null, device_id: deviceId(), referral_code: m.ref || null }).catch(() => undefined)
  }

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
          <MessageCircle size={15} aria-hidden="true" /> {m.rep?.first_name ? S.card.tellRep(m.rep.first_name) : S.card.askYq}
        </a>
      )
    }
    return (
      <button
        type="button"
        onClick={canOrder ? add : askBack}
        disabled={!canOrder && asked}
        aria-label={canOrder ? `${out ? S.card.backorder : S.card.add}${!out && defaultQty > 1 ? ` · ${defaultQty}` : ''} — ${name}` : `${asked ? S.card.tellBackDone : S.card.tellBack} — ${name}`}
        className={cn(
          'flex w-full items-center justify-center gap-1.5 rounded-sm text-sm font-semibold transition duration-1 ease-m active:scale-[.985] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2',
          size === 'sm' ? 'h-10' : 'h-11',
          canOrder ? (out ? 'border border-line bg-surface text-ink hover:border-ink/25 hover:bg-plum-wash' : 'bg-plum text-white shadow-1 hover:bg-plum-deep') : asked ? 'cursor-default border border-ok/30 bg-ok-soft text-ok' : 'border border-line bg-surface text-ink hover:border-ink/25 hover:bg-plum-wash',
        )}
      >
        {canOrder ? (
          <>
            <Plus size={15} aria-hidden="true" /> {out ? S.card.backorder : S.card.add}
            {!out && defaultQty > 1 && <span className="tnum opacity-80">· {defaultQty}</span>}
          </>
        ) : asked ? (
          <>
            <Check size={15} aria-hidden="true" /> {S.card.tellBackDone}
          </>
        ) : (
          <>
            <MessageCircle size={15} aria-hidden="true" /> {S.card.tellBack}
          </>
        )}
      </button>
    )
  }

  const keypadEl = keypad && <QtySheet item={item} value={qty} onApply={setQty} onRemove={removeWithUndo} onClose={() => setKeypad(false)} />
  const lowWord = (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap font-semibold text-deal-ink">
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-deal" aria-hidden="true" />
      {S.card.stockLow}
    </span>
  )

  /* ── list row ── */
  if (variant === 'list') {
    const mg = marginOf(item)
    const meta = Boolean(badges[0] || mg || out || low || firstTier)
    return (
      <article className={cn('flex items-center gap-3 border-b border-line-2 py-2.5', selected && '-mx-2 rounded-sm bg-plum-wash/60 px-2', className)}>
        <button type="button" onClick={open} className="h-14 w-14 shrink-0 self-start overflow-hidden rounded-sm border border-line-2 bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70" aria-label={`${S.card.quickView}: ${name}`}>
          <div ref={imgWrap}>
            <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={56} imgClassName={cn('p-1', out && 'opacity-70 saturate-[.25]')} iconSize={18} showCaption={false} />
          </div>
        </button>
        <button type="button" onClick={open} className="min-w-0 flex-1 rounded-xs text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
          <CardKicker item={item} />
          <span className="mt-px block truncate text-sm font-semibold text-ink">{name}</span>
          <VariantChips item={item} reserve={false} className="mt-1" />
          {meta && (
            // one 20px line, most decisive first; whatever does not fit wraps out of sight (whole items only)
            <span className="mt-1 flex h-5 min-w-0 flex-wrap items-center gap-x-2 gap-y-1 overflow-hidden text-2xs leading-5 tnum">
              {out ? <span className="font-semibold text-bad">{S.card.stockOut}</span> : low ? lowWord : null}
              {mg && (
                <span className="whitespace-nowrap rounded-xs bg-deal-soft px-1.5 text-deal-ink">
                  {S.deals.retail(money(mg.retail))} · <b className="font-bold">{S.card.marginPct(mg.pct)}</b>
                </span>
              )}
              {badges[0] && <Chip tone={badgeMeta(badges[0]).tone}>{badgeMeta(badges[0]).label}</Chip>}
              {!out && <HighMarginTag item={item} />}
              {firstTier && <span className="whitespace-nowrap text-ink-2">{S.card.tier(firstTier.min_qty, money(firstTier.unit_price_bhd))}</span>}
            </span>
          )}
        </button>
        <div className="flex shrink-0 flex-col items-end gap-1.5 self-center">
          {item.price_bhd != null ? (
            <span className="flex items-baseline gap-1 tnum">
              {!selected && was != null && (
                <span className="text-2xs text-ink-3">
                  <span className="sr-only">{S.card.was} </span>
                  <s>{money(was)}</s>
                </span>
              )}
              <span className="font-display text-sm font-bold text-ink">{bhd(selected ? (unitAt(item, qty) ?? Number(item.price_bhd)) * qty : item.price_bhd)}</span>
              {!selected && <span className="text-2xs text-ink-3">{S.card.perPc}</span>}
            </span>
          ) : (
            <span className="text-xs font-semibold text-ink-2">{S.card.priceOnRequest}</span>
          )}
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
        compact ? 'cv-compact w-[clamp(10rem,46vw,13rem)] shrink-0 lg:w-auto' : priority ? '' : 'cv-card',
        selected ? 'border-plum/50 shadow-[0_0_0_1px_hsl(var(--m-plum)/.18),var(--m-shadow-2)]' : 'border-line shadow-1 hover:-translate-y-0.5 hover:border-ink/15 hover:shadow-2',
        className,
      )}
    >
      <div className="relative">
        <button
          type="button"
          onClick={() => savedStore.toggle(item.item_code)}
          aria-pressed={saved}
          aria-label={`${saved ? S.card.savedItem : S.card.saveItem} — ${name}`}
          className={cn(
            'absolute end-2 top-2 z-[1] grid h-9 w-9 place-items-center rounded-full border bg-surface/95 shadow-1 transition duration-1 ease-m after:absolute after:-inset-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
            saved ? 'border-plum/40 text-plum' : 'border-line text-ink-3 hover:text-plum',
          )}
        >
          <Heart size={compact ? 15 : 16} aria-hidden="true" className={cn(saved && 'fill-current')} />
        </button>
        <button type="button" onClick={open} className="block w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70" aria-label={`${S.card.quickView}: ${name}`}>
          <div ref={imgWrap}>
            <ProductImage
              item={item}
              alt={name}
              sizes={compact ? SIZES_RAIL : SIZES_GRID}
              size={compact ? 208 : 320}
              priority={priority}
              eager={priority}
              className="w-full"
              imgClassName={cn('p-3 transition-transform duration-3 ease-m group-hover:scale-[1.03]', out && 'opacity-70 saturate-[.25]')}
              iconSize={compact ? 28 : 40}
              showCaption={!compact}
            />
          </div>
        </button>
        <div className="pointer-events-none absolute inset-0">
          {badges.length > 0 && (
            <span className="absolute start-2 top-2 flex flex-col items-start gap-1">
              {badges.map((b) => (
                <Chip key={b} tone={badgeMeta(b).tone}>
                  {badgeMeta(b).label}
                </Chip>
              ))}
            </span>
          )}
          {/* the photo's bottom edge carries the shelf facts: out of stock, or a high real margin */}
          {(out || high) && (
            <span className={cn('absolute bottom-2 max-w-[calc(100%-1rem)]', compact ? 'start-2.5' : 'start-3')}>
              <Chip tone={out ? 'bad' : 'deal-strong'}>{out ? S.card.stockOut : S.deals.high}</Chip>
            </span>
          )}
          {/* desktop hover reveal */}
          <span aria-hidden="true" className="absolute bottom-2 end-2 hidden opacity-0 transition duration-2 ease-m group-hover:opacity-100 lg:flex">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-ink/85 px-3 py-1.5 text-xs font-semibold text-white shadow-2 backdrop-blur">
              <Eye size={13} aria-hidden="true" /> {S.card.quickView}
            </span>
          </span>
        </div>
      </div>

      {/* an inline-size container: the margin strip picks its short or full form from the card's real width */}
      <div className={cn('flex flex-1 flex-col border-t border-line-2 [container-type:inline-size]', compact ? 'px-2.5 pb-2.5 pt-2' : 'p-3')}>
        <CardKicker item={item} />
        <h3 className={cn('mt-0.5 font-sans font-semibold tracking-normal text-ink', compact ? 'text-[13px] leading-[17px]' : 'text-sm md:text-[14px] md:leading-[19px]')}>
          <button type="button" onClick={open} className={cn('block w-full rounded-xs text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', compact ? 'min-h-[34px]' : 'min-h-[36px] md:min-h-[38px]')}>
            <span className="line-clamp-2">{name}</span>
          </button>
        </h3>
        <VariantChips item={item} className={compact ? 'mt-1.5' : 'mt-2'} />

        <div className={cn('flex min-w-0 items-baseline gap-x-1', compact ? 'mt-1.5' : 'mt-2')}>
          {item.price_bhd != null ? (
            <>
              <span className={cn('whitespace-nowrap font-display font-bold tnum text-ink', compact ? 'text-[15px] leading-5' : 'text-base leading-[22px] md:text-md')}>{bhd(item.price_bhd)}</span>
              <span className="text-2xs text-ink-3">{S.card.perPc}</span>
              {was != null && (
                <span className="ms-1 min-w-0 truncate text-2xs tnum text-ink-3">
                  <span className="sr-only">{S.card.was} </span>
                  <s>{money(was)}</s>
                </span>
              )}
            </>
          ) : (
            <span className={cn('font-semibold text-ink-2', compact ? 'text-xs leading-5' : 'text-sm leading-[22px]')}>{S.card.priceOnRequest}</span>
          )}
        </div>

        {!compact && (min > 1 || step > 1 || firstTier || (item.has_tiers && !m.publicTiers)) && (
          <div className="mt-1 text-2xs leading-4 tnum text-ink-2">
            {(min > 1 || step > 1) && <div>{[min > 1 ? S.card.min(min) : null, step > 1 ? S.card.packs(step) : null].filter(Boolean).join(' · ')}</div>}
            {firstTier ? (
              <div>
                <TierLine qty={firstTier.min_qty} price={bhd(firstTier.unit_price_bhd)} />
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

        <MarginStrip item={item} form="fit" className="mt-2" />
        {low && <p className={cn('text-2xs leading-[14px]', compact ? 'mt-1.5' : 'mt-2')}>{lowWord}</p>}
        {!compact && item.social_proof && (
          <p className="mt-2 flex items-start gap-1 text-2xs leading-[14px] text-ink-2">
            <Store size={12} aria-hidden="true" className="mt-px shrink-0 text-ink-3" />
            <span className="line-clamp-2">{item.social_proof}</span>
          </p>
        )}

        <div className={cn('mt-auto', compact ? 'pt-2.5' : 'pt-3')}>{action(compact ? 'sm' : 'md')}</div>
        {!compact && nudgeTier && <p className="mt-1.5 text-xs font-medium text-plum">{S.card.nudge(nudgeTier.min_qty - qty, money(nudgeTier.unit_price_bhd))}</p>}
      </div>
      {keypadEl}
    </article>
  )
})
