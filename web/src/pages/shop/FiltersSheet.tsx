import { Check } from 'lucide-react'
import { Sheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { RING } from './shared'
import { NO_FILTERS, STAFF_SORTS, type StaffFilters, type StaffSort } from './staffFilters'

const TOGGLES: { key: keyof StaffFilters; label: string; hint: string }[] = [
  { key: 'inStock', label: 'In stock', hint: 'Hide the sold-out lines' },
  { key: 'clearance', label: 'Clearance', hint: 'Lines marked for clearance' },
  { key: 'best', label: 'Best sellers', hint: 'What shops reorder most' },
]

/**
 * The catalog's "Filters" chip opens this (Sprint 5: one chip row — the categories, then Filters).
 * Each toggle shows how many lines it would leave; one that would leave none is disabled rather
 * than offered as a dead end. Sold-out lines always sit last, whatever the sort.
 */
export function FiltersSheet({
  open,
  onClose,
  filters,
  onFilters,
  sort,
  onSort,
  counts,
}: {
  open: boolean
  onClose: () => void
  filters: StaffFilters
  onFilters: (f: StaffFilters) => void
  sort: StaffSort
  onSort: (s: StaffSort) => void
  counts: Record<keyof StaffFilters, number>
}) {
  const active = TOGGLES.filter((t) => filters[t.key]).length + (sort !== 'featured' ? 1 : 0)
  return (
    <Sheet
      open={open}
      onClose={onClose}
      variant="dialog"
      title="Filters"
      subtitle="Sold-out lines always sit last"
      footer={
        <div className="flex gap-2">
          <button
            type="button"
            disabled={!active}
            onClick={() => {
              onFilters(NO_FILTERS)
              onSort('featured')
            }}
            className={cn('h-12 rounded-xl border border-[#E2DCEA] bg-white px-4 text-[13px] font-semibold text-[#1A1428] hover:bg-[#f7f5fb] disabled:opacity-50', RING)}
          >
            Clear all
          </button>
          <button type="button" onClick={onClose} className={cn('h-12 flex-1 rounded-xl bg-[#6D4091] text-[14px] font-semibold text-white hover:bg-[#5A3478]', RING)}>
            Show products
          </button>
        </div>
      }
    >
      <div className="space-y-5 px-4 py-4 sm:px-5">
        <fieldset>
          <legend className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[#6b6480]">Show only</legend>
          <div className="mt-2 space-y-2">
            {TOGGLES.map((t) => {
              const on = filters[t.key]
              const empty = !on && counts[t.key] === 0
              return (
                <button
                  key={t.key}
                  type="button"
                  aria-pressed={on}
                  disabled={empty}
                  onClick={() => onFilters({ ...filters, [t.key]: !on })}
                  className={cn(
                    'flex min-h-[3.25rem] w-full items-center gap-3 rounded-xl border px-3.5 text-left transition duration-150 ease-out',
                    RING,
                    on ? 'border-[#6D4091] bg-[#EEE8F4]' : 'border-[#E2DCEA] bg-white hover:bg-[#f7f5fb]',
                    empty && 'cursor-not-allowed opacity-50',
                  )}
                >
                  <span className={cn('grid h-5 w-5 shrink-0 place-items-center rounded-md border', on ? 'border-[#6D4091] bg-[#6D4091] text-white' : 'border-[#CFC3DE] bg-white')}>
                    {on && <Check size={13} aria-hidden="true" />}
                  </span>
                  <span className="min-w-0 flex-1 leading-tight">
                    <span className="block text-[13.5px] font-semibold text-[#1A1428]">{t.label}</span>
                    <span className="block text-[11.5px] text-[#6b6480]">{t.hint}</span>
                  </span>
                  <span className="shrink-0 text-[12px] tabular-nums text-[#6b6480]">{counts[t.key]}</span>
                </button>
              )
            })}
          </div>
        </fieldset>
        <fieldset>
          <legend className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[#6b6480]">Sort</legend>
          <div role="radiogroup" aria-label="Sort" className="mt-2 grid gap-1.5 sm:grid-cols-2">
            {STAFF_SORTS.map((s) => (
              <button
                key={s.value}
                type="button"
                role="radio"
                aria-checked={sort === s.value}
                onClick={() => onSort(s.value)}
                className={cn(
                  'h-11 rounded-xl border px-3.5 text-left text-[13px] font-medium transition duration-150 ease-out',
                  RING,
                  sort === s.value ? 'border-[#6D4091] bg-[#EEE8F4] font-semibold text-[#5A3478]' : 'border-[#E2DCEA] bg-white text-[#1A1428] hover:bg-[#f7f5fb]',
                )}
              >
                {s.label}
              </button>
            ))}
          </div>
        </fieldset>
      </div>
    </Sheet>
  )
}
