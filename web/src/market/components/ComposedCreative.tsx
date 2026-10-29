import { useEffect, useReducer, useRef, useState, type CSSProperties, type RefObject } from 'react'
import { useLayerOpen } from '@/lib/sheet'
import { cn } from '@/lib/utils'
import { onSlideActive, slideIdle, whenWatched, SLIDE_THUMB_SIZES, type SlideArt, type SlideCanvas, type SlideSize } from '../lib/slides'
import { useShellViewport } from '../shell/ShellContext'
import { isDesktopLike, useReducedMotion } from '../shell/useViewport'
import { ProductImage } from '../ui/ProductImage'

/**
 * A campaign creative composed from real products — no upload, no crop, always on-brand: up to
 * three product photos on white discs, fanned −8° / 0 / +8°, the first (centre) one largest. Each
 * photo is object-contain inside a padding that keeps the whole square inside its circle, so a
 * product is never cut. Geometry lives in the "v3: slider" block of market.css (`.creative`): the
 * root is a size container that fills its parent (give the parent a size), the discs are sized in
 * container units, so the composition scales with the card and reserves its space (no CLS).
 *
 * Motion: when the slide around it is marked `data-slide-state="idle"` (PromoSlider) the discs sit
 * a little lower and tighter; becoming active springs them into the fan (CSS transition, staggered,
 * reduced-motion safe). Decorative: the slide's text says what it is.
 *
 * The reel (29-Oct-2026, round 2: the owner asked for the banners to feel like a video): given a
 * `reel` pool, a desktop's discs turn through more of the slide's own products — the centre every
 * 2.2 s, the sides 0.7 s and 1.4 s after it — each new photo zooming in over the last one (market.css,
 * "The banner's motion"). It starts once the page can be watched (lib/slides whenWatched: loaded,
 * idle, the opening gone), so slide 1's first frame is the preloaded photo; a slide coming on stage
 * shows its own photos for 2.2 s first. It holds on an idle slide, a hovered card, behind an open panel
 * and in a hidden tab; never on a phone or tablet, a lite device or under reduced motion.
 *
 * Never a white disc: the next photo waits hidden in the centre disc; every photo keeps one layer
 * (keyed by its code) from waiting to showing to leaving, so none loads twice; and the leaving photo
 * goes only once the new one has fully arrived (its m-reel-in ended) — on a busy laptop the compositor
 * can hold a swap's start back for a few hundred ms (measured 29-Oct-2026), so a disc mid-swap also
 * skips its next turn. The reel's state is one reducer: the turns (timers) and the arrivals
 * (animation events) interleave freely without a stale copy undoing a swap.
 */

const POS = ['c', 'l', 'r'] as const
const REEL_MS = 2200
const REEL_LAG = [0, 700, 1400]

export interface ComposedCreativeProps {
  products: SlideArt[]
  /** more photos of the same pool for the desktop reel (lib/slides `reel`); none, no reel */
  reel?: SlideArt[]
  canvas: SlideCanvas
  size: SlideSize
  /** slide 1: the centre photo loads eager + fetchpriority high (the LCP candidate), the rest eager */
  priority?: boolean
  className?: string
  /** night canvas: paint the static horizon behind the discs (SlideCard paints one for the whole card and passes false) */
  backdrop?: boolean
}

interface Disc {
  cur: SlideArt
  /** the photo the new one is covering, until it has arrived */
  prev: SlideArt | null
  /** turns so far (0: the slide's own photo, loaded as it always was) */
  tick: number
  /** when the last turn started (performance.now) */
  at: number
}

interface Reel {
  discs: Disc[]
  /** where the next turn looks in the cycle (the reel pool, then the slide's own products) */
  cursor: number
  /** the photo the next turn will likely show, loading hidden in the centre disc */
  upcoming: SlideArt | null
}

type ReelAct = { type: 'reset'; products: SlideArt[] } | { type: 'arm'; cycle: SlideArt[] } | { type: 'turn'; k: number; cycle: SlideArt[]; at: number } | { type: 'arrived'; k: number; tick: number }

const fresh = (products: SlideArt[]): Reel => ({ discs: products.map((p) => ({ cur: p, prev: null, tick: 0, at: 0 })), cursor: 0, upcoming: null })

/** the products on show, the leaving ones included: a product shows in one disc at a time */
const onShow = (discs: Disc[]) => new Set(discs.flatMap((d) => (d.prev ? [d.cur.item_code, d.prev.item_code] : [d.cur.item_code])))

/** the first product of the cycle from `cursor` that is not on show, and the cursor after it */
function nextUp(cycle: SlideArt[], cursor: number, shown: Set<string>): [SlideArt | null, number] {
  for (let n = 0; n < cycle.length; n++) {
    const c = cycle[(cursor + n) % cycle.length]
    if (!shown.has(c.item_code)) return [c, (cursor + n + 1) % cycle.length]
  }
  return [null, cursor]
}

