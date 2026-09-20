import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { ChevronLeft, ChevronRight, Pause, Play, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMarket } from '../MarketContext'
import { bhd, productName } from '../lib/format'
import { bestSellers } from '../lib/home'
import { buildSlides, useCarousel, useClaimedSlides, useSlideClaimsActive } from '../lib/slides'
import { useReducedMotion } from '../shell/useViewport'
import { useCartLines } from '../store/cart'
import { S } from '../strings'
import { ProductImage, SIZES_THUMB } from '../ui/ProductImage'
import { SlideCard } from './SlideCard'

/**
 * The aside's "Right now at YQ" — the desktop dead space turned into a selling surface, built from
 * the SAME slide model as the home slider (lib/slides.ts): campaigns placed in the aside first, then
 * hero/strip campaigns and data slides that no mounted surface already shows (the claim store), so
 * the aside never repeats the hero beside it. Every slide is real data; nothing counts down unless
 * a campaign really ends. Dissolves every 7 s (one slide at a time, never two at half opacity over
 * each other); pauses on hover, keyboard focus, interaction, a
 * hidden tab; still under reduced motion. The header carries a pause/play control, because hover
 * and focus are not a way to stop it (WCAG 2.2.2).
 *
 * No visible swap on Home: the slider claims its slides in a layout effect in the same commit, so
 * this re-renders before paint. A page without a slider gets SETTLE_MS to register one before the
 * aside shows anything (the aside sits below the mini-cart, so the late entrance moves nothing).
 *
 * On Home it renders NOTHING, by design, and there is deliberately no fallback. Home claims its
 * composition AND every id it states as a section of its own (SECTION_SLIDE_IDS), which today is
 * the whole deck — so the only slides left to "fall back" to are the ones the merchant is already
 * looking at. A fallback would put "Restock essentials" beside the essentials rail or a third
 * paste ask on one screen, which is the exact repetition this component was built to avoid. The
 * aside earns its column on Home through the mini-cart and its popular rows below instead.
 */

const DWELL = 7000
const MAX = 5
const SETTLE_MS = 400

