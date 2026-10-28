/**
 * Which face a page's panel shows for one react-query read — the honest-data rule of the office pages.
 *
 * Before it, several pages drew their "nothing here" line from `data?.rows || []`: while the first read was
 * still under way, and again after it failed, the Orders page said "Everything ordered has been received",
 * Shop orders "No orders yet" and Stock moves "No transfers in this window". A failure must never read as an
 * empty answer, and neither may a page that is still loading:
 *
 *   loading — nothing read yet (the skeleton)
 *   error   — the read failed and there is nothing to show (LoadError, with Retry)
 *   empty   — the answer came back and it is empty (the page's own "nothing here" line)
 *   ready   — there is something to show
 *
 * A failed RE-read keeps the figures already on screen (react-query keeps `data` when a refetch fails):
 * they are still the last answer the server gave; `refreshFailed` lets a page say so beside them.
 */
export type PanelState = 'loading' | 'error' | 'empty' | 'ready'

/** The minimum of a react-query result this rule reads. */
export interface ReadLike<T> {
  data?: T | undefined
  isError?: boolean
}

/** No answer, or an empty list. An object is not empty unless the page says what empty means for it. */
export function isEmptyData(d: unknown): boolean {
  if (d === null || d === undefined) return true
  return Array.isArray(d) && d.length === 0
}

export function panelState<T>(q: ReadLike<T>, isEmpty: (d: T) => boolean = isEmptyData): PanelState {
  // react-query never stores `undefined` as an answer, so a defined `data` is a real one (null included)
  if (q.data !== undefined) return isEmpty(q.data as T) ? 'empty' : 'ready'
  return q.isError ? 'error' : 'loading'
}

/** The figures on screen are an earlier answer: the latest re-read failed. */
export function refreshFailed(q: ReadLike<unknown>): boolean {
  return Boolean(q.isError) && q.data !== undefined
}
