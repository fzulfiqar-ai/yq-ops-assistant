import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Clock, ClipboardList, Megaphone, Package, RotateCcw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { track } from '../lib/events'
import { useCountdown } from '../lib/format'
import { endsSoon, firstSlideImage, type Slide, type SlideSize } from '../lib/slides'
import { S } from '../strings'
import { ComposedCreative } from './ComposedCreative'

/**
 * The visual unit of every promotion: one link on a pastel (or plum / night) canvas — kicker,
 * title, line and a CTA pill on the left, the art on the right (a composed creative, or the
 * uploaded image: contained on the canvas, or behind the whole card with a scrim when the admin
 * chose `cover`). Shared by the phone slider, the desktop hero stage, the hero tiles, the aside
 * Spotlight and a category banner. The card keeps a fixed aspect per size (override with
 * className, e.g. `aspect-auto h-full`), is a CSS size container so its type scales with it (the
 * "v3: slider" block in market.css), and is ONE tab stop.
 *
 * Honest by construction: the sticker is a word the data backs (Last chance, New, Price drops),
 * the ends chip only appears for a real end within 7 days, a sponsored campaign always says so.
 */

export interface SlideCardProps {
  slide: Slide
  size: SlideSize
  /** slide 1 / the LCP candidate: its first image loads eager with fetchpriority high */
  priority?: boolean
  className?: string
  /** analytics surface for rail_click (default: slider · tile · spotlight by size) */
  where?: string
}

const FRAME: Record<SlideSize, string> = {
  phone: 'aspect-[2/1] rounded-lg',
  hero: 'aspect-[21/9] rounded-xl',
  tile: 'aspect-[2/1] rounded-lg',
  aside: 'aspect-[6/5] rounded-lg',
}
/* Logical, not physical: the art sits at the inline END of the card and the copy at the inline
 * START, so the whole slide mirrors in Arabic. The two painted shapes that cannot follow a writing
 * mode — the .slide-sash clip-path and the .slide-scrim gradient angle — are mirrored under
 * [dir='rtl'] in the "v3: slider" block of market.css. */
const ART: Record<SlideSize, string> = {
  phone: 'inset-y-0 end-0 w-[45%]',
  hero: 'inset-y-[7%] end-[3%] w-[42%]',
  tile: 'inset-y-0 end-0 w-[42%]',
  aside: 'end-0 top-0 h-[46%] w-[70%]',
}
const COPY: Record<SlideSize, string> = {
  phone: 'inset-y-0 start-0 w-[57%] justify-center py-3 ps-4 pe-1',
  hero: 'inset-y-0 start-0 w-[56%] justify-center py-[5%] ps-[5.5%] pe-2',
  tile: 'inset-y-0 start-0 w-[60%] justify-center py-3 ps-4 pe-1 lg:ps-5',
  aside: 'inset-x-0 bottom-0 justify-end p-4',
}
/** the sticker's corner; a tile is too small to carry one */
const STICKER_AT: Record<SlideSize, string | null> = {
  phone: 'end-2.5 top-2.5',
  hero: 'end-[4%] top-[9%]',
  tile: null,
  aside: 'start-3 top-3',
}
/** a real end within 7 days sits in the top corner, clear of the (always visible) sponsor kicker */
const ENDS_AT: Record<SlideSize, string> = {
  phone: 'end-2.5 top-2.5',
  hero: 'end-[3%] top-[6%]',
  tile: 'end-2 top-2',
  aside: 'end-3 top-3',
}
const STICKER_TONE = {
  deal: 'bg-deal text-deal-ink',
  fresh: 'bg-fresh-ink text-white',
  drop: 'bg-bad text-white',
} as const
const CTA: Record<SlideSize, string> = {
  // the pill may run a little past the copy column, under the empty corner of the first disc
  phone: 'mt-2.5 h-[30px] max-w-[calc(100%+1.75rem)] gap-1 px-3 text-[12px]',
  hero: 'mt-6 h-11 gap-2 px-5 text-[15px]',
  tile: 'mt-2',
  aside: 'mt-3 h-9 gap-1.5 px-3.5 text-[13px]',
}
const WHERE: Record<SlideSize, string> = { phone: 'slider', hero: 'slider', tile: 'tile', aside: 'spotlight' }

/** A picture for a slide with no photo at all (a catalog without images): one disc, one glyph. */
function SlideGlyph({ slide }: { slide: Slide }) {
  const props = { className: 'h-[40%] w-[40%]', strokeWidth: 1.5 }
  if (slide.kind === 'campaign') return <Megaphone {...props} />
  if (slide.id === 'd:quick') return <ClipboardList {...props} />
  if (slide.id === 'd:again') return <RotateCcw {...props} />
  return <Package {...props} />
}

function EndsChip({ endsAt, dark, className }: { endsAt: string; dark: boolean; className?: string }) {
  useCountdown(endsAt) // re-render once a minute while mounted
  const t = endsSoon(endsAt)
  if (!t) return null
  return (
    <span className={cn('z-[4] inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-[3px] text-2xs font-semibold', dark ? 'bg-white/15 text-white' : 'bg-white/80 text-ink', className)}>
      <Clock size={11} aria-hidden="true" />
      {S.home.offerEnds(t)}
    </span>
  )
}

