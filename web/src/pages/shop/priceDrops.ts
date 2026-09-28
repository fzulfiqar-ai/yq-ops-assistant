import type { ShopItem } from '@/lib/shopApi'
import { priceAnchor } from '@/market/lib/format'
import { bhd, money } from './shared'

/**
 * Price drops in the rep's app (R7e) — the marketplace's own "Was → Now" rule, nothing looser:
 * a real cut in the price book (`was_bhd`, which the API sends only while today's price is still
 * the cut price) above today's trade price, exactly when market/lib/format priceAnchor() returns
 * it. A `price_drop` badge without `was_bhd` never prints an old price, and a retail price is
 * never an anchor. Pure (no React), so web/scripts/rep_ui_test.mjs runs it in plain node.
 */

export interface PriceDrop {
  was: number
  now: number
  pct: number
}

export interface DropRow {
  item: ShopItem
  drop: PriceDrop
}

/** The genuine drop on this line, or null. */
export function priceDropOf(item?: ShopItem | null): PriceDrop | null {
  if (!item) return null
  const a = priceAnchor(item)
  if (!a || a.kind !== 'was') return null
  return { was: a.was, now: Number(item.price_bhd), pct: a.pct }
}

/** Every line with a genuine drop, the deepest cut first, then by code (a stable order on screen). */
export function dropsOf(items: readonly ShopItem[]): DropRow[] {
  const out: DropRow[] = []
  for (const item of items) {
    const drop = priceDropOf(item)
    if (drop) out.push({ item, drop })
  }
  const code = (r: DropRow) => r.item.item_code
  return out.sort((a, b) => b.drop.pct - a.drop.pct || (code(a) < code(b) ? -1 : code(a) > code(b) ? 1 : 0))
}

/** "Was 1.500 · ↓20%" — the marketplace's wasPill wording; the old price is never struck through. */
export function wasText(was: number, pct: number): string {
  return `Was ${money(was)} · ↓${pct}%`
}

/**
 * The product's own page on the rep's storefront: `{origin}/p/{CODE}?ref={slug}` from his link
 * (`https://…/{slug}`), so the shop lands on the product and the order is his. A legacy token link
 * (`/c/{token}?ref=…`, before the marketplace) keeps its path and opens the product with `?item=`.
 * No link (a login not linked to a salesman), or one that is not a URL: '' — nothing to share.
 */
export function productShareUrl(link: string | null | undefined, code: string): string {
  if (!link || !code) return ''
  let url: URL
  try {
    url = new URL(link)
  } catch {
    return ''
  }
  const path = url.pathname.replace(/\/+$/, '')
  if (path.startsWith('/c/')) {
    url.searchParams.set('item', code)
    return url.toString()
  }
  const slug = url.searchParams.get('ref') || path.split('/').pop() || ''
  return `${url.origin}/p/${encodeURIComponent(code)}${slug ? `?ref=${encodeURIComponent(slug)}` : ''}`
}

/**
 * The words that go with a shared product: "Name — now BHD 1.200 (was BHD 1.500, ↓20%)" on a genuine
 * drop, else "Name — BHD 1.200" (the sheet's old text). `url`, when given, goes on its own line.
 */
export function dropShareText(item: ShopItem, url?: string): string {
  const name = item.display_name || item.item_code
  const d = priceDropOf(item)
  const line = d
    ? `${name} — now ${bhd(d.now)} (was ${bhd(d.was)}, ↓${d.pct}%)`
    : `${name}${item.price_bhd != null ? ` — ${bhd(item.price_bhd)}` : ''}`
  return url ? `${line}\n${url}` : line
}
