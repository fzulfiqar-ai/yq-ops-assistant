import * as React from 'react'
import { cn } from '@/lib/utils'

export type BadgeTone = 'accent' | 'green' | 'amber' | 'grey' | 'rose' | 'ink'

/**
 * Tints are deliberately pale — a badge is a footnote, not a headline. The ring
 * is a hairline at 10–15% so chips still read as separate objects on a white card
 * without drawing a hard border around every one of them. Every tone is a token
 * (index.css), so a badge reads in dark mode too: the ink on its own tint is
 * 4.9:1 or more in both themes.
 */
const TONE: Record<BadgeTone, string> = {
  accent: 'bg-accent text-primary ring-primary/10',
  green: 'bg-success/10 text-success ring-success/15',
  amber: 'bg-warning/10 text-warning ring-warning/15',
  grey: 'bg-muted text-muted-foreground ring-muted-foreground/10',
  rose: 'bg-destructive/10 text-destructive ring-destructive/15',
  ink: 'bg-foreground text-background ring-foreground/20',
}

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
  /** Small filled circle before the label — used for live stock. */
  dot?: boolean
}

const DOT: Record<BadgeTone, string> = {
  accent: 'bg-primary',
  green: 'bg-success',
  amber: 'bg-warning',
  grey: 'bg-muted-foreground/60',
  rose: 'bg-destructive',
  ink: 'bg-background',
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
