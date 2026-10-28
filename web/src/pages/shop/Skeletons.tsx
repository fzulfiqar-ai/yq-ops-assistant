import { cn } from '@/lib/utils'
import { SKELETON } from './shared'

/**
 * The catalog while it loads, in the one loading look of the rep's screens (shared.SKELETON): the
 * same card, rows and bars as what is coming, so nothing jumps when it arrives. It used to be a
 * line of words while the page's code came, a spinner before that (the shell), then these rows.
 */

/** One list row: the photo, the code and a line of words, the quantity and "+". */
function RowSkeleton() {
  return (
    <div className="flex h-[72px] items-center gap-3 px-3 xl:h-14 xl:px-4">
      <div className={cn(SKELETON, 'h-14 w-14 shrink-0 rounded-xl xl:h-10 xl:w-10 xl:rounded-lg')} />
      <div className="min-w-0 flex-1 space-y-2">
        <div className={cn(SKELETON, 'h-3 w-1/3 rounded')} />
        <div className={cn(SKELETON, 'h-2.5 w-2/3 rounded')} />
      </div>
      <div className={cn(SKELETON, 'h-11 w-12 shrink-0 rounded-xl xl:hidden')} />
      <div className={cn(SKELETON, 'h-11 w-11 shrink-0 rounded-xl xl:h-10 xl:w-10')} />
    </div>
  )
}

/** The list card with its rows. */
export function ListSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div aria-hidden="true" className="overflow-hidden rounded-2xl border border-[#E9E4EF] bg-white">
      <div className="divide-y divide-[#F3F0F6]">
        {Array.from({ length: rows }, (_, i) => (
          <RowSkeleton key={i} />
        ))}
      </div>
    </div>
  )
}

/** The whole catalog screen before its code has arrived (the shop page's Suspense): pill, tools, list. */
export function CatalogSkeleton() {
  return (
    <div role="status" className="bg-[#F9F7F3]">
      <span className="sr-only">Loading the catalog…</span>
      <div aria-hidden="true" className="mx-auto max-w-[1600px] px-4 lg:px-6">
        <div className={cn(SKELETON, 'mt-3 h-[3.25rem] rounded-2xl xl:hidden')} />
        <div className="mt-2 pb-2 pt-2">
          <div className={cn(SKELETON, 'h-11 rounded-xl')} />
          <div className="mt-0.5 flex gap-1.5 overflow-hidden py-1.5">
            {[48, 76, 84, 108, 92, 72].map((w) => (
              <div key={w} className={cn(SKELETON, 'h-9 shrink-0 rounded-full')} style={{ width: w }} />
            ))}
          </div>
        </div>
        <div className={cn(SKELETON, 'mb-2 mt-3 h-3 w-24 rounded')} />
        <ListSkeleton />
      </div>
    </div>
  )
}
