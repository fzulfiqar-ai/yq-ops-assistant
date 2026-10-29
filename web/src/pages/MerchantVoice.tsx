import { useCallback, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Eye } from 'lucide-react'
import { apiGet, apiPatch } from '@/lib/api'
import { errorText } from '@/lib/errorText'
import { useToast } from '@/components/Toast'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge, type BadgeTone } from '@/components/ui/badge'
import { DataTable, type Column } from '@/components/DataTable'
import { ConversationsTab } from './assist/ConversationsTab'
import { InsightsTab } from './assist/InsightsTab'
import { bhTime } from './assist/format'
import { NotReady, Pills, Tabs } from './assist/shared'
import { TicketsTab } from './assist/TicketsTab'
import { TodayTab } from './assist/TodayTab'

// YQ Assist (v2, 29-Oct-2026; the page was "Merchant voice"): the back office of the marketplace chat. Today (is it
// working, is it selling, what needs a person now), Conversations (a chat is its session), Tickets (what shops
// reported, owned by the order's rep), Insights (sourcing, reorder, the approved answers), the feedback box and the
// free AI. Everything is summed on the server (app/assist_admin.py). Merchant text arrives already masked.

type FbStatus = 'new' | 'seen' | 'done'

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
const KIND_TONE: Record<string, BadgeTone> = { idea: 'accent', problem: 'rose', question: 'amber', up: 'green', down: 'rose' }

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

type Tab = 'today' | 'conversations' | 'tickets' | 'insights' | 'ideas' | 'ai'
const TABS: Tab[] = ['today', 'conversations', 'tickets', 'insights', 'ideas', 'ai']

export default function MerchantVoice() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab') as Tab | null
  const tab: Tab = raw && TABS.includes(raw) ? raw : 'today'
  const openRef = params.get('open')
  const setTab = useCallback((t: Tab, open?: string) => {
    const next = new URLSearchParams()
    next.set('tab', t)
    if (open) next.set('open', open)
    setParams(next, { replace: true })
  }, [setParams])
  const opened = useCallback(() => {
    if (params.get('open')) setTab(tab)
  }, [params, setTab, tab])
  const open = useQuery({
    queryKey: ['yq-assist', 'overview', 7],
    queryFn: () => apiGet<{ tiles: { open_tickets: number } }>('/shop/assist/overview?days=7'),
    retry: false,
  })
  return (
    <div>
      <PageHeader title="YQ Assist" subtitle="The marketplace chat's back office: what it solved, what shops reported, what they asked for that we do not have, and whether the free AI is holding up." />
      <Tabs<Tab> value={tab} onChange={(t) => setTab(t)}
        items={[{ id: 'today', label: 'Today' }, { id: 'conversations', label: 'Conversations' },
          { id: 'tickets', label: 'Tickets', count: open.data?.tiles.open_tickets }, { id: 'insights', label: 'Insights' },
          { id: 'ideas', label: 'Ideas & problems' }, { id: 'ai', label: 'Free AI today' }]} />
      {tab === 'today' && <TodayTab onTicket={(ref) => setTab('tickets', ref)} onTab={(t) => setTab(t)} />}
      {tab === 'conversations' && <ConversationsTab />}
      {tab === 'tickets' && <TicketsTab openRef={openRef} onOpened={opened} />}
      {tab === 'insights' && <InsightsTab />}
      {tab === 'ideas' && <IdeasSection />}
      {tab === 'ai' && <AiSection />}
    </div>
  )
}
