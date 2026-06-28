import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Loader2, PackageCheck, Clock, TriangleAlert, Percent, Truck, CheckCircle2, Flame, FileText, Camera, FileCode, MessageSquareQuote, Trash2 } from 'lucide-react'
import { apiGet, apiUpload, apiDelete } from '@/lib/api'
import { cn } from '@/lib/utils'
import { bhd } from '@/lib/format'
import { useAuth } from '@/lib/auth'
import { PageHeader } from '@/components/PageHeader'
import { useToast } from '@/components/Toast'
import { Card } from '@/components/ui/card'

interface OLine {
  code: string; description: string; ordered_qty: number; ordered_rate: number
  recv_qty: number | null; landed_unit: number | null; sell_unit: number | null
  margin_pct: number | null; cost_variance_pct: number | null; cost_estimated?: boolean; flags: string[]
}
interface OrderDetail {
  found: boolean; po_no: string; po_date: string; vendor: string
  ordered_value_bhd: number; line_count: number
  status: 'received' | 'partial' | 'pending'; stage: string | null
  lines: OLine[]
  reconciliation: { short: string[]; over: string[]; cost_overrun: string[]; not_received: string[]; not_ordered: string[]; summary: string }
  margin: { order_margin_pct: number | null; thin_items: string[]; summary: string }
  timeline: { stage: string; note: string; actor: string; created_at: string }[]
  files: { id: number; kind: string; filename: string; url: string | null; by: string }[]
  draft_message: string
  summary: string
}

const STATUS: Record<string, { label: string; cls: string; icon: typeof PackageCheck }> = {
  received: { label: 'Received', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200', icon: PackageCheck },
  partial: { label: 'Partially received', cls: 'bg-amber-50 text-amber-700 border-amber-200', icon: Truck },
  pending: { label: 'On the way', cls: 'bg-sky-50 text-sky-700 border-sky-200', icon: Clock },
}
function marginCls(p?: number | null) {
  if (p == null) return 'text-muted-foreground'
  return p >= 30 ? 'text-emerald-600' : p >= 15 ? 'text-amber-600' : 'text-rose-600'
}
const num = (n?: number | null) => (n == null ? '—' : Number(n).toLocaleString('en-US'))
const rate = (n?: number | null) => (n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 }))

