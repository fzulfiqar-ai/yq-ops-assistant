import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiGet, apiPatch } from '@/lib/api'
import { errorText } from '@/lib/errorText'
import { useToast } from '@/components/Toast'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Sheet } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { DataTable, type Column } from '@/components/DataTable'
import { bhTime, RESOLUTIONS, TICKET_KIND, TICKET_STATUS } from './format'
import { NotReady, Pills } from './shared'

/**
 * YQ Assist → Tickets: what shops reported in the chat (damaged, wrong, missing, not working, delivery, a return)
 * and their call-back requests. Owned by the order's rep (owner, 29-Oct-2026); the office sees all, opens, moves,
 * reassigns and resolves. The shop was promised the next step only.
 */

interface TicketRow {
  id: number
  ref: string
  kind: string
  status: string
  order_id: number | null
  lines: { line_id: number | null; item_code: string; qty: number }[]
  salesman_id: number | null
  resolution: string | null
  created_at: string
  first_seen_at: string | null
  resolved_at: string | null
}
interface TicketFull extends TicketRow {
  note: string | null
  lang: string | null
  photo_urls: string[]
  events: { at: string; actor: string; event: string; from_status: string | null; to_status: string | null; detail: Record<string, unknown> }[]
  order: { id: number; order_no: string; status: string; customer_shop: string | null; customer_name: string | null; delivered_at: string | null } | null
}
interface Rep { id: number; name: string; active?: boolean }

function Detail({ id, reps, onClose }: { id: number; reps: Rep[]; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [note, setNote] = useState('')
  const [resolution, setResolution] = useState('')
  const q = useQuery({ queryKey: ['yq-assist', 'ticket', id], queryFn: () => apiGet<TicketFull>(`/shop/tickets/${id}`) })
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => apiPatch<TicketFull>(`/shop/tickets/${id}`, body),
    onSuccess: (t) => {
      qc.setQueryData(['yq-assist', 'ticket', id], t)
      void qc.invalidateQueries({ queryKey: ['yq-assist', 'tickets'] })
      void qc.invalidateQueries({ queryKey: ['yq-assist', 'overview'] })
      setNote('')
      toast('Ticket updated', 'success')
    },
    onError: (e) => toast(errorText(e, 'Could not update the ticket.'), 'error'),
  })
  const t = q.data
  const repName = (sid: number | null) => reps.find((r) => r.id === sid)?.name || (sid ? `#${sid}` : 'Nobody (the office)')
  const closed = t ? t.status === 'resolved' || t.status === 'rejected' : false

  const footer = t && !closed ? (
    <div className="flex flex-wrap items-center gap-2 p-3">
      {t.status !== 'in_progress' ? <Button size="sm" variant="outline" onClick={() => save.mutate({ status: 'in_progress' })}>In progress</Button> : null}
      {t.status !== 'waiting_shop' ? <Button size="sm" variant="outline" onClick={() => save.mutate({ status: 'waiting_shop' })}>Waiting for shop</Button> : null}
      <select value={resolution} onChange={(e) => setResolution(e.target.value)} className="h-8 rounded-md border bg-card px-2 text-sm">
        <option value="">Resolve as…</option>
        {RESOLUTIONS.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
      </select>
      <Button size="sm" disabled={!resolution || save.isPending}
        onClick={() => save.mutate({ status: resolution === 'not_valid' ? 'rejected' : 'resolved', resolution })}>
        Resolve
      </Button>
    </div>
  ) : null

  return (
    <Sheet open onClose={onClose} variant="drawer" footer={footer}
      title={t ? `${t.ref} · ${TICKET_KIND[t.kind] || t.kind}` : 'Ticket'}
      subtitle={t ? [TICKET_STATUS[t.status]?.label, bhTime(t.created_at), t.order?.customer_shop || t.order?.customer_name].filter(Boolean).join(' · ') : undefined}>
      {!t ? <div className="space-y-2 p-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded-xl" />)}</div> : (
        <div className="space-y-5 p-4 text-sm">
          <section className="grid grid-cols-2 gap-3">
            <div><div className="text-xs text-muted-foreground">Order</div>
              {t.order ? <Link className="font-semibold text-primary hover:underline" to={`/shop-orders?open=${t.order.id}`}>{t.order.order_no}</Link> : <span>Bought on a visit (no order)</span>}
              {t.order?.delivered_at ? <div className="text-xs text-muted-foreground">delivered {bhTime(t.order.delivered_at)}</div> : null}
            </div>
            <div><div className="text-xs text-muted-foreground">Rep</div>
              <select value={t.salesman_id ?? ''} disabled={save.isPending}
                onChange={(e) => save.mutate(e.target.value ? { salesman_id: Number(e.target.value) } : { unassign: true })}
                className="mt-0.5 h-8 w-full rounded-md border bg-card px-2 text-sm">
                <option value="">{repName(null)}</option>
                {reps.filter((r) => r.active !== false).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
          </section>
          {t.lines?.length ? (
            <section><div className="mb-1 text-xs text-muted-foreground">Items</div>
              <ul className="space-y-1">{t.lines.map((l, i) => <li key={i} className="font-mono text-xs">{l.item_code} × {l.qty}</li>)}</ul>
            </section>
          ) : null}
          {t.note ? <section><div className="mb-1 text-xs text-muted-foreground">The shop wrote</div><p className="whitespace-pre-wrap rounded-lg bg-secondary/60 p-3">{t.note}</p></section> : null}
          {t.photo_urls?.length ? (
            <section><div className="mb-1 text-xs text-muted-foreground">Photos (links last an hour)</div>
              <div className="flex flex-wrap gap-2">{t.photo_urls.map((u) => (
                <a key={u} href={u} target="_blank" rel="noreferrer"><img src={u} alt="" className="h-28 w-28 rounded-lg border object-cover" /></a>
              ))}</div>
            </section>
          ) : null}
          {t.resolution ? <section className="text-xs">Resolved as <b>{RESOLUTIONS.find((r) => r.id === t.resolution)?.label || t.resolution}</b>{t.resolved_at ? ` · ${bhTime(t.resolved_at)}` : ''}</section> : null}
          <section>
            <div className="mb-1 text-xs text-muted-foreground">Timeline</div>
            <ol className="space-y-1.5 border-l pl-3">
              {t.events.map((e, i) => (
                <li key={i} className="text-xs">
                  <span className="text-muted-foreground">{bhTime(e.at)}</span> · <b>{e.event}</b>
                  {e.to_status ? ` → ${TICKET_STATUS[e.to_status]?.label || e.to_status}` : ''}
                  {typeof e.detail?.text === 'string' ? <span className="block whitespace-pre-wrap text-foreground/80">“{e.detail.text}”</span> : null}
                  <span className="text-muted-foreground"> · {e.actor}</span>
                </li>
              ))}
            </ol>
          </section>
          <section className="space-y-2">
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="Add a note to the timeline"
              className="w-full rounded-lg border bg-card p-2 text-sm" />
            <Button size="sm" variant="outline" disabled={!note.trim() || save.isPending} onClick={() => save.mutate({ note: note.trim() })}>Add note</Button>
          </section>
        </div>
      )}
    </Sheet>
  )
}

