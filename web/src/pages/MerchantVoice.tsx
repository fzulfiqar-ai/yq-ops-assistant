import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Eye } from 'lucide-react'
import { apiGet, apiPatch } from '@/lib/api'
import { errorText } from '@/lib/errorText'
import { useToast } from '@/components/Toast'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/PageHeader'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { DataTable, Stat, type Column } from '@/components/DataTable'

// Merchant voice: what merchants tell the marketplace chat (YQ Assist) and the feedback box.
// Rows come already masked; no customer names exist in them.

type Outcome = 'answered' | 'miss' | 'fallback' | 'handoff' | 'capped' | 'error'
type FbStatus = 'new' | 'seen' | 'done'

interface Turn {
  id: number
  ts: string
  lang?: string | null
  intent?: string | null
  message?: string | null
  query?: string | null
  reply?: string | null
  item_codes?: string[] | null
  outcome: Outcome
  providers?: string | null
  ms?: number | null
  rating?: 'up' | 'down' | null
}
interface Feedback {
  id: number
  ts: string
  kind: 'idea' | 'problem' | 'question' | 'up' | 'down'
  text?: string | null
  turn_id?: number | null
  lang?: string | null
  page?: string | null
  status: FbStatus
  handled_by?: string | null
  handled_at?: string | null
}
interface ProviderRow {
  name: string
  model: string
  quality?: string | number | null
  fast_host?: boolean
  throttled?: boolean
  available: boolean
  cooldown_s?: number | null
  failures?: number | null
  today?: { day?: string; ok?: number; '429'?: number; fail?: number; bad?: number } | null
}
interface Wanted { query: string; count: number; last: string }

const OUTCOME_TONE: Record<Outcome, BadgeTone> = {
  answered: 'green', miss: 'amber', fallback: 'grey', handoff: 'accent', capped: 'rose', error: 'rose',
}
const KIND_TONE: Record<string, BadgeTone> = { idea: 'accent', problem: 'rose', question: 'amber', up: 'green', down: 'rose' }

/** Bahrain wall-clock time, like the rest of the portal. */
function bhTime(ts?: string | null): string {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleString('en-GB', {
      timeZone: 'Asia/Bahrain', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
    })
  } catch {
    return String(ts)
  }
}

function NotReady({ what }: { what: string }) {
  return (
    <Card className="p-6 text-sm text-muted-foreground">
      {what} is not switched on yet. It appears here once the marketplace chat database update has been applied and
      the first merchants have used it.
    </Card>
  )
}

function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: string }[] }) {
  return (
    <div role="tablist" className="mb-4 flex flex-wrap gap-1.5">
      {items.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={cn('rounded-full px-3.5 py-1.5 text-sm font-semibold transition',
            value === t.id ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground')}>
          {t.label}
        </button>
      ))}
    </div>
  )
}

function Pills<T extends string | number>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { id: T; label: string }[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((t) => (
        <button key={String(t.id)} type="button" aria-pressed={value === t.id} onClick={() => onChange(t.id)}
          className={cn('rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset transition',
            value === t.id ? 'bg-primary/10 text-primary ring-primary/30' : 'text-muted-foreground ring-border hover:text-foreground')}>
          {t.label}
        </button>
      ))}
    </div>
  )
}

/* ───────────────────────── a. Ideas & problems ───────────────────────── */

const FB_KINDS = new Set(['idea', 'problem', 'question'])
type FbData = { feedback: Feedback[] }

