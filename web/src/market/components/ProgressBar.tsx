import { Check, Tag, Truck } from 'lucide-react'
import type { QuoteProgress } from '@/lib/shopApi'
import { cn } from '@/lib/utils'
import { S } from '../strings'

/**
 * Progress to a REAL rule (free delivery / next cart offer) from the server quote — never invented.
 * Always green, so it never reads as the plum wholesale rail it sits under.
 */
export function ProgressBar({ progress, compact }: { progress: QuoteProgress; compact?: boolean }) {
  const threshold = Number(progress.threshold_bhd) || 0
  const pct = threshold > 0 ? Math.max(0, Math.min(100, ((threshold - (Number(progress.remaining_bhd) || 0)) / threshold) * 100)) : 0
  const value = progress.unlocked ? 100 : pct
  const Icon = progress.kind === 'free_delivery' ? Truck : Tag
  const label = progress.unlocked && progress.kind === 'free_delivery' ? S.cart.unlocked : progress.label || S.cart.delivery
  return (
    <div className={cn(!compact && 'rounded-lg border border-line bg-surface px-4 py-3')}>
      <div className="flex items-center gap-2">
        <Icon size={compact ? 13 : 15} strokeWidth={1.9} className="shrink-0 text-ok" aria-hidden="true" />
        <span key={label} className={cn('min-w-0 flex-1 font-medium text-ink anim-fade-in', compact ? 'text-xs' : 'text-sm')}>
          {label}
        </span>
        {progress.unlocked && (
          <span className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-ok text-white" aria-hidden="true" style={{ animation: 'm-scale-in 260ms var(--m-ease-spring) both' }}>
            <Check size={10} strokeWidth={3.25} />
          </span>
        )}
      </div>
      <div className={cn('w-full overflow-hidden rounded-full bg-ok-soft', compact ? 'mt-1.5 h-1' : 'mt-2 h-1.5')} role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div className="h-full rounded-full bg-ok transition-[width] duration-500 ease-m" style={{ width: `${value}%` }} />
      </div>
    </div>
  )
}
