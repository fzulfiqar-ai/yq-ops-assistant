import * as React from 'react'
import { cn } from '@/lib/utils'

export type BadgeTone = 'accent' | 'green' | 'amber' | 'grey' | 'rose' | 'ink'

const TONE: Record<BadgeTone, string> = {
  accent: 'bg-[#f1ecfb] text-[#6d28d9] ring-[#6d28d9]/12',
  green: 'bg-[#e8f7ee] text-[#137a48] ring-[#137a48]/12',
  amber: 'bg-[#fdf3e3] text-[#96600d] ring-[#96600d]/12',
  grey: 'bg-[#f2f1f6] text-[#6b6480] ring-[#6b6480]/12',
  rose: 'bg-[#fdecef] text-[#9f1239] ring-[#9f1239]/12',
  ink: 'bg-[#1a1430] text-white ring-[#1a1430]/20',
}

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
}

/** Small, quiet status chip. No gradients, no emoji — a tint and a hairline ring. */
export function Badge({ tone = 'accent', className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-semibold leading-4 ring-1 ring-inset',
        TONE[tone],
        className,
      )}
      {...props}
    />
  )
}
