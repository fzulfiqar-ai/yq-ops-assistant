import { cn } from '@/lib/utils'

/** Shimmering placeholder. Give it the exact size of what it stands in for, so nothing shifts. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn('skeleton rounded-sm', className)} />
}

/** A grid card's exact silhouette. */
export function CardSkeleton({ compact }: { compact?: boolean }) {
  return (
    <div className={cn('overflow-hidden rounded-lg border border-line bg-surface', compact && 'w-[clamp(9.5rem,44vw,12.5rem)] shrink-0')}>
      <div className="skeleton aspect-square w-full" />
      <div className={cn('space-y-2 border-t border-line-2', compact ? 'p-2.5' : 'p-3')}>
        <div className="skeleton h-3.5 w-4/5 rounded-xs" />
        {!compact && <div className="skeleton h-3 w-3/5 rounded-xs" />}
        <div className="skeleton h-5 w-24 rounded-xs" />
        <div className={cn('skeleton w-full rounded-sm', compact ? 'h-10' : 'h-11')} />
      </div>
    </div>
  )
}
