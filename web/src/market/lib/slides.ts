import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type FocusEvent, type PointerEvent, type RefObject } from 'react'
import type { Campaign, CampaignCanvas, ShopItem } from '@/lib/shopApi'
import { locale, S } from '../strings'
import { categorySlug, countdownLabel, hasBadge, niceCategory } from './format'
import { dealSets, hasPhoto, inStock } from './home'

/**
 * The promo slide model, built ONCE per render from real data and shared by the phone slider,
 * the desktop hero + tiles and the aside Spotlight — so a campaign is never shown twice (the
 * `placement[]` multi-select used to be filtered independently in three places) and a thin
 * campaign list is padded with data slides instead of repeating one card.
 *
 * Pure, except the claim store (Home ↔ Spotlight) and the carousel timer hook at the bottom.
 */

export type SlideCanvas = CampaignCanvas
export type SlideKind = 'campaign' | 'data'
/** The four renderings of a slide (components/SlideCard.tsx) — each has its own `sizes` strings below. */
export type SlideSize = 'hero' | 'phone' | 'tile' | 'aside'
type Placement = 'hero' | 'strip' | 'aside'

export interface Slide {
  /** 'c:<campaign id>' | 'd:again' | 'd:last' | 'd:drops' | 'd:essentials' | 'd:fresh' | 'd:moving' | 'd:quick' */
  id: string
  kind: SlideKind
  /** small line above the title, e.g. S.deals.title, or the sponsor label on a sponsored campaign */
  kicker?: string | null
  title: string
  line?: string | null
  cta: string
  to: string
  canvas: SlideCanvas
  /** uploaded campaign image (1200 w in `src`, 600 w in `src600` when the upload made one) */
  image?: { src: string; src600?: string | null; fit: 'contain' | 'cover' } | null
  /** 0–3 items with photos for a composed creative (used when `image` is null) */
  products: ShopItem[]
  /** the sponsor label when the campaign is sponsored — same text as `kicker`; render one of them */
  sponsored?: string | null
  endsAt?: string | null
  campaignId?: number | null
  /** a round word sticker the data backs (never a number): Last chance, New, Price drops */
  sticker?: { label: string; tone: 'deal' | 'fresh' | 'drop' } | null
}

export interface SlideContext {
  items: ShopItem[]
  /** already audience-filtered by MarketContext */
  campaigns: Campaign[]
  recognized: boolean
  /** items of the merchant's newest order (in stock), when known */
  lastOrder?: ShopItem[] | null
}

export interface SlideOptions {
  exclude?: ReadonlySet<string>
  kinds?: SlideKind[]
  max?: number
  /**
   * Which campaign placements to include, in priority order (default ['hero', 'strip']). The aside
   * Spotlight passes ['aside', 'hero', 'strip'] so a campaign placed only in the aside still shows.
   */
  placements?: Placement[]
}

/** Pastel canvases a campaign without its own canvas cycles through. */
const CAMPAIGN_CYCLE: readonly SlideCanvas[] = ['lilac', 'apricot', 'mint']
const MIN_DATA = 3
const ART = 3

/**
 * The slides, in order: campaigns (by placement priority, hero before strip) → data slides, each
 * only when the data backs it (last chance ≥3, price drops ≥2, restock essentials ≥3, new ≥3,
 * moving ≥3) → paste-a-list (always). Order again (a recognised merchant with a last order) goes
 * SECOND, so slide 1 depends on the catalog payload alone — public/catalog-prefetch.js preloads its
 * image before the app has even downloaded (mirror any change to the slide-1 rules there).
 * `exclude` drops slide ids (e.g. the ones Home already shows), `kinds` keeps only those kinds,
 * `max` caps (default 6).
 */
