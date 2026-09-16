import MiniSearch from 'minisearch'
import type { ShopItem } from '@/lib/shopApi'

/**
 * Client-side search over the ~180-item catalog (plan §Q). Merchants type "type c", "iphone
 * cable", "20w", "tws" — so a synonym field is built INTO each document at index time and the
 * query is expanded for the highest-value terms, then MiniSearch does prefix + fuzzy matching.
 * Ranking: score → in stock first → best seller/trending → catalog (shelf) order. ~20 ms to build
 * on a mid-range phone; the index is rebuilt whenever the payload changes and never serialized.
 */

const SYNONYMS: Record<string, string[]> = {
  'usb-c': ['type c', 'typec', 'type-c', 'usb c', 'usbc', 'c type', 'ctype'],
  lightning: ['iphone', 'apple', 'ios', 'ipad'],
  micro: ['micro usb', 'microusb', 'android old', 'v8'],
  earbuds: ['tws', 'airpods', 'buds', 'true wireless', 'earpods', 'pods'],
  'bluetooth headset': ['bt headset', 'handsfree', 'hands free', 'hfs', 'earpiece'],
  'fast charge': ['20w', '18w', '25w', '30w', '33w', '45w', '65w', 'pd', 'qc', 'quick charge', 'fast charging', 'super fast'],
  charger: ['adapter', 'adaptor', 'plug', 'wall charger', 'travel charger', 'home charger'],
  'car charger': ['car', 'cigarette', 'lighter', 'vehicle'],
  cable: ['wire', 'lead', 'data cable', 'charging cable', 'cord'],
  'power bank': ['powerbank', 'battery', 'portable charger', 'mah'],
  speaker: ['bluetooth speaker', 'bt speaker', 'sound', 'music box'],
  aux: ['3.5', '3.5mm', 'audio jack', 'headphone jack'],
  magnetic: ['magsafe', 'magnet', 'wireless charging', 'qi'],
  holder: ['mount', 'stand', 'car holder', 'phone holder'],
  earphone: ['wired earphone', 'headphone', 'handsfree wired', 'earphones'],
}

/** canonical term → every spelling that should hit it (both directions) */
const EXPAND: Map<string, string[]> = (() => {
  const m = new Map<string, string[]>()
  for (const [canon, alts] of Object.entries(SYNONYMS)) {
    const all = [canon, ...alts]
    for (const a of all) m.set(a.toLowerCase(), all)
  }
  return m
})()

const CATEGORY_WORDS: Record<string, string> = {
  CABLE: 'cable wire lead',
  CHARGER: 'charger adapter plug fast charge',
  'CAR CHARGER': 'car charger vehicle cigarette',
  'POWER BANK': 'power bank powerbank battery portable',
  EARPHONE: 'earphone wired headphone handsfree',
  'BLUETOOTH HEADSET': 'bluetooth headset earbuds tws wireless',
  'BLUETOOTH SPEAKER': 'bluetooth speaker sound music',
  'CAR ACCESSORIES': 'car holder mount accessory',
}

interface Doc {
  id: string
  code: string
  name: string
  spec: string
  category: string
  brand: string
  syn: string
}

function tokenize(s: string): string[] {
  return s
    .toLowerCase()
    .split(/[^a-z0-9+.]+/)
    .filter(Boolean)
}

function synonymsFor(text: string): string {
  const t = text.toLowerCase()
  const out = new Set<string>()
  for (const [alt, all] of EXPAND) {
    if (t.includes(alt)) for (const a of all) out.add(a)
  }
  return Array.from(out).join(' ')
}

export interface SearchIndex {
  mini: MiniSearch<Doc>
  order: Map<string, number>
}

export function buildIndex(items: ShopItem[]): SearchIndex {
  const mini = new MiniSearch<Doc>({
    fields: ['code', 'name', 'spec', 'category', 'brand', 'syn'],
    storeFields: ['code'],
    tokenize,
    searchOptions: {
      prefix: true,
      fuzzy: 0.2,
      combineWith: 'AND',
      boost: { code: 6, name: 3, syn: 2.5, category: 1.5, brand: 1.2, spec: 1 },
    },
  })
  const docs: Doc[] = items.map((it) => {
    const cat = String(it.category || '')
    const blob = `${it.item_code} ${it.display_name || ''} ${it.spec || ''} ${cat} ${CATEGORY_WORDS[cat] || ''}`
    return {
      id: it.item_code,
      code: it.item_code,
      name: it.display_name || '',
      spec: it.spec || '',
      category: `${cat} ${CATEGORY_WORDS[cat] || ''}`,
      brand: it.brand || '',
      syn: synonymsFor(blob),
    }
  })
  mini.addAll(docs)
  return { mini, order: new Map(items.map((i, n) => [i.item_code, n])) }
}

/** Expand the query with canonical synonyms so "type c" also asks for "usb-c". */
function expandQuery(q: string): string {
  const lower = q.toLowerCase().trim()
  const extra = new Set<string>()
  for (const [alt, all] of EXPAND) {
    if (lower.includes(alt)) for (const a of all.slice(0, 3)) extra.add(a)
  }
  return extra.size ? `${lower} ${Array.from(extra).join(' ')}` : lower
}

export function searchItems(index: SearchIndex, items: ShopItem[], q: string, limit = 50): ShopItem[] {
  const query = q.trim()
  if (query.length < 1) return []
  const byCode = new Map(items.map((i) => [i.item_code, i]))
  let hits = index.mini.search(expandQuery(query))
  if (!hits.length) hits = index.mini.search(query, { combineWith: 'OR', fuzzy: 0.3, prefix: true })
  const rank = (it: ShopItem) => {
    const out = it.stock_status === 'out_of_stock' ? 1 : 0
    const hot = (it.badges || []).some((b) => b === 'best_seller' || b === 'trending') ? 0 : 1
    return [out, hot, index.order.get(it.item_code) ?? 0] as const
  }
  const scored = hits
    .map((h) => ({ it: byCode.get(String(h.id)), score: h.score }))
    .filter((x): x is { it: ShopItem; score: number } => Boolean(x.it))
  scored.sort((a, b) => {
    // scores within 15% of each other are "the same" — let stock and velocity break the tie
    if (Math.abs(a.score - b.score) > Math.max(a.score, b.score) * 0.15) return b.score - a.score
    const ra = rank(a.it)
    const rb = rank(b.it)
    return ra[0] - rb[0] || ra[1] - rb[1] || ra[2] - rb[2]
  })
  return scored.slice(0, limit).map((x) => x.it)
}

export function suggest(index: SearchIndex, q: string): string[] {
  try {
    return index.mini
      .autoSuggest(q.trim(), { fuzzy: 0.3, prefix: true })
      .slice(0, 5)
      .map((s) => s.suggestion)
  } catch {
    return []
  }
}
