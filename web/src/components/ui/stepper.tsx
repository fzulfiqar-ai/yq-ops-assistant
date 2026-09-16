import { Minus, Plus, Trash2 } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Quantity control. Steps by `step` (a pack size, usually), never goes below
 * `min` (the MOQ) — stepping below it calls `onRemove` instead, because "one less
 * than the minimum" means "take it out", not "an invalid order".
 *
 * Sizes are the shop's three tap targets: 40 (dense rows), 44 (cards — it takes
 * the place of the Add button, so it must be exactly as tall), 48 (the primary
 * action in a sheet footer).
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
  size?: 'sm' | 'md' | 'lg'
  className?: string
  /** Marketplace: tapping the number opens a keypad sheet to type a quantity. */
  onValueClick?: () => void
}

const SHELL: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'h-10 rounded-xl',
  md: 'h-11 rounded-xl',
  lg: 'h-12 rounded-xl',
}
const BTN: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'h-9 w-9 rounded-lg',
  md: 'h-10 w-10 rounded-[10px]',
  lg: 'h-11 w-11 rounded-xl',
}
const ICON: Record<'sm' | 'md' | 'lg', number> = { sm: 15, md: 16, lg: 17 }
const VALUE: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'text-[13px]',
  md: 'text-[14px]',
  lg: 'text-[15px]',
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
  onValueClick,
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
  const icon = ICON[size]

  return (
    <div
      role="group"
      aria-label={`Quantity for ${label}`}
      className={cn(
        'inline-flex select-none items-center justify-between gap-1 border border-[#e4e0ee] bg-white p-0.5 transition duration-150 ease-out focus-within:border-[#6d28d9]',
        SHELL[size],
        className,
      )}
    >
      <button
        type="button"
        onClick={dec}
        aria-label={willRemove ? `Remove ${label}` : `Decrease ${label} by ${step}`}
        className={cn(
          'grid shrink-0 place-items-center text-[#6b6480] transition duration-150 ease-out hover:bg-[#f4f2f9] hover:text-[#1a1430] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#6d28d9]/70 active:scale-95',
          willRemove && 'hover:bg-[#fdecef] hover:text-[#9f1239]',
          BTN[size],
        )}
      >
        {willRemove ? <Trash2 size={icon - 2} /> : <Minus size={icon} />}
      </button>
      {onValueClick ? (
        <button
          type="button"
          onClick={onValueClick}
          aria-label={`Type a quantity for ${label} (currently ${value})`}
          className={cn(
            'min-w-[2.25rem] flex-1 self-stretch rounded-lg text-center font-display font-bold tabular-nums text-[#1a1430] transition duration-150 ease-out hover:bg-[#f4f2f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#6d28d9]/70',
            VALUE[size],
          )}
        >
          {value}
        </button>
      ) : (
        <span
          aria-live="polite"
          className={cn(
            'min-w-[2.25rem] flex-1 text-center font-display font-bold tabular-nums text-[#1a1430]',
            VALUE[size],
          )}
        >
          {value}
        </span>
      )}
      <button
        type="button"
        onClick={inc}
        disabled={atMax}
        aria-label={`Increase ${label} by ${step}`}
        className={cn(
          'grid shrink-0 place-items-center text-[#6d28d9] transition duration-150 ease-out hover:bg-[#f3eefc] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#6d28d9]/70 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40',
          BTN[size],
        )}
      >
        <Plus size={icon} />
      </button>
    </div>
  )
}
