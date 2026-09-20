import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Boxes, Check, ChevronDown, Loader2, PackageCheck, Plus } from 'lucide-react'
import type { GapSuggestion, ShopItem } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket, useOrder } from '../MarketContext'
import { track } from '../lib/events'
import { bhd, minQtyOf, normalizeQty, productName, variantOf } from '../lib/format'
import { useShell } from '../shell/ShellContext'
import { isPhoneLike } from '../shell/useViewport'
import { useCartCounts, useCartQty } from '../store/cart'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { CheckMark } from './CheckMark'

/**
 * The wholesale basket, as one state card (plan D2). Every number is the server quote's
 * (`quote.minimum`, `quote.gap_suggestions`) — nothing is estimated here.
 *
 *   page     — Restock page: kicker, "BHD 7.000 away from your wholesale order", the rail
 *              (BHD 13.000 ━━━●━━ BHD 20.000), up to 6 one-tap gap fillers (3 shown, "Show 3 more";
 *              a gap-closing one is always among the first 3), and the actions:
 *              under → Keep restocking + "Request a small order" (not in block mode);
 *              met → "Wholesale order ready" (the tick draws) + Continue to checkout.
 *              actions="secondary" keeps only the small-order text button — for pages whose sticky
 *              bar already carries the primary (the phone Restock page); false renders none.
 *              fillers={false} hands the list to a <WholesaleFillers> block further down the page,
 *              which is what the Restock page does on BOTH layouts: on a phone the list pushed the
 *              merchant's own restock off the first screen, and in the desktop sticky column it
 *              pushed the CTA out of a box the page cannot scroll.
 *   compact  — mini-cart: headline, rail, "BHD 13.000 of BHD 20.000", 3 fillers, no actions.
 *   home     — Continue your restock: headline, rail, Review restock → /cart. No card chrome of
 *              its own (the home block owns the surface; pass className to add one). On a phone or
 *              tablet, where the CartDock is fixed over Home with the same sentence, the card also
 *              does the other half of the job — the gap fillers as one-tap adds — but it keeps the
 *              number and the link itself: that dock hides on every downward scroll.
 *
 * The gap fillers are never the server's one-line "close the whole gap with 30 of this code": each
 * row is offered at about a third of what is left (`shareOut`), so the basket stays mixed — and
 * never below the line's own pack, so close to the minimum it finishes the order instead of
 * offering a single piece. Only a row that really does close the gap is tagged.
 *
 * Renders nothing when the shop has no minimum (`quote.minimum` is null) or before the first
 * quote. The fill animates its width (the reduced-motion kill switch in market.css stops it).
 */

export interface WholesaleStateProps {
  variant: 'page' | 'compact' | 'home'
  /** under the minimum, outside block mode: the "Request a small order" text button (page) */
  onRequestSmall?: () => void
  /** page variant: true (default) = primary + secondary; 'secondary' = only "Request a small order"; false = none */
  actions?: boolean | 'secondary'
  /**
   * page variant: false hands the gap fillers to a `<WholesaleFillers>` block elsewhere on the page.
   * The phone Restock page does that — with the list inside it, the card ran past the fold and the
   * merchant's own lines ("In this restock") started below three products they had not chosen.
   */
  fillers?: boolean
  /**
   * Speak the headline as it changes. Off by default: a surface can carry several of these at once
   * (home block + mini-cart, page card + page bar) and every one of them announcing turns a single
   * stepper tap into a pile-up. The surface picks ONE region — this, or its own status line.
   */
  announce?: boolean
  className?: string
}

/** gap fillers read from the quote (the server sends up to shop_gap_suggestions, 6 by default) */
const MAX_FILLERS = 6
/** fillers shown before "Show more" (page) — all a compact state shows */
const FIRST = 3
/**
 * How much of the gap ONE suggested row is sized to close. The server sizes every suggestion to
 * close the whole gap on its own ("30 × one cable code"), which is not how a shop tops up: the
 * owner's rule is a MIXED basket. Each row is shown at about a third of what is left — its own
 * minimum and pack size respected, and never more than the server's own quantity.
 */
const GAP_SHARE = 3
/** BHD are quoted to 3 decimals: anything inside half a fils is "closes the gap" */
const EPS = 0.0005

/** one gap-filler row at the quantity this surface offers (not necessarily the server's) */
interface Filler {
  g: GapSuggestion
  item: ShopItem
  qty: number
  value: number
  closes: boolean
}

