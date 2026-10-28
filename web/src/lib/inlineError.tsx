import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { errorText } from '@/lib/errorText'

/**
 * "This part did not load" for one section or card of a page, on one line with its Retry — the compact sibling
 * of components/LoadError.tsx (the whole-page panel). A section whose read failed used to vanish, or to show
 * its "nothing here yet" line, so a failure read as an empty answer. The words come from errorText: the
 * connection line, "Server error (ref …)", or the API's own detail.
 */
export function InlineError({ error, onRetry, isRetrying = false, fallback }: {
  error: unknown
  onRetry?: () => void
  isRetrying?: boolean
  fallback: string
}) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-[13px] text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
      <AlertTriangle size={15} className="shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1">{errorText(error, fallback)}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} disabled={isRetrying}
          className="inline-flex min-h-8 items-center gap-1 font-semibold hover:underline disabled:opacity-60">
          {isRetrying ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : <RefreshCw size={13} aria-hidden="true" />} Retry
        </button>
      )}
    </div>
  )
}
