import { useEffect, useState } from 'react'
import type { BadgeKind, ShopItem, StockStatus } from '@/lib/shopApi'
import { S } from '../strings'
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
  // "Deal" is reserved for real offers; clearing lines read "Last chance" (plan D3).
  on_offer: { label: S.deals.deal, tone: 'deal' },
  best_seller: { label: 'Best seller', tone: 'ink' },
  selling_fast: { label: 'Selling fast', tone: 'warn' },
  new: { label: 'New', tone: 'fresh' },
  trending: { label: 'Trending', tone: 'grey' },
  price_drop: { label: 'Price drop', tone: 'bad' },
  clearance: { label: S.deals.badge, tone: 'deal' },
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
 * The struck-through anchor next to a price — only ever the REAL previous trade price after a
 * genuine cut in the price book (`was_bhd`). Retail is never struck through any more: it is
 * shown as the merchant's margin by `marginOf()`. The signature (and the `'retail'` kind in the
 * type) stays so existing consumers compile; `kind` is always `'was'` now.
 */
export function priceAnchor(item: ShopItem, _showCompare?: boolean): { was: number; pct: number; kind: 'was' | 'retail' } | null {
  void _showCompare
  const price = item.price_bhd != null ? Number(item.price_bhd) : null
  if (price == null || !Number.isFinite(price)) return null
  const was = item.was_bhd != null ? Number(item.was_bhd) : null
  if (was == null || !Number.isFinite(was) || was <= price) return null
  return { was, pct: Math.round(((was - price) / was) * 100), kind: 'was' }
}

/**
 * Merchant maths from real payload numbers only. Retail reaches the public payload as
 * `compare_at_bhd` (backend `anchor_ok`), `b2c_bhd` where the payload carries it. Null unless
 * retail > price > 0. `pct` is the margin as a share of the retail price (what the shop keeps).
 */
export function marginOf(item: ShopItem): { price: number; retail: number; margin: number; pct: number } | null {
  const rawRetail = item.compare_at_bhd ?? item.b2c_bhd
  if (item.price_bhd == null || rawRetail == null) return null
  const price = Number(item.price_bhd)
  const retail = Number(rawRetail)
  if (!Number.isFinite(price) || !Number.isFinite(retail) || !(price > 0) || !(retail > price)) return null
  const margin = Math.round((retail - price) * 1000) / 1000
  return { price, retail, margin, pct: Math.round((margin / retail) * 100) }
}

export function hasBadge(item: ShopItem, kind: BadgeKind): boolean {
  return (item.badges || []).includes(kind)
}

