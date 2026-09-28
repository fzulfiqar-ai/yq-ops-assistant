/** The salesman catalog's filters and sorts (the Filters sheet and the catalog share them). */

import type { ShopItem } from '@/lib/shopApi'
import { priceDropOf } from './priceDrops'
import { hasBadge, isSoldOut } from './shared'

export type StaffSort = 'featured' | 'best' | 'price_asc' | 'price_desc' | 'newest'

export const STAFF_SORTS: { value: StaffSort; label: string }[] = [
  { value: 'featured', label: 'Shelf order' },
  { value: 'best', label: 'Best selling first' },
  { value: 'price_asc', label: 'Price: low to high' },
  { value: 'price_desc', label: 'Price: high to low' },
  { value: 'newest', label: 'Newest first' },
]

export interface StaffFilters {
  inStock: boolean
  /** a genuine price-book cut (priceDrops.priceDropOf) — Today's "See all" lands on /shop?f=drops */
  drops: boolean
  clearance: boolean
  best: boolean
}

export const NO_FILTERS: StaffFilters = { inStock: false, drops: false, clearance: false, best: false }

/** A line that reads "Clearing line" — never one with a price drop (price drop wins, shared.shownBadges). */
export function isClearing(i: ShopItem): boolean {
  return hasBadge(i, 'clearance') && !hasBadge(i, 'price_drop')
}

/** Every switched-on filter must hold (pure: web/scripts/rep_ui_test.mjs runs it). */
export function passesStaffFilters(i: ShopItem, f: StaffFilters): boolean {
  return (
    (!f.inStock || !isSoldOut(i)) &&
    (!f.drops || priceDropOf(i) != null) &&
    (!f.clearance || isClearing(i)) &&
    (!f.best || hasBadge(i, 'best_seller'))
  )
}
