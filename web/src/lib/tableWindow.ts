/**
 * DataTable's rules for how many rows it mounts and when it starts again (polish stream, 28-Oct-2026).
 *
 * The table mounts a page of rows and grows by a page each time its sentinel (an invisible block over the list's
 * last SENTINEL_LEAD_PX, watched by an IntersectionObserver on the viewport) comes on screen. It used to start again from the
 * first page — and jump its scroll back to the top — whenever the `rows` array changed identity, so a page that
 * rebuilt its rows on every render (Customer orders, on opening or closing an order) threw the office back to
 * the top of the list. Now only what the person changed starts it again: the table's own search, its sort, or a
 * filter the page names through `resetKey` (a status chip). A re-read of the same rows keeps the window.
 */

/** table rows per page (a desktop row is short) */
export const TABLE_PAGE = 150
/** phone cards per page (a card is the height of three rows) */
export const CARD_PAGE = 60
/** how far before the end of the list the sentinel starts the next page, so the rows are there before they are
 *  needed; the sentinel SPANS that distance, so a jump straight to the bottom still meets it */
export const SENTINEL_LEAD_PX = 600

export interface SortState { key: string; dir: 'asc' | 'desc' }
export interface WindowState { key: string; limit: number }

/** What the window belongs to: a change of any part starts it again at one page. */
export function windowKey(query: string, sort: SortState | null, resetKey?: string | number | null): string {
  return JSON.stringify([query, sort ? `${sort.key}:${sort.dir}` : '', resetKey ?? ''])
}

/** The rows to mount for `key`: the grown window while the key is the same, else one page. */
export function limitFor(state: WindowState, key: string, page: number): number {
  return state.key === key ? state.limit : page
}

/** One page more, never past the last row (and never shrinking). */
export function grownLimit(limit: number, total: number, page: number): number {
  return limit >= total ? limit : Math.min(total, limit + page)
}

export function hasMore(limit: number, total: number): boolean {
  return limit < total
}

/**
 * Which edges of a sideways scroller get the soft fade that says "more this way": none when the content fits.
 * A pixel of slack absorbs the fractions a zoomed screen reports.
 */
export function edgeFade(m: { scrollLeft: number; clientWidth: number; scrollWidth: number }): { left: boolean; right: boolean } {
  const SLACK = 1
  const overflow = m.scrollWidth - m.clientWidth
  if (overflow <= SLACK) return { left: false, right: false }
  return { left: m.scrollLeft > SLACK, right: m.scrollLeft < overflow - SLACK }
}
