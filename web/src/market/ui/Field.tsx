import { forwardRef, type InputHTMLAttributes, type LabelHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

/** Text inputs are 16px so iOS never zooms; 48px tall on forms, 44px inline. */
export const FIELD =
  'w-full rounded-sm border border-line bg-surface px-3.5 text-md text-ink outline-none transition duration-1 ease-m placeholder:text-ink-3 hover:border-ink/25 focus:border-plum focus:ring-2 focus:ring-plum/15 disabled:opacity-60 aria-[invalid=true]:border-bad aria-[invalid=true]:focus:ring-bad/15'

export function Label({ className, children, hint, ...props }: LabelHTMLAttributes<HTMLLabelElement> & { hint?: ReactNode }) {
  return (
    <label className={cn('mb-1.5 flex items-baseline justify-between gap-2 text-sm font-semibold text-ink', className)} {...props}>
      <span>{children}</span>
      {hint && <span className="text-xs font-normal text-ink-3">{hint}</span>}
    </label>
  )
}

export function Hint({ error, children, id }: { error?: boolean; children: ReactNode; id?: string }) {
  return (
    <p id={id} className={cn('mt-1 text-xs leading-snug', error ? 'font-medium text-bad' : 'text-ink-2')}>
      {children}
    </p>
  )
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { tall?: boolean }>(function Input({ className, tall = true, ...props }, ref) {
  return <input ref={ref} className={cn(FIELD, tall ? 'h-12' : 'h-11', className)} {...props} />
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...props }, ref) {
  return <textarea ref={ref} className={cn(FIELD, 'resize-none py-3 leading-snug', className)} {...props} />
})

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, children, ...props }, ref) {
  return (
    <div className="relative">
      <select ref={ref} className={cn(FIELD, 'h-12 appearance-none pe-10 ps-3', className)} {...props}>
        {children}
      </select>
      <ChevronDown size={16} className="pointer-events-none absolute end-3.5 top-1/2 -translate-y-1/2 text-ink-3" aria-hidden="true" />
    </div>
  )
}
)
