import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiSend } from '@/lib/api'
import { errorText } from '@/lib/errorText'
import { useToast } from '@/components/Toast'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { DataTable, type Column } from '@/components/DataTable'
import { bhTime, msText, POLICY_TOPIC } from './format'
import { NotReady, Pills } from './shared'

/**
 * YQ Assist → Insights: what the chat data says, each list with who acts on it. Asked for but not stocked =
 * sourcing; asked for while sold out = reorder; a policy question with no approved answer = "Teach the assistant"
 * (write it once below and the chat answers with your words from then on); speed and failures per provider.
 */

interface Insights {
  days: number
  intents: { intent: string; turns: number }[]
  not_stocked: { asked: string; times: number; last: string }[]
  sold_out: { code: string; name: string; times: number }[]
  teach: { topic: string; times: number; examples: string[] }[]
  providers: { provider: string; turns: number; p50_ms: number | null; p95_ms: number | null; failed: number }[]
  thumbs_down: { ts: string; intent: string | null; message: string | null; reply: string | null }[]
}
interface Answer { topic: string; en: string; ar?: string }

export function TeachPanel({ focus }: { focus?: string | null }) {
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({ queryKey: ['yq-assist', 'policies'], queryFn: () => apiGet<{ topics: string[]; answers: Answer[] }>('/shop/assist/policies'), retry: false })
  // only what was changed here lives in state; everything else reads the saved answers
  const [edits, setEdits] = useState<Record<string, Answer>>({})
  const saved: Record<string, Answer> = Object.fromEntries((q.data?.answers ?? []).map((a) => [a.topic, a]))
  const draft: Record<string, Answer> = { ...saved, ...edits }
  const save = useMutation({
    mutationFn: () => apiSend<{ topics: string[]; answers: Answer[] }>('PUT', '/shop/assist/policies', {
      answers: Object.values(draft).filter((a) => a.en.trim()).map((a) => ({ topic: a.topic, en: a.en.trim(), ...(a.ar?.trim() ? { ar: a.ar.trim() } : {}) })),
    }),
    onSuccess: (d) => {
      qc.setQueryData(['yq-assist', 'policies'], d)
      setEdits({})
      toast('Saved. The chat uses these words from now on.', 'success')
    },
    onError: (e) => toast(errorText(e, 'Could not save the answers.'), 'error'),
  })
  if (q.isError && !q.data) return <NotReady what="Approved answers" />
  const topics = q.data?.topics ?? []
  const set = (topic: string, f: 'en' | 'ar', v: string) => setEdits((d) => ({ ...d, [topic]: { ...(draft[topic] || { topic, en: '' }), ...d[topic], [f]: v } }))
  return (
    <Card className="p-4">
      <h2 className="font-display text-base font-semibold">Teach the assistant: your approved answers</h2>
      <p className="mb-3 text-xs text-muted-foreground">
        The chat never writes a policy itself. For these topics it repeats your words exactly, marked "Official YQ answer";
        a topic left empty is handed to the shop's rep. Keep each answer short and only promise what YQ really does.
      </p>
      {!q.data ? <Skeleton className="h-40" /> : (
        <div className="space-y-3">
          {topics.map((t) => (
            <div key={t} id={`teach-${t}`} className={`rounded-lg border p-3 ${focus === t ? 'ring-2 ring-primary' : ''}`}>
              <div className="mb-1.5 flex items-center gap-2 text-sm font-semibold">{POLICY_TOPIC[t] || t}{draft[t]?.en?.trim() ? <Badge tone="green">answered</Badge> : <Badge tone="grey">goes to the rep</Badge>}</div>
              <textarea value={draft[t]?.en ?? ''} onChange={(e) => set(t, 'en', e.target.value)} rows={2} maxLength={600}
                placeholder="English (required to switch this topic on)" className="mb-1.5 w-full rounded-md border bg-card p-2 text-sm" />
              <textarea value={draft[t]?.ar ?? ''} onChange={(e) => set(t, 'ar', e.target.value)} rows={2} maxLength={600} dir="rtl" lang="ar"
                placeholder="العربية (اختياري)" className="w-full rounded-md border bg-card p-2 text-sm" />
            </div>
          ))}
          <Button onClick={() => save.mutate()} disabled={save.isPending}>Save the answers</Button>
        </div>
      )}
    </Card>
  )
}

