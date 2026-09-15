import { Minus, Plus, Trash2 } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Quantity control. Steps by `step` (a pack size, usually), never goes below
 * `min` (the MOQ) — stepping below it calls `onRemove` instead, because "one less
 * than the minimum" means "take it out", not "an invalid order".
 */

export interface StepperProps {
  value: number
  onChange: (next: number) => void
  onRemove?: () => void
  step?: number
  min?: number
  max?: number
  /** what the group is counting, e.g. the item code — used for screen readers */
  label?: string
  size?: 'md' | 'sm'
  className?: string
}

export function Stepper({
  value,
  onChange,
  onRemove,
  step = 1,
  min = 1,
  max,
  label = 'quantity',
  size = 'md',
  className,
}: StepperProps) {
  const dec = () => {
    const next = value - step
    if (next < min) {
      if (onRemove) onRemove()
      else onChange(min)
      return
    }
    onChange(next)
  }
  const inc = () => {
    const next = value + step
    onChange(max != null && next > max ? max : next)
  }

  const atMax = max != null && value >= max
  const willRemove = value - step < min
  const btn =
    size === 'sm'
      ? 'h-9 w-9 rounded-lg'
      : 'h-11 w-11 rounded-xl'

  return (
    <div
      role="group"
      aria-label={`Quantity for ${label}`}
      className={cn(
        'inline-flex items-center justify-between gap-1 rounded-xl border border-[#e4e0ee] bg-white p-0.5',
        size === 'md' ? 'h-12' : 'h-10',
        className,
      )}
    >
      <button
        type="button"
        onClick={dec}
        aria-label={willRemove ? `Remove ${label}` : `Decrease ${label} by ${step}`}
        className={cn(
          'grid shrink-0 place-items-center text-[#6b6480] transition hover:bg-[#f4f2f9] hover:text-[#1a1430] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9]',
          willRemove && 'hover:bg-[#fdecef] hover:text-[#9f1239]',
          btn,
        )}
      >
        {willRemove ? <Trash2 size={size === 'sm' ? 14 : 16} /> : <Minus size={size === 'sm' ? 15 : 17} />}
      </button>
      <span
        aria-live="polite"
        className={cn(
          'min-w-[2.25rem] select-none text-center font-display font-bold tabular-nums text-[#1a1430]',
          size === 'sm' ? 'text-[13px]' : 'text-[15px]',
        )}
      >
        {value}
      </span>
      <button
        type="button"
        onClick={inc}
        disabled={atMax}
        aria-label={`Increase ${label} by ${step}`}
        className={cn(
          'grid shrink-0 place-items-center text-[#6d28d9] transition hover:bg-[#f1ecfb] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#6d28d9] disabled:cursor-not-allowed disabled:opacity-40',
          btn,
        )}
      >
        <Plus size={size === 'sm' ? 15 : 17} />
      </button>
    </div>
  )
}
