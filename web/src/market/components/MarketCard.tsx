import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, Clock, Eye, Heart, MessageCircle, Plus, Store } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { BadgeKind, ShopItem } from '@/lib/shopApi'
import { useMarket } from '../MarketContext'
import { track } from '../lib/events'
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
import { useTellBack } from './RestockAsk'

/**
 * The product card (v3), three densities, one reading order so a merchant scans the same way
 * everywhere:
 *
 *   photo (white 1:1 frame, never cropped; badges top, stock state on the bottom edge — and
 *          nothing else: a fact is never a sticker over the product)
 *   → kicker  "VFAN · UK04-C"          brand · code — ALWAYS shown: many SKUs differ only here
 *   → name    family name, 2 lines     productName(): no code, no "(VFAN)"
 *   → chips   "+ Type-C cable" "20W"   variantOf(), the chips that tell it from its siblings first
 *   → price   "BHD 1.000 /pc"          ALWAYS the unit price — never a line total, in any density
 *   → facts   anchors, then ONE line   "Was 2.000 · ↓5%" (price-book cuts only) then the margin
 *                                      strip → "Only a few left" → shops → price breaks
 *   → action  Add · 12 → ✓ Added → stepper
 *             sold out: "Backorder" where the shop allows one (shop_allow_backorder), otherwise the
 *             add is gone and the one action is "Tell me when back" — the restock request the rep
 *             sees in the portal (RestockAsk: never sent without a phone, the rep on WhatsApp as
 *             the secondary route). The line keeps its URL and its card; it is labelled "Sold out"
 *             (never "Out of stock"; dated once the stock snapshot is stale) and sits after the
 *             available lines.
 *
 *   grid    — the shelf; two badges, tiers, "Ordered by N shops"
 *   compact — rails and the mega-nav; fixed width clamp(10rem, 46vw, 13rem), one badge
 *   list    — mission mode (Shop/Search list toggle, search rows): the same facts in one row
 *
 * The margin strip (marginOf: real payload numbers, never struck) wraps rather than truncates —
 * the retail number the percentage is computed from is never clipped, at any width.
 *
 * The kicker, name and chip rows have reserved heights, so prices line up across a rail or a grid
 * row. The facts line is reserved on `grid` (the action is bottom-aligned there anyway) and
 * collapses on `compact`, where 165 of 179 SKUs carry no fact at all and a reserved-but-empty
 * line would be pure void. One action: `Add · 12` (the remembered quantity, rounded to the pack)
 * → "✓ Added" for 600 ms → the stepper, with a plum border on the card. With no remembered
 * quantity there is nothing to add "one" of: the button opens the keypad instead, so the merchant
 * always states a wholesale quantity. On `compact` the untouched card's round + sits IN the price
 * row (the rails would otherwise be a wall of plum bars, and a lone circle on its own row left
 * 60 px of dead card under every price); once the line exists the full-width row returns for
 * "✓ Added" and the stepper. `grid` keeps the full-width bar throughout.
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
  /**
   * Badge(s) the surface already says in its own title — a shelf of "Last chance" cards does not
   * repeat "Last chance" on all ten. The next-best badge is shown instead. Pass a single key or a
   * stable array (a fresh array literal on every render would defeat the memo below).
   */
  hideBadge?: BadgeKind | readonly BadgeKind[] | null
  className?: string
}

const ADDED_MS = 600
/** A margin this share of retail (or more) earns the "High margin" tag. */
const HIGH_MARGIN_PCT = 50
/** Ask for every badge, then pick: a hidden one must not eat the card's only badge slot. */
const BADGE_ORDER_MAX = 8
/**
 * `fit`: the width at which the margin strip turns from one line into the two-line form that
 * spells the BHD the shop keeps per piece. Measured, not guessed — the container is the card
 * BODY's content box: 150 px on the 390 px phone grid (card 176 − border − p-3), 157 px in a
 * phone rail cell, 203 px on the md 3-up grid, 149 px in the 1440 5-up deals grid. The long line
 * ("Your margin BHD 0.800/pc · 67%") needs ~176 px before it wraps, so 11rem is the first width
 * where `full` is an improvement instead of three ragged lines. Narrower cards keep `short`,
 * which carries the same two numbers.
 */
