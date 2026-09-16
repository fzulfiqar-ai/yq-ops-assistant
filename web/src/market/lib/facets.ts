import type { ShopItem } from '@/lib/shopApi'
import { savedStore } from '../store/saved'

/**
 * Sub-facets inferred from the product text — no attribute model in the backend, and the
 * merchant's language is "type c 100W" not "connector=USB-C". A facet is only shown when it
 * splits the list into at least two groups of two, so a category never grows a useless row.
 * All state lives in the URL (?conn=typec&watt=100).
 */

export interface FacetValue {
  key: string
  label: string
  test: (t: string) => boolean
}
export interface FacetDef {
  key: string
  label: string
  values: FacetValue[]
}
export interface FacetGroup extends FacetDef {
  values: (FacetValue & { count: number })[]
}

const re = (r: RegExp) => (t: string) => r.test(t)
const watt = (t: string): number | null => {
  const m = t.match(/(\d{2,3})\s?w\b/i)
  return m ? Number(m[1]) : null
}
const mah = (t: string): number | null => {
  const m = t.match(/(\d{1,2})[,.]?(\d{3})\s?mah/i) || t.match(/(\d{4,5})\s?mah/i)
  if (!m) return null
  return Number(m[2] ? `${m[1]}${m[2]}` : m[1])
}

const CONNECTOR: FacetDef = {
  key: 'conn',
  label: 'Connector',
  values: [
    { key: 'typec', label: 'Type-C', test: re(/type[\s-]?c|usb[\s-]?c|\bc to c\b|\bc-c\b|\bpd\b/i) },
    { key: 'lightning', label: 'Lightning', test: re(/lightning|iphone|\bios\b|ipad/i) },
    { key: 'micro', label: 'Micro USB', test: re(/micro|\bv8\b/i) },
    { key: 'usba', label: 'USB-A', test: re(/usb[\s-]?a\b|\busb\b(?![\s-]?c)/i) },
  ],
}
const PAIRING: FacetDef = {
  key: 'pair',
  label: 'Cable type',
  values: [
    { key: 'ctoc', label: 'C to C', test: re(/\bc to c\b|c-c|type[\s-]?c to type[\s-]?c|c2c/i) },
    { key: 'ctol', label: 'C to Lightning', test: re(/(c|type[\s-]?c) to (l|lightning|iphone)|c-l\b|\bcl\b/i) },
    { key: 'utoc', label: 'USB to C', test: re(/usb to (c|type)|\busb-c\b(?! to)/i) },
    { key: 'utol', label: 'USB to Lightning', test: re(/usb to (l|lightning|iphone)/i) },
    { key: '3in1', label: '3-in-1', test: re(/3[\s-]?in[\s-]?1|three in one/i) },
  ],
}
const LENGTH: FacetDef = {
  key: 'len',
  label: 'Length',
  values: [
    { key: '1m', label: '1 m', test: re(/\b1(\.0)?\s?(m|mtr|meter)\b/i) },
    { key: '2m', label: '2 m', test: re(/\b2(\.0)?\s?(m|mtr|meter)\b/i) },
    { key: '3m', label: '3 m+', test: re(/\b[3-9](\.0)?\s?(m|mtr|meter)\b/i) },
  ],
}
const WATT: FacetDef = {
  key: 'watt',
  label: 'Power',
  values: [
    { key: 'w20', label: 'Up to 20W', test: (t) => (watt(t) ?? -1) > 0 && (watt(t) as number) <= 20 },
    { key: 'w65', label: '25–65W', test: (t) => (watt(t) ?? -1) > 20 && (watt(t) as number) <= 65 },
    { key: 'w100', label: '100W+', test: (t) => (watt(t) ?? -1) > 65 },
  ],
}
const PORTS: FacetDef = {
  key: 'ports',
  label: 'Ports',
  values: [
    { key: 'dual', label: 'Dual port', test: re(/dual|2 ?port|\+|twin|double/i) },
    { key: 'typec', label: 'Type-C port', test: re(/type[\s-]?c|usb[\s-]?c|\bpd\b/i) },
    { key: 'usba', label: 'USB-A port', test: re(/\busb\b(?![\s-]?c)|usb[\s-]?a\b|\bqc\b/i) },
  ],
}
const WIRELESS: FacetDef = {
  key: 'wl',
  label: 'Type',
  values: [
    { key: 'wireless', label: 'Wireless', test: re(/wireless|\bqi\b|magsafe|magnetic/i) },
    { key: 'wired', label: 'Wired', test: (t) => !/wireless|\bqi\b|magsafe|magnetic/i.test(t) },
  ],
}
const PLUG: FacetDef = {
  key: 'plug',
  label: 'Plug',
  values: [
    { key: 'uk', label: 'UK plug', test: re(/\buk\b|3[\s-]?pin/i) },
    { key: 'eu', label: 'EU plug', test: re(/\beu\b|2[\s-]?pin/i) },
  ],
}
const CAPACITY: FacetDef = {
  key: 'mah',
  label: 'Capacity',
  values: [
    { key: 'c5', label: 'Up to 5,000 mAh', test: (t) => (mah(t) ?? -1) > 0 && (mah(t) as number) <= 5000 },
    { key: 'c10', label: '10,000 mAh', test: (t) => (mah(t) ?? -1) > 5000 && (mah(t) as number) <= 12000 },
    { key: 'c20', label: '20,000 mAh+', test: (t) => (mah(t) ?? -1) > 12000 },
  ],
}
const AUDIO_CONN: FacetDef = {
  key: 'conn',
  label: 'Connector',
  values: [
    { key: 'typec', label: 'Type-C', test: re(/type[\s-]?c|usb[\s-]?c/i) },
    { key: 'jack', label: '3.5 mm', test: re(/3\.5|aux|jack/i) },
    { key: 'lightning', label: 'Lightning', test: re(/lightning|iphone/i) },
  ],
}
const BT_STYLE: FacetDef = {
  key: 'style',
  label: 'Style',
  values: [
    { key: 'tws', label: 'TWS earbuds', test: re(/tws|earbud|\bpods?\b|airpod|true wireless/i) },
    { key: 'neck', label: 'Neckband', test: re(/neck|sport|band/i) },
    { key: 'mono', label: 'Mono / handsfree', test: re(/mono|single|hands ?free|\bhfs?\b|earpiece/i) },
    { key: 'over', label: 'Headphones', test: re(/headphone|over[\s-]?ear|on[\s-]?ear/i) },
  ],
}
const FEATURES: FacetDef = {
  key: 'feat',
  label: 'Features',
  values: [
    { key: 'mic', label: 'With mic', test: re(/\bmic\b|microphone/i) },
    { key: 'fm', label: 'FM', test: re(/\bfm\b|radio/i) },
    { key: 'holder', label: 'Holder / mount', test: re(/holder|mount|stand/i) },
    { key: 'aux', label: 'AUX', test: re(/\baux\b|3\.5/i) },
  ],
}

