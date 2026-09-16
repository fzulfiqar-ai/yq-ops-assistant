import type { SelectHTMLAttributes } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * A native <select> that stops looking like one.
 *
 * The browser's default control is the single fastest way to make a considered
 * page look like a template — and on iOS it is a different shape again. So the
 * chrome is ours (hairline, 20px radius family, one accent focus ring, our own
 * chevron) while the element underneath stays native: a real picker on a phone,
 * real keyboard support, zero JS.
 */

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  /** `pill` is the compact filter-bar shape; `field` matches the form inputs. */
  shape?: 'pill' | 'field'
  wrapperClassName?: string
}

export function Select({ shape = 'field', className, wrapperClassName, ...props }: SelectProps) {
  const pill = shape === 'pill'
  return (
    <div className={cn('relative', pill ? 'shrink-0' : 'w-full', wrapperClassName)}>
      <select
        {...props}
        className={cn(
          'w-full appearance-none bg-white font-medium text-[#1A1428] outline-none transition duration-150 ease-out',
          'border border-[#E2DCEA] hover:border-[#CFC3DE] focus:border-[#6D4091] focus:ring-2 focus:ring-[#6D4091]/15',
          'focus-visible:outline-none',
          pill ? 'h-9 rounded-full pl-3.5 pr-8 text-[12px]' : 'h-11 rounded-xl pl-3 pr-9 text-[14px]',
          className,
        )}
      />
      <ChevronDown
        size={pill ? 14 : 16}
        aria-hidden="true"
        className={cn(
          'pointer-events-none absolute top-1/2 -translate-y-1/2 text-[#6b6480]',
          pill ? 'right-3' : 'right-3.5',
        )}
      />
    </div>
  )
}