export function InsightsTab() {
  const [days, setDays] = useState(30)
  const [focus, setFocus] = useState<string | null>(null)
  const q = useQuery({ queryKey: ['yq-assist', 'insights', days], queryFn: () => apiGet<Insights>(`/shop/assist/insights?days=${days}`), retry: false })
  if (q.isError && !q.data) return <NotReady what="Insights" />
  const d = q.data
  const wantCols: Column<Insights['not_stocked'][number]>[] = [
    { key: 'asked', label: 'Asked for' }, { key: 'times', label: 'Times', align: 'right' },
    { key: 'last', label: 'Last', render: (v) => <span className="whitespace-nowrap">{bhTime(v as string)}</span> },
  ]
  const soldCols: Column<Insights['sold_out'][number]>[] = [
    { key: 'code', label: 'Code', render: (v) => <span className="font-mono text-xs">{String(v)}</span> }, { key: 'name', label: 'Item' },
    { key: 'times', label: 'Asked while sold out', align: 'right' },
  ]
  const provCols: Column<Insights['providers'][number]>[] = [
    { key: 'provider', label: 'Provider', render: (v) => <span className="font-mono text-xs">{String(v)}</span> },
    { key: 'turns', label: 'Turns', align: 'right' },
    { key: 'p50_ms', label: 'Typical', align: 'right', render: (v) => msText(v as number | null) },
    { key: 'p95_ms', label: '95%', align: 'right', render: (v) => msText(v as number | null) },
    { key: 'failed', label: 'Failed', align: 'right' },
  ]
  return (
    <div className="space-y-5">
      <Pills<number> value={days} onChange={setDays} items={[7, 30, 90].map((x) => ({ id: x, label: `${x} days` }))} />
      {!d ? <Skeleton className="h-60 w-full" /> : (
        <>
          <div className="grid gap-5 lg:grid-cols-2">
            <div>
              <h2 className="mb-1 font-display text-base font-semibold">Asked for, not in the catalogue</h2>
              <p className="mb-2 text-xs text-muted-foreground">Sourcing leads: products shops asked for that YQ does not stock.</p>
              <DataTable rows={d.not_stocked} cols={wantCols} searchable={false} empty="Nothing in this period." maxHeight="36vh" />
            </div>
            <div>
              <h2 className="mb-1 font-display text-base font-semibold">Asked for while sold out</h2>
              <p className="mb-2 text-xs text-muted-foreground">Lost sales: a reorder signal for /yq-stock.</p>
              <DataTable rows={d.sold_out} cols={soldCols} searchable={false} empty="Nothing in this period." maxHeight="36vh" />
            </div>
          </div>
          <Card className="p-4">
            <h2 className="mb-1 font-display text-base font-semibold">Policy questions with no approved answer</h2>
            <p className="mb-2 text-xs text-muted-foreground">Each went to the rep. Write the answer once below and the chat gives it from then on.</p>
            {d.teach.length === 0 ? <p className="text-sm text-muted-foreground">None in this period.</p> : (
              <ul className="space-y-2">
                {d.teach.map((t) => (
                  <li key={t.topic} className="flex flex-wrap items-start justify-between gap-2 rounded-lg border p-3">
                    <span className="min-w-0 flex-1">
                      <b>{POLICY_TOPIC[t.topic] || t.topic}</b> · asked {t.times} time{t.times === 1 ? '' : 's'}
                      {t.examples.map((x, i) => <span key={i} className="block text-xs text-muted-foreground">“{x}”</span>)}
                    </span>
                    <Button size="sm" variant="outline" onClick={() => { setFocus(t.topic); document.getElementById(`teach-${t.topic}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }}>
                      Write the answer
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <div className="grid gap-5 lg:grid-cols-2">
            <div>
              <h2 className="mb-2 font-display text-base font-semibold">Speed and failures by provider</h2>
              <DataTable rows={d.providers} cols={provCols} searchable={false} empty="No model calls in this period." maxHeight="30vh" />
            </div>
            <div>
              <h2 className="mb-2 font-display text-base font-semibold">Thumbs down</h2>
              {d.thumbs_down.length === 0 ? <p className="text-sm text-muted-foreground">None in this period.</p> : (
                <ul className="space-y-2">{d.thumbs_down.map((t, i) => (
                  <li key={i} className="rounded-lg border p-2 text-xs">
                    <span className="text-muted-foreground">{bhTime(t.ts)} · {t.intent}</span>
                    <span className="block font-semibold">{t.message}</span>
                    <span className="block text-muted-foreground">{t.reply}</span>
                  </li>
                ))}</ul>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
            What merchants asked: {d.intents.map((x) => <Badge key={x.intent} tone="grey">{x.intent} {x.turns}</Badge>)}
          </div>
        </>
      )}
      <TeachPanel focus={focus} />
    </div>
  )
}
