import type { CatalogPayload, Offer, OrderStatusPayload, ShopItem } from '@/lib/shopApi'
import { recentlyViewed } from './device'
import { hasBadge, marginOf, priceAnchor } from './format'
import type { Slide } from './slides'
import { S } from '../strings'

/**
 * The home page's merchandising, as pure functions over the payload and device memory, so the
 * page is a block list and the rules live in one testable place.
 */

export const RAIL_MAX = 10

export function inStock(items: ShopItem[]): ShopItem[] {
  return items.filter((i) => i.stock_status !== 'out_of_stock')
}

export function bestSellers(items: ShopItem[]): ShopItem[] {
  return inStock(items).filter((i) => hasBadge(i, 'best_seller')).slice(0, RAIL_MAX)
}

/**
 * A price drop we can show: the price book really went down and the card prints the old trade
 * price (`was_bhd` above today's price — exactly when `priceAnchor()` returns it). A `price_drop`
 * badge alone is not enough: every "price drops" surface promises the old price on the card.
 */
export function hasRealDrop(i: ShopItem): boolean {
  return priceAnchor(i) != null
}

/* ───────────────────────── v3: deals, exclusive rails, brands ───────────────────────── */

/** Has a photo the slider / tiles can show (WebP set, legacy thumb or the full photo). */
export function hasPhoto(i: ShopItem): boolean {
  return Boolean(i.thumb_urls?.['320'] || i.thumb_url || i.product_image_url)
}

/**
 * Last-Chance Stock: clearing lines still on the shelf (low stock included), the best real
 * retail margin first (lines without a public retail price after), then shelf order. No cap.
 */
export function lastChance(items: ShopItem[]): ShopItem[] {
  return inStock(items)
    .filter((i) => hasBadge(i, 'clearance'))
    .map((item, index) => ({ item, index, pct: marginOf(item)?.pct ?? null }))
    .sort((a, b) => {
      if (a.pct != null && b.pct != null && a.pct !== b.pct) return b.pct - a.pct
      if ((a.pct == null) !== (b.pct == null)) return a.pct == null ? 1 : -1
      return a.index - b.index
    })
    .map((x) => x.item)
}

export interface DealSets {
  drops: ShopItem[]
  lastChance: ShopItem[]
  offers: ShopItem[]
  bundles: ShopItem[]
  all: ShopItem[]
  hasRealDeals: boolean
}

function isLiveOffer(o: Offer): boolean {
  if (!o.ends_at) return true
  const end = new Date(o.ends_at).getTime()
  return Number.isNaN(end) || end > Date.now()
}

/**
 * The Stock-Up Deals section, from real data only: price-book drops that carry the old price
 * (`hasRealDrop`), live offers, bundle rules, and last-chance lines. Every set is in stock. A code
 * sits in one of drops/offers/bundles first (drops and offers may share a code), and last-chance
 * never repeats one of them.
 */
export function dealSets(items: ShopItem[], offers?: Offer[] | null): DealSets {
  const live = inStock(items)
  const bundleCodes = new Set<string>()
  for (const o of offers || []) {
    const kind = (o.kind || '').toLowerCase()
    if (!isLiveOffer(o) || !(kind === 'bundle_price' || kind.includes('bundle'))) continue
    for (const c of o.scope_codes || []) bundleCodes.add(c)
  }
  const drops = live.filter(hasRealDrop)
  const bundles = live.filter((i) => bundleCodes.has(i.item_code))
  const offerItems = live.filter((i) => hasBadge(i, 'on_offer') && !bundleCodes.has(i.item_code))
  const taken = new Set([...drops, ...offerItems, ...bundles].map((i) => i.item_code))
  const last = lastChance(items).filter((i) => !taken.has(i.item_code))
  const seen = new Set<string>()
  const all: ShopItem[] = []
  for (const i of [...drops, ...offerItems, ...bundles, ...last]) {
    if (seen.has(i.item_code)) continue
    seen.add(i.item_code)
    all.push(i)
  }
  return { drops, lastChance: last, offers: offerItems, bundles, all, hasRealDeals: drops.length + offerItems.length + bundles.length > 0 }
}

