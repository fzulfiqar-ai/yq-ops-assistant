import { bucketFromParam, VISIBLE_STATUSES, type VisibleStatus } from '@/pages/shop-ops/heart'
import { num } from '@/lib/format'

/**
 * Customer orders, read a page at a time (polish stream, 28-Oct-2026).
 *
 * The list asked GET /shop/orders for its first 100 orders and stopped there, while its chip said "All 342".
 * The API pages (limit, at most 200, and offset, newest first — app/shop.list_orders) and answers the exact
 * `count` of the filtered list, so the page now reads 100 at a time with Load more and says how many it holds.
 */

/** orders per read */
export const ORDER_PAGE = 100

export interface OrderPage<T> { orders: T[]; count?: number | null }

/**
 * The offset of the next read, or undefined when there is no more: every order of the count is read, or the
 * last read came back short (the list ended, whatever the count said a moment before). An answer without a
 * count (an older API) may have more after a full page.
 */
export function nextOrderOffset(pages: OrderPage<unknown>[], page: number = ORDER_PAGE): number | undefined {
  if (!pages.length) return undefined
  const last = pages[pages.length - 1]
  const read = pages.reduce((n, p) => n + (p.orders?.length ?? 0), 0)
  if ((last.orders?.length ?? 0) < page) return undefined
  if (typeof last.count === 'number' && last.count > 0 && read >= last.count) return undefined
  if (last.count === 0) return undefined
  return read
}

/**
 * The pages as one list, each order once. The list is newest first: an order placed between two reads pushes
 * the older ones down a place, so the next page starts with the last order of the page before.
 */
export function flattenOrders<T extends { id: number }>(pages: OrderPage<T>[]): T[] {
  const seen = new Set<number>()
  const out: T[] = []
  for (const p of pages) {
    for (const o of p.orders || []) {
      if (seen.has(o.id)) continue
      seen.add(o.id)
      out.push(o)
    }
  }
  return out
}

/** "Latest 100 of 342 orders" and how many the next tap reads; null once every order is on the page. */
export function listFooter(loaded: number, total: number, page: number = ORDER_PAGE, what = 'orders'): { text: string; more: number } | null {
  if (!(total > loaded)) return null
  return { text: `Latest ${num(loaded)} of ${num(total)} ${what}`, more: Math.min(page, total - loaded) }
}

// ── the rep's stage tab, kept for this browser tab ───────────────────────────────────────────────────

/** sessionStorage: one value per browser tab, gone when the tab closes */
export const STAGE_KEY = 'yq-orders-stage'

type KeyStore = Pick<Storage, 'getItem' | 'setItem'>

/** A link's ?bucket= wins (Today and Customers open a stage), else this tab's last stage, else Received. */
export function initialStage(param: string | null | undefined, stored: string | null | undefined): VisibleStatus {
  if (param && param.trim()) return bucketFromParam(param)
  const s = String(stored || '')
  return (VISIBLE_STATUSES as readonly string[]).includes(s) ? (s as VisibleStatus) : 'new'
}

/** null when nothing is kept, or the storage is blocked (a private window, a locked-down browser) */
export function readStage(store: KeyStore | null | undefined): string | null {
  try {
    return store?.getItem(STAGE_KEY) ?? null
  } catch {
    return null
  }
}

export function saveStage(store: KeyStore | null | undefined, stage: VisibleStatus): void {
  try {
    store?.setItem(STAGE_KEY, stage)
  } catch {
    /* a blocked storage: the tab still works, it just starts at Received next time */
  }
}

/** This tab's sessionStorage, or null where reading it throws. */
export function tabStore(): KeyStore | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}
