/**
 * The marketplace's transport: the token-less `/public/market*` family (docs/SHOP.md
 * § Marketplace) plus the order-token endpoints it shares with the legacy shop. Same rules as
 * lib/shopApi: no auth header, no supabase client, every field optional on read.
 *
 * The catalog itself (R6) is read from the SAME ORIGIN first — /api/market, the edge Worker in
 * web/workers/market.js that keeps the last good copy of the API's /public/market in Cloudflare's
 * cache: 60 s fresh, stale while it revalidates, and served straight from the edge when the free
 * Render origin is cold (11–12 s) or down. When no Worker is in front of the host (local preview,
 * `vite preview`, the old vercel.app 308 host) the same-origin call answers with index.html, and
 * the app falls back to the API exactly as before. Nothing else moves: quote, order, events and
 * restock still go to the API directly.
 */
import {
  API_BASE,
  request,
  type CatalogPayload,
  type EventPing,
  type MarketOrderRequest,
  type MarketOrderResponse,
  type MyOrderSummary,
  type OrderStatusPayload,
  type Quote,
  type QuoteRequest,
  type RepCard,
} from '@/lib/shopApi'
import { noteCatalog } from './rum'

const JSON_HEADERS = { 'Content-Type': 'application/json' }
/** Same as lib/shopApi request(): a cold origin behind the edge can still take ~50 s. */
const EDGE_TIMEOUT_MS = 60000

/** The same-origin edge path for the catalog — MIRRORS public/catalog-prefetch.js (the prefetch's
 *  `url` must equal this for getMarket() to pick it up). */
export function marketPath(ref?: string | null): string {
  return `/api/market${ref ? `?ref=${encodeURIComponent(ref)}` : ''}`
}

/** The API's own path for the same answer: the fallback, and what the legacy callers still use. */
export function marketApiPath(ref?: string | null): string {
  return `/public/market${ref ? `?ref=${encodeURIComponent(ref)}` : ''}`
}

/** The catalog for the root URL or a /{slug} storefront; picks up the pre-JS prefetch when it matches. */
export function getMarket(ref?: string | null): Promise<CatalogPayload> {
  const path = marketPath(ref)
  const early = typeof window !== 'undefined' ? window.__yqCatalog : undefined
  if (early && early.url === path) {
    early.res
      .finally(() => {
        if (window.__yqCatalog === early) window.__yqCatalog = undefined
      })
      .catch(() => {})
    return early.res.then(
      (text) => {
        const data = JSON.parse(text) as CatalogPayload
        noteCatalog(early.src || 'pre')
        return data
      },
      () => fetchMarket(ref),
    )
  }
  return fetchMarket(ref)
}

/** Edge first, API second. A non-JSON same-origin answer is the SPA's index.html (no Worker in
 *  front), a non-2xx one is passed through from the origin (404 = closed, 5xx = down): both fall
 *  back to the API call, whose error then carries the real status for MarketContext. */
async function fetchMarket(ref?: string | null): Promise<CatalogPayload> {
  const edge = await fetchEdge(marketPath(ref))
  if (edge) {
    const data = JSON.parse(edge.text) as CatalogPayload
    noteCatalog(edge.src)
    return data
  }
  const data = await request<CatalogPayload>(marketApiPath(ref))
  noteCatalog('api')
  return data
}

async function fetchEdge(path: string): Promise<{ text: string; src: string } | null> {
  if (typeof window === 'undefined' || !window.location.origin.startsWith('http')) return null
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), EDGE_TIMEOUT_MS)
  try {
    const res = await fetch(path, { signal: ctrl.signal })
    if (!res.ok || !(res.headers.get('content-type') || '').includes('application/json')) return null
    const text = await res.text()
    return { text, src: `edge-${(res.headers.get('x-yq-cache') || 'miss').toLowerCase()}` }
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

export function getRep(slug: string): Promise<RepCard> {
  return request<RepCard>(`/public/rep/${encodeURIComponent(slug)}`)
}

export function postMarketQuote(body: QuoteRequest, signal?: AbortSignal): Promise<Quote> {
  return request<Quote>('/public/market/quote', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }, signal)
}

