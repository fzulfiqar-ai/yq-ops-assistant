import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export type ChipTone = 'plum' | 'ok' | 'warn' | 'bad' | 'ink' | 'grey'

/**
 * Small status chip. A tint and a hairline ring — a footnote, not a headline.
 * `ink` is the one solid chip (Best seller, Save %): it has to read on a white photo tile.
 */
const TONE: Record<ChipTone, string> = {
  plum: 'bg-plum-soft text-plum-ink ring-plum/15',
  ok: 'bg-ok-soft text-ok ring-ok/15',
  warn: 'bg-warn-soft text-warn ring-warn/15',
  bad: 'bg-bad-soft text-bad ring-bad/15',
  ink: 'bg-ink text-white ring-ink/20',
  grey: 'bg-surface-2 text-ink-2 ring-ink/10',
}

const DOT: Record<ChipTone, string> = {
  plum: 'bg-plum',
  ok: 'bg-ok',
  warn: 'bg-warn',
  bad: 'bg-bad',
  ink: 'bg-white',
  grey: 'bg-ink-3',
}

export interface ChipProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: ChipTone
  /** small filled circle before the label — live stock */
  dot?: boolean
  size?: 'sm' | 'md'
}

export function Chip({ tone = 'grey', dot = false, size = 'sm', className, children, ...props }: ChipProps) {
  return (
    <span
      className={cn(
        'inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded-full font-semibold ring-1 ring-inset',
        size === 'sm' ? 'px-2 py-[3px] text-2xs leading-[14px]' : 'px-2.5 py-1 text-xs leading-4',
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