export function buildSlides(ctx: SlideContext, opts?: SlideOptions): Slide[] {
  const max = Math.max(0, opts?.max ?? 6)
  const kinds = opts?.kinds ? new Set(opts.kinds) : null
  const exclude = opts?.exclude
  const placements = opts?.placements ?? ['hero', 'strip']
  const out: Slide[] = []
  if (!max) return out
  const byCode = new Map(ctx.items.map((i) => [i.item_code, i]))

  const again = ctx.recognized ? inStock(ctx.lastOrder || []) : []
  const allowed = (id: string, kind: SlideKind) => (!kinds || kinds.has(kind)) && !exclude?.has(id)
  // Order again takes one of the `max` places (never slide 1, so with max 1 it stays out)
  const withAgain = again.length > 0 && max >= 2 && allowed('d:again', 'data')
  const cap = withAgain ? max - 1 : max

  // Composed art keeps the pool's order but prefers photos no earlier slide used and, within a
  // slide, different categories — three look-alike chargers make a dull creative. `strict` takes
  // the pool's top photos as they are (last chance: the best margins).
  const usedArt = new Set<string>()
  const art = (pool: ShopItem[], strict = false): ShopItem[] => {
    const photos = pool.filter(hasPhoto)
    let pick: ShopItem[] = photos.slice(0, ART)
    if (!strict) {
      pick = []
      const cats = new Set<string>()
      const passes = [(i: ShopItem) => !usedArt.has(i.item_code) && !cats.has(i.category || ''), (i: ShopItem) => !usedArt.has(i.item_code), () => true]
      for (const ok of passes) {
        for (const i of photos) {
          if (pick.length >= ART) break
          if (pick.includes(i) || !ok(i)) continue
          pick.push(i)
          cats.add(i.category || '')
        }
      }
    }
    for (const i of pick) usedArt.add(i.item_code)
    return pick
  }
  const wants = (id: string, kind: SlideKind) => out.length < cap && allowed(id, kind) && !out.some((s) => s.id === id)
  const push = (slide: Slide) => void out.push(slide)

  /* 1 · campaigns, by placement priority; each campaign once */
  const rank = (c: Campaign) => {
    const at = (c.placement || []).map((p) => placements.indexOf(p as Placement)).filter((n) => n >= 0)
    return at.length ? Math.min(...at) : -1
  }
  const promoted = ctx.campaigns
    .map((c, order) => ({ c, order, rank: rank(c) }))
    .filter((x) => x.rank >= 0)
    .sort((a, b) => a.rank - b.rank || a.order - b.order)
    .map((x) => x.c)
  for (let n = 0; n < promoted.length; n++) {
    if (!wants(`c:${promoted[n].id}`, 'campaign')) continue
    const slide = campaignSlide(promoted[n], CAMPAIGN_CYCLE[n % CAMPAIGN_CYCLE.length], byCode)
    for (const i of slide.products) usedArt.add(i.item_code)
    push(slide)
  }

  /* 2 · data slides — only what the catalog can back */
  const live = inStock(ctx.items)
  const deals = dealSets(ctx.items)
  const best = live.filter((i) => hasBadge(i, 'best_seller'))

  if (deals.lastChance.length >= MIN_DATA && wants('d:last', 'data')) {
    push({ id: 'd:last', kind: 'data', kicker: deals.hasRealDeals ? S.deals.title : null, title: S.slides.last, line: S.slides.lastLine(deals.lastChance.length), cta: S.slides.lastCta, to: '/shop?f=clearance', canvas: 'apricot', products: art(deals.lastChance, true), sticker: { label: S.deals.badge, tone: 'deal' } })
  }
  if (deals.drops.length >= 2 && wants('d:drops', 'data')) {
    push({ id: 'd:drops', kind: 'data', kicker: S.deals.title, title: S.slides.drops(deals.drops.length), line: S.slides.dropsLine, cta: S.slides.dropsCta, to: '/shop?f=drops', canvas: 'lilac', products: art(deals.drops), sticker: { label: S.deals.drops, tone: 'drop' } })
  }
  if (best.length >= MIN_DATA && wants('d:essentials', 'data')) {
    push({ id: 'd:essentials', kind: 'data', kicker: null, title: S.slides.essentials, line: S.slides.essentialsLine, cta: S.slides.essentialsCta, to: '/shop?f=best', canvas: 'mint', products: art(best) })
  }
  const fresh = live.filter((i) => hasBadge(i, 'new'))
  if (fresh.length >= MIN_DATA && wants('d:fresh', 'data')) {
    push({ id: 'd:fresh', kind: 'data', kicker: null, title: S.slides.fresh, line: S.slides.freshLine(fresh.length), cta: S.slides.freshCta, to: '/shop?f=new', canvas: 'lilac', products: art(fresh), sticker: { label: S.slides.stickerNew, tone: 'fresh' } })
  }
  const moving = live.filter((i) => hasBadge(i, 'trending') || hasBadge(i, 'selling_fast'))
  if (moving.length >= MIN_DATA && wants('d:moving', 'data')) {
    push({ id: 'd:moving', kind: 'data', kicker: null, title: S.slides.moving, line: S.slides.movingLine, cta: S.slides.movingCta, to: '/shop?f=moving', canvas: 'apricot', products: art(moving) })
  }
  if (wants('d:quick', 'data')) {
    push({ id: 'd:quick', kind: 'data', kicker: null, title: S.slides.quick, line: S.slides.quickLine, cta: S.slides.quickCta, to: '/quick', canvas: 'night', products: art(best.length ? best : live) })
  }

  /* 3 · Order again, second (its art is picked last so slide 1's art never depends on it) */
  if (withAgain) {
    const slide: Slide = { id: 'd:again', kind: 'data', kicker: S.home.lastOrder, title: S.slides.again, line: S.slides.againLine(again.length), cta: S.slides.againCta, to: '/quick?load=last', canvas: 'plum', products: art(again) }
    out.splice(Math.min(1, out.length), 0, slide)
  }
  return out
}

