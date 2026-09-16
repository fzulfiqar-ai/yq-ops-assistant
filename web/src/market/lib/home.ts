import type { CatalogPayload, Offer, OrderStatusPayload, ShopItem } from '@/lib/shopApi'
import { deviceId, recentlyViewed } from './device'
import { hasBadge, hashStr } from './format'

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

export function justArrived(items: ShopItem[]): ShopItem[] {
  return inStock(items).filter((i) => hasBadge(i, 'new')).slice(0, RAIL_MAX)
}

export function onOffer(items: ShopItem[]): ShopItem[] {
  return items.filter((i) => hasBadge(i, 'on_offer') || i.compare_at_bhd != null).slice(0, RAIL_MAX)
}

export function clearance(items: ShopItem[]): ShopItem[] {
  return inStock(items).filter((i) => hasBadge(i, 'clearance')).slice(0, RAIL_MAX)
}

export function priceDrops(items: ShopItem[]): ShopItem[] {
  return inStock(items).filter((i) => hasBadge(i, 'price_drop') || i.was_bhd != null).slice(0, RAIL_MAX)
}

export function pickedUpAgain(items: ShopItem[], inCart: Set<string>): ShopItem[] {
  const byCode = new Map(items.map((i) => [i.item_code, i]))
  return recentlyViewed()
    .map((c) => byCode.get(c))
    .filter((i): i is ShopItem => Boolean(i) && !inCart.has(i!.item_code) && i!.stock_status !== 'out_of_stock')
    .slice(0, 8)
}

/**
 * The hero product: the first best seller in stock with a photo — deterministic, because the
 * prefetch script (public/catalog-prefetch.js) preloads exactly this photo as the LCP image.
 * The shelf order already leads with the biggest category.
 */
export function heroProduct(items: ShopItem[], categories: string[]): { item: ShopItem; rank: number; category: string } | null {
  const withPhoto = inStock(items).filter((i) => i.thumb_url || i.thumb_urls?.['320'] || i.product_image_url)
  const best = withPhoto.filter((i) => hasBadge(i, 'best_seller'))
  const pool = best.length ? best : withPhoto
  if (!pool.length) return null
  const catOrder = new Map(categories.map((c, i) => [c, i]))
  const pick = pool.slice().sort((a, b) => (catOrder.get(a.category || '') ?? 99) - (catOrder.get(b.category || '') ?? 99))[0]
  return { item: pick, rank: best.length ? 1 : 0, category: pick.category || '' }
}

/** A stable per-device seed for anything that should vary between merchants (not the hero). */
export function deviceSeed(): number {
  return hashStr(deviceId())
}

/** One tile image per category: its first best seller with a photo, else the first with a photo. */
export function categoryTiles(items: ShopItem[], categories: string[]) {
  return categories.map((c) => {
    const inCat = items.filter((i) => (i.category || 'OTHER') === c)
    const photo = (i: ShopItem) => Boolean(i.thumb_url || i.product_image_url)
    const img = inCat.find((i) => hasBadge(i, 'best_seller') && photo(i) && i.stock_status !== 'out_of_stock') || inCat.find(photo) || null
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
