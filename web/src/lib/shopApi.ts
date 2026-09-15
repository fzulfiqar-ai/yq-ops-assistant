import { API_BASE, ApiError, apiGet, apiPost } from '@/lib/api'

/**
 * Typed client for the shop endpoints (docs/SHOP.md).
 *
 * Two audiences, one payload shape:
 *
 *  • PUBLIC  — `/public/…`, no auth header, no supabase session. These links are
 *    opened by customers who have never logged in.
 *  • SALESMAN — `/shop/…`, bearer token via lib/api. Same catalog, plus exact
 *    stock numbers, who the logged-in salesman is, and his recent customers.
 *
 * Every field is optional on read: the API is documented as backward compatible,
 * so an older deployment can answer with the old catalog payload and this UI
 * must still render.
 */

/* ───────────────────────── catalog payload ───────────────────────── */

export type StockStatus = 'in_stock' | 'low_stock' | 'out_of_stock'
export type BadgeKind = 'best_seller' | 'trending' | 'new' | 'on_offer' | 'price_drop'
export type ShopEventKind = 'view' | 'item' | 'add' | 'checkout'

export interface Tier {
  min_qty: number
  unit_price_bhd: number
  label?: string | null
}

export interface ShopItem {
  item_code: string
  display_name?: string | null
  spec?: string | null
  category?: string | null
  brand?: string | null
  /** trade (B2B) price — the price the customer actually pays */
  price_bhd?: number | null
  /** retail (B2C) price, kept for compatibility with the old payload */
  b2c_bhd?: number | null
  compare_at_bhd?: number | null
  save_pct?: number | null
  product_image_url?: string | null
  package_image_url?: string | null
  thumb_url?: string | null
  stock_status?: StockStatus | null
  /**
   * Exact units on hand. Salesman mode only — the public payload never carries a
   * number (docs/SHOP.md), so `undefined`/`null` means "show the status wording".
   */
  stock_qty?: number | null
  moq?: number | null
  pack_size?: number | null
  tiers?: Tier[] | null
  badges?: BadgeKind[] | null
  social_proof?: string | null
}

export interface Salesman {
  id: number
  name: string
  referral_code?: string | null
}

export interface Offer {
  id: number
  name: string
  kind?: string | null
  summary?: string | null
  ends_at?: string | null
  min_value_bhd?: number | null
  pct_off?: number | null
  amount_off_bhd?: number | null
  coupon_code?: string | null
  scope_codes?: string[] | null
}

export interface ShopSettings {
  currency?: string | null
  min_order_bhd?: number | null
  free_delivery_threshold_bhd?: number | null
  allow_backorder?: boolean | null
  show_retail_compare?: boolean | null
}

export interface ShopRef {
  referral_code?: string | null
  salesman_id?: number | null
  salesman_name?: string | null
}

/** Optional "frequently bought together" hints — absent on older payloads. */
export interface ItemPair {
  item_code: string
  with: string[]
}

/** Who is placing the order, in salesman mode. Absent on the public payload. */
export interface StaffMe {
  salesman_id?: number | null
  salesman_name?: string | null
  is_admin?: boolean | null
}

export interface CatalogPayload {
  company?: string | null
  brand?: string | null
  prices_updated?: string | null
  stock_as_of?: string | null
  whatsapp?: string | null
  categories?: string[] | null
  items?: ShopItem[] | null
  salesmen?: Salesman[] | null
  offers?: Offer[] | null
  settings?: ShopSettings | null
  ref?: ShopRef | null
  pairs?: ItemPair[] | null
  /** "salesman" on GET /shop/catalog; absent (or "public") on a share link. */
  mode?: 'public' | 'salesman' | null
  me?: StaffMe | null
}

/* ───────────────────────── quote / order ───────────────────────── */

export interface CartLine {
  item_code: string
  qty: number
}

export interface AppliedRule {
  rule_id?: number | null
  name?: string | null
  kind?: string | null
}

export interface QuoteLine {
  item_code: string
  display_name?: string | null
  qty: number
  moq?: number | null
  list_price_bhd?: number | null
  unit_price_bhd?: number | null
  discount_bhd?: number | null
  line_total_bhd?: number | null
  stock_status?: StockStatus | null
  backorder?: boolean | null
  applied?: AppliedRule[] | null
  warning?: string | null
}