/**
 * The server's suggestions turned into a mixed basket. Each row is offered at about a third of what
 * is left — rounded up to that item's pack size and MOQ, and never above the quantity the server
 * sized — so a BHD 7 gap reads as three different products at a few pieces each instead of "30 ×
 * one cable code". The shop owner's rule is a mixed restock; nothing here pushes a single-SKU bulk
 * quantity to close the gap on its own.
 *
 * Near the minimum a third of the gap is worth less than one pack, and the row would read "1 ×
 * BHD 1.000" — a quantity no wholesaler offers and no shop orders. There the row is sized to
 * finish the order instead, which at that point is at most three packs, so the smallest thing ever
 * shown is one real pack.
 *
 * "completes your order" is therefore only true of a row whose OWN quantity clears what is left —
 * an item dear enough, or with a pack size big enough, that one tap does it. Often that is no row
 * at all, and then no row is tagged. Every number still comes from the quote (the unit price) or
 * from the item's own pack rules (the quantity) — none is invented.
 */
function shareOut(suggestions: GapSuggestion[], itemsByCode: Map<string, ShopItem>, remaining: number, max: number): Filler[] {
  const rows: Filler[] = []
  for (const g of suggestions) {
    if (rows.length >= max) break
    const item = itemsByCode.get(g.item_code)
    if (!item) continue
    const unit = Number(g.unit_price_bhd) || 0
    // the smallest quantity this line can really be ordered at: its MOQ, in whole packs
    const pack = minQtyOf(item)
    let qty = pack
    if (unit > 0) {
      const share = normalizeQty(item, Math.ceil(remaining / GAP_SHARE / unit))
      // A third of what is left can be less than one pack — close to the minimum that turns into
      // "1 × one charger", which is not a quantity a trade supplier offers. There the row is sized
      // to FINISH the order instead: by definition that is at most three packs, still a top-up.
      const closing = normalizeQty(item, Math.ceil((remaining - EPS) / unit))
      qty = share <= pack ? Math.max(pack, closing) : Math.max(pack, Math.min(Number(g.qty) || pack, share))
    }
    const value = qty * unit
    rows.push({ g, item, qty, value, closes: unit > 0 && value >= remaining - EPS })
  }
  return rows
}

interface GapFillers {
  /** the rows this surface shows right now */
  rows: Filler[]
  /** rows held back behind "Show N more" */
  rest: Filler[]
  expanded: boolean
  expand: () => void
}

/**
 * The quote's gap suggestions as the mixed basket a surface offers (`shareOut`): the server's order
 * kept, but one row that really closes the gap always in view. A hook, so the Restock page can show
 * the state card and the filler list as two separate blocks without computing the rows twice.
 */
function useGapFillers(max: number): GapFillers {
  const { quote } = useOrder()
  const { itemsByCode } = useMarket()
  const [expanded, setExpanded] = useState(false)
  const minimum = quote?.minimum
  const remaining = Math.max(0, Number(minimum?.remaining_bhd) || 0)
  const met = !minimum || Boolean(minimum.met) || remaining <= 0
  const all: Filler[] = met ? [] : shareOut(quote?.gap_suggestions || [], itemsByCode, remaining, max)
  let head = all.slice(0, FIRST)
  const closer = all.find((x) => x.closes)
  if (closer && !head.some((x) => x.closes)) head = [...head.slice(0, FIRST - 1), closer]
  const rest = all.filter((x) => !head.includes(x))
  return { rows: expanded ? [...head, ...rest] : head, rest, expanded, expand: () => setExpanded(true) }
}

