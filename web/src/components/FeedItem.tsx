import {
  AlertTriangle, AlertOctagon, Info, PackageSearch, TrendingDown, Coins,
  Boxes, Sparkles, Workflow, CheckCircle2, Bell, type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'

export interface FeedEvent {
  id: number
  ts: string
  emitter: string
  event_type: string
  severity: 'info' | 'warn' | 'critical'
  entity_key?: string | null
  payload?: Record<string, unknown>
  consumed_by?: { reaction: string; result: Record<string, unknown> }[]
}

// Status palette (reserved): critical / warn / info — each ships with an icon + label,
// never colour alone (dataviz status rule). Rose/amber/slate match the escalation emails.
const SEV: Record<string, { ring: string; dot: string; label: string; Icon: LucideIcon }> = {
  critical: { ring: 'border-rose-500/30 bg-rose-500/[0.03]', dot: 'text-rose-500', label: 'Critical', Icon: AlertOctagon },
  warn: { ring: 'border-amber-500/30 bg-amber-500/[0.03]', dot: 'text-amber-500', label: 'Warning', Icon: AlertTriangle },
  info: { ring: 'border-violet-500/20 bg-violet-500/[0.02]', dot: 'text-violet-500', label: 'Info', Icon: Info },
}

const TYPE_ICON: Record<string, LucideIcon> = {
  'stock.low': PackageSearch,
  'margin.negative': TrendingDown,
  'ar.risk': Coins,
  'catalog.changed': Boxes,
  'trend.rising': Sparkles,
  'returns.spike': AlertTriangle,
  'procurement.stage': Workflow,
  'ingest.completed': CheckCircle2,
  'action.decided': CheckCircle2,
  'platform.alert': Bell,
}

function relTime(iso: string) {
  const ms = Date.now() - new Date(iso).getTime()
  const m = Math.floor(ms / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

const prettyType = (t: string) => t.replace(/[._]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export function FeedItem({ ev }: { ev: FeedEvent }) {
  const sev = SEV[ev.severity] || SEV.info
  const TypeIcon = TYPE_ICON[ev.event_type] || sev.Icon
  const summary = (ev.payload?.summary as string) || prettyType(ev.event_type)
  const reactions = (ev.consumed_by || []).filter((c) => c.reaction && c.reaction !== 'skipped')

  return (
    <div className={cn('flex gap-3 rounded-xl border p-3.5', sev.ring)}>
      <div className={cn('mt-0.5 shrink-0', sev.dot)}>
        <TypeIcon className="h-5 w-5" aria-hidden />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium text-sm">{prettyType(ev.event_type)}</span>
          <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold', sev.dot)}>
            <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
            {sev.label}
          </span>
          <span className="text-xs text-muted-foreground">· {ev.emitter}</span>
          <span className="ml-auto text-xs text-muted-foreground">{relTime(ev.ts)}</span>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{summary}</p>
        {reactions.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {reactions.map((r, i) => (
              <span key={i} className="inline-flex items-center gap-1 rounded-md bg-violet-500/10 px-2 py-0.5 text-[11px] text-violet-600 dark:text-violet-300">
                <Workflow className="h-3 w-3" aria-hidden />
                {r.reaction.replace(/^_react_/, '').replace(/_/g, ' ')}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
