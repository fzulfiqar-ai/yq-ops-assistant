import { useMemo } from 'react'
import { ArrowDown, ArrowRight, MessageCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { UpcomingItem } from '../lib/marketApi'
import { useMarket } from '../MarketContext'
import { ProductImage } from '../ui/ProductImage'
import { asCreative, askRangeUrl, sectionLabel, stageArt, upcomingCopy } from './ComingSoonShared'
import { ComposedCreative } from './ComposedCreative'

/**
 * The top of the brand page ("Coming soon"), in the home page's own two-panel language:
 *
 *   ComingSoonStage   the dark night stage (the "Restock faster" look): glow horizon, scrim, three
 *                     round product discs from three different categories, a round "Coming soon"
 *                     sticker, the month (payload), the headline, one line, and a white "See the
 *                     range" pill that scrolls to the grid
 *   ComingSoonTile    the lilac tile beside it on desktop-like widths: "Price on arrival", the
 *                     step-up line, one box-photo disc; the whole tile is one target — "Ask {rep}
 *                     on WhatsApp" when the shop came through a rep link, otherwise a scroll to
 *                     the range ("Notify me on any model")
 *   ComingSoonCircles the home page's colourful category circles, one per section, no counts —
 *                     each a button that scrolls to its section
 *
 * These reuse SlideCard's classes (slide-card, canvas-*, horizon, slide-*, creative) and copy its
 * hero / phone / tile geometry, but are not SlideCard: that component drives the home page's first
 * paint and stays untouched. Class names are written out literally on purpose — several of them
 * (the horizon's modifiers, slide-*, creative*) are not in the Tailwind safelist and must be found
 * in source. Every jump is a button (scrollIntoView through `onJump`), never a #hash link, so the
 * URL — and a rep's ?ref= — stays as it came in.
 */

type StageSize = 'hero' | 'phone'

/* SlideCard's FRAME / ART / COPY / STICKER_AT / CTA entries for these sizes, copied, not imported */
const FRAME: Record<StageSize, string> = {
  phone: 'aspect-[2/1] rounded-lg',
  hero: 'aspect-[21/9] rounded-xl',
}
const ART: Record<StageSize, string> = {
  phone: 'inset-y-0 end-0 w-[36%] pe-1.5 py-1',
  hero: 'inset-y-[7%] end-[3%] w-[42%] p-0',
}
/* One deviation from SlideCard, for a copy column that always carries four rows (month, a
 * two-line headline, a two-line line, the pill): on a hero stage 700 px wide or less — the desktop
 * grid from 1024 to ~1700 — the rhythm tightens (padding, the line's and the pill's top margin),
 * because 21:9 of ~560 px is ~240 px tall and the full rhythm needed ~210 px of it inside the
 * padding. The rows are also shrink-0: whatever still does not fit overflows into the padding,
 * never into a clamped line's glyphs. */
const COPY: Record<StageSize, string> = {
  phone: 'inset-y-0 start-0 w-[60%] justify-center py-3 ps-4 pe-1',
  hero: 'inset-y-0 start-0 w-[56%] justify-center py-[5%] ps-[5.5%] pe-2 [@container(max-width:700px)]:py-[3.5%]',
}
const STICKER_AT: Record<StageSize, string> = {
  phone: 'end-2.5 top-2.5',
  hero: 'end-[4%] top-[9%]',
}
const CTA: Record<StageSize, string> = {
  phone: 'mt-2.5 h-[30px] max-w-full gap-1 px-3 text-[12px]',
  hero: 'mt-6 h-11 gap-2 px-5 text-[15px] [@container(max-width:700px)]:mt-3.5',
}

export interface ComingSoonStageProps {
  brand: string
  /** "Arriving October" — the payload's month, never a literal */
  when: string
  items: UpcomingItem[]
  size: StageSize
  /** h1 where the page has no other (desktop-like); h2 under the phone header's own h1 */
  heading: 'h1' | 'h2'
  onSeeRange: () => void
  className?: string
}

export function ComingSoonStage({ brand, when, items, size, heading, onSeeRange, className }: ComingSoonStageProps) {
  const t = upcomingCopy()
  const art = useMemo(() => stageArt(items), [items])
  const Title = heading
  return (
    <section aria-labelledby="brand-hero" data-size={size} className={cn('slide-card canvas-night group relative isolate block overflow-hidden text-start', FRAME[size], className)}>
      <div className="horizon is-small is-offset" aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </div>
      <span className="slide-scrim" aria-hidden="true" />
      {art.length > 0 && (
        <div className={cn('slide-art absolute', ART[size])} aria-hidden="true">
          <ComposedCreative products={art} canvas="night" size={size} priority backdrop={false} />
        </div>
      )}
      <span className={cn('slide-sticker absolute bg-fresh-ink text-white', STICKER_AT[size])} aria-hidden="true">
        {t.kicker}
      </span>

      <div className={cn('slide-copy absolute flex flex-col', COPY[size])}>
        {when && <p className="slide-kicker mb-1.5 shrink-0 truncate text-white/80">{when}</p>}
        {/* pb: the clamp's overflow box would otherwise shave the descenders off line 2 ("coming to YQ") */}
        <Title id="brand-hero" className="slide-title line-clamp-2 shrink-0 pb-[0.1em] font-display font-extrabold text-white">
          {t.headline(brand)}
        </Title>
        <p className={cn('slide-line mt-1 line-clamp-2 shrink-0 text-[12.5px] leading-4 text-white/80', size === 'hero' && 'mt-3 max-w-[34ch] leading-snug [@container(max-width:700px)]:mt-2')}>{t.stageLine}</p>
        <button
          type="button"
          onClick={onSeeRange}
          className={cn(
            'slide-cta hit relative inline-flex w-fit max-w-full shrink-0 items-center whitespace-nowrap rounded-full bg-white font-semibold text-ink transition-colors duration-2 ease-m hover:bg-plum-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-night',
            CTA[size],
          )}
        >
          <span className="truncate">{t.seeAll}</span>
          <ArrowDown size={size === 'hero' ? 17 : 14} aria-hidden="true" className="shrink-0" />
        </button>
      </div>
    </section>
  )
}

export interface ComingSoonTileProps {
  brand: string
  items: UpcomingItem[]
  onNotifyAny: () => void
  className?: string
}

export function ComingSoonTile({ brand, items, onNotifyAny, className }: ComingSoonTileProps) {
  const t = upcomingCopy()
  const { rep } = useMarket()
  const askUrl = askRangeUrl(rep, brand)
  // one box photo: the first model with a box that is not already a disc on the stage
  const disc = useMemo(() => {
    const onStage = new Set(stageArt(items).map((a) => a.item_code))
    const it = items.find((i) => i.box_url && !onStage.has(`wk:${i.id}`)) || items.find((i) => i.box_url)
    return it ? [asCreative(it, 'box')] : []
  }, [items])
  const cls = cn(
    'slide-card canvas-lilac group relative isolate block h-full w-full overflow-hidden rounded-lg text-start',
    // the ring is an overlay: an inset box-shadow would paint under the art
    'after:pointer-events-none after:absolute after:inset-0 after:z-10 after:rounded-[inherit] focus-visible:outline-none focus-visible:after:shadow-[inset_0_0_0_3px_hsl(var(--m-focus)),inset_0_0_0_5px_#fff]',
    className,
  )
  const body = (
    <>
      <span className="slide-sash" aria-hidden="true" />
      {disc.length > 0 && (
        <span className="slide-art absolute inset-y-0 end-0 w-[42%] py-0.5 pe-3" aria-hidden="true">
          <ComposedCreative products={disc} canvas="lilac" size="tile" backdrop={false} />
        </span>
      )}
      <span className="slide-copy absolute inset-y-0 start-0 flex w-[58%] flex-col justify-center py-3 ps-4 pe-1 lg:ps-5">
        <span className="slide-kicker mb-1.5 shrink-0 truncate text-plum">{t.price}</span>
        {/* three lines, and greedy wrapping: .slide-title's balance split "step-up" at its hyphen */}
        <span className="slide-title line-clamp-3 shrink-0 pb-[0.1em] font-display font-extrabold text-ink" style={{ textWrap: 'pretty' }}>
          {t.tileTitle}
        </span>
        <span className="slide-cta mt-2 inline-flex w-fit max-w-full shrink-0 items-center gap-1 whitespace-nowrap text-[13px] font-semibold text-ink underline-offset-4 transition-colors duration-2 ease-m group-hover:underline lg:text-sm">
          {askUrl && <MessageCircle size={14} aria-hidden="true" className="shrink-0 text-wa" />}
          <span className="truncate">{askUrl ? t.askRange(rep?.first_name || '') : t.notifyAny}</span>
          {askUrl ? (
            <ArrowRight size={14} aria-hidden="true" className="shrink-0 transition-transform duration-2 ease-m group-hover:translate-x-0.5 rtl:-scale-x-100 rtl:group-hover:-translate-x-0.5" />
          ) : (
            <ArrowDown size={14} aria-hidden="true" className="shrink-0" />
          )}
        </span>
      </span>
    </>
  )
  return askUrl ? (
    <a href={askUrl} target="_blank" rel="noreferrer" data-size="tile" className={cls}>
      {body}
    </a>
  ) : (
    <button type="button" onClick={onNotifyAny} data-size="tile" className={cls}>
      {body}
    </button>
  )
}

/** the home page's pastel rings and washes (HomeBlocks CategoryTiles), cycled across the row */
const TILE_RING = ['ring-tile-lilac', 'ring-tile-apricot', 'ring-tile-mint']
const TILE_WASH = ['bg-tile-lilac/60', 'bg-tile-apricot/60', 'bg-tile-mint/60']

export interface ComingSoonCirclesProps {
  sections: { id: string; category: string; first: UpcomingItem }[]
  onJump: (id: string) => void
  className?: string
}

export function ComingSoonCircles({ sections, onJump, className }: ComingSoonCirclesProps) {
  const t = upcomingCopy()
  if (sections.length < 2) return null
  return (
    <nav aria-label={t.rangeNav} className={className}>
      <ul className="grid grid-cols-4 gap-x-1.5 gap-y-3.5 md:grid-cols-8 lg:gap-x-3">
        {sections.map((s, i) => (
          <li key={s.id} className="min-w-0">
            <button
              type="button"
              onClick={() => onJump(s.id)}
              className="group flex w-full flex-col items-center rounded-md px-0.5 pb-1 pt-1 text-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
            >
              <span className={cn('relative isolate grid h-16 w-16 place-items-center rounded-full shadow-[0_1px_2px_hsl(268_30%_10%/0.06),0_8px_18px_-10px_hsl(268_30%_10%/0.22)] ring-2 transition duration-2 ease-m group-hover:-translate-y-0.5 group-hover:shadow-2 group-hover:ring-plum/25 group-active:scale-95 min-[360px]:h-[72px] min-[360px]:w-[72px] lg:h-[88px] lg:w-[88px]', TILE_WASH[i % TILE_WASH.length], TILE_RING[i % TILE_RING.length])}>
                {/* the square inscribed in the disc (70.7%): no corner of the photo can fall outside the ring */}
                <span className="block h-[70%] w-[70%]">
                  <ProductImage item={asCreative(s.first, 'photo')} alt="" sizes="(min-width: 1024px) 88px, 72px" size={88} className="h-full w-full bg-transparent" imgClassName="mix-blend-multiply transition-transform duration-3 ease-m group-hover:scale-[1.07]" iconSize={20} showCaption={false} eager />
                </span>
              </span>
              <span className="mt-2 flex min-h-[30px] items-start justify-center lg:mt-2.5 lg:min-h-[36px]">
                <span className="line-clamp-2 text-[11px] font-semibold leading-[14px] text-ink min-[360px]:text-xs min-[360px]:leading-[15px] lg:text-sm lg:leading-[18px]">{sectionLabel(s.category) || s.first.brand}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  )
}
