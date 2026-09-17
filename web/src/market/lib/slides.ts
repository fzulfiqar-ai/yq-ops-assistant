import { useEffect, useSyncExternalStore } from 'react'
import type { Campaign, CampaignCanvas, ShopItem } from '@/lib/shopApi'
import { S } from '../strings'
import { hasBadge } from './format'
import { dealSets, hasPhoto, inStock } from './home'

/**
 * The promo slide model, built ONCE per render from real data and shared by the phone slider,
 * the desktop hero + tiles and the aside Spotlight — so a campaign is never shown twice (the
 * `placement[]` multi-select used to be filtered independently in three places) and a thin
 * campaign list is padded with data slides instead of repeating one card.
 *
 * Pure, except the tiny claim store at the bottom: Home registers the slide ids it shows,
 * Spotlight reads them and leaves those out.
 */

export type SlideCanvas = CampaignCanvas
export type SlideKind = 'campaign' | 'data'

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
}

export interface SlideContext {
  items: ShopItem[]
  /** already audience-filtered by MarketContext */
  campaigns: Campaign[]
  recognized: boolean
  /** items of the merchant's newest order (in stock), when known */
  lastOrder?: ShopItem[] | null
}

/** Pastel canvases a campaign without its own canvas cycles through. */
const CAMPAIGN_CYCLE: readonly SlideCanvas[] = ['lilac', 'apricot', 'mint']
const MIN_DATA = 3
const ART = 3

/**
 * The slides, in order: Order again (recognised merchant with a last order) → hero campaigns →
 * strip campaigns → data slides, each only when the data backs it (last chance ≥3, price drops
 * ≥2, restock essentials ≥3, new ≥3, moving ≥3) → paste-a-list (always). `exclude` drops slide
 * ids (e.g. the ones Home already shows), `kinds` keeps only those kinds, `max` caps (default 6).
 * Slide 1 of the default call is the LCP candidate that public/catalog-prefetch.js mirrors.
 */