export function WholesaleState({ variant, onRequestSmall, actions = true, fillers: withFillers = true, announce = false, className }: WholesaleStateProps) {
  const { quote, quoting } = useOrder()
  const { viewport } = useShell()
  const { items: cartItems } = useCartCounts()
  const navigate = useNavigate()
  const headingId = useId()
  const gap = useGapFillers(variant === 'home' ? FIRST : MAX_FILLERS)
  const minimum = quote?.minimum
  if (!quote || !minimum) return null

  const need = Math.max(0, Number(minimum.value_bhd) || 0)
  const remaining = Math.max(0, Number(minimum.remaining_bhd) || 0)
  const met = Boolean(minimum.met) || remaining <= 0
  // what counts toward the minimum: the order after discounts, before delivery (price_cart's net_after)
  const net = Math.max(0, (Number(quote.subtotal_bhd) || 0) - (Number(quote.discount_bhd) || 0))
  const have = met ? Math.max(need, net) : Math.max(0, need - remaining)
  const canSmall = !met && minimum.mode !== 'block' && Boolean(onRequestSmall)
  // the last stretch reads differently from the first: "Almost there" + the amber kicker
  const near = !met && need > 0 && have / need >= 0.8
  // Home on a phone/tablet: the CartDock is on screen with the same number and the same Review
  // link, so this card drops both and does the other half of the job instead (see the home variant).
  const docked = variant === 'home' && isPhoneLike(viewport) && cartItems > 0

  // the page can hand its list to a block of its own (WholesaleFillers) so the card stays short
  const fillers = variant === 'page' && !withFillers ? [] : gap.rows

  const away = bhd(remaining)
  const headline = near ? S.wholesale.almost(away) : S.wholesale.away(away)

  /* ── compact (mini-cart) ── */
  if (variant === 'compact') {
    return (
      <div className={cn('rounded-lg bg-plum-wash p-3 ring-1 ring-inset ring-plum/10', className)} aria-busy={quoting || undefined}>
        <p aria-live={announce ? 'polite' : undefined} aria-atomic="true" className="flex items-start gap-1.5 text-sm font-semibold leading-snug text-ink">
          {met ? (
            <>
              <span aria-hidden="true" className="mt-px grid h-4 w-4 shrink-0 place-items-center rounded-full bg-ok text-white" style={{ animation: 'm-scale-in 260ms var(--m-ease-spring) both' }}>
                <Check size={10} strokeWidth={3.25} />
              </span>
              {S.wholesale.ready}
            </>
          ) : (
            <span>
              <Emphasis text={headline} token={away} tokenKey={away} className="inline-block text-plum tnum anim-tick-up" />
            </span>
          )}
        </p>
        <WholesaleRail have={have} need={need} met={met} size="sm" trackClassName="bg-surface" className="mt-2.5" />
        <p key={`${have}/${need}`} className="mt-2 text-2xs tnum text-ink-2 anim-fade-in">
          {S.wholesale.progress(bhd(have), bhd(need))}
        </p>
        {fillers.length > 0 && (
          <div className="mt-2.5 border-t border-plum/10 pt-2">
            <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-3">{S.wholesale.fill}</h3>
            <ul className="mt-0.5 divide-y divide-plum/10 pb-0.5">
              {fillers.map((f) => (
                <FillerRow key={f.g.item_code} f={f} dense />
              ))}
            </ul>
          </div>
        )}
      </div>
    )
  }

  /* ── home (Continue your restock) ── */
  if (variant === 'home') {
    // The phone/tablet CartDock carries the same sentence and the same Review link — but it hides
    // itself on every downward scroll, which is exactly when this card is being read. So the card
    // always keeps the number and a way forward; what the dock's presence changes is only the extra
    // job this card takes on: filling the order with the mixed gap fillers as one-tap adds.
    const split = docked && (met || fillers.length > 0)
    return (
      <div className={className} aria-busy={quoting || undefined}>
        <Kicker met={met} quoting={quoting} near={near} />
        <h3 id={headingId} aria-live={announce ? 'polite' : undefined} aria-atomic="true" className="mt-2.5 text-balance font-display text-lg font-bold leading-tight text-ink">
          {met ? (
            split ? (
              S.wholesale.readyHint
            ) : (
              S.wholesale.ready
            )
          ) : (
            <Emphasis text={headline} token={away} tokenKey={away} className="inline-block text-plum tnum anim-tick-up" />
          )}
        </h3>
        <WholesaleRail have={have} need={need} met={met} className="mt-4" />
        <div className="mt-2 flex items-center justify-between gap-3">
          <span key={`${have}/${need}`} className="min-w-0 truncate text-xs tnum text-ink-2 anim-fade-in">
            {S.wholesale.progress(bhd(have), bhd(need))}
          </span>
          {/* one forward link in every state — the dock that used to carry it can be off screen */}
          <Link
            to={split && met ? '/checkout' : '/cart'}
            aria-describedby={headingId}
            className="-me-2 inline-flex h-11 shrink-0 items-center gap-1 rounded-sm px-2 text-sm font-semibold text-plum transition duration-1 ease-m hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
          >
            {split && met ? S.wholesale.checkout : S.wholesale.review}
            <ArrowRight size={14} aria-hidden="true" className="rtl:-scale-x-100" />
          </Link>
        </div>
        {split && !met && fillers.length > 0 && (
          <div className="mt-3 border-t border-line-2 pt-2.5">
            <h4 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-3">{S.wholesale.fill}</h4>
            <ul className="mt-0.5 divide-y divide-line-2">
              {fillers.map((f) => (
                <FillerRow key={f.g.item_code} f={f} />
              ))}
            </ul>
          </div>
        )}
      </div>
    )
  }

  /* ── page (Restock) ── */
  return (
    <section aria-labelledby={headingId} aria-busy={quoting || undefined} className={cn('rounded-xl border border-line bg-surface p-4 shadow-1 sm:p-5', className)}>
      <Kicker met={met} quoting={quoting} near={near} />

      {met ? (
        <div className="mt-3.5 flex items-center gap-3">
          <CheckMark size={44} />
          <div className="min-w-0" aria-live={announce ? 'polite' : undefined} aria-atomic="true">
            <h2 id={headingId} className="font-display text-xl font-bold leading-tight text-ink">
              {S.wholesale.ready}
            </h2>
            <p className="mt-0.5 text-sm leading-snug text-ink-2">{S.wholesale.readyHint}</p>
          </div>
        </div>
      ) : (
        <h2 id={headingId} aria-live={announce ? 'polite' : undefined} aria-atomic="true" className="mt-3 text-balance font-display text-xl font-bold leading-tight text-ink">
          <Emphasis text={headline} token={away} tokenKey={away} className="inline-block text-plum tnum anim-tick-up" />
        </h2>
      )}

      <WholesaleRail have={have} need={need} met={met} className="mt-5" />
      <div className="mt-3 flex items-start justify-between gap-4">
        <RailLabel amount={bhd(have)} caption={S.wholesale.inRestock} />
        <RailLabel amount={bhd(need)} caption={S.wholesale.minimumLabel} end />
      </div>
      {/* what the minimum is FOR — the office's real small-order rule, so only where it applies */}
      {!met && minimum.mode === 'request' && <p className="mt-2 text-xs leading-snug text-ink-2">{S.wholesale.whyUnder}</p>}

      {fillers.length > 0 && (
        <div className="mt-4 border-t border-line-2 pt-3.5">
          <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-3">{S.wholesale.fill}</h3>
          <FillerRows gap={gap} className="mt-0.5" />
        </div>
      )}

      {actions === 'secondary' ? (
        canSmall && <SmallRequestButton onClick={onRequestSmall!} className={fillers.length ? 'mt-2' : 'mt-3.5'} />
      ) : actions ? (
        met ? (
          <Button size="lg" full className="mt-4" onClick={() => navigate('/checkout')} icon={<ArrowRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />}>
            {S.wholesale.checkout}
          </Button>
        ) : (
          <div className="mt-4">
            <Button size="lg" full onClick={() => navigate('/shop')} icon={<Boxes size={16} aria-hidden="true" />}>
              {S.wholesale.keep}
            </Button>
            {canSmall && <SmallRequestButton onClick={onRequestSmall!} className="mt-1" />}
          </div>
        )
      ) : null}
    </section>
  )
}

