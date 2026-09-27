import type { ShopItem } from '@/lib/shopApi'
import { partitionByAvailability } from './facets'
import { codeKey, isOut } from './format'
import { queryText, westernDigits, type SearchIndex } from './search'

/**
 * Turns a pasted WhatsApp-style list into order rows:
 *   "24 x C18, 12 UK15\n tws 6; X26-C"
 * Split on newlines / commas / semicolons; per token accept `24 x C18`, `C18 x 24`, `24 C18`,
 * `C18 24`, or a bare code/name (default quantity). Resolution: exact code (space/dash/case
 * insensitive) → the index's top hit when it clearly wins (≥1.5× the runner-up) → otherwise
 * "pick one" with the top three.
 *
 * Arabic lists (R5) read the same way: Arabic-Indic digits (٢٤ → 24), the Arabic comma «،» and
 * semicolon «؛» between lines, the direction marks WhatsApp adds dropped, and the unit words the
 * trade texts — «حبة» and «قطعة» are pieces, «درزن» is a dozen (×12): "٢ درزن C18" is 24 × C18.
 * A name typed in Arabic is read into the catalog's English by lib/search.ts queryText().
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

/** unit words for pieces (English, and the Arabic «حبة» / «قطعة» with their plurals and ه spellings) */
const PIECES = String.raw`pcs?|pieces?|حب(?:ة|ه|ات)|قطع(?:ة|ه)?`
/** a dozen: «درزن» (and «دزن», «دزينة») — the quantity is ×12 */
const DOZEN = String.raw`درزن|دزن|دزين(?:ة|ه)`
const DOZEN_RE = new RegExp(`^(?:${DOZEN})$`)
const QTY_FIRST = new RegExp(String.raw`^(\d{1,4})\s*(x|×|\*|${PIECES}|${DOZEN})?\s+(.+)$`, 'i')
/** query · the separator (kept, see parseToken) · quantity · unit */
const QTY_LAST = new RegExp(String.raw`^(.+?)(\s*(?:x|×|\*|-)?\s*)(\d{1,4})\s*(${PIECES}|${DOZEN})?$`, 'i')
/** how many index hits a Quick-order row lists under the input */
const SUGGEST_MAX = 6
/** the direction marks and isolates a line copied out of an Arabic WhatsApp chat carries */
const MARKS = /[\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069]/g

export function splitList(text: string): string[] {
  return westernDigits(text.replace(MARKS, ''))
    .split(/[\n,;\u060c\u061b]+/)
    .map((s) => s.trim().replace(/^[-•*\d]+[.)]\s+/, ''))
    .filter(Boolean)
}

const qtyOf = (n: string, unit: string | undefined) => Number(n) * (unit && DOZEN_RE.test(unit) ? 12 : 1)

export function parseToken(raw: string): { query: string; qty: number | null } {
  const t = westernDigits(raw.replace(MARKS, '')).trim()
  let m = t.match(QTY_FIRST)
  if (m) return { query: m[3].trim(), qty: qtyOf(m[1], m[2]) }
  m = t.match(QTY_LAST)
  if (m && !/^\d+$/.test(m[1].trim())) {
    // "X26-C" must not be read as "X26 -" qty C; only treat a trailing number as qty when separated by space/x
    if (/[\sx×*]/i.test(m[2])) return { query: m[1].trim(), qty: qtyOf(m[3], m[4]) }
  }
  return { query: t, qty: null }
}

/** The index hits for a query, best first (AND, then a looser OR pass), as catalog items. */
function rankedHits(query: string, items: ShopItem[], index: SearchIndex): { it: ShopItem; score: number }[] {
  const q = queryText(query)
  if (!q.trim()) return []
  const hits = index.mini.search(q, { prefix: true, fuzzy: 0.2, combineWith: 'AND' })
  const alt = hits.length ? hits : index.mini.search(q, { prefix: true, fuzzy: 0.3, combineWith: 'OR' })
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
  const asked = queryText(query)
  const hits = index && asked.trim() ? index.mini.search(asked, { prefix: true, fuzzy: 0.2 }).slice(0, SUGGEST_MAX).map((h) => byCode.get(String(h.id))).filter((x): x is ShopItem => Boolean(x)) : []
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
