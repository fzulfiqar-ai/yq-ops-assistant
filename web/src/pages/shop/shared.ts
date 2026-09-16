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
  selling_fast: { label: 'Selling fast', tone: 'amber' },
  clearance: { label: 'Clearance', tone: 'amber' },
}

export function badgeMeta(kind: string) {
  return BADGE_META[kind as BadgeKind] || { label: String(kind).replace(/_/g, ' '), tone: 'grey' as BadgeTone }
}

/** Status only — the PUBLIC shop never reveals a stock number (docs/SHOP.md). */
export const STOCK_META: Record<StockStatus, { label: string; tone: BadgeTone }> = {
  in_stock: { label: 'In stock', tone: 'green' },
  low_stock: { label: 'Only a few left', tone: 'amber' },
  out_of_stock: { label: 'Sold out', tone: 'rose' },
}

export function stockMeta(status?: StockStatus | null) {
  return STOCK_META[(status || 'in_stock') as StockStatus] || STOCK_META.in_stock
}

/**
 * The one stock chip a card or sheet shows.
 *
 * A salesman standing in a shop needs the number — "42 in stock" ends the
 * argument. A customer only ever sees the wording, because `stock_qty` is
 * absent from the public payload by design. One helper, so the two modes can
 * never drift apart.
 */
export function stockPill(item?: ShopItem | null): { label: string; tone: BadgeTone } {
  const qty = item?.stock_qty
  const status = item?.stock_status
  if (typeof qty !== 'number' || !Number.isFinite(qty)) return stockMeta(status)
  if (qty <= 0 || status === 'out_of_stock') return { label: 'Sold out', tone: 'rose' }
  if (status === 'low_stock') return { label: `Only ${qty} left`, tone: 'amber' }
  return { label: `${qty} in stock`, tone: 'green' }
}

/* ───────────────────────── names ───────────────────────── */

/** "Ali Hassan" → "Ali". Used for the one-tap "Send confirmation to Ali" button. */
export function firstName(full?: string | null): string {
  return String(full || '').trim().split(/\s+/)[0] || ''
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

/* ───────────────────────── design system ─────────────────────────
   One place for the class strings that repeat across the shop, so a control
   can't quietly drift into a different height, radius or focus ring. Canvas
   #faf9fc · card white · ink #1a1430 · muted #6b6480 · hairline #ece9f3 ·
   ONE accent #6d28d9 (price, primary action, selection). */

/** Visible focus ring — every interactive element in the shop wears this. */
export const RING = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]/70 focus-visible:ring-offset-2 focus-visible:ring-offset-white'

/** Same ring, drawn inside the element (for full-bleed buttons and photos). */
export const RING_INSET = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#6d28d9]/70'

/** Text input / textarea / select. 44px, hairline, accent focus. */
export const FIELD =
  'h-11 w-full rounded-xl border border-[#e4e0ee] bg-white px-3 text-[14px] leading-none text-[#1a1430] outline-none transition duration-150 ease-out placeholder:text-[#a8a2bb] hover:border-[#d9d2ee] focus:border-[#6d28d9] focus:ring-2 focus:ring-[#6d28d9]/15'

export const LABEL = 'mb-1.5 block text-[11.5px] font-semibold tracking-[0.01em] text-[#1a1430]'

/** Quiet secondary button — white, hairline, ink text. */
export const BTN_GHOST =
  'inline-flex items-center justify-center gap-1.5 rounded-xl border border-[#e4e0ee] bg-white font-semibold text-[#1a1430] transition duration-150 ease-out hover:border-[#d9d2ee] hover:bg-[#f7f5fb] active:scale-[.99]'

/** The one accent button. Nothing else on the page may be this colour. */
export const BTN_PRIMARY =
  'inline-flex items-center justify-center gap-2 rounded-xl bg-[#6d28d9] font-semibold text-white transition duration-150 ease-out hover:bg-[#5b21b6] active:scale-[.99] disabled:pointer-events-none'

/** Card surface: 20px radius, hairline, a shadow you feel rather than see. */
export const CARD =
  'rounded-[20px] border border-[#ece9f3] bg-white shadow-[0_1px_2px_rgba(24,16,48,.04)]'
