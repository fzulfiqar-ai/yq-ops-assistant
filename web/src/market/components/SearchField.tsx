import { forwardRef, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { Search, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useReducedMotion } from '../shell/useViewport'
import { S } from '../strings'

const HINT_EVERY_MS = 2400
/** the typed hint's pace: a character typed, a character erased, the whole example held, the gap before the next */
const TYPE_MS = 55
const ERASE_MS = 30
const HOLD_MS = 1600
const GAP_MS = 280
/** calm (reduced motion): the next whole example every 3 s, through a 200 ms fade out and in */
const CALM_EVERY_MS = 3000
const CALM_FADE_MS = 200

/**
 * The rotating example query — "Search “20W charger”" — shown where a placeholder would be (the
 * phone band, the desktop search trigger, an empty unfocused SearchField). Purely visual: it is
 * aria-hidden and the control around it carries the accessible name. The two hints share one
 * clipped line box: the outgoing one lifts out of it and is gone before the next rises in
 * (`anim-hint-out` / `anim-hint-in`, market.css "v3: hints"). It holds still
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
    <span aria-hidden="true" className={cn('pointer-events-none flex min-w-0 items-center gap-[0.3em] overflow-hidden whitespace-nowrap leading-[1.6]', className)}>
      <span className={cn('shrink-0', leadClassName)}>{S.search.hintLead}</span>
      {/* one line box, exactly — and a FULL one: 1.6em (not 1.3em) so the descenders of "charger"
          and "Type-C" sit inside it instead of being sliced flat by the clip. The outgoing hint is
          transparent long before it is half out of the window (market.css "v3: hints"), so a
          screenshot can never catch two cut lines in the pill. */}
      <span className="relative block h-[1.6em] min-w-0 flex-1 overflow-hidden">
        {before !== null && (
          <span key={`out-${tick}`} className={cn('anim-hint-out absolute inset-x-0 top-0 truncate', hintClassName)}>
            {S.search.hintQuoted(hints[before])}
          </span>
        )}
        <span key={`in-${tick}`} className={cn('absolute inset-x-0 top-0 truncate', before !== null && 'anim-hint-in', hintClassName)}>
          {S.search.hintQuoted(hints[now])}
        </span>
      </span>
    </span>
  )
}

/**
 * The desktop header's example search, typed (round 2, 29-Oct-2026: the owner chose "Typing hint"):
 * after "Search" an example appears a character at a time, holds, is erased and the next one is typed,
 * a thin plum caret after it (solid while it types, blinking while it holds). Purely visual: aria-hidden,
 * the trigger around it carries the accessible name. The characters are set straight on the text node
 * (React renders it empty), so a keystroke costs no render. It stands on a whole example — never half a
 * word — while `paused` (the palette or a panel is open), while the trigger has focus and in a hidden tab.
 * Under reduced motion it never types: calm but alive, the whole next example fades in every 3 s. The
 * phone band keeps SearchHints.
 */
export function TypedHints({ hints, paused, className, leadClassName, hintClassName }: { hints: readonly string[]; paused?: boolean; className?: string; leadClassName?: string; hintClassName?: string }) {
  const reduced = useReducedMotion()
  const textRef = useRef<HTMLSpanElement>(null)
  const boxRef = useRef<HTMLSpanElement>(null)
  // which example, and how many of its characters show (-1: all); kept across a pause
  const at = useRef({ i: 0, n: -1 })
  const run = !paused && hints.length > 1
  const typing = run && !reduced
  useLayoutEffect(() => {
    const el = textRef.current
    const box = boxRef.current
    if (!el || !box || !hints.length) return
    const s = at.current
    // characters, not UTF-16 units: an Arabic example is typed letter by letter and shapes as it grows
    const word = (i: number) => Array.from(S.search.hintQuoted(hints[i % hints.length]))
    let chars = word(s.i)
    const show = (n: number) => {
      s.n = n
      el.textContent = chars.slice(0, n).join('')
    }
    // before the first paint, and on every pause: the whole example
    show(chars.length)
    box.removeAttribute('data-typing')
    if (!run) return
    const trigger = box.closest('button')
    const held = () => document.hidden || Boolean(trigger?.matches(':focus'))
    if (reduced) {
      // calm: the whole next example, faded out and in (Web Animations, opacity only)
      let fade: Animation | null = null
      const id = window.setInterval(() => {
        if (held()) return
        fade = el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: CALM_FADE_MS, easing: 'ease-in', fill: 'forwards' })
        fade.onfinish = () => {
          s.i = (s.i + 1) % hints.length
          chars = word(s.i)
          show(chars.length)
          fade?.cancel()
          fade = el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: CALM_FADE_MS, easing: 'ease-out' })
        }
      }, CALM_EVERY_MS)
      return () => {
        window.clearInterval(id)
        fade?.cancel()
      }
    }
    let timer = 0
    const later = (ms: number, fn: () => void) => {
      timer = window.setTimeout(fn, ms)
    }
    const stand = () => {
      show(chars.length)
      hold()
    }
    const hold = () => {
      box.removeAttribute('data-typing')
      later(HOLD_MS, () => {
        if (held()) return hold()
        box.setAttribute('data-typing', '')
        erase()
      })
    }
    const erase = () => {
      if (held()) return stand()
      if (s.n > 0) {
        show(s.n - 1)
        return later(ERASE_MS, erase)
      }
      later(GAP_MS, () => {
        s.i = (s.i + 1) % hints.length
        chars = word(s.i)
        type()
      })
    }
    const type = () => {
      if (held()) return stand()
      if (s.n < chars.length) {
        show(s.n + 1)
        return later(TYPE_MS, type)
      }
      hold()
    }
    hold()
    return () => window.clearTimeout(timer)
  }, [run, reduced, hints])
  if (!hints.length) return null
  return (
    <span aria-hidden="true" className={cn('pointer-events-none flex min-w-0 items-center gap-[0.3em] overflow-hidden whitespace-nowrap leading-[1.6]', className)}>
      <span className={cn('shrink-0', leadClassName)}>{S.search.hintLead}</span>
      <span ref={boxRef} className="typed flex h-[1.6em] min-w-0 flex-1 items-center">
        <span ref={textRef} className={cn('min-w-0 truncate', hintClassName)} />
        {typing && <i className="typed-caret" />}
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
        <button type="button" onClick={() => onChange('')} aria-label={S.states.clear} className="hit absolute end-[4.75rem] top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-xs text-ink-2 hover:bg-plum-wash hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70">
          <X size={16} aria-hidden="true" />
        </button>
      )}
      <button type="submit" className={cn('hit absolute end-1.5 top-1/2 -translate-y-1/2 rounded-sm bg-ink px-3.5 text-sm font-semibold text-white transition duration-1 ease-m hover:bg-ink/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70', size === 'lg' ? 'h-9' : 'h-8')}>
        {S.search.submit}
      </button>
    </form>
  )
})