function reelStep(s: Reel, a: ReelAct): Reel {
  switch (a.type) {
    case 'reset':
      return fresh(a.products)
    case 'arm':
      return { ...s, upcoming: nextUp(a.cycle, s.cursor, onShow(s.discs))[0] }
    case 'turn': {
      const d = s.discs[a.k]
      // a disc still swapping waits for its new photo to arrive (unless that swap is long stuck)
      if (!d || (d.prev && a.at - d.at < 2 * REEL_MS)) return s
      const shown = onShow(s.discs)
      const [c, cursor] = nextUp(a.cycle, s.cursor, shown)
      if (!c) return s
      const discs = s.discs.slice()
      discs[a.k] = { cur: c, prev: d.cur, tick: d.tick + 1, at: a.at }
      shown.add(c.item_code)
      return { discs, cursor, upcoming: nextUp(a.cycle, cursor, shown)[0] }
    }
    case 'arrived': {
      const d = s.discs[a.k]
      if (!d || d.tick !== a.tick || !d.prev) return s
      const discs = s.discs.slice()
      discs[a.k] = { ...d, prev: null }
      return { ...s, discs }
    }
  }
}

function useReel(root: RefObject<HTMLDivElement | null>, products: SlideArt[], reel: SlideArt[] | undefined) {
  const viewport = useShellViewport()
  const still = useReducedMotion()
  const layered = useLayerOpen()
  const codes = products.map((p) => p.item_code).join(' ')
  const pool = reel ? reel.map((p) => p.item_code).join(' ') : ''
  const on = codes !== '' && pool !== '' && isDesktopLike(viewport) && !still
  const [state, dispatch] = useReducer(reelStep, products, fresh)
  const [seen, setSeen] = useState(codes)
  if (seen !== codes) {
    // the slide's products changed (the data refreshed): the reel starts again from them
    setSeen(codes)
    dispatch({ type: 'reset', products })
  }
  const layeredRef = useRef(layered)
  const pools = useRef({ products, reel })
  useEffect(() => {
    layeredRef.current = layered
    pools.current = { products, reel }
  })

  useEffect(() => {
    const el = root.current
    if (!on || !el) return
    const { products, reel = [] } = pools.current
    const cycle = [...reel, ...products]
    let started = false
    const chain: number[] = []
    // the slide in view and not held: not idle, not hovered, not under a panel, not in a hidden tab
    const live = () => !(document.hidden || layeredRef.current || 'lite' in document.documentElement.dataset || slideIdle(el) || el.closest('.slide-card')?.matches(':hover'))
    const schedule = (k: number, delay: number) => {
      chain[k] = window.setTimeout(() => {
        if (live()) dispatch({ type: 'turn', k, cycle, at: performance.now() })
        schedule(k, REEL_MS)
      }, delay)
    }
    const restart = () => {
      for (const t of chain) window.clearTimeout(t)
      products.forEach((_, k) => schedule(k, REEL_MS + REEL_LAG[k]))
    }
    const stopWatch = whenWatched(() => {
      started = true
      dispatch({ type: 'arm', cycle })
      restart()
    })
    // a slide coming on stage shows its own photos first; the reel turns 2.2 s later
    const stopActive = onSlideActive(el, () => {
      if (started) restart()
    })
    return () => {
      stopWatch()
      stopActive()
      for (const t of chain) window.clearTimeout(t)
    }
  }, [on, codes, pool, root])

  return { on, state, dispatch }
}

export function ComposedCreative({ products, reel, canvas, size, priority, className, backdrop = true }: ComposedCreativeProps) {
  const list = products.slice(0, 3)
  const root = useRef<HTMLDivElement>(null)
  const { on, state, dispatch } = useReel(root, list, reel)
  // the opening photos load as they always did (slide 1's centre is the LCP candidate); a reel photo is eager, it shows by swapping in
  const photo = (item: SlideArt, k: number, first: boolean) => (
    <ProductImage item={item} alt="" sizes={SLIDE_THUMB_SIZES[size]} size={320} priority={first && Boolean(priority) && k === 0} eager={first ? Boolean(priority) && k > 0 : true} className="h-full w-full rounded-full" imgClassName="p-[15%]" iconSize={22} showCaption={false} />
  )
  return (
    <div ref={root} className={cn('creative', className)} data-count={list.length} data-canvas={canvas} data-reel={on ? '' : undefined} aria-hidden="true">
      {backdrop && canvas === 'night' && (
        <div className="horizon is-small is-offset">
          <i />
          <i />
          <i />
          <i />
        </div>
      )}
      {state.discs.map((d, k) => (
        <div key={POS[k]} className="creative-disc" data-pos={POS[k]} style={{ '--k': k } as CSSProperties}>
          {d.prev && (
            <div key={d.prev.item_code} className="reel reel-out">
              {photo(d.prev, k, false)}
            </div>
          )}
          <div
            key={d.cur.item_code}
            className={cn('reel', d.tick > 0 && 'reel-in')}
            onAnimationEnd={(e) => {
              if (d.prev && e.target === e.currentTarget) dispatch({ type: 'arrived', k, tick: d.tick })
            }}
          >
            {photo(d.cur, k, d.tick === 0)}
          </div>
          {on && k === 0 && state.upcoming && (
            <div key={state.upcoming.item_code} className="reel reel-next">
              {photo(state.upcoming, k, false)}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
