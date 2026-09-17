import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { S } from '../strings'

export interface SectionHeaderProps {
  title: string
  /** muted one-liner under the title */
  line?: string | null
  /** renders the "See all →" link when set */
  seeAllTo?: string | null
  seeAllLabel?: string
  /** extra controls before the link (e.g. the tablet rail arrows); centred on the title row */
  action?: ReactNode
  /** id of the heading element, for `<section aria-labelledby={id}>` */
  id?: string
  as?: 'h2' | 'h3'
  className?: string
}

/**
 * The home/browse section header: Sora title, optional muted line, and "See all →" at the end,
 * sitting on the title's baseline (the rail header look, one step larger). The link keeps its
 * 36px visual box but its hit area reaches 44px, and it is described by the heading so screen
 * readers hear "See all, Restock essentials". No margins of its own: the section spaces it.
 */
export function SectionHeader({ title, line, seeAllTo, seeAllLabel = S.home.seeAll, action, id, as: Heading = 'h2', className }: SectionHeaderProps) {
  return (
    <div className={cn('grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline', className)}>
      <Heading id={id} className={cn('col-start-1 row-start-1 min-w-0 text-balance font-display text-xl font-bold text-ink lg:text-2xl', (action || seeAllTo) && 'pr-3')}>
        {title}
      </Heading>
      {action && <div className={cn('col-start-2 row-start-1 -my-2 flex items-center gap-1.5 self-center', seeAllTo && 'mr-1.5')}>{action}</div>}
      {seeAllTo && (
        <Link
          to={seeAllTo}
          aria-describedby={id}
          className="relative col-start-3 row-start-1 -my-2 inline-flex h-9 items-center gap-1 whitespace-nowrap rounded-sm px-2.5 text-sm font-semibold text-plum transition duration-1 ease-m after:absolute after:inset-x-0 after:-inset-y-1 hover:bg-plum-wash focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/70"
        >
          {seeAllLabel}
          <ArrowRight size={14} aria-hidden="true" className="rtl:-scale-x-100" />
        </Link>
      )}
      {line && <p className="col-start-1 row-start-2 mt-0.5 text-xs text-ink-2 lg:text-sm">{line}</p>}
    </div>
  )
}
