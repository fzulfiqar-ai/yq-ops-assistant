import { forwardRef, useEffect, useState, type FormEvent } from 'react'
import { Search, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useReducedMotion } from '../shell/useViewport'
import { S } from '../strings'

const HINT_EVERY_MS = 2400

/**
 * The rotating example query — "Search “20W charger”" — shown where a placeholder would be (the
 * phone band, the desktop search trigger, an empty unfocused SearchField). Purely visual: it is
 * aria-hidden and the control around it carries the accessible name. The outgoing hint lifts away
 * as the next rises in (`anim-hint-out` / `anim-hint-in`, market.css "v3: hints"). It holds still
 * while `paused`, while the tab is hidden, and under reduced motion (first hint, static).
 */
export function SearchHints({ hints, paused, className, leadClassName, hintClassName }: { hints: readonly string[]; paused?: boolean; className?: string; leadClassName?: string; hintClassName?: string }) {
  const reduced = useReducedMotion()
  const [tick, setTick] = useState(0)
  // true only after a tick while running, so resuming from a pause does not replay the last swap
  const [moving, setMoving] = useState(false)
  const run = !paused && !reduced && hints.length > 1
  if (!run && moving) setMoving(false)
  useEffect(() => {
    if (!run) return
    const id = window.setInterval(() => {
      if (document.hidden) return
      setTick((t) => t + 1)
      setMoving(true)
    }, HINT_EVERY_MS)
    return () => window.clearInterval(id)
  }, [run])
  if (!hints.length) return null
  const n = hints.length
  const now = reduced ? 0 : tick % n
  const before = run && moving && tick > 0 ? (tick - 1) % n : null
  return (
    <span aria-hidden="true" className={cn('pointer-events-none flex min-w-0 items-baseline gap-[0.3em] overflow-hidden whitespace-nowrap', className)}>
      <span className={cn('shrink-0', leadClassName)}>{S.search.hintLead}</span>
      <span className="grid min-w-0 flex-1 overflow-hidden">
        {before !== null && (
          <span key={`out-${tick}`} className={cn('anim-hint-out col-start-1 row-start-1 truncate', hintClassName)}>
            {S.search.hintQuoted(hints[before])}
          </span>
        )}
        <span key={`in-${tick}`} className={cn('col-start-1 row-start-1 truncate', before !== null && 'anim-hint-in', hintClassName)}>
          {S.search.hintQuoted(hints[now])}
        </span>
      </span>
    </span>
  )
}

/** The search field with a VISIBLE submit (Baymard: 90% hide it) and a clear button. */
export const SearchField = forwardRef<
  HTMLInputElement,
  {
    value: string
    onChange: (v: string) => void
    onSubmit?: (v: string) => void
    onFocus?: () => void
    autoFocus?: boolean
    placeholder?: string
    className?: string
    size?: 'md' | 'lg'
    readOnlyTap?: boolean
    /** rotating example queries shown while the field is empty and not focused (S.search.hints) */
    hints?: readonly string[]
  }
>(function SearchField({ value, onChange, onSubmit, onFocus, autoFocus, placeholder, className, size = 'lg', readOnlyTap, hints }, ref) {
  const [focused, setFocused] = useState(false)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    onSubmit?.(value)
  }
  const h = size === 'lg' ? 'h-12' : 'h-11'
  const showHints = Boolean(hints?.length) && !value && !focused
  return (
    <form role="search" onSubmit={submit} className={cn('relative flex items-center', className)}>
      <Search size={18} className="pointer-events-none absolute start-3.5 top-1/2 -translate-y-1/2 text-ink-3" aria-hidden="true" />
      <input
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => {
          setFocused(true)
          onFocus?.()
        }}
        onBlur={() => setFocused(false)}
        placeholder={showHints ? '' : placeholder || S.searchPlaceholder}
        aria-label={S.searchLabel}
        type="search"
        autoFocus={autoFocus}
        readOnly={readOnlyTap}
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        enterKeyHint="search"
        className={cn(
          'w-full rounded-md border border-line bg-surface ps-11 text-md text-ink outline-none transition duration-1 ease-m placeholder:text-ink-3 hover:border-ink/25 focus:border-plum focus:ring-2 focus:ring-plum/15',
          h,
          value ? 'pe-[7.25rem]' : 'pe-24',
          '[&::-webkit-search-cancel-button]:hidden',
        )}
      />
      {showHints && hints && <SearchHints hints={hints} className="absolute inset-y-0 end-24 start-11 items-center text-md" leadClassName="text-ink-3" hintClassName="text-ink-2" />}
      {value && (
        <button type="button" onClick={() => onChange('')} aria-label={S.states.clear} className="absolute end-[4.75rem] top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-xs text-ink-2 hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
          <X size={16} aria-hidden="true" />
        </button>
      )}
      <button type="submit" className={cn('absolute end-1.5 top-1/2 -translate-y-1/2 rounded-sm bg-ink px-3.5 text-sm font-semibold text-white transition duration-1 ease-m hover:bg-ink/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', size === 'lg' ? 'h-9' : 'h-8')}>
        {S.search.submit}
      </button>
    </form>
  )
})
