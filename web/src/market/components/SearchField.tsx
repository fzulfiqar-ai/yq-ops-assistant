import { forwardRef, type FormEvent } from 'react'
import { Search, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { S } from '../strings'

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
  }
>(function SearchField({ value, onChange, onSubmit, onFocus, autoFocus, placeholder, className, size = 'lg', readOnlyTap }, ref) {
  const submit = (e: FormEvent) => {
    e.preventDefault()
    onSubmit?.(value)
  }
  const h = size === 'lg' ? 'h-12' : 'h-11'
  return (
    <form role="search" onSubmit={submit} className={cn('relative flex items-center', className)}>
      <Search size={18} className="pointer-events-none absolute start-3.5 top-1/2 -translate-y-1/2 text-ink-3" aria-hidden="true" />
      <input
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={onFocus}
        placeholder={placeholder || S.searchPlaceholder}
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
