import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Check, ChevronRight, Clock, Package, Percent, Plus, RotateCcw, Sparkles, Tag, TrendingDown, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { MyOrderSummary, Offer, ShopItem } from '@/lib/shopApi'
import { tiltHandlers } from '../hooks/useTilt'
import { useMarket } from '../MarketContext'
import { track } from '../lib/events'
import { badgeMeta, bhd, cardBadges, categorySlug, fmtDateShort, minQtyOf, money, niceCategory, stepOf, unitAt, useCountdown, productDetail, productName, stockMeta } from '../lib/format'
import type { RegularLine } from '../lib/home'
import { useShell } from '../shell/ShellContext'
import { useCartQty } from '../store/cart'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { Chip } from '../ui/Chip'
import { ProductImage, SIZES_HERO, SIZES_THUMB } from '../ui/ProductImage'
import { Stepper } from '../ui/Stepper'

/* ───────────────────────── mission strip ───────────────────────── */

export function MissionStrip({ lastCount, newCount, offerCount, clearanceCount = 0, dropCount = 0 }: { lastCount: number; newCount: number; offerCount: number; clearanceCount?: number; dropCount?: number }) {
  const pill = 'inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full border border-line bg-surface px-3.5 text-sm font-semibold text-ink transition duration-1 ease-m hover:border-ink/25 hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70'
  return (
    <div className="no-scrollbar -mx-gutter mt-3 flex gap-2 overflow-x-auto px-gutter lg:mx-0 lg:px-0">
      {lastCount > 0 && (
        <Link to="/quick?load=last" className={cn(pill, 'border-plum/30 bg-plum-soft text-plum-ink')}>
          <RotateCcw size={15} aria-hidden="true" /> {S.home.again(lastCount)}
        </Link>
      )}
      <Link to="/quick" className={pill}>
        <Zap size={15} className="text-plum" aria-hidden="true" /> {S.home.quick}
      </Link>
      {newCount > 0 && (
        <Link to="/shop?f=new" className={pill}>
          <Sparkles size={15} className="text-plum" aria-hidden="true" /> {S.rails.arrived} <span className="text-xs font-normal tnum text-ink-3">{newCount}</span>
        </Link>
      )}
      {offerCount > 0 && (
        <Link to="/shop?f=offers" className={pill}>
          <Tag size={15} className="text-plum" aria-hidden="true" /> {S.rails.offers} <span className="text-xs font-normal tnum text-ink-3">{offerCount}</span>
        </Link>
      )}
      {dropCount > 0 && (
        <Link to="/shop?f=drops" className={pill}>
          <TrendingDown size={15} className="text-plum" aria-hidden="true" /> {S.rails.drops} <span className="text-xs font-normal tnum text-ink-3">{dropCount}</span>
        </Link>
      )}
      {clearanceCount > 0 && (
        <Link to="/shop?f=clearance" className={cn(pill, 'border-warn/30 bg-warn-soft text-warn')}>
          <Percent size={15} aria-hidden="true" /> {S.rails.clearance} <span className="text-xs font-normal tnum">{clearanceCount}</span>
        </Link>
      )}
    </div>
  )
}

/* ───────────────────────── category tiles ───────────────────────── */