/** One campaign as a slide (Arabic copy when the locale is Arabic; sponsor label always carried). */
function campaignSlide(c: Campaign, fallbackCanvas: SlideCanvas, byCode: Map<string, ShopItem>): Slide {
  const ar = locale.lang === 'ar'
  const sponsor = c.sponsored ? S.campaign.sponsored(c.sponsor_name || '') : null
  return {
    id: `c:${c.id}`,
    kind: 'campaign',
    kicker: sponsor,
    title: (ar && c.title_ar) || c.title,
    line: (ar && c.line_ar) || c.line || null,
    cta: (ar && c.cta_label_ar) || c.cta_label || S.campaign.cta,
    to: c.cta_to || '/shop',
    canvas: c.canvas || fallbackCanvas,
    image: c.image_url ? { src: c.image_url, src600: c.image_url_600 || null, fit: c.image_fit === 'cover' ? 'cover' : 'contain' } : null,
    products: (c.product_codes || [])
      .map((code) => byCode.get(code))
      .filter((i): i is ShopItem => i != null && hasPhoto(i))
      .slice(0, ART),
    sponsored: sponsor,
    endsAt: c.ends_at || null,
    campaignId: c.id,
  }
}

/**
 * The header creative of one category's shelf (CampaignStrip.tsx CategoryBanner): the campaign an
 * admin placed on that category; else, when the shelf's `items` are given, a banner composed from
 * them — the category name, its in-stock line count, the one message its data backs (last-chance
 * lines → price drops → new arrivals; the CTA filters this shelf) and three of those photos. With
 * no such message the banner is not a link. Null when there is nothing to show.
 */