export function postMarketOrder(body: MarketOrderRequest): Promise<MarketOrderResponse> {
  return request<MarketOrderResponse>('/public/market/order', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  })
}

export function getOrder(token: string): Promise<OrderStatusPayload> {
  return request<OrderStatusPayload>(`/public/shop/order/${encodeURIComponent(token)}`)
}

export function cancelOrder(token: string, reason: string): Promise<{ ok: boolean; status: string; order: OrderStatusPayload }> {
  return request(`/public/shop/order/${encodeURIComponent(token)}/cancel`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ reason }),
  })
}

export function postMyOrders(tokens: string[]): Promise<MyOrderSummary[]> {
  if (!tokens.length) return Promise.resolve([])
  return request<{ orders?: MyOrderSummary[] | null }>('/public/shop/my-orders', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ tokens: tokens.slice(0, 20) }),
  }).then((r) => r.orders || [])
}

/** Fire-and-forget funnel ping (events v2). Never throws, never blocks the UI. */
export function pingMarketEvent(body: EventPing): void {
  try {
    void fetch(`${API_BASE}/public/market/event`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {})
  } catch {
    /* best effort only */
  }
}

/** "Tell me when back": a sold-out product the merchant wants; the rep sees the list. */
export function postRestock(body: { item_code: string; phone?: string | null; device_id?: string; referral_code?: string | null }): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/public/market/restock', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) })
}

/** A returning merchant on a new phone: shop, area and first name for a known number. */
export function recognizePhone(phone: string, deviceId: string): Promise<{ known: { shop?: string | null; area?: string | null; first_name?: string | null; orders_count?: number } | null }> {
  return request('/public/market/recognize', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ phone, device_id: deviceId }) })
}

/* ───────────────────────── "Coming soon" (app/upcoming.py) ─────────────────────────
   The announced range before it lands: a separate, lazily fetched payload, never part of the
   catalog. Whitelisted server-side — no price, cost or quantity field exists in this shape. */

export interface UpcomingVariant {
  label: string
  label_ar?: string | null
}

export interface UpcomingItem {
  id: number
  brand: string
  model_code: string
  category?: string | null
  name_en: string
  name_ar?: string | null
  spec_en?: string | null
  spec_ar?: string | null
  variants: UpcomingVariant[]
  photo_url?: string | null
  /** WebP size set ({"160": url, "320": url, "512": url}), same shape as ShopItem.thumb_urls */
  photo_thumb_urls?: Record<string, string> | null
  box_url?: string | null
  /** the box photo's own WebP size set — the card's Box toggle never has to load the full JPEG */
  box_thumb_urls?: Record<string, string> | null
  /** "Arriving October" — derived from the owner's month; "Arriving soon" once the month has passed */
  expected_label_en?: string | null
  expected_label_ar?: string | null
  sort_order?: number | null
}

export interface UpcomingPayload {
  enabled: boolean
  brand: string
  /** how many cards are live — the headline's number comes from here, never from copy */
  count: number
  expected_label_en: string
  expected_label_ar: string
  items: UpcomingItem[]
}

/** Published upcoming cards (whitelisted), cached 60 s at the API and CDN. */
export function getUpcoming(): Promise<UpcomingPayload> {
  return request<UpcomingPayload>('/public/market/upcoming')
}

/** "Notify me when it lands": the restock flow for a card that has no stock yet. The phone is required (the server answers 400 without a usable one); the quantity is optional and never a commitment. */
export function postUpcomingInterest(body: { upcoming_id: number; phone: string; qty_interest?: number | null; device_id?: string; ref?: string | null }): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/public/market/upcoming/interest', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) })
}
