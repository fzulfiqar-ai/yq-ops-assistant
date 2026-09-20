import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { WifiOff } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { RepCard as Rep } from '@/lib/shopApi'
import { S } from '../strings'
import { Button, LinkButton } from '../ui/Button'
import { CardSkeleton, Skeleton } from '../ui/Skeleton'
import { RepCard } from './RepCard'

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

/**
 * The home page's silhouette while the first catalog is on its way (no cache yet): the promise
 * strip, the promo slider (phone) or the hero stage + tiles (desktop), the round category tiles,
 * then cards — the same boxes the page paints, so nothing jumps when it arrives.
 */
export function HomeSkeleton() {
  return (
    <div className="px-gutter pt-2 lg:px-0 lg:pt-5" aria-busy="true" aria-label={S.states.loading}>
      <Skeleton className="h-3.5 w-3/4 rounded-full lg:hidden" />
      <Skeleton className="mt-4 aspect-[2/1] w-[88%] rounded-xl md:w-[60%] lg:mt-0 lg:aspect-[21/9] lg:w-full" />
      <div className="mt-4 hidden grid-cols-3 gap-4 lg:grid">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-40 rounded-xl" />
        ))}
      </div>
      <div className="mt-7 grid grid-cols-4 gap-x-1.5 gap-y-4 md:grid-cols-8 lg:mt-14 lg:gap-x-3">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="flex flex-col items-center pt-1">
            <Skeleton className="h-16 w-16 rounded-full lg:h-[88px] lg:w-[88px]" />
            <Skeleton className="mt-2.5 h-3 w-12 rounded-full lg:w-16" />
            <Skeleton className="mt-2 h-2.5 w-5 rounded-full" />
          </div>
        ))}
      </div>
      <div className="mt-7 grid grid-cols-2 gap-3 md:grid-cols-3 lg:mt-14 lg:grid-cols-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <CardSkeleton key={i} />
        ))}
      </div>
      <p className="mt-6 text-center text-xs text-ink-2">{S.states.connecting}</p>
    </div>
  )
}

/**
 * The storefront footer (home). Desktop: four columns — the brand and what YQ is, help pages,
 * the merchant's representative (with WhatsApp when the rep shares it), and the price-book note
 * with the dates the prices and stock are from. Phone: the same, stacked and compact.
 */
export function Footer({ prices, stock, rep, className }: { prices?: string | null; stock?: string | null; rep?: Rep | null; className?: string }) {
  const help = [
    { to: '/about', label: S.footer.about },
    { to: '/about#how', label: S.footer.how },
    { to: '/about#trade', label: S.footer.tradeLink },
    { to: '/about#delivery', label: S.footer.delivery },
    { to: '/about#privacy', label: S.footer.privacy },
  ]
  const dates = [prices ? S.states.pricesAsOf(prices) : null, stock ? S.states.stockAsOf(stock) : null].filter((d): d is string => Boolean(d))
  const heading = 'text-2xs font-semibold uppercase tracking-[0.12em] text-ink-3'
  const link = 'inline-flex min-h-11 items-center rounded-xs text-sm font-medium text-ink underline-offset-4 transition-colors duration-1 ease-m hover:text-plum hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 lg:min-h-8'
  return (
    <footer className={cn('border-t border-line pb-4 pt-8 text-ink-2 lg:pb-8 lg:pt-12', className)}>
      <div className="grid gap-x-10 gap-y-7 md:grid-cols-2 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,0.9fr)_minmax(0,1.25fr)_minmax(0,1fr)] 2xl:gap-x-14">
        <div className="min-w-0">
          <Link to="/" className="inline-flex items-center gap-2.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
            <img src="/yq-logo-160.webp" alt="" width={36} height={36} loading="lazy" decoding="async" className="h-9 w-9 rounded-sm" />
            <span className="font-display text-md font-bold text-ink">{S.brand}</span>
          </Link>
          <p className="mt-3 text-balance font-display text-lg font-bold leading-snug text-ink lg:text-xl">{S.footer.tagline}</p>
          <p className="mt-1 text-sm">{S.footer.what}</p>
        </div>

        <nav aria-labelledby="footer-help" className="min-w-0">
          <h2 id="footer-help" className={heading}>
            {S.footer.help}
          </h2>
          <ul className="mt-2 grid grid-cols-2 gap-x-4 md:grid-cols-1">
            {help.map((h) => (
              <li key={h.to}>
                <Link to={h.to} className={link}>
                  {h.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <div className="min-w-0">
          <h2 className={heading}>{S.footer.rep}</h2>
          {rep ? <RepCard rep={rep} mini className="mt-3" /> : <p className="mt-3 max-w-xs text-sm">{S.footer.noRep}</p>}
        </div>

        <div className="min-w-0">
          <h2 className={heading}>{S.footer.priceBook}</h2>
          <p className="mt-3 max-w-xs text-sm">{S.footer.trade}</p>
          {dates.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-xs tnum text-ink-3">
              {dates.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="mt-8 flex flex-wrap items-center justify-between gap-x-6 gap-y-1 border-t border-line-2 pt-4 text-xs text-ink-3 lg:mt-10">
        <span>{S.footer.company}</span>
        <Link to="/me" className="inline-flex min-h-11 items-center font-semibold text-plum hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70 lg:min-h-8">
          {S.nav.me}
        </Link>
      </div>
    </footer>
  )
}