/**
 * The one line a Deals shelf may claim: it names only the kinds that are really in the set. The
 * home Deals section and /shop?f=deals both read it, so they cannot drift apart — `hasRealDeals`
 * is true for a bare offer or bundle too, and promising "price drops" with none in the price book
 * would be a lie.
 */
export function dealsLine(sets: DealSets): string {
  const last = sets.lastChance.length > 0
  if (sets.drops.length) return last ? S.deals.dealsLine : S.deals.dealsLineDrops
  if (sets.offers.length + sets.bundles.length) return last ? S.deals.dealsLineMixed : S.deals.dealsLineOffers
  return S.deals.line
}

export interface HomeRails {
  deals: ShopItem[]
  essentials: ShopItem[]
  fresh: ShopItem[]
  moving: ShopItem[]
}

/**
 * The home rails, mutually exclusive: every product appears in at most one of them. Priority
 * Deals (dealSets.all) → Restock essentials (best sellers) → New arrivals → Moving fast
 * (trending / selling fast); in stock only; RAIL_MAX each; codes in `exclude` are skipped.
 * Every deal code is reserved for Deals — also the ones past RAIL_MAX — because the deals section
 * can show a whole set behind its chips, and a product must not show there and in a rail below.
 */
export function homeRails(items: ShopItem[], offers?: Offer[] | null, exclude?: ReadonlySet<string>): HomeRails {
  const used = new Set<string>(exclude ? [...exclude] : [])
  const live = inStock(items)
  const take = (pool: ShopItem[], reserveAll = false): ShopItem[] => {
    const out: ShopItem[] = []
    for (const i of pool) {
      if (used.has(i.item_code)) continue
      if (out.length < RAIL_MAX) out.push(i)
      else if (!reserveAll) break
    }
    for (const i of reserveAll ? pool : out) used.add(i.item_code)
    return out
  }
  const deals = take(dealSets(items, offers).all, true)
  const essentials = take(live.filter((i) => hasBadge(i, 'best_seller')))
  const fresh = take(live.filter((i) => hasBadge(i, 'new')))
  const moving = take(live.filter((i) => hasBadge(i, 'trending') || hasBadge(i, 'selling_fast')))
  return { deals, essentials, fresh, moving }
}

export interface BrandTile {
  brand: string
  count: number
  image: ShopItem | null
}

/** Shop by brand — only worth a section when ≥2 brands each carry ≥3 products; else []. */
export function brandTiles(items: ShopItem[]): BrandTile[] {
  const groups = new Map<string, { brand: string; list: ShopItem[] }>()
  for (const i of items) {
    const brand = (i.brand || '').trim()
    if (!brand) continue
    const key = brand.toUpperCase()
    const g = groups.get(key)
    if (g) g.list.push(i)
    else groups.set(key, { brand, list: [i] })
  }
  const tiles = [...groups.values()]
    .filter((g) => g.list.length >= 3)
    .map((g) => ({
      brand: g.brand,
      count: g.list.length,
      image: g.list.find((i) => hasPhoto(i) && i.stock_status !== 'out_of_stock' && hasBadge(i, 'best_seller')) || g.list.find((i) => hasPhoto(i) && i.stock_status !== 'out_of_stock') || g.list.find(hasPhoto) || null,
    }))
  if (tiles.length < 2) return []
  return tiles.sort((a, b) => b.count - a.count || a.brand.localeCompare(b.brand))
}

/**
 * The desktop hero composition from ONE slide list: a slider stage (≤3 slides) and a row of pastel
 * tiles (2–3) under it, FreshMart's three promo tiles. Tiles are data slides only, taken from the
 * end of the list (the lowest priority), so campaigns and Order again stay on the stage; pastel
 * canvases are preferred over the night one. With 5+ slides the row gets 3 tiles, with 3–4 it
 * gets 2; fewer than 2 possible tiles → no row, the stage keeps up to 3. A slide shows at most
 * once; the ones left out (a 4th campaign) stay unclaimed, so the aside Spotlight can carry them.
 */