function IdeasSection() {
  const toast = useToast()
  const qc = useQueryClient()
  const [status, setStatus] = useState<FbStatus>('new')

  const newCount = useQuery({
    queryKey: ['merchant-voice', 'fb', 'new'],
    queryFn: () => apiGet<FbData>('/shop/feedback?status=new&limit=200'),
    retry: false,
  })
  const list = useQuery({
    queryKey: ['merchant-voice', 'fb', status],
    queryFn: () => apiGet<FbData>(`/shop/feedback?status=${status}&limit=200`),
    retry: false,
  })

  const mark = useMutation({
    mutationFn: ({ id, to }: { id: number; to: FbStatus }) => apiPatch<Feedback>(`/shop/feedback/${id}`, { status: to }),
    onMutate: async ({ id }) => {
      // optimistic: the row leaves the current filter at once
      await qc.cancelQueries({ queryKey: ['merchant-voice', 'fb'] })
      const snaps = qc.getQueriesData<FbData>({ queryKey: ['merchant-voice', 'fb'] })
      for (const [key, d] of snaps) {
        if (d) qc.setQueryData<FbData>(key, { feedback: d.feedback.filter((f) => f.id !== id) })
      }
      return { snaps }
    },
    onError: (e, _v, ctx) => {
      ctx?.snaps.forEach(([key, d]) => qc.setQueryData(key, d))
      toast(errorText(e, 'Could not update that item.'), 'error')
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['merchant-voice', 'fb'] }),
  })

  const rows = useMemo(() => (list.data?.feedback ?? []).filter((f) => FB_KINDS.has(f.kind)), [list.data])
  const newN = (newCount.data?.feedback ?? []).filter((f) => FB_KINDS.has(f.kind)).length

  const cols: Column<Feedback>[] = [
    { key: 'ts', label: 'Date', render: (v) => <span className="whitespace-nowrap">{bhTime(v as string)}</span> },
    { key: 'kind', label: 'Kind', render: (v) => <Badge tone={KIND_TONE[v as string] ?? 'grey'}>{String(v)}</Badge> },
    { key: 'text', label: 'Text', render: (v) => <span className="block max-w-[34rem] whitespace-pre-wrap break-words">{String(v ?? '')}</span> },
    { key: 'lang', label: 'Language', render: (v) => String(v || '—') },
    { key: 'page', label: 'Page', render: (v) => String(v || '—') },
    {
      key: 'status', label: '', align: 'right',
      render: (_v, r) => (
        <div className="flex justify-end gap-1.5">
          {r.status === 'new' && (
            <Button size="sm" variant="outline" onClick={() => mark.mutate({ id: r.id, to: 'seen' })}><Eye size={14} /> Seen</Button>
          )}
          {r.status !== 'done' && (
            <Button size="sm" onClick={() => mark.mutate({ id: r.id, to: 'done' })}><Check size={14} /> Done</Button>
          )}
        </div>
      ),
    },
  ]

  if (list.isError && !list.data) return <NotReady what="The feedback box" />
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <Pills<FbStatus> value={status} onChange={setStatus}
          items={[{ id: 'new', label: 'New' }, { id: 'seen', label: 'Seen' }, { id: 'done', label: 'Done' }]} />
        <div className="text-sm text-muted-foreground"><b className="text-foreground">{newN}</b> new</div>
      </div>
      {list.isLoading ? <Skeleton className="h-40 w-full" /> : (
        <DataTable rows={rows} cols={cols} pinLast empty={`Nothing ${status} from merchants.`} maxHeight="60vh" />
      )}
    </div>
  )
}

/* ───────────────────────── b. What merchants asked ───────────────────────── */

function AskedSection() {
  const [days, setDays] = useState(14)
  const [missOnly, setMissOnly] = useState(false)
  const q = useQuery({
    queryKey: ['merchant-voice', 'turns', days],
    queryFn: () => apiGet<{ turns: Turn[] }>(`/shop/assist/turns?days=${days}&limit=200`),
    retry: false,
  })
  const turns = useMemo(() => q.data?.turns ?? [], [q.data])

  const tiles = useMemo(() => {
    const n = turns.length
    const c = (o: Outcome) => turns.filter((t) => t.outcome === o).length
    return {
      n, answered: n ? Math.round((c('answered') / n) * 100) : 0, miss: c('miss'), handoff: c('handoff'), fallback: c('fallback'),
      up: turns.filter((t) => t.rating === 'up').length, down: turns.filter((t) => t.rating === 'down').length,
    }
  }, [turns])

  const wanted = useMemo<Wanted[]>(() => {
    const m = new Map<string, Wanted>()
    for (const t of turns) {
      if (t.outcome !== 'miss') continue
      const key = (t.query || t.message || '').trim().toLowerCase()
      if (!key) continue
      const cur = m.get(key)
      if (!cur) m.set(key, { query: key, count: 1, last: t.ts })
      else { cur.count += 1; if (t.ts > cur.last) cur.last = t.ts }
    }
    return [...m.values()].sort((a, b) => (a.last < b.last ? 1 : -1))
  }, [turns])

  const shown = missOnly ? turns.filter((t) => t.outcome === 'miss') : turns
  const cols: Column<Turn>[] = [
    { key: 'ts', label: 'Time', render: (v) => <span className="whitespace-nowrap">{bhTime(v as string)}</span> },
    { key: 'intent', label: 'Intent', render: (v) => String(v || '—') },
    { key: 'message', label: 'Message', render: (v) => <span className="block max-w-[22rem] break-words">{String(v ?? '')}</span> },
    { key: 'reply', label: 'Reply', render: (v) => <span className="block max-w-[26rem] break-words text-muted-foreground">{String(v ?? '')}</span> },
    { key: 'item_codes', label: 'Items', render: (v) => <span className="font-mono text-xs">{((v as string[] | null) ?? []).join(', ') || '—'}</span> },
    { key: 'outcome', label: 'Outcome', render: (v) => <Badge tone={OUTCOME_TONE[v as Outcome] ?? 'grey'}>{String(v)}</Badge> },
    { key: 'ms', label: 'ms', align: 'right', render: (v) => (v == null ? '—' : String(v)) },
    { key: 'rating', label: 'Rating', render: (v) => (v === 'up' ? 'Up' : v === 'down' ? 'Down' : '—') },
  ]
  const wantedCols: Column<Wanted>[] = [
    { key: 'query', label: 'Asked for' },
    { key: 'count', label: 'Times', align: 'right' },
    { key: 'last', label: 'Last asked', render: (v) => <span className="whitespace-nowrap">{bhTime(v as string)}</span> },
  ]

  if (q.isError && !q.data) return <NotReady what="The chat log" />
  return (
    <div className="space-y-5">
      <Pills<number> value={days} onChange={setDays} items={[7, 14, 30, 90].map((d) => ({ id: d, label: `${d} days` }))} />
      {q.isLoading ? <Skeleton className="h-24 w-full" /> : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          <Stat label="Turns" value={tiles.n} />
          <Stat label="Answered" value={`${tiles.answered}%`} tone="emerald" />
          <Stat label="Misses" value={tiles.miss} tone="amber" />
          <Stat label="Hand-offs" value={tiles.handoff} tone="violet" />
          <Stat label="Fallbacks" value={tiles.fallback} tone="slate" />
          <Stat label="Thumbs up" value={tiles.up} tone="emerald" />
          <Stat label="Thumbs down" value={tiles.down} tone="rose" />
        </div>
      )}

      <div>
        <h2 className="mb-1 font-display text-base font-semibold">Asked for, not in catalogue</h2>
        <p className="mb-2 text-xs text-muted-foreground">Products merchants asked for that we do not stock: a sourcing lead list.</p>
        <DataTable rows={wanted} cols={wantedCols} searchable={false} empty="No misses in this period." maxHeight="40vh" />
      </div>

      <div>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-display text-base font-semibold">All turns</h2>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            <input type="checkbox" checked={missOnly} onChange={(e) => setMissOnly(e.target.checked)} /> Misses only
          </label>
        </div>
        <DataTable rows={shown} cols={cols} empty="No chat turns in this period." maxHeight="60vh" />
      </div>
    </div>
  )
}