const FIT_FULL_AT = { short: '[@container(min-width:11rem)]:hidden', full: 'hidden [@container(min-width:11rem)]:flex' } as const

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
 * Retail is never struck through. `short` — one line ("Retail BHD 2.200 · 41% margin") for narrow
 * cards; `full` — "Retail BHD 2.200" over "Your margin BHD 0.900/pc · 41%" (`size="lg"` + `tag`
 * for the product panel); `fit` — short, turning full once the enclosing inline-size container
 * (the card body) is wide enough for the long line (FIT_FULL_AT). One component, so the panel and
 * the grid can never drift apart on the strongest number in the shop.
 *
 * Nothing in here is ever truncated: the retail price is the number the percentage is computed
 * from, so a clipped "Retail 1.2…" would leave an unverifiable claim. Below ~390 px the short
 * strip wraps to two lines instead. A high real margin is carried by the strip's own weight (a
 * hairline and bolder type), never by a sticker on the photo.
 */
export function MarginStrip({ item, form, size = 'sm', tag = false, className }: { item: ShopItem; form: 'short' | 'full' | 'fit'; /** `lg` — the product panel's type scale */ size?: 'sm' | 'lg'; /** `full` only: the "High margin" tag at the end */ tag?: boolean; className?: string }) {
  const mg = marginOf(item)
  if (!mg) return null
  const high = mg.pct >= HIGH_MARGIN_PCT
  const short = (
    // the retail number always carries its currency: a bare "1.200" under a "BHD 0.400" is not a
    // price, it is a digit — the merchant has to be able to read the maths without decoding it
    <span className={cn('flex min-h-[22px] flex-wrap items-center justify-between gap-x-1.5 rounded-xs bg-deal-soft px-1.5 py-px text-2xs leading-[14px] tnum text-deal-ink', high && 'font-semibold ring-1 ring-inset ring-deal/45', form === 'fit' && FIT_FULL_AT.short, className)}>
      <span className="whitespace-nowrap">{S.deals.retail(bhd(mg.retail))}</span>
      <span className="shrink-0 whitespace-nowrap font-bold">{S.card.marginPct(mg.pct)}</span>
    </span>
  )
  const full = (
    <span className={cn('flex items-start justify-between gap-3 bg-deal-soft tnum text-deal-ink', size === 'lg' ? 'rounded-md px-3 py-2.5' : 'rounded-sm px-2 py-1.5', form === 'fit' && FIT_FULL_AT.full, className)}>
      <span className={cn('min-w-0', size === 'lg' ? 'text-xs leading-[18px]' : 'text-2xs leading-[15px]')}>
        <span className="block">{S.deals.retail(bhd(mg.retail))}</span>
        <span className={cn('block font-bold', size === 'lg' && 'text-sm')}>{S.deals.margin(bhd(mg.margin), mg.pct)}</span>
      </span>
      {tag && <HighMarginTag item={item} className="mt-0.5 shrink-0" />}
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

/**
 * The one honest anchor: the previous price from OUR price book (priceAnchor → `was_bhd` only,
 * never retail, never invented). It used to trail the price as an 11 px grey strike-through, so
 * on a shelf titled "Real price drops" the proof of the drop was the quietest mark on the card
 * while a margin pill shouted next to it. Same slot, same weight now — and it wraps to its own
 * line instead of truncating: an ellipsised old price ("2.0…") proves nothing.
 */
export function WasPill({ was, pct, className }: { was: number; pct: number; className?: string }) {
  return <span className={cn('inline-flex w-fit max-w-full items-center whitespace-nowrap rounded-xs bg-deal-soft px-1.5 py-px text-2xs font-semibold leading-[14px] tnum text-deal-ink', className)}>{S.card.wasPill(money(was), pct)}</span>
}

/**
 * A list row is one 20 px meta line next to a 14 px price. The solid black "Best seller" pill the
 * grid wears over a 200 px photo becomes the heaviest object in that row — heavier than the price
 * it is meant to sell — so in list context it drops to the quiet grey. Every other badge keeps its
 * own tone (a "Last chance" that is not amber is not last chance).
 */
function listBadgeTone(kind: BadgeKind): ReturnType<typeof badgeMeta>['tone'] {
  return kind === 'best_seller' ? 'grey' : badgeMeta(kind).tone
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

export const MarketCard = memo(function MarketCard({ item, variant = 'grid', from, priority, presetQty, hideBadge, className }: MarketCardProps) {
  const m = useMarket()
  const { openProduct, viewport } = useShell()
  const toast = useToast()
  const qty = useCartQty(item.item_code)
  const saved = useIsSaved(item.item_code)
  const [added, setAdded] = useState(false)
  /** 'add' — no line yet, the keypad states the first quantity; 'edit' — change the line's quantity */
  const [keypad, setKeypad] = useState<'add' | 'edit' | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const imgWrap = useRef<HTMLDivElement>(null)
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const out = isOut(item)
  const low = item.stock_status === 'low_stock'
  const step = stepOf(item)
  const min = minQtyOf(item)
  const name = productName(item)
  const defaultQty = presetQty && presetQty > 0 ? presetQty : m.defaultQty(item)
  const anchor = priceAnchor(item)
  const mg = marginOf(item)
  /**
   * The badges this card shows. `cardBadges` already drops demand claims from a sold-out line;
   * here a shelf that is defined by a badge ("Last chance" ten times down a clearance grid) drops
   * it as well — the next-best badge shows instead, or none.
   */
  const hideKey = typeof hideBadge === 'string' ? hideBadge : hideBadge ? hideBadge.join(',') : ''
  const badges = useMemo(() => {
    const hidden = hideKey ? hideKey.split(',') : []
    return cardBadges(item, BADGE_ORDER_MAX)
      .filter((b) => !hidden.includes(b))
      .slice(0, variant === 'compact' ? 1 : 2)
  }, [item, hideKey, variant])
  const tiers = m.publicTiers ? item.tiers || [] : []
  const firstTier = tiers[0]
  const canOrder = !out || m.allowBackorder
  const selected = qty > 0
  const nudgeTier = selected ? nextTier(item, qty) : null
  /** the sold-out rule's one action: a restock request the rep sees — with a phone to come back to, never a silent backorder */
  const { asked, ask: askBack, sheet: tellSheet } = useTellBack(item)

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

  const flash = useCallback(() => {
    setAdded(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setAdded(false), ADDED_MS)
    try {
      navigator.vibrate?.(8)
    } catch {
      /* not supported */
    }
  }, [])

  const add = useCallback(() => {
    m.add(item, defaultQty, from)
    flash()
  }, [m, item, defaultQty, from, flash])

  /** the keypad's first commit: an add (analytics + the Added flash), not a quantity edit */
  const addQty = useCallback(
    (n: number) => {
      m.add(item, n, from)
      flash()
    },
    [m, item, from, flash],
  )

  const removeWithUndo = useCallback(() => {
    const last = qty
    m.remove(item.item_code)
    toast(S.card.removed(name), { kind: 'info', action: { label: S.card.undo, onClick: () => m.setQty(item, last) } })
  }, [m, item, qty, name, toast])

  const setQty = useCallback((n: number) => m.setQty(item, n), [m, item])

  /* ── the one action ── */
  /**
   * `round` (the compact density): a 40 px plum circle at the end of the row instead of a
   * full-width bar, so a rail of eight cards is a shelf and not a wall of buttons. It still
   * carries the quantity ("+12") whenever one is remembered.
   */
  const action = (size: 'sm' | 'md', round = false) => {
    const tall = size === 'sm' ? 'h-10' : 'h-11'
    if (added) {
      return round ? (
        <div className="flex justify-end">
          <div aria-live="polite" className="grid h-10 w-10 place-items-center rounded-full bg-plum text-white shadow-1">
            <Check size={18} aria-hidden="true" />
            <span className="sr-only">{S.card.added}</span>
          </div>
        </div>
      ) : (
        <div aria-live="polite" className={cn('flex w-full items-center justify-center gap-1.5 rounded-sm bg-plum text-sm font-semibold text-white', tall)}>
          <Check size={16} aria-hidden="true" /> {S.card.added}
        </div>
      )
    }
    if (selected) {
      // a committed line must never look lighter than one that was ignored: the stepper carries
      // a plum wash of the same weight as the Add block it replaced
      return <Stepper value={qty} step={step} min={min} size={size} label={name} onChange={setQty} onRemove={removeWithUndo} onValueClick={() => setKeypad('edit')} className="w-full bg-plum-wash" />
    }
    /**
     * Nothing is remembered for this line yet, so there is no honest quantity to add: the button
     * opens the keypad (pre-filled at the minimum, quick picks) and the merchant states a
     * wholesale quantity. Once one is remembered the button adds it straight away, "Add · 12".
     * Sold out with backorder off: no add at all — the slot is "Tell me when back".
     */
    const askFirst = canOrder && defaultQty <= 1
    const press = canOrder ? (askFirst ? () => setKeypad('add') : add) : askBack
    const label = canOrder ? `${out ? S.card.backorder : S.card.add}${!out && defaultQty > 1 ? ` · ${defaultQty}` : ''} — ${name}` : `${asked ? S.card.tellBackDone : S.card.tellBack} — ${name}`
    if (round && canOrder && !out) {
      return (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={press}
            aria-label={label}
            // 40 px of ink, 48 px of target: the pseudo-element carries the rest of the tap area
            className="relative grid h-10 w-10 shrink-0 place-items-center rounded-full bg-plum text-white shadow-1 transition duration-1 ease-m after:absolute after:-inset-1 active:scale-95 hover:bg-plum-deep focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2"
          >
            {defaultQty > 1 ? <span className={cn('font-bold leading-none tnum', String(defaultQty).length > 2 ? 'text-2xs' : 'text-[13px]')}>+{defaultQty}</span> : <Plus size={18} aria-hidden="true" />}
          </button>
        </div>
      )
    }
    return (
      <button
        type="button"
        onClick={press}
        disabled={!canOrder && asked}
        aria-label={label}
        className={cn(
          'hit relative flex w-full items-center justify-center gap-1.5 rounded-sm text-sm font-semibold transition duration-1 ease-m active:scale-[.985] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2',
          tall,
          canOrder ? (out ? 'border border-line bg-surface text-ink hover:border-ink/25 hover:bg-plum-wash' : 'bg-plum text-white shadow-1 hover:bg-plum-deep') : asked ? 'cursor-default border border-ok/30 bg-ok-soft text-ok' : 'border border-line bg-surface text-ink hover:border-ink/25 hover:bg-plum-wash',
        )}
      >
        {canOrder ? (
          <>
            {/* a backorder is not an add: "we will order it in, date unconfirmed" must not wear
                the same + as the line that ships today */}
            {out ? <Clock size={15} aria-hidden="true" /> : <Plus size={15} aria-hidden="true" />} {out ? S.card.backorder : S.card.add}
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

  // the card's overlays: the quantity keypad and the "Tell me when back" phone sheet
  const keypadEl = (
    <>
      {keypad && <QtySheet item={item} value={keypad === 'add' ? 0 : qty} onApply={keypad === 'add' ? addQty : setQty} onRemove={removeWithUndo} onClose={() => setKeypad(null)} />}
      {tellSheet}
    </>
  )
  const lowWord = (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap font-semibold text-deal-ink">
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-deal" aria-hidden="true" />
      {S.card.stockLow}
    </span>
  )

  /* ── list row ── */
  if (variant === 'list') {
    const meta = Boolean(badges[0] || mg || out || low || firstTier)
    return (
      // data-stock: the availability the card was rendered with — the QA harness reads it to assert
      // that no available card ever follows a sold-out one on a listing (scripts/qa/market_qa.py)
      <article data-stock={item.stock_status || 'in_stock'} className={cn('flex items-center gap-3 border-b border-line-2 py-2.5', selected && '-mx-2 rounded-sm bg-plum-wash/60 px-2', className)}>
        <button type="button" onClick={open} className="h-14 w-14 shrink-0 self-start overflow-hidden rounded-sm border border-line-2 bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 md:h-[4.5rem] md:w-[4.5rem]" aria-label={`${S.card.quickView}: ${name}`}>
          <div ref={imgWrap}>
            <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={72} imgClassName={cn('p-1', out && 'opacity-70 saturate-[.25]')} iconSize={18} showCaption={false} />
          </div>
        </button>
        <button type="button" onClick={open} className="min-w-0 flex-1 rounded-xs text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
          <CardKicker item={item} />
          {/* two lines: the tail of a cable name ("… (Type-C to Type-C)") is what tells two rows apart.
              NO `block` here — it would win over line-clamp-2's `display:-webkit-box` in the cascade
              and the clamp would be inert, which is what made this shelf ragged (rows 96–138 px). */}
          <span className={cn('mt-px text-sm font-semibold leading-snug line-clamp-2', out ? 'text-ink-2' : 'text-ink')}>{name}</span>
          <VariantChips item={item} reserve={false} className="mt-1" />
          {meta && (
            // one 20px line, most decisive first; whatever does not fit wraps out of sight (whole
            // items only). State and badge lead: on a 390 px phone they are what the margin strip
            // used to push out of the clipped box — a last-chance line that never says so is worse
            // than a margin the merchant can still read on the card or the panel.
            <span className="mt-1 flex h-5 min-w-0 flex-wrap items-center gap-x-2 gap-y-1 overflow-hidden text-2xs leading-5 tnum">
              {/* sold out is a state, not an error: red is kept for things that went wrong.
                  Same chip as the grid card's, so one product reads the same in both densities. */}
              {out ? <Chip tone="grey">{m.soldOutLabel}</Chip> : low ? lowWord : null}
              {badges[0] && <Chip tone={listBadgeTone(badges[0])}>{badgeMeta(badges[0]).label}</Chip>}
              {mg && (
                // the same two numbers as the card's strip, without the amber fill: a filled
                // margin pill sitting against the amber "Last chance" chip made two lozenges of
                // different shape touch, which at 11 px reads as one smeared blob. In a text row
                // the percentage alone carries the colour.
                <span className="whitespace-nowrap text-ink-2">
                  {S.deals.retail(bhd(mg.retail))} · <b className="font-semibold text-deal-ink">{S.card.marginPct(mg.pct)}</b>
                </span>
              )}
              {firstTier && <span className="whitespace-nowrap text-ink-2">{S.card.tier(firstTier.min_qty, money(firstTier.unit_price_bhd))}</span>}
            </span>
          )}
        </button>
        <div className="flex shrink-0 flex-col items-end gap-1.5 self-center">
          {item.price_bhd != null ? (
            <span className="flex flex-col items-end gap-0.5">
              {/* the UNIT price, always — the same number the grid and the rail show for this SKU.
                  Swapping in the line total here (and dropping "/pc" with it) made a 5-piece line
                  read as a sibling that costs five times as much. */}
              <span className="flex items-baseline gap-1 tnum">
                <span className="font-display text-sm font-bold text-ink">{bhd(item.price_bhd)}</span>
                <span className="text-2xs text-ink-3">{S.card.perPc}</span>
              </span>
              {selected ? (
                // the extended total never travels without its multiplier
                <span className="whitespace-nowrap text-2xs font-semibold tnum text-plum-ink">{S.card.lineTotal(qty, money(unitAt(item, qty) ?? Number(item.price_bhd)), bhd((unitAt(item, qty) ?? Number(item.price_bhd)) * qty))}</span>
              ) : (
                anchor != null && <WasPill was={anchor.was} pct={anchor.pct} />
              )}
            </span>
          ) : (
            <span className="text-xs font-semibold text-ink-2">{S.card.priceOnRequest}</span>
          )}
          <div className="w-[6.5rem] md:w-[7.5rem]">{action('sm')}</div>
        </div>
        {keypadEl}
      </article>
    )
  }

  const compact = variant === 'compact'
  /**
   * The compact card's round "+" rides INSIDE the price row instead of owning a 40 px row of its
   * own. A rail card reserved a full-width row for a 40 px circle pinned to its end, so 139 of the
   * row's 179 px were blank — with the (usually empty) facts line above it, the bottom 62 px of
   * every rail card was a lone purple dot in the corner.
   *
   * Only for a line that has not been started yet: once there is a quantity, "✓ Added" and the
   * stepper take the full-width row back (they are wider than a circle, and the row appearing
   * under the price — with the card's plum border — is what makes the state change legible).
   * `out` keeps its own row too: "Backorder" is a word, not a glyph.
   */
  const inlineAction = compact && !selected && !added && !out

  return (
    <article
      data-stock={item.stock_status || 'in_stock'}
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
            'absolute end-2 top-2 z-[1] grid h-9 w-9 place-items-center rounded-full border bg-surface/95 shadow-1 transition duration-1 ease-m after:absolute after:-inset-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
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
          {/* the photo's bottom edge carries stock state and nothing else — a fact about the trade
              (margin) belongs under the photo, never as a sticker across the product */}
          {out && (
            <span className={cn('absolute bottom-2 max-w-[calc(100%-1rem)]', compact ? 'start-2.5' : 'start-3')}>
              <Chip tone="grey">{m.soldOutLabel}</Chip>
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

      {/* an inline-size container: card-width-aware bits (the margin strip's `fit` form) read it */}
      <div className={cn('flex flex-1 flex-col border-t border-line-2 [container-type:inline-size]', compact ? 'px-2.5 pb-2.5 pt-2' : 'p-3')}>
        <CardKicker item={item} />
        <h3 className={cn('mt-0.5 font-sans font-semibold tracking-normal text-ink', compact ? 'text-[13px] leading-[17px]' : 'text-sm md:text-[14px] md:leading-[19px]')}>
          <button type="button" onClick={open} className={cn('hit relative block w-full rounded-xs text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', compact ? 'min-h-[36px]' : 'min-h-[36px] md:min-h-[38px]')}>
            <span className="line-clamp-2">{name}</span>
          </button>
        </h3>
        <VariantChips item={item} className={compact ? 'mt-1.5' : 'mt-2'} />

        <div className={cn('flex min-w-0 gap-x-1', inlineAction ? 'items-center justify-between' : 'items-baseline', compact ? 'mt-1.5' : 'mt-2')}>
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-1">
            {item.price_bhd != null ? (
              <>
                <span className={cn('whitespace-nowrap font-display font-bold tnum text-ink', compact ? 'text-[15px] leading-5' : 'text-base leading-[22px] md:text-md')}>{bhd(item.price_bhd)}</span>
                <span className="text-2xs text-ink-3">{S.card.perPc}</span>
              </>
            ) : (
              <span className={cn('font-semibold text-ink-2', compact ? 'text-xs leading-5' : 'text-sm leading-[22px]')}>{S.card.priceOnRequest}</span>
            )}
          </span>
          {/* compact, untouched: the round + rides here instead of owning a 40 px row of its own */}
          {inlineAction && <span className="shrink-0">{action('sm', true)}</span>}
        </div>

        {/* The facts under the price, most decisive first: our own previous price ("Was 2.000 ·
            ↓5%") — never trailing the price as a truncated 11 px strike-through, which is what put
            "BHD 1.900 /pc 2.0…" on the Stock-Up Deals grid at 1440 and 1920 — and then ONE line,
            the merchant's maths / stock / the first price break.
            Reserved on the shelf grid, so prices line up across a row; on a rail it collapses when
            there is nothing to say (165 of 179 SKUs carry no fact at all, and a reserved-but-empty
            line was most of the dead band at the bottom of every rail card). */}
        <div className={cn('mt-1.5 flex min-w-0 flex-col justify-start gap-1', compact ? 'empty:hidden' : 'min-h-[22px]')}>
          {anchor && <WasPill was={anchor.was} pct={anchor.pct} />}
          {mg ? (
            // `fit`: a card body wide enough for it spells out the BHD the shop keeps per piece
            // ("Retail BHD 1.200 / Your margin BHD 0.800/pc · 67%") — the strongest wholesale fact
            // in the shop, which until now only the product panel showed. Narrow cards and the
            // rails keep the one-line form, which carries the same two numbers.
            <MarginStrip item={item} form={compact ? 'short' : 'fit'} />
          ) : low ? (
            <p className="text-2xs leading-[14px]">{lowWord}</p>
          ) : !compact && item.social_proof ? (
            <p className="flex items-start gap-1 text-2xs leading-[14px] text-ink-2">
              <Store size={12} aria-hidden="true" className="mt-px shrink-0 text-ink-3" />
              <span className="line-clamp-2">{item.social_proof}</span>
            </p>
          ) : !compact && firstTier ? (
            <p className="text-2xs leading-4 tnum text-ink-2">
              <TierLine qty={firstTier.min_qty} price={bhd(firstTier.unit_price_bhd)} />
              {tiers.length > 1 && (
                <button type="button" onClick={open} className="hit relative ms-1.5 font-semibold text-plum hover:underline">
                  {S.card.breaks(tiers.length)}
                </button>
              )}
            </p>
          ) : !compact && item.has_tiers && !m.publicTiers ? (
            <p className="text-2xs leading-4 text-ink-2">{S.card.volumePrices}</p>
          ) : !compact && (min > 1 || step > 1) ? (
            <p className="text-2xs leading-4 tnum text-ink-2">{[min > 1 ? S.card.min(min) : null, step > 1 ? S.card.packs(step) : null].filter(Boolean).join(' · ')}</p>
          ) : null}
        </div>

        {/* rails (compact): an untouched card has NO action row — its + already rides in the price
            row, and this row was 40 px of blank card with a lone plum circle at the end of it.
            "✓ Added" and the stepper take the full-width row back (they are words and controls, not
            a glyph). No mt-auto here: a card stretched by a taller neighbour must not open a dead
            band between its price and its button. The shelf grid keeps its aligned bottoms. */}
        {!inlineAction && <div className={compact ? 'pt-2.5' : 'mt-auto pt-3'}>{action(compact ? 'sm' : 'md')}</div>}
        {!compact && nudgeTier && <p className="mt-1.5 text-xs font-medium text-plum">{S.card.nudge(nudgeTier.min_qty - qty, money(nudgeTier.unit_price_bhd))}</p>}
      </div>
      {keypadEl}
    </article>
  )
})