export function Spotlight({ className }: { className?: string }) {
  const { items, campaigns, recognized } = useMarket()
  const claimed = useClaimedSlides()
  const claiming = useSlideClaimsActive()
  const reduced = useReducedMotion()
  const rootRef = useRef<HTMLElement>(null)

  const hasItems = items.length > 0
  const [settled, setSettled] = useState(false)
  useEffect(() => {
    if (!hasItems || settled) return
    const t = window.setTimeout(() => setSettled(true), SETTLE_MS)
    return () => window.clearTimeout(t)
  }, [hasItems, settled])

  const slides = useMemo(() => buildSlides({ items, campaigns, recognized }, { exclude: claimed, placements: ['aside', 'hero', 'strip'], max: MAX }), [items, campaigns, recognized, claimed])
  const n = claiming || settled ? slides.length : 0
  const c = useCarousel({ count: n, dwell: DWELL, reduced, rootRef })
  const idx = c.index

  const [shown, setShown] = useState({ cur: idx, prev: -1 })
  if (shown.cur !== idx) setShown({ cur: idx, prev: shown.cur })
  const prev = shown.cur === idx ? shown.prev : -1

  if (!n) return null
  const go = (d: number) => c.go((idx + d + n) % n)
  // 36 px under a mouse, the full 44 px on a touch screen (a tablet in landscape gets this shell too)
  const control =
    'grid h-9 w-9 place-items-center rounded-full text-ink-3 transition duration-1 ease-m hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11'

  return (
    <section ref={rootRef} className={cn('rounded-xl border border-line bg-surface p-3 shadow-1', className)} aria-roledescription="carousel" aria-label={S.spot.title} {...c.bind}>
      <header className="flex items-center justify-between ps-1">
        <h2 className="min-w-0 truncate text-2xs font-semibold uppercase tracking-[0.08em] text-ink-3">{S.spot.title}</h2>
        {n > 1 && (
          <div className="-me-1 flex items-center">
            {/* WCAG 2.2.2: the aside turns its own slides, so it must offer a way to stop them */}
            {c.autoplay && (
              <button type="button" onClick={c.togglePause} aria-label={c.userPaused ? S.slides.play : S.slides.pause} className={control}>
                {c.userPaused ? <Play size={14} aria-hidden="true" /> : <Pause size={14} aria-hidden="true" />}
              </button>
            )}
            <button type="button" onClick={() => go(-1)} aria-label={S.spot.prev} className={control}>
              <ChevronLeft size={16} aria-hidden="true" className="rtl:-scale-x-100" />
            </button>
            <button type="button" onClick={() => go(1)} aria-label={S.spot.next} className={control}>
              <ChevronRight size={16} aria-hidden="true" className="rtl:-scale-x-100" />
            </button>
          </div>
        )}
      </header>

      {/* the stage wears the incoming slide's canvas and the two slides are sequenced, never
          simultaneous (components/PromoSlider.tsx): a plain opacity crossfade painted both
          headlines on top of each other over a mix of two canvases */}
      <div className={cn('slider-stage relative mt-1.5 aspect-[6/5] overflow-hidden rounded-lg', `canvas-${slides[idx]?.canvas || 'lilac'}`)} aria-live={c.running ? 'off' : 'polite'}>
        {slides.map((s, i) => {
          const active = i === idx
          return (
            <div
              key={s.id}
              role="group"
              aria-roledescription="slide"
              aria-label={S.spot.slide(i + 1, n)}
              aria-hidden={active ? undefined : true}
              inert={!active}
              data-slide-state={active ? 'active' : 'idle'}
              className={cn('absolute inset-0 motion-reduce:transition-none', active ? 'z-[2] opacity-100 transition-opacity duration-[260ms] delay-[180ms] ease-m' : i === prev ? 'z-[1] opacity-0 transition-opacity duration-[200ms] ease-m' : 'z-0 opacity-0')}
            >
              <SlideCard slide={s} size="aside" where="spotlight" className="h-full w-full" />
            </div>
          )
        })}
      </div>

      {n > 1 && (
        <div className="mt-1 flex items-center justify-center">
          {slides.map((s, i) => {
            const active = i === idx
            return (
              <button key={s.id} type="button" onClick={() => c.go(i)} aria-label={S.spot.slide(i + 1, n)} aria-current={active ? 'true' : undefined} className="group/dot grid h-9 w-8 place-items-center rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-9">
                <span className={cn('relative block h-1.5 overflow-hidden rounded-full transition-[width,background-color] duration-3 ease-m', active ? 'w-5 bg-ink/15' : 'w-1.5 bg-ink/25 group-hover/dot:bg-ink/50')}>
                  {active && (
                    <span
                      key={`${s.id}:${idx}`}
                      /* the fill follows the reading direction: it grows from the inline start */
                      className={cn('slide-dwell absolute inset-0 origin-left rounded-full bg-plum rtl:origin-right', !c.autoplay && 'is-static')}
                      style={{ animationDuration: `${DWELL}ms`, animationPlayState: c.running ? 'running' : 'paused' } as CSSProperties}
                    />
                  )}
                </span>
              </button>
            )
          })}
        </div>
      )}
    </section>
  )
}

/**
 * The empty mini-cart's suggestions. The "Restock essentials" rail the aside stands beside is the
 * first RAIL_MAX best sellers (lib/home `bestSellers`), so taking the top of the same list printed
 * the rail's first three products again, in the same order, 300px to their right — an echo, not a
 * suggestion. These are the NEXT best sellers; the rail's own lines are kept as a fallback for a
 * shelf that has nothing else to offer, so the block never empties out.
 */
export function PopularRows({ limit = 3 }: { limit?: number }) {
  const { items, add, defaultQty } = useMarket()
  const lines = useCartLines()
  const inCart = new Set(lines.map((l) => l.item_code))
  const onRail = useMemo(() => new Set(bestSellers(items).map((i) => i.item_code)), [items])
  const pool = items.filter((it) => it.badges?.includes('best_seller') && it.stock_status !== 'out_of_stock' && !inCart.has(it.item_code))
  const rows = [...pool.filter((it) => !onRail.has(it.item_code)), ...pool.filter((it) => onRail.has(it.item_code))].slice(0, limit)
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
              {/* two lines, as in the mini-cart's own rows: this catalog hides the distinguishing
                  part at the END of the name ("… (2USB Port)" vs "… (USB + Type-C Port)", "1Mtr"
                  vs "2Mtr"), so a single clamped line offers three identical-looking chargers */}
              <span className="line-clamp-2 text-sm font-semibold leading-snug text-ink">{productName(it)}</span>
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
