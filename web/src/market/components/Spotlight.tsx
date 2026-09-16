import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, ChevronLeft, ChevronRight, Megaphone, Plus, Sparkles, TrendingDown, Percent, Zap } from 'lucide-react'
import type { ShopItem } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { track } from '../lib/events'
import { bhd, niceCategory, productName, useCountdown } from '../lib/format'
import { clearance, heroProduct, justArrived, liveOffer, priceDrops } from '../lib/home'
import { useCartLines } from '../store/cart'
import { S } from '../strings'
import { Button } from '../ui/Button'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { campaignText } from './CampaignStrip'
import { RepCard } from './RepCard'

/**
 * The aside's "what is happening at YQ right now" — the desktop dead space turned into a
 * selling surface. Every slide is built from real data (live offer, clearance count, price
 * drops, new arrivals, the top seller, the merchant's own cart pairs, the representative);
 * nothing is invented and nothing counts down unless the rule really ends. Rotates gently,
 * pauses on hover/focus, and stands still under prefers-reduced-motion.
 */

type Slide =
  | { key: string; kind: 'link'; icon: typeof Sparkles; kicker: string; title: string; body?: string; to: string; item?: ShopItem | null; img?: string | null; tone: 'plum' | 'warn' | 'ok' | 'ink' }
  | { key: string; kind: 'product'; kicker: string; item: ShopItem }
  | { key: string; kind: 'pairs'; items: ShopItem[] }
  | { key: string; kind: 'rep' }

const DWELL = 7000

