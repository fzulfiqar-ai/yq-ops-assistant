import { cn } from '@/lib/utils'
import { S } from '../strings'

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

/**
 * A page whose code is still on its way (shell/RouteOutlet): a heading and a row of cards inside the
 * shell, so the header and nav never empty out. aria-busy tells assistive tech it is loading.
 */
export function PageSkeleton() {
  return (
    <div aria-busy="true" aria-label={S.states.loading} className="px-gutter pt-4 lg:px-0 lg:pt-6">
      <Skeleton className="h-7 w-48" />
      <Skeleton className="mt-2 h-4 w-72 max-w-full" />
      <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <CardSkeleton key={i} />
        ))}
      </div>
    </div>
  )
}

/**
 * The desktop chrome while its own code downloads (a first visit on a laptop): the header's frame
 * with the real logo (preloaded by the HTML), then the page skeleton — never an empty canvas.
 */
export function DesktopShellSkeleton() {
  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-line bg-surface">
        <div className="container-m flex h-[72px] items-center gap-4">
          <img src="/yq-logo-160.webp" alt="" width={44} height={44} className="h-11 w-11 rounded-md" />
          <Skeleton className="h-4 w-28" />
          <Skeleton className="ms-6 h-11 max-w-[460px] flex-1 rounded-full" />
          <Skeleton className="ms-auto h-11 w-28 rounded-md" />
        </div>
      </header>
      <div className="container-m">
        <PageSkeleton />
      </div>
    </div>
  )
}
