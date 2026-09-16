import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { WifiOff } from 'lucide-react'
import { cn } from '@/lib/utils'
import { S } from '../strings'
import { Button, LinkButton } from '../ui/Button'
import { CardSkeleton, Skeleton } from '../ui/Skeleton'

export function EmptyState({ title, hint, action, className }: { title: string; hint?: string; action?: ReactNode; className?: string }) {
  return (
    <div className={cn('rounded-lg border border-line bg-surface px-6 py-12 text-center', className)}>
      <p className="font-display text-base font-bold text-ink">{title}</p>
      {hint && <p className="mt-1 text-sm leading-snug text-ink-2">{hint}</p>}
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  )
}

export function OfflineBanner({ onRetry }: { onRetry?: () => void }) {
  return (
    <div className="mt-3 flex items-center gap-2 rounded-sm bg-warn-soft px-3 py-2 text-xs leading-snug text-warn">
      <WifiOff size={14} className="shrink-0" aria-hidden="true" />
      <span className="flex-1">{S.states.offline}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="shrink-0 font-semibold underline underline-offset-2">
          {S.states.retry}
        </button>
      )}
    </div>
  )
}

export function ConnectingState({ onRetry, failed }: { onRetry: () => void; failed: boolean }) {
  return (
    <div className="mx-auto max-w-sm px-4 py-16 text-center">
      <div className={cn('mx-auto h-10 w-10 rounded-full border-4 border-surface-2 border-t-plum', !failed && 'animate-spin')} aria-hidden="true" />
      <h2 className="mt-5 font-display text-lg font-bold text-ink">{failed ? S.states.couldNot : S.states.connecting}</h2>
      <p className="mt-1 text-sm leading-snug text-ink-2">{failed ? S.states.couldNotHint : S.states.connectingHint}</p>
      {failed && (
        <Button className="mt-5" onClick={onRetry}>
          {S.states.retry}
        </Button>
      )}
    </div>
  )
}

export function ClosedState() {
  return (
    <div className="mx-auto max-w-sm px-4 py-16 text-center">
      <h2 className="font-display text-lg font-bold text-ink">{S.states.closed}</h2>
      <p className="mt-1 text-sm text-ink-2">{S.states.closedHint}</p>
      <LinkButton to="/orders" className="mt-5">
        {S.orders.title}
      </LinkButton>
    </div>
  )
}

/** The home page's silhouette while the first catalog is on its way (no cache yet). */
export function HomeSkeleton() {
  return (
    <div className="px-gutter pt-2" aria-busy="true" aria-label={S.states.loading}>
      <Skeleton className="h-12 w-full rounded-md" />
      <div className="mt-4 grid grid-cols-4 gap-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="aspect-square rounded-md" />
        ))}
      </div>
      <Skeleton className="mt-4 h-44 w-full rounded-xl" />
      <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <CardSkeleton key={i} />
        ))}
      </div>
      <p className="mt-6 text-center text-xs text-ink-2">{S.states.connecting}</p>
    </div>
  )
}

export function Footer({ prices, stock }: { prices?: string | null; stock?: string | null }) {
  return (
    <footer className="px-gutter py-10 text-center text-xs leading-relaxed text-ink-2">
      <div>{S.states.footer}</div>
      <div className="mt-0.5">
        {[prices ? S.states.pricesAsOf(prices) : null, stock ? S.states.stockAsOf(stock) : null].filter(Boolean).join(' · ')}
      </div>
      <div className="mt-2 flex flex-wrap justify-center gap-x-4 gap-y-1">
        <Link to="/about" className="font-semibold text-plum hover:underline">{S.nav.about}</Link>
        <Link to="/about#delivery" className="font-semibold text-plum hover:underline">{S.about.delivery}</Link>
        <Link to="/about#privacy" className="font-semibold text-plum hover:underline">{S.about.privacy}</Link>
        <Link to="/me" className="font-semibold text-plum hover:underline">{S.nav.me}</Link>
      </div>
    </footer>
  )
}