/**
 * The gap fillers as a block of their own — same rows, same quantities, same heading as the card's
 * list. The phone Restock page mounts it under "In this restock": with the list inside the state
 * card, the card alone was taller than the screen and the merchant's own lines opened below three
 * products they had not chosen. Renders nothing when the order is at the minimum (or before the
 * first quote), so the page loses a block instead of showing an empty one.
 */
export function WholesaleFillers({ className }: { className?: string }) {
  const gap = useGapFillers(MAX_FILLERS)
  const headingId = useId()
  if (gap.rows.length === 0) return null
  return (
    <section aria-labelledby={headingId} className={cn('rounded-xl border border-line bg-surface px-4 pb-1.5 pt-3.5 shadow-1', className)}>
      <h2 id={headingId} className="font-display text-base font-bold leading-tight text-ink">
        {S.wholesale.fill}
      </h2>
      <FillerRows gap={gap} className="mt-1.5" />
    </section>
  )
}

/** The rows themselves, plus "Show N more" when the surface is holding some back. */
function FillerRows({ gap, className }: { gap: GapFillers; className?: string }) {
  return (
    <>
      <ul className={cn('divide-y divide-line-2', className)}>
        {gap.rows.map((f) => (
          <FillerRow key={f.g.item_code} f={f} />
        ))}
      </ul>
      {!gap.expanded && gap.rest.length > 0 && (
        <button
          type="button"
          onClick={gap.expand}
          aria-expanded={false}
          className="-mb-1 mt-0.5 flex h-10 w-full items-center justify-center gap-1 rounded-sm border-t border-line-2 text-xs font-semibold text-plum transition duration-1 ease-m hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 [@media(pointer:coarse)]:h-11"
        >
          {S.wholesale.showMore(gap.rest.length)}
          <ChevronDown size={14} aria-hidden="true" />
        </button>
      )}
    </>
  )
}

