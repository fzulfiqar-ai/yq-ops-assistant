/**
 * Is there a page of the shop behind this one in this tab? (28-Oct-2026, final review)
 *
 * The header's Back steps back only then; otherwise it goes Home, never out of the shop to the
 * WhatsApp chat or the search page the merchant came from. react-router writes `idx` into
 * history.state: 0 on the entry the tab arrived on, +1 per push, unchanged by a replace. A filter
 * chip or the search box REPLACES the entry (a new `key`, the same idx), so the old
 * `location.key === 'default'` test let Back leave the shop after a single filter tap.
 */
export function canGoBack(state: unknown): boolean {
  const idx = state && typeof state === 'object' ? (state as { idx?: unknown }).idx : undefined
  return typeof idx === 'number' && idx > 0
}
