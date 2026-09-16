import { useEffect, useState } from 'react'
import type { BadgeKind, ShopItem, StockStatus } from '@/lib/shopApi'
import type { ChipTone } from '../ui/Chip'

/**
 * Pure helpers for the marketplace — money, dates, quantity rules, badges, validation.
 * A market-local copy of the bits of pages/shop/shared.ts the merchant build needs, so that
 * file (and its portal class strings) stays out of the market bundle and CSS.
 */

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
  const dt = new Date(d)
  if (Number.isNaN(dt.getTime())) return null
  return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

export function fmtDateShort(d?: string | null): string | null {
  if (!d) return null
  const dt = new Date(d)
  if (Number.isNaN(dt.getTime())) return null
  return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function fmtDateTime(d?: string | null): string | null {
  if (!d) return null
  const dt = new Date(d)
  if (Number.isNaN(dt.getTime())) return null
  return dt.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
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

/** Round a wanted quantity up to the item's rules (never below the minimum, whole packs). */
export function normalizeQty(item: ShopItem | null | undefined, wanted: number): number {
  const step = stepOf(item)
  const min = minQtyOf(item)
  const q = Math.max(min, Math.ceil(Math.max(0, wanted) / step) * step)
  return Math.min(q, 9999)
}

/** The next volume tier above `qty`, if any. */
export function nextTier(item: ShopItem, qty: number) {
  return (item.tiers || []).find((t) => t.min_qty > qty) || null
}

/** Unit price the merchant would pay at `qty` given the public tiers (client estimate only). */
export function unitAt(item: ShopItem, qty: number): number | null {
  if (item.price_bhd == null) return null
  let price = Number(item.price_bhd)
  for (const t of item.tiers || []) if (qty >= t.min_qty && Number(t.unit_price_bhd) < price) price = Number(t.unit_price_bhd)
  return price
}

/* ───────────────────────── badges & stock ───────────────────────── */

export const BADGE_ORDER: BadgeKind[] = ['on_offer', 'price_drop', 'clearance', 'best_seller', 'selling_fast', 'new', 'trending']

export const BADGE_META: Record<BadgeKind, { label: string; tone: ChipTone }> = {
  on_offer: { label: 'Offer', tone: 'plum' },
  best_seller: { label: 'Best seller', tone: 'ink' },
  selling_fast: { label: 'Selling fast', tone: 'warn' },
  new: { label: 'New', tone: 'ok' },
  trending: { label: 'Trending', tone: 'grey' },
  price_drop: { label: 'Price drop', tone: 'bad' },
  clearance: { label: 'Clearance', tone: 'warn' },
}

export function badgeMeta(kind: string): { label: string; tone: ChipTone } {
  return BADGE_META[kind as BadgeKind] || { label: String(kind).replace(/_/g, ' '), tone: 'grey' }
}

/** The badges a card shows: priority order, max `limit`, "selling fast" dropped once sold out. */
export function cardBadges(item: ShopItem, limit = 2): BadgeKind[] {
  const have = new Set(item.badges || [])
  if (item.stock_status === 'out_of_stock') have.delete('selling_fast')
  return BADGE_ORDER.filter((b) => have.has(b)).slice(0, limit)
}

/**
 * The struck-through anchor next to a price — only ever a REAL number: the previous trade
 * price after a genuine cut (`was_bhd`), else the retail price when the owner enabled the
 * compare setting. Never an invented "was".
 */
export function priceAnchor(item: ShopItem, showCompare: boolean): { was: number; pct: number; kind: 'was' | 'retail' } | null {
  const price = item.price_bhd != null ? Number(item.price_bhd) : null
  if (price == null) return null
  if (item.was_bhd != null && Number(item.was_bhd) > price) {
    const was = Number(item.was_bhd)
    return { was, pct: Math.round(((was - price) / was) * 100), kind: 'was' }
  }
  if (showCompare && item.compare_at_bhd != null && Number(item.compare_at_bhd) > price) {
    const was = Number(item.compare_at_bhd)
    return { was, pct: Number(item.save_pct) || Math.round(((was - price) / was) * 100), kind: 'retail' }
  }
  return null
}

export function hasBadge(item: ShopItem, kind: BadgeKind): boolean {
  return (item.badges || []).includes(kind)
}

/** Status only — the public shop never reveals a stock number (docs/SHOP.md). */
export const STOCK_META: Record<StockStatus, { label: string; tone: ChipTone }> = {
  in_stock: { label: 'In stock', tone: 'ok' },
  low_stock: { label: 'Only a few left', tone: 'warn' },
  out_of_stock: { label: 'Sold out', tone: 'bad' },
}

export function stockMeta(status?: StockStatus | null) {
  return STOCK_META[(status || 'in_stock') as StockStatus] || STOCK_META.in_stock
}

export function isOut(item?: ShopItem | null): boolean {
  return item?.stock_status === 'out_of_stock'
}

/* ───────────────────────── product names ─────────────────────────
   Catalog data is uneven: for many SKUs `display_name` is just the code ("X01") and the real
   name lives in the first line of `spec` ("X01 3A 3in1 PVC USB Cable (…) (VFAN)"). The house
   brand suffix "(VFAN)" is noise on a single-brand shelf. One helper, used everywhere, so a
   merchant always reads a product name, and the code sits underneath it. */

const BRAND_SUFFIX = /\s*\((vfan|v-fan)\)\s*$/i

function stripCode(text: string, code: string): string {
  const esc = code.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return text.replace(new RegExp(`^\\s*${esc}\\s*[-–:·]?\\s*`, 'i'), '')
}

export function productName(item: Pick<ShopItem, 'item_code' | 'display_name' | 'spec'>): string {
  const code = item.item_code
  const dn = (item.display_name || '').trim()
  const firstSpec = (item.spec || '').split('\n')[0].trim()
  let raw = dn && dn.toUpperCase() !== code.toUpperCase() ? dn : firstSpec
  raw = raw.replace(BRAND_SUFFIX, '')
  const noCode = stripCode(raw, code).trim()
  const out = (noCode.length >= 3 ? noCode : raw).replace(/\s{2,}/g, ' ').trim()
  return out || code
}

/** The detail line under a name: the spec minus whatever the name already said. */
export function productDetail(item: Pick<ShopItem, 'item_code' | 'display_name' | 'spec'>): string {
  const name = productName(item).toLowerCase()
  const lines = (item.spec || '')
    .split('\n')
    .map((l) => l.replace(BRAND_SUFFIX, '').trim())
    .filter(Boolean)
  const rest = lines.filter((l) => {
    const t = stripCode(l, item.item_code).trim().toLowerCase()
    return t && t !== name && !name.includes(t) && !t.includes(name)
  })
  return rest.join(' · ')
}

/* ───────────────────────── names & words ───────────────────────── */

/** "Ali Hassan" → "Ali". */
export function firstName(full?: string | null): string {
  return String(full || '').trim().split(/\s+/)[0] || ''
}

export function initials(name?: string | null): string {
  return (name || '')
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join('')
}

/** Title-case a shouting category ("BLUETOOTH HEADSET" → "Bluetooth headset"). */
export function niceCategory(cat?: string | null): string {
  const s = String(cat || '').trim().toLowerCase()
  return s ? s[0].toUpperCase() + s.slice(1) : ''
}

export function categorySlug(cat: string): string {
  return cat.toLowerCase().replace(/\s+/g, '-')
}

export function categoryFromSlug(slug: string, categories: string[]): string | null {
  const want = decodeURIComponent(slug).toLowerCase().replace(/-/g, ' ')
  return categories.find((c) => c.toLowerCase() === want) || null
}

/* ───────────────────────── session ───────────────────────── */

const SESSION_KEY = 'yq-shop-session'

/** One id per browser, so the funnel can join view → item → add → checkout. */
export function sessionId(): string {
  try {
    const found = localStorage.getItem(SESSION_KEY)
    if (found) return found
    const made = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
    localStorage.setItem(SESSION_KEY, made)
    return made
  } catch {
    return 'anon'
  }
}

/* ───────────────────────── countdown (hours, never seconds) ───────────────────────── */

export function countdownLabel(endsAt?: string | null): string | null {
  if (!endsAt) return null
  const end = new Date(endsAt).getTime()
  if (Number.isNaN(end)) return null
  const ms = end - Date.now()
  if (ms <= 0) return null
  const mins = Math.floor(ms / 60000)
  const days = Math.floor(mins / 1440)
  const hours = Math.floor((mins % 1440) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h`
  return `${Math.max(1, mins)} min`
}

/** Live "3d 4h" that ticks once a minute — real offers only, never a fake timer. */
export function useCountdown(endsAt?: string | null): string | null {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!endsAt) return
    const id = window.setInterval(() => setTick((t) => t + 1), 60000)
    return () => window.clearInterval(id)
  }, [endsAt])
  return countdownLabel(endsAt)
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

/** Normalise an item code for matching: "uk 15" / "UK-15" → "UK15". */
export function codeKey(s: string): string {
  return String(s || '')
    .toUpperCase()
    .replace(/[\s\-_.]/g, '')
}

/** Cheap, stable hash for per-device rotation (hero pick). */
export function hashStr(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return h >>> 0
}
