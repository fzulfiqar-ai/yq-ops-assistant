/** The salesman catalog's filters and sorts (the Filters sheet and the catalog share them). */

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
  clearance: boolean
  best: boolean
}

export const NO_FILTERS: StaffFilters = { inStock: false, clearance: false, best: false }