export interface QuoteDiscount {
  rule_id?: number | null
  name?: string | null
  kind?: string | null
  amount_bhd?: number | null
}

export interface QuoteCoupon {
  code?: string | null
  valid?: boolean | null
  message?: string | null
}

export interface QuoteProgress {
  kind?: string | null
  threshold_bhd?: number | null
  remaining_bhd?: number | null
  unlocked?: boolean | null
  label?: string | null
}

export interface Quote {
  ok?: boolean
  lines?: QuoteLine[] | null
  subtotal_bhd?: number | null
  discount_bhd?: number | null
  delivery_bhd?: number | null
  total_bhd?: number | null
  units?: number | null
  items?: number | null
  discounts?: QuoteDiscount[] | null
  coupon?: QuoteCoupon | null
  progress?: QuoteProgress | null
  warnings?: string[] | null
  can_submit?: boolean | null
  min_order_bhd?: number | null
}

export interface QuoteRequest {
  lines: CartLine[]
  coupon_code?: string
  referral_code?: string
}

export interface OrderCustomer {
  name: string
  phone: string
  shop?: string
  area?: string
  email?: string
}

export interface OrderRequest {
  lines: CartLine[]
  coupon_code?: string
  referral_code?: string
  salesman_id?: number | null
  customer: OrderCustomer
  note?: string
  src?: string
  session_id?: string
  /** honeypot — always submitted empty */
  website?: string
}

export interface OrderResponse {
  ok?: boolean
  order_no: string
  /** portal row id — salesman mode only */
  order_id?: number | null
  token?: string | null
  status_url?: string | null
  salesman?: { name?: string | null; phone?: string | null } | null
  /**
   * Public mode: the CUSTOMER taps this to send the order to his salesman.
   * Salesman mode: the SALESMAN taps it to send a confirmation to the customer.
   */
  whatsapp_url?: string | null
  /** mailto: link, pre-filled with the salesman's address and the whole order (no provider needed) */
  email_url?: string | null
  totals?: Quote | null
  has_backorder?: boolean | null
  /** "salesman" when the order was placed from inside the portal */
  source?: string | null
}

/* ───────────────────────── order status ───────────────────────── */

export interface OrderStatusLine {
  item_code: string
  display_name?: string | null
  qty: number
  unit_price_bhd?: number | null
  line_total_bhd?: number | null
  stock_status?: StockStatus | null
  backorder?: boolean | null
}

export interface OrderTimelineEntry {
  ts?: string | null
  event?: string | null
  note?: string | null
}

export type OrderState = 'new' | 'confirmed' | 'packed' | 'delivered' | 'cancelled'

export interface OrderStatusPayload {
  order_no: string
  status?: OrderState | string | null
  created_at?: string | null
  updated_at?: string | null
  salesman?: { name?: string | null; whatsapp_url?: string | null; email_url?: string | null } | null
  customer?: { name?: string | null; shop?: string | null; area?: string | null } | null
  lines?: OrderStatusLine[] | null
  subtotal_bhd?: number | null
  discount_bhd?: number | null
  delivery_bhd?: number | null
  total_bhd?: number | null
  has_backorder?: boolean | null
  timeline?: OrderTimelineEntry[] | null
}

export interface EventPing {
  event: ShopEventKind
  item_code?: string
  referral_code?: string
  src?: string
  session_id?: string
}

/* ───────────────────────── transport ───────────────────────── */

/** Carries the server's `detail` verbatim so the form can show exactly what it said. */
export class ShopApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(detail || `Request failed (${status})`)
    this.name = 'ShopApiError'
    this.status = status
    this.detail = detail
  }
}

function readDetail(body: string): string {
  try {
    const parsed = JSON.parse(body)
    const d = parsed?.detail
    if (typeof d === 'string') return d
    if (Array.isArray(d)) {
      return d
        .map((e) => {
          if (typeof e === 'string') return e
          const field = Array.isArray(e?.loc) ? e.loc[e.loc.length - 1] : null
          return [field, e?.msg].filter(Boolean).join(': ')
        })
        .filter(Boolean)
        .join(' · ')
    }
    if (typeof parsed?.message === 'string') return parsed.message
  } catch {
    /* not JSON — fall through to the generic message */
  }
  return ''
}

