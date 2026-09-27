import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { errorText } from '@/lib/errorText'

/**
 * The shared "this did not load" panel for a page's main query (R7a). Before it, a report page
 * that got an error kept its skeleton up forever (`isLoading || !data`), so a 500 looked like a
 * slow page. The line comes from errorText: the connection message, "Server error (ref …)", or
 * the API's own detail.
 */
export function LoadError({ error, onRetry, isRetrying = false, fallback = 'Could not load this page.' }: {
  error: unknown
  onRetry?: () => void
  isRetrying?: boolean
  fallback?: string
}) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-10 text-center text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
      <AlertTriangle size={20} className="shrink-0" aria-hidden="true" />
      <span className="max-w-sm">{errorText(error, fallback)}</span>
      {onRetry && (
        <Button type="button" variant="outline" size="sm" onClick={onRetry} disabled={isRetrying}>
          {isRetrying ? <Loader2 className="animate-spin" size={14} /> : <RefreshCw size={14} />} Retry
        </Button>
      )}
    </div>
  )
}