export default function OrderDetail() {
  const { poNo = '' } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const { me } = useAuth()
  const isAdmin = me?.role === 'admin'
  const photoRef = useRef<HTMLInputElement>(null)
  const docRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)

  async function delFile(id: number) {
    if (!window.confirm('Remove this file?')) return
    try {
      const r = await apiDelete<{ ok?: boolean; error?: string }>(`/orders/files/${id}`)
      if (r.error) toast(r.error, 'error')
      else { toast('File removed.', 'success'); qc.invalidateQueries({ queryKey: ['order', poNo] }) }
    } catch { toast('Could not remove the file.', 'error') }
  }
  const { data, isLoading } = useQuery({
    queryKey: ['order', poNo],
    queryFn: () => apiGet<OrderDetail>(`/orders/${encodeURIComponent(poNo)}`),
  })

  async function uploadDoc(f: File | null, endpoint: 'photo' | 'file') {
    if (!f) return
    setBusy(true)
    try {
      const fd = new FormData(); fd.append('file', f)
      const r = await apiUpload<{ ok?: boolean; error?: string }>(`/orders/${encodeURIComponent(poNo)}/${endpoint}`, fd)
      if (r.error) toast(r.error, 'error')
      else { toast('Added to the order.', 'success'); qc.invalidateQueries({ queryKey: ['order', poNo] }) }
    } catch { toast('Could not add the file.', 'error') } finally { setBusy(false) }
  }

  if (isLoading) return <div className="grid h-[50vh] place-items-center"><Loader2 className="animate-spin text-primary" /></div>
  if (!data?.found) return (
    <div className="mx-auto max-w-2xl">
      <Link to="/orders" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft size={15} /> Orders</Link>
      <Card className="p-10 text-center text-sm text-muted-foreground">{data?.summary || `Order ${poNo} not found.`}</Card>
    </div>
  )

  const st = STATUS[data.status] || STATUS.pending
  const StIcon = st.icon
  const m = data.margin.order_margin_pct

  return (
    <div>
      <Link to="/orders" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition hover:text-foreground"><ArrowLeft size={15} /> Orders</Link>
      <PageHeader title={data.po_no} subtitle={`${data.vendor || 'Vendor'} · ${data.po_date || ''} · ${data.line_count} lines`}
        actions={
          <button onClick={() => { navigator.clipboard.writeText(data.draft_message).then(() => toast('Vendor message copied.', 'success')).catch(() => toast('Could not copy.', 'error')) }}
            className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-[13px] font-medium text-muted-foreground transition hover:border-primary/40 hover:text-foreground">
            <MessageSquareQuote size={14} /> Message vendor
          </button>
        } />

      {/* Status + margin hero */}
      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card className="flex items-center gap-3 p-4">
          <span className={cn('grid h-10 w-10 place-items-center rounded-xl border', st.cls)}><StIcon size={18} /></span>
          <div><div className="font-display text-base font-bold">{st.label}</div>
            <div className="text-[11px] text-muted-foreground">{data.stage ? `pipeline: ${data.stage}` : 'order status'}</div></div>
        </Card>
        <Card className="flex items-center gap-3 p-4">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent/60 text-primary ring-1 ring-inset ring-white/40"><Percent size={18} /></span>
          <div><div className={cn('font-display text-base font-bold tabular-nums', marginCls(m))}>{m == null ? '—' : `${m}%`}</div>
            <div className="text-[11px] text-muted-foreground" title="Margin = profit ÷ selling price. Markup = profit ÷ cost (what Focus shows).">
              margin on arrival{m != null && m < 100 ? ` · ${Math.round((m / (100 - m)) * 1000) / 10}% markup` : ''}</div></div>
        </Card>
        <Card className="flex items-center gap-3 p-4">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent/60 text-foreground ring-1 ring-inset ring-white/40 font-display text-sm font-bold">BD</span>
          <div><div className="font-display text-base font-bold tabular-nums">{bhd(data.ordered_value_bhd, 0)}</div>
            <div className="text-[11px] text-muted-foreground">ordered value</div></div>
        </Card>
      </div>

      {/* Reconciliation + margin banners */}
      {(() => {
        const r = data.reconciliation
        const issue = r.short.length + r.over.length + r.cost_overrun.length + r.not_received.length + r.not_ordered.length
        return (
          <Card className={cn('mb-5 flex flex-wrap items-center gap-x-6 gap-y-2 p-4 text-sm', issue && 'border-amber-300 bg-amber-50/40')}>
            <span className="inline-flex items-center gap-2">
              {issue ? <TriangleAlert size={16} className="text-amber-500" /> : <CheckCircle2 size={16} className="text-emerald-500" />}
              <span className="font-medium">{r.summary}</span>
            </span>
            <span className="text-muted-foreground">{data.margin.summary}</span>
          </Card>
        )
      })()}

      {/* Line table */}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                <th className="px-4 py-2.5">Item</th>
                <th className="px-4 py-2.5 text-right">Ordered</th>
                <th className="px-4 py-2.5 text-right">Received</th>
                <th className="px-4 py-2.5 text-right">Landed cost</th>
                <th className="px-4 py-2.5 text-right">Sells at</th>
                <th className="px-4 py-2.5 text-right">Margin</th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((l) => (
                <tr key={l.code} className="border-b last:border-0 hover:bg-accent/30">
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{l.code}</div>
                    <div className="max-w-[28ch] truncate text-[11px] text-muted-foreground">{l.description}</div>
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    {l.ordered_qty == null
                      ? <span className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-semibold text-violet-700">not on PO</span>
                      : <>{num(l.ordered_qty)} <span className="text-muted-foreground">@ {rate(l.ordered_rate)}</span></>}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    {l.recv_qty == null
                      ? (l.flags.includes('not_received')
                          ? <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">not received</span>
                          : <span className="text-muted-foreground">—</span>)
                      : (
                        <span className={cn((l.flags.includes('short') || l.flags.includes('over')) && 'text-amber-600')}>
                          {num(l.recv_qty)}
                          {(l.flags.includes('short') || l.flags.includes('over')) && <Flame size={10} className="ml-1 inline text-amber-500" />}
                        </span>
                      )}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    {l.landed_unit == null ? '—' : <>{l.cost_estimated && <span title="estimated from the VFAN invoice" className="text-amber-600">~</span>}{rate(l.landed_unit)}</>}
                    {l.flags.includes('cost_overrun') && <span title="costs more than ordered" className="ml-1 text-rose-500">▲</span>}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">{rate(l.sell_unit)}</td>
                  <td className={cn('px-4 py-2.5 text-right font-semibold tabular-nums', marginCls(l.margin_pct))}>
                    {l.margin_pct == null ? '—' : <>{l.cost_estimated && <span className="text-amber-600">~</span>}{l.margin_pct}%</>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Files & photos vault */}
      <div className="mt-6">
        <div className="mb-2 flex items-center justify-between px-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Documents & photos</span>
          <div className="flex items-center gap-2">
            <input ref={photoRef} type="file" accept="image/*" capture="environment" className="hidden"
              onChange={(e) => uploadDoc(e.target.files?.[0] || null, 'photo')} />
            <input ref={docRef} type="file" accept=".pdf,.xlsx,.xls,.csv,.xml,image/*" className="hidden"
              onChange={(e) => uploadDoc(e.target.files?.[0] || null, 'file')} />
            <button onClick={() => docRef.current?.click()} disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition hover:border-primary/40 hover:text-foreground">
              {busy ? <Loader2 className="animate-spin" size={13} /> : <FileText size={13} />} Attach file
            </button>
            <button onClick={() => photoRef.current?.click()} disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition hover:border-primary/40 hover:text-foreground">
              <Camera size={13} /> Add photo
            </button>
          </div>
        </div>
        {data.files.length === 0 ? (
          <Card className="p-6 text-center text-sm text-muted-foreground">No documents yet — drop the PO/MRN on the Orders page, or add a photo.</Card>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {data.files.map((f, i) => (
              <div key={i} className="group relative">
                {f.kind === 'photo' ? (
                  <a href={f.url || '#'} target="_blank" rel="noreferrer">
                    <img src={f.url || ''} alt="order photo" className="h-32 w-full rounded-xl border object-cover shadow-soft transition group-hover:shadow-lift" />
                  </a>
                ) : (
                  <a href={f.url || '#'} target="_blank" rel="noreferrer"
                    className="flex items-center gap-2.5 rounded-xl border bg-card p-3 transition hover:border-primary/40 hover:shadow-soft">
                    <span className={cn('grid h-9 w-9 shrink-0 place-items-center rounded-lg',
                      f.kind === 'po' ? 'bg-blue-50 text-blue-600' : f.kind === 'invoice' ? 'bg-amber-50 text-amber-600' : 'bg-violet-50 text-violet-600')}>
                      {f.kind === 'po' ? <FileText size={16} /> : <FileCode size={16} />}
                    </span>
                    <div className="min-w-0">
                      <div className="text-sm font-medium uppercase">{f.kind}</div>
                      <div className="truncate text-[11px] text-muted-foreground">{f.filename}</div>
                    </div>
                  </a>
                )}
                {isAdmin && (
                  <button onClick={() => delFile(f.id)} title="Remove file (admin)"
                    className="absolute -right-2 -top-2 hidden h-6 w-6 place-items-center rounded-full border bg-card text-muted-foreground shadow-soft transition hover:text-rose-600 group-hover:grid">
                    <Trash2 size={11} />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Pipeline timeline */}
      {data.timeline.length > 0 && (
        <div className="mt-6">
          <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Lifecycle timeline</div>
          <Card className="p-4">
            <ol className="space-y-2.5">
              {data.timeline.map((e, i) => (
                <li key={i} className="flex items-start gap-3 text-sm">
                  <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-primary" />
                  <div className="flex-1">
                    <span className="font-medium capitalize">{e.stage.replace(/_/g, ' ')}</span>
                    {e.note && <span className="text-muted-foreground"> — {e.note}</span>}
                    <span className="ml-2 text-[11px] text-muted-foreground">{e.actor} · {new Date(e.created_at).toLocaleDateString()}</span>
                  </div>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      )}
    </div>
  )
}