function useReducedMotion(): boolean {
  const [pref, setPref] = useState(() => (typeof window !== 'undefined' ? window.matchMedia('(prefers-reduced-motion: reduce)').matches : false))
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const on = () => setPref(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return pref
}

export function Spotlight({ className }: { className?: string }) {
  const { data, items, categories, rep, pairsFor, add, defaultQty, campaigns } = useMarket()
  const lines = useCartLines()
  const reduced = useReducedMotion()
  const [i, setI] = useState(0)
  const [hold, setHold] = useState(false)

  const slides = useMemo<Slide[]>(() => {
    const out: Slide[] = []
    for (const c of campaigns.filter((x) => x.placement.includes('aside')).slice(0, 3)) {
      const t = campaignText(c)
      out.push({ key: `c${c.id}`, kind: 'link', icon: Megaphone, kicker: c.sponsored ? S.campaign.sponsored(c.sponsor_name || '') : S.campaign.title, title: t.title, body: t.line || undefined, to: c.cta_to, img: c.image_url, tone: 'plum' })
    }
    const inCart = new Set(lines.map((l) => l.item_code))
    if (lines.length) {
      const seen = new Set<string>()
      const pairs: ShopItem[] = []
      for (const l of lines) {
        for (const p of pairsFor(l.item_code)) {
          if (inCart.has(p.item_code) || seen.has(p.item_code) || p.stock_status === 'out_of_stock') continue
          seen.add(p.item_code)
          pairs.push(p)
          if (pairs.length >= 3) break
        }
        if (pairs.length >= 3) break
      }
      if (pairs.length) out.push({ key: 'pairs', kind: 'pairs', items: pairs })
    }
    const offer = liveOffer(data)
    if (offer) out.push({ key: 'offer', kind: 'link', icon: Percent, kicker: S.rails.offers, title: offer.name, body: offer.summary || undefined, to: '/shop?f=offers', tone: 'plum' })
    const aging = clearance(items)
    if (aging.length) out.push({ key: 'clearance', kind: 'link', icon: Percent, kicker: S.rails.clearance, title: S.spot.clearance(aging.length), body: S.rails.clearanceHint, to: '/shop?f=clearance', item: aging[0], tone: 'warn' })
    const drops = priceDrops(items)
    if (drops.length) out.push({ key: 'drops', kind: 'link', icon: TrendingDown, kicker: S.rails.drops, title: S.spot.drops(drops.length), body: S.spot.dropsBody, to: '/shop?f=drops', item: drops[0], tone: 'ok' })
    const hero = heroProduct(items, categories)
    if (hero) out.push({ key: 'hero', kind: 'product', kicker: hero.rank ? S.home.rank(hero.rank, niceCategory(hero.category)) : S.home.hero, item: hero.item })
    const arrived = justArrived(items)
    if (arrived.length >= 3) out.push({ key: 'new', kind: 'link', icon: Sparkles, kicker: S.rails.arrived, title: S.spot.arrived(arrived.length), to: '/shop?f=new', item: arrived[0], tone: 'plum' })
    out.push({ key: 'quick', kind: 'link', icon: Zap, kicker: S.nav.quick, title: S.spot.quick, body: S.spot.quickBody, to: '/quick', tone: 'ink' })
    if (rep) out.push({ key: 'rep', kind: 'rep' })
    return out
  }, [data, items, categories, rep, pairsFor, lines, campaigns])

  const n = slides.length
  const idx = n ? i % n : 0

  useEffect(() => {
    if (n < 2 || hold || reduced) return
    const id = window.setInterval(() => setI((v) => (v + 1) % n), DWELL)
    return () => window.clearInterval(id)
  }, [n, hold, reduced])

  if (!n) return null
  const slide = slides[idx]
  const go = (d: number) => setI((idx + d + n) % n)

  return (
    <section
      className={cn('rounded-xl border border-line bg-surface shadow-1', className)}
      aria-roledescription="carousel"
      aria-label={S.spot.title}
      onMouseEnter={() => setHold(true)}
      onMouseLeave={() => setHold(false)}
      onFocusCapture={() => setHold(true)}
      onBlurCapture={() => setHold(false)}
    >
      <header className="flex items-center justify-between px-4 pt-3">
        <h2 className="text-xs font-semibold uppercase tracking-[0.08em] text-ink-3">{S.spot.title}</h2>
        {n > 1 && (
          <div className="flex items-center gap-0.5">
            <button type="button" onClick={() => go(-1)} aria-label={S.spot.prev} className="grid h-7 w-7 place-items-center rounded-full text-ink-3 hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              <ChevronLeft size={15} aria-hidden="true" />
            </button>
            <button type="button" onClick={() => go(1)} aria-label={S.spot.next} className="grid h-7 w-7 place-items-center rounded-full text-ink-3 hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              <ChevronRight size={15} aria-hidden="true" />
            </button>
          </div>
        )}
      </header>

      <div key={slide.key} className="px-4 pb-3 pt-2 anim-fade-in" aria-live="polite" aria-label={S.spot.slide(idx + 1, n)}>
        {slide.kind === 'link' && <LinkSlide slide={slide} />}
        {slide.kind === 'product' && <ProductSlide kicker={slide.kicker} item={slide.item} onAdd={() => { add(slide.item, undefined, 'spotlight'); }} qty={defaultQty(slide.item)} />}
        {slide.kind === 'pairs' && <PairsSlide items={slide.items} onAdd={(it) => add(it, undefined, 'spotlight_pairs')} />}
        {slide.kind === 'rep' && rep && (
          <div>
            <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-plum">{S.rep.yours}</div>
            <RepCard rep={rep} className="mt-2 border-0 bg-plum-wash" />
          </div>
        )}
      </div>

      {n > 1 && (
        <div className="flex items-center justify-center gap-1.5 pb-3" role="tablist" aria-label={S.spot.title}>
          {slides.map((s, k) => (
            <button
              key={s.key}
              type="button"
              role="tab"
              aria-selected={k === idx}
              aria-label={S.spot.slide(k + 1, n)}
              onClick={() => setI(k)}
              className={cn('h-1.5 rounded-full transition-all duration-2 ease-m', k === idx ? 'w-5 bg-plum' : 'w-1.5 bg-line hover:bg-ink-3')}
            />
          ))}
        </div>
      )}
    </section>
  )
}

const TONE = {
  plum: 'bg-plum-wash text-plum-ink',
  warn: 'bg-warn-soft text-warn',
  ok: 'bg-ok-soft text-ok',
  ink: 'bg-ink text-white',
}

function LinkSlide({ slide }: { slide: Extract<Slide, { kind: 'link' }> }) {
  const Icon = slide.icon
  return (
    <Link to={slide.to} onClick={() => track('rail_click', { meta: { rail: 'spotlight', code: slide.key } })} className={cn('group flex items-center gap-3 rounded-lg p-3 transition duration-1 ease-m hover:-translate-y-0.5', TONE[slide.tone])}>
      {slide.img ? (
        <img src={slide.img} alt="" width={64} height={64} loading="lazy" className="h-16 w-16 shrink-0 rounded-md object-cover" />
      ) : slide.item ? (
        <span className="h-16 w-16 shrink-0 overflow-hidden rounded-md bg-white">
          <ProductImage item={slide.item} alt="" sizes={SIZES_THUMB} size={64} imgClassName="p-1" iconSize={18} showCaption={false} />
        </span>
      ) : (
        <span className={cn('grid h-12 w-12 shrink-0 place-items-center rounded-md', slide.tone === 'ink' ? 'bg-white/10' : 'bg-white/70')}>
          <Icon size={20} aria-hidden="true" />
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block text-2xs font-semibold uppercase tracking-[0.08em] opacity-80">{slide.kicker}</span>
        <span className="mt-0.5 block font-display text-[15px] font-bold leading-tight">{slide.title}</span>
        {slide.body && <span className="mt-0.5 block text-xs leading-snug opacity-80">{slide.body}</span>}
      </span>
      <ArrowRight size={16} className="shrink-0 opacity-70 transition-transform duration-1 group-hover:translate-x-0.5" aria-hidden="true" />
    </Link>
  )
}

function ProductSlide({ kicker, item, qty, onAdd }: { kicker: string; item: ShopItem; qty: number; onAdd: () => void }) {
  return (
    <div className="flex items-center gap-3">
      <span className="h-20 w-20 shrink-0 overflow-hidden rounded-md border border-line-2 bg-white">
        <ProductImage item={item} alt="" sizes={SIZES_THUMB} size={80} imgClassName="p-1.5" iconSize={20} showCaption={false} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-plum">{kicker}</div>
        <div className="mt-0.5 line-clamp-2 font-display text-[15px] font-bold leading-tight text-ink">{productName(item)}</div>
        <div className="mt-1 flex items-center justify-between gap-2">
          <span className="font-display text-base font-extrabold tnum text-plum">{bhd(item.price_bhd)}</span>
          <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={onAdd}>
            {S.card.add} · {qty}
          </Button>
        </div>
      </div>
    </div>
  )
}

function PairsSlide({ items, onAdd }: { items: ShopItem[]; onAdd: (it: ShopItem) => void }) {
  return (
    <div>
      <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-plum">{S.rails.together}</div>
      <ul className="mt-2 divide-y divide-line-2">
        {items.map((it) => (
          <li key={it.item_code} className="flex items-center gap-2.5 py-2">
            <span className="h-10 w-10 shrink-0 overflow-hidden rounded-sm border border-line-2 bg-white">
              <ProductImage item={it} alt="" sizes={SIZES_THUMB} size={40} imgClassName="p-0.5" iconSize={14} showCaption={false} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold text-ink">{productName(it)}</span>
              <span className="block text-xs tnum text-ink-2">{bhd(it.price_bhd)}</span>
            </span>
            <button type="button" onClick={() => onAdd(it)} aria-label={`${S.card.add} — ${productName(it)}`} className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-line text-plum transition duration-1 ease-m hover:border-plum hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              <Plus size={16} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function PopularRows({ limit = 3 }: { limit?: number }) {
  const { items, add, defaultQty } = useMarket()
  const lines = useCartLines()
  const inCart = new Set(lines.map((l) => l.item_code))
  const rows = items.filter((it) => it.badges?.includes('best_seller') && it.stock_status !== 'out_of_stock' && !inCart.has(it.item_code)).slice(0, limit)
  if (!rows.length) return null
  return (
    <div className="border-t border-line-2 px-4 pb-4 pt-3 text-start">
      <div className="text-2xs font-semibold uppercase tracking-[0.08em] text-ink-3">{S.spot.popular}</div>
      <ul className="mt-1.5 divide-y divide-line-2">
        {rows.map((it) => (
          <li key={it.item_code} className="flex items-center gap-2.5 py-2">
            <span className="h-10 w-10 shrink-0 overflow-hidden rounded-sm border border-line-2 bg-white">
              <ProductImage item={it} alt="" sizes={SIZES_THUMB} size={40} imgClassName="p-0.5" iconSize={14} showCaption={false} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold text-ink">{productName(it)}</span>
              <span className="block text-xs tnum text-ink-2">{bhd(it.price_bhd)}</span>
            </span>
            <button type="button" onClick={() => add(it, undefined, 'minicart_popular')} aria-label={`${S.card.add} ${defaultQty(it)} — ${productName(it)}`} className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-line text-plum transition duration-1 ease-m hover:border-plum hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
              <Plus size={16} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Kept for parity with the offer strip: a live rule with a real end shows how long is left. */
export function OfferEnds({ endsAt }: { endsAt?: string | null }) {
  const t = useCountdown(endsAt)
  return t ? <span className="text-2xs text-ink-3">{S.home.offerEnds(t)}</span> : null
}