/** 60s, not 20s: the API sleeps on the free tier and takes ~50s to wake. */
const TIMEOUT_MS = 60000

async function request<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  const onAbort = () => ctrl.abort()
  signal?.addEventListener('abort', onAbort)
  try {
    const res = await fetch(`${API_BASE}${path}`, { ...init, signal: ctrl.signal })
    const text = await res.text().catch(() => '')
    if (!res.ok) throw new ShopApiError(res.status, readDetail(text))
    return (text ? JSON.parse(text) : {}) as T
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onAbort)
  }
}

const seg = (s: string) => encodeURIComponent(s)

export function getCatalog(token: string, ref?: string | null): Promise<CatalogPayload> {
  const qs = ref ? `?ref=${encodeURIComponent(ref)}` : ''
  return request<CatalogPayload>(`/public/catalog/${seg(token)}${qs}`)
}

export function postQuote(token: string, body: QuoteRequest, signal?: AbortSignal): Promise<Quote> {
  return request<Quote>(
    `/public/shop/${seg(token)}/quote`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
    signal,
  )
}

export function postOrder(token: string, body: OrderRequest): Promise<OrderResponse> {
  return request<OrderResponse>(`/public/shop/${seg(token)}/order`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function getOrderStatus(orderToken: string): Promise<OrderStatusPayload> {
  return request<OrderStatusPayload>(`/public/shop/order/${seg(orderToken)}`)
}

/** Attribution ping — ?src=outreach-{id} ties this visit to a touch. Never throws. */
export function pingVisit(token: string, src: string): void {
  try {
    void fetch(`${API_BASE}/public/catalog/${seg(token)}/visit?src=${encodeURIComponent(src)}`, {
      method: 'POST',
      keepalive: true,
    }).catch(() => {})
  } catch {
    /* best effort only */
  }
}

/** Funnel ping (view/item/add/checkout). Fire-and-forget — never blocks the UI. */
export function pingEvent(token: string, body: EventPing): void {
  try {
    void fetch(`${API_BASE}/public/shop/${seg(token)}/event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {})
  } catch {
    /* best effort only */
  }
}

/* ───────────────────── salesman mode (bearer via lib/api) ─────────────────────
   Same catalog, same quote shape — but the caller is a logged-in salesman, so the
   payload carries exact stock numbers, who he is, and the customers he has served.
   lib/api throws ApiError with the raw body; re-shape it to ShopApiError so every
   form in this folder keeps showing the server's own `detail` line. */

/** A shop the salesman has already sold to — the quick-pick above the form. */
export interface StaffCustomer {
  name: string
  phone?: string | null
  shop?: string | null
  area?: string | null
  email?: string | null
  orders?: number | null
  last_order_at?: string | null
}

export interface StaffOrderRequest {
  lines: CartLine[]
  coupon_code?: string
  /** admin only, and only when `me.salesman_id` is null */
  salesman_id?: number | null
  customer: OrderCustomer
  note?: string
}

async function staff<T>(run: () => Promise<T>): Promise<T> {
  try {
    return await run()
  } catch (e: unknown) {
    if (e instanceof ApiError) throw new ShopApiError(e.status, readDetail(e.body))
    throw e
  }
}

export function getStaffCatalog(): Promise<CatalogPayload> {
  return staff(() => apiGet<CatalogPayload>('/shop/catalog'))
}

/**
 * `signal` is accepted for symmetry with postQuote and deliberately ignored: the
 * bearer helper has its own timeout, and useQuote already drops any answer that
 * lands after its controller was aborted, so a stale reply can never overwrite a
 * newer one.
 */
export function postStaffQuote(body: QuoteRequest, _signal?: AbortSignal): Promise<Quote> {
  void _signal
  return staff(() => apiPost<Quote>('/shop/quote', { lines: body.lines, coupon_code: body.coupon_code || '' }))
}

export function postStaffOrder(body: StaffOrderRequest): Promise<OrderResponse> {
  return staff(() => apiPost<OrderResponse>('/shop/order', body))
}

export function getMyCustomers(): Promise<StaffCustomer[]> {
  return staff(async () => (await apiGet<{ customers?: StaffCustomer[] | null }>('/shop/customers')).customers || [])
}