export function CategoryTiles({ tiles }: { tiles: { category: string; count: number; newCount: number; image: ShopItem | null }[] }) {
  return (
    <section className="mt-4" aria-label={S.categories.title}>
      <div className="grid grid-cols-4 gap-2 md:grid-cols-8 md:gap-3">
        {tiles.map((t, i) => (
          <Link
            key={t.category}
            to={`/t/${categorySlug(t.category)}`}
            onClick={() => track('rail_click', { meta: { rail: 'tile', pos: i } })}
            className="group flex flex-col items-center rounded-md border border-line bg-surface p-1.5 text-center transition duration-2 ease-m hover:-translate-y-0.5 hover:border-ink/15 hover:shadow-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 md:p-2.5"
          >
            <div className="relative w-full">
              <ProductImage item={t.image} alt="" sizes="(min-width: 1024px) 140px, 22vw" size={140} className="w-full rounded-sm" imgClassName="p-1 transition-transform duration-3 ease-m group-hover:scale-[1.05] md:p-2" iconSize={22} showCaption={false} eager={i < 4} />
              {t.newCount > 0 && (
                <span className="absolute -end-1 -top-1">
                  <Chip tone="ok">{S.home.newCount(t.newCount)}</Chip>
                </span>
              )}
            </div>
            <span className="mt-1 line-clamp-2 min-h-[2rem] text-2xs font-semibold leading-4 text-ink md:text-xs">{niceCategory(t.category)}</span>
            <span className="text-2xs tnum text-ink-3">{t.count}</span>
          </Link>
        ))}
      </div>
    </section>
  )
}

/* ───────────────────────── offer strip ───────────────────────── */

export function OfferStrip({ offer }: { offer: Offer }) {
  const ends = useCountdown(offer.ends_at)
  const to = offer.scope_codes?.length || offer.kind === 'qty_tier' ? '/shop?f=offers' : '/cart'
  return (
    <Link
      to={to}
      onClick={() => track('rail_click', { meta: { rail: 'offer', code: String(offer.id) } })}
      className="mt-3 flex h-11 items-center gap-2.5 rounded-md bg-plum-soft px-3.5 text-sm text-plum-ink ring-1 ring-plum/15 transition duration-1 ease-m hover:bg-plum/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
    >
      <Tag size={16} className="shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate">
        <b className="font-semibold">{offer.name}</b>
        {offer.summary ? <span className="text-plum-ink/80"> · {offer.summary}</span> : null}
      </span>
      {ends && (
        <span className="hidden shrink-0 items-center gap-1 text-xs font-medium sm:inline-flex">
          <Clock size={13} aria-hidden="true" /> {S.home.offerEnds(ends)}
        </span>
      )}
      <ChevronRight size={16} className="shrink-0" aria-hidden="true" />
    </Link>
  )
}

/* ───────────────────────── track order card ───────────────────────── */

export function TrackCard({ order }: { order: MyOrderSummary }) {
  return (
    <Link to={`/o/${order.token}`} className="mt-3 flex items-center gap-3 rounded-lg border border-line bg-surface px-4 py-3 transition duration-1 ease-m hover:border-ink/15 hover:shadow-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-sm bg-plum-soft text-plum">
        <Package size={18} aria-hidden="true" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2 tnum">{order.order_no}</span>
        <span className="block truncate font-display text-sm font-bold text-ink">
          {order.status_label || order.status}
          {order.expected_delivery ? <span className="font-sans font-medium text-ink-2"> · {order.expected_delivery}</span> : null}
        </span>
      </span>
      <span className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-plum">
        {S.placed.track} <ChevronRight size={15} aria-hidden="true" />
      </span>
    </Link>
  )
}

/* ───────────────────────── hero: the product ───────────────────────── */