export function categorySlide(campaigns: Campaign[], category: string, items?: ShopItem[] | null): Slide | null {
  const key = category.toUpperCase()
  const pool = (items || []).filter((i) => (i.category || 'OTHER').toUpperCase() === key)
  const c = campaigns.find((x) => (x.placement || []).includes('category') && (x.category || '').toUpperCase() === key)
  if (c) return campaignSlide(c, 'lilac', new Map(pool.map((i) => [i.item_code, i])))
  const live = inStock(pool)
  if (!live.length) return null
  const deals = dealSets(pool)
  const fresh = live.filter((i) => hasBadge(i, 'new'))
  const best = live.filter((i) => hasBadge(i, 'best_seller'))
  const shelf = `/t/${categorySlug(category)}`
  const base = { kind: 'data' as const, kicker: S.campaign.categoryStock(live.length), title: niceCategory(category) }
  let slide: Slide
  if (deals.lastChance.length) {
    slide = { ...base, id: 'k:last', line: S.spot.clearance(deals.lastChance.length), cta: S.slides.lastCta, to: `${shelf}?f=clearance`, canvas: 'apricot', products: deals.lastChance, sticker: { label: S.deals.badge, tone: 'deal' } }
  } else if (deals.drops.length) {
    slide = { ...base, id: 'k:drops', line: S.spot.drops(deals.drops.length), cta: S.slides.dropsCta, to: `${shelf}?f=drops`, canvas: 'lilac', products: deals.drops, sticker: { label: S.deals.drops, tone: 'drop' } }
  } else if (fresh.length) {
    slide = { ...base, id: 'k:fresh', line: S.spot.arrived(fresh.length), cta: S.slides.freshCta, to: `${shelf}?f=new`, canvas: 'mint', products: fresh, sticker: { label: S.slides.stickerNew, tone: 'fresh' } }
  } else {
    slide = { ...base, id: 'k:shelf', line: null, cta: '', to: '', canvas: 'lilac', products: [...best, ...live.filter((i) => !best.includes(i))] }
  }
  slide.products = slide.products.filter(hasPhoto).slice(0, ART)
  return slide.products.length ? slide : null
}

/* ───────────────────────── image sizes (shared with public/catalog-prefetch.js) ───────────────────────── */

/**
 * `sizes` strings for slide imagery, copied VERBATIM into public/catalog-prefetch.js so the preload
 * and the rendered <img> pick the same candidate — change both together.
 *   SLIDE_SIZES        the whole card: an uploaded image with fit 'cover' (600/1200 w)
 *   SLIDE_ART_SIZES    the art column: an uploaded image with fit 'contain' (600/1200 w)
 *   SLIDE_THUMB_SIZES  one disc of a composed creative (product thumbs 160/320/512 w)
 * Widths follow components/SlideCard.tsx: phone cards are 88% of the track (84vw of a 390 phone,
 * 56vw on a tablet); the desktop hero stage spans the main column (≈860–1000 px beside the 320/360
 * aside); tiles are the pastel cards under it (or a category banner); aside is the Spotlight card.
 */
export const SLIDE_SIZES: Record<SlideSize, string> = {
  hero: '(min-width: 1440px) 1000px, (min-width: 1024px) 900px, 92vw',
  phone: '(min-width: 768px) 56vw, 84vw',
  tile: '(min-width: 1024px) 480px, 92vw',
  aside: '(min-width: 1440px) 336px, 296px',
}
export const SLIDE_ART_SIZES: Record<SlideSize, string> = {
  hero: '(min-width: 1440px) 420px, (min-width: 1024px) 380px, 40vw',
  phone: '(min-width: 768px) 25vw, 38vw',
  tile: '(min-width: 1024px) 200px, 40vw',
  aside: '(min-width: 1440px) 220px, 196px',
}
export const SLIDE_THUMB_SIZES: Record<SlideSize, string> = {
  hero: '(min-width: 1440px) 208px, (min-width: 1024px) 184px, 20vw',
  phone: '(min-width: 768px) 13vw, 19vw',
  tile: '(min-width: 1024px) 112px, 20vw',
  aside: '(min-width: 1440px) 104px, 96px',
}

export interface SlideImage {
  src: string
  srcset?: string
  sizes: string
}

/** The srcset ui/ProductImage.tsx builds from `thumb_urls` — keep the two identical. */
export function thumbSrcset(set: Record<string, string>): string {
  return `${set['160'] || set['320']} 160w, ${set['320']} 320w, ${set['512'] || set['320']} 512w`
}

/**
 * The image slide 1 paints first, exactly as the app requests it: the uploaded image (600/1200 w
 * srcset; `sizes` by fit) or the first product thumb of a composed creative (the centre disc).
 * Null when the slide has no picture. public/catalog-prefetch.js mirrors this.
 */
