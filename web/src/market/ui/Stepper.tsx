import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Minus, Plus, Trash2 } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * Quantity control. Steps by `step` (a pack size, usually), never goes below `min` (the MOQ) —
 * stepping below it calls `onRemove`, because "one less than the minimum" means "take it out".
 * Holding ± auto-repeats after 400 ms (every 110 ms). The number ticks up/down as it changes
 * and, when `onValueClick` is given, opens the keypad sheet.
 *
 * Sizes are the market's tap targets: xs 32 (mini-cart), sm 40 (rows), md 44 (cards — it takes
 * the place of the Add button, so it must be exactly as tall), lg 48 (sheet footers). On a touch
 * screen every ± button also has an invisible 44px hit area (`hit`, market.css); the drawn sizes
 * stay as listed.
 */

export interface StepperProps {
  value: number
  onChange: (next: number) => void
  onRemove?: () => void
  step?: number
  min?: number
  max?: number
  /** what the group counts, e.g. the product name — for screen readers */
  label?: string
  size?: 'xs' | 'sm' | 'md' | 'lg'
  className?: string
  onValueClick?: () => void
}

type Size = NonNullable<StepperProps['size']>
const SHELL: Record<Size, string> = { xs: 'h-8 rounded-xs', sm: 'h-10 rounded-sm', md: 'h-11 rounded-sm', lg: 'h-12 rounded-md' }
const PAD: Record<Size, string> = { xs: 'p-0.5', sm: 'p-px', md: 'p-0.5', lg: 'p-0.5' }
const BTN: Record<Size, string> = { xs: 'h-7 w-7 rounded-xs', sm: 'h-[38px] w-[38px] rounded-xs', md: 'h-10 w-10 rounded-xs', lg: 'h-11 w-11 rounded-sm' }
const ICON: Record<Size, number> = { xs: 13, sm: 15, md: 16, lg: 17 }
const VALUE: Record<Size, string> = { xs: 'text-xs', sm: 'text-sm', md: 'text-sm', lg: 'text-base' }

const HOLD_MS = 400
const REPEAT_MS = 110

interface Hold {
  t?: number
  i?: number
  fired: boolean
  latest: { value: number; step: number; min: number; max?: number; onChange: (n: number) => void; onRemove?: () => void }
}

export function Stepper({ value, onChange, onRemove, step = 1, min = 1, max, label = 'quantity', size = 'md', className, onValueClick }: StepperProps) {
  // direction of the last change, for the tick animation (derived during render, no effect)
  const [seen, setSeen] = useState(value)
  const [dir, setDir] = useState<'up' | 'down' | null>(null)
  if (seen !== value) {
    setSeen(value)
    setDir(value > seen ? 'up' : 'down')
  }

  const hold = useRef<Hold>({ fired: false, latest: { value, step, min, max, onChange, onRemove } })
  useEffect(() => {
    hold.current.latest = { value, step, min, max, onChange, onRemove }
  })
  useEffect(
    () => () => {
      window.clearTimeout(hold.current.t)
      window.clearInterval(hold.current.i)
    },
    [],
  )

  const stepBy = (sign: 1 | -1): boolean => {
    const { value: v, step: s, min: m, max: mx, onChange: oc, onRemove: orm } = hold.current.latest
    const next = v + sign * s
    if (sign < 0 && next < m) {
      if (orm) orm()
      else oc(m)
      return false
    }
    if (mx != null && next > mx) {
      oc(mx)
      return false
    }
    oc(next)
    return true
  }
  const stopHold = () => {
    window.clearTimeout(hold.current.t)
    window.clearInterval(hold.current.i)
    hold.current.t = hold.current.i = undefined
  }
  const beginHold = (e: ReactPointerEvent, sign: 1 | -1) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    hold.current.fired = false
    stopHold()
    hold.current.t = window.setTimeout(() => {
      hold.current.fired = true
      hold.current.i = window.setInterval(() => {
        if (!stepBy(sign)) stopHold()
      }, REPEAT_MS)
    }, HOLD_MS)
  }
  const clickStep = (sign: 1 | -1) => {
    // a click that ended a hold already applied its steps
    if (hold.current.fired) {
      hold.current.fired = false
      return
    }
    stepBy(sign)
  }

  const atMax = max != null && value >= max
  const willRemove = value - step < min
  const icon = ICON[size]
  const tick = cn('inline-block', dir === 'up' && 'anim-tick-up', dir === 'down' && 'anim-tick-down')

  return (
    <div role="group" aria-label={`Quantity for ${label}`} className={cn('inline-flex select-none items-center justify-between gap-0.5 border border-plum/40 bg-surface transition duration-1 ease-m focus-within:border-plum', PAD[size], SHELL[size], className)}>
      <button
        type="button"
        onClick={() => clickStep(-1)}
        onPointerDown={(e) => beginHold(e, -1)}
        onPointerUp={stopHold}
        onPointerLeave={stopHold}
        onPointerCancel={stopHold}
        onContextMenu={(e) => e.preventDefault()}
        aria-label={willRemove ? `Remove ${label}` : `Decrease ${label} by ${step}`}
        className={cn(
          'hit relative grid shrink-0 place-items-center text-ink-2 transition duration-1 ease-m hover:bg-plum-wash hover:text-ink active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70',
          willRemove && 'hover:bg-bad-soft hover:text-bad',
          BTN[size],
        )}
      >
        {willRemove ? <Trash2 size={icon - 2} aria-hidden="true" /> : <Minus size={icon} aria-hidden="true" />}
      </button>
      {onValueClick ? (
        <button type="button" onClick={onValueClick} aria-label={`Type a quantity for ${label} (currently ${value})`} className={cn('min-w-[2.25rem] flex-1 self-stretch overflow-hidden rounded-xs text-center font-display font-bold tnum text-ink transition duration-1 ease-m hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus/70', VALUE[size])}>
          <span key={value} className={tick}>
            {value}
          </span>
        </button>
      ) : (
        <span aria-live="polite" className={cn('min-w-[2.25rem] flex-1 overflow-hidden text-center font-display font-bold tnum text-ink', VALUE[size])}>
          <span key={value} className={tick}>
            {value}
          </span>
        </span>
      )}
      <button
        type="button"
        onClick={() => clickStep(1)}
        onPointerDown={(e) => beginHold(e, 1)}
        onPointerUp={stopHold}
        onPointerLeave={stopHold}
        onPointerCancel={stopHold}
        onContextMenu={(e) => e.preventDefault()}
        disabled={atMax}
        aria-label={`Increase ${label} by ${step}`}
        className={cn('hit relative grid shrink-0 place-items-center bg-plum text-white transition duration-1 ease-m hover:bg-plum-deep active:scale-95 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white/70', BTN[size])}
      >
        <Plus size={icon} aria-hidden="true" />
      </button>
    </div>
  )
}
