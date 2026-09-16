import type { QuoteProgress } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { S } from '../strings'
import { Chip } from '../ui/Chip'

/** Progress to a REAL rule (free delivery / next cart offer) from the server quote — never invented. */
export function ProgressBar({ progress, compact }: { progress: QuoteProgress; compact?: boolean }) {
  const pct = progress.threshold_bhd ? Math.max(0, Math.min(100, ((Number(progress.threshold_bhd) - Number(progress.remaining_bhd || 0)) / Number(progress.threshold_bhd)) * 100)) : 0
  const value = progress.unlocked ? 100 : pct
  return (
    <div className={cn(!compact && 'rounded-md border border-line bg-surface p-3.5')}>
      <div className="flex items-center justify-between gap-2">
        <span className={cn('font-medium text-ink', compact ? 'text-xs' : 'text-sm')}>{progress.unlocked ? S.cart.unlocked : progress.label}</span>
        {progress.unlocked && <Chip tone="ok">✓</Chip>}
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-2" role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100} aria-label={progress.label || 'Progress'}>
        <div className={cn('h-full rounded-full transition-[width] duration-500 ease-m', progress.unlocked ? 'bg-ok' : 'bg-plum')} style={{ width: `${value}%` }} />
      </div>
    </div>
  )
}
