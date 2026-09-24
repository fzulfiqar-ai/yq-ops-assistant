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
 *
 * And Enter never substitutes: a typed code that IS a sold-out SKU resolves to nothing else — the
 * SKU is shown as "Sold out" with "Tell me when back", the in-stock lines are offered as
 * suggestions the merchant picks himself (resolveQuick). Before this, "UK05⏎" locked UK15 and
 * "C03⏎" put a headset on a cable order: 45 of the 54 sold-out codes keyed a different SKU.
 */

export interface ParsedRow {
  raw: string
  query: string
  qty: number | null
  item: ShopItem | null
  candidates: ShopItem[]
  /** the SKU whose code the token IS, sold out or not (null = no exact code) */
  exact: ShopItem | null
}

export interface Resolved {
  /** the one line this query means — never a sold-out one */
  item: ShopItem | null
  /** what to offer instead (or as well): available lines first, then sold-out */
  candidates: ShopItem[]
  /** the SKU whose code the query IS, whatever its stock (null = no exact code) */
  exact: ShopItem | null
}

/** One Quick-order row's reading of what was typed (resolveQuick). */
export interface QuickResolution {
  /** lock the row at once: the typed code is an available SKU */
  lock: ShopItem | null
  /**
   * what Enter may take: the query's best match — the first hit the merchant sees — and only when
   * it is available. Null when the typed code is a sold-out SKU, whatever else the index found.
   */
  best: ShopItem | null
  /** the typed code IS a sold-out SKU: shown "Sold out" with "Tell me when back", never added */
  exactOut: ShopItem | null
  /** the rows to offer, available first (with exactOut: the alternatives, without it) */
  suggestions: ShopItem[]
  candidates: ShopItem[]
}

const QTY_FIRST = /^(\d{1,4})\s*(?:x|×|\*|pcs?|pieces?)?\s+(.+)$/i
const QTY_LAST = /^(.+?)\s*(?:x|×|\*|-)?\s*(\d{1,4})\s*(?:pcs?|pieces?)?$/i
/** how many index hits a Quick-order row lists under the input */
const SUGGEST_MAX = 6

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
  const exact = items.find((i) => codeKey(i.item_code) === key) || null
  if (exact && !isOut(exact)) return { item: exact, candidates: [], exact }
  if (exact) {
    // the code is right but the line is sold out: offer it with its in-stock siblings ahead of it
    const near = index ? rankedHits(query, items, index).map((r) => r.it).filter((i) => i.item_code !== exact.item_code) : []
    return { item: null, candidates: partitionByAvailability([exact, ...near.slice(0, 2)]), exact }
  }
  if (!index) return { item: null, candidates: [], exact: null }
  const ranked = rankedHits(query, items, index)
  if (!ranked.length) return { item: null, candidates: [], exact: null }
  const top = ranked[0]
  const clear = ranked.length === 1 || top.score >= ranked[1].score * 1.5
  const candidates = partitionByAvailability(ranked.slice(0, 3).map((r) => r.it))
  if (clear && !isOut(top.it)) return { item: top.it, candidates, exact: null }
  return { item: null, candidates, exact: null }
}

/**
 * What a Quick-order row does with what was typed — the one rule for typing, Enter and the list
 * under the input, shared with the node test (web/scripts/soldout_order_test.mjs):
 *   · the typed code is an available SKU  → `lock` it at once (no Enter needed)
 *   · the typed code is a sold-out SKU    → `exactOut`; `best` is null, so Enter adds NOTHING;
 *                                            the in-stock lines are `suggestions` to pick by hand
 *   · anything else                        → `best` = the first hit as the merchant sees it, only
 *                                            when available (Enter takes it); `suggestions` are
 *                                            the hits, available first
 */
export function resolveQuick(query: string, items: ShopItem[], index: SearchIndex | null): QuickResolution {
  const { item, candidates, exact } = resolveQuery(query, items, index)
  if (exact && !isOut(exact)) return { lock: exact, best: exact, exactOut: null, suggestions: [], candidates: [] }
  const exactOut = exact && isOut(exact) ? exact : null
  const byCode = new Map(items.map((i) => [i.item_code, i]))
  const hits = index ? index.mini.search(query, { prefix: true, fuzzy: 0.2 }).slice(0, SUGGEST_MAX).map((h) => byCode.get(String(h.id))).filter((x): x is ShopItem => Boolean(x)) : []
  const pool = hits.length ? hits : candidates
  const suggestions = partitionByAvailability(exactOut ? pool.filter((x) => x.item_code !== exactOut.item_code) : pool)
  // Enter's target is the query's best match BEFORE the partition (what the merchant sees first
  // would otherwise be the first available near-miss) — and only when it is available; a typed
  // code that is a sold-out SKU has no target at all: Enter must never substitute another SKU
  const top = pool[0] || null
  const best = exactOut ? null : item || (top && !isOut(top) ? top : null)
  return { lock: null, best, exactOut, suggestions, candidates }
}

export function parseList(text: string, items: ShopItem[], index: SearchIndex | null): ParsedRow[] {
  return splitList(text).map((raw) => {
    const { query, qty } = parseToken(raw)
    const { item, candidates, exact } = resolveQuery(query, items, index)
    return { raw, query, qty, item, candidates, exact }
  })
}