export function buildSlides(ctx: SlideContext, opts?: { exclude?: ReadonlySet<string>; kinds?: SlideKind[]; max?: number }): Slide[] {
  const max = Math.max(0, opts?.max ?? 6)
  const kinds = opts?.kinds ? new Set(opts.kinds) : null
  const exclude = opts?.exclude
  const out: Slide[] = []
  if (!max) return out
  const byCode = new Map(ctx.items.map((i) => [i.item_code, i]))

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
  const wants = (id: string, kind: SlideKind) => out.length < max && (!kinds || kinds.has(kind)) && !exclude?.has(id) && !out.some((s) => s.id === id)
  const push = (slide: Slide) => void out.push(slide)

  /* 1 · Order again */
  const again = inStock(ctx.lastOrder || [])
  if (ctx.recognized && again.length && wants('d:again', 'data')) {
    push({ id: 'd:again', kind: 'data', kicker: null, title: S.slides.again, line: S.slides.againLine(again.length), cta: S.slides.againCta, to: '/quick?load=last', canvas: 'plum', products: art(again) })
  }

  /* 2 · campaigns: hero placement first, then strip; each campaign once */
  const heroes = ctx.campaigns.filter((c) => (c.placement || []).includes('hero'))
  const strips = ctx.campaigns.filter((c) => !(c.placement || []).includes('hero') && (c.placement || []).includes('strip'))
  const promoted = [...heroes, ...strips]
  for (let n = 0; n < promoted.length; n++) {
    const c = promoted[n]
    const id = `c:${c.id}`
    if (!wants(id, 'campaign')) continue
    const sponsor = c.sponsored ? S.campaign.sponsored(c.sponsor_name || '') : null
    const products = (c.product_codes || [])
      .map((code) => byCode.get(code))
      .filter((i): i is ShopItem => i != null && hasPhoto(i))
      .slice(0, ART)
    for (const i of products) usedArt.add(i.item_code)
    push({
      id,
      kind: 'campaign',
      kicker: sponsor,
      title: c.title,
      line: c.line || null,
      cta: c.cta_label || S.campaign.cta,
      to: c.cta_to || '/shop',
      canvas: c.canvas || CAMPAIGN_CYCLE[n % CAMPAIGN_CYCLE.length],
      image: c.image_url ? { src: c.image_url, src600: c.image_url_600 || null, fit: c.image_fit === 'cover' ? 'cover' : 'contain' } : null,
      products,
      sponsored: sponsor,
      endsAt: c.ends_at || null,
      campaignId: c.id,
    })
  }

  /* 3 · data slides — only what the catalog can back */
  const live = inStock(ctx.items)
  const deals = dealSets(ctx.items)
  const best = live.filter((i) => hasBadge(i, 'best_seller'))

  if (deals.lastChance.length >= MIN_DATA && wants('d:last', 'data')) {
    push({ id: 'd:last', kind: 'data', kicker: deals.hasRealDeals ? S.deals.title : null, title: S.slides.last, line: S.slides.lastLine(deals.lastChance.length), cta: S.slides.lastCta, to: '/shop?f=clearance', canvas: 'apricot', products: art(deals.lastChance, true) })
  }
  if (deals.drops.length >= 2 && wants('d:drops', 'data')) {
    push({ id: 'd:drops', kind: 'data', kicker: S.deals.title, title: S.slides.drops(deals.drops.length), line: S.slides.dropsLine, cta: S.slides.dropsCta, to: '/shop?f=drops', canvas: 'lilac', products: art(deals.drops) })
  }
  if (best.length >= MIN_DATA && wants('d:essentials', 'data')) {
    // there is no best-seller filter; the "Best sellers" See-all has always been the popular sort
    push({ id: 'd:essentials', kind: 'data', kicker: null, title: S.slides.essentials, line: S.slides.essentialsLine, cta: S.slides.essentialsCta, to: '/shop?sort=popular', canvas: 'mint', products: art(best) })
  }
  const fresh = live.filter((i) => hasBadge(i, 'new'))
  if (fresh.length >= MIN_DATA && wants('d:fresh', 'data')) {
    push({ id: 'd:fresh', kind: 'data', kicker: null, title: S.slides.fresh, line: S.slides.freshLine(fresh.length), cta: S.slides.freshCta, to: '/shop?f=new', canvas: 'lilac', products: art(fresh) })
  }
  const moving = live.filter((i) => hasBadge(i, 'trending') || hasBadge(i, 'selling_fast'))
  if (moving.length >= MIN_DATA && wants('d:moving', 'data')) {
    push({ id: 'd:moving', kind: 'data', kicker: null, title: S.slides.moving, line: S.slides.movingLine, cta: S.slides.movingCta, to: '/shop?sort=popular', canvas: 'apricot', products: art(moving) })
  }
  if (wants('d:quick', 'data')) {
    push({ id: 'd:quick', kind: 'data', kicker: null, title: S.slides.quick, line: S.slides.quickLine, cta: S.slides.quickCta, to: '/quick', canvas: 'night', products: art(best.length ? best : live) })
  }
  return out
}

/**
 * `sizes` for slide imagery, shared verbatim with public/catalog-prefetch.js so the preload and
 * the rendered <img> pick the same candidate. Widths follow the layout: phone slides ~88% of the
 * viewport; the desktop hero stage is the main column beside the two stacked tiles (aside open
 * from 1280); tiles are the stacked pastel cards; aside is the 320/360 px Spotlight.
 */
export const SLIDE_SIZES: { hero: string; phone: string; tile: string; aside: string } = {
  hero: '(min-width: 1440px) 760px, (min-width: 1280px) 620px, (min-width: 1024px) 640px, 88vw',
  phone: '(min-width: 768px) 60vw, 88vw',
  tile: '(min-width: 1440px) 380px, (min-width: 1024px) 300px, 44vw',
  aside: '(min-width: 1440px) 360px, 320px',
}

/* ───────────────────────── claim store (Home ↔ Spotlight) ───────────────────────── */

const claims = new Map<string, number>()
const EMPTY: ReadonlySet<string> = new Set()
let snapshot: ReadonlySet<string> = EMPTY
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

/** Home registers the slide ids it shows while mounted (reference-counted, released on unmount). */
export function useClaimSlides(ids: readonly string[]): void {
  const key = ids.join('\n')
  useEffect(() => {
    const list = key ? key.split('\n') : []
    if (!list.length) return
    for (const id of list) claims.set(id, (claims.get(id) || 0) + 1)
    publish()
    return () => {
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