/* ───────────────────────── c. Free AI today ───────────────────────── */

function AiSection() {
  const q = useQuery({
    queryKey: ['merchant-voice', 'llm-health'],
    queryFn: () => apiGet<{ providers: ProviderRow[] }>('/llm/health'),
    retry: false,
  })
  const cols: Column<ProviderRow>[] = [
    { key: 'name', label: 'Provider' },
    { key: 'model', label: 'Model', render: (v) => <span className="font-mono text-xs">{String(v)}</span> },
    {
      key: 'available', label: 'Available',
      render: (_v, r) => r.available
        ? <Badge tone="green">yes</Badge>
        : <Badge tone="amber">{r.cooldown_s ? `cooldown ${Math.round(r.cooldown_s)}s` : 'no'}</Badge>,
    },
    { key: 'today', label: 'OK', align: 'right', render: (_v, r) => String(r.today?.ok ?? 0) },
    { key: 'failures', label: '429', align: 'right', render: (_v, r) => String(r.today?.['429'] ?? 0) },
    { key: 'quality', label: 'Fail', align: 'right', render: (_v, r) => String(r.today?.fail ?? 0) },
    { key: 'throttled', label: 'Bad', align: 'right', render: (_v, r) => String(r.today?.bad ?? 0) },
    { key: 'fast_host', label: 'Fast host', render: (v) => (v ? 'yes' : 'no') },
  ]
  if (q.isError && !q.data) return <NotReady what="The provider check" />
  return (
    <div>
      <p className="mb-3 text-sm text-muted-foreground">
        Providers with no calls today are idle, not broken; a provider with only failures needs a new key or model
        (see scripts/probe_models.py).
      </p>
      {q.isLoading ? <Skeleton className="h-40 w-full" /> : (
        <DataTable rows={q.data?.providers ?? []} cols={cols} searchable={false} empty="No providers reported." maxHeight="60vh" />
      )}
    </div>
  )
}

export default function MerchantVoice() {
  const [tab, setTab] = useState<'ideas' | 'asked' | 'ai'>('ideas')
  return (
    <div>
      <PageHeader title="Merchant voice" subtitle="What merchants ask the marketplace chat, what they suggest, and whether the free AI is holding up." />
      <Tabs value={tab} onChange={setTab}
        items={[{ id: 'ideas', label: 'Ideas & problems' }, { id: 'asked', label: 'What merchants asked' }, { id: 'ai', label: 'Free AI today' }]} />
      {tab === 'ideas' && <IdeasSection />}
      {tab === 'asked' && <AskedSection />}
      {tab === 'ai' && <AiSection />}
    </div>
  )
}