export function TicketsTab({ openRef, onOpened }: { openRef?: string | null; onOpened?: () => void }) {
  const [status, setStatus] = useState('open')
  const [kind, setKind] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const q = useQuery({
    queryKey: ['yq-assist', 'tickets', status, kind],
    queryFn: () => apiGet<{ rows: TicketRow[]; total: number | null }>(`/shop/tickets?status=${status}&limit=200${kind ? `&kind=${kind}` : ''}`),
    refetchInterval: 60_000,
    retry: false,
  })
  const reps = useQuery({ queryKey: ['shop-salesmen'], queryFn: () => apiGet<{ salesmen: Rep[] }>('/shop/salesmen'), retry: false })
  const roster = reps.data?.salesmen ?? []

  // a deep link (the Command Centre, Today's do-now list) opens that ticket by its reference until it is closed
  const linked = openRef ? q.data?.rows.find((r) => r.ref === openRef)?.id ?? null : null
  const shown = open ?? linked
  const close = () => {
    setOpen(null)
    if (openRef) onOpened?.()
  }

  if (q.isError && !q.data) return <NotReady what="Tickets" />
  const cols: Column<TicketRow>[] = [
    { key: 'ref', label: 'Ticket', render: (v) => <span className="font-mono text-xs font-semibold">{String(v)}</span> },
    { key: 'kind', label: 'What', render: (v) => TICKET_KIND[v as string] || String(v) },
    { key: 'lines', label: 'Items', render: (v) => <span className="font-mono text-xs">{((v as TicketRow['lines']) || []).map((l) => `${l.item_code}×${l.qty}`).join(', ') || '—'}</span> },
    { key: 'salesman_id', label: 'Rep', render: (v) => roster.find((r) => r.id === v)?.name || (v ? `#${v}` : 'Office') },
    { key: 'status', label: 'Status', render: (v, r) => <span><Badge tone={TICKET_STATUS[v as string]?.tone ?? 'grey'}>{TICKET_STATUS[v as string]?.label ?? String(v)}</Badge>{r.status === 'new' && !r.first_seen_at ? <span className="ml-1 text-[11px] text-rose-600">not opened</span> : null}</span> },
    { key: 'created_at', label: 'Raised', render: (v) => <span className="whitespace-nowrap">{bhTime(v as string)}</span> },
    { key: 'id', label: '', align: 'right', render: (v) => <Button size="sm" variant="outline" onClick={() => setOpen(v as number)}>Open</Button> },
  ]
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Pills<string> value={status} onChange={setStatus} items={[
          { id: 'open', label: 'Open' }, { id: 'new', label: 'New' }, { id: 'in_progress', label: 'In progress' },
          { id: 'waiting_shop', label: 'Waiting for shop' }, { id: 'resolved', label: 'Resolved' }, { id: 'rejected', label: 'Not valid' }]} />
        <select value={kind} onChange={(e) => setKind(e.target.value)} className="h-8 rounded-md border bg-card px-2 text-sm">
          <option value="">Every kind</option>
          {Object.entries(TICKET_KIND).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
      </div>
      {q.isLoading ? <Skeleton className="h-60 w-full" /> : (
        <DataTable rows={q.data?.rows ?? []} cols={cols} pinLast empty="No tickets here." maxHeight="64vh" />
      )}
      {shown != null ? <Detail id={shown} reps={roster} onClose={close} /> : null}
    </div>
  )
}
