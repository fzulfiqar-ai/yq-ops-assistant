import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * The data-freshness chip (Sprint 4, plan §8 / §27): "Focus data to 24 Sep · Marketplace live ·
 * Invoice links confirmed 3 of 4". It replaces the portal header's rotating quotes and its
 * always-green "Live" pill, which said nothing true. Focus figures stop at the last uploaded sale
 * day; marketplace figures are read live; the invoice links (admin and management only) say how
 * many delivered orders have a Focus invoice link a PERSON accepted — a suggested invoice still
 * waiting for acceptance does not count, so this is never read as "0 % of orders have no invoice".
 *
 * Without `data` the chip reads GET /freshness itself (any portal login); the Command Centre passes
 * the freshness block of its own overview instead, so the page does not ask twice.
 */
export interface Freshness {
  focus_to: string | null
  focus_label?: string
  focus_days_behind: number | null
  stale: boolean
  marketplace_live: boolean
  last_order_at?: string | null
  match_rate?: { available: boolean; pct: number | null; matched: number; eligible: number } | null
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** '2026-09-24' → '24 Sep' (the API's own form; en-GB would print "Sept") */
function dayLabel(iso: string | null | undefined): string {
  if (!iso) return ''
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}` : iso
}

function freshnessParts(f: Freshness): { focus: string; market: string; match: string | null } {
  const focus = f.focus_to ? `Focus data to ${f.focus_label || dayLabel(f.focus_to)}` : 'No Focus data loaded'
  const market = f.marketplace_live ? 'Marketplace live' : 'Marketplace not answering'
  const m = f.match_rate
  const match = m && m.available && m.eligible > 0 ? `Invoice links confirmed ${m.matched} of ${m.eligible}` : null
  return { focus, market, match }
}

function titleOf(f: Freshness): string {
  const lines = [
    f.focus_to
      ? `Focus reports are loaded up to ${dayLabel(f.focus_to)}${f.focus_days_behind != null ? ` (${f.focus_days_behind} day${f.focus_days_behind === 1 ? '' : 's'} ago)` : ''}. Every Focus figure stops there.`
      : 'No Focus sales are loaded yet.',
    f.marketplace_live ? 'Marketplace orders are read live.' : 'The marketplace orders did not answer just now.',
  ]
  const m = f.match_rate
  if (m && m.available) {
    lines.push(m.eligible
      ? `${m.matched} of ${m.eligible} delivered orders older than 3 days have a Focus invoice link someone accepted; the rest are not yet matched (a suggested invoice may be waiting for acceptance).`
      : 'No delivered order is old enough to need its Focus invoice yet.')
  }
  return lines.join(' ')
}

export function FreshnessChip({ data, variant = 'default', className }: {
  data?: Freshness | null
  /** 'onPlum' = white text on the Command Centre's plum band */
  variant?: 'default' | 'onPlum'
  className?: string
}) {
  const q = useQuery({
    queryKey: ['freshness'],
    queryFn: () => apiGet<Freshness>('/freshness'),
    enabled: data === undefined,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
    retry: 1,
  })
  const f = data === undefined ? q.data : data
  if (!f) return null
  const { focus, market, match } = freshnessParts(f)
  const warn = f.stale || !f.marketplace_live
  const onPlum = variant === 'onPlum'
  return (
    <span
      title={titleOf(f)}
      className={cn(
        'inline-flex min-w-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold',
        onPlum ? 'border-white/25 bg-white/10 text-white' : 'bg-card text-muted-foreground',
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn('h-1.5 w-1.5 shrink-0 rounded-full', warn ? 'bg-amber-500' : onPlum ? 'bg-emerald-300' : 'bg-emerald-500')}
      />
      <span className="truncate">
        <span className={cn(f.stale && (onPlum ? 'text-amber-200' : 'text-amber-600 dark:text-amber-400'))}>{focus}</span>
        <span className="hidden sm:inline"> · {market}</span>
        {match && <span className="hidden xl:inline"> · {match}</span>}
      </span>
    </span>
  )
}
