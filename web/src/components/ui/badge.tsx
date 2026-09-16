import * as React from 'react'
import { cn } from '@/lib/utils'

export type BadgeTone = 'accent' | 'green' | 'amber' | 'grey' | 'rose' | 'ink'

/**
 * Tints are deliberately pale — a badge is a footnote, not a headline. The ring
 * is a hairline at 10% so chips still read as separate objects on a white card
 * without drawing a hard border around every one of them.
 */
const TONE: Record<BadgeTone, string> = {
  accent: 'bg-[#EEE8F4] text-[#6D4091] ring-[#6D4091]/10',
  green: 'bg-[#e8f7ee] text-[#137a48] ring-[#137a48]/10',
  amber: 'bg-[#fdf3e3] text-[#96600d] ring-[#96600d]/10',
  grey: 'bg-[#f4f3f8] text-[#6b6480] ring-[#6b6480]/10',
  rose: 'bg-[#fdecef] text-[#9f1239] ring-[#9f1239]/10',
  ink: 'bg-[#1A1428] text-white ring-[#1A1428]/20',
}

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
  /** Small filled circle before the label — used for live stock. */
  dot?: boolean
}

const DOT: Record<BadgeTone, string> = {
  accent: 'bg-[#6D4091]',
  green: 'bg-[#137a48]',
  amber: 'bg-[#b8790f]',
  grey: 'bg-[#a8a2bb]',
  rose: 'bg-[#9f1239]',
  ink: 'bg-white',
}

/** Small, quiet status chip. No gradients, no emoji — a tint and a hairline ring. */
export function Badge({ tone = 'accent', dot = false, className, children, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-full px-2 py-[3px] text-[10.5px] font-semibold leading-[14px] tracking-[0.005em] ring-1 ring-inset',
        TONE[tone],
        className,
      )}
      {...props}
    >
      {dot && <span aria-hidden="true" className={cn('h-[5px] w-[5px] shrink-0 rounded-full', DOT[tone])} />}
      <span className="min-w-0 truncate">{children}</span>
    </span>
  )
}
