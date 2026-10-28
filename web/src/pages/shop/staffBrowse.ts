import type { ShopItem } from '@/lib/shopApi'
import { codeKey } from '@/market/lib/format'
import { NO_FILTERS, STAFF_SORTS, type StaffFilters, type StaffSort } from './staffFilters'

/**
 * The salesman catalog between visits and before its search index is built — pure, so
 * web/scripts/rep_field_test.mjs runs it in plain node.
 *
 *  • Its search, category, filters and sort are remembered for this browser tab (sessionStorage,
 *    pages/sales/repDevice.BROWSE_KEY): Catalog → Orders → Catalog comes back to the same list
 *    instead of the whole catalog. A link that says what to show (Today's price drops,
 *    /shop?f=drops) starts clean, and sign-out clears it with the rest of the rep's things.
 *  • The index (MiniSearch) used to be built in the first render after the catalog arrived, holding
 *    up the first paint. It is now built when the phone is idle (whenIdle); a search typed before it
 *    is ready is a plain filter (plainSearch), so the list never ignores what was typed.
 */

export interface StaffBrowse {
  q: string
  cat: string
  filters: StaffFilters
  sort: StaffSort
}

export function browseRecord(b: StaffBrowse): string {
  return JSON.stringify({ q: b.q, cat: b.cat, filters: b.filters, sort: b.sort })
}

/** What was kept, each field checked on its own: an odd or stale field falls back, the rest stays. */
export function readBrowse(raw: string | null | undefined): StaffBrowse | null {
  let v: unknown
  try {
    v = raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null
  const o = v as Record<string, unknown>
  const f = o.filters && typeof o.filters === 'object' ? (o.filters as Record<string, unknown>) : {}
  const filters = { ...NO_FILTERS }
  for (const k of Object.keys(NO_FILTERS) as (keyof StaffFilters)[]) filters[k] = f[k] === true
  return {
    q: typeof o.q === 'string' ? o.q.slice(0, 200) : '',
    cat: typeof o.cat === 'string' && o.cat ? o.cat : 'All',
    filters,
    sort: STAFF_SORTS.some((s) => s.value === o.sort) ? (o.sort as StaffSort) : 'featured',
  }
}

/** What the catalog opens on: this tab's last browse state — unless the link says what to show. */
export function rememberedFor(search: string, raw: string | null | undefined): StaffBrowse | null {
  return new URLSearchParams(search).get('f') ? null : readBrowse(raw)
}

type IdleWindow = typeof globalThis & {
  requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number
  cancelIdleCallback?: (id: number) => void
}

/**
 * Run `fn` when the phone is idle (requestIdleCallback, at the latest after `timeout` ms), or on the
 * next timer turn where there is none (Safari); the function returned cancels it.
 */
export function whenIdle(fn: () => void, timeout = 600): () => void {
  const w = globalThis as IdleWindow
  if (typeof w.requestIdleCallback === 'function') {
    const id = w.requestIdleCallback(fn, { timeout })
    return () => w.cancelIdleCallback?.(id)
  }
  const t = setTimeout(fn, 1)
  return () => clearTimeout(t)
}

/** Every word of the text in the code (spaces, dashes and case ignored), the name, the spec or the category. */
export function plainSearch(items: readonly ShopItem[], text: string): ShopItem[] {
  const words = text.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return items.slice()
  return items.filter((i) => {
    const code = codeKey(i.item_code)
    const hay = `${i.item_code} ${i.display_name || ''} ${i.spec || ''} ${i.category || ''}`.toLowerCase()
    return words.every((w) => hay.includes(w) || (codeKey(w) !== '' && code.includes(codeKey(w))))
  })
}
