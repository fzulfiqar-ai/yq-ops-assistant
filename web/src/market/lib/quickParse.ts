import type { ShopItem } from '@/lib/shopApi'
import { codeKey } from './format'
import type { SearchIndex } from './search'

/**
 * Turns a pasted WhatsApp-style list into order rows:
 *   "24 x C18, 12 UK15\n tws 6; X26-C"
 * Split on newlines / commas / semicolons; per token accept `24 x C18`, `C18 x 24`, `24 C18`,
 * `C18 24`, or a bare code/name (default quantity). Resolution: exact code (space/dash/case
 * insensitive) → the index's top hit when it clearly wins (≥1.5× the runner-up) → otherwise
 * "pick one" with the top three.
 */

export interface ParsedRow {
  raw: string
  query: string
  qty: number | null
  item: ShopItem | null
  candidates: ShopItem[]
}

const QTY_FIRST = /^(\d{1,4})\s*(?:x|×|\*|pcs?|pieces?)?\s+(.+)$/i
const QTY_LAST = /^(.+?)\s*(?:x|×|\*|-)?\s*(\d{1,4})\s*(?:pcs?|pieces?)?$/i

export function splitList(text: string): string[] {
  return text
    .split(/[\n,;]+/)
    .map((s) => s.trim().replace(/^[-•*\d]+[.)]\s+/, ''))
    .filter(Boolean)
}

export function parseToken(raw: string): { query: string; qty: number | null } {
  const t = raw.trim()
  let m = t.match(QTY_FIRST)
  if (m) return { query: m[2].trim(), qty: Number(m[1]) }
  m = t.match(QTY_LAST)
  if (m && !/^\d+$/.test(m[1].trim())) {
    // "X26-C" must not be read as "X26 -" qty C; only treat a trailing number as qty when separated by space/x
    const sep = t.slice(m[1].length, t.length - m[2].length)
    if (/[\sx×*]/i.test(sep)) return { query: m[1].trim(), qty: Number(m[2]) }
  }
  return { query: t, qty: null }
}

export function resolveQuery(query: string, items: ShopItem[], index: SearchIndex | null): { item: ShopItem | null; candidates: ShopItem[] } {
  const key = codeKey(query)
  const exact = items.find((i) => codeKey(i.item_code) === key)
  if (exact) return { item: exact, candidates: [] }
  if (!index) return { item: null, candidates: [] }
  const hits = index.mini.search(query, { prefix: true, fuzzy: 0.2, combineWith: 'AND' })
  const alt = hits.length ? hits : index.mini.search(query, { prefix: true, fuzzy: 0.3, combineWith: 'OR' })
  const byCode = new Map(items.map((i) => [i.item_code, i]))
  const ranked = alt.map((h) => ({ it: byCode.get(String(h.id)), score: h.score })).filter((x): x is { it: ShopItem; score: number } => Boolean(x.it))
  if (!ranked.length) return { item: null, candidates: [] }
  if (ranked.length === 1 || ranked[0].score >= ranked[1].score * 1.5) return { item: ranked[0].it, candidates: ranked.slice(0, 3).map((r) => r.it) }
  return { item: null, candidates: ranked.slice(0, 3).map((r) => r.it) }
}

export function parseList(text: string, items: ShopItem[], index: SearchIndex | null): ParsedRow[] {
  return splitList(text).map((raw) => {
    const { query, qty } = parseToken(raw)
    const { item, candidates } = resolveQuery(query, items, index)
    return { raw, query, qty, item, candidates }
  })
}
