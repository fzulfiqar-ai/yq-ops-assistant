import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ChevronRight, PackageX, PhoneCall, Zap } from 'lucide-react'
import { apiGet } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Stat } from '@/components/DataTable'
import { hoursText, msText, TICKET_KIND } from './format'
import { NotReady, Pills } from './shared'

/** YQ Assist → Today: is the chat working, is it selling, and what needs a person now. */

interface Overview {
  days: number
  tiles: {
    conversations: number
    turns: number
    solved_pct: number | null
    escalated: number
    orders_after_chat: number
    orders_after_chat_bhd: number
    open_tickets: number
    oldest_ticket_hours: number | null
    callbacks_waiting: number
    misses: number
    failed: number
    p50_ms: number | null
    p95_ms: number | null
    thumbs_up_pct: number | null
  }
  do_now: {
    late_tickets: { ref: string; kind: string; hours: number | null }[]
    callbacks: { ref: string; kind: string; hours: number | null }[]
    sold_out_asks: { code: string; times: number }[]
    failed_last_day: number
    sla_hours: number
  }
}

function DoRow({ icon: Icon, tone, title, detail, onOpen }: { icon: typeof Zap; tone: string; title: string; detail: string; onOpen?: () => void }) {
  return (
    <button type="button" onClick={onOpen} disabled={!onOpen}
      className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-secondary/60 disabled:cursor-default disabled:hover:bg-transparent">
      <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${tone}`}><Icon size={16} /></span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold">{title}</span>
        <span className="block text-xs text-muted-foreground">{detail}</span>
      </span>
      {onOpen ? <ChevronRight size={16} className="text-muted-foreground" /> : null}
    </button>
  )
}

export function TodayTab({ onTicket, onTab }: { onTicket: (ref: string) => void; onTab: (tab: 'tickets' | 'insights' | 'ai') => void }) {
  const [days, setDays] = useState(7)
  const q = useQuery({
    queryKey: ['yq-assist', 'overview', days],
    queryFn: () => apiGet<Overview>(`/shop/assist/overview?days=${days}`),
    refetchInterval: 60_000,
    retry: false,
  })
  if (q.isError && !q.data) return <NotReady what="YQ Assist" />
  const t = q.data?.tiles
  const d = q.data?.do_now
  const doNow = d ? d.late_tickets.length + d.callbacks.length + d.sold_out_asks.length + (d.failed_last_day ? 1 : 0) : 0

  return (
    <div className="space-y-5">
      <Pills<number> value={days} onChange={setDays} items={[1, 7, 30].map((x) => ({ id: x, label: x === 1 ? 'Today' : `${x} days` }))} />
      {!t ? <Skeleton className="h-40 w-full" /> : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Conversations" value={t.conversations} foot={`${t.turns} messages`} />
          <Stat label="Solved by the AI" value={t.solved_pct == null ? '—' : `${t.solved_pct}%`} tone="emerald" foot="no rep, no ticket, no miss" />
          <Stat label="Orders after a chat" value={t.orders_after_chat} tone="violet" foot={`BHD ${t.orders_after_chat_bhd.toFixed(3)} · same phone within a day`} />
          <Stat label="Open tickets" value={t.open_tickets} tone={t.open_tickets ? 'rose' : 'slate'} foot={t.oldest_ticket_hours != null ? `oldest ${hoursText(t.oldest_ticket_hours)}` : 'none waiting'} />
          <Stat label="Call-backs waiting" value={t.callbacks_waiting} tone={t.callbacks_waiting ? 'amber' : 'slate'} />
          <Stat label="To the rep" value={t.escalated} tone="blue" foot="hand-offs and tickets" />
          <Stat label="Asked, not stocked" value={t.misses} tone="amber" foot="sourcing leads (Insights)" />
          <Stat label="AI failed" value={t.failed} tone={t.failed ? 'rose' : 'slate'} foot="the page searched instead" />
          <Stat label="Reply time" value={msText(t.p50_ms)} foot={`95% within ${msText(t.p95_ms)}`} />
          <Stat label="Thumbs up" value={t.thumbs_up_pct == null ? '—' : `${t.thumbs_up_pct}%`} tone="emerald" />
        </div>
      )}

      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="font-display text-base font-semibold">Do now</h2>
          <span className="text-xs text-muted-foreground">{doNow ? `${doNow} to look at` : 'nothing waiting'}</span>
        </div>
        {!d ? <Skeleton className="m-4 h-20" /> : doNow === 0 ? (
          <p className="px-4 py-6 text-sm text-muted-foreground">Nothing needs a person right now.</p>
        ) : (
          <div className="divide-y">
            {d.late_tickets.map((x) => (
              <DoRow key={x.ref} icon={AlertTriangle} tone="bg-rose-100 text-rose-700" title={`${x.ref} · ${TICKET_KIND[x.kind] || x.kind}`}
                detail={`Nobody has opened it for ${hoursText(x.hours)} (SLA ${d.sla_hours} working hours)`} onOpen={() => onTicket(x.ref)} />
            ))}
            {d.callbacks.map((x) => (
              <DoRow key={x.ref} icon={PhoneCall} tone="bg-amber-100 text-amber-700" title={`${x.ref} · call the shop back`}
                detail={`Asked ${hoursText(x.hours)} ago`} onOpen={() => onTicket(x.ref)} />
            ))}
            {d.sold_out_asks.map((x) => (
              <DoRow key={x.code} icon={PackageX} tone="bg-violet-100 text-violet-700" title={`${x.code} asked for while sold out`}
                detail={`${x.times} time${x.times === 1 ? '' : 's'}: a reorder signal (Insights)`} onOpen={() => onTab('insights')} />
            ))}
            {d.failed_last_day ? (
              <DoRow icon={Zap} tone="bg-slate-100 text-slate-700" title={`${d.failed_last_day} answer${d.failed_last_day === 1 ? '' : 's'} the AI could not give today`}
                detail="The page searched instead. Check Free AI today for a provider that needs a key." onOpen={() => onTab('ai')} />
            ) : null}
          </div>
        )}
      </Card>
    </div>
  )
}
