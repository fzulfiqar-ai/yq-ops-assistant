import { useEffect, useState } from 'react'
import type { BadgeTone } from '@/components/ui/badge'
import type { BadgeKind, ShopItem, StockStatus } from '@/lib/shopApi'

/* ───────────────────────── money & dates ───────────────────────── */

/** Bahrain prices are quoted to 3 decimals — always, even for a round number. */
export function money(n?: number | null): string {
  return Number(n || 0).toFixed(3)
}

export function bhd(n?: number | null): string {
  return `BHD ${money(n)}`
}

export function fmtDate(d?: string | null): string | null {
  if (!d) return null
  try {
    const dt = new Date(d)
    if (Number.isNaN(dt.getTime())) return null
    return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
  } catch {
    return null
  }
}

export function fmtDateTime(d?: string | null): string | null {
  if (!d) return null
  try {
    const dt = new Date(d)
    if (Number.isNaN(dt.getTime())) return null
    return dt.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
  } catch {
    return null
  }
}

/* ───────────────────────── quantities ───────────────────────── */

/** Pieces added per tap — a pack size when the item ships in packs. */
export function stepOf(item?: ShopItem | null): number {
  const step = Number(item?.pack_size) || 1
  return step > 0 ? step : 1
}

/** Smallest orderable quantity: the MOQ, rounded up to a whole number of packs. */
export function minQtyOf(item?: ShopItem | null): number {
  const step = stepOf(item)
  const moq = Number(item?.moq) || 1
  return Math.max(step, Math.ceil(moq / step) * step)
}

/* ───────────────────────── badges & stock ───────────────────────── */

export const BADGE_META: Record<BadgeKind, { label: string; tone: BadgeTone }> = {
  best_seller: { label: 'Best seller', tone: 'accent' },
  trending: { label: 'Trending', tone: 'accent' },
  new: { label: 'New', tone: 'ink' },
  on_offer: { label: 'On offer', tone: 'green' },
  price_drop: { label: 'Price drop', tone: 'rose' },
}

export function badgeMeta(kind: string) {
  return BADGE_META[kind as BadgeKind] || { label: String(kind).replace(/_/g, ' '), tone: 'grey' as BadgeTone }
}

/** Status only — the shop never reveals a stock number (docs/SHOP.md). */
export const STOCK_META: Record<StockStatus, { label: string; tone: BadgeTone }> = {
  in_stock: { label: 'In stock', tone: 'green' },
  low_stock: { label: 'Only a few left', tone: 'amber' },
  out_of_stock: { label: 'Out of stock', tone: 'grey' },
}

export function stockMeta(status?: StockStatus | null) {
  return STOCK_META[(status || 'in_stock') as StockStatus] || STOCK_META.in_stock
}

export function hasBadge(item: ShopItem, kind: BadgeKind): boolean {
  return (item.badges || []).includes(kind)
}

/* ───────────────────────── session ───────────────────────── */

const SESSION_KEY = 'yq-shop-session'

/** One id per browser, so the funnel can join view → item → add → checkout. */
export function sessionId(): string {
  try {
    const found = localStorage.getItem(SESSION_KEY)
    if (found) return found
    const made =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
    localStorage.setItem(SESSION_KEY, made)
    return made
  } catch {
    return 'anon'
  }
}

/* ───────────────────────── countdown ───────────────────────── */

function countdownLabel(endsAt: string): string | null {
  const end = new Date(endsAt).getTime()
  if (Number.isNaN(end)) return null
  const ms = end - Date.now()
  if (ms <= 0) return null
  const mins = Math.floor(ms / 60000)
  const days = Math.floor(mins / 1440)
  const hours = Math.floor((mins % 1440) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${mins % 60}m`
  const secs = Math.floor((ms % 60000) / 1000)
  return `${mins}m ${secs}s`
}

/**
 * Live "Ends in 2d 4h". Returns null once the offer is over (or has no end).
 * The label is derived on every render and the interval only nudges a tick, so
 * there is no state to get out of sync with the prop.
 */
export function useCountdown(endsAt?: string | null): string | null {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!endsAt) return
    const id = setInterval(() => {
      if (countdownLabel(endsAt) == null) clearInterval(id)
      setTick((t) => t + 1)
    }, 1000)
    return () => clearInterval(id)
  }, [endsAt])
  return endsAt ? countdownLabel(endsAt) : null
}

/* ───────────────────────── validation ───────────────────────── */

/** Keep a leading + and the digits; people paste spaces, dashes and brackets. */
export function cleanPhone(raw: string): string {
  const trimmed = (raw || '').trim()
  const plus = trimmed.startsWith('+')
  const digits = trimmed.replace(/\D/g, '')
  return plus ? `+${digits}` : digits
}

/** 8-digit Bahrain local, or an international number with a country code. */
export function isPhone(raw: string): boolean {
  const v = cleanPhone(raw)
  if (v.startsWith('+')) return /^\+\d{8,15}$/.test(v)
  return /^\d{8}$/.test(v)
}

export function isEmail(raw: string): boolean {
  const v = (raw || '').trim()
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v)
}