/** Status only — the public shop never reveals a stock number (docs/SHOP.md). */
export const STOCK_META: Record<StockStatus, { label: string; tone: ChipTone }> = {
  in_stock: { label: S.card.stockIn, tone: 'ok' },
  low_stock: { label: S.card.stockLow, tone: 'warn' },
  out_of_stock: { label: S.card.stockOut, tone: 'bad' },
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

/** "(VFAN)" anywhere in a line — the export often repeats the code after it. */
const BRAND_TAG = /\s*\((?:vfan|v-fan)\)/gi
/** Upper-case variant suffixes the export leaves at the front of a name ("P04 CL 1Mtr …"). */
const VARIANT_LEAD = /^(?:CCC|CCL|CC|CL|UC|UL|UM|TC|LT|MK)\s+/

const escRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/**
 * The code at the start of a line; spaces and hyphens in the code match either ("X05 UC-1Mtr" ~
 * "X05 UC 1Mtr"). A length the code ends with stays in the name ("X24 CC 1Mtr Cable …" →
 * "1Mtr Cable …"), so "P01-1Mtr" and "P01-2Mtr" do not both read "TPE (…)".
 */
function stripCode(text: string, code: string): string {
  const parts = code.trim().split(/[\s-]+/).filter(Boolean)
  if (parts.length > 1 && /^\d+(?:\.\d+)?\s*mtrs?$/i.test(parts[parts.length - 1])) parts.pop()
  else if (parts.length > 2 && /^mtrs?$/i.test(parts[parts.length - 1]) && /^\d+(?:\.\d+)?$/.test(parts[parts.length - 2])) parts.splice(-2)
  if (!parts.length) return text
  return text.replace(new RegExp(`^\\s*${parts.map(escRe).join('[\\s-]*')}\\s*[-–:·]?\\s*`, 'i'), '')
}

/** The model token of a code: "X05 UC-1Mtr" → "X05". */
function codeHead(code: string): string {
  return code.trim().split(/[\s-]+/)[0] || ''
}

/**
 * One name line without the brand tag, doubled spaces, or the code the export repeats at the end
 * of a line that already starts with it ("P04 CL 1Mtr … (VFAN) P04 CL"). A line that only
 * mentions the code ("Model: H08") keeps it.
 */
function cleanLine(text: string, code: string): string {
  let t = text.replace(BRAND_TAG, '').replace(/\s{2,}/g, ' ').trim()
  const head = codeHead(code)
  if (head && t.toUpperCase().startsWith(head.toUpperCase())) {
    t = t.replace(new RegExp(`\\s+${escRe(head)}(?:[\\s-]+[\\w().]+){0,3}\\s*$`, 'i'), '').trim()
  }
  return t
}

export function productName(item: Pick<ShopItem, 'item_code' | 'display_name' | 'spec'>): string {
  const code = item.item_code
  const dn = (item.display_name || '').trim()
  const firstSpec = (item.spec || '').split('\n')[0].trim()
  const raw = cleanLine(dn && dn.toUpperCase() !== code.toUpperCase() ? dn : firstSpec, code)
  let noCode = stripCode(raw, code).trim()
  if (noCode === raw) {
    // the line starts with the model only ("X24 2Mtr Cable …" for code "X24 CC")
    const head = codeHead(code)
    if (head) noCode = raw.replace(new RegExp(`^${escRe(head)}(?![\\w])\\s*[-–:·]?\\s*`, 'i'), '').trim()
  }
  const lead = noCode.replace(VARIANT_LEAD, '')
  if (lead.length >= 3) noCode = lead
  const out = (noCode.length >= 3 ? noCode : raw).replace(/\s{2,}/g, ' ').trim()
  return out || code
}

/** The detail line under a name: the spec minus whatever the name already said. */
export function productDetail(item: Pick<ShopItem, 'item_code' | 'display_name' | 'spec'>): string {
  const name = productName(item).toLowerCase()
  const lines = (item.spec || '')
    .split('\n')
    .map((l) => cleanLine(l, item.item_code))
    .filter(Boolean)
  const rest = lines.filter((l) => {
    const t = stripCode(l, item.item_code).trim().toLowerCase()
    return t && t !== name && !name.includes(t) && !t.includes(name)
  })
  return rest.join(' · ')
}

/* ───────────────────────── variant chips ─────────────────────────
   Look-alike SKUs share a family name ("20W Charger + … Cable", "1.2Mtr … AUX Cable"); what tells
   them apart is buried in the text. variantOf() lifts the distinguishing facts into ≤3 short chips
   — connector, included cable, capacity, wattage, length, ports — read only from the item's own
   name/spec, never guessed. Nothing matched → no chip. */

type End = 'Type-C' | 'Lightning' | 'Micro' | 'USB' | 'AUX F' | 'AUX M' | 'AUX' | '3.5 mm'

const END_SRC = String.raw`aux\s*female|aux\s*male|type[\s-]?c\b|usb[\s-]?c\b|usb(?:[\s-]?a\b)?|lightning|ligthning|lighting|micro|3\.5\s?mm|aux|\bc\b|\bl\b|\ba\b`
const PAIR_RE = new RegExp(String.raw`(${END_SRC})\s+(?:to|→)\s+(${END_SRC})((?:\s*\+\s*(?:${END_SRC}))*)`, 'gi')

function endOf(raw: string): End | null {
  const t = raw.toLowerCase().replace(/\s+/g, ' ').trim()
  if (/^aux ?female$/.test(t)) return 'AUX F'
  if (/^aux ?male$/.test(t)) return 'AUX M'
  if (/^(type[ -]?c|usb[ -]?c|c)$/.test(t)) return 'Type-C'
  if (/^(lightning|ligthning|lighting|l)$/.test(t)) return 'Lightning'
  if (t === 'micro') return 'Micro'
  if (/^(usb([ -]?a)?|a)$/.test(t)) return 'USB'
  if (/^3\.5 ?mm$/.test(t)) return '3.5 mm'
  if (t === 'aux') return 'AUX'
  return null
}

/** Short form for tight labels: Type-C → "C". */
const shortEnd = (e: End) => (e === 'Type-C' ? 'C' : e)
const END_RANK: Record<End, number> = { 'AUX F': 0, 'AUX M': 0, AUX: 0, '3.5 mm': 0, USB: 1, 'Type-C': 2, Lightning: 3, Micro: 4 }

interface Pair {
  from: End
  to: End[]
}

/** Every "X to Y (+ Z)" in the text, device end last ("Lightning to Type-C" reads Type-C → Lightning). */
function pairsIn(text: string): Pair[] {
  const out: Pair[] = []
  const seen = new Set<string>()
  for (const m of text.matchAll(PAIR_RE)) {
    const first = endOf(m[1])
    const to = [m[2], ...(m[3] || '').split('+')].map((s) => s.trim()).filter(Boolean).map(endOf)
    if (!first || to.some((e) => !e)) continue
    let from: End = first
    let ends = to as End[]
    if (ends.length === 1 && (from === 'Lightning' || from === 'Micro') && (ends[0] === 'Type-C' || ends[0] === 'USB')) {
      const device = from
      from = ends[0]
      ends = [device]
    }
    if (ends.length > 1) ends = ends.slice().sort((a, b) => END_RANK[a] - END_RANK[b])
    const key = `${from}>${ends.join('+')}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push({ from, to: ends })
  }
  return out
}

function pairLabel(p: Pair): string {
  return p.to.length > 1 ? `${p.from} → ${p.to.map(shortEnd).join(' + ')}` : `${p.from} → ${p.to[0]}`
}

/** "2USB" / "PD +PD" / "USB 48W + Type-C 30W" / "1USB + 1C" → "2 USB" / "2 × PD" / "USB + Type-C". */
function portList(list: string): string | null {
  const kinds = new Map<string, number>()
  for (const raw of list.split(/\s*[+&,/]\s*/)) {
    const tok = raw.replace(/\d+(?:\.\d+)?\s?w\b/gi, '').trim()
    if (!tok) continue
    const m = tok.match(/^(\d)\s*(.*)$/)
    const count = m ? Number(m[1]) : 1
    const name = (m ? m[2] : tok).trim().toLowerCase()
    let kind: string | null = null
    if (/^(type[\s-]?c|usb[\s-]?c|c)$/.test(name)) kind = 'Type-C'
    else if (name === 'pd') kind = 'PD'
    else if (/^(usb([\s-]?a)?|a)$/.test(name)) kind = 'USB'
    else if (/^qc/.test(name)) kind = 'QC'
    else if (/^(lightning|ligthning)$/.test(name)) kind = 'Lightning'
    else if (name === 'micro') kind = 'Micro'
    if (!kind || count < 1) return null // an unknown token: say nothing rather than half the truth
    kinds.set(kind, (kinds.get(kind) || 0) + count)
  }
  const parts = [...kinds]
  if (!parts.length) return null
  if (parts.length === 1) {
    const [kind, n] = parts[0]
    if (n === 1) return `${kind} port`
    return kind === 'PD' ? `${n} × PD` : `${n} ${kind}`
  }
  return parts.map(([kind, n]) => (n > 1 ? `${n} ${kind}` : kind)).join(' + ')
}

function portsIn(text: string): string | null {
  const grouped = text.match(/\b\d\s*ports?\s*\(([^()]+)\)/i) // "2 Port (USB + Type C)"
  if (grouped) return portList(grouped[1])
  const paren = text.match(/\(([^()]*?)\s*ports?\s*\)/i) // "(USB + Type-C Port)"
  if (paren) return portList(paren[1])
  const plus = text.match(/\+\s*((?:usb|type[\s-]?c|pd|\d)[\w\s&-]*?)\s+ports?\b/i) // "+ USB & Type C ports"
  if (plus) return portList(plus[1])
  const single = text.match(/\b(type[\s-]?c|usb[\s-]?c|lightning|ligthning|micro|usb)\s+ports?\b/i) // "Type-C Port"
  if (single) return portList(single[1])
  const charging = text.match(/charging\s+(?:port|interface)\s*:\s*(type[\s-]?c|lightning|micro)/i) // "Charging port: Lightning"
  if (charging) return portList(charging[1])
  return null
}

const fmtNum = (n: number) => String(Number(n.toFixed(2)))

function maxWatt(text: string): string | null {
  let best = 0
  for (const m of text.matchAll(/(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)\s?w(?![a-z0-9])/gi)) best = Math.max(best, Number(m[1]))
  return best > 0 ? `${fmtNum(best)}W` : null
}

function lengthIn(text: string): string | null {
  for (const m of text.matchAll(/(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)\s?(?:mtrs?|meters?|metres?|m)(?![a-z])/gi)) {
    const before = text.slice(Math.max(0, (m.index || 0) - 14), m.index || 0)
    const n = Number(m[1])
    if (/distance\W*$|range\W*$/i.test(before) || !(n > 0) || n > 5) continue // range of a speaker, not a cable
    return `${fmtNum(n)} m`
  }
  return null
}

function capacityIn(text: string): string | null {
  let best = 0
  for (const m of text.matchAll(/(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d{3,6})\s?mah\b/gi)) best = Math.max(best, Number(m[1].replace(/,/g, '')))
  return best >= 1000 ? `${best.toLocaleString('en-US')} mAh` : null
}

/** The chips one text yields, in priority order (uncapped). */
function chipsFrom(text: string): string[] {
  const out: string[] = []
  const add = (c: string | null | undefined) => {
    if (c && !out.includes(c)) out.push(c)
  }
  const assortment = /\bmix\b|\d+\s*pcs\b/i.test(text) // a box of mixed cables: no single connector
  const powered = /charger|\bpb\b|power\s*bank/i.test(text) // the connector named is the cable in the box
  const pairs = assortment ? [] : pairsIn(text)
  const nIn1 = text.match(/\b(\d)\s*-?\s*in\s*-?\s*1\b/i)

  // connector
  if (!powered && !assortment) {
    if (pairs.length === 1) add(pairLabel(pairs[0]))
    else if (!pairs.length) {
      const jack = text.match(/\b(3\.5\s?mm|type[\s-]?c|lightning|ligthning)\s+(?:l-shape\s+)?jack\b/i)
      if (jack) add(`${endOf(jack[1])} jack`)
      else if (!nIn1 && /cable/i.test(text)) {
        const kinds = new Set<End>()
        if (/type[\s-]?c\b|usb[\s-]?c\b/i.test(text)) kinds.add('Type-C')
        if (/lightn|ligthn|lighting/i.test(text)) kinds.add('Lightning')
        if (/micro/i.test(text)) kinds.add('Micro')
        if (kinds.size === 1 && /\busb\b/i.test(text.replace(/usb[\s-]?c\b/gi, ''))) add(`USB → ${[...kinds][0]}`)
      }
    }
    if (nIn1 && !(pairs.length === 1 && pairs[0].to.length > 1)) add(`${nIn1[1]}-in-1`)
  }

  // included cable (chargers, power banks)
  if (powered) {
    const attachedN = text.match(/\+\s*attached\s+(\d+)\s+cables?\b/i)
    const nIn1Cable = text.match(/\+\s*(\d)\s*-?\s*in\s*-?\s*1\s+cables?\b/i)
    const single = text.match(/\+\s*(type[\s-]?c|usb[\s-]?c|lightning|ligthning|micro)\s+cables?\b/i)
    if (pairs.length === 1) add(`+ ${shortEnd(pairs[0].from)} → ${pairs[0].to.map(shortEnd).join(' + ')} cable`)
    else if (attachedN) add(`${attachedN[1]} built-in cables`)
    else if (/\+\s*attached\s+cables?\b/i.test(text)) add('Built-in cable')
    else if (nIn1Cable) add(`+ ${nIn1Cable[1]}-in-1 cable`)
    else if (single) add(`+ ${endOf(single[1])} cable`)
    else if (/\+\s*cables?\b/i.test(text)) add('+ cable')
    else if (nIn1) add(`${nIn1[1]}-in-1`)
  }

  add(capacityIn(text)) // a power bank is bought by capacity first
  add(maxWatt(text))
  add(lengthIn(text))
  add(portsIn(text))
  return out
}

/** ≤3 short, meaningful variant chips derived from name+spec, e.g. ['Type-C → Type-C', '60W', '1 m'] or ['+ Lightning cable', '20W', '2 USB']. */
export function variantOf(item: ShopItem): string[] {
  const code = item.item_code || ''
  const dn = (item.display_name || '').trim()
  const spec = item.spec || ''
  const primary = dn && dn.toUpperCase() !== code.toUpperCase() ? dn : spec.split('\n')[0]
  const text = primary.replace(BRAND_TAG, ' ')
  let chips = chipsFrom(text)
  // the name says nothing measurable ("Bluetooth 5.3"): read the whole spec sheet
  if (!chips.length && spec.trim() && spec.trim() !== primary.trim()) chips = chipsFrom(`${dn}\n${spec}`.replace(BRAND_TAG, ' '))
  if (!chips.length) {
    if (/braided/i.test(text)) chips.push('Braided')
    else if (/alumin(?:i)?um/i.test(text)) chips.push('Aluminium')
  }
  chips = chips.slice(0, 3)
  // "UK20" vs "UK20 (New)": the code itself is the only difference
  if (/\(new\)/i.test(code) || /\(new\)/i.test(primary)) chips = [...chips.slice(0, 2), 'New version']
  return chips
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
