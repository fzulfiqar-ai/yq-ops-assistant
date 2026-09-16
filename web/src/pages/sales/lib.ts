import { useEffect, useState, useSyncExternalStore } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { getSessionSafe } from '@/lib/supabase'
import type { StaffCustomer } from '@/lib/shopApi'

/**
 * Shared pieces of the salesman app (web/src/pages/sales/*, components/SalesmanShell.tsx):
 * the rep's own card (`/shop/me`), his book of shops (`/shop/customers`), the shop he is
 * currently ordering for (session-scoped, so the catalog and the checkout agree), bearer-
 * protected images (the QR), and small formatting helpers.
 */

/* ───────────────────────── /shop/me ───────────────────────── */

export interface ShopMe {
  salesman: { id: number; name: string; referral_code?: string | null; phone?: string | null; whatsapp?: string | null; title?: string | null; photo_url?: string | null } | null
  link?: string | null
  qr_url?: string | null
  kpis?: { orders_7d?: number; orders_30d?: number; value_30d_bhd?: number; customers_30d?: number } | null
  focus?: { revenue_90d_bhd?: number | null; target_bhd?: number | null } | null
  hint?: string | null
}

export function useShopMe() {
  return useQuery({ queryKey: ['shop-me'], queryFn: () => apiGet<ShopMe>('/shop/me'), staleTime: 60_000 })
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
