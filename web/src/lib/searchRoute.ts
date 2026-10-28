/**
 * Where a ⌘K search result opens (components/CommandPalette.tsx), and how the page it opens reads the name.
 *
 * A salesman result used to open /sales unfiltered: the office searched for a rep and landed on everybody.
 * It now opens /sales?rep=<name>, and Sales selects that rep in its daily chart and marks his bar.
 */

/** The palette's hit, as GET /search answers it (app/reports.search). */
export interface SearchHit { type: 'customer' | 'item' | 'salesman'; label: string; sub?: string }

/** the Sales page's query parameter for one rep */
export const REP_PARAM = 'rep'

export function hitRoute(h: Pick<SearchHit, 'type' | 'label'>): string {
  const q = encodeURIComponent(h.label)
  if (h.type === 'customer') return `/receivables?q=${q}`
  if (h.type === 'item') return `/inventory?q=${q}`
  return `/sales?${REP_PARAM}=${q}`
}

/** The rep a link names, as the page spells him (case and spaces aside); null when the page has no such rep. */
export function matchSalesman(names: readonly string[], wanted: string | null | undefined): string | null {
  const w = String(wanted || '').trim().toLowerCase()
  if (!w) return null
  return names.find((n) => n.trim().toLowerCase() === w) ?? null
}
