import { Loader2, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * The rep's "this did not load" card — Today's, now shared with Shops: what did not load, what to
 * check, and Try again, which asks the server again (the query's refetch) instead of leaving the rep
 * a line of red text with nothing to press. While it asks, the button says so.
 */
export function LoadFailed({
  title,
  body = 'The server did not answer. Check the connection and try again.',
  onRetry,
  retrying = false,
  className,
}: {
  title: string
  body?: string
  onRetry: () => void
  retrying?: boolean
  className?: string
}) {
  return (
    <div role="alert" className={cn('rounded-2xl border border-border bg-card px-4 py-6 text-center', className)}>
      <p className="text-[13.5px] font-semibold">{title}</p>
      <p className="mt-1 text-[12.5px] text-muted-foreground">{body}</p>
      <button
        type="button"
        onClick={onRetry}
        disabled={retrying}
        className="mt-4 inline-flex h-11 items-center gap-2 rounded-xl bg-primary px-5 text-[13px] font-semibold text-primary-foreground disabled:opacity-70"
      >
        {retrying ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <RefreshCw size={15} aria-hidden="true" />}
        {retrying ? 'Trying again…' : 'Try again'}
      </button>
    </div>
  )
}
