import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Boxes, Check, ChevronDown, Loader2, PackageCheck, Plus } from 'lucide-react'
import type { GapSuggestion, ShopItem } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket, useOrder } from '../MarketContext'
import { track } from '../lib/events'
import { bhd, productName, variantOf } from '../lib/format'
import { useShell } from '../shell/ShellContext'
import { useCartQty } from '../store/cart'
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
 *   compact  — mini-cart: headline, rail, "BHD 13.000 of BHD 20.000", 3 fillers, no actions.
 *   home     — Continue your restock: headline, rail, Review restock → /cart. No card chrome of
 *              its own (the home block owns the surface; pass className to add one).
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

export function WholesaleState({ variant, onRequestSmall, actions = true, announce = false, className }: WholesaleStateProps) {
  const { quote, quoting } = useOrder()
  const { itemsByCode } = useMarket()
  const navigate = useNavigate()
  const headingId = useId()
  const [expanded, setExpanded] = useState(false)
  const minimum = quote?.minimum
  if (!quote || !minimum) return null

  const need = Math.max(0, Number(minimum.value_bhd) || 0)
  const remaining = Math.max(0, Number(minimum.remaining_bhd) || 0)
  const met = Boolean(minimum.met) || remaining <= 0
  // what counts toward the minimum: the order after discounts, before delivery (price_cart's net_after)
  const net = Math.max(0, (Number(quote.subtotal_bhd) || 0) - (Number(quote.discount_bhd) || 0))
  const have = met ? Math.max(need, net) : Math.max(0, need - remaining)
  const canSmall = !met && minimum.mode !== 'block' && Boolean(onRequestSmall)

  const allFillers = met
    ? []
    : (quote.gap_suggestions || [])
        .map((g) => ({ g, item: itemsByCode.get(g.item_code) }))
        .filter((x): x is { g: GapSuggestion; item: ShopItem } => Boolean(x.item))
        .slice(0, variant === 'home' ? 0 : MAX_FILLERS)
  // the first rows keep the server's order, but one tap that closes the gap is always in view
  let head = allFillers.slice(0, FIRST)
  const closer = allFillers.find((x) => x.g.closes_gap)
  if (closer && !head.some((x) => x.g.closes_gap)) head = [...head.slice(0, FIRST - 1), closer]
  const rest = allFillers.filter((x) => !head.includes(x))
  const fillers = expanded ? [...head, ...rest] : head

  const away = bhd(remaining)

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
              <Emphasis text={S.wholesale.away(away)} token={away} tokenKey={away} className="inline-block text-plum tnum anim-tick-up" />
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
            <ul className="mt-0.5 divide-y divide-plum/10">
              {fillers.map(({ g, item }) => (
                <FillerRow key={g.item_code} g={g} item={item} dense />
              ))}
            </ul>
          </div>
        )}
      </div>
    )
  }

  /* ── home (Continue your restock) ── */
  if (variant === 'home') {
    return (
      <div className={className} aria-busy={quoting || undefined}>
        <Kicker met={met} quoting={quoting} />
        <h3 id={headingId} aria-live={announce ? 'polite' : undefined} aria-atomic="true" className="mt-2.5 text-balance font-display text-lg font-bold leading-tight text-ink">
          {met ? S.wholesale.ready : <Emphasis text={S.wholesale.away(away)} token={away} tokenKey={away} className="inline-block text-plum tnum anim-tick-up" />}
        </h3>
        <WholesaleRail have={have} need={need} met={met} className="mt-4" />
        <div className="mt-2 flex items-center justify-between gap-3">
          <span key={`${have}/${need}`} className="min-w-0 truncate text-xs tnum text-ink-2 anim-fade-in">
            {S.wholesale.progress(bhd(have), bhd(need))}
          </span>
          <Link
            to="/cart"
            aria-describedby={headingId}
            className="-me-2 inline-flex h-11 shrink-0 items-center gap-1 rounded-sm px-2 text-sm font-semibold text-plum transition duration-1 ease-m hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
          >
            {S.wholesale.review}
            <ArrowRight size={14} aria-hidden="true" className="rtl:-scale-x-100" />
          </Link>
        </div>
      </div>
    )
  }

  /* ── page (Restock) ── */
  return (
    <section aria-labelledby={headingId} aria-busy={quoting || undefined} className={cn('rounded-xl border border-line bg-surface p-4 shadow-1 sm:p-5', className)}>
      <Kicker met={met} quoting={quoting} />

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
          <Emphasis text={S.wholesale.away(away)} token={away} tokenKey={away} className="inline-block text-plum tnum anim-tick-up" />
        </h2>
      )}

      <WholesaleRail have={have} need={need} met={met} className="mt-5" />
      <div className="mt-3 flex items-start justify-between gap-4">
        <RailLabel amount={bhd(have)} caption={S.wholesale.inRestock} />
        <RailLabel amount={bhd(need)} caption={S.wholesale.minimumLabel} end />
      </div>

      {fillers.length > 0 && (
        <div className="mt-4 border-t border-line-2 pt-3.5">
          <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-3">{S.wholesale.fill}</h3>
          <ul className="mt-0.5 divide-y divide-line-2">
            {fillers.map(({ g, item }) => (
              <FillerRow key={g.item_code} g={g} item={item} />
            ))}
          </ul>
          {!expanded && rest.length > 0 && (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              aria-expanded={false}
              className="-mb-1 mt-0.5 flex h-10 w-full items-center justify-center gap-1 rounded-sm border-t border-line-2 text-xs font-semibold text-plum transition duration-1 ease-m hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 [@media(pointer:coarse)]:h-11"
            >
              {S.wholesale.showMore(rest.length)}
              <ChevronDown size={14} aria-hidden="true" />
            </button>
          )}
        </div>
      )}

      {actions === 'secondary' ? (
        canSmall && <SmallRequestButton onClick={onRequestSmall!} className={fillers.length ? 'mt-2' : 'mt-3'} />
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

function Kicker({ met, quoting }: { met: boolean; quoting: boolean }) {
  const Icon = met ? PackageCheck : Boxes
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="inline-flex h-6 items-center gap-1.5 rounded-full bg-plum-wash px-2.5 text-2xs font-semibold uppercase tracking-[0.1em] text-plum-ink ring-1 ring-inset ring-plum/10">
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
 * suggested quantity; the button turns plum with a tick and stays that way while the line is in
 * the restock, until the next quote takes the row away.
 */
function FillerRow({ g, item, dense }: { g: GapSuggestion; item: ShopItem; dense?: boolean }) {
  const m = useMarket()
  const { openProduct } = useShell()
  const inCart = useCartQty(g.item_code) > 0
  const [flash, setFlash] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const name = productName(item)
  const on = inCart || flash
  const closes = Boolean(g.closes_gap)
  const chips = dense ? [] : variantOf(item).slice(0, closes ? 1 : 2)
  const value = bhd(g.value_bhd)

  const add = () => {
    if (inCart) return
    m.add(item, g.qty, 'gap_filler')
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
        <div className={cn('mt-0.5 truncate tnum text-ink-2', dense ? 'text-2xs' : 'text-xs')}>
          <Emphasis text={S.wholesale.line(g.qty, bhd(g.unit_price_bhd), value)} token={value} className="font-semibold text-ink" last />
        </div>
        {closes && dense && <div className="mt-0.5 text-2xs font-semibold text-fresh-ink">{S.wholesale.closes}</div>}
      </div>
      <button
        type="button"
        onClick={add}
        aria-disabled={inCart || undefined}
        aria-label={inCart ? `${S.card.added} — ${name}` : `${S.card.add} ${g.qty} — ${name}`}
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
