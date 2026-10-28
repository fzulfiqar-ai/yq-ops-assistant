import type { StaffCustomer } from '@/lib/shopApi'

/**
 * What the rep's app keeps on the phone between visits, and what leaves with him at sign-out —
 * pure, no runtime imports, so web/scripts/rep_field_test.mjs runs it in plain node. lib.ts does
 * the storage calls.
 *
 *  • The shop he is ordering for lives in localStorage for 12 hours from the pick. It used to be
 *    sessionStorage, which a phone throws away when it closes the app during a WhatsApp detour: the
 *    rep came back to no shop and what looked like an empty cart (the cart was there, under the
 *    shop's key). Twelve hours covers the working day; the next morning starts with no shop.
 *  • A shared phone: signing out clears every rep cart (one per shop, plus "no shop yet"), the pick
 *    and the catalog's remembered search, so the next person starts clean. A customer's cart from a
 *    share link and the device's own choices (list or grid, the sidebar) are not the rep's and stay.
 */

/** localStorage: the shop being ordered for, as { c: the shop, at: when it was picked }. */
export const SELECTION_KEY = 'yq-staff-customer'
/** sessionStorage: the catalog's search, category, filters and sort for this browser tab. */
export const BROWSE_KEY = 'yq-staff-catalog-browse'
/** The rep's carts: `yq-shop-cart:staff` (no shop yet) and `yq-shop-cart:staff:<book key>` (lib/cart). */
const REP_CART = /^yq-shop-cart:staff(?::|$)/

export const SELECTION_TTL_MS = 12 * 60 * 60_000

export interface Selection {
  c: StaffCustomer
  /** when it was picked (epoch ms) */
  at: number
}

/** A shop the catalog can order for: a book key, a name or a shop name. */
function asCustomer(v: unknown): StaffCustomer | null {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null
  const c = v as StaffCustomer
  const has = (s: unknown) => typeof s === 'string' && s.trim() !== ''
  return has(c.key) || has(c.name) || has(c.shop) ? c : null
}

function parse(raw: string | null | undefined): unknown {
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

/** The text stored for a pick made at `now`. */
export function selectionRecord(c: StaffCustomer, now: number): string {
  return JSON.stringify({ c, at: now })
}

/**
 * The stored pick while it is fresh: younger than 12 hours. A record without a time, without a shop,
 * or dated far ahead (a clock that was wrong when it was written) is no pick.
 */
export function readSelection(raw: string | null | undefined, now: number): Selection | null {
  const v = parse(raw) as { c?: unknown; at?: unknown } | null
  if (!v || typeof v !== 'object') return null
  const c = asCustomer(v.c)
  const at = v.at
  if (!c || typeof at !== 'number' || !Number.isFinite(at)) return null
  return Math.abs(now - at) < SELECTION_TTL_MS ? { c, at } : null
}

/** A pick stored before this release (sessionStorage held the bare shop): read once, then moved. */
export function legacySelection(raw: string | null | undefined): StaffCustomer | null {
  return asCustomer(parse(raw))
}

/** The keys of one storage area that leave with the rep at sign-out. */
export function repDeviceKeys(keys: readonly (string | null | undefined)[]): string[] {
  return keys.filter((k): k is string => typeof k === 'string' && (REP_CART.test(k) || k === SELECTION_KEY || k === BROWSE_KEY))
}

/** The orders in progress on this phone (rep carts with lines), for the sign-out question. */
export function openRepOrders(entries: readonly (readonly [string, string | null])[]): { orders: number; lines: number } {
  let orders = 0
  let lines = 0
  for (const [key, raw] of entries) {
    if (!REP_CART.test(key)) continue
    const v = parse(raw)
    const n = Array.isArray(v) ? v.filter((l) => l && typeof l === 'object' && Number((l as { qty?: unknown }).qty) > 0).length : 0
    if (n > 0) {
      orders += 1
      lines += n
    }
  }
  return { orders, lines }
}
