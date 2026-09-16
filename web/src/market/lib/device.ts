/**
 * What this phone remembers (Tier 0 of the identity design, plan §O/§S).
 *
 * Everything here is per-device convenience, never private data: an anonymous device id, the
 * salesman whose link the merchant arrived with, the order tokens this device created (each token
 * is a capability for exactly that order), the last quantity ordered per SKU, the checkout details
 * the merchant asked us to keep, and recent searches. Nothing in localStorage can unlock another
 * merchant's history — that needs a verified session (Phase 2).
 *
 * Every read is wrapped: private mode, cleared site data and quota errors must never break the
 * page, they only make it forget.
 */

const KEYS = {
  device: 'yq-device',
  ref: 'yq-ref',
  orders: 'yq-orders',
  qty: 'yq-qty',
  customer: 'yq-shop-customer', // shared with the legacy /c/{token} drawer on purpose
  save: 'yq-save-details',
  searches: 'yq-searches',
  viewed: 'yq-viewed',
  note: 'yq-cart-note',
} as const

const REF_DAYS = 90
const MAX_ORDERS = 20
const MAX_QTY_MEMORY = 250
const MAX_SEARCHES = 8
const MAX_VIEWED = 12

// Parsed once per key, kept in memory: sixty product cards asking for their remembered
// quantity must not JSON.parse the same blob sixty times on the first paint.
const cache = new Map<string, unknown>()

function read<T>(key: string, fallback: T): T {
  if (cache.has(key)) return cache.get(key) as T
  let v: T
  try {
    const raw = localStorage.getItem(key)
    v = raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    v = fallback
  }
  cache.set(key, v)
  return v
}

function write(key: string, value: unknown): void {
  if (value === null || value === undefined) cache.delete(key)
  else cache.set(key, value)
  try {
    if (value === null || value === undefined) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage unavailable — forget, never fail */
  }
}

if (typeof window !== 'undefined') {
  // another tab wrote: drop the stale copy
  window.addEventListener('storage', (e) => {
    if (e.key) cache.delete(e.key)
  })
}

function uuid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `d-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

/* ───────────────────────── device ───────────────────────── */

export function deviceId(): string {
  const found = read<string | null>(KEYS.device, null)
  if (found && typeof found === 'string') return found
  const made = uuid()
  write(KEYS.device, made)
  return made
}

/* ───────────────────────── attribution ───────────────────────── */

export interface RememberedRef {
  slug: string
  ts: number
}

const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,31}$/

export function isSlugShaped(s: string | null | undefined): s is string {
  return typeof s === 'string' && SLUG_RE.test(s.toLowerCase())
}

export function rememberRef(slug: string): void {
  const s = slug.trim().toLowerCase()
  if (!isSlugShaped(s)) return
  write(KEYS.ref, { slug: s, ts: Date.now() } satisfies RememberedRef)
}

export function currentRef(): string | null {
  const r = read<RememberedRef | null>(KEYS.ref, null)
  if (!r || !isSlugShaped(r.slug)) return null
  if (Date.now() - Number(r.ts || 0) > REF_DAYS * 86400000) {
    write(KEYS.ref, null)
    return null
  }
  return r.slug
}

export function forgetRef(): void {
  write(KEYS.ref, null)
}

/* ───────────────────────── my orders (tokens) ───────────────────────── */

export interface RememberedOrder {
  token: string
  order_no: string
  ts: number
  total?: number | null
}

export function rememberOrder(o: RememberedOrder): void {
  const list = read<RememberedOrder[]>(KEYS.orders, []).filter((x) => x && x.token !== o.token)
  write(KEYS.orders, [o, ...list].slice(0, MAX_ORDERS))
}

export function rememberedOrders(): RememberedOrder[] {
  return read<RememberedOrder[]>(KEYS.orders, []).filter((x) => x && typeof x.token === 'string' && x.token.length >= 16)
}

export function forgetOrder(token: string): void {
  write(KEYS.orders, rememberedOrders().filter((x) => x.token !== token))
}

/* ───────────────────────── quantity memory ───────────────────────── */

type QtyMemory = Record<string, { last: number; at: number }>

export function rememberQty(code: string, qty: number): void {
  if (!code || !(qty > 0)) return
  const m = { ...read<QtyMemory>(KEYS.qty, {}) }
  m[code] = { last: qty, at: Date.now() }
  const entries = Object.entries(m)
  if (entries.length > MAX_QTY_MEMORY) {
    entries.sort((a, b) => b[1].at - a[1].at)
    write(KEYS.qty, Object.fromEntries(entries.slice(0, MAX_QTY_MEMORY)))
  } else {
    write(KEYS.qty, m)
  }
}

export function lastQty(code: string): number | null {
  const m = read<QtyMemory>(KEYS.qty, {})
  const v = m[code]?.last
  return typeof v === 'number' && v > 0 ? v : null
}

/* ───────────────────────── checkout details ───────────────────────── */

export interface CustomerDraft {
  name: string
  phone: string
  shop: string
  area: string
  email: string
}

export const EMPTY_CUSTOMER: CustomerDraft = { name: '', phone: '', shop: '', area: '', email: '' }

export function readCustomer(): CustomerDraft {
  const p = read<Partial<CustomerDraft> | null>(KEYS.customer, null)
  if (!p) return EMPTY_CUSTOMER
  return {
    name: String(p.name || ''),
    phone: String(p.phone || ''),
    shop: String(p.shop || ''),
    area: String(p.area || ''),
    email: String(p.email || ''),
  }
}

export function writeCustomer(c: CustomerDraft | null): void {
  write(KEYS.customer, c)
}

export function saveDetailsEnabled(): boolean {
  return read<boolean>(KEYS.save, true) !== false
}

export function setSaveDetails(on: boolean): void {
  write(KEYS.save, on)
  if (!on) writeCustomer(null)
}

/** The merchant is "recognized" when this device has placed an order or kept its details. */
export function isRecognized(): boolean {
  return rememberedOrders().length > 0 || Boolean(readCustomer().phone)
}

/* ───────────────────────── searches & views ───────────────────────── */

export function rememberSearch(q: string): void {
  const s = q.trim()
  if (s.length < 2) return
  const list = read<string[]>(KEYS.searches, []).filter((x) => x.toLowerCase() !== s.toLowerCase())
  write(KEYS.searches, [s, ...list].slice(0, MAX_SEARCHES))
}

export function recentSearches(): string[] {
  return read<string[]>(KEYS.searches, []).filter((x) => typeof x === 'string')
}

export function rememberViewed(code: string): void {
  if (!code) return
  const list = read<string[]>(KEYS.viewed, []).filter((x) => x !== code)
  write(KEYS.viewed, [code, ...list].slice(0, MAX_VIEWED))
}

export function recentlyViewed(): string[] {
  return read<string[]>(KEYS.viewed, []).filter((x) => typeof x === 'string')
}

/* ───────────────────────── cart note ───────────────────────── */

export function readNote(): string {
  return read<string>(KEYS.note, '')
}

export function writeNote(note: string): void {
  write(KEYS.note, note || null)
}

/** A fresh idempotency key for one checkout attempt (kept in sessionStorage until it succeeds). */
export function clientOrderId(): string {
  try {
    const found = sessionStorage.getItem('yq-client-order-id')
    if (found) return found
    const made = uuid()
    sessionStorage.setItem('yq-client-order-id', made)
    return made
  } catch {
    return uuid()
  }
}

export function resetClientOrderId(): void {
  try {
    sessionStorage.removeItem('yq-client-order-id')
  } catch {
    /* ignore */
  }
}
