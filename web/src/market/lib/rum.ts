/**
 * One fact the transport records and the vitals beacon reads: WHEN the catalog became available to
 * the app and WHERE it came from. Its own module, with no imports, because marketApi.ts (which knows
 * the source) and vitals.ts (which sends) would otherwise import each other through events.ts.
 *
 * Sources, as they land in shop_events.meta.catalog_src (whitelisted in app/shop.py _META_KEYS):
 *   edge-hit / edge-stale / edge-miss / edge-error-stale / edge-revalidated   the same-origin Worker
 *   api                                                                        the Render origin direct
 *   pre-<any of the above>                                                     catalog-prefetch.js got it first
 *   none                                                                       the visit ended before a catalog
 */
let note: { ms: number; src: string } | null = null

/** Called once per page visit, for the FIRST catalog that arrives; later refetches do not count. */
export function noteCatalog(src: string): void {
  if (note || typeof performance === 'undefined') return
  note = { ms: Math.round(performance.now()), src: src.slice(0, 24) }
}

export function catalogNote(): { ms: number; src: string } | null {
  return note
}
