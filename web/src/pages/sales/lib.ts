import { useEffect, useState, useSyncExternalStore } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { apiGet, apiPost } from '@/lib/api'
import { getSessionSafe } from '@/lib/supabase'
import type { StaffCustomer } from '@/lib/shopApi'

/**
 * Shared pieces of the salesman app (web/src/pages/sales/*, components/SalesmanShell.tsx):
 * the rep's own card (`/shop/me`), his book of shops (`/shop/customers`), the shop he is
 * currently ordering for (session-scoped, so the catalog and the checkout agree), bearer-
 * protected images (the QR), and small formatting helpers.
 */

/* ───────────────────────── /shop/me ───────────────────────── */

/** Tiered kickback standing for the month (app/shop.py tier_progress). */
export interface TierProgress {
  team: 'normal' | 'mobile_accessories' | string
  month: string | null            // YYYY-MM
  data_through: string | null     // last loaded sale date
  days_left: number | null        // calendar days left in the month (Bahrain date)
  data_age_days?: number | null   // how old the sales data is
  basis?: 'net_ex_vat' | string   // owner, 24-Sep-2026: ex-VAT sales
  is_estimate?: boolean           // always true until a statement is approved
  returns_deducted?: boolean      // false until the Focus Sales Return register is loaded
  mtd_bhd: number
  tiers: { n: number; bhd: number; pct: number }[]
  tier_reached: number            // 0 = below Tier 1
  kickback_pct: number            // fraction
  kickback_bhd: number
  next_tier: { n: number; bhd: number; gap_bhd: number; pct: number } | null
  progress_pct: number            // vs the top tier
}