/* ───────────────────────── pieces (also used by the Restock page and the small-order sheet) ───────────────────────── */

/**
 * The owner's "BHD 13 ━━━●━━ BHD 20": a 10 px plum-wash track, the plum fill, and a white knob
 * with a plum ring riding the end of the fill (it moves with the width transition — no second
 * animation). At the minimum the knob turns plum with a tick.
 */
export function WholesaleRail({ have, need, met, size = 'md', trackClassName, className }: { have: number; need: number; met: boolean; size?: 'md' | 'sm'; trackClassName?: string; className?: string }) {
  const pct = met ? 100 : need > 0 ? Math.max(0, Math.min(100, (have / need) * 100)) : 100
  const md = size === 'md'
  return (
    <div
      role="progressbar"
      aria-label={S.wholesale.kicker}
      aria-valuemin={0}
      aria-valuemax={need}
      aria-valuenow={Math.min(have, need)}
      aria-valuetext={S.wholesale.progress(bhd(have), bhd(need))}
      className={cn('relative w-full rounded-full ring-1 ring-inset ring-plum/10', md ? 'h-2.5' : 'h-2', trackClassName || 'bg-plum-wash', className)}
    >
      <div className="relative h-full rounded-full bg-plum transition-[width] duration-500 ease-m" style={{ width: `max(${pct}%, ${md ? 10 : 8}px)` }}>
        <span
          aria-hidden="true"
          className={cn(
            'absolute top-1/2 grid -translate-y-1/2 place-items-center rounded-full shadow-2 transition-colors duration-3 ease-m',
            met ? 'bg-plum text-white ring-2 ring-surface' : 'bg-surface ring-[3px] ring-inset ring-plum',
            md ? '-end-1.5 h-5 w-5' : '-end-1 h-4 w-4',
          )}
        >
          {met && <Check size={md ? 11 : 9} strokeWidth={3.25} style={{ animation: 'm-scale-in 260ms var(--m-ease-spring) both' }} />}
        </span>
      </div>
    </div>
  )
}

function RailLabel({ amount, caption, end }: { amount: string; caption: string; end?: boolean }) {
  return (
    <div className={cn('min-w-0', end && 'text-end')}>
      <div key={amount} className="font-display text-sm font-bold tnum text-ink anim-tick-up">
        {amount}
      </div>
      <div className="text-xs text-ink-2">{caption}</div>
    </div>
  )
}

/** `near` = the last fifth of the way to the minimum: the chip turns amber, nothing else changes. */
function Kicker({ met, quoting, near }: { met: boolean; quoting: boolean; near?: boolean }) {
  const Icon = met ? PackageCheck : Boxes
  return (
    <div className="flex items-center justify-between gap-2">
      <span className={cn('inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 text-2xs font-semibold uppercase tracking-[0.1em] ring-1 ring-inset', near ? 'bg-deal-soft text-deal-ink ring-deal/20' : 'bg-plum-wash text-plum-ink ring-plum/10')}>
        <Icon size={12} strokeWidth={2} aria-hidden="true" />
        {S.wholesale.kicker}
      </span>
      {quoting && <Loader2 size={14} className="animate-spin text-ink-3" aria-hidden="true" />}
    </div>
  )
}

/** The quiet secondary action under the minimum: a full-width text button, 44 px tall. */
export function SmallRequestButton({ onClick, className }: { onClick: () => void; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex h-11 w-full items-center justify-center rounded-sm text-sm font-semibold text-plum underline-offset-4 transition duration-1 ease-m hover:bg-plum-wash hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70',
        className,
      )}
    >
      {S.wholesale.small}
    </button>
  )
}

/** Wrap the first (or last) occurrence of `token` in `text` — keeps whole sentences in strings.ts. */
function Emphasis({ text, token, tokenKey, className, last }: { text: string; token: string; tokenKey?: string; className?: string; last?: boolean }): ReactNode {
  const i = last ? text.lastIndexOf(token) : text.indexOf(token)
  if (!token || i < 0) return text
  return (
    <>
      {text.slice(0, i)}
      <span key={tokenKey} className={className}>
        {token}
      </span>
      {text.slice(i + token.length)}
    </>
  )
}

