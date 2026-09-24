import { useEffect, useState, useSyncExternalStore } from 'react'
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
