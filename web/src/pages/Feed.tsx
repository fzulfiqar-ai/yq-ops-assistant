import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { FeedItem, type FeedEvent } from '@/components/FeedItem'

interface FeedResponse {
  events: FeedEvent[]
  runs: { agent: string; last_run?: string; summary?: string }[]
}

type SevFilter = 'all' | 'critical' | 'warn' | 'info'
const SEV_TABS: { key: SevFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'warn', label: 'Warnings' },
  { key: 'info', label: 'Info' },
]

export default function Feed() {
  const [sev, setSev] = useState<SevFilter>('all')
  // Poll every 30s so the feed feels live without a socket.
  const { data, isLoading } = useQuery({
    queryKey: ['feed'],
    queryFn: () => apiGet<FeedResponse>('/feed?limit=80'),
    refetchInterval: 30000,
  })

  const events = (data?.events || []).filter((e) => sev === 'all' || e.severity === sev)
  const counts = (data?.events || []).reduce<Record<string, number>>((a, e) => {
    a[e.severity] = (a[e.severity] || 0) + 1
    return a
  }, {})

  return (
    <div>
      <PageHeader
        title="Live Feed"
        subtitle="What the AI team noticed and did — events, reactions, and the latest agent runs."
      />

      {/* severity filter row (one row above the content, per interaction spec) */}
      <div className="mb-4 flex flex-wrap gap-2">
        {SEV_TABS.map((t) => {
          const n = t.key === 'all' ? (data?.events?.length || 0) : (counts[t.key] || 0)
          return (
            <button
              key={t.key}
              onClick={() => setSev(t.key)}
              className={cn(
                'rounded-full border px-3.5 py-1.5 text-sm transition-colors',
                sev === t.key
                  ? 'border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-200'
                  : 'border-border text-muted-foreground hover:bg-muted/50',
              )}
            >
              {t.label} <span className="ml-1 opacity-60">{n}</span>
            </button>
          )
        })}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
        {/* event timeline */}
        <div className="space-y-2.5">
          {isLoading ? (
            [...Array(6)].map((_, i) => <Skeleton key={i} className="h-20 w-full rounded-xl" />)
          ) : events.length ? (
            events.map((e) => <FeedItem key={e.id} ev={e} />)
          ) : (
            <Card className="p-8 text-center text-sm text-muted-foreground">
              No events yet. As the AI team runs, notable findings and the actions they trigger
              will appear here.
            </Card>
          )}
        </div>

        {/* latest agent runs rail */}
        <Card className="h-fit p-4">
          <div className="mb-3 font-display text-sm font-semibold">Latest agent runs</div>
          <div className="space-y-3">
            {(data?.runs || []).slice(0, 12).map((r) => (
              <div key={r.agent} className="border-b border-border/60 pb-2.5 last:border-0 last:pb-0">
                <div className="text-sm font-medium">{r.agent.replace(/_/g, ' ')}</div>
                <div className="line-clamp-2 text-xs text-muted-foreground">{r.summary || '—'}</div>
              </div>
            ))}
            {!data?.runs?.length && !isLoading && (
              <div className="text-xs text-muted-foreground">No runs recorded yet.</div>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}