export function HeroProduct({ item, rank, category, also = [] }: { item: ShopItem; rank: number; category: string; also?: ShopItem[] }) {
  const m = useMarket()
  const { openProduct } = useShell()
  const qty = useCartQty(item.item_code)
  const tilt = tiltHandlers(3)
  const [added, setAdded] = useState(false)
  const t = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(t.current), [])
  const name = productName(item)
  const firstTier = m.publicTiers ? (item.tiers || [])[0] : undefined
  const badges = cardBadges(item, 2)
  const def = m.defaultQty(item)
  const add = () => {
    m.add(item, def, 'hero')
    setAdded(true)
    window.clearTimeout(t.current)
    t.current = window.setTimeout(() => setAdded(false), 600)
  }
  return (
    <section className="mt-4 grid grid-cols-[minmax(0,5fr)_minmax(0,7fr)] overflow-hidden rounded-xl border border-line bg-surface shadow-1 md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]" aria-label={S.home.hero}>
      <div {...tilt} className="tilt relative flex items-center border-e border-line-2 bg-surface">
        <button type="button" onClick={() => openProduct(item.item_code, 'hero')} className="block w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70" aria-label={`${S.card.quickView}: ${name}`}>
          <ProductImage item={item} alt={name} sizes={SIZES_HERO} size={512} priority eager className="mx-auto w-full max-w-[22rem]" imgClassName="p-2 md:p-8" iconSize={40} />
        </button>
        <span className="absolute start-2 top-2 inline-flex items-center gap-1.5 md:start-3 md:top-3">
          <Chip tone="ink" size="md" className="max-md:!px-2 max-md:!text-2xs">
            {rank > 0 && category ? S.home.rank(rank, niceCategory(category)) : S.home.hero}
          </Chip>
        </span>
      </div>
      <div className="flex min-w-0 flex-col p-3 md:p-6 lg:p-8">
        <div className="flex flex-wrap items-center gap-1.5">
          <Chip tone={stockMeta(item.stock_status).tone} dot>
            {stockMeta(item.stock_status).label}
          </Chip>
          {badges.map((b) => (
            <Chip key={b} tone={badgeMeta(b).tone}>
              {badgeMeta(b).label}
            </Chip>
          ))}
          {item.social_proof && <span className="text-xs text-ink-2">{item.social_proof}</span>}
        </div>
        <button type="button" onClick={() => openProduct(item.item_code, 'hero')} className="mt-2 rounded-xs text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
          <h2 className="line-clamp-3 font-display text-base font-bold leading-tight text-ink md:text-2xl lg:text-3xl">{name}</h2>
        </button>
        <p className="mt-1 line-clamp-2 text-xs text-ink-2 md:text-sm">
          <span className="tnum">{item.item_code}</span>
          {productDetail(item) ? ` · ${productDetail(item)}` : null}
        </p>
        <div className="mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="font-display text-xl font-extrabold tnum text-plum-ink md:text-3xl">{item.price_bhd != null ? bhd(item.price_bhd) : S.card.priceOnRequest}</span>
          {item.price_bhd != null && <span className="text-xs text-ink-3">{S.card.perPiece}</span>}
          {firstTier && (
            <span className="text-sm tnum text-ink-2">
              {firstTier.min_qty}+ pcs → <b className="font-semibold text-ink">{bhd(firstTier.unit_price_bhd)}</b>
            </span>
          )}
        </div>
        {also.length > 0 && (
          <div className="mt-5 hidden md:block">
            <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-2">{S.home.alsoPopular}</div>
            <div className="mt-2 flex gap-2">
              {also.slice(0, 4).map((a) => (
                <button key={a.item_code} type="button" onClick={() => openProduct(a.item_code, 'hero_also')} className="group/also flex min-w-0 flex-1 items-center gap-2 rounded-md border border-line bg-surface p-1.5 text-start transition duration-1 ease-m hover:border-ink/20 hover:shadow-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
                  <span className="h-11 w-11 shrink-0 overflow-hidden rounded-xs border border-line-2">
                    <ProductImage item={a} alt="" sizes={SIZES_THUMB} size={44} imgClassName="p-1" iconSize={14} showCaption={false} />
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-semibold text-ink">{productName(a)}</span>
                    <span className="block text-xs tnum text-plum-ink">{a.price_bhd != null ? bhd(a.price_bhd) : ''}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="mt-auto flex items-center gap-2 pt-3 md:pt-5">
          {added ? (
            <div className="flex h-12 flex-1 items-center justify-center gap-2 rounded-md bg-plum text-base font-semibold text-white md:max-w-[16rem]">
              <Check size={18} aria-hidden="true" /> {S.card.added}
            </div>
          ) : qty > 0 ? (
            <Stepper value={qty} step={stepOf(item)} min={minQtyOf(item)} size="lg" label={name} onChange={(n) => m.setQty(item, n)} onRemove={() => m.remove(item.item_code)} className="flex-1 md:max-w-[16rem]" />
          ) : (
            <Button size="lg" className="flex-1 md:max-w-[16rem]" onClick={add} icon={<Plus size={17} aria-hidden="true" />} disabled={item.stock_status === 'out_of_stock' && !m.allowBackorder} aria-label={`${S.card.add}${def > 1 ? ` · ${def}` : ''} — ${name}`}>
              {S.card.add}
              {def > 1 && <span className="tnum opacity-80">· {def}</span>}
            </Button>
          )}
          <Button variant="secondary" size="lg" className="hidden md:inline-flex" onClick={() => openProduct(item.item_code, 'hero')}>
            {S.card.quickView}
          </Button>
        </div>
      </div>
    </section>
  )
}

/* ───────────────────────── hero: order again ───────────────────────── */

export function OrderAgainHero({ lines, placedAt, more }: { lines: RegularLine[]; placedAt?: string | null; more: number }) {
  const m = useMarket()
  const navigate = useNavigate()
  const { openProduct } = useShell()
  const shown = lines.slice(0, 4)
  const total = lines.reduce((s, l) => s + (unitAt(l.item, l.qty) ?? 0) * l.qty, 0)
  const addAll = () => {
    m.addMany(lines, 'order_again')
    navigate('/cart')
  }
  return (
    <section className="mt-4 overflow-hidden rounded-xl border border-line bg-surface shadow-1" aria-label={S.rails.regulars}>
      <div className="flex items-center gap-3 border-b border-line-2 px-4 py-3 md:px-5">
        <span className="grid h-9 w-9 place-items-center rounded-sm bg-plum-soft text-plum">
          <RotateCcw size={17} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-base font-bold text-ink">{S.rails.regulars}</h2>
          <p className="truncate text-xs text-ink-2">
            {S.home.lastOrder}
            {placedAt ? ` · ${fmtDateShort(placedAt)}` : ''} · {S.states.products(lines.length)}
          </p>
        </div>
      </div>
      <ul className="divide-y divide-line-2 px-4 md:px-5">
        {shown.map((l) => (
          <li key={l.item.item_code} className="flex items-center gap-3 py-2.5">
            <button type="button" onClick={() => openProduct(l.item.item_code, 'order_again')} className="h-11 w-11 shrink-0 overflow-hidden rounded-sm border border-line-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70" aria-label={productName(l.item)}>
              <ProductImage item={l.item} alt="" sizes={SIZES_THUMB} size={44} imgClassName="p-1" iconSize={16} showCaption={false} />
            </button>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold text-ink">{productName(l.item)}</span>
              <span className="block text-xs text-ink-2 tnum">
                {l.qty} × {money(unitAt(l.item, l.qty))}
                {l.item.stock_status === 'out_of_stock' ? <span className="ms-1.5 font-medium text-bad">{S.card.soldOut}</span> : null}
              </span>
            </span>
            <span className="text-sm font-semibold tnum text-ink">{bhd((unitAt(l.item, l.qty) ?? 0) * l.qty)}</span>
          </li>
        ))}
      </ul>
      {more > 0 && <p className="px-4 pb-2 text-xs text-ink-2 md:px-5">{S.home.andMore(more)}</p>}
      <div className="flex gap-2 border-t border-line-2 p-3 md:p-4">
        <Button size="lg" className="flex-1" onClick={addAll} icon={<ArrowRight size={16} aria-hidden="true" />}>
          {S.home.addAll(lines.length, bhd(total))}
        </Button>
        <Button size="lg" variant="secondary" onClick={() => navigate('/quick?load=last')}>
          {S.home.editQty}
        </Button>
      </div>
    </section>
  )
}

/** Compact "welcome" line for recognised merchants / storefronts. */
export function TradePill({ className }: { className?: string }) {
  return <span className={cn('inline-flex h-8 items-center rounded-full bg-plum-soft px-3 text-xs font-semibold text-plum-ink', className)}>{S.home.tradePrices}</span>
}

/* ───────────────────────── unfinished order ─────────────────────────
   The honest version of the "your cart is almost sold out" popup: a quiet card, once per
   session, when the merchant comes back with lines in the cart. The thumbnails advance on
   their own (pause on touch/hover, off under reduced motion); "Only a few left" appears only
   when the server says low_stock; one button. Never a modal, never invented urgency. */

const RESUME_KEY = 'yq-resume-seen'

export function ResumeOrderCard({ lines, itemsByCode, total }: { lines: { item_code: string; qty: number }[]; itemsByCode: Map<string, ShopItem>; total: number }) {
  const [show] = useState(() => {
    try {
      return !sessionStorage.getItem(RESUME_KEY)
    } catch {
      return true
    }
  })
  const strip = useRef<HTMLDivElement>(null)
  const paused = useRef(false)
  useEffect(() => {
    if (!show) return
    try {
      sessionStorage.setItem(RESUME_KEY, '1')
    } catch {
      /* ignore */
    }
  }, [show])
  useEffect(() => {
    if (!show || lines.length < 3) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const id = window.setInterval(() => {
      const el = strip.current
      if (!el || paused.current) return
      const atEnd = el.scrollLeft + el.clientWidth >= el.scrollWidth - 4
      el.scrollTo({ left: atEnd ? 0 : el.scrollLeft + 72, behavior: 'smooth' })
    }, 2400)
    return () => window.clearInterval(id)
  }, [show, lines.length])
  if (!show || !lines.length) return null
  const units = lines.reduce((s, l) => s + l.qty, 0)
  const low = lines.filter((l) => itemsByCode.get(l.item_code)?.stock_status === 'low_stock').length
  return (
    <section className="mt-3 overflow-hidden rounded-xl border border-plum/20 bg-surface shadow-1" aria-label={S.home.resume}>
      <div className="flex items-center gap-3 px-4 pt-3.5">
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-base font-bold text-ink">{S.home.resume}</h2>
          <p className="text-xs text-ink-2">
            {S.cart.summary(lines.length, units)} · <span className="tnum">{bhd(total)}</span>
            {low > 0 && (
              <>
                {' '}
                · <span className="font-medium text-warn">{low} × {S.home.fewLeft}</span>
              </>
            )}
          </p>
        </div>
        <Link to="/cart" className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-sm bg-plum px-3.5 text-sm font-semibold text-white hover:bg-plum-deep focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
          {S.home.resumeCta} <ArrowRight size={15} aria-hidden="true" />
        </Link>
      </div>
      <div
        ref={strip}
        onPointerDown={() => (paused.current = true)}
        onPointerUp={() => (paused.current = false)}
        onMouseEnter={() => (paused.current = true)}
        onMouseLeave={() => (paused.current = false)}
        className="rail mt-3 px-4 pb-3.5"
        style={{ ['--m-rail-gap' as string]: '8px' }}
      >
        {lines.map((l) => {
          const item = itemsByCode.get(l.item_code)
          const lowStock = item?.stock_status === 'low_stock'
          return (
            <div key={l.item_code} className="relative w-16 shrink-0">
              <div className="overflow-hidden rounded-sm border border-line-2">
                <ProductImage item={item} alt={(item ? productName(item) : l.item_code)} sizes={SIZES_THUMB} size={64} imgClassName="p-1" iconSize={16} showCaption={false} />
              </div>
              <span className="absolute -end-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full bg-ink px-1 text-[10px] font-bold tnum text-white ring-2 ring-surface">{l.qty}</span>
              {lowStock && <span className="absolute inset-x-0 bottom-0 rounded-b-sm bg-warn/90 px-1 text-center text-[9px] font-semibold leading-4 text-white">{S.home.fewLeft}</span>}
            </div>
          )
        })}
      </div>
    </section>
  )
}