export function firstSlideImage(slide: Pick<Slide, 'image' | 'products'>, size: SlideSize = 'phone'): SlideImage | null {
  const image = slide.image
  if (image?.src) {
    return { src: image.src, srcset: image.src600 ? `${image.src600} 600w, ${image.src} 1200w` : undefined, sizes: (image.fit === 'cover' ? SLIDE_SIZES : SLIDE_ART_SIZES)[size] }
  }
  const first = slide.products[0]
  if (!first) return null
  const set = first.thumb_urls
  if (set && set['320']) return { src: set['320'], srcset: thumbSrcset(set), sizes: SLIDE_THUMB_SIZES[size] }
  const src = [first.thumb_url, first.product_image_url, first.package_image_url].find((s): s is string => typeof s === 'string' && s.length > 0)
  return src ? { src, sizes: SLIDE_THUMB_SIZES[size] } : null
}

/** "Ends in 2d 4h" only for a real end inside the next `days` days — never an invented countdown. */
export function endsSoon(endsAt?: string | null, days = 7): string | null {
  if (!endsAt) return null
  const end = new Date(endsAt).getTime()
  if (Number.isNaN(end) || end - Date.now() > days * 86400000) return null
  return countdownLabel(endsAt)
}

/* ───────────────────────── claim store (Home ↔ Spotlight) ───────────────────────── */

const claims = new Map<string, number>()
const EMPTY: ReadonlySet<string> = new Set()
let snapshot: ReadonlySet<string> = EMPTY
let claimants = 0
const listeners = new Set<() => void>()

