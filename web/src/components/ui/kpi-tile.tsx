import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * The portal's one KPI tile (ship 2): the Dashboard's figures and business health, the Command Centre's first
 * screen and every page's Stat row (components/DataTable) draw this, so a figure looks the same wherever it is.
 * There used to be four designs (an accent rail with a glow and gradient ink, a glow with a toned icon, a plum
 * label card, a value-first stat).
 *
 *   label (plum, small caps, with its icon)            [the corner: a drill link, or ↗ when the tile opens a page]
 *   VALUE (Sora, bold, tabular)  [chip]
 *   children (a sparkline, a bar, three cells)
 *   foot (what the figure is, over which days)
 *
 * A tile that opens a page lifts 2 px on hover (transform only, 200 ms; still under reduced motion). The surface
 * is the one card surface (.card-luxe).
 */
export type KpiTone = 'default' | 'primary' | 'success' | 'warning' | 'destructive' | 'info' | 'muted'

const TONE_TEXT: Record<KpiTone, string> = {
  default: '',
  primary: 'text-primary',
  success: 'text-success',
  warning: 'text-warning',
  destructive: 'text-destructive',
  info: 'text-info',
  muted: 'text-muted-foreground',
}

export interface KpiTileProps {
  label: ReactNode
  value?: ReactNode
  icon?: LucideIcon
  /** the icon's colour when it carries a meaning of its own (a health stat that is good or bad); else the label's plum */
  iconTone?: KpiTone
  /** the figure's colour (a Stat that is a warning, a count that is bad news) */
  tone?: KpiTone
  /** the exact figure, on hover of the value */
  exact?: string
  /** beside the value: a delta, a share */
  chip?: ReactNode
  /** under the value: a sparkline, a progress bar, cells */
  children?: ReactNode
  /** the last line: what the figure is and over which days */
  foot?: ReactNode
  /** the whole tile opens this page */
  to?: string
  /** a link of its own in the top corner (when the tile itself does not open a page) */
  corner?: ReactNode
  /** how the figure is counted, on hover of the tile */
  title?: string
  className?: string
}

export function KpiTile({ label, value, icon: Icon, iconTone, tone = 'default', exact, chip, children, foot, to, corner, title, className }: KpiTileProps) {
  const tile = (
    <div
      title={title}
      className={cn(
        'card-luxe flex h-full min-w-0 flex-col rounded-xl p-4 text-card-foreground',
        to && 'transition-transform duration-200 ease-[cubic-bezier(.22,1,.36,1)] group-hover:-translate-y-0.5 motion-reduce:transition-none motion-reduce:group-hover:translate-y-0',
        className,
      )}
    >
      <div className="flex min-h-6 items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5 text-2xs font-semibold uppercase leading-snug tracking-[0.08em] text-primary">
          {Icon && <Icon size={14} aria-hidden="true" className={cn('shrink-0', iconTone && TONE_TEXT[iconTone])} />}
          <span className="min-w-0">{label}</span>
        </div>
        {to ? (
          <ArrowUpRight size={15} aria-hidden="true" className="shrink-0 text-muted-foreground/60 transition-colors group-hover:text-primary" />
        ) : corner}
      </div>
      {(value != null || chip) && (
        <div className="mt-1.5 flex flex-wrap items-baseline gap-x-2 gap-y-1">
          {value != null && (
            <span title={exact} className={cn('font-display text-kpi font-bold tabular-nums', TONE_TEXT[tone])}>{value}</span>
          )}
          {chip}
        </div>
      )}
      {children}
      {foot && <div className="mt-auto pt-2 text-[12px] leading-snug text-muted-foreground">{foot}</div>}
    </div>
  )
  return to ? <Link to={to} className="group block h-full rounded-xl">{tile}</Link> : tile
}
