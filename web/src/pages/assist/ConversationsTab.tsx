import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Sheet } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { DataTable, type Column } from '@/components/DataTable'
import { bhTime, msText, SESSION_OUTCOME, TURN_OUTCOME } from './format'
import { NotReady, Pills } from './shared'

/** YQ Assist → Conversations: a chat is its session (not a turn); open one to replay it as the merchant saw it. */

interface SessionRow {
  session_id: string
  first_ts: string
  last_ts: string
  turns: number
  lang: string | null
  intents: string[]
  outcome: string
  first_message: string | null
  shop: string | null
  rep: string | null
  up: number
  down: number
  ms_max: number
}
interface TurnRow {
  id: number
  ts: string
  intent: string | null
  action?: string | null
  message: string | null
  reply: string | null
  item_codes: string[] | null
  cards?: { code: string; why: string }[] | null
  outcome: string
  providers: string | null
  ms: number | null
  ms_understand?: number | null
  ms_answer?: number | null
  rating: string | null
  topic?: string | null
  detected_lang?: string | null
  escalated?: boolean
  ticket_id?: number | null
}

function Replay({ sid, onClose }: { sid: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['yq-assist', 'session', sid],
    queryFn: () => apiGet<{ turns: TurnRow[] }>(`/shop/assist/sessions/${encodeURIComponent(sid)}`),
  })
  const turns = q.data?.turns ?? []
  return (
    <Sheet open onClose={onClose} variant="drawer" title="Conversation" subtitle={turns.length ? `${turns.length} messages · ${bhTime(turns[0].ts)}` : undefined}>
      {q.isLoading ? (
        <div className="space-y-2 p-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded-xl" />)}</div>
      ) : (
        <div className="space-y-4 p-4">
          {turns.map((t) => (
            <div key={t.id} className="space-y-1.5">
              <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3 py-2 text-sm text-primary-foreground">
                {t.action ? <span className="mr-1 rounded bg-white/20 px-1 text-[10px] uppercase">tile</span> : null}
                {t.message}
              </div>
              <div className="max-w-[92%] rounded-2xl rounded-bl-md border bg-card px-3 py-2 text-sm">
                {t.reply || <span className="text-muted-foreground">(cards only)</span>}
                {(t.cards?.length || t.item_codes?.length) ? (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {(t.cards?.length ? t.cards : (t.item_codes ?? []).map((c) => ({ code: c, why: '' }))).map((c) => (
                      <span key={c.code} className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${c.why === 'sold_out' ? 'bg-rose-100 text-rose-700' : c.why === 'alternative' ? 'bg-emerald-100 text-emerald-700' : 'bg-secondary'}`}>
                        {c.code}{c.why && c.why !== 'match' ? ` · ${c.why.replace('_', ' ')}` : ''}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                <span>{bhTime(t.ts)}</span>
                {t.intent ? <Badge tone="grey">{t.intent}</Badge> : null}
                <Badge tone={TURN_OUTCOME[t.outcome] ?? 'grey'}>{t.outcome}</Badge>
                {t.topic ? <Badge tone="amber">{t.topic}</Badge> : null}
                {t.escalated ? <Badge tone="accent">to the rep</Badge> : null}
                {t.rating ? <Badge tone={t.rating === 'up' ? 'green' : 'rose'}>{t.rating === 'up' ? 'thumbs up' : 'thumbs down'}</Badge> : null}
                <span>· {msText(t.ms)}{t.ms_understand != null ? ` (read ${msText(t.ms_understand)}${t.ms_answer ? `, write ${msText(t.ms_answer)}` : ''})` : ''}</span>
                {t.providers ? <span className="font-mono">· {t.providers}</span> : null}
              </div>
            </div>
          ))}
        </div>
      )}
    </Sheet>
  )
}

export function ConversationsTab() {
  const [days, setDays] = useState(7)
  const [outcome, setOutcome] = useState<string>('')
  const [text, setText] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const q = useQuery({
    queryKey: ['yq-assist', 'sessions', days, outcome, text],
    queryFn: () => apiGet<{ rows: SessionRow[]; total: number; counts: Record<string, number> }>(
      `/shop/assist/sessions?days=${days}&limit=200${outcome ? `&outcome=${outcome}` : ''}${text.trim() ? `&q=${encodeURIComponent(text.trim())}` : ''}`),
    retry: false,
  })
  if (q.isError && !q.data) return <NotReady what="The chat log" />
  const counts = q.data?.counts ?? {}
  const cols: Column<SessionRow>[] = [
    { key: 'last_ts', label: 'When', render: (v) => <span className="whitespace-nowrap">{bhTime(v as string)}</span> },
    { key: 'shop', label: 'Shop', render: (v, r) => <span>{String(v || '—')}{r.rep ? <span className="block text-xs text-muted-foreground">rep {r.rep}</span> : null}</span> },
    { key: 'first_message', label: 'First message', render: (v) => <span className="block max-w-[22rem] break-words">{String(v ?? '')}</span> },
    { key: 'intents', label: 'Asked', render: (v) => <span className="text-xs text-muted-foreground">{((v as string[]) || []).join(', ')}</span> },
    { key: 'outcome', label: 'Outcome', render: (v) => { const o = SESSION_OUTCOME[v as string]; return <Badge tone={o?.tone ?? 'grey'}>{o?.label ?? String(v)}</Badge> } },
    { key: 'turns', label: 'Msgs', align: 'right' },
    { key: 'lang', label: 'Lang', render: (v) => String(v || '—') },
    { key: 'session_id', label: '', align: 'right', render: (v) => <Button size="sm" variant="outline" onClick={() => setOpen(v as string)}>Open</Button> },
  ]
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Pills<number> value={days} onChange={setDays} items={[1, 7, 30, 90].map((x) => ({ id: x, label: x === 1 ? 'Today' : `${x} days` }))} />
        <Pills<string> value={outcome} onChange={setOutcome}
          items={[{ id: '', label: 'All' }, ...Object.entries(SESSION_OUTCOME).map(([id, o]) => ({ id, label: `${o.label}${counts[id] ? ` ${counts[id]}` : ''}` }))]} />
        <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Search what merchants wrote" className="h-8 w-60" />
      </div>
      {q.isLoading ? <Skeleton className="h-60 w-full" /> : (
        <DataTable rows={q.data?.rows ?? []} cols={cols} pinLast searchable={false} empty="No conversations in this period." maxHeight="64vh" />
      )}
      {open ? <Replay sid={open} onClose={() => setOpen(null)} /> : null}
    </div>
  )
}