function publish(): void {
  snapshot = claims.size ? new Set(claims.keys()) : EMPTY
  for (const l of listeners) l()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

const getSnapshot = () => snapshot
const getServerSnapshot = () => EMPTY
const getClaiming = () => claimants > 0
const getNotClaiming = () => false

/**
 * A surface registers the slide ids it shows while mounted (reference-counted, released on
 * unmount). A LAYOUT effect: the claim lands before the browser paints, so a Spotlight rendered in
 * the same commit re-renders without the claimed slides and never shows them for a frame.
 * PromoSlider claims its own slides; Home claims the hero tiles it renders outside the slider.
 */
export function useClaimSlides(ids: readonly string[]): void {
  const key = ids.join('\n')
  useLayoutEffect(() => {
    const list = key ? key.split('\n') : []
    claimants++
    for (const id of list) claims.set(id, (claims.get(id) || 0) + 1)
    publish()
    return () => {
      claimants--
      for (const id of list) {
        const n = (claims.get(id) || 0) - 1
        if (n > 0) claims.set(id, n)
        else claims.delete(id)
      }
      publish()
    }
  }, [key])
}

/** The slide ids some mounted surface already shows — Spotlight passes them as `exclude`. */
export function useClaimedSlides(): ReadonlySet<string> {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

/** True while at least one surface (a PromoSlider, Home) is registered, even with no ids. */
export function useSlideClaimsActive(): boolean {
  return useSyncExternalStore(subscribe, getClaiming, getNotClaiming)
}

/* ───────────────────────── carousel timing (PromoSlider, Spotlight) ───────────────────────── */

/** Dwell per slide, and the quiet period after the merchant touched the carousel. */
export const SLIDE_DWELL = 5500
export const SLIDE_RESUME = 8000

function subscribeVisibility(fn: () => void): () => void {
  document.addEventListener('visibilitychange', fn)
  return () => document.removeEventListener('visibilitychange', fn)
}
const getHidden = () => document.hidden
const getShown = () => false

export interface Carousel {
  /** the active slide, clamped to the slide count */
  index: number
  /** autoplay is counting down right now (drives the dot fill's animation-play-state) */
  running: boolean
  /** autoplay can run at all: more than one slide and motion allowed */
  autoplay: boolean
  userPaused: boolean
  /** the merchant navigated: show `i` and hold autoplay for SLIDE_RESUME */
  go: (i: number) => void
  /** follow the index without a hold (the phone track syncing to its scroll position) */
  sync: (i: number) => void
  /** a user interaction that is not navigation (touch, scroll of the track) */
  hold: () => void
  togglePause: () => void
  /** spread on the carousel root: hover (mouse only) and keyboard focus pause while they last */
  bind: {
    onPointerEnter: (e: PointerEvent) => void
    onPointerLeave: (e: PointerEvent) => void
    onPointerDown: () => void
    onFocus: (e: FocusEvent) => void
    onBlur: (e: FocusEvent) => void
  }
}

/**
 * One timer for a carousel with every pause rule: hover, keyboard focus inside, a pointer/touch
 * interaction or user scroll (resumes SLIDE_RESUME after the last one), the pause button, a hidden
 * tab, the carousel off-screen, and never under reduced motion. Pausing keeps the elapsed dwell, so
 * a CSS fill driven by `running` (animation-play-state) stays in step with the timer. `onAdvance`
 * runs before the index moves (the phone track scrolls there).
 */
export function useCarousel({ count, dwell = SLIDE_DWELL, reduced, rootRef, onAdvance }: { count: number; dwell?: number; reduced: boolean; rootRef: RefObject<HTMLElement | null>; onAdvance?: (next: number) => void }): Carousel {
  const [rawIndex, setIndex] = useState(0)
  const [hover, setHover] = useState(false)
  const [focused, setFocused] = useState(false)
  const [held, setHeld] = useState(false)
  const [userPaused, setUserPaused] = useState(false)
  const [onScreen, setOnScreen] = useState(true)
  const hidden = useSyncExternalStore(subscribeVisibility, getHidden, getShown)
  const index = count ? Math.min(rawIndex, count - 1) : 0
  const autoplay = count > 1 && !reduced
  const running = autoplay && !userPaused && !held && !hover && !focused && !hidden && onScreen

  const holdTimer = useRef(0)
  const hold = useCallback(() => {
    setHeld(true)
    window.clearTimeout(holdTimer.current)
    holdTimer.current = window.setTimeout(() => setHeld(false), SLIDE_RESUME)
  }, [])
  useEffect(() => () => window.clearTimeout(holdTimer.current), [])

  // re-attached when slides appear: a carousel that rendered nothing at first has no root yet
  const hasSlides = count > 0
  useEffect(() => {
    const el = rootRef.current
    if (!hasSlides || !el || typeof IntersectionObserver !== 'function') return
    const io = new IntersectionObserver(([entry]) => setOnScreen(entry.isIntersecting), { threshold: 0.3 })
    io.observe(el)
    return () => io.disconnect()
  }, [rootRef, hasSlides])

  const advanceRef = useRef(onAdvance)
  useLayoutEffect(() => {
    advanceRef.current = onAdvance
  })
  const elapsed = useRef(0)
  // declared before the timer: on an index change React runs the timer's cleanup (which banks the
  // elapsed time), then this reset, then the new timer with a full dwell
  useEffect(() => {
    elapsed.current = 0
  }, [index])
  useEffect(() => {
    if (!running) return
    const started = performance.now()
    const t = window.setTimeout(() => {
      const next = (index + 1) % count
      advanceRef.current?.(next)
      setIndex(next)
    }, Math.max(0, dwell - elapsed.current))
    return () => {
      window.clearTimeout(t)
      elapsed.current += performance.now() - started
    }
  }, [running, index, count, dwell])

  const go = useCallback(
    (i: number) => {
      setIndex(i)
      hold()
    },
    [hold],
  )
  const togglePause = useCallback(() => {
    if (userPaused) {
      // pressing Play resumes now, not after the interaction hold
      window.clearTimeout(holdTimer.current)
      setHeld(false)
    }
    setUserPaused(!userPaused)
  }, [userPaused])

  const bind = {
    onPointerEnter: (e: PointerEvent) => {
      if (e.pointerType === 'mouse') setHover(true)
    },
    onPointerLeave: (e: PointerEvent) => {
      if (e.pointerType === 'mouse') setHover(false)
    },
    onPointerDown: hold,
    onFocus: (e: FocusEvent) => {
      // keyboard focus only: a mouse click leaves focus on a dot, which must not pause for good
      let visible = true
      try {
        visible = (e.target as Element).matches(':focus-visible')
      } catch {
        /* old engine: treat as keyboard */
      }
      if (visible) setFocused(true)
    },
    onBlur: (e: FocusEvent) => {
      if (!(e.currentTarget as Element).contains(e.relatedTarget as Node | null)) setFocused(false)
    },
  }

  return { index, running, autoplay, userPaused, go, sync: setIndex, hold, togglePause, bind }
}