const ADDED_MS = 700

/**
 * One gap filler: thumb (opens the product), name, 1–2 variant chips, "12 × BHD 0.400 · BHD 4.800"
 * and a fresh "completes your order" tag when one tap closes the gap. The round + adds the
 * quantity on the row; the button turns plum with a tick and stays that way while the line is in
 * the restock, until the next quote takes the row away.
 *
 * The quantity, the value and the tag are the row's own (`shareOut` — a restock share of the gap,
 * not the server's "close it all with this one code"); the unit price is the quote's.
 */
function FillerRow({ f, dense }: { f: Filler; dense?: boolean }) {
  const { g, item, qty, closes } = f
  const m = useMarket()
  const { openProduct } = useShell()
  const inCart = useCartQty(g.item_code) > 0
  const [flash, setFlash] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const name = productName(item)
  const on = inCart || flash
  const chips = dense ? [] : variantOf(item).slice(0, closes ? 1 : 2)
  const value = bhd(f.value)

  const add = () => {
    if (inCart) return
    m.add(item, qty, 'gap_filler')
    track('rail_click', { item_code: g.item_code, meta: { rail: 'gap', code: g.why } })
    setFlash(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setFlash(false), ADDED_MS)
    try {
      navigator.vibrate?.(8)
    } catch {
      /* not supported */
    }
  }

  return (
    <li className={cn('flex items-center', dense ? 'gap-2.5 py-2' : 'gap-3 py-2.5')}>
      <button
        type="button"
        onClick={() => openProduct(g.item_code, 'gap')}
        aria-label={name}
        className={cn('shrink-0 overflow-hidden border border-line-2 bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', dense ? 'h-10 w-10 rounded-sm' : 'h-12 w-12 rounded-md')}
      >
        <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={dense ? 40 : 48} imgClassName="p-1" iconSize={dense ? 14 : 16} showCaption={false} />
      </button>
      <div className="min-w-0 flex-1">
        <div className={cn('truncate font-semibold text-ink', dense ? 'text-xs' : 'text-sm')}>{name}</div>
        {(chips.length > 0 || (closes && !dense)) && (
          <div className="mt-1 flex max-h-5 min-w-0 flex-wrap gap-1 overflow-hidden">
            {closes && (
              <Chip tone="fresh" dot>
                {S.wholesale.closes}
              </Chip>
            )}
            {chips.map((c) => (
              <Chip key={c} tone="spec">
                {c}
              </Chip>
            ))}
          </div>
        )}
        {/* the name may truncate at 320 px, a price may not: the pair wraps to a second line
            instead of clipping "BHD 0.8…" — a cut number reads as a data error */}
        <div className={cn('mt-0.5 flex flex-wrap items-baseline gap-x-1 tnum text-ink-2', dense ? 'text-2xs' : 'text-xs')}>
          <span className="whitespace-nowrap">
            {S.wholesale.qtyAt(qty, bhd(g.unit_price_bhd))} <span aria-hidden="true">·</span>
          </span>
          <span className="whitespace-nowrap font-semibold text-ink">{value}</span>
        </div>
        {/* one label, one design: the dense rows get the same mint chip as the page, not green text */}
        {closes && dense && (
          <div className="mt-1">
            <Chip tone="fresh" dot>
              {S.wholesale.closes}
            </Chip>
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={add}
        aria-disabled={inCart || undefined}
        aria-label={inCart ? `${S.card.added} — ${name}` : `${S.card.add} ${qty} — ${name}`}
        className={cn(
          'relative grid shrink-0 place-items-center rounded-full border transition duration-2 ease-m focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 focus-visible:ring-offset-2',
          dense ? 'h-10 w-10 after:absolute after:-inset-0.5' : 'h-11 w-11',
          on ? 'border-plum bg-plum text-white' : 'border-line bg-surface text-plum shadow-1 hover:border-plum hover:bg-plum-wash active:scale-95',
        )}
      >
        {on ? (
          <Check key="on" size={dense ? 15 : 17} strokeWidth={2.5} aria-hidden="true" style={{ animation: 'm-scale-in 220ms var(--m-ease-spring) both' }} />
        ) : (
          <Plus key="off" size={dense ? 16 : 18} aria-hidden="true" />
        )}
      </button>
    </li>
  )
}
