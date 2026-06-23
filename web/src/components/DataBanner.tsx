import { CalendarClock, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/utils'
import { fmtDate, daysSince } from '@/lib/format'

export function DataBanner({ date }: { date?: string | null }) {
  if (!date) return null
  const days = daysSince(date)
  const stale = days > 7
  return (
    <div
      className={cn(
        'mb-5 flex items-center gap-2.5 rounded-xl border px-4 py-2.5 text-sm',
        stale
          ? 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300'
          : 'border-border bg-card text-muted-foreground',
      )}
    >
      {stale ? <TriangleAlert size={16} className="shrink-0" /> : <CalendarClock size={16} className="shrink-0" />}
      <span>
        <strong className="font-semibold">Data as of {fmtDate(date)}</strong>
        {stale && ` · ${days} days old — refresh the Focus exports for live figures.`}
      </span>
    </div>
  )
}
