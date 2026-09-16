/**
 * The marketplace's transport: the token-less `/public/market*` family (docs/SHOP.md
 * § Marketplace) plus the order-token endpoints it shares with the legacy shop. Same rules as
 * lib/shopApi: no auth header, no supabase client, every field optional on read.
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

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export function marketPath(ref?: string | null): string {
  return `/public/market${ref ? `?ref=${encodeURIComponent(ref)}` : ''}`
}

/** The catalog for the root URL or a /{slug} storefront; picks up the pre-JS prefetch when it matches. */
export function getMarket(ref?: string | null): Promise<CatalogPayload> {
  const path = marketPath(ref)
  const early = typeof window !== 'undefined' ? window.__yqCatalog : undefined
  if (early && early.url === `${API_BASE}${path}`) {
    early.res
      .finally(() => {
        if (window.__yqCatalog === early) window.__yqCatalog = undefined
      })
      .catch(() => {})
    return early.res.then(
      (text) => JSON.parse(text) as CatalogPayload,
      () => request<CatalogPayload>(path),
    )
  }
  return request<CatalogPayload>(path)
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