/** "2026-09-24" -> "24 Sep" */
export function dayLabel(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

/** "2026-09" -> "September" */
export function monthName(ym?: string | null): string {
  if (!ym) return ''
  const [y, m] = ym.split('-').map(Number)
  return new Date(y, (m || 1) - 1, 1).toLocaleDateString('en-GB', { month: 'long' })
}

/** A frozen kickback statement as the rep sees it (app/statements.py rep_summary). Amounts are
 *  3-dp strings — never floats — because they are money on record. */
export interface StatementBrief {
  id: number
  period: string                  // YYYY-MM
  status: 'draft' | 'approved' | 'paid' | 'superseded' | 'snapshot' | string
  basis?: string | null
  data_through?: string | null
  sales_bhd?: string | null
  returns_bhd?: string | null     // null = returns not valued yet (no Sales Return register)
  tier_reached: number
  rate?: number | null
  kickback_bhd: string
  approved_at?: string | null
  paid_at?: string | null
  created_at?: string | null
  /** a draft of a month that has not ended yet (a documented moment, not yet approvable) */
  in_progress?: boolean
}

export interface ShopMe {
  salesman: { id: number; name: string; referral_code?: string | null; phone?: string | null; whatsapp?: string | null; title?: string | null; photo_url?: string | null } | null
  link?: string | null
  qr_url?: string | null
  kpis?: { orders_7d?: number; orders_30d?: number; value_30d_bhd?: number; customers_30d?: number } | null
  focus?: { revenue_90d_bhd?: number | null; basis?: string; target?: TierProgress | null; error?: string } | null
  hint?: string | null
  /** R3a: the money that is final (latest approved / paid statement) and the month being closed */
  last_closed?: StatementBrief | null
  draft?: StatementBrief | null
}

export function useShopMe(enabled = true) {
  return useQuery({ queryKey: ['shop-me'], queryFn: () => apiGet<ShopMe>('/shop/me'), staleTime: 60_000, enabled })
}

/* ───────────────────────── R3a: follow-ups, open baskets, my link this week ───────────────────────── */

export interface FollowupSku {
  item_code: string
  display_name: string
  median_qty: number
  times_bought: number
  cadence_days?: number | null
  days_since: number
}

/** One shop in the rep's Focus book (app/followups.py rank_shops). `why` is the only copy shown
 *  under the name and never claims a date. */
export interface FollowupShop {
  shop: string
  rep: string
  days_since: number
  gap_days: number
  gap_source: 'own' | 'default' | string
  overdue_ratio: number
  monthly_value_bhd: string       // 3-dp string, ex-VAT
  invoices_180d: number
  visits: number
  rep_visits: number
  last_date?: string | null
  first_date?: string | null
  top_skus: FollowupSku[]
  phone?: string | null
  why: string
  holdout?: boolean               // admin view only — the rep never receives a held-back shop
  rank?: number
  list?: 'due' | 'lapsed' | string
  wa_text?: string
}

export interface Followups {
  rep?: string | null
  scope?: 'rep' | 'admin' | string
  due: FollowupShop[]
  lapsed: FollowupShop[]
  counts?: { shops?: number; due?: number; lapsed?: number; fresh?: number; holdout?: number } | null
  data_through?: string | null
  hint?: string | null
  rules?: { due_ratio?: number; lapsed_min_days?: number; lapsed_gap_mult?: number; default_gap_days?: number; holdout_share?: number } | null
}

export function useFollowups(enabled = true) {
  return useQuery({ queryKey: ['shop-followups'], queryFn: () => apiGet<Followups>('/shop/me/followups'), staleTime: 5 * 60_000, enabled, retry: 1 })
}

export interface BasketLine { item_code: string; display_name: string; qty: number; line_bhd?: string | null }
export interface Basket {
  basket: string                  // opaque 6-char label, not a device id
  items: BasketLine[]
  items_count: number
  units: number
  value_bhd: string
  last_activity?: string | null
  first_activity?: string | null
}
export interface Baskets { days: number; since?: string; baskets: Basket[]; count: number; value_bhd?: string; hint?: string | null }

export function useBaskets(enabled = true) {
  return useQuery({ queryKey: ['shop-baskets'], queryFn: () => apiGet<Baskets>('/shop/me/baskets'), staleTime: 5 * 60_000, enabled, retry: 1 })
}

export interface LinkWeek {
  days: number
  since?: string | null
  sessions: number
  item_views?: number
  adds?: number
  checkouts?: number
  orders: number
  cancelled?: number
  value_bhd?: string
  aov_bhd?: string
  customers?: number
  conversion_pct?: number | null
  shares?: number
  top_products?: { item_code: string; display_name?: string | null; units: number; value_bhd: string }[]
  hint?: string | null
}

export function useLinkWeek(enabled = true) {
  return useQuery({ queryKey: ['shop-link-week'], queryFn: () => apiGet<LinkWeek>('/shop/me/link-week'), staleTime: 5 * 60_000, enabled, retry: 1 })
}

/** Measurement only (audit_log 'followup.tap'): fire and forget, never blocks the tap itself. */
export function logFollowupTap(shop: string, kind: 'due' | 'lapsed', channel: 'wa' | 'call' | 'order' | 'view', rank?: number | null): void {
  void apiPost('/shop/me/followups/tap', { shop, kind, channel, rank: rank ?? null }).catch(() => {})
}

/** "1216.510" -> "BHD 1,216.510" (3-dp strings from the API; a number is accepted too). */
export function bhdStr(v?: string | number | null): string {
  const n = Number(v ?? 0)
  return `BHD ${(Number.isFinite(n) ? n : 0).toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`
}

/* ───────────────────────── Sprint 5: the book, the usual basket, Today ───────────────────────── */

/** An over-90 receivable on the shop's Focus account (only accounts that have one carry it). */
export interface CreditChip {
  over_90_bhd: string
  outstanding_bhd: string
  as_of?: string | null
}

/** One shop of the rep's merged book (app/followups.py merge_book). */
export interface BookShop {
  key: string                       // 'f:<Focus name>' | 'm:<phone digits>'
  name: string
  focus_name: string | null
  contact_name: string | null
  phone: string | null              // digits, 973… for Bahrain
  area: string | null
  email: string | null
  sources: ('focus' | 'app')[]
  last_focus_date: string | null
  last_app_order_at: string | null
  last_order_date: string | null    // YYYY-MM-DD, the later of the two
  app_orders: number
  days_since: number | null
  status: 'due' | 'lapsed' | 'ok'
  due: boolean
  why: string | null
  monthly_value_bhd: string | null
  credit: CreditChip | null
  rank: number | null
  holdout?: boolean                 // admin view only
}

export interface Book {
  shops: BookShop[]
  count: number
  counts?: { due?: number; focus?: number; app?: number; credit?: number } | null
  data_through?: string | null
  hint?: string | null
  error?: string | null
}

export function useShopBook(enabled = true) {
  return useQuery({ queryKey: ['shop-book'], queryFn: () => apiGet<Book>('/shop/me/book'), staleTime: 2 * 60_000, enabled, retry: 1 })
}

export interface BasketItem {
  item_code: string
  display_name: string
  qty: number
  in_catalog: boolean
  sold_out: boolean
  price_bhd: string | null
  times_bought?: number
  due?: boolean
  cadence_days?: number | null
  days_since?: number
}

export interface ShopBasket {
  shop: Pick<BookShop, 'key' | 'name' | 'focus_name' | 'contact_name' | 'phone' | 'area' | 'email' | 'last_order_date' | 'status' | 'due' | 'why' | 'credit'>
  usual: BasketItem[]
  last_order: { order_no: string; created_at: string; status: string; lines: BasketItem[] } | null
  suggested: BasketItem[]
  suggested_from: 'last_order' | 'due_regulars' | 'regulars' | null
  available: BasketItem[]
  sold_out: BasketItem[]
  value_bhd: string
  restock_link: string | null
}

export const basketQuery = (key: string) => ({
  queryKey: ['shop-basket', key],
  queryFn: () => apiGet<ShopBasket>(`/shop/me/basket?shop=${encodeURIComponent(key)}`),
  staleTime: 2 * 60_000,
})

export function useShopBasket(key?: string | null) {
  return useQuery({ ...basketQuery(key || ''), enabled: Boolean(key), retry: 1 })
}

/** Fill a blank phone for a Focus shop of the rep's book. Never replaces a number on file. */
export function saveShopPhone(shop: string, phone: string): Promise<{ ok: boolean; saved: boolean; reason?: string }> {
  return apiPost('/shop/me/book/phone', { shop, phone })
}

/** A book row as the shop the catalog orders for. */
export function customerOf(s: Pick<BookShop, 'key' | 'name' | 'focus_name' | 'contact_name' | 'phone' | 'area' | 'email'>): StaffCustomer {
  return {
    key: s.key,
    focus_name: s.focus_name,
    name: s.contact_name || s.name,
    shop: s.name,
    phone: s.phone,
    area: s.area,
    email: s.email,
  }
}

/** The cart / book key of the shop being ordered for ('' = none picked). */
export function shopKeyOf(c?: StaffCustomer | null): string {
  if (!c) return ''
  if (c.key) return c.key
  const digits = String(c.phone || '').replace(/\D/g, '')
  if (digits) return `m:${digits.length === 8 ? `973${digits}` : digits}`
  return c.shop || c.name ? `n:${(c.shop || c.name || '').trim().toLowerCase()}` : ''
}

/** "97333001122" → "+973 3300 1122"; any other number keeps its digits after a "+". */
export function phoneLabel(p?: string | null): string {
  const d = String(p || '').replace(/\D/g, '')
  if (!d) return ''
  if (d.length === 11 && d.startsWith('973')) return `+973 ${d.slice(3, 7)} ${d.slice(7)}`
  if (d.length === 8) return `${d.slice(0, 4)} ${d.slice(4)}`
  return `+${d}`
}

/** The WhatsApp text that carries a restock link. Plain words, the shop's own name when known. */
export function restockText(link: string, contact?: string | null): string {
  const first = firstName(contact)
  return `${first ? `Hello ${first}` : 'Hello'}, your usual restock is ready — open the link, check the quantities and tap Place order:\n${link}`
}

/** "Stock as of 24 Sep (3 days old)"; stale = the server's stock_fresh flag said no (shop_stock_fresh_days,
 *  the same rule the marketplace dates "Sold out" by); an older API without the flag: over 3 days. */
export function stockAge(asOf?: string | null, fresh?: boolean | null): { label: string; stale: boolean } | null {
  if (!asOf) return null
  const d = new Date(`${asOf.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(d.getTime())) return null
  const today = new Date()
  today.setHours(12, 0, 0, 0)
  const days = Math.max(0, Math.round((today.getTime() - d.getTime()) / 86_400_000))
  const age = days === 0 ? 'today' : `${days} day${days === 1 ? '' : 's'} old`
  return { label: `Stock as of ${dayLabel(asOf)} (${age})`, stale: fresh === false || (fresh == null && days > 3) }
}

/** Today in one call (GET /shop/me/today). */
export interface WaitingOrder {
  id: number
  order_no: string
  created_at: string
  customer_name?: string | null
  customer_shop?: string | null
  customer_area?: string | null
  total_bhd: string
  items: number
  units: number
  order_kind?: 'standard' | 'small' | string
  is_test?: boolean
  age_min: number | null
  overdue: boolean
}

export interface MoneyStrip {
  month: string | null
  mtd_bhd: string
  tier: number
  rate: number
  kickback_bhd: string
  next_tier: number | null
  next_gap_bhd: string | null
  next_gain_bhd: string | null
  days_left: number | null
  data_through: string | null
  data_age_days: number | null
  stale: boolean
  basis?: string | null
  is_estimate: boolean
  returns_deducted: boolean
}

export interface TodayData {
  hint?: string | null
  me: ShopMe | null
  money: MoneyStrip | null
  waiting: WaitingOrder[]
  waiting_count: number
  in_progress: number | null
  sla_min: number | null
  due: (FollowupShop & { key: string })[]
  due_count: number
  due_counts?: Followups['counts']
  due_data_through?: string | null
  due_hint?: string | null
  baskets: Baskets | null
  link_week: LinkWeek | null
  restock: { item_code: string; display_name: string; count: number; phones: string[]; first_at: string; ids: number[]; stock_qty?: number | null; back_in_stock: boolean }[]
  errors: Record<string, string>
  generated_at?: string
}

export function useToday() {
  return useQuery({ queryKey: ['shop-today'], queryFn: () => apiGet<TodayData>('/shop/me/today'), staleTime: 30_000, refetchInterval: 60_000, retry: 1 })
}

/** "95" → "1 h 35 min"; the age of an order waiting to be confirmed. */
export function ageLabel(min?: number | null): string {
  if (min == null) return ''
  if (min < 60) return `${Math.max(1, min)} min`
  const h = Math.floor(min / 60)
  if (h < 48) return `${h} h`
  return `${Math.floor(h / 24)} d`
}

/** Go to the catalog for this shop with its usual basket in the cart ("Order again", from any page). */
export function useOrderAgain() {
  const navigate = useNavigate()
  return (shop: StaffCustomer) => {
    setSelectedCustomer(shop)
    navigate('/shop', { state: { again: shop.key } })
  }
}

export function useCustomers(enabled = true) {
  return useQuery({
    queryKey: ['shop-customers'],
    queryFn: async () => (await apiGet<{ customers?: StaffCustomer[] | null }>('/shop/customers')).customers || [],
    enabled,
    staleTime: 60_000,
  })
}

export function useNewOrderCount(): number {
  const { data } = useQuery({
    queryKey: ['shop-orders-new-count'],
    queryFn: () => apiGet<{ count: number }>('/shop/orders?status=new&limit=1'),
    refetchInterval: 60_000,
    retry: 1,
  })
  return Number(data?.count || 0)
}

/* ───────────────────────── the shop I am ordering for ───────────────────────── */

const SEL_KEY = 'yq-staff-customer'
const listeners = new Set<() => void>()
let selected: StaffCustomer | null = read()

function read(): StaffCustomer | null {
  try {
    const raw = sessionStorage.getItem(SEL_KEY)
    return raw ? (JSON.parse(raw) as StaffCustomer) : null
  } catch {
    return null
  }
}

export function setSelectedCustomer(c: StaffCustomer | null) {
  selected = c
  try {
    if (c) sessionStorage.setItem(SEL_KEY, JSON.stringify(c))
    else sessionStorage.removeItem(SEL_KEY)
  } catch {
    /* ignore */
  }
  listeners.forEach((fn) => fn())
}

export function getSelectedCustomer(): StaffCustomer | null {
  return selected
}

export function useSelectedCustomer(): StaffCustomer | null {
  return useSyncExternalStore(
    (fn) => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    () => selected,
    () => null,
  )
}

/* ───────────────────────── bearer-protected images ───────────────────────── */

/** `<img src>` cannot carry a bearer token — fetch the blob and hand back an object URL. */
export function useAuthedBlob(path?: string | null): { blobUrl: string | null; loading: boolean } {
  // { for, url } — the effect records what it fetched and for which path; "loading" is derived.
  const [got, setGot] = useState<{ for: string; url: string | null } | null>(null)
  useEffect(() => {
    if (!path) return
    let alive = true
    let url: string | null = null
    ;(async () => {
      try {
        const session = await getSessionSafe()
        const base = (import.meta.env.VITE_API_URL as string) || ''
        const res = await fetch(`${base}${path}`, { headers: session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {} })
        if (!res.ok) throw new Error(String(res.status))
        url = URL.createObjectURL(await res.blob())
      } catch {
        url = null
      }
      if (alive) setGot({ for: path, url })
    })()
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [path])
  const current = path && got?.for === path ? got : null
  return { blobUrl: current?.url ?? null, loading: Boolean(path) && !current }
}

/* ───────────────────────── words & numbers ───────────────────────── */

export function greeting(name?: string | null): string {
  const h = new Date().getHours()
  const part = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
  return name ? `${part}, ${name}` : part
}

export function firstName(full?: string | null): string {
  return (full || '').trim().split(/\s+/)[0] || ''
}

export function initials(name?: string | null): string {
  return (name || '')
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join('')
}

export function relTime(iso?: string | null): string {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  const m = Math.max(0, Math.round(ms / 60000))
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} h ago`
  const d = Math.round(h / 24)
  if (d < 7) return `${d} d ago`
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function bhd3(n?: number | null): string {
  return `BHD ${Number(n || 0).toFixed(3)}`
}

export function waLink(phone?: string | null, text?: string): string | null {
  const digits = String(phone || '').replace(/\D/g, '')
  if (!digits) return null
  const full = digits.length === 8 ? `973${digits}` : digits
  return `https://wa.me/${full}${text ? `?text=${encodeURIComponent(text)}` : ''}`
}

export function telLink(phone?: string | null): string | null {
  const digits = String(phone || '').replace(/\D/g, '')
  if (!digits) return null
  return `tel:${digits.length === 8 ? `+973${digits}` : `+${digits}`}`
}