const BY_CATEGORY: Record<string, FacetDef[]> = {
  CABLE: [CONNECTOR, PAIRING, LENGTH, WATT],
  CHARGER: [PORTS, WATT, WIRELESS, PLUG],
  'CAR CHARGER': [PORTS, WATT],
  'POWER BANK': [CAPACITY, WATT, WIRELESS],
  EARPHONE: [AUDIO_CONN, FEATURES],
  'BLUETOOTH HEADSET': [BT_STYLE, FEATURES],
  'BLUETOOTH SPEAKER': [FEATURES],
  'CAR ACCESSORIES': [FEATURES],
}

export function itemText(it: ShopItem): string {
  return `${it.item_code} ${it.display_name || ''} ${it.spec || ''}`
}

/** The facets worth showing for this category over these items (≥2 values with ≥2 items each). */
export function facetsFor(category: string, items: ShopItem[]): FacetGroup[] {
  const defs = BY_CATEGORY[category] || []
  const texts = items.map(itemText)
  const out: FacetGroup[] = []
  for (const def of defs) {
    const values = def.values
      .map((v) => ({ ...v, count: texts.filter((t) => v.test(t)).length }))
      .filter((v) => v.count >= 2)
    if (values.length >= 2) out.push({ ...def, values })
  }
  return out
}

export type FacetSelection = Record<string, string>

/** AND across facets, one value per facet. */
export function matchesFacets(it: ShopItem, groups: FacetGroup[], sel: FacetSelection): boolean {
  const t = itemText(it)
  for (const g of groups) {
    const want = sel[g.key]
    if (!want) continue
    const v = g.values.find((x) => x.key === want)
    if (v && !v.test(t)) return false
  }
  return true
}

export function readFacets(params: URLSearchParams, groups: FacetGroup[]): FacetSelection {
  const sel: FacetSelection = {}
  for (const g of groups) {
    const v = params.get(g.key)
    if (v && g.values.some((x) => x.key === v)) sel[g.key] = v
  }
  return sel
}

/* ───────────────────────── sort & filter ───────────────────────── */

export type SortMode = 'shelf' | 'popular' | 'price_asc' | 'price_desc'
export type QuickFilter = 'instock' | 'offers' | 'new' | 'clearance' | 'drops' | 'saved'

function popularity(it: ShopItem): number {
  let s = 0
  if ((it.badges || []).includes('best_seller')) s += 100
  if ((it.badges || []).includes('trending')) s += 50
  const m = (it.social_proof || '').match(/(\d+)/)
  if (m) s += Number(m[1])
  return s
}

export function sortItems(items: ShopItem[], mode: SortMode): ShopItem[] {
  const arr = items.slice()
  const out = (i: ShopItem) => (i.stock_status === 'out_of_stock' ? 1 : 0)
  switch (mode) {
    case 'popular':
      return arr.sort((a, b) => out(a) - out(b) || popularity(b) - popularity(a))
    case 'price_asc':
      return arr.sort((a, b) => out(a) - out(b) || (Number(a.price_bhd) || Infinity) - (Number(b.price_bhd) || Infinity))
    case 'price_desc':
      return arr.sort((a, b) => out(a) - out(b) || (Number(b.price_bhd) || 0) - (Number(a.price_bhd) || 0))
    default:
      return arr
  }
}

export function applyQuickFilters(items: ShopItem[], filters: Set<QuickFilter>): ShopItem[] {
  let r = items
  if (filters.has('instock')) r = r.filter((i) => i.stock_status !== 'out_of_stock')
  if (filters.has('offers')) r = r.filter((i) => (i.badges || []).includes('on_offer') || i.compare_at_bhd != null)
  if (filters.has('new')) r = r.filter((i) => (i.badges || []).includes('new'))
  if (filters.has('clearance')) r = r.filter((i) => (i.badges || []).includes('clearance'))
  if (filters.has('drops')) r = r.filter((i) => (i.badges || []).includes('price_drop') || i.was_bhd != null)
  if (filters.has('saved')) {
    const saved = new Set(savedStore.get())
    r = r.filter((i) => saved.has(i.item_code))
  }
  return r
}

export function parseFilters(s: string | null): Set<QuickFilter> {
  const set = new Set<QuickFilter>()
  for (const p of (s || '').split(',')) if (p === 'instock' || p === 'offers' || p === 'new' || p === 'clearance' || p === 'drops' || p === 'saved') set.add(p)
  return set
}
