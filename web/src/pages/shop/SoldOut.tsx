import { cn } from '@/lib/utils'

/**
 * The sold-out rule, drawn (ported from the marketplace's SoldOutDivider): sold-out lines stay in
 * the salesman's list — a shop asks about them — but after every line he can sell today, under a
 * quiet ruled heading "Sold Out · N lines". A heading, never role=separator, so a screen reader
 * keeps the count and the group boundary. `grid` spans every column of a product grid.
 */
export function SoldOutDivider({ count, className, grid = false }: { count: number; className?: string; grid?: boolean }) {
  if (count <= 0) return null
  return (
    <h3
      className={cn(
        'flex items-center gap-3 py-2 text-[11.5px] font-semibold tracking-[0.01em] text-[#6b6480]',
        'before:h-px before:flex-1 before:bg-[#E2DCEA] after:h-px after:flex-1 after:bg-[#E2DCEA]',
        grid && 'col-span-full',
        className,
      )}
    >
      Sold Out · {count} {count === 1 ? 'line' : 'lines'}
    </h3>
  )
}
