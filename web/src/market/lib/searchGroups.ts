import type { ShopItem } from '@/lib/shopApi'
import { itemText } from './facets'
import { codeKey, hasBadge } from './format'
import type { SearchIndex } from './search'

/** Grouped search results: Codes (exact/prefix, ≤3) · Products (index hits, ≤20) · Categories (≤2). */
export interface Grouped {
  codes: ShopItem[]
  products: ShopItem[]
  categories: string[]
  exact: ShopItem | null
}

export const EMPTY_GROUPED: Grouped = { codes: [], products: [], categories: [], exact: null }

export function groupResults(q: string, items: ShopItem[], index: SearchIndex | null, search: (ix: SearchIndex, items: ShopItem[], q: string, limit?: number) => ShopItem[]): Grouped {
  const query = q.trim()
  if (!query) return EMPTY_GROUPED
  const key = codeKey(query)
  const exact = items.find((i) => codeKey(i.item_code) === key) || null
  const codes = items.filter((i) => codeKey(i.item_code).startsWith(key)).slice(0, 3)
  const hits = index ? search(index, items, query, 20) : []
  const codeSet = new Set(codes.map((c) => c.item_code))
  const products = hits.filter((h) => !codeSet.has(h.item_code))
  const ql = query.toLowerCase()
  const cats = Array.from(new Set(items.map((i) => i.category || 'OTHER'))).filter((c) => c.toLowerCase().includes(ql) || ql.split(/\s+/).some((w) => w.length > 2 && c.toLowerCase().includes(w)))
  return { codes, products, categories: cats.slice(0, 2), exact }
}

/** "Popular right now" — best sellers' names + the vocabulary merchants actually type. */
export function popularTerms(items: ShopItem[]): string[] {
  const best = items.filter((i) => hasBadge(i, 'best_seller') || hasBadge(i, 'trending'))
  const text = best.map(itemText).join(' ').toLowerCase()
  const vocab: [string, RegExp][] = [
    ['Type-C cable', /type[\s-]?c|usb[\s-]?c/],
    ['20W charger', /20\s?w/],
    ['TWS', /tws|earbud/],
    ['Car charger', /car charger/],
    ['Power bank', /power ?bank|mah/],
    ['Lightning', /lightning|iphone/],
    ['100W', /100\s?w/],
    ['Wireless', /wireless|magsafe/],
  ]
  const terms = vocab.filter(([, r]) => r.test(text)).map(([t]) => t)
  const names = best
    .slice(0, 4)
    .map((i) => (i.display_name || i.item_code).split(/\s[(·]/)[0].trim())
    .filter((n) => n.length <= 28)
  return Array.from(new Set([...terms, ...names])).slice(0, 8)
}
