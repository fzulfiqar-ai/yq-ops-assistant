import type { ShopItem } from '@/lib/shopApi'
import { partitionByAvailability } from './facets'
import { codeKey, isOut } from './format'
import type { SearchIndex } from './search'

/**
 * Turns a pasted WhatsApp-style list into order rows:
 *   "24 x C18, 12 UK15\n tws 6; X26-C"
 * Split on newlines / commas / semicolons; per token accept `24 x C18`, `C18 x 24`, `24 C18`,
 * `C18 24`, or a bare code/name (default quantity). Resolution: exact code (space/dash/case
 * insensitive) → the index's top hit when it clearly wins (≥1.5× the runner-up) → otherwise
 * "pick one" with the top three.
 *
 * The sold-out rule: a sold-out line is NEVER resolved on its own — not from an exact code, not as
 * the clear winner. It is offered as a candidate, after the in-stock alternatives, so the merchant
 * sees the state and chooses; a backorder line must be his decision, never a side effect of Enter
 * or of a pasted list (verified before this: a sold-out best match silently became a backorder).
 */

export interface ParsedRow {
  raw: string
  query: string
  qty: number | null
  item: ShopItem | null
  candidates: ShopItem[]
}

export interface Resolved {
  /** the one line this query means — never a sold-out one */
  item: ShopItem | null
  /** what to offer instead (or as well): available lines first, then sold-out */
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

/** The index hits for a query, best first (AND, then a looser OR pass), as catalog items. */
function rankedHits(query: string, items: ShopItem[], index: SearchIndex): { it: ShopItem; score: number }[] {
  const hits = index.mini.search(query, { prefix: true, fuzzy: 0.2, combineWith: 'AND' })
  const alt = hits.length ? hits : index.mini.search(query, { prefix: true, fuzzy: 0.3, combineWith: 'OR' })
  const byCode = new Map(items.map((i) => [i.item_code, i]))
  return alt.map((h) => ({ it: byCode.get(String(h.id)), score: h.score })).filter((x): x is { it: ShopItem; score: number } => Boolean(x.it))
}

export function resolveQuery(query: string, items: ShopItem[], index: SearchIndex | null): Resolved {
  const key = codeKey(query)
  const exact = items.find((i) => codeKey(i.item_code) === key)
  if (exact && !isOut(exact)) return { item: exact, candidates: [] }
  if (exact) {
    // the code is right but the line is sold out: offer it with its in-stock siblings ahead of it
    const near = index ? rankedHits(query, items, index).map((r) => r.it).filter((i) => i.item_code !== exact.item_code) : []
    return { item: null, candidates: partitionByAvailability([exact, ...near.slice(0, 2)]) }
  }
  if (!index) return { item: null, candidates: [] }
  const ranked = rankedHits(query, items, index)
  if (!ranked.length) return { item: null, candidates: [] }
  const top = ranked[0]
  const clear = ranked.length === 1 || top.score >= ranked[1].score * 1.5
  const candidates = partitionByAvailability(ranked.slice(0, 3).map((r) => r.it))
  if (clear && !isOut(top.it)) return { item: top.it, candidates }
  return { item: null, candidates }
}

export function parseList(text: string, items: ShopItem[], index: SearchIndex | null): ParsedRow[] {
  return splitList(text).map((raw) => {
    const { query, qty } = parseToken(raw)
    const { item, candidates } = resolveQuery(query, items, index)
    return { raw, query, qty, item, candidates }
  })
}