export function SlideCard({ slide, size, priority, className, where }: SlideCardProps) {
  const dark = slide.canvas === 'plum' || slide.canvas === 'night'
  const cover = slide.image?.fit === 'cover'
  const img = slide.image ? firstSlideImage(slide, size) : null
  const kicker = slide.kicker || slide.sponsored || null

  const imgTag = img && (
    <img
      src={img.src}
      srcSet={img.srcset}
      sizes={img.srcset ? img.sizes : undefined}
      alt=""
      width={1200}
      height={600}
      loading={priority ? 'eager' : 'lazy'}
      decoding="async"
      fetchPriority={priority ? 'high' : undefined}
      draggable={false}
      data-fit={cover ? 'cover' : 'contain'}
      className={cover ? 'absolute inset-0 h-full w-full object-cover' : 'slide-photo h-full w-full object-contain'}
    />
  )

  const body: ReactNode = (
    <>
      {slide.canvas === 'night' ? (
        <div className="horizon is-small is-offset" aria-hidden="true">
          <i />
          <i />
          <i />
          <i />
        </div>
      ) : (
        !cover && <span className="slide-sash" aria-hidden="true" />
      )}
      {cover && imgTag}
      {(cover || slide.canvas === 'night') && <span className="slide-scrim" aria-hidden="true" />}

      {!cover && (
        <div className={cn('absolute', ART[size], size === 'hero' ? 'p-0' : size === 'phone' ? 'py-1' : 'py-0.5')} aria-hidden="true">
          {imgTag ? (
            <div className={cn('h-full w-full', size === 'hero' ? 'p-2' : 'p-2.5')}>{imgTag}</div>
          ) : slide.products.length ? (
            <ComposedCreative products={slide.products} canvas={slide.canvas} size={size} priority={priority} backdrop={false} />
          ) : (
            <div className="creative" data-count="1" data-canvas={slide.canvas}>
              <div className="creative-disc grid place-items-center text-plum" data-pos="c">
                <SlideGlyph slide={slide} />
              </div>
            </div>
          )}
        </div>
      )}

      {slide.sticker && !cover && STICKER_AT[size] && (
        <span className={cn('slide-sticker absolute', STICKER_AT[size], STICKER_TONE[slide.sticker.tone])} aria-hidden="true">
          {slide.sticker.label}
        </span>
      )}
      {slide.endsAt && <EndsChip endsAt={slide.endsAt} dark={dark && !cover} className={cn('absolute', ENDS_AT[size])} />}

      <span className={cn('slide-copy absolute flex flex-col', COPY[size])}>
        {kicker && <span className={cn('slide-kicker mb-1.5 truncate', dark ? 'text-white/80' : 'text-plum')}>{kicker}</span>}
        <span className={cn('slide-title line-clamp-2 font-display font-extrabold', dark ? 'text-white' : 'text-ink')}>{slide.title}</span>
        {slide.line && <span className={cn('slide-line mt-1 line-clamp-2 text-[12.5px] leading-4', size === 'hero' && 'mt-3 max-w-[34ch] leading-snug', dark ? 'text-white/80' : 'text-ink-2')}>{slide.line}</span>}
        {slide.to && (
          <span
            className={cn(
              'slide-cta inline-flex w-fit max-w-full items-center whitespace-nowrap font-semibold transition-colors duration-2 ease-m',
              CTA[size],
              size === 'tile'
                ? cn('text-[13px] underline-offset-4 group-hover:underline lg:text-sm', dark ? 'text-white' : 'text-ink')
                : cn('rounded-full', dark ? 'bg-white text-ink group-hover:bg-plum-soft' : 'bg-ink text-white group-hover:bg-plum'),
            )}
          >
            <span className="truncate">{slide.cta}</span>
            <ArrowRight size={size === 'hero' ? 17 : 14} aria-hidden="true" className="shrink-0 transition-transform duration-2 ease-m group-hover:translate-x-0.5 rtl:-scale-x-100 rtl:group-hover:-translate-x-0.5" />
          </span>
        )}
      </span>
    </>
  )

  const cls = cn(
    'slide-card group relative isolate block overflow-hidden text-start',
    `canvas-${slide.canvas}`,
    FRAME[size],
    // the ring is an overlay: an inset box-shadow would paint under the art and the horizon
    slide.to && 'after:pointer-events-none after:absolute after:inset-0 after:z-10 after:rounded-[inherit] focus-visible:outline-none focus-visible:after:shadow-[inset_0_0_0_3px_hsl(var(--m-focus)),inset_0_0_0_5px_#fff]',
    className,
  )
  const onClick = () => track('rail_click', { meta: { rail: where || WHERE[size], code: slide.id } })

  if (!slide.to) {
    return (
      <div className={cls} data-size={size}>
        {body}
      </div>
    )
  }
  if (/^https?:\/\//i.test(slide.to)) {
    return (
      <a href={slide.to} target="_blank" rel="noreferrer" onClick={onClick} className={cls} data-size={size}>
        {body}
      </a>
    )
  }
  return (
    <Link to={slide.to} onClick={onClick} className={cls} data-size={size}>
      {body}
    </Link>
  )
}