export function heroSplit(slides: readonly Slide[]): { hero: Slide[]; tiles: Slide[] } {
  const n = slides.length
  const want = n >= 5 ? 3 : n >= 3 ? 2 : 0
  const tileable = (s: Slide) => s.kind === 'data' && s.id !== 'd:again'
  const picked = new Set<string>()
  // pastel first, then the night slide, each walked from the end of the list; slide 1 (the LCP
  // candidate catalog-prefetch.js preloads) always stays on the stage
  for (const pass of [(s: Slide) => s.canvas !== 'night', () => true]) {
    for (let i = n - 1; i >= 1 && picked.size < want; i--) {
      const s = slides[i]
      if (!picked.has(s.id) && tileable(s) && pass(s)) picked.add(s.id)
    }
  }
  if (picked.size < 2) return { hero: slides.slice(0, 3), tiles: [] }
  return { hero: slides.filter((s) => !picked.has(s.id)).slice(0, 3), tiles: slides.filter((s) => picked.has(s.id)) }
}

export function pickedUpAgain(items: ShopItem[], inCart: Set<string>): ShopItem[] {
  const byCode = new Map(items.map((i) => [i.item_code, i]))
  return recentlyViewed()
    .map((c) => byCode.get(c))
    .filter((i): i is ShopItem => Boolean(i) && !inCart.has(i!.item_code) && i!.stock_status !== 'out_of_stock')
    .slice(0, 8)
}

/** One tile image per category: its first best seller with a photo, else the first with a photo. */
export function categoryTiles(items: ShopItem[], categories: string[]) {
  return categories.map((c) => {
    const inCat = items.filter((i) => (i.category || 'OTHER') === c)
    const img = inCat.find((i) => hasBadge(i, 'best_seller') && hasPhoto(i) && i.stock_status !== 'out_of_stock') || inCat.find((i) => hasPhoto(i) && i.stock_status !== 'out_of_stock') || inCat.find(hasPhoto) || null
    return { category: c, count: inCat.length, newCount: inCat.filter((i) => hasBadge(i, 'new')).length, image: img }
  })
}

/** The offer strip: the live rule ending soonest. */
export function liveOffer(data: CatalogPayload | null): Offer | null {
  const offers = (data?.offers || []).filter((o) => !o.ends_at || new Date(o.ends_at).getTime() > Date.now())
  if (!offers.length) return null
  return offers.slice().sort((a, b) => (a.ends_at ? new Date(a.ends_at).getTime() : Infinity) - (b.ends_at ? new Date(b.ends_at).getTime() : Infinity))[0]
}

export interface RegularLine {
  item: ShopItem
  qty: number
}

/** Lines of the newest order, with what was actually confirmed. */
export function orderLines(order: OrderStatusPayload | null, byCode: Map<string, ShopItem>): RegularLine[] {
  const out: RegularLine[] = []
  for (const ln of order?.lines || []) {
    if ((ln.line_status || 'ok') === 'removed') continue
    const it = byCode.get(ln.item_code)
    if (it) out.push({ item: it, qty: Number(ln.qty_confirmed ?? ln.qty) || 1 })
  }
  return out
}

/** Items that appeared in at least two of the last orders — the merchant's regular stock. */
export function regularStock(orders: OrderStatusPayload[], byCode: Map<string, ShopItem>, exclude: Set<string>): RegularLine[] {
  const seen = new Map<string, { n: number; qty: number }>()
  for (const o of orders) {
    for (const ln of orderLines(o, byCode)) {
      const cur = seen.get(ln.item.item_code)
      if (cur) {
        cur.n++
        cur.qty = Math.max(cur.qty, ln.qty)
      } else seen.set(ln.item.item_code, { n: 1, qty: ln.qty })
    }
  }
  const out: RegularLine[] = []
  for (const [code, v] of seen) {
    if (v.n < 2 || exclude.has(code)) continue
    const item = byCode.get(code)
    if (item && item.stock_status !== 'out_of_stock') out.push({ item, qty: v.qty })
  }
  return out.slice(0, RAIL_MAX)
}
